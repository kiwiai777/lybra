"""AIPOS-C3B 大项C①③: state lint + state repair

state lint: 卡状态三方一致检查(队列目录 × frontmatter status × records)
state repair: 按 records 重建卡的一致状态(坏卡修复)

三方来源(transitions.schema.json#state_consistency):
  1. 队列目录 (5_tasks/queue/{pending|claimed|completed}/)
  2. frontmatter status 字段
  3. records 目录最新记录推导的状态

AIPOS-F79D 件④: 记录文件面
  - lint: records/<kind>/<ID>/*.md 为 0 字节 → RECORD_EMPTY(ERROR, 带出口 state repair --task-id)
  - repair: sessions/<ID>/ 空文件 + claims/<ID>/ 下同名 session 正常份(或仅错位副本) → 按声明位重铸
    (内容移到 record_writer.session_record_path 声明位, 删空文件与错位副本, 写 repair 记录)

AIPOS-F87 件②: 卡面 frontmatter 不可解析
  - lint: 卡经产品唯一读取口 parse_markdown_frontmatter 出解析告警 → FRONTMATTER_INVALID(ERROR, 带出口 state repair --task-id)
  - repair: 保值规整——只对解析失败所在的顶层单行标量做最小引号化(该行由单源 record_writer.render_frontmatter_line 产出),
    规整后须可解析、除被规整行外逐字节不变、其余字段值与原文去掉被规整行后的解析结果相等、被规整字段值 = 原行文本;
    任一不满足 = unresolved 拒改。写 records/events/<ID>/event_repair_*.md(前后差异摘要); dry-run 先行; 不改队列状态。
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.schema_constants import RecordType

RECORD_EMPTY = "RECORD_EMPTY"
FRONTMATTER_INVALID = "FRONTMATTER_INVALID"
FRONTMATTER_REPAIR_ACTOR = "lybra state repair"

try:  # 定位解析失败行需要 PyYAML 的错误标记; 缺席时规整一律 unresolved(不猜)
    import yaml as _yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    _yaml = None


# AIPOS-F89 件① M8: 队列状态名 = 目录名(task_loader.QUEUE_STATES); 目录 = 队列根(唯一读取口 task_loader.queue_root_for)/<状态>。
# 原写死 5_tasks/queue/<状态> 的映射表删除。
from tools.aipos_cli.task_loader import QUEUE_STATES as QUEUE_DIRS  # noqa: E402  — 状态名元组(保留旧名, 调用方只用作状态集)


def _queue_state_dir(governance_root: Path, state: str) -> Path:
    from tools.aipos_cli.task_loader import queue_root_for

    return queue_root_for(governance_root) / state

RECORD_TYPE_TO_STATE = {
    "closure": "completed",
    "claim": "claimed",
    "return": "returned",
    "publish": "pending",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _derive_state_from_records(governance_root: Path, task_id: str) -> str | None:
    """从 records 推导任务的真实状态(以最新记录为准)。"""
    records_dir = governance_root / "5_tasks" / "records"
    
    # 按优先级检查各类记录(最新优先)
    # closure > return > claim > publish
    checks = [
        ("closures", "completed"),
        ("returns", "returned"),
        ("claims", "claimed"),
        ("publishes", "pending"),
    ]
    
    latest_state = None
    latest_ts = ""
    
    for record_type, state in checks:
        type_dir = records_dir / record_type / task_id
        if not type_dir.is_dir():
            continue
        for f in type_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                fm, _, _ = parse_markdown_frontmatter(text)
                # 取时间戳最大的记录
                ts = str(fm.get("closed_at") or fm.get("returned_at") or fm.get("claimed_at") or fm.get("published_at") or fm.get("timestamp") or "")
                if ts > latest_ts:
                    latest_ts = ts
                    latest_state = state
            except Exception:
                continue
    
    return latest_state


def _get_queue_state(governance_root: Path, task_id: str) -> tuple[str | None, Path | None]:
    """获取任务在队列目录中的状态(AIPOS-F78B 件①: 唯一查找 task_loader.find_task_card; 状态名 = 目录名 = QUEUE_DIRS 键)。"""
    from tools.aipos_cli.task_loader import find_task_card

    card_path, state = find_task_card(governance_root, task_id, states=tuple(QUEUE_DIRS))
    return state, card_path


def _get_frontmatter_state(card_path: Path) -> str | None:
    """从卡 frontmatter 读取 status 字段。"""
    try:
        text = card_path.read_text(encoding="utf-8")
        fm, _, _ = parse_markdown_frontmatter(text)
        return str(fm.get("status") or "").strip() or None
    except Exception:
        return None


def _list_all_task_ids(governance_root: Path) -> set[str]:
    """列出所有在队列目录中的 task_id。"""
    task_ids: set[str] = set()
    for state in QUEUE_DIRS:
        queue_dir = _queue_state_dir(governance_root, state)
        if not queue_dir.is_dir():
            continue
        for f in queue_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                fm, _, _ = parse_markdown_frontmatter(text)
                tid = str(fm.get("task_id") or "").strip()
                if tid:
                    task_ids.add(tid)
                else:
                    # fallback: 从文件名提取
                    task_ids.add(f.stem.upper())
            except Exception:
                task_ids.add(f.stem.upper())
    return task_ids


def run_state_lint(
    governance_root: Path,
    task_id_filter: str | None = None,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C①: 卡状态三方一致 lint。
    
    检查每个任务的三方一致性:
      1. 队列目录位置
      2. frontmatter status
      3. records 推导状态
    
    Returns:
        {
            "scanned": int,
            "issues": [{"task_id": str, "severity": str, "message": str, ...}],
        }
    """
    issues: list[dict[str, Any]] = []
    
    if task_id_filter:
        task_ids = {task_id_filter.upper()}
    else:
        task_ids = _list_all_task_ids(governance_root)
    
    invalid_frontmatter: list[dict[str, Any]] = []
    for task_id in sorted(task_ids):
        queue_state, card_path = _get_queue_state(governance_root, task_id)
        fm_state = _get_frontmatter_state(card_path) if card_path else None
        # AIPOS-F87 件②: 同一趟里顺带判卡面可解析(不另起一遍全量查找)
        if card_path is not None:
            invalid = _invalid_frontmatter_entry(governance_root, task_id, card_path)
            if invalid is not None:
                invalid_frontmatter.append(invalid)
        record_state = _derive_state_from_records(governance_root, task_id)
        
        # 检查: completed 卡必须有 closure 记录
        if queue_state == "completed" and record_state != "completed":
            issues.append({
                "task_id": task_id,
                "severity": "ERROR",
                "message": f"卡在 completed/ 但无 closure 记录(断层)",
                "queue_state": queue_state,
                "fm_state": fm_state,
                "record_state": record_state,
            })
        
        # 检查: claimed 卡必须有 claim 记录
        if queue_state == "claimed" and record_state not in ("claimed", "returned"):
            issues.append({
                "task_id": task_id,
                "severity": "ERROR",
                "message": f"卡在 claimed/ 但无 claim 记录或记录状态不一致(断层)",
                "queue_state": queue_state,
                "fm_state": fm_state,
                "record_state": record_state,
            })
        
        # 检查: frontmatter status 与队列目录不一致
        if queue_state and fm_state and fm_state != queue_state:
            # 允许某些合法组合(如 returned 状态卡仍在 claimed/ 等审计)
            allowed_combos = {
                ("claimed", "returned"),  # 卡等审计
            }
            if (queue_state, fm_state) not in allowed_combos:
                issues.append({
                    "task_id": task_id,
                    "severity": "WARN",
                    "message": f"frontmatter status={fm_state} 与队列目录位置={queue_state} 不一致",
                    "queue_state": queue_state,
                    "fm_state": fm_state,
                    "record_state": record_state,
                })
        
        # 检查: 有 records 但卡不在对应队列
        if record_state == "completed" and queue_state != "completed":
            issues.append({
                "task_id": task_id,
                "severity": "WARN",
                "message": f"有 closure 记录但卡不在 completed/(在 {queue_state or 'unknown'})",
                "queue_state": queue_state,
                "fm_state": fm_state,
                "record_state": record_state,
            })
    
    # AIPOS-F79D 件④: 0 字节记录文件 → RECORD_EMPTY(F73ER/F78CR 两案: sessions/<ID>/ 下空文件, 正常内容错位在 claims/)
    for empty in find_empty_record_files(governance_root, task_id_filter):
        issues.append({
            "task_id": empty["task_id"],
            "severity": "ERROR",
            "code": RECORD_EMPTY,
            "message": (
                f"{RECORD_EMPTY}: 记录文件为 0 字节 {empty['path']}({empty['record_kind']}/); "
                f"出口: lybra state repair --task-id {empty['task_id']} --workspace-root {governance_root}"
                + ("(sessions/ 空文件按 claims/ 下同名正常份重铸到声明位)" if empty["record_kind"] == "sessions" else "")
            ),
            "path": empty["path"],
            "record_kind": empty["record_kind"],
        })

    # AIPOS-F87 件②: 卡面 frontmatter 不可解析 → FRONTMATTER_INVALID(判据 = 产品唯一读取口出告警, 与 validator「Frontmatter parse issue」同源)
    for invalid in invalid_frontmatter:
        issues.append({
            "task_id": invalid["task_id"],
            "severity": "ERROR",
            "code": FRONTMATTER_INVALID,
            "message": (
                f"{FRONTMATTER_INVALID}: 卡面 frontmatter 不可解析 {invalid['path']}: {invalid['warnings'][0]}; "
                f"出口: lybra state repair --task-id {invalid['task_id']} --workspace-root {governance_root} --dry-run"
                "(先预览保值规整, 无误后去掉 --dry-run 落盘)"
            ),
            "path": invalid["path"],
            "warnings": invalid["warnings"],
        })

    return {
        "scanned": len(task_ids),
        "issues": issues,
    }


