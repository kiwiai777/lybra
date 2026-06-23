"""AIPOS-203 — interactive confirm command (gate client).

A thin MCP client over the AIPOS-201 Streamable-HTTP transport that lets the Owner
review and confirm Supervised claim/return dry-runs without the F-c7 ergonomics traps
seen in the 191B rerun and the AIPOS-202 Form-B dogfood:

- the owner token is read internally (connection.json by role, or an env var) and is
  NEVER taken on the command line and NEVER printed (fingerprint-only);
- state is read through gate read-tools (``lybra_queue_list``), not by reading files;
- the confirm step auto-replays the dry-run's actor / agent_instance / owner_policy_ref
  (RF-4) so a missing arg can never BLOCK the confirm;
- dry-run TTL is surfaced and a one-call refresh re-issues the dry-run (10-minute window);
- the confirm action is explicit Owner intent (y/N + the owner-confirmation literal); the
  client never self-supplies confirmation.

This module is a pure client. It does not embed board_adapter / gate logic and does not
duplicate scope (AIPOS-197), confirmer attribution (AIPOS-199), or controlled-execute —
all of that stays server-side in the gate. The client only reads, previews, and relays.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request as _request

ACCEPT_STREAMABLE = "application/json, text/event-stream"
SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_VERSION = "2025-03-26"

_CLAIM_DRY_RUN = "lybra_queue_claim_dry_run"
_CLAIM_CONFIRM = "lybra_queue_claim_confirm"
_RETURN_DRY_RUN = "lybra_queue_return_dry_run"
_RETURN_CONFIRM = "lybra_queue_return_confirm"
# AIPOS-205: the TUI confirm panel also covers gated publish (AIPOS-204). The publish
# confirm has a different arg shape (no agent_instance/owner_policy_ref), so confirm()
# is op-aware below.
_PUBLISH_DRY_RUN = "lybra_draft_publish_dry_run"
_PUBLISH_CONFIRM = "lybra_draft_publish_confirm"
_QUEUE_LIST = "lybra_queue_list"

_DRY_RUN_TOOL = {"claim": _CLAIM_DRY_RUN, "return": _RETURN_DRY_RUN, "publish": _PUBLISH_DRY_RUN}
_CONFIRM_TOOL = {"claim": _CLAIM_CONFIRM, "return": _RETURN_CONFIRM, "publish": _PUBLISH_CONFIRM}

# The three args a confirm must replay from its dry-run (RF-4). Held by the client
# because the client issued the dry-run, so they can never be mistyped/omitted.
_REPLAY_KEYS = ("actor", "agent_instance", "owner_policy_ref")


def token_fingerprint(token: str) -> str:
    """Non-secret fingerprint of a bearer token (never the raw token)."""
    if not token:
        return "(none)"
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def load_owner_token(*, connection_json: str | Path | None = None, role: str = "owner", token_env: str | None = None) -> str:
    """Read a role token internally. Either from a connection.json (by role) or an env var.

    The raw token is returned for in-process use only; callers must never print it.
    Prefer a connection.json + role so the token never touches the command line.
    """
    if token_env:
        value = os.environ.get(token_env, "").strip()
        if not value:
            raise ValueError(f"environment variable {token_env} is empty or unset")
        return value
    if connection_json is None:
        raise ValueError("provide connection_json (+ role) or token_env to source the token")
    path = Path(connection_json).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("tokens", []):
        if isinstance(item, dict) and item.get("role") == role:
            token = str(item.get("token") or "").strip()
            if not token:
                raise ValueError(f"role {role!r} in {path} has no token")
            return token
    raise ValueError(f"role {role!r} not found in {path}")


@dataclass
class Preview:
    """A confirmable dry-run the client issued (so it owns the replay args)."""

    op: str  # "claim" | "return"
    dry_run_token: str
    expires_at: str | None
    snapshot_hash: str | None
    replay_args: dict[str, Any]
    structured: dict[str, Any] = field(default_factory=dict)

    def ttl_remaining_seconds(self, *, now: datetime | None = None) -> float | None:
        if not self.expires_at:
            return None
        now = now or datetime.now(timezone.utc)
        try:
            expires = datetime.fromisoformat(str(self.expires_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return (expires - now).total_seconds()

    def is_expired(self, *, now: datetime | None = None) -> bool:
        remaining = self.ttl_remaining_seconds(now=now)
        return remaining is not None and remaining <= 0


class GateError(RuntimeError):
    pass


class GateClient:
    """Streamable-HTTP MCP client for the Owner confirm workflow."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token  # raw token: never logged or returned
        self._session_id: str | None = None
        self._timeout = timeout
        # Bypass any ambient HTTP proxy for loopback gate calls.
        self._opener = _request.build_opener(_request.ProxyHandler({}))
        self._next_id = 0

    @property
    def token_fingerprint(self) -> str:
        return token_fingerprint(self._token)

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": ACCEPT_STREAMABLE,
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers[SESSION_HEADER] = self._session_id
        req = _request.Request(f"{self._base_url}/mcp", data=body, headers=headers, method="POST")
        with self._opener.open(req, timeout=self._timeout) as response:
            issued = response.headers.get(SESSION_HEADER)
            if issued:
                self._session_id = issued
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("error"):
            raise GateError(str(payload["error"].get("message") or payload["error"]))
        return payload.get("result") if isinstance(payload, dict) else None

    def initialize(self) -> dict[str, Any]:
        result = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION}) or {}
        return result

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise GateError(f"tool {name} returned no structuredContent")
        return structured

    # --- read path: state via gate read-tool, never direct file reads ---

    def queue_tasks(self) -> list[dict[str, Any]]:
        structured = self.call_tool(_QUEUE_LIST, {})
        data = structured.get("data") if isinstance(structured.get("data"), dict) else {}
        tasks = data.get("tasks")
        return tasks if isinstance(tasks, list) else []

    def list_confirm_gates(self) -> list[dict[str, Any]]:
        """Tasks in a state where an Owner confirm is the applicable next step.

        pending -> a claim can be confirmed; claimed (not yet returned) -> a return
        can be confirmed. Derived purely from the gate read-tool output.
        """
        gates: list[dict[str, Any]] = []
        for task in self.queue_tasks():
            state = str(task.get("queue_state") or "")
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            if state == "pending":
                gates.append({"op": "claim", "task_id": task.get("task_id"), "task": task})
            elif state == "claimed":
                already_returned = metadata.get("executor_status") == "completed" or metadata.get("audit_readiness") == "ready"
                if not already_returned:
                    gates.append({"op": "return", "task_id": task.get("task_id"), "task": task})
        return gates

    # --- preview (issue dry-run) + confirm (replay) ---

    def preview(self, op: str, dry_run_args: dict[str, Any]) -> Preview:
        tool = _DRY_RUN_TOOL.get(op)
        if tool is None:
            raise ValueError(f"unknown op {op!r}; expected 'claim', 'return', or 'publish'")
        structured = self.call_tool(tool, dry_run_args)
        token = structured.get("dry_run_token") or structured.get("dry_run_id")
        if not token:
            reasons = structured.get("blocking_reasons") or structured.get("errors") or structured
            raise GateError(f"{op} dry-run produced no token: {reasons}")
        # publish confirm replays only actor (no agent_instance/owner_policy_ref).
        replay_keys = ("actor",) if op == "publish" else _REPLAY_KEYS
        replay = {key: dry_run_args.get(key) for key in replay_keys}
        return Preview(
            op=op,
            dry_run_token=str(token),
            expires_at=structured.get("dry_run_expires_at") or structured.get("expires_at"),
            snapshot_hash=structured.get("dry_run_snapshot_hash"),
            replay_args=replay,
            structured=structured,
        )

    def refresh(self, preview: Preview, dry_run_args: dict[str, Any]) -> Preview:
        """Re-issue the dry-run (TTL window expired or about to)."""
        return self.preview(preview.op, dry_run_args)

    def confirm(self, preview: Preview, owner_confirmation_literal: str) -> dict[str, Any]:
        """Send the Owner confirm, auto-replaying the dry-run's 3 args (RF-4).

        The owner_confirmation_literal is explicit Owner intent supplied at call time;
        the client never defaults or self-supplies it.
        """
        if not owner_confirmation_literal:
            raise ValueError("owner confirmation literal is required (explicit Owner intent)")
        tool = _CONFIRM_TOOL.get(preview.op)
        if tool is None:
            raise ValueError(f"unknown op {preview.op!r}; expected 'claim', 'return', or 'publish'")
        arguments = {
            "dry_run_token": preview.dry_run_token,
            "owner_confirmation_token": owner_confirmation_literal,
        }
        # publish confirm replays only actor; claim/return replay the 3 identity args (RF-4).
        replay_keys = ("actor",) if preview.op == "publish" else _REPLAY_KEYS
        for key in replay_keys:
            if preview.replay_args.get(key) is not None:
                arguments[key] = preview.replay_args.get(key)
        return self.call_tool(tool, arguments)


