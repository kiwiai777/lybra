"""AIPOS-R8C: Project-declarable card validation policy loader.

Product provides the executor; each project declares its own rules in its
governance repo. The product understands ZERO project-specific semantics —
it only performs "field existence + value domain" checks.

Zero-invasion: when no card_policy is declared for a project, behavior is
identical to pre-R8C (no extra required fields, no extra checks).

Declaration file format (JSON):
{
  "schema_version": "1.0.0",
  "description": "...",
  "rules": [
    {
      "field": "anchor_refs",
      "required": true,
      "values_from": "transitions.schema.json#main_flow.nodes[].node_id+transitions.schema.json#cross_cutting",
      "message": "anchor_refs is required; values must be valid transition nodes"
    },
    {
      "field": "some_field",
      "required": false,
      "values": ["a", "b", "c"],
      "message": "some_field must be one of: a, b, c"
    }
  ]
}

values_from syntax:
  <schema_file>#<json_path>
  - schema_file: filename under schema/ (e.g., transitions.schema.json)
  - json_path: dot-separated path with optional [] for array iteration
    e.g., main_flow.nodes[].node_id → extract node_id from each element of nodes array
  - Multiple sources can be combined with + (union of values)
  - Special: "cross_cutting" as the path means "keys of the cross_cutting object"
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tools.schema_loader import load_schema, _find_repo_root, SchemaLoadError


# ---------------------------------------------------------------------------
# Declaration file loading
# ---------------------------------------------------------------------------

def get_project_card_policy_path(
    governance_root: Path | str,
    project_id: str | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """Resolve the card_policy declaration file path for a project.

    Reads config.schema.json's multi_project_support.project_registry.structure.card_policy
    to find the relative path, then resolves it under governance_root.

    Returns None if card_policy is not declared (zero-invasion: no policy = no extra checks).
    """
    governance_root = Path(governance_root)
    if repo_root is None:
        repo_root = _find_repo_root()

    config = load_schema("config", repo_root)
    registry = config.get("multi_project_support", {}).get("project_registry", {})
    structure = registry.get("structure", {})
    card_policy_rel = structure.get("card_policy")

    if not card_policy_rel or not isinstance(card_policy_rel, str):
        return None

    # The card_policy field in structure is a description of the field type.
    # The actual per-project declaration path is stored in a project-level config.
    # We look for it in governance_root/.lybra/card_policy.json or
    # governance_root/card_policy.json (project declares its own).
    #
    # Actually, per the card design: the card_policy field in project_registry.structure
    # is the *type description* for the registry. The actual path for each project
    # comes from the project's own configuration. Let's check governance_root for
    # a card_policy declaration file.

    # Try standard locations within governance_root
    candidates = [
        governance_root / "card_policy.json",
        governance_root / ".lybra" / "card_policy.json",
        governance_root / "config" / "card_policy.json",
    ]
    # AIPOS-C3B 大项C⑥: also check 2_projects/<project_id>/ (multi-project layout)
    if project_id:
        candidates.append(governance_root / "2_projects" / project_id / "card_policy.json")

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def load_card_policy_declaration(declaration_path: Path) -> dict[str, Any]:
    """Load and parse a card_policy declaration file.

    Returns the parsed declaration dict.
    Raises SchemaLoadError if the file is invalid.
    """
    try:
        data = json.loads(declaration_path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SchemaLoadError(f"Card policy declaration not found: {declaration_path}") from e
    except json.JSONDecodeError as e:
        raise SchemaLoadError(f"Invalid JSON in card policy declaration {declaration_path}: {e}") from e

    if not isinstance(data, dict):
        raise SchemaLoadError(f"Card policy declaration must be a JSON object: {declaration_path}")

    rules = data.get("rules")
    if rules is not None and not isinstance(rules, list):
        raise SchemaLoadError(f"Card policy 'rules' must be a list: {declaration_path}")

    return data


def get_card_policy_rules(
    governance_root: Path | str,
    project_id: str | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Load the card policy rules for a project.

    Returns an empty list if no declaration exists (zero-invasion).
    """
    declaration_path = get_project_card_policy_path(governance_root, project_id, repo_root)
    if declaration_path is None:
        return []

    try:
        declaration = load_card_policy_declaration(declaration_path)
    except SchemaLoadError:
        return []

    rules = declaration.get("rules")
    if not isinstance(rules, list):
        return []

    return rules


