"""AIPOS-340F1 S4 测试 — 连败计数器与有界重试。
"""
import pytest
from pathlib import Path
from tools.turn_advancer.failure_tracker import (
    count_consecutive_failures,
    check_retry_limit,
)


def test_count_consecutive_failures_no_records(tmp_path):
    """无记录时返回 0"""
    result = count_consecutive_failures(tmp_path, "AIPOS-TEST")
    assert result["consecutive_failures"] == 0
    assert result["total_attempts"] == 0
    assert result["failure_history"] == []


def test_check_retry_limit_allow(tmp_path):
    """未超限时允许重试"""
    result = check_retry_limit(tmp_path, "AIPOS-TEST", max_consecutive_failures=3)
    assert result["action"] == "allow_retry"
    assert "允许继续重试" in result["reason"]


def test_retry_limit_integration():
    """集成测试:读取真实治理仓数据(如果存在)"""
    # 这是集成测试,依赖真实数据
    governance_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    if not governance_root.exists():
        pytest.skip("治理仓不存在,跳过集成测试")
    
    # 测试任意存在的任务
    task_id = "AIPOS-327"
    records_claims = governance_root / "5_tasks" / "records" / "claims" / task_id
    if records_claims.exists():
        result = count_consecutive_failures(governance_root, task_id)
        # 基本检查:返回结构正确
        assert "consecutive_failures" in result
        assert "total_attempts" in result
        assert isinstance(result["consecutive_failures"], int)
        assert isinstance(result["total_attempts"], int)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
