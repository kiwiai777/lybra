#!/usr/bin/env python3
"""AIPOS-274: 验证 verify_bench 对新字段和正文解析的支持"""
import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root))

from tools.aipos_cli.verify_bench import (
    _extract_acceptance_assertions,
    _extract_owner_verify_checklist,
)


def test_extract_checklist():
    """测试从正文解析 Owner 核验单"""
    body = """# 任务卡标题

## 验收断言
- S1 技术断言第一条
- S2 技术断言第二条

## Owner 核验单(人话)
1. 打开板子看 271-274 任一站——第一眼应是"你要验证的是…"人话清单
2. 找一个带预览的站——页面区块应直接嵌在站内
3. 展开"技术细节"——原来的断言和证据还在

## 其他段落
随便什么内容
"""
    
    checklist = _extract_owner_verify_checklist(body)
    assertions = _extract_acceptance_assertions(body)
    
    print("✓ 提取人话清单:")
    for item in checklist:
        print(f"  - {item}")
    
    print("\n✓ 提取验收断言:")
    for item in assertions:
        print(f"  - {item}")
    
    assert len(checklist) == 3, f"应提取 3 条人话清单，实际 {len(checklist)}"
    assert len(assertions) == 2, f"应提取 2 条断言，实际 {len(assertions)}"
    assert "打开板子看" in checklist[0]
    assert "S1 技术断言第一条" == assertions[0]
    
    print("\n✓ 测试通过：正文解析正常工作")


def test_checklist_variants():
    """测试核验单标题变体"""
    variants = [
        "## Owner 核验单",
        "## Owner核验单",
        "## 核验单",
        "## Owner 核验单(人话)",
    ]
    
    for heading in variants:
        body = f"""{heading}
- 测试项目一
- 测试项目二
"""
        checklist = _extract_owner_verify_checklist(body)
        assert len(checklist) == 2, f"标题 '{heading}' 应提取 2 项，实际 {len(checklist)}"
    
    print("✓ 测试通过：标题变体识别正常")


def test_no_checklist_fallback():
    """测试无核验单时的兼容性"""
    body = """# 任务卡

## 验收断言
- S1 只有断言没有核验单
- S2 应该优雅回退
"""
    
    checklist = _extract_owner_verify_checklist(body)
    assertions = _extract_acceptance_assertions(body)
    
    assert len(checklist) == 0, "无核验单应返回空列表"
    assert len(assertions) == 2, "断言应正常提取"
    
    print("✓ 测试通过：无核验单时优雅回退")


if __name__ == "__main__":
    test_extract_checklist()
    test_checklist_variants()
    test_no_checklist_fallback()
    print("\n✅ 所有测试通过")
