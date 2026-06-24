"""AIPOS-208 — TUI AI-authoring first screen (DG-8) tests (core lane, no Textual).

Structure is verified with a FakeLLM (real-LLM quality is left to the acceptance script): a
natural-language ask becomes a *conformant* task card whose shape is guaranteed by code
(draft_validator contract), not by LLM luck — closing the N4/N5 dogfood lesson. The card is
proven gated-publishable against the real draft_publish_dry_run (real serve-rotate owner creds,
per AIPOS-207). The AIPOS-206 invariants (copilot read-only ★A1, zero file write, RF-5) must hold.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from tools.aipos_cli.confirm_client import GateClient, load_owner_token
from tools.aipos_cli.service_mode import build_connection_config, write_connection_config
from tools.lybra_tui.copilot import CopilotSession, DraftProposal
from tools.lybra_tui.state import TuiSession
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server, load_service_role_registry

_CARD_JSON = json.dumps({
    "task_id": "AIPOS-DOC-1",
    "title": "Document the Planning Copilot read-only mode",
    "task_mode": "docs",
    "priority": "low",
    "output_target": "README.md",
    "assigned_to": "dev_claude",
    "body": "Add a README section describing the Planning Copilot read-only mode and the gate publish flow.",
})


class FakeLLM:
    def __init__(self, reply: str = _CARD_JSON) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.reply


class AiAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "drafts").mkdir(parents=True, exist_ok=True)
        self.seed = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-seed.md"
        self.seed.write_text(
            "\n".join([
                "---", "task_id: AIPOS-SEED", "title: seed", "project: lybra", "assigned_to: dev_claude",
                "agent_instance: agent-01", "context_bundle: dev_claude", "task_mode: code", "model_tier: L2",
                "priority: medium", "status: pending", "created_by: tester", "needs_owner: false",
                "output_target: tools/", "artifact_policy: formal_write", "session_policy: single_task_session",
                "context_isolation: strict", "artifact_scope: tools/", "memory_scope: t", "claim_policy: specific_instance_only",
                "---", "seed.", "",
            ]),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _rotate(self) -> dict:
        config = build_connection_config(self.repo_root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)
        write_connection_config(self.repo_root, config)
        return config

    @contextmanager
    def _gate(self) -> Iterator[str]:
        registry = load_service_role_registry(self.repo_root / ".lybra" / "local" / "connection.json")
        config = HttpSseConfig(host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01, max_keepalive_events=1, service_role_registry=registry)
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown(); thread.join(timeout=2); httpd.server_close()

    def _copilot(self, url: str, llm: FakeLLM | None = None) -> CopilotSession:
        cj = self.repo_root / ".lybra" / "local" / "connection.json"
        return CopilotSession.connect(url, llm=llm or FakeLLM(), project="lybra", connection_json=str(cj), role="copilot")

    def _all_files(self) -> set[str]:
        return {str(p.relative_to(self.repo_root)) for p in self.repo_root.rglob("*") if p.is_file()}

    # --- T1: conformant + actually gated-publishable against real draft_publish_dry_run ---
    def test_card_conformant_and_publishable(self) -> None:
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url)
            proposal = cop.draft_task_card("document the copilot read-only mode")
            self.assertIsInstance(proposal, DraftProposal)
            self.assertTrue(proposal.conformant, proposal.blocking_reasons)
            self.assertEqual(proposal.blocking_reasons, [])
            # land via the owner session, then prove the gate accepts it (not SCOPE_DENIED / not BLOCK)
            owner = TuiSession.connect(url, connection_json=str(self.repo_root / ".lybra/local/connection.json"), role="owner")
            owner.land_draft(proposal.content, workspace_root=str(self.repo_root), draft_rel_path=proposal.draft_rel_path)
            otok = load_owner_token(connection_json=str(self.repo_root / ".lybra/local/connection.json"), role="owner")
            oc = GateClient(url, otok); oc.initialize()
            r = oc.call_tool("lybra_draft_publish_dry_run", {"path": proposal.draft_rel_path, "actor": "owner"})
        self.assertNotEqual(r.get("error_code"), "SCOPE_DENIED", r)
        self.assertIn(r.get("verdict"), ("PASS", "WARN"), r)

    # --- T3: copilot card authoring writes no file ---
    def test_card_zero_file_write(self) -> None:
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url)
            before = self._all_files()
            cop.draft_task_card("document the copilot mode")
            after = self._all_files()
        self.assertEqual(before, after)

    # --- T4: RF-5 truth re-read before the card draft ---
    def test_card_rereads_truth(self) -> None:
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url)
            p = cop.draft_task_card("document the copilot mode")
        self.assertTrue(p.truth_reread)
        self.assertIn("queue", cop.memory.l0_truth)

    # --- T5: context_bundle suggested read-only from existing bundles, lands in the card ---
    def test_context_bundle_suggested_from_existing(self) -> None:
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url)
            self.assertIn("dev_claude", cop.available_context_bundles())
            p = cop.draft_task_card("document the copilot mode")
        self.assertEqual(p.context_bundle, "dev_claude")
        self.assertIn("context_bundle: dev_claude", p.content)
        self.assertFalse(p.needs_bundle)

    # --- T6: no forbidden runtime fields; slug-aligned path; valid task_id ---
    def test_no_forbidden_fields_and_slug(self) -> None:
        self._rotate()
        with self._gate() as url:
            p = self._copilot(url).draft_task_card("document the copilot mode")
        for forbidden in ("claim_id", "claimed_by", "active_session_id", "completed_by"):
            self.assertNotIn(f"{forbidden}:", p.content)
        self.assertEqual(p.draft_rel_path, "5_tasks/drafts/aipos-doc-1.md")
        self.assertEqual(p.task_id, "AIPOS-DOC-1")

    # --- T7: no-bundle fallback — surface the gap, then Owner specifies at proceed ---
    def test_no_bundle_fallback_then_owner_specifies(self) -> None:
        self.seed.unlink()  # empty queue -> no existing bundles
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url)
            self.assertEqual(cop.available_context_bundles(), [])
            p = cop.draft_task_card("document the copilot mode")
            self.assertTrue(p.needs_bundle)
            self.assertFalse(p.conformant)
            self.assertIn("Missing required field: context_bundle", p.blocking_reasons)
            fixed = cop.finalize_card(p, context_bundle="dev_claude")
        self.assertTrue(fixed.conformant, fixed.blocking_reasons)
        self.assertIn("context_bundle: dev_claude", fixed.content)

    # --- T2: ★A1 regression — copilot card credential cannot confirm/publish ---
    def test_copilot_credential_still_scope_denied(self) -> None:
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url)
            d1 = cop._client.call_tool("lybra_draft_publish_confirm", {"dry_run_token": "x", "owner_confirmation_token": "OWNER_CONFIRMED"})
            d2 = cop._client.call_tool("lybra_queue_claim_confirm", {"dry_run_token": "x", "owner_confirmation_token": "OWNER_CONFIRMED"})
        self.assertEqual(d1.get("error_code"), "SCOPE_DENIED")
        self.assertEqual(d2.get("error_code"), "SCOPE_DENIED")

    # --- malformed LLM output degrades gracefully (not conformant, no crash, no write) ---
    def test_malformed_llm_output_not_conformant(self) -> None:
        self._rotate()
        with self._gate() as url:
            cop = self._copilot(url, FakeLLM(reply="I cannot create files — I'm read-only."))
            before = self._all_files()
            p = cop.draft_task_card("document the copilot mode")
            after = self._all_files()
        self.assertFalse(p.conformant)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
