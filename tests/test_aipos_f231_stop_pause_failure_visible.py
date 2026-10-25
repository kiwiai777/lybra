"""AIPOS-F231: 停线 / 暂停与执行体故障可见(10-25 chris HBJOTA-61V2 / 10-24 HBLX-24BVR、35VR / HBLX-31 实撞)。

件① Owner 暂停 / 恢复: `lybra queue pause|resume`(唯一实现 board_adapter.pause_task; 记录 = owner_decision_record 追加, 判定唯一
     owner_inbox.card_pause_state); 暂停期间 loop 拒起 / 步间停(结束原因 paused), lybra next 显示已暂停且 --run 拒, loop status 下一动作
     owner_needed(paused) 给恢复命令(不给「重跑 loop」), 收件箱事项可一键「恢复推进」(owner decide = 看板 F226 写路由同一实现)。
件② 协作式停线: `lybra loop stop`(唯一实现 loop_run_record.stop_run / stop_cleanup): 存现场 → 有界清理本卡进程组 + 验证根 run 目录进程 →
     核无残留释放独占资源(另一张排队卡随即取得); 超时 / 模拟器未删 / loop 已不在 三条路径如实报告。
件③ 已结束运行的独占资源显示「已随运行结束释放」(存量 held 记录同)。
件④ pi 早退读会话记录真实原因(401 / 429 / length), 401 / 429 有界退避重试, 用尽 = launch_provider_error 点名 provider。
件⑤ 拉起前开局体积估算: 超窗口安全上限先精简(大节给路径), 仍超拒拉起。
件⑥ --max-steps 主动停不计连败。

靶场: F90 进程内门 + 临时治理根(rig), F95 拉起靶场(lrig: 假 harness / 假 pi, 收尾断言无残留假 harness 进程); 第二个 loop 与
`lybra loop stop` 是真实 CLI 子进程(只连临时根); 伪模拟器 = 假 harness 起的脱离进程组的进程(cwd = 本卡验证根 run 目录)。
不连真实门、不起真实 pi、不碰真实模拟器与真实治理根; 本文件起的每个进程都由本文件等完或杀掉。
"""
from __future__ import annotations

import contextlib
import copy
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  - loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  - 拉起靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _card as f78_card  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  - pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  - pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import owner_inbox as oi  # noqa: E402
from tools.aipos_cli.agent_watch_fs import run_fs_watch  # noqa: E402
from tools.aipos_cli.loop_driver import _watch_args, exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK, AUDIT = f90.TASK, f90.AUDIT
CARD_B = "PROBE-F231-B"
SIM = ["ios-simulator"]
WORDS = "先停下, 把模拟器让给理想"


def _show(text: str) -> None:
    print(text, flush=True)


def _quiet(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("[ENVELOPE"))


def _loop(r, task_id: str, watch=None, *, policy: str = f90.POLICY, **kw):
    out = io.StringIO()
    res = run_loop(task_id, r.gov, actor=DRIVER, policy_id=policy, out=out, watch=watch, interval=kw.pop("interval", 0.05),
                   max_wait=kw.pop("max_wait", 5), max_steps=kw.pop("max_steps", 40), **kw)
    return res, out.getvalue()


def _records(gov: Path, card: str) -> list[dict]:
    return [nr._read_frontmatter(p) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))]


def _status(gov: Path, card: str) -> tuple[str, dict]:
    _rc, out, err = _cli(["loop", "status", "--task-id", card, "--workspace-root", str(gov)])
    _rcj, outj, _ = _cli(["loop", "status", "--task-id", card, "--workspace-root", str(gov), "--json"])
    return _quiet(out + err), json.loads(outj)


def _f90_watch(r):
    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            f90._auditor_work(r)
        else:
            f90._executor_work(r)
        return 0 if expect_ready([]) else 3

    return watch


def _await(predicate, root: Path, pattern: str, timeout: float = 120) -> bool:
    """经产品唯一等待原语(agent watch)有界等 predicate 成立(禁 sleep 轮询)。"""
    root.mkdir(parents=True, exist_ok=True)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        rc = run_fs_watch(_watch_args(root, [pattern], max_wait=timeout, interval=0.1), expect_ready=lambda _m: predicate())
    return rc == 0 or predicate()


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LYBRA_")}
    env.update(PYTHONPATH=str(REPO_ROOT), HOME=str(home))
    return env


# ===========================================================================
# 件① Owner 暂停 / 恢复
# ===========================================================================