# --- dry-run arg derivation from a gate read-tool task (no file reads) ---


def claim_args_from_task(task: dict[str, Any], *, owner_policy_ref: str, claim_reason: str = "owner-confirmed claim") -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    instance = str(metadata.get("agent_instance") or task.get("agent_instance") or "agent-01")
    return {
        "task_id": task.get("task_id"),
        "actor": instance,
        "agent_instance": instance,
        "autonomy_mode": "Supervised",
        "owner_policy_ref": owner_policy_ref,
        "runtime_profile": str(metadata.get("runtime_profile") or "cc"),
        "active_session_id": f"session_{task.get('task_id')}_confirm",
        "context_bundle_ack": "ack",
        "with_records": True,
        "claim_reason": claim_reason,
    }


def return_args_from_task(task: dict[str, Any], *, result_summary: str, return_reason: str = "owner-confirmed return", completion_report_ref: str | None = None) -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    instance = str(metadata.get("agent_instance") or task.get("agent_instance") or "agent-01")
    return {
        "task_id": task.get("task_id"),
        "actor": instance,
        "agent_instance": instance,
        "autonomy_mode": "Supervised",
        "owner_policy_ref": str(metadata.get("return_owner_policy_ref") or metadata.get("owner_policy_ref") or ""),
        "claim_id": metadata.get("claim_id"),
        "active_session_id": metadata.get("active_session_id"),
        "result_summary": result_summary,
        "completion_report_ref": completion_report_ref or "reports/owner-confirmed-return.md",
        "executor_status": "completed",
        "audit_readiness": "ready",
        "return_reason": return_reason,
    }


