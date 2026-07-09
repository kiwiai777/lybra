"""AIPOS-209 — serve↔TUI + install wiring tests (core lane, no Textual).

Covers the minimal installable path: the `lybra tui` connection.json default fallback (so
`lybra serve` then `lybra tui` needs no explicit token source), the no-token-source guidance,
dependency isolation of the entry module, and the 5a release-discipline doc.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.lybra_tui.__main__ import CONNECTION_REL, default_connection_json, run_tui


class ServeTuiInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        # AIPOS-226: default_connection_json now checks the runtime root ~/.lybra/local/ first.
        # Patch HOME to an empty temp dir so these tests are isolated from the real ~/.lybra.
        self.fake_home = self.root / "userhome"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self._home_patcher = patch.dict(os.environ, {"HOME": str(self.fake_home)})
        self._home_patcher.start()

    def tearDown(self) -> None:
        self._home_patcher.stop()
        self.temp_dir.cleanup()

    # --- T3: connection.json default fallback under the workspace (legacy) ---
    def test_default_connection_json_found(self) -> None:
        conn = self.root / CONNECTION_REL
        conn.parent.mkdir(parents=True, exist_ok=True)
        conn.write_text("{}", encoding="utf-8")
        self.assertEqual(default_connection_json(str(self.root)), str(conn))

    def test_default_connection_json_prefers_runtime_root(self) -> None:
        # runtime root wins over the in-workspace legacy path
        runtime = self.fake_home / ".lybra" / "local" / "connection.json"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text("{}", encoding="utf-8")
        ws_conn = self.root / CONNECTION_REL
        ws_conn.parent.mkdir(parents=True, exist_ok=True)
        ws_conn.write_text("{}", encoding="utf-8")
        self.assertEqual(default_connection_json(str(self.root)), str(runtime))

    def test_default_connection_json_absent(self) -> None:
        self.assertIsNone(default_connection_json(str(self.root)))

    # --- T3b: no token source + no default → exit 2 with guidance (no gate/textual needed) ---
    def test_run_tui_no_token_source_returns_2(self) -> None:
        rc = run_tui(gate_url="http://127.0.0.1:7118", workspace_root=str(self.root))
        self.assertEqual(rc, 2)

    # --- T5: the entry module imports no Textual at module level ---
    def test_entry_module_imports_no_textual(self) -> None:
        self.assertNotIn("textual", sys.modules, "tools.lybra_tui.__main__ must not import textual at top level")

    # --- AIPOS-247 S1: argparse → run_tui `mouse` passthrough (the single fork point, R 钩1) ---
    def test_247_main_default_passes_mouse_false(self) -> None:
        from tools.lybra_tui import __main__ as entry

        with patch.object(entry, "run_tui", return_value=0) as run_tui_mock:
            rc = entry.main(["--gate-url", "http://127.0.0.1:1"])
        self.assertEqual(rc, 0)
        self.assertFalse(run_tui_mock.call_args.kwargs["mouse"], "no --mouse → run_tui(mouse=False)")

    def test_247_main_mouse_flag_passes_mouse_true(self) -> None:
        from tools.lybra_tui import __main__ as entry

        with patch.object(entry, "run_tui", return_value=0) as run_tui_mock:
            rc = entry.main(["--gate-url", "http://127.0.0.1:1", "--mouse"])
        self.assertEqual(rc, 0)
        self.assertTrue(run_tui_mock.call_args.kwargs["mouse"], "--mouse → run_tui(mouse=True)")

    # --- T6: 5a release-discipline doc exists with the codified points ---
    def test_release_discipline_doc(self) -> None:
        doc = Path(__file__).resolve().parents[3] / "docs" / "release_discipline.md"
        self.assertTrue(doc.is_file(), f"missing {doc}")
        text = doc.read_text(encoding="utf-8")
        for needle in ("git add -A", "pathspec", "Two-repo", "Manual finalize", "fingerprint-only"):
            self.assertIn(needle, text, f"release_discipline.md missing: {needle}")


if __name__ == "__main__":
    unittest.main()