def _pause(gov: Path, action: str, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["--workspace-root", str(gov), "queue", action, "--task-id", TASK, "--relayed-by", DRIVER,
                         "--owner-words", WORDS if action == "pause" else "可以继续了", *extra])
    return rc, _quiet(out + err)


def test_item1_pause_refuses_loop_next_status_shows_paused_inbox_resume_then_continues(rig):
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    rc, out = _pause(rig.gov, "pause")
    _show(f"---- 件① queue pause(预演)exit {rc} ----\n{out}")
    assert rc == 0 and out.startswith(f"预演(零写入): 暂停 卡 {TASK}[pending]") and oi.card_pause_state(rig.gov, TASK) is None
    rc, out = _pause(rig.gov, "pause", "--confirm")
    _show(f"---- 件① queue pause --confirm exit {rc} ----\n{out}")
    pause = oi.card_pause_state(rig.gov, TASK)
    assert rc == 0 and pause is not None and f"已暂停: 卡 {TASK}" in out and f"Owner 原话「{WORDS}」" in out
    assert "落账" in out or "landed" in out.lower(), out
    rc2, out2 = _pause(rig.gov, "pause", "--confirm")
    assert rc2 == 1 and "PAUSE_ALREADY" in out2, out2
    # loop 拒起: 拒因点名暂停记录与恢复命令, 未推进任何步(不认领、不派审、不请求资源)
    res, text = _loop(rig, TASK, _f90_watch(rig))
    _show(f"---- 件① 暂停期间 lybra loop exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.reason == "paused" and res.exit_code == exit_code_for(load_loop_contract(), "not_derivable"), text
    assert pause["record"] in res.message and "queue resume --task-id " + TASK in res.message
    assert not res.steps and nr._read_task_records(rig.gov, TASK).get("latest_claim") is None
    # lybra next: 显示已暂停, --run 拒
    rc, out, err = _cli(["next", "--task-id", TASK, "--workspace-root", str(rig.gov)])
    _show(f"---- 件① lybra next --task-id(暂停中)exit {rc} ----\n{_quiet(out)}")
    assert rc == 1 and "已暂停" in out and "queue resume" in out
    rc, out, err = _cli(["next", "--task-id", TASK, "--workspace-root", str(rig.gov), "--run"])
    _show(f"---- 件① lybra next --run(暂停中)exit {rc} ----\n{_quiet(err)}")
    assert rc == 1 and "已暂停, lybra next --run 拒执行" in err
    # loop status: 已暂停(owner_needed paused), 给恢复命令, 不给「重跑 loop」
    status_text, status = _status(rig.gov, TASK)
    _show("---- 件① lybra loop status(暂停中)----\n" + status_text)
    na = status["next_action"]
    assert na["action"] == "owner_needed" and na["reason"] == "paused" and na["owner_tier"] == 1, na
    assert "queue resume" in na["hint"] and "lybra loop --task-id" not in na["hint"]
    # 收件箱: 已暂停事项, 选项 = 恢复推进 / 撤卡; Owner 选「恢复推进」(owner decide = 看板一键裁决同一实现)
    inbox = oi.collect_inbox(rig.gov.parent, include_paused=True)
    item = next(i for i in inbox["items"] if i.get("task_id") == TASK)
    _show(f"---- 件① 收件箱事项(暂停中)----\n  {item['item_id']} {item['category']} 事由 {item['facts']['reason']}: {item['ask']}\n"
          f"  选项: {[o['key'] + '=' + o['label'] for o in item['options']]}")
    assert item["category"] == "loop_owner_needed" and item["facts"]["reason"] == "paused"
    assert [o["key"] for o in item["options"]] == ["resume", "withdraw"]
    report = oi.decide(item["item_id"], "resume", home_root=rig.gov.parent, relayed_by=DRIVER, owner_words="恢复吧", confirm=True)
    assert report["ok"], report
    assert oi.card_pause_state(rig.gov, TASK) is None, "收件箱「恢复推进」= 解除暂停(同一记录类型)"
    status_text, status = _status(rig.gov, TASK)
    _show("---- 件① lybra loop status(收件箱恢复后)----\n" + status_text)
    assert status["next_action"]["action"] == "advisor_fix" and status["next_action"]["reason"] == "owner_ruled"
    assert "lybra loop --task-id " + TASK in status["next_action"]["hint"]
    res, text = _loop(rig, TASK, _f90_watch(rig))
    _show(f"---- 件① 恢复后 lybra loop exit {res.exit_code} ----\n" + "\n".join(_quiet(text).splitlines()[-4:]))
    assert res.exit_code == 0 and res.outcome == "completed", text
    chain = [r["allowed_next_action"] for r in oi.pause_rulings(rig.gov, TASK)]
    _show(f"[件①] 暂停 / 恢复裁决链(追加不覆盖): {chain}")
    assert chain == ["pause", "resume"]


def test_item1_pause_while_running_stops_between_steps_no_audit_dispatch_and_cli_resume(rig):
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    seen: dict = {}

    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        assert f"/{AUDIT}/" not in patterns, "暂停后不得进入审计等待"
        seen["pause"] = _pause(rig.gov, "pause", "--confirm")  # 执行体干活期间 Owner 暂停
        f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    res, text = _loop(rig, TASK, watch)
    _show(f"---- 件① 运行中暂停: lybra loop exit {res.exit_code} ----\n{_quiet(text)}")
    assert seen["pause"][0] == 0 and res.reason == "paused" and "步间停下" in res.message, text
    records = nr._read_task_records(rig.gov, TASK)
    # 执行体的交回报告已落盘, 但暂停后 loop 在下一步推导前即停: 交回入门 / 审计派生都不发生
    assert (rig.gov / "task_cards" / TASK / "RETURN.md").exists() or nr.executor_artifact_watch(rig.gov, TASK)
    assert records.get("latest_return") is None and records.get("latest_audit_dispatch") is None, "暂停后不推进交回 / 派审"
    rc, out = _pause(rig.gov, "resume", "--confirm")
    _show(f"---- 件① queue resume --confirm exit {rc} ----\n{out}")
    assert rc == 0 and oi.card_pause_state(rig.gov, TASK) is None
    rc2, out2 = _pause(rig.gov, "resume", "--confirm")
    assert rc2 == 1 and "PAUSE_NOT_PAUSED" in out2
    res, text = _loop(rig, TASK, _f90_watch(rig))
    assert res.exit_code == 0 and res.outcome == "completed", text


def test_item1_pause_refusals_and_declaration():
    from tools.aipos_cli.queue_mutation import pause_declaration

    decl = pause_declaration()
    contract = load_loop_contract()
    assert decl["end_reason"] in contract["run_record"]["end_reasons"] and decl["pause_choice"] == "pause"
    inbox = oi.inbox_declaration()
    assert {"pause", "resume"} <= set(inbox["options"]) and inbox["tiers"]["loop_reasons"]["paused"] == {"tier": 1, "owner_reason": "paused"}


# ===========================================================================
# 件② 协作式停线
# ===========================================================================

def _validation_root(gov: Path, card: str, run_dir_base: Path, *, simulator: bool = False, finished: bool = False) -> Path:
    """project.json validation_roots: 本机一条声明 + 一个 run(形 = config.schema 投影的键表); 返回 run 目录。"""
    from tools.aipos_cli.validation_root import local_host_label

    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    run = {"run_id": "run-1", "candidate": "a" * 40, "started_at": "2026-10-25T00:00:00Z", "started_by": DRIVER,
           "finished_at": "2026-10-25T01:00:00Z" if finished else None, "finished_by": DRIVER if finished else None}
    if simulator:
        run["simulator"] = {"udid": "12345678-1234-1234-1234-123456789ABC", "name": f"lybra-{card}-run-1", "bound_at": "2026-10-25T00:01:00Z",
                            "bound_by": DRIVER, "verified_at": None, "verified_by": None, "evidence_sha256": None}
    entry = {"id": f"vr-{card.lower()}-1", "host": local_host_label(), "host_fingerprint": None, "base": str(run_dir_base), "task_id": card,
             "instance": EXEC, "role_class": "executor", "owner_ref": "夹具 Owner 授权", "declared_by": DRIVER,
             "declared_at": "2026-10-25T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z", "revoked_at": None, "revoked_by": None,
             "revoke_reason": None, "runs": [run]}
    roots = data.setdefault("validation_roots", {"declarations": []})
    roots["declarations"] = [e for e in roots["declarations"] if e["task_id"] != card] + [entry]
    (gov / "project.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return run_dir_base / card / "run-1"


def test_item2_stop_saves_cleans_group_and_sim_releases_resource_next_card_takes_it(lrig, monkeypatch, tmp_path):
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    for card in (TASK, CARD_B):
        f90._card(lrig.gov, card, "pending", harness="pi", resources=SIM)
    run_dir = _validation_root(lrig.gov, TASK, tmp_path / "vr")
    init_governance_repo(lrig.gov)
    pre, _t = _loop(lrig, CARD_B, lambda args, expect_ready, deploy_guard=None: 3 if "looprun_" not in " ".join(args.expect)
                    else run_fs_watch(args, expect_ready=expect_ready), max_wait=1)
    assert pre.reason == "wait_timeout"  # B 先认领(其执行体等待随即超时), 运行结束释放资源
    monkeypatch.setenv("FAKE_EXEC_MODE", "hang_sim")
    monkeypatch.setenv("FAKE_SIM_DIR", str(run_dir))
    ctx: dict = {}
    b_log, stop_log = tmp_path / "loop-b.out", tmp_path / "stop.out"

    def watch(args, expect_ready, stop_when=None, sleeper=None, deploy_guard=None):
        import time

        base = sleeper or time.sleep  # 资源等待不传 sleeper = 唯一哨兵缺省

        def tick(seconds: float) -> None:
            base(seconds)
            sim = lrig.log / f"{TASK}.sim_pid"
            if "b" not in ctx and sim.exists():  # A 已持有资源且伪模拟器已起: 起 B(真实 loop 子进程, 排队等资源)
                with b_log.open("w", encoding="utf-8") as handle:
                    ctx["b"] = subprocess.Popen(
                        [sys.executable, "-m", "tools.aipos_cli", "loop", "--task-id", CARD_B, "--workspace-root", str(lrig.gov), "--actor",
                         DRIVER, "--envelope", f90.POLICY, "--no-launch", "--max-wait", "20", "--interval", "0.2", "--max-steps", "3"],
                        cwd=str(REPO_ROOT), env=_env(lrig.home), stdout=handle, stderr=subprocess.STDOUT)
            b_recs = _records(lrig.gov, CARD_B)
            if "stop" not in ctx and "b" in ctx and len(b_recs) >= 2 and (b_recs[-1].get("resource_lock") or {}).get("state") == "waiting":
                ctx["status_a"] = _status(lrig.gov, TASK)  # 停线前: A 持有
                with stop_log.open("w", encoding="utf-8") as handle:  # Owner 要停: 顾问转达(真实 lybra loop stop 子进程)
                    ctx["stop"] = subprocess.Popen(
                        [sys.executable, "-m", "tools.aipos_cli", "loop", "stop", "--task-id", TASK, "--workspace-root", str(lrig.gov),
                         "--owner-words", WORDS, "--relayed-by", DRIVER, "--reason", "让出 ios-simulator", "--timeout", "60"],
                        cwd=str(REPO_ROOT), env=_env(lrig.home), stdout=handle, stderr=subprocess.STDOUT)

        extra = {"deploy_guard": deploy_guard} if deploy_guard is not None else {}
        return run_fs_watch(args, expect_ready=expect_ready, stop_when=stop_when, sleeper=tick, **extra)

    sim_pid = None
    try:
        res, text = _loop(lrig, TASK, watch, policy=f95.POLICY_LAUNCH, max_wait=120, interval=0.1)
        _show(f"---- 件② loop A(持有资源, 被停线)exit {res.exit_code} ----\n" + "\n".join(
            ln for ln in _quiet(text).splitlines() if re.search(r"stop|resource|exit \d|已清理|已保存|pid |未清理|manual|charter|context", ln)))
        sim_file = lrig.log / f"{TASK}.sim_pid"
        assert sim_file.exists(), text
        sim_pid = int(sim_file.read_text())
        assert ctx.get("stop") is not None, "B 未排队 / 停线未发起"
        rc = ctx["stop"].wait(timeout=120)
        out = stop_log.read_text(encoding="utf-8")
        _show(f"---- 件② `lybra loop stop`(Owner 原话, 真实 CLI 子进程)exit {rc} ----\n{_quiet(out)}")
        assert res.reason == "stopped_by_owner" and res.exit_code == exit_code_for(load_loop_contract(), "stop_requested"), text
        assert rc == 0 and "已停线: 现场已保存, 本卡进程 / 隧道已清理, 核无残留, 独占资源已释放(stopped_by_owner)" in out
        assert "已保存现场:" in out and "returncode unknown" in out and "独占资源 ios-simulator: 已释放" in out
        assert f"pid {sim_pid} @ 本机: 验证根 vr-{TASK.lower()}-1 run run-1 目录下进程(cwd)" in out
        assert re.search(rf"进程组 \d+\(收尾终止整组\)\((sigkill|sigterm)\)", out), "拉起进程组(含组内孙进程)已终止"
        assert not lrr.process_alive(sim_pid, None), "伪模拟器(组外进程)已按 run 目录归属清理"
        meta = _records(lrig.gov, TASK)[-1]
        stop = meta["stop"]
        assert stop["outcome"] == "stopped" and stop["owner_words"] == WORDS and not stop["residual"]
        assert meta["resource_lock"]["state"] == "released" and stop["saved"]["launches"][0]["returncode"] == "unknown"
        status_before = ctx["status_a"][0]
        _show("---- 件② 停线前 loop status A(节选)----\n" + "\n".join(ln for ln in status_before.splitlines() if "独占资源" in ln or "拉起" in ln))
        assert "独占资源 ios-simulator: held" in status_before
        # 另一张等资源的卡随即取得(B 是真实 loop 子进程, 一直在排队)
        assert _await(lambda: any((m.get("resource_lock") or {}).get("held_at") for m in _records(lrig.gov, CARD_B)[1:]),
                      lrig.gov / "5_tasks" / "records" / "loop_runs" / CARD_B, "looprun_*.md"), "B 未取得资源"
        ctx["b"].wait(timeout=120)
        out_b = b_log.read_text(encoding="utf-8")
        _show("---- 件② loop B(排队卡)输出节选 ----\n" + "\n".join(ln for ln in _quiet(out_b).splitlines() if "resource" in ln))
        assert "resource_wait: 独占资源 ios-simulator 被占用: 卡 " + TASK in out_b and "s 后获得(占用者已结束)" in out_b
        status_text, status = _status(lrig.gov, TASK)
        _show("---- 件② 停线后 loop status A ----\n" + status_text)
        assert "独占资源 ios-simulator: 已随运行结束释放" in status_text and "停线请求" in status_text
        assert status["next_action"]["action"] == "advisor_fix" and status["next_action"]["reason"] == "stopped_by_owner"
    finally:
        for key in ("b", "stop"):
            proc = ctx.get(key)
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=60)
        if sim_pid and lrr.process_alive(sim_pid, None):
            os.kill(sim_pid, signal.SIGKILL)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=60)
    return proc.pid


