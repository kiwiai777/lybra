"""AIPOS-F268 — 常驻单元带用户 PATH(看板自检 / 拉 pi 不误报)+ stop 限时进声明 + supervisor 正常退出清 state。

依据(F266 / F267 交回遗留): `serve install`(F263)渲染的单元只设 LYBRA_HOME_ROOT / PYTHONPATH, 不设 PATH; systemd --user / launchd
缺省 PATH 不含 ~/.local/bin、npm 全局 bin、nvm / volta → 常驻门内看板自检把已装的 pi 报 ✗、loop 拉 pi 找不到; F266 stop 三个限时写死;
supervisor 正常退出后 state 不清理, serve status 仍列已退出 pid。

靶场: 临时 HOME + 临时 XDG_CONFIG_HOME + 临时治理根 / 工作区; PATH 只含本用例自建 bin(python3 / git / node 链到真实可执行, pi 为夹具替身,
systemctl / launchctl 为记录调用的替身)——不碰真实用户 systemd / launchd; 起进程一律经 F265 共享助手 fixture_procs(整组回收)。

 ① 单元带安装者 PATH: systemd / launchd 预演全文各一(含 PATH 行), 逐项列 PATH 目录, 标 pi / node / git 目录是否在内。
 ② 假 pi 只在安装者 PATH: 单元 PATH 含其目录; 从 PATH 去掉 = 预演提示(不阻断); 已装单元 PATH 与当前不一致 = 「重装以刷新」出口。
 ③ setup check: 单元 PATH 不含 pi 目录 = ⚠ + 出口; 含 = ✓; F268 前装的单元(未写 PATH)= ⚠。
 ④ stop 限时只读声明 config.schema serve.stop(缺省 = 原值; 改声明值生效; 写坏 = fail-closed)。
 ⑤ supervisor 正常退出(停止信号)后 state 标已停止(保留最后 pid), serve status 显示「未运行」; 别的 supervisor 的 state 不动。
"""
from __future__ import annotations

import copy
import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import service_mode  # noqa: E402
from tools.aipos_cli.loop_run_record import process_fingerprint  # noqa: E402
from tools.aipos_cli.tests import fixture_procs  # noqa: E402

IGNORE_TERM_CODE = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
PI_OK = ('case "$1" in --version) echo 0.85.1;; --list-models) printf "provider  model  context\\nkiwiai  m-one  100K\\n";; esac\n'
         'exit 0\n')


