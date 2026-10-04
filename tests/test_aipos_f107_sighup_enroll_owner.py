"""AIPOS-F107 — chris 接入运行时前置(#43/#44): loop 拉起期 SIGHUP 清进程组; 接入事件写工位所属项目 log。

靶场: ①沿用 AIPOS-F95 靶场(tests/test_aipos_f95_harness_launch.py setup_launch / _land; F90 tmp 治理根 + 假 harness
tests/fake_harness.py), 子进程跑 run_loop_cli 后发信号/断开 stdout; ②tmp home 下两个已建项目 proj-a(签发门)/proj-b,
LYBRA_HOME_ROOT/HOME 指向 tmp(禁真治理根/真工位/真门/真 pi)。

验收(卡面 ★验收):
 ① 拉起假 harness 后向 loop 发 SIGHUP → 进程组被清(pgrep 原文); stdout 断开(读端关闭)不致孤儿、不致 loop 未清理即崩
 ② A 项目签发 B 项目实例的码 → create/use/land 事件落 B 的 log, workstation_location(B) 可定位, A 的 log 无该实例事件
 ③ 诊断 `lybra roles enroll-where --instance` 只读: 存量事件在签发方 log → misplaced(实际/应在 log 原文); 重接入后 in_place
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源(禁第二份)
from tools.aipos_cli import enrollment  # noqa: E402
from tools.aipos_cli.harness_launch import TolerantOutput, loop_signals  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract  # noqa: E402

TASK = f95.TASK


def _show(line: str) -> None:
    print(line, flush=True)


# ===========================================================================
# ① SIGHUP / stdout 断开
# ===========================================================================

_LOOP_SCRIPT = r"""
import sys, pytest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {repo!r}); sys.path.insert(0, {tests!r})
import test_aipos_f90_loop_one_stage as f90
import test_aipos_f95_harness_launch as f95
from test_aipos_f78_engine_agnostic import DRIVER, EXEC
from tools.aipos_cli.loop_driver import run_loop_cli
mp = pytest.MonkeyPatch()
tmp = Path({tmp!r})
r = f90.rig.__wrapped__(tmp, mp)
extra = f95.setup_launch(r, mp)
mp.setenv("FAKE_EXEC_MODE", "hang")
f90._card(r.gov, f90.TASK, "pending", harness="pi")
f95._land(r.gov, EXEC, "executor", extra.ws_exec)
print("LOG", extra.log, flush=True)
print("GOV", r.gov, flush=True)
sys.exit(run_loop_cli(SimpleNamespace(task_id=f90.TASK, workspace_root=r.gov, envelope=f95.POLICY_LAUNCH, actor=DRIVER,
                                      connection_json=None, max_steps=5, max_wait={max_wait}, interval=0.05, json=False, no_launch=False)))
