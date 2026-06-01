from __future__ import annotations

import argparse
import os
import json
import re
import sys
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.adapter_response import blocked_response
from tools.aipos_cli.ai_assisted_authoring import build_authoring_draft, confirm_authoring_draft
from tools.aipos_cli.board_adapter import (
    append_orchestration_event,
    append_planner_iteration,
    claim_task,
    create_draft,
    execute_dry_run,
    get_agents,
    get_context_pack_preview,
    get_drafts,
    get_external_intake_review,
    get_health,
    get_governance,
    get_needs_owner,
    get_owner_decision_records,
    get_orchestration_summary_preview,
    get_orchestration_index,
    get_orchestration_timeline_preview,
    get_planner_loop_mvp_preview,
    get_preview,
    get_queue,
    get_records,
    get_task,
    get_validate,
    publish_draft,
    record_owner_decision,
)
from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
from tools.aipos_cli.draft_validator import validate_draft_file
from tools.aipos_cli.draft_writer import publish_draft as backend_publish_draft

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    return "application/octet-stream"


def _api_routes(repo_root: Path | None) -> dict[str, Callable[[dict[str, list[str]]], dict[str, Any]]]:
    return {
        "/api/health": lambda _params: get_health(repo_root=repo_root),
        "/api/governance": lambda _params: get_governance(repo_root=repo_root),
        "/api/queue": lambda _params: get_queue(repo_root=repo_root),
        "/api/needs-owner": lambda _params: get_needs_owner(repo_root=repo_root),
        "/api/validate": lambda _params: get_validate(repo_root=repo_root),
        "/api/agents": lambda _params: get_agents(repo_root=repo_root),
        "/api/drafts": lambda _params: get_drafts(repo_root=repo_root),
        "/api/records": lambda _params: get_records(repo_root=repo_root),
        "/api/external-intake/review": lambda _params: get_external_intake_review(repo_root=repo_root),
        "/api/owner-decision-records": lambda _params: get_owner_decision_records(repo_root=repo_root),
        "/api/planner-drafts/review": partial(_get_planner_drafts_review_route, repo_root=repo_root),
        "/api/owner-decisions/review": partial(_get_owner_decisions_review_route, repo_root=repo_root),
        "/api/orchestration/index": lambda _params: get_orchestration_index(repo_root=repo_root),
        "/api/orchestration-summary": partial(_get_orchestration_summary_route, repo_root=repo_root),
        "/api/orchestration/summary": partial(_get_orchestration_summary_route, repo_root=repo_root),
        "/api/orchestration-timeline": partial(_get_orchestration_timeline_route, repo_root=repo_root),
        "/api/orchestration/timeline": partial(_get_orchestration_timeline_route, repo_root=repo_root),
        "/api/planner-loop/mvp": partial(_get_planner_loop_mvp_route, repo_root=repo_root),
        "/api/context-pack/preview": partial(_get_context_pack_preview_route, repo_root=repo_root),
        "/api/task": partial(_get_task_route, repo_root=repo_root),
        "/api/preview": partial(_get_preview_route, repo_root=repo_root),
    }


def _api_post_routes(repo_root: Path | None) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    return {
        "/api/parent-requirement/preview": partial(_parent_requirement_preview_route, repo_root=repo_root),
        "/api/planner-tick/preview": partial(_planner_tick_preview_route, repo_root=repo_root),
        "/api/planner-tick/manual-flow/preview": partial(_planner_tick_manual_flow_preview_route, repo_root=repo_root),
        "/api/planner-draft/review": partial(_planner_draft_review_route, repo_root=repo_root),
        "/api/forum-event/review": partial(_forum_event_review_route, repo_root=repo_root),
        "/api/owner-decision/resolve/review": partial(_owner_decision_resolution_review_route, repo_root=repo_root),
        "/api/planner-draft/publish/dry-run": partial(_planner_draft_publish_dry_run_route, repo_root=repo_root),
        "/api/ai-author/preview": partial(_ai_author_preview_route, repo_root=repo_root),
        "/api/ai-author/confirm": partial(_ai_author_confirm_route, repo_root=repo_root),
        "/api/execute/dry-run": partial(_execute_dry_run_route, repo_root=repo_root),
        "/api/execute/confirm": partial(_execute_confirm_route, repo_root=repo_root),
    }


def _first_param(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name) or []
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def _selector_error(operation: str, message: str) -> dict[str, Any]:
    return blocked_response(
        operation=operation,
        dry_run=False,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local read-only web UI route. No files are written.",
    )


def _execute_error(operation: str, message: str, *, category: str = "VALIDATION_ERROR") -> dict[str, Any]:
    return blocked_response(
        operation=operation,
        dry_run=True,
        category=category,
        message=message,
        safety_notice="Local controlled execute UI route. Writes require dry-run token revalidation.",
    )


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", text) or "requirement"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parent_requirement_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="parent_requirement_preview",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local parent requirement preview route. No files are written.",
    )


PLANNER_TICK_VERDICTS = {
    "continue",
    "draft_subtasks",
    "publish_ready",
    "wait_for_audit",
    "repair",
    "needs_owner",
    "blocked",
    "complete",
    "cancel",
    "failed",
}


def _planner_tick_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="planner_tick_preview",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local planner tick preview route. No files are written.",
    )


def _list_from_payload(payload: dict[str, Any], name: str) -> list[str]:
    value = payload.get(name)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


PLANNER_DRAFT_REQUIRED_FIELDS = [
    "draft_id",
    "draft_status",
    "draft_created_by",
    "draft_created_at",
    "draft_source",
    "publish_status",
    "publish_target",
    "requirement_id",
    "orchestration_id",
    "parent_task_id",
    "created_by_planner",
    "planner_agent",
    "planner_agent_instance",
    "planner_model_tier",
    "planner_iteration_id",
    "iteration",
    "subtask_sequence",
    "subtask_type",
    "depends_on",
    "reviewer",
    "audit_by",
    "assigned_to",
    "agent_instance",
    "context_bundle",
    "task_mode",
    "model_tier",
    "output_target",
    "artifact_policy",
    "session_policy",
    "context_isolation",
    "artifact_scope",
    "memory_scope",
    "forum_thread_ref",
]


def _planner_draft_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="planner_draft_review",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local planner draft review route. No files are written.",
    )


def _is_missing_metadata(metadata: dict[str, Any], field: str) -> bool:
    return metadata.get(field) in (None, "", [])


ORCHESTRATION_EVENT_TYPES = {
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

ORCHESTRATION_EVENT_SEVERITIES = {"info", "warning", "needs_owner", "blocking"}
ORCHESTRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _forum_event_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="forum_event_persistence_review",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local forum event persistence review route. No files are written.",
    )


