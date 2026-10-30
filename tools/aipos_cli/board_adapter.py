from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from tools.aipos_cli.clock import file_slug, iso_ms, iso_z, utc_now
from tools.aipos_cli.record_writer import record_dir, record_root, records_root
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
from tools.aipos_cli.queue_mutation import mutate_queue_task, render_task_markdown
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
from tools.schema_loader import SchemaLoadError
from tools.aipos_cli.records import expected_closure_record_path, load_records
from tools.aipos_cli.task_loader import (
    find_repo_context,
    find_repo_root,
    find_task_by_id,
    find_task_card,
    load_all_tasks,
    load_task_by_path,
    queue_root_for,
)
from tools.aipos_cli.workspace_config import (
    find_workspace_config,
    governance_paths,
    has_workspace_queue,
    load_workspace_config,
    project_paths,
    read_project_json,
    CardRepoUnresolved,
    resolve_card_repo,
    resolve_active_project,
)
from tools.aipos_cli.validator import validate_single_task, validate_tasks
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
_GOVERNANCE_DOC_KEYS = ("decision_log",)
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


# AIPOS-F132 件①: 审计裁决记录属主 = 角色类 auditor(roles.schema roles[].role_class 内建类名, authorize_instance_class 读注册表
# 校验其存在; 是单一角色类身份, 不是实例前缀集合)。自定义审计角色经门注册表 role_class 解析到此类即放行。
_AUDIT_VERDICT_SUBMITTER_CLASS = "auditor"


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