def test_item2_stop_timeout_pending_and_dead_loop_sim_residual_then_recheck_releases(rig, tmp_path):
    contract = load_loop_contract()
    msgs: list[str] = []
    # 超时路径: 运行记录是活进程(本测试进程)而它不响应停线请求 → 到时如实报告, 资源仍占用
    live = lrr.LoopRunRecorder(rig.gov, "PROBE-F231-T", driver=DRIVER, contract=contract, warn=msgs.append)
    live.resource_request(SIM, "PROBE-F231-T")
    live.resource_held()
    try:
        report = lrr.stop_run(rig.gov, "PROBE-F231-T", requested_by=DRIVER, timeout=1)
        _show("---- 件② 超时路径 `lybra loop stop --timeout 1` 输出 ----\n" + "\n".join(report["lines"]))
        assert report["outcome"] == "pending" and "超时: 1s 内 loop 未确认停线" in report["lines"][1]
        assert "独占资源 ios-simulator: 仍占用(停线未清干净, 不释放)" in "\n".join(report["lines"])
        other = lrr.LoopRunRecorder(rig.gov, "PROBE-F231-O", driver=DRIVER, contract=contract, warn=msgs.append)
        other.resource_request(SIM, "PROBE-F231-O")
        assert lrr.resource_conflict(rig.gov, other.meta, contract=contract)["run_id"] == live.run_id, "未释放 = 仍算占用"
        other.end(outcome="completed", exit_code=0, reason="completed", message="done")
    finally:
        live.end(outcome="completed", exit_code=0, reason="completed", message="done")
    # loop 已不在(SIGKILL 形: 记录未写结束)+ 本卡专用模拟器仍绑定 + run 目录下孤儿进程
    card = "PROBE-F231-D"
    f90._card(rig.gov, card, "claimed")
    run_dir = _validation_root(rig.gov, card, tmp_path / "vr", simulator=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], cwd=str(run_dir), start_new_session=True)
    dead = lrr.LoopRunRecorder(rig.gov, card, driver=DRIVER, contract=contract, warn=msgs.append)
    dead.resource_request(SIM, card)
    dead.resource_held()
    dead.meta.update(pid=_dead_pid(), process_fingerprint="starttime:1")
    dead.flush(force=True)
    dead.close_log()
    try:
        dry = lrr.stop_run(rig.gov, card, requested_by=DRIVER, dry_run=True)
        _show("---- 件② `lybra loop stop --dry-run`(loop 已不在)----\n" + "\n".join(dry["lines"]))
        assert dry["outcome"] == "dry_run" and f"将清理(本机): pid {orphan.pid}" in "\n".join(dry["lines"]) and orphan.poll() is None
        rc, out, err = _cli(["loop", "stop", "--task-id", card, "--workspace-root", str(rig.gov), "--timeout", "10"])
        _show(f"---- 件② `lybra loop stop`(loop 已不在 + 模拟器未删)exit {rc} ----\n{_quiet(out + err)}")
        assert rc == 3 and "loop 进程已不在(loop_dead)" in out and "停线未完成" in out
        assert "本卡专用模拟器 lybra-PROBE-F231-D-run-1(12345678-1234-1234-1234-123456789ABC)" in out and "sim-delete --run-dir" in out
        assert orphan.wait(timeout=30) is not None, "run 目录下孤儿进程已清理"
        meta = _records(rig.gov, card)[-1]
        assert meta["end_reason"] == "stop_incomplete" and meta["resource_lock"]["state"] == "residual"
        probe = lrr.LoopRunRecorder(rig.gov, "PROBE-F231-P", driver=DRIVER, contract=contract, warn=msgs.append)
        probe.resource_request(SIM, "PROBE-F231-P")
        held = lrr.resource_conflict(rig.gov, probe.meta, contract=contract)
        _show(f"[件②] 残留期间另一卡请求同一资源: {held['message']}")
        assert held is not None and held["task_id"] == card, "停线未清干净 = 资源仍算占用"
        status_text, _st = _status(rig.gov, card)
        _show("---- 件② loop status(停线未清干净)----\n" + status_text)
        assert "停线未清干净, 仍占用" in status_text and "顾问下一动作: advisor_fix(stop_incomplete)" in status_text
        _validation_root(rig.gov, card, tmp_path / "vr", simulator=True, finished=True)  # 顾问按处置删模拟器并 run-finish 收口
        rc, out, err = _cli(["loop", "stop", "--task-id", card, "--workspace-root", str(rig.gov)])
        _show(f"---- 件② 处置后重跑 `lybra loop stop` 复核 exit {rc} ----\n{_quiet(out + err)}")
        assert rc == 0 and "复核: 上次停线的未清理项已无残留, 独占资源已释放" in out
        assert lrr.resource_conflict(rig.gov, probe.meta, contract=contract) is None
        probe.end(outcome="completed", exit_code=0, reason="completed", message="done")
        rc, out, err = _cli(["loop", "stop", "--task-id", card, "--workspace-root", str(rig.gov)])
        assert rc == 1 and "无在跑的 loop" in out
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait(timeout=60)


