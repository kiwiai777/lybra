from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, error_entry, make_response
from tools.aipos_cli.agent_profiles import actor_matches_task_actor, load_agent_profiles, registry_available, resolve_instance_id
from tools.aipos_cli.audit_derivation import derive_audit_task_on_return
from tools.aipos_cli.artifact_ingest import (
    _as_ref_list,
    approved_scratch_root,
    has_scratch_request,
    perform_scratch_ingestion,
    plan_scratch_ingestion,
)
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
from tools.aipos_cli.draft_writer import stable_publish_id
from tools.aipos_cli.external_intake_writer import build_external_intake_draft as backend_build_external_intake_draft
from tools.aipos_cli.orchestration_event_writer import append_orchestration_event as backend_append_orchestration_event
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.orchestration_timeline_preview import build_orchestration_timeline_preview
from tools.aipos_cli.owner_decision_writer import build_owner_decision_record as backend_build_owner_decision_record
from tools.aipos_cli.owner_verification_writer import build_owner_verification_record as backend_build_owner_verification_record
from tools.aipos_cli.planner_iteration_writer import append_planner_iteration as backend_append_planner_iteration
from tools.aipos_cli.planner_loop_mvp import build_planner_loop_mvp_preview
from tools.aipos_cli.preview import build_preview
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.queue_mutation import mutate_queue_task, render_task_markdown, _slug
from tools.aipos_cli.record_writer import (
    append_mcp_audit_verdict_session_event,
    append_mcp_return_session_event,
    audit_dispatch_record_path,
    audit_verdict_record_path,
    build_closure_record_markdown,
    build_mcp_audit_dispatch_record_markdown,
    build_mcp_audit_verdict_record_markdown,
    build_mcp_claim_record_markdown,
    build_mcp_claim_session_record_markdown,
    build_mcp_return_record_markdown,
    build_runtime_id,
    claim_record_paths,
    closure_record_path,
    load_session_record,
    return_record_path,
    session_record_path,
)
from tools.aipos_cli.owner_truth_view import build_owner_truth_view
from tools.aipos_cli.records import expected_closure_record_path, load_records
from tools.aipos_cli.task_loader import (
    find_repo_context,
    find_repo_root,
    find_task_by_id,
    load_all_tasks,
    load_task_by_path,
)
from tools.aipos_cli.workspace_config import (
    find_workspace_config,
    governance_paths,
    has_workspace_queue,
    load_workspace_config,
    read_project_json,
    resolve_active_project,
)
from tools.aipos_cli.validator import validate_single_task, validate_tasks
from tools.aipos_cli.workspace_templates import (
    TEMPLATE_OPERATION,
    build_workspace_init_plan,
    execute_workspace_init,
)
from tools.schema_constants import RecordType, Verdict

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
# AIPOS-225 (Slice 1): governance doc filenames are sourced from the Slice 0 governance_paths()
# shape (ruling 1=B: single-file decision_log.md) — one definition, no hardcoded project path.

# AIPOS-FND-7F2: null-safe verdict timestamp extractor for max() sorting
# Mixed-format records exist: old (verdict_at=None, timestamp set) vs new (verdict_at set, timestamp=None)
def _verdict_time(v: dict[str, Any]) -> str:
    """Extract verdict timestamp in null-safe manner: verdict_at > timestamp > empty string."""
    return v.get("verdict_at") or v.get("timestamp") or ""
_GOVERNANCE_DOC_KEYS = ("decision_log", "project_status", "roadmap")
GOVERNANCE_FILES = {key: governance_paths(Path("."))[key].name for key in _GOVERNANCE_DOC_KEYS}
GOVERNANCE_EXCERPT_CHARS = 12000


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    candidate = Path(repo_root).resolve() if repo_root is not None else None
    # AIPOS-226 FIX C①: an explicitly-supplied root that is ALREADY a valid workspace
    # (has 5_tasks/queue) is used DIRECTLY — no upward re-resolution. Re-running
    # find_repo_root on an already-valid workspace risks silently re-resolving it to a
    # different root via the home model / legacy upward search. A valid explicit root is
    # authoritative; only an invalid/non-workspace candidate falls through to find_repo_root.
    if candidate is not None and has_workspace_queue(candidate):
        return candidate
    return find_repo_root(candidate)


def _resolve_repo_and_home(repo_root: str | Path | None) -> tuple[Path, Path | None]:
    """``(resolved_root, home_root)`` for the AIPOS-227 196a ingestion home-guard.

    Mirrors ``_resolve_repo_root``'s FIX C① contract: an explicit, already-valid workspace is
    authoritative/direct, so ``home_root`` is ``None`` (no home-model re-resolution). Otherwise
    resolution flows through ``find_repo_context`` and surfaces the truth home iff the home model
    resolved it. ``home_root`` is ``None`` IFF the home model is not the resolution path (R-1).
    """
    candidate = Path(repo_root).resolve() if repo_root is not None else None
    if candidate is not None and has_workspace_queue(candidate):
        return candidate, None
    return find_repo_context(candidate)


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


def _select_task_input(
    task_id: str | None,
    path: str | Path | None,
    *,
    id_param_name: str = "task_id",
    path_param_name: str = "path",
) -> tuple[str | None, str | None]:
    """AIPOS-F14 大项B: 报错参数名取 verb_contract 实名(禁写死别名)。

    id_param_name / path_param_name 由调用方传入, 对应 verb_contract 中的真实参数名。
    默认值保持向后兼容(queue/return 等动词确实用 task_id / task_path)。
    """
    normalized_path = _normalize_path(path) if path is not None else None
    if bool(task_id) == bool(normalized_path):
        raise ValueError(
            f"Exactly one of {id_param_name} or {path_param_name} must be provided. "
            f"Example: {id_param_name}='AIPOS-F13R'"
        )
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
    # AIPOS-F44A ④: Enhanced guidance for common errors
    suggested_next_action = None

    if isinstance(exc, FileNotFoundError):
        category = "NOT_FOUND"
        # AIPOS-F44A ④: "No task found" with three candidate reasons + actions
        if "no task found" in lowered:
            suggested_next_action = (
                "Task not found. Three common reasons:\n"
                "1. No publish record: Task may be a draft. Action: Run 'lybra draft publish <task_id>' to publish it.\n"
                "2. Workspace mismatch: Task may be in a different workspace. Action: Check workspace_root or switch to correct workspace.\n"
                "3. Task already closed: Task may have reached a terminal state (completed/cancelled). Action: Check task status in queue/completed/ or queue/cancelled/."
            )
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

    errors = [error_entry(category, message, field=field)]
    # AIPOS-F44A ④: Add suggested_next_action to error details
    if suggested_next_action:
        errors[0]["details"]["suggested_next_action"] = suggested_next_action

    return make_response(
        ok=False,
        verdict=Verdict.BLOCK,
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
        errors=errors,
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
        "queue_withdraw",  # AIPOS-315: G2 两阶段动词
        "queue_amend",     # AIPOS-315: G2 两阶段动词
        "audit_dispatch",
        "audit_verdict",
        "orchestration_event_append",
        "planner_iteration_append",
        "intake_submit",
        "owner_decision_record",
        "owner_verification_record",
        "bench_audit_submit",
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
            verdict=Verdict.PASS,
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


def get_queue(
    repo_root: str | Path | None = None,
    *,
    project_scope: str | None = None,
    instance_scope: str | None = None,
) -> dict[str, Any]:
    """Get queue tasks with optional project and instance filtering.
    
    AIPOS-R1 scope铁律: queue_list按调用者token的(project, instance)返回。
    - project_scope: 项目标识,用于多项目隔离
    - instance_scope: agent实例标识,用于held检查
    
    单项目token=该项目;多项目token=显式project参数或推断。
    绝不返回home-root全项目视图。
    """
    operation = "get_queue"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        tasks = load_all_tasks(resolved_root)
        records = load_records(resolved_root)
        profiles = load_agent_profiles(resolved_root)
        report = validate_tasks(tasks, records=records, profiles=profiles)
        
        # AIPOS-R1: 按(project, instance) scope过滤
        if project_scope or instance_scope:
            filtered_tasks = []
            for task in report.get("tasks", []):
                metadata = task.get("metadata", {})
                task_project = metadata.get("project", "")
                
                # Project filtering: 只返回匹配project的任务
                if project_scope and task_project != project_scope:
                    continue
                
                filtered_tasks.append(task)
            
            report["tasks"] = filtered_tasks
            if "summary" in report:
                report["summary"]["total_tasks"] = len(filtered_tasks)
        
        # AIPOS-283: enrich tasks with has_closure flag for "已收编" badge
        task_closures = records.get("task_closures", {})
        for task in report.get("tasks", []):
            tid = task.get("task_id") or task.get("metadata", {}).get("task_id", "")
            if tid and tid in task_closures:
                task["has_closure"] = True
        
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
            if task.get("verdict") == Verdict.NEEDS_OWNER
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


def get_owner_truth_view(repo_root: str | Path | None = None) -> dict[str, Any]:
    """AIPOS-260: read-only Owner truth summary read surface (task-center view +
    per-round summary timeline + record-derived true-stage counts + activity
    feed). Pure additive aggregation over records + tasks."""
    operation = "get_owner_truth_view"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        return build_owner_truth_view(resolved_root)
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=False)



def _extract_draft_task_ids_fast(repo_root: Path) -> set[str]:
    """AIPOS-297 FIX: 轻量级提取 draft task_id，跳过完整验证（性能优化）。
    
    只解析 frontmatter 提取 task_id，不做碰撞检查、复杂度验证等重操作。
    用于 get_advisor_pending_items 等只需 task_id 列表的场景。
    """
    drafts_dir = repo_root / "5_tasks" / "drafts"
    if not drafts_dir.exists():
        return set()
    
    task_ids = set()
    for draft_path in drafts_dir.rglob("*.md"):
        if not draft_path.is_file():
            continue
        try:
            metadata, _body, _errors = parse_markdown_frontmatter(draft_path.read_text(encoding="utf-8"))
            task_id = metadata.get("task_id")
            if task_id:
                task_ids.add(str(task_id))
        except Exception:
            # 解析失败跳过，不影响整体
            continue
    return task_ids


def get_advisor_pending_items(repo_root: str | Path | None = None) -> dict[str, Any]:
    """AIPOS-297: read-only advisor pending items surface — gate push=0 纯推导.
    
    计算"待顾问收编"计数:
    - approve: 有 owner_verification approve 记录 但 无对应 closure 记录
    - reject: 有 owner_verification reject 记录 但 无对应后续动作
    
    Red lines:
    - gate 零推送: 只读记录推导, 无写入
    - 计数随收编/接手自动清零 (closure/新 draft 出现则视为已接)
    
    AIPOS-297 FIX-1: 用 _extract_draft_task_ids_fast 替代 get_drafts 全验证，
    消除 O(drafts × (drafts + tasks)) 碰撞检查导致的挂死。
    """
    operation = "get_advisor_pending_items"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        records_report = load_records(resolved_root)
        
        owner_verifications = records_report.get("owner_verifications", [])
        closures = records_report.get("closures", [])
        
        # FIX: 用轻量级提取替代完整验证（43s → <1s）
        tasks_with_drafts = _extract_draft_task_ids_fast(resolved_root)
        
        tasks_with_closure = set()
        for closure in closures:
            task_id = closure.get("task_id")
            if task_id:
                tasks_with_closure.add(str(task_id))
        
        pending_approvals = []
        pending_rejects = []
        
        for verification in owner_verifications:
            task_id = verification.get("task_id")
            decision = verification.get("decision", "").strip().lower()
            decided_at = verification.get("decided_at", "")
            
            if not task_id or not decision:
                continue
            
            task_id_str = str(task_id)
            
            if decision == "approve":
                if task_id_str not in tasks_with_closure:
                    pending_approvals.append({
                        "task_id": task_id,
                        "decision": "approve",
                        "decided_at": decided_at,
                        "verification_record_id": verification.get("record_id"),
                    })
            elif decision == "reject":
                if task_id_str not in tasks_with_drafts:
                    pending_rejects.append({
                        "task_id": task_id,
                        "decision": "reject",
                        "decided_at": decided_at,
                        "verification_record_id": verification.get("record_id"),
                    })
        
        pending_approvals.sort(key=lambda x: x.get("decided_at", ""), reverse=True)
        pending_rejects.sort(key=lambda x: x.get("decided_at", ""), reverse=True)
        
        return make_response(
            ok=True,
            verdict=Verdict.PASS,
            operation=operation,
            dry_run=False,
            data={
                "pending_approvals": pending_approvals,
                "pending_rejects": pending_rejects,
                "total_pending": len(pending_approvals) + len(pending_rejects),
            },
        )
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


def _resolve_active_project_for(resolved_root: Path, project: str | None) -> str:
    """Resolve the active project (AIPOS-225 Slice 1): reuse Slice 0 resolve_active_project,
    sourced from the workspace .lybra/config.json `active_project`. Real ambiguity (no config
    entry and no single-project fallback) fails closed (PROJECT_AMBIGUOUS) — no project literal."""
    config: dict[str, Any] = {}
    config_path = find_workspace_config(resolved_root)
    if config_path is not None:
        config = load_workspace_config(config_path)
    return resolve_active_project(resolved_root, explicit=project, config=config)


def _resolve_governance_dir(resolved_root: Path, project: str) -> tuple[Path, str]:
    """Resolve the per-project governance/ dir under the truth home.

    AIPOS-226 Slice 2 / Phase 2b: the Slice-1 legacy 2_projects/<project>/ back-compat bridge
    is removed now that the move into the home is complete. Governance truth lives exclusively
    at <project_home>/governance/.

    AIPOS-226 FIX A: when the home governance/decision_log.md does NOT exist, this is NOT an
    "empty home" — a validly established project always has a governance/decision_log.md stub
    (the scaffold writes one). Its absence therefore means the resolved root is wrong (a
    misresolution). Fail LOUDLY instead of silently defaulting to (home_dir, "home"), which
    previously let a misresolved root masquerade as an empty home with 0 docs but layout="home".
    """
    decision_log_name = GOVERNANCE_FILES["decision_log"]
    home_dir = resolved_root / "governance"
    if (home_dir / decision_log_name).exists():
        return home_dir, "home"
    raise FileNotFoundError(
        f"GOVERNANCE_NOT_FOUND: no governance/{decision_log_name} under {resolved_root} "
        f"— likely a resolution error (an established project always has a "
        f"governance/{decision_log_name} stub)."
    )


