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
from typing import Any

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Header, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from tools.aipos_cli.home_git import execute_home_git_init, plan_home_git_init
from tools.aipos_cli.workspace_config import (
    project_json_path,
    resolve_home_root,
    scaffold_project,
)
from tools.lybra_tui.presentation import LYBRA_GREEN, banner, color_enabled
from tools.lybra_tui.state import COPILOT_MODE, MODES, TuiSession

# --- the /command set (Owner ruling 1: `/gates`, not `/confirm`) -------------------
# (command, one-line description). Order is the /help + autocomplete order.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "list all commands + descriptions"),
    ("/draft", "turn the conversation so far into a project-init task-card draft (read-only)"),
    ("/proceed", "land the pending card under 5_tasks/drafts/ + stage a publish dry-run (never publishes; Owner confirms OOB)"),
    ("/queue", "observe the queue summary (read-only)"),
    ("/validate", "run the validator (read-only)"),
    ("/gates", "list pending confirm gates (read-only view; confirm is OOB)"),
    ("/mode", "switch mode: /mode [observe|confirm|copilot]"),
    ("/audit", "compact L3 authority verdict for a task: /audit [task_id] (read-only)"),
    ("/project", "Owner: scaffold a project under the home: /project new <name> (local, not a gate op)"),
    ("/home", "Owner: one-shot local git init of the home: /home git-init (no remote, no push)"),
    ("/compact", "compress the current chat context now (read-only; trims chat only, never truth)"),
    ("/clear", "clear the conversation area"),
    ("/quit", "quit (also /exit, q, Ctrl+C)"),
    ("/exit", "quit (alias of /quit)"),
)
_COMMAND_NAMES = tuple(name for name, _ in COMMANDS)
_NEXT_AFTER_DRAFT = (
    "Next: review the card, then `/proceed` to land it + stage the publish dry-run "
    "(the Owner confirms out of band)."
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
    #brandbar {{ height: auto; padding: 0; margin: 0; }}
    #banner {{ height: auto; padding: 0; margin: 0; }}
    #conversation {{ height: 1fr; padding: 0 1; }}
    .turn {{ margin: 0; }}
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

    def __init__(self, session: TuiSession, copilot_session: Any = None, *, workspace_root: str | None = None) -> None:
        super().__init__()
        self._session = session
        # AIPOS-206: optional read-only planning copilot (CopilotSession). When absent,
        # NL input explains how to enable it; the owner session is unaffected.
        self._copilot = copilot_session
        self._workspace_root = workspace_root
        self._pending_preview: Any = None
        self._pending_proposal: Any = None  # last copilot DraftProposal awaiting Owner /proceed
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
        yield Vertical(Static(id="banner"), id="brandbar")
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
        banner_widget = self.query_one("#banner", Static)
        raw = banner(self.size.width)
        if color_enabled():
            banner_widget.update(_banner_markup(raw))
        else:
            banner_widget.update(raw)
        # AIPOS-221/222 (ruling 4): the chat prompt is a multi-line TextArea that accepts CJK /
        # wide chars natively (no `restrict`/`type` filter exists on a TextArea to drop them). The
        # `/` OptionList dropdown is the primary command autocomplete (driven by TextArea.Changed).
        cmd = self.query_one("#cmd", PromptArea)
        # Short, single-line placeholder (claude-code style): a long one can soft-wrap to a 2nd
        # line in a narrow pane, inflating the input box. The full hint is in the welcome line.
        cmd.placeholder = "Type a task, or /help"
        self.query_one("#ac", OptionList).display = False
        self._render_status()
        self._render_ctxbar()
        self.set_focus(cmd)
        self._system(
            "Connected. Type what you want to do in plain language and press Enter; "
            "or start with `/` for a command (try `/help`)."
        )

    # --- conversation transcript (codex/claude-code style message stream) ---------

    def _append(self, text: str, css_class: str) -> None:
        convo = self.query_one("#conversation", VerticalScroll)
        widget = Static(text, classes=f"turn {css_class}")
        convo.mount(widget)
        widget.scroll_visible()

    def _markdown(self, text: str, css_class: str) -> None:
        # AIPOS-222 (fix 4): render copilot answers + task-card content as Textual `Markdown` so
        # fenced ```code``` blocks are syntax-highlighted (Rich/Pygments — ships with Textual, no
        # new dependency), exactly like codex/claude-code. Pure display; no accountability state.
        convo = self.query_one("#conversation", VerticalScroll)
        widget = Markdown(text, classes=f"turn {css_class}")
        convo.mount(widget)
        widget.scroll_visible()

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
        if not text:
            return
        # Record the submitted line in the in-memory history and reset the browse cursor.
        self._history.append(text)
        self._history_index = None
        self._history_draft = ""
        if text.startswith("/"):
            self._handle_command(text)
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
                self._pre(json.dumps(self._session.observe("queue").get("data", {}).get("summary", {}), indent=2))
                self._system("Read-only queue summary (no truth changed).")
            elif cmd == "/validate":
                self._pre(json.dumps(self._session.observe("validate").get("summary", {}), indent=2))
                self._system("Read-only validator run (no truth changed).")
            elif cmd == "/gates":
                self._cmd_gates()
            elif cmd == "/mode":
                self._cmd_mode(args[0] if args else None)
            elif cmd == "/audit":
                self._cmd_audit(args[0] if args else None)
            elif cmd == "/project":
                self._cmd_project(args)
            elif cmd == "/home":
                self._cmd_home(args)
            elif cmd == "/clear":
                self.query_one("#conversation", VerticalScroll).remove_children()
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
        self._pre("\n".join(lines))

    def _cmd_gates(self) -> None:
        # Ruling 1: `/gates` is a READ-ONLY VIEW of pending confirm gates — not an action.
        gates = self._session.confirm_gates()
        if not gates:
            self._pre("No pending confirm gates.")
        else:
            self._pre(
                "Pending confirm gates (read-only view):\n"
                + "\n".join(f"  [{i}] {g['op']} {g['task_id']}" for i, g in enumerate(gates))
            )
        self._system("These are confirmed OUT OF BAND by the Owner with the owner token — the TUI never confirms.")

    def _cmd_mode(self, name: str | None) -> None:
        if not name:
            self._system(f"Current mode: {self._session.mode}. Usage: /mode [{('|').join(MODES)}].")
            return
        self._session.set_mode(name)  # raises ValueError on unknown → caught by _handle_command
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

    # --- AIPOS-226 (Slice 2): local Owner actions (NOT gate, NOT copilot) ----------

    def _cmd_project(self, args: list[str]) -> None:
        # Owner scaffold (ruling 2=a): a local filesystem action — no gate confirm, no token,
        # never routed through the copilot. Only `/project new <name>` is wired in the TUI.
        if not args or args[0] != "new" or len(args) < 2:
            self._system("Usage: /project new <name>.")
            return
        name = args[1]
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
        convo.mount(line)
        line.scroll_visible()
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

    def _render_proposal(self, p: Any) -> str:
        head = f"TASK CARD DRAFT (read-only, not yet landed) — task_id {p.task_id}\n\n{p.content}"
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
            self._system("No pending card. Type a task in plain language first.")
            return
        if not self._workspace_root:
            self._system("workspace_root unknown; cannot land card.")
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


def build_app(
    session: TuiSession, copilot_session: Any = None, *, workspace_root: str | None = None
) -> LybraTui:
    # Signature must mirror run_tui's call site: build_app(session, copilot, workspace_root=...).
    # (Drift here crashed `lybra tui` at launch — AIPOS-216; guarded by the run_tui→build_app smoke.)
    return LybraTui(session, copilot_session, workspace_root=workspace_root)
