from __future__ import annotations

import importlib.util
import unittest

# The Textual app layer is exercised only in the `tui` CI lane (textual installed).
# In the gate/core lane (no textual) this whole module skips — which itself proves the
# dependency isolation: the suite is green without Textual.
_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class TuiAppTests(unittest.TestCase):
    def test_build_app_is_constructable(self) -> None:
        # AIPOS-216: assert the REAL call signature run_tui uses — build_app(session, copilot,
        # workspace_root=...). The old 1-arg form passed in isolation while `lybra tui` crashed.
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import LybraTui, build_app

        session = MagicMock()
        session.status_line.return_value = "gate ... · token sha256:x · read-only-view"
        copilot = MagicMock()
        app = build_app(session, copilot, workspace_root="/tmp/ws")
        self.assertIsInstance(app, LybraTui)
        # AIPOS-221: Shift+Tab kept as a convenience; Esc cancel/reject still bound.
        keys = {b.key for b in LybraTui.BINDINGS}
        self.assertIn("shift+tab", keys)
        self.assertIn("escape", keys)

    def test_agents_command_is_oneshot_readonly_projection(self) -> None:
        # AIPOS-234: /agents reads the SAME queue observation /queue uses (read-only) and renders a
        # by-agent snapshot once. Assert it calls only observe("queue"), renders the grouped owner +
        # the "not live" disclosure, and performs no mutation.
        from unittest.mock import MagicMock

        from tools.lybra_tui.agents_view import NOT_LIVE_LABEL
        from tools.lybra_tui.app import build_app

        session = MagicMock()
        session.status_line.return_value = "gate ... · read-only-view"
        session.observe.return_value = {
            "data": {"tasks": [
                {"task_id": "AIPOS-9", "queue_state": "claimed", "claimed_by": "dev.codex.local"},
            ]}
        }
        app = build_app(session, MagicMock(), workspace_root="/tmp/ws")
        app._pre = MagicMock()      # bypass DOM mount
        app._system = MagicMock()

        app._cmd_agents()

        session.observe.assert_called_once_with("queue")  # one-shot, single read
        rendered = app._pre.call_args.args[0]
        self.assertIn("dev.codex.local", rendered)         # grouped by owning instance
        self.assertIn("AIPOS-9", rendered)
        self.assertIn(NOT_LIVE_LABEL, rendered)            # honest disclosure rendered
        # read-only: no mutating session entrypoints touched
        session.confirm_gates.assert_not_called()

    def test_apply_cjk_kitty_fix_reduces_to_disambiguate_only(self) -> None:
        # AIPOS-237 (F-o3-12a): the fix reduces the kitty enable flag to DISAMBIGUATE-only so IME
        # CJK is delivered as plain UTF-8. Assert the RESULT VALUES (not merely "it ran").
        from textual.drivers import linux_driver as ld
        from tools.lybra_tui.app import apply_cjk_kitty_fix

        saved = (
            ld.KITTY_DISAMBIGUATE_ESCAPE_CODES,
            ld.KITTY_REPORT_ALL_KEYS,
            ld.KITTY_REPORT_ASSOCIATED_TEXT,
        )
        try:
            apply_cjk_kitty_fix()
            self.assertEqual(ld.KITTY_REPORT_ASSOCIATED_TEXT, 0)
            self.assertEqual(ld.KITTY_REPORT_ALL_KEYS, 0)
            self.assertEqual(ld.KITTY_DISAMBIGUATE_ESCAPE_CODES, 1)
            # the driver would OR these -> exactly \x1b[>1u (DISAMBIGUATE only)
            flag = (
                ld.KITTY_DISAMBIGUATE_ESCAPE_CODES
                | ld.KITTY_REPORT_ALL_KEYS
                | ld.KITTY_REPORT_ASSOCIATED_TEXT
            )
            self.assertEqual(flag, 1)
        finally:
            (
                ld.KITTY_DISAMBIGUATE_ESCAPE_CODES,
                ld.KITTY_REPORT_ALL_KEYS,
                ld.KITTY_REPORT_ASSOCIATED_TEXT,
            ) = saved

    def test_apply_cjk_kitty_fix_fails_loud_on_missing_constant(self) -> None:
        # A Textual rename/refactor must FAIL LOUD, never a silent no-op.
        from textual.drivers import linux_driver as ld
        from tools.lybra_tui.app import apply_cjk_kitty_fix

        saved = ld.KITTY_REPORT_ASSOCIATED_TEXT
        try:
            del ld.KITTY_REPORT_ASSOCIATED_TEXT
            with self.assertRaises(RuntimeError) as cm:
                apply_cjk_kitty_fix()
            self.assertIn("KITTY_REPORT_ASSOCIATED_TEXT", str(cm.exception))
        finally:
            ld.KITTY_REPORT_ASSOCIATED_TEXT = saved

    def test_transcript_widgets_stay_selectable(self) -> None:
        # AIPOS-237 (F-o3-12b): copy is native terminal selection (mouse capture is off, see
        # test_run_tui...). Assert nothing disables Textual/terminal text selection.
        from tools.lybra_tui.app import LybraTui

        self.assertTrue(LybraTui.ALLOW_SELECT)

    def test_run_tui_constructs_app_through_real_call_path(self) -> None:
        # AIPOS-216 regression for the build_app signature drift that crashed `lybra tui`:
        # walk the actual run_tui → build_app path (with copilot + workspace_root) with .run()
        # mocked, so a future factory/caller signature mismatch fails HERE, not at user launch.
        from unittest.mock import MagicMock, patch

        from tools.lybra_tui import __main__ as M
        from tools.lybra_tui.app import LybraTui
        from tools.lybra_tui.state import COPILOT_MODE

        session = MagicMock()
        copilot = MagicMock()
        with patch.object(M.TuiSession, "connect", return_value=session) as connect, \
             patch.object(M, "_maybe_build_copilot", return_value=copilot), \
             patch.object(LybraTui, "run", autospec=True) as run:
            rc = M.run_tui(
                gate_url="http://127.0.0.1:7118",
                connection_json="/tmp/connection.json",
                project="p",
                workspace_root="/tmp/ws",
                llm_base_url="http://llm",
                llm_key_env="LYBRA_PLANCHAT_LLM_KEY",
                llm_model="m",
            )
        self.assertEqual(rc, 0)
        connect.assert_called_once()
        # copilot present → first screen is copilot mode, and the app was actually constructed
        self.assertEqual(session.mode, COPILOT_MODE)
        run.assert_called_once()
        constructed = run.call_args.args[0]
        self.assertIsInstance(constructed, LybraTui)
        # AIPOS-237 (F-o3-12b): the TUI runs with mouse capture OFF so the terminal keeps native
        # selection + copy (Claude-Code parity). Assert the real launch passes mouse=False.
        self.assertEqual(run.call_args.kwargs.get("mouse"), False)

    def test_session_active_project_state_and_status_line(self) -> None:
        # AIPOS-230 §2: client-side active-project state mirrors set_mode; status line shows it.
        from unittest.mock import MagicMock

        from tools.lybra_tui.state import TuiSession

        session = TuiSession(gate_url="http://x", _client=MagicMock())
        self.assertIsNone(session.active_project)
        self.assertEqual(session.set_active_project("beta"), "beta")
        self.assertEqual(session.active_project, "beta")
        self.assertIn("project beta", session.status_line())
        with self.assertRaises(ValueError):
            session.set_active_project("")  # non-empty required


