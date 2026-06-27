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
import textwrap
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from tools.aipos_cli.confirm_client import GateClient, load_owner_token
from tools.lybra_tui.copilot import (
    CHAT_KEEP_LAST,
    ChatReply,
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

    # --- T7: dependency isolation (order-independent subprocess; mirrors AIPOS-218 WS7) ---
    def test_copilot_module_imports_no_textual(self) -> None:
        """Verify in a FRESH SUBPROCESS that importing tools.lybra_tui.copilot alone does not pull
        in textual. A global ``sys.modules`` check is order-dependent (another test — e.g. the app
        layer — may have already loaded textual into THIS process); the subprocess makes the
        isolation claim robust regardless of test order. copilot.py must stay textual-free."""
        import os
        import subprocess

        repo = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        check = (
            "import sys\n"
            "import tools.lybra_tui.copilot  # noqa\n"
            "loaded = any(k == 'textual' or k.startswith('textual.') for k in sys.modules)\n"
            "sys.exit(1 if loaded else 0)\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-c", check], capture_output=True, text=True, env=env
        )
        self.assertEqual(
            proc.returncode, 0,
            f"copilot.py must not import textual (order-independent probe):\nstderr: {proc.stderr}",
        )

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

    # --- AIPOS-222: read-only conversational chat() is zero-write / records memory ---
    def test_chat_returns_nl_answer_and_writes_no_file(self) -> None:
        # RED LINE: chat() writes NO file. Snapshot the full workspace file set before/after.
        llm = FakeLLM(reply="Here is my read-only advice. Generate a draft?")
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            before = self._all_files()
            reply = copilot.chat("how should I structure this project?")
            after = self._all_files()
        self.assertIsInstance(reply, ChatReply)
        self.assertIn("advice", reply.content)
        self.assertEqual(before, after, "chat() must not write any file")

    def test_chat_records_user_and_assistant_turns_non_truth(self) -> None:
        llm = FakeLLM(reply="ok")
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            copilot.chat("plan it with me")
        roles = [(t.role, t.content) for t in copilot.memory.l3_chat]
        self.assertIn(("user", "plan it with me"), roles)
        self.assertIn(("assistant", "ok"), roles)
        self.assertTrue(all(t.truth is False for t in copilot.memory.l3_chat))

    def test_chat_uses_read_only_truth_rehydrate_egress(self) -> None:
        # chat() uses the SAME read-only read-tools as draft(): truth is rehydrated + sent egress.
        llm = FakeLLM(reply="answer")
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            copilot.chat("what is in the queue?")
        self.assertIn("queue", copilot.memory.l0_truth)
        blob = json.dumps(llm.calls[0])
        self.assertIn("queue", blob)

    def test_chat_session_stays_copilot_role_scopes_empty(self) -> None:
        # The chat path never escalates: the session credential stays role=copilot / scopes [].
        llm = FakeLLM(reply="answer")
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            copilot.chat("hi")
            basis = copilot._read("lybra_queue_list").get("scope_basis", {})
        self.assertEqual(basis.get("role"), "copilot")
        self.assertEqual(basis.get("scopes"), [])

    def test_chat_calls_no_write_or_confirm_tool(self) -> None:
        # chat() must call NO write/confirm/publish/file operation. Inspect its CODE (strip the
        # docstring, which legitimately uses the words "confirm"/"publish" in prose) for call-like
        # tokens that would indicate a write/confirm/publish/file op.
        import ast
        import inspect

        chat_src = inspect.getsource(CopilotSession.chat)
        func = ast.parse(textwrap.dedent(chat_src)).body[0]
        func.body = [n for n in func.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        code_only = ast.unparse(func)
        for forbidden in ("write_text", "open(", "_confirm", "land_draft", "draft_publish", "preview_publish", "owner_confirmation_token"):
            self.assertNotIn(forbidden, code_only, f"chat() code must not reference {forbidden}")

    def test_chat_auto_compacts_l3_but_l0_truth_byte_identical(self) -> None:
        # RED LINE: auto-compact trims L3 chat to CHAT_KEEP_LAST while L0 truth is BYTE-IDENTICAL
        # before/after (truth is never trimmed). Drive enough turns to exceed the threshold.
        llm = FakeLLM(reply="ok")
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            copilot.rehydrate_truth()  # populate L0 once with a real read-tool snapshot
            # RF-5 re-reads truth on every chat() turn, and the gate's validate verdict embeds a
            # wall-clock `validated_at` timestamp — so the live L0 snapshot legitimately differs
            # second-to-second. This test isolates the CLAIM under test (compaction never trims L0)
            # from that orthogonal RF-5 churn by pinning the re-read to a fixed snapshot; the L0
            # byte-identity then proves compaction itself leaves truth untouched (deterministically).
            fixed = json.loads(json.dumps(copilot.memory.l0_truth, sort_keys=True))
            copilot.rehydrate_truth = lambda *a, **k: copilot.memory.l0_truth.update(fixed) or copilot.memory.l0_truth  # type: ignore[method-assign]
            l0_before = json.dumps(copilot.memory.l0_truth, sort_keys=True)
            last = None
            # each chat() adds 2 turns (user+assistant); run well past the threshold.
            for n in range(CHAT_KEEP_LAST + 10):
                last = copilot.chat(f"turn {n}")
            l0_after = json.dumps(copilot.memory.l0_truth, sort_keys=True)
        self.assertLessEqual(len(copilot.memory.l3_chat), CHAT_KEEP_LAST)
        self.assertEqual(l0_before, l0_after, "L0 truth must be byte-identical across compaction")
        self.assertTrue(last.compacted, "the final chat turn should report a compaction occurred")

    # --- AIPOS-222: read-only usage telemetry surfaced on ChatReply (zero-write) ---
    def test_chat_surfaces_read_only_usage_telemetry_and_writes_nothing(self) -> None:
        # The LLM client captures token usage from its HTTP response; chat() surfaces it on
        # ChatReply as pure observability. RED LINE: capturing/surfacing usage writes NO file and
        # changes no scope. Here a FakeLLM exposes `last_usage` like LLMClient does.
        from tools.lybra_tui.copilot import Usage

        class UsageLLM(FakeLLM):
            def __init__(self) -> None:
                super().__init__(reply="advice")
                self.last_usage = Usage(prompt_tokens=2400, completion_tokens=7100)

        llm = UsageLLM()
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            before = self._all_files()
            reply = copilot.chat("plan with usage")
            after = self._all_files()
        self.assertIsNotNone(reply.usage)
        self.assertEqual(reply.usage.prompt_tokens, 2400)
        self.assertEqual(reply.usage.completion_tokens, 7100)
        self.assertEqual(before, after, "surfacing usage telemetry must not write any file")

    def test_chat_usage_is_none_when_provider_omits_it(self) -> None:
        # Honest fallback: when the provider returns no usage, ChatReply.usage is None (the TUI then
        # shows a `~`-marked estimate — never a fabricated count).
        llm = FakeLLM(reply="advice")  # FakeLLM has no last_usage attribute
        with self.gate() as url, self.conn() as cpath:
            copilot = self._copilot(url, cpath, llm)
            reply = copilot.chat("plan without usage")
        self.assertIsNone(reply.usage)

    def test_llm_client_captures_usage_from_response_read_only(self) -> None:
        # LLMClient.complete() captures usage from the /chat/completions JSON into last_usage —
        # pure read of the HTTP response (no file write, no scope change). Stub the opener.
        import io
        from tools.lybra_tui.copilot import LLMClient, LLMConfig

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        payload = json.dumps({
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        }).encode("utf-8")
        client = LLMClient(LLMConfig(base_url="http://x", api_key="k"))
        client._opener.open = lambda req, timeout=None: _Resp(payload)  # type: ignore[assignment]
        out = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "hi")
        self.assertIsNotNone(client.last_usage)
        self.assertEqual(client.last_usage.prompt_tokens, 11)
        self.assertEqual(client.last_usage.completion_tokens, 22)

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
