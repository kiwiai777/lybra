#!/usr/bin/env python3
"""AIPOS-R6E 靶⑤:N0容量lint测试——启发式WARN交付大项>3或验证+修复混装"""
import pytest
from tools.aipos_cli.task_complexity import validate_task_complexity


def test_n0_lint_warns_on_too_many_major_items():
    """超过3个大项应该WARN"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "功能A、功能B、功能C、功能D",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    assert len(result["blocking_reasons"]) == 0, "Should only warn, not block"
    assert any("N0 capacity lint" in w and "4 major items" in w for w in result["warnings"]), \
        f"Should warn about 4 major items, got: {result['warnings']}"


def test_n0_lint_no_warn_on_three_items():
    """3个或更少大项不应该WARN"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "功能A、功能B、功能C",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    n0_warnings = [w for w in result["warnings"] if "N0 capacity lint" in w and "major items" in w]
    assert len(n0_warnings) == 0, f"Should not warn on <=3 items, got: {n0_warnings}"


def test_n0_lint_warns_on_mixed_verify_fix():
    """验证+修复混装应该WARN"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "验证功能A + 修复bug B",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    assert any("N0 capacity lint" in w and "verification+fix" in w for w in result["warnings"]), \
        f"Should warn about mixed concerns, got: {result['warnings']}"


def test_n0_lint_warns_on_mixed_verify_fix_cleanup():
    """验证+修复+清账三混应该WARN"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "验证部署、修复配置、清账遗留issue",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    assert any("N0 capacity lint" in w and "mixes" in w for w in result["warnings"]), \
        f"Should warn about mixed concerns, got: {result['warnings']}"


def test_n0_lint_no_warn_on_pure_fix():
    """纯修复卡不应该WARN"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "修复bug A和bug B",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    mixed_warnings = [w for w in result["warnings"] if "N0 capacity lint" in w and "mixes" in w]
    assert len(mixed_warnings) == 0, f"Should not warn on pure fix, got: {mixed_warnings}"


def test_n0_lint_english_keywords():
    """英文关键词也应该识别"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "verify deployment + fix config + cleanup legacy",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    assert any("N0 capacity lint" in w and "mixes" in w for w in result["warnings"]), \
        f"Should detect English keywords, got: {result['warnings']}"


def test_n0_lint_only_warns_never_blocks():
    """N0 lint只警不拦"""
    metadata = {
        "task_class": "simple",
        "artifact_scope": "功能A、功能B、功能C、功能D、功能E + 验证 + 修复 + 清账",
    }
    result = validate_task_complexity(metadata, enforce_dependency_gate=False)
    
    assert len(result["blocking_reasons"]) == 0, "N0 lint must never block"
    assert len(result["warnings"]) > 0, "Should produce warnings"
    assert any("N0 capacity lint" in w for w in result["warnings"]), \
        "Warnings should be labeled as N0 capacity lint"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
