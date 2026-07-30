"""AIPOS-273F1: End-to-end tests for owner verification record routes.

Tests the full HTTP → controlled-execute → disk write flow.
P0 fix validation: approve/reject must write actual files to disk.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import (
    execute_dry_run,
    record_owner_verification,
)
from web.board.app import (
    _owner_verification_approve_route,
    _owner_verification_reject_route,
)


class TestOwnerVerificationE2E(unittest.TestCase):
    """End-to-end tests for owner verification HTTP routes -> disk writes."""

    def setUp(self) -> None:
        """Create isolated test repo."""
        self.test_dir = Path(tempfile.mkdtemp(prefix="lybra_test_verify_e2e_"))
        # Create minimal workspace structure for _resolve_repo_root
        self.queue_dir = self.test_dir / "5_tasks" / "queue"
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir = self.test_dir / "5_tasks" / "records" / "owner_verifications"
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        """Clean up test repo."""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_approve_route_writes_record_to_disk(self) -> None:
        """S1: POST approve → actual file on disk with correct frontmatter."""
        task_id = "TEST-APPROVE-001"
        payload = {
            "task_id": task_id,
            "owner_confirmed": True,
            "actor": "test_owner",
        }

        # Execute approve route
        response = _owner_verification_approve_route(payload, repo_root=self.test_dir)

        # Assert success
        self.assertTrue(response.get("ok"), f"Response not ok: {response}")
        self.assertIn(response.get("verdict"), ["OK", "PASS"], f"Verdict unexpected: {response}")
        self.assertTrue(response.get("data", {}).get("wrote"), "No write reported")

        # Assert file exists on disk
        task_records_dir = self.records_dir / task_id
        self.assertTrue(task_records_dir.exists(), f"Task records dir missing: {task_records_dir}")

        record_files = list(task_records_dir.glob("*.md"))
        self.assertEqual(len(record_files), 1, f"Expected 1 record file, found {len(record_files)}")

        record_file = record_files[0]
        content = record_file.read_text(encoding="utf-8")

        # Assert frontmatter fields present
        self.assertIn("decision: approve", content, "Missing decision field")
        self.assertIn("decided_via: web_session", content, "Missing decided_via field")
        self.assertIn("decided_at:", content, "Missing decided_at timestamp")
        self.assertIn("decided_by:", content, "Missing decided_by field")

    def test_reject_route_writes_record_to_disk(self) -> None:
        """S1: POST reject → actual file on disk with reason."""
        task_id = "TEST-REJECT-001"
        payload = {
            "task_id": task_id,
            "reason": "Does not meet acceptance criteria S1",
            "owner_confirmed": True,
            "actor": "test_owner",
        }

        # Execute reject route
        response = _owner_verification_reject_route(payload, repo_root=self.test_dir)

        # Assert success
        self.assertTrue(response.get("ok"), f"Response not ok: {response}")
        self.assertIn(response.get("verdict"), ["OK", "PASS"], f"Verdict unexpected: {response}")
        self.assertTrue(response.get("data", {}).get("wrote"), "No write reported")

        # Assert file exists on disk
        task_records_dir = self.records_dir / task_id
        self.assertTrue(task_records_dir.exists(), f"Task records dir missing: {task_records_dir}")

        record_files = list(task_records_dir.glob("*.md"))
        self.assertEqual(len(record_files), 1, f"Expected 1 record file, found {len(record_files)}")

        record_file = record_files[0]
        content = record_file.read_text(encoding="utf-8")

        # Assert frontmatter fields present
        self.assertIn("decision: reject", content, "Missing decision field")
        self.assertIn("reason: Does not meet acceptance criteria S1", content, "Missing reason field")
        self.assertIn("decided_via: web_session", content, "Missing decided_via field")
        self.assertIn("decided_at:", content, "Missing decided_at timestamp")
        self.assertIn("decided_by:", content, "Missing decided_by field")

    def test_reject_without_reason_blocks(self) -> None:
        """Contract: reject requires reason field."""
        task_id = "TEST-REJECT-NO-REASON"
        payload = {
            "task_id": task_id,
            "owner_confirmed": True,
            "actor": "test_owner",
        }

        response = _owner_verification_reject_route(payload, repo_root=self.test_dir)

        # Should block without reason
        self.assertFalse(response.get("ok"), "Should reject when reason missing")
        self.assertIn("reason is required", str(response).lower())

        # No file should be written
        task_records_dir = self.records_dir / task_id
        if task_records_dir.exists():
            record_files = list(task_records_dir.glob("*.md"))
            self.assertEqual(len(record_files), 0, "No file should be written when blocked")

    def test_controlled_execute_flow_approve(self) -> None:
        """Verify controlled-execute plumbing: dry-run → confirm → write."""
        task_id = "TEST-CE-APPROVE"
        actor = "test_owner"

        verification_payload = {
            "task_id": task_id,
            "decision": "approve",
            "reason": "",
            "decided_via": "web_session",
        }

        # Step 1: dry-run
        dry_run_response = record_owner_verification(
            verification_payload,
            dry_run=True,
            repo_root=self.test_dir,
            actor=actor,
        )

        self.assertTrue(dry_run_response.get("ok"), f"Dry-run failed: {dry_run_response}")
        self.assertIn(dry_run_response.get("verdict"), ["OK", "PASS"])
        dry_run_id = dry_run_response.get("dry_run_id")
        self.assertIsNotNone(dry_run_id, "No dry_run_id returned")

        # Step 2: confirm with owner token
        from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
        execute_response = execute_dry_run(
            dry_run_id,
            actor,
            owner_confirmation_token=OWNER_CONFIRMATION_TOKEN,
            repo_root=self.test_dir,
        )

        self.assertTrue(execute_response.get("ok"), f"Execute failed: {execute_response}")
        self.assertIn(execute_response.get("verdict"), ["OK", "PASS"])
        self.assertTrue(execute_response.get("data", {}).get("wrote"), "No write reported")

        # Step 3: verify file on disk
        task_records_dir = self.records_dir / task_id
        self.assertTrue(task_records_dir.exists())
        record_files = list(task_records_dir.glob("*.md"))
        self.assertEqual(len(record_files), 1)


if __name__ == "__main__":
    unittest.main()