def card_frontmatter_warnings(text: str) -> list[str]:
    """AIPOS-F87 件②: 卡文本经产品唯一读取口的解析告警(空 = 可解析)。FRONTMATTER_INVALID 与 my-tasks 选卡同此判据。"""
    _meta, _body, warnings = parse_markdown_frontmatter(text)
    return [str(w) for w in warnings]


def _invalid_frontmatter_entry(governance_root: Path, task_id: str, card_path: Path) -> dict[str, Any] | None:
    """AIPOS-F87 件②: 卡面不可解析 → {task_id, path, warnings}; 可解析 → None(只读)。"""
    warnings = card_frontmatter_warnings(card_path.read_text(encoding="utf-8"))
    if not warnings:
        return None
    return {
        "task_id": task_id,
        "path": card_path.resolve().relative_to(Path(governance_root).resolve()).as_posix(),
        "warnings": warnings,
    }


_TOP_LEVEL_SCALAR_LINE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_.-]*):[ \t]+(?P<value>\S.*?)[ \t]*$")
# 值以这些字符开头 = 原作者可能意在引号串 / 流式集合 / 块标量: 引号化会改义, 不做(unresolved)
_REQUOTE_REFUSED_LEADS = ("'", '"', "[", "{", "|", ">")


def _split_frontmatter_lines(text: str) -> tuple[list[str], int] | None:
    """与 parse_markdown_frontmatter 同一围栏规则: 首行以 --- 起, 第一条 strip()=='---' 的行收尾。返回 (按 \\n 切的行, 收尾行号)。"""
    if not text.startswith("---"):
        return None
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines, index
    return None


