"""AIPOS-FND-7F2: Test null-safe verdict sorting with mixed-format records.

Mixed-format scenario:
- Old records: verdict_at=None, timestamp="2026-08-07T10:00:00Z"
- New records: verdict_at="2026-08-08T12:00:00Z", timestamp=None

The _verdict_time helper must handle None values gracefully without TypeError.
"""
from __future__ import annotations

import unittest

from tools.aipos_cli.board_adapter import _verdict_time


class VerdictSortingMixedFormatTest(unittest.TestCase):
    """Test verdict sorting with mixed-format records (FND-7F2 regression prevention)."""

    def test_verdict_time_new_format(self):
        """New format: verdict_at set, timestamp None."""
        v = {"verdict_at": "2026-08-08T12:00:00Z", "timestamp": None}
        self.assertEqual(_verdict_time(v), "2026-08-08T12:00:00Z")

    def test_verdict_time_old_format(self):
        """Old format: verdict_at None, timestamp set."""
        v = {"verdict_at": None, "timestamp": "2026-08-07T10:00:00Z"}
        self.assertEqual(_verdict_time(v), "2026-08-07T10:00:00Z")

    def test_verdict_time_both_none(self):
        """Both fields None: fallback to empty string."""
        v = {"verdict_at": None, "timestamp": None}
        self.assertEqual(_verdict_time(v), "")

    def test_verdict_time_both_missing(self):
        """Both fields missing: fallback to empty string."""
        v = {}
        self.assertEqual(_verdict_time(v), "")

    def test_verdict_time_both_set(self):
        """Both fields set: verdict_at takes precedence."""
        v = {"verdict_at": "2026-08-08T14:00:00Z", "timestamp": "2026-08-07T10:00:00Z"}
        self.assertEqual(_verdict_time(v), "2026-08-08T14:00:00Z")

    def test_max_with_mixed_format_no_typeerror(self):
        """max() on mixed-format records must not raise TypeError (FND-7F2 root cause)."""
        existing_verdicts = [
            # Old format (FND-1 R1)
            {
                "verdict": "FAIL",
                "verdict_at": None,
                "timestamp": "2026-08-07T10:00:00Z",
                "verdict_id": "verdict-fnd1-r1",
            },
            # New format (FND-1 R2)
            {
                "verdict": "REQUEST_CHANGES",
                "verdict_at": "2026-08-08T12:00:00Z",
                "timestamp": None,
                "verdict_id": "verdict-fnd1-r2",
            },
        ]
        # This used to raise: TypeError: '>' not supported between instances of 'NoneType' and 'str'
        try:
            latest_verdict = max(existing_verdicts, key=_verdict_time)
            # Latest should be R2 (2026-08-08T12:00:00Z > 2026-08-07T10:00:00Z)
            self.assertEqual(latest_verdict["verdict_id"], "verdict-fnd1-r2")
            self.assertEqual(latest_verdict["verdict"], "REQUEST_CHANGES")
        except TypeError as e:
            self.fail(f"max() raised TypeError on mixed-format records: {e}")

    def test_max_with_three_mixed_records(self):
        """Realistic scenario: 3 verdicts with different formats."""
        existing_verdicts = [
            {"verdict": "FAIL", "verdict_at": None, "timestamp": "2026-08-06T09:00:00Z"},
            {"verdict": "REQUEST_CHANGES", "verdict_at": "2026-08-07T11:00:00Z", "timestamp": None},
            {"verdict": "PASS", "verdict_at": "2026-08-08T15:30:00Z", "timestamp": None},
        ]
        latest_verdict = max(existing_verdicts, key=_verdict_time)
        self.assertEqual(latest_verdict["verdict"], "PASS")
        self.assertEqual(latest_verdict["verdict_at"], "2026-08-08T15:30:00Z")


if __name__ == "__main__":
    unittest.main()
