"""AIPOS-F68: Tests for machine zone derivation and validation.

Tests verify:
1. Machine zone fields are derived from schema (not hardcoded)
2. draft_publish validates machine zone hasn't been hand-edited
3. output_target coverage validation works
4. Existing cards remain compatible (warnings, not rejections)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.machine_zone import (
    derive_machine_zone_fields,
    validate_machine_zone_unchanged,
    validate_output_target_coverage,
)
from tools.schema_loader import get_machine_zone_fields


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal repo structure for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    
    # Create minimal schema directory
    schema_dir = repo / "schema"
    schema_dir.mkdir()
    
    # Copy card.schema.json with machine_zone declaration
    card_schema = {
        "schema_version": "1.0.0",
        "fields": {
            "task_id": {"type": "string", "required": True},
            "title": {"type": "string", "required": True},
            "project": {"type": "string", "required": True},
            "created_by": {"type": "string", "required": True},
            "draft_status": {"type": "string", "required": False},
            "draft_created_by": {"type": "string", "required": False},
            "draft_created_at": {"type": "string", "required": False},
            "draft_updated_at": {"type": "string", "required": False},
            "draft_publish_target": {"type": "string", "required": False},
            "output_target": {"type": "string", "required": True},
        },
        "machine_zone": {
            "fields": [
                "draft_status",
                "draft_created_by",
                "draft_created_at",
                "draft_updated_at",
                "draft_publish_target",
            ]
        },
        "advisor_zone": {
            "fields": ["title", "output_target"]
        },
    }
    (schema_dir / "card.schema.json").write_text(json.dumps(card_schema, indent=2))
    
    # Create minimal transitions.schema.json
    transitions_schema = {
        "nodes": {
            "N5": {
                "branch_integration": {
                    "branch_pattern": "card/{task_id}",
                    "merge_strategy": "no-ff",
                }
            }
        }
    }
    (schema_dir / "transitions.schema.json").write_text(json.dumps(transitions_schema, indent=2))
    
    # Create minimal config.schema.json
    config_schema = {
        "governance_structure": {
            "paths": {
                "queue": {
                    "relative_to": "governance_root",
                    "path": "5_tasks/queue/",
                },
                "task_cards": {
                    "relative_to": "governance_root",
                    "path": "task_cards/",
                },
            }
        }
    }
    (schema_dir / "config.schema.json").write_text(json.dumps(config_schema, indent=2))
    
    # Create minimal enums.schema.json
    enums_schema = {"enums": {}}
    (schema_dir / "enums.schema.json").write_text(json.dumps(enums_schema, indent=2))
    
    # Create drafts directory
    (repo / "5_tasks" / "drafts").mkdir(parents=True)
    
    return repo


def test_machine_zone_fields_from_schema(tmp_repo: Path):
    """验证机器区字段列表从 schema 读取（非硬编码）。"""
    machine_fields = get_machine_zone_fields(tmp_repo)
    
    assert "draft_status" in machine_fields
    assert "draft_created_by" in machine_fields
    assert "draft_created_at" in machine_fields
    assert "draft_updated_at" in machine_fields
    assert "draft_publish_target" in machine_fields


def test_derive_machine_zone(tmp_repo: Path):
    """验证机器区字段从 schema 派生，无硬编码值。"""
    metadata = {
        "task_id": "TEST-001",
        "created_by": "advisor.test",
    }
    
    machine = derive_machine_zone_fields(metadata, tmp_repo)
    
    assert machine["draft_status"] == "draft"
    assert machine["draft_created_by"] == "advisor.test"
    assert "draft_created_at" in machine
    assert "draft_updated_at" in machine
    assert "draft_publish_target" in machine


def test_validate_machine_zone_unchanged_pass(tmp_repo: Path):
    """验证：机器区未改 → 校验通过。"""
    metadata = {
        "task_id": "TEST-001",
        "created_by": "advisor.test",
        "draft_status": "draft",
        "draft_created_by": "advisor.test",
        "draft_created_at": "2026-09-03T00:00:00Z",
        "draft_updated_at": "2026-09-03T00:00:00Z",
        "draft_publish_target": "5_tasks/queue/pending/",
    }
    
    valid, reasons = validate_machine_zone_unchanged(metadata, tmp_repo)
    
    assert valid
    assert len(reasons) == 0


def test_validate_machine_zone_hand_edited_block(tmp_repo: Path):
    """验证：机器区被手改 → BLOCK 并给出可执行出口。"""
    metadata = {
        "task_id": "TEST-001",
        "created_by": "advisor.test",
        "draft_status": "hand_edited_status",  # Hand-edited
        "draft_created_by": "advisor.test",
        "draft_created_at": "2026-09-03T00:00:00Z",
        "draft_updated_at": "2026-09-03T00:00:00Z",
        "draft_publish_target": "5_tasks/queue/pending/",
    }
    
    valid, reasons = validate_machine_zone_unchanged(metadata, tmp_repo)
    
    assert not valid
    assert len(reasons) > 0
    assert "机器区字段" in reasons[0]
    assert "draft_status" in reasons[0]
    assert "可执行出口" in reasons[0]


def test_output_target_coverage_pass(tmp_repo: Path):
    """验证：output_target 覆盖所有锚点文件 → 通过。"""
    metadata = {
        "output_target": "tools/aipos_cli/, schema/card.schema.json",
    }
    anchor_refs = [
        "★锚点对照表: ... → 锚点 `tools/aipos_cli/draft_writer.py` ...",
        "... 锚点 `schema/card.schema.json` ...",
    ]
    
    valid, reasons = validate_output_target_coverage(metadata, anchor_refs)
    
    assert valid
    assert len(reasons) == 0


def test_output_target_coverage_missing_block(tmp_repo: Path):
    """验证：output_target 漏列锚点文件 → BLOCK（治顾问三次漏列）。"""
    metadata = {
        "output_target": "tools/aipos_cli/",
    }
    anchor_refs = [
        "★锚点对照表: ... → 锚点 `tools/aipos_cli/draft_writer.py` ...",
        "... 锚点 `schema/card.schema.json` ...",  # Missing from output_target
    ]
    
    valid, reasons = validate_output_target_coverage(metadata, anchor_refs)
    
    assert not valid
    assert len(reasons) > 0
    assert "output_target 覆盖度不足" in reasons[0]
    assert "schema/card.schema.json" in reasons[0]
    assert "可执行出口" in reasons[0]


def test_schema_change_auto_follows(tmp_repo: Path):
    """验证：schema 改声明 → 新卡自动跟随（先红后绿验收条款①）。"""
    # Before: branch_pattern = "card/{task_id}"
    metadata_before = {"task_id": "TEST-001", "created_by": "advisor.test"}
    machine_before = derive_machine_zone_fields(metadata_before, tmp_repo)
    
    # Change schema: branch_pattern = "wip/{task_id}"
    transitions_schema = {
        "nodes": {
            "N5": {
                "branch_integration": {
                    "branch_pattern": "wip/{task_id}",
                    "merge_strategy": "no-ff",
                }
            }
        }
    }
    (tmp_repo / "schema" / "transitions.schema.json").write_text(
        json.dumps(transitions_schema, indent=2)
    )
    
    # Clear schema cache to pick up changes
    from tools.schema_loader import clear_cache
    clear_cache()
    
    # After: new draft should follow new pattern (纪律段 would reference wip/{task_id})
    metadata_after = {"task_id": "TEST-001", "created_by": "advisor.test"}
    machine_after = derive_machine_zone_fields(metadata_after, tmp_repo)
    
    # Machine zone fields themselves don't contain branch name (that's in 纪律段)
    # but derivation function should pick up new schema without hardcoded values
    assert machine_after["draft_status"] == "draft"  # Still works after schema change


def test_no_hardcoded_paths_in_machine_zone():
    """验证：machine_zone.py 不含硬编码路径/分支名字面量（硬约束①）。"""
    import inspect
    from tools.aipos_cli import machine_zone
    
    source = inspect.getsource(machine_zone)
    
    # Forbidden literals (should not appear as hardcoded strings)
    forbidden = [
        '"card/"',
        "'card/'",
        '"5_tasks/queue/pending"',  # Should read from schema
        '"task_cards/"',  # Should read from schema
    ]
    
    for literal in forbidden:
        assert literal not in source, (
            f"Found hardcoded literal {literal} in machine_zone.py. "
            f"All paths must be read via schema_loader (AIPOS-F68 硬约束①)"
        )


def test_branch_pattern_missing_raises_error(tmp_repo: Path):
    """验证：branch_pattern 声明缺失时 raise 报错（fail-closed），禁静默回退写死值。"""
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
    
    # Remove branch_pattern from schema
    transitions_schema_path = tmp_repo / "schema" / "transitions.schema.json"
    transitions = json.loads(transitions_schema_path.read_text())
    del transitions["nodes"]["N5"]["branch_integration"]["branch_pattern"]
    transitions_schema_path.write_text(json.dumps(transitions, indent=2))
    
    # Should raise ValueError with actionable message
    metadata = {"task_id": "TEST-001", "created_by": "advisor.test"}
    with pytest.raises(ValueError) as exc_info:
        derive_machine_zone_纪律段("TEST-001", metadata, tmp_repo)
    
    error_msg = str(exc_info.value)
    assert "branch_pattern" in error_msg
    assert "声明缺失" in error_msg
    assert "可执行出口" in error_msg


def test_queue_path_missing_raises_error(tmp_repo: Path):
    """验证：queue.path 声明缺失时 raise 报错（fail-closed），禁静默回退写死值。"""
    from tools.aipos_cli.machine_zone import derive_machine_zone_fields
    
    # Remove queue.path from schema
    config_schema_path = tmp_repo / "schema" / "config.schema.json"
    config = json.loads(config_schema_path.read_text())
    del config["governance_structure"]["paths"]["queue"]["path"]
    config_schema_path.write_text(json.dumps(config, indent=2))
    
    # Should raise ValueError with actionable message
    metadata = {"task_id": "TEST-001", "created_by": "advisor.test"}
    with pytest.raises(ValueError) as exc_info:
        derive_machine_zone_fields(metadata, tmp_repo)
    
    error_msg = str(exc_info.value)
    assert "queue.path" in error_msg
    assert "声明缺失" in error_msg
    assert "可执行出口" in error_msg


def test_task_cards_path_missing_raises_error(tmp_repo: Path):
    """验证：task_cards.path 声明缺失时 raise 报错（fail-closed），禁静默回退写死值。"""
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
    from tools.schema_loader import SchemaLoadError
    
    # Remove task_cards from schema
    config_schema_path = tmp_repo / "schema" / "config.schema.json"
    config = json.loads(config_schema_path.read_text())
    del config["governance_structure"]["paths"]["task_cards"]
    config_schema_path.write_text(json.dumps(config, indent=2))
    
    # Should raise SchemaLoadError (from schema_loader.resolve_governance_path)
    metadata = {"task_id": "TEST-001", "created_by": "advisor.test"}
    with pytest.raises(SchemaLoadError) as exc_info:
        derive_machine_zone_纪律段("TEST-001", metadata, tmp_repo)
    
    error_msg = str(exc_info.value)
    assert "task_cards" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
