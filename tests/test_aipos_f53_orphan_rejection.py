#!/usr/bin/env python3
"""AIPOS-F53: 修复轮承接判定测试 - 负夹具

验收③: 无链关系的孤儿 commit 仍拒绝，但带路给出 dev_override 出口
"""
import json
import subprocess
import tempfile
from pathlib import Path


def test_f53_orphan_commit_rejection_with_guidance(tmp_path):
    """AIPOS-F53 验收③: 负夹具 - 孤儿 commit 拒绝 + dev_override 出口"""
    
    print("\n=== AIPOS-F53 验收③ 负夹具 ===\n")
    
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
    
    # 创建孤儿 commit (TEST-020，无任何链关系)
    (repo_root / "orphan.py").write_text("def orphan(): pass")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "TEST-020: Orphan commit"], cwd=repo_root, check=True, capture_output=True)
    
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True
    )
    orphan_commit = result.stdout.strip()
    print(f"✓ 孤儿 commit (TEST-020): {orphan_commit[:8]}")
    
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
    
    # 3. 创建 TEST-999 的 PASS 裁决（与孤儿 commit 无关）
    verdicts_root = governance_root / "5_tasks" / "records" / "audit_verdicts" / "TEST-999"
    verdicts_root.mkdir(parents=True)
    
    verdict_file = verdicts_root / "verdict_TEST-999_20260828_000000_audit-test.md"
    verdict_file.write_text(f"""---
record_type: audit_verdict
verdict_id: verdict_TEST-999_20260828_000000_audit-test
reviewed_task_id: TEST-999
verdict: PASS
verdict_at: 2026-08-28T00:00:00Z
auditor: audit.test
---
# Audit Verdict: TEST-999 PASS
""")
    print(f"✓ 创建 TEST-999 PASS 裁决（与孤儿 commit 无关）")
    
    print()
    
    # 测试: 用 TEST-999 的裁决部署 TEST-020 的孤儿 commit
    print("=== 测试: 用无关裁决部署孤儿 commit（应拒绝 + 带路）===")
    
    from tools.aipos_cli.deployment_authorization import check_verdict_ref_authorization
    
    result = check_verdict_ref_authorization(
        verdict_ref="verdict_TEST-999_20260828_000000_audit-test",
        governance_root=governance_root,
        commits_to_deploy=[orphan_commit],
        repo_root=repo_root,
    )
    
    print(f"authorized: {result['authorized']}")
    print(f"message: {result['message'][:200]}...")
    
    if not result['authorized']:
        print("\n✓ 孤儿 commit 被正确拒绝")
        
        # 检查错误消息是否包含 dev_override 出口引导
        message = result['message']
        if "dev_override" in message and "lybra-deploy deploy --dev-override" in message:
            print("✓ 错误消息包含 dev_override 出口引导")
            print(f"\n出口引导内容:\n{message}")
            print("\n✓✓✓ 验收③ PASS: 孤儿 commit 拒绝 + dev_override 出口引导完整 ✓✓✓")
        else:
            print(f"✗ 错误消息缺少 dev_override 出口引导")
            print(f"实际消息: {message}")
            raise AssertionError("错误消息应包含 dev_override 出口引导")
    else:
        print(f"\n✗✗✗ 验收③ FAIL: 孤儿 commit 应该被拒绝但被授权 ✗✗✗")
        raise AssertionError("孤儿 commit 应该被拒绝")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_f53_orphan_commit_rejection_with_guidance(Path(tmpdir))
