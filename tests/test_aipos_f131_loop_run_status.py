"""AIPOS-F131 — loop 运行与拉起执行体的进度可查: 运行记录(件①)/ `lybra loop status`(件②)/ 日志落点(件③)。

靶场 = AIPOS-F95 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME + 进程内门 + tmp 工位 + 假 harness tests/fake_harness.py;
禁真门/真治理根/真工位/真 pi)。夹具 lrig 收尾断言无残留假 harness 进程(验收⑥)。

验收(卡面 ★验收):
 ① 授权拉起全链: 运行记录逐步落盘(每轮推导前读盘快照: 已落定步数单调增、当前步随轮次变), 结束原因 = completed;
    loop 照常 exit 0(运行记录目录不入卡落账范围, 否则 loop 自己写记录会让落账判据永不过)
 ② status 四态原文: 运行中(拉起进程存活 + 最近活动)/ stalled(假 harness 静默超声明阈值)/ launch_dead(杀掉假 harness)/
    已结束(含退出原因); 另 loop_dead / unprobeable 由同一判据 judge_run 给出
 ③ 运行记录与日志不含凭据 / 会话正文 / 工具参数 / kickoff(专门断言; 测试源码另受 F121 凭据切片守卫扫描)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import AUDIT_CLAIMER, POLICY_LAUNCH, _land, lrig  # noqa: E402,F401  — 拉起靶场
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.harness_launch import _CREDENTIAL_WORD_RE  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.next_resolver import derive_next_step  # noqa: E402

TASK = f90.TASK
AUDIT = f90.AUDIT


def _show(line: str) -> None:
    print(line, flush=True)


def _decl() -> dict:
    return lrr.run_record_declaration(load_loop_contract())


def _runs(gov: Path, card: str = TASK) -> list[Path]:
    return sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))


def _meta(gov: Path, card: str = TASK) -> dict:
    paths = _runs(gov, card)
    assert len(paths) == 1, paths
    return lrr.read_run(paths[0], _decl())


def _cli(argv: list[str]) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return int(rc or 0), out.getvalue(), err.getvalue()


# 会话正文 / 工具参数 / kickoff / 凭据: 假 harness 吐出的这些字面一律不得进运行记录与日志(stdout 一行式进度照旧, 不在此列)
FORBIDDEN = ("先读卡面", "第二行不应上屏", "AGENTS.md", "git -C", "second line", "ENOENT", "fixture-not-a-secret",
             "fixture-ws-not-a-secret", "已认领任务卡", "this line is not json")


def _assert_no_session_or_credentials(texts: dict[str, str]) -> None:
    hidden = "[含凭据字样, 整行已隐去]"
    for name, text in texts.items():
        for needle in FORBIDDEN:
            assert needle not in text, f"{name} 含 {needle!r}"
        cred = [ln for ln in text.splitlines() if _CREDENTIAL_WORD_RE.search(ln.replace(hidden, ""))]
        assert cred == [], f"{name} 含凭据字样行: {cred}"


# ===========================================================================
# ① 运行记录逐步落盘; 结束原因; 不入落账范围(loop 照常 exit 0)
# ===========================================================================

def test_item1_run_record_lands_step_by_step_and_completed(lrig, monkeypatch):
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    _land(lrig.gov, AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    snapshots: list[tuple[int, str, int]] = []

    def derive(card: str, gov: Path) -> dict:
        if card == TASK and _runs(gov):  # 每次推导前读盘(主推导轮 + 等待就绪谓词): 运行记录原文快照
            meta = _meta(gov)
            cur = meta.get("current_step") or {}
            snapshots.append((len(meta["steps"]), f"{cur.get('index')}:{cur.get('kind')}:{cur.get('action') or ''}",
                              len(meta["launches"])))
        return derive_next_step(card, gov)

    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30,
                   derive=derive)
    text = out.getvalue()
    _show("---- ① lybra loop 输出原文 ----\n" + text)
    assert res.exit_code == 0 and res.outcome == "completed", text
    record_path = _runs(lrig.gov)[0]
    record_text = record_path.read_text(encoding="utf-8")
    meta = lrr.read_run(record_path, _decl())
    log_text = Path(meta["log_path"]).read_text(encoding="utf-8")
    _show(f"---- ① 运行记录原文 {record_path} ----\n{record_text}")
    _show(f"---- ① 日志原文 {meta['log_path']} ----\n{log_text}")
    distinct = list(dict.fromkeys(snapshots))
    _show(f"[①] 逐步快照(已落定步数, 当前步, 拉起次数): {distinct}")
    counts = [s[0] for s in snapshots]
    assert counts == sorted(counts) and counts[0] == 0 and counts[-1] >= 5, snapshots  # 逐步落盘: 单调增
    assert any(":wait:" in s[1] for s in distinct) and any(":execute:claim" in s[1] for s in distinct), distinct
    assert [s[2] for s in distinct] == sorted(s[2] for s in distinct) and distinct[-1][2] == 2, distinct
    # 输出给出记录与日志落点(产品给出, 调用方不再自选 /tmp)
    assert f"run_record={record_path}" in text and f"log={meta['log_path']}" in text
    # 记录形
    assert meta["record_type"] == _decl()["record_type"] and meta["status"] == "ended"
    assert meta["outcome"] == "completed" and meta["exit_code"] == 0 and meta["end_reason"] == "completed"
    assert meta["envelope"] == POLICY_LAUNCH and meta["driver"] == DRIVER and meta["pid"] == os.getpid()
    assert [s["index"] for s in meta["steps"]] == [s.index for s in res.steps]
    assert [(l["card"], l["harness"]) for l in meta["launches"]] == [(TASK, "pi"), (AUDIT, "pi")]
    for launch, ws in zip(meta["launches"], (lrig.ws_exec, lrig.ws_audit)):
        act = launch["activity"]
        assert launch["workstation"] == str(ws) and launch["outcome"] == "artifact_ready" and launch["ended_at"]
        assert act["events"] > 0 and act["last_event_at"] and act["last_activity_kind"] == "agent_end", act
        assert act["counts"]["non_json"] == 1 and act["counts"]["unknown:weird_future_event"] == 1, act
    # 日志: 拉起进程进度行只有类别; loop 自身行在
    assert re.search(rf"\[pi {TASK} pid=\d+\] tool:read$", log_text, re.M), log_text
    assert re.search(rf"\[pi {TASK} pid=\d+\] assistant$", log_text, re.M) and "tool_error:read" in log_text
    assert "tool_end:write" in log_text  # 工具正常结束只记类别
    assert f"[end] outcome=completed exit=0 reason=completed" in log_text and "envelope=" in log_text
    # ③ 运行记录与日志不含会话正文 / 工具参数 / kickoff / 凭据
    _assert_no_session_or_credentials({"运行记录": record_text, "日志": log_text})
    # 运行记录目录不入卡落账范围(loop 已 exit 0 即证落账判据未被运行记录拖住; 此处再核范围候选)
    from tools.aipos_cli.governance_commit import task_scope_candidates

    scope = task_scope_candidates(lrig.gov, TASK)["paths"]
    assert not any("loop_runs" in p for p in scope), scope
    rc, status_out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show(f"---- ① lybra loop status(已结束)原文 exit {rc} ----\n{status_out}")
    assert rc == 0 and "[ended]" in status_out and "outcome=completed exit=0 reason=completed" in status_out


# ===========================================================================
# ② status 四态: running → stalled → launch_dead → ended(同一次 loop 运行, 等待期内取快照)
# ===========================================================================

def test_item2_status_running_stalled_launch_dead_ended(lrig, monkeypatch):
    from tools.aipos_cli.agent_watch_fs import run_fs_watch

    f90._card(lrig.gov, TASK, "pending", harness="pi")
    _land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")  # 吐事件后静默挂起(另起孙进程)
    stall_after = _decl()["stall_after_seconds"]
    seen: dict[str, str] = {}

    def watch(args, expect_ready, stop_when=None, sleeper=None):
        if sleeper is not None and not seen:
            for _ in range(100):  # 有界: 经拉起进程的 pump(select 事件驱动读输出)把已吐事件读进来并落盘
                sleeper(0.1)
                launch = (_meta(lrig.gov)["launches"] or [{}])[-1]
                if (launch.get("activity") or {}).get("last_activity_kind") == "tool_error:read":
                    break
            rc, text, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
            seen["running"] = text
            report = lrr.loop_status(lrig.gov, TASK)
            run = report["runs"][0]
            assert rc == 0 and run["state"] == "running", text
            assert run["loop_alive"] is True and run["launch"]["alive"] is True, run
            assert run["launch"]["last_activity_kind"] == "tool_error:read" and run["launch"]["events"] >= 8, run
            assert run["current_step"]["kind"] == "wait" and run["current_step"]["waiting_for"], run
            # stalled: 判据读声明阈值; 「此刻」= 最近一行输出 + 阈值 + 1s(进程仍在)
            later = lrr._parse_iso(run["launch"]["last_event_at"]) + timedelta(seconds=stall_after + 1)
            stalled = lrr.loop_status(lrig.gov, TASK, now=later)
            assert stalled["runs"][0]["state"] == "stalled" and stalled["runs"][0]["launch"]["alive"] is True, stalled
            seen["stalled"] = lrr.render_status(stalled, _decl()["states"])
            # launch_dead: 杀掉假 harness 整组(含孙进程), loop 仍在等
            os.killpg(run["launch"]["pgid"], signal.SIGKILL)
            for _ in range(100):
                dead = lrr.loop_status(lrig.gov, TASK)
                if dead["runs"][0]["state"] == "launch_dead":
                    break
                sleeper(0.05)
            assert dead["runs"][0]["state"] == "launch_dead" and dead["runs"][0]["launch"]["alive"] is False, dead
            assert dead["runs"][0]["loop_alive"] is True
            seen["launch_dead"] = lrr.render_status(dead, _decl()["states"])
            rc2, json_out, _ = _cli(["loop", "status", "--workspace-root", str(lrig.gov), "--json"])  # 缺 --task-id = 全部未结束
            listed = json.loads(json_out)
            assert rc2 == 0 and [r["task_id"] for r in listed["runs"]] == [TASK] and listed["runs"][0]["state"] == "launch_dead"
        return run_fs_watch(args, expect_ready=expect_ready, stop_when=stop_when, sleeper=sleeper)

    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=POLICY_LAUNCH, out=out, interval=0.05, max_wait=30, max_steps=5,
                   watch=watch)
    _show("---- ② lybra loop 输出原文 ----\n" + out.getvalue())
    for name in ("running", "stalled", "launch_dead"):
        _show(f"---- ② status [{name}] 原文 ----\n{seen[name]}")
    assert res.exit_code == 3 and res.outcome == "wait_timeout", res.message
    rc, ended, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show(f"---- ② status [ended] 原文 exit {rc} ----\n{ended}")
    assert rc == 0 and "[ended]" in ended and "outcome=wait_timeout exit=3 reason=launch_early_exit" in ended, ended
    meta = _meta(lrig.gov)
    assert meta["launches"][0]["outcome"] == "early_exit" and meta["launches"][0]["returncode"] == -signal.SIGKILL
    assert "[running]" in seen["running"] and "存活" in seen["running"] and "tool_error:read" in seen["running"]
    assert "[stalled]" in seen["stalled"] and "[launch_dead]" in seen["launch_dead"] and "已不在" in seen["launch_dead"]
    rc3, json_out, _ = _cli(["loop", "status", "--workspace-root", str(lrig.gov), "--json"])
    assert rc3 == 0 and json.loads(json_out)["runs"] == []  # 已结束 → 不在「未结束」列表
    _assert_no_session_or_credentials({"运行记录": _runs(lrig.gov)[0].read_text(encoding="utf-8"),
                                       "日志": Path(meta["log_path"]).read_text(encoding="utf-8")})


def test_item2_judge_run_loop_dead_and_unprobeable_same_criterion(rig):
    """无信封即拒跑也留记录(结束原因 no_envelope); 同一判据: loop 进程已不在 = loop_dead, 别机记录 = unprobeable。"""
    f90._card(rig.gov, TASK, "pending")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id="pol_absent_1", out=out, interval=0.05, max_wait=1, max_steps=2)
    assert res.exit_code == 5
    meta = _meta(rig.gov)
    assert meta["status"] == "ended" and meta["end_reason"] == "no_envelope" and meta["exit_code"] == 5
    gone = subprocess.Popen(["true"])  # 一个已退出并回收的进程号(不探任何真实 loop/pi)
    gone.wait()
    running = {**meta, "status": "running", "pid": gone.pid, "process_fingerprint": "0" * 16}
    view = lrr.judge_run(running, _decl())
    _show(f"[② loop_dead] {lrr.render_status({'runs': [{**view, 'record_path': '-'}], 'task_id': TASK, 'loop_runs_root': '-'}, _decl()['states'])}")
    assert view["state"] == "loop_dead" and view["loop_alive"] is False
    other = lrr.judge_run({**running, "host": "another-host.invalid"}, _decl())
    assert other["state"] == "unprobeable" and "治理根所在机" in other["note"] and other["loop_alive"] is None
    # 工具在跑(最近有意义活动 = tool:<名>, 如跑全量测试)期间本就无输出: 阈值取 stall_after_tool_seconds, 不误判停滞
    me, fp = os.getpid(), lrr.process_fingerprint(os.getpid())
    t0 = lrr._parse_iso(meta["started_at"])
    launch = {"card": TASK, "harness": "pi", "pid": me, "pgid": me, "process_fingerprint": fp, "started_at": meta["started_at"],
              "ended_at": None, "activity": {"events": 3, "last_event_at": meta["started_at"], "last_activity_kind": "tool:bash"}}
    alive = {**meta, "status": "running", "pid": me, "process_fingerprint": fp, "launches": [launch]}
    decl = _decl()
    in_tool = lrr.judge_run(alive, decl, now=t0 + timedelta(seconds=decl["stall_after_seconds"] + 1))
    assert in_tool["state"] == "running" and in_tool["launch"]["tool_in_flight"] is True, in_tool
    assert lrr.judge_run(alive, decl, now=t0 + timedelta(seconds=decl["stall_after_tool_seconds"] + 1))["state"] == "stalled"
    talk = {**alive, "launches": [{**launch, "activity": {**launch["activity"], "last_activity_kind": "assistant"}}]}
    assert lrr.judge_run(talk, decl, now=t0 + timedelta(seconds=decl["stall_after_seconds"] + 1))["state"] == "stalled"


# ===========================================================================
# 件②/件③ 契约: CLI 解析与退出码、声明、落点可声明、记录建不起来 fail-closed
# ===========================================================================

def test_cli_status_exit_codes_and_loop_still_requires_task_id(rig):
    from tools.aipos_cli.verb_contract import declared_exit_code

    rc, out, err = _cli(["loop", "status", "--task-id", "NOPE-1", "--workspace-root", str(rig.gov)])
    assert rc == declared_exit_code("lybra_loop_status", "no_run") == 4 and "无 loop 运行记录" in out, (out, err)
    rc, out, _ = _cli(["loop", "status", "--workspace-root", str(rig.gov)])
    assert rc == 0 and "本项目无未结束的 loop 运行" in out
    rc, _out, err = _cli(["loop", "--workspace-root", str(rig.gov)])
    assert rc == 2 and "--task-id" in err  # 推进仍须 --task-id(与原 argparse 必填同一退出码)
    bad = rig.gov / "5_tasks" / "records" / "loop_runs" / "BAD-1"
    _write(bad / "looprun_BAD-1_20261008_000000_1.md", "---\nrecord_type: something_else\n---\n")
    rc, _out, err = _cli(["loop", "status", "--task-id", "BAD-1", "--workspace-root", str(rig.gov)])
    assert rc == declared_exit_code("lybra_loop_status", "unreadable") and "record_type" in err  # 读不出 = 点名, 不跳过


def test_declarations_single_source():
    contract = load_loop_contract()
    decl = lrr.run_record_declaration(contract)
    assert set(lrr.RUN_RECORD_KEYS) <= set(decl)
    assert {"running", "stalled", "launch_dead", "loop_dead", "ended", "unprobeable"} == set(decl["states"])
    # 结束原因: loop 全部出口名(exit_codes)都在声明里
    assert set(contract["exit_codes"]) <= set(decl["end_reasons"])
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    assert verbs["lybra_loop_status"]["cli_command"] == "lybra loop status"
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    paths = config["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]
    assert paths["loop_runs_root"]["default"] == "5_tasks/records/loop_runs"


def test_declared_loop_runs_root_used_and_unwritable_root_fails_closed(rig, tmp_path):
    custom = tmp_path / "elsewhere" / "loop-runs"
    project = json.loads((rig.gov / "project.json").read_text(encoding="utf-8"))
    project.setdefault("paths", {})["loop_runs_root"] = str(custom)
    (rig.gov / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    f90._card(rig.gov, TASK, "pending")
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id="pol_absent_1", out=io.StringIO(), interval=0.05, max_wait=1, max_steps=2)
    assert res.exit_code == 5 and len(list((custom / TASK).glob("looprun_*.md"))) == 1 and not _runs(rig.gov)
    # 落点不可写(声明指向一个文件)= 拒跑 exit 4, 不裸跑
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    project["paths"]["loop_runs_root"] = str(blocker)
    (rig.gov / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, interval=0.05, max_wait=1, max_steps=2)
    _show(f"[落点不可写] {out.getvalue()}")
    assert res.exit_code == 4 and res.outcome == "not_derivable" and "运行记录" in res.message
    assert rig.cli == []  # 未执行任何门动作


def test_rerun_same_second_gets_own_record_status_shows_latest(rig):
    """同进程同秒再跑(立即重跑 / 夹具): 各得一份记录, 绝不覆盖; status --task-id 取最近一次并注明共几次。"""
    f90._card(rig.gov, TASK, "pending")
    for _ in range(2):
        res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id="pol_absent_1", out=io.StringIO(), interval=0.05, max_wait=1,
                       max_steps=2)
        assert res.exit_code == 5 and res.run_record and Path(res.run_record).is_file()
    paths = _runs(rig.gov)
    assert len(paths) == 2 and len({p.name for p in paths}) == 2, paths
    report = lrr.loop_status(rig.gov, TASK)
    assert report["runs_on_record"] == 2 and report["runs"][0]["record_path"] == res.run_record
    assert "共 2 次运行记录" in lrr.render_status(report, _decl()["states"])


@pytest.mark.parametrize("raised, reason, code", [("signal", "interrupted", 128 + signal.SIGTERM), ("crash", "crashed", 1)])
def test_interrupt_and_crash_still_end_the_record(rig, raised, reason, code):
    """loop 被信号打断 / 内部异常: 先把结束原因写进运行记录再原样抛出(不吞), status 不会把它误报成仍在跑。"""
    from tools.aipos_cli.loop_driver import LoopInterrupted

    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)

    def watch(args, expect_ready, **_kw):
        if raised == "signal":
            raise LoopInterrupted(signal.SIGTERM)
        raise RuntimeError("fixture crash in watch")

    expected = LoopInterrupted if raised == "signal" else RuntimeError
    with pytest.raises(expected):
        run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=io.StringIO(), interval=0.05, max_wait=1, max_steps=5,
                 watch=watch)
    meta = _meta(rig.gov)
    assert meta["status"] == "ended" and meta["end_reason"] == reason and meta["exit_code"] == code, meta
    assert [s["action"] for s in meta["steps"]][:1] == ["claim"]  # 打断前已落定的步在记录里
    assert lrr.loop_status(rig.gov, TASK)["runs"][0]["state"] == "ended"
