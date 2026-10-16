"""AIPOS-F171B: 同卡 loop 单实例锁 + 拉起 pi 半途停笔同会话续接 + 章程「只重跑已停的 loop」(10-16 chris HBLX-8 实撞)。

件① 单实例: loop 启动唯一口(run_loop, 运行记录建好后)经 loop_run_record.live_run_conflict(与 loop status 同一 judge_run 判活)
     判同卡已有存活 loop → 本次以 already_running 结束(exit 7, 未推进任何步); loop status 仍显示在跑的那一个; 原 loop 结束后可启动。
件② 续接: 拉起 pi 固定会话 id(enums.schema harness pi launch.session); exit 0 而产物未就绪 → 同会话续接(verbs.schema
     lybra_loop.launch.resume.prompt), 至多 project.json launch_resume_max(缺省 2)次; 用尽才判 launch_early_exit; 非 0 退出不续接。
件③ 章程: 顾问母本「收到 Lybra 更新通知」= 先 loop status, 在跑不动, 只重跑已停; 审计 / 执行母本补单发停笔一句。

靶场: F90/F95 拉起靶场(临时治理根 + 假门 + 临时工位); 假 pi = tests/fake_pi.py(PATH 里名为 pi 的薄壳; 按 --session-id 记会话,
续接时把原 kickoff + 续接序号交给假 harness), 用产品真实 enums.schema pi 模板; 不调真实 pi、不起真实 loop、不碰真实门。
"""
from __future__ import annotations

import io
import json
import re
import socket
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f169_pi_model_declaration import _project, _real_pi_template, _write_project, install_fake_pi  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK, AUDIT = f95.TASK, f95.AUDIT


def _show(text: str) -> None:
    print(text, flush=True)


def _launch_rig(lrig, monkeypatch) -> None:
    """产品真实 pi 模板(含 session 声明)+ 假 pi; 执行 / 审计工位落地。"""
    _real_pi_template(monkeypatch)
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)


def _run(lrig, card: str = TASK, **kw):
    out = io.StringIO()
    res = run_loop(card, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05,
                   max_wait=kw.pop("max_wait", 60), max_steps=kw.pop("max_steps", 30), **kw)
    return res, out.getvalue()


def _records(gov: Path, card: str = TASK) -> list[dict]:
    return [nr._read_frontmatter(p) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))]


def _resume_prompt() -> str:
    return load_loop_contract()["launch"]["resume"]["prompt"]


# ===========================================================================
# 件② 停笔续接: 第一次 exit 0 无产物 → 同会话续接 → 写出产物 → 推进到 completed
# ===========================================================================

