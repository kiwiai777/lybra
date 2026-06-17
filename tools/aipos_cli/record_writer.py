from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.records import expected_claim_log_path, expected_return_record_path, expected_session_record_path

RECORDS_ROOT = Path("5_tasks/records")
CLAIMS_ROOT = RECORDS_ROOT / "claims"
SESSIONS_ROOT = RECORDS_ROOT / "sessions"
RETURNS_ROOT = RECORDS_ROOT / "returns"
AUDIT_DISPATCHES_ROOT = RECORDS_ROOT / "audit_dispatches"
AUDIT_VERDICTS_ROOT = RECORDS_ROOT / "audit_verdicts"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


def actor_slug(actor: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", actor.lower()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if not value:
        raise ValueError(f"Actor cannot be converted to a safe slug: {actor}")
    return value


def validate_safe_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError(f"Unsafe task_id for records path: {task_id}")
    if task_id in {".", ".."} or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError(f"Unsafe task_id for records path: {task_id}")


def build_runtime_id(prefix: str, task_id: str, timestamp: str, actor: str) -> str:
    validate_safe_task_id(task_id)
    return f"{prefix}_{task_id}_{timestamp.replace('-', '').replace(':', '').replace('T', '_').replace('Z', '')}_{actor_slug(actor)}"


def _resolved_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def ensure_safe_record_path(repo_root: Path, path: Path, record_type: str, task_id: str) -> Path:
    validate_safe_task_id(task_id)
    if record_type == "claim_log":
        root = (repo_root / CLAIMS_ROOT / task_id).resolve()
    elif record_type == "session_record":
        root = (repo_root / SESSIONS_ROOT / task_id).resolve()
    elif record_type == "return_record":
        root = (repo_root / RETURNS_ROOT / task_id).resolve()
    elif record_type == "audit_dispatch_record":
        root = (repo_root / AUDIT_DISPATCHES_ROOT / task_id).resolve()
    elif record_type == "audit_verdict_record":
        root = (repo_root / AUDIT_VERDICTS_ROOT / task_id).resolve()
    else:
        raise ValueError(f"Unsupported record_type: {record_type}")
    resolved = path.resolve()
    if not _resolved_within(root, resolved):
        raise ValueError(f"Record path resolves outside allowed records root: {path}")
    if resolved.suffix.lower() != ".md":
        raise ValueError(f"Record path is not a markdown file: {path}")
    return resolved


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


def render_markdown(metadata: dict[str, Any], body: str, order: list[str] | None = None) -> str:
    ordered_keys = [key for key in (order or []) if key in metadata]
    ordered_keys.extend(sorted(key for key in metadata if key not in ordered_keys))
    lines = ["---"]
    for key in ordered_keys:
        value = _normalize_value(metadata[key])
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"- {_yaml_scalar(item)}")
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.extend(["---", body.rstrip(), ""])
    return "\n".join(lines)


CLAIM_FRONTMATTER_ORDER = [
    "record_type",
    "claim_id",
    "task_id",
    "task_path",
    "actor",
    "claim_action",
    "created_at",
    "from_state",
    "to_state",
    "session_id",
]

SESSION_FRONTMATTER_ORDER = [
    "record_type",
    "session_id",
    "task_id",
    "task_path",
    "actor",
    "created_at",
    "updated_at",
    "status",
    "claim_id",
    "current_state",
    "event_count",
]

MCP_CLAIM_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "claim_id",
    "task_id",
    "task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "claimed_at",
    "from_state",
    "to_state",
    "claim_policy",
    "claim_match_basis",
    "claim_requirements_hash",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "confirmer_role",
    "confirmer_token_ref",
    "confirmer_token_fingerprint",
    "gate_signature",
    "authority_seal",
    "signature_key_ref",
    "signed_payload_hash",
    "signed_at",
    "session_id",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_SESSION_FRONTMATTER_ORDER = [
    "record_type",
    "session_id",
    "task_id",
    "task_path",
    "surface",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "claim_id",
    "created_at",
    "updated_at",
    "session_status",
    "current_state",
    "lease_status",
    "lease_path",
    "active_lease_written",
    "event_count",
]

