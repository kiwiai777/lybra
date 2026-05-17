from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.agent_profiles import runtime_config_for_actor
from tools.aipos_cli.records import (
    check_task_record_refs,
    expected_claim_log_path,
    expected_session_record_path,
    find_records_for_task,
)


def actor_to_slug(actor: str | None) -> str:
    source = (actor or "unknown_actor").strip().lower()
    chars = [char if char.isalnum() else "_" for char in source]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "unknown_actor"


def build_preview(
    task: dict[str, Any],
    actor: str | None,
    records: dict[str, Any] | None = None,
    profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = task["metadata"]
    selected_actor = actor or metadata.get("assigned_to") or metadata.get("agent_instance") or "unknown_actor"
    actor_slug = actor_to_slug(str(selected_actor))
    created_at = datetime.now(timezone.utc)
    stamp = created_at.strftime("%Y%m%d_%H%M%S")
    task_id = task.get("task_id") or "UNKNOWN_TASK"
    repo_root = Path(task.get("repo_root", "."))

    verdict = task["verdict"]
    can_start = verdict in {"PASS", "WARN"}
    copy_allowed = verdict in {"PASS", "WARN"}
    claim_allowed = verdict in {"PASS", "WARN"}
    run_locally_allowed = verdict in {"PASS", "WARN"}
    proposed_session_id = f"session_{task_id}_{stamp}_{actor_slug}"
    proposed_claim_id = f"claim_{task_id}_{stamp}_{actor_slug}"
    existing_records = find_records_for_task(records, task_id) if records is not None else {"sessions": [], "claims": []}
    runtime_config = runtime_config_for_actor(selected_actor, profiles or {})
    record_report = check_task_record_refs(task, records) if records is not None else {
        "checks": [],
        "warnings": [],
        "needs_owner_reasons": [],
    }

    return {
        "preview_id": f"preview_{task_id}_{stamp}_{actor_slug}",
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "task_path": task.get("path"),
        "queue_state": task.get("queue_state"),
        "frontmatter_status": task.get("frontmatter_status"),
        "status_consistent": task.get("status_consistent"),
        "current_actor": selected_actor,
        "actor_canonical_agent": task.get("actor_match", {}).get("actor_canonical_agent"),
        "actor_profile_matched": runtime_config.get("actor_profile_matched", False),
        "actor_match_reason": task.get("actor_match", {}).get("reason"),
        "actor_aliases": task.get("actor_match", {}).get("actor_aliases", []),
        "task_assigned_to_canonical": task.get("actor_match", {}).get("task_assigned_to_canonical"),
        "actor_availability_status": runtime_config.get("actor_availability_status", "unknown"),
        "actor_instance_availability_status": runtime_config.get("actor_instance_availability_status", "unknown"),
        "actor_agent_availability_status": runtime_config.get("actor_agent_availability_status", "unknown"),
        "availability_warning": runtime_config.get("availability_warning"),
        "assigned_to": task.get("assigned_to"),
        "agent_instance": task.get("agent_instance"),
        "can_start_session": can_start,
        "verdict": verdict,
        "blocking_reasons": task.get("blocking_reasons", []),
        "warnings": task.get("warnings", []),
        "needs_owner_reasons": task.get("needs_owner_reasons", []),
        "proposed_session_id": proposed_session_id,
        "proposed_claim_id": proposed_claim_id,
        "proposed_session_record_path": str(
            expected_session_record_path(repo_root, task_id, proposed_session_id).relative_to(repo_root)
        ),
        "proposed_claim_log_path": str(
            expected_claim_log_path(repo_root, task_id, proposed_claim_id).relative_to(repo_root)
        ),
        "existing_session_records": existing_records["sessions"],
        "existing_claim_logs": existing_records["claims"],
        "record_ref_checks": record_report["checks"],
        "record_warnings": record_report["warnings"] + record_report["needs_owner_reasons"],
        "runtime_profile": runtime_config.get("runtime_profile"),
        "runtime_entrypoint": runtime_config.get("runtime_entrypoint"),
        "runtime_command": runtime_config.get("runtime_command"),
        "runtime_args": runtime_config.get("runtime_args", []),
        "runtime_env": runtime_config.get("runtime_env", {}),
        "launch_notes": runtime_config.get("launch_notes"),
        "session_policy": metadata.get("session_policy"),
        "context_isolation": metadata.get("context_isolation"),
        "artifact_scope": metadata.get("artifact_scope"),
        "memory_scope": metadata.get("memory_scope"),
        "output_target": metadata.get("output_target"),
        "artifact_policy": metadata.get("artifact_policy"),
        "copy_context_allowed": copy_allowed,
        "claim_allowed": claim_allowed,
        "run_locally_allowed": run_locally_allowed,
        "recommended_action": task.get("recommended_action"),
        "created_at": created_at.isoformat(),
        "project": metadata.get("project"),
        "task_mode": metadata.get("task_mode"),
        "model_tier": metadata.get("model_tier"),
        "context_bundle": metadata.get("context_bundle"),
        "priority": metadata.get("priority"),
        "claim_policy": metadata.get("claim_policy"),
        "polling_mode": metadata.get("polling_mode"),
        "report_mode": metadata.get("report_mode"),
        "needs_owner": metadata.get("needs_owner"),
        "risk_level": metadata.get("risk_level"),
        "approval_required": metadata.get("approval_required"),
        "owner_review_required": metadata.get("owner_review_required"),
        "active_session_id": metadata.get("active_session_id"),
        "last_session_id": metadata.get("last_session_id"),
        "claim_id": metadata.get("claim_id"),
    }
