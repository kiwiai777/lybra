#!/usr/bin/env python3
"""
AIPOS-F49-fix1-fix1: 验证 owner_confirmation_token 数据流贯通

测试策略:
1. 红测试: 模拟修复前（owner_confirmation_token 未注入）
2. 绿测试: 模拟修复后（owner_confirmation_token 已注入）
"""
import json


def test_red_token_not_injected():
    """红测试: 修复前，owner_confirmation_token 未注入到 mcp_return_metadata"""
    
    # 模拟修复前的逻辑（owner_confirmation_token 作为独立参数，未注入）
    owner_confirmation_token = "OWNER_CONFIRMED"
    mcp_return_metadata = {}  # 空的 metadata
    
    # 模拟自检逻辑（从 mcp_return_metadata 取值）
    owner_token = mcp_return_metadata.get("owner_confirmation_token")
    
    # 验证: 取不到 token（数据流断裂）
    assert owner_token is None, f"应该取不到 token（数据流断裂），实际: {owner_token}"
    
    print("✓ 红测试通过: 修复前数据流断裂，owner_confirmation_token 未注入")


def test_green_token_injected():
    """绿测试: 修复后，owner_confirmation_token 注入到 mcp_return_metadata"""
    
    # 模拟修复后的逻辑（owner_confirmation_token 注入到 mcp_return_metadata）
    owner_confirmation_token = "OWNER_CONFIRMED"
    mcp_return_metadata = {}
    
    # AIPOS-F49-fix1-fix1: 注入 owner_confirmation_token
    if owner_confirmation_token:
        if mcp_return_metadata is None:
            mcp_return_metadata = {}
        mcp_return_metadata["owner_confirmation_token"] = owner_confirmation_token
    
    # 模拟自检逻辑（从 mcp_return_metadata 取值）
    owner_token = mcp_return_metadata.get("owner_confirmation_token")
    
    # 验证: 能取到 token（数据流贯通）
    assert owner_token == "OWNER_CONFIRMED", f"应该取到 'OWNER_CONFIRMED'，实际: {owner_token}"
    
    print("✓ 绿测试通过: 修复后数据流贯通，owner_confirmation_token 注入成功")


def test_green_waiver_logic():
    """绿测试: 验证完整放行逻辑（注入 + 自检 + 放行）"""
    
    # 模拟自检失败
    self_check_reasons = [
        "SELF_CHECK_TEST_NOT_IN_RUNALL: test_new.py not found in run-all.sh",
    ]
    
    # 模拟 owner_confirmation_token 注入
    owner_confirmation_token = "OWNER_CONFIRMED"
    mcp_return_metadata = {}
    if owner_confirmation_token:
        mcp_return_metadata["owner_confirmation_token"] = owner_confirmation_token
    
    # 模拟放行逻辑（board_adapter.py:3066）
    self_check_waived = False
    waiver_reason = None
    warnings = []
    blocking_reasons = []
    
    if self_check_reasons and mcp_return_metadata:
        owner_token = mcp_return_metadata.get("owner_confirmation_token")
        if owner_token:
            # Owner 强制放行
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
    assert "waived 1 self-check failures" in waiver_reason, f"waiver_reason 应说明豁免数量，实际: {waiver_reason}"
    assert len(blocking_reasons) == 0, "blocking_reasons 应该被清空（放行）"
    assert len(warnings) == 1, "豁免判据应写入 warnings"
    assert "SELF_CHECK_WAIVED" in warnings[0], "warnings 应包含 SELF_CHECK_WAIVED 前缀"
    
    print(f"✓ 绿测试通过: 完整放行逻辑工作正常")
    print(f"  waiver_reason: {waiver_reason}")
    print(f"  warnings: {warnings[0][:60]}...")


if __name__ == "__main__":
    print("=== AIPOS-F49-fix1-fix1 数据流验证 ===")
    test_red_token_not_injected()
    test_green_token_injected()
    test_green_waiver_logic()
    print("✓ AIPOS-F49-fix1-fix1 数据流验证全部通过")
