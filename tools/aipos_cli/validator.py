from __future__ import annotations

from collections import Counter
import re
from typing import Any

from tools.aipos_cli.agent_profiles import (
    actor_match_details,
    actor_matches_task,
    actor_matches_task_actor,
    availability_warning_for_actor,
)
from tools.aipos_cli.records import check_task_record_refs, find_records_for_task
from tools.aipos_cli.task_complexity import validate_task_complexity

VERDICT_PRIORITY = {
    "PASS": 0,
    "WARN": 1,
    "NEEDS_OWNER": 2,
    "BLOCK": 3,
}

REQUIRED_FIELDS = [
    "task_id",
    "title",
    "assigned_to",
    "context_bundle",
    "task_mode",
    "priority",
    "status",
    "created_by",
    "needs_owner",
    "output_target",
    "artifact_policy",
]

_EXTERNAL_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _is_missing(value: Any) -> bool:
    return value in (None, "")


def _add(reason_list: list[str], message: str) -> None:
    if message not in reason_list:
        reason_list.append(message)


def _valid_runtime_id(value: Any, prefix: str, task_id: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if not value.startswith(f"{prefix}_"):
        return False
    task_text = str(task_id or "")
    return f"{prefix}_{task_text}_" in value


def _validate_external_intake_metadata(metadata: dict[str, Any], warnings: list[str]) -> None:
    for field in ("source_tag", "client_tag"):
        value = metadata.get(field)
        if _is_missing(value):
            continue
        if not isinstance(value, str) or not _EXTERNAL_TAG_PATTERN.fullmatch(value):
            _add(warnings, f"Invalid {field} format")

    external_ref = metadata.get("external_ref")
    if _is_missing(external_ref):
        return
    if not isinstance(external_ref, str):
        _add(warnings, "Invalid external_ref format")
        return
    if len(external_ref) > 256 or any(ord(char) < 32 for char in external_ref):
        _add(warnings, "Invalid external_ref format")


def _derive_verdict(
    blocking_reasons: list[str],
    warnings: list[str],
    needs_owner_reasons: list[str],
) -> str:
    if blocking_reasons:
        return "BLOCK"
    if needs_owner_reasons:
        return "NEEDS_OWNER"
    if warnings:
        return "WARN"
    return "PASS"


def _normalize_record_ref_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "field": check.get("reference"),
        "record_type": check.get("record_type"),
        "record_id": check.get("record_id"),
        "status": check.get("status"),
        "severity": check.get("level"),
        "message": check.get("message"),
        "matches": check.get("matches", []),
    }


def _task_records_summary(task: dict[str, Any]) -> dict[str, Any]:
    sessions = list(task.get("record_links", {}).get("sessions", []))
    claims = list(task.get("record_links", {}).get("claims", []))
    checks = list(task.get("record_ref_checks", []))
    return {
        "session_records": len(sessions),
        "claim_logs": len(claims),
        "has_record_issues": any(check.get("status") in {"missing", "conflict"} for check in checks),
    }