def _read_fix_derivation_mode(repo_root: Path) -> str:
    """AIPOS-F75 件①: 读取 transitions.schema.json 中的 fix_derivation.mode 声明。
    
    默认 manual (不派生)。门读此声明执行，禁代码内写死默认行为。
    """
    import json
    schema_path = repo_root / "schema" / "transitions.schema.json"
    if not schema_path.exists():
        # schema 文件不存在，使用默认值 manual
        return "manual"
    try:
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        # 读取 nodes.N4.fix_derivation.mode
        nodes = data.get("nodes", {})
        n4 = nodes.get("N4", {})
        fix_derivation = n4.get("fix_derivation", {})
        mode = str(fix_derivation.get("mode", "manual")).strip().lower()
        # 只允许 auto 或 manual
        if mode not in {"auto", "manual"}:
            return "manual"
        return mode
    except (json.JSONDecodeError, OSError) as e:
        # 解析失败，使用默认值 manual (fail-closed)，但记录 warning
        import warnings
        warnings.warn(f"Failed to parse transitions.schema.json for fix_derivation mode: {e}, defaulting to manual")
        return "manual"


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
        "queue_rework",    # AIPOS-F154: 返工节两阶段(门 lybra_queue_rework_confirm 认此 operation)
        "audit_dispatch",
        "audit_verdict",
        "orchestration_event_append",
        "planner_iteration_append",
        "intake_submit",
        "owner_decision_record",
        "owner_verification_record",
        "bench_audit_submit",
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
                "queue_root_found": queue_root_for(resolved_root).exists(),
                "records_root_found": (resolved_root / records_root()).exists(),
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
        # AIPOS-F141: 看板「需 Owner 决定」与 CLI needs-owner 同一筛选(aipos_cli._filter_needs_owner, 原本处第二份判据已删)
        # 并经四视图唯一可见卡入口 machine_zone.visible_cards: 存量冻结卡不列, 张数进 frozen_hidden
        from tools.aipos_cli.aipos_cli import _filter_needs_owner

        owner_report = _filter_needs_owner(report, governance_root=Path(resolved_root))
        filtered = owner_report["tasks"]
        view = owner_report["view"]
        payload = {
            "scope": "needs_owner",
            "summary": {
                "total_tasks": len(filtered),
                "needs_owner": len(filtered),
            },
            "tasks": filtered,
            "frozen_hidden": view["frozen_hidden"],
            "frozen_error": view["frozen_error"],
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
    """Resolve the active project (AIPOS-F66: 调用统一解析器).
    
    AIPOS-F52: 显式 workspace_root 时，从该路径的 project.json#project 读取项目名，
    而不是回落到全局 active_project。保证项目无关性，禁项目名硬编码。
    
    AIPOS-F66: 收敛到统一解析器 ProjectResolver。
    """
    from tools.project_resolution import ProjectResolver
    return ProjectResolver.resolve_project(
        explicit_project=project,
        workspace_root=resolved_root
    )


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
            "records_dir": record_root("owner_decisions").as_posix(),
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


def get_owner_inbox(home_root: str | Path | None = None, *, project: str | None = None, include_paused: bool = False) -> dict[str, Any]:
    """AIPOS-F168 件①: 看板「Owner 待决」页与 /api/owner/inbox 的数据 —— 与 CLI `lybra owner inbox` 同一函数 owner_inbox.collect_inbox
    (跨 home 根全部已注册项目; home 根缺省 = resolve_home_root 同一优先级梯)。读不出处进 warnings(点名, 不吞)。只读。"""
    from tools.aipos_cli.owner_inbox import collect_inbox
    from tools.schema_loader import SchemaLoadError

    operation = "get_owner_inbox"
    try:
        inbox = collect_inbox(home_root, project=project, include_paused=include_paused)  # AIPOS-F181: board page switch
    except (OSError, ValueError, SchemaLoadError) as exc:
        return _normalize_exception(operation, exc, dry_run=False)
    warnings = [f"{err['project']}: {err['source']}: {err['error']}" for err in inbox["errors"]]
    return make_response(
        ok=True,
        verdict=Verdict.WARN if warnings else Verdict.PASS,
        operation=operation,
        dry_run=False,
        data=inbox,
        summary={"count": inbox["count"], "projects": len(inbox["projects"]), "decided_hidden": inbox["decided_hidden"],
                 "errors": len(inbox["errors"])},
        warnings=warnings,
        blocking_reasons=[],
        needs_owner_reasons=[],
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
        safety_notice=READ_SAFETY_NOTICE,
        errors=[],
    )


def get_roadmap_status(repo_root: str | Path | None = None, *, lane: str | None = None) -> dict[str, Any]:
    """AIPOS-F173: 路线图进度与偏差(看板「路线图与验收」页数据, F174 用)——与 `lybra roadmap status` / Owner 收件箱 roadmap_drift 类别
    同一推导 roadmap_drift.roadmap_status(只读)。无路线图 = ok + exists False; 路线图 / 阈值读不出 = BLOCK 应答带拒因(点名, 不吞)。"""
    from tools.aipos_cli.roadmap import RoadmapError
    from tools.aipos_cli.roadmap_drift import json_view, roadmap_status
    from tools.aipos_cli.workspace_config import RoadmapDriftThresholdError
    from tools.schema_loader import SchemaLoadError

    operation = "get_roadmap_status"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        view = roadmap_status(resolved_root, lane=lane)
    except (RoadmapError, RoadmapDriftThresholdError) as exc:
        return blocked_response(operation=operation, dry_run=False, category="VALIDATION_ERROR", message=str(exc),
                                safety_notice=READ_SAFETY_NOTICE)
    except (OSError, ValueError, SchemaLoadError) as exc:
        return _normalize_exception(operation, exc, dry_run=False)
    return make_response(
        ok=True,
        verdict=Verdict.PASS,
        operation=operation,
        dry_run=False,
        data=json_view(view),
        summary={"exists": view["exists"], "milestones": len(view["milestones"]), "signals": len(view["signals"]),
                 "met": view["totals"]["met"], "total": view["totals"]["total"]},
        warnings=[],
        blocking_reasons=[],
        needs_owner_reasons=[],
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
        safety_notice=READ_SAFETY_NOTICE,
        errors=view["errors"],
    )


def get_roadmap_view(repo_root: str | Path | None = None, *, lane: str | None = None) -> dict[str, Any]:
    """AIPOS-F171 件③: 看板项目页里程碑简版(/api/roadmap)的数据——与 `lybra roadmap show` 同一函数 roadmap.roadmap_view(只读)。
    无路线图文件 = ok + exists False(前端整区隐藏); 读不出 / 不合声明 = BLOCK 应答带拒因(点名, 不吞)。"""
    from tools.aipos_cli.roadmap import RoadmapError, roadmap_view
    from tools.schema_loader import SchemaLoadError

    operation = "get_roadmap_view"
    try:
        resolved_root = _resolve_repo_root(repo_root)
        view = roadmap_view(resolved_root, lane=lane)
    except RoadmapError as exc:
        return blocked_response(operation=operation, dry_run=False, category="VALIDATION_ERROR", message=str(exc),
                                safety_notice=READ_SAFETY_NOTICE)
    except (OSError, ValueError, SchemaLoadError) as exc:
        return _normalize_exception(operation, exc, dry_run=False)
    return make_response(
        ok=True,
        verdict=Verdict.PASS,
        operation=operation,
        dry_run=False,
        data={k: (str(v) if isinstance(v, Path) else v) for k, v in view.items()},
        summary={"exists": view["exists"], "milestones": len(view["milestones"]), "unlinked": len(view["unlinked"])},
        warnings=[],
        blocking_reasons=[],
        needs_owner_reasons=[],
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
        safety_notice=READ_SAFETY_NOTICE,
        errors=[],
    )


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


def _build_owner_decision(resolved_root: Path, payload: dict[str, Any], *, actor: str | None, dry_run: bool) -> dict[str, Any]:
    """owner_decision_record 构造分派(预演与门确认重放同调): payload 带 project_authorization 块(AIPOS-F224 项目常设授权签发 / 扩大 / 撤销)=
    autonomy_policy.build_authorization_decision(决策记录仍由既有 writer 渲染, 另加授权修订一份写); 其余 = 既有 writer 原样(行为不变)。"""
    if payload.get("project_authorization") not in (None, ""):
        from tools.aipos_cli.autonomy_policy import build_authorization_decision

        return build_authorization_decision(resolved_root, payload, actor=actor, dry_run=dry_run, backend=backend_build_owner_decision_record)
    return backend_build_owner_decision_record(resolved_root, payload, actor=actor, dry_run=dry_run)


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
        result = _build_owner_decision(resolved_root, dict(payload), actor=actor, dry_run=True)
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
            # AIPOS-F224: 项目授权修订(签发 / 扩大 / 撤销)随决策记录一并落盘
            **({"project_authorization_id": result.get("project_authorization_id"),
                "authorization_revision": result.get("authorization_revision"),
                "authorization_path": result.get("authorization_path"),
                "authorization_record": result.get("authorization_record"),
                "authorization_markdown": result.get("authorization_markdown")} if result.get("project_authorization_id") else {}),
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
        refusal = _needs_owner_reason_refusal(result.get("rendered_markdown"))  # AIPOS-F225 件③: needs_owner 卡须带第一档事由
        if refusal:
            result = {**result, "blocking_reasons": [*list(result.get("blocking_reasons", [])), refusal]}
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


def _needs_owner_reason_refusal(rendered: Any) -> str | None:
    """AIPOS-F225 件③: 待发布卡 needs_owner = true 而卡面第一档事由(tiers.needs_owner_reason_field)缺 / 不在清单 = 阻断理由原文(校验唯一实现
    owner_inbox.require_owner_reason); 否则 None。渲染稿读不出 = None(既有发布校验已点名)。"""
    if not isinstance(rendered, str) or not rendered.strip():
        return None
    from tools.aipos_cli.owner_inbox import OwnerReasonRefused, owner_reason_keys, require_owner_reason, tier_declaration

    meta, _body, _warnings = parse_markdown_frontmatter(rendered)
    if not isinstance(meta, dict) or meta.get("needs_owner") is not True:
        return None
    tiers = tier_declaration()
    field = str(tiers["needs_owner_reason_field"])
    try:
        require_owner_reason(meta.get(field))
    except OwnerReasonRefused:
        return str(tiers["texts"]["needs_owner_reason_missing"]).format(field=field, reasons=owner_reason_keys())
    return None


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
        # AIPOS-R6A F-002修复 + F78C: code_repo = 该卡声明的仓(resolve_card_repo), 禁硬编码
        product_code_repo = _resolve_product_code_repo(resolved_root, task_metadata)
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
    adoption: dict[str, Any] | None = None,
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
        adoption=adoption,
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


def _create_return_skeleton(repo_root: Path, task_id: str,
                            card_frontmatter: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """认领时在卡报告声明位创建空模板(AIPOS-F65A-fix2 单一实现点)。

    AIPOS-F89 件③c: 落点 = next_resolver.card_report_path(执行卡 = <paths.return_root>/<ID>/<默认候选>; 审计卡 =
    <paths.verdict_root>/<审计卡ID>/<默认候选>), 与真实报告同名同位; 审计卡用 record_writer.build_verdict_skeleton_markdown
    (verdict/commit_sha 占位), 执行卡用 build_return_skeleton_markdown。完成判据读声明(占位不算), 空模板不被当产物。

    Returns:
        dict with path, record_type, wrote if skeleton was created; None if already exists

    Raises:
        RuntimeError: If path resolution fails (fail-closed semantic)
    """
    from tools.aipos_cli.record_writer import build_return_skeleton_markdown, build_verdict_skeleton_markdown

    try:
        from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter, card_report_path, forensic_subject

        fm = card_frontmatter
        if fm is None:
            found, _state = _find_task_in_queue(Path(repo_root), task_id)
            fm = _read_frontmatter(found) if found else {}
        skeleton_path = card_report_path(Path(repo_root), task_id, fm)
        if skeleton_path.exists():
            return None  # Only create if doesn't exist (idempotent)
        reviewed = forensic_subject(fm)
        # AIPOS-F197 件② + AIPOS-F210 件③: 模板契约一律 = card_report_contract(交付主体三态: 卡分支 / 固定候选 / 治理根产出清单;
        # 原只对治理根卡取契约, 固定候选卡回落卡分支契约 = HBLX-24BV 骨架叫填 card/<验证卡> tip 的混淆源)
        from tools.aipos_cli.next_resolver import card_report_contract

        contract = card_report_contract(Path(repo_root), task_id, {**fm, "task_id": task_id})
        if reviewed is not None:
            content, record_type = build_verdict_skeleton_markdown(task_id, reviewed or None, contract), "verdict_skeleton"
        else:
            content, record_type = build_return_skeleton_markdown(task_id, contract), "return_skeleton"
        skeleton_path.parent.mkdir(parents=True, exist_ok=True)
        skeleton_path.write_text(content, encoding="utf-8")
        try:
            rel_path = str(skeleton_path.relative_to(repo_root))
        except ValueError:
            rel_path = str(skeleton_path)
        return {"path": rel_path, "record_type": record_type, "wrote": True}
    except Exception as exc:
        # AIPOS-F65A: fail-closed - 路径解析失败即阻断 claim 并出声
        # transitions.schema 已声明 fail_closed: "Path resolution failure blocks claim (skeleton creation is not optional)"
        raise RuntimeError(
            f"RETURN_SKELETON_PATH_RESOLUTION_FAILED: 无法创建报告空模板 - 路径解析失败: {exc}。"
            f"出口: 检查治理根 project.json paths(return_root/verdict_root)声明是否正确。"
        ) from exc


def _write_mcp_claim_records(repo_root: Path, record_plan: dict[str, Any], task_id: str | None = None) -> list[dict[str, Any]]:
    """Write claim records and optionally create RETURN.md skeleton (AIPOS-F65A 大项①).
    
    Args:
        repo_root: Repository root (governance workspace root)
        record_plan: Record plan with previews
        task_id: Task ID (for RETURN.md skeleton creation)
        
    Returns:
        List of performed writes
    """
    performed: list[dict[str, Any]] = []
    
    # Write claim/session records (AIPOS-F79D 件④: session 落点 = claim_record_paths → records/sessions/<ID>/ 声明位; 禁 0 字节)
    for preview in record_plan.get("record_previews", []):
        path = repo_root / str(preview.get("path") or "")
        rendered = str(preview.get("rendered_markdown") or "")
        if not rendered.strip():
            raise RuntimeError(f"拒写 0 字节记录: {preview.get('path')} ({preview.get('record_type')})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        performed.append({"path": str(preview.get("path")), "record_type": preview.get("record_type"), "wrote": True})
    
    # AIPOS-F65A-fix2: Create RETURN.md skeleton via shared function
    if task_id:
        skeleton_result = _create_return_skeleton(repo_root, task_id)
        if skeleton_result:
            performed.append(skeleton_result)
    
    return performed


def _return_outcome_fields(repo_root: Path, task_id: str, card_metadata: dict[str, Any], blocking: list[str]) -> dict[str, str] | None:
    """AIPOS-F252 件②: 门交回记录的交回结果字段(声明 transitions artifact_ingest.return.outcome): 报告显式声明 = {record_field: 值};
    未声明 / 报告不在 = None(存量同形); 值不在值域 = 追加拒因(门拒写, 与产物入口 INGEST_RETURN_OUTCOME_INVALID 同一判定)。
    只对执行卡(非审计卡)取; 报告路径 = next_resolver.card_report_path 唯一读取口。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.next_resolver import (_read_frontmatter, card_report_path, report_return_outcome, return_outcome_declaration,
                                               task_mode_is_audit)

    if task_mode_is_audit((card_metadata or {}).get("task_mode")):
        return None
    path = card_report_path(Path(repo_root), task_id, {**(card_metadata or {}), "task_id": task_id})
    if not path.is_file():
        return None
    try:
        value, declared, problem = report_return_outcome(_read_frontmatter(path, allow_missing_block=True))
    except FrontmatterReadError as exc:
        blocking.append(f"RETURN_OUTCOME_UNREADABLE: {exc}")
        return None
    if problem:
        blocking.append(f"{return_outcome_declaration()['reject_category']}: {path}: {problem}")
        return None
    return {str(return_outcome_declaration()["record_field"]): value} if declared else None


def _report_declares_blocked(repo_root: Path, task_id: str, card_metadata: dict[str, Any]) -> bool:
    """AIPOS-F252 件②: 本卡交回报告声明 outcome=blocked(同一判定 _return_outcome_fields; 读不出 / 值不合 = 否, 拒因由交回记录计划给出)。"""
    from tools.aipos_cli.next_resolver import return_outcome_declaration

    fields = _return_outcome_fields(repo_root, task_id, card_metadata, [])
    return (fields or {}).get(str(return_outcome_declaration()["record_field"])) == "blocked"


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
    self_check_waived: bool = False,
    self_check_waiver_reason: str | None = None,
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
    # AIPOS-F114 件①: 交回记录绑定卡分支此刻 tip(与裁决绑 tip 同一读取口), 推导核据此判交回是否已过期(nodes.N3.return_stale)
    from tools.aipos_cli.next_resolver import return_binding_subject
    # AIPOS-F252 件②: 交回报告声明的交回结果随记录落(同一判定 next_resolver.report_return_outcome; 值不合 = 拒写, fail-closed)
    return_outcome = _return_outcome_fields(repo_root, task_id, source_metadata, blocking)

    return_markdown = build_mcp_return_record_markdown(
        artifact_subject=return_binding_subject(repo_root, task_id, source_metadata),
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
        self_check_waived=self_check_waived,
        self_check_waiver_reason=self_check_waiver_reason,
        return_outcome=return_outcome,
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


def _return_ref_prefix(repo_root: Path, task_id: str) -> str:
    """交回材料引用前缀 = 治理根相对的 <paths.return_root>/<task_id>/(AIPOS-F89 件① H9: 唯一推导 next_resolver.return_artifact_dir)。
    声明落点不在治理根内 = ValueError(交回材料引用一律治理根相对, fail-closed)。"""
    from tools.aipos_cli.next_resolver import return_artifact_dir

    directory = return_artifact_dir(Path(repo_root), task_id)
    try:
        rel = directory.resolve().relative_to(Path(repo_root).resolve())
    except ValueError as exc:
        raise ValueError(f"交回落点 {directory} 不在治理根 {repo_root} 内") from exc
    return f"{rel.as_posix()}/"


def _return_body_rel(repo_root: Path, task_id: str) -> str:
    """门侧 return_body 落盘 / 读取的治理根相对路径 = next_resolver.default_return_file(AIPOS-F89 件① H9, 禁写死)。"""
    from tools.aipos_cli.next_resolver import default_return_file

    target = default_return_file(Path(repo_root), task_id)
    return target.resolve().relative_to(Path(repo_root).resolve()).as_posix()


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
    # AIPOS-F89 件① H9: 交回材料落点只读项目声明(project.json paths.return_root, 唯一推导 next_resolver.return_artifact_dir);
    # 原读 config.schema governance_structure.paths.task_cards(第二份声明, 已删)。
    try:
        expected_prefix = _return_ref_prefix(repo_root, task_id)
    except (SchemaLoadError, OSError, ValueError) as exc:
        # AIPOS-F65A: fail-closed - 路径解析失败即拒并出声
        blocking_reasons.append(
            f"RETURN_ARTIFACT_PATH_RESOLUTION_FAILED: 无法解析交回落点声明: {exc}。"
            "出口: 检查治理根 project.json paths.return_root(须在治理根内, 缺省见 config.schema "
            "configuration_sources.project_json.schema.paths.return_root.default)。"
        )
        return blocking_reasons
    
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
            # 检查落点: 必须在 <paths.return_root>/<task_id>/ 内
            if not ref_stripped.startswith(expected_prefix):
                blocking_reasons.append(
                    f"RETURN_ARTIFACT_WRONG_LOCATION: 报告材料 '{ref_stripped}' 不在正确落点。"
                    f"必须在 {expected_prefix} 内 (治理工作区 <project.json paths.return_root>/<task_id>/)。"
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


class ProductRepoNotConfigured(CardRepoUnresolved):
    """AIPOS-FND-5F1 → F78C: raised when the card's product repo cannot be resolved from declaration
    (project.json repos 清单 / code_repo 别名 / 卡 lane.repo). Callers must surface this as an actionable
    blocking reason, never silently guess a path."""


def _resolve_product_code_repo(governance_root: Path, card_frontmatter: dict[str, Any] | None = None) -> Path:
    """AIPOS-FND-5F1 → AIPOS-F78C 件②: 产品仓(代码真正落地处)只经 workspace_config.resolve_card_repo 一处解析——
    卡 lane.repo(仓名/绝对路径)→ project.json repos 清单 → code_repo 兼容别名 → 治理根自身(仅未注册靶场根/治理根自身是 git 仓)。

    Owner ruling (2026-08-09) 不变: ZERO machine-specific hardcoded fallback paths; missing/invalid declaration fails
    LOUD (ProductRepoNotConfigured, message carries the exit) — never a silent path guess. 2026-09-22 裁定: 一卡一仓,
    仓由卡声明; card_frontmatter 缺(项目级调用/存量夹具 monkeypatch 单参形)= 项目缺省仓(repos.default / code_repo)。
    """
    try:
        return resolve_card_repo(governance_root, card_frontmatter or {})
    except ProductRepoNotConfigured:
        raise
    except CardRepoUnresolved as exc:
        raise ProductRepoNotConfigured(exc.code, exc.reason) from exc


def _card_product_repo(repo_root: Path, card_frontmatter: dict[str, Any] | None) -> Path:
    """按卡取仓(交回判据/派审复审共用)。card_frontmatter 缺时保持单参调用形: 存量夹具以 `lambda x: repo` 替身
    _resolve_product_code_repo(F49/F51/F74/F78), 该形不可破。"""
    if card_frontmatter is None:
        return _resolve_product_code_repo(repo_root)
    return _resolve_product_code_repo(repo_root, card_frontmatter)


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


def _legacy_frozen_block(
    operation: str,
    repo_root: Path,
    selectors: list[tuple[str | None, str | Path | None]],
    *,
    dry_run: bool,
    actor: Any,
) -> dict[str, Any] | None:
    """AIPOS-F122 件③: 门写动作对存量冻结卡拒(LEGACY_FROZEN, 附解冻命令)。是否冻结 = legacy_baseline.frozen_rejection
    (唯一判定 frozen_tasks, 与 state lint / next / loop 共用); 清单读不出 = 拒(LEGACY_BASELINE_INVALID, fail-closed)。
    selectors = [(task_id, path)]: 只给 path 时按卡面取 task_id; 卡解析不到的选择子留给动词原有的找卡判据报错。None = 放行。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_rejection

    task_ids: list[str] = []
    for selector_id, selector_path in selectors:
        if selector_id and str(selector_id).strip():
            task_ids.append(str(selector_id).strip())
        elif selector_path:
            try:
                task_ids.append(str(_select_task(repo_root, task_id=None, path=selector_path).get("task_id") or ""))
            except (FileNotFoundError, ValueError, OSError):
                continue
    try:
        rejection = frozen_rejection(repo_root, task_ids, action=operation)
    except LegacyBaselineError as exc:
        response = blocked_response(operation=operation, dry_run=dry_run, category="VALIDATION_ERROR",
                                    message=f"{exc}; 存量冻结清单读不出, 拒写(出口: 修正清单条目 / project.json legacy_baseline)",
                                    actor=_actor_payload(str(actor or "").strip()), safety_notice=CONTROLLED_EXECUTE_NOTICE)
        response["error_code"] = exc.code
        return response
    if rejection is None:
        return None
    response = blocked_response(operation=operation, dry_run=dry_run, category="STATUS_MISMATCH", message=rejection["message"],
                                actor=_actor_payload(str(actor or "").strip()), safety_notice=CONTROLLED_EXECUTE_NOTICE)
    response["error_code"] = rejection["code"]
    response["unfreeze_command"] = rejection["unfreeze_command"]
    response["next_step"] = rejection["unfreeze_command"]
    return response


def _check_return_self_checks(
    *,
    task_id: str,
    task_metadata: dict[str, Any],
    repo_root: Path,
    result_summary: str | None,
    completion_report_ref: str | None,
    artifact_refs: list[str],
    claim_snapshot: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """
    AIPOS-F49: N3 交回自检门——五条机器判据在交回时拒收不合格交付。
    AIPOS-F93 件③: ①③ 读项目声明 test_contract; 未声明的判据跳过, warning 追加进 warnings(交回结果的 warnings)。
    
    五条判据:
    ① 夹具入常驻: 本卡新增 test 文件必须在项目声明的测试清单中(AIPOS-F93: test_contract.runall_path)
    ② 改动面在界内: git diff 文件必须落在 output_target 范围内
    ③ 有测试: 模式声明 requires_tests 的卡(enums.schema task_mode.requires_tests, 唯一读取口 next_resolver.task_mode_requires_tests;
       AIPOS-F211 取代原 task_mode == "code" 字面)必须有新增/修改的 test 文件
    ④ RETURN 非骨架: 不得含占位符，result_summary 非空
    ⑤ 分支合规 (AIPOS-F74): card/{task_id} 分支存在、含本卡提交、基座合法——AIPOS-F211: 适用 = 本卡有自己的卡分支(非审计卡且
       写产品仓, 唯一判据 next_resolver.card_writes_product_repo; 与门后 finalize / 裁决绑 tip 同口径, 写仓的 docs 等卡同查)
    
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
    
    from tools.aipos_cli.next_resolver import card_writes_product_repo, forensic_subject, task_mode_requires_tests

    task_mode = str(task_metadata.get("task_mode") or "").strip()
    # AIPOS-F211 件①: 本卡自己的卡分支 = 非审计卡且写产品仓(唯一判据 card_writes_product_repo; 审计卡无自己的分支, 自动豁免)
    own_branch = forensic_subject(task_metadata) is None and card_writes_product_repo(repo_root, {**task_metadata, "task_id": task_id})
    output_target = str(task_metadata.get("output_target") or "").strip()
    # AIPOS-F78 前置零⑤: 改动面判据读车道目录 lane.paths(声明), output_target 仅作派生来源
    lane = task_metadata.get("lane") if isinstance(task_metadata.get("lane"), dict) else {}
    lane_paths = [str(p).strip() for p in (lane.get("paths") or []) if str(p).strip()] if isinstance(lane.get("paths"), list) else []
    
    # ① 夹具入常驻(AIPOS-F78C: 判据读卡声明的仓 card_frontmatter=task_metadata)
    blocking_reasons.extend(_check_test_in_runall(
        task_id=task_id,
        repo_root=repo_root,
        card_frontmatter=task_metadata,
        warnings=warnings,
    ))
    
    # ② 改动面在界内
    blocking_reasons.extend(_check_changes_in_scope(
        task_id=task_id,
        output_target=output_target,
        repo_root=repo_root,
        lane_paths=lane_paths,
        card_frontmatter=task_metadata,
    ))
    
    # ③ 有测试(AIPOS-F211: 模式声明 requires_tests, 原 task_mode == "code" 字面退役)
    if own_branch and task_mode_requires_tests(task_mode):
        blocking_reasons.extend(_check_has_tests(
            task_id=task_id,
            repo_root=repo_root,
            card_frontmatter=task_metadata,
            warnings=warnings,
        ))
    
    # ④ RETURN 非骨架
    blocking_reasons.extend(_check_return_not_skeleton(
        task_id=task_id,
        result_summary=result_summary,
        completion_report_ref=completion_report_ref,
        artifact_refs=artifact_refs,
        repo_root=repo_root,
    ))
    
    # ⑤ 分支合规 (AIPOS-F74): 本卡有自己的卡分支才查(AIPOS-F211: card_writes_product_repo, 原 task_mode == "code" 字面退役)
    if own_branch:
        blocking_reasons.extend(_check_branch_compliance(
            task_id=task_id,
            task_metadata=task_metadata,
            repo_root=repo_root,
        ))
    
    return blocking_reasons



def _card_branch_changed_files(
    product_repo_root: Path, task_id: str, *, with_status: bool = False
) -> list[str] | list[tuple[str, str]] | None:
    """AIPOS-F78 前置零④: 卡分支相对其与基线分支的合并基的改动文件(`git diff <base>...<卡分支>`, 三点=merge-base)。
    AIPOS-F108 件②: 分支名 / 基线读 N5.branch_integration 声明(next_resolver.card_branch_name / card_base_branch), 零写死。

    worktree 模型下 main 与卡分支并行前进, 两点 diff(main..branch)会把 main 上别人的改动反向算进本卡 → 判据必错;
    三点 diff 只看卡分支自己的提交。git 失败返回 None(调用方跳过, 出 warning)。
    AIPOS-F97: 一次 `--name-status` 取改动集。缺省(with_status=False)返回路径列表, 与原 `--name-only` 同形同序
    (含删除项; 重命名/复制取新路径)——车道检查 CHANGES_OUT_OF_SCOPE 等调用方行为不变; with_status=True 返回
    [(状态字母, 改动后路径)] 供「本卡测试文件」判据 workspace_config.card_test_files 排除删除。"""
    import subprocess

    from tools.aipos_cli.next_resolver import card_base_branch, card_branch_name

    branch_name = card_branch_name(task_id)
    base_branch = card_base_branch()
    try:
        result = subprocess.run(
            ["git", "diff", f"{base_branch}...{branch_name}", "--name-status"],
            cwd=product_repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: git diff {base_branch}...{branch_name} failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    entries: list[tuple[str, str]] = []
    for line in result.stdout.split("\n"):
        if not line.strip():
            continue
        fields = line.split("\t")
        # 形: "<状态>\t<路径>"; 重命名/复制 "<R|C><相似度>\t<旧路径>\t<新路径>"(取新路径, 与 --name-only 一致)
        entries.append((fields[0].strip(), fields[-1].strip()))
    if with_status:
        return entries
    return [path for _status, path in entries]


def _git_show_on_branch(product_repo_root: Path, task_id: str, relative_path: str) -> str | None:
    """AIPOS-F78 前置零④: 读卡分支上的文件内容(`git show <卡分支>:<path>`), 不读共用检出文件系统。"""
    import subprocess

    from tools.aipos_cli.next_resolver import card_branch_name

    branch_name = card_branch_name(task_id)  # AIPOS-F108 件②: 分支名读 N5 branch_pattern 声明
    try:
        result = subprocess.run(
            ["git", "show", f"{branch_name}:{relative_path}"],
            cwd=product_repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: git show {branch_name}:{relative_path} failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _test_contract_for(repo_root: Path, product_repo_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """AIPOS-F93 件③: 卡仓的测试约定(唯一读取口 workspace_config.project_test_contract)。
    返回 (约定, None) 或 (None, TEST_CONTRACT_INVALID 拒因)——形不合声明 fail-closed 拒, 不当未声明跳过。"""
    from tools.aipos_cli.workspace_config import project_test_contract

    try:
        return project_test_contract(repo_root, product_repo_root), None
    except ValueError as exc:
        return None, (f"{exc}。出口: 按 config.schema project_json.test_contract 声明形修正治理根 project.json 的 test_contract")


def _card_test_files_for(changes: list[tuple[str, str]], contract: dict[str, Any]) -> tuple[list[str], str | None]:
    """AIPOS-F97 件①: 「本卡测试文件」(唯一判据 workspace_config.card_test_files)。返回 (测试文件, None) 或
    ([], TEST_FILES_UNRESOLVED 拒因)——改动集判不了 fail-closed 拒, 不当无测试放过。"""
    from tools.aipos_cli.next_resolver import card_base_branch
    from tools.aipos_cli.workspace_config import card_test_files

    try:
        return card_test_files(changes, contract), None
    except ValueError as exc:
        return [], f"{exc}。出口: 核对卡分支提交(git diff --name-status {card_base_branch()}...<卡分支>)后重交; 仍判不了请顾问出卡修判据"


def _contract_skip_warning(warnings: list[str] | None, text: str) -> None:
    """AIPOS-F93 件③: 测试约定未声明 = 判据跳过并 warning(进交回结果 warnings; 无 warnings 通道时出声 stderr)。"""
    if warnings is not None:
        warnings.append(text)
        return
    import sys

    print(f"Warning: {text}", file=sys.stderr)


def _check_test_in_runall(
    *,
    task_id: str,
    repo_root: Path,
    card_frontmatter: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """① 夹具入常驻: 本卡新增 test 文件必须在项目声明的测试清单中。
    AIPOS-F78 前置零④: 改动集与清单都读卡分支(git diff 三点 / git show card/<ID>:path), 不读共用检出。
    AIPOS-F78C: 仓 = 卡声明的仓(card_frontmatter.lane.repo)。
    AIPOS-F93 件③: 清单位置 = 项目声明 test_contract.runall_path(workspace_config.project_test_contract), 未声明 = 跳过并
    warning(原对所有项目套 lybra 自己的 run-all 约定); 声明了但卡分支上不存在 = 拒。拒因只引用声明值。"""
    blocking_reasons = []
    
    try:
        product_repo_root = _card_product_repo(repo_root, card_frontmatter)
    except ProductRepoNotConfigured:
        return blocking_reasons  # 无产品仓，跳过
    
    # 1. 卡分支改动集中的本卡测试文件(AIPOS-F97: 唯一判据 workspace_config.card_test_files, 删除不计, 式样读声明)
    changes = _card_branch_changed_files(product_repo_root, task_id, with_status=True)
    if changes is None:
        return blocking_reasons  # git 失败，跳过检查(已 warning)
    contract, invalid = _test_contract_for(repo_root, product_repo_root)
    if invalid:
        return [invalid]
    runall_rel = contract.get("runall_path")
    test_files, unresolved = _card_test_files_for(changes, contract)
    if unresolved:
        return [unresolved]
    if not test_files:
        return blocking_reasons  # 无 test 文件，跳过
    if not runall_rel:
        _contract_skip_warning(warnings, (
            f"TEST_REGISTRY_UNDECLARED: {task_id} 改动了测试文件但项目未声明测试清单(project.json test_contract.runall_path), "
            f"「测试须登记进清单」判据跳过(不 BLOCK); 来源 {contract.get('source') if contract else '-'}"
        ))
        return blocking_reasons
    
    # 2. 读取卡分支上的声明清单(禁读共用检出文件系统)
    runall_content = _git_show_on_branch(product_repo_root, task_id, runall_rel)
    if runall_content is None:
        from tools.aipos_cli.next_resolver import card_branch_name

        return [(
            f"TEST_NOT_IN_RUNALL: 项目声明的测试清单 {runall_rel}(来源 {contract.get('source')})在卡分支 {card_branch_name(task_id)} 上不存在。"
            f"出口: ①在卡分支提交该清单; ②声明有误请顾问修正 project.json test_contract.runall_path"
        )]
    
    # 3. 本卡测试文件是否已登记(AIPOS-F109 件④: 唯一判据 workspace_config.runall_unregistered——清单声明 discover = 命中声明式样
    #    即登记、只有文件级 exclude 的算未登记; 无 discover 行 = 原判据, 路径或文件名须出现在清单文本中)
    from tools.aipos_cli.next_resolver import card_branch_name
    from tools.aipos_cli.workspace_config import runall_unregistered

    try:
        missing_tests, directives = runall_unregistered(test_files, runall_content)
    except ValueError as exc:
        return [f"{exc}(清单 {runall_rel}, 卡分支 {card_branch_name(task_id)})。出口: 修正声明行后重交"]
    if missing_tests and directives["discover"]:
        # AIPOS-F119 件②: 拒因逐项附清单内 exclude 声明的理由(读 runall_directives 解析结果, 不另解析)
        excluded = [f"{path}(声明理由: {directives['exclude'][path]})" for path in missing_tests]
        blocking_reasons.append(
            f"TEST_NOT_IN_RUNALL: 本卡测试文件被项目声明的测试清单 {runall_rel}(来源 {contract.get('source')})以 exclude 声明排除、"
            f"不会执行: {', '.join(excluded)}。出口: ①删该 exclude 行让其随自动发现执行; ②确属不可在夹具环境执行, 请顾问裁定后再交"
        )
        return blocking_reasons

    if missing_tests:
        blocking_reasons.append(
            f"TEST_NOT_IN_RUNALL: 本卡新增/修改的 test 文件未登记进项目声明的测试清单 {runall_rel}"
            f"(来源 {contract.get('source')})。缺失项: {', '.join(missing_tests)}。"
            f"出口: ①在 {runall_rel} 中登记这些测试; "
            f"②若属卡面漏列, 请顾问 amend 车道后重试; "
            f"③若确属越界, 请回退该文件。"
        )
    
    return blocking_reasons


def _check_changes_in_scope(
    *,
    task_id: str,
    output_target: str,
    repo_root: Path,
    lane_paths: list[str] | None = None,
    card_frontmatter: dict[str, Any] | None = None,
) -> list[str]:
    """② 改动面在界内: 卡分支改动文件必须落在车道目录内。
    AIPOS-F78 前置零⑤: 范围 = lane.paths(声明); 无 lane 时由 output_target 解析(同一解析函数 machine_zone.parse_output_target_paths)。
    AIPOS-F78 前置零④: 改动集读卡分支三点 diff(main...card/<ID>), 不读共用检出。"""
    blocking_reasons = []
    
    path_patterns = [str(p).strip() for p in (lane_paths or []) if str(p).strip()]
    scope_source = "lane.paths"
    if not path_patterns:
        if not output_target:
            return blocking_reasons  # 无 lane/output_target 声明，跳过
        from tools.aipos_cli.machine_zone import parse_output_target_paths

        path_patterns = parse_output_target_paths(output_target)
        scope_source = "output_target"
    
    if not path_patterns:
        return blocking_reasons  # 无法解析 output_target，跳过
    
    try:
        product_repo_root = _card_product_repo(repo_root, card_frontmatter)
    except ProductRepoNotConfigured:
        return blocking_reasons
    
    # 1. 卡分支改动文件(三点 diff)
    changed_files = _card_branch_changed_files(product_repo_root, task_id)
    if changed_files is None:
        return blocking_reasons  # git 失败，跳过检查(已 warning)
    if not changed_files:
        return blocking_reasons  # 无改动，跳过
    
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
            f"CHANGES_OUT_OF_SCOPE: 以下文件超出卡面声明的车道范围({scope_source})。"
            f"越界文件: {', '.join(out_of_scope)}。"
            f"卡面声明范围: {', '.join(path_patterns)}。"
            f"出口: ①若属卡面漏列, 请顾问 `lybra queue amend --restricted --task-id {task_id} --amendments '{{\"lane\": {{...}}}}'` 补车道后重试; "
            f"②若确属越界, 请回退该文件(git checkout main -- <file>); "
            f"③若需保留改动, 请顾问重发任务卡覆盖全部根因文件。"
        )
    
    return blocking_reasons


def _check_has_tests(
    *,
    task_id: str,
    repo_root: Path,
    card_frontmatter: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """③ 有测试: code 类卡必须有新增/修改的 test 文件。AIPOS-F78C: 仓 = 卡声明的仓。
    AIPOS-F93 件③: 只在项目声明 test_contract.require_tests=true 时检查; 未声明 = 跳过并 warning。拒因只引用声明值。"""
    blocking_reasons = []
    
    try:
        product_repo_root = _card_product_repo(repo_root, card_frontmatter)
    except ProductRepoNotConfigured:
        return blocking_reasons
    
    # 1. 卡分支改动集(AIPOS-F78 前置零④: 三点 diff, 只看卡分支自己的提交; AIPOS-F97: 带状态, 供唯一判据排除删除)
    changes = _card_branch_changed_files(product_repo_root, task_id, with_status=True)
    if changes is None:
        return blocking_reasons  # git 失败，跳过检查(已 warning)
    if not changes:
        return blocking_reasons  # 无改动，跳过
    changed_files = [path for _status, path in changes]
    contract, invalid = _test_contract_for(repo_root, product_repo_root)
    if invalid:
        return [invalid]
    if contract.get("require_tests") is not True:
        if contract.get("require_tests") is None:
            _contract_skip_warning(warnings, (
                f"TEST_REQUIREMENT_UNDECLARED: 项目未声明 code 类卡是否必须含测试(project.json test_contract.require_tests), "
                f"「须含测试」判据跳过(不 BLOCK); 来源 {contract.get('source')}"
            ))
        return blocking_reasons
    
    # 2. 检查是否有本卡测试文件(AIPOS-F97: 唯一判据 workspace_config.card_test_files; 只删测试 = 无测试改动)
    test_files, unresolved = _card_test_files_for(changes, contract)
    if unresolved:
        return [unresolved]
    
    if not test_files:
        registry = contract.get("runall_path")
        blocking_reasons.append(
            f"NO_TESTS: 项目声明 code 类卡须含测试文件改动(来源 {contract.get('source')}; "
            f"测试文件式样 {contract.get('test_file_globs')} 来源 {contract.get('test_file_globs_source')}, 删除不计)。"
            f"当前改动文件: {', '.join(changed_files[:5])}{'...' if len(changed_files) > 5 else ''}。"
            f"出口: ①添加测试文件{f'并登记进项目声明的测试清单 {registry}' if registry else ''}; "
            f"②若本卡确无需测试(纯配置/纯文档), 请顾问 amend task_mode 为非 code。"
        )
    
    return blocking_reasons


def _check_return_not_skeleton(
    *,
    task_id: str,
    result_summary: str | None,
    completion_report_ref: str | None,
    artifact_refs: list[str],
    repo_root: Path,
) -> list[str]:
    """④ RETURN 非骨架: 不得含占位符，result_summary 非空。
    
    AIPOS-F63 修复: fail-closed 改造
    - result_summary 必须检查占位符（原来只查空不查占位符）
    - completion_report_ref 为空即拒（原来为空就跳过整段检查）
    - artifact_refs 为空即拒（AIPOS-F63 追加甲）
    - 文件不存在即拒（原来不存在就跳过）
    - 读取失败即拒（原来失败就跳过）
    """
    from tools.aipos_cli.validation_common import (
        check_required_field,
        check_file_ref_exists,
        check_placeholder_in_text,
    )
    
    blocking_reasons = []
    
    # AIPOS-F63: result_summary 必填且检查占位符（原来只查空）
    blocking_reasons.extend(
        check_required_field(
            result_summary,
            "result_summary",
            check_placeholders=True,
        )
    )
    
    # AIPOS-F63: completion_report_ref 必填（原来为空就跳过检查）
    blocking_reasons.extend(
        check_required_field(
            completion_report_ref,
            "completion_report_ref",
            check_placeholders=False,
        )
    )
    
    # AIPOS-F63 追加甲: artifact_refs 必填且非空
    blocking_reasons.extend(
        check_required_field(
            artifact_refs,
            "artifact_refs",
            check_placeholders=False,
        )
    )
    
    # AIPOS-F63: 文件必须存在（原来不存在就跳过）
    if completion_report_ref and completion_report_ref.strip():
        file_check = check_file_ref_exists(
            completion_report_ref,
            "completion_report_ref",
            repo_root,
            required=True,
        )
        blocking_reasons.extend(file_check)
        
        # 只有文件存在时才检查内容占位符
        if not file_check:
            report_path = repo_root / completion_report_ref
            try:
                content = report_path.read_text(encoding="utf-8")
                blocking_reasons.extend(
                    check_placeholder_in_text(content, "RETURN.md 内容")
                )
            except Exception as e:
                # AIPOS-F63: 读取失败即拒（原来失败就跳过）
                blocking_reasons.append(
                    f"FILE_READ_FAILED: 无法读取 RETURN.md 文件 {completion_report_ref}: {e}。"
                    f"出口: 确保文件可读，检查文件权限和编码。"
                )
    
    return blocking_reasons


def base_sync_command(branch: str, base: str) -> str:
    """AIPOS-F117 件②(gap #75): BRANCH_WRONG_BASE 出口命令的唯一渲染——读 transitions.schema N5.branch_integration.base_sync
    (基线合入卡分支, 禁 rebase/stash)。声明缺 / command 非串 / 占位不全 = SchemaLoadError(fail-closed, 不回落写死命令)。"""
    from tools.schema_loader import get_branch_integration

    decl = get_branch_integration().get("base_sync")
    template = decl.get("command") if isinstance(decl, dict) else None
    if not isinstance(template, str) or "{branch}" not in template or "{base}" not in template:
        raise SchemaLoadError("transitions.schema.json N5.branch_integration.base_sync.command 未声明(须含 {branch} 与 {base})")
    return template.replace("{branch}", branch).replace("{base}", base)


def _check_branch_compliance(
    *,
    task_id: str,
    task_metadata: dict[str, Any],
    repo_root: Path,
) -> list[str]:
    """⑤ 分支合规 (AIPOS-F74): card/{task_id} 分支存在、含本卡提交、基座合法。
    
    AIPOS-F74 件①: 交回自检判据⑤「分支合规」— 门上拦分支丢失。
    
    三条子判据:
    1. card/{task_id} 分支存在(名按 N5.branch_integration 声明推导)
    2. 分支包含本卡至少一笔提交
    3. merge-base 为当前 main(或 fix 卡声明的合法基座)
    
    任一不满足 = 拒并给出口(明示正确命令)。
    fail-closed: 豁免走既有 §9 通道(owner_confirmation_token)。
    
    注: 判据仅施于 task_mode=code 的执行卡,审计卡(R 卡, 无代码分支)自动豁免。
    """
    blocking_reasons = []
    
    try:
        product_repo_root = _card_product_repo(repo_root, task_metadata)  # AIPOS-F78C: 仓 = 卡声明的仓
    except ProductRepoNotConfigured:
        # 无产品仓配置,跳过分支检查
        return blocking_reasons
    
    import subprocess
    
    # AIPOS-F108 件②(M18): 分支名 / 基线读 N5.branch_integration 声明(唯一读取口, 读 Lybra 自身 schema 而非项目产品仓);
    # 原实现读产品仓 schema(非 lybra 形项目无 schema/ → except Exception 静默回落写死 card/{task_id}), 已退役; 声明缺 = SchemaLoadError
    from tools.aipos_cli.next_resolver import card_base_branch, card_branch_name, card_expected_base

    branch_name = card_branch_name(task_id)
    base_branch = card_base_branch()
    
    # 子判据 1: 分支存在
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=product_repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            blocking_reasons.append(
                f"BRANCH_NOT_FOUND: 分支 '{branch_name}' 不存在。"
                f"出口: 在产品仓执行 'git checkout -b {branch_name}' 创建分支,"
                f"提交代码后再交回。"
            )
            # 分支不存在,后续检查无法进行
            return blocking_reasons
    except subprocess.TimeoutExpired:
        blocking_reasons.append(
            f"BRANCH_CHECK_TIMEOUT: git 命令超时,无法验证分支 '{branch_name}'。"
        )
        return blocking_reasons
    except Exception as e:
        blocking_reasons.append(
            f"BRANCH_CHECK_FAILED: 无法验证分支 '{branch_name}': {e}。"
        )
        return blocking_reasons
    
    # 子判据 2: 分支包含本卡至少一笔提交
    try:
        result = subprocess.run(
            ["git", "log", f"{base_branch}..{branch_name}", "--oneline"],
            cwd=product_repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            commits = [line for line in result.stdout.strip().split("\n") if line.strip()]
            if not commits and not _report_declares_blocked(repo_root, task_id, task_metadata):
                # AIPOS-F252 件②: 交回报告声明 outcome=blocked(按卡停线)可无新提交; 分支存在与基座两条照核
                blocking_reasons.append(
                    f"BRANCH_NO_COMMITS: 分支 '{branch_name}' 相对 {base_branch} 没有新提交。"
                    f"出口: 在产品仓 '{branch_name}' 分支提交代码:"
                    f"'git add <files> && git commit -m \"feat({task_id}): <message>\"',"
                    f"然后再交回。"
                )
        else:
            # git log 失败,可能是分支与基线分支无共同祖先
            blocking_reasons.append(
                f"BRANCH_DIVERGED: 分支 '{branch_name}' 与 {base_branch} 无共同历史,无法对比提交。"
                f"出口: 检查分支是否从正确的 {base_branch} 创建。"
            )
    except subprocess.TimeoutExpired:
        blocking_reasons.append(
            f"BRANCH_COMMIT_CHECK_TIMEOUT: git log 超时,无法验证分支 '{branch_name}' 的提交。"
        )
    except Exception as e:
        blocking_reasons.append(
            f"BRANCH_COMMIT_CHECK_FAILED: 无法验证分支 '{branch_name}' 的提交: {e}。"
        )
    
    # 子判据 3: merge-base 为当前基线分支(或 fix 卡声明的合法基座)
    # 读取 fix 卡的合法基座(如果有)
    expected_base = card_expected_base(task_metadata)  # AIPOS-F150: 期望基座唯一判定(fix 卡 fix_base_branch, 缺省 base_branch 声明)
    
    try:
        # 获取 merge-base
        result = subprocess.run(
            ["git", "merge-base", branch_name, expected_base],
            cwd=product_repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            merge_base = result.stdout.strip()
            # 获取 expected_base 的当前 commit
            result_base = subprocess.run(
                ["git", "rev-parse", expected_base],
                cwd=product_repo_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result_base.returncode == 0:
                base_commit = result_base.stdout.strip()
                if merge_base != base_commit:
                    blocking_reasons.append(
                        f"BRANCH_WRONG_BASE: 分支 '{branch_name}' 的基座不是当前 {expected_base}。"
                        f"merge-base: {merge_base[:8]}, 当前 {expected_base}: {base_commit[:8]}。"
                        f"出口: 在产品仓执行 '{base_sync_command(branch_name, expected_base)}' "
                        f"把最新 {expected_base} 合入本分支(禁 rebase/stash, 声明 transitions N5.branch_integration.base_sync)。"
                    )
            else:
                blocking_reasons.append(
                    f"BRANCH_BASE_CHECK_FAILED: 无法获取 {expected_base} 的当前 commit。"
                )
        else:
            # merge-base 失败,可能是分支与基座无共同祖先
            blocking_reasons.append(
                f"BRANCH_NO_COMMON_ANCESTOR: 分支 '{branch_name}' 与 {expected_base} 无共同祖先。"
                f"出口: 检查分支是否从正确的 {expected_base} 创建。"
            )
    except subprocess.TimeoutExpired:
        blocking_reasons.append(
            f"BRANCH_BASE_CHECK_TIMEOUT: git merge-base 超时,无法验证分支 '{branch_name}' 的基座。"
        )
    except Exception as e:
        blocking_reasons.append(
            f"BRANCH_BASE_CHECK_FAILED: 无法验证分支 '{branch_name}' 的基座: {e}。"
        )
    
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
        try:
            example_prefix = _return_ref_prefix(repo_root, task_id_for_example)
        except (SchemaLoadError, OSError, ValueError) as exc:
            example_prefix = f"<project.json paths.return_root>/{task_id_for_example}/ (声明读取失败: {exc}) "
        blocking_reasons.append(
            f"UNSAFE_RETURN_REF: return材料路径必须在治理工作区 <project.json paths.return_root>/<task_id>/ 内，"
            f"禁止 /tmp 与仓外路径。非法路径: {unsafe_refs}. "
            f"正确落点示例: {example_prefix}RETURN.md, "
            f"{example_prefix}artifacts/output.txt"
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
        # AIPOS-F242 件③: 凭据判据下沉门侧交回写入口(同 F162/F163 运行证据下沉)——artifact ingest 派生 / 手敲 / 门 MCP 直调一切交回同核
        # 唯一判据 artifact_ingest.credential_scan(经 gate_credential_blocking), 拒因同码同文、不回显值; 随请求提交的 result_summary /
        # return_body(门将写成交回报告)与报告 / 落点同扫
        from tools.aipos_cli.artifact_ingest import gate_credential_blocking

        report_candidate = (repo_root / completion_report_ref) if completion_report_ref and not _unsafe_return_ref(completion_report_ref) else None
        blocking_reasons.extend(gate_credential_blocking(
            repo_root, task_id_text, kind="return", card_fm={**source_metadata, "task_id": task_id_text},
            report=report_candidate if report_candidate is not None and report_candidate.is_file() else None,
            extra_texts=[("queue_return.result_summary", str(result_summary or "")),
                         (_return_body_rel(repo_root, task_id_text) + "(return_body)", str(return_body or ""))]))
    
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
    
    # AIPOS-FND-5: 写产品仓的卡交回门检测未提交代码
    # AIPOS-R4B-2 N3: 交回门按卡 scope — 只检查本卡声明的路径，不全仓扫描
    # AIPOS-F197 件②: 只写治理根的卡(next_resolver.card_governance_outputs)不写产品仓、无卡分支, 产出在治理根由 N6 落账提交——
    # 无「代码未提交」可查(交回绑定产出清单指纹代之, return_binding_subject)
    from tools.aipos_cli.next_resolver import card_governance_outputs

    governance_outputs = card_governance_outputs(repo_root, {**source_metadata, "task_id": str(task.get("task_id") or "")})
    if governance_outputs:
        # 门侧同核产出清单(与产物入口同一实现 governance_outputs_manifest; 手工 queue return 不绕过): 清单不合 = 拒, 不落无绑定交回
        from tools.aipos_cli.next_resolver import GovernanceOutputsError, governance_outputs_manifest

        try:
            governance_outputs_manifest(repo_root, str(task.get("task_id") or ""), source_metadata)
        except GovernanceOutputsError as exc:
            blocking_reasons.append(f"{exc.code}: AIPOS-F197 只写治理根的卡交回须带合规产出清单(Return frontmatter outputs): {exc.reason}")
    # AIPOS-F211 件①: 查不查 = 本卡写产品仓(唯一判据 next_resolver.card_writes_product_repo, 与建树 / 分支合规 / finalize 同口径);
    # 原 task_mode == "code" 或 artifact_policy == "formal_write" 第二判据退役(起草缺省人人 formal_write, 不写仓的验证卡 / 缺 task_mode 卡也被查)
    from tools.aipos_cli.next_resolver import card_writes_product_repo, forensic_subject

    writes_repo = forensic_subject(source_metadata) is None and card_writes_product_repo(
        repo_root, {**source_metadata, "task_id": str(task.get("task_id") or "")})
    if writes_repo:  # 只写治理根卡(governance_outputs)恒不写仓
        current_task_id = str(task.get("task_id") or "")
        try:
            product_repo_root = _resolve_product_code_repo(repo_root, source_metadata)  # AIPOS-F78C: 卡声明的仓
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
    returned_at = planned_returned_at or iso_z()
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

    # AIPOS-F49: N3 交回自检门——六条机器判据
    # claim 快照(判据⑤⑥)尚无解析实现: 恒为 None。AIPOS-F115 件③: 原此处读 claim 记录到未使用变量并 except Exception: pass 吞读错(死代码), 删除。
    claim_snapshot = None
    
    self_check_reasons = _check_return_self_checks(
        task_id=task_id_text,
        task_metadata=source_metadata,
        repo_root=repo_root,
        result_summary=result_summary,
        completion_report_ref=completion_report_ref,
        artifact_refs=artifact_refs,
        claim_snapshot=claim_snapshot,
        warnings=warnings,
    )
    
    # AIPOS-F49-fix1: owner_confirmation_token 强制放行机制
    # 若自检失败但调用带 owner_confirmation_token, 则放行并标记 waived
    self_check_waived = False
    waiver_reason = None
    if self_check_reasons and mcp_return_metadata:
        owner_token = mcp_return_metadata.get("owner_confirmation_token")
        if owner_token:
            # Owner 强制放行，豁免自检
            self_check_waived = True
            waiver_reason = f"Owner confirmation token provided, waived {len(self_check_reasons)} self-check failures"
            # 记录被豁免的判据（留痕）
            warnings.extend([
                f"SELF_CHECK_WAIVED: {reason}" for reason in self_check_reasons
            ])
            # 清空 blocking_reasons 中的自检失败（放行）
            self_check_reasons = []
    
    blocking_reasons.extend(self_check_reasons)

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
            self_check_waived=self_check_waived,
            self_check_waiver_reason=waiver_reason,
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

    # AIPOS-320: RETURN.md planned write (gate-side落盘, 路径严格限定声明位 <paths.return_root>/<ID>/<默认候选>, AIPOS-F89 件① H9)
    return_body_planned_writes: list[dict[str, Any]] = []
    if return_body is not None and task_id_text:
        return_body_rel = _return_body_rel(repo_root, task_id_text)
        return_body_planned_writes = [
            {"path": return_body_rel, "kind": "create", "type": "return_body"}
        ]

    # AIPOS-F49-fix1: 将 waiver 信息添加到 data 中，供 return 记录使用
    if self_check_waived:
        data["self_check_waived"] = True
        data["self_check_waiver_reason"] = waiver_reason

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
        frozen_block = _legacy_frozen_block("queue_return", resolved_root, [(task_id, path)], dry_run=dry_run, actor=actor_text)
        if frozen_block is not None:
            return frozen_block
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
        # AIPOS-320: write RETURN.md when return_body was provided (路径严格限定声明位, AIPOS-F89 件① H9: _return_body_rel)
        return_body_performed_writes: list[dict[str, Any]] = []
        if return_body is not None:
            task_id_for_return = str(data.get("task_id") or "")
            if task_id_for_return:
                return_body_rel = _return_body_rel(resolved_root, task_id_for_return)
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
    supplementary: bool = False,
    runtime_evidence_commands: list[str] | None = None,
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

    source_task_id_for_verdict = str(source_task.get("task_id") or "")
    existing_verdicts = records.get("task_audit_verdicts", {}).get(source_task_id_for_verdict, [])
    superseded_ref = None  # AIPOS-F72: 初始化 superseded_ref
    supplementary_runtime_evidence: dict[str, Any] | None = None
    if supplementary:
        # AIPOS-F162 件②: 已结案卡补充审计(声明 transitions supplementary_audit)——前提 = 原卡已结案, 同卡无在途补审, 有争议摘要,
        # 发起者 = 项目顾问(角色类 advisor); 不判 claimed/executor_status/已 PASS(那是常规派审的前提), 原卡卡面不改
        from tools.aipos_cli.audit_derivation import supplementary_audit_declaration, supplementary_in_flight
        from tools.aipos_cli.custom_roles import authorize_instance_class

        sup_decl = supplementary_audit_declaration()
        # AIPOS-F209: 是否结案读结案口径唯一判定 task_complexity.closure_state(门生 closure / 顾问复核结案 / 卡在 completed/), 不另判
        from tools.aipos_cli.task_complexity import closure_state

        try:
            source_closure = closure_state(repo_root, source_task_id_for_verdict)
        except (OSError, ValueError) as exc:  # 卡面 / 记录读不出(FrontmatterReadError ⊂ ValueError)= 拒, 不当作已结案
            source_closure = None
            blocking_reasons.append(f"SUPPLEMENTARY_SOURCE_NOT_CONCLUDED: {source_task_id_for_verdict} 结案口径读不出(fail-closed): "
                                    f"{type(exc).__name__}: {exc}")
        if source_closure is not None and not source_closure["concluded"]:
            blocking_reasons.append(
                f"SUPPLEMENTARY_SOURCE_NOT_CONCLUDED: {source_task_id_for_verdict} 未结案(queue={source_task.get('queue_state')}, 无 closure 记录, "
                "非顾问复核结案); 补充审计只对已结案卡。出口: 在途卡走常规 audit dispatch / 复审轮")
        in_flight = supplementary_in_flight(repo_root, source_task_id_for_verdict)
        if in_flight:
            blocking_reasons.append(
                f"SUPPLEMENTARY_AUDIT_IN_FLIGHT: {source_task_id_for_verdict} 已有在途补充审计 {in_flight}(同卡同时只允许一张); "
                f"出口: 等 {in_flight} 出裁决后再发起")
        if not str(dispatch_reason or "").strip():
            blocking_reasons.append("SUPPLEMENTARY_REASON_REQUIRED: 补充审计须给争议摘要(--reason)")
        role_gate = authorize_instance_class(actor, str(sup_decl["actor_role_class"]), repo_root)
        if not role_gate["ok"]:
            blocking_reasons.append(
                f"SUPPLEMENTARY_ACTOR_NOT_ADVISOR: 补充审计只能由角色类 {sup_decl['actor_role_class']} 的实例发起: {role_gate['reason']}")
        if runtime_evidence_commands:
            # AIPOS-F165 件①: 声明校验唯一处(形 + 派发前可执行核查, 出卡 draft_validator 同调); 不过 = 预演即报, --confirm 拒
            from tools.aipos_cli.artifact_ingest import (
                RuntimeEvidenceDeclarationError, check_runtime_evidence_declaration, normalize_runtime_evidence_declaration,
            )

            raw_evidence = {"required": True, "commands": list(runtime_evidence_commands)}
            # AIPOS-F233 件②: 补审声明命令别名按被审卡交付主体改写为规范形(改写前后原文进 warnings); 词表外花括号按字面保留, 提示进 warnings
            normalized = normalize_runtime_evidence_declaration(raw_evidence, card_fm=source_metadata, task_id=str(source_task_id_for_verdict))
            raw_evidence = normalized["value"]
            warnings.extend(normalized["notes"])
            try:
                checked = check_runtime_evidence_declaration(raw_evidence, governance_root=repo_root, audit_instance=audit_agent_instance,
                                                             card_task_id=str(source_task_id_for_verdict))
                blocking_reasons.extend(checked["problems"])
                warnings.extend(checked["notes"])
                supplementary_runtime_evidence = raw_evidence
            except RuntimeEvidenceDeclarationError as exc:
                blocking_reasons.append(f"RUNTIME_EVIDENCE_DECLARATION_INVALID: {exc}")
    else:
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
                # AIPOS-F78 前置零⑦(F79 实撞): PASS 绑的是裁决自述 artifact_subject.commit_sha; 卡分支 tip 已变(解冲突/追加提交)
                # → 审过的产物已不是当前产物, 放行为「复审」派审(warn), 而非 AUDIT_ALREADY_PASSED 死锁只能承接卡重审
                rereview = _pass_verdict_tip_changed(repo_root, source_task_id_for_verdict, latest_verdict, card_frontmatter=source_metadata)
                if rereview:
                    warnings.append(f"REREVIEW_TIP_CHANGED: {rereview}")
                else:
                    blocking_reasons.append("AUDIT_ALREADY_PASSED: source task already has audit PASS (terminal state, cannot overturn)")
            # FAIL/REQUEST_CHANGES/BLOCKED 等非终态:允许 re-dispatch,不 BLOCK
        elif source_metadata.get("dependency_audit_status") == Verdict.PASS:
            # 兜底:metadata 显示 PASS 但没找到 verdict 记录(数据不一致)
            blocking_reasons.append("AUDIT_ALREADY_PASSED: source task already has audit PASS")
    
        # AIPOS-F2 ③立墙带路: 检测手写文件在场时附加提示
        from tools.aipos_cli.audit_helpers import detect_hand_written_verdicts, HAND_WRITTEN_VERDICT_NOTICE, is_dispatch_chain_valid
        _hw_dispatch = detect_hand_written_verdicts(
            record_dir(repo_root, "audit_verdicts", source_task_id_for_verdict)
        )
        if _hw_dispatch:
            warnings.append(HAND_WRITTEN_VERDICT_NOTICE)
            for _hw in _hw_dispatch:
                warnings.append(f"  ignored: {_hw['file']} ({_hw['reason']})")
    
        # AIPOS-F72: 派审链有效性判据(manual dispatch 与 auto derivation 同源)
        # 链有效 ⇔ (审计卡 pending/claimed 在途) 或 (源卡已有裁决)
        # 审计卡 concluded/withdrawn 且零裁决 → 链失效,放行 re-dispatch
        superseded_ref = None  # AIPOS-F72: 初始化 superseded_ref
        if source_metadata.get("related_audit_task_ref") or source_metadata.get("audit_dispatch_record_ref"):
            chain_valid, superseded_ref = is_dispatch_chain_valid(source_metadata, existing_verdicts, repo_root)
            if chain_valid:
                # 链有效:审计在途或已有裁决 → BLOCK
                if existing_verdicts:
                    latest_verdict = max(existing_verdicts, key=_verdict_time)
                    latest_verdict_value = str(latest_verdict.get("verdict", "")).upper().strip()
                    # AIPOS-R6J: 非终态裁决允许re-dispatch(FAIL/BLOCK/WARN/NEEDS_OWNER)
                    if latest_verdict_value not in {Verdict.FAIL, Verdict.BLOCK, Verdict.WARN, Verdict.NEEDS_OWNER}:
                        blocking_reasons.append("AUDIT_ALREADY_DISPATCHED: source task already links an audit dispatch")
                else:
                    # 审计卡在途(pending/claimed),BLOCK
                    blocking_reasons.append("AUDIT_ALREADY_DISPATCHED: source task already links an audit dispatch")
            else:
                # 链失效:旧审计卡已废且零裁决 → 放行,但须出声披露旧链
                if superseded_ref:
                    warnings.append(f"SUPERSEDING_DEAD_DISPATCH_CHAIN: old audit card {superseded_ref} is concluded with zero verdicts; allowing re-dispatch (new dispatch will record supersedes={superseded_ref})")
    # AIPOS-F72: 若放行 re-dispatch,记录 supersedes 引用
    supersedes_dispatch_ref = None
    if superseded_ref:
        supersedes_dispatch_ref = str(source_metadata.get("audit_dispatch_record_ref") or source_metadata.get("related_audit_task_ref") or "").strip()

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
    from tools.aipos_cli.task_loader import queue_state_ref  # AIPOS-F89 件① M8: 队列根唯一读取口

    audit_rel = f"{queue_state_ref(repo_root, 'pending')}{_task_filename_for(task_id_text)}.md"
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

    timestamp = planned_dispatched_at or iso_z()
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
        "task_class": "simple",  # AIPOS-F64: 单源卡形 (F72R为准, 消灭 complex 分叉)
        "model_tier": source_metadata.get("model_tier") or "L2",
        "priority": source_metadata.get("priority") or "medium",
        "status": "pending",
        "created_by": actor,
        "needs_owner": False,
        "audit": "none",  # AIPOS-F64: 单源卡形 - 审计卡不需要再次审计
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
    if supplementary:
        # AIPOS-F162 件②: 补审卡卡面 = 补审标记 + 争议摘要; 无依赖(原卡已结案, 依赖门按常规审计条件判必不满足)
        audit_metadata["supplementary_of"] = str(source_task.get("task_id") or "")
        audit_metadata["supplementary_reason"] = str(dispatch_reason or "").strip()
        audit_metadata["title"] = audit_task_title or f"补充审计 {source_task.get('task_id')}: {str(dispatch_reason or '').strip()[:80]}"
        audit_metadata["depends_on"] = []
        for key in ("dependency_condition", "dependency_executor_status", "dependency_audit_readiness", "dependency_audit_status"):
            audit_metadata.pop(key, None)
        if supplementary_runtime_evidence:
            audit_metadata["audit_runtime_evidence"] = supplementary_runtime_evidence
    # AIPOS-A1 大项C: 手动派审也注入取证锚点到 governance_refs
    # AIPOS-F78C 产品仓 = 被审卡声明的仓; AIPOS-F66B 件③ 报告落点 = 审计卡 ID 目录; AIPOS-F210 件②: 锚点一行与自动派生同一函数
    # forensic_anchor_ref(按被审卡交付主体三态: 卡分支 diff / 固定候选只读 / 治理根产出清单), 禁本处另拼
    from tools.aipos_cli.audit_derivation import forensic_anchor_ref
    _forensic_ref = forensic_anchor_ref(repo_root, str(source_task.get("task_id") or ""), source_metadata, task_id_text)
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
    if supplementary:
        audit_body += (
            f"\n## 补充审计(AIPOS-F162 件②)\n\n- 原卡 `{source_task.get('task_id')}` 已结案; 本卡是顾问 `{actor}` 发起的补充审计, "
            f"原 closure 与原裁决不动、不回滚 main\n- 争议摘要: {str(dispatch_reason or '').strip()}\n"
            "- 审的是原卡分支当前 tip(取证工作树由认领建); 准绳 = 原卡全文 + 本争议摘要\n"
            "- 裁决: PASS 类 = 原卡附「补审通过」; FAIL/BLOCK = 门自动派生关联修复卡(走常规执行/审计/合入)\n"
        )
    audit_body += build_forensic_anchor_section(str(source_task.get("task_id") or ""), repo_root, source_metadata, audit_task_id=task_id_text,
                                                audit_metadata=audit_metadata)
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
        supersedes=supersedes_dispatch_ref,  # AIPOS-F72: 记录被取代的旧链
        supplementary_of=str(source_task.get("task_id") or "") if supplementary else None,
        supplementary_reason=str(dispatch_reason or "").strip() if supplementary else None,
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
        "supplementary": bool(supplementary),  # AIPOS-F162 件②: 补充审计 = 原卡卡面不改(不写 target_path)
        # AIPOS-C1 大项C②: idempotent dispatch flags
        "idempotent_supplement": audit_task_already_exists or dispatch_record_already_exists,
        "audit_task_already_existed": audit_task_already_exists,
        "dispatch_record_already_existed": dispatch_record_already_exists,
    }
    if supplementary and audit_task_already_exists:
        # AIPOS-F162 件②: 补审卡号只派新卡(缺省 = 声明形下一个空号), 不幂等覆盖既有卡
        blocking_reasons.append(f"SUPPLEMENTARY_AUDIT_ID_EXISTS: {task_id_text} 已存在, 补充审计不覆盖既有卡(省略 --audit-task-id 取下一个空号)")
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
        planned_writes=([] if supplementary else [{"path": source_rel, "kind": "update", "type": "task_markdown"}]) + [
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
    supplementary: bool = False,
    runtime_evidence_commands: list[str] | None = None,
) -> dict[str, Any]:
    try:
        actor_text = str(actor or "").strip()
        instance_text = str(agent_instance or "").strip()
        policy_ref = str(owner_policy_ref or "").strip()
        audit_id = str(audit_task_id or "").strip()
        audit_instance = str(audit_agent_instance or "").strip()
        if supplementary:
            # AIPOS-F162 件②: 补充审计缺省值读声明/原卡(卡号 = 声明形下一个; 审计实例 = 原卡 audit_by → 按项目推导;
            # 派审实例 = 发起顾问; 授权引用 = 声明 owner_policy_ref, 不需 Owner 信封)
            from tools.aipos_cli.audit_derivation import (
                next_supplementary_audit_id, resolve_audit_instance, supplementary_audit_declaration,
            )

            sup_root = _resolve_repo_root(repo_root)
            sup_task = _select_task(sup_root, task_id=source_task_id, path=source_path,
                                    id_param_name="source_task_id", path_param_name="source_task_path")
            sup_fm, _b, _w = parse_markdown_frontmatter((sup_root / str(sup_task.get("path") or "")).read_text(encoding="utf-8"))
            sup_fm = _normalize_return_value(sup_fm)
            instance_text = instance_text or actor_text
            policy_ref = policy_ref or str(supplementary_audit_declaration()["owner_policy_ref"])
            audit_id = audit_id or next_supplementary_audit_id(sup_root, str(sup_task.get("task_id") or ""))
            audit_instance = audit_instance or resolve_audit_instance(sup_fm, sup_root)
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
        frozen_block = _legacy_frozen_block("audit_dispatch", resolved_root, [(source_task_id, source_path), (audit_id, None)],
                                            dry_run=dry_run, actor=actor_text)
        if frozen_block is not None:
            return frozen_block
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
            supplementary=supplementary,
            runtime_evidence_commands=runtime_evidence_commands,
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
        performed: list[dict[str, Any]] = []
        if not data.get("supplementary"):  # AIPOS-F162 件②: 补充审计不改原卡卡面(原卡已结案)
            (resolved_root / str(data.get("target_path") or "")).write_text(str(data.get("rendered_markdown") or ""), encoding="utf-8")
            performed.append({"path": data.get("target_path"), "kind": "update", "type": "task_markdown"})
        audit_path = resolved_root / str(data.get("audit_task_path") or "")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        # AIPOS-C1 大项C②: idempotent — if audit card already exists, skip creating it
        # (it was created by a prior dispatch). Still update source + dispatch record.
        audit_task_kind = "update" if audit_path.exists() else "create"
        audit_path.write_text(str(data.get("audit_task_markdown") or ""), encoding="utf-8")
        performed.append({"path": data.get("audit_task_path"), "kind": audit_task_kind, "type": "task_markdown"})
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


REPORT_SNAPSHOT_HEADER_END = "\n---\n"


def report_snapshot_declaration() -> dict[str, Any]:
    """transitions artifact_ingest.verdict.report_snapshot(审计报告快照落点 / 记录类型 / 记录头字段)唯一读取口。缺 = SchemaLoadError。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration

    decl = (_artifact_ingest_declaration().get("verdict") or {}).get("report_snapshot")
    if not isinstance(decl, dict) or not decl.get("location") or not decl.get("record_type") or not decl.get("header_fields"):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.report_snapshot(location/record_type/header_fields)未声明")
    return decl


#: AIPOS-F177: upper bound for waiting out a same-second verdict ID collision with another audit round (seconds)
_VERDICT_ID_COLLISION_WAIT_SECONDS = 2.0


def _settle_verdict_id_collision(
    records: dict[str, Any],
    repo_root: Path,
    *,
    reviewed_task_id: str,
    audit_task_id: str,
    actor_id: str,
    timestamp: str,
    verdict_id: str,
    verdict_path: Path,
) -> tuple[str, str, Path, str | None]:
    """AIPOS-F177 (pre-existing gap exposed by faster loops): verdict IDs keep their format verdict_<reviewed>_<YYYYMMDD_HHMMSS>_<auditor>,
    so two rounds audited by the same instance within one second map to the same record file. When the existing record belongs to
    ANOTHER audit round (its audit_task_id differs), wait until the next whole second and take a fresh ID, at most
    _VERDICT_ID_COLLISION_WAIT_SECONDS; still colliding = fail-closed reason. Same round (or a record whose round cannot be read)
    = returned unchanged, so the existing "already exists" / duplicate-verdict refusals apply as before.
    Returns (timestamp, verdict_id, verdict_path, collision_reason | None)."""
    import time

    def other_round(vid: str) -> bool:
        owners = {str(r.get("audit_task_id") or "") for r in (records.get("audit_verdict_index") or {}).get(vid, [])}
        return bool(owners) and audit_task_id not in owners

    if not verdict_path.exists() or not other_round(verdict_id):
        return timestamp, verdict_id, verdict_path, None
    taken = verdict_id
    deadline = time.monotonic() + _VERDICT_ID_COLLISION_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(max(0.0, 1.0 - (time.time() % 1.0)) + 0.001)  # to the next whole second
        timestamp = iso_z()
        verdict_id = build_runtime_id("verdict", reviewed_task_id, timestamp, actor_id)
        verdict_path = audit_verdict_record_path(repo_root, reviewed_task_id, verdict_id)
        if not verdict_path.exists():
            return timestamp, verdict_id, verdict_path, None
    return timestamp, verdict_id, verdict_path, (
        f"VERDICT_ID_COLLISION: 裁决 ID {verdict_id} 与另一审计轮已落裁决 {taken} 同秒撞名, 等待 {_VERDICT_ID_COLLISION_WAIT_SECONDS:g} 秒后仍被占用"
        "(裁决 ID 秒级粒度); 出口: 稍后重提本轮裁决")


def render_report_snapshot(raw: bytes, *, verdict_id: str, reviewed_task_id: str, audit_task_id: str, source_ref: str) -> tuple[str, str]:
    """AIPOS-F89 件③d: 快照内容唯一构造 = 记录头(声明 header_fields, 经单源 record_writer.render_frontmatter_block)+ 换行 + 报告原文
    逐字节。返回 (内容, 报告原文字节 sha256)。门写快照(_audit_report_snapshot_plan)与 AIPOS-F158 记录类型补齐迁移
    (record_type_migration, 存量无头快照补记录头)同用。"""
    import hashlib

    from tools.aipos_cli.record_writer import render_frontmatter_block

    decl = report_snapshot_declaration()
    original = raw.decode("utf-8")
    sha256 = hashlib.sha256(raw).hexdigest()
    values = {
        "record_type": str(decl["record_type"]),
        "verdict_id": verdict_id,
        "reviewed_task_id": reviewed_task_id,
        "audit_task_id": audit_task_id,
        "report_source_ref": source_ref,
        "report_sha256": sha256,
        "report_bytes": len(raw),
    }
    fields = [str(f) for f in decl["header_fields"]]
    unknown = [f for f in fields if f not in values]
    if unknown:
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.report_snapshot.header_fields 含未知字段 {unknown}")
    header = render_frontmatter_block({f: values[f] for f in fields}, fields)
    return header + "\n" + original, sha256


def _audit_report_snapshot_plan(repo_root: Path, audit_task_id: str, reviewed_task_id: str, verdict_id: str) -> dict[str, Any] | None:
    """AIPOS-F90 件②: 审计报告快照计划(唯一实现)。报告 = 推导核唯一就绪判据 _check_verdict_artifact 命中的文件; 落点/record_type
    读 transitions artifact_ingest.verdict.report_snapshot(缺 = SchemaLoadError)。无报告 = None(裁决照落, 不带快照字段)。

    AIPOS-F89 件③d(2026-10-03 F91 实撞: 原文直拷进 records 缺 record_type 被护栏 B④ 拒): 快照 = 记录头 + 报告原文逐字节,
    构造 = render_report_snapshot(AIPOS-F158 抽出, 存量无头快照迁移同用); sha256 = 报告原文字节的 sha256(= 裁决记录
    report_snapshot_sha256); 复原 = unwrap_report_snapshot(去记录头 + 核 sha256)。"""
    from tools.aipos_cli.next_resolver import _check_verdict_artifact, _resolve_governance_path_with_relative

    decl = report_snapshot_declaration()
    report = _check_verdict_artifact(repo_root, audit_task_id) if audit_task_id else None
    if report is None:
        return None
    raw = report.read_bytes()
    root = repo_root.resolve()
    target = _resolve_governance_path_with_relative(str(decl.get("relative_to") or "records"), repo_root) / str(decl["location"]).format(
        reviewed_task_id=reviewed_task_id, verdict_id=verdict_id)
    try:
        source_ref = str(report.resolve().relative_to(root))
    except ValueError:
        source_ref = str(report.resolve())
    content, sha256 = render_report_snapshot(raw, verdict_id=verdict_id, reviewed_task_id=reviewed_task_id,
                                             audit_task_id=audit_task_id, source_ref=source_ref)
    return {
        "path": str(target.resolve().relative_to(root)),
        "record_type": str(decl["record_type"]),
        "content": content,
        "sha256": sha256,
        "source_ref": source_ref,
    }


def unwrap_report_snapshot(snapshot_text: str) -> str:
    """AIPOS-F89 件③d: 快照复原 = 去掉记录头(首个 frontmatter 块)取报告原文, 并以记录头 report_sha256 核对逐字节一致。
    形不对 / 校验不符 = ValueError(fail-closed)。"""
    import hashlib

    if not snapshot_text.startswith("---\n"):
        raise ValueError("报告快照缺记录头(首行须为 ---)")
    end = snapshot_text.find(REPORT_SNAPSHOT_HEADER_END, 4)
    if end < 0:
        raise ValueError("报告快照记录头未闭合")
    header_meta, _body, _warnings = parse_markdown_frontmatter(snapshot_text[: end + len(REPORT_SNAPSHOT_HEADER_END)])
    original = snapshot_text[end + len(REPORT_SNAPSHOT_HEADER_END):]
    expected = str(header_meta.get("report_sha256") or "")
    actual = hashlib.sha256(original.encode("utf-8")).hexdigest()
    if not expected or actual != expected:
        raise ValueError(f"报告快照原文 sha256 不符(记录头 {expected[:12] or '缺'} ≠ 原文 {actual[:12]})")
    return original


# ---------------------------------------------------------------------------
# AIPOS-F162 件① / AIPOS-F163: 运行证据硬门判据——门侧裁决写入唯一口(_build_audit_verdict_preview)同核;
# 声明 transitions.schema artifact_ingest.verdict.runtime_evidence(唯一), 声明/证据节解析在 artifact_ingest
# ---------------------------------------------------------------------------

def _collapse_ws(text: str) -> str:
    return " ".join(str(text).split())


def _evidence_cd_prefix_re() -> Any:
    import re

    return re.compile(r"^cd\s+(?P<dir>'[^']*'|\"[^\"]*\"|[^\s'\";&|]+)\s*&&\s*(?P<rest>\S.*)$")


def evidence_command_key(command: Any, worktree: Any) -> str:
    """AIPOS-F241 件③(声明 transitions artifact_ingest.verdict.runtime_evidence.command_match): 运行证据命令的比对键(只供
    runtime_evidence_check 调用, 门 / 入门预检 / 推导核同读)。空白折叠后, 若命令形如 `cd <W> && X`、<W> 为绝对路径且规范化(展开 ~、
    解析符号链接与 ..)后 = 该条运行记录的 worktree(run-long 记录即其 --cwd)= 键为 X; 其余一律为空白折叠后的原文(只认这一种前缀等价,
    加参数 / 别的前缀 / cwd 不同仍判不合)。"""
    text = _collapse_ws(command)
    if worktree in (None, ""):
        return text
    match = _evidence_cd_prefix_re().match(text)
    if match is None:
        return text
    target = match.group("dir")
    if target[:1] in ("'", '"'):
        target = target[1:-1]
    where = Path(target).expanduser()
    base = Path(str(worktree)).expanduser()
    if not where.is_absolute() or not base.is_absolute():
        return text
    return match.group("rest") if where.resolve() == base.resolve() else text


def _run_locate_refusal(workspace_root: Path, audit_task_id: str, exc: Exception) -> str:
    """AIPOS-F232 件④: 裁决入口定位运行证据 run 被拒的拒因(前缀按拒因码分, 声明 transitions runtime_evidence.candidate_subject.host_selection
    .verdict_refusals): 主机不符 / 主机未定 = 点名卡面声明主机; 其余 = 声明读不出。声明缺键 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.validation_root import ValidationRootError, audit_evidence_requirement, host_selection_declaration

    texts = host_selection_declaration().get("verdict_refusals")
    needed = ("VR_EVIDENCE_HOST_MISMATCH", "VR_EVIDENCE_HOST_AMBIGUOUS", "unreadable", "host_undeclared")
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) or not texts[k] for k in needed):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.runtime_evidence.candidate_subject.host_selection.verdict_refusals"
                              f"({' / '.join(needed)})未声明齐")
    code = str(getattr(exc, "code", "") or "")
    if code not in ("VR_EVIDENCE_HOST_MISMATCH", "VR_EVIDENCE_HOST_AMBIGUOUS"):
        return str(texts["unreadable"]).format(error=exc)
    try:
        requirement, _card = audit_evidence_requirement(workspace_root, audit_task_id)
        host = str((requirement or {}).get("host") or "").strip() or str(texts["host_undeclared"])
    except ValidationRootError:  # 要求本身形不合 = 主机说不出(拒因原文已含原因)
        host = str(texts["host_undeclared"])
    return str(texts[code]).format(host=host, error=exc)


def _candidate_run_problem(workspace_root: Path, audit_task_id: str, entry: dict[str, Any],
                           subject: dict[str, str]) -> tuple[dict[str, str], str | None]:
    """AIPOS-F199 件①: 固定候选卡一条运行记录(已过必填 / 执行 run 引用 / exit_code 核对)的验证根 run 判据(只供 runtime_evidence_check 调用,
    规则声明 transitions runtime_evidence.candidate_subject.rules)。run 定位 / 归属 / src-verify 核对全部复用 validation_root 唯一实现。
    返回 (该 run 的占位值, 拒因或 None)。"""
    import hashlib
    import posixpath

    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration
    from tools.aipos_cli.validation_root import (
        ValidationRootError, card_kinds, declaration_status, evidence_log_file, is_local_host, locate_run, remote_run_evidence,
        src_verify_evidence_check, task_ownership_problems,
    )
    from tools.aipos_cli.validation_root_helper import SRC_DIR_NAME, canonical_problem, is_within

    run_dir, run_id = str(entry.get("run_dir") or "").strip(), str(entry.get("run_id") or "").strip()
    problem = canonical_problem(run_dir)
    if problem:
        return {}, f"run_dir {problem}"
    try:
        decl_entry, run = locate_run(workspace_root, run_dir, run_id)
    except ValidationRootError as exc:
        return {}, _run_locate_refusal(workspace_root, audit_task_id, exc)
    if decl_entry is None:
        return {}, (f"run_dir {run_dir}(run_id {run_id})不是任何验证根声明的 run 目录(project.json validation_roots; 按声明 path_template 反查不到); "
                    "须为本审计卡声明并 run-start 的 run")
    where = f"run {run_id}(声明 {decl_entry['id']}: 卡 {decl_entry['task_id']} 实例 {decl_entry['instance']} 角色类 {decl_entry['role_class']})"
    # AIPOS-F230 件⑥: 条目声明复用上一轮(reuse.field)= 核被引轮次 / 实例 / 资格(唯一判定 next_resolver.audit_evidence_reuse), 过则按被引声明照常核
    from tools.aipos_cli.next_resolver import evidence_reuse_declaration

    reuse_decl = evidence_reuse_declaration()
    reused_from = str(entry.get(reuse_decl["field"]) or "").strip()
    if reused_from:
        problem = _evidence_reuse_problem(workspace_root, audit_task_id, decl_entry, run_id, reused_from, reuse_decl)
        if problem:
            return {}, f"{where} {problem}"
    elif decl_entry["task_id"] != audit_task_id:
        return {}, f"{where} 是卡 {decl_entry['task_id']} 的 run, 不是本审计卡 {audit_task_id} 的(他卡 run 不构成本卡审计运行证据)"
    if card_kinds().get(decl_entry["role_class"]) != "audit_card":
        return {}, f"{where} 不是审计实例的声明(执行 / 审计隔离)"
    owned = task_ownership_problems(Path(workspace_root), instance=decl_entry["instance"], role_class=decl_entry["role_class"],
                                    task_id=reused_from or audit_task_id)
    if owned:
        return {}, f"{where} 不属本审计实例: {'; '.join(owned)}"
    if declaration_status(decl_entry) == "revoked":
        return {}, f"{where} 所在声明已于 {decl_entry['revoked_at']} 撤销({decl_entry['revoke_reason']}), 其 run 不作运行证据"
    if run is None:
        return {}, f"{where} 未开启(声明 runs 中无 {run_id}; 先由顾问 run-start, 候选 = 卡面固定候选)"
    if run["candidate"] != subject["commit_sha"]:
        return {}, f"{where} 候选 {run['candidate']} ≠ 被审卡卡面固定候选 {subject['commit_sha']}"
    remote = None
    if not is_local_host(decl_entry["host"]):
        # AIPOS-F200 件③: 远端 run 改读审计报告落点副本(evidence-manifest → evidence-fetch 产出), 副本核对过后判据与本机 run 同一(只换读取源)
        try:
            remote = remote_run_evidence(workspace_root, decl_entry, run)
        except (ValidationRootError, OSError, ValueError) as exc:
            return {}, f"{where} 在主机 {decl_entry['host']}(非治理根所在机), 取回副本核对不了(fail-closed): {exc}"
        if not remote["ok"]:
            return {}, (f"{where} 在主机 {decl_entry['host']}(非治理根所在机), 裁决入口读不到 run 内送达核对证据与日志(fail-closed); "
                        f"取回副本 {remote['copy_dir']} 不可用: {'; '.join(remote['problems'])}; {remote['outlet']}")
    src = src_verify_evidence_check(decl_entry, run, candidate=subject["commit_sha"], tree=subject["tree_hash"],
                                    files_root=remote["files_root"] if remote else None)
    if src:
        return {}, f"{where} 源码封签不过: {'; '.join(src)}"
    worktree = str(entry.get("worktree") or "").strip()
    if worktree and not is_within(posixpath.normpath(str(Path(worktree).expanduser())), run_dir):
        return {}, f"{where} 运行目录(worktree / run-long --cwd){worktree} 不在本 run 目录 {run_dir} 内"
    keys = runtime_evidence_declaration()["log_fields"]
    log_path, log_sha = str(entry.get(keys["path"]) or "").strip(), str(entry.get(keys["sha256"]) or "").strip().lower()
    log_file = Path(log_path).expanduser()
    if not log_file.is_absolute():
        return {}, f"{keys['path']} 须为绝对路径, 实为 {log_path}"
    if remote:  # AIPOS-F200: 远端 run 的日志 = 目标主机本 run 内路径, 读取副本 files/<相对路径>
        mapped, problem = evidence_log_file(remote, log_path)
        if problem:
            return {}, f"{where} {problem}"
        log_file = mapped
    try:
        actual = hashlib.sha256(log_file.read_bytes()).hexdigest()
    except OSError as exc:
        return {}, f"日志 {log_path} 不可读: {exc}"
    if actual != log_sha:
        return {}, f"日志 {log_path} sha256={actual[:12]} ≠ 报告自述 {log_sha[:12]}"
    return {"run_dir": run_dir, "src_dir": f"{run_dir}/{SRC_DIR_NAME}", "candidate": subject["commit_sha"],
            "candidate_tree": subject["tree_hash"]}, None


def _evidence_reuse_problem(workspace_root: Path, audit_task_id: str, decl_entry: dict[str, Any], run_id: str, reused_from: str,
                            reuse_decl: dict[str, Any]) -> str | None:
    """AIPOS-F230 件⑥: 复用上一轮运行证据的核对(只供 _candidate_run_problem): ①被引卡 = 本审计卡同一被审卡更早一轮的审计卡; ②run 所在声明
    属被引卡; ③声明实例 = 本审计卡认领实例; ④资格(next_resolver.audit_evidence_reuse 唯一判定)。通过 = None, 否则拒因一句。"""
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter, audit_evidence_reuse
    from tools.aipos_cli.validation_root import _card_instances

    texts = reuse_decl["texts"]
    field = reuse_decl["field"]
    view = audit_evidence_reuse(Path(workspace_root), audit_task_id)
    if reused_from not in view["earlier_rounds"]:
        return texts["not_earlier_round"].format(field=field, value=reused_from, audit_task_id=audit_task_id, reviewed=view["reviewed"] or "?",
                                                 rounds=", ".join(view["earlier_rounds"]) or "(无)")
    if decl_entry["task_id"] != reused_from:
        return texts["decl_mismatch"].format(field=field, value=reused_from, run_id=run_id, decl_task=decl_entry["task_id"])
    path, _q = _find_task_in_queue(Path(workspace_root), audit_task_id)
    instances = _card_instances(_read_frontmatter(path) if path else {})
    if decl_entry["instance"] not in instances:
        return texts["instance_mismatch"].format(run_id=run_id, decl_instance=decl_entry["instance"], instances=sorted(instances) or "(空)")
    if not view["eligible"]:
        return texts["not_eligible"].format(value=reused_from, verdict_id=view["verdict_id"] or "?", scope_note=view["scope_note"] or "",
                                            rework_field=reuse_decl["rework_field"], rework_value=reuse_decl["rework_value"])
    return None


def runtime_evidence_check(workspace_root: Path, audit_task_id: str, audit_fm: dict[str, Any], *,
                           report_path: Path | str | None = None, report_fm: dict[str, Any] | None = None,
                           verdict: str | None = None, reviewed_task_id: str | None = None,
                           reviewed_fm: dict[str, Any] | None = None, commit_sha: str | None = None) -> dict[str, Any] | None:
    """运行证据判据(唯一实现; AIPOS-F163 自 artifact_ingest 下沉到门侧裁决写入唯一口)。None = 不适用或通过; 否则
    {category, reasons}(拒因原文逐条列缺什么 + 出口)。

    调用方(全部同读本函数, 禁第二份): 门 _build_audit_verdict_preview(一切裁决提交路径——artifact ingest 派生的
    audit-verdict --confirm、手敲 audit-verdict [--confirm]、MCP lybra_audit_verdict_dry_run/confirm——预演与确认重放都经此)、
    artifact_ingest._validate_task_artifact(入门前预检, 拒收即不提交)、推导核 next_resolver 审计卡裁决步(loop 让审计体补做)。
    verdict = 门收到的裁决值(缺省读报告 frontmatter); reviewed_task_id / reviewed_fm 缺省按报告或审计卡推导;
    report_path 缺省 = 推导核唯一就绪判据 next_resolver._check_verdict_artifact 命中的报告; commit_sha 缺省读报告。
    不适用: 裁决不在 applies_to_verdicts(FAIL 等不受限) / 审计卡与被审卡都未声明 required=true(此时零额外读盘)。
    审计分配工作树 = next_resolver.card_worktree_location(<审计卡>)(认领建树同一推导); 被审执行卡工作树同一函数(被审卡号)。
    AIPOS-F199 件①: 被审卡交付主体为固定候选(next_resolver.candidate_subject_values 非 None, enums.schema task_mode.candidate_subject)=
    不推导审计工作树, 每条记录绑定审计实例自己的验证根 run(_candidate_run_problem; 声明 runtime_evidence.candidate_subject), 声明命令按该条
    run 的占位值渲染后逐字匹配。其余卡行为不变。AIPOS-F233 件①③: 渲染经占位统一词表(artifact_ingest.render_evidence_command: 别名按被审卡主体
    改写为规范形再代值, 原 F199 旧占位拒收退役); 声明本身错(形不合 / 本主体渲染不了的已知占位; 字面花括号不算, 按字面比对)= declaration_invalid_category + 受限 amend 出口
    (artifact_ingest.evidence_declaration_problem 唯一判据, 推导核同读; loop 立即判声明错归顾问, 不拉起审计体补做)。"""
    import hashlib

    from tools.aipos_cli.artifact_ingest import (
        RuntimeEvidenceDeclarationError, _git_out, parse_runtime_evidence_section, runtime_evidence_command_diff,
        runtime_evidence_declaration, runtime_evidence_guidance, runtime_evidence_requirement,
    )
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id
    from tools.aipos_cli.next_resolver import _check_verdict_artifact, _find_task_in_queue, _read_frontmatter, card_worktree_location
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    decl = runtime_evidence_declaration()
    verdict_value = str(verdict or (report_fm or {}).get("verdict") or "").strip().upper()
    if verdict_value not in {str(v).upper() for v in decl["applies_to_verdicts"]}:
        return None
    workspace_root = Path(workspace_root)
    reviewed = (str(reviewed_task_id or "").strip() or str((report_fm or {}).get("reviewed_task_id") or "").strip()
                or audit_card_reviewed_id(audit_task_id, audit_fm))
    if reviewed_fm is None:
        reviewed_path, _q = _find_task_in_queue(workspace_root, reviewed) if reviewed else (None, None)
        reviewed_fm = _read_frontmatter(reviewed_path) if reviewed_path else {}
    try:
        requirement = runtime_evidence_requirement(audit_fm, reviewed_fm)
    except RuntimeEvidenceDeclarationError as exc:
        return {"category": str(decl["declaration_invalid_category"]),
                "reasons": [f"卡面 {decl['card_field']} 声明不合({exc}); 出口: 顾问 lybra queue amend 修正卡面声明后重推导(fail-closed, 不当未声明放行)"]}
    if requirement is None:
        return None
    from tools.aipos_cli.artifact_ingest import evidence_declaration_problem, render_evidence_command

    declaration_problem = evidence_declaration_problem(workspace_root, audit_task_id, audit_fm, reviewed_fm=reviewed_fm,
                                                       reviewed_task_id=reviewed)
    if declaration_problem is not None:  # AIPOS-F233 件③: 声明命令渲染不了 = 声明错(审计体补做也匹配不上), 给受限 amend 出口
        return {"category": declaration_problem["category"], "reasons": [*declaration_problem["reasons"], declaration_problem["outlet"]],
                "declaration_problem": declaration_problem}
    category = str(decl["rejection_category"])
    heading = str(decl["section_heading"])
    # AIPOS-F199 件①: 按被审卡交付主体分形(enums.schema task_mode.candidate_subject, F196 唯一判据)——固定候选卡(validation 等)与其审计卡
    # 都不建树, 证据绑定审计实例自己的验证根 run(声明 runtime_evidence.candidate_subject); 其余卡照旧核审计分配工作树(行为不变)
    from tools.aipos_cli.artifact_ingest import render_candidate_evidence_command, runtime_evidence_candidate_declaration
    from tools.aipos_cli.next_resolver import CandidateSubjectError, candidate_subject_values

    try:
        subject = candidate_subject_values(workspace_root, reviewed, reviewed_fm or {}) if reviewed else None
    except CandidateSubjectError as exc:
        return {"category": category, "reasons": [f"被审卡 {reviewed} 固定候选不可用, 无从核对验证 run 运行证据(fail-closed): {exc}; "
                                                  "出口: 顾问 lybra queue amend 修正来源卡 / 候选(enums.schema task_mode.candidate_subject)"]}
    cand_decl = runtime_evidence_candidate_declaration() if subject is not None else {}
    if subject is not None:
        outlet = str(cand_decl["outlet"]).format(heading=heading) + runtime_evidence_guidance()
        code_repo = audit_wt = exec_wt = None
    else:
        outlet = (f"出口: 审计体在自己的审计分配工作树重跑卡面声明的验证命令, 按 transitions.schema artifact_ingest.verdict.runtime_evidence.format "
                  f"把每条命令/工作树/退出码/日志写进报告「{heading}」节; 跑不了(环境/时长)= 给 FAIL 或 BLOCK 并写原因, 不得以核执行体原件 hash/退出码代替。"
                  + runtime_evidence_guidance())  # AIPOS-F165 件②: 固定指引(声明命令不可执行 → BLOCK; 严禁提权/改系统凑命令)
        try:
            code_repo, audit_wt = card_worktree_location(workspace_root, audit_task_id, audit_fm)
            exec_wt = card_worktree_location(workspace_root, reviewed, reviewed_fm)[1] if reviewed else None
        except (CardRepoUnresolved, SchemaLoadError, OSError, ValueError) as exc:
            return {"category": category, "reasons": [f"审计分配工作树不可推导, 无从核对运行证据(fail-closed): {exc}"]}
    if report_path is None:
        report_path = _check_verdict_artifact(workspace_root, audit_task_id)
        if report_path is None:
            return {"category": category, "reasons": [
                f"未在声明落点找到 {audit_task_id} 已完成的审计报告(transitions artifact_ingest.verdict 落点与完成判据), 无从核对运行证据; "
                f"卡面要求独立运行证据(来源 {requirement['source']})。{outlet}"]}
    if report_fm is None:
        report_fm = _read_frontmatter(Path(report_path), allow_missing_block=True)
    commit_sha = str(commit_sha or (report_fm or {}).get("commit_sha") or "").strip()
    if subject is None:
        tree_hash = _git_out(code_repo, "rev-parse", f"{commit_sha}^{{tree}}") if commit_sha else None
        values = {"worktree": str(audit_wt), "commit_sha": commit_sha, "tree_hash": tree_hash or ""}
    else:
        values = {}

    def render(command: str) -> str:  # AIPOS-F233: 唯一渲染(别名按卡分支主体规范化后代值; 词表 placeholder_vocabulary)
        return _collapse_ws(render_evidence_command(command, "branch", values))

    def rejected(problems: list[str], missing_commands: list[str]) -> dict[str, Any]:
        # AIPOS-F241 件①②: 拒因原文另带 problems(不含出口的逐条拒因, loop 日志 / 运行记录 / loop status / next 同一原文)、
        # missing_commands(缺 / 不等的声明命令渲染后原文, 同会话补证据指令只补这几条)、outlet; reasons 形不变(= problems + 出口)
        return {"category": category, "reasons": [*problems, outlet], "problems": list(problems),
                "missing_commands": list(missing_commands), "outlet": outlet}

    def all_rendered() -> list[str]:
        if subject is not None:
            return [_collapse_ws(render_candidate_evidence_command(c, {})) for c in requirement["commands"]]
        return [render(c) for c in requirement["commands"]]

    try:
        text = Path(report_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"category": category, "reasons": [f"{report_path}: 报告不可读({exc})"]}
    entries, problem = parse_runtime_evidence_section(text)
    where = f"{report_path}: "
    if entries is None:
        return rejected([where + problem + f"; 卡面要求独立运行证据(来源 {requirement['source']})。"], all_rendered())
    reasons: list[str] = []
    valid_commands: list[str] = []
    valid_runs: list[tuple[str, dict[str, str]]] = []  # AIPOS-F199: 固定候选卡 (实跑命令, 该条 run 的占位值)
    log_keys = decl["log_fields"]
    audit_resolved = Path(audit_wt).resolve() if audit_wt is not None else None
    exec_resolved = Path(exec_wt).resolve() if exec_wt else None
    run_long_key = str((decl.get("run_long") or {}).get("field") or "")
    for index, entry in enumerate(entries, 1):
        label = f"运行记录 #{index}"
        if not isinstance(entry, dict):
            reasons.append(f"{label} 不是对象: {entry!r}")
            continue
        if run_long_key and entry.get(run_long_key) not in (None, ""):
            # AIPOS-F179 item 1: the entry references a `lybra run-long` run; command / worktree / exit code / log come from
            # that record (resolver run_long.evidence_from_record), entry fields given alongside must agree with it
            from tools.aipos_cli.run_long import evidence_from_record

            fields, problem = evidence_from_record(str(entry[run_long_key]))
            if fields is None:
                reasons.append(f"{label} {problem}")
                continue
            # AIPOS-F241 件③: command 比对同一比对键(`cd <记录 --cwd> && X` 与记录命令 X 视为同一条, evidence_command_key)
            clash = [k for k in ("command", "worktree", "exit_code") if entry.get(k) not in (None, "")
                     and (evidence_command_key(entry[k], fields["worktree"]) != evidence_command_key(fields[k], fields["worktree"])
                          if k == "command" else str(entry[k]) != str(fields[k]))]
            if clash:
                sides = "; ".join(f"{k}: 报告写 {_collapse_ws(entry[k])!r} vs 记录 {_collapse_ws(fields[k])!r}" for k in clash)
                reasons.append(f"{label} 字段 {', '.join(clash)} 与引用的 run-long 记录 {entry[run_long_key]} 不一致(以记录为准, 删掉或改对; "
                               f"{sides})")
                continue
            entry = {**{k: v for k, v in entry.items() if k != log_keys["excerpt"]}, **{
                "command": fields["command"], "worktree": fields["worktree"], "exit_code": fields["exit_code"],
                log_keys["path"]: fields["log_path"], log_keys["sha256"]: fields["log_sha256"]}}
            entries[index - 1] = entry
        required_fields = cand_decl["entry_required_fields"] if subject is not None else decl["entry_required_fields"]
        missing = [k for k in required_fields if k not in entry or entry.get(k) in (None, "")]
        if missing:
            reasons.append(f"{label} 缺字段 {', '.join(missing)}"
                           + ("(固定候选卡: 运行记录绑定审计自己的验证根 run, 日志须为文件 + sha256)" if subject is not None else ""))
            continue
        command = str(entry.get("command"))
        label = f"{label}(command={_collapse_ws(command)!r})"
        # AIPOS-F190 件②: 审计不得写或引用被审执行卡的跨机验证 run 目录作为自身运行证据(声明 project.json validation_roots)
        from tools.aipos_cli.validation_root import ValidationRootError, executor_run_dir_refs

        try:
            run_refs = executor_run_dir_refs(workspace_root, reviewed, [command, str(entry.get("worktree") or ""),
                                                                         str(entry.get(log_keys["path"]) or ""),
                                                                         str(entry.get(log_keys["excerpt"]) or ""),
                                                                         str(entry.get("run_dir") or "")])
        except ValidationRootError as exc:
            reasons.append(f"{label} 验证根声明读不出, 无从核对是否引用执行 run 目录(fail-closed): {exc}")
            continue
        if run_refs:
            reasons.append(f"{label} 引用被审执行卡 {reviewed} 的验证 run 目录 {', '.join(run_refs)}(执行 run 的运行 / 产物不构成审计独立"
                           "运行证据; 审计须在本审计卡自己的验证 run 目录重跑)")
            continue
        exit_code = entry.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            reasons.append(f"{label} exit_code 须为整数, 实为 {exit_code!r}")
            continue
        if subject is not None:
            run_values, problem = _candidate_run_problem(workspace_root, audit_task_id, entry, subject)
            if problem:
                reasons.append(f"{label} {problem}")
                continue
            valid_runs.append((evidence_command_key(command, entry.get("worktree")), run_values, entry.get("worktree")))
            continue
        worktree = Path(str(entry.get("worktree"))).expanduser()
        resolved = worktree.resolve() if worktree.is_absolute() else None
        if resolved is None:
            reasons.append(f"{label} worktree 须为绝对路径, 实为 {worktree}")
            continue
        if resolved != audit_resolved:
            if exec_resolved is not None and resolved == exec_resolved:
                reasons.append(f"{label} 执行工作树 {worktree} 是被审执行卡 {reviewed} 的工作树, 不是审计分配工作树 {audit_wt}"
                               "(执行体树上的运行/原件不构成独立核验)")
            else:
                reasons.append(f"{label} 执行工作树 {worktree} ≠ 审计分配工作树 {audit_wt}")
            continue
        excerpt = str(entry.get(log_keys["excerpt"]) or "").strip()
        log_path = str(entry.get(log_keys["path"]) or "").strip()
        log_sha = str(entry.get(log_keys["sha256"]) or "").strip().lower()
        if not excerpt:
            if not log_path or not log_sha:
                reasons.append(f"{label} 缺日志: {log_keys['excerpt']}(摘要)或 {log_keys['path']} + {log_keys['sha256']} 二选一")
                continue
            log_file = Path(log_path).expanduser()
            try:
                actual = hashlib.sha256(log_file.read_bytes()).hexdigest() if log_file.is_absolute() else ""
            except OSError as exc:
                reasons.append(f"{label} 日志 {log_path} 不可读: {exc}")
                continue
            if not actual:
                reasons.append(f"{label} {log_keys['path']} 须为绝对路径, 实为 {log_path}")
                continue
            if actual != log_sha:
                reasons.append(f"{label} 日志 {log_path} sha256={actual[:12]} ≠ 报告自述 {log_sha[:12]}")
                continue
        valid_commands.append(evidence_command_key(command, resolved))  # AIPOS-F241 件③: 比对键(本条 worktree = 审计分配工作树)
    if not entries:
        reasons.append("「独立运行证据」节运行记录为空")
    ran = [_collapse_ws(str(e.get("command"))) for e in entries if isinstance(e, dict) and e.get("command") not in (None, "")]
    ran_keys = {evidence_command_key(e.get("command"), e.get("worktree")) for e in entries
                if isinstance(e, dict) and e.get("command") not in (None, "")}
    missing_commands: list[str] = []
    if subject is not None:  # 差异说明用: 有效 run 的占位值, 都无效时按各条自述 run_dir 试渲染(只作拒因说明, 判据不变)
        from tools.aipos_cli.validation_root_helper import SRC_DIR_NAME

        probes = [v for _got, v, _wt in valid_runs] or [
            {"run_dir": str(e["run_dir"]).rstrip("/"), "src_dir": f"{str(e['run_dir']).rstrip('/')}/{SRC_DIR_NAME}",
             "candidate": subject["commit_sha"], "candidate_tree": subject["tree_hash"]}
            for e in entries if isinstance(e, dict) and str(e.get("run_dir") or "").strip()]
    for declared in requirement["commands"]:
        if subject is not None:  # AIPOS-F199: 每条有效记录按其自身 run 的占位值渲染声明命令, 逐字一致即算该条已运行
            if any(evidence_command_key(render_candidate_evidence_command(declared, run_values), wt) == got
                   for got, run_values, wt in valid_runs):
                continue
            shown = _collapse_ws(render_candidate_evidence_command(declared, probes[0] if probes else {}))  # AIPOS-F233: 无 run 也按规范形展示
            missing_commands.append(shown)
            if shown in ran:  # 命令跑了, 但该条记录不合格(拒因已在上方逐条列出)
                reasons.append(f"卡面声明的命令 {shown!r}(声明原文 {declared!r})只有不合格的运行记录(见上列该条拒因), 不算已运行")
                continue
            reasons.append(f"卡面声明的命令缺有效运行记录: {shown!r}(声明原文 {declared!r}; 须在审计实例自己的验证根 run 运行, 占位按该条 run 渲染后"
                           "命令逐字一致); " + runtime_evidence_command_diff(shown, ran))
            continue
        rendered = render(declared)
        key = evidence_command_key(rendered, audit_resolved)  # AIPOS-F241 件③: `cd <审计分配工作树> && X` 与在该树跑的 X 同一条
        if key not in valid_commands:
            missing_commands.append(rendered)
            if rendered in ran or key in ran_keys:  # 命令跑了, 但该条记录不合格(拒因已在上方逐条列出)
                reasons.append(f"卡面声明的命令 {rendered!r}(声明原文 {declared!r})只有不合格的运行记录(见上列该条拒因), 不算已运行")
                continue
            reasons.append(f"卡面声明的命令缺有效运行记录: {rendered!r}(声明原文 {declared!r}; 须在审计分配工作树运行, 命令逐字一致"
                           "或为 `cd <审计分配工作树> && 命令` 与在该树运行的同一命令); "
                           + runtime_evidence_command_diff(rendered, ran))  # AIPOS-F165 件②: 逐条差异(声明 vs 实跑)
    if not reasons:
        return None
    return rejected([where + r for r in reasons], missing_commands)


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
    artifact_subject: dict[str, Any] | None = None,
    submitted_by: str | None = None,  # AIPOS-F73E 件②: 提交身份(驱动方 token 实例), 记录字段, 不参与 actor 校验
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

    # AIPOS-F70: artifact_subject fail-closed 校验(AIPOS-F197 件③: 适用 = 被审卡写产品仓——按审计卡问唯一判据
    # next_resolver.card_needs_worktree(审计卡跟随被审卡; 被审卡缺 task_mode = 按代码卡读), 写仓 docs 等卡与代码卡同强制; 原按 task_mode == code)
    # 读取被审任务的 task_mode
    reviewed_task_mode = str(reviewed_metadata.get("task_mode") or "").strip()
    # AIPOS-F196 件①: 交付主体为固定候选的被审卡(validation)= 裁决 artifact_subject 必须精确是该候选(唯一读取口 card_candidate_subject)
    from tools.aipos_cli.next_resolver import (CandidateSubjectError, card_candidate_subject, card_governance_outputs, card_needs_worktree,
                                               governance_outputs_recheck)

    try:
        reviewed_candidate = card_candidate_subject(reviewed_metadata)
    except CandidateSubjectError as exc:
        reviewed_candidate = None
        blocking_reasons.append(f"AIPOS-F196: 被审卡固定候选声明不合: {exc}")
    audit_card_view = {**audit_metadata, "task_id": str(audit_metadata.get("task_id") or audit_task.get("task_id") or ""),
                       "task_mode": "audit", "reviewed_task_id": str(reviewed_task.get("task_id") or reviewed_task_id)}
    reviewed_writes_repo = card_needs_worktree(repo_root, audit_card_view)
    reviewed_governance = (reviewed_candidate is None and not reviewed_writes_repo
                           and card_governance_outputs(repo_root, audit_card_view))
    if reviewed_candidate is not None:
        audited = str((artifact_subject or {}).get("commit_sha") or "").strip() if isinstance(artifact_subject, dict) else ""
        if audited != reviewed_candidate["candidate"]:
            blocking_reasons.append(
                f"AIPOS-F196: 被审卡 task_mode={reviewed_task_mode} 的交付主体 = 固定候选 {reviewed_candidate['candidate']}, "
                f"裁决 artifact_subject.commit_sha={audited or '(缺)'} 不符(fail-closed)")
    elif reviewed_governance:
        # AIPOS-F197 件②: 被审卡只写治理根 = 裁决绑交回时产品绑定的产出清单(此刻指纹须 = 交回绑定; 唯一实现 governance_outputs_recheck);
        # 提交方给了 outputs_digest 须与之相等; 裁决记录 artifact_subject = 交回绑定主体(门算, 不信提交值)
        recheck = governance_outputs_recheck(repo_root, str(reviewed_task.get("task_id") or reviewed_task_id), reviewed_metadata,
                                             allow_legacy=True)
        offered = str((artifact_subject or {}).get("outputs_digest") or "").strip() if isinstance(artifact_subject, dict) else ""
        if not recheck["ok"]:
            blocking_reasons.append(f"AIPOS-F197: {recheck['code']}: {recheck['reason']}")
        elif recheck["legacy"]:
            warnings.append(f"AIPOS-F197: {recheck['reason']}")  # 存量交回无绑定: 沿 F70 legacy 口径警告放行(裁决不带产出清单主体)
        elif offered and offered != recheck["bound"]["outputs_digest"]:
            blocking_reasons.append(f"AIPOS-F197: INGEST_OUTPUTS_CHANGED: 裁决 artifact_subject.outputs_digest={offered} ≠ 被审卡交回绑定 "
                                    f"{recheck['bound']['outputs_digest']}(审的必须是交回的那份产出)")
        else:
            artifact_subject = {k: recheck["bound"][k] for k in ("kind", "repository", "outputs_digest", "outputs") if k in recheck["bound"]}
    elif reviewed_writes_repo:
        if not artifact_subject or not isinstance(artifact_subject, dict):
            blocking_reasons.append(
                f"AIPOS-F70: 被审卡写产品仓(task_mode={reviewed_task_mode or 'unspecified'}; 判据 next_resolver.card_needs_worktree)必须提供 "
                "artifact_subject 参数 (repository + commit_sha + tree_hash), 缺失即 BLOCK (fail-closed)"
            )
        else:
            # 校验必需字段
            repository = str(artifact_subject.get("repository") or "").strip()
            commit_sha = str(artifact_subject.get("commit_sha") or "").strip()
            tree_hash = str(artifact_subject.get("tree_hash") or "").strip()
            
            missing_fields = []
            if not repository:
                missing_fields.append("repository")
            if not commit_sha:
                missing_fields.append("commit_sha")
            if not tree_hash:
                missing_fields.append("tree_hash")
            
            if missing_fields:
                blocking_reasons.append(
                    f"AIPOS-F70: artifact_subject 缺少必需字段: {', '.join(missing_fields)}. "
                    "被审卡写产品仓(card_needs_worktree)必须提供完整的 repository + commit_sha + tree_hash"
                )
            else:
                # 校验 commit_sha 和 tree_hash 格式 (40字符十六进制)
                import re
                if not re.fullmatch(r'[0-9a-f]{40}', commit_sha):
                    blocking_reasons.append(
                        f"AIPOS-F70: artifact_subject.commit_sha 格式错误 (应为40字符十六进制): '{commit_sha}'"
                    )
                if not re.fullmatch(r'[0-9a-f]{40}', tree_hash):
                    blocking_reasons.append(
                        f"AIPOS-F70: artifact_subject.tree_hash 格式错误 (应为40字符十六进制): '{tree_hash}'"
                    )
    elif artifact_subject:
        # 不写产品仓的卡提供了 artifact_subject -> 记录但不阻塞(前向兼容)
        warnings.append(
            f"AIPOS-F70: task_mode={reviewed_task_mode or 'unspecified'} 的卡提供了 artifact_subject, "
            "将记录到裁决但不强制要求 (只对写产品仓的被审卡强制, 判据 card_needs_worktree)"
        )

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
                return_record_path = record_dir(repo_root, "returns", reviewed_task_id) / f"{return_record_ref}.md"
                if return_record_path.exists():
                    return_record_content = return_record_path.read_text(encoding="utf-8")
                    return_record_fm, _, _ = parse_markdown_frontmatter(return_record_content)
                    return_record_fm = _normalize_return_value(return_record_fm)
                    reviewed_executor_instance = str(return_record_fm.get("canonical_agent_instance") or "").strip()
            except (OSError, ValueError) as exc:
                # AIPOS-F115 件③: 精确捕获 + 告警可见(原 except Exception: pass 静默落 fallback)
                warnings.append(
                    f"RETURN_RECORD_UNREADABLE: {return_record_ref} 读取/解析失败({exc}), 执行体实例改取 reviewed 卡 executor_completed_by"
                )
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
    
    # AIPOS-F63: PASS类裁决必须带非空证据（evidence_refs 或 findings_summary 至少一项非空）
    if normalized_verdict in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
        from tools.aipos_cli.validation_common import check_evidence_refs_non_empty
        evidence_check = check_evidence_refs_non_empty(evidence_refs, findings_summary)
        blocking_reasons.extend(evidence_check)

    # AIPOS-F163: 运行证据硬门在门侧裁决写入唯一口——一切提交路径(ingest 派生 / 手敲 audit-verdict [--confirm] / MCP 直调;
    # 预演与确认重放)同核唯一判据 runtime_evidence_check; 裁决值取门收到的值, 产物指纹取提交的 artifact_subject(缺省读报告)
    runtime_evidence = runtime_evidence_check(
        repo_root, str(audit_task.get("task_id") or ""), audit_metadata, verdict=normalized_verdict,
        reviewed_task_id=str(reviewed_task.get("task_id") or ""), reviewed_fm=reviewed_metadata,
        commit_sha=str((artifact_subject or {}).get("commit_sha") or "").strip() or None,
    )
    if runtime_evidence is not None:
        blocking_reasons.extend(f"{runtime_evidence['category']}: {reason}" for reason in runtime_evidence["reasons"])
    # AIPOS-F242 件③: 凭据判据同在门侧裁决写入唯一口核(审计报告 / 审计落点 + 随请求提交的 findings_summary; 唯一判据 credential_scan,
    # 经 gate_credential_blocking; 拒因同码同文、不回显值)——ingest 派生 / 手敲 audit-verdict / 门 MCP 直调同核
    from tools.aipos_cli.artifact_ingest import gate_credential_blocking

    blocking_reasons.extend(gate_credential_blocking(
        repo_root, str(audit_task.get("task_id") or ""), kind="verdict", card_fm=audit_metadata,
        extra_texts=[("audit_verdict.findings_summary", str(findings_summary or ""))]))

    timestamp = planned_verdict_at or iso_z()
    verdict_id = planned_verdict_id or build_runtime_id("verdict", str(reviewed_task.get("task_id") or ""), timestamp, canonical_agent_instance or actor)
    verdict_path = audit_verdict_record_path(repo_root, str(reviewed_task.get("task_id") or ""), verdict_id)
    if planned_verdict_id is None:
        # AIPOS-F177 附带修复(既有缺口, 提速暴露): 裁决 ID 秒级粒度, 另一审计轮同一秒已落裁决 = 撞名; 等到下一秒重取(上限
        # _VERDICT_ID_COLLISION_WAIT_SECONDS), 仍撞 = fail-closed 拒因。同一轮重复提交不在此等待, 照旧由下方「已存在 / 幂等第二层」拒。
        timestamp, verdict_id, verdict_path, collision = _settle_verdict_id_collision(
            records, repo_root, reviewed_task_id=str(reviewed_task.get("task_id") or ""), audit_task_id=str(audit_task.get("task_id") or ""),
            actor_id=canonical_agent_instance or actor, timestamp=timestamp, verdict_id=verdict_id, verdict_path=verdict_path)
        if collision:
            blocking_reasons.append(collision)
    root = repo_root.resolve()  # AIPOS-240 (F-o3-19): record paths are .resolve()d; symlink-safe render
    verdict_rel = str(verdict_path.resolve().relative_to(root))
    # AIPOS-F90 件②: 审计报告全文快照(与裁决记录同一批受控落盘; 声明 transitions artifact_ingest.verdict.report_snapshot)
    try:
        report_snapshot = _audit_report_snapshot_plan(repo_root, str(audit_task.get("task_id") or ""),
                                                      str(reviewed_task.get("task_id") or ""), verdict_id)
    except (OSError, UnicodeDecodeError) as exc:
        report_snapshot = None
        blocking_reasons.append(f"REPORT_SNAPSHOT_UNREADABLE: 审计报告不可读, 无法快照进 records(AIPOS-F90 件②): {exc}")
    
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
        record_dir(repo_root, "audit_verdicts", reviewed_task_id_for_verdict)
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
    # AIPOS-F162 件②: 补审卡(卡面 supplementary_of = 被审卡)的裁决 = 补审裁决: 原卡原裁决字段不动, 只写补审两字段
    # (文案读声明 supplementary_audit.status_text; FAIL 类的修复卡号在派生后由 audit_verdict_task 回填)
    supplementary_of = str(audit_metadata.get("supplementary_of") or "").strip()
    is_supplementary = bool(supplementary_of) and supplementary_of == str(reviewed_task.get("task_id") or "")
    if is_supplementary:
        from tools.aipos_cli.audit_derivation import supplementary_audit_declaration, supplementary_audit_status_text

        sup_decl = supplementary_audit_declaration()
        updated_reviewed["supplementary_audit_ref"] = str(audit_task.get("task_id") or "")
        updated_reviewed["supplementary_audit_status"] = (
            supplementary_audit_status_text("pass", audit_task_id=str(audit_task.get("task_id") or ""))
            if normalized_verdict in sup_decl["pass_verdicts"]
            else supplementary_audit_status_text("other", verdict=normalized_verdict, audit_task_id=str(audit_task.get("task_id") or "")))
    else:
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
        artifact_subject=artifact_subject,  # AIPOS-F70: 产物指纹
        submitted_by=submitted_by,  # AIPOS-F73E 件②
        report_snapshot=report_snapshot,  # AIPOS-F90 件②
        supplementary_of=supplementary_of if is_supplementary else None,  # AIPOS-F162 件②
    )
    session_markdown = ""
    session_rel = str(session_path.resolve().relative_to(root)) if session_path else ""  # AIPOS-240: symlink-safe
    if session_path and not session_path.exists():
        # AIPOS-F79D 件④: 审计 session 记录不在声明位 → 不追加事件、不写空文件(F73ER/F78CR 0 字节文件的病根),
        # 出声 warning 带出口(存量错位副本用 lybra state repair --task-id 重铸到声明位)。
        warnings.append(
            f"Audit session record not found at declared position {session_rel}; no session event appended "
            f"(no empty file written). Exit: lybra state lint / lybra state repair --task-id {audit_task.get('task_id')}"
        )
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
        "supplementary_of": supplementary_of if is_supplementary else "",  # AIPOS-F162 件②
        "audit_verdict_record_path": verdict_rel,
        "audit_session_record_path": session_rel,
        "record_writes": [_mcp_record_write_plan(verdict_rel, RecordType.AUDIT_VERDICT_RECORD, would_write=not blocking_reasons)],
        "record_updates": [_mcp_record_write_plan(session_rel, RecordType.SESSION_RECORD, would_update=not blocking_reasons)] if session_markdown else [],
        "record_previews": [
            {"path": verdict_rel, "record_type": RecordType.AUDIT_VERDICT_RECORD, "rendered_markdown": verdict_markdown},
        ] + (
            # AIPOS-F90 件②: 审计报告快照(AIPOS-F89 件③d: 记录头 + 原文逐字节)与裁决记录同批落盘
            [{"path": report_snapshot["path"], "record_type": report_snapshot["record_type"], "rendered_markdown": report_snapshot["content"]}]
            if report_snapshot else []
        ) + (
            # AIPOS-F79D 件④: 只有 session 记录真在声明位(有内容可追加)才进 preview; 禁 0 字节 session 文件
            [{"path": session_rel, "record_type": RecordType.SESSION_RECORD, "rendered_markdown": session_markdown}]
            if session_markdown else []
        ),
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
            "artifact_subject": artifact_subject,
            "submitted_by": submitted_by,
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
        ] + (
            [{"path": report_snapshot["path"], "kind": "create", "type": "record_markdown", "record_type": report_snapshot["record_type"]}]
            if report_snapshot else []
        ),
        planned_moves=[],
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        needs_owner_reasons=[],
        owner_confirmation_required=verdict != Verdict.BLOCK,
        owner_confirmation_reasons=_verdict_owner_reasons() if verdict != Verdict.BLOCK else [],
        safety_notice=CONTROLLED_EXECUTE_NOTICE,
        errors=[],
    )
    
    # AIPOS-F75 件①: dry_run 预览也要包含 fix_derivation 决策
    if normalized_verdict in {"FAIL", "BLOCK"}:
        fix_derivation_mode = _read_fix_derivation_mode(repo_root)
        if fix_derivation_mode == "auto":
            # auto模式会派生,在data中标记
            response["data"]["fix_derivation_mode"] = "auto"
            response["data"]["will_derive_repair_card"] = True
        else:
            # manual模式不派生,明确告知
            response["data"]["fix_derivation_skipped"] = {
                "mode": fix_derivation_mode,
                "message": f"FAIL 裁决已落库,fix_derivation={fix_derivation_mode} 不派生修复卡。请顾问使用 queue_rework 追加返工节到卡面。",
            }
    
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
    claimed_dir = queue_root_for(repo_root) / "claimed"
    if not str(source_path.resolve()).startswith(str(claimed_dir.resolve())):
        return None  # already moved or in unexpected location

    target_path = queue_root_for(repo_root) / "completed" / source_path.name

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


def _supplementary_repair(resolved_root: Path, response: dict[str, Any], data: dict[str, Any], *, reviewed_id: str,
                          audit_task_id: str | None, findings_summary: str | None, actor: str) -> None:
    """AIPOS-F162 件②: 补审裁决入门后的处置(声明 transitions supplementary_audit): FAIL 类 → derive_repair_card_on_fail(supplementary 模式)
    派修复卡, 原卡卡面 supplementary_audit_status 回填「补审未通过·修复卡 X」(经单源 render_task_markdown)。派生失败 = 出声(warnings +
    data.supplementary_repair_error), 裁决记录已落不回滚。"""
    from tools.aipos_cli.audit_derivation import (
        derive_repair_card_on_fail, supplementary_audit_declaration, supplementary_audit_status_text,
    )

    verdict_value = str(data.get("verdict") or "").upper()
    if verdict_value not in supplementary_audit_declaration()["fail_verdicts"]:
        return
    try:
        repair = derive_repair_card_on_fail(
            governance_root=resolved_root, reviewed_task_id=reviewed_id,
            audit_task_id=str(data.get("audit_task_id") or audit_task_id or ""), verdict_id=str(data.get("verdict_id") or ""),
            fail_reason=str(findings_summary or "(no reason provided)")[:500], actor=actor, supplementary_of=reviewed_id,
        )
    except (ValueError, OSError, SchemaLoadError) as exc:
        response.setdefault("warnings", []).append(f"SUPPLEMENTARY_REPAIR_FAILED: 补审 FAIL 后修复卡派生失败: {exc}")
        data["supplementary_repair_error"] = str(exc)
        return
    data["supplementary_repair"] = repair
    if not repair.get("derived"):
        response.setdefault("warnings", []).append(f"SUPPLEMENTARY_REPAIR_NOT_DERIVED: {repair.get('message')}")
        return
    response.setdefault("performed_writes", []).append(
        {"path": repair.get("repair_task_path"), "kind": "create", "type": "derived_repair_task"})
    target = resolved_root / str(data.get("target_path") or "")
    meta, body, _w = parse_markdown_frontmatter(target.read_text(encoding="utf-8"))
    meta = _normalize_return_value(meta)
    meta["supplementary_audit_status"] = supplementary_audit_status_text("fail", repair_task_id=str(repair.get("repair_task_id") or ""))
    target.write_text(render_task_markdown(meta, body), encoding="utf-8")


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
    artifact_subject: dict[str, Any] | None = None,
    submitted_by: str | None = None,  # AIPOS-F73E 件②
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
        
        # AIPOS-R6A 靶子⑧: 记录属主校验 — audit_verdicts 只接受 auditor 角色类
        # (执行体补字段篡改 verdict 实证·.bak 铁证)
        # AIPOS-F132 件①: 角色类经唯一解析 custom_roles.authorize_instance_class(实例 → 角色 → 角色类, 自定义角色读门注册表),
        # 不再按实例名前缀字面量集合判(chris hbj-auditor.* = 注册角色 class auditor 曾被拒); 解析不到 = 拒(fail-closed)。
        resolved_root = _resolve_repo_root(repo_root)
        # AIPOS-F221 件②⑤: 非代码快车道卡(只写治理根、不声明独立审计)交回后由顾问复核, 审计裁决先于角色判定拒——拒因点名复核结案 /
        # 复核打回两条正式出口(唯一判定 next_resolver.fast_lane_audit_refusal, CLI audit-verdict 取凭据前同判)
        from tools.aipos_cli.next_resolver import fast_lane_audit_refusal

        fast_lane_refusal = fast_lane_audit_refusal(resolved_root, reviewed_id)
        if fast_lane_refusal:
            return {
                "verdict": Verdict.BLOCK,
                "task_id": reviewed_id,
                "actor": actor_text,
                "dry_run": dry_run,
                "blocking_reasons": [fast_lane_refusal],
                "warnings": [],
                "data": {"reviewed_task_id": reviewed_id, "redirect_verb": "lybra_mark_concluded"},
                "message": "Audit verdict blocked: fast-lane card is concluded by advisor review (AIPOS-F221)",
            }
        from tools.aipos_cli.custom_roles import authorize_instance_class

        role_gate = authorize_instance_class(instance_text, _AUDIT_VERDICT_SUBMITTER_CLASS, resolved_root)
        if not role_gate["ok"]:
            return {
                "verdict": Verdict.BLOCK,
                "task_id": reviewed_id,
                "actor": actor_text,
                "dry_run": dry_run,
                "blocking_reasons": [
                    f"ROLE_VIOLATION: audit_verdict 只能由角色类 {role_gate['required_class']} 的实例提交"
                    f"(内建角色或门注册表 role_class={role_gate['required_class']} 的自定义角色)。"
                    f"实例 '{instance_text}' 未授权: {role_gate['reason']}。防止执行体/其他角色篡改裁决记录。"
                ],
                "warnings": [],
                "data": {"role_resolution": role_gate},
                "message": "Audit verdict submission blocked: role violation",
            }
        
        frozen_block = _legacy_frozen_block("audit_verdict", resolved_root, [(reviewed_id, None), (audit_task_id, audit_task_path)],
                                            dry_run=dry_run, actor=actor_text)
        if frozen_block is not None:
            return frozen_block
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
            artifact_subject=artifact_subject,
            submitted_by=str(submitted_by or "").strip() or None,
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
            rendered = str(preview.get("rendered_markdown") or "")
            if not rendered.strip():
                # AIPOS-F79D 件④: 禁写 0 字节记录(fail-closed, 不静默落空文件)
                raise RuntimeError(f"拒写 0 字节记录: {preview.get('path')} ({preview.get('record_type')})")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            kind = "update" if preview.get("record_type") == RecordType.SESSION_RECORD else "create"
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
        # AIPOS-F75 件①: 检查 fix_derivation 开关 — 默认 manual (不派生)
        # 从 data 中读取 verdict 字段
        normalized_verdict_value = str(data.get("verdict") or verdict_text or "").upper()
        if data.get("supplementary_of"):
            # AIPOS-F162 件②: 补审裁决 FAIL 类 = 同一 writer 派修复卡(原卡已结案, 不走返工节; 不受 fix_derivation 开关)并回填原卡补审结论
            _supplementary_repair(resolved_root, response, data, reviewed_id=reviewed_id, audit_task_id=audit_task_id,
                                  findings_summary=findings_summary, actor=actor_text)
        elif normalized_verdict_value in {"FAIL", "BLOCK"}:
            # 读取 transitions.schema.json 中的 fix_derivation 声明
            fix_derivation_mode = _read_fix_derivation_mode(resolved_root)
            if fix_derivation_mode == "auto":
                # 只有显式声明 auto 时才派生修复卡
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
            else:
                # manual 模式:不派生,提示使用 queue_rework
                response.setdefault("data", {})["fix_derivation_skipped"] = {
                    "mode": fix_derivation_mode,
                    "message": f"FAIL 裁决已落库,fix_derivation={fix_derivation_mode} 不派生修复卡。请顾问使用 queue_rework 追加返工节到卡面。",
                }
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
            "queue_rework",    # AIPOS-F154: 返工节两阶段
            "audit_dispatch",
            "audit_verdict",
            "orchestration_event_append",
            "planner_iteration_append",
            "intake_submit",
            "owner_decision_record",
            "owner_verification_record",
            "bench_audit_submit",
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
            # AIPOS-F51: inject owner_confirmation_token into mcp_return_metadata for dry_run
            # so self-check waiver is reachable at the same stage as the判据 (not only at confirm).
            if owner_confirmation_token:
                if mcp_return_metadata is None:
                    mcp_return_metadata = {}
                mcp_return_metadata["owner_confirmation_token"] = owner_confirmation_token
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
                ack_in_transit=source_data.get("ack_in_transit") is True,  # AIPOS-F234: 预演所带在途确认原样重放
            )
        elif op == "queue_amend":
            # AIPOS-315: G2 两阶段动词 - amend revalidation
            # AIPOS-F157 件②: 以 dry-run 原始参数重跑同一 amend_task 预览(卡仍 pending / 受限模式卡仍 claimed / 改动值仍合 schema),
            # 快照比对 task_id + original_payload + planned_writes(修订记录路径含时间戳, 不入哈希)——原「跳过 revalidation」的
            # 占位 current 与 dry-run 快照必不等(确认恒 REVALIDATION_FAILED)
            current = amend_task(**amend_replay_kwargs(source_data.get("original_payload") or {}), actor=actor_text, dry_run=True,
                                 repo_root=resolved_root)
        elif op == "queue_rework":
            # AIPOS-F154: 返工节 revalidation = 以 dry-run 原始参数重跑同一 queue_rework_task 预览(卡仍 claimed / 轮次未超限 /
            # 门侧角色类授权仍通过), 快照比对 task_id + original_payload + planned_writes(修订记录路径含时间戳, 不入哈希)
            current = queue_rework_task(**(source_data.get("original_payload") or {}), actor=actor_text, dry_run=True,
                                        repo_root=resolved_root)
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
                artifact_subject=payload.get("artifact_subject"),
                submitted_by=payload.get("submitted_by"),
                dry_run=True,
                repo_root=resolved_root,
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
            result = _build_owner_decision(resolved_root, payload, actor=actor_text, dry_run=False)
            
            # AIPOS-R6M 大项A②: 决策粒度机器触发 - owner_decision_record落库时自动生成decision_log指针条目
            # AIPOS-F158: 指针写出即列入 performed_writes(落账范围以门应答为准, `lybra envelope mint --confirm` 据此一并提交);
            # 写失败不再静默: 拒因进 warnings(决策记录已落, 不回滚)
            performed_writes = list(result.get("planned_writes", [])) if result.get("wrote") else []
            warnings_out = list(result.get("warnings", []))
            if result.get("wrote") and result.get("decision_id"):
                original_payload = result.get("original_payload") or {}
                decision_summary = str(original_payload.get("decision_summary") or "Owner decision")
                decided_by = str(original_payload.get("decided_by_ref") or actor_text or "unknown")
                record_path = str(result.get("target_path") or "")
                
                pointer = _auto_generate_decision_log_pointer(
                    resolved_root,
                    result["decision_id"],
                    decision_summary,
                    decided_by,
                    record_path,
                )
                if pointer.get("path"):
                    performed_writes.append({"path": pointer["path"], "kind": "create", "type": "decision_log_pointer"})
                if pointer.get("warning"):
                    warnings_out.append(pointer["warning"])
            
            verdict = derive_verdict(
                blocking_reasons=list(result.get("blocking_reasons", [])),
                warnings=warnings_out,
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
                performed_writes=performed_writes,
                warnings=warnings_out,
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
            # AIPOS-F49-fix1-fix1: 注入 owner_confirmation_token 到 mcp_return_metadata
            # 修复数据流断裂：owner_confirmation_token 是 execute_dry_run 的参数(4407行),
            # 但 _build_return_preview 的放行逻辑(3066行)从 mcp_return_metadata 取值。
            # 两条道接不上 → 放行分支永远不触发。方案B：调用前注入。
            if owner_confirmation_token:
                if mcp_return_metadata is None:
                    mcp_return_metadata = {}
                mcp_return_metadata["owner_confirmation_token"] = owner_confirmation_token
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
                ack_in_transit=source_data.get("ack_in_transit") is True,  # AIPOS-F234: 与预演同一在途判定与确认
            )
            verdict = str(result.get("verdict") or Verdict.BLOCK)
            # AIPOS-F213 件①: 确认分支写后即经同一薄壳落账(queue_writers withdraw; 原门侧确认不落账)
            landing = land_after_write(resolved_root, queue_write_paths(result), actor=actor_text, subcommand="withdraw",
                                       task_id=str((result.get("data") or {}).get("task_id") or source_data.get("task_id") or ""))
            return attach_landing(make_response(
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
            ), landing)

        if op == "queue_amend":
            # AIPOS-315: G2 两阶段动词 - amend confirm 执行
            # AIPOS-F157 件②: 以令牌计划的原始参数执行同一 amend_task(原取 amendments_to_apply = 字段名列表、reason 未入计划, 必拒)
            result = amend_task(**amend_replay_kwargs(source_data.get("original_payload") or {}), actor=actor_text, dry_run=False,
                                repo_root=resolved_root)
            verdict = str(result.get("verdict") or Verdict.BLOCK)
            # AIPOS-F213 件①: 确认分支写后即经同一薄壳落账(queue_writers amend: 卡面 + 修订记录; 原门侧确认不落账)
            landing = land_after_write(resolved_root, queue_write_paths(result), actor=actor_text, subcommand="amend",
                                       task_id=str((result.get("data") or {}).get("task_id") or ""))
            return attach_landing(make_response(
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
            ), landing)

        if op == "queue_rework":
            # AIPOS-F154: 返工节 confirm 执行 = 同一 queue_rework_task(dry_run=False), 写卡面 rework_rounds + 修订记录(F75 语义)
            result = queue_rework_task(**(source_data.get("original_payload") or {}), actor=actor_text, dry_run=False,
                                       repo_root=resolved_root)
            verdict = str(result.get("verdict") or (Verdict.PASS if result.get("ok") else Verdict.BLOCK))
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
                artifact_subject=payload.get("artifact_subject"),
                submitted_by=payload.get("submitted_by"),
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
                record_performed_writes = _write_mcp_claim_records(
                    resolved_root,
                    record_plan,
                    task_id=str(result.get("task_id") or "")
                )
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
            # AIPOS-F88 件①: 门认领建卡工作树结果进 summary(建树失败 = worktree_error 明确拒因, 不再吞成 warning)
            summary={
                "task_id": result.get("task_id"),
                "moved": result.get("moved", False),
                **{k: result[k] for k in ("worktree_created", "worktree_path", "worktree_branch", "worktree_error",
                                           "worktree_carried_from", "worktree_carried_line") if k in result},
            },
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


def adopt_task(
    *,
    task_id: str,
    branch: str,
    adopted_by: str,
    owner_policy_ref: str,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    autonomy_mode: str = "PreAuthorized",
    confirmer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """AIPOS-F123 件①: 收编人肉期在途卡的门侧后端(声明 transitions nodes.N1.adoption)。

    判据/卡面/建树 = queue_mutation.adopt_queue_task; claim + session 记录 = 认领同一 writer(_mcp_claim_record_plan /
    _write_mcp_claim_records, 记录带 adoption 字段, 报告骨架同一实现 _create_return_skeleton: 已有手写 Return 不覆盖)。
    dry_run=True: 判据 + 记录预览零写入; dry_run=False: 先以预览复核(含记录已存在), 再建树写卡面, 最后落记录。
    信封判定不在此(门工具 lybra_queue_adopt_dry_run 判, 与 claim 同一 matcher)。"""
    operation = "queue_adopt"
    try:
        from tools.aipos_cli.agent_profiles import resolve_instance_id
        from tools.aipos_cli.queue_mutation import adopt_queue_task

        resolved_root = _resolve_repo_root(repo_root)
        profiles = load_agent_profiles(resolved_root)
        preview = adopt_queue_task(resolved_root, task_id=task_id, branch=branch, adopted_by=adopted_by, dry_run=True,
                                   profiles=profiles)
        claimer = str(preview.get("claimer") or "")
        resolution = resolve_instance_id(claimer, profiles) if claimer else {}
        if resolution.get("resolution") == "ambiguous":
            preview["blocking_reasons"].append(f"INSTANCE_AMBIGUOUS: 卡面认领实例 {claimer} 解析不唯一, 收编前须在卡面写具体实例")
            preview["verdict"] = Verdict.BLOCK
        canonical = str(resolution.get("canonical_instance_id") or claimer)
        adoption = {**dict(preview.get("adoption") or {}), "legacy_card_runtime": dict(preview.get("legacy_card_runtime") or {})}
        record_plan: dict[str, Any] | None = None
        if preview.get("verdict") != Verdict.BLOCK:
            updated = dict(preview.get("updated_frontmatter") or {})
            record_plan = _mcp_claim_record_plan(
                repo_root=resolved_root, task_id=str(preview.get("task_id") or task_id), task_path=str(preview.get("target_path") or ""),
                actor=canonical, canonical_agent_instance=canonical, owner_policy_ref=owner_policy_ref, updated_metadata=updated,
                autonomy_mode=autonomy_mode, confirmer=confirmer, adoption=adoption)
            for reason_text in record_plan.get("record_blocking_reasons", []):
                preview["blocking_reasons"].append(reason_text)
                preview["verdict"] = Verdict.BLOCK

        def _respond(result: dict[str, Any], plan: dict[str, Any] | None, *, performed: list[dict[str, Any]], is_dry: bool) -> dict[str, Any]:
            verdict = str(result.get("verdict") or Verdict.BLOCK)
            data = {
                "task_id": result.get("task_id"),
                "card_path": result.get("target_path"),
                "from_state": result.get("from_state"),
                "to_state": result.get("to_state"),
                "claimer": canonical,
                "adoption": adoption,
                "worktree_path": result.get("worktree_path"),
                "worktree_created": result.get("worktree_created", False),
                "updated_frontmatter": result.get("updated_frontmatter"),
                "claim_record_path": (plan or {}).get("claim_record_path"),
                "session_record_path": (plan or {}).get("session_record_path"),
                "record_previews": (plan or {}).get("record_previews", []) if is_dry else [],
                "wrote": bool(result.get("wrote")),
            }
            if result.get("worktree_error"):
                data["worktree_error"] = result["worktree_error"]
            return make_response(
                ok=verdict != Verdict.BLOCK,
                verdict=verdict,
                operation=operation,
                dry_run=is_dry,
                actor=_actor_payload(adopted_by),
                data=data,
                summary={"task_id": result.get("task_id"), "adopted": bool(result.get("wrote")), "worktree_path": result.get("worktree_path")},
                planned_writes=list(result.get("planned_writes", [])) + [
                    {"path": item.get("path"), "kind": "create", "type": "record_markdown", "record_type": item.get("record_type")}
                    for item in (plan or {}).get("record_writes", [])],
                performed_writes=performed,
                warnings=list(result.get("warnings", [])),
                blocking_reasons=list(result.get("blocking_reasons", [])),
                needs_owner_reasons=list(result.get("needs_owner_reasons", [])),
                safety_notice=MUTATION_DRY_RUN_NOTICE if is_dry else CONTROLLED_EXECUTE_NOTICE,
                errors=[],
            )

        if dry_run or preview.get("verdict") == Verdict.BLOCK:
            return _respond(preview, record_plan, performed=[], is_dry=True)
        updated = dict(preview.get("updated_frontmatter") or {})
        executed = adopt_queue_task(resolved_root, task_id=task_id, branch=branch, adopted_by=adopted_by, dry_run=False,
                                    profiles=profiles, claim_id_override=str(updated.get("claim_id") or "") or None,
                                    session_id_override=str(updated.get("active_session_id") or "") or None)
        if not executed.get("wrote"):
            return _respond(executed, record_plan, performed=[], is_dry=False)
        final_plan = _mcp_claim_record_plan(
            repo_root=resolved_root, task_id=str(executed.get("task_id") or task_id), task_path=str(executed.get("target_path") or ""),
            actor=canonical, canonical_agent_instance=canonical, owner_policy_ref=owner_policy_ref,
            updated_metadata=dict(executed.get("updated_frontmatter") or {}), autonomy_mode=autonomy_mode, confirmer=confirmer,
            adoption=adoption)
        if final_plan.get("record_blocking_reasons"):
            executed["blocking_reasons"].extend(final_plan["record_blocking_reasons"])
            executed["verdict"] = Verdict.BLOCK
            return _respond(executed, final_plan, performed=[{"path": executed.get("target_path"), "kind": "update"}], is_dry=False)
        performed = [{"path": executed.get("target_path"), "kind": "update", "type": "task_markdown"}]
        performed += _write_mcp_claim_records(resolved_root, final_plan, task_id=str(executed.get("task_id") or task_id))
        return _respond(executed, final_plan, performed=performed, is_dry=False)
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(adopted_by))


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
        "reviewed_task_id": metadata.get("reviewed_task_id"),  # AIPOS-F90 件①: 审计卡信封判定对象 = 被审卡(autonomy_policy.envelope_subject)
        "lane": metadata.get("lane") if isinstance(metadata.get("lane"), dict) else None,  # AIPOS-F134 件③: 信封 lane 选择器判定对象
    }


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
    from tools.aipos_cli.next_resolver import task_mode_is_audit  # AIPOS-F216 件④: 审计卡识别卡面层单源

    resolved_root = _resolve_repo_root(repo_root)
    converged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for queue_state in ("claimed", "pending"):
        queue_dir = queue_root_for(resolved_root) / queue_state
        if not queue_dir.is_dir():
            continue
        for card_file in sorted(queue_dir.glob("*.md")):
            try:
                text = card_file.read_text(encoding="utf-8")
                fm, body, _ = parse_markdown_frontmatter(text)
                if not isinstance(fm, dict):
                    continue
                task_id = str(fm.get("task_id") or "")
                # Only process audit-derived cards (审计模式 = next_resolver.task_mode_is_audit, AIPOS-F216 件④; or created_by=gate_derivation)
                created_by = str(fm.get("created_by") or "")
                if not task_mode_is_audit(fm.get("task_mode")) and created_by != "gate_derivation":
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
                verdicts_dir = record_dir(resolved_root, "audit_verdicts", reviewed_task_id)
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
                target_path = queue_root_for(resolved_root) / "completed" / card_file.name
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


def _pass_verdict_tip_changed(repo_root: Path, task_id: str, verdict_record: dict[str, Any],
                              card_frontmatter: dict[str, Any] | None = None) -> str | None:
    """AIPOS-F78 前置零⑦: PASS 裁决自述的 artifact_subject.commit_sha 与卡分支当前 tip 不一致 → 返回说明(允许复审); 一致/无法判定 → None。
    AIPOS-F78C: 卡分支所在仓 = 卡声明的仓(card_frontmatter)。
    AIPOS-F112: 卡分支 tip 读取口与推导核裁决过期判据(next_resolver.verdict_staleness)、裁决提交绑 tip 同一函数
    (next_resolver._extract_artifact_subject_from_branch, 分支名读声明 card_branch_name); 原本处自拼分支名 + git rev-parse(第二份读取)删除。"""
    from tools.aipos_cli.next_resolver import _extract_artifact_subject_from_branch, card_branch_name

    subject = verdict_record.get("artifact_subject") if isinstance(verdict_record.get("artifact_subject"), dict) else None
    audited_sha = str((subject or {}).get("commit_sha") or "").strip()
    if not audited_sha:
        return None  # 存量 legacy 裁决无指纹, 维持终态语义
    try:
        product_repo_root = _card_product_repo(repo_root, card_frontmatter)
    except ProductRepoNotConfigured:
        return None
    current = _extract_artifact_subject_from_branch(Path(product_repo_root), task_id)
    tip = str((current or {}).get("commit_sha") or "")
    if not tip or tip == audited_sha:
        return None
    return (
        f"PASS 裁决 {verdict_record.get('verdict_id') or ''} 绑定 {audited_sha[:12]}, 卡分支 {card_branch_name(task_id)} tip 已变为 {tip[:12]}; "
        f"派审类型=复审(re-review), 审计体须对新 tip 重新裁决"
    )


def mark_concluded_task(
    *,
    task_id: str | None = None,
    report_path: str | None = None,
    actor: str | None = None,
    conclusion_note: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    outcome: str | None = None,
    focus_items: Any = None,
    acceptance_criteria: Any = None,
) -> dict[str, Any]:
    """AIPOS-354 S3: 报告式审结 — 显式标记审结.

    AIPOS-F221 件②: outcome = review_rework.outcome(verbs.schema lybra_mark_concluded.review_rework)= 非代码快车道卡顾问复核不通过、
    打回原卡返工(不结案): 落顾问复核记录 + 经门 queue rework 同一写函数 queue_rework_task 追加返工轮(focus_items 必填,
    acceptance_criteria 可选; 只此 outcome 收这两项), 卡留 claimed。实现 _mark_concluded_review_rework。

    AIPOS-F207 件①: outcome = 顾问复核结论 pass | block(值域 card.schema dependency_gate.conclusion.outcomes; 写卡面 outcome_field);
    缺省 = default_outcome 并在 warnings 提示(不按 conclusion_note 判)。依赖判定与门生 closure 等价(task_complexity.closure_state)。
    AIPOS-F209 件②: CLI `lybra mark-concluded` 与 MCP lybra_mark_concluded 同调本函数(唯一写路径, 两入口都收 outcome);
    结案后触发工作树清理(worktree_cleanup.cleanup_after, trigger=conclude), 结果在 data.worktree_cleanup。

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
        # AIPOS-F207 件①: 结论值域与卡面键读 card.schema dependency_gate.conclusion(task_complexity 唯一读取口)
        from tools.aipos_cli.task_complexity import dependency_gate_declaration

        conclusion_decl = dependency_gate_declaration()["conclusion"]
        outcome_text = str(outcome or "").strip().lower()
        outcome_warnings: list[str] = []
        # AIPOS-F221 件②: 复核不通过打回返工 = review_rework.outcome(不是结案结论, 不进 conclusion.outcomes)
        from tools.aipos_cli.next_resolver import review_rework_declaration

        review_rework = outcome_text == str(review_rework_declaration()["outcome"])
        # AIPOS-F230 件④: 同轮补正 = same_round.outcome(不是结案结论, 不登记返工轮; 写路径 _mark_concluded_same_round)
        from tools.aipos_cli.next_resolver import same_round_declaration

        if outcome_text == str(same_round_declaration()["outcome"]):
            return _mark_concluded_same_round(task_id=str(task_id or "").strip(), actor_text=actor_text, report_ref=str(report_path or "").strip(),
                                              note=str(conclusion_note or "").strip(), focus_items=focus_items,
                                              acceptance_criteria=acceptance_criteria, dry_run=dry_run, repo_root=repo_root)
        if outcome_text and not review_rework and outcome_text not in conclusion_decl["outcomes"]:
            raise ValueError(f"--outcome 只认 {list(mark_concluded_outcome_choices())}(得 {outcome!r})")
        if not review_rework and (focus_items is not None or acceptance_criteria is not None):
            raise ValueError(f"--focus-items / --acceptance-criteria 只用于 --outcome {review_rework_declaration()['outcome']}"
                             "(复核不通过打回返工, AIPOS-F221)")
        if not outcome_text:
            outcome_text = str(conclusion_decl["default_outcome"])
            outcome_warnings.append(str(conclusion_decl["default_note"]).format(outcome=outcome_text))
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
        # AIPOS-F78B 件①: 唯一查找 task_loader.find_task_card(frontmatter task_id 匹配; 多义 → AmbiguousTaskCard 由外层 _normalize_exception 出声)
        card_file, queue_state = find_task_card(resolved_root, tid, states=("claimed", "pending"))
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
        # AIPOS-F78 前置零⑨(F79B 实撞): 已 PASS 未 finalize 的卡, 顾问可用 mark-concluded 登记承接世系
        # (conclusion_note 含承接声明), 不再被 FORMAL_VERDICT_EXISTS 挡死; FAIL/BLOCK 仍拒(走 queue_rework)。
        continuation_id = _continuation_from_note(note, resolved_root)
        if existing_verdicts:
            latest_verdict = max(existing_verdicts, key=_verdict_time)
            latest_verdict_value = str(latest_verdict.get("verdict", "")).upper().strip()
            pass_family = latest_verdict_value in {str(Verdict.PASS), str(Verdict.PASS_WITH_NOTES)}
            if pass_family and continuation_id:
                existing_verdicts = []  # 放行: PASS + 承接声明 = 登记承接结案
        if existing_verdicts:
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
        # AIPOS-F197 件②: 只写治理根的卡已经门交回 = 顾问复核结案前核「此刻产出清单指纹 = 交回绑定」(governance_outputs_recheck 唯一实现);
        # 交回后产出被改 / 交回记录无绑定 = 拒(执行体重交回), 复核的必须是交回的那份产出
        from tools.aipos_cli.next_resolver import card_governance_outputs, governance_outputs_recheck

        outputs_check = None
        if (card_governance_outputs(resolved_root, {**fm, "task_id": tid})
                and records.get("task_returns", {}).get(tid)):
            outputs_check = governance_outputs_recheck(resolved_root, tid, fm, allow_legacy=True)
            if not outputs_check["ok"]:
                return blocked_response(
                    operation=operation,
                    dry_run=dry_run,
                    category="VALIDATION_ERROR",
                    message=f"AIPOS-F197: {outputs_check['code']}: 顾问复核结案拒: {outputs_check['reason']}",
                    actor=_actor_payload(actor_text),
                    data={"task_id": tid, "outputs_check": {k: outputs_check.get(k) for k in ("code", "reason", "bound")}},
                )
        if review_rework:
            return _mark_concluded_review_rework(
                resolved_root=resolved_root, tid=tid, queue_state=str(queue_state or ""), fm=fm, actor_text=actor_text,
                report_ref=report_ref, note=note, focus_items=focus_items, acceptance_criteria=acceptance_criteria,
                outputs_check=outputs_check, dry_run=dry_run)
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
                    "target_path": str((queue_root_for(resolved_root) / "completed" / card_file.name).relative_to(resolved_root)),
                    "outcome": outcome_text,
                },
                warnings=outcome_warnings,
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
        fm[str(conclusion_decl["marker_field"])] = str(conclusion_decl["marker_value"])
        fm[str(conclusion_decl["outcome_field"])] = outcome_text  # AIPOS-F207 件①: 依赖判定读此(closure_state)
        if continuation_id:
            fm["continuation_task_id"] = continuation_id  # AIPOS-F78 前置零⑨: 承接世系机器可读(F53 lineage 亦读 conclusion_note)
        rendered = render_task_markdown(fm, body)
        target_path = queue_root_for(resolved_root) / "completed" / card_file.name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
        card_file.unlink()
        # AIPOS-F209 件②: 顾问复核结案同为结案 → 清理本卡工作树与审计工作树(声明 transitions worktree_cleanup.triggers.conclude;
        # 唯一实现 worktree_cleanup.cleanup_after, 与 close / withdraw / supersede 同一判据: 干净删、分支保留、脏不碰只报告)。
        # 失败只进应答与清理记录, 绝不影响结案(cleanup_after 不抛)。
        from tools.aipos_cli.worktree_cleanup import cleanup_after

        worktree_cleanup = cleanup_after(resolved_root, tid, trigger="conclude", actor=actor_text)
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
                "outcome": outcome_text,
                "worktree_cleanup": worktree_cleanup,
            },
            warnings=outcome_warnings,
            safety_notice="AIPOS-354 mark_concluded: card moved to completed/ with machine-readable closure marker.",
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(str(actor or "")))


def mark_concluded_outcome_choices() -> tuple[str, ...]:
    """AIPOS-F221 件②: `lybra mark-concluded --outcome` 的 choices 与 MCP lybra_mark_concluded inputSchema enum 的唯一渲染 = 结案结论值域
    (card.schema dependency_gate.conclusion.outcomes, task_complexity.conclusion_outcomes)+ 复核打回返工(verbs.schema
    lybra_mark_concluded.review_rework.outcome; 不结案, 不进结论值域)。"""
    from tools.aipos_cli.next_resolver import review_rework_declaration, same_round_declaration
    from tools.aipos_cli.task_complexity import conclusion_outcomes

    # AIPOS-F230 件④: + 同轮补正 outcome(verbs.schema lybra_mark_concluded.same_round; 不结案)
    return (*conclusion_outcomes(), str(review_rework_declaration()["outcome"]), str(same_round_declaration()["outcome"]))


def _mark_concluded_same_round(*, task_id: str, actor_text: str, report_ref: str, note: str, focus_items: Any, acceptance_criteria: Any,
                               dry_run: bool, repo_root: str | Path | None) -> dict[str, Any]:
    """AIPOS-F230 件④: 同轮补正写口(声明 verbs.schema lybra_mark_concluded.same_round + transitions nodes.N4.same_round)。前置全过才写:
    卡在 claimed / 有门交回 / 复核者角色类(queue rework 同一判据)/ 该交回未被同轮打回过(next_resolver.same_round_state)/ 审计未开始
    (本交回无裁决; 在途审计卡全在 audit_not_started_states)/ 补正要点非空。写: 顾问复核记录(event, outcome = same_round, 不登记返工轮)+
    本轮未认领审计卡同一操作撤回(queue_mutation.withdraw_linked_audits)。dry_run = 零写入(预演复核记录与撤回清单)。
    AIPOS-F238 件①: 「有门交回」放宽为「有门交回或交回拒收记录」——无门交回(或门最新交回早于最新拒收记录)而有交回拒收记录 = 所审对象是该拒收记录
    (唯一判定 next_resolver.same_round_rejection_subject; 复核记录 reviewed_rejection_ref / 复核时刻产物指纹 / 报告 sha256); 两者都没有才拒
    SAME_ROUND_NO_RETURN。同一所审对象(门交回或拒收记录)的复核未消费 = SAME_ROUND_ALREADY_PENDING。入门判据不放宽(被拒报告不入门)。"""
    from tools.aipos_cli.custom_roles import authorize_instance_class
    from tools.aipos_cli.next_resolver import (_read_task_records, card_delivery_subject, governance_outputs_return_binding,
                                               review_rework_declaration, same_round_declaration, same_round_rejection_subject,
                                               same_round_rejection_values, same_round_state)
    from tools.aipos_cli.queue_mutation import in_flight_audit_cards, withdraw_linked_audits
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic

    operation = "mark_concluded"
    decl = same_round_declaration()
    codes = decl["reject_codes"]

    def _refuse(code: str, **values: Any) -> dict[str, Any]:
        return blocked_response(operation=operation, dry_run=dry_run, category="VALIDATION_ERROR",
                                message=f"AIPOS-F230: {code}: " + str(codes[code]).format(task_id=task_id, **values),
                                actor=_actor_payload(actor_text), data={"task_id": task_id, "code": code, "outcome": decl["outcome"]})

    if not task_id:
        raise ValueError("task_id is required")
    resolved_root = _resolve_repo_root(repo_root)
    card_file, queue_state = find_task_card(resolved_root, task_id)
    if card_file is None or queue_state != "claimed":
        return _refuse("SAME_ROUND_NOT_CLAIMED", state=queue_state or "不在队列")
    fm, _body, _w = parse_markdown_frontmatter(card_file.read_text(encoding="utf-8"))
    fm = fm if isinstance(fm, dict) else {}
    role_gate = authorize_instance_class(actor_text, restricted_amend_actor_class(), resolved_root)
    if not role_gate["ok"]:
        return _refuse("SAME_ROUND_ROLE", required_class=role_gate["required_class"], actor=actor_text, reason=role_gate["reason"])
    records = _read_task_records(resolved_root, task_id)
    return_id = str((records.get("latest_return") or {}).get("return_id") or "").strip()
    # AIPOS-F238 件①: 所审对象 = 入门被拒的交回(无门交回, 或门最新交回早于最新交回拒收记录; 唯一判定 next_resolver.same_round_rejection_subject)
    rejection = same_round_rejection_subject(resolved_root, task_id, records)
    if not return_id and rejection is None:
        return _refuse("SAME_ROUND_NO_RETURN")
    subject_ref = str(rejection.get("rejection_id") or "") if rejection is not None else return_id
    pending = same_round_state(resolved_root, task_id, fm, records)
    if pending is not None:
        review = pending["review"]
        pending_ref = (str(review.get("reviewed_rejection_ref") or "") if pending["subject"] == "rejection"
                       else str(review.get("reviewed_return_record_ref") or ""))
        if pending_ref == subject_ref:  # 同一所审对象(门交回 / 拒收记录)不重复打回
            return _refuse("SAME_ROUND_ALREADY_PENDING", return_id=subject_ref, reviewed_at=review.get("verdict_at") or "?",
                           review_id=review.get("verdict_id") or "?")
    latest_verdict = records.get("latest_verdict") or {}
    if rejection is None and latest_verdict and str(latest_verdict.get("reviewed_return_record_ref") or "") == return_id:
        return _refuse("SAME_ROUND_AUDIT_STARTED", audit_task_id=latest_verdict.get("audit_task_id") or "?",
                       detail=f"本交回已出裁决 {latest_verdict.get('verdict_id') or '?'}")
    in_flight = in_flight_audit_cards(resolved_root, task_id)
    started = [c for c in in_flight if c["state"] not in decl["audit_not_started_states"]]
    if started:
        return _refuse("SAME_ROUND_AUDIT_STARTED", audit_task_id=started[-1]["audit_task_id"],
                       detail=f"审计卡在 {started[-1]['state']}(已认领 = 审计已开始)")
    items = _json_string_list(focus_items)
    if not items:
        return _refuse("SAME_ROUND_FOCUS_REQUIRED", value=repr(focus_items))
    criteria: list[str] = []
    if acceptance_criteria is not None:
        parsed = _json_string_list(acceptance_criteria)
        if parsed is None:
            raise ValueError(f"--acceptance-criteria 须为 JSON 字符串数组(得 {acceptance_criteria!r})")
        criteria = parsed
    withdraw_ids = [c["audit_task_id"] for c in in_flight]
    event_type = str(review_rework_declaration()["event_type"])
    # AIPOS-F232 件⑤ 同一规则: 复核时刻毫秒精度(clock.iso_ms), ID 尾 = 时刻压缩(毫秒段以 _ 连接), 同秒两次复核不撞名
    reviewed_at = iso_ms()
    review_id = f"{event_type}_{task_id}_{file_slug('compact', reviewed_at).replace('.', '_')}"
    subject = card_delivery_subject(resolved_root, task_id, fm)
    binding = dict(governance_outputs_return_binding(resolved_root, task_id) or {}) or {"kind": subject.get("kind")}
    # AIPOS-F238 件①: 所审对象 = 拒收记录时 reviewed_return_record_ref 为空, 另记 rejection_id / 复核时刻产物指纹 / 报告 sha256; 门交回对象这三键为 null
    rejection_values = (same_round_rejection_values(resolved_root, task_id, rejection) if rejection is not None
                        else {"reviewed_rejection_ref": None, "reviewed_fingerprint": None, "reviewed_report_sha256": None})
    values = {"record_type": RecordType.TASK_PROGRESS_EVENT, "event_type": event_type, "task_id": task_id, "review_id": review_id,
              "actor": actor_text, "reviewer_instance": actor_text, "timestamp": reviewed_at, "outcome": str(decl["outcome"]),
              "reviewed_return_record_ref": return_id if rejection is None else None, "artifact_subject": binding,
              "report_path": report_ref or None, "conclusion_note": note or None, "focus_items": items, "acceptance_criteria": criteria,
              "withdrawn_audit_task_id": withdraw_ids[-1] if withdraw_ids else None, **rejection_values}
    meta = {key: values[key] for key in decl["record_fields"]}  # 字段只读声明(声明多出未知字段 = KeyError, fail-closed)
    what = (f"入门被拒的交回 {subject_ref}({rejection.get('category') or '?'}; 拒收记录, 未落门交回)" if rejection is not None
            else f"交回 {return_id}")
    body = (f"# Advisor Review (same round): {task_id}\n\n顾问 {actor_text} 在审计开始前复核{what}, 同轮打回补正(不登记返工轮、"
            f"不计返工上限)" + (f"; 本轮未认领审计卡 {', '.join(withdraw_ids)} 同一操作撤回" if withdraw_ids else "") + "。\n\n"
            + "".join(f"- 补正要点: {x}\n" for x in items) + "".join(f"- 验收: {x}\n" for x in criteria)
            + ("\n卡留 claimed; 执行体按拒因与上列要点修产物后重写交回报告, 照常经产物入口重交(判据不放宽, 被拒报告不入门; pi 卡 loop 同会话续接原执行体)。"
               "判据 transitions nodes.N4.same_round.rejection; " if rejection is not None else
               "\n卡留 claimed; 执行体补正重写交回后重交(审计卡 = 派下一轮审计取代被撤轮; 快车道 = 回到顾问复核)。判据 transitions nodes.N4.same_round; ")
            + "实现 tools/aipos_cli/board_adapter._mark_concluded_same_round。\n")
    record_path = record_dir(resolved_root, "events", task_id) / f"{review_id}.md"
    data: dict[str, Any] = {"task_id": task_id, "from_queue": queue_state, "outcome": str(decl["outcome"]), "moved": False,
                            "review_record": {"review_id": review_id, "path": str(record_path.relative_to(resolved_root)),
                                              "reviewed_return_record_ref": meta.get("reviewed_return_record_ref"),
                                              "subject": "rejection" if rejection is not None else "return",
                                              "reviewed_rejection_ref": meta.get("reviewed_rejection_ref"), "frontmatter": meta}}
    if dry_run:
        data["linked_audits"] = withdraw_linked_audits(resolved_root, task_id, action="same_round", actor=actor_text, dry_run=True,
                                                       detail=f"顾问复核 {review_id}", only=withdraw_ids)
        return make_response(ok=True, verdict=Verdict.PASS, operation=operation, dry_run=True, actor=_actor_payload(actor_text), data=data,
                             warnings=[item["line"] for item in data["linked_audits"]],
                             safety_notice="AIPOS-F230 mark_concluded --outcome same_round dry-run: 复核记录与撤回清单预演, 零写入; 卡留 claimed。")
    markdown = render_markdown(meta, body, list(meta))
    written = write_records_atomic(resolved_root, [("event", review_id, markdown, task_id)])
    data["linked_audits"] = withdraw_linked_audits(resolved_root, task_id, action="same_round", actor=actor_text, dry_run=False,
                                                   detail=f"顾问复核 {review_id}", only=withdraw_ids)
    data["written"] = list(written.get("paths") or []) + linked_audit_write_paths(data["linked_audits"])
    failed = [item["line"] for item in data["linked_audits"] if not item.get("ok")]
    return make_response(ok=True, verdict=Verdict.PASS, operation=operation, dry_run=False, actor=_actor_payload(actor_text), data=data,
                         warnings=[item["line"] for item in data["linked_audits"]],
                         safety_notice=("AIPOS-F230 mark_concluded --outcome same_round: 顾问复核记录已落(不登记返工轮), 卡留 claimed"
                                        + ("; 连带撤回有失败条, 见 warnings 补撤命令" if failed else "") + "; 复核记录随该卡 N6 收账。"))


def _json_string_list(value: Any) -> list[str] | None:
    """AIPOS-F221 件②: --focus-items / --acceptance-criteria 的值(JSON 数组文本或列表)→ 字符串列表; 不是字符串数组 / 有空项 = None。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        return None
    return [x.strip() for x in value]


def _mark_concluded_review_rework(*, resolved_root: Path, tid: str, queue_state: str, fm: dict[str, Any], actor_text: str,
                                  report_ref: str, note: str, focus_items: Any, acceptance_criteria: Any,
                                  outputs_check: dict[str, Any] | None, dry_run: bool) -> dict[str, Any]:
    """AIPOS-F221 件②: 非代码快车道卡顾问复核不通过 → 落顾问复核记录 + 追加返工轮(声明 verbs.schema lybra_mark_concluded.review_rework)。
    前置全过才写(调用方 mark_concluded_task 已核: 卡存在、无门生裁决、交回后产出未改 governance_outputs_recheck): 卡在 claimed、属快车道
    (next_resolver.fast_lane_review)、有门交回记录、该交回未被打回过(fast_lane_rework_state: 已打回且门未落新交回 = 拒)、返工要点非空、
    复核者角色类与轮次上限(custom_roles.authorize_instance_class + queue_mutation.build_rework_round, 与 queue rework 预演同判)。
    写: 复核记录(record_writer.write_records_atomic, 事件记录) → 返工轮(门 queue rework 同一写函数 queue_rework_task, verdict_ref = review_id)。
    dry_run = 零写入(复核记录与返工节预演)。返工轮写失败 = 拒并给补登记命令(复核记录已落, 不回滚: 记录只追加)。"""
    from tools.aipos_cli.custom_roles import authorize_instance_class
    from tools.aipos_cli.next_resolver import (_read_task_records, advisor_review_anchor, fast_lane_review, fast_lane_rework_state,
                                               governance_outputs_return_binding, review_rework_declaration, rework_round_for_verdict)
    from tools.aipos_cli.queue_mutation import build_rework_round
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    operation = "mark_concluded"
    decl = review_rework_declaration()
    codes = decl["reject_codes"]

    def _refuse(code: str, suffix: str = "", **values: Any) -> dict[str, Any]:
        return blocked_response(operation=operation, dry_run=dry_run, category="VALIDATION_ERROR",
                                message=f"AIPOS-F221: {code}: " + str(codes[code]).format(task_id=tid, **values) + suffix,
                                actor=_actor_payload(actor_text), data={"task_id": tid, "code": code, "outcome": decl["outcome"]})

    if queue_state != "claimed":
        return _refuse("REVIEW_REWORK_NOT_CLAIMED", state=queue_state or "?")
    if not fast_lane_review(resolved_root, tid, fm):
        return _refuse("REVIEW_REWORK_NOT_FAST_LANE")
    latest_return = _read_task_records(resolved_root, tid).get("latest_return") or {}
    return_id = str(latest_return.get("return_id") or "").strip()
    if not return_id:
        return _refuse("REVIEW_REWORK_NO_RETURN")
    pending = fast_lane_rework_state(resolved_root, tid, fm)
    if pending is not None and not pending["re_returned"]:
        anchor = pending["anchor"]
        return _refuse("REVIEW_REWORK_ALREADY_PENDING", return_id=anchor.get("reviewed_return_record_ref") or "?",
                       reviewed_at=anchor.get("verdict_at") or "?", review_id=anchor.get("verdict_id") or "?",
                       round=pending["rework"].get("round") or "?")
    items = _json_string_list(focus_items)
    if not items:
        return _refuse("REVIEW_REWORK_FOCUS_REQUIRED", value=repr(focus_items))
    criteria: list[str] = []
    if acceptance_criteria is not None:
        parsed = _json_string_list(acceptance_criteria)
        if parsed is None:
            raise ValueError(f"--acceptance-criteria 须为 JSON 字符串数组(得 {acceptance_criteria!r})")
        criteria = parsed
    event_type = str(decl["event_type"])
    # AIPOS-F232 件⑤(F222R F-1): 复核时刻毫秒精度(clock.iso_ms), ID 尾 = 时刻压缩(毫秒段以 _ 连接)——同秒两次复核不撞名;
    # 秒级存量记录(…_YYYYmmdd_HHMMSS)照旧可读, 读侧按解析后的时刻比较(next_resolver.advisor_reviews / rework_round_for_verdict)
    reviewed_at = iso_ms()
    review_id = f"{event_type}_{tid}_{file_slug('compact', reviewed_at).replace('.', '_')}"
    # AIPOS-F222 件①: 同一交回已有一条无返工轮的复核记录(上次在返工上限处被拒、已升 Owner 收件箱)= 复用它, 不重复记录
    latest_review = advisor_review_anchor(resolved_root, tid)
    reused: str | None = None
    if (latest_review is not None and str(latest_review[1].get("reviewed_return_record_ref") or "") == return_id
            and str(latest_review[1].get("outcome") or "") == str(decl["outcome"])  # AIPOS-F230: 同轮补正记录不当复核打回复用
            and rework_round_for_verdict(fm, latest_review[1]) is None):
        reused = review_id = str(latest_review[1]["verdict_id"])
    role_gate = authorize_instance_class(actor_text, restricted_amend_actor_class(), resolved_root)
    planned = build_rework_round(repo_root=resolved_root, task_id=tid, verdict_ref=review_id, focus_items=items,
                                 acceptance_criteria="\n".join(criteria), actor=actor_text)
    reasons = [str(r) for r in (planned.get("blocking_reasons") or [])]
    if not role_gate["ok"]:
        reasons.insert(0, f"ROLE_VIOLATION: 复核打回(queue rework 同一判据)只能由角色类 {role_gate['required_class']} 的实例执行; "
                          f"实例 '{actor_text}' 未授权: {role_gate['reason']}")
    limit_only = role_gate["ok"] and (planned.get("data") or {}).get("code") == "REWORK_LIMIT"
    if (reasons or planned.get("verdict") == Verdict.BLOCK) and not limit_only:
        return _refuse("REVIEW_REWORK_ROUND_REFUSED", reasons="; ".join(reasons) or "(返工节构建被拒)")
    if limit_only and dry_run:
        return _refuse("REVIEW_REWORK_ROUND_REFUSED", suffix=f"; {decl['escalate_hint']}", reasons="; ".join(reasons))
    round_data = planned.get("data") or {}
    binding = dict((outputs_check or {}).get("bound") or governance_outputs_return_binding(resolved_root, tid) or {})
    values = {"record_type": RecordType.TASK_PROGRESS_EVENT, "event_type": event_type, "task_id": tid, "review_id": review_id,
              "actor": actor_text, "reviewer_instance": actor_text, "timestamp": reviewed_at, "outcome": str(decl["outcome"]),
              "reviewed_return_record_ref": return_id, "artifact_subject": binding, "report_path": report_ref or None,
              "conclusion_note": note or None, "focus_items": items, "acceptance_criteria": criteria,
              "rework_round": round_data.get("round_added")}
    meta = {key: values[key] for key in decl["record_fields"]}  # 字段只读声明(声明多出未知字段 = KeyError, fail-closed)
    body = (f"# Advisor Review (rework): {tid}\n\n顾问 {actor_text} 复核交回 {return_id}(产出清单指纹 "
            f"{binding.get('outputs_digest') or '(交回记录无绑定)'})不通过, 打回原卡第 {round_data.get('round_added')} 轮返工。\n\n"
            + "".join(f"- 返工要点: {x}\n" for x in items) + "".join(f"- 验收: {x}\n" for x in criteria)
            + "\n卡留 claimed 不结案; 执行体按返工节重交回后回到顾问复核。判据 verbs.schema lybra_mark_concluded.review_rework; "
              "实现 tools/aipos_cli/board_adapter._mark_concluded_review_rework。\n")
    if limit_only:  # AIPOS-F222 件①: 仅因上限被拒(非预演)= 照落复核记录(无返工轮)并升 Owner 收件箱返工上限事项
        body = (f"# Advisor Review (rework, limit reached): {tid}\n\n顾问 {actor_text} 复核交回 {return_id}(产出清单指纹 "
                f"{binding.get('outputs_digest') or '(交回记录无绑定)'})不通过; 返工轮次已达上限, 未登记返工轮, 升 Owner 收件箱返工上限事项"
                "(可裁授权本卡追加一轮返工 extend_rework)。\n\n" + "".join(f"- 返工要点: {x}\n" for x in items)
                + "".join(f"- 验收: {x}\n" for x in criteria)
                + "\n判据 verbs.schema lybra_mark_concluded.review_rework.escalation_note; 实现 tools/aipos_cli/board_adapter._mark_concluded_review_rework。\n")
    markdown = render_markdown(meta, body, list(meta))
    record_path = record_dir(resolved_root, "events", tid) / f"{review_id}.md"
    data = {"task_id": tid, "from_queue": queue_state, "outcome": str(decl["outcome"]), "moved": False,
            "review_record": {"review_id": review_id, "path": str((latest_review[0] if reused else record_path).relative_to(resolved_root)),
                              "reviewed_return_record_ref": return_id, "reused": bool(reused),
                              # 复用 = 既有记录原样(不改写); 新记录 = 将写 / 已写的字段
                              "frontmatter": {k: latest_review[1].get(k) for k in decl["record_fields"]} if reused else meta},
            "rework_round": round_data.get("new_round"), "round_added": round_data.get("round_added"),
            "max_rounds": round_data.get("max_rounds"), "owner_extension_ref": round_data.get("owner_extension_ref")}
    if limit_only:
        written_paths = [] if reused else list(write_records_atomic(resolved_root, [("event", review_id, markdown, tid)]).get("paths") or [])
        from tools.aipos_cli.owner_inbox import rework_limit_core

        item = rework_limit_core(resolved_root, tid, fm)
        refused = _refuse("REVIEW_REWORK_ESCALATED", reasons="; ".join(reasons), review_id=review_id, return_id=return_id,
                          item_id=(item or {}).get("item_id") or "?")
        refused["data"] = {**(refused.get("data") or {}), **data, "written": written_paths, "inbox_item_id": (item or {}).get("item_id")}
        return refused
    if dry_run:
        return make_response(ok=True, verdict=Verdict.PASS, operation=operation, dry_run=True, actor=_actor_payload(actor_text), data=data,
                             safety_notice="AIPOS-F221 mark_concluded --outcome rework dry-run: 顾问复核记录与返工节预演, 零写入; 卡留 claimed。")
    written = {"paths": []} if reused else write_records_atomic(resolved_root, [("event", review_id, markdown, tid)])
    rework = queue_rework_task(task_id=tid, verdict_ref=review_id, focus_items=items, acceptance_criteria="\n".join(criteria),
                               actor=actor_text, agent_instance=actor_text, dry_run=False, repo_root=resolved_root)
    if rework.get("verdict") == Verdict.BLOCK or not rework.get("ok", False):
        why = "; ".join(str(r) for r in (rework.get("blocking_reasons") or [])) or str(rework.get("message") or rework.get("errors") or "")
        criteria_json = json.dumps(criteria, ensure_ascii=False)
        return blocked_response(
            operation=operation, dry_run=False, category="VALIDATION_ERROR",
            message=(f"AIPOS-F221: 顾问复核记录已落 {written.get('paths')}, 但返工轮登记被拒: {why}。补登记: "
                     f"{lybra_command_prefix(resolved_root)} queue rework --task-id {tid} --actor {actor_text} --verdict-ref {review_id} "
                     f"--focus-items '{json.dumps(items, ensure_ascii=False)}' --acceptance-criteria '{criteria_json}'"),
            actor=_actor_payload(actor_text), data={**data, "written": written.get("paths")})
    data["written"] = list(written.get("paths") or [])
    data["rework_round"] = (rework.get("data") or {}).get("rework_round") or data["rework_round"]
    data["auto_release"] = (rework.get("data") or {}).get("auto_release")  # AIPOS-F225 件②: 第二档自动追加(记录 ID + 依据)
    data["written"] += [str(w.get("path")) for w in (rework.get("performed_writes") or []) if isinstance(w, dict) and w.get("type") == "auto_release_record"]
    return make_response(ok=True, verdict=Verdict.PASS, operation=operation, dry_run=False, actor=_actor_payload(actor_text), data=data,
                         safety_notice=("AIPOS-F221 mark_concluded --outcome rework: 顾问复核记录已落、返工轮已登记(门 queue rework 同一写函数), "
                                        "卡留 claimed; 复核记录与返工节随该卡 N6 收账。"))


def _continuation_from_note(note: str, governance_root: Path) -> str | None:
    """AIPOS-F78 前置零⑨: 从 conclusion_note 解析承接卡 ID(与 F53 deployment_authorization._find_continuation_task 同一正则口径)。"""
    if not note:
        return None
    import re

    try:
        from tools.aipos_cli.deployment_authorization import _resolve_task_id_pattern

        task_id_pattern = _resolve_task_id_pattern(governance_root)
    except Exception as exc:  # 声明缺失(SchemaLoadError 等): 出声, 用通用卡号形
        import sys

        print(f"Warning: task_id_pattern 声明读取失败, 承接解析用通用形: {exc}", file=sys.stderr)
        task_id_pattern = r"[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9._-]+"
    match = re.search(rf"(?:由.*?({task_id_pattern}).*?承接|承接.*?({task_id_pattern}))", note)
    if match:
        return match.group(1) or match.group(2)
    return None


def _auto_generate_decision_log_pointer(
    repo_root: Path,
    decision_id: str,
    decision_summary: str,
    decided_by: str,
    record_path: str,
    *,
    decided_at: datetime | None = None,
    backfilled_by: str | None = None,
    origin: str | None = None,
    dry_run: bool = False,
) -> dict[str, str | None]:
    """AIPOS-R6M 大项A②: 决策粒度机器触发 - owner_decision_record落库时自动生成decision_log指针条目

    AIPOS-F158: 目录读 config.schema governance_structure.paths.decision_log_dir(schema_loader.resolve_governance_path, 原写死
    governance/decision_log 退役, 缺省值不变); 返回 {"path": 写出的治理根相对路径 | None(同名指针已在 = 跳过), "warning": 写失败拒因 | None}
    (原「静默失败」改为拒因出声, 决策记录照落不回滚)。

    AIPOS-F159 件②(gap #142): 文件名唯一键 = 完整 decision_id(信封决策 = envelope-<policy_id>; 声明 config.schema
    timeline_enforcement.decision_level.path_pattern)。原取 decision_id 首个 `_` 前段, 同日多张信封都成 `envelope-pol` 重名,
    第二张起被「同名已在 = 跳过」吞掉。既有旧名指针不重命名: 读取侧(brief / 缺指针预演 decision_log_pointers)按目录与内容读, 不依赖文件名。

    AIPOS-F160 件③(gap #145): 历史缺指针补生成(`lybra migrate decision-log-pointers --confirm`, decision_log_pointers.run_pointer_backfill)
    复用本函数(禁第二份指针渲染/命名): decided_at = 决策记录的决策时刻(月目录 / 日期 / frontmatter decided_at 随之, 缺省 = 当前 UTC,
    门落库路径不变); backfilled_by = 补生成命令(给出即尾注写明补生成来源, 不冒充落库时生成); dry_run = 只算将写的路径不写盘
    (预演列出指针名同此一处命名)。

    AIPOS-F171 件①: 路线图变更(`lybra roadmap … --confirm`, roadmap.apply_roadmap_change)每次修改留指针, 复用本函数(禁第二份指针
    渲染/命名): record_path = 路线图文件(治理根相对); origin = 生成来源说明(给出即尾注写明来源, 不冒充 owner_decision_record 落库触发)。
    """
    import re

    from tools.schema_loader import resolve_governance_path

    try:
        decision_log_dir = resolve_governance_path("decision_log_dir", repo_root)
        
        # 生成 YYYY-MM 目录(AIPOS-F160: 补生成按决策记录的决策时刻)
        now = utc_now() if decided_at is None else decided_at
        year_month = file_slug("month", now)
        month_dir = decision_log_dir / year_month
        
        # 生成文件名: YYYY-MM-DD-<decision_id>-auto-pointer.md(AIPOS-F159 件②: 唯一键 = 完整 decision_id, 同日多签不重名;
        # decision_id 已按 owner_decision_writer.DECISION_ID_PATTERN 限于文件名安全字符, 此处再收一道防御)
        date_str = file_slug("date", now)
        slug = re.sub(r"[^A-Za-z0-9._-]", "-", str(decision_id))
        filename = f"{date_str}-{slug}-auto-pointer.md"
        pointer_file = month_dir / filename
        
        # 如果文件已存在，跳过
        if pointer_file.exists():
            return {"path": None, "warning": None}
        if dry_run:  # AIPOS-F160: 只算将写的路径(预演), 不写盘
            return {"path": pointer_file.resolve().relative_to(Path(repo_root).resolve()).as_posix(), "warning": None}
        
        # 生成指针内容（固化命名·一句话+decided_by+指向记录路径）
        # AIPOS-F87 件①: frontmatter 经单源 record_writer.render_frontmatter_block(原 f-string 手拼退役)
        from tools.aipos_cli.record_writer import render_frontmatter_block

        pointer_fm = render_frontmatter_block(
            {
                "status": "active",
                "decided_at": now.isoformat().replace('+00:00', 'Z'),
                "decision_type": "auto_pointer",
                "auto_generated": True,
            },
            ["status", "decided_at", "decision_type", "auto_generated"],
        )
        content = pointer_fm + f"""

## {decision_summary}

**Decided by**: {decided_by}

**Record**: `{record_path}`

""" + (f"**Backfilled pointer** (AIPOS-F160): This entry was created later by `{backfilled_by}` for a decision record "
       "that had no decision_log pointer.\n" if backfilled_by else
       f"**Auto-generated pointer** (AIPOS-F171): This entry was automatically created by `{origin}`.\n" if origin else
       "**Auto-generated pointer** (AIPOS-R6M 大项A②): This entry was automatically created when the owner_decision_record was written.\n")
        
        month_dir.mkdir(parents=True, exist_ok=True)
        pointer_file.write_text(content, encoding="utf-8")
        return {"path": pointer_file.resolve().relative_to(Path(repo_root).resolve()).as_posix(), "warning": None}
    except (OSError, ValueError, SchemaLoadError) as exc:
        return {"path": None, "warning": f"decision_log 自动指针未写出({decision_id}): {exc.__class__.__name__}: {exc}"}


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
    # AIPOS-F109 件①: 落点只读声明(record_locations.kinds.fix_closures → fix_card_closure.record.location), 缺 = 拒;
    # 原「声明缺时按写死字面兜底」第二份落点退役(fail-closed)。
    from tools.aipos_cli.record_writer import record_location

    location_tpl = record_location("fix_closures", {"nodes": {"fix_card_closure": {"record": record_decl}}})
    ts_compact = file_slug("compact", str(derived_at or ""))
    record_rel = location_tpl.format(fix_task_id=fix_task_id, timestamp=ts_compact)

    fields = {
        "record_type": RecordType.FIX_CLOSURE_DERIVATION,  # AIPOS-F147 件②: 值域单源 enums.schema record_type
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
    # AIPOS-F87 件①: 经单源 record_writer.render_markdown(safe_dump + 写后回读校验), 原逐行 f"{k}: {v}" 拼接退役
    from tools.aipos_cli.record_writer import render_markdown

    fm_fields: dict[str, Any] = {k: fields.get(k, "") for k in required}
    fm_fields.update({k: v for k, v in fields.items() if k not in required})

    body_text = f"""# Fix Closure Derivation Record: {fix_task_id}

fix卡 close(PASS族)触发 `fix_card_closure` 级联: 为原卡派生复审卡(卡号模式见声明 revision_card_numbering)。

- fix卡: `{fix_task_id}`(裁决 `{verdict_id}`)
- 原卡: `{source_task_id}`
- 派生复审卡: `{derived_audit_task_id}`
- 派生时间: `{derived_at}`
- 声明源: transitions.schema.json `nodes.fix_card_closure`(位置模板/必填字段/门标记均来自该声明)

本记录为门生记录(append-only), 由 queue_close 级联自动写入;手写件会被 sweep 隔离。
"""
    body = render_markdown(fm_fields, body_text, list(fm_fields))
    # AIPOS-F64: 统一写入器
    # AIPOS-F115 件①: 原写法 `("closure", closure_id, body)` 引用本函数未定义的 closure_id(NameError 被调用方吞成
    # 「fix卡复审派生失败」), 且记录类型错写成 N6 closure。改为声明类型 fix_closure_derivation, record_id 取声明位置模板渲染出的文件名。
    from tools.aipos_cli.record_writer import write_records_atomic
    record_id = Path(record_rel).stem
    write_result = write_records_atomic(
        repo_root=resolved_root,
        records=[("fix_closure_derivation", record_id, body, fix_task_id)],  # AIPOS-F109 件①: 显式 key(落点目录 = fix 卡 ID)
    )
    if write_result["paths"][0] != record_rel:
        raise RuntimeError(
            f"fix_closures 记录落点与声明模板不一致: 写入 {write_result['paths'][0]} ≠ 声明 {record_rel}"
        )
    return write_result["paths"][0]


def _n6_landing_next_step(task_id: str, governance_root: Path, submitted_by: str | None, *, preview: bool) -> dict[str, str]:
    """AIPOS-F94 件①: close 应答的 N6 next_step = 产品命令 `lybra governance-commit --task-id`(task 范围精确提交 + push,
    唯一渲染 governance_commit.governance_commit_command); 原手写 git add/commit 文案删除(违「只用产品命令」)。
    actor = 提交身份(驱动方, submitted_by); 未知时占位。lybra loop 驱动时由推导核派生同一命令自动执行。"""
    from tools.aipos_cli.governance_commit import N6_LANDING_ACTOR_PLACEHOLDER, governance_commit_command

    actor = str(submitted_by or "").strip() or N6_LANDING_ACTOR_PLACEHOLDER
    return {
        "audience": "advisor",
        "action": ("任务将 close; 完成后 N6 落账(" if preview else "任务已 close; 待 N6 落账(")
        + "本卡与审计卡的队列文件、记录、卡编年史等 task 范围精确提交并推送治理仓)。lybra loop 驱动时自动执行; 手动推进时执行下面这条",
        "command": governance_commit_command(task_id, actor, governance_root),
    }


def close_task(
    *,
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    closure_evidence: dict[str, Any] | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    conclusion_note: str | None = None,
    submitted_by: str | None = None,
) -> dict[str, Any]:
    """AIPOS-283: gate close verb — move a claimed task to completed/ with closure evidence.

    AIPOS-F73E 件②: actor 校验按 actor==claimer(queue_mutation complete 判据不变); submitted_by=提交身份(驱动方 token
    角色实例, MCP 取 capability, CLI 取工位声明)只记进 closure 记录, 声明在 transitions record_authenticity.submission_identity。

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

        # AIPOS-F252 件②: 门侧兜底——本轮最新交回声明 outcome=blocked(卡面验收未达成)= 拒结案, 出口点名原因
        # (判定只读门交回记录 return_outcome: next_resolver._read_task_records 同一轮界 + return_record_outcome; 缺 = done, 存量不变)
        from tools.aipos_cli.next_resolver import _read_task_records, return_outcome_declaration, return_record_outcome

        latest_return = _read_task_records(resolved_root, resolved_task_id).get("latest_return") or {}
        if latest_return and return_record_outcome(latest_return) == "blocked":
            refusal = str(return_outcome_declaration()["blocked"]["close_refusal"]).format(
                task_id=resolved_task_id, return_id=str(latest_return.get("return_id") or "?"), governance_root=resolved_root)
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="RETURN_OUTCOME_BLOCKED",
                message=refusal,
                actor=_actor_payload(actor_text),
                data={"task_id": resolved_task_id, "return_id": latest_return.get("return_id"), "return_outcome": "blocked"},
                safety_notice="AIPOS-F252 queue_close refuses a blocked return (transitions artifact_ingest.return.outcome.blocked).",
            )

        # Check if already closed (idempotency)
        task_closures = records.get("task_closures", {}).get(resolved_task_id, [])
        # AIPOS-R6S 大项A③: round 序号 — reopen 后的新 round 允许再次 close(旧 round
        # 的 closure 不拦新 round; append-only 历史保留)。AIPOS-F252 件③: 原按卡面 round 判(selected_task 顶层无 round 键, 恒为 1 =
        # reopen 后永远 ALREADY_CLOSED)改为结案口径唯一判定 closure_state(只认轮界之后的 closure 记录): 本轮已结案才拦
        from tools.aipos_cli.task_complexity import closure_state

        if task_closures and closure_state(resolved_root, resolved_task_id)["kind"] == "closure":
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
        timestamp = iso_z()
        from tools.aipos_cli.record_writer import CLOSURE_ID_PREFIX

        closure_id = build_runtime_id(CLOSURE_ID_PREFIX, resolved_task_id, timestamp, actor_text)
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
        # AIPOS-F186 件③: 撤换承继关系(卡面 carried_from, 唯一读取 next_resolver.card_carried_from; 形不合 = 拒结案, fail-closed)
        from tools.aipos_cli.next_resolver import card_carried_from

        closure_carried_from = card_carried_from(selected_task.get("metadata") or fix_card_metadata)

        # AIPOS-R6M 大项A①: 卡粒度机器触发 - close时校验卡编年史存在本卡条目
        # 原'decision_log每卡追加'语义废除(73次无人理的WARN遗迹), decision_log改为只记决策粒度
        # AIPOS-F89 件② M17: 编年史位置只读项目声明 project.json paths.foundation_backlog(唯一读取口 project_paths);
        # 原写死 governance/<lybra 编年史文件名> 删除。未声明 = 跳过编年史校验 + warning(不 BLOCK, 不替项目建文件)。
        governance_warnings: list[str] = []
        foundation_backlog_path = project_paths(resolved_root)["foundation_backlog"]

        # 检查卡编年史是否存在本卡条目 (或closure携excuse_ref豁免)
        backlog_entry_found = False
        excuse_ref = closure_evidence.get("excuse_ref")  # 豁免引用
        auto_generated_backlog_entry = False  # AIPOS-A1 大项B: 机器自动生成标记

        if foundation_backlog_path is None:
            governance_warnings.append(
                "FOUNDATION_BACKLOG_UNDECLARED: 项目未声明 project.json paths.foundation_backlog, 卡编年史校验跳过"
                "(声明后 close 自动校验/生成本卡条目)"
            )
        elif not excuse_ref:
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
                machine_marker = f"<!-- auto-generated by close_task (AIPOS-A1 大项B) at {iso_z()} -->\n"
                entry_with_marker = machine_marker + entry_text
                
                # 追加到项目声明的卡编年史
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
        
        # 保留旧的stage_archive检查作为WARN (不BLOCK)(governance_warnings 已在编年史校验前初始化)
        
        # AIPOS-R6M: stage_archives鲜度检查 (WARN, 不BLOCK - 阶段粒度由转换门票机制执法)
        # AIPOS-F145 件①: 落点经唯一读取口 stage_archive_root(原写死 governance/stage_archives, 与阶段闸门读的目录不一致)
        from tools.aipos_cli.workspace_config import stage_archive_root

        stage_archive_threshold_days = 30
        stage_archive_dir = stage_archive_root(resolved_root)
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
                    "next_step_preview": _n6_landing_next_step(resolved_task_id, resolved_root, submitted_by, preview=True),
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
            submitted_by=str(submitted_by or "").strip() or None,
            carried_from=closure_carried_from,  # AIPOS-F186 件③: 结案记录写撤换承继关系
        )
        closure_path_resolved = resolved_root / closure_path
        
        # AIPOS-F64: 统一写入器
        from tools.aipos_cli.record_writer import write_records_atomic
        write_result = write_records_atomic(
            repo_root=resolved_root,
            records=[("closure", closure_id, closure_markdown)],
        )

        # AIPOS-F78 前置零⑨: close 可写 conclusion_note(含承接声明即登记承接世系, F53 lineage 读此字段)
        note_text = str(conclusion_note or "").strip()
        if note_text:
            completed_rel = str(mutation_result.get("target_path") or "")
            completed_file = resolved_root / completed_rel if completed_rel else None
            if completed_file is not None and completed_file.is_file():
                c_meta, c_body, _c_warn = parse_markdown_frontmatter(completed_file.read_text(encoding="utf-8"))
                c_meta["conclusion_note"] = note_text
                continuation_id = _continuation_from_note(note_text, resolved_root)
                if continuation_id:
                    c_meta["continuation_task_id"] = continuation_id
                completed_file.write_text(render_task_markdown(c_meta, c_body), encoding="utf-8")
            else:
                governance_warnings.append(f"conclusion_note 未写入卡面: 结案后卡路径不可读 ({completed_rel or '?'})")

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
                            # AIPOS-F66: 去除 "lybra" 字面量回落,改用统一解析器
                            # AIPOS-F102 件①: 复审卡认领实例 = 被审卡 audit_by, 缺则按项目推导(resolve_audit_instance 唯一实现)
                            from tools.aipos_cli.audit_derivation import _derive_audit_assigned_to, resolve_audit_instance
                            _reaudit_project = source_task_metadata_for_reaudit.get("project")
                            if not _reaudit_project:
                                try:
                                    _reaudit_project = _resolve_active_project_for(repo_root, None)
                                except (ValueError, FileNotFoundError, OSError):
                                    # 无法解析项目时,不应静默回落到 "lybra",应该报错
                                    blocking_reasons.append(
                                        "PROJECT_UNRESOLVED: reaudit project could not be resolved from source task metadata or workspace"
                                    )
                                    _reaudit_project = "UNRESOLVED"  # 占位,让后续逻辑可以继续但会BLOCK
                            _reaudit_audit_instance = resolve_audit_instance(
                                {**source_task_metadata_for_reaudit, "project": _reaudit_project}, resolved_root)
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
                                "audit": "none",  # AIPOS-F64-fix1 F-5-1: 审计卡不需要再次审计
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
                            reaudit_task_path = queue_root_for(resolved_root) / "pending" / f"{derived_audit_for_source_task.lower()}.md"
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
                audit_target_path = queue_root_for(resolved_root) / "completed" / audit_source_path.name
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

        # AIPOS-F187 件③: closure 落盘且审计卡自动结案后, 清理本卡工作树与审计工作树(干净删、分支保留; 脏不碰只报告;
        # 承继卡结案时一并清原卡树)。失败只进应答与清理记录, 绝不影响结案(cleanup_after 不抛)。
        from tools.aipos_cli.worktree_cleanup import cleanup_after

        worktree_cleanup = cleanup_after(resolved_root, resolved_task_id, trigger="close", actor=actor_text)
        combined_warnings = list(mutation_result.get("warnings", []))
        combined_warnings.extend(governance_warnings)
        # AIPOS-F44A ⑥: Add N6 next_step to close success response
        response_data = {
            "worktree_cleanup": worktree_cleanup,
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
            "next_step": _n6_landing_next_step(resolved_task_id, resolved_root, submitted_by, preview=False),
        }
        return make_response(
            ok=True,
            operation=operation,
            dry_run=False,
            verdict=mutation_result.get("verdict", Verdict.PASS),
            data=response_data,
            warnings=combined_warnings,
            safety_notice="AIPOS-283/289 queue_close completed. Closure record written (append-only)." + (" Card chronicle (project.json paths.foundation_backlog) entry auto-generated (AIPOS-A1)." if auto_generated_backlog_entry else "") + (f" 派生复审卡 {derived_audit_for_source_task}(AIPOS-F18)." if derived_audit_for_source_task else ""),
        )
    except Exception as exc:
        return _normalize_exception(operation, exc, dry_run=dry_run, actor=_actor_payload(actor))


# ---------------------------------------------------------------------------
# AIPOS-F213 件①: 队列写动作写后落账唯一薄壳(CLI / MCP / owner decide 共用; 原 aipos_cli._land_queue_write、
# owner_inbox._land、aipos_cli._queue_write_paths / owner_inbox._release_writes 各一份, 收为本节两个函数)。
# 提交本体 = governance_commit.land_written_paths(F158 写后落账唯一实现), 本节只做「本次写出文件清单 → 治理根相对路径」与
# 「落账声明不可读 = 不落账、出声」两件事。登记: verbs.lybra_governance_commit.post_write_landing(queue_writers / owner_decide /
# queue_write_sites; 棘轮 R5 tests/test_aipos_f213_queue_write_landing.py 对表)。
# ---------------------------------------------------------------------------

def queue_write_paths(result: dict[str, Any] | None) -> list[str]:
    """队列写出口本次写出的文件(治理根相对或绝对, 原样): 预演 / 被拒 / 未写入 = []。两种结果形同读——
    queue_mutation.mutate_queue_task 结果(moved / wrote, source_path / target_path, record_writes / record_updates 中已写者)与
    board_adapter 应答(data 内同名键 + 修订的 task_path / amendment_record_path + 清理记录 worktree_cleanup_record)。"""
    if not isinstance(result, dict) or result.get("dry_run") is True or result.get("ok") is False \
            or result.get("verdict") == Verdict.BLOCK:
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    paths: list[str] = []
    if data.get("moved") or data.get("wrote"):
        paths += [str(data[k]) for k in ("source_path", "target_path") if data.get(k)]
    if data.get("amendment_record_path"):  # amend_task 确认: 卡面 + 修订记录
        paths += [str(data[k]) for k in ("task_path", "amendment_record_path") if data.get(k)]
    if data.get("worktree_cleanup_record"):  # AIPOS-F187: 清理记录随同落账
        paths.append(str(data["worktree_cleanup_record"]))
    paths += linked_audit_write_paths(data.get("linked_audits"))  # AIPOS-F230 件⑤: 连带撤回的审计卡随同落账
    for item in list(result.get("record_writes") or []) + list(result.get("record_updates") or []):
        if isinstance(item, dict) and item.get("path") and (item.get("wrote") or item.get("updated")):
            paths.append(str(item["path"]))
    return list(dict.fromkeys(paths))


def land_after_write(governance_root: str | Path, written: list[str | Path], *, actor: str, command: str = "queue_writers",
                     **fields: Any) -> dict[str, Any] | None:
    """AIPOS-F213 件①: 写后落账唯一薄壳——written(本次写出的文件, 绝对或治理根相对)换算为治理根相对路径, 经
    governance_commit.land_written_paths(command = post_write_landing.commands 的键, fields 填其提交信息模板)精确提交并推送。
    无写出 = None(不落)。落账声明不可读 = 不落账(fail-closed), 写入已生效: 返回 landed False + 拒因(调用方出声, 不回滚已写入)。
    返回 land_written_paths 的结果(含供文本输出的 line)。"""
    from tools.aipos_cli.governance_commit import land_written_paths
    from tools.schema_loader import SchemaLoadError

    if not written:
        return None
    root = Path(governance_root).resolve()
    rels = sorted({(Path(p).resolve().relative_to(root) if Path(p).is_absolute() else Path(p)).as_posix() for p in written})
    try:
        return land_written_paths(root, rels, str(actor), command=command, **fields)
    except SchemaLoadError as exc:
        return {"landed": False, "exit_code": 1, "reason": str(exc), "paths": rels,
                "line": f"⚠ 落账未完成(写入已生效, 勿重复执行写命令): {exc}"}


def landing_view(landing: dict[str, Any] | None) -> dict[str, Any] | None:
    """落账结果的 JSON / 应答视图(去掉供文本输出的 line)。"""
    return None if landing is None else {k: v for k, v in landing.items() if k != "line"}


def attach_landing(response: dict[str, Any], landing: dict[str, Any] | None) -> dict[str, Any]:
    """门侧(MCP / execute_dry_run 确认分支)应答带上落账结果: landing = 视图; 落账未完成 = 落账行进 warnings(不静默; 写入已生效,
    应答 ok 不变——与 CLI 同一处置: 出拒因 + 补账命令, 勿重复执行写命令)。"""
    if landing is None:
        return response
    response["landing"] = landing_view(landing)
    if not landing.get("landed"):
        response["warnings"] = list(response.get("warnings") or []) + [str(landing.get("line") or landing.get("reason"))]
    return response


def withdraw_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    reason: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    owner_confirmation_required_override: bool | None = None,
    owner_confirmation_reasons_override: list[str] | None = None,
    owner_ruling_ref: str | None = None,
    ack_in_transit: bool = False,
) -> dict[str, Any]:
    """AIPOS-315: withdraw a task from pending or claimed queue state.
    AIPOS-F170 件①: 声明须带 Owner 裁决引用的源目录(transitions.schema withdraw.owner_ruling_required_from, 即 blocked)另需
    owner_ruling_ref(决策记录 decision_id; 核验唯一实现 queue_mutation.withdraw_owner_ruling_problem)。
    
    Moves task to withdrawn/ state with reason. Does NOT delete any existing
    records (claims/returns/sessions/audit) - those are preserved.
    
    AIPOS-F234 件①: in-transit protection = queue_mutation.withdraw_in_transit_check (live loop run / launched process, not session
    age), same judgement for dry_run and confirm; ack_in_transit=True (CLI --ack-in-transit / MCP ack_in_transit) passes an in-transit
    card and records the risk confirmation in withdrawal_reason; orphan audit cards (business card withdrawn / completed) are exempt.
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
        
        task = _select_task(resolved_root, task_id=selected_task_id, path=selected_path)

        # AIPOS-F234 件①: 在途保护按真相判(取代 AIPOS-F41 B3「24h 内有会话」且只在预演判): 预演与正式撤回同一判定
        # queue_mutation.withdraw_in_transit_check(本卡有存活 loop 运行 / 执行审计拉起进程 = 在途; 孤儿审计卡不适用; 显式 ack 放行并记风险确认)
        from tools.aipos_cli.loop_run_record import LoopRunRecordError
        from tools.aipos_cli.queue_mutation import withdraw_in_transit_check

        task_meta = task.get("metadata") or {}
        try:
            transit = withdraw_in_transit_check(resolved_root, str(task.get("task_id") or selected_task_id or ""), task_meta,
                                                ack=bool(ack_in_transit))
        except LoopRunRecordError as exc:  # 运行记录读不出 / 探活无权限 = 判不出是否在途 = 拒(fail-closed)
            return blocked_response(operation=operation, dry_run=dry_run, category="STATUS_MISMATCH",
                                    message=f"IN_TRANSIT_UNVERIFIABLE: 判不出卡 {task.get('task_id')} 是否在途(运行记录 / 探活失败, 拒撤回): {exc}",
                                    actor=_actor_payload(actor_text), data={"task_id": task.get("task_id")})
        in_transit_view = {k: transit.get(k) for k in ("in_transit", "live", "lines", "note", "ack_note", "orphan")}
        if transit["blocking_reason"]:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category=str(transit["category"]),
                message=str(transit["blocking_reason"]),
                actor=_actor_payload(actor_text),
                data={"task_id": task.get("task_id"), "error_code": transit["error_code"], "in_transit": in_transit_view,
                      "exits": {"stop_commands": transit.get("stop_commands") or [], "ack_flag": transit["ack_flag"]}},
                safety_notice="AIPOS-F234: in-transit protection by live loop run / launched process truth (same judgement for preview and confirm).",
            )
        requested_reason = str(reason).strip()
        reason = requested_reason + (str(transit["ack_note"]) if transit["ack_note"] else "")  # 卡面 withdrawal_reason 记风险确认

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
            owner_ruling_ref=owner_ruling_ref,
        )
        
        # Build response
        verdict = result.get("verdict", Verdict.BLOCK)
        # AIPOS-F230 件⑤: 业务卡撤回 = 其在途审计卡同一操作连带撤回(审计卡自身撤回不再连带)
        from tools.aipos_cli.audit_derivation import is_audit_card
        from tools.aipos_cli.queue_mutation import withdraw_linked_audits

        withdrawn_id = str(result.get("task_id") or selected_task_id or "")
        link_applies = bool(withdrawn_id) and not is_audit_card(withdrawn_id, task.get("metadata") or None)
        linked = withdraw_linked_audits(resolved_root, withdrawn_id, action="withdraw", actor=actor_text, dry_run=True,
                                        detail=requested_reason) if link_applies and dry_run else []
        
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
                    "reason": requested_reason,  # 两阶段确认重放用原样理由(+ ack_in_transit), 卡面实写 withdrawal_reason
                    "ack_in_transit": bool(ack_in_transit),
                    "withdrawal_reason": reason,
                    "owner_ruling_ref": result.get("owner_ruling_ref"),
                    "linked_audits": linked,  # AIPOS-F230 件⑤: 在途审计卡将随本次撤回一并撤回
                    "in_transit": in_transit_view,  # AIPOS-F234 件①: 在途判定(与确认同一判据)
                },
                summary={"task_id": result.get("task_id"), "to_state": "withdrawn"},
                warnings=list(result.get("warnings", [])) + list(transit["warnings"]) + [item["line"] for item in linked],
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
        
        # AIPOS-F187 件③: 卡进 withdrawn 后清理其工作树 / 审计工作树(干净删、脏不碰; 失败只进输出, 不影响撤回)
        from tools.aipos_cli.worktree_cleanup import cleanup_after

        worktree_cleanup = cleanup_after(resolved_root, str(result.get("task_id") or selected_task_id), trigger="withdraw", actor=actor_text)
        linked = withdraw_linked_audits(resolved_root, withdrawn_id, action="withdraw", actor=actor_text, dry_run=False,
                                        detail=requested_reason, profiles=profiles) if link_applies else []
        return make_response(
            ok=True,
            operation=operation,
            dry_run=False,
            actor=_actor_payload(actor_text),
            data={
                "linked_audits": linked,  # AIPOS-F230 件⑤: 连带撤回的在途审计卡(失败条 ok=false + 补撤命令)
                "task_id": result.get("task_id"),
                "source_path": result.get("source_path"),
                "target_path": result.get("target_path"),
                "from_state": result.get("from_state"),
                "to_state": result.get("to_state"),
                "reason": requested_reason,
                "ack_in_transit": bool(ack_in_transit),
                "withdrawal_reason": reason,
                "owner_ruling_ref": result.get("owner_ruling_ref"),
                "moved": result.get("moved", False),
                "wrote": result.get("wrote", False),
                "worktree_cleanup": worktree_cleanup,
                "worktree_cleanup_record": worktree_cleanup.get("record"),
                "in_transit": in_transit_view,  # AIPOS-F234 件①: 在途判定(与预演同一判据)
            },
            summary={"task_id": result.get("task_id"), "withdrawn": True},
            warnings=list(result.get("warnings", [])) + list(transit["warnings"]) + [item["line"] for item in linked],
            safety_notice="AIPOS-315: task withdrawn, all existing records preserved.",
        )
    except Exception as exc:
        return _normalize_exception("queue_withdraw", exc, dry_run=dry_run, actor=_actor_payload(actor))


def withdraw_orphan_audits(*, actor: str, confirm: bool = False, repo_root: str | Path | None = None) -> dict[str, Any]:
    """AIPOS-F234 件②: 存量孤儿审计卡一次性扫描(lybra queue orphan-audits 唯一实现)。孤儿判定唯一来源 queue_mutation.orphan_audit_cards
    (业务卡已撤回 / 撤换 / 结案而审计卡仍在 linked_audit_withdrawal.states)。缺省 = 只读预演逐张列出(texts.scan_preview);
    confirm = 逐张经撤回唯一实现 withdraw_task(dry_run=False; 卡面 withdrawal_reason = texts.orphan_reason)撤回, 某张失败 = 该条
    ok=False + 拒因 + 单张补撤命令(texts.scan_failed; 不抛, 不影响其余)。不落账: 写出文件 written(queue_write_paths 同一口径)由 CLI
    经 queue_writers withdraw 落账。返回 {ok, confirm, items: [{task_id, state, business, ok, line, written?}], written, lines}。"""
    from tools.aipos_cli.queue_mutation import in_transit_declaration, linked_audit_declaration, orphan_audit_cards

    texts = in_transit_declaration()["texts"]
    root = _resolve_repo_root(repo_root)
    items: list[dict[str, Any]] = []
    written: list[str] = []
    for card in orphan_audit_cards(root):
        fill = {k: card[k] for k in ("task_id", "state", "business", "business_state", "superseded")}
        entry = {k: card[k] for k in ("task_id", "state", "business", "business_state", "superseded_by", "reason", "command")}
        if not confirm:
            items.append({**entry, "ok": True, "planned": True, "line": texts["scan_preview"].format(**fill)})
            continue
        response = withdraw_task(task_id=card["task_id"], actor=actor, reason=card["reason"], dry_run=False, repo_root=root)
        if response.get("ok") and response.get("verdict") != Verdict.BLOCK:
            paths = queue_write_paths(response)
            written += paths
            items.append({**entry, "ok": True, "line": texts["scan_done"].format(**fill), "written": paths})
            continue
        why = "; ".join(str(r) for r in response.get("blocking_reasons") or []) or "; ".join(
            str(e.get("message") or "") for e in response.get("errors") or []) or "被拒"
        items.append({**entry, "ok": False, "line": texts["scan_failed"].format(**fill, reason=why, command=card["command"])})
    lines = [item["line"] for item in items] or [texts["scan_none"].format(states=", ".join(linked_audit_declaration()["states"]))]
    return {"ok": all(item["ok"] for item in items), "confirm": bool(confirm), "items": items, "written": list(dict.fromkeys(written)),
            "lines": lines}


def supersede_task(
    task_id: str,
    *,
    by: str,
    actor: str,
    owner_words: str | None,
    owner_ref: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    carry_branch: bool = False,
    decision_id: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F184 件③(gap #186): 顾问凭 Owner 裁决撤换在途卡(lybra queue supersede 唯一实现)。

    预演(dry_run=True, 零写入): 搬卡预演(queue_mutation.mutate_queue_task supersede: 角色类 / 新卡 / 原话前置 + 卡面校验)+
    决策记录预演(record_owner_decision, payload = owner_inbox.supersede_decision_payload, 与 owner decide 同一构造)。
    确认: 先 execute_dry_run 落 owner_decision_record(含 decision_log 自动指针), 再搬卡(卡面记 superseded_* 与决策 id);
    搬卡被拒 = 决策记录照落(written 照列, 调用方照落账)。不落账(写出文件 written 由 CLI 经 queue_writers 落账)。
    AIPOS-F186 carry_branch=True(`--carry-branch`): 承继判定唯一实现 queue_mutation.supersede_carry_plan(预演列原卡分支 / tip /
    工作树 / 未提交改动计数; 拒因并入 blocking_reasons, 零写入)+ 新卡卡面预演 amend_task(dry_run); 确认 = 决策记录落盘后先经
    amend_task 写新卡 carried_from(修订记录随 written 落账; 已记同一承继 = 幂等不重写)再搬原卡。carry_branch=False = F184 行为不变。
    AIPOS-F213 件②: decision_id = 引用收件箱已有 Owner 裁决(核验唯一实现 owner_inbox.referenced_ruling; 不符 = 拒, 零写入): 不另记
    决策记录(预演 / 确认都不经 record_owner_decision), 卡面 superseded_decision_id = 该裁决, owner_words 缺省取该裁决的 Owner 原话;
    written 只含搬卡 / 承继写出。不带 = 行为不变。
    返回 {ok, verdict, stage, task_id, by, from_state, decision_id, card, decision, carry, blocking_reasons, warnings, written, line,
    carry_line}。"""
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
    from tools.aipos_cli.owner_inbox import referenced_ruling, supersede_decision_payload
    from tools.aipos_cli.queue_mutation import supersede_declaration, supersede_texts

    texts = supersede_texts()
    root = _resolve_repo_root(repo_root)
    actor_text = str(actor or "").strip()
    ref_id = str(decision_id or "").strip() or None
    ref_ruling: dict[str, Any] | None = None
    ref_problems: list[str] = []
    if ref_id:
        ref_ruling, ref_problems = referenced_ruling(root, ref_id, choice=str(supersede_declaration()["decision_choice"]), task_id=task_id,
                                                     action="queue supersede")
        if ref_ruling and not str(owner_words or "").strip():
            owner_words = ref_ruling.get("owner_words") or owner_words
    words = str(owner_words or "").strip()  # 内含换行 = supersede_problems 拒(单行)
    report: dict[str, Any] = {"ok": False, "verdict": Verdict.BLOCK, "stage": "preview" if dry_run else "confirm", "task_id": task_id,
                              "by": str(by or "").strip(), "actor": actor_text, "owner_words": words, "owner_ref": owner_ref,
                              "from_state": None, "decision_id": None, "card": None, "decision": None, "blocking_reasons": [],
                              "warnings": [], "written": [], "line": "", "carry": None, "carry_line": ""}
    profiles = load_agent_profiles(root)
    task = _select_task(root, task_id=task_id, path=None)
    report["task_id"] = str(task.get("task_id") or task_id)
    report["from_state"] = str(task.get("queue_state") or "")
    supersede = {"superseded_by": report["by"], "owner_words": words, "owner_ref": str(owner_ref or "").strip() or None}
    reason = texts["reason_template"].format(by=report["by"], actor=actor_text, owner_words=words)
    card_preview = mutate_queue_task(root, "supersede", task_id=report["task_id"], actor=actor_text, reason=reason, dry_run=True,
                                     profiles=profiles, supersede=supersede)
    report["card"] = {k: card_preview.get(k) for k in ("verdict", "source_path", "target_path", "from_state", "to_state",
                                                       "blocking_reasons", "warnings")}
    report["blocking_reasons"].extend(card_preview.get("blocking_reasons") or [])
    meta = task.get("metadata") or {}
    if meta.get("active_worktree_path") or meta.get("active_worktree_branch"):
        report["warnings"].append(texts["worktree_note"].format(path=meta.get("active_worktree_path") or "-",
                                                                branch=meta.get("active_worktree_branch") or "-"))
    carry_plan: dict[str, Any] | None = None
    carry_amend: dict[str, Any] | None = None
    if carry_branch:
        # AIPOS-F186 件①: 承继判定(唯一实现 queue_mutation.supersede_carry_plan)+ 新卡卡面预演(既有 pending 卡修订唯一实现 amend_task)
        from tools.aipos_cli.queue_mutation import supersede_carry_declaration, supersede_carry_plan, supersede_carry_texts

        carry_decl = supersede_carry_declaration()
        carry_texts = supersede_carry_texts()
        carry_plan = supersede_carry_plan(root, report["task_id"], report["by"])
        report["carry"] = {**(carry_plan.get("carry") or {}), "idempotent": carry_plan["idempotent"]} if carry_plan.get("carry") else None
        report["carry_line"] = carry_plan.get("line") or ""
        report["blocking_reasons"].extend(carry_plan["problems"])
        if carry_plan.get("carried_from") and not carry_plan["problems"] and not carry_plan["idempotent"]:
            carry_amend = {"task_id": report["by"], "actor": actor_text, "amendments": {carry_decl["card_field"]: carry_plan["carried_from"]},
                           "amendment_reason": reason, "amendment_type": carry_decl["amendment_type"], "repo_root": root}
            amend_dry = amend_task(**carry_amend, dry_run=True)
            if not amend_dry.get("ok"):
                report["blocking_reasons"].append(carry_texts["amend_refused"].format(
                    by=report["by"], reason=json.dumps(amend_dry.get("errors") or amend_dry.get("blocking_reasons") or amend_dry.get("message"),
                                                       ensure_ascii=False)[:400]))
    dry: dict[str, Any] = {}
    if ref_id:
        # AIPOS-F213 件②: 引用收件箱已有裁决——核验不符即拒(零写入), 不另记决策记录
        report["blocking_reasons"].extend(ref_problems)
        report["decision_id"] = ref_id
        report["decision"] = {"ok": not ref_problems, "referenced": True, "record": (ref_ruling or {}).get("_path"),
                              "decided_at": (ref_ruling or {}).get("decided_at"), "relayed_by": (ref_ruling or {}).get("relayed_by"),
                              "owner_words": (ref_ruling or {}).get("owner_words"), "blocking_reasons": list(ref_problems)}
    else:
        payload = supersede_decision_payload(root, task_id=report["task_id"], by=report["by"], relayed_by=actor_text, owner_words=words,
                                             owner_ref=supersede["owner_ref"], from_state=report["from_state"], decided_at=iso_z())
        report["decision_id"] = payload["decision_id"]
        dry = record_owner_decision(payload, dry_run=True, repo_root=root, actor="owner")
        report["decision"] = {k: dry.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings", "planned_writes")}
        report["decision"]["target_path"] = (dry.get("data") or {}).get("target_path")
        if dry.get("verdict") == Verdict.BLOCK or not dry.get("ok") or not dry.get("dry_run_token"):
            report["blocking_reasons"].extend(f"决策记录预演被拒: {r}" for r in (dry.get("blocking_reasons") or [])
                                              or [json.dumps(dry.get("errors") or dry.get("message"), ensure_ascii=False)[:400]])
    fill = {"task_id": report["task_id"], "from_state": report["from_state"], "by": report["by"], "decision_id": report["decision_id"]}
    if report["blocking_reasons"]:
        report["line"] = texts["blocked_line"].format(reasons="; ".join(report["blocking_reasons"]))
        return report
    # AIPOS-F230 件⑤: 在途审计卡同一操作连带撤回(唯一实现 queue_mutation.withdraw_linked_audits); 预演列出
    from tools.aipos_cli.queue_mutation import withdraw_linked_audits

    if dry_run:
        report["linked_audits"] = withdraw_linked_audits(root, report["task_id"], action="supersede", actor=actor_text, dry_run=True,
                                                         detail=f"由 {report['by']} 承接")
        report["warnings"].extend(item["line"] for item in report["linked_audits"])
        report.update({"ok": True, "verdict": card_preview.get("verdict") or Verdict.PASS,
                       "line": texts["preview_line_ref" if ref_id else "preview_line"].format(**fill)})
        return report
    if not ref_id:
        done = execute_dry_run(str(dry["dry_run_token"]), "owner", owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=root)
        report["decision"] = {k: done.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings", "performed_writes", "errors")}
        written = [str(w.get("path")) for w in (done.get("performed_writes") or []) if isinstance(w, dict) and w.get("path")]
        report["written"] = list(written)
        if not done.get("ok") or not written:
            report["blocking_reasons"].append("决策记录写入失败(零搬卡): "
                                              + json.dumps(done.get("errors") or done.get("blocking_reasons"), ensure_ascii=False)[:400])
            report["line"] = texts["blocked_line"].format(reasons="; ".join(report["blocking_reasons"]))
            return report
    if carry_amend is not None:
        # AIPOS-F186 件①: 先记新卡承继(carried_from)再搬原卡——承继记录写不成则原卡不搬(决策记录已落照落账)
        amended = amend_task(**carry_amend, dry_run=False)
        if not amended.get("ok"):
            report["blocking_reasons"].append("决策记录已落, 新卡承继记录被拒(原卡未搬): " + carry_texts["amend_refused"].format(
                by=report["by"], reason=json.dumps(amended.get("errors") or amended.get("message"), ensure_ascii=False)[:400]))
            report["line"] = texts["blocked_line"].format(reasons="; ".join(report["blocking_reasons"]))
            return report
        amend_data = amended.get("data") or {}
        report["written"] += [str(root / str(amend_data["amendment_record_path"])), str(root / str(amend_data["task_path"]))]
        report["carry"]["amendment_id"] = amend_data.get("amendment_id")
    moved = mutate_queue_task(root, "supersede", task_id=report["task_id"], actor=actor_text, reason=reason, dry_run=False,
                              profiles=profiles, supersede={**supersede, "decision_id": report["decision_id"]})
    report["card"] = {k: moved.get(k) for k in ("verdict", "source_path", "target_path", "from_state", "to_state", "blocking_reasons",
                                                "warnings", "moved", "wrote")}
    if not moved.get("moved"):
        report["blocking_reasons"].extend(f"决策记录已落, 搬卡被拒: {r}" for r in (moved.get("blocking_reasons") or ["(无拒因原文)"]))
        report["line"] = texts["blocked_line"].format(reasons="; ".join(report["blocking_reasons"]))
        return report
    report["written"] += [str(moved["source_path"]), str(moved["target_path"])]
    report.update({"ok": True, "verdict": Verdict.PASS, "line": texts["done_line_ref" if ref_id else "done_line"].format(**fill)})
    if carry_plan is not None and report["carry"]:
        report["carry_line"] = carry_texts["done_line"].format(
            by=report["by"], amendment_id=report["carry"].get("amendment_id") or carry_texts["idempotent_note"], **carry_plan["carried_from"])
    # AIPOS-F187 件③: 原卡进 withdrawn 后清理其工作树 / 审计工作树(--carry-branch = 新卡 carried_from 引用, 原卡树保留; 失败不影响撤换)
    from tools.aipos_cli.worktree_cleanup import cleanup_after

    report["worktree_cleanup"] = cleanup_after(root, report["task_id"], trigger="supersede", actor=actor_text)
    if report["worktree_cleanup"].get("record"):
        report["written"].append(str(report["worktree_cleanup"]["record"]))
    report["linked_audits"] = withdraw_linked_audits(root, report["task_id"], action="supersede", actor=actor_text, dry_run=False,
                                                     detail=f"由 {report['by']} 承接", profiles=profiles)
    report["written"] += linked_audit_write_paths(report["linked_audits"])
    report["warnings"].extend(item["line"] for item in report["linked_audits"])
    return report


def linked_audit_write_paths(items: list[dict[str, Any]] | None) -> list[str]:
    """AIPOS-F230 件⑤: 连带撤回的审计卡本次写出的文件(源路径删除 + withdrawn 新卡面 + 清理记录), 随业务卡落账。"""
    paths: list[str] = []
    for item in items or []:
        if not item.get("ok") or item.get("planned") or not item.get("target_path"):
            continue
        paths += [str(item["path"]), str(item["target_path"])]
        record = (item.get("worktree_cleanup") or {}).get("record")
        if record:
            paths.append(str(record))
    return paths


def _strip_machine_sections(body: str) -> str:
    """AIPOS-F207: 原卡正文去掉机器区节(## 工作纪律 / 门契约节由发布按新卡重新派生)并把标题降两级, 作承接卡「原卡正文」节。"""
    import re

    from tools.aipos_cli.machine_zone import machine_zone_section_titles

    text = str(body or "")
    for title in machine_zone_section_titles():
        text = re.sub(rf"(?ms)^## 【?{re.escape(title)}.*?(?=^## |\Z)", "", text)
    lines = [("##" + line) if line.startswith("#") else line for line in text.strip().splitlines()]
    return "\n".join(lines).strip()


def correct_closed_task(
    task_id: str,
    *,
    relayed_by: str | None,
    owner_words: str | None,
    reason: str | None = None,
    followup_title: str | None = None,
    owner_ref: str | None = None,
    actor: str = "owner",
    dry_run: bool = True,
    repo_root: str | Path | None = None,
    connection_json: str | None = None,
    decision_id: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F207 件②(gap #216): Owner 纠正已结案卡(`lybra queue correct` 唯一实现; 声明 transitions.schema queue_mutations.correct)。

    预演(零写入): 前置(卡在 from_states / 转达人角色类 custom_roles.authorize_instance_class / 未纠正过 closure_disputes)→ 原结案链
    (disputed_record_kinds 的门生记录 + sha256, 卡面报告引用)→ 决策记录预演(owner_inbox.queue_ruling_payload, 与 owner decide /
    supersede 同一构造与写路径 record_owner_decision)→ 承接卡起草预演(roadmap.followup_draft_payload 唯一起草口 + draft_writer.create_draft
    dry_run)+ 签发实例核验(draft_writer.resolve_publish_signer)→ 路线图标准状态(roadmap_drift.roadmap_status)与承接卡续跑命令
    (owner_inbox._resume_for, loop status 同一模板)。
    确认: execute_dry_run 落决策记录 → 写争议标记事件记录(record_writer.write_records_atomic; 原 closure / 裁决 / 交回 / 卡面不改)→
    create_draft → publish_draft(签发 = 转达人)。发布被拒 = 已写的决策与标记照列 written(调用方照落账), ok False, 给手工发布命令。
    relayed_by / owner_words 缺或多行 = owner_inbox.InboxUsageError(用法错, 与 owner decide 同一校验)。不落账(written 由 CLI 经
    queue_writers 落账)。返回 {ok, verdict, stage, task_id, title, from_state, closed_kind, chain, decision_id, decision, dispute,
    followup, criteria, resume, blocking_reasons, warnings, written, line, publish_error}。
    AIPOS-F213 件②: decision_id = 引用收件箱已有 Owner 纠正裁决(核验唯一实现 owner_inbox.referenced_ruling; 不符 = 拒, 零写入): 不另记
    决策记录, 争议标记 / 承接卡引用该裁决; owner_words 缺省取该裁决的 Owner 原话(relayed_by 仍须给: 承接卡签发实例)。不带 = 行为不变。"""
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
    from tools.aipos_cli.custom_roles import authorize_instance_class
    from tools.aipos_cli.draft_writer import resolve_publish_signer
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.owner_inbox import _resume_for, queue_ruling_payload, referenced_ruling, relay_fields
    from tools.aipos_cli.queue_mutation import correct_declaration, correct_texts
    from tools.aipos_cli.record_writer import gate_record_files, render_markdown, write_records_atomic
    from tools.aipos_cli.roadmap import _fix_task_id, followup_draft_payload
    from tools.aipos_cli.task_complexity import closure_disputes, closure_state, dispute_event_type
    from tools.aipos_cli.task_loader import find_task_card

    decl = correct_declaration()
    texts = correct_texts()
    fu = decl["followup"]
    root = _resolve_repo_root(repo_root)
    ref_id = str(decision_id or "").strip() or None
    ref_ruling: dict[str, Any] | None = None
    ref_problems: list[str] = []
    if ref_id:  # AIPOS-F213 件②: 引用收件箱已有裁决(核验不符 = 拒, 零写入); Owner 原话缺省取自该裁决
        ref_ruling, ref_problems = referenced_ruling(root, ref_id, choice=str(decl["decision_choice"]), task_id=task_id,
                                                     action="queue correct")
        if ref_ruling and not str(owner_words or "").strip():
            owner_words = ref_ruling.get("owner_words") or owner_words
    if ref_problems:
        relayed, words = str(relayed_by or "").strip(), str(owner_words or "").strip()
    else:
        relayed, words = relay_fields(relayed_by, owner_words)
    reason_text = " ".join(str(reason or "").split())
    report: dict[str, Any] = {"ok": False, "verdict": Verdict.BLOCK, "stage": "preview" if dry_run else "confirm", "task_id": task_id,
                              "title": None, "from_state": None, "relayed_by": relayed, "owner_words": words, "reason": reason_text or None,
                              "owner_ref": owner_ref, "closed_kind": None, "chain": None, "decision_id": None, "decision": None,
                              "dispute": None, "followup": None, "criteria": [], "resume": None, "blocking_reasons": [],
                              "warnings": [], "written": [], "line": "", "publish_error": None}

    def _blocked(reasons: list[str]) -> dict[str, Any]:
        report["blocking_reasons"].extend(reasons)
        report["line"] = texts["blocked_line"].format(reasons="; ".join(report["blocking_reasons"]))
        return report

    if ref_problems:
        return _blocked(ref_problems)
    path, state = find_task_card(root, task_id)
    if path is None:
        return _blocked([texts["not_found"].format(task_id=task_id)])
    report["from_state"] = state
    report["governance_root"] = str(root)
    if state not in decl["from_states"]:
        return _blocked([texts["not_closed"].format(task_id=task_id, state=state, states="/".join(decl["from_states"]))])
    fm, body = require_frontmatter(Path(path))
    tid = str(fm.get("task_id") or task_id)
    report["task_id"], report["title"] = tid, str(fm.get("title") or "")
    role_gate = authorize_instance_class(relayed, str(decl["actor_role_class"]), root)
    problems: list[str] = []
    if not role_gate["ok"]:
        problems.append(texts["role_refused"].format(required_class=decl["actor_role_class"], reason=role_gate["reason"]))
    existing = closure_disputes(root, tid)
    if existing:
        problems.append(texts["already"].format(task_id=tid, decision_id=existing[-1].get("decision_id") or "?",
                                                followup=existing[-1].get("followup_task_id") or "?"))
    if problems:
        return _blocked(problems)

    # 原结案链(只读; 指纹进争议标记)
    def _rel(p: Path) -> str:
        try:
            return str(Path(p).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            return str(p)

    report["closed_kind"] = closure_state(root, tid)["kind"] or "completed"
    disputed: list[dict[str, Any]] = []
    for kind in decl["disputed_record_kinds"]:
        for f in gate_record_files(record_dir(root, str(kind), tid), str(kind)):
            disputed.append({"kind": str(kind), "path": _rel(f), "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()})
    refs = [fm.get("completion_report_ref"), fm.get("conclusion_report_ref"), *(fm.get("artifact_refs") or [])]
    reports = list(dict.fromkeys(str(r) for r in refs if isinstance(r, str) and r.strip()))
    by_kind = {k: [d["path"] for d in disputed if d["kind"] == k] for k in decl["disputed_record_kinds"]}
    none = str(fu["none"])
    report["chain"] = {"records": disputed, "reports": reports}
    chain_fill = {"closures": ", ".join(by_kind.get("closures") or []) or (str(fm.get("auto_closed_by") or "") or none),
                  "verdicts": ", ".join(by_kind.get("audit_verdicts") or []) or none,
                  "returns": ", ".join(by_kind.get("returns") or []) or none, "reports": ", ".join(reports) or none}

    # 承接卡卡号 / 标题 / 挂接
    followup_id = _fix_task_id(root, tid, {}, template=str(fu["task_id_template"]))
    title = " ".join(str(followup_title or "").split()) or str(fu["title_template"]).format(source_task_id=tid, source_title=report["title"])
    criteria = [str(c) for c in (fm.get("criteria") or []) if str(c).strip()] if isinstance(fm.get("criteria"), list) else []
    milestone = str(fm.get("milestone") or "").strip() or None

    # 决策记录(预演; AIPOS-F213 件②: 引用收件箱已有裁决 = 不另记)
    decided_at = iso_z()
    dry: dict[str, Any] = {}
    if ref_id:
        decision_id = ref_id
        report["decision"] = {"ok": True, "referenced": True, "record": (ref_ruling or {}).get("_path"),
                              "decided_at": (ref_ruling or {}).get("decided_at"), "relayed_by": (ref_ruling or {}).get("relayed_by"),
                              "owner_words": (ref_ruling or {}).get("owner_words")}
    else:
        payload = queue_ruling_payload(root, choice=str(decl["decision_choice"]), task_id=tid, anchor=followup_id,
                                       subject=f"{tid} [{state}] → {followup_id}", relayed_by=relayed, owner_words=words,
                                       owner_ref=str(owner_ref or "").strip() or None, decided_at=decided_at, note=reason_text or None,
                                       actor=actor)
        decision_id = str(payload["decision_id"])
        dry = record_owner_decision(payload, dry_run=True, repo_root=root, actor="owner")
        report["decision"] = {k: dry.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings")}
        report["decision"]["target_path"] = (dry.get("data") or {}).get("target_path")
        if dry.get("verdict") == Verdict.BLOCK or not dry.get("ok") or not dry.get("dry_run_token"):
            problems.extend(f"决策记录预演被拒: {r}" for r in (dry.get("blocking_reasons") or [])
                            or [json.dumps(dry.get("errors") or dry.get("message"), ensure_ascii=False)[:400]])
    report["decision_id"] = decision_id

    # 争议标记(渲染; 确认时写)
    event_type = dispute_event_type()  # 写读同一读取口(task_complexity.closure_disputes 按此前缀读)
    record_id = f"{event_type}_{tid}_{file_slug('compact', decided_at)}"
    event_meta = {"record_type": RecordType.TASK_PROGRESS_EVENT, "event_type": event_type, "task_id": tid, "actor": relayed,
                  "timestamp": decided_at, "decision_id": decision_id, "followup_task_id": followup_id, "relayed_by": relayed,
                  "owner_words": words, "reason": reason_text or None, "closed_kind": report["closed_kind"],
                  "disputed_records": [{"path": d["path"], "sha256": d["sha256"]} for d in disputed], "reports": reports}
    event_body = (f"# Closure Disputed: {tid}\n\nOwner 纠正已结案卡 {tid} 的结案(决策 {decision_id}, 经 {relayed} 转达; Owner 原话「{words}」)。"
                  f"原结案 / 裁决 / 交回记录不改(上列 sha256), 承接卡 {followup_id}。判据 transitions.schema queue_mutations.correct; "
                  "实现 tools/aipos_cli/board_adapter.correct_closed_task。\n")
    event_markdown = render_markdown(event_meta, event_body, list(event_meta))
    report["dispute"] = {"record_id": record_id, "path": None, "event_type": event_type}

    # 承接卡草稿(预演)
    criteria_part = str(fu["criteria_part"]).format(milestone=milestone, criteria=", ".join(criteria)) if milestone and criteria else ""
    fill = {"decision_id": decision_id, "relayed_by": relayed, "owner_words": words, "reason": reason_text or none,
            "source_title": report["title"], "dispute": record_id, "criteria_part": criteria_part,
            "source_body": _strip_machine_sections(body) or none, **chain_fill}
    meta, fbody = followup_draft_payload(root, {**fm, "task_id": tid}, template=fu, task_id=followup_id, milestone=milestone,
                                         criteria=criteria, ref_field=str(fu["ref_field"]), ref_value=decision_id, created_by=relayed,
                                         title=title, fill=fill, rebind_fields=list(fu["rebind_fields"]))
    draft = backend_create_draft(root, meta, fbody, dry_run=True)
    report["followup"] = {"task_id": followup_id, "title": title, "path": draft.get("target_path"), "task_mode": meta.get("task_mode"),
                          "harness": meta.get("harness"), "lane": meta.get("lane"), "milestone": meta.get("milestone"),
                          "criteria": meta.get("criteria") or [], "draft_warnings": draft.get("warnings") or [], "published": False,
                          "pending_path": None}
    if draft.get("verdict") == Verdict.BLOCK:
        problems.append(texts["followup_blocked"].format(followup=followup_id, reason="; ".join(draft.get("blocking_reasons") or [])))
    signer = resolve_publish_signer(root, relayed, connection_json)
    report["followup"]["signer"] = signer
    if signer["refusal"]:
        problems.append(texts["followup_blocked"].format(followup=followup_id, reason=signer["refusal"]))

    # 路线图标准(推导唯一 roadmap_drift.roadmap_status)与续跑命令
    report["criteria"] = _correct_criteria_rows(root, criteria, None)
    report["resume"] = _resume_for(root, followup_id, relayed)
    if problems:
        return _blocked(problems)
    fill_line = {"task_id": tid, "followup": followup_id, "decision_id": decision_id}
    if dry_run:
        report.update({"ok": True, "verdict": draft.get("verdict") or Verdict.PASS, "line": texts["preview_line"].format(**fill_line)})
        return report

    if not ref_id:
        done = execute_dry_run(str(dry["dry_run_token"]), "owner", owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=root)
        report["decision"] = {k: done.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings", "errors")}
        written = [str(w.get("path")) for w in (done.get("performed_writes") or []) if isinstance(w, dict) and w.get("path")]
        if not done.get("ok") or not written:
            return _blocked(["决策记录写入失败(零写入): "
                             + json.dumps(done.get("errors") or done.get("blocking_reasons"), ensure_ascii=False)[:400]])
        report["written"] = list(written)
    event_written = write_records_atomic(root, [("event", record_id, event_markdown, tid)])
    report["dispute"]["path"] = str(event_written["paths"][0])
    report["written"].append(str(root / report["dispute"]["path"]))

    def _partial(why: str) -> dict[str, Any]:
        report["publish_error"] = why
        report["blocking_reasons"].append(why)
        report["line"] = texts["partial_line"].format(reasons=why, **fill_line)
        return report

    created = backend_create_draft(root, meta, fbody, dry_run=False)
    if created.get("verdict") == Verdict.BLOCK or not created.get("wrote"):
        return _partial(texts["followup_blocked"].format(followup=followup_id, reason="; ".join(created.get("blocking_reasons") or [])))
    draft_rel = str(created["target_path"])
    report["followup"]["path"] = draft_rel
    report["written"].append(str(root / draft_rel))
    published = backend_publish_draft(root, draft_rel, dry_run=False, actor=signer["actor"])
    if published.get("verdict") == Verdict.BLOCK or not published.get("wrote"):
        return _partial("; ".join(published.get("blocking_reasons") or ["发布未写出"]))
    report["followup"]["published"] = True
    report["followup"]["pending_path"] = str(published["target_path"])
    report["written"].append(str(root / str(published["target_path"])))
    report["written"] += [str(root / str(w["path"])) for w in (published.get("record_writes") or []) if w.get("path")]
    report["criteria"] = _correct_criteria_rows(root, criteria, report["criteria"])
    report.update({"ok": True, "verdict": Verdict.PASS, "line": texts["done_line"].format(**fill_line)})
    return report


def render_correct_text(report: dict[str, Any]) -> list[str]:
    """AIPOS-F207: `lybra queue correct` 人读输出(文案读 verbs.lybra_queue_correct.texts; 首行 = 结论行)。"""
    from tools.aipos_cli.queue_mutation import correct_texts

    texts = correct_texts()
    lines = [str(report.get("line") or "")]
    if report.get("chain") is None:  # 前置即被拒: 只有结论行
        return lines
    written = report.get("stage") == "confirm" and bool(report.get("written"))
    will = "" if written else texts["will"]
    fu = report.get("followup") or {}
    lines.append(texts["header"].format(project=Path(str(report.get("governance_root") or ".")).name, task_id=report["task_id"],
                                        title=report.get("title") or "", closed_kind=report.get("closed_kind") or "-"))
    chain = report["chain"]
    by_kind: dict[str, list[str]] = {}
    for rec in chain.get("records") or []:
        by_kind.setdefault(rec["kind"], []).append(f"{rec['path']}(sha256 {rec['sha256'][:12]})")
    lines.append(texts["chain"].format(closures=", ".join(by_kind.get("closures") or []) or "-",
                                       verdicts=", ".join(by_kind.get("audit_verdicts") or []) or "-",
                                       returns=", ".join(by_kind.get("returns") or []) or "-", reports=", ".join(chain.get("reports") or []) or "-"))
    reason_part = texts["reason_part"].format(reason=report["reason"]) if report.get("reason") else ""
    decision = report.get("decision") or {}
    if decision.get("referenced"):  # AIPOS-F213 件②: 引用收件箱已有裁决(不另记)
        lines.append(texts["decision_ref"].format(decision_id=report.get("decision_id"), decided_at=decision.get("decided_at") or "?",
                                                  relayed_by=decision.get("relayed_by") or report.get("relayed_by"),
                                                  owner_words=report.get("owner_words"), reason_part=reason_part))
    else:
        lines.append(texts["decision"].format(will=will, decision_id=report.get("decision_id"), relayed_by=report.get("relayed_by"),
                                              owner_words=report.get("owner_words"), reason_part=reason_part))
    dispute = report.get("dispute") or {}
    lines.append(texts["dispute"].format(will=will, path=dispute.get("path") or dispute.get("record_id"),
                                         count=len(chain.get("records") or [])))
    crit = f"; 挂 {fu.get('milestone')} / {', '.join(fu.get('criteria') or [])}" if fu.get("milestone") else ""
    lines.append(texts["followup"].format(will="" if fu.get("published") else texts["will"], followup_task_id=fu.get("task_id"),
                                          path=fu.get("pending_path") or fu.get("path"), task_mode=fu.get("task_mode"),
                                          harness=fu.get("harness"), criteria_part=crit))
    for row in report.get("criteria") or []:
        lines.append(texts["criterion"].format(criterion=row["criterion"], before=row.get("before") or "-",
                                               after=row.get("after") or "(纠正后: 不符·待承接)"))
    if report.get("publish_error"):
        hint = texts["publish_hint"].format(governance_root=report.get("governance_root") or ".", path=fu.get("path") or "<草稿路径>",
                                            actor=report.get("relayed_by"))
        lines.append(texts["publish_blocked"].format(record_state=texts["record_kept_written"], reason=report["publish_error"], hint=hint))
    if report.get("resume"):
        lines.append(texts["resume"].format(resume=report["resume"]))
    for warning in report.get("warnings") or []:
        lines.append(f"  ! {warning}")
    return lines


def pause_task(
    task_id: str,
    *,
    action: str,
    relayed_by: str | None,
    owner_words: str | None,
    owner_ref: str | None = None,
    actor: str = "owner",
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-F231 件①: Owner 暂停 / 恢复一张在途卡(`lybra queue pause|resume` 唯一实现; 声明 verbs.schema lybra_queue_pause)。

    action ∈ {"pause", "resume"}。前置: 卡在 from_states / 转达人角色类 = actor_role_class(custom_roles.authorize_instance_class)/ 暂停须未暂停、
    恢复须已暂停(判定唯一 owner_inbox.card_pause_state)。记录 = 既有 owner_decision_record(owner_inbox.queue_ruling_payload, 与 supersede /
    correct / owner decide 同一构造与写路径 record_owner_decision + execute_dry_run; 选项 = 声明 pause_choice / resume_choice, 锚点 = 裁决时刻
    = 每次各一份, 追加不覆盖)。预演零写入; 确认写决策记录(written 由 CLI 经 queue_writers 落账)。relayed_by / owner_words 缺或多行 =
    owner_inbox.InboxUsageError(与 owner decide 同一校验)。返回 {ok, verdict, stage, action, task_id, state, decision_id, decision, pause,
    blocking_reasons, written, line}。"""
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
    from tools.aipos_cli.custom_roles import authorize_instance_class
    from tools.aipos_cli.owner_inbox import card_pause_state, pause_rulings, queue_ruling_payload, relay_fields
    from tools.aipos_cli.queue_mutation import pause_declaration
    from tools.aipos_cli.task_loader import find_task_card

    decl = pause_declaration()
    texts = decl["texts"]
    if action not in ("pause", "resume"):
        raise ValueError(f"pause_task action 须为 pause / resume, 得到 {action!r}")
    label = texts["pause_action"] if action == "pause" else texts["resume_action"]
    choice = str(decl["pause_choice"] if action == "pause" else decl["resume_choice"])
    relayed, words = relay_fields(relayed_by, owner_words)
    root = _resolve_repo_root(repo_root)
    report: dict[str, Any] = {"ok": False, "verdict": Verdict.BLOCK, "stage": "preview" if dry_run else "confirm", "action": action,
                              "task_id": task_id, "state": None, "decision_id": None, "decision": None, "pause": None,
                              "relayed_by": relayed, "owner_words": words, "blocking_reasons": [], "written": [], "line": ""}

    def _blocked(reasons: list[str]) -> dict[str, Any]:
        report["blocking_reasons"].extend(reasons)
        report["line"] = texts["blocked_line"].format(action=label, reasons="; ".join(report["blocking_reasons"]))
        return report

    path, state = find_task_card(root, task_id)
    if path is None:
        return _blocked([texts["not_found"].format(task_id=task_id)])
    report["state"] = state
    if state not in decl["from_states"]:
        return _blocked([texts["bad_state"].format(task_id=task_id, state=state, states="/".join(decl["from_states"]))])
    problems: list[str] = []
    role_gate = authorize_instance_class(relayed, str(decl["actor_role_class"]), root)
    if not role_gate["ok"]:
        problems.append(texts["role_refused"].format(required_class=decl["actor_role_class"], reason=role_gate["reason"]))
    current = card_pause_state(root, task_id)
    report["pause"] = current
    if action == "pause" and current is not None:
        problems.append(texts["already_paused"].format(task_id=task_id, decision_id=current["decision_id"], decided_at=current["decided_at"]))
    if action == "resume" and current is None:
        problems.append(texts["not_paused"].format(task_id=task_id))
    if problems:
        return _blocked(problems)
    # 暂停 / 恢复链按裁决时刻排序(card_pause_state 取最近一条): 与上一条同秒 = 顺延 1 秒, 保证链上时刻严格递增(判定不靠文件名序)
    moment = utc_now().replace(microsecond=0)
    chain = pause_rulings(root, task_id, decl)
    if chain:
        from datetime import timedelta

        prev = datetime.fromisoformat(str(chain[-1].get("decided_at") or "").replace("Z", "+00:00"))
        if prev >= moment:
            moment = prev + timedelta(seconds=1)
    decided_at = iso_z(moment)
    payload = queue_ruling_payload(root, choice=choice, task_id=task_id, anchor=decided_at, subject=f"{task_id} [{state}] {label}",
                                   relayed_by=relayed, owner_words=words, owner_ref=str(owner_ref or "").strip() or None,
                                   decided_at=decided_at, actor=actor)
    report["decision_id"] = str(payload["decision_id"])
    dry = record_owner_decision(payload, dry_run=True, repo_root=root, actor="owner")
    report["decision"] = {k: dry.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings")}
    target = (dry.get("data") or {}).get("target_path")
    report["decision"]["target_path"] = target
    if dry.get("verdict") == Verdict.BLOCK or not dry.get("ok") or not dry.get("dry_run_token"):
        return _blocked([f"决策记录预演被拒: {r}" for r in (dry.get("blocking_reasons") or [])]
                        or [json.dumps(dry.get("errors") or dry.get("message"), ensure_ascii=False)[:400]])
    if dry_run:
        report.update({"ok": True, "verdict": Verdict.PASS,
                       "line": texts["preview_line"].format(action=label, task_id=task_id, state=state, target=target or "?")})
        return report
    done = execute_dry_run(str(dry["dry_run_token"]), "owner", owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=root)
    report["decision"] = {k: done.get(k) for k in ("ok", "verdict", "blocking_reasons", "warnings", "errors")}
    written = [str(w.get("path")) for w in (done.get("performed_writes") or []) if isinstance(w, dict) and w.get("path")]
    if not done.get("ok") or not written:
        return _blocked(["决策记录写入失败(零写入): " + json.dumps(done.get("errors") or done.get("blocking_reasons"), ensure_ascii=False)[:400]])
    report["written"] = written
    report["pause"] = card_pause_state(root, task_id)  # 写后复核(同一判定): 暂停 = 已暂停, 恢复 = None
    record = next((w for w in written if Path(w).name.startswith(str(report["decision_id"]))), written[0])
    report.update({"ok": True, "verdict": Verdict.PASS,
                   "line": texts["done_line"].format(action=label, task_id=task_id, decision_id=report["decision_id"], record=record)})
    if report["pause"] is not None:
        report["line"] += "\n  " + report["pause"]["line"]
    return report


def _correct_criteria_rows(root: Path, criteria: list[str], before: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """AIPOS-F207: 原卡挂的路线图标准此刻状态(推导唯一 roadmap_drift.roadmap_status; 无路线图 / 未挂 = 空)。before 给出 = 填 after。"""
    if not criteria:
        return []
    from tools.aipos_cli.roadmap_drift import roadmap_status

    view = roadmap_status(root, project=Path(root).resolve().name)
    rows = {r["id"]: r for m in view.get("milestones") or [] for r in m.get("criteria") or []}
    if before is None:
        return [{"criterion": c, "before": (rows.get(c) or {}).get("state_label"), "after": None} for c in criteria if c in rows]
    return [{**b, "after": (rows.get(b["criterion"]) or {}).get("state_label"),
             "basis": (rows.get(b["criterion"]) or {}).get("basis")} for b in before]


def restricted_amend_actor_class() -> str:
    """AIPOS-F132 件②: 受限 amend(含 queue_rework 返工节)的执行者角色类——唯一声明 card.schema restricted_amend.actor_role_class。
    缺声明 = SchemaLoadError(fail-closed, 由调用方 _normalize_exception 出声)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card", _code_repo_schema_root()).get("restricted_amend")
    cls = str((decl or {}).get("actor_role_class") or "").strip() if isinstance(decl, dict) else ""
    if not cls:
        raise SchemaLoadError("card.schema.json restricted_amend.actor_role_class 未声明")
    return cls


def restricted_amend_fields() -> set[str]:
    """AIPOS-F78 前置零⑤: advisor 对 claimed 卡可受限修改的字段集合——唯一声明 card.schema restricted_amend.claimed_card_fields
    (F75 只写死 {rework_rounds}; 现读声明, 含 output_target/lane)。缺声明 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card", _code_repo_schema_root()).get("restricted_amend")
    fields = list((decl or {}).get("claimed_card_fields") or []) if isinstance(decl, dict) else []
    if not fields:
        raise SchemaLoadError("card.schema.json restricted_amend.claimed_card_fields 未声明")
    return {str(f) for f in fields}


def amend_replay_payload(*, task_id: str, amendments: dict[str, Any], amendment_reason: str, amendment_type: str | None,
                         restricted_fields: set[str] | None) -> dict[str, Any]:
    """AIPOS-F157 件②: queue_amend 确认令牌计划里的原始参数(amend_task 的关键字参数; restricted_fields 存为有序列表以入快照哈希)。
    execute_dry_run 重放(预演核快照 / 确认写入)经 amend_replay_kwargs 还原, 两处同一形。"""
    return {"task_id": task_id, "amendments": dict(amendments), "amendment_reason": amendment_reason,
            "amendment_type": amendment_type, "restricted_fields": sorted(restricted_fields) if restricted_fields is not None else None}


def amend_replay_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F157 件②: 由确认令牌计划的 original_payload 还原 amend_task 关键字参数(缺键 = amend_task 自身拒, fail-closed)。"""
    restricted = payload.get("restricted_fields")
    return {"task_id": payload.get("task_id"), "amendments": payload.get("amendments"),
            "amendment_reason": payload.get("amendment_reason"), "amendment_type": payload.get("amendment_type"),
            "restricted_fields": set(restricted) if isinstance(restricted, list) else None}


def amend_task(
    task_id: str | None = None,
    path: str | Path | None = None,
    actor: str | None = None,
    amendments: dict[str, Any] | None = None,
    amendment_reason: str | None = None,
    amendment_type: str | None = None,
    restricted_fields: set[str] | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-315: amend a pending (unclaimed) task's frontmatter or body.
    
    AIPOS-F75: 受限模式(restricted_fields 非空)允许对 claimed 卡修改指定字段。
    
    Only works on pending tasks. Claimed tasks cannot be amended (in-transit work
    should not have requirements changed underneath it).
    
    EXCEPTION: restricted mode (restricted_fields != None) allows amending claimed
    tasks but ONLY for fields in restricted_fields set. All other fields blocked.
    
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
        
        # AIPOS-F75: 受限模式判据
        is_restricted_mode = restricted_fields is not None
        current_state = task.get("queue_state")
        # AIPOS-F171 件②: 只改路线图挂接字段(card.schema roadmap_link.fields)的 amend = 老卡回挂, 在 roadmap_link.amend_states 内任一
        # 队列状态均允许(不走 pending / 受限两条状态闸); 挂接引用经 roadmap.link_problems 校验(与出卡同一判据)
        from tools.aipos_cli.roadmap import link_declaration, link_problems

        _link_decl = link_declaration()
        roadmap_link_only = set(amendments) <= set(_link_decl["fields"])
        if roadmap_link_only and current_state not in _link_decl["amend_states"]:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="ROADMAP_LINK_STATE",
                message=f"Roadmap link amend is allowed in states {_link_decl['amend_states']}, but task is {current_state}.",
                actor=_actor_payload(actor_text),
                data={"task_id": task.get("task_id"), "current_state": current_state},
                safety_notice="AIPOS-F171: roadmap link amend states are declared in card.schema roadmap_link.amend_states.",
            )
        
        # S1: Only allow amending pending tasks (UNLESS restricted mode for claimed)
        if roadmap_link_only:
            pass
        elif not is_restricted_mode:
            # 普通模式: 只允许 pending
            if current_state != "pending":
                return blocked_response(
                    operation=operation,
                    dry_run=dry_run,
                    category="NOT_PENDING",
                    message=f"Task is in {current_state} state. Only pending (unclaimed) tasks can be amended.",
                    actor=_actor_payload(actor_text),
                    data={
                        "task_id": task.get("task_id"),
                        "current_state": current_state,
                        "recommended_action": "Amendments are only allowed on pending tasks to avoid changing requirements for in-transit work."
                    },
                    safety_notice="AIPOS-315 S1: claimed tasks cannot be amended (would change requirements mid-execution)."
                )
        else:
            # 受限模式: 只允许 claimed
            if current_state != "claimed":
                return blocked_response(
                    operation=operation,
                    dry_run=dry_run,
                    category="RESTRICTED_MODE_NOT_CLAIMED",
                    message=f"Restricted mode amend requires task to be claimed, but it is {current_state}.",
                    actor=_actor_payload(actor_text),
                    data={
                        "task_id": task.get("task_id"),
                        "current_state": current_state,
                        "restricted_fields": list(restricted_fields),
                        "recommended_action": "Restricted amendments are only for claimed tasks (in-transit controlled updates)."
                    },
                    safety_notice="AIPOS-F75: restricted amend only applies to claimed tasks for controlled field updates (e.g. rework_rounds)."
                )
            # 检查 amendments 中的字段是否在允许清单内
            disallowed = set(amendments.keys()) - restricted_fields
            if disallowed:
                return blocked_response(
                    operation=operation,
                    dry_run=dry_run,
                    category="RESTRICTED_FIELD_VIOLATION",
                    message=f"Restricted mode only allows amending {restricted_fields}, but attempted to amend: {disallowed}",
                    actor=_actor_payload(actor_text),
                    data={
                        "task_id": task.get("task_id"),
                        "restricted_fields": list(restricted_fields),
                        "attempted_fields": list(amendments.keys()),
                        "disallowed_fields": list(disallowed),
                        "recommended_action": "Only amend fields within restricted_fields set."
                    },
                    safety_notice="AIPOS-F75: restricted amend must not touch fields outside restricted_fields."
                )
        
        # Read current task content(AIPOS-F100 件②: 读-改-写前「必须读出」; 读不出 = 拒——原忽略解析告警, 卡被拒成 {}
        # 时会把卡重写成只剩被 amend 的字段)
        from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

        try:
            metadata, body = require_frontmatter(task_path_obj)
        except FrontmatterReadError as exc:
            return blocked_response(
                operation=operation,
                dry_run=dry_run,
                category="VALIDATION_ERROR",
                message=f"FRONTMATTER_UNREADABLE: task card frontmatter unreadable, amend refused (no rewrite): {exc}",
                actor=_actor_payload(actor_text),
                data={"task_id": task.get("task_id"), "recommended_action": "Repair the card frontmatter first (lybra state repair --task-id)."},
                safety_notice="AIPOS-F100: read-modify-write requires a fully readable card; never rewrite from a partial read.",
            )
        
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
        
        # AIPOS-F179 item 2: exclusive resources keep the draft-time shape check (single check loop_run_record.card_resources)
        if "resources" in amendments:
            from tools.aipos_cli.loop_run_record import card_resources

            try:
                card_resources(updated_metadata)
            except ValueError as exc:
                validation_errors.append(str(exc))

        # AIPOS-F205 件③: 改来源卡 / 候选(固定候选卡与环境卡候选绑定)= 与起草发卡同一判据 draft_writer.candidate_subject_refusals
        # (字段名读 enums.schema task_mode.candidate_subject.fields; 已认领卡经 --restricted, 允许字段 card.schema restricted_amend)
        from tools.aipos_cli.next_resolver import candidate_subject_declaration

        if set(amendments) & set(candidate_subject_declaration()["fields"].values()):
            from tools.aipos_cli.draft_writer import candidate_subject_refusals

            validation_errors.extend(candidate_subject_refusals(resolved_root, {**updated_metadata,
                                                                                "task_id": str(updated_metadata.get("task_id") or task.get("task_id") or "")}))

        # AIPOS-F175 件②: 改卡面 artifacts 同经 link_problems(含产物声明形校验 roadmap.artifact_problems, 与出卡同一判据)
        from tools.aipos_cli.roadmap import artifacts_declaration

        if set(amendments) & {*_link_decl["fields"], artifacts_declaration()["field"]}:
            link_blocking, _link_warnings = link_problems(updated_metadata, resolved_root)
            validation_errors.extend(link_blocking)

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
        amendment_timestamp = iso_z()
        amendment_id = build_runtime_id("amendment", task.get("task_id"), amendment_timestamp, actor_text)
        
        amendment_record = {
            "record_type": RecordType.AMENDMENT_RECORD,  # AIPOS-F147 件②: 值域单源 enums.schema record_type
            "amendment_id": amendment_id,
            "task_id": task.get("task_id"),
            "amended_by": actor_text,
            "amended_at": amendment_timestamp,
            "reason": str(amendment_reason).strip(),
            "amendment_type": str(amendment_type or "general").strip(),
            "original_metadata": original_metadata,
            "updated_metadata": updated_metadata,
            "original_body": original_body if body_changed else None,
            "updated_body": body if body_changed else None,
            "amendments_applied": list(amendments.keys()),
        }
        
        # Build amendment record markdown
        # AIPOS-F109 件①: 落点 = 声明 queue_rework.record.location(record_locations.kinds.amendments) 经 record_dir; 落盘名 = amendment_id
        # (原文件名手写 `T…Z` 压缩法、与 amendment_id 不同式样; 存量旧名照读)
        amendments_dir = record_dir(resolved_root, "amendments", str(task.get("task_id")))
        amendment_path = amendments_dir / f"{amendment_id}.md"
        
        # AIPOS-F87 件①: frontmatter 经单源 record_writer.render_frontmatter_block(原 f-string 手拼退役:
        # 理由含 `**`/冒号/`#` 时曾写出不可解析的修订记录)。
        from tools.aipos_cli.record_writer import render_frontmatter_block

        amendment_fm_order = ["record_type", "amendment_id", "task_id", "amended_by", "amended_at", "amendment_type", "reason"]
        amendment_fm = render_frontmatter_block(
            {key: amendment_record[key] for key in amendment_fm_order},
            amendment_fm_order,
        )
        amendment_markdown = amendment_fm + f"""

# Amendment Record: {task.get('task_id')}

## Reason
{str(amendment_reason).strip()}

## Amendment Type
{str(amendment_type or 'general').strip()}

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
                    "current_state": current_state,
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
            # AIPOS-F157 件②: 确认令牌的计划带原始参数(F154 queue_rework 同一口径)——门 lybra_queue_amend_confirm 凭 dry_run_token 经
            # execute_dry_run 以同一 amend_task 重放预演(快照比对)后写入; 原 confirm 不认令牌、按入参裸重做, execute 路径拿
            # amendments_to_apply(字段名列表)当改动 dict, 两条都落不了预演过的那次修改。queue_rework 复用本函数后以自己的 original_payload 覆盖。
            response.setdefault("data", {})["original_payload"] = amend_replay_payload(
                task_id=str(task.get("task_id") or ""), amendments=amendments, amendment_reason=str(amendment_reason).strip(),
                amendment_type=amendment_type, restricted_fields=restricted_fields)
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
def read_settlement_status(
    task_id: str,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-F61 大项③: 结算状态一次读齐三类记录。

    禁只读其一或只看队列目录位置。一次调用读齐:
    1. 队列位置 (pending/claimed/completed/blocked/withdrawn)
    2. closure 记录 (records/closures/<task_id>/)
    3. finalization 记录 (records/finalizations/<task_id>/)

    Returns:
        {
            "task_id": str,
            "queue_position": {"state": str, "path": str | None},
            "closure_records": list[dict],  # 每条 closure 的 frontmatter
            "finalization_records": list[dict],  # 每条 finalization 的 frontmatter
            "is_settled": bool,  # 三类记录齐全 = 已结算
            "missing": list[str],  # 缺哪些
        }
    """
    resolved_root = _resolve_repo_root(repo_root)
    result: dict[str, Any] = {
        "task_id": task_id,
        "queue_position": {"state": "unknown", "path": None},
        "closure_records": [],
        "finalization_records": [],
        "is_settled": False,
        "missing": [],
    }

    # ① 队列位置
    try:
        matches = find_task_by_id(task_id, resolved_root)
        if matches[1]:
            task_info = matches[1][0]
            result["queue_position"] = {
                "state": str(task_info.get("queue_state") or "unknown"),
                "path": str(task_info.get("path") or ""),
            }
    except (ValueError, FileNotFoundError, OSError):
        result["missing"].append("queue_position")

    # ②③ closure / finalization 记录(AIPOS-F100 件②: 「必须读出」; 读不出的不计入记录——原 `except Exception: pass` 与
    # 忽略解析告警会把读不出的 closure 当成一条 {} 记录、算作已结算——而是列进 unreadable「读不出: <路径>: <原因>」)
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    result["unreadable"] = []
    for key, sub, prefix in (("closure_records", "closures", "close_"), ("finalization_records", "finalizations", "finalization_")):
        kind_dir = record_dir(resolved_root, sub, task_id)
        if not kind_dir.is_dir():
            continue
        for record_file in sorted(kind_dir.glob(f"{prefix}*.md")):
            try:
                fm, _body = require_frontmatter(record_file)
            except FrontmatterReadError as exc:
                result["unreadable"].append(str(exc))
                continue
            fm["_path"] = str(record_file.relative_to(resolved_root))
            result[key].append(fm)
    if not result["closure_records"]:
        result["missing"].append("closure_record")

    if not result["finalization_records"]:
        result["missing"].append("finalization_record")

    # 判定是否已结算: 队列在 completed + 有 closure + 有 finalization
    result["is_settled"] = (
        result["queue_position"]["state"] == "completed"
        and bool(result["closure_records"])
        and bool(result["finalization_records"])
    )

    return result


def queue_rework_task(
    *,
    task_id: str,
    verdict_ref: str,
    focus_items: list[str],
    acceptance_criteria: str = "",
    actor: str | None = None,
    agent_instance: str | None = None,
    owner_policy_ref: str | None = None,
    dry_run: bool = True,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """AIPOS-F75 件②: queue_rework 门动词 — 对 claimed 卡追加返工轮次。
    
    两阶段: dry_run 预览 + confirm 执行。
    受限 amend: 只允许修改 rework_rounds，禁触其他字段。
    
    复用 amend_task 的受限模式完成写卡+落记录。
    """
    try:
        actor_text = str(actor or "").strip()
        instance_text = str(agent_instance or "").strip()
        if not actor_text:
            raise ValueError("actor is required")
        if not instance_text:
            raise ValueError("agent_instance is required")
        if not task_id:
            raise ValueError("task_id is required")
        if not verdict_ref:
            raise ValueError("verdict_ref is required")
        if not focus_items:
            raise ValueError("focus_items is required (at least one item)")
        
        # 检查角色: 只有顾问类可以追加返工节
        # AIPOS-F132 件②: 角色类经唯一解析(与裁决提交同一函数), 要求类读 card.schema restricted_amend.actor_role_class
        # (queue_rework = 受限 amend 子集的唯一声明); 不再按实例名前缀 {advisor, advise} 判。
        resolved_root = _resolve_repo_root(repo_root)
        from tools.aipos_cli.custom_roles import authorize_instance_class

        role_gate = authorize_instance_class(instance_text, restricted_amend_actor_class(), resolved_root)
        if not role_gate["ok"]:
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor_text,
                "dry_run": dry_run,
                "blocking_reasons": [
                    f"ROLE_VIOLATION: queue_rework 只能由角色类 {role_gate['required_class']} 的实例执行"
                    f"(内建或门注册表 role_class={role_gate['required_class']} 的自定义角色)。"
                    f"实例 '{instance_text}' 未授权: {role_gate['reason']}。"
                ],
                "warnings": [],
                "data": {"role_resolution": role_gate},
                "message": "Queue rework blocked: role violation",
            }
        
        
        # 构建新返工轮次 (校验 + 构造)
        from tools.aipos_cli.queue_mutation import build_rework_round
        round_result = build_rework_round(
            repo_root=resolved_root,
            task_id=task_id,
            verdict_ref=verdict_ref,
            focus_items=focus_items,
            acceptance_criteria=acceptance_criteria,
            actor=actor_text,
        )
        
        if round_result.get("verdict") == Verdict.BLOCK:
            return round_result
        
        # 调用 amend_task 的受限模式
        new_rounds = round_result["data"]["updated_rework_rounds"]
        result = amend_task(
            task_id=task_id,
            actor=actor_text,
            amendments={"rework_rounds": new_rounds},
            amendment_reason=f"Add rework round {round_result['data']['round_added']} based on verdict {verdict_ref}",
            amendment_type="add_rework_round",
            restricted_fields={"rework_rounds"} & restricted_amend_fields(),  # AIPOS-F78 前置零⑤: 子集须在声明内
            dry_run=dry_run,
            repo_root=resolved_root,
        )
        
        # 增强返回信息
        if result.get("ok"):
            result.setdefault("data", {})["round_added"] = round_result["data"]["round_added"]
            result.setdefault("data", {})["total_rounds"] = round_result["data"]["total_rounds"]
            result.setdefault("data", {})["max_rounds"] = round_result["data"]["max_rounds"]
            result.setdefault("data", {})["rework_round"] = round_result["data"]["new_round"]
        auto_release = (round_result.get("data") or {}).get("auto_release")
        if auto_release and result.get("ok") and result.get("verdict") != Verdict.BLOCK:
            # AIPOS-F225 件②: 第二档自动追加轮 = 改卡写入成功后落一条自动放行记录(owner_inbox.write_auto_release 唯一写口; 轮次 auto_extension_ref = 其 ID)
            result.setdefault("data", {})["auto_release"] = {"record_id": auto_release["record_id"], "basis": auto_release["basis"]}
            if not dry_run:
                from tools.aipos_cli.owner_inbox import write_auto_release

                written = write_auto_release(
                    resolved_root, task_id, str(auto_release["kind"]), released_by=actor_text, basis=str(auto_release["basis"]),
                    record_id=str(auto_release["record_id"]), round_no=round_result["data"]["round_added"], verdict_ref=verdict_ref,
                    evidence={"fingerprint": auto_release.get("fingerprint"), "publish_ref": auto_release.get("publish_ref")})
                for path_text in written["paths"]:
                    rel = Path(path_text).resolve()
                    rel_text = rel.relative_to(resolved_root.resolve()).as_posix() if rel.is_relative_to(resolved_root.resolve()) else str(rel)
                    result.setdefault("performed_writes", []).append({"path": rel_text, "kind": "create", "type": "auto_release_record"})

        if dry_run and result.get("ok") and result.get("verdict") != Verdict.BLOCK:
            # AIPOS-F154: 预览签发的确认令牌须是 queue_rework 自己的(门 lybra_queue_rework_confirm 只认 operation=queue_rework;
            # 原样透出内部 amend 的 queue_amend 令牌 = 确认必拒 INCOMPATIBLE_DRY_RUN, 返工节永远落不了)。令牌 plan 带原始参数,
            # confirm 经 execute_dry_run 以同一 queue_rework_task 重放; 内部 amend 令牌不外露。
            for key in ("dry_run_id", "dry_run_token", "dry_run_snapshot_hash", "dry_run_created_at", "dry_run_expires_at"):
                result.pop(key, None)
            result.setdefault("data", {})["original_payload"] = {
                "task_id": task_id,
                "verdict_ref": verdict_ref,
                "focus_items": list(focus_items),
                "acceptance_criteria": acceptance_criteria,
                "agent_instance": instance_text,
                "owner_policy_ref": owner_policy_ref,
            }
            result = _attach_controlled_execute_metadata(
                operation="queue_rework", actor=actor_text, response=result, execute_allowed=True
            )
        return result
    except Exception as exc:
        return _normalize_exception("queue_rework", exc, dry_run=dry_run, actor=_actor_payload(actor))


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
