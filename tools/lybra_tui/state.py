"""AIPOS-205 — TUI session state (pure logic, no Textual).

The Lybra TUI is a thin client over an Owner-started gate. All gate I/O goes through the
AIPOS-203 GateClient (JSON-RPC tools/call only); state is read via gate read-tools, never
by reading files. This module holds the testable core — connect, status line, observe via
read-tools, and the confirm-panel orchestration — so the Textual layer (app.py) stays a
thin renderer. It imports NO Textual, so it runs in the gate/core CI lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.aipos_cli.confirm_client import (
    GateClient,
    Preview,
    claim_args_from_task,
    load_owner_token,
    return_args_from_task,
    token_fingerprint,
)

WRITE_SCOPES = ("queue_claim", "queue_return", "owner_confirm", "draft_publish", "audit_dispatch", "audit_verdict")
OBSERVE_MODE = "observe"
CONFIRM_MODE = "confirm"
COPILOT_MODE = "copilot"
# AIPOS-206 (DG-11): Shift+Tab cycles observe -> confirm -> copilot. The copilot mode is
# a read-only planning advisor (see tools.lybra_tui.copilot); it never confirms/publishes.
MODES = (OBSERVE_MODE, CONFIRM_MODE, COPILOT_MODE)


@dataclass
class TuiSession:
    """Owner-side TUI session over a gate. Thin wrapper around GateClient."""

    gate_url: str
    _client: GateClient
    scope_basis: dict[str, Any] = field(default_factory=dict)
    mode: str = OBSERVE_MODE
    active_project: str | None = None

    @classmethod
    def connect(
        cls,
        gate_url: str,
        *,
        connection_json: str | None = None,
        token_env: str | None = None,
        role: str = "owner",
    ) -> "TuiSession":
        token = load_owner_token(connection_json=connection_json, role=role, token_env=token_env)
        client = GateClient(gate_url, token)
        client.initialize()
        session = cls(gate_url=gate_url, _client=client)
        session.refresh_scope()
        return session

    @property
    def client(self) -> GateClient:
        return self._client

    @property
    def token_fingerprint(self) -> str:
        return self._client.token_fingerprint

    def refresh_scope(self) -> dict[str, Any]:
        # The gate echoes the connection's scope_basis (service mode) on any tool result.
        structured = self._client.call_tool("lybra_queue_list", {})
        basis = structured.get("scope_basis")
        self.scope_basis = basis if isinstance(basis, dict) else {}
        return self.scope_basis

    @property
    def scopes(self) -> list[str]:
        scopes = self.scope_basis.get("scopes")
        return [str(s) for s in scopes] if isinstance(scopes, list) else []

    @property
    def has_owner_confirm(self) -> bool:
        return "owner_confirm" in self.scopes

    def toggle_mode(self) -> str:
        # Shift+Tab cycles observe -> confirm -> copilot (AIPOS-206).
        idx = MODES.index(self.mode) if self.mode in MODES else 0
        self.mode = MODES[(idx + 1) % len(MODES)]
        return self.mode

    def set_mode(self, name: str) -> str:
        # AIPOS-221: explicit mode set for `/mode [observe|confirm|copilot]` (no Shift+Tab
        # dependency). Pure client-state only — no scope/accountability change. Raises on an
        # unknown name so the TUI can surface a clear error rather than silently no-op.
        if name not in MODES:
            raise ValueError(f"unknown mode {name!r}; choose one of {', '.join(MODES)}")
        self.mode = name
        return self.mode

    def set_active_project(self, name: str) -> str:
        # AIPOS-230 §2: client-side active-project state, mirroring set_mode. Pure display/context;
        # the gate-side switch (which makes enforcement see the new project) is the app Owner action
        # that writes the runtime config — this only records what the TUI shows / where DRAFTs land.
        project = str(name or "").strip()
        if not project:
            raise ValueError("active project name must be non-empty")
        self.active_project = project
        return self.active_project

    def status_line(self) -> str:
        role = self.scope_basis.get("role") or "?"
        scope_text = ",".join(self.scopes) or "unknown"
        oc = "yes" if self.has_owner_confirm else "no"
        project_text = self.active_project or "—"
        return (
            f"gate {self.gate_url} · token {self.token_fingerprint} · role {role} · "
            f"scopes [{scope_text}] · owner_confirm {oc} · project {project_text} · read-only-view · mode {self.mode}"
        )

    # --- observe: state via gate read-tool, never direct file reads ---

    def observe(self, view: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        tool = {
            "queue": "lybra_queue_list",
            "task": "lybra_task_preview",
            "validate": "lybra_validate",
            "context_pack": "lybra_context_pack_build",
            "project_status": "lybra_project_status",  # AIPOS-242: the gate's own project view
        }.get(view)
        if tool is None:
            raise ValueError(f"unknown observe view {view!r}")
        return self._client.call_tool(tool, arguments or {})

    # --- confirm panel: delegates to GateClient.preview/confirm only ---

    def confirm_gates(self) -> list[dict[str, Any]]:
        return self._client.list_confirm_gates()

    def preview_gate(self, gate: dict[str, Any], *, owner_policy_ref: str = "owner_policy:supervised", result_summary: str = "owner-confirmed return") -> Preview:
        op = gate["op"]
        if op == "claim":
            args = claim_args_from_task(gate["task"], owner_policy_ref=owner_policy_ref)
        elif op == "return":
            args = return_args_from_task(gate["task"], result_summary=result_summary)
        else:
            raise ValueError(f"unsupported gate op {op!r}")
        return self._client.preview(op, args)

    def preview_publish(self, draft_path: str, *, actor: str = "owner") -> Preview:
        return self._client.preview("publish", {"path": draft_path, "actor": actor})

    # --- AIPOS-206 Owner "proceed": land a copilot DRAFT, then publish via the gate ---
    #
    # This is the OWNER/TUI proceed action, NOT the copilot. The copilot loop only ever
    # returns DRAFT data (tools.lybra_tui.copilot.DraftProposal); materializing it to a
    # file and feeding the AIPOS-204 publish gate happens here, on the owner session, in
    # one Owner action. The copilot credential can never reach this code (no write scope,
    # no file write path on its side).
    def land_draft(self, content: str, *, workspace_root: str, draft_rel_path: str) -> str:
        """Write the DRAFT to drafts/ under the workspace (Owner action). Returns the path."""
        if not draft_rel_path.startswith("5_tasks/drafts/"):
            raise ValueError("a copilot DRAFT must land under 5_tasks/drafts/ (per-project, R4)")
        path = Path(workspace_root).expanduser().resolve() / draft_rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return draft_rel_path

    def confirm(self, preview: Preview, owner_literal: str) -> dict[str, Any]:
        # Esc / empty literal = reject (the caller passes "" to cancel; never auto-supply).
        if not owner_literal:
            return {"ok": False, "verdict": "BLOCK", "error_code": "CANCELLED", "message": "Owner did not confirm."}
        return self._client.confirm(preview, owner_literal)


__all__ = ["TuiSession", "OBSERVE_MODE", "CONFIRM_MODE", "COPILOT_MODE", "MODES", "WRITE_SCOPES", "token_fingerprint"]
