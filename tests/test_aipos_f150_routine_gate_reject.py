"""AIPOS-F150 — 例行门拒交顾问自处理(gap #128): 运行记录结构化保存拒因码(件①), `loop status` 下一动作新增 advisor_fix
(件②, 例行清单与修法只在 verbs.schema lybra_loop_status.next_action.routine_rejections 一处声明), 顾问章程补守则(件③)。

靶场 = AIPOS-F90 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME, 进程内门经真产品 CLI 薄壳; 禁真门/真治理根/真工位/pi)。
BRANCH_WRONG_BASE 由真门交回检查⑤分支合规产生: 执行体在卡分支提交后, 「他 lane」在产品仓 main 上前进一笔。

验收(卡面 ★验收):
 ① 临时治理根造 BRANCH_WRONG_BASE 门拒 → 运行记录 end_codes 原文、status 下一动作 advisor_fix + 修法原文(人读与 --json);
    按修法处理后续跑 loop 推进到结案; 非例行拒因仍 owner_needed 原文; 同码连败升级 owner_needed 原文
 ② 章程渲染含新守则原文
"""
from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f131_loop_run_status import _cli, _meta, _runs  # noqa: E402  — F131 夹具读法唯一来源
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

TASK = f90.TASK
VERB = "lybra_loop_status"
ADVISOR_MASTER = REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md"


def _show(line: str) -> None:
    print(line, flush=True)


def _status(gov: Path, *extra: str) -> tuple[int, str]:
    rc, out, err = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(gov), *extra])
    return rc, out + err


def _latest(gov: Path) -> dict:
    """本卡最近一次运行记录(选取同 loop status: loop_run_record.loop_status 唯一读取口)。"""
    path = lrr.loop_status(gov, TASK)["runs"][0]["record_path"]
    return lrr.read_run(Path(path), lrr.run_record_declaration(load_loop_contract()))


def _routine() -> dict:
    return lrr.status_contract()["next_action"]["routine_rejections"]


def _other_lane_lands_on_main(rig, n: int) -> str:
    """他 lane 的卡先合入共用仓 main(产品仓 main 前进一笔)。"""
    _write(rig.code_repo / f"other_lane_{n}.txt", f"other lane {n}\n")
    _git(rig.code_repo, "add", f"other_lane_{n}.txt")
    _git(rig.code_repo, "commit", "-q", "-m", f"OTHER-{n}: merged first")
    return _git(rig.code_repo, "rev-parse", "main")


def _executor_then_main_advances(rig):
    """watch 注入: 执行体落产物(卡分支提交 + Return), 之后他 lane 合入 main → 本卡交回时分支落后(真门 BRANCH_WRONG_BASE)。"""

    def watch(args, expect_ready):
        f90._executor_work(rig)
        _other_lane_lands_on_main(rig, 1)
        return 0 if expect_ready([]) else 3

    return watch


def _loop(rig, watch=None, **kw):
    out = io.StringIO()
    res = run_loop(TASK, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch or (lambda args, expect_ready: 3),
                   interval=0.05, max_wait=kw.pop("max_wait", 5), max_steps=kw.pop("max_steps", 30))
    return res, out.getvalue()


def _first_rejection(rig):
    f90._card(rig.gov, TASK, "pending")
    init_governance_repo(rig.gov)
    res, text = _loop(rig, _executor_then_main_advances(rig))
    assert res.outcome == "gate_rejected" and res.reason == "gate_rejected", text
    assert "BRANCH_WRONG_BASE" in res.message
    return res


# ===========================================================================
# ① 件① + 件②: 真门 BRANCH_WRONG_BASE → end_codes → advisor_fix + 修法原文 → 按修法处理续跑到结案
# ===========================================================================

