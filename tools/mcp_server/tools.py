from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.aipos_cli.board_adapter import (
    _resolve_active_project_for,
    amend_task,
    audit_dispatch_task,
    audit_verdict_task,
    bench_audit_submit,
    claim_task,
    close_task,
    converge_r_cards,
    create_draft,
    execute_dry_run,
    get_context_pack_preview,
    get_preview,
    get_queue,
    get_validate,
    load_task_snapshot,
    mark_concluded_task,
    publish_draft,
    record_owner_decision,
    return_task,
    submit_external_intake,
    withdraw_task,
)
from tools.aipos_cli.autonomy_policy import (
    AUTONOMY_MODE_PREAUTHORIZED,
    AUTONOMY_MODE_SUPERVISED,
    count_preauthorized_claims,
    load_policy,
    match_claim_envelope,
    trace_envelope,
)
from tools.aipos_cli.agent_profiles import load_agent_profiles, registry_available, resolve_instance_id
from tools.aipos_cli.controlled_execute import get_dry_run
from tools.aipos_cli.records import find_records_for_task, load_records
from tools.aipos_cli.task_loader import find_repo_root
from tools.aipos_cli.workspace_config import _project_candidates, has_workspace_queue, resolve_home_root
from tools.schema_loader import get_role_scopes, load_schema
from tools.schema_constants import RecordType, Verdict


READ_ONLY_NOTICE = "Lybra MCP exposes read tools by default. Write tools are visible only with scoped capability."
CAPABILITY_ENV_VAR = "LYBRA_CAPABILITY_TOKEN"
REQUEST_CAPABILITY: ContextVar[dict[str, Any] | None] = ContextVar("lybra_mcp_request_capability", default=None)
INTAKE_SCOPE = "intake_submit"
OWNER_DECISION_SCOPE = RecordType.OWNER_DECISION_RECORD
DRAFT_PUBLISH_SCOPE = "draft_publish"
# AIPOS-249 (planner slice): the planner's ONLY write scope — land a task-card DRAFT into
# 5_tasks/drafts/ (a proposal zone, path-locked by DRAFTS_DIR + draft_slug). This is NOT
# draft_publish: submitting a draft does NOT put it into truth (queue/pending). Landing it —
# drafts -> queue/pending — is draft_publish, which additionally requires owner_confirm, so
# the planner (which holds neither) can never publish. Draft submit confirm does NOT require
# owner_confirm (a draft is a proposal, not truth); the Owner gate is at publish.
DRAFT_SUBMIT_SCOPE = "draft_submit"
QUEUE_CLAIM_SCOPE = "queue_claim"
QUEUE_RETURN_SCOPE = "queue_return"
AUDIT_DISPATCH_SCOPE = RecordType.AUDIT_DISPATCH
AUDIT_VERDICT_SCOPE = RecordType.AUDIT_VERDICT
# AIPOS-283: queue_close scope — executor/advisor(planner) can call.
# This is NOT owner-gated: the close verb is the finalize settlement step
# that the executor calls after work is returned. It requires closure_evidence
# and a prior return record, but NOT owner_confirm.
QUEUE_CLOSE_SCOPE = "queue_close"
# AIPOS-315: withdraw and amend scopes for task lifecycle management.
# withdraw: remove task from queue (pending or claimed) with reason, preserves all records.
# amend: modify pending task frontmatter/body with amendment history (only pending allowed).
QUEUE_WITHDRAW_SCOPE = "queue_withdraw"
QUEUE_AMEND_SCOPE = "queue_amend"
# AIPOS-323: task_progress scope — agent self-reports task facts (started/progress/completed/blocked)
# to the gate, which records them (append-only events) without maintaining online/offline state.
# This is the "agent opens mouth to gate" direction (取代顾问观察式); gate只记录不判活、不心跳、不推送。
TASK_PROGRESS_SCOPE = "task_progress"
# AIPOS-197 gate-hardening v0: an Owner-only scope required to CONFIRM consequential
# truth mutations. dry-run keeps its operation scope; confirm additionally requires
# this scope, which the executor token does not hold — so a confined agent cannot
# self-confirm regardless of whether it knows the static OWNER_CONFIRMED literal.
# v0 scope: claim + return confirm only (the F-candidate-1 surface proven in 191B).
# Principle: every confirm tool that mutates Owner-gated truth should require an
# Owner-held confirm scope; audit_dispatch/audit_verdict/intake/owner_decision/
# workspace_init confirms keep their existing role gate until each one's legitimate
# confirmer is decided per-tool (to preserve executor != auditor != owner).
OWNER_CONFIRM_SCOPE = "owner_confirm"
DISCIPLINE_DOC_REF = "AIPOS-109 MCP-native discipline"
SUPERVISED_CLAIM_DOC_REF = "AIPOS-165 Supervised MCP Explicit Claim Protocol"
OWNER_CONFIRMATION_TOKEN = "OWNER_CONFIRMED"
FORBIDDEN_QUEUE_CLAIM_FIELDS = {
    "api_key",
    "auto_pick",
    "auto_select",
    "background_worker",
    "batch",
    "bearer_token",
    "credential",
    "credentials",
    "delegated_policy",
    "llm_raw_prompt",
    "llm_raw_response",
    "policy_budget",
    "raw_prompt",
    "raw_response",
    "standing_policy",
    "token",
}
FORBIDDEN_QUEUE_RETURN_FIELDS = {
    *FORBIDDEN_QUEUE_CLAIM_FIELDS,
    "audit_dispatch",
    "audit_pass",
    "audit_verdict",
    "finalize",
    "finalize_approval",
    "lease_activation",
    "lease_writer",
    "raw_transcript",
}
FORBIDDEN_AUDIT_FIELDS = {
    *FORBIDDEN_QUEUE_RETURN_FIELDS,
    "accepted_work_unblock",
    "audit_pass_override",
    "auto_dispatch",
    "auto_verdict",
    "finalize_after_pass",
    "launch_auditor",
    "runtime",
    "scheduler",
    "worker",
}


# AIPOS-294: Request-level project routing context. When set, _repo_root() resolves to
# this project's workspace instead of using the process-global binding.
REQUEST_PROJECT: ContextVar[str | None] = ContextVar("lybra_mcp_request_project", default=None)


def _reload_token_registry() -> None:
    """FIX-2: 热重载 token registry(从 connection.json 重新加载)。
    
    调用 http_sse 模块的全局 server 实例来重载凭据源。
    """
    import sys
    from pathlib import Path
    
    # Debug: 写日志文件
    debug_log = Path("/tmp/reload_debug.log")
    try:
        with debug_log.open("a") as f:
            f.write(f"\n=== _reload_token_registry called ===\n")
            f.flush()
        
        from tools.mcp_server import http_sse
        with debug_log.open("a") as f:
            f.write(f"_CURRENT_SERVER exists: {http_sse._CURRENT_SERVER is not None}\n")
            f.flush()
        
        print(f"[tools.py] _reload_token_registry called, _CURRENT_SERVER={http_sse._CURRENT_SERVER is not None}", file=sys.stderr)
        if http_sse._CURRENT_SERVER is not None:
            http_sse._CURRENT_SERVER.reload_token_registry()
            with debug_log.open("a") as f:
                f.write(f"reload_token_registry() completed\n")
                f.flush()
            print(f"[tools.py] Token registry reload complete", file=sys.stderr)
        else:
            with debug_log.open("a") as f:
                f.write(f"WARNING: _CURRENT_SERVER is None\n")
                f.flush()
            print(f"[tools.py] Warning: _CURRENT_SERVER is None, cannot reload", file=sys.stderr)
    except Exception as exc:
        import logging
        import traceback
        with debug_log.open("a") as f:
            f.write(f"EXCEPTION: {exc}\n")
            traceback.print_exc(file=f)
            f.flush()
        logging.warning(f"_reload_token_registry failed: {exc}")
        print(f"[tools.py] _reload_token_registry exception: {exc}", file=sys.stderr)


def _repo_root() -> Path:
    """Resolve workspace root for the current request.
    
    AIPOS-294: Request-level routing — if REQUEST_PROJECT is set (from tool argument or
    capability default), resolve <home>/<project>; otherwise fall back to find_repo_root()
    (legacy/env-based resolution for backward compatibility).
    """
    project = REQUEST_PROJECT.get()
    if project:
        # Request specifies a project -> resolve via home model
        from tools.aipos_cli.workspace_config import resolve_home_root, resolve_project_root
        home = resolve_home_root()
        return resolve_project_root(home, project)
    # Legacy: process-level workspace resolution (AIPOS_WORKSPACE_ROOT or upward search)
    return find_repo_root()


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


# AIPOS-CONN-LOOP-2 ②: 从 transitions.schema 生成 next_action
_TRANSITIONS_CACHE: dict[str, Any] | None = None

def _load_transitions_schema() -> dict[str, Any]:
    """加载 transitions.schema.json（带缓存）。"""
    global _TRANSITIONS_CACHE
    if _TRANSITIONS_CACHE is not None:
        return _TRANSITIONS_CACHE
    try:
        _TRANSITIONS_CACHE = load_schema("transitions")
        return _TRANSITIONS_CACHE
    except Exception:
        return {}

def _generate_next_action(current_state: str, task_mode: str, operation: str) -> dict[str, Any] | None:
    """根据当前态×task_mode 从 transitions.schema 生成 next_action。
    
    Args:
        current_state: 当前队列状态（pending/claimed/returned/completed）
        task_mode: 任务模式（code/docs/governance/config）
        operation: 当前操作（claim/return/audit/finalize/close）
    
    Returns:
        {verb: str, params_hint: str, auth_note: str} 或 None
    """
    schema = _load_transitions_schema()
    if not schema:
        return None
    
    # 根据当前操作决定下一步
    # N1 claim -> N2 execute (报 started)
    if operation == RecordType.CLAIM and current_state == "claimed":
        return {
            "verb": "lybra_task_progress",
            "params_hint": "event_type=started, 开始执行任务",
            "auth_note": "executor 使用 task_progress scope 自报进度",
        }
    
    # N2 execute (completed) -> N3 return
    if operation == "task_progress" and current_state == "claimed":
        return {
            "verb": "lybra_queue_return_dry_run",
            "params_hint": "autonomy_mode=Supervised, 携带 result_summary 和 artifact_refs",
            "auth_note": "executor 使用 queue_return scope 归还工作；328机制：executor持dry_run_token自行confirm, owner_confirmation_token为参数字面量OWNER_CONFIRMED",
            "reminder": "⚠️ 干完必发completed信号(lybra_task_progress event_type=completed), 否则不会自动return",
        }
    
    # N3 return dry-run -> return confirm
    if operation == "return_dry_run" and current_state == "claimed":
        return {
            "verb": "lybra_queue_return_confirm",
            "params_hint": "dry_run_token=<from_dry_run>, owner_confirmation_token=OWNER_CONFIRMED",
            "auth_note": "328机制：executor持dry_run_token自行confirm, owner_confirmation_token为参数字面量OWNER_CONFIRMED（非秘密，公开常量）",
        }
    
    # N3 return confirm -> N4 audit (code) 或 N6 close (docs/governance/config)
    if operation == "return_confirm" and current_state == "returned":
        if task_mode == "code":
            return {
                "verb": "lybra_audit_dispatch",
                "params_hint": "等待 owner/advisor 派审；auditor 认领 R 卡后执行独立审计",
                "auth_note": "audit_dispatch 需要 owner_dispatch scope（advisor角色）",
            }
        else:
            return {
                "verb": "lybra_queue_close",
                "params_hint": "非 code 任务跳过 N4/N5，advisor review 后直接 close",
                "auth_note": "close 操作需要 queue_close scope",
            }
    
    # N4 audit verdict PASS -> N5 finalize (code only)
    if operation == RecordType.AUDIT_VERDICT and task_mode == "code":
        return {
            "verb": "lybra_finalize",
            "params_hint": "commit 到产品仓，push，deploy（如适用）",
            "auth_note": "finalize 需要 audit verdict PASS 前置（FND-14 gate 强制）",
        }
    
    # N5 finalize -> N6 close
    if operation == "finalize":
        return {
            "verb": "lybra_queue_close",
            "params_hint": "创建 closure record，更新治理文档，push 治理仓",
            "auth_note": "close 操作需要 queue_close scope；N6 包含完整治理对账",
        }
    
    return None


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    capability = REQUEST_CAPABILITY.get()
    if isinstance(capability, dict) and capability.get("source") == "service_v0" and isinstance(payload, dict):
        payload = dict(payload)
        # AIPOS-347/R4B-1: scope_basis echoes the REAL-TIME resolved scopes from the
        # roles registry (schema/roles.schema.json), not the token's baked-in
        # `operations` snapshot. The minted operations are echoed separately as
        # `minted_scopes` for debugging/audit.
        _role = str(capability.get("role") or "").strip()
        _role_class = str(capability.get("role_class") or "").strip() or None
        _resolved_scopes = get_role_scopes(_role, role_class=_role_class) if _role else list(capability.get("operations") or [])
        scope_basis: dict[str, Any] = {
            "mode": "service_v0",
            "token_ref": capability.get("token_ref"),
            "role": capability.get("role"),
            "scopes": _resolved_scopes,
            "mcp_endpoint_ref": "local_service_mcp",
        }
        # Echo the minted (baked) operations for audit/debugging — informational only.
        _minted = list(capability.get("operations") or [])
        if _minted and _minted != _resolved_scopes:
            scope_basis["minted_scopes"] = _minted
        # AIPOS-228/229: echo the `projects` dimension + its enforcement marker — ONLY when the
        # capability carries it, so a token without `projects` keeps a byte-identical scope_basis.
        # As of Slice 5 the project gate ENFORCES it at the dispatch choke-point.
        if capability.get("projects"):
            scope_basis["projects"] = list(capability.get("projects") or [])
            scope_basis["projects_enforced"] = True
        payload.setdefault("scope_basis", scope_basis)
        
        # AIPOS-CONN-LOOP-2 ②: 注入 next_action（从 transitions.schema 生成）
        # 从 payload 提取当前状态和操作
        operation = str(payload.get("operation") or "").strip()
        if operation and not is_error:
            # 从 payload 中提取状态信息
            data = payload.get("data") or {}
            task_state = str(data.get("task_state") or data.get("queue_state") or "").strip()
            task_mode = str(data.get("task_mode") or "").strip()
            
            # 如果是 return_confirm，状态从 claimed 转为 returned
            if operation == "queue_return" and payload.get("ok"):
                task_state = "returned"
            
            if task_state and operation:
                # 根据 operation 决定操作类型
                op_type = operation
                if "claim" in operation:
                    op_type = RecordType.CLAIM
                elif "return" in operation:
                    if "confirm" in operation:
                        op_type = "return_confirm"
                    else:
                        op_type = "return_dry_run"
                elif "audit" in operation:
                    op_type = RecordType.AUDIT_VERDICT
                elif "finalize" in operation:
                    op_type = "finalize"
                elif "close" in operation:
                    op_type = "close"
                
                next_action = _generate_next_action(task_state, task_mode, op_type)
                if next_action:
                    payload["next_action"] = next_action
    return {
        "content": [{"type": "text", "text": _json_text(payload)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _teaching_error(
    error_code: str,
    message: str,
    suggested_next_action: str,
    *,
    doc_ref: str = DISCIPLINE_DOC_REF,
    verb_name: str | None = None,
    example_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Teaching error with optional verb parameter shape (AIPOS-R6E ⑧)
    
    Args:
        error_code: Error code
        message: Human-readable error message
        suggested_next_action: What to do next
        doc_ref: Documentation reference
        verb_name: Optional verb name to include parameter shape
        example_args: Optional copyable example arguments
    """
    details: dict[str, Any] = {
        "suggested_next_action": suggested_next_action,
        "doc_ref": doc_ref,
    }
    
    # AIPOS-R6E ⑧: 错误报文自带参数shape+可抄示例
    if verb_name:
        # 从 verbs.schema.json 加载参数定义(简化版:仅包含常见动词)
        param_shape = _get_verb_param_shape(verb_name)
        if param_shape:
            details["parameter_shape"] = param_shape
    
    if example_args:
        details["copyable_example"] = example_args
    
    return _tool_result(
        {
            "ok": False,
            "verdict": Verdict.BLOCK,
            "operation": "mcp_write_tool",
            "error_code": error_code,
            "message": message,
            "suggested_next_action": suggested_next_action,
            "doc_ref": doc_ref,
            "errors": [
                {
                    "category": error_code,
                    "message": message,
                    "details": details,
                }
            ],
        },
        is_error=True,
    )


def _get_verb_param_shape(verb_name: str) -> dict[str, Any] | None:
    """AIPOS-R6E ⑧: 从 verbs.schema.json 获取动词参数shape
    
    简化实现:硬编码常见动词的必填参数。完整实现应从 schema 动态加载。
    """
    # 常见动词的必填参数(简化版)
    verb_shapes = {
        "lybra_queue_amend_dry_run": {
            "required": ["task_id", "amendments", "amendment_reason", "actor"],
            "example": {
                "task_id": "TASK-123",
                "amendments": {"title": "Updated title"},
                "amendment_reason": "Clarify scope per owner feedback",
                "actor": "advisor.lybra",
            }
        },
        "lybra_owner_decision_record": {
            "required": ["decision_id", "task_id", "decision_type", "decision", "rationale", "actor"],
            "example": {
                "decision_id": "DEC-001",
                "task_id": "TASK-123",
                "decision_type": "scope_change",
                "decision": "approved",
                "rationale": "Aligns with project roadmap",
                "actor": "owner",
            }
        },
        "lybra_queue_return_dry_run": {
            "required": ["task_id", "actor", "agent_instance", "owner_policy_ref", "result_summary", "autonomy_mode"],
            "example": {
                "task_id": "TASK-123",
                "actor": "exec.lybra.kiwiai-dev",
                "agent_instance": "exec.lybra.kiwiai-dev",
                "owner_policy_ref": "pol_lybra_dev_9",
                "result_summary": "Completed all deliverables",
                "autonomy_mode": "Supervised",
            }
        },
    }
    return verb_shapes.get(verb_name)


def _error_result(message: str, *, category: str = "VALIDATION_ERROR") -> dict[str, Any]:
    return _tool_result(
        {
            "ok": False,
            "verdict": Verdict.BLOCK,
            "operation": "mcp_tool_call",
            "safety_notice": READ_ONLY_NOTICE,
            "errors": [{"category": category, "message": message, "details": {}}],
        },
        is_error=True,
    )


def _capability_token() -> dict[str, Any]:
    request_capability = REQUEST_CAPABILITY.get()
    if isinstance(request_capability, dict):
        return request_capability
    raw = os.environ.get(CAPABILITY_ENV_VAR, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def request_capability_scope(capability: dict[str, Any] | None) -> Iterator[None]:
    token = REQUEST_CAPABILITY.set(capability if isinstance(capability, dict) else None)
    try:
        yield
    finally:
        REQUEST_CAPABILITY.reset(token)


@contextmanager
def request_project_scope(project: str | None) -> Iterator[None]:
    """AIPOS-294: Set request-level project for workspace routing."""
    token = REQUEST_PROJECT.set(project if project else None)
    try:
        yield
    finally:
        REQUEST_PROJECT.reset(token)


def _capability_has_scope(scope: str) -> bool:
    """AIPOS-347: scope resolved at call time from the roles registry, not from token snapshot.

    The token provides IDENTITY (role + token_ref + expires_at).  The SCOPE is looked up
    from the roles registry (schema/roles.schema.json, via schema_loader.get_role_scopes)
    based on the token's ``role`` field.  This is the single source of truth — same
    registry that ``serve rotate`` mints from (service_mode.ROLE_SPECS is built from it).

    Identity checks preserved (not weakened):
    - ``token_ref`` (or ``token_id``) must be present — proves the token was minted by the system
    - ``expires_at`` must be present and in the future — temporal validity

    Scope resolution (AIPOS-347):
    - If ``role`` is present and resolves in the registry → use registry scopes (real-time)
    - If ``role`` is absent → fall back to ``operations`` (backward compat for legacy tokens)

    This means: changing a role's scopes in the registry takes effect immediately for ALL
    existing tokens of that role — no re-minting required.  Old tokens with stale
    ``operations`` but a valid ``role`` get the CURRENT role scopes, not their baked ones.
    Tokens without ``role`` (legacy) still use their baked ``operations``.
    """
    token = _capability_token()
    # --- Identity checks (unchanged) ---
    if not bool(token.get("token_ref") or token.get("token_id")):
        return False
    expires_at_raw = str(token.get("expires_at") or "").strip()
    if not expires_at_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return False
    # --- AIPOS-347: scope from the roles registry at call time ---
    role = str(token.get("role") or "").strip()
    if role:
        # AIPOS-352: custom roles carry role_class for scope resolution
        role_class = str(token.get("role_class") or "").strip() or None
        role_scopes = get_role_scopes(role, role_class=role_class)
        if role_scopes:
            # Role resolved in registry -> real-time scope resolution
            return scope in role_scopes
        # Role present but NOT in registry -> fail-closed (unknown role denied)
        return False
    # --- Backward compat: no role field → fall back to baked operations ---
    operations = token.get("operations")
    if not isinstance(operations, list) or scope not in operations:
        return False
    return True


def _scope_denied_result() -> dict[str, Any]:
    return _scope_denied_result_for(INTAKE_SCOPE, "intake submit tools")


def _scope_denied_result_for(scope: str, label: str) -> dict[str, Any]:
    # AIPOS-330 S4: Actionable rejection — say who holds this scope.
    from tools.aipos_cli.verb_contract import who_holds_scope
    holders = who_holds_scope(scope)
    holders_text = ", ".join(holders) if holders else "(no role currently holds this scope)"
    return _teaching_error(
        "SCOPE_DENIED",
        f"Connection capability does not include '{scope}'; {label} are not available. "
        f"Roles that hold '{scope}': {holders_text}.",
        (
            f"Bearer transport auth may still be valid, but scoped mutation tools stay hidden until "
            f"LYBRA_CAPABILITY_TOKEN contains operations: [\"{scope}\"]. "
            f"Roles holding this scope: {holders_text}. "
            f"If you are the auditor and the scope is 'audit_verdict', your token DOES hold it — "
            f"the 'owner' in 'owner_confirmation_token' is a parameter name, not a scope requirement. "
            f"Run `lybra mcp doctor` to inspect redacted effective scopes."
        ),
        doc_ref="AIPOS-109 capability token scope-gated tool visibility; AIPOS-330 S4 actionable rejection",
    )


# --- AIPOS-229 (Slice 5): token project ENFORCEMENT -------------------------------------------
# The project gate is a NEW pre-check ADDED IN FRONT of the operation-scope (★A1) gate at the
# single dispatch choke-point (dispatch_tool). It can ONLY deny (narrow); it never bypasses or
# weakens ★A1 and never grants an operation a role lacks. PROJECT_SCOPE_DENIED is a denial, not a
# new operation class. PROJECT_SCOPE_DENIED is a denial category, NOT a mintable operation scope,
# so there is deliberately no *_SCOPE constant for it (it is never granted to a role).


def _capability_in_project(active_project: str) -> bool:
    """True if the request's active_project is within the token's `projects`.

    AIPOS-229 §2: a token WITHOUT a `projects` field is NOT narrowed by project -> True (back-compat
    byte-identical). With `projects`, membership decides. Callers MUST check presence first (R-ii)
    so an absent-`projects` token never triggers active_project resolution.
    """
    projects = _capability_token().get("projects")
    if not projects:
        return True
    return active_project in [str(p) for p in projects]


def _project_scope_denied_result(detail: str) -> dict[str, Any]:
    return _teaching_error(
        "PROJECT_SCOPE_DENIED",
        f"Connection capability is project-scoped and does not authorize this project: {detail}",
        (
            "This local role token carries a `projects` scope that does not include the active "
            "project. Rotate a token scoped to this project (`lybra serve rotate --project <name>`) "
            "or operate within an authorized project."
        ),
        doc_ref="AIPOS-229 token project enforcement",
    )


def _project_gate_denied() -> dict[str, Any] | None:
    """The project gate (AIPOS-229, AIPOS-294, AIPOS-FND-17). Returns a PROJECT_SCOPE_DENIED result to BLOCK,
    or None to fall through to the operation-scope (★A1) gate.

    AIPOS-FND-17: When REQUEST_PROJECT is set (from explicit arg, default_project, or single-project
    inference), validates against that project. Otherwise falls back to resolving active_project
    from the workspace (legacy behavior).

    R-ii ordering (back-compat critical):
      1. `projects` ABSENT -> return None (allow); do NOT resolve active_project at all.
      2. `projects` PRESENT -> resolve active_project; resolution failure -> fail-closed deny
         (reachable ONLY in this branch); else membership test.
    """
    token = _capability_token()
    projects = token.get("projects")
    if not projects:
        return None
    
    # AIPOS-FND-17: Determine which project to validate
    request_project = REQUEST_PROJECT.get()
    if request_project:
        # Request-level routing: validate the explicitly routed project
        target_project = request_project
    else:
        # Legacy path: resolve active_project from the workspace
        # Note: _repo_root() here will use find_repo_root() since REQUEST_PROJECT is None
        try:
            target_project = _resolve_active_project_for(_repo_root(), None)
        except (ValueError, FileNotFoundError, OSError) as exc:
            # AIPOS-FND-17: Improved error message with actionable guidance
            token_projects = list(projects) if isinstance(projects, list) else []
            default_project = str(token.get("default_project") or "").strip()
            guidance = "Pass 'project' argument in tool calls"
            if len(token_projects) == 1:
                guidance += f" (or your single-project token should auto-infer '{token_projects[0]}')"
            elif default_project:
                guidance += f" (or bind default_project='{default_project}' at connection level)"
            elif len(token_projects) > 1:
                guidance += f" or bind a default_project at token mint time. Authorized: {token_projects}"
            return _project_scope_denied_result(
                f"active project could not be resolved ({exc}). {guidance}"
            )
    
    # Check authorization
    if not _capability_in_project(target_project):
        token_projects = list(projects) if isinstance(projects, list) else []
        return _project_scope_denied_result(
            f"project '{target_project}' is not in the token's authorized projects {token_projects}. "
            f"Pass an authorized project explicitly or bind default_project at token mint time."
        )
    return None


def _resolve_request_project(arguments: dict[str, Any]) -> str | None:
    """AIPOS-FND-17: Resolve the target project for this request with inference.
    
    Priority (first match wins):
    1. Explicit `project` argument (per-call override)
    2. Token's `default_project` field (connection-level default)
    3. Single-project inference: if token.projects has exactly one entry, use it
    4. None (fall back to legacy path)
    
    This lets standard MCP clients (Claude Code) use single-gate without injecting
    project per-call. Single-project tokens (e.g., agency→kiwiaiagency) auto-route.
    Multi-project tokens can bind a default_project at mint time or pass explicit project.
    """
    # Priority 1: Explicit project argument
    explicit = str(arguments.get("project") or "").strip()
    if explicit:
        return explicit
    
    # Priority 2 & 3: Connection-level default or single-project inference
    token = _capability_token()
    
    # Priority 2: Token's default_project (connection-level binding)
    default_project = str(token.get("default_project") or "").strip()
    if default_project:
        return default_project
    
    # Priority 3: Single-project inference
    projects = token.get("projects")
    if isinstance(projects, list) and len(projects) == 1:
        single_project = str(projects[0]).strip()
        if single_project:
            return single_project
    
    # Priority 4: No inference possible, fall back to legacy
    return None


def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Single dispatch choke-point for every tools/call (AIPOS-229 R-i / R-α, AIPOS-294).

    AIPOS-294: Request-level project routing. Extract `project` from arguments (or infer from
    capability for single-project tokens), set REQUEST_PROJECT context, then route. The project
    gate runs HERE, before the handler — so it is structurally unavoidable for ALL tools (read +
    write), with ZERO exemptions. The handler's own operation-scope (★A1) check runs after,
    unchanged. Ordering: project routing -> project gate -> ★A1 -> controlled-execute.
    """
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(name)
    
    # AIPOS-294: Extract request project from arguments or infer from capability
    args = arguments if isinstance(arguments, dict) else {}
    request_project = _resolve_request_project(args)
    
    # Set request project context and dispatch
    with request_project_scope(request_project):
        denied = _project_gate_denied()
        if denied is not None:
            return denied
        return handler(args)


def _intake_scope_allowed() -> bool:
    return _capability_has_scope(INTAKE_SCOPE)


def _owner_decision_scope_allowed() -> bool:
    return _capability_has_scope(OWNER_DECISION_SCOPE)


def _queue_claim_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_CLAIM_SCOPE)


def _queue_return_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_RETURN_SCOPE)


def _owner_confirm_scope_allowed() -> bool:
    return _capability_has_scope(OWNER_CONFIRM_SCOPE)


def _draft_publish_scope_allowed() -> bool:
    return _capability_has_scope(DRAFT_PUBLISH_SCOPE)


def _draft_submit_scope_allowed() -> bool:
    return _capability_has_scope(DRAFT_SUBMIT_SCOPE)


def _confirmer_attribution() -> dict[str, Any]:
    """Non-secret identity of the token performing a confirm (AIPOS-197 / F-c12).

    Lets durable provenance distinguish an Owner-role confirmation from a
    non-Owner/agent self-confirmation. Never includes the raw token.
    """
    cap = _capability_token()
    return {
        "confirmer_role": str(cap.get("role") or "") or None,
        "confirmer_token_ref": str(cap.get("token_ref") or cap.get("token_id") or "") or None,
        "confirmer_token_fingerprint": str(cap.get("fingerprint") or "") or None,
    }


def _audit_dispatch_scope_allowed() -> bool:
    return _capability_has_scope(AUDIT_DISPATCH_SCOPE)


def _audit_verdict_scope_allowed() -> bool:
    return _capability_has_scope(AUDIT_VERDICT_SCOPE)


def _queue_close_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_CLOSE_SCOPE)


def _queue_withdraw_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_WITHDRAW_SCOPE)


