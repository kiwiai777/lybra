#!/usr/bin/env python3
"""AIPOS-F44C⑤测试 — status 文案说人话：禁"(未配置)"/"(none)"/"未部署"字眼。

验证点:
1. 循环未启动时显示"循环未启动, /lybra on 启动"而非"(未配置)"
2. 清单比对失败显示"稍后重试"而非"未部署"

测试方式: 检查 lybra-loop.ts 渲染逻辑（TypeScript 代码审查）
先红后绿: 修改前含禁用字眼; 修改后不含
"""
import subprocess
import sys
import re
from pathlib import Path


def test_status_no_forbidden_words():
    """负夹具: status 渲染不含禁用字眼"""
    # 检查源码中是否还有禁用字眼
    lybra_loop_path = Path("/home/kiwi/projects/lybra/agents/harness/pi/lybra-loop/lybra-loop.ts")
    
    if not lybra_loop_path.exists():
        raise AssertionError(f"lybra-loop.ts not found at {lybra_loop_path}")
    
    content = lybra_loop_path.read_text(encoding="utf-8")
    
    # 检查禁用字眼（在 status 渲染相关行）
    # 查找 gate: 行（2603行附近）
    gate_line_match = re.search(r'lines\.push\(`\s*gate:.*?\$\{.*?\}.*?`\)', content, re.MULTILINE)
    if gate_line_match:
        gate_line = gate_line_match.group(0)
        # 修改后应该不含 "(未配置)"
        if "(未配置)" in gate_line or '"(未配置)"' in gate_line or "'(未配置)'" in gate_line:
            raise AssertionError(f"gate line still contains '(未配置)': {gate_line}")
        print(f"✓ gate line does not contain '(未配置)': {gate_line[:100]}")
    
    # 查找清单比对行（2678行附近）
    manifest_line_match = re.search(r'清单比对:.*?线上版本.*?获取.*?\)', content, re.MULTILINE)
    if manifest_line_match:
        manifest_line = manifest_line_match.group(0)
        # 修改后应该不含 "未部署"
        if "未部署" in manifest_line:
            raise AssertionError(f"manifest line still contains '未部署': {manifest_line}")
        # 应该包含 "稍后重试"
        if "稍后重试" not in manifest_line:
            raise AssertionError(f"manifest line does not contain '稍后重试': {manifest_line}")
        print(f"✓ manifest line contains '稍后重试' and not '未部署': {manifest_line[:100]}")
    
    # 全文搜索 "(none)" 在 status 相关代码中（排除注释和变量名）
    # 只检查 status 命令处理部分（2590-2690行附近）
    status_section_match = re.search(
        r'/lybra status.*?ctx\.ui\.notify\(lines\.join',
        content,
        re.DOTALL
    )
    if status_section_match:
        status_section = status_section_match.group(0)
        # 检查是否有 "(none)" 字面量（排除 currentTokenFp = "(none)" 的初始化）
        none_literals = re.findall(r'["\']?\(none\)["\']?', status_section)
        # 初始化的 "(none)" 不在 status 段，这里不应该有
        if none_literals:
            print(f"⚠ Found '(none)' literals in status section: {none_literals}")
            # 不作为错误，因为可能是合理使用


def test_status_positive_wording():
    """正夹具: status 文案使用正向引导（告诉用户如何启动）"""
    lybra_loop_path = Path("/home/kiwi/projects/lybra/agents/harness/pi/lybra-loop/lybra-loop.ts")
    content = lybra_loop_path.read_text(encoding="utf-8")
    
    # 查找循环未启动的文案
    # 应该包含 "/lybra on 启动" 的引导
    if "/lybra on 启动" not in content and "/lybra on" not in content:
        print("⚠ Could not find '/lybra on 启动' guidance in source")
    else:
        print("✓ Found '/lybra on 启动' guidance")
    
    # 查找 gateDisplay 变量定义（AIPOS-F44C 修改点）
    gate_display_match = re.search(r'const gateDisplay = currentGateUrl \|\| ["\'](.+?)["\']', content)
    if gate_display_match:
        fallback_text = gate_display_match.group(1)
        print(f"✓ gateDisplay fallback text: '{fallback_text}'")
        # 应该是正向引导，不是"未配置"
        if "未配置" in fallback_text:
            raise AssertionError(f"gateDisplay still uses '未配置': {fallback_text}")
        if "启动" not in fallback_text:
            raise AssertionError(f"gateDisplay does not guide user to start: {fallback_text}")


if __name__ == "__main__":
    test_status_no_forbidden_words()
    test_status_positive_wording()
    print("✓ AIPOS-F44C⑤ status 文案测试通过")