def plan_frontmatter_requote(text: str) -> dict[str, Any]:
    """AIPOS-F87 件②: 为不可解析的卡面 frontmatter 计划保值规整(纯函数, 不写盘)。

    逐次让 PyYAML 指出解析失败行 → 该行须为顶层单行 `key: value`(下一非空行为顶层键或围栏, 即无续行), 值不以引号/流式/块标量起头 →
    用单源 render_frontmatter_line(key, 原行值文本) 重写该行。直到可解析或无法安全规整。

    放行条件(全满足, 否则 unresolved, repaired_text=None):
      a) 规整后全文经产品读取口解析无告警;
      b) 除被规整行外逐行(逐字节)不变, 正文不变;
      c) 被规整字段解析值 == 原行值文本(逐字);
      d) 原 frontmatter 去掉被规整行后的解析结果 == 规整后去掉被规整字段的解析结果(其余字段值语义不变)。
    """
    from tools.aipos_cli.record_writer import render_frontmatter_line

    result: dict[str, Any] = {"repaired_text": None, "repairs": [], "unresolved": []}
    if _yaml is None:
        result["unresolved"].append("PyYAML 不可用, 无法定位解析失败行, 不做规整(不猜)")
        return result
    split = _split_frontmatter_lines(text)
    if split is None:
        result["unresolved"].append("frontmatter 围栏缺失(无起始 --- 或无收尾 ---), 不是单行标量问题, 不做规整")
        return result
    lines, end = split
    original_lines = list(lines)
    repaired_idx: dict[int, dict[str, Any]] = {}
    for _attempt in range(end):
        fm_text = "\n".join(lines[1:end])
        try:
            loaded = _yaml.safe_load(fm_text)
        except _yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            if mark is None:
                result["unresolved"].append(f"YAML 解析失败但无行标记: {exc}")
                return result
            idx = mark.line + 1  # frontmatter 第 0 行 = 文件第 1 行(0 基)
            if not 1 <= idx < end:
                result["unresolved"].append(f"解析失败标记落在 frontmatter 外(第 {idx + 1} 行): {exc.problem}")
                return result
            if idx in repaired_idx:
                result["unresolved"].append(f"第 {idx + 1} 行规整后仍解析失败: {exc.problem}")
                return result
            line = lines[idx]
            match = _TOP_LEVEL_SCALAR_LINE.match(line)
            if match is None:
                result["unresolved"].append(
                    f"第 {idx + 1} 行不是顶层单行 `key: value`(缩进/列表项/多行结构), 不做规整: {line[:80]!r}"
                )
                return result
            key, value = match.group("key"), match.group("value")
            if value.startswith(_REQUOTE_REFUSED_LEADS):
                result["unresolved"].append(
                    f"第 {idx + 1} 行 {key} 的值以 {value[0]!r} 起头(引号串/流式集合/块标量), 引号化可能改义, 不做规整"
                )
                return result
            nxt = next((j for j in range(idx + 1, end) if lines[j].strip()), None)
            if nxt is not None and (lines[nxt][:1] in (" ", "\t") or lines[nxt].startswith("- ")):
                result["unresolved"].append(f"第 {idx + 1} 行 {key} 后跟续行/子结构, 不是单行标量, 不做规整")
                return result
            try:
                new_line = render_frontmatter_line(key, value)
            except ValueError as exc2:
                result["unresolved"].append(f"第 {idx + 1} 行 {key} 无法单行安全序列化: {exc2}")
                return result
            repaired_idx[idx] = {"line": idx + 1, "key": key, "value": value, "before": line, "after": new_line}
            lines[idx] = new_line
            continue
        except _yaml.YAMLError as exc:
            result["unresolved"].append(f"YAML 解析失败且无法定位: {exc}")
            return result
        if not isinstance(loaded, dict):
            result["unresolved"].append("frontmatter 解析结果不是映射, 不做规整")
            return result
        break
    else:
        result["unresolved"].append("规整轮次超过 frontmatter 行数仍不可解析, 不做规整")
        return result

    if not repaired_idx:
        result["unresolved"].append("PyYAML 可解析但产品读取口仍告警(非单行标量问题), 不做规整")
        return result

    repaired_text = "\n".join(lines)
    parsed, body_after, warnings = parse_markdown_frontmatter(repaired_text)
    _orig_meta, body_before, _w = parse_markdown_frontmatter(text)
    problems: list[str] = []
    if warnings:
        problems.append(f"规整后仍有解析告警: {warnings}")
    if len(lines) != len(original_lines) or any(
        lines[i] != original_lines[i] for i in range(len(lines)) if i not in repaired_idx
    ):
        problems.append("规整改动了被规整行以外的内容")
    if body_after != body_before:
        problems.append("规整改动了正文")
    for item in repaired_idx.values():
        if parsed.get(item["key"]) != item["value"]:
            problems.append(f"字段 {item['key']} 规整后值 {parsed.get(item['key'])!r} ≠ 原行文本 {item['value']!r}")
    rest_text = "\n".join(original_lines[i] for i in range(1, end) if i not in repaired_idx)
    try:
        rest_meta = _yaml.safe_load(rest_text) or {}
    except _yaml.YAMLError as exc:
        problems.append(f"原 frontmatter 去掉被规整行后仍不可解析, 无法证明其余字段语义不变: {exc}")
        rest_meta = None
    if rest_meta is not None:
        repaired_keys = {item["key"] for item in repaired_idx.values()}
        if rest_meta != {k: v for k, v in parsed.items() if k not in repaired_keys}:
            problems.append("其余字段解析值与原文不一致(语义可能改变)")
    if problems:
        result["unresolved"].extend(problems)
        return result
    result["repaired_text"] = repaired_text
    result["repairs"] = [repaired_idx[i] for i in sorted(repaired_idx)]
    return result


