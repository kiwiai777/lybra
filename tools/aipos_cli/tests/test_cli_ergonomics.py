from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli import aipos_cli
from tools.aipos_cli.service_mode import connection_path, start_report


class CliErgonomicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_cli(self, args: list[str]) -> tuple[int, str]:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = aipos_cli.main(args)
        return code, stdout.getvalue()

    def _make_workspace(self) -> Path:
        workspace = self.root / "workspace"
        for state in ("pending", "claimed", "completed", "blocked"):
            (workspace / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (workspace / ".lybra").mkdir(parents=True, exist_ok=True)
        (workspace / ".lybra" / "config.json").write_text(
            json.dumps(
                {
                    "workspace_root": ".",
                    "board": {"host": "127.0.0.1", "port": 7117},
                    "mcp": {
                        "host": "127.0.0.1",
                        "port": 7118,
                        "transport_token_env": "LYBRA_MCP_TOKEN",
                        "capability_token_env": "LYBRA_CAPABILITY_TOKEN",
                    },
                }
            ),
            encoding="utf-8",
        )
        return workspace

    def test_top_level_init_writes_workspace_config(self) -> None:
        output = self.root / "new-workspace"
        code, raw = self._run_cli(["init", str(output), "--project-id", "demo_project", "--json"])
        result = json.loads(raw)

        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertTrue((output / "5_tasks" / "queue" / "pending").is_dir())
        config = json.loads((output / ".lybra" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["board"]["port"], 7117)
        self.assertEqual(config["mcp"]["port"], 7118)
        self.assertEqual(config["mcp"]["transport_token_env"], "LYBRA_MCP_TOKEN")
        self.assertNotIn("secret-transport-token", json.dumps(config))

    def test_mcp_config_prints_env_refs_and_fingerprints_without_raw_tokens(self) -> None:
        workspace = self._make_workspace()
        env = {
            "LYBRA_MCP_TOKEN": "secret-transport-token",
            "LYBRA_CAPABILITY_TOKEN": json.dumps({"token_ref": "dev", "operations": ["queue_claim"]}),
        }
        with patch.dict(os.environ, env, clear=True):
            code, raw = self._run_cli(["mcp-config", "--workspace-root", str(workspace), "--json"])
        result = json.loads(raw)

        self.assertEqual(code, 0)
        self.assertEqual(result["endpoint"], "http://127.0.0.1:7118/mcp")
        self.assertEqual(result["server_env"]["LYBRA_MCP_TOKEN"], "${LYBRA_MCP_TOKEN}")
        self.assertEqual(result["client"]["authorization_header"], "Bearer ${LYBRA_MCP_TOKEN}")
        self.assertNotIn("secret-transport-token", raw)
        self.assertNotIn("queue_claim", result["server_env"]["LYBRA_CAPABILITY_TOKEN"])
        self.assertTrue(result["fingerprints"]["LYBRA_MCP_TOKEN"].startswith("sha256:"))

    def test_global_workspace_root_is_accepted_before_subcommand(self) -> None:
        workspace = self._make_workspace()
        env = {
            "LYBRA_MCP_TOKEN": "secret-transport-token",
            "LYBRA_CAPABILITY_TOKEN": json.dumps({"token_ref": "dev", "operations": ["queue_claim"]}),
        }
        with patch.dict(os.environ, env, clear=True):
            code, raw = self._run_cli(["--workspace-root", str(workspace), "mcp-config", "--json"])
        result = json.loads(raw)

        self.assertEqual(code, 0)
        self.assertEqual(result["workspace_root"], str(workspace.resolve()))
        self.assertEqual(result["endpoint"], "http://127.0.0.1:7118/mcp")

    def test_board_wrapper_uses_workspace_discovery_and_config_defaults(self) -> None:
        workspace = self._make_workspace()
        with patch("web.board.app.run_server") as run_server:
            code, _raw = self._run_cli(["board", "--workspace-root", str(workspace)])

        self.assertEqual(code, 0)
        run_server.assert_called_once_with(host="127.0.0.1", port=7117, repo_root=workspace.resolve())

    def test_mcp_wrapper_uses_workspace_discovery_and_config_defaults(self) -> None:
        workspace = self._make_workspace()
        with patch("tools.mcp_server.http_sse.run_http_server", return_value=0) as run_http_server:
            code, _raw = self._run_cli(["mcp", "--workspace-root", str(workspace)])

        self.assertEqual(code, 0)
        config = run_http_server.call_args.args[0]
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 7118)

    def test_serve_status_outputs_redacted_connection_config(self) -> None:
        workspace = self._make_workspace()
        start_report(
            workspace,
            board_host="127.0.0.1",
            board_port=7117,
            mcp_host="127.0.0.1",
            mcp_port=7118,
            start_processes=False,
        )
        config = json.loads(connection_path(workspace).read_text(encoding="utf-8"))
        raw_tokens = [item["token"] for item in config["tokens"]]

        code, raw = self._run_cli(["--workspace-root", str(workspace), "serve", "status", "--json"])
        result = json.loads(raw)

        self.assertEqual(code, 0)
        self.assertEqual(result["operation"], "serve_status")
        self.assertEqual(result["connection"]["mcp"]["rpc_url"], "http://127.0.0.1:7118/mcp")
        for token in raw_tokens:
            self.assertNotIn(token, raw)


if __name__ == "__main__":
    unittest.main()
