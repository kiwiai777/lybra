"""AIPOS-283: Contract tests for queue_close and has_closure field in board UI.

Tests verify:
- S3: get_queue response includes has_closure flag for completed tasks with closure records
- S3: Board UI contract: tasks[].has_closure, tasks[].queue_state keys preserved
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import close_task, get_queue
from tools.aipos_cli.records import load_records


class QueueCloseContractTests(unittest.TestCase):
    """S3: Contract tests for queue_close and board UI has_closure flag."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "returns" / "TEST-001").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_queue_includes_has_closure_for_closed_tasks(self) -> None:
        """S3: get_queue must enrich completed tasks with has_closure=True when closure records exist."""
        # Create a claimed task with return record
        claimed_card = self.repo_root / "5_tasks" / "queue" / "claimed" / "test-001.md"
        claimed_card.write_text(
            "---\n"
            "task_id: TEST-001\n"
            "title: Test task\n"
            "project: lybra\n"
            "status: claimed\n"
            "claimed_by: exec.test\n"
            "claimed_at: '2026-07-30T12:00:00Z'\n"
            "claim_id: claim_TEST-001_20260730_120000Z_exec-test\n"
            "active_session_id: session_TEST-001_20260730_120000Z_exec-test\n"
            "assigned_to: exec.test\n"
            "agent_instance: exec.test\n"
            "context_bundle: exec.test\n"
            "task_mode: code\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# Test\n",
            encoding="utf-8",
        )
        
        # Create return record
        return_record = self.repo_root / "5_tasks" / "records" / "returns" / "TEST-001" / "return_test.md"
        return_record.write_text(
            "---\n"
            "record_type: return_record\n"
            "task_id: TEST-001\n"
            "return_id: return_test\n"
            "returned_at: '2026-07-30T13:00:00Z'\n"
            "---\n"
            "# Return\n",
            encoding="utf-8",
        )

        # Close the task (moves to completed/ and writes closure record)
        close_result = close_task(
            task_id="TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=self.repo_root,
        )
        self.assertTrue(close_result.get("ok"), f"Close failed: {close_result}")

        # Call get_queue
        response = get_queue(repo_root=self.repo_root)
        self.assertTrue(response["ok"])
        
        tasks = response["data"]["tasks"]
        test_task = next((t for t in tasks if t.get("task_id") == "TEST-001"), None)
        self.assertIsNotNone(test_task, "TEST-001 not found in get_queue response")
        
        # S3 contract: has_closure must be True for closed tasks
        self.assertTrue(
            test_task.get("has_closure"),
            "S3: get_queue must set has_closure=True for tasks with closure records"
        )
        self.assertEqual(test_task.get("queue_state"), "completed")

    def test_get_queue_no_has_closure_for_unclosed_completed_tasks(self) -> None:
        """S3: get_queue should NOT set has_closure for completed tasks without closure records."""
        # Create a completed task WITHOUT closure (legacy or manual move)
        completed_card = self.repo_root / "5_tasks" / "queue" / "completed" / "test-002.md"
        completed_card.write_text(
            "---\n"
            "task_id: TEST-002\n"
            "title: Completed without closure\n"
            "project: lybra\n"
            "status: completed\n"
            "assigned_to: exec.test\n"
            "agent_instance: exec.test\n"
            "context_bundle: exec.test\n"
            "task_mode: code\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# No closure\n",
            encoding="utf-8",
        )

        response = get_queue(repo_root=self.repo_root)
        self.assertTrue(response["ok"])
        
        tasks = response["data"]["tasks"]
        test_task = next((t for t in tasks if t.get("task_id") == "TEST-002"), None)
        self.assertIsNotNone(test_task)
        
        # Should NOT have has_closure (no closure record exists)
        self.assertNotIn("has_closure", test_task, "has_closure should not be set without closure record")

    def test_queue_state_key_preserved(self) -> None:
        """S3: Board UI contract — tasks[].queue_state must be present."""
        # Create a pending task
        pending_card = self.repo_root / "5_tasks" / "queue" / "pending" / "test-003.md"
        pending_card.write_text(
            "---\n"
            "task_id: TEST-003\n"
            "title: Pending\n"
            "project: lybra\n"
            "status: pending\n"
            "assigned_to: exec.test\n"
            "context_bundle: exec.test\n"
            "task_mode: code\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# Pending\n",
            encoding="utf-8",
        )

        response = get_queue(repo_root=self.repo_root)
        self.assertTrue(response["ok"])
        
        tasks = response["data"]["tasks"]
        test_task = next((t for t in tasks if t.get("task_id") == "TEST-003"), None)
        self.assertIsNotNone(test_task)
        
        # S3 contract: queue_state must exist (app.js reads task.queue_state)
        self.assertIn("queue_state", test_task, "Board UI contract: tasks[].queue_state required")
        self.assertEqual(test_task["queue_state"], "pending")


if __name__ == "__main__":
    unittest.main()
