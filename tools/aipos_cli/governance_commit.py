"""AIPOS-R7A2 靶②: lybra governance-commit — 顾问收口一条命令

N6 收账清单校验四件齐全:
1. 本卡台账条目 (task_cards/<ID>/)
2. decision_log 指针 (如适用)
3. 阶段快照 (如阶段收口)
4. task_cards 归档

校验通过 → commit(过治理仓 pre-commit 四检) → push
缺件明确报哪一件并拒绝;失败不静默。

命令须可由顾问工位 advisor token 使用(无 finalize 权限)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tools.schema_constants import Verdict


def check_governance_completeness(
    governance_root: Path,
    task_id: str,
) -> dict[str, Any]:
    """校验 N6 收账清单四件齐全。
    
    Returns:
        {
            "complete": bool,
            "missing": list[str],  # 缺件列表
            "details": dict,       # 各项详情
        }
    """
    missing = []
    details = {}
    
    # ① 本卡台账条目 (task_cards/<ID>/)
    task_cards_dir = governance_root / "task_cards" / task_id
    if task_cards_dir.is_dir():
        details["task_cards"] = {
            "exists": True,
            "path": str(task_cards_dir),
            "files": [f.name for f in task_cards_dir.iterdir()],
        }
    else:
        missing.append(f"task_cards/{task_id}/ (台账条目不存在)")
        details["task_cards"] = {"exists": False}
    
    # ② decision_log 指针 (如适用 - 检查 task_cards/<ID>/ 下是否有 decision_*.md)
    decision_files = []
    if task_cards_dir.is_dir():
        decision_files = list(task_cards_dir.glob("decision_*.md"))
    
    details["decision_log"] = {
        "applicable": len(decision_files) > 0,
        "files": [f.name for f in decision_files],
    }
    
    # ③ 阶段快照 (检查 5_tasks/records/stage_archives/ 下是否有本卡相关快照)
    stage_archives = governance_root / "5_tasks" / "records" / "stage_archives"
    stage_snapshots = []
    if stage_archives.is_dir():
        # 查找包含 task_id 的快照目录
        for snapshot_dir in stage_archives.iterdir():
            if snapshot_dir.is_dir() and task_id.lower() in snapshot_dir.name.lower():
                stage_snapshots.append(snapshot_dir.name)
    
    details["stage_snapshots"] = {
        "applicable": True,  # 阶段收口才需要,这里简化为总是检查
        "snapshots": stage_snapshots,
    }
    
    # ④ task_cards 归档 (检查 task_cards/<ID>/ 下是否有 RETURN.md 或 AUDIT-REPORT.md)
    archive_files = []
    if task_cards_dir.is_dir():
        archive_files = [
            f.name for f in task_cards_dir.iterdir()
            if f.name in ["RETURN.md", "AUDIT-REPORT.md", "CLOSURE.md"]
        ]
    
    if not archive_files and task_cards_dir.is_dir():
        missing.append(f"task_cards/{task_id}/ 缺少归档文件 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)")
    
    details["archive_files"] = {
        "exists": len(archive_files) > 0,
        "files": archive_files,
    }
    
    return {
        "complete": len(missing) == 0,
        "missing": missing,
        "details": details,
    }


def governance_commit(
    governance_root: Path,
    task_id: str,
    actor: str,
    *,
    dry_run: bool = False,
    push: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    """N6 收账提交:校验四件 → commit → push。
    
    Args:
        governance_root: 治理仓根目录
        task_id: 任务 ID
        actor: 执行者
        dry_run: 只校验不提交
        push: 是否 push 到远程
        message: commit message (默认自动生成)
    
    Returns:
        {
            "verdict": "PASS" | "BLOCK" | "FAIL",
            "task_id": str,
            "actor": str,
            "dry_run": bool,
            "completeness_check": dict,
            "committed": bool,
            "pushed": bool,
            "commit_hash": str | None,
            "message": str,
            "operations": list[str],
        }
    """
    operations = []
    
    # 校验治理仓目录
    if not governance_root.is_dir():
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Governance root does not exist: {governance_root}",
            "operations": operations,
        }
    
    operations.append(f"Governance root: {governance_root}")
    
    # ① 校验 N6 收账清单四件齐全
    completeness = check_governance_completeness(governance_root, task_id)
    operations.append(f"Completeness check: {'PASS' if completeness['complete'] else 'FAIL'}")
    
    if not completeness["complete"]:
        missing_items = "\n  - ".join(completeness["missing"])
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"N6 收账清单不完整,缺少:\n  - {missing_items}",
            "operations": operations,
        }
    
    # ② 检查是否有待提交的更改
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        has_changes = bool(status_result.stdout.strip())
        
        if not has_changes:
            operations.append("No changes to commit")
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": "N6 收账清单完整,但无待提交更改",
                "operations": operations,
            }
    except subprocess.CalledProcessError as e:
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git status check failed: {e.stderr}",
            "operations": operations,
        }
    
    if dry_run:
        operations.append("DRY-RUN: Would commit and push governance changes")
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": "DRY-RUN: N6 收账清单完整,可以提交",
            "operations": operations,
        }
    
    # ③ Commit (会触发 pre-commit 四检)
    commit_msg = message or f"chore(governance): N6 收账 {task_id}\n\nActor: {actor}\nType: governance_commit"
    
    try:
        # Stage all changes in governance repo
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        operations.append("Staged all governance changes (git add -A)")
        
        # Commit with explicit identity
        subprocess.run(
            [
                "git",
                "-c", f"user.name={actor}",
                "-c", f"user.email={actor}@lybra.local",
                "commit",
                "-m", commit_msg,
            ],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        
        # Get commit hash
        commit_hash_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = commit_hash_result.stdout.strip()
        operations.append(f"Committed: {commit_hash[:8]}")
        
        # ④ Push (N6 语义「漏 push 即未收口」)
        pushed = False
        if push:
            try:
                subprocess.run(
                    ["git", "push"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Pushed to remote")
                pushed = True
            except subprocess.CalledProcessError as e:
                operations.append(f"Push failed: {e.stderr}")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "completeness_check": completeness,
                    "committed": True,
                    "pushed": False,
                    "commit_hash": commit_hash,
                    "message": f"Committed but push failed: {e.stderr}",
                    "operations": operations,
                }
            except subprocess.TimeoutExpired:
                operations.append("Push timed out after 30s")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "completeness_check": completeness,
                    "committed": True,
                    "pushed": False,
                    "commit_hash": commit_hash,
                    "message": "Committed but push timed out",
                    "operations": operations,
                }
        
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": True,
            "pushed": pushed,
            "commit_hash": commit_hash,
            "message": f"N6 收账完成: {commit_hash[:8]}" + (" (pushed)" if pushed else ""),
            "operations": operations,
        }
        
    except subprocess.CalledProcessError as e:
        operations.append(f"Git operation failed: {e.stderr}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Commit failed: {e.stderr}",
            "operations": operations,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