def _fmt_ttl(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds <= 0:
        return "EXPIRED"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="lybra confirm",
        description="Interactive Owner confirm client over the gate (F-c7 fix). The owner token is read internally; never pass it on the command line.",
    )
    parser.add_argument("--gate-url", required=True, help="e.g. http://127.0.0.1:7118")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--connection-json", help="path to .lybra/local/connection.json (token read by role)")
    src.add_argument("--token-env", help="env var holding the owner bearer token")
    parser.add_argument("--role", default="owner", help="role to read from connection.json (default owner)")
    parser.add_argument("--owner-policy-ref", default="owner_policy:supervised", help="owner_policy_ref for a claim preview")
    parser.add_argument("--result-summary", default="owner-confirmed return", help="result_summary for a return preview")
    parser.add_argument("--list", action="store_true", help="list confirm gates and exit")
    args = parser.parse_args(argv)

    try:
        token = load_owner_token(connection_json=args.connection_json, role=args.role, token_env=args.token_env)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error reading token: {exc}")
        return 2

    client = GateClient(args.gate_url, token)
    print(f"gate: {args.gate_url}  token: {client.token_fingerprint}")
    client.initialize()
    gates = client.list_confirm_gates()
    if not gates:
        print("no confirm gates pending.")
        return 0
    for index, gate in enumerate(gates):
        print(f"  [{index}] {gate['op']:<6} {gate['task_id']}")
    if args.list:
        return 0

    raw = input("select gate # (or blank to cancel): ").strip()
    if not raw:
        print("cancelled.")
        return 0
    try:
        gate = gates[int(raw)]
    except (ValueError, IndexError):
        print("invalid selection.")
        return 2

    if gate["op"] == "claim":
        dry_args = claim_args_from_task(gate["task"], owner_policy_ref=args.owner_policy_ref)
    else:
        dry_args = return_args_from_task(gate["task"], result_summary=args.result_summary)

    preview = client.preview(gate["op"], dry_args)
    print(f"dry-run {preview.dry_run_token}  TTL {_fmt_ttl(preview.ttl_remaining_seconds())}")
    print(f"  replay: actor={preview.replay_args.get('actor')} agent_instance={preview.replay_args.get('agent_instance')} owner_policy_ref={preview.replay_args.get('owner_policy_ref')}")

    if preview.is_expired():
        if input("dry-run expired; refresh? [y/N]: ").strip().lower() == "y":
            preview = client.refresh(preview, dry_args)
            print(f"refreshed: {preview.dry_run_token}  TTL {_fmt_ttl(preview.ttl_remaining_seconds())}")

    if input(f"confirm {gate['op']} of {gate['task_id']}? [y/N]: ").strip().lower() != "y":
        print("cancelled.")
        return 0
    literal = input("owner confirmation literal: ").strip()
    if not literal:
        print("no literal supplied; cancelled.")
        return 0
    result = client.confirm(preview, literal)
    if result.get("ok"):
        print(f"OK: {gate['op']} confirmed for {gate['task_id']} (confirmer recorded server-side).")
        return 0
    print(f"BLOCKED: error_code={result.get('error_code')} verdict={result.get('verdict')} {result.get('message','')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
