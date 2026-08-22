"""AIPOS-F21: unit tests for token rotation (`lybra roles rotate`) and
instance-token removal (`lybra roles remove --instance`).

Acceptance anchors (task card AIPOS-F21):
① dry-run previews fingerprints and lands NO change
② execution updates every selected token (same structure), timestamped 0600
   backup in place, old->new fingerprint table recorded (no plaintext)
④ instance removal removes exactly the bound entry and writes a record
Red line: token plaintext never appears in results or records.
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.token_rotation import (
    remove_instance_report,
    rotate_tokens_report,
    secret_fingerprint,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def make_workspace(tmp: Path, tokens: list[dict]) -> Path:
    ws = tmp / "ws"
    (ws / ".lybra").mkdir(parents=True)
    (ws / "5_tasks").mkdir(parents=True)
    conn = {
        "config_version": 1,
        "mode": "service_v0",
        "mcp": {"rpc_url": "http://127.0.0.1:59999/mcp", "host": "127.0.0.1", "port": 59999},
        "workspace_root": str(ws),
        "rotated_at": "2026-08-04T17:11:00Z",
        "tokens": tokens,
    }
    (ws / ".lybra" / "connection.json").write_text(json.dumps(conn, indent=2), encoding="utf-8")
    return ws


def sample_tokens() -> list[dict]:
    return [
        {"role": "owner", "token": "tok-owner-old", "token_ref": "svc-owner",
         "scopes": ["owner_confirm"], "fingerprint": secret_fingerprint("tok-owner-old")},
        {"role": "executor", "token": "tok-exec-old", "token_ref": "svc-executor",
         "scopes": ["queue_claim"], "fingerprint": secret_fingerprint("tok-exec-old"),
         "agent_instance": "exec.lybra.kiwiai-dev"},
        {"role": "executor", "token": "tok-test-old", "token_ref": "svc-executor",
         "scopes": ["queue_claim"], "fingerprint": secret_fingerprint("tok-test-old"),
         "agent_instance": "test.mac.aipos362"},
    ]


def load(ws: Path) -> dict:
    return json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))


class TestRotateDryRun(unittest.TestCase):
    def test_dry_run_lands_no_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            before = (ws / ".lybra" / "connection.json").read_bytes()
            result = rotate_tokens_report(ws, dry_run=True, reload_gate=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["verdict"], "PASS")
            after = (ws / ".lybra" / "connection.json").read_bytes()
            self.assertEqual(before, after, "dry-run must not touch connection.json")
            self.assertEqual(len(result["would_rotate"]), 3)
            for entry in result["would_rotate"]:
                self.assertTrue(entry["fingerprint"].startswith("sha256:"))
                self.assertNotIn("token", entry)
            self.assertFalse(list((ws / "5_tasks" / "records").rglob("*.md")) if (ws / "5_tasks" / "records").exists() else [])

    def test_role_subset_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = rotate_tokens_report(ws, dry_run=True, roles=["owner"], reload_gate=False)
            self.assertTrue(result["ok"])
            self.assertEqual([e["role"] for e in result["would_rotate"]], ["owner"])

    def test_execute_requires_owner_authorization_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = rotate_tokens_report(ws, dry_run=False, reload_gate=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["verdict"], "BLOCK")

    def test_unknown_role_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = rotate_tokens_report(ws, dry_run=True, roles=["nope"], reload_gate=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["verdict"], "BLOCK")


class TestRotateExecute(unittest.TestCase):
    def test_full_rotation_backup_record_no_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = rotate_tokens_report(
                ws, dry_run=False,
                owner_authorization_ref="task-card:AIPOS-F21",
                actor="exec.lybra.kiwiai-dev",
                reason="pre-migration rotation",
                reload_gate=False,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["verdict"], "PASS")
            # every entry rotated with old->new fingerprint mapping
            self.assertEqual(len(result["rotated"]), 3)
            config = load(ws)
            fingerprints = {t["fingerprint"] for t in config["tokens"]}
            for item in result["rotated"]:
                self.assertIn(item["new_fingerprint"], fingerprints)
                self.assertNotEqual(item["old_fingerprint"], item["new_fingerprint"])
            # structure preserved: roles/scopes/instances/refs identical, only token+fingerprint change
            old = {t["agent_instance"] if "agent_instance" in t else t["role"]: t for t in sample_tokens()}
            for t in config["tokens"]:
                key = t.get("agent_instance") or t["role"]
                self.assertEqual(t["role"], old[key]["role"])
                self.assertEqual(t["scopes"], old[key]["scopes"])
                self.assertEqual(t["token_ref"], old[key]["token_ref"])
                self.assertNotEqual(t["token"], old[key]["token"])
            # rotated_at advanced
            self.assertNotEqual(config["rotated_at"], "2026-08-04T17:11:00Z")
            # backup: timestamped suffix, 0600, contains the OLD tokens
            backup = Path(result["backup_path"])
            self.assertTrue(backup.exists())
            self.assertIn(".bak-", backup.name)
            mode = stat.S_IMODE(backup.stat().st_mode)
            self.assertEqual(mode, 0o600)
            backup_tokens = json.loads(backup.read_text())["tokens"]
            self.assertIn("tok-owner-old", {t["token"] for t in backup_tokens})
            # record: machine marker + fingerprint-only
            record = Path(result["rotation_record"])
            self.assertTrue(record.exists())
            text = record.read_text(encoding="utf-8")
            self.assertIn("record_type: token_rotation", text)
            for item in result["rotated"]:
                self.assertIn(item["old_fingerprint"], text)
                self.assertIn(item["new_fingerprint"], text)
            self.assertNotIn("tok-owner-old", text)
            self.assertNotIn("tok-exec-old", text)
            # connection.json itself stays 0600
            self.assertEqual(stat.S_IMODE((ws / ".lybra" / "connection.json").stat().st_mode), 0o600)

    def test_role_subset_leaves_others_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = rotate_tokens_report(
                ws, dry_run=False, roles=["executor"],
                owner_authorization_ref="task-card:AIPOS-F21", reload_gate=False,
            )
            self.assertTrue(result["ok"])
            config = load(ws)
            by_key = {(t.get("agent_instance") or t["role"]): t for t in config["tokens"]}
            self.assertEqual(by_key["owner"]["token"], "tok-owner-old")  # untouched
            self.assertNotEqual(by_key["exec.lybra.kiwiai-dev"]["token"], "tok-exec-old")
            self.assertEqual(len(result["rotated"]), 2)  # both executor entries

    def test_reload_failure_yields_restart_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            # rpc_url points nowhere; reload_gate=True must not crash, must guide restart
            result = rotate_tokens_report(
                ws, dry_run=False,
                owner_authorization_ref="task-card:AIPOS-F21", reload_gate=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["gate_reload"], "restart_required")
            self.assertTrue(any("systemctl restart" in line for line in result["restart_guidance"]))
            self.assertTrue(any("re-enroll" in line or "enroll" in line for line in result["next_steps"]))


class TestRemoveInstance(unittest.TestCase):
    def test_remove_bound_instance_with_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = remove_instance_report(
                ws, instance="test.mac.aipos362",
                owner_authorization_ref="task-card:AIPOS-F21",
                reason="early test residue cleanup",
                reload_gate=False,
            )
            self.assertTrue(result["ok"])
            config = load(ws)
            instances = {t.get("agent_instance") for t in config["tokens"]}
            self.assertNotIn("test.mac.aipos362", instances)
            self.assertIn("exec.lybra.kiwiai-dev", instances)
            record = Path(result["removal_record"])
            text = record.read_text(encoding="utf-8")
            self.assertIn("record_type: token_removal", text)
            self.assertIn("test.mac.aipos362", text)
            self.assertNotIn("tok-test-old", text)

    def test_remove_requires_owner_authorization_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = remove_instance_report(ws, instance="test.mac.aipos362", reload_gate=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["verdict"], "BLOCK")

    def test_remove_unknown_instance_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), sample_tokens())
            result = remove_instance_report(
                ws, instance="ghost.example", owner_authorization_ref="task-card:AIPOS-F21",
                reload_gate=False,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["verdict"], "BLOCK")


class TestReloadVerbRegistration(unittest.TestCase):
    def test_verb_registered_in_gate(self):
        from tools.mcp_server import tools as mcp_tools
        self.assertIn("lybra_roles_reload", mcp_tools.TOOL_HANDLERS)
        names = [d["name"] for d in mcp_tools.WRITE_TOOL_DESCRIPTORS]
        self.assertIn("lybra_roles_reload", names)


if __name__ == "__main__":
    unittest.main()