# ===========================================================================
# 件③ 已结束运行的资源显示
# ===========================================================================

def test_item3_ended_run_resource_shows_released_including_legacy_held_record(rig):
    contract = load_loop_contract()
    rec = lrr.LoopRunRecorder(rig.gov, "PROBE-F231-L", driver=DRIVER, contract=contract, warn=lambda _t: None)
    rec.resource_request(["ios-simulator", "android-emulator"], "PROBE-F231-L")
    rec.resource_held()
    rec.end(outcome="deploy_changed", exit_code=6, reason="deploy_changed", message="部署已更新")
    meta = _records(rig.gov, "PROBE-F231-L")[-1]
    assert meta["resource_lock"]["state"] == "released" and meta["resource_lock"]["state_before_end"] == "held"
    # 存量记录(HBJOTA-61V2 形: 已结束而 state 仍为 held)
    path = Path(meta["_path"] if "_path" in meta else rec.path)
    legacy = {k: v for k, v in meta.items() if not k.startswith("_")}
    legacy["resource_lock"] = {**{k: v for k, v in meta["resource_lock"].items() if k not in ("released_at", "state_before_end")}, "state": "held"}
    lrr._write_record(path, legacy)
    status_text, status = _status(rig.gov, "PROBE-F231-L")
    line = next(ln for ln in status_text.splitlines() if "独占资源" in ln)
    _show(f"---- 件③ loop status 已结束运行(存量 held 记录)资源行原文 ----\n{line}")
    assert "独占资源 ios-simulator, android-emulator: 已随运行结束释放(" in line and "held" in line
    assert status["runs"][0]["resource_lock"]["state"] == "released"


