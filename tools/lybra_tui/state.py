"""AIPOS-205 — TUI session state (pure logic, no Textual).

The Lybra TUI is a thin client over an Owner-started gate. All gate I/O goes through the
AIPOS-203 GateClient (JSON-RPC tools/call only); state is read via gate read-tools, never
by reading files. This module holds the testable core — connect, status line, observe via
read-tools, and the confirm-panel orchestration — so the Textual layer (app.py) stays a
thin renderer. It imports NO Textual, so it runs in the gate/core CI lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
MODES = (OBSERVE_MODE, CONFIRM_MODE)  # copilot mode is added by the DG-11 slice, not here


@dataclass
class TuiSession:
    """Owner-side TUI session over a gate. Thin wrapper around GateClient."""

    gate_url: str
    _client: GateClient
    scope_basis: dict[str, Any] = field(default_factory=dict)
    mode: str = OBSERVE_MODE

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
        # Shift+Tab cycles observe <-> confirm (copilot mode reserved for the DG-11 slice).
        idx = MODES.index(self.mode) if self.mode in MODES else 0
        self.mode = MODES[(idx + 1) % len(MODES)]
        return self.mode

    def status_line(self) -> str:
        role = self.scope_basis.get("role") or "?"
        scope_text = ",".join(self.scopes) or "unknown"
        oc = "yes" if self.has_owner_confirm else "no"
        return (
            f"gate {self.gate_url} · token {self.token_fingerprint} · role {role} · "
            f"scopes [{scope_text}] · owner_confirm {oc} · read-only-view · mode {self.mode}"
        )

    # --- observe: state via gate read-tool, never direct file reads ---

    def observe(self, view: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        tool = {
            "queue": "lybra_queue_list",
            "task": "lybra_task_preview",
            "validate": "lybra_validate",
            "context_pack": "lybra_context_pack_build",
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

    def confirm(self, preview: Preview, owner_literal: str) -> dict[str, Any]:
        # Esc / empty literal = reject (the caller passes "" to cancel; never auto-supply).
        if not owner_literal:
            return {"ok": False, "verdict": "BLOCK", "error_code": "CANCELLED", "message": "Owner did not confirm."}
        return self._client.confirm(preview, owner_literal)


__all__ = ["TuiSession", "OBSERVE_MODE", "CONFIRM_MODE", "MODES", "WRITE_SCOPES", "token_fingerprint"]
