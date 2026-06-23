"""AIPOS-205 — Textual rendering layer (the ONLY module importing Textual).

Thin renderer over tools.lybra_tui.state.TuiSession. All logic lives in state.py (pure,
testable in the core lane); this file only wires Textual widgets/keys to that logic. It is
exercised in the `tui` CI lane (textual installed); the core lane never imports it.
"""

from __future__ import annotations

import json
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Static

from tools.lybra_tui.state import COPILOT_MODE, TuiSession


class LybraTui(App):
    """Owner console: observe gate state + modal confirm + read-only copilot. Non-daemon."""

    CSS = "Screen { layout: vertical; } #body { height: 1fr; }"
    BINDINGS = [
        Binding("shift+tab", "toggle_mode", "Mode"),
        Binding("/", "command_palette", "/-menu"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "cancel", "Cancel/Reject"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, session: TuiSession, copilot_session: Any = None, *, workspace_root: str | None = None) -> None:
        super().__init__()
        self._session = session
        # AIPOS-206: optional read-only planning copilot (CopilotSession). When absent,
        # copilot mode explains how to enable it; the owner session is unaffected.
        self._copilot = copilot_session
        self._workspace_root = workspace_root
        self._pending_preview: Any = None
        self._pending_proposal: Any = None  # last copilot DraftProposal awaiting Owner proceed

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(Static(id="status"), Static(id="body"), id="main")
        yield Input(placeholder="/-menu: queue | validate | confirm | publish <draft> | draft <intent> | proceed | <Esc>", id="cmd")
        yield Footer()

    def on_mount(self) -> None:
        self._render_status()
        self._show("Connected. Pull-on-demand: type a /-menu command (queue, validate, confirm).")

    def _render_status(self) -> None:
        self.query_one("#status", Static).update(self._session.status_line())

    def _show(self, text: str) -> None:
        self.query_one("#body", Static).update(text)

    def action_toggle_mode(self) -> None:
        self._session.toggle_mode()
        self._render_status()

    def action_refresh(self) -> None:
        self._session.refresh_scope()
        self._render_status()
        self._show("Refreshed (pull-on-demand).")

    def action_cancel(self) -> None:
        # Esc = reject / do not submit (never a default-yes).
        self._pending_preview = None
        self._show("Cancelled. Nothing submitted.")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        parts = event.value.strip().split()
        event.input.value = ""
        if not parts:
            return
        cmd = parts[0]
        try:
            if cmd == "queue":
                self._show(json.dumps(self._session.observe("queue").get("data", {}).get("summary", {}), indent=2))
            elif cmd == "validate":
                self._show(json.dumps(self._session.observe("validate").get("summary", {}), indent=2))
            elif cmd == "confirm":
                gates = self._session.confirm_gates()
                self._show("Confirm gates:\n" + "\n".join(f"[{i}] {g['op']} {g['task_id']}" for i, g in enumerate(gates)))
            elif cmd == "draft":
                self._copilot_draft(" ".join(parts[1:]))
            elif cmd == "proceed":
                self._copilot_proceed(parts[1] if len(parts) > 1 else None)
            else:
                self._show(f"Unknown command: {cmd}")
        except Exception as exc:  # surface gate errors without crashing the console
            self._show(f"Error: {exc}")

    # --- AIPOS-206 copilot mode: read-only draft, then Owner "proceed" lands + publishes ---

    def _copilot_draft(self, intent: str) -> None:
        if self._session.mode != COPILOT_MODE:
            self._show("Switch to copilot mode (Shift+Tab) first.")
            return
        if self._copilot is None:
            self._show("Copilot not enabled. Start the TUI with an LLM config (base_url + key) to enable read-only planning.")
            return
        if not intent:
            self._show("Usage: draft <what you want planned>")
            return
        proposal = self._copilot.draft(intent)  # read-only: returns DRAFT data, writes nothing
        self._pending_proposal = proposal
        self._show(f"DRAFT (read-only, not yet landed):\n\n{proposal.content}\n\n— `proceed <slug>` to land under drafts/ and publish via the gate (Owner action).")

    def _copilot_proceed(self, slug: str | None) -> None:
        # The Owner "proceed" action: land the DRAFT, then publish through the AIPOS-204
        # gate — on the OWNER session, not the copilot. Zero extra Owner steps.
        if self._pending_proposal is None:
            self._show("No pending DRAFT. Use `draft <intent>` first.")
            return
        if not self._workspace_root:
            self._show("workspace_root unknown; cannot land DRAFT.")
            return
        slug = slug or "copilot-draft"
        rel = self._session.land_draft(
            self._pending_proposal.content,
            workspace_root=self._workspace_root,
            draft_rel_path=f"5_tasks/drafts/{slug}.md",
        )
        preview = self._session.preview_publish(rel, actor="owner")
        self._pending_preview = preview
        self._pending_proposal = None
        self._show(f"Landed {rel}. Publish dry-run {preview.dry_run_token} ready — confirm with the owner literal to publish.")


def build_app(session: TuiSession) -> LybraTui:
    return LybraTui(session)