def test_item1_branch_wrong_base_end_codes_advisor_fix_then_resume_to_completed(rig):
    _first_rejection(rig)
    meta = _meta(rig.gov)
    record_text = _runs(rig.gov)[-1].read_text(encoding="utf-8")
    fm_lines = [ln for ln in record_text.split("---")[1].splitlines() if ln.startswith(("end_reason", "end_codes", "- BRANCH"))]
    _show("---- ① 运行记录 frontmatter(结束段)原文 ----\n" + "\n".join(fm_lines))
    assert meta["end_reason"] == "gate_rejected" and meta["end_codes"] == ["BRANCH_WRONG_BASE"], meta
    log = Path(meta["log_path"]).read_text(encoding="utf-8")
    assert "reason=gate_rejected codes=BRANCH_WRONG_BASE" in log

    rc, text = _status(rig.gov, "--wait", "5")
    _show(f"---- ① lybra loop status --wait 5(BRANCH_WRONG_BASE)原文 exit {rc} ----\n{text}")
    assert rc == declared_exit_code(VERB, "advisor_fix") == 7
    assert "拒因码: BRANCH_WRONG_BASE" in text
    assert "顾问下一动作: advisor_fix(gate_rejected) — 例行门拒 BRANCH_WRONG_BASE 第 1/3 次" in text
    code_repo, worktree = f90.nr.card_worktree_location(rig.gov, TASK)
    assert f"在卡工作树 {worktree} 把最新 main 合入卡分支 card/{TASK}" in text
    assert f"git checkout card/{TASK} && git merge main" in text  # 唯一渲染 board_adapter.base_sync_command
    assert "不 rebase" in text and "verdict_stale" in text
    resume = f"lybra loop --task-id {TASK} --workspace-root {rig.gov} --actor {DRIVER} --envelope {f90.POLICY}"
    assert text.rstrip().splitlines()[-2].strip() == resume or resume in text

    rc, js, _ = _cli(["loop", "status", "--task-id", TASK, "--workspace-root", str(rig.gov), "--json"])
    report = json.loads(js)
    na = report["next_action"]
    _show("---- ① --json next_action 原文 ----\n" + json.dumps(na, ensure_ascii=False, indent=2))
    assert rc == 0 and na["action"] == "advisor_fix" and na["codes"] == ["BRANCH_WRONG_BASE"]
    assert report["runs"][0]["end_codes"] == ["BRANCH_WRONG_BASE"]
    declared = _routine()["codes"]["BRANCH_WRONG_BASE"]["fix"]
    assert len(na["fix"]) == len(declared) and na["hint"].splitlines()[-1] == na["resume"] == resume
    assert na["hint"].splitlines()[:len(declared)] == [f"{i}) {s}" for i, s in enumerate(na["fix"], 1)]

    # 按修法处理(执行体在卡工作树合 main → 定向测试 → 交回改新 tip), 顾问用 hint 末行同一命令续跑 → loop 推进到结案
    _git(worktree, "merge", "-q", "--no-edit", "main")
    sha, tree = _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")
    _write(rig.gov / "task_cards" / TASK / "RETURN.md",
           _fm({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{TASK}", "model": f90.SELF_EXEC_MODEL},
               f"# RETURN — {TASK}\n\n## 一句话结论\n完成(已合入最新 main)。\n\n## 改动清单\n- tests\n"))
    # 续跑命令 = hint 末行原文: 经产品 CLI 解析器解析(参数可用), 再以同一组参数调 run_loop(=`lybra loop` 实现), 审计体产物由 watch 注入
    from tools.aipos_cli.aipos_cli import build_parser

    args = build_parser().parse_args(resume.split()[1:])
    assert (args.task_id, args.actor, args.envelope, args.workspace_root) == (TASK, DRIVER, f90.POLICY, str(rig.gov)), vars(args)
    out = io.StringIO()
    res = run_loop(args.task_id, Path(args.workspace_root), actor=args.actor, policy_id=args.envelope, out=out,
                   watch=f90._agent_watch(rig, []), interval=0.05, max_wait=5, max_steps=30)
    _show(f"---- ① 续跑 `{resume}` exit {res.exit_code} ----\n" + "\n".join(l for l in out.getvalue().splitlines() if not l.startswith("[ENVELOPE")))
    assert res.exit_code == exit_code_for(load_loop_contract(), "completed") == 0, out.getvalue()
    rc, text = _status(rig.gov, "--wait", "5")
    _show(f"---- ① 续跑后 status 原文 exit {rc} ----\n{text}")
    assert rc == 0 and "顾问下一动作: card_done_take_next(completed)" in text and "拒因码" not in text
    assert _latest(rig.gov)["end_codes"] is None  # 非门拒结束不落拒因码


# ===========================================================================
# ① 非例行拒因仍 owner_needed(真门 WORKTREE_CREATE_FAILED + 混入非例行码 + 抽不出码 + 旧记录)
# ===========================================================================

def test_item1_non_routine_rejection_stays_owner_needed(rig):
    not_git = rig.gov.parent / "plain-dir"
    not_git.mkdir()
    _write(rig.gov / "project.json", json.dumps({"project": "lybra", "code_repo": str(not_git), "config_version": 1}))
    f90._card(rig.gov, TASK, "pending")
    res, text = _loop(rig, max_wait=1)
    assert res.outcome == "gate_rejected" and "WORKTREE_CREATE_FAILED" in res.message, text
    meta = _meta(rig.gov)
    _show(f"[① 非例行] 运行记录 end_codes={meta['end_codes']}")
    assert meta["end_codes"] and "WORKTREE_CREATE_FAILED" in meta["end_codes"]
    assert not set(meta["end_codes"]) & set(_routine()["codes"])
    rc, text = _status(rig.gov, "--wait", "5")
    _show(f"---- ① status --wait(非例行门拒 WORKTREE_CREATE_FAILED)原文 exit {rc} ----\n{text}")
    assert rc == declared_exit_code(VERB, "owner_needed") == 5
    assert "顾问下一动作: owner_needed(gate_rejected) — 门拒: 按拒因原文裁定" in text and "advisor_fix" not in text


def test_item1_mixed_empty_and_legacy_codes_owner_needed():
    rules = lrr.next_action_declaration(lrr.run_record_declaration(load_loop_contract()))
    gov = Path("/tmp/f150-gov-nonexistent")  # 只读判定: 非例行分支不读卡面
    base = {"task_id": TASK, "run_id": "r", "state": "ended", "end_reason": "gate_rejected", "end_message": "门拒原文"}
    cases = {
        "例行 + 非例行混合": {**base, "end_codes": ["BRANCH_WRONG_BASE", "TEST_NOT_IN_RUNALL"]},
        "抽不出码": {**base, "end_codes": []},
        "旧记录无 end_codes": dict(base),
        "landing_rejected 带非例行码": {**base, "end_reason": "landing_rejected", "end_codes": ["CLAIMANT_MISMATCH"]},
    }
    for label, view in cases.items():
        na = lrr.next_action(view, rules, governance_root=gov)
        _show(f"[① {label}] {na['action']}({na['reason']}) — {na['detail']}")
        assert na["action"] == "owner_needed" and na["reason"] == view["end_reason"], (label, na)


# ===========================================================================
# ① 同码连败升级: 不修续跑, 同一例行码连续 repeat_owner_needed 次 = owner_needed(routine_fix_exhausted)
# ===========================================================================

def test_item1_same_routine_code_repeated_escalates_to_owner_needed(rig):
    _first_rejection(rig)
    limit = _routine()["repeat_owner_needed"]
    seen = [lrr.loop_status(rig.gov, TASK)["next_action"]]
    for n in range(2, limit + 1):
        _other_lane_lands_on_main(rig, n)  # 两条 lane 交替抢合入: 每次续跑前 main 又前进
        res, text = _loop(rig)
        assert res.reason == "gate_rejected" and _latest(rig.gov)["end_codes"] == ["BRANCH_WRONG_BASE"], text
        seen.append(lrr.loop_status(rig.gov, TASK)["next_action"])
    for i, na in enumerate(seen, 1):
        _show(f"[① 连败 第 {i} 次] {na['action']}({na['reason']}) codes={na.get('codes')}")
    assert [na["action"] for na in seen[:-1]] == ["advisor_fix"] * (limit - 1)
    assert [f"第 {i}/{limit} 次" in na["detail"] for i, na in enumerate(seen[:-1], 1)] == [True] * (limit - 1)
    rc, text = _status(rig.gov, "--wait", "5")
    _show(f"---- ① status --wait(同码连败 {limit} 次)原文 exit {rc} ----\n{text}")
    assert rc == declared_exit_code(VERB, "owner_needed")
    assert f"顾问下一动作: owner_needed(routine_fix_exhausted) — 例行门拒修了仍连败" in text
    assert f"本卡 BRANCH_WRONG_BASE 连续 {limit} 次" in text
    # 中间夹一次非门拒结束 = 连续性断开(只数尾段连续)
    rules = lrr.next_action_declaration(lrr.run_record_declaration(load_loop_contract()))
    view = {"task_id": TASK, "state": "ended", "end_reason": "gate_rejected", "end_message": "x", "end_codes": ["BRANCH_WRONG_BASE"],
            "driver": DRIVER, "envelope": f90.POLICY}
    broken = lrr.next_action(view, rules, governance_root=rig.gov,
                             earlier_end_codes=[["BRANCH_WRONG_BASE"]] * limit + [None])
    assert broken["action"] == "advisor_fix" and "第 1/" in broken["detail"], broken


# ===========================================================================
# 件① 抽取与声明: 只抽门侧既有码(不造码)、去重保序; 声明缺 = fail-closed
# ===========================================================================

def test_item1_extract_end_codes_and_declaration_fail_closed():
    from tools.schema_loader import SchemaLoadError

    decl = lrr.run_record_declaration(load_loop_contract())
    msg = ('门拒 @ return (X) exit 1: return 失败: lybra_queue_return BLOCKED: ["BRANCH_WRONG_BASE: 分支 ... merge-base: ab12", '
           '"TEST_NOT_IN_RUNALL: 未登记"]\n[stderr]\nlybra_queue_return BLOCKED: ["BRANCH_WRONG_BASE: 同上"]\n'
           'BLOCKED: error_code=OWNER_POLICY_REF_REQUIRED verdict=None')
    codes = lrr.extract_end_codes(msg, decl)
    _show(f"[件① 抽取] {codes}")
    assert codes == ["BRANCH_WRONG_BASE", "TEST_NOT_IN_RUNALL", "OWNER_POLICY_REF_REQUIRED"]
    assert lrr.extract_end_codes("门拒: lane 越界(无错误码)", decl) == []
    assert lrr.end_codes_reasons(decl) == ["gate_rejected", "landing_rejected"]
    broken = copy.deepcopy(decl)
    broken["end_codes"]["patterns"] = ["[A-Z_]+:"]  # 缺命名组 code
    with pytest.raises(SchemaLoadError):
        lrr.extract_end_codes(msg, broken)
    contract = copy.deepcopy(load_loop_contract())
    contract["run_record"].pop("end_codes")
    with pytest.raises(SchemaLoadError):
        lrr.run_record_declaration(contract)
    # 例行清单声明形: 缺修法 / 缺升级事由 = fail-closed(顾问拿不到下一动作, 宁拒不猜)
    for mutate in (lambda d: d["routine_rejections"]["codes"]["BRANCH_WRONG_BASE"].update(fix=[]),
                   lambda d: d["owner_reasons"].pop("routine_fix_exhausted"),
                   lambda d: d["routine_rejections"].update(repeat_owner_needed=0),
                   lambda d: d["actions"].pop("advisor_fix")):
        status = copy.deepcopy(lrr.status_contract())
        mutate(status["next_action"])
        with pytest.raises(SchemaLoadError):
            lrr.next_action_declaration(decl, status)


def test_item1_routine_codes_are_existing_gate_codes_declared_once():
    """例行码取自门侧既有错误码(不另造): 每个例行码在门侧代码里有拒因原文; 清单与修法只在 verbs.schema 一处(代码零写死)。"""
    import subprocess

    for code in _routine()["codes"]:
        hits = subprocess.run(["git", "grep", "-l", f"{code}:", "--", "tools/aipos_cli/board_adapter.py", "tools/mcp_server/"],
                              cwd=REPO_ROOT, capture_output=True, text=True).stdout.split()
        _show(f"[例行码出处] {code}: {hits}")
        assert hits, f"{code} 不是门侧既有错误码"
        src = subprocess.run(["git", "grep", "-l", code, "--", "tools/aipos_cli/loop_run_record.py", "tools/aipos_cli/loop_driver.py"],
                             cwd=REPO_ROOT, capture_output=True, text=True).stdout.split()
        assert src == [], f"例行码 {code} 写死在 {src}(清单只在 verbs.schema 声明)"


# ===========================================================================
# ② 章程渲染含新守则原文
# ===========================================================================

def test_item2_advisor_charter_renders_advisor_fix_rule(rig):
    from tools.aipos_cli import charter_render as cr

    identity = {"instance": DRIVER, "project": "lybra", "role": "advisor", "harness_root": str(rig.gov)}
    rendered = cr.render_charter(ADVISOR_MASTER.read_text(encoding="utf-8"), cr.charter_render_context(rig.gov, identity=identity))
    start = rendered.index("## 🟢 持续推进守则")
    section = rendered[start:rendered.index("\n---", start)]
    rule = section[section.index("   - `advisor_fix`"):section.index("   - `owner_needed`")]
    _show("---- ② 渲染后章程「持续推进守则」advisor_fix 条原文 ----\n" + rule)
    assert "{{" not in rendered
    for needle in ("**例行门拒, 按 hint 自处理, 不问 Owner**", "lybra_loop_status.next_action.routine_rejections", "BRANCH_WRONG_BASE",
                   "**派本卡执行体做**", "永不碰产品仓", "末行命令续跑 loop", "产品自动升为 `owner_needed`"):
        assert needle in rule, needle
    assert "**只在 `owner_needed` 时停。**" in section
