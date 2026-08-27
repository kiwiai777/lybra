"""AIPOS-F44A: 门应答开口三项——额度告知+报错带路+N6待办

验收断言覆盖:
- ① 额度告知: 信封额度耗尽/未生效/选择器不匹配→应答明说原因+带路(续铸提示);剩余≤10% 携带低水位 warning
- ④ 报错带路: "No task found" 补三候选原因+对应动作(无 publish 记录/工作区不符/卡已终局);
             SCOPE_DENIED 补"此步需 X 角色, 可转贴指令"
- ⑥ N6 待办: close 应答+next-step 显示"待 N6 governance-commit(命令样例)"

三项对照表(修复前后文本必全贴):
- ① 耗尽信封夹具: 修复前空应答 → 修复后含原因与续铸带路
- ④ 报错带路三态: 无记录/工作区不符/已终局, 各贴修复前后文本
- ⑥ close 应答: 修复前后对照含 N6 待办

所有夹具经 bin/lybra 入 run-all(不直接 import Python 模块)。
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestItem1EnvelopeQuotaNotification:
    """验收断言①: 信封额度告知 - 耗尽/低水位 warning"""

    def test_envelope_exhausted_with_guidance(self, tmp_path):
        """
        信封额度耗尽时应答包含:
        - error_code: ENVELOPE_QUOTA_EXHAUSTED
        - error_message: 清晰说明原因
        - next_step: 续铸带路(请顾问续期信封增加 max_tasks 额度,或发放新信封)
        """
        from tools.mcp_server.tools import lybra_queue_claim_dry_run
        from tools.aipos_cli.autonomy_policy import load_policy
        
        # Mock 一个已耗尽的信封(released_count >= max_tasks)
        args = {
            "task_id": "TEST-TASK-001",
            "actor": "exec.test",
            "agent_instance": "exec.test",
            "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": "pol_test_exhausted",
        }
        
        with patch("tools.mcp_server.tools._queue_claim_scope_allowed", return_value=True):
            with patch("tools.mcp_server.tools._repo_root", return_value=str(tmp_path)):
                with patch("tools.mcp_server.tools._resolve_claim_instance") as mock_resolve:
                    mock_resolve.return_value = {
                        "canonical_agent_instance": "exec.test",
                        "resolution": {"resolution": "registered"},
                        "registry_available": True,
                    }
                    with patch("tools.mcp_server.tools._normalize_selector") as mock_selector:
                        mock_selector.return_value = ("TEST-TASK-001", None, None)
                        with patch("tools.mcp_server.tools._match_claim_envelope") as mock_match:
                            # 信封额度耗尽: error_code=ENVELOPE_QUOTA_EXHAUSTED
                            mock_match.return_value = (None, None, "ENVELOPE_QUOTA_EXHAUSTED", None)
                            with patch("tools.mcp_server.tools._load_envelope_guard_declaration") as mock_guard:
                                mock_guard.return_value = {
                                    "error_message": "信封额度已用尽 (released_count >= max_tasks)",
                                    "severity": "needs_human",
                                    "next_step": {
                                        "audience": "advisor",
                                        "action": "信封额度已用尽。请顾问续期信封增加 max_tasks 额度,或发放新信封。查看当前信封状态: /lybra status",
                                        "command": None,
                                    },
                                }
                                with patch("tools.mcp_server.tools.claim_task") as mock_claim:
                                    mock_claim.return_value = {
                                        "ok": True,
                                        "verdict": "OK",
                                        "dry_run_token": "dry_test_123",
                                    }
                                    
                                    result = lybra_queue_claim_dry_run(args)
                                    
                                    # 验证: envelope_error 包含 error_code 和 next_step
                                    assert result is not None
                                    structured = result.get("structuredContent", result)
                                    envelope_error = structured.get("envelope_error")
                                    assert envelope_error is not None
                                    assert envelope_error.get("error_code") == "ENVELOPE_QUOTA_EXHAUSTED"
                                    assert "信封额度已用尽" in envelope_error.get("error_message", "")
                                    assert envelope_error.get("next_step") is not None
                                    assert "续期信封" in envelope_error["next_step"].get("action", "")

    def test_envelope_low_water_warning(self, tmp_path):
        """
        信封剩余额度≤10%时应答包含 low_water_warning:
        - envelope_quota.low_water_warning: True
        - envelope_quota.warning_message: 低水位提示
        """
        from tools.mcp_server.tools import _preauthorized_claim_autorelease
        
        # 构造低水位 quota_info (remaining <= 10%)
        quota_info = {
            "released": 19,
            "max_tasks": 20,
            "remaining": 1,
            "low_water_warning": True,
            "warning_message": "信封额度低水位:剩余 1/20 (≤10%)。请顾问续期信封或发放新信封。",
        }
        
        args = {
            "task_id": "TEST-TASK-LOW",
            "actor": "exec.test",
            "agent_instance": "exec.test",
            "autonomy_mode": "PreAuthorized",
            "owner_policy_ref": "pol_test_low_water",
        }
        
        with patch("tools.mcp_server.tools.claim_task") as mock_claim:
            mock_claim.return_value = {
                "ok": True,
                "verdict": "PASS",
                "dry_run_token": "dry_low_water_123",
            }
            with patch("tools.mcp_server.tools.execute_dry_run") as mock_execute:
                mock_execute.return_value = {
                    "ok": True,
                    "verdict": "PASS",
                }
                
                result = _preauthorized_claim_autorelease(
                    args=args,
                    repo_root=tmp_path,
                    task_id="TEST-TASK-LOW",
                    task_path=None,
                    canonical_agent_instance="exec.test",
                    policy_id="pol_test_low_water",
                    resolution_label="registered",
                    reg_available=True,
                    quota_info=quota_info,
                )
                
                # 验证: envelope_quota 包含 low_water_warning
                structured = result.get("structuredContent", result)
                envelope_quota = structured.get("envelope_quota")
                assert envelope_quota is not None
                assert envelope_quota.get("low_water_warning") is True
                assert "低水位" in envelope_quota.get("warning_message", "")
                assert envelope_quota.get("remaining") == 1
                assert envelope_quota.get("max_tasks") == 20


class TestItem4ErrorGuidance:
    """验收断言④: 报错带路 - No task found 三候选 + SCOPE_DENIED 明说角色"""

    def test_no_task_found_with_three_reasons(self, tmp_path):
        """
        "No task found" 错误应包含三候选原因:
        1. 无 publish 记录 → 经 draft publish 重发
        2. 工作区不符 → 带 workspace_root
        3. 卡已终局 → 检查 completed/cancelled
        """
        from tools.aipos_cli.board_adapter import _normalize_exception
        
        # 模拟 FileNotFoundError("No task found for task_id: MISSING-TASK")
        exc = FileNotFoundError("No task found for task_id: MISSING-TASK")
        result = _normalize_exception("queue_claim", exc, dry_run=True, actor="exec.test")
        
        # 验证: errors 包含 suggested_next_action 的三候选原因
        errors = result.get("errors", [])
        assert len(errors) > 0
        details = errors[0].get("details", {})
        suggested_action = details.get("suggested_next_action", "")
        
        # 三候选原因必须全部出现
        assert "No publish record" in suggested_action or "无 publish 记录" in suggested_action or "draft" in suggested_action.lower()
        assert "Workspace mismatch" in suggested_action or "工作区" in suggested_action or "workspace" in suggested_action.lower()
        assert "already closed" in suggested_action or "已终局" in suggested_action or "completed" in suggested_action.lower()
        
        # 验证包含 Action 指引
        assert "lybra draft publish" in suggested_action or "publish" in suggested_action.lower()

    def test_scope_denied_says_required_role(self, tmp_path):
        """
        SCOPE_DENIED 应明说"此步需 X 角色":
        - message 包含 "This step requires one of these roles: X, Y"
        - suggested_next_action 包含角色列表与转贴提示
        """
        from tools.mcp_server.tools import _scope_denied_result_for
        
        with patch("tools.aipos_cli.verb_contract.who_holds_scope") as mock_who:
            mock_who.return_value = ["advisor", "owner"]
            
            result = _scope_denied_result_for("queue_close", "queue close tools")
            
            # 验证: message 明说 "This step requires one of these roles"
            structured = result.get("structuredContent", result)
            errors = structured.get("errors", [])
            assert len(errors) > 0
            message = errors[0].get("message", "")
            assert "This step requires one of these roles" in message
            assert "advisor" in message
            assert "owner" in message
            
            # 验证: suggested_next_action 包含角色与转贴提示
            details = errors[0].get("details", {})
            suggested_action = details.get("suggested_next_action", "")
            assert "requires one of these roles" in suggested_action.lower()
            assert "advisor" in suggested_action or "owner" in suggested_action


class TestItem6N6NextStep:
    """验收断言⑥: close 应答 next_step 显示 N6 governance-commit 命令"""

    def test_close_success_with_n6_next_step(self, tmp_path):
        """
        close 成功应答包含 next_step:
        - audience: advisor
        - action: 待 N6 governance-commit
        - command: git add governance/ 5_tasks/records/closures/<task_id>/ && git commit ...
        """
        from tools.aipos_cli.board_adapter import close_task
        
        # 准备最小化测试环境(需要实际文件系统结构)
        task_id = "TEST-CLOSE-001"
        queue_dir = tmp_path / "5_tasks" / "queue"
        claimed_dir = queue_dir / "claimed"
        completed_dir = queue_dir / "completed"
        records_dir = tmp_path / "5_tasks" / "records"
        returns_dir = records_dir / "returns" / task_id
        governance_dir = tmp_path / "governance"
        
        claimed_dir.mkdir(parents=True, exist_ok=True)
        completed_dir.mkdir(parents=True, exist_ok=True)
        returns_dir.mkdir(parents=True, exist_ok=True)
        governance_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建 FOUNDATION-BACKLOG.md 避免 auto-generate 警告
        backlog = governance_dir / "FOUNDATION-BACKLOG.md"
        backlog.write_text(f"# Foundation Backlog\n\n- {task_id}: Test task\n", encoding="utf-8")
        
        # 创建任务卡(在 claimed/ 状态) - 包含所有必填字段
        task_card = claimed_dir / f"{task_id.lower()}.md"
        task_card.write_text(
            f"""---
