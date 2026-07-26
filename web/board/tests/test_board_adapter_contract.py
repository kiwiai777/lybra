"""AIPOS-255: Contract tests to prevent board UI / adapter interface drift.

These tests pin the exact keys that the board UI reads from board_adapter responses.
Any rename on either side must update both the adapter AND these tests, making drift visible.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import get_queue, get_records
from tools.aipos_cli.task_loader import load_all_tasks
from tools.aipos_cli.validator import validate_tasks
from tools.aipos_cli.records import load_records


class BoardAdapterContractTests(unittest.TestCase):
    """Contract tests: board UI depends on these exact keys from board_adapter."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records" / "returns").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_queue_summary_provides_queue_state_counts(self) -> None:
        """AIPOS-255 F-BOARD-1: board UI (app.py:125-129, project-detail.html:369-372)
        reads summary keys 'pending', 'claimed', 'blocked', 'completed'.
        
        Validator must return these keys in summary, not just verdict counts.
        """
        # Create fixture tasks in different states
        (self.repo_root / "5_tasks" / "queue" / "pending" / "task-1.md").write_text(
            "---\ntask_id: TASK-1\ntitle: Pending Task\nstatus: pending\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-2.md").write_text(
            "---\ntask_id: TASK-2\ntitle: Claimed Task\nstatus: claimed\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "blocked" / "task-3.md").write_text(
            "---\ntask_id: TASK-3\ntitle: Blocked Task\nstatus: blocked\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "completed" / "task-4.md").write_text(
            "---\ntask_id: TASK-4\ntitle: Completed Task\nstatus: completed\n---\n",
            encoding="utf-8",
        )

        # Call get_queue (used by board /api/queue endpoint)
        response = get_queue(repo_root=self.repo_root)

        # Assert board UI contract
        self.assertTrue(response["ok"])
        summary = response["data"]["summary"]
        
        # Board UI reads these exact keys (app.py:128-129)
        self.assertIn("pending", summary, "Board UI reads summary['pending']")
        self.assertIn("claimed", summary, "Board UI reads summary['claimed']")
        self.assertIn("blocked", summary, "Board UI reads summary['blocked']")
        self.assertIn("completed", summary, "Board UI reads summary['completed']")
        
        # Verify counts match fixtures
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["claimed"], 1)
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["completed"], 1)

    def test_validator_validate_tasks_provides_queue_state_counts(self) -> None:
        """AIPOS-255 F-BOARD-1: validate_tasks (used by get_queue) must include
        queue_state counts in summary, not just verdict counts.
        """
        # Create fixture
        (self.repo_root / "5_tasks" / "queue" / "pending" / "task-p.md").write_text(
            "---\ntask_id: TASK-P\ntitle: Pending\nstatus: pending\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / "5_tasks" / "queue" / "claimed" / "task-c.md").write_text(
            "---\ntask_id: TASK-C\ntitle: Claimed\nstatus: claimed\n---\n",
            encoding="utf-8",
        )

        tasks = load_all_tasks(self.repo_root)
        report = validate_tasks(tasks)

        # Validator contract: must provide queue_state counts
        summary = report["summary"]
        self.assertIn("pending", summary)
        self.assertIn("claimed", summary)
        self.assertIn("blocked", summary)
        self.assertIn("completed", summary)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["claimed"], 1)

    def test_records_expose_actor_field_for_timeline(self) -> None:
        """AIPOS-255 F-BOARD-2: board UI (project-detail.html:602) reads
        record.actor for timeline rendering. Records must expose 'actor' field
        in session/return/audit records.
        """
        # Create session record with actor
        session_dir = self.repo_root / "5_tasks" / "records" / "sessions" / "TASK-S"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session_123.md").write_text(
            "---\n"
            "record_type: session_record\n"
            "session_id: session_123\n"
            "task_id: TASK-S\n"
            "actor: exec.lybra.test\n"
            "created_at: '2026-01-01T00:00:00Z'\n"
            "---\n",
            encoding="utf-8",
        )

        # Create return record with actor
        return_dir = self.repo_root / "5_tasks" / "records" / "returns" / "TASK-R"
        return_dir.mkdir(parents=True, exist_ok=True)
        (return_dir / "return_456.md").write_text(
            "---\n"
            "record_type: return_record\n"
            "return_id: return_456\n"
            "task_id: TASK-R\n"
            "actor: exec.lybra.test\n"
            "returned_at: '2026-01-01T01:00:00Z'\n"
            "---\n",
            encoding="utf-8",
        )

        records = load_records(self.repo_root)

        # Assert actor is exposed in session records
        sessions = records["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertIn("actor", sessions[0], "Session records must expose 'actor' field")
        self.assertEqual(sessions[0]["actor"], "exec.lybra.test")

        # Assert actor is exposed in return records
        returns = records["returns"]
        self.assertEqual(len(returns), 1)
        self.assertIn("actor", returns[0], "Return records must expose 'actor' field")
        self.assertEqual(returns[0]["actor"], "exec.lybra.test")

    def test_get_records_response_contract(self) -> None:
        """AIPOS-255 F-BOARD-2: get_records (used by /api/records endpoint)
        must return records with actor field for timeline UI.
        """
        session_dir = self.repo_root / "5_tasks" / "records" / "sessions" / "TASK-X"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session_xyz.md").write_text(
            "---\n"
            "record_type: session_record\n"
            "session_id: session_xyz\n"
            "task_id: TASK-X\n"
            "actor: auditor.lybra.test\n"
            "---\n",
            encoding="utf-8",
        )

        response = get_records(repo_root=self.repo_root)

        self.assertTrue(response["ok"])
        sessions = response["data"]["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertIn("actor", sessions[0], "Board timeline reads record.actor")
        self.assertEqual(sessions[0]["actor"], "auditor.lybra.test")


if __name__ == "__main__":
    unittest.main()
