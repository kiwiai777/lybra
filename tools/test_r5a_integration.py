#!/usr/bin/env python3
"""AIPOS-R5A: Worktree 隔离集成测试 - claim → execute → finalize 流程。"""

import json
import subprocess
import tempfile
from pathlib import Path


def test_claim_worktree_integration():
    """测试 claim 时创建 worktree 的集成。"""
    
    # 使用真实的产品仓
    repo_root = Path("/home/kiwi/projects/lybra")
    
    # 模拟一个 code 任务卡片
    test_task_id = "TEST-WORKTREE-001"
    
    print(f"Testing worktree integration for {test_task_id}...")
    
    # 检查 worktree 管理器
    from tools.worktree_manager import WorktreeManager
    
    manager = WorktreeManager.from_workspace_config(repo_root)
    print(f"✓ WorktreeManager initialized")
    print(f"  Code repo: {manager.code_repo}")
    print(f"  Worktree root: {manager.worktree_root}")
    
    # 检查当前 worktrees
    worktrees = manager.list_worktrees()
    print(f"✓ Current worktrees: {len(worktrees)}")
    for wt in worktrees:
        print(f"  - {wt.path} ({wt.branch or 'bare'})")
    
    # 模拟 claim 操作 - 创建 worktree
    print(f"\nSimulating claim operation...")
    try:
        worktree_path, branch_name = manager.create_worktree(test_task_id)
        print(f"✓ Created worktree:")
        print(f"  Path: {worktree_path}")
        print(f"  Branch: {branch_name}")
        
        # 验证 worktree 存在
        assert worktree_path.exists(), "Worktree should exist"
        
        # 在 worktree 中模拟工作
        test_file = worktree_path / "test_feature.txt"
        test_file.write_text("Test feature implementation\n")
        
        subprocess.run(
            ["git", "add", "test_feature.txt"],
            cwd=worktree_path,
            check=True,
            capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-c", "user.name=Test User",
                "-c", "user.email=test@example.com",
                "commit", "-m", f"{test_task_id}: Add test feature"
            ],
            cwd=worktree_path,
            check=True,
            capture_output=True
        )
        print(f"✓ Committed test changes in worktree")
        
        # 模拟 finalize 操作 - 合并和清理
        print(f"\nSimulating finalize operation...")
        
        # 合并到 main (使用 squash)
        merge_result = manager.merge_to_main(
            branch_name=branch_name,
            strategy='squash',
            main_branch='main'
        )
        print(f"✓ Merged to main: {merge_result['strategy']}")
        
        # 清理 worktree 和分支
        cleanup_result = manager.cleanup_task(
            task_id=test_task_id,
            remove_branch=True,
            force=False
        )
        print(f"✓ Cleanup result:")
        print(f"  Worktree removed: {cleanup_result['worktree_removed']}")
        print(f"  Branch deleted: {cleanup_result['branch_deleted']}")
        
        # 验证清理后状态
        worktrees_after = manager.list_worktrees()
        branch_still_exists = any(wt.branch == branch_name for wt in worktrees_after)
        
        assert not branch_still_exists, "Task branch should be deleted"
        print(f"✓ Verified: branch {branch_name} is gone")
        
        # 验证文件在 main 中
        main_test_file = repo_root / "test_feature.txt"
        assert main_test_file.exists(), "Feature file should exist in main"
        print(f"✓ Verified: test file exists in main")
        
        # 清理测试文件
        subprocess.run(
            ["git", "rm", "test_feature.txt"],
            cwd=repo_root,
            check=True,
            capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-c", "user.name=Test User",
                "-c", "user.email=test@example.com",
                "commit", "-m", "Clean up test file"
            ],
            cwd=repo_root,
            check=True,
            capture_output=True
        )
        print(f"✓ Cleaned up test file from main")
        
        print(f"\n✓✓✓ Integration test passed!")
        
    except Exception as exc:
        print(f"\n✗ Test failed: {exc}")
        # 清理可能残留的 worktree
        try:
            manager.cleanup_task(test_task_id, remove_branch=True, force=True)
        except:
            pass
        raise


def test_parallel_worktrees():
    """测试并行 worktree 场景（FND-19 主诉）。"""
    
    print("\n" + "=" * 60)
    print("Testing parallel worktrees (FND-19 scenario)")
    print("=" * 60)
    
    from tools.worktree_manager import WorktreeManager
    
    repo_root = Path("/home/kiwi/projects/lybra")
    manager = WorktreeManager.from_workspace_config(repo_root)
    
    task1_id = "TEST-PARALLEL-A"
    task2_id = "TEST-PARALLEL-B"
    
    try:
        # 创建两个并行 worktrees
        wt1_path, branch1 = manager.create_worktree(task1_id)
        print(f"✓ Created worktree 1: {wt1_path}")
        
        wt2_path, branch2 = manager.create_worktree(task2_id)
        print(f"✓ Created worktree 2: {wt2_path}")
        
        # 在两个 worktree 中同时工作
        file1 = wt1_path / "feature_a.txt"
        file1.write_text("Feature A\n")
        subprocess.run(["git", "add", "feature_a.txt"], cwd=wt1_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Add feature A"],
            cwd=wt1_path, check=True, capture_output=True
        )
        print(f"✓ Task A committed in its worktree")
        
        file2 = wt2_path / "feature_b.txt"
        file2.write_text("Feature B\n")
        subprocess.run(["git", "add", "feature_b.txt"], cwd=wt2_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Add feature B"],
            cwd=wt2_path, check=True, capture_output=True
        )
        print(f"✓ Task B committed in its worktree")
        
        # 验证隔离：两个 worktree 互不干扰
        assert file1.exists(), "File A should exist in worktree 1"
        assert not (wt2_path / "feature_a.txt").exists(), "File A should NOT exist in worktree 2"
        assert file2.exists(), "File B should exist in worktree 2"
        assert not (wt1_path / "feature_b.txt").exists(), "File B should NOT exist in worktree 1"
        print(f"✓ Verified: worktrees are isolated")
        
        # 清理
        manager.cleanup_task(task1_id, remove_branch=True, force=True)
        manager.cleanup_task(task2_id, remove_branch=True, force=True)
        print(f"✓ Cleaned up both worktrees")
        
        print(f"\n✓✓✓ Parallel worktrees test passed!")
        
    except Exception as exc:
        print(f"\n✗ Test failed: {exc}")
        # 清理
        try:
            manager.cleanup_task(task1_id, remove_branch=True, force=True)
            manager.cleanup_task(task2_id, remove_branch=True, force=True)
        except:
            pass
        raise


if __name__ == "__main__":
    print("=" * 60)
    print("AIPOS-R5A Integration Tests")
    print("=" * 60)
    
    test_claim_worktree_integration()
    test_parallel_worktrees()
    
    print("\n" + "=" * 60)
    print("✓✓✓ All integration tests passed!")
    print("=" * 60)
