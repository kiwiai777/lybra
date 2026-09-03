#!/usr/bin/env python3
"""AIPOS-F65C 件④: 占位符检测区分引用与实际空白的测试。

验收:两实撞报告原文为夹具样本(误拦转不拦), 真骨架照拦零回归。
"""
from tools.aipos_cli.validation_common import check_placeholder_in_text


def test_placeholder_standalone_line_blocked():
    """占位符独占一行:应拦截"""
    text = """
## 测试结果

(待填写)

## 下一步
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) > 0, "独占一行的占位符应被拦截"
    assert "PLACEHOLDER_DETECTED" in reasons[0]


def test_placeholder_in_list_item_blocked():
    """占位符作为列表项独占:应拦截"""
    text = """
## 改动清单

- (待填写)
- 另一个文件
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) > 0, "列表项中独占的占位符应被拦截"


def test_placeholder_in_description_not_blocked():
    """占位符在描述句子中(引用):不应拦截"""
    text = """
## 占位符机制说明

报告中描述占位符机制时引用 "(待填写)" 字样不应被拦截,
因为这是在说明占位符的用法,不是实际的占位符。
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) == 0, "描述中引用占位符字样不应被拦截"


def test_placeholder_in_code_block_not_blocked():
    """占位符在代码块中:不应拦截"""
    text = """
## 示例代码

```python
# 模板中的占位符
result_summary = "(待填写)"
```

这段代码展示了如何处理占位符。
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) == 0, "代码块中的占位符不应被拦截"


def test_f63_return_not_blocked():
    """F63 交回报告原文:不应被误拦(实撞样本1)"""
    # F63 实撞:报告在描述占位符检测机制时引用 "(待填写)" 被误拦
    text = """
## 改动清单

### tools/aipos_cli/validation_common.py
- 新增 `check_placeholder_in_text()` 函数:检查文本字段是否包含占位符字符串 "(待填写)" 等
- 新增 `check_required_field()` 函数:统一必填字段校验

### tools/aipos_cli/record_writer.py
- 在 `build_return_record()` 中调用 `check_required_field()` 校验必填字段
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) == 0, "F63 交回报告不应被误拦"


def test_f65a_return_not_blocked():
    """F65A 交回报告原文:不应被误拦(实撞样本2)"""
    # F65A 实撞:与 F63 类似,描述占位符机制
    text = """
## 验收核对

✅ ① 占位符检测作用于所有 return 必填字段(result_summary / changes / evidence)
✅ ② 占位符字典单一源 PLACEHOLDER_PATTERNS = ["(待填写)", "TODO", ...]
✅ ③ 检测到占位符即 BLOCK,输出包含 "PLACEHOLDER_DETECTED" 与出口提示
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) == 0, "F65A 交回报告不应被误拦"


def test_real_skeleton_blocked():
    """真正的骨架(只有占位符):应拦截"""
    text = """
## 一句话结论

(待填写)

## 做了什么

(待填写)
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) > 0, "真骨架应被拦截"


def test_mixed_content_with_placeholder_description_not_blocked():
    """混合内容含占位符描述:不应拦截"""
    text = """
## 占位符检测改进

本次修改实现了占位符检测的改进。当报告中描述 "(待填写)" 占位符的使用方法时,
不应触发误拦。实际的独占行占位符仍会被正确拦截。

测试验证:
- 引用描述: 通过 ✓
- 独占行: 拦截 ✓
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) == 0, "混合内容中的占位符描述不应被拦截"


def test_todo_standalone_blocked():
    """TODO 独占一行:应拦截"""
    text = """
## 待办事项

TODO

## 其他
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) > 0, "独占的 TODO 应被拦截"


def test_todo_in_sentence_not_blocked():
    """TODO 在句子中:不应拦截"""
    text = """
## 说明

需要处理 TODO 列表中的所有项目。
"""
    reasons = check_placeholder_in_text(text, "test_field")
    assert len(reasons) == 0, "句子中的 TODO 不应被拦截"


if __name__ == "__main__":
    print("Running AIPOS-F65C 件④ placeholder detection tests...")
    
    test_placeholder_standalone_line_blocked()
    print("✓ test_placeholder_standalone_line_blocked")
    
    test_placeholder_in_list_item_blocked()
    print("✓ test_placeholder_in_list_item_blocked")
    
    test_placeholder_in_description_not_blocked()
    print("✓ test_placeholder_in_description_not_blocked")
    
    test_placeholder_in_code_block_not_blocked()
    print("✓ test_placeholder_in_code_block_not_blocked")
    
    test_f63_return_not_blocked()
    print("✓ test_f63_return_not_blocked (实撞样本1)")
    
    test_f65a_return_not_blocked()
    print("✓ test_f65a_return_not_blocked (实撞样本2)")
    
    test_real_skeleton_blocked()
    print("✓ test_real_skeleton_blocked")
    
    test_mixed_content_with_placeholder_description_not_blocked()
    print("✓ test_mixed_content_with_placeholder_description_not_blocked")
    
    test_todo_standalone_blocked()
    print("✓ test_todo_standalone_blocked")
    
    test_todo_in_sentence_not_blocked()
    print("✓ test_todo_in_sentence_not_blocked")
    
    print("\n✅ All placeholder detection tests passed!")
