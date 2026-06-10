from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, error_entry, make_response
from tools.aipos_cli.agent_profiles import actor_matches_task_actor, load_agent_profiles, resolve_instance_id
from tools.aipos_cli.context_pack_builder import build_context_pack_preview
from tools.aipos_cli.controlled_execute import (
    OWNER_CONFIRMATION_TOKEN,
    get_dry_run,
    is_expired,
    register_dry_run,
    snapshot_hash,
    validate_owner_confirmation,
)
from tools.aipos_cli.draft_validator import list_drafts, validate_draft_file
from tools.aipos_cli.draft_writer import create_draft as backend_create_draft
from tools.aipos_cli.draft_writer import default_draft_body, publish_draft as backend_publish_draft
from tools.aipos_cli.external_intake_writer import build_external_intake_draft as backend_build_external_intake_draft
from tools.aipos_cli.orchestration_event_writer import append_orchestration_event as backend_append_orchestration_event
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.orchestration_timeline_preview import build_orchestration_timeline_preview
from tools.aipos_cli.owner_decision_writer import build_owner_decision_record as backend_build_owner_decision_record
from tools.aipos_cli.planner_iteration_writer import append_planner_iteration as backend_append_planner_iteration
from tools.aipos_cli.planner_loop_mvp import build_planner_loop_mvp_preview
from tools.aipos_cli.preview import build_preview
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.queue_mutation import mutate_queue_task, render_task_markdown
from tools.aipos_cli.record_writer import (
    append_mcp_audit_verdict_session_event,
    append_mcp_return_session_event,
    audit_dispatch_record_path,
    audit_verdict_record_path,
    build_mcp_audit_dispatch_record_markdown,
    build_mcp_audit_verdict_record_markdown,
    build_mcp_claim_record_markdown,
    build_mcp_claim_session_record_markdown,
    build_mcp_return_record_markdown,
    build_runtime_id,
    claim_record_paths,
    load_session_record,
    return_record_path,
    session_record_path,
)
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import find_repo_root, find_task_by_id, load_all_tasks, load_task_by_path
from tools.aipos_cli.validator import validate_single_task, validate_tasks
from tools.aipos_cli.workspace_templates import (
    TEMPLATE_OPERATION,
    build_workspace_init_plan,
    execute_workspace_init,
)

READ_SAFETY_NOTICE = "Read-only local Board adapter call. No files are written."
MUTATION_DRY_RUN_NOTICE = (
    "AIPOS-36 local Board adapter supports dry-run mutation previews only. "
    "Execute mutations remain blocked until dry-run token and revalidation contract are implemented."
)
CONTROLLED_EXECUTE_NOTICE = (
    "AIPOS-38 controlled execute is local-only and limited to dry-run-linked operations: "
    "draft_create, draft_publish, queue_claim, orchestration_event_append, planner_iteration_append, intake_submit, "
    "owner_decision_record, queue_return, audit_dispatch, audit_verdict."
)
HEALTH_NOTICE = "Local module adapter health check only. No CLI runtime bridge, server, or network behavior is used."
GOVERNANCE_FILES = {
    "decision_log": "2_projects/lybra/decision_log.md",
    "project_status": "2_projects/lybra/project_status.md",
    "roadmap": "2_projects/lybra/roadmap.md",
}
GOVERNANCE_EXCERPT_CHARS = 12000


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    candidate = Path(repo_root).resolve() if repo_root is not None else None
    return find_repo_root(candidate)


def _actor_payload(actor: str | None) -> dict[str, Any] | None:
    if not str(actor or "").strip():
        return None
    return {"actor": str(actor)}


def _normalize_path(path: str | Path | None, *, field: str = "path") -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        raise ValueError(f"{field} is required")
    raw = Path(text)
    if raw.is_absolute():
        raise ValueError(f"{field} must be repo-relative")
    if ".." in raw.parts:
        raise ValueError(f"{field} must not contain path traversal")
    return text


def _target_file_state(repo_root: Path, target_path: Any) -> dict[str, Any]:
    text = str(target_path or "").strip()
    if not text:
        return {"path": None, "exists": False, "sha256": None}
    normalized = _normalize_path(text, field="target_path")
    path = repo_root / str(normalized)
    if not path.exists():
        return {"path": normalized, "exists": False, "sha256": None}
    if not path.is_file():
        return {"path": normalized, "exists": True, "sha256": None, "file": False}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": normalized, "exists": True, "sha256": digest, "file": True}


def _governance_doc(repo_root: Path, name: str, rel_path: str) -> dict[str, Any]:
    path = repo_root / rel_path
    doc: dict[str, Any] = {
        "name": name,
        "path": rel_path,
        "exists": path.exists(),
        "is_file": path.is_file() if path.exists() else False,
        "byte_size": None,
        "line_count": None,
        "excerpt": "",
        "truncated": False,
    }
    if not path.exists() or not path.is_file():
        return doc
    text = path.read_text(encoding="utf-8", errors="replace")
    doc["byte_size"] = len(text.encode("utf-8"))
    doc["line_count"] = len(text.splitlines())
    doc["truncated"] = len(text) > GOVERNANCE_EXCERPT_CHARS
    doc["excerpt"] = text[-GOVERNANCE_EXCERPT_CHARS:] if doc["truncated"] else text
    return doc


def _select_task_input(task_id: str | None, path: str | Path | None) -> tuple[str | None, str | None]:
    normalized_path = _normalize_path(path) if path is not None else None
    if bool(task_id) == bool(normalized_path):
        raise ValueError("Exactly one of task_id or path must be provided")
    return task_id, normalized_path