def build_records_summary(records: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    duplicate_session_ids = sum(
        1 for items in records.get("session_index", {}).values() if len(items) > 1
    )
    duplicate_claim_ids = sum(1 for items in records.get("claim_index", {}).values() if len(items) > 1)
    task_ids_with_records = set(records.get("task_sessions", {}).keys()) | set(records.get("task_claims", {}).keys())
    task_ids_with_record_issues = {
        str(task.get("task_id"))
        for task in tasks
        if task.get("task_id") and task.get("records", {}).get("has_record_issues")
    }
    task_ids_with_record_issues.update(
        str(record.get("task_id"))
        for record in [*records.get("sessions", []), *records.get("claims", [])]
        if record.get("task_id")
        and (record.get("parse_errors") or record.get("warnings"))
    )
    return {
        "sessions_total": len(records.get("sessions", [])),
        "claims_total": len(records.get("claims", [])),
        "parse_errors_total": len(records.get("parse_errors", [])),
        "warnings_total": len(records.get("warnings", [])),
        "tasks_with_records": len(task_ids_with_records),
        "tasks_with_record_issues": len(task_ids_with_record_issues),
        "records_root_exists": bool(records.get("records_root_exists")),
        "session_task_count": len(records.get("task_sessions", {})),
        "claim_task_count": len(records.get("task_claims", {})),
        "duplicate_session_ids": duplicate_session_ids,
        "duplicate_claim_ids": duplicate_claim_ids,
        "task_id_mismatch_count": sum(
            1 for warning in records.get("warnings", []) if "task_id mismatch" in str(warning)
        ),
    }


def build_records_diagnostics(records: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []

    for task in tasks:
        for check in task.get("record_ref_checks", []):
            if check.get("status") not in {"missing", "conflict"}:
                continue
            diagnostics.append(
                {
                    "severity": check.get("level"),
                    "kind": "missing_record" if check.get("status") == "missing" else "record_reference_conflict",
                    "task_id": task.get("task_id"),
                    "field": check.get("reference"),
                    "record_type": check.get("record_type"),
                    "record_id": check.get("record_id"),
                    "path": task.get("path"),
                    "message": check.get("message"),
                }
            )

    for record_type, items in (("session", records.get("sessions", [])), ("claim", records.get("claims", []))):
        for record in items:
            record_id = record.get("record_id")
            for message in record.get("parse_errors", []):
                diagnostics.append(
                    {
                        "severity": "warn",
                        "kind": "parse_error",
                        "task_id": record.get("task_id"),
                        "field": None,
                        "record_type": record_type,
                        "record_id": record_id,
                        "path": record.get("path"),
                        "message": message,
                    }
                )
            for message in record.get("warnings", []):
                lowered = str(message).lower()
                severity = "needs_owner" if "task_id mismatch" in lowered else "warn"
                kind = "task_id_mismatch" if "task_id mismatch" in lowered else "record_warning"
                diagnostics.append(
                    {
                        "severity": severity,
                        "kind": kind,
                        "task_id": record.get("task_id"),
                        "field": None,
                        "record_type": record_type,
                        "record_id": record_id,
                        "path": record.get("path"),
                        "message": message,
                    }
                )

    for record_type, index_name in (("session", "session_index"), ("claim", "claim_index")):
        for record_id, matches in records.get(index_name, {}).items():
            if len(matches) <= 1:
                continue
            diagnostics.append(
                {
                    "severity": "needs_owner",
                    "kind": "duplicate_record_id",
                    "task_id": None,
                    "field": None,
                    "record_type": record_type,
                    "record_id": record_id,
                    "path": None,
                    "message": f"Duplicate {record_type}_id found: {record_id}",
                    "related_tasks": sorted({str(item.get('task_id')) for item in matches if item.get('task_id')}),
                    "matches": [
                        {
                            "path": item.get("path"),
                            "task_id": item.get("task_id"),
                            "record_id": item.get("record_id"),
                        }
                        for item in matches
                    ],
                }
            )

    diagnostics.sort(
        key=lambda item: (
            {"info": 0, "warn": 1, "needs_owner": 2}.get(str(item.get("severity")), 9),
            str(item.get("task_id") or ""),
            str(item.get("record_type") or ""),
            str(item.get("field") or ""),
            str(item.get("record_id") or ""),
            str(item.get("message") or ""),
        )
    )
    return diagnostics


def validate_task(
    task: dict[str, Any],
    duplicate_task_ids: set[str] | None = None,
    current_actor: str | None = None,
    records: dict[str, Any] | None = None,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = task["metadata"]
    queue_state = task["queue_state"]
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    needs_owner_reasons: list[str] = []
    match_details = {
        "matched": False,
        "reason": "not_checked",
        "matched_value": None,
        "actor_canonical_agent": None,
        "task_assigned_to_canonical": None,
        "actor_aliases": [],
    }

    if task["parse_errors"]:
        for error in task["parse_errors"]:
            _add(blocking_reasons, f"Frontmatter parse issue: {error}")

    for field in REQUIRED_FIELDS:
        if _is_missing(metadata.get(field)):
            _add(blocking_reasons, f"Missing required field: {field}")

    task_id = metadata.get("task_id")
    if duplicate_task_ids and task_id in duplicate_task_ids:
        _add(blocking_reasons, f"Duplicate task_id across queue: {task_id}")
        _add(needs_owner_reasons, f"Duplicate task_id requires manual repair: {task_id}")

    if task["frontmatter_status"] != queue_state:
        _add(blocking_reasons, "Queue directory does not match frontmatter status")
        _add(needs_owner_reasons, "Directory/status mismatch")

    if queue_state not in {"pending", "claimed", "completed", "blocked"}:
        _add(blocking_reasons, f"Invalid queue state: {queue_state}")

    if _is_missing(metadata.get("project")):
        _add(warnings, "Missing project")
    if _is_missing(metadata.get("agent_instance")) and not _is_missing(metadata.get("assigned_to")):
        _add(warnings, "Missing agent_instance")
    if _is_missing(metadata.get("model_tier")):
        if str(metadata.get("risk_level")).lower() == "high":
            _add(needs_owner_reasons, "High-risk task missing model_tier")
        else:
            _add(warnings, "Missing model_tier")
    if _is_missing(metadata.get("session_policy")):
        _add(warnings, "Missing session_policy")
    if _is_missing(metadata.get("context_isolation")):
        if str(metadata.get("risk_level")).lower() == "high":
            _add(needs_owner_reasons, "High-risk task missing context_isolation")
        else:
            _add(warnings, "Missing context_isolation")
    if _is_missing(metadata.get("artifact_scope")):
        _add(warnings, "Missing artifact_scope")
    if _is_missing(metadata.get("memory_scope")):
        _add(warnings, "Missing memory_scope")

    _validate_external_intake_metadata(metadata, warnings)
    complexity = validate_task_complexity(metadata, enforce_dependency_gate=True)
    for message in complexity["blocking_reasons"]:
        _add(blocking_reasons, message)
    classification_warnings = list(complexity["warnings"])
    for message in complexity["needs_owner_reasons"]:
        _add(needs_owner_reasons, message)

    if metadata.get("needs_owner") is True:
        _add(needs_owner_reasons, "needs_owner is true")
    if metadata.get("approval_required") is True:
        _add(needs_owner_reasons, "approval_required is true")
    if metadata.get("owner_review_required") is True:
        _add(needs_owner_reasons, "owner_review_required is true")

    if current_actor:
        actor_matches = (
            actor_matches_task(task, current_actor, profiles or {})
            if profiles is not None
            else current_actor in {
                metadata.get("assigned_to"),
                metadata.get("agent_instance"),
                metadata.get("claimed_by"),
            }
        )
        if profiles is not None:
            match_details = actor_match_details(task, current_actor, profiles)
            availability_warning = availability_warning_for_actor(current_actor, profiles)
            if availability_warning:
                _add(warnings, availability_warning)
        if not actor_matches:
            _add(blocking_reasons, "Current actor does not match assigned_to or agent_instance")

    if queue_state == "claimed":
        for field in ("claim_id", "claimed_by", "claimed_at", "active_session_id"):
            if _is_missing(metadata.get(field)):
                _add(blocking_reasons, f"Claimed task missing {field}")
        if not _is_missing(metadata.get("claim_id")) and not _valid_runtime_id(
            metadata.get("claim_id"), "claim", task_id
        ):
            _add(blocking_reasons, "Invalid claim_id format on claimed task")
        if not _is_missing(metadata.get("active_session_id")) and not _valid_runtime_id(
            metadata.get("active_session_id"), "session", task_id
        ):
            _add(blocking_reasons, "Invalid active_session_id format on claimed task")
        claimed_by_matches = (
            actor_matches_task_actor(metadata.get("claimed_by"), metadata.get("assigned_to"), profiles or {})
            or actor_matches_task_actor(metadata.get("claimed_by"), metadata.get("agent_instance"), profiles or {})
            if profiles is not None
            else metadata.get("claimed_by") in {metadata.get("assigned_to"), metadata.get("agent_instance")}
        )
        if metadata.get("claimed_by") and not claimed_by_matches:
            _add(needs_owner_reasons, "claimed_by does not match assigned_to or agent_instance")
        if metadata.get("last_session_id") and metadata.get("active_session_id"):
            _add(warnings, "Claimed task has both last_session_id and active_session_id")
        if current_actor and metadata.get("claimed_by") and not (
            actor_matches_task_actor(current_actor, metadata.get("claimed_by"), profiles or {})
            if profiles is not None
            else metadata.get("claimed_by") == current_actor
        ):
            _add(blocking_reasons, "Task is claimed by another actor")

    elif queue_state == "completed":
        for field in ("completed_by", "completed_at"):
            if _is_missing(metadata.get(field)):
                _add(blocking_reasons, f"Completed task missing {field}")
        if _is_missing(metadata.get("last_session_id")):
            _add(warnings, "Completed task missing last_session_id")
        if not _is_missing(metadata.get("active_session_id")):
            _add(blocking_reasons, "Completed task should not have active_session_id")

    elif queue_state == "blocked":
        for field in ("blocked_by", "blocked_at", "block_reason"):
            if _is_missing(metadata.get(field)):
                _add(blocking_reasons, f"Blocked task missing {field}")
        if _is_missing(metadata.get("last_session_id")):
            _add(warnings, "Blocked task missing last_session_id")
        if not _is_missing(metadata.get("active_session_id")):
            _add(blocking_reasons, "Blocked task should not have active_session_id")

    elif queue_state == "pending":
        if not _is_missing(metadata.get("active_session_id")):
            _add(warnings, "Pending task has active_session_id")
        if not _is_missing(metadata.get("claim_id")):
            _add(warnings, "Pending task retains claim_id")

    record_links = {"sessions": [], "claims": []}
    record_ref_checks = {"checks": [], "warnings": [], "needs_owner_reasons": []}
    if records is not None and task_id:
        record_links = find_records_for_task(records, str(task_id))
        record_ref_checks = check_task_record_refs(task, records)
        for message in record_ref_checks["warnings"]:
            _add(warnings, message)
        for message in record_ref_checks["needs_owner_reasons"]:
            _add(needs_owner_reasons, message)

    verdict = _derive_verdict(blocking_reasons, warnings, needs_owner_reasons)
    recommended_action = {
        "PASS": "start_session",
        "WARN": "acknowledge_and_continue",
        "NEEDS_OWNER": "send_to_needs_owner",
        "BLOCK": "do_not_execute",
    }[verdict]
    if "Queue directory does not match frontmatter status" in blocking_reasons:
        recommended_action = "repair_status_frontmatter"

    return {
        **task,
        "status": metadata.get("status"),
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "warnings": [*warnings, *classification_warnings],
        "classification_warnings": classification_warnings,
        "needs_owner_reasons": needs_owner_reasons,
        "recommended_action": recommended_action,
        "record_links": record_links,
        "record_ref_checks": record_ref_checks["checks"],
        "records": _task_records_summary(
            {"record_links": record_links, "record_ref_checks": record_ref_checks["checks"]}
        ),
        "actor_match": match_details,
    }


def validate_tasks(
    tasks: list[dict[str, Any]],
    current_actor: str | None = None,
    records: dict[str, Any] | None = None,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts = Counter(task["task_id"] for task in tasks if task.get("task_id"))
    duplicate_task_ids = {task_id for task_id, count in counts.items() if count > 1}

    validated = [
        validate_task(task, duplicate_task_ids, current_actor=current_actor, records=records, profiles=profiles)
        for task in tasks
    ]
    summary = Counter(task["verdict"].lower() for task in validated)
    return {
        "scope": "queue",
        "summary": {
            "total_tasks": len(validated),
            "pass": summary.get("pass", 0),
            "warn": summary.get("warn", 0),
            "block": summary.get("block", 0),
            "needs_owner": summary.get("needs_owner", 0),
        },
        "records_summary": build_records_summary(records or {}, validated),
        "records_diagnostics": build_records_diagnostics(records or {}, validated),
        "tasks": validated,
    }


def validate_single_task(
    task: dict[str, Any],
    tasks: list[dict[str, Any]] | None = None,
    current_actor: str | None = None,
    records: dict[str, Any] | None = None,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    comparison_tasks = tasks or [task]
    counts = Counter(item["task_id"] for item in comparison_tasks if item.get("task_id"))
    duplicate_task_ids = {task_id for task_id, count in counts.items() if count > 1}
    return validate_task(task, duplicate_task_ids, current_actor=current_actor, records=records, profiles=profiles)
