"""AIPOS-F266 — `serve stop` 停得住 supervisor: state 记 supervisor pid(+ 启动时刻指纹)、stop 先停 supervisor 再停子进程、重拉后回写子进程 pid。

依据(F265 交回遗留①): `_run_supervisor` 写入 service_state.json 的 processes 只有看板 / 门两个子进程 pid, 不含 supervisor 自身;
`stop_report` 只对 state 里的 pid 发 SIGTERM; supervisor 见子进程退出即按 1/2/4/8/16 秒退避重拉且新 pid 不回写 state →
`serve stop` 后看板 / 门被重拉、残留, 再次 stop 找不到新 pid(F263 的 3 个孤儿看板)。

靶场: 临时治理根 + 临时 connection.json + 临时端口, 不带 LYBRA_HOME_ROOT(门走 --service-connection-json, 不碰真实 home 根);
所有进程经 F265 共享夹具助手 fixture_procs 起与收(整组回收 + 登记标签), 只对本用例自起的进程发信号。

 ① 真 `serve start` → `serve stop`: supervisor / 看板 / 门全停, 之后不被重拉(登记标签下无存活进程), 端口释放; state 记 supervisor。
 ② 先 kill 看板制造重拉: state 原子回写新 pid(restarts=1), 再 stop 能停掉新看板。
 ③ pid 复用防误杀: state 记录的指纹与现进程不符 / 旧 state 命令行不符 = 只报不杀。
 ④ 常驻模式(systemd / launchd 直接拉起的 supervisor): stop 不发信号, 给 systemctl / launchctl 出口; 判定只认「父进程即管理器」。
 ⑤ stop 报告逐个列最终状态; 不理 SIGTERM = SIGKILL 停; 未能停止 = BLOCK; 旧 state(无指纹)按命令行核身。
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import service_mode  # noqa: E402
from tools.aipos_cli.loop_run_record import process_fingerprint  # noqa: E402
from tools.aipos_cli.runall_discovery import _pid_alive  # noqa: E402
from tools.aipos_cli.tests import fixture_procs  # noqa: E402

SLEEP_CODE = "import time; time.sleep(120)"
IGNORE_TERM_CODE = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"


def _show(text: str) -> None:
    print(text, flush=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"127.0.0.1:{port} 未在 {timeout}s 内就绪")


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_gone(pids: list[int], timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    alive = [p for p in pids if _pid_alive(p)]
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        alive = [p for p in alive if _pid_alive(p)]
    return alive


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "workspace"
    for state in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / state).mkdir(parents=True)
    return root, root / ".lybra" / "connection.json"


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT")}
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    return env


def _serve(root: Path, conn: Path, board_port: int, mcp_port: int) -> subprocess.Popen:
    return fixture_procs.spawn(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(root), "serve", "--connection-json", str(conn),
         "start", "--board-port", str(board_port), "--mcp-port", str(mcp_port)],
        cwd=str(REPO_ROOT), env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _cli_stop(conn: Path) -> tuple[int, dict]:
    out = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "serve", "--connection-json", str(conn), "stop", "--json"],
        cwd=str(REPO_ROOT), env=_env(), capture_output=True, text=True, timeout=60, check=False,
    )
    return out.returncode, json.loads(out.stdout)


def _state(conn: Path) -> dict:
    return json.loads((conn.parent / "service_state.json").read_text(encoding="utf-8"))


def _write_state(conn: Path, state: dict) -> None:
    conn.parent.mkdir(parents=True, exist_ok=True)
    (conn.parent / "service_state.json").write_text(json.dumps(state), encoding="utf-8")


def _by_name(report: dict) -> dict[str, dict]:
    return {str(row["name"]): row for row in report["processes"]}


# ===========================================================================
# ① 真 serve start → serve stop: 全停且不被重拉
# ===========================================================================
def test_item1_real_serve_stop_stops_supervisor_and_children_without_respawn(tmp_path):
    root, conn = _workspace(tmp_path)
    board_port, mcp_port = _free_port(), _free_port()
    proc = _serve(root, conn, board_port, mcp_port)
    try:
        _wait_port(mcp_port, 30)
        _wait_port(board_port, 30)
        state = _state(conn)
        sup = state["supervisor"]
        assert sup["pid"] == proc.pid and sup["fingerprint"] == process_fingerprint(proc.pid)
        assert sup["service_manager"] is None  # 测试进程拉起 = 前台, 不是常驻
        children = {p["name"]: p for p in state["processes"]}
        assert set(children) == {"board", "mcp"}
        for entry in children.values():
            assert entry["fingerprint"] == process_fingerprint(entry["pid"]) and entry["restarts"] == 0
        rc, report = _cli_stop(conn)
        _show(f"[①] stop 报告: rc={rc} verdict={report['verdict']} processes={report['processes']}")
        assert rc == 0 and report["verdict"] == "PASS" and report["ok"] is True
        rows = _by_name(report)
        assert rows["supervisor"]["status"] == "stopped" and rows["supervisor"]["signals"] == ["SIGTERM"]
        assert rows["board"]["status"] in ("stopped", "gone") and rows["mcp"]["status"] in ("stopped", "gone")
        tracked = [proc.pid] + [e["pid"] for e in children.values()]
        assert _wait_gone(tracked, 10) == []
        time.sleep(3)  # 跨过首档重拉退避(1 秒)+ 余量: 修前此时看板 / 门已被重拉
        survivors = fixture_procs.tagged(proc)
        _show(f"[①] stop 后 3 秒登记标签下存活: {survivors}")
        assert survivors == []
        assert _port_free(board_port) and _port_free(mcp_port)
    finally:
        fixture_procs.reap(proc, settle=5)
    assert proc.returncode == 0, proc.fixture_output


# ===========================================================================
# ② 先 kill 看板制造重拉: state 回写新 pid, 再 stop 能停掉
# ===========================================================================
def test_item2_respawned_child_pid_written_back_and_stopped(tmp_path):
    root, conn = _workspace(tmp_path)
    board_port, mcp_port = _free_port(), _free_port()
    proc = _serve(root, conn, board_port, mcp_port)
    try:
        _wait_port(mcp_port, 30)
        _wait_port(board_port, 30)
        old_board = {p["name"]: p for p in _state(conn)["processes"]}["board"]["pid"]
        os.kill(old_board, signal.SIGKILL)  # 本用例自起 serve 的看板子进程
        deadline = time.monotonic() + 15
        board = None
        while time.monotonic() < deadline:
            board = {p["name"]: p for p in _state(conn)["processes"]}["board"]
            if board["pid"] != old_board:
                break
            time.sleep(0.1)
        _show(f"[②] 看板 {old_board} 被 kill → state 回写 {board}")
        assert board and board["pid"] != old_board and board["restarts"] == 1
        assert board["fingerprint"] == process_fingerprint(board["pid"])
        assert not list((conn.parent).glob("service_state.json.tmp*"))  # 原子写: 无 .tmp 残留
        new_board = board["pid"]
        _wait_port(board_port, 30)
        rc, report = _cli_stop(conn)
        _show(f"[②] stop 报告: {report['processes']}")
        assert rc == 0 and report["verdict"] == "PASS"
        assert new_board in [row["pid"] for row in report["processes"]]
        assert _wait_gone([proc.pid, new_board], 10) == []
        time.sleep(3)
        assert fixture_procs.tagged(proc) == []
    finally:
        fixture_procs.reap(proc, settle=5)


# ===========================================================================
# ③ pid 复用防误杀
# ===========================================================================
def test_item3_pid_reuse_is_reported_not_killed(tmp_path):
    _root, conn = _workspace(tmp_path)
    victim = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        assert process_fingerprint(victim.pid)
        _write_state(conn, {
            "mode": "service_v0",
            "supervisor": {"pid": victim.pid, "fingerprint": "starttime:1", "started_at": "2026-01-01T00:00:00Z", "service_manager": None},
            "processes": [
                {"name": "board", "pid": victim.pid, "service_owned": True, "fingerprint": "starttime:2"},
                {"name": "mcp", "pid": victim.pid, "service_owned": True},  # 旧 state: 无指纹, 命令行不含 tools.mcp_server
            ],
        })
        with patch("tools.aipos_cli.service_mode.os.kill", wraps=os.kill) as kill:
            report = service_mode.stop_report(_root, connection_target=conn)
        _show(f"[③] pid 复用: {report['processes']}")
        assert [c for c in kill.call_args_list if c.args[0] == victim.pid] == []
        assert victim.poll() is None and _pid_alive(victim.pid)
        rows = report["processes"]
        assert [r["status"] for r in rows] == ["gone", "gone", "gone"]
        assert "复用" in rows[0]["reason"] and "复用" in rows[1]["reason"] and "复用" in rows[2]["reason"]
        assert report["verdict"] == "PASS" and report["stopped"] == []
    finally:
        fixture_procs.reap(victim)


def test_item3_recorded_none_fingerprint_is_not_killed(tmp_path):
    """记录时子进程已退出(指纹为空)、其 pid 现为他进程 = 不发信号。"""
    _root, conn = _workspace(tmp_path)
    other = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        _write_state(conn, {"supervisor": {"pid": 0}, "processes": [
            {"name": "board", "pid": other.pid, "service_owned": True, "fingerprint": None}]})
        report = service_mode.stop_report(_root, connection_target=conn)
        assert report["processes"][0]["status"] == "gone" and other.poll() is None
    finally:
        fixture_procs.reap(other)


# ===========================================================================
# ④ 常驻模式: 不发信号, 给出口; 判定只认父进程即管理器
# ===========================================================================
def test_item4_resident_supervisor_is_left_running_with_exit(tmp_path):
    _root, conn = _workspace(tmp_path)
    sup = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    child = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        manager = {"kind": "systemd", "unit": "lybra-gate.service", "stop_command": "systemctl --user stop lybra-gate.service"}
        _write_state(conn, {
            "supervisor": {"pid": sup.pid, "fingerprint": process_fingerprint(sup.pid), "service_manager": manager},
            "processes": [{"name": "board", "pid": child.pid, "service_owned": True, "fingerprint": process_fingerprint(child.pid)}],
        })
        with patch("tools.aipos_cli.service_mode.os.kill", wraps=os.kill) as kill:
            report = service_mode.stop_report(_root, connection_target=conn)
        _show(f"[④] 常驻: {report['blocking_reasons']}")
        assert kill.call_args_list == []
        assert sup.poll() is None and child.poll() is None
        assert report["verdict"] == "BLOCK" and report["ok"] is False and report["resident"] == manager
        assert report["blocking_reasons"][0]["fix_command"] == "systemctl --user stop lybra-gate.service"
        assert "systemctl --user stop lybra-gate.service" in report["blocking_reasons"][0]["message"]
        assert {r["status"] for r in report["processes"]} == {"left_running"}
    finally:
        fixture_procs.reap(sup)
        fixture_procs.reap(child)


def test_item4_resident_flag_on_dead_supervisor_falls_back_to_children(tmp_path):
    """常驻记录但 supervisor 已不在(单元已停): 不当常驻拦, 按子进程逐个停。"""
    _root, conn = _workspace(tmp_path)
    child = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        _write_state(conn, {
            "supervisor": {"pid": child.pid, "fingerprint": "starttime:1",
                           "service_manager": {"kind": "systemd", "unit": "x.service", "stop_command": "systemctl --user stop x.service"}},
            "processes": [{"name": "board", "pid": child.pid, "service_owned": True, "fingerprint": process_fingerprint(child.pid)}],
        })
        report = service_mode.stop_report(_root, connection_target=conn, child_grace=2)
        rows = _by_name(report)
        assert rows["supervisor"]["status"] == "gone" and rows["board"]["status"] == "stopped"
        assert report["verdict"] == "PASS"
        child.wait(timeout=5)
    finally:
        fixture_procs.reap(child)


def _fake_proc(tmp: Path, pid: int, ppid: int, comm_parent: str, cgroup: str) -> Path:
    root = tmp / "proc"
    (root / str(pid)).mkdir(parents=True, exist_ok=True)
    (root / str(ppid)).mkdir(parents=True, exist_ok=True)
    (root / str(pid) / "stat").write_text(f"{pid} (python3 (x)) S {ppid} {pid} {pid} 0 -1\n", encoding="utf-8")
    (root / str(pid) / "cgroup").write_text(cgroup, encoding="utf-8")
    (root / str(ppid) / "comm").write_text(comm_parent + "\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("ppid,comm,cgroup,expected", [
    (1999, "systemd", "0::/user.slice/user-1000.slice/user@1000.service/app.slice/lybra-gate.service\n",
     {"kind": "systemd", "unit": "lybra-gate.service", "stop_command": "systemctl --user stop lybra-gate.service"}),
    (1, "systemd", "0::/system.slice/lybra.service\n",
     {"kind": "systemd", "unit": "lybra.service", "stop_command": "systemctl stop lybra.service"}),
    (1999, "systemd", "12:pids:/x\n1:name=systemd:/user.slice/user-1000.slice/user@1000.service/hand.service\n",
     {"kind": "systemd", "unit": "hand.service", "stop_command": "systemctl --user stop hand.service"}),
    (4242, "bash", "0::/user.slice/user-1000.slice/user@1000.service/app.slice/lybra-gate.service\n", None),  # 门派生的子孙
    (1999, "systemd", "0::/user.slice/user-1000.slice/session-3.scope\n", None),
    (1999, "systemd", "0::/user.slice/user-1000.slice/user@1000.service/init.scope\n", None),
])
def test_item4_detect_systemd(tmp_path, ppid, comm, cgroup, expected):
    proc_root = _fake_proc(tmp_path, 5000, ppid, comm, cgroup)
    got = service_mode.detect_service_manager(pid=5000, platform="linux", proc_root=proc_root, env={})
    assert got == expected


def test_item4_detect_unreadable_proc_is_foreground(tmp_path):
    assert service_mode.detect_service_manager(pid=5000, platform="linux", proc_root=tmp_path / "nope", env={}) is None


@pytest.mark.parametrize("ppid,label,resident", [(1, "lybra-gate", True), (1, "0", False), (1, "", False), (777, "lybra-gate", False)])
def test_item4_detect_launchd(ppid, label, resident):
    got = service_mode.detect_service_manager(pid=5000, ppid=ppid, platform="darwin", env={"XPC_SERVICE_NAME": label})
    if resident:
        assert got["kind"] == "launchd" and got["unit"] == label and got["stop_command"].startswith("launchctl bootout gui/")
        assert got["stop_command"].endswith(f"/{label}")
    else:
        assert got is None


# ===========================================================================
# ⑤ 报告逐个列最终状态 / SIGKILL 路径 / 未能停止 = BLOCK / 旧 state
# ===========================================================================
def test_item5_supervisor_ignoring_sigterm_is_killed_then_children(tmp_path):
    _root, conn = _workspace(tmp_path)
    sup = fixture_procs.spawn([sys.executable, "-c", IGNORE_TERM_CODE])
    child = fixture_procs.spawn([sys.executable, "-c", IGNORE_TERM_CODE])
    try:
        time.sleep(0.5)  # 让两个进程先装好 SIG_IGN
        _write_state(conn, {
            "supervisor": {"pid": sup.pid, "fingerprint": process_fingerprint(sup.pid), "service_manager": None},
            "processes": [{"name": "mcp", "pid": child.pid, "service_owned": True, "fingerprint": process_fingerprint(child.pid)}],
        })
        report = service_mode.stop_report(_root, connection_target=conn, supervisor_grace=0.5, child_grace=0.5)
        rows = _by_name(report)
        _show(f"[⑤] 不理 SIGTERM: {report['processes']}")
        assert rows["supervisor"]["status"] == "stopped" and rows["supervisor"]["signals"] == ["SIGTERM", "SIGKILL"]
        assert rows["mcp"]["status"] == "stopped" and rows["mcp"]["signals"] == ["SIGTERM", "SIGKILL"]
        assert rows["mcp"]["label"] == "已停" and report["verdict"] == "PASS"
        assert sup.wait(timeout=5) == -signal.SIGKILL and child.wait(timeout=5) == -signal.SIGKILL
    finally:
        fixture_procs.reap(sup)
        fixture_procs.reap(child)


def test_item5_unstoppable_process_blocks(tmp_path):
    _root, conn = _workspace(tmp_path)
    child = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        _write_state(conn, {"supervisor": {"pid": 0}, "processes": [
            {"name": "board", "pid": child.pid, "service_owned": True, "fingerprint": process_fingerprint(child.pid)}]})
        real_kill = os.kill

        def deny(pid, sig):
            if pid == child.pid and sig != 0:
                raise PermissionError("EPERM (模拟)")
            return real_kill(pid, sig)

        with patch("tools.aipos_cli.service_mode.os.kill", side_effect=deny):
            report = service_mode.stop_report(_root, connection_target=conn)
        _show(f"[⑤] 未能停止: {report['processes']} / {report['blocking_reasons']}")
        row = report["processes"][0]
        assert row["status"] == "failed" and row["label"] == "未能停止" and "无权限" in row["reason"]
        assert report["verdict"] == "BLOCK" and report["ok"] is False
        assert "未能停止" in report["blocking_reasons"][0]["message"]
        assert child.poll() is None
    finally:
        fixture_procs.reap(child)


def test_item5_cli_exit_nonzero_on_block(tmp_path):
    """CLI: 有未能停止(这里 = 常驻模式未动)时退出码非 0。"""
    _root, conn = _workspace(tmp_path)
    sup = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        _write_state(conn, {"supervisor": {"pid": sup.pid, "fingerprint": process_fingerprint(sup.pid),
                                           "service_manager": {"kind": "launchd", "unit": "lybra-gate", "stop_command": "launchctl bootout gui/501/lybra-gate"}},
                            "processes": []})
        rc, report = _cli_stop(conn)
        assert rc == 1 and report["verdict"] == "BLOCK" and sup.poll() is None
    finally:
        fixture_procs.reap(sup)


def test_item5_legacy_state_verified_by_cmdline(tmp_path):
    """旧 state(F266 前: 无 supervisor 块、子进程无指纹): 命令行含子进程模块名才停; 并提示 supervisor 未记。"""
    _root, conn = _workspace(tmp_path)
    board_like = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE, "web.board.app"])
    stranger = fixture_procs.spawn([sys.executable, "-c", SLEEP_CODE])
    try:
        _write_state(conn, {"mode": "service_v0", "processes": [
            {"name": "board", "pid": board_like.pid, "service_owned": True},
            {"name": "mcp", "pid": stranger.pid, "service_owned": True},
            {"name": "not-ours", "pid": stranger.pid, "service_owned": False},
        ]})
        report = service_mode.stop_report(_root, connection_target=conn, child_grace=2)
        rows = _by_name(report)
        assert rows["board"]["status"] == "stopped" and board_like.wait(timeout=5) == -signal.SIGTERM
        assert rows["mcp"]["status"] == "gone" and stranger.poll() is None
        assert "not-ours" not in rows
        messages = " ".join(w["message"] for w in report["warnings"])
        assert "未记 supervisor" in messages and "按命令行核身" in messages
    finally:
        fixture_procs.reap(board_like)
        fixture_procs.reap(stranger)


def test_item5_unreadable_state_blocks_without_signals(tmp_path):
    _root, conn = _workspace(tmp_path)
    conn.parent.mkdir(parents=True)
    (conn.parent / "service_state.json").write_text("{not json", encoding="utf-8")
    with patch("tools.aipos_cli.service_mode.os.kill") as kill:
        report = service_mode.stop_report(_root, connection_target=conn)
    assert kill.call_args_list == [] and report["verdict"] == "BLOCK"


def test_item5_state_write_is_atomic_0600(tmp_path):
    root, conn = _workspace(tmp_path)
    service_mode._write_service_state(root, {"processes": []}, connection_target=conn)
    path = conn.parent / "service_state.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"processes": []}
    assert (path.stat().st_mode & 0o777) == 0o600
    assert sorted(p.name for p in conn.parent.iterdir()) == ["service_state.json"]


def test_item5_malformed_supervisor_pid_is_treated_as_unrecorded(tmp_path):
    _root, conn = _workspace(tmp_path)
    _write_state(conn, {"supervisor": {"pid": "not-a-pid"}, "processes": [{"name": "board", "pid": "x", "service_owned": True}]})
    with patch("tools.aipos_cli.service_mode.os.kill") as kill:
        report = service_mode.stop_report(_root, connection_target=conn)
    assert kill.call_args_list == [] and report["verdict"] == "PASS" and report["processes"] == []


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGHUP])
def test_item2_supervisor_interrupt_reaps_children_without_respawn(tmp_path, sig):
    """supervisor 只收到 SIGINT / SIGHUP(停止标志, 不再抛异常): 不重拉, 收子进程后正常退出(退出码 0)。"""
    root, conn = _workspace(tmp_path)
    board_port, mcp_port = _free_port(), _free_port()
    proc = _serve(root, conn, board_port, mcp_port)
    try:
        _wait_port(mcp_port, 30)
        _wait_port(board_port, 30)
        children = [p["pid"] for p in _state(conn)["processes"]]
        proc.send_signal(sig)
        assert proc.wait(timeout=20) == 0
        assert _wait_gone(children, 5) == []
        assert fixture_procs.tagged(proc) == []
    finally:
        fixture_procs.reap(proc)
