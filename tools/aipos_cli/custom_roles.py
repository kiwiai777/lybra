"""AIPOS-352 / AIPOS-F32B — Gate custom role registry (single source).

AIPOS-F32B 来源修真: 角色 → builtin class 的真相只在**门注册表**一处——
即 serve 所读的凭据库(``<workspace>/.lybra/connection.json`` 的 ``tokens[]``,
经 ``load_unified_service_role_registry`` 按 home_root 统一加载, 与凭据
projects 归属同源)。自定义角色 = 注册表里带 ``role_class`` 的非内建 token 条目
(例: ``hbj-coder`` → ``executor``)。enroll(exchange 登记落盘)与
``lybra_roles_register`` 都写入这份注册表。

历史(AIPOS-352 时代): 注册表曾落 ``project.json`` 的 ``custom_roles`` 字段。
AIPOS-F32B 已废止该来源——角色是**门级**概念不是项目级(chris 工作区
project.json 为空 {}, hbj-* 实际登记在 lybra 工作区的门凭据库)。本模块
的加载路径不再读 project.json(防碎片化铁律: 角色→class 真相只有门注册表
一处; 禁 project.json / 自建映射表 / 调用方参数喂三种变体;
``_policy_matches_role`` 的 custom_roles 参数仅限测试注入, 生产路径一律
从注册表取)。

Scope resolution (AIPOS-347 link reuse, unchanged):
  custom name → builtin class → ROLE_SPECS → scopes.
  The registry NEVER grants scopes beyond the builtin class's ROLE_SPECS
  (anti-privilege-escalation). Adding a custom role = one registry entry,
  zero code changes. Built-in six roles are untouched.

F26C 分发类展开(distribute_tools.get_distributions_for_role →
resolve_role_to_class → load_custom_roles)与本模块同一加载函数——
信封解析(policy_resolver)与分发读同一份注册表(单源)。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.workspace_config import governance_paths


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


# ---------------------------------------------------------------------------
# AIPOS-F32B: gate registry loading — SAME loader the gate serve/reload uses
# (tools/mcp_server/http_sse.load_unified_service_role_registry). Lazy import:
# mcp_server depends on aipos_cli, never the other way at module level.
# ---------------------------------------------------------------------------

def _gate_registry_loader():
    """Return the gate's unified registry loader (single loader, single source)."""
    from tools.mcp_server.http_sse import load_unified_service_role_registry
    return load_unified_service_role_registry


def _registry_entry_expired(entry: dict[str, Any], now: datetime | None = None) -> bool:
    """True if a registry entry's expires_at is in the past (dead credentials
    must not count as live role registrations)."""
    expires_raw = str(entry.get("expires_at") or "").strip()
    if not expires_raw:
        return False
    try:
        expires_dt = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) > expires_dt


def load_custom_roles(project_root: str | Path) -> dict[str, dict[str, str]]:
    """Load the custom role registry from the GATE registry (AIPOS-F32B).

    Source = the gate's unified service role registry: every
    ``<project>/.lybra/connection.json`` (+ home-level ``.lybra/connection.json``)
    under the workspace's home root (= workspace parent; projects are direct
    children of home_root), loaded by the SAME loader the gate serve/reload
    uses. This is the same source credentials/projects attribution comes from.

    A custom role = a registry token entry with a non-builtin ``role`` and a
    ``role_class`` naming a builtin class. Expired entries are skipped.
    First-seen entry wins on role-name collision (deterministic: home level
    first, then sorted project dirs — the loader's own order).

    Returns dict: {custom_name: {"class": builtin_class}}.
    Empty dict if the registry is absent/unreadable (defensive; callers fall
    back to legacy direct-match semantics). project.json is NOT a source.
    """
    try:
        home_root = Path(project_root).expanduser().resolve().parent
        loader = _gate_registry_loader()
        registry = loader(home_root)
    except Exception:
        return {}
    if not registry:
        return {}

    builtins = _builtin_role_names()
    now = datetime.now(timezone.utc)
    result: dict[str, dict[str, str]] = {}
    for entry in registry.values():
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip()
        cls = str(entry.get("role_class") or "").strip()
        if not role or not cls or role in builtins or cls not in builtins:
            continue  # builtin roles resolve to themselves; no class → not a custom role
        if _registry_entry_expired(entry, now):
            continue
        if role not in result:  # first-seen wins (deterministic loader order)
            result[role] = {"class": cls}
    return result


