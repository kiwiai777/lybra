"""
AIPOS-F63: fail-closed 普查与改造测试

验证所有"跳过检查"的分支已改为"缺失即拒"。
"""
import pytest
from pathlib import Path
from tools.aipos_cli.validation_common import (
    check_placeholder_in_text,
    check_required_field,
    check_file_ref_exists,
    check_evidence_refs_non_empty,
    PLACEHOLDER_PATTERNS,
)


class TestPlaceholderDetection:
    """占位符检测测试：作用于所有文本字段"""
    
    def test_placeholder_patterns_complete(self):
        """验证占位符字典包含所有已知占位符"""
        expected = ["(待填写)", "(PASS / FAIL", "(无验收清单)", "TODO", "FIXME"]
        for pattern in expected:
            assert pattern in PLACEHOLDER_PATTERNS, f"Missing placeholder: {pattern}"
    
    def test_detect_placeholder_in_text(self):
        """验证能检测文本中的占位符"""
        text = "这是一个(待填写)的文本"
        reasons = check_placeholder_in_text(text, "test_field")
        assert len(reasons) == 1
        assert "PLACEHOLDER_DETECTED" in reasons[0]
        assert "(待填写)" in reasons[0]
    
    def test_detect_multiple_placeholders(self):
        """验证能检测多个占位符"""
        text = "TODO: 实现这个功能 FIXME: 修复 bug"
        reasons = check_placeholder_in_text(text, "test_field")
        assert len(reasons) == 1
        assert "TODO" in reasons[0]
        assert "FIXME" in reasons[0]
    
    def test_no_placeholder_passes(self):
        """验证无占位符时通过"""
        text = "这是正常的文本内容"
        reasons = check_placeholder_in_text(text, "test_field")
        assert len(reasons) == 0


class TestRequiredFieldValidation:
    """必填字段验证测试：缺失即拒"""
    
    def test_none_value_rejected(self):
        """验证 None 值被拒绝"""
        reasons = check_required_field(None, "test_field")
        assert len(reasons) == 1
        assert "REQUIRED_FIELD_MISSING" in reasons[0]
        assert "test_field" in reasons[0]
    
    def test_empty_string_rejected(self):
        """验证空字符串被拒绝"""
        reasons = check_required_field("", "test_field")
        assert len(reasons) == 1
        assert "REQUIRED_FIELD_EMPTY" in reasons[0]
    
    def test_whitespace_only_rejected(self):
        """验证纯空白字符串被拒绝"""
        reasons = check_required_field("   ", "test_field")
        assert len(reasons) == 1
        assert "REQUIRED_FIELD_EMPTY" in reasons[0]
    
    def test_empty_list_rejected(self):
        """验证空列表被拒绝"""
        reasons = check_required_field([], "test_field")
        assert len(reasons) == 1
        assert "REQUIRED_FIELD_EMPTY" in reasons[0]
    
    def test_placeholder_in_string_rejected(self):
        """验证包含占位符的字符串被拒绝"""
        reasons = check_required_field("(待填写)", "test_field", check_placeholders=True)
        assert len(reasons) == 1
        assert "PLACEHOLDER_DETECTED" in reasons[0]
    
    def test_valid_value_passes(self):
        """验证有效值通过"""
        reasons = check_required_field("valid value", "test_field")
        assert len(reasons) == 0


class TestFileRefValidation:
    """文件引用验证测试：不存在即拒"""
    
    def test_empty_ref_rejected_when_required(self):
        """验证必填时空引用被拒绝"""
        reasons = check_file_ref_exists("", "test_file", Path("/tmp"), required=True)
        assert len(reasons) == 1
        assert "FILE_REF_MISSING" in reasons[0]
    
    def test_empty_ref_passes_when_optional(self):
        """验证非必填时空引用通过"""
        reasons = check_file_ref_exists("", "test_file", Path("/tmp"), required=False)
        assert len(reasons) == 0
    
    def test_nonexistent_file_rejected(self):
        """验证不存在的文件被拒绝"""
        reasons = check_file_ref_exists(
            "nonexistent/file.txt", 
            "test_file", 
            Path("/tmp"),
            required=True
        )
        assert len(reasons) == 1
        assert "FILE_NOT_FOUND" in reasons[0]