def test_item2_stop_then_resume_same_session_writes_artifact_and_completes(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "stop_once")  # 审计体首轮只发「let me poll」即 exit 0(HBLX-8R 同形), 续接后写报告
    res, text = _run(lrig)
    _show("---- 件② 停笔→续接→completed: lybra loop 输出原文(节选) ----\n"
          + "\n".join(ln for ln in text.splitlines() if re.search(r"launch:|resume:|ready:|exit \d|助手: UI tests", ln)))
    assert res.exit_code == 0 and res.outcome == "completed", text
    first = json.loads((lrig.log / f"{AUDIT}.argv.json").read_text(encoding="utf-8"))
    second = json.loads((lrig.log / f"{AUDIT}.argv.resume1.json").read_text(encoding="utf-8"))
    _show(f"[件②] {AUDIT} 首次拉起命令行原文: {first}\n[件②] {AUDIT} 续接 1 命令行原文: {second}")
    sid = first[first.index("--session-id") + 1]
    assert second == [("<resume>" if a == "<kickoff>" else a) for a in first], "续接 = 同一 argv 模板与会话 id, 只换指令"
    assert re.fullmatch(rf"lybra-{AUDIT.lower()}-[0-9a-f]{{12}}", sid), sid
    prompt = (lrig.log / f"{AUDIT}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[件②] 续接指令原文: {prompt}")
    root, patterns = nr.auditor_artifact_watch(lrig.gov, AUDIT)  # 等待产物候选首项 = 续接指令里的落点
    artifact = root / patterns[0]
    assert prompt == _resume_prompt().replace("{artifact}", str(artifact)), prompt
    assert not (lrig.log / f"{AUDIT}.argv.resume2.json").exists(), "产物已写出, 不应再续接"
    assert f"resume: pi {AUDIT} exit 0 而产物未就绪(半途停笔) → 同会话 {sid} 续接 1/2" in text
    # 运行记录: 同一会话两条拉起(resume 0 停笔早退 / resume 1 产物就绪), 会话 id 与续接次数原文
    launches = [l for r in _records(lrig.gov) for l in r.get("launches") or [] if l["card"] == AUDIT]
    _show("[件②] 运行记录 launches[](审计卡)原文: " + json.dumps(
        [{k: l.get(k) for k in ("session_id", "resume", "resume_max", "outcome", "returncode")} for l in launches], ensure_ascii=False))
    assert [(l["session_id"], l["resume"], l["resume_max"], l["outcome"], l["returncode"]) for l in launches] == [
        (sid, 0, 2, "early_exit", 0), (sid, 1, 2, "artifact_ready", launches[1]["returncode"])]
    log = Path(_records(lrig.gov)[-1]["log_path"]).read_text(encoding="utf-8")
    resume_line = next(ln for ln in log.splitlines() if " [resume] " in ln)  # 日志行 = <时间戳> <内容>
    _show(f"[件②] 运行日志 [resume] 行原文: {resume_line}")
    assert resume_line.split(" ", 1)[1] == f"[resume] pi {AUDIT} 会话 {sid} 续接 1/2(上一轮 exit 0 而产物未就绪)"
    events = [e for e in f95._events(lrig.gov, AUDIT) if "harness_launch" in e]
    _show(f"[件②] {AUDIT} session Events(拉起)末段原文: " + " | ".join(e.split("; model=")[-1] for e in events))
    assert len(events) == 2 and events[0].endswith(f"; session={sid}; resume=0/2.") and events[1].endswith(f"; session={sid}; resume=1/2."), events
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("拉起 "))
    _show(f"[件②] loop status 拉起行原文: {line.strip()}")
    assert rc == 0 and f"会话 {sid} 续接 1/2" in line


