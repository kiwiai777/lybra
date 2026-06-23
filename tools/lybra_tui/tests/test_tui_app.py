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
        from unittest.mock import MagicMock

        from tools.lybra_tui.app import LybraTui, build_app

        session = MagicMock()
        session.status_line.return_value = "gate ... · token sha256:x · read-only-view"
        app = build_app(session)
        self.assertIsInstance(app, LybraTui)
        # bindings include Shift+Tab (mode), /-menu, and Esc (cancel/reject)
        keys = {b.key for b in LybraTui.BINDINGS}
        self.assertIn("shift+tab", keys)
        self.assertIn("escape", keys)


if __name__ == "__main__":
    unittest.main()
