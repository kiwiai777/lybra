"""Lybra Schema Loader - Single source of truth loader for all schema files.

This is the ONLY schema loading implementation. Per DESIGN v2 §6:
"一机制一实现总红线" - adding a second schema loader = audit FAIL.

All code (gate, CLI, connectors) must use this loader. Cross-language implementations
must be locked with conformance tests against these schemas.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

# Schema file paths relative to repo root
SCHEMA_DIR = Path("schema")
CARD_SCHEMA_FILE = SCHEMA_DIR / "card.schema.json"
ENUMS_SCHEMA_FILE = SCHEMA_DIR / "enums.schema.json"
VERBS_SCHEMA_FILE = SCHEMA_DIR / "verbs.schema.json"
CONFIG_SCHEMA_FILE = SCHEMA_DIR / "config.schema.json"
TRANSITIONS_SCHEMA_FILE = SCHEMA_DIR / "transitions.schema.json"
ROLES_SCHEMA_FILE = SCHEMA_DIR / "roles.schema.json"
DISTRIBUTION_SCHEMA_FILE = SCHEMA_DIR / "distribution.schema.json"

SchemaType = Literal["card", "enums", "verbs", "config", "transitions", "roles", "distribution"]


class SchemaLoadError(Exception):
    """Raised when schema loading fails."""
    pass


def _find_repo_root(start: Path | None = None) -> Path:
    """Find repository root by looking for schema/ directory.
    
    Args:
        start: Starting directory (defaults to current file's location)
        
    Returns:
        Repository root path
        
    Raises:
        SchemaLoadError: If repo root cannot be found
    """
    if start is None:
        # Start from this file's location
        start = Path(__file__).parent.parent
    
    current = Path(start).resolve()
    
    # Walk up looking for schema/ directory
    for _ in range(10):  # Limit search depth
        schema_path = current / SCHEMA_DIR
        if schema_path.is_dir():
            return current
        parent = current.parent
        if parent == current:  # Reached filesystem root
            break
        current = parent
    
    raise SchemaLoadError(f"Could not find schema/ directory from {start}")


def code_repo_schema_root() -> Path:
    """AIPOS-F18-fix2 F-B-1: 运行代码所在仓根 = schema/ 真实所在根。

    门以 release 目录运行(AIPOS-333 运行时隔离), 产品仓/dev 仓根均含 schema/;
    治理工作区根(5_tasks 所在根)没有 schema/。声明类读取(toggle/模式/记录位置)
    必须以本根解析, 否则真实门语境必 SchemaLoadError→声明静默失效。
    唯一实现;调用方应在调用时动态 import 以便测试替换。
    """
    return Path(__file__).resolve().parents[1]


def _load_json_file(path: Path) -> dict[str, Any]:
    """Load and parse JSON file.
    
    Args:
        path: Path to JSON file
        
    Returns:
        Parsed JSON data
        
    Raises:
        SchemaLoadError: If file cannot be loaded or parsed
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise SchemaLoadError(f"Schema file not found: {path}") from e
    except json.JSONDecodeError as e:
        raise SchemaLoadError(f"Invalid JSON in schema file {path}: {e}") from e
    except Exception as e:
        raise SchemaLoadError(f"Error loading schema file {path}: {e}") from e


@lru_cache(maxsize=8)
def load_schema(schema_type: SchemaType, repo_root: Path | None = None) -> dict[str, Any]:
    """Load a schema file by type.
    
    This is the single entry point for loading any schema. Results are cached.
    
    Args:
        schema_type: Type of schema to load
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Schema data as dictionary
        
    Raises:
        SchemaLoadError: If schema cannot be loaded
        
    Example:
        >>> card_schema = load_schema("card")
        >>> enums = load_schema("enums")
    """
    if repo_root is None:
        repo_root = _find_repo_root()
    
    schema_files = {
        "card": CARD_SCHEMA_FILE,
        "enums": ENUMS_SCHEMA_FILE,
        "verbs": VERBS_SCHEMA_FILE,
        "config": CONFIG_SCHEMA_FILE,
        "transitions": TRANSITIONS_SCHEMA_FILE,
        "roles": ROLES_SCHEMA_FILE,
        "distribution": DISTRIBUTION_SCHEMA_FILE,
    }
    
    schema_file = schema_files.get(schema_type)
    if schema_file is None:
        raise SchemaLoadError(f"Unknown schema type: {schema_type}")
    
    schema_path = repo_root / schema_file
    return _load_json_file(schema_path)


def resolve_enum_ref(enum_name: str, repo_root: Path | None = None) -> list[str]:
    """Resolve an enum name to its values from enums.schema.json (single source).

    Args:
        enum_name: Name of the enum in enums.schema.json
        repo_root: Repository root path

    Returns:
        List of valid enum values

    Raises:
        SchemaLoadError: If enum not found in enums.schema.json
    """
    enums_schema = load_schema("enums", repo_root)
    enum_def = enums_schema.get("enums", {}).get(enum_name)
    if enum_def is None:
        raise SchemaLoadError(
            f"$enum reference '{enum_name}' not found in enums.schema.json. "
            f"Available enums: {sorted(enums_schema.get('enums', {}).keys())}"
        )
    return [item["value"] for item in enum_def.get("values", [])]


def resolve_field_enum(field_schema: dict[str, Any], repo_root: Path | None = None) -> list[str] | None:
    """Resolve enum values from a field schema dict.

    Handles both:
    - Legacy inline "enum": [...] (for backward compat during transition)
    - New "$enum": "name" reference (resolved via enums.schema.json)

    Returns None if field has no enum constraint.
    Raises SchemaLoadError if $enum references a non-existent enum.
    """
    if "$enum" in field_schema:
        return resolve_enum_ref(field_schema["$enum"], repo_root)
    if "enum" in field_schema:
        return list(field_schema["enum"])
    return None


def get_card_field_schema(field_name: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Get schema definition for a specific card field.
    
    Args:
        field_name: Name of the field
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Field schema dict or None if field not defined
        
    Example:
        >>> task_id_schema = get_card_field_schema("task_id")
        >>> print(task_id_schema["required"])  # True
    """
    card_schema = load_schema("card", repo_root)
    return card_schema.get("fields", {}).get(field_name)


def get_enum_values(enum_name: str, repo_root: Path | None = None) -> list[str]:
    """Get valid values for an enum type.
    
    Args:
        enum_name: Name of the enum (e.g., "queue_state", "verdict")
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        List of valid enum values
        
    Raises:
        SchemaLoadError: If enum not found
        
    Example:
        >>> states = get_enum_values("queue_state")
        >>> print(states)  # ["pending", "claimed", "returned", ...]
    """
    enums_schema = load_schema("enums", repo_root)
    enum_def = enums_schema.get("enums", {}).get(enum_name)
    
    if enum_def is None:
        raise SchemaLoadError(f"Unknown enum: {enum_name}")
    
    return [item["value"] for item in enum_def.get("values", [])]


def get_required_card_fields(repo_root: Path | None = None) -> list[str]:
    """Get list of required card fields.
    
    Args:
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        List of required field names
        
    Example:
        >>> required = get_required_card_fields()
        >>> print("task_id" in required)  # True
    """
    card_schema = load_schema("card", repo_root)
    fields = card_schema.get("fields", {})
    return [
        name for name, spec in fields.items()
        if spec.get("required", False)
    ]


def get_forbidden_draft_fields(repo_root: Path | None = None) -> list[str]:
    """Get list of fields forbidden in draft cards (runtime fields).
    
    Args:
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        List of forbidden field names
        
    Example:
        >>> forbidden = get_forbidden_draft_fields()
        >>> print("claim_id" in forbidden)  # True
    """
    card_schema = load_schema("card", repo_root)
    return list(card_schema.get("forbidden_in_draft", []))


def get_verb_contract(verb_name: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Get contract definition for a verb.
    
    Args:
        verb_name: Name of the verb (e.g., "lybra_draft_publish")
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Verb contract dict or None if verb not defined
        
    Example:
        >>> publish_contract = get_verb_contract("lybra_draft_publish")
        >>> print(publish_contract["phases"])  # ["dry_run", "confirm"]
    """
    verbs_schema = load_schema("verbs", repo_root)
    return verbs_schema.get("verbs", {}).get(verb_name)


def get_transition_node(node_id: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Get state machine node definition.
    
    Args:
        node_id: Node identifier (e.g., "N0", "N1", ...)
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Node definition dict or None if not found
        
    Example:
        >>> n0 = get_transition_node("N0")
        >>> print(n0["name"])  # "publish"
    """
    transitions_schema = load_schema("transitions", repo_root)
    nodes = transitions_schema.get("main_flow", {}).get("nodes", [])
    
    for node in nodes:
        if node.get("node_id") == node_id:
            return node
    
    return None


def get_branch_integration(repo_root: Path | None = None) -> dict[str, Any]:
    """Read N5.branch_integration declaration from transitions.schema.json (single source).
    
    AIPOS-C3C: 卡分支整合的唯一真相。finalize 合并信息生成与
    deployment_authorization._task_id_from_commit_subject 归属解析读同一份声明:
    生成什么格式就解析什么格式, 代码零写死。
    
    Args:
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        The branch_integration declaration dict
        
    Raises:
        SchemaLoadError: If missing or malformed
    """
    transitions_schema = load_schema("transitions", repo_root)
    nodes = transitions_schema.get("nodes", {})
    n5 = nodes.get("N5", {}) if isinstance(nodes, dict) else {}
    branch_integration = n5.get("branch_integration")
    if not isinstance(branch_integration, dict):
        raise SchemaLoadError(
            "transitions.schema.json N5.branch_integration missing or invalid"
        )
    return branch_integration


def get_task_mode_routing(task_mode: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Get workflow routing for a task_mode.
    
    Args:
        task_mode: Task mode (e.g., "code", "docs")
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Routing definition dict or None if mode not found
        
    Example:
        >>> code_route = get_task_mode_routing("code")
        >>> print(code_route["flow"])  # "N0 → N1 → N2 → N3 → N4 → N5 → N6"
    """
    transitions_schema = load_schema("transitions", repo_root)
    routes = transitions_schema.get("task_mode_routing", {}).get("routes", {})
    return routes.get(task_mode)


# ---------------------------------------------------------------------------
# Role registry accessors (AIPOS-R4B-1 / DESIGN v2 §5-6)
# Single source for role category definitions: scopes / tool package / naming.
# Replaces ROLE_TOOL_MAPPING (distribute), _resolve_role_scopes (gate),
# DEFAULT_PREFIX_MAPPING (naming_profile), and 20 scattered instance-name literals.
# ---------------------------------------------------------------------------

def _find_role_spec(role: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Find a role entry by name in the roles registry."""
    roles_schema = load_schema("roles", repo_root)
    for spec in roles_schema.get("roles", []):
        if spec.get("role") == role:
            return spec
    return None


def get_role_spec(role: str, repo_root: Path | None = None) -> dict[str, Any] | None:
    """Get the full definition for a role category (single source).

    Returns the role entry dict (role/token_ref/scopes/naming/tool_package) or
    None if the role is not a builtin registry role. Custom (workspace) roles
    are not listed here — they resolve to a builtin class via custom_roles.
    """
    return _find_role_spec(role, repo_root)


def get_role_scopes(
    role: str, *, role_class: str | None = None, repo_root: Path | None = None
) -> list[str]:
    """Resolve the current scopes for a role from the registry (single source).

    AIPOS-347 link (call-time scope resolution) now reads the registry instead of
    service_mode.ROLE_SPECS. AIPOS-352 custom-role reuse: when ``role`` is not a
    builtin registry role and ``role_class`` is given, scopes come from the
    builtin class. Returns [] for an unknown role (fail-open-empty, same as the
    deleted _resolve_role_scopes).
    """
    spec = _find_role_spec(role, repo_root)
    if spec is None and role_class:
        spec = _find_role_spec(role_class, repo_root)
    if spec is None:
        return []
    return list(spec.get("scopes", []))


def get_role_tool_package(role: str, repo_root: Path | None = None) -> dict[str, Any]:
    """Get the distribute tool-package spec for a role (extensions + skills).

    Replaces ROLE_TOOL_MAPPING in distribute_tools.py. Raises SchemaLoadError for
    roles without a distributed tool package (owner/owner-dispatch/copilot/planner).
    """
    spec = _find_role_spec(role, repo_root)
    if spec is None:
        raise SchemaLoadError(
            f"Unknown role: {role}. Builtin roles: {get_all_role_names(repo_root)}"
        )
    pkg = spec.get("tool_package")
    if not pkg:
        raise SchemaLoadError(
            f"Role {role!r} has no distributed tool package. "
            f"Roles with a tool package: {get_roles_with_tool_package(repo_root)}"
        )
    return dict(pkg)


def get_role_naming_prefix(role: str, repo_root: Path | None = None) -> str | None:
    """Get the instance-name prefix for a role (e.g. 'exec' for 'executor').

    Single source for the role->prefix map; replaces naming_profile's hardcoded
    DEFAULT_PREFIX_MAPPING. Returns None if the role has no prefix.
    """
    spec = _find_role_spec(role, repo_root)
    if spec is None:
        return None
    naming = spec.get("naming") or {}
    prefix = naming.get("prefix")
    return prefix if prefix else None


def get_all_role_names(repo_root: Path | None = None) -> list[str]:
    """List all builtin role category names in the registry."""
    roles_schema = load_schema("roles", repo_root)
    return [spec.get("role") for spec in roles_schema.get("roles", []) if spec.get("role")]


def get_roles_with_tool_package(repo_root: Path | None = None) -> list[str]:
    """List builtin roles that carry a distributed tool package."""
    roles_schema = load_schema("roles", repo_root)
    return [
        spec.get("role")
        for spec in roles_schema.get("roles", [])
        if spec.get("tool_package")
    ]


def get_role_naming_template(repo_root: Path | None = None) -> str:
    """Get the instance-name derivation template (product rule).

    Default '{prefix}.{project}.{host}'. Values for project/host come from
    workspace config (project.json), not the registry.
    """
    roles_schema = load_schema("roles", repo_root)
    return roles_schema.get("naming", {}).get("template", "{prefix}.{project}.{host}")


def get_builtin_role_classes(repo_root: Path | None = None) -> list[str]:
    """List builtin role classes that custom_roles may map to."""
    roles_schema = load_schema("roles", repo_root)
    custom = roles_schema.get("custom_roles", {}) or {}
    return list(custom.get("builtin_classes") or get_all_role_names(repo_root))


def get_machine_zone_fields(repo_root: Path | None = None) -> list[str]:
    """Get list of machine-zone fields from card.schema (AIPOS-F68 single source).
    
    Machine zone = fields derived from schema declarations that advisors cannot
    hand-edit. draft_create generates them; draft_publish validates they match.
    
    Returns:
        List of field names in machine zone
    """
    card_schema = load_schema("card", repo_root)
    machine_zone = card_schema.get("machine_zone", {})
    return list(machine_zone.get("fields", []))


def get_advisor_zone_fields(repo_root: Path | None = None) -> list[str]:
    """Get list of advisor-zone fields from card.schema (AIPOS-F68 single source).
    
    Advisor zone = fields requiring human judgment that machine cannot generate.
    
    Returns:
        List of field names in advisor zone
    """
    card_schema = load_schema("card", repo_root)
    advisor_zone = card_schema.get("advisor_zone", {})
    return list(advisor_zone.get("fields", []))


def get_machine_zone_body_sections(repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Get list of machine-generated body sections from card.schema (AIPOS-F68).
    
    Returns:
        List of section definitions (section_name, description, source)
    """
    card_schema = load_schema("card", repo_root)
    machine_zone = card_schema.get("machine_zone", {})
    return list(machine_zone.get("body_sections", []))


# ---------------------------------------------------------------------------
# Config value accessors (AIPOS-R4B-1 / DESIGN v2 §5-4)
# Single source for port / URL defaults. Replaces 35 hardcoded 7117/7118 refs.
# ---------------------------------------------------------------------------

def get_config_port(name: str, repo_root: Path | None = None) -> int:
    """Get a service port default from config.schema (single source).

    Names: board_default (7117), gate_default (7118), mcp_server_default (7118),
    auditor_loop_default (7119), tunnel_default (7120).
    """
    config_schema = load_schema("config", repo_root)
    ports = config_schema.get("ports", {}) or {}
    if name not in ports:
        raise SchemaLoadError(
            f"Unknown port name: {name}. Known: {sorted(ports.keys())}"
        )
    return int(ports[name])


def get_config_default_gate_url(repo_root: Path | None = None) -> str:
    """Get the default local gate URL from config.schema (single source)."""
    config_schema = load_schema("config", repo_root)
    urls = config_schema.get("urls", {}) or {}
    url = urls.get("gate_local")
    if not url:
        raise SchemaLoadError("config.schema urls.gate_local is missing")
    return str(url)


def is_field_defined(field_name: str, repo_root: Path | None = None) -> bool:
    """Check if a field is defined in card schema.
    
    Args:
        field_name: Field name to check
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        True if field is defined, False otherwise
        
    Example:
        >>> is_field_defined("task_id")  # True
        >>> is_field_defined("bogus_field")  # False
    """
    return get_card_field_schema(field_name, repo_root) is not None


def validate_field_value(field_name: str, value: Any, repo_root: Path | None = None) -> tuple[bool, str | None]:
    """Validate a field value against its schema.
    
    Args:
        field_name: Field name
        value: Value to validate
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Tuple of (is_valid, error_message)
        
    Example:
        >>> valid, error = validate_field_value("status", "pending")
        >>> print(valid)  # True
        >>> valid, error = validate_field_value("status", "invalid_state")
        >>> print(error)  # "Value not in allowed enum: ..."
    """
    field_schema = get_card_field_schema(field_name, repo_root)
    
    if field_schema is None:
        return False, f"Field '{field_name}' is not defined in schema"
    
    field_type = field_schema.get("type")
    
    # Type checking
    if field_type == "string" and not isinstance(value, str):
        return False, f"Field '{field_name}' must be a string, got {type(value).__name__}"
    elif field_type == "boolean" and not isinstance(value, bool):
        return False, f"Field '{field_name}' must be a boolean, got {type(value).__name__}"
    elif field_type == "array" and not isinstance(value, list):
        return False, f"Field '{field_name}' must be an array, got {type(value).__name__}"
    elif field_type == "object" and not isinstance(value, dict):
        return False, f"Field '{field_name}' must be an object, got {type(value).__name__}"
    
    # Enum validation (resolves $enum references from enums.schema.json)
    allowed_values = resolve_field_enum(field_schema, repo_root)
    if allowed_values is not None:
        if value not in allowed_values:
            return False, f"Field '{field_name}' value '{value}' not in allowed values: {allowed_values}"
    
    return True, None


def get_all_defined_fields(repo_root: Path | None = None) -> list[str]:
    """Get list of all defined card fields.
    
    Args:
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        List of all field names defined in schema
        
    Example:
        >>> fields = get_all_defined_fields()
        >>> print(len(fields))  # 100+
    """
    card_schema = load_schema("card", repo_root)
    return list(card_schema.get("fields", {}).keys())


def clear_cache() -> None:
    """Clear the schema cache.
    
    Useful for testing or when schemas are updated at runtime.
    """
    load_schema.cache_clear()


# Version introspection
def get_schema_version(schema_type: SchemaType, repo_root: Path | None = None) -> str:
    """Get version of a schema file.
    
    Args:
        schema_type: Type of schema
        repo_root: Repository root path (auto-detected if not provided)
        
    Returns:
        Schema version string
    """
    schema = load_schema(schema_type, repo_root)
    return schema.get("schema_version", "unknown")


# ---------------------------------------------------------------------------
# Cross-validation (AIPOS-SCHEMA-UNIFY-1: single-source enforcement)
# ---------------------------------------------------------------------------

def _walk_for_enum_literals(obj: Any, path: str) -> list[str]:
    """Recursively walk a JSON structure and find residual 'enum' array literals."""
    findings = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            current_path = f"{path}.{key}" if path else key
            if key == "enum" and isinstance(val, list):
                findings.append(f"{current_path}: {val}")
            else:
                findings.extend(_walk_for_enum_literals(val, current_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            findings.extend(_walk_for_enum_literals(item, f"{path}[{i}]"))
    return findings


def _walk_for_enum_refs(obj: Any, path: str) -> list[tuple[str, str]]:
    """Recursively walk a JSON structure and find all '$enum' references."""
    findings = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            current_path = f"{path}.{key}" if path else key
            if key == "$enum" and isinstance(val, str):
                findings.append((current_path, val))
            else:
                findings.extend(_walk_for_enum_refs(val, current_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            findings.extend(_walk_for_enum_refs(item, f"{path}[{i}]"))
    return findings


def cross_validate_schemas(repo_root: Path | None = None) -> list[str]:
    """Cross-validate schema package for single-source compliance.

    Checks:
    1. All '$enum' references resolve to existing enums in enums.schema.json
    2. No residual 'enum' array literals exist outside enums.schema.json

    Returns:
        List of error messages (empty = all good)

    Raises:
        SchemaLoadError: If any validation error is found (with all errors listed)
    """
    errors: list[str] = []

    # Get known enum names
    enums_schema = load_schema("enums", repo_root)
    known_enums = set(enums_schema.get("enums", {}).keys())

    # Check schemas that should use $enum references (not enums.schema.json itself)
    schemas_to_check: list[SchemaType] = ["card", "verbs", "config", "transitions", "roles"]

    for schema_type in schemas_to_check:
        try:
            schema_data = load_schema(schema_type, repo_root)
        except SchemaLoadError:
            continue  # Schema file might not exist yet

        schema_path = f"{schema_type}.schema.json"

        # Check 1: No residual 'enum' array literals
        residual = _walk_for_enum_literals(schema_data, schema_path)
        for r in residual:
            errors.append(
                f"RESIDUAL ENUM LITERAL in {r} — "
                f"must use {{\"$enum\": \"<enum_name>\"}} referencing enums.schema.json"
            )

        # Check 2: All '$enum' references resolve
        refs = _walk_for_enum_refs(schema_data, schema_path)
        for ref_path, enum_name in refs:
            if enum_name not in known_enums:
                errors.append(
                    f"BROKEN $enum REF at {ref_path}: '{enum_name}' "
                    f"not found in enums.schema.json. Available: {sorted(known_enums)}"
                )

    if errors:
        raise SchemaLoadError(
            f"Schema cross-validation failed ({len(errors)} error(s)):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    return errors


def validate_all_enum_refs(repo_root: Path | None = None) -> bool:
    """Validate all $enum references resolve. Raises SchemaLoadError on failure.

    Convenience wrapper around cross_validate_schemas() that returns True on success.
    """
    cross_validate_schemas(repo_root)
    return True


def get_governance_structure(repo_root: Path | None = None) -> dict[str, Any]:
    """Read the governance directory tree from config.schema (single source).

    AIPOS-R6M: 命名与路径一律读 config.schema 治理目录树, 代码零写死。
    Returns the ``governance_structure`` object (with ``paths`` and
    ``timeline_enforcement``). Raises SchemaLoadError if missing/malformed.
    """
    config = load_schema("config", repo_root)
    gs = config.get("governance_structure")
    if not isinstance(gs, dict):
        raise SchemaLoadError("config.schema governance_structure missing or invalid")
    return gs


def get_governance_path(key: str, repo_root: Path | None = None) -> dict[str, Any]:
    """Get a single governance directory-tree path entry from config.schema.

    Args:
        key: path key under ``governance_structure.paths`` (e.g. ``stage_archive``,
            ``governance_docs``, ``decision_log_dir``, ``records``, ``tasks_root``).

    Returns the entry dict (``relative_to`` + ``path`` + ...). Raises SchemaLoadError
    if the key is missing or not a dict.
    """
    gs = get_governance_structure(repo_root)
    paths = gs.get("paths")
    if not isinstance(paths, dict):
        raise SchemaLoadError("config.schema governance_structure.paths missing or invalid")
    entry = paths.get(key)
    if not isinstance(entry, dict):
        raise SchemaLoadError(f"config.schema governance_structure.paths.{key} missing or invalid")
    return entry


def resolve_governance_path(key: str, governance_root: Path, repo_root: Path | None = None) -> Path:
    """Resolve a governance directory-tree path to an absolute path under governance_root.

    Reads the ``path`` from config.schema (single source) and joins it under
    ``governance_root``. Never hardcodes a directory name.
    """
    entry = get_governance_path(key, repo_root)
    raw = str(entry.get("path") or "").strip()
    rel = raw.strip("/")
    if not rel:
        raise SchemaLoadError(f"config.schema governance_structure.paths.{key}.path is empty")
    return Path(governance_root) / rel


# Convenience exports
__all__ = [
    "SchemaLoadError",
    "load_schema",
    "resolve_enum_ref",
    "resolve_field_enum",
    "get_card_field_schema",
    "get_enum_values",
    "get_required_card_fields",
    "get_forbidden_draft_fields",
    "get_verb_contract",
    "get_transition_node",
    "get_task_mode_routing",
    "get_branch_integration",
    "get_role_spec",
    "get_role_scopes",
    "get_role_tool_package",
    "get_role_naming_prefix",
    "get_all_role_names",
    "get_roles_with_tool_package",
    "get_role_naming_template",
    "get_builtin_role_classes",
    "get_config_port",
    "get_config_default_gate_url",
    "get_governance_structure",
    "get_governance_path",
    "resolve_governance_path",
    "is_field_defined",
    "validate_field_value",
    "get_all_defined_fields",
    "get_schema_version",
    "clear_cache",
    "cross_validate_schemas",
    "validate_all_enum_refs",
    "get_machine_zone_fields",
    "get_advisor_zone_fields",
    "get_machine_zone_body_sections",
]