def repair_frontmatter_invalid(
    governance_root: Path,
    task_id: str,
    dry_run: bool = False,
    actor: str = FRONTMATTER_REPAIR_ACTOR,
) -> dict[str, Any]:
    """AIPOS-F87 件②: 对 FRONTMATTER_INVALID 卡做保值规整(卡留原队列目录原文件名, 不改队列状态)。

    返回 applicable=False 表示卡面可解析(本修复不适用)。unresolved 非空 = 拒改(不写任何文件)。
    非 dry-run: 写卡 → 回读核对(字节一致且可解析)→ 写 records/events/<ID>/event_repair_*.md; 记录写失败则还原卡原文并抛出。
    """
    import hashlib

    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic

    task_id = task_id.upper()
    root = Path(governance_root).resolve()
    out: dict[str, Any] = {
        "task_id": task_id,
        "applicable": False,
        "dry_run": dry_run,
        "repaired": False,
        "card_path": None,
        "repairs": [],
        "unresolved": [],
        "actions": [],
        "repair_record": None,
    }
    _state, card_path = _get_queue_state(root, task_id)
    if card_path is None:
        return out
    original = card_path.read_text(encoding="utf-8")
    warnings = card_frontmatter_warnings(original)
    if not warnings:
        return out
    rel = card_path.resolve().relative_to(root).as_posix()
    out.update({"applicable": True, "card_path": rel, "warnings": warnings})
    plan = plan_frontmatter_requote(original)
    out["repairs"] = [
        {"line": r["line"], "key": r["key"], "before": r["before"], "after": r["after"]} for r in plan["repairs"]
    ]
    out["unresolved"] = list(plan["unresolved"])
    if plan["repaired_text"] is None:
        out["actions"].append(f"{FRONTMATTER_INVALID}: {rel} 无法安全规整, 拒改(unresolved)")
        return out
    for r in plan["repairs"]:
        out["actions"].append(f"规整 {rel} 第 {r['line']} 行 {r['key']}: 仅加引号(值逐字不变)")
    if dry_run:
        return out

    repaired_text = plan["repaired_text"]
    card_path.write_text(repaired_text, encoding="utf-8")
    readback = card_path.read_text(encoding="utf-8")
    if readback != repaired_text or card_frontmatter_warnings(readback):
        card_path.write_text(original, encoding="utf-8")
        raise RuntimeError(f"{rel} 规整写入后回读不一致或仍不可解析, 已还原原文")

    timestamp = _utc_now()
    ts_slug = timestamp.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
    record_id = f"repair_{task_id}_{ts_slug}"  # write_records_atomic 从 record_id 第二段取 task_id → events/<ID>/event_repair_*.md
    metadata = {
        "record_type": RecordType.TASK_PROGRESS_EVENT,
        "event_type": "frontmatter_repair",
        "task_id": task_id,
        "actor": actor,
        "timestamp": timestamp,
        "repair": f"AIPOS-F87 件②: {FRONTMATTER_INVALID} 保值规整(仅引号化, 值逐字不变, 队列状态不变)",
        "card_path": rel,
        "sha256_before": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "sha256_after": hashlib.sha256(repaired_text.encode("utf-8")).hexdigest(),
        "repaired_lines": [f"L{r['line']} {r['key']}" for r in plan["repairs"]],
    }
    body_lines = [f"# Frontmatter Repair: {task_id}", "", f"卡 `{rel}` 原解析告警: {warnings[0]}", ""]
    for r in plan["repairs"]:
        body_lines += [f"## 第 {r['line']} 行 `{r['key']}`", "", "```diff", f"- {r['before']}", f"+ {r['after']}", "```", ""]
    body_lines.append("除上列行外卡文件逐字节不变; 由 `lybra state repair` 写入。")
    markdown = render_markdown(metadata, "\n".join(body_lines), list(metadata))
    try:
        written = write_records_atomic(root, [("event", record_id, markdown)])
    except Exception:
        card_path.write_text(original, encoding="utf-8")
        raise
    out["repaired"] = True
    out["repair_record"] = written["paths"][0]
    out["actions"].append(f"写 repair 记录: {out['repair_record']}")
    return out


