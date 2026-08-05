"""AIPOS-340 — 命令构建器（填充参数，生成可执行命令）。

根据动作类别 + 任务状态，构建完整的 MCP verb 调用或 CLI 命令（所有参数填好）。
"""
from pathlib import Path
from typing import Any
import os


def build_command(action: str, state: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """构建下一步完整命令。
    
    返回:
    {
        "command_type": "mcp_verb" | "cli" | "wait_human" | "done",
        "verb": "lybra_queue_claim_dry_run" | None,
        "args": {...},  # 填好的参数
        "copyable_line": "python3 .../... --args '{...}'",  # manual 模式粘贴行
    }
    """
    task_id = state["task_id"]
    task_frontmatter = state.get("task_frontmatter", {})
    latest_claim = state.get("latest_claim", {})
    
    if action == "claim_task":
        # 执行派工：lybra_queue_claim_dry_run
        # 需要参数：task_id, actor, agent_instance, autonomy_mode, owner_policy_ref
        assigned_to = task_frontmatter.get("assigned_to") or task_frontmatter.get("agent_instance")
        if not assigned_to:
            return {
                "command_type": "wait_human",
                "verb": None,
                "args": {},
                "copyable_line": f"任务 {task_id} 缺 assigned_to/agent_instance，无法自动派工",
            }
        
        # AIPOS-340F1 S6: 从工作区读活跃信封
        from tools.aipos_cli.policy_resolver import find_active_policy
        
        # 尝试从环境变量或默认路径获取治理仓路径
        governance_root = Path(os.getenv("LYBRA_GOVERNANCE_ROOT", 
                                         "/home/kiwi/ai-project-os/2_projects/lybra"))
        owner_policy_ref = find_active_policy(governance_root, role="exec", policy_type="dev")
        
        if not owner_policy_ref:
            return {
                "command_type": "wait_human",
                "verb": None,
                "args": {},
                "copyable_line": f"任务 {task_id} 无法解析活跃 claim 信封，检查治理仓 5_tasks/policies/ 或明确指定",
            }
        
        args = {
            "task_id": task_id,
            "actor": assigned_to,
            "agent_instance": assigned_to,
            "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": owner_policy_ref,
            "claim_reason": "auto-dispatch by turn advancer",
        }
        
        import json
        copyable = f"python3 ~/projects/lybra/task_cards/AIPOS-340/_gate_call.py call lybra_queue_claim_dry_run --args '{json.dumps(args)}'"
        
        return {
            "command_type": "mcp_verb",
            "verb": "lybra_queue_claim_dry_run",
            "args": args,
            "copyable_line": copyable,
        }
    
    elif action == "return_work":
        # 交回工作：lybra_queue_return_dry_run
        # 需要参数：task_id, actor, agent_instance, autonomy_mode, owner_policy_ref, 
        #          executor_status, audit_readiness, artifact_refs, etc.
        actor = latest_claim.get("canonical_agent_instance") or latest_claim.get("actor")
        claim_id = latest_claim.get("claim_id")
        
        # AIPOS-340F1 S6: 从工作区读活跃信封
        from tools.aipos_cli.policy_resolver import find_active_policy
        governance_root = Path(os.getenv("LYBRA_GOVERNANCE_ROOT", 
                                         "/home/kiwi/ai-project-os/2_projects/lybra"))
        owner_policy_ref = find_active_policy(governance_root, role="exec", policy_type="dev")
        
        if not owner_policy_ref:
            return {
                "command_type": "wait_human",
                "verb": None,
                "args": {},
                "copyable_line": f"任务 {task_id} 无法解析活跃 return 信封，检查治理仓 5_tasks/policies/ 或明确指定",
            }
        
        # 读取 RETURN.md 获取 artifact_refs（简化：这里只填必需参数）
        args = {
            "task_id": task_id,
            "actor": actor,
            "agent_instance": actor,
            "autonomy_mode": "Supervised",
            "owner_policy_ref": owner_policy_ref,
            "claim_id": claim_id,
            "executor_status": "completed",
            "audit_readiness": "ready",
            "result_summary": f"work completed for {task_id}",
            "artifact_refs": [],  # 简化：从 RETURN.md 解析
        }
        
        import json
        copyable = f"python3 ~/projects/lybra/task_cards/AIPOS-340/_gate_call.py call lybra_queue_return_dry_run --args '{json.dumps(args)}'"
        
        return {
            "command_type": "mcp_verb",
            "verb": "lybra_queue_return_dry_run",
            "args": args,
            "copyable_line": copyable,
        }
    
    elif action == "dispatch_audit":
        # 审计派工：lybra_audit_dispatch（owner-dispatch role）
        # 简化：这里输出"调用 lybra audit dispatch"（CLI 命令）
        copyable = f"lybra auditor dispatch --task-id {task_id}"
        return {
            "command_type": "cli",
            "verb": None,
            "args": {},
            "copyable_line": copyable,
        }
    
    elif action == "finalize":
        # finalize 卡生成 + 派工
        # 简化：输出"生成 finalize 卡并派工"
        copyable = f"# 生成 FINALIZE-{task_id}.md 并派工给 executor（机械步，可模板自动生成）"
        return {
            "command_type": "cli",
            "verb": None,
            "args": {},
            "copyable_line": copyable,
        }
    
    elif action == "wait_human":
        return {
            "command_type": "wait_human",
            "verb": None,
            "args": {},
            "copyable_line": "等待人工判断（见 rule）",
        }
    
    elif action == "done":
        return {
            "command_type": "done",
            "verb": None,
            "args": {},
            "copyable_line": "任务已完成，无下一步",
        }
    
    else:
        return {
            "command_type": "unknown",
            "verb": None,
            "args": {},
            "copyable_line": f"未知动作: {action}",
        }