def test_item2_resume_exhausted_then_launch_early_exit(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    monkeypatch.setenv("FAKE_EXEC_MODE", "stop")  # 每轮都只发说明即 exit 0
    res, text = _run(lrig)
    _show("---- 件② 续接用尽: lybra loop 结果原文 ----\n" + res.message)
    assert res.exit_code == 3 and res.outcome == "wait_timeout" and res.reason == "launch_early_exit", text
    sid = json.loads((lrig.log / f"{TASK}.argv.json").read_text(encoding="utf-8"))[-1]
    assert res.message.startswith(f"拉起的 pi 进程已退出(exit 0)而产物未就绪(同会话 {sid} 已续接 2/2 次仍未写出): 等的是 ")
    assert [p.name for p in sorted(lrig.log.glob(f"{TASK}.argv.resume*.json"))] == [f"{TASK}.argv.resume1.json", f"{TASK}.argv.resume2.json"]
    meta = _records(lrig.gov)[-1]
    got = [(l["resume"], l["outcome"], l["returncode"]) for l in meta["launches"]]
    _show(f"[件②] 续接用尽 运行记录 launches[](resume, outcome, returncode): {got}; end_reason={meta['end_reason']}")
    assert got == [(0, "early_exit", 0), (1, "early_exit", 0), (2, "early_exit", 0)] and meta["end_reason"] == "launch_early_exit"
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
    _show("[件②] loop status 原文(节选): " + " | ".join(ln.strip() for ln in out.splitlines() if "拉起" in ln or "已结束" in ln or "下一动作" in ln))
    assert "顾问下一动作: investigate(launch_early_exit)" in out and f"会话 {sid} 续接 2/2" in out


def test_item2_nonzero_exit_not_resumed_and_resume_max_zero_keeps_old_behavior(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    monkeypatch.setenv("FAKE_EXEC_MODE", "early")  # stderr 若干行后 exit 7
    res, _text = _run(lrig)
    _show("---- 件② 非 0 退出不续接: lybra loop 结果原文(首行) ----\n" + res.message.splitlines()[0])
    assert res.reason == "launch_early_exit" and res.message.startswith("拉起的 pi 进程已退出(exit 7)而产物未就绪: 等的是 ")
    assert not list(lrig.log.glob(f"{TASK}.argv.resume*.json")), "非 0 退出不续接"
    assert [(l["resume"], l["returncode"]) for l in _records(lrig.gov)[-1]["launches"]] == [(0, 7)]
    # project.json launch_resume_max=0 = 不续接(F171B 前行为): 停笔一次即早退
    data = _project(lrig.gov)
    data["launch_resume_max"] = 0
    _write_project(lrig.gov, data)
    monkeypatch.setenv("FAKE_EXEC_MODE", "stop")
    res0, _text0 = _run(lrig)
    _show("---- 件② launch_resume_max=0: lybra loop 结果原文(首行) ----\n" + res0.message.splitlines()[0])
    assert res0.reason == "launch_early_exit" and res0.message.startswith("拉起的 pi 进程已退出(exit 0)而产物未就绪(同会话 ")
    assert "已续接 0/0 次仍未写出" in res0.message and not list(lrig.log.glob(f"{TASK}.argv.resume*.json"))


def test_item2_resume_max_declaration_fail_closed(tmp_path):
    from tools.aipos_cli.workspace_config import LaunchResumeDeclarationError, launch_resume_max
    from tools.schema_loader import load_schema

    key = load_schema("config")["configuration_sources"]["project_json"]["schema"]["launch_resume_max"]
    assert key["default"] == 2 and key["required"] is False and "LAUNCH_RESUME_MAX_INVALID" in key["reject_codes"]
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    assert launch_resume_max(tmp_path) == 2  # 缺键 = 缺省 2
    assert launch_resume_max(tmp_path, project={"launch_resume_max": 0}) == 0
    for bad in (-1, "2", True, 1.5):
        with pytest.raises(LaunchResumeDeclarationError, match="LAUNCH_RESUME_MAX_INVALID"):
            launch_resume_max(tmp_path, project={"launch_resume_max": bad})
    pi = next(v for v in load_schema("enums")["enums"]["harness"]["values"] if v["value"] == "pi")
    assert pi["launch"]["argv"] == ["pi", "-p", "{kickoff}", "--mode", "json"], "拉起模板本体不变"
    assert pi["launch"]["session"]["argv"] == ["--session-id", "{session_id}"]
    from tools.aipos_cli.harness_launch import session_launch_args

    sid, args = session_launch_args(pi["launch"], "pi", "HBLX-8R")
    assert args == ["--session-id", sid] and re.fullmatch(r"lybra-hblx-8r-[0-9a-f]{12}", sid)
    assert session_launch_args({"argv": ["x"]}, "x", "C-1") is None  # 无 session 声明 = 不固定会话(行为不变)
    with pytest.raises(ValueError, match="launch.session"):
        session_launch_args({"session": {"argv": ["--session-id"], "id_prefix": "lybra", "id_pattern": ".*"}}, "pi", "C-1")


# ===========================================================================
# 件① 单实例: 同卡已有存活 loop → 第二次启动被拒; loop status 显示在跑的那一个; 原 loop 结束后可启动
# ===========================================================================

def test_item1_second_start_refused_while_first_alive_then_allowed_after(lrig, monkeypatch):
    from tools.aipos_cli.agent_watch_fs import run_fs_watch

    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang")  # 第一个 loop 拉起的执行体挂着(在途)
    seen: dict = {}

    def watch(args, expect_ready, stop_when=None, sleeper=None):
        if "refused" not in seen:  # 第一个 loop 在拉起等待中: 此刻再起同卡 loop
            seen["refused"] = _run(lrig)
            seen["status"] = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov)])
            seen["status_json"] = lrr.loop_status(lrig.gov, TASK)
            return declared_exit_code("lybra_agent_watch", "timeout")  # 第一个 loop 就此结束(launch_timeout)
        return run_fs_watch(args, expect_ready=expect_ready, stop_when=stop_when, sleeper=sleeper)

    out = io.StringIO()
    first = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60,
                     max_steps=30, watch=watch)
    first_meta = next(r for r in _records(lrig.gov) if r["end_reason"] == "launch_timeout")
    refused, refused_text = seen["refused"]
    _show("---- 件① 第二次启动(第一个仍在跑)lybra loop 输出原文 ----\n" + refused_text)
    code = exit_code_for(load_loop_contract(), "already_running")
    assert refused.exit_code == code == 7 and refused.outcome == refused.reason == "already_running", refused_text
    assert (f"本卡 loop 已在运行(run {first_meta['run_id']}, pid {first_meta['pid']} @ {socket.gethostname()}, 开始 "
            f"{first_meta['started_at']}, 当前步 [2] wait @ claim/claimed({TASK})), 本次启动已拒、未推进任何步") in refused.message
    assert f"用 lybra loop status --task-id {TASK} --workspace-root {lrig.gov} 观察, 勿重复启动" in refused.message
    assert refused.suggested_action == f"lybra loop status --task-id {TASK} --workspace-root {lrig.gov}"
    assert "envelope=" not in refused_text, "被拒的启动不得走到信封 / 推进"
    rc, status_text, _err = seen["status"]
    _show(f"---- 件① 被拒后 lybra loop status 原文 exit {rc} ----\n{status_text}")
    run = seen["status_json"]["runs"][0]
    assert rc == 0 and run["run_id"] == first_meta["run_id"] and run["state"] == "running", status_text
    assert seen["status_json"]["next_action"]["action"] == "continue_wait" and "顾问下一动作: continue_wait(running)" in status_text
    assert first.reason == "launch_timeout"
    # 原 loop 结束后: 同命令可启动并推进(执行体这次正常干活)
    monkeypatch.setenv("FAKE_EXEC_MODE", "work")
    again, again_text = _run(lrig)
    _show("---- 件① 原 loop 结束后再启动 lybra loop 输出原文(节选) ----\n"
          + "\n".join(ln for ln in again_text.splitlines() if "lybra loop" in ln or re.search(r"launch:|exit \d", ln)))
    assert again.exit_code == 0 and again.outcome == "completed", again_text
    # 被拒那次的记录不算「最近一次运行」, 也不进连败计数
    reasons = [r["end_reason"] for r in _records(lrig.gov)]
    _show(f"[件①] 本卡运行记录结束原因(按文件名序): {reasons}")
    assert sorted(reasons) == sorted(["launch_timeout", "already_running", "completed"])
    report = lrr.loop_status(lrig.gov, TASK)
    assert report["runs"][0]["end_reason"] == "completed" and report["runs_on_record"] == 2