def _queue_amend_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_AMEND_SCOPE)


def _task_progress_scope_allowed() -> bool:
    return _capability_has_scope(TASK_PROGRESS_SCOPE)


def _check_actor_has_claim(task_id: str, actor: str, repo_root: Path) -> bool:
    """AIPOS-366: Check if the given actor has a valid claim record for the task.
    
    Returns True if there is at least one claim record in records/claims/<task_id>/
    for this actor, False otherwise.
    """
    try:
        records = load_records(repo_root)
        task_records = find_records_for_task(records, task_id)
        claims = task_records.get("claims", [])
        
        # Check if any claim record matches the actor
        for claim in claims:
            claim_actor = claim.get("actor") or claim.get("claimed_by")
            if claim_actor and str(claim_actor).strip() == str(actor).strip():
                return True
        return False
    except Exception:
        # If we can't load records, fail closed (deny body access)
        return False


def _map_controlled_execute_error(response: dict[str, Any], *, dry_run_tool: str = "lybra_intake_submit_dry_run") -> dict[str, Any]:
    blocking = " ".join(str(item) for item in response.get("blocking_reasons", []))
    errors = response.get("errors") if isinstance(response.get("errors"), list) else []
    first_message = ""
    if errors and isinstance(errors[0], dict):
        first_message = str(errors[0].get("message") or "")
    text = f"{blocking} {first_message}".lower()
    if "expired" in text:
        return _teaching_error(
            "TOKEN_EXPIRED",
            "The dry_run_token expired before confirm.",
            f"Run {dry_run_tool} again and confirm with the new dry_run_token.",
        )
    if "snapshot mismatch" in text:
        return _teaching_error(
            "SNAPSHOT_MISMATCH",
            "The workspace state no longer matches the dry-run snapshot.",
            f"Run {dry_run_tool} again before confirming.",
        )
    return _teaching_error(
        "CONTROLLED_EXECUTE_REJECTED",
        first_message or blocking or "Controlled execute rejected the confirm request.",
        f"Inspect the dry-run response, then run {dry_run_tool} again if appropriate.",
    )


def _queue_claim_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref=SUPERVISED_CLAIM_DOC_REF,
    )


def _queue_return_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref="AIPOS-168 Supervised MCP Work Return Path Protocol",
    )


def _audit_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref="AIPOS-177 Audit Dispatch And Verdict Protocol",
    )


def _normalize_selector(args: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any] | None]:
    task_id = str(args.get("task_id") or "").strip()
    task_path = str(args.get("task_path") or args.get("path") or "").strip()
    if bool(task_id) == bool(task_path):
        return None, None, _queue_claim_error(
            "INVALID_TASK_SELECTOR",
            "Exactly one of task_id or task_path is required.",
            "Call lybra_queue_claim_dry_run with exactly one task selector.",
        )
    return task_id or None, task_path or None, None


def _forbidden_queue_claim_fields(args: dict[str, Any]) -> list[str]:
    return sorted(key for key in args if key in FORBIDDEN_QUEUE_CLAIM_FIELDS)


def _forbidden_queue_return_fields(args: dict[str, Any]) -> list[str]:
    return sorted(key for key in args if key in FORBIDDEN_QUEUE_RETURN_FIELDS)


def _forbidden_audit_fields(args: dict[str, Any]) -> list[str]:
    return sorted(key for key in args if key in FORBIDDEN_AUDIT_FIELDS)


def _resolve_claim_instance(agent_instance: str, repo_root: Path) -> dict[str, Any]:
    profiles = load_agent_profiles(repo_root)
    resolution = resolve_instance_id(agent_instance, profiles)
    reg_available = registry_available()
    return {
        "profiles": profiles,
        "resolution": resolution,
        "canonical_agent_instance": resolution.get("canonical_instance_id"),
        "registry_available": reg_available,
    }


def _claim_owner_reasons() -> list[str]:
    return [
        "MCP Supervised queue_claim requires explicit Owner confirmation for this dry-run preview",
    ]


def _return_owner_reasons() -> list[str]:
    return [
        "MCP Supervised queue_return requires explicit Owner confirmation for this dry-run preview",
    ]


def _audit_dispatch_owner_reasons() -> list[str]:
    return [
        "MCP Supervised audit_dispatch requires explicit Owner confirmation for this dry-run preview",
    ]


def _audit_verdict_owner_reasons() -> list[str]:
    return [
        "MCP Supervised audit_verdict requires explicit Owner confirmation for this dry-run preview",
    ]


def _reported_tokens_value(args: dict[str, Any]) -> int | None:
    # AIPOS-250 (capability ledger): agent-REPORTED token count. Coerce to int or drop —
    # the gate records it as-reported, never measures or verifies it (disclosure #15).
    raw = args.get("reported_tokens")
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _coerce_int_or_none(raw: Any) -> int | None:
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _agent_runtime_value(args: dict[str, Any]) -> dict[str, Any] | None:
    # AIPOS-261 (capability ledger, additive): agent-REPORTED runtime bundle for a
    # return — {harness, model_self_reported, tokens_in, tokens_out}. The gate records
    # it as-reported, never measures or verifies it (same disclosure caliber as
    # actual_model/reported_tokens). Returns None when the caller sent nothing usable,
    # so old records simply lack the key (popup shows 未记录).
    raw = args.get("agent_runtime")
    if not isinstance(raw, dict):
        return None
    harness = str(raw.get("harness") or "").strip() or None
    model_self_reported = str(raw.get("model_self_reported") or "").strip() or None
    tokens_in = _coerce_int_or_none(raw.get("tokens_in"))
    tokens_out = _coerce_int_or_none(raw.get("tokens_out"))
    bundle: dict[str, Any] = {}
    if harness:
        bundle["harness"] = harness
    if model_self_reported:
        bundle["model_self_reported"] = model_self_reported
    if tokens_in is not None:
        bundle["tokens_in"] = tokens_in
    if tokens_out is not None:
        bundle["tokens_out"] = tokens_out
    return bundle or None


def _claim_metadata(
    args: dict[str, Any],
    *,
    canonical_agent_instance: str,
    resolution_label: str = "unregistered",
    reg_available: bool = True,
    autonomy_mode: str = "Supervised",
    owner_policy_ref: str | None = None,
    owner_confirmation_required: bool = True,
    owner_confirmation_reasons: list[str] | None = None,
    binding_status: str | None = None,
) -> dict[str, Any]:
    return {
        "surface": "mcp",
        "operation": "queue_claim",
        # AIPOS-250: read the mode from the caller (Supervised default; PreAuthorized only when
        # the gate has confirmed a matching active envelope). Never trust a raw client-supplied
        # PreAuthorized — the gate sets this after a structural match.
        "autonomy_mode": str(autonomy_mode or "Supervised").strip() or "Supervised",
        "owner_policy_ref": str(owner_policy_ref if owner_policy_ref is not None else args.get("owner_policy_ref") or "").strip(),
        "agent_instance": str(args.get("agent_instance") or "").strip(),
        "canonical_agent_instance": canonical_agent_instance,
        # AIPOS-219 P5: FLAT bounded-map provenance marker (depth-1, readable on bare python)
        "identity_provenance": {
            "resolution": resolution_label,
            "registry_available": reg_available,
            # AIPOS-250B: informational binding status (binding_absent/binding_mismatch when
            # PreAuthorized falls back Supervised due to token identity gate).
            **(({"binding_status": binding_status} if binding_status else {})),
        },
        "runtime_profile": str(args.get("runtime_profile") or "").strip() or None,
        "active_session_id": str(args.get("active_session_id") or "").strip() or None,
        "context_bundle_ack": str(args.get("context_bundle_ack") or "").strip() or None,
        "claim_reason": str(args.get("claim_reason") or "").strip() or None,
        # AIPOS-250 (capability ledger): agent-reported, not gate-measured.
        "actual_model": str(args.get("actual_model") or "").strip() or None,
        "reported_tokens": _reported_tokens_value(args),
        "with_records_requested": bool(args.get("with_records", True)),
        "owner_confirmation_required": bool(owner_confirmation_required),
        "owner_confirmation_reasons": list(owner_confirmation_reasons) if owner_confirmation_reasons is not None else _claim_owner_reasons(),
        "lease_path": "claim_only",
        "lease_status": "proposed",
    }


def _return_metadata(
    args: dict[str, Any],
    *,
    canonical_agent_instance: str,
    resolution_label: str = "unregistered",
    reg_available: bool = True,
) -> dict[str, Any]:
    return {
        "surface": "mcp",
        "operation": "queue_return",
        "autonomy_mode": "Supervised",
        "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        "agent_instance": str(args.get("agent_instance") or "").strip(),
        "canonical_agent_instance": canonical_agent_instance,
        # AIPOS-219 P5: FLAT bounded-map provenance marker (depth-1, readable on bare python)
        "identity_provenance": {
            "resolution": resolution_label,
            "registry_available": reg_available,
        },
        "claim_id": str(args.get("claim_id") or "").strip() or None,
        "active_session_id": str(args.get("active_session_id") or "").strip() or None,
        "return_reason": str(args.get("return_reason") or "").strip() or None,
        # AIPOS-250 (capability ledger): agent-reported, not gate-measured (disclosure #15).
        "actual_model": str(args.get("actual_model") or "").strip() or None,
        "reported_tokens": _reported_tokens_value(args),
        # AIPOS-261 (additive): optional agent-reported runtime bundle
        # {harness, model_self_reported, tokens_in, tokens_out}. Recorded as-reported,
        # never verified; absent → None (old records show 未记录 in the popup).
        "agent_runtime": _agent_runtime_value(args),
        # AIPOS-C1 大项B: derived from stage_contract — executor self-confirm → false
        "owner_confirmation_required": False,
        "owner_confirmation_reasons": [],
        "lease_path": "claim_only",
        "lease_status": "proposed",
    }


def _decorate_queue_claim_dry_run(response: dict[str, Any], *, args: dict[str, Any], canonical_agent_instance: str) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, dict):
        data = {}
        response["data"] = data
    data.setdefault("mcp_claim", _claim_metadata(args, canonical_agent_instance=canonical_agent_instance))
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = str(args.get("agent_instance") or "").strip()
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = str(args.get("owner_policy_ref") or "").strip()
    response["claim_policy"] = (
        data.get("updated_frontmatter", {}).get("claim_policy")
        if isinstance(data.get("updated_frontmatter"), dict)
        else None
    )
    response["claim_match_basis"] = response.get("actor_match")
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
        "next_required_action": "separate explicit lease activation before execution",
    }
    response["planned_records"] = []
    # AIPOS-C1 大项B: owner_confirmation_required derived from stage_contract.self_confirm_allowed.
    # Since AIPOS-328 allows executor self-confirm, owner_confirmation_required=false
    # (Owner doesn't need to participate; executor uses OWNER_CONFIRMED literal).
    response["owner_confirmation_required"] = False
    response["owner_confirmation_reasons"] = []
    response["owner_confirmation_token_required"] = OWNER_CONFIRMATION_TOKEN
    response["dry_run_token"] = response.get("dry_run_token") or response.get("dry_run_id")
    response["expires_at"] = response.get("dry_run_expires_at")
    response["confirmation_preview"] = {
        "envelope_version": "aipos-170.claim.v1",
        "operation": "queue_claim",
        "surface": "mcp",
        "autonomy_mode": "Supervised",
        "client_hint": "AIPOS-328: executor自行confirm。使用 lybra_queue_claim_confirm 带上 dry_run_token 与 owner_confirmation_token='OWNER_CONFIRMED'(字面常量,非秘密)。Owner不需参与此步骤。",
        "review_checklist": [
            "Verify task selector and planned pending-to-claimed move.",
            "Verify actor, agent_instance, canonical_agent_instance, and owner_policy_ref.",
            "Verify lease_status remains proposed and no worker is launched.",
            "PreAuthorized模式:直接复制confirm.arguments执行;Supervised模式才需Owner审批。",
        ],
        "task": {
            "task_id": response.get("data", {}).get("task_id") if isinstance(response.get("data"), dict) else args.get("task_id"),
            "task_path": response.get("data", {}).get("source_path") if isinstance(response.get("data"), dict) else args.get("task_path"),
            "current_status": "pending",
        },
        "actor": {
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
        },
        "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        "lease": response["lease_preview"],
        "preview": {
            "planned_writes": response.get("planned_writes", []),
            "planned_moves": response.get("planned_moves", []),
            "planned_records": [],
            "blocking_reasons": response.get("blocking_reasons", []),
            "warnings": response.get("warnings", []),
        },
        "confirm": {
            "tool_name": "lybra_queue_claim_confirm",
            "required_owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            "arguments": {
                "dry_run_token": response.get("dry_run_token"),
                "actor": str(args.get("actor") or "").strip(),
                "agent_instance": str(args.get("agent_instance") or "").strip(),
                "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
                "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            },
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        },
        "copyable_confirm_arguments": {
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
            "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
        },
    }
    return response


def _decorate_queue_return_dry_run(response: dict[str, Any], *, args: dict[str, Any], canonical_agent_instance: str) -> dict[str, Any]:
    data = response.setdefault("data", {})
    if isinstance(data, dict):
        data.setdefault("mcp_return", _return_metadata(args, canonical_agent_instance=canonical_agent_instance))
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = str(args.get("agent_instance") or "").strip()
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = str(args.get("owner_policy_ref") or "").strip()
    response["executor_status"] = "completed"
    response["audit_readiness"] = "ready"
    response["audit_status"] = "pending"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
    }
    response["planned_records"] = []
    # AIPOS-C1 大项B: owner_confirmation_required derived from stage_contract.self_confirm_allowed.
    # AIPOS-328: executor self-confirm → owner_confirmation_required=false
    response["owner_confirmation_required"] = False
    response["owner_confirmation_reasons"] = []
    response["owner_confirmation_token_required"] = OWNER_CONFIRMATION_TOKEN
    response["dry_run_token"] = response.get("dry_run_token") or response.get("dry_run_id")
    response["expires_at"] = response.get("dry_run_expires_at")
    response["confirmation_preview"] = {
        "envelope_version": "aipos-168.v1",
        "operation": "queue_return",
        "surface": "mcp",
        "autonomy_mode": "Supervised",
        "client_hint": "AIPOS-328: executor自行confirm。使用 lybra_queue_return_confirm 带上 dry_run_token 与 owner_confirmation_token='OWNER_CONFIRMED'(字面常量,非秘密)。Owner不需参与此步骤。",
        "review_checklist": [
            "Verify returned work evidence is normalized and non-secret.",
            "Verify actor, agent_instance, canonical_agent_instance, and owner_policy_ref.",
            "Verify executor_status is completed and audit_readiness is ready.",
            "Verify lease_status remains proposed; no audit dispatch, audit PASS, or finalize occurs.",
            "328机制:直接复制confirm.arguments执行,无需等待Owner。",
        ],
        "task": {
            "task_id": response.get("data", {}).get("task_id") if isinstance(response.get("data"), dict) else args.get("task_id"),
            "task_path": response.get("data", {}).get("source_path") if isinstance(response.get("data"), dict) else args.get("task_path"),
            "current_status": "claimed",
        },
        "actor": {
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
        },
        "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        "return": response.get("return_preview"),
        "lease": response["lease_preview"],
        "preview": {
            "planned_writes": response.get("planned_writes", []),
            "planned_moves": response.get("planned_moves", []),
            "planned_records": [],
            "blocking_reasons": response.get("blocking_reasons", []),
            "warnings": response.get("warnings", []),
        },
        "confirm": {
            "tool_name": "lybra_queue_return_confirm",
            "required_owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            "arguments": {
                "dry_run_token": response.get("dry_run_token"),
                "actor": str(args.get("actor") or "").strip(),
                "agent_instance": str(args.get("agent_instance") or "").strip(),
                "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
                "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            },
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        },
        "copyable_confirm_arguments": {
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
            "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
        },
    }
    return response


def _decorate_audit_dry_run(response: dict[str, Any], *, args: dict[str, Any], canonical_agent_instance: str, operation: str) -> dict[str, Any]:
    confirm_tool = "lybra_audit_dispatch_confirm" if operation == RecordType.AUDIT_DISPATCH else "lybra_audit_verdict_confirm"
    owner_reasons = _audit_dispatch_owner_reasons() if operation == RecordType.AUDIT_DISPATCH else _audit_verdict_owner_reasons()
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = str(args.get("agent_instance") or "").strip()
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = str(args.get("owner_policy_ref") or "").strip()
    response["reviewed_executor_instance"] = data.get("reviewed_executor_instance")
    response["reviewed_return_record_ref"] = data.get("reviewed_return_record_ref")
    response["audit_dispatch_record_ref"] = data.get("audit_dispatch_record_ref")
    response["verdict"] = response.get("verdict")
    blocking_text = " ".join(str(item) for item in response.get("blocking_reasons", []))
    if response.get("verdict") == Verdict.BLOCK and not response.get("error_code"):
        if "INDEPENDENCE_FAILED" in blocking_text:
            response["error_code"] = "INDEPENDENCE_FAILED"
        elif "MISSING_RETURN_RECORD" in blocking_text:
            response["error_code"] = "MISSING_RETURN_RECORD"
        elif "MISSING_AUDIT_DISPATCH_RECORD" in blocking_text:
            response["error_code"] = "MISSING_AUDIT_DISPATCH_RECORD"
        elif "MISSING_AUDIT_SESSION_RECORD" in blocking_text:
            response["error_code"] = "MISSING_AUDIT_SESSION_RECORD"
        else:
            response["error_code"] = "AUDIT_ACTION_BLOCKED"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
    }
    # AIPOS-C1 大项B: owner_confirmation_required derived from stage_contract.self_confirm_allowed.
    # AIPOS-328: advisor/auditor self-confirm → owner_confirmation_required=false
    response["owner_confirmation_required"] = False
    response["owner_confirmation_reasons"] = []
    response["owner_confirmation_token_required"] = OWNER_CONFIRMATION_TOKEN
    response["dry_run_token"] = response.get("dry_run_token") or response.get("dry_run_id")
    response["expires_at"] = response.get("dry_run_expires_at")
    response["confirmation_preview"] = {
        "envelope_version": f"aipos-177.{operation}.v1",
        "operation": operation,
        "surface": "mcp",
        "autonomy_mode": "Supervised",
        "client_hint": "AIPOS-328: advisor/auditor自行confirm。使用对应confirm工具(lybra_audit_dispatch_confirm/lybra_audit_verdict_confirm)带上 dry_run_token 与 owner_confirmation_token='OWNER_CONFIRMED'(字面常量,非秘密)。Owner不需参与此步骤。",
        "review_checklist": [
            "Verify actor, agent_instance, canonical_agent_instance, and owner_policy_ref.",
            "Verify this action does not activate leases, finalize, or unblock accepted work.",
            "Verify auditor distinctness and provenance links.",
            "328机制:直接复制confirm.arguments执行,无需等待Owner。",
        ],
        "preview": {
            "planned_writes": response.get("planned_writes", []),
            "planned_moves": response.get("planned_moves", []),
            "blocking_reasons": response.get("blocking_reasons", []),
            "warnings": response.get("warnings", []),
            "data": {
                "reviewed_executor_instance": data.get("reviewed_executor_instance"),
                "reviewed_return_record_ref": data.get("reviewed_return_record_ref"),
                "audit_dispatch_record_ref": data.get("audit_dispatch_record_ref"),
                "audit_task_id": data.get("audit_task_id"),
                "verdict": data.get("verdict"),
            },
        },
        "confirm": {
            "tool_name": confirm_tool,
            "required_owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            "arguments": {
                "dry_run_token": response.get("dry_run_token"),
                "actor": str(args.get("actor") or "").strip(),
                "agent_instance": str(args.get("agent_instance") or "").strip(),
                "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
                "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            },
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        },
        "copyable_confirm_arguments": {
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
            "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
        },
    }
    return response


