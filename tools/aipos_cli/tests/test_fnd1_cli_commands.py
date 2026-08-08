"""AIPOS-FND-1: Unit tests for five new CLI commands.

Tests parameter parsing and backend function calls for:
- task-progress
- queue-return
- bench-audit
- owner-verify
- converge
- mark-concluded
"""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from tools.aipos_cli.aipos_cli import main


class TestNewCLICommands(unittest.TestCase):
    """Test the five new CLI commands added in AIPOS-FND-1."""

    @patch('tools.aipos_cli.aipos_cli._find_repo_root_for_args')
    @patch('tools.aipos_cli.task_progress_writer.write_task_progress_event')
    def test_task_progress_parsing(self, mock_write, mock_root):
        """Test task-progress command parameter parsing."""
        mock_root.return_value = Path("/tmp/test")
        mock_write.return_value = {"ok": True, "operation": "task_progress"}
        
        args = [
            "task-progress",
            "--task-id", "TEST-1",
            "--actor", "test-actor",
            "--agent-instance", "test-instance",
            "--event-type", "started",
            "--summary", "Test summary",
            "--json"
        ]
        
        exit_code = main(args)
        
        self.assertEqual(exit_code, 0)
        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args[1]
        self.assertEqual(call_kwargs["task_id"], "TEST-1")
        self.assertEqual(call_kwargs["actor"], "test-actor")
        self.assertEqual(call_kwargs["event_type"], "started")
        self.assertEqual(call_kwargs["summary"], "Test summary")

    @patch('tools.aipos_cli.aipos_cli._find_repo_root_for_args')
    @patch('tools.aipos_cli.board_adapter.return_task')
    def test_queue_return_parsing(self, mock_return, mock_root):
        """Test queue-return command parameter parsing."""
        mock_root.return_value = Path("/tmp/test")
        mock_return.return_value = {"ok": True, "verdict": "PASS"}
        
        args = [
            "queue-return",
            "--task-id", "TEST-1",
            "--actor", "test-actor",
            "--agent-instance", "test-instance",
            "--owner-policy-ref", "pol_test",
            "--result-summary", "Test result",
            "--dry-run",
            "--json"
        ]
        
        exit_code = main(args)
        
        self.assertEqual(exit_code, 0)
        mock_return.assert_called_once()
        call_kwargs = mock_return.call_args[1]
        self.assertEqual(call_kwargs["task_id"], "TEST-1")
        self.assertEqual(call_kwargs["actor"], "test-actor")
        self.assertEqual(call_kwargs["result_summary"], "Test result")
        self.assertTrue(call_kwargs["dry_run"])

    @patch('tools.aipos_cli.aipos_cli._find_repo_root_for_args')
    @patch('tools.aipos_cli.bench_audit_writer.build_bench_audit_record')
    def test_bench_audit_parsing(self, mock_build, mock_root):
        """Test bench-audit command parameter parsing."""
        mock_root.return_value = Path("/tmp/test")
        mock_build.return_value = {"verdict": "PASS"}
        
        args = [
            "bench-audit",
            "--task-id", "TEST-1",
            "--actor", "test-actor",
            "--conclusion", "pass",
            "--dry-run",
            "--json"
        ]
        
        exit_code = main(args)
        
        self.assertEqual(exit_code, 0)
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args[1]
        self.assertEqual(call_kwargs["payload"]["task_id"], "TEST-1")
        self.assertEqual(call_kwargs["payload"]["conclusion"], "pass")
        self.assertTrue(call_kwargs["dry_run"])

    @patch('tools.aipos_cli.aipos_cli._find_repo_root_for_args')
    @patch('tools.aipos_cli.owner_verification_writer.build_owner_verification_record')
    def test_owner_verify_parsing(self, mock_build, mock_root):
        """Test owner-verify command parameter parsing."""
        mock_root.return_value = Path("/tmp/test")
        mock_build.return_value = {"verdict": "PASS"}
        
        args = [
            "owner-verify",
            "--task-id", "TEST-1",
            "--actor", "owner",
            "--decision-type", "approve",
            "--decision-summary", "Test approval",
            "--dry-run",
            "--json"
        ]
        
        exit_code = main(args)
        
        self.assertEqual(exit_code, 0)
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args[1]
        self.assertEqual(call_kwargs["payload"]["task_id"], "TEST-1")
        self.assertEqual(call_kwargs["payload"]["decision"], "approve")

    @patch('tools.aipos_cli.aipos_cli._find_repo_root_for_args')
    @patch('tools.aipos_cli.board_adapter.converge_r_cards')
    def test_converge_parsing(self, mock_converge, mock_root):
        """Test converge command parameter parsing."""
        mock_root.return_value = Path("/tmp/test")
        mock_converge.return_value = {"ok": True, "converged": []}
        
        args = [
            "converge",
            "--actor", "system",
            "--dry-run",
            "--json"
        ]
        
        exit_code = main(args)
        
        self.assertEqual(exit_code, 0)
        mock_converge.assert_called_once()
        call_kwargs = mock_converge.call_args[1]
        self.assertEqual(call_kwargs["actor"], "system")
        self.assertTrue(call_kwargs["dry_run"])

    @patch('tools.aipos_cli.aipos_cli._find_repo_root_for_args')
    @patch('tools.aipos_cli.board_adapter.mark_concluded_task')
    def test_mark_concluded_parsing(self, mock_mark, mock_root):
        """Test mark-concluded command parameter parsing."""
        mock_root.return_value = Path("/tmp/test")
        mock_mark.return_value = {"ok": True, "verdict": "PASS"}
        
        args = [
            "mark-concluded",
            "--task-id", "TEST-1",
            "--actor", "system",
            "--conclusion-note", "Test note",
            "--dry-run",
            "--json"
        ]
        
        exit_code = main(args)
        
        self.assertEqual(exit_code, 0)
        mock_mark.assert_called_once()
        call_kwargs = mock_mark.call_args[1]
        self.assertEqual(call_kwargs["task_id"], "TEST-1")
        self.assertEqual(call_kwargs["conclusion_note"], "Test note")


if __name__ == "__main__":
    unittest.main()
