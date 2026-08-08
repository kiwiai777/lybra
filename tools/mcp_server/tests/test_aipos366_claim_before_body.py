"""AIPOS-366: claim-before-work hard enforcement — body requires valid claim record."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.mcp_server.server import handle_request


class ClaimBeforeBodyTests(unittest.TestCase):
    """AIPOS-366: Test that include_body=true requires a valid claim record."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "test_project" / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        
        # Write a test task
        self.task_id = "AIPOS-366-TEST"
        self.actor = "exec.test.agent"
        self.write_task()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self) -> None:
        """Create a pending task for testing."""
        (self.repo_root / "5_tasks" / "queue" / "pending" / f"{self.task_id.lower()}.md").write_text(
            "\n".join([
                "---",
                f"task_id: {self.task_id}",
                "title: Test Task for AIPOS-366",
                "project: test_project",
                f"assigned_to: {self.actor}",
                f"agent_instance: {self.actor}",
                "context_bundle: test_bundle",
                "task_mode: code",
                "priority: medium",
                "status: pending",
                "created_by: tester",
                "needs_owner: false",
                "---",
                "# Task Body",
                "",
                "This is the task body that should be protected.",
                "",
            ]),
            encoding="utf-8",
        )

    def write_claim_record(self, task_id: str, actor: str) -> None:
        """Create a claim record for the given task and actor."""
        claim_id = f"claim_{task_id}_20260808_test"
        claim_dir = self.repo_root / "5_tasks" / "records" / "claims" / task_id
        claim_dir.mkdir(parents=True, exist_ok=True)
        
        claim_path = claim_dir / f"{claim_id}.md"
        claim_path.write_text(
            "\n".join([
                "---",
                "record_type: claim_record",
                "event_type: mcp_queue_claim",
                f"claim_id: {claim_id}",
                f"task_id: {task_id}",
                f"task_path: 5_tasks/queue/claimed/{task_id.lower()}.md",
                "surface: mcp",
                "operation: queue_claim",
                "autonomy_mode: Supervised",
                f"actor: {actor}",
                f"canonical_agent_instance: {actor}",
                "owner_policy_ref: pol_test",
                "claimed_at: '2026-08-08T00:00:00Z'",  # Quote the datetime to keep it as string
                "from_state: pending",
                "to_state: claimed",
                "---",
                "# Claim Record",
                "",
            ]),
            encoding="utf-8",
        )

    def capability_token(self, operations: list[str], role: str | None = None, agent_instance: str | None = None) -> str:
        """Create a capability token for testing."""
        payload: dict[str, object] = {
            "token_ref": "cap_test_366",
            "operations": operations,
            "projects": ["test_project"],
            "expires_at": "2999-01-01T00:00:00Z",
        }
        if role is not None:
            payload["role"] = role
        if agent_instance is not None:
            payload["agent_instance"] = agent_instance
        return json.dumps(payload)

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Call an MCP tool."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        with patch.dict(
            os.environ,
            {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_ACTIVE_PROJECT": "test_project"},
        ):
            response = handle_request(request)
        assert response is not None
        return response

    def test_include_body_denied_without_claim(self) -> None:
        """AIPOS-366: include_body=true should be denied when no claim record exists."""
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_ACTIVE_PROJECT": "test_project",
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(
                operations=["queue_claim"],
                agent_instance=self.actor,
            ),
        }
        
        with patch.dict(os.environ, env, clear=True):
            response = self.call_tool(
                "lybra_task_preview",
                {"task_id": self.task_id, "include_body": True, "actor": self.actor},
            )
        
        # Should get structured content
        self.assertIn("result", response)
        result = response["result"]
        self.assertIn("structuredContent", result)
        
        structured = result["structuredContent"]
        self.assertFalse(structured["ok"])
        self.assertEqual(structured["error_code"], "CLAIM_REQUIRED")
        self.assertIn("claim record", structured["message"].lower())

    def test_include_body_allowed_with_claim(self) -> None:
        """AIPOS-366: include_body=true should be allowed when a valid claim record exists."""
        # Create claim record
        self.write_claim_record(self.task_id, self.actor)
        
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_ACTIVE_PROJECT": "test_project",
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(
                operations=["queue_claim"],
                agent_instance=self.actor,
            ),
        }
        
        with patch.dict(os.environ, env, clear=True):
            response = self.call_tool(
                "lybra_task_preview",
                {"task_id": self.task_id, "include_body": True, "actor": self.actor},
            )
        
        # Should succeed
        self.assertIn("result", response)
        result = response["result"]
        self.assertIn("structuredContent", result)
        
        structured = result["structuredContent"]
        self.assertTrue(structured["ok"])
        self.assertIn("body_markdown", structured.get("data", {}))

    def test_include_body_denied_for_different_actor(self) -> None:
        """AIPOS-366: include_body should be denied if claim exists for different actor."""
        # Create claim record for different actor
        different_actor = "exec.different.agent"
        self.write_claim_record(self.task_id, different_actor)
        
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_ACTIVE_PROJECT": "test_project",
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(
                operations=["queue_claim"],
                agent_instance=self.actor,
            ),
        }
        
        with patch.dict(os.environ, env, clear=True):
            response = self.call_tool(
                "lybra_task_preview",
                {"task_id": self.task_id, "include_body": True, "actor": self.actor},
            )
        
        # Should be denied
        self.assertIn("result", response)
        result = response["result"]
        self.assertIn("structuredContent", result)
        
        structured = result["structuredContent"]
        self.assertFalse(structured["ok"])
        self.assertEqual(structured["error_code"], "CLAIM_REQUIRED")

    def test_metadata_allowed_without_claim(self) -> None:
        """AIPOS-366: task metadata (without body) should be accessible without claim."""
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_ACTIVE_PROJECT": "test_project",
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(
                operations=["queue_claim"],
                agent_instance=self.actor,
            ),
        }
        
        with patch.dict(os.environ, env, clear=True):
            response = self.call_tool(
                "lybra_task_preview",
                {"task_id": self.task_id, "include_body": False, "actor": self.actor},
            )
        
        # Should succeed
        self.assertIn("result", response)
        result = response["result"]
        self.assertIn("structuredContent", result)
        
        structured = result["structuredContent"]
        self.assertTrue(structured["ok"])
        # Should NOT have body_markdown
        self.assertNotIn("body_markdown", structured.get("data", {}))


if __name__ == "__main__":
    unittest.main()
