"""AIPOS-F68: Machine zone derivation and validation.

Machine zone = fields derived from schema declarations that advisors cannot hand-edit.
draft_create generates them from schema; draft_publish validates they match current
schema derivation (preventing advisor hand-edits from creating a second truth source).

All values come through schema_loader (single read interface), no hardcoded literals.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.schema_loader import (
    get_branch_integration,
    get_machine_zone_fields,
    resolve_governance_path,
)


def derive_machine_zone_fields(
    metadata: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Derive machine zone field values from schema declarations.
    
    AIPOS-F68: Machine zone fields are generated from schema, never hand-written.
    All values come through schema_loader (no hardcoded paths/branch names).
    
    Args:
        metadata: Task card metadata (for context like task_id, created_by)
        repo_root: Repository root (code repo for schema_loader)
        
    Returns:
        Dictionary of machine zone field values
        
    Example machine zone fields (from card.schema machine_zone.fields):
        - draft_status: "draft"
        - draft_created_by: from metadata.created_by
        - draft_created_at: current timestamp
        - draft_updated_at: current timestamp
        - draft_publish_target: from config.schema governance_structure.paths.queue
    """
    from datetime import datetime, timezone
    
    machine = {}
    
    # Read machine zone field list from schema (single source)
    machine_fields = get_machine_zone_fields(repo_root)
    
    # Derive each field value from schema declarations
    if "draft_status" in machine_fields:
        machine["draft_status"] = "draft"
    
    if "draft_created_by" in machine_fields:
        created_by = metadata.get("created_by")
        if created_by not in (None, ""):
            machine["draft_created_by"] = created_by
    
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    
    if "draft_created_at" in machine_fields:
        machine["draft_created_at"] = timestamp
    
    if "draft_updated_at" in machine_fields:
        machine["draft_updated_at"] = timestamp
    
    if "draft_publish_target" in machine_fields:
        # Read queue path from config.schema governance_structure.paths (single source)
        # Fail-closed: schema declaration missing → raise with actionable exit
        from tools.schema_loader import get_governance_path
        queue_path_entry = get_governance_path("queue", repo_root)
        queue_relative = queue_path_entry.get("path")
        if not queue_relative:
            raise ValueError(
                "config.schema.json governance_structure.paths.queue.path 声明缺失。"
                "可执行出口: 在 schema 中声明 queue.path (如 '5_tasks/queue/')"
            )
        # Append pending/ subdirectory
        machine["draft_publish_target"] = queue_relative.rstrip("/") + "/pending/"
    
    return machine


def derive_machine_zone_纪律段(
    task_id: str,
    metadata: dict[str, Any],
    repo_root: Path,
) -> str:
    """Derive machine-generated 纪律段 (discipline section) from schema.
    
    AIPOS-F68: 纪律段 content is derived from:
    - transitions.schema.json N5.branch_integration (branch pattern, merge strategy)
    - config.schema.json governance_structure.paths (report path, records path)
    
    All values read through schema_loader, no hardcoded literals.
    
    Args:
        task_id: Task ID for path substitution
        metadata: Task card metadata (for task_mode, output_target, etc.)
        repo_root: Repository root
        
    Returns:
        Markdown string for discipline section
    """
    lines = []
    
    # Read branch integration from transitions.schema (single source)
    try:
        branch_integration = get_branch_integration(repo_root)
        branch_pattern = branch_integration.get("branch_pattern")
        if not branch_pattern:
            raise ValueError(
                "transitions.schema.json N5.branch_integration.branch_pattern 声明缺失。"
                "可执行出口: 在 schema 中声明 branch_pattern (如 'card/{task_id}')"
            )
        # Substitute {task_id} placeholder
        branch_name = branch_pattern.replace("{task_id}", task_id)
        
        lines.append("## 工作纪律")
        lines.append("")
        lines.append(f"- **分支**: `{branch_name}` (读自 transitions.schema.json N5.branch_integration.branch_pattern)")
        lines.append("- **工作起点**: 从当前 main 拉取")
        
        # Read report path from config.schema governance_structure.paths (single source)
        # Fail-closed: path resolution fails → raise with actionable exit
        task_cards_root = resolve_governance_path("task_cards", repo_root, repo_root)
        report_path = task_cards_root / task_id / "RETURN.md"
        lines.append(f"- **报告落点**: `{report_path}` (读自 config.schema governance_structure.paths.task_cards)")
        
        lines.append("- **治理仓**: 永远停在 main 分支，不 commit")
        lines.append("- **写完停手**: 等待托管/审计，不自行 push")
        
    except ValueError:
        # Re-raise ValueError (fail-closed: schema declaration missing)
        raise
    
    return "\n".join(lines)


