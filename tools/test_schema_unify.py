#!/usr/bin/env python3
"""AIPOS-SCHEMA-UNIFY-1: Schema single-source cross-validation tests.

Audit assertions:
1. grep全schema包无重复枚举字面量(enums外零份)
2. 故意造一个坏引用→loader加载即炸且报错指名
3. 消费方回归:N0校验全活测不变
4. 一机制一实现:引用解析只在loader一处
"""
import json
import sys
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.schema_loader import (
    SchemaLoadError,
    clear_cache,
    cross_validate_schemas,
    get_card_field_schema,
    get_enum_values,
    resolve_enum_ref,
    resolve_field_enum,
    validate_all_enum_refs,
    validate_field_value,
)

SCHEMA_DIR = Path(__file__).parent.parent / "schema"


def test_no_residual_enum_literals():
    """Audit #1: No 'enum' array literals outside enums.schema.json."""
    print("Test 1: No residual enum literals outside enums.schema.json")
    clear_cache()

    for schema_file in SCHEMA_DIR.glob("*.schema.json"):
        if schema_file.name == "enums.schema.json":
            continue
        data = json.loads(schema_file.read_text())
        # Walk the entire structure looking for "enum" keys with list values
        def find_enum_literals(obj, path=""):
            findings = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    p = f"{path}.{k}" if path else k
                    if k == "enum" and isinstance(v, list):
                        findings.append(p)
                    else:
                        findings.extend(find_enum_literals(v, p))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    findings.extend(find_enum_literals(item, f"{path}[{i}]"))
            return findings

        found = find_enum_literals(data)
        assert not found, f"Residual enum literals in {schema_file.name}: {found}"
        print(f"  ✓ {schema_file.name}: clean")

    print("  ✓ PASS\n")


def test_all_enum_refs_resolve():
    """All $enum references in schema package resolve to enums.schema.json."""
    print("Test 2: All $enum references resolve")
    clear_cache()

    # This should not raise
    cross_validate_schemas()
    print("  ✓ cross_validate_schemas() passed")

    # Also test validate_all_enum_refs convenience wrapper
    assert validate_all_enum_refs() is True
    print("  ✓ validate_all_enum_refs() returned True")
    print("  ✓ PASS\n")


def test_bad_enum_reference_raises():
    """Audit #2: Bad $enum reference → SchemaLoadError with name."""
    print("Test 3: Bad $enum reference → SchemaLoadError")
    clear_cache()

    card_path = SCHEMA_DIR / "card.schema.json"
    original = card_path.read_text()

    try:
        # Inject bad reference
        data = json.loads(original)
        data["fields"]["task_mode"]["$enum"] = "totally_fake_enum"
        card_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        clear_cache()

        try:
            cross_validate_schemas()
            assert False, "Should have raised SchemaLoadError"
        except SchemaLoadError as e:
            msg = str(e)
            assert "totally_fake_enum" in msg, f"Error should name the bad enum: {msg}"
            assert "BROKEN" in msg, f"Error should say BROKEN: {msg}"
            print(f"  ✓ SchemaLoadError raised, names 'totally_fake_enum'")
    finally:
        card_path.write_text(original)
        clear_cache()

    print("  ✓ PASS\n")


def test_residual_literal_raises():
    """Residual 'enum' array literal → SchemaLoadError."""
    print("Test 4: Residual enum literal → SchemaLoadError")
    clear_cache()

    card_path = SCHEMA_DIR / "card.schema.json"
    original = card_path.read_text()

    try:
        data = json.loads(original)
        # Re-introduce a literal enum
        data["fields"]["task_mode"] = {
            "type": "string",
            "enum": ["code", "docs"],
            "required": True,
        }
        card_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        clear_cache()

        try:
            cross_validate_schemas()
            assert False, "Should have raised SchemaLoadError"
        except SchemaLoadError as e:
            msg = str(e)
            assert "RESIDUAL" in msg, f"Error should say RESIDUAL: {msg}"
            print(f"  ✓ SchemaLoadError raised for residual literal")
    finally:
        card_path.write_text(original)
        clear_cache()

    print("  ✓ PASS\n")


