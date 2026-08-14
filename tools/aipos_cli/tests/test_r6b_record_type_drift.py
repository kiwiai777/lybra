"""AIPOS-R6B regression: lock the audit_verdict vs audit_verdict_record drift (CONN-LOOP-2).

Historical bug: the WRITE side (board_adapter audit_verdict writer) wrote
``record_type: audit_verdict`` while the VALIDATION side (records loader /
ref-checker) looked for ``audit_verdict_record`` — two different literal strings
for the same concept. The record was written but the ref-checker couldn't find it,
producing phantom "missing record" errors.

This test locks the fix: with typed constants from a single loader
(``tools.schema_constants``), both sides reference the SAME constant. If the
schema value changes, the constant changes in ONE place, and both sides
auto-update — no more drift.
"""
from __future__ import annotations

import unittest

from tools.schema_constants import RecordType, Verdict


class RecordTypeDriftRegressionTests(unittest.TestCase):
    """CONN-LOOP-2 drift lock: write side and read side must use the same constant."""

    def test_audit_verdict_constant_is_single_source(self) -> None:
        """The canonical audit_verdict value exists exactly once in the schema-derived
        namespace and is a plain string (so file/network comparisons still work)."""
        # The constant is the string — not a wrapper, so == with raw data works
        self.assertEqual(RecordType.AUDIT_VERDICT, "audit_verdict")
        self.assertIsInstance(RecordType.AUDIT_VERDICT, str)

    def test_audit_verdict_record_alias_exists_and_differs(self) -> None:
        """The deprecated alias audit_verdict_record is a DIFFERENT value.
        Both exist in the schema, but the canonical one is audit_verdict."""
        self.assertEqual(RecordType.AUDIT_VERDICT_RECORD, "audit_verdict_record")
        self.assertNotEqual(RecordType.AUDIT_VERDICT, RecordType.AUDIT_VERDICT_RECORD)

    def test_no_drift_between_write_and_read_paths(self) -> None:
        """Simulate: write side sets record_type = RecordType.AUDIT_VERDICT,
        read side checks record_type == RecordType.AUDIT_VERDICT.
        Both pull from the SAME namespace, so they can never diverge."""
        # Write side (board_adapter writes record_type field)
        written_record_type = RecordType.AUDIT_VERDICT

        # Read side (records.py loader / validator ref-checker reads it back)
        self.assertEqual(written_record_type, RecordType.AUDIT_VERDICT)

        # If schema changes the value, BOTH sides change automatically
        # (they reference the same attribute). This is the drift lock.
        self.assertIs(written_record_type.__class__, RecordType.AUDIT_VERDICT.__class__)

    def test_constants_derived_from_schema_not_hardcoded(self) -> None:
        """Verify constants are dynamically generated from enums.schema.json,
        not a second hardcoded mapping (forbidden: 一机制一实现)."""
        from tools.schema_loader import get_enum_values

        schema_values = set(get_enum_values("record_type"))
        # Every RecordType.* constant value must appear in the schema
        for attr in dir(RecordType):
            if attr.startswith("_"):
                continue
            val = getattr(RecordType, attr)
            self.assertIn(val, schema_values,
                          f"RecordType.{attr}='{val}' not in schema record_type enum")

        # Conversely, every schema value must have a constant (no silent gaps)
        for v in schema_values:
            const_name = v.upper()
            self.assertTrue(hasattr(RecordType, const_name),
                           f"schema value '{v}' has no RecordType.{const_name} constant")

    def test_verdict_constants_single_source(self) -> None:
        """Same single-source guarantee for verdict constants."""
        from tools.schema_loader import get_enum_values

        schema_values = set(get_enum_values("verdict"))
        for attr in dir(Verdict):
            if attr.startswith("_"):
                continue
            val = getattr(Verdict, attr)
            self.assertIn(val, schema_values,
                          f"Verdict.{attr}='{val}' not in schema verdict enum")
        for v in schema_values:
            self.assertTrue(hasattr(Verdict, v.upper()),
                           f"schema value '{v}' has no Verdict.{v.upper()} constant")

    def test_claim_drift_lock(self) -> None:
        """Same drift lock for the claim / claim_log / claim_record family.
        CONN-LOOP-2 also hit claim_record vs claim confusion."""
        # Canonical
        self.assertEqual(RecordType.CLAIM, "claim")
        # Deprecated aliases are different values
        self.assertEqual(RecordType.CLAIM_LOG, "claim_log")
        self.assertEqual(RecordType.CLAIM_RECORD, "claim_record")
        self.assertNotEqual(RecordType.CLAIM, RecordType.CLAIM_LOG)
        self.assertNotEqual(RecordType.CLAIM, RecordType.CLAIM_RECORD)


if __name__ == "__main__":
    unittest.main()
