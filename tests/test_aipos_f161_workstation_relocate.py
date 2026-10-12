"""AIPOS-F161 — pi 工位迁出共用检出: `lybra project relocate-workstation`(预演 / 确认 / 回滚)。

靶场 = 临时 home 根(治理仓 + 临时远端裸仓)+ 临时「共用 git 检出」(工位父根, 内含本项目审计工位, 工位态部分已入该检出版本库)
+ 假门 RepoGate(test_aipos_f82: 清单 = 真 build_role_manifest, 内容 = 产品树真文件)。项目名 / 实例名 / 卡号均为靶场值。
禁碰真实治理根、真实工位、生产门; 凭据文件只做 sha256 / 权限前后比对(夹具假凭据, 测试不打印内容)。

验收(卡面 ★验收):
 ① 预演原文 + 零写入; 确认后新落点工位态完整(凭据存在、权限不变、sha256 前后一致)、位置声明已改且已落账、sync/清单一致、
    check-workstation 与 enroll-where 指向新落点、拉起派生 cwd = 新落点
 ② 拒绝三条: 在途(运行记录拉起进程 / loop 等待本实例 / 进程 cwd)、新落点在他人 git 检出内、新落点非空 —— 均零写入
 ③ 回滚(预演 + 确认)
 ④ 非本项目工位拒(零写入)
 ⑤ 单一来源: 位置事件解析唯一实现、落账走 F158/F159 包装、两阶段旗标同一包装、文档写进顾问技能与 docs
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402  — 靶场构件唯一来源
from test_aipos_f82_workstation_intake import RepoGate, _secret, _station  # noqa: E402  — 假门与工位构件唯一来源
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture(lrig 依赖)
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — 拉起靶场(收尾无残留断言)

PROJECT = "f161-probe"
ADVISOR = f"advisor.{PROJECT}.h1"
AUDITOR = f"audit.{PROJECT}.ws"
OTHER = "audit.f161-other.ws"
CARD = "PROBE-F161-A"
RELOCATE = "relocate-workstation"


def _show(text: str) -> None:
    print(text, flush=True)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
                          cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _digest(root: Path) -> dict[str, str]:
    """目录树指纹(文件 sha256 + 链接目标 + 权限); 用于零写入断言。"""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if ".git" in p.relative_to(root).parts:
            continue
        mode = oct(stat.S_IMODE(p.lstat().st_mode))
        if p.is_symlink():
            out[rel] = f"link:{os.readlink(p)}:{mode}"
        elif p.is_file():
            out[rel] = f"{hashlib.sha256(p.read_bytes()).hexdigest()}:{mode}"
        else:
            out[rel] = f"dir:{mode}"
    return out


def _credentials(ws: Path) -> dict[str, tuple[str, str]]:
    """.lybra/ 下文件 → (sha256, 权限)(夹具假凭据; 只比对指纹, 不打印内容)。"""
    return {p.relative_to(ws).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), oct(stat.S_IMODE(p.stat().st_mode)))
            for p in sorted((ws / ".lybra").rglob("*")) if p.is_file()}


def _land(gov: Path, instance: str, role: str, ws: Path) -> None:
    """land 行(产品唯一渲染 _trail_line + 唯一写口 _write_trail_line)。"""
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path

    project = instance.split(".")[1]
    _write_trail_line(enrollment_trail_path(gov), _trail_line(
        action="land", code_id=f"enroll_fixture_{role}", role=role, instance=instance, project=project, by="(agent-enroll)",
        reason=f"host={socket.gethostname()} workstation={ws} files=['connection.json', 'role']"))


@pytest.fixture
def wrig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import autonomy_policy, confirm_client
    from tools.aipos_cli import distribution_sync as ds

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER", "LYBRA_TOKEN", "LYBRA_HARNESS_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", PROJECT, "--home-root", hroot, "--actor", ADVISOR)
    assert rc == 0, out + err
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(hroot))
    remote = init_governance_repo(hroot)
    gov = hroot / PROJECT
    # 共用 git 检出(工位父根): 本项目审计工位 + 他项目工位; 工位 AGENTS.md / .pi/settings.json 入该检出版本库(真实形)
    shared = tmp_path / "shared-checkout"
    shared.mkdir()
    _git(shared, "init", "-q", "-b", "main")
    ws = _station(shared, "probe-auditor", role="auditor", instance=AUDITOR, gov=gov, project=PROJECT)
    (ws / ".lybra" / "connection.json").chmod(0o600)
    (ws / ".lybra").chmod(0o700)
    _write(ws / ".pi" / "settings.json", json.dumps({"defaultProvider": "fixture", "enableSkillCommands": True, "skills": []}))
    _write(ws / "scratchpad" / "runall-tip.log", "fixture log\n")
    RepoGate.roles = {_secret("auditor"): ("auditor", None)}
    monkeypatch.setattr(confirm_client, "GateClient", RepoGate)
    monkeypatch.setattr(autonomy_policy, "launchable_harnesses",
                        lambda: {"pi": {"argv": [sys.executable, "-c", "pass", "{kickoff}"], "events": "pi-json"}})
    first = ds.sync(harness_root=ws, governance_root=gov)  # 共用父根已有 _distributed / _shared + 章程 + 挂载(真实形)
    assert first["status"] == "synced", first
    _write(shared / ".gitignore", ".lybra/\n*/.lybra/\n")
    _git(shared, "add", "-A")
    _git(shared, "commit", "-q", "-m", "shared checkout fixture")
    _land(gov, AUDITOR, "auditor", ws)
    _git(hroot, "add", "-A")
    _git(hroot, "commit", "-q", "-m", "fixture: enrollment trail")
    _git(hroot, "push", "-q", "origin", "main")
    return SimpleNamespace(home=home, hroot=hroot, gov=gov, remote=remote, shared=shared, ws=ws, tmp=tmp_path,
                           new=tmp_path / "workstations-home" / f"{PROJECT}-workstations" / "probe-auditor")


def _trail(gov: Path) -> Path:
    from tools.aipos_cli.enrollment import enrollment_trail_path

    return enrollment_trail_path(gov)


def _relocate(r: SimpleNamespace, *extra: str) -> tuple[int, str, str]:
    return _cli("project", RELOCATE, "--workspace-root", r.gov, "--instance", AUDITOR, *extra)


def _zero_write_state(r: SimpleNamespace) -> tuple:
    return (_trail(r.gov).read_bytes(), _digest(r.ws), _digest(r.new.parent), _git(r.hroot, "rev-parse", "HEAD"))


# ===========================================================================
# ① 预演 → 确认: 工位态完整、声明已改并落账、sync 一致、核查 / 接入诊断 / 拉起指向新落点
# ===========================================================================

def test_item1_dry_run_then_confirm_relocates_and_lands(wrig):
    from tools.aipos_cli import distribution_sync as ds
    from tools.aipos_cli.enrollment import workstation_location

    before = _zero_write_state(wrig)
    creds = _credentials(wrig.ws)
    rc, out, err = _relocate(wrig, "--to", wrig.new)
    _show(f"---- ① 预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "预览(未写; 加 --confirm 写入)" in out and str(wrig.ws) in out and str(wrig.new) in out
    assert ".lybra/connection.json [0600]" in out and "不读内容" in out and _secret("auditor") not in out + err
    assert "✓ 新落点不在任何 git 检出内" in out and "✓ 在途运行" in out and f"位于 git 检出 {wrig.shared} 内" in out
    assert _zero_write_state(wrig) == before  # 预演零写入

    rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
    _show(f"---- ① 确认原文 rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    assert _secret("auditor") not in out + err
    # 工位态完整: 凭据在新落点、sha256 与权限前后一致; 旧落点只剩只读退役指针
    assert _credentials(wrig.new) == creds and creds, (_credentials(wrig.new), creds)
    assert sorted(p.name for p in wrig.ws.iterdir()) == ["LYBRA-RELOCATED.json"]
    pointer = wrig.ws / "LYBRA-RELOCATED.json"
    assert oct(stat.S_IMODE(pointer.stat().st_mode)) == "0o444"
    pdata = json.loads(pointer.read_text(encoding="utf-8"))
    assert pdata["instance"] == AUDITOR and pdata["moved_to"] == str(wrig.new) and "--rollback" in pdata["rollback"]
    _show(f"[①] 凭据 sha256/权限 前后一致: {sorted(creds)}; 旧落点 = {sorted(p.name for p in wrig.ws.iterdir())}")
    # 位置声明(唯一来源 enrollment_log)已改且已落账
    last = _trail(wrig.gov).read_text(encoding="utf-8").rstrip("\n").splitlines()[-1]
    _show(f"[①] 接入日志末行: {last}")
    assert "  relocate  " in last and f"relocated_from={wrig.ws} workstation={wrig.new}" in last and f"by={ADVISOR}" in last
    assert workstation_location(wrig.gov, AUDITOR)["dir"] == str(wrig.new)
    msg = _git(wrig.hroot, "log", "-1", "--format=%B")
    names = _git(wrig.hroot, "show", "--name-only", "--format=", "HEAD").splitlines()
    pushed = _git(wrig.hroot, "rev-parse", "HEAD") == _git(wrig.remote, "rev-parse", "main")
    _show(f"[①] 落账 git log -1:\n{msg}\n[names] {names} pushed={pushed}")
    assert f"项目配置落账 {RELOCATE} {PROJECT}" in msg and f"Actor: {ADVISOR}" in msg and pushed
    assert names == [_trail(wrig.gov).relative_to(wrig.hroot).as_posix()]
    assert "落账: verdict=PASS" in out
    # sync / 清单 / 章程一致: 新父根清单在, 稳态预演 plan 与 would_prune 皆空, 章程按新落点渲染
    steady = ds.sync(harness_root=wrig.new, governance_root=wrig.gov, dry_run=True)
    manifest = wrig.new.parent / "_distributed" / ".version-auditor"
    _show(f"[①] 新落点 sync 稳态预演: plan={steady['plan']} would_prune={steady['would_prune']} 清单={manifest} 在={manifest.is_file()}")
    assert steady["plan"] == [] and steady["would_prune"] == [] and manifest.is_file()
    assert f"`{wrig.new}`" in (wrig.new / "AGENTS.md").read_text(encoding="utf-8")
    assert all((wrig.new / ".pi" / "skills" / name).resolve().is_dir() for name in os.listdir(wrig.new / ".pi" / "skills"))
    assert "sync 稳态预演: plan=[] would_prune=[]" in out
    # check-workstation / enroll-where 指向新落点
    rc, ck, ckerr = _cli("project", "check-workstation", PROJECT, "--home-root", wrig.hroot, "--instance", AUDITOR, "--harness", "pi")
    _show(f"---- ① check-workstation 原文 rc={rc} ----\n{ck}{ckerr}")
    assert rc == 0 and f":{wrig.new} transport=local" in ck and f"{wrig.new}/.lybra/role instance={AUDITOR}" in ck
    rc, wh, wherr = _cli("roles", "--workspace-root", wrig.gov, "enroll-where", "--instance", AUDITOR)
    _show(f"---- ① enroll-where 原文 rc={rc} ----\n{wh}{wherr}")
    assert rc == 0 and f"workstation={wrig.new}" in wh and f"dir={wrig.new}" in wh
    # 共用检出: 本命令未跑任何 git(HEAD 不变), 已入库的工位文件在该检出里显示为删除(由该检出的维护方处置)
    assert _git(wrig.shared, "rev-parse", "HEAD") and "probe-auditor/AGENTS.md" in _git(wrig.shared, "ls-files")


# ===========================================================================
# ① 拉起派生 cwd = 新落点(F95 拉起靶场)
# ===========================================================================

def test_item1_launch_cwd_follows_relocation(lrig, tmp_path, monkeypatch):
    import test_aipos_f90_loop_one_stage as f90
    import test_aipos_f95_harness_launch as f95
    from test_aipos_f78_engine_agnostic import EXEC
    from tools.aipos_cli import confirm_client
    from tools.aipos_cli.autonomy_policy import load_policy
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract, plan_launch
    from tools.aipos_cli.next_resolver import _ensure_worktree
    from tools.aipos_cli.workstation_relocate import relocate_workstation

    f90._card(lrig.gov, CARD, "claimed", claimed_by=EXEC, harness="pi")
    f90.f78._claim_record(lrig.gov, CARD, EXEC)
    assert _ensure_worktree(lrig.gov, CARD)["ok"]
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    RepoGate.roles = {"fixture-ws-not-a-secret": ("executor", None)}
    monkeypatch.setattr(confirm_client, "GateClient", RepoGate)
    decl = launch_declaration(load_loop_contract())
    policy = load_policy(lrig.gov, f95.POLICY_LAUNCH)
    before = plan_launch(lrig.gov, CARD, policy=policy, no_launch=False, decl=decl, already_launched=set())
    new = tmp_path / "relocated" / "probe-executor"
    result = relocate_workstation(lrig.gov, EXEC, to=new, dry_run=False, actor="advisor.lybra.test")
    after = plan_launch(lrig.gov, CARD, policy=policy, no_launch=False, decl=decl, already_launched=set())
    _show(f"[①拉起] 迁前 plan_launch.cwd={before.cwd!r} refusal={before.refusal!r}\n"
          f"[①拉起] 迁后 plan_launch.cwd={after.cwd!r} refusal={after.refusal!r} argv[0]={after.argv[:1]}")
    assert before.cwd == str(lrig.ws_exec) and not before.refusal
    assert result["ok"] and after.cwd == str(new) and not after.refusal and after.kickoff


# ===========================================================================
# ② 拒绝: 在途 / 新落点在他人检出内 / 新落点非空(均零写入)
# ===========================================================================

def _card_file(gov: Path, card: str, instance: str) -> None:
    _write(gov / "5_tasks" / "queue" / "claimed" / f"{card.lower()}.md",
           f"---\ntask_id: {card}\ntitle: {card} probe\nproject: {PROJECT}\nassigned_to: {instance}\nstatus: claimed\n---\n# {card}\n")


def test_item2_refusals_in_flight_checkout_nonempty_zero_write(wrig):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    before = _zero_write_state(wrig)
    # (a) 在途拉起: 运行记录里未收尾的拉起, workstation = 本工位, 进程活(本用例自起的子进程, cwd 不在工位, 只由运行记录判出)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=str(wrig.tmp))
    rec = None
    try:
        rec = LoopRunRecorder(wrig.gov, CARD, driver=ADVISOR, contract=load_loop_contract(), warn=_show)
        rec.launch_started(SimpleNamespace(card=CARD, harness="pi", location={"transport": "local", "host": socket.gethostname()},
                                           cwd=str(wrig.ws)),
                           SimpleNamespace(pid=child.pid, pgid=child.pid, remote_pgid=None, counts={}))
        rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
        _show(f"---- ②(a) 在途拉起 拒原文 rc={rc} ----\n{out}{err}")
        assert rc == 1 and "RELOCATE_IN_FLIGHT" in err and f"在途拉起: 卡 {CARD} 的 pi 进程 pid={child.pid}" in err
    finally:
        child.kill()
        child.wait(timeout=10)
        if rec is not None:
            rec.launch_ended("killed", -9, "fixture")
            rec.end(outcome="interrupted", exit_code=130, reason="interrupted", message="fixture")
    # (b) 在途 loop 等待本实例产物: loop 进程活(本测试进程)且当前步是等待步, 等待卡的干活实例 = 本实例
    card_b = f"{CARD}B"
    _card_file(wrig.gov, card_b, AUDITOR)
    rec = LoopRunRecorder(wrig.gov, card_b, driver=ADVISOR, contract=load_loop_contract(), warn=_show)
    try:
        rec.step_started(1, "wait", "N1", "claimed", card_b, mode="manual")
        rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
        _show(f"---- ②(a') loop 等待 拒原文 rc={rc} ----\n{out}{err}")
        assert rc == 1 and "RELOCATE_IN_FLIGHT" in err and f"在途 loop 等待本实例产物: 卡 {card_b}" in err
    finally:
        rec.end(outcome="interrupted", exit_code=130, reason="interrupted", message="fixture")
    # (c) 在途进程: 本机有进程 cwd 在工位目录内(手工开的 harness 会话形)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=str(wrig.ws))
    try:
        rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
        _show(f"---- ②(a'') 进程 cwd 拒原文 rc={rc} ----\n{out}{err}")
        assert rc == 1 and "RELOCATE_IN_FLIGHT" in err and f"在途进程: pid={child.pid}" in err
    finally:
        child.kill()
        child.wait(timeout=10)
    # (d) 新落点在他人 git 检出内
    other = wrig.tmp / "other-checkout"
    other.mkdir()
    _git(other, "init", "-q")
    rc, out, err = _relocate(wrig, "--to", other / "sub" / "probe-auditor", "--confirm", "--actor", ADVISOR)
    _show(f"---- ②(b) 他人检出内 拒原文 rc={rc} ----\n{out}{err}")
    assert rc == 1 and "RELOCATE_TARGET_IN_CHECKOUT" in err and f"位于 git 检出 {other} 内" in err
    # (e) 新落点已存在且非空
    _write(wrig.new / "someone-else.txt", "x\n")
    rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
    _show(f"---- ②(c) 新落点非空 拒原文 rc={rc} ----\n{out}{err}")
    assert rc == 1 and "RELOCATE_TARGET_NOT_EMPTY" in err and "someone-else.txt" in err
    (wrig.new / "someone-else.txt").unlink()
    wrig.new.rmdir()
    # 全部零写入: 接入日志 / 旧工位 / 新落点父 / 治理仓 HEAD 不变(运行记录是本用例自造的夹具, 不计)
    after = _zero_write_state(wrig)
    assert after[0] == before[0] and after[1] == before[1] and after[3] == before[3]
    assert not wrig.new.exists()


# ===========================================================================
# ③ 回滚(预演 + 确认)
# ===========================================================================

def test_item3_rollback_dry_run_then_confirm(wrig):
    from tools.aipos_cli.enrollment import workstation_location

    creds = _credentials(wrig.ws)
    rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
    assert rc == 0, out + err
    snap = (_trail(wrig.gov).read_bytes(), _digest(wrig.new), _digest(wrig.ws))
    rc, out, err = _relocate(wrig, "--rollback")
    _show(f"---- ③ 回滚预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "回滚" in out and f"新落点: {wrig.ws}" in out and "只含本实例退役指针" in out
    assert (_trail(wrig.gov).read_bytes(), _digest(wrig.new), _digest(wrig.ws)) == snap  # 预演零写入
    rc, out, err = _relocate(wrig, "--rollback", "--confirm", "--actor", ADVISOR)
    _show(f"---- ③ 回滚确认原文 rc={rc} ----\n{out}{err}")
    assert rc == 0, out + err
    assert workstation_location(wrig.gov, AUDITOR)["dir"] == str(wrig.ws)
    assert _credentials(wrig.ws) == creds
    assert sorted(p.name for p in wrig.new.iterdir()) == ["LYBRA-RELOCATED.json"]
    last = _trail(wrig.gov).read_text(encoding="utf-8").rstrip("\n").splitlines()[-1]
    _show(f"[③] 接入日志末行: {last}")
    assert "relocation=rollback" in last and f"relocated_from={wrig.new} workstation={wrig.ws}" in last
    assert f"项目配置落账 {RELOCATE} {PROJECT}" in _git(wrig.hroot, "log", "-1", "--format=%B")
    assert f"`{wrig.ws}`" in (wrig.ws / "AGENTS.md").read_text(encoding="utf-8")  # 章程按旧落点重渲染
    # 再迁一次: 新落点只含本实例退役指针 = 可作落点
    rc, out, err = _relocate(wrig, "--to", wrig.new, "--confirm", "--actor", ADVISOR)
    _show(f"[③] 回滚后再迁 rc={rc}: {out.splitlines()[1] if out else err}")
    assert rc == 0 and workstation_location(wrig.gov, AUDITOR)["dir"] == str(wrig.new) and _credentials(wrig.new) == creds
    # 再回滚一次(最新事件仍是 relocate = 可回; 只有最新位置事件是 land 时才拒, 见 test_item3_rollback_without_relocation_refused)
    rc, out, err = _cli("project", RELOCATE, "--workspace-root", wrig.gov, "--instance", AUDITOR, "--rollback", "--confirm",
                        "--actor", ADVISOR)
    assert rc == 0, out + err


def test_item3_rollback_without_relocation_refused(wrig):
    before = _zero_write_state(wrig)
    rc, out, err = _relocate(wrig, "--rollback", "--confirm", "--actor", ADVISOR)
    _show(f"---- ③ 无迁移可回滚 拒原文 rc={rc} ----\n{out}{err}")
    assert rc == 1 and "RELOCATE_NOTHING_TO_ROLLBACK" in err and _zero_write_state(wrig) == before


# ===========================================================================
# ④ 非本项目工位拒(零写入)
# ===========================================================================

def test_item4_foreign_project_workstation_refused_zero_write(wrig):
    other_ws = _station(wrig.shared, "other-auditor", role="auditor", instance=OTHER, gov=wrig.gov, project="f161-other")
    _land(wrig.gov, OTHER, "auditor", other_ws)
    before = (_trail(wrig.gov).read_bytes(), _digest(other_ws), _digest(wrig.new.parent))
    for phase in ((), ("--confirm", "--actor", ADVISOR)):
        rc, out, err = _cli("project", RELOCATE, "--workspace-root", wrig.gov, "--instance", OTHER, "--to", wrig.new, *phase)
        _show(f"---- ④ 非本项目工位 {'确认' if phase else '预演'} 拒原文 rc={rc} ----\n{out}{err}")
        assert rc == 1 and "RELOCATE_NOT_THIS_PROJECT" in err and "f161-other" in err
    assert (_trail(wrig.gov).read_bytes(), _digest(other_ws), _digest(wrig.new.parent)) == before


def test_item4_confirm_requires_explicit_target_and_dry_run_suggests(wrig):
    rc, out, err = _relocate(wrig)
    _show(f"---- ④' 无 --to 预演原文(建议落点) rc={rc} ----\n{out}{err}")
    assert rc == 0 and f"建议落点: {wrig.shared.parent / (PROJECT + '-workstations') / 'probe-auditor'}" in out
    rc, out, err = _relocate(wrig, "--confirm", "--actor", ADVISOR)
    assert rc == 1 and "RELOCATE_TARGET_REQUIRED" in err


# ===========================================================================
# ⑤ 单一来源 / 防碎片化 / 文档
# ===========================================================================

def test_item5_single_source_and_docs():
    import ast

    from tools.aipos_cli import enrollment
    from tools.aipos_cli.workstation_relocate import relocation_declaration
    from tools.schema_loader import load_schema

    decl = relocation_declaration()
    assert decl["event_action"] == enrollment.RELOCATE_ACTION
    assert enrollment._LAND_LINE_RE.match(f"- 2026-01-01T00:00:00Z  {enrollment.RELOCATE_ACTION}  code_id=c  role=auditor  "
                                          "instance=a.b.c  project=b  by=x  reason=host=h relocation=forward relocated_from=/o workstation=/n")
    en_src = (REPO_ROOT / "tools/aipos_cli/enrollment.py").read_text(encoding="utf-8")
    assert en_src.count("relocated_from=") >= 1 and en_src.count("def append_relocation_event(") == 1
    rel_src = (REPO_ROOT / "tools/aipos_cli/workstation_relocate.py").read_text(encoding="utf-8")
    assert "_write_trail_line" not in rel_src and "governance_commit" not in rel_src.split('"""', 2)[2]
    cli = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    seg = cli[cli.index(f'add_parser("{RELOCATE}"'):cli.index(f'add_parser("{RELOCATE}"') + 3000]
    assert f'_project_json_two_phase_flags(project_relocate_parser, "{RELOCATE}", family="enrollment_log_writers")' in seg
    tree = ast.parse(cli)
    lands = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_land_project_writes"
             and any(isinstance(k.value, ast.Constant) and k.value.value == RELOCATE for k in n.keywords)]
    assert len(lands) == 1
    verbs = load_schema("verbs", REPO_ROOT)
    assert RELOCATE in verbs["two_phase_protocol"]["enrollment_log_writers"]["commands"]
    assert RELOCATE in verbs["verbs"]["lybra_governance_commit"]["post_write_landing"]["commands"]["project_writers"]["subcommands"]
    skill = (REPO_ROOT / "agents/skills/advisor-commands/SKILL.md").read_text(encoding="utf-8")
    docs = (REPO_ROOT / "docs/mcp-agent-setup.md").read_text(encoding="utf-8")
    assert f"lybra project {RELOCATE}" in skill and "--rollback" in skill
    assert f"lybra project {RELOCATE}" in docs and "--rollback" in docs
