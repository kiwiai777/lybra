#!/usr/bin/env python3
"""AIPOS-F53: 修复轮承接判定测试 - 结案-承接形态

验收②: 卡A结案+续卡B承接，用B的裁决可部署A的commit
"""
import json
import subprocess
import tempfile
from pathlib import Path


def test_f53_conclusion_continuation_lineage(tmp_path):
    """AIPOS-F53 验收②: 结案-承接形态 - 续卡裁决覆盖结案卡 commit"""
    
    print("\n=== AIPOS-F53 验收② 结案-承接形态 ===\n")
    
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
    
    # 创建结案卡 commit (TEST-010)
    (repo_root / "feature.py").write_text("def feature(): pass")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "TEST-010: Add feature (blocked by card defect)"], cwd=repo_root, check=True, capture_output=True)
    
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True
    )
    test_010_commit = result.stdout.strip()
    print(f"✓ 结案卡 commit (TEST-010): {test_010_commit[:8]}")
    
    # 准备治理结构
    # 1. 创建 schema/config.schema.json
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
    
    # 2. card_policy.json
    (governance_root / "card_policy.json").write_text(json.dumps({
        "task_id_pattern": "TEST-[0-9]+((-fix[0-9]+)*)?",
        "version": 1
    }))
    
    # 3. 创建 TEST-010 结案任务卡（含承接声明）
    queue_completed = governance_root / "5_tasks" / "queue" / "completed"
    queue_completed.mkdir(parents=True)
    
    test_010_card = queue_completed / "test-010.md"
    test_010_card.write_text(f"""---
task_id: TEST-010
title: TEST-010 原始任务（因卡面缺陷结案）
status: completed
conclusion_note: '卡面缺陷结案(非执行体问题):output_target 起草时漏列关键文件。工作已完成于 card/TEST-010 分支 commit {test_010_commit[:8]}, 由续卡 TEST-011 承接交回'
---
# TEST-010 结案卡
""")
    print(f"✓ 创建 TEST-010 结案卡（声明由 TEST-011 承接）")
    
    # 4. 创建 TEST-011 的 PASS 裁决
    verdicts_root = governance_root / "5_tasks" / "records" / "audit_verdicts" / "TEST-011"
    verdicts_root.mkdir(parents=True)
    
    verdict_file = verdicts_root / "verdict_TEST-011_20260828_000000_audit-test.md"
    verdict_file.write_text(f"""---
record_type: audit_verdict
verdict_id: verdict_TEST-011_20260828_000000_audit-test
reviewed_task_id: TEST-011
verdict: PASS
verdict_at: 2026-08-28T00:00:00Z
auditor: audit.test
---
# Audit Verdict: TEST-011 PASS
""")
    print(f"✓ 创建 TEST-011 PASS 裁决")
    
    print()
    
    # 测试: 用 TEST-011 的裁决部署 TEST-010 的 commit
    print("=== 测试: 用承接卡裁决部署结案卡 commit ===")
    
    from tools.aipos_cli.deployment_authorization import check_verdict_ref_authorization
    
    result = check_verdict_ref_authorization(
        verdict_ref="verdict_TEST-011_20260828_000000_audit-test",
        governance_root=governance_root,
        commits_to_deploy=[test_010_commit],
        repo_root=repo_root,
    )
    
    print(f"authorized: {result['authorized']}")
    print(f"message: {result['message']}")
    
    if result['authorized']:
        print("\n✓✓✓ 验收② PASS: 承接卡裁决成功覆盖结案卡 commit ✓✓✓")
        assert result['verdict_id'] == "verdict_TEST-011_20260828_000000_audit-test"
        assert result['reviewed_task_id'] == "TEST-011"
        assert result['uncovered_commits'] == []
    else:
        print(f"\n✗✗✗ 验收② FAIL: 应该授权但被拒绝 ✗✗✗")
        print(f"uncovered_commits: {result['uncovered_commits']}")
        raise AssertionError("承接卡裁决应该覆盖结案卡 commit，但被拒绝")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_f53_conclusion_continuation_lineage(Path(tmpdir))
