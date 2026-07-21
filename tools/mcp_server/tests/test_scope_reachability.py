"""AIPOS-207 — standing scope-reachability regression (test-fidelity fix for F-cop-204scope-1).

The AIPOS-206 dogfood surfaced that tools.py referenced 8 scopes while service_mode.ROLE_SPECS
granted only 5 — draft_publish/intake_submit/owner_decision_record had no mintable role, yet the
AIPOS-204 tests passed because they hand-built a registry that granted draft_publish. That is RF-5
at the scope-registration layer: tests asserting against a fabricated premise, never the real
credential path.

These tests close that hole. They use REAL serve-rotate credentials (no hand-built registry) to
assert every scope tools.py references is either reachable by some minted role OR registered-exempt
with a *proven* alternate path (path B = LYBRA_CAPABILITY_TOKEN). Adding a new gate tool that
references a scope without granting a role (and without a working exempt path) turns these red.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from tools.aipos_cli.confirm_client import GateClient
from tools.aipos_cli.service_mode import ROLE_SPECS, build_connection_config, write_connection_config
from tools.mcp_server import tools as gate_tools
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server, load_service_role_registry

# Scopes intentionally NOT granted to any service role: the AIPOS-109 (intake_submit) stdio MCP
# controlled write-tool is reached via path B — an operator-minted LYBRA_CAPABILITY_TOKEN — not a
# service role. Each exempt scope is PROVEN reachable via path B below, so EXEMPT cannot be used as
# an escape hatch for a future missed role grant.
# AIPOS-250: owner_decision_record MOVED OFF this exemption — it is now granted to the owner role
# (so the owner-console PreAuthorized-envelope flow is reachable via serve-rotate creds); it is
# covered by the rotate union, not by path B. (Path B still works code-wise; it is simply no longer
# the ONLY path, so it is not asserted as exempt here.)
CAPABILITY_TOKEN_EXEMPT = {"intake_submit"}
# scope -> its dry-run tool (maintained alongside tools.py); every EXEMPT scope must appear here.
EXEMPT_DRY_RUN_TOOL = {
    "intake_submit": "lybra_intake_submit_dry_run",
}


def _tools_py_scopes() -> set[str]:
    """Every scope tools.py references — enumerated from its *_SCOPE constants (single source)."""
    return {
        value
        for name, value in vars(gate_tools).items()
        if name.endswith("_SCOPE") and isinstance(value, str) and value
    }


def _capability_env_token(scope: str) -> str:
    return json.dumps(
        {"operations": [scope], "token_ref": "test-capability", "expires_at": "2999-01-01T00:00:00Z"}
    )


class ScopeReachabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "drafts").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _real_rotate_config(self) -> dict:
        # REAL serve-rotate output (mints tokens from ROLE_SPECS) — no hand-built registry.
        config = build_connection_config(
            self.repo_root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118
        )
        write_connection_config(self.repo_root, config)
        return config

    @contextmanager
    def _gate(self, *, service_role_registry=None, token: str = "") -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST, port=0, token=token, keepalive_seconds=0.01,
            max_keepalive_events=1, service_role_registry=service_role_registry,
        )
        httpd = build_http_server(config)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            yield f"http://{host}:{port}"
        finally:
            httpd.shutdown()
            thread.join(timeout=2)
            httpd.server_close()

    # --- T1 (★): every tools.py scope is reachable via a real role OR registered-exempt ---
    def test_every_scope_reachable_or_exempt(self) -> None:
        config = self._real_rotate_config()
        rotate_union = {s for tok in config["tokens"] for s in tok.get("scopes", [])}
        required = _tools_py_scopes()
        covered = rotate_union | CAPABILITY_TOKEN_EXEMPT
        missing = required - covered
        self.assertEqual(
            missing, set(),
            f"scopes referenced by tools.py with no mintable role and not registered-exempt: {sorted(missing)}",
        )

    def test_exempt_set_has_no_role_overlap(self) -> None:
        # An exempt scope must NOT also be granted by a role (would be a contradictory disposition).
        union = {s for spec in ROLE_SPECS for s in spec["scopes"]}
        self.assertEqual(CAPABILITY_TOKEN_EXEMPT & union, set())

    # --- T2: draft_publish reachable by owner via REAL rotate creds ---
    def test_owner_draft_publish_reachable(self) -> None:
        config = self._real_rotate_config()
        registry = load_service_role_registry(self.repo_root / ".lybra" / "local" / "connection.json")
        owner_token = next(t["token"] for t in config["tokens"] if t["role"] == "owner")
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            with self._gate(service_role_registry=registry) as url:
                c = GateClient(url, owner_token); c.initialize()
                r = c.call_tool("lybra_draft_publish_dry_run", {"path": "5_tasks/drafts/x.md", "actor": "owner"})
        self.assertNotEqual(r.get("error_code"), "SCOPE_DENIED", r)

    # --- T2b (AIPOS-250): owner_decision_record reachable by owner via REAL rotate creds ---
    def test_owner_decision_record_reachable(self) -> None:
        config = self._real_rotate_config()
        registry = load_service_role_registry(self.repo_root / ".lybra" / "local" / "connection.json")
        owner_token = next(t["token"] for t in config["tokens"] if t["role"] == "owner")
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            with self._gate(service_role_registry=registry) as url:
                c = GateClient(url, owner_token); c.initialize()
                r = c.call_tool("lybra_owner_decision_record_dry_run", {"actor": "owner"})
        # not SCOPE_DENIED — the owner-console envelope flow (arm owner_autonomy_policy) is reachable.
        self.assertNotEqual(r.get("error_code"), "SCOPE_DENIED", r)

    def test_owner_decision_record_denied_for_executor(self) -> None:
        # ★A1 boundary: owner_decision_record stays Owner-only — executor/planner cannot arm a policy.
        config = self._real_rotate_config()
        registry = load_service_role_registry(self.repo_root / ".lybra" / "local" / "connection.json")
        tokens = {t["role"]: t["token"] for t in config["tokens"]}
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            with self._gate(service_role_registry=registry) as url:
                for role in ("executor", "planner", "copilot"):
                    c = GateClient(url, tokens[role]); c.initialize()
                    r = c.call_tool("lybra_owner_decision_record_dry_run", {"actor": role})
                    self.assertEqual(r.get("error_code"), "SCOPE_DENIED", f"{role}: {r}")

    # --- T3: ★A1 not weakened — executor/copilot draft_publish denied via REAL rotate creds ---
    def test_executor_and_copilot_draft_publish_denied(self) -> None:
        config = self._real_rotate_config()
        registry = load_service_role_registry(self.repo_root / ".lybra" / "local" / "connection.json")
        tokens = {t["role"]: t["token"] for t in config["tokens"]}
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            with self._gate(service_role_registry=registry) as url:
                for role in ("executor", "copilot"):
                    c = GateClient(url, tokens[role]); c.initialize()
                    dry = c.call_tool("lybra_draft_publish_dry_run", {"path": "5_tasks/drafts/x.md", "actor": role})
                    conf = c.call_tool("lybra_draft_publish_confirm", {"dry_run_token": "x", "owner_confirmation_token": "OWNER_CONFIRMED"})
                    self.assertEqual(dry.get("error_code"), "SCOPE_DENIED", f"{role} dry-run: {dry}")
                    self.assertEqual(conf.get("error_code"), "SCOPE_DENIED", f"{role} confirm: {conf}")

    # --- T4: copilot must remain scopes [] (never gains draft_publish) ---
    def test_copilot_role_scopes_empty(self) -> None:
        copilot = next(spec for spec in ROLE_SPECS if spec["role"] == "copilot")
        self.assertEqual(copilot["scopes"], [])

    # --- T5 (Owner hardening): EVERY exempt scope is genuinely reachable via path B ---
    def test_exempt_set_maps_to_dry_run_tools(self) -> None:
        # Adding a name to EXEMPT without a dry-run mapping must fail (no escape hatch).
        self.assertEqual(set(EXEMPT_DRY_RUN_TOOL.keys()), CAPABILITY_TOKEN_EXEMPT)

    def test_every_exempt_scope_reachable_via_path_b(self) -> None:
        for scope in sorted(CAPABILITY_TOKEN_EXEMPT):
            tool = EXEMPT_DRY_RUN_TOOL[scope]
            # path B: no service registry, transport-token auth, capability via env.
            with patch.dict(
                os.environ,
                {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": _capability_env_token(scope)},
                clear=True,
            ):
                with self._gate(service_role_registry=None, token="transport-secret") as url:
                    c = GateClient(url, "transport-secret"); c.initialize()
                    granted = c.call_tool(tool, {})
            self.assertNotEqual(
                granted.get("error_code"), "SCOPE_DENIED",
                f"exempt scope {scope} not reachable via path B (LYBRA_CAPABILITY_TOKEN): {granted}",
            )

    def test_exempt_scope_denied_without_capability(self) -> None:
        # Negative control: same path B, but no capability token → SCOPE_DENIED.
        for scope in sorted(CAPABILITY_TOKEN_EXEMPT):
            tool = EXEMPT_DRY_RUN_TOOL[scope]
            with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
                with self._gate(service_role_registry=None, token="transport-secret") as url:
                    c = GateClient(url, "transport-secret"); c.initialize()
                    denied = c.call_tool(tool, {})
            self.assertEqual(denied.get("error_code"), "SCOPE_DENIED", f"{scope}: {denied}")


if __name__ == "__main__":
    unittest.main()
