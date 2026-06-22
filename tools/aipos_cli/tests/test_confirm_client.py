from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from tools.aipos_cli.confirm_client import (
    GateClient,
    GateError,
    Preview,
    claim_args_from_task,
    load_owner_token,
    return_args_from_task,
    token_fingerprint,
)
from tools.aipos_cli.records import load_records
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server


class ConfirmClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.write_claim_task()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_claim_task(self, task_id: str = "AIPOS-CC-CLAIM", *, agent_instance: str = "agent-01") -> None:
        (self.repo_root / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md").write_text(
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    "title: Confirm-client claim test",
                    "project: lybra",
                    "assigned_to: dev_claude",
                    f"agent_instance: {agent_instance}",
                    "context_bundle: dev_claude",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/aipos_cli/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/aipos_cli/",
                    "memory_scope: confirm client tests",
                    "claim_policy: specific_instance_only",
                    "---",
                    "Supervised claim test task for the confirm client.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def claim_args(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "task_id": "AIPOS-CC-CLAIM",
            "actor": "agent-01",
            "agent_instance": "agent-01",
            "autonomy_mode": "Supervised",
            "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
            "runtime_profile": "cc",
            "active_session_id": "session_cc_claim_test",
            "context_bundle_ack": "ack",
            "with_records": True,
            "claim_reason": "confirm client claim test",
        }
        payload.update(overrides)
        return payload

    def registry(self) -> dict[str, dict[str, object]]:
        return {
            "owner-secret": {
                "role": "owner",
                "token_ref": "svc-owner",
                "scopes": ["queue_claim", "queue_return", "owner_confirm"],
                "expires_at": "2999-01-01T00:00:00Z",
                "fingerprint": "sha256:ownerfp203",
            },
            "executor-secret": {
                "role": "executor",
                "token_ref": "svc-executor",
                "scopes": ["queue_claim", "queue_return"],
                "expires_at": "2999-01-01T00:00:00Z",
                "fingerprint": "sha256:execfp203",
            },
        }

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST,
            port=0,
            token="",
            keepalive_seconds=0.01,
            max_keepalive_events=1,
            service_role_registry=self.registry(),
        )
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}
        with patch.dict(os.environ, env, clear=True):
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

    # --- T6 / read path + listing ---

    def test_lists_confirm_gates_via_read_tool(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            client.initialize()
            gates = client.list_confirm_gates()
        ops = {(g["op"], g["task_id"]) for g in gates}
        self.assertIn(("claim", "AIPOS-CC-CLAIM"), ops)

    # --- T2 owner confirm closes the loop with confirmer attribution on disk ---

    def test_owner_preview_and_confirm_records_confirmer_on_disk(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            client.initialize()
            preview = client.preview("claim", self.claim_args())
            self.assertTrue(preview.dry_run_token)
            result = client.confirm(preview, "OWNER_CONFIRMED")
        self.assertTrue(result.get("ok"), result)
        records = load_records(self.repo_root)
        meta = records["claims"][0]["metadata"]
        self.assertEqual(meta.get("confirmer_role"), "owner")
        self.assertEqual(meta.get("confirmer_token_ref"), "svc-owner")
        self.assertEqual(meta.get("confirmer_token_fingerprint"), "sha256:ownerfp203")

    # --- T1 ★A1: executor-scope token cannot confirm through the client ---

    def test_executor_scope_confirm_is_scope_denied(self) -> None:
        with self.gate() as url:
            # executor issues the dry-run (allowed), then tries to confirm (denied).
            client = GateClient(url, "executor-secret")
            client.initialize()
            preview = client.preview("claim", self.claim_args())
            denied = client.confirm(preview, "OWNER_CONFIRMED")
        self.assertEqual(denied.get("error_code"), "SCOPE_DENIED")
        # and the task was NOT claimed
        records = load_records(self.repo_root)
        self.assertEqual(records.get("claims", []), [])

    # --- T4 the confirm auto-replays the dry-run's 3 args (RF-4) ---

    def test_confirm_auto_replays_dry_run_identity_args(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            client.initialize()
            preview = client.preview("claim", self.claim_args())
        self.assertEqual(preview.replay_args["actor"], "agent-01")
        self.assertEqual(preview.replay_args["agent_instance"], "agent-01")
        self.assertEqual(preview.replay_args["owner_policy_ref"], "owner_policy:aipos-166-supervised-test")

    def test_confirm_blocks_when_replay_arg_omitted(self) -> None:
        # Control proving the replay matters: a confirm missing owner_policy_ref BLOCKs.
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            client.initialize()
            preview = client.preview("claim", self.claim_args())
            preview.replay_args["owner_policy_ref"] = None  # simulate the RF-4 omission
            blocked = client.confirm(preview, "OWNER_CONFIRMED")
        self.assertFalse(blocked.get("ok"))

    # --- T3 TTL surfaced + refresh re-issues ---

    def test_ttl_remaining_and_expiry(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        fresh = Preview(op="claim", dry_run_token="t", expires_at=future, snapshot_hash=None, replay_args={})
        stale = Preview(op="claim", dry_run_token="t", expires_at=past, snapshot_hash=None, replay_args={})
        self.assertGreater(fresh.ttl_remaining_seconds() or 0, 0)
        self.assertFalse(fresh.is_expired())
        self.assertTrue(stale.is_expired())

    def test_refresh_issues_a_new_token(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            client.initialize()
            first = client.preview("claim", self.claim_args())
            second = client.refresh(first, self.claim_args())
        self.assertTrue(second.dry_run_token)
        self.assertNotEqual(first.dry_run_token, second.dry_run_token)

    # --- T5 no raw token leak ---

    def test_token_is_never_exposed(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            fp = client.token_fingerprint
        self.assertTrue(fp.startswith("sha256:"))
        self.assertNotIn("owner-secret", fp)
        self.assertNotIn("owner-secret", repr(client))
        self.assertNotEqual(token_fingerprint("owner-secret"), "owner-secret")

    def test_confirm_requires_explicit_owner_literal(self) -> None:
        preview = Preview(op="claim", dry_run_token="t", expires_at=None, snapshot_hash=None, replay_args={})
        client = GateClient("http://127.0.0.1:1", "owner-secret")
        with self.assertRaises(ValueError):
            client.confirm(preview, "")

    # --- token loader: connection.json by role, never argv ---

    def test_load_owner_token_from_connection_by_role(self) -> None:
        conn = self.repo_root / "connection.json"
        conn.write_text(json.dumps({"tokens": [
            {"role": "owner", "token": "owner-secret", "token_ref": "svc-owner"},
            {"role": "executor", "token": "executor-secret", "token_ref": "svc-executor"},
        ]}), encoding="utf-8")
        self.assertEqual(load_owner_token(connection_json=conn, role="owner"), "owner-secret")
        self.assertEqual(load_owner_token(connection_json=conn, role="executor"), "executor-secret")
        with self.assertRaises(ValueError):
            load_owner_token(connection_json=conn, role="nope")

    def test_load_owner_token_from_env(self) -> None:
        with patch.dict(os.environ, {"CC_OWNER_TOKEN": "owner-secret"}, clear=False):
            self.assertEqual(load_owner_token(token_env="CC_OWNER_TOKEN"), "owner-secret")

    # --- arg derivation from a gate read-tool task (claim end-to-end via helper) ---

    def test_claim_args_from_task_drive_a_real_confirm(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "owner-secret")
            client.initialize()
            gate = next(g for g in client.list_confirm_gates() if g["op"] == "claim")
            dry_args = claim_args_from_task(gate["task"], owner_policy_ref="owner_policy:aipos-166-supervised-test")
            preview = client.preview("claim", dry_args)
            result = client.confirm(preview, "OWNER_CONFIRMED")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(load_records(self.repo_root)["claims"][0]["metadata"].get("confirmer_role"), "owner")

    def test_return_args_from_task_carry_identity(self) -> None:
        task = {
            "task_id": "AIPOS-CC-RET",
            "metadata": {
                "agent_instance": "agent-09",
                "claim_id": "claim_x",
                "active_session_id": "session_x",
                "return_owner_policy_ref": "owner_policy:ret",
            },
        }
        args = return_args_from_task(task, result_summary="done")
        self.assertEqual(args["actor"], "agent-09")
        self.assertEqual(args["agent_instance"], "agent-09")
        self.assertEqual(args["owner_policy_ref"], "owner_policy:ret")
        self.assertEqual(args["claim_id"], "claim_x")


if __name__ == "__main__":
    unittest.main()
