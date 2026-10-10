"""AIPOS-F116 — 测试卫生: 测试不得写真实治理根与真实 HOME、夹具进程不泄漏、不扫全局 /tmp、撤 run-all 并集合并。

靶场全部临时目录(假「真实」home 根 = tmp 下 git 仓 + 假 ~/.lybra/config.json 指向它); 禁碰真实治理根/真实工位/生产门。

 ① 真实治理根守卫(gap #34/#70): run-all 执行器(tools/aipos_cli/runall_discovery.py)每个测试文件前后只读快照 home 根下各项目
    governance/ 与 5_tasks/ 的 git status 脏项 + 关键日志(governance/*_log.md)md5, 变则该文件判红并列出变动; 注入演示=靶场测试
    写「真实」enrollment_log → 红; 干净测试 → 绿; 守卫本身不写被监视仓(index 字节不变)。
 ② HOME 隔离(gap #56): 各测试目录 conftest.py 会话层隔离 HOME/LYBRA_*/AIPOS_*(唯一实现 isolate_test_session, 与执行器子进程
    同一环境构造 isolated_test_env); 直接跑 pytest(真实形 HOME + LYBRA_HOME_ROOT)也拿到临时 HOME。
 ③ 孤儿进程守卫(gap #12/#25/#55): 测试子进程带本轮标记变量; 文件结束后仍带标记的存活进程(另起会话逃出进程组收尸的
    web.board.app)= 泄漏 → SIGKILL 并判红。
 ④ 全局 /tmp 扫描(gap #76): 见 tests/test_governance_commit_f79c.py(残留判据只看本用例私有临时目录 + 全局同前缀诱饵用例)。
 ⑤ 撤并集合并(gap #83): 仓根 .gitattributes 的 `tests/run-all.sh merge=union` 已撤(git check-attr = unspecified);
    run-all.sh 可执行段不得再出现逐卡登记(自动发现执行器为唯一入口)。
"""
from __future__ import annotations

import io
import json
import os
import pwd
import re
import secrets
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import runall_discovery  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

RUNALL = "tests/run-all.sh"
CONFTESTS = ("tests/conftest.py", "tools/aipos_cli/conftest.py", "tools/mcp_server/tests/conftest.py", "web/conftest.py")


def _show(text: str) -> None:
    print(text, flush=True)