class TestEvidenceValidation:
    """审计证据验证测试：PASS必须带非空证据"""
    
    def test_empty_evidence_refs_and_findings_rejected(self):
        """验证证据和发现都为空时被拒绝"""
        reasons = check_evidence_refs_non_empty([], None)
        assert len(reasons) == 1
        assert "EMPTY_EVIDENCE" in reasons[0]
        assert "PASS" in reasons[0]
    
    def test_empty_evidence_refs_and_empty_findings_rejected(self):
        """验证证据为空列表、发现为空字符串时被拒绝"""
        reasons = check_evidence_refs_non_empty([], "")
        assert len(reasons) == 1
        assert "EMPTY_EVIDENCE" in reasons[0]
    
    def test_empty_evidence_refs_and_whitespace_findings_rejected(self):
        """验证证据为空、发现为纯空白时被拒绝"""
        reasons = check_evidence_refs_non_empty([], "   ")
        assert len(reasons) == 1
        assert "EMPTY_EVIDENCE" in reasons[0]
    
    def test_non_empty_evidence_refs_passes(self):
        """验证非空证据引用通过"""
        reasons = check_evidence_refs_non_empty(["evidence1.md"], None)
        assert len(reasons) == 0
    
    def test_non_empty_findings_passes(self):
        """验证非空发现总结通过"""
        reasons = check_evidence_refs_non_empty([], "审计发现：代码质量良好")
        assert len(reasons) == 0
    
    def test_both_non_empty_passes(self):
        """验证两者都非空时通过"""
        reasons = check_evidence_refs_non_empty(["evidence1.md"], "审计发现：代码质量良好")
        assert len(reasons) == 0


class TestReturnNotSkeleton:
    """交回骨架检测测试：复现 F62 场景"""
    
    def test_f62_scenario_should_be_rejected(self, tmp_path):
        """
        复现 AIPOS-F62: result_summary="(待填写)" + completion_report_ref=""
        修复前通过，修复后应被拒绝
        """
        from tools.aipos_cli.board_adapter import _check_return_not_skeleton
        
        # F62 的实际输入
        reasons = _check_return_not_skeleton(
            task_id="AIPOS-F62",
            result_summary="(待填写)",  # 非空但是占位符
            completion_report_ref="",  # 空字符串
            artifact_refs=[],  # 空列表
            repo_root=tmp_path,
        )
        
        # 修复后应该有拒绝理由
        assert len(reasons) >= 2, f"Expected at least 2 blocking reasons, got {len(reasons)}: {reasons}"
        
        # 应该检测到 result_summary 中的占位符
        placeholder_detected = any("PLACEHOLDER_DETECTED" in r or "待填写" in r for r in reasons)
        assert placeholder_detected, f"Should detect placeholder in result_summary. Got: {reasons}"
        
        # 应该检测到 completion_report_ref 为空
        ref_missing = any("REQUIRED_FIELD" in r and "completion_report_ref" in r for r in reasons)
        assert ref_missing, f"Should detect missing completion_report_ref. Got: {reasons}"
        
        # 应该检测到 artifact_refs 为空
        artifact_missing = any("artifact_refs" in r for r in reasons)
        assert artifact_missing, f"Should detect empty artifact_refs. Got: {reasons}"
    
    def test_valid_return_passes(self, tmp_path):
        """验证有效的交回通过检查"""
        from tools.aipos_cli.board_adapter import _check_return_not_skeleton
        
        # 创建一个有效的 RETURN.md
        return_file = tmp_path / "task_cards" / "TEST-123" / "RETURN.md"
        return_file.parent.mkdir(parents=True, exist_ok=True)
        return_file.write_text("# 一句话结论\n\n完成了功能实现。\n\n## 改动清单\n\n- 修改了文件A\n- 新增了文件B")
        
        reasons = _check_return_not_skeleton(
            task_id="TEST-123",
            result_summary="完成了功能实现",  # 非空且无占位符
            completion_report_ref="task_cards/TEST-123/RETURN.md",  # 非空且文件存在
            artifact_refs=["task_cards/TEST-123/RETURN.md"],  # 非空列表
            repo_root=tmp_path,
        )
        
        assert len(reasons) == 0, f"Valid return should pass, but got: {reasons}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
