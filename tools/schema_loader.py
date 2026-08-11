"""Lybra Schema Loader - Single source of truth loader for all schema files.

This is the ONLY schema loading implementation. Per LOOP-REDESIGN v2 §6:
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

SchemaType = Literal["card", "enums", "verbs", "config", "transitions"]


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


@lru_cache(maxsize=5)
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
    }
    
    schema_file = schema_files.get(schema_type)
    if schema_file is None:
        raise SchemaLoadError(f"Unknown schema type: {schema_type}")
    
    schema_path = repo_root / schema_file
    return _load_json_file(schema_path)


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
    
    # Enum validation
    if "enum" in field_schema:
        allowed_values = field_schema["enum"]
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


# Convenience exports
__all__ = [
    "SchemaLoadError",
    "load_schema",
    "get_card_field_schema",
    "get_enum_values",
    "get_required_card_fields",
    "get_forbidden_draft_fields",
    "get_verb_contract",
    "get_transition_node",
    "get_task_mode_routing",
    "is_field_defined",
    "validate_field_value",
    "get_all_defined_fields",
    "get_schema_version",
    "clear_cache",
]
