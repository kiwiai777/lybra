"""AIPOS-212 — disclosure ledger completeness guard (core lane).

Makes "honesty" regressable: the v1.0 disclosure ledger must cover all nine disclosed-deferred /
discipline-held categories, cross-reference real decision-log / AIPOS ids, honestly distinguish
discipline-held from structure-held, and not silently over-claim.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_DOC = Path(__file__).resolve().parents[3] / "docs" / "v1_disclosure.md"


class DisclosureLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert _DOC.is_file(), f"missing {_DOC}"
        cls.text = _DOC.read_text(encoding="utf-8")

    def test_covers_nine_categories(self) -> None:
        for needle in (
            "RF-3",                       # 1 orchestrator can read owner token
            "§9",                         # 2 nonce / gate signature
            "DG-9",                       # 3 CLI publish ungated
            "intake_submit",              # 4 path-B exemption
            "owner_decision_record",
            "egress",                     # 5 network egress
            "Supervised only",            # 6 autonomy
            "Form A",                     # 7 Wall = Claude only
            "F-candidate-2",
            "mutual audit",               # 8 heterogeneous / R2 / R5 / 206b
            "LYBRA_PLANCHAT_LLM_KEY",     # 9 LLM key
        ):
            self.assertIn(needle, self.text, f"disclosure ledger missing category marker: {needle}")

    def test_cross_references_real_ids(self) -> None:
        for ref in (
            "DL-20260622-05", "DL-20260623-08", "DL-20260623-10", "DL-20260623-11", "DL-20260623-12",
            "AIPOS-197", "AIPOS-199", "AIPOS-204", "AIPOS-206", "AIPOS-207",
        ):
            self.assertIn(ref, self.text, f"disclosure ledger missing cross-reference: {ref}")

    def test_honestly_distinguishes_discipline_vs_structure(self) -> None:
        # RF-3 must be marked discipline-held (NOT dressed up as a structural guarantee).
        self.assertIn("Discipline-held (NOT structure-held", self.text)
        self.assertIn("discipline-held", self.text.lower())
        self.assertIn("structure-held", self.text.lower())

    def test_does_not_overclaim(self) -> None:
        # Positioning guard: Form B is named; "heterogeneous accountability loop" only appears as the
        # explicitly-rejected claim, never asserted of v1.0.
        self.assertIn("Form B", self.text)
        self.assertIn("NOT", self.text)
        self.assertIn("heterogeneous", self.text.lower())
        # the rejected phrase must be flagged as NOT what v1.0 is
        self.assertRegex(self.text, r"NOT[^\n]*heterogeneous")


if __name__ == "__main__":
    unittest.main()
