"""AIPOS-330 S1 — Verb contract registry.

The single source of truth for gate verb names, required/optional parameters,
and required scopes. Names/params derived mechanically from the gate's own TOOL_HANDLERS
registry and WRITE_TOOL_DESCRIPTORS; required scopes read from the verbs.schema declaration
(AIPOS-F101 件②, same reader as gate enforcement) — never hand-written, never a parallel doc.

Design principles (S6):
- ① Contract source is singular and auto-follows: verb add/rename/param-change
  requires zero changes in consumers.
- ② Consumers use a stable interface (this module), not internal registry structure.
- ③ Validation rules are extensible: "verb name must exist" is the first rule;
  new rules (required params present, scope sufficient) can be added without
  changing the validation framework.
"""
from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Stable interface (S6②): consumers call these, never touch TOOL_HANDLERS directly.
# ---------------------------------------------------------------------------

def get_verb_registry() -> list[dict[str, Any]]:
    """Return the full verb contract registry.

    Each entry: {
        "name": str,                    # real full verb name (e.g. "lybra_audit_verdict_dry_run")
        "required_params": list[str],   # required argument names
        "optional_params": list[str],   # optional argument names
        "required_scope": str | None,   # scope needed (None = read-only, no scope)
        "confirm_pair": str | None,     # for dry_run verbs, the matching confirm verb name
        "is_confirm": bool,             # True if this is a *_confirm verb
    }

    Derived from WRITE_TOOL_DESCRIPTORS + TOOL_HANDLERS at call time.
    Adding a new verb to the gate → it appears here automatically.
    """
    # Import here to avoid circular imports and to always get the live registry.
    from tools.mcp_server.tools import (
        TOOL_HANDLERS,
        WRITE_TOOL_DESCRIPTORS,
        READ_TOOL_DESCRIPTORS,
    )

    # Build a lookup from descriptor name → descriptor
    descriptor_map: dict[str, dict[str, Any]] = {}
    for desc in WRITE_TOOL_DESCRIPTORS + READ_TOOL_DESCRIPTORS:
        descriptor_map[desc["name"]] = desc

    results: list[dict[str, Any]] = []
    for tool_name in TOOL_HANDLERS:
        desc = descriptor_map.get(tool_name, {})
        schema = desc.get("inputSchema", {})
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        required_params = sorted(k for k in properties if k in required)
        optional_params = sorted(k for k in properties if k not in required)

        # AIPOS-F101 件②: required scope 读 verbs.schema 声明(与门执法同源)
        required_scope = _scope_for_verb(tool_name)

        # Determine confirm pairing
        is_confirm = tool_name.endswith("_confirm")
        confirm_pair = None
        if tool_name.endswith("_dry_run"):
            paired_confirm = tool_name.replace("_dry_run", "_confirm")
            if paired_confirm in TOOL_HANDLERS:
                confirm_pair = paired_confirm
        elif is_confirm:
            paired_dry_run = tool_name.replace("_confirm", "_dry_run")
            if paired_dry_run in TOOL_HANDLERS:
                confirm_pair = paired_dry_run

        results.append({
            "name": tool_name,
            "required_params": required_params,
            "optional_params": optional_params,
            "required_scope": required_scope,
            "confirm_pair": confirm_pair,
            "is_confirm": is_confirm,
        })

    return results


def get_verb_names() -> set[str]:
    """Return the set of all registered verb names."""
    from tools.mcp_server.tools import TOOL_HANDLERS
    return set(TOOL_HANDLERS.keys())


def validate_verb_name(name: str) -> bool:
    """Return True if `name` is a registered verb."""
    return name in get_verb_names()


def get_verb_contract(name: str) -> dict[str, Any] | None:
    """Return the contract for a single verb, or None if not registered."""
    for entry in get_verb_registry():
        if entry["name"] == name:
            return entry
    return None


# ---------------------------------------------------------------------------
# AIPOS-338 S1: gate-contract-section verb resolver (single source for verbs).
#
# The card "认领与交回」section must derive its verb names/params from the
# registry — the publisher (draft_writer) carries ZERO verb-name literals.
# This resolver maps a stable LOGICAL ROLE to the live registry contract by
# matching operation keyword + scope, so a registry rename (that preserves the
# operation stem, e.g. *_dry_run → *_preview) auto-flows into newly published
# cards with no edits anywhere in the publisher.
# ---------------------------------------------------------------------------

# Logical roles the contract section renders. (operation_token, scope, variant)
# variant: "dry_run" | "confirm" | "plain". operation_token must appear as a
# whole word in the verb name.
_GATE_VERB_ROLES: dict[str, tuple[str, str | None, str]] = {
    "claim_dry_run": ("claim", "queue_claim", "dry_run"),
    "claim_confirm": ("claim", "queue_claim", "confirm"),
    "task_progress": ("progress", "task_progress", "plain"),
    "return_dry_run": ("return", "queue_return", "dry_run"),
    "return_confirm": ("return", "queue_return", "confirm"),
    "audit_dispatch_dry_run": ("dispatch", "audit_dispatch", "dry_run"),
    "audit_verdict_dry_run": ("verdict", "audit_verdict", "dry_run"),
    "close_dry_run": ("close", "queue_close", "dry_run"),
    "bench_audit_submit": ("bench_audit_submit", "bench_audit_submit", "dry_run"),
    "bench_audit_confirm": ("bench_audit_confirm", "bench_audit_confirm", "confirm"),
}


