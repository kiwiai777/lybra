"""AIPOS-340F1 S5 测试 — fix↔复审轮次派生。

用 327 家族真实历史回放: F1→F1R FAIL→F2→F2R PASS
"""
import pytest
from tools.turn_advancer.fix_round_tracker import (
    parse_fix_round,
    derive_next_round,
    derive_review_round,
    check_fix_round_limit,
)


def test_parse_fix_round():
    """测试 fix 轮次解析"""
    # 初始轮
    result = parse_fix_round("AIPOS-327")
    assert result["base_id"] == "AIPOS-327"
    assert result["round"] == 0
    assert result["is_review"] is False
    assert result["full_suffix"] == ""
    
    # F1
    result = parse_fix_round("AIPOS-327F1")
    assert result["base_id"] == "AIPOS-327"
    assert result["round"] == 1
    assert result["is_review"] is False
    assert result["full_suffix"] == "F1"
    
    # F1R
    result = parse_fix_round("AIPOS-327F1R")
    assert result["base_id"] == "AIPOS-327"
    assert result["round"] == 1
    assert result["is_review"] is True
    assert result["full_suffix"] == "F1R"
    
    # F2
    result = parse_fix_round("AIPOS-327F2")
    assert result["base_id"] == "AIPOS-327"
    assert result["round"] == 2
    assert result["is_review"] is False


def test_327_family_history():
    """327 家族回放: 初审→F1→F1R FAIL→F2→F2R PASS"""
    
    # 1. AIPOS-327 初审 FAIL → 派生 F1 (fix 卡已出)
    result = derive_next_round("AIPOS-327", "FAIL", fix_card_exists=True)
    assert result["action"] == "derive_fix"
    assert result["next_task_id"] == "AIPOS-327F1"
    
    # 2. AIPOS-327F1 交回 → 派生 F1R
    result = derive_review_round("AIPOS-327F1")
    assert result["action"] == "derive_review"
    assert result["next_task_id"] == "AIPOS-327F1R"
    
    # 3. AIPOS-327F1R 复审 FAIL → 派生 F2 (fix 卡已出)
    result = derive_next_round("AIPOS-327F1R", "FAIL", fix_card_exists=True)
    assert result["action"] == "derive_fix"
    assert result["next_task_id"] == "AIPOS-327F2"
    
    # 4. AIPOS-327F2 交回 → 派生 F2R
    result = derive_review_round("AIPOS-327F2")
    assert result["action"] == "derive_review"
    assert result["next_task_id"] == "AIPOS-327F2R"
    
    # 5. AIPOS-327F2R 复审 PASS → 完成
    result = derive_next_round("AIPOS-327F2R", "PASS")
    assert result["action"] == "pass_complete"
    assert result["next_task_id"] is None


def test_wait_fix_card():
    """fix 卡未出时,停在等待"""
    result = derive_next_round("AIPOS-327", "FAIL", fix_card_exists=False)
    assert result["action"] == "wait_fix_card"
    assert result["next_task_id"] == "AIPOS-327F1"
    assert "等待顾问出" in result["reason"]


def test_fix_round_limit():
    """fix 轮次超限检查"""
    # 在限内
    result = check_fix_round_limit("AIPOS-327F2", max_fix_rounds=3)
    assert result["within_limit"] is True
    assert result["current_round"] == 2
    
    # 超限
    result = check_fix_round_limit("AIPOS-327F4", max_fix_rounds=3)
    assert result["within_limit"] is False
    assert result["current_round"] == 4
    assert "Owner 裁定" in result["reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