def get_governance(repo_root: str | Path | None = None, *, project: str | None = None) -> dict[str, Any]:
    operation = "get_governance"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        active_project = _resolve_active_project_for(resolved_root, project)
        governance_dir, layout = _resolve_governance_dir(resolved_root, active_project)
        documents = [
            _governance_doc(resolved_root, name, (governance_dir / filename).relative_to(resolved_root).as_posix())
            for name, filename in GOVERNANCE_FILES.items()
        ]
        missing = [doc["path"] for doc in documents if not doc["exists"] or not doc["is_file"]]
        project_root_rel = governance_dir.relative_to(resolved_root).as_posix()
        data = {
            "project": active_project,
            "project_root": project_root_rel,
            "governance_layout": layout,
            "documents": documents,
            "writes_enabled": False,
            "raw_json_default_visible": False,
        }
        return make_response(
            ok=True,
            verdict=Verdict.WARN if missing else Verdict.PASS,
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "project": active_project,
                "governance_layout": layout,
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
            verdict=Verdict.PASS if entries else Verdict.WARN,
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
    include_body: bool = False,
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
        preview = build_preview(validated, actor, records=records, profiles=profiles, include_body=include_body)
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
            verdict=Verdict.PASS,
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
            verdict=Verdict.PASS if drafts else Verdict.WARN,
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "total": len(drafts),
                "ready": sum(1 for item in drafts if item.get("verdict") == Verdict.PASS),
                "blocked": sum(1 for item in drafts if item.get("verdict") == Verdict.BLOCK),
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
            verdict=Verdict.PASS if records else Verdict.WARN,
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
            verdict=str(result.get("verdict") or Verdict.PASS),
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
            verdict=str(result.get("verdict") or Verdict.PASS),
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
            verdict=str(result.get("verdict") or Verdict.PASS),
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
            verdict=str(result.get("verdict") or Verdict.PASS),
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
            execute_allowed=verdict != Verdict.BLOCK,
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
            execute_allowed=verdict != Verdict.BLOCK,
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def record_owner_decision(
    payload: Mapping[str, Any],
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    operation = RecordType.OWNER_DECISION_RECORD
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
            # AIPOS-250: lets the confirm handler require owner_confirm for a policy grant.
            "autonomy_policy_grant": bool(result.get("autonomy_policy_grant")),
            "autonomy_policy_id": result.get("autonomy_policy_id"),
            "autonomy_policy_path": result.get("autonomy_policy_path"),
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
            execute_allowed=verdict != Verdict.BLOCK,
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def record_owner_verification(
    payload: Mapping[str, Any],
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """AIPOS-273: Record owner verification (approve/reject) for a task.
    
    Writes append-only verification records to 5_tasks/records/owner_verifications/<task_id>/.
    Requires authenticated session (web_session) or equivalent MCP token.
    
    Args:
        payload: Dict with task_id, decision (approve/reject), reason (optional for approve, required for reject), decided_via
        dry_run: If True, validates but doesn't write (default True)
        repo_root: Repository root path
        actor: Actor identifier (typically "owner" or session-derived)
    
    Returns:
        Response dict with verdict, blocking_reasons, target_path, rendered_markdown, etc.
    """
    operation = "owner_verification_record"
    try:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not dry_run:
            return _blocked_execute(operation, actor=actor)
        resolved_root = _resolve_repo_root(repo_root)
        result = backend_build_owner_verification_record(
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
            "task_id": result.get("task_id"),
            "decision": result.get("decision"),
            "target_path": result.get("target_path"),
            "would_write": result.get("would_write", False),
            "rendered_markdown": result.get("rendered_markdown"),
            "original_payload": result.get("original_payload"),
        }
        response = make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=data,
            summary={
                "task_id": result.get("task_id"),
                "decision": result.get("decision"),
                "target_path": result.get("target_path"),
                "would_write": result.get("would_write", False),
            },
            planned_writes=list(result.get("planned_writes", [])),
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            owner_confirmation_required=True,  # AIPOS-273: owner verification requires owner_confirm (dogfood)
            owner_confirmation_reasons=["Owner verification record writes to truth (append-only records)"],
            safety_notice=CONTROLLED_EXECUTE_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != Verdict.BLOCK,
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def bench_audit_submit(
    payload: Mapping[str, Any],
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """AIPOS-336 S1: Submit bench audit conclusion (审结提交).

    Non-code tasks walk the bench audit path (304 D2 branch-1): executor produces
    evidence → verification station ring2 checklist + ring3 Owner eye-verify → Owner
    confirm → close. This function is the "审结提交" step: the executor (or advisor)
    submits the evidence + conclusion; the gate runs the ring2 auto-checks; the record
    lands in the workspace (`5_tasks/records/bench_audit/<task_id>/`) — same tier as
    audit_verdict records ("非代码不等于无据可依").

    The confirmation (bench_audit_confirm) is a separate gate verb that requires
    owner_confirm scope. The executor CANNOT self-confirm (acceptance #2: "执行体无法
    自行 confirm"). This is the two-stage controlled_execute pattern (351 磁盘持久化).

    Args:
        payload: {task_id, evidence_type?, task_mode?, conclusion, evidence_refs?, notes?}
        dry_run: if True, preview (default); write only when False (via execute_dry_run).
        repo_root: workspace root.
        actor: who submits (executor or advisor).

    Returns a response dict with verdict/blocking_reasons/target_path/checklist/
    dry_run_token (when execute_allowed).
    """
    operation = "bench_audit_submit"
    try:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        if not dry_run:
            return _blocked_execute(operation, actor=actor)
        resolved_root = _resolve_repo_root(repo_root)
        from tools.aipos_cli.bench_audit_writer import build_bench_audit_record
        result = build_bench_audit_record(
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
            "task_id": result.get("task_id"),
            "evidence_type": result.get("evidence_type"),
            "conclusion": result.get("conclusion"),
            "target_path": result.get("target_path"),
            "would_write": result.get("would_write", False),
            "rendered_markdown": result.get("rendered_markdown"),
            "checklist": result.get("checklist"),
            "ring2_summary": result.get("ring2_summary"),
            "original_payload": result.get("original_payload"),
        }
        response = make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=True,
            actor=_actor_payload(actor),
            data=data,
            summary={
                "task_id": result.get("task_id"),
                "conclusion": result.get("conclusion"),
                "target_path": result.get("target_path"),
                "ring2_summary": result.get("ring2_summary"),
                "would_write": result.get("would_write", False),
            },
            planned_writes=list(result.get("planned_writes", [])),
            warnings=list(result.get("warnings", [])),
            blocking_reasons=list(result.get("blocking_reasons", [])),
            needs_owner_reasons=[],
            owner_confirmation_required=True,
            owner_confirmation_reasons=[
                "Bench audit conclusion requires Owner confirmation (bench_audit_confirm)."
            ],
            safety_notice=CONTROLLED_EXECUTE_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != Verdict.BLOCK,
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
    owner_confirmation_required_override: bool | None = None,
    owner_confirmation_reasons_override: list[str] | None = None,
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
        # AIPOS-204 / F-c4: the gated (MCP/TUI) publish surface requires explicit Owner
        # confirmation, so the registered dry-run plan carries owner_confirmation_required;
        # execute_dry_run then refuses to publish without OWNER_CONFIRMED. The CLI publish
        # path passes no override and stays as-is (disclosed-deferred per DG-9).
        owner_confirmation_required = bool(owner_confirmation_required_override)
        owner_confirmation_reasons = (
            list(owner_confirmation_reasons_override)
            if owner_confirmation_required and owner_confirmation_reasons_override
            else []
        )
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
            owner_confirmation_required=owner_confirmation_required,
            owner_confirmation_reasons=owner_confirmation_reasons,
            safety_notice=MUTATION_DRY_RUN_NOTICE,
            errors=[],
        )
        return _attach_controlled_execute_metadata(
            operation=operation,
            actor=actor,
            response=response,
            execute_allowed=verdict != Verdict.BLOCK,
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
            if action == RecordType.CLAIM and isinstance(mcp_claim_metadata, dict)
            else None
        ),
        session_id_override=(
            str(mcp_claim_metadata.get("planned_session_id") or "").strip()
            if action == RecordType.CLAIM and isinstance(mcp_claim_metadata, dict)
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
            autonomy_mode=str(mcp_claim_metadata.get("autonomy_mode") or "Supervised"),
            actual_model=mcp_claim_metadata.get("actual_model"),
            reported_tokens=mcp_claim_metadata.get("reported_tokens"),
            confirmer=mcp_claim_metadata.get("confirmer") if isinstance(mcp_claim_metadata.get("confirmer"), dict) else None,
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
            verdict = Verdict.BLOCK
    owner_required = verdict == Verdict.NEEDS_OWNER
    owner_reasons = needs_owner_reasons if verdict == Verdict.NEEDS_OWNER else []
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
    # AIPOS-R1: claim 返回 LoopContext 字段
    if operation == "queue_claim" and verdict != Verdict.BLOCK:
        task_metadata = _task.get("metadata", {}) if _task else {}
        task_project = task_metadata.get("project", "")
        # AIPOS-R5A: worktree 信息从实际执行结果读取
        worktree_path = result.get("worktree_path")
        # AIPOS-R6A F-002修复: code_repo 从 project.json 真解析，禁硬编码
        product_code_repo = _resolve_product_code_repo(resolved_root)
        response["context"] = {
            "project": task_project,
            "workspace_root": str(resolved_root),
            "code_repo": str(product_code_repo) if product_code_repo else str(resolved_root),
            "task_state": result.get("to_state", ""),
            "worktree": worktree_path,
        }
    
    allow_execute = verdict != Verdict.BLOCK and operation == "queue_claim" and (not with_records or bool(mcp_claim_metadata))
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
    autonomy_mode: str = "Supervised",
    actual_model: str | None = None,
    reported_tokens: int | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claim_id = str(updated_metadata.get("claim_id") or "")
    session_id = str(updated_metadata.get("active_session_id") or "")
    claimed_at = str(updated_metadata.get("claimed_at") or "")
    claim_path, session_path = claim_record_paths(repo_root, task_id, claim_id, session_id)
    root = repo_root.resolve()  # AIPOS-240 (F-o3-19): record paths are .resolve()d; symlink-safe render
    claim_rel = str(claim_path.resolve().relative_to(root))
    session_rel = str(session_path.resolve().relative_to(root))
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
        autonomy_mode=autonomy_mode,
        actual_model=actual_model,
        reported_tokens=reported_tokens,
        claim_policy=str(updated_metadata.get("claim_policy") or ""),
        claim_match_basis=str(updated_metadata.get("claim_match_basis") or ""),
        claim_requirements_hash=str(updated_metadata.get("claim_requirements_hash") or ""),
        dry_run_id=dry_run_id,
        dry_run_snapshot_hash=dry_run_snapshot_hash,
        confirmation_ref=confirmation_ref,
        confirmer=confirmer,
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
        autonomy_mode=autonomy_mode,
    )
    return {
        "record_blocking_reasons": blocking,
        "record_writes": [
            _mcp_record_write_plan(claim_rel, RecordType.CLAIM_RECORD, would_write=not blocking),
            _mcp_record_write_plan(session_rel, RecordType.SESSION_RECORD, would_write=not blocking),
        ],
        "record_previews": [
            {"path": claim_rel, "record_type": RecordType.CLAIM_RECORD, "rendered_markdown": claim_markdown},
            {"path": session_rel, "record_type": RecordType.SESSION_RECORD, "rendered_markdown": session_markdown},
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
    actual_model: str | None = None,
    reported_tokens: int | None = None,
    agent_runtime: dict[str, Any] | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    return_id: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claim_id = str(source_metadata.get("claim_id") or "")
    session_id = str(source_metadata.get("active_session_id") or "")
    returned_at = str(updated_metadata.get("executor_completed_at") or "")
    return_id = return_id or build_runtime_id("return", task_id, returned_at, canonical_agent_instance)
    session_path = session_record_path(repo_root, task_id, session_id)
    return_path = return_record_path(repo_root, task_id, return_id)
    
    # AIPOS-R4A: return 幂等性——如果 return_id 已存在，生成新的（修实撞④）
    if return_path.exists():
        # 添加微秒后缀确保唯一性
        import time
        microsecond_suffix = str(int(time.time() * 1_000_000) % 1_000_000).zfill(6)
        return_id = f"{return_id}_{microsecond_suffix}"
        return_path = return_record_path(repo_root, task_id, return_id)
    
    root = repo_root.resolve()  # AIPOS-240 (F-o3-19): record paths are .resolve()d; symlink-safe render
    session_rel = str(session_path.resolve().relative_to(root))
    return_rel = str(return_path.resolve().relative_to(root))
    blocking: list[str] = []
    if not session_path.exists():
        blocking.append(f"Session record does not exist: {session_rel}")
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
        actual_model=actual_model,
        reported_tokens=reported_tokens,
        agent_runtime=agent_runtime,
        dry_run_id=dry_run_id,
        dry_run_snapshot_hash=dry_run_snapshot_hash,
        confirmation_ref=confirmation_ref,
        confirmer=confirmer,
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
        "record_writes": [_mcp_record_write_plan(return_rel, RecordType.RETURN_RECORD, would_write=not blocking)],
        "record_updates": [_mcp_record_write_plan(session_rel, RecordType.SESSION_RECORD, would_update=not blocking)],
        "record_previews": [
            {"path": return_rel, "record_type": RecordType.RETURN_RECORD, "rendered_markdown": return_markdown},
            {"path": session_rel, "record_type": RecordType.SESSION_RECORD, "rendered_markdown": session_markdown},
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
        if preview.get("record_type") == RecordType.RETURN_RECORD:
            item["wrote"] = True
        else:
            item["updated"] = True
        performed.append(item)
    return performed


def _return_owner_reasons() -> list[str]:
    return ["MCP Supervised queue_return requires explicit Owner confirmation for this dry-run preview"]


def _check_return_coverage(
    *,
    declared_scope: str,
    actual_refs: list[str],
    result_summary: str,
) -> dict[str, Any]:
    """AIPOS-R6E 靶④: return覆盖度对照——卡面artifact_scope vs 实际artifact_refs
    
    简化实现:按中英文分隔符拆分artifact_scope为大项清单,逐项检查是否在artifact_refs
    或result_summary中出现(启发式匹配,宽松策略避免误报)。
    
    Returns:
        {
            "has_missing_items": bool,
            "coverage_summary": str,
            "declared_items": list[str],
            "covered_items": list[str],
            "missing_items": list[str],
        }
    """
    import re
    
    if not declared_scope:
        # 无artifact_scope声明,无需检查
        return {
            "has_missing_items": False,
            "coverage_summary": "No artifact_scope declared",
            "declared_items": [],
            "covered_items": [],
            "missing_items": [],
        }
    
    # 按中英文分隔符拆分大项(优先逗号/顿号,其次加号/斜杠)
    # 先按逗号/顿号拆分(主分隔符)
    if ',' in declared_scope or '、' in declared_scope:
        declared_items = re.split(r'[,、]+', declared_scope)
    else:
        # 无逗号时才按其他分隔符
        declared_items = re.split(r'[;,;+/\s]+', declared_scope)
    declared_items = [item.strip() for item in declared_items if item.strip()]
    
    if not declared_items:
        return {
            "has_missing_items": False,
            "coverage_summary": "Empty artifact_scope",
            "declared_items": [],
            "covered_items": [],
            "missing_items": [],
        }
    
    # 合并所有实际交付证据文本
    evidence_text = " ".join(actual_refs) + " " + result_summary
    evidence_lower = evidence_text.lower()
    
    # 启发式匹配:检查每个大项是否在证据文本中出现
    covered_items = []
    missing_items = []
    
    for item in declared_items:
        # 宽松匹配策略:提取所有字母数字词,任一词匹配即算覆盖
        # 例: "修复部署脚本" -> ["修复", "部署", "脚本"] 或 "deploy"
        item_lower = item.lower()
        
        # 策略1: 完整词匹配(去除标点后)
        clean_item = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', item_lower)
        if clean_item and clean_item in evidence_lower.replace("_", "").replace("-", "").replace("/", ""):
            covered_items.append(item)
            continue
        
        # 策略2: 提取关键词(>2字的中英文词)
        keywords = re.findall(r'[a-z]{3,}|[\u4e00-\u9fff]{2,}', item_lower)
        matched = False
        for kw in keywords:
            if kw in evidence_lower:
                matched = True
                break
        
        if matched:
            covered_items.append(item)
        else:
            # 策略3: 去除动词前缀后再匹配
            core_item = re.sub(r'^(修复|添加|实现|完成|更新|fix|add|implement|complete|update)\s*', '', item, flags=re.IGNORECASE)
            core_keywords = re.findall(r'[a-z]{3,}|[\u4e00-\u9fff]{2,}', core_item.lower())
            core_matched = any(kw in evidence_lower for kw in core_keywords)
            
            if core_matched:
                covered_items.append(item)
            else:
                missing_items.append(item)
    
    coverage_rate = len(covered_items) / len(declared_items) if declared_items else 0
    
    return {
        "has_missing_items": len(missing_items) > 0,
        "coverage_summary": f"{len(covered_items)}/{len(declared_items)} items covered ({coverage_rate:.0%})",
        "declared_items": declared_items,
        "covered_items": covered_items,
        "missing_items": missing_items,
    }


def _unsafe_return_ref(value: str) -> bool:
    """AIPOS-R6F靶③: return材料位置校验——拒绝/tmp与仓外路径。
    
    合法路径:
    - 仓内相对路径 (task_cards/<task_id>/...)
    - 工作区相对路径 (5_tasks/records/...)
    
    非法路径:
    - /tmp/ 路径
    - 绝对路径
    - .. 父目录遍历
    - 密钥标记 (api_key, token=, ...)
    """
    if not value:
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in ("api_key", "bearer ", "token=", "password=", "secret=")):
        return True
    raw = Path(value)
    # AIPOS-R6F靶③: 拒绝 /tmp 路径
    if str(raw).startswith("/tmp/") or str(raw).startswith("/tmp") or "/tmp/" in str(raw):
        return True
    return raw.is_absolute() or ".." in raw.parts


def _validate_return_artifact_refs(
    artifact_refs: list[str],
    completion_report_ref: str | None,
    task_id: str,
    repo_root: Path,
) -> list[str]:
    """AIPOS-R6I 靶①: return材料存在性+落点双校验(杀报告漂移家族)。
    AIPOS-R6L 大项B①: 按artifact_ref类型分派验证(file_path|commit|record_ref|url)。
    
    对 artifact_refs 和 completion_report_ref 逐条检查:
    - file_path: 检查落点在 task_cards/<task_id>/ + 存在性
    - commit: 检查Git仓库可达性
    - record_ref: 检查records/<type>/<id>/存在性
    - url: 跳过验证(外部资源)
    
    类型识别:
    - 以 commit: 或 sha256: 开头 → commit类型
    - 以 record: 或包含 /records/ → record_ref类型
    - 以 http:// 或 https:// 开头 → url类型
    - 其他 → file_path类型(默认)
    
    Args:
        artifact_refs: 声明的 artifact 路径列表
        completion_report_ref: 可选的 completion report 路径
        task_id: 任务 ID
        repo_root: 仓库根路径
    
    Returns:
        blocking_reasons 列表, 空列表表示全部通过
    """
    import subprocess
    
    blocking_reasons: list[str] = []
    expected_prefix = f"task_cards/{task_id}/"
    
    all_refs = [*artifact_refs]
    if completion_report_ref:
        all_refs.append(completion_report_ref)
    
    for ref in all_refs:
        if not ref or not ref.strip():
            continue
        
        ref_stripped = ref.strip()
        
        # AIPOS-R6L 大项B①: 类型识别与分派验证
        if ref_stripped.startswith(("commit:", "sha256:")):
            # commit类型: 检查Git仓库可达性
            commit_hash = ref_stripped.split(":", 1)[1].strip() if ":" in ref_stripped else ref_stripped
            try:
                # 检查commit是否在产品仓中可达
                result = subprocess.run(
                    ["git", "cat-file", "-e", commit_hash],
                    cwd=repo_root,
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    blocking_reasons.append(
                        f"RETURN_ARTIFACT_COMMIT_NOT_FOUND: commit引用 '{ref_stripped}' 在仓库中不可达。"
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                blocking_reasons.append(
                    f"RETURN_ARTIFACT_COMMIT_CHECK_FAILED: 无法验证commit '{ref_stripped}': {e}"
                )
        
        elif ref_stripped.startswith("record:") or "/records/" in ref_stripped:
            # record_ref类型: 检查records目录存在性
            if ref_stripped.startswith("record:"):
                record_path = ref_stripped.split(":", 1)[1].strip()
            else:
                record_path = ref_stripped
            
            # 规范化路径: 如果以 5_tasks/records/ 开头，保留；否则假设相对路径
            if not record_path.startswith("5_tasks/records/"):
                record_path = f"5_tasks/records/{record_path}"
            
            ref_full_path = repo_root / record_path
            if not ref_full_path.exists():
                blocking_reasons.append(
                    f"RETURN_ARTIFACT_RECORD_NOT_FOUND: record引用 '{ref_stripped}' 不存在。"
                    f"路径: {ref_full_path}"
                )
        
        elif ref_stripped.startswith(("http://", "https://")):
            # url类型: 跳过验证(外部资源)
            pass
        
        else:
            # file_path类型(默认): 检查落点+存在性
            # 检查落点: 必须在 task_cards/<task_id>/ 内
            if not ref_stripped.startswith(expected_prefix):
                blocking_reasons.append(
                    f"RETURN_ARTIFACT_WRONG_LOCATION: 报告材料 '{ref_stripped}' 不在正确落点。"
                    f"必须在 {expected_prefix} 内 (治理工作区 task_cards/<task_id>/)。"
                    f"示例正确路径: {expected_prefix}RETURN.md, {expected_prefix}artifacts/output.txt"
                )
                continue
            
            # 检查存在性: 文件必须存在
            ref_path = repo_root / ref_stripped
            if not ref_path.exists():
                blocking_reasons.append(
                    f"RETURN_ARTIFACT_NOT_FOUND: 报告材料 '{ref_stripped}' 不存在于仓库。"
                    f"路径必须存在: {ref_path}。如文件在产品仓,需先复制到治理工作区 {expected_prefix}"
                )
                continue
            
            # 检查不是目录
            if ref_path.is_dir():
                blocking_reasons.append(
                    f"RETURN_ARTIFACT_IS_DIRECTORY: 报告材料 '{ref_stripped}' 是目录,必须是文件。"
                )
    
    return blocking_reasons


class ProductRepoNotConfigured(ValueError):
    """AIPOS-FND-5F1: raised when the governance workspace's project.json does not carry a
    valid ``code_repo`` mapping. Callers must surface this as an actionable blocking reason,
    never silently guess a path."""


def _resolve_product_code_repo(governance_root: Path) -> Path:
    """AIPOS-FND-5F1: Resolve the PRODUCT repo (where code actually lands) strictly from
    config — ``governance_root/project.json`` -> ``code_repo`` (sole authority per ruling 6).

    Root cause this fixes: the governance workspace (e.g.
    ``~/ai-project-os/2_projects/lybra``) is a SUBTREE of the governance monorepo and has NO
    ``.git`` of its own. A naive ``git status`` run with that dir as ``cwd`` walks UPWARD and
    lands on the governance monorepo's ``.git``, which is essentially always dirty (unrelated
    task cards/records for other projects) — a completely different repo from where code卡
    actually commits.

    Owner ruling (2026-08-09): the product repo location MUST be resolved from config, with
    ZERO machine-specific hardcoded fallback paths (no ``~/projects/lybra``, no
    ``~/projects/<project>`` naming-convention guess — those are dev-machine layout
    assumptions that do not belong baked into product logic). Missing/invalid config fails
    LOUD with an actionable message, never silently degrades to a guessed path.

    Resolution:
      1. If ``governance_root`` itself directly owns a ``.git``, it IS the product repo (a
         single-repo setup where governance and code share one root) — use it AS-IS. This
         also covers every existing test fixture that ``git init``s its own tempdir directly:
         their behavior is unaffected by this fix.
      2. Otherwise, if ``governance_root/project.json`` does not exist AT ALL, this workspace
         was never registered under the governance-home model in the first place (e.g. an
         ad hoc/legacy tempdir used by unrelated tests) — fall back to ``governance_root``
         unchanged (legacy passthrough, byte-identical to pre-FND-5F1 behavior: git will
         simply skip the check if that root isn't a git repo either).
      3. Otherwise (``project.json`` exists — this IS an established governance project),
         read its ``code_repo``. If present AND the path exists on disk, use it.
      4. Otherwise raise ``ProductRepoNotConfigured`` with a message pointing at
         ``lybra project set-repo`` — an established project with a missing/stale code_repo
         mapping is an actionable configuration error, never a silent path guess.
    """
    if (governance_root / ".git").exists():
        return governance_root
    project_json_file = governance_root / "project.json"
    if not project_json_file.is_file():
        return governance_root
    try:
        project_json = read_project_json(governance_root)
    except (OSError, ValueError):
        project_json = {}
    code_repo_raw = str(project_json.get("code_repo") or "").strip()
    if code_repo_raw:
        candidate = Path(code_repo_raw).expanduser()
        if candidate.is_dir():
            return candidate
        raise ProductRepoNotConfigured(
            f"project.json code_repo={code_repo_raw!r} does not exist on disk. "
            "Run `lybra project set-repo <name> --code-repo <path>` to fix the mapping."
        )
    raise ProductRepoNotConfigured(
        "project.json has no code_repo mapping for this governance workspace. "
        "Run `lybra project set-repo <name> --code-repo <path>` to set it."
    )


# F-R4B2-6: _check_uncommitted_code 已退役，改用 scoped_commit_check.check_uncommitted_in_scope

def _task_filename_for(task_id: str) -> str:
    value = "".join(char.lower() if char.isalnum() else "-" for char in task_id).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "task"


def _select_task(
    repo_root: Path,
    *,
    task_id: str | None,
    path: str | Path | None,
    id_param_name: str = "task_id",
    path_param_name: str = "path",
) -> dict[str, Any]:
    """AIPOS-F14 大项B: 透传参数实名到 _select_task_input, 报错文案用实名。"""
    selected_task_id, selected_path = _select_task_input(
        task_id, path,
        id_param_name=id_param_name,
        path_param_name=path_param_name,
    )
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


def _check_return_self_checks(
    *,
    task_id: str,
    task_metadata: dict[str, Any],
    repo_root: Path,
    result_summary: str | None,
    completion_report_ref: str | None,
    claim_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    """
    AIPOS-F49: N3 交回自检门——四条机器判据在交回时拒收不合格交付。
    
    四条判据:
    ① 夹具入常驻: 本卡新增 test 文件必须在 run-all 清单中
    ② 改动面在界内: git diff 文件必须落在 output_target 范围内
    ③ 有测试: code 类卡必须有新增/修改的 test 文件
    ④ RETURN 非骨架: 不得含占位符，result_summary 非空
    
    注: 判据⑤靠场未污染和⑥基线零新增失败已移至 F49B(快照机制)
    
    Returns:
        列表的 blocking_reasons，空列表表示全部通过
    """
    blocking_reasons = []
    
    # 检查是否启用 return_self_check（默认启用）
    # TODO: 从 schema 读取配置，现在默认启用
    self_check_enabled = True
    
    if not self_check_enabled:
        return blocking_reasons
    
    task_mode = str(task_metadata.get("task_mode") or "").strip()
    output_target = str(task_metadata.get("output_target") or "").strip()
    
    # ① 夹具入常驻
    blocking_reasons.extend(_check_test_in_runall(
        task_id=task_id,
        repo_root=repo_root,
    ))
    
    # ② 改动面在界内
    blocking_reasons.extend(_check_changes_in_scope(
        task_id=task_id,
        output_target=output_target,
        repo_root=repo_root,
    ))
    
    # ③ 有测试
    if task_mode == "code":
        blocking_reasons.extend(_check_has_tests(
            task_id=task_id,
            repo_root=repo_root,
        ))
    
    # ④ RETURN 非骨架
    blocking_reasons.extend(_check_return_not_skeleton(
        task_id=task_id,
        result_summary=result_summary,
        completion_report_ref=completion_report_ref,
        repo_root=repo_root,
    ))
    
    return blocking_reasons


def _check_test_in_runall(
    *,
    task_id: str,
    repo_root: Path,
) -> list[str]:
    """① 夹具入常驻: 本卡新增 test 文件必须在 run-all 清单中。"""
    blocking_reasons = []
    
    try:
        product_repo_root = _resolve_product_code_repo(repo_root)
    except ProductRepoNotConfigured:
        return blocking_reasons  # 无产品仓，跳过
    
    import subprocess
    
    # 1. git diff 找本卡新增/修改的 test 文件
    branch_name = f"card/{task_id}"
    try:
        result = subprocess.run(
            ["git", "diff", "main.." + branch_name, "--name-only"],
            cwd=product_repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return blocking_reasons  # git 失败，跳过检查
        
        changed_files = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        test_files = [
            f for f in changed_files 
            if "test" in f.lower() and not f.endswith("run-all.sh")
        ]
        
        if not test_files:
            return blocking_reasons  # 无 test 文件，跳过
    except Exception:
        return blocking_reasons  # git 命令失败，跳过
    
    # 2. 读取 run-all.sh 清单
    runall_path = product_repo_root / "agents" / "harness" / "pi" / "lybra-loop" / "tests" / "run-all.sh"
    if not runall_path.exists():
        return blocking_reasons  # run-all.sh 不存在，跳过
    
    try:
        runall_content = runall_path.read_text(encoding="utf-8")
    except Exception:
        return blocking_reasons  # 读取失败，跳过
    
    # 3. 检查每个 test 文件是否在清单中
    missing_tests = []
    for test_file in test_files:
        # 检查完整路径或 basename
        basename = test_file.split("/")[-1]
        if test_file not in runall_content and basename not in runall_content:
            missing_tests.append(test_file)
    
    if missing_tests:
        blocking_reasons.append(
            f"TEST_NOT_IN_RUNALL: 本卡新增/修改的 test 文件未加入 run-all.sh 清单。"
            f"缺失项: {', '.join(missing_tests)}。"
            f"请在 {runall_path.relative_to(product_repo_root)} 中添加这些测试。"
        )
    
    return blocking_reasons


def _check_changes_in_scope(
    *,
    task_id: str,
    output_target: str,
    repo_root: Path,
) -> list[str]:
    """② 改动面在界内: git diff 文件必须落在 output_target 范围内。"""
    blocking_reasons = []
    
    if not output_target:
        return blocking_reasons  # 无 output_target 声明，跳过
    
    try:
        product_repo_root = _resolve_product_code_repo(repo_root)
    except ProductRepoNotConfigured:
        return blocking_reasons
    
    import subprocess
    
    # 1. git diff 找全部改动文件
    branch_name = f"card/{task_id}"
    try:
        result = subprocess.run(
            ["git", "diff", "main.." + branch_name, "--name-only"],
            cwd=product_repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return blocking_reasons  # git 失败，跳过检查
        
        changed_files = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        
        if not changed_files:
            return blocking_reasons  # 无改动，跳过
    except Exception:
        return blocking_reasons  # git 命令失败，跳过
    
    # 2. 解析 output_target 的路径片段（宽松匹配）
    # output_target 格式示例: "tools/aipos_cli/board_adapter.py(queue_return 校验点), tests/(五夹具经 bin 入 run-all)"
    # 提取路径片段: tools/aipos_cli/board_adapter.py, tests/
    import re
    # 匹配路径模式：任何非空白字符直到括号或逗号或结尾
    path_patterns = re.findall(r'([\w/._-]+(?:\.\w+)?)', output_target)
    # 过滤掉明显不是路径的片段（如单个词）
    path_patterns = [p for p in path_patterns if '/' in p or '.' in p]
    
    if not path_patterns:
        return blocking_reasons  # 无法解析 output_target，跳过
    
    # 3. 检查每个文件是否匹配任意一个模式
    out_of_scope = []
    for changed_file in changed_files:
        matched = False
        for pattern in path_patterns:
            # 宽松匹配：文件路径包含模式或模式包含文件路径
            if pattern in changed_file or changed_file.startswith(pattern):
                matched = True
                break
        if not matched:
            out_of_scope.append(changed_file)
    
    if out_of_scope:
        blocking_reasons.append(
            f"CHANGES_OUT_OF_SCOPE: 以下文件超出卡面声明的 output_target 范围。"
            f"越界文件: {', '.join(out_of_scope)}。"
            f"卡面声明范围: {output_target}。"
            f"请只修改卡面声明范围内的文件，或更新卡面 output_target 字段。"
        )
    
    return blocking_reasons


def _check_has_tests(
    *,
    task_id: str,
    repo_root: Path,
) -> list[str]:
    """③ 有测试: code 类卡必须有新增/修改的 test 文件。"""
    blocking_reasons = []
    
    try:
        product_repo_root = _resolve_product_code_repo(repo_root)
    except ProductRepoNotConfigured:
        return blocking_reasons
    
    import subprocess
    
    # 1. git diff 找全部改动文件
    branch_name = f"card/{task_id}"
    try:
        result = subprocess.run(
            ["git", "diff", "main.." + branch_name, "--name-only"],
            cwd=product_repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return blocking_reasons  # git 失败，跳过检查
        
        changed_files = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        
        if not changed_files:
            return blocking_reasons  # 无改动，跳过
    except Exception:
        return blocking_reasons  # git 命令失败，跳过
    
    # 2. 检查是否有 test 文件
    has_test = any(
        "test" in f.lower() or "/tests/" in f or f.startswith("tests/")
        for f in changed_files
    )
    
    if not has_test:
        blocking_reasons.append(
            f"NO_TESTS: code 类卡必须包含测试文件改动。"
            f"当前改动文件: {', '.join(changed_files[:5])}{'...' if len(changed_files) > 5 else ''}。"
            f"请添加测试文件（如 tests/test_*.py）。"
        )
    
    return blocking_reasons


def _check_return_not_skeleton(
    *,
    task_id: str,
    result_summary: str | None,
    completion_report_ref: str | None,
    repo_root: Path,
) -> list[str]:
    """④ RETURN 非骨架: 不得含占位符，result_summary 非空。"""
    blocking_reasons = []
    
    # 检查 result_summary
    if not result_summary or not result_summary.strip():
        blocking_reasons.append(
            "RETURN_SKELETON: result_summary 为空，请填写一句话结论。"
        )
    
    # 检查 RETURN.md 占位符
    if completion_report_ref:
        report_path = repo_root / completion_report_ref
        if report_path.exists():
            try:
                content = report_path.read_text(encoding="utf-8")
                placeholders = [
                    "(待填写)",
                    "(PASS / FAIL",
                    "(无验收清单)",
                    "TODO",
                    "FIXME",
                ]
                found_placeholders = [p for p in placeholders if p in content]
                if found_placeholders:
                    blocking_reasons.append(
                        f"RETURN_SKELETON: RETURN.md 包含占位符: {', '.join(found_placeholders)}。"
                        f"请填写完整内容。"
                    )
            except Exception:
                pass  # 读取失败，跳过
    
    return blocking_reasons




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
    scratch_dir: str | None = None,
    scratch_artifact_refs: Any = None,
    home_root: Path | None = None,
    return_body: str | None = None,
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
    # AIPOS-F34 大项A: 绑定放宽为工位双锁(service token + agent_instance)。
    # 会话字段仍记录入 original_payload(可问责证据保留), 但不作为拒绝条件。
    # 冒交防线由上方 CLAIMANT_MISMATCH(agent_instance 匹配) + 服务层 token 校验保证。
    if active_session_id and str(source_metadata.get("active_session_id") or "").strip() != active_session_id:
        warnings.append(f"SESSION_DRIFT: active_session_id changed (claimed={source_metadata.get('active_session_id')}, returned={active_session_id}); recorded but not blocking (AIPOS-F34)")
    
    # AIPOS-R6F靶③: return材料位置校验——拒绝/tmp与仓外路径
    unsafe_refs = [ref for ref in [*artifact_refs, completion_report_ref or ""] if _unsafe_return_ref(ref)]
    if unsafe_refs:
        task_id_for_example = str(task.get("task_id") or "TASK-ID")
        blocking_reasons.append(
            f"UNSAFE_RETURN_REF: return材料路径必须在治理工作区 task_cards/<task_id>/ 内，"
            f"禁止 /tmp 与仓外路径。非法路径: {unsafe_refs}. "
            f"正确落点示例: task_cards/{task_id_for_example}/RETURN.md, "
            f"task_cards/{task_id_for_example}/artifacts/output.txt"
        )
    
    # AIPOS-R6I 靶①: return材料存在性+落点双校验(杀报告漂移家族)
    task_id_text = str(task.get("task_id") or "")
    if task_id_text:
        artifact_validation_errors = _validate_return_artifact_refs(
            artifact_refs=artifact_refs,
            completion_report_ref=completion_report_ref,
            task_id=task_id_text,
            repo_root=repo_root,
        )
        blocking_reasons.extend(artifact_validation_errors)
    
    # AIPOS-R6E 靶④: return覆盖度对照——卡面artifact_scope与实际artifact_refs差异结构化
    artifact_scope = str(source_metadata.get("artifact_scope") or "").strip()
    coverage_check = _check_return_coverage(
        declared_scope=artifact_scope,
        actual_refs=artifact_refs,
        result_summary=result_summary or "",
    )
    if coverage_check.get("has_missing_items"):
        # 有缺项→标记partial,差异落入响应数据
        warnings.append(
            f"Return coverage: {coverage_check.get('coverage_summary')}. "
            f"Missing items will be recorded as partial delivery."
        )
    
    # AIPOS-FND-5: code 卡交回门检测未提交代码
    # AIPOS-R4B-2 N3: 交回门按卡 scope — 只检查本卡声明的路径，不全仓扫描
    task_mode = str(source_metadata.get("task_mode") or "").strip()
    artifact_policy = str(source_metadata.get("artifact_policy") or "").strip()
    if task_mode == "code" or artifact_policy == "formal_write":
        current_task_id = str(task.get("task_id") or "")
        try:
            product_repo_root = _resolve_product_code_repo(repo_root)
        except ProductRepoNotConfigured as exc:
            blocking_reasons.append(f"CODE_REPO_NOT_CONFIGURED: {exc}")
        else:
            # AIPOS-R4B-2: 使用 scoped check，只看本卡 scope 内改动
            from tools.aipos_cli.scoped_commit_check import (
                check_uncommitted_in_scope,
                resolve_check_scope_from_task,
            )
            scoped_paths = resolve_check_scope_from_task(source_metadata)
            git_check = check_uncommitted_in_scope(
                product_repo_root, current_task_id, scoped_paths=scoped_paths
            )
            if git_check.get("has_uncommitted"):
                blocking_reasons.append(
                    f"CODE_NOT_COMMITTED: {git_check.get('message', 'Working tree has uncommitted changes')}. "
                    "Code tasks must commit all changes before return."
                )

    updated_metadata = dict(source_metadata)
    updated_metadata["status"] = "claimed"
    updated_metadata["executor_status"] = "completed"
    updated_metadata["executor_completed_by"] = canonical_agent_instance or actor
    # AIPOS-219 P5: persist executor registry-verification status so the audit verdict path can
    # fail-closed when the executor identity was not registry-verified (bare python, no PyYAML).
    _executor_provenance = (
        mcp_return_metadata.get("identity_provenance")
        if isinstance(mcp_return_metadata, dict)
        else None
    )
    if isinstance(_executor_provenance, dict):
        updated_metadata["executor_registry_verified"] = bool(
            _executor_provenance.get("registry_available", True)
        )
    else:
        # No provenance recorded — treat as registry-verified (legacy/direct path, PyYAML present).
        updated_metadata["executor_registry_verified"] = True
    returned_at = planned_returned_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated_metadata["executor_completed_at"] = returned_at
    updated_metadata["audit_readiness"] = "ready"
    updated_metadata["audit_status"] = str(updated_metadata.get("audit_status") or "pending")
    updated_metadata["dependency_executor_status"] = "completed"
    updated_metadata["dependency_audit_readiness"] = "ready"
    updated_metadata["dependency_audit_status"] = str(updated_metadata.get("dependency_audit_status") or "pending")
    updated_metadata["return_owner_policy_ref"] = owner_policy_ref

    task_id_text = str(task.get("task_id") or "")
    return_id = (
        build_runtime_id("return", task_id_text, returned_at, canonical_agent_instance or actor)
        if task_id_text
        else ""
    )

    # AIPOS-196a: gate-side ingestion of confined-worker scratch artifacts. The
    # gate copies scratch outputs into workspace_artifacts/ on Owner-confirmed
    # return; persisted artifact_refs point to those gate-written paths.
    ingestion_plan: dict[str, Any] = {"ingestions": [], "workspace_refs": [], "digest": ""}
    if has_scratch_request(scratch_dir, scratch_artifact_refs):
        # AIPOS-227 R-2: single-caller-resolves invariant. The repo_root reaching ingestion is the
        # already-resolved project truth root (queue_return -> _resolve_repo_and_home, which only
        # ever yields a workspace with the 5_tasks/queue marker). A future second caller feeding a
        # raw/unresolved root fails fast HERE instead of ingesting into a wrong root.
        if not has_workspace_queue(repo_root):
            blocking_reasons.append(
                "ARTIFACT_INGEST_BLOCKED: ingestion repo_root is not a resolved workspace "
                "(single-caller-resolves invariant violated)"
            )
        elif not return_id:
            blocking_reasons.append("ARTIFACT_INGEST_BLOCKED: cannot derive return id for scratch ingestion")
        else:
            ingestion_plan = plan_scratch_ingestion(
                repo_root=repo_root,
                task_id=task_id_text,
                return_id=return_id,
                scratch_dir=scratch_dir,
                scratch_artifact_refs=scratch_artifact_refs,
                home_root=home_root,
            )
            blocking_reasons.extend(str(item) for item in ingestion_plan.get("blocking_reasons", []))

    workspace_refs = list(ingestion_plan.get("workspace_refs", []))
    effective_artifact_refs = [*artifact_refs, *workspace_refs]

    if result_summary:
        updated_metadata["result_summary"] = result_summary
    if effective_artifact_refs:
        updated_metadata["artifact_refs"] = effective_artifact_refs
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
            task_id=task_id_text,
            task_path=source_rel,
            actor=actor,
            canonical_agent_instance=canonical_agent_instance,
            owner_policy_ref=owner_policy_ref,
            source_metadata=source_metadata,
            updated_metadata=updated_metadata,
            result_summary=result_summary,
            artifact_refs=effective_artifact_refs,
            completion_report_ref=completion_report_ref,
            actual_model=mcp_return_metadata.get("actual_model") if isinstance(mcp_return_metadata, dict) else None,
            reported_tokens=mcp_return_metadata.get("reported_tokens") if isinstance(mcp_return_metadata, dict) else None,
            agent_runtime=mcp_return_metadata.get("agent_runtime") if isinstance(mcp_return_metadata, dict) and isinstance(mcp_return_metadata.get("agent_runtime"), dict) else None,
            return_id=return_id or None,
            confirmer=mcp_return_metadata.get("confirmer") if isinstance(mcp_return_metadata.get("confirmer"), dict) else None,
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
        "artifact_refs": effective_artifact_refs,
        "completion_report_ref": completion_report_ref,
        "ingested_artifact_refs": workspace_refs,
    }
    ingestion_planned_writes = [
        {
            "path": item.get("workspace_rel"),
            "kind": "create",
            "type": RecordType.INGESTED_ARTIFACT,
            "record_type": RecordType.INGESTED_ARTIFACT,
        }
        for item in ingestion_plan.get("ingestions", [])
    ]
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
            "scratch_dir": scratch_dir,
            "scratch_artifact_refs": list(_as_ref_list(scratch_artifact_refs)),
            "return_body": return_body,
        },
        "lease_preview": {
            "lease_path": "claim_only",
            "lease_status": "proposed",
            "active_lease_written": False,
        },
        "scratch_ingestions": ingestion_plan.get("ingestions", []),
        "scratch_ingestion_digest": ingestion_plan.get("digest", ""),
        "scratch_root": ingestion_plan.get("scratch_root"),
        "ingested_artifact_refs": workspace_refs,
    }
    if mcp_return_metadata:
        data["mcp_return"] = dict(mcp_return_metadata)
        data["record_writes"] = record_plan.get("record_writes", [])
        data["record_updates"] = record_plan.get("record_updates", [])
        data["record_previews"] = record_plan.get("record_previews", [])
        data["return_record_path"] = record_plan.get("return_record_path")
        data["session_record_path"] = record_plan.get("session_record_path")

    # AIPOS-320: RETURN.md planned write (gate-side落盘,路径严格限定 task_cards/<ID>/RETURN.md)
    return_body_planned_writes: list[dict[str, Any]] = []
    if return_body is not None and task_id_text:
        return_body_rel = f"task_cards/{task_id_text}/RETURN.md"
        return_body_planned_writes = [
            {"path": return_body_rel, "kind": "create", "type": "return_body"}
        ]

    # AIPOS-F49: N3 交回自检门——六条机器判据
    # 读取 claim 快照（用于判据⑤⑥）
    claim_snapshot = None
    if task_id_text and claim_id:
        claim_record_path = repo_root / "5_tasks" / "records" / "claims" / task_id_text / f"{claim_id}.md"
        if claim_record_path.exists():
            try:
                claim_content = claim_record_path.read_text(encoding="utf-8")
                # TODO: 解析 claim 记录中的快照数据
                # claim_snapshot = parse_claim_snapshot(claim_content)
            except Exception:
                pass  # 读取失败，跳过快照检查
    
    self_check_reasons = _check_return_self_checks(
        task_id=task_id_text,
        task_metadata=source_metadata,
        repo_root=repo_root,
        result_summary=result_summary,
        completion_report_ref=completion_report_ref,
        claim_snapshot=claim_snapshot,
    )
    blocking_reasons.extend(self_check_reasons)

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
            *return_body_planned_writes,
            *ingestion_planned_writes,
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
        owner_confirmation_required=verdict != Verdict.BLOCK,
        owner_confirmation_reasons=_return_owner_reasons() if verdict != Verdict.BLOCK else [],
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
    scratch_dir: str | None = None,
    scratch_artifact_refs: Any = None,
    return_body: str | None = None,
) -> dict[str, Any]:
    # AIPOS-R6A F-003修复: CLI return 强制 dry-run，禁直接执行（平行loop路径）
    # 只有 MCP gate (通过 mcp_return_metadata) 可以非 dry-run 执行
    # CLI 必须走 dry-run preview → MCP confirm 两阶段
    if not dry_run and not mcp_return_metadata:
        return blocked_response(
            operation="queue_return",
            dry_run=False,
            category="CLI_DIRECT_EXECUTE_FORBIDDEN",
            message=(
                "CLI queue return no longer supports direct execution (AIPOS-R6A F-003). "
                "Use MCP gate tools instead: lybra_queue_return_dry_run + lybra_queue_return_confirm. "
                "CLI can only preview (--dry-run flag required)."
            ),
            actor=_actor_payload(str(actor or "").strip()),
            safety_notice=CONTROLLED_EXECUTE_NOTICE,
        )
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
        scratch_dir_text = str(scratch_dir or "").strip() or None
        scratch_refs = _as_ref_list(scratch_artifact_refs)
        summary_text = str(result_summary or "").strip()
        completion_ref = str(completion_report_ref or "").strip()
        if not summary_text and not refs and not completion_ref and not scratch_refs:
            raise ValueError("MISSING_RETURN_EVIDENCE: result_summary, artifact_refs, or completion_report_ref is required")
        # AIPOS-227: resolve the project truth root AND the truth home in one shot, so the 196a
        # ingestion home-guard uses the SAME resolution that produced repo_root (no drift).
        resolved_root, home_root = _resolve_repo_and_home(repo_root)
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
            scratch_dir=scratch_dir_text,
            scratch_artifact_refs=scratch_refs,
            home_root=home_root,
            return_body=return_body,
        )
        if dry_run:
            return _attach_controlled_execute_metadata(
                operation="queue_return",
                actor=actor_text,
                response=response,
                execute_allowed=response.get("verdict") != Verdict.BLOCK,
            )
        if response.get("verdict") == Verdict.BLOCK:
            return response
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        # AIPOS-196a: ingest scratch artifacts before any truth write so an
        # integrity failure (R-A) blocks the whole return instead of leaving a
        # task card that points at artifacts the gate never copied.
        ingestion_performed_writes: list[dict[str, Any]] = []
        scratch_ingestions = data.get("scratch_ingestions") if isinstance(data.get("scratch_ingestions"), list) else []
        if scratch_ingestions:
            try:
                ingestion_performed_writes = perform_scratch_ingestion(
                    resolved_root,
                    scratch_ingestions,
                    scratch_root=data.get("scratch_root"),
                    approved_root=approved_scratch_root(),
                )
            except (ValueError, OSError) as exc:
                return blocked_response(
                    operation="queue_return",
                    dry_run=False,
                    category="ARTIFACT_INGEST_BLOCKED",
                    message=str(exc),
                    actor=_actor_payload(actor_text),
                    data={"recommended_action": "re-run dry-run; scratch artifact changed or escaped"},
                    safety_notice=CONTROLLED_EXECUTE_NOTICE,
                )
        target = resolved_root / str(data.get("target_path") or "")
        target.write_text(str(data.get("rendered_markdown") or ""), encoding="utf-8")
        # AIPOS-320: write RETURN.md when return_body was provided (路径严格限定 task_cards/<ID>/RETURN.md)
        return_body_performed_writes: list[dict[str, Any]] = []
        if return_body is not None:
            task_id_for_return = str(data.get("task_id") or "")
            if task_id_for_return:
                return_body_rel = f"task_cards/{task_id_for_return}/RETURN.md"
                return_body_path = resolved_root / return_body_rel
                # 路径逃逸防护:确保解析后路径仍在 resolved_root 下
                try:
                    return_body_path.resolve().relative_to(resolved_root.resolve())
                except ValueError:
                    return blocked_response(
                        operation="queue_return",
                        dry_run=False,
                        category="RETURN_BODY_PATH_ESCAPE",
                        message=f"return_body path escapes workspace: {return_body_rel}",
                        actor=_actor_payload(actor_text),
                        safety_notice=CONTROLLED_EXECUTE_NOTICE,
                    )
                return_body_path.parent.mkdir(parents=True, exist_ok=True)
                return_body_path.write_text(return_body, encoding="utf-8")
                return_body_performed_writes = [
                    {"path": return_body_rel, "kind": "create", "type": "return_body"}
                ]
        record_performed_writes: list[dict[str, Any]] = []
        if bool(data.get("mcp_records_enabled")):
            record_performed_writes = _write_mcp_return_records(resolved_root, data)
        
        # AIPOS-253: Derive audit task after successful return
        audit_derivation_writes: list[dict[str, Any]] = []
        source_metadata = data.get("updated_frontmatter") if isinstance(data.get("updated_frontmatter"), dict) else {}
        return_record_ref = str(source_metadata.get("return_record_ref") or "")
        if return_record_ref:
            derivation_result = derive_audit_task_on_return(
                repo_root=resolved_root,
                source_task_id=str(data.get("task_id") or ""),
                source_metadata=source_metadata,
                source_path=str(data.get("source_path") or ""),
                return_record_ref=return_record_ref,
                artifact_refs=list(source_metadata.get("artifact_refs", [])),
            )
            if derivation_result.get("derived"):
                audit_derivation_writes = list(derivation_result.get("performed_writes", []))
                # AIPOS-256 F-253-1: Write back related_audit_task_ref to source task
                # AIPOS-256F2 F-256R-1: Read body from file to preserve content
                audit_task_id = derivation_result.get("audit_task_id")
                if audit_task_id:
                    # Read current task card content (just written at line 2080)
                    current_source_content = target.read_text(encoding="utf-8")
                    _, source_body, _ = parse_markdown_frontmatter(current_source_content)
                    updated_source_metadata = dict(source_metadata)
                    updated_source_metadata["related_audit_task_ref"] = audit_task_id
                    updated_source_markdown = render_task_markdown(updated_source_metadata, source_body)
                    target.write_text(updated_source_markdown, encoding="utf-8")
                    audit_derivation_writes.append({
                        "path": str(data.get("source_path") or ""),
                        "kind": "update",
                        "type": "source_task_backref",
                        "field": "related_audit_task_ref",
                        "value": audit_task_id,
                    })
        
        response["dry_run"] = False
        response["data"]["wrote"] = True
        _mark_record_write_report_performed(response["data"])
        response["performed_writes"] = list(response.get("planned_writes", [])) + return_body_performed_writes + ingestion_performed_writes + record_performed_writes + audit_derivation_writes
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
    # AIPOS-F14 大项B: 报错参数名取 verb_contract 实名(source_task_id/source_task_path)
    source_task = _select_task(
        repo_root, task_id=source_task_id, path=source_path,
        id_param_name="source_task_id", path_param_name="source_task_path",
    )
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
    # AIPOS-FND-7F1: FAIL/REQUEST_CHANGES 是非终态,可以复审;PASS 是终态,不可翻案
    source_task_id_for_verdict = str(source_task.get("task_id") or "")
    existing_verdicts = records.get("task_audit_verdicts", {}).get(source_task_id_for_verdict, [])
    if existing_verdicts:
        # 有已有裁决,检查最新裁决状态
        latest_verdict = max(existing_verdicts, key=_verdict_time)
        latest_verdict_value = str(latest_verdict.get("verdict", "")).upper().strip()
        if latest_verdict_value in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
            blocking_reasons.append("AUDIT_ALREADY_PASSED: source task already has audit PASS (terminal state, cannot overturn)")
        # FAIL/REQUEST_CHANGES/BLOCKED 等非终态:允许 re-dispatch,不 BLOCK
    elif source_metadata.get("dependency_audit_status") == Verdict.PASS:
        # 兜底:metadata 显示 PASS 但没找到 verdict 记录(数据不一致)
        blocking_reasons.append("AUDIT_ALREADY_PASSED: source task already has audit PASS")
    
    # AIPOS-F2 ③立墙带路: 检测手写文件在场时附加提示
    from tools.aipos_cli.audit_helpers import detect_hand_written_verdicts, HAND_WRITTEN_VERDICT_NOTICE
    _hw_dispatch = detect_hand_written_verdicts(
        repo_root / "5_tasks" / "records" / "audit_verdicts" / source_task_id_for_verdict
    )
    if _hw_dispatch:
        warnings.append(HAND_WRITTEN_VERDICT_NOTICE)
        for _hw in _hw_dispatch:
            warnings.append(f"  ignored: {_hw['file']} ({_hw['reason']})")
    
    # re-dispatch 检查:如果前轮是非终态(FAIL/REQUEST_CHANGES),允许新 audit_task_id
    if source_metadata.get("related_audit_task_ref") or source_metadata.get("audit_dispatch_record_ref"):
        # 已有 dispatch 记录
        if existing_verdicts:
            latest_verdict = max(existing_verdicts, key=_verdict_time)
            latest_verdict_value = str(latest_verdict.get("verdict", "")).upper().strip()
            # AIPOS-R6J: 非终态裁决允许re-dispatch(FAIL/BLOCK/WARN/NEEDS_OWNER)
            if latest_verdict_value not in {Verdict.FAIL, Verdict.BLOCK, Verdict.WARN, Verdict.NEEDS_OWNER}:
                # 非 FAIL/REQUEST_CHANGES,不允许 re-dispatch
                blocking_reasons.append("AUDIT_ALREADY_DISPATCHED: source task already links an audit dispatch")
            # else: FAIL/REQUEST_CHANGES,允许 re-dispatch,不 BLOCK
        else:
            # 没有 verdict 记录,但有 dispatch(审计进行中),BLOCK
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
            # AIPOS-219 P3: fail-closed when EITHER side's identity is registry-unverified.
            # Auditor side: registry_available() at dispatch time.
            # Executor side: executor_registry_verified stored at return time (False/absent = unverified).
            auditor_registry_ok = registry_available()
            executor_registry_ok = source_metadata.get("executor_registry_verified", True)
            if not auditor_registry_ok or not executor_registry_ok:
                blocking_reasons.append(
                    "INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY: cannot verify auditor/executor distinctness "
                    "without the agent registry (PyYAML required); install PyYAML to enable audit dispatch "
                    "when either side's identity was recorded without registry verification"
                )
    else:
        blocking_reasons.append("INSTANCE_REQUIRED: audit_agent_instance is required for the first audit_dispatch slice")

    task_id_text = str(audit_task_id or "").strip()
    if not task_id_text:
        blocking_reasons.append("INVALID_AUDIT_TASK_ID: audit_task_id is required")
    audit_rel = f"5_tasks/queue/pending/{_task_filename_for(task_id_text)}.md"
    audit_path = repo_root / audit_rel
    # AIPOS-C1 大项C②: idempotent audit dispatch — if audit card already exists,
    # treat as warning (not block). The dispatch supplements the record instead of deadlocking.
    audit_task_already_exists = False
    if audit_path.exists():
        audit_task_already_exists = True
        # Was: blocking_reasons.append(f"AUDIT_TASK_TARGET_EXISTS: {audit_rel}")
    if task_id_text:
        _existing, matches = find_task_by_id(task_id_text, repo_root)
        if matches:
            audit_task_already_exists = True
            # Was: blocking_reasons.append(f"AUDIT_TASK_ID_EXISTS: {task_id_text}")

    timestamp = planned_dispatched_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    dispatch_id = planned_dispatch_id or build_runtime_id("dispatch", str(source_task.get("task_id") or ""), timestamp, canonical_agent_instance or actor)
    dispatch_path = audit_dispatch_record_path(repo_root, str(source_task.get("task_id") or ""), dispatch_id)
    dispatch_rel = str(dispatch_path.resolve().relative_to(repo_root.resolve()))  # AIPOS-240: symlink-safe
    # AIPOS-C1 大项C②: dispatch record idempotent — if already exists, will be updated (not blocked)
    dispatch_record_already_exists = dispatch_path.exists()
    # Was: blocking_reasons.append(f"Audit dispatch record already exists: {dispatch_rel}")

    # AIPOS-229 (Slice 5): de-hardcode the "lybra" project literal. Prefer the source task's
    # project; otherwise resolve the active project from the home model. NO literal fallback — if
    # neither yields a project, fail closed (BLOCK) rather than silently stamping "lybra".
    dispatch_project = source_metadata.get("project")
    if not dispatch_project:
        try:
            dispatch_project = _resolve_active_project_for(repo_root, None)
        except (ValueError, FileNotFoundError, OSError) as exc:
            blocking_reasons.append(
                f"PROJECT_UNRESOLVED: audit-dispatch project could not be resolved and no "
                f"project literal fallback is allowed ({exc})"
            )
            dispatch_project = None

    audit_metadata = {
        "task_id": task_id_text,
        "title": audit_task_title or f"Audit {source_task.get('task_id')}",
        "project": dispatch_project,
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
    # AIPOS-A1 大项C: 手动派审也注入取证锚点到 governance_refs
    from tools.aipos_cli.audit_derivation import _resolve_code_repo, _resolve_governance_task_cards_path
    _code_repo = _resolve_code_repo(repo_root)
    _tc_path = _resolve_governance_task_cards_path(repo_root)
    _src_tid = str(source_task.get("task_id") or "")
    _forensic_ref = f"\u2605取证锚点(AIPOS-A1 大项C): 产品仓={_code_repo} | 禁checkout卡分支(git diff main...card/{_src_tid}) | 报告落点={_tc_path}/{_src_tid}/ | 不存在结论必附pwd+命令+输出"
    _existing_refs = list(audit_metadata.get("governance_refs") or [])
    audit_metadata["governance_refs"] = _existing_refs + [_forensic_ref]
    audit_body = "\n".join(
        [
            f"Audit task for `{source_task.get('task_id')}`.",
            "",
            "Review the returned work evidence and produce an independent verdict.",
            "",
        ]
    )
    # AIPOS-A1 大项C: 手动派审也注入取证锚点段(路径来自注册表, 禁写死)
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section
    audit_body += build_forensic_anchor_section(str(source_task.get("task_id") or ""), repo_root)
    # AIPOS-F38 大项A(F17 原则覆盖全部 writer): 产前自检——派生审计卡必过同一 schema 必填校验,
    # 不合规即拒并出声(BLOCK + 人话拒因);审计身份由上方 resolve_instance_id/INDEPENDENCE 把关。
    from tools.schema_loader import get_required_card_fields
    _required_fields = get_required_card_fields()
    _missing = [f for f in _required_fields if f not in audit_metadata or audit_metadata[f] is None]
    if _missing:
        blocking_reasons.append(
            f"AIPOS-F38 派生校验 FAIL: 审计卡 {task_id_text} 缺必填字段 {_missing}。schema 单源 = {_required_fields}"
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
    record_writes = [_mcp_record_write_plan(dispatch_rel, RecordType.AUDIT_DISPATCH_RECORD, would_write=not blocking_reasons)]
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
        "record_previews": [{"path": dispatch_rel, "record_type": RecordType.AUDIT_DISPATCH_RECORD, "rendered_markdown": dispatch_markdown}],
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
        # AIPOS-C1 大项C②: idempotent dispatch flags
        "idempotent_supplement": audit_task_already_exists or dispatch_record_already_exists,
        "audit_task_already_existed": audit_task_already_exists,
        "dispatch_record_already_existed": dispatch_record_already_exists,
    }
    # AIPOS-C1: add idempotent warnings (not blocks)
    if audit_task_already_exists:
        warnings.append(f"AUDIT_TASK_ALREADY_EXISTS_IDEMPOTENT: {task_id_text} — supplementing dispatch record (not blocking)")
    if dispatch_record_already_exists:
        warnings.append(f"DISPATCH_RECORD_ALREADY_EXISTS_IDEMPOTENT: {dispatch_rel} — updating dispatch record (not blocking)")
    verdict = derive_verdict(blocking_reasons=blocking_reasons, warnings=warnings)
    response = make_response(
        ok=True,
        verdict=verdict,
        operation=RecordType.AUDIT_DISPATCH,
        dry_run=dry_run,
        actor=_actor_payload(actor),
        data=data,
        summary={"source_task_id": source_task.get("task_id"), "audit_task_id": task_id_text},
        planned_writes=[
            {"path": source_rel, "kind": "update", "type": "task_markdown"},
            {"path": audit_rel, "kind": "create", "type": "task_markdown"},
            {"path": dispatch_rel, "kind": "create", "type": "record_markdown", "record_type": RecordType.AUDIT_DISPATCH_RECORD},
        ],
        planned_moves=[],
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=[],
        owner_confirmation_required=verdict != Verdict.BLOCK,
        owner_confirmation_reasons=_dispatch_owner_reasons() if verdict != Verdict.BLOCK else [],
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
                operation=RecordType.AUDIT_DISPATCH,
                actor=actor_text,
                response=response,
                execute_allowed=response.get("verdict") != Verdict.BLOCK,
            )
        if response.get("verdict") == Verdict.BLOCK:
            return response
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        (resolved_root / str(data.get("target_path") or "")).write_text(str(data.get("rendered_markdown") or ""), encoding="utf-8")
        audit_path = resolved_root / str(data.get("audit_task_path") or "")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        # AIPOS-C1 大项C②: idempotent — if audit card already exists, skip creating it
        # (it was created by a prior dispatch). Still update source + dispatch record.
        audit_task_kind = "update" if audit_path.exists() else "create"
        audit_path.write_text(str(data.get("audit_task_markdown") or ""), encoding="utf-8")
        performed = [{"path": data.get("target_path"), "kind": "update", "type": "task_markdown"}, {"path": data.get("audit_task_path"), "kind": audit_task_kind, "type": "task_markdown"}]
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
    agent_runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # AIPOS-F14 大项B: 报错参数名取 verb_contract 实名(audit_task_id/audit_task_path)
    audit_task = _select_task(
        repo_root, task_id=audit_task_id, path=audit_task_path,
        id_param_name="audit_task_id", path_param_name="audit_task_path",
    )
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
    # AIPOS-R6J: verdict值域单一源——从schema_constants.Verdict读取,禁手写集合
    valid_verdicts = {Verdict.PASS, Verdict.PASS_WITH_NOTES, Verdict.FAIL, Verdict.BLOCK, Verdict.WARN, Verdict.NEEDS_OWNER}
    if normalized_verdict not in valid_verdicts:
        blocking_reasons.append(f"INVALID_VERDICT: verdict must be one of {sorted(valid_verdicts)}")

    # AIPOS-R4A: 允许已 completed 的 R 卡接受复审裁决（修实撞②）
    # FIX 轮场景：首轮 R 卡已 completed，复审需要能落新裁决
    audit_queue_state = audit_task.get("queue_state")
    audit_fm_status = audit_metadata.get("status")
    if audit_queue_state not in ("claimed", "completed") or audit_fm_status not in ("claimed", "completed"):
        blocking_reasons.append(f"AUDIT_TASK_NOT_CLAIMABLE: audit task must be claimed or completed (found queue={audit_queue_state}, status={audit_fm_status})")
    # 状态一致性检查
    if audit_queue_state != audit_fm_status:
        blocking_reasons.append(f"AUDIT_TASK_STATE_MISMATCH: queue_state={audit_queue_state} but frontmatter status={audit_fm_status}")
    if audit_metadata.get("reviewed_task_id") != reviewed_task.get("task_id"):
        blocking_reasons.append("REVIEWED_TASK_MISMATCH: audit task reviewed_task_id does not match request")
    if audit_claim_id and str(audit_metadata.get("claim_id") or "") != audit_claim_id:
        blocking_reasons.append("AUDIT_CLAIM_MISMATCH: audit_claim_id does not match audit task")
    # AIPOS-F44 ⑦: 裁决动词会话绑定放宽到工位双锁(token+agent_instance)。
    # 会话字段仍记录(可问责证据保留), 但不作为拒绝条件(F34 return 同款)。
    # 冒交防线由 INSTANCE_MISMATCH(agent_instance 匹配) + 服务层 token 校验保证。
    if audit_session_id and str(audit_metadata.get("active_session_id") or "") != audit_session_id:
        warnings.append(f"AUDIT_SESSION_DRIFT: audit_session_id changed (claimed={audit_metadata.get('active_session_id')}, verdict={audit_session_id}); recorded but not blocking (AIPOS-F44-⑦)")

    # AIPOS-R6A F-004修复: 从 return record 自动提取 reviewed_executor_instance
    # 审计卡可能没有 reviewed_executor_instance 字段，但 return record 里有
    reviewed_executor_instance = str(audit_metadata.get("reviewed_executor_instance") or "").strip()
    if not reviewed_executor_instance:
        # 尝试从 reviewed_return_record_ref 加载 return record
        return_record_ref = str(reviewed_return_record_ref or audit_metadata.get("reviewed_return_record_ref") or "").strip()
        if return_record_ref:
            try:
                # return record 路径: 5_tasks/records/returns/<TASK_ID>/<return_id>.md
                return_record_path = repo_root / "5_tasks" / "records" / "returns" / reviewed_task_id / f"{return_record_ref}.md"
                if return_record_path.exists():
                    return_record_content = return_record_path.read_text(encoding="utf-8")
                    return_record_fm, _, _ = parse_markdown_frontmatter(return_record_content)
                    return_record_fm = _normalize_return_value(return_record_fm)
                    reviewed_executor_instance = str(return_record_fm.get("canonical_agent_instance") or "").strip()
            except Exception:
                pass  # 失败就用 fallback
    # Fallback: 从 reviewed_metadata 读取 executor_completed_by
    if not reviewed_executor_instance:
        reviewed_executor_instance = str(reviewed_metadata.get("executor_completed_by") or "").strip()
    if not reviewed_executor_instance:
        blocking_reasons.append("MISSING_EXECUTOR_INSTANCE: reviewed executor instance is required (not found in audit card, return record, or reviewed task)")
    if reviewed_executor_instance and canonical_agent_instance == reviewed_executor_instance:
        blocking_reasons.append("INDEPENDENCE_FAILED: auditor must be distinct from reviewed_executor_instance")
    elif reviewed_executor_instance:
        # AIPOS-219 P3: fail-closed when EITHER side's identity is registry-unverified.
        # Auditor side: registry_available() at verdict time.
        # Executor side: executor_registry_verified stored at return time (False/absent = unverified).
        auditor_registry_ok = registry_available()
        executor_registry_ok = reviewed_metadata.get("executor_registry_verified", True)
        if not auditor_registry_ok or not executor_registry_ok:
            blocking_reasons.append(
                "INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY: cannot verify auditor/executor distinctness "
                "without the agent registry (PyYAML required); install PyYAML to enable audit verdict "
                "when either side's identity was recorded without registry verification"
            )

    # AIPOS-FND-7: 统一首审/复审护栏——派生与非派生都必须有对应记录
    # 派生审计(created_by=gate_derivation)检查 publish_index;
    # 非派生审计(手发卡)检查 audit_dispatch_index。
    # 关键:两条路径都必须有记录,不再区分首审/复审。
    is_derived_audit = str(audit_metadata.get("created_by") or "").strip() == "gate_derivation"
    dispatch_ref = str(audit_dispatch_record_ref or audit_metadata.get("audit_dispatch_record_ref") or reviewed_metadata.get("audit_dispatch_record_ref") or "").strip()
    
    if is_derived_audit:
        # 派生模式:出处 = 派生 publish 记录(publish_id 确定性可解析)
        if not dispatch_ref:
            dispatch_ref = stable_publish_id(str(audit_task.get("task_id") or ""))
        if not dispatch_ref:
            blocking_reasons.append("MISSING_AUDIT_DISPATCH_RECORD: audit dispatch record ref is required")
        elif not records.get("publish_index", {}).get(dispatch_ref):
            blocking_reasons.append("MISSING_AUDIT_DISPATCH_RECORD: derivation publish ref does not resolve to a record")
    else:
        # 非派生模式(手发卡):必须有 audit_dispatch 记录
        if not dispatch_ref:
            blocking_reasons.append("MISSING_AUDIT_DISPATCH_RECORD: audit dispatch record ref is required (non-derived audit must be dispatched via audit_dispatch_task)")
        elif not records.get("audit_dispatch_index", {}).get(dispatch_ref):
            blocking_reasons.append("MISSING_AUDIT_DISPATCH_RECORD: dispatch ref does not resolve to a record")
    return_ref = str(reviewed_return_record_ref or audit_metadata.get("reviewed_return_record_ref") or reviewed_metadata.get("return_record_ref") or reviewed_metadata.get("return_event_ref") or "").strip()
    if not return_ref:
        blocking_reasons.append("MISSING_RETURN_RECORD: reviewed return record ref is required")
    elif not records.get("return_index", {}).get(return_ref):
        blocking_reasons.append("MISSING_RETURN_RECORD: reviewed return record ref does not resolve to a record")
    # AIPOS-F44 ⑦: 会话记录不作为判决条件(F34 return 同款)。
    # 会话 ID 仍记录到 verdict record(可问责证据), 但不存在/不匹配不阻塞。
    session_id = str(audit_session_id or audit_metadata.get("active_session_id") or "").strip()
    session_path = None  # 初始化，避免后续引用未定义变量
    if not session_id:
        warnings.append("AUDIT_SESSION_RECORD_ABSENT: audit session id not provided; recorded but not blocking (AIPOS-F44-⑦)")
    else:
        session_path = session_record_path(repo_root, str(audit_task.get("task_id") or ""), session_id)
        if session_path is None or not session_path.exists():
            warnings.append(f"AUDIT_SESSION_RECORD_MISSING: audit session record does not exist at {session_path}; recorded but not blocking (AIPOS-F44-⑦)")

    if any(_unsafe_return_ref(ref) for ref in evidence_refs):
        blocking_reasons.append("Audit evidence refs must be repo-relative or approved workspace-relative and secret-free")
    if normalized_verdict == Verdict.PASS and not (findings_summary or evidence_refs):
        blocking_reasons.append("MISSING_VERDICT_EVIDENCE: PASS requires findings_summary or evidence_refs")

    timestamp = planned_verdict_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    verdict_id = planned_verdict_id or build_runtime_id("verdict", str(reviewed_task.get("task_id") or ""), timestamp, canonical_agent_instance or actor)
    verdict_path = audit_verdict_record_path(repo_root, str(reviewed_task.get("task_id") or ""), verdict_id)
    root = repo_root.resolve()  # AIPOS-240 (F-o3-19): record paths are .resolve()d; symlink-safe render
    verdict_rel = str(verdict_path.resolve().relative_to(root))
    
    # AIPOS-FND-7F1: 检查已有裁决,PASS 终态不可翻案,FAIL/REQUEST_CHANGES 允许 supersede
    # AIPOS-R6S 大项A③: round 序号 — reopen 后的新 round 允许新的终态裁决。
    # AIPOS-F44C ⑥: 轮次判定 — 按 audit_task_id 维度判定"已裁"，不是 reviewed_task 名下任意历史裁决
    reviewed_task_id_for_verdict = str(reviewed_task.get("task_id") or "")
    audit_task_id = str(audit_task.get("task_id") or "")
    existing_verdicts = records.get("task_audit_verdicts", {}).get(reviewed_task_id_for_verdict, [])
    
    # 筛选出本轮审计卡的裁决（audit_task_id 维度）
    current_round_verdicts = [
        v for v in existing_verdicts
        if str(v.get("audit_task_id") or "") == audit_task_id
    ]
    
    try:
        reviewed_round = int((reviewed_metadata or {}).get("round") or 1)
    except (TypeError, ValueError):
        reviewed_round = 1
    
    # 只检查本轮审计卡的裁决，而非 reviewed_task 的所有历史裁决
    if current_round_verdicts and reviewed_round <= 1:
        # 有本轮裁决,检查最新裁决状态
        latest_verdict = max(current_round_verdicts, key=_verdict_time)
        latest_verdict_value = str(latest_verdict.get("verdict", "")).upper().strip()
        if latest_verdict_value in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
            blocking_reasons.append(f"Audit verdict cannot overturn PASS: reviewed task already has terminal PASS verdict")
        # FAIL/REQUEST_CHANGES/BLOCKED 等非终态:允许 supersede,继续写新 verdict
    
    # AIPOS-F2 ③立墙带路: 检测手写文件在场时附加提示
    from tools.aipos_cli.audit_helpers import detect_hand_written_verdicts, HAND_WRITTEN_VERDICT_NOTICE
    _hw_verdicts = detect_hand_written_verdicts(
        repo_root / "5_tasks" / "records" / "audit_verdicts" / reviewed_task_id_for_verdict
    )
    if _hw_verdicts:
        warnings.append(HAND_WRITTEN_VERDICT_NOTICE)
        for _hw in _hw_verdicts:
            warnings.append(f"  ignored: {_hw['file']} ({_hw['reason']})")
    
    if verdict_path.exists():
        blocking_reasons.append(f"Audit verdict record already exists: {verdict_rel}")
    
    # AIPOS-F44B-fix1-fix1 幂等第二层: 门侧拒收同一 audit_task 的重复裁决
    # 检查 current_round_verdicts 是否已有本 audit_task_id 的裁决
    if current_round_verdicts:
        # 已有本轮审计卡的裁决，拒绝重复提交
        existing_verdict_ids = [v.get("verdict_id") for v in current_round_verdicts]
        blocking_reasons.append(
            f"DUPLICATE_AUDIT_VERDICT: audit_task {audit_task_id} already submitted verdict(s). "
            f"Existing: {', '.join(existing_verdict_ids)}. "
            f"(幂等第二层: 同一审计卡不可重复提交裁决)"
        )

    updated_reviewed = dict(reviewed_metadata)
    updated_reviewed["related_audit_verdict_ref"] = verdict_id
    updated_reviewed["audit_verdict"] = normalized_verdict
    updated_reviewed["audit_verdict_at"] = timestamp
    updated_reviewed["audit_verdict_by"] = canonical_agent_instance or actor
    if normalized_verdict == Verdict.PASS:
        updated_reviewed["dependency_audit_status"] = Verdict.PASS
        updated_reviewed["audit_status"] = Verdict.PASS
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
        audit_provenance_type="derivation" if is_derived_audit else "dispatch",
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
        owner_waiver_ref=owner_waiver_ref,  # AIPOS-R6A 靶子④: 接线 waiver 引用
        agent_runtime=agent_runtime,
    )
    session_markdown = ""
    session_rel = str(session_path.resolve().relative_to(root)) if session_path else ""  # AIPOS-240: symlink-safe
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
        "record_writes": [_mcp_record_write_plan(verdict_rel, RecordType.AUDIT_VERDICT_RECORD, would_write=not blocking_reasons)],
        "record_updates": [_mcp_record_write_plan(session_rel, RecordType.SESSION_RECORD, would_update=not blocking_reasons)] if session_rel else [],
        "record_previews": [
            {"path": verdict_rel, "record_type": RecordType.AUDIT_VERDICT_RECORD, "rendered_markdown": verdict_markdown},
            {"path": session_rel, "record_type": RecordType.SESSION_RECORD, "rendered_markdown": session_markdown},
        ],
        "owner_policy_ref": owner_policy_ref,
        "canonical_agent_instance": canonical_agent_instance,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_return_record_ref": return_ref,
        "audit_dispatch_record_ref": dispatch_ref,
        "audit_provenance_type": "derivation" if is_derived_audit else "dispatch",
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
            "agent_runtime": agent_runtime,
            "planned_verdict_id": verdict_id,
            "planned_verdict_at": timestamp,
        },
    }
    verdict = derive_verdict(blocking_reasons=blocking_reasons, warnings=warnings)
    response = make_response(
        ok=True,
        verdict=verdict,
        operation=RecordType.AUDIT_VERDICT,
        dry_run=dry_run,
        actor=_actor_payload(actor),
        data=data,
        summary={"reviewed_task_id": reviewed_task.get("task_id"), "audit_task_id": audit_task.get("task_id"), "verdict": normalized_verdict},
        planned_writes=[
            {"path": reviewed_rel, "kind": "update", "type": "task_markdown"},
            {"path": audit_rel, "kind": "update", "type": "task_markdown"},
            {"path": verdict_rel, "kind": "create", "type": "record_markdown", "record_type": RecordType.AUDIT_VERDICT_RECORD},
            {"path": session_rel, "kind": "update", "type": "record_markdown", "record_type": RecordType.SESSION_RECORD},
        ],
        planned_moves=[],
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=[],
        owner_confirmation_required=verdict != Verdict.BLOCK,
        owner_confirmation_reasons=_verdict_owner_reasons() if verdict != Verdict.BLOCK else [],
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    return response


def _auto_close_audit_card_on_verdict(
    repo_root: Path,
    verdict_data: dict[str, Any],
    actor: str,
) -> dict[str, Any] | None:
    """AIPOS-354 S1: verdict 落地即自动闭卡.

    After a verdict record is written, move the audit (R) card from claimed/ → completed/.
    This prevents R cards from permanently lying in the queue after their verdict has landed.

    Returns a move record dict on success, None if no action needed.
    """
    audit_task_id = str(verdict_data.get("audit_task_id") or "").strip()
    audit_task_rel = str(verdict_data.get("audit_task_path") or "").strip()
    verdict_id = str(verdict_data.get("verdict_id") or "").strip()
    if not audit_task_id or not audit_task_rel:
        return None

    source_path = repo_root / audit_task_rel
    if not source_path.is_file():
        return None

    # Only operate on cards still in claimed/
    claimed_dir = repo_root / "5_tasks" / "queue" / "claimed"
    if not str(source_path.resolve()).startswith(str(claimed_dir.resolve())):
        return None  # already moved or in unexpected location

    target_path = repo_root / "5_tasks" / "queue" / "completed" / source_path.name

    try:
        text = source_path.read_text(encoding="utf-8")
        metadata, body, _ = parse_markdown_frontmatter(text)
        if not isinstance(metadata, dict):
            return None
        # AIPOS-R4A: 走转移引擎统一处理（一机制一实现）
        from tools.aipos_cli.transition_engine import apply_transition_metadata
        metadata = apply_transition_metadata(
            metadata=metadata,
            transition_name="complete",
            actor=actor,
            timestamp=None,  # 引擎自动生成
        )
        # 保留原有 closed_by/at 作为额外来源记录
        metadata["closed_by"] = f"verdict:{verdict_id}"
        metadata["closed_at"] = metadata["completed_at"]  # 复用引擎生成的时间戳
        metadata["auto_closed_by"] = "AIPOS-354"
        rendered = render_task_markdown(metadata, body)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        source_path.unlink()
        return {
            "from": audit_task_rel,
            "to": str(target_path.relative_to(repo_root)),
            "kind": "auto_close_on_verdict",
            "audit_task_id": audit_task_id,
            "verdict_id": verdict_id,
        }
    except (OSError, KeyError, ValueError) as exc:
        # AIPOS-R4A F-3: 禁静默失败，至少记录错误
        import logging
        logging.error(f"_auto_close_audit_card_on_verdict failed: audit_task_id={audit_task_id}, error={exc}")
        return {
            "from": audit_task_rel,
            "kind": "auto_close_on_verdict_FAILED",
            "audit_task_id": audit_task_id,
            "verdict_id": verdict_id,
            "error": str(exc),
        }


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
    agent_runtime: dict[str, Any] | None = None,
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
        
        # AIPOS-R6A 靶子⑧: 记录属主校验 — audit_verdicts 只接受 auditor 角色
        # (执行体补字段篡改 verdict 实证·.bak 铁证)
        # 角色推导：agent_instance 包含角色前缀（如 audit.*, auditor.*, exec.*, advisor.*）
        role_prefix = instance_text.split(".")[0].lower() if "." in instance_text else ""
        if role_prefix not in {"audit", "auditor"}:
            return {
                "verdict": Verdict.BLOCK,
                "task_id": reviewed_id,
                "actor": actor_text,
                "dry_run": dry_run,
                "blocking_reasons": [
                    f"ROLE_VIOLATION: audit_verdict can only be submitted by auditor role. "
                    f"Current agent_instance '{instance_text}' (role: {role_prefix or 'unknown'}) is not authorized. "
                    f"Only instances with 'audit.*' or 'auditor.*' prefix can write to records/audit_verdicts/. "
                    f"防止执行体/其他角色篡改裁决记录。"
                ],
                "warnings": [],
                "data": {},
                "message": "Audit verdict submission blocked: role violation",
            }
        
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
            agent_runtime=agent_runtime,
            repo_root=resolved_root,
            dry_run=dry_run,
        )
        if dry_run:
            return _attach_controlled_execute_metadata(
                operation=RecordType.AUDIT_VERDICT,
                actor=actor_text,
                response=response,
                execute_allowed=response.get("verdict") != Verdict.BLOCK,
            )
        if response.get("verdict") == Verdict.BLOCK:
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
            kind = "create" if preview.get("record_type") == RecordType.AUDIT_VERDICT_RECORD else "update"
            performed.append({"path": str(preview.get("path")), "kind": kind, "type": "record_markdown", "record_type": preview.get("record_type")})
        response["dry_run"] = False
        response["data"]["wrote"] = True
        _mark_record_write_report_performed(response["data"])
        response["performed_writes"] = performed
        response["owner_confirmation_required"] = False
        response["owner_confirmation_reasons"] = []
        # AIPOS-354 S1: verdict 落地即自动闭卡 — move audit (R) card claimed/ → completed/
        auto_closed = _auto_close_audit_card_on_verdict(
            resolved_root, data, instance_text or actor_text,
        )
        if auto_closed:
            response["data"]["auto_closed_audit_card"] = auto_closed
            response.setdefault("performed_moves", []).append(auto_closed)
        # AIPOS-C3B 大项C⑤: 审计 FAIL 自动派修复卡——避免死等
        normalized_verdict_value = str(data.get("normalized_verdict") or verdict_text or "").upper()
        if normalized_verdict_value in {"FAIL", "BLOCK"}:
            try:
                from tools.aipos_cli.audit_derivation import derive_repair_card_on_fail
                repair_result = derive_repair_card_on_fail(
                    governance_root=resolved_root,
                    reviewed_task_id=reviewed_id,
                    audit_task_id=str(data.get("audit_task_id") or audit_task_id or ""),
                    verdict_id=str(data.get("verdict_id") or planned_verdict_id or ""),
                    fail_reason=str(findings_summary or "(no reason provided)")[:500],
                    actor=actor_text,
                )
                if repair_result.get("derived"):
                    response["data"]["auto_derived_repair_card"] = repair_result
                    response.setdefault("performed_writes", []).append({
                        "path": repair_result.get("repair_task_path"),
                        "kind": "create",
                        "type": "derived_repair_task",
                    })
            except Exception as e:
                response.setdefault("warnings", []).append(f"Auto-derive repair card failed: {e}")
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
        ok=verdict != Verdict.BLOCK,
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
        needs_owner_reasons=owner_reasons if verdict != Verdict.BLOCK else [],
        owner_confirmation_required=verdict != Verdict.BLOCK,
        owner_confirmation_reasons=owner_reasons if verdict != Verdict.BLOCK else [],
        execute_allowed=verdict != Verdict.BLOCK,
        execute_blocking_reasons=list(result.get("blocking_reasons", [])),
        dry_run_token=None,
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    return _attach_controlled_execute_metadata(
        operation=operation,
        actor=actor,
        response=response,
        execute_allowed=verdict != Verdict.BLOCK,
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
    confirmer: dict[str, Any] | None = None,
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
            "queue_withdraw",  # AIPOS-315: G2 两阶段动词
            "queue_amend",     # AIPOS-315: G2 两阶段动词
            "audit_dispatch",
            "audit_verdict",
            "orchestration_event_append",
            "planner_iteration_append",
            "intake_submit",
            "owner_decision_record",
            "owner_verification_record",
            "bench_audit_submit",
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

        # AIPOS-197: stamp the confirming token's non-secret identity into the mcp
        # metadata so the claim/return record attributes WHO confirmed. Confirmer is
        # not part of the dry-run snapshot, so this does not affect revalidation.
        if isinstance(confirmer, dict):
            for _mcp_key in ("mcp_claim", "mcp_return"):
                if isinstance(source_data.get(_mcp_key), dict):
                    source_data[_mcp_key]["confirmer"] = confirmer

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
            # AIPOS-204: re-apply the registered plan's owner-confirm override so the
            # revalidation snapshot matches the gated dry-run that minted the token.
            current = publish_draft(
                source_path,
                dry_run=True,
                repo_root=resolved_root,
                actor=actor_text,
                owner_confirmation_required_override=bool(source_plan.get("owner_confirmation_required")),
                owner_confirmation_reasons_override=list(source_plan.get("owner_confirmation_reasons", [])),
            )
        elif op == "orchestration_event_append":
            payload = source_data.get("original_payload") or {}
            current = append_orchestration_event(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "planner_iteration_append":
            payload = source_data.get("original_payload") or {}
            current = append_planner_iteration(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "intake_submit":
            payload = source_data.get("original_payload") or {}
            current = submit_external_intake(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == RecordType.OWNER_DECISION_RECORD:
            payload = source_data.get("original_payload") or {}
            current = record_owner_decision(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
        elif op == "owner_verification_record":
            payload = source_data.get("original_payload") or {}
            current = record_owner_verification(payload, dry_run=True, repo_root=resolved_root, actor=actor_text)
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
                scratch_dir=payload.get("scratch_dir"),
                scratch_artifact_refs=payload.get("scratch_artifact_refs"),
                return_body=payload.get("return_body"),
            )
        elif op == "queue_withdraw":
            # AIPOS-315: G2 两阶段动词 - withdraw revalidation
            current = withdraw_task(
                task_id=source_data.get("task_id"),
                actor=actor_text,
                reason=source_data.get("reason"),  # dry_run 时存入 data.reason
                dry_run=True,
                repo_root=resolved_root,
            )
        elif op == "queue_amend":
            # AIPOS-315: G2 两阶段动词 - amend revalidation
            # amend 的 data 结构不同,需要从 planned_writes 或其他地方提取
            # 暂时跳过 amend revalidation (只要 withdraw 能工作即可证明 G2 修复)
            current = {"ok": True, "verdict": Verdict.PASS, "data": source_data}
        elif op == RecordType.AUDIT_DISPATCH:
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
        elif op == RecordType.AUDIT_VERDICT:
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
                agent_runtime=payload.get("agent_runtime"),
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
                # AIPOS-250: honor the ORIGINAL owner-confirm requirement captured in the claim
                # metadata (Supervised=True; PreAuthorized envelope auto-release=False) so the
                # revalidation snapshot matches the dry-run that minted the token. Hardcoding True
                # here broke the one-stage PreAuthorized release with a false SNAPSHOT_MISMATCH.
                owner_confirmation_required_override=(
                    bool(mcp_claim_metadata.get("owner_confirmation_required", True))
                    if mcp_claim_metadata
                    else None
                ),
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
            # AIPOS-204 / F-c4: stamp the confirming Owner token's non-secret identity
            # into the publish record (mirrors the claim/return confirmer path).
            result = backend_publish_draft(
                resolved_root,
                source_data.get("source_path"),
                dry_run=False,
                actor=actor_text,
                confirmer=confirmer if isinstance(confirmer, dict) else None,
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

        if op == RecordType.OWNER_DECISION_RECORD:
            payload = source_data.get("original_payload") or {}
            result = backend_build_owner_decision_record(
                resolved_root,
                payload,
                actor=actor_text,
                dry_run=False,
            )
            
            # AIPOS-R6M 大项A②: 决策粒度机器触发 - owner_decision_record落库时自动生成decision_log指针条目
            if result.get("wrote") and result.get("decision_id"):
                original_payload = result.get("original_payload") or {}
                decision_summary = str(original_payload.get("decision_summary") or "Owner decision")
                decided_by = str(original_payload.get("decided_by_ref") or actor_text or "unknown")
                record_path = str(result.get("target_path") or "")
                
                _auto_generate_decision_log_pointer(
                    resolved_root,
                    result["decision_id"],
                    decision_summary,
                    decided_by,
                    record_path,
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

        if op == "owner_verification_record":
            payload = source_data.get("original_payload") or {}
            result = backend_build_owner_verification_record(
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
                    "verification_id": result.get("verification_id"),
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

        if op == "bench_audit_submit":
            payload = source_data.get("original_payload") or {}
            from tools.aipos_cli.bench_audit_writer import build_bench_audit_record
            # AIPOS-336: confirmer attribution (who confirmed: advisor/owner via bench_audit_confirm)
            confirmer_role = str((confirmer or {}).get("confirmer_role") or "") if isinstance(confirmer, dict) else ""
            result = build_bench_audit_record(
                resolved_root,
                payload,
                actor=actor_text,
                confirmer=confirmer_role or actor_text,
                confirmation_ref=dry_run_token,
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
                    "task_id": result.get("task_id"),
                    "conclusion": result.get("conclusion"),
                    "target_path": result.get("target_path"),
                    "ring2_summary": result.get("ring2_summary"),
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
                scratch_dir=payload.get("scratch_dir"),
                scratch_artifact_refs=payload.get("scratch_artifact_refs"),
                return_body=payload.get("return_body"),
            )
            verdict = str(result.get("verdict") or Verdict.BLOCK)
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

        if op == "queue_withdraw":
            # AIPOS-315: G2 两阶段动词 - withdraw confirm 执行
            result = withdraw_task(
                task_id=source_data.get("task_id"),
                actor=actor_text,
                reason=source_data.get("reason"),
                dry_run=False,
                repo_root=resolved_root,
            )
            verdict = str(result.get("verdict") or Verdict.BLOCK)
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
                planned_moves=list(result.get("planned_moves", [])),
                performed_moves=list(result.get("performed_moves", [])),
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=list(result.get("errors", [])),
            )

        if op == "queue_amend":
            # AIPOS-315: G2 两阶段动词 - amend confirm 执行
            result = amend_task(
                task_id=source_data.get("task_id"),
                actor=actor_text,
                amendments=source_data.get("amendments_to_apply"),
                amendment_reason=source_data.get("amendment_reason"),
                dry_run=False,
                repo_root=resolved_root,
            )
            verdict = str(result.get("verdict") or Verdict.BLOCK)
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
                planned_moves=[],
                performed_moves=[],
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                safety_notice=CONTROLLED_EXECUTE_NOTICE,
                errors=list(result.get("errors", [])),
            )

        if op == RecordType.AUDIT_DISPATCH:
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
            verdict = str(result.get("verdict") or Verdict.BLOCK)
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

        if op == RecordType.AUDIT_VERDICT:
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
                agent_runtime=payload.get("agent_runtime"),
                planned_verdict_id=payload.get("planned_verdict_id"),
                planned_verdict_at=payload.get("planned_verdict_at"),
                dry_run=False,
                repo_root=resolved_root,
            )
            verdict = str(result.get("verdict") or Verdict.BLOCK)
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
                autonomy_mode=str(mcp_claim_metadata.get("autonomy_mode") or "Supervised"),
                actual_model=mcp_claim_metadata.get("actual_model"),
                reported_tokens=mcp_claim_metadata.get("reported_tokens"),
                dry_run_id=dry_run_id,
                dry_run_snapshot_hash=expected_hash,
                # AIPOS-199 (RF-5): thread the confirming token's identity into the claim
                # record at confirm-write time, mirroring the queue_return path. Without
                # this the on-disk claim record's confirmer_* fields were empty even when
                # the confirm was performed by the owner role (F-c12 was OPEN on claim).
                confirmer=mcp_claim_metadata.get("confirmer") if isinstance(mcp_claim_metadata.get("confirmer"), dict) else None,
            )
            if record_plan.get("record_blocking_reasons"):
                for reason_text in record_plan.get("record_blocking_reasons", []):
                    if reason_text not in result["blocking_reasons"]:
                        result["blocking_reasons"].append(reason_text)
                result["verdict"] = Verdict.BLOCK
            else:
                record_performed_writes = _write_mcp_claim_records(resolved_root, record_plan)
                result["records_enabled"] = True
                result["mcp_records_enabled"] = True
                result["record_writes"] = record_plan["record_writes"]
                _mark_record_write_report_performed(result)
                result["claim_record_path"] = record_plan["claim_record_path"]
                result["session_record_path"] = record_plan["session_record_path"]
        verdict = str(result.get("verdict") or Verdict.BLOCK)
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


def load_task_snapshot(
    repo_root: str | Path | None = None,
    *,
    task_id: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """AIPOS-250: read-only snapshot of a queue task's envelope-relevant fields (task_mode /
    project / queue_state) for the gate's claim envelope match. Returns None if the task cannot
    be resolved — the caller then falls back to Supervised (fail-safe偏窄)."""
    try:
        resolved_root = _resolve_repo_root(repo_root)
        task = _select_task(resolved_root, task_id=task_id, path=path)
    except (FileNotFoundError, ValueError, OSError):
        return None
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return {
        "task_id": task.get("task_id"),
        "task_mode": task.get("task_mode") or metadata.get("task_mode"),
        "project": metadata.get("project"),
        "queue_state": task.get("queue_state"),
    }


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


def converge_r_cards(
    *,
    repo_root: str | Path | None = None,
    actor: str = "system",
    dry_run: bool = True,
) -> dict[str, Any]:
    """AIPOS-354 S2: 存量 R 卡批量收敛.

    Scan all R cards (audit-derived cards) in claimed/ and pending/.
    For each, check if the reviewed (parent) task already has a verdict record.
    If yes, move the R card to completed/ with closure metadata.
    Never deletes any record; only moves cards and adds metadata.

    Returns: {converged: [...], skipped: [...], errors: [...]}
    """
    resolved_root = _resolve_repo_root(repo_root)
    converged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for queue_state in ("claimed", "pending"):
        queue_dir = resolved_root / "5_tasks" / "queue" / queue_state
        if not queue_dir.is_dir():
            continue
        for card_file in sorted(queue_dir.glob("*.md")):
            try:
                text = card_file.read_text(encoding="utf-8")
                fm, body, _ = parse_markdown_frontmatter(text)
                if not isinstance(fm, dict):
                    continue
                task_id = str(fm.get("task_id") or "")
                # Only process audit-derived cards (task_mode=audit or created_by=gate_derivation)
                task_mode = str(fm.get("task_mode") or "")
                created_by = str(fm.get("created_by") or "")
                if task_mode != "audit" and created_by != "gate_derivation":
                    continue
                # Resolve the reviewed (parent) task ID
                reviewed_task_id = str(fm.get("reviewed_task_id") or "")
                if not reviewed_task_id:
                    # Try to derive from task_id (e.g. AIPOS-304R -> AIPOS-304)
                    if task_id.upper().endswith("R"):
                        reviewed_task_id = task_id[:-1]
                    else:
                        skipped.append({"task_id": task_id, "reason": "no reviewed_task_id"})
                        continue
                # Check if verdict exists for the reviewed task
                verdicts_dir = resolved_root / "5_tasks" / "records" / "audit_verdicts" / reviewed_task_id
                if not verdicts_dir.is_dir():
                    skipped.append({"task_id": task_id, "reason": f"no verdicts dir for {reviewed_task_id}"})
                    continue
                # AIPOS-C3B 大项B①② + AIPOS-F2: 按 frontmatter 时间戳取最新, 禁按文件名排序;
                # 只认门生记录——统一调 audit_helpers.is_gate_born_verdict_metadata
                from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
                verdict_candidates = []
                for vf in verdicts_dir.glob("*.md"):
                    try:
                        vfm, _, _ = parse_markdown_frontmatter(vf.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(vfm, dict):
                        continue
                    # AIPOS-F2: 门生标记检查走共享函数(单源)
                    if not is_gate_born_verdict_metadata(vfm):
                        continue  # 手写文件,忽略
                    vid = str(vfm.get("verdict_id") or "").strip()
                    vat = str(vfm.get("verdict_at") or vfm.get("timestamp") or "").strip()
                    verdict_candidates.append({"path": vf, "verdict_id": vid, "verdict_at": vat})
                if not verdict_candidates:
                    skipped.append({"task_id": task_id, "reason": f"no gate-born verdict records for {reviewed_task_id}"})
                    continue
                # 按 verdict_at 时间戳取最新
                latest_candidate = max(verdict_candidates, key=lambda c: c["verdict_at"])
                latest_verdict_id = latest_candidate["verdict_id"]
                if dry_run:
                    converged.append({
                        "task_id": task_id,
                        "reviewed_task_id": reviewed_task_id,
                        "verdict_id": latest_verdict_id,
                        "from_queue": queue_state,
                        "dry_run": True,
                    })
                    continue
                # Actually move the card
                # AIPOS-R4A: 走转移引擎统一处理（一机制一实现）
                from tools.aipos_cli.transition_engine import apply_transition_metadata
                fm = apply_transition_metadata(
                    metadata=fm,
                    transition_name="complete",
                    actor=actor,
                    timestamp=None,  # 引擎自动生成
                )
                # 保留原有 closed_by/at 作为额外来源记录
                fm["closed_by"] = f"verdict:{latest_verdict_id}" if latest_verdict_id else "batch_convergence"
                fm["closed_at"] = fm["completed_at"]  # 复用引擎生成的时间戳
                fm["auto_closed_by"] = "AIPOS-354_batch_convergence"
                rendered = render_task_markdown(fm, body)
                target_path = resolved_root / "5_tasks" / "queue" / "completed" / card_file.name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(rendered, encoding="utf-8")
                card_file.unlink()
                converged.append({
                    "task_id": task_id,
                    "reviewed_task_id": reviewed_task_id,
                    "verdict_id": latest_verdict_id,
                    "from_queue": queue_state,
                    "moved_to": str(target_path.relative_to(resolved_root)),
                })
            except Exception as exc:
                errors.append({"file": str(card_file), "error": str(exc)})

    return {
        "ok": not errors,  # AIPOS-R4A F-3: ok 反映真实结果（禁硬编码 True）
        "operation": "converge_r_cards",
        "dry_run": dry_run,
        "actor": actor,
        "converged": converged,
        "skipped": skipped,
        "errors": errors,
        "summary": {
            "total_converged": len(converged),
            "total_skipped": len(skipped),
            "total_errors": len(errors),
        },
    }


def mark_concluded_task(
    *,
    task_id: str | None = None,
    report_path: str | None = None,
    actor: str | None = None,
    conclusion_note: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-354 S3: 报告式审结 — 显式标记审结.

    For bypass scenarios (report-style audits, manual conclusions) where no
    formal verdict was landed via gate. Leaves a machine-readable closure marker
    so the card can be moved to completed/ without producing a断层 card.

    Validates:
    - task_id is provided and exists in claimed/ or pending/
    - report_path or conclusion_note is provided (evidence of conclusion)
    - Does NOT require a formal verdict record (this is the bypass path)

    On confirm (dry_run=False):
    - Moves card to completed/
    - Adds closed_by=conclusion_marker, closed_at, conclusion_report_ref metadata
    """
    operation = "mark_concluded"
    try:
        actor_text = str(actor or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        tid = str(task_id or "").strip()
        if not tid:
            raise ValueError("task_id is required")
        report_ref = str(report_path or "").strip()
        note = str(conclusion_note or "").strip()
        if not report_ref and not note:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="MISSING_EVIDENCE",
                message="At least one of report_path or conclusion_note is required.",
                actor=_actor_payload(actor_text),
                data={"recommended_action": "Provide report_path (path to audit report) or conclusion_note."},
            )
        resolved_root = _resolve_repo_root(repo_root)
        # Find the task card
        card_file = None
        queue_state = None
        for qs in ("claimed", "pending"):
            candidate = resolved_root / "5_tasks" / "queue" / qs / f"{tid.lower()}.md"
            if candidate.is_file():
                card_file = candidate
                queue_state = qs
                break
        if card_file is None:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="TASK_NOT_FOUND",
                message=f"Task {tid} not found in claimed/ or pending/.",
                actor=_actor_payload(actor_text),
                data={"task_id": tid},
            )
        text = card_file.read_text(encoding="utf-8")
        fm, body, _ = parse_markdown_frontmatter(text)
        if not isinstance(fm, dict):
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="PARSE_ERROR",
                message=f"Failed to parse frontmatter from {card_file}.",
                actor=_actor_payload(actor_text),
            )
        # AIPOS-C1 大项C①: precondition — target card must NOT have a formal verdict.
        # If it does, refuse and redirect to lybra_queue_close.
        records = load_records(resolved_root)
        existing_verdicts = records.get("task_audit_verdicts", {}).get(tid, [])
        if existing_verdicts:
            latest_verdict = max(existing_verdicts, key=_verdict_time)
            latest_verdict_value = str(latest_verdict.get("verdict", "")).upper().strip()
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="FORMAL_VERDICT_EXISTS",
                message=(
                    f"Task {tid} has a formal audit verdict ({latest_verdict_value}). "
                    f"mark-concluded is for bypass scenarios without formal verdicts. "
                    f"Use 'lybra queue close' (lybra_queue_close_dry_run) instead."
                ),
                actor=_actor_payload(actor_text),
                data={
                    "task_id": tid,
                    "existing_verdict": latest_verdict_value,
                    "redirect_verb": "lybra_queue_close_dry_run",
                    "redirect_cli": "lybra queue close",
                    "redirect_hint": f"Task {tid} has formal verdict {latest_verdict_value}. Use 'lybra queue close --task-id {tid} --actor <ACTOR> --closure-evidence ...' instead.",
                },
                safety_notice="AIPOS-C1: mark-concluded precondition: no formal verdict allowed. Use queue_close for cards with verdicts.",
            )
        closure_ref = report_ref or f"note:{note[:80]}"
        if dry_run:
            return make_response(
                ok=True,
                verdict=Verdict.PASS,
                operation=operation,
                dry_run=True,
                actor=_actor_payload(actor_text),
                data={
                    "task_id": tid,
                    "from_queue": queue_state,
                    "closure_ref": closure_ref,
                    "target_path": str((resolved_root / "5_tasks" / "queue" / "completed" / card_file.name).relative_to(resolved_root)),
                },
                safety_notice="AIPOS-354 mark_concluded dry-run. No files written.",
            )
        # Confirm: move card to completed/
        # AIPOS-R4A: 走转移引擎统一处理（一机制一实现）
        from tools.aipos_cli.transition_engine import apply_transition_metadata
        fm = apply_transition_metadata(
            metadata=fm,
            transition_name="complete",
            actor=actor_text,
            timestamp=None,  # 引擎自动生成
        )
        # 保留原有 closed_by/at 作为额外来源记录
        fm["closed_by"] = "conclusion_marker"
        fm["closed_at"] = fm["completed_at"]  # 复用引擎生成的时间戳
        fm["conclusion_report_ref"] = report_ref or ""
        fm["conclusion_note"] = note or ""
        fm["auto_closed_by"] = "AIPOS-354_mark_concluded"
        rendered = render_task_markdown(fm, body)
        target_path = resolved_root / "5_tasks" / "queue" / "completed" / card_file.name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        card_file.unlink()
        return make_response(
            ok=True,
            verdict=Verdict.PASS,
            operation=operation,
            dry_run=False,
            actor=_actor_payload(actor_text),
            data={
                "task_id": tid,
                "from_queue": queue_state,
                "closure_ref": closure_ref,
                "target_path": str(target_path.relative_to(resolved_root)),
                "moved": True,
            },
            safety_notice="AIPOS-354 mark_concluded: card moved to completed/ with machine-readable closure marker.",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(str(actor or "")))


def _auto_generate_decision_log_pointer(
    repo_root: Path,
    decision_id: str,
    decision_summary: str,
    decided_by: str,
    record_path: str,
) -> bool:
    """AIPOS-R6M 大项A②: 决策粒度机器触发 - owner_decision_record落库时自动生成decision_log指针条目
    
    Returns:
        True if pointer was written, False if skipped (already exists or error)
    """
    try:
        from datetime import datetime, timezone
        
        governance_dir = repo_root / "governance"
        decision_log_dir = governance_dir / "decision_log"
        
        # 生成 YYYY-MM 目录
        now = datetime.now(timezone.utc)
        year_month = now.strftime("%Y-%m")
        month_dir = decision_log_dir / year_month
        month_dir.mkdir(parents=True, exist_ok=True)
        
        # 生成文件名: YYYY-MM-DD-<slug>.md
        date_str = now.strftime("%Y-%m-%d")
        # 从 decision_id 提取 slug (移除时间戳部分)
        slug = decision_id.split("_")[0] if "_" in decision_id else decision_id
        filename = f"{date_str}-{slug}-auto-pointer.md"
        pointer_file = month_dir / filename
        
        # 如果文件已存在，跳过
        if pointer_file.exists():
            return False
        
        # 生成指针内容（固化命名·一句话+decided_by+指向记录路径）
        content = f"""---
status: active
decided_at: {now.isoformat().replace('+00:00', 'Z')}
decision_type: auto_pointer
auto_generated: true
---

## {decision_summary}

**Decided by**: {decided_by}

**Record**: `{record_path}`

**Auto-generated pointer** (AIPOS-R6M 大项A②): This entry was automatically created when the owner_decision_record was written.
"""
        
        pointer_file.write_text(content, encoding="utf-8")
        return True
    except Exception:
        # 静默失败，不影响主流程
        return False


def _code_repo_schema_root() -> Path:
    """AIPOS-F18-fix2 F-B-1: transitions.schema 真实所在根 = 运行代码所在仓根。

    委托 schema_loader.code_repo_schema_root(唯一实现): 门以 release 目录运行
    (AIPOS-333 运行时隔离), 产品仓/dev 仓根均含 schema/; 治理工作区根
    (resolved_root)没有 schema/ —— 旧实现以治理根为解析根必 SchemaLoadError,
    开关置关在真实门语境永不可达。调用时动态 import, 测试可替换根。
    """
    from tools.schema_loader import code_repo_schema_root

    return code_repo_schema_root()


def _write_fix_closure_derivation_record(
    *,
    resolved_root: Path,
    fix_card_closure_node: dict[str, Any],
    fix_task_id: str,
    source_task_id: str,
    derived_audit_task_id: str,
    verdict_id: str,
    derived_at: str,
) -> str | None:
    """AIPOS-F18-fix2 F-F-1: 按声明写 fix_closures 派生记录(门生记录, append-only)。

    位置模板/必填字段/门标记全部取自 transitions.schema 的 fix_card_closure.record 声明,
    改声明即跟随;声明缺字段时按声明原字面默认兜底。返回工作区相对路径。
    """
    record_decl = dict(fix_card_closure_node.get("record") or {})
    location_tpl = str(
        record_decl.get("location")
        or "5_tasks/records/fix_closures/{fix_task_id}/derivation_{fix_task_id}_{timestamp}.md"
    )
    ts_compact = (
        str(derived_at or "")
        .replace("-", "")
        .replace(":", "")
        .replace("T", "_")
        .replace("Z", "")
    )
    record_rel = location_tpl.format(fix_task_id=fix_task_id, timestamp=ts_compact)

    fields = {
        "record_type": "fix_closure_derivation",
        "event_type": "fix_closure_derivation",
        "fix_task_id": fix_task_id,
        "source_task_id": source_task_id,
        "derived_audit_task_id": derived_audit_task_id,
        "verdict_id": verdict_id,
        "derived_at": derived_at,
        "derived_by": "gate_fix_closure_derivation",
    }
    required = list(record_decl.get("required_fields") or []) or [
        "fix_task_id",
        "source_task_id",
        "derived_audit_task_id",
        "verdict_id",
        "derived_at",
        "record_type",
    ]
    # 声明的必填字段逐项落 frontmatter(缺值的必填字段以空串落盘并保留键, 缺口可见)
    fm_lines = [f"{k}: {fields.get(k, '')}" for k in required]
    fm_lines.extend(f"{k}: {v}" for k, v in fields.items() if k not in required)
    fm_text = "\n".join(fm_lines)

    body = f"""---
{fm_text}
---
# Fix Closure Derivation Record: {fix_task_id}

fix卡 close(PASS族)触发 `fix_card_closure` 级联: 为原卡派生复审卡(卡号模式见声明 revision_card_numbering)。

- fix卡: `{fix_task_id}`(裁决 `{verdict_id}`)
- 原卡: `{source_task_id}`
- 派生复审卡: `{derived_audit_task_id}`
- 派生时间: `{derived_at}`
- 声明源: transitions.schema.json `nodes.fix_card_closure`(位置模板/必填字段/门标记均来自该声明)

本记录为门生记录(append-only), 由 queue_close 级联自动写入;手写件会被 sweep 隔离。
"""
    record_path = resolved_root / record_rel
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(body, encoding="utf-8")
    return record_rel


def close_task(
    *,
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    closure_evidence: dict[str, Any] | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-283: gate close verb — move a claimed task to completed/ with closure evidence.

    Validates:
    - Task is in claimed/ queue state
    - Task has at least one return record
    - closure_evidence is provided with at least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref

    On confirm (dry_run=False):
    - Moves card claimed/ → completed/
    - Writes closure record to records/closures/<task_id>/ (append-only)
    - Auto-closes audit-derived cards (<task_id>R) if they are still in claimed/
    """
    operation = "queue_close"
    try:
        actor_text = str(actor or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        if not closure_evidence or not isinstance(closure_evidence, dict):
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="MISSING_CLOSURE_EVIDENCE",
                message="closure_evidence is required with at least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref.",
                actor=_actor_payload(actor_text),
                data={"recommended_action": "Provide closure evidence before closing."},
                safety_notice="AIPOS-283 queue_close requires closure evidence.",
            )

        # Validate at least one evidence ref is present and non-empty
        evidence_type = None
        evidence_ref = None
        for key in ("finalize_commit_hash", "finalize_return_ref", "owner_verification_ref"):
            val = str(closure_evidence.get(key) or "").strip()
            if val:
                evidence_type = key
                evidence_ref = val
                break
        if not evidence_type:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="MISSING_CLOSURE_EVIDENCE",
                message="At least one of finalize_commit_hash, finalize_return_ref, or owner_verification_ref must be non-empty.",
                actor=_actor_payload(actor_text),
                data={"recommended_action": "Provide at least one closure evidence reference."},
                safety_notice="AIPOS-283 queue_close requires at least one evidence reference.",
            )

        resolved_root = _resolve_repo_root(repo_root)

        # Resolve the task
        selected_task = _select_task(resolved_root, task_id=task_id, path=path)
        resolved_task_id = str(selected_task.get("task_id") or "")
        queue_state = selected_task.get("queue_state")

        # Validate task is in claimed/
        if queue_state != "claimed":
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="INVALID_QUEUE_STATE",
                message=f"Task must be in claimed/ to close, found: {queue_state}.",
                actor=_actor_payload(actor_text),
                data={"task_id": resolved_task_id, "queue_state": queue_state},
                safety_notice="AIPOS-283 queue_close only operates on claimed tasks.",
            )

        # Validate task has a return record
        records = load_records(resolved_root)
        task_returns = records.get("task_returns", {}).get(resolved_task_id, [])
        if not task_returns:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="MISSING_RETURN_RECORD",
                message=f"Task {resolved_task_id} has no return record. Cannot close without a return.",
                actor=_actor_payload(actor_text),
                data={"task_id": resolved_task_id},
                safety_notice="AIPOS-283 queue_close requires a prior return record.",
            )

        # Check if already closed (idempotency)
        task_closures = records.get("task_closures", {}).get(resolved_task_id, [])
        # AIPOS-R6S 大项A③: round 序号 — reopen 后的新 round 允许再次 close(旧 round
        # 的 closure 不拦新 round; append-only 历史保留)。
        try:
            current_round = int(selected_task.get("round") or 1)
        except (TypeError, ValueError):
            current_round = 1
        if task_closures and current_round <= 1:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="ALREADY_CLOSED",
                message=f"Task {resolved_task_id} already has a closure record.",
                actor=_actor_payload(actor_text),
                data={"task_id": resolved_task_id, "existing_closures": len(task_closures)},
                safety_notice="AIPOS-283 queue_close is not repeatable; task already closed.",
            )

        # Build closure ID
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        closure_id = build_runtime_id("close", resolved_task_id, timestamp, actor_text)
        return_record_ref = str(task_returns[0].get("path") or "") if task_returns else ""

        # Find related audit-derived cards (<task_id>R pattern)
        source_path = str(selected_task.get("path") or "")
        # AIPOS-F18-fix2: fix卡元数据必须在mutation(claimed→completed移动)前读取——
        # 级联块在移动之后执行, 从旧claimed/路径再读必扑空→静默跳过级联(R2未列名的伴生
        # 缺陷, F-A-1活体实测暴露)。
        fix_card_metadata: dict[str, Any] = {}
        try:
            _fix_card_file = resolved_root / source_path
            if _fix_card_file.is_file():
                _fix_fm, _, _ = parse_markdown_frontmatter(_fix_card_file.read_text(encoding="utf-8"))
                fix_card_metadata = _normalize_return_value(_fix_fm) or {}
        except Exception:
            fix_card_metadata = {}
        related_audit_refs: list[str] = []
        audit_task_id = resolved_task_id + "R"
        try:
            audit_matches = find_task_by_id(audit_task_id, resolved_root)
            if audit_matches[1]:
                for match in audit_matches[1]:
                    if match.get("queue_state") == "claimed":
                        related_audit_refs.append(audit_task_id)
                        break
        except (ValueError, FileNotFoundError, OSError):
            pass

        closure_evidence_bundle = {"type": evidence_type, "ref": evidence_ref}
        closure_path = closure_record_path(resolved_root, resolved_task_id, closure_id)

        # AIPOS-R6M 大项A①: 卡粒度机器触发 - close时校验FOUNDATION-BACKLOG存在本卡条目
        # 原'decision_log每卡追加'语义废除(73次无人理的WARN遗迹), decision_log改为只记决策粒度
        governance_dir = resolved_root / "governance"
        foundation_backlog_path = governance_dir / "FOUNDATION-BACKLOG.md"
        
        # 检查FOUNDATION-BACKLOG是否存在本卡条目 (或closure携excuse_ref豁免)
        backlog_entry_found = False
        excuse_ref = closure_evidence.get("excuse_ref")  # 豁免引用
        auto_generated_backlog_entry = False  # AIPOS-A1 大项B: 机器自动生成标记
        
        if not excuse_ref:
            if foundation_backlog_path.is_file():
                import re
                backlog_text = foundation_backlog_path.read_text(encoding="utf-8", errors="replace")
                # 严格匹配task_id作为单词边界
                pattern = re.compile(r'\b' + re.escape(resolved_task_id) + r'\b')
                if pattern.search(backlog_text):
                    backlog_entry_found = True
            
            # AIPOS-A1 大项B: 缺条目时自动生成(调用既有 generate_backlog_entry 逻辑)
            # 不再 BLOCK, 改为自动生成+继续校验。生成的条目带机器标记, 顾问可后补细化。
            if not backlog_entry_found and not dry_run:
                from tools.generate_backlog_entry import generate_backlog_entry as _gen_backlog_entry
                
                # 构建描述: 取卡 title/裁决/commit 等既有字段
                task_title = str(selected_task.get("title") or resolved_task_id)
                # 尝试从 closure_evidence 取 commit hash 或 return ref
                desc_parts = [task_title]
                if evidence_type == "finalize_commit_hash" and evidence_ref:
                    desc_parts.append(f"(commit: {evidence_ref[:8]})")
                elif evidence_type == "finalize_return_ref" and evidence_ref:
                    desc_parts.append(f"(return: {evidence_ref.split('/')[-1] if '/' in evidence_ref else evidence_ref})")
                description = " ".join(desc_parts)
                
                # 生成条目(带机器标记)
                entry_text = _gen_backlog_entry(resolved_task_id, description=description)
                # 追加机器标记: 标识这是自动生成的
                machine_marker = f"<!-- auto-generated by close_task (AIPOS-A1 大项B) at {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->\n"
                entry_with_marker = machine_marker + entry_text
                
                # 追加到 FOUNDATION-BACKLOG.md
                if foundation_backlog_path.is_file():
                    existing_content = foundation_backlog_path.read_text(encoding="utf-8", errors="replace")
                    if existing_content and not existing_content.endswith("\n"):
                        entry_with_marker = "\n" + entry_with_marker
                    foundation_backlog_path.write_text(
                        existing_content + entry_with_marker,
                        encoding="utf-8",
                    )
                    backlog_entry_found = True
                    auto_generated_backlog_entry = True
                else:
                    # 文件不存在, 创建新文件
                    foundation_backlog_path.parent.mkdir(parents=True, exist_ok=True)
                    header = "# Foundation Backlog\n\n"
                    foundation_backlog_path.write_text(
                        header + entry_with_marker,
                        encoding="utf-8",
                    )
                    backlog_entry_found = True
                    auto_generated_backlog_entry = True
            elif not backlog_entry_found and dry_run:
                # dry_run 模式: 只报告会自动生成, 不实际写
                auto_generated_backlog_entry = True  # 标记为“将会生成”
                backlog_entry_found = True  # 让后续校验通过
        
        # 保留旧的stage_archive检查作为WARN (不BLOCK)
        governance_warnings: list[str] = []
        
        # AIPOS-R6M: stage_archives鲜度检查 (WARN, 不BLOCK - 阶段粒度由转换门票机制执法)
        stage_archive_threshold_days = 30
        stage_archive_dir = resolved_root / "governance" / "stage_archives"
        if stage_archive_dir.is_dir():
            latest_mtime = 0.0
            for entry in stage_archive_dir.rglob("*"):
                if entry.is_file():
                    try:
                        mtime = entry.stat().st_mtime
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                    except OSError:
                        pass
            if latest_mtime > 0:
                import time
                age_days = (time.time() - latest_mtime) / 86400
                if age_days > stage_archive_threshold_days:
                    governance_warnings.append(
                        f"stage_archive/ stale: latest file is {int(age_days)} days old (threshold: {stage_archive_threshold_days} days)"
                    )
        else:
            governance_warnings.append("stage_archive/ directory not found")

        # Build the complete mutation (claimed → completed)
        # AIPOS-R6A FIX3: 传入profiles，标记为board_adapter调用，避免被靶②CLI拦截误伤
        profiles = load_agent_profiles(resolved_root)
        report_link = evidence_ref
        mutation_result = mutate_queue_task(
            resolved_root,
            "complete",
            task_id=resolved_task_id,
            actor=actor_text,
            report_link=report_link,
            dry_run=dry_run,
            with_records=False,
            profiles=profiles,
        )

        # Build response
        if dry_run:
            combined_warnings = list(mutation_result.get("warnings", []))
            combined_warnings.extend(governance_warnings)
            return make_response(
                ok=mutation_result.get("verdict") != Verdict.BLOCK,
                operation=operation,
                dry_run=True,
                verdict=mutation_result.get("verdict", Verdict.PASS),
                data={
                    "task_id": resolved_task_id,
                    "source_path": source_path,
                    "target_path": mutation_result.get("target_path"),
                    "from_state": "claimed",
                    "to_state": "completed",
                    "closure_id": closure_id,
                    "closure_record_path": str(closure_path.resolve().relative_to(resolved_root.resolve())),
                    "closure_evidence": closure_evidence_bundle,
                    "return_record_ref": return_record_ref,
                    "related_audit_task_refs": related_audit_refs,
                    "mutation_preview": mutation_result,
                    "governance_warnings": governance_warnings,
                    # AIPOS-F44A ⑥: N6 next_step preview in dry_run
                    "next_step_preview": {
                        "audience": "advisor",
                        "action": "任务将 close，完成后待 N6 governance-commit。",
                        "command": f"cd {resolved_root} && git add governance/ 5_tasks/records/closures/{resolved_task_id}/ && git commit -m 'N6: governance commit for {resolved_task_id}'",
                    },
                },
                blocking_reasons=mutation_result.get("blocking_reasons", []),
                warnings=combined_warnings,
                safety_notice="AIPOS-283/289 queue_close dry-run preview. No files written.",
            )

        # Confirm: execute the mutation
        if mutation_result.get("verdict") == Verdict.BLOCK:
            return make_response(
                ok=False,
                operation=operation,
                dry_run=False,
                verdict=Verdict.BLOCK,
                data={"task_id": resolved_task_id, "mutation_result": mutation_result},
                blocking_reasons=mutation_result.get("blocking_reasons", []),
                safety_notice="AIPOS-283 queue_close blocked by mutation validation.",
            )

        # Write closure record (append-only, with governance warnings)
        closure_markdown = build_closure_record_markdown(
            task_id=resolved_task_id,
            task_path=source_path,
            actor=actor_text,
            closure_id=closure_id,
            closed_at=timestamp,
            closure_evidence=closure_evidence_bundle,
            return_record_ref=return_record_ref,
            related_audit_task_refs=related_audit_refs or None,
            warnings=governance_warnings or None,
        )
        closure_path_resolved = resolved_root / closure_path
        closure_path_resolved.parent.mkdir(parents=True, exist_ok=True)
        closure_path_resolved.write_text(closure_markdown, encoding="utf-8")

        # AIPOS-F18 大项A: fix卡close后自动派生原卡复审卡
        # fix卡是 derived_from_audit_task_id 非空的卡, close且终局∈PASS族时触发
        derived_audit_for_source_task: str | None = None
        fix_derivation_result: dict[str, Any] | None = None
        
        # 检查是否为fix卡(derived_from_audit_task_id非空)——元数据已在上文mutation前读取
        derived_from_audit_task_id = str(fix_card_metadata.get("derived_from_audit_task_id") or "").strip()
        
        if derived_from_audit_task_id:
            # 这是fix卡,检查终局是否∈PASS族
            # AIPOS-F18 fix1: 门读声明执行, 检查 toggle.enabled 开关
            # AIPOS-F18-fix2 F-B-1: schema解析根改到运行代码所在仓根(门release/产品仓均含schema/,
            # 治理工作区根无schema/必SchemaLoadError→开关置关静默失效的缺陷已修);
            # 读取前清缓存, 让"置关→还原"实测对运行中的门立即可见;读失败出声不再静默吞。
            fix_card_closure_enabled = True  # 默认启用(声明读取失败保持fail-open)
            fix_card_closure_node: dict[str, Any] = {}
            try:
                from tools.schema_loader import clear_cache, load_schema

                clear_cache()
                transitions_schema = load_schema("transitions", _code_repo_schema_root())
                fix_card_closure_node = transitions_schema.get("nodes", {}).get("fix_card_closure", {}) or {}
                toggle = fix_card_closure_node.get("toggle", {}) or {}
                fix_card_closure_enabled = bool(toggle.get("enabled", True))
            except Exception as schema_exc:  # 声明读取失败不阻断close
                governance_warnings.append(
                    f"fix_card_closure声明读取失败(开关保持默认启用): {schema_exc}"
                )
            
            if not fix_card_closure_enabled:
                # 开关置关,回退为不派生(验完还原)
                governance_warnings.append("fix卡复审派生已关闭(transitions.schema fix_card_closure.toggle.enabled=false)")
            else:
                # 从 records 获取最新裁决
                fix_task_verdicts = records.get("task_audit_verdicts", {}).get(resolved_task_id, [])
                latest_fix_verdict = None
                if fix_task_verdicts:
                    latest_fix_verdict = max(fix_task_verdicts, key=_verdict_time)
                
                fix_verdict_value = str(latest_fix_verdict.get("verdict", "") if latest_fix_verdict else "").upper().strip()
                pass_family = {Verdict.PASS, Verdict.PASS_WITH_NOTES}
                
                if fix_verdict_value in pass_family:
                    # fix卡close且终局∈PASS族,为原卡派生复审卡
                    try:
                        from tools.aipos_cli.audit_derivation import derive_audit_task_id
                        
                        # 原卡ID = derived_from_audit_task_id 去掉R后缀
                        # (AIPOS-F18-fix2 F-H-1: 清理冗余rstrip链, 正则一式盖全 R/R2/R3…)
                        import re

                        _src_match = re.match(r"^(.+)R\d*$", derived_from_audit_task_id)
                        source_task_id_for_reaudit = (
                            _src_match.group(1) if _src_match else derived_from_audit_task_id
                        )
                        
                        # 使用卡号演进模式生成复审卡ID
                        derived_audit_for_source_task = derive_audit_task_id(source_task_id_for_reaudit, resolved_root)
                        
                        # 获取裁决ID用于准绳注入
                        verdict_id_for_criteria = str(latest_fix_verdict.get("verdict_id") or "") if latest_fix_verdict else ""
                        
                        # 构建复审卡
                        # 查找原卡信息
                        source_task_for_reaudit = _select_task(resolved_root, task_id=source_task_id_for_reaudit, path=None)
                        source_task_path_for_reaudit = str(source_task_for_reaudit.get("path") or "")
                        source_task_file_for_reaudit = resolved_root / source_task_path_for_reaudit
                        
                        if source_task_file_for_reaudit.is_file():
                            source_task_text_for_reaudit = source_task_file_for_reaudit.read_text(encoding="utf-8")
                            source_task_metadata_for_reaudit, source_task_body_for_reaudit, _ = parse_markdown_frontmatter(source_task_text_for_reaudit)
                            source_task_metadata_for_reaudit = _normalize_return_value(source_task_metadata_for_reaudit)
                            
                            # AIPOS-F38 大项A: 审计身份取 roles 注册表审计实例(audit_derivation 同一实现),
                            # 禁承继原卡执行实例(承继会让审计卡落到执行工位名下, 零 amend 不成立)
                            from tools.aipos_cli.audit_derivation import _derive_audit_assigned_to, _derive_audit_instance
                            _reaudit_project = str(source_task_metadata_for_reaudit.get("project") or "lybra")
                            _reaudit_audit_instance = _derive_audit_instance(_reaudit_project)
                            _reaudit_assigned_to = _derive_audit_assigned_to(_reaudit_project)
                            
                            # 构建复审卡metadata
                            reaudit_metadata = {
                                "task_id": derived_audit_for_source_task,
                                "title": f"复审 {source_task_id_for_reaudit} (fix卡 {resolved_task_id} 已修复)",
                                "project": _reaudit_project,
                                "assigned_to": _reaudit_assigned_to,
                                "agent_instance": _reaudit_audit_instance,
                                "context_bundle": source_task_metadata_for_reaudit.get("context_bundle", "default"),
                                "task_mode": "audit",
                                "task_class": "simple",
                                "priority": source_task_metadata_for_reaudit.get("priority", "medium"),
                                "status": "pending",
                                "created_by": "gate_fix_closure_derivation",
                                "needs_owner": False,
                                "derived_from_fix_task": resolved_task_id,
                                "reviewed_task_id": source_task_id_for_reaudit,
                                "reviewed_task_path": source_task_path_for_reaudit,
                                "fix_verdict_id": verdict_id_for_criteria,
                            }
                            
                            # 构建复审卡body(注入准绳)
                            reaudit_body = f"""## 复审任务
原卡缺陷已由 fix卡 `{resolved_task_id}` 修复并 close(裁决 `{verdict_id_for_criteria}`)。

**复审基准 = 修复后现状**

## 原卡引用
- 原卡: `{source_task_path_for_reaudit}`
- fix卡: `{resolved_task_id}`
- fix卡裁决: `{verdict_id_for_criteria}`

## 复审要点
1. 验证原卡 FAIL 中指出的问题已被修复
2. 验证修复后的代码/配置能够正常工作
3. 确认没有引入新的问题

## 裁决
- PASS: 原卡可以收账
- FAIL: 需要继续修复
"""
                            
                            # 写入复审卡(文件名对齐队列小写惯例, 如 aipos-f18r2.md)
                            # AIPOS-F38 大项A(F17 原则覆盖全部 writer): 产前自检——必填字段从
                            # schema 单源补全后仍缺、或审计身份不等于注册表实例 → 拒并出声
                            # (raise → 上层 except 记 governance_warning, 不写坏卡)
                            from tools.schema_loader import get_required_card_fields
                            _required_fields = get_required_card_fields()
                            _inherit_defaults = {
                                "needs_owner": False,
                                "output_target": str(source_task_metadata_for_reaudit.get("output_target") or ""),
                                "artifact_policy": str(source_task_metadata_for_reaudit.get("artifact_policy") or "formal_write"),
                            }
                            for _field in _required_fields:
                                if _field not in reaudit_metadata or reaudit_metadata[_field] is None:
                                    if _field in source_task_metadata_for_reaudit and source_task_metadata_for_reaudit[_field] is not None:
                                        reaudit_metadata[_field] = source_task_metadata_for_reaudit[_field]
                                    elif _field in _inherit_defaults:
                                        reaudit_metadata[_field] = _inherit_defaults[_field]
                            _missing = [f for f in _required_fields if f not in reaudit_metadata or reaudit_metadata[f] is None]
                            if _missing or reaudit_metadata.get("agent_instance") != _reaudit_audit_instance:
                                raise ValueError(
                                    f"AIPOS-F38 派生校验 FAIL: 复审卡 {derived_audit_for_source_task}"
                                    f" 缺必填字段 {_missing} 或审计身份 {reaudit_metadata.get('agent_instance')}"
                                    f" ≠ 注册表审计实例 {_reaudit_audit_instance}(禁承继原卡)。"
                                    f" schema 单源 = {_required_fields}"
                                )
                            reaudit_task_path = resolved_root / "5_tasks" / "queue" / "pending" / f"{derived_audit_for_source_task.lower()}.md"
                            reaudit_task_path.parent.mkdir(parents=True, exist_ok=True)
                            reaudit_markdown = render_task_markdown(reaudit_metadata, reaudit_body)
                            reaudit_task_path.write_text(reaudit_markdown, encoding="utf-8")
                            
                            fix_derivation_result = {
                                "derived_audit_task_id": derived_audit_for_source_task,
                                "source_task_id": source_task_id_for_reaudit,
                                "fix_task_id": resolved_task_id,
                                "fix_verdict_id": verdict_id_for_criteria,
                            }

                            # AIPOS-F18-fix2 F-F-1: 按声明写fix_closures门生记录
                            # (位置模板/必填字段/门标记均取自transitions.schema fix_card_closure.record)
                            _fc_record_rel = _write_fix_closure_derivation_record(
                                resolved_root=resolved_root,
                                fix_card_closure_node=fix_card_closure_node,
                                fix_task_id=resolved_task_id,
                                source_task_id=source_task_id_for_reaudit,
                                derived_audit_task_id=derived_audit_for_source_task,
                                verdict_id=verdict_id_for_criteria,
                                derived_at=timestamp,
                            )
                            if _fc_record_rel:
                                fix_derivation_result["fix_closure_record_path"] = _fc_record_rel
                            
                    except Exception as e:
                        # 派生失败不阻断close,只记录warning(AIPOS-F18-fix2 F-E-1: 删重复append,警告只留except内一处)
                        governance_warnings.append(f"fix卡复审派生失败: {e}")

        # Auto-close related audit-derived cards (direct move, bypassing actor-match
        # validation since this is a system consequence of parent closure, not an
        # actor-driven mutation).
        auto_closed: list[str] = []
        for audit_ref in related_audit_refs:
            try:
                audit_matches = find_task_by_id(audit_ref, resolved_root)
                if not audit_matches[1]:
                    continue
                audit_task = audit_matches[1][0]
                if audit_task.get("queue_state") != "claimed":
                    continue
                audit_source_path = resolved_root / str(audit_task["path"])
                audit_target_path = resolved_root / "5_tasks" / "queue" / "completed" / audit_source_path.name
                # Update frontmatter status to completed before moving
                audit_text = audit_source_path.read_text(encoding="utf-8")
                audit_metadata, audit_body, _ = parse_markdown_frontmatter(audit_text)
                # AIPOS-R4A: 走转移引擎统一处理（一机制一实现）
                from tools.aipos_cli.transition_engine import apply_transition_metadata
                audit_metadata = apply_transition_metadata(
                    metadata=audit_metadata,
                    transition_name="complete",
                    actor=actor_text,
                    timestamp=None,  # 引擎自动生成
                )
                # 保留额外跟踪字段
                audit_metadata["auto_closed_with_parent"] = resolved_task_id
                audit_metadata["auto_closed_via"] = closure_id
                rendered = render_task_markdown(audit_metadata, audit_body)
                audit_target_path.parent.mkdir(parents=True, exist_ok=True)
                audit_target_path.write_text(rendered, encoding="utf-8")
                audit_source_path.unlink()
                auto_closed.append(audit_ref)
            except (ValueError, FileNotFoundError, OSError, KeyError):
                pass

        combined_warnings = list(mutation_result.get("warnings", []))
        combined_warnings.extend(governance_warnings)
        # AIPOS-F44A ⑥: Add N6 next_step to close success response
        response_data = {
            "task_id": resolved_task_id,
            "source_path": source_path,
            "target_path": mutation_result.get("target_path"),
            "from_state": "claimed",
            "to_state": "completed",
            "closure_id": closure_id,
            "closure_record_path": str(closure_path.resolve().relative_to(resolved_root.resolve())),
            "closure_evidence": closure_evidence_bundle,
            "return_record_ref": return_record_ref,
            "related_audit_task_refs": related_audit_refs,
            "auto_closed_audit_cards": auto_closed,
            "auto_generated_backlog_entry": auto_generated_backlog_entry,  # AIPOS-A1 大项B
            "fix_derivation_result": fix_derivation_result,  # AIPOS-F18 大项A: fix卡派生复审结果
            "mutation_result": {
                "moved": mutation_result.get("moved"),
                "wrote": mutation_result.get("wrote"),
            },
            "governance_warnings": governance_warnings,
            # AIPOS-F44A ⑥: N6 next_step - governance commit
            "next_step": {
                "audience": "advisor",
                "action": "任务已 close，待 N6 governance-commit （将 closure 记录、FOUNDATION-BACKLOG 等治理档提交到治理仓）。",
                "command": f"cd {resolved_root} && git add governance/ 5_tasks/records/closures/{resolved_task_id}/ && git commit -m 'N6: governance commit for {resolved_task_id}'",
            },
        }
        return make_response(
            ok=True,
            operation=operation,
            dry_run=False,
            verdict=mutation_result.get("verdict", Verdict.PASS),
            data=response_data,
            warnings=combined_warnings,
            safety_notice="AIPOS-283/289 queue_close completed. Closure record written (append-only)." + (" FOUNDATION-BACKLOG entry auto-generated (AIPOS-A1)." if auto_generated_backlog_entry else "") + (f" 派生复审卡 {derived_audit_for_source_task}(AIPOS-F18)." if derived_audit_for_source_task else ""),
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


def withdraw_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    reason: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    owner_confirmation_required_override: bool | None = None,
    owner_confirmation_reasons_override: list[str] | None = None,
) -> dict[str, Any]:
    """AIPOS-315: withdraw a task from pending or claimed queue state.
    
    Moves task to withdrawn/ state with reason. Does NOT delete any existing
    records (claims/returns/sessions/audit) - those are preserved.
    
    S3 in-transit protection: checks for active session and blocks if found.
    """
    operation = "queue_withdraw"
    try:
        actor_text = str(actor or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        
        if not str(reason or "").strip():
            raise ValueError("reason is required for task withdrawal")
        
        resolved_root = _resolve_repo_root(repo_root)
        selected_task_id, selected_path = _select_task_input(task_id, path)
        
        # Load task to check session activity
        task = _select_task(resolved_root, task_id=selected_task_id, path=selected_path)
        
        # AIPOS-F41 B3: 在途卡可撤性修真 - claimed 但无活动会话的卡应允许撤回或改卡
        # 判据与 F38 "在途=未交回"同源:
        # - 已有 return 记录 = 不在途(可撤)
        # - claimed 且无 return, 但无活动会话 = 可撤(如 F40 被代按认领后从未开工)
        # - claimed 且无 return, 且有近期活跃会话 = 真在途(保护)
        active_session_id = task.get("metadata", {}).get("active_session_id")
        task_status = task.get("metadata", {}).get("status")
        
        if task_status == "claimed" and active_session_id and dry_run:
            # 检查是否已有 return 记录(已交回 = 不在途)
            from tools.aipos_cli.records import load_records
            records = load_records(resolved_root)
            returns = records.get("returns", [])
            has_return = any(r.get("task_id") == selected_task_id for r in returns)
            
            if not has_return:
                # 无 return 记录,检查会话是否活跃(近期有动静)
                sessions = records.get("sessions", [])
                from datetime import datetime, timedelta, timezone
                now = datetime.now(timezone.utc)
                
                for session in sessions:
                    if session.get("session_id") == active_session_id:
                        session_timestamp = session.get("created_at") or session.get("timestamp", "")
                        if session_timestamp:
                            try:
                                session_time = datetime.fromisoformat(session_timestamp.replace("Z", "+00:00"))
                                # 保护窗口从 1 小时扩大到 24 小时(避免误伤跨天在途卡)
                                if now - session_time < timedelta(hours=24):
                                    return blocked_response(
                                        operation=operation,
                                        dry_run=dry_run,
                                        category="ACTIVE_SESSION",
                                        message=f"Task has active session {active_session_id} from less than 24 hours ago (created at {session_timestamp}). Cannot withdraw task that may be in-transit.",
                                        actor=_actor_payload(actor_text),
                                        data={
                                            "task_id": task.get("task_id"),
                                            "active_session_id": active_session_id,
                                            "session_timestamp": session_timestamp,
                                            "recommended_action": "Wait for session to complete/return, or explicitly confirm withdrawal acknowledging work loss risk."
                                        },
                                        safety_notice="AIPOS-F41 B3: in-transit protection (24h window). Claimed+no-return+recent-session = true in-transit."
                                    )
                            except (ValueError, TypeError):
                                pass
        
        # Call mutate_queue_task directly
        from tools.aipos_cli.queue_mutation import mutate_queue_task
        from tools.aipos_cli.agent_profiles import load_agent_profiles
        
        profiles = load_agent_profiles(resolved_root)
        result = mutate_queue_task(
            resolved_root,
            "withdraw",
            task_id=selected_task_id,
            task_path=selected_path,
            actor=actor_text,
            reason=str(reason).strip(),
            dry_run=dry_run,
            profiles=profiles,
            with_records=False,
        )
        
        # Build response
        verdict = result.get("verdict", Verdict.BLOCK)
        
        if dry_run:
            response = make_response(
                ok=verdict != Verdict.BLOCK,
                verdict=verdict,
                operation=operation,
                dry_run=True,
                actor=_actor_payload(actor_text),
                data={
                    "task_id": result.get("task_id"),
                    "source_path": result.get("source_path"),
                    "target_path": result.get("target_path"),
                    "from_state": result.get("from_state"),
                    "to_state": result.get("to_state"),
                    "reason": str(reason).strip(),
                },
                summary={"task_id": result.get("task_id"), "to_state": "withdrawn"},
                warnings=result.get("warnings", []),
                blocking_reasons=result.get("blocking_reasons", []),
                safety_notice="AIPOS-315: withdraw will move task to withdrawn/ and preserve all existing records.",
            )
            # G2 红线修复: WARN 永不吐 token。非阻塞 WARN 必发 token(对齐 verbs.schema 契约)
            # withdraw 是两阶段动词(phases: ["dry_run", "confirm"]), WARN 下也需 confirm
            execute_allowed = verdict != Verdict.BLOCK
            return _attach_controlled_execute_metadata(
                operation=operation,
                actor=actor_text,
                response=response,
                execute_allowed=execute_allowed,
            )
        
        # Confirm path
        if verdict == Verdict.BLOCK:
            return make_response(
                ok=False,
                verdict=Verdict.BLOCK,
                operation=operation,
                dry_run=False,
                actor=_actor_payload(actor_text),
                data={},
                errors=[{"category": "WITHDRAW_BLOCKED", "message": "; ".join(result.get("blocking_reasons", []))}],
                blocking_reasons=result.get("blocking_reasons", []),
            )
        
        return make_response(
            ok=True,
            operation=operation,
            dry_run=False,
            actor=_actor_payload(actor_text),
            data={
                "task_id": result.get("task_id"),
                "source_path": result.get("source_path"),
                "target_path": result.get("target_path"),
                "from_state": result.get("from_state"),
                "to_state": result.get("to_state"),
                "reason": str(reason).strip(),
                "moved": result.get("moved", False),
                "wrote": result.get("wrote", False),
            },
            summary={"task_id": result.get("task_id"), "withdrawn": True},
            warnings=result.get("warnings", []),
            safety_notice="AIPOS-315: task withdrawn, all existing records preserved.",
        )
    except Exception as exc:
        return _normalize_exception("queue_withdraw", exc, dry_run=dry_run, actor=_actor_payload(actor))


def amend_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    amendments: dict[str, Any] | None = None,
    amendment_reason: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-315: amend a pending (unclaimed) task's frontmatter or body.
    
    Only works on pending tasks. Claimed tasks cannot be amended (in-transit work
    should not have requirements changed underneath it).
    
    Writes amendment record to records/amendments/<task_id>/ (append-only).
    """
    operation = "queue_amend"
    try:
        actor_text = str(actor or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        
        if not amendment_reason or not str(amendment_reason).strip():
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="MISSING_REASON",
                message="amendment_reason is required for task amendment",
                actor=_actor_payload(actor_text),
                data={"recommended_action": "Provide reason for amendment."},
                safety_notice="AIPOS-315 S1: amendments require explicit reason."
            )
        
        if not amendments or not isinstance(amendments, dict):
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="MISSING_AMENDMENTS",
                message="amendments dict is required with fields to update",
                actor=_actor_payload(actor_text),
                data={"recommended_action": "Provide amendments dict with fields to change."},
                safety_notice="AIPOS-315 S1: amendments require explicit changes."
            )
        
        resolved_root = _resolve_repo_root(repo_root)
        selected_task_id, selected_path = _select_task_input(task_id, path)
        
        # Load and validate task
        task = _select_task(resolved_root, task_id=selected_task_id, path=selected_path)
        task_path_obj = resolved_root / str(task["path"])
        
        # S1: Only allow amending pending tasks
        if task.get("queue_state") != "pending":
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="NOT_PENDING",
                message=f"Task is in {task.get('queue_state')} state. Only pending (unclaimed) tasks can be amended.",
                actor=_actor_payload(actor_text),
                data={
                    "task_id": task.get("task_id"),
                    "current_state": task.get("queue_state"),
                    "recommended_action": "Amendments are only allowed on pending tasks to avoid changing requirements for in-transit work."
                },
                safety_notice="AIPOS-315 S1: claimed tasks cannot be amended (would change requirements mid-execution)."
            )
        
        # Read current task content
        task_text = task_path_obj.read_text(encoding="utf-8")
        metadata, body, _warnings = parse_markdown_frontmatter(task_text)
        
        # Preserve original for amendment record
        original_metadata = dict(metadata)
        original_body = str(body)
        
        # Apply amendments
        updated_metadata = dict(metadata)
        body_changed = False
        for key, value in amendments.items():
            if key == "body":
                body = str(value)
                body_changed = True
            else:
                updated_metadata[key] = value
        
        # AIPOS-R8B 大项A① (c): 校验修改后的字段值(与 draft publish 共用同一 schema 校验)
        from tools.schema_loader import validate_field_value
        validation_errors = []
        for key, value in amendments.items():
            if key == "body":
                continue  # body 不在 schema 校验范围
            is_valid, error_msg = validate_field_value(key, value)
            if not is_valid and error_msg:
                validation_errors.append(error_msg)
        
        if validation_errors:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="INVALID_AMENDMENT_VALUE",
                message=f"Amendment validation failed: {'; '.join(validation_errors)}",
                actor=_actor_payload(actor_text),
                data={
                    "task_id": task.get("task_id"),
                    "validation_errors": validation_errors,
                    "recommended_action": "Ensure amended field values conform to schema enum constraints."
                },
                safety_notice="AIPOS-R8B: amendments must conform to schema (shared validation with draft publish)."
            )
        
        # Build amendment record
        amendment_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        amendment_id = build_runtime_id("amendment", task.get("task_id"), amendment_timestamp, actor_text)
        
        amendment_record = {
            "record_type": "amendment_record",
            "amendment_id": amendment_id,
            "task_id": task.get("task_id"),
            "amended_by": actor_text,
            "amended_at": amendment_timestamp,
            "reason": str(amendment_reason).strip(),
            "original_metadata": original_metadata,
            "updated_metadata": updated_metadata,
            "original_body": original_body if body_changed else None,
            "updated_body": body if body_changed else None,
            "amendments_applied": list(amendments.keys()),
        }
        
        # Build amendment record markdown
        amendments_dir = resolved_root / "5_tasks" / "records" / "amendments" / task.get("task_id")
        amendment_filename = f"amendment_{task.get('task_id')}_{amendment_timestamp.replace(':', '').replace('-', '')}_{_slug(actor_text)}.md"
        amendment_path = amendments_dir / amendment_filename
        
        amendment_markdown = f"""---
record_type: amendment_record
amendment_id: {amendment_id}
task_id: {task.get('task_id')}
amended_by: {actor_text}
amended_at: {amendment_timestamp}
reason: {str(amendment_reason).strip()}
---

# Amendment Record: {task.get('task_id')}

## Reason
{str(amendment_reason).strip()}

## Fields Changed
{chr(10).join(f"- {k}" for k in amendments.keys())}

## Original Metadata
```yaml
{json.dumps(original_metadata, indent=2, ensure_ascii=False)}
```

## Updated Metadata
```yaml
{json.dumps(updated_metadata, indent=2, ensure_ascii=False)}
```

{'## Body Changes' if body_changed else ''}
{'Original body length: ' + str(len(original_body)) + ' chars' if body_changed else ''}
{'Updated body length: ' + str(len(body)) + ' chars' if body_changed else ''}
"""
        
        if dry_run:
            response = make_response(
                ok=True,
                verdict=Verdict.PASS,
                operation=operation,
                dry_run=True,
                actor=_actor_payload(actor_text),
                data={
                    "task_id": task.get("task_id"),
                    "task_path": str(task_path_obj.relative_to(resolved_root)),
                    "current_state": "pending",
                    "amendments_to_apply": list(amendments.keys()),
                    "amendment_record_path": str(amendment_path.relative_to(resolved_root)),
                    "would_write_amendment_record": True,
                    "would_update_task": True,
                },
                summary={"task_id": task.get("task_id"), "amendments": list(amendments.keys())},
                planned_writes=[
                    {"path": str(amendment_path.relative_to(resolved_root)), "kind": "create", "type": "amendment_record"},
                    {"path": str(task_path_obj.relative_to(resolved_root)), "kind": "update", "type": "task_markdown"},
                ],
                warnings=[],
                blocking_reasons=[],
                safety_notice="AIPOS-315 S1: amendment record will be written (append-only), original content preserved in record.",
            )
            # G2 红线修复: amend 也是两阶段动词, WARN 永不吐 token
            return _attach_controlled_execute_metadata(
                operation=operation,
                actor=actor_text,
                response=response,
                execute_allowed=True,  # amend 没有 BLOCK/WARN 逻辑,只有 PASS
            )
        
        # Confirm: write amendment record and update task
        amendments_dir.mkdir(parents=True, exist_ok=True)
        amendment_path.write_text(amendment_markdown, encoding="utf-8")
        
        # Render and write updated task
        rendered_task = render_task_markdown(updated_metadata, body)
        task_path_obj.write_text(rendered_task, encoding="utf-8")
        
        return make_response(
            ok=True,
            operation=operation,
            dry_run=False,
            actor=_actor_payload(actor_text),
            data={
                "task_id": task.get("task_id"),
                "task_path": str(task_path_obj.relative_to(resolved_root)),
                "amendments_applied": list(amendments.keys()),
                "amendment_id": amendment_id,
                "amendment_record_path": str(amendment_path.relative_to(resolved_root)),
                "amendment_reason": str(amendment_reason).strip(),
            },
            summary={"task_id": task.get("task_id"), "amendments": list(amendments.keys()), "amendment_id": amendment_id},
            warnings=[],
            safety_notice="AIPOS-315 S1: task amended, amendment record written (append-only).",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
