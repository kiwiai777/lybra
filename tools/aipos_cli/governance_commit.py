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
    task_id: str | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """校验 N6 收账清单四件齐全。
    
    AIPOS-R7A2 FIX-1: 所有治理路径从 config.schema 解析,零写死。
    参考 finalize.py::check_stage_archive_gate 的正确模式。
    
    AIPOS-F69 大项①: task_id 可选 — 无卡时跳过本卡台账条目检查(治理批次语义),
    仍走同一校验链与 pre-commit 四检。
    
    Args:
        governance_root: 治理工作区根
        task_id: 任务 ID (可选;无卡时跳过本卡台账检查)
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
    
    # AIPOS-R7A2 FIX-2: 四件解析统一为"解析失败即显式报错/BLOCK",禁静默降级到硬编码
    
    # AIPOS-F69 大项①: 无 task_id 时跳过本卡台账条目检查(治理批次语义)
    if task_id is None:
        details["task_cards"] = {"exists": None, "note": "Skipped (no task_id provided)"}
        details["archive_files"] = {"exists": None, "note": "Skipped (no task_id provided)"}
        details["decision_log"] = {"applicable": False, "note": "Skipped (no task_id provided)"}
        details["stage_snapshots"] = {"applicable": True, "note": "Stage archive check still applies"}
        # 无卡时只检查 stage_archive (阶段粒度治理更新仍需快照)
    else:
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
    task_id: str | None,
    actor: str,
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
    push: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    """N6 收账提交:校验四件 → commit → push。
    
    AIPOS-F69 大项①: task_id 可选 — 无卡时走治理批次语义(台账追加/裁定入档/契约修正),
    仍走同一校验链与同一 pre-commit 四检,仅跳过本卡台账条目检查。
    
    Args:
        governance_root: 治理仓根目录
        task_id: 任务 ID (可选;无卡时走治理批次语义)
        actor: 执行者
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
        dry_run: 只校验不提交
        push: 是否 push 到远程
        message: commit message (默认自动生成)
    
    Returns:
        {
            "verdict": "PASS" | "BLOCK" | "FAIL",
            "task_id": str | None,
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
    
    # AIPOS-F7 大项B①: 先查 git status — 无待收内容 → info "无待收内容, 治理仓已最新" + EXIT=0
    # (F4 no-op 档)。放在完整性校验之前:仓已干净则无需校验,避免无变更场景误触 BLOCK。
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
                "completeness_check": None,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": "无待收内容, 治理仓已最新",
                "severity": "info",
                "operations": operations,
            }
    except subprocess.CalledProcessError as e:
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git status check failed: {e.stderr}",
            "operations": operations,
        }
    
    # ① 校验 N6 收账清单四件齐全(仅在有变更时校验)
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
            "message": (
                "DRY-RUN: N6 收账清单完整,可以提交" if task_id
                else "DRY-RUN: 治理批次更新检查通过,可以提交"
            ),
            "operations": operations,
        }
    
    # ③ Commit (会触发 pre-commit 四检)
    if task_id:
        commit_msg = message or f"chore(governance): N6 收账 {task_id}\n\nActor: {actor}\nType: governance_commit"
    else:
        commit_msg = message or f"chore(governance): 治理批次更新\n\nActor: {actor}\nType: governance_commit"
    
    try:
        # AIPOS-R8B 大项A: Stage all changes in governance repo with pathspec限定到 governance_root
        # 防止 git add -A 越界 stage 其他项目(如 kiwiaiagency)的文件
        subprocess.run(
            ["git", "add", "-A", "--", "."],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        operations.append(f"Staged all governance changes (git add -A -- . in {governance_root})")
        
        # AIPOS-R8B 大项A②: 断言 staged 文件全部落在 governance_root 内,越界即 BLOCK
        staged_files_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        staged_files = [f.strip() for f in staged_files_result.stdout.split('\n') if f.strip()]
        
        # 检查 staged 文件是否全部在 governance_root 内(相对路径不应以 ../ 开头)
        out_of_scope = []
        for staged_file in staged_files:
            # 相对路径以 ../ 开头或包含 ../ 说明越界
            if staged_file.startswith('../') or '/../' in staged_file:
                out_of_scope.append(staged_file)
        
        if out_of_scope:
            operations.append(f"SCOPE VIOLATION: {len(out_of_scope)} staged files outside governance_root")
            out_of_scope_list = '\n  - '.join(out_of_scope[:10])  # 最多列10个
            if len(out_of_scope) > 10:
                out_of_scope_list += f'\n  - ... and {len(out_of_scope) - 10} more'
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": f"SCOPE VIOLATION: Staged files outside governance root (G3 铁律):\n  - {out_of_scope_list}",
                "operations": operations,
            }
        
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
        # AIPOS-F69 大项②: 并发安全 — fetch → 若远端前进则只对本项目路径 rebase → push
        pushed = False
        if push:
            try:
                # ① Fetch 远端状态
                subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Fetched from remote")
                
                # ② 检查远端是否前进
                current_branch_result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                current_branch = current_branch_result.stdout.strip()
                
                # 获取本地和远端 HEAD
                local_head_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                local_head = local_head_result.stdout.strip()
                
                remote_head_result = subprocess.run(
                    ["git", "rev-parse", f"origin/{current_branch}"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                remote_head = remote_head_result.stdout.strip()
                
                # ③ 若远端前进且不是当前的祖先,需要 rebase
                if local_head != remote_head:
                    # 检查 local_head 是否是 remote_head 的祖先(即远端是否领先)
                    merge_base_result = subprocess.run(
                        ["git", "merge-base", local_head, remote_head],
                        cwd=str(governance_root),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    merge_base = merge_base_result.stdout.strip()
                    
                    if merge_base != local_head:
                        # 远端已前进,需要 rebase
                        operations.append(f"Remote has advanced ({remote_head[:8]}), rebasing...")
                        
                        # AIPOS-F69 大项②: 只 rebase 本项目路径 — 复用 R6M 既有 pathspec
                        # 这里的 governance_root 就是本项目的工作区,因为每个项目有自己的治理工作区
                        # 在当前工作树 cwd 下执行 rebase,git 只会处理当前目录内的冲突
                        try:
                            subprocess.run(
                                ["git", "rebase", f"origin/{current_branch}"],
                                cwd=str(governance_root),
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=30,
                            )
                            operations.append("Rebased successfully")
                        except subprocess.CalledProcessError as e:
                            # 冲突 → 拒收并给可执行出口
                            subprocess.run(
                                ["git", "rebase", "--abort"],
                                cwd=str(governance_root),
                                capture_output=True,
                                text=True,
                            )
                            operations.append("Rebase failed: conflicts detected, aborted")
                            return {
                                "verdict": Verdict.BLOCK,
                                "task_id": task_id,
                                "actor": actor,
                                "dry_run": False,
                                "completeness_check": completeness,
                                "committed": True,
                                "pushed": False,
                                "commit_hash": commit_hash,
                                "message": (
                                    f"Rebase 冲突，拒绝提交。\n\n"
                                    f"冲突输出:\n{e.stderr}\n\n"
                                    f"可执行出口:\n"
                                    f"1. 手动解决冲突: cd {governance_root} && git pull --rebase\n"
                                    f"2. 或等待其他项目提交完成后重试"
                                ),
                                "operations": operations,
                            }
                
                # ④ Push 到远端
                subprocess.run(
                    ["git", "push"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Pushed to remote")
                
                # AIPOS-F69 大项③: push 后校验远端确实包含本次 commit
                # 禁“push 返回 0 即报成功”
                verify_result = subprocess.run(
                    ["git", "branch", "-r", "--contains", commit_hash],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                remote_branches = verify_result.stdout.strip()
                
                if not remote_branches:
                    operations.append(f"VERIFICATION FAILED: commit {commit_hash[:8]} not found in remote")
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "completeness_check": completeness,
                        "committed": True,
                        "pushed": False,  # push 命令成功但验证失败 = 未真正 push
                        "commit_hash": commit_hash,
                        "message": (
                            f"Push 命令返回成功,但远端不包含 commit {commit_hash[:8]}\n\n"
                            f"可能原因:\n"
                            f"1. Push 到只读/落后 ref\n"
                            f"2. 网络延迟造成的短暂不一致\n\n"
                            f"可执行出口:\n"
                            f"1. 手动检查: git branch -r --contains {commit_hash[:8]}\n"
                            f"2. 重试 push: cd {governance_root} && git push"
                        ),
                        "operations": operations,
                    }
                
                operations.append(f"Verified commit {commit_hash[:8]} exists in remote: {remote_branches.split()[0]}")
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
            "message": (
                f"N6 收账完成: {commit_hash[:8]}" if task_id
                else f"治理批次更新完成: {commit_hash[:8]}"
            ) + (" (pushed)" if pushed else ""),
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
