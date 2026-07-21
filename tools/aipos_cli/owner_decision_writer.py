from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.autonomy_policy import (
    POLICIES_DIR,
    POLICY_ID_PATTERN,
    build_autonomy_policy_markdown,
)
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


def _normalize_autonomy_policy(
    value: Any, *, decision_id: str, blocking_reasons: list[str]
) -> dict[str, Any] | None:
    """AIPOS-250: OPTIONAL — when an owner decision GRANTS a PreAuthorized autonomy envelope,
    the payload carries an autonomy_policy block. Validating and materializing the policy
    artifact HERE means the envelope only lands when the owner_decision_record itself lands
    (owner_confirm-gated on confirm) — 'pre-authorization = one Owner hand-confirm', red line 1.
    Absent this block, owner_decision behaviour is 100% unchanged."""
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        _add(blocking_reasons, "autonomy_policy must be a mapping")
        return None

    policy_id = str(value.get("policy_id") or "").strip()
    if not policy_id:
        _add(blocking_reasons, "Missing required field: autonomy_policy.policy_id")
    elif not POLICY_ID_PATTERN.fullmatch(policy_id):
        _add(blocking_reasons, "Invalid autonomy_policy.policy_id format")

    agent_or_role = str(value.get("agent_or_role") or "").strip()
    if not agent_or_role:
        _add(blocking_reasons, "Missing required field: autonomy_policy.agent_or_role")

    active_from = _parse_iso_datetime(value.get("active_from"), "autonomy_policy.active_from", blocking_reasons)
    expires_at = _parse_iso_datetime(value.get("expires_at"), "autonomy_policy.expires_at", blocking_reasons)
    if active_from and expires_at:
        af = datetime.fromisoformat(active_from.replace("Z", "+00:00"))
        ex = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if ex <= af:
            _add(blocking_reasons, "autonomy_policy.expires_at must be after active_from")

    max_tasks_raw = value.get("max_tasks")
    try:
        max_tasks = int(max_tasks_raw)
    except (TypeError, ValueError):
        max_tasks = 0
        _add(blocking_reasons, "autonomy_policy.max_tasks must be an integer")
    if max_tasks_raw is not None and max_tasks <= 0:
        _add(blocking_reasons, "autonomy_policy.max_tasks must be a positive count bound")

    selector = value.get("task_selector")
    if not isinstance(selector, dict):
        selector = {}
        _add(blocking_reasons, "autonomy_policy.task_selector must be a mapping")
    sel_mode = str(selector.get("task_mode") or "").strip()
    sel_project = str(selector.get("project") or "").strip()
    sel_ids_raw = selector.get("task_ids")
    sel_ids = [str(item).strip() for item in sel_ids_raw if str(item).strip()] if isinstance(sel_ids_raw, list) else []
    if sel_ids_raw not in (None, []) and not isinstance(sel_ids_raw, list):
        _add(blocking_reasons, "autonomy_policy.task_selector.task_ids must be a list of strings")
    if not (sel_mode or sel_project or sel_ids):
        _add(blocking_reasons, "autonomy_policy.task_selector must set at least one of task_mode/project/task_ids (no wildcard envelope)")

    return {
        "policy_id": policy_id,
        "agent_or_role": agent_or_role,
        "active_from": active_from,
        "expires_at": expires_at,
        "max_tasks": max_tasks,
        "task_selector_task_mode": sel_mode,
        "task_selector_project": sel_project,
        "task_selector_task_ids": sel_ids,
        "owner_approval_ref": decision_id,
    }