def validate_machine_zone_unchanged(
    source_metadata: dict[str, Any],
    repo_root: Path,
) -> tuple[bool, list[str]]:
    """Validate that machine zone fields match current schema derivation.
    
    AIPOS-F68: draft_publish must validate machine zone hasn't been hand-edited.
    Compare source_metadata (from draft file) against fresh derivation from schema.
    
    Args:
        source_metadata: Metadata from draft file (potentially hand-edited)
        repo_root: Repository root
        
    Returns:
        Tuple of (is_valid, list_of_blocking_reasons)
    """
    blocking = []
    
    # Derive fresh machine zone from schema
    expected = derive_machine_zone_fields(source_metadata, repo_root)
    
    # Check each machine zone field
    machine_fields = get_machine_zone_fields(repo_root)
    for field in machine_fields:
        if field not in expected:
            continue  # Field not derived (e.g., conditional fields)
        
        expected_value = expected[field]
        actual_value = source_metadata.get(field)
        
        # For timestamp fields, allow drift (timestamps are generated at creation time)
        # Only validate non-timestamp machine zone fields
        timestamp_fields = {"draft_created_at", "draft_updated_at"}
        if field in timestamp_fields:
            # Skip timestamp validation - they're machine-generated but not stable
            continue
        
        if actual_value != expected_value:
            blocking.append(
                f"机器区字段 {field} 被手改: 期望 {expected_value!r}, 实际 {actual_value!r}. "
                f"机器区由 schema 派生，禁止手动编辑。可执行出口: 删除手改值，重新 draft create"
            )
    
    return (len(blocking) == 0, blocking)


def validate_output_target_coverage(
    metadata: dict[str, Any],
    anchor_refs: list[str],
) -> tuple[bool, list[str]]:
    """Validate that output_target covers all files mentioned in anchor_refs.
    
    AIPOS-F68 大项②: output_target 覆盖度校验 — 锚点对照表提到的文件必须被
    output_target 覆盖，否则拒收（治顾问三次漏列）。
    
    Args:
        metadata: Task card metadata (contains output_target)
        anchor_refs: List of anchor references from governance_refs/anchor_refs
        
    Returns:
        Tuple of (is_valid, list_of_blocking_reasons)
    """
    blocking = []
    
    output_target = str(metadata.get("output_target") or "").strip()
    if not output_target:
        # If no output_target, can't validate coverage (but this should be caught by required field check)
        return (True, [])
    
    # Parse output_target: comma-separated list of paths/patterns
    target_patterns = [p.strip() for p in output_target.split(",")]
    
    # Extract file paths from anchor_refs
    # anchor_refs format: "★锚点对照表: ... → 锚点 `path/to/file.py` ..."
    mentioned_files = []
    for ref in anchor_refs:
        if not isinstance(ref, str):
            continue
        # Simple extraction: look for `...` patterns that look like paths
        import re
        # Match `path/to/file.ext` or `path/to/dir/`
        for match in re.finditer(r'`([a-zA-Z0-9_./+-]+\.[a-zA-Z0-9]+|[a-zA-Z0-9_./+-]+/)`', ref):
            mentioned_files.append(match.group(1))
    
    if not mentioned_files:
        # No files mentioned in anchor_refs, coverage check passes
        return (True, [])
    
    # Check each mentioned file is covered by at least one output_target pattern
    uncovered = []
    for file_path in mentioned_files:
        covered = False
        for pattern in target_patterns:
            # Simple coverage check: file starts with pattern (treating patterns as prefixes)
            # or pattern contains wildcard directory that matches
            if file_path.startswith(pattern.rstrip("/")):
                covered = True
                break
            # Also check if pattern is a parent directory
            if pattern.endswith("/") and file_path.startswith(pattern):
                covered = True
                break
        
        if not covered:
            uncovered.append(file_path)
    
    if uncovered:
        blocking.append(
            f"output_target 覆盖度不足: 锚点对照表提到 {uncovered} 但 output_target 未覆盖. "
            f"可执行出口: 修改 output_target 添加这些路径后重新 publish"
        )
    
    return (len(blocking) == 0, blocking)
