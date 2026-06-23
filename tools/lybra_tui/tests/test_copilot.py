"""AIPOS-206 — Planning Copilot (DG-11) tests (core lane, no Textual).

Covers the rev.2 e2e assertions: T1 copilot-side ★A1, T1b DRAFT-write no-write,
T2 read-only + scope verify, T4 the three memory disciplines, T6 secrets fingerprint,
T7 dependency isolation, plus the Owner "proceed" land action (T3) and the 3-mode cycle.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from tools.aipos_cli.confirm_client import GateClient, load_owner_token
from tools.lybra_tui.copilot import (
    ChatTurn,
    CopilotMemory,
    CopilotSession,
    DraftProposal,
    LLMConfig,
)
from tools.lybra_tui.state import COPILOT_MODE, CONFIRM_MODE, OBSERVE_MODE, TuiSession
from tools.aipos_cli.records import load_records
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "aipos_cli" / "tests" / "fixtures"


class FakeLLM:
    """Records the messages it is sent (to assert egress + RF-5) and returns a canned draft."""

    def __init__(self, reply: str = "# DRAFT\nplan body") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.reply


class CopilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "drafts").mkdir(parents=True, exist_ok=True)
        self.write_pending_task()
        shutil.copyfile(FIXTURE_ROOT / "drafts/valid_publishable_draft.md", self.repo_root / "5_tasks/drafts/aipos-39-publish-valid.md")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_pending_task(self) -> None:
        (self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-tui-claim.md").write_text(
            "\n".join(
                [
                    "---", "task_id: AIPOS-TUI-CLAIM", "title: TUI claim test", "project: lybra",
                    "assigned_to: dev_claude", "agent_instance: agent-01", "context_bundle: dev_claude",
                    "task_mode: code", "model_tier: L2", "priority: medium", "status: pending",
                    "created_by: tester", "needs_owner: false", "output_target: tools/", "artifact_policy: formal_write",
                    "session_policy: single_task_session", "context_isolation: strict", "artifact_scope: tools/",
                    "memory_scope: tui tests", "claim_policy: specific_instance_only", "---", "TUI claim test.", "",
                ]
            ),
            encoding="utf-8",
        )

    def registry(self) -> dict[str, dict[str, object]]:
        return {
            "owner-secret": {"role": "owner", "token_ref": "svc-owner", "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish"], "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:ownercop01"},
            # AIPOS-206: the read-only copilot role — scopes [] (verified-sufficient for read).
            "copilot-secret": {"role": "copilot", "token_ref": "svc-copilot", "scopes": [], "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:copilot001"},
        }

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01, max_keepalive_events=1, service_role_registry=self.registry())
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(timeout=2)
                httpd.server_close()

    @contextmanager
    def conn(self) -> Iterator[Path]:
        path = self.repo_root / "connection.json"
        path.write_text(
            json.dumps(
                {
                    "tokens": [
                        {"role": "owner", "token": "owner-secret", "token_ref": "svc-owner"},
                        {"role": "copilot", "token": "copilot-secret", "token_ref": "svc-copilot"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        yield path

    def _copilot(self, url: str, cpath: Path, llm: FakeLLM) -> CopilotSession:
        return CopilotSession.connect(url, llm=llm, project="lybra", connection_json=cpath, role="copilot")

    def _all_files(self) -> set[str]:
        return {str(p.relative_to(self.repo_root)) for p in self.repo_root.rglob("*") if p.is_file()}

    # --- T7: dependency isolation ---
    def test_copilot_module_imports_no_textual(self) -> None:
        self.assertNotIn("textual", sys.modules, "copilot core path must not import textual")
        import tools.lybra_tui.copilot  # noqa: F401
        self.assertNotIn("textual", sys.modules)

    # --- T2 + scope verify: read-tool visible at scopes [], writes denied ---
    def test_copilot_scopes_empty_read_works_writes_denied(self) -> None:
        with self.gate() as url, self.conn() as cpath:
            token = load_owner_token(connection_json=cpath, role="copilot")
            client = GateClient(url, token)
            client.initialize()
            # read tool works with scopes []
            queue = client.call_tool("lybra_queue_list", {})
            self.assertEqual(queue.get("operation"), "get_queue")
            self.assertEqual(queue.get("scope_basis", {}).get("role"), "copilot")
            self.assertEqual(queue.get("scope_basis", {}).get("scopes"), [])

    # --- T1: copilot-side ★A1 — confirm/publish are SCOPE_DENIED with copilot creds ---
    def test_copilot_credential_confirm_and_publish_scope_denied(self) -> None:
        with self.gate() as url, self.conn() as cpath:
            token = load_owner_token(connection_json=cpath, role="copilot")
            client = GateClient(url, token)
            client.initialize()
            denied_publish = client.call_tool("lybra_draft_publish_confirm", {"dry_run_token": "x", "owner_confirmation_token": "OWNER_CONFIRMED"})
            denied_claim = client.call_tool("lybra_queue_claim_confirm", {"dry_run_token": "x", "owner_confirmation_token": "OWNER_CONFIRMED"})
        self.assertEqual(denied_publish.get("error_code"), "SCOPE_DENIED")
        self.assertEqual(denied_claim.get("error_code"), "SCOPE_DENIED")
        # nothing written
        self.assertEqual(load_records(self.repo_root).get("publishes", []), [])
        self.assertEqual(load_records(self.repo_root).get("claims", []), [])

    # --- T1b: the copilot draft loop returns DATA and writes NO file ---
    def test_draft_returns_data_and_writes_nothing(self) -> None:
        llm = FakeLLM(reply="# DRAFT\nAIPOS-XX plan")
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            before = self._all_files()
            proposal = copilot.draft("plan AIPOS-XX", task_id="AIPOS-TUI-CLAIM")
            after = self._all_files()
        self.assertIsInstance(proposal, DraftProposal)
        self.assertIn("plan", proposal.content)
        self.assertEqual(before, after, "the copilot draft loop must not write any file")
        self.assertFalse(hasattr(proposal, "path"))

    def test_copilot_module_imports_no_write_helper(self) -> None:
        src = (Path(__file__).resolve().parent.parent / "copilot.py").read_text(encoding="utf-8")
        for forbidden in ("draft_writer", "board_adapter", "publish_draft", "execute_dry_run"):
            self.assertNotIn(forbidden, src, f"copilot.py must not import/reference {forbidden}")

    # --- T4 discipline (b) / RF-5: truth re-read via read-tools before every draft ---
    def test_draft_rereads_truth_and_sends_it_egress(self) -> None:
        llm = FakeLLM()
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            proposal = copilot.draft("plan it", task_id="AIPOS-TUI-CLAIM")
        self.assertTrue(proposal.truth_reread)
        self.assertIn("queue", copilot.memory.l0_truth)
        self.assertIn("task", copilot.memory.l0_truth)
        # egress: the truth snapshot is in the messages sent to the LLM
        blob = json.dumps(llm.calls[0])
        self.assertIn("AIPOS-TUI-CLAIM", blob)

    # --- T4 discipline (a): compact never touches L0/L1 ---
    def test_compact_never_touches_l0(self) -> None:
        mem = CopilotMemory(l0_truth={"queue": {"x": 1}}, l1_index={"i": 2})
        for n in range(50):
            mem.record_chat("user", f"m{n}")
        l0_before = json.dumps(mem.l0_truth, sort_keys=True)
        l1_before = json.dumps(mem.l1_index, sort_keys=True)
        mem.compact(keep_last=10)
        self.assertEqual(json.dumps(mem.l0_truth, sort_keys=True), l0_before)
        self.assertEqual(json.dumps(mem.l1_index, sort_keys=True), l1_before)
        self.assertEqual(len(mem.l3_chat), 10)

    # --- T4 discipline (c): persisted chat marked non-truth ---
    def test_persisted_chat_is_non_truth(self) -> None:
        llm = FakeLLM()
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            copilot.draft("plan it", task_id="AIPOS-TUI-CLAIM")
        self.assertTrue(copilot.memory.l3_chat)
        self.assertTrue(all(turn.truth is False for turn in copilot.memory.l3_chat))

    # --- T6: secrets fingerprint-only ---
    def test_llm_key_fingerprint_only(self) -> None:
        cfg = LLMConfig(base_url="http://x", api_key="super-secret-key")
        fp = cfg.key_fingerprint
        self.assertTrue(fp.startswith("sha256:"))
        self.assertNotIn("super-secret-key", fp)

    def test_copilot_token_fingerprint_only(self) -> None:
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, FakeLLM())
            fp = copilot.token_fingerprint
        self.assertTrue(fp.startswith("sha256:"))
        self.assertNotIn("copilot-secret", fp)

    # --- T3: Owner "proceed" — land_draft is an owner action under drafts/ only ---
    def test_owner_land_draft_writes_under_drafts(self) -> None:
        with self.gate() as url, self.conn() as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            rel = s.land_draft("# DRAFT\nbody", workspace_root=str(self.repo_root), draft_rel_path="5_tasks/drafts/copilot-x.md")
        self.assertEqual(rel, "5_tasks/drafts/copilot-x.md")
        self.assertTrue((self.repo_root / rel).exists())

    def test_owner_land_draft_rejects_non_drafts_path(self) -> None:
        with self.gate() as url, self.conn() as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            with self.assertRaises(ValueError):
                s.land_draft("x", workspace_root=str(self.repo_root), draft_rel_path="5_tasks/queue/pending/sneaky.md")

    # --- 3-mode cycle ---
    def test_toggle_mode_three_cycle(self) -> None:
        with self.gate() as url, self.conn() as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            self.assertEqual(s.mode, OBSERVE_MODE)
            self.assertEqual(s.toggle_mode(), CONFIRM_MODE)
            self.assertEqual(s.toggle_mode(), COPILOT_MODE)
            self.assertEqual(s.toggle_mode(), OBSERVE_MODE)


if __name__ == "__main__":
    unittest.main()