def _synthesize_policy_grant_record(
    payload: dict[str, Any],
    autonomy_policy: dict[str, Any] | None,
    decision_id: str,
    actor: str | None,
) -> dict[str, Any]:
    """Build a complete, HONEST owner_decision_record for the envelope-arming path WITHOUT demanding
    the heavy AIPOS-110 out-of-band evidence. All values derive deterministically from the policy +
    a few payload scalars (so the dry-run↔confirm re-run yields an identical snapshot; NO now())."""
    ap = autonomy_policy or {}
    policy_id = str(ap.get("policy_id") or "").strip()
    agent_or_role = str(ap.get("agent_or_role") or "").strip()
    active_from = str(ap.get("active_from") or "").strip()   # already ISO-normalized by _normalize_autonomy_policy
    expires_at = str(ap.get("expires_at") or "").strip()
    project = str(ap.get("task_selector_project") or "").strip() or str(payload.get("project") or "").strip() or None
    decided_by = str(payload.get("decided_by_ref") or actor or "owner").strip() or "owner"
    captured_by = str(payload.get("captured_by") or decided_by).strip() or "owner.console"
    summary = str(
        payload.get("decision_summary")
        or f"Arm PreAuthorized autonomy envelope {policy_id} covering {agent_or_role}."
    ).strip()
    external_ref = f"autonomy_policy:{policy_id}"
    policy_ref_path = f"5_tasks/policies/{policy_id}.md"
    # TRUTHFUL in-band evidence: the approval IS the live harness owner_confirm at confirm time —
    # not an out-of-band artifact. No fabricated evidence_hash; capture_method names the real gate.
    evidence = {
        "evidence_id": f"inband-owner-confirm:{policy_id}",
        "source_tag": "owner_console",
        "client_tag": project,
        "external_ref": external_ref,
        "approval_actor_ref": decided_by,
        "approval_timestamp": active_from,
        "approval_intent": "arm_autonomy_envelope",
        "evidence_hash": "",  # no out-of-band artifact to hash — approval is in-band (harness confirm)
        "evidence_ref": "harness:owner_confirm",
        "captured_by": captured_by,
        "capture_method": "harness_owner_confirm",
        "redaction_status": "not_applicable",
        "refs": [policy_ref_path],
    }
    applies_to = {
        "project": project,
        "task_id": None,
        "draft_path": None,
        "orchestration_id": None,
        "iteration_id": None,
        "event_id": None,
        "external_ref": external_ref,
    }
    approval_scope = {
        "operation": "owner_decision_record",
        "authority_boundary": f"arm PreAuthorized autonomy envelope {policy_id}",
        "allowed_next_action": "preauthorized_claim_autorelease",
        "expires_at": expires_at or None,
    }
    return {
        "decision_id": decision_id,
        "decision_type": str(payload.get("decision_type") or "grant_autonomy_policy").strip() or "grant_autonomy_policy",
        "decision_status": str(payload.get("decision_status") or "approved").strip() or "approved",
        "decided_at": active_from,
        "decided_by_ref": decided_by,
        "captured_by": captured_by,
        "capture_surface": str(payload.get("capture_surface") or "mcp").strip() or "mcp",
        "decision_summary": summary,
        "decision_rationale": str(payload.get("decision_rationale") or "").strip() or None,
        "applies_to": applies_to,
        "approval_scope": approval_scope,
        "owner_approval_evidence": evidence,
        "refs": [policy_ref_path],
        "capability_scope": {
            "token_ref": "owner_console",
            "operations": ["owner_decision_record"],
            "projects": [project] if project else [],
            "expires_at": expires_at,
            "evidence_ref": None,
        },
        "recorded_by": actor or captured_by or None,
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

    decision_id = _normalize_decision_id(payload.get("decision_id"), blocking_reasons)
    autonomy_policy = _normalize_autonomy_policy(
        payload.get("autonomy_policy"), decision_id=decision_id, blocking_reasons=blocking_reasons
    )
    is_policy_grant = payload.get("autonomy_policy") not in (None, "")

    if is_policy_grant:
        # AIPOS-250 design decision (relax): arming a PreAuthorized envelope's approval is IN-BAND —
        # the live harness owner_confirm press at confirm time IS the approval. The heavy AIPOS-110
        # out-of-band owner_approval_evidence is redundant here (and demanding it would invite the
        # advisor to FABRICATE evidence for an approval that is happening live). So this path does
        # NOT require the advisor to supply owner_approval_evidence / applies_to / approval_scope /
        # capability_scope: the record is synthesized from the policy + a TRUTHFUL in-band evidence
        # marker (capture_method=harness_owner_confirm). Advisor supplies only decision_id + the
        # autonomy_policy block. The non-grant owner_decision path is unchanged (full AIPOS-110 schema).
        normalized_record = _synthesize_policy_grant_record(payload, autonomy_policy, decision_id, actor)
    else:
        for field in REQUIRED_PAYLOAD_FIELDS:
            if _is_missing(payload.get(field)):
                _add(blocking_reasons, f"Missing required field: {field}")

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
    # AIPOS-250: round-trip the RAW autonomy_policy block through original_payload so the confirm
    # re-run (which rebuilds from original_payload) re-materializes the policy artifact identically.
    if autonomy_policy is not None and payload.get("autonomy_policy") is not None:
        normalized_record["autonomy_policy"] = payload.get("autonomy_policy")

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

    # AIPOS-250: when this owner decision GRANTS a PreAuthorized envelope, plan (and on confirm,
    # write) the policy artifact alongside the decision record. status=active + approved_by_owner
    # is stamped by the gate only through this owner_confirm-gated path.
    policy_path = None
    policy_file = None
    policy_markdown = ""
    if autonomy_policy is not None and autonomy_policy.get("policy_id"):
        policy_path = str(POLICIES_DIR / f"{autonomy_policy['policy_id']}.md")
        policy_file = repo_root / policy_path
        if policy_file.exists():
            _add(blocking_reasons, f"Autonomy policy already exists: {policy_path}")
        policy_markdown = build_autonomy_policy_markdown(
            policy_id=autonomy_policy["policy_id"],
            agent_or_role=autonomy_policy["agent_or_role"],
            active_from=autonomy_policy["active_from"],
            expires_at=autonomy_policy["expires_at"],
            max_tasks=autonomy_policy["max_tasks"],
            owner_approval_ref=autonomy_policy["owner_approval_ref"],
            task_selector_task_mode=autonomy_policy["task_selector_task_mode"],
            task_selector_project=autonomy_policy["task_selector_project"],
            task_selector_task_ids=autonomy_policy["task_selector_task_ids"],
        )
        planned_writes.append(
            {
                "path": policy_path,
                "kind": "create",
                "type": "record_markdown",
                "record_type": "owner_autonomy_policy",
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
        # read from the record (defined in BOTH branches) — the grant path never binds a local
        # capability_scope of its own (AIPOS-250 relax: it is synthesized inside the record).
        "capability_scope": normalized_record.get("capability_scope"),
        # AIPOS-250: surfaced so the confirm handler can require owner_confirm for a policy grant
        # (mirrors claim/return/publish confirm) — a policy grant is a consequential truth mutation.
        "autonomy_policy_grant": bool(autonomy_policy is not None and autonomy_policy.get("policy_id")),
        "autonomy_policy_id": autonomy_policy.get("policy_id") if autonomy_policy else None,
        "autonomy_policy_path": policy_path,
    }

    if dry_run:
        return result

    if verdict == "BLOCK" or target_file is None:
        result["wrote"] = False
        return result

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(rendered_markdown, encoding="utf-8")
    if policy_file is not None and policy_markdown:
        policy_file.parent.mkdir(parents=True, exist_ok=True)
        policy_file.write_text(policy_markdown, encoding="utf-8")
    result["wrote"] = True
    return result
