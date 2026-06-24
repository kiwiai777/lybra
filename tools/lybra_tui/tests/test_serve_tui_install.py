"""AIPOS-209 — serve↔TUI + install wiring tests (core lane, no Textual).

Covers the minimal installable path: the `lybra tui` connection.json default fallback (so
`lybra serve` then `lybra tui` needs no explicit token source), the no-token-source guidance,
dependency isolation of the entry module, and the 5a release-discipline doc.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tools.lybra_tui.__main__ import CONNECTION_REL, default_connection_json, run_tui


class ServeTuiInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- T3: connection.json default fallback under the workspace ---
    def test_default_connection_json_found(self) -> None:
        conn = self.root / CONNECTION_REL
        conn.parent.mkdir(parents=True, exist_ok=True)
        conn.write_text("{}", encoding="utf-8")
        self.assertEqual(default_connection_json(str(self.root)), str(conn))

    def test_default_connection_json_absent(self) -> None:
        self.assertIsNone(default_connection_json(str(self.root)))

    # --- T3b: no token source + no default → exit 2 with guidance (no gate/textual needed) ---
    def test_run_tui_no_token_source_returns_2(self) -> None:
        rc = run_tui(gate_url="http://127.0.0.1:7118", workspace_root=str(self.root))
        self.assertEqual(rc, 2)

    # --- T5: the entry module imports no Textual at module level ---
    def test_entry_module_imports_no_textual(self) -> None:
        self.assertNotIn("textual", sys.modules, "tools.lybra_tui.__main__ must not import textual at top level")

    # --- T6: 5a release-discipline doc exists with the codified points ---
    def test_release_discipline_doc(self) -> None:
        doc = Path(__file__).resolve().parents[3] / "docs" / "release_discipline.md"
        self.assertTrue(doc.is_file(), f"missing {doc}")
        text = doc.read_text(encoding="utf-8")
        for needle in ("git add -A", "pathspec", "Two-repo", "Manual finalize", "fingerprint-only"):
            self.assertIn(needle, text, f"release_discipline.md missing: {needle}")


if __name__ == "__main__":
    unittest.main()