# ===========================================================================
# 件④ pi 早退真实原因 + 401 / 429 退避重试
# ===========================================================================

def _pi_rig(lrig, monkeypatch) -> None:
    _real_pi_template(monkeypatch)
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    current = loop_driver.load_loop_contract

    def short(repo_root=None):
        data = copy.deepcopy(current(repo_root))
        data["launch"]["early_exit"].update(base_seconds=0.2, max_seconds=0.4)
        return data

    monkeypatch.setattr(loop_driver, "load_loop_contract", short)


@pytest.mark.parametrize("mode", ["provider_401", "provider_429_once", "length"])
def test_item4_pi_early_exit_real_reason_backoff_retry(lrig, monkeypatch, mode):
    _pi_rig(lrig, monkeypatch)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", mode)
    res, text = _loop(lrig, TASK, policy=f95.POLICY_LAUNCH, max_wait=60, max_steps=30)
    _show(f"---- 件④ {mode}: lybra loop exit {res.exit_code} ----\n" + "\n".join(
        ln for ln in _quiet(text).splitlines() if re.search(r"early_exit|retry|exit \d|done", ln)))
    meta = [m for m in _records(lrig.gov, TASK)][-1]
    launches = [l for l in meta["launches"] if l["card"] == TASK]
    status_text, status = _status(lrig.gov, TASK)
    _show(f"---- 件④ {mode}: loop status 节选 ----\n" + "\n".join(
        ln for ln in status_text.splitlines() if "早退原因" in ln or "退避重试" in ln or "下一动作" in ln or "已结束" in ln))
    if mode == "provider_401":
        assert res.reason == "launch_provider_error" and res.exit_code == 3, text
        assert "凭据问题(HTTP 401" in res.message and "provider kiwiai" in res.message and "已退避重试 2/2 次仍失败" in res.message
        assert len(launches) == 3 and [l["diagnosis"]["status"] for l in launches] == ["401"] * 3
        assert [r["attempt"] for r in meta["provider_retries"]] == [1, 2]
        assert "模型服务 401 / 429 退避重试: 2/2 次(最近 HTTP 401 credential" in status_text
        assert status["next_action"]["action"] == "advisor_fix" and status["next_action"]["reason"] == "launch_provider_error"
    elif mode == "provider_429_once":
        assert res.exit_code == 0 and res.outcome == "completed", text
        assert launches[0]["diagnosis"]["kind"] == "quota" and launches[1]["outcome"] == "artifact_ready"
        assert any(r["status"] == "429" for m in _records(lrig.gov, TASK) for r in m.get("provider_retries") or [])
    else:
        assert res.reason == "launch_context_overflow", text
        assert "开局上下文超出模型窗口(stopReason=length)" in res.message and "本轮输入 98293 词元" in res.message
        assert len(launches) == 1 and not list(lrig.log.glob(f"{TASK}.argv.resume*.json")), "超窗口不续接"
        assert "pi 早退原因: 开局上下文超出模型窗口" in status_text


