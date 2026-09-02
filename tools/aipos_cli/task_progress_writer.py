"""AIPOS-FND-1: Task progress event writer (local CLI variant).

Writes task progress event records to
``5_tasks/records/events/<task_id>/<event_type>_<timestamp>.md``
in append-only fashion. This is the local CLI variant that bypasses MCP scope
checks, wrapping the same write logic as lybra_task_progress (tools.py:2847).

Used by `lybra task-progress` CLI command for same-machine progress reporting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.schema_constants import RecordType, Verdict

# AIPOS-SMOKE-LOOP-1 FIX (task-progress session 真落盘):
# 事件记录写到 records/events/<task_id>/ 后,还必须追加更新对应 session record
# (records/sessions/<task_id>/<session_id>.md)——后者才是 N2 执行期真相载体
# (claim 时创建, claim/return/audit 共用)。原实现只写 event 文件却报 ok:True,
# 属"报 OK 实没写"隐患(HAZARD-LEDGER 08-12 行12)。session 不存在必须响亮报错(禁吞错)。


# event_type -> session_status / current_state (session record 是 N2 真相载体)
_SESSION_STATUS_FOR_EVENT = {
    "started": "active",
    "progress": "active",
    "completed": "completed",
    "blocked": "blocked",
}


def _resolve_session_record_path(repo_root: Path, task_id: str) -> tuple[Path | None, str | None, str | None]:
    """Locate the active session record for a task.

    Reads the task card (5_tasks/queue/**) frontmatter for ``active_session_id``
    and maps it to the session record path. Returns ``(path, session_id, reason)``;
    when no session can be resolved, ``path`` is None and ``reason`` explains why.
    """
    from tools.aipos_cli.task_loader import find_task_by_id
    from tools.aipos_cli.record_writer import session_record_path

    task, _matches = find_task_by_id(task_id, repo_root)
    if task is None:
        return None, None, f"task {task_id!r} not found in 5_tasks/queue/"
    session_id = str(task.get("metadata", {}).get("active_session_id") or task.get("active_session_id") or "").strip()
    if not session_id:
        return None, None, (
            f"task {task_id!r} has no active_session_id (not claimed, or claim did not stamp a session). "
            "task-progress cannot append to a non-existent session record."
        )
    path = session_record_path(repo_root, task_id, session_id)
    if not path.exists():
        return None, session_id, (
            f"session record not found at {path.relative_to(repo_root)} ""(session_id={session_id}). Claim should have created it."
        )
    return path, session_id, None


def _update_session_record(
    session_path: Path,
    *,
    repo_root: Path,
    task_id: str,
    actor: str,
    event_type: str,
    timestamp: str,
    summary: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Append a progress event line to the session record and refresh its status.

    Returns a small dict describing the update (ok / reason). On any failure this
    raises so the caller can surface a loud error (never silently ok:True).
    """
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.record_writer import (
        MCP_SESSION_FRONTMATTER_ORDER,
        SESSION_FRONTMATTER_ORDER,
        render_markdown,
    )

    text = session_path.read_text(encoding="utf-8")
    metadata, body, _warnings = parse_markdown_frontmatter(text)
    # pick the order that matches whichever writer created this record
    order = MCP_SESSION_FRONTMATTER_ORDER if metadata.get("surface") else SESSION_FRONTMATTER_ORDER

    metadata["updated_at"] = timestamp
    new_status = _SESSION_STATUS_FOR_EVENT.get(event_type)
    if new_status:
        metadata["session_status"] = new_status
        if event_type == "completed":
            metadata["current_state"] = "completed"
        elif event_type == "blocked":
            metadata["current_state"] = "blocked"
    metadata["event_count"] = int(metadata.get("event_count") or 1) + 1

    event_line = f"- {timestamp} task_progress:{event_type} by {actor}"
    detail = summary or reason
    if detail:
        event_line += f"; {detail.strip()}"
    event_line += "."

    new_body = body.rstrip()
    if "## Events" in new_body:
        new_body = new_body + "\n" + event_line
    else:
        new_body = (new_body + "\n\n## Events\n\n" + event_line) if new_body else ("## Events\n\n" + event_line)

    # AIPOS-F64-fix1: 迁移到统一 writer (write_records_atomic)
    from tools.aipos_cli.record_writer import write_records_atomic
    
    session_markdown = render_markdown(metadata, new_body, order)
    session_record_id = session_path.stem  # e.g., "session_TASK-1_20260902_120000"
    records_to_write = [("session", session_record_id, session_markdown)]
    write_result = write_records_atomic(repo_root, records_to_write)
    
    return {
        "ok": write_result["ok"],
        "session_record_path": str(session_path.resolve().relative_to(repo_root)),
        "session_id": metadata.get("session_id"),
        "event_count": metadata["event_count"],
        "session_status": metadata.get("session_status"),
    }


def write_task_progress_event(
    repo_root: Path,
    task_id: str,
    actor: str,
    event_type: str,
    *,
    agent_instance: str | None = None,
    summary: str | None = None,
    model_self_reported: str | None = None,
    stage: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Write a task progress event record.
    
    Args:
        repo_root: Workspace root (must contain 5_tasks/queue)
        task_id: Task ID
        actor: Actor reporting progress
        event_type: One of: started, progress, completed, blocked
        agent_instance: Agent instance name (optional)
        summary: Event summary (optional)
        model_self_reported: Model used for capability ledger (optional)
        stage: Current stage (optional)
        reason: Reason for blocked events (optional)
    
    Returns:
        Result dict with ok, event_file, timestamp, etc.
    """
    # Validate workspace
    queue_dir = repo_root / "5_tasks" / "queue"
    if not queue_dir.is_dir():
        return {
            "ok": False,
            "verdict": Verdict.BLOCK,
            "operation": "task_progress",
            "blocking_reasons": [
                f"Not a Lybra workspace: {repo_root} (missing 5_tasks/queue). "
                "Task progress events must land in the governance workspace."
            ],
        }
    
    # Validate event_type
    if event_type not in ("started", "progress", "completed", "blocked"):
        return {
            "ok": False,
            "verdict": Verdict.BLOCK,
            "operation": "task_progress",
            "blocking_reasons": [
                f"Invalid event_type: {event_type}. Must be one of: started, progress, completed, blocked."
            ],
        }
    
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    
    # Build event record
    events_dir = repo_root / "5_tasks" / "records" / "events" / task_id
    events_dir.mkdir(parents=True, exist_ok=True)
    
    # Event filename: <event_type>_<timestamp>.md
    timestamp_slug = timestamp.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    event_file = events_dir / f"{event_type}_{timestamp_slug}.md"
    
    # Build frontmatter
    metadata: dict[str, Any] = {
        "record_type": RecordType.TASK_PROGRESS_EVENT,
        "event_type": event_type,
        "task_id": task_id,
        "actor": actor,
        "timestamp": timestamp,
    }
    if agent_instance:
        metadata["agent_instance"] = agent_instance
    if model_self_reported:
        metadata["model_self_reported"] = model_self_reported
    if stage:
        metadata["stage"] = stage
    if summary:
        metadata["summary"] = summary
    if reason:
        metadata["reason"] = reason
    
    # Build markdown — AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown)
    from tools.aipos_cli.record_writer import render_markdown as _render_markdown_single_source

    body_lines = [
        f"# Task Progress Event: {event_type}",
        "",
        f"Agent `{actor}` reported {event_type} for task `{task_id}` at {timestamp}.",
        "",
    ]
    if summary:
        body_lines.extend(["## Summary", "", summary, ""])
    if reason:
        body_lines.extend(["## Reason", "", reason, ""])
    body_lines.extend([
        "---",
        "",
        "This event was reported via the `lybra task-progress` CLI command (AIPOS-FND-1).",
        "Same write logic as the task_progress MCP verb (AIPOS-323).",
        "",
    ])
    body = "\n".join(body_lines)

    event_order = ["record_type", "event_type", "task_id", "actor", "agent_instance",
                   "timestamp", "model_self_reported", "stage", "summary", "reason"]
    event_markdown = _render_markdown_single_source(metadata, body, event_order)

    # AIPOS-F64-fix1: 迁移到统一 writer (write_records_atomic)
    from tools.aipos_cli.record_writer import write_records_atomic
    
    event_record_id = event_file.stem  # e.g., "event_TASK-1_20260902_120000"
    records_to_write = [("event", event_record_id, event_markdown)]
    write_result = write_records_atomic(repo_root, records_to_write)
    
    # AIPOS-SMOKE-LOOP-1 FIX: 追加更新 session record (N2 真相载体)。session 找不到/不存在
    # 必须响亮报错 —— 不再 ok:True 实没写 session (HAZARD-LEDGER 08-12 行12)。
    result = {
        "ok": True,
        "operation": "task_progress",
        "event_type": event_type,
        "task_id": task_id,
        "actor": actor,
        "timestamp": timestamp,
        "event_file": str(event_file.relative_to(repo_root)),
        "summary": summary or "(none)",
        "model_self_reported": model_self_reported or "(none)",
        "stage": stage or "(none)",
        "reason": reason or "(none)",
    }
    try:
        session_path, session_id, resolve_reason = _resolve_session_record_path(repo_root, task_id)
        if session_path is None:
            # session 不可解析 = 真问题, 响亮报错 (禁吞错)
            result["ok"] = False
            result["verdict"] = Verdict.BLOCK
            result["recorded"] = False
            result["blocking_reasons"] = [
                f"task_progress event was written to {event_file.relative_to(repo_root)} "
                f"but the session record could NOT be updated: {resolve_reason}. "
                f"The session record is the N2 execution-truth carrier; an event without "
                f"a session update is invisible to the board."
            ]
            return result
        update = _update_session_record(
            session_path,
            repo_root=repo_root,
            task_id=task_id,
            actor=actor,
            event_type=event_type,
            timestamp=timestamp,
            summary=summary,
            reason=reason,
        )
        result["recorded"] = True
        result["session_update"] = update
    except Exception as exc:
        # 任何 session 更新异常都响亮报错, 绝不吞 (R4A F-3 同款红线)
        result["ok"] = False
        result["verdict"] = Verdict.BLOCK
        result["recorded"] = False
        result["blocking_reasons"] = [
            f"task_progress event was written but session record update FAILED: {type(exc).__name__}: {exc}"
        ]
    return result
