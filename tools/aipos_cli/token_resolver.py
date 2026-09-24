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


# AIPOS-F81: connection.json tokens[] 条目字段名 —— 挑选判据的唯一声明(产品无 schema 声明这些字段, 故声明在此)。
# TS 侧 agents/harness/pi/lybra-loop/loop-context.ts::TOKEN_ENTRY_FIELDS 注明来源于此, 同构夹具
# tests/test_aipos_f81_token_single_source.py 逐键比对并跑同一组靶场, 两边不一致即红。
TOKEN_ENTRY_FIELDS: dict[str, str] = {
    "token": "token",
    "role": "role",
    "agent_instance": "agent_instance",
    "projects": "projects",
    "retired": "retired",
}

# AIPOS-F81: 全 retired 时拒因携带的出口(重签)。TS 侧同名常量注明来源于此。
TOKEN_REENROLL_EXIT = "lybra roles enroll --code <码> --workspace <工位根>"

_F_TOKEN = TOKEN_ENTRY_FIELDS["token"]
_F_ROLE = TOKEN_ENTRY_FIELDS["role"]
_F_INSTANCE = TOKEN_ENTRY_FIELDS["agent_instance"]
_F_PROJECTS = TOKEN_ENTRY_FIELDS["projects"]
_F_RETIRED = TOKEN_ENTRY_FIELDS["retired"]


class TokenResolutionError(ValueError):
    """AIPOS-F81: token 解析失败(ValueError 子类, 存量 ``except ValueError`` 调用方不变)。"""


class TokenNotFoundError(TokenResolutionError):
    """没有任何条目命中选择器(instance/role)——调用方可按声明优先级落到下一层(如 env 兜底)。"""


class TokenAllRetiredError(TokenResolutionError):
    """命中选择器的条目全部 retired —— fail-closed, 禁落到下一层; 拒因带重签出口。"""


def is_token_entry_active(entry: Any) -> bool:
    """AIPOS-F81: 条目是否可用于取值(dict·非 retired·token 非空)。挑选判据的原子谓词, 唯一实现。"""
    return (
        isinstance(entry, dict)
        and not entry.get(_F_RETIRED)
        and bool(str(entry.get(_F_TOKEN) or "").strip())
    )


def load_connection_tokens(connection_json: str | Path) -> list[Any]:
    """读 connection.json 的 tokens 列表(读失败/非对象/无 tokens 列表 = ValueError, fail-closed)。"""
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
    return tokens


def select_token_entry(
    tokens: list[Any],
    *,
    role: str | None = None,
    agent_instance: str | None = None,
    project: str | None = None,
    allow_retired: bool = False,
    any_role: bool = False,
    source: str = "connection.json",
) -> dict[str, Any]:
    """AIPOS-F81: 从 tokens[] 挑一条条目 —— 挑选逻辑的唯一实现(F59 单源的核心)。

    判据(与 config.schema#identity_resolution.keys.token 的「agent_instance 匹配 → role 匹配」同序):
    1. 选择器分阶段: agent_instance 匹配(最具体) → role 匹配 → (any_role=True 时)任意条目;
    2. 每阶段只取可用条目: 非 retired(allow_retired=True 除外)且 token 非空;
    3. project 给定时按域过滤(无 projects 字段的旧条目仅在无显式域命中时入选);
    4. 首个有候选的阶段返回其第一条。
    都没有: 无命中 = TokenNotFoundError(可落下一层); 命中的全 retired = TokenAllRetiredError
    (fail-closed, 带重签出口); 其余(域不符/token 空)= TokenResolutionError。拒因只带指纹, 永不带 token 值。
    """
    stages: list[tuple[str, Any]] = []
    if agent_instance:
        stages.append((f"agent_instance={agent_instance!r}", lambda e: e.get(_F_INSTANCE) == agent_instance))
    if role:
        stages.append((f"role={role!r}", lambda e: e.get(_F_ROLE) == role))
    if any_role:
        stages.append(("any role", lambda e: True))
    selector = " → ".join(label for label, _ in stages) or "(no selector)"
    if not stages:
        raise TokenNotFoundError(f"No token selector (role/agent_instance) given for {source}")

    matched: list[dict[str, Any]] = []
    for _label, pred in stages:
        hits = [e for e in tokens if isinstance(e, dict) and pred(e)]
        for e in hits:
            if not any(e is m for m in matched):
                matched.append(e)
        usable = [
            e for e in hits
            if (allow_retired or not e.get(_F_RETIRED)) and str(e.get(_F_TOKEN) or "").strip()
        ]
        if project is not None:
            usable = [
                e for e in usable
                if not isinstance(e.get(_F_PROJECTS), list) or project in e[_F_PROJECTS]
            ]
            explicit = [e for e in usable if isinstance(e.get(_F_PROJECTS), list)]
            if explicit:
                usable = explicit
        if usable:
            return usable[0]

    if not matched:
        raise TokenNotFoundError(f"No token found for {selector} in {source}")
    if not allow_retired and all(e.get(_F_RETIRED) for e in matched):
        fps = ", ".join(token_fingerprint(str(e.get(_F_TOKEN) or "")) for e in matched)
        raise TokenAllRetiredError(
            f"All {len(matched)} token entr{'y' if len(matched) == 1 else 'ies'} for {selector} in {source} "
            f"are retired (fingerprints: {fps}) — no active credential. "
            f"出口: 重签凭据 `{TOKEN_REENROLL_EXIT}` (Owner 签发新 enrollment 码)"
        )
    if project is not None:
        raise TokenNotFoundError(
            f"No token found for {selector} with project domain={project!r} in {source}. "
            f"Re-enroll or check that the token's projects field includes {project!r}."
        )
    raise TokenResolutionError(f"Token entry for {selector} exists in {source} but has no token value")


