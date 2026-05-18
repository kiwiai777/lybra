from __future__ import annotations

import os
import tempfile
import unittest
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.mcp_server.server import handle_request, serve
from tools.mcp_server.tools import TOOL_DESCRIPTORS


class McpToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles" / "examples").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles" / "examples" / "dev.codex.local.md").write_text(
            "\n".join(
                [
                    "role_instance: dev.codex.local",
                    "environment: local_wsl_ubuntu",
                    "description: test bundle",
                    "allowed_task_modes:",
                    "  - code",
                    "preferred_model_tiers:",
                    "  - L2",
                    "allowed_model_tiers:",
                    "  - L2",
                    "memory_access:",
                    "  - 2_projects/lybra/",
                    "output_target:",
                    "  - repository",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.write_task()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self) -> None:
        (self.repo_root / "5_tasks" / "queue" / "pending" / "example.md").write_text(
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-MCP-READ",
                    "title: MCP Read Test",
                    "project: lybra",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/mcp_server/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/mcp_server/",
                    "memory_scope: mcp tests",
                    "---",
                    "Read-only MCP test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}):
            response = handle_request(request)
        assert response is not None
        return response

    def data_paths(self) -> list[str]:
        return sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))

    def assert_tool_ok(self, response: dict[str, object]) -> dict[str, object]:
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertIsInstance(result, dict)
        structured = result["structuredContent"]  # type: ignore[index]
        self.assertIsInstance(structured, dict)
        return structured  # type: ignore[return-value]

    def test_tools_list_contains_only_mvp_read_tools(self) -> None:
        names = [tool["name"] for tool in TOOL_DESCRIPTORS]
        self.assertEqual(
            names,
            [
                "lybra_queue_list",
                "lybra_task_preview",
                "lybra_validate",
                "lybra_context_pack_build",
            ],
        )

    def test_queue_list_is_read_only(self) -> None:
        before = self.data_paths()
        structured = self.assert_tool_ok(self.call_tool("lybra_queue_list"))
        after = self.data_paths()
        self.assertEqual(before, after)
        self.assertEqual(structured["operation"], "get_queue")
        self.assertIn("tasks", structured["data"])  # type: ignore[operator]

    def test_task_preview_happy_path_and_missing_selector_error(self) -> None:
        structured = self.assert_tool_ok(
            self.call_tool("lybra_task_preview", {"task_id": "AIPOS-MCP-READ", "actor": "dev.codex.local"})
        )
        self.assertEqual(structured["operation"], "get_preview")
        self.assertEqual(structured["data"]["task_id"], "AIPOS-MCP-READ")  # type: ignore[index]

        error = self.assert_tool_ok(self.call_tool("lybra_task_preview", {}))
        self.assertFalse(error["ok"])
        self.assertEqual(error["verdict"], "BLOCK")

    def test_validate_happy_path(self) -> None:
        structured = self.assert_tool_ok(self.call_tool("lybra_validate"))
        self.assertEqual(structured["operation"], "get_validate")
        self.assertIn("summary", structured["data"])  # type: ignore[operator]

    def test_context_pack_build_happy_path_and_missing_selector_error(self) -> None:
        structured = self.assert_tool_ok(self.call_tool("lybra_context_pack_build", {"task_id": "AIPOS-MCP-READ"}))
        self.assertEqual(structured["operation"], "context_pack_preview")
        self.assertEqual(structured["data"]["task"]["task_id"], "AIPOS-MCP-READ")  # type: ignore[index]
        self.assertFalse(structured["data"]["writes_enabled"])  # type: ignore[index]

        error = self.assert_tool_ok(self.call_tool("lybra_context_pack_build", {}))
        self.assertFalse(error["ok"])
        self.assertEqual(error["verdict"], "BLOCK")

    def test_unknown_tool_returns_json_rpc_error(self) -> None:
        stdin = StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lybra_queue_claim","arguments":{}}}\n'
        )
        stdout = StringIO()
        stderr = StringIO()

        serve(stdin, stdout, stderr)

        response = json.loads(stdout.getvalue())
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32601)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
