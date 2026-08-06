"""AIPOS-352 — Workspace custom role registry.

Design (from task card AIPOS-352):
  - Custom roles are WORKSPACE DATA, not code. They live in project.json.
  - A custom role = {name → builtin_class} mapping. Example: "kiwiaiops" → "executor".
  - Scope resolution: custom name → builtin class → ROLE_SPECS (AIPOS-347 link reuse).
  - Registry carries ZERO scope fields (anti-privilege-escalation).
  - Adding a new custom role = adding one registry entry, zero code changes.
  - Built-in six roles are untouched.

Registry format in project.json:
  "custom_roles": {
    "kiwiaiops": {"class": "executor"},
    "my-auditor": {"class": "auditor"}
  }

The registry is a dict: custom_role_name → {"class": builtin_class_name}.
No scope fields anywhere in the registry. Scopes are always resolved from
ROLE_SPECS via the builtin class at call time (AIPOS-347 link).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.workspace_config import read_project_json, project_json_path, governance_paths


# The set of built-in role names — derived from ROLE_SPECS at import time.
# This is the CLOSED SET of capability classes. Custom roles must map to one of these.
def _builtin_role_names() -> set[str]:
    from tools.aipos_cli.service_mode import ROLE_SPECS
    return {spec["role"] for spec in ROLE_SPECS}


def validate_custom_role_name(name: str) -> tuple[bool, str | None]:
    """Validate a custom role name.

    Rules:
    - Non-empty, lowercase, alphanumeric + hyphens only.
    - Must NOT collide with a built-in role name.
    - Max 32 chars.
    """
    clean = str(name or "").strip()
    if not clean:
        return False, "Custom role name cannot be empty"
    if len(clean) > 32:
        return False, f"Custom role name too long (max 32 chars): {clean}"
    if not all(c.isalnum() or c == '-' for c in clean):
        return False, f"Custom role name must be alphanumeric + hyphens only: {clean}"
    if clean != clean.lower():
        return False, f"Custom role name must be lowercase: {clean}"
    if clean in _builtin_role_names():
        return False, f"Custom role name collides with built-in role: {clean}"
    return True, None


def validate_builtin_class(class_name: str) -> tuple[bool, str | None]:
    """Validate that class_name is a valid built-in role name."""
    clean = str(class_name or "").strip()
    if not clean:
        return False, "Built-in class name cannot be empty"
    if clean not in _builtin_role_names():
        return False, f"Unknown built-in class: {clean}. Valid: {sorted(_builtin_role_names())}"
    return True, None


def load_custom_roles(project_root: str | Path) -> dict[str, dict[str, str]]:
    """Load custom role registry from project.json.

    Returns dict: {custom_name: {"class": builtin_class}}.
    Empty dict if no custom_roles field exists.
    Validates on load: entries with invalid class are skipped with a warning.
    """
    project_json = read_project_json(project_root)
    raw = project_json.get("custom_roles")
    if not raw or not isinstance(raw, dict):
        return {}

    result: dict[str, dict[str, str]] = {}
    builtins = _builtin_role_names()
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        class_name = str(entry.get("class") or "").strip()
        if class_name and class_name in builtins:
            result[name] = {"class": class_name}
        # Invalid entries are silently skipped (defensive)
    return result


def resolve_role_to_class(
    role_name: str,
    project_root: str | Path | None = None,
) -> str | None:
    """Resolve a role name to its built-in class.

    - Built-in role → returns itself.
    - Custom role (in registry) → returns the mapped class.
    - Unknown role → returns None.

    When project_root is None, only built-in roles are recognized.
    """
    clean = str(role_name or "").strip()
    if not clean:
        return None
    # Built-in roles resolve to themselves
    if clean in _builtin_role_names():
        return clean
    # Custom roles: look up in registry
    if project_root is not None:
        custom = load_custom_roles(project_root)
        entry = custom.get(clean)
        if entry:
            return entry["class"]
    return None


def is_custom_role(role_name: str, project_root: str | Path | None = None) -> bool:
    """True if role_name is a registered custom role (not a built-in)."""
    clean = str(role_name or "").strip()
    if not clean or clean in _builtin_role_names():
        return False
    if project_root is not None:
        return clean in load_custom_roles(project_root)
    return False


def register_custom_role(
    project_root: str | Path,
    name: str,
    builtin_class: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Register a custom role in project.json.

    Validates name and class, writes to project.json, appends trail.
    Returns the updated custom_roles registry.
    """
    name_ok, name_err = validate_custom_role_name(name)
    if not name_ok:
        raise ValueError(name_err)
    class_ok, class_err = validate_builtin_class(builtin_class)
    if not class_ok:
        raise ValueError(class_err)

    root = Path(project_root)
    path = project_json_path(root)
    data = read_project_json(root)

    custom_roles = data.get("custom_roles")
    if not isinstance(custom_roles, dict):
        custom_roles = {}
    custom_roles[name] = {"class": builtin_class}
    data["custom_roles"] = custom_roles

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Append trail
    _append_custom_role_trail(root, change_type="register", name=name,
                              builtin_class=builtin_class, by=by, reason=reason)
    return custom_roles


def remove_custom_role(
    project_root: str | Path,
    name: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Remove a custom role from project.json. Idempotent.

    Returns the updated custom_roles registry.
    """
    root = Path(project_root)
    path = project_json_path(root)
    data = read_project_json(root)

    custom_roles = data.get("custom_roles")
    if not isinstance(custom_roles, dict) or name not in custom_roles:
        return custom_roles if isinstance(custom_roles, dict) else {}

    del custom_roles[name]
    data["custom_roles"] = custom_roles

    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _append_custom_role_trail(root, change_type="unregister", name=name,
                              builtin_class="(removed)", by=by, reason=reason)
    return custom_roles


def _append_custom_role_trail(
    project_root: Path,
    *,
    change_type: str,
    name: str,
    builtin_class: str,
    by: str,
    reason: str,
) -> Path:
    """Append-only trail for custom role changes."""
    trail = governance_paths(project_root)["decision_log"].parent / "custom_roles_log.md"
    trail.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    line = f"- {ts}  {change_type}  name={name}  class={builtin_class}  by={by}  reason={reason or '(none)'}\n"
    with trail.open("a", encoding="utf-8") as fh:
        if trail.stat().st_size == 0:
            fh.write("# Custom Roles Registry Log (append-only)\n\n")
        fh.write(line)
    return trail


def custom_roles_for_naming(project_root: str | Path) -> dict[str, str]:
    """Return custom role name → prefix mapping for naming_profile integration.

    Custom roles use their own name as the prefix (e.g., "kiwiaiops" → "kiwiaiops").
    This is merged into the naming profile's prefix_mapping.
    """
    custom = load_custom_roles(project_root)
    return {name: name for name in custom}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
