"""AIPOS-FND-2/FND-9 — finalize: git commit/push/deploy for PASS tasks.

AIPOS-FINALIZE-FIX-1 (2026-08-12): 三项红线修正:
  ① 剥离治理仓 git 操作 — finalize 的 git commit/push 只作用于产品仓 (workspace_root),
     绝不操作治理仓 (governance_root)。records/queue 文件由 gate 动词写入,治理仓 git
     归 N6 收账节点 (顾问职责),executor 无权推治理仓。
  ② deploy 失败 → finalize 整体 FAIL — deploy 子步失败 (显式或自动) 必须返回
     verdict=Verdict.FAIL + exit 非0,禁止吞错报成功。
  ③ lybra-deploy 路径从产品仓根解析 — repo_root / "tools" / "lybra-deploy",
     禁止 cwd 猜测,符合 config.schema 标准位置。

After audit verdict=PASS, finalize commits the changes to git and optionally pushes.
Enforces deployment integrity (current==HEAD) and only allows finalization of PASS tasks.

AIPOS-FND-9: Auto-deploy gate-side changes after commit to prevent "committed but not live" drift.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.schema_loader import get_enum_values
from tools.schema_constants import RecordType, Verdict

# FND-47: record_type 从 enums.schema 读取（单一源）
_RECORD_TYPE_ENUM_CACHE: list[str] | None = None

def _get_valid_record_types() -> list[str]:
    """Get all valid record_type values from enums.schema.json."""
    global _RECORD_TYPE_ENUM_CACHE
    if _RECORD_TYPE_ENUM_CACHE is None:
        _RECORD_TYPE_ENUM_CACHE = get_enum_values("record_type")
    return _RECORD_TYPE_ENUM_CACHE


def _git_rev_parse_head(repo_root: Path) -> str:
    """Get current git HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def _git_status_clean(repo_root: Path) -> bool:
    """Check if working tree is clean (no uncommitted changes)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return not result.stdout.strip()
    except subprocess.CalledProcessError:
        return False


def _git_local_origin_synced(repo_root: Path) -> bool:
    """Check if local HEAD is synced with origin (AIPOS-R6A 靶子③: push判据修正).
    
    Returns:
        True if local HEAD == origin/HEAD (or origin doesn't exist)
        False if local has unpushed commits
    
    Context:
        working tree clean ≠ already pushed. A clean tree with unpushed commits
        should trigger push, not skip as "nothing to do".
    """
    try:
        # Get current branch
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        branch = branch_result.stdout.strip()
        
        # Get local HEAD
        local_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        local_head = local_result.stdout.strip()
        
        # Try to get origin HEAD
        origin_result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        
        # If origin doesn't exist, consider synced (no remote to push to)
        if origin_result.returncode != 0:
            return True
        
        origin_head = origin_result.stdout.strip()
        return local_head == origin_head
        
    except subprocess.CalledProcessError:
        # If git commands fail, assume not synced (safe default)
        return False


def _read_deploy_current(repo_root: Path) -> dict[str, str | None]:
    """读 .deploy/current/VERSION 的 git_commit / deployment_provenance / authorization_ref。"""
    deploy_dir = repo_root / ".deploy"
    current_link = deploy_dir / "current"
    result: dict[str, str | None] = {"current_commit": None, "provenance": None, "authorization_ref": None}
    if not current_link.exists():
        return result
    version_file = current_link / "VERSION"
    if not version_file.exists():
        return result
    text = version_file.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("git_commit:"):
            result["current_commit"] = line.split(":", 1)[1].strip()
        elif line.startswith("deployment_provenance:"):
            result["provenance"] = line.split(":", 1)[1].strip()
        elif line.startswith("authorization_ref:"):
            result["authorization_ref"] = line.split(":", 1)[1].strip()
    return result



def _check_deployment_integrity(repo_root: Path, governance_root: Path | None = None) -> dict[str, Any]:
    """AIPOS-C3 大项A: 部署完整性区间校验(current..HEAD 每个 commit 都属已 PASS 的卡)。

    使用 deployment_authorization.check_commit_interval_coverage 的统一实现。
    实证修复(2026-08-18 三层空洞): 取代原 current==HEAD 简单相等。

    语义:
      - 无部署 → OK(首次 commit, 无漂移可校验)
      - provenance=dev_override → 拒(finalize 拒绝在 dev_override 上结算)
      - current == HEAD → OK
      - current..HEAD 每个 commit 均属已 PASS 的卡 → OK(待 deploy 追平)
      - 否则 → 拒, 列出缺审 commit

    Returns:
        {"integrity_ok": bool, "current_commit": str|None, "head_commit": str,
         "provenance": str|None, "missing_commits": list[str], "message": str}
    """
    from tools.aipos_cli.deployment_authorization import check_commit_interval_coverage
    
    head_commit = _git_rev_parse_head(repo_root)
    deploy_dir = repo_root / ".deploy"
    current_link = deploy_dir / "current"
    
    if not current_link.exists():
        # No deployment setup yet - this is OK for finalize (we're just committing)
        return {
            "integrity_ok": True,
            "current_commit": None,
            "head_commit": head_commit,
            "provenance": None,
            "missing_commits": [],
            "message": "No .deploy/current symlink (no deployment yet - OK for commit)",
        }

    deployed = _read_deploy_current(repo_root)
    current_commit = deployed["current_commit"]
    provenance = deployed["provenance"]
    
    if not current_commit:
        return {
            "integrity_ok": False,
            "current_commit": None,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": ".deploy/current/VERSION missing git_commit field",
        }

    # AIPOS-C3 大项A: provenance=dev_override → finalize 拒绝在其上结算
    if provenance == "dev_override":
        return {
            "integrity_ok": False,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": (
                f"Deployment provenance=dev_override (current={current_commit[:8]}). "
                "finalize 拒绝在 dev_override 部署上结算 —— 必须先用审过的 commit 重部署 "
                "(lybra-deploy --verdict-ref <pass_verdict_id>)。"
            ),
        }

    if current_commit == head_commit:
        return {
            "integrity_ok": True,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": f"Deployment integrity OK: current == HEAD ({head_commit[:8]})",
        }

    # AIPOS-C3 大项A②: 区间校验统一实现(check_commit_interval_coverage)
    if governance_root is None:
        # 无 governance_root, 退化为简单检查(不做深度校验)
        return {
            "integrity_ok": True,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": (
                f"区间校验跳过(无 governance_root): current({current_commit[:8]})..HEAD({head_commit[:8]})"
            ),
        }
    
    coverage = check_commit_interval_coverage(
        repo_root=repo_root,
        governance_root=governance_root,
        current_commit=current_commit,
        head_commit=head_commit,
    )
    
    return {
        "integrity_ok": coverage["coverage_ok"],
        "current_commit": current_commit,
        "head_commit": head_commit,
        "provenance": provenance,
        "missing_commits": coverage["missing_commits"],
        "message": coverage["message"],
    }


def _report_frontmatter_verdict_for_display(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-FND-14: best-effort, DISPLAY-ONLY lookup of the human-authored
    task_cards/<task_id>/AUDIT-REPORT-*.md frontmatter ``verdict:`` field.

    This is NEVER judged for finalize eligibility (see ``check_task_can_finalize`` below —
    that report has no reliable frontmatter and is a plain editable markdown file anyone could
    hand-write a fake ``verdict: PASS`` into). It is surfaced purely so operators can see what
    the (non-authoritative) report says alongside the real gate verdict. Any failure here is
    swallowed — this must never block or alter the real finalize decision.
    """
    try:
        task_dir = workspace_root / "task_cards" / task_id
        audit_reports = sorted(task_dir.glob("AUDIT-REPORT-*.md"))
        if not audit_reports:
            return {"report_path": None, "report_verdict": None}
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

        report_path = audit_reports[0]
        metadata, _body, _warnings = parse_markdown_frontmatter(report_path.read_text(encoding="utf-8"))
        return {"report_path": str(report_path), "report_verdict": metadata.get("verdict")}
    except Exception:
        return {"report_path": None, "report_verdict": None}


def check_task_can_finalize(task_id: str, governance_root: Path) -> dict[str, Any]:
    """AIPOS-C3 大项A: 检查任务是否可以 finalize(基于门生 PASS 裁决)。

    使用 deployment_authorization.find_gate_pass_verdict_for_task 的统一实现。
    
    实证修复:
      - 旧逻辑读 task_cards AUDIT-REPORT frontmatter(手写文件,可伪造)
      - 新逻辑只认门生裁决(5_tasks/records/audit_verdicts/,具备机器特征)
      - 手写文件(缺 record_type/verdict_id/verdict_at) = 拒绝

    Returns:
        {
            "can_finalize": bool,
            "task_id": str,
            "verdict": str | None,
            "verdict_record_path": str | None,
            "verdict_id": str | None,
            "reason": str
        }
    """
    from tools.aipos_cli.deployment_authorization import find_gate_pass_verdict_for_task
    
    verdict_check = find_gate_pass_verdict_for_task(task_id, governance_root)
    
    return {
        "can_finalize": verdict_check["found"],
        "task_id": task_id,
        "verdict": verdict_check["verdict"],
        "verdict_record_path": verdict_check["verdict_file"],
        "verdict_id": verdict_check["verdict_id"],
        "reason": verdict_check["reason"],
    }


def check_stage_archive_gate(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """AIPOS-R6M 大项A③: 阶段粒度门票 — stage transition (finalize/发布门) 前校验阶段快照存在。

    判据与路径从 config.schema 治理目录树读
    (``timeline_enforcement.stage_level.path_key`` + ``governance_structure.paths.<key>``),
    代码零写死。缺阶段快照 → BLOCK (门票机制: 阶段快照=转换前提, 缺快照=未关账=不许转换)。

    Args:
        governance_root: 治理工作区根 (拥有 stage_archive/ 的根, 非产品仓)。
        repo_root: 产品仓根 (用于定位 schema/config.schema.json 单一源)。

    Returns:
        {"passed": bool, "message": str, "stage_archive_dir": str|None,
         "snapshot_count": int, "path_key": str|None}
    """
    try:
        from tools.schema_loader import get_governance_structure, resolve_governance_path

        gs = get_governance_structure(repo_root)
        stage_level = (gs.get("timeline_enforcement") or {}).get("stage_level") or {}
        path_key = str(stage_level.get("path_key") or "stage_archive")
        stage_dir = resolve_governance_path(path_key, governance_root, repo_root)
    except Exception as exc:
        return {
            "passed": False,
            "message": f"Stage gate config load failed: {exc}",
            "stage_archive_dir": None,
            "snapshot_count": 0,
            "path_key": None,
        }

    if not stage_dir.is_dir():
        return {
            "passed": False,
            "message": (
                f"Stage gate BLOCK: stage archive dir missing ({stage_dir}). "
                "阶段快照=转换前提, 缺快照=未关账=不许转换 (AIPOS-R6M 大项A③)."
            ),
            "stage_archive_dir": str(stage_dir),
            "snapshot_count": 0,
            "path_key": path_key,
        }

    # 阶段快照 = 目录内 .md 文件, 排除 README/index (索引非阶段快照)。
    snapshots = sorted(
        p for p in stage_dir.glob("*.md")
        if p.name.lower() not in {"readme.md", "index.md"}
    )
    if not snapshots:
        return {
            "passed": False,
            "message": (
                f"Stage gate BLOCK: no stage snapshot in {stage_dir} (empty or index-only). "
                "阶段快照=转换前提, 缺快照=未关账=不许转换 (AIPOS-R6M 大项A③)."
            ),
            "stage_archive_dir": str(stage_dir),
            "snapshot_count": 0,
            "path_key": path_key,
        }

    return {
        "passed": True,
        "message": f"Stage gate OK: {len(snapshots)} stage snapshot(s) in {stage_dir}",
        "stage_archive_dir": str(stage_dir),
        "snapshot_count": len(snapshots),
        "path_key": path_key,
    }


def finalize_task(
    task_id: str,
    actor: str,
    workspace_root: Path,
    *,
    governance_root: Path | None = None,
    dry_run: bool = False,
    push: bool = False,
    deploy: bool = False,
) -> dict[str, Any]:
    """Finalize a PASS task by committing changes to git.

    AIPOS-FINALIZE-FIX-1: finalize 只操作产品仓 git,绝不 commit/push 治理仓。
    治理仓(5_tasks/records/)的 git 操作归 N6 收账节点(顾问职责),executor 无权。
    
    AIPOS-FND-9: After commit, auto-deploys gate-side changes to prevent drift.
    AIPOS-FND-14 + FND-47: Audit eligibility is now checked against the authoritative gate
    audit verdict record (governance workspace 5_tasks/records/), NOT the task_cards
    AUDIT-REPORT markdown frontmatter. FND-47: record_type validation reads from enums.schema (single source).

    Args:
        task_id: Task ID to finalize
        actor: Actor performing the finalization
        workspace_root: Product code repo root (git operations run here) - must be product
            repo, NOT governance repo. finalize git commit/push only operates here.
        governance_root: Governance workspace root (owns 5_tasks/records/) - read-only for
            audit verdict check. NO git operations here. If None, resolved via
            resolve_workspace_root().
        dry_run: If True, only validate without committing
        push: If True, also push after commit (product repo only)
        deploy: If True, run lybra-deploy after push (AIPOS-R4B-2)

    Returns:
        {
            "verdict": Verdict.PASS | "BLOCK" | "FAIL",  # AIPOS-FINALIZE-FIX-1: deploy fail -> FAIL
            "task_id": str,
            "actor": str,
            "dry_run": bool,
            "can_finalize": bool,
            "integrity_check": dict,
            "branch_check": dict,  # AIPOS-R4B-2: deployment branch enforcement
            "committed": bool,
            "pushed": bool,
            "deployed": bool,
            "deployment_skipped": bool,
            "deployment_error": str | None,
            "commit_hash": str | None,
            "message": str,
            "operations": list[str]
        }
    """
    operations = []

    # AIPOS-FND-14: resolve governance root (where 5_tasks/records/ lives) separately from
    # workspace_root (the product code repo, where git commit/push runs). In the standard
    # two-root setup, workspace_root=~/projects/lybra and governance_root=
    # ~/ai-project-os/2_projects/lybra; they MUST NOT be conflated.
    if governance_root is None:
        from tools.aipos_cli.workspace_config import resolve_workspace_root
        try:
            governance_root = resolve_workspace_root()
        except FileNotFoundError as exc:
            operations.append(f"Cannot resolve governance root: {exc}")
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": False,
                "integrity_check": None,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                "message": f"Cannot locate governance workspace (5_tasks/records/): {exc}",
                "operations": operations,
            }

    operations.append(f"Governance root (audit verdicts): {governance_root}")
    operations.append(f"Product repo root (git ops): {workspace_root}")
    
    # AIPOS-R6A 靶子⑦: finalize 场地根治 — 硬拒治理仓 git 操作
    # workspace_root 必须是产品仓，绝不能是治理仓（218f8b7 实证：治理仓大扫除卷入 agency 记录）
    try:
        ws_resolved = workspace_root.resolve()
        gov_resolved = governance_root.resolve()
        
        # 检查 workspace_root 是否在治理仓路径下
        if ws_resolved == gov_resolved or str(ws_resolved).startswith(str(gov_resolved) + "/"):
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": False,
                "integrity_check": None,
                "branch_check": None,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                "message": (
                    f"BLOCKED: workspace_root ({ws_resolved}) is inside governance_root ({gov_resolved}). "
                    f"finalize git operations MUST run in product repo only. "
                    f"治理仓 git 归 N6 收账节点(顾问职责), executor 无权操作。"
                ),
                "operations": operations,
            }
    except Exception as exc:
        operations.append(f"Warning: Could not verify workspace/governance separation: {exc}")

    # AIPOS-FND-14: display-only — surface the task_cards AUDIT-REPORT frontmatter verdict
    # (if any) alongside the real gate verdict for operator visibility. Never judged.
    report_display = _report_frontmatter_verdict_for_display(workspace_root, task_id)
    if report_display["report_path"]:
        operations.append(
            f"(display only, not judged) task_cards AUDIT-REPORT frontmatter verdict: "
            f"{report_display['report_verdict']!r} at {report_display['report_path']}"
        )

    # Check if task can be finalized (gate audit verdict = PASS)
    finalize_check = check_task_can_finalize(task_id, governance_root)
    operations.append(f"Checked finalize eligibility: {finalize_check['reason']}")
    
    if not finalize_check["can_finalize"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": False,
            "integrity_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": finalize_check["reason"],
            "operations": operations,
        }

    # AIPOS-R6M 大项A③: 阶段粒度门票 — finalize(发布门) 前校验 stage_archive 快照存在。
    # 判据与路径从 config.schema 治理目录树读(代码零写死), 缺快照 → BLOCK。
    stage_gate = check_stage_archive_gate(governance_root, repo_root=workspace_root)
    operations.append(f"Stage gate: {stage_gate['message']}")
    if not stage_gate["passed"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "stage_gate": stage_gate,
            "message": stage_gate["message"],
            "operations": operations,
        }

    # Check deployment integrity (current==HEAD)
    integrity = _check_deployment_integrity(workspace_root)
    operations.append(f"Deployment integrity: {integrity['message']}")
    
    if not integrity["integrity_ok"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": f"Deployment integrity check failed: {integrity['message']}",
            "operations": operations,
        }
    
    # AIPOS-R4B-2: 部署分支强制 — finalize/deploy 只允许从 main 分支
    from tools.aipos_cli.deploy_gate import check_deployment_branch
    
    branch_check = check_deployment_branch(workspace_root, required_branch="main")
    operations.append(f"Branch check: {branch_check['message']}")
    
    # 如果要 push 或 deploy，必须在 main 分支上
    if (push or deploy) and not branch_check["on_required_branch"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": f"Deployment branch check failed: {branch_check['message']}",
            "operations": operations,
        }
    
    # AIPOS-R5A: Worktree 合并和清理（finalize 收敛）
    worktree_merged = False
    worktree_cleaned = False
    try:
        from tools.worktree_manager import WorktreeManager
        wt_manager = WorktreeManager.from_workspace_config(workspace_root)
        branch_name = wt_manager.branch_name_for_task(task_id)
        
        # 检查是否存在该任务的分支
        if wt_manager._branch_exists(branch_name):
            if not dry_run:
                # 合并 worktree 分支到 main
                merge_result = wt_manager.merge_to_main(
                    branch_name=branch_name,
                    strategy='squash',  # 默认 squash 策略
                    main_branch='main'
                )
                operations.append(f"Merged {branch_name} to main (squash)")
                worktree_merged = True
                
                # 删除 worktree
                cleanup_result = wt_manager.cleanup_task(
                    task_id=task_id,
                    remove_branch=True,
                    force=False
                )
                if cleanup_result['worktree_removed']:
                    operations.append(f"Removed worktree for {task_id}")
                if cleanup_result['branch_deleted']:
                    operations.append(f"Deleted branch {branch_name}")
                worktree_cleaned = True
            else:
                operations.append(f"DRY-RUN: Would merge {branch_name} to main and cleanup")
    except Exception as exc:
        # Worktree 处理失败不阻塞 finalize（可能本就没用 worktree）
        operations.append(f"Worktree cleanup warning: {exc}")
    
    # Check if there are changes to commit
    # AIPOS-R6A 靶子③: finalize push判据修正 — working tree clean ≠ already pushed
    # 需要检查 local vs origin 同步状态
    # AIPOS-R7A2 靶①(P0): clean-tree 早退必须检查 deploy 状态,禁静默跳过
    if _git_status_clean(workspace_root):
        synced = _git_local_origin_synced(workspace_root)
        current_commit = _git_rev_parse_head(workspace_root)
        
        # AIPOS-R7A2 靶①: 检查当前 commit 是否已部署
        # 有 PASS 裁决 + 未部署 → 必须 deploy 或显式 FAIL
        deployed_info = _read_deploy_current(workspace_root)
        deployed_commit = deployed_info.get("current_commit")
        needs_deploy = (deployed_commit != current_commit)
        
        # Case 1: working tree clean + synced → 检查 deploy 状态
        if synced:
            if needs_deploy:
                # AIPOS-R7A2 靶①(P0): 未部署但有 PASS 裁决 → 必须 deploy,不可静默跳过
                # 进入 finalize 说明已有 PASS 裁决,commit 未部署是闭环缺口,必须补
                operations.append(f"⚠️  Current commit {current_commit[:8]} not deployed (deployed: {deployed_commit[:8] if deployed_commit else 'none'})")
                operations.append("⚠️  PASS 裁决下的 commit 必须部署,触发强制 deploy...")
                
                from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
                if deploy_result["success"]:
                    operations.append("✓ Deploy completed successfully")
                    verification = verify_deployment_version(workspace_root, current_commit)
                    if verification["verified"]:
                        return {
                            "verdict": Verdict.PASS,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": dry_run,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": False,
                            "deployed": True,
                            "deployment_skipped": False,
                            "deployment_error": None,
                            "commit_hash": current_commit,
                            "message": f"No changes to commit, deployed {current_commit[:8]} to close gap",
                            "operations": operations,
                        }
                    else:
                        # Deploy 验证失败 → FAIL
                        return {
                            "verdict": Verdict.FAIL,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": dry_run,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": False,
                            "deployed": False,
                            "deployment_skipped": False,
                            "deployment_error": verification["message"],
                            "commit_hash": current_commit,
                            "message": f"Deploy verification failed: {verification['message']}",
                            "operations": operations,
                        }
                else:
                    # Deploy 失败 → FAIL
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": dry_run,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": False,
                        "pushed": False,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deploy_result["stderr"],
                        "commit_hash": current_commit,
                        "message": f"Deploy failed: {deploy_result['stderr'][:200]}",
                        "operations": operations,
                    }
            else:
                # 已部署 → 真正无事可做
                return {
                    "verdict": Verdict.PASS,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": dry_run,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": False,
                    "pushed": False,
                    "deployed": False,
                    "deployment_skipped": True,
                    "deployment_error": None,
                    "commit_hash": current_commit,
                    "message": "No changes to commit (working tree clean, synced, and deployed)",
                    "operations": operations,
                }
        
        # Case 2: working tree clean but not synced → 需要 push (如果 push=True)
        if not push:
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": True,
                "integrity_check": integrity,
                "branch_check": branch_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": current_commit,
                "message": "Working tree clean but unpushed commits exist (use --push to push)",
                "operations": operations,
            }
        
        # Case 3: working tree clean, not synced, push=True → 执行 push
        if dry_run:
            operations.append("DRY-RUN: Would push unpushed commits to remote")
            if needs_deploy:
                operations.append("DRY-RUN: Would deploy to close deployment gap")
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": True,
                "can_finalize": True,
                "integrity_check": integrity,
                "branch_check": branch_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": current_commit,
                "message": "DRY-RUN: Would push unpushed commits",
                "operations": operations,
            }
        
        # Actually push
        try:
            operations.append("Pushing unpushed commits to remote...")
            subprocess.run(
                ["git", "push"],
                cwd=str(workspace_root),
                check=True,
                capture_output=True,
                text=True,
            )
            operations.append("Push successful")
            
            # AIPOS-R7A2 靶①: push 成功后检查 deploy 需求
            if needs_deploy:
                operations.append(f"⚠️  Current commit {current_commit[:8]} not deployed (deployed: {deployed_commit[:8] if deployed_commit else 'none'})")
                operations.append("Triggering deploy after push...")
                
                from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
                if deploy_result["success"]:
                    operations.append("✓ Deploy completed successfully")
                    verification = verify_deployment_version(workspace_root, current_commit)
                    if verification["verified"]:
                        return {
                            "verdict": Verdict.PASS,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": False,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": True,
                            "deployed": True,
                            "deployment_skipped": False,
                            "deployment_error": None,
                            "commit_hash": current_commit,
                            "message": "Pushed and deployed successfully",
                            "operations": operations,
                        }
                    else:
                        return {
                            "verdict": Verdict.FAIL,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": False,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": True,
                            "deployed": False,
                            "deployment_skipped": False,
                            "deployment_error": verification["message"],
                            "commit_hash": current_commit,
                            "message": f"Pushed but deploy verification failed: {verification['message']}",
                            "operations": operations,
                        }
                else:
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": False,
                        "pushed": True,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deploy_result["stderr"],
                        "commit_hash": current_commit,
                        "message": f"Pushed but deploy failed: {deploy_result['stderr'][:200]}",
                        "operations": operations,
                    }
            else:
                # 已部署,只需 push
                return {
                    "verdict": Verdict.PASS,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": False,
                    "pushed": True,
                    "deployed": False,
                    "deployment_skipped": True,
                    "deployment_error": None,
                    "commit_hash": current_commit,
                    "message": "Pushed unpushed commits (already deployed)",
                    "operations": operations,
                }
        except subprocess.CalledProcessError as e:
            operations.append(f"Push failed: {e.stderr}")
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "can_finalize": False,
                "integrity_check": integrity,
                "branch_check": branch_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                "message": f"Push failed: {e.stderr}",
                "operations": operations,
            }
    
    if dry_run:
        operations.append("DRY-RUN: Would commit changes")
        if push:
            operations.append("DRY-RUN: Would push to remote")
        if deploy:
            operations.append("DRY-RUN: Would run lybra-deploy")
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": "DRY-RUN: Changes would be committed",
            "operations": operations,
        }
    
    # Commit changes
    commit_msg = f"feat({task_id}): finalize PASS task\n\nActor: {actor}\nAudit: {finalize_check['verdict']}"
    
    try:
        # AIPOS-R8B 大项A: Stage changes with pathspec限定到 workspace_root (产品仓)
        # 防止 git add -A 越界 stage 治理仓或其他项目的文件
        subprocess.run(
            ["git", "add", "-A", "--", "."],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        operations.append(f"Staged all changes (git add -A -- . in {workspace_root})")
        
        # AIPOS-R8B 大项A②: 断言 staged 文件全部落在 workspace_root 内,越界即 BLOCK
        staged_files_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        staged_files = [f.strip() for f in staged_files_result.stdout.split('\n') if f.strip()]
        
        # 检查 staged 文件是否全部在 workspace_root 内(相对路径不应以 ../ 开头)
        # 同时检查敏感路径(.lybra/connection.json 等)不应被 stage
        out_of_scope = []
        sensitive_files = []
        for staged_file in staged_files:
            # 相对路径以 ../ 开头或包含 ../ 说明越界
            if staged_file.startswith('../') or '/../' in staged_file:
                out_of_scope.append(staged_file)
            # 敏感路径检查 (AIPOS-R6K token 泄漏同病根)
            if staged_file.startswith('.lybra/') and any(sensitive in staged_file for sensitive in ['connection.json', 'role', 'token']):
                sensitive_files.append(staged_file)
        
        if out_of_scope:
            operations.append(f"SCOPE VIOLATION: {len(out_of_scope)} staged files outside workspace_root")
            out_of_scope_list = '\n  - '.join(out_of_scope[:10])  # 最多列10个
            if len(out_of_scope) > 10:
                out_of_scope_list += f'\n  - ... and {len(out_of_scope) - 10} more'
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "finalize_check": finalize_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "commit_hash": None,
                "message": f"SCOPE VIOLATION: Staged files outside workspace root (G3 铁律):\n  - {out_of_scope_list}",
                "operations": operations,
            }
        
        if sensitive_files:
            operations.append(f"SENSITIVE FILES BLOCKED: {len(sensitive_files)} credential/config files")
            sensitive_list = '\n  - '.join(sensitive_files)
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "finalize_check": finalize_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "commit_hash": None,
                "message": f"SENSITIVE FILES: Cannot commit credentials/config to product repo:\n  - {sensitive_list}",
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
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = _git_rev_parse_head(workspace_root)
        operations.append(f"Committed changes: {commit_hash[:8]}")
        
        pushed = False
        if push:
            try:
                subprocess.run(
                    ["git", "push"],
                    cwd=str(workspace_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Pushed to remote")
                pushed = True
            except subprocess.CalledProcessError as e:
                operations.append(f"Push failed: {e.stderr}")
            except subprocess.TimeoutExpired:
                operations.append("Push timed out after 30s")
        
        # AIPOS-R4B-2 / AIPOS-FINALIZE-FIX-1: Explicit deploy with lybra-deploy
        # deploy 失败 → finalize 整体 FAIL (exit 非0 + verdict FAIL),禁吞错报成功
        deployed = False
        deployment_skipped = False
        deployment_error = None
        
        if deploy:
            # 显式 deploy 模式：直接调用 lybra-deploy
            from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
            
            operations.append("ℹ️  Invoking lybra-deploy (explicit deploy mode)...")
            deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
            
            if deploy_result["success"]:
                operations.append("✓ lybra-deploy completed successfully")
                # Append first 10 lines of output
                deploy_lines = deploy_result["stdout"].strip().splitlines()
                for line in deploy_lines[:10]:
                    operations.append(f"  {line}")
                if len(deploy_lines) > 10:
                    operations.append(f"  ... ({len(deploy_lines) - 10} more lines)")
                
                # Verify deployment
                verification = verify_deployment_version(workspace_root, commit_hash)
                operations.append(f"Deployment verification: {verification['message']}")
                if verification["verified"]:
                    deployed = True
                else:
                    # AIPOS-FINALIZE-FIX-1: 部署验证失败 → finalize FAIL
                    deployment_error = verification["message"]
                    operations.append(f"✗ Deployment verification FAILED: {verification['message']}")
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": True,
                        "pushed": pushed,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deployment_error,
                        "commit_hash": commit_hash,
                        "message": f"Deployment verification failed: {deployment_error}",
                        "operations": operations,
                    }
            else:
                # AIPOS-FINALIZE-FIX-1: deploy 子步失败 → finalize 整体 FAIL
                deployment_error = deploy_result["stderr"]
                operations.append(f"✗ lybra-deploy FAILED: {deploy_result['stderr'][:200]}")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": True,
                    "pushed": pushed,
                    "deployed": False,
                    "deployment_skipped": False,
                    "deployment_error": deployment_error,
                    "commit_hash": commit_hash,
                    "message": f"Deployment failed: {deployment_error[:200]}",
                    "operations": operations,
                }
        else:
            # F-R4B2-3: FND-9 Auto-deploy gate-side changes (无论 push 与否都检查)
            from tools.aipos_cli.gate_drift import check_gate_drift
            from tools.aipos_cli.deploy_gate import invoke_lybra_deploy
            
            drift_check = check_gate_drift(workspace_root)
            operations.append(f"Drift check: {drift_check['message']}")
            
            if drift_check["has_drift"] and drift_check["classification"]["has_gate_side_changes"]:
                # Gate-side changes detected - auto-deploy
                operations.append("⚠️  Gate-side changes detected - triggering auto-deploy...")
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
                if deploy_result["success"]:
                    operations.append("✓ Deployment completed successfully")
                    # Append deployment output (first 10 lines)
                    deploy_lines = deploy_result["stdout"].strip().splitlines()
                    for line in deploy_lines[:10]:
                        operations.append(f"  {line}")
                    if len(deploy_lines) > 10:
                        operations.append(f"  ... ({len(deploy_lines) - 10} more lines)")
                    deployed = True
                else:
                    # AIPOS-FINALIZE-FIX-1: 自动部署失败也必须 FAIL,禁吞错
                    deployment_error = deploy_result["stderr"]
                    operations.append(f"✗ Auto-deployment FAILED: {deploy_result['stderr'][:200]}")
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": True,
                        "pushed": pushed,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deployment_error,
                        "commit_hash": commit_hash,
                        "message": f"Auto-deployment failed: {deployment_error[:200]}",
                        "operations": operations,
                    }
            elif drift_check["has_drift"] and not drift_check["classification"]["has_gate_side_changes"]:
                operations.append("ℹ️  CLI-side changes only - no deployment needed")
                deployment_skipped = True
            else:
                operations.append("ℹ️  No drift detected - deployment up-to-date")
                deployment_skipped = True
        
        # Build final message
        final_message = f"Successfully committed changes: {commit_hash[:8]}"
        if deployed:
            final_message += " and deployed to gate"
        elif deployment_error:
            final_message += f" but deployment FAILED: {deployment_error[:100]}"
        
        # AIPOS-R8B 大项B F-3.1: 写 finalization 记录(必落,按 task_id 分目录)
        if not dry_run:
            try:
                from tools.aipos_cli.finalization_record import write_finalization_record
                fin_result = write_finalization_record(
                    governance_root=governance_root,
                    task_id=task_id,
                    actor=actor,
                    commit=commit_hash,
                    authorization_type="verdict_ref",
                    authorization_ref=finalize_check.get("verdict_id", "unknown"),
                    deployed=deployed,
                    deployment_record_ref=None,  # TODO: 从 deployment_record 返回值获取
                )
                operations.append(f"Finalization record written: {fin_result['path']}")
            except Exception as e:
                operations.append(f"⚠️  Finalization record write failed: {e}")
        
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": True,
            "pushed": pushed,
            "deployed": deployed,
            "deployment_skipped": deployment_skipped,
            "deployment_error": deployment_error,
            "commit_hash": commit_hash,
            "message": final_message,
            "operations": operations,
        }
        
    except subprocess.CalledProcessError as e:
        operations.append(f"Git operation failed: {e.stderr}")
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": f"Git operation failed: {e.stderr}",
            "operations": operations,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
