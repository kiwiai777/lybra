"""AIPOS-330 S3/S6④/S7/S8 — Data-driven flow description.

Maps (collaboration_profile × task_fields) → gate chain. The mapping is expressed
as declarative data, NOT hardcoded conditionals. Adding a new branch = adding a
data entry, zero code changes (S6④, S7).

S7 hard constraint: the structure explicitly contains a branch dimension
(project_type × task_category → gate chain). Currently only "code + independent
agent audit" is filled; other branches (non-code → bench audit; code+deploy →
add deploy gate) have empty slots.

S8: Binds to AIPOS-304 D1/D2/D6 concrete definitions.
"""
from __future__ import annotations

from tools.schema_constants import RecordType

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# S7/S8: Branch dimension — project_type × task_category → gate chain
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateChainStep:
    """One step in a gate chain.

    verb_name: the real full verb name (from verb_contract registry).
               None if the verb is not yet implemented (S8: bench audit verbs).
    not_implemented: True if this verb is a placeholder (S8: "该动词尚未实现").
    required_params: list of required param names for this step.
    scope_needed: scope required to call this verb.
    description: human-readable description of what this step does.
    """
    verb_name: str | None
    not_implemented: bool = False
    required_params: list[str] = field(default_factory=list)
    scope_needed: str | None = None
    description: str = ""


@dataclass(frozen=True)
class GateChain:
    """A complete gate chain for a specific branch.

    branch_id: unique identifier for this branch (e.g., "code_no_deploy").
    branch_label: human-readable label.
    steps: ordered list of gate chain steps.
    """
    branch_id: str
    branch_label: str
    steps: tuple[GateChainStep, ...]


# ---------------------------------------------------------------------------
# S7: Three-branch gate chains (currently one filled, two with empty slots)
# S8: Bound to AIPOS-304 D2 concrete definitions
# ---------------------------------------------------------------------------