def test_item1_live_run_conflict_order_and_cross_host_heartbeat(tmp_path):
    """先者继续、后者被拒(双方看同一顺序); 别机记录按心跳判(≤ 阈值 = 存活, 更久 = 已停); 已结束 / 进程已不在 = 不拦。"""
    contract = load_loop_contract()
    gov = tmp_path / "gov"
    gov.mkdir()
    (gov / "project.json").write_text(json.dumps({"project": "probe"}), encoding="utf-8")
    msgs: list[str] = []
    a = lrr.LoopRunRecorder(gov, "C-1", driver="d", contract=contract, warn=msgs.append)
    b = lrr.LoopRunRecorder(gov, "C-1", driver="d", contract=contract, warn=msgs.append)
    assert (a.meta["started_at"], a.run_id) < (b.meta["started_at"], b.run_id)
    hit = lrr.live_run_conflict(gov, "C-1", own=b.meta, contract=contract)
    assert hit is not None and hit["run_id"] == a.run_id and hit["state"] == "running"
    assert lrr.live_run_conflict(gov, "C-1", own=a.meta, contract=contract) is None, "先者不被后者拦"
    # 别机记录: 心跳(updated_at)距今 ≤ stall_after_tool_seconds = 存活
    beat = lrr.run_record_declaration(contract)["stall_after_tool_seconds"]
    other = lrr.live_run_conflict(gov, "C-1", own=b.meta, contract=contract, local_host="another-host")
    _show(f"[件①] 别机记录(心跳新)拒因原文: {other['message']}")
    assert other["state"] == "unprobeable" and "按心跳判存活" in other["message"]
    later = lrr._parse_iso(a.meta["updated_at"]) + timedelta(seconds=beat + 1)
    assert lrr.live_run_conflict(gov, "C-1", own=b.meta, contract=contract, local_host="another-host", now=later) is None
    a.end(outcome="completed", exit_code=0, reason="completed", message="done")
    assert lrr.live_run_conflict(gov, "C-1", own=b.meta, contract=contract) is None, "已结束的运行不拦"
    b.end(outcome="completed", exit_code=0, reason="completed", message="done")


