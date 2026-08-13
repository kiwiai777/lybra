"""AIPOS-R5B: 治理仓 per-project worktree + merge 收敛。

设计权威: LOOP-REDESIGN v2 §7 R5
收编: FND-24 (多项目共用一工作树/分支 → 推送相撞)

核心机制:
① 复用 R5A worktree_manager.py (一机制一实现)
② gov/<project> = collection lane (收集车道) 非特性分叉
③ 每项目专属 worktree + 常驻分支
④ merge 收敛: 一条命令合所有 gov/* 回 main 并 push
⑤ 越界校验: 只许碰 2_projects/<project>/** + 公共白名单

红线:
- 分支语义在 schema/config (governance_worktree.branch_semantics)
- 路径白名单在 schema/config (governance_worktree.path_constraints)
- 复用 R5A worktree_manager.py 模块 (禁第二份实现)
- origin main = 唯一真相 (禁第二 origin/镜像)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tools.worktree_manager import WorktreeManager, WorktreeInfo
from tools.schema_loader import load_schema


class GovernanceWorktreeManager:
    """治理仓 per-project worktree 管理器。
    
    复用 R5A WorktreeManager 基础设施,扩展 gov/* 分支语义。
    """
    
    def __init__(self, governance_root: Path):
        """Initialize governance worktree manager.
        
        Args:
            governance_root: Governance repository root path
        """
        self.governance_root = Path(governance_root).resolve()
        
        # Load schema configuration
        schema = load_schema('config')
        gov_config = schema.get('governance_worktree', {})
        
        # 复用 R5A WorktreeManager
        worktree_root = self.governance_root / '.worktrees' / 'gov'
        self.wt_manager = WorktreeManager(
            code_repo=self.governance_root,
            worktree_root=worktree_root
        )
        
        # Load path constraints from schema
        path_config = gov_config.get('path_constraints', {})
        self.common_whitelist = path_config.get('common_paths_whitelist', [])
    
    def gov_branch_name(self, project: str) -> str:
        """Get gov branch name for a project.
        
        Args:
            project: Project identifier
            
        Returns:
            Branch name (e.g., "gov/lybra")
        """
        return f"gov/{project}"
    
    def gov_worktree_path(self, project: str) -> Path:
        """Get worktree path for a gov branch.
        
        Args:
            project: Project identifier
            
        Returns:
            Path to worktree directory
        """
        return self.wt_manager.worktree_root / project
    
    def create_gov_worktree(
        self,
        project: str,
        base_branch: str = 'main',
        force: bool = False
    ) -> tuple[Path, str]:
        """Create gov worktree for a project.
        
        Args:
            project: Project identifier
            base_branch: Base branch to branch from
            force: Force creation
            
        Returns:
            Tuple of (worktree_path, branch_name)
        """
        branch_name = self.gov_branch_name(project)
        worktree_path = self.gov_worktree_path(project)
        
        # Check if worktree already exists
        existing = self.wt_manager.get_worktree_for_branch(branch_name)
        if existing and not force:
            return existing.path, branch_name
        
        # Check if branch exists
        branch_exists = self.wt_manager._branch_exists(branch_name)
        
        cmd = ['git', 'worktree', 'add']
        
        if branch_exists:
            cmd.extend([str(worktree_path), branch_name])
        else:
            cmd.extend(['-b', branch_name, str(worktree_path), base_branch])
        
        if force:
            cmd.append('--force')
        
        try:
            subprocess.run(
                cmd,
                cwd=self.governance_root,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git worktree add failed: {exc.stderr}\nCommand: {' '.join(cmd)}"
            ) from exc
        
        return worktree_path, branch_name
    
    def list_gov_branches(self) -> list[str]:
        """List all gov/* branches.
        
        Returns:
            List of gov branch names (e.g., ["gov/lybra", "gov/kiwiai"])
        """
        try:
            result = subprocess.run(
                ['git', 'branch', '--list', 'gov/*'],
                cwd=self.governance_root,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError:
            return []
        
        branches = []
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith('* '):
                line = line[2:]
            if line.startswith('gov/'):
                branches.append(line)
        
        return branches
    
    def validate_commit_paths(
        self,
        project: str,
        commit_sha: str
    ) -> dict[str, Any]:
        """Validate that a commit only touches allowed paths.
        
        Args:
            project: Project identifier
            commit_sha: Commit SHA to validate
            
        Returns:
            Dict with validation results {
                'valid': bool,
                'violations': list[str],  # Files outside allowed paths
                'allowed_files': list[str]
            }
        """
        # Get files changed in commit
        try:
            result = subprocess.run(
                ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit_sha],
                cwd=self.governance_root,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to get commit files: {exc.stderr}") from exc
        
        changed_files = [f.strip() for f in result.stdout.split('\n') if f.strip()]
        
        # Check each file against allowed paths
        project_prefix = f"2_projects/{project}/"
        violations = []
        allowed_files = []
        
        for file_path in changed_files:
            # Check if in project directory
            if file_path.startswith(project_prefix):
                allowed_files.append(file_path)
                continue
            
            # Check if in common whitelist
            allowed = False
            for pattern in self.common_whitelist:
                if pattern.endswith('/**'):
                    # Directory glob
                    prefix = pattern[:-3]
                    if file_path.startswith(prefix):
                        allowed = True
                        break
                elif pattern.endswith('**'):
                    # Suffix glob
                    prefix = pattern[:-2]
                    if file_path.startswith(prefix):
                        allowed = True
                        break
                else:
                    # Exact match
                    if file_path == pattern:
                        allowed = True
                        break
            
            if allowed:
                allowed_files.append(file_path)
            else:
                violations.append(file_path)
        
        return {
            'valid': len(violations) == 0,
            'violations': violations,
            'allowed_files': allowed_files,
            'total_files': len(changed_files)
        }
    
    def validate_branch_commits(
        self,
        project: str,
        base_branch: str = 'main'
    ) -> dict[str, Any]:
        """Validate all commits in gov/<project> branch against path constraints.
        
        Args:
            project: Project identifier
            base_branch: Base branch to compare against
            
        Returns:
            Dict with validation results
        """
        branch_name = self.gov_branch_name(project)
        
        # Get commits in branch not in base
        try:
            result = subprocess.run(
                ['git', 'log', '--format=%H', f'{base_branch}..{branch_name}'],
                cwd=self.governance_root,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to get commit list: {exc.stderr}") from exc
        
        commit_shas = [c.strip() for c in result.stdout.split('\n') if c.strip()]
        
        all_violations = []
        commit_results = []
        
        for commit_sha in commit_shas:
            validation = self.validate_commit_paths(project, commit_sha)
            commit_results.append({
                'commit': commit_sha[:8],
                'valid': validation['valid'],
                'violations': validation['violations']
            })
            if not validation['valid']:
                all_violations.extend(validation['violations'])
        
        return {
            'branch': branch_name,
            'valid': len(all_violations) == 0,
            'violations': list(set(all_violations)),  # Dedupe
            'commit_count': len(commit_shas),
            'commit_results': commit_results
        }
    
    def merge_gov_branch_to_main(
        self,
        project: str,
        main_branch: str = 'main',
        validate: bool = True,
        no_ff: bool = True
    ) -> dict[str, Any]:
        """Merge gov/<project> branch to main.
        
        Args:
            project: Project identifier
            main_branch: Target main branch
            validate: Run path validation before merge
            no_ff: Use --no-ff merge (preserve history)
            
        Returns:
            Dict with merge results
            
        Raises:
            RuntimeError: If validation fails or merge fails
        """
        branch_name = self.gov_branch_name(project)
        
        # Validate commits if requested
        if validate:
            validation = self.validate_branch_commits(project, main_branch)
            if not validation['valid']:
                raise RuntimeError(
                    f"Path validation failed for {branch_name}:\n"
                    f"Violations (越界文件):\n" +
                    '\n'.join(f"  - {v}" for v in validation['violations'])
                )
        
        # Ensure we're on main branch
        try:
            subprocess.run(
                ['git', 'checkout', main_branch],
                cwd=self.governance_root,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to checkout {main_branch}: {exc.stderr}") from exc
        
        # Merge
        cmd = ['git', 'merge']
        if no_ff:
            cmd.append('--no-ff')
        cmd.extend(['-m', f"Merge {branch_name} (gov collection lane convergence)", branch_name])
        
        try:
            result = subprocess.run(
                cmd,
                cwd=self.governance_root,
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as exc:
            # Check for conflicts
            if 'CONFLICT' in exc.stdout or 'CONFLICT' in exc.stderr:
                # Get conflicting files
                conflict_result = subprocess.run(
                    ['git', 'diff', '--name-only', '--diff-filter=U'],
                    cwd=self.governance_root,
                    capture_output=True,
                    text=True,
                    check=False
                )
                conflicts = [f.strip() for f in conflict_result.stdout.split('\n') if f.strip()]
                
                # Abort merge
                subprocess.run(
                    ['git', 'merge', '--abort'],
                    cwd=self.governance_root,
                    capture_output=True,
                    check=False
                )
                
                raise RuntimeError(
                    f"Merge conflict detected (目录隔离应零冲突,真冲突=越界改他人目录):\n" +
                    '\n'.join(f"  - {c}" for c in conflicts)
                ) from exc
            else:
                raise RuntimeError(f"Merge failed: {exc.stderr}") from exc
        
        return {
            'branch': branch_name,
            'target': main_branch,
            'merged': True,
            'merge_output': result.stdout
        }
    
    def merge_all_gov_branches(
        self,
        main_branch: str = 'main',
        validate: bool = True,
        push: bool = True,
        remote: str = 'origin'
    ) -> dict[str, Any]:
        """Merge all gov/* branches to main and push.
        
        AIPOS-R5B merge 收敛动词 — 一条命令合所有 gov/<project> 回 main 并 push。
        
        Args:
            main_branch: Target main branch
            validate: Run path validation before merge
            push: Push to remote after merge
            remote: Remote name
            
        Returns:
            Dict with merge results for all branches
        """
        gov_branches = self.list_gov_branches()
        
        if not gov_branches:
            return {
                'merged_branches': [],
                'total': 0,
                'pushed': False,
                'message': 'No gov/* branches to merge'
            }
        
        # Extract project names
        projects = [b.replace('gov/', '') for b in gov_branches]
        
        merge_results = []
        failed = []
        
        for project in projects:
            try:
                result = self.merge_gov_branch_to_main(
                    project=project,
                    main_branch=main_branch,
                    validate=validate
                )
                merge_results.append({
                    'project': project,
                    'branch': result['branch'],
                    'success': True
                })
            except RuntimeError as exc:
                failed.append({
                    'project': project,
                    'branch': self.gov_branch_name(project),
                    'error': str(exc)
                })
        
        if failed:
            # Rollback: some merges succeeded but later ones failed
            # User should investigate and fix manually
            raise RuntimeError(
                f"Merge failed for {len(failed)} branch(es):\n" +
                '\n'.join(f"  - {f['branch']}: {f['error']}" for f in failed)
            )
        
        # Push if requested
        pushed = False
        if push:
            try:
                subprocess.run(
                    ['git', 'push', remote, main_branch],
                    cwd=self.governance_root,
                    capture_output=True,
                    text=True,
                    check=True
                )
                pushed = True
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"Push failed: {exc.stderr}") from exc
        
        return {
            'merged_branches': merge_results,
            'total': len(merge_results),
            'pushed': pushed,
            'remote': remote if pushed else None,
            'target_branch': main_branch
        }
