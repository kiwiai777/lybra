"""AIPOS-F265 — 测试夹具回收: 起看板 / 门 / serve 子进程的夹具在超时或失败时必须整组回收(禁留 ppid=1 孤儿); run-all 结束报告残留夹具进程。

依据(F263 交回实撞): 在 run-all 之外直接跑 tools/aipos_cli/tests/test_service_mode.py, 既有失败用例只 kill supervisor 本身, 看板 / 门
子进程被过继给 init(ppid=1)长期存活、占端口。靶场全部临时目录 + 本用例自起进程; 不碰真实治理根 / 生产门 / 他方进程。

 ① 共享夹具助手 tools/aipos_cli/tests/fixture_procs(唯一实现; 收尾 = runall_discovery.terminate_group 整组 SIGTERM → 限时 → SIGKILL):
    断言失败 / 就绪超时 / 子进程不理 SIGTERM / 子孙另起会话逃出进程组 / 测试忘了收尾(atexit)——均不留存活进程; 真 `serve start`
    (supervisor + 看板 + 门)在就绪超时与断言失败路径上整组回收。
 ② run-all 残留报告: 执行器结束时按本轮运行号查登记标签仍存活的夹具进程——有 = 总结末尾逐个 ⚠ pid / 命令 / cwd 并回收本轮登记的;
    无 = 明示「无」; 不带本轮登记标签的进程(他方 / 别的 run-all)不碰。
 ③ 盘点不回潮: 测试文件里起看板 / 门 / serve 子进程的 subprocess.Popen 一律改经共享助手(AST 扫描, 车道外存量显式列出)。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import runall_discovery  # noqa: E402
from tools.aipos_cli.tests import fixture_procs  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

RUNALL = "tests/run-all.sh"
SLEEP = [sys.executable, "-c", "import time; time.sleep(120)"]


def _show(text: str) -> None:
    print(text, flush=True)


def _alive(pid: int) -> bool:
    return runall_discovery._pid_alive(pid)


def _wait_gone(pids: list[int], timeout: float = 10.0) -> list[int]:
    deadline = time.monotonic() + timeout
    alive = [p for p in pids if _alive(p)]
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        alive = [p for p in alive if _alive(p)]
    return alive


def _read_pids(path: Path, count: int, timeout: float = 10.0) -> list[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            pids = [int(x) for x in path.read_text(encoding="utf-8").split()]
            if len(pids) >= count:
                return pids
        time.sleep(0.05)
    raise AssertionError(f"{path} 未在 {timeout}s 内写满 {count} 个 pid")


def _supervisor_script(pid_file: Path, *, ignore_term: bool = False, escape: bool = False) -> list[str]:
    """假 supervisor(serve start 形): 自己 + 一个同组子进程(escape=True 时子进程另起会话逃出进程组), 把 pid 写进 pid_file 后常驻。"""
    code = (
        "import os, signal, subprocess, sys, time\n"
        f"ignore = {ignore_term!r}\n"
        "if ignore:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child_code = 'import signal, time\\n' + ('signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n' if ignore else '') + 'time.sleep(120)'\n"
        f"child = subprocess.Popen([sys.executable, '-c', child_code], start_new_session={escape!r})\n"
        f"open({str(pid_file)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "time.sleep(120)\n"
    )
    return [sys.executable, "-c", code]


# ===========================================================================
# ① 共享夹具助手
# ===========================================================================
def test_item1_running_reaps_whole_group_on_assertion_failure(tmp_path):
    pid_file = tmp_path / "pids"
    with pytest.raises(AssertionError, match="注入断言失败"):
        with fixture_procs.running(_supervisor_script(pid_file)) as proc:
            pids = _read_pids(pid_file, 2)
            assert os.getpgid(pids[1]) == proc.pid  # 子进程与 supervisor 同在夹具新建的进程组(pgid == supervisor pid)
            raise AssertionError("注入断言失败")
    _show(f"[①] 断言失败路径: supervisor {pids[0]} + 子进程 {pids[1]} → 存活 {_wait_gone(pids, 0)}")
    assert _wait_gone(pids, 0) == [] and fixture_procs.tagged(proc) == []
    assert proc.pid not in fixture_procs._LIVE


def test_item1_sigterm_ignored_escalates_to_sigkill(tmp_path):
    pid_file = tmp_path / "pids"
    proc = fixture_procs.spawn(_supervisor_script(pid_file, ignore_term=True))
    pids = _read_pids(pid_file, 2)
    started = time.monotonic()
    outcome = fixture_procs.reap(proc, grace=1.0)
    elapsed = time.monotonic() - started
    _show(f"[①] 不理 SIGTERM: 结果 {outcome}, 用时 {elapsed:.2f}s, 存活 {_wait_gone(pids, 0)}")
    assert outcome == "killed" and 1.0 <= elapsed < 8.0
    assert _wait_gone(pids, 0) == [] and proc.returncode == -9


def test_item1_escaped_descendant_is_reaped_by_registration_tag(tmp_path):
    """子孙另起会话(逃出夹具进程组, killpg 够不着)→ 按登记标签兜底回收。"""
    pid_file = tmp_path / "pids"
    proc = fixture_procs.spawn(_supervisor_script(pid_file, escape=True))
    pids = _read_pids(pid_file, 2)
    assert os.getpgid(pids[1]) != proc.pid  # 确实逃出了进程组
    assert sorted(pid for pid, _cmd in fixture_procs.tagged(proc)) == sorted(pids)
    assert fixture_procs.reap(proc, grace=2.0) == "terminated"
    assert _wait_gone(pids) == []
    assert fixture_procs.reap(proc) == "gone"  # 重复调用无害


def test_item1_spawn_owns_session_kwargs():
    for kwargs in ({"start_new_session": False}, {"process_group": 0}):
        with pytest.raises(ValueError, match="AIPOS-F265"):
            fixture_procs.spawn(SLEEP, **kwargs)


def test_item1_tag_carries_run_id_from_runall_env(monkeypatch):
    monkeypatch.setenv(runall_discovery.FIXTURE_RUN_ENV, "f265probe")
    with fixture_procs.running(SLEEP) as proc:
        assert proc.fixture_tag.startswith("f265probe:")
        deadline = time.monotonic() + 10  # fork 与 exec 之间 environ 暂为空: 有界等到 exec 完成
        while not fixture_procs.tagged(proc) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert [pid for pid, _cmd in fixture_procs.tagged(proc)] == [proc.pid]
        assert [pid for pid, _cmd in runall_discovery.processes_with_env(
            f"{runall_discovery.FIXTURE_TAG_ENV}=f265probe:", prefix=True)] == [proc.pid]
    monkeypatch.delenv(runall_discovery.FIXTURE_RUN_ENV)
    assert fixture_procs.run_id() == f"direct-{os.getpid()}"


def test_item1_atexit_backstop_reaps_forgotten_fixture(tmp_path):
    """测试忘了收尾: 解释器退出时 atexit 兜底整组回收。"""
    pid_file = tmp_path / "pids"
    code = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tools.aipos_cli.tests import fixture_procs\n"
        f"fixture_procs.spawn({_supervisor_script(pid_file)!r})\n"
        "import time\n"
        "for _ in range(200):\n"
        "    import os\n"
        f"    if os.path.exists({str(pid_file)!r}) and len(open({str(pid_file)!r}).read().split()) == 2:\n"
        "        break\n"
        "    time.sleep(0.05)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    pids = _read_pids(pid_file, 2)
    _show(f"[①] atexit 兜底: 忘了收尾的夹具 {pids} → 存活 {_wait_gone(pids)}")
    assert _wait_gone(pids) == []


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"等待 127.0.0.1:{port} 超时({timeout}s)")


@pytest.mark.parametrize("failure", ["timeout", "assertion"])
def test_item1_real_serve_start_group_reaped_on_failure(tmp_path, failure):
    """真 `serve start`(supervisor 再派生 web.board.app 与门, F263 孤儿原形): 就绪超时 / 断言失败路径整组回收, 无 ppid=1 孤儿。"""
    root = tmp_path / "workspace"
    for state in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / state).mkdir(parents=True)
    conn = root / ".lybra" / "connection.json"
    board_port, mcp_port = _free_port(), _free_port()
    env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost,::1"}
    children: list[int] = []
    with pytest.raises(AssertionError):
        with fixture_procs.running(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(root), "serve", "--connection-json", str(conn),
             "start", "--board-port", str(board_port), "--mcp-port", str(mcp_port)],
            cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as proc:
            _wait_port(mcp_port, 30)
            _wait_port(board_port, 30)
            state = json.loads((conn.parent / "service_state.json").read_text(encoding="utf-8"))
            children = [int(p["pid"]) for p in state["processes"]]
            assert len(children) == 2 and all(_alive(p) for p in children)
            if failure == "timeout":
                _wait_port(_free_port(), 1.0)  # 注入: 等一个没人监听的端口 → 就绪超时
            raise AssertionError("注入断言失败")
    survivors = _wait_gone(children + [proc.pid], 0)
    _show(f"[①] 真 serve start({failure}): supervisor {proc.pid} + 看板/门 {children} → 存活 {survivors}; 标签残留 {fixture_procs.tagged(proc)}")
    assert survivors == [] and fixture_procs.tagged(proc) == []
    for port in (board_port, mcp_port):  # 端口已释放(无孤儿占端口)
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))


# ===========================================================================
# ② run-all 结束残留报告
# ===========================================================================
def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True)


def _product_repo(tmp_path: Path, tests: dict[str, str]) -> Path:
    repo = tmp_path / "product"
    (repo / "tests").mkdir(parents=True)
    (repo / RUNALL).write_text("#!/usr/bin/env bash\n# lybra-runall: discover\n", encoding="utf-8")
    for name, body in tests.items():
        (repo / "tests" / name).write_text(body, encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _run_executor(repo: Path, guard_home: Path) -> tuple[int, str]:
    out = io.StringIO()
    contract = {**default_test_contract(), "runall_path": RUNALL}
    contract["isolation"] = {**contract["isolation"], "mode": "none", "source": "夹具钉 none(验证残留报告)"}
    rc = runall_discovery.run(repo, RUNALL, contract, out=out, guard_env={"HOME": str(guard_home)})
    return rc, out.getvalue()


def _fixture_test(pid_file: Path, cwd: Path, *, reap: bool) -> str:
    """假测试: 经共享助手起夹具(干净环境 = 不带孤儿守卫标记, 只剩登记标签); reap=False 时测试进程自 SIGKILL(模拟被硬杀, atexit 走不到)。"""
    tail = ("    fixture_procs.reap(proc)\n" if reap else "    os.kill(os.getpid(), signal.SIGKILL)\n")
    return (
        f"import os, signal, sys\nsys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tools.aipos_cli.tests import fixture_procs\n\n"
        "def test_fixture():\n"
        f"    proc = fixture_procs.spawn({SLEEP!r}, env={{'PATH': os.environ['PATH']}}, cwd={str(cwd)!r})\n"
        f"    open({str(pid_file)!r}, 'w').write(str(proc.pid))\n"
        + tail
    )


def test_item2_runall_reports_and_reclaims_registered_residual(tmp_path):
    guard_home = tmp_path / "guard-home"
    guard_home.mkdir()
    cwd = tmp_path / "fixture-cwd"
    cwd.mkdir()
    pid_file = tmp_path / "leaked.pid"
    repo = _product_repo(tmp_path, {"test_killed.py": _fixture_test(pid_file, cwd, reap=False)})
    # 旁观进程: 无登记标签 / 别的运行号的登记标签——执行器不得碰
    bystander = subprocess.Popen(SLEEP, start_new_session=True)
    other_run = subprocess.Popen(SLEEP, start_new_session=True,
                                 env={**os.environ, runall_discovery.FIXTURE_TAG_ENV: f"other-run-{secrets.token_hex(4)}:x"})
    try:
        rc, text = _run_executor(repo, guard_home)
        report = [ln for ln in text.splitlines() if "残留夹具进程" in ln]
        _show("[②] 有残留(执行器输出摘录):\n" + "\n".join(report))
        leaked = int(pid_file.read_text(encoding="utf-8"))
        assert rc == 1  # 测试进程被硬杀 → 该文件红
        assert re.search(r"⚠ 残留夹具进程 1 个\(本轮运行号 [0-9a-f]{16} 登记, 测试结束后仍存活\), 已回收", report[0]), report
        assert report[1] == f"[runall_discovery] ⚠ 残留夹具进程: pid {leaked} 命令 {' '.join(SLEEP)} cwd {cwd}", report
        assert text.rstrip().splitlines()[-2:] == report[-2:]  # 列在总结末尾
        assert _wait_gone([leaked]) == []  # 本轮登记的已回收
        assert _alive(bystander.pid) and _alive(other_run.pid)  # 非本轮登记的不碰
    finally:
        for proc in (bystander, other_run):
            proc.kill()
            proc.wait(timeout=10)


def test_item2_runall_reports_none_when_fixtures_reaped(tmp_path):
    guard_home = tmp_path / "guard-home"
    guard_home.mkdir()
    pid_file = tmp_path / "reaped.pid"
    repo = _product_repo(tmp_path, {"test_clean.py": _fixture_test(pid_file, tmp_path, reap=True)})
    rc, text = _run_executor(repo, guard_home)
    report = [ln for ln in text.splitlines() if "残留夹具进程" in ln]
    _show("[②] 无残留(执行器输出摘录):\n" + "\n".join(report))
    assert rc == 0, text
    assert len(report) == 1 and re.fullmatch(
        r"\[runall_discovery\] 残留夹具进程: 无\(本轮运行号 [0-9a-f]{16} 登记的夹具进程均已收尾\)", report[0]), report
    assert text.rstrip().splitlines()[-1] == report[0]
    assert not _alive(int(pid_file.read_text(encoding="utf-8")))


def test_item2_child_env_carries_run_id_not_leaked_into_isolated_env():
    """运行号只由执行器在 run() 里下传给测试子进程; 环境构造唯一实现 isolated_test_env 不变(会话层隔离不带运行号)。"""
    assert runall_discovery.FIXTURE_RUN_ENV not in runall_discovery.isolated_test_env("/x/home", {"PATH": "/bin"})
    for name in (runall_discovery.FIXTURE_RUN_ENV, runall_discovery.FIXTURE_TAG_ENV):
        assert not name.startswith(("LYBRA_", "AIPOS_"))  # 嵌套执行器只剥这两类前缀, 登记标签一路下传


# ===========================================================================
# ③ 盘点不回潮: 起看板 / 门 / serve 子进程一律经共享助手
# ===========================================================================
#: 起看板 / 门 / serve 的 argv 记号(字符串常量整值命中)。
SERVICE_ARGV_MARKS = {"web.board.app", "serve", "serve-http"}
#: 车道外存量(本卡 lane = tests/ + tools/aipos_cli/tests/ 等, 改不到): 只许删不许加。
OUT_OF_LANE = {
    "tools/test_aipos_r2_enroll.py": "车道外(tools/ 根测试文件); 单进程门(serve-http 不派生子进程), 自带 terminate → kill 收尾; 迁共享助手另卡",
}


def raw_service_popens(root: Path = REPO_ROOT) -> dict[str, list[int]]:
    """测试文件里 argv 带看板 / 门 / serve 记号的 subprocess.Popen 直调 {文件: [行号]}(生成代码的字符串里的不算)。"""
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split()
    found: dict[str, list[int]] = {}
    for rel in discover_test_files(tracked, default_test_contract()):
        if not rel.endswith(".py") or not (root / rel).is_file():
            continue
        for node in ast.walk(ast.parse((root / rel).read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"):
                continue
            consts = {n.value for arg in node.args for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            if consts & SERVICE_ARGV_MARKS:
                found.setdefault(rel, []).append(node.lineno)
    return found


def test_item3_service_fixtures_go_through_shared_helper():
    found = raw_service_popens()
    _show(f"[③] 测试里直调 Popen 起看板 / 门 / serve: {found}")
    assert set(found) == set(OUT_OF_LANE), f"改用 tools/aipos_cli/tests/fixture_procs.spawn / running(整组回收): {found}"
    migrated = ("tools/aipos_cli/tests/test_service_mode.py", "tests/test_aipos_f117_entry_text_cleanup.py",
                "tests/test_aipos_f27_regression.py", "tests/test_aipos_f92_onboarding_walkthrough.py")
    for rel in migrated:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "fixture_procs.spawn(" in text and "fixture_procs.reap(" in text, rel


def test_item3_scanner_catches_raw_board_popen(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_probe.py").write_text(
        "import subprocess, sys\n\ndef test_x():\n"
        "    subprocess.Popen([sys.executable, '-m', 'web.board.app', '--port', '1'])\n"
        "    code = \"subprocess.Popen([sys.executable, '-m', 'web.board.app'])\"\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    assert raw_service_popens(tmp_path) == {"tests/test_probe.py": [4]}