def _show(text: str) -> None:
    print(text, flush=True)


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _bin(tmp_path: Path) -> Path:
    """基础 bin: 真实 python3 / git / node 的链接 + systemctl / launchctl 替身(记录调用; is-active 回 active)。不含 pi。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("python3", "git", "node"):
        real = shutil.which(name)
        if real is None:
            pytest.fail(f"{name} 不在 PATH: 本用例要把它链进临时 bin")
        os.symlink(os.path.realpath(real), bindir / name)
    for svc in ("systemctl", "launchctl"):
        _script(bindir / svc, f'echo "{svc} $*" >> "$FAKE_SVC_LOG"\ncase "$*" in *is-active*) echo active;; esac\nexit 0\n')
    return bindir


def _pi_dir(tmp_path: Path) -> Path:
    """假 pi 单放一个目录(模拟 npm 全局 bin / ~/.local/bin: 只在安装者 PATH 里)。"""
    pidir = tmp_path / "npm-global" / "bin"
    pidir.mkdir(parents=True)
    _script(pidir / "pi", PI_OK)
    return pidir


def _env(tmp_path: Path, path: str) -> dict[str, str]:
    (tmp_path / "home").mkdir(exist_ok=True)
    return {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "PATH": path, "LANG": "C.UTF-8",
            "PYTHONPATH": str(REPO_ROOT), "FAKE_SVC_LOG": str(tmp_path / "svc.log"), "LYBRA_HOME_ROOT": str(tmp_path / "hr")}


def _cli(tmp_path: Path, env: dict[str, str], *args: str) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", *args], cwd=str(tmp_path), env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def _gov(tmp_path: Path) -> Path:
    gov = tmp_path / "hr" / "demo"
    (gov / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
    return gov


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unit(tmp_path: Path) -> Path:
    return tmp_path / "xdg" / "systemd" / "user" / "lybra-gate.service"


# ---------------------------------------------------------------------------
# ① 单元带安装者 PATH(systemd / launchd 预演全文)
# ---------------------------------------------------------------------------

def test_item1_systemd_dry_run_writes_installer_path(tmp_path):
    bindir, pidir = _bin(tmp_path), _pi_dir(tmp_path)
    path = f"{bindir}{os.pathsep}{pidir}"
    env = _env(tmp_path, path)
    gov = _gov(tmp_path)
    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--dry-run")
    _show("---- ① serve install --dry-run · systemd 原文 ----\n" + out)
    assert rc == 0 and not _unit(tmp_path).exists() and not (tmp_path / "svc.log").exists(), "预演零写入、不跑服务命令"
    assert f"Environment=PATH={path}" in out
    assert f"单元 PATH(取自安装者当前 PATH, 共 2 项, 原样写入):\n  1. {bindir}\n  2. {pidir}" in out
    assert f"  pi → {pidir / 'pi'}(目录 {pidir}: 在单元 PATH 内 ✓)" in out
    assert f"  git → {bindir / 'git'}(目录 {bindir}: 在单元 PATH 内 ✓)" in out
    assert f"  node → {bindir / 'node'}(目录 {bindir}: 在单元 PATH 内 ✓)" in out
    assert "预演无拒因" in out


def test_item1_launchd_dry_run_plist_has_path(tmp_path, monkeypatch):
    from tools.aipos_cli.gate_resident import install, render_resident_report

    bindir, pidir = _bin(tmp_path), _pi_dir(tmp_path)
    path = f"{pidir}{os.pathsep}{bindir}"
    home = tmp_path / "machome"
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(tmp_path / "hr"))
    plan = install(_gov(tmp_path), confirm=False, platform="darwin", env={"HOME": str(home), "PATH": path})
    text = render_resident_report(plan)
    _show("---- ① serve install 预演 · launchd(macOS)原文 ----\n" + text)
    assert plan["ok"] and not Path(plan["unit_path"]).exists()
    data = plistlib.loads(plan["unit_text"].encode("utf-8"))
    assert data["EnvironmentVariables"]["PATH"] == path
    assert "<key>PATH</key>" in text and f"<string>{path}</string>" in text
    assert f"  pi → {pidir / 'pi'}(目录 {pidir}: 在单元 PATH 内 ✓)" in text


def test_item1_path_comes_from_declaration_not_hardcoded():
    from tools.aipos_cli.gate_resident import gate_resident_declaration

    decl = gate_resident_declaration()
    assert decl["environment"]["PATH"] == "{install_path}"
    assert decl["path_check"]["tools"] == ["pi", "node", "git"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "gate_resident.py").read_text(encoding="utf-8")
    for literal in ("/.local/bin", "/usr/local/bin", ".nvm", ".volta", "npm-global"):
        assert literal not in src, f"gate_resident.py 不得写死目录 {literal}"


def test_item1_empty_installer_path_not_written(tmp_path):
    from tools.aipos_cli.gate_resident import render_unit

    u = render_unit(workspace_root=Path("/g"), home_root="/h", code_root=Path("/c"), exec_argv=["/py", "serve", "start"],
                    platform="linux", env={"HOME": str(tmp_path), "PATH": ""})
    assert "Environment=PATH" not in u["text"] and u["skipped_env"] == ["PATH"]


# ---------------------------------------------------------------------------
# ② 假 pi 只在安装者 PATH / 去掉后提示 / 已装单元 PATH 不一致 = 重装以刷新
# ---------------------------------------------------------------------------

def test_item2_pi_missing_from_installer_path_is_hint_not_block(tmp_path):
    bindir = _bin(tmp_path)
    _pi_dir(tmp_path)  # pi 在盘上, 但不在安装者 PATH
    env = _env(tmp_path, str(bindir))
    gov = _gov(tmp_path)
    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--dry-run")
    _show("---- ② 安装者 PATH 去掉 pi 目录 · 预演原文 ----\n" + out)
    assert rc == 0, "找不到 pi 只提示, 不阻断"
    assert "  pi → 安装者 PATH 里找不到 ⚠" in out
    assert "⚠ 安装者当前 PATH 里找不到 pi: 写进单元的 PATH 同样找不到" in out
    assert "预演无拒因" in out


def test_item2_installed_unit_path_drift_gives_refresh_exit(tmp_path):
    bindir, pidir = _bin(tmp_path), _pi_dir(tmp_path)
    gov = _gov(tmp_path)
    env_old = _env(tmp_path, str(bindir))
    rc, out = _cli(tmp_path, env_old, "serve", "--workspace-root", str(gov), "install", "--confirm")
    assert rc == 0 and _unit(tmp_path).is_file()
    assert f"Environment=PATH={bindir}\n" in _unit(tmp_path).read_text(encoding="utf-8")
    rc, same = _cli(tmp_path, env_old, "serve", "--workspace-root", str(gov), "install", "--dry-run")
    assert rc == 1 and "UNIT_EXISTS_MANAGED" in same and "重装以刷新" not in same, "PATH 一致 = 原出口"

    env_new = _env(tmp_path, f"{bindir}{os.pathsep}{pidir}")
    rc, out = _cli(tmp_path, env_new, "serve", "--workspace-root", str(gov), "install", "--dry-run")
    _show("---- ② 已装单元 PATH 与当前不一致 · 预演原文 ----\n" + out)
    assert rc == 1 and "UNIT_EXISTS_MANAGED" in out
    assert f"已装单元 {_unit(tmp_path)} 的 PATH 与当前 PATH 不一致(单元: {bindir}; 当前: {bindir}{os.pathsep}{pidir})" in out
    assert f"pi 目录 {pidir}: 不在已装单元 PATH 内 ⚠" in out
    refresh = ("重装以刷新 PATH: `lybra serve uninstall --confirm` 后在能找到 pi 的终端重跑 "
               f"`lybra serve --workspace-root {gov.resolve()} install --confirm`")
    assert f"出口(重装以刷新): {refresh}" in out and f"  出口: {refresh}" in out
    assert _unit(tmp_path).read_text(encoding="utf-8").count("Environment=PATH=") == 1, "预演不改已装单元"


# ---------------------------------------------------------------------------
# ③ setup check 对照单元 PATH
# ---------------------------------------------------------------------------

def test_item3_setup_check_warns_when_unit_path_lacks_pi_dir(tmp_path):
    bindir, pidir = _bin(tmp_path), _pi_dir(tmp_path)
    gov = _gov(tmp_path)
    rc, _ = _cli(tmp_path, _env(tmp_path, str(bindir)), "serve", "--workspace-root", str(gov), "install", "--confirm")
    assert rc == 0
    env_user = _env(tmp_path, f"{bindir}{os.pathsep}{pidir}")  # 用户终端: pi 在 npm 全局 bin
    rc, out = _cli(tmp_path, env_user, "setup", "check", "--gate-url", f"http://127.0.0.1:{_free_port()}")
    _show("---- ③ setup check · 单元 PATH 不含 pi 目录 原文 ----\n" + out)
    assert "✓ pi(审计 / 非代码执行工位): 0.85.1" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("⚠ 门常驻:"))
    assert f"{_unit(tmp_path)}(本命令生成; 在跑); 单元 PATH 不含 pi 所在目录 {pidir}(单元 PATH: {bindir})" in line
    assert "重跑 lybra serve install --confirm 刷新 PATH(已装单元先 lybra serve uninstall --confirm" in out
    rc, js = _cli(tmp_path, env_user, "setup", "check", "--json", "--gate-url", f"http://127.0.0.1:{_free_port()}")
    item = {it["id"]: it for it in json.loads(js)["items"]}["gate_resident"]
    assert item["status"] == "warn" and "刷新 PATH" in item["exit"]


def test_item3_setup_check_ok_when_unit_path_has_pi_dir(tmp_path):
    bindir, pidir = _bin(tmp_path), _pi_dir(tmp_path)
    gov = _gov(tmp_path)
    env = _env(tmp_path, f"{bindir}{os.pathsep}{pidir}")
    rc, _ = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--confirm")
    assert rc == 0
    rc, out = _cli(tmp_path, env, "setup", "check", "--gate-url", f"http://127.0.0.1:{_free_port()}")
    _show("---- ③ setup check · 单元 PATH 含 pi 目录 原文 ----\n" + out)
    assert f"✓ 门常驻: {_unit(tmp_path)}(本命令生成; 在跑)" in out


def test_item3_setup_check_legacy_unit_without_path(tmp_path):
    """F268 前装的本命令单元(未写 PATH): pi 目录无从在内 = ⚠。"""
    from tools.aipos_cli.gate_resident import gate_resident_declaration

    bindir, pidir = _bin(tmp_path), _pi_dir(tmp_path)
    unit = _unit(tmp_path)
    unit.parent.mkdir(parents=True)
    unit.write_text(f"# {gate_resident_declaration()['managed_marker']}\n[Service]\nEnvironment=LYBRA_HOME_ROOT=/x\n"
                    "ExecStart=/usr/bin/python3 -m tools.aipos_cli.aipos_cli serve start\n", encoding="utf-8")
    env = _env(tmp_path, f"{bindir}{os.pathsep}{pidir}")
    rc, out = _cli(tmp_path, env, "setup", "check", "--gate-url", f"http://127.0.0.1:{_free_port()}")
    _show("---- ③ setup check · F268 前单元(未写 PATH)原文 ----\n" + out)
    assert f"单元 PATH 不含 pi 所在目录 {pidir}(单元 PATH: (未写 PATH: AIPOS-F268 前安装, 沿用服务管理器缺省))" in out


def test_item3_unit_environment_parses_quoted_systemd_and_launchd(tmp_path):
    from tools.aipos_cli.gate_resident import render_unit, unit_environment

    path = "/opt/My Tools 100%/bin:/usr/bin"
    u = render_unit(workspace_root=Path("/g"), home_root="/h r", code_root=Path("/c"), exec_argv=["/py", "serve", "start"],
                    platform="linux", env={"HOME": str(tmp_path), "PATH": path})
    assert 'Environment="PATH=/opt/My Tools 100%%/bin:/usr/bin"' in u["text"]
    f = tmp_path / "u.service"
    f.write_text(u["text"] + 'Environment=A=1 "B=two words"\n', encoding="utf-8")
    env = unit_environment(f, "systemd_user")
    assert env == {"LYBRA_HOME_ROOT": "/h r", "PYTHONPATH": "/c", "PATH": path, "A": "1", "B": "two words"}
    m = render_unit(workspace_root=Path("/g"), home_root="/h", code_root=Path("/c"), exec_argv=["/py", "serve", "start"],
                    platform="darwin", env={"HOME": str(tmp_path), "PATH": path})
    p = tmp_path / "u.plist"
    p.write_text(m["text"], encoding="utf-8")
    assert unit_environment(p, "launchd")["PATH"] == path
    bad = tmp_path / "bad.service"
    bad.write_text('Environment="PATH=/unterminated\n', encoding="utf-8")
    assert unit_environment(bad, "systemd_user") is None


# ---------------------------------------------------------------------------
# ④ stop 限时只读声明
# ---------------------------------------------------------------------------

def _patched_schema(monkeypatch, **stop_values):
    import tools.schema_loader as schema_loader

    real = schema_loader.load_schema

    def fake(schema_type, repo_root=None):
        data = real(schema_type, repo_root)
        if schema_type != "config":
            return data
        data = copy.deepcopy(data)
        data["serve"]["stop"].update(stop_values)
        return data

    monkeypatch.setattr(schema_loader, "load_schema", fake)


def test_item4_defaults_equal_previous_constants():
    assert service_mode.serve_stop_timeouts() == {"supervisor_grace_seconds": 15.0, "child_grace_seconds": 5.0,
                                                  "kill_confirm_seconds": 3.0}
    src = (REPO_ROOT / "tools" / "aipos_cli" / "service_mode.py").read_text(encoding="utf-8")
    for gone in ("STOP_SUPERVISOR_GRACE_SECONDS", "STOP_CHILD_GRACE_SECONDS", "STOP_KILL_CONFIRM_SECONDS", "timeout=5)"):
        assert gone not in src, f"{gone} 应已改读声明"


def test_item4_changed_declaration_takes_effect(tmp_path, monkeypatch):
    """改声明值 → stop 按新值等待(记录 _wait_gone 的限时)+ 真进程: 不理 SIGTERM 的 supervisor / 子进程按声明限时被 SIGKILL。"""
    _patched_schema(monkeypatch, supervisor_grace_seconds=0.4, child_grace_seconds=0.3, kill_confirm_seconds=2.5)
    waits: list[float] = []
    real_wait = service_mode._wait_gone

    def spy(pid, fingerprint, seconds):
        waits.append(seconds)
        return real_wait(pid, fingerprint, seconds)

    monkeypatch.setattr(service_mode, "_wait_gone", spy)
    conn = tmp_path / "ws" / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True)
    sup = fixture_procs.spawn([sys.executable, "-c", IGNORE_TERM_CODE])
    child = fixture_procs.spawn([sys.executable, "-c", IGNORE_TERM_CODE])
    try:
        time.sleep(0.5)  # 让两个进程先装好 SIG_IGN
        (conn.parent / "service_state.json").write_text(json.dumps({
            "supervisor": {"pid": sup.pid, "fingerprint": process_fingerprint(sup.pid), "service_manager": None},
            "processes": [{"name": "mcp", "pid": child.pid, "service_owned": True, "fingerprint": process_fingerprint(child.pid)}],
        }), encoding="utf-8")
        started = time.monotonic()
        report = service_mode.stop_report(tmp_path / "ws", connection_target=conn)
        elapsed = time.monotonic() - started
        _show(f"[④] 声明改为 0.4 / 0.3 / 2.5 秒: 等待序列 {waits}, 用时 {elapsed:.2f}s, processes={report['processes']}")
        assert waits == [0.4, 2.5, 0.3, 2.5]
        assert elapsed < 5, "未按改后的短限时等(原值 15 + 5 秒)"
        rows = {r["name"]: r for r in report["processes"]}
        assert rows["supervisor"]["signals"] == ["SIGTERM", "SIGKILL"] and rows["mcp"]["signals"] == ["SIGTERM", "SIGKILL"]
        assert report["verdict"] == "PASS"
    finally:
        fixture_procs.reap(sup)
        fixture_procs.reap(child)


def test_item4_supervisor_teardown_uses_declared_child_grace(monkeypatch):
    proc = fixture_procs.spawn([sys.executable, "-c", IGNORE_TERM_CODE])
    try:
        time.sleep(0.5)
        started = time.monotonic()
        service_mode._terminate_processes([proc], grace=0.3)
        assert proc.wait(timeout=5) == -signal.SIGKILL and time.monotonic() - started < 3
    finally:
        fixture_procs.reap(proc)


@pytest.mark.parametrize("bad", [0, -1, "5", True, None])
def test_item4_bad_declaration_fails_closed(monkeypatch, tmp_path, bad):
    _patched_schema(monkeypatch, child_grace_seconds=bad)
    with pytest.raises(ValueError, match="serve.stop.child_grace_seconds 须为正数"):
        service_mode.serve_stop_timeouts()


# ---------------------------------------------------------------------------
# ⑤ supervisor 正常退出后 state 标已停止, serve status 显示「未运行」
# ---------------------------------------------------------------------------

def _wait_port(port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"127.0.0.1:{port} 未在 {timeout}s 内就绪")


def _serve_env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT")}
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")  # 常驻一节只读临时单元目录
    env["PATH"] = f"{_bin(tmp_path)}{os.pathsep}{env.get('PATH', '')}"  # systemctl 替身在前
    env["FAKE_SVC_LOG"] = str(tmp_path / "svc.log")
    return env


def test_item5_supervisor_normal_exit_marks_state_stopped_and_status_not_running(tmp_path):
    root = tmp_path / "workspace"
    for state in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / state).mkdir(parents=True)
    conn = root / ".lybra" / "connection.json"
    board_port, mcp_port = _free_port(), _free_port()
    env = _serve_env(tmp_path)
    proc = fixture_procs.spawn(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(root), "serve", "--connection-json", str(conn),
         "start", "--board-port", str(board_port), "--mcp-port", str(mcp_port)],
        cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    status_cmd = [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(root), "serve", "--connection-json",
                  str(conn), "status"]
    try:
        _wait_port(mcp_port, 30)
        _wait_port(board_port, 30)
        running = subprocess.run(status_cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60)
        _show("---- ⑤ serve status · 在跑 原文 ----\n" + running.stdout + running.stderr)
        assert f"门进程: 已记录在跑(supervisor pid {proc.pid}, board pid" in running.stdout
        before = json.loads((conn.parent / "service_state.json").read_text(encoding="utf-8"))
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
        after = json.loads((conn.parent / "service_state.json").read_text(encoding="utf-8"))
        _show(f"[⑤] 退出后 state: stopped_at={after.get('stopped_at')} stopped_reason={after.get('stopped_reason')}")
        assert after["stopped_reason"] == "stop_signal" and after["stopped_at"]
        assert after["supervisor"] == before["supervisor"] and [p["pid"] for p in after["processes"]] == [
            p["pid"] for p in before["processes"]], "保留最后 pid 供排查"
        stopped = subprocess.run(status_cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60)
        _show("---- ⑤ serve status · supervisor 正常退出后 原文 ----\n" + stopped.stdout + stopped.stderr)
        assert stopped.returncode == 0
        assert "门进程: 未运行(supervisor 于 " in stopped.stdout and "退出: 收到停止信号" in stopped.stdout
        assert f"pid {proc.pid}" not in stopped.stdout
        js = subprocess.run(status_cmd + ["--json"], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60)
        st = json.loads(js.stdout)["service_state"]
        assert st["status"] == "stopped" and st["label"] == "未运行" and st["processes"] == [] and st["supervisor"] is None
        # serve stop 对已停止 state: 核身照旧(全不在 = 无信号, PASS)
        report = service_mode.stop_report(root, connection_target=conn)
        assert report["verdict"] == "PASS" and report["stopped"] == []
        assert all(r["status"] == "gone" for r in report["processes"])
    finally:
        fixture_procs.reap(proc, settle=5)
    assert not (tmp_path / "svc.log").exists() or "is-active" not in (tmp_path / "svc.log").read_text(encoding="utf-8")


def test_item5_mark_stopped_only_touches_own_state(tmp_path):
    conn = tmp_path / "ws" / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True)
    path = conn.parent / "service_state.json"
    me = {"pid": os.getpid(), "fingerprint": process_fingerprint(os.getpid())}
    other = {"supervisor": {"pid": os.getpid(), "fingerprint": "someone-else"}, "processes": []}
    path.write_text(json.dumps(other), encoding="utf-8")
    assert service_mode._mark_state_stopped(tmp_path / "ws", me, "stop_signal", connection_target=conn) is None
    assert json.loads(path.read_text(encoding="utf-8")) == other, "别的 supervisor(如 --reuse-port 交接)写的 state 不动"
    mine = {"supervisor": dict(me), "processes": [{"name": "board", "pid": 123}]}
    path.write_text(json.dumps(mine), encoding="utf-8")
    assert service_mode._mark_state_stopped(tmp_path / "ws", me, "restart_limit", connection_target=conn) is None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["stopped_reason"] == "restart_limit" and data["processes"] == [{"name": "board", "pid": 123}]
    st = service_mode.redacted_service_state(data)
    assert service_mode.render_service_state_line(st).startswith("门进程: 未运行(") and "子进程达重拉上限" in st["stopped_reason_label"]
    path.unlink()
    assert "已不在" in service_mode._mark_state_stopped(tmp_path / "ws", me, "stop_signal", connection_target=conn)
