"""AIPOS-F126 — run-all 真实根防护由「事后快照猜归因」改为「事前隔离」(gap #90/#102)。

靶场一律临时目录: 假「真实」环境 = tmp 下 HOME/.lybra/config.json home_root → tmp 下 git 治理仓(两个项目, 各有 governance/ 与
5_tasks/); 禁碰真实治理根 / 真实工位 / 生产门。执行器在本进程内调用(runall_discovery.run, guard_env 指向假「真实」HOME)。

 件① 事前隔离: 测试文件在沙箱里启动, 假真实 home 根及其 git 仓根只读 → 写入当场失败、归本文件, 根零变动, 不重跑。
 件② 声明与降级: config.schema test_contract.isolation {mode, network} 缺省 auto/isolated, 项目声明逐键覆盖, 命令行单次覆盖同一校验;
     探测唯一实现 runall_isolation.probe(真试写); 沙箱不可用 → 头部明示「隔离:无, 降级为快照守卫」且快照守卫照旧判红;
     指定后端不可用 → 报红不跑(不悄悄换后端)。
 件③ 守卫语义: 沙箱生效时快照只作信息(他方写入照列、永不判红); 孤儿进程收尸 / leak_mark 在沙箱内照常生效。
 件④ 夹具: a) 金丝雀 b) 测试外进程持续写被监视目录 c) 降级路径。

说明: 在 run-all 全量里本文件本身已跑在外层沙箱中; Ubuntu 上 bwrap 不可嵌套(非特权 user namespace 受 AppArmor 限制), auto
此时由同一探测落到 landlock(可嵌套)——本文件断言「有沙箱生效」而不绑定具体后端, 头部原文逐条打印。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import runall_discovery, runall_isolation  # noqa: E402
from tools.aipos_cli.workspace_config import apply_isolation_override, default_test_contract, project_test_contract  # noqa: E402

RUNALL = "tests/run-all.sh"
LINUX = sys.platform.startswith("linux")


def _show(text: str) -> None:
    print(text, flush=True)


def _g(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _git_repo(path: Path) -> None:
    _g(path, "init", "-q", "-b", "main")
    _g(path, "config", "user.name", "f126")
    _g(path, "config", "user.email", "f126@example.invalid")


def _fake_real_home(tmp_path: Path) -> tuple[Path, Path]:
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
    (home / ".lybra").mkdir(parents=True)
    (home / ".lybra" / "config.json").write_text(json.dumps({"config_version": 2, "home_root": str(root)}), encoding="utf-8")
    return home, root.resolve()


def _product_repo(tmp_path: Path, tests: dict[str, str]) -> Path:
    repo = tmp_path / "product"
    (repo / "tests").mkdir(parents=True)
    (repo / RUNALL).write_text("#!/usr/bin/env bash\n# lybra-runall: discover\n", encoding="utf-8")
    for name, body in tests.items():
        (repo / "tests" / name).write_text(body, encoding="utf-8")
    _git_repo(repo)
    return repo


def _tree_fingerprint(root: Path) -> dict[str, str]:
    """根下全部文件(含 .git)的 内容 sha1 + 权限位; 目录集合一并计入。零变动 = 两次指纹全等。"""
    fp: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            fp[rel + "/"] = "dir"
        else:
            fp[rel] = f"{hashlib.sha1(path.read_bytes()).hexdigest()}:{oct(path.stat().st_mode)}"
    return fp


def _scratch(tmp_path: Path) -> Path:
    """内层测试要新建文件的目录须在执行器起跑前就存在: landlock 后端下, 只读路径各级祖先目录(此处 tmp_path 是假真实根的父目录)
    里运行开始后新建的条目同样不可写(runall_isolation 文档的已知局限; bwrap 无此限)。"""
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return scratch


def _contract(mode: str | None = None) -> dict:
    contract = {**default_test_contract(), "runall_path": RUNALL}
    if mode is not None:
        contract["isolation"] = apply_isolation_override(contract["isolation"], {"mode": mode}, "夹具")
    return contract


def _run_executor(repo: Path, guard_home: Path, mode: str | None = None) -> tuple[int, str]:
    out = io.StringIO()
    rc = runall_discovery.run(repo, RUNALL, _contract(mode), out=out, guard_env={"HOME": str(guard_home)})
    return rc, out.getvalue()


def _verdict(text: str, path: str) -> str:
    match = re.search(rf"^([✓✗]) {re.escape(path)} (PASS|FAIL)$", text, re.M)
    assert match, f"无 {path} 判定行:\n{text}"
    return match.group(2)


def _header(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.startswith(("[runall_discovery] 隔离:", "✗ 隔离"))]
    assert lines, f"无隔离头部行:\n{text}"
    return lines[0]


def _excerpt(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines()
                     if ln.startswith(("✓", "✗", "[runall_discovery] 隔离", "[runall_discovery] 真实治理根守卫", "[runall_discovery] 孤儿")))


def _sandbox_header_ok(header: str) -> str:
    """auto 在本机须有沙箱生效(Linux: bwrap 或嵌套时 landlock); 返回后端名。"""
    match = re.match(r"\[runall_discovery\] 隔离:(bwrap|landlock)\(只读: ", header)
    assert match, f"Linux 上 auto 未得到沙箱: {header}"
    return match.group(1)


# ===========================================================================
# 件④a 金丝雀: 测试写真实 home 根 → 沙箱下该文件判红、根零变动、不重跑
# ===========================================================================
def test_item4a_canary_write_to_real_root_fails_in_sandbox_root_untouched(tmp_path):
    home, root = _fake_real_home(tmp_path)
    counter = _scratch(tmp_path) / "canary-runs.txt"
    log = root / "alpha" / "governance" / "enrollment_log.md"
    repo = _product_repo(tmp_path, {
        "test_clean.py": "def test_clean():\n    assert 1 + 1 == 2\n",
        "test_canary.py": (
            "import subprocess\nfrom pathlib import Path\n\n"
            f"COUNTER = Path({str(counter)!r})\n\n"
            "def test_append_real_log():\n"
            "    with COUNTER.open('a', encoding='utf-8') as fh:\n"
            "        fh.write('run\\n')\n"
            f"    with open({str(log)!r}, 'a', encoding='utf-8') as fh:\n"
            "        fh.write('- canary\\n')\n\n"
            "def test_new_card_in_real_queue():\n"
            f"    Path({str(root / 'beta' / '5_tasks' / 'queue' / 'pending' / 'canary.md')!r}).write_text('x', encoding='utf-8')\n\n"
            "def test_git_commit_in_real_governance_repo():\n"
            f"    subprocess.run(['git', '-C', {str(root)!r}, 'commit', '--allow-empty', '-q', '-m', 'canary'], check=True)\n"
        ),
    })
    before = _tree_fingerprint(root)
    rc, text = _run_executor(repo, home)
    _show("[④a 金丝雀] 执行器输出摘录:\n" + _excerpt(text))
    header = _header(text)
    if not LINUX:  # 非 Linux(macOS 等)无此二后端: 降级须明示, 由 test_item4c 覆盖降级判定
        assert header.startswith("[runall_discovery] 隔离:无, 降级为快照守卫"), header
        return
    backend = _sandbox_header_ok(header)
    assert str(root) in header, "只读路径 = 守卫同一梯解析出的 home 根(此处同时是其 git 仓根)"
    assert rc == 1
    assert _verdict(text, "tests/test_clean.py") == "PASS" and _verdict(text, "tests/test_canary.py") == "FAIL"
    canary = text.split("── tests/test_canary.py", 1)[1].split("✗ tests/test_canary.py FAIL", 1)[0]
    errno_text = "Read-only file system" if backend == "bwrap" else "Permission denied"
    assert errno_text in canary, canary
    assert canary.count("FAILED tests/test_canary.py::") == 3, "三种写法(追加日志 / 新建卡 / git 提交)都在沙箱内当场失败"
    assert f"隔离: 本文件失败输出提及只读路径 ['{root}']" in canary
    assert _tree_fingerprint(root) == before, "假真实根(含 .git)零变动"
    assert counter.read_text(encoding="utf-8") == "run\n", "沙箱生效时不重跑归因: 金丝雀文件只执行一次"
    assert "真实治理根守卫汇总(沙箱" in text and "同时段他方写入 0 处(不判红)" in text
    assert "测试所致变动" not in text and "单独重跑" not in text


# ===========================================================================
# 件④b 并发他方写入: run-all 期间测试外进程持续写被监视目录 → 沙箱下无误判
# ===========================================================================
_WRITER = (
    "import sys, time\nfrom pathlib import Path\n"
    "root, stop = Path(sys.argv[1]), Path(sys.argv[2])\n"
    "i = 0\n"
    "while not stop.exists():\n"
    "    i += 1\n"
    "    (root / 'beta' / 'governance' / 'LEDGER.md').write_text(f'advisor edit {i}\\n', encoding='utf-8')\n"
    "    (root / 'alpha' / 'governance' / 'enrollment_log.md').open('a', encoding='utf-8').write(f'- other {i}\\n')\n"
    "    (root / 'alpha' / '5_tasks' / 'queue' / 'pending' / f'other-{i}.md').write_text('x', encoding='utf-8')\n"
    "    time.sleep(0.02)\n"
)


def test_item4b_concurrent_outside_writer_is_not_misjudged_in_sandbox(tmp_path):
    home, root = _fake_real_home(tmp_path)
    stop = tmp_path / "stop-writer"
    body = "import time\n\ndef test_slow():\n    time.sleep(0.6)\n"
    repo = _product_repo(tmp_path, {"test_a.py": body, "test_b.py": body, "test_c.py": body})
    writer = subprocess.Popen([sys.executable, "-c", _WRITER, str(root), str(stop)])
    try:
        time.sleep(0.2)  # 写入方先跑起来: 每个测试文件的整个时间窗都有他方写入
        rc, text = _run_executor(repo, home)
    finally:
        stop.write_text("1", encoding="utf-8")
        writer.wait(timeout=30)
    _show("[④b 并发他方写入] 执行器输出摘录:\n" + "\n".join(ln for ln in _excerpt(text).splitlines() if "(信息)" not in ln)
          + f"\n(信息行 {text.count('真实治理根守卫(信息)')} 条, 摘一条) "
          + next((ln for ln in text.splitlines() if "真实治理根守卫(信息)" in ln), "(无)"))
    header = _header(text)
    if not LINUX:
        assert header.startswith("[runall_discovery] 隔离:无, 降级为快照守卫"), header
        return
    _sandbox_header_ok(header)
    assert rc == 0, text
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        assert _verdict(text, f"tests/{name}") == "PASS"
    assert text.count("真实治理根守卫(信息): 沙箱(") >= 3, "他方写入照列为信息"
    summary = re.search(r"真实治理根守卫汇总\(沙箱 \w+ 生效, 仅信息\): 同时段他方写入 (\d+) 处\(不判红\)", text)
    assert summary and int(summary.group(1)) > 0, text
    assert "测试所致变动" not in text and "单独重跑" not in text


# ===========================================================================
# 件④c 降级路径: 沙箱不可用 → 头部明示降级, 快照守卫照旧判红
# ===========================================================================
def test_item4c_no_sandbox_degrades_loudly_and_snapshot_guard_still_judges(tmp_path, monkeypatch):
    home, root = _fake_real_home(tmp_path)
    log = root / "alpha" / "governance" / "enrollment_log.md"
    repo = _product_repo(tmp_path, {
        "test_clean.py": "def test_clean():\n    pass\n",
        "test_polluter.py": (
            "def test_writes_real_governance_root():\n"
            f"    with open({str(log)!r}, 'a', encoding='utf-8') as fh:\n"
            "        fh.write('- polluted\\n')\n"
        ),
    })
    tried: list[tuple[str, bool]] = []

    def unavailable(backend: str, network_isolated: bool) -> tuple[bool, str]:
        tried.append((backend, network_isolated))
        return False, f"{backend} 不可用: 夹具模拟(如 macOS / 无 bwrap 且无 Landlock)"

    monkeypatch.setattr(runall_isolation, "probe", unavailable)
    rc, text = _run_executor(repo, home)
    _show("[④c 降级] 执行器输出摘录:\n" + _excerpt(text))
    assert tried == [("bwrap", True), ("landlock", True)], "auto 按声明顺序逐个探测"
    header = _header(text)
    assert header.startswith("[runall_discovery] 隔离:无, 降级为快照守卫(无可用沙箱(已探测 bwrap、landlock); 声明 mode=auto network=isolated")
    assert "bwrap 不可用: 夹具模拟" in header and "landlock 不可用: 夹具模拟" in header
    assert rc == 1
    assert _verdict(text, "tests/test_clean.py") == "PASS" and _verdict(text, "tests/test_polluter.py") == "FAIL"
    assert "真实治理根守卫: 单独重跑本文件复现变动(判为本文件所致)" in text
    assert re.search(r"真实治理根守卫汇总: 测试所致变动 [1-9]\d* 处", text)


def test_item2_declared_backend_unavailable_is_red_not_silently_swapped(tmp_path, monkeypatch):
    """指定后端探测真不可用(PATH 里没有 bwrap, 探测启动即失败)→ 报红不跑测试, 不悄悄换 landlock。"""
    home, _root = _fake_real_home(tmp_path)
    repo = _product_repo(tmp_path, {"test_clean.py": "def test_clean():\n    pass\n"})
    bin_dir = tmp_path / "bin-without-bwrap"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))
    rc, text = _run_executor(repo, home, mode="bwrap")
    _show("[② 指定后端不可用] 执行器输出:\n" + _excerpt(text))
    assert rc == 1
    assert "[runall_discovery] 隔离:声明后端 bwrap 不可用, 不悄悄换后端(声明 mode=bwrap 探测不可用" in text
    assert "bwrap 不可用: 启动失败" in text
    assert "✗ 隔离声明 mode=bwrap 不可用, 不跑测试" in text and "── tests/test_clean.py" not in text


def test_item2_mode_none_keeps_snapshot_guard_and_says_so(tmp_path):
    home, _root = _fake_real_home(tmp_path)
    repo = _product_repo(tmp_path, {"test_clean.py": "def test_clean():\n    pass\n"})
    rc, text = _run_executor(repo, home, mode="none")
    assert rc == 0, text
    assert _header(text).startswith("[runall_discovery] 隔离:无(声明 none), 快照守卫照常判定(声明 mode=none network=isolated, 来源 夹具 单次覆盖")
    assert "真实治理根守卫汇总: 测试所致变动 0 处" in text


# ===========================================================================
# 件③ 既有机制在沙箱内照常: 孤儿进程收尸(leak_mark)
# ===========================================================================
def test_item3_orphan_reaper_still_works_inside_sandbox(tmp_path):
    home, _root = _fake_real_home(tmp_path)
    pid_file = _scratch(tmp_path) / "orphan.pid"
    repo = _product_repo(tmp_path, {
        "test_leaks.py": (
            "import subprocess, sys\nfrom pathlib import Path\n\n"
            "def test_spawn_escaping_sleeper():\n"
            "    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)\n"
            f"    Path({str(pid_file)!r}).write_text(str(proc.pid), encoding='utf-8')\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    _show("[③ 沙箱内孤儿收尸] 执行器输出摘录:\n" + _excerpt(text))
    if LINUX:
        _sandbox_header_ok(_header(text))
    assert rc == 1 and _verdict(text, "tests/test_leaks.py") == "FAIL"
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert f"孤儿进程守卫: 测试结束后仍存活(逃出进程组收尸), 已 SIGKILL: pid {pid} " in text
    assert "孤儿进程守卫汇总: 泄漏进程 1 个" in text
    status = Path(f"/proc/{pid}/status")
    assert not status.exists() or "State:\tZ" in status.read_text(encoding="utf-8"), "已被收尸"


# ===========================================================================
# 件② 声明 / 探测 / 只读路径
# ===========================================================================
def test_item2_declaration_defaults_override_and_validation(tmp_path):
    from tools.schema_loader import load_schema

    decl = load_schema("config")["configuration_sources"]["project_json"]["schema"]["test_contract"]["schema"]["isolation"]["schema"]
    assert decl["mode"]["values"] == ["auto", "bwrap", "landlock", "none"] and decl["mode"]["default"] == "auto"
    assert decl["network"]["values"] == ["isolated", "host"] and decl["network"]["default"] == "isolated"
    assert set(decl["mode"]["values"]) - {"auto", "none"} == set(runall_isolation.BACKENDS), "schema 值域与已实现后端一致"
    assert default_test_contract()["isolation"] == {"mode": "auto", "network": "isolated",
                                                    "source": "config.schema test_contract.isolation 缺省"}
    gov = tmp_path / "gov"
    gov.mkdir()
    (gov / "project.json").write_text(json.dumps({"test_contract": {"runall_path": RUNALL}}), encoding="utf-8")
    assert project_test_contract(gov)["isolation"]["mode"] == "auto", "未声明 = 缺省(新键不改既有 project.json)"
    (gov / "project.json").write_text(json.dumps({"test_contract": {"isolation": {"network": "host"}}}), encoding="utf-8")
    got = project_test_contract(gov)["isolation"]
    assert (got["mode"], got["network"]) == ("auto", "host") and got["source"].endswith("test_contract.isolation")
    for bad, needle in (({"mode": "chroot"}, "不在声明值域"), ({"x": 1}, "未声明键"), ("auto", "须为对象")):
        (gov / "project.json").write_text(json.dumps({"test_contract": {"isolation": bad}}), encoding="utf-8")
        with pytest.raises(ValueError, match="TEST_CONTRACT_INVALID") as exc:
            project_test_contract(gov)
        assert needle in str(exc.value)
    with pytest.raises(ValueError, match="TEST_CONTRACT_INVALID: 命令行.isolation.mode='chroot'"):
        runall_discovery.main(["--runall", RUNALL, "--repo-root", str(REPO_ROOT), "--list", "--isolation", "chroot"])


def test_item2_probe_really_writes_and_is_the_single_capability_check(tmp_path):
    if LINUX:
        ok, detail = runall_isolation.probe("landlock", True)
        _show(f"[② 探测] landlock: {ok} {detail}")
        assert ok and "ro-denied errno=13 rw-ok" in detail, detail
    ok, detail = runall_isolation.probe("bwrap", True)
    _show(f"[② 探测] bwrap(network=isolated): {ok} {detail}")
    if ok:
        assert "ro-denied errno=30 rw-ok ifaces=lo" in detail, "bwrap 可用 = 只读路径 EROFS 且沙箱内网卡只剩 lo"
    else:
        assert detail.startswith("bwrap 不可用: "), detail
    src = (REPO_ROOT / "tools" / "aipos_cli" / "runall_isolation.py").read_text(encoding="utf-8")
    run_src = (REPO_ROOT / "tools" / "aipos_cli" / "runall_discovery.py").read_text(encoding="utf-8")
    assert "which(" not in src and "which(" not in run_src, "能力探测不凭 which"
    assert "probe_fn = probe_fn or probe" in src and "runall_isolation.resolve(" in run_src
    assert "except Exception" not in src and "except:" not in src


def test_item1_protected_paths_follow_guard_ladder_and_holes_only_inside(tmp_path):
    home, root = _fake_real_home(tmp_path)
    targets = runall_discovery.governance_guard_targets({"HOME": str(home)})
    assert runall_isolation.protected_paths(targets) == [root], "home 根即其 git 仓根: 去重为一条"
    outer = tmp_path / "vault"
    (outer / "projects" / "p1" / "governance").mkdir(parents=True)
    _git_repo(outer)
    targets = runall_discovery.governance_guard_targets({"HOME": str(home), "LYBRA_HOME_ROOT": str(outer / "projects")})
    assert runall_isolation.protected_paths(targets) == [outer.resolve()], "被守卫目录的 git 仓根在 home 根之上: 取仓根(含 .git)"
    product = outer / "code" / "repo"
    product.mkdir(parents=True)
    holes = runall_isolation.writable_holes([outer.resolve()], [product, tmp_path, outer])
    assert holes == [product.resolve()], "只放行严格落在只读路径之内者; 祖先 / 自身不放行"
    missing = runall_discovery.governance_guard_targets({"HOME": str(tmp_path / "nobody")})
    assert runall_isolation.protected_paths(missing) == []
    iso = runall_isolation.resolve({"mode": "auto", "network": "isolated", "source": "t"}, [], [])
    assert not iso.active and iso.header().startswith("隔离:无, 降级为快照守卫(无可只读挂载的路径(home 根不存在)")


def test_item1_product_repo_inside_protected_root_stays_writable(tmp_path):
    """产品仓落在只读路径之内(如代码仓放在治理仓里)→ 单独放行可写, 测试照常写自己的仓; 治理文件仍只读。"""
    home, root = _fake_real_home(tmp_path)
    inner = root / "alpha" / "code"
    (inner / "tests").mkdir(parents=True)
    (inner / RUNALL).write_text("#!/usr/bin/env bash\n# lybra-runall: discover\n", encoding="utf-8")
    log = root / "alpha" / "governance" / "enrollment_log.md"
    (inner / "tests" / "test_inner.py").write_text(
        "from pathlib import Path\nimport pytest\n\n"
        "def test_write_own_repo():\n"
        f"    Path({str(inner / 'build.out')!r}).write_text('ok', encoding='utf-8')\n\n"
        "def test_governance_still_read_only():\n"
        "    with pytest.raises(OSError):\n"
        f"        open({str(log)!r}, 'a', encoding='utf-8').write('x')\n",
        encoding="utf-8",
    )
    _git_repo(inner)
    rc, text = _run_executor(inner, home)
    _show("[① 产品仓在只读路径内] 执行器输出摘录:\n" + _excerpt(text))
    if not LINUX:
        assert _header(text).startswith("[runall_discovery] 隔离:无, 降级为快照守卫"), text
        return
    _sandbox_header_ok(_header(text))
    assert f"只读内放行可写: {inner.resolve()}" in _header(text)
    assert rc == 0, text
    assert (inner / "build.out").read_text(encoding="utf-8") == "ok"
    assert log.read_text(encoding="utf-8") == "# enrollment log\n"


# ===========================================================================
# --unshare-net 评估所得: 新网络命名空间里 git 无 DNS 推断提交邮箱 → 补 EMAIL(最低一级回退)
# ===========================================================================
def test_unshare_net_git_identity_fallback_is_lowest_precedence_email(tmp_path):
    decl = {"mode": "auto", "network": "isolated", "source": "t"}
    netns = runall_isolation.Isolation(declared=decl, backend="bwrap", network_isolated=True)
    assert netns.env({"PATH": "/bin"}) == {"PATH": "/bin", "RUNALL_SANDBOX": "bwrap", "EMAIL": runall_isolation.NETNS_GIT_EMAIL}
    assert netns.env({"EMAIL": "someone@example.invalid"})["EMAIL"] == "someone@example.invalid", "环境已有 EMAIL 不动"
    assert "EMAIL" not in runall_isolation.Isolation(declared=decl, backend="landlock").env({}), "未隔离网络不补"
    home, _root = _fake_real_home(tmp_path)
    repo = _product_repo(tmp_path, {
        "test_commit_without_identity.py": (
            "import subprocess\n\n"
            "def test_commit(tmp_path):\n"
            "    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)\n"
            "    (tmp_path / 'f').write_text('x')\n"
            "    subprocess.run(['git', '-C', str(tmp_path), 'add', 'f'], check=True)\n"
            "    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-q', '-m', 'm'], check=True)\n"
            "    subprocess.run(['git', '-C', str(tmp_path), 'config', 'user.email', 'repo@example.invalid'], check=True)\n"
            "    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-q', '--allow-empty', '-m', 'm2'], check=True)\n"
            "    log = subprocess.run(['git', '-C', str(tmp_path), 'log', '--format=%ae'], capture_output=True, text=True, check=True)\n"
            "    first, second = log.stdout.split()[1], log.stdout.split()[0]\n"
            "    print('author emails:', first, second)\n"
            "    assert second == 'repo@example.invalid', '仓内配置优先于 EMAIL'\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    _show("[--unshare-net git 身份] 执行器输出摘录:\n" + _excerpt(text))
    assert rc == 0, text
    assert _verdict(text, "tests/test_commit_without_identity.py") == "PASS"
