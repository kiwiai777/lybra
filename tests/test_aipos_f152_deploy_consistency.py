"""AIPOS-F152(gap #127/#87) — 长驻进程与部署版本一致(件①) + `lybra` CLI 从部署快照运行(件②)。

靶场一律临时目录: 假部署目录 <tmp>/.deploy/{releases/<名>, current}(布局同 tools/lybra-deploy), 假产品仓主检出(git init 的临时仓,
含 tools/lybra-deploy 与代码副本), 入口装进临时 bin 目录。绝不碰真实 .deploy / ~/.local/bin / 生产门, 不跑 lybra-deploy deploy/rollback。

验收(卡面 ★验收):
 ① 临时部署目录: loop 运行中切换部署 → deploy_changed 结束原文(运行记录写明新旧提交)、status 下一动作原文、重跑续做原文;
    手工等待期经唯一等待原语每轮比对(切换即醒来); agent watch --stream / loop status --wait 同样检测并明示
 ② 主检出制造未提交改动后 `lybra --version` / 命令行为按部署版本原文; 开发开关原文; pip 控制台入口同一入口文本
 ④ 不变量: 布局名与 lybra-deploy 一致、入口文本唯一渲染、VERSION 解析唯一口、部署时更新入口、声明齐
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f131_loop_run_status import _cli, _runs  # noqa: E402  — F131 夹具读法唯一来源
from tools.aipos_cli import deploy_runtime as dr  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.agent_watch_fs import run_fs_watch  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK = f90.TASK
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
CODE_DIRS = ("tools", "schema", "config", "agents")  # 快照里 CLI 运行所需(真实部署 = 整仓 rsync 去 EXCLUDES)
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def _show(line: str) -> None:
    print(line, flush=True)


def _version_text(commit: str, release: str) -> str:
    return (f"release: {release}\ngit_commit: {commit}\ngit_short: {commit[:7]}\nbuilt_at: 2026-10-11T00:00:00Z\n"
            "working_tree_dirty: no\ndeployment_provenance: audited\nauthorization_type: verdict_ref\nauthorization_ref: fixture\n")


def _switch(deploy_dir: Path, release: str) -> None:
    """原子切换 current(同 lybra-deploy 的 ln -sfn: 新链接 + rename)。"""
    tmp = deploy_dir / ".current.tmp"
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(deploy_dir / "releases" / release, tmp)
    os.replace(tmp, deploy_dir / "current")


def _fake_deploy(base: Path) -> Path:
    """只含 VERSION 的两份假快照(loop 进程内靶场用: 运行代码身份经 running_code(code_root=快照) 判定)。"""
    deploy = base / ".deploy"
    for name, commit in (("r-a", COMMIT_A), ("r-b", COMMIT_B)):
        (deploy / "releases" / name).mkdir(parents=True)
        (deploy / "releases" / name / "VERSION").write_text(_version_text(commit, name), encoding="utf-8")
    _switch(deploy, "r-a")
    return deploy


def _guard(deploy: Path, release: str) -> dr.DeployGuard:
    running = dr.running_code(code_root=deploy / "releases" / release, env={})
    assert running.mode == "deploy" and running.release == release, running
    return dr.DeployGuard(running)


def _loop(rig, guard, watch, **kw):
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch, interval=0.05,
                   max_wait=kw.pop("max_wait", 5), max_steps=kw.pop("max_steps", 30), deploy_guard=guard)
    return res, out.getvalue()


def _guarded(base_watch):
    """注入的靶场 watch 接住 loop 在部署快照运行时多传的 deploy_guard(产品唯一等待原语 run_fs_watch 才用它)。"""

    def watch(args, expect_ready, deploy_guard=None):
        return base_watch(args, expect_ready)

    return watch


def _latest(gov: Path) -> dict:
    path = lrr.loop_status(gov, TASK)["runs"][0]["record_path"]
    return lrr.read_run(Path(path), lrr.run_record_declaration(load_loop_contract()))


def _record_frontmatter_deploy(path: Path) -> str:
    text = path.read_text(encoding="utf-8").split("---")[1]
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("deploy:"))
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if not ln.startswith("  "):
            break
        out.append(ln)
    return "\n".join(out + [ln for ln in lines if ln.startswith(("end_reason", "end_message", "outcome", "exit_code"))])


# ===========================================================================
# ① loop 运行中切换部署 → deploy_changed 结束 → status 下一动作 → 重跑从断点续做到结案
# ===========================================================================

def test_item1_loop_deploy_switched_between_steps_ends_deploy_changed_then_resume_completes(rig, tmp_path):
    deploy = _fake_deploy(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    base = f90._agent_watch(rig, [])

    def executor_then_deploy(args, expect_ready, deploy_guard=None):
        rc = base(args, expect_ready)  # 执行体落产物(卡分支提交 + Return)
        _switch(deploy, "r-b")  # 期间他卡 finalize 部署了新版本
        return rc

    res, text = _loop(rig, _guard(deploy, "r-a"), executor_then_deploy)
    _show(f"---- ① loop(运行中切换部署)原文 exit {res.exit_code} ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "deploy_changed") == 6
    assert res.outcome == "deploy_changed" and res.reason == "deploy_changed"
    assert res.message.startswith(f"部署已更新(本进程运行快照 commit {COMMIT_A[:12]} → 现部署 commit {COMMIT_B[:12]}), "
                                  "本 loop 跑的是旧版本代码, 已在步间停下(未执行下一步): 用原命令重跑 loop 从断点续做")
    assert "运行部署快照 r-a" in text and "部署一变即停下(deploy_changed)" in text

    meta = _latest(rig.gov)
    record = Path(lrr.loop_status(rig.gov, TASK)["runs"][0]["record_path"])
    _show("---- ① 运行记录 frontmatter(deploy 段 + 结束段)原文 ----\n" + _record_frontmatter_deploy(record))
    assert meta["end_reason"] == "deploy_changed" and meta["exit_code"] == 6
    assert meta["deploy"]["mode"] == "deploy" and meta["deploy"]["deploy_check"] == "active"
    assert meta["deploy"]["commit"] == COMMIT_A and meta["deploy"]["changed_to"] == COMMIT_B and meta["deploy"]["release"] == "r-a"
    assert meta["end_message"].startswith("部署已更新")
    claims_before = len(f90._records(rig.gov, "claims", TASK))
    assert claims_before == 1

    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--wait", "5"])
    text = out + err
    _show(f"---- ① lybra loop status --wait 5 原文 exit {rc} ----\n{text}")
    rules = lrr.status_contract()["next_action"]
    declared = rules["by_end_reason"]["deploy_changed"]
    assert rc == declared_exit_code("lybra_loop_status", declared)
    assert f"reason=deploy_changed — 部署已更新" in text
    assert f"部署: 本运行快照 r-a commit {COMMIT_A} → 现部署 commit {COMMIT_B}" in text
    assert f"顾问下一动作: {declared}(deploy_changed)" in text

    # 重跑(新进程跑的是新快照 r-b): 推导核按已落记录续推——不重复认领, 从交回往下走到结案
    res2, text2 = _loop(rig, _guard(deploy, "r-b"), _guarded(f90._agent_watch(rig, [])))
    _show(f"---- ① 重跑 loop(新快照 r-b)原文 exit {res2.exit_code} ----\n"
          + "\n".join(l for l in text2.splitlines() if not l.startswith("[ENVELOPE")))
    assert res2.exit_code == exit_code_for(load_loop_contract(), "completed") == 0, text2
    assert len(f90._records(rig.gov, "claims", TASK)) == claims_before  # 断点续做: 认领不重复
    assert "运行部署快照 r-b" in text2 and "[1] run claim" not in text2
    assert _latest(rig.gov)["deploy"]["commit"] == COMMIT_B and "changed_to" not in _latest(rig.gov)["deploy"]


def test_item1_manual_wait_wakes_on_deploy_switch_via_single_watch_primitive(rig, tmp_path):
    """手工模式等待(未拉起 harness)经唯一等待原语 run_fs_watch 每轮比对: 部署一切换即醒来结束, 不空等到 max_wait。"""
    deploy = _fake_deploy(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            _switch(deploy, "r-b")

    def real_watch(args, expect_ready, deploy_guard=None):
        assert deploy_guard is not None, "部署快照运行时 loop 须把 guard 交给唯一等待原语"
        return run_fs_watch(args, expect_ready=expect_ready, deploy_guard=deploy_guard, sleeper=sleeper)

    res, text = _loop(rig, _guard(deploy, "r-a"), real_watch, max_wait=600)
    _show(f"---- ① 手工等待中切换部署 原文 exit {res.exit_code} ----\n{text}")
    assert res.exit_code == 6 and res.reason == "deploy_changed", text
    assert len(sleeps) == 1  # 第一轮等待后即醒来(非等满 600s)
    wait_step = res.steps[-1]
    assert wait_step.kind == "wait" and not wait_step.ok and wait_step.message == "等待中部署已更新"
    assert json.loads(wait_step.output)["deploy_changed"]["to"] == COMMIT_B
    ret = rig.gov / "task_cards" / TASK / "RETURN.md"  # 认领时的交回骨架原样(执行体未交, loop 未空等)
    assert "commit_sha: '(待填写" in ret.read_text(encoding="utf-8")


def test_item1_worktree_run_not_applicable_behavior_unchanged(rig):
    """非部署快照运行(卡工作树测试): 不检测, 运行记录如实标明 not_applicable, 不出部署提示行。"""
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=lambda args, expect_ready: 3,
                   interval=0.05, max_wait=1, max_steps=5)
    meta = _latest(rig.gov)
    _show(f"[① 工作树运行] deploy 段: {meta['deploy']}; 结束 {res.reason}")
    assert meta["deploy"]["mode"] == "worktree" and meta["deploy"]["deploy_check"] == "not_applicable"
    assert res.reason != "deploy_changed" and "部署一变即停下" not in out.getvalue()


def test_item1_loop_status_wait_detects_deploy_change(rig, tmp_path, monkeypatch):
    """`loop status --wait` 也是长驻命令: 等待中部署一变 = 明示并以 deploy_changed 退出(函数级 + CLI 原文与退出码)。"""
    deploy = _fake_deploy(tmp_path / "product")
    f90._card(rig.gov, TASK, "pending")
    # 一份「在跑」的运行记录(pid = 本测试进程, 探活为真)→ 下一动作 continue_wait, --wait 进入等待
    recorder = lrr.LoopRunRecorder(rig.gov, TASK, driver=DRIVER, contract=load_loop_contract(), warn=_show)
    try:
        assert lrr.loop_status(rig.gov, TASK)["next_action"]["action"] == "continue_wait"
        sleeps: list[float] = []

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            _switch(deploy, "r-b")

        report = lrr.wait_for_next_action(rig.gov, TASK, 60, sleeper=sleeper, deploy_guard=_guard(deploy, "r-a"))
        _show(f"[① status --wait 函数级] wait={report['wait']}; deploy_changed.to={report['deploy_changed']['to']}")
        assert report["wait"]["outcome"] == "deploy_changed" and len(sleeps) == 1
        assert report["deploy_changed"]["from"] == COMMIT_A and report["deploy_changed"]["to"] == COMMIT_B

        stale = _guard(deploy, "r-a")  # current 已是 r-b: 本命令(若跑在 r-a)即旧快照
        monkeypatch.setattr(dr.DeployGuard, "for_running_process", classmethod(lambda cls: stale))
        rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--wait", "60"])
        _show(f"---- ① lybra loop status --wait 60(本命令运行旧快照)原文 exit {rc} ----\n{out}{err}")
        assert rc == declared_exit_code("lybra_loop_status", "deploy_changed") == 8
        assert "(--wait 60s: 部署已更新, 等待停下" in out
        assert (f"(AIPOS-F152) 部署已更新(本进程运行快照 commit {COMMIT_A[:12]} → 现部署 commit {COMMIT_B[:12]}" in out
                and f"本命令运行的是旧版本代码, 等待已停下, 用原命令重跑(lybra loop status --task-id {TASK} --wait <秒>)" in out)
    finally:
        recorder.end(outcome="interrupted", exit_code=130, reason="interrupted", message="fixture end")


# ===========================================================================
# ② `lybra` 入口从部署快照运行(真子进程; 临时产品仓 + 临时部署目录 + 临时 bin)
# ===========================================================================

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.pop("PYTHONPATH", None)
    env.update(extra)
    return env


@pytest.fixture
def product(tmp_path: Path):
    """临时产品仓主检出(代码副本 + tools/lybra-deploy, 已提交)+ 部署目录(快照 r-a = 该提交的副本, current → r-a)+ 空 bin。"""
    repo = tmp_path / "product"
    repo.mkdir()
    for d in CODE_DIRS:
        shutil.copytree(REPO_ROOT / d, repo / d, ignore=_IGNORE)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "range")
    _git(repo, "config", "user.email", "range@range.local")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "range: product snapshot")
    head = _git(repo, "rev-parse", "HEAD")
    deploy = repo / ".deploy"
    rel = deploy / "releases" / "r-a"
    rel.parent.mkdir(parents=True)
    for d in CODE_DIRS:
        shutil.copytree(repo / d, rel / d, ignore=_IGNORE)
    (rel / "VERSION").write_text(_version_text(head, "r-a"), encoding="utf-8")
    _switch(deploy, "r-a")
    gov = tmp_path / "gov"
    gov.mkdir()
    (gov / "marker.txt").write_text("x\n", encoding="utf-8")
    from types import SimpleNamespace

    return SimpleNamespace(repo=repo, deploy=deploy, head=head, bin=tmp_path / "bin", gov=gov)


def _install(product) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(product.repo / "tools" / "lybra-deploy"), "install-entry", "--bin-dir", str(product.bin)],
                          cwd=str(product.repo), env=_env(), capture_output=True, text=True, timeout=120)


def test_item2_entry_runs_deploy_snapshot_even_with_dirty_main_checkout(product):
    inst = _install(product)
    _show(f"---- ② tools/lybra-deploy install-entry 原文 rc={inst.returncode} ----\n{inst.stdout}{inst.stderr}")
    assert inst.returncode == 0, inst.stderr
    entry = product.bin / "lybra"
    assert f"✓ lybra 入口新装: {entry} → {product.deploy / 'current'}" in inst.stdout
    assert dr.LAUNCHER_MARK in entry.read_text(encoding="utf-8") and os.access(entry, os.X_OK)

    # 主检出制造未提交改动: 工作树里 CLI 主模块被改坏(中间态), 另有未跟踪文件
    broken = product.repo / "tools" / "aipos_cli" / "aipos_cli.py"
    broken.write_text('raise SystemExit("工作树中间态代码被加载(本不该)")\n', encoding="utf-8")
    (product.repo / "tools" / "aipos_cli" / "half_merged.py").write_text("<<<<<<< HEAD\n", encoding="utf-8")
    status = _git(product.repo, "status", "--porcelain")
    _show(f"---- ② 主检出 git status --porcelain 原文 ----\n{status}")
    assert "M tools/aipos_cli/aipos_cli.py" in status and "?? tools/aipos_cli/half_merged.py" in status

    direct = subprocess.run([sys.executable, "-m", "tools.aipos_cli", "--version"], cwd=str(product.repo), env=_env(),
                            capture_output=True, text=True, timeout=120)
    _show(f"[② 对照: 直接跑工作树代码] rc={direct.returncode} {direct.stderr.strip()}")
    assert direct.returncode != 0 and "工作树中间态代码被加载" in direct.stderr

    ver = subprocess.run([str(entry), "--version"], cwd=str(product.repo), env=_env(), capture_output=True, text=True, timeout=120)
    _show(f"---- ② lybra --version(主检出脏)原文 rc={ver.returncode} ----\n{ver.stdout}{ver.stderr}")
    real_rel = os.path.realpath(product.deploy / "current")
    assert ver.returncode == 0 and ver.stderr == ""
    assert ver.stdout.strip() == f"lybra 部署快照 r-a(commit {product.head}); 代码根 {real_rel}"

    watch = subprocess.run([str(entry), "agent", "watch", "--workspace-root", str(product.gov), "--expect", "marker.txt",
                            "--interval", "0.2", "--timeout", "5"], cwd=str(product.repo), env=_env(),
                           capture_output=True, text=True, timeout=120)
    _show(f"---- ② lybra agent watch(主检出脏, 按部署版本运行)原文 rc={watch.returncode} ----\n{watch.stdout}{watch.stderr}")
    assert watch.returncode == 0 and json.loads(watch.stdout)["expect_satisfied"] == ["marker.txt"]


def test_item2_dev_switch_marks_first_line_and_loads_named_tree(product):
    assert _install(product).returncode == 0
    entry = product.bin / "lybra"
    dev = subprocess.run([str(entry), "--version"], cwd=str(product.gov), env=_env(LYBRA_DEV_TREE=str(REPO_ROOT)),
                         capture_output=True, text=True, timeout=120)
    _show(f"---- ② 开发开关 LYBRA_DEV_TREE=<卡工作树> lybra --version 原文 rc={dev.returncode} ----\n"
          f"[stderr]\n{dev.stderr}[stdout]\n{dev.stdout}")
    assert dev.returncode == 0
    assert dev.stderr.splitlines()[0] == (f"[lybra] 开发模式(LYBRA_DEV_TREE): 代码取自 {REPO_ROOT}, 不是部署快照 "
                                          f"{product.deploy / 'current'}; 取消: unset LYBRA_DEV_TREE")
    assert dev.stdout.startswith(f"lybra 开发模式 LYBRA_DEV_TREE={REPO_ROOT}(非部署快照; HEAD ")

    (product.repo / "tools" / "aipos_cli" / "aipos_cli.py").write_text('raise SystemExit("dev 树代码被加载")\n', encoding="utf-8")
    named = subprocess.run([str(entry), "--version"], cwd=str(product.gov), env=_env(LYBRA_DEV_TREE=str(product.repo)),
                           capture_output=True, text=True, timeout=120)
    _show(f"[② 开发开关指向改坏的主检出] rc={named.returncode} stderr={named.stderr.strip()!r}")
    assert named.returncode != 0 and named.stderr.startswith("[lybra] 开发模式") and "dev 树代码被加载" in named.stderr

    bogus = subprocess.run([str(entry), "--version"], cwd=str(product.gov), env=_env(LYBRA_DEV_TREE=str(product.gov)),
                           capture_output=True, text=True, timeout=120)
    _show(f"[② 开发开关指向非代码树] rc={bogus.returncode} {bogus.stderr.strip()}")
    assert bogus.returncode == 1 and "不是 lybra 产品代码树" in bogus.stderr  # fail-closed, 不回落部署快照


def test_item2_pip_console_entry_is_trampoline_to_same_launcher(product):
    """pyproject [project.scripts] lybra = deploy_runtime:main: 从(可能脏的)工作树导入后, exec 同一份入口文本跑部署快照。"""
    (product.repo / "tools" / "aipos_cli" / "aipos_cli.py").write_text('raise SystemExit("工作树被加载")\n', encoding="utf-8")
    code = (f"import sys; sys.path.insert(0, {str(product.repo)!r}); "
            "from tools.aipos_cli.deploy_runtime import main; main()")
    ver = subprocess.run([sys.executable, "-c", code, "--version"], cwd=str(product.gov), env=_env(),
                         capture_output=True, text=True, timeout=120)
    _show(f"---- ② pip 控制台入口(跳板)lybra --version 原文 rc={ver.returncode} ----\n{ver.stdout}{ver.stderr}")
    assert ver.returncode == 0 and ver.stdout.startswith(f"lybra 部署快照 r-a(commit {product.head})")
    py = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^lybra = "tools\.aipos_cli\.deploy_runtime:main"$', py, re.M)


def test_item1_agent_watch_stream_from_snapshot_detects_deploy_switch_then_rerun_uses_new(product):
    """真子进程: 部署快照里跑的 agent watch --stream, 运行中切换部署 → kind:deploy_changed + end, 退出码 deploy_changed;
    重跑同一命令 = 新快照。"""
    assert _install(product).returncode == 0
    entry = product.bin / "lybra"
    rel_b = product.deploy / "releases" / "r-b"
    shutil.copytree(product.deploy / "releases" / "r-a", rel_b, copy_function=os.link)
    (rel_b / "VERSION").unlink()  # 硬链接副本: 先断链再写, 不改 r-a 的 VERSION
    (rel_b / "VERSION").write_text(_version_text(COMMIT_B, "r-b"), encoding="utf-8")
    proc = subprocess.Popen([str(entry), "agent", "watch", "--workspace-root", str(product.gov), "--stream", "--expect", "marker.txt",
                             "--interval", "0.2", "--timeout", "60"], cwd=str(product.repo), env=_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        first = proc.stdout.readline()  # 布防即检: 进程已起、已记下运行快照
        assert json.loads(first)["kind"] == "expect", first
        _switch(product.deploy, "r-b")
        rest, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    _show(f"---- ① lybra agent watch --stream(运行中切换部署)原文 exit {proc.returncode} ----\n{first}{rest}{err}")
    events = [json.loads(ln) for ln in rest.splitlines() if ln.strip()]
    assert proc.returncode == declared_exit_code("lybra_agent_watch", "deploy_changed") == 6
    assert [e["kind"] for e in events] == ["deploy_changed", "end"] and events[1]["reason"] == "deploy_changed"
    assert events[0]["from"] == product.head and events[0]["to"] == COMMIT_B and events[0]["running_release"] == "r-a"
    assert events[0]["message"].endswith("本命令运行的是旧版本代码, 已停下, 用原命令重跑")
    again = subprocess.run([str(entry), "--version"], cwd=str(product.repo), env=_env(), capture_output=True, text=True, timeout=120)
    _show(f"[① 重跑同一入口] {again.stdout.strip()}")
    assert again.stdout.startswith(f"lybra 部署快照 r-b(commit {COMMIT_B})")


def test_item2_install_entry_refuses_without_snapshot_and_backs_up_foreign_entry(tmp_path):
    bin_dir = tmp_path / "bin"
    with pytest.raises(dr.EntryError, match="不是部署链接"):
        dr.install_entry(tmp_path / "nope" / "current", bin_dir)
    deploy = _fake_deploy(tmp_path / "p")  # 快照里没有入口实现
    with pytest.raises(dr.EntryError, match="不是含本入口实现的部署快照"):
        dr.install_entry(deploy / "current", bin_dir)
    rel = deploy / "releases" / "r-a"
    (rel / "tools" / "aipos_cli").mkdir(parents=True)
    (rel / "tools" / "aipos_cli" / "deploy_runtime.py").write_text("# stub\n", encoding="utf-8")
    bin_dir.mkdir()
    (bin_dir / "lybra").write_text("#!/usr/bin/python3\nfrom tools.aipos_cli.aipos_cli import main\n", encoding="utf-8")
    result = dr.install_entry(deploy / "current", bin_dir, interpreter="/usr/bin/python3")
    _show(f"[② 替换 pip 控制台脚本] {result}")
    assert result["previous"] == "other" and Path(result["backup"]).read_text(encoding="utf-8").endswith("import main\n")
    assert dr.install_entry(deploy / "current", bin_dir, interpreter="/usr/bin/python3")["previous"] == "launcher"


# ===========================================================================
# ④ 不变量(防回潮)
# ===========================================================================

def test_item4_layout_names_match_lybra_deploy_and_deploy_updates_entry_after_switch():
    script = (REPO_ROOT / "tools" / "lybra-deploy").read_text(encoding="utf-8")
    assert f'RELEASES_DIR="$DEPLOY_DIR/{dr.RELEASES_DIRNAME}"' in script
    assert f'CURRENT_LINK="$DEPLOY_DIR/{dr.CURRENT_LINKNAME}"' in script
    assert f'DEPLOY_DIR="$PRODUCT_REPO/{dr.DEPLOY_DIRNAME}"' in script
    assert f'"$staging_dir/{dr.VERSION_FILENAME}"' in script
    deploy_fn = script[script.index("cmd_deploy() {"):script.index("# ── rollback")]
    switch = deploy_fn.index('ln -sfn "$release_dir" "$CURRENT_LINK"')
    assert switch < deploy_fn.index("Assertion passed: current → HEAD") < deploy_fn.index("if ! cmd_install_entry; then")
    assert "install-entry) cmd_install_entry ;;" in script
    # 入口文本只有一处渲染(deploy_runtime.launcher_source): 脚本只调它, 不内嵌第二份 python 入口
    install_fn = script[script.index("cmd_install_entry() {"):script.index("# ── deploy ─")]
    assert "python3 -m tools.aipos_cli.deploy_runtime install-entry" in install_fn
    assert "sys.path" not in script and "LYBRA_DEV_TREE" not in install_fn


def test_item4_single_implementations_and_fail_closed():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "deploy_runtime.py").read_text(encoding="utf-8")
    assert "git_commit:" not in src  # VERSION 解析唯一口 gate_drift.read_version_commit
    assert "read_version_commit" in src and "code_repo_schema_root" in src
    assert not re.search(r"except\s+(Exception|BaseException)?\s*:", src) and "except Exception" not in src
    for name in ("loop_driver.py", "agent_watch_fs.py", "loop_run_record.py"):  # 长驻命令只经 DeployGuard 判定, 不自读部署
        text = (REPO_ROOT / "tools" / "aipos_cli" / name).read_text(encoding="utf-8")
        assert "DeployGuard" in text, name
        assert "git_commit:" not in text and '".deploy"' not in text and "releases" not in text, name
    contract = load_loop_contract()
    assert contract["deploy_consistency"]["end_reason"] in contract["run_record"]["end_reasons"]
    assert "deploy_changed" in lrr.status_contract()["next_action"]["by_end_reason"]
    for verb in ("lybra_loop", "lybra_agent_watch", "lybra_loop_status"):
        assert declared_exit_code(verb, "deploy_changed")