def _match_verb(registry, token, scope, variant):
    """Find the live verb for (token, scope, variant); scope-strict then token-fallback."""
    def _ok(entry, require_scope):
        name = entry["name"]
        if token not in name:
            return False
        if require_scope and scope is not None and entry.get("required_scope") != scope:
            return False
        if variant == "dry_run" and not name.endswith("_dry_run"):
            return False
        if variant == "confirm" and not name.endswith("_confirm"):
            return False
        if variant == "plain" and (name.endswith("_dry_run") or name.endswith("_confirm")):
            return False
        return True
    # strict: token + scope + variant
    for entry in registry:
        if _ok(entry, require_scope=True):
            return entry
    # robust fallback: token + variant (survives stem renames that keep the op token)
    for entry in registry:
        if _ok(entry, require_scope=False):
            return entry
    return None


def resolve_gate_verbs() -> dict[str, dict[str, Any] | None]:
    """Map every logical role → live registry contract (None if not registered).

    Single source for the contract section's verbs. Returns a dict keyed by the
    logical role; each value is the registry contract (name/required_params/...)
    or None when that verb is not implemented (e.g. bench verbs pre-AIPOS-336).
    """
    registry = get_verb_registry()
    resolved: dict[str, dict[str, Any] | None] = {}
    for role, (token, scope, variant) in _GATE_VERB_ROLES.items():
        resolved[role] = _match_verb(registry, token, scope, variant)
    return resolved


def resolve_gate_verb(role: str) -> dict[str, Any] | None:
    """Return the live registry contract for one logical role (None if absent)."""
    return resolve_gate_verbs().get(role)


# ---------------------------------------------------------------------------
# S6③: Extensible validation framework
# ---------------------------------------------------------------------------

class VerbValidationRule:
    """Base class for verb validation rules.

    Subclass and override `check()` to add new rules. Register via
    `register_validation_rule()`. Rules are run in registration order.
    """

    name: str = "base_rule"

    def check(self, verb_name: str, context: dict[str, Any] | None = None) -> list[str]:
        """Return list of error messages (empty = pass)."""
        return []


class VerbNameExistsRule(VerbValidationRule):
    """First rule: verb name must exist in the registry."""

    name = "verb_name_exists"

    def check(self, verb_name: str, context: dict[str, Any] | None = None) -> list[str]:
        if not validate_verb_name(verb_name):
            return [f"Verb '{verb_name}' is not registered in the gate. "
                    f"Registered verbs: {sorted(get_verb_names())}"]
        return []


class RequiredParamsPresentRule(VerbValidationRule):
    """Second rule: required params must be present when params are provided."""

    name = "required_params_present"

    def check(self, verb_name: str, context: dict[str, Any] | None = None) -> list[str]:
        if context is None:
            return []
        provided_params = set(context.get("params", {}).keys())
        contract = get_verb_contract(verb_name)
        if contract is None:
            return []  # verb_name_exists rule catches this
        missing = set(contract["required_params"]) - provided_params
        # Filter out params that are context-supplied (like dry_run_token from prior step)
        context_supplied = set(context.get("context_supplied", []))
        missing -= context_supplied
        if missing:
            return [f"Verb '{verb_name}' requires params {sorted(missing)} but they are missing."]
        return []


# Global rule registry (extensible, S6③)
_validation_rules: list[VerbValidationRule] = [
    VerbNameExistsRule(),
    RequiredParamsPresentRule(),
]


def register_validation_rule(rule: VerbValidationRule) -> None:
    """Register a new validation rule (S6③: extensible without changing framework)."""
    _validation_rules.append(rule)


def validate_verb_usage(verb_name: str, context: dict[str, Any] | None = None) -> list[str]:
    """Run all registered validation rules. Return list of errors (empty = pass)."""
    errors: list[str] = []
    for rule in _validation_rules:
        errors.extend(rule.check(verb_name, context))
    return errors


def validate_kickoff_verbs(kickoff_text: str) -> list[str]:
    """Extract all lybra_* verb names from kickoff text and validate each.

    Returns list of errors (empty = all verbs valid).
    This is the S2 validation: any hand-written verb name that doesn't exist
    in the registry is caught at generation time, not at agent runtime.
    """
    # Match lybra_ prefixed identifiers that look like verb names
    pattern = r'\b(lybra_[a-z_]+)\b'
    found_verbs = set(re.findall(pattern, kickoff_text))

    errors: list[str] = []
    for verb in sorted(found_verbs):
        if not validate_verb_name(verb):
            # Find close matches for helpful error
            all_verbs = get_verb_names()
            close = _find_close_matches(verb, all_verbs)
            msg = f"Kickoff contains unregistered verb '{verb}'."
            if close:
                msg += f" Did you mean: {', '.join(close)}?"
            else:
                msg += f" Registered verbs: {sorted(all_verbs)}"
            errors.append(msg)
    return errors


