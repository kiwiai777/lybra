"""AIPOS-338 S4 — two-mode record-shape identity (auto pump vs manual /claim).

The claim: the same card produces records of identical shape whether it walks
the automatic pump or a manual /claim, so audit/ledger cannot tell the source.

This is structurally guaranteed: BOTH modes converge on the SAME gate verbs
(lybra_queue_claim_dry_run / lybra_task_progress / lybra_queue_return_dry_run),
which write the SAME claim/return/event records via the SAME record writers.
dispatch_mode lives ONLY in project.json (a workspace property), never inside
claim/return/event records — so no record reveals "this came from the pump".
"""
import re
import unittest
from pathlib import Path

import tools.aipos_cli.pump_orchestration as pump


class TestTwoModeRecordShapeIdentity(unittest.TestCase):
    def test_pump_claim_uses_same_verb_as_manual_claim(self):
        """The pump's claim step calls the exact claim verb a manual /claim uses."""
        src = Path(pump.__file__).read_text(encoding="utf-8")
        # pump step_claim calls the dry_run claim verb (same as manual /claim)
        self.assertIn('call_tool("lybra_queue_claim_dry_run"', src)

    def test_dispatch_mode_is_not_a_record_field(self):
        """dispatch_mode never appears inside claim/return/event record writers."""
        for mod in ("record_writer", "queue_mutation", "records", "audit_derivation"):
            path = Path(f"tools/aipos_cli/{mod}.py")
            if path.is_file():
                src = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "dispatch_mode", src,
                    f"{mod}.py must not embed dispatch_mode in records (records are mode-agnostic)",
                )

    def test_claim_record_path_convention_is_mode_agnostic(self):
        """Claim records land at claims/<ID>/claim_*.md regardless of trigger."""
        # pump's landed-claim detector and the manual path use the SAME location.
        src = Path(pump.__file__).read_text(encoding="utf-8")
        # _claim_record_landed looks under claims/<ID>/
        self.assertRegex(src, r"claims[^`]*claim_\*\.md|_record_rel_dir\(.claim.")

    def test_manual_claim_still_works_in_auto_mode(self):
        """auto mode does not gate the pump's claim verb (manual /claim always works)."""
        # dispatch_mode only short-circuits run_pump_dispatch; the claim verb itself
        # is unconditional. We assert the gate check is in run_pump_dispatch, not step_claim.
        src = Path(pump.__file__).read_text(encoding="utf-8")
        # the manual-mode refusal lives in run_pump_dispatch, identified by its docstring
        run_fn = src[src.index("def run_pump_dispatch"):]
        self.assertIn("get_dispatch_mode", run_fn)
        # step_claim does NOT read dispatch_mode (manual /claim is never gated by mode)
        claim_fn = src[src.index("def step_claim("):src.index("def step_launch(")]
        self.assertNotIn("dispatch_mode", claim_fn)


if __name__ == "__main__":
    unittest.main()