def _records_root(governance_root: Path) -> Path:
    from tools.aipos_cli.record_writer import RECORDS_ROOT

    return governance_root / RECORDS_ROOT


def find_empty_record_files(governance_root: Path, task_id_filter: str | None = None) -> list[dict[str, str]]:
    """AIPOS-F79D 件④: 列出 records/<kind>/<task_id>/*.md 中的 0 字节文件(只读)。"""
    root = _records_root(governance_root)
    found: list[dict[str, str]] = []
    if not root.is_dir():
        return found
    wanted = task_id_filter.upper() if task_id_filter else None
    for kind_dir in sorted(root.iterdir()):
        if not kind_dir.is_dir():
            continue
        for task_dir in sorted(kind_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            if wanted and task_dir.name.upper() != wanted:
                continue
            for record_file in sorted(task_dir.glob("*.md")):
                if record_file.is_file() and record_file.stat().st_size == 0:
                    found.append({
                        "task_id": task_dir.name,
                        "record_kind": kind_dir.name,
                        "path": record_file.relative_to(governance_root).as_posix(),
                    })
    return found


def repair_empty_session_records(
    governance_root: Path,
    task_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """AIPOS-F79D 件④: 按声明位重铸 session 记录。

    对「sessions/<ID>/ 下 0 字节文件 + claims/<ID>/ 下同名正常份(record_type=session_record)」
    以及「仅 claims/ 下错位副本」: 内容写到声明位(record_writer.session_record_path), 删空文件与错位副本,
    写 repair 记录(records/events/<ID>/event_record_repair_<ts>.md)。声明位已有非空内容 → 不覆盖, 列为 unresolved(需人工)。
    无副本可重铸的空文件 → unresolved。禁吞写失败(写失败即抛)。
    """
    from tools.aipos_cli.record_writer import (
        CLAIMS_ROOT,
        SESSIONS_ROOT,
        render_markdown,
        session_record_path,
        write_records_atomic,
    )

    root = governance_root.resolve()
    task_id = task_id.upper()
    sessions_dir = root / SESSIONS_ROOT / task_id
    claims_dir = root / CLAIMS_ROOT / task_id
    actions: list[str] = []
    moved: list[dict[str, str]] = []
    unresolved: list[str] = []

    def rel(path: Path) -> str:
        return path.resolve().relative_to(root).as_posix()

    misplaced: list[Path] = []
    if claims_dir.is_dir():
        for candidate in sorted(claims_dir.glob("*.md")):
            if not candidate.is_file() or candidate.stat().st_size == 0:
                continue
            fm, _body, _warn = parse_markdown_frontmatter(candidate.read_text(encoding="utf-8"))
            if str(fm.get("record_type") or "").strip() == RecordType.SESSION_RECORD:
                misplaced.append(candidate)

    for src in misplaced:
        target = session_record_path(root, task_id, src.stem)
        if target.exists() and target.stat().st_size > 0:
            unresolved.append(f"{rel(target)} 已有非空内容, 与错位副本 {rel(src)} 并存, 不覆盖(需人工比对后删其一)")
            continue
        had_empty = target.exists()
        actions.append(
            f"重铸 session 记录到声明位: {rel(src)} → {rel(target)}"
            + ("(删 0 字节空文件)" if had_empty else "")
        )
        if not dry_run:
            content = src.read_text(encoding="utf-8")
            if had_empty:
                target.unlink()
            write_records_atomic(root, [("session", src.stem, content)])
            src.unlink()
        moved.append({"from": rel(src), "to": rel(target), "removed_empty": "true" if had_empty else "false"})

    if sessions_dir.is_dir():
        for leftover in sorted(sessions_dir.glob("*.md")):
            if leftover.is_file() and leftover.stat().st_size == 0:
                if dry_run and any(m["to"] == rel(leftover) for m in moved):
                    continue
                unresolved.append(f"{rel(leftover)} 为 0 字节且 claims/ 下无同名 session 正常份, 无法重铸(需人工)")

    repair_record: str | None = None
    if moved and not dry_run:
        timestamp = _utc_now()
        ts_slug = timestamp.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
        record_id = f"repair_{task_id}_{ts_slug}"  # write_records_atomic 从 record_id 第二段取 task_id
        metadata = {
            "record_type": RecordType.TASK_PROGRESS_EVENT,
            "event_type": "record_repair",
            "task_id": task_id,
            "actor": "lybra state repair",
            "timestamp": timestamp,
            "repair": "AIPOS-F79D 件④: session 记录按声明位重铸",
            "moved": [f"{m['from']} -> {m['to']}" for m in moved],
        }
        body = "\n".join(
            ["# Record Repair: session records recast to declared position", ""]
            + [f"- `{m['from']}` → `{m['to']}`" + (" (removed 0-byte file)" if m["removed_empty"] == "true" else "") for m in moved]
            + ["", "Declared position: record_writer.session_record_path (records/sessions/<task_id>/). Written by `lybra state repair`.", ""]
        )
        markdown = render_markdown(metadata, body, ["record_type", "event_type", "task_id", "actor", "timestamp", "repair", "moved"])
        written = write_records_atomic(root, [("event", record_id, markdown)])
        repair_record = written["paths"][0]
        actions.append(f"写 repair 记录: {repair_record}")

    return {
        "task_id": task_id,
        "repaired": bool(moved) and not dry_run,
        "dry_run": dry_run,
        "actions": actions,
        "moved": moved,
        "unresolved": unresolved,
        "repair_record": repair_record,
        "message": (
            (f"重铸 {len(moved)} 份 session 记录到声明位" if moved else "无错位/空 session 记录")
            + (f"; {len(unresolved)} 项无法自动重铸" if unresolved else "")
        ),
    }


def repair_task_state(
    governance_root: Path,
    task_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C③ + AIPOS-F79D 件④: 先重铸错位/空 session 记录, 再按 records 重建卡的一致状态。

    AIPOS-F87 件②: 卡面 FRONTMATTER_INVALID 时本次只做卡面保值规整(不重铸记录、不动队列状态——坏卡上的字段读不准,
    按其重建状态会写坏); 规整落盘后再跑一次 state repair 核状态。
    """
    task_id = task_id.upper()
    fm_repair = repair_frontmatter_invalid(governance_root, task_id, dry_run=dry_run)
    if fm_repair["applicable"]:
        if fm_repair["unresolved"]:
            message = f"{FRONTMATTER_INVALID}: 卡面无法安全规整, 拒改({len(fm_repair['unresolved'])} 项 unresolved)"
        elif dry_run:
            message = f"(dry-run) {FRONTMATTER_INVALID}: 将规整 {len(fm_repair['repairs'])} 行(仅引号化); 本次不做记录重铸与队列状态修复"
        else:
            message = f"{FRONTMATTER_INVALID}: 已规整 {len(fm_repair['repairs'])} 行; 队列状态未动, 再跑 state repair 核对状态一致性"
        return {
            "task_id": task_id,
            "repaired": fm_repair["repaired"],
            "dry_run": dry_run,
            "message": message,
            "actions": list(fm_repair["actions"]),
            "frontmatter_repair": fm_repair,
            "unresolved": list(fm_repair["unresolved"]),
        }
    record_repair = repair_empty_session_records(governance_root, task_id, dry_run=dry_run)
    state_repair = _repair_queue_state(governance_root, task_id, dry_run=dry_run)
    actions = list(record_repair["actions"]) + list(state_repair["actions"])
    message = state_repair["message"]
    if record_repair["actions"] or record_repair["unresolved"]:
        message = f"{record_repair['message']}; {message}"
    return {
        "task_id": task_id,
        "repaired": bool(record_repair["repaired"] or state_repair["repaired"]),
        "dry_run": dry_run,
        "message": message,
        "actions": actions,
        "record_repair": record_repair,
        "state_repair": {"repaired": state_repair["repaired"], "message": state_repair["message"]},
    }


def _repair_queue_state(
    governance_root: Path,
    task_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C③: 按 records 重建卡的一致状态。
    
    读 records 推导真实状态 → 修正 frontmatter + 移动队列目录。
    
    Returns:
        {
            "task_id": str,
            "repaired": bool,
            "dry_run": bool,
            "message": str,
            "actions": list[str],
        }
    """
    task_id = task_id.upper()
    record_state = _derive_state_from_records(governance_root, task_id)
    queue_state, card_path = _get_queue_state(governance_root, task_id)
    
    actions: list[str] = []
    
    if record_state is None:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": "无 records 可推导状态,无法修复",
            "actions": [],
        }
    
    if queue_state == record_state or (queue_state == "claimed" and record_state == "returned"):
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": f"卡已一致(队列={queue_state}, records→{record_state})",
            "actions": [],
        }
    
    if card_path is None:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": f"卡文件不存在于任何队列目录,无法修复(需人工检查)",
            "actions": [],
        }
    
    # 需要移动卡到正确的队列目录
    target_dir_name = record_state if record_state in QUEUE_DIRS else None
    if target_dir_name is None:
        # returned 状态 → 卡应留在 claimed/ 等审计
        if record_state == "returned":
            target_dir_name = "claimed"
        else:
            return {
                "task_id": task_id,
                "repaired": False,
                "dry_run": dry_run,
                "message": f"records 推导状态={record_state} 无对应队列目录",
                "actions": [],
            }
    
    target_dir = _queue_state_dir(governance_root, target_dir_name)
    target_path = target_dir / card_path.name
    
    actions.append(f"移动卡: {card_path} → {target_path}")
    
    # 修正 frontmatter status
    try:
        text = card_path.read_text(encoding="utf-8")
        fm, body, _ = parse_markdown_frontmatter(text)
        if fm.get("status") != record_state:
            fm["status"] = record_state
            actions.append(f"修正 frontmatter status: {fm.get('status')} → {record_state}")
    except Exception as e:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": f"读取卡文件失败: {e}",
            "actions": actions,
        }
    
    if dry_run:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": True,
            "message": f"会修复: 队列 {queue_state}→{record_state}",
            "actions": actions,
        }
    
    # 执行修复
    try:
        from tools.aipos_cli.queue_mutation import render_task_markdown
        target_dir.mkdir(parents=True, exist_ok=True)
        rendered = render_task_markdown(fm, body)
        target_path.write_text(rendered, encoding="utf-8")
        if card_path != target_path:
            card_path.unlink()
        return {
            "task_id": task_id,
            "repaired": True,
            "dry_run": False,
            "message": f"已修复: 队列 {queue_state}→{record_state}",
            "actions": actions,
        }
    except Exception as e:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": False,
            "message": f"修复失败: {e}",
            "actions": actions,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
