"""AIPOS-F109 件②(M12): 时间原语——产品代码「取当前时间 / ISO 输出 / 文件名 slug」的唯一实现。

原状: 当前时间函数 20 份副本(`_utc_now` / `_now_iso` / `utc_timestamp` / `_stamp_now` …)、文件名 slug 7 种手写压缩法。
现: 产品代码(tools/aipos_cli、tools/mcp_server)一律调本模块; `datetime.now(` 只许出现在本模块(夹具
tests/test_aipos_f109_records_clock_runall.py 以 git grep 守)。

格式声明只在本模块常量一处(ISO_Z_FORMAT / SLUG_FORMATS)。落盘格式与改造前逐字节一致(存量兼容):
  - ISO 输出 = 秒精度、UTC 以 `Z` 结尾(`2026-10-06T06:14:45Z`)——原 `.replace(microsecond=0).isoformat().replace("+00:00", "Z")`;
    AIPOS-F232: 须同秒可区分的时刻另用 iso_ms(毫秒精度 `2026-10-06T06:14:45.123Z`), 读侧解析比较。
  - slug 式样按名取(既有落盘名各式并存, 只收口实现不改式样; 新式样只许加进 SLUG_FORMATS)。
零依赖(只用标准库), 不 import 产品其他模块。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

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


#: AIPOS-F249 件①: 时刻读取的观察者(看板真相缓存开启时由 task_loader 装上; None = 不观察, CLI / 门行为不变)。
#: 调用形 observer(kind, moment): kind = "read"(utc_now: 未声明判定翻转的读取, 缓存推导按分钟粒度失效, 同 F245)/
#: "judged"(judged_now: 读者声明它对此刻做的每个时间判定都经 holds_until 报出翻转时刻)/ "until"(holds_until 报的翻转时刻)。
_observer: Callable[[str, datetime], None] | None = None


#: judged_now 正在读时刻(本线程): utc_now 不另报「未声明的读取」
_judging = threading.local()


def utc_now() -> datetime:
    """当前时刻(UTC, 带时区)。"""
    value = datetime.now(timezone.utc)
    observer = _observer
    if observer is not None and not getattr(_judging, "depth", 0):
        observer("read", value)
    return value


def judged_now(read: Callable[[], datetime] | None = None) -> datetime:
    """AIPOS-F249 件①: 当前时刻(UTC), 供「对它做的每个时间判定都经 holds_until 报出翻转时刻」的读者使用(到期 / 逾期 / 停滞 / 陈旧 /
    窗口 / 整天数): 看板真相缓存据此让结果一直有效到最早的翻转时刻(不按分钟失效)。由它派生而只作展示的时刻值(建议命令里的
    「现在 + 有效期」、响应时间戳)= 时间字段(与 F245 同口径)。read = 调用方模块里的 utc_now(缺省本模块的; 照旧经它取时刻, 换掉它的
    夹具 / 调用方照常生效)。缓存之外(CLI / 门)与 utc_now 完全相同。"""
    _judging.depth = getattr(_judging, "depth", 0) + 1
    try:
        value = (read or utc_now)()
    finally:
        _judging.depth -= 1
    observer = _observer
    if observer is not None:
        observer("judged", value)
    return value


def holds_until(now: datetime, *moments: datetime | float | int | None) -> None:
    """AIPOS-F249 件①: 报出时间判定的翻转时刻。对 now 做的「now < m / now >= m」类判定到 m 之前结果不变; m <= now = 已定(时间只往前走),
    不报。m 可为 datetime(无时区 = UTC)/ epoch 秒 / None(忽略)。「now <= m / now > m」类判定的翻转在 m 之后, 调用方传 m 加一个最小刻度
    (timedelta(microseconds=1))。只在看板真相缓存记录推导时生效; 否则无事。"""
    observer = _observer
    if observer is None:
        return
    base = now.timestamp()
    for moment in moments:
        if moment is None:
            continue
        if hasattr(moment, "timestamp"):  # a datetime (duck-typed: callers / tests may hold a datetime subclass)
            stamp = (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()
        else:
            stamp = float(moment)
        if stamp > base:
            observer("until", datetime.fromtimestamp(stamp, timezone.utc))


def holds_count(now: datetime, anchor: datetime | float | int | None, unit_seconds: float, count: int | None) -> None:
    """AIPOS-F249 件①: 报出「自 anchor 起已满几个单位」类计数(整天数 / 整小时数)的下一次变化时刻 = anchor + (count + 1) × 单位;
    算出的时刻不晚于 now(调用方的浮点计算与精确边界差一个刻度)= 报 now 后 1 秒(马上重判, 不缓存过界)。count / anchor 为 None = 不报。"""
    if _observer is None or anchor is None or count is None:
        return
    start = anchor.timestamp() if hasattr(anchor, "timestamp") else float(anchor)
    moment = start + (int(count) + 1) * float(unit_seconds)
    holds_until(now, moment if moment > now.timestamp() else now.timestamp() + 1.0)


def local_now() -> datetime:
    """当前时刻(本机本地时区, 不带 tzinfo)——只给沿用本地日期的既有落盘名(governance add 按日文件/备份名)。"""
    return datetime.now()


def iso_z(moment: datetime | None = None) -> str:
    """ISO 输出: 秒精度, UTC 写作 `Z`。moment 缺省 = 当前 UTC。"""
    value = utc_now() if moment is None else moment
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_ms(moment: datetime | None = None) -> str:
    """AIPOS-F232 件⑤: ISO 输出毫秒精度(UTC, `Z` 结尾, 如 `2026-10-25T04:11:49.123Z`)——同秒两写须可区分的时刻(顾问复核记录时刻 /
    返工轮登记时刻)。读侧比较一律解析成时刻(秒级存量记录 `…49Z` 视同 `…49.000Z`), 不按字符串比。moment 缺省 = 当前 UTC。"""
    value = utc_now() if moment is None else moment
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