def test_validate_field_value_with_enum_refs():
    """validate_field_value resolves $enum references correctly."""
    print("Test 5: validate_field_value with $enum resolution")
    clear_cache()

    # Valid values
    for field, val in [
        ("status", "pending"),
        ("task_mode", "code"),
        ("priority", "high"),
        ("artifact_policy", "formal_write"),
        ("autonomy_mode", "PreAuthorized"),
        ("context_isolation", "shared"),
        ("audit", "required"),
        ("claim_policy", "open"),
        ("polling_mode", "agent_polling"),
        ("risk_level", "critical"),
    ]:
        valid, err = validate_field_value(field, val)
        assert valid, f"{field}={val} should be valid, got: {err}"
        print(f"  ✓ {field}={val} accepted")

    # Invalid values
    for field, val in [
        ("status", "flying"),
        ("task_mode", "telepathy"),
        ("priority", "ultra_super_high"),
    ]:
        valid, err = validate_field_value(field, val)
        assert not valid, f"{field}={val} should be invalid"
        assert err is not None
        print(f"  ✓ {field}={val} rejected: {err[:60]}...")

    print("  ✓ PASS\n")


def test_resolve_enum_ref():
    """resolve_enum_ref returns values from enums.schema.json."""
    print("Test 6: resolve_enum_ref")
    clear_cache()

    assert resolve_enum_ref("queue_state") == [
        "pending", "claimed", "returned", "completed", "blocked", "withdrawn"
    ]
    print("  ✓ queue_state resolved")

    assert resolve_enum_ref("validation_verdict") == ["PASS", "WARN", "BLOCK"]
    print("  ✓ validation_verdict resolved")

    assert resolve_enum_ref("progress_status") == [
        "started", "progress", "completed", "blocked"
    ]
    print("  ✓ progress_status resolved")

    try:
        resolve_enum_ref("nonexistent")
        assert False, "Should raise"
    except SchemaLoadError as e:
        assert "nonexistent" in str(e)
        print("  ✓ nonexistent enum raises SchemaLoadError")

    print("  ✓ PASS\n")


def test_resolve_field_enum():
    """resolve_field_enum handles both $enum refs and legacy inline enums."""
    print("Test 7: resolve_field_enum")
    clear_cache()

    # $enum reference
    field_schema = {"$enum": "task_mode"}
    vals = resolve_field_enum(field_schema)
    assert vals == ["code", "docs", "governance", "config"]
    print("  ✓ $enum reference resolved")

    # Legacy inline enum (backward compat)
    field_schema = {"enum": ["a", "b", "c"]}
    vals = resolve_field_enum(field_schema)
    assert vals == ["a", "b", "c"]
    print("  ✓ Legacy inline enum still works")

    # No enum
    field_schema = {"type": "string"}
    vals = resolve_field_enum(field_schema)
    assert vals is None
    print("  ✓ No enum returns None")

    print("  ✓ PASS\n")


