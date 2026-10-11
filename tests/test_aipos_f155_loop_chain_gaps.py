"""AIPOS-F155(gap #133/#134/#135/#136) — loop 推进链补漏。

依据(10-11 lybra F151/F152 与 chris 岚图 SW3 实撞):
  ① finalize 阶段 BLOCK(合并冲突 / F135 仓级合入锁)不出码 → 运行记录 end_codes 为空 → 例行情况被误报 owner_needed;
  ② 审计 FAIL 后返工登记成功, 推导核 N4 FAIL 分支仍「需人工处理」→ loop not_derivable 反复 → 连败升 owner_needed;
  ③ F152 部署一致性把 loop 自身 finalize 的部署当外部更新 → 合入部署后在 close/N6 前 deploy_changed;
  ④ `lybra queue rework` 无独立预演; 顾问章程无「收到更新通知读完即继续」。

各件并入点(唯一实现, 禁第二份):
  件① 抽取 = loop_run_record.extract_end_codes(运行记录写 end_codes 的唯一函数, 式样读 verbs.schema end_codes.patterns);
      finalize 结果 category 经 finalize 输出 `error_code: <码>` 行出声; 例行清单只在 verbs.schema next_action.routine_rejections。
  件② 返工识别 = next_resolver.uncleared_rework_rounds(F75 原逻辑抽出) / rework_round_for_verdict; 新交回 = F112 verdict_staleness →
      _derive_verdict_stale 同一出口; loop 等待 = 执行体等待同一分支。
  件③ deploy_runtime.DeployGuard.adopt_own_deploy(F152 DeployGuard)。
  件④ 两阶段旗标 = aipos_cli._project_json_two_phase_flags(family=queue_writers); 预演 = queue_mutation.build_rework_round。

靶场: F90 进程内真门 + 临时治理根 / 临时产品仓 / 临时部署目录(F152 假快照); 无真实凭据、不连真实门、不碰真实 .deploy。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — loop 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f131_loop_run_status import _cli  # noqa: E402  — F131 夹具读法唯一来源
from test_aipos_f152_deploy_consistency import COMMIT_A, COMMIT_B, _fake_deploy, _guard, _switch, _version_text  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK = f90.TASK
VERB = "lybra_loop_status"
ADVISOR_MASTER = REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md"
FOCUS = ["修审计 FAIL 点 1: 缺边界用例", "补测 2"]
CRITERIA = ["夹具绿", "两边基线"]


def _show(text: str) -> None:
    print(text, flush=True)


def _status(gov: Path, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), *extra])
    return rc, out + err


def _status_json(gov: Path) -> dict:
    rc, out, _err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), "--json"])
    return json.loads(out)


def _latest(gov: Path) -> dict:
    path = lrr.loop_status(gov, TASK)["runs"][0]["record_path"]
    return lrr.read_run(Path(path), lrr.run_record_declaration(load_loop_contract()))


def _routine() -> dict:
    return lrr.status_contract()["next_action"]["routine_rejections"]


def _quiet(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith("[ENVELOPE"))


def _audit_id(patterns: str) -> str | None:
    """等待产物 glob 里的审计卡号(task_cards/<审计卡>/…); 执行体等待 = None。"""
    for part in patterns.replace("\\", "/").split():
        seg = part.split("/")
        if len(seg) >= 2 and seg[1].startswith(f"{TASK}R"):
            return seg[1]
    return None


def _audit_report(rig, audit_id: str, verdict: str) -> None:
    tip = _git(rig.code_repo, "rev-parse", f"card/{TASK}")
    report = rig.gov / "task_cards" / audit_id / "audit_report.md"
    _write(report, _fm({"task_id": audit_id, "reviewed_task_id": TASK, "verdict": verdict, "commit_sha": tip, "model": f90.SELF_AUDIT_MODEL},
                       f"# 审计报告 {audit_id}\n\n## 一句话结论\n{'逐条复核通过' if verdict == 'PASS' else '缺边界用例, 打回返工'}。\n\n"
                       "## 证据\n- 夹具\n"))
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL, name=f"sess-{audit_id}.jsonl")


def _rework_work(rig) -> tuple[str, str]:
    """执行体按返工节修复: 卡工作树再提交一笔 + Return frontmatter 改新 tip(正文补返工证据)。"""
    _code_repo, worktree = nr.card_worktree_location(rig.gov, TASK)
    _write(worktree / "tests" / "test_rework_edge.py", "def test_edge():\n    assert 1 + 1 == 2\n")
    _git(worktree, "add", "-A", "tests")
    _git(worktree, "commit", "-q", "-m", f"{TASK}: 返工第 1 轮")
    sha, tree = _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")
    _write(rig.gov / "task_cards" / TASK / "RETURN.md",
           _fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{TASK}", "model": f90.SELF_EXEC_MODEL},
               f"# RETURN — {TASK}\n\n## 一句话结论\n按第 1 轮返工节修复(补边界用例)。\n\n## 改动清单\n- tests\n"))
    return sha, tree


def _watch(rig, verdicts: dict[str, str], seen: list[str], executor=None):
    """watch 注入: 等审计卡 = 审计体按 verdicts[审计卡](缺省 PASS)落报告; 等执行体 = executor(缺省首交 f90._executor_work)。
    就绪判据仍是产品推导核(expect_ready)。签名接住部署快照运行时多传的 deploy_guard。"""

    def watch(args, expect_ready, deploy_guard=None):
        audit_id = _audit_id(" ".join(args.expect))
        if audit_id:
            seen.append(f"auditor:{audit_id}:{verdicts.get(audit_id, 'PASS')}")
            _audit_report(rig, audit_id, verdicts.get(audit_id, "PASS"))
        else:
            seen.append("executor")
            (executor or f90._executor_work)(rig)
        return 0 if expect_ready([]) else 3

    return watch


def _loop(rig, watch, *, guard=None, **kw):
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch, interval=0.05,
                   max_wait=kw.pop("max_wait", 5), max_steps=kw.pop("max_steps", 40), deploy_guard=guard)
    return res, out.getvalue()


def _tree_digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


# ===========================================================================
# 件① 三种门拒原文格式同一抽取 + finalize 真实输出出码
# ===========================================================================

F151_LIST_TEXT = (  # F151 运行 looprun_AIPOS-F151_20261011_052022 日志原文(列表格式, 门 queue_return BLOCKED)
    "门拒 @ return (AIPOS-F151) exit 1: return 失败: lybra_queue_return BLOCKED: [\"BRANCH_WRONG_BASE: 分支 'card/AIPOS-F151' 的基座不是当前 main。"
    "merge-base: 0daac0c2, 当前 main: 7de50085。出口: 在产品仓执行 'git checkout card/AIPOS-F151 && git merge main' 把最新 main 合入本分支"
    "(禁 rebase/stash, 声明 transitions N5.branch_integration.base_sync)。\"]")


def _range(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """临时产品仓(无 remote, 部署桩只在临时仓)+ 临时治理根(F130/F135 靶场唯一来源)。"""
    from test_aipos_f130_finalize_unlock import _gov, _product

    gov = _gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo)
    return gov, repo


def _finalize_cli(repo: Path, gov: Path, task: str) -> tuple[int, str]:
    from test_aipos_f130_finalize_unlock import EXEC

    rc, out, err = _cli(["finalize", "--task-id", task, "--actor", EXEC, "--workspace-root", str(repo), "--governance-root", str(gov),
                         "--push", "--deploy"])
    return rc, out + err


def _finalize_lock_busy_text(tmp_path: Path, monkeypatch) -> str:
    from test_aipos_f130_finalize_unlock import _card_branch, _verdict
    from tools.aipos_cli import finalize as fz

    gov, repo = _range(tmp_path / "lock", monkeypatch)
    _verdict(gov, "RNG-551", _card_branch(repo, "RNG-551"))
    with fz._repo_merge_lock(repo, "RNG-550", fz._load_branch_integration()):  # 同仓另一张卡持锁合入中
        rc, text = _finalize_cli(repo, gov, "RNG-551")
    assert rc == 1, text
    return text


def _finalize_conflict_text(tmp_path: Path, monkeypatch) -> str:
    from test_aipos_f130_finalize_unlock import _card_branch, _verdict

    gov, repo = _range(tmp_path / "conflict", monkeypatch)
    _verdict(gov, "RNG-552", _card_branch(repo, "RNG-552"))
    _write(repo / "RNG-552-0.txt", "main 上他卡先合入的同名文件\n")  # 他卡先改了同一文件
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "OTHER: merged first")
    rc, text = _finalize_cli(repo, gov, "RNG-552")
    assert rc == 1, text
    return text


def test_item1_three_raw_formats_one_extractor(tmp_path, monkeypatch):
    decl = lrr.run_record_declaration(load_loop_contract())
    lock_text = _finalize_lock_busy_text(tmp_path, monkeypatch)
    conflict_text = _finalize_conflict_text(tmp_path, monkeypatch)
    _show("---- ① finalize 真实输出(仓级合入锁被持有)原文 ----\n" + lock_text)
    _show("---- ① finalize 真实输出(合并冲突)原文 ----\n" + conflict_text)
    assert "Verdict: BLOCK\nerror_code: REPO_MERGE_IN_PROGRESS\n" in lock_text
    assert "Verdict: BLOCK\nerror_code: MERGE_CONFLICT\n" in conflict_text and "合并冲突, 中止" in conflict_text
    single = "lybra_queue_return_confirm REJECTED (门拒确认, 未落记录): BRANCH_WRONG_BASE: 分支 'card/X' 的基座不是当前 main"
    single_json = '{"verdict": "BLOCK", "error_code": "OWNER_POLICY_REF_REQUIRED", "message": "缺信封"}'
    cases = [("列表(F151 日志原文)", F151_LIST_TEXT, ["BRANCH_WRONG_BASE"]),
             ("列表(单引号 repr)", "lybra_queue_return BLOCKED: ['CHANGES_OUT_OF_SCOPE: 越界文件: x.py']", ["CHANGES_OUT_OF_SCOPE"]),
             ("单条", single, ["BRANCH_WRONG_BASE"]),
             ("单条(JSON error_code)", single_json, ["OWNER_POLICY_REF_REQUIRED"]),
             ("finalize 段(仓级合入锁)", "门拒 @ finalize (RNG-551) exit 1: finalize 失败: x\n" + lock_text, ["REPO_MERGE_IN_PROGRESS"]),
             ("finalize 段(合并冲突)", "门拒 @ finalize (RNG-552) exit 1: finalize 失败: x\n" + conflict_text, ["MERGE_CONFLICT"])]
    for name, text, want in cases:
        got = lrr.extract_end_codes(text, decl)
        _show(f"[件①·抽取 {name}] {got}")
        assert got == want, (name, got)
    # 改前(finalize 不出码)的 F151 合并冲突运行原文: 抽不出 = end_codes [](记 gap #133 的那条)
    legacy = "门拒 @ finalize (AIPOS-F151) exit 1: finalize 失败: Action: Resolve the blocking reasons listed above before retrying.\n" \
             "Verdict: BLOCK\n\nMessage: 合并冲突, 中止 (main 无半合并残留): 3 个冲突文件 — schema/verbs.schema.json"
    assert lrr.extract_end_codes(legacy, decl) == []


def _rig_finalize_then_real(monkeypatch, text: str):
    """F90 靶场 finalize 替身: 第一次 = 真实 finalize 输出(BLOCK, exit 1), 之后 = 原替身(合入 + 写 finalization 记录)。"""
    original = f90._finalize_double
    calls = []

    def double(gov, code_repo, argv):
        calls.append(list(argv))
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 1, text, "")
        return original(gov, code_repo, argv)

    monkeypatch.setattr(f90, "_finalize_double", double)
    return calls


def test_item1_repo_merge_in_progress_advisor_fix_then_rerun_completes(rig, tmp_path, monkeypatch):
    lock_text = _finalize_lock_busy_text(tmp_path, monkeypatch)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(rig.gov))
    calls = _rig_finalize_then_real(monkeypatch, lock_text)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, _watch(rig, {}, []))
    _show(f"---- ① loop(finalize 撞仓级合入锁)原文 exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.reason == "gate_rejected" and "error_code: REPO_MERGE_IN_PROGRESS" in res.message
    meta = _latest(rig.gov)
    assert meta["end_codes"] == ["REPO_MERGE_IN_PROGRESS"], meta["end_codes"]
    rc, out = _status(rig.gov, "--wait", "5")
    _show(f"---- ① lybra loop status --wait 5 原文 exit {rc} ----\n{out}")
    assert rc == declared_exit_code(VERB, "advisor_fix") == 7
    assert "拒因码: REPO_MERGE_IN_PROGRESS" in out
    assert "顾问下一动作: advisor_fix(gate_rejected) — 例行门拒 REPO_MERGE_IN_PROGRESS 第 1/3 次" in out
    na = _status_json(rig.gov)["next_action"]
    assert na["fix"] == [s.format(governance_root=str(rig.gov)) for s in _routine()["codes"]["REPO_MERGE_IN_PROGRESS"]["fix"]]
    resume = f"lybra loop --task-id {TASK} --workspace-root {rig.gov} --actor {DRIVER} --envelope {f90.POLICY}"
    assert na["resume"] == resume == na["hint"].splitlines()[-1]
    # 修法: 等持锁卡合完 → 原命令重跑 → 推导核重派同一条 finalize → 结案
    res2, text2 = _loop(rig, _watch(rig, {}, []))
    _show(f"---- ① 重跑原文 exit {res2.exit_code} ----\n{_quiet(text2)}")
    assert res2.exit_code == 0 and len(calls) == 2 and "[1] run finalize" in text2


def test_item1_merge_conflict_advisor_fix_then_reaudit_exit(rig, tmp_path, monkeypatch):
    conflict_text = _finalize_conflict_text(tmp_path, monkeypatch)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(rig.gov))
    _rig_finalize_then_real(monkeypatch, conflict_text)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, _watch(rig, {}, []))
    assert res.reason == "gate_rejected" and _latest(rig.gov)["end_codes"] == ["MERGE_CONFLICT"], _quiet(text)
    rc, out = _status(rig.gov, "--wait", "5")
    _show(f"---- ① lybra loop status --wait 5(合并冲突)原文 exit {rc} ----\n{out}")
    assert rc == 7 and "顾问下一动作: advisor_fix(gate_rejected) — 例行门拒 MERGE_CONFLICT 第 1/3 次" in out
    _code_repo, worktree = nr.card_worktree_location(rig.gov, TASK)
    assert f"在卡工作树 {worktree} 把最新 main 合入卡分支 card/{TASK}" in out and f"git checkout card/{TASK} && git merge main" in out
    assert "verdict_stale" in out
    # 按修法: 执行体合入最新 main(他卡先合入) → 更新交回 → 续跑: 走既有 verdict_stale 重交回 → 下一轮审计 R2 → 结案
    _write(rig.code_repo / "other_lane.txt", "other\n")
    _git(rig.code_repo, "add", "other_lane.txt")
    _git(rig.code_repo, "commit", "-q", "-m", "OTHER: merged first")
    _git(worktree, "merge", "-q", "--no-edit", "main")
    sha, tree = _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")
    _write(rig.gov / "task_cards" / TASK / "RETURN.md",
           _fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{TASK}", "model": f90.SELF_EXEC_MODEL},
               f"# RETURN — {TASK}\n\n## 一句话结论\n合入最新 main 解冲突。\n\n## 改动清单\n- tests\n"))
    seen: list[str] = []
    res2, text2 = _loop(rig, _watch(rig, {}, seen))
    _show(f"---- ① 续跑原文 exit {res2.exit_code} ----\n{_quiet(text2)}")
    assert res2.exit_code == 0 and seen == [f"auditor:{TASK}R2:PASS"], (seen, _quiet(text2))
    assert "verdict_stale" in text2


# ===========================================================================
# 件② 审计 FAIL → 返工登记(预演零写入 → 确认)→ loop 等返工交回 → 新交回 → R2 审计 → 结案
# ===========================================================================

def test_item2_fail_rework_loop_waits_rework_return_then_r2(rig):
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    seen: list[str] = []
    res, text = _loop(rig, _watch(rig, {f"{TASK}R": "FAIL"}, seen))
    _show(f"---- ② loop 首跑(审计 FAIL, 未登记返工)原文 exit {res.exit_code} ----\n{_quiet(text)}")
    assert seen == ["executor", f"auditor:{TASK}R:FAIL"]
    assert res.outcome == "not_derivable" and "处理裁决 FAIL" in res.message  # 未登记返工: 照旧不可推导(交顾问决定返工)
    verdict_id = str(nr._read_task_records(rig.gov, TASK)["latest_verdict"]["verdict_id"])

    base = ["queue", "rework", "--task-id", TASK, "--actor", DRIVER, "--verdict-ref", verdict_id,
            "--focus-items", json.dumps(FOCUS, ensure_ascii=False), "--acceptance-criteria", json.dumps(CRITERIA, ensure_ascii=False)]
    before = _tree_digest(rig.gov)
    f90.InProcessGate.log = []
    rc, out, err = _cli(base)
    _show(f"---- ④ lybra queue rework(缺省预演)原文 exit {rc} ----\n{out}{err}")
    assert rc == 0 and _tree_digest(rig.gov) == before, "预演零写入"
    assert f90.InProcessGate.log == [], "预演不调门动词"
    assert f"queue rework {TASK}: 预览(未写; 加 --confirm 经门写入)" in out and "将追加第 1 轮" in out
    assert f"依据裁决: {verdict_id}" in out and f"点杀: {FOCUS[0]}" in out and "验收: 夹具绿" in out
    assert "fixture-not-a-secret" not in out + err
    rc, out, err = _cli(base + ["--confirm"])
    _show(f"---- ② lybra queue rework --confirm 原文 exit {rc} ----\n{out}{err}")
    assert rc == 0, err
    card = nr._read_frontmatter(nr._find_task_in_queue(rig.gov, TASK)[0])
    assert [r["round"] for r in card["rework_rounds"]] == [1] and card["rework_rounds"][0]["verdict_ref"] == verdict_id

    d = nr.derive_next_step(TASK, rig.gov)
    _show("---- ② 推导核(返工登记后)原文 ----\n" + json.dumps({k: d.get(k) for k in ("derivable", "current_node", "triggered_by",
                                                                               "suggested_action", "notes", "action")},
                                                               ensure_ascii=False, indent=2))
    assert d["derivable"] is False and d["action"] == {"type": "await_rework", "card": TASK, "round": 1}
    assert d["triggered_by"] == "executor" and "需人工处理" not in d["notes"] and FOCUS[0] in d["suggested_action"]
    assert nr.kickoff_refusal(rig.gov, TASK, f90.EXEC, queue_state="claimed", card_frontmatter=card) is None  # 执行体可开工返工

    mid: dict = {}

    def rework(r):
        mid["status"] = _status_json(r.gov)["next_action"]  # loop 正在等返工交回时的顾问下一动作
        mid["text"] = _status(r.gov)[1]
        _rework_work(r)

    seen2: list[str] = []
    res2, text2 = _loop(rig, _watch(rig, {}, seen2, executor=rework))
    _show(f"---- ② loop 续跑(等返工交回 → 重交 → R2)原文 exit {res2.exit_code} ----\n{_quiet(text2)}")
    _show(f"---- ② loop 等返工交回时 lybra loop status 原文 ----\n{mid['text']}")
    assert res2.exit_code == 0, _quiet(text2)
    assert seen2 == ["executor", f"auditor:{TASK}R2:PASS"]
    assert mid["status"]["action"] == "continue_wait" and mid["status"]["reason"] == "manual_kickoff"
    assert "返工: 执行体按第 1 轮返工节修复后重新交回" in mid["status"]["detail"] and FOCUS[0] in mid["status"]["detail"]
    assert "验收: 夹具绿; 两边基线" in mid["status"]["detail"] and "\n" not in mid["status"]["detail"]  # 等待提示单行
    assert "not_derivable" not in text2 and "需人工处理" not in text2
    assert f"run return @ verdict_stale/claimed ({TASK})" in text2  # 新交回走 F112 verdict_stale 同一出口
    recs = nr._read_task_records(rig.gov, TASK)
    assert recs["audit_round_ids"][:2] == [f"{TASK}R", f"{TASK}R2"] and recs["latest_verdict"]["verdict"] == "PASS"
    assert recs["latest_verdict"]["audit_task_id"] == f"{TASK}R2"


def test_item2_rework_round_matching_and_single_implementation():
    verdict = {"verdict_id": "verdict_X_20261011_010000_audit", "audit_task_id": "X-1R", "verdict_at": "2026-10-11T01:00:00Z"}
    old = {"round": 1, "verdict_ref": "verdict_X_20261010_old", "created_at": "2026-10-10T00:00:00Z"}
    by_id = {"round": 2, "verdict_ref": verdict["verdict_id"], "created_at": "2026-10-09T00:00:00Z"}
    by_audit = {"round": 2, "verdict_ref": "X-1R 的 FAIL", "created_at": "2026-10-09T00:00:00Z"}
    later = {"round": 2, "verdict_ref": "见审计报告", "created_at": "2026-10-11T02:00:00Z"}
    cleared = {**later, "cleared_at": "2026-10-11T03:00:00Z"}
    assert nr.rework_round_for_verdict({"rework_rounds": [old]}, verdict) is None  # 上一轮 FAIL 的旧返工节不算
    for rnd in (by_id, by_audit, later):
        assert nr.rework_round_for_verdict({"rework_rounds": [old, rnd]}, verdict) == rnd
    assert nr.rework_round_for_verdict({"rework_rounds": [old, cleared]}, verdict) is None
    assert nr.rework_round_for_verdict({"rework_rounds": [{"round": 2, "verdict_ref": "X-1R2", "created_at": ""}]}, verdict) is None
    assert nr.uncleared_rework_rounds({"rework_rounds": "坏"}) == []
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count('r.get("cleared_at")') == 1, "返工识别只在 uncleared_rework_rounds 一处"
    assert src.count('"notes": f"N4: 裁决 {verdict_result},需人工处理"') == 1  # 未登记返工的非 PASS 裁决照旧


# ===========================================================================
# 件③ 本 loop 自身 finalize 的部署不打断; 外部部署仍 deploy_changed
# ===========================================================================

def _deploying_finalize(monkeypatch, deploy: Path, *, own: bool):
    """finalize 替身: 原替身合入 + 写记录后, 部署 = own 时把 current 切到合入提交的新快照(本 loop 的部署), 否则切到无关提交 r-b(他人部署)。"""
    original = f90._finalize_double

    def double(gov, code_repo, argv):
        done = original(gov, code_repo, argv)
        if own:
            merge = _git(code_repo, "rev-parse", "main")
            rel = deploy / "releases" / "r-own"
            rel.mkdir(parents=True)
            (rel / "VERSION").write_text(_version_text(merge, "r-own"), encoding="utf-8")
            _switch(deploy, "r-own")
        else:
            _switch(deploy, "r-b")
        return done

    monkeypatch.setattr(f90, "_finalize_double", double)


def test_item3_own_finalize_deploy_continues_to_close_and_n6(rig, tmp_path, monkeypatch):
    deploy = _fake_deploy(tmp_path / "product")
    _deploying_finalize(monkeypatch, deploy, own=True)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, _watch(rig, {}, []), guard=_guard(deploy, "r-a"))
    _show(f"---- ③ loop(本 loop finalize 自身部署)原文 exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "completed") == 0, _quiet(text)
    merge = _git(rig.code_repo, "rev-parse", "main")
    assert f"本 loop finalize 已部署 commit {merge[:12]}(本卡合入提交): 部署比对基线 {COMMIT_A[:12]} → {merge[:12]}" in text
    assert "run close" in text and "run governance_commit" in text and "deploy_changed" not in res.reason
    meta = _latest(rig.gov)
    _show("---- ③ 运行记录 deploy 段原文 ----\n" + json.dumps(meta["deploy"], ensure_ascii=False, indent=2))
    assert meta["deploy"]["commit"] == COMMIT_A and meta["deploy"]["self_deployed_to"] == merge and "changed_to" not in meta["deploy"]
    rc, out = _status(rig.gov)
    _show(f"---- ③ lybra loop status 原文 exit {rc} ----\n{out}")
    assert f"部署: 本 loop finalize 自身部署 commit {merge}" in out and "card_done_take_next(completed)" in out


def test_item3_external_deploy_during_finalize_still_deploy_changed(rig, tmp_path, monkeypatch):
    deploy = _fake_deploy(tmp_path / "product")
    _deploying_finalize(monkeypatch, deploy, own=False)
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, _watch(rig, {}, []), guard=_guard(deploy, "r-a"))
    _show(f"---- ③ loop(finalize 期间他人部署 r-b)原文 exit {res.exit_code} ----\n{_quiet(text)}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "deploy_changed") == 6
    assert res.message.startswith(f"部署已更新(本进程运行快照 commit {COMMIT_A[:12]} → 现部署 commit {COMMIT_B[:12]})")
    assert "run finalize" in text and "run close" not in text and "本 loop finalize 已部署" not in text
    assert "self_deployed_to" not in _latest(rig.gov)["deploy"]


def test_item3_adopt_own_deploy_unit(tmp_path):
    deploy = _fake_deploy(tmp_path / "p")
    guard = _guard(deploy, "r-a")
    assert guard.adopt_own_deploy(COMMIT_B) is None and guard.baseline == COMMIT_A  # current 不是该提交 = 不采纳
    _switch(deploy, "r-b")
    adopted = guard.adopt_own_deploy(COMMIT_B)
    assert adopted["from"] == COMMIT_A and adopted["to"] == COMMIT_B and guard.check() is None
    _switch(deploy, "r-a")
    assert guard.check()["to"] == COMMIT_A  # 采纳后他人再部署仍检出
    assert guard.adopt_own_deploy("") is None


# ===========================================================================
# 件④ 章程渲染含新守则; 例行清单只在 verbs.schema
# ===========================================================================

def test_item4_charter_renders_continue_after_update_and_rework(rig):
    from tools.aipos_cli import charter_render as cr

    identity = {"instance": DRIVER, "project": "lybra", "role": "advisor", "harness_root": str(rig.gov)}
    rendered = cr.render_charter(ADVISOR_MASTER.read_text(encoding="utf-8"), cr.charter_render_context(rig.gov, identity=identity))
    start = rendered.index("## 🟢 持续推进守则")
    section = rendered[start:rendered.index("\n---", start)]
    update = section[section.index("**收到 Lybra 更新通知**"):]
    rework = section[section.index("**审计 FAIL 后**"):section.index("**收到 Lybra 更新通知**")]
    _show("---- ④ 渲染后章程「持续推进守则」新增两条原文 ----\n" + rework + update)
    assert "{{" not in rendered
    assert "**直接继续推进**, 不等 Owner 再说「推进」" in update and "重取本章程" in update and "重跑\n在途卡的 loop" in update
    assert "lybra queue rework" in rework and "--confirm" in rework and "continue_wait" in rework
    assert "MERGE_CONFLICT" in section and "REPO_MERGE_IN_PROGRESS" in section
    skill = (REPO_ROOT / "agents" / "skills" / "advisor-commands" / "SKILL.md").read_text(encoding="utf-8")
    assert "`advisor_fix`(例行情况, 附修法与续跑命令)" in skill and "**`advisor_fix` 用法**" in skill
    assert "必须带 `--confirm`" not in skill and "缺省预演, `--confirm` 才写" in skill


def test_item4_rework_flags_from_two_phase_declaration_and_codes_declared_once():
    from tools.aipos_cli.aipos_cli import build_parser

    parser = build_parser()
    common = ["queue", "rework", "--task-id", "T", "--actor", "a", "--verdict-ref", "v", "--focus-items", "[]", "--acceptance-criteria", "[]"]
    assert parser.parse_args(common).confirm is False and parser.parse_args(common + ["--confirm"]).confirm is True
    with pytest.raises(SystemExit):
        with contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(common + ["--confirm", "--dry-run"])
    from tools.schema_loader import load_schema

    fam = load_schema("verbs")["two_phase_protocol"]["queue_writers"]
    assert fam["commands"] == ["rework"] and "flags" not in fam and "default_phase" not in fam
    codes = _routine()["codes"]
    assert list(codes)[:3] == ["BRANCH_WRONG_BASE", "REPO_MERGE_IN_PROGRESS", "MERGE_CONFLICT"]  # 只追加
    for code in ("REPO_MERGE_IN_PROGRESS", "MERGE_CONFLICT"):
        hits = subprocess.run(["git", "grep", "-l", code, "--", "tools/aipos_cli/loop_run_record.py", "tools/aipos_cli/loop_driver.py",
                               "tools/aipos_cli/next_resolver.py"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.split()
        assert hits == [], f"例行码 {code} 写死在 {hits}"
