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



@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class TuiOwnerConfirmTests(unittest.TestCase):
    """AIPOS-244 — TUI Owner confirm 面端到端测试(真实输入路径,正向真相)."""

    def _mock_session(self, gates: list[dict], confirm_result: dict | None = None) -> Any:
        """创建 mock session,返回 gates 和 confirm 结果."""
        from unittest.mock import MagicMock
        session = MagicMock()
        session.confirm_gates.return_value = gates
        if gates:
            gate = gates[0]
            preview = {
                "operation": gate.get("op", "claim"),
                "task": {"task_id": gate.get("task_id", "AIPOS-999"), "assigned_to": gate.get("task", {}).get("assigned_to")},
                "actor": {"actor": gate.get("task", {}).get("assigned_to", "agent-01")},
            }
            session.preview_gate.return_value = preview
        if confirm_result:
            session.confirm_gate.return_value = confirm_result
        return session

    def _app_with_session(self, gates: list[dict], confirm_result: dict | None = None) -> tuple[Any, Any]:
        """创建 app + mock session."""
        from unittest.mock import MagicMock
        from tools.lybra_tui.app import build_app

        session = self._mock_session(gates, confirm_result)
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        app._user = MagicMock()
        return app, session

    def _texts(self, app: Any) -> str:
        """提取 app 的所有文本输出."""
        pre = "\n".join(str(c.args[0]) for c in app._pre.call_args_list)
        sys = "\n".join(str(c.args[0]) for c in app._system.call_args_list)
        return pre + "\n" + sys

    def _simulate_input(self, app: Any, text: str) -> None:
        """模拟用户输入(走真实输入拦截逻辑,不依赖 Textual 事件)."""
        text = text.strip()
        if not text:
            # 空输入
            if app._pending_confirm:
                app._pre("已取消 confirm。")
                app._pending_confirm = None
            return

        if text.startswith("/"):
            # AIPOS-244 R-h1(与 app.py 逐字同构): /confirm(affirmation 阶段)是肯定词;
            # 其余一切 / 命令先响亮取消 pending、再照常执行。
            if app._pending_confirm and app._pending_confirm.get("awaiting") == "affirmation" and text.lower() == "/confirm":
                app._user(text)
                app._execute_pending_confirm()
            else:
                # AIPOS-245 F-245-o3-4(与 app.py 逐字同构): 命令回显,让每个输出块可见地
                # 配对到产生它的输入(重复命令 ≠ 双打印,F-245-o3-2 症状形状)。
                app._user(text)
                if app._pending_confirm is not None:
                    app._pending_confirm = None
                    app._pre("已取消 confirm(你执行了其他命令)。")
                app._handle_command(text)
        elif app._pending_confirm and app._pending_confirm.get("awaiting") == "actor":
            # 等待 actor 输入
            app._user(text)
            actor = text.strip()
            gate = app._pending_confirm["gate"]
            op = app._pending_confirm["op"]
            task_id = app._pending_confirm["task_id"]

            if op == "claim":
                question = f"确认把 {task_id} 批给 {actor} (claim) 吗?"
            elif op == "return":
                question = f"确认接受 {actor} 的 {task_id} return 吗?"
            elif op == "publish":
                question = f"确认发布草稿 {task_id} 到队列吗?"
            else:
                question = f"确认执行 {op} {task_id} 吗?"

            # AIPOS-245 F-245-o3-4b(与 app.py 逐字同构): 单逻辑块 → 单 widget。
            app._pre(
                f"Preview: {op} {task_id}\n"
                f"归因给: {actor}\n"
                f"{question}\n"
                "输入 是 / yes / /confirm 确认; 其余输入取消。"
            )
            app._pending_confirm["actor"] = actor
            app._pending_confirm["awaiting"] = "affirmation"
        elif app._pending_confirm and app._pending_confirm.get("awaiting") == "affirmation" and text.lower() in ["是", "yes", "/confirm"]:
            # 肯定词 → 发射
            app._user(text)
            app._execute_pending_confirm()
        elif app._pending_confirm:
            # 其他输入 → 取消
            app._user(text)
            app._pre("已取消 confirm。")
            app._pending_confirm = None

    def test_affirmative_fires_confirm(self) -> None:
        """端到端: /confirm 0 → 是 → gate 被调."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-01"}}]
        success = {"ok": True, "data": {"planned_writes": [{"kind": "create", "path": "5_tasks/queue/claimed/AIPOS-999.md"}]}}
        app, session = self._app_with_session(gates, confirm_result=success)

        self._simulate_input(app, "/confirm 0")
        self.assertIsNotNone(app._pending_confirm)
        self.assertEqual(app._pending_confirm.get("awaiting"), "affirmation")

        self._simulate_input(app, "是")
        self.assertTrue(session.confirm_gate.called)
        out = self._texts(app)
        self.assertIn("Confirmed", out)

    def test_non_affirmative_cancels_empty(self) -> None:
        """端到端: /confirm 0 → 空回车 → gate 未被调,取消."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-01"}}]
        app, session = self._app_with_session(gates)

        self._simulate_input(app, "/confirm 0")
        self.assertIsNotNone(app._pending_confirm)

        self._simulate_input(app, "")
        self.assertFalse(session.confirm_gate.called)
        out = self._texts(app)
        self.assertIn("已取消", out)

    def test_non_affirmative_cancels_other_text(self) -> None:
        """端到端: /confirm 0 → 其他话 → gate 未被调,取消."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-01"}}]
        app, session = self._app_with_session(gates)

        self._simulate_input(app, "/confirm 0")
        self._simulate_input(app, "不确认")

        self.assertFalse(session.confirm_gate.called)
        out = self._texts(app)
        self.assertIn("已取消", out)

    def test_three_affirmatives_all_fire(self) -> None:
        """端到端: 是/yes//confirm 三种肯定各测 → 都发射."""
        for affirmative in ["是", "yes", "/confirm"]:
            gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-01"}}]
            success = {"ok": True, "data": {}}
            app, session = self._app_with_session(gates, confirm_result=success)

            self._simulate_input(app, "/confirm 0")
            self._simulate_input(app, affirmative)

            self.assertTrue(session.confirm_gate.called, f"{affirmative} 应触发 confirm")

    def test_gate_denied_loud(self) -> None:
        """端到端: gate denied → 响亮(error_code 可见,无成功文案)."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-01"}}]
        denied = {"ok": False, "error_code": "SCOPE_DENIED", "message": "缺少 owner_confirm scope"}
        app, session = self._app_with_session(gates, confirm_result=denied)

        self._simulate_input(app, "/confirm 0")
        self._simulate_input(app, "是")
        out = self._texts(app)

        self.assertIn("SCOPE_DENIED", out)
        self.assertIn("缺少 owner_confirm scope", out)
        self.assertNotIn("Confirmed", out)

    def test_no_assigned_to_asks_actor_then_fires(self) -> None:
        """端到端: 无 assigned_to → 先问 actor,给之前零调用,给后+肯定词才发射."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {}}]
        success = {"ok": True, "data": {}}
        app, session = self._app_with_session(gates, confirm_result=success)

        # Step 1: /confirm 0 → 问 actor
        self._simulate_input(app, "/confirm 0")
        out = self._texts(app)
        self.assertIn("无 assigned_to", out)
        self.assertIn("归因给哪个 agent", out)
        self.assertEqual(app._pending_confirm.get("awaiting"), "actor")
        self.assertFalse(session.confirm_gate.called)

        # Step 2: 输入 actor
        self._simulate_input(app, "agent-02")
        self.assertEqual(app._pending_confirm.get("actor"), "agent-02")
        self.assertEqual(app._pending_confirm.get("awaiting"), "affirmation")
        self.assertFalse(session.confirm_gate.called)

        # Step 3: 输入肯定词
        self._simulate_input(app, "yes")
        self.assertTrue(session.confirm_gate.called)

    # --- F-244-2: 真接线测试(mock 降到 GateClient 层;session 是 REAL TuiSession) ---
    def test_f244_2_real_wiring_confirm_literal_reaches_gateclient(self) -> None:
        """F-244-2: /confirm→affirm 的真实发射链 TuiSession.confirm_gate → GateClient.confirm
        必须带 owner_confirmation_literal == "OWNER_CONFIRMED",且 preview 原样传递。

        写法要点(非同义反复):stub 用 create_autospec(GateClient) 强制真实签名——漏传字面量
        (本 bug: state.py confirm_gate 调 confirm(preview) 缺参)会立刻 TypeError → 断言失败。
        session 边界的 8 条 UX 测试 mock 掉了 TuiSession,永远走不到这条真接线(教训入卡)。
        """
        from unittest.mock import MagicMock, create_autospec

        from tools.aipos_cli.confirm_client import GateClient, Preview
        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.state import TuiSession

        client = create_autospec(GateClient, instance=True)
        gate_task = {
            "task_id": "AIPOS-999",
            "assigned_to": "exec.cc.local",
            "metadata": {"agent_instance": "exec.cc.local"},
        }
        client.list_confirm_gates.return_value = [
            {"op": "claim", "task_id": "AIPOS-999", "task": gate_task}
        ]
        preview_obj = Preview(
            op="claim",
            dry_run_token="dryrun_wiring_test",
            expires_at=None,
            snapshot_hash="snap",
            replay_args={"actor": "exec.cc.local", "agent_instance": "exec.cc.local", "autonomy_mode": "Supervised"},
        )
        client.preview.return_value = preview_obj
        client.confirm.return_value = {"ok": True, "data": {"planned_writes": []}}

        session = TuiSession(gate_url="http://stub", _client=client)  # REAL session, stub transport
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        app._user = MagicMock()

        app._cmd_confirm(0)                       # real command path → pending (affirmation)
        self.assertEqual(app._pending_confirm.get("awaiting"), "affirmation")
        self._simulate_input(app, "是")           # affirmative → real _execute_pending_confirm

        client.preview.assert_called_once()
        client.confirm.assert_called_once()
        call = client.confirm.call_args
        got_preview = call.args[0] if call.args else call.kwargs.get("preview")
        literal = (
            call.args[1] if len(call.args) > 1 else call.kwargs.get("owner_confirmation_literal")
        )
        self.assertIs(got_preview, preview_obj)            # preview 原样(不重构、不换对象)
        self.assertEqual(literal, "OWNER_CONFIRMED")       # 字面量由 NL 肯定仪式背书传入
        out = self._texts(app)
        self.assertIn("Confirmed", out)
        self.assertNotIn("Confirm failed", out)

    # --- R-h1: pending 严格模态(pending 绝不跨命令存活) ---
    def test_r_h1_command_cancels_pending_and_stale_affirmative_is_inert(self) -> None:
        """R-h1 (a): /confirm 0 → /queue → pending 已清 + gate 零调用;随后 "是" → 仍零调用.

        stale pending 的危险:若 pending 跨命令存活,后续对话里的一句"是"会变成意外真相写入。
        """
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-01"}}]
        app, session = self._app_with_session(gates)
        session.observe.return_value = {"ok": True, "data": {"summary": {"pending": 1}}}

        self._simulate_input(app, "/confirm 0")
        self.assertEqual(app._pending_confirm.get("awaiting"), "affirmation")

        self._simulate_input(app, "/queue")
        self.assertIsNone(app._pending_confirm)                    # pending 已清
        out = self._texts(app)
        self.assertIn("已取消 confirm(你执行了其他命令)", out)      # 明说取消
        session.observe.assert_called_with("queue")                 # 命令照常执行
        session.preview_gate.assert_not_called()                    # gate 零调用
        session.confirm_gate.assert_not_called()

        self._simulate_input(app, "是")                             # stale 肯定词 → 只是普通输入
        session.preview_gate.assert_not_called()
        session.confirm_gate.assert_not_called()

    def test_r_h1_command_cancels_pending_in_actor_stage(self) -> None:
        """R-h1 (b): awaiting=actor 时敲 / 命令 → 同样取消 + 零调用."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {}}]  # 无 assigned_to → 问 actor
        app, session = self._app_with_session(gates)

        self._simulate_input(app, "/confirm 0")
        self.assertEqual(app._pending_confirm.get("awaiting"), "actor")

        self._simulate_input(app, "/help")
        self.assertIsNone(app._pending_confirm)
        out = self._texts(app)
        self.assertIn("已取消 confirm(你执行了其他命令)", out)
        session.preview_gate.assert_not_called()
        session.confirm_gate.assert_not_called()


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class Aipos245GuidanceContinuityTests(unittest.TestCase):
    """AIPOS-245 (Slice F′) — Owner-side guidance & continuity (presentation-only).

    Two hard invariants under test (per DRAFT §0 + R-1..R-5):
    - Every step (success AND failure) carries a "where you are / what to type next" line (P-A).
    - Guidance NEVER pre-fills an answer / auto-fires the gate (default-yes red line, P-2). The
      Scope A/B tests assert guidance TEXT appears; they NEVER let it become a gate call.
    """

    # --- helpers (mirror TuiOwnerConfirmTests conventions) --------------------------
    def _app_with_session(self, gates: list[dict], confirm_result: dict | None = None) -> tuple[Any, Any]:
        from unittest.mock import MagicMock
        from tools.lybra_tui.app import build_app

        session = MagicMock()
        session.confirm_gates.return_value = gates
        if gates:
            gate = gates[0]
            session.preview_gate.return_value = {
                "operation": gate.get("op", "claim"),
                "task": {"task_id": gate.get("task_id", "AIPOS-999")},
                "actor": {"actor": gate.get("task", {}).get("assigned_to", "agent-01")},
            }
        if confirm_result:
            session.confirm_gate.return_value = confirm_result
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        app._user = MagicMock()
        return app, session

    def _texts(self, app: Any) -> str:
        pre = "\n".join(str(c.args[0]) for c in app._pre.call_args_list if c.args)
        sys = "\n".join(str(c.args[0]) for c in app._system.call_args_list if c.args)
        return pre + "\n" + sys

    def _fire_confirm(self, app: Any) -> None:
        # Drive the real pending machine: /confirm 0 → 是 → _execute_pending_confirm.
        app._cmd_confirm(0)
        app._execute_pending_confirm()

    # --- Scope A: happy-path guidance ----------------------------------------------
    def test_a1_claim_success_guides_to_notify_agent_and_return(self) -> None:
        """A1: claim 成功后引导含"通知 agent" + return 环位置,且 actor 是透传的 canonical(R-3)."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-07"}}]
        success = {"ok": True, "data": {"planned_writes": []}}
        app, _ = self._app_with_session(gates, confirm_result=success)

        self._fire_confirm(app)
        out = self._texts(app)
        self.assertIn("通知 agent", out)
        self.assertIn("return", out)
        self.assertIn("agent-07", out)          # R-3: canonical actor 透传,不美化

    def test_a2_return_success_guides_to_audit(self) -> None:
        """A2: return 成功后引导含 /audit <task_id>."""
        gates = [{"op": "return", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-07"}}]
        success = {"ok": True, "data": {"planned_writes": []}}
        app, _ = self._app_with_session(gates, confirm_result=success)

        self._fire_confirm(app)
        out = self._texts(app)
        self.assertIn("/audit AIPOS-999", out)

    def test_a3_gates_list_shows_title_and_attribution_and_confirm_hint(self) -> None:
        """A3: /gates 行含 title + 归因人 + /confirm N 提示(数据现成,纯呈现)."""
        gates = [{"op": "claim", "task_id": "AIPOS-999",
                  "task": {"assigned_to": "agent-07", "title": "重构登录流程"}}]
        app, _ = self._app_with_session(gates)

        app._cmd_gates()
        out = self._texts(app)
        self.assertIn("重构登录流程", out)
        self.assertIn("agent-07", out)
        self.assertIn("/confirm 0", out)

    def test_a3_gates_missing_fields_fall_back_never_prefill(self) -> None:
        """A3 红线: 缺 title/assigned_to → 中性回落,绝不预填答案(P-2)."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {}}]
        app, _ = self._app_with_session(gates)

        app._cmd_gates()
        out = self._texts(app)
        self.assertIn("(无标题)", out)
        self.assertIn("(未归因)", out)

    def test_f248_o3_2_bare_confirm_with_multiple_gates_renders_same_list_as_gates(self) -> None:
        """F-248-o3-2(O3 real-machine finding):裸 /confirm(无下标)遇多个 pending gate 时,
        必须渲染与 /gates 相同的列表(confirm 是保留的备用执行面),而非此前的一句计数提示。"""
        gates = [
            {"op": "claim", "task_id": "AIPOS-A", "task": {"assigned_to": "agent-07", "title": "任务甲"}},
            {"op": "claim", "task_id": "AIPOS-B", "task": {"assigned_to": "agent-08", "title": "任务乙"}},
        ]
        app, _ = self._app_with_session(gates)

        app._cmd_confirm(None)  # bare /confirm, no selector
        out = self._texts(app)
        self.assertIn("任务甲", out)
        self.assertIn("任务乙", out)
        self.assertIn("agent-07", out)
        self.assertIn("agent-08", out)
        self.assertIn("/confirm 0", out)
        self.assertIn("/confirm 1", out)

    def test_f248_o3_2_confirm_accepts_claim_id_selector(self) -> None:
        """F-248-o3-2(量力小加):/confirm <claim_id> 可选中一个 return gate(按 claim_id 匹配)。"""
        gates = [
            {
                "op": "return",
                "task_id": "AIPOS-A",
                "task": {"assigned_to": "agent-07", "metadata": {"claim_id": "claim_AIPOS-A_x"}},
            }
        ]
        app, _ = self._app_with_session(gates)

        app._cmd_confirm("claim_AIPOS-A_x")
        self.assertEqual(app._pending_confirm.get("task_id"), "AIPOS-A")
        self.assertEqual(app._pending_confirm.get("awaiting"), "affirmation")

    def test_a4_draft_conformant_guidance_describes_two_step_revise_not_phantom(self) -> None:
        """A4 (R-2): draft conformant 引导贴合真实两步改稿链路,不承诺"单句即重出"."""
        from tools.lybra_tui.app import _NEXT_AFTER_DRAFT
        # 措辞含两步(先答复 → 回 yes/是 重出),不出现"直接重出/立即重出"的幻影承诺。
        self.assertIn("回 yes", _NEXT_AFTER_DRAFT)
        self.assertIn("重出草稿", _NEXT_AFTER_DRAFT)
        self.assertIn("我先答复", _NEXT_AFTER_DRAFT)

    def test_a6_connected_shows_full_loop_map(self) -> None:
        """A6: 首屏全环导览含 认领 / /gates / /confirm / /audit 关键词."""
        import inspect
        from tools.lybra_tui.app import LybraTui
        src = inspect.getsource(LybraTui.on_mount)
        self.assertIn("本环", src)
        self.assertIn("认领", src)
        self.assertIn("/gates", src)
        self.assertIn("/audit", src)

    # --- Scope B: exception-path guidance (R-5 pass-through + loop overlay) ---------
    def test_b_confirm_failure_passes_through_gate_next_step_and_adds_loop(self) -> None:
        """R-5: confirm 失败透传 gate 自带 suggested_next_action(原文)+ 叠 confirm 环位置."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-07"}}]
        denied = {
            "ok": False,
            "error_code": "SCOPE_DENIED",
            "message": "token 缺 owner_confirm scope",
            "suggested_next_action": "用 owner-role token 重连后重试",
        }
        app, _ = self._app_with_session(gates, confirm_result=denied)

        self._fire_confirm(app)
        out = self._texts(app)
        self.assertIn("SCOPE_DENIED", out)
        self.assertIn("用 owner-role token 重连后重试", out)   # R-5: gate 原文透传
        self.assertIn("你在 confirm 环", out)                  # TUI 只叠环位置
        self.assertNotIn("Confirmed. Writes", out)

    def test_b_confirm_failure_without_next_step_does_not_fabricate(self) -> None:
        """R-5 负向: gate 无 suggested_next_action → TUI 不虚构下一步(只 code:msg + 环位置)."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-07"}}]
        denied = {"ok": False, "error_code": "SNAPSHOT_MISMATCH", "message": "快照已变"}
        app, _ = self._app_with_session(gates, confirm_result=denied)

        self._fire_confirm(app)
        out = self._texts(app)
        self.assertIn("SNAPSHOT_MISMATCH", out)
        self.assertIn("快照已变", out)
        self.assertIn("你在 confirm 环", out)          # 环位置(TUI 职责)仍在
        self.assertNotIn("→ None", out)                # 不把缺失 next 渲染成假箭头

    def test_b7_audit_fail_verdict_guides_back_to_executor(self) -> None:
        """B7: audit verdict=FAIL → 引导退回执行者(读面态,非 gate error_code)."""
        gates: list[dict] = []
        app, session = self._app_with_session(gates)
        session.observe.return_value = {"ok": True, "data": {"verdict": "FAIL"}}

        app._cmd_audit("AIPOS-999")
        out = self._texts(app)
        self.assertIn("AIPOS-999: FAIL", out)
        self.assertIn("退回执行者", out)

    def test_a5_proceed_success_names_oob_publish_next_step(self) -> None:
        """A5: /proceed 落盘后引导显式"下一步 = owner token OOB 确认发布"(TUI 不持发布权)."""
        from unittest.mock import MagicMock

        app, session = self._app_with_session([])
        proposal = MagicMock(conformant=True, content="card", draft_rel_path="5_tasks/drafts/t.md",
                             blocking_reasons=[])
        app._pending_proposal = proposal
        session.land_draft.return_value = "5_tasks/drafts/t.md"
        session.preview_publish.return_value = MagicMock(dry_run_token="dryrun_x")

        app._copilot_proceed(None)
        out = self._texts(app)
        self.assertIn("NOT published", out)
        self.assertIn("OOB", out)
        self.assertIn("不持发布权", out)

    def test_b1_ask_actor_branch_names_assigned_to_fix_and_zero_gate_calls(self) -> None:
        """B1: 缺 assigned_to → 问 actor 之外补"先给任务卡补 assigned_to"的治本引导;给前零调用."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {}}]
        app, session = self._app_with_session(gates)

        app._cmd_confirm(0)
        out = self._texts(app)
        self.assertIn("assigned_to", out)
        self.assertIn("补上 assigned_to", out)
        self.assertEqual(app._pending_confirm.get("awaiting"), "actor")
        session.preview_gate.assert_not_called()
        session.confirm_gate.assert_not_called()

    def test_b8_audit_error_face_adds_local_next_step(self) -> None:
        """B8: /audit 错误面(gate err 已响亮)之外补本地下一步(核对拼写 / /queue)."""
        app, session = self._app_with_session([])
        session.observe.return_value = {"ok": False, "error_code": "TASK_NOT_FOUND", "message": "no such task"}

        app._cmd_audit("AIPOS-404")
        out = self._texts(app)
        self.assertIn("TASK_NOT_FOUND", out)
        self.assertIn("核对 task_id", out)
        self.assertIn("/queue", out)

    def test_b10_proceed_without_card_names_where_cards_are_born(self) -> None:
        """B10 微调: 无 pending 草稿 → 指明 copilot 模式说需求."""
        app, _ = self._app_with_session([])
        app._pending_proposal = None

        app._copilot_proceed(None)
        out = self._texts(app)
        self.assertIn("No pending card", out)
        self.assertIn("copilot 模式", out)

    def test_b11_proceed_without_workspace_names_restart_flag(self) -> None:
        """B11: workspace_root 未知 → 指明 --workspace-root 重启旗标(本地态,无 gate error)."""
        from unittest.mock import MagicMock

        app, _ = self._app_with_session([])
        app._pending_proposal = MagicMock(conformant=True)
        app._workspace_root = None

        app._copilot_proceed(None)
        out = self._texts(app)
        self.assertIn("workspace_root unknown", out)
        self.assertIn("--workspace-root", out)

    def test_b12_no_pending_gates_names_queue_truth_next_step(self) -> None:
        """B12: /confirm 无 pending gate → 指明 gate 从队列真相派生 + /queue / 发新任务;零 gate 写调用."""
        app, session = self._app_with_session([])

        app._cmd_confirm(0)
        out = self._texts(app)
        self.assertIn("No pending confirm gates", out)
        self.assertIn("/queue", out)
        self.assertIn("pending/claimed", out)
        session.preview_gate.assert_not_called()
        session.confirm_gate.assert_not_called()

    # --- ROUND 增量: Owner O3 findings (F-245-o3-5 / o3-4 / o3-2 / B12 nit) ----------
    def test_o3_5_card_frontmatter_fenced_prose_stays_markdown(self) -> None:
        """F-245-o3-5: YAML frontmatter 进 ```yaml 围栏(不进 markdown 解析);散文体留 markdown;
        内容字节不改(只包裹)."""
        from tools.lybra_tui.app import LybraTui

        card = "---\ntask_id: T-1\nassigned_to: exec.cc.local\n---\n\n任务描述:**加粗**散文。"
        out = LybraTui._card_markdown(card)
        self.assertTrue(out.startswith("```yaml\n---\ntask_id: T-1\n"))
        self.assertIn("assigned_to: exec.cc.local\n---\n```", out)   # frontmatter 整段在围栏内
        self.assertIn("任务描述:**加粗**散文。", out)                 # 散文在围栏外(markdown 照走)
        self.assertNotIn("```yaml\n---\ntask_id: T-1\n" + "```", out.split("任务描述")[0].replace("\n", "") or "x")
        # 字节不改:去掉围栏三行后还原出原卡
        self.assertEqual(out.replace("```yaml\n", "", 1).replace("\n```", "", 1), card)

    def test_o3_5_non_frontmatter_content_untouched(self) -> None:
        """F-245-o3-5 负向: 无 frontmatter 的内容原样返回(不误包)."""
        from tools.lybra_tui.app import LybraTui

        prose = "just prose, no frontmatter"
        self.assertEqual(LybraTui._card_markdown(prose), prose)

    def test_o3_5_render_proposal_uses_fenced_card(self) -> None:
        """F-245-o3-5: _render_proposal 输出对 frontmatter 卡含 ```yaml 围栏."""
        from unittest.mock import MagicMock

        app, _ = self._app_with_session([])
        p = MagicMock(task_id="T-1", conformant=True,
                      content="---\ntask_id: T-1\n---\nbody")
        out = app._render_proposal(p)
        self.assertIn("```yaml\n---\ntask_id: T-1\n---\n```", out)
        self.assertIn("TASK CARD DRAFT", out)

    def test_o3_4_command_input_is_echoed(self) -> None:
        """F-245-o3-4: / 命令有用户回显(镜像走真实拦截逻辑;app 侧同构由源断言钉)."""
        import inspect
        from tools.lybra_tui.app import LybraTui

        gates: list[dict] = []
        app, session = self._app_with_session(gates)
        session.observe.return_value = {"ok": True, "data": {"summary": {}}}
        # 经镜像(真实拦截逻辑)提交命令 → _user 被调(镜像不依赖 self,直接借用)
        TuiOwnerConfirmTests._simulate_input(None, app, "/queue")  # type: ignore[arg-type]
        app._user.assert_any_call("/queue")
        # app.py 真分支同构钉:else 分支在 _handle_command 前回显
        src = inspect.getsource(LybraTui.on_prompt_area_submitted)
        idx_echo = src.find("self._user(text)\n                if self._pending_confirm is not None:")
        self.assertGreater(idx_echo, -1, "app / 分支缺命令回显(须在 cancel/handle 之前)")

    def test_o3_4_turn_blocks_have_breathing_margin(self) -> None:
        """F-245-o3-4: .turn 块间有空行(margin-bottom 1)——钉 CSS,防回退贴死."""
        from tools.lybra_tui.app import LybraTui

        self.assertIn(".turn { margin: 0 0 1 0; }", LybraTui.CSS)

    def test_o3_2_audit_emits_verdict_exactly_once_per_invocation(self) -> None:
        """F-245-o3-2 守卫: 单次 /audit 只发一条 verdict 行(单发射性质钉)."""
        app, session = self._app_with_session([])
        session.observe.return_value = {"ok": True, "data": {"verdict": "WARN"}}

        app._cmd_audit("O3-FX-3")
        verdict_lines = [str(c.args[0]) for c in app._pre.call_args_list
                         if c.args and "O3-FX-3: WARN" in str(c.args[0])]
        self.assertEqual(len(verdict_lines), 1)
        self.assertEqual(session.observe.call_count, 1)

    def test_o3_4b_confirm_prompt_is_one_block_widget(self) -> None:
        """F-245-o3-4b: confirm 提示(Preview/归因给/问句/输入指引)是单 widget(块内紧凑)."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-07"}}]
        app, _ = self._app_with_session(gates)

        app._cmd_confirm(0)
        block = [str(c.args[0]) for c in app._pre.call_args_list
                 if c.args and "Preview: claim AIPOS-999" in str(c.args[0])]
        self.assertEqual(len(block), 1)
        # 四行同居一 widget(块内单倍行距;块间呼吸由 .turn margin 提供)
        self.assertIn("归因给: agent-07", block[0])
        self.assertIn("输入 是 / yes / /confirm 确认", block[0])

    def test_o3_4b_planned_writes_rows_are_one_block_widget(self) -> None:
        """F-245-o3-4b: Confirmed. Writes 表(标题+行)是单 widget——表行间不得插空."""
        gates = [{"op": "claim", "task_id": "AIPOS-999", "task": {"assigned_to": "agent-07"}}]
        success = {"ok": True, "data": {"planned_writes": [
            {"kind": "claim_record", "path": "records/claims/x.md"},
            {"kind": "session_record", "path": "records/sessions/y.md"},
        ]}}
        app, _ = self._app_with_session(gates, confirm_result=success)

        self._fire_confirm(app)
        blocks = [str(c.args[0]) for c in app._pre.call_args_list
                  if c.args and "Confirmed. Writes:" in str(c.args[0])]
        self.assertEqual(len(blocks), 1)
        self.assertIn("claim_record records/claims/x.md", blocks[0])
        self.assertIn("session_record records/sessions/y.md", blocks[0])

    def test_o3_6_copilot_error_adds_endpoint_guidance_no_retry(self) -> None:
        """F-245-o3-6: copilot 侧失败(如 read timeout)→ 裸错误 + P-A 引导;零重试逻辑."""
        from tools.lybra_tui.app import CopilotResult

        app, session = self._app_with_session([])
        app._stop_thinking = __import__("unittest").mock.MagicMock()
        app.set_focus = __import__("unittest").mock.MagicMock()
        app._prompt = __import__("unittest").mock.MagicMock()

        app.on_copilot_result(CopilotResult(kind="chat", error="read timeout: xchai.xyz"))
        out = self._texts(app)
        self.assertIn("Copilot error: read timeout: xchai.xyz", out)   # 原始错误仍是唯一真相
        self.assertIn("稍后重试", out)
        self.assertIn("--llm-base-url", out)
        session.confirm_gate.assert_not_called()                        # 引导 ≠ 任何行为

    def test_b12_nit_empty_gates_names_copilot_mode(self) -> None:
        """B12 nit: 空态引导"说需求 → /proceed"带 (copilot 模式下) 限定."""
        app, session = self._app_with_session([])

        app._cmd_confirm(0)
        out = self._texts(app)
        self.assertIn("(copilot 模式下)", out)

    def test_b14_mode_unknown_teaches_valid_modes_not_bare_exception(self) -> None:
        """B14: /mode 未知模式 → 引导有效模式,不再裸抛 Error: {exc}."""
        gates: list[dict] = []
        app, session = self._app_with_session(gates)
        session.mode = "observe"
        session.set_mode.side_effect = ValueError("unknown mode: bogus")

        app._cmd_mode("bogus")
        out = self._texts(app)
        self.assertIn("observe", out)
        self.assertIn("confirm", out)
        self.assertIn("copilot", out)

    # --- default-yes regression nail: guidance TEXT is not a gate call --------------
    def test_guidance_never_auto_fires_gate(self) -> None:
        """新引导文案出现 ≠ gate 被调:仅 /gates(读面)不得触发 confirm/preview."""
        gates = [{"op": "claim", "task_id": "AIPOS-999",
                  "task": {"assigned_to": "agent-07", "title": "T"}}]
        app, session = self._app_with_session(gates)

        app._cmd_gates()
        session.confirm_gate.assert_not_called()
        session.preview_gate.assert_not_called()

    # --- ★ R-4: real-wiring penetration (mock down to GateClient; REAL TuiSession) --
    def test_f245_success_guidance_reaches_real_confirm_wiring(self) -> None:
        """R-4 成功分支: A1 引导经真实 session→GateClient 接线发出(非 session-mock 假绿).

        mock 降到 GateClient 层(create_autospec 锁签名),session 是 REAL TuiSession;
        走 _cmd_confirm → 是 → _execute_pending_confirm → session.confirm_gate → GateClient.confirm。
        断言: ① Confirmed ② A1 下一步引导(通知 agent + return) ③ actor 是透传的 canonical(R-3)。
        """
        from unittest.mock import MagicMock, create_autospec

        from tools.aipos_cli.confirm_client import GateClient, Preview
        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.state import TuiSession

        client = create_autospec(GateClient, instance=True)
        gate_task = {
            "task_id": "AIPOS-999",
            "assigned_to": "exec.cc.local",
            "metadata": {"agent_instance": "exec.cc.local"},
        }
        client.list_confirm_gates.return_value = [
            {"op": "claim", "task_id": "AIPOS-999", "task": gate_task}
        ]
        client.preview.return_value = Preview(
            op="claim", dry_run_token="dryrun_ok", expires_at=None, snapshot_hash="snap",
            replay_args={"actor": "exec.cc.local", "agent_instance": "exec.cc.local", "autonomy_mode": "Supervised"},
        )
        client.confirm.return_value = {"ok": True, "data": {"planned_writes": []}}

        session = TuiSession(gate_url="http://stub", _client=client)  # REAL session, stub transport
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        app._user = MagicMock()

        app._cmd_confirm(0)
        self.assertEqual(app._pending_confirm.get("awaiting"), "affirmation")
        app._execute_pending_confirm()

        client.confirm.assert_called_once()
        out = self._texts(app)
        self.assertIn("Confirmed", out)
        self.assertIn("通知 agent", out)          # A1 引导经真接线发出
        self.assertIn("return", out)
        self.assertIn("exec.cc.local", out)        # R-3: canonical actor 透传

    def test_f245_teaching_and_loop_reach_real_confirm_wiring_fail(self) -> None:
        """R-4 失败分支: gate 真实 _teaching_error 形状经真接线到达 → 透传 next_step + 叠环位置.

        GateClient.confirm 返回真实 teaching-error 形状(ok:False + error_code + message +
        suggested_next_action)。断言: ① 响亮 SCOPE_DENIED ② 透传 gate 自带 next_step 原文
        ③ 叠 confirm 环位置 ④ 无成功文案。证明引导挂在真失败呈现路径(防 F-244-2 掩盖)。
        """
        from unittest.mock import MagicMock, create_autospec

        from tools.aipos_cli.confirm_client import GateClient, Preview
        from tools.lybra_tui.app import build_app
        from tools.lybra_tui.state import TuiSession

        client = create_autospec(GateClient, instance=True)
        gate_task = {
            "task_id": "AIPOS-999",
            "assigned_to": "exec.cc.local",
            "metadata": {"agent_instance": "exec.cc.local"},
        }
        client.list_confirm_gates.return_value = [
            {"op": "claim", "task_id": "AIPOS-999", "task": gate_task}
        ]
        client.preview.return_value = Preview(
            op="claim", dry_run_token="dryrun_deny", expires_at=None, snapshot_hash="snap",
            replay_args={"actor": "exec.cc.local", "agent_instance": "exec.cc.local", "autonomy_mode": "Supervised"},
        )
        # 真实 _teaching_error 顶层形状(tools.py:138-146)。
        client.confirm.return_value = {
            "ok": False,
            "verdict": "BLOCK",
            "error_code": "SCOPE_DENIED",
            "message": "token 缺 owner_confirm scope",
            "suggested_next_action": "用 owner-role token 重连后重试",
        }

        session = TuiSession(gate_url="http://stub", _client=client)
        app = build_app(session, None, workspace_root="/tmp/ws")
        app._pre = MagicMock()
        app._system = MagicMock()
        app._user = MagicMock()

        app._cmd_confirm(0)
        app._execute_pending_confirm()

        client.confirm.assert_called_once()
        out = self._texts(app)
        self.assertIn("SCOPE_DENIED", out)                       # ① 响亮
        self.assertIn("用 owner-role token 重连后重试", out)      # ② gate 原文透传(R-5)
        self.assertIn("你在 confirm 环", out)                    # ③ TUI 只叠环位置
        self.assertNotIn("Confirmed. Writes", out)               # ④ 无成功文案


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class Aipos246ScrollTests(unittest.IsolatedAsyncioTestCase):
    """AIPOS-246 (F-245-o3-1) — conversation scrollback via textual anchor (real pilot, real keys).

    Invariants (DRAFT §2/§3, R-1..R-3 folded):
    - Scrolled-up position is NEVER yanked back by new messages or thinking frames (§3 a/a′ —
      RED before the fix: per-mount scroll_visible pulled to bottom).
    - At bottom the view follows new content (anchor; today's UX preserved) (§3 b).
    - scroll_end returns to bottom AND re-engages following (§3 c).
    - S1b boundary (R ruling): ONLY an Owner submission re-bottoms; gate events / new messages /
      worker replies / thinking frames never do (§3 a″).
    - S2: PgUp/PgDn/End scroll the conversation while the prompt keeps focus (keyboard channel).
    """

    def _make_app(self):
        from unittest.mock import MagicMock
        from tools.lybra_tui.app import build_app

        session = MagicMock()
        session.mode = "observe"
        session.status_line.return_value = "stub status"
        session.scopes = []
        session.confirm_gates.return_value = []
        session.observe.return_value = {"ok": True, "data": {"summary": {}}}
        return build_app(session, None, workspace_root="/tmp/ws"), session

    async def _fill(self, app, pilot, n: int = 40) -> "object":
        from textual.containers import VerticalScroll

        for i in range(n):
            app._pre(f"backlog line {i}")
        await pilot.pause()
        convo = app.query_one("#conversation", VerticalScroll)
        self.assertGreater(convo.max_scroll_y, 0, "content must overflow for a scroll test")
        return convo

    async def test_a_scrolled_up_not_yanked_by_new_message_or_thinking(self) -> None:
        """§3(a): 上滚(释放 anchor)后,新消息 + thinking 帧不得拽回(修前 RED)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            convo = await self._fill(app, pilot)
            convo.scroll_page_up(animate=False)   # user scroll → releases anchor
            await pilot.pause()
            y = convo.scroll_y
            self.assertLess(y, convo.max_scroll_y)

            app._pre("new message while scrolled up")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, y, "new message must not yank the view to bottom")

            app._start_thinking()
            await pilot.pause()
            app._tick_thinking()
            app._tick_thinking()
            await pilot.pause()
            app._stop_thinking()
            self.assertEqual(convo.scroll_y, y, "thinking frames must not yank the view to bottom")

    async def test_a2_r3_thinking_ticks_alone_do_not_pull(self) -> None:
        """§3(a′) R-3: anchor 释放期间,仅 thinking tick 连打多帧,scroll_y 全程不动."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            convo = await self._fill(app, pilot)
            app._start_thinking()
            await pilot.pause()
            convo.scroll_page_up(animate=False)
            await pilot.pause()
            y = convo.scroll_y
            for _ in range(6):
                app._tick_thinking()
                await pilot.pause()
            app._stop_thinking()
            self.assertEqual(convo.scroll_y, y)

    async def test_a3_s1b_boundary_only_owner_submit_rebottoms(self) -> None:
        """§3(a″) S1b 边界(R 裁定): worker 回包/新消息不回底;Owner 提交回底."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            convo = await self._fill(app, pilot)
            convo.scroll_page_up(animate=False)
            await pilot.pause()
            y = convo.scroll_y

            # worker 回包(copilot error path mounts messages) → 不回底
            from tools.lybra_tui.app import CopilotResult
            app.on_copilot_result(CopilotResult(kind="chat", error="stub timeout"))
            await pilot.pause()
            self.assertEqual(convo.scroll_y, y, "a worker reply must not re-bottom")

            # Owner 提交输入 → 回底(S1b)
            await pilot.press(*"hello")
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "an Owner submission re-bottoms")

    async def test_b_at_bottom_follows_new_content(self) -> None:
        """§3(b): 在底部时新消息自动跟随(现状体验保留)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            convo = await self._fill(app, pilot)
            convo.scroll_end(animate=False)
            await pilot.pause()
            for i in range(5):
                app._pre(f"tail {i}")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, convo.max_scroll_y)

    async def test_c_scroll_end_reengages_following(self) -> None:
        """§3(c): 上滚释放后 scroll_end 回底 → 跟随恢复."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            convo = await self._fill(app, pilot)
            convo.scroll_page_up(animate=False)
            await pilot.pause()
            convo.scroll_end(animate=False)
            await pilot.pause()
            for i in range(5):
                app._pre(f"tail {i}")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, convo.max_scroll_y)

    async def test_o3_2_short_content_not_anchored_until_overflow(self) -> None:
        """F-246-o3-2: 开屏内容不足一屏 → 不 anchor(欢迎语不被钉到视口底);首次溢出才挂 + 跟随.

        机制(装机 8.2.8 _compositor.py:609-618 核实):anchored 容器被 compositor 无条件
        set_reactive(scroll_y = 内容底 - 视口高)——短内容时为负,内容被推到视口底部。
        """
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll
            convo = app.query_one("#conversation", VerticalScroll)
            self.assertEqual(convo.max_scroll_y, 0, "premise: startup content fits one screen")
            self.assertFalse(convo.is_anchored, "short content must NOT be anchored (RED pre-fix)")
            self.assertGreaterEqual(convo.scroll_y, 0, "no negative scroll (pinned-bottom symptom)")

            # 首次溢出 → 懒挂载生效 + 跟随(挂载滞后 ≤1 条消息:同 tick 连发 40 条后,
            # 下一条消息的 mount 前同步检查在已结算布局上挂上 —— 与真实节奏一致)
            for i in range(40):
                app._pre(f"overflow line {i}")
            await pilot.pause()
            self.assertGreater(convo.max_scroll_y, 0)
            app._pre("next message after overflow")
            await pilot.pause()
            self.assertTrue(convo.is_anchored, "anchor engages on first overflow (≤1 message lag)")
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "and follows at the bottom")

    async def test_s2_pgup_pgdn_end_scroll_conversation_from_prompt(self) -> None:
        """S2: prompt 聚焦下 PgUp 上滚 / PgDn 下滚 / End 回底(键盘通道,鼠标失效兜底)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            convo = await self._fill(app, pilot)
            convo.scroll_end(animate=False)
            await pilot.pause()
            bottom = convo.scroll_y

            await pilot.press("pageup")
            await pilot.pause()
            self.assertLess(convo.scroll_y, bottom, "PgUp must scroll the conversation up")
            up_y = convo.scroll_y

            await pilot.press("pagedown")
            await pilot.pause()
            self.assertGreater(convo.scroll_y, up_y, "PgDn must scroll the conversation down")

            await pilot.press("pageup")
            await pilot.press("end")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "End must return to bottom")
            # End 复位跟随:后续新消息继续跟
            app._pre("after end")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, convo.max_scroll_y)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class Aipos247MouseBannerFlowTests(unittest.IsolatedAsyncioTestCase):
    """AIPOS-247 — banner joins the conversation flow + `--mouse` opt-in hint (R folded).

    Invariants (§2/§3, R-A/R-C folded):
    - S2① (R-A — the 246 opening pin EVOLVES, never deleted): the banner is the FIRST
      `#conversation` child, the welcome line the SECOND. RED pre-fix: the banner lived in
      the fixed `#brandbar` layer, outside the conversation.
    - S2②: once content overflows, the banner scrolls OUT of the viewport with the flow
      (no fixed layer); the 246 anchor invariants stay pinned by Aipos246ScrollTests untouched.
    - S2③: `/clear` removes the banner with the rest of the flow — no rebuild, no crash
      (disclosed; claude-code same shape).
    - S1 (R-C): the cost-disclosure hint prints ONLY when mouse=True; a default session's
      startup output gains ZERO new lines.
    """

    def _make_app(self, *, mouse: bool = False):
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app

        session = MagicMock()
        session.mode = "observe"
        session.status_line.return_value = "stub status"
        session.scopes = []
        session.confirm_gates.return_value = []
        session.observe.return_value = {"ok": True, "data": {"summary": {}}}
        # Passed only when on, so the default path exercises build_app exactly as before S1.
        kwargs = {"mouse": True} if mouse else {}
        return build_app(session, None, workspace_root="/tmp/ws", **kwargs), session

    async def test_s2_banner_first_child_welcome_second(self) -> None:
        """S2①(R-A): banner 入流 = #conversation 首子件,welcome 紧随(修前 RED:banner 在固定层)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll

            convo = app.query_one("#conversation", VerticalScroll)
            children = list(convo.children)
            self.assertGreaterEqual(len(children), 2, "startup conversation must hold banner + welcome")
            self.assertEqual(children[0].id, "banner", "banner must be the FIRST conversation child (in-flow)")
            self.assertIn("Connected.", str(children[1].render()), "welcome must sit directly under the banner")

    async def test_s2_banner_scrolls_out_on_overflow(self) -> None:
        """S2②: 超屏后 banner 随流滚出视口(无固定层);anchor 跟随不受影响."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll

            convo = app.query_one("#conversation", VerticalScroll)
            banner_widget = app.query_one("#banner")
            for i in range(40):
                app._pre(f"overflow line {i}")
            await pilot.pause()
            app._pre("post-overflow")  # anchor engages (≤1-message lag, 246 semantics) and follows
            await pilot.pause()
            self.assertGreater(convo.max_scroll_y, 0)
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "at bottom, following (246 preserved)")
            self.assertLessEqual(
                banner_widget.virtual_region.bottom,
                convo.scroll_y,
                "banner must have scrolled OUT of the viewport with the flow (no fixed layer)",
            )

    async def test_s2_clear_removes_banner_no_rebuild_no_crash(self) -> None:
        """S2③: /clear 后 banner 随流清走,不重建、不炸(披露,claude-code 同型)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(app.query("#banner"), "premise: banner present at startup")
            app._handle_command("/clear")
            await pilot.pause()
            self.assertFalse(app.query("#banner"), "/clear removes the banner with the flow (no rebuild)")
            app._system("still alive")  # post-clear mounts keep working
            await pilot.pause()
            texts = [str(w.render()) for w in app.query("#conversation Static")]
            self.assertTrue(any("still alive" in t for t in texts))

    async def test_s1_hint_only_when_mouse_on(self) -> None:
        """S1②(R-C): --mouse on → 开屏一行代价告知(Option+拖拽逃生门)."""
        app, _ = self._make_app(mouse=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            texts = "\n".join(str(w.render()) for w in app.query("#conversation Static"))
            self.assertIn("Option+拖拽", texts, "--mouse must disclose the native-selection cost at startup")

    async def test_s1_default_session_prints_no_mouse_hint(self) -> None:
        """S1③(R-C 负向): 默认会话零新增输出 — 无任何鼠标提示."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            texts = "\n".join(str(w.render()) for w in app.query("#conversation Static"))
            self.assertNotIn("Option+拖拽", texts, "default session must gain ZERO new startup output (R-C)")
            self.assertNotIn("鼠标模式", texts)


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class F247o31GrowthAnchorTests(unittest.IsolatedAsyncioTestCase):
    """F-247-o3-1 (latent since 246 F-246-o3-2 lazy mount) — anchor engagement missed when the
    LAST message GROWS after mount.

    O3 symptom: a single long copilot reply overflows the screen but the view stays pinned at
    the top (scroll_y 0, banner visible) — PgDn needed by hand. Mechanism (code-verified):
    engagement checks ran only on APPEND (sync pre-mount + one call_after_refresh); a Markdown
    reply lays out asynchronously and grows AFTER both checks, and with no later message the
    anchor never engaged. 246/247 O3 missed it because those sessions filled the screen with
    many short messages (every append re-checked).

    Fix invariant: overflow detection must also fire on CONTENT-SIZE change (virtual_size
    watch — textual-native reactive, zero polling). Release/re-engage semantics of an ALREADY
    engaged anchor must not change (pinned below); the 246 multi-message path stays pinned by
    Aipos246ScrollTests untouched.
    """

    def _make_app(self, *, mouse: bool = False):
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import build_app

        session = MagicMock()
        session.mode = "observe"
        session.status_line.return_value = "stub status"
        session.scopes = []
        session.confirm_gates.return_value = []
        session.observe.return_value = {"ok": True, "data": {"summary": {}}}
        return build_app(session, None, workspace_root="/tmp/ws", mouse=mouse), session

    async def test_red_single_message_growing_after_mount_engages_anchor(self) -> None:
        """确定性复现:单条消息挂载后长高(无后续追加)→ anchor 须挂上 + 视图跟到底(修前 RED)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll

            convo = app.query_one("#conversation", VerticalScroll)
            app._pre("copilot reply placeholder")  # append-side checks run HERE (no overflow yet)
            await pilot.pause()
            self.assertEqual(convo.max_scroll_y, 0, "premise: no overflow at mount/refresh check time")
            self.assertFalse(convo.is_anchored, "premise: anchor not engaged yet")
            # The widget grows AFTER its mount (async layout of a long reply) — NO further append.
            convo.children[-1].update("\n".join(f"late-layout line {i}" for i in range(80)))
            # Bounded settle: the growth check is refresh-deferred (watcher → call_after_refresh),
            # so engagement needs 2 refresh cycles; under full-suite load one pause can be short.
            # Pre-fix this loop changes nothing (no growth trigger exists at all — stays RED).
            for _ in range(10):
                await pilot.pause()
                if convo.is_anchored:
                    break
            self.assertGreater(convo.max_scroll_y, 0, "content now overflows")
            self.assertTrue(convo.is_anchored, "content growth must engage the anchor (RED pre-fix)")
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "and the view follows to the bottom")

    async def test_real_markdown_single_long_reply_follows_to_bottom(self) -> None:
        """O3 同型场景:单条真 Markdown 长回复(异步排版)→ 视图须跟到底,无需手动 PgDn."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll

            convo = app.query_one("#conversation", VerticalScroll)
            long_md = "# Reply\n\n" + "\n\n".join(f"Paragraph {i}: lorem ipsum dolor." for i in range(40))
            app._markdown(long_md, "turn-copilot")  # ONE message; Markdown lays out asynchronously
            for _ in range(10):  # bounded settle for async block mounts + the refresh-deferred check
                await pilot.pause()
                if convo.is_anchored:
                    break
            self.assertGreater(convo.max_scroll_y, 0, "premise: the single reply overflows a screen")
            self.assertTrue(convo.is_anchored, "the view must follow the single long reply")
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "bottom = latest content visible")

    async def test_r3_growth_pass_engages_with_zero_scheduling_dependence(self) -> None:
        """R3 忠实钉(O3 REJECTED 后):逐字节重放 textual 真实生长 pass 入口
        (screen.py:1373 → `_size_updated(size, 长高的 virtual, container)`,内部赋值序 =
        `_size` → `virtual_size`(watcher 同步触发)→ `_container_size` 即真实中途序),
        然后不做任何泵处理立即断言已挂——挂载不得依赖任何后续调度跳
        (修前 RED:R2 的 defer 把一次性事件押在 App 泵 → screen._callbacks 多跳链上)."""
        app, _ = self._make_app()
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll
            from textual.geometry import Size

            convo = app.query_one("#conversation", VerticalScroll)
            self.assertFalse(app._anchor_engaged, "premise: tall screen, startup content short")
            grown = Size(convo.virtual_size.width, convo.size.height + 30)
            convo._size_updated(convo.size, grown, convo.container_size)
            # NO pilot.pause() before the assert: engagement must be synchronous in the watcher.
            self.assertTrue(
                app._anchor_engaged,
                "growth must engage the anchor with ZERO scheduling dependence (RED pre-R3-fix)",
            )
            self.assertTrue(convo.is_anchored)

    async def test_r3_real_draft_chain_on_tall_screen_follows_to_bottom(self) -> None:
        """O3 场景行为钉(100×50 高屏——开屏不溢出,溢出只能来自卡片排版):真实 /draft
        渲染链(thinking 收尾 + markdown 卡[frontmatter+prose] + blocking reasons + 尾行)
        → 视图须自动跟到底,无需手动 PgDn."""
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import CopilotResult

        app, _ = self._make_app()
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll

            convo = app.query_one("#conversation", VerticalScroll)
            self.assertEqual(convo.max_scroll_y, 0, "premise: tall screen — startup does not overflow")
            prop = MagicMock()
            prop.task_id = "AIPOS-999"
            prop.content = (
                "---\n" + "\n".join(f"field_{i}: value_{i}" for i in range(12)) + "\n---\n\n# Intent\n\n"
                + "\n\n".join(f"Paragraph {i} of the drafted card body." for i in range(10))
            )
            prop.conformant = False
            prop.needs_bundle = False
            prop.blocking_reasons = [f"blocking reason {i}" for i in range(4)]
            app._start_thinking()
            await pilot.pause()
            app.on_copilot_result(CopilotResult(kind="draft", proposal=prop))
            for _ in range(10):  # bounded settle (async markdown block mounts)
                await pilot.pause()
                if convo.is_anchored and convo.scroll_y == convo.max_scroll_y:
                    break
            self.assertGreater(convo.max_scroll_y, 0, "premise: the card overflows the tall screen")
            self.assertTrue(convo.is_anchored, "the /draft card must engage the anchor")
            self.assertEqual(convo.scroll_y, convo.max_scroll_y, "the view must follow the /draft card")

    async def test_o3_2_default_session_hides_scrollbar_mouse_session_keeps_it(self) -> None:
        """F-247-o3-2(Owner 裁定):默认(无鼠标)会话隐藏会话区滚动条(不可拖拽的滚动条是
        误导性摆设);--mouse 会话保留显示。CSS 层实现,滚动行为(PgUp/PgDn/End/anchor)不变."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            convo = app.query_one("#conversation")
            self.assertEqual(
                convo.styles.scrollbar_size_vertical, 0, "default session: vertical scrollbar hidden"
            )
        app2, _ = self._make_app(mouse=True)
        async with app2.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            convo2 = app2.query_one("#conversation")
            self.assertGreater(
                convo2.styles.scrollbar_size_vertical, 0, "--mouse session: scrollbar stays (draggable)"
            )

    async def test_growth_never_re_engages_a_released_anchor(self) -> None:
        """红线钉:anchor 已挂后被用户上滚释放 → 内容长高不得复挂/拽回(释放语义不变)."""
        app, _ = self._make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            from textual.containers import VerticalScroll

            convo = app.query_one("#conversation", VerticalScroll)
            for i in range(40):
                app._pre(f"backlog line {i}")
            await pilot.pause()
            app._pre("engage")  # ≤1-message lag (246 semantics)
            await pilot.pause()
            self.assertTrue(convo.is_anchored, "premise: anchor engaged")
            convo.scroll_page_up(animate=False)  # user scroll → releases the anchor
            await pilot.pause()
            y = convo.scroll_y
            self.assertLess(y, convo.max_scroll_y)
            convo.children[-1].update("\n".join(f"grown line {i}" for i in range(30)))
            await pilot.pause()
            await pilot.pause()  # cover the refresh-deferred check window before asserting NO change
            self.assertEqual(convo.scroll_y, y, "growth must not yank a released view")
            # textual keeps `_anchored=True` on release (`_anchor_released` is the internal flag;
            # installed 8.2.7 source verified) — the honest PUBLIC pin is behavioral: a released
            # view must keep NOT following new content after the growth event.
            app._pre("after growth")
            await pilot.pause()
            self.assertEqual(convo.scroll_y, y, "released view must stay put after growth (no re-engage)")


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed (gate/core lane); app layer is tui-lane only")
class Aipos247MouseWiringTests(unittest.TestCase):
    """AIPOS-247 S1 接线钉(R 钩1):唯一分叉点 = `mouse` 透传链(grep 可对账);默认 run 收 False."""

    def _run_tui(self, **extra):
        from unittest.mock import MagicMock, patch

        from tools.lybra_tui.__main__ import run_tui

        with patch("tools.lybra_tui.state.TuiSession.connect", return_value=MagicMock(mode="observe")), patch(
            "tools.lybra_tui.app.build_app"
        ) as build_app, patch("tools.lybra_tui.app.apply_cjk_kitty_fix"):
            rc = run_tui(gate_url="http://127.0.0.1:1", token_env="LYBRA_TEST_TOKEN", **extra)
        return rc, build_app

    def test_default_run_receives_mouse_false(self) -> None:
        rc, build_app = self._run_tui()
        self.assertEqual(rc, 0)
        build_app.return_value.run.assert_called_once_with(mouse=False)
        self.assertFalse(build_app.call_args.kwargs["mouse"])

    def test_mouse_flag_run_receives_mouse_true(self) -> None:
        rc, build_app = self._run_tui(mouse=True)
        self.assertEqual(rc, 0)
        build_app.return_value.run.assert_called_once_with(mouse=True)
        self.assertTrue(build_app.call_args.kwargs["mouse"])


if __name__ == "__main__":
    unittest.main()
