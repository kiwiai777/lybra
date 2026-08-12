"""AIPOS-FND-2/FND-9 — finalize: git commit/push/deploy for PASS tasks.

After audit verdict=PASS, finalize commits the changes to git and optionally pushes.
Enforces deployment integrity (current==HEAD) and only allows finalization of PASS tasks.

AIPOS-FND-9: Auto-deploy gate-side changes after commit to prevent "committed but not live" drift.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


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

    The authoritative source of truth is the gate's own audit_verdict_record files under
    ``<governance_root>/5_tasks/records/audit_verdicts/<task_id>/*.md`` — written ONLY by the
    ``audit_verdict`` MCP verb, always carrying ``record_type: audit_verdict_record``. This
    scans that directory, rejects any file that doesn't carry that exact ``record_type`` (never
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
        # AIPOS-FND-14: reject hand-written markdown masquerading as a gate verdict. A real
        # gate audit_verdict_record ALWAYS carries record_type: audit_verdict_record.
        if str(metadata.get("record_type") or "").strip() != "audit_verdict_record":
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
                "(files present but none carry record_type: audit_verdict_record — hand-written "
                "markdown is never accepted as finalize evidence)."
            ),
        }

    latest = max(candidates, key=lambda c: c["verdict_at"])

    if latest["verdict"] in {"PASS", "PASS_WITH_NOTES"}:
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

    AIPOS-FND-9: After commit, auto-deploys gate-side changes to prevent drift.
    AIPOS-FND-14: Audit eligibility is now checked against the authoritative gate
    audit_verdict_record (governance workspace 5_tasks/records/), NOT the task_cards
    AUDIT-REPORT markdown frontmatter.

    Args:
        task_id: Task ID to finalize
        actor: Actor performing the finalization
        workspace_root: Product code repo root (git operations run here)
        governance_root: Governance workspace root (owns 5_tasks/records/).  If None,
            resolved via ``resolve_workspace_root()`` using the standard Lybra precedence
            ladder (LYBRA_WORKSPACE_ROOT / AIPOS_WORKSPACE_ROOT / ~/.lybra/config.json home
            model).  Must be supplied explicitly in contexts where auto-resolution would
            produce the wrong workspace.
        dry_run: If True, only validate without committing
        push: If True, also push after commit
        deploy: If True, run lybra-deploy after push (AIPOS-R4B-2)

    Returns:
        {
            "verdict": "PASS" | "BLOCK",
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
                "verdict": "BLOCK",
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
            "verdict": "BLOCK",
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
    
    # Check deployment integrity (current==HEAD)
    integrity = _check_deployment_integrity(workspace_root)
    operations.append(f"Deployment integrity: {integrity['message']}")
    
    if not integrity["integrity_ok"]:
        return {
            "verdict": "BLOCK",
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
    from tools.aipos_cli.finalize_enhanced import check_deployment_branch
    
    branch_check = check_deployment_branch(workspace_root, required_branch="main")
    operations.append(f"Branch check: {branch_check['message']}")
    
    # 如果要 push 或 deploy，必须在 main 分支上
    if (push or deploy) and not branch_check["on_required_branch"]:
        return {
            "verdict": "BLOCK",
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
    
    # Check if there are changes to commit
    if _git_status_clean(workspace_root):
        return {
            "verdict": "PASS",
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
            "message": "No changes to commit (working tree clean)",
            "operations": operations,
        }
    
    if dry_run:
        operations.append("DRY-RUN: Would commit changes")
        if push:
            operations.append("DRY-RUN: Would push to remote")
        if deploy:
            operations.append("DRY-RUN: Would run lybra-deploy")
        return {
            "verdict": "PASS",
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
        
        # AIPOS-R4B-2: Explicit deploy with lybra-deploy
        deployed = False
        deployment_skipped = False
        deployment_error = None
        
        if deploy:
            # 显式 deploy 模式：直接调用 lybra-deploy
            from tools.aipos_cli.finalize_enhanced import invoke_lybra_deploy, verify_deployment_version
            
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
                    deployment_error = verification["message"]
                    operations.append(f"✗ Deployment verification FAILED: {verification['message']}")
            else:
                deployment_error = deploy_result["stderr"]
                operations.append(f"✗ lybra-deploy FAILED: {deploy_result['stderr'][:200]}")
        else:
            # F-R4B2-3: FND-9 Auto-deploy gate-side changes (无论 push 与否都检查)
            from tools.aipos_cli.gate_drift import check_gate_drift
            from tools.aipos_cli.finalize_enhanced import invoke_lybra_deploy
            
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
                    deployment_error = deploy_result["stderr"]
                    operations.append(f"✗ Deployment FAILED: {deploy_result['stderr'][:200]}")
                    # Don't fail the finalize, but warn strongly
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
            "verdict": "PASS",
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
            "verdict": "BLOCK",
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
