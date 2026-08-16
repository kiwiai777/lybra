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
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """校验 N6 收账清单四件齐全。
    
    AIPOS-R7A2 FIX-1: 所有治理路径从 config.schema 解析,零写死。
    参考 finalize.py::check_stage_archive_gate 的正确模式。
    
    Args:
        governance_root: 治理工作区根
        task_id: 任务 ID
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
    
    Returns:
        {
            "complete": bool,
            "missing": list[str],  # 缺件列表
            "details": dict,       # 各项详情
        }
    """
    from tools.schema_loader import resolve_governance_path
    
    missing = []
    details = {}
    
    # AIPOS-R7A2 FIX-2: 四件解析统一为“解析失败即显式报错/BLOCK”,禁静默降级到硬编码
    
    # ① 本卡台账条目 (task_cards/<ID>/) + ④ 归档文件检查
    # AIPOS-R7A2 FIX-2: task_cards 路径从 schema 解析,失败即 BLOCK
    try:
        task_cards_root = resolve_governance_path("task_cards", governance_root, repo_root)
        task_cards_dir = task_cards_root / task_id
        
        if task_cards_dir.is_dir():
            details["task_cards"] = {
                "exists": True,
                "path": str(task_cards_dir),
                "files": [f.name for f in task_cards_dir.iterdir()],
            }
            
            # ④ task_cards 归档 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)
            archive_files = [
                f.name for f in task_cards_dir.iterdir()
                if f.name in ["RETURN.md", "AUDIT-REPORT.md", "CLOSURE.md"]
            ]
            
            if not archive_files:
                missing.append(f"task_cards/{task_id}/ 缺少归档文件 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)")
            
            details["archive_files"] = {
                "exists": len(archive_files) > 0,
                "files": archive_files,
            }
        else:
            missing.append(f"task_cards/{task_id}/ (台账条目不存在)")
            details["task_cards"] = {"exists": False}
            details["archive_files"] = {"exists": False, "files": []}
    except Exception as exc:
        # AIPOS-R7A2 FIX-2: 移除 fallback,解析失败显式报错
        missing.append(f"task_cards/ (路径解析失败: {exc})")
        details["task_cards"] = {"exists": False, "error": str(exc)}
        details["archive_files"] = {"exists": False, "error": str(exc)}
    
    # ② decision_log 指针 (如适用)
    # AIPOS-R7A2 FIX-2: decision_log 路径从 schema 解析,失败显式报错
    try:
        decision_log_dir = resolve_governance_path("decision_log_dir", governance_root, repo_root)
        # 在 decision_log/ 下查找与本任务相关的条目 (按 YYYY-MM/YYYY-MM-DD-<slug>.md 结构)
        # 这里简化为检查整个目录树中包含 task_id 的 .md 文件
        decision_files = []
        if decision_log_dir.is_dir():
            for md_file in decision_log_dir.rglob("*.md"):
                if task_id.lower() in md_file.stem.lower():
                    decision_files.append(str(md_file.relative_to(decision_log_dir)))
        
        details["decision_log"] = {
            "applicable": len(decision_files) > 0,
            "files": decision_files,
            "path": str(decision_log_dir),
        }
    except Exception as exc:
        # AIPOS-R7A2 FIX-2: decision_log 解析失败也显式报错 (BLOCK)
        missing.append(f"decision_log_dir/ (路径解析失败: {exc})")
        details["decision_log"] = {
            "applicable": False,
            "error": str(exc),
        }
    
    # ③ 阶段快照 (stage_archive/)
    # AIPOS-R7A2 FIX-2: stage_archive 路径从 schema 解析,失败显式报错 (同 finalize.py 模式)
    try:
        stage_archive_dir = resolve_governance_path("stage_archive", governance_root, repo_root)
        
        stage_snapshots = []
        if stage_archive_dir.is_dir():
            # 阶段快照 = *.md 文件 (排除 README/index)
            for snapshot_file in stage_archive_dir.glob("*.md"):
                if snapshot_file.name.lower() not in {"readme.md", "index.md"}:
                    stage_snapshots.append(snapshot_file.name)
        
        details["stage_snapshots"] = {
            "applicable": True,
            "snapshots": stage_snapshots,
            "path": str(stage_archive_dir),
        }
        
        # 阶段快照为空时不强制 BLOCK (允许非阶段收口卡无快照)
        if not stage_snapshots and stage_archive_dir.is_dir():
            details["stage_snapshots"]["note"] = "Stage archive directory exists but no snapshots found (OK for non-stage-closure tasks)"
    
    except Exception as exc:
        # AIPOS-R7A2 FIX-2: stage_archive 解析失败显式报错 (BLOCK)
        missing.append(f"stage_archive/ (路径解析失败: {exc})")
        details["stage_snapshots"] = {
            "applicable": False,
            "error": str(exc),
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
    repo_root: Path | None = None,
    dry_run: bool = False,
    push: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    """N6 收账提交:校验四件 → commit → push。
    
    Args:
        governance_root: 治理仓根目录
        task_id: 任务 ID
        actor: 执行者
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
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
    completeness = check_governance_completeness(governance_root, task_id, repo_root)
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
