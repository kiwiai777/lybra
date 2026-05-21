from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_writer import render_markdown_task_card

EXTERNAL_INTAKE_DRAFTS_DIR = Path("5_tasks/drafts/external_intake")
EXTERNAL_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
REQUIRED_PAYLOAD_FIELDS = [
    "source_tag",
    "client_tag",
    "external_ref",
    "title",
    "body",
    "submitted_at",
    "submitter_ref",
    "capability_scope",
]
ALLOWED_PRIORITIES = {"low", "medium", "high", "urgent"}


def load_intake_payload_from_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("External intake payload JSON must be an object")
    return data


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _add(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _is_missing(value: Any) -> bool:
    return value in (None, "")


def _parse_iso_datetime(value: Any, field: str, blocking_reasons: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        _add(blocking_reasons, f"Missing required field: {field}")
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _add(blocking_reasons, f"Invalid {field}: expected ISO timestamp")
        return None


def _normalize_text(value: Any, *, field: str, blocking_reasons: list[str], max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        _add(blocking_reasons, f"Missing required field: {field}")
        return ""
    text = value.strip()
    if max_length is not None and len(text) > max_length:
        _add(blocking_reasons, f"Invalid {field}: length exceeds {max_length}")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        _add(blocking_reasons, f"Invalid {field}: control characters are not allowed")
    return text


def _normalize_tag(value: Any, field: str, blocking_reasons: list[str]) -> str:
    text = _normalize_text(value, field=field, blocking_reasons=blocking_reasons, max_length=64)
    if text and not EXTERNAL_TAG_PATTERN.fullmatch(text):
        _add(blocking_reasons, f"Invalid {field} format")
    return text


def _normalize_external_ref(value: Any, blocking_reasons: list[str]) -> str:
    text = _normalize_text(value, field="external_ref", blocking_reasons=blocking_reasons, max_length=256)
    if text and any(ord(char) < 32 for char in text):
        _add(blocking_reasons, "Invalid external_ref format")
    return text


def _normalize_scope(value: Any, *, client_tag: str, blocking_reasons: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _add(blocking_reasons, "Missing required field: capability_scope")
        return {}

    token_ref = str(value.get("token_ref") or value.get("token_id") or "").strip()
    if not token_ref:
        _add(blocking_reasons, "capability_scope.token_ref or capability_scope.token_id is required")

    operations = value.get("operations")
    if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
        _add(blocking_reasons, "capability_scope.operations must be a list of strings")
        operations = []
    if "intake_submit" not in operations:
        _add(blocking_reasons, "capability_scope.operations must include intake_submit")

    projects = value.get("projects")
    if not isinstance(projects, list) or not all(isinstance(item, str) for item in projects):
        _add(blocking_reasons, "capability_scope.projects must be a list of strings")
        projects = []
    if client_tag and client_tag not in projects:
        _add(blocking_reasons, "capability_scope.projects must include client_tag")

    expires_at = _parse_iso_datetime(value.get("expires_at"), "capability_scope.expires_at", blocking_reasons)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            _add(blocking_reasons, "capability_scope.expires_at must be in the future")

    return {
        "token_ref": token_ref,
        "operations": sorted(set(operations)),
        "projects": sorted(set(projects)),
        "expires_at": str(value.get("expires_at") or "").strip(),
        "evidence_ref": str(value.get("evidence_ref") or "").strip() or None,
    }


def _safe_id(source_tag: str, client_tag: str, external_ref: str) -> str:
    digest = hashlib.sha256(f"{source_tag}\n{client_tag}\n{external_ref}".encode("utf-8")).hexdigest()
    return digest[:16]


def _task_id(client_tag: str, safe_id: str) -> str:
    client_part = re.sub(r"[^A-Za-z0-9]+", "-", client_tag).strip("-").upper()
    return f"EXT-{client_part}-{safe_id.upper()}"


def _external_project_exists(repo_root: Path, client_tag: str) -> bool:
    return bool(client_tag) and (repo_root / "2_projects" / client_tag).is_dir()


def _render_body(payload: dict[str, Any], *, actor: str | None) -> str:
    optional_lines = []
    for label, field in (
        ("Source thread", "source_thread_ref"),
        ("Requested due date", "requested_due_date"),
        ("Owner approval evidence", "owner_approval_evidence"),
    ):
        value = payload.get(field)
        if value not in (None, ""):
            optional_lines.append(f"- {label}: {value}")

    lines = [
        "## External Intake",
        "",
        f"- Source tag: {payload['source_tag']}",
        f"- Client tag: {payload['client_tag']}",
        f"- External ref: {payload['external_ref']}",
        f"- Submitted at: {payload['submitted_at']}",
        f"- Submitter ref: {payload['submitter_ref']}",
        f"- Intake actor: {actor or 'unspecified'}",
    ]
    if optional_lines:
        lines.extend(optional_lines)
    lines.extend(
        [
            "",
            "## Normalized Request",
            "",
            str(payload["body"]).strip(),
            "",
            "## Acceptance Criteria",
            "",
            "- Owner reviews this external intake draft before publishing any task.",
            "- Any publish or execution step uses existing Lybra controlled execute flows.",
            "",
            "## Completion Report Instructions",
            "",
            "- Report whether the intake was accepted, rewritten, rejected, or split.",
            "- Preserve source_tag, client_tag, and external_ref when creating follow-up task cards.",
            "",
        ]
    )
    return "\n".join(lines)


def build_external_intake_draft(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    actor: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(payload, dict):
        raise TypeError("payload must be a mapping")

    for field in REQUIRED_PAYLOAD_FIELDS:
        if _is_missing(payload.get(field)):
            _add(blocking_reasons, f"Missing required field: {field}")

    source_tag = _normalize_tag(payload.get("source_tag"), "source_tag", blocking_reasons)
    client_tag = _normalize_tag(payload.get("client_tag"), "client_tag", blocking_reasons)
    external_ref = _normalize_external_ref(payload.get("external_ref"), blocking_reasons)
    title = _normalize_text(payload.get("title"), field="title", blocking_reasons=blocking_reasons, max_length=160)
    body = _normalize_text(payload.get("body"), field="body", blocking_reasons=blocking_reasons)
    submitted_at = _parse_iso_datetime(payload.get("submitted_at"), "submitted_at", blocking_reasons)
    submitter_ref = _normalize_text(payload.get("submitter_ref"), field="submitter_ref", blocking_reasons=blocking_reasons, max_length=160)
    scope = _normalize_scope(payload.get("capability_scope"), client_tag=client_tag, blocking_reasons=blocking_reasons)

    priority = str(payload.get("priority_hint") or "medium").strip().lower()
    if priority not in ALLOWED_PRIORITIES:
        _add(warnings, "priority_hint is not recognized; using medium")
        priority = "medium"

    if client_tag and not _external_project_exists(repo_root, client_tag):
        _add(blocking_reasons, f"client_tag does not map to an existing project: {client_tag}")

    safe_id = _safe_id(source_tag, client_tag, external_ref) if source_tag and client_tag and external_ref else ""
    target_path = str(EXTERNAL_INTAKE_DRAFTS_DIR / f"{safe_id}.md") if safe_id else None
    target_file = repo_root / target_path if target_path else None
    if target_file is not None and target_file.exists():
        _add(blocking_reasons, f"External intake draft already exists: {target_path}")

    normalized_payload = {
        "source_tag": source_tag,
        "client_tag": client_tag,
        "external_ref": external_ref,
        "title": title,
        "body": body,
        "submitted_at": str(payload.get("submitted_at") or "").strip(),
        "submitter_ref": submitter_ref,
        "priority_hint": priority,
        "requested_due_date": str(payload.get("requested_due_date") or "").strip() or None,
        "source_thread_ref": str(payload.get("source_thread_ref") or "").strip() or None,
        "owner_approval_evidence": str(payload.get("owner_approval_evidence") or "").strip() or None,
        "capability_scope": scope,
    }
    task_id = _task_id(client_tag, safe_id) if safe_id and client_tag else None
    metadata = {
        "task_id": task_id,
        "title": f"Review external intake: {title}" if title else None,
        "project": client_tag,
        "task_type": "one_shot",
        "assigned_to": "planner",
        "agent_instance": "planner",
        "context_bundle": "external_intake",
        "task_mode": "planning",
        "model_tier": "L3",
        "priority": priority,
        "status": "pending",
        "created_by": actor or source_tag or "external_intake",
        "needs_owner": True,
        "output_target": str(EXTERNAL_INTAKE_DRAFTS_DIR),
        "artifact_policy": "draft_only",
        "polling_mode": "manual_owner_review",
        "claim_policy": "owner_review_required",
        "report_mode": "completion_summary",
        "recurrence": "none",
        "source_tag": source_tag,
        "client_tag": client_tag,
        "external_ref": external_ref,
        "draft_id": f"external_intake_{safe_id}" if safe_id else None,
        "draft_status": "draft",
        "draft_created_by": actor or source_tag or "external_intake",
        "draft_created_at": _utc_now(),
        "draft_updated_at": _utc_now(),
        "draft_publish_target": "5_tasks/queue/pending/",
    }
    rendered_markdown = render_markdown_task_card(metadata, _render_body(normalized_payload, actor=actor))
    planned_writes = []
    if target_path:
        planned_writes.append(
            {
                "path": target_path,
                "kind": "create",
                "type": "draft_markdown",
                "record_type": "external_intake_draft",
            }
        )

    verdict = "BLOCK" if blocking_reasons else ("WARN" if warnings else "PASS")
    result: dict[str, Any] = {
        "action": "intake_submit",
        "dry_run": dry_run,
        "safe_id": safe_id or None,
        "task_id": task_id,
        "target_path": target_path,
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "planned_writes": planned_writes,
        "would_write": verdict != "BLOCK" and bool(target_path),
        "rendered_markdown": rendered_markdown,
        "original_payload": normalized_payload,
        "capability_scope": scope,
        "submitted_at_valid": submitted_at is not None,
    }

    if dry_run:
        return result

    if verdict == "BLOCK" or target_file is None:
        result["wrote"] = False
        return result

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(rendered_markdown, encoding="utf-8")
    result["wrote"] = True
    return result
