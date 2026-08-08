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
        # AIPOS-358: 审计派工 → lybra auditor launch (经 launch_auditor_runtime)
        # 参数来源: state + policy_resolver + workspace_root
        assigned_to = task_frontmatter.get("assigned_to") or task_frontmatter.get("agent_instance")
        reviewed_id = task_frontmatter.get("reviewed_task_id", "")
        if not reviewed_id and task_id.endswith("R") and len(task_id) > 1:
            reviewed_id = task_id[:-1]
        audit_card_path = str(state.get("task_path") or "")

        from tools.aipos_cli.policy_resolver import find_active_policy
        governance_root = Path(os.getenv("LYBRA_GOVERNANCE_ROOT",
                                         "/home/kiwi/ai-project-os/2_projects/lybra"))
        envelope = find_active_policy(governance_root, role="audit", policy_type="audit")
        if not envelope:
            envelope = "pol_lybra_audit_1"

        runtime_cmd = os.getenv("LYBRA_AUDITOR_RUNTIME_CMD",
                                "pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'")

        import sys as _sys
        copyable = (
            f"{_sys.executable} -m tools.aipos_cli.aipos_cli auditor launch"
            f" --task-id {task_id}"
            f" --reviewed-task-id {reviewed_id}"
            f" --workspace-root {workspace_root}"
            f" --envelope {envelope}"
            f" --runtime-cmd '{runtime_cmd}'"
        )
        if audit_card_path:
            copyable += f" --audit-card-path '{audit_card_path}'"

        return {
            "command_type": "cli",
            "verb": None,
            "args": {
                "task_id": task_id,
                "reviewed_task_id": reviewed_id,
                "workspace_root": str(workspace_root),
                "envelope": envelope,
                "runtime_cmd": runtime_cmd,
            },
            "copyable_line": copyable,
        }
    
    elif action == "finalize":
        # AIPOS-FND-2: finalize PASS task (git commit/push)
        # 参数来源: state + task_frontmatter
        assigned_to = task_frontmatter.get("assigned_to") or task_frontmatter.get("agent_instance")
        actor = assigned_to or "system"
        
        import sys as _sys
        copyable = (
            f"{_sys.executable} -m tools.aipos_cli.aipos_cli finalize"
            f" --task-id {task_id}"
            f" --actor {actor}"
            f" --workspace-root {workspace_root}"
        )
        
        return {
            "command_type": "cli",
            "verb": None,
            "args": {
                "task_id": task_id,
                "actor": actor,
                "workspace_root": str(workspace_root),
            },
            "copyable_line": copyable,
        }
    
    elif action == "resume_round":
        # AIPOS-340F5: resume 轮派工 — lybra pump run --round-type resume
        # 参数来源:
        #   --card: task_id
        #   --role: executor(固定)
        #   --round-type: resume(固定)
        #   --workspace-root: workspace_root
        #   --envelope: policy_resolver 实时解析
        #   --runtime: 从 task_frontmatter 或默认 pi
        #   --runtime-cmd: 从配置/档案读取;无配置 → 输出等待人工选择提示
        #   --executor-instance: 卡面 assigned_to
        assigned_to = task_frontmatter.get("assigned_to") or task_frontmatter.get("agent_instance")
        if not assigned_to:
            return {
                "command_type": "wait_human",
                "verb": None,
                "args": {},
                "copyable_line": f"任务 {task_id} 缺 assigned_to/agent_instance，无法构建 resume 命令",
            }

        # 信封:policy_resolver 实时解析
        from tools.aipos_cli.policy_resolver import find_active_policy
        governance_root = Path(os.getenv("LYBRA_GOVERNANCE_ROOT",
                                         "/home/kiwi/ai-project-os/2_projects/lybra"))
        envelope = find_active_policy(governance_root, role="exec", policy_type="dev")

        if not envelope:
            return {
                "command_type": "wait_human",
                "verb": None,
                "args": {},
                "copyable_line": f"任务 {task_id} 无法解析活跃 resume 信封，检查治理仓 5_tasks/policies/ 或明确指定",
            }

        # runtime:卡面指定或默认 pi
        runtime = task_frontmatter.get("runtime", "pi")

        # runtime-cmd:从配置读取;无配置 → 明确等待提示
        # 配置查找顺序:环境变量 LYBRA_RUNTIME_CMD > 产品仓 config/runtime_cmds.yaml
        runtime_cmd = os.getenv("LYBRA_RUNTIME_CMD")
        if not runtime_cmd:
            runtime_cmds_config = Path(workspace_root) / "config" / "runtime_cmds.yaml"
            if runtime_cmds_config.is_file():
                try:
                    import yaml
                    cfg = yaml.safe_load(runtime_cmds_config.read_text(encoding="utf-8"))
                    if isinstance(cfg, dict):
                        runtime_cmd = cfg.get(runtime) or cfg.get("default")
                except Exception:
                    pass

        # 模型顶替:failure_tracker + 配置表
        # 检查 failure_tracker 是否建议顶替(连败达阈值)
        model_note = ""
        try:
            from tools.turn_advancer.failure_tracker import check_retry_limit
            retry_info = check_retry_limit(governance_root, task_id)
            if retry_info["action"] == "trigger_substitution":
                model_note = f" [模型顶替: {retry_info['reason']} — 等待人工选择替代模型]"
            elif retry_info["action"] == "block_escalate":
                model_note = f" [顶替升级: {retry_info['reason']} — 等待 Owner 裁定]"
        except Exception:
            pass

        # 组装命令
        ws_str = str(workspace_root)
        if runtime_cmd:
            copyable = (
                f"lybra pump run --card {task_id} --role executor --round-type resume"
                f" --workspace-root {ws_str}"
                f" --envelope {envelope}"
                f" --runtime {runtime}"
                f" --runtime-cmd '{runtime_cmd}'"
                f" --executor-instance {assigned_to}"
                f"{model_note}"
            )
            args = {
                "card": task_id,
                "role": "executor",
                "round_type": "resume",
                "workspace_root": ws_str,
                "envelope": envelope,
                "runtime": runtime,
                "runtime_cmd": runtime_cmd,
                "executor_instance": assigned_to,
            }
            return {
                "command_type": "cli",
                "verb": None,
                "args": args,
                "copyable_line": copyable,
            }
        else:
            # 无 runtime-cmd 配置 → 命令含明确等待提示
            copyable = (
                f"lybra pump run --card {task_id} --role executor --round-type resume"
                f" --workspace-root {ws_str}"
                f" --envelope {envelope}"
                f" --runtime {runtime}"
                f" --runtime-cmd <等待人工选择:无 runtime-cmd 配置,请指定拉起命令模板>"
                f" --executor-instance {assigned_to}"
                f"{model_note}"
            )
            return {
                "command_type": "wait_human",
                "verb": None,
                "args": {
                    "card": task_id,
                    "role": "executor",
                    "round_type": "resume",
                    "workspace_root": ws_str,
                    "envelope": envelope,
                    "runtime": runtime,
                    "executor_instance": assigned_to,
                    "missing": "runtime_cmd",
                },
                "copyable_line": copyable,
            }

    elif action == "wait_executor":
        return {
            "command_type": "wait_human",
            "verb": None,
            "args": {},
            "copyable_line": f"等待执行体完成(执行中,不动作)",
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
