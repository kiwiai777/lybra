"""AIPOS-210 — TUI presentation / branding tests (core lane, no Textual).

Pure presentation: banner (wide art + narrow plain-text fallback), color degradation
(NO_COLOR / non-TTY), the single brand-color token (no scattered color literals — grep
assertion), and dependency isolation (only app.py imports textual).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from tools.lybra_tui.presentation import BANNER_MIN_WIDTH, LYBRA_GREEN, banner, color_enabled

_TUI_DIR = Path(__file__).resolve().parents[1]


class PresentationTests(unittest.TestCase):
    # --- T1: banner wide art + narrow plain-text fallback ---
    def test_banner_wide_has_art(self) -> None:
        out = banner(120)
        self.assertIn("\n", out)  # multi-line word-mark
        self.assertIn("█", out)
        self.assertGreater(len(out.splitlines()), 1)

    def test_banner_wide_includes_logo_and_box(self) -> None:
        # §1 minimal: a rounded box with the pixel llama mark + an identity panel.
        out = banner(120)
        self.assertIn("█", out)      # pixel llama mark
        self.assertIn("╭", out)      # rounded framed box
        self.assertIn("Lybra", out)  # identity panel

    def test_banner_narrow_falls_back_to_plain(self) -> None:
        self.assertEqual(banner(10), "LYBRA")
        self.assertEqual(banner(BANNER_MIN_WIDTH - 1), "LYBRA")
        self.assertEqual(banner(None), "LYBRA")

    def test_banner_never_exceeds_when_wide(self) -> None:
        # at exactly the threshold, the art renders without raising
        out = banner(BANNER_MIN_WIDTH)
        self.assertIn("\n", out)

    # --- T2: color degradation ---
    def test_color_disabled_by_no_color(self) -> None:
        self.assertFalse(color_enabled({"NO_COLOR": "1"}))
        self.assertFalse(color_enabled({"NO_COLOR": ""}))  # presence disables (NO_COLOR convention)

    def test_color_disabled_by_non_tty(self) -> None:
        self.assertFalse(color_enabled({}, isatty=False))

    def test_color_enabled_default(self) -> None:
        self.assertTrue(color_enabled({}, isatty=True))

    # --- T3: single brand-color token — no scattered color literals ---
    def test_single_color_token(self) -> None:
        hex_re = re.compile(r"#[0-9A-Fa-f]{6}")
        # presentation.py: the hex appears on exactly one line (the token definition)
        pres = (_TUI_DIR / "presentation.py").read_text(encoding="utf-8")
        hex_lines = [ln for ln in pres.splitlines() if hex_re.search(ln) and not ln.strip().startswith("#")]
        self.assertEqual(len(hex_lines), 1, f"hex color literal must be defined once; found: {hex_lines}")
        self.assertIn("LYBRA_GREEN", hex_lines[0])
        # app.py: references LYBRA_GREEN (the token), never a raw hex color literal
        app = (_TUI_DIR / "app.py").read_text(encoding="utf-8")
        self.assertNotRegex(app, hex_re)
        self.assertIn("LYBRA_GREEN", app)

    def test_token_value(self) -> None:
        self.assertEqual(LYBRA_GREEN, "#1A7A52")

    # --- T4: dependency isolation — presentation.py imports no textual ---
    def test_presentation_no_textual(self) -> None:
        self.assertNotIn("textual", sys.modules)
        import tools.lybra_tui.presentation  # noqa: F401
        self.assertNotIn("textual", sys.modules)
        src = (_TUI_DIR / "presentation.py").read_text(encoding="utf-8")
        self.assertNotIn("import textual", src)
        self.assertNotIn("from textual", src)


if __name__ == "__main__":
    unittest.main()