# Branch 2: Code without deploy → full independent audit (IMPLEMENTED)
_CODE_NO_DEPLOY_CHAIN = GateChain(
    branch_id="code_no_deploy",
    branch_label="代码任务（无部署）→ 完整独立审计",
    steps=(
        GateChainStep(
            verb_name="lybra_queue_claim_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            scope_needed="queue_claim",
            description="认领任务（dry-run 预览）",
        ),
        GateChainStep(
            verb_name="lybra_queue_claim_confirm",
            required_params=["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            scope_needed="queue_claim",  # + owner_confirm additionally
            description="确认认领",
        ),
        GateChainStep(
            verb_name="lybra_task_progress",
            required_params=["task_id", "event_type", "actor"],
            scope_needed="task_progress",
            description="上报进度（started/progress/completed/blocked）",
        ),
        GateChainStep(
            verb_name="lybra_queue_return_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            scope_needed="queue_return",
            description="归还工作（dry-run 预览）",
        ),
        GateChainStep(
            verb_name="lybra_queue_return_confirm",
            required_params=["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            scope_needed="queue_return",
            description="确认归还",
        ),
        GateChainStep(
            verb_name="lybra_audit_dispatch_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref", "audit_task_id", "audit_agent_instance"],
            scope_needed=RecordType.AUDIT_DISPATCH,
            description="派审（dry-run 预览）",
        ),
        GateChainStep(
            verb_name="lybra_audit_verdict_dry_run",
            required_params=["reviewed_task_id", "actor", "agent_instance", "autonomy_mode", "owner_policy_ref", "verdict"],
            scope_needed=RecordType.AUDIT_VERDICT,
            description="审计裁决（dry-run 预览）",
        ),
        GateChainStep(
            verb_name="lybra_queue_close_dry_run",
            required_params=["task_id", "actor", "closure_evidence"],
            scope_needed="queue_close",
            description="结案（dry-run 预览）",
        ),
    ),
)

# Branch 1: Non-code → bench audit (S8: verbs not yet implemented, explicit markers)
_NONCODE_CHAIN = GateChain(
    branch_id="noncode_bench_audit",
    branch_label="非代码任务 → 验证台审计（ring2 证据清单 + ring3 Owner 眼验）",
    steps=(
        GateChainStep(
            verb_name="lybra_queue_claim_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            scope_needed="queue_claim",
            description="认领任务",
        ),
        GateChainStep(
            verb_name="lybra_queue_claim_confirm",
            required_params=["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            scope_needed="queue_claim",
            description="确认认领",
        ),
        GateChainStep(
            verb_name="lybra_task_progress",
            required_params=["task_id", "event_type", "actor"],
            scope_needed="task_progress",
            description="上报进度",
        ),
        GateChainStep(
            verb_name="lybra_queue_return_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            scope_needed="queue_return",
            description="归还工作（产出证据）",
        ),
        GateChainStep(
            verb_name="lybra_queue_return_confirm",
            required_params=["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            scope_needed="queue_return",
            description="确认归还",
        ),
        # AIPOS-336: bench audit verbs implemented
        GateChainStep(
            verb_name="lybra_bench_audit_submit_dry_run",
            not_implemented=False,
            required_params=["task_id", "actor", "conclusion"],
            scope_needed="bench_audit_submit",
            description="提交验证台审计(ring2 自动检查 + ring3 Owner 眼验)",
        ),
        GateChainStep(
            verb_name="lybra_bench_audit_confirm",
            not_implemented=False,
            required_params=["dry_run_token", "actor"],
            scope_needed="bench_audit_confirm",
            description="确认验证台审计结果(审结提交)",
        ),
        GateChainStep(
            verb_name="lybra_queue_close_dry_run",
            required_params=["task_id", "actor", "closure_evidence"],
            scope_needed="queue_close",
            description="结案",
        ),
    ),
)

# Branch 3: Code with deploy → full audit + deploy gate (empty slot)
_CODE_WITH_DEPLOY_CHAIN = GateChain(
    branch_id="code_with_deploy",
    branch_label="代码任务（有部署）→ 完整审计 + 部署门",
    steps=(
        GateChainStep(
            verb_name="lybra_queue_claim_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            scope_needed="queue_claim",
            description="认领任务",
        ),
        GateChainStep(
            verb_name="lybra_queue_claim_confirm",
            required_params=["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            scope_needed="queue_claim",
            description="确认认领",
        ),
        GateChainStep(
            verb_name="lybra_task_progress",
            required_params=["task_id", "event_type", "actor"],
            scope_needed="task_progress",
            description="上报进度",
        ),
        GateChainStep(
            verb_name="lybra_queue_return_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            scope_needed="queue_return",
            description="归还工作",
        ),
        GateChainStep(
            verb_name="lybra_queue_return_confirm",
            required_params=["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            scope_needed="queue_return",
            description="确认归还",
        ),
        GateChainStep(
            verb_name="lybra_audit_dispatch_dry_run",
            required_params=["actor", "agent_instance", "autonomy_mode", "owner_policy_ref", "audit_task_id", "audit_agent_instance"],
            scope_needed=RecordType.AUDIT_DISPATCH,
            description="派审",
        ),
        GateChainStep(
            verb_name="lybra_audit_verdict_dry_run",
            required_params=["reviewed_task_id", "actor", "agent_instance", "autonomy_mode", "owner_policy_ref", "verdict"],
            scope_needed=RecordType.AUDIT_VERDICT,
            description="审计裁决",
        ),
        # Deploy gate: owner_verify required, irreversible confirmation
        GateChainStep(
            verb_name=None,
            not_implemented=True,
            required_params=["task_id", "deploy_evidence"],
            scope_needed="deploy_gate",
            description="部署门（owner_verify: required，不可逆确认，该动词尚未实现）",
        ),
        GateChainStep(
            verb_name="lybra_queue_close_dry_run",
            required_params=["task_id", "actor", "closure_evidence"],
            scope_needed="queue_close",
            description="结案",
        ),
    ),
)


# ---------------------------------------------------------------------------
# S7/S8: Branch registry — declarative data, extensible without code changes
# ---------------------------------------------------------------------------

# The branch registry: a dict of (project_type_key) → GateChain.
# project_type_key = (code_enabled, deploy_gate_enabled, default_audit_mode)
# Adding a new branch = adding an entry here, zero code changes.
_BRANCH_REGISTRY: dict[tuple[bool, bool, str], GateChain] = {
    # Branch 2: code enabled, no deploy, agent audit (IMPLEMENTED)
    (True, False, "agent"): _CODE_NO_DEPLOY_CHAIN,
    # Branch 1: non-code → bench audit (S8: bench verbs not yet implemented)
    (False, False, "bench"): _NONCODE_CHAIN,
    (False, False, "agent"): _NONCODE_CHAIN,  # non-code always uses bench regardless
    # Code project but task-level audit=bench → bench audit (S8: task can opt into lighter flow)
    (True, False, "bench"): _NONCODE_CHAIN,
    # Branch 3: code + deploy (empty slot)
    (True, True, "agent"): _CODE_WITH_DEPLOY_CHAIN,
    (True, True, "bench"): _CODE_WITH_DEPLOY_CHAIN,
}

# Default chain when no match found
_DEFAULT_CHAIN = _CODE_NO_DEPLOY_CHAIN


# ---------------------------------------------------------------------------
# S8: Resolve gate chain from collaboration_profile × task fields
# ---------------------------------------------------------------------------

def resolve_collaboration_profile(project_json_path: Path) -> dict[str, Any]:
    """Read collaboration_profile from project.json.

    AIPOS-F89 件① M14: 委托唯一读取口 workspace_config.get_collaboration_profile(缺省值唯一声明在 config.schema
    project_json.schema.collaboration_profile.default); 本处原第二读取口 + 第二份缺省删除。保留函数名供既有调用方。
    """
    from tools.aipos_cli.workspace_config import get_collaboration_profile

    return get_collaboration_profile(Path(project_json_path).parent)


def resolve_gate_chain(
    collaboration_profile: dict[str, Any],
    task_fields: dict[str, Any],
) -> GateChain:
    """Resolve the gate chain for a task given the project's collaboration profile.

    S8: The answer MUST be a function of collaboration_profile, not cached/hardcoded.
    Same card asked before/after profile change → different answer.

    Args:
        collaboration_profile: from project.json's collaboration_profile field
        task_fields: task-level fields (task_mode, output_target, deploy, audit, owner_verify)

    Returns:
        The matching GateChain.
    """
    code_enabled = bool(collaboration_profile.get("code_enabled", True))
    deploy_gate_enabled = bool(collaboration_profile.get("deploy_gate_enabled", False))
    default_audit_mode = str(collaboration_profile.get("default_audit_mode", "agent"))

    # Task-level override: audit=bench forces bench audit even for code projects
    task_audit = str(task_fields.get("audit", "")).strip()
    if task_audit == "bench":
        default_audit_mode = "bench"

    # Task-level: deploy=true forces deploy gate
    task_deploy = task_fields.get("deploy")
    if task_deploy is True or str(task_deploy).lower() == "true":
        deploy_gate_enabled = True

    # Task mode can NARROW code_enabled (non-code task modes force non-code chain)
    # but NOT WIDEN it (project says code_enabled=False, task_mode=code still uses non-code chain).
    # S8: the answer must be a function of collaboration_profile — the project decides
    # what flows it supports, tasks can only opt into lighter flows.
    task_mode = str(task_fields.get("task_mode", "")).strip()
    if task_mode in ("content", "research", "config"):
        code_enabled = False
    elif task_mode == "deploy":
        deploy_gate_enabled = True
    # NOTE: task_mode="code" does NOT override code_enabled=False from profile.
    # If the project doesn't support code flow, a code task still uses the non-code chain.
    # This ensures S8: changing profile.code_enabled actually changes the answer.

    key = (code_enabled, deploy_gate_enabled, default_audit_mode)
    return _BRANCH_REGISTRY.get(key, _DEFAULT_CHAIN)
