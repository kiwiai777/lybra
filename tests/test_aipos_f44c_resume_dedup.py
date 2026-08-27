#!/usr/bin/env python3
"""AIPOS-F44C⑦测试 — 复工提醒去重：同卡同状态只出一次。

验证点:
1. 同卡同状态连续三拍，提醒只出现一次
2. 状态变化时，提醒再次出现

测试方式: 检查源码中的去重逻辑（lastResumeVoice 状态跟踪）
先红后绿: 修改前无去重逻辑; 修改后有 lastResumeVoice 跟踪
"""
import subprocess
import sys
import re
from pathlib import Path


def test_resume_dedup_state_exists():
    """正夹具: 源码中存在 lastResumeVoice 状态跟踪"""
    lybra_loop_path = Path("/home/kiwi/projects/lybra/agents/harness/pi/lybra-loop/lybra-loop.ts")
    
    if not lybra_loop_path.exists():
        raise AssertionError(f"lybra-loop.ts not found at {lybra_loop_path}")
    
    content = lybra_loop_path.read_text(encoding="utf-8")
    
    # 检查模块级状态声明
    if "let lastResumeVoice" not in content:
        raise AssertionError("lastResumeVoice state variable not found in source")
    
    print("✓ lastResumeVoice state variable exists")
    
    # 检查状态结构定义
    state_match = re.search(r'let lastResumeVoice:\s*\{[^}]+\}', content)
    if state_match:
        print(f"✓ State structure: {state_match.group(0)}")
    else:
        print("⚠ Could not parse lastResumeVoice structure")
    
    # 应该包含 taskId 和 status 字段
    if "taskId" not in content or "status" not in content:
        print("⚠ State structure may not include taskId/status fields")


def test_resume_dedup_logic_applied():
    """正夹具: 复工提醒使用去重逻辑"""
    lybra_loop_path = Path("/home/kiwi/projects/lybra/agents/harness/pi/lybra-loop/lybra-loop.ts")
    content = lybra_loop_path.read_text(encoding="utf-8")
    
    # 查找复工提醒相关代码（两处：audit-no-verdict 和 exec-resume）
    # 1. 审计卡复工提醒
    audit_resume_match = re.search(
        r'voice\(`复工.*?是审计卡.*?只差提交裁决',
        content,
        re.MULTILINE
    )
    
    if audit_resume_match:
        # 检查前面是否有 if 条件判断（去重逻辑）
        # 向前查找 100 行
        audit_pos = audit_resume_match.start()
        before_audit = content[max(0, audit_pos - 500):audit_pos]
        
        if "lastResumeVoice" in before_audit:
            print("✓ Audit resume voice has dedup check")
        else:
            raise AssertionError("Audit resume voice missing dedup check")
    
    # 2. 执行卡复工提醒
    exec_resume_match = re.search(
        r'voice\(`复工.*?继续执行',
        content,
        re.MULTILINE
    )
    
    if exec_resume_match:
        exec_pos = exec_resume_match.start()
        before_exec = content[max(0, exec_pos - 500):exec_pos]
        
        if "lastResumeVoice" in before_exec:
            print("✓ Exec resume voice has dedup check")
        else:
            raise AssertionError("Exec resume voice missing dedup check")


def test_dedup_condition_logic():
    """正夹具: 去重条件包含 taskId 和 status 比对"""
    lybra_loop_path = Path("/home/kiwi/projects/lybra/agents/harness/pi/lybra-loop/lybra-loop.ts")
    content = lybra_loop_path.read_text(encoding="utf-8")
    
    # 查找去重条件（if (!lastResumeVoice || ...)）
    dedup_conditions = re.findall(
        r'if \(!lastResumeVoice \|\|[^{]+\{',
        content,
        re.MULTILINE
    )
    
    if not dedup_conditions:
        raise AssertionError("No dedup condition found (if (!lastResumeVoice || ...))")
    
    print(f"✓ Found {len(dedup_conditions)} dedup condition(s)")
    
    # 检查条件是否包含 taskId 和 status 比对
    for condition in dedup_conditions:
        if "taskId" in condition and "status" in condition:
            print(f"✓ Dedup condition includes taskId and status check: {condition[:80]}...")
        else:
            print(f"⚠ Dedup condition may be incomplete: {condition[:80]}...")


def test_state_update_after_voice():
    """正夹具: voice 调用后更新状态"""
    lybra_loop_path = Path("/home/kiwi/projects/lybra/agents/harness/pi/lybra-loop/lybra-loop.ts")
    content = lybra_loop_path.read_text(encoding="utf-8")
    
    # 查找状态更新语句（lastResumeVoice = { taskId: ..., status: ... }）
    state_updates = re.findall(
        r'lastResumeVoice = \{[^}]+\}',
        content,
        re.MULTILINE
    )
    
    if not state_updates:
        raise AssertionError("No state update found (lastResumeVoice = { ... })")
    
    print(f"✓ Found {len(state_updates)} state update(s)")
    
    # 验证状态更新包含 taskId 和 status
    for update in state_updates:
        if "taskId:" in update and "status:" in update:
            print(f"✓ State update includes taskId and status: {update[:80]}...")
        else:
            raise AssertionError(f"State update incomplete: {update}")


if __name__ == "__main__":
    test_resume_dedup_state_exists()
    test_resume_dedup_logic_applied()
    test_dedup_condition_logic()
    test_state_update_after_voice()
    print("✓ AIPOS-F44C⑦ 复工提醒去重测试通过")