def resolve_token_entry(
    connection_json: str | Path,
    role: str | None = None,
    project: str | None = None,
    *,
    agent_instance: str | None = None,
    allow_retired: bool = False,
) -> dict[str, Any]:
    """AIPOS-F81: 读 connection.json 并按唯一判据挑条目(需要条目元数据如 agent_instance/actor 的调用方用它)。"""
    path = Path(connection_json).expanduser().resolve()
    return select_token_entry(
        load_connection_tokens(path),
        role=role,
        agent_instance=agent_instance,
        project=project,
        allow_retired=allow_retired,
        source=str(path),
    )


def get_token_for_role_and_project(
    connection_json: str | Path,
    role: str | None,
    project: str | None = None,
    *,
    agent_instance: str | None = None,
    allow_retired: bool = False,
) -> str:
    """AIPOS-F59: Unified token getter by (role, project_domain).

    This is the SINGLE implementation point for token retrieval. All callers in
    confirm_client.py, advisor_pump.py, pump_orchestration.py, agent_supervise.py,
    board_login.py — and since AIPOS-F81 tools/loop_context.py ConnectionResolver
    (resolve_token / resolve_identity) — must delegate here (selection = select_token_entry).

    Selection logic (select_token_entry):
    1. agent_instance match first (AIPOS-F81, when given), then role match
    2. Filter by project domain (token.projects contains the requested project)
    3. Exclude retired tokens (unless allow_retired=True) and empty token values
    4. Return the first match

    Args:
        connection_json: Path to .lybra/connection.json
        role: Role name (e.g., "owner", "executor", "planner"); may be None when agent_instance given
        project: Project domain (e.g., "lybra", "chris-huibojin"). If None, project
                 domain filtering is skipped (back-compat for non-project-aware callers).
        agent_instance: AIPOS-F81 — agent instance id, matched before role (most specific)
        allow_retired: If True, include retired tokens in search (default False)

    Returns:
        The token string (raw, for in-process use only; callers must never print it)

    Raises:
        TokenNotFoundError: no entry matches the selectors (ValueError subclass)
        TokenAllRetiredError: every matching entry is retired — fail-closed, message carries the re-enroll exit
        ValueError: file unreadable/invalid, domain mismatch, or empty token value

    Examples:
        >>> token = get_token_for_role_and_project(".lybra/connection.json", "planner", "lybra")
        >>> # This will find planner tokens with projects:["lybra"], excluding retired ones
    """
    entry = resolve_token_entry(
        connection_json, role, project, agent_instance=agent_instance, allow_retired=allow_retired,
    )
    return str(entry.get(_F_TOKEN) or "").strip()


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
