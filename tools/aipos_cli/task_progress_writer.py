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
            "verdict": "BLOCK",
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
            "verdict": "BLOCK",
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
        "record_type": "task_progress_event",
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
    
    # Build markdown
    lines = ["---"]
    for key in ["record_type", "event_type", "task_id", "actor", "agent_instance", "timestamp", "model_self_reported", "stage", "summary", "reason"]:
        if key in metadata and metadata[key]:
            value = metadata[key]
            # YAML escape for special chars
            needs_quoting = any(char in str(value) for char in [":", "#", "[", "]", "{", "}", "\n"]) or str(value) != str(value).strip()
            if needs_quoting:
                escaped_value = str(value).replace("'", "''")
                lines.append(f"{key}: '{escaped_value}'")
            else:
                lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append(f"# Task Progress Event: {event_type}")
    lines.append("")
    lines.append(f"Agent `{actor}` reported {event_type} for task `{task_id}` at {timestamp}.")
    lines.append("")
    if summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")
    if reason:
        lines.append("## Reason")
        lines.append("")
        lines.append(reason)
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("This event was reported via the `lybra task-progress` CLI command (AIPOS-FND-1).")
    lines.append("Same write logic as the task_progress MCP verb (AIPOS-323).")
    lines.append("")
    
    event_file.write_text("\n".join(lines), encoding="utf-8")
    
    return {
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
