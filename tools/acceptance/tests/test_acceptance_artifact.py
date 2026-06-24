"""AIPOS-211 — tests for the acceptance ARTIFACT itself (core lane, no Textual).

Lightweight: verifies the aggregator's red lines (no owner token, no confirm, no textual/3rd-party
import, no external network) and the runbook's explicit Owner-OOB gates — WITHOUT running the full
aggregator (which would re-run the suite recursively).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tools.acceptance import v1_acceptance

_REPO = Path(__file__).resolve().parents[3]
_AGG_SRC = (_REPO / "tools" / "acceptance" / "v1_acceptance.py").read_text(encoding="utf-8")
_RUNBOOK = _REPO / "docs" / "v1_acceptance_runbook.md"


class AcceptanceArtifactTests(unittest.TestCase):
    # --- aggregator red lines: no owner token, no confirm, no network ---
    def test_aggregator_holds_no_owner_token_and_never_confirms(self) -> None:
        for forbidden in ("load_owner_token", "OWNER_CONFIRMED", "owner_confirmation_token",
                          "_confirm(", "draft_publish_confirm", "queue_claim_confirm",
                          "urllib", "urlopen", "requests", "socket.connect"):
            self.assertNotIn(forbidden, _AGG_SRC, f"acceptance aggregator must not reference {forbidden}")

    def test_aggregator_imports_no_textual_or_third_party(self) -> None:
        # Runtime proof for textual (the source only mentions it as a string in the blocking probe).
        self.assertNotIn("textual", sys.modules)
        import tools.acceptance.v1_acceptance  # noqa: F401
        self.assertNotIn("textual", sys.modules)
        for forbidden in ("import openai", "import httpx", "import requests", "from anthropic"):
            self.assertNotIn(forbidden, _AGG_SRC)

    # --- isolation grep check works (only app.py imports textual) ---
    def test_isolation_grep_passes(self) -> None:
        ok, detail = v1_acceptance.check_isolation_grep()
        self.assertTrue(ok, detail)

    # --- the textual-absence probe blocks textual (RF-5 hardening) ---
    def test_isolation_probe_blocks_textual(self) -> None:
        self.assertIn("_BlockTextual", v1_acceptance._ISOLATION_PROBE)
        self.assertIn("textual blocked", v1_acceptance._ISOLATION_PROBE)
        self.assertIn("GATE_OK_NO_TEXTUAL", v1_acceptance._ISOLATION_PROBE)

    # --- anchors map to real test modules (承 the named slices) ---
    def test_anchors_are_real_modules(self) -> None:
        mods = [m for _, m in v1_acceptance.ANCHORS]
        self.assertIn("tools.mcp_server.tests.test_scope_reachability", mods)
        self.assertIn("tools.lybra_tui.tests.test_ai_authoring", mods)
        self.assertIn("tools.lybra_tui.tests.test_copilot", mods)

    # --- runbook: explicit Owner-OOB gates + red line + quality anchors ---
    def test_runbook_has_owner_oob_gates_and_redline(self) -> None:
        self.assertTrue(_RUNBOOK.is_file(), f"missing {_RUNBOOK}")
        text = _RUNBOOK.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("[Owner OOB]"), 3)  # R4 publish, R5 claim, R6 return
        self.assertIn("NEVER holds the owner token", text)
        self.assertIn("LYBRA_PLANCHAT_LLM_KEY", text)
        self.assertIn("confirmer_role=owner", text)
        for anchor in ("semantically sensible", "NOT fabricated", "no secrets"):
            self.assertIn(anchor, text)


if __name__ == "__main__":
    unittest.main()