def _find_close_matches(target: str, candidates: set[str], max_results: int = 3) -> list[str]:
    """Simple prefix/stem matching for close verb names."""
    # Extract the core stem (e.g., "lybra_audit_verdict" → "audit_verdict")
    target_stem = target.replace("lybra_", "")
    matches = []
    for c in sorted(candidates):
        c_stem = c.replace("lybra_", "")
        # Check if one contains the other or they share a long prefix
        if target_stem in c_stem or c_stem in target_stem:
            matches.append(c)
        elif len(set(target_stem) & set(c_stem)) > len(target_stem) * 0.6:
            matches.append(c)
    return matches[:max_results]


# ---------------------------------------------------------------------------
# Scope-to-verb mapping —— AIPOS-F101 件②: 读 verbs.schema 声明(门工具 scope 唯一来源), 原按工具名前缀的第二份映射删除
# ---------------------------------------------------------------------------

def _scope_for_verb(verb_name: str) -> str | None:
    """门工具声明的 required_scope(schema/verbs.schema.json verbs[<工具名>]); null = 无操作 scope。

    与门执法同一读取口(tools/mcp_server/tools.py _verb_scope), 未声明 = SchemaLoadError(fail-closed)。
    """
    from tools.mcp_server.tools import _verb_scope

    return _verb_scope(verb_name)


# ---------------------------------------------------------------------------
# AIPOS-F101 件③: 退出码唯一读取口 —— verbs.schema verbs[<动词>].exit_codes({出口名: {code, meaning}})
# ---------------------------------------------------------------------------

def exit_code_in(contract: dict[str, Any], outcome: str, verb_name: str) -> int:
    """按出口名从动词声明取退出码(唯一实现; loop_driver.exit_code_for 委托本函数)。缺声明 = SchemaLoadError(fail-closed)。"""
    entry = (contract.get("exit_codes") or {}).get(outcome) if isinstance(contract, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("code"), int) or isinstance(entry.get("code"), bool):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.schema.json verbs.{verb_name}.exit_codes.{outcome} 未声明(须为 {{code: int, meaning}})")
    return int(entry["code"])


def declared_exit_code(verb_name: str, outcome: str) -> int:
    """读产品仓 verbs.schema 中 verb_name 的 exit_codes[outcome].code(artifact ingest / agent watch / loop 同一读取口)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    contract = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(verb_name)
    if not isinstance(contract, dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{verb_name} 未声明")
    return exit_code_in(contract, outcome, verb_name)


def declared_exit_codes(verb_name: str) -> dict[str, int]:
    """该动词全部声明出口 {出口名: 退出码}(CLI help / 夹具渲染用, 同一读取口)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    contract = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(verb_name)
    if not isinstance(contract, dict) or not isinstance(contract.get("exit_codes"), dict) or not contract["exit_codes"]:
        raise SchemaLoadError(f"verbs.schema.json verbs.{verb_name}.exit_codes 未声明")
    return {outcome: exit_code_in(contract, outcome, verb_name) for outcome in contract["exit_codes"]}


# ---------------------------------------------------------------------------
# Scope-to-role mapping (derived from service_mode.py ROLE_SPECS)
# ---------------------------------------------------------------------------

def get_scope_role_map(workspace_root: str | None = None) -> dict[str, list[str]]:
    """Return mapping: scope → list of roles that hold it.

    Derived from service_mode.py ROLE_SPECS at call time.
    AIPOS-352: includes custom roles from workspace registry (resolved via role_class).
    """
    from tools.aipos_cli.service_mode import ROLE_SPECS

    scope_to_roles: dict[str, list[str]] = {}
    for spec in ROLE_SPECS:
        role = spec["role"]
        for scope in spec.get("scopes", []):
            scope_to_roles.setdefault(scope, []).append(role)
    # AIPOS-352: add custom roles (they inherit their class's scopes)
    if workspace_root is not None:
        try:
            from tools.aipos_cli.custom_roles import load_custom_roles
            custom = load_custom_roles(workspace_root)
            for name, entry in custom.items():
                builtin_class = entry["class"]
                class_spec = next((s for s in ROLE_SPECS if s["role"] == builtin_class), None)
                if class_spec:
                    for scope in class_spec.get("scopes", []):
                        scope_to_roles.setdefault(scope, []).append(name)
        except Exception:
            pass
    return scope_to_roles


def get_role_scope_map() -> dict[str, list[str]]:
    """Return mapping: role → list of scopes it holds."""
    from tools.aipos_cli.service_mode import ROLE_SPECS

    return {spec["role"]: list(spec.get("scopes", [])) for spec in ROLE_SPECS}


def who_holds_scope(scope: str, workspace_root: str | None = None) -> list[str]:
    """Return list of roles that hold a given scope."""
    return get_scope_role_map(workspace_root).get(scope, [])
