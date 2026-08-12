#!/usr/bin/env python3
"""AIPOS-R5A: WorktreeManager 单元测试。"""

import os
import subprocess
import tempfile
from pathlib import Path

from tools.worktree_manager import WorktreeManager, WorktreeInfo


def setup_test_repo() -> Path:
    """创建测试 git 仓库。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="test_worktree_"))
    
    # 初始化 git 仓库
    subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmpdir, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmpdir, check=True, capture_output=True
    )
    
    # 创建初始提交
    (tmpdir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmpdir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmpdir, check=True, capture_output=True
    )
    
    # 确保在 main 分支上
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmpdir, check=True, capture_output=True, text=True
    )
    current_branch = result.stdout.strip()
    if current_branch != "main":
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=tmpdir, check=True, capture_output=True
        )
    
    return tmpdir


def test_worktree_creation():
    """测试 worktree 创建。"""
    repo = setup_test_repo()
    
    try:
        manager = WorktreeManager(repo)
        
        # 创建 worktree
        task_id = "TEST-001"
        worktree_path, branch_name = manager.create_worktree(task_id)
        
        print(f"✓ Created worktree: {worktree_path}")
        print(f"  Branch: {branch_name}")
        
        # 验证 worktree 存在
        assert worktree_path.exists(), "Worktree path should exist"
        assert branch_name == f"card/{task_id}", f"Branch name should be card/{task_id}"
        
        # 验证 worktree 列表
        worktrees = manager.list_worktrees()
        assert len(worktrees) >= 2, "Should have at least 2 worktrees (main + task)"
        
        task_wt = manager.get_worktree_for_branch(branch_name)
        assert task_wt is not None, "Should find worktree for task branch"
        assert task_wt.path == worktree_path, "Worktree path should match"
        
        print(f"✓ Worktree verified in git worktree list")
        
        # 在 worktree 中创建文件
        test_file = worktree_path / "test.txt"
        test_file.write_text("Test content\n")
        subprocess.run(["git", "add", "test.txt"], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add test file"],
            cwd=worktree_path, check=True, capture_output=True
        )
        
        print(f"✓ Committed change in worktree")
        
        # 清理 worktree
        removed = manager.remove_worktree(task_id=task_id)
        assert removed, "Worktree should be removed"
        
        print(f"✓ Removed worktree")
        
        # 删除分支
        deleted = manager.delete_branch(branch_name, force=True)
        assert deleted, "Branch should be deleted"
        
        print(f"✓ Deleted branch")
        
        print("\n✓✓✓ All tests passed!")
        
    finally:
        # 清理测试仓库
        import shutil
        shutil.rmtree(repo, ignore_errors=True)


def test_merge_workflow():
    """测试合并工作流。"""
    repo = setup_test_repo()
    
    try:
        manager = WorktreeManager(repo)
        
        # 创建 worktree
        task_id = "TEST-002"
        worktree_path, branch_name = manager.create_worktree(task_id)
        
        print(f"✓ Created worktree for {task_id}")
        
        # 在 worktree 中进行修改
        test_file = worktree_path / "feature.txt"
        test_file.write_text("New feature\n")
        subprocess.run(["git", "add", "feature.txt"], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add new feature"],
            cwd=worktree_path, check=True, capture_output=True
        )
        
        print(f"✓ Committed changes in worktree")
        
        # 合并到 main
        merge_result = manager.merge_to_main(branch_name, strategy='squash')
        print(f"✓ Merged to main: {merge_result}")
        
        # 验证文件存在于 main
        main_file = repo / "feature.txt"
        assert main_file.exists(), "File should exist in main after merge"
        
        print(f"✓ Verified merge result in main")
        
        # 清理
        cleanup_result = manager.cleanup_task(task_id, remove_branch=True, force=True)
        print(f"✓ Cleanup: {cleanup_result}")
        
        # 验证清理后状态
        worktrees = manager.list_worktrees()
        assert all(wt.branch != branch_name for wt in worktrees), "Task branch should be gone"
        
        print(f"✓ Verified cleanup (branch removed)")
        
        print("\n✓✓✓ Merge workflow test passed!")
        
    finally:
        import shutil
        shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    print("Testing WorktreeManager...\n")
    print("=" * 60)
    print("Test 1: Worktree Creation and Removal")
    print("=" * 60)
    test_worktree_creation()
    
    print("\n" + "=" * 60)
    print("Test 2: Merge Workflow")
    print("=" * 60)
    test_merge_workflow()
