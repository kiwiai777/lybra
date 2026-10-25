"""AIPOS-F234 — 撤回在途保护按真相判 / 孤儿审计卡出口 / 合并后回归时限推导与排队。

靶场一律临时 git 仓 + 临时远端裸仓 + 临时治理根(复用 F184 靶场 rig; 不连生产门, 不碰真实治理仓 / chris 治理根)。
「存活进程」= 本测试进程自身(pid + 进程启动时刻指纹), 「已退出进程」= 一个已回收的 `true` 子进程号; 不探任何真实 loop / pi。

件① 在途判据按真相: claimed 卡有近期会话而无存活进程 = 预演与正式撤回都放行; 有存活 loop 运行 / 拉起进程 = 预演与正式都拒, 拒因给
   loop stop 出口与 --ack-in-transit 出口原文; 带 --ack-in-transit = 放行且卡面 withdrawal_reason 记风险确认。
件② 孤儿审计卡: 业务卡已撤换(withdrawn + superseded_by)的审计卡 = lybra next 给「随业务卡撤回」出口(不补跑旧命令), loop status 同;
   有存活进程也不拒(只提醒); `lybra queue orphan-audits` 预演只读列出, --confirm 撤回并落账。
件③ 合并后回归时限: 近 N 次全量实测耗时中位数 × 倍数(声明值为下限, 超时一跑记下限样本); 缺省(timeout_recent_runs=0)行为不变;
   serialize 排队: 有在前的进行中回归 = 等它结束再开跑(记录 queue); 超时结论写当时本机并发全量数。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f184_queue_landing_supersede import (  # noqa: E402
    AUDITOR,
    DRIVER,
    EXEC,
    _card,
    _cli,
    _commit_all,
    _git,
    _session,
    _show,
    rig,  # noqa: F401  — pytest fixture
)
from test_aipos_f73d_loop_driver import _write  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import post_merge_regression as pmr  # noqa: E402
from tools.aipos_cli.clock import iso_z  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract  # noqa: E402


def _run_record(gov: Path, owner: str, *, status: str, pid: int, fingerprint: str | None, launches: list[dict] | None = None,
                steps: list[dict] | None = None, host: str | None = None, seq: int = 1) -> Path:
    import socket

    decl = lrr.run_record_declaration(load_loop_contract())
    now = iso_z()
    run_id = f"looprun_{owner}_20261025_00000{seq}_{pid}"
    meta = {"record_type": decl["record_type"], "run_id": run_id, "task_id": owner, "status": status, "driver": DRIVER,
            "envelope": None, "host": host or socket.gethostname(), "pid": pid, "process_fingerprint": fingerprint,
            "started_at": now, "updated_at": now, "log_path": f"/nonexistent/{run_id}.log", "current_step": None,
            "steps": steps or [], "launches": launches or []}
    if status == "ended":
        meta.update(ended_at=now, outcome="not_derivable", exit_code=4, end_reason="runtime_evidence_exhausted", end_message="靶场")
    path = lrr.loop_runs_dir(gov, owner) / f"{run_id}.md"
    _write(path, lrr.render_run_record(meta))
    return path


def _dead_pid() -> int:
    gone = subprocess.Popen(["true"])  # 已退出并回收的进程号(不探任何真实 loop / pi)
    gone.wait()
    return gone.pid


def _launch(card: str, pid: int, fingerprint: str | None, *, ended: bool) -> dict:
    import socket

    now = iso_z()
    return {"card": card, "harness": "pi", "transport": "local", "host": socket.gethostname(), "pid": pid, "pgid": pid,
            "remote_pgid": None, "process_fingerprint": fingerprint, "started_at": now, "ended_at": now if ended else None,
            "outcome": "artifact_ready" if ended else None, "activity": {"events": 1, "last_event_at": now, "last_activity_kind": "agent_end"}}


def _claimed(gov: Path, task_id: str, *, extra: dict | None = None) -> None:
    sid = f"session_{task_id}_x"
    _card(gov, task_id, "claimed", extra={"claimed_by": EXEC, "claimed_at": iso_z(), "claim_id": f"claim_{task_id}_x",
                                          "active_session_id": sid, **(extra or {})})
    _session(gov, task_id, sid)  # 会话刚建(< 24h): 原 AIPOS-F41 B3 据此拒; 本卡起不看会话时刻


def _audit_card(gov: Path, task_id: str, business: str, queue: str = "claimed") -> None:
    extra = {"task_mode": "audit", "audit": "none", "derived_from": business, "reviewed_task_id": business, "assigned_to": AUDITOR,
             "agent_instance": AUDITOR}
    if queue == "claimed":
        extra.update({"claimed_by": AUDITOR, "claimed_at": iso_z(), "claim_id": f"claim_{task_id}_x",
                      "active_session_id": f"session_{task_id}_x"})
    _card(gov, task_id, queue, assigned=AUDITOR, extra=extra)


def _withdraw(gov: Path, task_id: str, *, dry_run: bool, ack: bool = False) -> dict:
    from tools.aipos_cli.board_adapter import withdraw_task

    return withdraw_task(task_id=task_id, actor=DRIVER, reason="靶场撤回", dry_run=dry_run, repo_root=gov, ack_in_transit=ack)


def _msg(resp: dict) -> str:
    return str((resp.get("errors") or [{}])[0].get("message") or "")


# ---------------------------------------------------------------------------
# 件① 在途判据按真相, 预演与正式同一判据
# ---------------------------------------------------------------------------

def test_item1_not_in_transit_preview_and_confirm_both_pass(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    _claimed(gov, "AIPOS-T234A")
    dead = _dead_pid()
    _run_record(gov, "AIPOS-T234A", status="ended", pid=dead, fingerprint="starttime:1",
                launches=[_launch("AIPOS-T234A", dead, "starttime:1", ended=True)])
    _commit_all(repo, "T234A claimed, executor exited, loop ended")
    preview = _withdraw(gov, "AIPOS-T234A", dry_run=True)
    _show(f"[件①] 进程已退出 + loop 已结束: 预演 verdict={preview['verdict']} in_transit={preview['data']['in_transit']}")
    assert preview["verdict"] != "BLOCK" and preview["data"]["in_transit"]["in_transit"] is False
    assert "非在途" in preview["data"]["in_transit"]["note"]
    confirm = _withdraw(gov, "AIPOS-T234A", dry_run=False)
    _show(f"[件①] 正式撤回 ok={confirm['ok']} to_state={confirm['data'].get('to_state')}")
    assert confirm["ok"] and confirm["data"]["moved"] and (gov / "5_tasks/queue/withdrawn/aipos-t234a.md").is_file()


def test_item1_live_loop_and_live_launch_refused_in_preview_and_confirm_with_exits(rig: dict) -> None:
    repo, gov = rig["repo"], rig["gov"]
    me, fp = os.getpid(), lrr.process_fingerprint(os.getpid())
    _claimed(gov, "AIPOS-T234B")
    _run_record(gov, "AIPOS-T234B", status="running", pid=me, fingerprint=fp)  # loop 在跑
    _claimed(gov, "AIPOS-T234C")
    dead = _dead_pid()
    _run_record(gov, "AIPOS-T234C", status="ended", pid=dead, fingerprint="starttime:1",
                launches=[_launch("AIPOS-T234C", me, fp, ended=False)])  # loop 已结束, 拉起的执行体进程仍在
    _commit_all(repo, "T234B/C in transit")
    for card, want in (("AIPOS-T234B", "loop 运行"), ("AIPOS-T234C", "拉起的 pi 进程")):
        for dry_run in (True, False):
            resp = _withdraw(gov, card, dry_run=dry_run)
            msg = _msg(resp)
            _show(f"[件①] {card} {'预演' if dry_run else '正式'}: {resp['verdict']} {resp['errors'][0]['category']} — {msg}")
            assert resp["verdict"] == "BLOCK" and resp["data"]["error_code"] == "IN_TRANSIT" and msg.startswith("IN_TRANSIT: "), resp
            assert want in msg and f"loop stop --task-id {card}" in msg and "--ack-in-transit" in msg, msg
            assert (gov / f"5_tasks/queue/claimed/{card.lower()}.md").is_file()  # 拒 = 零写入
    rc, out, err = _cli(gov, "queue", "withdraw", "--task-id", "AIPOS-T234B", "--actor", DRIVER, "--reason", "Owner 撤卡",
                        "--ack-in-transit")
    _show(f"[件①] --ack-in-transit 正式撤回 rc={rc}\n{out[-600:]}{err}")
    assert rc == 0
    from tools.aipos_cli.frontmatter import require_frontmatter

    fm, _ = require_frontmatter(gov / "5_tasks/queue/withdrawn/aipos-t234b.md")
    _show(f"[件①] 卡面 withdrawal_reason: {fm['withdrawal_reason']}")
    assert fm["withdrawal_reason"].startswith("Owner 撤卡(在途风险确认 --ack-in-transit: loop 运行")
    assert "队列写落账 withdraw AIPOS-T234B" in _git(repo, "log", "-1", "--format=%s")


def test_item1_mcp_two_phase_carries_ack_and_same_refusal(rig: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """门侧两阶段(预演 → 确认重放)同一判据: 预演所带 ack_in_transit 原样随确认重放(不重复追加确认文案)。"""
    from tools.aipos_cli.board_adapter import withdraw_task

    repo, gov = rig["repo"], rig["gov"]
    me, fp = os.getpid(), lrr.process_fingerprint(os.getpid())
    _claimed(gov, "AIPOS-T234D")
    _run_record(gov, "AIPOS-T234D", status="running", pid=me, fingerprint=fp)
    _commit_all(repo, "T234D in transit")
    preview = withdraw_task(task_id="AIPOS-T234D", actor=DRIVER, reason="门侧撤", dry_run=True, repo_root=gov, ack_in_transit=True)
    assert preview["verdict"] != "BLOCK" and preview["data"]["ack_in_transit"] is True and preview["data"]["reason"] == "门侧撤"
    assert preview["data"]["withdrawal_reason"].startswith("门侧撤(在途风险确认")
    replay = withdraw_task(task_id="AIPOS-T234D", actor=DRIVER, reason=preview["data"]["reason"], dry_run=False, repo_root=gov,
                           ack_in_transit=preview["data"]["ack_in_transit"])
    assert replay["ok"] and replay["data"]["withdrawal_reason"] == preview["data"]["withdrawal_reason"]


# ---------------------------------------------------------------------------
# 件② 孤儿审计卡: next / loop status 出口 + 存量扫描
# ---------------------------------------------------------------------------

def test_item2_orphan_audit_next_status_withdraw_and_scan(rig: dict) -> None:
    from tools.aipos_cli.next_resolver import derive_next_step

    repo, gov = rig["repo"], rig["gov"]
    me, fp = os.getpid(), lrr.process_fingerprint(os.getpid())
    _card(gov, "AIPOS-T234X", "withdrawn", extra={"superseded_by": "AIPOS-T234XB", "superseded_at": iso_z(),
                                                  "withdrawal_reason": "superseded by AIPOS-T234XB"})
    _card(gov, "AIPOS-T234XB", "pending")
    _audit_card(gov, "AIPOS-T234XR", "AIPOS-T234X")
    # 业务卡 loop 已结束, 但(假设)审计进程仍挂着: 孤儿审计卡在途保护不适用, 只提醒
    _run_record(gov, "AIPOS-T234X", status="ended", pid=_dead_pid(), fingerprint="starttime:1",
                launches=[_launch("AIPOS-T234XR", me, fp, ended=False)], steps=[{"index": 1, "card": "AIPOS-T234XR", "kind": "wait"}])
    _card(gov, "AIPOS-T234Y", "completed")
    _audit_card(gov, "AIPOS-T234YR", "AIPOS-T234Y", queue="pending")
    _claimed(gov, "AIPOS-T234Z")  # 业务卡仍在途 → 其审计卡不是孤儿
    _audit_card(gov, "AIPOS-T234ZR", "AIPOS-T234Z", queue="pending")
    from tools.aipos_cli.audit_derivation import supplementary_audit_id

    sup = supplementary_audit_id("AIPOS-T234Y", 1)  # 已结案卡的补充审计卡(AIPOS-F162)本就审已结案卡 → 不是孤儿
    _card(gov, sup, "pending", assigned=AUDITOR, extra={"task_mode": "audit", "audit": "none", "derived_from": "AIPOS-T234Y",
                                                       "reviewed_task_id": "AIPOS-T234Y", "supplementary_of": "AIPOS-T234Y"})
    _commit_all(repo, "orphan audit cards")

    step = derive_next_step("AIPOS-T234XR", gov)
    _show(f"[件②] next AIPOS-T234XR: triggered_by={step['triggered_by']} action={step.get('action')}\n  Suggested: {step['suggested_action']}")
    assert step["derivable"] is False and step["action"]["type"] == "orphan_audit_withdraw"
    assert "已由 AIPOS-T234XB 撤换" in step["suggested_action"] and "不补跑旧命令" in step["suggested_action"]
    assert "queue withdraw --task-id AIPOS-T234XR" in step["action"]["command"] and "queue orphan-audits" in step["suggested_action"]
    assert derive_next_step("AIPOS-T234ZR", gov).get("action", {}).get("type") != "orphan_audit_withdraw"

    rc, out, _ = _cli(gov, "loop", "status", "--task-id", "AIPOS-T234X")
    _show(f"[件②] loop status AIPOS-T234X:\n{out}")
    assert "随业务卡撤回" in out and "AIPOS-T234XR" in out

    preview = _withdraw(gov, "AIPOS-T234XR", dry_run=True)
    _show(f"[件②] 孤儿审计卡撤回预演 {preview['verdict']} warnings={preview['warnings']}")
    assert preview["verdict"] != "BLOCK" and preview["data"]["in_transit"]["orphan"]["business"] == "AIPOS-T234X"
    assert any("在途保护不适用" in w for w in preview["warnings"]) and any("仍在" in w for w in preview["warnings"])

    rc, out, err = _cli(gov, "queue", "orphan-audits")
    _show(f"[件②] 存量扫描预演 rc={rc}\n{out}")
    assert rc == 0 and "孤儿审计卡 AIPOS-T234XR(claimed)" in out and "孤儿审计卡 AIPOS-T234YR(pending)" in out
    assert "AIPOS-T234ZR" not in out and sup not in out and "预演(未执行" in out
    assert (gov / "5_tasks/queue/claimed/aipos-t234xr.md").is_file()  # 预演零写入
    rc, _o, err = _cli(gov, "queue", "orphan-audits", "--confirm")
    assert rc == 2 and "--actor" in err
    rc, out, err = _cli(gov, "queue", "orphan-audits", "--confirm", "--actor", DRIVER)
    _show(f"[件②] 存量扫描确认 rc={rc}\n{out}{err}")
    assert rc == 0 and "孤儿审计卡 AIPOS-T234XR(claimed)已撤回" in out
    for card in ("aipos-t234xr", "aipos-t234yr"):
        assert (gov / f"5_tasks/queue/withdrawn/{card}.md").is_file()
    assert (gov / "5_tasks/queue/pending/aipos-t234zr.md").is_file() and (gov / f"5_tasks/queue/pending/{sup.lower()}.md").is_file()
    assert "队列写落账 orphan-audits" in _git(repo, "log", "-1", "--format=%s")
    rc, out, _ = _cli(gov, "queue", "orphan-audits")
    assert rc == 0 and "无孤儿审计卡" in out


# ---------------------------------------------------------------------------
# 件③ 合并后回归: 时限推导 / 缺省不变 / 排队串行 / 超时写并发数
# ---------------------------------------------------------------------------

def _pmr_record(gov: Path, task: str, result: dict) -> str:
    return pmr.write_event_record(gov, task, DRIVER, {"task_id": task, "runall_path": "tests/run-all.sh", **result})


def test_item3_timeout_derived_from_recent_runs_default_unchanged(rig: dict) -> None:
    gov = rig["gov"]
    _pmr_record(gov, "AIPOS-T1", {"merge_commit": "a" * 40, "status": "pass", "merged_complete": True, "merged_seconds": 1440.0})
    _pmr_record(gov, "AIPOS-T2", {"merge_commit": "b" * 40, "status": "pass", "merged_complete": True, "merged_seconds": 1480.0,
                                  "baseline_seconds": 1460.0})
    _pmr_record(gov, "AIPOS-T3", {"merge_commit": "c" * 40, "status": "timeout", "merged_complete": False, "merged_seconds": 1500.1,
                                  "error": "合并后一跑: 测试清单在 1500s 内未跑完, 已终止"})
    _pmr_record(gov, "AIPOS-T4", {"merge_commit": "d" * 40, "status": "pass", "merged_complete": True, "merged_seconds": 99.0,
                                  "runall_path": "other/run.sh"})  # 别的清单不计
    got = pmr.derive_timeout(gov, runall_path="tests/run-all.sh", declared=1500, recent_runs=5, factor=1.5)
    _show(f"[件③] 推导: {got['basis']}")
    assert sorted(x["seconds"] for x in got["samples"]) == [1440.0, 1460.0, 1480.0, 1500.1]
    assert got["median"] == 1470.0 and got["timeout_seconds"] == 2205  # ceil(1.5 × 1470)
    assert any(x["lower_bound"] for x in got["samples"]) and "≥1500.1s" in got["basis"]
    assert pmr.derive_timeout(gov, runall_path="tests/run-all.sh", declared=3000, recent_runs=5, factor=1.5)["timeout_seconds"] == 3000
    assert pmr.derive_timeout(gov, runall_path="tests/run-all.sh", declared=1500, recent_runs=0, factor=1.5)["timeout_seconds"] == 1500
    assert pmr.derive_timeout(gov, runall_path="none.sh", declared=1500, recent_runs=5, factor=1.5)["basis"].endswith("(无全量实测样本可推导)")

    project = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    project["test_contract"] = {"runall_path": "tests/run-all.sh", "post_merge_regression": {"mode": "warn", "execution": "async",
                                                                                             "timeout_seconds": 1500}}
    _write(gov / "project.json", json.dumps(project))
    policy = pmr.resolve_policy(gov, Path(project["code_repo"]))
    _show(f"[件③] 缺省(未声明推导): timeout={policy['timeout_seconds']} serialize={policy['serialize']} — {policy['timeout_basis']}")
    assert policy["timeout_seconds"] == 1500 and policy["serialize"] is False  # 缺省 = 行为不变
    project["test_contract"]["post_merge_regression"].update({"timeout_recent_runs": 5, "timeout_factor": 1.5, "serialize": True})
    _write(gov / "project.json", json.dumps(project))
    policy = pmr.resolve_policy(gov, Path(project["code_repo"]))
    _show(f"[件③] 声明推导: timeout={policy['timeout_seconds']} declared={policy['declared_timeout_seconds']} — {policy['timeout_basis']}")
    assert policy["timeout_seconds"] == 2205 and policy["declared_timeout_seconds"] == 1500 and policy["serialize"] is True
    from tools.aipos_cli.workspace_config import project_test_contract

    for bad, needle in (({"timeout_recent_runs": -1}, "≥0 整数"), ({"timeout_factor": 0.5}, "≥1 的数"), ({"serialize": "yes"}, "true/false")):
        project["test_contract"]["post_merge_regression"] = {"mode": "warn", **bad}
        _write(gov / "project.json", json.dumps(project))
        with pytest.raises(ValueError, match="TEST_CONTRACT_INVALID") as exc:
            project_test_contract(gov)
        assert needle in str(exc.value)


def test_item3_serialize_waits_for_earlier_running_regression(rig: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    gov = rig["gov"]
    monkeypatch.setattr(pmr, "_QUEUE_POLL_SECONDS", 0.2)
    ahead = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)  # 在前的回归(假)
    try:
        _pmr_record(gov, "AIPOS-T234Q", {"merge_commit": "e" * 40, "status": "running", "execution": "async", "async_pid": ahead.pid,
                                         "process_fingerprint": lrr.process_fingerprint(ahead.pid), "host": socket.gethostname(),
                                         "started_at": iso_z(), "timeout_seconds": 3000})
        rows = pmr.regressions_ahead(gov, own_pid=os.getpid(), own_key=pmr.own_queue_key(gov, os.getpid()))
        assert [r["task_id"] for r in rows] == ["AIPOS-T234Q"]
        threading.Timer(1.0, ahead.stdin.close).start()  # 1s 后在前者结束
        result = {"status": "pass", "mode": "warn", "merged_failures": [], "baseline_failures": [], "baseline_source": "record:x"}
        pmr._queued(result, gov, "AIPOS-T234S", serialize=True, own_pid=os.getpid(), default_timeout=1500)
        _show(f"[件③] 排队: {result['queue']}\n  {pmr.summary_line(result)}")
        assert result["queue"]["behind"][0]["task_id"] == "AIPOS-T234Q" and result["queue"]["still_ahead"] == 0
        assert 0.5 <= result["queue"]["waited_seconds"] < 30 and "已结束后开跑" in result["queue"]["note"]
        assert "(排队: 排队" in pmr.summary_line(result)
        quiet: dict = {}
        pmr._queued(quiet, gov, "AIPOS-T234S", serialize=False, own_pid=os.getpid(), default_timeout=1500)
        assert quiet == {}  # serialize 缺省 false = 不排队(行为不变)
    finally:
        if ahead.poll() is None:
            ahead.kill()
        ahead.wait()


def test_item3_timeout_conclusion_names_concurrent_full_runs(tmp_path: Path) -> None:
    repo = tmp_path / "prod"
    repo.mkdir()
    _write(repo / "tests/run-all.sh", "#!/usr/bin/env bash\nexec python3 -c 'import select; select.select([], [], [])'\n")
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    res = pmr.run_suite(repo, head, "tests/run-all.sh", 1.0, log_dir=tmp_path)
    _show(f"[件③] 超时结论: {res['status']} — {res['error']}(开跑时并发 {res.get('concurrent_at_start')})")
    assert res["status"] == "timeout" and "当时本机并发全量" in res["error"] and "含本次" in res["error"]
    assert "concurrent_full_runs" in res and "concurrent_at_start" in res