MCP_RETURN_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "return_id",
    "task_id",
    "task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "claim_id",
    "session_id",
    "returned_at",
    "executor_status",
    "audit_readiness",
    "dependency_executor_status",
    "dependency_audit_readiness",
    "dependency_audit_status",
    "result_summary_present",
    "artifact_refs",
    "completion_report_ref",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "confirmer_role",
    "confirmer_token_ref",
    "confirmer_token_fingerprint",
    "gate_signature",
    "authority_seal",
    "signature_key_ref",
    "signed_payload_hash",
    "signed_at",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_AUDIT_DISPATCH_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "dispatch_id",
    "reviewed_task_id",
    "reviewed_task_path",
    "reviewed_return_record_ref",
    "reviewed_executor_instance",
    "reviewed_executor_claim_id",
    "reviewed_executor_session_id",
    "audit_task_id",
    "audit_task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "dispatched_at",
    "independence_distinct_instance",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "dependency_executor_status",
    "dependency_audit_readiness",
    "dependency_audit_status",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_AUDIT_VERDICT_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "verdict_id",
    "verdict",
    "reviewed_task_id",
    "reviewed_task_path",
    "reviewed_return_record_ref",
    "audit_dispatch_record_ref",
    "audit_task_id",
    "audit_task_path",
    "audit_claim_id",
    "audit_session_id",
    "reviewed_executor_instance",
    "auditor_instance",
    "independence_distinct_instance",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "verdict_at",
    "findings_summary_present",
    "evidence_refs",
    "recommended_next_action",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "dependency_audit_status_after",
    "finalize_performed",
    "accepted_work_unblocked",
    "lease_status",
    "lease_path",
    "active_lease_written",
]


def build_claim_log_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    claim_id: str,
    session_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": "claim_log",
        "claim_id": claim_id,
        "task_id": task_id,
        "task_path": task_path,
        "actor": actor,
        "claim_action": "claimed",
        "created_at": created_at,
        "from_state": "pending",
        "to_state": "claimed",
        "session_id": session_id,
    }
    body = "\n".join(
        [
            f"# Claim Log: {claim_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` claimed by `{actor}`.",
            "",
            "## Safety",
            "",
            "This claim log was created by AIPOS queue mutation with records enabled.",
            "",
        ]
    )
    return render_markdown(metadata, body, CLAIM_FRONTMATTER_ORDER)


def build_session_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    session_id: str,
    claim_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": "session_record",
        "session_id": session_id,
        "task_id": task_id,
        "task_path": task_path,
        "actor": actor,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "active",
        "claim_id": claim_id,
        "current_state": "claimed",
        "event_count": 1,
    }
    body = "\n".join(
        [
            f"# Session Record: {session_id}",
            "",
            "## Events",
            "",
            f"- {created_at} claimed by {actor}",
            "",
        ]
    )
    return render_markdown(metadata, body, SESSION_FRONTMATTER_ORDER)


