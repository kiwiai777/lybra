"""AIPOS-363 S1/S2 — REAL-gate round-trip integration test.

Proves the connector works against the real gate code path (not just the fake): owner arms a
PreAuthorized envelope → executor `materialize` claims + pulls body + drops LOCAL material → a
simulated agent writes LOCAL RETURN → executor `pushback` relays it (320) + self-confirms (328).
This is acceptance 2's 'dev machine simulating the remote material area' for the executor side.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from tools.aipos_cli.agent_materialize import materialize, pushback
from tools.aipos_cli.confirm_client import GateClient
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server


def _registry() -> dict[str, dict[str, Any]]:
    return {
        "owner-secret": {
            "role": "owner", "token_ref": "svc-owner",
            "scopes": ["queue_claim", "queue_return", "owner_confirm", "owner_decision_record"],
            "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:ownfp363rt",
        },
        "executor-secret": {
            "role": "executor", "token_ref": "svc-executor",
            "scopes": ["queue_claim", "queue_return", "task_progress"],
            "expires_at": "2999-01-01T00:00:00Z", "fingerprint": "sha256:exfp363rt",
            "agent_instance": "exec.rt",
        },
    }


def _pending_task(task_id: str) -> str:
    return "\n".join([
        "---",
        f"task_id: {task_id}",
        f"title: 363 round-trip {task_id}",
        "project: lybra",
        "assigned_to: exec.rt",
        "agent_instance: exec.rt",
        "context_bundle: exec.rt",
        "task_mode: code",
        "priority: medium",
        "status: pending",
        "created_by: t",
        "needs_owner: false",
        "output_target: docs/",
        "artifact_policy: formal_write",
        "---",
        "## Body\n\nThis is the card body the cross-machine agent must receive.\n",
    ])


class MaterializeRoundTripGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.workspace.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-363-rt.md").write_text(
            _pending_task("AIPOS-363-RT"), encoding="utf-8")
        self.material_root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        self.workspace.cleanup()

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01,
            max_keepalive_events=1, service_role_registry=_registry(),
        )
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
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

    def _arm_envelope(self, owner: GateClient) -> None:
        payload = {
            "decision_id": "pol-decision-363-rt", "actor": "owner", "decided_by_ref": "owner",
            "decision_summary": "Arm envelope for the 363 round-trip test.",
            "autonomy_policy": {
                "policy_id": "pol_363_rt", "agent_or_role": "exec.rt",
                "active_from": "2020-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
                "max_tasks": 5, "task_selector": {"task_mode": "code"},
            },
        }
        dry = owner.call_tool("lybra_owner_decision_record_dry_run", payload)
        self.assertTrue(dry.get("dry_run_token"), dry)
        conf = owner.call_tool(
            "lybra_owner_decision_record_confirm",
            {"dry_run_token": dry["dry_run_token"], "actor": "owner", "owner_confirmation_token": "OWNER_CONFIRMED"},
        )
        self.assertTrue(conf.get("ok"), conf)

    def test_materialize_then_pushback_round_trip(self) -> None:
        with self.gate() as url:
            owner = GateClient(url, "owner-secret"); owner.initialize()
            self._arm_envelope(owner)
            executor = GateClient(url, "executor-secret"); executor.initialize()

            # S1: materialize — claim + pull body + drop local material
            mat = materialize(
                executor, task_id="AIPOS-363-RT", actor="exec.rt",
                owner_policy_ref="pol_363_rt", root=self.material_root,
                gate_workspace=str(self.repo_root),
            )
            self.assertTrue(mat["ok"], mat)
            card = Path(mat["card_path"]).read_text(encoding="utf-8")
            self.assertIn("card body the cross-machine agent must receive", card)
            # the task moved pending -> claimed on the REMOTE workspace (the gate's truth)
            self.assertFalse(list((self.repo_root / "5_tasks" / "queue" / "pending").glob("*.md")))
            self.assertTrue(list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md")))

            # S3: the agent only saw LOCAL files — simulate it writing its RETURN locally
            return_path = Path(mat["return_path"])
            self.assertFalse(return_path.exists(), "no RETURN until the agent writes one")
            return_path.write_text("# RETURN\n\nShipped the cross-machine work.\n", encoding="utf-8")

            # S2: pushback — read local RETURN + relay (320) + self-confirm (328)
            pb = pushback(
                executor, task_id="AIPOS-363-RT", actor="exec.rt",
                owner_policy_ref="pol_363_rt", root=self.material_root,
            )
            self.assertTrue(pb["ok"], pb)
            self.assertEqual(pb["phase"], "pushed_back")
            # the gate wrote the relayed body to the workspace RETURN.md (320)
            ws_return = self.repo_root / "task_cards" / "AIPOS-363-RT" / "RETURN.md"
            self.assertTrue(ws_return.is_file(), "gate must have written the relayed return_body")
            self.assertIn("Shipped the cross-machine work", ws_return.read_text(encoding="utf-8"))
            # local RETURN retained as copy (card S2)
            self.assertTrue(return_path.is_file())


if __name__ == "__main__":
    unittest.main()
