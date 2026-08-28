#!/usr/bin/env python3
"""
AIPOS-F49 判据④ 夹具: RETURN.md 不得含占位符，result_summary 非空。

测试策略: 使用 tmp_path 临时工作区，模拟 RETURN.md + result_summary
"""
from pathlib import Path
import tempfile


def test_criterion_4_return_not_skeleton(tmp_path: Path):
    """判据④: RETURN.md 不得含占位符，result_summary 非空。"""
    
    from tools.aipos_cli.board_adapter import _check_return_not_skeleton
    
    # 红测试 1: result_summary 为空
    reasons = _check_return_not_skeleton(
        task_id="TEST-004",
        result_summary="",
        completion_report_ref=None,
        repo_root=tmp_path,
    )
    
    assert len(reasons) > 0, "应该检测到 result_summary 为空"
    assert "RETURN_SKELETON" in reasons[0], f"应该返回 RETURN_SKELETON，实际: {reasons[0]}"
    assert "result_summary" in reasons[0], f"应该提到 result_summary，实际: {reasons[0]}"
    print("✓ 红测试 1 通过: 检测到 result_summary 为空")
    
    # 红测试 2: RETURN.md 含占位符
    report_dir = tmp_path / "task_cards" / "TEST-004"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "RETURN.md"
    report_path.write_text("""# TEST-004 执行归还

## 一句话结论

(待填写)

## 做了什么

实现了功能 A。

## 验收结果

(PASS / FAIL / BLOCK)
""", encoding="utf-8")
    
    reasons = _check_return_not_skeleton(
        task_id="TEST-004",
        result_summary="完成功能 A",
        completion_report_ref="task_cards/TEST-004/RETURN.md",
        repo_root=tmp_path,
    )
    
    assert len(reasons) > 0, "应该检测到 RETURN.md 含占位符"
    assert "RETURN_SKELETON" in reasons[0], f"应该返回 RETURN_SKELETON，实际: {reasons[0]}"
    assert "(待填写)" in reasons[0] or "(PASS / FAIL" in reasons[0], f"应该提到占位符，实际: {reasons[0]}"
    print("✓ 红测试 2 通过: 检测到 RETURN.md 含占位符")
    
    # 绿测试: result_summary 非空且 RETURN.md 无占位符
    report_path.write_text("""# TEST-004 执行归还

## 一句话结论

完成功能 A，所有测试通过。

## 做了什么

实现了功能 A，添加了单元测试。

## 验收结果

通过。
""", encoding="utf-8")
    
    reasons = _check_return_not_skeleton(
        task_id="TEST-004",
        result_summary="完成功能 A",
        completion_report_ref="task_cards/TEST-004/RETURN.md",
        repo_root=tmp_path,
    )
    
    assert len(reasons) == 0, f"result_summary 非空且无占位符不应该有阻塞，实际: {reasons}"
    print("✓ 绿测试通过: result_summary 非空且 RETURN.md 无占位符")
    
    print("✓ AIPOS-F49 判据④ 测试通过")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_criterion_4_return_not_skeleton(Path(tmpdir))