def get_card_policy_all_declared_fields(
    governance_root: Path | str,
    project_id: str | None = None,
    repo_root: Path | None = None,
) -> set[str]:
    """AIPOS-C3B 大项C⑥: 返回 card_policy 声明的所有字段名(含 rules + _suspended_rules)。

    用于 unknown-field 白名单:policy 声明的字段不应被 card.schema 报 unknown。
    这解决了死锁二号:policy 声明字段(如 anchor_refs)不在 card.schema 中,
    不加白名单会撞 unknown-field;不加 rule 会撞 policy required = 死锁。
    """
    declaration_path = get_project_card_policy_path(governance_root, project_id, repo_root)
    if declaration_path is None:
        return set()

    try:
        declaration = load_card_policy_declaration(declaration_path)
    except SchemaLoadError:
        return set()

    fields: set[str] = set()
    # Collect from active rules
    rules = declaration.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            fname = rule.get("field")
            if isinstance(fname, str) and fname:
                fields.add(fname)
    # Collect from suspended rules (AIPOS-C3B 大项C⑥)
    suspended = declaration.get("_suspended_rules")
    if isinstance(suspended, list):
        for rule in suspended:
            fname = rule.get("field")
            if isinstance(fname, str) and fname:
                fields.add(fname)
    return fields


# ---------------------------------------------------------------------------
# values_from resolution
# ---------------------------------------------------------------------------

def _resolve_json_path(data: Any, path: str) -> list[str]:
    """Resolve a dot-separated JSON path, extracting values.

    Supports:
    - Simple dot navigation: "a.b.c"
    - Array iteration: "a.b[].c" (extracts c from each element of array b)
    - Special: if path is a simple key and data is a dict, return dict keys

    Returns a list of string values.
    """
    parts = path.split(".")
    current = [data]

    for part in parts:
        if not part:
            continue

        next_values: list[Any] = []

        if part.endswith("[]"):
            # Array iteration: e.g., "nodes[]"
            array_key = part[:-2]
            for item in current:
                if isinstance(item, dict):
                    arr = item.get(array_key)
                    if isinstance(arr, list):
                        next_values.extend(arr)
        else:
            for item in current:
                if isinstance(item, dict):
                    val = item.get(part)
                    if val is not None:
                        if isinstance(val, list):
                            next_values.extend(val)
                        else:
                            next_values.append(val)

        current = next_values

    # Convert all leaf values to strings
    result = []
    for item in current:
        if isinstance(item, (str, int, float, bool)):
            result.append(str(item))
        elif isinstance(item, dict):
            # If we ended on a dict, use its keys
            result.extend(str(k) for k in item.keys())

    return result