def _load_validated_task(
    *,
    repo_root: Path,
    task_id: str | None,
    path: str | None,
    actor: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    tasks = load_all_tasks(repo_root)
    records = load_records(repo_root)
    profiles = load_agent_profiles(repo_root)
    if task_id:
        selected, matches = find_task_by_id(task_id, repo_root)
        if not matches:
            raise FileNotFoundError(f"No task found for task_id: {task_id}")
        if len(matches) > 1:
            paths = ", ".join(sorted(str(match.get("path")) for match in matches))
            raise ValueError(f"Duplicate task_id {task_id} found in: {paths}")
        assert selected is not None
        task = selected
    else:
        assert path is not None
        task = load_task_by_path(path, repo_root)
    validated = validate_single_task(task, tasks=tasks, current_actor=actor, records=records, profiles=profiles)
    return validated, tasks, records, profiles, task


def _normalize_exception(operation: str, exc: Exception, *, dry_run: bool, actor: Any = None) -> dict[str, Any]:
    message = str(exc) or exc.__class__.__name__
    category = "INTERNAL_ERROR"
    field: str | None = None
    lowered = message.lower()

    if isinstance(exc, FileNotFoundError):
        category = "NOT_FOUND"
    elif isinstance(exc, ValueError):
        category = "VALIDATION_ERROR"
        if "duplicate task_id" in lowered:
            category = "DUPLICATE_ID"
        elif "repo-relative" in lowered or "outside " in lowered or "path traversal" in lowered:
            category = "PATH_UNSAFE"
            field = "path"
        elif "current actor does not match" in lowered:
            category = "ACTOR_MISMATCH"
            field = "actor"
        elif "source state" in lowered or "directory/status mismatch" in lowered:
            category = "STATUS_MISMATCH"
        elif "dry-run" in lowered:
            category = "DRY_RUN_REQUIRED"
        elif "unsupported" in lowered:
            category = "UNSUPPORTED_OPERATION"
    elif isinstance(exc, KeyError | TypeError):
        category = "BACKEND_CONTRACT_MISMATCH"

    return make_response(
        ok=False,
        verdict="BLOCK",
        operation=operation,
        dry_run=dry_run,
        actor=actor,
        data=None,
        summary=None,
        warnings=[],
        blocking_reasons=[message],
        needs_owner_reasons=[],
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
        safety_notice=READ_SAFETY_NOTICE if dry_run is False and operation.startswith("get_") else MUTATION_DRY_RUN_NOTICE,
        errors=[error_entry(category, message, field=field)],
    )


def _response_from_validated_report(
    *,
    operation: str,
    report: dict[str, Any],
    dry_run: bool = False,
    actor: Any = None,
    actor_match: Any = None,
    safety_notice: str = READ_SAFETY_NOTICE,
) -> dict[str, Any]:
    summary = report.get("summary")
    warnings = list(report.get("warnings", []))
    blocking_reasons = list(report.get("blocking_reasons", []))
    needs_owner_reasons = list(report.get("needs_owner_reasons", []))
    verdict = report.get("verdict") or derive_verdict(
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        needs_owner_reasons=needs_owner_reasons,
    )
    return make_response(
        ok=True,
        verdict=verdict,
        operation=operation,
        dry_run=dry_run,
        actor=actor,
        actor_match=actor_match if actor_match is not None else report.get("actor_match"),
        data=report,
        summary=summary,
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=needs_owner_reasons,
        owner_confirmation_required=bool(report.get("owner_confirmation_required", False)),
        owner_confirmation_reasons=list(report.get("owner_confirmation_reasons", [])),
        safety_notice=safety_notice,
        errors=[],
    )


def _blocked_execute(operation: str, *, actor: str | None = None) -> dict[str, Any]:
    return blocked_response(
        operation=operation,
        dry_run=False,
        category="DRY_RUN_REQUIRED",
        message="AIPOS-36 execute mutations are blocked. Use dry_run=True for preview only.",
        actor=_actor_payload(actor),
        safety_notice=MUTATION_DRY_RUN_NOTICE,
    )


def _attach_controlled_execute_metadata(
    *,
    operation: str,
    actor: str | None,
    response: dict[str, Any],
    execute_allowed: bool,
) -> dict[str, Any]:
    actor_text = str(actor or "").strip()
    if not actor_text:
        response["execute_allowed"] = False
        response["execute_blocking_reasons"] = ["actor is required for controlled execute dry-run token"]
        return response
    if operation not in {
        "draft_create",
        "draft_publish",
        "queue_claim",
        "queue_return",
        "audit_dispatch",
        "audit_verdict",
        "orchestration_event_append",
        "planner_iteration_append",
        "intake_submit",
        "owner_decision_record",
        TEMPLATE_OPERATION,
    }:
        response["execute_allowed"] = False
        response["execute_blocking_reasons"] = ["operation is not enabled for controlled execute"]
        return response

    response["operation"] = operation
    response["actor"] = {"actor": actor_text}
    response["execute_allowed"] = execute_allowed
    response["execute_blocking_reasons"] = list(response.get("blocking_reasons", []))
    if not execute_allowed:
        return response

    token_meta = register_dry_run(operation=operation, actor=actor_text, plan=response)
    response.update(token_meta)
    response["dry_run_token"] = token_meta["dry_run_id"]
    return response


def get_health(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "health_check"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        data = {
            "adapter_mode": "module",
            "repo_root": str(resolved_root),
            "available_backend_modules": {
                "task_loader": True,
                "validator": True,
                "preview": True,
                "records": True,
                "agent_profiles": True,
                "draft_writer": True,
                "draft_validator": True,
                "queue_mutation": True,
                "orchestration_timeline_preview": True,
                "orchestration_summary_preview": True,
                "planner_loop_mvp": True,
                "context_pack_builder": True,
            },
            "capabilities": {
                "read_operations": True,
                "mutation_dry_run_operations": True,
                "mutation_execute_operations": True,
                "cli_runtime_bridge_required": False,
                "server_mode": False,
                "network_required": False,
            },
            "remote_dogfood_readiness": {
                "aipos_86_boundary": "read_only_report_oriented",
                "live_agent_connection_enabled": False,
                "autonomous_runtime_enabled": False,
                "queue_polling_enabled": False,
                "public_endpoint_required": False,
                "read_paths": [
                    "/api/health",
                    "/api/governance",
                    "/api/queue",
                    "/api/agents",
                    "/api/records",
                    "/api/drafts",
                    "/api/external-intake/review",
                    "/api/owner-decision-records",
                    "/api/orchestration/index",
                    "/api/orchestration/summary",
                    "/api/orchestration/timeline",
                    "/api/context-pack/preview",
                ],
                "legacy_read_aliases": [
                    "/api/orchestration-summary",
                    "/api/orchestration-timeline",
                ],
            },
            "paths": {
                "queue_root_found": (resolved_root / "5_tasks" / "queue").exists(),
                "records_root_found": (resolved_root / "5_tasks" / "records").exists(),
                "drafts_root_found": (resolved_root / "5_tasks" / "drafts").exists(),
            },
        }
        return make_response(
            ok=True,
            verdict="PASS",
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "adapter_mode": "module",
                "mutation_execute_operations": True,
                "remote_dogfood_readiness": "read_only_report_oriented",
            },
            safety_notice=HEALTH_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_queue(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_queue"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        tasks = load_all_tasks(resolved_root)
        records = load_records(resolved_root)
        profiles = load_agent_profiles(resolved_root)
        report = validate_tasks(tasks, records=records, profiles=profiles)
        return _response_from_validated_report(operation=operation, report=report)
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_needs_owner(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_needs_owner"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        tasks = load_all_tasks(resolved_root)
        records = load_records(resolved_root)
        profiles = load_agent_profiles(resolved_root)
        report = validate_tasks(tasks, records=records, profiles=profiles)
        filtered = [
            task
            for task in report["tasks"]
            if task.get("verdict") == "NEEDS_OWNER"
            or task.get("metadata", {}).get("needs_owner") is True
            or task.get("metadata", {}).get("owner_review_required") is True
            or task.get("metadata", {}).get("approval_required") is True
            or bool(task.get("needs_owner_reasons"))
        ]
        payload = {
            "scope": "needs_owner",
            "summary": {
                "total_tasks": len(filtered),
                "needs_owner": len(filtered),
            },
            "tasks": filtered,
        }
        return _response_from_validated_report(operation=operation, report=payload)
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_validate(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_validate"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        tasks = load_all_tasks(resolved_root)
        records = load_records(resolved_root)
        profiles = load_agent_profiles(resolved_root)
        report = validate_tasks(tasks, records=records, profiles=profiles)
        return _response_from_validated_report(operation=operation, report=report)
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_records(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_records"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        report = load_records(resolved_root)
        return _response_from_validated_report(operation=operation, report=report)
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_agents(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_agents"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        report = load_agent_profiles(resolved_root)
        return _response_from_validated_report(operation=operation, report=report)
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_governance(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_governance"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        documents = [_governance_doc(resolved_root, name, rel_path) for name, rel_path in GOVERNANCE_FILES.items()]
        missing = [doc["path"] for doc in documents if not doc["exists"] or not doc["is_file"]]
        data = {
            "project": "lybra",
            "project_root": "2_projects/lybra",
            "documents": documents,
            "writes_enabled": False,
            "raw_json_default_visible": False,
        }
        return make_response(
            ok=True,
            verdict="WARN" if missing else "PASS",
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "project": "lybra",
                "documents_total": len(documents),
                "documents_present": len(documents) - len(missing),
                "documents_missing": len(missing),
                "missing": missing,
            },
            warnings=[f"Missing governance file: {path}" for path in missing],
            blocking_reasons=[],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_orchestration_index(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_orchestration_index"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        orchestration_root = resolved_root / "5_tasks" / "orchestration"
        entries: list[dict[str, Any]] = []
        if orchestration_root.exists() and orchestration_root.is_dir():
            for child in sorted(orchestration_root.iterdir()):
                if not child.is_dir():
                    continue
                entries.append(
                    {
                        "orchestration_id": child.name,
                        "path": f"5_tasks/orchestration/{child.name}",
                        "has_events": (child / "orchestration_events.md").is_file(),
                        "has_iterations": (child / "planner_iterations.md").is_file(),
                    }
                )
        data = {
            "orchestration_root": "5_tasks/orchestration",
            "root_exists": orchestration_root.exists(),
            "entries": entries,
            "writes_enabled": False,
        }
        warnings = [] if entries else ["No orchestration ids found in this workspace."]
        return make_response(
            ok=True,
            verdict="PASS" if entries else "WARN",
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "root_exists": orchestration_root.exists(),
                "orchestration_count": len(entries),
                "first_orchestration_id": entries[0]["orchestration_id"] if entries else None,
            },
            warnings=warnings,
            blocking_reasons=[],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_task(task_id: str | None = None, path: str | Path | None = None, repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_task"
    try:
        selected_task_id, selected_path = _select_task_input(task_id, path)
        resolved_root = _resolve_repo_root(repo_root)
        validated, _tasks, _records, _profiles, _task = _load_validated_task(
            repo_root=resolved_root,
            task_id=selected_task_id,
            path=selected_path,
        )
        return _response_from_validated_report(
            operation=operation,
            report=validated,
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_preview(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "get_preview"
    try:
        selected_task_id, selected_path = _select_task_input(task_id, path)
        resolved_root = _resolve_repo_root(repo_root)
        validated, _tasks, records, profiles, _task = _load_validated_task(
            repo_root=resolved_root,
            task_id=selected_task_id,
            path=selected_path,
            actor=actor,
        )
        preview = build_preview(validated, actor, records=records, profiles=profiles)
        report = {
            "verdict": validated.get("verdict"),
            "blocking_reasons": list(validated.get("blocking_reasons", [])),
            "warnings": list(validated.get("warnings", [])),
            "needs_owner_reasons": list(validated.get("needs_owner_reasons", [])),
            "actor_match": validated.get("actor_match"),
            "summary": {
                "task_id": validated.get("task_id"),
                "can_start_session": preview.get("can_start_session"),
            },
            "preview": preview,
        }
        return make_response(
            ok=True,
            verdict=str(report["verdict"]),
            operation=operation,
            dry_run=False,
            actor=_actor_payload(actor),
            actor_match=validated.get("actor_match"),
            data=preview,
            summary=report["summary"],
            warnings=report["warnings"],
            blocking_reasons=report["blocking_reasons"],
            needs_owner_reasons=report["needs_owner_reasons"],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False, actor=_actor_payload(actor))


def get_drafts(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_drafts"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        report = list_drafts(resolved_root)
        payload = {"drafts_dir": report.get("drafts_dir"), "drafts": report.get("drafts", [])}
        return make_response(
            ok=True,
            verdict="PASS",
            operation=operation,
            dry_run=False,
            data=payload,
            summary={"total": report.get("total", 0)},
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_external_intake_review(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_external_intake_review"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        report = list_drafts(resolved_root)
        drafts = [
            draft
            for draft in report.get("drafts", [])
            if str(draft.get("path") or "").startswith("5_tasks/drafts/external_intake/")
        ]
        data = {
            "drafts_dir": "5_tasks/drafts/external_intake",
            "drafts_dir_exists": (resolved_root / "5_tasks" / "drafts" / "external_intake").is_dir(),
            "drafts": drafts,
            "writes_enabled": False,
        }
        return make_response(
            ok=True,
            verdict="PASS" if drafts else "WARN",
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "total": len(drafts),
                "ready": sum(1 for item in drafts if item.get("verdict") == "PASS"),
                "blocked": sum(1 for item in drafts if item.get("verdict") == "BLOCK"),
                "needs_owner": sum(1 for item in drafts if item.get("needs_owner") is True),
            },
            warnings=[] if drafts else ["No external intake drafts found."],
            blocking_reasons=[],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_owner_decision_records(repo_root: str | Path | None = None) -> dict[str, Any]:
    operation = "get_owner_decision_records"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        report = load_records(resolved_root)
        records = list(report.get("owner_decisions", []))
        data = {
            "records_dir": "5_tasks/records/owner_decisions",
            "records_dir_exists": bool(report.get("owner_decisions_root_exists")),
            "records": records,
            "writes_enabled": False,
        }
        return make_response(
            ok=True,
            verdict="PASS" if records else "WARN",
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "total": len(records),
                "approved": sum(1 for item in records if item.get("decision_status") == "approved"),
                "needs_revision": sum(1 for item in records if item.get("decision_status") == "needs_revision"),
                "rejected": sum(1 for item in records if item.get("decision_status") == "rejected"),
                "parse_errors": sum(len(item.get("parse_errors", [])) for item in records),
            },
            warnings=[] if records else ["No owner decision records found."],
            blocking_reasons=[],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)


def get_orchestration_summary_preview(
    orchestration_id: str | None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "orchestration_summary_preview"
    try:
        if not str(orchestration_id or "").strip():
            raise ValueError("orchestration_id is required")
        resolved_root = _resolve_repo_root(repo_root)
        tasks = load_all_tasks(resolved_root)
        records = load_records(resolved_root)
        result = build_orchestration_summary_preview(
            resolved_root,
            str(orchestration_id or "").strip(),
            tasks=tasks,
            records=records,
        )
        planned_summary = dict(result.get("planned_summary") or {})
        needs_owner_reasons = list(planned_summary.get("needs_owner_reasons", []))
        conflicts = list(result.get("conflicts", []))
        summary = dict(planned_summary)
        summary.update(
            {
                "conflict_count": len(conflicts),
                "writes_enabled": False,
                "execute_allowed": False,
            }
        )
        return make_response(
            ok=not bool(result.get("blocking_reasons")),
            verdict=str(result.get("verdict") or "PASS"),
            operation=operation,
            dry_run=True,
            data=result,
            summary=summary,
            planned_writes=[],
            planned_moves=[],
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=needs_owner_reasons,
            owner_confirmation_required=bool(result.get("owner_confirmation_required", False)),
            owner_confirmation_reasons=needs_owner_reasons + conflicts,
            execute_allowed=False,
            execute_blocking_reasons=["AIPOS-69 orchestration summary preview UI is read-only."],
            dry_run_token=None,
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=True)


def get_orchestration_timeline_preview(
    orchestration_id: str | None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "orchestration_timeline_preview"
    try:
        if not str(orchestration_id or "").strip():
            raise ValueError("orchestration_id is required")
        resolved_root = _resolve_repo_root(repo_root)
        result = build_orchestration_timeline_preview(resolved_root, str(orchestration_id or "").strip())
        summary = dict(result.get("summary") or {})
        needs_owner_reasons = [
            item.get("summary")
            for item in result.get("timeline", [])
            if item.get("owner_attention_required") and item.get("summary")
        ]
        return make_response(
            ok=not bool(result.get("blocking_reasons")),
            verdict=str(result.get("verdict") or "PASS"),
            operation=operation,
            dry_run=True,
            data=result,
            summary=summary,
            planned_writes=[],
            planned_moves=[],
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=needs_owner_reasons,
            owner_confirmation_required=bool(result.get("owner_confirmation_required", False)),
            owner_confirmation_reasons=needs_owner_reasons + list(result.get("conflicts", [])),
            execute_allowed=False,
            execute_blocking_reasons=["AIPOS-70 orchestration timeline UI is read-only."],
            dry_run_token=None,
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        response = _normalize_exception(operation, exc, dry_run=True)
        response["safety_notice"] = READ_SAFETY_NOTICE
        return response



def get_planner_loop_mvp_preview(
    orchestration_id: str | None,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    operation = "planner_loop_mvp_preview"
    try:
        if not str(orchestration_id or "").strip():
            raise ValueError("orchestration_id is required")
        resolved_root = _resolve_repo_root(repo_root)
        result = build_planner_loop_mvp_preview(
            resolved_root,
            str(orchestration_id or "").strip(),
            actor=actor,
        )
        summary = {
            "orchestration_id": result.get("orchestration_id"),
            "recommended_step": result.get("recommended_step", {}).get("step"),
            "recommended_route": result.get("recommended_step", {}).get("route"),
            "owner_gate_active": result.get("owner_gate", {}).get("active", False),
            "draft_candidates": len(result.get("draft_candidates", [])),
            "controlled_mutation_enabled": False,
            "writes_enabled": False,
            "execute_allowed": False,
        }
        return make_response(
            ok=not bool(result.get("blocking_reasons")),
            verdict=str(result.get("verdict") or "PASS"),
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=result,
            summary=summary,
            planned_writes=[],
            planned_moves=[],
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=list(result.get("needs_owner_reasons", [])),
            owner_confirmation_required=bool(result.get("owner_gate", {}).get("active", False)),
            owner_confirmation_reasons=list(result.get("owner_gate", {}).get("reasons", [])) + list(result.get("owner_gate", {}).get("conflicts", [])),
            execute_allowed=False,
            execute_blocking_reasons=["AIPOS-75 planner loop MVP is a coordinator preview; use existing controlled panels for mutations."],
            dry_run_token=None,
            safety_notice=str(result.get("safety_notice") or READ_SAFETY_NOTICE),
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=True)


def get_context_pack_preview(
    *,
    task_id: str | None = None,
    path: str | Path | None = None,
    orchestration_id: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "context_pack_preview"
    try:
        if sum(bool(str(value or "").strip()) for value in (task_id, path, orchestration_id)) != 1:
            raise ValueError("Exactly one of task_id, path, or orchestration_id is required")
        normalized_path = _normalize_path(path) if path is not None else None
        resolved_root = _resolve_repo_root(repo_root)
        result = build_context_pack_preview(
            resolved_root,
            task_id=str(task_id).strip() if task_id else None,
            path=normalized_path,
            orchestration_id=str(orchestration_id).strip() if orchestration_id else None,
        )
        return make_response(
            ok=not bool(result.get("blocking_reasons")),
            verdict=str(result.get("verdict") or "PASS"),
            operation=operation,
            dry_run=True,
            data=result,
            summary={
                "pack_id": result.get("pack_id"),
                "scope": result.get("scope"),
                "source_type": result.get("source_type"),
                "source_refs": len(result.get("source_refs", [])),
                "writes_enabled": False,
                "execute_allowed": False,
            },
            planned_writes=[],
            planned_moves=[],
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=list(result.get("needs_owner_reasons", [])),
            owner_confirmation_required=bool(result.get("needs_owner_reasons", [])),
            owner_confirmation_reasons=list(result.get("needs_owner_reasons", [])),
            execute_allowed=False,
            execute_blocking_reasons=["AIPOS-78 Context Pack preview is read-only."],
            dry_run_token=None,
            safety_notice=str(result.get("safety_notice") or READ_SAFETY_NOTICE),
            errors=[],
        )
    except Exception as exc:
        response = _normalize_exception(operation, exc, dry_run=True)
        response["safety_notice"] = READ_SAFETY_NOTICE
        return response


def _coerce_draft_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if "frontmatter" in payload:
        frontmatter = payload.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise TypeError("payload.frontmatter must be a mapping")
        body = payload.get("body", default_draft_body())
        if body is None:
            body = default_draft_body()
        if not isinstance(body, str):
            raise TypeError("payload.body must be a string")
        return dict(frontmatter), body

    metadata = dict(payload)
    body = metadata.pop("body", default_draft_body())
    if body is None:
        body = default_draft_body()
    if not isinstance(body, str):
        raise TypeError("payload.body must be a string")
    return metadata, body


def create_draft(
    payload: Mapping[str, Any],
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    operation = "draft_create"
    try:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not dry_run:
            return _blocked_execute(operation, actor=actor)
        resolved_root = _resolve_repo_root(repo_root)
        metadata, body = _coerce_draft_payload(payload)
        result = backend_create_draft(resolved_root, metadata, body, dry_run=True)
        verdict = derive_verdict(
            blocking_reasons=list(result.get("blocking_reasons", [])),
            warnings=list(result.get("warnings", [])),
        )
        data = {
            "task_id": result.get("task_id"),
            "target_path": result.get("target_path"),
            "would_write": result.get("would_write", False),
            "rendered_markdown": result.get("rendered_markdown"),
            "original_payload": {"frontmatter": metadata, "body": body},
        }
        response = make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=data,
            summary={"task_id": result.get("task_id"), "would_write": result.get("would_write", False)},
            planned_writes=list(result.get("planned_writes", [])),
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            safety_notice=MUTATION_DRY_RUN_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != "BLOCK",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def submit_external_intake(
    payload: Mapping[str, Any],
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    operation = "intake_submit"
    try:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not dry_run:
            return _blocked_execute(operation, actor=actor)
        resolved_root = _resolve_repo_root(repo_root)
        result = backend_build_external_intake_draft(
            resolved_root,
            dict(payload),
            actor=actor,
            dry_run=True,
        )
        verdict = derive_verdict(
            blocking_reasons=list(result.get("blocking_reasons", [])),
            warnings=list(result.get("warnings", [])),
        )
        data = {
            "safe_id": result.get("safe_id"),
            "task_id": result.get("task_id"),
            "target_path": result.get("target_path"),
            "would_write": result.get("would_write", False),
            "rendered_markdown": result.get("rendered_markdown"),
            "original_payload": result.get("original_payload"),
            "capability_scope": result.get("capability_scope"),
        }
        response = make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=data,
            summary={
                "safe_id": result.get("safe_id"),
                "task_id": result.get("task_id"),
                "target_path": result.get("target_path"),
                "would_write": result.get("would_write", False),
            },
            planned_writes=list(result.get("planned_writes", [])),
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            safety_notice=CONTROLLED_EXECUTE_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != "BLOCK",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def record_owner_decision(
    payload: Mapping[str, Any],
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    operation = "owner_decision_record"
    try:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not dry_run:
            return _blocked_execute(operation, actor=actor)
        resolved_root = _resolve_repo_root(repo_root)
        result = backend_build_owner_decision_record(
            resolved_root,
            dict(payload),
            actor=actor,
            dry_run=True,
        )
        verdict = derive_verdict(
            blocking_reasons=list(result.get("blocking_reasons", [])),
            warnings=list(result.get("warnings", [])),
        )
        data = {
            "decision_id": result.get("decision_id"),
            "target_path": result.get("target_path"),
            "would_write": result.get("would_write", False),
            "rendered_markdown": result.get("rendered_markdown"),
            "original_payload": result.get("original_payload"),
            "capability_scope": result.get("capability_scope"),
        }
        response = make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=data,
            summary={
                "decision_id": result.get("decision_id"),
                "target_path": result.get("target_path"),
                "would_write": result.get("would_write", False),
            },
            planned_writes=list(result.get("planned_writes", [])),
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            safety_notice=CONTROLLED_EXECUTE_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != "BLOCK",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def validate_draft(path: str | Path, repo_root: str | Path | None = None, actor: str | None = None) -> dict[str, Any]:
    operation = "draft_validate"
    try:
        normalized_path = _normalize_path(path)
        resolved_root = _resolve_repo_root(repo_root)
        result = validate_draft_file(resolved_root, normalized_path)
        verdict = derive_verdict(
            blocking_reasons=list(result.get("blocking_reasons", [])),
            warnings=list(result.get("warnings", [])),
        )
        return make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=False,
            actor=_actor_payload(actor),
            data=result,
            summary={"task_id": result.get("task_id"), "verdict": verdict},
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False, actor=_actor_payload(actor))


def publish_draft(
    path: str | Path,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    operation = "draft_publish"
    try:
        normalized_path = _normalize_path(path)
        if not dry_run:
            return _blocked_execute(operation, actor=actor)
        resolved_root = _resolve_repo_root(repo_root)
        result = backend_publish_draft(resolved_root, normalized_path, dry_run=True, actor=actor)
        verdict = derive_verdict(
            blocking_reasons=list(result.get("blocking_reasons", [])),
            warnings=list(result.get("warnings", [])),
        )
        data = {
            "task_id": result.get("task_id"),
            "source_path": result.get("source_path"),
            "target_path": result.get("target_path"),
            "would_write": result.get("would_write", False),
            "validation": result.get("validation"),
            "rendered_markdown": result.get("rendered_markdown"),
        }
        response = make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=data,
            summary={"task_id": result.get("task_id"), "would_write": result.get("would_write", False)},
            planned_writes=list(result.get("planned_writes", [])),
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            safety_notice=MUTATION_DRY_RUN_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != "BLOCK",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def _queue_mutation_preview(
    *,
    operation: str,
    action: str,
    task_id: str | None,
    path: str | Path | None,
    actor: str | None,
    dry_run: bool,
    with_records: bool,
    repo_root: str | Path | None,
    reason: str | None = None,
    report_link: str | None = None,
    owner_confirmation_required_override: bool | None = None,
    owner_confirmation_reasons_override: list[str] | None = None,
    mcp_claim_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not str(actor or "").strip():
        raise ValueError("actor is required")
    selected_task_id, selected_path = _select_task_input(task_id, path)
    if not dry_run:
        return _blocked_execute(operation, actor=actor)
    resolved_root = _resolve_repo_root(repo_root)
    profiles = load_agent_profiles(resolved_root)
    result = mutate_queue_task(
        resolved_root,
        action,
        task_id=selected_task_id,
        task_path=selected_path,
        actor=str(actor),
        reason=reason,
        report_link=report_link,
        dry_run=True,
        profiles=profiles,
        with_records=with_records,
        claim_id_override=(
            str(mcp_claim_metadata.get("planned_claim_id") or "").strip()
            if action == "claim" and isinstance(mcp_claim_metadata, dict)
            else None
        ),
        session_id_override=(
            str(mcp_claim_metadata.get("planned_session_id") or "").strip()
            if action == "claim" and isinstance(mcp_claim_metadata, dict)
            else None
        ),
    )
    validated, _tasks, _records, _profiles, _task = _load_validated_task(
        repo_root=resolved_root,
        task_id=selected_task_id,
        path=selected_path,
        actor=str(actor),
    )
    needs_owner_reasons = list(result.get("needs_owner_reasons", [])) or list(validated.get("needs_owner_reasons", []))
    verdict = str(result.get("verdict") or derive_verdict(
        blocking_reasons=list(result.get("blocking_reasons", [])),
        warnings=list(result.get("warnings", [])),
        needs_owner_reasons=needs_owner_reasons,
    ))
    data = {
        "task_id": result.get("task_id"),
        "source_path": result.get("source_path"),
        "target_path": result.get("target_path"),
        "from_state": result.get("from_state"),
        "to_state": result.get("to_state"),
        "would_write": result.get("would_write", False),
        "would_move": result.get("would_move", False),
        "updated_frontmatter": result.get("updated_frontmatter"),
        "with_records": result.get("with_records", False),
        "records_enabled": result.get("records_enabled", False),
    }
    if "record_writes" in result:
        data["record_writes"] = result.get("record_writes", [])
    if "record_updates" in result:
        data["record_updates"] = result.get("record_updates", [])
    if "record_previews" in result:
        data["record_previews"] = result.get("record_previews", [])
    if mcp_claim_metadata:
        data["mcp_claim"] = dict(mcp_claim_metadata)
        updated_frontmatter = result.get("updated_frontmatter") if isinstance(result.get("updated_frontmatter"), dict) else {}
        data["mcp_claim"]["planned_claim_id"] = updated_frontmatter.get("claim_id")
        data["mcp_claim"]["planned_session_id"] = updated_frontmatter.get("active_session_id")
        record_plan = _mcp_claim_record_plan(
            repo_root=resolved_root,
            task_id=str(result.get("task_id") or ""),
            task_path=str(result.get("target_path") or ""),
            actor=str(actor),
            canonical_agent_instance=str(mcp_claim_metadata.get("canonical_agent_instance") or actor),
            owner_policy_ref=str(mcp_claim_metadata.get("owner_policy_ref") or ""),
            updated_metadata=updated_frontmatter,
        )
        data["mcp_records_enabled"] = True
        data["records_enabled"] = True
        data["record_writes"] = record_plan["record_writes"]
        data["record_previews"] = record_plan["record_previews"]
        data["claim_record_path"] = record_plan["claim_record_path"]
        data["session_record_path"] = record_plan["session_record_path"]
        for reason_text in record_plan.get("record_blocking_reasons", []):
            if reason_text not in result["blocking_reasons"]:
                result["blocking_reasons"].append(reason_text)
        if record_plan.get("record_blocking_reasons"):
            verdict = "BLOCK"
    owner_required = verdict == "NEEDS_OWNER"
    owner_reasons = needs_owner_reasons if verdict == "NEEDS_OWNER" else []
    if owner_confirmation_required_override is not None:
        owner_required = bool(owner_confirmation_required_override)
        owner_reasons = list(owner_confirmation_reasons_override or [])
    response = make_response(
        ok=True,
        verdict=verdict,
        operation=operation,
        dry_run=True,
        actor=_actor_payload(actor),
        actor_match=validated.get("actor_match"),
        data=data,
        summary={"task_id": result.get("task_id"), "to_state": result.get("to_state")},
        planned_writes=(
            list(result.get("planned_writes", []))
            + [
                {"path": item.get("path"), "kind": "create", "type": "record_markdown", "record_type": item.get("record_type")}
                for item in data.get("record_writes", [])
            ]
        ),
        planned_moves=list(result.get("planned_moves", [])),
        warnings=list(result.get("warnings", [])),
        blocking_reasons=list(result.get("blocking_reasons", [])),
        needs_owner_reasons=needs_owner_reasons,
        owner_confirmation_required=owner_required,
        owner_confirmation_reasons=owner_reasons,
        safety_notice=MUTATION_DRY_RUN_NOTICE,
        errors=[],
    )
    allow_execute = verdict != "BLOCK" and operation == "queue_claim" and (not with_records or bool(mcp_claim_metadata))
    if with_records:
        response["execute_allowed"] = False
        response["execute_blocking_reasons"] = ["with_records execute is not enabled in AIPOS-38"]
        return response
    if operation in {"queue_block", "queue_complete", "queue_reopen"}:
        response["execute_allowed"] = False
        response["execute_blocking_reasons"] = ["operation is not enabled for controlled execute in AIPOS-38"]
        return response
    return _attach_controlled_execute_metadata(
        operation=operation,
        actor=actor,
        response=response,
        execute_allowed=allow_execute,
    )


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_return_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_normalize_return_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_return_value(item) for key, item in value.items()}
    return value


def _mcp_record_write_plan(path: str, record_type: str, *, would_write: bool = False, would_update: bool = False) -> dict[str, Any]:
    item = {"path": path, "record_type": record_type}
    if would_write:
        item["would_write"] = True
        item["wrote"] = False
    if would_update:
        item["would_update"] = True
        item["updated"] = False
    return item


def _mark_record_write_report_performed(data: dict[str, Any]) -> None:
    for key in ("record_writes", "record_updates"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("would_write"):
                item["wrote"] = True
            if item.get("would_update"):
                item["updated"] = True


def _mcp_claim_record_plan(
    *,
    repo_root: Path,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    updated_metadata: dict[str, Any],
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
) -> dict[str, Any]:
    claim_id = str(updated_metadata.get("claim_id") or "")
    session_id = str(updated_metadata.get("active_session_id") or "")
    claimed_at = str(updated_metadata.get("claimed_at") or "")
    claim_path, session_path = claim_record_paths(repo_root, task_id, claim_id, session_id)
    claim_rel = str(claim_path.relative_to(repo_root))
    session_rel = str(session_path.relative_to(repo_root))
    blocking: list[str] = []
    if claim_path.exists():
        blocking.append(f"Claim record already exists: {claim_rel}")
    if session_path.exists():
        blocking.append(f"Session record already exists: {session_rel}")
    confirmation_ref = f"owner_policy:{owner_policy_ref}"
    claim_markdown = build_mcp_claim_record_markdown(
        task_id=task_id,
        task_path=task_path,
        actor=actor,
        canonical_agent_instance=canonical_agent_instance,
        owner_policy_ref=owner_policy_ref,
        claim_id=claim_id,
        session_id=session_id,
        claimed_at=claimed_at,
        claim_policy=str(updated_metadata.get("claim_policy") or ""),
        claim_match_basis=str(updated_metadata.get("claim_match_basis") or ""),
        claim_requirements_hash=str(updated_metadata.get("claim_requirements_hash") or ""),
        dry_run_id=dry_run_id,
        dry_run_snapshot_hash=dry_run_snapshot_hash,
        confirmation_ref=confirmation_ref,
    )
    session_markdown = build_mcp_claim_session_record_markdown(
        task_id=task_id,
        task_path=task_path,
        actor=actor,
        canonical_agent_instance=canonical_agent_instance,
        owner_policy_ref=owner_policy_ref,
        session_id=session_id,
        claim_id=claim_id,
        created_at=claimed_at,
    )
    return {
        "record_blocking_reasons": blocking,
        "record_writes": [
            _mcp_record_write_plan(claim_rel, "claim_record", would_write=not blocking),
            _mcp_record_write_plan(session_rel, "session_record", would_write=not blocking),
        ],
        "record_previews": [
            {"path": claim_rel, "record_type": "claim_record", "rendered_markdown": claim_markdown},
            {"path": session_rel, "record_type": "session_record", "rendered_markdown": session_markdown},
        ],
        "claim_record_path": claim_rel,
        "session_record_path": session_rel,
        "claim_record_markdown": claim_markdown,
        "session_record_markdown": session_markdown,
    }


def _write_mcp_claim_records(repo_root: Path, record_plan: dict[str, Any]) -> list[dict[str, Any]]:
    performed: list[dict[str, Any]] = []
    for preview in record_plan.get("record_previews", []):
        path = repo_root / str(preview.get("path") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(preview.get("rendered_markdown") or ""), encoding="utf-8")
        performed.append({"path": str(preview.get("path")), "record_type": preview.get("record_type"), "wrote": True})
    return performed


def _mcp_return_record_plan(
    *,
    repo_root: Path,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    source_metadata: dict[str, Any],
    updated_metadata: dict[str, Any],
    result_summary: str | None,
    artifact_refs: list[str],
    completion_report_ref: str | None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
) -> dict[str, Any]:
    claim_id = str(source_metadata.get("claim_id") or "")
    session_id = str(source_metadata.get("active_session_id") or "")
    returned_at = str(updated_metadata.get("executor_completed_at") or "")
    return_id = build_runtime_id("return", task_id, returned_at, canonical_agent_instance)
    session_path = session_record_path(repo_root, task_id, session_id)
    return_path = return_record_path(repo_root, task_id, return_id)
    session_rel = str(session_path.relative_to(repo_root))
    return_rel = str(return_path.relative_to(repo_root))
    blocking: list[str] = []
    if not session_path.exists():
        blocking.append(f"Session record does not exist: {session_rel}")
    if return_path.exists():
        blocking.append(f"Return record already exists: {return_rel}")
    existing_metadata: dict[str, Any] = {}
    existing_body = ""
    if session_path.exists():
        existing_metadata, existing_body, parse_warnings = load_session_record(session_path)
        for warning in parse_warnings:
            blocking.append(f"Session record parse issue: {warning}")
        if existing_metadata.get("task_id") not in (None, task_id):
            blocking.append("Session record task_id does not match queue task")
        if existing_metadata.get("claim_id") not in (None, claim_id):
            blocking.append("Session record claim_id does not match queue task")
    confirmation_ref = f"owner_policy:{owner_policy_ref}"
    return_markdown = build_mcp_return_record_markdown(
        task_id=task_id,
        task_path=task_path,
        actor=actor,
        canonical_agent_instance=canonical_agent_instance,
        owner_policy_ref=owner_policy_ref,
        return_id=return_id,
        claim_id=claim_id,
        session_id=session_id,
        returned_at=returned_at,
        result_summary=result_summary,
        artifact_refs=artifact_refs,
        completion_report_ref=completion_report_ref,
        dry_run_id=dry_run_id,
        dry_run_snapshot_hash=dry_run_snapshot_hash,
        confirmation_ref=confirmation_ref,
    )
    session_markdown = ""
    if not blocking:
        session_markdown = append_mcp_return_session_event(
            existing_metadata,
            existing_body,
            actor=actor,
            canonical_agent_instance=canonical_agent_instance,
            owner_policy_ref=owner_policy_ref,
            timestamp=returned_at,
            return_id=return_id,
        )
    return {
        "return_id": return_id,
        "return_record_ref": return_id,
        "return_record_path": return_rel,
        "session_record_path": session_rel,
        "record_blocking_reasons": blocking,
        "record_writes": [_mcp_record_write_plan(return_rel, "return_record", would_write=not blocking)],
        "record_updates": [_mcp_record_write_plan(session_rel, "session_record", would_update=not blocking)],
        "record_previews": [
            {"path": return_rel, "record_type": "return_record", "rendered_markdown": return_markdown},
            {"path": session_rel, "record_type": "session_record", "rendered_markdown": session_markdown},
        ],
        "return_record_markdown": return_markdown,
        "session_record_markdown": session_markdown,
    }


def _write_mcp_return_records(repo_root: Path, record_plan: dict[str, Any]) -> list[dict[str, Any]]:
    performed: list[dict[str, Any]] = []
    for preview in record_plan.get("record_previews", []):
        path = repo_root / str(preview.get("path") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(preview.get("rendered_markdown") or ""), encoding="utf-8")
        item = {"path": str(preview.get("path")), "record_type": preview.get("record_type")}
        if preview.get("record_type") == "return_record":
            item["wrote"] = True
        else:
            item["updated"] = True
        performed.append(item)
    return performed


def _return_owner_reasons() -> list[str]:
    return ["MCP Supervised queue_return requires explicit Owner confirmation for this dry-run preview"]


def _unsafe_return_ref(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in ("api_key", "bearer ", "token=", "password=", "secret=")):
        return True
    raw = Path(value)
    return raw.is_absolute() or ".." in raw.parts


def _task_filename_for(task_id: str) -> str:
    value = "".join(char.lower() if char.isalnum() else "-" for char in task_id).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "task"


def _select_task(repo_root: Path, *, task_id: str | None, path: str | Path | None) -> dict[str, Any]:
    selected_task_id, selected_path = _select_task_input(task_id, path)
    if selected_task_id:
        selected, matches = find_task_by_id(selected_task_id, repo_root)
        if not matches:
            raise FileNotFoundError(f"No task found for task_id: {selected_task_id}")
        if len(matches) > 1:
            paths = ", ".join(sorted(str(match.get("path")) for match in matches))
            raise ValueError(f"Duplicate task_id {selected_task_id} found in: {paths}")
        assert selected is not None
        return selected
    assert selected_path is not None
    return load_task_by_path(selected_path, repo_root)


def _build_return_preview(
    *,
    task_id: str | None,
    path: str | Path | None,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str,
    claim_id: str | None,
    active_session_id: str | None,
    result_summary: str | None,
    artifact_refs: list[str],
    completion_report_ref: str | None,
    return_reason: str | None,
    repo_root: Path,
    dry_run: bool,
    planned_returned_at: str | None = None,
    mcp_return_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_task_id, selected_path = _select_task_input(task_id, path)
    validated, _tasks, _records, profiles, task = _load_validated_task(
        repo_root=repo_root,
        task_id=selected_task_id,
        path=selected_path,
        actor=actor,
    )
    source_rel = str(task.get("path") or "")
    source_path = repo_root / source_rel
    parsed_metadata, source_body, parse_warnings = parse_markdown_frontmatter(source_path.read_text(encoding="utf-8"))
    source_metadata = _normalize_return_value(parsed_metadata)

    blocking_reasons = list(validated.get("blocking_reasons", []))
    warnings = list(validated.get("warnings", []))
    warnings.extend(parse_warnings)
    if task.get("queue_state") != "claimed":
        blocking_reasons.append(f"TASK_NOT_CLAIMED: expected queue state claimed, found {task.get('queue_state')}")
    if task.get("frontmatter_status") != "claimed":
        blocking_reasons.append("TASK_NOT_CLAIMED: expected frontmatter status claimed")

    resolved = resolve_instance_id(agent_instance, profiles)
    canonical_agent_instance = str(resolved.get("canonical_instance_id") or "").strip()
    if resolved.get("resolution") == "ambiguous" or not canonical_agent_instance:
        blocking_reasons.append("agent_instance must resolve to one canonical concrete instance")
    if actor != canonical_agent_instance:
        blocking_reasons.append("For the first Supervised MCP return slice, actor must equal canonical agent_instance")

    claimed_by = str(source_metadata.get("claimed_by") or "")
    task_agent_instance = str(source_metadata.get("agent_instance") or "")
    if claimed_by:
        if not actor_matches_task_actor(canonical_agent_instance, claimed_by, profiles):
            blocking_reasons.append("CLAIMANT_MISMATCH: task is claimed by another actor")
    elif task_agent_instance:
        if not actor_matches_task_actor(canonical_agent_instance, task_agent_instance, profiles):
            blocking_reasons.append("CLAIMANT_MISMATCH: task agent_instance does not match returning instance")
    else:
        blocking_reasons.append("CLAIMANT_MISMATCH: claimed task lacks claimed_by or agent_instance")

    if claim_id and str(source_metadata.get("claim_id") or "").strip() != claim_id:
        blocking_reasons.append("CLAIM_ID_MISMATCH: claim_id does not match claimed task")
    if active_session_id and str(source_metadata.get("active_session_id") or "").strip() != active_session_id:
        blocking_reasons.append("SESSION_MISMATCH: active_session_id does not match claimed task")
    if any(_unsafe_return_ref(ref) for ref in [*artifact_refs, completion_report_ref or ""]):
        blocking_reasons.append("Return evidence refs must be repo-relative or approved workspace-relative and secret-free")

    updated_metadata = dict(source_metadata)
    updated_metadata["status"] = "claimed"
    updated_metadata["executor_status"] = "completed"
    updated_metadata["executor_completed_by"] = canonical_agent_instance or actor
    returned_at = planned_returned_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated_metadata["executor_completed_at"] = returned_at
    updated_metadata["audit_readiness"] = "ready"
    updated_metadata["audit_status"] = str(updated_metadata.get("audit_status") or "pending")
    updated_metadata["dependency_executor_status"] = "completed"
    updated_metadata["dependency_audit_readiness"] = "ready"
    updated_metadata["dependency_audit_status"] = str(updated_metadata.get("dependency_audit_status") or "pending")
    updated_metadata["return_owner_policy_ref"] = owner_policy_ref
    if result_summary:
        updated_metadata["result_summary"] = result_summary
    if artifact_refs:
        updated_metadata["artifact_refs"] = artifact_refs
    if completion_report_ref:
        updated_metadata["completion_report_ref"] = completion_report_ref
    if return_reason:
        updated_metadata["return_reason"] = return_reason

    record_plan: dict[str, Any] = {
        "record_writes": [],
        "record_updates": [],
        "record_previews": [],
        "record_blocking_reasons": [],
    }
    if mcp_return_metadata:
        record_plan = _mcp_return_record_plan(
            repo_root=repo_root,
            task_id=str(task.get("task_id") or ""),
            task_path=source_rel,
            actor=actor,
            canonical_agent_instance=canonical_agent_instance,
            owner_policy_ref=owner_policy_ref,
            source_metadata=source_metadata,
            updated_metadata=updated_metadata,
            result_summary=result_summary,
            artifact_refs=artifact_refs,
            completion_report_ref=completion_report_ref,
        )
        if record_plan.get("record_blocking_reasons"):
            blocking_reasons.extend(str(item) for item in record_plan.get("record_blocking_reasons", []))
        else:
            updated_metadata["return_record_ref"] = record_plan.get("return_record_ref")
            updated_metadata["return_event_ref"] = record_plan.get("return_record_ref")

    rendered_markdown = render_task_markdown(updated_metadata, source_body)
    planned_write = {"path": source_rel, "kind": "update", "type": "task_markdown"}
    return_preview = {
        "executor_status": "completed",
        "audit_readiness": "ready",
        "audit_status_after_return": updated_metadata["audit_status"],
        "result_summary": result_summary,
        "artifact_refs": artifact_refs,
        "completion_report_ref": completion_report_ref,
    }
    data = {
        "task_id": task.get("task_id"),
        "source_path": source_rel,
        "target_path": source_rel,
        "from_state": "claimed",
        "to_state": "claimed",
        "would_write": not blocking_reasons,
        "would_move": False,
        "updated_frontmatter": updated_metadata,
        "rendered_markdown": rendered_markdown,
        "target_file_state": _target_file_state(repo_root, source_rel),
        "with_records": False,
        "records_enabled": bool(mcp_return_metadata),
        "mcp_records_enabled": bool(mcp_return_metadata),
        "owner_policy_ref": owner_policy_ref,
        "canonical_agent_instance": canonical_agent_instance,
        "claim_id": str(source_metadata.get("claim_id") or claim_id or ""),
        "claimed_by": claimed_by,
        "return_record_ref": updated_metadata.get("return_record_ref"),
        "return_preview": return_preview,
        "original_payload": {
            "task_id": task_id,
            "path": str(path) if path is not None else None,
            "actor": actor,
            "agent_instance": agent_instance,
            "owner_policy_ref": owner_policy_ref,
            "claim_id": claim_id,
            "active_session_id": active_session_id,
            "result_summary": result_summary,
            "artifact_refs": artifact_refs,
            "completion_report_ref": completion_report_ref,
            "return_reason": return_reason,
            "planned_returned_at": returned_at,
        },
        "lease_preview": {
            "lease_path": "claim_only",
            "lease_status": "proposed",
            "active_lease_written": False,
        },
    }
    if mcp_return_metadata:
        data["mcp_return"] = dict(mcp_return_metadata)
        data["record_writes"] = record_plan.get("record_writes", [])
        data["record_updates"] = record_plan.get("record_updates", [])
        data["record_previews"] = record_plan.get("record_previews", [])
        data["return_record_path"] = record_plan.get("return_record_path")
        data["session_record_path"] = record_plan.get("session_record_path")

    verdict = derive_verdict(blocking_reasons=blocking_reasons, warnings=warnings)
    response = make_response(
        ok=True,
        verdict=verdict,
        operation="queue_return",
        dry_run=dry_run,
        actor=_actor_payload(actor),
        actor_match=validated.get("actor_match"),
        data=data,
        summary={"task_id": task.get("task_id"), "audit_readiness": "ready"},
        planned_writes=[
            planned_write,
            *[
                {"path": item.get("path"), "kind": "create", "type": "record_markdown", "record_type": item.get("record_type")}
                for item in record_plan.get("record_writes", [])
            ],
            *[
                {"path": item.get("path"), "kind": "update", "type": "record_markdown", "record_type": item.get("record_type")}
                for item in record_plan.get("record_updates", [])
            ],
        ],
        planned_moves=[],
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=[],
        owner_confirmation_required=verdict != "BLOCK",
        owner_confirmation_reasons=_return_owner_reasons() if verdict != "BLOCK" else [],
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    response["lease_preview"] = data["lease_preview"]
    response["return_preview"] = return_preview
    return response


def return_task(
    *,
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    agent_instance: str | None = None,
    owner_policy_ref: str | None = None,
    claim_id: str | None = None,
    active_session_id: str | None = None,
    result_summary: str | None = None,
    artifact_refs: Any = None,
    completion_report_ref: str | None = None,
    return_reason: str | None = None,
    planned_returned_at: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    mcp_return_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        actor_text = str(actor or "").strip()
        instance_text = str(agent_instance or "").strip()
        policy_ref = str(owner_policy_ref or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        if not instance_text:
            raise ValueError("agent_instance is required")
        if not policy_ref:
            raise ValueError("owner_policy_ref is required")
        refs = _as_list(artifact_refs)
        summary_text = str(result_summary or "").strip()
        completion_ref = str(completion_report_ref or "").strip()
        if not summary_text and not refs and not completion_ref:
            raise ValueError("MISSING_RETURN_EVIDENCE: result_summary, artifact_refs, or completion_report_ref is required")
        resolved_root = _resolve_repo_root(repo_root)
        response = _build_return_preview(
            task_id=task_id,
            path=path,
            actor=actor_text,
            agent_instance=instance_text,
            owner_policy_ref=policy_ref,
            claim_id=str(claim_id or "").strip() or None,
            active_session_id=str(active_session_id or "").strip() or None,
            result_summary=summary_text or None,
            artifact_refs=refs,
            completion_report_ref=completion_ref or None,
            return_reason=str(return_reason or "").strip() or None,
            planned_returned_at=str(planned_returned_at or "").strip() or None,
            repo_root=resolved_root,
            dry_run=dry_run,
            mcp_return_metadata=mcp_return_metadata,
        )
        if dry_run:
            return _attach_controlled_execute_metadata(
                operation="queue_return",
                actor=actor_text,
                response=response,
                execute_allowed=response.get("verdict") != "BLOCK",
            )
        if response.get("verdict") == "BLOCK":
            return response
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        target = resolved_root / str(data.get("target_path") or "")
        target.write_text(str(data.get("rendered_markdown") or ""), encoding="utf-8")
        record_performed_writes: list[dict[str, Any]] = []
        if bool(data.get("mcp_records_enabled")):
            record_performed_writes = _write_mcp_return_records(resolved_root, data)
        response["dry_run"] = False
        response["data"]["wrote"] = True
        _mark_record_write_report_performed(response["data"])
        response["performed_writes"] = list(response.get("planned_writes", [])) + record_performed_writes
        response["owner_confirmation_required"] = False
        response["owner_confirmation_reasons"] = []
        return response
    except Exception as exc:
        return _normalize_exception("queue_return", exc, dry_run=dry_run, actor=_actor_payload(actor))


def _dispatch_owner_reasons() -> list[str]:
    return ["MCP Supervised audit_dispatch requires explicit Owner confirmation for this dry-run preview"]


def _verdict_owner_reasons() -> list[str]:
    return ["MCP Supervised audit_verdict requires explicit Owner confirmation for this dry-run preview"]


def _build_audit_dispatch_preview(
    *,
    source_task_id: str | None,
    source_path: str | Path | None,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str,
    audit_task_id: str,
    audit_task_title: str | None,
    audit_by: str | None,
    audit_agent_instance: str,
    dispatch_reason: str | None,
    repo_root: Path,
    dry_run: bool,
    planned_dispatch_id: str | None = None,
    planned_dispatched_at: str | None = None,
) -> dict[str, Any]:
    source_task = _select_task(repo_root, task_id=source_task_id, path=source_path)
    source_rel = str(source_task.get("path") or "")
    source_file = repo_root / source_rel
    source_metadata, source_body, parse_warnings = parse_markdown_frontmatter(source_file.read_text(encoding="utf-8"))
    source_metadata = _normalize_return_value(source_metadata)
    tasks = load_all_tasks(repo_root)
    records = load_records(repo_root)
    profiles = load_agent_profiles(repo_root)
    source_validated = validate_single_task(source_task, tasks=tasks, records=records, profiles=profiles)

    blocking_reasons = list(source_validated.get("blocking_reasons", []))
    warnings = [*list(source_validated.get("warnings", [])), *parse_warnings]

    resolved = resolve_instance_id(agent_instance, profiles)
    canonical_agent_instance = str(resolved.get("canonical_instance_id") or "").strip()
    if resolved.get("resolution") == "ambiguous" or not canonical_agent_instance:
        blocking_reasons.append("INSTANCE_REQUIRED: agent_instance must resolve to one canonical concrete instance")
    if actor != canonical_agent_instance:
        blocking_reasons.append("INSTANCE_MISMATCH: actor must equal canonical agent_instance for Supervised MCP audit_dispatch")

    if source_task.get("queue_state") != "claimed" or source_metadata.get("status") != "claimed":
        blocking_reasons.append("SOURCE_TASK_NOT_AUDIT_READY: source task must be claimed")
    if source_metadata.get("executor_status") != "completed":
        blocking_reasons.append("SOURCE_TASK_NOT_AUDIT_READY: executor_status must be completed")
    if source_metadata.get("audit_readiness") != "ready":
        blocking_reasons.append("SOURCE_TASK_NOT_AUDIT_READY: audit_readiness must be ready")
    if source_metadata.get("dependency_audit_status") == "PASS":
        blocking_reasons.append("AUDIT_ALREADY_PASSED: source task already has audit PASS")
    if source_metadata.get("related_audit_task_ref") or source_metadata.get("audit_dispatch_record_ref"):
        blocking_reasons.append("AUDIT_ALREADY_DISPATCHED: source task already links an audit dispatch")

    reviewed_return_record_ref = str(source_metadata.get("return_record_ref") or source_metadata.get("return_event_ref") or "").strip()
    if not reviewed_return_record_ref:
        blocking_reasons.append("MISSING_RETURN_RECORD: source task lacks return_record_ref")
    elif not records.get("return_index", {}).get(reviewed_return_record_ref):
        blocking_reasons.append("MISSING_RETURN_RECORD: return_record_ref does not resolve to a return record")

    reviewed_executor_instance = str(source_metadata.get("executor_completed_by") or source_metadata.get("agent_instance") or source_metadata.get("claimed_by") or "").strip()
    if not reviewed_executor_instance:
        blocking_reasons.append("MISSING_EXECUTOR_INSTANCE: source task lacks reviewed executor instance")
    if reviewed_executor_instance and audit_agent_instance:
        audit_resolved = resolve_instance_id(audit_agent_instance, profiles)
        audit_canonical = str(audit_resolved.get("canonical_instance_id") or "").strip()
        if audit_resolved.get("resolution") == "ambiguous" or not audit_canonical:
            blocking_reasons.append("INSTANCE_REQUIRED: audit_agent_instance must resolve to one canonical concrete instance")
        elif audit_canonical == reviewed_executor_instance:
            blocking_reasons.append("INDEPENDENCE_FAILED: audit_agent_instance must be distinct from reviewed_executor_instance")
    else:
        blocking_reasons.append("INSTANCE_REQUIRED: audit_agent_instance is required for the first audit_dispatch slice")

    task_id_text = str(audit_task_id or "").strip()
    if not task_id_text:
        blocking_reasons.append("INVALID_AUDIT_TASK_ID: audit_task_id is required")
    audit_rel = f"5_tasks/queue/pending/{_task_filename_for(task_id_text)}.md"
    audit_path = repo_root / audit_rel
    if audit_path.exists():
        blocking_reasons.append(f"AUDIT_TASK_TARGET_EXISTS: {audit_rel}")
    if task_id_text:
        _existing, matches = find_task_by_id(task_id_text, repo_root)
        if matches:
            blocking_reasons.append(f"AUDIT_TASK_ID_EXISTS: {task_id_text}")

    timestamp = planned_dispatched_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    dispatch_id = planned_dispatch_id or build_runtime_id("dispatch", str(source_task.get("task_id") or ""), timestamp, canonical_agent_instance or actor)
    dispatch_path = audit_dispatch_record_path(repo_root, str(source_task.get("task_id") or ""), dispatch_id)
    dispatch_rel = str(dispatch_path.relative_to(repo_root))
    if dispatch_path.exists():
        blocking_reasons.append(f"Audit dispatch record already exists: {dispatch_rel}")

    audit_metadata = {
        "task_id": task_id_text,
        "title": audit_task_title or f"Audit {source_task.get('task_id')}",
        "project": source_metadata.get("project") or "lybra",
        "assigned_to": audit_by or source_metadata.get("assigned_to") or "audit",
        "agent_instance": audit_agent_instance,
        "context_bundle": source_metadata.get("context_bundle") or "default",
        "task_mode": "audit",
        "task_class": "complex",
        "model_tier": source_metadata.get("model_tier") or "L2",
        "priority": source_metadata.get("priority") or "medium",
        "status": "pending",
        "created_by": actor,
        "needs_owner": False,
        "planner_agent": "owner_planner",
        "reviewer": "owner_review",
        "audit_by": audit_agent_instance,
        "output_target": source_metadata.get("output_target") or source_rel,
        "artifact_policy": source_metadata.get("artifact_policy") or "formal_write",
        "session_policy": source_metadata.get("session_policy") or "single_task_session",
        "context_isolation": source_metadata.get("context_isolation") or "strict",
        "artifact_scope": source_metadata.get("artifact_scope") or source_rel,
        "memory_scope": f"audit of {source_task.get('task_id')}",
        "claim_policy": "specific_instance_only",
        "depends_on": [str(source_task.get("task_id") or "")],
        "dependency_condition": "audit_readiness",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "reviewed_task_id": source_task.get("task_id"),
        "reviewed_task_path": source_rel,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_executor_claim_id": source_metadata.get("claim_id") or "",
        "reviewed_executor_session_id": source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or "",
        "audit_subject_condition": "audit_readiness",
        "required_verdict_condition": "audit_pass",
        "independence_distinct_instance": True,
        "audit_dispatch_record_ref": dispatch_id,
        "audit_dispatch_owner_policy_ref": owner_policy_ref,
    }
    if dispatch_reason:
        audit_metadata["dispatch_reason"] = dispatch_reason
    audit_body = "\n".join(
        [
            f"Audit task for `{source_task.get('task_id')}`.",
            "",
            "Review the returned work evidence and produce an independent verdict.",
            "",
        ]
    )
    audit_markdown = render_task_markdown(audit_metadata, audit_body)

    updated_source_metadata = dict(source_metadata)
    updated_source_metadata["related_audit_task_ref"] = task_id_text
    updated_source_metadata["audit_dispatch_record_ref"] = dispatch_id
    updated_source_metadata["audit_dispatched_at"] = timestamp
    updated_source_metadata["audit_dispatched_by"] = canonical_agent_instance or actor
    updated_source_metadata["audit_dispatch_owner_policy_ref"] = owner_policy_ref
    source_markdown = render_task_markdown(updated_source_metadata, source_body)

    dispatch_markdown = build_mcp_audit_dispatch_record_markdown(
        dispatch_id=dispatch_id,
        reviewed_task_id=str(source_task.get("task_id") or ""),
        reviewed_task_path=source_rel,
        reviewed_return_record_ref=reviewed_return_record_ref,
        reviewed_executor_instance=reviewed_executor_instance,
        reviewed_executor_claim_id=str(source_metadata.get("claim_id") or ""),
        reviewed_executor_session_id=str(source_metadata.get("active_session_id") or source_metadata.get("last_session_id") or ""),
        audit_task_id=task_id_text,
        audit_task_path=audit_rel,
        actor=actor,
        canonical_agent_instance=canonical_agent_instance,
        owner_policy_ref=owner_policy_ref,
        dispatched_at=timestamp,
    )
    record_writes = [_mcp_record_write_plan(dispatch_rel, "audit_dispatch_record", would_write=not blocking_reasons)]
    data = {
        "source_task_id": source_task.get("task_id"),
        "task_id": source_task.get("task_id"),
        "source_path": source_rel,
        "target_path": source_rel,
        "from_state": "claimed",
        "to_state": "claimed",
        "would_write": not blocking_reasons,
        "would_move": False,
        "updated_frontmatter": updated_source_metadata,
        "rendered_markdown": source_markdown,
        "target_file_state": _target_file_state(repo_root, source_rel),
        "audit_task_id": task_id_text,
        "audit_task_path": audit_rel,
        "audit_task_markdown": audit_markdown,
        "audit_task_metadata": audit_metadata,
        "dispatch_id": dispatch_id,
        "audit_dispatch_record_path": dispatch_rel,
        "record_writes": record_writes,
        "record_previews": [{"path": dispatch_rel, "record_type": "audit_dispatch_record", "rendered_markdown": dispatch_markdown}],
        "owner_policy_ref": owner_policy_ref,
        "canonical_agent_instance": canonical_agent_instance,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "original_payload": {
            "source_task_id": source_task_id,
            "source_path": str(source_path) if source_path is not None else None,
            "actor": actor,
            "agent_instance": agent_instance,
            "owner_policy_ref": owner_policy_ref,
            "audit_task_id": task_id_text,
            "audit_task_title": audit_task_title,
            "audit_by": audit_by,
            "audit_agent_instance": audit_agent_instance,
            "dispatch_reason": dispatch_reason,
            "planned_dispatch_id": dispatch_id,
            "planned_dispatched_at": timestamp,
        },
    }
    verdict = derive_verdict(blocking_reasons=blocking_reasons, warnings=warnings)
    response = make_response(
        ok=True,
        verdict=verdict,
        operation="audit_dispatch",
        dry_run=dry_run,
        actor=_actor_payload(actor),
        data=data,
        summary={"source_task_id": source_task.get("task_id"), "audit_task_id": task_id_text},
        planned_writes=[
            {"path": source_rel, "kind": "update", "type": "task_markdown"},
            {"path": audit_rel, "kind": "create", "type": "task_markdown"},
            {"path": dispatch_rel, "kind": "create", "type": "record_markdown", "record_type": "audit_dispatch_record"},
        ],
        planned_moves=[],
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=[],
        owner_confirmation_required=verdict != "BLOCK",
        owner_confirmation_reasons=_dispatch_owner_reasons() if verdict != "BLOCK" else [],
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    return response


def audit_dispatch_task(
    *,
    source_task_id: str | None = None,
    source_path: str | Path | None = None,
    actor: str | None = None,
    agent_instance: str | None = None,
    owner_policy_ref: str | None = None,
    audit_task_id: str | None = None,
    audit_task_title: str | None = None,
    audit_by: str | None = None,
    audit_agent_instance: str | None = None,
    dispatch_reason: str | None = None,
    planned_dispatch_id: str | None = None,
    planned_dispatched_at: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        actor_text = str(actor or "").strip()
        instance_text = str(agent_instance or "").strip()
        policy_ref = str(owner_policy_ref or "").strip()
        audit_id = str(audit_task_id or "").strip()
        audit_instance = str(audit_agent_instance or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        if not instance_text:
            raise ValueError("agent_instance is required")
        if not policy_ref:
            raise ValueError("owner_policy_ref is required")
        if not audit_id:
            raise ValueError("audit_task_id is required")
        if not audit_instance:
            raise ValueError("audit_agent_instance is required")
        resolved_root = _resolve_repo_root(repo_root)
        response = _build_audit_dispatch_preview(
            source_task_id=source_task_id,
            source_path=source_path,
            actor=actor_text,
            agent_instance=instance_text,
            owner_policy_ref=policy_ref,
            audit_task_id=audit_id,
            audit_task_title=str(audit_task_title or "").strip() or None,
            audit_by=str(audit_by or "").strip() or None,
            audit_agent_instance=audit_instance,
            dispatch_reason=str(dispatch_reason or "").strip() or None,
            planned_dispatch_id=str(planned_dispatch_id or "").strip() or None,
            planned_dispatched_at=str(planned_dispatched_at or "").strip() or None,
            repo_root=resolved_root,
            dry_run=dry_run,
        )
        if dry_run:
            return _attach_controlled_execute_metadata(
                operation="audit_dispatch",
                actor=actor_text,
                response=response,
                execute_allowed=response.get("verdict") != "BLOCK",
            )
        if response.get("verdict") == "BLOCK":
            return response
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        (resolved_root / str(data.get("target_path") or "")).write_text(str(data.get("rendered_markdown") or ""), encoding="utf-8")
        audit_path = resolved_root / str(data.get("audit_task_path") or "")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(str(data.get("audit_task_markdown") or ""), encoding="utf-8")
        performed = [{"path": data.get("target_path"), "kind": "update", "type": "task_markdown"}, {"path": data.get("audit_task_path"), "kind": "create", "type": "task_markdown"}]
        for preview in data.get("record_previews", []):
            path = resolved_root / str(preview.get("path") or "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(preview.get("rendered_markdown") or ""), encoding="utf-8")
            performed.append({"path": str(preview.get("path")), "kind": "create", "type": "record_markdown", "record_type": preview.get("record_type")})
        response["dry_run"] = False
        response["data"]["wrote"] = True
        _mark_record_write_report_performed(response["data"])
        response["performed_writes"] = performed
        response["owner_confirmation_required"] = False
        response["owner_confirmation_reasons"] = []
        return response
    except Exception as exc:
        return _normalize_exception("audit_dispatch", exc, dry_run=dry_run, actor=_actor_payload(actor))


def _build_audit_verdict_preview(
    *,
    audit_task_id: str | None,
    audit_task_path: str | Path | None,
    reviewed_task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str,
    audit_claim_id: str | None,
    audit_session_id: str | None,
    audit_dispatch_record_ref: str | None,
    reviewed_return_record_ref: str | None,
    verdict_value: str,
    findings_summary: str | None,
    evidence_refs: list[str],
    recommended_next_action: str | None,
    owner_waiver_ref: str | None,
    repo_root: Path,
    dry_run: bool,
    planned_verdict_id: str | None = None,
    planned_verdict_at: str | None = None,
) -> dict[str, Any]:
    audit_task = _select_task(repo_root, task_id=audit_task_id, path=audit_task_path)
    audit_rel = str(audit_task.get("path") or "")
    audit_file = repo_root / audit_rel
    audit_metadata, audit_body, audit_parse_warnings = parse_markdown_frontmatter(audit_file.read_text(encoding="utf-8"))
    audit_metadata = _normalize_return_value(audit_metadata)
    reviewed_task = _select_task(repo_root, task_id=reviewed_task_id, path=None)
    reviewed_rel = str(reviewed_task.get("path") or "")
    reviewed_file = repo_root / reviewed_rel
    reviewed_metadata, reviewed_body, reviewed_parse_warnings = parse_markdown_frontmatter(reviewed_file.read_text(encoding="utf-8"))
    reviewed_metadata = _normalize_return_value(reviewed_metadata)
    tasks = load_all_tasks(repo_root)
    records = load_records(repo_root)
    profiles = load_agent_profiles(repo_root)
    audit_validated = validate_single_task(audit_task, tasks=tasks, current_actor=actor, records=records, profiles=profiles)
    reviewed_validated = validate_single_task(reviewed_task, tasks=tasks, records=records, profiles=profiles)

    blocking_reasons = [*list(audit_validated.get("blocking_reasons", [])), *list(reviewed_validated.get("blocking_reasons", []))]
    warnings = [
        *list(audit_validated.get("warnings", [])),
        *list(reviewed_validated.get("warnings", [])),
        *audit_parse_warnings,
        *reviewed_parse_warnings,
    ]
    resolved = resolve_instance_id(agent_instance, profiles)
    canonical_agent_instance = str(resolved.get("canonical_instance_id") or "").strip()
    if resolved.get("resolution") == "ambiguous" or not canonical_agent_instance:
        blocking_reasons.append("INSTANCE_REQUIRED: agent_instance must resolve to one canonical concrete instance")
    if actor != canonical_agent_instance:
        blocking_reasons.append("INSTANCE_MISMATCH: actor must equal canonical agent_instance for Supervised MCP audit_verdict")

    normalized_verdict = verdict_value.upper().strip()
    if normalized_verdict == "CHANGES":
        normalized_verdict = "REQUEST_CHANGES"
    if normalized_verdict not in {"PASS", "FAIL", "REQUEST_CHANGES", "BLOCKED", "WAIVED"}:
        blocking_reasons.append("INVALID_VERDICT: verdict must be PASS, FAIL, REQUEST_CHANGES, BLOCKED, or WAIVED")
    if normalized_verdict == "WAIVED" and not str(owner_waiver_ref or "").strip():
        blocking_reasons.append("WAIVER_REQUIRES_OWNER_EVIDENCE: owner_waiver_ref is required for WAIVED")

    if audit_task.get("queue_state") != "claimed" or audit_metadata.get("status") != "claimed":
        blocking_reasons.append("AUDIT_TASK_NOT_CLAIMED: audit task must be claimed")
    if audit_metadata.get("reviewed_task_id") != reviewed_task.get("task_id"):
        blocking_reasons.append("REVIEWED_TASK_MISMATCH: audit task reviewed_task_id does not match request")
    if audit_claim_id and str(audit_metadata.get("claim_id") or "") != audit_claim_id:
        blocking_reasons.append("AUDIT_CLAIM_MISMATCH: audit_claim_id does not match audit task")
    if audit_session_id and str(audit_metadata.get("active_session_id") or "") != audit_session_id:
        blocking_reasons.append("AUDIT_SESSION_MISMATCH: audit_session_id does not match audit task")

    reviewed_executor_instance = str(audit_metadata.get("reviewed_executor_instance") or reviewed_metadata.get("executor_completed_by") or "").strip()
    if not reviewed_executor_instance:
        blocking_reasons.append("MISSING_EXECUTOR_INSTANCE: reviewed executor instance is required")
    if reviewed_executor_instance and canonical_agent_instance == reviewed_executor_instance:
        blocking_reasons.append("INDEPENDENCE_FAILED: auditor must be distinct from reviewed_executor_instance")

    dispatch_ref = str(audit_dispatch_record_ref or audit_metadata.get("audit_dispatch_record_ref") or reviewed_metadata.get("audit_dispatch_record_ref") or "").strip()
    if not dispatch_ref:
        blocking_reasons.append("MISSING_AUDIT_DISPATCH_RECORD: audit dispatch record ref is required")
    elif not records.get("audit_dispatch_index", {}).get(dispatch_ref):
        blocking_reasons.append("MISSING_AUDIT_DISPATCH_RECORD: dispatch ref does not resolve to a record")
    return_ref = str(reviewed_return_record_ref or audit_metadata.get("reviewed_return_record_ref") or reviewed_metadata.get("return_record_ref") or reviewed_metadata.get("return_event_ref") or "").strip()
    if not return_ref:
        blocking_reasons.append("MISSING_RETURN_RECORD: reviewed return record ref is required")
    elif not records.get("return_index", {}).get(return_ref):
        blocking_reasons.append("MISSING_RETURN_RECORD: reviewed return record ref does not resolve to a record")
    session_id = str(audit_session_id or audit_metadata.get("active_session_id") or "").strip()
    if not session_id:
        blocking_reasons.append("MISSING_AUDIT_SESSION_RECORD: audit session id is required")
    session_path = session_record_path(repo_root, str(audit_task.get("task_id") or ""), session_id) if session_id else None
    if session_path is None or not session_path.exists():
        blocking_reasons.append("MISSING_AUDIT_SESSION_RECORD: audit session record does not exist")

    if any(_unsafe_return_ref(ref) for ref in evidence_refs):
        blocking_reasons.append("Audit evidence refs must be repo-relative or approved workspace-relative and secret-free")
    if normalized_verdict == "PASS" and not (findings_summary or evidence_refs):
        blocking_reasons.append("MISSING_VERDICT_EVIDENCE: PASS requires findings_summary or evidence_refs")

    timestamp = planned_verdict_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    verdict_id = planned_verdict_id or build_runtime_id("verdict", str(reviewed_task.get("task_id") or ""), timestamp, canonical_agent_instance or actor)
    verdict_path = audit_verdict_record_path(repo_root, str(reviewed_task.get("task_id") or ""), verdict_id)
    verdict_rel = str(verdict_path.relative_to(repo_root))
    if verdict_path.exists():
        blocking_reasons.append(f"Audit verdict record already exists: {verdict_rel}")

    updated_reviewed = dict(reviewed_metadata)
    updated_reviewed["related_audit_verdict_ref"] = verdict_id
    updated_reviewed["audit_verdict"] = normalized_verdict
    updated_reviewed["audit_verdict_at"] = timestamp
    updated_reviewed["audit_verdict_by"] = canonical_agent_instance or actor
    if normalized_verdict == "PASS":
        updated_reviewed["dependency_audit_status"] = "PASS"
        updated_reviewed["audit_status"] = "PASS"
    else:
        updated_reviewed["dependency_audit_status"] = normalized_verdict
        updated_reviewed["audit_status"] = normalized_verdict
    reviewed_markdown = render_task_markdown(updated_reviewed, reviewed_body)

    updated_audit = dict(audit_metadata)
    updated_audit["audit_verdict"] = normalized_verdict
    updated_audit["related_audit_verdict_ref"] = verdict_id
    updated_audit["audit_verdict_at"] = timestamp
    updated_audit["audit_verdict_by"] = canonical_agent_instance or actor
    audit_markdown = render_task_markdown(updated_audit, audit_body)

    verdict_markdown = build_mcp_audit_verdict_record_markdown(
        verdict_id=verdict_id,
        verdict=normalized_verdict,
        reviewed_task_id=str(reviewed_task.get("task_id") or ""),
        reviewed_task_path=reviewed_rel,
        reviewed_return_record_ref=return_ref,
        audit_dispatch_record_ref=dispatch_ref,
        audit_task_id=str(audit_task.get("task_id") or ""),
        audit_task_path=audit_rel,
        audit_claim_id=str(audit_metadata.get("claim_id") or audit_claim_id or ""),
        audit_session_id=session_id,
        reviewed_executor_instance=reviewed_executor_instance,
        auditor_instance=canonical_agent_instance,
        actor=actor,
        canonical_agent_instance=canonical_agent_instance,
        owner_policy_ref=owner_policy_ref,
        verdict_at=timestamp,
        findings_summary=findings_summary,
        evidence_refs=evidence_refs,
        recommended_next_action=recommended_next_action,
    )
    session_markdown = ""
    session_rel = str(session_path.relative_to(repo_root)) if session_path else ""
    if session_path and session_path.exists():
        existing_metadata, existing_body, parse_warnings = load_session_record(session_path)
        for warning in parse_warnings:
            blocking_reasons.append(f"Audit session record parse issue: {warning}")
        session_markdown = append_mcp_audit_verdict_session_event(
            existing_metadata,
            existing_body,
            actor=actor,
            canonical_agent_instance=canonical_agent_instance,
            owner_policy_ref=owner_policy_ref,
            timestamp=timestamp,
            verdict_id=verdict_id,
            verdict=normalized_verdict,
        )

    data = {
        "task_id": reviewed_task.get("task_id"),
        "source_path": reviewed_rel,
        "target_path": reviewed_rel,
        "from_state": str(reviewed_metadata.get("status") or reviewed_task.get("queue_state")),
        "to_state": str(updated_reviewed.get("status") or reviewed_task.get("queue_state")),
        "would_write": not blocking_reasons,
        "would_move": False,
        "updated_frontmatter": updated_reviewed,
        "rendered_markdown": reviewed_markdown,
        "target_file_state": _target_file_state(repo_root, reviewed_rel),
        "audit_task_id": audit_task.get("task_id"),
        "audit_task_path": audit_rel,
        "audit_task_markdown": audit_markdown,
        "audit_task_metadata": updated_audit,
        "verdict_id": verdict_id,
        "verdict": normalized_verdict,
        "audit_verdict_record_path": verdict_rel,
        "audit_session_record_path": session_rel,
        "record_writes": [_mcp_record_write_plan(verdict_rel, "audit_verdict_record", would_write=not blocking_reasons)],
        "record_updates": [_mcp_record_write_plan(session_rel, "session_record", would_update=not blocking_reasons)] if session_rel else [],
        "record_previews": [
            {"path": verdict_rel, "record_type": "audit_verdict_record", "rendered_markdown": verdict_markdown},
            {"path": session_rel, "record_type": "session_record", "rendered_markdown": session_markdown},
        ],
        "owner_policy_ref": owner_policy_ref,
        "canonical_agent_instance": canonical_agent_instance,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_return_record_ref": return_ref,
        "audit_dispatch_record_ref": dispatch_ref,
        "original_payload": {
            "audit_task_id": audit_task_id,
            "audit_task_path": str(audit_task_path) if audit_task_path is not None else None,
            "reviewed_task_id": reviewed_task_id,
            "actor": actor,
            "agent_instance": agent_instance,
            "owner_policy_ref": owner_policy_ref,
            "audit_claim_id": audit_claim_id,
            "audit_session_id": audit_session_id,
            "audit_dispatch_record_ref": audit_dispatch_record_ref,
            "reviewed_return_record_ref": reviewed_return_record_ref,
            "verdict": verdict_value,
            "findings_summary": findings_summary,
            "evidence_refs": evidence_refs,
            "recommended_next_action": recommended_next_action,
            "owner_waiver_ref": owner_waiver_ref,
            "planned_verdict_id": verdict_id,
            "planned_verdict_at": timestamp,
        },
    }
    verdict = derive_verdict(blocking_reasons=blocking_reasons, warnings=warnings)
    response = make_response(
        ok=True,
        verdict=verdict,
        operation="audit_verdict",
        dry_run=dry_run,
        actor=_actor_payload(actor),
        data=data,
        summary={"reviewed_task_id": reviewed_task.get("task_id"), "audit_task_id": audit_task.get("task_id"), "verdict": normalized_verdict},
        planned_writes=[
            {"path": reviewed_rel, "kind": "update", "type": "task_markdown"},
            {"path": audit_rel, "kind": "update", "type": "task_markdown"},
            {"path": verdict_rel, "kind": "create", "type": "record_markdown", "record_type": "audit_verdict_record"},
            {"path": session_rel, "kind": "update", "type": "record_markdown", "record_type": "session_record"},
        ],
        planned_moves=[],
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=[],
        owner_confirmation_required=verdict != "BLOCK",
        owner_confirmation_reasons=_verdict_owner_reasons() if verdict != "BLOCK" else [],
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    return response


def audit_verdict_task(
    *,
    audit_task_id: str | None = None,
    audit_task_path: str | Path | None = None,
    reviewed_task_id: str | None = None,
    actor: str | None = None,
    agent_instance: str | None = None,
    owner_policy_ref: str | None = None,
    audit_claim_id: str | None = None,
    audit_session_id: str | None = None,
    audit_dispatch_record_ref: str | None = None,
    reviewed_return_record_ref: str | None = None,
    verdict: str | None = None,
    findings_summary: str | None = None,
    evidence_refs: Any = None,
    recommended_next_action: str | None = None,
    owner_waiver_ref: str | None = None,
    planned_verdict_id: str | None = None,
    planned_verdict_at: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        actor_text = str(actor or "").strip()
        instance_text = str(agent_instance or "").strip()
        policy_ref = str(owner_policy_ref or "").strip()
        reviewed_id = str(reviewed_task_id or "").strip()
        verdict_text = str(verdict or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        if not instance_text:
            raise ValueError("agent_instance is required")
        if not policy_ref:
            raise ValueError("owner_policy_ref is required")
        if not reviewed_id:
            raise ValueError("reviewed_task_id is required")
        if not verdict_text:
            raise ValueError("verdict is required")
        resolved_root = _resolve_repo_root(repo_root)
        response = _build_audit_verdict_preview(
            audit_task_id=audit_task_id,
            audit_task_path=audit_task_path,
            reviewed_task_id=reviewed_id,
            actor=actor_text,
            agent_instance=instance_text,
            owner_policy_ref=policy_ref,
            audit_claim_id=str(audit_claim_id or "").strip() or None,
            audit_session_id=str(audit_session_id or "").strip() or None,
            audit_dispatch_record_ref=str(audit_dispatch_record_ref or "").strip() or None,
            reviewed_return_record_ref=str(reviewed_return_record_ref or "").strip() or None,
            verdict_value=verdict_text,
            findings_summary=str(findings_summary or "").strip() or None,
            evidence_refs=_as_list(evidence_refs),
            recommended_next_action=str(recommended_next_action or "").strip() or None,
            owner_waiver_ref=str(owner_waiver_ref or "").strip() or None,
            planned_verdict_id=str(planned_verdict_id or "").strip() or None,
            planned_verdict_at=str(planned_verdict_at or "").strip() or None,
            repo_root=resolved_root,
            dry_run=dry_run,
        )
        if dry_run:
            return _attach_controlled_execute_metadata(
                operation="audit_verdict",
                actor=actor_text,
                response=response,
                execute_allowed=response.get("verdict") != "BLOCK",
            )
        if response.get("verdict") == "BLOCK":
            return response
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        (resolved_root / str(data.get("target_path") or "")).write_text(str(data.get("rendered_markdown") or ""), encoding="utf-8")
        (resolved_root / str(data.get("audit_task_path") or "")).write_text(str(data.get("audit_task_markdown") or ""), encoding="utf-8")
        performed = [
            {"path": data.get("target_path"), "kind": "update", "type": "task_markdown"},
            {"path": data.get("audit_task_path"), "kind": "update", "type": "task_markdown"},
        ]
        for preview in data.get("record_previews", []):
            path = resolved_root / str(preview.get("path") or "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(preview.get("rendered_markdown") or ""), encoding="utf-8")
            kind = "create" if preview.get("record_type") == "audit_verdict_record" else "update"
            performed.append({"path": str(preview.get("path")), "kind": kind, "type": "record_markdown", "record_type": preview.get("record_type")})
        response["dry_run"] = False
        response["data"]["wrote"] = True
        _mark_record_write_report_performed(response["data"])
        response["performed_writes"] = performed
        response["owner_confirmation_required"] = False
        response["owner_confirmation_reasons"] = []
        return response
    except Exception as exc:
        return _normalize_exception("audit_verdict", exc, dry_run=dry_run, actor=_actor_payload(actor))


def _append_response(
    *,
    operation: str,
    actor: str | None,
    result: dict[str, Any],
    original_payload: Mapping[str, Any],
    target_file_state: dict[str, Any],
) -> dict[str, Any]:
    verdict = derive_verdict(
        blocking_reasons=list(result.get("blocking_reasons", [])),
        warnings=list(result.get("warnings", [])),
    )
    data = {
        **result,
        "original_payload": dict(original_payload),
        "target_path": result.get("target_path"),
        "write_snapshot_hash": result.get("write_snapshot_hash"),
        "target_file_state": target_file_state,
        "append_only": True,
        "controlled_persistence_gate": "AIPOS-77",
    }
    owner_reasons = ["AIPOS-77 append-only planner loop persistence requires explicit Owner confirmation."]
    response = make_response(
        ok=verdict != "BLOCK",
        verdict=verdict,
        operation=operation,
        dry_run=True,
        actor=_actor_payload(actor),
        data=data,
        summary={
            "target_path": result.get("target_path"),
            "write_snapshot_hash": result.get("write_snapshot_hash"),
            "controlled_persistence_gate": "AIPOS-77",
        },
        planned_writes=list(result.get("planned_writes", [])),
        planned_moves=[],
        warnings=list(result.get("warnings", [])),
        blocking_reasons=list(result.get("blocking_reasons", [])),
        needs_owner_reasons=owner_reasons if verdict != "BLOCK" else [],
        owner_confirmation_required=verdict != "BLOCK",
        owner_confirmation_reasons=owner_reasons if verdict != "BLOCK" else [],
        execute_allowed=verdict != "BLOCK",
        execute_blocking_reasons=list(result.get("blocking_reasons", [])),
        dry_run_token=None,
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    return _attach_controlled_execute_metadata(
        operation=operation,
        actor=actor,
        response=response,
        execute_allowed=verdict != "BLOCK",
    )


def append_orchestration_event(
    payload: Mapping[str, Any],
    *,
    actor: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "orchestration_event_append"
    if not dry_run:
        return _blocked_execute(operation, actor=actor)
    try:
        resolved_root = _resolve_repo_root(repo_root)
        result = backend_append_orchestration_event(resolved_root, dict(payload), actor=actor, dry_run=True)
        return _append_response(
            operation=operation,
            actor=actor,
            result=result,
            original_payload=payload,
            target_file_state=_target_file_state(resolved_root, result.get("target_path")),
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=True, actor=_actor_payload(actor))


def append_planner_iteration(
    payload: Mapping[str, Any],
    *,
    actor: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "planner_iteration_append"
    if not dry_run:
        return _blocked_execute(operation, actor=actor)
    try:
        resolved_root = _resolve_repo_root(repo_root)
        result = backend_append_planner_iteration(resolved_root, dict(payload), actor=actor, dry_run=True)
        return _append_response(
            operation=operation,
            actor=actor,
            result=result,
            original_payload=payload,
            target_file_state=_target_file_state(resolved_root, result.get("target_path")),
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=True, actor=_actor_payload(actor))


def execute_dry_run(
    dry_run_id: str,
    actor: str,
    owner_confirmation_token: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    operation = "execute_dry_run"
    actor_text = str(actor or "").strip()
    try:
        if not str(dry_run_id or "").strip():
            raise ValueError("dry_run_id is required")
        if not actor_text:
            raise ValueError("actor is required")
        token = get_dry_run(dry_run_id)
        if token is None:
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="DRY_RUN_REQUIRED",
                message="dry_run_id not found; run dry-run again",
                actor=_actor_payload(actor_text),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )
        if token.operation not in {
            "draft_create",
            "draft_publish",
            "queue_claim",
            "queue_return",
            "audit_dispatch",
            "audit_verdict",
            "orchestration_event_append",
            "planner_iteration_append",
            "intake_submit",
            "owner_decision_record",
            TEMPLATE_OPERATION,
        }:
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="UNSUPPORTED_OPERATION",
                message=f"Unsupported controlled execute operation: {token.operation}",
                actor=_actor_payload(actor_text),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )
        if is_expired(token):
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="REVALIDATION_FAILED",
                message="dry-run token expired; run dry-run again",
                actor=_actor_payload(actor_text),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )
        if token.actor != actor_text:
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="ACTOR_MISMATCH",
                message="execute actor does not match dry-run actor",
                actor=_actor_payload(actor_text),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )

        resolved_root = _resolve_repo_root(repo_root)
        source_plan = token.plan
        source_data = source_plan.get("data") or {}
        op = token.operation

        if bool(source_data.get("with_records", False)):
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="UNSUPPORTED_OPERATION",
                message="with_records execute is not enabled in AIPOS-38",
                actor=_actor_payload(actor_text),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )

        if op == "draft_create":
            payload = source_data.get("original_payload") or {}
            current = create_draft(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "draft_publish":
            source_path = source_data.get("source_path")
            current = publish_draft(source_path, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "orchestration_event_append":
            payload = source_data.get("original_payload") or {}
            current = append_orchestration_event(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "planner_iteration_append":
            payload = source_data.get("original_payload") or {}
            current = append_planner_iteration(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "intake_submit":
            payload = source_data.get("original_payload") or {}
            current = submit_external_intake(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "owner_decision_record":
            payload = source_data.get("original_payload") or {}
            current = record_owner_decision(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "queue_return":
            payload = source_data.get("original_payload") or {}
            mcp_return_metadata = source_data.get("mcp_return") if isinstance(source_data.get("mcp_return"), dict) else None
            current = return_task(
                task_id=payload.get("task_id"),
                path=payload.get("path"),
                actor=actor_text,
                agent_instance=payload.get("agent_instance"),
                owner_policy_ref=payload.get("owner_policy_ref"),
                claim_id=payload.get("claim_id"),
                active_session_id=payload.get("active_session_id"),
                result_summary=payload.get("result_summary"),
                artifact_refs=payload.get("artifact_refs"),
                completion_report_ref=payload.get("completion_report_ref"),
                return_reason=payload.get("return_reason"),
                planned_returned_at=payload.get("planned_returned_at"),
                dry_run=True,
                repo_root=resolved_root,
                mcp_return_metadata=mcp_return_metadata,
            )
        elif op == "audit_dispatch":
            payload = source_data.get("original_payload") or {}
            current = audit_dispatch_task(
                source_task_id=payload.get("source_task_id"),
                source_path=payload.get("source_path"),
                actor=actor_text,
                agent_instance=payload.get("agent_instance"),
                owner_policy_ref=payload.get("owner_policy_ref"),
                audit_task_id=payload.get("audit_task_id"),
                audit_task_title=payload.get("audit_task_title"),
                audit_by=payload.get("audit_by"),
                audit_agent_instance=payload.get("audit_agent_instance"),
                dispatch_reason=payload.get("dispatch_reason"),
                planned_dispatch_id=payload.get("planned_dispatch_id"),
                planned_dispatched_at=payload.get("planned_dispatched_at"),
                dry_run=True,
                repo_root=resolved_root,
            )
        elif op == "audit_verdict":
            payload = source_data.get("original_payload") or {}
            current = audit_verdict_task(
                audit_task_id=payload.get("audit_task_id"),
                audit_task_path=payload.get("audit_task_path"),
                reviewed_task_id=payload.get("reviewed_task_id"),
                actor=actor_text,
                agent_instance=payload.get("agent_instance"),
                owner_policy_ref=payload.get("owner_policy_ref"),
                audit_claim_id=payload.get("audit_claim_id"),
                audit_session_id=payload.get("audit_session_id"),
                audit_dispatch_record_ref=payload.get("audit_dispatch_record_ref"),
                reviewed_return_record_ref=payload.get("reviewed_return_record_ref"),
                verdict=payload.get("verdict"),
                findings_summary=payload.get("findings_summary"),
                evidence_refs=payload.get("evidence_refs"),
                recommended_next_action=payload.get("recommended_next_action"),
                owner_waiver_ref=payload.get("owner_waiver_ref"),
                planned_verdict_id=payload.get("planned_verdict_id"),
                planned_verdict_at=payload.get("planned_verdict_at"),
                dry_run=True,
                repo_root=resolved_root,
            )
        elif op == TEMPLATE_OPERATION:
            payload = source_data.get("original_payload") or {}
            current = build_workspace_init_plan(
                template=str(payload.get("template") or ""),
                output=str(payload.get("output") or ""),
                variables=payload.get("variables") if isinstance(payload.get("variables"), dict) else {},
                actor=actor_text,
                dry_run=True,
            )
        else:
            claim_task_id = source_data.get("task_id")
            claim_path = None if claim_task_id else source_data.get("source_path")
            mcp_claim_metadata = source_data.get("mcp_claim") if isinstance(source_data.get("mcp_claim"), dict) else None
            current = claim_task(
                task_id=claim_task_id,
                path=claim_path,
                actor=actor_text,
                dry_run=True,
                with_records=False,
                repo_root=resolved_root,
                owner_confirmation_required_override=True if mcp_claim_metadata else None,
                owner_confirmation_reasons_override=(
                    list(mcp_claim_metadata.get("owner_confirmation_reasons", []))
                    if mcp_claim_metadata
                    else None
                ),
                mcp_claim_metadata=mcp_claim_metadata,
            )

        current_hash = snapshot_hash(op, actor_text, current)
        expected_hash = token.snapshot_hash
        if current_hash != expected_hash:
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="REVALIDATION_FAILED",
                message="dry-run snapshot mismatch; run dry-run again",
                actor=_actor_payload(actor_text),
                data={
                    "expected_dry_run_snapshot_hash": expected_hash,
                    "current_snapshot_hash": current_hash,
                    "recommended_action": "run dry-run again",
                },
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )

        owner_required = bool(source_plan.get("owner_confirmation_required", False))
        ok_owner, owner_error = validate_owner_confirmation(
            required=owner_required,
            owner_confirmation_token=owner_confirmation_token,
        )
        if not ok_owner:
            return blocked_response(
                operation=operation,
                dry_run=False,
                category="OWNER_CONFIRMATION_REQUIRED",
                message=owner_error or "owner confirmation required",
                actor=_actor_payload(actor_text),
                owner_confirmation_required=True,
                owner_confirmation_reasons=list(source_plan.get("owner_confirmation_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
            )

        if op == "draft_create":
            payload = source_data.get("original_payload") or {}
            metadata, body = _coerce_draft_payload(payload)
            result = backend_create_draft(resolved_root, metadata, body, dry_run=False)
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("wrote", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result,
                summary={"task_id": result.get("task_id"), "wrote": result.get("wrote", False)},
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )
        if op == "draft_publish":
            result = backend_publish_draft(
                resolved_root,
                source_data.get("source_path"),
                dry_run=False,
                actor=actor_text,
            )
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("wrote", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result,
                summary={"task_id": result.get("task_id"), "wrote": result.get("wrote", False)},
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )
        if op == "orchestration_event_append":
            payload = source_data.get("original_payload") or {}
            result = backend_append_orchestration_event(
                resolved_root,
                payload,
                actor=actor_text,
                dry_run=False,
                expected_hash=source_data.get("write_snapshot_hash"),
            )
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("wrote", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result,
                summary={"target_path": result.get("target_path"), "wrote": result.get("wrote", False)},
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )
        if op == "planner_iteration_append":
            payload = source_data.get("original_payload") or {}
            result = backend_append_planner_iteration(
                resolved_root,
                payload,
                actor=actor_text,
                dry_run=False,
                expected_hash=source_data.get("write_snapshot_hash"),
            )
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("wrote", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result,
                summary={"target_path": result.get("target_path"), "wrote": result.get("wrote", False)},
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if op == "intake_submit":
            payload = source_data.get("original_payload") or {}
            result = backend_build_external_intake_draft(
                resolved_root,
                payload,
                actor=actor_text,
                dry_run=False,
            )
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("wrote", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result,
                summary={
                    "safe_id": result.get("safe_id"),
                    "task_id": result.get("task_id"),
                    "target_path": result.get("target_path"),
                    "wrote": result.get("wrote", False),
                },
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if op == "owner_decision_record":
            payload = source_data.get("original_payload") or {}
            result = backend_build_owner_decision_record(
                resolved_root,
                payload,
                actor=actor_text,
                dry_run=False,
            )
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("wrote", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result,
                summary={
                    "decision_id": result.get("decision_id"),
                    "target_path": result.get("target_path"),
                    "wrote": result.get("wrote", False),
                },
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if op == "queue_return":
            payload = source_data.get("original_payload") or {}
            mcp_return_metadata = source_data.get("mcp_return") if isinstance(source_data.get("mcp_return"), dict) else None
            result = return_task(
                task_id=payload.get("task_id"),
                path=payload.get("path"),
                actor=actor_text,
                agent_instance=payload.get("agent_instance"),
                owner_policy_ref=payload.get("owner_policy_ref"),
                claim_id=payload.get("claim_id"),
                active_session_id=payload.get("active_session_id"),
                result_summary=payload.get("result_summary"),
                artifact_refs=payload.get("artifact_refs"),
                completion_report_ref=payload.get("completion_report_ref"),
                return_reason=payload.get("return_reason"),
                planned_returned_at=payload.get("planned_returned_at"),
                dry_run=False,
                repo_root=resolved_root,
                mcp_return_metadata=mcp_return_metadata,
            )
            verdict = str(result.get("verdict") or "BLOCK")
            return make_response(
                ok=bool(result.get("data", {}).get("wrote", False)) if isinstance(result.get("data"), dict) else False,
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result.get("data"),
                summary=result.get("summary"),
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("performed_writes", [])),
                planned_moves=[],
                performed_moves=[],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if op == "audit_dispatch":
            payload = source_data.get("original_payload") or {}
            result = audit_dispatch_task(
                source_task_id=payload.get("source_task_id"),
                source_path=payload.get("source_path"),
                actor=actor_text,
                agent_instance=payload.get("agent_instance"),
                owner_policy_ref=payload.get("owner_policy_ref"),
                audit_task_id=payload.get("audit_task_id"),
                audit_task_title=payload.get("audit_task_title"),
                audit_by=payload.get("audit_by"),
                audit_agent_instance=payload.get("audit_agent_instance"),
                dispatch_reason=payload.get("dispatch_reason"),
                planned_dispatch_id=payload.get("planned_dispatch_id"),
                planned_dispatched_at=payload.get("planned_dispatched_at"),
                dry_run=False,
                repo_root=resolved_root,
            )
            verdict = str(result.get("verdict") or "BLOCK")
            return make_response(
                ok=bool(result.get("data", {}).get("wrote", False)) if isinstance(result.get("data"), dict) else False,
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result.get("data"),
                summary=result.get("summary"),
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("performed_writes", [])),
                planned_moves=[],
                performed_moves=[],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if op == "audit_verdict":
            payload = source_data.get("original_payload") or {}
            result = audit_verdict_task(
                audit_task_id=payload.get("audit_task_id"),
                audit_task_path=payload.get("audit_task_path"),
                reviewed_task_id=payload.get("reviewed_task_id"),
                actor=actor_text,
                agent_instance=payload.get("agent_instance"),
                owner_policy_ref=payload.get("owner_policy_ref"),
                audit_claim_id=payload.get("audit_claim_id"),
                audit_session_id=payload.get("audit_session_id"),
                audit_dispatch_record_ref=payload.get("audit_dispatch_record_ref"),
                reviewed_return_record_ref=payload.get("reviewed_return_record_ref"),
                verdict=payload.get("verdict"),
                findings_summary=payload.get("findings_summary"),
                evidence_refs=payload.get("evidence_refs"),
                recommended_next_action=payload.get("recommended_next_action"),
                owner_waiver_ref=payload.get("owner_waiver_ref"),
                planned_verdict_id=payload.get("planned_verdict_id"),
                planned_verdict_at=payload.get("planned_verdict_at"),
                dry_run=False,
                repo_root=resolved_root,
            )
            verdict = str(result.get("verdict") or "BLOCK")
            return make_response(
                ok=bool(result.get("data", {}).get("wrote", False)) if isinstance(result.get("data"), dict) else False,
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result.get("data"),
                summary=result.get("summary"),
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("performed_writes", [])),
                planned_moves=[],
                performed_moves=[],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if op == TEMPLATE_OPERATION:
            payload = source_data.get("original_payload") or {}
            variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
            result = execute_workspace_init(
                template=str(payload.get("template") or ""),
                output=str(payload.get("output") or ""),
                variables={str(key): str(value) for key, value in variables.items()},
                actor=actor_text,
            )
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
            return make_response(
                ok=bool(result.get("ok", False)),
                verdict=verdict,
                operation=op,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data=result.get("data"),
                summary=result.get("summary"),
                planned_writes=list(result.get("planned_writes", [])),
                performed_writes=list(result.get("performed_writes", [])),
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        claim_task_id = source_data.get("task_id")
        claim_path = None if claim_task_id else source_data.get("source_path")
        mcp_claim_metadata = source_data.get("mcp_claim") if isinstance(source_data.get("mcp_claim"), dict) else None
        result = mutate_queue_task(
            resolved_root,
            "claim",
            task_id=claim_task_id,
            task_path=claim_path,
            actor=actor_text,
            dry_run=False,
            with_records=False,
            profiles=load_agent_profiles(resolved_root),
            claim_id_override=(
                str(mcp_claim_metadata.get("planned_claim_id") or "").strip()
                if isinstance(mcp_claim_metadata, dict)
                else None
            ),
            session_id_override=(
                str(mcp_claim_metadata.get("planned_session_id") or "").strip()
                if isinstance(mcp_claim_metadata, dict)
                else None
            ),
        )
        record_performed_writes: list[dict[str, Any]] = []
        if result.get("wrote") and isinstance(mcp_claim_metadata, dict) and bool(source_data.get("mcp_records_enabled")):
            record_plan = _mcp_claim_record_plan(
                repo_root=resolved_root,
                task_id=str(result.get("task_id") or ""),
                task_path=str(result.get("target_path") or ""),
                actor=actor_text,
                canonical_agent_instance=str(mcp_claim_metadata.get("canonical_agent_instance") or actor_text),
                owner_policy_ref=str(mcp_claim_metadata.get("owner_policy_ref") or ""),
                updated_metadata=result.get("updated_frontmatter") if isinstance(result.get("updated_frontmatter"), dict) else {},
                dry_run_id=dry_run_id,
                dry_run_snapshot_hash=expected_hash,
            )
            if record_plan.get("record_blocking_reasons"):
                for reason_text in record_plan.get("record_blocking_reasons", []):
                    if reason_text not in result["blocking_reasons"]:
                        result["blocking_reasons"].append(reason_text)
                result["verdict"] = "BLOCK"
            else:
                record_performed_writes = _write_mcp_claim_records(resolved_root, record_plan)
                result["records_enabled"] = True
                result["mcp_records_enabled"] = True
                result["record_writes"] = record_plan["record_writes"]
                _mark_record_write_report_performed(result)
                result["claim_record_path"] = record_plan["claim_record_path"]
                result["session_record_path"] = record_plan["session_record_path"]
        verdict = str(result.get("verdict") or "BLOCK")
        planned_record_writes = [
            {"path": item.get("path"), "kind": "create", "type": "record_markdown", "record_type": item.get("record_type")}
            for item in source_data.get("record_writes", [])
            if isinstance(item, dict)
        ]
        return make_response(
            ok=bool(result.get("wrote", False)),
            verdict=verdict,
            operation=op,
            dry_run=False,
            actor=_actor_payload(actor_text),
            data=result,
            summary={"task_id": result.get("task_id"), "moved": result.get("moved", False)},
            planned_writes=list(result.get("planned_writes", [])) + planned_record_writes,
            planned_moves=list(result.get("planned_moves", [])),
            performed_writes=(list(result.get("planned_writes", [])) if result.get("wrote") else []) + record_performed_writes,
            performed_moves=list(result.get("planned_moves", [])) if result.get("moved") else [],
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            safety_notice=CONTROLLED_EXECUTE_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False, actor=_actor_payload(actor_text))


def claim_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    dry_run: bool = True,
    with_records: bool = False,
    repo_root: str | Path | None = None,
    owner_confirmation_required_override: bool | None = None,
    owner_confirmation_reasons_override: list[str] | None = None,
    mcp_claim_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return _queue_mutation_preview(
            operation="queue_claim",
            action="claim",
            task_id=task_id,
            path=path,
            actor=actor,
            dry_run=dry_run,
            with_records=with_records,
            repo_root=repo_root,
            owner_confirmation_required_override=owner_confirmation_required_override,
            owner_confirmation_reasons_override=owner_confirmation_reasons_override,
            mcp_claim_metadata=mcp_claim_metadata,
        )
    except Exception as exc:
        return _normalize_exception("queue_claim", exc, dry_run=dry_run, actor=_actor_payload(actor))


def block_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    reason: str | None = None,
    dry_run: bool = True,
    with_records: bool = False,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        return _queue_mutation_preview(
            operation="queue_block",
            action="block",
            task_id=task_id,
            path=path,
            actor=actor,
            dry_run=dry_run,
            with_records=with_records,
            repo_root=repo_root,
            reason=reason,
        )
    except Exception as exc:
        return _normalize_exception("queue_block", exc, dry_run=dry_run, actor=_actor_payload(actor))


def complete_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    dry_run: bool = True,
    with_records: bool = False,
    repo_root: str | Path | None = None,
    report_link: str | None = None,
) -> dict[str, Any]:
    try:
        return _queue_mutation_preview(
            operation="queue_complete",
            action="complete",
            task_id=task_id,
            path=path,
            actor=actor,
            dry_run=dry_run,
            with_records=with_records,
            repo_root=repo_root,
            report_link=report_link or "adapter://report-link-required",
        )
    except Exception as exc:
        return _normalize_exception("queue_complete", exc, dry_run=dry_run, actor=_actor_payload(actor))


def reopen_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    dry_run: bool = True,
    with_records: bool = False,
    repo_root: str | Path | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    try:
        return _queue_mutation_preview(
            operation="queue_reopen",
            action="reopen",
            task_id=task_id,
            path=path,
            actor=actor,
            dry_run=dry_run,
            with_records=with_records,
            repo_root=repo_root,
            reason=reason or "adapter preview reopen",
        )
    except Exception as exc:
        return _normalize_exception("queue_reopen", exc, dry_run=dry_run, actor=_actor_payload(actor))
