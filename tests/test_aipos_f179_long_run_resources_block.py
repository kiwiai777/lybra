"""AIPOS-F179: long verification runs, exclusive resources, audit BLOCK = fix environment then re-audit.

Source incident (10-16, chris HBLX-8): the auditor verified module tests / builds / new UI tests in its own tree, but the
full UI suite (38 items, ~600s) exceeded the timeout it passed to its bash tool (420s), then hit an iOS simulator conflict
with another card; it gave BLOCK, the derivation had no path for BLOCK ("handle verdict BLOCK" not derivable) and the
runs chained into consecutive_failures -> owner_needed.

Items (one implementation each, no second copy):
  1 `lybra run-long start|wait|status` = tools/aipos_cli/run_long.py (verbs.schema lybra_run_long); runtime evidence
    entries may reference a run (transitions artifact_ingest.verdict.runtime_evidence.run_long, checked in
    board_adapter.runtime_evidence_check).
  2 card field `resources` (card.schema, enums.schema exclusive_resource); judgement loop_run_record.resource_conflict,
    queueing at the loop launch entry (loop_driver._acquire_resources, next to the F171B single-instance lock).
  3 N4 BLOCK -> transitions nodes.N4.audit_blocked (next_resolver.audit_block_state / reaudit_command, in the N4
    non-PASS branch next to F155 rework); loop end reasons audit_blocked (advisor_fix) / audit_blocked_repeated (owner_needed).
  4 charters (auditor / executor / advisor masters) render the new clauses.

Rig: F90 in-process gate + temporary governance root / product repo (no real gate, no real pi, no real loop on real
roots). The second concurrent loop of item 2 is a real `lybra loop` subprocess on the temporary root; every process the
tests start is waited for or killed by the test itself.
"""
from __future__ import annotations

import io
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  - loop rig (single source)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _fm, _git, _make_gov, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  - pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import run_long  # noqa: E402
from tools.aipos_cli.agent_watch_fs import run_fs_watch  # noqa: E402
from tools.aipos_cli.artifact_ingest import validate_task_artifact  # noqa: E402
from tools.aipos_cli.loop_driver import _watch_args, exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK, AUDIT = f90.TASK, f90.AUDIT
CARD_B, CARD_C = "PROBE-F179-B", "PROBE-F179-C"
SIM = ["ios-simulator"]
# a "long" verification command: 4 ticks of 1s; the simulated tool limit per call is 1s (wait --timeout 1)
CMD_LONG = "python3 -c \"import time; [(print('tick', i, flush=True), time.sleep(1)) for i in range(4)]; print('RESULT run=38 failures=0')\""
BLOCK_SUMMARY = "BLOCK — UI 全量 38 项约 600s 超过本次会话工具单次时限且模拟器被他卡占用, 无法独立跑完"


def _show(text: str) -> None:
    print(text, flush=True)


def _rl(gov: Path, *argv: str) -> tuple[int, str]:
    rc, out, err = _cli(["--workspace-root", str(gov), "run-long", *argv])
    return rc, out + err


def _loop(rig, task_id: str, watch, **kw):
    out = io.StringIO()
    res = run_loop(task_id, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch, interval=0.05,
                   max_wait=kw.pop("max_wait", 5), max_steps=kw.pop("max_steps", 40))
    return res, out.getvalue()


def _quiet(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("[ENVELOPE"))


def _records(gov: Path, card: str) -> list[dict]:
    return [nr._read_frontmatter(p) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))]


def _status(gov: Path, card: str) -> tuple[str, dict]:
    rc, out, err = _cli(["loop", "status", "--task-id", card, "--workspace-root", str(gov)])
    rcj, outj, _ = _cli(["loop", "status", "--task-id", card, "--workspace-root", str(gov), "--json"])
    return out + err, json.loads(outj)


def _is_resource_wait(args) -> bool:
    return any("looprun_" in p for p in args.expect)


# ===========================================================================
# item 1: run-long - start / segmented wait / record files / runtime evidence reference
# ===========================================================================

