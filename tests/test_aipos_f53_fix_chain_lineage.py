#!/usr/bin/env python3
"""AIPOS-F53: 修复轮承接判定测试 - 先红后绿

验收①: 构造 FAIL→fix→PASS 链，用链末端裁决 finalize
- 修复前: 报"跨卡挪用"拒绝
- 修复后: 放行且 provenance=audited
"""
import json
import subprocess
import tempfile
from pathlib import Path


def test_f53_fix_chain_lineage_red_then_green(tmp_path):
    """AIPOS-F53 验收①: 先红后绿 - fix 链末端裁决覆盖原卡 commit"""
    
    print("\n=== AIPOS-F53 验收① 先红后绿 ===\n")
    
    # 准备: 创建治理工作区和产品仓
    governance_root = tmp_path / "governance"
    repo_root = tmp_path / "repo"
    
    governance_root.mkdir()
    repo_root.mkdir()
    
    # 初始化 git 仓库
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建基线 commit
    (repo_root / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_root, check=True, capture_output=True)
    
    # 创建原卡 commit (TEST-001)
    (repo_root / "feature.py").write_text("def feature(): pass")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "TEST-001: Add feature"], cwd=repo_root, check=True, capture_output=True)
    
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True
    )
    test_001_commit = result.stdout.strip()
    print(f"✓ 原卡 commit (TEST-001): {test_001_commit[:8]}")
    
    # 创建 fix 卡 commit (TEST-001-fix1)
    (repo_root / "feature.py").write_text("def feature():\n    # Fixed\n    pass")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "TEST-001-fix1: Fix feature"], cwd=repo_root, check=True, capture_output=True)
    
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True
    )
    test_001_fix1_commit = result.stdout.strip()
    print(f"✓ fix 卡 commit (TEST-001-fix1): {test_001_fix1_commit[:8]}")
    
    # 准备治理结构
    # 1. 创建 schema/config.schema.json (用于路径解析)
    schema_root = repo_root / "schema"
    schema_root.mkdir()
    (schema_root / "config.schema.json").write_text(json.dumps({
        "governance_root": str(governance_root),
        "multi_project_support": {
            "project_registry": {
                "structure": {
                    "card_policy": "card_policy.json"
                }
            }
        },
        "version": 1
    }))
    
    # 2. card_policy.json (声明 task_id_pattern)
    (governance_root / "card_policy.json").write_text(json.dumps({
        "task_id_pattern": "TEST-[0-9]+((-fix[0-9]+)*)?",
        "version": 1
    }))
    
    # 3. 创建 fix 链派生记录
    fix_closures_root = governance_root / "5_tasks" / "records" / "fix_closures" / "TEST-001-fix1"
    fix_closures_root.mkdir(parents=True)
    
    derivation_file = fix_closures_root / "derivation_TEST-001-fix1_20260828_000000.md"
    derivation_file.write_text(f"""---
fix_task_id: TEST-001-fix1
source_task_id: TEST-001
derived_audit_task_id: TEST-001R2
verdict_id: verdict_TEST-001-fix1_20260828_000000_audit-test
derived_at: 2026-08-28T00:00:00Z
record_type: fix_closure_derivation
event_type: fix_closure_derivation
derived_by: gate_fix_closure_derivation
---
# Fix Closure Derivation Record: TEST-001-fix1
""")
    print(f"✓ 创建 fix 链派生记录: TEST-001 → TEST-001-fix1")
    
    # 3. 创建 TEST-001-fix1 的 PASS 裁决
    verdicts_root = governance_root / "5_tasks" / "records" / "audit_verdicts" / "TEST-001-fix1"
    verdicts_root.mkdir(parents=True)
    
    verdict_file = verdicts_root / "verdict_TEST-001-fix1_20260828_000000_audit-test.md"
    verdict_file.write_text(f"""---
record_type: audit_verdict
verdict_id: verdict_TEST-001-fix1_20260828_000000_audit-test
reviewed_task_id: TEST-001-fix1
verdict: PASS
verdict_at: 2026-08-28T00:00:00Z
auditor: audit.test
---
# Audit Verdict: TEST-001-fix1 PASS
""")
    print(f"✓ 创建 TEST-001-fix1 PASS 裁决")
    
    print()
    
    # 测试: 用 TEST-001-fix1 的裁决部署 TEST-001 的 commit
    print("=== 测试: 用 fix 链末端裁决部署原卡 commit ===")
    
    from tools.aipos_cli.deployment_authorization import check_verdict_ref_authorization
    
    result = check_verdict_ref_authorization(
        verdict_ref="verdict_TEST-001-fix1_20260828_000000_audit-test",
        governance_root=governance_root,
        commits_to_deploy=[test_001_commit],
        repo_root=repo_root,
    )
    
    print(f"authorized: {result['authorized']}")
    print(f"message: {result['message']}")
    
    if result['authorized']:
        print("\n✓✓✓ 验收① PASS: fix 链末端裁决成功覆盖原卡 commit ✓✓✓")
        assert result['verdict_id'] == "verdict_TEST-001-fix1_20260828_000000_audit-test"
        assert result['reviewed_task_id'] == "TEST-001-fix1"
        assert result['uncovered_commits'] == []
    else:
        print(f"\n✗✗✗ 验收① FAIL: 应该授权但被拒绝 ✗✗✗")
        print(f"uncovered_commits: {result['uncovered_commits']}")
        raise AssertionError("Fix 链末端裁决应该覆盖原卡 commit，但被拒绝")
    
    # 同时测试 fix 卡自身的 commit 也能被覆盖
    print()
    print("=== 测试: fix 链末端裁决覆盖 fix 卡自身 commit ===")
    
    result2 = check_verdict_ref_authorization(
        verdict_ref="verdict_TEST-001-fix1_20260828_000000_audit-test",
        governance_root=governance_root,
        commits_to_deploy=[test_001_fix1_commit],
        repo_root=repo_root,
    )
    
    print(f"authorized: {result2['authorized']}")
    
    if result2['authorized']:
        print("✓ fix 链末端裁决也覆盖自身 commit")
    else:
        print("✗ fix 卡自身 commit 应该被覆盖")
        raise AssertionError("Fix 链末端裁决应该覆盖自身 commit")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_f53_fix_chain_lineage_red_then_green(Path(tmpdir))
