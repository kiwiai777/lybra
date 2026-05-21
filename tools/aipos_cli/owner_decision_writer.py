from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.record_writer import render_markdown

OWNER_DECISION_RECORDS_DIR = Path("5_tasks/records/owner_decisions")
DECISION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ALLOWED_DECISION_STATUSES = {"approved", "rejected", "needs_revision", "superseded", "expired"}
ALLOWED_CAPTURE_SURFACES = {"board", "cli", "mcp", "external_client"}
REQUIRED_EVIDENCE_FIELDS = [
    "evidence_id",
    "source_tag",
    "client_tag",
    "external_ref",
    "approval_actor_ref",
    "approval_timestamp",
    "approval_intent",
    "evidence_hash",
    "captured_by",
    "capture_method",
    "redaction_status",
    "refs",
]
REQUIRED_PAYLOAD_FIELDS = [
    "decision_id",
    "decision_type",
    "decision_status",
    "decided_at",
    "decided_by_ref",
    "captured_by",
    "capture_surface",
    "decision_summary",
    "applies_to",
    "approval_scope",
    "owner_approval_evidence",
    "capability_scope",
]


def load_owner_decision_payload_from_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Owner decision payload JSON must be an object")
    return data


def _add(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _is_missing(value: Any) -> bool:
    return value in (None, "")


def _parse_iso_datetime(value: Any, field: str, blocking_reasons: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        _add(blocking_reasons, f"Missing required field: {field}")
        return ""
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _add(blocking_reasons, f"Invalid {field}: expected ISO timestamp")
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    if text and not TAG_PATTERN.fullmatch(text):
        _add(blocking_reasons, f"Invalid {field} format")
    return text


def _normalize_decision_id(value: Any, blocking_reasons: list[str]) -> str:
    text = _normalize_text(value, field="decision_id", blocking_reasons=blocking_reasons, max_length=128)
    if text and not DECISION_ID_PATTERN.fullmatch(text):
        _add(blocking_reasons, "Invalid decision_id format")
    return text


def _normalize_list(value: Any, field: str, blocking_reasons: list[str]) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _add(blocking_reasons, f"{field} must be a list of strings")
        return []
    return [item.strip() for item in value if item.strip()]


def _normalize_mapping(value: Any, field: str, blocking_reasons: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _add(blocking_reasons, f"Missing required field: {field}")
        return {}
    return dict(value)


def _normalize_applies_to(value: Any, blocking_reasons: list[str]) -> dict[str, Any]:
    data = _normalize_mapping(value, "applies_to", blocking_reasons)
    normalized = {
        "project": str(data.get("project") or "").strip() or None,
        "task_id": str(data.get("task_id") or "").strip() or None,
        "draft_path": str(data.get("draft_path") or "").strip() or None,
        "orchestration_id": str(data.get("orchestration_id") or "").strip() or None,
        "iteration_id": str(data.get("iteration_id") or "").strip() or None,
        "event_id": str(data.get("event_id") or "").strip() or None,
        "external_ref": str(data.get("external_ref") or "").strip() or None,
    }
    if not any(normalized.values()):
        _add(blocking_reasons, "applies_to must identify at least one scoped target")
    return normalized


def _normalize_approval_scope(value: Any, blocking_reasons: list[str]) -> dict[str, Any]:
    data = _normalize_mapping(value, "approval_scope", blocking_reasons)
    operation = _normalize_text(data.get("operation"), field="approval_scope.operation", blocking_reasons=blocking_reasons, max_length=96)
    authority_boundary = _normalize_text(
        data.get("authority_boundary"),
        field="approval_scope.authority_boundary",
        blocking_reasons=blocking_reasons,
        max_length=160,
    )
    return {
        "operation": operation,
        "authority_boundary": authority_boundary,
        "allowed_next_action": str(data.get("allowed_next_action") or "").strip() or None,
        "expires_at": str(data.get("expires_at") or "").strip() or None,
    }


def _normalize_evidence(value: Any, blocking_reasons: list[str]) -> dict[str, Any]:
    data = _normalize_mapping(value, "owner_approval_evidence", blocking_reasons)
    for field in REQUIRED_EVIDENCE_FIELDS:
        if _is_missing(data.get(field)):
            _add(blocking_reasons, f"Missing required field: owner_approval_evidence.{field}")

    refs = _normalize_list(data.get("refs"), "owner_approval_evidence.refs", blocking_reasons)
    source_tag = _normalize_tag(data.get("source_tag"), "owner_approval_evidence.source_tag", blocking_reasons)
    client_tag = _normalize_tag(data.get("client_tag"), "owner_approval_evidence.client_tag", blocking_reasons)
    approval_timestamp = _parse_iso_datetime(data.get("approval_timestamp"), "owner_approval_evidence.approval_timestamp", blocking_reasons)
    return {
        "evidence_id": _normalize_text(data.get("evidence_id"), field="owner_approval_evidence.evidence_id", blocking_reasons=blocking_reasons, max_length=128),
        "source_tag": source_tag,
        "client_tag": client_tag,
        "external_ref": _normalize_text(data.get("external_ref"), field="owner_approval_evidence.external_ref", blocking_reasons=blocking_reasons, max_length=256),
        "approval_actor_ref": _normalize_text(
            data.get("approval_actor_ref"),
            field="owner_approval_evidence.approval_actor_ref",
            blocking_reasons=blocking_reasons,
            max_length=160,
        ),
        "approval_timestamp": approval_timestamp,
        "approval_intent": _normalize_text(
            data.get("approval_intent"),
            field="owner_approval_evidence.approval_intent",
            blocking_reasons=blocking_reasons,
            max_length=160,
        ),
        "evidence_hash": _normalize_text(data.get("evidence_hash"), field="owner_approval_evidence.evidence_hash", blocking_reasons=blocking_reasons, max_length=160),
        "evidence_ref": str(data.get("evidence_ref") or "").strip() or None,
        "captured_by": _normalize_text(data.get("captured_by"), field="owner_approval_evidence.captured_by", blocking_reasons=blocking_reasons, max_length=160),
        "capture_method": _normalize_text(data.get("capture_method"), field="owner_approval_evidence.capture_method", blocking_reasons=blocking_reasons, max_length=96),
        "redaction_status": _normalize_text(data.get("redaction_status"), field="owner_approval_evidence.redaction_status", blocking_reasons=blocking_reasons, max_length=96),
        "refs": refs,
    }


def _normalize_capability_scope(value: Any, *, project: str | None, blocking_reasons: list[str]) -> dict[str, Any]:
    data = _normalize_mapping(value, "capability_scope", blocking_reasons)
    token_ref = str(data.get("token_ref") or data.get("token_id") or "").strip()
    if not token_ref:
        _add(blocking_reasons, "capability_scope.token_ref or capability_scope.token_id is required")

    operations = data.get("operations")
    if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
        _add(blocking_reasons, "capability_scope.operations must be a list of strings")
        operations = []
    if "owner_decision_record" not in operations:
        _add(blocking_reasons, "capability_scope.operations must include owner_decision_record")

    projects = data.get("projects")
    if not isinstance(projects, list) or not all(isinstance(item, str) for item in projects):
        _add(blocking_reasons, "capability_scope.projects must be a list of strings")
        projects = []
    if project and project not in projects:
        _add(blocking_reasons, "capability_scope.projects must include applies_to.project")

    expires_at = _parse_iso_datetime(data.get("expires_at"), "capability_scope.expires_at", blocking_reasons)
    if expires_at:
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_dt <= datetime.now(timezone.utc):
            _add(blocking_reasons, "capability_scope.expires_at must be in the future")

    return {
        "token_ref": token_ref,
        "operations": sorted(set(operations)),
        "projects": sorted(set(projects)),
        "expires_at": expires_at,
        "evidence_ref": str(data.get("evidence_ref") or "").strip() or None,
    }


def _render_body(record: dict[str, Any]) -> str:
    applies_to = record["applies_to"]
    approval_scope = record["approval_scope"]
    evidence = record["owner_approval_evidence"]
    body = [
        f"# Owner Decision Record: {record['decision_id']}",
        "",
        "## Decision",
        "",
        f"- Type: {record['decision_type']}",
        f"- Status: {record['decision_status']}",
        f"- Summary: {record['decision_summary']}",
        f"- Rationale: {record['decision_rationale'] or 'not provided'}",
        "",
        "## Scope",
        "",
    ]
    for key in ("project", "task_id", "draft_path", "orchestration_id", "iteration_id", "event_id", "external_ref"):
        if applies_to.get(key):
            body.append(f"- {key}: {applies_to[key]}")
    body.extend(
        [
            "",
            "## Approval Scope",
            "",
            f"- Operation: {approval_scope['operation']}",
            f"- Authority boundary: {approval_scope['authority_boundary']}",
            f"- Allowed next action: {approval_scope['allowed_next_action'] or 'none'}",
            f"- Expires at: {approval_scope['expires_at'] or 'not set'}",
            "",
            "## Owner Approval Evidence",
            "",
            f"- Evidence id: {evidence['evidence_id']}",
            f"- Source tag: {evidence['source_tag']}",
            f"- Client tag: {evidence['client_tag']}",
            f"- External ref: {evidence['external_ref']}",
            f"- Approval actor ref: {evidence['approval_actor_ref']}",
            f"- Approval timestamp: {evidence['approval_timestamp']}",
            f"- Approval intent: {evidence['approval_intent']}",
            f"- Evidence hash: {evidence['evidence_hash']}",
            f"- Evidence ref: {evidence['evidence_ref'] or 'not provided'}",
            "",
            "## Non-Authority Boundary",
            "",
            "This record captures a scoped Owner decision. It does not publish drafts, mutate queues, append orchestration events, launch runtimes, grant credential access, or bypass controlled execute.",
            "",
        ]
    )
    return "\n".join(body)


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    applies_to = record["applies_to"]
    approval_scope = record["approval_scope"]
    evidence = record["owner_approval_evidence"]
    return {
        "record_type": "owner_decision_record",
        "decision_id": record["decision_id"],
        "decision_type": record["decision_type"],
        "decision_status": record["decision_status"],
        "decided_at": record["decided_at"],
        "decided_by_ref": record["decided_by_ref"],
        "captured_by": record["captured_by"],
        "capture_surface": record["capture_surface"],
        "project": applies_to.get("project"),
        "task_id": applies_to.get("task_id"),
        "draft_path": applies_to.get("draft_path"),
        "orchestration_id": applies_to.get("orchestration_id"),
        "external_ref": applies_to.get("external_ref"),
        "approval_operation": approval_scope.get("operation"),
        "allowed_next_action": approval_scope.get("allowed_next_action"),
        "evidence_id": evidence.get("evidence_id"),
        "evidence_hash": evidence.get("evidence_hash"),
        "source_tag": evidence.get("source_tag"),
        "client_tag": evidence.get("client_tag"),
    }


def build_owner_decision_record(
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

    decision_id = _normalize_decision_id(payload.get("decision_id"), blocking_reasons)
    decision_type = _normalize_text(payload.get("decision_type"), field="decision_type", blocking_reasons=blocking_reasons, max_length=96)
    decision_status = _normalize_text(payload.get("decision_status"), field="decision_status", blocking_reasons=blocking_reasons, max_length=64)
    if decision_status and decision_status not in ALLOWED_DECISION_STATUSES:
        _add(blocking_reasons, "Invalid decision_status")
    capture_surface = _normalize_text(payload.get("capture_surface"), field="capture_surface", blocking_reasons=blocking_reasons, max_length=64)
    if capture_surface and capture_surface not in ALLOWED_CAPTURE_SURFACES:
        _add(blocking_reasons, "Invalid capture_surface")
    applies_to = _normalize_applies_to(payload.get("applies_to"), blocking_reasons)
    approval_scope = _normalize_approval_scope(payload.get("approval_scope"), blocking_reasons)
    evidence = _normalize_evidence(payload.get("owner_approval_evidence"), blocking_reasons)
    capability_scope = _normalize_capability_scope(payload.get("capability_scope"), project=applies_to.get("project"), blocking_reasons=blocking_reasons)

    if applies_to.get("project") and evidence.get("client_tag") and applies_to["project"] != evidence["client_tag"]:
        _add(blocking_reasons, "owner_approval_evidence.client_tag must match applies_to.project")
    if applies_to.get("external_ref") and evidence.get("external_ref") and applies_to["external_ref"] != evidence["external_ref"]:
        _add(blocking_reasons, "owner_approval_evidence.external_ref must match applies_to.external_ref")

    normalized_record = {
        "decision_id": decision_id,
        "decision_type": decision_type,
        "decision_status": decision_status,
        "decided_at": _parse_iso_datetime(payload.get("decided_at"), "decided_at", blocking_reasons),
        "decided_by_ref": _normalize_text(payload.get("decided_by_ref"), field="decided_by_ref", blocking_reasons=blocking_reasons, max_length=160),
        "captured_by": _normalize_text(payload.get("captured_by"), field="captured_by", blocking_reasons=blocking_reasons, max_length=160),
        "capture_surface": capture_surface,
        "decision_summary": _normalize_text(payload.get("decision_summary"), field="decision_summary", blocking_reasons=blocking_reasons, max_length=320),
        "decision_rationale": str(payload.get("decision_rationale") or "").strip() or None,
        "applies_to": applies_to,
        "approval_scope": approval_scope,
        "owner_approval_evidence": evidence,
        "refs": _normalize_list(payload.get("refs"), "refs", blocking_reasons),
        "capability_scope": capability_scope,
        "recorded_by": actor or str(payload.get("captured_by") or "").strip() or None,
    }

    target_path = str(OWNER_DECISION_RECORDS_DIR / f"{decision_id}.md") if decision_id else None
    target_file = repo_root / target_path if target_path else None
    if target_file is not None and target_file.exists():
        _add(blocking_reasons, f"Owner decision record already exists: {target_path}")

    rendered_markdown = render_markdown(_metadata(normalized_record), _render_body(normalized_record))
    planned_writes = []
    if target_path:
        planned_writes.append(
            {
                "path": target_path,
                "kind": "create",
                "type": "record_markdown",
                "record_type": "owner_decision_record",
            }
        )

    verdict = "BLOCK" if blocking_reasons else ("WARN" if warnings else "PASS")
    result: dict[str, Any] = {
        "action": "owner_decision_record",
        "dry_run": dry_run,
        "decision_id": decision_id or None,
        "target_path": target_path,
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "planned_writes": planned_writes,
        "would_write": verdict != "BLOCK" and bool(target_path),
        "rendered_markdown": rendered_markdown,
        "original_payload": normalized_record,
        "capability_scope": capability_scope,
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
