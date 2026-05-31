from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.agent_profiles import actor_matches_task_actor
from tools.aipos_cli.draft_validator import find_case_insensitive_path_collision
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.record_writer import (
    build_claim_log_markdown,
    build_runtime_id,
    build_session_record_markdown,
    claim_record_paths,
    load_session_record,
    session_record_path,
    update_session_record_markdown,
    validate_safe_task_id,
)
from tools.aipos_cli.task_loader import QUEUE_STATES, find_task_by_id, load_task_file
from tools.aipos_cli.validator import validate_single_task

QUEUE_ROOT = Path("5_tasks/queue")
QUEUE_STATE_DIRS = {state: QUEUE_ROOT / state for state in QUEUE_STATES}
MUTATION_SAFETY_NOTICE = (
    "AIPOS-31 queue mutation only moves validated task cards within 5_tasks/queue/. "
    "It does not write records, run agents, or mutate orchestration state."
)
MUTATION_RECORDS_SAFETY_NOTICE = (
    "AIPOS-32 queue mutation with records enabled writes only within 5_tasks/queue/ and 5_tasks/records/. "
    "Records writing is opt-in and limited to 5_tasks/records/. It does not run agents or mutate orchestration state."
)
ALLOWED_TRANSITIONS = {
    "claim": ("pending", "claimed"),
    "block": ("claimed", "blocked"),
    "complete": ("claimed", "completed"),
    "reopen": ("blocked", "pending"),
}
FRONTMATTER_ORDER = [
    "task_id",
    "title",
    "project",
    "task_type",
    "assigned_to",
    "agent_instance",
    "context_bundle",
    "task_mode",
    "task_class",
    "complexity_note",
    "model_tier",
    "priority",
    "status",
    "created_by",
    "needs_owner",
    "output_target",
    "artifact_policy",
    "session_policy",
    "context_isolation",
    "artifact_scope",
    "memory_scope",
    "polling_mode",
    "claim_policy",
    "report_mode",
    "recurrence",
    "claim_id",
    "claimed_by",
    "claimed_at",
    "active_session_id",
    "last_session_id",
    "blocked_by",
    "blocked_at",
    "block_reason",
    "completed_by",
    "completed_at",
    "artifact_links",
    "reopened_by",
    "reopened_at",
    "reopen_reason",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", value) or "actor"


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


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return value


def render_task_markdown(metadata: dict[str, Any], body: str) -> str:
    ordered_keys = [key for key in FRONTMATTER_ORDER if key in metadata]
    ordered_keys.extend(sorted(key for key in metadata if key not in ordered_keys))
    lines = ["---"]
    for key in ordered_keys:
        value = metadata[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"- {_yaml_scalar(item)}")
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.extend(["---", body.rstrip(), ""])
    return "\n".join(lines)


def _resolved_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def resolve_queue_path(repo_root: Path, provided_path: str | Path) -> Path:
    raw_path = Path(provided_path)
    path = raw_path.resolve() if raw_path.is_absolute() else (repo_root / raw_path).resolve()
    queue_root = (repo_root / QUEUE_ROOT).resolve()
    if not _resolved_within(queue_root, path):
        raise ValueError(f"Task path is outside 5_tasks/queue: {provided_path}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Task path is not a markdown file: {provided_path}")
    state_names = {part.name for part in path.parents if _resolved_within(queue_root, part)}
    if not any(state in state_names for state in QUEUE_STATES):
        raise ValueError(f"Task path is not inside a queue state directory: {provided_path}")
    if not path.exists():
        raise FileNotFoundError(f"Task path does not exist: {provided_path}")
    if not path.is_file():
        raise FileNotFoundError(f"Task path is not a file: {provided_path}")
    return path


def _read_task_markdown(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body, warnings = parse_markdown_frontmatter(text)
    return _normalize_value(metadata), body, warnings


def _append_unique_list(existing: Any, item: str) -> list[str]:
    values = [str(value) for value in existing] if isinstance(existing, list) else ([] if existing in (None, "") else [str(existing)])
    if item not in values:
        values.append(item)
    return values


def _select_task(repo_root: Path, *, task_id: str | None = None, task_path: str | None = None) -> dict[str, Any]:
    if bool(task_id) == bool(task_path):
        raise ValueError("Exactly one of --task-id or --path must be provided")
    if task_id:
        selected, matches = find_task_by_id(task_id, repo_root)
        if not matches:
            raise ValueError(f"No task found for task_id: {task_id}")
        if len(matches) > 1:
            paths = ", ".join(sorted(str(match.get("path")) for match in matches))
            raise ValueError(f"Duplicate task_id {task_id} found in: {paths}")
        assert selected is not None
        return selected
    path = resolve_queue_path(repo_root, str(task_path))
    return load_task_file(path, repo_root)


def _prepare_claim(metadata: dict[str, Any], actor: str, timestamp: str) -> dict[str, Any]:
    updated = dict(metadata)
    task_id = str(updated.get("task_id"))
    updated["status"] = "claimed"
    updated["claimed_by"] = actor
    updated["claimed_at"] = timestamp
    updated["claim_id"] = build_runtime_id("claim", task_id, timestamp, actor)
    updated["active_session_id"] = build_runtime_id("session", task_id, timestamp, actor)
    if updated.get("needs_owner") is None:
        updated["needs_owner"] = False
    return updated


def _prepare_block(metadata: dict[str, Any], actor: str, timestamp: str, reason: str) -> dict[str, Any]:
    updated = dict(metadata)
    updated["status"] = "blocked"
    updated["blocked_by"] = actor
    updated["blocked_at"] = timestamp
    updated["block_reason"] = reason
    updated["needs_owner"] = True
    if updated.get("active_session_id") not in (None, ""):
        updated["last_session_id"] = updated.get("active_session_id")
    updated.pop("active_session_id", None)
    return updated


def _prepare_complete(metadata: dict[str, Any], actor: str, timestamp: str, report_link: str) -> dict[str, Any]:
    updated = dict(metadata)
    updated["status"] = "completed"
    updated["completed_by"] = actor
    updated["completed_at"] = timestamp
    updated["needs_owner"] = False
    updated["approval_required"] = False
    updated["owner_review_required"] = False
    if updated.get("active_session_id") not in (None, ""):
        updated["last_session_id"] = updated.get("active_session_id")
    updated.pop("active_session_id", None)
    updated.pop("needs_owner_reasons", None)
    updated["artifact_links"] = _append_unique_list(updated.get("artifact_links"), report_link)
    return updated


def _prepare_reopen(metadata: dict[str, Any], actor: str, timestamp: str, reason: str) -> dict[str, Any]:
    updated = dict(metadata)
    updated["status"] = "pending"
    updated["reopened_by"] = actor
    updated["reopened_at"] = timestamp
    updated["reopen_reason"] = reason
    updated["needs_owner"] = False
    updated.pop("active_session_id", None)
    updated.pop("claim_id", None)
    return updated


def _mutation_metadata(action: str, metadata: dict[str, Any], actor: str, *, reason: str | None = None, report_link: str | None = None) -> dict[str, Any]:
    timestamp = _utc_now()
    if action == "claim":
        return _prepare_claim(metadata, actor, timestamp)
    if action == "block":
        assert reason is not None
        return _prepare_block(metadata, actor, timestamp, reason)
    if action == "complete":
        assert report_link is not None
        return _prepare_complete(metadata, actor, timestamp, report_link)
    if action == "reopen":
        assert reason is not None
        return _prepare_reopen(metadata, actor, timestamp, reason)
    raise ValueError(f"Unsupported queue mutation action: {action}")


def _base_result(source_path: Path, repo_root: Path, source_task: dict[str, Any], action: str, dry_run: bool, actor: str, to_state: str) -> dict[str, Any]:
    return {
        "action": f"queue_{action}",
        "dry_run": dry_run,
        "would_write": False,
        "wrote": False,
        "would_move": False,
        "moved": False,
        "task_id": source_task.get("task_id"),
        "source_path": str(source_path.relative_to(repo_root)),
        "target_path": None,
        "from_state": source_task.get("queue_state"),
        "to_state": to_state,
        "actor": actor,
        "verdict": "PASS",
        "blocking_reasons": [],
        "warnings": [],
        "classification_warnings": [],
        "planned_writes": [],
        "planned_moves": [],
        "updated_frontmatter": {},
        "safety_notice": MUTATION_SAFETY_NOTICE,
        "with_records": False,
        "records_enabled": False,
    }


def _build_record_plan(path: Path, record_type: str, *, would_write: bool = False, wrote: bool = False, would_update: bool = False, updated: bool = False) -> dict[str, Any]:
    item = {"path": str(path), "record_type": record_type}
    if would_write or wrote:
        item["would_write"] = would_write
        item["wrote"] = wrote
    if would_update or updated:
        item["would_update"] = would_update
        item["updated"] = updated
    return item


def _prepare_records_plan(
    repo_root: Path,
    action: str,
    *,
    source_task: dict[str, Any],
    source_metadata: dict[str, Any],
    updated_metadata: dict[str, Any],
    actor: str,
    reason: str | None,
    report_link: str | None,
) -> dict[str, Any]:
    task_id = str(updated_metadata.get("task_id") or "")
    validate_safe_task_id(task_id)
    timestamp = str(updated_metadata.get("claimed_at") or updated_metadata.get("blocked_at") or updated_metadata.get("completed_at") or updated_metadata.get("reopened_at") or "")
    result: dict[str, Any] = {
        "with_records": True,
        "records_enabled": True,
        "record_writes": [],
        "record_updates": [],
        "record_blocking_reasons": [],
        "record_warnings": [],
        "record_previews": [],
    }
    task_target_path = str(source_task.get("path") or "")

    if action == "claim":
        claim_id = str(updated_metadata.get("claim_id") or "")
        session_id = str(updated_metadata.get("active_session_id") or "")
        claim_path, session_path = claim_record_paths(repo_root, task_id, claim_id, session_id)
        result["proposed_claim_id"] = claim_id
        result["proposed_session_id"] = session_id
        result["claim_log_path"] = str(claim_path.relative_to(repo_root))
        result["session_record_path"] = str(session_path.relative_to(repo_root))
        if claim_path.exists():
            result["record_blocking_reasons"].append(f"Claim log already exists: {claim_path.relative_to(repo_root)}")
        if session_path.exists():
            result["record_blocking_reasons"].append(f"Session record already exists: {session_path.relative_to(repo_root)}")
        claim_markdown = build_claim_log_markdown(
            task_id=task_id,
            task_path=task_target_path.replace("/pending/", "/claimed/"),
            actor=actor,
            claim_id=claim_id,
            session_id=session_id,
            created_at=timestamp,
        )
        session_markdown = build_session_record_markdown(
            task_id=task_id,
            task_path=task_target_path.replace("/pending/", "/claimed/"),
            actor=actor,
            session_id=session_id,
            claim_id=claim_id,
            created_at=timestamp,
        )
        result["record_writes"] = [
            _build_record_plan(Path(result["claim_log_path"]), "claim_log", would_write=not result["record_blocking_reasons"]),
            _build_record_plan(Path(result["session_record_path"]), "session_record", would_write=not result["record_blocking_reasons"]),
        ]
        result["record_previews"] = [
            {"path": result["claim_log_path"], "record_type": "claim_log", "rendered_markdown": claim_markdown},
            {"path": result["session_record_path"], "record_type": "session_record", "rendered_markdown": session_markdown},
        ]
        result["claim_log_markdown"] = claim_markdown
        result["session_record_markdown"] = session_markdown
        return result

    active_or_last = str(source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or "")
    if action in {"block", "complete"} and not str(source_metadata.get("active_session_id") or "").strip():
        result["record_blocking_reasons"].append(f"{action} with records requires active_session_id")
        return result
    if action == "reopen" and not active_or_last:
        result["record_warnings"].append("reopen with records found no active_session_id or last_session_id; no session record update planned")
        return result

    session_id = str(source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or "")
    path = session_record_path(repo_root, task_id, session_id)
    result["proposed_session_id"] = session_id
    result["session_record_path"] = str(path.relative_to(repo_root))
    if not path.exists():
        result["record_blocking_reasons"].append(f"Session record does not exist: {path.relative_to(repo_root)}")
        return result

    existing_metadata, existing_body, parse_warnings = load_session_record(path)
    for warning in parse_warnings:
        result["record_blocking_reasons"].append(f"Session record parse issue: {warning}")
    if existing_metadata.get("task_id") not in (None, task_id):
        result["record_blocking_reasons"].append("Session record task_id does not match queue task")
        return result

    if action == "block":
        updated_markdown = update_session_record_markdown(
            existing_metadata,
            existing_body,
            actor=actor,
            timestamp=timestamp,
            status="blocked",
            current_state="blocked",
            event_line=f"{timestamp} blocked by {actor}: {reason}",
        )
    elif action == "complete":
        updated_markdown = update_session_record_markdown(
            existing_metadata,
            existing_body,
            actor=actor,
            timestamp=timestamp,
            status="completed",
            current_state="completed",
            event_line=f"{timestamp} completed by {actor}: {report_link}",
        )
    else:
        updated_markdown = update_session_record_markdown(
            existing_metadata,
            existing_body,
            actor=actor,
            timestamp=timestamp,
            status="reopened",
            current_state="pending",
            event_line=f"{timestamp} reopened by {actor}: {reason}",
        )
    result["record_updates"] = [_build_record_plan(Path(result["session_record_path"]), "session_record", would_update=True)]
    result["record_previews"] = [
        {"path": result["session_record_path"], "record_type": "session_record", "rendered_markdown": updated_markdown}
    ]
    result["session_record_markdown"] = updated_markdown
    return result


def mutate_queue_task(
    repo_root: Path,
    action: str,
    *,
    task_id: str | None = None,
    task_path: str | None = None,
    actor: str,
    reason: str | None = None,
    report_link: str | None = None,
    dry_run: bool = False,
    profiles: dict[str, Any] | None = None,
    with_records: bool = False,
) -> dict[str, Any]:
    if action not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Unsupported queue mutation action: {action}")
    if not str(actor or "").strip():
        raise ValueError("--actor is required")
    if action in {"block", "reopen"} and not str(reason or "").strip():
        raise ValueError("--reason is required and must be non-empty")
    if action == "complete" and not str(report_link or "").strip():
        raise ValueError("--report-link is required and must be non-empty")

    source_task = _select_task(repo_root, task_id=task_id, task_path=task_path)
    source_path = (repo_root / str(source_task["path"])).resolve()
    from_state, to_state = ALLOWED_TRANSITIONS[action]
    target_path = repo_root / QUEUE_STATE_DIRS[to_state] / source_path.name
    source_metadata, source_body, _warnings = _read_task_markdown(source_path)
    result = _base_result(source_path, repo_root, source_task, action, dry_run, actor, to_state)
    result["target_path"] = str(target_path.relative_to(repo_root))
    if with_records:
        result["with_records"] = True
        result["records_enabled"] = True
        result["safety_notice"] = MUTATION_RECORDS_SAFETY_NOTICE

    validation_profiles = profiles if profiles else None
    validation = validate_single_task(source_task, current_actor=actor, profiles=validation_profiles)
    if validation["blocking_reasons"]:
        result["blocking_reasons"].extend(validation["blocking_reasons"])
    if validation["warnings"]:
        result["warnings"].extend(validation["warnings"])
    result["classification_warnings"].extend(validation.get("classification_warnings", []))
    needs_owner_reasons: list[str] = []

    if source_task.get("queue_state") != from_state:
        result["blocking_reasons"].append(
            f"Invalid transition for {action}: expected source state {from_state}, found {source_task.get('queue_state')}"
        )
    if source_task.get("frontmatter_status") != from_state:
        result["blocking_reasons"].append(
            f"Directory/status mismatch blocks {action}: expected frontmatter status {from_state}"
        )

    claimed_by = str(source_metadata.get("claimed_by") or "")
    claimed_actor_matches = (
        actor_matches_task_actor(actor, claimed_by, profiles)
        if profiles
        else claimed_by == actor
    )
    if action in {"block", "complete"} and not claimed_actor_matches:
        result["blocking_reasons"].append("Task is claimed by another actor")

    pending_collision = find_case_insensitive_path_collision(target_path.parent, target_path.name)
    if pending_collision is not None:
        collision_rel = str(pending_collision.resolve().relative_to(repo_root.resolve()))
        if pending_collision.resolve() != target_path.resolve():
            result["blocking_reasons"].append(f"Case-insensitive target filename collision: {collision_rel}")
        elif target_path.exists():
            result["blocking_reasons"].append(f"Target file already exists: {result['target_path']}")

    updated_metadata = _mutation_metadata(
        action,
        source_metadata,
        actor,
        reason=str(reason or "").strip() or None,
        report_link=str(report_link or "").strip() or None,
    )
    rendered_markdown = render_task_markdown(updated_metadata, source_body)
    result["updated_frontmatter"] = updated_metadata
    result["planned_writes"] = [{"path": result["target_path"], "kind": "create", "type": "task_markdown"}]
    result["planned_moves"] = [{"from": result["source_path"], "to": result["target_path"], "kind": "queue_state_move"}]

    record_plan: dict[str, Any] = {
        "with_records": False,
        "records_enabled": False,
        "record_writes": [],
        "record_updates": [],
        "record_blocking_reasons": [],
        "record_warnings": [],
        "record_previews": [],
    }
    if with_records:
        record_plan = _prepare_records_plan(
            repo_root,
            action,
            source_task=source_task,
            source_metadata=source_metadata,
            updated_metadata=updated_metadata,
            actor=actor,
            reason=str(reason or "").strip() or None,
            report_link=str(report_link or "").strip() or None,
        )
        result.update({key: value for key, value in record_plan.items() if key not in {"record_previews", "claim_log_markdown", "session_record_markdown"}})
        for reason_text in record_plan.get("record_blocking_reasons", []):
            if reason_text not in result["blocking_reasons"]:
                result["blocking_reasons"].append(reason_text)
        for warning_text in record_plan.get("record_warnings", []):
            if warning_text not in result["warnings"]:
                result["warnings"].append(warning_text)

    preview_task = load_task_file(source_path, repo_root)
    preview_task["metadata"] = updated_metadata
    preview_task["frontmatter_status"] = updated_metadata.get("status")
    preview_task["queue_state"] = to_state
    preview_task["path"] = str(target_path.relative_to(repo_root))
    preview_task["assigned_to"] = updated_metadata.get("assigned_to")
    preview_task["agent_instance"] = updated_metadata.get("agent_instance")
    preview_task["claimed_by"] = updated_metadata.get("claimed_by")
    preview_task["needs_owner"] = updated_metadata.get("needs_owner")
    preview_validation = validate_single_task(preview_task, current_actor=actor, profiles=validation_profiles)
    if preview_validation["blocking_reasons"]:
        for reason_text in preview_validation["blocking_reasons"]:
            if reason_text not in result["blocking_reasons"]:
                result["blocking_reasons"].append(reason_text)
    for warning_text in preview_validation["warnings"]:
        if warning_text not in result["warnings"]:
            result["warnings"].append(warning_text)
    for warning_text in preview_validation.get("classification_warnings", []):
        if warning_text not in result["classification_warnings"]:
            result["classification_warnings"].append(warning_text)
    needs_owner_reasons.extend(
        reason_text
        for reason_text in preview_validation.get("needs_owner_reasons", [])
        if reason_text not in needs_owner_reasons
    )

    if result["blocking_reasons"]:
        result["verdict"] = "BLOCK"
    elif needs_owner_reasons:
        result["verdict"] = "NEEDS_OWNER"
    elif [warning for warning in result["warnings"] if warning not in result["classification_warnings"]]:
        result["verdict"] = "WARN"
    else:
        result["verdict"] = "PASS"

    result["would_write"] = result["verdict"] != "BLOCK"
    result["would_move"] = result["verdict"] != "BLOCK"
    if dry_run:
        result["rendered_markdown"] = rendered_markdown
        if with_records and record_plan.get("record_previews"):
            result["record_previews"] = record_plan["record_previews"]
        return result
    if result["verdict"] == "BLOCK":
        return result

    if with_records:
        for item in record_plan.get("record_writes", []):
            path = repo_root / str(item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if item["record_type"] == "claim_log":
                path.write_text(str(record_plan["claim_log_markdown"]), encoding="utf-8")
            elif item["record_type"] == "session_record":
                path.write_text(str(record_plan["session_record_markdown"]), encoding="utf-8")
            item["wrote"] = True
        for item in record_plan.get("record_updates", []):
            path = repo_root / str(item["path"])
            path.write_text(str(record_plan["session_record_markdown"]), encoding="utf-8")
            item["updated"] = True

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered_markdown, encoding="utf-8")
    source_path.unlink()
    result["wrote"] = True
    result["moved"] = True
    return result
