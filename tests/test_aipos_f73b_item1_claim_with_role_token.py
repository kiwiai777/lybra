"""AIPOS-F73B件①测试: pending 卡按角色 token 认领 + worktree + spawn action"""

import tempfile
from pathlib import Path
import json
import pytest
from unittest.mock import patch, MagicMock
from tools.aipos_cli.next_resolver import (
    derive_next_step,
    execute_derived_action,
)


def test_f73b_item1_pending_claim_with_role_token():
    """件①: pending 卡认领使用角色 token，创建 worktree，输出 spawn_worker action"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        # 1. 创建产品仓结构
        (workspace / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (workspace / "5_tasks" / "records" / "claims").mkdir(parents=True)
        
        task_id = "AIPOS-F73B-ITEM1"
        assigned_to = "exec.lybra.test"
        
        # 2. 写入 pending 任务卡
        task_path = workspace / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md"
        task_path.write_text(f"""---
task_id: {task_id}
task_mode: code
assigned_to: {assigned_to}
---

# {task_id}

测试任务。
""", encoding="utf-8")
        
        # 3. 推导应该输出 claim 命令
        derivation = derive_next_step(task_id, workspace)
        assert derivation["derivable"] is True
        assert derivation["current_node"] == "pending"
        assert "queue claim" in derivation["command"]
        
        # 4. Mock subprocess 和 git 操作来测试 execute_derived_action
        with patch("tools.aipos_cli.next_resolver.subprocess.run") as mock_run:
            # Mock claim 成功
            mock_claim_result = MagicMock()
            mock_claim_result.returncode = 0
            mock_claim_result.stdout = "Claim successful"
            mock_claim_result.stderr = ""
            
            # Mock git 操作（worktree 创建）
            def run_side_effect(cmd, **kwargs):
                if "claim" in " ".join(cmd):
                    return mock_claim_result
                elif "git" in cmd[0] and "rev-parse" in cmd:
                    # 分支不存在
                    result = MagicMock()
                    result.returncode = 1
                    return result
                elif "git" in cmd[0] and "worktree" in cmd:
                    # 创建 worktree 成功
                    worktree_path = workspace / "card" / task_id
                    worktree_path.mkdir(parents=True, exist_ok=True)
                    result = MagicMock()
                    result.returncode = 0
                    result.stdout = f"Preparing worktree (new branch 'card/{task_id}')"
                    result.stderr = ""
                    return result
                return mock_claim_result
            
            mock_run.side_effect = run_side_effect
            
            # 执行认领
            result = execute_derived_action(
                derivation=derivation,
                workspace_root=workspace,
                connection_json=None,
            )
            
            # 5. 验证结果
            assert result["ok"] is True
            assert result["action_type"] == "claim"
            assert result["worktree_path"] is not None
            assert "spawn_action" in result
            
            spawn_action = result["spawn_action"]
            assert spawn_action["type"] == "spawn_worker"
            assert spawn_action["card"] == task_id
            assert spawn_action["instance"] == assigned_to
            assert task_id in spawn_action["worktree"]


def test_f73b_item1_claim_missing_assigned_to():
    """件①错误场景: 任务卡缺少 assigned_to 字段"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        (workspace / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        
        task_id = "AIPOS-F73B-NO-ASSIGN"
        
        # 任务卡没有 assigned_to
        task_path = workspace / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md"
        task_path.write_text(f"""---
task_id: {task_id}
task_mode: code
---

# {task_id}
""", encoding="utf-8")
        
        derivation = derive_next_step(task_id, workspace)
        
        with patch("tools.aipos_cli.next_resolver.subprocess.run"):
            result = execute_derived_action(
                derivation=derivation,
                workspace_root=workspace,
                connection_json=None,
            )
            
            # 应该失败并说明原因
            assert result["ok"] is False
            assert "assigned_to" in result["message"].lower() or "agent_instance" in result["message"].lower()
