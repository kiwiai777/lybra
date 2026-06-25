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
        # bindings include Shift+Tab (mode), /-menu, and Esc (cancel/reject)
        keys = {b.key for b in LybraTui.BINDINGS}
        self.assertIn("shift+tab", keys)
        self.assertIn("escape", keys)

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


if __name__ == "__main__":
    unittest.main()