class UnknownRoleClass(ValueError):
    """AIPOS-F102 件②: 角色 → 角色类解析不到(既非内建角色, 也不在门注册表自定义角色内)——统一失败语义 = 拒。"""


def resolve_role_to_class(
    role_name: str,
    project_root: str | Path | None = None,
    *,
    required: bool = False,
) -> str | None:
    """Resolve a role name to its built-in class — THE single role→class implementation (AIPOS-F102 件②).

    - Built-in role → returns itself.
    - Custom role (in gate registry) → returns the registered class.
    - Unknown role → None; with ``required=True`` → raises UnknownRoleClass(带出口).
      守卫/分组判定一律 required=True(统一失败语义 = 拒, 禁回落角色名自身或按"非工位"放行)。

    When project_root is None, only built-in roles are recognized.
    """
    clean = str(role_name or "").strip()
    resolved: str | None = None
    if clean in _builtin_role_names():
        # Built-in roles resolve to themselves
        resolved = clean
    elif clean and project_root is not None:
        # Custom roles: look up in the gate registry
        entry = load_custom_roles(project_root).get(clean)
        if entry:
            resolved = entry["class"]
    if resolved is None and required:
        raise UnknownRoleClass(
            f"角色 {clean!r} 的角色类不可解析: 既非 roles.schema 内建角色, 也不在门注册表"
            f"(connection.json tokens[].role_class)自定义角色内"
            f"{'' if project_root is not None else '(未给项目根, 只认内建角色)'}; "
            f"出口: lybra roles register {clean or '<name>'} --class <builtin>"
        )
    return resolved


def _declared_class_groups() -> dict[str, list[str]]:
    """AIPOS-F102 件②: roles.schema 角色类分组声明 → {内建角色类: [分组]}(注册表顺序)。缺声明 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    schema = load_schema("roles")
    known = set((schema.get("class_groups") or {}).get("values") or {})
    if not known:
        raise SchemaLoadError("roles.schema.json class_groups.values 未声明(AIPOS-F102 件②)")
    out: dict[str, list[str]] = {}
    for spec in schema.get("roles") or []:
        cls = str(spec.get("role_class") or spec.get("role") or "").strip()
        groups = spec.get("class_groups")
        if not isinstance(groups, list):
            raise SchemaLoadError(f"roles.schema.json roles[{cls}].class_groups 未声明(AIPOS-F102 件②: 缺键 = 声明缺失)")
        unknown = [g for g in groups if g not in known]
        if unknown:
            raise SchemaLoadError(f"roles.schema.json roles[{cls}].class_groups 含未声明分组 {unknown}; 值域 {sorted(known)}")
        out.setdefault(cls, [])
        out[cls].extend(g for g in groups if g not in out[cls])
    return out


def role_classes_in_group(group: str) -> tuple[str, ...]:
    """AIPOS-F102 件②: 某分组内的内建角色类(注册表顺序)。未声明的分组 = SchemaLoadError(禁静默空集)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    if group not in ((load_schema("roles").get("class_groups") or {}).get("values") or {}):
        raise SchemaLoadError(f"角色类分组 {group!r} 未在 roles.schema.json class_groups.values 声明")
    return tuple(cls for cls, groups in _declared_class_groups().items() if group in groups)


def role_in_class_group(role_name: str, group: str, project_root: str | Path | None = None) -> bool:
    """AIPOS-F102 件②: 角色(内建或自定义)是否属某分组——角色类经 resolve_role_to_class(required=True, 解析不到 = 拒), 分组读声明。"""
    return resolve_role_to_class(role_name, project_root, required=True) in role_classes_in_group(group)


def is_custom_role(role_name: str, project_root: str | Path | None = None) -> bool:
    """True if role_name is a registered custom role (not a built-in)."""
    clean = str(role_name or "").strip()
    if not clean or clean in _builtin_role_names():
        return False
    if project_root is not None:
        return clean in load_custom_roles(project_root)
    return False


