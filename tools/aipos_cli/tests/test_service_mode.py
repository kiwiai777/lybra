from __future__ import annotations

import json
import os
import signal
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
    redacted_connection,
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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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


def _listen_bind_address(port: int) -> str | None:
    """Bind address (e.g. '0.0.0.0', '127.0.0.1') of a LISTEN socket on `port`, read from
    /proc/net/tcp (Linux); None if not found / unavailable. Used to prove a REAL 0.0.0.0 bind
    (AIPOS-259 S2: bind all-interfaces while URLs carry the advertise address)."""
    path = Path("/proc/net/tcp")
    if not path.exists():
        return None
    want = f":{port:04X}"
    for line in path.read_text(encoding="ascii", errors="replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        local, state = parts[1], parts[3]
        if local.endswith(want) and state == "0A":  # 0A = TCP_LISTEN
            ip_hex = local.split(":", 1)[0]
            octets = [int(ip_hex[i:i + 2], 16) for i in (6, 4, 2, 0)]  # /proc/net/tcp is LE per u32
            return ".".join(str(o) for o in octets)
    return None


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
        # AIPOS-349: the connection.json default is now the workspace path
        # (<workspace>/.lybra/connection.json). To keep these existing assertions (which check the
        # connection path / 0600 / gitignore relative to self.root) meaningful AND isolated from
        # the real ~/.lybra, point the runtime default at self.root for this class.
        self._rt_patcher = patch(
            "tools.aipos_cli.service_mode.runtime_connection_path",
            return_value=self.root / CONNECTION_REL,
        )
        self._rt_patcher.start()

    def tearDown(self) -> None:
        self._rt_patcher.stop()
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
        self.assertIn(".lybra/connection.json", (self.root / ".gitignore").read_text(encoding="utf-8"))
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

    # ---- AIPOS-238 (F-o3-13) serve lifecycle fail-closed hardening ----

    def test_ports_in_use_detects_active_listener_via_connect(self) -> None:
        # D1: connect() probe reports an ACTIVE listener; a free port is not reported.
        from tools.aipos_cli.service_mode import _ports_in_use

        free = _free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", free))
            srv.listen()
            occupied = _ports_in_use([("127.0.0.1", free, "mcp")])
            self.assertEqual(occupied, [("127.0.0.1", free, "mcp")])
        # after close, the port is free again → not reported
        self.assertEqual(_ports_in_use([("127.0.0.1", free, "mcp")]), [])

    def test_start_report_blocks_on_occupied_mcp_port_without_spawning(self) -> None:
        # D1: an occupied MCP port → BLOCK naming the port; NO children spawned (no service_state).
        board_port = _free_port()
        mcp_port = _free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            busy.bind(("127.0.0.1", mcp_port))
            busy.listen()
            result = start_report(
                self.root,
                board_host="127.0.0.1",
                board_port=board_port,
                mcp_host="127.0.0.1",
                mcp_port=mcp_port,
                start_processes=True,
            )
        self.assertEqual(result.get("verdict"), "BLOCK")
        self.assertFalse(result.get("ok"))
        msg = json.dumps(result)
        self.assertIn("already in use", msg)
        self.assertIn(str(mcp_port), msg)
        self.assertFalse(service_state_path(self.root).exists())  # never spawned

    def test_start_report_allows_non_loopback_host_without_blocking(self) -> None:
        # AIPOS-258 S2 (refined by 259): serve start with a CONCRETE non-loopback host (a tailnet
        # IP) is allowed — non-loopback is opt-in, no longer blocked as "loopback-only". A
        # wildcard (0.0.0.0) is exercised separately by the advertise/fail-closed tests below.
        # advertise defaults to the bind host, so URLs carry it directly (byte-identical shape).
        result = start_report(
            self.root,
            board_host="100.64.0.1",
            board_port=7117,
            mcp_host="100.64.0.1",
            mcp_port=7118,
            start_processes=False,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("verdict"), "PASS")
        self.assertEqual(result.get("blocking_reasons"), [])
        # connection.json (url/rpc_url/sse_url + host) reflects the requested host, not 127.0.0.1
        self.assertEqual(result["connection"]["board"]["host"], "100.64.0.1")
        self.assertEqual(result["connection"]["board"]["url"], "http://100.64.0.1:7117")
        self.assertEqual(result["connection"]["mcp"]["host"], "100.64.0.1")
        self.assertEqual(result["connection"]["mcp"]["rpc_url"], "http://100.64.0.1:7118/mcp")
        # advertise == bind -> no advertise_host field (byte-identical to pre-259 output)
        self.assertNotIn("advertise_host", result["connection"]["mcp"])

    def test_child_command_carries_default_loopback_host(self) -> None:
        # AIPOS-258 S1: default config (no host override) → child argv --host is 127.0.0.1.
        from tools.aipos_cli.service_mode import _build_child_commands
        config = build_connection_config(
            self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118,
        )
        board_cmd, mcp_cmd = _build_child_commands(
            config, child_workspace_root=self.root, connection_path_str=str(self.root / "connection.json"),
        )
        self.assertEqual(board_cmd[board_cmd.index("--host") + 1], "127.0.0.1")
        self.assertEqual(mcp_cmd[mcp_cmd.index("--host") + 1], "127.0.0.1")

    def test_child_command_passes_through_non_loopback_host(self) -> None:
        # AIPOS-258 S2: --mcp-host 0.0.0.0 / --board-host <tailnet addr> reach the child --host flags
        # (spawn command-line assertion, without spawning a real subprocess).
        from tools.aipos_cli.service_mode import _build_child_commands
        config = build_connection_config(
            self.root, board_host="100.64.0.1", board_port=7117, mcp_host="0.0.0.0", mcp_port=7118,
        )
        board_cmd, mcp_cmd = _build_child_commands(
            config, child_workspace_root=self.root, connection_path_str=str(self.root / "connection.json"),
        )
        # board binds the tailnet address, mcp binds all interfaces
        self.assertEqual(board_cmd[board_cmd.index("--host") + 1], "100.64.0.1")
        self.assertEqual(mcp_cmd[mcp_cmd.index("--host") + 1], "0.0.0.0")
        # argv shape unchanged: still --host <value>, --port <value>, --repo-root/--service-connection-json
        self.assertEqual(board_cmd[board_cmd.index("--port") + 1], "7117")
        self.assertEqual(mcp_cmd[mcp_cmd.index("--port") + 1], "7118")

    def test_start_host_param_overrides_stored_config_and_reaches_child(self) -> None:
        # AIPOS-259 S1 / F-258-1: with an EXISTING connection.json, an explicit --mcp-host WINS
        # over the stored host (the stored value used to win silently — 258's blind spot: its
        # tests all used fresh workspaces). The new host reaches the child --host flag AND is
        # written back to config (the process leaves a trail `serve status` then shows).
        from tools.aipos_cli.service_mode import _build_child_commands
        rotate_report(self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)
        result = start_report(
            self.root, board_host=None, board_port=7117, mcp_host="100.64.0.1", mcp_port=7118,
            start_processes=False,
        )
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["connection"]["mcp"]["host"], "100.64.0.1")
        # writeback trail: the on-disk config now reflects the override
        on_disk = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["mcp"]["host"], "100.64.0.1")
        self.assertEqual(on_disk["mcp"]["rpc_url"], "http://100.64.0.1:7118/mcp")
        # the child the supervisor WOULD spawn carries the NEW host (the 258 blind spot)
        _, mcp_cmd = _build_child_commands(
            on_disk, child_workspace_root=self.root, connection_path_str=str(connection_path(self.root)),
        )
        self.assertEqual(mcp_cmd[mcp_cmd.index("--host") + 1], "100.64.0.1")

    def test_start_with_no_host_param_leaves_stored_config_byte_identical(self) -> None:
        # AIPOS-259 S1: no host param at all -> the stored config is byte-identical (NOT rewritten)
        # and the STORED host is what the child binds (not the 127.0.0.1 default).
        from tools.aipos_cli.service_mode import _build_child_commands
        rotate_report(self.root, board_host="127.0.0.2", board_port=7117, mcp_host="127.0.0.2", mcp_port=7118)
        before = connection_path(self.root).read_bytes()
        result = start_report(
            self.root, board_host=None, board_port=7117, mcp_host=None, mcp_port=7118, start_processes=False,
        )
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(connection_path(self.root).read_bytes(), before)  # file untouched
        on_disk = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["mcp"]["host"], "127.0.0.2")  # stored, not the 127.0.0.1 default
        _, mcp_cmd = _build_child_commands(
            on_disk, child_workspace_root=self.root, connection_path_str=str(connection_path(self.root)),
        )
        self.assertEqual(mcp_cmd[mcp_cmd.index("--host") + 1], "127.0.0.2")

    def test_start_blocks_on_wildcard_bind_without_advertise(self) -> None:
        # AIPOS-259 S3 (fail-closed): bind 0.0.0.0 with no advertise -> BLOCK; nothing written.
        result = start_report(
            self.root, board_host=None, board_port=7117, mcp_host="0.0.0.0", mcp_port=7118,
            start_processes=False,
        )
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertFalse(result.get("ok"))
        msg = json.dumps(result)
        self.assertIn("0.0.0.0", msg)
        self.assertIn("--mcp-advertise", msg)
        self.assertFalse(connection_path(self.root).exists())  # fail-closed before any write

    def test_start_wildcard_bind_with_advertise_urls_use_advertise(self) -> None:
        # AIPOS-259 S2 (unit): bind 0.0.0.0 + advertise -> PASS; host stays 0.0.0.0 (bind), URLs
        # carry the ADVERTISE address, advertise_host stored (differs from bind).
        result = start_report(
            self.root, board_host="127.0.0.1", board_port=7117,
            mcp_host="0.0.0.0", mcp_port=7118, mcp_advertise_host="tailnet.example",
            start_processes=False,
        )
        self.assertEqual(result["verdict"], "PASS")
        mcp = result["connection"]["mcp"]
        self.assertEqual(mcp["host"], "0.0.0.0")
        self.assertEqual(mcp["advertise_host"], "tailnet.example")
        self.assertEqual(mcp["rpc_url"], "http://tailnet.example:7118/mcp")
        self.assertEqual(mcp["sse_url"], "http://tailnet.example:7118/sse")
        self.assertNotIn("0.0.0.0", mcp["rpc_url"])

    def test_serve_start_reaps_children_on_sigterm(self) -> None:
        # B/F-NEW-b: a plain SIGTERM to the supervisor reaps board+mcp (no orphans holding the port).
        board_port = _free_port()
        mcp_port = _free_port()
        env = os.environ.copy()
        env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        conn_json = str(self.root / CONNECTION_REL)
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(self.root),
             "serve", "--connection-json", conn_json, "start",
             "--board-port", str(board_port), "--mcp-port", str(mcp_port)],
            cwd=Path(__file__).resolve().parents[3], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child_pids: list[int] = []
        try:
            _wait_for_port(mcp_port)
            state = json.loads(service_state_path(self.root).read_text(encoding="utf-8"))
            child_pids = [int(p["pid"]) for p in state["processes"]]
            proc.send_signal(signal.SIGTERM)  # NOT SIGINT — the F-NEW-b path
            proc.wait(timeout=8)
            deadline = time.time() + 6
            alive = child_pids
            while time.time() < deadline:
                alive = [pid for pid in child_pids if _pid_alive(pid)]
                if not alive:
                    break
                time.sleep(0.2)
            self.assertEqual(alive, [], f"orphaned children survived SIGTERM: {alive}")
        finally:
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)

    def test_serve_stop_kills_without_home_root_or_project(self) -> None:
        # A/F-NEW-a: `serve stop --connection-json X` locates the state + kills recorded PIDs even
        # with NO LYBRA_HOME_ROOT / no established project (it must not fail-close on project resolve).
        board_port = _free_port()
        mcp_port = _free_port()
        env = os.environ.copy()
        env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        conn_json = str(self.root / CONNECTION_REL)
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(self.root),
             "serve", "--connection-json", conn_json, "start",
             "--board-port", str(board_port), "--mcp-port", str(mcp_port)],
            cwd=Path(__file__).resolve().parents[3], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child_pids: list[int] = []
        try:
            _wait_for_port(mcp_port)
            state = json.loads(service_state_path(self.root).read_text(encoding="utf-8"))
            child_pids = [int(p["pid"]) for p in state["processes"]]
            # stop in an env with NO LYBRA_HOME_ROOT and NO --workspace-root — only --connection-json.
            stop_env = {k: v for k, v in env.items() if k not in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT")}
            stopped = subprocess.run(
                [sys.executable, "-m", "tools.aipos_cli.aipos_cli",
                 "serve", "--connection-json", conn_json, "stop"],
                cwd=Path(__file__).resolve().parents[3], env=stop_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, check=False,
            )
            self.assertEqual(stopped.returncode, 0, f"stop failed: {stopped.stderr}")
            self.assertNotIn("PROJECT_NOT_ESTABLISHED", stopped.stdout + stopped.stderr)
            deadline = time.time() + 6
            alive = child_pids
            while time.time() < deadline:
                alive = [pid for pid in child_pids if _pid_alive(pid)]
                if not alive:
                    break
                time.sleep(0.2)
            self.assertEqual(alive, [], f"stop did not kill children: {alive}")
        finally:
            for pid in child_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)

    def test_service_mode_spawn_listens_and_resolves_workspace_without_shell_env(self) -> None:
        board_port = _free_port()
        mcp_port = _free_port()
        env = os.environ.copy()
        env.pop("AIPOS_WORKSPACE_ROOT", None)
        env.pop("LYBRA_MCP_TOKEN", None)
        env.pop("LYBRA_CAPABILITY_TOKEN", None)
        env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        # AIPOS-226: connection.json defaults to the global runtime root; pin it to a path under
        # the temp workspace via --connection-json so the spawned process stays isolated from the
        # real ~/.lybra (and matches the in-process patched connection_path).
        conn_json = str(self.root / CONNECTION_REL)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.aipos_cli.aipos_cli",
                "--workspace-root",
                str(self.root),
                "serve",
                "--connection-json",
                conn_json,
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
            config = json.loads(Path(conn_json).read_text(encoding="utf-8"))
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
                    "--connection-json",
                    conn_json,
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

    def test_service_mode_spawn_listens_on_non_loopback_bind(self) -> None:
        # AIPOS-259 S2 (local): bind 0.0.0.0 (all interfaces) + advertise 127.0.0.1. The child must
        # LISTEN on 0.0.0.0 (proven via /proc/net/tcp), while connection.json rpc_url/sse_url/url
        # carry the ADVERTISE address (127.0.0.1) — a local RPC via that advertise works because
        # 0.0.0.0 covers loopback. (Cross-machine tailnet curl is advisor acceptance.)
        board_port = _free_port()
        mcp_port = _free_port()
        env = os.environ.copy()
        env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        conn_json = str(self.root / CONNECTION_REL)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.aipos_cli.aipos_cli",
                "--workspace-root",
                str(self.root),
                "serve",
                "--connection-json",
                conn_json,
                "start",
                "--board-host",
                "0.0.0.0",
                "--board-advertise",
                "127.0.0.1",
                "--board-port",
                str(board_port),
                "--mcp-host",
                "0.0.0.0",
                "--mcp-advertise",
                "127.0.0.1",
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
            config = json.loads(Path(conn_json).read_text(encoding="utf-8"))
            # BIND is 0.0.0.0 (recorded + actually listening on all interfaces)
            self.assertEqual(config["board"]["host"], "0.0.0.0")
            self.assertEqual(config["mcp"]["host"], "0.0.0.0")
            self.assertEqual(_listen_bind_address(mcp_port), "0.0.0.0")
            # ADVERTISE is 127.0.0.1 — URLs carry it, NOT 0.0.0.0 (the F-258-2 fix)
            self.assertEqual(config["mcp"]["advertise_host"], "127.0.0.1")
            self.assertTrue(config["mcp"]["rpc_url"].startswith("http://127.0.0.1:"), config["mcp"]["rpc_url"])
            self.assertNotIn("0.0.0.0", config["mcp"]["rpc_url"])
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
                    "--connection-json",
                    conn_json,
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

    def test_start_existing_wildcard_config_blocks_without_advertise(self) -> None:
        # AIPOS-259: the real-machine record — a stored config with host=0.0.0.0 and a 0.0.0.0
        # rpc_url (exactly 258's regenerate output) — must BLOCK on a plain `serve start` until
        # an advertise is given. fail-closed catches the unusable URL instead of silently serving.
        config = build_connection_config(
            self.root, board_host="127.0.0.1", board_port=7117, mcp_host="0.0.0.0", mcp_port=7118,
        )
        write_connection_config(self.root, config)
        result = start_report(
            self.root, board_host=None, board_port=7117, mcp_host=None, mcp_port=7118, start_processes=False,
        )
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("--mcp-advertise", json.dumps(result))

    def test_start_existing_wildcard_config_fixed_by_advertise_param(self) -> None:
        # AIPOS-259: a stored 0.0.0.0 config is fixed by passing ONLY --mcp-advertise (no bind
        # override): bind stays 0.0.0.0, URLs become usable, the advertise is written back.
        config = build_connection_config(
            self.root, board_host="127.0.0.1", board_port=7117, mcp_host="0.0.0.0", mcp_port=7118,
        )
        write_connection_config(self.root, config)
        result = start_report(
            self.root, board_host=None, board_port=7117, mcp_host=None, mcp_port=7118,
            mcp_advertise_host="tailnet.example", start_processes=False,
        )
        self.assertEqual(result["verdict"], "PASS")
        mcp = result["connection"]["mcp"]
        self.assertEqual(mcp["host"], "0.0.0.0")  # bind preserved
        self.assertEqual(mcp["advertise_host"], "tailnet.example")
        self.assertEqual(mcp["rpc_url"], "http://tailnet.example:7118/mcp")

    def test_start_preserves_stored_advertise_on_no_param_restart(self) -> None:
        # AIPOS-259 S4: after rotate writes bind=0.0.0.0 + advertise=tailnet, a plain `serve
        # start` (no params) keeps rpc_url on the tailnet advertise (stored advertise carries
        # forward) and the child still binds 0.0.0.0 — rotate and start stay consistent.
        from tools.aipos_cli.service_mode import _build_child_commands
        rotate_report(
            self.root, board_host="127.0.0.1", board_port=7117,
            mcp_host="0.0.0.0", mcp_port=7118, mcp_advertise_host="tailnet.example",
        )
        before = connection_path(self.root).read_bytes()
        result = start_report(
            self.root, board_host=None, board_port=7117, mcp_host=None, mcp_port=7118, start_processes=False,
        )
        self.assertEqual(result["verdict"], "PASS")
        mcp = result["connection"]["mcp"]
        self.assertEqual(mcp["host"], "0.0.0.0")
        self.assertEqual(mcp["rpc_url"], "http://tailnet.example:7118/mcp")
        self.assertEqual(connection_path(self.root).read_bytes(), before)  # no param -> byte-identical
        on_disk = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        _, mcp_cmd = _build_child_commands(
            on_disk, child_workspace_root=self.root, connection_path_str=str(connection_path(self.root)),
        )
        self.assertEqual(mcp_cmd[mcp_cmd.index("--host") + 1], "0.0.0.0")  # bind preserved

    def test_rotate_fail_closed_and_advertise_field_rules(self) -> None:
        # AIPOS-259 S4 / fail-closed: rotate regenerates fresh, so a wildcard with no advertise
        # BLOCKs (never writes a 0.0.0.0-url config); with an advertise it passes. And the
        # advertise_host field is stored ONLY when it differs from bind (byte-identical otherwise).
        blocked = rotate_report(
            self.root, board_host="127.0.0.1", board_port=7117, mcp_host="0.0.0.0", mcp_port=7118,
        )
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertFalse(connection_path(self.root).exists())  # nothing written
        ok = rotate_report(
            self.root, board_host="127.0.0.1", board_port=7117,
            mcp_host="0.0.0.0", mcp_port=7118, mcp_advertise_host="tailnet.example",
        )
        self.assertEqual(ok["verdict"], "PASS")
        on_disk = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["mcp"]["host"], "0.0.0.0")
        self.assertEqual(on_disk["mcp"]["advertise_host"], "tailnet.example")
        # advertise == bind (concrete) -> NO advertise_host field (byte-identical to pre-259)
        cfg = build_connection_config(
            self.root, board_host="10.0.0.5", board_port=7117, mcp_host="10.0.0.5", mcp_port=7118,
        )
        self.assertNotIn("advertise_host", cfg["mcp"])
        self.assertEqual(cfg["mcp"]["rpc_url"], "http://10.0.0.5:7118/mcp")