def _audit_report(rig, audit_id: str, verdict: str, *, evidence: list | None = None, summary: str | None = None) -> Path:
    tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
    section = ""
    if evidence is not None:
        section = "\n## 独立运行证据\n\n```json\n" + json.dumps(evidence, ensure_ascii=False, indent=1) + "\n```\n"
    text = summary or ("逐条复核通过" if verdict.startswith("PASS") else "打回")
    report = rig.gov / "task_cards" / audit_id / "audit_report.md"
    _write(report, _fm({"task_id": audit_id, "reviewed_task_id": TASK, "verdict": verdict, "commit_sha": tip, "model": f90.SELF_AUDIT_MODEL},
                       f"# 审计报告 {audit_id}\n\n## 一句话结论\n{text}\n\n## 证据\n- 夹具\n{section}"))
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL,
                    name=f"sess-{audit_id}-{verdict}.jsonl")
    return report


def test_item1_run_long_segmented_wait_and_evidence_reference_complete_the_card(rig):
    f90._card(rig.gov, TASK, "pending", audit_runtime_evidence={"required": True, "commands": [CMD_LONG]})
    init_governance_repo(rig.gov)
    seen: dict = {"waits": []}

    def audit_writer(r):
        assert "start" not in seen, f"audit writer called again; first check: {seen.get('check')}"
        _code, wt = nr.card_worktree_location(r.gov, AUDIT)
        rc, out = _rl(r.gov, "start", "--task-id", AUDIT, "--name", "ui-suite", "--cwd", str(wt), "--", CMD_LONG)
        seen["start"] = (rc, out)
        assert rc == 0, out
        for _ in range(30):  # bounded: each call is one 1s segment through the product wait (agent watch), never a sleep loop
            rc, out = _rl(r.gov, "wait", "--task-id", AUDIT, "--name", "ui-suite", "--timeout", "1")
            seen["waits"].append((rc, out))
            if rc != run_long.exit_code("still_running"):
                break
        run_dir = run_long.run_dir(r.gov, AUDIT, "ui-suite")
        seen["run_dir"] = run_dir
        _audit_report(r, AUDIT, "PASS", evidence=[{"run_long": str(run_dir)}])
        seen["check"] = validate_task_artifact(AUDIT, r.gov)

    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            audit_writer(rig)
        else:
            f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    res, text = _loop(rig, TASK, watch)
    rc, out = seen["start"]
    _show(f"---- item1 `lybra run-long start` exit {rc} ----\n{out}")
    for i, (rc_w, out_w) in enumerate(seen["waits"], 1):
        _show(f"---- item1 `lybra run-long wait --timeout 1` #{i} exit {rc_w} ----\n{out_w}")
    still = run_long.exit_code("still_running")
    assert seen["waits"][0][0] == still and "仍在跑, 再等一段" in seen["waits"][0][1], "first 1s segment must return progress, not the result"
    assert seen["waits"][-1][0] == 0 and len(seen["waits"]) >= 3, "the 4s command is completed through several 1s segments"
    run_dir: Path = seen["run_dir"]
    files = {p.name: p for p in run_dir.iterdir()}
    log_text = files["output.log"].read_text(encoding="utf-8")
    exit_text = files["exit_code"].read_text(encoding="utf-8")
    meta = json.loads(files["run.json"].read_text(encoding="utf-8"))
    result = json.loads(files["result.json"].read_text(encoding="utf-8"))
    _show(f"[item1] run dir {run_dir} files {sorted(files)}\n[item1] output.log:\n{log_text}[item1] exit_code file: {exit_text!r}\n"
          f"[item1] run.json command/cwd: {meta['command']} | {meta['cwd']}\n[item1] result.json: {result}")
    assert run_dir == nr.verdict_artifact_dir(rig.gov, AUDIT) / "run_long" / "ui-suite", "record lands under the audit card report dir"
    assert "tick 3" in log_text and "RESULT run=38 failures=0" in log_text and exit_text == "0\n"
    assert meta["command"] == CMD_LONG and meta["cwd"] == str(nr.card_worktree_location(rig.gov, AUDIT)[1].resolve())
    assert result["exit_code"] == 0 and result["log_sha256"] == run_long._sha256(files["output.log"])
    check = seen["check"]
    _show(f"[item1] verdict entry pre-check with evidence {{\"run_long\": \"{run_dir}\"}}: category={check['category']} ok={check['ok']}")
    assert check["category"] == "OK" and check["ok"], check
    _show(f"---- item1 lybra loop (PASS with run-long evidence) exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    verdict = nr._read_task_records(rig.gov, TASK)["latest_verdict"]
    assert verdict["verdict"] == "PASS" and verdict["audit_task_id"] == AUDIT


def test_item1_run_long_refusals_timeout_lost_and_tampered_log(tmp_path, monkeypatch):
    gov = _make_gov(tmp_path, monkeypatch, shape="chris", project="lybra")
    f90._card(gov, AUDIT, "claimed", task_mode="audit", reviewed_task_id=TASK)
    wt = tmp_path / "audit-tree"
    wt.mkdir()
    # declared paths (chris shape: verdict_root = 5_tasks/records/audit_verdicts) decide where the run lands
    rc, out = _rl(gov, "start", "--task-id", AUDIT, "--name", "quick", "--cwd", str(wt), "--", "echo", "hello world")
    _show(f"---- item1 start (argv form, chris-shaped paths) exit {rc} ----\n{out}")
    assert rc == 0 and str(gov / "5_tasks" / "records" / "audit_verdicts" / AUDIT / "run_long" / "quick") in out
    rc, out = _rl(gov, "wait", "--task-id", AUDIT, "--name", "quick", "--timeout", "30")
    assert rc == 0 and "退出码 0" in out, out
    rc, out = _rl(gov, "start", "--task-id", AUDIT, "--name", "quick", "--cwd", str(wt), "--", "true")
    _show(f"---- item1 name reuse refused exit {rc} ----\n{out}")
    assert rc == run_long.exit_code("refused") and "已用" in out and "绝不覆盖既有运行" in out
    for argv, needle in ((["start", "--task-id", AUDIT, "--name", "x", "--cwd", "rel/dir", "--", "true"], "--cwd 须为已存在目录的绝对路径"),
                         (["start", "--task-id", AUDIT, "--name", "bad name", "--cwd", str(wt), "--", "true"], "运行名"),
                         (["start", "--task-id", AUDIT, "--name", "y", "--cwd", str(wt)], "缺命令"),
                         (["start", "--task-id", "NOPE-1", "--name", "z", "--cwd", str(wt), "--", "true"], "队列中找不到卡"),
                         (["wait", "--task-id", AUDIT, "--name", "quick", "--timeout", "999999"], "--timeout 须在")):
        rc, out = _rl(gov, *argv)
        assert rc == run_long.exit_code("refused") and needle in out, (argv, out)
    # --max-seconds bound: the supervisor terminates the command group, exit code = declared timeout_exit_code
    rc, out = _rl(gov, "start", "--task-id", AUDIT, "--name", "bounded", "--cwd", str(wt), "--max-seconds", "1", "--", "sleep 30")
    assert rc == 0, out
    rc, out = _rl(gov, "wait", "--task-id", AUDIT, "--name", "bounded", "--timeout", "60")
    _show(f"---- item1 --max-seconds 1 on `sleep 30`: wait exit {rc} ----\n{out}")
    decl = run_long.declaration()
    assert rc == 0 and f"退出码 {decl['timeout_exit_code']} (超上限被终止)" in out
    # lost: supervisor gone without an exit file (simulated reboot: the finished run loses its exit/result files)
    quick = run_long.run_dir(gov, AUDIT, "quick")
    (quick / "exit_code").unlink()
    (quick / "result.json").unlink()
    rc, out = _rl(gov, "wait", "--task-id", AUDIT, "--name", "quick", "--timeout", "5")
    _show(f"---- item1 lost run: wait exit {rc} ----\n{out}")
    assert rc == run_long.exit_code("lost") and "lost" in out and "换名重跑" in out
    # evidence reference: unfinished / tampered log are refused with the reason
    bounded = run_long.run_dir(gov, AUDIT, "bounded")
    fields, problem = run_long.evidence_from_record(str(bounded))
    assert fields is not None and fields["exit_code"] == decl["timeout_exit_code"] and fields["command"] == "sleep 30"
    fields, problem = run_long.evidence_from_record(str(quick))
    _show(f"[item1] unfinished reference: {problem}")
    assert fields is None and "未收尾" in problem
    with (bounded / "output.log").open("a", encoding="utf-8") as handle:
        handle.write("forged line\n")
    fields, problem = run_long.evidence_from_record(str(bounded / "run.json"))
    _show(f"[item1] tampered log reference: {problem}")
    assert fields is None and "日志收尾后被改动" in problem
    fields, problem = run_long.evidence_from_record("relative/dir")
    assert fields is None and "绝对路径" in problem
    rc, out = _rl(gov, "status", "--task-id", AUDIT)
    _show(f"---- item1 run-long status (all runs) exit {rc} ----\n{out}")
    assert rc == 0 and out.count("run-long ") == 2


# ===========================================================================
# item 2: exclusive resources - two loops on the same resource
# ===========================================================================

def _await_record(gov: Path, card: str, predicate, timeout: float = 90) -> bool:
    """Wait (product watch primitive, no sleep loop) until the card's latest run record satisfies predicate."""
    root = gov / "5_tasks" / "records" / "loop_runs" / card
    root.mkdir(parents=True, exist_ok=True)

    def ready(_matched) -> bool:
        recs = _records(gov, card)
        return bool(recs) and predicate(recs[-1])

    sink = io.StringIO()
    import contextlib

    with contextlib.redirect_stdout(sink):
        rc = run_fs_watch(_watch_args(root, ["looprun_*.md"], max_wait=timeout, interval=0.1), expect_ready=ready)
    return rc == 0


def _resource_watch(rig, handler):
    def watch(args, expect_ready, deploy_guard=None):
        if _is_resource_wait(args):
            return run_fs_watch(args, expect_ready=expect_ready)
        return handler(args, expect_ready)

    return watch


def _preclaim(rig, card: str) -> str:
    """Claim the card through the loop (its executor wait times out at once); the run holds then releases the resource."""
    res, text = _loop(rig, card, _resource_watch(rig, lambda args, ready: 3), max_wait=1)
    assert res.reason == "wait_timeout", text
    return text


def test_item2_second_loop_waits_for_holder_then_continues_and_dead_holder_releases(rig, tmp_path):
    for card in (TASK, CARD_B, CARD_C):
        f90._card(rig.gov, card, "pending", resources=SIM)
    init_governance_repo(rig.gov)
    pre_b = _preclaim(rig, CARD_B)
    _preclaim(rig, CARD_C)
    _show("---- item2 pre-claim of B (holds then releases on run end) ----\n" + "\n".join(
        ln for ln in pre_b.splitlines() if "resource" in ln or "exit" in ln))
    assert "resource: 独占资源 ios-simulator 已由本卡 loop 持有(运行结束即释放)" in pre_b
    b_log = tmp_path / "loop-b.out"
    ctx: dict = {}

    def a_handler(args, expect_ready):
        patterns = " ".join(args.expect)
        if f"/{AUDIT}/" in patterns or patterns.startswith(f"task_cards/{AUDIT}"):
            f90._auditor_work(rig)
            return 0 if expect_ready([]) else 3
        if "proc" not in ctx:
            env = {k: v for k, v in os.environ.items() if not k.startswith("LYBRA_")}
            env["PYTHONPATH"] = str(REPO_ROOT)
            with b_log.open("w", encoding="utf-8") as handle:
                ctx["proc"] = subprocess.Popen(
                    [sys.executable, "-m", "tools.aipos_cli", "loop", "--task-id", CARD_B, "--workspace-root", str(rig.gov),
                     "--actor", DRIVER, "--envelope", f90.POLICY, "--max-wait", "120", "--interval", "0.2", "--max-steps", "5"],
                    cwd=str(REPO_ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
            ctx["queued"] = _await_record(rig.gov, CARD_B, lambda m: (m.get("resource_lock") or {}).get("state") == "waiting")
            ctx["status_b"] = _status(rig.gov, CARD_B)
        f90._executor_work(rig)
        return 0 if expect_ready([]) else 3

    try:
        res_a, text_a = _loop(rig, TASK, _resource_watch(rig, a_handler))
        _show(f"---- item2 loop A (holder) exit {res_a.exit_code} ----\n" + "\n".join(
            ln for ln in _quiet(text_a).splitlines() if "resource" in ln or "exit" in ln or "done" in ln))
        assert res_a.exit_code == 0 and res_a.outcome == "completed", text_a
        assert ctx.get("queued"), "loop B never queued behind A"
        status_text, status_json = ctx["status_b"]
        _show("---- item2 `lybra loop status --task-id B` while queued ----\n" + status_text)
        run_a = next(r for r in _records(rig.gov, TASK) if r.get("resource_lock", {}).get("state") == "held" and r["status"] == "ended")
        na = status_json["next_action"]
        assert na["action"] == "continue_wait" and na["reason"] == "resource_wait", na
        assert f"独占资源 ios-simulator 被占用: 卡 {TASK}(run {run_a['run_id']}, pid {os.getpid()} @ " in na["detail"]
        assert "本卡 loop 排队等待, 占用者 loop 结束后自动继续" in na["detail"]
        assert f"占用者 卡 {TASK} run {run_a['run_id']} pid {os.getpid()}" in status_text
        # A ended -> B continues by itself (same process, no restart)
        assert _await_record(rig.gov, CARD_B, lambda m: (m.get("resource_lock") or {}).get("state") == "held"), "B did not take over"
        out_b = b_log.read_text(encoding="utf-8")
        _show("---- item2 loop B output (real `lybra loop` subprocess) ----\n" + out_b)
        assert "resource_wait: 独占资源 ios-simulator 被占用: 卡 " + TASK in out_b
        assert "resource: 独占资源 ios-simulator 已由本卡 loop 持有(运行结束即释放); 排队 " in out_b and "s 后获得(占用者已结束)" in out_b
        # dead holder: B is killed (SIGKILL, record never ends) -> its lock no longer counts (F137 liveness)
        ctx["proc"].send_signal(signal.SIGKILL)
        ctx["proc"].wait(timeout=60)
        dead_text, dead_json = _status(rig.gov, CARD_B)
        _show("---- item2 `lybra loop status --task-id B` after SIGKILL ----\n" + dead_text)
        assert dead_json["runs"][0]["state"] == "loop_dead" and dead_json["runs"][0]["resource_lock"]["state"] == "held"
        res_c, text_c = _loop(rig, CARD_C, _resource_watch(rig, lambda args, ready: 3), max_wait=1)
        _show("---- item2 loop C after B died ----\n" + "\n".join(ln for ln in text_c.splitlines() if "resource" in ln or "exit" in ln))
        assert "resource_wait" not in text_c and "resource: 独占资源 ios-simulator 已由本卡 loop 持有(运行结束即释放)" in text_c
    finally:
        proc = ctx.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=60)


def test_item2_order_cross_waiters_and_bounded_wait_ends_resource_busy(rig, tmp_path):
    contract = load_loop_contract()
    gov = rig.gov
    msgs: list[str] = []
    a = lrr.LoopRunRecorder(gov, "X-1", driver="d", contract=contract, warn=msgs.append)
    b = lrr.LoopRunRecorder(gov, "X-2", driver="d", contract=contract, warn=msgs.append)
    other = lrr.LoopRunRecorder(gov, "X-3", driver="d", contract=contract, warn=msgs.append)
    a.resource_request(SIM, "X-1")
    b.resource_request(SIM, "X-2")
    other.resource_request(["android-emulator"], "X-3")
    assert lrr.resource_conflict(gov, a.meta, contract=contract) is None, "earlier request is not blocked by a later waiter"
    hit = lrr.resource_conflict(gov, b.meta, contract=contract)
    assert hit is not None and hit["run_id"] == a.run_id and hit["state"] == "waiting", "later request queues behind the earlier one"
    assert lrr.resource_conflict(gov, other.meta, contract=contract) is None, "different resource = no conflict"
    a.resource_held()
    hit = lrr.resource_conflict(gov, b.meta, contract=contract)
    _show(f"[item2] holder line (unit): {hit['message']}")
    assert hit["state"] == "held" and "持有中" in hit["message"]
    # both marked held in the same instant (each judged before seeing the other): the re-check after holding lets the
    # earlier-ordered one keep it and sends the later one back to waiting - never two holders
    b.resource_held()
    assert lrr.resource_conflict(gov, a.meta, contract=contract) is None
    back = lrr.resource_conflict(gov, b.meta, contract=contract)
    assert back is not None and back["run_id"] == a.run_id and back["state"] == "held"
    b.resource_waiting(back)
    a.end(outcome="completed", exit_code=0, reason="completed", message="done")
    assert lrr.resource_conflict(gov, b.meta, contract=contract) is None, "ended run releases"
    b.end(outcome="completed", exit_code=0, reason="completed", message="done")
    other.end(outcome="completed", exit_code=0, reason="completed", message="done")
    # bounded wait: the holder never ends within --max-wait -> resource_busy (wait_timeout exit), advisor_fix with holder line
    f90._card(gov, TASK, "pending", resources=SIM)
    holder = lrr.LoopRunRecorder(gov, "X-9", driver="d", contract=contract, warn=msgs.append)
    holder.resource_request(SIM, "X-9")
    holder.resource_held()
    try:
        res, text = _loop(rig, TASK, _resource_watch(rig, lambda args, ready: 3), max_wait=1)
    finally:
        holder.end(outcome="completed", exit_code=0, reason="completed", message="done")
    _show(f"---- item2 bounded resource wait: lybra loop exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.reason == "resource_busy" and res.exit_code == exit_code_for(contract, "wait_timeout")
    assert "等独占资源 ios-simulator 超过 1s 仍被占用" in res.message and "卡 X-9" in res.message
    status_text, status_json = _status(gov, TASK)
    _show("---- item2 loop status after resource_busy ----\n" + status_text)
    assert status_json["next_action"]["action"] == "advisor_fix" and status_json["next_action"]["reason"] == "resource_busy"


def test_item2_card_field_shape_single_check_and_declarations(rig):
    from tools.aipos_cli.draft_validator import validate_draft_metadata
    from tools.schema_loader import get_enum_values, load_schema

    assert get_enum_values("exclusive_resource") == ["ios-simulator", "android-emulator"]
    card = load_schema("card")
    assert card["fields"]["resources"]["items"]["$enum"] == "exclusive_resource" and "resources" in card["advisor_zone"]["fields"]
    assert lrr.card_resources({"resources": ["ios-simulator", "ios-simulator"]}) == ["ios-simulator"]
    assert lrr.card_resources({}) == [] and lrr.card_resources(None) == []
    for bad in ("ios-simulator", ["xcode"], [1]):
        with pytest.raises(ValueError, match="CARD_RESOURCES_INVALID"):
            lrr.card_resources({"resources": bad})
    good = validate_draft_metadata(rig.gov, {"task_id": "DRAFT-1", "resources": ["android-emulator"], "body": "x"})
    bad = validate_draft_metadata(rig.gov, {"task_id": "DRAFT-1", "resources": ["xcode"], "body": "x"})
    bad_reasons = [r for r in bad["blocking_reasons"] if "CARD_RESOURCES_INVALID" in r]
    _show(f"[item2] draft validator on resources=['xcode']: {bad_reasons}")
    assert bad_reasons and not [r for r in good["blocking_reasons"] if "CARD_RESOURCES_INVALID" in r]
    # a card already claimed (HBLX-8 shape) gets the declaration through restricted amend; bad values are refused the same way
    from tools.aipos_cli.board_adapter import amend_task, restricted_amend_fields

    f90._card(rig.gov, "AMEND-1", "claimed", claimed_by=EXEC, claim_id="c1")
    ok = amend_task(task_id="AMEND-1", actor=DRIVER, amendments={"resources": SIM}, amendment_reason="补独占资源声明",
                    restricted_fields=restricted_amend_fields(), dry_run=False, repo_root=rig.gov)
    assert ok.get("verdict") != "BLOCK", ok
    assert nr._read_frontmatter(nr._find_task_in_queue(rig.gov, "AMEND-1")[0])["resources"] == SIM
    refused = amend_task(task_id="AMEND-1", actor=DRIVER, amendments={"resources": ["xcode"]}, amendment_reason="错值",
                         restricted_fields=restricted_amend_fields(), dry_run=True, repo_root=rig.gov)
    _show(f"[item2] restricted amend resources=['xcode']: {refused.get('message') or refused}")
    assert refused.get("verdict") == "BLOCK" and "CARD_RESOURCES_INVALID" in json.dumps(refused, ensure_ascii=False)
    decl = lrr.resource_lock_declaration(load_loop_contract())
    assert decl["end_reason"] == "resource_busy" and decl["card_field"] == "resources" and decl["enum"] == "exclusive_resource"
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert src.count("resource_conflict(") == 1 and "sleep(" not in src and "except Exception" not in src


# ===========================================================================
# item 3: audit BLOCK -> advisor_fix(audit_blocked) + re-audit command, not a consecutive failure; 2 in a row -> owner
# ===========================================================================

def _block_watch(rig, verdicts: dict[str, str]):
    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        audit_id = next((c for c in verdicts if f"/{c}/" in patterns or patterns.startswith(f"task_cards/{c}/")), None)
        if audit_id is None:
            f90._executor_work(rig)
        else:
            value = verdicts[audit_id]
            _audit_report(rig, audit_id, value, summary=BLOCK_SUMMARY if value == "BLOCK" else None)
        return 0 if expect_ready([]) else 3

    return watch


def _first_block(rig) -> tuple[str, dict, str]:
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    # an earlier investigate-class run (max steps exhausted) - under the old rule it chains with the BLOCK into consecutive_failures
    res0, _text0 = _loop(rig, TASK, _block_watch(rig, {}), max_steps=1)
    assert res0.reason == "max_steps_exhausted"
    res, text = _loop(rig, TASK, _block_watch(rig, {AUDIT: "BLOCK"}))
    _show(f"---- item3 lybra loop (audit BLOCK) exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.reason == "audit_blocked" and res.exit_code == exit_code_for(load_loop_contract(), "not_derivable"), text
    status_text, status_json = _status(rig.gov, TASK)
    _show("---- item3 lybra loop status after BLOCK ----\n" + status_text)
    return text, status_json, status_text


def test_item3_block_advisor_fix_reaudit_then_pass_completes(rig):
    text, status_json, status_text = _first_block(rig)
    na = status_json["next_action"]
    assert na["action"] == "advisor_fix" and na["reason"] == "audit_blocked", na
    assert "consecutive_failures" not in status_text and "连败" not in na["detail"]
    reaudit = next(step for step in na["fix"] if step.startswith("修好后重新派审计下一轮: "))[len("修好后重新派审计下一轮: "):]
    assert BLOCK_SUMMARY[:40] in na["fix"][0], na["fix"][0]
    assert reaudit.startswith(f"lybra --workspace-root {rig.gov} audit dispatch --source-task-id {TASK} --actor {DRIVER}")
    assert f"--owner-policy-ref {f90.POLICY} --audit-task-id {TASK}R2 --audit-agent-instance {AUDITOR}" in reaudit
    assert f"重审命令: {reaudit}" in text, "the loop ending carries the same command"
    d = nr.derive_next_step(TASK, rig.gov)
    _show("---- item3 `lybra next` derivation after BLOCK ----\n" + json.dumps(
        {k: d.get(k) for k in ("derivable", "current_node", "triggered_by", "suggested_action", "notes", "action")}, ensure_ascii=False, indent=2))
    assert d["action"]["type"] == "audit_blocked" and d["action"]["end_reason"] == "audit_blocked" and "需人工处理" not in d["notes"]
    argv = shlex.split(reaudit)
    rc, out, err = _cli(argv[1:])
    _show(f"---- item3 advisor runs the re-audit command, exit {rc} ----\n{out[:1500]}{err}")
    assert rc == 0, out + err
    assert nr._read_task_records(rig.gov, TASK)["audit_round_ids"] == [AUDIT, f"{TASK}R2"]
    d2 = nr.derive_next_step(TASK, rig.gov)
    assert d2["action"] == {"type": "await_artifact", "card": f"{TASK}R2"}, d2
    status_after = _status(rig.gov, TASK)[1]["next_action"]
    assert any("已派下一轮审计" in s for s in status_after.get("fix") or []), status_after
    res, text2 = _loop(rig, TASK, _block_watch(rig, {f"{TASK}R2": "PASS"}))
    _show(f"---- item3 lybra loop after re-audit (R2 PASS) exit {res.exit_code} ----\n{_quiet(text2)}")
    assert res.exit_code == 0 and res.outcome == "completed", text2
    assert nr._read_task_records(rig.gov, TASK)["latest_verdict"]["audit_task_id"] == f"{TASK}R2"


def test_item3_two_consecutive_blocks_escalate_owner_needed(rig):
    _text, status_json, _status_text = _first_block(rig)
    reaudit = next(s for s in status_json["next_action"]["fix"] if s.startswith("修好后重新派审计下一轮: "))[len("修好后重新派审计下一轮: "):]
    rc, out, err = _cli(shlex.split(reaudit)[1:])
    assert rc == 0, out + err
    res, text = _loop(rig, TASK, _block_watch(rig, {f"{TASK}R2": "BLOCK"}))
    _show(f"---- item3 second BLOCK in a row: lybra loop exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.reason == "audit_blocked_repeated", text
    status_text, status = _status(rig.gov, TASK)
    _show("---- item3 lybra loop status after 2 consecutive BLOCK ----\n" + status_text)
    na = status["next_action"]
    # AIPOS-F225: 连续 BLOCK = 第二档(部署 / 验证根未变 = advisor_fix 归顾问修环境; 有变化 = 续跑 loop 自动重新派审), 原判事由留在 detail
    assert na["action"] == "advisor_fix" and na["reason"] == "audit_blocked_repeated" and na["owner_tier"] == 2, na
    assert "审计连续 BLOCK" in na["detail"] and "连续 2 次" in text


def test_item3_declarations_and_not_a_consecutive_failure():
    contract = load_loop_contract()
    decl = nr.audit_blocked_declaration()
    assert decl["verdicts"] == ["BLOCK"] and decl["owner_needed_after"] == 2
    run_decl = lrr.run_record_declaration(contract)
    rules = lrr.next_action_declaration(run_decl)
    assert {decl["end_reason"], decl["repeated_end_reason"]} <= set(run_decl["end_reasons"])
    assert rules["by_end_reason"]["audit_blocked"] == "advisor_fix" and rules["by_end_reason"]["audit_blocked_repeated"] == "owner_needed"
    view = {"task_id": "C-1", "state": "ended", "end_reason": "not_derivable", "end_message": "处理裁决 BLOCK"}
    old = lrr.next_action(view, rules, governance_root=Path("/nonexistent-f179"), earlier_end_reasons=["max_steps_exhausted"])
    _show(f"[item3] old-style ending (not_derivable after an investigate run) -> {old['action']}({old['reason']})")
    assert old["action"] == "owner_needed" and old["reason"] == "consecutive_failures"
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count("audit_block_state(workspace_root, task_id)") == 1, "one judgement point in the N4 branch"


# ===========================================================================
# item 4: charters render the new clauses
# ===========================================================================

def test_item4_charters_render_run_long_block_and_return_accuracy_clauses(rig):
    from tools.aipos_cli import charter_render as cr

    rendered = {}
    for role, instance in (("auditor", AUDITOR), ("executor", EXEC), ("advisor", DRIVER)):
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        rendered[role] = cr.render_charter(master, cr.charter_render_context(rig.gov, identity={
            "instance": instance, "project": "lybra", "role": role, "harness_root": str(rig.gov)}))
        assert "{{" not in rendered[role] and "}}" not in rendered[role]
    start = f"`lybra --workspace-root {rig.gov} run-long start --task-id <本卡卡号> --name <运行名> --cwd <本卡工作树> -- '<验证命令>'`"
    wait = f"`lybra --workspace-root {rig.gov} run-long wait --task-id <本卡卡号> --name <运行名> --timeout 300`"
    aud = rendered["auditor"]
    block = aud[aud.index("- **长验证命令一律 run-long(AIPOS-F179)**"):aud.index("- 裁决:**PASS**")]
    _show("---- item4 rendered auditor charter (new clauses) ----\n" + block)
    for needle in (start, wait, "不得因工具时限放弃、不得以子集", "单次工具时限与全量时限句以开工稿为准", '{"run_long": "<运行目录>"}',
                   "= BLOCK 并写清原因", "不计连败", "**不得写成「执行体声称通过」**", "NOT_RUN"):
        assert needle in block, needle
    exe = rendered["executor"]
    eblock = exe[exe.index("- **长验证命令用 run-long(AIPOS-F179)**"):exe.index("- 你不是审计者")]
    _show("---- item4 rendered executor charter (new clauses) ----\n" + eblock)
    assert start in eblock and wait in eblock and "绝不写成通过" in eblock
    adv = rendered["advisor"]
    line = next(ln for ln in adv.splitlines() if "**独占资源(AIPOS-F179)**" in ln)
    _show("---- item4 rendered advisor charter line ----\n" + line + "\n" + next(ln for ln in adv.splitlines() if "audit_blocked" in ln))
    assert '"resources": ["ios-simulator"]' in line and "audit_blocked" in adv

