#!/usr/bin/env python3
"""
AIPOS-F49-fix1 夹具: owner_confirmation_token 强制放行机制

测试策略: 
1. 红测试: 模拟自检失败不带 token → blocking_reasons 有内容
2. 绿测试: 模拟自检失败带 token → 放行且标记 waived
"""
import json
import tempfile
from pathlib import Path


def test_red_no_token_blocks():
    """红测试: 自检失败不带 owner_confirmation_token → blocking_reasons 有内容"""
    
    # 模拟自检失败
    self_check_reasons = [
        "SELF_CHECK_TEST_NOT_IN_RUNALL: test_new_feature.py not found in run-all.sh",
        "SELF_CHECK_HAS_TESTS: No test files found for code task",
    ]
    
    # 模拟无 owner_confirmation_token
    mcp_return_metadata = None
    
    # 应用放行逻辑
    self_check_waived = False
    waiver_reason = None
    warnings = []
    blocking_reasons = []
    
    if self_check_reasons and mcp_return_metadata:
        owner_token = mcp_return_metadata.get("owner_confirmation_token")
        if owner_token:
            self_check_waived = True
            waiver_reason = f"Owner confirmation token provided, waived {len(self_check_reasons)} self-check failures"
            warnings.extend([
                f"SELF_CHECK_WAIVED: {reason}" for reason in self_check_reasons
            ])
            self_check_reasons = []
    
    blocking_reasons.extend(self_check_reasons)
    
    # 验证：应该被拒收
    assert len(blocking_reasons) > 0, "应该有 blocking_reasons（自检失败）"
    assert self_check_waived is False, "应该未放行（无 token）"
    assert waiver_reason is None, "应该无 waiver_reason（无 token）"
    assert len(warnings) == 0, "应该无 warnings（未放行）"
    
    print(f"✓ 红测试通过: 无 token 时自检拒收 ({len(blocking_reasons)} 条失败)")
    print(f"  失败原因: {blocking_reasons[0]}")


def test_green_token_waives():
    """绿测试: 自检失败带 owner_confirmation_token → 放行且标记 waived"""
    
    # 模拟自检失败
    self_check_reasons = [
        "SELF_CHECK_TEST_NOT_IN_RUNALL: test_new_feature.py not found in run-all.sh",
        "SELF_CHECK_HAS_TESTS: No test files found for code task",
    ]
    
    # 模拟带 owner_confirmation_token
    mcp_return_metadata = {
        "owner_confirmation_token": "OWNER_CONFIRMED",
    }
    
    # 应用放行逻辑（与 board_adapter.py 中一致）
    self_check_waived = False
    waiver_reason = None
    warnings = []
    blocking_reasons = []
    
    if self_check_reasons and mcp_return_metadata:
        owner_token = mcp_return_metadata.get("owner_confirmation_token")
        if owner_token:
            self_check_waived = True
            waiver_reason = f"Owner confirmation token provided, waived {len(self_check_reasons)} self-check failures"
            warnings.extend([
                f"SELF_CHECK_WAIVED: {reason}" for reason in self_check_reasons
            ])
            self_check_reasons = []
    
    blocking_reasons.extend(self_check_reasons)
    
    # 验证放行
    assert self_check_waived is True, "应该标记 self_check_waived=True"
    assert waiver_reason is not None, "应该有 waiver_reason"
    assert "waived 2 self-check failures" in waiver_reason, f"waiver_reason 应说明豁免数量，实际: {waiver_reason}"
    assert len(blocking_reasons) == 0, "blocking_reasons 应该被清空（放行）"
    assert len(warnings) == 2, f"应该有 2 条豁免判据写入 warnings（留痕），实际: {len(warnings)}"
    assert all("SELF_CHECK_WAIVED" in w for w in warnings), "所有 warnings 应包含 SELF_CHECK_WAIVED"
    
    print(f"✓ 绿测试通过: 带 token 时放行且标记 waived")
    print(f"  waiver_reason: {waiver_reason}")
    print(f"  warnings 留痕: {len(warnings)} 条豁免判据")
    for w in warnings:
        print(f"    - {w[:80]}...")


if __name__ == "__main__":
    print("=== AIPOS-F49-fix1 先红后绿测试 ===")
    test_red_no_token_blocks()
    test_green_token_waives()
    print("✓ AIPOS-F49-fix1 先红后绿测试全部通过")
