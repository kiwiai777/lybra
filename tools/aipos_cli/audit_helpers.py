"""AIPOS-R4B-2: Audit helpers — 审计裁决自助落库.

AIPOS-R6C: Renamed from audit_verdict_helper.py to audit_helpers.py for neutral naming.

审计 pi 自发现身份（从 LoopContext/自发现）→ dry_run → confirm → verdict record 落库。
参数从 LoopContext 出，审计 pi 不再要 GateClient snippet。

设计权威: LOOP-REDESIGN v2 §2 N4 (审计自助, 收编FND-15)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.loop_context import ConnectionResolver, LoopContext


def derive_audit_task_id(reviewed_task_id: str, repo_root: Path | None = None) -> str | None:
    """AIPOS-SMOKE-LOOP-1 FIX (坑①): 由被审任务 ID 派生审计 R 卡 task_id。

    CLI `--audit-task-id` 标可选但 gate 动词必填 (HAZARD-LEDGER 08-12 行11)。
    约定:审计 R 卡 task_id 形如 ``{reviewed}R`` / ``{reviewed}R1`` 等,且
    frontmatter ``derived_from == reviewed_task_id`` (gate 派生时盖章)。
    本函数查治理队列找唯一匹配的 R 卡;拿不准(0 或 >1 命中)返回 None,让 CLI 响亮报错。
    """
    from tools.aipos_cli.task_loader import load_all_tasks

    if not reviewed_task_id:
        return None
    candidates = {f"{reviewed_task_id}R", f"{reviewed_task_id}R1"}
    # primary: R 卡的 derived_from 指回被审任务 (gate 派生时盖章,最可靠)
    matches: list[str] = []
    try:
        for task in load_all_tasks(repo_root):
            meta = task.get("metadata", {}) if isinstance(task, dict) else {}
            tid = str(meta.get("task_id") or "")
            if not tid:
                continue
            if str(meta.get("derived_from") or "") == reviewed_task_id:
                matches.append(tid)
                continue
            # fallback: 命名约定 {reviewed}R / {reviewed}R1 且 task_id 以被审 ID 为前缀
            if tid in candidates or (tid.startswith(reviewed_task_id) and tid[len(reviewed_task_id):].rstrip("0123456789") == "R"):
                matches.append(tid)
    except Exception:
        return None
    # 去重;唯一命中才信,否则让 CLI 显式问
    uniq = sorted(set(matches))
    return uniq[0] if len(uniq) == 1 else None


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
            
            # AIPOS-R6C ⑩: policy_ref 自发现全序 (policy_resolver → env → 显式)
            from tools.aipos_cli.policy_resolver import find_active_policy
            owner_policy_ref = find_active_policy(workspace_root, role=role, policy_type="dev")
            
            # Env override if set
            if not owner_policy_ref:
                import os
                owner_policy_ref = os.environ.get("LYBRA_OWNER_POLICY_REF")
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
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """构建 audit_verdict dry_run 参数（从 context 填充身份字段）。
    
    Args:
        reviewed_task_id: 被审计任务 ID
        verdict: 裁决（PASS/PASS_WITH_NOTES/FAIL/BLOCK/WARN，从 enums.schema 读取）
        context: 从 resolve_audit_context 得到的上下文
        其他参数: 可选的审计元数据
    
    Returns:
        准备传给 lybra_audit_verdict_dry_run 的参数字典
    """
    args = {
        "reviewed_task_id": reviewed_task_id,
        "actor": context.get("actor") or context.get("agent_instance") or "unknown-auditor",
        "agent_instance": context.get("agent_instance") or context.get("actor") or "unknown-auditor",
        "owner_policy_ref": context.get("owner_policy_ref") or "unknown-policy",
        "autonomy_mode": "Supervised",
        "verdict": verdict,
    }

    # AIPOS-SMOKE-LOOP-1 FIX (坑①): audit_task_id gate 必填但 CLI 标可选 ——
    # 缺省时由 reviewed_task_id 自动派生 (查队列 R 卡),拿不准则不填让 CLI 响亮报错。
    if not audit_task_id:
        audit_task_id = derive_audit_task_id(reviewed_task_id, repo_root)
    # audit_task_id 是 gate 动词必填 (verbs.schema lybra_audit_verdict.task_id: required),
    # 这里始终带上 (派生成功 / 调用者显式给);派生为 None 时 gate 会 BLOCK 并给出明确提示。
    args["audit_task_id"] = audit_task_id or ""
    
    # 添加可选参数 (audit_task_id 已在上面处理)
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
        "owner_policy_ref": context.get("owner_policy_ref") or "unknown-policy",
        "owner_confirmation_token": "OWNER_CONFIRMED",
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
