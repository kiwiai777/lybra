from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.board_adapter import execute_dry_run, record_owner_decision


class OwnerDecisionWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for queue_state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / queue_state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def payload(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "decision_id": "owner-decision-001",
            "decision_type": "approve_intake",
            "decision_status": "approved",
            "decided_at": "2026-05-21T10:00:00Z",
            "decided_by_ref": "owner_ref",
            "captured_by": "board.local",
            "capture_surface": "cli",
            "decision_summary": "Approve intake draft review.",
            "decision_rationale": "The request is in scope for the client.",
            "applies_to": {
                "project": "acme_client",
                "task_id": "EXT-ACME-001",
                "draft_path": "5_tasks/drafts/external_intake/example.md",
                "external_ref": "chat:msg-1001",
            },
            "approval_scope": {
                "operation": "owner_decision_record",
                "authority_boundary": "record decision only",
                "allowed_next_action": "review_draft",
            },
            "owner_approval_evidence": {
                "evidence_id": "evidence-001",
                "source_tag": "wechat_bot",
                "client_tag": "acme_client",
                "external_ref": "chat:msg-1001",
                "approval_actor_ref": "owner_ref",
                "approval_timestamp": "2026-05-21T10:00:00Z",
                "approval_intent": "approve_owner_decision_record",
                "evidence_hash": "sha256:abc123",
                "evidence_ref": "chat:redacted:msg-1001",
                "captured_by": "bot.local",
                "capture_method": "external_client",
                "redaction_status": "redacted",
                "refs": ["5_tasks/drafts/external_intake/example.md"],
            },
            "refs": ["5_tasks/drafts/external_intake/example.md"],
            "capability_scope": {
                "token_ref": "cap_owner_decision_test",
                "operations": ["owner_decision_record"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
        }
        data.update(overrides)
        return data

    def test_owner_decision_record_dry_run_and_confirm_writes_record_only(self) -> None:
        dry = record_owner_decision(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")

        self.assertTrue(dry["ok"])
        self.assertEqual(dry["verdict"], "PASS")
        self.assertIn("dry_run_id", dry)
        self.assertEqual(dry["planned_writes"][0]["path"], "5_tasks/records/owner_decisions/owner-decision-001.md")

        executed = execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "owner_decision_record")
        target = self.repo_root / executed["data"]["target_path"]
        self.assertTrue(target.exists())
        self.assertFalse(any((self.repo_root / "5_tasks" / "queue" / "pending").iterdir()))
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "orchestration").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "sessionstore").exists())
        text = target.read_text(encoding="utf-8")
        self.assertIn("record_type: owner_decision_record", text)
        self.assertIn("decision_id: owner-decision-001", text)
        self.assertIn("This record captures a scoped Owner decision.", text)

    def test_missing_owner_approval_evidence_blocks(self) -> None:
        payload = self.payload()
        payload.pop("owner_approval_evidence")

        result = record_owner_decision(payload, dry_run=True, repo_root=self.repo_root, actor="bot.local")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Missing required field: owner_approval_evidence", result["blocking_reasons"])
        self.assertIsNone(result["dry_run_id"])

    def test_scope_mismatch_blocks(self) -> None:
        payload = self.payload()
        evidence = dict(payload["owner_approval_evidence"])  # type: ignore[arg-type]
        evidence["client_tag"] = "other_client"
        payload["owner_approval_evidence"] = evidence

        result = record_owner_decision(payload, dry_run=True, repo_root=self.repo_root, actor="bot.local")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("owner_approval_evidence.client_tag must match applies_to.project", result["blocking_reasons"])

    def test_duplicate_decision_id_blocks_second_record(self) -> None:
        dry = record_owner_decision(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        executed = execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)
        self.assertTrue(executed["ok"])

        second = record_owner_decision(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")

        self.assertEqual(second["verdict"], "BLOCK")
        self.assertIn("Owner decision record already exists", "\n".join(second["blocking_reasons"]))

    def test_confirm_creates_missing_owner_decisions_directory(self) -> None:
        self.assertFalse((self.repo_root / "5_tasks" / "records").exists())

        dry = record_owner_decision(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        executed = execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertTrue((self.repo_root / "5_tasks" / "records" / "owner_decisions").is_dir())

    def test_cli_stateless_confirm_uses_dry_run_json_proof(self) -> None:
        payload_path = self.repo_root / "payload.json"
        dry_path = self.repo_root / "dry.json"
        payload_path.write_text(json.dumps(self.payload()), encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            os.chdir(self.repo_root)
            with redirect_stdout(StringIO()) as dry_stdout:
                dry_rc = main(
                    [
                        "controlled-execute",
                        "dry-run",
                        "--operation",
                        "owner_decision_record",
                        "--from-json",
                        str(payload_path),
                        "--actor",
                        "bot.local",
                        "--json",
                    ]
                )
            self.assertEqual(dry_rc, 0)
            dry = json.loads(dry_stdout.getvalue())
            dry_path.write_text(json.dumps(dry), encoding="utf-8")

            with redirect_stdout(StringIO()) as confirm_stdout:
                confirm_rc = main(["controlled-execute", "confirm", "--from-json", str(dry_path), "--actor", "bot.local", "--json"])
        finally:
            os.chdir(original_cwd)

        self.assertEqual(confirm_rc, 0)
        executed = json.loads(confirm_stdout.getvalue())
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "owner_decision_record")
        self.assertTrue((self.repo_root / executed["data"]["target_path"]).exists())


if __name__ == "__main__":
    unittest.main()
