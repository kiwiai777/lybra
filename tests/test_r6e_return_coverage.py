#!/usr/bin/env python3
"""AIPOS-R6E 靶④:return覆盖度对照测试——卡面artifact_scope vs 实际artifact_refs差异结构化

注:覆盖度检查为启发式WARN,不是BLOCK。目标是提醒executor可能有遄10,而非精确语义匹配。
"""
import pytest
from tools.aipos_cli.board_adapter import _check_return_coverage


def test_coverage_full_match_same_language():
    """完全覆盖:同语言关键词匹配"""
    result = _check_return_coverage(
        declared_scope="deploy script, contract test, documentation",
        actual_refs=["tools/lybra-deploy", "tests/test_r6e_write_contract.py", "docs/README.md"],
        result_summary="Completed deployment fix, contract tests, and documentation",
    )
    
    assert result["has_missing_items"] is False
    assert len(result["declared_items"]) == 3
    assert len(result["covered_items"]) == 3
    assert len(result["missing_items"]) == 0


def test_coverage_partial_match():
    """部分覆盖:有声明项未在实际交付中"""
    result = _check_return_coverage(
        declared_scope="authentication, authorization, logging",
        actual_refs=["src/auth.py"],
        result_summary="Fixed authentication",
    )
    
    assert result["has_missing_items"] is True
    assert len(result["declared_items"]) == 3
    assert len(result["covered_items"]) >= 1  # 至少authentication匹配
    assert len(result["missing_items"]) >= 1  # authorization和logging缺失


def test_coverage_keyword_in_path():
    """关键词在文件路径中匹配"""
    result = _check_return_coverage(
        declared_scope="login, permission",
        actual_refs=["auth/login.py", "auth/permissions.py"],
        result_summary="Fixed login issue and implemented permission checks",
    )
    
    assert result["has_missing_items"] is False
    assert len(result["covered_items"]) == 2


def test_coverage_no_scope_declared():
    """无artifact_scope声明:不做覆盖度检查"""
    result = _check_return_coverage(
        declared_scope="",
        actual_refs=["some/file.py"],
        result_summary="Completed work",
    )
    
    assert result["has_missing_items"] is False
    assert result["coverage_summary"] == "No artifact_scope declared"


def test_coverage_case_insensitive():
    """大小写不敏感匹配"""
    result = _check_return_coverage(
        declared_scope="Authentication",
        actual_refs=["auth/authentication.py"],
        result_summary="fixed authentication issue",
    )
    
    assert result["has_missing_items"] is False


def test_coverage_summary_format():
    """覆盖度摘要格式"""
    result = _check_return_coverage(
        declared_scope="featureA, featureB, featureC, featureD",
        actual_refs=["feature_a.py", "feature_b.py"],
        result_summary="Completed featureA and featureB",
    )
    
    # featureA和featureB应该匹配
    assert result["has_missing_items"] is True
    assert len(result["covered_items"]) == 2
    assert len(result["missing_items"]) == 2
    assert "2/4" in result["coverage_summary"]
    assert "50%" in result["coverage_summary"]


def test_coverage_cross_language_limitation():
    """跨语言限制:中文scope vs 英文refs = 启发式失效(预期行为)
    
    这是已知限制,不强求跨语言语义匹配。Executor应使用一致语言书写artifact_scope。
    """
    result = _check_return_coverage(
        declared_scope="修复部署脚本、添加测试",
        actual_refs=["tools/deploy.sh", "tests/test_new.py"],
        result_summary="Fixed deployment script and added tests",
    )
    
    # 跨语言匹配失败是预期的
    # 这个测试文档化限制,不强求通过
    assert result["declared_items"] == ["修复部署脚本", "添加测试"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