# ===========================================================================
# 件⑤ 开局体积守卫
# ===========================================================================

def test_item5_oversize_card_refuses_launch_and_kickoff_sections_compacted(lrig, monkeypatch, tmp_path):
    _pi_rig(lrig, monkeypatch)
    f78_card(lrig.gov, TASK, "pending", extra={"harness": "pi"}, body=f"# {TASK}\n\n## Goal\n" + "超大必读件内容。" * 20000 + "\n")
    init_governance_repo(lrig.gov)
    res, text = _loop(lrig, TASK, policy=f95.POLICY_LAUNCH, max_wait=30)
    _show(f"---- 件⑤ 超大开局: lybra loop exit {res.exit_code} ----\n{res.message}")
    assert res.reason == "launch_context_too_large" and not list(lrig.log.glob(f"{TASK}.argv*.json")), "未拉起"
    assert "开局上下文超出模型窗口安全上限, 不拉起 pi" in res.message and "上限 39321" in res.message
    check = _records(lrig.gov, TASK)[-1]["context_checks"][-1]
    assert check["outcome"] == "refused" and check["parts"]["cards"] > check["limit"]
    # 精简: 开工稿大节存运行记录旁文件, 开工稿只给路径(单元: 同一 _context_before_launch)
    contract = copy.deepcopy(load_loop_contract())
    contract["launch"]["context_budget"].update(default_window_tokens=6000, harness_overhead_tokens={"pi": 0})
    rec = lrr.LoopRunRecorder(lrig.gov, "PROBE-F231-K", driver=DRIVER, contract=contract, warn=lambda _t: None)
    big = "已认领任务卡 X。\n工作树: /tmp/x\n\n## 大节\n" + "长说明。" * 2000
    plan = loop_driver.LaunchPlan(card="PROBE-F231-K", harness="pi", cwd=str(tmp_path), kickoff=big, argv=["pi", "-p", big], location={"transport": "local"})
    step = loop_driver.LoopStep(1, "wait", None, None, "PROBE-F231-K")
    result = loop_driver.LoopResult("PROBE-F231-K", "completed", 0, "")
    lines: list[str] = []
    refused = loop_driver._context_before_launch(1, step, plan, lrig.gov, result, contract=contract, say=lines.append, recorder=rec)
    _show("---- 件⑤ 精简开工稿(单元)----\n" + "\n".join(lines) + f"\n精简后开工稿:\n{plan.kickoff}")
    saved = rec.path.with_name(f"{rec.run_id}.kickoff-PROBE-F231-K.log")
    assert not refused and saved.read_text(encoding="utf-8") == big and str(saved) in plan.kickoff and plan.argv[2] == plan.kickoff
    assert rec.meta["context_checks"][-1]["outcome"] == "compacted"
    rec.end(outcome="completed", exit_code=0, reason="completed", message="done")