def resolve_values_from(
    values_from: str,
    repo_root: Path | None = None,
) -> list[str]:
    """Resolve a values_from specification to a list of valid values.

    Syntax: <schema_file>#<json_path>[+<schema_file>#<json_path>...]

    Examples:
    - "transitions.schema.json#main_flow.nodes[].node_id"
    - "transitions.schema.json#cross_cutting"
    - "transitions.schema.json#main_flow.nodes[].node_id+transitions.schema.json#cross_cutting"
    """
    if repo_root is None:
        repo_root = _find_repo_root()

    all_values: list[str] = []
    sources = values_from.split("+")

    for source in sources:
        source = source.strip()
        if "#" not in source:
            continue

        schema_file, json_path = source.split("#", 1)
        schema_file = schema_file.strip()
        json_path = json_path.strip()

        # Determine schema type from filename
        schema_type_map = {
            "card.schema.json": "card",
            "enums.schema.json": "enums",
            "verbs.schema.json": "verbs",
            "config.schema.json": "config",
            "transitions.schema.json": "transitions",
            "roles.schema.json": "roles",
            "distribution.schema.json": "distribution",
        }

        schema_type = schema_type_map.get(schema_file)
        if schema_type is None:
            continue

        try:
            schema_data = load_schema(schema_type, repo_root)
        except SchemaLoadError:
            continue

        values = _resolve_json_path(schema_data, json_path)
        all_values.extend(values)

    return sorted(set(all_values))


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def evaluate_card_policy_rules(
    metadata: dict[str, Any],
    governance_root: Path | str,
    project_id: str | None = None,
    repo_root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Evaluate card policy rules against draft metadata.

    Returns:
        Tuple of (blocking_reasons, warnings)
        blocking_reasons: list of BLOCK messages (from project-defined rules)
        warnings: list of warning messages

    Zero-invasion: if no rules are declared, returns ([], []).
    """
    rules = get_card_policy_rules(governance_root, project_id, repo_root)
    if not rules:
        return [], []

    blocking: list[str] = []
    warnings: list[str] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue

        field_name = rule.get("field")
        if not field_name or not isinstance(field_name, str):
            continue

        required = rule.get("required", False)
        message = rule.get("message", f"Card policy violation for field '{field_name}'")
        values_from = rule.get("values_from")
        values_literal = rule.get("values")

        field_value = metadata.get(field_name)
        is_present = field_value is not None and field_value != ""

        # Check required
        if required and not is_present:
            blocking.append(message)
            continue

        # If field is not present and not required, skip value checks
        if not is_present:
            continue

        # Check value domain
        allowed_values: list[str] = []

        if values_from:
            allowed_values = resolve_values_from(values_from, repo_root)

        if values_literal and isinstance(values_literal, list):
            allowed_values = [str(v) for v in values_literal]

        if allowed_values:
            # Handle both single values and arrays
            if isinstance(field_value, list):
                invalid = [str(v) for v in field_value if str(v) not in allowed_values]
                if invalid:
                    blocking.append(
                        f"{message}; invalid value(s): {invalid}; "
                        f"allowed: {allowed_values}"
                    )
            else:
                if str(field_value) not in allowed_values:
                    blocking.append(
                        f"{message}; value '{field_value}' not in allowed: {allowed_values}"
                    )

    return blocking, warnings


# ---------------------------------------------------------------------------
# Draft create: pre-populate placeholder fields from card_policy
# ---------------------------------------------------------------------------

def get_card_policy_placeholder_fields(
    governance_root: Path | str,
    project_id: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Get placeholder values for required fields from card_policy rules.

    Used by draft create to pre-populate placeholder fields in the skeleton.
    Returns a dict of field_name -> placeholder_value.

    For required fields with values_from, the placeholder is an empty array []
    (or empty string if the field is typically scalar).
    """
    rules = get_card_policy_rules(governance_root, project_id, repo_root)
    placeholders: dict[str, Any] = {}

    for rule in rules:
        if not isinstance(rule, dict):
            continue

        field_name = rule.get("field")
        if not field_name or not isinstance(field_name, str):
            continue

        required = rule.get("required", False)
        if not required:
            continue

        # Determine placeholder type
        values_from = rule.get("values_from")
        values_literal = rule.get("values")

        if values_from:
            # If values come from an array source, use empty array
            # Check if the source path contains [] (array extraction)
            if "[]" in values_from:
                placeholders[field_name] = []
            else:
                placeholders[field_name] = ""
        elif values_literal and isinstance(values_literal, list):
            placeholders[field_name] = []
        else:
            placeholders[field_name] = ""

    return placeholders


def get_task_id_pattern(
    governance_root: Path | str,
    project_id: str | None = None,
    repo_root: Path | None = None,
) -> str | None:
    """AIPOS-F5: 读项目声明的 task_id_pattern (卡号形状声明位, R8C 同构)。

    "什么长得像本项目的卡号"是项目属性, 声明一处 (card_policy.json 的
    task_id_pattern), 归属解析与一切判卡号的调用点只读它。lybra 声明
    `AIPOS-[A-Z0-9]+`, 别的项目声明自己的 (换项目三问过关)。

    返回声明的正则片段字符串 (如 "AIPOS-[A-Z0-9]+"), 或 None 表示项目未声明
    (调用方必须出声报错 —— C2 原则: 无内置默认模式)。
    """
    declaration_path = get_project_card_policy_path(governance_root, project_id, repo_root)
    if declaration_path is None:
        return None
    try:
        declaration = load_card_policy_declaration(declaration_path)
    except SchemaLoadError:
        return None
    pattern = declaration.get("task_id_pattern")
    if isinstance(pattern, str) and pattern.strip():
        return pattern.strip()
    return None


__all__ = [
    "get_project_card_policy_path",
    "load_card_policy_declaration",
    "get_card_policy_rules",
    "resolve_values_from",
    "evaluate_card_policy_rules",
    "get_card_policy_placeholder_fields",
    "get_task_id_pattern",
]
