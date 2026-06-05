from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.controlled_execute import get_dry_run, is_expired
from tools.aipos_cli.records import check_task_record_refs, find_records_for_task
from tools.aipos_cli.task_loader import load_all_tasks, load_task_by_path

SAFETY_NOTICE = (
    "AIPOS-173 state recovery preview is read-only. It derives staleness, provenance, "
    "and contradiction markers from durable files; it does not write records, repair state, "
    "activate leases, dispatch audit, finalize, or move queue tasks."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_task(repo_root: Path, *, task_id: str | None = None, path: str | None = None) -> tuple[dict[str, Any], list[str]]:
    if bool(task_id) == bool(path):
        raise ValueError("Exactly one of task_id or path is required")
    if path:
        return load_task_by_path(path, repo_root), []

    tasks = load_all_tasks(repo_root)
    matches = [task for task in tasks if task.get("task_id") == task_id]
    if len(matches) == 1:
        return matches[0], []
    if not matches:
        raise ValueError(f"No task found for task_id: {task_id}")
    paths = ", ".join(str(task.get("path") or "") for task in matches)
    raise ValueError(f"Duplicate task_id {task_id} found in: {paths}")


def _source_ref(path: Any) -> str | None:
    value = str(path or "").strip()
    return value or None


def _path_status(repo_root: Path, ref: Any) -> dict[str, Any]:
    value = str(ref or "").strip()
    if not value:
        return {"ref": None, "exists": False, "status": "absent"}
    pure = Path(value)
    if pure.is_absolute():
        try:
            pure.relative_to(repo_root)
        except ValueError:
            return {"ref": value, "exists": False, "status": "outside_repo"}
        target = pure
    else:
        target = repo_root / pure
    return {"ref": value, "exists": target.exists(), "status": "ok" if target.exists() else "missing"}


def _list_path_status(repo_root: Path, refs: Any) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        return []
    return [_path_status(repo_root, item) for item in refs if str(item or "").strip()]


def _record_ref_status(checks: list[dict[str, Any]], reference: str) -> dict[str, Any] | None:
    for check in checks:
        if check.get("reference") == reference:
            return check
    return None


def _first_match(check: dict[str, Any] | None) -> str | None:
    if not check:
        return None
    matches = check.get("matches")
    if isinstance(matches, list) and len(matches) == 1 and isinstance(matches[0], dict):
        return _source_ref(matches[0].get("path"))
    return None


def _dry_run_staleness(dry_run_token: str | None, expected_operation: str | None) -> list[dict[str, Any]]:
    if not dry_run_token:
        return [
            {
                "verdict": "unknown",
                "severity": "info",
                "subject_type": "dry_run_token",
                "subject_ref": None,
                "reason_code": "NO_DRY_RUN_TOKEN_SUPPLIED",
                "reason": "No dry-run token was supplied for this read-only recovery preview.",
                "suggested_next_action": "Run the relevant dry-run again before any confirm operation.",
            }
        ]

    token = get_dry_run(dry_run_token)
    if token is None:
        return [
            {
                "verdict": "stale",
                "severity": "block",
                "subject_type": "dry_run_token",
                "subject_ref": dry_run_token,
                "reason_code": "PROCESS_LOCAL_TOKEN_STALE",
                "reason": "dry_run_token was not found in this process; tokens are process-local or expired.",
                "suggested_next_action": "Run a fresh dry-run, review the preview, then confirm explicitly.",
            }
        ]
    if is_expired(token):
        return [
            {
                "verdict": "stale",
                "severity": "block",
                "subject_type": "dry_run_token",
                "subject_ref": dry_run_token,
                "reason_code": "TOKEN_EXPIRED",
                "reason": "dry_run_token is known but expired.",
                "observed_at": token.created_at,
                "expires_at": token.expires_at,
                "suggested_next_action": "Run a fresh dry-run before confirm.",
            }
        ]
    if expected_operation and token.operation != expected_operation:
        return [
            {
                "verdict": "stale",
                "severity": "block",
                "subject_type": "dry_run_token",
                "subject_ref": dry_run_token,
                "reason_code": "INCOMPATIBLE_DRY_RUN",
                "reason": "dry_run_token is known but belongs to another operation surface.",
                "operation": token.operation,
                "expected_operation": expected_operation,
                "observed_at": token.created_at,
                "expires_at": token.expires_at,
                "suggested_next_action": "Confirm only with a token produced by the reviewed dry-run surface.",
            }
        ]
    return [
        {
            "verdict": "current",
            "severity": "info",
            "subject_type": "dry_run_token",
            "subject_ref": dry_run_token,
            "reason_code": "PROCESS_LOCAL_TOKEN_CURRENT",
            "reason": "dry_run_token is present in the current process and not expired.",
            "operation": token.operation,
            "observed_at": token.created_at,
            "expires_at": token.expires_at,
            "snapshot_hash": token.snapshot_hash,
        }
    ]


def _derive_verdict(blocking: list[str], needs_owner: list[str], warnings: list[str]) -> str:
    if blocking:
        return "BLOCK"
    if needs_owner:
        return "NEEDS_OWNER"
    if warnings:
        return "WARN"
    return "PASS"


def build_state_recovery_preview(
    repo_root: Path,
    *,
    task_id: str | None = None,
    path: str | None = None,
    records: dict[str, Any] | None = None,
    dry_run_token: str | None = None,
    expected_operation: str | None = None,
) -> dict[str, Any]:
    task, selection_warnings = _resolve_task(repo_root, task_id=task_id, path=path)
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    task_id_value = str(task.get("task_id") or metadata.get("task_id") or "")
    records_report = records if records is not None else None
    if records_report is None:
        from tools.aipos_cli.records import load_records

        records_report = load_records(repo_root)

    record_report = check_task_record_refs(task, records_report)
    record_checks = list(record_report.get("checks", []))
    linked_records = find_records_for_task(records_report, task_id_value) if task_id_value else {"sessions": [], "claims": []}

    warnings: list[str] = list(selection_warnings)
    blocking: list[str] = []
    needs_owner: list[str] = []
    contradictions: list[dict[str, Any]] = []
    staleness: list[dict[str, Any]] = []

    if not task.get("status_consistent"):
        message = "queue directory state and frontmatter status disagree"
        blocking.append(message)
        contradictions.append(
            {
                "reason_code": "QUEUE_STATUS_CONTRADICTION",
                "severity": "block",
                "message": message,
                "queue_state": task.get("queue_state"),
                "frontmatter_status": task.get("frontmatter_status"),
                "source_refs": [_source_ref(task.get("path"))],
            }
        )

    for check in record_checks:
        status = check.get("status")
        message = str(check.get("message") or "")
        if check.get("level") == "warn":
            warnings.append(message)
            staleness.append(
                {
                    "verdict": "unknown",
                    "severity": "warn",
                    "subject_type": f"{check.get('record_type')}_record",
                    "subject_ref": check.get("record_id"),
                    "reason_code": "RECORD_REF_MISSING",
                    "reason": message,
                    "source_refs": [_source_ref(task.get("path"))],
                    "suggested_next_action": "Treat as provenance gap; do not use as active lease or acceptance evidence.",
                }
            )
        elif check.get("level") == "needs_owner":
            needs_owner.append(message)
            contradictions.append(
                {
                    "reason_code": "RECORD_REF_CONTRADICTION",
                    "severity": "needs_owner",
                    "message": message,
                    "reference": check.get("reference"),
                    "record_id": check.get("record_id"),
                    "matches": check.get("matches", []),
                }
            )
        elif status == "ok":
            staleness.append(
                {
                    "verdict": "current",
                    "severity": "info",
                    "subject_type": f"{check.get('record_type')}_record",
                    "subject_ref": check.get("record_id"),
                    "reason_code": "RECORD_REF_PRESENT",
                    "reason": message,
                    "source_refs": [item.get("path") for item in check.get("matches", []) if isinstance(item, dict)],
                }
            )

    staleness.extend(_dry_run_staleness(dry_run_token, expected_operation))

    queue_state = str(task.get("queue_state") or "")
    lease_status = metadata.get("lease_status")
    if not lease_status and queue_state == "claimed":
        lease_status = "proposed"
    lease_path = metadata.get("lease_path")
    if not lease_path and lease_status == "proposed":
        lease_path = "claim_only"
    if lease_status == "proposed":
        staleness.append(
            {
                "verdict": "unknown",
                "severity": "warn",
                "subject_type": "lease",
                "subject_ref": metadata.get("active_session_id"),
                "reason_code": "LEASE_AUTHORITY_NOT_ACTIVE",
                "reason": "lease_status is proposed; this is not active execution authority.",
                "source_refs": [_source_ref(task.get("path"))],
                "suggested_next_action": "Use a separate Owner-gated lease writer before relying on active lease authority.",
            }
        )

    artifact_status = _list_path_status(repo_root, metadata.get("artifact_refs"))
    completion_report_status = _path_status(repo_root, metadata.get("completion_report_ref"))
    has_return_evidence = bool(metadata.get("result_summary")) or any(item.get("exists") for item in artifact_status) or bool(
        completion_report_status.get("exists")
    )
    if metadata.get("audit_readiness") == "ready":
        if metadata.get("executor_status") != "completed":
            warnings.append("audit_readiness is ready but executor_status is not completed")
            contradictions.append(
                {
                    "reason_code": "AUDIT_READINESS_WITHOUT_EXECUTOR_COMPLETION",
                    "severity": "warn",
                    "message": "audit_readiness is ready but executor_status is not completed",
                }
            )
        if not has_return_evidence:
            warnings.append("audit_readiness is ready but no return evidence refs or result_summary were found")
            staleness.append(
                {
                    "verdict": "unknown",
                    "severity": "warn",
                    "subject_type": "audit_readiness",
                    "subject_ref": task_id_value,
                    "reason_code": "AUDIT_READINESS_EVIDENCE_GAP",
                    "reason": "audit_readiness is ready but no return evidence refs or result_summary were found.",
                    "source_refs": [_source_ref(task.get("path"))],
                }
            )
        if metadata.get("dependency_audit_status") != "PASS":
            warnings.append("audit_readiness is ready but audit PASS is still pending")
            staleness.append(
                {
                    "verdict": "current",
                    "severity": "info",
                    "subject_type": "audit_pass",
                    "subject_ref": task_id_value,
                    "reason_code": "AUDIT_PASS_STILL_PENDING",
                    "reason": "executor completion and audit readiness do not imply audit PASS.",
                    "source_refs": [_source_ref(task.get("path"))],
                }
            )

    claim_check = _record_ref_status(record_checks, "claim_id")
    session_check = _record_ref_status(record_checks, "active_session_id")
    claim_status = (claim_check or {}).get("status")
    session_status = (session_check or {}).get("status")
    if contradictions:
        completeness = "contradictory"
    elif "missing" in {claim_status, session_status}:
        completeness = "partial"
    elif metadata.get("audit_readiness") == "ready" and not has_return_evidence:
        completeness = "missing"
    elif metadata.get("claim_id") or metadata.get("active_session_id") or metadata.get("executor_status"):
        completeness = "complete"
    else:
        completeness = "missing"

    source_refs = [item for item in [task.get("path"), _first_match(claim_check), _first_match(session_check)] if item]
    if completion_report_status.get("exists"):
        source_refs.append(str(completion_report_status.get("ref")))
    source_refs.extend(str(item.get("ref")) for item in artifact_status if item.get("exists"))

    provenance_chain = {
        "task": {
            "task_id": task_id_value or None,
            "task_path": task.get("path"),
            "queue_state": task.get("queue_state"),
            "frontmatter_status": task.get("frontmatter_status"),
        },
        "claim": {
            "claim_id": metadata.get("claim_id"),
            "claimed_by": metadata.get("claimed_by"),
            "claimed_agent_instance": metadata.get("claimed_agent_instance") or metadata.get("agent_instance"),
            "claimed_at": metadata.get("claimed_at"),
            "owner_policy_ref": metadata.get("owner_policy_ref"),
            "claim_record_ref": _first_match(claim_check),
            "record_status": claim_status,
        },
        "session": {
            "active_session_id": metadata.get("active_session_id"),
            "session_record_ref": _first_match(session_check),
            "lease_status": lease_status,
            "lease_path": lease_path,
            "record_status": session_status,
        },
        "return": {
            "return_event_ref": metadata.get("return_event_ref"),
            "executor_completed_by": metadata.get("executor_completed_by"),
            "executor_completed_at": metadata.get("executor_completed_at"),
            "executor_status": metadata.get("executor_status"),
            "audit_readiness": metadata.get("audit_readiness"),
            "artifact_refs": metadata.get("artifact_refs") if isinstance(metadata.get("artifact_refs"), list) else [],
            "artifact_ref_status": artifact_status,
            "completion_report_ref": metadata.get("completion_report_ref"),
            "completion_report_status": completion_report_status,
            "return_owner_policy_ref": metadata.get("return_owner_policy_ref"),
            "result_summary_present": bool(metadata.get("result_summary")),
        },
        "audit": {
            "related_audit_task_ref": metadata.get("related_audit_task_ref"),
            "related_audit_verdict_ref": metadata.get("related_audit_verdict_ref"),
            "dependency_audit_status": metadata.get("dependency_audit_status"),
        },
        "finalize": {
            "finalize_ref": metadata.get("finalize_ref"),
            "finalize_status": metadata.get("finalize_status"),
        },
    }

    verdict = _derive_verdict(blocking, needs_owner, warnings)
    return {
        "action": "state_recovery_preview",
        "protocol_ref": "AIPOS-172 State Staleness and Provenance Protocol",
        "verdict": verdict,
        "task_id": task_id_value or None,
        "task_path": task.get("path"),
        "queue_state": task.get("queue_state"),
        "frontmatter_status": task.get("frontmatter_status"),
        "status_consistent": task.get("status_consistent"),
        "claimed_by": metadata.get("claimed_by"),
        "canonical_agent_instance": metadata.get("claimed_agent_instance") or metadata.get("agent_instance"),
        "claim_id": metadata.get("claim_id"),
        "active_session_id": metadata.get("active_session_id"),
        "lease_status": lease_status,
        "lease_path": lease_path,
        "executor_status": metadata.get("executor_status"),
        "audit_readiness": metadata.get("audit_readiness"),
        "dependency_audit_status": metadata.get("dependency_audit_status"),
        "provenance_chain": provenance_chain,
        "provenance_completeness": completeness,
        "record_ref_checks": record_checks,
        "linked_records": linked_records,
        "staleness": staleness,
        "contradictions": contradictions,
        "warnings": warnings,
        "blocking_reasons": blocking,
        "needs_owner_reasons": needs_owner,
        "source_refs": source_refs,
        "recommended_next_action": _recommended_action(verdict, completeness, metadata),
        "derived_at": _utc_now(),
        "writes_enabled": False,
        "execute_allowed": False,
        "safety_notice": SAFETY_NOTICE,
    }


def _recommended_action(verdict: str, completeness: str, metadata: dict[str, Any]) -> str:
    if verdict == "BLOCK":
        return "Resolve contradictory durable state before any mutation or recovery action."
    if verdict == "NEEDS_OWNER":
        return "Route conflicting provenance to Owner review before continuing."
    if metadata.get("audit_readiness") == "ready" and metadata.get("dependency_audit_status") != "PASS":
        return "Create or run the separately gated independent audit path; do not finalize from audit readiness alone."
    if completeness in {"partial", "missing"}:
        return "Treat missing provenance as a gap; use separate Owner-gated records or recovery writers if durable repair is needed."
    return "State is recoverable from durable files for read-only inspection."
