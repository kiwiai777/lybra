"""AIPOS-FND-2 — finalize: git commit/push for PASS tasks.

After audit verdict=PASS, finalize commits the changes to git and optionally pushes.
Enforces deployment integrity (current==HEAD) and only allows finalization of PASS tasks.
"""

from __future__ import annotations

import subprocess
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


def check_task_can_finalize(task_id: str, workspace_root: Path) -> dict[str, Any]:
    """Check if a task can be finalized (verdict=PASS).
    
    Returns:
        {
            "can_finalize": bool,
            "task_id": str,
            "verdict": str | None,
            "audit_card_path": str | None,
            "reason": str
        }
    """
    # Look for audit report in task_cards/<task_id>/AUDIT-REPORT-*.md
    task_dir = workspace_root / "task_cards" / task_id
    
    if not task_dir.exists():
        return {
            "can_finalize": False,
            "task_id": task_id,
            "verdict": None,
            "audit_card_path": None,
            "reason": f"Task directory not found: {task_dir}",
        }
    
    # Find audit report
    audit_reports = list(task_dir.glob("AUDIT-REPORT-*.md"))
    if not audit_reports:
        return {
            "can_finalize": False,
            "task_id": task_id,
            "verdict": None,
            "audit_card_path": None,
            "reason": f"No audit report found in {task_dir}",
        }
    
    # Use the first audit report (should be only one)
    audit_card_path = audit_reports[0]
    
    # Parse frontmatter directly to get verdict
    try:
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        
        content = audit_card_path.read_text(encoding="utf-8")
        metadata, body, errors = parse_markdown_frontmatter(content)
        verdict = metadata.get("verdict")
        
        if verdict == "PASS":
            return {
                "can_finalize": True,
                "task_id": task_id,
                "verdict": verdict,
                "audit_card_path": str(audit_card_path),
                "reason": "Audit verdict is PASS",
            }
        else:
            return {
                "can_finalize": False,
                "task_id": task_id,
                "verdict": verdict,
                "audit_card_path": str(audit_card_path),
                "reason": f"Audit verdict is {verdict}, not PASS (cannot finalize)",
            }
    except Exception as e:
        return {
            "can_finalize": False,
            "task_id": task_id,
            "verdict": None,
            "audit_card_path": str(audit_card_path),
            "reason": f"Error loading audit report: {e}",
        }


def finalize_task(
    task_id: str,
    actor: str,
    workspace_root: Path,
    *,
    dry_run: bool = False,
    push: bool = False,
) -> dict[str, Any]:
    """Finalize a PASS task by committing changes to git.
    
    Args:
        task_id: Task ID to finalize
        actor: Actor performing the finalization
        workspace_root: Workspace root directory
        dry_run: If True, only validate without committing
        push: If True, also push after commit
    
    Returns:
        {
            "verdict": "PASS" | "BLOCK",
            "task_id": str,
            "actor": str,
            "dry_run": bool,
            "can_finalize": bool,
            "integrity_check": dict,
            "committed": bool,
            "pushed": bool,
            "commit_hash": str | None,
            "message": str,
            "operations": list[str]
        }
    """
    operations = []
    
    # Check if task can be finalized (verdict=PASS)
    finalize_check = check_task_can_finalize(task_id, workspace_root)
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
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Deployment integrity check failed: {integrity['message']}",
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
            "committed": False,
            "pushed": False,
            "commit_hash": _git_rev_parse_head(workspace_root),
            "message": "No changes to commit (working tree clean)",
            "operations": operations,
        }
    
    if dry_run:
        operations.append("DRY-RUN: Would commit changes")
        if push:
            operations.append("DRY-RUN: Would push to remote")
        return {
            "verdict": "PASS",
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "can_finalize": True,
            "integrity_check": integrity,
            "committed": False,
            "pushed": False,
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
        
        return {
            "verdict": "PASS",
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "can_finalize": True,
            "integrity_check": integrity,
            "committed": True,
            "pushed": pushed,
            "commit_hash": commit_hash,
            "message": f"Successfully committed changes: {commit_hash[:8]}",
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
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git operation failed: {e.stderr}",
            "operations": operations,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
