"""AIPOS-323 — lybra_task_progress 工具测试

测试覆盖：
1. 四类事件（started/progress/completed/blocked）落记录到 5_tasks/records/events/<task_id>/
2. scope 拒绝路径：无 task_progress scope 的 token 调用被拒绝
3. AIPOS-318 动词-scope 断言保持绿（task_progress 已注册到 ROLE_SPECS）
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.mcp_server.server import handle_request
from tools.aipos_cli.service_mode import ROLE_SPECS


class TaskProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        
        # Setup directory structure
        (self.repo_root / "5_tasks" / "records" / "events").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "queue" / "pending").mkdir(parents=True, exist_ok=True)
        
        # Create a mock task
        (self.repo_root / "5_tasks" / "queue" / "pending" / "AIPOS-TEST.md").write_text(
            "---\ntask_id: AIPOS-TEST\ntitle: Test Task\n---\n# Test\n",
            encoding="utf-8",
        )
        
        # Mock tokens (capability token format)
        self.executor_token = json.dumps({
            "role": "executor",
            "operations": ["queue_claim", "queue_return", "task_progress"],
            "token_ref": "test-executor",
            "expires_at": "2999-01-01T00:00:00Z"
        })
        
        self.auditor_token = json.dumps({
            "role": "auditor",
            "operations": ["queue_claim", "audit_verdict", "task_progress"],
            "token_ref": "test-auditor",
            "expires_at": "2999-01-01T00:00:00Z"
        })
        
        self.no_scope_token = json.dumps({
            "role": "copilot",
            "operations": ["queue_claim"],
            "token_ref": "test-no-scope",
            "expires_at": "2999-01-01T00:00:00Z"
        })

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _call_tool(self, token: str, task_id: str, event_type: str, **kwargs):
        """Helper to call lybra_task_progress"""
        arguments = {
            "task_id": task_id,
            "event_type": event_type,
            **kwargs
        }
        
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "lybra_task_progress",
                "arguments": arguments
            }
        }
        
        with patch.dict(os.environ, {
            "LYBRA_CAPABILITY_TOKEN": token,
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root)
        }):
            response = handle_request(request)
        
        assert response is not None
        return response

    def test_started_event_creates_record(self):
        """Test that 'started' event creates a record file"""
        result = self._call_tool(
            self.executor_token,
            "AIPOS-TEST",
            "started",
            actor="exec.lybra.test",
            model_self_reported="claude-sonnet-4"
        )
        
        self.assertIsInstance(result, dict)
        structured = result.get("result", {}).get("structuredContent", {})
        self.assertTrue(structured.get("ok"), f"Expected ok=true, got: {result}")
        
        # Check record file exists
        events_dir = self.repo_root / "5_tasks" / "records" / "events" / "AIPOS-TEST"
        self.assertTrue(events_dir.exists(), "Events directory should exist")
        
        records = list(events_dir.glob("*.md"))
        self.assertEqual(len(records), 1, "Should have exactly one record")
        
        # Verify record content (it's markdown with frontmatter)
        content = records[0].read_text()
        self.assertIn("AIPOS-TEST", content)
        self.assertIn("started", content)
        self.assertIn("exec.lybra.test", content)
        self.assertIn("claude-sonnet-4", content)

    def test_progress_event_with_summary(self):
        """Test 'progress' event with summary text"""
        result = self._call_tool(
            self.executor_token,
            "AIPOS-TEST",
            "progress",
            actor="exec.lybra.test",
            summary="Completed step 1 of 3"
        )
        
        structured = result.get("result", {}).get("structuredContent", {})
        self.assertTrue(structured.get("ok"))
        
        events_dir = self.repo_root / "5_tasks" / "records" / "events" / "AIPOS-TEST"
        records = list(events_dir.glob("*.md"))
        self.assertEqual(len(records), 1)
        
        content = records[0].read_text()
        self.assertIn("progress", content)
        self.assertIn("Completed step 1 of 3", content)

    def test_completed_event(self):
        """Test 'completed' event"""
        result = self._call_tool(
            self.executor_token,
            "AIPOS-TEST",
            "completed",
            actor="exec.lybra.test",
            summary="All tests passing"
        )
        
        structured = result.get("result", {}).get("structuredContent", {})
        self.assertTrue(structured.get("ok"))
        
        events_dir = self.repo_root / "5_tasks" / "records" / "events" / "AIPOS-TEST"
        content = list(events_dir.glob("*.md"))[0].read_text()
        self.assertIn("completed", content)

    def test_blocked_event(self):
        """Test 'blocked' event"""
        result = self._call_tool(
            self.executor_token,
            "AIPOS-TEST",
            "blocked",
            actor="exec.lybra.test",
            reason="Missing API credentials"
        )
        
        structured = result.get("result", {}).get("structuredContent", {})
        self.assertTrue(structured.get("ok"))
        
        events_dir = self.repo_root / "5_tasks" / "records" / "events" / "AIPOS-TEST"
        content = list(events_dir.glob("*.md"))[0].read_text()
        self.assertIn("blocked", content)
        self.assertIn("Missing API credentials", content)

    def test_auditor_can_report_progress(self):
        """Test that auditor role with task_progress scope can report"""
        result = self._call_tool(
            self.auditor_token,
            "AIPOS-TEST",
            "progress",
            actor="audit.lybra.test",
            summary="Audit in progress"
        )
        
        structured = result.get("result", {}).get("structuredContent", {})
        self.assertTrue(structured.get("ok"))

    def test_scope_denial(self):
        """Test that token without task_progress scope is denied"""
        result = self._call_tool(
            self.no_scope_token,
            "AIPOS-TEST",
            "started",
            actor="test.actor"
        )
        
        self.assertFalse(result.get("ok"), "Should be denied without task_progress scope")
        # Check in structuredContent for scope denial message
        msg = result.get("result", {}).get("structuredContent", {}).get("message", "")
        self.assertIn("task_progress", msg.lower())

    def test_multiple_events_append(self):
        """Test that multiple events append to same task directory"""
        # First event
        self._call_tool(self.executor_token, "AIPOS-TEST", "started", actor="exec.lybra.test")
        
        # Second event
        self._call_tool(self.executor_token, "AIPOS-TEST", "progress", actor="exec.lybra.test", summary="Step 1")
        
        # Third event
        self._call_tool(self.executor_token, "AIPOS-TEST", "completed", actor="exec.lybra.test")
        
        events_dir = self.repo_root / "5_tasks" / "records" / "events" / "AIPOS-TEST"
        records = sorted(events_dir.glob("*.md"))
        self.assertEqual(len(records), 3, "Should have three separate records")
        
        # Verify all three event types are present (file order is by timestamp, not type)
        all_content = " ".join(r.read_text() for r in records)
        self.assertIn("started", all_content)
        self.assertIn("progress", all_content)
        self.assertIn("completed", all_content)

    def test_task_progress_scope_in_role_specs(self):
        """AIPOS-318 regression: verify task_progress scope is granted to executor and auditor"""
        # ROLE_SPECS is a tuple of role dicts
        executor_spec = next((r for r in ROLE_SPECS if r["role"] == "executor"), None)
        auditor_spec = next((r for r in ROLE_SPECS if r["role"] == "auditor"), None)
        
        self.assertIsNotNone(executor_spec, "executor role must exist")
        self.assertIsNotNone(auditor_spec, "auditor role must exist")
        
        self.assertIn("task_progress", executor_spec["scopes"], 
                     "executor role must have task_progress scope")
        self.assertIn("task_progress", auditor_spec["scopes"],
                     "auditor role must have task_progress scope")


if __name__ == "__main__":
    unittest.main()
