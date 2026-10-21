"""AIPOS-F204 — 执行体交回格式错的自动修正(10-21 chris 岚图 LANTU-VALID1 实撞)。

实撞: validation 卡(固定候选, 来源卡 LANTU-UI5)的 pi 开工提示给出「commit_sha / tree_hash / branch = 产品给值照抄」, pi 却写成只写治理根卡
的 outputs 契约; loop 推导核 artifact_invalid → exit 4, 拒因补法却写「卡分支 card/LANTU-VALID1 tip」(验证卡不建卡分支, 错);
pi 拉起的执行体无出口, 只能停线。

件① 拒因: 缺键提示唯一实现 artifact_ingest.return_header_problem(声明 transitions artifact_ingest.return.header_fix)——补法 = 本卡开工提示
    同一契约(候选主体卡给产品值原文, 照抄即可), 误写 outputs 指出「本卡为验证卡, 不用 outputs 契约」; 治理根卡误写三键对称提示 outputs 契约;
    code 卡缺键提示逐字不变。
件② 自动修正: loop 遇 pi 拉起执行体的交回因可纠正头错误被拒 → 同会话续接执行体一次只修 RETURN frontmatter(指令 verbs.schema
    lybra_loop.launch.report_fix.executor, 给出正确值), 修后原步骤续跑; 仍错 → return_report_form_error(advisor_fix, 下一条给修头指令);
    会话不可续接(子 agent / 无会话记录)= 同一结束原因, 结束原文与下一条附修头指令(顾问派修)。运行记录 report_fixes role=executor。
件③ 恢复: 已停在 artifact_invalid 的卡(F204 前的运行)loop status 复核本卡此刻交回 = advisor_fix(原命令重跑); 原命令重跑 loop 即同会话修头。

靶场 = AIPOS-F90/F95/F171B/F196 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 假 pi tests/fake_pi.py + 假 harness
tests/fake_harness.py 模式 validate_outputs_once / validate_outputs; 本机模拟目标主机)。禁真门 / 真治理根 / 真工位 / 真 chris / 真 pi。
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

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 进程内门 + loop 全链靶场
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from test_aipos_f196_validation_to_pi import (  # noqa: E402,F401  — F196 靶场(执行方式声明 + 假 ssh + 模拟目标主机)
    ALIAS, HOST, OWNER_REF, POLICY_V, SRC, VAL, _declare_vr, _real_finalize, _validation_card, _vr, vrig,
)
from test_aipos_f197_noncode_pi_completion import _gov_card  # noqa: E402
from tools.aipos_cli import loop_driver as ld  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.artifact_ingest import header_fix_declaration, return_header_problem, validate_task_artifact  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, report_fix_declaration, run_loop  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _records(gov: Path, card: str) -> list[dict]:
    return [nr._read_frontmatter(p) for p in sorted((gov / "5_tasks" / "records" / "loop_runs" / card).glob("looprun_*.md"))]


def _fixes(gov: Path, card: str) -> list[dict]:
    return [f for r in _records(gov, card) for f in r.get("report_fixes") or []]


def _return_path(gov: Path, card: str) -> Path:
    root, patterns = nr.executor_artifact_watch(gov, card)
    return root / patterns[0]


def _tree(r) -> str:
    return f78._git(r.code_repo, "rev-parse", f"{r.candidate}^{{tree}}")


def _prepare_validation(r, monkeypatch, *, session: bool = True) -> None:
    """F196 validation 卡全链前置(验证根声明 + run-start)+ 产品真实 pi 模板(含 session 声明)与假 pi(同会话续接)。"""
    monkeypatch.setattr(f90, "_finalize_double", _real_finalize)
    if session:
        _real_pi_template(monkeypatch)
        install_fake_pi(r.home / "fakepi", monkeypatch)
    _validation_card(r)
    init_governance_repo(r.gov)
    assert _vr(r.gov, "set-host", "--host", HOST, "--ssh-alias", ALIAS, "--owner-ref", OWNER_REF, "--actor", DRIVER, "--confirm")[0] == 0
    decl = _declare_vr(r)
    assert _vr(r.gov, "run-start", "--id", decl, "--candidate", r.candidate, "--actor", DRIVER, "--confirm")[0] == 0
    monkeypatch.setenv("FAKE_AUDIT_MODE", "work")


def _loop(r, **kw):
    out = io.StringIO()
    res = run_loop(VAL, r.gov, actor=DRIVER, policy_id=f"{POLICY_V}_validation", out=out, interval=0.05,
                   max_wait=kw.pop("max_wait", 90), max_steps=kw.pop("max_steps", 40), **kw)
    return res, out.getvalue()


def _excerpt(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if re.search(r"launch:|report_fix:|交回被拒 @|ready:|exit \d|done:|run (return|claim|verdict)", ln))


def _product_fix(r) -> str:
    """件① 期望补法原文(产品给值; 逐键说明 = 开工提示同一契约 report_frontmatter_contract(candidate=True))。"""
    values = nr.candidate_subject_values(r.gov, VAL, nr._read_frontmatter(nr._find_task_in_queue(r.gov, VAL)[0]))
    entries = nr.report_frontmatter_contract("return", branch_task_id=SRC, subject=values, candidate=True)
    return "; ".join(f"{e['key']} = {e['value']}({e['hint']})" for e in entries)


# ===========================================================================
# 件① + 件②: validation 卡 pi 写 outputs 契约 → 拒因含产品值 → 同会话续接修头 → 入门 → 派审 → 结案
# ===========================================================================

def test_item1_item2_validation_outputs_contract_resumed_same_session_to_completed(vrig, monkeypatch):
    _prepare_validation(vrig, monkeypatch)
    monkeypatch.setenv("FAKE_EXEC_MODE", "validate_outputs_once")
    res, text = _loop(vrig)
    _show("---- 件①② validation 卡 outputs 契约 → 同会话修头 → completed: lybra loop 输出(节选)----\n" + _excerpt(text))
    assert res.exit_code == 0 and res.outcome == "completed", text
    tree = _tree(vrig)
    reject = next(ln for ln in text.splitlines() if "交回被拒 @" in ln)
    fix = _product_fix(vrig)
    _show(f"[件① 拒因原文] {reject}")
    assert f"交回被拒 @ claim/claimed ({VAL}) INGEST_FRONTMATTER_MISSING:" in reject
    assert f"补法: {fix}。本卡为验证卡(交付主体 = 来源卡 {SRC} 的固定候选, 不建卡分支): 交回用 commit_sha / tree_hash / branch 三键" in reject
    assert "不用 outputs 契约(那是只写治理根卡的产出清单契约), 删去 outputs 键" in reject
    assert f"commit_sha = {vrig.candidate}(" in fix and f"tree_hash = {tree}(" in fix and f"branch = card/{SRC}(" in fix
    assert f"卡分支 card/{VAL}" not in reject, "验证卡不建卡分支, 拒因不得给本卡卡分支补法"
    first = json.loads((vrig.log / f"{VAL}.argv.json").read_text(encoding="utf-8"))
    second = json.loads((vrig.log / f"{VAL}.argv.resume1.json").read_text(encoding="utf-8"))
    sid = first[first.index("--session-id") + 1]
    assert second == [("<resume>" if a == "<kickoff>" else a) for a in first], "同一 argv 模板与会话 id, 只换指令"
    fix_line = next(ln for ln in text.splitlines() if "report_fix:" in ln)
    _show(f"[件② report_fix 行] {fix_line}")
    assert fix_line.endswith(f"→ 同会话 {sid} 续接修报告头 1/1") and f"执行卡 {VAL} 交回报告头不合本卡交回契约(缺 commit_sha, tree_hash, branch;" in fix_line
    prompt = (vrig.log / f"{VAL}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[件② 续接修头指令原文] {prompt}")
    expected = report_fix_declaration(load_loop_contract(), "executor")["prompt"]
    problem = (f"缺 commit_sha, tree_hash, branch; 本卡为验证卡(交付主体 = 来源卡 {SRC} 的固定候选, 不建卡分支): 交回用 commit_sha / "
               "tree_hash / branch 三键(产品给值, 照抄), 不用 outputs 契约(那是只写治理根卡的产出清单契约), 删去 outputs 键")
    for key, value in (("artifact", str(_return_path(vrig.gov, VAL))), ("problem", problem), ("fix", fix)):
        expected = expected.replace("{" + key + "}", value)
    assert prompt == expected
    ret = _return_path(vrig.gov, VAL).read_text(encoding="utf-8")
    fm = nr._read_frontmatter(_return_path(vrig.gov, VAL))
    _show(f"[件② 修后 RETURN frontmatter] {fm}")
    assert (fm["commit_sha"], fm["tree_hash"], fm["branch"]) == (vrig.candidate, tree, f"card/{SRC}") and "outputs" not in fm
    assert "固定候选验证完成(假 harness)" in ret, "只修报告头, 正文不动"
    fixes = _fixes(vrig.gov, VAL)
    _show("[件② 运行记录 report_fixes] " + json.dumps([{k: f[k] for k in ("card", "role", "outcome", "kinds", "session_id")} for f in fixes],
                                                      ensure_ascii=False))
    assert [(f["card"], f["role"], f["outcome"], f["kinds"], f["session_id"]) for f in fixes] == [
        (VAL, "executor", "resumed", ["missing", "contract_misuse"], sid)]
    dispatch = next(ln for ln in text.splitlines() if f"run claim @ publish/pending ({VAL}R)" in ln)  # 交回入门即派审(门侧派生审计卡)
    ingest = [ln for ln in text.splitlines() if re.match(r"\[4\] (run return|ok)", ln)]
    _show("[件② 修后入门 → 派审原文]\n  " + "\n  ".join(ln[:200] for ln in [*ingest, dispatch]))
    assert ingest[-1] == "[4] ok: return 成功" and nr._find_task_in_queue(vrig.gov, f"{VAL}R")[0] is not None
    vfm = nr._read_frontmatter(f90._records(vrig.gov, "audit_verdicts", VAL)[-1])
    assert vfm.get("verdict") == "PASS" and (vfm.get("artifact_subject") or {}).get("commit_sha") == vrig.candidate


def test_item2_still_wrong_after_resume_ends_advisor_fix(vrig, monkeypatch):
    _prepare_validation(vrig, monkeypatch)
    monkeypatch.setenv("FAKE_EXEC_MODE", "validate_outputs")  # 续接后改写了报告头但仍不合契约
    res, text = _loop(vrig)
    _show("---- 件② 修后仍错: lybra loop 输出(节选)----\n" + _excerpt(text) + "\n---- 结果原文 ----\n" + res.message)
    assert res.reason == "return_report_form_error" and res.exit_code == exit_code_for(load_loop_contract(), "not_derivable"), text
    assert res.message.startswith(f"交回报告头不合契约, 本运行已续接执行会话修报告头 1/1 次仍被拒: 执行卡 {VAL} 交回报告头不合本卡交回契约(")
    assert f"修头指令(会话不可续接时顾问派给本卡执行实例): 你的交回报告 {_return_path(vrig.gov, VAL)}" in res.message
    assert [f["outcome"] for f in _fixes(vrig.gov, VAL)] == ["resumed", "exhausted"]
    status = lrr.loop_status(vrig.gov, VAL)
    na = status["next_action"]
    _show("---- 件② 修后仍错: loop status 下一动作 ----\n" + json.dumps(na, ensure_ascii=False, indent=1))
    assert na["action"] == "advisor_fix" and na["reason"] == "return_report_form_error"
    assert f"lybra artifact ingest --task-id {VAL} --kind return --workspace-root {vrig.gov} --dry-run" in na["hint"]
    assert f"commit_sha = {vrig.candidate}(" in na["hint"] and f"branch = card/{SRC}(" in na["hint"], "下一条给手工补法(产品给值)"
    assert na["hint"].splitlines()[-1].startswith(f"lybra loop --task-id {VAL} --workspace-root {vrig.gov}")


def test_item2_no_session_ends_advisor_fix_with_fix_instruction(vrig, monkeypatch):
    # F95 靶场模板无 session 声明 = 拉起无固定会话 id = 不可续接(子 agent / 手工工位同此出口)
    _prepare_validation(vrig, monkeypatch, session=False)
    monkeypatch.setenv("FAKE_EXEC_MODE", "validate_outputs_once")
    res, text = _loop(vrig)
    _show("---- 件② 会话不可续接: lybra loop 结果原文 ----\n" + res.message)
    assert res.reason == "return_report_form_error", text
    assert res.message.startswith("交回报告头不合契约, 执行会话不可续接(本卡运行记录无该执行卡的固定会话 id): ")
    assert f"commit_sha = {vrig.candidate}(" in res.message.splitlines()[-1]
    assert [(f["role"], f["outcome"]) for f in _fixes(vrig.gov, VAL)] == [("executor", "not_resumable")]
    na = lrr.loop_status(vrig.gov, VAL)["next_action"]
    _show("---- 件② 会话不可续接: loop status 下一动作 hint ----\n" + na["hint"])
    assert na["action"] == "advisor_fix" and "执行会话不可续接(子 agent / 手工工位 / 无会话记录)= 顾问把下面的修头指令原文派给本卡执行实例" in na["hint"]
    assert "你的交回报告 " in na["hint"] and f"tree_hash = {_tree(vrig)}(" in na["hint"]


# ===========================================================================
# 件③: 已停在 artifact_invalid 的卡(F204 前的形)— loop status = advisor_fix; 原命令重跑 loop 即同会话修头并走完
# ===========================================================================

def test_item3_stuck_artifact_invalid_status_advisor_fix_then_rerun_recovers(vrig, monkeypatch):
    _prepare_validation(vrig, monkeypatch)
    monkeypatch.setenv("FAKE_EXEC_MODE", "validate_outputs_once")
    with pytest.MonkeyPatch.context() as legacy:
        legacy.setattr(ld, "_report_fix", lambda *a, **k: None)  # 模拟 F204 前的部署: 报告头错误即 artifact_invalid 停线
        res, text = _loop(vrig)
    _show("---- 件③ F204 前形(LANTU-VALID1 同形): lybra loop 结果原文 ----\n" + res.message)
    assert res.exit_code == 4 and res.reason == "artifact_invalid", text
    assert f"补法: commit_sha = {vrig.candidate}(" in res.message and f"卡分支 card/{VAL}" not in res.message, "件① 新口径拒因"
    na = lrr.loop_status(vrig.gov, VAL)["next_action"]
    _show("---- 件③ loop status 下一动作(停在 artifact_invalid 的运行)----\n" + json.dumps(na, ensure_ascii=False, indent=1))
    assert na["action"] == "advisor_fix" and na["reason"] == "return_report_form_error" and na["end_reason"] == "artifact_invalid"
    assert "复核本卡此刻交回 = 报告头可纠正, AIPOS-F204" in na["detail"]
    again, again_text = _loop(vrig)
    _show("---- 件③ 原命令重跑 loop(节选)----\n" + _excerpt(again_text))
    assert again.exit_code == 0 and again.outcome == "completed", again_text
    launches = [la for r in _records(vrig.gov, VAL) for la in r.get("launches") or [] if la["card"] == VAL]
    _show(f"[件③ 执行卡 launches(会话, 续接序, 结果)] {[(la['session_id'], la['resume'], la['outcome']) for la in launches]}")
    assert len({la["session_id"] for la in launches}) == 1 and [la["resume"] for la in launches] == [0, 1]
    assert [(f["role"], f["outcome"]) for f in _fixes(vrig.gov, VAL)] == [("executor", "resumed")]


# ===========================================================================
# 件①: 治理根卡误写三键对称提示; code 卡缺键提示逐字不变; code 卡 commit_sha 形式错可纠正(补 tip)
# ===========================================================================

def test_item1_governance_card_branch_keys_symmetric_hint(rig):
    gov = rig.gov
    card = "PROBE-F204-G1"
    _gov_card(gov, card, "claimed")
    f78._claim_record(gov, card, EXEC)
    ret = gov / "task_cards" / card / "RETURN.md"
    _write(ret, f78._fm({"commit_sha": "a" * 40, "tree_hash": "b" * 40, "branch": f"card/{card}"},
                        f"# RETURN — {card}\n\n## 一句话结论\n文档写好了。\n"))
    d = nr.derive_next_step(card, gov)
    check = validate_task_artifact(card, gov)
    _show(f"[件① 治理根卡误写三键: 推导核拒因] {d['suggested_action']}\n[件① 同卡 ingest 拒因] {check['reasons'][0]}")
    note = ("本卡只写治理根(不建卡工作树 / 卡分支): 交回用 outputs 产出清单契约, 不用 commit_sha / tree_hash / branch"
            "(那是写产品仓卡的分支三键契约), 删去这些键")
    assert d["action"]["type"] == "artifact_invalid" and d["suggested_action"].endswith(f"。{note}")
    assert "补法: outputs = 本卡产出文件清单(YAML 列表" in d["suggested_action"] and note in check["reasons"][0]
    problem = check["header_problem"]
    assert problem["contract"] == "governance" and problem["kinds"] == ["missing", "contract_misuse"] and problem["correctable"]
    assert problem["misused"] == ["commit_sha", "tree_hash", "branch"]


def test_item1_code_card_missing_hint_unchanged_and_form_error_correctable(tmp_path, monkeypatch):
    task_id = "F204-CODE"
    gov, code_repo, sha, tree, ret = f78._ingest_setup(tmp_path, monkeypatch, "chris", task_id)  # 人肉期 chris 形临时根(自造样本)
    _write(ret, f78._fm({"model": "m"}, f"# RETURN — {task_id}\n\n## 一句话结论\n完成。\n"))
    d = nr.derive_next_step(task_id, gov)
    how = {e["key"]: e["hint"] for e in nr.report_frontmatter_contract("return", branch_task_id=task_id)}  # F204 前的补法渲染
    before = (f"执行体在 {ret} 的 frontmatter 补齐 commit_sha, tree_hash, branch(声明: transitions.schema artifact_ingest.return."
              "required_frontmatter)。补法: " + "; ".join(f"{k} = {how[k]}" for k in ("commit_sha", "tree_hash", "branch")))
    _show(f"[件① code 卡缺键拒因原文(不变)] {d['suggested_action']}")
    assert d["suggested_action"] == before
    _write(ret, f78._return_text(task_id, sha[:20] + sha[23:], tree))  # F180R 形: 少中间 3 位
    check = validate_task_artifact(task_id, gov)
    problem = check["header_problem"]
    _show(f"[件② code 卡 commit_sha 形式错] category={check['category']} kinds={problem['kinds']} correctable={problem['correctable']}"
          f"\n  problem={problem['problem']}\n  fix={problem['fix']}")
    assert check["category"] == "INGEST_TIP_MISMATCH" and problem["kinds"] == ["form"] and problem["correctable"]
    assert problem["fix"].startswith(f"commit_sha = {sha}(卡分支 card/{task_id} tip 的完整 40 位 sha")
    other = f78._git(code_repo, "rev-parse", "main")
    _write(ret, f78._return_text(task_id, other, tree))  # 真指向另一个提交 = 不可纠正(照原规则)
    assert validate_task_artifact(task_id, gov)["header_problem"]["correctable"] is False
    _write(ret, f78._return_text(task_id, sha, "c" * 40))  # tree_hash 写错 = 可纠正(应为该 commit 的 tree)
    tree_problem = validate_task_artifact(task_id, gov)["header_problem"]
    assert tree_problem["kinds"] == ["mismatch"] and tree_problem["fix"].startswith(f"tree_hash = {tree}(")


# ===========================================================================
# 声明单源 / fail-closed / 唯一实现
# ===========================================================================

def test_declarations_single_source_and_fail_closed():
    contract = load_loop_contract()
    decl = report_fix_declaration(contract, "executor")
    run_decl = lrr.run_record_declaration(contract)
    rules = lrr.next_action_declaration(run_decl)
    _show(f"[声明] launch.report_fix.executor: max_per_run={decl['max_per_run']} end_reason={decl['end_reason']}\n  prompt={decl['prompt']}")
    assert decl["max_per_run"] == 1 and decl["end_reason"] in run_decl["end_reasons"]
    assert rules["by_end_reason"][decl["end_reason"]] == "advisor_fix"
    assert any("{header_fix_prompt}" in s for s in rules["routine_rejections"]["end_reasons"][decl["end_reason"]]["fix"])
    assert rules["routine_rejections"]["return_header_probe"]["end_reasons"] == ["artifact_invalid"]
    assert set(header_fix_declaration()["correctable_kinds"]) == {"missing", "mismatch", "form", "contract_misuse"}
    broken = json.loads(json.dumps(contract))
    broken["launch"]["report_fix"]["executor"]["prompt"] = "no placeholders"
    with pytest.raises(SchemaLoadError, match="report_fix.executor.prompt"):
        report_fix_declaration(broken, "executor")
    broken["launch"]["report_fix"].pop("executor")
    with pytest.raises(SchemaLoadError, match="report_fix.executor not declared"):
        report_fix_declaration(broken, "executor")
    real = nr._artifact_ingest_declaration()
    bad = json.loads(json.dumps(real))
    bad["return"]["header_fix"]["contract_misuse"].pop("governance")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nr, "_artifact_ingest_declaration", lambda: bad)
        with pytest.raises(SchemaLoadError, match="header_fix"):
            header_fix_declaration()
    ingest = (REPO_ROOT / "tools" / "aipos_cli" / "artifact_ingest.py").read_text(encoding="utf-8")
    resolver = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    driver = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert ingest.count("def return_header_problem(") == 1 and "return_header_problem(" in resolver
    assert "how.get(k, '按声明填写实值')" not in resolver, "缺键补法只剩唯一实现"
    assert "_audit_report_fix" not in driver and driver.count("def _report_fix(") == 1 and driver.count("_launch_once(") == 3
    assert "outputs 契约" not in driver and "except Exception" not in driver and "sleep(" not in driver
    assert return_header_problem.__module__ == "tools.aipos_cli.artifact_ingest"
