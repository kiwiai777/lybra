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

from tools.aipos_cli.records import load_records
from tools.lybra_tui.state import TuiSession, OBSERVE_MODE, CONFIRM_MODE, COPILOT_MODE
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "aipos_cli" / "tests" / "fixtures"


class TuiStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "drafts").mkdir(parents=True, exist_ok=True)
        self.write_claim_task()
        shutil.copyfile(FIXTURE_ROOT / "drafts/valid_publishable_draft.md", self.repo_root / "5_tasks/drafts/aipos-39-publish-valid.md")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_claim_task(self) -> None:
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
            "owner-secret": {"role": "owner", "token_ref": "svc-owner", "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish"], "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:ownertui01"},
            "executor-secret": {"role": "executor", "token_ref": "svc-executor", "scopes": ["queue_claim", "queue_return"], "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:exectui01"},
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
    def conn(self, token: str) -> Iterator[Path]:
        path = self.repo_root / "connection.json"
        path.write_text(json.dumps({"tokens": [{"role": "owner" if token == "owner-secret" else "executor", "token": token, "token_ref": "svc"}]}), encoding="utf-8")
        yield path

    # T6 — dependency isolation: state imports no Textual
    def test_state_module_does_not_import_textual(self) -> None:
        self.assertNotIn("textual", sys.modules, "the TUI state/core path must not import textual")
        import tools.mcp_server.tools  # noqa: F401
        import tools.aipos_cli.confirm_client  # noqa: F401
        self.assertNotIn("textual", sys.modules, "gate / confirm-client must never import textual")

    # T1 — connect + status line (token never raw; scope reflected)
    def test_connect_status_line_no_token_leak(self) -> None:
        with self.gate() as url, self.conn("owner-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            line = s.status_line()
        self.assertIn("sha256:", line)
        self.assertNotIn("owner-secret", line)
        self.assertTrue(s.has_owner_confirm)
        self.assertIn("owner_confirm", s.scopes)

    def test_executor_session_has_no_owner_confirm(self) -> None:
        with self.gate() as url, self.conn("executor-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="executor")
        self.assertFalse(s.has_owner_confirm)

    def test_toggle_mode_cycles_observe_confirm_copilot(self) -> None:
        with self.gate() as url, self.conn("owner-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            self.assertEqual(s.mode, OBSERVE_MODE)
            self.assertEqual(s.toggle_mode(), CONFIRM_MODE)
            self.assertEqual(s.toggle_mode(), COPILOT_MODE)  # AIPOS-206
            self.assertEqual(s.toggle_mode(), OBSERVE_MODE)

    # T2 — observe via gate read-tool
    def test_observe_queue_via_read_tool(self) -> None:
        with self.gate() as url, self.conn("owner-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            data = s.observe("queue")
        self.assertEqual(data.get("operation"), "get_queue")

    # T3 — confirm panel reuse (claim): owner confirm -> confirmer on disk
    def test_owner_confirm_claim_records_confirmer(self) -> None:
        with self.gate() as url, self.conn("owner-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            gate = next(g for g in s.confirm_gates() if g["op"] == "claim")
            preview = s.preview_gate(gate, owner_policy_ref="owner_policy:aipos-166-supervised-test")
            result = s.confirm(preview, "OWNER_CONFIRMED")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(load_records(self.repo_root)["claims"][0]["metadata"].get("confirmer_role"), "owner")

    # T3b — publish confirm through the panel (AIPOS-204 reuse via GateClient publish op)
    def test_owner_confirm_publish_records_confirmer(self) -> None:
        with self.gate() as url, self.conn("owner-secret") as cpath:
            with patch.dict(os.environ, {"LYBRA_APPROVED_SCRATCH_ROOT": str(self.repo_root / "scratch")}, clear=False):
                s = TuiSession.connect(url, connection_json=cpath, role="owner")
                preview = s.preview_publish("5_tasks/drafts/aipos-39-publish-valid.md", actor="owner")
                result = s.confirm(preview, "OWNER_CONFIRMED")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(load_records(self.repo_root)["publishes"][0]["metadata"].get("confirmer_role"), "owner")

    # T4 — ★A1 through the panel: executor cannot confirm
    def test_executor_confirm_is_scope_denied(self) -> None:
        with self.gate() as url, self.conn("executor-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="executor")
            gate = next(g for g in s.confirm_gates() if g["op"] == "claim")
            preview = s.preview_gate(gate, owner_policy_ref="owner_policy:aipos-166-supervised-test")
            denied = s.confirm(preview, "OWNER_CONFIRMED")
        self.assertEqual(denied.get("error_code"), "SCOPE_DENIED")
        self.assertEqual(load_records(self.repo_root).get("claims", []), [])

    # T5 — Esc / empty literal = reject, nothing submitted
    def test_empty_literal_is_rejected_not_submitted(self) -> None:
        with self.gate() as url, self.conn("owner-secret") as cpath:
            s = TuiSession.connect(url, connection_json=cpath, role="owner")
            gate = next(g for g in s.confirm_gates() if g["op"] == "claim")
            preview = s.preview_gate(gate, owner_policy_ref="owner_policy:aipos-166-supervised-test")
            result = s.confirm(preview, "")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "CANCELLED")
        self.assertEqual(load_records(self.repo_root).get("claims", []), [])


if __name__ == "__main__":
    unittest.main()