class ConnectionLocationTests(unittest.TestCase):
    """AIPOS-226: connection.json default location is the global runtime root ~/.lybra/local/.

    Tokens must NOT be written under the truth home; --connection-json (connection_target) must
    still override. ★A1-adjacent: only the file LOCATION changes — scopes/minting are unchanged.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.fake_home = self.base / "userhome"
        self.fake_home.mkdir(parents=True)
        # a truth home (separate from the runtime root)
        self.truth_home = self.base / "truth"
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.truth_home / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rotate_writes_to_workspace_not_runtime_root(self) -> None:
        """AIPOS-349: rotate writes to <workspace>/.lybra/connection.json, NOT the global agent credential."""
        from tools.aipos_cli.service_mode import runtime_connection_path

        with patch.dict(os.environ, {"HOME": str(self.fake_home)}, clear=True):
            result = rotate_report(
                self.truth_home, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118
            )
            runtime = runtime_connection_path()

        self.assertEqual(result["verdict"], "PASS")
        # tokens landed in <workspace>/.lybra/connection.json (workspace path)
        ws_conn = self.truth_home / CONNECTION_REL
        self.assertTrue(ws_conn.is_file())
        self.assertEqual(_mode(ws_conn), REQUIRED_CONNECTION_MODE)
        # global agent credential NOT touched by workspace operation
        self.assertFalse(runtime.is_file())

    def test_connection_json_override_still_works(self) -> None:
        target = self.base / "custom" / "conn.json"
        with patch.dict(os.environ, {"HOME": str(self.fake_home)}, clear=True):
            result = rotate_report(
                self.truth_home,
                board_host="127.0.0.1",
                board_port=7117,
                mcp_host="127.0.0.1",
                mcp_port=7118,
                connection_target=target,
            )
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(target.is_file())
        self.assertEqual(_mode(target), REQUIRED_CONNECTION_MODE)
        # runtime root untouched when overridden
        self.assertFalse((self.fake_home / ".lybra" / "agent_credentials.json").exists())

    def test_scopes_unchanged_after_location_move(self) -> None:
        # ★A1: scope contents per role are unchanged by the location move.
        with patch.dict(os.environ, {"HOME": str(self.fake_home)}, clear=True):
            config = build_connection_config(
                self.truth_home, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118
            )
        scopes = {t["role"]: sorted(t["scopes"]) for t in config["tokens"]}
        self.assertEqual(scopes["executor"], sorted(["queue_claim", "queue_return"]))
        # AIPOS-250: owner also holds owner_decision_record so the owner-console can arm a
        # PreAuthorized autonomy envelope via serve-rotate creds (Owner-only write surface).
        self.assertEqual(
            scopes["owner"],
            sorted(["queue_claim", "queue_return", "owner_confirm", "draft_publish", "owner_decision_record"]),
        )
        self.assertEqual(scopes["copilot"], [])
        self.assertEqual(scopes["auditor"], sorted(["queue_claim", "audit_verdict"]))
        self.assertEqual(scopes["owner-dispatch"], ["audit_dispatch"])
        # AIPOS-249: planner holds ONLY draft_submit — no claim/return/confirm/publish/audit.
        self.assertEqual(scopes["planner"], ["draft_submit"])


class TokenProjectsMintEchoTests(unittest.TestCase):
    """AIPOS-228 Slice 4 — capability token `projects` dimension: mint + echo, ZERO enforcement."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "workspace"
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build(self, **overrides):
        kwargs = dict(board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118)
        kwargs.update(overrides)
        return build_connection_config(self.root, **kwargs)

    def test_with_project_mints_projects_into_every_role_token(self) -> None:
        # Identity: --project X -> every role token entry carries projects == [X] + marker false.
        cfg = self._build(project="lybra")
        for token in cfg["tokens"]:
            self.assertEqual(token.get("projects"), ["lybra"])
            self.assertEqual(token.get("projects_enforced"), True)

    def test_absence_is_byte_identical_no_projects_field(self) -> None:
        # No --project -> NO projects/projects_enforced field anywhere (back-compat byte-stable).
        cfg = self._build()
        for token in cfg["tokens"]:
            self.assertNotIn("projects", token)
            self.assertNotIn("projects_enforced", token)

    def test_redacted_echo_carries_projects_only_when_present(self) -> None:
        red_with = redacted_connection(self._build(project="lybra"))
        self.assertEqual(red_with["tokens"][0].get("projects"), ["lybra"])
        self.assertEqual(red_with["tokens"][0].get("projects_enforced"), True)
        self.assertNotIn("token", red_with["tokens"][0])  # secret discipline: raw token never echoed
        red_without = redacted_connection(self._build())
        self.assertNotIn("projects", red_without["tokens"][0])
        self.assertNotIn("projects_enforced", red_without["tokens"][0])

    def test_rotate_project_writes_0600_and_fingerprint_only(self) -> None:
        with patch(
            "tools.aipos_cli.service_mode.runtime_connection_path",
            return_value=self.root / CONNECTION_REL,
        ):
            result = rotate_report(
                self.root, board_host="127.0.0.1", board_port=7117, mcp_host="127.0.0.1", mcp_port=7118, project="lybra"
            )
            on_disk = json.loads(connection_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(_mode(connection_path(self.root)), REQUIRED_CONNECTION_MODE)
        for token in on_disk["tokens"]:
            self.assertEqual(token.get("projects"), ["lybra"])
        # raw token is on disk (0600) but NEVER in the rendered/redacted report
        rendered = render_connection_table(result)
        for token in on_disk["tokens"]:
            self.assertNotIn(token["token"], rendered)

    def test_projects_value_is_slug_space_not_normalized(self) -> None:
        # R-b: token.projects records the project selection verbatim in the resolve_active_project
        # slug space (no display-name normalization layer introduced).
        cfg = self._build(project="ai-project-os")
        self.assertEqual(cfg["tokens"][0].get("projects"), ["ai-project-os"])


if __name__ == "__main__":
    unittest.main()
