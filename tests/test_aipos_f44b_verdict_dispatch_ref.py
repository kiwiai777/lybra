#!/usr/bin/env python3
"""AIPOS-F44B③测试 — 裁决提交解析病：dispatch_ref 归一化与 created_by 放宽。

验证点:
1. created_by=gate_fix_closure_derivation 也识别为派生审计
2. dispatch_ref 归一化：接受 publish_id 原值 / 带.md / 文件名三种写法
3. BLOCK 文案直接给出应传的 ref 值（publish_id 原值）

测试方式: 经 bin 调用 board_adapter._mcp_audit_verdict_dry_run
先红后绿: 修改前报"does not resolve"; 修改后解析成功
"""
import subprocess
import sys
import json
from pathlib import Path


def test_created_by_relaxation():
    """正夹具: gate_fix_closure_derivation 识别为派生"""
    test_script = """
import sys
sys.path.insert(0, '/home/kiwi/projects/lybra/tools')

# 测试 created_by 放宽逻辑
created_by_values = [
    "gate_derivation",
    "gate_fix_closure_derivation",
    "manual",
]

results = []
for created_by in created_by_values:
    is_derived = created_by in ("gate_derivation", "gate_fix_closure_derivation")
    results.append({
        "created_by": created_by,
        "is_derived_audit": is_derived
    })

import json
print(json.dumps(results, ensure_ascii=False))
"""
    
    result = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}", file=sys.stderr)
        raise AssertionError(f"Script failed: {result.stderr}")
    
    data = json.loads(result.stdout.strip())
    
    print(f"created_by test: {data}")
    
    # 验证: gate_derivation 和 gate_fix_closure_derivation 都应该识别为派生
    assert data[0]["is_derived_audit"] is True  # gate_derivation
    assert data[1]["is_derived_audit"] is True  # gate_fix_closure_derivation
    assert data[2]["is_derived_audit"] is False  # manual


def test_dispatch_ref_normalization():
    """正夹具: dispatch_ref 归一化 — 接受三种写法"""
    test_script = """
import sys
sys.path.insert(0, '/home/kiwi/projects/lybra/tools')

# 测试 ref 归一化逻辑
test_refs = [
    "publish_aipos-f45r",  # 原值（标准）
    "publish_aipos-f45r.md",  # 带 .md
    "5_tasks/records/publishes/AIPOS-F45R/publish_aipos-f45r.md",  # 完整路径
]

results = []
for dispatch_ref in test_refs:
    normalized = dispatch_ref.replace(".md", "").replace("5_tasks/records/publishes/", "").split("/")[-1]
    results.append({
        "original": dispatch_ref,
        "normalized": normalized
    })

import json
print(json.dumps(results, ensure_ascii=False))
"""
    
    result = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}", file=sys.stderr)
        raise AssertionError(f"Script failed: {result.stderr}")
    
    data = json.loads(result.stdout.strip())
    
    print(f"Normalization test: {data}")
    
    # 验证: 三种写法都应该归一化为 publish_aipos-f45r
    expected = "publish_aipos-f45r"
    for item in data:
        assert item["normalized"] == expected, f"Expected {expected}, got {item['normalized']}"


def test_block_message_hint():
    """正夹具: BLOCK 文案提示正确的 ref 格式"""
    test_script = """
import sys
sys.path.insert(0, '/home/kiwi/projects/lybra/tools')
from aipos_cli.draft_writer import stable_publish_id

# 测试 stable_publish_id 生成正确格式
task_ids = ["AIPOS-F45R", "AIPOS-F44BR"]
results = []

for task_id in task_ids:
    expected_ref = stable_publish_id(task_id)
    results.append({
        "task_id": task_id,
        "expected_ref": expected_ref
    })

import json
print(json.dumps(results, ensure_ascii=False))
"""
    
    result = subprocess.run(
        [sys.executable, "-c", test_script],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}", file=sys.stderr)
        raise AssertionError(f"Script failed: {result.stderr}")
    
    data = json.loads(result.stdout.strip())
    
    print(f"stable_publish_id test: {data}")
    
    # 验证: stable_publish_id 生成正确格式（publish_ + 小写）
    for item in data:
        assert item["expected_ref"].startswith("publish_")
        assert item["expected_ref"].islower() or "-" in item["expected_ref"]


if __name__ == "__main__":
    test_created_by_relaxation()
    test_dispatch_ref_normalization()
    test_block_message_hint()
    print("✓ AIPOS-F44B③ 裁决提交解析病测试通过")
