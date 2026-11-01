"""AIPOS-F263 产品化 P2b: `lybra setup check` 安装自检 + 门常驻安装(`serve install / uninstall / status`)+ 向导前置检查 / 起门步。

全部在临时 HOME + 临时 XDG_CONFIG_HOME + 临时 cwd 下跑(PATH 只含本用例自建的 bin 目录: node / python3 / git 指向真实可执行,
pi / systemctl 为夹具替身), 门可达用本进程起的本地 HTTP 夹具或已关闭端口; 不碰真实用户 systemd、不连真实门。
"""
from __future__ import annotations

import http.server
import io
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LYBRA_BIN = REPO_ROOT / "bin" / "lybra"


def _show(text: str) -> None:
    print(text, flush=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bin(tmp_path: Path, *, pi: str | None = None, node: str | None = None) -> Path:
    """用例 PATH: 真实 python3 / git / node 的符号链接 + 夹具 pi / systemctl / launchctl(记录调用)。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("python3", "git"):
        os.symlink(shutil.which(name), bindir / name)
    if node is None:
        real_node = shutil.which("node")
        if real_node:
            os.symlink(os.path.realpath(real_node), bindir / "node")
    else:
        _script(bindir / "node", node)
    if pi is not None:
        _script(bindir / "pi", pi)
    for svc in ("systemctl", "launchctl"):
        _script(bindir / svc, f'echo "{svc} $*" >> "$FAKE_SVC_LOG"\ncase "$*" in *is-active*) echo active;; esac\nexit 0\n')
    return bindir


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


PI_OK = ('case "$1" in --version) echo 0.85.1;; --list-models) printf "provider  model  context\\nkiwiai  m-one  100K\\n'
         'kiwiai  m-two  100K\\n";; esac\nexit 0\n')
PI_NO_MODELS = 'case "$1" in --version) echo 0.85.1;; --list-models) echo "No models available.";; esac\nexit 0\n'


def _env(tmp_path: Path, bindir: Path, **extra: str) -> dict[str, str]:
    env = {"HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "PATH": str(bindir), "LANG": "C.UTF-8",
           "PYTHONPATH": str(REPO_ROOT), "FAKE_SVC_LOG": str(tmp_path / "svc.log"), "LYBRA_HOME_ROOT": str(tmp_path / "hr")}
    (tmp_path / "home").mkdir(exist_ok=True)
    env.update(extra)
    return env


def _cli(tmp_path: Path, env: dict[str, str], *args: str) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", *args], cwd=str(tmp_path), env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


class _Gate404(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - 门对未知路径回 404 结构化错误的替身
        body = b'{"error":{"code":"NOT_FOUND"}}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: D401
        return None


@pytest.fixture()
def fake_gate():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Gate404)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------------------
# 件① setup check
# ---------------------------------------------------------------------------

def test_requirements_mirror_package_json_and_pyproject():
    from tools.aipos_cli.setup_check import setup_check_declaration

    req = setup_check_declaration()["requirements"]
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert pkg["engines"]["node"] == req["node"]["mirror"]["form"].replace("{min}", req["node"]["min"])
    pyproj = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^requires-python\s*=\s*"([^"]+)"', pyproj, re.M)
    assert m and m.group(1) == req["python"]["mirror"]["form"].replace("{min}", req["python"]["min"])


def test_setup_check_temp_home_without_pi_and_gate(tmp_path):
    bindir = _bin(tmp_path)
    env = _env(tmp_path, bindir, LYBRA_GATE_URL=f"http://127.0.0.1:{_free_port()}")
    rc, out = _cli(tmp_path, env, "setup", "check")
    _show("---- ① setup check · 临时 HOME(无 pi / 无门)原文 ----\n" + out)
    assert rc == 1
    assert "✓ Python:" in out and "✓ git:" in out
    assert "✗ pi(审计 / 非代码执行工位): pi 不在 PATH" in out and "npm install -g @earendil-works/pi-coding-agent" in out
    assert "⚠ pi 可用模型: 未核(pi 未安装)" in out
    assert "⚠ 门(在跑且可达):" in out and "不可达" in out and "lybra serve --workspace-root <治理根> start" in out
    assert "⚠ 门常驻: 未安装" in out and str(tmp_path / "xdg" / "systemd" / "user" / "lybra-gate.service") in out
    assert "结论: 有 1 项 ✗(阻断)" in out
    rc, out = _cli(tmp_path, env, "setup", "check", "--json")
    data = json.loads(out)
    assert rc == 1 and data["ok"] is False and data["blocking"] == ["pi"]
    from tools.aipos_cli.setup_check import setup_check_declaration

    assert [it["id"] for it in data["items"]] == setup_check_declaration()["items"]
    assert all({"id", "label", "status", "mark", "detail", "exit"} <= set(it) for it in data["items"])
    assert not (tmp_path / "xdg" / "systemd").exists(), "setup check 只读, 不建单元目录"


def test_setup_check_all_green_with_fake_pi_and_gate(tmp_path, fake_gate):
    bindir = _bin(tmp_path, pi=PI_OK)
    env = _env(tmp_path, bindir)
    rc, out = _cli(tmp_path, env, "setup", "check", "--gate-url", fake_gate)
    _show("---- ① setup check · 夹具 pi(2 模型)+ 本地门替身 原文 ----\n" + out)
    assert rc == 0
    assert "✓ pi(审计 / 非代码执行工位): 0.85.1" in out
    assert "✓ pi 可用模型: 2 个已配置模型: kiwiai/m-one, kiwiai/m-two" in out
    assert f"✓ 门(在跑且可达): {fake_gate}" in out and "HTTP 404(门已应答)" in out
    assert "结论: 无 ✗ 项" in out


def test_setup_check_pi_without_models_and_old_node(tmp_path):
    bindir = _bin(tmp_path, pi=PI_NO_MODELS, node="echo v16.20.0\n")
    env = _env(tmp_path, bindir, LYBRA_GATE_URL=f"http://127.0.0.1:{_free_port()}")
    rc, out = _cli(tmp_path, env, "setup", "check")
    _show("---- ① setup check · pi 无模型 + node 过旧 原文 ----\n" + out)
    assert rc == 1
    assert "✗ Node.js: v16.20.0" in out and "低于要求 ≥18" in out
    assert "✗ pi 可用模型:" in out and "pi --list-models" in out


def test_python_guard_readable_message():
    from tools.aipos_cli._python_guard import require_python

    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        require_python((3, 8, 10), buf)
    _show("---- ① 解释器过旧提示原文 ----\n" + buf.getvalue())
    assert exc.value.code == 1 and "Python >= 3.10 is required" in buf.getvalue() and "LYBRA_PYTHON" in buf.getvalue()
    assert require_python((3, 10, 0)) is None


def test_bin_entry_without_python_is_readable(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node 不在 PATH: 本用例验 bin/lybra(node 入口)缺 python 时的提示, 须有 node")
    bindir = tmp_path / "onlynode"
    bindir.mkdir()
    os.symlink(os.path.realpath(node), bindir / "node")
    proc = subprocess.run([str(bindir / "node"), str(LYBRA_BIN), "setup", "check"], cwd=str(tmp_path),
                          env={"HOME": str(tmp_path), "PATH": str(bindir)}, capture_output=True, text=True, timeout=60)
    _show(f"---- ① bin/lybra 缺 python 原文 rc={proc.returncode} ----\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 127 and "Python is required" in proc.stderr and "Traceback" not in proc.stderr


# ---------------------------------------------------------------------------
# 件② serve install / uninstall / status(临时 HOME + 临时 XDG_CONFIG_HOME + systemctl 夹具)
# ---------------------------------------------------------------------------

def _gov(tmp_path: Path) -> Path:
    gov = tmp_path / "hr" / "demo"
    (gov / "5_tasks" / "queue").mkdir(parents=True)
    return gov


def test_serve_install_dry_run_confirm_refuse_uninstall(tmp_path):
    bindir = _bin(tmp_path)
    env = _env(tmp_path, bindir)
    gov = _gov(tmp_path)
    unit = tmp_path / "xdg" / "systemd" / "user" / "lybra-gate.service"
    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--mcp-port", "7190")
    _show("---- ② serve install 预演原文 ----\n" + out)
    assert rc == 0 and not unit.exists() and not (tmp_path / "svc.log").exists(), "预演零写入、不跑服务命令"
    assert f"将写入 {unit} 全文" in out and "# lybra-managed: generated by lybra serve install" in out
    assert f"ExecStart={sys.executable} -m tools.aipos_cli.aipos_cli serve --workspace-root {gov.resolve()} start --mcp-port 7190" in out
    assert f"Environment=LYBRA_HOME_ROOT={tmp_path / 'hr'}" in out
    assert f"EnvironmentFile=-{tmp_path / 'xdg' / 'lybra' / 'gate.env'}" in out
    assert "systemctl --user enable --now lybra-gate.service" in out

    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--mcp-port", "7190", "--confirm")
    _show("---- ② serve install --confirm 原文(systemctl 夹具)----\n" + out)
    assert rc == 0 and unit.is_file() and "回读状态" in out and "(在跑)" in out
    calls = (tmp_path / "svc.log").read_text(encoding="utf-8").splitlines()
    assert calls == ["systemctl --user daemon-reload", "systemctl --user enable --now lybra-gate.service",
                     "systemctl --user is-active lybra-gate.service"]
    assert not (tmp_path / "xdg" / "lybra" / "gate.env").exists(), "环境文件只引用不创建"

    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install")
    _show("---- ② 已有同名单元再装 原文 ----\n" + out)
    assert rc == 1 and "UNIT_EXISTS_MANAGED" in out and "不覆盖" in out and "lybra serve uninstall --confirm" in out

    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "status")
    _show("---- ② serve status 常驻一节 ----\n" + out)
    assert f"门常驻: 已安装 {unit}(本命令生成; 在跑)" in out

    before = unit.read_text(encoding="utf-8")
    rc, out = _cli(tmp_path, env, "serve", "uninstall")
    assert rc == 0 and unit.read_text(encoding="utf-8") == before and "systemctl --user disable --now lybra-gate.service" in out
    rc, out = _cli(tmp_path, env, "serve", "uninstall", "--confirm")
    _show("---- ② serve uninstall --confirm 原文 ----\n" + out)
    assert rc == 0 and not unit.exists()
    assert (tmp_path / "svc.log").read_text(encoding="utf-8").splitlines()[-2:] == [
        "systemctl --user disable --now lybra-gate.service", "systemctl --user daemon-reload"]


def test_serve_install_handwritten_unit_hint_and_confirm_refused(tmp_path):
    bindir = _bin(tmp_path)
    env = _env(tmp_path, bindir)
    gov = _gov(tmp_path)
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    hand = unit_dir / "my-dev-gate.service"
    hand.write_text("[Service]\nExecStart=/usr/bin/python3 -m tools.aipos_cli.aipos_cli serve \\\n  --workspace-root /x \\\n"
                    "  start --mcp-port 7118\n", encoding="utf-8")
    (unit_dir / "other.service").write_text("[Service]\nExecStart=/usr/bin/ssh -N tunnel\n", encoding="utf-8")
    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install")
    _show("---- ② 已有手写常驻单元 · 预演原文 ----\n" + out)
    assert f"检测到已有手写常驻单元 my-dev-gate.service({hand}), 本命令不接管" in out
    assert "other.service" not in out and rc == 1 and "HANDWRITTEN_UNIT_PRESENT" in out
    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--confirm")
    assert rc == 1 and not (unit_dir / "lybra-gate.service").exists() and not (tmp_path / "svc.log").exists()
    rc, out = _cli(tmp_path, env, "setup", "check", "--gate-url", f"http://127.0.0.1:{_free_port()}")
    assert "✓ 门常驻: 检测到已有手写常驻单元 my-dev-gate.service" in out


def test_serve_install_foreign_same_name_untouched(tmp_path):
    bindir = _bin(tmp_path)
    env = _env(tmp_path, bindir)
    gov = _gov(tmp_path)
    unit = tmp_path / "xdg" / "systemd" / "user" / "lybra-gate.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    rc, out = _cli(tmp_path, env, "serve", "--workspace-root", str(gov), "install", "--confirm")
    _show("---- ② 同名非本命令生成 原文 ----\n" + out)
    assert rc == 1 and "UNIT_EXISTS_FOREIGN" in out and unit.read_text(encoding="utf-8") == "[Service]\nExecStart=/bin/true\n"
    rc, out = _cli(tmp_path, env, "serve", "uninstall", "--confirm")
    assert rc == 1 and "UNIT_NOT_MANAGED" in out and unit.exists() and not (tmp_path / "svc.log").exists()


def test_systemd_quoting_of_spaces_and_percent(tmp_path):
    from tools.aipos_cli.gate_resident import render_unit

    u = render_unit(workspace_root=Path("/srv/My Gov 100%"), home_root="/srv/h r", code_root=Path("/opt/ly"),
                    exec_argv=["/usr/bin/python3", "-m", "x", "serve", "--workspace-root", "/srv/My Gov 100%", "start"],
                    platform="linux", env={"HOME": str(tmp_path)})
    assert 'ExecStart=/usr/bin/python3 -m x serve --workspace-root "/srv/My Gov 100%%" start' in u["text"]
    assert 'Environment="LYBRA_HOME_ROOT=/srv/h r"' in u["text"]
    assert u["path"] == str(tmp_path / ".config" / "systemd" / "user" / "lybra-gate.service")


# ---------------------------------------------------------------------------
# 件② macOS 分支: 渲染单测覆盖 plist 内容 + 手写 plist 识别
# ---------------------------------------------------------------------------

def test_launchd_plist_render_and_handwritten_detection(tmp_path):
    from tools.aipos_cli.gate_resident import gate_resident_declaration, handwritten_units, render_unit

    home = tmp_path / "machome"
    env = {"HOME": str(home)}
    argv = ["/usr/local/bin/python3", "-m", "tools.aipos_cli.aipos_cli", "serve", "--workspace-root", "/Users/o/My Gov", "start"]
    u = render_unit(workspace_root=Path("/Users/o/My Gov"), home_root="/Users/o/hr", code_root=Path("/Users/o/ly"),
                    exec_argv=argv, platform="darwin", env=env)
    _show("---- ③ macOS plist 渲染原文 ----\n" + u["text"])
    assert u["path"] == str(home / "Library" / "LaunchAgents" / "lybra-gate.plist")
    marker = gate_resident_declaration()["managed_marker"]
    assert f"<!-- {marker} -->" in u["text"]
    data = plistlib.loads(u["text"].encode("utf-8"))
    assert data["Label"] == "lybra-gate" and data["RunAtLoad"] is True and data["KeepAlive"] is True
    assert data["ProgramArguments"][:2] == ["/bin/sh", "-c"]
    env_file = str(home / ".config" / "lybra" / "gate.env")
    assert data["ProgramArguments"][2] == (f"if [ -f {env_file} ]; then set -a; . {env_file}; set +a; fi; exec /usr/local/bin/python3 "
                                           "-m tools.aipos_cli.aipos_cli serve --workspace-root '/Users/o/My Gov' start")
    assert data["EnvironmentVariables"] == {"LYBRA_HOME_ROOT": "/Users/o/hr", "PYTHONPATH": "/Users/o/ly"}
    assert data["WorkingDirectory"] == "/Users/o/ly"
    assert data["StandardOutPath"] == str(home / "Library" / "Logs" / "lybra" / "lybra-gate.out.log")
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.me.gate.plist").write_bytes(plistlib.dumps({"Label": "com.me.gate", "ProgramArguments": [
        "/usr/bin/python3", "-m", "tools.aipos_cli.aipos_cli", "serve", "start"]}))
    (agents / "com.me.other.plist").write_bytes(plistlib.dumps({"Label": "x", "ProgramArguments": ["/bin/echo", "hi"]}))
    found = handwritten_units(platform="darwin", env=env)
    assert [h["name"] for h in found] == ["com.me.gate.plist"]


def test_launchd_install_plan_commands(tmp_path, monkeypatch):
    from tools.aipos_cli.gate_resident import install

    home = tmp_path / "machome"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(tmp_path / "hr"))
    gov = _gov(tmp_path)
    plan = install(gov, confirm=False, platform="darwin", env={"HOME": str(home)})
    assert plan["ok"] and plan["kind"] == "launchd" and not Path(plan["unit_path"]).exists()
    assert plan["commands"] == [["launchctl", "bootstrap", f"gui/{os.getuid()}", plan["unit_path"]]]
    assert plan["status_command"] == ["launchctl", "print", f"gui/{os.getuid()}/lybra-gate"]


# ---------------------------------------------------------------------------
# 件③ 向导前置检查步 + 起门步
# ---------------------------------------------------------------------------

def test_onboarding_guide_preflight_blocks_and_gate_start_step(tmp_path):
    bindir = _bin(tmp_path)
    env = _env(tmp_path, bindir)
    gate = f"http://127.0.0.1:{_free_port()}"
    rc, out = _cli(tmp_path, env, "onboarding", "guide", "demo", "--home-root", str(tmp_path / "hr"), "--gate-url", gate)
    pre = out[out.index("── 前置检查"):out.index("── Step 1:")]
    _show("---- ④ 向导前置检查步原文 ----\n" + pre)
    assert rc == 0
    assert "命令: lybra setup check" in pre and "✗ pi(审计 / 非代码执行工位): pi 不在 PATH" in pre
    assert "⛔ 前置检查有 ✗ 项(pi): 以下各步被阻断" in pre and "lybra onboarding check demo --step 0" in pre
    start = out[out.index("── 起门"):out.index("── Step 3:")]
    _show("---- ④ 向导起门步原文 ----\n" + start)
    gov = tmp_path / "hr" / "demo"
    assert f"lybra serve --workspace-root {gov} start" in start
    assert f"lybra serve --workspace-root {gov} install --dry-run" in start and f"lybra serve --workspace-root {gov} install --confirm" in start
    assert out.index("── Step 2:") < out.index("── 起门") < out.index("── Step 3:")

    rc, out = _cli(tmp_path, env, "onboarding", "guide", "demo", "--home-root", str(tmp_path / "hr"), "--gate-url", gate, "--json")
    guide = json.loads(out)
    assert guide["preflight"]["ran"] and guide["preflight"]["blocking"] and guide["preflight"]["blocking_items"] == ["pi"]
    assert guide["gate_start"]["before_step"] == min(guide["owner_actions"]) and guide["total_steps"] == len(guide["steps"])

    rc, out = _cli(tmp_path, env, "onboarding", "guide", "demo", "--home-root", str(tmp_path / "hr"), "--gate-url", gate,
                   "--skip-preflight")
    assert rc == 0 and "本次未运行自检" in out and "⛔" not in out

    rc, out = _cli(tmp_path, env, "onboarding", "check", "demo", "--step", "0", "--home-root", str(tmp_path / "hr"))
    _show(f"---- ④ onboarding check --step 0 原文 rc={rc} ----\n" + out)
    assert rc == 1 and "preflight:pi" in out and "npm install -g @earendil-works/pi-coding-agent" in out


def test_guide_function_default_does_not_probe(tmp_path, monkeypatch):
    """函数缺省不跑自检(存量调用方结构不变, 不起子进程): 前置检查只给命令。"""
    import tools.aipos_cli.setup_check as sc
    from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide

    def _boom(**_k):
        raise AssertionError("缺省不得跑自检")

    monkeypatch.setattr(sc, "run_setup_check", _boom)
    guide = generate_onboarding_guide("demo", home_root=str(tmp_path / "hr"), gate_url="http://127.0.0.1:7118")
    assert guide["preflight"]["ran"] is False and guide["total_steps"] == 9
    assert "本次未运行自检" in format_guide_text(guide)
