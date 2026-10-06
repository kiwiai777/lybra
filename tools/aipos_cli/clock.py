"""AIPOS-F109 件②(M12): 时间原语——产品代码「取当前时间 / ISO 输出 / 文件名 slug」的唯一实现。

原状: 当前时间函数 20 份副本(`_utc_now` / `_now_iso` / `utc_timestamp` / `_stamp_now` …)、文件名 slug 7 种手写压缩法。
现: 产品代码(tools/aipos_cli、tools/mcp_server)一律调本模块; `datetime.now(` 只许出现在本模块(夹具
tests/test_aipos_f109_records_clock_runall.py 以 git grep 守)。

格式声明只在本模块常量一处(ISO_Z_FORMAT / SLUG_FORMATS)。落盘格式与改造前逐字节一致(存量兼容):
  - ISO 输出 = 秒精度、UTC 以 `Z` 结尾(`2026-10-06T06:14:45Z`)——原 `.replace(microsecond=0).isoformat().replace("+00:00", "Z")`。
  - slug 式样按名取(既有落盘名各式并存, 只收口实现不改式样; 新式样只许加进 SLUG_FORMATS)。
零依赖(只用标准库), 不 import 产品其他模块。
"""
from __future__ import annotations

from datetime import datetime, timezone

#: ISO 输出式样(秒精度 UTC, Z 结尾)。
ISO_Z_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: 文件名 slug 式样表(唯一声明)。键 = 式样名, 值 = strftime 式样。
#:   compact   20261006_061445   记录 id / 落盘名(claim/return/close/verdict/event/finalization 旧名/repair/preview)
#:   compact_t 20261006T061445   owner_verification / bench_audit 落盘名(保留 T)
#:   digits    20261006061445    governance add 备份名(本地时间)
#:   millis    20261006-061445-123  token_rotation 记录名(毫秒精度, 同秒两写不撞; 截到毫秒)
#:   date      2026-10-06        按日文件名
#:   month     2026-10           按月目录名
SLUG_FORMATS: dict[str, str] = {
    "compact": "%Y%m%d_%H%M%S",
    "compact_t": "%Y%m%dT%H%M%S",
    "digits": "%Y%m%d%H%M%S",
    "millis": "%Y%m%d-%H%M%S-%f",
    "date": "%Y-%m-%d",
    "month": "%Y-%m",
}


def utc_now() -> datetime:
    """当前时刻(UTC, 带时区)。"""
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """当前时刻(本机本地时区, 不带 tzinfo)——只给沿用本地日期的既有落盘名(governance add 按日文件/备份名)。"""
    return datetime.now()


def iso_z(moment: datetime | None = None) -> str:
    """ISO 输出: 秒精度, UTC 写作 `Z`。moment 缺省 = 当前 UTC。"""
    value = utc_now() if moment is None else moment
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


#: ISO 文本入参的压缩法(只对这两种式样; 与改造前手写的 `.replace("-", "").replace(":", "")…` 逐字节一致, 含非规范输入)。
_ISO_TEXT_T_REPLACEMENT = {"compact": "_", "compact_t": "T"}


def file_slug(style: str = "compact", moment: datetime | str | None = None) -> str:
    """文件名 slug。style ∈ SLUG_FORMATS; moment = datetime / 缺省当前 UTC / ISO 文本(只 compact 与 compact_t)。

    datetime 入参按 SLUG_FORMATS 套式样; ISO 文本入参(调用方已持有的记录时间戳)去掉 `-` `:` `Z`、`T` 按式样换写——
    与改造前各处手写压缩法逐字节一致(存量落盘名兼容)。未声明的 style / 该式样不收文本入参 = ValueError(fail-closed, 不猜)。"""
    fmt = SLUG_FORMATS.get(style)
    if fmt is None:
        raise ValueError(f"未声明的 slug 式样 {style!r}(声明: tools/aipos_cli/clock.py SLUG_FORMATS {sorted(SLUG_FORMATS)})")
    if isinstance(moment, str):
        if style not in _ISO_TEXT_T_REPLACEMENT:
            raise ValueError(f"slug 式样 {style!r} 只收 datetime 入参(ISO 文本入参只许 {sorted(_ISO_TEXT_T_REPLACEMENT)})")
        return moment.replace("-", "").replace(":", "").replace("Z", "").replace("T", _ISO_TEXT_T_REPLACEMENT[style])
    value = utc_now() if moment is None else moment
    text = value.strftime(fmt)
    if style == "millis":
        return text[:-3]
    return text
