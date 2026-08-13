"""AIPOS-R5A: Worktree 生命周期管理 — 一个模块一份实现。

设计权威: LOOP-REDESIGN v2 §7 R5

每张 code 卡独立 worktree 执行:
- claim 时为该卡建/用专属 git worktree
- 绑定写进 claim/session 记录与 LoopContext.worktree
- 执行/commit 在 worktree
- finalize 合回 main + 删分支 + 删 worktree (分叉活不过一张卡)
- 异常路: FAIL=继续用; withdraw/block=清理留档

红线(Owner 2026-08-12):
① 一机制一实现 — worktree 管理只此一份
② worktree 根路径从 config.schema 读(禁写死)
③ 新字段已进 card.schema (active_worktree_path/branch)
④ worktree 状态以 git worktree list 为唯一真相(禁第二份登记)
⑤ worktree 目录=生成物不入库
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class WorktreeInfo:
    """Worktree information from git worktree list."""
    path: Path
    branch: str | None
    commit: str
    is_bare: bool = False
    is_detached: bool = False
    
    @classmethod
    def from_porcelain_line(cls, line: str) -> WorktreeInfo | None:
        """Parse git worktree list --porcelain output.
        
        Format:
            worktree /path/to/worktree
            HEAD commit_sha
            branch refs/heads/branch_name
            detached (optional)
            bare (optional)
        """
        # This is called per-worktree block, not per line
        lines = line.strip().split('\n')
        if not lines or not lines[0].startswith('worktree '):
            return None
        
        path_str = lines[0].replace('worktree ', '', 1)
        commit = None
        branch = None
        is_bare = False
        is_detached = False
        
        for ln in lines[1:]:
            if ln.startswith('HEAD '):
                commit = ln.replace('HEAD ', '', 1)
            elif ln.startswith('branch '):
                branch_ref = ln.replace('branch ', '', 1)
                # Extract branch name from refs/heads/...
                if branch_ref.startswith('refs/heads/'):
                    branch = branch_ref.replace('refs/heads/', '', 1)
                else:
                    branch = branch_ref
            elif ln == 'detached':
                is_detached = True
            elif ln == 'bare':
                is_bare = True
        
        return cls(
            path=Path(path_str),
            branch=branch,
            commit=commit or '',
            is_bare=is_bare,
            is_detached=is_detached
        )


class WorktreeManager:
    """Git worktree 生命周期管理 — 唯一实现。
    
    worktree 状态以 `git worktree list` 为唯一真相,禁第二份登记。
    """
    
    def __init__(self, code_repo: Path, worktree_root: Path | None = None):
        """Initialize worktree manager.
        
        Args:
            code_repo: Code repository root path
            worktree_root: Root directory for worktrees (from config.schema)
                          If None, defaults to {code_repo}/.worktrees
        """
        self.code_repo = Path(code_repo).resolve()
        
        # AIPOS-R6A 靶子⑦: worktree供给bug根治 — 硬断言 code_repo ≠ 治理仓
        # 全量实证：R5A起每张卡都被错建了治理仓树（已全拆）
        # 治理仓特征：路径包含 ai-project-os（简单可靠的识别方法）
        repo_path_str = str(self.code_repo)
        if 'ai-project-os' in repo_path_str:
            raise ValueError(
                f"BLOCKED: WorktreeManager cannot operate on governance repo ({self.code_repo}). "
                f"Worktrees must only be created in product repos (e.g., ~/projects/lybra). "
                f"治理仓从此无代码可改，化石树已被铲除。 (AIPOS-R6A 靶子⑦)"
            )
        
        if worktree_root:
            self.worktree_root = Path(worktree_root).resolve()
        else:
            # Default from config.schema
            self.worktree_root = self.code_repo / '.worktrees'
        
        # Ensure worktree root exists
        self.worktree_root.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def from_workspace_config(cls, workspace_root: Path) -> WorktreeManager:
        """Create from workspace config (读 config.schema)。
        
        Args:
            workspace_root: Workspace root path
            
        Returns:
            WorktreeManager instance
            
        Raises:
            ValueError: If code_repo not configured
        """
        # 读取 .lybra/config.json (如果存在)
        config_path = workspace_root / ".lybra" / "config.json"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                code_repo = config.get('code_repo')
                worktree_root = config.get('worktree_root')
            except (json.JSONDecodeError, OSError):
                config = {}
                code_repo = None
                worktree_root = None
        else:
            config = {}
            code_repo = None
            worktree_root = None
        
        # 默认 code_repo = workspace_root
        if not code_repo:
            code_repo = str(workspace_root)
        
        code_repo_path = Path(code_repo)
        
        if worktree_root:
            worktree_root_path = Path(worktree_root)
        else:
            # Default from schema
            worktree_root_path = code_repo_path / '.worktrees'
        
        return cls(code_repo_path, worktree_root_path)
    
    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees (git worktree list --porcelain).
        
        Returns:
            List of WorktreeInfo objects
        """
        try:
            result = subprocess.run(
                ['git', 'worktree', 'list', '--porcelain'],
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"git worktree list failed: {exc.stderr}") from exc
        
        # Parse porcelain output (blocks separated by blank lines)
        worktrees = []
        current_block = []
        
        for line in result.stdout.split('\n'):
            if line.strip():
                current_block.append(line)
            elif current_block:
                # End of block
                block_text = '\n'.join(current_block)
                wt_info = WorktreeInfo.from_porcelain_line(block_text)
                if wt_info:
                    worktrees.append(wt_info)
                current_block = []
        
        # Handle last block if no trailing newline
        if current_block:
            block_text = '\n'.join(current_block)
            wt_info = WorktreeInfo.from_porcelain_line(block_text)
            if wt_info:
                worktrees.append(wt_info)
        
        return worktrees
    
    def get_worktree_for_branch(self, branch: str) -> WorktreeInfo | None:
        """Get worktree info for a given branch.
        
        Args:
            branch: Branch name (e.g., "card/AIPOS-R5A")
            
        Returns:
            WorktreeInfo if found, None otherwise
        """
        worktrees = self.list_worktrees()
        for wt in worktrees:
            if wt.branch == branch:
                return wt
        return None
    
    def worktree_path_for_task(self, task_id: str) -> Path:
        """Get worktree path for a task.
        
        Args:
            task_id: Task identifier (e.g., "AIPOS-R5A")
            
        Returns:
            Path to worktree directory
        """
        # Normalize task_id for filesystem (lowercase, safe chars)
        safe_task_id = task_id.lower().replace('_', '-')
        return self.worktree_root / safe_task_id
    
    def branch_name_for_task(self, task_id: str) -> str:
        """Get branch name for a task.
        
        Args:
            task_id: Task identifier (e.g., "AIPOS-R5A")
            
        Returns:
            Branch name (e.g., "card/AIPOS-R5A")
        """
        return f"card/{task_id}"
    
    def create_worktree(
        self,
        task_id: str,
        base_branch: str = 'main',
        force: bool = False
    ) -> tuple[Path, str]:
        """Create worktree for a task.
        
        Args:
            task_id: Task identifier
            base_branch: Base branch to branch from (default: main)
            force: Force creation even if worktree exists
            
        Returns:
            Tuple of (worktree_path, branch_name)
            
        Raises:
            RuntimeError: If git worktree add fails
        """
        worktree_path = self.worktree_path_for_task(task_id)
        branch_name = self.branch_name_for_task(task_id)
        
        # Check if worktree already exists
        existing = self.get_worktree_for_branch(branch_name)
        if existing and not force:
            # Worktree already exists, return it
            return existing.path, branch_name
        
        # Check if branch exists
        branch_exists = self._branch_exists(branch_name)
        
        cmd = ['git', 'worktree', 'add']
        
        if branch_exists:
            # Use existing branch
            cmd.extend([str(worktree_path), branch_name])
        else:
            # Create new branch from base
            cmd.extend(['-b', branch_name, str(worktree_path), base_branch])
        
        if force:
            cmd.append('--force')
        
        try:
            subprocess.run(
                cmd,
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git worktree add failed: {exc.stderr}\nCommand: {' '.join(cmd)}"
            ) from exc
        
        return worktree_path, branch_name
    
    def remove_worktree(
        self,
        task_id: str | None = None,
        worktree_path: Path | None = None,
        force: bool = False
    ) -> bool:
        """Remove worktree.
        
        Args:
            task_id: Task identifier (will derive path)
            worktree_path: Direct worktree path
            force: Force removal even with uncommitted changes
            
        Returns:
            True if removed, False if not found
            
        Raises:
            RuntimeError: If git worktree remove fails
            ValueError: If neither task_id nor worktree_path provided
        """
        if task_id:
            path = self.worktree_path_for_task(task_id)
        elif worktree_path:
            path = worktree_path
        else:
            raise ValueError("Must provide either task_id or worktree_path")
        
        # Check if worktree exists
        worktrees = self.list_worktrees()
        exists = any(wt.path == path for wt in worktrees)
        
        if not exists:
            return False
        
        cmd = ['git', 'worktree', 'remove', str(path)]
        if force:
            cmd.append('--force')
        
        try:
            subprocess.run(
                cmd,
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git worktree remove failed: {exc.stderr}\nCommand: {' '.join(cmd)}"
            ) from exc
        
        return True
    
    def delete_branch(self, branch_name: str, force: bool = False) -> bool:
        """Delete a branch.
        
        Args:
            branch_name: Branch name to delete
            force: Force delete (use -D instead of -d)
            
        Returns:
            True if deleted, False if branch doesn't exist
            
        Raises:
            RuntimeError: If git branch delete fails
        """
        if not self._branch_exists(branch_name):
            return False
        
        flag = '-D' if force else '-d'
        
        try:
            subprocess.run(
                ['git', 'branch', flag, branch_name],
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git branch delete failed: {exc.stderr}"
            ) from exc
        
        return True
    
    def cleanup_task(
        self,
        task_id: str,
        remove_branch: bool = True,
        force: bool = False
    ) -> dict[str, Any]:
        """Clean up worktree and branch for a task.
        
        Args:
            task_id: Task identifier
            remove_branch: Also delete the branch
            force: Force removal
            
        Returns:
            Dict with cleanup results
        """
        branch_name = self.branch_name_for_task(task_id)
        
        worktree_removed = False
        branch_deleted = False
        
        # Remove worktree
        try:
            worktree_removed = self.remove_worktree(task_id=task_id, force=force)
        except RuntimeError as exc:
            # Worktree removal failed, but continue to branch cleanup
            pass
        
        # Delete branch
        if remove_branch:
            try:
                branch_deleted = self.delete_branch(branch_name, force=force)
            except RuntimeError as exc:
                pass
        
        return {
            'task_id': task_id,
            'branch_name': branch_name,
            'worktree_removed': worktree_removed,
            'branch_deleted': branch_deleted
        }
    
    def _branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists.
        
        Args:
            branch_name: Branch name
            
        Returns:
            True if branch exists
        """
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--verify', f'refs/heads/{branch_name}'],
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=False
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def merge_to_main(
        self,
        branch_name: str,
        strategy: str = 'squash',
        main_branch: str = 'main'
    ) -> dict[str, Any]:
        """Merge branch to main (finalize 收敛).
        
        Args:
            branch_name: Branch to merge
            strategy: Merge strategy ('ff', 'squash', 'merge')
            main_branch: Target main branch
            
        Returns:
            Dict with merge results
            
        Raises:
            RuntimeError: If merge fails
        """
        if not self._branch_exists(branch_name):
            raise RuntimeError(f"Branch {branch_name} does not exist")
        
        # Checkout main
        try:
            subprocess.run(
                ['git', 'checkout', main_branch],
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to checkout {main_branch}: {exc.stderr}") from exc
        
        # Merge
        if strategy == 'ff':
            cmd = ['git', 'merge', '--ff-only', branch_name]
        elif strategy == 'squash':
            cmd = ['git', 'merge', '--squash', branch_name]
        else:
            cmd = ['git', 'merge', branch_name]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=self.code_repo,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Merge failed: {exc.stderr}\nCommand: {' '.join(cmd)}"
            ) from exc
        
        # If squash, need to commit
        if strategy == 'squash':
            commit_msg = f"Merge {branch_name} (squash)"
            try:
                subprocess.run(
                    ['git', 'commit', '-m', commit_msg],
                    cwd=self.code_repo,
                    capture_output=True,
                    text=True,
                    check=True
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"Squash commit failed: {exc.stderr}") from exc
        
        return {
            'branch_name': branch_name,
            'target_branch': main_branch,
            'strategy': strategy,
            'merged': True
        }
    
    def list_orphan_worktrees(self) -> list[WorktreeInfo]:
        """List orphan worktrees (worktrees that exist but have no associated task).
        
        Returns:
            List of orphan WorktreeInfo objects
        """
        worktrees = self.list_worktrees()
        
        # Filter worktrees under worktree_root
        managed = []
        for wt in worktrees:
            try:
                # Check if worktree is under worktree_root
                wt.path.relative_to(self.worktree_root)
                managed.append(wt)
            except ValueError:
                # Not under worktree_root, skip
                continue
        
        return managed
