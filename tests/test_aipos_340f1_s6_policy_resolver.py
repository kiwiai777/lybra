"""AIPOS-340F1 S6 测试 — 配置表读取(策略信封解析)。
"""
import pytest
from pathlib import Path
from tools.aipos_cli.policy_resolver import find_active_policy


def test_find_active_policy_no_dir(tmp_path):
    """策略目录不存在时返回 None"""
    result = find_active_policy(tmp_path, role="exec", policy_type="dev")
    assert result is None


def test_find_active_policy_integration():
    """集成测试:读取真实治理仓策略"""
    governance_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    if not governance_root.exists():
        pytest.skip("治理仓不存在,跳过集成测试")
    
    policies_dir = governance_root / "5_tasks" / "policies"
    if not policies_dir.exists():
        pytest.skip("策略目录不存在,跳过集成测试")
    
    # 读取 dev 策略
    result = find_active_policy(governance_root, role="exec", policy_type="dev")
    # 应该返回最新的活跃策略(如 pol_lybra_dev_7)
    if result:
        assert result.startswith("pol_lybra_dev_")
        print(f"找到活跃 dev 策略: {result}")
    
    # 读取 audit 策略
    result = find_active_policy(governance_root, role="audit", policy_type="audit")
    if result:
        assert result.startswith("pol_lybra_audit_")
        print(f"找到活跃 audit 策略: {result}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
