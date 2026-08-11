#!/usr/bin/env python3
"""Verification test for AIPOS-R0 N0 validation acceptance criteria."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.aipos_cli.draft_validator import validate_draft_file

# Use real repo root for schema access
REPO_ROOT = Path(__file__).parent.parent


def create_test_draft(content: str, name: str = "test-draft.md") -> Path:
    """Create a test draft file in real repo."""
    drafts_dir = REPO_ROOT / "5_tasks" / "drafts" / "_test"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    
    draft_path = drafts_dir / name
    draft_path.write_text(content, encoding="utf-8")
    return draft_path


def cleanup_test_drafts():
    """Clean up test drafts."""
    test_dir = REPO_ROOT / "5_tasks" / "drafts" / "_test"
    if test_dir.exists():
        import shutil
        shutil.rmtree(test_dir)


def test_misspelled_field():
    """验收断言1: 一张卡写拼错字段(如 referenced_files)→ draft_publish 明确指出该字段"""
    print("Test 1: Misspelled field detection")
    print("-" * 60)
    
    # Create draft with misspelled field
    draft_content = """---
task_id: TEST-MISSPELL
title: Test misspelled field
project: lybra
assigned_to: exec.lybra.kiwiai-dev
context_bundle: exec.lybra.kiwiai-dev
task_mode: code
priority: high
status: pending
created_by: advisor.lybra.kiwiai-dev
needs_owner: false
output_target: repo
artifact_policy: formal_write
materialize_refs:
- some/file.txt
---
# Test Task

This draft has a misspelled field 'materialize_refs' instead of 'referenced_files'.
"""
    
    draft_path = create_test_draft(draft_content, "test-misspell.md")
    result = validate_draft_file(REPO_ROOT, draft_path)
    
    print(f"Verdict: {result['verdict']}")
    print(f"Blocking reasons: {result['blocking_reasons']}")
    print(f"Warnings: {result['warnings']}")
    
    # Check that misspelled field is detected
    has_misspell_detection = (
        any("materialize_refs" in str(r) for r in result['blocking_reasons']) or
        any("materialize_refs" in str(w) for w in result['warnings'])
    )
    
    if has_misspell_detection:
        print("✓ PASS: Misspelled field 'materialize_refs' detected")
        print()
        return True
    else:
        print("✗ FAIL: Misspelled field not detected")
        print()
        return False


def test_missing_required_field():
    """验收断言2: 缺 session_policy 等必填 → 发卡明确处理,不再无声 WARN 堆积"""
    print("Test 2: Missing required field detection")
    print("-" * 60)
    
    # Create draft missing required fields
    draft_content = """---
task_id: TEST-MISSING
title: Test missing fields
project: lybra
assigned_to: exec.lybra.kiwiai-dev
context_bundle: exec.lybra.kiwiai-dev
task_mode: code
priority: high
status: pending
created_by: advisor.lybra.kiwiai-dev
---
# Test Task

This draft is missing needs_owner, output_target, and artifact_policy.
"""
    
    draft_path = create_test_draft(draft_content, "test-missing.md")
    result = validate_draft_file(REPO_ROOT, draft_path)
    
    print(f"Verdict: {result['verdict']}")
    print(f"Blocking reasons:")
    for reason in result['blocking_reasons']:
        print(f"  - {reason}")
    
    # Check that missing required fields are blocked
    has_missing_needs_owner = any("needs_owner" in r for r in result['blocking_reasons'])
    has_missing_output_target = any("output_target" in r for r in result['blocking_reasons'])
    has_missing_artifact_policy = any("artifact_policy" in r for r in result['blocking_reasons'])
    
    if result['verdict'] == 'BLOCK' and has_missing_needs_owner and has_missing_output_target and has_missing_artifact_policy:
        print("✓ PASS: Missing required fields cause BLOCK verdict with clear messages")
        print()
        return True
    else:
        print("✗ FAIL: Missing required fields not properly blocked")
        print()
        return False


def test_runtime_field_forbidden():
    """验收断言: Runtime fields in draft cause BLOCK"""
    print("Test 3: Runtime fields forbidden in draft")
    print("-" * 60)
    
    # Create draft with runtime fields
    draft_content = """---
task_id: TEST-RUNTIME
title: Test runtime field
project: lybra
assigned_to: exec.lybra.kiwiai-dev
context_bundle: exec.lybra.kiwiai-dev
task_mode: code
priority: high
status: pending
created_by: advisor.lybra.kiwiai-dev
needs_owner: false
output_target: repo
artifact_policy: formal_write
claim_id: claim_123456
claimed_by: some.agent
claimed_at: '2026-08-11T10:00:00Z'
---
# Test Task