# ===========================================================================
# 件⑥ --max-steps 主动停不计连败
# ===========================================================================

def test_item6_max_steps_stop_not_counted_as_consecutive_failure(rig):
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    for _ in range(3):  # 认领 / 等交回 / 交回 各一步即主动停(--max-steps 1), 每次都有推进
        res, _text = _loop(rig, TASK, _f90_watch(rig), max_steps=1)
        assert res.reason == "max_steps_exhausted", res.reason
    status_text, status = _status(rig.gov, TASK)
    reasons = [m["end_reason"] for m in _records(rig.gov, TASK)]
    _show(f"---- 件⑥ 三次 --max-steps 1 后 loop status(结束原因 {reasons})----\n"
          + "\n".join(ln for ln in status_text.splitlines() if "下一动作" in ln))
    na = status["next_action"]
    assert reasons == ["max_steps_exhausted"] * 3 and na["reason"] != "consecutive_failures" and "连败" not in na["detail"], na
    rules = lrr.next_action_declaration(lrr.run_record_declaration(load_loop_contract()))
    view = {"task_id": "C-1", "state": "ended", "end_reason": "launch_early_exit", "end_message": "早退"}
    root = Path("/nonexistent-f231")
    assert lrr.next_action(view, rules, governance_root=root, earlier_end_reasons=["launch_early_exit"])["reason"] == "consecutive_failures"
    assert lrr.next_action(view, rules, governance_root=root, earlier_end_reasons=["launch_early_exit", "max_steps_exhausted"])["action"] == "investigate"
    ms = {**view, "end_reason": "max_steps_exhausted"}
    assert lrr.next_action(ms, rules, governance_root=root, earlier_end_reasons=["launch_early_exit"])["action"] == "investigate"
    assert rules["consecutive_failures_exempt"] == ["max_steps_exhausted"]


def test_single_sources_and_no_spin():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert "sleep(" not in src and "except Exception" not in src and src.count("def card_paused_stop(") == 1
    rec = (REPO_ROOT / "tools" / "aipos_cli" / "loop_run_record.py").read_text(encoding="utf-8")
    assert "time.sleep(" not in rec.split("def stop_declaration")[1].split("def loop_status(")[0], "停线等待一律经 agent watch"
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    assert verbs["lybra_loop_stop"]["cli_command"] == "lybra loop stop" and verbs["lybra_queue_pause"]["cli_command"] == "lybra queue pause"
    assert verbs["lybra_queue_resume"]["cli_command"] == "lybra queue resume"
    assert socket.gethostname()  # 停线只在记录所在主机清理(unprobeable = 拒)
    advisor = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    line = next(ln for ln in advisor.splitlines() if "**暂停 / 停线(AIPOS-F231)**" in ln)
    assert "queue pause" in line and "loop stop" in line and "早退原因" in line and "禁手动 kill" in line
