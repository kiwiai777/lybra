from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import request

from tools.aipos_cli.service_mode import (
    CONNECTION_REL,
    LOCAL_DIR_REL,
    REQUIRED_CONNECTION_MODE,
    REQUIRED_LOCAL_DIR_MODE,
    build_connection_config,
    connection_path,
    render_connection_table,
    rotate_report,
    secret_fingerprint,
    service_state_path,
    start_report,
    status_report,
    stop_report,
    write_connection_config,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, *, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    last_error: OSError | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for 127.0.0.1:{port}: {last_error}")


def _post_rpc(port: int, token: str, payload: dict[str, object]) -> dict[str, object]:
    req = request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


class ServiceModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "workspace"
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_start_creates_gitignored_0600_connection_without_printing_raw_tokens(self) -> None:
        result = start_report(
            self.root,
            board_host="127.0.0.1",
            board_port=7117,
            mcp_host="127.0.0.1",
            mcp_port=7118,
            start_processes=False,
        )
        raw = json.dumps(result, sort_keys=True)
        config = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        rendered = render_connection_table(result)

        self.assertTrue(result["ok"])
        self.assertEqual(_mode(self.root / LOCAL_DIR_REL), REQUIRED_LOCAL_DIR_MODE)
        self.assertEqual(_mode(self.root / CONNECTION_REL), REQUIRED_CONNECTION_MODE)
        self.assertIn(".lybra/local/", (self.root / ".gitignore").read_text(encoding="utf-8"))
        for token in config["tokens"]:
            self.assertNotIn(token["token"], raw)
            self.assertNotIn(token["token"], rendered)
            self.assertIn(token["fingerprint"], rendered)

    def test_start_and_status_warn_when_proxy_may_intercept_loopback_without_printing_proxy_value(self) -> None:
        env = {
            "HTTPS_PROXY": "http://proxy.internal.example:8080",
            "NO_PROXY": "example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            status = status_report(self.root)
            start = start_report(
                self.root,
                board_host="127.0.0.1",
                board_port=7117,
                mcp_host="127.0.0.1",
                mcp_port=7118,
                start_processes=False,
            )
        status_text = json.dumps(status)
        start_text = json.dumps(start)
        self.assertIn("NO_PROXY=127.0.0.1,localhost,::1", status_text)
        self.assertIn("NO_PROXY=127.0.0.1,localhost,::1", start_text)
        self.assertNotIn("proxy.internal.example", status_text)
        self.assertNotIn("proxy.internal.example", start_text)

    def test_rotate_blocks_existing_overbroad_secret_paths_with_actionable_fix(self) -> None:
        local_dir = self.root / LOCAL_DIR_REL
        local_dir.mkdir(parents=True)
        os.chmod(local_dir, 0o777)
        path = self.root / CONNECTION_REL
        path.write_text("{}", encoding="utf-8")
        os.chmod(path, 0o644)

        result = rotate_report(self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)

        self.assertEqual(result["verdict"], "BLOCK")
        text = json.dumps(result)
        self.assertIn("chmod 700", text)
        self.assertIn("chmod 600", text)
        self.assertIn("observed_mode", text)

    def test_start_blocks_existing_overbroad_secret_paths_before_loading_tokens(self) -> None:
        local_dir = self.root / LOCAL_DIR_REL
        local_dir.mkdir(parents=True)
        os.chmod(local_dir, 0o777)
        path = self.root / CONNECTION_REL
        path.write_text(
            json.dumps({"tokens": [{"role": "executor", "token": "raw-token", "scopes": ["queue_claim"]}]}),
            encoding="utf-8",
        )
        os.chmod(path, 0o644)

        result = start_report(
            self.root,
            board_host="127.0.0.1",
            board_port=7117,
            mcp_host="127.0.0.1",
            mcp_port=7118,
            start_processes=False,
        )
        raw = json.dumps(result)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("chmod 700", raw)
        self.assertIn("chmod 600", raw)
        self.assertNotIn("raw-token", raw)

    def test_non_posix_permission_paths_warn_instead_of_blocking(self) -> None:
        local_dir = self.root / LOCAL_DIR_REL
        local_dir.mkdir(parents=True)
        os.chmod(local_dir, 0o777)
        path = self.root / CONNECTION_REL
        path.write_text("{}", encoding="utf-8")
        os.chmod(path, 0o644)

        with patch("tools.aipos_cli.service_mode._is_probably_non_posix", return_value=True):
            result = rotate_report(self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["warnings"])
        self.assertIn("not be faithfully enforceable", json.dumps(result))

    def test_status_warns_on_overbroad_secret_paths_without_blocking(self) -> None:
        config = build_connection_config(self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)
        write_connection_config(self.root, config)
        os.chmod(self.root / LOCAL_DIR_REL, 0o777)
        os.chmod(self.root / CONNECTION_REL, 0o644)

        result = status_report(self.root)

        self.assertEqual(result["verdict"], "PASS")
        self.assertFalse(result["blocking_reasons"])
        self.assertGreaterEqual(len(result["warnings"]), 2)

    def test_rotate_changes_tokens_and_prints_only_fingerprints(self) -> None:
        first = rotate_report(self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)
        first_config = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        second = rotate_report(self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)
        second_config = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        rendered = render_connection_table(second)

        first_tokens = {item["role"]: item["token"] for item in first_config["tokens"]}
        second_tokens = {item["role"]: item["token"] for item in second_config["tokens"]}
        self.assertNotEqual(first_tokens, second_tokens)
        for token in second_tokens.values():
            self.assertNotIn(token, rendered)
            self.assertIn(secret_fingerprint(token), rendered)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])

    def test_stop_only_targets_service_owned_processes(self) -> None:
        (self.root / LOCAL_DIR_REL).mkdir(parents=True)
        state = {
            "mode": "service_v0",
            "processes": [
                {"name": "board", "pid": 111, "service_owned": True},
                {"name": "not-ours", "pid": 222, "service_owned": False},
                {"name": "mcp", "pid": 333, "service_owned": True},
            ],
        }
        service_state_path(self.root).write_text(json.dumps(state), encoding="utf-8")

        with patch("os.kill") as kill:
            result = stop_report(self.root)

        self.assertTrue(result["ok"])
        self.assertEqual([call.args[0] for call in kill.call_args_list], [111, 333])

    def test_service_mode_spawn_listens_and_resolves_workspace_without_shell_env(self) -> None:
        board_port = _free_port()
        mcp_port = _free_port()
        env = os.environ.copy()
        env.pop("AIPOS_WORKSPACE_ROOT", None)
        env.pop("LYBRA_MCP_TOKEN", None)
        env.pop("LYBRA_CAPABILITY_TOKEN", None)
        env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.aipos_cli.aipos_cli",
                "--workspace-root",
                str(self.root),
                "serve",
                "start",
                "--board-port",
                str(board_port),
                "--mcp-port",
                str(mcp_port),
            ],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout = ""
        stderr = ""
        try:
            _wait_for_port(mcp_port)
            config = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
            executor_token = next(item["token"] for item in config["tokens"] if item["role"] == "executor")
            response = _post_rpc(
                mcp_port,
                executor_token,
                {
                    "jsonrpc": "2.0",
                    "id": "queue-list",
                    "method": "tools/call",
                    "params": {"name": "lybra_queue_list", "arguments": {}},
                },
            )
            structured = response["result"]["structuredContent"]  # type: ignore[index]
            self.assertEqual(structured["operation"], "get_queue")  # type: ignore[index]
            self.assertEqual(structured["scope_basis"]["role"], "executor")  # type: ignore[index]
            self.assertNotIn("error", response)
        finally:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.aipos_cli.aipos_cli",
                    "--workspace-root",
                    str(self.root),
                    "serve",
                    "stop",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[3],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                stdout, stderr = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 0, (stdout, stderr))


if __name__ == "__main__":
    unittest.main()