This draft incorrectly contains runtime fields.
"""
    
    draft_path = create_test_draft(draft_content, "test-runtime.md")
    result = validate_draft_file(REPO_ROOT, draft_path)
    
    print(f"Verdict: {result['verdict']}")
    print(f"Blocking reasons:")
    for reason in result['blocking_reasons']:
        print(f"  - {reason}")
    
    # Check that runtime fields are blocked
    has_claim_id_block = any("claim_id" in r and "forbidden" in r for r in result['blocking_reasons'])
    has_claimed_by_block = any("claimed_by" in r and "forbidden" in r for r in result['blocking_reasons'])
    
    if result['verdict'] == 'BLOCK' and has_claim_id_block and has_claimed_by_block:
        print("✓ PASS: Runtime fields properly blocked")
        print()
        return True
    else:
        print("✗ FAIL: Runtime fields not properly blocked")
        print()
        return False


def test_valid_draft():
    """Test that valid draft passes validation"""
    print("Test 4: Valid draft acceptance")
    print("-" * 60)
    
    # Create valid draft with proper slug
    draft_content = """---
task_id: test-valid
title: Test valid draft
project: lybra
assigned_to: exec.lybra.kiwiai-dev
agent_instance: exec.lybra.kiwiai-dev
context_bundle: exec.lybra.kiwiai-dev
task_mode: code
task_class: simple
priority: high
status: pending
created_by: advisor.lybra.kiwiai-dev
needs_owner: false
output_target: repo
artifact_policy: formal_write
session_policy: default
context_isolation: shared
memory_scope: none
referenced_files:
- some/file.txt
governance_refs:
- governance/LOOP-REDESIGN.md
---
# Test Task

This is a valid draft with all required fields and no errors.
"""
    
    # Use proper draft location outside _test to avoid path mismatch
    drafts_dir = REPO_ROOT / "5_tasks" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    draft_path = drafts_dir / "test-valid.md"
    draft_path.write_text(draft_content, encoding="utf-8")
    
    result = validate_draft_file(REPO_ROOT, draft_path)
    
    # Clean up this specific draft
    if draft_path.exists():
        draft_path.unlink()
    
    print(f"Verdict: {result['verdict']}")
    if result['warnings']:
        print(f"Warnings: {result['warnings'][:3]}")  # Show first 3 warnings
    if result['blocking_reasons']:
        print(f"Blocking reasons: {result['blocking_reasons']}")
    
    if result['verdict'] in ['PASS', 'WARN'] and not result['blocking_reasons']:
        print("✓ PASS: Valid draft accepted")
        print()
        return True
    else:
        print("✗ FAIL: Valid draft rejected")
        print()
        return False


def test_enum_validation():
    """Test that enum values are validated"""
    print("Test 5: Enum value validation")
    print("-" * 60)
    
    # Create draft with invalid enum value
    draft_content = """---
task_id: TEST-ENUM
title: Test enum validation
project: lybra
assigned_to: exec.lybra.kiwiai-dev
context_bundle: exec.lybra.kiwiai-dev
task_mode: invalid_mode
priority: ultra_super_high
status: pending
created_by: advisor.lybra.kiwiai-dev
needs_owner: false
output_target: repo
artifact_policy: formal_write
---
# Test Task

This draft has invalid enum values.
"""
    
    draft_path = create_test_draft(draft_content, "test-enum.md")
    result = validate_draft_file(REPO_ROOT, draft_path)
    
    print(f"Verdict: {result['verdict']}")
    print(f"Blocking reasons:")
    for reason in result['blocking_reasons']:
        print(f"  - {reason}")
    
    # Check that invalid enum values are blocked
    has_task_mode_error = any("task_mode" in r and "not in allowed values" in r for r in result['blocking_reasons'])
    has_priority_error = any("priority" in r and "not in allowed values" in r for r in result['blocking_reasons'])
    
    if result['verdict'] == 'BLOCK' and has_task_mode_error and has_priority_error:
        print("✓ PASS: Invalid enum values properly blocked")
        print()
        return True
    else:
        print("✗ FAIL: Invalid enum values not properly blocked")
        print()
        return False


def main():
    """Run all verification tests."""
    print("=" * 70)
    print("AIPOS-R0 N0 Validation Verification Tests")
    print("=" * 70)
    print()
    
    # Clean up any previous test drafts
    cleanup_test_drafts()
    
    tests = [
        test_misspelled_field,
        test_missing_required_field,
        test_runtime_field_forbidden,
        test_valid_draft,
        test_enum_validation,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ TEST ERROR: {e}")
            import traceback
            traceback.print_exc()
            print()
            results.append(False)
    
    # Clean up test drafts
    cleanup_test_drafts()
    
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        print(f"✓ ALL TESTS PASSED ({passed}/{total})")
        print("=" * 70)
        return 0
    else:
        print(f"✗ SOME TESTS FAILED ({passed}/{total})")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
