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
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.schema_constants import RecordType

RECORD_EMPTY = "RECORD_EMPTY"


QUEUE_DIRS = {
    "pending": "5_tasks/queue/pending",
    "claimed": "5_tasks/queue/claimed",
    "completed": "5_tasks/queue/completed",
    "blocked": "5_tasks/queue/blocked",
    "withdrawn": "5_tasks/queue/withdrawn",
}

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
    for state, rel_dir in QUEUE_DIRS.items():
        queue_dir = governance_root / rel_dir
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
    
    for task_id in sorted(task_ids):
        queue_state, card_path = _get_queue_state(governance_root, task_id)
        fm_state = _get_frontmatter_state(card_path) if card_path else None
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

    return {
        "scanned": len(task_ids),
        "issues": issues,
    }


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
    """AIPOS-C3B 大项C③ + AIPOS-F79D 件④: 先重铸错位/空 session 记录, 再按 records 重建卡的一致状态。"""
    task_id = task_id.upper()
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
    target_dir_name = QUEUE_DIRS.get(record_state)
    if target_dir_name is None:
        # returned 状态 → 卡应留在 claimed/ 等审计
        if record_state == "returned":
            target_dir_name = QUEUE_DIRS["claimed"]
        else:
            return {
                "task_id": task_id,
                "repaired": False,
                "dry_run": dry_run,
                "message": f"records 推导状态={record_state} 无对应队列目录",
                "actions": [],
            }
    
    target_dir = governance_root / target_dir_name
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
