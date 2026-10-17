"""AIPOS-F183 - the whole board on the official KiwiAI / Lybra design system (kiwiai-design), one stylesheet.

Checks: (1) every page links only the one stylesheet /readonly-pages.css (skeleton pages rendered on the F181 rig, plus the
static login / former home / legacy project page / legacy task center), no other stylesheet file exists, no inline style
block; (2) the Lybra logo lockup is on every page and the asset exists; (3) the stylesheet follows the design system's
forbidden list - no gradients, no shadows, no motion, no rounded corners, no raw colour outside the one :root token block,
every token colour is green-family or neutral (no blue / purple), only the design-system font stacks, every var() used is
declared; (4) inline styles in static pages / scripts carry no raw colour, radius or shadow. Functional behaviour of the
pages is covered by the F174 / F180 / F181 fixtures, unchanged.
"""
from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f181_board_usability import PAGES, M, _get, board, rig  # noqa: E402,F401  - single source of the board rig

STATIC = REPO_ROOT / "web" / "board" / "static"
SHEET = "/readonly-pages.css"
LINK = f'<link rel="stylesheet" href="{SHEET}">'
LOGO = "/assets/lybra-logo-black.png"
STATIC_PAGES = ("login.html", "overview.html", "project-detail.html", "index.html")


def _show(text: str) -> None:
    print(text, flush=True)


def _css() -> str:
    return (STATIC / "readonly-pages.css").read_text(encoding="utf-8")


