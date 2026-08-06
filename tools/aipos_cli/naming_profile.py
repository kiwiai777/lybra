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
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.workspace_config import (
    read_project_json,
    project_json_path,
    governance_paths,
)


# ---------------------------------------------------------------------------
# Default naming profile — matches current Lybra conventions
# ---------------------------------------------------------------------------

DEFAULT_PREFIX_MAPPING: dict[str, str] = {
    "executor": "exec",
    "auditor": "audit",
    "owner": "owner",
    "copilot": "copilot",
    "planner": "planner",
    "owner-dispatch": "owner-dispatch",
}


def default_naming_profile() -> dict[str, Any]:
    """Default naming profile. Matches current Lybra conventions.

    prefix_mapping: role -> display prefix (planner maps to 'planner' by default;
        projects that use 'advisor' for planner set it via set_prefix_mapping).
    project_segment: the canonical project name used in instance names.
    project_segment_aliases: alternative names accepted for the project segment
        (e.g. 'kiwiaiops' as alias for 'lybra').
    host_segment: the canonical host name used in instance names.
    host_segment_aliases: alternative names accepted for the host segment.
    """
    return {
        "prefix_mapping": dict(DEFAULT_PREFIX_MAPPING),
        "project_segment": "lybra",
        "project_segment_aliases": [],
        "host_segment": "kiwiai-dev",
        "host_segment_aliases": [],
    }


# ---------------------------------------------------------------------------
# S2: Read / write naming profile (workspace data in project.json)
# ---------------------------------------------------------------------------

def get_naming_profile(project_root: str | Path) -> dict[str, Any]:
    """Read naming_profile from project.json. Absent/invalid fields -> defaults.

    Backward compatible: old projects without naming_profile get defaults.
    Partial profiles are merged with defaults (missing fields filled in).
    """
    project_json = read_project_json(project_root)
    profile = project_json.get("naming_profile")
    if profile is None or not isinstance(profile, dict):
        return default_naming_profile()
    # Merge with defaults
    result = default_naming_profile()
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
) -> str:
    """Generate the canonical instance name for a role: <prefix>.<project>.<host>.

    All three segments come from the naming profile (alias layer). No hardcoded values.
    Raises ValueError if the role has no prefix mapping.
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
    host = profile["host_segment"]
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


def validate_instance_name_with_profile(
    name: str,
    role: str,
    project_root: str | Path,
) -> tuple[bool, str | None]:
    """Validate instance name using the naming profile (alias layer).

    Returns (is_valid, error_message).

    Rules:
    - Three-part dotted structure (product rule, not configurable).
    - Role prefix must match the naming profile's prefix_mapping for the role.
    - Project segment must be the canonical value or one of its aliases,
      AND must not be a role name (common mistake: audit.auditor.xxx).
    - Host segment must be the canonical value or one of its aliases.
    """
    if not name or not name.strip():
        return False, "Instance name cannot be empty"

    parts = name.split(".")
    if len(parts) != 3:
        return False, f"Instance name must have 3 parts (<role>.<project>.<machine>), got {len(parts)}: {name}"

    role_part, project_part, machine_part = parts
    profile = get_naming_profile(project_root)

    # Role prefix check — from alias layer, NOT hardcoded
    accepted_prefixes = _accepted_prefixes_for_role(role, profile)
    if accepted_prefixes and role_part not in accepted_prefixes:
        expected = profile["prefix_mapping"].get(role, "?")
        return False, f"Role prefix mismatch: expected '{expected}' for role '{role}', got '{role_part}' in '{name}'"

    # Project part must not be a role name (common mistake: audit.auditor.xxx)
    if project_part in ROLE_NAMES:
        return False, f"Project part '{project_part}' is a role name, not a project name in '{name}'"

    # Project segment check — from alias layer
    accepted_projects = _accepted_project_segments(profile)
    if project_part not in accepted_projects:
        return False, (
            f"Project part '{project_part}' is not the project segment "
            f"('{profile['project_segment']}') or any of its aliases "
            f"({sorted(accepted_projects)}) in '{name}'"
        )

    # Host segment check — from alias layer
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
# Backward-compatible validate_instance_name (reads naming profile)
#
# This replaces the hardcoded version in service_mode.py. It requires a
# project_root to read the naming profile. When called without one (legacy
# callers), it falls back to the DEFAULT naming profile (zero-hardcode
# preserved via default_naming_profile()).
# ---------------------------------------------------------------------------

_DEFAULT_PROFILE_CACHE: dict[str, Any] | None = None


def _default_profile() -> dict[str, Any]:
    """Lazy singleton default profile (for callers without a project_root)."""
    global _DEFAULT_PROFILE_CACHE
    if _DEFAULT_PROFILE_CACHE is None:
        _DEFAULT_PROFILE_CACHE = default_naming_profile()
    return _DEFAULT_PROFILE_CACHE


def validate_instance_name_default(name: str, role: str) -> tuple[bool, str | None]:
    """Validate using the default naming profile (no project_root needed).

    This is the drop-in replacement for the old hardcoded validate_instance_name.
    It uses default_naming_profile() which matches current Lybra conventions.
    For workspace-aware validation (with aliases), use validate_instance_name_with_profile().
    """
    if not name or not name.strip():
        return False, "Instance name cannot be empty"

    parts = name.split(".")
    if len(parts) != 3:
        return False, f"Instance name must have 3 parts (<role>.<project>.<machine>), got {len(parts)}: {name}"

    role_part, project_part, machine_part = parts
    profile = _default_profile()

    # Role prefix check — from alias layer (default), NOT hardcoded
    accepted_prefixes = _accepted_prefixes_for_role(role, profile)
    if accepted_prefixes and role_part not in accepted_prefixes:
        expected = profile["prefix_mapping"].get(role, "?")
        return False, f"Role prefix mismatch: expected '{expected}' for role '{role}', got '{role_part}' in '{name}'"

    # Project part must not be a role name
    if project_part in ROLE_NAMES:
        return False, f"Project part '{project_part}' is a role name, not a project name in '{name}'"

    # Project/machine non-empty
    if not project_part.strip():
        return False, f"Project part cannot be empty in '{name}'"
    if not machine_part.strip():
        return False, f"Machine part cannot be empty in '{name}'"

    return True, None


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
