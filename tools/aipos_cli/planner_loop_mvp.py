from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_validator import list_drafts, validate_draft_file
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.orchestration_timeline_preview import build_orchestration_timeline_preview
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks

ORCHESTRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and ".." not in value and bool(ORCHESTRATION_ID_PATTERN.fullmatch(value))


def _frontmatter_matches_orchestration(frontmatter: dict[str, Any], orchestration_id: str) -> bool:
    if _safe_text(frontmatter.get("orchestration_id")) == orchestration_id:
        return True
    orchestration = frontmatter.get("orchestration")
    return isinstance(orchestration, dict) and _safe_text(orchestration.get("orchestration_id")) == orchestration_id


def _planner_draft_candidates(repo_root: Path, orchestration_id: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for draft in list_drafts(repo_root).get("drafts", []):
        path = _safe_text(draft.get("path"))
        if not path:
            continue
        validation = validate_draft_file(repo_root, path)
        frontmatter = dict(validation.get("frontmatter") or {})
        if not _frontmatter_matches_orchestration(frontmatter, orchestration_id):
            continue
        publish_status = _safe_text(frontmatter.get("publish_status")) or _safe_text(frontmatter.get("draft_status"))
        owner_gate = frontmatter.get("needs_owner") is True or publish_status in {"needs_owner", "blocked"}
        publish_ready = publish_status == "approved_for_publish" and validation.get("verdict") in {"PASS", "WARN"} and not owner_gate
        candidates.append(
            {
                "task_id": _safe_text(frontmatter.get("task_id")),
                "title": _safe_text(frontmatter.get("title")),
                "path": path,
                "assigned_to": _safe_text(frontmatter.get("assigned_to")),
                "reviewer": _safe_text(frontmatter.get("reviewer")),
                "audit_by": _safe_text(frontmatter.get("audit_by")),
                "publish_status": publish_status,
                "validation_verdict": _safe_text(validation.get("verdict")),
                "publish_ready": publish_ready,
                "owner_gate": owner_gate,
                "blocking_reasons": list(validation.get("blocking_reasons", [])),
                "warnings": list(validation.get("warnings", [])),
            }
        )
    return candidates


def _build_recommended_step(
    summary: dict[str, Any],
    timeline: dict[str, Any],
    drafts: list[dict[str, Any]],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    planned_summary = dict(summary.get("planned_summary") or {})
    owner_reasons = list(planned_summary.get("needs_owner_reasons", []))
    owner_attention = int((timeline.get("summary") or {}).get("owner_attention_count") or 0)
    conflicts = list(summary.get("conflicts", [])) + list(timeline.get("conflicts", []))
    publish_ready = [draft for draft in drafts if draft.get("publish_ready")]
    owner_gated_drafts = [draft for draft in drafts if draft.get("owner_gate")]

    if blocking_reasons:
        return {
            "step": "repair_orchestration_context",
            "route": "orchestration_summary",
            "reason": "Blocking context issue prevents safe planner loop coordination.",
            "requires_owner": False,
            "controlled_mutation_available": False,
        }
    if owner_reasons or owner_attention or conflicts or owner_gated_drafts:
        return {
            "step": "stop_for_owner_decision",
            "route": "owner_decision_gate",
            "reason": "Owner decision gate is active before the loop can continue.",
            "requires_owner": True,
            "controlled_mutation_available": False,
        }
    if publish_ready:
        return {
            "step": "review_controlled_publish",
            "route": "approved_planner_draft_publish",
            "reason": "Approved planner draft candidates can use the existing controlled publish UI.",
            "requires_owner": True,
            "controlled_mutation_available": True,
            "controlled_operation": "draft_publish",
        }
    if drafts:
        return {
            "step": "review_planner_drafts",
            "route": "planner_draft_review",
            "reason": "Planner drafts exist but are not yet ready for controlled publish.",
            "requires_owner": False,
            "controlled_mutation_available": False,
        }
    if int(planned_summary.get("open_subtask_count") or 0) > 0:
        return {
            "step": "monitor_open_subtasks",
            "route": "orchestration_timeline",
            "reason": "Open subtasks exist; review queue, records, summary, and timeline before the next planner tick.",
            "requires_owner": False,
            "controlled_mutation_available": False,
        }
    return {
        "step": "run_manual_planner_tick_preview",
        "route": "manual_planner_tick_flow",
        "reason": "No active Owner gate or publish candidate was found; prepare one manual planner tick preview.",
        "requires_owner": False,
        "controlled_mutation_available": False,
    }


def build_planner_loop_mvp_preview(repo_root: Path, orchestration_id: str, *, actor: str | None = None) -> dict[str, Any]:
    orchestration_id = _safe_text(orchestration_id)
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    if not _safe_id(orchestration_id):
        blocking_reasons.append("orchestration_id is required and must be path-safe")

    tasks = load_all_tasks(repo_root) if not blocking_reasons else []
    records = load_records(repo_root) if not blocking_reasons else {"summary": {}}
    summary = build_orchestration_summary_preview(repo_root, orchestration_id, tasks=tasks, records=records) if not blocking_reasons else {}
    timeline = build_orchestration_timeline_preview(repo_root, orchestration_id) if not blocking_reasons else {}
    drafts = _planner_draft_candidates(repo_root, orchestration_id) if not blocking_reasons else []

    blocking_reasons.extend(summary.get("blocking_reasons", []))
    blocking_reasons.extend(timeline.get("blocking_reasons", []))
    warnings.extend(summary.get("warnings", []))
    warnings.extend(timeline.get("warnings", []))
    warnings = list(dict.fromkeys(str(item) for item in warnings if str(item)))
    blocking_reasons = list(dict.fromkeys(str(item) for item in blocking_reasons if str(item)))

    recommended_step = _build_recommended_step(summary, timeline, drafts, blocking_reasons)
    planned_summary = dict(summary.get("planned_summary") or {})
    timeline_summary = dict(timeline.get("summary") or {})
    owner_reasons = list(planned_summary.get("needs_owner_reasons", []))
    owner_reasons.extend(item.get("summary") for item in timeline.get("timeline", []) if item.get("owner_attention_required") and item.get("summary"))
    owner_reasons = list(dict.fromkeys(str(item) for item in owner_reasons if str(item)))
    publish_ready = [draft for draft in drafts if draft.get("publish_ready")]

    verdict = "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if recommended_step.get("requires_owner") and not publish_ready else "PASS")
    return {
        "action": "planner_loop_mvp_preview",
        "orchestration_id": orchestration_id,
        "actor": _safe_text(actor),
        "verdict": verdict,
        "dry_run": True,
        "would_write": False,
        "writes_enabled": False,
        "execute_allowed": False,
        "dry_run_token": None,
        "planned_writes": [],
        "planned_moves": [],
        "controlled_mutation_enabled": False,
        "autonomous_runtime_enabled": False,
        "automatic_polling_enabled": False,
        "automatic_agent_execution_enabled": False,
        "automatic_publish_enabled": False,
        "automatic_claim_enabled": False,
        "automatic_push_enabled": False,
        "self_audit_enabled": False,
        "recommended_step": recommended_step,
        "loop_state_preview": {
            "status": planned_summary.get("status", "unknown"),
            "current_iteration": planned_summary.get("current_iteration", 0),
            "open_subtask_count": planned_summary.get("open_subtask_count", 0),
            "completed_subtask_count": planned_summary.get("completed_subtask_count", 0),
            "blocked_subtask_count": planned_summary.get("blocked_subtask_count", 0),
            "timeline_items": timeline_summary.get("timeline_items", 0),
            "owner_attention_count": timeline_summary.get("owner_attention_count", 0),
            "planner_model_tier": planned_summary.get("planner_model_tier", ""),
            "planner_agent": planned_summary.get("planner_agent", ""),
            "planner_agent_instance": planned_summary.get("planner_agent_instance", ""),
        },
        "owner_gate": {
            "active": bool(owner_reasons or summary.get("conflicts") or timeline.get("conflicts")),
            "reasons": owner_reasons,
            "conflicts": list(summary.get("conflicts", [])) + list(timeline.get("conflicts", [])),
        },
        "draft_candidates": drafts,
        "controlled_handoffs": [
            {
                "route": "approved_planner_draft_publish",
                "operation": "draft_publish",
                "available": bool(publish_ready),
                "candidate_count": len(publish_ready),
                "requires_second_confirmation": True,
            },
            {
                "route": "manual_planner_tick_flow",
                "operation": "planner_tick_manual_flow_preview",
                "available": recommended_step.get("step") == "run_manual_planner_tick_preview",
                "candidate_count": 0,
                "requires_second_confirmation": False,
            },
        ],
        "source_refs": list(dict.fromkeys(list(summary.get("source_refs", [])) + list(timeline.get("source_refs", [])) + ["5_tasks/drafts/"])),
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": owner_reasons if verdict == "NEEDS_OWNER" else [],
        "safety_notice": "AIPOS-75 planner loop MVP is a single-step coordinator preview. It writes no files, launches no runtime, polls no queue, runs no agents, publishes or claims nothing, and returns no execute token.",
    }
