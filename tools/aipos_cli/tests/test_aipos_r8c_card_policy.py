"""AIPOS-R8C: Tests for project-declarable card validation policy.

Tests the zero-invasion card_policy executor:
1. No card_policy → no extra checks (zero-invasion)
2. card_policy present → missing required field → BLOCK
3. card_policy present → invalid value → BLOCK with allowed values
4. Zero-hardcoding: add fake node to schema → immediately accepted
5. Semantics-blind: rename field → same validation behavior
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure product repo is on path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.card_policy_loader import (
    evaluate_card_policy_rules,
    get_card_policy_placeholder_fields,
    get_card_policy_rules,
    get_project_card_policy_path,
    resolve_values_from,
)
from tools.schema_loader import clear_cache


class TestValuesFromResolution(unittest.TestCase):
    """Test values_from path resolution against schema files."""

    def test_resolve_node_ids(self):
        values = resolve_values_from("transitions.schema.json#main_flow.nodes[].node_id")
        self.assertIn("N0", values)
        self.assertIn("N6", values)
        self.assertEqual(len(values), 7)  # N0-N6

    def test_resolve_cross_cutting_keys(self):
        values = resolve_values_from("transitions.schema.json#cross_cutting")
        self.assertIn("g1_owner_gate", values)
        self.assertIn("g2_maintenance", values)
        self.assertIn("g3_derivation_dispatch", values)

    def test_resolve_union(self):
        values = resolve_values_from(
            "transitions.schema.json#main_flow.nodes[].node_id"
            "+transitions.schema.json#cross_cutting"
        )
        self.assertIn("N0", values)
        self.assertIn("g1_owner_gate", values)

    def test_resolve_nonexistent_schema(self):
        values = resolve_values_from("nonexistent.schema.json#foo")
        self.assertEqual(values, [])


class TestCardPolicyDeclaration(unittest.TestCase):
    """Test card_policy declaration file loading."""

    def setUp(self):
        self.gov_root = Path(__file__).resolve().parent.parent.parent.parent.parent / "ai-project-os" / "2_projects" / "lybra"
        if not self.gov_root.exists():
            self.gov_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")

    def test_declaration_path_found(self):
        path = get_project_card_policy_path(self.gov_root)
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())

    def test_rules_loaded(self):
        rules = get_card_policy_rules(self.gov_root)
        self.assertGreater(len(rules), 0)
        self.assertEqual(rules[0]["field"], "anchor_refs")
        self.assertTrue(rules[0]["required"])

    def test_no_declaration_returns_empty(self):
        rules = get_card_policy_rules("/nonexistent/path")
        self.assertEqual(rules, [])


class TestCardPolicyEvaluation(unittest.TestCase):
    """Test card_policy rule evaluation."""

    def setUp(self):
        self.gov_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
        self.policy_file = self.gov_root / "card_policy.json"

    def test_missing_required_field_blocks(self):
        blocking, _ = evaluate_card_policy_rules(
            {"task_id": "TEST-1"},
            governance_root=self.gov_root,
        )
        self.assertEqual(len(blocking), 1)
        self.assertIn("anchor_refs", blocking[0])

    def test_valid_values_pass(self):
        blocking, _ = evaluate_card_policy_rules(
            {"task_id": "TEST-1", "anchor_refs": ["N0", "N3"]},
            governance_root=self.gov_root,
        )
        self.assertEqual(blocking, [])

    def test_invalid_value_blocks(self):
        blocking, _ = evaluate_card_policy_rules(
            {"task_id": "TEST-1", "anchor_refs": ["N99"]},
            governance_root=self.gov_root,
        )
        self.assertEqual(len(blocking), 1)
        self.assertIn("N99", blocking[0])
        self.assertIn("N0", blocking[0])  # Lists allowed values

    def test_zero_invasion_no_policy(self):
        """When no card_policy exists, no extra checks."""
        import tempfile
        import shutil

        # Temporarily move the policy file
        backup = self.policy_file.with_suffix(".json.testbak")
        self.policy_file.rename(backup)
        try:
            blocking, warnings = evaluate_card_policy_rules(
                {"task_id": "TEST-1"},
                governance_root=self.gov_root,
            )
            self.assertEqual(blocking, [])
            self.assertEqual(warnings, [])
        finally:
            backup.rename(self.policy_file)

    def test_semantics_blind_rename(self):
        """Renaming the field in declaration → same validation behavior."""
        original = self.policy_file.read_text()
        modified = original.replace("anchor_refs", "foo_refs")
        self.policy_file.write_text(modified)
        try:
            # foo_refs missing → should block
            blocking, _ = evaluate_card_policy_rules(
                {"task_id": "TEST-1", "anchor_refs": ["N0"]},
                governance_root=self.gov_root,
            )
            self.assertEqual(len(blocking), 1)
            self.assertIn("foo_refs", blocking[0])

            # foo_refs present with valid value → should pass
            blocking2, _ = evaluate_card_policy_rules(
                {"task_id": "TEST-1", "foo_refs": ["N0"]},
                governance_root=self.gov_root,
            )
            self.assertEqual(blocking2, [])
        finally:
            self.policy_file.write_text(original)


class TestZeroHardcoding(unittest.TestCase):
    """Test that values come from schema, not hardcoded."""

    def setUp(self):
        self.gov_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
        self.transitions_file = REPO_ROOT / "schema" / "transitions.schema.json"

    def test_fake_node_immediately_accepted(self):
        """Add N7 to transitions.schema → validator accepts it."""
        original = self.transitions_file.read_text()
        data = json.loads(original)
        data["main_flow"]["nodes"].append({
            "node_id": "N7",
            "name": "fake_test_node",
            "description": "Temporary test node",
        })
        self.transitions_file.write_text(json.dumps(data, indent=2))
        clear_cache()
        try:
            blocking, _ = evaluate_card_policy_rules(
                {"task_id": "TEST-1", "anchor_refs": ["N7"]},
                governance_root=self.gov_root,
            )
            self.assertEqual(blocking, [])
        finally:
            self.transitions_file.write_text(original)
            clear_cache()


class TestPlaceholderFields(unittest.TestCase):
    """Test draft create placeholder pre-population."""

    def setUp(self):
        self.gov_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")

    def test_placeholders_for_required_fields(self):
        placeholders = get_card_policy_placeholder_fields(self.gov_root)
        self.assertIn("anchor_refs", placeholders)
        # values_from contains [] → should be empty array
        self.assertEqual(placeholders["anchor_refs"], [])


if __name__ == "__main__":
    unittest.main()
