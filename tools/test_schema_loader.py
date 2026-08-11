#!/usr/bin/env python3
"""Test schema loader functionality."""

import sys
from pathlib import Path

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.schema_loader import (
    load_schema,
    get_card_field_schema,
    get_enum_values,
    get_required_card_fields,
    get_forbidden_draft_fields,
    is_field_defined,
    validate_field_value,
    get_all_defined_fields,
)


def test_load_schemas():
    """Test loading all schema files."""
    print("Testing schema loading...")
    
    schemas = ["card", "enums", "verbs", "config", "transitions"]
    for schema_type in schemas:
        schema = load_schema(schema_type)
        assert schema is not None, f"Failed to load {schema_type} schema"
        assert "schema_version" in schema, f"{schema_type} schema missing version"
        print(f"  ✓ {schema_type}.schema.json loaded (version {schema['schema_version']})")
    
    print("✓ All schemas loaded successfully\n")


def test_card_fields():
    """Test card field queries."""
    print("Testing card field queries...")
    
    # Test field definition check
    assert is_field_defined("task_id"), "task_id should be defined"
    assert not is_field_defined("bogus_field_xyz"), "bogus field should not be defined"
    print("  ✓ Field definition check works")
    
    # Test getting field schema
    task_id_schema = get_card_field_schema("task_id")
    assert task_id_schema is not None, "task_id schema should exist"
    assert task_id_schema["required"] == True, "task_id should be required"
    assert task_id_schema["type"] == "string", "task_id should be string"
    print("  ✓ Field schema retrieval works")
    
    # Test required fields
    required = get_required_card_fields()
    assert "task_id" in required, "task_id should be required"
    assert "title" in required, "title should be required"
    assert "project" in required, "project should be required"
    print(f"  ✓ Required fields: {len(required)} fields")
    
    # Test forbidden draft fields
    forbidden = get_forbidden_draft_fields()
    assert "claim_id" in forbidden, "claim_id should be forbidden in draft"
    assert "claimed_by" in forbidden, "claimed_by should be forbidden in draft"
    print(f"  ✓ Forbidden draft fields: {len(forbidden)} fields")
    
    # Test all defined fields
    all_fields = get_all_defined_fields()
    assert len(all_fields) > 50, "Should have many defined fields"
    print(f"  ✓ Total defined fields: {len(all_fields)}")
    
    print("✓ Card field queries work\n")


def test_enums():
    """Test enum value queries."""
    print("Testing enum queries...")
    
    # Test queue states
    queue_states = get_enum_values("queue_state")
    assert "pending" in queue_states, "pending should be a queue state"
    assert "claimed" in queue_states, "claimed should be a queue state"
    assert "completed" in queue_states, "completed should be a queue state"
    print(f"  ✓ Queue states: {queue_states}")
    
    # Test verdicts
    verdicts = get_enum_values("verdict")
    assert "PASS" in verdicts, "PASS should be a verdict"
    assert "FAIL" in verdicts, "FAIL should be a verdict"
    assert "BLOCK" in verdicts, "BLOCK should be a verdict"
    print(f"  ✓ Verdicts: {verdicts}")
    
    # Test task modes
    task_modes = get_enum_values("task_mode")
    assert "code" in task_modes, "code should be a task mode"
    assert "docs" in task_modes, "docs should be a task mode"
    print(f"  ✓ Task modes: {task_modes}")
    
    print("✓ Enum queries work\n")


def test_field_validation():
    """Test field value validation."""
    print("Testing field value validation...")
    
    # Test valid status
    valid, error = validate_field_value("status", "pending")
    assert valid, f"pending should be valid status: {error}"
    print("  ✓ Valid status accepted")
    
    # Test invalid status
    valid, error = validate_field_value("status", "invalid_state")
    assert not valid, "invalid_state should be rejected"
    assert "not in allowed values" in error, "Should mention allowed values"
    print(f"  ✓ Invalid status rejected: {error}")
    
    # Test valid priority
    valid, error = validate_field_value("priority", "high")
    assert valid, f"high should be valid priority: {error}"
    print("  ✓ Valid priority accepted")
    
    # Test type mismatch
    valid, error = validate_field_value("task_id", 12345)
    assert not valid, "task_id should reject non-string"
    assert "must be a string" in error, "Should mention type error"
    print(f"  ✓ Type mismatch detected: {error}")
    
    print("✓ Field validation works\n")


def test_draft_validation_integration():
    """Test integration with draft validator."""
    print("Testing draft validation integration...")
    
    try:
        from tools.aipos_cli.draft_validator import validate_draft_metadata
        
        # Test with valid metadata
        valid_metadata = {
            "task_id": "TEST-001",
            "title": "Test task",
            "project": "lybra",
            "assigned_to": "exec.lybra.kiwiai-dev",
            "context_bundle": "exec.lybra.kiwiai-dev",
            "task_mode": "code",
            "priority": "high",
            "status": "pending",
            "created_by": "advisor.lybra.kiwiai-dev",
            "needs_owner": False,
            "output_target": "repo",
            "artifact_policy": "formal_write",
        }
        
        result = validate_draft_metadata(Path.cwd(), valid_metadata)
        assert result["verdict"] in ["PASS", "WARN"], f"Valid metadata should pass: {result['blocking_reasons']}"
        print(f"  ✓ Valid metadata: {result['verdict']}")
        
        # Test with unknown field
        invalid_metadata = valid_metadata.copy()
        invalid_metadata["bogus_field_name"] = "test"
        
        result = validate_draft_metadata(Path.cwd(), invalid_metadata)
        has_warning = any("Unknown field" in w for w in result["warnings"])
        assert has_warning, "Should warn about unknown field"
        print("  ✓ Unknown field detected")
        
        # Test with typo (referenced_files vs materialize_refs)
        typo_metadata = valid_metadata.copy()
        typo_metadata["materialize_refs"] = []  # Should suggest "referenced_files"
        
        result = validate_draft_metadata(Path.cwd(), typo_metadata)
        # This might be a warning or blocking depending on closeness of match
        print(f"  ✓ Typo field handling: verdict={result['verdict']}")
        
        # Test with forbidden runtime field
        runtime_metadata = valid_metadata.copy()
        runtime_metadata["claim_id"] = "claim_123"
        
        result = validate_draft_metadata(Path.cwd(), runtime_metadata)
        assert result["verdict"] == "BLOCK", "Runtime field should block"
        assert any("forbidden runtime-state field" in r for r in result["blocking_reasons"])
        print("  ✓ Forbidden runtime field blocked")
        
        # Test with missing required field
        incomplete_metadata = valid_metadata.copy()
        del incomplete_metadata["title"]
        
        result = validate_draft_metadata(Path.cwd(), incomplete_metadata)
        assert result["verdict"] == "BLOCK", "Missing required field should block"
        assert any("Missing required field: title" in r for r in result["blocking_reasons"])
        print("  ✓ Missing required field blocked")
        
        print("✓ Draft validation integration works\n")
        
    except ImportError as e:
        print(f"  ⚠ Draft validator not available: {e}\n")


def main():
    """Run all tests."""
    print("=" * 70)
    print("Schema Loader Test Suite (AIPOS-R0)")
    print("=" * 70)
    print()
    
    try:
        test_load_schemas()
        test_card_fields()
        test_enums()
        test_field_validation()
        test_draft_validation_integration()
        
        print("=" * 70)
        print("✓ ALL TESTS PASSED")
        print("=" * 70)
        return 0
        
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
