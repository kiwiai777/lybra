"""AIPOS-F59: Token resolution by (role, project domain) — the single source of truth.

Root cause (AIPOS-F59 anchor): Token selection was "take first match by role" (ignoring
`projects` domain), and enroll was append-only. This meant stale tokens with wrong project
domains always got selected first, making chris's 5 re-enrollments ineffective.

This module provides:
1. `get_token_for_role_and_project()` — unified token getter by (role, project_domain)
2. `retire_token_entry()` — mark old entries as retired (leave trace, don't delete)
3. `detect_wrong_domain_tokens()` — reconcile wrong-domain entries

All token retrieval MUST go through this module. The 5+ duplicate implementations across
confirm_client.py, advisor_pump.py, pump_orchestration.py, agent_supervise.py, board_login.py
are consolidated here (AIPOS-F59 constraint: no new token retrieval implementations).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# AIPOS-F59: Project domain resolution is delegated to workspace_config.resolve_active_project
# (the existing primary resolver). We do NOT create a new project domain parser here
# (constraint: F66 owns that).


def token_fingerprint(token: str) -> str:
    """Non-secret fingerprint of a bearer token (never the raw token)."""
    if not token:
        return "(none)"
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def get_token_for_role_and_project(
    connection_json: str | Path,
    role: str,
    project: str | None = None,
    *,
    allow_retired: bool = False,
) -> str:
    """AIPOS-F59: Unified token getter by (role, project_domain).

    This is the SINGLE implementation point for token retrieval. All callers in
    confirm_client.py, advisor_pump.py, pump_orchestration.py, agent_supervise.py,
    board_login.py must delegate here.

    Selection logic:
    1. Filter by role
    2. Filter by project domain (token.projects contains the requested project)
    3. Exclude retired tokens (unless allow_retired=True)
    4. Return the first match

    Args:
        connection_json: Path to .lybra/connection.json
        role: Role name (e.g., "owner", "executor", "planner")
        project: Project domain (e.g., "lybra", "chris-huibojin"). If None, project
                 domain filtering is skipped (back-compat for non-project-aware callers).
        allow_retired: If True, include retired tokens in search (default False)

    Returns:
        The token string (raw, for in-process use only; callers must never print it)

    Raises:
        ValueError: If no matching token is found, or if the file is invalid

    Examples:
        >>> token = get_token_for_role_and_project(".lybra/connection.json", "planner", "lybra")
        >>> # This will find planner tokens with projects:["lybra"], excluding retired ones
    """
    path = Path(connection_json).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read connection.json at {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"connection.json at {path} is not a JSON object")

    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError(f"connection.json at {path} has no tokens list")

    # Filter candidates
    candidates = []
    for item in tokens:
        if not isinstance(item, dict):
            continue

        # Filter by role
        if item.get("role") != role:
            continue

        # Filter by retired status
        if not allow_retired and item.get("retired"):
            continue

        # Filter by project domain (if specified)
        if project is not None:
            item_projects = item.get("projects")
            if isinstance(item_projects, list):
                if project not in item_projects:
                    continue
            # If the token has no projects field, it's a legacy token (pre-project-domain).
            # For back-compat, we include it in candidates only if no better match exists.
            # We'll deprioritize it by checking explicit-domain matches first below.

        candidates.append(item)

    # Prioritize tokens with explicit project domain match
    if project is not None:
        explicit_matches = [
            c for c in candidates
            if isinstance(c.get("projects"), list) and project in c["projects"]
        ]
        if explicit_matches:
            candidates = explicit_matches

    if not candidates:
        if project is not None:
            raise ValueError(
                f"No token found for role={role!r} with project domain={project!r} in {path}. "
                f"Re-enroll or check that the token's projects field includes {project!r}."
            )
        else:
            raise ValueError(f"No token found for role={role!r} in {path}")

    # Return the first candidate
    token = str(candidates[0].get("token") or "").strip()
    if not token:
        raise ValueError(f"Token entry for role={role!r} exists but has no token value")

    return token


def retire_token_entry(
    connection_json: str | Path,
    *,
    role: str | None = None,
    agent_instance: str | None = None,
    token_fingerprint_match: str | None = None,
    reason: str = "superseded by re-enrollment",
) -> int:
    """AIPOS-F59: Mark token entries as retired (leave trace, don't delete).

    Old enroll behavior: append-only, never remove. This meant stale tokens stayed first.
    New behavior: mark old entries as retired with timestamp and reason.

    At least one of role, agent_instance, or token_fingerprint_match must be provided.

    Args:
        connection_json: Path to .lybra/connection.json
        role: Role to retire (all matching entries if multiple)
        agent_instance: Agent instance to retire (all matching entries)
        token_fingerprint_match: Fingerprint prefix to match (e.g., "sha256:3e44d7f190ce")
        reason: Human-readable reason for retirement

    Returns:
        Number of entries retired (0 if none matched)

    Side effects:
        Writes updated connection.json with retired entries marked:
        {
            "role": "planner",
            "projects": ["lybra"],
            "token": "...",
            "retired": true,
            "retired_at": "2026-08-31T12:00:00Z",
            "retired_reason": "superseded by re-enrollment"
        }
    """
    if not any([role, agent_instance, token_fingerprint_match]):
        raise ValueError("Must provide at least one of: role, agent_instance, token_fingerprint_match")

    path = Path(connection_json).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read connection.json at {path}: {exc}") from exc

    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError(f"connection.json at {path} has no tokens list")

    retired_count = 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for item in tokens:
        if not isinstance(item, dict):
            continue
        if item.get("retired"):
            # Already retired, skip
            continue

        # Check match criteria
        match = True
        if role is not None and item.get("role") != role:
            match = False
        if agent_instance is not None and item.get("agent_instance") != agent_instance:
            match = False
        if token_fingerprint_match is not None:
            tok = str(item.get("token") or "")
            fp = token_fingerprint(tok)
            if not fp.startswith(token_fingerprint_match):
                match = False

        if match:
            item["retired"] = True
            item["retired_at"] = now
            item["retired_reason"] = reason
            retired_count += 1

    if retired_count > 0:
        # Write back
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return retired_count


def detect_wrong_domain_tokens(
    connection_json: str | Path,
    expected_project: str,
) -> list[dict[str, Any]]:
    """AIPOS-F59: Detect tokens with wrong project domain (reconcile).

    This helps identify cases like chris's situation: a planner token with projects:["lybra"]
    in a chris-huibojin workspace, which should be projects:["chris-huibojin"].

    Args:
        connection_json: Path to .lybra/connection.json
        expected_project: The project domain this workspace should have (from project.json)

    Returns:
        List of wrong-domain token entries (each a dict with keys: role, projects, fingerprint,
        retired, mismatch_reason). Empty list if all tokens have correct domains.

    Example output:
        [
            {
                "role": "planner",
                "agent_instance": "advisor.lybra.kiwiai-dev",
                "projects": ["lybra"],  # Wrong! Should be ["chris-huibojin"]
                "fingerprint": "sha256:3e44d7f190ce",
                "retired": false,
                "mismatch_reason": "Token projects ['lybra'] do not include expected 'chris-huibojin'"
            }
        ]
    """
    path = Path(connection_json).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        return []

    wrong_domain = []
    for item in tokens:
        if not isinstance(item, dict):
            continue

        item_projects = item.get("projects")
        if not isinstance(item_projects, list):
            # No projects field: legacy token (pre-domain). Not necessarily wrong,
            # but flag it for manual review.
            wrong_domain.append({
                "role": item.get("role"),
                "agent_instance": item.get("agent_instance"),
                "projects": None,
                "fingerprint": token_fingerprint(str(item.get("token") or "")),
                "retired": bool(item.get("retired")),
                "mismatch_reason": f"Token has no projects field (legacy). Expected: ['{expected_project}']",
            })
            continue

        if expected_project not in item_projects:
            wrong_domain.append({
                "role": item.get("role"),
                "agent_instance": item.get("agent_instance"),
                "projects": item_projects,
                "fingerprint": token_fingerprint(str(item.get("token") or "")),
                "retired": bool(item.get("retired")),
                "mismatch_reason": f"Token projects {item_projects} do not include expected '{expected_project}'",
            })

    return wrong_domain


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
