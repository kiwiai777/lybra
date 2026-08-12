"""AIPOS-350 — Instance naming productization.

Three-layer design:
  S1: generate_canonical_name() — auto-produces <prefix>.<project>.<host>
  S2: naming_profile — workspace-level config data (in project.json)
      holding prefix_mapping, project_segment (+aliases), host_segment (+aliases).
      Modify via product commands; append-only trail; immediate effect.
  S3: validate_instance_name() reads this data — zero hardcoded prefix mapping.

The three-part dotted structure (<role_prefix>.<project_segment>.<host_segment>)
is a PRODUCT RULE (not configurable). The VALUES within each segment come from
the alias layer (naming_profile in project.json). Changing an alias = config edit
(no code change, no new task card).

AIPOS-350F1: project_segment and host_segment are NEVER hardcoded.
  - project_segment: derived from project.json's 'project' field (workspace identity).
  - host_segment: must be explicitly configured per workspace; at minting time an
    per-instance override may be supplied (workspace value is the default pre-fill).
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.workspace_config import (
    read_project_json,
    project_json_path,
    governance_paths,
)
# AIPOS-R4B-1 FIX-2: schema_loader 导入移至函数内(惰性),避免 CLI 早期导入链在 editable-install
# 环境下 ModuleNotFoundError(namespace package 'tools' 无法解析顶层模块 'schema_loader')。
# 见 AUDIT-R4B-1 F-R4B1-1。


# ---------------------------------------------------------------------------
# Default naming profile — matches current Lybra conventions
# ---------------------------------------------------------------------------

def _registry_prefix_mapping() -> dict[str, str]:
    """Build the role→prefix map from the roles registry (single source, AIPOS-R4B-1).

    Replaces the previous hardcoded DEFAULT_PREFIX_MAPPING dict. Each registry
    role's naming.prefix is the prefix (LOOP-REDESIGN v2 §5-6). A role without a
    prefix is skipped. New role = add naming.prefix to the registry; naming picks
    it up with zero code change.
    """
    from tools.schema_loader import get_all_role_names, get_role_naming_prefix
    mapping: dict[str, str] = {}
    for role in get_all_role_names():
        prefix = get_role_naming_prefix(role)
        if prefix:
            mapping[role] = prefix
    return mapping


# AIPOS-R4B-1: centralized dev defaults for the non-raising instance-name path.
# Used by default_instance_name() when no workspace project.json is available
# (CLI fallback paths: pump_orchestration, advisor_pump, agent_supervise).
# Forward-compatible: override via env. The canonical, config-backed, validating
# path remains generate_canonical_name() (which reads project.json).
DEFAULT_INSTANCE_PROJECT = os.environ.get("LYBRA_PROJECT", "lybra")


def default_instance_name(
    prefix: str, *, project: str | None = None, host: str | None = None
) -> str:
    """Non-raising instance-name derivation from the registry template (AIPOS-R4B-1).

    THE single implementation of the {prefix}.{project}.{host} pattern for the
    non-validating/fallback paths. Replaces ~20 scattered inline literals such
    as 'exec.lybra.kiwiai-dev' / f"{role}.lybra.kiwiai-dev" (pump_orchestration,
    advisor_pump, agent_supervise, audit_derivation).

    - prefix: the role's display prefix (e.g. 'exec', 'audit', 'advisor').
      Callers already hold this (from policy envelopes). The prefix<->role map
      is also single-sourced in the registry (role.naming.prefix) for the
      canonical path (generate_canonical_name).
    - project: from caller; else DEFAULT_INSTANCE_PROJECT (env-overridable).
    - host: from caller; else socket.gethostname() short form (machine identity;
      matches audit_derivation's prior behavior — no hardcoded 'kiwiai-dev').

    NEVER raises. For the canonical (config-backed, validating) path use
    generate_canonical_name() instead.
    """
    # 惰性导入(避免模块级导入崩溃 CLI)
    try:
        from tools.schema_loader import get_role_naming_template
        template = get_role_naming_template()
    except ImportError as e:
        raise ImportError(
            "Cannot load schema_loader.get_role_naming_template() for instance naming. "
            "This typically occurs when running lybra CLI from outside the project root "
            "in an editable install. Run from the project directory or ensure PYTHONPATH "
            "includes the project root."
        ) from e
    proj = project or DEFAULT_INSTANCE_PROJECT
    h = host or socket.gethostname().split(".")[0]
    return template.format(prefix=prefix, project=proj, host=h)


def default_naming_profile(
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Default naming profile — derived from workspace, never hardcoded.

    AIPOS-350F1: no project or host literal is baked in.

    prefix_mapping: role -> display prefix (planner maps to 'planner' by default;
        projects that use 'advisor' for planner set it via set_prefix_mapping).
    project_segment: derived from project.json's 'project' field when project_root
        is provided.  If the field is missing, raises ValueError with guidance.
        If project_root is None, project_segment is omitted from the result.
    host_segment: intentionally omitted — no hardcoded default.  Must be explicitly
        configured per workspace via set_host_segment() or in project.json.
    *_aliases: empty lists (stable defaults).
    """
    profile: dict[str, Any] = {
        "prefix_mapping": _registry_prefix_mapping(),
        "project_segment_aliases": [],
        "host_segment_aliases": [],
    }
    if project_root is not None:
        project_json = read_project_json(project_root)
        project_name = str(project_json.get("project") or "").strip()
        if project_name:
            profile["project_segment"] = project_name
        else:
            raise ValueError(
                "未配置项目段 (project_segment): project.json 缺少 'project' 字段。"
                "请运行 `lybra naming set-project-segment <name>` 或在 project.json "
                "中添加 naming_profile.project_segment"
            )
    return profile


# ---------------------------------------------------------------------------
# S2: Read / write naming profile (workspace data in project.json)
# ---------------------------------------------------------------------------

def get_naming_profile(project_root: str | Path) -> dict[str, Any]:
    """Read naming_profile from project.json.  AIPOS-350F1: no baked-in values.

    prefix_mapping: merged with the registry prefix map (new roles from registry
        added, existing overridden). AIPOS-352: also includes custom role prefixes
        from the custom_roles registry (custom role name → itself as prefix).
    project_segment: from naming_profile.project_segment, falling back to
        project.json's 'project' field.  If neither exists, raises ValueError.
    host_segment: from naming_profile.host_segment only (no fallback).  If absent
        the key is omitted from the result — generate_canonical_name() will raise
        at call time, and validate_instance_name_with_profile() skips host check.
    *_aliases: from naming_profile or empty lists.
    """
    project_json = read_project_json(project_root)
    profile = project_json.get("naming_profile")

    # Start with prefix defaults only — no project/host hardcodes
    result: dict[str, Any] = {
        "prefix_mapping": _registry_prefix_mapping(),
        "project_segment_aliases": [],
        "host_segment_aliases": [],
    }

    if profile is not None and isinstance(profile, dict):
        # prefix_mapping: merge (new roles from default added, existing overridden)
        if isinstance(profile.get("prefix_mapping"), dict):
            result["prefix_mapping"].update(profile["prefix_mapping"])
        # Scalar fields: override if present and non-empty
        for key in ("project_segment", "host_segment"):
            val = profile.get(key)
            if isinstance(val, str) and val.strip():
                result[key] = val.strip()
        # List fields: override if present and is a list
        for key in ("project_segment_aliases", "host_segment_aliases"):
            val = profile.get(key)
            if isinstance(val, list):
                result[key] = [str(v).strip() for v in val if str(v).strip()]
    # AIPOS-352: merge custom role prefixes (custom name → itself as prefix)
    try:
        from tools.aipos_cli.custom_roles import custom_roles_for_naming
        result["prefix_mapping"].update(custom_roles_for_naming(project_root))
    except Exception:
        pass  # defensive: custom_roles module may not be available in all contexts

    # project_segment fallback: derive from project.json's 'project' field
    if "project_segment" not in result:
        project_name = str(project_json.get("project") or "").strip()
        if project_name:
            result["project_segment"] = project_name
        else:
            raise ValueError(
                "未配置项目段 (project_segment): project.json 既无 "
                "naming_profile.project_segment 也无 'project' 字段。"
                "请运行 `lybra naming set-project-segment <name>`"
            )

    # host_segment: no fallback — intentionally omitted if not configured.
    # generate_canonical_name() raises when it is needed but absent.
    return result


def _write_naming_profile(
    project_root: str | Path,
    profile: dict[str, Any],
    *,
    by: str = "owner",
    reason: str = "",
    change_type: str = "update",
) -> Path:
    """Write naming_profile into project.json (preserve all other fields).

    Returns the project.json path. Appends to the naming switch trail.
    """
    root = Path(project_root)
    path = project_json_path(root)
    data = read_project_json(root)
    data["naming_profile"] = profile
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Append-only trail
    _append_naming_trail(root, change_type=change_type, by=by, reason=reason, profile=profile)
    return path


def _append_naming_trail(
    project_root: Path,
    *,
    change_type: str,
    by: str,
    reason: str,
    profile: dict[str, Any],
) -> Path:
    """Append-only trail for naming profile changes."""
    trail = governance_paths(project_root)["decision_log"].parent / "naming_profile_log.md"
    trail.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # Summarize the change compactly
    prefix_summary = ",".join(f"{r}={p}" for r, p in sorted(profile.get("prefix_mapping", {}).items()))
    summary = (
        f"type={change_type} "
        f"project_segment={profile.get('project_segment', '?')} "
        f"host_segment={profile.get('host_segment', '?')} "
        f"prefixes=[{prefix_summary}]"
    )
    line = f"- {ts}  {summary}  by={by}  reason={reason or '(none)'}\n"
    with trail.open("a", encoding="utf-8") as fh:
        if trail.stat().st_size == 0:
            fh.write("# Naming Profile Switch Log (append-only)\n\n")
        fh.write(line)
    return trail


# ---------------------------------------------------------------------------
# S2: Product commands for modifying aliases
# ---------------------------------------------------------------------------

def set_prefix_mapping(
    project_root: str | Path,
    role: str,
    prefix: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Set the prefix for a single role. Returns updated naming profile.

    Example: set_prefix_mapping(root, "planner", "advisor") makes planner
    instances generate as advisor.<project>.<host>.
    """
    role_clean = str(role or "").strip()
    prefix_clean = str(prefix or "").strip()
    if not role_clean:
        raise ValueError("role must be non-empty")
    if not prefix_clean:
        raise ValueError("prefix must be non-empty")
    profile = get_naming_profile(project_root)
    profile["prefix_mapping"][role_clean] = prefix_clean
    _write_naming_profile(project_root, profile, by=by, reason=reason, change_type="set_prefix")
    return profile


def set_project_segment(
    project_root: str | Path,
    segment: str,
    *,
    aliases: list[str] | None = None,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Set the project segment (and optionally its aliases). Returns updated profile."""
    clean = str(segment or "").strip()
    if not clean:
        raise ValueError("project_segment must be non-empty")
    profile = get_naming_profile(project_root)
    profile["project_segment"] = clean
    if aliases is not None:
        profile["project_segment_aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
    _write_naming_profile(project_root, profile, by=by, reason=reason, change_type="set_project_segment")
    return profile


def set_host_segment(
    project_root: str | Path,
    segment: str,
    *,
    aliases: list[str] | None = None,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Set the host segment (and optionally its aliases). Returns updated profile."""
    clean = str(segment or "").strip()
    if not clean:
        raise ValueError("host_segment must be non-empty")
    profile = get_naming_profile(project_root)
    profile["host_segment"] = clean
    if aliases is not None:
        profile["host_segment_aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
    _write_naming_profile(project_root, profile, by=by, reason=reason, change_type="set_host_segment")
    return profile


def add_project_segment_alias(
    project_root: str | Path,
    alias: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Add an alias for the project segment. Idempotent."""
    clean = str(alias or "").strip()
    if not clean:
        raise ValueError("alias must be non-empty")
    profile = get_naming_profile(project_root)
    if clean not in profile["project_segment_aliases"]:
        profile["project_segment_aliases"].append(clean)
        _write_naming_profile(project_root, profile, by=by, reason=reason, change_type="add_project_alias")
    return profile


def add_host_segment_alias(
    project_root: str | Path,
    alias: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Add an alias for the host segment. Idempotent."""
    clean = str(alias or "").strip()
    if not clean:
        raise ValueError("alias must be non-empty")
    profile = get_naming_profile(project_root)
    if clean not in profile["host_segment_aliases"]:
        profile["host_segment_aliases"].append(clean)
        _write_naming_profile(project_root, profile, by=by, reason=reason, change_type="add_host_alias")
    return profile


# ---------------------------------------------------------------------------
# S1: Canonical name generation
# ---------------------------------------------------------------------------

def generate_canonical_name(
    role: str,
    project_root: str | Path,
    *,
    host_segment_override: str | None = None,
) -> str:
    """Generate the canonical instance name for a role: <prefix>.<project>.<host>.

    All three segments come from the naming profile (alias layer). No hardcoded values.
    AIPOS-350F1: host_segment_override allows per-instance host at minting time;
    the workspace-level host_segment is the default pre-fill.
    Raises ValueError if the role has no prefix mapping or host_segment is absent.
    """
    profile = get_naming_profile(project_root)
    role_clean = str(role or "").strip()
    prefix = profile["prefix_mapping"].get(role_clean)
    if not prefix:
        raise ValueError(
            f"No prefix mapping for role {role_clean!r}. "
            f"Known roles: {sorted(profile['prefix_mapping'].keys())}"
        )
    project = profile["project_segment"]
    # Host: per-instance override > workspace config > error
    host = str(host_segment_override or "").strip() or profile.get("host_segment")
    if not host:
        raise ValueError(
            "未配置主机段 (host_segment): 请在 project.json 中设置 "
            "naming_profile.host_segment, 或在铸发时通过 host_segment_override 指定"
        )
    return f"{prefix}.{project}.{host}"


# ---------------------------------------------------------------------------
# S3: Validator helpers — zero hardcoded prefix mapping
# ---------------------------------------------------------------------------

# The set of known role NAMES (not prefixes) — this is a product rule (the
# three-part structure), not a hardcoded value map.
ROLE_NAMES: set[str] = {"executor", "auditor", "owner", "copilot", "planner", "owner-dispatch"}


def _accepted_project_segments(profile: dict[str, Any]) -> set[str]:
    """All accepted values for the project segment (canonical + aliases)."""
    result = {profile["project_segment"]}
    result.update(profile.get("project_segment_aliases", []))
    return result


def _accepted_host_segments(profile: dict[str, Any]) -> set[str]:
    """All accepted values for the host segment (canonical + aliases)."""
    result = {profile["host_segment"]}
    result.update(profile.get("host_segment_aliases", []))
    return result


def _accepted_prefixes_for_role(role: str, profile: dict[str, Any]) -> set[str]:
    """All accepted prefixes for a given role.

    The canonical prefix from prefix_mapping is always accepted.
    Additionally, if a role's canonical prefix is X, any alias that maps to the
    same role is also accepted. But the simplest model: the role's own prefix
    from the mapping is the single accepted prefix. If the project wants
    'advisor' to be accepted for 'planner', they set planner->advisor in the mapping.
    """
    prefix = profile["prefix_mapping"].get(role)
    if prefix:
        return {prefix}
    return set()


_DEFAULT_PROFILE_CACHE: dict[str, Any] | None = None


def _default_profile() -> dict[str, Any]:
    """Lazy singleton default profile (for callers without a project_root).

    AIPOS-350F1: contains only prefix_mapping (no project/host — those have no
    safe global default).  Callers use this for format-only validation.
    """
    global _DEFAULT_PROFILE_CACHE
    if _DEFAULT_PROFILE_CACHE is None:
        _DEFAULT_PROFILE_CACHE = default_naming_profile()  # no project_root → no project/host
    return _DEFAULT_PROFILE_CACHE


def validate_instance_name(
    name: str,
    role: str,
    project_root: str | Path | None = None,
) -> tuple[bool, str | None]:
    """Unified compliance validation — single entry point (AIPOS-350F2).

    Reads naming_profile (prefix mapping / project segment aliases / host
    segment aliases) from the workspace's project.json when project_root is
    provided.  When project_root is None, falls back to format-only validation
    using the default prefix mapping (no project/host value checks — there are
    no hardcoded defaults).

    All callers (roles list/reconcile, rotate pre-check, generators) must use
    this function.  No parallel validation logic anywhere.

    Returns (is_valid, error_message).

    Rules:
    - Three-part dotted structure (product rule, not configurable).
    - Role prefix must match the naming_profile's prefix_mapping for the role.
    - Project part must not be a role name (common mistake: audit.auditor.xxx).
    - When project_root provided: project/host segments checked against alias layer.
    - When project_root is None: format-only (prefix + role-name guard + non-empty).
    """
    if not name or not name.strip():
        return False, "Instance name cannot be empty"

    parts = name.split(".")
    if len(parts) != 3:
        return False, f"Instance name must have 3 parts (<role>.<project>.<machine>), got {len(parts)}: {name}"

    role_part, project_part, machine_part = parts

    # Resolve profile: workspace-aware or format-only default
    if project_root is not None:
        try:
            profile = get_naming_profile(project_root)
        except (ValueError, KeyError):
            # Workspace has no project.json or no project_segment configured;
            # fall back to format-only validation (no project/host value checks).
            profile = _default_profile()
    else:
        profile = _default_profile()

    # Role prefix check — from naming_profile alias layer, NOT hardcoded
    accepted_prefixes = _accepted_prefixes_for_role(role, profile)
    if accepted_prefixes and role_part not in accepted_prefixes:
        expected = profile["prefix_mapping"].get(role, "?")
        return False, f"Role prefix mismatch: expected '{expected}' for role '{role}', got '{role_part}' in '{name}'"

    # Project part must not be a role name (common mistake: audit.auditor.xxx)
    if project_part in ROLE_NAMES:
        return False, f"Project part '{project_part}' is a role name, not a project name in '{name}'"

    # Project segment check — from alias layer (only when profile has project_segment)
    if "project_segment" in profile:
        accepted_projects = _accepted_project_segments(profile)
        if project_part not in accepted_projects:
            return False, (
                f"Project part '{project_part}' is not the project segment "
                f"('{profile['project_segment']}') or any of its aliases "
                f"({sorted(accepted_projects)}) in '{name}'"
            )

    # Host segment check — from alias layer (only when profile has host_segment)
    if "host_segment" in profile:
        accepted_hosts = _accepted_host_segments(profile)
        if machine_part not in accepted_hosts:
            return False, (
                f"Host part '{machine_part}' is not the host segment "
                f"('{profile['host_segment']}') or any of its aliases "
                f"({sorted(accepted_hosts)}) in '{name}'"
            )

    # Non-empty (belt-and-suspenders after split, but defensive)
    if not project_part.strip():
        return False, f"Project part cannot be empty in '{name}'"
    if not machine_part.strip():
        return False, f"Machine part cannot be empty in '{name}'"

    return True, None


# ---------------------------------------------------------------------------
# Backward-compatible aliases — delegate to unified validate_instance_name()
# AIPOS-350F2: these are thin wrappers, zero duplicated logic.
# ---------------------------------------------------------------------------

def validate_instance_name_with_profile(
    name: str,
    role: str,
    project_root: str | Path,
) -> tuple[bool, str | None]:
    """Backward-compatible alias — delegates to validate_instance_name()."""
    return validate_instance_name(name, role, project_root)


def validate_instance_name_default(name: str, role: str) -> tuple[bool, str | None]:
    """Backward-compatible alias — delegates to validate_instance_name(None)."""
    return validate_instance_name(name, role)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
