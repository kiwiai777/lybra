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


def _check_deployment_integrity(repo_root: Path) -> dict[str, Any]:
    """Check if .deploy/current symlink points to HEAD (AIPOS-369 assertion).
    
    Returns:
        {"integrity_ok": bool, "current_commit": str, "head_commit": str, "message": str}
    """
    deploy_dir = repo_root / ".deploy"
    current_link = deploy_dir / "current"
    
    if not current_link.exists():
        # No deployment setup yet - this is OK for finalize (we're just committing)
        return {
            "integrity_ok": True,
            "current_commit": None,
            "head_commit": _git_rev_parse_head(repo_root),
            "message": "No .deploy/current symlink (no deployment yet - OK for commit)",
        }
    
    # Read VERSION file from current deployment
    version_file = current_link / "VERSION"
    if not version_file.exists():
        return {
            "integrity_ok": False,
            "current_commit": None,
            "head_commit": _git_rev_parse_head(repo_root),
            "message": ".deploy/current/VERSION does not exist",
        }
    
    version_data = version_file.read_text(encoding="utf-8")
    current_commit = None
    for line in version_data.splitlines():
        if line.startswith("git_commit:"):
            current_commit = line.split(":", 1)[1].strip()
            break
    
    head_commit = _git_rev_parse_head(repo_root)
    
    if current_commit == head_commit:
        return {
            "integrity_ok": True,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "message": f"Deployment integrity OK: current == HEAD ({head_commit[:8]})",
        }
    else:
        return {
            "integrity_ok": False,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "message": f"DRIFT: current ({current_commit[:8] if current_commit else 'unknown'}) != HEAD ({head_commit[:8]})",
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
    """AIPOS-FND-14: Check if a task can be finalized against the AUTHORITATIVE gate
    audit verdict record — NOT the task_cards/<task_id>/AUDIT-REPORT-*.md frontmatter.

    Root cause this fixes: the old implementation read a hand-authored audit report's
    frontmatter ``verdict:`` field. That report ships with no frontmatter at all, so
    ``verdict`` was always ``None`` and finalize was permanently BLOCKed — forcing executors
    to hand-roll ``git push`` around the gate entirely. Worse, that report is a plain editable
    markdown file: anyone could hand-write ``verdict: PASS`` into it and finalize would trust
    it, which is backwards for an accountability harness.

    The authoritative source of truth is the gate's own audit verdict record files under
    ``<governance_root>/5_tasks/records/audit_verdicts/<task_id>/*.md`` — written ONLY by the
    ``audit_verdict`` MCP verb, carrying ``record_type`` from enums.schema audit_verdict family. This
    scans that directory, rejects any file that doesn't carry a valid audit_verdict record_type (never
    counts hand-written markdown as judgeable evidence), sorts the rest by ``verdict_at``, and
    requires the LATEST terminal verdict to be PASS or PASS_WITH_NOTES (same acceptance rule as
    ``queue_mutation._check_for_pass_audit_verdict``, AIPOS-FND-7F3).

    ``governance_root`` MUST be the governance workspace root — the one that owns
    ``5_tasks/records/`` — NOT the product code repo. The two are decoupled (task_cards/ lives
    in the product repo; 5_tasks/records/ lives in governance); callers must resolve this
    explicitly rather than guessing between the two roots.

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
    verdicts_dir = governance_root / "5_tasks" / "records" / "audit_verdicts" / task_id

    if not verdicts_dir.is_dir():
        return {
            "can_finalize": False,
            "task_id": task_id,
            "verdict": None,
            "verdict_record_path": None,
            "verdict_id": None,
            "reason": (
                f"No gate audit verdict record found for {task_id} under {verdicts_dir} "
                "(finalize requires an authoritative gate audit_verdict record; a task_cards "
                "AUDIT-REPORT is never sufficient)."
            ),
        }

    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    candidates: list[dict[str, Any]] = []
    for verdict_file in sorted(verdicts_dir.glob("*.md")):
        try:
            text = verdict_file.read_text(encoding="utf-8")
        except OSError:
            continue
        metadata, _body, _warnings = parse_markdown_frontmatter(text)
        # AIPOS-FND-14 + FND-47: reject hand-written markdown masquerading as a gate verdict.
        # A real gate audit_verdict record carries record_type from enums.schema audit_verdict family.
        # FND-47 fix: read valid types from schema (single source), accept both audit_verdict and
        # audit_verdict_record (legacy) to handle schema migration period.
        record_type = str(metadata.get("record_type") or "").strip()
        valid_types = _get_valid_record_types()
        # Accept audit_verdict* family (audit_verdict, audit_verdict_record)
        if not (record_type in valid_types and record_type.startswith(RecordType.AUDIT_VERDICT)):
            continue
        verdict_value = str(metadata.get("verdict") or "").strip().upper()
        verdict_at = str(metadata.get("verdict_at") or metadata.get("timestamp") or "")
        candidates.append(
            {
                "path": verdict_file,
                "verdict": verdict_value,
                "verdict_at": verdict_at,
                "verdict_id": metadata.get("verdict_id") or verdict_file.stem,
            }
        )

    if not candidates:
        return {
            "can_finalize": False,
            "task_id": task_id,
            "verdict": None,
            "verdict_record_path": None,
            "verdict_id": None,
            "reason": (
                f"No gate audit verdict record found for {task_id} under {verdicts_dir} "
                "(files present but none carry valid audit_verdict* record_type from enums.schema — hand-written "
                "markdown is never accepted as finalize evidence)."
            ),
        }

    latest = max(candidates, key=lambda c: c["verdict_at"])

    if latest["verdict"] in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
        return {
            "can_finalize": True,
            "task_id": task_id,
            "verdict": latest["verdict"],
            "verdict_record_path": str(latest["path"]),
            "verdict_id": latest["verdict_id"],
            "reason": f"Latest gate audit verdict is {latest['verdict']} ({latest['path'].name})",
        }

    return {
        "can_finalize": False,
        "task_id": task_id,
        "verdict": latest["verdict"] or None,
        "verdict_record_path": str(latest["path"]),
        "verdict_id": latest["verdict_id"],
        "reason": (
            f"Latest gate audit verdict is {latest['verdict'] or 'UNKNOWN'}, not PASS "
            f"(cannot finalize): {latest['path'].name}"
        ),
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
    if _git_status_clean(workspace_root):
        synced = _git_local_origin_synced(workspace_root)
        
        # Case 1: working tree clean + synced → 真正无事可做
        if synced:
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
                "commit_hash": _git_rev_parse_head(workspace_root),
                "message": "No changes to commit (working tree clean and synced with origin)",
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
                "commit_hash": _git_rev_parse_head(workspace_root),
                "message": "Working tree clean but unpushed commits exist (use --push to push)",
                "operations": operations,
            }
        
        # Case 3: working tree clean, not synced, push=True → 执行 push
        if dry_run:
            operations.append("DRY-RUN: Would push unpushed commits to remote")
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
                "commit_hash": _git_rev_parse_head(workspace_root),
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
                "commit_hash": _git_rev_parse_head(workspace_root),
                "message": "Pushed unpushed commits (no new changes to commit)",
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
        # Stage all changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        operations.append("Staged all changes (git add -A)")
        
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
            deploy_result = invoke_lybra_deploy(workspace_root)
            
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
                
                deploy_result = invoke_lybra_deploy(workspace_root)
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