def _safe_orchestration_id(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    if "/" in value or "\\" in value or ".." in value:
        return False
    return bool(ORCHESTRATION_ID_PATTERN.fullmatch(value))



OWNER_DECISION_TYPE_KEYWORDS = {
    "architecture": ["architecture", "route", "design", "boundary", "service", "database", "deployment"],
    "scope": ["scope", "expand", "expansion", "out of scope", "requirement"],
    "risk": ["risk", "high-risk", "irreversible", "data loss", "refactor"],
    "security": ["security", "credential", "secret", "permission", "auth", "rbac"],
    "model_tier": ["model", "tier", "l3", "l4", "authority"],
    "authority": ["authority", "permission", "owner", "agent", "role"],
    "audit_boundary": ["audit", "reviewer", "auditor", "self-audit"],
    "publish_finalize": ["publish", "finalize", "commit", "push", "release"],
    "long_term_direction": ["long-term", "direction", "strategy", "workflow", "policy"],
}


def _decision_type_from_text(values: list[Any]) -> str:
    text = " ".join(str(value or "") for value in values).lower()
    for decision_type, keywords in OWNER_DECISION_TYPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return decision_type
    return "owner_review"


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            seen.setdefault(text, None)
    return list(seen.keys())


def _get_owner_decisions_review_route(_params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    requests: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    try:
        needs_owner = get_needs_owner(repo_root=resolved_root)
    except Exception as exc:
        needs_owner = {"data": {"tasks": []}}
        warnings.append(f"Unable to read needs-owner tasks: {str(exc) or exc.__class__.__name__}")
    needs_owner_data = needs_owner.get("data") if isinstance(needs_owner, dict) else {}
    if not isinstance(needs_owner_data, dict):
        needs_owner_data = {}
    for task in needs_owner_data.get("tasks", []) or []:
        metadata = dict(task.get("metadata") or {})
        reasons = _dedupe_strings(list(task.get("needs_owner_reasons", [])) + list(metadata.get("needs_owner_reasons") or []))
        title = metadata.get("title") or task.get("title") or task.get("task_id") or task.get("path")
        source_refs = _dedupe_strings([task.get("path"), metadata.get("forum_thread_ref")])
        requests.append(
            {
                "request_id": f"queue:{task.get('task_id') or task.get('path')}",
                "source": "queue_task",
                "decision_type": _decision_type_from_text([title, *reasons, metadata.get("output_target"), metadata.get("artifact_policy")]),
                "title": title,
                "summary": "; ".join(reasons) or "Task requires Owner review.",
                "severity": "needs_owner",
                "status": "open",
                "related_task_id": task.get("task_id"),
                "related_orchestration_id": metadata.get("orchestration_id"),
                "related_iteration_id": metadata.get("planner_iteration_id"),
                "source_refs": source_refs,
                "timeline_refs": [],
                "owner_decision_required": True,
                "review_only": True,
                "resolution_enabled": False,
            }
        )

    orchestration_root = resolved_root / "5_tasks" / "orchestration"
    if orchestration_root.exists():
        for directory in sorted(orchestration_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            orchestration_id = directory.name
            try:
                timeline = get_orchestration_timeline_preview(orchestration_id=orchestration_id, repo_root=resolved_root)
            except Exception as exc:
                warnings.append(f"Unable to read orchestration timeline {orchestration_id}: {str(exc) or exc.__class__.__name__}")
                continue
            if timeline.get("verdict") == "BLOCK":
                blocking_reasons.extend(str(item) for item in timeline.get("blocking_reasons", []))
            for item in timeline.get("data", {}).get("timeline", []) or []:
                if not item.get("owner_attention_required") and not item.get("blocking"):
                    continue
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                reasons = _dedupe_strings(raw.get("needs_owner_reasons") if isinstance(raw.get("needs_owner_reasons"), list) else [])
                summary = item.get("summary") or "; ".join(reasons) or item.get("title") or "Owner decision requested."
                refs = _dedupe_strings(list(item.get("refs") or []) + [item.get("source_ref")])
                requests.append(
                    {
                        "request_id": f"timeline:{orchestration_id}:{item.get('id') or item.get('timestamp')}",
                        "source": item.get("kind") or "orchestration_timeline",
                        "decision_type": _decision_type_from_text([item.get("title"), summary, *reasons, item.get("severity")]),
                        "title": item.get("title") or item.get("id") or "Timeline decision request",
                        "summary": summary,
                        "severity": item.get("severity") or "needs_owner",
                        "status": "open",
                        "related_task_id": item.get("related_task_id") or raw.get("parent_task_id"),
                        "related_orchestration_id": orchestration_id,
                        "related_iteration_id": item.get("related_iteration_id") or raw.get("iteration_id"),
                        "source_refs": refs,
                        "timeline_refs": [item.get("source_ref")],
                        "owner_decision_required": True,
                        "review_only": True,
                        "resolution_enabled": False,
                    }
                )

    type_counts: dict[str, int] = {}
    for request in requests:
        decision_type = str(request.get("decision_type") or "owner_review")
        type_counts[decision_type] = type_counts.get(decision_type, 0) + 1
    return {
        "ok": not blocking_reasons,
        "verdict": "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if requests else ("WARN" if warnings else "PASS")),
        "operation": "owner_decisions_review",
        "dry_run": True,
        "data": {
            "decision_requests": requests,
            "decision_type_counts": type_counts,
            "writes_enabled": False,
            "review_only": True,
            "controlled_mutation_allowed": False,
            "resolution_enabled": False,
            "mobile_responsive_required": True,
        },
        "summary": {
            "total": len(requests),
            "open": len(requests),
            "by_type": type_counts,
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": [str(item.get("summary")) for item in requests if item.get("summary")],
        "owner_confirmation_required": bool(requests),
        "owner_confirmation_reasons": [str(item.get("summary")) for item in requests if item.get("summary")],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-72 Owner Decision Gate UI is read-only and does not resolve decisions."],
        "dry_run_token": None,
        "safety_notice": "Local Owner decision review route. No files are written and no decisions are resolved.",
        "errors": [],
    }


def _get_planner_drafts_review_route(_params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    drafts_root = resolved_root / "5_tasks" / "drafts"
    drafts: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    base_payload = {
        "drafts_dir": "5_tasks/drafts",
        "writes_enabled": False,
        "review_only": True,
        "controlled_mutation_allowed": False,
        "publish_execute_disabled": True,
        "mobile_responsive_required": True,
    }

    if not drafts_root.exists():
        return {
            "ok": True,
            "verdict": "PASS",
            "operation": "planner_drafts_review",
            "dry_run": True,
            "data": {**base_payload, "drafts": []},
            "summary": {"total": 0, "planner_created_total": 0, "ready": 0, "needs_owner": 0, "blocked": 0, "review": 0},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": [],
            "blocking_reasons": [],
            "needs_owner_reasons": [],
            "owner_confirmation_required": False,
            "owner_confirmation_reasons": [],
            "execute_allowed": False,
            "execute_blocking_reasons": ["AIPOS-71 planner draft review desk is read-only."],
            "dry_run_token": None,
            "safety_notice": "Local planner draft review list route. No files are written.",
            "errors": [],
        }

    for path in sorted(drafts_root.rglob("*.md")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(resolved_root).as_posix()
        try:
            validation = validate_draft_file(resolved_root, rel_path)
            publish_preview = backend_publish_draft(resolved_root, rel_path, dry_run=True)
        except Exception as exc:
            warnings.append(f"Unable to review draft {rel_path}: {str(exc) or exc.__class__.__name__}")
            continue

        metadata = dict(validation.get("frontmatter") or {})
        planner_created = (
            str(metadata.get("draft_source") or "").strip() == "planner"
            or _as_bool(metadata.get("created_by_planner"))
            or "/planner/" in rel_path
        )
        if not planner_created:
            continue

        missing_fields = [field for field in PLANNER_DRAFT_REQUIRED_FIELDS if _is_missing_metadata(metadata, field)]
        publish_status = str(metadata.get("publish_status") or "").strip()
        draft_status = str(metadata.get("draft_status") or "").strip()
        planner_tier = str(metadata.get("planner_model_tier") or "").strip().upper()
        planner_agent = str(metadata.get("planner_agent") or "").strip()
        reviewer = str(metadata.get("reviewer") or "").strip()
        audit_by = str(metadata.get("audit_by") or "").strip()
        owner_gate = draft_status == "needs_owner" or publish_status == "needs_owner" or _as_bool(metadata.get("needs_owner"))
        rejected_or_blocked = draft_status in {"rejected", "superseded", "blocked"} or publish_status in {"rejected", "superseded", "blocked"}
        publish_target_ok = str(metadata.get("publish_target") or metadata.get("draft_publish_target") or "").strip() == "5_tasks/queue/pending/"
        publish_preview_blocked = str(publish_preview.get("verdict") or "") == "BLOCK"
        planner_separated = bool(planner_agent and reviewer and audit_by and planner_agent != reviewer and planner_agent != audit_by)
        ready = (
            not missing_fields
            and planner_tier in {"L3", "L4"}
            and publish_status == "approved_for_publish"
            and not owner_gate
            and not rejected_or_blocked
            and publish_target_ok
            and planner_separated
            and not publish_preview_blocked
        )
        if rejected_or_blocked or publish_preview_blocked or validation.get("verdict") == "BLOCK":
            review_status = "blocked"
        elif owner_gate or publish_status != "approved_for_publish":
            review_status = "needs_owner"
        elif ready:
            review_status = "ready"
        else:
            review_status = "review"

        drafts.append(
            {
                "task_id": validation.get("task_id"),
                "title": metadata.get("title"),
                "path": validation.get("path") or rel_path,
                "draft_status": draft_status or None,
                "publish_status": publish_status or None,
                "review_status": review_status,
                "planner_created": True,
                "assigned_to": metadata.get("assigned_to"),
                "agent_instance": metadata.get("agent_instance"),
                "task_mode": metadata.get("task_mode"),
                "task_class": metadata.get("task_class"),
                "effective_task_class": str(metadata.get("task_class") or "simple").strip().lower(),
                "complexity_note": metadata.get("complexity_note"),
                "planner_agent": planner_agent or None,
                "planner_agent_instance": metadata.get("planner_agent_instance"),
                "planner_model_tier": planner_tier or None,
                "reviewer": reviewer or None,
                "audit_by": audit_by or None,
                "depends_on": metadata.get("depends_on"),
                "forum_thread_ref": metadata.get("forum_thread_ref"),
                "requirement_id": metadata.get("requirement_id"),
                "orchestration_id": metadata.get("orchestration_id"),
                "parent_task_id": metadata.get("parent_task_id"),
                "publish_target": metadata.get("publish_target") or metadata.get("draft_publish_target"),
                "target_path": publish_preview.get("target_path"),
                "missing_metadata": missing_fields,
                "owner_gate": owner_gate,
                "publish_ready": ready,
                "validation_verdict": validation.get("verdict"),
                "publish_preview_verdict": publish_preview.get("verdict"),
                "warnings": list(validation.get("warnings", [])) + list(publish_preview.get("warnings", [])),
                "blocking_reasons": list(validation.get("blocking_reasons", [])) + list(publish_preview.get("blocking_reasons", [])),
                "review_only": True,
                "controlled_mutation_allowed": False,
                "publish_execute_disabled": True,
            }
        )

    summary = {
        "total": len(drafts),
        "planner_created_total": len(drafts),
        "ready": sum(1 for item in drafts if item.get("review_status") == "ready"),
        "needs_owner": sum(1 for item in drafts if item.get("review_status") == "needs_owner"),
        "blocked": sum(1 for item in drafts if item.get("review_status") == "blocked"),
        "review": sum(1 for item in drafts if item.get("review_status") == "review"),
    }
    return {
        "ok": True,
        "verdict": "WARN" if warnings else "PASS",
        "operation": "planner_drafts_review",
        "dry_run": True,
        "data": {**base_payload, "drafts": drafts},
        "summary": summary,
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": [],
        "owner_confirmation_required": False,
        "owner_confirmation_reasons": [],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-71 planner draft review desk is read-only."],
        "dry_run_token": None,
        "safety_notice": "Local planner draft review list route. No files are written.",
        "errors": [],
    }


def _forum_event_review_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    del repo_root
    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    event_type = str(payload.get("event_type") or "").strip()
    severity = str(payload.get("severity") or "info").strip()
    actor = str(payload.get("actor") or "").strip()
    source = str(payload.get("source") or "web_board_forum_event_review").strip()
    summary = str(payload.get("summary") or "").strip()
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    timestamp = str(payload.get("timestamp") or "").strip() or _utc_now()
    related_task_id = str(payload.get("related_task_id") or "").strip() or None
    related_subtask_id = str(payload.get("related_subtask_id") or "").strip() or None
    related_iteration_id = str(payload.get("related_iteration_id") or "").strip() or None
    details_text = str(payload.get("details") or "").strip()
    refs = _list_from_payload(payload, "refs")
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    needs_owner_reasons: list[str] = []
    preconditions: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str, severity_level: str = "block") -> None:
        preconditions.append({"name": name, "passed": passed, "severity": severity_level, "detail": detail})
        if passed:
            return
        if severity_level == "needs_owner":
            if detail not in needs_owner_reasons:
                needs_owner_reasons.append(detail)
        elif severity_level == "warn":
            if detail not in warnings:
                warnings.append(detail)
        elif detail not in blocking_reasons:
            blocking_reasons.append(detail)

    add_check("orchestration_id_present", bool(orchestration_id), "orchestration_id is required")
    add_check("orchestration_id_path_safe", _safe_orchestration_id(orchestration_id), "orchestration_id must be path-safe")
    add_check("event_type_allowed", event_type in ORCHESTRATION_EVENT_TYPES, "event_type must be allowed by orchestration_event_log_schema.md")
    add_check("severity_allowed", severity in ORCHESTRATION_EVENT_SEVERITIES, "severity must be info, warning, needs_owner, or blocking")
    add_check("actor_present", bool(actor), "actor is required")
    add_check("source_present", bool(source), "source is required")
    add_check("summary_present", bool(summary), "summary is required")
    add_check("forum_ref_present", bool(forum_thread_ref), "forum_thread_ref is required")

    if forum_thread_ref and forum_thread_ref not in refs:
        refs.insert(0, forum_thread_ref)

    owner_gate_event = event_type in {"needs_owner_raised", "owner_decision_recorded"} or severity == "needs_owner"
    if event_type == "owner_decision_recorded":
        has_owner_ref = any("owner" in ref.lower() or "decision" in ref.lower() for ref in refs)
        add_check(
            "owner_decision_evidence_ref",
            has_owner_ref,
            "owner_decision_recorded requires an Owner decision evidence ref",
        )
    if owner_gate_event:
        add_check(
            "owner_gate_preserved",
            event_type != "owner_decision_recorded" or not blocking_reasons,
            "Owner gate events must preserve or reference explicit Owner decision evidence",
            severity_level="needs_owner",
        )

    event_id = str(payload.get("event_id") or "").strip()
    if not event_id and orchestration_id and event_type:
        event_id = f"evt_{_slug(orchestration_id)}_{_slug(event_type)}_{timestamp[:10].replace('-', '')}"
    target_path = f"5_tasks/orchestration/{orchestration_id}/orchestration_events.md" if orchestration_id else None
    event_entry = {
        "event_id": event_id or None,
        "orchestration_id": orchestration_id or None,
        "event_type": event_type or None,
        "timestamp": timestamp,
        "actor": actor or None,
        "source": source or None,
        "related_task_id": related_task_id,
        "related_subtask_id": related_subtask_id,
        "related_iteration_id": related_iteration_id,
        "severity": severity,
        "summary": summary or None,
        "details": {"text": details_text} if details_text else {},
        "refs": refs,
    }
    append_plan = {
        "target_path": target_path,
        "append_only": True,
        "operation": "future_append_orchestration_event",
        "planned_writes": [
            {
                "path": target_path,
                "kind": "append",
                "type": "orchestration_event_entry",
            }
        ] if target_path and not blocking_reasons else [],
    }
    review_passed = not blocking_reasons
    verdict = "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if needs_owner_reasons else ("WARN" if warnings else "PASS"))
    return {
        "ok": review_passed,
        "verdict": verdict,
        "operation": "forum_event_persistence_review",
        "dry_run": True,
        "actor": {"actor": actor} if actor else None,
        "data": {
            "event_entry": event_entry,
            "append_plan": append_plan,
            "preconditions": preconditions,
            "writer_review_only": True,
            "writes_enabled": False,
            "forum_backend_enabled": False,
            "network_posting_enabled": False,
            "controlled_execute_expanded": False,
            "handoff_to_future_writer": {
                "enabled": review_passed,
                "next_operation": "append_only_orchestration_event_writer",
                "requires_future_audit": True,
            },
        },
        "summary": {
            "orchestration_id": orchestration_id,
            "event_type": event_type,
            "severity": severity,
            "target_path": target_path,
            "writer_review_passed": review_passed,
            "preconditions_total": len(preconditions),
            "preconditions_passed": sum(1 for item in preconditions if item.get("passed")),
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": needs_owner_reasons,
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-63 forum event persistence review is review-only; AIPOS-64 must implement any writer."],
        "safety_notice": "Local forum event persistence review route. No files are written.",
        "errors": [],
    }




def _owner_decision_resolution_review_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "").strip()
    decision = str(payload.get("decision") or "").strip()
    decision_reason = str(payload.get("decision_reason") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    evidence_ref = str(payload.get("evidence_ref") or "").strip()
    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    decision_type = str(payload.get("decision_type") or "owner_review").strip()
    related_task_id = str(payload.get("related_task_id") or "").strip()
    related_iteration_id = str(payload.get("related_iteration_id") or "").strip()
    allowed_decisions = {"approved", "rejected", "scope_reduced", "needs_revision", "deferred"}
    blocking_reasons: list[str] = []
    if not request_id:
        blocking_reasons.append("request_id is required")
    if decision not in allowed_decisions:
        blocking_reasons.append("decision must be approved, rejected, scope_reduced, needs_revision, or deferred")
    if not decision_reason:
        blocking_reasons.append("decision_reason is required")
    if not actor:
        blocking_reasons.append("actor is required")
    if not evidence_ref:
        blocking_reasons.append("evidence_ref is required")
    if not orchestration_id:
        blocking_reasons.append("orchestration_id is required")
    if not forum_thread_ref:
        blocking_reasons.append("forum_thread_ref is required")
    if blocking_reasons:
        return {
            "ok": False,
            "verdict": "BLOCK",
            "operation": "owner_decision_resolution_review",
            "dry_run": True,
            "data": {
                "resolution_review_only": True,
                "writes_enabled": False,
                "decision_persistence_enabled": False,
            },
            "summary": {"request_id": request_id, "decision": decision, "writes_enabled": False},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": [],
            "blocking_reasons": blocking_reasons,
            "needs_owner_reasons": [],
            "execute_allowed": False,
            "execute_blocking_reasons": ["AIPOS-76 Owner decision resolution review is preview-only."],
            "dry_run_token": None,
            "safety_notice": "Owner decision resolution review only. No files are written and no decisions are persisted.",
            "errors": [],
        }
    details = {
        "request_id": request_id,
        "decision": decision,
        "decision_type": decision_type,
        "decision_reason": decision_reason,
        "resolution_scope": "review_only",
    }
    review = _forum_event_review_route(
        {
            "orchestration_id": orchestration_id,
            "event_type": "owner_decision_recorded",
            "severity": "info",
            "actor": actor,
            "source": "web_board_owner_decision_resolution_review",
            "forum_thread_ref": forum_thread_ref,
            "related_task_id": related_task_id,
            "related_iteration_id": related_iteration_id,
            "summary": f"Owner decision {decision} for {request_id}: {decision_reason}",
            "details": json.dumps(details, ensure_ascii=False, sort_keys=True),
            "refs": [forum_thread_ref, evidence_ref, f"owner_decision:{request_id}"],
        },
        repo_root=repo_root,
    )
    data = dict(review.get("data") or {})
    data.update(
        {
            "resolution_request": {
                "request_id": request_id,
                "decision": decision,
                "decision_type": decision_type,
                "decision_reason": decision_reason,
                "evidence_ref": evidence_ref,
            },
            "resolution_review_only": True,
            "decision_persistence_enabled": False,
            "controlled_mutation_allowed": False,
            "writes_enabled": False,
        }
    )
    return {
        **review,
        "operation": "owner_decision_resolution_review",
        "data": data,
        "summary": {
            **dict(review.get("summary") or {}),
            "request_id": request_id,
            "decision": decision,
            "decision_type": decision_type,
            "writer_review_passed": review.get("verdict") == "PASS",
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-76 previews Owner decision resolution only; persistence remains a future controlled gate."],
        "dry_run_token": None,
        "safety_notice": "Owner decision resolution review only. No files are written, no forum backend is posted, and no decision is persisted.",
    }


def _planner_draft_publish_dry_run_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    path = str(payload.get("path") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    if not actor:
        return _execute_error("planner_draft_publish", "actor is required")
    if not path:
        return _execute_error("planner_draft_publish", "path is required")

    review = _planner_draft_review_route({"path": path, "actor": actor}, repo_root=repo_root)
    review_data = dict(review.get("data") or {})
    review_summary = dict(review.get("summary") or {})
    if review.get("verdict") != "PASS" or not review_summary.get("publish_eligible"):
        return {
            "ok": False,
            "verdict": "NEEDS_OWNER" if review.get("verdict") == "NEEDS_OWNER" else "BLOCK",
            "operation": "planner_draft_publish",
            "dry_run": True,
            "actor": {"actor": actor},
            "data": {
                "path": path,
                "planner_review": review_data,
                "owner_decision_gate": {"clear": False, "decision_requests": []},
                "writes_enabled": False,
                "controlled_execute_operation": "draft_publish",
            },
            "summary": {"path": path, "publish_eligible": False, "owner_gate_clear": False},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": list(review.get("warnings", [])),
            "blocking_reasons": list(review.get("blocking_reasons", [])),
            "needs_owner_reasons": list(review.get("needs_owner_reasons", [])),
            "owner_confirmation_required": bool(review.get("needs_owner_reasons")),
            "owner_confirmation_reasons": list(review.get("needs_owner_reasons", [])),
            "execute_allowed": False,
            "execute_blocking_reasons": ["Planner draft is not approved for controlled publish."],
            "dry_run_token": None,
            "safety_notice": "Planner draft publish wrapper did not create a dry-run token because preconditions failed.",
            "errors": [],
        }

    metadata = dict(review_data.get("frontmatter") or {})
    task_id = str(review_summary.get("task_id") or metadata.get("task_id") or "").strip()
    orchestration_id = str(metadata.get("orchestration_id") or "").strip()
    owner_gate = _get_owner_decisions_review_route({}, repo_root=repo_root)
    related_requests = []
    for request in owner_gate.get("data", {}).get("decision_requests", []) or []:
        request_task = str(request.get("related_task_id") or "").strip()
        request_orch = str(request.get("related_orchestration_id") or "").strip()
        if (task_id and request_task == task_id) or (orchestration_id and request_orch == orchestration_id):
            related_requests.append(request)
    if related_requests:
        reasons = [str(item.get("summary") or item.get("title") or item.get("request_id")) for item in related_requests]
        return {
            "ok": False,
            "verdict": "NEEDS_OWNER",
            "operation": "planner_draft_publish",
            "dry_run": True,
            "actor": {"actor": actor},
            "data": {
                "path": path,
                "planner_review": review_data,
                "owner_decision_gate": {"clear": False, "decision_requests": related_requests},
                "writes_enabled": False,
                "controlled_execute_operation": "draft_publish",
            },
            "summary": {"path": path, "task_id": task_id, "publish_eligible": False, "owner_gate_clear": False},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": list(owner_gate.get("warnings", [])),
            "blocking_reasons": [],
            "needs_owner_reasons": reasons,
            "owner_confirmation_required": True,
            "owner_confirmation_reasons": reasons,
            "execute_allowed": False,
            "execute_blocking_reasons": ["Related Owner decision gate is still open."],
            "dry_run_token": None,
            "safety_notice": "Planner draft publish wrapper blocked publish because an Owner decision gate is open.",
            "errors": [],
        }

    dry_run = publish_draft(path=path, dry_run=True, repo_root=repo_root, actor=actor)
    dry_data = dict(dry_run.get("data") or {})
    dry_data.update(
        {
            "planner_review_summary": review_summary,
            "owner_decision_gate": {"clear": True, "decision_requests": []},
            "controlled_execute_operation": "draft_publish",
            "second_confirmation_required": True,
        }
    )
    dry_run["operation"] = "planner_draft_publish"
    dry_run["data"] = dry_data
    dry_run["summary"] = {
        **dict(dry_run.get("summary") or {}),
        "task_id": task_id,
        "path": path,
        "publish_eligible": True,
        "owner_gate_clear": True,
        "second_confirmation_required": True,
    }
    dry_run["execute_blocking_reasons"] = list(dry_run.get("execute_blocking_reasons", []))
    dry_run["safety_notice"] = "Planner draft publish uses existing controlled draft_publish dry-run token and still requires explicit confirmation."
    return dry_run


def _planner_draft_review_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    path = str(payload.get("path") or "").strip()
    actor = str(payload.get("actor") or "").strip() or None
    if not path:
        return _planner_draft_error("path is required")
    resolved_root = (repo_root or REPO_ROOT).resolve()
    try:
        validation = validate_draft_file(resolved_root, path)
        publish_preview = backend_publish_draft(resolved_root, path, dry_run=True)
    except Exception as exc:
        return _planner_draft_error(str(exc) or exc.__class__.__name__)

    metadata = dict(validation.get("frontmatter") or {})
    blocking_reasons = list(validation.get("blocking_reasons", []))
    warnings = list(validation.get("warnings", []))
    needs_owner_reasons: list[str] = []
    preconditions: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str, severity: str = "block") -> None:
        preconditions.append({"name": name, "passed": passed, "severity": severity, "detail": detail})
        if passed:
            return
        if severity == "needs_owner":
            if detail not in needs_owner_reasons:
                needs_owner_reasons.append(detail)
        elif severity == "warn":
            if detail not in warnings:
                warnings.append(detail)
        elif detail not in blocking_reasons:
            blocking_reasons.append(detail)

    planner_created = (
        str(metadata.get("draft_source") or "").strip() == "planner"
        or _as_bool(metadata.get("created_by_planner"))
        or "/planner/" in path
    )
    add_check(
        "planner_created_draft",
        planner_created,
        "Draft must be marked as planner-created for Planner Draft Review.",
    )

    missing_fields = [field for field in PLANNER_DRAFT_REQUIRED_FIELDS if _is_missing_metadata(metadata, field)]
    add_check(
        "required_planner_metadata",
        not missing_fields,
        "Missing planner draft metadata: " + ", ".join(missing_fields),
    )

    planner_tier = str(metadata.get("planner_model_tier") or "").strip().upper()
    add_check(
        "planner_model_tier",
        planner_tier in {"L3", "L4"},
        "planner_model_tier must be L3 or L4.",
    )

    publish_status = str(metadata.get("publish_status") or "").strip()
    draft_status = str(metadata.get("draft_status") or "").strip()
    add_check(
        "publish_status_approved",
        publish_status == "approved_for_publish",
        "publish_status must be approved_for_publish before planner draft publish.",
        severity="needs_owner",
    )
    add_check(
        "draft_not_rejected_or_superseded",
        draft_status not in {"rejected", "superseded", "blocked"} and publish_status not in {"rejected", "superseded", "blocked"},
        "Draft is rejected, superseded, or blocked.",
    )
    add_check(
        "owner_gate_clear",
        draft_status != "needs_owner" and publish_status != "needs_owner" and not _as_bool(metadata.get("needs_owner")),
        "Draft has a pending Owner decision gate.",
        severity="needs_owner",
    )

    planner_agent = str(metadata.get("planner_agent") or "").strip()
    reviewer = str(metadata.get("reviewer") or "").strip()
    audit_by = str(metadata.get("audit_by") or "").strip()
    add_check("reviewer_explicit", bool(reviewer), "reviewer is required for planner-created drafts.")
    add_check("audit_by_explicit", bool(audit_by), "audit_by is required for planner-created drafts.")
    add_check(
        "planner_not_reviewer",
        bool(planner_agent and reviewer and planner_agent != reviewer),
        "planner_agent must not review its own planned work.",
    )
    add_check(
        "planner_not_auditor",
        bool(planner_agent and audit_by and planner_agent != audit_by),
        "planner_agent must not audit its own planned work.",
    )
    add_check(
        "publish_target_pending_queue",
        str(metadata.get("publish_target") or metadata.get("draft_publish_target") or "").strip() == "5_tasks/queue/pending/",
        "publish target must be 5_tasks/queue/pending/.",
    )

    publish_blocking = list(publish_preview.get("blocking_reasons", []))
    publish_warnings = list(publish_preview.get("warnings", []))
    classification_warnings = list(validation.get("classification_warnings", [])) + list(
        publish_preview.get("classification_warnings", [])
    )
    add_check(
        "controlled_publish_dry_run_compatible",
        str(publish_preview.get("verdict")) != "BLOCK",
        "Existing draft_publish dry-run blocks this draft: " + "; ".join(publish_blocking),
    )
    for warning in publish_warnings:
        if warning not in warnings:
            warnings.append(warning)

    publish_eligible = not blocking_reasons and not needs_owner_reasons
    verdict_warnings = [warning for warning in warnings if warning not in classification_warnings]
    verdict = "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if needs_owner_reasons else ("WARN" if verdict_warnings else "PASS"))
    return {
        "ok": verdict != "BLOCK",
        "verdict": verdict,
        "operation": "planner_draft_review",
        "dry_run": True,
        "actor": {"actor": actor} if actor else None,
        "data": {
            "path": validation.get("path"),
            "task_id": validation.get("task_id"),
            "frontmatter": metadata,
            "planner_created": planner_created,
            "preconditions": preconditions,
            "publish_preview": {
                "operation": "draft_publish",
                "source_path": publish_preview.get("source_path"),
                "target_path": publish_preview.get("target_path"),
                "task_id": publish_preview.get("task_id"),
                "verdict": publish_preview.get("verdict"),
                "would_write": publish_preview.get("would_write", False),
                "planned_writes": publish_preview.get("planned_writes", []),
                "blocking_reasons": publish_blocking,
                "warnings": publish_warnings,
                "classification_warnings": classification_warnings,
            },
            "handoff_to_draft_publish": {
                "enabled": publish_eligible,
                "path": validation.get("path"),
                "next_operation": "draft_publish",
                "next_action": "Run the existing Draft Publish dry-run, then confirm through the controlled execute path.",
            },
            "writes_enabled": False,
            "review_only": True,
            "controlled_execute_expanded": False,
        },
        "summary": {
            "task_id": validation.get("task_id"),
            "path": validation.get("path"),
            "publish_eligible": publish_eligible,
            "preconditions_total": len(preconditions),
            "preconditions_passed": sum(1 for item in preconditions if item.get("passed")),
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": needs_owner_reasons,
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-61 planner draft review is review-only; publish must use existing draft_publish dry-run and confirm."],
        "safety_notice": "Local planner draft review route. No files are written.",
        "errors": [],
    }


def _planner_tick_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    del repo_root
    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    parent_task_id = str(payload.get("parent_task_id") or "").strip()
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    planner_agent = str(payload.get("planner_agent") or "planner_agent").strip() or "planner_agent"
    planner_agent_instance = str(payload.get("planner_agent_instance") or "planner.local.001").strip() or "planner.local.001"
    planner_model_tier = str(payload.get("planner_model_tier") or "L3").strip().upper()
    decision = str(payload.get("decision") or "").strip()
    decision_reason = str(payload.get("decision_reason") or "").strip()
    next_expected_action = str(payload.get("next_expected_action") or "").strip()
    combined_planner_executor = bool(payload.get("combined_planner_executor", False))
    try:
        iteration_number = int(str(payload.get("iteration_number") or "1").strip())
    except ValueError:
        return _planner_tick_error("iteration_number must be an integer")
    if not orchestration_id:
        return _planner_tick_error("orchestration_id is required")
    if not parent_task_id:
        return _planner_tick_error("parent_task_id is required")
    if not forum_thread_ref:
        return _planner_tick_error("forum_thread_ref is required")
    if planner_model_tier not in {"L3", "L4"}:
        return _planner_tick_error("planner_model_tier must be L3 or L4")
    if iteration_number < 1:
        return _planner_tick_error("iteration_number must be greater than zero")
    if decision not in PLANNER_TICK_VERDICTS:
        return _planner_tick_error("decision must be an AIPOS-54 planner tick verdict")
    if not decision_reason:
        return _planner_tick_error("decision_reason is required")
    if not next_expected_action:
        return _planner_tick_error("next_expected_action is required")

    timestamp = _utc_now()
    iteration_id = f"iter_{timestamp[:10].replace('-', '')}_{_slug(orchestration_id)}_{iteration_number:03d}"
    inputs_read = _list_from_payload(payload, "inputs_read")
    observations = _list_from_payload(payload, "observations")
    needs_owner_reasons = _list_from_payload(payload, "needs_owner_reasons")
    publish_candidates = _list_from_payload(payload, "publish_candidates")
    repair_recommendations = _list_from_payload(payload, "repair_recommendations")
    stop_condition_hits = _list_from_payload(payload, "stop_condition_hits")
    subtask_drafts_proposed = _list_from_payload(payload, "subtask_drafts_proposed")
    audit_handoff_needed = decision == "wait_for_audit" or bool(payload.get("audit_handoff_needed", False))
    owner_decision_required = decision == "needs_owner" or bool(needs_owner_reasons)
    severity = "needs_owner" if owner_decision_required else "blocking" if decision in {"blocked", "failed"} else "info"
    planner_iteration = {
        "iteration_id": iteration_id,
        "orchestration_id": orchestration_id,
        "iteration_number": iteration_number,
        "planner_agent": planner_agent,
        "planner_agent_instance": planner_agent_instance,
        "planner_model_tier": planner_model_tier,
        "started_at": timestamp,
        "ended_at": timestamp,
        "input_refs": inputs_read,
        "observed_queue_state": str(payload.get("observed_queue_state") or "not_observed_in_preview").strip(),
        "observed_subtask_summary": observations,
        "decisions": [
            {
                "decision": decision,
                "reason": decision_reason,
                "owner_decision_required": owner_decision_required,
            }
        ],
        "created_subtasks": subtask_drafts_proposed,
        "updated_recommendations": repair_recommendations,
        "failure_observations": _list_from_payload(payload, "failure_observations"),
        "quota_observations": _list_from_payload(payload, "quota_observations"),
        "needs_owner_reasons": needs_owner_reasons,
        "next_check_after": str(payload.get("next_check_after") or "").strip() or None,
        "verdict": decision,
    }
    visible_report = {
        "planner_iteration_id": iteration_id,
        "orchestration_id": orchestration_id,
        "parent_task_id": parent_task_id,
        "planner_agent": planner_agent,
        "planner_agent_instance": planner_agent_instance,
        "planner_model_tier": planner_model_tier,
        "combined_planner_executor": combined_planner_executor,
        "forum_thread_ref": forum_thread_ref,
        "inputs_read": inputs_read,
        "observations": observations,
        "decision": decision,
        "decision_reason": decision_reason,
        "owner_decision_required": owner_decision_required,
        "needs_owner_reasons": needs_owner_reasons,
        "subtask_drafts_proposed": subtask_drafts_proposed,
        "publish_candidates": publish_candidates,
        "repair_recommendations": repair_recommendations,
        "audit_handoff_needed": audit_handoff_needed,
        "next_expected_action": next_expected_action,
        "stop_condition_hits": stop_condition_hits,
    }
    event_log_preview = [
        {
            "event_id": f"evt_{iteration_id}_started",
            "orchestration_id": orchestration_id,
            "event_type": "planner_tick_started",
            "timestamp": timestamp,
            "actor": planner_agent_instance,
            "source": "web_board_planner_tick_preview",
            "related_task_id": parent_task_id,
            "related_subtask_id": None,
            "related_iteration_id": iteration_id,
            "severity": "info",
            "summary": "Planner tick preview started.",
            "details": {"planner_model_tier": planner_model_tier},
            "refs": [forum_thread_ref],
        },
        {
            "event_id": f"evt_{iteration_id}_verdict",
            "orchestration_id": orchestration_id,
            "event_type": "planner_verdict_recorded",
            "timestamp": timestamp,
            "actor": planner_agent_instance,
            "source": "web_board_planner_tick_preview",
            "related_task_id": parent_task_id,
            "related_subtask_id": None,
            "related_iteration_id": iteration_id,
            "severity": severity,
            "summary": f"Planner tick preview verdict: {decision}.",
            "details": {"decision_reason": decision_reason, "next_expected_action": next_expected_action},
            "refs": [forum_thread_ref],
        },
        {
            "event_id": f"evt_{iteration_id}_completed",
            "orchestration_id": orchestration_id,
            "event_type": "planner_tick_completed",
            "timestamp": timestamp,
            "actor": planner_agent_instance,
            "source": "web_board_planner_tick_preview",
            "related_task_id": parent_task_id,
            "related_subtask_id": None,
            "related_iteration_id": iteration_id,
            "severity": severity,
            "summary": "Planner tick preview completed.",
            "details": {"owner_decision_required": owner_decision_required},
            "refs": [forum_thread_ref],
        },
    ]
    if owner_decision_required:
        event_log_preview.append(
            {
                "event_id": f"evt_{iteration_id}_needs_owner",
                "orchestration_id": orchestration_id,
                "event_type": "needs_owner_raised",
                "timestamp": timestamp,
                "actor": planner_agent_instance,
                "source": "web_board_planner_tick_preview",
                "related_task_id": parent_task_id,
                "related_subtask_id": None,
                "related_iteration_id": iteration_id,
                "severity": "needs_owner",
                "summary": "Planner tick preview requires Owner decision.",
                "details": {"needs_owner_reasons": needs_owner_reasons},
                "refs": [forum_thread_ref],
            }
        )
    return {
        "ok": True,
        "verdict": "NEEDS_OWNER" if owner_decision_required else "PASS",
        "operation": "planner_tick_preview",
        "dry_run": True,
        "data": {
            "planner_iteration": planner_iteration,
            "visible_report": visible_report,
            "event_log_preview": event_log_preview,
            "writes_enabled": False,
            "forum_backend_enabled": False,
            "planner_runtime_launch_enabled": False,
            "orchestration_writer_enabled": False,
        },
        "summary": {
            "orchestration_id": orchestration_id,
            "planner_iteration_id": iteration_id,
            "decision": decision,
            "owner_decision_required": owner_decision_required,
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": [
            "Preview only. AIPOS-60 does not write planner iterations, orchestration events, forum events, task cards, drafts, queue files, records, or memory."
        ],
        "blocking_reasons": [],
        "needs_owner_reasons": needs_owner_reasons,
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-60 planner tick/event log UI is preview-only"],
        "safety_notice": "Local planner tick preview route. No files are written.",
        "errors": [],
    }



CRITICAL_OWNER_FORK_KEYWORDS = {
    "architecture": ["architecture", "route", "design", "service", "database", "deployment"],
    "scope": ["scope", "expand", "expansion", "requirement"],
    "risk": ["risk", "high-risk", "irreversible", "data loss", "refactor"],
    "authority": ["authority", "permission", "agent authority", "model tier"],
    "security": ["security", "credential", "secret", "auth", "rbac"],
    "audit_boundary": ["audit", "reviewer", "auditor", "self-audit"],
}


def _critical_fork_hits(payload: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(payload.get(field) or "")
        for field in [
            "decision",
            "decision_reason",
            "observations",
            "needs_owner_reasons",
            "publish_candidates",
            "repair_recommendations",
            "stop_condition_hits",
            "next_expected_action",
        ]
    ).lower()
    hits: list[str] = []
    for fork_type, keywords in CRITICAL_OWNER_FORK_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            hits.append(fork_type)
    return hits


def _planner_tick_manual_flow_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    preview = _planner_tick_preview_route(payload, repo_root=repo_root)
    if preview.get("verdict") == "BLOCK":
        preview["operation"] = "planner_tick_manual_flow_preview"
        return preview

    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    summary = get_orchestration_summary_preview(orchestration_id=orchestration_id, repo_root=resolved_root) if orchestration_id else {}
    timeline = get_orchestration_timeline_preview(orchestration_id=orchestration_id, repo_root=resolved_root) if orchestration_id else {}
    owner_gate = _get_owner_decisions_review_route({}, repo_root=repo_root)
    related_owner_requests = [
        request
        for request in owner_gate.get("data", {}).get("decision_requests", []) or []
        if str(request.get("related_orchestration_id") or "") == orchestration_id
        or str(request.get("related_task_id") or "") == str(payload.get("parent_task_id") or "").strip()
    ]
    fork_hits = _critical_fork_hits(payload)
    decision = str(payload.get("decision") or "").strip()
    needs_owner_reasons = list(preview.get("needs_owner_reasons", []))
    if related_owner_requests:
        needs_owner_reasons.extend(str(item.get("summary") or item.get("title") or item.get("request_id")) for item in related_owner_requests)
    if fork_hits and decision != "needs_owner":
        needs_owner_reasons.extend(f"critical fork requires Owner decision: {fork}" for fork in fork_hits)

    data = dict(preview.get("data") or {})
    visible_report = dict(data.get("visible_report") or {})
    owner_decision_required = bool(needs_owner_reasons) or bool(visible_report.get("owner_decision_required"))
    visible_report["critical_fork_hits"] = fork_hits
    visible_report["related_owner_decision_requests"] = related_owner_requests
    visible_report["owner_decision_required"] = owner_decision_required
    visible_report["manual_flow_next_step"] = "stop_for_owner" if owner_decision_required else visible_report.get("next_expected_action")
    data["visible_report"] = visible_report
    data.update(
        {
            "manual_flow": True,
            "orchestration_summary_snapshot": summary.get("summary") or {},
            "timeline_snapshot": timeline.get("summary") or {},
            "owner_decision_snapshot": {
                "total": owner_gate.get("summary", {}).get("total", 0),
                "related_requests": related_owner_requests,
            },
            "writes_enabled": False,
            "orchestration_writer_enabled": False,
            "planner_iteration_append_enabled": False,
            "forum_event_append_enabled": False,
            "planner_runtime_launch_enabled": False,
            "queue_mutation_enabled": False,
        }
    )
    verdict = "NEEDS_OWNER" if owner_decision_required else preview.get("verdict")
    return {
        **preview,
        "ok": True,
        "verdict": verdict,
        "operation": "planner_tick_manual_flow_preview",
        "data": data,
        "summary": {
            **dict(preview.get("summary") or {}),
            "manual_flow": True,
            "critical_fork_hits": fork_hits,
            "related_owner_decision_requests": len(related_owner_requests),
            "owner_decision_required": owner_decision_required,
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "needs_owner_reasons": list(dict.fromkeys(reason for reason in needs_owner_reasons if reason)),
        "owner_confirmation_required": owner_decision_required,
        "owner_confirmation_reasons": list(dict.fromkeys(reason for reason in needs_owner_reasons if reason)),
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-74 manual planner tick flow is preview-only; no planner iteration or event is persisted."],
        "dry_run_token": None,
        "safety_notice": "Manual planner tick flow preview only. No planner iteration, event, task, queue, draft, record, runtime, forum, or git state is written.",
    }


def _parent_requirement_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    del repo_root
    title = str(payload.get("title") or "").strip()
    owner_goal = str(payload.get("owner_goal") or "").strip()
    project = str(payload.get("project") or "lybra").strip() or "lybra"
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    planner_agent = str(payload.get("planner_agent") or "planner_agent").strip() or "planner_agent"
    planner_agent_instance = str(payload.get("planner_agent_instance") or "planner.local.001").strip() or "planner.local.001"
    planner_runtime_profile = str(payload.get("planner_runtime_profile") or "local_process").strip() or "local_process"
    planner_model_tier = str(payload.get("planner_model_tier") or "L3").strip().upper()
    max_iterations = str(payload.get("max_iterations") or "5").strip()
    timestamp = _utc_now()
    if not title:
        return _parent_requirement_error("title is required")
    if not owner_goal:
        return _parent_requirement_error("owner_goal is required")
    if not forum_thread_ref:
        return _parent_requirement_error("forum_thread_ref is required")
    if planner_model_tier not in {"L3", "L4"}:
        return _parent_requirement_error("planner_model_tier must be L3 or L4")
    requirement_slug = _slug(title)
    date_slug = timestamp[:10].replace("-", "")
    requirement_id = f"REQ-{date_slug}-{requirement_slug}"
    orchestration_id = f"orch_{_slug(project)}_{date_slug}_{requirement_slug}"
    parent_task_id = f"{requirement_id}-PARENT"
    requirement = {
        "requirement_id": requirement_id,
        "title": title,
        "owner_goal": owner_goal,
        "created_by": "Owner",
        "created_at": timestamp,
        "project": project,
        "task_class": "complex",
        "complexity_note": "Parent requirement uses the governed planner closed loop.",
        "intake_status": "received",
        "forum_thread_ref": forum_thread_ref,
        "visibility": "forum_visible",
        "planning_required": True,
        "min_planner_model_tier": "L3",
        "allowed_planner_agents": ["dev_codex", "dev_claude"],
        "assigned_planner": planner_agent,
        "assigned_planner_instance": planner_agent_instance,
        "planner_runtime_profile": planner_runtime_profile,
        "planner_assignment_status": "proposed",
        "orchestration_id": orchestration_id,
        "parent_task_id": parent_task_id,
        "needs_owner": False,
        "needs_owner_reasons": [],
    }
    planner_loop_preview = {
        "loop": "observe -> decide -> emit -> wait",
        "next_expected_action": "Owner reviews preview, then a future approved writer may create the parent requirement record.",
        "max_iterations": max_iterations,
        "stop_conditions": [
            "owner_decision_required",
            "audit_pending",
            "dependency_blocked",
            "max_iterations_reached",
            "scope_or_risk_fork",
        ],
        "owner_decision_required_for": [
            "architecture_route_split",
            "scope_expansion",
            "risk_escalation",
            "new_runtime_or_service",
            "audit_boundary_change",
            "turning_protocol_into_implementation",
        ],
    }
    return {
        "ok": True,
        "verdict": "PASS",
        "operation": "parent_requirement_preview",
        "dry_run": True,
        "data": {
            "parent_requirement": requirement,
            "planner_loop_preview": planner_loop_preview,
            "writes_enabled": False,
            "forum_backend_enabled": False,
            "planner_runtime_launch_enabled": False,
        },
        "summary": {
            "requirement_id": requirement_id,
            "orchestration_id": orchestration_id,
            "planner_model_tier": planner_model_tier,
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": [
            "Preview only. AIPOS-59 does not write parent requirement records, orchestration files, forum events, or task cards."
        ],
        "blocking_reasons": [],
        "needs_owner_reasons": [],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-59 parent requirement entry is preview-only"],
        "safety_notice": "Local parent requirement preview route. No files are written.",
        "errors": [],
    }


def _get_orchestration_summary_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    orchestration_id = _first_param(params, "orchestration_id")
    if not orchestration_id:
        return _selector_error("orchestration_summary_preview", "orchestration_id is required")
    return get_orchestration_summary_preview(orchestration_id=orchestration_id, repo_root=repo_root)


def _get_orchestration_timeline_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    orchestration_id = _first_param(params, "orchestration_id")
    if not orchestration_id:
        return _selector_error("orchestration_timeline_preview", "orchestration_id is required")
    return get_orchestration_timeline_preview(orchestration_id=orchestration_id, repo_root=repo_root)


def _get_planner_loop_mvp_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    orchestration_id = _first_param(params, "orchestration_id")
    actor = _first_param(params, "actor")
    if not orchestration_id:
        return _selector_error("planner_loop_mvp_preview", "orchestration_id is required")
    return get_planner_loop_mvp_preview(orchestration_id=orchestration_id, repo_root=repo_root, actor=actor)


def _get_context_pack_preview_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    task_id = _first_param(params, "task_id")
    path = _first_param(params, "path")
    orchestration_id = _first_param(params, "orchestration_id")
    if sum(bool(value) for value in (task_id, path, orchestration_id)) != 1:
        return _selector_error("context_pack_preview", "Exactly one of task_id, path, or orchestration_id is required")
    return get_context_pack_preview(task_id=task_id, path=path, orchestration_id=orchestration_id, repo_root=repo_root)


def _get_task_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    task_id = _first_param(params, "task_id")
    path = _first_param(params, "path")
    if bool(task_id) == bool(path):
        return _selector_error("get_task", "Exactly one of task_id or path is required")
    return get_task(task_id=task_id, path=path, repo_root=repo_root)


def _get_preview_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    task_id = _first_param(params, "task_id")
    path = _first_param(params, "path")
    actor = _first_param(params, "actor")
    if not actor:
        return _selector_error("get_preview", "actor is required")
    if bool(task_id) == bool(path):
        return _selector_error("get_preview", "Exactly one of task_id or path is required")
    return get_preview(task_id=task_id, path=path, actor=actor, repo_root=repo_root)


def _ai_author_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    fixture_id = str(payload.get("fixture_id") or "").strip()
    intent = payload.get("intent")
    if not actor:
        return _execute_error("ai_assisted_fixture_authoring", "actor is required")
    if not fixture_id:
        return _execute_error("ai_assisted_fixture_authoring", "fixture_id is required")
    if not isinstance(intent, dict):
        return _execute_error("ai_assisted_fixture_authoring", "intent object is required")
    try:
        return build_authoring_draft(Path(repo_root or REPO_ROOT), intent, fixture_id=fixture_id, actor=actor)
    except Exception as exc:
        return _execute_error("ai_assisted_fixture_authoring", str(exc))


def _ai_author_confirm_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    preview = payload.get("preview")
    if not actor:
        return _execute_error("ai_assisted_fixture_authoring", "actor is required")
    if not isinstance(preview, dict):
        return _execute_error("ai_assisted_fixture_authoring", "preview object is required")
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", False)) else None
    try:
        return confirm_authoring_draft(
            Path(repo_root or REPO_ROOT),
            preview,
            actor=actor,
            owner_confirmation_token=owner_token,
        )
    except Exception as exc:
        return _execute_error("ai_assisted_fixture_authoring", str(exc))


def _execute_dry_run_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    operation = str(payload.get("operation") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    if not actor:
        return _execute_error(operation or "execute_dry_run", "actor is required")
    if operation == "draft_create":
        draft_payload = payload.get("payload")
        if not isinstance(draft_payload, dict):
            return _execute_error("draft_create", "payload object is required")
        return create_draft(draft_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "draft_publish":
        path = str(payload.get("path") or "").strip()
        if not path:
            return _execute_error("draft_publish", "path is required")
        return publish_draft(path=path, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "orchestration_event_append":
        event_payload = payload.get("payload")
        if not isinstance(event_payload, dict):
            return _execute_error("orchestration_event_append", "payload object is required")
        return append_orchestration_event(event_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "planner_iteration_append":
        iteration_payload = payload.get("payload")
        if not isinstance(iteration_payload, dict):
            return _execute_error("planner_iteration_append", "payload object is required")
        return append_planner_iteration(iteration_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "owner_decision_record":
        decision_payload = payload.get("payload")
        if not isinstance(decision_payload, dict):
            return _execute_error("owner_decision_record", "payload object is required")
        return record_owner_decision(decision_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation != "queue_claim":
        return _execute_error(
            "execute_dry_run",
            "Only queue_claim, draft_create, draft_publish, orchestration_event_append, planner_iteration_append, and owner_decision_record are enabled in the controlled execute API",
        )
    task_id = str(payload.get("task_id") or "").strip() or None
    path = str(payload.get("path") or "").strip() or None
    if bool(task_id) == bool(path):
        return _execute_error("queue_claim", "Exactly one of task_id or path is required")
    if bool(payload.get("with_records", False)):
        return _execute_error("queue_claim", "with_records execute is not enabled in the AIPOS-55 UI")
    return claim_task(task_id=task_id, path=path, actor=actor, dry_run=True, with_records=False, repo_root=repo_root)


def _execute_confirm_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    dry_run_id = str(payload.get("dry_run_id") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    if not dry_run_id:
        return _execute_error("execute_dry_run", "dry_run_id is required")
    if not actor:
        return _execute_error("execute_dry_run", "actor is required")
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", False)) else None
    return execute_dry_run(dry_run_id, actor, owner_confirmation_token=owner_token, repo_root=repo_root)


def dispatch_api_request(
    *,
    method: str,
    path: str,
    routes: dict[str, Callable[[dict[str, list[str]]], dict[str, Any]]],
    post_routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    clean_path = parsed.path
    params = parse_qs(parsed.query, keep_blank_values=True)
    if method == "POST" and post_routes and clean_path in post_routes:
        return int(HTTPStatus.OK), post_routes[clean_path](body or {})
    if method != "GET":
        return (
            int(HTTPStatus.METHOD_NOT_ALLOWED),
            {
                "ok": False,
                "verdict": "BLOCK",
                "error": "METHOD_NOT_ALLOWED",
                "message": "Read-only API. Only GET is supported.",
            },
        )
    if clean_path in routes:
        return int(HTTPStatus.OK), routes[clean_path](params)
    return (
        int(HTTPStatus.NOT_FOUND),
        {
            "ok": False,
            "verdict": "BLOCK",
            "error": "NOT_FOUND",
            "message": "Route not found",
        },
    )


def make_handler(repo_root: Path | None = None) -> type[BaseHTTPRequestHandler]:
    routes = _api_routes(repo_root)
    post_routes = _api_post_routes(repo_root)

    class BoardHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path) -> None:
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _content_type(path))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._send_json(HTTPStatus.NOT_FOUND, dispatch_api_request(method="GET", path="/missing", routes={})[1])

        def _method_not_allowed(self) -> None:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                dispatch_api_request(method="POST", path="/api/health", routes=routes)[1],
            )

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path in routes:
                _status, result = dispatch_api_request(method="GET", path=self.path, routes=routes)
                self._send_json(HTTPStatus.OK, result)
                return

            if path == "/" or path == "/index.html":
                self._send_file(STATIC_DIR / "index.html")
                return

            static_path = (STATIC_DIR / path.lstrip("/")).resolve()
            if static_path.exists() and static_path.is_file() and STATIC_DIR.resolve() in static_path.parents:
                self._send_file(static_path)
                return

            self._not_found()

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in post_routes:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                _status, result = dispatch_api_request(
                    method="POST",
                    path=self.path,
                    routes=routes,
                    post_routes=post_routes,
                    body=body,
                )
                self._send_json(HTTPStatus.OK, result)
                return
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def log_message(self, format: str, *args: object) -> None:
            return

    return BoardHandler


def run_server(host: str = "127.0.0.1", port: int = 8765, repo_root: Path | None = None) -> None:
    handler = make_handler(repo_root=repo_root)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"AIPOS board local UI listening on http://{host}:{port}")
        httpd.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local read-only board UI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repo-root", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root_arg = args.repo_root or os.environ.get("AIPOS_WORKSPACE_ROOT")
    repo_root = Path(repo_root_arg).expanduser().resolve() if repo_root_arg else None
    run_server(host=str(args.host), port=int(args.port), repo_root=repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
