"""AIPOS-F66C 件② 测试: 工位仓隔离 — 一工位一 worktree, 禁 stash/pull --rebase。

验收要求:
- 件②活体: 两工位目录各自为独立 worktree (git worktree list 证)
- 禁令分发: 三工位章程含禁 stash/pull --rebase 条款 (grep 证)
- 夹具: 两工位各自 worktree 并发写, 断言互不波及

红线: 多工位共用一棵 git 工作树是 08-28 凭据连坐与 09-05 wrapper 借尸还魂的结构根因。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def test_charter_contains_git_isolation_rule():
    """验证所有角色章程包含 git 隔离纪律条款。"""
    repo = Path(__file__).resolve().parents[1]
    roles = ["executor", "auditor", "advisor"]
    
    for role in roles:
        charter = repo / "agents" / "roles" / role / "AGENTS.md"
        assert charter.exists(), f"Charter not found: {charter}"
        
        content = charter.read_text(encoding="utf-8")
        # 检查包含 git 隔离纪律
        assert "工位仓 git 隔离纪律" in content or "工位仓 git 隔离" in content, \
            f"{role} charter missing git isolation discipline"
        assert "禁 `git stash`" in content or "禁 git stash" in content, \
            f"{role} charter missing git stash prohibition"
        assert "禁 `git pull --rebase`" in content or "禁 git pull --rebase" in content, \
            f"{role} charter missing git pull --rebase prohibition"
        assert "AIPOS-F66C" in content, \
            f"{role} charter missing F66C reference"


def test_worktree_isolation_fixture():
    """夹具: 两工位各自 worktree 并发写, 断言互不波及。
    
    模拟场景: 工位A和工位B各自在独立的 worktree 中修改文件,
    验证一个工位的修改不会影响另一个工位。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        
        # 创建主仓库
        main_repo = base / "main-repo"
        main_repo.mkdir()
        subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=main_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=main_repo, check=True, capture_output=True)
        
        # 创建初始文件并提交
        (main_repo / "README.md").write_text("main repo")
        subprocess.run(["git", "add", "README.md"], cwd=main_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=main_repo, check=True, capture_output=True)
        
        # 创建两个独立的 worktree (模拟两个工位)
        workspace_a = base / "workspace-a"
        workspace_b = base / "workspace-b"
        
        subprocess.run(
            ["git", "worktree", "add", str(workspace_a), "-b", "workspace-a"],
            cwd=main_repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "worktree", "add", str(workspace_b), "-b", "workspace-b"],
            cwd=main_repo, check=True, capture_output=True
        )
        
        # 验证 worktree 列表包含两个工位
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=main_repo, capture_output=True, text=True, check=True
        )
        worktree_list = result.stdout
        assert str(workspace_a) in worktree_list
        assert str(workspace_b) in worktree_list
        
        # 工位A写文件
        (workspace_a / "file-a.txt").write_text("from workspace A")
        subprocess.run(["git", "add", "file-a.txt"], cwd=workspace_a, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "A commit"], cwd=workspace_a, check=True, capture_output=True)
        
        # 工位B写文件
        (workspace_b / "file-b.txt").write_text("from workspace B")
        subprocess.run(["git", "add", "file-b.txt"], cwd=workspace_b, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "B commit"], cwd=workspace_b, check=True, capture_output=True)
        
        # 断言互不波及: A 的文件不在 B, B 的文件不在 A
        assert (workspace_a / "file-a.txt").exists()
        assert not (workspace_a / "file-b.txt").exists(), "B's file leaked to A - worktree isolation broken"
        
        assert (workspace_b / "file-b.txt").exists()
        assert not (workspace_b / "file-a.txt").exists(), "A's file leaked to B - worktree isolation broken"
        
        # 清理 worktree
        subprocess.run(["git", "worktree", "remove", str(workspace_a)], cwd=main_repo, check=True, capture_output=True)
        subprocess.run(["git", "worktree", "remove", str(workspace_b)], cwd=main_repo, check=True, capture_output=True)


def test_stash_danger_simulation():
    """演示 git stash 在共用工作树场景的危险性。
    
    模拟场景: 如果两个工位共用一棵工作树, 一个工位的 stash 会影响全局,
    导致另一个工位的未提交修改被误藏或丢失。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        
        # 创建共用仓库 (反面教材)
        shared_repo = base / "shared-repo"
        shared_repo.mkdir()
        subprocess.run(["git", "init"], cwd=shared_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=shared_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=shared_repo, check=True, capture_output=True)
        
        # 初始提交
        (shared_repo / "README.md").write_text("shared")
        subprocess.run(["git", "add", "README.md"], cwd=shared_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=shared_repo, check=True, capture_output=True)
        
        # 模拟工位A的工作
        workspace_a_file = shared_repo / "workspace-a" / "file.txt"
        workspace_a_file.parent.mkdir(parents=True, exist_ok=True)
        workspace_a_file.write_text("A's work in progress")
        
        # 模拟工位B的工作
        workspace_b_file = shared_repo / "workspace-b" / "file.txt"
        workspace_b_file.parent.mkdir(parents=True, exist_ok=True)
        workspace_b_file.write_text("B's work in progress")
        
        # 如果工位A执行 git stash (假设为了切换分支), 会把所有未提交修改都藏起来
        subprocess.run(["git", "add", "-A"], cwd=shared_repo, check=True, capture_output=True)
        subprocess.run(["git", "stash"], cwd=shared_repo, check=True, capture_output=True)
        
        # 灾难: 工位B的文件也被 stash 了 (连坐)
        assert not workspace_a_file.exists(), "A's file should be stashed"
        assert not workspace_b_file.exists(), "B's file should NOT be stashed, but it is - this is the danger!"
        
        # 恢复
        subprocess.run(["git", "stash", "pop"], cwd=shared_repo, check=True, capture_output=True)
        
        # 这个测试演示了为什么需要 worktree 隔离


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
