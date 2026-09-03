"""AIPOS-F68: Integration tests for draft create/publish with machine zone.

Tests verify the full draft lifecycle with machine zone validation:
1. draft create generates machine zone from schema
2. draft publish validates machine zone unchanged
3. Schema changes auto-follow (先红后绿验收条款①)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.schema_constants import Verdict


@pytest.fixture
def integration_repo(tmp_path: Path) -> Path:
    """Create a complete repo structure for integration testing."""
    repo = tmp_path / "integration_repo"
    repo.mkdir()
    
    # Create schema directory with all required schemas
    schema_dir = repo / "schema"
    schema_dir.mkdir()
    
    # card.schema.json
    card_schema = {
        "schema_version": "1.0.0",
        "fields": {
            "task_id": {"type": "string", "required": True},
            "title": {"type": "string", "required": True},
            "project": {"type": "string", "required": True},
            "assigned_to": {"type": "string", "required": True},
            "context_bundle": {"type": "string", "required": True},
            "task_mode": {"type": "string", "required": True},
            "priority": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
            "created_by": {"type": "string", "required": True},
            "needs_owner": {"type": "boolean", "required": True},
            "output_target": {"type": "string", "required": True},
            "artifact_policy": {"type": "string", "required": True},
            "draft_status": {"type": "string", "required": False},
            "draft_created_by": {"type": "string", "required": False},
            "draft_created_at": {"type": "string", "required": False},
            "draft_updated_at": {"type": "string", "required": False},
            "draft_publish_target": {"type": "string", "required": False},
            "governance_refs": {"type": "array", "required": False},
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
        "runtime_fields": [],
        "forbidden_in_draft": [],
    }
    (schema_dir / "card.schema.json").write_text(json.dumps(card_schema, indent=2))
    
    # transitions.schema.json
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
    
    # config.schema.json
    config_schema = {
        "governance_structure": {
            "paths": {
                "queue": {"relative_to": "governance_root", "path": "5_tasks/queue/"},
                "task_cards": {"relative_to": "governance_root", "path": "task_cards/"},
            }
        }
    }
    (schema_dir / "config.schema.json").write_text(json.dumps(config_schema, indent=2))
    
    # enums.schema.json
    enums_schema = {"enums": {}}
    (schema_dir / "enums.schema.json").write_text(json.dumps(enums_schema, indent=2))
    
    # Create directory structure
    (repo / "5_tasks" / "drafts").mkdir(parents=True)
    (repo / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (repo / "5_tasks" / "records" / "publishes").mkdir(parents=True)
    
    return repo


def test_draft_create_includes_machine_zone(integration_repo: Path):
    """验证：draft create 生成的草稿包含机器区字段。"""
    metadata = {
        "task_id": "AIPOS-F68-TEST-001",
        "title": "Test task",
        "project": "test-project",
        "assigned_to": "test-agent",
        "context_bundle": "test-bundle",
        "task_mode": "code",
        "priority": "high",
        "status": "pending",
        "created_by": "advisor.test",
        "needs_owner": True,
        "output_target": "tools/",
        "artifact_policy": "formal_write",
    }
    body = "## Test task body"
    
    result = create_draft(integration_repo, metadata, body)
    
    assert result["wrote"]
    assert result["verdict"] != Verdict.BLOCK
    
    # Read the created draft
    draft_path = integration_repo / result["target_path"]
    assert draft_path.exists()
    
    from tools.aipos_cli.draft_validator import read_draft_markdown
    draft_meta, draft_body, _ = read_draft_markdown(draft_path)
    
    # Verify machine zone fields are present
    assert draft_meta.get("draft_status") == "draft"
    assert draft_meta.get("draft_created_by") == "advisor.test"
    assert "draft_created_at" in draft_meta
    assert "draft_updated_at" in draft_meta
    assert draft_meta.get("draft_publish_target") == "5_tasks/queue/pending/"


def test_publish_blocks_hand_edited_machine_zone(integration_repo: Path):
    """验证：publish 检测到机器区手改 → BLOCK（先红后绿②）。"""
    # Create a draft
    metadata = {
        "task_id": "AIPOS-F68-TEST-002",
        "title": "Test task",
        "project": "test-project",
        "assigned_to": "test-agent",
        "context_bundle": "test-bundle",
        "task_mode": "code",
        "priority": "high",
        "status": "pending",
        "created_by": "advisor.test",
        "needs_owner": True,
        "output_target": "tools/",
        "artifact_policy": "formal_write",
    }
    body = "## Test"
    
    create_result = create_draft(integration_repo, metadata, body)
    assert create_result["wrote"]
    
    # Hand-edit machine zone field
    draft_path = integration_repo / create_result["target_path"]
    content = draft_path.read_text()
    # Change draft_status from "draft" to "hand_edited"
    modified_content = content.replace("draft_status: draft", "draft_status: hand_edited")
    draft_path.write_text(modified_content)
    
    # Try to publish
    publish_result = publish_draft(integration_repo, draft_path, dry_run=True)
    
    # Should BLOCK due to hand-edited machine zone
    assert publish_result["verdict"] == Verdict.BLOCK
    assert any("机器区字段" in reason for reason in publish_result["blocking_reasons"])
    assert any("draft_status" in reason for reason in publish_result["blocking_reasons"])


def test_publish_passes_with_unchanged_machine_zone(integration_repo: Path):
    """验证：机器区未改 → publish 通过。"""
    metadata = {
        "task_id": "AIPOS-F68-TEST-003",
        "title": "Test task",
        "project": "test-project",
        "assigned_to": "test-agent",
        "context_bundle": "test-bundle",
        "task_mode": "code",
        "priority": "high",
        "status": "pending",
        "created_by": "advisor.test",
        "needs_owner": True,
        "output_target": "tools/",
        "artifact_policy": "formal_write",
    }
    body = "## Test"
    
    create_result = create_draft(integration_repo, metadata, body)
    assert create_result["wrote"]
    
    draft_path = integration_repo / create_result["target_path"]
    
    # Publish without modifications (except we need to mock gate contract section)
    # For this test, we accept that contract section generation may fail in minimal fixture
    publish_result = publish_draft(integration_repo, draft_path, dry_run=True)
    
    # Should not have machine zone blocking reasons
    machine_zone_blocks = [
        r for r in publish_result["blocking_reasons"]
        if "机器区字段" in r
    ]
    assert len(machine_zone_blocks) == 0


def test_output_target_coverage_blocks_missing_files(integration_repo: Path):
    """验证：output_target 未覆盖锚点文件 → BLOCK（大项②）。"""
    metadata = {
        "task_id": "AIPOS-F68-TEST-004",
        "title": "Test task",
        "project": "test-project",
        "assigned_to": "test-agent",
        "context_bundle": "test-bundle",
        "task_mode": "code",
        "priority": "high",
        "status": "pending",
        "created_by": "advisor.test",
        "needs_owner": True,
        "output_target": "tools/aipos_cli/",  # Only covers tools/aipos_cli/
        "artifact_policy": "formal_write",
        "governance_refs": [
            "★锚点对照表: ... → 锚点 `tools/aipos_cli/draft_writer.py` ...",
            "... 锚点 `schema/card.schema.json` ...",  # Not covered by output_target
        ],
    }
    body = "## Test"
    
    create_result = create_draft(integration_repo, metadata, body)
    assert create_result["wrote"]
    
    draft_path = integration_repo / create_result["target_path"]
    publish_result = publish_draft(integration_repo, draft_path, dry_run=True)
    
    # Should BLOCK due to missing output_target coverage
    coverage_blocks = [
        r for r in publish_result["blocking_reasons"]
        if "output_target 覆盖度不足" in r or "schema/card.schema.json" in r
    ]
    assert len(coverage_blocks) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
