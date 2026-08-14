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

    Returns the profile dict, or a default profile if not found.
    """
    default_profile = {
        "code_enabled": True,
        "deploy_gate_enabled": False,
        "default_audit_mode": "agent",
        "output_locations": ["product_repo_worktree", "workspace_records"],
    }

    if not project_json_path.is_file():
        return default_profile

    try:
        data = json.loads(project_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_profile

    profile = data.get("collaboration_profile")
    if not isinstance(profile, dict):
        return default_profile

    # Merge with defaults for missing fields
    result = dict(default_profile)
    result.update(profile)
    return result


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


# ---------------------------------------------------------------------------
# S3: "Next step" resolution — given card + role, what should the agent do?
# ---------------------------------------------------------------------------

def _read_task_frontmatter(task_path: Path) -> dict[str, Any]:
    """Read frontmatter from a task card."""
    if not task_path.is_file():
        return {}
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end < 0:
        return {}

    fm: dict[str, Any] = {}
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().strip("'\"")
            fm[key.strip()] = value
    return fm


def _find_task_card(workspace_root: Path, task_id: str) -> Path | None:
    """Find a task card by ID in the queue directories."""
    for queue_dir in ["pending", "claimed", "blocked", "completed"]:
        path = workspace_root / "5_tasks" / "queue" / queue_dir / f"{task_id.lower()}.md"
        if path.is_file():
            return path
    return None


def _infer_task_status(workspace_root: Path, task_id: str) -> str:
    """Infer the current status of a task from queue location."""
    for status, dir_name in [
        ("pending", "pending"),
        ("claimed", "claimed"),
        ("blocked", "blocked"),
        ("completed", "completed"),
    ]:
        path = workspace_root / "5_tasks" / "queue" / dir_name / f"{task_id.lower()}.md"
        if path.is_file():
            return status
    return "unknown"


def _has_return_record(workspace_root: Path, task_id: str) -> bool:
    """Check if a return record exists for this task."""
    returns_dir = workspace_root / "5_tasks" / "records" / "returns" / task_id
    return returns_dir.is_dir() and any(returns_dir.glob("return_*.md"))


def _has_audit_dispatch_record(workspace_root: Path, task_id: str) -> bool:
    """Check if an audit dispatch record exists for this task."""
    dispatch_dir = workspace_root / "5_tasks" / "records" / "audit_dispatch" / task_id
    return dispatch_dir.is_dir() and any(dispatch_dir.glob("dispatch_*.md"))


def _has_verdict_record(workspace_root: Path, task_id: str) -> bool:
    """Check if an audit verdict record exists for this task."""
    for subdir in ["verdicts", "audit_verdicts"]:
        verdict_dir = workspace_root / "5_tasks" / "records" / subdir / task_id
        if verdict_dir.is_dir() and any(verdict_dir.glob("verdict_*.md")):
            return True
    return False


def resolve_next_step(
    task_id: str,
    role: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """S3: Given card + role, resolve what the agent should do next.

    Returns: {
        "task_id": str,
        "role": str,
        "status": str,              # current task status
        "branch_id": str,           # which gate chain branch
        "branch_label": str,
        "next_verb": str | None,    # verb to call next (None if no more steps or not implemented)
        "next_verb_not_implemented": bool,  # True if the verb is a placeholder
        "required_params": list[str],
        "scope_needed": str,
        "scope_sufficient": bool,   # whether the role's scope is sufficient
        "scope_holders": list[str], # roles that hold the needed scope
        "description": str,
        "remaining_steps": list[dict],  # all remaining steps in the chain
    }
    """
    from tools.aipos_cli.verb_contract import get_role_scope_map, who_holds_scope

    # Find task card and read frontmatter
    task_path = _find_task_card(workspace_root, task_id)
    task_fields = _read_task_frontmatter(task_path) if task_path else {}
    task_fields["task_id"] = task_id

    # Resolve collaboration profile
    project_json = workspace_root / "project.json"
    # Also check under 2_projects/lybra/ structure
    if not project_json.is_file():
        project_json = workspace_root / "2_projects" / "lybra" / "project.json"
    collab_profile = resolve_collaboration_profile(project_json)

    # Resolve gate chain
    chain = resolve_gate_chain(collab_profile, task_fields)

    # Infer current status
    status = _infer_task_status(workspace_root, task_id)

    # Determine which step is "next" based on status and records
    remaining_steps = _compute_remaining_steps(
        chain, status, workspace_root, task_id, role,
    )

    # Build result
    if remaining_steps:
        next_step = remaining_steps[0]
        scope_needed = next_step.get("scope_needed") or ""
        role_scopes = get_role_scope_map().get(role, [])
        scope_sufficient = scope_needed in role_scopes if scope_needed else True
        scope_holders = who_holds_scope(scope_needed) if scope_needed else []

        return {
            "task_id": task_id,
            "role": role,
            "status": status,
            "branch_id": chain.branch_id,
            "branch_label": chain.branch_label,
            "next_verb": next_step.get("verb_name"),
            "next_verb_not_implemented": next_step.get("not_implemented", False),
            "required_params": next_step.get("required_params", []),
            "scope_needed": scope_needed,
            "scope_sufficient": scope_sufficient,
            "scope_holders": scope_holders,
            "description": next_step.get("description", ""),
            "remaining_steps": remaining_steps,
        }
    else:
        return {
            "task_id": task_id,
            "role": role,
            "status": status,
            "branch_id": chain.branch_id,
            "branch_label": chain.branch_label,
            "next_verb": None,
            "next_verb_not_implemented": False,
            "required_params": [],
            "scope_needed": "",
            "scope_sufficient": True,
            "scope_holders": [],
            "description": "No remaining steps in the gate chain.",
            "remaining_steps": [],
        }


def _compute_remaining_steps(
    chain: GateChain,
    status: str,
    workspace_root: Path,
    task_id: str,
    role: str,
) -> list[dict[str, Any]]:
    """Compute remaining steps in the chain based on current state.

    Uses state markers (queue location, records) to determine which steps
    have already been completed.
    """
    all_steps = [{"verb_name": s.verb_name, "not_implemented": s.not_implemented,
                  "required_params": list(s.required_params), "scope_needed": s.scope_needed,
                  "description": s.description}
                 for s in chain.steps]

    if status == "pending":
        # Nothing done yet — all steps remain
        # Filter to role-appropriate steps
        return _filter_steps_for_role(all_steps, role)

    if status == "claimed":
        # Task has been claimed — skip claim steps
        remaining = []
        past_claim = False
        for step in all_steps:
            verb = step.get("verb_name") or ""
            if "claim" in verb:
                past_claim = True
                continue
            if past_claim:
                remaining.append(step)

        # Check if return records exist
        if _has_return_record(workspace_root, task_id):
            # Past return — skip to post-return steps
            remaining = [s for s in remaining if not _is_pre_return_step(s)]
            # For auditor role, start from audit steps
            if role == "auditor":
                remaining = [s for s in remaining if _is_audit_step(s)]
            else:
                remaining = [s for s in remaining if not _is_audit_step(s)]

        return _filter_steps_for_role(remaining, role)

    if status == "completed":
        return []

    # Unknown/blocked — return all steps
    return _filter_steps_for_role(all_steps, role)


def _is_pre_return_step(step: dict[str, Any]) -> bool:
    verb = step.get("verb_name") or ""
    return "claim" in verb or "return" in verb or "task_progress" in verb


def _is_audit_step(step: dict[str, Any]) -> bool:
    verb = step.get("verb_name") or ""
    return "audit" in verb


def _filter_steps_for_role(steps: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """Filter steps to those relevant for a given role.

    - executor: claim, return, task_progress, close
    - auditor: claim (audit claim), audit_verdict, task_progress
    - advisor/dispatcher: audit_dispatch
    """
    if role == "executor":
        return [s for s in steps if not _is_audit_step(s) or (s.get("verb_name") or "").startswith("lybra_queue_close")]
    elif role == "auditor":
        # Auditor needs: claim (for audit card), audit_verdict, task_progress
        return [s for s in steps if _is_audit_step(s) or "claim" in (s.get("verb_name") or "") or "task_progress" in (s.get("verb_name") or "")]
    elif role in ("advisor", "owner-dispatch"):
        return [s for s in steps if "audit_dispatch" in (s.get("verb_name") or "")]
    else:
        return steps


# ---------------------------------------------------------------------------
# S8: Capability profile evolution — gate chain must switch when profile changes
# ---------------------------------------------------------------------------

def resolve_next_step_with_profile(
    task_id: str,
    role: str,
    workspace_root: Path,
    collaboration_profile_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """S8: Like resolve_next_step but accepts a profile override for testing.

    When collaboration_profile changes (e.g., code_enabled flips), the same card
    must produce a different next-step answer — zero code changes.
    """
    from tools.aipos_cli.verb_contract import get_role_scope_map, who_holds_scope

    task_path = _find_task_card(workspace_root, task_id)
    task_fields = _read_task_frontmatter(task_path) if task_path else {}
    task_fields["task_id"] = task_id

    if collaboration_profile_override is not None:
        collab_profile = collaboration_profile_override
    else:
        project_json = workspace_root / "project.json"
        if not project_json.is_file():
            project_json = workspace_root / "2_projects" / "lybra" / "project.json"
        collab_profile = resolve_collaboration_profile(project_json)

    chain = resolve_gate_chain(collab_profile, task_fields)
    status = _infer_task_status(workspace_root, task_id)
    remaining_steps = _compute_remaining_steps(chain, status, workspace_root, task_id, role)

    if remaining_steps:
        next_step = remaining_steps[0]
        scope_needed = next_step.get("scope_needed") or ""
        role_scopes = get_role_scope_map().get(role, [])
        scope_sufficient = scope_needed in role_scopes if scope_needed else True
        scope_holders = who_holds_scope(scope_needed) if scope_needed else []

        return {
            "task_id": task_id,
            "role": role,
            "status": status,
            "branch_id": chain.branch_id,
            "branch_label": chain.branch_label,
            "next_verb": next_step.get("verb_name"),
            "next_verb_not_implemented": next_step.get("not_implemented", False),
            "required_params": next_step.get("required_params", []),
            "scope_needed": scope_needed,
            "scope_sufficient": scope_sufficient,
            "scope_holders": scope_holders,
            "description": next_step.get("description", ""),
            "remaining_steps": remaining_steps,
        }
    else:
        return {
            "task_id": task_id,
            "role": role,
            "status": status,
            "branch_id": chain.branch_id,
            "branch_label": chain.branch_label,
            "next_verb": None,
            "next_verb_not_implemented": False,
            "required_params": [],
            "scope_needed": "",
            "scope_sufficient": True,
            "scope_holders": [],
            "description": "No remaining steps in the gate chain.",
            "remaining_steps": [],
        }
