"""AIPOS-F111 — 并行审计: 自动拉起按卡号取开工提示 + tests/run-all.sh 并集合并免冲突。

靶场 = AIPOS-F95 同一靶场(F90 tmp 治理根 + tmp 产品仓 + tmp HOME + 进程内门; tmp 工位; 假 harness tests/fake_harness.py;
禁真门/真治理根/真工位/真 pi)。

验收(卡面 ★验收):
 ① 同一审计工位认领两张审计卡: 两张同时在办时 loop 各自拉起, 假 harness 收到的 kickoff 含各自卡号(逐字节 = 该卡
    `my-tasks --workstation <dir> --task-id <卡>` 的 next_card.kickoff); 两张同时拉起各自进程组、各自汇总前缀;
    已结案 / 非本实例认领 → 拒拉起退回手工, 拒因原样转述(判据 next_resolver.kickoff_refusal 唯一实现)
 ② (已退役)仓根 .gitattributes 曾为 tests/run-all.sh 声明 merge=union(F111 止血)。AIPOS-F109 件④ 起 run-all 自动发现、
    并行卡不再改清单; AIPOS-F116 件⑤(gap #83)撤 union 规则(它把旧登记块静默并回自动发现版 run-all.sh), 撤除与「不得再现
    逐卡登记块」的防护夹具见 tests/test_aipos_f116_test_hygiene.py; 本节只留 run-all 语法兜底
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源(禁第二份)
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
from test_aipos_f78_engine_agnostic import DRIVER, EXEC  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli.autonomy_policy import load_policy  # noqa: E402
from tools.aipos_cli.harness_launch import LaunchedHarness  # noqa: E402
from tools.aipos_cli.loop_driver import plan_launch, run_loop, workstation_kickoff  # noqa: E402

TA, TB = "PROBE-F111-A", "PROBE-F111-B"
TAR, TBR = f"{TA}R", f"{TB}R"
AUDITOR = f95.AUDIT_CLAIMER


def _show(line: str) -> None:
    print(line, flush=True)


def _group_gone(pgid: int) -> bool:
    """SIGKILL 后组内孙进程(已改挂 init)收尸是异步的: 有界等待(≤5s)组内无进程。"""
    import time

    for _ in range(100):
        if not f95._group_alive(pgid):
            return True
        time.sleep(0.05)
    return False


def _ws_my_tasks(ws: Path, ref: str | None = None) -> dict:
    from tools.aipos_cli.aipos_cli import main

    buf = io.StringIO()
    argv = ["my-tasks", "--workstation", str(ws), "--json"] + (["--task-id", ref] if ref else [])
    with contextlib.redirect_stdout(buf):
        assert main(argv) == 0
    return json.loads(buf.getvalue())


def _loop(r, card: str) -> tuple[object, str]:
    out = io.StringIO()
    res = run_loop(card, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    return res, out.getvalue()


def _launches(res) -> list[dict]:
    return [s.launch for s in res.steps if s.launch and s.launch.get("launched")]


# ===========================================================================
# ① 同一审计工位同时在办两张审计卡: 按卡号各取各的 kickoff
# ===========================================================================

def test_item1_same_audit_workstation_two_cards_each_launch_own_kickoff(lrig, monkeypatch):
    for card in (TA, TB):
        f90._card(lrig.gov, card, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)

    # 阶段 1: 两卡各经 loop 走到「审计卡已认领、审计体在办」; 审计体早退 → 两张审计卡同时在办(同一审计实例/同一工位)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "early")
    for card in (TA, TB):
        res, text = _loop(lrig, card)
        _show(f"---- ① 阶段1 lybra loop {card} 输出原文 ----\n{text}")
        assert res.exit_code == 3 and [l["card"] for l in _launches(res)] == [card, f"{card}R"], (res.exit_code, text)
    for audit in (TAR, TBR):
        assert (lrig.gov / "5_tasks" / "queue" / "claimed" / f"{audit.lower()}.md").is_file(), audit
    # F111 前判据的反证: 该工位 next_card 只能是其中一张(NEXT_CARD_RULE 选出), 另一张按「next_card 必须等于目标卡」会被拒拉起
    bare = _ws_my_tasks(lrig.ws_audit)
    picked = bare["next_card"]["task_id"]
    skipped = {TAR: TBR, TBR: TAR}[picked]
    _show(f"[①] 审计工位 my-tasks(不带卡号) next_card={picked} "
          f"claimed={[t['task_id'] for t in bare['tasks'] if t.get('queue_state') == 'claimed']} → F111 前 {skipped} 只能退回手工")
    assert picked in (TAR, TBR)

    # 阶段 2: 同工位同时拉起两张 —— 各自进程组、各自汇总前缀, kickoff 各含各卡号, 逐字节 = my-tasks --task-id <卡>
    monkeypatch.setenv("FAKE_AUDIT_MODE", "hang")
    decl = loop_driver.load_loop_contract()["launch"]
    lines: list[str] = []
    plans = {c: plan_launch(lrig.gov, c, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                            decl=decl, already_launched=set()) for c in (TAR, TBR)}
    for c, p in plans.items():
        assert not p.refusal and p.cwd == str(lrig.ws_audit), (c, p.refusal)
        assert p.kickoff == _ws_my_tasks(lrig.ws_audit, c)["next_card"]["kickoff"]
    running = {c: LaunchedHarness(p, decl, lines.append) for c, p in plans.items()}
    try:
        for _ in range(100):  # 有界: 等两进程各自写下收到的 kickoff(假 harness 启动即写)
            for h in running.values():
                h.pump(0.05)
            if all((lrig.log / f"{c}.pgid").is_file() and (lrig.log / f"{c}.pgid").read_text() for c in running):
                break
        pgids = {c: h.pgid for c, h in running.items()}
        alive = {c: f95._group_alive(g) for c, g in pgids.items()}
        _show(f"[①] 同时拉起: pgid={pgids} 同时存活={alive}")
        assert pgids[TAR] != pgids[TBR] and all(alive.values())
        for c in running:
            got = (lrig.log / f"{c}.kickoff").read_bytes()
            assert got == plans[c].kickoff.encode("utf-8") and f"已认领任务卡 {c}。".encode() in got
            assert int((lrig.log / f"{c}.pgid").read_text()) == pgids[c]
            assert (lrig.log / f"{c}.cwd").read_text() == str(lrig.ws_audit)
    finally:
        for h in running.values():
            h.terminate_group(1)
            h.close()
    for c, h in running.items():
        lines.append(h.counts_line())
    text = "\n".join(lines)
    _show("---- ① 同时拉起两张 汇总原文 ----\n" + text)
    for c, h in running.items():
        assert f"[pi {c} pid={h.pid}] 助手: 开工 {c}: 先读卡面" in text, text
        assert _group_gone(h.pgid), f"{c} 进程组 {h.pgid} 未清净"
    other = {TAR: TBR, TBR: TAR}
    for c in running:  # 互不串台: 本卡汇总行不含另一张卡的号
        own = [ln for ln in lines if f"[pi {c} " in ln]
        assert own and not any(other[c] in ln.split("]", 1)[1] for ln in own), own

    # 阶段 3: loop 两次拉起各取各卡 kickoff, 都走到 completed(先审非 next_card 的那张: F111 前这一步拒拉起)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "work")
    for card in (skipped[:-1], picked[:-1]):
        res, text = _loop(lrig, card)
        _show(f"---- ① 阶段3 lybra loop {card} 输出原文 ----\n{text}")
        assert res.exit_code == 0 and res.outcome == "completed", text
        launched = _launches(res)
        assert [l["card"] for l in launched] == [f"{card}R"] and launched[0]["workstation"] == str(lrig.ws_audit), launched
        assert f"kickoff = 工位 my-tasks --task-id {card}R next_card.kickoff" in text
        kickoff = (lrig.log / f"{card}R.kickoff").read_text(encoding="utf-8")
        _show(f"[①] 假 harness 收到的 {card}R kickoff(原文):\n{kickoff}")
        assert f"已认领任务卡 {card}R。" in kickoff and f"task_cards/{card}R/" in kickoff
        assert other[f"{card}R"] not in kickoff
        assert (lrig.gov / "5_tasks" / "queue" / "completed" / f"{card.lower()}.md").is_file()

    # 拒因: 已结案 / 非本实例认领 → 不拉起, 拒因原样转述(kickoff_refusal 同一判据)
    done_kickoff, done_why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), TAR)
    _show(f"[①拒因·已结案] {done_why}")
    assert done_kickoff == "" and f"{TAR} CONCLUDED: " in done_why, done_why
    mine_kickoff, mine_why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), TAR)
    _show(f"[①拒因·执行工位指向已结案审计卡] {mine_why}")
    assert mine_kickoff == "" and f"{TAR} CONCLUDED: " in mine_why, mine_why  # 已结案优先于实例核验(判据顺序同 /go)
    plan = plan_launch(lrig.gov, TAR, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                       decl=decl, already_launched=set())
    _show(f"[①拒因·plan_launch 已结案] refusal={plan.refusal!r} manual_hint={plan.manual_hint!r}")
    assert plan.refusal and "CONCLUDED" in plan.refusal and not plan.argv and "/go" in plan.manual_hint


def test_item1_refusal_relays_product_reason_not_mine_and_artifact_submitted(lrig):
    """单测拒因: 非本实例认领(NOT_MINE) / 产物已交(ARTIFACT_SUBMITTED) → workstation_kickoff 拒, 拒因含产品原文。"""
    from tools.aipos_cli.next_resolver import _ensure_worktree

    f90._card(lrig.gov, TA, "claimed", claimed_by=EXEC)
    f90.f78._claim_record(lrig.gov, TA, EXEC)
    assert _ensure_worktree(lrig.gov, TA)["ok"]
    f90._card(lrig.gov, TB, "claimed", assigned="exec.other.instance", claimed_by="exec.other.instance")
    f90.f78._claim_record(lrig.gov, TB, "exec.other.instance")
    ok, why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), TA)
    assert why == "" and f"已认领任务卡 {TA}。" in ok
    for card, code in ((TB, "NOT_MINE"), ("PROBE-F111-NOPE", "NOT_FOUND")):
        product = _ws_my_tasks(lrig.ws_exec, card)["next_card_excluded"][0]
        kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), card)
        _show(f"[①拒因·{code}] {why}")
        assert kickoff == "" and product["code"] == code and f"{card} {code}: {product['reason']}" in why, why
    # 产物已交 → ARTIFACT_SUBMITTED(防覆盖)
    _code_repo, worktree = f90.nr.card_worktree_location(lrig.gov, TA)
    f90._write(worktree / "tests" / "t.py", "x\n")
    f90._git(worktree, "add", "tests/t.py")
    f90._git(worktree, "commit", "-q", "-m", "w")
    sha, tree = f90._git(worktree, "rev-parse", "HEAD"), f90._git(worktree, "rev-parse", "HEAD^{tree}")
    f90._write(lrig.gov / "task_cards" / TA / "RETURN.md", f90.f78._return_text(TA, sha, tree))
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), TA)
    _show(f"[①拒因·ARTIFACT_SUBMITTED] {why}")
    assert kickoff == "" and f"{TA} ARTIFACT_SUBMITTED: " in why


def test_item1_single_implementation_and_no_next_card_equality_restriction():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    body = src.split("def workstation_kickoff(", 1)[1].split("\ndef ", 1)[0]
    assert '"--task-id", card' in body and "≠ 等待目标卡" not in body
    assert "render_kickoff" not in body and "kickoff_refusal(" not in body  # 渲染/核验只在产品 my-tasks 一处, 此处不另判
    assert "except Exception" not in src and "except:" not in src


# ===========================================================================
# ② (AIPOS-F109 件④ 后) tests/run-all.sh 改自动发现: 并行卡新增测试不再改清单 → 合并零冲突(靶场演示见
#    tests/test_aipos_f109_records_clock_runall.py 件④); 原「两分支各追加 run_pytest 登记块 + union 合并」靶场随 run_pytest 一行式
#    约定退役删除(删除前 git grep: run_pytest / _merge_demo / _run_registered_tail 除本夹具与原 run-all.sh 外无调用方)。
# ===========================================================================


def test_item2_superseded_by_f109_discovery_no_per_card_registration():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    from tools.aipos_cli.workspace_config import runall_directives

    assert runall_directives(runall)["discover"] is True
    assert "run_pytest" not in runall.split("# ---- 声明行", 1)[0].replace("run_pytest 约定退役", "")


def test_runall_syntax_ok():
    """本仓 tests/run-all.sh 语法检查通过(合并出坏块即此处红)。union 合并规则已由 AIPOS-F116 件⑤ 撤除(防护见 F116 夹具)。"""
    syntax = subprocess.run(["bash", "-n", str(REPO_ROOT / "tests" / "run-all.sh")], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr


def test_f111_fixture_registered_in_runall():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    from tools.aipos_cli.workspace_config import runall_unregistered  # AIPOS-F109 件④: 登记判据 = 门同一实现

    assert runall_unregistered(["tests/test_aipos_f111_parallel_audit.py"], runall)[0] == []
