"""AIPOS-210 — TUI presentation / branding (pure, NO Textual).

Pure presentation helpers — a single brand-color token, the startup banner with a
narrow-terminal fallback, and color-enablement detection. No Textual here, so this runs
in the core CI lane and the only Textual importer stays app.py. This module holds NO
logic and touches NO red line; app.py (the Textual layer) consumes it.
"""

from __future__ import annotations

import os
from typing import Mapping

# --- single brand-color token (the ONLY color literal in the TUI) ------------------
# codex-blue → Lybra-green is a one-line change here. app.py references LYBRA_GREEN (the
# name), never the hex — so a grep for a hex color literal finds only this line.
LYBRA_GREEN = "#1A7A52"  # homepage --green-b: brand-consistent + readable on dark terminals

# Crisp pixel rendition of the brand mark (the llama from docs/assets/lybra-logo-white.png),
# downscaled from that PNG at build time to solid block pixels (Claude-style pixel feel), kept
# small with box breathing room so it does not fill the frame edge to edge. Pure block-art — NOT
# an embedded PNG / terminal-image protocol (§5c); no runtime image dependency.
_MARK_LINES = [
    "       ███",
    "        ██",
    "      ████",
    "     ██  ██████",
    "         █    █",
    "         █    █",
]
# Identity panel to the RIGHT of the mark inside the box (Claude-style: a few identity lines).
_INFO_LINES = [
    "Lybra  v0.2.0",
    "accountability harness for AI agents",
    "",
    "read-only planning · gate-governed publish",
]
_NAME = "LYBRA"  # plain-text fallback for very narrow terminals
_GAP = "    "  # space between the mark and the identity panel
_VPAD = 1  # blank rows inside the box, top and bottom, so the mark/text get breathing room


def _hjoin(left: list[str], right: list[str], gap: str) -> list[str]:
    """Join two text blocks side by side, vertically centering the shorter one."""
    lw = max((len(s) for s in left), default=0)
    h = max(len(left), len(right))

    def _pad(block: list[str], total: int) -> list[str]:
        top = (total - len(block)) // 2
        return [""] * top + block + [""] * (total - len(block) - top)

    left_p, right_p = _pad(left, h), _pad(right, h)
    return [f"{left_p[i].ljust(lw)}{gap}{right_p[i]}".rstrip() for i in range(h)]


def _box(lines: list[str]) -> list[str]:
    """Frame lines in a rounded box with 1-space horizontal padding + _VPAD blank rows top/bottom."""
    body = [""] * _VPAD + list(lines) + [""] * _VPAD
    width = max((len(s) for s in body), default=0)
    out = ["╭" + "─" * (width + 2) + "╮"]
    out += [f"│ {s.ljust(width)} │" for s in body]
    out.append("╰" + "─" * (width + 2) + "╯")
    return out


# Composed banner (minimal): a single rounded box with the pixel llama mark (left) and the identity
# panel (right). No top word-mark — kept simple per Owner art direction.
_BANNER = "\n".join(_box(_hjoin(_MARK_LINES, _INFO_LINES, _GAP)))
# Below this width the banner falls back to plain text (no wrap/garble); tracks the boxed width.
BANNER_MIN_WIDTH = max(len(line) for line in _BANNER.splitlines())


def banner(width: int) -> str:
    """Startup banner: a rounded box with the pixel llama mark + a small identity panel (minimal).

    Pure: returns a string, never raises. Narrow terminals (below the boxed width) fall back to
    plain 'LYBRA' (no wrap/garble) so nothing breaks.
    """
    if width is None or width < BANNER_MIN_WIDTH:
        return "LYBRA"
    return _BANNER


def color_enabled(env: Mapping[str, str] | None = None, *, isatty: bool = True) -> bool:
    """Whether to apply brand color. False under NO_COLOR (any value) or a non-TTY/monochrome.

    Honors the NO_COLOR convention (presence disables color) so output degrades to plain text.
    """
    environ = os.environ if env is None else env
    if "NO_COLOR" in environ:
        return False
    return bool(isatty)


__all__ = ["LYBRA_GREEN", "BANNER_MIN_WIDTH", "banner", "color_enabled"]
