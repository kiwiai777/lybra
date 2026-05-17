from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ORCHESTRATION_ROOT = Path("5_tasks/orchestration")
EVENT_LOG_FILENAME = "orchestration_events.md"
ORCHESTRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

ALLOWED_EVENT_TYPES = {
    "orchestration_created",
    "planner_assigned",
    "planner_tick_started",
    "planner_tick_completed",
    "planner_paused",
    "planner_resumed",
    "planner_verdict_recorded",
    "subtask_created",
    "subtask_draft_proposed",
    "subtask_publish_ready",
    "subtask_claimed",
    "subtask_completed",
    "subtask_blocked",
    "review_submitted",
    "repair_requested",
    "quota_warning",
    "quota_exhausted",
    "runtime_unavailable",
    "needs_owner_raised",
    "owner_decision_recorded",
    "audit_handoff_requested",
    "handoff_recommended",
    "handoff_approved",
    "orchestration_completed",
    "orchestration_cancelled",
    "orchestration_failed",
}
ALLOWED_SEVERITIES = {"info", "warning", "needs_owner", "blocking"}
REQUIRED_FIELDS = ["event_id", "orchestration_id", "event_type", "timestamp", "actor", "source", "summary"]


def load_event_payload_from_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Event payload JSON must be an object")
    return data


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return ""
    if any(char in text for char in [":", "#", "[", "]", "{", "}", "\n"]) or text != text.strip():
        return "'" + text.replace("'", "''") + "'"
    return text


def _is_safe_orchestration_id(value: str) -> bool:
    if not value or "/" in value or "\\" in value or ".." in value:
        return False
    return bool(ORCHESTRATION_ID_PATTERN.fullmatch(value))


def _target_path_for(orchestration_id: str) -> Path:
    return ORCHESTRATION_ROOT / orchestration_id / EVENT_LOG_FILENAME


def _contains_event_id(log_text: str, event_id: str) -> bool:
    pattern = re.compile(rf"(?m)^\s*-\s*event_id:\s*{re.escape(event_id)}\s*$|^\s*event_id:\s*{re.escape(event_id)}\s*$")
    return bool(pattern.search(log_text))


def _normalize_event(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    blocking: list[str] = []
    event = {field: _safe_text(payload.get(field)) for field in REQUIRED_FIELDS}
    event["severity"] = _safe_text(payload.get("severity")) or "info"
    event["related_task_id"] = _safe_text(payload.get("related_task_id"))
    event["related_subtask_id"] = _safe_text(payload.get("related_subtask_id"))
    event["related_iteration_id"] = _safe_text(payload.get("related_iteration_id"))
    event["details"] = _safe_text(payload.get("details"))

    refs = _as_list(payload.get("refs"))
    forum_thread_ref = _safe_text(payload.get("forum_thread_ref"))
    if forum_thread_ref and forum_thread_ref not in refs:
        refs.append(forum_thread_ref)
    event["refs"] = refs

    for field in REQUIRED_FIELDS:
        if not event[field]:
            blocking.append(f"Missing required field: {field}")
    if not _safe_text(payload.get("forum_thread_ref")):
        blocking.append("forum_thread_ref is required")
    if not _is_safe_orchestration_id(event["orchestration_id"]):
        blocking.append("orchestration_id is path-unsafe")
    if event["event_type"] and event["event_type"] not in ALLOWED_EVENT_TYPES:
        blocking.append(f"Unsupported event_type: {event['event_type']}")
    if event["severity"] not in ALLOWED_SEVERITIES:
        blocking.append(f"Unsupported severity: {event['severity']}")
    if not event["refs"]:
        blocking.append("refs must include forum/control-plane evidence")
    if event["event_type"] == "owner_decision_recorded":
        joined_refs = " ".join(event["refs"]).lower()
        if "owner" not in joined_refs and "decision" not in joined_refs:
            blocking.append("owner_decision_recorded requires Owner decision evidence in refs")
    return event, blocking


def render_event_entry(event: dict[str, Any]) -> str:
    ordered = [
        "event_id",
        "orchestration_id",
        "event_type",
        "timestamp",
        "actor",
        "source",
        "related_task_id",
        "related_subtask_id",
        "related_iteration_id",
        "severity",
        "summary",
        "details",
        "refs",
    ]
    lines: list[str] = []
    for index, key in enumerate(ordered):
        prefix = "- " if index == 0 else "  "
        value = event.get(key)
        if key == "refs":
            lines.append(f"{prefix}{key}:")
            for item in event.get("refs", []):
                lines.append(f"    - {_yaml_scalar(item)}")
            continue
        lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def _snapshot_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": result.get("action"),
        "actor": result.get("actor"),
        "target_path": result.get("target_path"),
        "event_entry": result.get("event_entry"),
        "planned_writes": result.get("planned_writes", []),
        "blocking_reasons": result.get("blocking_reasons", []),
        "owner_confirmation_required": result.get("owner_confirmation_required", False),
    }


