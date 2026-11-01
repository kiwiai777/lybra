"""AIPOS-F275 — pi 执行体交回被例行门拒时有出路: loop 带修法重拉同一 pi 工位(11-01 AIPOS-F273 实撞: pi 执行工位的文档卡交回时 main 已因
并行卡合入前进, 门拒 BRANCH_WRONG_BASE; 产品对 pi 执行体没有派发修法的路径)。

件① 例行门拒声明(verbs.schema lybra_loop_status.next_action.routine_rejections)每码增「修法执行方」fixer(只追加, 缺 = fixer_default
    = 现行行为); 修法须执行体动手的码标 executor, 带 executor_fix(执行体口吻的固定修法)与 return_fields(须更新的交回报告字段)。
件② loop 在交回步遇全为 executor 的例行拒、本卡执行者是 loop 可拉起的工位(唯一拉起判定 plan_launch)= 经唯一拉起口 _launched_wait
    重拉同一工位, 开工稿 = 既有开工稿 + 返工要点(lybra_loop.launch.routine_relaunch); 等交回报告 commit_sha 改为卡分支新 tip
    (产物入口同一 tip 核对)后重试交回; 连败计数同一实现(loop_run_record.routine_streaks), 第 3 次仍拒 = owner_needed。
件③ 子 agent 执行体(loop 不拉起)维持 advisor_fix; loop status 修法首条按执行体种类分别给文案(可拉起 = 「loop 将自动重拉」/ 子 agent = 「派子 agent」)。
件④ 运行记录 routine_relaunches + loop 输出 / loop status 写明「例行拒自动重拉 第 N/3 次」、拒因码与重拉 pid。
    返工要点的说理通道 = 运行记录(routine_relaunches + 重拉开工稿), 不另起说理(F238 same_round 通道不复用, 理由见 RETURN)。

靶场 = AIPOS-F90 / F95 / F150 同一靶场(临时治理根 + 临时产品仓 + 临时 HOME + 进程内真门; 假 pi = PATH 里的 tests/fake_pi.py 薄壳,
按 FAKE_EXEC_MODE=wrong_base / wrong_base_nofix 干活, 真门交回检查⑤分支合规产生 BRANCH_WRONG_BASE)。禁真门 / 真治理根 / 真工位 / 真 pi。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
import test_aipos_f150_routine_gate_reject as f150  # noqa: E402  — BRANCH_WRONG_BASE 靶场构件
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC, _git  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f156_subagent_wait_card_owner import _declare_subagent  # noqa: E402
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from tools.aipos_cli import loop_driver as ld  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK = f95.TASK
CODE = "BRANCH_WRONG_BASE"
MARK = "## 返工要点(loop 例行拒自动重拉"


def _show(text: str) -> None:
    print(text, flush=True)


def _routine() -> dict:
    return lrr.status_contract()["next_action"]["routine_rejections"]


def _relaunch_decl() -> dict:
    return ld.routine_relaunch_declaration(load_loop_contract())


def _launch_rig(lrig, monkeypatch, mode: str) -> None:
    """产品真实 pi 模板 + 假 pi(PATH 里的夹具替身); 执行 / 审计工位落地; 假 pi 干活方式 = mode。"""
    _real_pi_template(monkeypatch)
    install_fake_pi(lrig.home / "fakebin", monkeypatch)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)
    monkeypatch.setenv("FAKE_EXEC_MODE", mode)
    monkeypatch.setenv("FAKE_MAIN_REPO", str(lrig.code_repo))


def _run(lrig, **kw):
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05,
                   max_wait=kw.pop("max_wait", 60), max_steps=kw.pop("max_steps", 40), **kw)
    return res, out.getvalue()


def _records(gov: Path) -> list[dict]:
    return [nr._read_frontmatter(p) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / TASK).glob("looprun_*.md"))]


def _status(gov: Path, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), *extra])
    return rc, out + err


def _lines(text: str, *needles: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if any(n in ln for n in needles))


# ===========================================================================
# 件① 声明: 每码 fixer; executor 码带 executor_fix / return_fields; 形不合 fail-closed
# ===========================================================================

def test_item1_fixer_declared_per_code_and_fail_closed():
    routine = _routine()
    fixers = {code: lrr.routine_fixer(routine, code) for code in routine["codes"]}
    _show("[件①] 例行码 → 修法执行方: " + json.dumps(fixers, ensure_ascii=False))
    assert fixers == {"BRANCH_WRONG_BASE": "executor", "REPO_MERGE_IN_PROGRESS": "advisor", "MERGE_CONFLICT": "executor",
                      "INGEST_CODE_FILE_IN_SLOT": "executor", "INGEST_REPORT_UNFILLED": "executor", "ARTIFACT_SLOT_CODE_FILES": "advisor"}
    assert routine["fixer_default"] == "advisor" and set(routine["fixers"]) == {"executor", "advisor", "owner"}
    assert routine["codes"][CODE]["return_fields"] == ["commit_sha", "tree_hash"]
    # 缺 fixer = fixer_default(现行行为, 只追加)
    legacy = json.loads(json.dumps(routine))
    legacy["codes"][CODE].pop("fixer")
    assert lrr.routine_fixer(legacy, CODE) == "advisor"
    decl = lrr.status_contract()
    from tools.schema_loader import SchemaLoadError

    for mutate, why in ((lambda d: d["routine_rejections"]["codes"][CODE].update(fixer="robot"), "fixer 不在值域"),
                        (lambda d: d["routine_rejections"]["codes"][CODE].pop("executor_fix"), "executor 码缺 executor_fix"),
                        (lambda d: d["routine_rejections"].update(fixer_default="nobody"), "fixer_default 不在值域"),
                        (lambda d: d["routine_rejections"]["fixer_texts"].pop("subagent"), "fixer_texts 缺 subagent")):
        broken = json.loads(json.dumps(decl))
        mutate(broken["next_action"])
        with pytest.raises(SchemaLoadError) as exc:
            lrr._check_routine_rejections(broken["next_action"]["routine_rejections"])
        _show(f"[件① fail-closed·{why}] {exc.value}")
    relaunch = _relaunch_decl()
    assert relaunch["steps"] == ["return"] and relaunch["fixer"] == "executor"
    broken = json.loads(json.dumps(load_loop_contract()))
    broken["launch"]["routine_relaunch"]["kickoff_note"] = "no placeholders"
    with pytest.raises(SchemaLoadError):
        ld.routine_relaunch_declaration(broken)


# ===========================================================================
# 件② ① 假 pi 工位: 真门 BRANCH_WRONG_BASE → loop 自动重拉一次(开工稿附返工要点)→ 假 pi 合 main 改 RETURN → 交回成功 → completed
# ===========================================================================

def test_item2_pi_branch_wrong_base_relaunched_once_then_completed(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch, "wrong_base")
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = _run(lrig)
    _show("---- 件②① lybra loop 输出原文(节选: 拉起 / 门拒 / 例行拒自动重拉 / 结果) ----\n"
          + _lines(text, "launch:", "routine_relaunch:", "run return", "exit ", "done:", "ready:"))
    assert res.exit_code == 0 and res.outcome == "completed", text
    limit = _routine()["repeat_owner_needed"]
    assert f"routine_relaunch: 例行拒自动重拉 第 1/{limit} 次: 交回被门拒 拒因码 {CODE}(修法执行方 = 执行体) → 重拉 pi 工位 {EXEC}" in text
    m = re.search(rf"routine_relaunch: 例行拒自动重拉 第 1/{limit} 次: 重拉 pid=(\d+) 结果 artifact_ready", text)
    assert m, text
    # 第一次交回真被门拒(门拒原文一行进 loop 输出 / 日志)
    gate_line = next(ln for ln in text.splitlines() if "routine_relaunch: 门拒原文 " in ln)
    _show(f"[件②①] 门拒原文行: {gate_line}")
    assert f"{CODE}: 分支 'card/{TASK}' 的基座不是当前 main" in gate_line
    # 重拉开工稿 = 工位既有开工稿 + 返工要点(拒因码 / 门原文 / 声明渲染的固定修法 / 须更新字段)
    kickoff = (lrig.log / f"{TASK}.kickoff.relaunch1").read_text(encoding="utf-8")
    base, note = kickoff.split("\n\n" + MARK, 1)
    _show("---- 件②① 重拉开工稿「返工要点」节原文 ----\n" + MARK + note)
    assert f"已认领任务卡 {TASK}。" in base and "报告落点: " in base  # 既有开工稿(工位 my-tasks --task-id 同一渲染)
    code_repo, worktree = nr.card_worktree_location(lrig.gov, TASK)
    assert f"第 1/{limit} 次" in note and f"- 拒因码: {CODE}" in note and f"{CODE}: 分支 'card/{TASK}' 的基座不是当前 main" in note
    assert f"1) 在卡工作树 {worktree} 把最新 main 合入卡分支 card/{TASK}" in note
    assert f"git checkout card/{TASK} && git merge main" in note  # 唯一渲染 board_adapter.base_sync_command
    assert f"git rev-parse card/{TASK}^{{tree}}" in note and "- 须更新的交回报告字段: commit_sha, tree_hash" in note
    assert "顾问" not in note.split("固定修法", 1)[1].split("- 须更新", 1)[0], "返工要点是执行体口吻(executor_fix), 不含顾问步骤"
    # 运行记录 routine_relaunches + 拉起两次(首拉 / 重拉)同一实例同一工位
    meta = _records(lrig.gov)[-1]
    rel = meta["routine_relaunches"]
    _show("[件②①④] 运行记录 routine_relaunches 原文: " + json.dumps(rel, ensure_ascii=False))
    assert [(r["attempt"], r["limit"], r["codes"], r["card"], r["harness"], r["instance"], r["outcome"]) for r in rel] == [
        (1, limit, [CODE], TASK, "pi", EXEC, "artifact_ready")]
    assert rel[0]["pid"] == int(m.group(1))
    execs = [l for l in meta["launches"] if l["card"] == TASK]
    assert len(execs) == 2 and execs[1]["pid"] == rel[0]["pid"] and all(l["workstation"] == str(lrig.ws_exec) for l in execs)
    # 卡分支已合入当时的 main(第一次交回时落后的那笔他 lane 提交)
    assert (lrig.code_repo / "other_lane_1.txt").is_file()
    assert "other_lane_1.txt" in _git(lrig.code_repo, "ls-tree", "--name-only", f"card/{TASK}")
    # loop status 显示重拉行(件④)
    rc, out = _status(lrig.gov)
    line = next(ln for ln in out.splitlines() if "例行拒自动重拉" in ln)
    _show(f"[件②①④] loop status 重拉行原文: {line.strip()}")
    assert rc == 0 and f"例行拒自动重拉 第 1/{limit} 次: 拒因码 {CODE}, 重拉 pid={rel[0]['pid']}, 结果 artifact_ready" in line


# ===========================================================================
# 件② ② 假 pi 不修(不合 main 只再提交)→ 重拉两次仍拒 → 第 3 次门拒不再重拉 → loop status owner_needed(routine_fix_exhausted)
# ===========================================================================

def test_item2_pi_not_fixed_third_rejection_owner_needed(lrig, monkeypatch):
    _launch_rig(lrig, monkeypatch, "wrong_base_nofix")
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = _run(lrig)
    limit = _routine()["repeat_owner_needed"]
    _show("---- 件②② lybra loop 输出原文(节选) ----\n" + _lines(text, "routine_relaunch:", "exit "))
    assert res.outcome == "gate_rejected" and res.reason == "gate_rejected" and CODE in res.message, text
    assert text.count("routine_relaunch: 例行拒自动重拉 第 ") == 2 * (limit - 1)
    assert (f"routine_relaunch: 例行拒自动重拉不适用(本卡 {CODE} 连续第 {limit}/{limit} 次仍拒, 不再重拉(loop status 判 owner_needed)), "
            "照旧以门拒结束") in text
    meta = _records(lrig.gov)[-1]
    assert [(r["attempt"], r["codes"], r["outcome"]) for r in meta["routine_relaunches"]] == [
        (i, [CODE], "artifact_ready") for i in range(1, limit)]
    assert meta["end_reason"] == "gate_rejected" and meta["end_codes"] == [CODE]
    rc, out = _status(lrig.gov, "--wait", "5")
    _show(f"---- 件②② lybra loop status --wait 5 原文 exit {rc} ----\n{out}")
    report = json.loads(_cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov), "--json"])[1])
    na = report["next_action"]
    _show("[件②②] --json next_action(action / reason / tier_from / detail)原文: "
          + json.dumps({k: na.get(k) for k in ("action", "reason", "tier_from", "detail")}, ensure_ascii=False))
    # 连败计数同一实现(运行内重拉 2 次 + 本次结束 = 连续 3 次)→ 原判 owner_needed(routine_fix_exhausted); 分档照旧(F150 同码连败同一出口:
    # AIPOS-F225 第三档 investigate 归顾问, 带升 Owner 提示)
    assert (na.get("tier_from") or na)["action"] == "owner_needed" and na["reason"] == "routine_fix_exhausted", na
    assert rc == declared_exit_code("lybra_loop_status", "investigate")
    assert "顾问下一动作: investigate(routine_fix_exhausted) — 第三档(归顾问, AIPOS-F225)" in out and "例行门拒修了仍连败" in out
    assert f"本卡 {CODE} 连续 {limit} 次" in out
    assert out.count("例行拒自动重拉 第 ") == limit - 1


# ===========================================================================
# 件③ 子 agent 执行体同场景仍 advisor_fix(派子 agent 文案); pi 存量运行(F273 形态)status 给「loop 将自动重拉」→ 原命令续跑即重拉到结案
# ===========================================================================

def test_item3_subagent_executor_still_advisor_fix(rig):
    _declare_subagent(rig.gov)
    f150._first_rejection(rig)  # 子 agent 执行体(手工等待, watch 注入干活)→ 真门 BRANCH_WRONG_BASE
    meta = f150._latest(rig.gov)
    assert not meta.get("routine_relaunches"), "子 agent 执行体 loop 不拉起 = 不重拉"
    log = Path(meta["log_path"]).read_text(encoding="utf-8")
    assert "例行拒自动重拉不适用(本卡执行者不由 loop 拉起: " in log and "子 agent" in log.split("例行拒自动重拉不适用", 1)[1].splitlines()[0]
    rc, text = f150._status(rig.gov, "--wait", "5")
    _show(f"---- 件③ 子 agent 执行体 lybra loop status --wait 5 原文 exit {rc} ----\n{text}")
    assert rc == declared_exit_code("lybra_loop_status", "advisor_fix") == 7
    assert f"顾问下一动作: advisor_fix(gate_rejected) — 例行门拒 {CODE} 第 1/3 次" in text
    head = _routine()["fixer_texts"]["subagent"].format(task_id=f150.TASK, governance_root=rig.gov)
    assert f"1) {head}" in text and "派子 agent" in head and "loop 将自动重拉" not in text
    na = json.loads(_cli(["loop", "status", "--task-id", f150.TASK, "--workspace-root", str(rig.gov), "--json"])[1])["next_action"]
    declared = _routine()["codes"][CODE]["fix"]
    assert na["fix"][0] == head and len(na["fix"]) == 1 + len(declared) and "派本卡执行体" in na["fix"][1]


def test_item3_pi_legacy_rejection_status_says_relaunch_then_resume_relaunches(lrig, monkeypatch):
    """F273 形态回放: pi 卡先在不重拉的运行里被拒 BRANCH_WRONG_BASE(--no-launch: 手工等待, watch 注入干活)→ loop status 首条 =
    「loop 可拉起的 pi 工位, 用原命令续跑 loop 将自动重拉」+ 执行体口吻修法; 原命令续跑(可拉起)→ 交回再拒 → 第 2/3 次自动重拉 → 结案。"""
    _launch_rig(lrig, monkeypatch, "wrong_base")
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)

    def watch(args, expect_ready, **_kw):
        f90._executor_work(lrig, TASK)
        f150._other_lane_lands_on_main(lrig, 1)
        return 0 if expect_ready([]) else 3

    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, watch=watch, interval=0.05, max_wait=5,
                   max_steps=20, no_launch=True)
    assert res.outcome == "gate_rejected" and CODE in res.message, out.getvalue()
    assert "例行拒自动重拉不适用(本卡执行者不由 loop 拉起: --no-launch)" in out.getvalue()
    rc, text = _status(lrig.gov, "--wait", "5")
    _show(f"---- 件③ pi 执行体(存量门拒运行)lybra loop status --wait 5 原文 exit {rc} ----\n{text}")
    limit = _routine()["repeat_owner_needed"]
    head = _routine()["fixer_texts"]["launchable"].format(harness="pi", instance=EXEC, limit=limit)
    assert rc == 7 and f"1) {head}" in text and "loop 在交回步再遇此拒将自动重拉同一工位" in head
    _code_repo, worktree = nr.card_worktree_location(lrig.gov, TASK)
    assert f"2) 在卡工作树 {worktree} 把最新 main 合入卡分支 card/{TASK}" in text and "派本卡执行体" not in text
    resume = f"lybra loop --task-id {TASK} --workspace-root {lrig.gov} --actor {DRIVER} --envelope {f95.POLICY_LAUNCH}"
    assert text.rstrip().splitlines()[-1].strip() == resume or resume in text
    # 原命令续跑(可拉起): 交回再拒 → 连败第 2 次 → 自动重拉 → 合 main → 交回成功 → 结案
    res, text = _run(lrig)
    _show("---- 件③ 原命令续跑 lybra loop 输出原文(节选) ----\n" + _lines(text, "routine_relaunch:", "exit ", "done:"))
    assert res.outcome == "completed", text
    assert f"routine_relaunch: 例行拒自动重拉 第 2/{limit} 次" in text
    assert "## 返工要点(loop 例行拒自动重拉 第 2/" in (lrig.log / f"{TASK}.kickoff.relaunch1").read_text(encoding="utf-8")


# ===========================================================================
# 件② ④ 非例行拒 / 修法执行方 = 顾问 / 混合 = 不重拉(未起进程、未写记录)
# ===========================================================================

class _NoRecorder:
    """不该被碰到的记录器: 不适用的拒因在读记录 / 拉起之前即返回 None。"""

    run_id = "never"

    def __getattr__(self, name):
        raise AssertionError(f"不适用的拒因不应触及记录器({name})")


def test_item4_non_executor_or_non_routine_not_relaunched(rig):
    f90._card(rig.gov, TASK, "claimed", harness="pi")
    result = ld.LoopResult(TASK, "running", 0, "")
    cases = {
        "非例行(CHANGES_OUT_OF_SCOPE)": 'lybra_queue_return BLOCKED: ["CHANGES_OUT_OF_SCOPE: 改动越出车道 tools/x.py"]',
        "例行但修法执行方 = 顾问(REPO_MERGE_IN_PROGRESS)": "Verdict: BLOCK\nerror_code: REPO_MERGE_IN_PROGRESS",
        "例行 + 非例行混合": 'lybra_queue_return BLOCKED: ["BRANCH_WRONG_BASE: 落后", "CHANGES_OUT_OF_SCOPE: 越界"]',
        "无码": "门拒: 连接被拒",
    }
    said: list[str] = []
    for label, gate_text in cases.items():
        got = ld._routine_relaunch(1, TASK, rig.gov, result, gate_text=gate_text, node="N2", state="claimed", steps=[],
                                   contract=load_loop_contract(), launch_decl=ld.launch_declaration(load_loop_contract()), policy=None,
                                   no_launch=False, launched=set(), watch=lambda *a, **k: 3, max_wait=1, interval=0.05,
                                   say=said.append, actor=DRIVER, recorder=_NoRecorder())
        _show(f"[件②④ {label}] _routine_relaunch → {got!r}")
        assert got is None
    assert said == [] and lrr.routine_fixer(_routine(), "REPO_MERGE_IN_PROGRESS") == "advisor"
    assert "CHANGES_OUT_OF_SCOPE" not in _routine()["codes"]