def _split_root(css: str) -> tuple[str, str]:
    """(the :root block, everything else) with comments removed."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = re.search(r":root \{[^{}]*\}", text)
    assert found, "no :root block"
    return found.group(0), text[:found.start()] + text[found.end():]


def _stylesheets(page: str) -> list[str]:
    return re.findall(r'<link[^>]+rel="stylesheet"[^>]*href="([^"]+)"', page)


# ===========================================================================
# (1) one stylesheet entry for every page; (2) logo lockup
# ===========================================================================

def test_skeleton_pages_link_only_the_one_stylesheet_and_show_the_logo(board):
    for path in PAGES + (f"/owner/project?project={M}", f"/owner/roadmap?project={M}", "/owner/overview?lang=en"):
        status, page, _h = _get(board, path)
        assert status == 200, path
        assert _stylesheets(page) == [SHEET], (path, _stylesheets(page))
        assert "<style" not in page and " style=\"color" not in page, path
        nav = re.search(r'<header class="rp-nav">.*?</header>', page, re.S).group(0)
        assert f'<span class="rp-brand"><img class="rp-logo" src="{LOGO}" alt="">Lybra</span>' in nav, path
    _show(f"[skeleton nav raw] {nav}")
    status, body, headers = _get(board, SHEET)
    assert status == 200 and headers.get("Content-Type", "").startswith("text/css") and ".rp-page .rp-nav" in body


def test_static_pages_link_only_the_one_stylesheet_and_show_the_logo():
    sheets = sorted(p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*.css"))
    _show(f"[stylesheets on disk] {sheets}")
    assert sheets == ["readonly-pages.css"]
    for name in STATIC_PAGES:
        page = (STATIC / name).read_text(encoding="utf-8")
        assert _stylesheets(page) == [SHEET], (name, _stylesheets(page))
        assert "<style" not in page, name
        assert f'src="{LOGO}"' in page, name
        for asset in re.findall(r'src="(/assets/[^"]+)"', page):
            assert (STATIC / asset.lstrip("/")).is_file(), (name, asset)
    body_classes = {name: re.search(r"<body([^>]*)>", (STATIC / name).read_text(encoding="utf-8")).group(1) for name in STATIC_PAGES}
    _show(f"[static page body scopes] {body_classes}")
    assert body_classes == {"login.html": ' class="login-body"', "overview.html": ' class="lg-ov"',
                            "project-detail.html": ' class="lg-ov lg-pd"', "index.html": ' class="lg-tc"'}
    logo = (STATIC / LOGO.lstrip("/")).read_bytes()
    assert logo[:8] == b"\x89PNG\r\n\x1a\n"
    assert sorted(p.name for p in (STATIC / "assets").iterdir()) == ["lybra-logo-black.png"]
    from web.board.readonly_pages import _LOGO

    assert _LOGO == LOGO


# ===========================================================================
# (3) stylesheet against the design system's forbidden list
# ===========================================================================

def test_stylesheet_follows_the_design_system_forbidden_list():
    css = _css()
    root, rules = _split_root(css)
    assert css.count(":root {") == 1
    lines = []
    for label, pattern in (("gradient", r"gradient\("), ("box-shadow", r"box-shadow"), ("text-shadow", r"text-shadow"),
                           ("transition", r"\btransition"), ("animation", r"\banimation"), ("keyframes", r"@keyframes"),
                           ("blur", r"blur\(|backdrop-filter"), ("rgb/hsl", r"\b(?:rgba?|hsla?)\("),
                           ("raw hex outside :root", r"#[0-9a-fA-F]{3,8}\b"), ("webfont", r"@font-face|@import"),
                           ("named colour", r":\s*(?:red|blue|purple|violet|indigo|navy|orange|yellow|pink|teal|cyan)\b")):
        hits = re.findall(pattern, rules)
        lines.append(f"{label}={len(hits)}")
        assert not hits, (label, hits[:5])
    radii = re.findall(r"border(?:-[a-z]+)*-radius\s*:\s*([^;]+);", rules)
    lines.append(f"radius declarations={radii}")
    assert all(v.strip() in ("0", "var(--radius-none)") for v in radii), radii
    fonts = sorted(set(v.strip() for v in re.findall(r"font-family\s*:\s*([^;]+);", rules)))
    lines.append(f"font-family values={fonts}")
    assert set(fonts) <= {"var(--font-sans)", "var(--font-mono)", "inherit"}, fonts
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", rules))
    lines.append(f"tokens declared={len(declared)} used={len(used)} undeclared={sorted(used - declared)}")
    assert used <= declared, sorted(used - declared)
    _show("[forbidden-list scan] " + " | ".join(lines))


def test_token_colours_are_the_design_system_green_and_neutrals_only():
    root, _rules = _split_root(_css())
    colours = re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", root)
    assert len(colours) == 24, colours  # 17 web palette + 7 deck palette, copied verbatim
    for name, hexv in colours:
        r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5))
        hue, light, sat = colorsys.rgb_to_hls(r, g, b)
        neutral = max(r, g, b) - min(r, g, b) < 0.03
        assert neutral or 80 <= hue * 360 <= 165, (name, hexv, round(hue * 360))  # green family only, never blue / purple
    expected = {"--green": "#0D5A3B", "--green-dark": "#083D28", "--green-bright": "#1A7A52", "--mint": "#E8F2EA",
                "--mint-2": "#D6E8DC", "--border": "#C8DCCE", "--ink": "#141414", "--muted": "#5F7268", "--white": "#FFFFFF"}
    assert {k: v for k, v in colours if k in expected} == expected
    for alias in ("--state-ok: var(--green-bright)", "--state-warn: var(--muted)", "--state-alert: var(--green-dark)",
                  "--accent-bar: 5px", "--accent-bar-quote: 11px", "--radius-none: 0"):
        assert alias in root, alias
    _show(f"[token colours] {colours}")


def test_cards_and_controls_use_the_design_system_shapes():
    css = _css()
    for rule in (".rp-page .rp-card {", ".rp-page .gr-card {", ".rp-page .rp-ob-project {", ".rp-page .rp-subproject {",
                 ".rp-page .upgrade-project {", ".rp-page .owner-inbox-item {"):
        block = css[css.index(rule):css.index("}", css.index(rule))]
        assert "border-left: var(--accent-bar) solid var(--green)" in block and "background: var(--surface-card)" in block, rule
    assert "*, *::before, *::after { box-sizing: border-box; border-radius: var(--radius-none); }" in css
    assert ".rp-page h1::before { content: \"\"; display: inline-block; width: 11px; height: 11px; background: var(--green);" in css
    assert ".rp-page button.gr-primary { background: var(--green); border-color: var(--green); color: var(--white); }" in css
    assert ".rp-page .rp-nav { display: flex;" in css and "background: var(--white); border-bottom: var(--border-hairline); }" in css
    for alert in (".rp-page .rp-signal.rp-sev-inbox { border-left: var(--bar-alert) solid var(--state-alert);",
                  ".rp-page .roadmap-ms.rp-is-overdue { border-left: var(--bar-alert) solid var(--state-alert); }",
                  ".rp-page .rp-chip-blocked { background: var(--state-alert); color: var(--white);"):
        assert alert in css, alert


# ===========================================================================
# (4) inline styles in static pages / scripts
# ===========================================================================

def test_inline_styles_carry_no_raw_colour_radius_or_shadow():
    offenders = []
    for path in sorted(list(STATIC.glob("*.html")) + list(STATIC.glob("*.js"))):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "style" not in line and "cssText" not in line:
                continue
            for pattern in (r"#[0-9a-fA-F]{3,8}\b(?![-\w])", r"\brgba?\(", r"border-radius", r"box-shadow", r"gradient\("):
                if re.search(r"(?:style=\"|cssText|\.style\.)[^\n]*" + pattern, line):
                    offenders.append(f"{path.name}:{no}: {line.strip()[:120]}")
    _show(f"[inline style offenders] {offenders}")
    assert offenders == []
    chrome = (STATIC / "auth-chrome.js").read_text(encoding="utf-8")
    assert "cssText" not in chrome and 'wrap.className = "auth-chrome";' in chrome
    assert "<style" not in (REPO_ROOT / "web" / "board" / "readonly_pages.py").read_text(encoding="utf-8")
