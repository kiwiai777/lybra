"""AIPOS-205 / AIPOS-221 — Textual rendering layer (the ONLY module importing Textual).

AIPOS-221 redoes this as a codex/claude-code-style plan-chat surface: a natural-language
bottom prompt (a multi-line `TextArea`), a scrolling conversation transcript, `/`-command
autocomplete, async
"working" feedback (the LLM call runs off the event loop in a thread worker), and explicit
next-step guidance after every step.

This is a PURE CLIENT-UX layer. Accountability logic is untouched — copilot stays read-only
(`role="copilot"`, scopes []), the loop is zero-file-write, the truth path is
DRAFT -> Owner -> gate publish, and `/proceed` only lands a draft + stages a publish
**dry-run** (it NEVER publishes; the Owner confirms out of band with the owner token). All
logic lives in state.py / copilot.py (pure, textual-free, testable in the core lane); this
file only wires Textual widgets/keys/worker to that logic. It is exercised in the `tui` CI
lane (textual installed); the core/bare lanes never import it.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Header, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from tools.aipos_cli.home_git import execute_home_git_init, plan_home_git_init
from tools.aipos_cli.workspace_config import (
    project_json_path,
    resolve_home_root,
    scaffold_project,
    set_active_project,
)

# AIPOS-242 (F-o3-18): the standardized PROJECT_SCOPE_DENIED message names the project the GATE
# actually resolved ("active project 'X' is not in the token's projects [...]"). Parsing it is the
# honest signal for the out-of-token-scope case WITHOUT touching the enforcement path
# (_project_gate is a red line). The format is product-owned + pinned by a client-side test.
_DENY_ACTIVE_RE = re.compile(r"active project '([^']+)'")


def _gate_active_from_deny(payload: dict[str, Any]) -> str | None:
    """Extract the gate-resolved active project from a PROJECT_SCOPE_DENIED payload, or None."""
    match = _DENY_ACTIVE_RE.search(str(payload.get("message") or ""))
    return match.group(1) if match else None


def _observe_error_face(payload: dict[str, Any]) -> str | None:
    """AIPOS-242 ROUND 2 (弱代理修复): shared error surface for observe-class commands.

    The gate wraps errors (PROJECT_SCOPE_DENIED, ...) in a top-level {ok: False, error_code: ...,
    message: ...} structure. Chained .get("data",{}).get("summary",{}) COLLAPSES the error to an
    empty dict and then unconditionally renders it as success (silent failure dressed as success —
    the F-o3-18 congruent defect). If the payload carries an error, return a loud string naming the
    code + message; otherwise None (the caller renders success).
    """
    if not payload.get("ok", True):
        code = str(payload.get("error_code") or "UNKNOWN_ERROR")
        msg = str(payload.get("message") or "")
        face = f"{code}: {msg}" if msg else code
        # AIPOS-245 R-5: the gate's own _teaching_error carries a `suggested_next_action`
        # (tools.py:145) that names HOW to fix. Surface it verbatim rather than dropping it —
        # the TUI must NOT parallel-author a reason/fix (that duplicates + can drift from the
        # gate's truth). Absent → show only `code: msg` (never fabricate a next step).
        nxt = str(payload.get("suggested_next_action") or "")
        if nxt:
            face = f"{face}\n  → {nxt}"
        return face
    return None
from tools.lybra_tui.agents_view import render_agents
from tools.lybra_tui.presentation import LYBRA_GREEN, banner, color_enabled
from tools.lybra_tui.state import COPILOT_MODE, MODES, TuiSession

# --- the /command set (Owner ruling 1: `/gates`, not `/confirm`) -------------------
# (command, one-line description). Order is the /help + autocomplete order.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "list all commands + descriptions"),
    ("/draft", "turn the conversation so far into a project-init task-card draft (read-only)"),
    ("/proceed", "land the pending card under 5_tasks/drafts/ + stage a publish dry-run (never publishes; Owner confirms OOB)"),
    ("/queue", "observe the queue summary (read-only)"),
    ("/agents", "snapshot tasks grouped by agent (read-only; as recorded, not live)"),
    ("/validate", "run the validator (read-only)"),
    ("/gates", "list pending confirm gates (read-only view; confirm is OOB)"),
    ("/confirm", "confirm a pending gate (Owner-only, explicit confirmation required)"),
    ("/mode", "switch mode: /mode [observe|confirm|copilot]"),
    ("/audit", "compact L3 authority verdict for a task: /audit [task_id] (read-only)"),
    ("/project", "Owner: /project (list) | new <name> | switch <name> — local, not a gate op"),
    ("/home", "Owner: one-shot local git init of the home: /home git-init (no remote, no push)"),
    ("/compact", "compress the current chat context now (read-only; trims chat only, never truth)"),
    ("/clear", "clear the conversation area"),
    ("/quit", "quit (also /exit, q, Ctrl+C)"),
    ("/exit", "quit (alias of /quit)"),
)
_COMMAND_NAMES = tuple(name for name, _ in COMMANDS)


def apply_cjk_kitty_fix() -> None:
    """AIPOS-237 Slice G (F-o3-12a): enable the kitty keyboard protocol with DISAMBIGUATE only.

    Textual's Linux driver enables the kitty protocol with ``REPORT_ASSOCIATED_TEXT`` (flag 16),
    whose CSI-u *associated text* Textual parses to an EMPTY character under iTerm2 — so IME-typed
    CJK is received but dropped (F-o3-12a). Reducing the enable flag to DISAMBIGUATE-only
    (``\\x1b[>1u``) makes printable text — including CJK — fall back to plain UTF-8 (types correctly),
    while ``DISAMBIGUATE`` still distinguishes Shift+Enter. Owner-confirmed on iTerm2→SSH→WSL.

    This is a client-side override of the driver's OWN module constants; the Textual package is not
    modified. It depends on Textual ``linux_driver``'s PRIVATE constant names, so it is guarded:
      * PRE: the three constant names must exist — a rename/refactor **fails LOUD** (never a silent
        no-op);
      * POST: the result values are asserted (ASSOCIATED_TEXT == 0, ALL_KEYS == 0, DISAMBIGUATE == 1).
    macOS runs through the same ``linux_driver`` (covered); Windows is out of scope.
    """
    from textual.drivers import linux_driver as _ld

    required = (
        "KITTY_DISAMBIGUATE_ESCAPE_CODES",
        "KITTY_REPORT_ALL_KEYS",
        "KITTY_REPORT_ASSOCIATED_TEXT",
    )
    missing = [name for name in required if not hasattr(_ld, name)]
    if missing:
        raise RuntimeError(
            "AIPOS-237 CJK fix cannot apply: textual.drivers.linux_driver is missing "
            f"{missing} (a Textual rename/refactor). Re-derive the DISAMBIGUATE-only kitty "
            "override for this Textual version — do not ship a silent no-op."
        )
    _ld.KITTY_REPORT_ALL_KEYS = 0
    _ld.KITTY_REPORT_ASSOCIATED_TEXT = 0
    if not (
        _ld.KITTY_REPORT_ASSOCIATED_TEXT == 0
        and _ld.KITTY_REPORT_ALL_KEYS == 0
        and _ld.KITTY_DISAMBIGUATE_ESCAPE_CODES == 1
    ):
        raise RuntimeError(
            "AIPOS-237 CJK fix post-condition failed: expected the kitty enable flag to reduce to "
            f"DISAMBIGUATE-only, got DISAMBIGUATE={_ld.KITTY_DISAMBIGUATE_ESCAPE_CODES} "
            f"ALL_KEYS={_ld.KITTY_REPORT_ALL_KEYS} ASSOCIATED_TEXT={_ld.KITTY_REPORT_ASSOCIATED_TEXT}."
        )
_NEXT_AFTER_DRAFT = (
    "Next: review the card, then `/proceed` to land it + stage the publish dry-run "
    "(the Owner confirms out of band).\n"
    # AIPOS-245 A4 (F-o3-9/10). NL-revise is a REAL two-step chain (grounded R-2): say what to
    # change → the copilot answers → reply yes/是 to regenerate. We describe that exact chain and
    # do NOT promise a single utterance regenerates the draft (that would be a phantom / a behavior
    # change out of F′'s presentation-only scope — see F-245-1).
    "满意就 /proceed;想改就说哪里改(如「优先级改成 high」),我先答复,你回 yes/是 我据此重出草稿。"
)
# AIPOS-222 (Owner ruling 1): consent affirmatives (zh/en), case-insensitive + trimmed. An
# affirmative only triggers a draft when a draft-offer is currently pending; otherwise it is
# just another NL chat turn.
_AFFIRMATIVES = frozenset({"yes", "是", "好", "可以"})
# AIPOS-222 ruling 3: the app's consent affordance is bilingual + prominent. It is APP-CONTROLLED
# (so consent arming stays reliable — we never parse an LLM-generated offer). Rendered italic +
# brand-green bold, with a blank line above it (see _render_offer) so it is visually separated
# from the footer/status bar below. The leading `↳` marks it as the suggested next step.
_DRAFT_OFFER = "↳ 生成项目草稿? /draft 或回复 yes/是   ·   generate a draft? /draft or yes"
_COMPACTION_NOTICE = "· earlier turns compacted to keep context small"

# AIPOS-222 ruling 4: a sensible, documented context-budget constant for the honest Ctx%
# estimate. ~200k tokens (the Claude model family context window) × ~4 chars/token ≈ 800k chars.
# Ctx% = min(100, round(100 * payload_chars / CTX_BUDGET_CHARS)); labelled "Ctx:" so it reads as
# an estimate of egress payload size (truth snapshot + chat + system prompt), NOT an exact count.
CTX_BUDGET_CHARS = 800_000
# The truth snapshot is capped at this many chars when sent (mirrors copilot._build_messages'
# `[:20000]` slice) so the estimate matches what actually goes out.
_TRUTH_CAP_CHARS = 20_000

# AIPOS-222 ruling 1: the box-drawing frame glyphs + the block-art llama are the ONLY banner
# parts painted green. Everything else (identity + subtitle text) keeps the terminal default.
_FRAME_GLYPHS = frozenset("╭╮╰╯─│")
_BLOCK_GLYPHS = frozenset("█")


def _banner_markup(raw: str) -> str:
    """Render the banner as Textual content markup: frame + llama green, "lybra" line bold.

    Pure display transform (AIPOS-222 ruling 1). Per character: box-frame glyphs and the block
    llama are wrapped in the brand green; the identity line (the one carrying "Lybra") is bolded;
    all other text stays the terminal default. Narrow-terminal fallback ("LYBRA") is returned
    bold-green unchanged. No `[` appears in banner content, so no markup escaping is needed.
    """
    if "\n" not in raw:  # narrow fallback ("LYBRA"): treat as the brand mark
        return f"[{LYBRA_GREEN} bold]{raw}[/]"
    out_lines: list[str] = []
    for line in raw.splitlines():
        is_identity = "Lybra" in line  # the single "Lybra  vX" identity line → bold (default fg)
        buf: list[str] = []
        run: list[str] = []
        run_style: str | None = None  # current run's style: None=default, the green, or "bold"

        def _flush() -> None:
            if not run:
                return
            text = "".join(run)
            if run_style is None:
                buf.append(text)
            else:
                buf.append(f"[{run_style}]{text}[/]")
            run.clear()

        for ch in line:
            if ch in _FRAME_GLYPHS or ch in _BLOCK_GLYPHS:
                style: str | None = f"{LYBRA_GREEN} bold"
            elif is_identity and not ch.isspace():
                style = "bold"
            else:
                style = None
            if style != run_style:
                _flush()
                run_style = style
            run.append(ch)
        _flush()
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


class PromptArea(TextArea):
    """AIPOS-222 (fix 3): a multi-line `TextArea` that behaves like claude-code's prompt.

    Enter SUBMITS the whole buffer (posts a `PromptArea.Submitted` message); Ctrl+J and
    Shift+Enter insert a NEWLINE for comfortable multi-line composing. ↑/↓ have a
    multi-line-aware precedence: when the cursor is on the FIRST line, ↑ is forwarded to the
    app (history recall / `/` dropdown); otherwise ↑ moves the cursor up a line (the default
    TextArea behavior). Symmetrically for ↓ on the LAST line. The app's `on_key` decides what
    ↑/↓ do once forwarded (dropdown vs. history); this class only decides WHEN to forward.

    Pure display/input wiring — no accountability state crosses this widget.
    """

    class Submitted(Message):
        """Posted when the Owner presses Enter — carries the full buffer text."""

        def __init__(self, prompt: "PromptArea", value: str) -> None:
            super().__init__()
            self.prompt = prompt
            self.value = value

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        # Enter SUBMITS (never inserts a newline); Ctrl+J / Shift+Enter insert a newline.
        if key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if key in ("ctrl+j", "shift+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        # AIPOS-246 S2: PgUp/PgDn/End become the KEYBOARD scroll channel for the conversation
        # (mouse-independent scrollback — the fallback when terminal mouse reporting is broken).
        # Sacrifice disclosed: TextArea's native cursor paging / end-of-line on these keys has no
        # practical value in a 1–3 line prompt (docs/v1_disclosure.md row; /help lists the keys).
        if key in ("pageup", "pagedown", "end"):
            event.stop()
            event.prevent_default()
            scroll = getattr(self.app, "_scroll_conversation", None)
            if scroll is not None:
                scroll(key)
            return
        # Multi-line-aware ↑/↓ precedence: forward to the app (history / dropdown) ONLY at the
        # buffer edge; otherwise let TextArea move the cursor between lines AND stop the event so
        # it does NOT also bubble to App.on_key (which would recall history on top of the move).
        if key == "up" and self.cursor_at_first_line:
            return  # bubble to App.on_key → history recall / dropdown nav
        if key == "down" and self.cursor_at_last_line:
            return  # bubble to App.on_key → history forward / dropdown nav
        if key in ("up", "down"):
            # An in-buffer cursor move (not at the edge): perform it HERE and stop the event so it
            # does NOT also bubble to App.on_key (which would recall history on top of the move).
            event.stop()
            event.prevent_default()
            if key == "up":
                self.action_cursor_up()
            else:
                self.action_cursor_down()
            return
        await super()._on_key(event)


class CopilotResult(Message):
    """Posted from the worker thread back to the app loop with a finished result (or error).

    Carries DATA only — a ``kind`` ("chat" | "draft") plus either a copilot ``ChatReply`` /
    ``DraftProposal`` or an error string. No accountability state crosses this boundary; the
    event loop renders it on the main thread.
    """

    def __init__(self, *, kind: str, proposal: Any = None, reply: Any = None, error: str | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.proposal = proposal
        self.reply = reply
        self.error = error


class LybraTui(App):
    """Owner console: plan-chat over a read-only copilot + observe gate state. Non-daemon."""

    # AIPOS-210: the brand green is injected from the single presentation token at exactly
    # one point (LYBRA_GREEN) — no color literal lives here. `.brand` styles the banner.
    CSS = f"""
    Screen {{ layout: vertical; }}
    /* AIPOS-247 S2: the banner lives IN the conversation flow (first child, `turn` class) — the
       fixed #brandbar layer is gone; the banner scrolls away with the transcript. */
    #banner {{ height: auto; padding: 0; margin: 0; }}
    #conversation {{ height: 1fr; padding: 0 1; }}
    /* F-247-o3-2 (Owner ruling): a scrollbar you cannot drag is a misleading ornament — the
       DEFAULT (no-mouse) session hides it (scrolling itself is untouched: PgUp/PgDn/End +
       anchor). A --mouse session keeps it (it IS draggable there). */
    #conversation.-hide-vscroll {{ scrollbar-size-vertical: 0; }}
    /* AIPOS-245 F-245-o3-4: one blank line AFTER each message block (claude-code style visual
       breathing — user echo / thinking / outputs no longer stick together; CJK included). */
    .turn {{ margin: 0 0 1 0; }}
    /* AIPOS-222 ruling 1: the Owner's turn is bold DEFAULT foreground (NOT green). System
       lines stay muted; green is reserved for logo/frame, the "lybra" line, the thinking
       dot, and the consent affordance. */
    .turn-user {{ text-style: bold; }}
    .turn-system {{ color: $text-muted; }}
    /* AIPOS-222 ruling 3: the consent affordance is the one transcript line that is green —
       italic + bold so it reads as the prominent next-step hint above the footer. */
    .turn-offer {{ color: {LYBRA_GREEN}; text-style: bold italic; }}
    /* AIPOS-222 ruling 2: the blinking thinking dot toggles between bright/dim green. */
    .thinking-dot-on {{ color: {LYBRA_GREEN}; text-style: bold; }}
    .thinking-dot-off {{ color: $text-muted; }}
    #status {{ height: 1; color: $text-muted; }}
    /* AIPOS-222 (fix 1+2): claude-code-style prompt row — a green `>` gutter to the left of a
       multi-line TextArea whose ONLY chrome is a two-rule (top+bottom) border in LYBRA_GREEN.
       The default Textual TextArea focus box (a full blue border) is overridden away on every
       side, then re-drawn as just the top+bottom rules in brand green (focused AND unfocused, so
       it never flashes blue). */
    #promptrow {{ height: auto; }}
    #prompt-gutter {{ width: 2; height: 3; color: {LYBRA_GREEN}; text-style: bold; padding: 0; content-align: left middle; }}
    #cmd {{
        height: auto;
        max-height: 10;
        border: none;
        border-top: solid {LYBRA_GREEN};
        border-bottom: solid {LYBRA_GREEN};
        padding: 0 1;
        background: $surface;
    }}
    #cmd:focus {{
        border: none;
        border-top: solid {LYBRA_GREEN};
        border-bottom: solid {LYBRA_GREEN};
    }}
    /* AIPOS-222 ruling 4: claude-code-style footer directly under the input. */
    #ctxbar {{ height: 1; color: $text-muted; }}
    #ac {{ max-height: 8; display: none; }}
    .brand {{ color: {LYBRA_GREEN}; text-style: bold; }}
    """
    BINDINGS = [
        # AIPOS-221: Shift+Tab kept as a convenience; `/mode` is the no-chord path.
        Binding("shift+tab", "toggle_mode", "Mode"),
        Binding("escape", "cancel", "Cancel/Reject"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        session: TuiSession,
        copilot_session: Any = None,
        *,
        workspace_root: str | None = None,
        mouse: bool = False,
    ) -> None:
        super().__init__()
        self._session = session
        # AIPOS-247 S1: whether this session runs with mouse capture ON (`--mouse`). Display-only:
        # gates the one-line cost disclosure at startup; the capture itself is App.run(mouse=...).
        self._mouse = mouse
        # AIPOS-206: optional read-only planning copilot (CopilotSession). When absent,
        # NL input explains how to enable it; the owner session is unaffected.
        self._copilot = copilot_session
        self._workspace_root = workspace_root
        self._pending_preview: Any = None
        self._pending_proposal: Any = None  # last copilot DraftProposal awaiting Owner /proceed
        self._pending_confirm: dict[str, Any] | None = None  # pending confirm gate awaiting affirmation (是/yes//confirm)
        # AIPOS-246 F-246-o3-2: conversation anchor engages lazily on first overflow (_maybe_anchor).
        self._anchor_engaged: bool = False
        # AIPOS-222 conversational substate (pure client-UX; no accountability state):
        self._pending_offer = False  # a "generate draft?" offer is open → an affirmative consents
        self._thinking: Static | None = None  # the inline "· thinking… (Ns)" line under the turn
        self._thinking_seconds = 0
        self._thinking_dot_on = True  # AIPOS-222: pulse state for the blinking thinking marker+word
        self._thinking_timer: Any = None
        self._thinking_up_est = 0  # char-based egress (↑) estimate shown while thinking (`~`)
        self._thinking_down_est = 0  # char-based answer (↓) estimate for the final line if no real usage
        # ↑/↓ shell-style input history (session-local, in-memory; never persisted to disk):
        self._history: list[str] = []
        self._history_index: int | None = None  # None = not browsing; else index into _history
        self._history_draft = ""  # the in-progress line preserved when stepping past the newest

    def compose(self) -> ComposeResult:
        yield Header()
        # AIPOS-247 S2: no fixed #brandbar layer — the banner is mounted in on_mount as the FIRST
        # #conversation child, so it scrolls away with the flow (claude-code shape).
        yield VerticalScroll(id="conversation")
        yield Static(id="status")
        # AIPOS-222 (fix 1+2+3): a claude-code-style prompt row — a green `>` gutter followed by a
        # multi-line PromptArea (TextArea). Enter submits; Shift+Enter / Ctrl+J insert a newline.
        yield Horizontal(
            Static(">", id="prompt-gutter"),
            PromptArea(id="cmd", soft_wrap=True),
            id="promptrow",
        )
        # AIPOS-222 ruling 4: claude-code-style footer directly under the input —
        # `<model> · <dir> | Ctx: <n>%`. Honest context-budget estimate.
        yield Static(id="ctxbar")
        yield OptionList(id="ac")
        yield Footer()

    def on_mount(self) -> None:
        # AIPOS-210/222: startup banner (narrow terminals fall back to plain LYBRA). Ruling 1:
        # ONLY the llama logo + the box frame are green; the "lybra" identity line is bold; the
        # subtitle lines use the terminal default foreground. We render per-line content markup
        # (display-only) instead of class-coloring the whole Static green.
        # AIPOS-247 S2: the banner joins the conversation FLOW (first child, before the welcome
        # lines) instead of a fixed layer — it scrolls away once content overflows, and /clear
        # removes it with the rest (disclosed; no rebuild — rendered ONCE here, never on resize,
        # same as the pre-247 single mount-time render). Width accounts for the container's 0 1
        # padding (content width = terminal - 2).
        raw = banner(self.size.width - 2)
        banner_widget = Static(
            _banner_markup(raw) if color_enabled() else raw, id="banner", classes="turn"
        )
        convo = self.query_one("#conversation", VerticalScroll)
        # F-247-o3-2: hide the (undraggable) scrollbar in default sessions; keep it under --mouse.
        convo.set_class(not self._mouse, "-hide-vscroll")
        convo.mount(banner_widget)
        # F-247-o3-1 (latent since 246 F-246-o3-2): anchor engagement must also fire on CONTENT
        # GROWTH, not only on append — a single Markdown reply lays out asynchronously and grows
        # AFTER the append-side checks (pre-mount sync + one call_after_refresh); with no later
        # message the anchor never engaged and the view stayed pinned at the top (O3: PgDn by
        # hand). `virtual_size` is the textual-native reactive for content size — this watch is
        # message-driven (zero polling/timers). _maybe_anchor stays the single engagement gate
        # (engage-once; a RELEASED anchor is never re-engaged here — _anchor_engaged short-circuits).
        self.watch(convo, "virtual_size", self._on_conversation_growth, init=False)
        # AIPOS-221/222 (ruling 4): the chat prompt is a multi-line TextArea that accepts CJK /
        # wide chars natively (no `restrict`/`type` filter exists on a TextArea to drop them). The
        # `/` OptionList dropdown is the primary command autocomplete (driven by TextArea.Changed).
        cmd = self.query_one("#cmd", PromptArea)
        # Short, single-line placeholder (claude-code style): a long one can soft-wrap to a 2nd
        # line in a narrow pane, inflating the input box. The full hint is in the welcome line.
        cmd.placeholder = "Type a task, or /help"
        # AIPOS-246 S1 + F-246-o3-2: the conversation anchor is engaged LAZILY (see _maybe_anchor)
        # — NOT here. Anchoring a shorter-than-a-screen container makes the compositor set a
        # NEGATIVE scroll_y (installed 8.2.8 _compositor.py:609-618 sets scroll_y = content bottom
        # minus viewport height via set_reactive, bypassing the clamp), pinning the welcome text to
        # the viewport bottom (Owner O3 regression). The anchor engages on first overflow instead.
        self.query_one("#ac", OptionList).display = False
        self._render_status()
        self._render_ctxbar()
        self.set_focus(cmd)
        self._system(
            "Connected. Type what you want to do in plain language and press Enter; "
            "or start with `/` for a command (try `/help`)."
        )
        # AIPOS-245 A6: a one-line full-loop map so the Owner knows where each step lives.
        self._system(
            "本环:发任务(说需求 → /proceed)→ agent 认领 → 你 /gates + /confirm → /audit 看判定。"
        )
        # AIPOS-247 S1 (P-A, R-C): the `--mouse` cost disclosure prints ONLY when the flag is on —
        # a default session's startup output is byte-identical to pre-247.
        if self._mouse:
            self._system("鼠标模式:滚轮/点击进 TUI;选中复制用 Option+拖拽(iTerm2)。")

    # --- conversation transcript (codex/claude-code style message stream) ---------

    def _on_conversation_growth(self) -> None:
        # F-247-o3-1 (R3): content-size-change side of the lazy anchor (see the watch in
        # on_mount). Growth is a ONE-SHOT event, so the check must be SYNCHRONOUS and race-free:
        # - max_scroll_y is NOT readable here: the virtual_size reactive fires mid-layout, before
        #   `_container_size` commits in the same pass (widget.py `_size_updated` assigns `_size`
        #   → `virtual_size` [watcher fires] → `_container_size`), so max reads a stale container
        #   (measured: startup vh=43/max=43 transient — the R2 reason for deferring).
        # - Deferring loses the race instead (R3 O3 REJECTED): call_after_refresh is a multi-hop
        #   chain (App pump InvokeLater → screen._callbacks → next update/idle flush) and the
        #   one-shot event has no re-trigger — the Owner's machine missed the engagement forever.
        # The SAME-pass consistent pair is (size, virtual_size): `_size` commits BEFORE the
        # watcher fires. And virtual_size is FLOORED at the container (compositor total_region
        # starts from the widget's own region, then unions content) — so vh > ch ⟺ genuine
        # content overflow; short content has vh == ch and can never false-engage (F-246-o3-2
        # stays impossible). Zero polling, zero deferral, zero scheduling dependence.
        if self._anchor_engaged:
            return
        convo = self.query_one("#conversation", VerticalScroll)
        if convo.virtual_size.height > convo.size.height > 0:
            convo.anchor()
            self._anchor_engaged = True

    def _maybe_anchor(self) -> None:
        # AIPOS-246 F-246-o3-2: engage the anchor only once the conversation actually OVERFLOWS a
        # screen (max_scroll_y > 0). Before that, anchoring is harmful (compositor pins short
        # content to the viewport bottom via a negative scroll_y — Owner O3 regression). At the
        # first overflow the view is necessarily at its only position (scroll_y 0 == bottom-most
        # reachable until now), so engaging + snapping to bottom IS the follow the Owner expects.
        # Runs via call_after_refresh so max_scroll_y reflects the just-mounted widget.
        if self._anchor_engaged:
            return
        convo = self.query_one("#conversation", VerticalScroll)
        if convo.max_scroll_y > 0:
            convo.anchor()
            self._anchor_engaged = True

    def _append(self, text: str, css_class: str) -> None:
        # AIPOS-246 S1: no per-mount scroll_visible — the #conversation container is ANCHORED
        # lazily (F-246-o3-2, _maybe_anchor). Anchored = follows new content at the bottom; a user
        # scroll-up releases the anchor (textual>=4.0 native), so new messages no longer yank the
        # view back (F-245-o3-1).
        convo = self.query_one("#conversation", VerticalScroll)
        # Engagement check BOTH before the mount (sync — sees the SETTLED layout of previous
        # content; covers a burst of same-tick mounts whose after-refresh callbacks can run before
        # layout settles) and after the refresh. Engagement therefore lags overflow by at most one
        # message — disclosed in the card.
        self._maybe_anchor()
        widget = Static(text, classes=f"turn {css_class}")
        convo.mount(widget)
        self.call_after_refresh(self._maybe_anchor)

    def _markdown(self, text: str, css_class: str) -> None:
        # AIPOS-222 (fix 4): render copilot answers + task-card content as Textual `Markdown` so
        # fenced ```code``` blocks are syntax-highlighted (Rich/Pygments — ships with Textual, no
        # new dependency), exactly like codex/claude-code. Pure display; no accountability state.
        convo = self.query_one("#conversation", VerticalScroll)
        self._maybe_anchor()  # sync pre-mount check (see _append)
        widget = Markdown(text, classes=f"turn {css_class}")
        convo.mount(widget)  # AIPOS-246 S1: (lazily) anchored container follows; no per-mount scroll
        self.call_after_refresh(self._maybe_anchor)

    def _user(self, text: str) -> None:
        self._append(f"› {text}", "turn-user")

    def _copilot_msg(self, text: str) -> None:
        # The copilot's NL answer / card draft is Markdown so fenced code is highlighted.
        self._markdown(text, "turn-copilot")

    def _pre(self, text: str) -> None:
        # Preformatted command output (e.g. /help, /queue JSON, /gates) — stays a plain Static so
        # aligned columns / JSON indentation survive (Markdown would reflow the whitespace).
        self._append(text, "turn-copilot")

    def _system(self, text: str) -> None:
        self._append(text, "turn-system")

    def _offer(self, text: str) -> None:
        # AIPOS-222 ruling 3: the consent affordance — a blank spacer line above it separates it
        # from the footer/status bar; the line itself is green + bold + italic (turn-offer).
        self._append("", "turn-system")  # blank spacer above the affordance
        self._append(text, "turn-offer")

    def _render_status(self) -> None:
        self.query_one("#status", Static).update(self._session.status_line())

    # --- AIPOS-222 ruling 4: claude-code-style footer `<model> · <dir> | Ctx: <n>%` ----

    def _model_id(self) -> str:
        # The LLM model id from the copilot's LLMConfig; `no-llm` when no copilot/LLM is wired.
        cfg = getattr(self._copilot, "_llm", None)
        cfg = getattr(cfg, "_config", None)
        model = getattr(cfg, "model", None)
        return str(model) if model else "no-llm"

    def _dir_label(self) -> str:
        # The workspace dir with $HOME abbreviated to ~ (claude-code style). Falls back to "·".
        root = self._workspace_root
        if not root:
            return "·"
        home = os.path.expanduser("~")
        if root == home or root.startswith(home + os.sep):
            return "~" + root[len(home):]
        return root

    def _ctx_percent(self) -> int:
        # Honest egress-size estimate: capped truth snapshot + all L3 chat + the system prompt,
        # over the documented CTX_BUDGET_CHARS. Mirrors copilot._build_messages' truth cap so the
        # number tracks what is actually sent. Returns 0 when there is no copilot/memory.
        memory = getattr(self._copilot, "memory", None)
        if memory is None:
            return 0
        truth = getattr(memory, "l0_truth", {}) or {}
        try:
            truth_chars = min(len(json.dumps(truth, sort_keys=True)), _TRUTH_CAP_CHARS)
        except (TypeError, ValueError):
            truth_chars = 0
        chat_chars = sum(len(getattr(t, "content", "") or "") for t in getattr(memory, "l3_chat", []))
        try:
            from tools.lybra_tui.copilot import _CHAT_SYSTEM_PROMPT
            system_chars = len(_CHAT_SYSTEM_PROMPT)
        except Exception:
            system_chars = 0
        payload = truth_chars + chat_chars + system_chars
        return min(100, round(100 * payload / CTX_BUDGET_CHARS))

    def _scroll_conversation(self, key: str) -> None:
        # AIPOS-246 S2: keyboard scroll channel (forwarded from PromptArea; focus stays on the
        # prompt). PgUp/PgDn page the conversation (scroll_to path → releases the anchor, textual
        # native); End returns to the bottom via scroll_end (release_anchor=False → re-engages
        # following at the bottom, §3 c). Pure view navigation — no state, no gate.
        convo = self.query_one("#conversation", VerticalScroll)
        if key == "pageup":
            convo.scroll_page_up(animate=False)
        elif key == "pagedown":
            convo.scroll_page_down(animate=False)
        else:
            convo.scroll_end(animate=False)

    def _render_ctxbar(self) -> None:
        ctxbar = self.query_one("#ctxbar", Static)
        ctxbar.update(f"{self._model_id()} · {self._dir_label()} | Ctx: {self._ctx_percent()}%")

    # --- mode / cancel ------------------------------------------------------------

    def action_toggle_mode(self) -> None:
        self._session.toggle_mode()
        self._render_status()
        self._system(f"Mode → {self._session.mode}.")

    def action_cancel(self) -> None:
        # Esc = reject / do not submit (never a default-yes). Also closes the autocomplete.
        ac = self.query_one("#ac", OptionList)
        if ac.display:
            ac.display = False
            self.set_focus(self._prompt())
            return
        self._pending_preview = None
        self._system("Cancelled. Nothing submitted.")

    # --- prompt (multi-line TextArea) helpers -------------------------------------

    def _prompt(self) -> PromptArea:
        return self.query_one("#cmd", PromptArea)

    @staticmethod
    def _set_prompt_text(cmd: PromptArea, text: str) -> None:
        # Replace the buffer and park the cursor at the end (mirrors the old Input.value setter).
        cmd.text = text
        cmd.move_cursor(cmd.document.end)

    # --- autocomplete: live-filtered OptionList overlay (Textual core only) -------

    def _autocomplete(self) -> OptionList | None:
        # The overlay may not be mounted yet (early TextArea.Changed during startup) or already
        # torn down (run_test teardown) — return None instead of raising NoMatches.
        try:
            return self.query_one("#ac", OptionList)
        except Exception:
            return None

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Drive the `/` command dropdown off the multi-line TextArea's content. Only a single-line
        # buffer that starts with `/` and has no space yet (the command token) opens the dropdown.
        if event.text_area.id != "cmd":
            return
        value = event.text_area.text
        ac = self._autocomplete()
        if ac is None:
            return
        if not value.startswith("/") or " " in value or "\n" in value:
            ac.display = False
            return
        token = value.lower()
        matches = [(n, d) for n, d in COMMANDS if n.startswith(token)]
        ac.clear_options()
        if not matches:
            ac.display = False
            return
        for name, desc in matches:
            ac.add_option(Option(f"{name}  —  {desc}", id=name))
        ac.display = True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # ↑/↓ + Enter in the dropdown inserts the command into the prompt, then refocuses it.
        name = event.option.id or ""
        cmd = self._prompt()
        self._set_prompt_text(cmd, f"{name} ")
        self.query_one("#ac", OptionList).display = False
        self.set_focus(cmd)

    def on_key(self, event) -> None:
        # CLEAR PRECEDENCE (AIPOS-222): when the `/` autocomplete dropdown is OPEN, ↑/↓ navigate
        # the dropdown. ONLY when it is CLOSED do ↑/↓ recall shell-style input history — and then
        # only at the buffer edge (PromptArea forwards ↑/↓ to the App only when the cursor is on
        # the first/last line; otherwise it moves the cursor between lines itself, so this handler
        # never sees those events). Enter in the dropdown is handled by the OptionList itself.
        ac = self._autocomplete()
        if ac is not None and ac.display:
            if event.key in ("up", "down"):
                self.set_focus(ac)
            return
        if event.key == "up":
            self._history_recall(-1)
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self._history_recall(+1)
            event.prevent_default()
            event.stop()

    # --- ↑/↓ input history recall (session-local, in-memory, no disk persistence) --

    def _history_recall(self, direction: int) -> None:
        if not self._history:
            return
        cmd = self._prompt()
        if self._history_index is None:
            # Begin browsing: preserve the in-progress draft line so ↓ past the newest restores it.
            if direction > 0:
                return  # ↓ with nothing being browsed is a no-op
            self._history_draft = cmd.text
            self._history_index = len(self._history) - 1
        else:
            new_index = self._history_index + direction
            if new_index < 0:
                self._history_index = 0  # clamp at the oldest entry
            elif new_index >= len(self._history):
                # Stepped past the newest entry → restore the preserved in-progress draft line.
                self._history_index = None
                self._set_prompt_text(cmd, self._history_draft)
                return
            else:
                self._history_index = new_index
        self._set_prompt_text(cmd, self._history[self._history_index])

    # --- submit: NL-first routing -------------------------------------------------

    def on_prompt_area_submitted(self, event: "PromptArea.Submitted") -> None:
        if event.prompt.id != "cmd":
            return
        raw = event.value
        event.prompt.text = ""
        self.query_one("#ac", OptionList).display = False
        text = raw.strip()
        # AIPOS-246 S1b (R boundary): ONLY an Owner submission re-bottoms the view (you typed →
        # you want the current turn). Gate events / new messages / worker replies / thinking
        # frames must NEVER trigger this — they rely solely on the passive anchor. Empty Enter
        # is not a submission (it is the R-h1 cancel path), so it does not re-bottom.
        if text:
            self._maybe_anchor()  # F-246-o3-2: an Owner submission is also an engagement point
            self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)
        if not text:
            # AIPOS-244 R-h1: an empty Enter while a confirm is pending IS a non-affirmative →
            # cancel loudly (the promised "其余输入取消" contract; previously the early return let
            # the pending survive an empty Enter). No pending → ignore as before.
            if self._pending_confirm is not None:
                self._pending_confirm = None
                self._pre("已取消 confirm。")
            return
        # Record the submitted line in the in-memory history and reset the browse cursor.
        self._history.append(text)
        self._history_index = None
        self._history_draft = ""
        if text.startswith("/"):
            # AIPOS-244 R-h1 严格模态: pending 挂起期间,/confirm(affirmation 阶段)是肯定词;
            # 其余一切 / 命令先响亮取消 pending、再照常执行。pending 绝不允许跨命令存活——stale
            # pending 会把后续对话里的一句"是"变成意外的真相写入。
            if self._pending_confirm and self._pending_confirm.get("awaiting") == "affirmation" and text.lower() == "/confirm":
                self._user(text)
                self._execute_pending_confirm()
            else:
                # AIPOS-245 F-245-o3-4: echo the Owner's command line (claude-code style `› /cmd`)
                # so every output block is visibly paired with the input that produced it. This also
                # makes a REPEATED command (↑ history recall + Enter) distinguishable from a double
                # print — without the echo, two consecutive /audit runs render two identical verdict
                # lines back-to-back (the F-245-o3-2 symptom shape).
                self._user(text)
                if self._pending_confirm is not None:
                    self._pending_confirm = None
                    self._pre("已取消 confirm(你执行了其他命令)。")
                self._handle_command(text)
        elif self._pending_confirm and self._pending_confirm.get("awaiting") == "actor":
            # AIPOS-244 R-2: waiting for actor input
            if text:
                self._user(text)
                actor = text.strip()
                # 拿到 actor,现在展示确认问句
                gate = self._pending_confirm["gate"]
                op = self._pending_confirm["op"]
                task_id = self._pending_confirm["task_id"]

                if op == "claim":
                    question = f"确认把 {task_id} 批给 {actor} (claim) 吗?"
                elif op == "return":
                    question = f"确认接受 {actor} 的 {task_id} return 吗?"
                elif op == "publish":
                    question = f"确认发布草稿 {task_id} 到队列吗?"
                else:
                    question = f"确认执行 {op} {task_id} 吗?"

                # AIPOS-245 F-245-o3-4b: the confirm prompt is ONE logical block → one widget
                # (tight inside; the .turn margin provides the breathing BETWEEN blocks).
                self._pre(
                    f"Preview: {op} {task_id}\n"
                    f"归因给: {actor}\n"
                    f"{question}\n"
                    "输入 是 / yes / /confirm 确认; 其余输入取消。"
                )

                # 更新 pending_confirm 状态:现在等待肯定词
                self._pending_confirm["actor"] = actor
                self._pending_confirm["awaiting"] = "affirmation"
            else:
                # 空输入 → 取消
                self._pre("已取消 confirm(未输入 actor)。")
                self._pending_confirm = None
        elif self._pending_confirm and self._pending_confirm.get("awaiting") == "affirmation" and text.lower() in ["是", "yes", "/confirm"]:
            # AIPOS-244: affirmative reply to pending confirm → execute gate
            self._user(text)
            self._execute_pending_confirm()
        elif self._pending_confirm:
            # AIPOS-244: any other input (空回车/Esc/其他) → cancel confirm
            self._user(text) if text else None
            self._pre("已取消 confirm。")
            self._pending_confirm = None
        elif self._pending_offer and text.lower() in _AFFIRMATIVES:
            # Owner ruling 1: an affirmative reply IMMEDIATELY AFTER a draft-offer = consent.
            self._user(text)
            self._consent_to_draft()
        else:
            # NL-first: the WHOLE line (spaces + CJK intact) is a conversational turn.
            self._submit_intent(raw.strip())

    # --- command handler ----------------------------------------------------------

    def _handle_command(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]
        try:
            if cmd == "/help":
                self._cmd_help()
            elif cmd == "/queue":
                # AIPOS-242 ROUND 2: check the error surface BEFORE chained .get (弱代理修复 — the
                # gate error structure is {ok: False, error_code, message}, not {data: {summary}}).
                result = self._session.observe("queue")
                err = _observe_error_face(result)
                if err:
                    self._pre(f"Error: {err}")
                else:
                    self._pre(json.dumps(result.get("data", {}).get("summary", {}), indent=2))
                    self._system("Read-only queue summary (no truth changed).")
            elif cmd == "/agents":
                self._cmd_agents()
            elif cmd == "/validate":
                result = self._session.observe("validate")
                err = _observe_error_face(result)
                if err:
                    self._pre(f"Error: {err}")
                else:
                    self._pre(json.dumps(result.get("summary", {}), indent=2))
                    self._system("Read-only validator run (no truth changed).")
            elif cmd == "/gates":
                self._cmd_gates()
            elif cmd == "/confirm":
                self._cmd_confirm(args[0] if args else None)
            elif cmd == "/mode":
                self._cmd_mode(args[0] if args else None)
            elif cmd == "/audit":
                self._cmd_audit(args[0] if args else None)
            elif cmd == "/project":
                self._cmd_project(args)
            elif cmd == "/home":
                self._cmd_home(args)
            elif cmd == "/clear":
                convo = self.query_one("#conversation", VerticalScroll)
                convo.remove_children()
                # AIPOS-246 F-246-o3-2: after /clear the content is short again — an engaged anchor
                # would re-pin it to the viewport bottom (negative-scroll compositor path). Reset;
                # it re-engages lazily at the next overflow.
                convo.anchor(False)
                self._anchor_engaged = False
                self._system("Conversation cleared.")
            elif cmd == "/draft":
                self._consent_to_draft()
            elif cmd == "/proceed":
                self._copilot_proceed(args[0] if args else None)
            elif cmd == "/compact":
                self._cmd_compact()
            elif cmd in ("/quit", "/exit"):
                self.exit()
            else:
                self._system(f"Unknown command: {cmd}. Try `/help`.")
        except Exception as exc:  # surface gate/read errors without crashing the console
            self._system(f"Error: {exc}")

    def _cmd_help(self) -> None:
        lines = ["Commands:"]
        width = max(len(n) for n, _ in COMMANDS)
        for name, desc in COMMANDS:
            lines.append(f"  {name.ljust(width)}  {desc}")
        lines.append("")
        lines.append("Anything without a leading `/` is sent to the copilot as a task intent.")
        # AIPOS-246 S2: keyboard scroll channel disclosure.
        lines.append("Scrollback: PgUp/PgDn 翻看会话历史,End 回到底部并恢复跟随(上滚后新消息不再拽回)(Mac: Fn+↑ / Fn+↓ / Fn+→)。")
        self._pre("\n".join(lines))

    def _cmd_agents(self) -> None:
        # AIPOS-234: read-only snapshot — group the SAME queue rows /queue reads, by owning agent.
        # One-shot on-command (never auto-refresh); a projection of recorded truth, not live state.
        # AIPOS-242 ROUND 2: check error surface before chained .get (同病同治).
        result = self._session.observe("queue")
        err = _observe_error_face(result)
        if err:
            self._pre(f"Error: {err}")
            return
        tasks = result.get("data", {}).get("tasks", []) or []
        self._pre(render_agents(tasks))
        self._system(
            "Read-only snapshot grouped by agent (no truth changed). "
            "As recorded — Lybra does not track live presence."
        )

    @staticmethod
    def _gates_list_lines(gates: list[dict[str, Any]]) -> list[str]:
        # AIPOS-245 A3: show the task title + canonical claimant per row so the Owner sees WHAT
        # they'd confirm and WHO it's attributed to. Data is already carried in g["task"] (pure
        # presentation — no change to list_confirm_gates). Missing fields fall back to a neutral
        # placeholder; NEVER pre-fill an answer (P-2 / default-yes red line).
        # F-248-o3-2 (shared with bare /confirm — see _cmd_confirm): also surface claim_id when
        # the gate's task carries one (return gates), so /confirm <claim_id> has something to match.
        lines = ["Pending confirm gates (read-only view):"]
        for i, g in enumerate(gates):
            task = g.get("task") or {}
            title = task.get("title") or "(无标题)"
            assigned_to = task.get("assigned_to") or "(未归因)"
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            claim_id = metadata.get("claim_id")
            selector_hint = f"/confirm {i}" + (f" 或 /confirm {claim_id}" if claim_id else "")
            lines.append(
                f"  [{i}] {g['op']} {g['task_id']} — {title}(归因 {assigned_to})  用 {selector_hint} 确认"
            )
        return lines

    def _cmd_gates(self) -> None:
        # Ruling 1: `/gates` is a READ-ONLY VIEW of pending confirm gates — not an action.
        gates = self._session.confirm_gates()
        if not gates:
            self._pre("No pending confirm gates.")
        else:
            self._pre("\n".join(self._gates_list_lines(gates)))
        self._system("Use /confirm <n> to confirm a gate (Owner-only, explicit confirmation required).")

    def _cmd_confirm(self, selector: int | str | None = None) -> None:
        """Execute a pending confirm gate (Owner-only, explicit confirmation required).

        `selector` is the `/confirm` argument: an int index (existing call sites/tests pass
        this directly), a numeric-string index or a claim_id string from the command line
        (F-248-o3-2, best-effort claim_id match against each gate's task metadata), or None
        (bare `/confirm`).
        """
        gates = self._session.confirm_gates()
        if not gates:
            self._pre("No pending confirm gates.")
            # AIPOS-245 B12 (P-A). Wording corrected against the REAL gate derivation (gates come
            # from queue truth: pending task → claim gate, claimed task → return gate — the agent
            # does NOT claim; deviation from the DRAFT's proposed wording disclosed in the ledger).
            self._system("→ 无待确认项:/queue 看队列;有 pending/claimed 任务才会出 gate。要发新任务就(copilot 模式下)说需求 → /proceed。")
            return

        # 1. 确定 gate 索引(int 下标、数字字符串下标,或 F-248-o3-2:claim_id 字符串匹配)
        index: int | None
        if selector is None:
            index = None
        elif isinstance(selector, int):
            index = selector
        elif selector.isdigit():
            index = int(selector)
        else:
            index = next(
                (
                    i
                    for i, g in enumerate(gates)
                    if str(((g.get("task") or {}).get("metadata") or {}).get("claim_id") or "") == selector
                ),
                None,
            )
            if index is None:
                self._pre(f"Unknown gate selector {selector!r} (not an index or a known claim_id).")
                self._pre("\n".join(self._gates_list_lines(gates)))
                return
        if index is None:
            if len(gates) == 1:
                index = 0
            else:
                # F-248-o3-2 (O3 real-machine finding): bare /confirm with >1 pending gate used to
                # print only a terse count line — the Owner had no way to see WHICH gates without a
                # separate /gates call. /confirm is the RESERVED fallback execution surface (Ruling
                # 1: /gates is the read-only view, /confirm executes) — it must show the same list.
                self._pre("\n".join(self._gates_list_lines(gates)))
                self._system(f"{len(gates)} pending gates. Use /confirm <n> (or /confirm <claim_id>) to specify.")
                return
        if index < 0 or index >= len(gates):
            self._pre(f"Invalid gate index {index}. Valid range: 0-{len(gates)-1}.")
            return

        gate = gates[index]
        op = gate.get("op", "unknown")
        task_id = gate.get("task_id", "unknown")
        task = gate.get("task", {})

        # 2. 提取 actor/agent_instance(R-2:缺 assigned_to → 先问 actor)
        assigned_to = task.get("assigned_to")
        if not assigned_to:
            # 缺失 assigned_to → 置 pending_confirm_ask_actor 状态,等 Owner 输入 actor
            # AIPOS-245 F-245-o3-4b: one logical block → one widget (tight inside).
            self._pre(f"任务 {task_id} 无 assigned_to。\n归因给哪个 agent? 输入 actor 名:")
            # AIPOS-245 B1 (P-A): the honest fix path — assigned_to is REQUIRED card schema, so the
            # gate will loudly BLOCK a claim on this card regardless of the actor typed here (AIPOS-244
            # UX note). Name the durable fix; never pre-fill an actor.
            self._system("(或先给任务卡补上 assigned_to 再 /confirm——缺必填字段时 gate 会响亮 BLOCK。)")
            self._pending_confirm = {
                "gate": gate,
                "op": op,
                "task_id": task_id,
                "awaiting": "actor",  # 等待 actor 输入
            }
            return

        # 3. 展示 preview + 自然语言问句
        if op == "claim":
            question = f"确认把 {task_id} 批给 {assigned_to} (claim) 吗?"
        elif op == "return":
            question = f"确认接受 {assigned_to} 的 {task_id} return 吗?"
        elif op == "publish":
            question = f"确认发布草稿 {task_id} 到队列吗?"
        else:
            question = f"确认执行 {op} {task_id} 吗?"

        # AIPOS-245 F-245-o3-4b: the confirm prompt is ONE logical block → one widget
        # (tight inside; the .turn margin provides the breathing BETWEEN blocks).
        self._pre(
            f"Preview: {op} {task_id}\n"
            f"归因给: {assigned_to}\n"
            f"{question}\n"
            "输入 是 / yes / /confirm 确认; 其余输入取消。"
        )

        # 4. 置 pending_confirm 状态并 return(不发射,等下一次输入拦截)
        self._pending_confirm = {
            "gate": gate,
            "op": op,
            "task_id": task_id,
            "actor": assigned_to,
            "awaiting": "affirmation",  # 等待肯定词
        }

    def _execute_pending_confirm(self) -> None:
        """Execute the pending confirm gate (called after affirmative input)."""
        if not self._pending_confirm:
            return

        pending = self._pending_confirm
        self._pending_confirm = None  # 清状态

        gate = pending["gate"]
        actor = pending.get("actor")

        # 调用既有 confirm_client 机器
        try:
            preview = self._session.preview_gate(gate)
            result = self._session.confirm_gate(preview, actor=actor)
        except Exception as e:
            self._pre(f"Confirm failed: {e}")
            return

        # 诚实呈现(前置错误面检查,Slice D 纪律)
        err = self._observe_error_face(result)
        if err:
            # `err` already carries the gate's own message + suggested_next_action (R-5 pass-through
            # in _observe_error_face). The TUI adds ONLY a loop-position line here — it does NOT
            # re-author the failure reason (that stays the gate's job). This is orthogonal to the
            # gate's suggested_next_action: it tells the Owner WHERE in the loop they are.
            self._pre(f"Confirm failed: {err}")
            self._system("↳ 你在 confirm 环;/gates 重看待确认项。")
            return

        # 成功:展示写了什么
        op = pending["op"]
        task_id = pending["task_id"]
        data = result.get("data", {})
        planned_writes = data.get("planned_writes", [])
        if planned_writes:
            # AIPOS-245 F-245-o3-4b: the writes table is ONE logical block → one widget
            # (rows stay single-spaced; no blank line between rows).
            self._pre(
                "Confirmed. Writes:\n"
                + "\n".join(f"  {w.get('kind', 'unknown')} {w.get('path', 'unknown')}" for w in planned_writes)
            )
        else:
            self._pre(f"Confirmed: {op} {task_id}")
        # AIPOS-245 A1/A2 (P-A: 成功分支也要"下一步"). Overlay the loop position after a
        # successful confirm. `actor` is the canonical claimant recorded by the pending machine
        # and already validated actor==canonical by the gate (R-3: transmit verbatim, never a
        # derived/friendly name — aliases are a separate slice).
        if op == "claim":
            who = actor or "(未归因)"
            self._system(
                f"→ 已批给 {who}。通知 agent 开工;完成后回 /gates 看 return gate 再 /confirm。"
            )
        elif op == "return":
            self._system(f"→ 任务已 RETURNED。下一步 /audit {task_id} 看判定。")

    def _observe_error_face(self, payload: dict[str, Any]) -> str | None:
        """Shared error surface (Slice D ROUND 2 纪律).

        AIPOS-245 R-5: delegate to the module-level surface so the two paths never fork —
        it passes through the gate's own `suggested_next_action` (tools.py:145) verbatim and
        never parallel-authors a reason/fix.
        """
        return _observe_error_face(payload)

    def _cmd_mode(self, name: str | None) -> None:
        if not name:
            self._system(f"Current mode: {self._session.mode}. Usage: /mode [{('|').join(MODES)}].")
            return
        try:
            self._session.set_mode(name)
        except ValueError:
            # AIPOS-245 B14 (P-A): a local, reachable failure (unknown mode) — teach the valid set
            # + the exact next command instead of leaking a bare `Error: {exc}`. Local state, no
            # gate teaching to pass through; we author only the "what to type" (never pre-fill).
            self._system(f"未知模式 “{name}”。→ /mode [{('|').join(MODES)}]。")
            return
        self._render_status()
        self._system(f"Mode → {self._session.mode}.")

    def _cmd_audit(self, task_id: str | None) -> None:
        # Ruling 3: a COMPACT single-line L3 verdict via the read-only authority/records path.
        if not task_id:
            self._system("Usage: /audit <task_id>.")
            return
        try:
            result = self._session.observe("task", {"task_id": task_id})
        except Exception as exc:
            self._system(f"Error: {exc}")
            return
        # AIPOS-242 ROUND 2: check error surface (同病同治).
        err = _observe_error_face(result)
        if err:
            self._pre(f"Error: {err}")
            # AIPOS-245 B8 (P-A): `err` already carries the gate's message (+ suggested_next_action
            # via the R-5 pass-through); the TUI adds only the local next step, not a re-authored reason.
            self._system("↳ 核对 task_id 拼写,或 /queue 看任务在不在队列。")
            return
        data = result.get("data") if isinstance(result, dict) else None
        verdict = "UNKNOWN"
        effective = None
        if isinstance(data, dict):
            verdict = str(data.get("verdict") or data.get("authority_verdict") or data.get("status") or "UNKNOWN")
            for key in ("effective_truth", "effective", "truth"):
                if key in data:
                    effective = data[key]
                    break
        suffix = f" (effective_truth={str(effective).lower()})" if effective is not None else ""
        self._pre(f"{task_id}: {verdict}{suffix}")
        # AIPOS-245 B7 (P-A: 失败判定也要"下一步"). The verdict is a read-face value (NOT a gate
        # error_code / _teaching_error), so there is no gate-authored next step to pass through —
        # this is a pure loop-position overlay. PASS → forward; FAIL/REQUEST_CHANGES → back to the
        # executor. The blocking reason itself lives in L3 (we don't parallel-author it here).
        v = verdict.upper()
        if v in ("FAIL", "REQUEST_CHANGES", "BLOCK"):
            self._system("↳ 审计未通过。看 L3 记录的 blocking 原因,退回执行者修后重走 return→/confirm。")
        elif v in ("PASS", "APPROVE", "APPROVED"):
            self._system("↳ 审计通过。该任务这一环已闭合。")

    # --- AIPOS-226 (Slice 2): local Owner actions (NOT gate, NOT copilot) ----------

    def _cmd_project(self, args: list[str]) -> None:
        # Owner-side local actions (ruling 2=a / AIPOS-230): filesystem / runtime-config only — no
        # gate confirm, no token, never routed through the copilot.
        #   /project                -> list candidate projects + show the active one
        #   /project new <name>     -> scaffold a project under the home (Slice 2)
        #   /project switch <name>  -> set the active project (Slice 6): writes the runtime config
        #                              so the gate resolves it, updates session + rebinds copilot.
        if not args:
            self._cmd_project_list()
            return
        verb = args[0]
        if verb == "new" and len(args) >= 2:
            self._cmd_project_new(args[1])
        elif verb == "switch" and len(args) >= 2:
            self._cmd_project_switch(args[1])
        else:
            self._system("Usage: /project (list) | /project new <name> | /project switch <name>.")

    def _cmd_project_list(self) -> None:
        # AIPOS-242 (F-o3-2): the GATE is the single source of truth for the project view. The old
        # implementation listed the CLIENT's guess (a bare resolve_home_root() reads the client
        # env/defaults — potentially a DIFFERENT home than the gate's; the O3 "no established
        # projects" symptom). No silent client-side fallback: gate view, or an honest error.
        try:
            status = self._session.observe("project_status")
        except Exception as exc:
            self._system(f"Error: could not read the gate's project view: {exc}")
            return
        code = str(status.get("error_code") or "")
        if code == "PROJECT_SCOPE_DENIED":
            gate_active = _gate_active_from_deny(status)
            seen = (
                f"the gate currently resolves '{gate_active}'"
                if gate_active
                else "the gate's active project"
            )
            self._pre(
                f"PROJECT_SCOPE_DENIED — {seen}, which is outside your token's projects.\n"
                f"All gated reads (including this view) are denied until you switch back:\n"
                f"  /project switch <name-in-your-token-scope>"
            )
            return
        if code:
            self._system(f"Error: gate project view failed: {code}: {status.get('message')}")
            return
        gate_active = status.get("active_project")
        projects = [str(p) for p in (status.get("projects") or [])]
        listing = "\n".join(
            f"  {'* ' if p == gate_active else '  '}{p}" for p in projects
        ) or "  (no established projects)"
        active_line = (
            f"Active (gate): {gate_active}"
            if gate_active
            else f"Active (gate): unresolved — {status.get('resolution_error')}"
        )
        session_note = ""
        if self._session.active_project and self._session.active_project != gate_active:
            session_note = (
                f"\nNOTE: this session shows '{self._session.active_project}' — differs from the "
                f"gate. The gate wins; /project switch to realign."
            )
        self._pre(
            f"Projects under {status.get('home_root')} (as resolved by the GATE):\n"
            f"{listing}\n\n{active_line}{session_note}"
        )

    def _cmd_project_new(self, name: str) -> None:
        try:
            home = resolve_home_root()
            root = scaffold_project(home, name)
        except Exception as exc:
            self._system(f"Error: {exc}")
            return
        self._pre(
            f"Created project root: {root}\n"
            f"project.json: {project_json_path(root)}\n"
            f"next: lybra serve with LYBRA_HOME_ROOT={home}"
        )
        self._system("Local Owner scaffold (no gate operation, no token minted).")

    def _cmd_project_switch(self, name: str) -> None:
        # AIPOS-230 §1b + AIPOS-242 (F-o3-18): a local Owner action — write the runtime config,
        # THEN VERIFY against the gate with a gated probe and report what is ACTUALLY true. The old
        # code printed "The gate now resolves '<name>'" without ever asking the gate — under an
        # LYBRA_ACTIVE_PROJECT env pin (resolution order: env > config) the switch silently failed
        # while claiming success. Four outcomes, reported as measured; never optimistic.
        try:
            path = set_active_project(name)
            self._session.set_active_project(name)
        except Exception as exc:
            self._system(f"Error: {exc}")
            return
        if self._copilot is not None and hasattr(self._copilot, "project"):
            self._copilot.project = name
        try:
            status = self._session.observe("project_status")
        except Exception as exc:
            # branch 4: cannot verify -> say so, do NOT claim the gate followed.
            self._pre(
                f"Active project -> {name} (runtime config written: {path})\n"
                f"WARNING: could not VERIFY that the gate followed (probe failed: {exc}).\n"
                f"Not claiming success — re-check with /project once the gate is reachable."
            )
            return
        code = str(status.get("error_code") or "")
        if code == "PROJECT_SCOPE_DENIED":
            gate_active = _gate_active_from_deny(status)
            if gate_active == name:
                # branch 2: the gate FOLLOWED the switch; the token just doesn't cover it — the
                # live-enforcement demo state, still a verified switch.
                self._pre(
                    f"Gate now resolves '{name}' ✓ (verified via gated probe)\n"
                    f"Your token's projects do not include '{name}' → every gated read/write "
                    f"returns PROJECT_SCOPE_DENIED until you switch back."
                )
            else:
                # branch 3: MISMATCH — the gate did NOT follow. Loud, with the likely cause.
                self._pre(
                    f"MISMATCH: runtime config written ({path}) but the gate still resolves "
                    f"'{gate_active or '(unparseable deny)'}', NOT '{name}'.\n"
                    f"Likely cause: the serve process carries an LYBRA_ACTIVE_PROJECT env pin "
                    f"(resolution order: env > config), so config writes can never drive it.\n"
                    f"Fix the serve start environment (drop the pin), then retry."
                )
            return
        if code:
            self._pre(
                f"Runtime config written ({path}) but the verify probe errored: "
                f"{code}: {status.get('message')}\nNot claiming success."
            )
            return
        gate_active = status.get("active_project")
        if gate_active == name:
            # branch 1: verified in-scope switch.
            self._pre(f"Gate now resolves '{name}' ✓ (verified via gated probe)\nruntime config: {path}")
        else:
            # branch 3 (success payload, different active): MISMATCH — loud.
            self._pre(
                f"MISMATCH: runtime config written ({path}) but the gate still resolves "
                f"'{gate_active}', NOT '{name}'.\n"
                f"Likely cause: LYBRA_ACTIVE_PROJECT env pin on the serve process (env > config).\n"
                f"Fix the serve start environment (drop the pin), then retry."
            )
        self._system("Local Owner action + gated verify probe (read-only; no confirm, no token minted).")

    def _cmd_home(self, args: list[str]) -> None:
        # Owner one-shot local git setup. Transparent: prints the exact plan first, then runs it.
        # NEVER configures a remote or pushes; never routed through the copilot.
        if not args or args[0] != "git-init":
            self._system("Usage: /home git-init.")
            return
        try:
            home = resolve_home_root()
            plan = plan_home_git_init(home)
            plan_lines = [f"Home: {plan['home']}", "Planned .gitignore:", plan["gitignore"].rstrip("\n")]
            plan_lines.append("Planned git commands (one-shot, local only — no remote, no push):")
            plan_lines.extend("  " + " ".join(cmd) for cmd in plan["commands"])
            self._pre("\n".join(plan_lines))
            result = execute_home_git_init(home)
        except Exception as exc:
            self._system(f"Error: {exc}")
            return
        out = ["Ran:"]
        out.extend("  " + ran for ran in result["ran"])
        out.append("Push hint (Owner runs this — Lybra never pushes):")
        out.extend("  " + hint for hint in result["push_hint"])
        self._pre("\n".join(out))

    def _cmd_compact(self) -> None:
        # AIPOS-222 ruling: compress the current context NOW. Calls the EXISTING
        # CopilotMemory.compact(keep_last=…) — which trims L3 CHAT ONLY and never touches the L0
        # truth snapshot / L1 index — then shows the compaction notice + refreshes Ctx%. Read-only,
        # chat-only: no gate write, no file write, no truth mutation.
        memory = getattr(self._copilot, "memory", None)
        if memory is None:
            self._system("Nothing to compact — the copilot is not enabled.")
            return
        from tools.lybra_tui.copilot import CHAT_KEEP_LAST

        memory.compact(keep_last=CHAT_KEEP_LAST)  # L3 chat only; L0/L1 truth untouched by construction
        self._system(_COMPACTION_NOTICE)
        self._render_ctxbar()

    # --- NL conversation → consent → card (each step OFF the event loop, in a worker) ---

    def _copilot_ready(self) -> bool:
        # Shared guard for any copilot-backed action (chat or draft). Renders the reason and
        # returns False when the copilot is unavailable, so the UI never silently no-ops.
        if self._session.mode != COPILOT_MODE:
            self._system("Not in copilot mode. Use `/mode copilot` (or Shift+Tab) to enable planning.")
            return False
        if self._copilot is None:
            self._system(
                "Copilot not enabled. Start the TUI with an LLM config (base_url + key) "
                "to enable read-only planning."
            )
            return False
        return True

    def _submit_intent(self, intent: str) -> None:
        # AIPOS-222: an NL line is a CONVERSATIONAL turn (NOT an instant card). Show the user
        # turn + an inline "thinking" line IMMEDIATELY, then run the SYNC, read-only copilot
        # `chat()` OFF the event loop in a thread worker so the UI never freezes (the 221 fix).
        self._user(intent)
        if not self._copilot_ready():
            return
        self._start_thinking(intent=intent)
        self._chat_worker(intent)

    def _consent_to_draft(self) -> None:
        # AIPOS-222 consent step: `/draft` or an affirmative reply turns the accumulated
        # conversation into a conformant task-card draft. Reuses the unchanged `draft_task_card`
        # (which pulls memory.l3_chat context). Clears the pending offer.
        self._pending_offer = False
        if not self._copilot_ready():
            return
        self._system("Generating a project-init draft from the conversation…")
        self._start_thinking()
        self._draft_worker("")

    # --- inline "thinking" indicator (Owner ruling 2: honest, no fabricated effort) ---

    def _start_thinking(self, *, intent: str = "") -> None:
        # AIPOS-222 ruling 2/fix 5: a line directly under the Owner's turn — a PULSING green marker
        # AND the word "Thinking…" both blink together (claude-code `✽ Thinking…` style), with a
        # real elapsed counter and an honest token field:
        #   ✽ Thinking… (3m 11s · ↑ ~2.4k tokens)
        # The ↑ (egress / prompt) count is shown DURING thinking as a char-based ESTIMATE (marked
        # `~`) since the real prompt-token count is only known once the provider responds; the ↓
        # (answer) count is unknown until the result arrives, so it is omitted until then. The
        # wording is an honest verb (no invented "effort"). The prompt is disabled while the worker
        # runs; the live line is removed when the result arrives (the final ↑/↓ line is rendered by
        # _finish_thinking).
        self._stop_thinking()
        self._thinking_seconds = 0
        self._thinking_dot_on = True
        # Estimate the egress (↑) tokens from what chat() actually sends: truth snapshot (capped) +
        # accumulated chat + system prompt + this intent. ≈ chars/4. Shown with a `~` (estimate).
        self._thinking_up_est = self._estimate_up_tokens(intent)
        convo = self.query_one("#conversation", VerticalScroll)
        line = Static(self._thinking_text(), classes="turn turn-system")
        convo.mount(line)  # AIPOS-246 S1/R-3: (lazily) anchored container follows; thinking NEVER forces scroll
        self.call_after_refresh(self._maybe_anchor)
        self._thinking = line
        self._thinking_timer = self.set_interval(0.5, self._tick_thinking)
        self._prompt().disabled = True

    @staticmethod
    def _fmt_elapsed(seconds: int) -> str:
        # Whole-second elapsed, claude-code style: "Ns" under a minute, else "Nm Ns".
        if seconds < 60:
            return f"{seconds}s"
        return f"{seconds // 60}m {seconds % 60}s"

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        # Compact token count: 2.4k over 1000, else the raw integer.
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _estimate_up_tokens(self, intent: str) -> int:
        # Honest char-based estimate (~chars/4) of the egress payload chat() will send. Mirrors
        # _ctx_percent's accounting (capped truth snapshot + L3 chat + system prompt) plus this
        # turn's intent. Used ONLY as a `~`-marked estimate while the real usage is unknown.
        memory = getattr(self._copilot, "memory", None)
        truth_chars = chat_chars = 0
        if memory is not None:
            truth = getattr(memory, "l0_truth", {}) or {}
            try:
                truth_chars = min(len(json.dumps(truth, sort_keys=True)), _TRUTH_CAP_CHARS)
            except (TypeError, ValueError):
                truth_chars = 0
            chat_chars = sum(len(getattr(t, "content", "") or "") for t in getattr(memory, "l3_chat", []))
        try:
            from tools.lybra_tui.copilot import _CHAT_SYSTEM_PROMPT
            system_chars = len(_CHAT_SYSTEM_PROMPT)
        except Exception:
            system_chars = 0
        return max(0, (truth_chars + chat_chars + system_chars + len(intent)) // 4)

    def _thinking_text(self) -> str:
        # The marker AND the word both pulse (bright/dim green) so "Thinking…" blinks with the ✽.
        # The token field shows the elapsed time and the ↑ estimate (`~`); ↓ is unknown until done.
        style = f"{LYBRA_GREEN} bold" if self._thinking_dot_on else "$text-muted"
        elapsed = self._fmt_elapsed(self._thinking_seconds)
        field = f"{elapsed} · ↑ ~{self._fmt_tokens(self._thinking_up_est)} tokens"
        return f"[{style}]✽ Thinking…[/] ({field})"

    def _tick_thinking(self) -> None:
        # 0.5s tick: toggle the pulse every tick; advance the seconds counter on whole seconds.
        self._thinking_dot_on = not self._thinking_dot_on
        if self._thinking_dot_on:
            self._thinking_seconds += 1
        if self._thinking is not None:
            self._thinking.update(self._thinking_text())

    def _finish_thinking(self, usage: Any) -> None:
        # On result: render a final, honest token summary line, then clear the live thinking line.
        #   ✽ Thinking… (Tm Ts · ↑ X · ↓ Y tokens)
        # Prefers REAL usage from the LLM response (ChatReply.usage). If the provider omitted usage
        # (or there is none — e.g. a draft path), falls back to a char-based ESTIMATE marked `~`
        # (↑ from the egress estimate, ↓ from the answer length) — never an unlabeled fake number.
        elapsed = self._fmt_elapsed(self._thinking_seconds)
        up_real = getattr(usage, "prompt_tokens", None) if usage is not None else None
        down_real = getattr(usage, "completion_tokens", None) if usage is not None else None
        if up_real is not None:
            up = f"↑ {self._fmt_tokens(int(up_real))}"
        else:
            up = f"↑ ~{self._fmt_tokens(self._thinking_up_est)}"
        if down_real is not None:
            down = f"↓ {self._fmt_tokens(int(down_real))}"
        else:
            down = f"↓ ~{self._fmt_tokens(self._thinking_down_est)}"
        self._system(f"✽ Thinking… ({elapsed} · {up} · {down} tokens)")

    def _stop_thinking(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer = None
        if self._thinking is not None:
            self._thinking.remove()
            self._thinking = None
        self._prompt().disabled = False

    @work(thread=True, exclusive=True, group="copilot")
    def _chat_worker(self, intent: str) -> None:
        # Worker THREAD: the SYNC, read-only copilot.chat() (rehydrate truth → LLM → record turn,
        # ZERO file write). Posts DATA (ChatReply) back to the loop via a Message. Never blocks UI.
        try:
            reply = self._copilot.chat(intent)
            self.post_message(CopilotResult(kind="chat", reply=reply))
        except Exception as exc:  # network/LLM errors come back as a system line, not a crash
            self.post_message(CopilotResult(kind="chat", error=str(exc)))

    @work(thread=True, exclusive=True, group="copilot")
    def _draft_worker(self, intent: str) -> None:
        # Worker THREAD: the SYNC, unchanged copilot.draft_task_card() (read-only: returns card
        # DATA, writes nothing). Posts the DATA result back to the loop via a Message.
        try:
            proposal = self._copilot.draft_task_card(intent)
            self.post_message(CopilotResult(kind="draft", proposal=proposal))
        except Exception as exc:
            self.post_message(CopilotResult(kind="draft", error=str(exc)))

    def on_copilot_result(self, message: CopilotResult) -> None:
        # Back on the event loop: render the final token summary, clear the inline thinking line,
        # then render the chat answer or the draft (or the error). Branches on the worker kind.
        if message.error is not None:
            self._stop_thinking()
            self._system(f"Copilot error: {message.error}")
            # AIPOS-245 F-245-o3-6 (P-A, Scope B 缺口): copilot-side failures (LLM endpoint — read
            # timeout / connection / HTTP errors) used to end at the bare error. Add ONLY the loop
            # guidance; the raw error above stays the single truth of WHAT failed. NO retry logic —
            # behavior unchanged (presentation-only red line).
            self._system("↳ 端点超时/失败(已知中转慢)→ 稍后重试;持续失败检查 --llm-base-url 端点与 key。")
            self.set_focus(self._prompt())
            return
        # Honest final token line (fix 5): REAL ↑/↓ from ChatReply.usage when present, else a
        # `~`-marked char estimate. The ↓ estimate is the answer length (≈chars/4).
        usage = getattr(message.reply, "usage", None) if message.kind == "chat" else None
        answer = ""
        if message.kind == "chat" and message.reply is not None:
            answer = getattr(message.reply, "content", "") or ""
        elif message.proposal is not None:
            answer = getattr(message.proposal, "content", "") or ""
        self._thinking_down_est = max(0, len(answer) // 4)
        self._finish_thinking(usage)
        self._stop_thinking()
        if message.kind == "chat":
            self._render_chat(message.reply)
        else:
            self._render_draft(message.proposal)
        # AIPOS-222 ruling 4: the egress payload changed this turn → refresh the Ctx% estimate.
        self._render_ctxbar()
        # Return focus to the chat prompt so the Owner can keep typing (mount/scroll_visible can
        # move focus to the conversation pane). Keeps the conversational flow uninterrupted.
        self.set_focus(self._prompt())

    def _render_chat(self, reply: Any) -> None:
        # Render the NL answer, then offer the draft step. The offer arms `_pending_offer` so an
        # affirmative reply ("yes"/是/好/可以) consents; `/draft` also always works.
        self._copilot_msg(reply.content)
        if getattr(reply, "compacted", False):
            self._system(_COMPACTION_NOTICE)
        self._pending_offer = True
        self._offer(_DRAFT_OFFER)

    def _render_draft(self, proposal: Any) -> None:
        self._pending_proposal = proposal
        self._copilot_msg(self._render_proposal(proposal))
        if proposal.conformant:
            self._system("✓ Conformant. " + _NEXT_AFTER_DRAFT)
        elif proposal.needs_bundle:
            bundles = ", ".join(self._copilot.available_context_bundles()) or "(none found)"
            self._system(
                f"Needs a context_bundle. Existing: {bundles}. "
                "Run `/proceed bundle=<ref>` to specify one (Owner)."
            )
        else:
            self._system("Not publishable yet — see the blocking reasons above; fix the intent and resubmit.")

    @staticmethod
    def _card_markdown(content: str) -> str:
        """AIPOS-245 F-245-o3-5: fence the card's YAML frontmatter before Markdown rendering.

        A task card is YAML frontmatter + prose. Feeding the frontmatter to the Markdown widget
        mangles it (`task_id`/`assigned_to` underscores parse as emphasis → a run-together blob,
        Owner O3 screenshot). Pure presentation fix: the frontmatter block (--- … ---) is wrapped
        in a ```yaml fence so it renders line-by-line, aligned, unparsed; only the prose body
        (task description) stays Markdown. Content bytes are NOT modified — this only wraps.
        """
        if content.startswith("---\n") or content.startswith("---\r\n"):
            end = content.find("\n---", 3)
            if end != -1:
                head_end = end + len("\n---")
                frontmatter = content[:head_end]
                body = content[head_end:]
                return f"```yaml\n{frontmatter}\n```{body}"
        return content

    def _render_proposal(self, p: Any) -> str:
        head = (
            f"TASK CARD DRAFT (read-only, not yet landed) — task_id {p.task_id}\n\n"
            f"{self._card_markdown(p.content)}"
        )
        if p.conformant:
            return head
        if p.needs_bundle:
            return head + "\n\n⚠ no matching context_bundle (see next step)."
        return head + "\n\n⚠ blocking reasons:\n- " + "\n- ".join(p.blocking_reasons)

    # --- /proceed: land the draft + stage a publish DRY-RUN (NEVER publishes) -----

    def _copilot_proceed(self, arg: str | None) -> None:
        # AIPOS-221 RED LINE: /proceed lands a draft under 5_tasks/drafts/ and stages a publish
        # DRY-RUN only. It NEVER publishes — the TUI holds no owner token; the Owner confirms
        # out of band. (This mirrors the welded AIPOS-206 owner-OOB confirm boundary.)
        if self._pending_proposal is None:
            # AIPOS-245 B10 (P-A 微调): make WHERE explicit — a card is born in copilot mode.
            self._system("No pending card. Type a task in plain language first(copilot 模式下说需求即可生成草稿)。")
            return
        if not self._workspace_root:
            # AIPOS-245 B11 (P-A): local presentation state (no gate error_code) — name the exact restart flag.
            self._system("workspace_root unknown; cannot land card. → 重启 lybra tui 时带 --workspace-root <path>。")
            return
        bundle = arg[len("bundle="):] if arg and arg.startswith("bundle=") else None
        proposal = self._pending_proposal
        if bundle:
            proposal = self._copilot.finalize_card(proposal, context_bundle=bundle)
            self._pending_proposal = proposal
        if not proposal.conformant:
            self._pre("Card not publishable yet:\n- " + "\n- ".join(proposal.blocking_reasons))
            self._system("Resolve the blocking reasons (e.g. `/proceed bundle=<ref>`) and try again.")
            return
        rel = self._session.land_draft(
            proposal.content, workspace_root=self._workspace_root, draft_rel_path=proposal.draft_rel_path,
        )
        # preview_publish == publish DRY-RUN. No confirm/publish is ever called here.
        preview = self._session.preview_publish(rel, actor="owner")
        self._pending_preview = preview
        self._pending_proposal = None
        self._pre(f"✓ Landed {rel}.")
        self._system(
            f"Publish dry-run staged ({preview.dry_run_token}) — NOT published. "
            "Confirm out of band with the owner token to publish."
        )
        # AIPOS-245 A5 (P-A: make the "next step" explicit). The TUI holds no publish authority.
        self._system("→ 用 owner token OOB 确认发布(TUI 不持发布权);发布后 agent 才能认领。")


def build_app(
    session: TuiSession,
    copilot_session: Any = None,
    *,
    workspace_root: str | None = None,
    mouse: bool = False,
) -> LybraTui:
    # Signature must mirror run_tui's call site: build_app(session, copilot, workspace_root=...,
    # mouse=...). (Drift here crashed `lybra tui` at launch — AIPOS-216; guarded by the
    # run_tui→build_app smoke + the AIPOS-247 mouse wiring pins.)
    return LybraTui(session, copilot_session, workspace_root=workspace_root, mouse=mouse)