"""


def _start_loop(tmp_path: Path, max_wait: float) -> tuple[subprocess.Popen, list[str], Path, Path]:
    """子进程起 loop(假 harness hang 模式: 吐事件后挂起 + 孙进程, 忽略 SIGTERM), 同步阻塞读到第一行进度为止(非轮询)。"""
    script = _LOOP_SCRIPT.format(repo=str(REPO_ROOT), tests=str(REPO_ROOT / "tests"), tmp=str(tmp_path), max_wait=max_wait)
    proc = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(REPO_ROOT))
    seen: list[str] = []
    log = gov = None
    for line in proc.stdout:
        seen.append(line.rstrip("\n"))
        if line.startswith("LOG "):
            log = Path(line.split(" ", 1)[1].strip())
        if line.startswith("GOV "):
            gov = Path(line.split(" ", 1)[1].strip())
        if re.search(r"\[pi \S+ pid=\d+\] 工具 read", line):
            break
    assert log is not None and gov is not None, seen
    return proc, seen, log, gov


def test_item1_loop_signals_declared_sighup_same_semantics_as_sigterm():
    assert set(loop_signals()) == {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
    from tools.aipos_cli.harness_launch import signals_deferred, signals_raise_interrupt

    before = {sig: signal.getsignal(sig) for sig in (*loop_signals(), signal.SIGPIPE)}
    with signals_raise_interrupt():
        assert signal.getsignal(signal.SIGHUP) is signal.getsignal(signal.SIGTERM)
        assert signal.getsignal(signal.SIGPIPE) is signal.SIG_IGN
        with signals_deferred() as pending:
            os.kill(os.getpid(), signal.SIGHUP)  # 清理期: 只记下不打断
            assert pending == [signal.SIGHUP]
    assert {sig: signal.getsignal(sig) for sig in before} == before  # 出上下文恢复原处置


def test_item1_sighup_to_loop_kills_launched_group(tmp_path):
    proc, seen, log, gov = _start_loop(tmp_path, max_wait=120)
    pgid = int((log / f"{TASK}.pgid").read_text())
    assert f95._group_alive(pgid), "拉起的进程组应在跑"
    before = f95._pgrep_fake()
    proc.send_signal(signal.SIGHUP)
    rest, err = proc.communicate(timeout=60)
    text = "\n".join(seen) + "\n" + rest
    _show("---- ① SIGHUP loop 输出原文 ----\n" + text + "\n---- stderr ----\n" + err)
    _show(f"[① SIGHUP] 发信号前 pgrep -af fake_harness:\n{before}")
    assert str(f95.FAKE) in before and "--grandchild" in before  # 进程组含子 + 孙
    assert proc.returncode == -signal.SIGHUP, (proc.returncode, err)  # 按该信号退出(不新增退出码)
    assert f"收到信号 {int(signal.SIGHUP)}, 已终止拉起的 harness 进程组" in err
    assert f"interrupted — loop 收到信号 {int(signal.SIGHUP)}" in text
    assert not f95._group_alive(pgid)
    after = f95._pgrep_fake()
    _show(f"[① SIGHUP] 之后 pgrep -af fake_harness: {after!r}")
    assert after == ""
    events = f95._events(gov, TASK)
    _show("[① SIGHUP] session record 拉起/收尾事件:\n" + "\n".join(events))
    assert any("harness_exit" in e and f"interrupted(signal {int(signal.SIGHUP)})" in e for e in events)


def test_item1_stdout_disconnected_then_sighup_no_orphan_no_crash(tmp_path):
    """ssh 断线形: 先断 stdout(读端关闭 → 写 EPIPE), 再到 SIGHUP → 照常清组、留痕、按 SIGHUP 退出, 无 Traceback。"""
    proc, seen, log, gov = _start_loop(tmp_path, max_wait=120)
    pgid = int((log / f"{TASK}.pgid").read_text())
    before = f95._pgrep_fake()
    proc.stdout.close()  # 输出端断开
    proc.send_signal(signal.SIGHUP)
    _out, err = proc.communicate(timeout=60)
    _show("---- ① stdout 断开 + SIGHUP: stderr 原文 ----\n" + err)
    _show(f"[① 断开+SIGHUP] 之前 pgrep -af fake_harness:\n{before}")
    assert proc.returncode == -signal.SIGHUP, (proc.returncode, err)
    assert "Traceback" not in err and "BrokenPipeError" not in err
    assert not f95._group_alive(pgid)
    after = f95._pgrep_fake()
    _show(f"[① 断开+SIGHUP] 之后 pgrep -af fake_harness: {after!r}")
    assert after == ""
    events = f95._events(gov, TASK)
    _show("[① 断开+SIGHUP] session record 收尾事件:\n" + "\n".join(events))
    assert any("harness_exit" in e and "termination=" in e for e in events)  # 写失败不跳过收尾留痕


def test_item1_stdout_disconnected_without_signal_cleans_up_on_timeout(tmp_path):
    """管道读端关闭而无信号: 输出写失败转静默丢弃, loop 继续按声明等待超时 → 清组 → 声明退出码(exit 3), 不崩不留孤儿。"""
    proc, _seen, log, gov = _start_loop(tmp_path, max_wait=3)
    pgid = int((log / f"{TASK}.pgid").read_text())
    proc.stdout.close()
    _out, err = proc.communicate(timeout=120)
    _show("---- ① stdout 断开(无信号)stderr 原文 ----\n" + err)
    declared = exit_code_for(load_loop_contract(), "wait_timeout")
    assert proc.returncode == declared, (proc.returncode, declared, err)
    assert "Traceback" not in err and "BrokenPipeError" not in err
    assert not f95._group_alive(pgid)
    after = f95._pgrep_fake()
    _show(f"[① 断开无信号] 之后 pgrep -af fake_harness: {after!r}")
    assert after == ""
    assert any("harness_exit" in e and "outcome=timeout" in e for e in f95._events(gov, TASK))


def test_item1_tolerant_output_units():
    class Broken(io.StringIO):
        def write(self, s):  # noqa: D401 — 模拟 ssh 断线后终端写 EIO
            raise OSError(5, "Input/output error")

    sink = TolerantOutput(Broken())
    sink("a")
    sink("b")
    assert sink.broken.startswith("OSError")
    # 真 fd: 管道读端关闭 → BrokenPipeError → fd 改指 /dev/null, 后续写与 flush 不再失败
    r, w = os.pipe()
    os.close(r)
    stream = os.fdopen(w, "w")
    out = TolerantOutput(stream)
    out("first")
    assert out.broken.startswith("BrokenPipeError")
    stream.write("after\n")
    stream.flush()
    stream.close()
    ok = io.StringIO()
    TolerantOutput(ok)("line")
    assert ok.getvalue() == "line\n"


# ===========================================================================
# ② 两项目: A 签发 B 的实例码 → 事件落 B 的 log
# ===========================================================================

def _project(home: Path, name: str) -> Path:
    from tools.aipos_cli.workspace_config import write_project_json

    root = home / name
    (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    write_project_json(root, name, code_repo=str(home / f"{name}-code"))
    return root


@pytest.fixture
def two(tmp_path, monkeypatch):
    home = tmp_path / "projects-home"
    a, b = _project(home, "proj-a"), _project(home, "proj-b")
    (a / ".lybra").mkdir()
    (a / ".lybra" / "connection.json").write_text(json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
                                                              "tokens": []}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    ws = tmp_path / "workstations" / "b-exec"
    ws.mkdir(parents=True)
    return type("Two", (), {"home": home, "a": a, "b": b, "ws": ws, "tmp": tmp_path})


def _log(root: Path) -> Path:
    return enrollment.enrollment_trail_path(root)


def test_item2_a_issues_b_instance_events_land_in_b_log(two):
    inst = "exec.proj-b.hostx"
    issued = enrollment.issue_self_contained_code(two.a, role="executor", instance=inst, ttl_seconds=600,
                                                  governance_root=str(two.b), by="fixture-owner-ref", reason="A 为 B 签发")
    inner = enrollment.decode_self_contained_code(issued["self_contained_code"])["code"]
    enrollment.mark_enrollment_used(two.a, inner, token_entry={"role": "executor", "token": "fixture-not-a-secret",
                                                              "projects": ["proj-b"], "agent_instance": inst})
    enrollment.land_enrollment(two.a, inner, landed_detail=f"host={socket.gethostname()} workstation={two.ws} files=['connection.json', 'role']")
    b_text = _log(two.b).read_text(encoding="utf-8")
    _show(f"---- ② B 的 enrollment_log 原文({_log(two.b)}) ----\n{b_text}")
    assert [ln.split()[2] for ln in b_text.splitlines() if ln.startswith("- ") and inst in ln] == ["create", "use", "land"]
    assert all("project=proj-b" in ln for ln in b_text.splitlines() if ln.startswith("- "))
    a_log = _log(two.a)
    _show(f"[②] A 的 enrollment_log 存在: {a_log.is_file()}")
    assert not a_log.is_file() or inst not in a_log.read_text(encoding="utf-8")
    assert (two.a / ".lybra" / "enrollments.json").is_file()  # 码记录仍住签发门注册表(交换/land 同根)
    loc = enrollment.workstation_location(two.b, inst)
    _show(f"[②] workstation_location(B, {inst}) = {loc}")
    assert loc["found"] and loc["dir"] == str(two.ws) and loc["transport"] == "local"
    assert enrollment.workstation_location(two.a, inst)["found"] is False
    # 吊销也随码落所属项目
    enrollment.revoke_enrollment_code(two.a, issued["code_id"], by="fixture-owner-ref", reason="t")
    assert "  revoke  " in _log(two.b).read_text(encoding="utf-8")


def test_item2_owner_root_single_resolver_precedence_and_refusals(two, tmp_path):
    r = enrollment.enrollment_owner_root
    assert r(two.a, governance_root=str(two.b))["source"] == "码记录 governance_root"
    hit = r(two.a, projects=["proj-b"], instance="exec.proj-a.h")
    assert hit["root"] == two.b.resolve() and hit["source"] == "凭据 projects"  # 凭据先于实例名
    hit = r(two.a, instance="hbj-coder.proj-b.h")
    assert hit["root"] == two.b.resolve() and hit["source"] == "实例名项目段"
    # governance_root 非已建项目 → 不认, 顺次落到实例名
    hit = r(two.a, governance_root=str(tmp_path / "gone"), instance="exec.proj-b.h")
    assert hit["root"] == two.b.resolve()
    miss = r(two.a, instance="exec.no-such.h")
    _show(f"[② 解析不到] {miss}")
    assert miss["root"] is None and "no-such" in miss["reason"]
    assert r(two.a, instance="bad-name")["root"] is None
    assert r(two.a, projects=["proj-a", "proj-b"])["root"] is None
    # 签发门不在 home 内 → 不按项目名跨 home 解析
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    miss = r(outside, instance="exec.proj-b.h")
    assert miss["root"] is None and "不在 home" in miss["reason"]
    # 无所属项目 → 落签发门自身 log, 行内 project=(未归属) 出声
    enrollment.create_enrollment_code(two.a, role="executor", instance=None, by="t")
    a_text = _log(two.a).read_text(encoding="utf-8")
    _show(f"[② 未归属] A log:\n{a_text}")
    assert "project=(未归属)" in a_text


def test_item2_single_writer_and_resolver_no_second_implementation():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "enrollment.py").read_text(encoding="utf-8")
    assert src.count("def enrollment_owner_root(") == 1 and src.count("def _append_enrollment_trail(") == 1
    assert src.count("owner = enrollment_owner_root(") == 2  # 写侧 + 诊断 同一解析口
    assert src.count("_LAND_LINE_RE.match(") == 1  # land 解析一处(workstation_location 与诊断共用 _latest_land)
    import inspect

    for fn in (enrollment.enrollment_owner_root, enrollment.enrollment_whereabouts, enrollment._append_enrollment_trail,
               enrollment._latest_land):  # 本卡新增/改动段 fail-closed(既有 resolve_gate_url_default 不在本卡范围)
        assert "except Exception" not in inspect.getsource(fn) and "pass\n" not in inspect.getsource(fn), fn.__name__


# ===========================================================================
# ③ 只读诊断 lybra roles enroll-where
# ===========================================================================

def _where(two, instance: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "HOME": str(two.tmp), "LYBRA_HOME_ROOT": str(two.home)}
    proc = subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(two.a), "roles", "enroll-where",
                           "--instance", instance, "--json"], capture_output=True, text=True, cwd=str(REPO_ROOT), env=env)
    human = subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(two.a), "roles", "enroll-where",
                            "--instance", instance], capture_output=True, text=True, cwd=str(REPO_ROOT), env=env)
    _show(f"---- ③ lybra roles enroll-where --instance {instance} 原文 ----\n{human.stdout}{human.stderr}")
    assert proc.returncode == 0 and human.returncode == 0, proc.stderr + human.stderr
    return json.loads(proc.stdout)


def _digest(*roots: Path) -> dict[str, str]:
    return {str(_log(r)): hashlib.sha256(_log(r).read_bytes()).hexdigest() if _log(r).is_file() else "-" for r in roots}


def test_item3_enroll_where_readonly_misplaced_then_in_place(two):
    inst = "hbj-coder.proj-b.hostx"
    legacy = _log(two.a)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# Enrollment Codes Log (append-only)\n\n"  # 存量形(F107 前写侧): 签发方 log, 无 project= 字段
                      f"- 2026-08-28T14:50:26Z  create  code_id=enroll_legacy  role=hbj-coder  instance={inst}  by=x  reason=t\n"
                      f"- 2026-08-28T14:50:26Z  land  code_id=enroll_legacy  role=hbj-coder  instance={inst}  by=(agent-enroll)  "
                      f"reason=workstation={two.ws} files=['connection.json', 'role']\n", encoding="utf-8")
    before = _digest(two.a, two.b)
    rep = _where(two, inst)
    assert _digest(two.a, two.b) == before  # 只读
    assert rep["verdict"] == "misplaced" and rep["owner_project"] == "proj-b" and rep["expected_log"] == str(_log(two.b))
    assert [f["log"] for f in rep["found"]] == [str(legacy)] and rep["found"][0]["actions"] == {"create": 1, "land": 1}
    assert rep["workstation_location"]["found"] is False
    # 按结果重接入: 新事件落所属项目
    sc = enrollment.issue_self_contained_code(two.a, role="hbj-coder", instance=inst, ttl_seconds=600, governance_root=str(two.b), by="t")
    inner = enrollment.decode_self_contained_code(sc["self_contained_code"])["code"]
    enrollment.mark_enrollment_used(two.a, inner, token_entry={"role": "hbj-coder", "token": "fixture-not-a-secret", "projects": ["proj-b"]})
    enrollment.land_enrollment(two.a, inner, landed_detail=f"workstation={two.ws} files=['connection.json', 'role']")
    rep = _where(two, inst)
    assert rep["verdict"] == "in_place" and rep["workstation_location"]["found"] is True
    assert {f["log"] for f in rep["found"]} == {str(legacy), str(_log(two.b))}
    assert _where(two, "exec.no-such.h")["verdict"] == "owner_unresolved"
    assert _where(two, "exec.proj-a.h")["verdict"] == "not_landed"