def snapshot_hash(result: dict[str, Any]) -> str:
    encoded = json.dumps(_snapshot_payload(result), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_event_append_plan(repo_root: Path, payload: dict[str, Any], *, actor: str | None = None) -> dict[str, Any]:
    event, blocking = _normalize_event(payload)
    actor_value = _safe_text(actor)
    if actor_value and actor_value != event.get("actor"):
        blocking.append("actor must match payload actor")
    target_rel = _target_path_for(event.get("orchestration_id", "invalid")).as_posix()
    target_path = repo_root / target_rel
    planned_writes = [
        {
            "path": target_rel,
            "kind": "append",
            "type": "orchestration_event_log",
        }
    ]

    try:
        target_path.resolve().relative_to((repo_root / ORCHESTRATION_ROOT).resolve())
    except ValueError:
        blocking.append("target path is outside 5_tasks/orchestration")

    if target_path.exists() and target_path.is_file():
        text = target_path.read_text(encoding="utf-8")
        if _contains_event_id(text, event.get("event_id", "")):
            blocking.append(f"Duplicate event_id already exists: {event.get('event_id')}")
    elif target_path.exists():
        blocking.append(f"Event log target exists but is not a file: {target_rel}")

    result: dict[str, Any] = {
        "action": "orchestration_event_append",
        "actor": actor_value or event.get("actor"),
        "verdict": "BLOCK" if blocking else "PASS",
        "blocking_reasons": blocking,
        "warnings": [],
        "target_path": target_rel,
        "planned_writes": planned_writes,
        "event_entry": event,
        "append_markdown": render_event_entry(event),
        "owner_confirmation_required": event.get("severity") in {"needs_owner", "blocking"}
        or event.get("event_type") == "needs_owner_raised",
        "safety_notice": (
            "AIPOS-65 appends one orchestration event under 5_tasks/orchestration/. "
            "It does not write planner iterations, summary state, queue tasks, drafts, records, forum backends, or git state."
        ),
    }
    result["write_snapshot_hash"] = snapshot_hash(result)
    return result


def append_orchestration_event(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    actor: str | None = None,
    dry_run: bool = False,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    result = build_event_append_plan(repo_root, payload, actor=actor)
    result["dry_run"] = dry_run
    result["would_write"] = result["verdict"] != "BLOCK"

    if dry_run:
        result["wrote"] = False
        return result

    if result["verdict"] == "BLOCK":
        result["wrote"] = False
        return result

    if not expected_hash:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = ["expected hash is required for non-dry-run append"]
        result["would_write"] = False
        result["wrote"] = False
        return result

    if expected_hash != result["write_snapshot_hash"]:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = ["expected hash does not match current append plan"]
        result["would_write"] = False
        result["wrote"] = False
        return result

    target_path = repo_root / result["target_path"]
    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    with target_path.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        if existing:
            handle.write("\n")
        handle.write(result["append_markdown"])
    result["wrote"] = True
    return result