def _map_owner_decision_dry_run_error(response: dict[str, Any]) -> dict[str, Any]:
    blocking = " ".join(str(item) for item in response.get("blocking_reasons", []))
    if "owner_approval_evidence" in blocking:
        return _teaching_error(
            "MISSING_OWNER_APPROVAL_EVIDENCE",
            "owner_decision_record requires structured owner_approval_evidence.",
            "Add an AIPOS-110 owner_approval_evidence envelope, then call lybra_owner_decision_record_dry_run again.",
            doc_ref="AIPOS-110 Owner Approval Evidence; AIPOS-111 Owner Decision Record; AIPOS-112 writer",
        )
    if "scope" in blocking.lower() or "must match" in blocking:
        return _teaching_error(
            "DECISION_SCOPE_MISMATCH",
            "The decision record scope does not match its evidence or capability scope.",
            "Align applies_to, owner_approval_evidence, and capability_scope, then call lybra_owner_decision_record_dry_run again.",
            doc_ref="AIPOS-111 Owner Decision Record; AIPOS-112 writer",
        )
    return _teaching_error(
        "INVALID_OWNER_DECISION_RECORD",
        blocking or "owner_decision_record dry-run was rejected.",
        "Inspect blocking_reasons, fix the payload, then call lybra_owner_decision_record_dry_run again.",
        doc_ref="AIPOS-111 Owner Decision Record; AIPOS-112 writer",
    )