def _workspace_registry_path(project_root: str | Path) -> Path:
    """The workspace's own file inside the gate's central credential library."""
    from tools.aipos_cli.service_mode import connection_path  # AIPOS-F106 件④: connection.json 定位唯一实现

    return connection_path(Path(project_root).expanduser().resolve())


def _read_registry_file(path: Path) -> dict[str, Any]:
    """Read a connection.json for a read-modify-write. Absent → empty skeleton; unreadable/malformed → ValueError
    (AIPOS-F102: 原「坏文件静默当空骨架」会让随后的写回抹掉全部既有凭据, 改 fail-closed 带路径)。"""
    if not path.exists():
        return {"config_version": 1, "tokens": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"门注册表 {path} 读不出, 拒绝改写(防覆盖既有凭据): {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"门注册表 {path} 顶层不是对象, 拒绝改写(防覆盖既有凭据)")
    return data


def _write_registry_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register_custom_role(
    project_root: str | Path,
    name: str,
    builtin_class: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Register a custom role in the GATE registry (AIPOS-F32B).

    Writes the workspace's ``.lybra/connection.json`` — a file of the gate's
    central credential library (serve reads it via the unified home_root
    loader, so the entry is visible gate-wide; project-level entries without
    explicit ``projects`` default to the source project at load time).

    Registry entry (token_ref ``svc-<name>``):
      - existing entry → token preserved, role_class/scopes updated in place;
      - no entry → a fresh service token is minted (owner-gated verb) with
        scopes DERIVED from ROLE_SPECS of the builtin class (zero scope fields
        of its own — anti-privilege-escalation).

    Returns the updated custom_roles mapping ({name: {"class": builtin_class}}).
    """
    name_ok, name_err = validate_custom_role_name(name)
    if not name_ok:
        raise ValueError(name_err)
    class_ok, class_err = validate_builtin_class(builtin_class)
    if not class_ok:
        raise ValueError(class_err)

    root = Path(project_root).expanduser().resolve()
    path = _workspace_registry_path(root)
    data = _read_registry_file(path)
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        tokens = []
        data["tokens"] = tokens

    from tools.aipos_cli.service_mode import ROLE_SPECS
    class_spec = next((s for s in ROLE_SPECS if s["role"] == builtin_class), None)
    derived_scopes = list(class_spec.get("scopes", [])) if class_spec else []

    token_ref = f"svc-{name}"
    entry: dict[str, Any] | None = None
    for item in tokens:
        if isinstance(item, dict) and str(item.get("token_ref") or "") == token_ref and str(item.get("role") or "") == name:
            entry = item
            break
    if entry is None:
        from tools.mcp_server.http_sse import _token_fingerprint  # same fingerprint scheme as the gate
        token = secrets.token_urlsafe(32)
        entry = {
            "role": name,
            "token": token,
            "token_ref": token_ref,
            "fingerprint": _token_fingerprint(token),
        }
        tokens.append(entry)
    # class truth + DERIVED scopes (never caller-supplied scope fields)
    entry["role_class"] = builtin_class
    entry["scopes"] = derived_scopes

    _write_registry_file(path, data)

    # Append trail
    _append_custom_role_trail(root, change_type="register", name=name,
                              builtin_class=builtin_class, by=by, reason=reason)
    return {name: {"class": builtin_class}}


def remove_custom_role(
    project_root: str | Path,
    name: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> dict[str, Any]:
    """Remove a custom role from the GATE registry. Idempotent.

    Removes every token entry with ``role == name`` from the workspace's
    ``.lybra/connection.json`` (the role's credentials die with it — a removed
    role must not keep live tokens). Entries for the same role in OTHER
    projects' registry files (if any) are not touched by this per-workspace
    verb.

    Returns the updated custom_roles mapping (from the gate registry).
    """
    root = Path(project_root).expanduser().resolve()
    path = _workspace_registry_path(root)
    data = _read_registry_file(path)
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        tokens = []

    kept = [t for t in tokens if not (isinstance(t, dict) and str(t.get("role") or "") == name)]
    removed = len(kept) != len(tokens)
    if removed:
        data["tokens"] = kept
        _write_registry_file(path, data)
        _append_custom_role_trail(root, change_type="unregister", name=name,
                                  builtin_class="(removed)", by=by, reason=reason)
    return load_custom_roles(root)


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
