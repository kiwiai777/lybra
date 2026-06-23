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

from tools.lybra_tui.state import TuiSession


class LybraTui(App):
    """Owner console: observe gate state + modal confirm. Non-daemon, pull-on-demand."""

    CSS = "Screen { layout: vertical; } #body { height: 1fr; }"
    BINDINGS = [
        Binding("shift+tab", "toggle_mode", "Mode"),
        Binding("/", "command_palette", "/-menu"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "cancel", "Cancel/Reject"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, session: TuiSession) -> None:
        super().__init__()
        self._session = session
        self._pending_preview: Any = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(Static(id="status"), Static(id="body"), id="main")
        yield Input(placeholder="/-menu: queue | validate | confirm | publish <draft> | <Esc> cancel", id="cmd")
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
            else:
                self._show(f"Unknown command: {cmd}")
        except Exception as exc:  # surface gate errors without crashing the console
            self._show(f"Error: {exc}")


def build_app(session: TuiSession) -> LybraTui:
    return LybraTui(session)