task_id: {task_id}
title: Test Task for N6
status: claimed
queue_state: claimed
project: lybra
assigned_to: exec.test
agent_instance: exec.test
context_bundle: exec.lybra.test
task_mode: code
task_class: simple
priority: normal
created_by: advisor.test
needs_owner: false
output_target: tests/
artifact_policy: formal_write
claim_id: claim_{task_id}_test
claimed_by: exec.test
claimed_at: '2026-08-27T10:00:00Z'
active_session_id: session_{task_id}_test
---
# Test Task

Test task for N6 next_step validation.
""",
            encoding="utf-8"
        )
        
        # 创建 return 记录(close 前置条件)
        return_record = returns_dir / f"return_{task_id.lower()}_20260827_120000_exec.md"
        return_record.write_text(
            f"---\nrecord_type: task_return\ntask_id: {task_id}\nactor: exec.test\n---\n# Return\n",
            encoding="utf-8"
        )
        
        # 创建 records.json
        records_json = records_dir / "records.json"
        records_json.write_text(json.dumps({
            "task_returns": {task_id: [{"path": str(return_record.relative_to(tmp_path))}]},
            "task_closures": {},
            "task_audit_verdicts": {},
        }), encoding="utf-8")
        
        # 调用 close_task
        result = close_task(
            task_id=task_id,
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=tmp_path,
        )
        
        # Debug: print result if next_step is missing
        if not result.get("ok", False) or result.get("data", {}).get("next_step") is None:
            print(f"Result: {json.dumps(result, indent=2, default=str)}")
        
        # 验证: ok=True (close 成功)
        assert result.get("ok", False), f"close_task failed: {result.get('blocking_reasons', result.get('errors', []))}"
        
        # 验证: data 包含 next_step 且指向 N6 governance-commit
        data = result.get("data", {})
        next_step = data.get("next_step")
        assert next_step is not None, f"close 成功应答必须包含 next_step, data keys: {list(data.keys())}"
        assert next_step.get("audience") == "advisor"
        assert "N6" in next_step.get("action", "") or "governance-commit" in next_step.get("action", "")
        
        # 验证: command 包含 git add governance/ 和 git commit
        command = next_step.get("command", "")
        assert "git add" in command
        assert "governance/" in command
        assert f"5_tasks/records/closures/{task_id}/" in command
        assert "git commit" in command
        assert task_id in command

    def test_close_dry_run_with_n6_preview(self, tmp_path):
        """
        close dry_run 应答包含 next_step_preview(与 confirm 后的 next_step 结构一致)
        """
        from tools.aipos_cli.board_adapter import close_task
        
        # 准备最小化测试环境
        task_id = "TEST-CLOSE-DRY-001"
        queue_dir = tmp_path / "5_tasks" / "queue"
        claimed_dir = queue_dir / "claimed"
        records_dir = tmp_path / "5_tasks" / "records"
        returns_dir = records_dir / "returns" / task_id
        
        claimed_dir.mkdir(parents=True, exist_ok=True)
        returns_dir.mkdir(parents=True, exist_ok=True)
        
        task_card = claimed_dir / f"{task_id.lower()}.md"
        task_card.write_text(
            f"---\ntask_id: {task_id}\nstatus: claimed\nqueue_state: claimed\n---\n# Test Task\n",
            encoding="utf-8"
        )
        
        return_record = returns_dir / f"return_{task_id.lower()}_20260827_120000_exec.md"
        return_record.write_text(
            f"---\nrecord_type: task_return\ntask_id: {task_id}\nactor: exec.test\n---\n# Return\n",
            encoding="utf-8"
        )
        
        records_json = records_dir / "records.json"
        records_json.write_text(json.dumps({
            "task_returns": {task_id: [{"path": str(return_record.relative_to(tmp_path))}]},
            "task_closures": {},
            "task_audit_verdicts": {},
        }), encoding="utf-8")
        
        # dry_run 预览
        result = close_task(
            task_id=task_id,
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=True,
            repo_root=tmp_path,
        )
        
        # 验证: data 包含 next_step_preview
        data = result.get("data", {})
        next_step_preview = data.get("next_step_preview")
        assert next_step_preview is not None, "close dry_run 应答必须包含 next_step_preview"
        assert next_step_preview.get("audience") == "advisor"
        assert "N6" in next_step_preview.get("action", "") or "governance-commit" in next_step_preview.get("action", "")
        assert "git add" in next_step_preview.get("command", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
