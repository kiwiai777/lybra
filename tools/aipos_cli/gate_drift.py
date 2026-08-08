"""AIPOS-FND-9 — Gate deployment drift detection.

Detects when gate-side code has been committed but not deployed, preventing
the "committed but not live" silent failure that caused multiple incidents.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


# Gate-side paths: changes here require deployment to take effect
GATE_SIDE_PATHS = [
    "tools/mcp_server/",
    "tools/turn_advancer/",
    "tools/sandbox_runtime/",
    "config/",
    "0_control_plane/",
]

# CLI-side paths: changes here are live immediately (editable install)
CLI_SIDE_PATHS = [
    "tools/aipos_cli/",
    "tools/lybra_tui/",
    "bin/",
]


def _git_rev_parse(repo_root: Path, ref: str) -> str:
    """Get commit hash for a given ref."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", ref],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def _git_log_count(repo_root: Path, rev_range: str) -> int:
    """Count commits in a range."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", rev_range],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return 0


def _git_log_commits(repo_root: Path, rev_range: str, limit: int = 10) -> list[dict[str, str]]:
    """Get commit info in a range."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--no-decorate", f"-{limit}", rev_range],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                commits.append({"hash": parts[0], "message": parts[1]})
            elif len(parts) == 1:
                commits.append({"hash": parts[0], "message": ""})
        return commits
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []


def _git_diff_name_only(repo_root: Path, rev_range: str) -> list[str]:
    """Get changed file paths in a range."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", rev_range],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []


def _classify_changed_paths(changed_paths: list[str]) -> dict[str, Any]:
    """Classify changed paths as gate-side, CLI-side, or other."""
    gate_side = []
    cli_side = []
    other = []
    
    for path in changed_paths:
        if any(path.startswith(p) for p in GATE_SIDE_PATHS):
            gate_side.append(path)
        elif any(path.startswith(p) for p in CLI_SIDE_PATHS):
            cli_side.append(path)
        else:
            other.append(path)
    
    return {
        "gate_side": gate_side,
        "cli_side": cli_side,
        "other": other,
        "has_gate_side_changes": len(gate_side) > 0,
    }


def _read_deployed_commit(workspace_root: Path) -> str | None:
    """Read the currently deployed commit from .deploy/current/VERSION."""
    deploy_dir = workspace_root / ".deploy"
    current_link = deploy_dir / "current"
    
    if not current_link.exists():
        return None
    
    version_file = current_link / "VERSION"
    if not version_file.exists():
        return None
    
    try:
        for line in version_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("git_commit:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    
    return None


def check_gate_drift(workspace_root: Path) -> dict[str, Any]:
    """Check if gate deployment is drifted from HEAD.
    
    Returns:
        {
            "has_drift": bool,
            "deployed_commit": str | None,
            "head_commit": str,
            "commits_ahead": int,
            "undeployed_commits": list[dict],  # [{hash, message}, ...]
            "changed_paths": list[str],
            "classification": {
                "gate_side": list[str],
                "cli_side": list[str],
                "other": list[str],
                "has_gate_side_changes": bool
            },
            "message": str,
            "recommendation": str
        }
    """
    # Get HEAD commit
    head_commit = _git_rev_parse(workspace_root, "HEAD")
    if not head_commit:
        return {
            "has_drift": False,
            "deployed_commit": None,
            "head_commit": "",
            "commits_ahead": 0,
            "undeployed_commits": [],
            "changed_paths": [],
            "classification": {
                "gate_side": [],
                "cli_side": [],
                "other": [],
                "has_gate_side_changes": False,
            },
            "message": "Unable to read git HEAD",
            "recommendation": "Check if you are in a git repository",
        }
    
    # Get deployed commit
    deployed_commit = _read_deployed_commit(workspace_root)
    if not deployed_commit:
        return {
            "has_drift": False,
            "deployed_commit": None,
            "head_commit": head_commit,
            "commits_ahead": 0,
            "undeployed_commits": [],
            "changed_paths": [],
            "classification": {
                "gate_side": [],
                "cli_side": [],
                "other": [],
                "has_gate_side_changes": False,
            },
            "message": "No deployment found (.deploy/current does not exist)",
            "recommendation": "Run 'lybra-deploy' to create initial deployment",
        }
    
    # Check if drifted
    if deployed_commit == head_commit:
        return {
            "has_drift": False,
            "deployed_commit": deployed_commit,
            "head_commit": head_commit,
            "commits_ahead": 0,
            "undeployed_commits": [],
            "changed_paths": [],
            "classification": {
                "gate_side": [],
                "cli_side": [],
                "other": [],
                "has_gate_side_changes": False,
            },
            "message": f"No drift: deployment is up-to-date (both at {head_commit[:8]})",
            "recommendation": "",
        }
    
    # Drift detected - analyze
    rev_range = f"{deployed_commit}..{head_commit}"
    commits_ahead = _git_log_count(workspace_root, rev_range)
    undeployed_commits = _git_log_commits(workspace_root, rev_range, limit=10)
    changed_paths = _git_diff_name_only(workspace_root, rev_range)
    classification = _classify_changed_paths(changed_paths)
    
    # Build message
    message = (
        f"DRIFT DETECTED: {commits_ahead} commit(s) ahead of deployment\n"
        f"  Deployed: {deployed_commit[:8]}\n"
        f"  HEAD:     {head_commit[:8]}\n"
    )
    
    if classification["has_gate_side_changes"]:
        message += f"  ⚠️  {len(classification['gate_side'])} gate-side file(s) changed (requires deployment)"
    
    # Build recommendation
    if classification["has_gate_side_changes"]:
        recommendation = "Run 'lybra-deploy' to deploy gate-side changes"
    else:
        recommendation = "Changes are CLI-side only (no deployment needed)"
    
    return {
        "has_drift": True,
        "deployed_commit": deployed_commit,
        "head_commit": head_commit,
        "commits_ahead": commits_ahead,
        "undeployed_commits": undeployed_commits,
        "changed_paths": changed_paths,
        "classification": classification,
        "message": message,
        "recommendation": recommendation,
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