def test_n0_validation_still_works():
    """Audit #3: N0 draft validation still works (zero behavior change)."""
    print("Test 8: N0 validation regression")
    clear_cache()

    # Import and run the N0 test
    from tools.aipos_cli.draft_validator import validate_draft_metadata

    # Valid draft
    valid_meta = {
        "task_id": "TEST-N0-REGRESSION",
        "title": "Regression test",
        "project": "lybra",
        "assigned_to": "exec.lybra.kiwiai-dev",
        "context_bundle": "exec.lybra.kiwiai-dev",
        "task_mode": "code",
        "priority": "high",
        "status": "pending",
        "created_by": "advisor.lybra.kiwiai-dev",
        "needs_owner": False,
        "output_target": "test/",
        "artifact_policy": "formal_write",
        "body": "# Test\nRegression test body.",
    }
    repo_root = Path(__file__).parent.parent
    result = validate_draft_metadata(repo_root, valid_meta, actual_path=Path("5_tasks/drafts/test-n0-regression.md"))
    assert result["verdict"] in ("PASS", "WARN"), f"Valid draft should PASS/WARN, got {result['verdict']}"
    print(f"  ✓ Valid draft: {result['verdict']}")

    # Invalid enum
    bad_meta = dict(valid_meta, task_mode="telepathy")
    result = validate_draft_metadata(repo_root, bad_meta, actual_path=Path("5_tasks/drafts/test-n0-bad.md"))
    assert result["verdict"] == "BLOCK"
    assert any("task_mode" in r for r in result.get("blocking_reasons", []))
    print(f"  ✓ Invalid task_mode blocked")

    print("  ✓ PASS\n")


def test_enum_count_in_enums_schema():
    """Verify all expected enums exist in enums.schema.json."""
    print("Test 9: Expected enums in enums.schema.json")
    clear_cache()

    expected = [
        "queue_state", "verdict", "record_type", "role_category",
        "task_mode", "task_class", "priority", "artifact_policy",
        "autonomy_mode", "audit_requirement", "context_isolation",
        "polling_mode", "claim_policy", "report_mode",
        # New enums added by AIPOS-SCHEMA-UNIFY-1:
        "task_type", "risk_level", "validation_verdict", "progress_status",
    ]
    for name in expected:
        vals = get_enum_values(name)
        assert len(vals) > 0, f"Enum {name} should have values"
        print(f"  ✓ {name}: {len(vals)} values")

    print("  ✓ PASS\n")


def test_card_fields_use_enum_refs():
    """Verify card fields that should reference enums do so."""
    print("Test 10: Card fields use $enum references")
    clear_cache()

    expected_refs = {
        "task_mode": "task_mode",
        "task_class": "task_class",
        "task_type": "task_type",
        "priority": "priority",
        "status": "queue_state",
        "artifact_policy": "artifact_policy",
        "context_isolation": "context_isolation",
        "audit": "audit_requirement",
        "polling_mode": "polling_mode",
        "claim_policy": "claim_policy",
        "report_mode": "report_mode",
        "autonomy_mode": "autonomy_mode",
        "risk_level": "risk_level",
    }

    for field_name, expected_enum in expected_refs.items():
        fs = get_card_field_schema(field_name)
        assert fs is not None, f"Field {field_name} not found"
        assert "$enum" in fs, f"Field {field_name} should have $enum ref"
        assert fs["$enum"] == expected_enum, f"Field {field_name} $enum should be {expected_enum}, got {fs['$enum']}"
        assert "enum" not in fs, f"Field {field_name} should NOT have inline enum"
        print(f"  ✓ {field_name} → $enum:{expected_enum}")

    print("  ✓ PASS\n")


if __name__ == "__main__":
    print("=" * 70)
    print("AIPOS-SCHEMA-UNIFY-1: Schema Single-Source Cross-Validation Tests")
    print("=" * 70)
    print()

    tests = [
        test_no_residual_enum_literals,
        test_all_enum_refs_resolve,
        test_bad_enum_reference_raises,
        test_residual_literal_raises,
        test_validate_field_value_with_enum_refs,
        test_resolve_enum_ref,
        test_resolve_field_enum,
        test_n0_validation_still_works,
        test_enum_count_in_enums_schema,
        test_card_fields_use_enum_refs,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            failed += 1
            print()

    print("=" * 70)
    if failed == 0:
        print(f"✓ ALL TESTS PASSED ({passed}/{passed})")
    else:
        print(f"✗ {failed} FAILED, {passed} PASSED")
    print("=" * 70)
    sys.exit(1 if failed else 0)