def test_item1_declarations_single_source():
    contract = load_loop_contract()
    si = lrr.single_instance_declaration(contract)
    run_decl = lrr.run_record_declaration(contract)
    assert si["end_reason"] == "already_running" and si["end_reason"] in run_decl["end_reasons"]
    assert contract["exit_codes"]["already_running"]["code"] == 7
    assert lrr.next_action_declaration(run_decl)["by_end_reason"]["already_running"] == "continue_wait"
    rules = lrr.next_action_declaration(run_decl)
    na = lrr.next_action({"task_id": "C-1", "state": "ended", "end_reason": "already_running", "end_message": "x"}, rules,
                         governance_root=Path("/nonexistent-f171b"))
    assert na["action"] == "continue_wait" and na["hint"] == rules["wait_hint"].format(task_id="C-1", governance_root="/nonexistent-f171b")
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert src.count("live_run_conflict(") == 1 and src.count("_should_resume(") == 2  # 唯一判定口(定义 + 一处调用)
    assert "sleep(" not in src and "except Exception" not in src


# ===========================================================================
# 件③ 章程渲染含新条款(顾问 / 审计 / 执行)
# ===========================================================================

def test_item3_charters_render_new_clauses(rig):
    from tools.aipos_cli import charter_render as cr

    masters = {role: (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
               for role in ("advisor", "auditor", "executor")}
    rendered = {role: cr.render_charter(text, cr.charter_render_context(rig.gov, identity={
        "instance": DRIVER if role == "advisor" else (AUDITOR if role == "auditor" else EXEC), "project": "lybra", "role": role,
        "harness_root": str(rig.gov)})) for role, text in masters.items()}
    adv = rendered["advisor"]
    para = adv[adv.index("**收到 Lybra 更新通知**"):adv.index("**禁止**", adv.index("**收到 Lybra 更新通知**"))]
    _show("---- 件③ 渲染后顾问章程「收到 Lybra 更新通知」原文 ----\n" + para)
    assert f"lybra loop status --task-id <卡ID> --workspace-root {rig.gov}" in para
    assert "**在跑的 loop 不动**" in para and "`already_running`" in para and "**只有已停的 loop** 才按其「下一条」重跑" in para
    clause = "**单发运行中不要只发说明不调用工具就结束;要等长任务就用工具等待**"
    for role in ("auditor", "executor"):
        text = rendered[role]
        line = next(ln for ln in text.splitlines() if clause in ln)
        _show(f"---- 件③ 渲染后{role}章程新句原文 ----\n{line}")
        assert "{{" not in line