def _g(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _git_repo(path: Path) -> None:
    _g(path, "init", "-q", "-b", "main")
    _g(path, "config", "user.name", "f116")
    _g(path, "config", "user.email", "f116@example.invalid")


def _fake_real_home(tmp_path: Path) -> tuple[Path, Path]:
    """假「真实」环境: HOME/.lybra/config.json home_root → 一个 git 治理仓(两个项目, 各有 governance/ 与 5_tasks/)。"""
    home = tmp_path / "real-home"
    root = tmp_path / "real-home-root"
    for project in ("alpha", "beta"):
        (root / project / "governance").mkdir(parents=True)
        (root / project / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (root / project / "governance" / "enrollment_log.md").write_text("# enrollment log\n", encoding="utf-8")
        (root / project / "5_tasks" / "queue" / "pending" / ".gitkeep").write_text("", encoding="utf-8")
    _git_repo(root)
    _g(root, "add", "-A")
    _g(root, "commit", "-q", "-m", "governance truth")
    (root / "alpha" / "5_tasks" / "queue" / "pending" / "card-1.md").write_text("---\ntask_id: A-1\n---\n", encoding="utf-8")  # 存量脏项
    (home / ".lybra").mkdir(parents=True)
    (home / ".lybra" / "config.json").write_text(json.dumps({"config_version": 2, "home_root": str(root)}), encoding="utf-8")
    return home, root


def _product_repo(tmp_path: Path, tests: dict[str, str]) -> Path:
    repo = tmp_path / "product"
    (repo / "tests").mkdir(parents=True)
    (repo / RUNALL).write_text("#!/usr/bin/env bash\n# lybra-runall: discover\n", encoding="utf-8")
    for name, body in tests.items():
        (repo / "tests" / name).write_text(body, encoding="utf-8")
    _git_repo(repo)
    return repo


def _run_executor(repo: Path, guard_home: Path) -> tuple[int, str]:
    """AIPOS-F126: 本文件验证的是沙箱未生效时的快照守卫判定(降级路径), 故钉隔离声明 mode=none; 沙箱路径见
    tests/test_aipos_f126_runall_isolation.py。"""
    out = io.StringIO()
    contract = {**default_test_contract(), "runall_path": RUNALL}
    contract["isolation"] = {**contract["isolation"], "mode": "none", "source": "夹具钉 none(验证快照守卫判定)"}
    rc = runall_discovery.run(repo, RUNALL, contract, out=out, guard_env={"HOME": str(guard_home)})
    return rc, out.getvalue()


def _verdict(text: str, path: str) -> str:
    match = re.search(rf"^([✓✗]) {re.escape(path)} (PASS|FAIL)$", text, re.M)
    assert match, f"无 {path} 判定行"
    return match.group(2)


# ===========================================================================
# ① 真实治理根守卫
# ===========================================================================
def test_item1_guard_catches_injected_pollution_and_stays_green_when_clean(tmp_path):
    home, root = _fake_real_home(tmp_path)
    log = root / "alpha" / "governance" / "enrollment_log.md"
    repo = _product_repo(tmp_path, {
        "test_clean.py": "def test_clean():\n    assert 1 + 1 == 2\n",
        # 注入演示: 写死「真实」治理根路径的测试(同 tools/test_aipos_r2_enroll.py 形, gap #34/#70)
        "test_polluter.py": (
            "from pathlib import Path\n\n"
            "def test_writes_real_governance_root():\n"
            f"    with open({str(log)!r}, 'a', encoding='utf-8') as fh:\n"
            "        fh.write('- 2026-10-06T00:00:00Z  create  instance=test.enroll.injected\\n')\n"
            f"    Path({str(root / 'beta' / '5_tasks' / 'queue' / 'pending' / 'injected.md')!r}).write_text('x', encoding='utf-8')\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    _show("[①] 注入演示(执行器输出摘录):\n" + "\n".join(
        ln for ln in text.splitlines() if ln.startswith(("✓", "✗", "[runall_discovery] 真实治理根守卫"))))
    assert rc == 1
    assert _verdict(text, "tests/test_clean.py") == "PASS" and _verdict(text, "tests/test_polluter.py") == "FAIL"
    assert f"真实治理根守卫: home 根 {root.resolve()}(来源 全局配置 ~/.lybra/config.json home_root); 监视 4 个目录" in text
    polluter_section = text.split("── tests/test_polluter.py", 1)[1]
    assert f"md5 {log.resolve()}" in polluter_section or f"md5 {log}" in polluter_section
    assert "injected.md" in polluter_section and "?? " in polluter_section and "enrollment_log.md: (无) →  M " in polluter_section
    assert "真实治理根守卫汇总: 测试所致变动 3 处" in text  # 复核重跑复现: 日志 md5 + 日志 status 指纹 + 未跟踪文件 mtime


def test_item1_unreproduced_change_is_concurrent_writer_not_red(tmp_path):
    """同时段他方写入(门/顾问)形: 变动只出现一次(复核重跑不复现)→ 照列, 不判红。"""
    home, root = _fake_real_home(tmp_path)
    sentinel = tmp_path / "already-wrote"
    ledger = root / "beta" / "governance" / "LEDGER.md"
    repo = _product_repo(tmp_path, {
        "test_bystander.py": (
            "from pathlib import Path\n\n"
            "def test_bystander():\n"
            f"    sentinel = Path({str(sentinel)!r})\n"
            "    if not sentinel.exists():  # 只在首轮写一次 = 模拟同一时间窗里别的进程(门/顾问)的正常写入\n"
            "        sentinel.write_text('1')\n"
            f"        Path({str(ledger)!r}).write_text('advisor edit\\n', encoding='utf-8')\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    _show("[①] 并发写入归因(执行器输出摘录):\n" + "\n".join(ln for ln in text.splitlines() if "守卫" in ln or ln.startswith(("✓", "✗"))))
    assert rc == 0, text
    assert _verdict(text, "tests/test_bystander.py") == "PASS"
    assert "单独重跑未复现 → 判为同时段他方写入(门/顾问), 不计本文件" in text and "LEDGER.md" in text
    assert "测试所致变动 0 处(无测试写入真实治理根); 同时段他方写入(重跑未复现, 不计) 1 处" in text


def test_item1_guard_is_read_only_and_green_on_clean_suite(tmp_path):
    home, root = _fake_real_home(tmp_path)
    index = root / ".git" / "index"
    index_before = index.read_bytes()
    status_before = _g(root, "status", "--porcelain")
    repo = _product_repo(tmp_path, {"test_clean.py": "def test_clean():\n    pass\n"})
    rc, text = _run_executor(repo, home)
    assert rc == 0, text
    assert "真实治理根守卫汇总: 测试所致变动 0 处(无测试写入真实治理根); 同时段他方写入(重跑未复现, 不计) 0 处" in text
    assert index.read_bytes() == index_before  # --no-optional-locks: 守卫不刷新/不写被监视仓 index
    assert _g(root, "status", "--porcelain") == status_before


def test_item1_guard_targets_follow_home_root_ladder_env_over_config(tmp_path):
    home, root = _fake_real_home(tmp_path)
    other = tmp_path / "env-root"
    (other / "gamma" / "governance").mkdir(parents=True)
    targets = runall_discovery.governance_guard_targets({"HOME": str(home), "LYBRA_HOME_ROOT": str(other)})
    assert targets["home_root"] == other.resolve() and targets["source"] == "环境变量 LYBRA_HOME_ROOT"
    assert targets["dirs"] == [other.resolve() / "gamma" / "governance"] and targets["plain"] == targets["dirs"]
    targets = runall_discovery.governance_guard_targets({"HOME": str(home)})
    assert targets["home_root"] == root.resolve() and list(targets["repos"]) == [root.resolve()]
    assert [p.name for p in targets["key_logs"]] == ["enrollment_log.md", "enrollment_log.md"]


def test_item1_executor_guards_against_the_real_environment_not_the_isolated_child():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "runall_discovery.py").read_text(encoding="utf-8")
    body = src.split("def run(", 1)[1].split("\ndef ", 1)[0]
    # 守卫按执行器自身环境(隔离前)解析 home 根; 子进程环境另行隔离
    assert "governance_guard_targets(os.environ if guard_env is None else guard_env)" in body
    assert body.index("governance_guard_targets(") < body.index("_child_env(home, leak_mark)")
    assert "except Exception" not in src and "except:" not in src


# ===========================================================================
# ② HOME 隔离(会话层 conftest, 唯一实现)
# ===========================================================================
def test_item2_this_session_is_isolated():
    home = os.environ["HOME"]
    assert os.environ.get(runall_discovery.ISOLATED_HOME_ENV) == home
    assert home != pwd.getpwuid(os.getuid()).pw_dir and str(Path.home()) == home
    assert not [k for k in os.environ if k.startswith(("LYBRA_", "AIPOS_"))]


@pytest.mark.parametrize("conftest", CONFTESTS)
def test_item2_each_conftest_isolates_a_real_shaped_environment(conftest, tmp_path):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    env = {k: v for k, v in os.environ.items() if k != runall_discovery.ISOLATED_HOME_ENV}
    env.update(HOME=str(real_home), LYBRA_HOME_ROOT=str(tmp_path / "real-root"), AIPOS_WORKSPACE_ROOT=str(tmp_path / "ws"))
    probe = ("import json, os, runpy, sys\n"
             f"runpy.run_path({str(REPO_ROOT / conftest)!r})\n"
             "print(json.dumps({'home': os.environ['HOME'], 'mark': os.environ.get('RUNALL_ISOLATED_HOME'),"
             " 'lybra': sorted(k for k in os.environ if k.startswith(('LYBRA_HOME', 'AIPOS_')))}))\n")
    result = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    _show(f"[②] {conftest}: 真实形 HOME={real_home} → 会话 HOME={seen['home']}")
    assert seen["home"] != str(real_home) and seen["mark"] == seen["home"] and seen["lybra"] == []
    assert not Path(seen["home"]).exists()  # 进程退出即清理临时 HOME


def test_item2_direct_pytest_run_gets_isolated_home(tmp_path):
    """直接跑 pytest(不经 run-all)的会话也被隔离: 用本文件的探针用例回报环境。"""
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    out = tmp_path / "probe.json"
    env = {k: v for k, v in os.environ.items() if k != runall_discovery.ISOLATED_HOME_ENV}
    env.update(HOME=str(real_home), LYBRA_HOME_ROOT=str(tmp_path / "real-root"), F116_PROBE_OUT=str(out))
    result = subprocess.run([sys.executable, "-m", "pytest", f"{Path(__file__)}::test_item2_probe_env", "-q", "-p", "no:cacheprovider"],
                            cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert seen["home"] != str(real_home) and seen["mark"] == seen["home"] and seen["home_root_env"] is None


def test_item2_probe_env():
    """探针: 由 test_item2_direct_pytest_run_gets_isolated_home 以子进程 pytest 调起(F116_PROBE_OUT 指定落点); 单独跑时仅自检隔离。"""
    payload = {"home": os.environ["HOME"], "mark": os.environ.get(runall_discovery.ISOLATED_HOME_ENV),
               "home_root_env": os.environ.get("LYBRA_HOME_ROOT")}
    target = os.environ.get("F116_PROBE_OUT")
    if target:
        Path(target).write_text(json.dumps(payload), encoding="utf-8")
    assert payload["mark"] == payload["home"] and payload["home_root_env"] is None


def test_item2_single_environment_construction():
    child = runall_discovery._child_env("/x/home", "RUNALL_LEAK_MARK_test")
    assert child == {**runall_discovery.isolated_test_env("/x/home"), "RUNALL_LEAK_MARK_test": "1"}
    for rel in CONFTESTS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "from tools.aipos_cli.runall_discovery import isolate_test_session" in text and "\nisolate_test_session()\n" in text, rel
        assert "os.environ[" not in text and "HOME" not in text.split('"""', 2)[2], rel  # 会话层不另写一套环境构造


# ===========================================================================
# ③ 孤儿进程守卫
# ===========================================================================
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_item3_orphan_guard_kills_escaped_board_and_fails_the_file(tmp_path):
    home, _root = _fake_real_home(tmp_path)
    board_root = tmp_path / "board-repo-root"
    board_root.mkdir()
    port = _free_port()
    repo = _product_repo(tmp_path, {
        "test_ok.py": "def test_ok():\n    pass\n",
        # 注入演示: 起 web.board.app 另起会话(逃出 killpg)且不收尾(gap #12/#25/#55 形)
        "test_leaky_board.py": (
            "import socket, subprocess, sys, time\n\n"
            "def test_leaks_board():\n"
            f"    subprocess.Popen([sys.executable, '-m', 'web.board.app', '--host', '127.0.0.1', '--port', '{port}',"
            f" '--repo-root', {str(board_root)!r}], cwd={str(REPO_ROOT)!r}, start_new_session=True,"
            " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "    for _ in range(200):\n"
            "        try:\n"
            f"            socket.create_connection(('127.0.0.1', {port}), timeout=0.2).close()\n"
            "            return\n"
            "        except OSError:\n"
            "            time.sleep(0.1)\n"
            "    raise AssertionError('board did not listen')\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    lines = [ln for ln in text.splitlines() if ln.startswith(("✓", "✗", "[runall_discovery] 孤儿进程守卫"))]
    _show("[③] 孤儿进程守卫(执行器输出摘录):\n" + "\n".join(lines))
    assert rc == 1
    assert _verdict(text, "tests/test_ok.py") == "PASS" and _verdict(text, "tests/test_leaky_board.py") == "FAIL"
    leak_lines = [ln for ln in lines if "已 SIGKILL" in ln]
    assert len(leak_lines) == 1 and "web.board.app" in leak_lines[0] and str(board_root) in leak_lines[0]
    assert "孤儿进程守卫汇总: 泄漏进程 1 个" in text
    survivors = subprocess.run(["pgrep", "-f", f"web.board.app.*{board_root}"], capture_output=True, text=True)
    assert survivors.stdout.strip() == "", survivors.stdout  # 已被守卫清掉, 无孤儿


def test_item3_marked_processes_sees_only_this_mark(tmp_path):
    # AIPOS-F149 件②: 标记带随机串——固定标记在并行 run-all 下会认到(并 SIGKILL)他方同名用例的进程; 只认本用例自起进程
    mark = runall_discovery.LEAK_MARK_PREFIX + "f116probe" + secrets.token_hex(8)
    env = {**os.environ, mark: "1"}
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=env, start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        seen = runall_discovery.marked_processes(mark)
        assert [pid for pid, _cmd in seen] == [sleeper.pid]
        reaped = runall_discovery.reap_marked(mark)
        assert [pid for pid, _cmd in reaped] == [sleeper.pid]
        assert sleeper.wait(timeout=10) == -signal.SIGKILL
        assert other.poll() is None  # 不带标记的进程(他人/生产门)不碰
    finally:
        for proc in (sleeper, other):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


# ===========================================================================
# ⑤ 撤 run-all 并集合并 + 不得再现逐卡登记块
# ===========================================================================
def _executable_lines(runall_text: str) -> list[str]:
    body = runall_text.split("# ---- 声明行", 1)[0]
    return [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def per_card_registrations(runall_text: str, test_files: list[str]) -> list[str]:
    """run-all 可执行段里的逐卡登记: 点名执行某个测试文件 / 直接调 pytest / run_pytest 一行式。自动发现执行器为唯一入口。"""
    hits = []
    for line in _executable_lines(runall_text):
        if any(path in line for path in test_files) or re.search(r"\b(run_pytest|pytest)\b", line):
            hits.append(line)
    return hits


def test_item5_union_merge_rule_withdrawn():
    attr = subprocess.run(["git", "check-attr", "merge", "--", RUNALL], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    _show(f"[⑤] git check-attr merge -- {RUNALL}: {attr.stdout.strip()}")
    assert attr.stdout.strip() == f"{RUNALL}: merge: unspecified"
    assert not (REPO_ROOT / ".gitattributes").exists()  # 该文件只有这一条规则, 随之删除


def test_item5_runall_has_no_per_card_registration_block():
    text = (REPO_ROOT / RUNALL).read_text(encoding="utf-8")
    contract = {**default_test_contract(), "runall_path": RUNALL}
    tests = discover_test_files(runall_discovery.repo_files(REPO_ROOT), contract)
    assert per_card_registrations(text, tests) == []
    entry = [ln for ln in _executable_lines(text) if "tools.aipos_cli.runall_discovery" in ln]
    assert len(entry) == 1 and "--runall tests/run-all.sh" in entry[0]  # 自动发现执行器 = 唯一入口
    # 注入演示: union 合并曾静默并回的旧式登记块 → 防护判红
    injected = text.replace('if ! PYTHONPATH=', 'run_pytest tests/test_aipos_f111_parallel_audit.py\n'
                            'python3 -m pytest tests/test_finalize.py -v\nif ! PYTHONPATH=', 1)
    hits = per_card_registrations(injected, tests)
    _show(f"[⑤] 注入旧式登记块 → 防护命中 {hits}")
    assert hits == ["run_pytest tests/test_aipos_f111_parallel_audit.py", "python3 -m pytest tests/test_finalize.py -v"]
