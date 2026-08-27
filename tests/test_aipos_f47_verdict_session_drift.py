"""AIPOS-F47: 裁决提交会话绑定放宽到工位双锁(F34 同款)

核心改动验证:
- AUDIT_SESSION_MISMATCH: blocking → warning (AUDIT_SESSION_DRIFT)
- MISSING_AUDIT_SESSION_RECORD: blocking → warning (AUDIT_SESSION_RECORD_ABSENT/MISSING)

测试策略: 直接测试 _build_audit_verdict_preview 函数(内部逻辑单元测试)
"""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestAuditVerdictSessionRelaxation:
    """验证会话绑定从 BLOCK 降级为 WARN 的核心逻辑"""

    def test_session_mismatch_becomes_warning(self, tmp_path):
        """
        会话不匹配从 blocking → warning:
        - 审计卡 active_session_id = old_session
        - 提交时 audit_session_id = new_session
        - 修复前: AUDIT_SESSION_MISMATCH → blocking_reasons
        - 修复后: AUDIT_SESSION_DRIFT → warnings (不阻塞)
        """
        from tools.aipos_cli.board_adapter import _build_audit_verdict_preview
        
        # Mock 审计卡元数据(旧会话)
        audit_metadata = {
            "task_id": "TEST-AUDIT",
            "reviewed_task_id": "TEST-TASK",
            "claim_id": "claim_test",
            "active_session_id": "old_session_12345",  # 旧会话
            "agent_instance": "audit.test",
        }
        
        # Mock 审计卡对象
        audit_task = {
            "task_id": "TEST-AUDIT",
            "reviewed_task_id": "TEST-TASK",
            "reviewed_executor_instance": "exec.test",
        }
        
        # Mock records (简化，只包含必需的return记录)
        records = {
            "return_index": {
                "5_tasks/records/returns/TEST-TASK/return_test.md": {
                    "task_id": "TEST-TASK",
                    "actor": "exec.test",
                    "agent_instance": "exec.test",
                }
            },
        }
        
        # 调用 _build_audit_verdict_preview (新会话)
        result = _build_audit_verdict_preview(
            audit_task_id="TEST-AUDIT",
            audit_task_path=None,
            reviewed_task_id="TEST-TASK",
            actor="audit.test",
            agent_instance="audit.test",
            owner_policy_ref="pol_test",
            audit_claim_id="claim_test",
            audit_session_id="new_session_67890",  # 新会话 (与审计卡不匹配)
            audit_dispatch_record_ref=None,
            reviewed_return_record_ref="5_tasks/records/returns/TEST-TASK/return_test.md",
            verdict_value="PASS",
            findings_summary="Test findings",
            evidence_refs=["5_tasks/records/returns/TEST-TASK/return_test.md"],
            recommended_next_action=None,
            owner_waiver_ref=None,
            repo_root=tmp_path,
            dry_run=True,
        )
        
        # 验证: warnings 包含 AUDIT_SESSION_DRIFT
        warnings = result.get("warnings", [])
        session_drift_found = any("AUDIT_SESSION_DRIFT" in w or "session" in w.lower() and "drift" in w.lower() for w in warnings)
        assert session_drift_found, f"Expected AUDIT_SESSION_DRIFT in warnings, got: {warnings}"
        
        # 验证: blocking_reasons 不包含 AUDIT_SESSION_MISMATCH
        blocking_reasons = result.get("blocking_reasons", [])
        session_blocking_found = any("AUDIT_SESSION_MISMATCH" in b for b in blocking_reasons)
        assert not session_blocking_found, f"AUDIT_SESSION_MISMATCH should not block, got blocking_reasons: {blocking_reasons}"

    def test_missing_session_becomes_warning(self, tmp_path):
        """
        会话记录缺失从 blocking → warning:
        - 审计卡无 active_session_id
        - 提交时无 audit_session_id
        - 修复前: MISSING_AUDIT_SESSION_RECORD → blocking_reasons
        - 修复后: AUDIT_SESSION_RECORD_ABSENT → warnings (不阻塞)
        """
        from tools.aipos_cli.board_adapter import _build_audit_verdict_preview
        
        # Mock 审计卡元数据(无会话)
        audit_metadata = {
            "task_id": "TEST-AUDIT",
            "reviewed_task_id": "TEST-TASK",
            "claim_id": "claim_test",
            # 无 active_session_id
            "agent_instance": "audit.test",
        }
        
        audit_task = {
            "task_id": "TEST-AUDIT",
            "reviewed_task_id": "TEST-TASK",
            "reviewed_executor_instance": "exec.test",
        }
        
        records = {
            "return_index": {
                "5_tasks/records/returns/TEST-TASK/return_test.md": {
                    "task_id": "TEST-TASK",
                    "actor": "exec.test",
                    "agent_instance": "exec.test",
                }
            },
        }
        
        # 调用 _build_audit_verdict_preview (无会话)
        result = _build_audit_verdict_preview(
            audit_task_id="TEST-AUDIT",
            audit_task_path=None,
            reviewed_task_id="TEST-TASK",
            actor="audit.test",
            agent_instance="audit.test",
            owner_policy_ref="pol_test",
            audit_claim_id="claim_test",
            audit_session_id=None,  # 无会话
            audit_dispatch_record_ref=None,
            reviewed_return_record_ref="5_tasks/records/returns/TEST-TASK/return_test.md",
            verdict_value="PASS",
            findings_summary="Test findings",
            evidence_refs=["5_tasks/records/returns/TEST-TASK/return_test.md"],
            recommended_next_action=None,
            owner_waiver_ref=None,
            repo_root=tmp_path,
            dry_run=True,
        )
        
        # 验证: warnings 包含 AUDIT_SESSION_RECORD_ABSENT
        warnings = result.get("warnings", [])
        session_absent_found = any("AUDIT_SESSION_RECORD_ABSENT" in w or ("session" in w.lower() and "absent" in w.lower()) for w in warnings)
        assert session_absent_found, f"Expected AUDIT_SESSION_RECORD_ABSENT in warnings, got: {warnings}"
        
        # 验证: blocking_reasons 不包含 MISSING_AUDIT_SESSION_RECORD
        blocking_reasons = result.get("blocking_reasons", [])
        session_blocking_found = any("MISSING_AUDIT_SESSION_RECORD" in b for b in blocking_reasons)
        assert not session_blocking_found, f"MISSING_AUDIT_SESSION_RECORD should not block, got blocking_reasons: {blocking_reasons}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
