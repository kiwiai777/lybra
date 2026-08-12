"""AIPOS-R4B-2: Audit verdict CLI self-service — 审计裁决自助落库.

审计 pi 自发现身份（从 LoopContext/自发现）→ dry_run → confirm → verdict record 落库。
参数从 LoopContext 出，审计 pi 不再要 GateClient snippet。

设计权威: LOOP-REDESIGN v2 §2 N4 (审计自助, 收编FND-15)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.loop_context import ConnectionResolver, LoopContext


def resolve_audit_context(
    *,
    workspace_root: Path | None = None,
    role: str = "auditor",
    gate_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """从 LoopContext 自发现审计身份参数。
    
    AIPOS-R4B-2 N4: 审计 pi 不再手写 GateClient snippet，所有参数从
    Context/自发现出。
    
    Args:
        workspace_root: 工作区根路径（用于 .lybra/ 自发现）
        role: 角色名（默认 auditor）
        gate_url: 显式指定 gate URL（可选，优先级最高）
        token: 显式指定 token（可选，优先级最高）
    
    Returns:
        {
            "gate_url": str,
            "token": str,
            "role": str,
            "agent_instance": str | None,
            "actor": str | None,
            "owner_policy_ref": str | None,
            "source": str,  # "explicit" | "auto_discovery" | "env"
        }
    
    Raises:
        ValueError: 无法解析必要参数
    """
    # Resolve workspace_root
    if workspace_root is None:
        from tools.aipos_cli.workspace_config import resolve_workspace_root
        try:
            workspace_root = resolve_workspace_root()
        except FileNotFoundError:
            # Fallback to current directory
            workspace_root = Path.cwd()
    
    # Resolve gate_url
    resolved_gate_url = ConnectionResolver.resolve_gate_url(
        workspace_root=workspace_root,
        explicit_url=gate_url,
    )
    
    # Resolve token
    resolved_token = ConnectionResolver.resolve_token(
        workspace_root=workspace_root,
        role=role,
        explicit_token=token,
    )
    
    # Try to load connection.json for additional metadata
    agent_instance = None
    actor = None
    owner_policy_ref = None
    source = "auto_discovery"
    
    try:
        lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
        if lybra_dir:
            connection_config = ConnectionResolver.load_connection_config(lybra_dir)
            
            # Find token entry for this role
            tokens = connection_config.get("tokens", [])
            for token_entry in tokens:
                if token_entry.get("role") == role:
                    agent_instance = token_entry.get("agent_instance")
                    actor = token_entry.get("actor") or agent_instance
                    break
            
            # Try to get policy reference
            policy_file = lybra_dir / "policy.json"
            if policy_file.exists():
                policy_data = json.loads(policy_file.read_text(encoding="utf-8"))
                owner_policy_ref = policy_data.get("policy_id")
    except Exception:
        # Discovery failed, use fallback
        pass
    
    if token and gate_url:
        source = "explicit"
    
    return {
        "gate_url": resolved_gate_url,
        "token": resolved_token,
        "role": role,
        "agent_instance": agent_instance,
        "actor": actor,
        "owner_policy_ref": owner_policy_ref,
        "source": source,
    }


def build_audit_verdict_dry_run_args(
    *,
    reviewed_task_id: str,
    verdict: str,
    context: dict[str, Any],
    audit_task_id: str | None = None,
    findings_summary: str | None = None,
    evidence_refs: list[str] | None = None,
    audit_claim_id: str | None = None,
    audit_session_id: str | None = None,
    audit_dispatch_record_ref: str | None = None,
    reviewed_return_record_ref: str | None = None,
    recommended_next_action: str | None = None,
    owner_waiver_ref: str | None = None,
) -> dict[str, Any]:
    """构建 audit_verdict dry_run 参数（从 context 填充身份字段）。
    
    Args:
        reviewed_task_id: 被审计任务 ID
        verdict: 裁决（PASS/FAIL/CONDITIONAL）
        context: 从 resolve_audit_context 得到的上下文
        其他参数: 可选的审计元数据
    
    Returns:
        准备传给 lybra_audit_verdict_dry_run 的参数字典
    """
    args = {
        "reviewed_task_id": reviewed_task_id,
        "actor": context.get("actor") or context.get("agent_instance") or "unknown-auditor",
        "agent_instance": context.get("agent_instance") or context.get("actor") or "unknown-auditor",
        "owner_policy_ref": context.get("owner_policy_ref") or "default-policy",
        "autonomy_mode": "Supervised",
        "verdict": verdict,
    }
    
    # 添加可选参数
    if audit_task_id:
        args["audit_task_id"] = audit_task_id
    if findings_summary:
        args["findings_summary"] = findings_summary
    if evidence_refs:
        args["evidence_refs"] = evidence_refs
    if audit_claim_id:
        args["audit_claim_id"] = audit_claim_id
    if audit_session_id:
        args["audit_session_id"] = audit_session_id
    if audit_dispatch_record_ref:
        args["audit_dispatch_record_ref"] = audit_dispatch_record_ref
    if reviewed_return_record_ref:
        args["reviewed_return_record_ref"] = reviewed_return_record_ref
    if recommended_next_action:
        args["recommended_next_action"] = recommended_next_action
    if owner_waiver_ref:
        args["owner_waiver_ref"] = owner_waiver_ref
    
    return args


def build_audit_verdict_confirm_args(
    *,
    dry_run_token: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """构建 audit_verdict confirm 参数。
    
    Args:
        dry_run_token: dry_run 返回的 token
        context: 从 resolve_audit_context 得到的上下文
    
    Returns:
        准备传给 lybra_audit_verdict_confirm 的参数字典
    """
    return {
        "dry_run_token": dry_run_token,
        "actor": context.get("actor") or context.get("agent_instance") or "unknown-auditor",
        "agent_instance": context.get("agent_instance") or context.get("actor") or "unknown-auditor",
        "owner_policy_ref": context.get("owner_policy_ref") or "default-policy",
        "owner_confirmation_token": "OWNER_CONFIRMED",
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