def _make_session(mode: str = "copilot"):
    """A read-only stub TuiSession with a stable status line (no network)."""
    from unittest.mock import MagicMock

    from tools.lybra_tui.state import COPILOT_MODE

    session = MagicMock()
    session.mode = COPILOT_MODE if mode == "copilot" else mode
    session.status_line.return_value = "gate stub · token sha256:x · role owner · mode copilot"
    return session


def _make_chat_copilot():
    """A copilot mock whose chat() returns a canned ChatReply (no network)."""
    from unittest.mock import MagicMock

    from tools.lybra_tui.copilot import ChatReply

    copilot = MagicMock()
    copilot.chat.return_value = ChatReply(content="advice", compacted=False)
    return copilot


def _make_proposal(*, conformant=True, needs_bundle=False, blocking=None):
    from tools.lybra_tui.copilot import DraftProposal

    return DraftProposal(
        intent="do a thing",
        content="---\ntask_id: AIPOS-DOC-1\n---\nbody\n",
        project="p",
        truth_reread=True,
        task_id="AIPOS-DOC-1",
        conformant=conformant,
        needs_bundle=needs_bundle,
        blocking_reasons=blocking or [],
        draft_rel_path="5_tasks/drafts/AIPOS-DOC-1.md",
    )


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class TuiAppPilotTests(unittest.IsolatedAsyncioTestCase):
    """App-layer behavior via Textual's run_test()/Pilot (tui lane only)."""

    async def test_nl_submit_routes_to_chat_not_an_instant_card(self) -> None:
        # AIPOS-222: an NL line is a CONVERSATIONAL turn → copilot.chat() OFF the event loop, NOT
        # an instant card. draft_task_card is NOT called; a "generate draft?" offer is armed.
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="here is my read-only advice", compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "请生成一个任务 plan this"
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_offer:
                    break
                await asyncio.sleep(0.02)
            copilot.chat.assert_called_once_with("请生成一个任务 plan this")
            copilot.draft_task_card.assert_not_called()
            self.assertTrue(app._pending_offer)
            self.assertIsNone(app._pending_proposal)
            # inline thinking line cleared after the result, prompt re-enabled.
            self.assertIsNone(app._thinking)
            self.assertFalse(app.query_one("#cmd").disabled)

    async def test_affirmative_reply_while_offer_pending_routes_to_draft(self) -> None:
        # AIPOS-222 Owner ruling 1: an affirmative reply ("yes"/是/好/可以) IMMEDIATELY AFTER an
        # offer consents → draft_task_card. (A bare affirmative with NO pending offer is just chat.)
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="advice", compacted=False)
        copilot.draft_task_card.return_value = _make_proposal(conformant=True)
        copilot.available_context_bundles.return_value = []
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "plan a project"
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_offer:
                    break
                await asyncio.sleep(0.02)
            inp.text = "是"  # affirmative (zh), trimmed/case-insensitive set
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_proposal is not None:
                    break
                await asyncio.sleep(0.02)
            copilot.draft_task_card.assert_called_once()
            self.assertIsNotNone(app._pending_proposal)
            self.assertFalse(app._pending_offer)

    async def test_affirmative_with_no_pending_offer_is_just_chat(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="advice", compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "yes"  # no offer pending → ordinary NL chat turn
            await pilot.press("enter")
            for _ in range(50):
                if copilot.chat.called:
                    break
                await asyncio.sleep(0.02)
            copilot.chat.assert_called_once_with("yes")
            copilot.draft_task_card.assert_not_called()

    async def test_slash_draft_routes_to_draft_task_card(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app

        copilot = MagicMock()
        copilot.draft_task_card.return_value = _make_proposal(conformant=True)
        copilot.available_context_bundles.return_value = []
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "/draft"
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_proposal is not None:
                    break
                await asyncio.sleep(0.02)
            copilot.draft_task_card.assert_called_once()

    async def test_inline_thinking_indicator_appears_then_clears(self) -> None:
        # AIPOS-222 ruling 2: an inline "· thinking… (Ns)" line appears while the worker runs and
        # is cleared when the result arrives. Honest wording — no fabricated "effort".
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        gate = asyncio.Event()

        def _slow_chat(intent):
            # block in the worker thread until the test observes the thinking line
            import time
            for _ in range(200):
                if gate.is_set():
                    break
                time.sleep(0.01)
            return ChatReply(content="done", compacted=False)

        copilot = MagicMock()
        copilot.chat.side_effect = _slow_chat
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "think about this"
            await pilot.press("enter")
            # thinking line present while the worker is mid-flight
            for _ in range(50):
                if app._thinking is not None:
                    break
                await asyncio.sleep(0.02)
            self.assertIsNotNone(app._thinking)
            # AIPOS-222 fix 5: the honest verb word is now "Thinking…" (case-insensitive check so
            # this assertion survives the wording polish from "thinking…" → "✽ Thinking…").
            self.assertIn("thinking", str(app._thinking.render()).lower())
            gate.set()  # let the worker finish
            for _ in range(100):
                if app._thinking is None:
                    break
                await asyncio.sleep(0.02)
            self.assertIsNone(app._thinking)

    async def test_compaction_notice_rendered_when_chat_reports_it(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import _COMPACTION_NOTICE, build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="advice", compacted=True)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "a long conversation"
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_offer:
                    break
                await asyncio.sleep(0.02)
            texts = [str(w.render()) for w in app.query("#conversation Static")]
            self.assertTrue(any(_COMPACTION_NOTICE in t for t in texts))

    async def test_up_arrow_recalls_last_input_when_dropdown_closed(self) -> None:
        # AIPOS-222: ↑ recalls the last submitted input when the `/` autocomplete is CLOSED.
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="ok", compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            await pilot.click("#cmd")
            inp.text = "first intent"
            await pilot.press("enter")
            await asyncio.sleep(0.05)
            self.assertFalse(app.query_one("#ac").display)  # dropdown closed
            await pilot.press("up")
            self.assertEqual(inp.text, "first intent")

    async def test_up_down_go_to_dropdown_when_open(self) -> None:
        # CLEAR PRECEDENCE: when the `/` autocomplete is OPEN, ↑/↓ navigate the dropdown (NOT
        # history). The prompt keeps its `/`-token text; focus moves to the OptionList.
        from tools.lybra_tui.app import build_app

        app = build_app(_make_session(), _make_chat_copilot(), workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            await pilot.click("#cmd")
            inp.text = "/"  # single-line `/` token → TextArea.Changed opens the dropdown
            await pilot.pause()
            ac = app.query_one("#ac")
            self.assertTrue(ac.display)  # dropdown open
            await pilot.press("up")
            self.assertEqual(inp.text, "/")  # history NOT recalled while dropdown open
            self.assertIs(app.focused, ac)

    async def test_slash_prefixed_routes_to_command_handler(self) -> None:
        from unittest.mock import MagicMock, patch

        from tools.lybra_tui.app import build_app

        app = build_app(_make_session(), MagicMock(), workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            with patch.object(app, "_handle_command") as handler:
                inp = app.query_one("#cmd")
                inp.text = "/help"
                await pilot.press("enter")
            handler.assert_called_once_with("/help")

    async def test_proceed_lands_draft_and_stages_dry_run_but_never_publishes(self) -> None:
        # RED LINE: /proceed lands a draft + stages a publish DRY-RUN, but performs NO publish.
        # Assert: no publish record written, nothing lands in queue/pending; only land_draft +
        # preview_publish (dry-run) are invoked — confirm/publish are NEVER called.
        import os
        import tempfile
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app

        with tempfile.TemporaryDirectory() as ws:
            session = _make_session()
            # land_draft / preview_publish are the ONLY truth-adjacent ops /proceed may call.
            preview = MagicMock()
            preview.dry_run_token = "dry-123"
            session.preview_publish.return_value = preview

            def _land(content, *, workspace_root, draft_rel_path):
                path = os.path.join(workspace_root, draft_rel_path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                return draft_rel_path

            session.land_draft.side_effect = _land

            copilot = MagicMock()
            app = build_app(session, copilot, workspace_root=ws)
            async with app.run_test() as pilot:
                app._pending_proposal = _make_proposal(conformant=True)
                inp = app.query_one("#cmd")
                inp.text = "/proceed"
                await pilot.press("enter")

            # dry-run staged (preview_publish called); confirm/publish NEVER called.
            session.preview_publish.assert_called_once()
            session.confirm.assert_not_called()
            self.assertFalse(session.client.confirm.called)
            # draft landed under drafts/, and NOTHING landed in queue/pending/.
            self.assertTrue(os.path.isfile(os.path.join(ws, "5_tasks/drafts/AIPOS-DOC-1.md")))
            self.assertFalse(os.path.isdir(os.path.join(ws, "queue", "pending")))
            # No publish record anywhere under the workspace.
            for root, _dirs, files in os.walk(ws):
                for name in files:
                    self.assertNotIn("publish", name.lower())

    async def test_copilot_session_built_read_only(self) -> None:
        # The TUI is constructed with whatever copilot session run_tui builds; assert the copilot
        # the app uses is the read-only one (role="copilot"). We assert the construction path:
        # _maybe_build_copilot connects with role="copilot" (the welded read-only credential).
        import inspect

        from tools.lybra_tui import __main__ as M

        src = inspect.getsource(M._maybe_build_copilot)
        self.assertIn('role="copilot"', src)

    async def test_exit_command_quits(self) -> None:
        # AIPOS-222: `/exit` is an alias of `/quit` — both call App.exit().
        from unittest.mock import MagicMock, patch

        from tools.lybra_tui.app import build_app

        app = build_app(_make_session(), MagicMock(), workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            with patch.object(app, "exit") as exit_:
                inp = app.query_one("#cmd")
                inp.text = "/exit"
                await pilot.press("enter")
            exit_.assert_called_once()

    async def test_compact_calls_memory_compact_chat_only_and_shows_notice(self) -> None:
        # AIPOS-222: `/compact` calls the EXISTING CopilotMemory.compact(keep_last=…), trims L3
        # chat ONLY (L0 truth / L1 index untouched), shows the compaction notice, and is read-only.
        import asyncio

        from tools.lybra_tui.app import _COMPACTION_NOTICE, build_app
        from tools.lybra_tui.copilot import CHAT_KEEP_LAST, CopilotMemory

        # A REAL CopilotMemory so we exercise the actual compact() discipline (not a mock).
        memory = CopilotMemory(
            l0_truth={"queue": "TRUTH-SNAPSHOT"},
            l1_index={"idx": "DERIVED"},
        )
        for i in range(CHAT_KEEP_LAST + 12):  # well past the keep-last threshold
            memory.record_chat("user", f"turn {i}")
        from unittest.mock import MagicMock

        copilot = MagicMock()
        copilot.memory = memory
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "/compact"
            await pilot.press("enter")
            await asyncio.sleep(0.05)
            # L3 chat trimmed to keep_last; L0 truth + L1 index untouched (read-only discipline).
            self.assertLessEqual(len(memory.l3_chat), CHAT_KEEP_LAST)
            self.assertEqual(memory.l0_truth, {"queue": "TRUTH-SNAPSHOT"})
            self.assertEqual(memory.l1_index, {"idx": "DERIVED"})
            # compaction notice rendered in the transcript.
            texts = [str(w.render()) for w in app.query("#conversation Static")]
            self.assertTrue(any(_COMPACTION_NOTICE in t for t in texts))

    async def test_footer_renders_model_and_ctx(self) -> None:
        # AIPOS-222 ruling 4: the footer under the input shows `<model> · <dir> | Ctx: <n>%`.
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import CopilotMemory, LLMClient, LLMConfig

        copilot = MagicMock()
        copilot._llm = LLMClient(LLMConfig(base_url="http://llm", api_key="k", model="claude-sonnet-4-6"))
        copilot.memory = CopilotMemory()
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            footer = str(app.query_one("#ctxbar").render())
            self.assertIn("claude-sonnet-4-6", footer)  # model id from LLMConfig
            self.assertIn("Ctx:", footer)               # honest context estimate, labelled
            self.assertIn("%", footer)

    async def test_footer_shows_no_llm_without_copilot(self) -> None:
        # No copilot/LLM → footer reads `no-llm` and Ctx: 0% (honest: nothing is being sent).
        from tools.lybra_tui.app import build_app

        app = build_app(_make_session(), None, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            footer = str(app.query_one("#ctxbar").render())
            self.assertIn("no-llm", footer)
            self.assertIn("Ctx:", footer)

    async def test_user_turn_style_is_not_green(self) -> None:
        # AIPOS-222 ruling 1: the Owner's turn must NOT be LYBRA_GREEN — it is bold DEFAULT fg.
        # green is reserved for the logo/frame, the "lybra" line, the thinking dot, and the offer.
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import LybraTui, build_app
        from tools.lybra_tui.presentation import LYBRA_GREEN

        # The CSS rule for .turn-user carries no color (so it inherits the default fg) and is bold.
        self.assertNotIn(f".turn-user {{ color: {LYBRA_GREEN}", LybraTui.CSS)
        self.assertIn(".turn-user { text-style: bold; }", LybraTui.CSS)

        app = build_app(_make_session(), MagicMock(), workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            app._user("hello owner")
            user_widgets = [w for w in app.query("#conversation Static") if "turn-user" in w.classes]
            self.assertTrue(user_widgets)
            styles = app.get_css_variables()  # smoke: CSS parsed without error
            self.assertIsInstance(styles, dict)

    async def test_draft_offer_affordance_is_present_and_prominent(self) -> None:
        # AIPOS-222 ruling 3: after a chat reply the app appends a bilingual, prominent consent
        # affordance (its own turn-offer line: green + bold + italic) with a blank spacer above it.
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import _DRAFT_OFFER, build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="advice", compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "plan something"
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_offer:
                    break
                await asyncio.sleep(0.02)
            offer_widgets = [w for w in app.query("#conversation Static") if "turn-offer" in w.classes]
            self.assertTrue(offer_widgets, "the consent affordance line is missing")
            offer_text = str(offer_widgets[-1].render())
            self.assertEqual(offer_text, _DRAFT_OFFER)
            # bilingual: carries both the Chinese and the English consent hint.
            self.assertIn("生成", offer_text)
            self.assertIn("draft", offer_text)

    async def test_cjk_input_is_accepted_by_chat_prompt(self) -> None:
        # Ruling 4 (fix 3): the chat prompt is a multi-line TextArea (PromptArea) that accepts CJK /
        # wide chars natively — a TextArea carries no restrict/type filter that could drop them.
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import PromptArea, build_app

        app = build_app(_make_session(), MagicMock(), workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            # The prompt is a TextArea-based PromptArea (multi-line), not a single-line Input.
            self.assertIsInstance(inp, PromptArea)
            # A TextArea has no restrict/type filters at all (so none can silently drop CJK input).
            self.assertFalse(hasattr(inp, "restrict"))
            await pilot.click("#cmd")
            for ch in "中文 test 你好":
                inp.insert(ch)
            self.assertEqual(inp.text, "中文 test 你好")
            inp.insert("汉字宽字符")
            self.assertEqual(inp.text, "中文 test 你好汉字宽字符")

    async def test_prompt_border_uses_lybra_green_two_rules(self) -> None:
        # AIPOS-222 fix 1: the prompt's chrome is a claude-code-style two-rule (top+bottom) border
        # in LYBRA_GREEN — NOT Textual's default full blue focus box (which is overridden away on
        # all sides, then re-drawn as just the top/bottom rules for both focused + unfocused).
        from tools.lybra_tui.app import LybraTui
        from tools.lybra_tui.presentation import LYBRA_GREEN

        css = LybraTui.CSS
        # centered `─` rules (solid), not `hkey`'s edge-pinned ▔/▁ which read as a wider gap.
        self.assertIn(f"border-top: solid {LYBRA_GREEN};", css)
        self.assertIn(f"border-bottom: solid {LYBRA_GREEN};", css)
        # the full default box is overridden away (border: none) before the two rules are drawn,
        # and the focus rule re-asserts the same green rules (so it never flashes blue).
        self.assertIn("#cmd:focus", css)

    async def test_prompt_gutter_shows_green_caret(self) -> None:
        # AIPOS-222 fix 2: a green `>` prompt gutter sits in front of the multi-line prompt.
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import LybraTui, build_app
        from tools.lybra_tui.presentation import LYBRA_GREEN

        app = build_app(_make_session(), MagicMock(), workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            gutter = app.query_one("#prompt-gutter")
            self.assertEqual(str(gutter.render()), ">")
            # the gutter is styled in the brand green via the #prompt-gutter CSS rule
            # (assert the rule's properties without pinning their order — AIPOS-222 fix).
            gutter_rule = next(
                line for line in LybraTui.CSS.splitlines() if "#prompt-gutter" in line
            )
            self.assertIn("width: 2", gutter_rule)
            self.assertIn(LYBRA_GREEN, gutter_rule)

    async def test_shift_enter_and_ctrl_j_insert_newline_enter_submits(self) -> None:
        # AIPOS-222 fix 3: Enter SUBMITS the buffer; Shift+Enter and Ctrl+J insert a NEWLINE.
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="ok", compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            await pilot.click("#cmd")
            inp.insert("line one")
            await pilot.press("shift+enter")  # newline, NOT submit
            inp.insert("line two")
            await pilot.press("ctrl+j")        # newline, NOT submit
            inp.insert("line three")
            self.assertEqual(inp.text, "line one\nline two\nline three")
            copilot.chat.assert_not_called()   # nothing submitted yet
            await pilot.press("enter")          # NOW submit the whole multi-line buffer
            for _ in range(50):
                if copilot.chat.called:
                    break
                await asyncio.sleep(0.02)
            copilot.chat.assert_called_once_with("line one\nline two\nline three")

    async def test_up_moves_cursor_within_multiline_then_recalls_at_first_line(self) -> None:
        # AIPOS-222 fix 3: ↑/↓ precedence inside a multi-line buffer. With the cursor NOT on the
        # first line, ↑ moves the cursor up a line (TextArea default) and does NOT recall history;
        # only when the cursor is on the first line does ↑ recall the previous submitted line.
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content="ok", compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            await pilot.click("#cmd")
            # Submit a line so there is history to (not) recall.
            inp.insert("history line")
            await pilot.press("enter")
            await asyncio.sleep(0.05)
            # Compose a two-line buffer; cursor ends on the LAST (second) line.
            inp.insert("alpha")
            await pilot.press("shift+enter")
            inp.insert("beta")
            self.assertEqual(inp.text, "alpha\nbeta")
            self.assertFalse(inp.cursor_at_first_line)  # on line 2
            await pilot.press("up")                      # moves cursor to line 1 (NOT history)
            self.assertEqual(inp.text, "alpha\nbeta")    # buffer unchanged → no history recall
            self.assertTrue(inp.cursor_at_first_line)
            await pilot.press("up")                      # NOW on first line → history recall
            self.assertEqual(inp.text, "history line")

    async def test_markdown_answer_renders_fenced_code(self) -> None:
        # AIPOS-222 fix 4: a copilot answer with a fenced code block renders as a Textual Markdown
        # widget (Rich/Pygments highlighting), NOT a plain Static.
        import asyncio
        from unittest.mock import MagicMock

        from textual.widgets import Markdown

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply

        answer = "Here is code:\n\n```python\ndef f():\n    return 1\n```\n"
        copilot = MagicMock()
        copilot.chat.return_value = ChatReply(content=answer, compacted=False)
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "show me code"
            await pilot.press("enter")
            for _ in range(50):
                if app._pending_offer:
                    break
                await asyncio.sleep(0.02)
            md = app.query("#conversation Markdown")
            self.assertTrue(len(md) >= 1, "copilot answer must render as a Markdown widget")

    async def test_project_and_home_commands_are_in_command_list(self) -> None:
        # AIPOS-226 Slice 2: the local Owner actions appear in the /help + autocomplete set.
        from tools.lybra_tui.app import _COMMAND_NAMES

        self.assertIn("/project", _COMMAND_NAMES)
        self.assertIn("/home", _COMMAND_NAMES)

    async def test_slash_project_new_scaffolds_against_temp_home(self) -> None:
        # AIPOS-226 Slice 2: `/project new <name>` is a LOCAL Owner scaffold (not gate, not
        # copilot). It creates the project tree under the resolved home and prints a success line.
        import asyncio
        import os
        import tempfile
        from unittest.mock import MagicMock, patch

        from tools.lybra_tui.app import build_app

        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"LYBRA_HOME_ROOT": home}):
                app = build_app(_make_session(), MagicMock(), workspace_root="/tmp/ws")
                async with app.run_test() as pilot:
                    inp = app.query_one("#cmd")
                    inp.text = "/project new demo"
                    await pilot.press("enter")
                    await asyncio.sleep(0.1)
                    # scaffolded tree exists
                    self.assertTrue(os.path.isdir(os.path.join(home, "demo", "5_tasks", "queue", "pending")))
                    self.assertTrue(os.path.isfile(os.path.join(home, "demo", "project.json")))
                    # a success line was rendered in the transcript
                    texts = [str(w.render()) for w in app.query("#conversation Static")]
                    self.assertTrue(any("Created project root" in t for t in texts))

    async def test_slash_project_switch_sets_active_and_rebinds_copilot(self) -> None:
        # AIPOS-230 §1b: `/project switch <name>` is a LOCAL Owner action — writes the runtime
        # config (patched here), updates session state, and rebinds the copilot session's project.
        # No gate confirm / no token / no copilot write. (Scope enforcement stays in the gate.)
        # AIPOS-242 (F-o3-18) DISCLOSED UPDATE: the old assertion pinned the OPTIMISTIC output
        # ("Active project -> beta" printed without asking the gate) — which WAS the bug. The
        # switch now runs a gated verify probe; with the gate agreeing (stubbed observe), the
        # verified message is asserted instead. Intent (set active + rebind copilot) unchanged.
        import asyncio
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from tools.lybra_tui.app import build_app

        session = _make_session()
        session.observe.return_value = {"ok": True, "active_project": "beta", "projects": ["beta"]}
        copilot = MagicMock()
        copilot.project = "old"
        app = build_app(session, copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            with patch(
                "tools.lybra_tui.app.set_active_project",
                return_value=Path("/tmp/userhome/.lybra/config.json"),
            ) as setp:
                app.query_one("#cmd").text = "/project switch beta"
                await pilot.press("enter")
                await asyncio.sleep(0.1)
            setp.assert_called_once_with("beta")
            session.set_active_project.assert_called_once_with("beta")
            self.assertEqual(copilot.project, "beta")  # copilot session rebound
            session.observe.assert_called_with("project_status")  # the verify probe ran
            texts = [str(w.render()) for w in app.query("#conversation Static")]
            self.assertTrue(any("Gate now resolves 'beta'" in t and "✓" in t for t in texts))

    async def test_thinking_line_blinks_word_and_shows_token_field(self) -> None:
        # AIPOS-222 fix 5: the live thinking line shows a pulsing "✽ Thinking…" (marker+word blink
        # together) with an honest token field (↑ shown as a `~` estimate while in-flight); on
        # completion a final line shows ↑/↓ token counts.
        import asyncio
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.copilot import ChatReply, Usage

        gate = asyncio.Event()

        def _slow_chat(intent):
            import time
            for _ in range(300):
                if gate.is_set():
                    break
                time.sleep(0.01)
            return ChatReply(content="answer body", compacted=False, usage=Usage(prompt_tokens=2400, completion_tokens=7100))

        copilot = MagicMock()
        copilot.chat.side_effect = _slow_chat
        copilot.memory = None
        app = build_app(_make_session(), copilot, workspace_root="/tmp/ws")
        async with app.run_test() as pilot:
            inp = app.query_one("#cmd")
            inp.text = "think hard"
            await pilot.press("enter")
            for _ in range(50):
                if app._thinking is not None:
                    break
                await asyncio.sleep(0.02)
            self.assertIsNotNone(app._thinking)
            live = str(app._thinking.render())
            self.assertIn("Thinking", live)         # the honest verb word is present
            self.assertIn("↑", live)                # the up-token field is present while thinking
            self.assertIn("tokens", live)
            self.assertIn("~", live)                # in-flight ↑ is a marked estimate
            # the word pulses with the marker: the markup styles the whole "✽ Thinking…" run.
            app._thinking_dot_on = True
            on_text = app._thinking_text()
            app._thinking_dot_on = False
            off_text = app._thinking_text()
            self.assertNotEqual(on_text, off_text)  # the styled run toggles (blinks)
            self.assertIn("✽ Thinking…", on_text)
            gate.set()
            for _ in range(100):
                if app._thinking is None:
                    break
                await asyncio.sleep(0.02)
            self.assertIsNone(app._thinking)
            # final line shows the REAL ↑/↓ token counts (from ChatReply.usage), no `~`.
            sys_texts = [str(w.render()) for w in app.query("#conversation Static")]
            final = [t for t in sys_texts if "Thinking" in t and "↓" in t]
            self.assertTrue(final, "a final ↑/↓ token line should be rendered")
            self.assertIn("2.4k", final[-1])
            self.assertIn("7.1k", final[-1])


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class ProjectViewGateTruthTests(unittest.TestCase):
    """AIPOS-242 (Slice D) — the GATE is the single source of truth for the project view.

    /project list = the gate's own view (no client-side resolve_home_root guess, no silent
    fallback); /project switch = write THEN verify with a gated probe, four outcomes reported as
    measured (never optimistic — F-o3-18). The deny-message parse is pinned against the REAL
    product deny constructor so a format drift turns a test red before it breaks the client.
    """

    def _app(self, observe=None):
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app

        session = _make_session()
        session.active_project = "lybra"
        if observe is not None:
            if callable(observe):
                session.observe.side_effect = observe
            else:
                session.observe.return_value = observe
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        return app, session

    @staticmethod
    def _pre_text(app) -> str:
        return "\n".join(str(c.args[0]) for c in app._pre.call_args_list)

    # --- deny parse pin (client regex <-> REAL product deny format coupling) ---
    def test_deny_parse_pinned_against_real_product_deny(self) -> None:
        from tools.lybra_tui.app import _gate_active_from_deny
        from tools.mcp_server.tools import _project_scope_denied_result

        real_deny = _project_scope_denied_result(
            "active project 'demo' is not in the token's projects ['lybra']"
        )["structuredContent"]
        self.assertEqual(real_deny["error_code"], "PROJECT_SCOPE_DENIED")
        self.assertEqual(_gate_active_from_deny(real_deny), "demo")
        self.assertIsNone(_gate_active_from_deny({"message": "no project mentioned"}))

    # --- /project list = gate view ---
    def test_list_shows_gate_view_not_client_guess(self) -> None:
        app, session = self._app(
            observe={
                "ok": True,
                "source": "gate",
                "home_root": "/disposable/home",
                "active_project": "lybra",
                "resolution_error": None,
                "projects": ["demo", "lybra"],
            }
        )
        app._cmd_project_list()
        session.observe.assert_called_once_with("project_status")
        out = self._pre_text(app)
        self.assertIn("as resolved by the GATE", out)
        self.assertIn("/disposable/home", out)
        self.assertIn("demo", out)
        self.assertIn("* lybra", out)
        self.assertIn("Active (gate): lybra", out)

    def test_list_denied_shows_gate_active_not_dead_air(self) -> None:
        app, _ = self._app(
            observe={
                "error_code": "PROJECT_SCOPE_DENIED",
                "message": (
                    "Connection capability is project-scoped and does not authorize this project: "
                    "active project 'demo' is not in the token's projects ['lybra']"
                ),
            }
        )
        app._cmd_project_list()
        out = self._pre_text(app)
        self.assertIn("PROJECT_SCOPE_DENIED", out)
        self.assertIn("'demo'", out)  # what the gate ACTUALLY resolves, surfaced
        self.assertIn("/project switch", out)

    # --- /project switch: four outcomes, as measured ---
    def _switch(self, observe, name="demo"):
        from unittest.mock import patch as _patch

        app, session = self._app(observe=observe)
        with _patch("tools.lybra_tui.app.set_active_project", return_value="/fake/.lybra/config.json"):
            app._cmd_project_switch(name)
        return app, session

    def test_switch_branch1_verified_in_scope(self) -> None:
        app, _ = self._switch({"ok": True, "active_project": "demo", "projects": ["demo", "lybra"]})
        out = self._pre_text(app)
        self.assertIn("Gate now resolves 'demo' ✓", out)
        self.assertIn("verified via gated probe", out)
        self.assertNotIn("MISMATCH", out)

    def test_switch_branch2_gate_followed_token_out_of_scope(self) -> None:
        app, _ = self._switch(
            {
                "error_code": "PROJECT_SCOPE_DENIED",
                "message": "…: active project 'demo' is not in the token's projects ['lybra']",
            }
        )
        out = self._pre_text(app)
        self.assertIn("Gate now resolves 'demo' ✓", out)  # switch DID land (the live-demo state)
        self.assertIn("PROJECT_SCOPE_DENIED", out)
        self.assertNotIn("MISMATCH", out)

    def test_switch_branch3_env_pin_mismatch_is_loud(self) -> None:
        # env-pin simulation: the gate STILL resolves 'lybra' after switching to 'demo' — the old
        # code would have printed "The gate now resolves 'demo'" (silent failure). Now: LOUD.
        app, _ = self._switch({"ok": True, "active_project": "lybra", "projects": ["demo", "lybra"]})
        out = self._pre_text(app)
        self.assertIn("MISMATCH", out)
        self.assertIn("still resolves 'lybra'", out)
        self.assertIn("LYBRA_ACTIVE_PROJECT env pin", out)
        self.assertNotIn("✓", out)  # NEVER a green check on a mismatch

    def test_switch_branch3b_deny_naming_other_project_is_mismatch(self) -> None:
        app, _ = self._switch(
            {
                "error_code": "PROJECT_SCOPE_DENIED",
                "message": "…: active project 'lybra' is not in the token's projects ['other']",
            }
        )
        out = self._pre_text(app)
        self.assertIn("MISMATCH", out)
        self.assertNotIn("✓", out)

    def test_switch_branch4_probe_failure_never_claims_success(self) -> None:
        def _raise(*_a, **_k):
            raise RuntimeError("gate unreachable")

        app, _ = self._switch(_raise)
        out = self._pre_text(app)
        self.assertIn("could not VERIFY", out)
        self.assertIn("Not claiming success", out)
        self.assertNotIn("✓", out)
        self.assertNotIn("now resolves", out)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class ObserveErrorSurfaceTests(unittest.TestCase):
    """AIPOS-242 ROUND 2 (弱代理修复) — observe-class commands MUST surface gate errors loudly.

    The gate wraps errors (PROJECT_SCOPE_DENIED, ...) in {ok: False, error_code, message}, NOT
    {data: {summary}}. Chained .get("data",{}).get("summary",{}) COLLAPSED the error to {} and
    rendered it as success (silent failure dressed as success — the F-o3-18 congruent defect).
    With _observe_error_face: an error structure prints the code + message and skips success text.
    """

    def _app_with_session(self, observe_return):
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app

        session = _make_session()
        session.observe.return_value = observe_return
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        return app, session

    @staticmethod
    def _texts(app) -> str:
        pre = "\n".join(str(c.args[0]) for c in app._pre.call_args_list)
        sys = "\n".join(str(c.args[0]) for c in app._system.call_args_list)
        return pre + "\n" + sys

    def test_queue_denied_loud_not_dressed_as_success(self) -> None:
        denied = {
            "ok": False,
            "error_code": "PROJECT_SCOPE_DENIED",
            "message": "Connection capability is project-scoped: active project 'demo' is not in the token's projects ['lybra']",
        }
        app, _ = self._app_with_session(denied)
        app._handle_command("/queue")
        out = self._texts(app)
        self.assertIn("PROJECT_SCOPE_DENIED", out)
        self.assertIn("active project 'demo'", out)
        self.assertNotIn("Read-only queue summary", out)  # success text NOT printed on error

    def test_validate_denied_loud(self) -> None:
        denied = {"ok": False, "error_code": "PROJECT_SCOPE_DENIED", "message": "..."}
        app, _ = self._app_with_session(denied)
        app._handle_command("/validate")
        out = self._texts(app)
        self.assertIn("PROJECT_SCOPE_DENIED", out)
        self.assertNotIn("Read-only validator run", out)

    def test_agents_denied_loud(self) -> None:
        denied = {"ok": False, "error_code": "PROJECT_SCOPE_DENIED", "message": "..."}
        app, _ = self._app_with_session(denied)
        app._handle_command("/agents")
        out = self._texts(app)
        self.assertIn("PROJECT_SCOPE_DENIED", out)
        self.assertNotIn("Read-only snapshot", out)

    def test_audit_denied_loud(self) -> None:
        denied = {"ok": False, "error_code": "PROJECT_SCOPE_DENIED", "message": "..."}
        app, _ = self._app_with_session(denied)
        app._handle_command("/audit AIPOS-999")
        out = self._texts(app)
        self.assertIn("PROJECT_SCOPE_DENIED", out)
        self.assertNotIn("AIPOS-999:", out)  # the verdict line is NOT printed on error

    def test_queue_success_renders_summary(self) -> None:
        # confirm the success path still works (the shared error face returns None → normal render)
        success = {"ok": True, "data": {"summary": {"total": 3}}}
        app, _ = self._app_with_session(success)
        app._handle_command("/queue")
        out = self._texts(app)
        self.assertIn('"total": 3', out)
        self.assertIn("Read-only queue summary", out)


if __name__ == "__main__":
    unittest.main()
