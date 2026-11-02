"""AIPOS-F278 — pi 执行体在 finalize 步撞 MERGE_CONFLICT 时同样自动重拉(F275 例行拒重拉扩到 finalize 步), 重拉后走既有 verdict_stale
重交回 → 重审。

件① 判定: verbs.schema lybra_loop.launch.routine_relaunch.steps 追加 finalize; 适用条件与交回步同一判定(拒因码全属例行清单且每码 fixer =
    executor, 执行者是 loop 可拉起的工位实例, 唯一判定 plan_launch), 同一调用口(loop_driver._drive 一处), 禁另写一套。
件② 重拉与续推: 经唯一拉起口 _launched_wait 重拉同一工位, 开工稿返工要点按被拒步渲染(routine_relaunch.step_texts, 声明化); 开工核验放行
    (next_resolver.routine_relaunch_kickoff)覆盖 finalize 步的卡状态; 等待判据同 F275; 就绪后不直接重试 finalize, 而是重推导——裁决覆盖的
    tip 已变 = 既有 verdict_stale 重交回 → 下一轮审计, 不绕过重审。
件③ 连败计数同一实现(routine_streaks), finalize 步重拉也计入; 耗尽 = owner_needed; loop status 对 pi / 子 agent 执行体分别给文案;
    运行记录 routine_relaunches 记 step。
件④ 子 agent 执行体维持现行(advisor_fix 文案不变)。

靶场 = AIPOS-F90 / F95 / F155 / F275 同一靶场(临时治理根 + 临时产品仓 + 临时 HOME + 进程内真门; 假 pi = PATH 里的 tests/fake_pi.py 薄壳,
FAKE_EXEC_MODE=merge_conflict / merge_conflict_nofix)。finalize 步 = F90 靶场 finalize 替身(禁在靶场 push / deploy), 其合入改走产品真实
卡分支整合实现 finalize._integrate_card_branch(冲突 → 列文件 → abort → main 无半合并残留, category MERGE_CONFLICT), 输出按 lybra finalize
人读结果同形(Verdict / error_code / Message 行; F155 件①已证真实 CLI 出 `error_code: MERGE_CONFLICT`)。审计 PASS 后 main 前进改同一文件 =
替身在合入前先让「他 lane」在产品仓 main 提交同一路径的不同内容。禁真门 / 真治理根 / 真工位 / 真 pi; 不 kill 任何本卡外进程。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
import test_aipos_f150_routine_gate_reject as f150  # noqa: E402  — 子 agent 执行体靶场构件
import test_aipos_f275_routine_relaunch as f275  # noqa: E402  — F275 例行拒重拉靶场构件
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _git  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f156_subagent_wait_card_owner import _declare_subagent  # noqa: E402
from tools.aipos_cli import loop_driver as ld  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK = f95.TASK
CODE = "MERGE_CONFLICT"
MARK = f275.MARK


def _show(text: str) -> None:
    print(text, flush=True)


def _relaunch_decl() -> dict:
    return ld.routine_relaunch_declaration(load_loop_contract())


def _conflict_file(task_id: str) -> str:
    """卡与他 lane 都改的同一文件 = 假 harness 执行卡干活写的那个(tests/test_<卡>.py)。"""
    return f"tests/test_{task_id.lower().replace('-', '_')}.py"


def _finalize_with_conflicts(monkeypatch, *, conflicts: int) -> list[dict]:
    """F90 靶场 finalize 替身改造: 前 conflicts 次 finalize = 合入前先让他 lane 在产品仓 main 改同一文件(审计 PASS 后 main 前进), 再走产品真实
    卡分支整合 finalize._integrate_card_branch; 冲突 = 按 lybra finalize 人读结果同形输出 BLOCK + error_code(exit 1); 合入成功 = 原替身写
    finalization 记录。返回每次调用的 {n, blocked, category, conflict_files}。"""
    from tools.aipos_cli import finalize as fz

    original = f90._finalize_double
    calls: list[dict] = []

    def double(gov: Path, code_repo: Path, argv: list[str]) -> subprocess.CompletedProcess:
        task_id = argv[argv.index("--task-id") + 1]
        actor = argv[argv.index("--actor") + 1]
        n = len(calls) + 1
        if n <= conflicts:
            rel = _conflict_file(task_id)
            (code_repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (code_repo / rel).write_text(f"def test_other_lane_{n}():\n    assert 'other lane {n}'\n", encoding="utf-8")
            _git(code_repo, "add", rel)
            _git(code_repo, "commit", "-q", "-m", f"OTHER-{n}: merged first (same file {rel})")
        verdict_id = str((nr._read_task_records(gov, task_id).get("latest_verdict") or {}).get("verdict_id") or "")
        operations: list[str] = []
        integrate = fz._integrate_card_branch(task_id, verdict_id, code_repo, gov, False, operations)
        calls.append({"n": n, "blocked": bool(integrate.get("blocked")), "category": integrate.get("category"),
                      "conflict_files": integrate.get("conflict_files")})
        if integrate.get("blocked"):
            text = (f"\n=== Finalize Result ===\nTask: {task_id}\nActor: {actor}\nVerdict: BLOCK\nerror_code: {integrate.get('category')}\n"
                    f"\nMessage: {integrate.get('message')}\n\nOperations:\n" + "".join(f"  - {op}\n" for op in operations))
            return subprocess.CompletedProcess(argv, 1, text, "")
        return original(gov, code_repo, argv)

    monkeypatch.setattr(f90, "_finalize_double", double)
    return calls


def _run(lrig, **kw):
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05,
                   max_wait=kw.pop("max_wait", 60), max_steps=kw.pop("max_steps", 60), **kw)
    return res, out.getvalue()


def _lines(text: str, *needles: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if any(n in ln for n in needles))


def _status(gov: Path, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), *extra])
    return rc, out + err


# ===========================================================================
# 件① 声明: steps 追加 finalize; step_texts 每步一套措辞; 形不合 fail-closed; 交回步渲染与 F278 前逐字相同
# ===========================================================================

def test_item1_finalize_step_declared_wording_per_step_fail_closed():
    decl = _relaunch_decl()
    _show("[件①] routine_relaunch.steps = " + json.dumps(decl["steps"]))
    assert decl["steps"] == ["return", "finalize"] and decl["fixer"] == "executor"
    routine = f275._routine()
    assert lrr.routine_fixer(routine, CODE) == "executor" and routine["codes"][CODE]["return_fields"] == ["commit_sha", "tree_hash"]
    ret, fin = ld.routine_relaunch_step_texts(decl, "return"), ld.routine_relaunch_step_texts(decl, "finalize")
    _show("[件①] step_texts.finalize = " + json.dumps(fin, ensure_ascii=False))
    # 交回步措辞 = F278 前写死的原文(行为与输出逐字不变)
    assert (ret["rejected"], ret["say"], ret["where"], ret["then"]) == ("在交回步被门拒", "交回被门拒", "交回步", "")
    assert ret["after"] == "loop 见交回报告按上述字段更新即重试交回"
    assert "verdict_stale" in fin["after"] and "下一轮审计" in fin["after"] and "不直接重试 finalize" in fin["after"]
    assert "finalize 步" in fin["rejected"] and fin["where"] == "finalize 步" and "verdict_stale" in fin["then"]
    from tools.schema_loader import SchemaLoadError

    for mutate, why in ((lambda d: d["step_texts"].pop("finalize"), "steps 有 finalize 但 step_texts 缺"),
                        (lambda d: d["step_texts"]["finalize"].pop("after"), "step_texts.finalize 缺 after"),
                        (lambda d: d["step_texts"]["return"].update(rejected=" "), "step_texts.return.rejected 空"),
                        (lambda d: d.update(kickoff_note=d["kickoff_note"].replace("{step_rejected}", "")), "kickoff_note 缺 {step_rejected}"),
                        (lambda d: d.pop("step_texts"), "缺 step_texts")):
        broken = json.loads(json.dumps(load_loop_contract()))
        mutate(broken["launch"]["routine_relaunch"])
        with pytest.raises(SchemaLoadError) as exc:
            ld.routine_relaunch_declaration(broken)
        _show(f"[件① fail-closed·{why}] {exc.value}")
    with pytest.raises(SchemaLoadError):
        ld.routine_relaunch_step_texts(decl, "verdict")  # 不在 steps 的账务步 = 不适用(调用方不会调到)


# ===========================================================================
# 件② ① 假 pi 工位: 审计 PASS 后 main 改同一文件 → finalize MERGE_CONFLICT → loop 自动重拉 → 假 pi 合 main 解冲突改 RETURN →
#        verdict_stale 重交回 → 下一轮审计(R2)派发并 PASS → finalize 合入 → 结案
# ===========================================================================

def test_item2_finalize_merge_conflict_relaunched_then_verdict_stale_reaudit_completed(lrig, monkeypatch):
    f275._launch_rig(lrig, monkeypatch, "merge_conflict")
    calls = _finalize_with_conflicts(monkeypatch, conflicts=1)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = _run(lrig)
    _show("---- 件②① lybra loop 输出原文(节选: 账务步 / 拉起 / 例行拒自动重拉 / 结果) ----\n"
          + _lines(text, "] run ", "launch:", "routine_relaunch:", "exit ", "done:", "ready:"))
    assert res.exit_code == 0 and res.outcome == "completed", text
    limit = f275._routine()["repeat_owner_needed"]
    # finalize 第 1 次真冲突(产品真实卡分支整合: 列文件 → abort), 第 2 次(R2 PASS 后)合入成功
    _show("[件②①] finalize 替身各次结果: " + json.dumps(calls, ensure_ascii=False))
    assert [(c["blocked"], c["category"]) for c in calls] == [(True, CODE), (False, None)]
    assert calls[0]["conflict_files"] == [_conflict_file(TASK)]
    gate_line = next(ln for ln in text.splitlines() if "routine_relaunch: 门拒原文 " in ln)
    assert "合并冲突, 中止 (main 无半合并残留): 1 个冲突文件" in gate_line, gate_line
    assert (f"routine_relaunch: 例行拒自动重拉 第 1/{limit} 次: finalize 被门拒 拒因码 {CODE}(修法执行方 = 执行体) → 重拉 pi 工位 {EXEC}, "
            "开工稿附返工要点") in text
    m = re.search(rf"routine_relaunch: 例行拒自动重拉 第 1/{limit} 次: 重拉 pid=(\d+) 结果 artifact_ready", text)
    assert m, text
    # 就绪后不直接重试 finalize: 重推导 = 既有 verdict_stale 重交回 → 下一轮审计 R2 派发并 PASS → 才 finalize
    order = [ln.split("] run ", 1)[1].split(" @ ", 1) for ln in text.splitlines() if "] run " in ln]
    acts = [f"{a} @ {b.split(' (', 1)[0]} ({b.split(' (', 1)[1].split(')', 1)[0]})" for a, b in order]
    _show("[件②①] 账务步序列: " + " → ".join(acts))
    i = next(k for k, a in enumerate(acts) if a.startswith("finalize"))
    assert acts[i + 1] == f"return @ verdict_stale/claimed ({TASK})", acts
    assert acts[i + 2] == f"claim @ publish/pending ({TASK}R2)" and acts[i + 3].startswith(f"verdict @ audit_verdict/claimed ({TASK}R2)"), acts
    assert acts[i + 4].startswith(f"finalize @ audit_verdict/claimed ({TASK})") and acts.count(f"finalize @ audit_verdict/claimed ({TASK})") == 2
    # 重拉开工稿 = 工位既有开工稿 + 返工要点(按被拒步渲染: finalize 步措辞; 修法 = MERGE_CONFLICT executor_fix)
    kickoff = (lrig.log / f"{TASK}.kickoff.relaunch1").read_text(encoding="utf-8")
    base, note = kickoff.split("\n\n" + MARK, 1)
    _show("---- 件②① 重拉开工稿「返工要点」节原文 ----\n" + MARK + note)
    assert f"已认领任务卡 {TASK}。" in base and "报告落点: " in base
    wording = ld.routine_relaunch_step_texts(_relaunch_decl(), "finalize")
    _code_repo, worktree = nr.card_worktree_location(lrig.gov, TASK)
    assert f"你上一次交回{wording['rejected']}——例行拒" in note and "在交回步被门拒" not in note
    assert f"改完即停: {wording['after']}。" in note and "重试交回" not in note
    assert f"- 拒因码: {CODE}" in note and f"1) 在卡工作树 {worktree} 把最新 main 合入卡分支 card/{TASK}" in note
    assert "冲突文件见门原文" in note and f"git checkout card/{TASK} && git merge main" in note
    assert "- 须更新的交回报告字段: commit_sha, tree_hash" in note
    # 运行记录 routine_relaunches 记 step = finalize; 返工要点(fix)与开工稿同一渲染
    meta = f275._records(lrig.gov)[-1]
    rel = meta["routine_relaunches"]
    _show("[件②①③] 运行记录 routine_relaunches 原文: " + json.dumps(rel, ensure_ascii=False))
    assert [(r["attempt"], r["limit"], r["codes"], r["card"], r["step"], r["harness"], r["instance"], r["outcome"]) for r in rel] == [
        (1, limit, [CODE], TASK, "finalize", "pi", EXEC, "artifact_ready")]
    assert rel[0]["pid"] == int(m.group(1)) and rel[0]["return_fields"] == ["commit_sha", "tree_hash"]
    assert [f"{i}) {s}" for i, s in enumerate(rel[0]["fix"], 1)] == [ln for ln in note.splitlines() if re.match(r"^\d\) ", ln)]
    execs = [la for la in meta["launches"] if la["card"] == TASK]
    assert len(execs) == 2 and execs[1]["pid"] == rel[0]["pid"] and all(la["workstation"] == str(lrig.ws_exec) for la in execs)
    # 卡分支已合入当时的 main(他 lane 的冲突提交), 冲突按卡意图解(保留卡一侧); 合入后 main 含本卡合入提交
    other = _git(lrig.code_repo, "log", "--format=%H", "-1", "--grep", "OTHER-1: merged first", "main")
    assert other and _git(lrig.code_repo, "merge-base", "--is-ancestor", other, f"card/{TASK}") == ""
    assert _git(lrig.code_repo, "show", f"main:{_conflict_file(TASK)}") == "def test_ok():\n    assert True"
    # loop status 重拉行(写明被拒步)
    rc, out = _status(lrig.gov)
    line = next(ln for ln in out.splitlines() if "例行拒自动重拉" in ln)
    _show(f"[件②①③] loop status 重拉行原文: {line.strip()}")
    assert rc == 0 and (f"例行拒自动重拉 第 1/{limit} 次: 拒因码 {CODE}, 重拉 pid={rel[0]['pid']}, 结果 artifact_ready"
                        f"({wording['where']}; ") in line


# ===========================================================================
# 件③ ② 假 pi 不修(不合 main, 只在卡分支再提交并改 RETURN)→ finalize 步重拉 1 次 → verdict_stale 重交回仍落后 main(BRANCH_WRONG_BASE)
#        → 交回步重拉(同一实现)→ 第 3 次仍拒 = 不再重拉, 以门拒结束 → loop status owner_needed(routine_fix_exhausted)
# ===========================================================================

def test_item3_pi_not_fixed_third_rejection_owner_needed(lrig, monkeypatch):
    f275._launch_rig(lrig, monkeypatch, "merge_conflict_nofix")
    calls = _finalize_with_conflicts(monkeypatch, conflicts=1)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = _run(lrig)
    limit = f275._routine()["repeat_owner_needed"]
    _show("---- 件③② lybra loop 输出原文(节选: 账务步 / 例行拒自动重拉 / 结束) ----\n" + _lines(text, "] run ", "routine_relaunch:", "exit "))
    assert res.outcome == "gate_rejected" and res.reason == "gate_rejected", text
    assert [(c["blocked"], c["category"]) for c in calls] == [(True, CODE)]  # finalize 只跑过一次(就绪后不直接重试 finalize)
    assert f"例行拒自动重拉 第 1/{limit} 次: finalize 被门拒 拒因码 {CODE}" in text
    assert (f"routine_relaunch: 例行拒自动重拉不适用(本卡 BRANCH_WRONG_BASE 连续第 {limit}/{limit} 次仍拒, 不再重拉(loop status 判 owner_needed)), "
            "照旧以门拒结束") in text
    meta = f275._records(lrig.gov)[-1]
    rel = meta["routine_relaunches"]
    _show("[件③②] 运行记录 routine_relaunches(attempt / codes / step / outcome): "
          + json.dumps([(r["attempt"], r["codes"], r["step"], r["outcome"]) for r in rel], ensure_ascii=False))
    assert [(r["attempt"], r["codes"], r["step"], r["outcome"]) for r in rel] == [
        (1, [CODE], "finalize", "artifact_ready"),
        *[(i, ["BRANCH_WRONG_BASE"], "return", "artifact_ready") for i in range(1, limit)]]
    assert meta["end_reason"] == "gate_rejected" and meta["end_codes"] == ["BRANCH_WRONG_BASE"]
    rc, out = _status(lrig.gov, "--wait", "5")
    _show(f"---- 件③② lybra loop status --wait 5 原文 exit {rc} ----\n{out}")
    na = json.loads(_cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov), "--json"])[1])["next_action"]
    _show("[件③②] --json next_action(action / reason / tier_from)原文: "
          + json.dumps({k: na.get(k) for k in ("action", "reason", "tier_from")}, ensure_ascii=False))
    assert (na.get("tier_from") or na)["action"] == "owner_needed" and na["reason"] == "routine_fix_exhausted", na
    assert rc == declared_exit_code("lybra_loop_status", "investigate")
    assert f"本卡 BRANCH_WRONG_BASE 连续 {limit} 次" in out
    assert f"拒因码 {CODE}, 重拉 pid={rel[0]['pid']}, 结果 artifact_ready(finalize 步; " in out


# ===========================================================================
# 件③ ②b finalize 步重拉同样计入连败: main 每次 finalize 前都被他 lane 改同一文件(假 pi 每次都按要点解冲突)→ finalize 步 MERGE_CONFLICT
#        第 1、2 次自动重拉(每次走 verdict_stale 重交回 → R2 / R3 重审)→ 第 3 次仍拒 = 不再重拉 → owner_needed(routine_fix_exhausted)
# ===========================================================================

def test_item3_finalize_step_relaunches_counted_third_conflict_owner_needed(lrig, monkeypatch):
    f275._launch_rig(lrig, monkeypatch, "merge_conflict")
    limit = f275._routine()["repeat_owner_needed"]
    calls = _finalize_with_conflicts(monkeypatch, conflicts=limit)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = _run(lrig, max_steps=80)
    _show("---- 件③②b lybra loop 输出原文(节选) ----\n" + _lines(text, "] run ", "routine_relaunch:", "exit "))
    assert res.outcome == "gate_rejected" and CODE in res.message, text
    assert [(c["blocked"], c["category"]) for c in calls] == [(True, CODE)] * limit
    assert text.count("finalize 被门拒 拒因码 MERGE_CONFLICT") == limit - 1
    assert (f"routine_relaunch: 例行拒自动重拉不适用(本卡 {CODE} 连续第 {limit}/{limit} 次仍拒, 不再重拉(loop status 判 owner_needed)), "
            "照旧以门拒结束") in text
    audits = re.findall(rf"run claim @ publish/pending \(({re.escape(TASK)}R\d*)\)", text)
    _show(f"[件③②b] 各轮审计卡: {audits}")
    assert audits == [f"{TASK}R", f"{TASK}R2", f"{TASK}R3"]  # 每次重拉后都走重审, 不绕过
    meta = f275._records(lrig.gov)[-1]
    assert [(r["attempt"], r["codes"], r["step"]) for r in meta["routine_relaunches"]] == [(i, [CODE], "finalize") for i in range(1, limit)]
    assert meta["end_codes"] == [CODE]
    rc, out = _status(lrig.gov, "--wait", "5")
    _show(f"---- 件③②b lybra loop status --wait 5 原文 exit {rc} ----\n{out}")
    na = json.loads(_cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(lrig.gov), "--json"])[1])["next_action"]
    assert (na.get("tier_from") or na)["action"] == "owner_needed" and na["reason"] == "routine_fix_exhausted", na
    assert f"本卡 {CODE} 连续 {limit} 次" in out


# ===========================================================================
# 件④ ③ 子 agent 执行体同场景(finalize MERGE_CONFLICT)→ 不重拉 → 仍 advisor_fix(首条「派子 agent」+ 现行修法, 末条 verdict_stale 续跑)
# ===========================================================================

def test_item4_subagent_executor_finalize_conflict_still_advisor_fix(rig, monkeypatch):
    import test_aipos_f155_loop_chain_gaps as f155

    _declare_subagent(rig.gov)
    calls = _finalize_with_conflicts(monkeypatch, conflicts=1)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = f155._loop(rig, f155._watch(rig, {}, []))
    assert res.reason == "gate_rejected" and [(c["blocked"], c["category"]) for c in calls] == [(True, CODE)], f155._quiet(text)
    meta = f155._latest(rig.gov)
    assert meta["end_codes"] == [CODE] and not meta.get("routine_relaunches"), "子 agent 执行体 loop 不拉起 = 不重拉"
    na_line = next(ln for ln in text.splitlines() if "例行拒自动重拉不适用" in ln)
    _show(f"[件④③] loop 输出原文: {na_line}")
    assert "本卡执行者不由 loop 拉起: " in na_line and "子 agent" in na_line
    rc, out = _status(rig.gov, "--wait", "5")
    _show(f"---- 件④③ 子 agent 执行体 lybra loop status --wait 5 原文 exit {rc} ----\n{out}")
    assert rc == declared_exit_code("lybra_loop_status", "advisor_fix") == 7
    assert f"顾问下一动作: advisor_fix(gate_rejected) — 例行门拒 {CODE} 第 1/3 次" in out
    routine = f275._routine()
    head = routine["fixer_texts"]["subagent"].format(task_id=TASK, governance_root=rig.gov)
    assert f"1) {head}" in out and "loop 将自动重拉" not in out and "loop 在" not in out
    na = json.loads(_cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--json"])[1])["next_action"]
    assert na["action"] == "advisor_fix" and na["fix"][0] == head and len(na["fix"]) == 1 + len(routine["codes"][CODE]["fix"])
    assert "派本卡执行体" in na["fix"][1] and "verdict_stale" in na["fix"][-1]  # 现行修法原样(末条: 续跑走既有 verdict_stale 重审)


# ===========================================================================
# 件③ pi 执行体存量门拒运行(以 finalize 步 MERGE_CONFLICT 结束, 当时未重拉)→ loop status 给 pi 文案(finalize 步措辞)→ 原命令续跑即
#        finalize 步再拒 → 第 2/3 次自动重拉 → verdict_stale 重交回 → R2 → 结案
# ===========================================================================

def test_item3_pi_legacy_finalize_rejection_status_says_relaunch_then_resume_completes(lrig, monkeypatch):
    import test_aipos_f155_loop_chain_gaps as f155

    f275._launch_rig(lrig, monkeypatch, "merge_conflict")
    calls = _finalize_with_conflicts(monkeypatch, conflicts=1)
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    out = io.StringIO()
    res = run_loop(TASK, lrig.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, watch=f155._watch(lrig, {}, []), interval=0.05,
                   max_wait=5, max_steps=30, no_launch=True)
    assert res.outcome == "gate_rejected" and CODE in res.message, out.getvalue()
    assert "例行拒自动重拉不适用(本卡执行者不由 loop 拉起: --no-launch)" in out.getvalue()
    rc, text = _status(lrig.gov, "--wait", "5")
    _show(f"---- 件③ pi 执行体(finalize 步存量门拒运行)lybra loop status --wait 5 原文 exit {rc} ----\n{text}")
    limit = f275._routine()["repeat_owner_needed"]
    wording = ld.routine_relaunch_step_texts(_relaunch_decl(), "finalize")
    head = f275._routine()["fixer_texts"]["launchable"].format(harness="pi", instance=EXEC, limit=limit, step=wording["where"],
                                                              step_then=wording["then"])
    assert rc == 7 and f"1) {head}" in text and "loop 在finalize 步再遇此拒将自动重拉同一工位" in head and "verdict_stale" in head
    _code_repo, worktree = nr.card_worktree_location(lrig.gov, TASK)
    assert f"2) 在卡工作树 {worktree} 把最新 main 合入卡分支 card/{TASK}" in text and "派本卡执行体" not in text
    resume = f"lybra loop --task-id {TASK} --workspace-root {lrig.gov} --actor {DRIVER} --envelope {f95.POLICY_LAUNCH}"
    assert resume in text
    # 原命令续跑(可拉起): finalize 步再拒(卡分支仍未合入他 lane 的冲突提交)→ 连败第 2 次 → 自动重拉 → 解冲突 → 重审 → 结案
    res, text = _run(lrig)
    _show("---- 件③ 原命令续跑 lybra loop 输出原文(节选) ----\n" + _lines(text, "] run ", "routine_relaunch:", "exit ", "done:"))
    assert res.outcome == "completed", text
    assert f"routine_relaunch: 例行拒自动重拉 第 2/{limit} 次: finalize 被门拒 拒因码 {CODE}" in text
    assert [(c["blocked"], c["category"]) for c in calls] == [(True, CODE), (True, CODE), (False, None)]
    assert f"run claim @ publish/pending ({TASK}R2)" in text
    assert "## 返工要点(loop 例行拒自动重拉 第 2/" in (lrig.log / f"{TASK}.kickoff.relaunch1").read_text(encoding="utf-8")


# ===========================================================================
# 件② 单实现: 同一调用口(按声明 steps, 不写死步名)/ 同一拉起口 / 同一连败计数 / 开工核验放行唯一判定(每步只放行声明的一项)
# ===========================================================================

def test_item2_single_call_site_and_kickoff_release_single_judgment(rig):
    import inspect

    drv = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert drv.count("_routine_relaunch(") == 2  # def + _drive 一处调用(交回步与 finalize 步共用)
    assert 'action_type in routine_relaunch_declaration(contract)["steps"]' in drv
    assert 'if action_type == "return" and target_card == task_id:' not in drv  # 不再写死交回步
    body = inspect.getsource(ld._routine_relaunch)
    assert "plan_launch(" in body and "_launched_wait(" in body and "_launch_once(" not in body and "routine_streaks(" in body
    assert "routine_relaunch_step_texts(" in body and "routine_relaunch_step_texts(" in inspect.getsource(lrr._fixer_fix_lines)
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count("_routine_relaunch_release(") == 2  # def + kickoff_refusal 一处
    assert '"lybra_finalize"' not in inspect.getsource(nr._routine_relaunch_release)  # 放行动词读声明 kickoff_release
    # 放行判定: 只在 routine_relaunch_kickoff 范围内、执行体、可派生、动词 = 声明某步 verb 时, 给出该步唯一放行码
    release = _relaunch_decl()["kickoff_release"]
    fin = {"derivable": True, "verb": release["finalize"]["verb"]}
    ret = {"derivable": True, "verb": release["return"]["verb"]}
    assert nr._routine_relaunch_release(TASK, "executor", fin) is None  # 范围外
    with nr.routine_relaunch_kickoff(TASK):
        got = {"finalize": nr._routine_relaunch_release(TASK, "executor", fin), "return": nr._routine_relaunch_release(TASK, "executor", ret),
               "auditor": nr._routine_relaunch_release(TASK, "auditor", fin),
               "not_derivable": nr._routine_relaunch_release(TASK, "executor", {**fin, "derivable": False}),
               "other_card": nr._routine_relaunch_release(TASK + "X", "executor", fin),
               "other_verb": nr._routine_relaunch_release(TASK, "executor", {"derivable": True, "verb": "lybra_queue_close"})}
    _show("[件②] 开工核验放行判定: " + json.dumps(got, ensure_ascii=False))
    assert got == {"finalize": "NOT_YOUR_TURN", "return": "ARTIFACT_SUBMITTED", "auditor": None, "not_derivable": None,
                   "other_card": None, "other_verb": None}
    from tools.schema_loader import SchemaLoadError

    broken = json.loads(json.dumps(load_loop_contract()))
    broken["launch"]["routine_relaunch"]["kickoff_release"]["finalize"]["refusal"] = "CONCLUDED_TYPO"
    with pytest.raises(SchemaLoadError):
        ld.routine_relaunch_declaration(broken)
    broken["launch"]["routine_relaunch"]["kickoff_release"].pop("finalize")
    with pytest.raises(SchemaLoadError):
        ld.routine_relaunch_declaration(broken)