def lybra_queue_list(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """List queue tasks with project and instance scope filtering.
    
    AIPOS-R1 scope铁律: 按调用者token的(project, instance)返回任务。
    单项目token=该项目;多项目token=显式project参数或推断。
    绝不返回home-root全项目视图。
    """
    args = arguments or {}
    
    # AIPOS-R1: 从token提取project和instance scope
    token = _capability_token()
    projects = token.get("projects")
    agent_instance = token.get("agent_instance")
    
    # 确定project scope
    project_scope: str | None = None
    if projects:
        if isinstance(projects, list):
            if len(projects) == 1:
                # 单项目token: 自动推断为该项目
                project_scope = str(projects[0])
            elif len(projects) > 1:
                # 多项目token: 需要显式参数或default_project
                explicit_project = args.get("project")
                if explicit_project:
                    project_scope = str(explicit_project)
                else:
                    default_project = token.get("default_project")
                    if default_project:
                        project_scope = str(default_project)
                    else:
                        # 多项目token无显式参数且无default_project: 必须报错
                        # (不尝试推断active project,因为租户token可能没有workspace访问权限)
                        raise ValueError(
                            f"Multi-project token (projects={projects}) requires explicit 'project' argument "
                            f"or 'default_project' in token. Cannot infer project scope."
                        )
    
    # Instance scope用于held检查(在classify时使用)
    instance_scope = str(agent_instance) if agent_instance else None
    
    # AIPOS-R1-FIX2: 根据project_scope动态解析workspace_root
    # 租户token可能没有全局workspace访问权限,必须从project_scope推导
    if project_scope:
        from tools.aipos_cli.workspace_config import resolve_home_root, resolve_project_root
        home = resolve_home_root()
        repo_root = resolve_project_root(home, project_scope)
    else:
        # Legacy: 无project scope时使用全局workspace
        repo_root = _repo_root()
    
    return _tool_result(get_queue(
        repo_root=repo_root,
        project_scope=project_scope,
        instance_scope=instance_scope,
    ))


def lybra_project_status(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-242 (Slice D): the gate's OWN project view — read-only self-report.

    The single source of truth for the project view is the GATE, not the client (F-o3-2 /
    F-o3-18): clients must not guess the home with their own env/defaults, and `/project switch`
    must verify against THIS report instead of optimistically claiming the gate followed. Zero
    write; reuses the exact resolution the enforcement path uses (`_resolve_active_project_for`)
    and the same "established project" criterion as the single-project fallback
    (`_project_candidates`: 5_tasks/queue + project.json). Registered like every other tool —
    project-gated at the dispatch choke-point, NO exemption (an out-of-scope active project
    denies this tool too; the standardized deny message names the resolved project, which IS the
    honest signal clients surface).
    """
    _ = arguments or {}
    home_root = resolve_home_root()
    active: str | None = None
    resolution_error: str | None = None
    dispatch_mode = "auto"
    try:
        active = _resolve_active_project_for(_repo_root(), None)
        from tools.aipos_cli.workspace_config import get_dispatch_mode
        dispatch_mode = get_dispatch_mode(_repo_root())
    except (ValueError, FileNotFoundError, OSError) as exc:
        resolution_error = str(exc)
    return _tool_result(
        {
            "ok": True,
            "source": "gate",
            "home_root": str(home_root),
            "active_project": active,
            "resolution_error": resolution_error,
            "projects": _project_candidates(home_root),
            "workspace_root": str(_repo_root()),
            "dispatch_mode": dispatch_mode,
        }
    )


def lybra_gate_version(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-369: report the gate's runtime version (git commit + VERSION file).

    Returns the git commit hash the gate is ACTUALLY running from (live runtime snapshot),
    not the working tree. Used by lybra-deploy to verify deployment took effect via HTTP
    endpoint probe (not in-process import). Read-only; no auth required (deployment health check).
    
    AIPOS-FND-8: Fixed to read deployment snapshot commit (from VERSION file or product repo),
    not workspace (治理仓) HEAD. Uses __file__ to locate actual loaded code (follows symlinks
    to .deploy/current), then reads VERSION from that root.
    """
    _ = arguments or {}
    import subprocess
    from pathlib import Path
    import re

    version_info: dict[str, Any] = {"ok": True, "source": "gate_runtime"}
    
    # Find the directory where THIS code is actually loaded from (follows symlinks)
    # Gate loads tools.mcp_server from .deploy/current (via symlink), not workspace
    code_file = Path(__file__).resolve()  # resolve() follows symlinks
    # Go up: tools.py -> mcp_server/ -> tools/ -> repo root
    code_root = code_file.parent.parent.parent
    version_info["runtime_directory"] = str(code_root)

    # Priority 1: Read VERSION file's git_commit field (deployment snapshot ground truth)
    version_file = code_root / "VERSION"
    if version_file.exists():
        try:
            version_content = version_file.read_text().strip()
            version_info["version_file"] = version_content
            
            # Parse YAML-like format: git_commit: <hash>
            match = re.search(r'^git_commit:\s*([a-f0-9]{40})\s*$', version_content, re.MULTILINE)
            if match:
                commit = match.group(1)
                version_info["git_commit"] = commit
                version_info["git_commit_short"] = commit[:7]
                version_info["source"] = "VERSION_file"
            
            # AIPOS-R6S 大项B③: 自曝 deployment_provenance + 授权引用
            prov = re.search(r'^deployment_provenance:\s*(\S+)\s*$', version_content, re.MULTILINE)
            if prov:
                version_info["deployment_provenance"] = prov.group(1).strip()
            auth_type = re.search(r'^authorization_type:\s*(\S+)\s*$', version_content, re.MULTILINE)
            if auth_type:
                version_info["authorization_type"] = auth_type.group(1).strip()
            auth_ref = re.search(r'^authorization_ref:\s*(.+?)\s*$', version_content, re.MULTILINE)
            if auth_ref:
                version_info["authorization_ref"] = auth_ref.group(1).strip()
        except Exception as e:
            version_info["version_file_error"] = str(e)

    # Priority 2: Fallback to git in code root (non-deployed / dev mode)
    # Use code_root (product repo), NOT workspace, to avoid reading 治理仓 HEAD
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=code_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            version_info["git_commit"] = commit
            version_info["git_commit_short"] = commit[:7]
            version_info["source"] = "git_code_root"
        else:
            version_info["git_error"] = result.stderr.strip()
    except Exception as e:
        version_info["git_error"] = str(e)

    return _tool_result(version_info)


def _gate_runtime_root() -> Path:
    """AIPOS-C4B: gate 自身的运行时快照根 (.deploy/current, 经 symlink 解析)。

    与 lybra_gate_version 同源: tools.py 被加载自 .deploy/current,
    __file__.resolve() 跟随 symlink 得到真实快照目录, 分发清单/文件内容从此读。
    """
    return Path(__file__).resolve().parent.parent.parent


def lybra_distribution_manifest(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-C4B 大项A②: 分发清单拉取面(gate 被动, 只读)。

    返回调用者角色(token 的 role)应得的分发清单: 每个分发物的文件列表 + sha256 哈希
    + 源 commit。工位侧 `lybra sync` 据此对比本地 _distributed 并只拉差异。

    红线: 本动词被动、只读, 不发治理内容, 不做任何推送。范围按角色 scope ——
    只返回调用者自己的角色清单, 不信任参数里的 role。
    """
    _ = arguments or {}
    cap = _capability_token()
    role = str(cap.get("role") or "").strip()
    if not role:
        return _error_result("lybra_distribution_manifest: cannot resolve caller role", category="SCOPE_ERROR")

    from tools.distribution_manifest import build_role_manifest, get_product_commit
    root = _gate_runtime_root()
    try:
        role_manifest = build_role_manifest(root, role)
    except FileNotFoundError as e:
        return _error_result(f"distribution source missing in gate runtime: {e}", category="DISTRIBUTION_ERROR")
    except ValueError as e:
        return _error_result(f"distribution manifest build failed: {e}", category="DISTRIBUTION_ERROR")

    payload = {
        "ok": True,
        "manifest_version": 1,
        "product_commit": get_product_commit(root),
        "role": role,
        "harness": role_manifest["harness"],
        "distributions": role_manifest["distributions"],
    }
    return _tool_result(payload)


def lybra_distribution_fetch(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-C4B 大项A②: 分发文件内容拉取(gate 被动, 只读)。

    按 distribution_id + 文件相对路径返回 base64 内容。路径经分发清单校验,
    越界(path traversal / 非清单内文件)即拒。工位发起 pull; 本动词零推送。
    """
    args = arguments or {}
    cap = _capability_token()
    role = str(cap.get("role") or "").strip()
    if not role:
        return _error_result("lybra_distribution_fetch: cannot resolve caller role", category="SCOPE_ERROR")

    distribution_id = str(args.get("distribution_id") or "").strip()
    paths = args.get("paths")
    if not distribution_id or not isinstance(paths, list) or not paths:
        return _error_result("lybra_distribution_fetch requires distribution_id + paths[]")

    from tools.distribution_manifest import build_role_manifest
    import base64
    root = _gate_runtime_root()
    try:
        role_manifest = build_role_manifest(root, role)
    except (FileNotFoundError, ValueError) as e:
        return _error_result(f"distribution manifest build failed: {e}", category="DISTRIBUTION_ERROR")

    dist = next((d for d in role_manifest["distributions"] if d["distribution_id"] == distribution_id), None)
    if dist is None:
        return _error_result(f"distribution_id not in role manifest: {distribution_id}", category="DISTRIBUTION_ERROR")

    # 合法路径集合(白名单): 只允许清单内声明的文件
    allowed: dict[str, str] = {}
    for f in dist["files"]:
        allowed[f["path"]] = f["sha256"]

    src_root = (root / dist["source_path"]).resolve()
    if dist.get("source_is_file"):
        # 文件型分发物(charter/schema): source_path 就是文件本身, 读面是它的父目录
        src_root = src_root.parent
    files: list[dict[str, Any]] = []
    for rel in paths:
        rel_s = str(rel)
        if rel_s not in allowed:
            return _error_result(f"path not in manifest for {distribution_id}: {rel_s}", category="DISTRIBUTION_ERROR")
        abs_path = (src_root / rel_s).resolve()
        # path traversal 防护: 解析后必须仍在源目录内
        if not str(abs_path).startswith(str(src_root) + "/") or not abs_path.is_file():
            return _error_result(f"path escapes distribution source: {rel_s}", category="DISTRIBUTION_ERROR")
        content = abs_path.read_bytes()
        files.append({"path": rel_s, "content_b64": base64.b64encode(content).decode("ascii")})

    return _tool_result({"ok": True, "distribution_id": distribution_id, "files": files})


def lybra_validate(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = arguments or {}
    return _tool_result(get_validate(repo_root=_repo_root()))


def lybra_task_preview(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    task_id = args.get("task_id")
    path = args.get("path")
    include_body = bool(args.get("include_body", False))
    if bool(str(task_id or "").strip()) == bool(str(path or "").strip()):
        return _error_result("Exactly one of task_id or path is required")
    if include_body and not _queue_claim_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLAIM_SCOPE, "lybra_task_preview with include_body")
    
    # AIPOS-366: claim-before-work gate — body requires a valid claim record
    if include_body:
        # Determine actor: explicit arg, or infer from capability token
        actor = str(args.get("actor")).strip() if args.get("actor") else None
        if not actor:
            cap = _capability_token()
            actor = str(cap.get("agent_instance") or cap.get("role") or "").strip() or None
        
        if actor and task_id:
            # task_id is the primary selector; if path is used, we'd need to resolve it to task_id first
            resolved_task_id = str(task_id).strip() if task_id else None
            if resolved_task_id and not _check_actor_has_claim(resolved_task_id, actor, _repo_root()):
                return _tool_result(
                    {
                        "ok": False,
                        "verdict": Verdict.BLOCK,
                        "error_code": "CLAIM_REQUIRED",
                        "operation": "lybra_task_preview",
                        "message": (
                            f"Task body access requires a valid claim record for actor '{actor}' on task '{resolved_task_id}'. "
                            "Claim the task first using the claim workflow before requesting include_body=true."
                        ),
                        "doc_ref": "AIPOS-366 claim-before-work hard enforcement",
                        "suggested_next_action": "Claim the task before requesting body content.",
                    },
                    is_error=True,
                )
    
    response = get_preview(
        task_id=str(task_id).strip() if task_id else None,
        path=str(path).strip() if path else None,
        actor=str(args.get("actor")).strip() if args.get("actor") else None,
        repo_root=_repo_root(),
        include_body=include_body,
    )
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_return_content(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-320: read-only tool that returns the RETURN.md body for a task card.
    Path is strictly confined to task_cards/<task_id>/RETURN.md within the gate workspace.
    Requires queue_claim scope (held by executor and auditor tokens)."""
    if not _queue_claim_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLAIM_SCOPE, "lybra_return_content")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _error_result("task_id is required", category="VALIDATION_ERROR")
    # Path escape prevention: task_id must not contain path separators or traversal
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        return _error_result(
            f"task_id contains invalid characters for path construction: {task_id!r}",
            category="PATH_ESCAPE_BLOCKED",
        )
    repo_root = _repo_root()
    return_body_rel = f"task_cards/{task_id}/RETURN.md"
    return_body_path = repo_root / return_body_rel
    # Double-check path confinement after resolution
    try:
        return_body_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return _error_result(
            f"return_body path escapes workspace: {return_body_rel}",
            category="PATH_ESCAPE_BLOCKED",
        )
    if not return_body_path.is_file():
        return _error_result(
            f"No RETURN.md found for task {task_id} at {return_body_rel}. "
            "The task may not have been returned yet, or the return did not include a return_body.",
            category="RETURN_CONTENT_NOT_FOUND",
        )
    content = return_body_path.read_text(encoding="utf-8")
    return _tool_result(
        {
            "ok": True,
            "verdict": Verdict.PASS,
            "operation": "return_content",
            "task_id": task_id,
            "return_body_path": return_body_rel,
            "return_body": content,
            "return_body_size": len(content),
        },
        is_error=False,
    )


def lybra_context_pack_build(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    path = str(args.get("path") or "").strip()
    orchestration_id = str(args.get("orchestration_id") or "").strip()
    if sum(bool(value) for value in (task_id, path, orchestration_id)) != 1:
        return _error_result("Exactly one of task_id, path, or orchestration_id is required")
    response = get_context_pack_preview(
        task_id=task_id or None,
        path=path or None,
        orchestration_id=orchestration_id or None,
        repo_root=_repo_root(),
    )
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_intake_submit_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _intake_scope_allowed():
        return _scope_denied_result()
    args = arguments or {}
    response = submit_external_intake(args, dry_run=True, repo_root=_repo_root(), actor=str(args.get("actor") or "mcp.client"))
    text = " ".join(str(item) for item in response.get("blocking_reasons", []))
    if response.get("verdict") == Verdict.BLOCK and "Invalid source_tag format" in text:
        return _teaching_error(
            "INVALID_SOURCE",
            "source_tag is invalid for external intake.",
            "Use a lowercase registered source_tag from external_intake_registry.md, then call lybra_intake_submit_dry_run again.",
            doc_ref="AIPOS-106 External Intake Registry Protocol; AIPOS-107 source_tag field",
        )
    return _tool_result(response, is_error=not bool(response.get("ok", False)) or response.get("verdict") == Verdict.BLOCK)


def lybra_intake_submit_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _intake_scope_allowed():
        return _scope_denied_result()
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _teaching_error(
            "MISSING_DRY_RUN_TOKEN",
            "lybra_intake_submit_confirm requires dry_run_token from a prior dry-run response.",
            "Call lybra_intake_submit_dry_run first, then pass its dry_run_token to lybra_intake_submit_confirm.",
        )
    response = execute_dry_run(
        dry_run_token,
        str(args.get("actor") or "mcp.client"),
        owner_confirmation_token=str(args.get("owner_confirmation_token") or "") or None,
        repo_root=_repo_root(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_intake_submit_dry_run")
    return _tool_result(response, is_error=False)


def lybra_owner_decision_record_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _owner_decision_scope_allowed():
        return _scope_denied_result_for(OWNER_DECISION_SCOPE, "owner decision record tools")
    args = arguments or {}
    response = record_owner_decision(args, dry_run=True, repo_root=_repo_root(), actor=str(args.get("actor") or "mcp.client"))
    if response.get("verdict") == Verdict.BLOCK:
        return _map_owner_decision_dry_run_error(response)
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_owner_decision_record_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _owner_decision_scope_allowed():
        return _scope_denied_result_for(OWNER_DECISION_SCOPE, "owner decision record tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _teaching_error(
            "MISSING_DRY_RUN_TOKEN",
            "lybra_owner_decision_record_confirm requires dry_run_token from a prior dry-run response.",
            "Call lybra_owner_decision_record_dry_run first, then pass its dry_run_token to lybra_owner_decision_record_confirm.",
            doc_ref="AIPOS-109 MCP-native discipline; AIPOS-112 owner_decision_record writer",
        )
    owner_confirmation_token = str(args.get("owner_confirmation_token") or "") or None
    # AIPOS-250: when this owner decision GRANTS a PreAuthorized autonomy envelope, the confirm is
    # the ONE Owner hand-confirmation that arms the envelope (red line 1). Gate it exactly like the
    # other consequential confirms — additionally require the Owner-only owner_confirm scope AND the
    # explicit OWNER_CONFIRMED token, so an autonomy envelope can never be armed without the Owner.
    token = get_dry_run(dry_run_token)
    plan_data = token.plan.get("data") if token is not None and isinstance(token.plan, dict) else None
    grants_policy = bool(plan_data.get("autonomy_policy_grant")) if isinstance(plan_data, dict) else False
    if grants_policy:
        if not _owner_confirm_scope_allowed():
            return _scope_denied_result_for(OWNER_CONFIRM_SCOPE, "owner decision record autonomy-policy grant (Owner-only)")
        if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
            return _teaching_error(
                "OWNER_CONFIRMATION_REQUIRED",
                "Arming a PreAuthorized autonomy envelope requires owner_confirmation_token: OWNER_CONFIRMED.",
                "Present the policy grant preview to the Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
            )
    response = execute_dry_run(
        dry_run_token,
        str(args.get("actor") or "mcp.client"),
        owner_confirmation_token=owner_confirmation_token,
        repo_root=_repo_root(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_owner_decision_record_dry_run")
    return _tool_result(response, is_error=False)


def _draft_publish_owner_reasons() -> list[str]:
    return ["Gated MCP draft_publish requires explicit Owner confirmation for this dry-run preview"]


def lybra_draft_publish_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    # AIPOS-204 / F-c4: gated publish surface. Visible with the draft_publish scope (the
    # planner / AI-authoring publisher).
    # AIPOS-342 (甲案): owner_confirmation_required set to False — publishing a card is NOT
    # a gate (Owner裁定 DL 05-10). The card lands in pending and waits for an agent to claim;
    # the real gates (envelope, red lines, audit, owner_verify, deploy) are unchanged.
    if not _draft_publish_scope_allowed():
        return _scope_denied_result_for(DRAFT_PUBLISH_SCOPE, "gated draft publish tools")
    args = arguments or {}
    path = str(args.get("path") or "").strip()
    if not path:
        return _teaching_error(
            "DRAFT_PATH_REQUIRED",
            "lybra_draft_publish_dry_run requires path (the draft to publish).",
            "Pass path to the draft under 5_tasks/drafts/, then review the preview before confirm.",
        )
    response = publish_draft(
        path,
        dry_run=True,
        repo_root=_repo_root(),
        actor=str(args.get("actor") or "mcp.client"),
        owner_confirmation_required_override=False,
        owner_confirmation_reasons_override=None,
    )
    return _tool_result(response, is_error=not bool(response.get("ok", False)) or response.get("verdict") == Verdict.BLOCK)


def lybra_draft_publish_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _draft_publish_scope_allowed():
        return _scope_denied_result_for(DRAFT_PUBLISH_SCOPE, "gated draft publish tools")
    # AIPOS-342 (甲案): owner_confirm scope check REMOVED from draft_publish_confirm.
    # Owner裁定 (DL 05-10): publishing a card is NOT a gate — the card lands in pending
    # and waits for an agent to claim; the real gates (envelope, red lines, independent
    # audit, owner_verify, deploy) are unchanged. Requiring Owner to confirm every
    # publish was proven unworkable. The planner (now holding draft_publish) can complete
    # the full publish flow without the Owner manually running a confirm command.
    # The owner_confirmation_token parameter is no longer required for draft_publish.
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _teaching_error(
            "MISSING_DRY_RUN_TOKEN",
            "lybra_draft_publish_confirm requires dry_run_token from a prior lybra_draft_publish_dry_run response.",
            "Call lybra_draft_publish_dry_run first, review the preview, then confirm with its dry_run_token.",
        )
    # AIPOS-342 (甲案): owner_confirmation_token is no longer required for draft_publish.
    # Publishing a card is NOT a gate (Owner裁定 DL 05-10). Pass empty token; execute_dry_run
    # will not require it because the dry-run plan has owner_confirmation_required=False.
    response = execute_dry_run(
        dry_run_token,
        str(args.get("actor") or "mcp.client"),
        owner_confirmation_token=None,
        repo_root=_repo_root(),
        confirmer=_confirmer_attribution(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_draft_publish_dry_run")
    response["surface"] = "mcp"
    response["provenance"] = {
        "event_type": "mcp_draft_publish",
        "actor": str(args.get("actor") or "mcp.client"),
        "surface": "mcp",
        "transport": "mcp",
        "result": response.get("verdict"),
        "dry_run_id": dry_run_token,
    }
    return _tool_result(response, is_error=False)


def lybra_draft_submit_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    # AIPOS-249 (planner slice): land a task-card DRAFT into 5_tasks/drafts/. Visible with the
    # draft_submit scope (the planner). Reuses the existing draft_create controlled-execute op —
    # the target path is DRAFTS_DIR / draft_slug(task_id).md (constant dir + regex-locked slug,
    # draft_validator.py), so the caller passes NO path field and CANNOT write outside drafts/.
    if not _draft_submit_scope_allowed():
        return _scope_denied_result_for(DRAFT_SUBMIT_SCOPE, "planner draft submit tools")
    args = arguments or {}
    response = create_draft(args, dry_run=True, repo_root=_repo_root(), actor=str(args.get("actor") or "mcp.client"))
    return _tool_result(response, is_error=not bool(response.get("ok", False)) or response.get("verdict") == Verdict.BLOCK)


def lybra_draft_submit_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    # AIPOS-249: submit confirm does NOT require owner_confirm — a draft is a PROPOSAL, not truth.
    # The Owner gate is at PUBLISH (drafts -> queue/pending = lybra_draft_publish, which the planner
    # lacks AND which requires owner_confirm). So a planner can fill the drafts zone autonomously,
    # but landing into truth is structurally Owner-gated.
    if not _draft_submit_scope_allowed():
        return _scope_denied_result_for(DRAFT_SUBMIT_SCOPE, "planner draft submit tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _teaching_error(
            "MISSING_DRY_RUN_TOKEN",
            "lybra_draft_submit_confirm requires dry_run_token from a prior lybra_draft_submit_dry_run response.",
            "Call lybra_draft_submit_dry_run first, review the rendered draft, then confirm with its dry_run_token.",
        )
    response = execute_dry_run(
        dry_run_token,
        str(args.get("actor") or "mcp.client"),
        owner_confirmation_token=None,
        repo_root=_repo_root(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_draft_submit_dry_run")
    return _tool_result(response, is_error=False)


def _match_claim_envelope(
    repo_root: Path,
    *,
    owner_policy_ref: str,
    task_id: str | None,
    task_path: str | None,
    canonical_agent_instance: str,
    actor: str,
) -> tuple[str | None, str | None]:
    """Return (owner_policy_ref, binding_status) iff it names an active Owner-signed envelope that strictly
    matches this claim; else (None, binding_status_reason). Loading a policy for a ref that
    does not resolve returns (None, None) (★A1 anti-forgery: a ref to a nonexistent/unauthorized policy
    grants nothing). binding_status is informational for identity_provenance when falling back Supervised."""
    # AIPOS-250B: PreAuthorized identity gate (zero-dependency, token authority).
    # The claim's agent_instance (and actor) MUST match the canonical instance bound to the
    # request token in connection.json. No binding or mismatch → fall back Supervised.
    # This is the authoritative identity source (Owner-minted token binding), not self-reported.
    # AIPOS-PRERELEASE-1: trace every outer envelope condition to stderr (→ journald) as
    # gate-side evidence (repo_root / capability identity / binding / policy load / snapshot /
    # queue_state / released count / final release switch). The inner match_claim_envelope
    # emits its own per-predicate trace. Behavior is unchanged — only observability is added.
    cap = _capability_token()
    bound = str(cap.get("agent_instance") or "").strip()
    claiming_role = str(cap.get("role") or "").strip()
    trace = {
        "phase": "_match_claim_envelope",
        "repo_root": str(repo_root),
        "request_project": REQUEST_PROJECT.get(),
        "owner_policy_ref": owner_policy_ref,
        "task_id": task_id,
        "task_path": task_path,
        "canonical_agent_instance": canonical_agent_instance,
        "actor": actor,
        "capability": {
            "agent_instance": bound,
            "role": claiming_role,
            "projects": cap.get("projects"),
            "default_project": cap.get("default_project"),
        },
    }

    def _emit(stage, matched_policy_id, binding_status, **extra):
        t = dict(trace)
        t["stage"] = stage
        t["matched_policy_id"] = matched_policy_id
        t["binding_status"] = binding_status
        t.update(extra)
        trace_envelope(t)

    if not bound:
        # Token has no agent_instance binding → PreAuthorized unavailable (backward-compatible).
        _emit("identity_gate", None, "binding_absent", reason="token has no agent_instance binding")
        return None, "binding_absent"
    if bound != canonical_agent_instance or bound != actor:
        # Identity mismatch: claim self-report doesn't match token authority → fall back Supervised.
        _emit("identity_gate", None, "binding_mismatch", reason="claim self-report does not match token-bound agent_instance")
        return None, "binding_mismatch"
    policy = load_policy(repo_root, owner_policy_ref)
    if policy is None:
        _emit("policy_load", None, None, policy_loaded=False)
        return None, None
    snapshot = load_task_snapshot(repo_root, task_id=task_id, path=task_path)
    if snapshot is None:
        _emit("snapshot_load", None, None, policy_loaded=True, snapshot_loaded=False)
        return None, None
    # Envelope auto-release covers only pending (claimable) queue tasks — anything else drops to
    # the Supervised path (which will itself surface why the task is not claimable).
    queue_state = str(snapshot.get("queue_state") or "").strip()
    if queue_state != "pending":
        _emit("queue_state_guard", None, None, policy_loaded=True, snapshot_loaded=True,
              queue_state=queue_state, reason="envelope covers pending tasks only")
        return None, None
    released = count_preauthorized_claims(repo_root, owner_policy_ref)
    # AIPOS-363 S4: carry the calling role so an envelope may name an AIPOS-352 custom role
    # (e.g. agent_or_role: kaia-asst) and still match an agent claiming under that role.
    # The role is read from the Owner-minted capability token (authoritative, not self-reported).
    matched, inner_reason = match_claim_envelope(
        policy=policy,
        task_id=str(snapshot.get("task_id") or task_id or ""),
        task_mode=str(snapshot.get("task_mode") or ""),
        project=str(snapshot.get("project") or ""),
        agent_instance=canonical_agent_instance,
        actor=actor,
        now=datetime.now(timezone.utc),
        released_count=released,
        claiming_role=claiming_role or None,
    )
    _emit("final", owner_policy_ref if matched else None, None,
          policy_loaded=True, snapshot_loaded=True, queue_state=queue_state,
          released_count=released, inner_matched=matched, inner_reason=inner_reason,
          release_switch=matched)
    return (owner_policy_ref, None) if matched else (None, None)


def _preauthorized_claim_autorelease(
    *,
    args: dict[str, Any],
    repo_root: Path,
    task_id: str | None,
    task_path: str | None,
    canonical_agent_instance: str,
    policy_id: str,
    resolution_label: str,
    reg_available: bool,
) -> dict[str, Any]:
    """One-stage PreAuthorized release: run a claim dry-run with owner_confirmation NOT required
    (the envelope already authorized it), then immediately execute it. The executor never calls
    claim_confirm and never holds owner_confirm scope — the gate performs the write as the executor
    of the Owner-signed policy. The claim record self-attributes autonomy_mode=PreAuthorized +
    owner_policy_ref=<policy_id>."""
    confirmer = {
        # The runtime "confirmer" is the Owner-signed policy, not any live token — honest
        # attribution: no agent pressed a button at runtime (red line 1).
        "confirmer_role": "autonomy_policy:PreAuthorized",
        "confirmer_token_ref": policy_id,
        "confirmer_token_fingerprint": "",
    }
    claim_meta = _claim_metadata(
        args,
        canonical_agent_instance=canonical_agent_instance,
        resolution_label=resolution_label,
        reg_available=reg_available,
        autonomy_mode=AUTONOMY_MODE_PREAUTHORIZED,
        owner_policy_ref=policy_id,
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
    )
    claim_meta["confirmer"] = confirmer
    dry = claim_task(
        task_id=task_id,
        path=task_path,
        actor=canonical_agent_instance,
        dry_run=True,
        with_records=False,
        repo_root=repo_root,
        owner_confirmation_required_override=False,
        owner_confirmation_reasons_override=[],
        mcp_claim_metadata=claim_meta,
    )
    dry_run_token = str(dry.get("dry_run_token") or "").strip()
    if dry.get("verdict") == Verdict.BLOCK or not dry_run_token:
        # Not auto-releasable (e.g. the task is no longer claimable). Surface the preview/blocks.
        decorated = _decorate_queue_claim_dry_run(dry, args=args, canonical_agent_instance=canonical_agent_instance)
        return _tool_result(decorated, is_error=True)
    executed = execute_dry_run(
        dry_run_token,
        canonical_agent_instance,
        owner_confirmation_token=None,
        repo_root=repo_root,
        confirmer=confirmer,
    )
    if not executed.get("ok", False):
        return _map_controlled_execute_error(executed, dry_run_tool="lybra_queue_claim_dry_run")
    executed["surface"] = "mcp"
    executed["autonomy_mode"] = AUTONOMY_MODE_PREAUTHORIZED
    executed["agent_instance"] = str(args.get("agent_instance") or "").strip()
    executed["canonical_agent_instance"] = canonical_agent_instance
    executed["owner_policy_ref"] = policy_id
    executed["owner_confirmation_required"] = False
    executed["preauthorized_release"] = True
    executed["lease_status"] = "proposed"
    executed["lease_path"] = "claim_only"
    executed["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
        "next_required_action": "separate explicit lease activation before execution",
    }
    executed["provenance"] = {
        "event_type": "mcp_queue_claim",
        "actor": canonical_agent_instance,
        "actor_instance_id": canonical_agent_instance,
        "surface": "mcp",
        "transport": "mcp",
        "owner_policy_ref": policy_id,
        "autonomy_mode": AUTONOMY_MODE_PREAUTHORIZED,
        "result": executed.get("verdict"),
        "dry_run_id": dry_run_token,
    }
    return _tool_result(executed, is_error=False)


def lybra_queue_claim_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_claim_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLAIM_SCOPE, "supervised queue claim tools")
    args = arguments or {}
    forbidden = _forbidden_queue_claim_fields(args)
    if forbidden:
        return _queue_claim_error(
            "UNSUPPORTED_QUEUE_CLAIM_FIELD",
            f"Supervised MCP queue_claim does not accept these fields: {', '.join(forbidden)}.",
            "Remove automatic, batch, credential, bearer-token, raw prompt, and raw response fields; then run dry-run again.",
        )
    requested_mode = str(args.get("autonomy_mode") or "").strip()
    if requested_mode not in {AUTONOMY_MODE_SUPERVISED, AUTONOMY_MODE_PREAUTHORIZED}:
        return _queue_claim_error(
            "INVALID_AUTONOMY_MODE",
            "lybra_queue_claim_dry_run supports autonomy_mode: Supervised or PreAuthorized.",
            "Use Supervised (per-task Owner confirm) or PreAuthorized (auto-release only when the gate matches an Owner-signed envelope). Delegated and Standing remain behind separate Owner gates.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _queue_claim_error("ACTOR_REQUIRED", "actor is required.", "Pass the visible claimant actor.")
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not owner_policy_ref:
        return _queue_claim_error(
            "OWNER_POLICY_REF_REQUIRED",
            "owner_policy_ref is required for Supervised MCP queue_claim.",
            "Pass the Owner approval or policy reference authorizing this supervised session.",
        )
    agent_instance = str(args.get("agent_instance") or "").strip()
    if not agent_instance:
        return _queue_claim_error(
            "INSTANCE_REQUIRED",
            "agent_instance is required and must resolve to one canonical concrete instance.",
            "Pass the canonical agent_instance or a non-ambiguous legacy instance ID.",
        )
    task_id, task_path, selector_error = _normalize_selector(args)
    if selector_error is not None:
        return selector_error

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous":
        return _queue_claim_error(
            "AMBIGUOUS_LEGACY_INSTANCE",
            f"agent_instance resolves ambiguously: {agent_instance}.",
            "Use a canonical opaque agent_instance before requesting claim.",
        )
    if not canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_REQUIRED",
            "agent_instance did not resolve to a concrete claimant.",
            "Pass a canonical opaque agent_instance before requesting claim.",
        )
    if actor != canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP claim slice, actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the same canonical opaque instance used for agent_instance.",
        )

    resolution_label = str(resolved["resolution"].get("resolution") or "unregistered")
    reg_available = bool(resolved.get("registry_available", True))

    # AIPOS-250 — PreAuthorized envelope: the gate does a STRUCTURAL match against an Owner-signed
    # policy and, ONLY on a strict match, auto-releases the claim in ONE stage (no dry_run token,
    # no confirm step). The executor never gains a confirm capability (★A1): the authorization is
    # the Owner-signed envelope, and the gate is the executor of that already-granted policy. Any
    # miss / expiry / count-bound / forged ref falls back to the Supervised per-task preview.
    binding_status = None
    if requested_mode == AUTONOMY_MODE_PREAUTHORIZED:
        matched_policy_id, binding_status = _match_claim_envelope(
            repo_root,
            owner_policy_ref=owner_policy_ref,
            task_id=task_id,
            task_path=task_path,
            canonical_agent_instance=canonical_agent_instance,
            actor=actor,
        )
        if matched_policy_id:
            return _preauthorized_claim_autorelease(
                args=args,
                repo_root=repo_root,
                task_id=task_id,
                task_path=task_path,
                canonical_agent_instance=canonical_agent_instance,
                policy_id=matched_policy_id,
                resolution_label=resolution_label,
                reg_available=reg_available,
            )
        # fall through to a Supervised preview (fail-safe: 回落 Supervised 逐单).

    response = claim_task(
        task_id=task_id,
        path=task_path,
        actor=canonical_agent_instance,
        dry_run=True,
        with_records=False,
        repo_root=repo_root,
        owner_confirmation_required_override=True,
        owner_confirmation_reasons_override=_claim_owner_reasons(),
        mcp_claim_metadata=_claim_metadata(
            args,
            canonical_agent_instance=canonical_agent_instance,
            resolution_label=resolution_label,
            reg_available=reg_available,
            autonomy_mode=AUTONOMY_MODE_SUPERVISED,
            binding_status=binding_status,
        ),
    )
    decorated = _decorate_queue_claim_dry_run(response, args=args, canonical_agent_instance=canonical_agent_instance)
    if decorated.get("verdict") == Verdict.BLOCK:
        return _tool_result(decorated, is_error=True)
    return _tool_result(decorated, is_error=not bool(decorated.get("ok", False)))


def lybra_queue_claim_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_claim_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLAIM_SCOPE, "supervised queue claim tools")
    # AIPOS-197: confirm additionally requires the Owner-only owner_confirm scope, so a
    # dry-run-capable (executor) token cannot self-confirm. Structural, not literal-secrecy.
    if not _owner_confirm_scope_allowed():
        return _scope_denied_result_for(OWNER_CONFIRM_SCOPE, "queue claim confirm (Owner-only)")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _queue_claim_error(
            "DRY_RUN_REQUIRED",
            "lybra_queue_claim_confirm requires dry_run_token from a prior lybra_queue_claim_dry_run response.",
            "Call lybra_queue_claim_dry_run first, review the preview, then confirm with its dry_run_token.",
        )
    owner_confirmation_token = str(args.get("owner_confirmation_token") or "").strip()
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return _queue_claim_error(
            "OWNER_CONFIRMATION_REQUIRED",
            "Supervised MCP queue_claim confirm requires owner_confirmation_token: OWNER_CONFIRMED.",
            "Present the dry-run preview to Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
        )
    actor = str(args.get("actor") or "").strip()
    agent_instance = str(args.get("agent_instance") or "").strip()
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not actor or not agent_instance or not owner_policy_ref:
        return _queue_claim_error(
            "CONFIRM_ARGUMENTS_REQUIRED",
            "actor, agent_instance, and owner_policy_ref are required on confirm.",
            "Pass the same actor, agent_instance, and owner_policy_ref reviewed in the dry-run preview.",
        )

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous" or not canonical_agent_instance:
        return _queue_claim_error(
            "AMBIGUOUS_LEGACY_INSTANCE" if resolution.get("resolution") == "ambiguous" else "INSTANCE_REQUIRED",
            f"agent_instance did not resolve to one concrete instance: {agent_instance}.",
            "Use the canonical opaque agent_instance from the dry-run preview.",
        )
    if actor != canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP claim slice, confirm actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the canonical opaque instance from the dry-run preview.",
        )

    token = get_dry_run(dry_run_token)
    if token is None:
        return _queue_claim_error(
            "STALE_DRY_RUN",
            "dry_run_token was not found in this MCP server process, or it expired.",
            "Dry-run tokens are currently process-local. Run lybra_queue_claim_dry_run again on this connection, review the new preview, then confirm.",
        )
    source_data = token.plan.get("data") if isinstance(token.plan, dict) else {}
    mcp_claim = source_data.get("mcp_claim") if isinstance(source_data, dict) else None
    if token.operation != "queue_claim" or not isinstance(mcp_claim, dict):
        return _queue_claim_error(
            "INCOMPATIBLE_DRY_RUN",
            "dry_run_token was recognized but is not compatible with lybra_queue_claim_confirm.",
            "Confirm only with a token produced by lybra_queue_claim_dry_run for this MCP claim surface.",
        )
    token_policy = str(mcp_claim.get("owner_policy_ref") or "").strip()
    token_instance = str(mcp_claim.get("canonical_agent_instance") or "").strip()
    if token_policy != owner_policy_ref:
        return _queue_claim_error(
            "OWNER_POLICY_MISMATCH",
            "owner_policy_ref does not match the dry-run preview.",
            "Run lybra_queue_claim_dry_run again or confirm with the reviewed owner_policy_ref.",
        )
    if token_instance != canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_MISMATCH",
            "agent_instance does not match the dry-run preview.",
            "Run lybra_queue_claim_dry_run again or confirm with the reviewed agent_instance.",
        )

    response = execute_dry_run(
        dry_run_token,
        canonical_agent_instance,
        owner_confirmation_token=owner_confirmation_token,
        repo_root=repo_root,
        confirmer=_confirmer_attribution(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_queue_claim_dry_run")
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = agent_instance
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = owner_policy_ref
    response["lease_status"] = "proposed"
    response["lease_path"] = "claim_only"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
        "next_required_action": "separate explicit lease activation before execution",
    }
    response["provenance"] = {
        "event_type": "mcp_queue_claim",
        "actor": canonical_agent_instance,
        "actor_instance_id": canonical_agent_instance,
        "surface": "mcp",
        "transport": "mcp",
        "owner_policy_ref": owner_policy_ref,
        "autonomy_mode": "Supervised",
        "result": response.get("verdict"),
        "dry_run_id": dry_run_token,
    }
    return _tool_result(response, is_error=False)


def lybra_queue_return_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_return_scope_allowed():
        return _scope_denied_result_for(QUEUE_RETURN_SCOPE, "supervised queue return tools")
    args = arguments or {}
    forbidden = _forbidden_queue_return_fields(args)
    if forbidden:
        return _queue_return_error(
            "UNSUPPORTED_QUEUE_RETURN_FIELD",
            f"Supervised MCP queue_return does not accept these fields: {', '.join(forbidden)}.",
            "Remove automatic, batch, credential, raw, lease, audit-dispatch, and finalize fields; then run dry-run again.",
        )
    if str(args.get("autonomy_mode") or "").strip() != "Supervised":
        return _queue_return_error(
            "INVALID_AUTONOMY_MODE",
            "lybra_queue_return_dry_run supports only autonomy_mode: Supervised.",
            "Use autonomy_mode: Supervised. Delegated and Standing remain behind separate Owner gates.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _queue_return_error("ACTOR_REQUIRED", "actor is required.", "Pass the visible returning actor.")
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not owner_policy_ref:
        return _queue_return_error(
            "OWNER_POLICY_REF_REQUIRED",
            "owner_policy_ref is required for Supervised MCP queue_return.",
            "Pass the Owner approval or policy reference authorizing this supervised return.",
        )
    agent_instance = str(args.get("agent_instance") or "").strip()
    if not agent_instance:
        return _queue_return_error(
            "INSTANCE_REQUIRED",
            "agent_instance is required and must resolve to one canonical concrete instance.",
            "Pass the canonical agent_instance or a non-ambiguous legacy instance ID.",
        )
    if str(args.get("executor_status") or "completed").strip() != "completed":
        return _queue_return_error(
            "INVALID_EXECUTOR_STATUS",
            "queue_return requires executor_status: completed.",
            "Return only completed executor work through this first slice.",
        )
    if str(args.get("audit_readiness") or "ready").strip() != "ready":
        return _queue_return_error(
            "INVALID_AUDIT_READINESS",
            "queue_return requires audit_readiness: ready.",
            "Return only audit-ready executor work through this first slice.",
        )
    if not (
        str(args.get("result_summary") or "").strip()
        or args.get("artifact_refs")
        or args.get("scratch_artifact_refs")
        or str(args.get("completion_report_ref") or "").strip()
    ):
        return _queue_return_error(
            "MISSING_RETURN_EVIDENCE",
            "result_summary, artifact_refs, scratch_artifact_refs, or completion_report_ref is required.",
            "Provide normalized non-secret executor evidence before returning work.",
        )
    if bool(args.get("scratch_artifact_refs")) != bool(str(args.get("scratch_dir") or "").strip()):
        return _queue_return_error(
            "INVALID_SCRATCH_INGEST",
            "scratch_dir and scratch_artifact_refs must be provided together.",
            "Pass both scratch_dir (the approved scratch root) and scratch_artifact_refs, or neither.",
        )
    task_id = str(args.get("task_id") or "").strip()
    task_path = str(args.get("task_path") or args.get("path") or "").strip()
    if bool(task_id) == bool(task_path):
        return _queue_return_error(
            "INVALID_TASK_SELECTOR",
            "Exactly one of task_id or task_path is required.",
            "Call lybra_queue_return_dry_run with exactly one task selector.",
        )

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous":
        return _queue_return_error(
            "AMBIGUOUS_LEGACY_INSTANCE",
            f"agent_instance resolves ambiguously: {agent_instance}.",
            "Use a canonical opaque agent_instance before returning work.",
        )
    if not canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_REQUIRED",
            "agent_instance did not resolve to a concrete returning instance.",
            "Pass a canonical opaque agent_instance before returning work.",
        )
    if actor != canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP return slice, actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the same canonical opaque instance used for agent_instance.",
        )

    response = return_task(
        task_id=task_id or None,
        path=task_path or None,
        actor=canonical_agent_instance,
        agent_instance=agent_instance,
        owner_policy_ref=owner_policy_ref,
        claim_id=str(args.get("claim_id") or "").strip() or None,
        active_session_id=str(args.get("active_session_id") or "").strip() or None,
        result_summary=str(args.get("result_summary") or "").strip() or None,
        artifact_refs=args.get("artifact_refs"),
        completion_report_ref=str(args.get("completion_report_ref") or "").strip() or None,
        return_reason=str(args.get("return_reason") or "").strip() or None,
        dry_run=True,
        repo_root=repo_root,
        mcp_return_metadata=_return_metadata(
            args,
            canonical_agent_instance=canonical_agent_instance,
            resolution_label=str(resolved["resolution"].get("resolution") or "unregistered"),
            reg_available=bool(resolved.get("registry_available", True)),
        ),
        scratch_dir=str(args.get("scratch_dir") or "").strip() or None,
        scratch_artifact_refs=args.get("scratch_artifact_refs"),
        return_body=args.get("return_body") if isinstance(args.get("return_body"), str) else None,
    )
    decorated = _decorate_queue_return_dry_run(response, args=args, canonical_agent_instance=canonical_agent_instance)
    if decorated.get("verdict") == Verdict.BLOCK:
        return _tool_result(decorated, is_error=True)
    return _tool_result(decorated, is_error=not bool(decorated.get("ok", False)))


def lybra_queue_return_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_return_scope_allowed():
        return _scope_denied_result_for(QUEUE_RETURN_SCOPE, "supervised queue return tools")
    # DL 03-02 / AIPOS-328: return is "I'm done, here's my output" — NOT an Owner gate.
    # The executor confirms its own return with its own queue_return scope (checked above).
    # The real gates are downstream: audit_verdict -> owner_verify -> close. Do NOT re-add an
    # owner_confirm scope check here: it forced advisors to press OWNER_CONFIRMED via a private
    # dual-token script (~/bin/lybra-dev-return), bypassing the gate — the exact drift the M4
    # judge targets. The owner_confirmation_token=OWNER_CONFIRMED literal below is retained as a
    # deliberate confirm-intent ceremony (public constant, not a secret); it gates nothing Owner-side.
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _queue_return_error(
            "DRY_RUN_REQUIRED",
            "lybra_queue_return_confirm requires dry_run_token from a prior lybra_queue_return_dry_run response.",
            "Call lybra_queue_return_dry_run first, review the confirmation_preview, then confirm.",
        )
    owner_confirmation_token = str(args.get("owner_confirmation_token") or "").strip()
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return _queue_return_error(
            "OWNER_CONFIRMATION_REQUIRED",
            "Supervised MCP queue_return confirm requires owner_confirmation_token: OWNER_CONFIRMED.",
            "Present the confirmation_preview to Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
        )
    actor = str(args.get("actor") or "").strip()
    agent_instance = str(args.get("agent_instance") or "").strip()
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not actor or not agent_instance or not owner_policy_ref:
        return _queue_return_error(
            "CONFIRM_ARGUMENTS_REQUIRED",
            "actor, agent_instance, and owner_policy_ref are required on confirm.",
            "Pass the same actor, agent_instance, and owner_policy_ref reviewed in the confirmation_preview.",
        )

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous" or not canonical_agent_instance:
        return _queue_return_error(
            "AMBIGUOUS_LEGACY_INSTANCE" if resolution.get("resolution") == "ambiguous" else "INSTANCE_REQUIRED",
            f"agent_instance did not resolve to one concrete instance: {agent_instance}.",
            "Use the canonical opaque agent_instance from the confirmation_preview.",
        )
    if actor != canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP return slice, confirm actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the canonical opaque instance from the confirmation_preview.",
        )

    token = get_dry_run(dry_run_token)
    if token is None:
        return _queue_return_error(
            "STALE_DRY_RUN",
            "dry_run_token was not found in this MCP server process, or it expired.",
            "Dry-run tokens are currently process-local. Run lybra_queue_return_dry_run again on this connection, review the new confirmation_preview, then confirm.",
        )
    source_data = token.plan.get("data") if isinstance(token.plan, dict) else {}
    mcp_return = source_data.get("mcp_return") if isinstance(source_data, dict) else None
    if token.operation != "queue_return" or not isinstance(mcp_return, dict):
        return _queue_return_error(
            "INCOMPATIBLE_DRY_RUN",
            "dry_run_token was recognized but is not compatible with lybra_queue_return_confirm.",
            "Confirm only with a token produced by lybra_queue_return_dry_run for this MCP return surface.",
        )
    token_policy = str(mcp_return.get("owner_policy_ref") or "").strip()
    token_instance = str(mcp_return.get("canonical_agent_instance") or "").strip()
    if token_policy != owner_policy_ref:
        return _queue_return_error(
            "OWNER_POLICY_MISMATCH",
            "owner_policy_ref does not match the confirmation_preview.",
            "Run lybra_queue_return_dry_run again or confirm with the reviewed owner_policy_ref.",
        )
    if token_instance != canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_MISMATCH",
            "agent_instance does not match the confirmation_preview.",
            "Run lybra_queue_return_dry_run again or confirm with the reviewed agent_instance.",
        )

    response = execute_dry_run(
        dry_run_token,
        canonical_agent_instance,
        owner_confirmation_token=owner_confirmation_token,
        repo_root=repo_root,
        confirmer=_confirmer_attribution(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_queue_return_dry_run")
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = agent_instance
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = owner_policy_ref
    response["executor_status"] = "completed"
    response["audit_readiness"] = "ready"
    response["audit_status"] = "pending"
    response["lease_status"] = "proposed"
    response["lease_path"] = "claim_only"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
    }
    response["provenance"] = {
        "event_type": "mcp_queue_return",
        "actor": canonical_agent_instance,
        "actor_instance_id": canonical_agent_instance,
        "surface": "mcp",
        "transport": "mcp",
        "owner_policy_ref": owner_policy_ref,
        "autonomy_mode": "Supervised",
        "result": response.get("verdict"),
        "dry_run_id": dry_run_token,
        "executor_status": "completed",
        "audit_readiness": "ready",
        "audit_status_after_return": "pending",
        "lease_status": "proposed",
    }
    return _tool_result(response, is_error=False)


def _validate_supervised_audit_args(args: dict[str, Any], *, operation: str) -> tuple[str, dict[str, Any] | None]:
    if str(args.get("autonomy_mode") or "").strip() != "Supervised":
        return "", _audit_error(
            "INVALID_AUTONOMY_MODE",
            f"lybra_{operation}_dry_run supports only autonomy_mode: Supervised.",
            "Use autonomy_mode: Supervised. Delegated and Standing remain behind separate Owner gates.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return "", _audit_error("ACTOR_REQUIRED", "actor is required.", "Pass the visible audit actor.")
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not owner_policy_ref:
        return "", _audit_error(
            "OWNER_POLICY_REF_REQUIRED",
            f"owner_policy_ref is required for Supervised MCP {operation}.",
            "Pass the Owner approval or policy reference authorizing this supervised action.",
        )
    agent_instance = str(args.get("agent_instance") or "").strip()
    if not agent_instance:
        return "", _audit_error(
            "INSTANCE_REQUIRED",
            "agent_instance is required and must resolve to one canonical concrete instance.",
            "Pass the canonical agent_instance or a non-ambiguous legacy instance ID.",
        )
    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous":
        return "", _audit_error(
            "AMBIGUOUS_LEGACY_INSTANCE",
            f"agent_instance resolves ambiguously: {agent_instance}.",
            "Use a canonical opaque agent_instance before requesting this audit action.",
        )
    if not canonical_agent_instance:
        return "", _audit_error(
            "INSTANCE_REQUIRED",
            "agent_instance did not resolve to a concrete instance.",
            "Pass a canonical opaque agent_instance before requesting this audit action.",
        )
    if actor != canonical_agent_instance:
        return "", _audit_error(
            "INSTANCE_MISMATCH",
            f"For the first Supervised MCP {operation} slice, actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the same canonical opaque instance used for agent_instance.",
        )
    return canonical_agent_instance, None


def lybra_audit_dispatch_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _audit_dispatch_scope_allowed():
        return _scope_denied_result_for(AUDIT_DISPATCH_SCOPE, "supervised audit dispatch tools")
    args = arguments or {}
    forbidden = _forbidden_audit_fields(args)
    if forbidden:
        return _audit_error(
            "UNSUPPORTED_AUDIT_DISPATCH_FIELD",
            f"Supervised MCP audit_dispatch does not accept these fields: {', '.join(forbidden)}.",
            "Remove automatic, credential, lease, finalize, accepted-work, runtime, and scheduler fields; then run dry-run again.",
        )
    canonical_agent_instance, error = _validate_supervised_audit_args(args, operation=RecordType.AUDIT_DISPATCH)
    if error is not None:
        return error
    response = audit_dispatch_task(
        source_task_id=str(args.get("source_task_id") or args.get("task_id") or "").strip() or None,
        source_path=str(args.get("source_task_path") or args.get("task_path") or "").strip() or None,
        actor=canonical_agent_instance,
        agent_instance=str(args.get("agent_instance") or "").strip(),
        owner_policy_ref=str(args.get("owner_policy_ref") or "").strip(),
        audit_task_id=str(args.get("audit_task_id") or "").strip(),
        audit_task_title=str(args.get("audit_task_title") or "").strip() or None,
        audit_by=str(args.get("audit_by") or "").strip() or None,
        audit_agent_instance=str(args.get("audit_agent_instance") or "").strip(),
        dispatch_reason=str(args.get("dispatch_reason") or "").strip() or None,
        dry_run=True,
        repo_root=_repo_root(),
    )
    decorated = _decorate_audit_dry_run(response, args=args, canonical_agent_instance=canonical_agent_instance, operation=RecordType.AUDIT_DISPATCH)
    return _tool_result(decorated, is_error=decorated.get("verdict") == Verdict.BLOCK or not bool(decorated.get("ok", False)))


def lybra_audit_dispatch_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _audit_dispatch_scope_allowed():
        return _scope_denied_result_for(AUDIT_DISPATCH_SCOPE, "supervised audit dispatch tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _audit_error(
            "DRY_RUN_REQUIRED",
            "lybra_audit_dispatch_confirm requires dry_run_token from a prior lybra_audit_dispatch_dry_run response.",
            "Call lybra_audit_dispatch_dry_run first, review the preview, then confirm with its dry_run_token.",
        )
    if str(args.get("owner_confirmation_token") or "").strip() != OWNER_CONFIRMATION_TOKEN:
        return _audit_error(
            "OWNER_CONFIRMATION_REQUIRED",
            "Supervised MCP audit_dispatch confirm requires owner_confirmation_token: OWNER_CONFIRMED.",
            "Present the dry-run preview to Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
        )
    actor = str(args.get("actor") or "").strip()
    agent_instance = str(args.get("agent_instance") or "").strip()
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not actor or not agent_instance or not owner_policy_ref:
        return _audit_error(
            "CONFIRM_ARGUMENTS_REQUIRED",
            "actor, agent_instance, and owner_policy_ref are required on confirm.",
            "Pass the same actor, agent_instance, and owner_policy_ref reviewed in the dry-run preview.",
        )
    canonical_agent_instance, error = _validate_supervised_audit_args(
        {"actor": actor, "agent_instance": agent_instance, "owner_policy_ref": owner_policy_ref, "autonomy_mode": "Supervised"},
        operation=RecordType.AUDIT_DISPATCH,
    )
    if error is not None:
        return error
    token = get_dry_run(dry_run_token)
    if token is None:
        return _audit_error(
            "STALE_DRY_RUN",
            "dry_run_token was not found in this MCP server process, or it expired.",
            "Dry-run tokens are currently process-local. Run lybra_audit_dispatch_dry_run again, review the new preview, then confirm.",
        )
    if token.operation != RecordType.AUDIT_DISPATCH:
        return _audit_error(
            "INCOMPATIBLE_DRY_RUN",
            "dry_run_token was recognized but is not compatible with lybra_audit_dispatch_confirm.",
            "Confirm only with a token produced by lybra_audit_dispatch_dry_run for this MCP dispatch surface.",
        )
    source_data = token.plan.get("data") if isinstance(token.plan, dict) else {}
    if str(source_data.get("owner_policy_ref") or "") != owner_policy_ref:
        return _audit_error("OWNER_POLICY_MISMATCH", "owner_policy_ref does not match the dry-run preview.", "Run dry-run again or confirm with the reviewed owner_policy_ref.")
    if str(source_data.get("canonical_agent_instance") or "") != canonical_agent_instance:
        return _audit_error("INSTANCE_MISMATCH", "agent_instance does not match the dry-run preview.", "Run dry-run again or confirm with the reviewed agent_instance.")
    response = execute_dry_run(dry_run_token, actor, owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=_repo_root())
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_audit_dispatch_dry_run")
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = owner_policy_ref
    return _tool_result(response, is_error=False)


def lybra_audit_verdict_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _audit_verdict_scope_allowed():
        return _scope_denied_result_for(AUDIT_VERDICT_SCOPE, "supervised audit verdict tools")
    args = arguments or {}
    forbidden = _forbidden_audit_fields(args)
    if forbidden:
        return _audit_error(
            "UNSUPPORTED_AUDIT_VERDICT_FIELD",
            f"Supervised MCP audit_verdict does not accept these fields: {', '.join(forbidden)}.",
            "Remove automatic, credential, lease, finalize, accepted-work, runtime, and scheduler fields; then run dry-run again.",
        )
    canonical_agent_instance, error = _validate_supervised_audit_args(args, operation=RecordType.AUDIT_VERDICT)
    if error is not None:
        return error
    response = audit_verdict_task(
        audit_task_id=str(args.get("audit_task_id") or "").strip() or None,
        audit_task_path=str(args.get("audit_task_path") or args.get("task_path") or "").strip() or None,
        reviewed_task_id=str(args.get("reviewed_task_id") or "").strip(),
        actor=canonical_agent_instance,
        agent_instance=str(args.get("agent_instance") or "").strip(),
        owner_policy_ref=str(args.get("owner_policy_ref") or "").strip(),
        audit_claim_id=str(args.get("audit_claim_id") or "").strip() or None,
        audit_session_id=str(args.get("audit_session_id") or "").strip() or None,
        audit_dispatch_record_ref=str(args.get("audit_dispatch_record_ref") or "").strip() or None,
        reviewed_return_record_ref=str(args.get("reviewed_return_record_ref") or "").strip() or None,
        verdict=str(args.get("verdict") or "").strip(),
        findings_summary=str(args.get("findings_summary") or "").strip() or None,
        evidence_refs=args.get("evidence_refs") if isinstance(args.get("evidence_refs"), list) else [],
        recommended_next_action=str(args.get("recommended_next_action") or "").strip() or None,
        owner_waiver_ref=str(args.get("owner_waiver_ref") or "").strip() or None,
        agent_runtime=_agent_runtime_value(args),
        dry_run=True,
        repo_root=_repo_root(),
    )
    decorated = _decorate_audit_dry_run(response, args=args, canonical_agent_instance=canonical_agent_instance, operation=RecordType.AUDIT_VERDICT)
    return _tool_result(decorated, is_error=decorated.get("verdict") == Verdict.BLOCK or not bool(decorated.get("ok", False)))


def lybra_audit_verdict_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _audit_verdict_scope_allowed():
        return _scope_denied_result_for(AUDIT_VERDICT_SCOPE, "supervised audit verdict tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _audit_error(
            "DRY_RUN_REQUIRED",
            "lybra_audit_verdict_confirm requires dry_run_token from a prior lybra_audit_verdict_dry_run response.",
            "Call lybra_audit_verdict_dry_run first, review the preview, then confirm with its dry_run_token.",
        )
    if str(args.get("owner_confirmation_token") or "").strip() != OWNER_CONFIRMATION_TOKEN:
        return _audit_error(
            "OWNER_CONFIRMATION_REQUIRED",
            "Supervised MCP audit_verdict confirm requires owner_confirmation_token: OWNER_CONFIRMED.",
            "Present the dry-run preview to Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
        )
    actor = str(args.get("actor") or "").strip()
    agent_instance = str(args.get("agent_instance") or "").strip()
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not actor or not agent_instance or not owner_policy_ref:
        return _audit_error(
            "CONFIRM_ARGUMENTS_REQUIRED",
            "actor, agent_instance, and owner_policy_ref are required on confirm.",
            "Pass the same actor, agent_instance, and owner_policy_ref reviewed in the dry-run preview.",
        )
    canonical_agent_instance, error = _validate_supervised_audit_args(
        {"actor": actor, "agent_instance": agent_instance, "owner_policy_ref": owner_policy_ref, "autonomy_mode": "Supervised"},
        operation=RecordType.AUDIT_VERDICT,
    )
    if error is not None:
        return error
    token = get_dry_run(dry_run_token)
    if token is None:
        return _audit_error(
            "STALE_DRY_RUN",
            "dry_run_token was not found in this MCP server process, or it expired.",
            "Dry-run tokens are currently process-local. Run lybra_audit_verdict_dry_run again, review the new preview, then confirm.",
        )
    if token.operation != RecordType.AUDIT_VERDICT:
        return _audit_error(
            "INCOMPATIBLE_DRY_RUN",
            "dry_run_token was recognized but is not compatible with lybra_audit_verdict_confirm.",
            "Confirm only with a token produced by lybra_audit_verdict_dry_run for this MCP verdict surface.",
        )
    source_data = token.plan.get("data") if isinstance(token.plan, dict) else {}
    if str(source_data.get("owner_policy_ref") or "") != owner_policy_ref:
        return _audit_error("OWNER_POLICY_MISMATCH", "owner_policy_ref does not match the dry-run preview.", "Run dry-run again or confirm with the reviewed owner_policy_ref.")
    if str(source_data.get("canonical_agent_instance") or "") != canonical_agent_instance:
        return _audit_error("INSTANCE_MISMATCH", "agent_instance does not match the dry-run preview.", "Run dry-run again or confirm with the reviewed agent_instance.")
    response = execute_dry_run(dry_run_token, actor, owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=_repo_root())
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_audit_verdict_dry_run")
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = owner_policy_ref
    return _tool_result(response, is_error=False)


# ---------------------------------------------------------------------------
# AIPOS-336: bench audit submit/confirm (non-code branch audit)
# ---------------------------------------------------------------------------

BENCH_AUDIT_SUBMIT_SCOPE = "bench_audit_submit"
BENCH_AUDIT_CONFIRM_SCOPE = "bench_audit_confirm"


def _bench_audit_scope_allowed() -> bool:
    """Check if the current token holds bench_audit_submit scope (executor/advisor)."""
    return _capability_has_scope(BENCH_AUDIT_SUBMIT_SCOPE)


def _bench_audit_confirm_scope_allowed() -> bool:
    """Check if the current token holds bench_audit_confirm scope (advisor/owner).
    
    AIPOS-336 S1 + acceptance #2: bench_audit_confirm is NOT held by executor.
    The executor can dry_run (submit the evidence), but CANNOT self-confirm.
    Confirmation is an advisor/owner gate (甲案家族: Owner 确认发生在验证台按键).
    """
    return _capability_has_scope(BENCH_AUDIT_CONFIRM_SCOPE)


def _bench_audit_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref="AIPOS-336 bench audit submit/confirm (non-code branch)",
    )


def lybra_bench_audit_submit_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-336 S1: Submit bench audit conclusion (审结提交) — dry-run preview.
    
    Non-code tasks (deploy/config/content/research) walk the bench audit path
    (304 D2 branch-1): executor produces evidence → verification station ring2
    checklist + ring3 Owner eye-verify → Owner confirm → close. This verb is the
    "审结提交" step: the executor/advisor submits the evidence + conclusion; the gate
    runs the ring2 auto-checks (data-driven, same source as card template); the
    record lands in the workspace (`5_tasks/records/bench_audit/<task_id>/`).
    
    Requires: bench_audit_submit scope (executor/advisor). Executor CAN dry_run
    (submit evidence), but CANNOT self-confirm (acceptance #2). Confirmation is
    a separate gate verb (lybra_bench_audit_confirm) that requires
    bench_audit_confirm scope (advisor/owner).
    
    Returns: controlled_execute envelope with verdict, planned_writes, dry_run_token,
    checklist (ring2 auto-check results + ring3 human items), ring2_summary.
    """
    if not _bench_audit_scope_allowed():
        return _scope_denied_result_for(BENCH_AUDIT_SUBMIT_SCOPE, "bench audit submit tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    actor = str(args.get("actor") or "").strip()
    if not task_id:
        return _bench_audit_error(
            "TASK_ID_REQUIRED",
            "lybra_bench_audit_submit_dry_run requires task_id.",
            "Pass the task_id of the task being audited.",
        )
    if not actor:
        return _bench_audit_error(
            "ACTOR_REQUIRED",
            "lybra_bench_audit_submit_dry_run requires actor.",
            "Pass the actor identifier (executor or advisor).",
        )
    response = bench_audit_submit(
        payload={
            "task_id": task_id,
            "evidence_type": str(args.get("evidence_type") or "").strip() or None,
            "task_mode": str(args.get("task_mode") or "").strip() or None,
            "conclusion": str(args.get("conclusion") or "").strip(),
            "evidence_refs": args.get("evidence_refs") if isinstance(args.get("evidence_refs"), list) else [],
            "notes": str(args.get("notes") or "").strip() or None,
        },
        actor=actor,
        dry_run=True,
        repo_root=_repo_root(),
    )
    return _tool_result(response, is_error=response.get("verdict") == Verdict.BLOCK or not bool(response.get("ok", False)))


def lybra_bench_audit_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-336 S1: Confirm bench audit submission — execute the dry-run token.
    
    Requires: bench_audit_confirm scope (advisor/owner). Executor does NOT hold
    this scope (acceptance #2: 执行体无法自行 confirm). This is the Owner/advisor
    gate that审结 the bench submission after reviewing the ring2 checklist and
    performing ring3 eye-verify.
    
    The dry_run_token is produced by lybra_bench_audit_submit_dry_run. This verb
    re-validates the preview, then writes the bench audit record to the workspace.
    
    Returns: executed response with ok, performed_writes, checklist, ring2_summary.
    """
    if not _bench_audit_confirm_scope_allowed():
        return _scope_denied_result_for(BENCH_AUDIT_CONFIRM_SCOPE, "bench audit confirm tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _bench_audit_error(
            "DRY_RUN_REQUIRED",
            "lybra_bench_audit_confirm requires dry_run_token from a prior lybra_bench_audit_submit_dry_run response.",
            "Call lybra_bench_audit_submit_dry_run first, review the preview, then confirm with its dry_run_token.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _bench_audit_error(
            "ACTOR_REQUIRED",
            "lybra_bench_audit_confirm requires actor.",
            "Pass the confirming actor identifier (advisor or owner).",
        )
    # AIPOS-336 顾问注记 #5: bench_audit_confirm 语义=审结提交(非 Owner 门).
    # Owner 确认发生在验证台按键(owner_verification_record),不在动词层重复设门.
    # bench_audit_confirm 是 advisor/owner scope-gated,但不要求 owner_confirmation_token.
    # 甲案家族: submit 干运行 + confirm 审结提交,Owner 眼验在台上.
    confirmer = _confirmer_attribution()
    response = execute_dry_run(
        dry_run_token,
        actor,
        owner_confirmation_token=None,  # bench confirm does NOT require owner_confirmation_token
        repo_root=_repo_root(),
        confirmer=confirmer,
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_bench_audit_submit_dry_run")
    response["surface"] = "mcp"
    response["confirmer"] = confirmer
    return _tool_result(response, is_error=False)


def _queue_close_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref="AIPOS-283 gate close verb + closure evidence protocol",
    )


def lybra_queue_close_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-283: dry-run preview for closing a claimed task (claimed/ -> completed/).

    Requires: queue_close scope (executor/advisor), task_id, closure_evidence.
    Validates: task in claimed/, has return record, evidence present.
    Does NOT require owner_confirm (this is the executor's finalize settlement step).
    """
    if not _queue_close_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLOSE_SCOPE, "queue close tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _queue_close_error(
            "TASK_ID_REQUIRED",
            "lybra_queue_close_dry_run requires task_id.",
            "Pass the task_id of the claimed task to close.",
        )
    closure_evidence = args.get("closure_evidence")
    if not closure_evidence or not isinstance(closure_evidence, dict):
        return _queue_close_error(
            "MISSING_CLOSURE_EVIDENCE",
            "closure_evidence is required (object with at least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref).",
            "Provide closure evidence before closing. See AIPOS-283.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _queue_close_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor performing the close.",
        )
    response = close_task(
        task_id=task_id,
        actor=actor,
        closure_evidence=closure_evidence,
        dry_run=True,
        repo_root=_repo_root(),
    )
    if response.get("verdict") == Verdict.BLOCK:
        return _tool_result(response, is_error=True)
    # Add MCP decoration
    response["surface"] = "mcp"
    response["operation"] = "queue_close"
    response["dry_run"] = True
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_queue_close_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-283: confirm close — execute the close (move card + write closure record).

    Does NOT require owner_confirm (executor/advisor callable per S2).
    Re-validates all inputs before executing.
    """
    if not _queue_close_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLOSE_SCOPE, "queue close tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _queue_close_error(
            "TASK_ID_REQUIRED",
            "lybra_queue_close_confirm requires task_id.",
            "Pass the task_id of the claimed task to close.",
        )
    closure_evidence = args.get("closure_evidence")
    if not closure_evidence or not isinstance(closure_evidence, dict):
        return _queue_close_error(
            "MISSING_CLOSURE_EVIDENCE",
            "closure_evidence is required.",
            "Provide closure evidence before closing.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _queue_close_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor performing the close.",
        )
    response = close_task(
        task_id=task_id,
        actor=actor,
        closure_evidence=closure_evidence,
        dry_run=False,
        repo_root=_repo_root(),
    )
    if response.get("verdict") == Verdict.BLOCK:
        return _tool_result(response, is_error=True)
    response["surface"] = "mcp"
    response["operation"] = "queue_close"
    response["dry_run"] = False
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_converge_r_cards(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-354 S2: batch convergence of existing R cards.

    Scans all audit-derived cards (R cards) in claimed/ and pending/.
    For each, checks if the reviewed (parent) task has a verdict record.
    If yes, moves the R card to completed/ with closure metadata.
    Never deletes records; only moves cards.

    Use dry_run=true first to preview, then dry_run=false to execute.
    """
    args = arguments or {}
    actor = str(args.get("actor") or "system").strip()
    dry_run = bool(args.get("dry_run", True))
    response = converge_r_cards(
        repo_root=_repo_root(),
        actor=actor,
        dry_run=dry_run,
    )
    response["surface"] = "mcp"
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_mark_concluded(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-354 S3: explicit mark-concluded for report-style audits.

    For bypass scenarios where no formal verdict was landed via gate.
    Leaves a machine-readable closure marker so the card moves to completed/
    without producing a断层 card.

    Requires: task_id, and at least one of report_path or conclusion_note.
    """
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _tool_result({
            "ok": False, "verdict": Verdict.BLOCK, "operation": "mark_concluded",
            "blocking_reasons": ["TASK_ID_REQUIRED: task_id is required"],
        }, is_error=True)
    response = mark_concluded_task(
        task_id=task_id,
        report_path=str(args.get("report_path") or "").strip() or None,
        actor=str(args.get("actor") or "").strip() or None,
        conclusion_note=str(args.get("conclusion_note") or "").strip() or None,
        dry_run=bool(args.get("dry_run", True)),
        repo_root=_repo_root(),
    )
    response["surface"] = "mcp"
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_queue_withdraw_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-315: dry-run preview for withdrawing a task from queue.
    
    Requires: queue_withdraw scope, task_id, reason.
    Supports: pending or claimed tasks.
    S3 in-transit protection: blocks if active session detected within last hour.
    """
    if not _queue_withdraw_scope_allowed():
        return _scope_denied_result_for(QUEUE_WITHDRAW_SCOPE, "queue withdraw tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _teaching_error(
            "TASK_ID_REQUIRED",
            "lybra_queue_withdraw_dry_run requires task_id.",
            "Pass the task_id of the task to withdraw.",
        )
    reason = str(args.get("reason") or "").strip()
    if not reason:
        return _teaching_error(
            "REASON_REQUIRED",
            "reason is required for task withdrawal.",
            "Provide the reason for withdrawing this task.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _teaching_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor performing the withdrawal.",
        )
    response = withdraw_task(
        task_id=task_id,
        actor=actor,
        reason=reason,
        dry_run=True,
        repo_root=_repo_root(),
    )
    if response.get("verdict") == Verdict.BLOCK:
        return _tool_result(response, is_error=True)
    response["surface"] = "mcp"
    response["operation"] = "queue_withdraw"
    response["dry_run"] = True
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_queue_withdraw_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-315: confirm withdraw — execute the withdrawal (move card to withdrawn/).
    
    Does NOT require owner_confirm (advisor callable).
    Re-validates all inputs and in-transit checks before executing.
    """
    if not _queue_withdraw_scope_allowed():
        return _scope_denied_result_for(QUEUE_WITHDRAW_SCOPE, "queue withdraw tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _teaching_error(
            "TASK_ID_REQUIRED",
            "lybra_queue_withdraw_confirm requires task_id.",
            "Pass the task_id of the task to withdraw.",
        )
    reason = str(args.get("reason") or "").strip()
    if not reason:
        return _teaching_error(
            "REASON_REQUIRED",
            "reason is required for task withdrawal.",
            "Provide the reason for withdrawing this task.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _teaching_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor performing the withdrawal.",
        )
    response = withdraw_task(
        task_id=task_id,
        actor=actor,
        reason=reason,
        dry_run=False,
        repo_root=_repo_root(),
    )
    if response.get("verdict") == Verdict.BLOCK:
        return _tool_result(response, is_error=True)
    response["surface"] = "mcp"
    response["operation"] = "queue_withdraw"
    response["dry_run"] = False
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_queue_amend_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-315: dry-run preview for amending a pending task.
    
    Requires: queue_amend scope, task_id, amendments, amendment_reason.
    Only works on pending tasks (claimed tasks cannot be amended mid-execution).
    Writes amendment record preserving original content.
    """
    if not _queue_amend_scope_allowed():
        return _scope_denied_result_for(QUEUE_AMEND_SCOPE, "queue amend tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _teaching_error(
            "TASK_ID_REQUIRED",
            "lybra_queue_amend_dry_run requires task_id.",
            "Pass the task_id of the pending task to amend.",
        )
    amendments = args.get("amendments")
    if not amendments or not isinstance(amendments, dict):
        return _teaching_error(
            "AMENDMENTS_REQUIRED",
            "amendments dict is required with fields to update.",
            "Provide amendments dict with frontmatter fields or 'body' to change.",
        )
    amendment_reason = str(args.get("amendment_reason") or "").strip()
    if not amendment_reason:
        return _teaching_error(
            "REASON_REQUIRED",
            "amendment_reason is required.",
            "Provide the reason for this amendment.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _teaching_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor performing the amendment.",
        )
    response = amend_task(
        task_id=task_id,
        actor=actor,
        amendments=amendments,
        amendment_reason=amendment_reason,
        dry_run=True,
        repo_root=_repo_root(),
    )
    if response.get("verdict") == Verdict.BLOCK:
        return _tool_result(response, is_error=True)
    response["surface"] = "mcp"
    response["operation"] = "queue_amend"
    response["dry_run"] = True
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_queue_amend_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-315: confirm amend — execute the amendment (update task + write amendment record).
    
    Does NOT require owner_confirm (advisor callable for governance amendments).
    Re-validates all inputs and pending state before executing.
    """
    if not _queue_amend_scope_allowed():
        return _scope_denied_result_for(QUEUE_AMEND_SCOPE, "queue amend tools")
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _teaching_error(
            "TASK_ID_REQUIRED",
            "lybra_queue_amend_confirm requires task_id.",
            "Pass the task_id of the pending task to amend.",
        )
    amendments = args.get("amendments")
    if not amendments or not isinstance(amendments, dict):
        return _teaching_error(
            "AMENDMENTS_REQUIRED",
            "amendments dict is required with fields to update.",
            "Provide amendments dict with frontmatter fields or 'body' to change.",
        )
    amendment_reason = str(args.get("amendment_reason") or "").strip()
    if not amendment_reason:
        return _teaching_error(
            "REASON_REQUIRED",
            "amendment_reason is required.",
            "Provide the reason for this amendment.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _teaching_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor performing the amendment.",
        )
    response = amend_task(
        task_id=task_id,
        actor=actor,
        amendments=amendments,
        amendment_reason=amendment_reason,
        dry_run=False,
        repo_root=_repo_root(),
    )
    if response.get("verdict") == Verdict.BLOCK:
        return _tool_result(response, is_error=True)
    response["surface"] = "mcp"
    response["operation"] = "queue_amend"
    response["dry_run"] = False
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_task_progress(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-323: agent self-reports task progress events (started/progress/completed/blocked).
    
    Agent pushes task facts to gate, which writes append-only event records to
    5_tasks/records/events/<task_id>/. Gate does NOT maintain online/offline state,
    does NOT timeout-judge liveness, does NOT push to anyone. Advisor/board read events as needed.
    
    This is the "agent opens mouth" direction (取代顾问观察式); gate只记录不判活、不心跳、不推送。
    跨机可用 (MCP HTTP, no gate filesystem access required).
    """
    if not _task_progress_scope_allowed():
        return _scope_denied_result_for(TASK_PROGRESS_SCOPE, "task progress tools")
    
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return _teaching_error(
            "TASK_ID_REQUIRED",
            "task_id is required for task progress events.",
            "Pass the task_id you are reporting progress for.",
        )
    
    event_type = str(args.get("event_type") or "").strip()
    if event_type not in ("started", "progress", "completed", "blocked"):
        return _teaching_error(
            "INVALID_EVENT_TYPE",
            f"event_type must be one of: started, progress, completed, blocked. Got: {event_type}",
            "Pass a valid event_type.",
        )
    
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _teaching_error(
            "ACTOR_REQUIRED",
            "actor is required.",
            "Pass the actor (agent instance) reporting this event.",
        )
    
    # Optional fields
    summary = str(args.get("summary") or "").strip()
    model_self_reported = str(args.get("model_self_reported") or "").strip()
    stage = str(args.get("stage") or "").strip()
    reason = str(args.get("reason") or "").strip()
    
    # Write event record
    try:
        repo_root = _repo_root()
        # AIPOS-357: event write root guard — task progress events MUST land in the
        # Lybra governance workspace (a root containing 5_tasks/queue), never the
        # product repo (which has no 5_tasks/queue). If _repo_root() resolved to a
        # non-workspace root, the event would be invisible to the board and silently
        # misdirected (the S10 blocked_verdict_submit misdirect) — reject it.
        if not has_workspace_queue(repo_root):
            return _teaching_error(
                "EVENTS_ROOT_NOT_WORKSPACE",
                f"task progress events must be written to the Lybra governance workspace "
                f"(a root containing 5_tasks/queue), but the resolved root is not a "
                f"workspace: {repo_root}. This looks like the product repo or a "
                f"non-workspace root; an event written here would be invisible to the "
                f"board (S10 misdirect). Likely the gate's AIPOS_WORKSPACE_ROOT is "
                f"misconfigured or the gate is running from the product repo.",
                "Run the gate with AIPOS_WORKSPACE_ROOT pointing at the governance "
                "workspace (the directory that contains 5_tasks/queue), or invoke the "
                "gate from a location that resolves upward to such a workspace.",
            )
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        
        # Build event record
        events_dir = repo_root / "5_tasks" / "records" / "events" / task_id
        events_dir.mkdir(parents=True, exist_ok=True)
        
        # Event filename: <event_type>_<timestamp>.md
        timestamp_slug = timestamp.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
        event_file = events_dir / f"{event_type}_{timestamp_slug}.md"
        
        # Build frontmatter
        metadata = {
            "record_type": RecordType.TASK_PROGRESS_EVENT,
            "event_type": event_type,
            "task_id": task_id,
            "actor": actor,
            "timestamp": timestamp,
        }
        if model_self_reported:
            metadata["model_self_reported"] = model_self_reported
        if stage:
            metadata["stage"] = stage
        if summary:
            metadata["summary"] = summary
        if reason:
            metadata["reason"] = reason
        
        # Build markdown
        lines = ["---"]
        for key in ["record_type", "event_type", "task_id", "actor", "timestamp", "model_self_reported", "stage", "summary", "reason"]:
            if key in metadata and metadata[key]:
                value = metadata[key]
                if any(char in str(value) for char in [":", "#", "[", "]", "{", "}", "\n"]) or str(value) != str(value).strip():
                    lines.append(f"{key}: '{str(value).replace("'", "''")}'") 
                else:
                    lines.append(f"{key}: {value}")
        lines.append("---")
        lines.append(f"# Task Progress Event: {event_type}")
        lines.append("")
        lines.append(f"Agent `{actor}` reported {event_type} for task `{task_id}` at {timestamp}.")
        lines.append("")
        if summary:
            lines.append(f"## Summary")
            lines.append("")
            lines.append(summary)
            lines.append("")
        if reason:
            lines.append(f"## Reason")
            lines.append("")
            lines.append(reason)
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("This event was self-reported by the agent via the task_progress MCP verb (AIPOS-323).")
        lines.append("Gate records only; it does not maintain online/offline state or judge timeouts.")
        lines.append("")
        
        event_file.write_text("\n".join(lines), encoding="utf-8")
        
        return _tool_result({
            "ok": True,
            "operation": "task_progress",
            "event_type": event_type,
            "task_id": task_id,
            "actor": actor,
            "timestamp": timestamp,
            "event_file": str(event_file.relative_to(repo_root)),
            "summary": summary or "(none)",
            "model_self_reported": model_self_reported or "(none)",
            "stage": stage or "(none)",
            "reason": reason or "(none)",
        }, is_error=False)
    except Exception as exc:
        return _teaching_error(
            "EVENT_WRITE_FAILED",
            f"Failed to write task progress event: {exc}",
            "Check gate logs and file permissions.",
        )


def lybra_gate_guidance(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-330 S3: Read-only gate guidance — given card + role, answer what to do next.

    Returns: the verb to call, required params, whether the role's scope is sufficient,
    and who holds the scope if not. Data-driven (flow_description.py), not hardcoded.

    This is the "agent asks gate" direction: kickoff no longer needs to describe the flow,
    it just says "ask the gate".
    """
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    role = str(args.get("role") or "").strip()

    if not task_id:
        return _error_result("task_id is required")
    if not role:
        return _error_result("role is required")

    from tools.aipos_cli.flow_description import resolve_next_step

    try:
        result = resolve_next_step(task_id, role, _repo_root())
    except Exception as exc:
        return _error_result(f"Failed to resolve guidance: {exc}")

    return _tool_result({
        "ok": True,
        "source": "gate",
        "guidance": result,
    })


# ---------------------------------------------------------------------------
# AIPOS-350: Naming profile gate verbs
# ---------------------------------------------------------------------------

def lybra_naming_profile_get(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-350 S2: read the naming profile (alias layer) for the active workspace.

    Returns the full naming profile: prefix_mapping, project_segment (+aliases),
    host_segment (+aliases). Read-only; no scope required beyond basic gate access.
    """
    from tools.aipos_cli.naming_profile import get_naming_profile, generate_canonical_name
    try:
        profile = get_naming_profile(_repo_root())
    except Exception as exc:
        return _error_result(f"Failed to read naming profile: {exc}")
    # Also generate canonical names for all known roles
    generated = {}
    for role in sorted(profile.get("prefix_mapping", {}).keys()):
        try:
            generated[role] = generate_canonical_name(role, _repo_root())
        except ValueError:
            pass
    return _tool_result({
        "ok": True,
        "operation": "naming_profile_get",
        "naming_profile": profile,
        "generated_canonical_names": generated,
    })


def lybra_naming_profile_set(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-350 S2: modify the naming profile (alias layer).

    Supports setting: prefix_mapping (single role->prefix), project_segment,
    host_segment, and their aliases. All changes are append-only logged.
    Requires owner scope (this is an Owner/advisor governance action).
    """
    from tools.aipos_cli.naming_profile import (
        get_naming_profile,
        set_prefix_mapping,
        set_project_segment,
        set_host_segment,
        add_project_segment_alias,
        add_host_segment_alias,
    )
    args = arguments or {}
    actor = str(args.get("actor") or "").strip() or "owner"
    reason = str(args.get("reason") or "").strip()
    changes_made: list[dict[str, Any]] = []
    root = _repo_root()
    try:
        # Set prefix for a role
        if "prefix_role" in args and "prefix_value" in args:
            role = str(args["prefix_role"]).strip()
            prefix = str(args["prefix_value"]).strip()
            set_prefix_mapping(root, role, prefix, by=actor, reason=reason)
            changes_made.append({"action": "set_prefix", "role": role, "prefix": prefix})
        # Set project segment
        if "project_segment" in args:
            aliases = args.get("project_segment_aliases")
            if isinstance(aliases, list):
                aliases = [str(a) for a in aliases]
            else:
                aliases = None
            set_project_segment(root, str(args["project_segment"]), aliases=aliases, by=actor, reason=reason)
            changes_made.append({"action": "set_project_segment", "value": args["project_segment"]})
        # Set host segment
        if "host_segment" in args:
            aliases = args.get("host_segment_aliases")
            if isinstance(aliases, list):
                aliases = [str(a) for a in aliases]
            else:
                aliases = None
            set_host_segment(root, str(args["host_segment"]), aliases=aliases, by=actor, reason=reason)
            changes_made.append({"action": "set_host_segment", "value": args["host_segment"]})
        # Add project segment alias
        if "add_project_alias" in args:
            add_project_segment_alias(root, str(args["add_project_alias"]), by=actor, reason=reason)
            changes_made.append({"action": "add_project_alias", "alias": args["add_project_alias"]})
        # Add host segment alias
        if "add_host_alias" in args:
            add_host_segment_alias(root, str(args["add_host_alias"]), by=actor, reason=reason)
            changes_made.append({"action": "add_host_alias", "alias": args["add_host_alias"]})
    except (ValueError, FileNotFoundError) as exc:
        return _error_result(f"Naming profile update failed: {exc}")
    if not changes_made:
        return _error_result("No naming profile changes specified. Provide one of: prefix_role+prefix_value, project_segment, host_segment, add_project_alias, add_host_alias.")
    # Return updated profile
    from tools.aipos_cli.naming_profile import get_naming_profile as gnp, generate_canonical_name as gcn
    updated = gnp(root)
    generated = {}
    for role in sorted(updated.get("prefix_mapping", {}).keys()):
        try:
            generated[role] = gcn(role, root)
        except ValueError:
            pass
    return _tool_result({
        "ok": True,
        "operation": "naming_profile_set",
        "changes": changes_made,
        "naming_profile": updated,
        "generated_canonical_names": generated,
    })


def lybra_roles_register(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-352F1: register a custom role in the workspace registry.

    Custom roles are workspace data (project.json). A custom role = {name → builtin_class}.
    Owner-gated: requires owner_authorization_ref (audit trail per AIPOS-346F2).
    Registry carries ZERO scope fields (anti-privilege-escalation).
    """
    from tools.aipos_cli.custom_roles import register_custom_role
    args = arguments or {}
    name = str(args.get("name") or "").strip()
    builtin_class = str(args.get("builtin_class") or args.get("class") or "").strip()
    owner_authorization_ref = str(args.get("owner_authorization_ref") or "").strip() or None
    reason = str(args.get("reason") or "").strip()
    if not name:
        return _error_result("Missing required parameter: name")
    if not builtin_class:
        return _error_result("Missing required parameter: builtin_class (or class)")
    if not owner_authorization_ref:
        return _error_result(
            "Missing required parameter: owner_authorization_ref. "
            "Custom role registration is owner-gated (AIPOS-346F2). "
            "Provide a reference to the owner authorization decision."
        )
    root = _repo_root()
    try:
        updated = register_custom_role(
            root, name, builtin_class,
            by=owner_authorization_ref,
            reason=reason or f"owner-authorization-ref: {owner_authorization_ref}",
        )
    except ValueError as exc:
        return _error_result(f"Custom role registration failed: {exc}")
    return _tool_result({
        "ok": True,
        "operation": "roles_register",
        "name": name,
        "builtin_class": builtin_class,
        "owner_authorization_ref": owner_authorization_ref,
        "custom_roles": updated,
    })


def lybra_roles_remove(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-352F1: remove a custom role from the workspace registry. Idempotent.

    Owner-gated: requires owner_authorization_ref (audit trail per AIPOS-346F2).
    """
    from tools.aipos_cli.custom_roles import remove_custom_role
    args = arguments or {}
    name = str(args.get("name") or "").strip()
    owner_authorization_ref = str(args.get("owner_authorization_ref") or "").strip() or None
    reason = str(args.get("reason") or "").strip()
    if not name:
        return _error_result("Missing required parameter: name")
    if not owner_authorization_ref:
        return _error_result(
            "Missing required parameter: owner_authorization_ref. "
            "Custom role removal is owner-gated (AIPOS-346F2). "
            "Provide a reference to the owner authorization decision."
        )
    root = _repo_root()
    try:
        updated = remove_custom_role(
            root, name,
            by=owner_authorization_ref,
            reason=reason or f"owner-authorization-ref: {owner_authorization_ref}",
        )
    except ValueError as exc:
        return _error_result(f"Custom role removal failed: {exc}")
    return _tool_result({
        "ok": True,
        "operation": "roles_remove",
        "name": name,
        "owner_authorization_ref": owner_authorization_ref,
        "custom_roles": updated,
    })


def lybra_roles_enroll_code(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-362: generate a one-time enrollment code for remote agent credential bootstrap.

    Owner-gated: requires owner_authorization_ref.
    The enrollment code is NOT a token — it's a temporary credential that can be exchanged
    for a real token via lybra_roles_enroll_exchange.
    """
    from tools.aipos_cli.enrollment import create_enrollment_code
    args = arguments or {}
    role = str(args.get("role") or "").strip()
    instance = str(args.get("instance") or "").strip() or None
    ttl = args.get("ttl")
    owner_authorization_ref = str(args.get("owner_authorization_ref") or "").strip() or None
    reason = str(args.get("reason") or "").strip()
    if not role:
        return _error_result("Missing required parameter: role")
    if not owner_authorization_ref:
        return _error_result(
            "Missing required parameter: owner_authorization_ref. "
            "Enrollment code generation is owner-gated (AIPOS-362 security model)."
        )
    if ttl is not None:
        try:
            ttl = int(ttl)
            if ttl <= 0:
                return _error_result("ttl must be a positive integer (seconds)")
        except (ValueError, TypeError):
            return _error_result("ttl must be a positive integer (seconds)")
    root = _repo_root()
    try:
        enrollment = create_enrollment_code(
            root,
            role=role,
            instance=instance,
            ttl_seconds=ttl,
            by=owner_authorization_ref,
            reason=reason or f"owner-authorization-ref: {owner_authorization_ref}",
        )
    except ValueError as exc:
        return _error_result(f"Enrollment code creation failed: {exc}")
    return _tool_result({
        "ok": True,
        "operation": "roles_enroll_code",
        "enrollment": enrollment,
        "security_notice": "The enrollment code is shown only once. Token values never appear in logs or responses.",
    })


def lybra_roles_enroll_exchange(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-362: exchange an enrollment code for a capability token.

    This is a PUBLIC endpoint (no token required). The enrollment code itself is the authentication.
    Once exchanged, the code is marked as used and cannot be reused.
    The token value is returned ONLY in this response and never logged.
    
    FIX-2: Gate 侧必须注册新铸 token 到凭据源并即时生效。
    """
    from tools.aipos_cli.enrollment import get_enrollment_status, mark_enrollment_used
    import secrets
    args = arguments or {}
    code = str(args.get("code") or "").strip()
    if not code:
        return _error_result("Missing required parameter: code")
    
    root = _repo_root()
    try:
        status, record = get_enrollment_status(root, code)
    except Exception as exc:
        return _error_result(f"Enrollment exchange failed: {exc}")
    
    if status != "pending":
        return _error_result(f"Enrollment code is {status}. Valid codes can only be used once.")
    if not record:
        return _error_result("Enrollment code not found or expired")
    
    # Generate token for the bound role
    role = record["role"]
    instance = record.get("instance")
    
    # Mint a new token (same logic as service_mode._role_token_entry)
    token = secrets.token_urlsafe(32)
    from tools.aipos_cli.service_mode import secret_fingerprint, ROLE_SPECS
    from tools.aipos_cli.custom_roles import resolve_role_to_class
    
    # Resolve role to class (handles custom roles)
    role_class = resolve_role_to_class(role, root)
    if not role_class:
        return _error_result(f"Unknown role: {role}. Role must be a built-in or registered custom role.")
    
    # Find the role spec
    spec = None
    for s in ROLE_SPECS:
        if s["role"] == role_class:
            spec = s
            break
    if not spec:
        return _error_result(f"Role class '{role_class}' not found in ROLE_SPECS")
    
    # Build token entry
    token_entry = {
        "role": role,
        "token": token,
        "token_ref": f"svc-{role}",
        "scopes": list(spec["scopes"]),
        "fingerprint": secret_fingerprint(token),
    }
    if role_class != role:  # Custom role
        token_entry["role_class"] = role_class
    if instance:
        token_entry["agent_instance"] = instance
    
    # FIX-2: 注册 token 到 gate workspace connection.json
    from tools.aipos_cli.enroll_client import (
        load_or_create_connection_json,
        upsert_token_entry,
        write_connection_json,
        ensure_lybra_dir,
    )
    import sys
    print(f"[enroll_exchange] FIX-2: Registering token to gate workspace", file=sys.stderr)
    try:
        lybra_dir = ensure_lybra_dir(root)
        connection_data = load_or_create_connection_json(lybra_dir, gate_url=None)  # 保留现有 gate_url
        rotated = upsert_token_entry(connection_data, token_entry)
        # AIPOS-R6S 大项C③: 同角色多 token 收敛(移除 test.* 陈旧 token)
        from tools.aipos_cli.enroll_client import converge_role_tokens
        removed_instances, converged = converge_role_tokens(connection_data, role)
        if converged:
            print(f"[enroll_exchange] Converged same-role tokens (removed test.*: {removed_instances})", file=sys.stderr)
        write_connection_json(lybra_dir, connection_data)
        print(f"[enroll_exchange] Token written to {lybra_dir}/connection.json (rotated={rotated})", file=sys.stderr)
        
        # 热加载: 通知当前 gate 进程重载 token registry
        print(f"[enroll_exchange] Calling _reload_token_registry()", file=sys.stderr)
        _reload_token_registry()
        print(f"[enroll_exchange] _reload_token_registry() returned", file=sys.stderr)
    except Exception as exc:
        # 注册失败不阻断 exchange(客户端已有 token),但记录警告
        import logging
        import traceback
        logging.warning(f"FIX-2: Failed to register token to gate workspace: {exc}")
        print(f"[enroll_exchange] ERROR in token registration: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        rotated = False
    
    # Mark code as used
    try:
        mark_enrollment_used(root, code)
    except ValueError as exc:
        return _error_result(f"Failed to mark enrollment code as used: {exc}")
    
    return _tool_result({
        "ok": True,
        "operation": "roles_enroll_exchange",
        "token_entry": token_entry,
        "rotated": rotated,
        "security_notice": "Store this token securely with 0600 permissions. It will not be shown again.",
    })


def lybra_roles_enroll_revoke(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-362: revoke an enrollment code. Idempotent.

    Owner-gated: requires owner_authorization_ref.
    """
    from tools.aipos_cli.enrollment import revoke_enrollment_code
    args = arguments or {}
    code_id = str(args.get("code_id") or "").strip()
    owner_authorization_ref = str(args.get("owner_authorization_ref") or "").strip() or None
    reason = str(args.get("reason") or "").strip()
    if not code_id:
        return _error_result("Missing required parameter: code_id")
    if not owner_authorization_ref:
        return _error_result(
            "Missing required parameter: owner_authorization_ref. "
            "Enrollment code revocation is owner-gated (AIPOS-362 security model)."
        )
    root = _repo_root()
    try:
        revoked = revoke_enrollment_code(
            root,
            code_id,
            by=owner_authorization_ref,
            reason=reason or f"owner-authorization-ref: {owner_authorization_ref}",
        )
    except ValueError as exc:
        return _error_result(f"Enrollment code revocation failed: {exc}")
    return _tool_result({
        "ok": True,
        "operation": "roles_enroll_revoke",
        "revoked": revoked,
    })


def lybra_roles_enroll_list(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-362: list all enrollment codes (without showing the actual code values).

    Read-only, no owner-gating required.
    """
    from tools.aipos_cli.enrollment import list_enrollment_codes
    root = _repo_root()
    try:
        codes = list_enrollment_codes(root, include_code=False)
    except Exception as exc:
        return _error_result(f"Failed to list enrollment codes: {exc}")
    return _tool_result({
        "ok": True,
        "operation": "roles_enroll_list",
        "enrollments": codes,
    })


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any] | None], dict[str, Any]]] = {
    "lybra_queue_list": lybra_queue_list,
    "lybra_project_status": lybra_project_status,
    "lybra_gate_version": lybra_gate_version,
    "lybra_distribution_manifest": lybra_distribution_manifest,
    "lybra_distribution_fetch": lybra_distribution_fetch,
    "lybra_task_preview": lybra_task_preview,
    "lybra_return_content": lybra_return_content,
    "lybra_validate": lybra_validate,
    "lybra_context_pack_build": lybra_context_pack_build,
    "lybra_intake_submit_dry_run": lybra_intake_submit_dry_run,
    "lybra_intake_submit_confirm": lybra_intake_submit_confirm,
    "lybra_owner_decision_record_dry_run": lybra_owner_decision_record_dry_run,
    "lybra_owner_decision_record_confirm": lybra_owner_decision_record_confirm,
    "lybra_draft_publish_dry_run": lybra_draft_publish_dry_run,
    "lybra_draft_publish_confirm": lybra_draft_publish_confirm,
    "lybra_draft_submit_dry_run": lybra_draft_submit_dry_run,
    "lybra_draft_submit_confirm": lybra_draft_submit_confirm,
    "lybra_queue_claim_dry_run": lybra_queue_claim_dry_run,
    "lybra_queue_claim_confirm": lybra_queue_claim_confirm,
    "lybra_queue_return_dry_run": lybra_queue_return_dry_run,
    "lybra_queue_return_confirm": lybra_queue_return_confirm,
    "lybra_audit_dispatch_dry_run": lybra_audit_dispatch_dry_run,
    "lybra_audit_dispatch_confirm": lybra_audit_dispatch_confirm,
    "lybra_audit_verdict_dry_run": lybra_audit_verdict_dry_run,
    "lybra_audit_verdict_confirm": lybra_audit_verdict_confirm,
    "lybra_bench_audit_submit_dry_run": lybra_bench_audit_submit_dry_run,
    "lybra_bench_audit_confirm": lybra_bench_audit_confirm,
    "lybra_queue_close_dry_run": lybra_queue_close_dry_run,
    "lybra_queue_close_confirm": lybra_queue_close_confirm,
    "lybra_converge_r_cards": lybra_converge_r_cards,
    "lybra_mark_concluded": lybra_mark_concluded,
    "lybra_queue_withdraw_dry_run": lybra_queue_withdraw_dry_run,
    "lybra_queue_withdraw_confirm": lybra_queue_withdraw_confirm,
    "lybra_queue_amend_dry_run": lybra_queue_amend_dry_run,
    "lybra_queue_amend_confirm": lybra_queue_amend_confirm,
    "lybra_task_progress": lybra_task_progress,
    "lybra_gate_guidance": lybra_gate_guidance,
    "lybra_naming_profile_get": lybra_naming_profile_get,
    "lybra_naming_profile_set": lybra_naming_profile_set,
    "lybra_roles_register": lybra_roles_register,
    "lybra_roles_remove": lybra_roles_remove,
    "lybra_roles_enroll_code": lybra_roles_enroll_code,
    "lybra_roles_enroll_exchange": lybra_roles_enroll_exchange,
    "lybra_roles_enroll_revoke": lybra_roles_enroll_revoke,
    "lybra_roles_enroll_list": lybra_roles_enroll_list,
}


READ_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "lybra_queue_list",
        "description": "List Lybra task queue state using existing read-only backend semantics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_project_status",
        "description": "The gate's own read-only project view: resolved home_root, active project (or resolution error), and established projects.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_gate_version",
        "description": "AIPOS-369: Report the gate's runtime version (git commit, VERSION file). Returns the actual deployed snapshot commit, used by lybra-deploy to verify deployment took effect.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_distribution_manifest",
        "description": "AIPOS-C4B: Return the distribution manifest for the caller's role (file list + sha256 hashes + source commit per distributable). Worker-side `lybra sync` compares local _distributed against this and pulls only diffs. Passive, read-only, role-scoped.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_distribution_fetch",
        "description": "AIPOS-C4B: Return base64 file content for requested distribution files (by distribution_id + paths). Paths are validated against the role manifest; traversal/out-of-manifest paths are rejected. Worker-initiated pull only, zero push.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "distribution_id": {"type": "string", "description": "Distribution entry id (e.g. executor-loop-extension)"},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "File paths relative to the distribution source"},
            },
            "required": ["distribution_id", "paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_gate_guidance",
        "description": "AIPOS-R6E ⑦: Gate self-describing help - returns verb usage guidance with complete parameter shapes and copyable examples. Query by verb_name for detailed usage, or omit to list all available verbs for your role. Zero source-code archaeology.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "verb_name": {"type": "string", "description": "Optional: specific verb to get detailed usage for (e.g., 'lybra_queue_return_dry_run')"},
                "role": {"type": "string", "description": "Optional: query verbs for a specific role (executor/auditor/advisor/owner)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_task_preview",
        "description": "Build a read-only task session preview for one task by task_id or path. Set include_body=true to receive the task card body markdown (requires queue_claim scope).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "path": {"type": "string"},
                "actor": {"type": "string"},
                "include_body": {"type": "boolean", "description": "When true, include body_markdown field with the task card body content. Requires queue_claim scope. Default false."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_return_content",
        "description": (
            "AIPOS-320: read-only tool that returns the RETURN.md body for a returned task card. "
            "Used by auditors for cross-machine evidence retrieval. "
            "Requires queue_claim scope. Returns error (not silent empty) when no RETURN.md exists for the task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task_id to read RETURN.md for. Path is strictly confined to task_cards/<task_id>/RETURN.md."},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_validate",
        "description": "Run Lybra validation using existing read-only backend semantics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_context_pack_build",
        "description": "Build a read-only Context Pack preview by task_id, path, or orchestration_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "path": {"type": "string"},
                "orchestration_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_gate_guidance",
        "description": (
            "AIPOS-330 S3: Read-only gate guidance. Given a task_id and role, the gate answers: "
            "which verb to call next, what params are required, and whether the role's scope is sufficient. "
            "Data-driven from the gate's flow description (collaboration_profile × task fields → gate chain). "
            "This replaces hand-written kickoff instructions: kickoff says 'ask the gate', gate answers with facts. "
            "Gate provides facts only — it does not execute, does not decide whether the agent should act."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to get guidance for."},
                "role": {"type": "string", "description": "Role requesting guidance (executor/auditor/advisor)."},
            },
            "required": ["task_id", "role"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_naming_profile_get",
        "description": (
            "AIPOS-350 S2: read the naming profile (alias layer) for the active workspace. "
            "Returns prefix_mapping (role->prefix), project_segment (+aliases), host_segment (+aliases), "
            "and pre-generated canonical names for all known roles. Read-only; no special scope required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


WRITE_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "lybra_draft_publish_dry_run",
        "description": (
            "When to use: create a controlled-execute preview for publishing a reviewed draft into the pending queue through the gated, accountable publish surface (the channel for TUI / AI-authored drafts). "
            "Prerequisites: this MCP connection must have a capability_token with draft_publish scope; path must point to a draft under 5_tasks/drafts/; this tool writes nothing. "
            "Return structure: a controlled-execute envelope with verdict, planned_writes (pending task + publish record), dry_run_token, dry_run_snapshot_hash, and dry_run_expires_at. Confirm requires the Owner-only owner_confirm scope."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "actor": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_draft_publish_confirm",
        "description": (
            "When to use: confirm a gated draft_publish dry-run, writing the pending task and a publish record that attributes the confirming Owner (confirmer_role/token_ref/fingerprint), closing F-c4. "
            "Prerequisites: capability_token with BOTH draft_publish AND owner_confirm scope (a publisher-only token is SCOPE_DENIED — the publish confirmer is provably the Owner); dry_run_token from lybra_draft_publish_dry_run; owner_confirmation_token: OWNER_CONFIRMED. "
            "Return structure: a controlled-execute envelope with performed_writes and the publish record reference."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
                "actor": {"type": "string"},
            },
            "required": ["dry_run_token", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_draft_submit_dry_run",
        "description": (
            "When to use: create a controlled-execute preview for submitting a NEW task-card DRAFT into 5_tasks/drafts/ (the planner's proposal zone). This does NOT put anything into truth. "
            "Prerequisites: this MCP connection must have a capability_token with draft_submit scope (the planner role); pass frontmatter (task-card fields incl. task_id/title) and optional body; you pass NO file path — the target is DRAFTS_DIR / draft_slug(task_id).md, regex-locked so a draft can never land outside drafts/. "
            "Return structure: a controlled-execute envelope with verdict, planned_writes, rendered_markdown, and dry_run_token. Confirm does NOT require owner_confirm (a draft is a proposal). Landing the draft into the queue is a separate Owner-gated draft_publish."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "frontmatter": {"type": "object"},
                "body": {"type": "string"},
                "actor": {"type": "string"},
            },
            "required": ["frontmatter"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_draft_submit_confirm",
        "description": (
            "When to use: confirm a lybra_draft_submit_dry_run, writing the task-card draft into 5_tasks/drafts/. "
            "Prerequisites: capability_token with draft_submit scope; dry_run_token from lybra_draft_submit_dry_run. owner_confirm is NOT required (a draft is a proposal, not truth — the Owner gate is at publish). "
            "Return structure: a controlled-execute envelope with performed_writes (the draft file under 5_tasks/drafts/)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
            },
            "required": ["dry_run_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_intake_submit_dry_run",
        "description": (
            "When to use: create a controlled execute preview for normalized external intake that should become an Owner-reviewed draft. "
            "Prerequisites: this MCP connection must have a capability_token with intake_submit scope; source_tag must match an approved external intake source from external_intake_registry.md; client_tag must map to an existing project; project-scoped capability tokens are enforced (PROJECT_SCOPE_DENIED when the active project is not in the token's projects); this tool does not publish or execute work. "
            "Return structure: a controlled execute envelope with verdict, planned_writes, dry_run_token, dry_run_snapshot_hash, dry_run_created_at, dry_run_expires_at, and rendered draft content. "
            "Next-step hint: pass dry_run_token to lybra_intake_submit_confirm; the resulting draft waits for Owner publish and no agent takes automatic follow-up action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "source_tag": {"type": "string"},
                "client_tag": {"type": "string"},
                "external_ref": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "submitted_at": {"type": "string"},
                "submitter_ref": {"type": "string"},
                "capability_scope": {"type": "object"},
                "priority_hint": {"type": "string"},
                "requested_due_date": {"type": "string"},
                "source_thread_ref": {"type": "string"},
                "owner_approval_evidence": {"type": "string"},
            },
            "required": [
                "source_tag",
                "client_tag",
                "external_ref",
                "title",
                "body",
                "submitted_at",
                "submitter_ref",
                "capability_scope",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_intake_submit_confirm",
        "description": (
            "When to use: confirm a prior lybra_intake_submit_dry_run after reviewing its planned_writes and rendered draft. "
            "Prerequisites: this MCP connection must have a capability_token with intake_submit scope; dry_run_token is required and must come from the immediately preceding dry-run flow; the dry-run token must be unexpired and its snapshot must still match. "
            "Return structure: a controlled execute result with performed_writes when the external intake draft is written, or a structured teaching error with error_code, message, suggested_next_action, and doc_ref. "
            "Next-step hint: confirm only writes a draft under 5_tasks/drafts/external_intake; the draft waits for Owner publish and no agent takes automatic follow-up action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_owner_decision_record_dry_run",
        "description": (
            "When to use: create a controlled execute preview for either (a) recording a scoped Owner decision with out-of-band approval evidence, or (b) AIPOS-250 arming a PreAuthorized autonomy envelope by passing an autonomy_policy block. "
            "Prerequisites: this MCP connection must have a capability_token with owner_decision_record scope (Owner-only). For the general decision path, owner_approval_evidence (aligned with applies_to) + capability_scope (including owner_decision_record) are required. For the autonomy_policy ENVELOPE path, pass ONLY decision_id + autonomy_policy (agent_or_role, active_from, expires_at, max_tasks, task_selector) — owner_approval_evidence / applies_to / approval_scope / capability_scope are NOT required and are auto-derived, because the approval is the in-band harness owner_confirm at confirm time, not an out-of-band artifact. This tool does not publish, mutate queues, or execute follow-up work. "
            "Return structure: a controlled execute envelope with verdict, planned_writes, dry_run_token, dry_run_snapshot_hash, autonomy_policy_grant (true on the envelope path), and rendered Owner decision record content. "
            "Next-step hint: pass dry_run_token to lybra_owner_decision_record_confirm (envelope path additionally requires owner_confirm + owner_confirmation_token OWNER_CONFIRMED); confirm writes a records artifact under 5_tasks/records/owner_decisions (+ the policy artifact under 5_tasks/policies on the envelope path) and no agent takes automatic follow-up action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "decision_id": {"type": "string"},
                "decision_type": {"type": "string"},
                "decision_status": {"type": "string"},
                "decided_at": {"type": "string"},
                "decided_by_ref": {"type": "string"},
                "captured_by": {"type": "string"},
                "capture_surface": {"type": "string"},
                "decision_summary": {"type": "string"},
                "decision_rationale": {"type": "string"},
                "applies_to": {"type": "object"},
                "approval_scope": {"type": "object"},
                "owner_approval_evidence": {"type": "object"},
                "refs": {"type": "array", "items": {"type": "string"}},
                "capability_scope": {"type": "object"},
                # AIPOS-250: the envelope-arming block. Present => the writer takes the relaxed grant
                # path (evidence/applies_to/capability_scope auto-derived). Absent => full AIPOS-110
                # decision schema, whose remaining fields the WRITER enforces conditionally (not the
                # schema): only decision_id is unconditionally required here, so the envelope path is
                # reachable through a schema-validating MCP client (which would otherwise strip an
                # undeclared autonomy_policy and force the evidence fields — the O3 defect).
                "autonomy_policy": {"type": "object"},
            },
            "required": ["decision_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_owner_decision_record_confirm",
        "description": (
            "When to use: confirm a prior lybra_owner_decision_record_dry_run after reviewing its planned_writes and rendered Owner decision record. "
            "Prerequisites: this MCP connection must have a capability_token with owner_decision_record scope; dry_run_token is required and must come from the immediately preceding dry-run flow; the dry-run token must be unexpired and its snapshot must still match. "
            "Return structure: a controlled execute result with performed_writes when the Owner decision record is written, or a structured teaching error with error_code, message, suggested_next_action, and doc_ref. "
            "Next-step hint: confirm only writes a record under 5_tasks/records/owner_decisions; it does not publish drafts, mutate queues, append orchestration events, or continue runtime execution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_claim_dry_run",
        "description": (
            "When to use: claim one pending task for one concrete agent instance. autonomy_mode Supervised returns a dry-run preview requiring Owner per-task confirm; autonomy_mode PreAuthorized asks the gate to check owner_policy_ref against Owner-signed autonomy envelopes and, ONLY on a strict structural match, auto-release the claim in one step (no confirm) — any miss/expiry/count-bound falls back to a Supervised preview. "
            "Prerequisites: this MCP connection must have a capability_token with queue_claim scope; actor must equal the resolved canonical agent_instance in this first slice; owner_policy_ref is required (for PreAuthorized it must name an active envelope policy_id). Optional actual_model / reported_tokens are agent-reported capability-ledger fields (recorded, never verified). "
            "Return structure: a controlled execute envelope with verdict, autonomy_mode (Supervised | PreAuthorized), owner_policy_ref, canonical_agent_instance, lease_status proposed; Supervised additionally returns dry_run_token + owner_confirmation_required, PreAuthorized returns performed_moves for the auto-released claim. "
            "Next-step hint: for Supervised, present the preview to Owner then confirm via lybra_queue_claim_confirm with OWNER_CONFIRMED; for PreAuthorized the claim is already landed and attributed to the policy — do NOT call confirm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "task_path": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "autonomy_mode": {"type": "string", "enum": ["Supervised", "PreAuthorized"]},
                "owner_policy_ref": {"type": "string"},
                "runtime_profile": {"type": "string"},
                "active_session_id": {"type": "string"},
                "context_bundle_ack": {"type": "string"},
                "with_records": {"type": "boolean"},
                "claim_reason": {"type": "string"},
                "actual_model": {"type": "string"},
                "reported_tokens": {"type": "integer"},
            },
            "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_claim_confirm",
        "description": (
            "When to use: confirm a prior Supervised MCP queue-claim dry-run after Owner has reviewed the exact preview. "
            "Prerequisites: this MCP connection must have a capability_token with queue_claim scope; dry_run_token is required; owner_confirmation_token must be OWNER_CONFIRMED; actor, agent_instance, and owner_policy_ref must match the dry-run preview. "
            "Return structure: a controlled execute result with performed_moves for the pending-to-claimed queue move, canonical_agent_instance, owner_policy_ref, provenance minimums, and lease_status proposed. "
            "Next-step hint: confirm only claims the task; it does not launch a worker, renew a lease, dispatch audit, finalize, or execute follow-up work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "owner_policy_ref": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_return_dry_run",
        "description": (
            "When to use: create a Supervised MCP preview for returning completed executor work on one already claimed task. "
            "Prerequisites: this MCP connection must have a capability_token with queue_return scope; autonomy_mode must be Supervised; actor must equal the resolved canonical agent_instance in this first slice; owner_policy_ref is required; normalized non-secret executor evidence is required. "
            "Return structure: a controlled execute envelope with verdict, planned_writes, dry_run_token, dry_run_snapshot_hash, confirmation_preview, canonical_agent_instance, owner_policy_ref, executor_status completed, audit_readiness ready, and lease_status proposed. "
            "Next-step hint: present confirmation_preview to Owner, then pass dry_run_token to lybra_queue_return_confirm with owner_confirmation_token OWNER_CONFIRMED; this tool does not activate leases, dispatch audit, record audit PASS, or finalize."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "task_path": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "autonomy_mode": {"type": "string", "enum": ["Supervised"]},
                "owner_policy_ref": {"type": "string"},
                "claim_id": {"type": "string"},
                "active_session_id": {"type": "string"},
                "result_summary": {"type": "string"},
                "artifact_refs": {"type": "array", "items": {"type": "string"}},
                "scratch_dir": {"type": "string"},
                "scratch_artifact_refs": {"type": "array", "items": {"type": "string"}},
                "completion_report_ref": {"type": "string"},
                "executor_status": {"type": "string", "enum": ["completed"]},
                "audit_readiness": {"type": "string", "enum": ["ready"]},
                "return_reason": {"type": "string"},
                "return_body": {"type": "string", "description": "AIPOS-320: optional RETURN.md body text. When provided, the gate writes it to task_cards/<ID>/RETURN.md on confirm. Path is strictly confined to task_cards/<ID>/RETURN.md (no path escape)."},
                "actual_model": {"type": "string"},
                "reported_tokens": {"type": "integer"},
                "agent_runtime": {
                    "type": "object",
                    "description": "AIPOS-261: optional agent-REPORTED runtime bundle (capability ledger, never verified). {harness, model_self_reported, tokens_in, tokens_out}.",
                    "properties": {
                        "harness": {"type": "string"},
                        "model_self_reported": {"type": "string"},
                        "tokens_in": {"type": "integer"},
                        "tokens_out": {"type": "integer"}
                    },
                    "additionalProperties": False
                },
            },
            "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_return_confirm",
        "description": (
            "When to use: confirm a prior Supervised MCP queue-return dry-run after Owner has reviewed the exact confirmation_preview. "
            "Prerequisites: this MCP connection must have a capability_token with queue_return scope; dry_run_token is required; owner_confirmation_token must be OWNER_CONFIRMED; actor, agent_instance, and owner_policy_ref must match the dry-run preview. "
            "Return structure: a controlled execute result with performed_writes for the claimed task metadata update, canonical_agent_instance, owner_policy_ref, provenance minimums, executor_status completed, audit_readiness ready, and lease_status proposed. "
            "Next-step hint: confirm only marks executor completion plus audit readiness; it does not activate a lease, write records, dispatch audit, record audit PASS, or finalize."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "owner_policy_ref": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_audit_dispatch_dry_run",
        "description": (
            "When to use: create a Supervised MCP preview that dispatches one audit-ready returned task into one pending audit task for a distinct auditor. "
            "Prerequisites: this MCP connection must have a capability_token with audit_dispatch scope; autonomy_mode must be Supervised; actor must equal resolved canonical agent_instance; owner_policy_ref and audit_agent_instance are required. "
            "Return structure: a controlled execute envelope with planned source-task update, pending audit task creation, audit-dispatch record creation, dry_run_token, confirmation_preview, reviewed_executor_instance, and no lease activation. "
            "Next-step hint: present confirmation_preview to Owner, then pass dry_run_token to lybra_audit_dispatch_confirm with owner_confirmation_token OWNER_CONFIRMED; confirm does not claim the audit task, launch an auditor, record a verdict, finalize, or unblock accepted work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_task_id": {"type": "string"},
                "source_task_path": {"type": "string"},
                "task_id": {"type": "string"},
                "task_path": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "autonomy_mode": {"type": "string", "enum": ["Supervised"]},
                "owner_policy_ref": {"type": "string"},
                "audit_task_id": {"type": "string"},
                "audit_task_title": {"type": "string"},
                "audit_by": {"type": "string"},
                "audit_agent_instance": {"type": "string"},
                "dispatch_reason": {"type": "string"},
            },
            "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref", "audit_task_id", "audit_agent_instance"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_audit_dispatch_confirm",
        "description": (
            "When to use: confirm a prior Supervised MCP audit-dispatch dry-run after Owner has reviewed the exact preview. "
            "Prerequisites: this MCP connection must have a capability_token with audit_dispatch scope; dry_run_token is required; owner_confirmation_token must be OWNER_CONFIRMED; actor, agent_instance, and owner_policy_ref must match the dry-run preview. "
            "Return structure: a controlled execute result with performed_writes for source task update, pending audit task creation, and audit-dispatch record creation. "
            "Next-step hint: confirm only creates an audit task and dispatch provenance; the auditor must claim it separately through lybra_queue_claim_dry_run/confirm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "owner_policy_ref": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_audit_verdict_dry_run",
        "description": (
            "When to use: create a Supervised MCP preview for recording an independent audit verdict on a reviewed returned task. "
            "Prerequisites: this MCP connection must have a capability_token with audit_verdict scope; autonomy_mode must be Supervised; actor must equal resolved canonical agent_instance; the audit task must be claimed by this auditor and distinct from reviewed_executor_instance. "
            "Return structure: a controlled execute envelope with planned reviewed-task update, audit-task update, audit-verdict record creation, auditor session update, dry_run_token, confirmation_preview, and no finalize or accepted-work unblock. "
            "Next-step hint: present confirmation_preview to Owner, then pass dry_run_token to lybra_audit_verdict_confirm with owner_confirmation_token OWNER_CONFIRMED; PASS maps only to audit_pass."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "audit_task_id": {"type": "string"},
                "audit_task_path": {"type": "string"},
                "task_path": {"type": "string"},
                "reviewed_task_id": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "autonomy_mode": {"type": "string", "enum": ["Supervised"]},
                "owner_policy_ref": {"type": "string"},
                "audit_claim_id": {"type": "string"},
                "audit_session_id": {"type": "string"},
                "audit_dispatch_record_ref": {"type": "string"},
                "reviewed_return_record_ref": {"type": "string"},
                "verdict": {"type": "string", "enum": [Verdict.PASS, Verdict.PASS_WITH_NOTES, Verdict.FAIL, Verdict.BLOCK, Verdict.WARN, Verdict.NEEDS_OWNER]},
                "findings_summary": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "recommended_next_action": {"type": "string"},
                "owner_waiver_ref": {"type": "string"},
                "agent_runtime": {
                    "type": "object",
                    "description": "AIPOS-265 FIX-1: optional agent-REPORTED runtime bundle for the auditor (capability ledger, never verified). Symmetric to the return half; populates the auditor's 档案 so its verdict-line popup shows a known profile. {harness, model_self_reported, tokens_in, tokens_out}.",
                    "properties": {
                        "harness": {"type": "string"},
                        "model_self_reported": {"type": "string"},
                        "tokens_in": {"type": "integer"},
                        "tokens_out": {"type": "integer"}
                    },
                    "additionalProperties": False
                },
            },
            "required": ["reviewed_task_id", "actor", "agent_instance", "autonomy_mode", "owner_policy_ref", "verdict"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_audit_verdict_confirm",
        "description": (
            "When to use: confirm a prior Supervised MCP audit-verdict dry-run after Owner has reviewed the exact preview. "
            "Prerequisites: this MCP connection must have a capability_token with audit_verdict scope; dry_run_token is required; owner_confirmation_token must be OWNER_CONFIRMED; actor, agent_instance, and owner_policy_ref must match the dry-run preview. "
            "Return structure: a controlled execute result with performed_writes for reviewed task audit status, audit task metadata, audit-verdict record, and auditor session event. "
            "Next-step hint: confirm only records the verdict; it does not finalize, unblock accepted work, activate a lease, or dispatch follow-up work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "owner_policy_ref": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_bench_audit_submit_dry_run",
        "description": (
            "AIPOS-336: Submit bench audit conclusion (审结提交) for non-code tasks (deploy/config/content/research). "
            "Non-code tasks walk the bench audit path (304 D2 branch-1): executor produces evidence → "
            "verification station ring2 checklist (auto-checks) + ring3 Owner eye-verify → Owner confirm → close. "
            "This verb is the '审结提交' step: executor/advisor submits evidence + conclusion; gate runs ring2 "
            "auto-checks (data-driven from EVIDENCE_TYPES registry); record lands in workspace "
            "(`5_tasks/records/bench_audit/<task_id>/`). Requires bench_audit_submit scope (executor/advisor). "
            "Executor CAN dry_run but CANNOT self-confirm (acceptance #2). Confirmation is lybra_bench_audit_confirm "
            "(bench_audit_confirm scope: advisor/owner). Returns controlled_execute envelope with verdict, "
            "planned_writes, dry_run_token, checklist (ring2 results + ring3 human items), ring2_summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID being audited (bench path)."},
                "actor": {"type": "string", "description": "Actor identifier (executor or advisor)."},
                "evidence_type": {"type": "string", "description": "Evidence type id (deploy/config/content/research). Optional; inferred from task_mode if absent."},
                "task_mode": {"type": "string", "description": "Task mode (deploy/config/content/research). Used to infer evidence_type if not explicit."},
                "conclusion": {"type": "string", "description": "Conclusion: pass | pass_with_notes | fail | needs_human."},
                "evidence_refs": {
                    "type": "array",
                    "description": "List of evidence references: [{check_id, ref, note?}]. check_id matches ring2 checklist.",
                    "items": {"type": "object"},
                },
                "notes": {"type": "string", "description": "Optional notes (附注)."},
            },
            "required": ["task_id", "actor", "conclusion"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_bench_audit_confirm",
        "description": (
            "AIPOS-336: Confirm bench audit submission — execute the dry-run token. "
            "Requires bench_audit_confirm scope (advisor/owner). Executor does NOT hold this scope "
            "(acceptance #2: 执行体无法自行 confirm). This is the Owner/advisor gate that 审结 the bench "
            "submission after reviewing the ring2 checklist and performing ring3 eye-verify. "
            "The dry_run_token is produced by lybra_bench_audit_submit_dry_run. This verb re-validates "
            "the preview, then writes the bench audit record to the workspace. "
            "甲案家族: bench_audit_confirm 是审结提交(非 Owner 门); Owner 确认发生在验证台按键 "
            "(owner_verification_record), 不在动词层重复设门. Returns executed response with ok, "
            "performed_writes, checklist, ring2_summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string", "description": "Token from lybra_bench_audit_submit_dry_run."},
                "actor": {"type": "string", "description": "Confirming actor (advisor or owner)."},
            },
            "required": ["dry_run_token", "actor"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_close_dry_run",
        "description": (
            "When to use: preview closing a claimed task (claimed/ -> completed/) with closure evidence. "
            "AIPOS-283 gate close verb: the finalize settlement step that moves a returned task out of the claimed queue. "
            "Prerequisites: queue_close scope (executor/advisor); task_id of a claimed task; closure_evidence with at least one of: "
            "finalize_commit_hash, finalize_return_ref, owner_verification_ref. Task must have a return record. "
            "Return structure: dry-run preview with closure_id, closure_record_path, related_audit_task_refs. "
            "Does NOT require owner_confirm (executor callable)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "actor": {"type": "string"},
                "closure_evidence": {
                    "type": "object",
                    "description": "At least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref.",
                    "properties": {
                        "finalize_commit_hash": {"type": "string"},
                        "finalize_return_ref": {"type": "string"},
                        "owner_verification_ref": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["task_id", "actor", "closure_evidence"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_close_confirm",
        "description": (
            "When to use: confirm closing a claimed task. Moves card claimed/ -> completed/ and writes closure record (append-only). "
            "Auto-closes audit-derived <ID>R cards if still claimed. "
            "Prerequisites: queue_close scope; same task_id, actor, closure_evidence as dry-run. "
            "Does NOT require owner_confirm (executor callable per AIPOS-283 S2)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "actor": {"type": "string"},
                "closure_evidence": {
                    "type": "object",
                    "description": "At least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref.",
                    "properties": {
                        "finalize_commit_hash": {"type": "string"},
                        "finalize_return_ref": {"type": "string"},
                        "owner_verification_ref": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["task_id", "actor", "closure_evidence"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_converge_r_cards",
        "description": (
            "AIPOS-354 S2: batch convergence of existing R cards (audit-derived cards). "
            "Scans claimed/ and pending/ for audit cards whose reviewed task already has a verdict. "
            "Moves matching R cards to completed/ with closure metadata. Never deletes records. "
            "Use dry_run=true first to preview, then dry_run=false to execute."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Who is running the convergence (default: system)."},
                "dry_run": {"type": "boolean", "description": "Preview only (default: true)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_mark_concluded",
        "description": (
            "AIPOS-354 S3: explicit mark-concluded for report-style audits (bypass path). "
            "For scenarios where no formal verdict was landed via gate. "
            "Leaves a machine-readable closure marker so the card moves to completed/ "
            "without producing a断层 card. Requires task_id and at least one of report_path or conclusion_note."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to mark concluded."},
                "actor": {"type": "string", "description": "Who is marking this concluded."},
                "report_path": {"type": "string", "description": "Path to the audit report (evidence)."},
                "conclusion_note": {"type": "string", "description": "Free-text conclusion note."},
                "dry_run": {"type": "boolean", "description": "Preview only (default: true)."},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_withdraw_dry_run",
        "description": (
            "AIPOS-315: dry-run preview for withdrawing a task from queue. "
            "Supports pending or claimed tasks. Moves to withdrawn/ state with reason. "
            "S3 in-transit protection: blocks if active session detected within last hour. "
            "Does NOT delete existing records (claims/returns/sessions preserved). "
            "Prerequisites: queue_withdraw scope, task_id, reason, actor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "actor": {"type": "string"},
                "reason": {"type": "string", "description": "Why this task is being withdrawn."},
            },
            "required": ["task_id", "actor", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_withdraw_confirm",
        "description": (
            "AIPOS-315: confirm withdraw — execute the withdrawal (move card to withdrawn/). "
            "Re-validates all inputs and in-transit checks before executing. "
            "Does NOT require owner_confirm (advisor callable)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "actor": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["task_id", "actor", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_amend_dry_run",
        "description": (
            "AIPOS-315: dry-run preview for amending a pending task's frontmatter or body. "
            "Only works on pending (unclaimed) tasks. Claimed tasks cannot be amended (in-transit work protection). "
            "Writes amendment record (append-only) preserving original content. "
            "Prerequisites: queue_amend scope, task_id, amendments dict, amendment_reason, actor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "actor": {"type": "string"},
                "amendments": {
                    "type": "object",
                    "description": "Dict with frontmatter fields to update or 'body' key for body changes.",
                    "additionalProperties": True,
                },
                "amendment_reason": {"type": "string", "description": "Why this amendment is needed."},
            },
            "required": ["task_id", "actor", "amendments", "amendment_reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_amend_confirm",
        "description": (
            "AIPOS-315: confirm amend — execute the amendment (update task + write amendment record). "
            "Re-validates pending state before executing. "
            "Does NOT require owner_confirm (advisor callable for governance amendments)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "actor": {"type": "string"},
                "amendments": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "amendment_reason": {"type": "string"},
            },
            "required": ["task_id", "actor", "amendments", "amendment_reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_task_progress",
        "description": (
            "AIPOS-323: agent self-reports task progress events (started/progress/completed/blocked). "
            "Agent pushes task facts to gate, which writes append-only records to 5_tasks/records/events/<task_id>/. "
            "Gate does NOT maintain online/offline state, does NOT judge timeouts, does NOT push to anyone. "
            "This is the 'agent opens mouth' direction (取代顾问观察式); gate只记录不判活、不心跳、不推送. "
            "跨机可用 (MCP HTTP). Frequency decided by agent; no gate-side timeout enforcement."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID being reported on."},
                "event_type": {"type": "string", "enum": ["started", "progress", "completed", "blocked"], "description": "Event type."},
                "actor": {"type": "string", "description": "Agent instance reporting this event."},
                "summary": {"type": "string", "description": "Optional free-text summary of progress."},
                "model_self_reported": {"type": "string", "description": "Optional self-reported model identifier."},
                "stage": {"type": "string", "description": "Optional stage marker (e.g., 'analysis', 'implementation')."},
                "reason": {"type": "string", "description": "Optional reason (e.g., for blocked events)."},
            },
            "required": ["task_id", "event_type", "actor"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_naming_profile_set",
        "description": (
            "AIPOS-350 S2: modify the naming profile (alias layer) for the active workspace. "
            "Supports: set prefix for a role (prefix_role + prefix_value), set project_segment, "
            "set host_segment, add project/host segment aliases. All changes are append-only logged "
            "and take effect immediately. No code change needed to rename anything. "
            "Returns the updated naming profile and regenerated canonical names."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Who is making this change (for audit trail)."},
                "reason": {"type": "string", "description": "Why this naming change is needed."},
                "prefix_role": {"type": "string", "description": "Role name to set prefix for (e.g. 'planner')."},
                "prefix_value": {"type": "string", "description": "New prefix value (e.g. 'advisor')."},
                "project_segment": {"type": "string", "description": "New canonical project segment."},
                "project_segment_aliases": {"type": "array", "items": {"type": "string"}, "description": "Aliases for project segment."},
                "host_segment": {"type": "string", "description": "New canonical host segment."},
                "host_segment_aliases": {"type": "array", "items": {"type": "string"}, "description": "Aliases for host segment."},
                "add_project_alias": {"type": "string", "description": "Add a single project segment alias."},
                "add_host_alias": {"type": "string", "description": "Add a single host segment alias."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_roles_register",
        "description": (
            "AIPOS-352F1: register a custom role in the workspace registry (project.json). "
            "A custom role maps a name to a built-in role class (e.g., 'kiwiaiops' → 'executor'). "
            "Owner-gated: requires owner_authorization_ref (AIPOS-346F2 audit trail). "
            "Registry carries ZERO scope fields (anti-privilege-escalation). "
            "Scopes are resolved from the built-in class at call time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Custom role name (lowercase, alphanumeric + hyphens, max 32 chars)."},
                "builtin_class": {"type": "string", "description": "Built-in role class to map to (e.g. executor, auditor)."},
                "class": {"type": "string", "description": "Alias for builtin_class."},
                "owner_authorization_ref": {"type": "string", "description": "AIPOS-346F2: reference to owner authorization for this registration."},
                "reason": {"type": "string", "description": "Reason for registering this custom role."},
            },
            "required": ["name", "owner_authorization_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_roles_remove",
        "description": (
            "AIPOS-352F1: remove a custom role from the workspace registry (project.json). Idempotent. "
            "Owner-gated: requires owner_authorization_ref (AIPOS-346F2 audit trail). "
            "Appends an unregister entry to the custom roles log."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Custom role name to remove."},
                "owner_authorization_ref": {"type": "string", "description": "AIPOS-346F2: reference to owner authorization for this removal."},
                "reason": {"type": "string", "description": "Reason for removing this custom role."},
            },
            "required": ["name", "owner_authorization_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_roles_enroll_code",
        "description": (
            "AIPOS-362: generate a one-time enrollment code for remote agent credential bootstrap. "
            "Owner-gated: requires owner_authorization_ref. "
            "The enrollment code can be exchanged for a capability token via lybra_roles_enroll_exchange. "
            "The code is NOT a token — it's a temporary credential that can be safely transmitted to the remote agent. "
            "Token values never appear in logs or agent-facing outputs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Role to bind (e.g., executor, auditor, or custom role)."},
                "instance": {"type": "string", "description": "Optional instance name to bind (e.g., exec.lybra.mac1); omit for any instance."},
                "ttl": {"type": "integer", "description": "Time-to-live in seconds; omit for no expiration."},
                "owner_authorization_ref": {"type": "string", "description": "Reference to owner authorization for this enrollment."},
                "reason": {"type": "string", "description": "Reason for generating this enrollment code."},
            },
            "required": ["role", "owner_authorization_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_roles_enroll_exchange",
        "description": (
            "AIPOS-362: exchange an enrollment code for a capability token. "
            "PUBLIC endpoint (no token required) — the enrollment code itself is the authentication. "
            "Once exchanged, the code is marked as used and cannot be reused. "
            "The token value is returned ONLY in this response and never logged. "
            "Remote agents should save the token with 0600 permissions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Enrollment code to exchange."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_roles_enroll_revoke",
        "description": (
            "AIPOS-362: revoke an enrollment code. Idempotent. "
            "Owner-gated: requires owner_authorization_ref. "
            "Revoked codes cannot be exchanged for tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code_id": {"type": "string", "description": "Enrollment code ID to revoke."},
                "owner_authorization_ref": {"type": "string", "description": "Reference to owner authorization for this revocation."},
                "reason": {"type": "string", "description": "Reason for revoking this enrollment code."},
            },
            "required": ["code_id", "owner_authorization_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_roles_enroll_list",
        "description": (
            "AIPOS-362: list all enrollment codes (without showing the actual code values). "
            "Read-only, no owner-gating required. Shows status, role, instance, and expiration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def visible_tool_descriptors() -> list[dict[str, Any]]:
    # AIPOS-229 (Slice 5): introspection is ADVISORY (R-α: descriptors are not the enforcement
    # path — the authoritative project gate is the dispatch choke-point). Best-effort: when the
    # token is project-scoped AND the active project RESOLVES to one outside the token's projects,
    # reflect the narrowing by listing nothing (every call would be PROJECT_SCOPE_DENIED). When the
    # active project cannot be resolved, do NOT hide here — the call path still fail-closes. A token
    # without `projects` is unaffected (back-compat byte-identical: no resolution attempted).
    _projects = _capability_token().get("projects")
    if _projects:
        # AIPOS-FND-17F1: apply the SAME project inference as tools/call (dispatch_tool via
        # _resolve_request_project) so tool DISCOVERY and INVOCATION route consistently. A
        # single-project token (e.g. agency->kiwiaiagency) must infer its OWN project here, not
        # resolve the default workspace project ('lybra') and then hide every tool as out-of-scope
        # (which returned {"tools": []} to standard MCP clients -> zero tools registered).
        _active = _resolve_request_project({})
        if _active is None:
            try:
                _active = _resolve_active_project_for(_repo_root(), None)
            except (ValueError, FileNotFoundError, OSError):
                _active = None
        if _active is not None and _active not in [str(p) for p in _projects]:
            return []
    descriptors = list(READ_TOOL_DESCRIPTORS)
    if _intake_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_intake_submit"))
    if _owner_decision_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_owner_decision_record"))
    if _draft_publish_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_draft_publish"))
    if _draft_submit_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_draft_submit"))
    if _queue_claim_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_claim"))
    if _queue_return_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_return"))
    if _audit_dispatch_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_audit_dispatch"))
    if _audit_verdict_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_audit_verdict"))
    # AIPOS-336: bench_audit_submit visible to executor/advisor; bench_audit_confirm to advisor/owner
    if _bench_audit_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_bench_audit_submit"))
    if _bench_audit_confirm_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"] == "lybra_bench_audit_confirm")
    if _queue_close_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_close"))
        # AIPOS-354: converge_r_cards and mark_concluded share queue_close scope
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"] in ("lybra_converge_r_cards", "lybra_mark_concluded"))
    if _queue_withdraw_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_withdraw"))
    if _queue_amend_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_amend"))
    if _task_progress_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_task_progress"))
    # AIPOS-350: naming profile verbs are always visible (governance config, append-only logged)
    descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_naming_profile"))
    # AIPOS-352F1: custom role write verbs are always visible (owner-gated via owner_authorization_ref param)
    descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_roles_register") or tool["name"].startswith("lybra_roles_remove"))
    # AIPOS-362: enrollment verbs - enroll_code/revoke/list are owner-gated (always visible); enroll_exchange is PUBLIC (always visible)
    descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_roles_enroll"))
    return descriptors


def lybra_gate_guidance(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-R6E ⑧: Gate自描述闭环——返回动词用法指南
    
    根据调用者角色返回其token面可用动词的完整参数shape+示例。
    目标:任何角色撞墙时产品自答,零源码考古。
    
    Args:
        verb_name: 可选,查询特定动词的用法
        role: 可选,查询特定角色的可用动词
    
    Returns:
        如果verb_name提供:该动词的详细用法(参数shape+示例)
        否则:当前角色可用的所有动词清单
    """
    args = arguments or {}
    verb_name = str(args.get("verb_name") or "").strip()
    requested_role = str(args.get("role") or "").strip()
    
    # 确定当前角色
    cap = _capability_token()
    current_role = str(cap.get("role") or "").strip() or "unknown"
    
    # 常见动词的用法指南(简化版)
    verb_guides = {
        "lybra_queue_return_dry_run": {
            "description": "Preview task return (executor reports completion)",
            "scope_required": "queue_return",
            "roles": ["executor"],
            "required_parameters": {
                "task_id": "Task ID to return (e.g., 'AIPOS-R6E')",
                "actor": "Agent instance returning the task",
                "agent_instance": "Same as actor for Supervised mode",
                "owner_policy_ref": "Policy reference (e.g., 'pol_lybra_dev_9')",
                "autonomy_mode": "Must be 'Supervised'",
                "result_summary": "Summary of work completed",
            },
            "optional_parameters": {
                "artifact_refs": "List of changed files",
                "active_session_id": "Session ID for tracking",
            },
            "example": {
                "task_id": "AIPOS-R6E",
                "actor": "exec.lybra.kiwiai-dev",
                "agent_instance": "exec.lybra.kiwiai-dev",
                "owner_policy_ref": "pol_lybra_dev_9",
                "autonomy_mode": "Supervised",
                "result_summary": "Completed all six targets",
                "artifact_refs": ["tools/lybra-deploy", "tests/test_r6e_write_contract.py"],
            },
            "next_step": "Call lybra_queue_return_confirm with the dry_run_token and owner_confirmation_token='OWNER_CONFIRMED'",
        },
        "lybra_queue_amend_dry_run": {
            "description": "Preview task amendment (modify pending task metadata)",
            "scope_required": "queue_amend",
            "roles": ["advisor", "owner"],
            "required_parameters": {
                "task_id": "Task ID to amend (must be in pending/ state)",
                "amendments": "Dict of frontmatter fields to update (e.g., {'title': 'New title'})",
                "amendment_reason": "Reason for this amendment",
                "actor": "Agent performing the amendment",
            },
            "example": {
                "task_id": "AIPOS-R6E",
                "amendments": {"title": "Updated title per owner feedback"},
                "amendment_reason": "Clarify scope based on design review",
                "actor": "advisor.lybra",
            },
            "next_step": "Call lybra_queue_amend_confirm with the dry_run_token",
        },
        "lybra_owner_decision_record": {
            "description": "Record owner decision on a task or policy matter",
            "scope_required": "owner_decision_record",
            "roles": ["owner"],
            "required_parameters": {
                "decision_id": "Unique decision ID (e.g., 'DEC-001')",
                "task_id": "Related task ID (or 'N/A' for policy decisions)",
                "decision_type": "Type: scope_change, priority_change, resource_allocation, etc.",
                "decision": "Decision outcome: approved, rejected, deferred, amended",
                "rationale": "Reason for this decision",
                "actor": "Owner actor",
            },
            "example": {
                "decision_id": "DEC-R6E-001",
                "task_id": "AIPOS-R6E",
                "decision_type": "scope_change",
                "decision": "approved",
                "rationale": "Aligns with zero-manual-intervention roadmap",
                "actor": "owner",
            },
        },
        "lybra_mark_concluded": {
            "description": "Mark a task as concluded (report-style audit bypass)",
            "scope_required": "queue_close",
            "roles": ["auditor", "advisor", "owner"],
            "required_parameters": {
                "task_id": "Task ID to conclude",
                "actor": "Agent marking conclusion",
            },
            "optional_parameters": {
                "report_path": "Path to audit report",
                "conclusion_note": "Brief conclusion note",
            },
            "example": {
                "task_id": "AIPOS-R6E",
                "actor": "audit.lybra",
                "conclusion_note": "All six targets verified and passing tests",
            },
        },
    }
    
    # 如果查询特定动词
    if verb_name:
        if verb_name in verb_guides:
            guide = verb_guides[verb_name]
            return _tool_result({
                "ok": True,
                "verb_name": verb_name,
                "guide": guide,
                "current_role": current_role,
            })
        else:
            return _tool_result({
                "ok": False,
                "error": f"No guidance available for verb: {verb_name}",
                "available_verbs": list(verb_guides.keys()),
                "suggestion": "Check verb name spelling or use lybra_gate_guidance without verb_name to list all available verbs",
            })
    
    # 否则返回当前角色可用的动词清单
    role_to_check = requested_role if requested_role else current_role
    available_verbs = []
    
    for verb, guide in verb_guides.items():
        if role_to_check in guide.get("roles", []) or role_to_check == "owner":
            available_verbs.append({
                "verb_name": verb,
                "description": guide["description"],
                "scope_required": guide.get("scope_required"),
            })
    
    return _tool_result({
        "ok": True,
        "role": role_to_check,
        "available_verbs": available_verbs,
        "usage": "Call lybra_gate_guidance with verb_name parameter to get detailed usage for a specific verb",
    })


TOOL_DESCRIPTORS = READ_TOOL_DESCRIPTORS