def _confirmer_fields(confirmer: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-197 confirmer attribution + AIPOS-193 §9 signature-ready placeholders.

    Records WHO confirmed (role + non-secret token fingerprint) so L3 can tell an
    Owner-role confirmation from an agent self-confirmation. Never stores a raw token.
    """
    c = confirmer or {}
    return {
        "confirmer_role": str(c.get("confirmer_role") or ""),
        "confirmer_token_ref": str(c.get("confirmer_token_ref") or ""),
        "confirmer_token_fingerprint": str(c.get("confirmer_token_fingerprint") or ""),
        "gate_signature": "",
        "authority_seal": "",
        "signature_key_ref": "",
        "signed_payload_hash": "",
        "signed_at": "",
    }


def build_mcp_claim_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    claim_id: str,
    session_id: str,
    claimed_at: str,
    claim_policy: str | None = None,
    claim_match_basis: str | None = None,
    claim_requirements_hash: str | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> str:
    metadata = {
        "record_type": "claim_record",
        "event_type": "mcp_queue_claim",
        "claim_id": claim_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_claim",
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "claimed_at": claimed_at,
        "from_state": "pending",
        "to_state": "claimed",
        "claim_policy": claim_policy or "",
        "claim_match_basis": claim_match_basis or "",
        "claim_requirements_hash": claim_requirements_hash or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        **_confirmer_fields(confirmer),
        "session_id": session_id,
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    body = "\n".join(
        [
            f"# MCP Claim Record: {claim_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` was claimed by `{canonical_agent_instance}` through the Supervised MCP claim surface.",
            f"- Owner policy: `{owner_policy_ref}`.",
            "",
            "## Boundary",
            "",
            "This record is provenance evidence only. It does not activate a lease, launch work, dispatch audit, record audit PASS, finalize, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_CLAIM_FRONTMATTER_ORDER)


def build_mcp_claim_session_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    session_id: str,
    claim_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": "session_record",
        "session_id": session_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "claim_id": claim_id,
        "created_at": created_at,
        "updated_at": created_at,
        "session_status": "claimed",
        "current_state": "claimed",
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
        "event_count": 1,
    }
    body = "\n".join(
        [
            f"# MCP Session Record: {session_id}",
            "",
            "## Events",
            "",
            f"- {created_at} mcp_queue_claim by {canonical_agent_instance}; claim_id={claim_id}; owner_policy_ref={owner_policy_ref}; lease_status=proposed.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def build_mcp_return_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    return_id: str,
    claim_id: str,
    session_id: str,
    returned_at: str,
    result_summary: str | None,
    artifact_refs: list[str],
    completion_report_ref: str | None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> str:
    metadata = {
        "record_type": "return_record",
        "event_type": "mcp_queue_return",
        "return_id": return_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_return",
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "claim_id": claim_id,
        "session_id": session_id,
        "returned_at": returned_at,
        "executor_status": "completed",
        "audit_readiness": "ready",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "result_summary_present": bool(result_summary),
        "artifact_refs": artifact_refs,
        "completion_report_ref": completion_report_ref or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        **_confirmer_fields(confirmer),
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    body = "\n".join(
        [
            f"# MCP Return Record: {return_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` was returned by `{canonical_agent_instance}` through the Supervised MCP return surface.",
            f"- Owner policy: `{owner_policy_ref}`.",
            f"- Result summary: {result_summary or 'not provided'}",
            "",
            "## Boundary",
            "",
            "This record marks executor completion plus audit readiness only. It does not dispatch audit, record audit PASS, finalize, activate a lease, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_RETURN_FRONTMATTER_ORDER)


def build_mcp_audit_dispatch_record_markdown(
    *,
    dispatch_id: str,
    reviewed_task_id: str,
    reviewed_task_path: str,
    reviewed_return_record_ref: str,
    reviewed_executor_instance: str,
    reviewed_executor_claim_id: str,
    reviewed_executor_session_id: str,
    audit_task_id: str,
    audit_task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    dispatched_at: str,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
) -> str:
    metadata = {
        "record_type": "audit_dispatch_record",
        "event_type": "mcp_audit_dispatch",
        "dispatch_id": dispatch_id,
        "reviewed_task_id": reviewed_task_id,
        "reviewed_task_path": reviewed_task_path,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_executor_claim_id": reviewed_executor_claim_id,
        "reviewed_executor_session_id": reviewed_executor_session_id,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "surface": "mcp",
        "operation": "audit_dispatch",
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "dispatched_at": dispatched_at,
        "independence_distinct_instance": True,
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    body = "\n".join(
        [
            f"# MCP Audit Dispatch Record: {dispatch_id}",
            "",
            "## Summary",
            "",
            f"- Task `{reviewed_task_id}` was dispatched for independent audit as `{audit_task_id}`.",
            f"- Reviewed executor instance: `{reviewed_executor_instance}`.",
            f"- Owner policy: `{owner_policy_ref}`.",
            "",
            "## Boundary",
            "",
            "This record creates audit-dispatch provenance only. It does not claim the audit task, launch an auditor, record a verdict, finalize, activate a lease, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_AUDIT_DISPATCH_FRONTMATTER_ORDER)


def build_mcp_audit_verdict_record_markdown(
    *,
    verdict_id: str,
    verdict: str,
    reviewed_task_id: str,
    reviewed_task_path: str,
    reviewed_return_record_ref: str,
    audit_dispatch_record_ref: str,
    audit_task_id: str,
    audit_task_path: str,
    audit_claim_id: str,
    audit_session_id: str,
    reviewed_executor_instance: str,
    auditor_instance: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    verdict_at: str,
    findings_summary: str | None,
    evidence_refs: list[str],
    recommended_next_action: str | None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
) -> str:
    metadata = {
        "record_type": "audit_verdict_record",
        "event_type": "mcp_audit_verdict",
        "verdict_id": verdict_id,
        "verdict": verdict,
        "reviewed_task_id": reviewed_task_id,
        "reviewed_task_path": reviewed_task_path,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "audit_dispatch_record_ref": audit_dispatch_record_ref,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "audit_claim_id": audit_claim_id,
        "audit_session_id": audit_session_id,
        "reviewed_executor_instance": reviewed_executor_instance,
        "auditor_instance": auditor_instance,
        "independence_distinct_instance": auditor_instance != reviewed_executor_instance,
        "surface": "mcp",
        "operation": "audit_verdict",
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "verdict_at": verdict_at,
        "findings_summary_present": bool(findings_summary),
        "evidence_refs": evidence_refs,
        "recommended_next_action": recommended_next_action or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        "dependency_audit_status_after": "PASS" if verdict == "PASS" else verdict,
        "finalize_performed": False,
        "accepted_work_unblocked": False,
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    body = "\n".join(
        [
            f"# MCP Audit Verdict Record: {verdict_id}",
            "",
            "## Summary",
            "",
            f"- Audit task `{audit_task_id}` returned verdict `{verdict}` for `{reviewed_task_id}`.",
            f"- Auditor instance: `{auditor_instance}`.",
            f"- Reviewed executor instance: `{reviewed_executor_instance}`.",
            f"- Findings summary: {findings_summary or 'not provided'}",
            "",
            "## Boundary",
            "",
            "This record is independent audit evidence. PASS may satisfy audit_pass only. It does not finalize, activate a lease, or unblock accepted-work dependencies.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_AUDIT_VERDICT_FRONTMATTER_ORDER)


def load_session_record(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body, warnings = parse_markdown_frontmatter(text)
    return _normalize_value(metadata), body, warnings


def update_session_record_markdown(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    timestamp: str,
    status: str,
    current_state: str,
    event_line: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["status"] = status
    metadata["current_state"] = current_state
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("record_type", "session_record")
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join([body, "", f"- {event_line}", ""])
    return render_markdown(metadata, body, SESSION_FRONTMATTER_ORDER)


def append_mcp_return_session_event(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    timestamp: str,
    return_id: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["session_status"] = "returned"
    metadata["current_state"] = "claimed"
    metadata.setdefault("lease_status", "proposed")
    metadata.setdefault("lease_path", "claim_only")
    metadata.setdefault("active_lease_written", False)
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("canonical_agent_instance", canonical_agent_instance)
    metadata.setdefault("owner_policy_ref", owner_policy_ref)
    metadata.setdefault("record_type", "session_record")
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join(
        [
            body,
            "",
            f"- {timestamp} mcp_queue_return by {canonical_agent_instance}; return_id={return_id}; owner_policy_ref={owner_policy_ref}; audit_readiness=ready.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def append_mcp_audit_verdict_session_event(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    timestamp: str,
    verdict_id: str,
    verdict: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["session_status"] = "audit_verdict"
    metadata["current_state"] = "claimed"
    metadata.setdefault("lease_status", "proposed")
    metadata.setdefault("lease_path", "claim_only")
    metadata.setdefault("active_lease_written", False)
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("canonical_agent_instance", canonical_agent_instance)
    metadata.setdefault("owner_policy_ref", owner_policy_ref)
    metadata.setdefault("record_type", "session_record")
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join(
        [
            body,
            "",
            f"- {timestamp} mcp_audit_verdict by {canonical_agent_instance}; verdict_id={verdict_id}; verdict={verdict}; owner_policy_ref={owner_policy_ref}.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def claim_record_paths(repo_root: Path, task_id: str, claim_id: str, session_id: str) -> tuple[Path, Path]:
    claim_path = ensure_safe_record_path(repo_root, expected_claim_log_path(repo_root, task_id, claim_id), "claim_log", task_id)
    session_path = ensure_safe_record_path(repo_root, expected_session_record_path(repo_root, task_id, session_id), "session_record", task_id)
    return claim_path, session_path


def session_record_path(repo_root: Path, task_id: str, session_id: str) -> Path:
    return ensure_safe_record_path(repo_root, expected_session_record_path(repo_root, task_id, session_id), "session_record", task_id)


def return_record_path(repo_root: Path, task_id: str, return_id: str) -> Path:
    return ensure_safe_record_path(repo_root, expected_return_record_path(repo_root, task_id, return_id), "return_record", task_id)


def audit_dispatch_record_path(repo_root: Path, task_id: str, dispatch_id: str) -> Path:
    path = repo_root / AUDIT_DISPATCHES_ROOT / task_id / f"{dispatch_id}.md"
    return ensure_safe_record_path(repo_root, path, "audit_dispatch_record", task_id)


def audit_verdict_record_path(repo_root: Path, task_id: str, verdict_id: str) -> Path:
    path = repo_root / AUDIT_VERDICTS_ROOT / task_id / f"{verdict_id}.md"
    return ensure_safe_record_path(repo_root, path, "audit_verdict_record", task_id)
