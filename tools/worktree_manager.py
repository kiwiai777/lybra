"""AIPOS-R5A: Worktree 生命周期管理(列表/删除/合并/孤儿盘点)。

设计权威: DESIGN v2 §7 R5
AIPOS-F88 件①: 建树(落点/分支/git worktree add)唯一实现在 tools/aipos_cli/next_resolver.py
(card_worktree_location / card_branch_name / _ensure_worktree), 本模块建树入口仅委托。

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
    """Git worktree 生命周期管理(列表/删除/合并/孤儿盘点)。

    AIPOS-F88 件①: 建树不再是本类的独立实现——落点/分支/建树全部委托 next_resolver 单源
    (card_worktree_location = workspace_config.resolve_card_repo + _resolve_worktree_root;
    card_branch_name = N5 branch_pattern 声明; _ensure_worktree = 唯一建树实现)。
    原独立实现(读治理根 .lybra/config.json、缺则把治理根当产品仓、路径 task_id 小写、分支写死、
    路径子串判治理仓)已退役; 门认领路径不再经本类建树。
    worktree 状态以 `git worktree list` 为唯一真相,禁第二份登记。
    """

    def __init__(self, code_repo: Path, worktree_root: Path | None = None, *, workspace_root: Path | None = None):
        """Initialize worktree manager.

        Args:
            code_repo: 产品仓根(git 仓)
            worktree_root: 工作树根; 缺省读声明(next_resolver._resolve_worktree_root: config.schema worktree_root,
                           治理根 .lybra/config.json 可覆盖)
            workspace_root: 治理根(卡/项目声明所在); 缺省 = code_repo(单根靶场)
        """
        from tools.aipos_cli.next_resolver import _resolve_worktree_root

        self.code_repo = Path(code_repo).resolve()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else self.code_repo
        if worktree_root:
            self.worktree_root = Path(worktree_root).resolve()
        else:
            self.worktree_root = _resolve_worktree_root(self.workspace_root, self.code_repo).resolve()

    @classmethod
    def from_workspace_config(cls, workspace_root: Path) -> WorktreeManager:
        """按项目声明构造: 产品仓 = workspace_config.resolve_card_repo(项目级缺省仓: repos.default → code_repo → 单根靶场),
        工作树根 = next_resolver._resolve_worktree_root(声明)。解析不到 = CardRepoUnresolved(fail-closed, 不猜路径)。"""
        from tools.aipos_cli.workspace_config import resolve_card_repo

        root = Path(workspace_root).resolve()
        return cls(resolve_card_repo(root, {}), workspace_root=root)

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
        """卡工作树路径 = next_resolver.card_worktree_location(唯一推导, 与门认领 / my-tasks 同一函数)。"""
        from tools.aipos_cli.next_resolver import card_worktree_location

        return card_worktree_location(self.workspace_root, task_id)[1].resolve()

    def branch_name_for_task(self, task_id: str) -> str:
        """卡分支名 = next_resolver.card_branch_name(N5 branch_pattern 声明)。"""
        from tools.aipos_cli.next_resolver import card_branch_name

        return card_branch_name(task_id)

    def create_worktree(
        self,
        task_id: str,
        base_branch: str = 'main',
        force: bool = False
    ) -> tuple[Path, str]:
        """建卡工作树——委托唯一建树实现 next_resolver._ensure_worktree(已存在即复用)。

        base_branch/force 仅保留签名兼容: 唯一实现从 main 起分支、不强制; 传非缺省值 = ValueError(不静默忽略)。

        Returns:
            Tuple of (worktree_path, branch_name)

        Raises:
            RuntimeError: 建树失败(拒因原文)
        """
        if base_branch != 'main' or force:
            raise ValueError(
                "WorktreeManager.create_worktree 已委托 next_resolver._ensure_worktree(从 main 起分支、不强制), "
                f"不支持 base_branch={base_branch!r} / force={force!r}"
            )
        from tools.aipos_cli.next_resolver import _ensure_worktree

        built = _ensure_worktree(self.workspace_root, task_id)
        if not built.get("ok"):
            raise RuntimeError(str(built.get("message") or "worktree 建立失败"))
        return Path(built["worktree_path"]).resolve(), str(built["branch"])

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
