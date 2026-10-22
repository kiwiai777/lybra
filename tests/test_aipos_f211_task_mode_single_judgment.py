"""AIPOS-F211 — 碎片化整理 C(F208 P1-1 / P1-2 / P1-5, gap #217): 写仓判据与 task_mode 缺省清零。

件① 写仓 / 建树 / 独立审计只调 next_resolver 已有唯一判定(card_writes_product_repo / card_needs_worktree /
    task_mode_independent_audit, 卡级 card_independent_audit; 交回「有测试」读声明 task_mode_requires_tests),
    删 8 处 task_mode == "code" 字面与 flow_description 自有集合。
件② 缺 task_mode 缺省 = schema(enums.schema task_mode.missing_default: 不写仓、不独立审计; Owner 10-22 裁定), 只在
    next_resolver._task_mode_entry 一处兜底; 删调用方 12 处 or "code" / get("task_mode", "code")。
件③ 执行方式 / harness 合一: loop_driver.card_harness 缺省由 workspace_config.card_execution_mode 推出; Owner 收件箱
    授权建议(owner_inbox._card_routes_to_pi)覆盖审计阶段。
件④ 固定候选验证卡审计 FAIL → transitions nodes.N4.candidate_fail: loop 以 candidate_fail 结束, loop status = advisor_fix
    (来源卡修复出新候选 → 另出验证卡 → queue supersede 本卡), 不计连败、不进 Owner 收件箱。
棘轮: 产品代码 task_mode 与声明模式值的字面比较(R1)、缺值补声明模式值(R2)只减不增(基线 tests/f211_task_mode_literal_baseline.jsonl)。

靶场 = 临时治理根(AIPOS-F78 `_make_gov` 骨架, lybra 形 + chris 形人肉期落点声明)+ 临时产品仓; 四形态卡自造(code / 写仓 docs /
治理根 docs / 缺 task_mode)。不读真实凭据、不碰真实治理根、不对真实门写、不起 pi。
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ratchet_baseline import read_entries  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract  # noqa: E402

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
from test_aipos_f210_delivery_subject_single_source import scoped_files  # noqa: E402  — 扫描面同 F210 棘轮口径

BASELINE_PATH = Path(__file__).resolve().parent / "f211_task_mode_literal_baseline.jsonl"
K, W, G, M = "PROBE-F211-K", "PROBE-F211-W", "PROBE-F211-G", "PROBE-F211-M"
SRC, VAL = "PROBE-F211-S", "PROBE-F211-V"
SUB = "exec.lybra-sub.test"
SHAPES = {K: "code", W: "写仓 docs(lane.repo = 产品仓)", G: "治理根 docs(lane.repo = governance_root)", M: "缺 task_mode(人肉期存量, 无 lane)"}


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _project(gov: Path, **extra) -> None:
    pj = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    pj.update(extra)
    (gov / "project.json").write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")


def _fm_of(gov: Path, task_id: str) -> dict:
    path, _q = nr._find_task_in_queue(gov, task_id)
    return {**nr._read_frontmatter(path), "task_id": task_id}


@pytest.fixture(params=["lybra", "chris"])
def rig(request, tmp_path, monkeypatch):
    """四形态卡(均已认领、audit: none——是否独立审计只看声明缺省): code / 写仓 docs / 治理根 docs / 缺 task_mode; 产品仓本卡车道内
    有一个未提交文件, 均无卡分支(第一阶段)。"""
    from tools.schema_loader import clear_cache

    clear_cache()
    gov = f78._make_gov(tmp_path, monkeypatch, shape=request.param)
    code_repo = tmp_path / f"product-{request.param}"
    _project(gov, test_contract={"require_tests": True})
    lane_paths = {"paths": ["tools/aipos_cli/"], "roles": ["executor"]}
    common = {"audit": "none", "output_target": "tools/aipos_cli/"}
    f78._card(gov, K, "claimed", extra=common)
    f78._card(gov, W, "claimed", task_mode="docs", extra={**common, "lane": {"repo": str(code_repo), **lane_paths}})
    f78._card(gov, G, "claimed", task_mode="docs",
              extra={**common, "output_target": f"task_cards/{G}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{G}/artifacts/"], "roles": ["executor"]}})
    path = f78._card(gov, M, "claimed", extra=common)
    path.write_text(path.read_text(encoding="utf-8").replace("task_mode: code\n", ""), encoding="utf-8")
    assert "task_mode" not in nr._read_frontmatter(path)
    for card in (K, W, G, M):
        f78._claim_record(gov, card, f78.EXEC)
    f78._write(code_repo / "tools" / "aipos_cli" / "dirty_uncommitted.py", "# 未提交\n")
    return gov, code_repo


# ===========================================================================
# 验收①: 四形态在交回自检 / 未提交检查 / finalize / 派审 / loop 推导各处行为原文(与 schema 一致)
# ===========================================================================

def test_accept1_four_shapes_single_judgment_everywhere(rig):
    from tools.aipos_cli import board_adapter as ba
    from tools.aipos_cli import finalize as fz
    from tools.aipos_cli.loop_driver import card_harness

    gov, code_repo = rig
    expected_write = {K: True, W: True, G: False, M: False}
    expected_audit = {K: True, W: False, G: False, M: False}
    rows = {}
    for card in (K, W, G, M):
        fm = _fm_of(gov, card)
        rows[card] = {
            "card_writes_product_repo": nr.card_writes_product_repo(gov, fm),
            "card_needs_worktree": nr.card_needs_worktree(gov, fm),
            "card_independent_audit": nr.card_independent_audit(fm),
            "task_mode_requires_tests": nr.task_mode_requires_tests(str(fm.get("task_mode") or "")),
            "card_governance_outputs": nr.card_governance_outputs(gov, fm),
            "card_delivery_subject": nr.card_delivery_subject(gov, card, fm)["kind"]
            if hasattr(nr, "card_delivery_subject") else None,
            "card_harness(未声明 execution)": card_harness(fm, gov, task_id=card),
        }
    _show("[验收① 判定点] " + json.dumps({f"{c}({SHAPES[c]})": r for c, r in rows.items()}, ensure_ascii=False, indent=1))
    assert {c: r["card_writes_product_repo"] for c, r in rows.items()} == expected_write
    assert {c: r["card_needs_worktree"] for c, r in rows.items()} == expected_write
    assert {c: r["card_independent_audit"] for c, r in rows.items()} == expected_audit
    assert {c: r["task_mode_requires_tests"] for c, r in rows.items()} == {K: True, W: False, G: False, M: False}
    assert rows[G]["card_governance_outputs"] and not rows[M]["card_governance_outputs"]  # 缺 task_mode 存量不算产出清单(F197 声明)
    assert all(r["card_harness(未声明 execution)"] == "pi" for r in rows.values())  # 缺 task_mode 走 missing_default.key 查表

    # (a) 交回自检 ⑤ 分支合规(无卡分支阶段): 只对有自己卡分支的写仓卡(code + 写仓 docs), 门前门后同口径
    selfcheck = {c: ba._check_return_self_checks(task_id=c, task_metadata=_fm_of(gov, c), repo_root=gov, result_summary="done",
                                                 completion_report_ref=None, artifact_refs=[], warnings=[]) for c in (K, W, G, M)}
    _show("[验收① 交回自检(无卡分支)原文] " + json.dumps(selfcheck, ensure_ascii=False, indent=1))
    branch_flag = {c: any(str(r).startswith("BRANCH_NOT_FOUND") for r in rs) for c, rs in selfcheck.items()}
    assert branch_flag == expected_write, branch_flag

    # (b) 未提交代码检查(交回门 dry-run): 只问 card_writes_product_repo(formal_write 不再单独触发)
    uncommitted = {}
    for c in (K, W, G, M):
        res = ba.return_task(task_id=c, actor=f78.EXEC, agent_instance=f78.EXEC, owner_policy_ref="fixture", result_summary="done",
                             dry_run=True, repo_root=gov)
        uncommitted[c] = [r for r in res.get("blocking_reasons", []) if "CODE_NOT_COMMITTED" in str(r)]
    _show("[验收① 未提交检查原文] " + json.dumps(uncommitted, ensure_ascii=False, indent=1))
    assert {c: bool(v) for c, v in uncommitted.items()} == expected_write

    # (c) finalize 缺卡分支: 写仓卡硬 BLOCK, 不写仓卡跳过(needs_worktree 由唯一判据算好传入, finalize 生产调用面同此)
    fin = {}
    for c in (K, W, G, M):
        fm = _fm_of(gov, c)
        res = fz._integrate_card_branch(c, "v", code_repo, gov, True, [], task_mode=str(fm.get("task_mode") or "") or None,
                                        output_target=str(fm.get("output_target") or ""), needs_worktree=nr.card_needs_worktree(gov, fm))
        fin[c] = f"{res['action']}: {res.get('message', '')[:90]}"
    _show("[验收① finalize 缺分支原文] " + json.dumps(fin, ensure_ascii=False, indent=1))
    assert {c: v.startswith("blocked_branch_not_found") for c, v in fin.items()} == expected_write
    assert all(fin[c].startswith("skipped_no_branch") for c in (G, M))

    # (d) 第二阶段: 写仓卡建卡分支(提交里无测试文件)→ ⑤ 过, ③「有测试」只对声明 requires_tests 的模式(code)
    for c in (K, W):
        f78._branch_with_commit(code_repo, c, rel=f"tools/aipos_cli/{c.lower().replace('-', '_')}.py")
    selfcheck2 = {c: ba._check_return_self_checks(task_id=c, task_metadata=_fm_of(gov, c), repo_root=gov, result_summary="done",
                                                  completion_report_ref=None, artifact_refs=[], warnings=[]) for c in (K, W)}
    _show("[验收① 交回自检(有卡分支)原文] " + json.dumps(selfcheck2, ensure_ascii=False, indent=1))
    assert any(str(r).startswith("NO_TESTS") for r in selfcheck2[K]) and not any(str(r).startswith("NO_TESTS") for r in selfcheck2[W])
    assert not any(str(r).startswith("BRANCH_") for rs in selfcheck2.values() for r in rs)

    # (e) 派审 / loop 推导(已交回): 独立审计只 code(模式声明); 写仓 docs / 治理根 docs / 缺 task_mode = 顾问复核
    for c in (K, W, G, M):
        f78._write(gov / "5_tasks" / "records" / "returns" / c / f"return_{c}_x_{f78.EXEC}.md",
                   f78._fm({"record_type": "return", "return_id": f"return_{c}_x", "task_id": c, "agent_instance": f78.EXEC}))
    derived = {c: nr.derive_next_step(c, gov) for c in (K, W, G, M)}
    _show("[验收① 推导核(已交回)原文] " + json.dumps(
        {c: {k: d.get(k) for k in ("suggested_action", "notes", "verb")} | {"command": str(d.get("command") or "").splitlines()[0][:120]}
         for c, d in derived.items()}, ensure_ascii=False, indent=1))
    assert derived[K]["suggested_action"] == "派发审计" and derived[K]["verb"] == "lybra_audit_dispatch_dry_run"
    for c in (W, G, M):
        assert derived[c]["verb"] != "lybra_audit_dispatch_dry_run" and "派审" not in str(derived[c]["notes"]), derived[c]
    assert "task_mode=(缺)" in str(derived[M]["command"]), derived[M]
    # loop / 裁决绑 tip: 卡分支 tip 读取只对写仓卡
    tips = {c: nr._card_branch_tip(gov, c, _fm_of(gov, c)) is not None for c in (K, W, G, M)}
    _show(f"[验收① 卡分支 tip 读取(_card_branch_tip)] {tips}")
    assert tips == expected_write


def test_accept1_flow_and_gate_hint_read_declarations(rig):
    """flow_description 链路收窄 = 卡不走独立审计(card_independent_audit); 门 next_action 裁决后 finalize 提示不再只给 code。"""
    from tools.aipos_cli.flow_description import resolve_gate_chain
    from tools.mcp_server.tools import _generate_next_action

    profile = {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}
    chains = {mode or "(缺)": resolve_gate_chain(profile, {"task_mode": mode} if mode else {}).branch_id
              for mode in ("code", "validation", "docs", "governance", "config", "")}
    chains["docs+audit:required"] = resolve_gate_chain(profile, {"task_mode": "docs", "audit": "required"}).branch_id
    _show(f"[验收① flow_description 链路] {chains}")
    assert chains["code"] == chains["validation"] == chains["docs+audit:required"] == "code_no_deploy"
    assert {chains[m] for m in ("docs", "governance", "config", "(缺)")} == {"noncode_bench_audit"}
    hints = {mode: (_generate_next_action("claimed", mode, "audit_verdict") or {}).get("verb") for mode in ("code", "validation", "docs", "")}
    _show(f"[验收① 门 next_action 裁决后提示] {hints}")
    assert set(hints.values()) == {"lybra_finalize"}


# ===========================================================================
# 验收②: card_harness 与 card_execution_mode 一致; 授权建议含审计阶段 pi
# ===========================================================================

def test_accept2_harness_follows_execution_mode_and_grant_covers_audit_stage(rig):
    from tools.aipos_cli.enrollment import _append_enrollment_trail, subagent_land_detail
    from tools.aipos_cli.loop_driver import CardHarnessUnresolved, card_harness
    from tools.aipos_cli.owner_inbox import _card_routes_to_pi, suggest_grant
    from tools.aipos_cli.workspace_config import card_execution_mode, project_execution

    gov, _code = rig
    _project(gov, execution={"default_mode": "subagent", "subagent_executor": SUB, "auditor": f78.AUDITOR,
                             "pi_executor": f78.EXEC, "pi_allowed_task_modes": ["validation", "docs"]})
    cards = {
        "code(卡面无 harness / assigned_to)": {"task_id": "X-1", "task_mode": "code"},
        "docs(在 pi_allowed)": {"task_id": "X-2", "task_mode": "docs"},
        "validation(在 pi_allowed)": {"task_id": "X-3", "task_mode": "validation"},
        "缺 task_mode": {"task_id": "X-4"},
        "审计卡": {"task_id": "X-1R", "task_mode": "audit", "reviewed_task_id": "X-1"},
        "code 卡面 harness=codex": {"task_id": "X-5", "task_mode": "code", "harness": "codex"},
    }
    execution = project_execution(gov)
    with pytest.raises(CardHarnessUnresolved) as unresolved:
        card_harness(cards["code(卡面无 harness / assigned_to)"], gov, task_id="X-1")
    _show(f"[验收② 子 agent 执行者无接入登记 = 拒(不猜)] {unresolved.value}")
    _append_enrollment_trail(gov, action="land", code_id="enroll_fixture_sub", role="executor", instance=SUB, by="(agent-enroll)",
                             reason=subagent_land_detail(host="advhost", harness="claude-code", mode="subagent", files=["connection.json"]))
    table = {}
    for label, fm in cards.items():
        from tools.aipos_cli.audit_derivation import is_audit_card

        mode = card_execution_mode(execution, fm, audit_card=is_audit_card(fm["task_id"], fm))
        table[label] = {"card_execution_mode": mode, "card_harness": card_harness(fm, gov, task_id=fm["task_id"]),
                        "授权建议拉起": _card_routes_to_pi(gov, fm, task_id=fm["task_id"])}
    _show("[验收② 执行方式 / harness / 授权建议] " + json.dumps(table, ensure_ascii=False, indent=1))
    for label, row in table.items():
        if "harness=codex" in label:
            continue
        assert (row["card_harness"] == "pi") == (row["card_execution_mode"] == "pi"), (label, row)
    assert table["code(卡面无 harness / assigned_to)"]["card_harness"] == "claude-code"  # 子 agent = 其接入登记的会话 harness
    assert table["审计卡"]["card_harness"] == "pi"
    # 代码卡执行阶段子 agent, 审计阶段(独立审计, 审计卡恒 pi)要拉 pi → 建议含 pi; 缺 task_mode 卡(不独立审计、子 agent)= 不含
    assert table["code(卡面无 harness / assigned_to)"]["授权建议拉起"] == "pi"
    assert table["缺 task_mode"]["授权建议拉起"] is None
    grant = suggest_grant(gov, "lybra", task_id="X-1", card_fm={**cards["code(卡面无 harness / assigned_to)"], "title": "x"},
                          driver=f78.DRIVER)
    _show(f"[验收② 授权建议原文(代码卡)] launch_harnesses={grant['launch_harnesses']} 来源={grant['sources']['launch_harnesses']}")
    assert grant["launch_harnesses"] == ["pi"] and grant["sources"]["launch_harnesses"] == "card_execution"


# ===========================================================================
# 验收③: 固定候选验证卡审计 FAIL → candidate_fail → loop status advisor_fix
# ===========================================================================

def test_accept3_candidate_fail_routes_advisor_fix_not_owner(rig):
    from tools.aipos_cli import loop_run_record as lrr
    from tools.aipos_cli.loop_driver import load_loop_contract

    gov, code_repo = rig
    sha, _tree = f78._branch_with_commit(code_repo, SRC, rel="app/ota.py")
    f78._card(gov, SRC, "completed")
    f78._card(gov, VAL, "claimed", task_mode="validation",
              extra={"validation_source_task": SRC, "validation_candidate": sha, "output_target": f"task_cards/{VAL}/artifacts/",
                     "lane": {"repo": str(code_repo), "paths": [f"task_cards/{VAL}/artifacts/"], "roles": ["executor"]}})
    f78._claim_record(gov, VAL, f78.EXEC)
    f78._write(gov / "5_tasks" / "records" / "returns" / VAL / f"return_{VAL}_x_{f78.EXEC}.md",
               f78._fm({"record_type": "return", "return_id": f"return_{VAL}_x", "task_id": VAL, "agent_instance": f78.EXEC}))
    f78._write(gov / "5_tasks" / "records" / "audit_dispatches" / f"{VAL}R" / "dispatch_x.md",
               f78._fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": VAL}))
    summary = "FAIL — 候选 OTA 脚本 datetime 解析缺陷, 联合验证第 3 步崩溃(属实, 非环境)"
    f78._write(gov / "5_tasks" / "records" / "audit_verdicts" / VAL / "verdict_x.md",
               f78._fm({"record_type": "audit_verdict_record", "verdict_id": f"verdict_{VAL}_x", "task_id": VAL, "audit_task_id": f"{VAL}R",
                        "verdict": "FAIL", "verdict_at": "2026-10-22T03:00:00Z"}, f"# 裁决\n\n- Findings summary: {summary}\n"))
    d = nr.derive_next_step(VAL, gov)
    _show("[验收③ 推导核原文] " + json.dumps({k: d.get(k) for k in ("derivable", "triggered_by", "suggested_action", "notes", "action")},
                                          ensure_ascii=False, indent=1))
    assert d["action"]["type"] == "candidate_fail" and d["action"]["end_reason"] == "candidate_fail" and d["triggered_by"] == "advisor"
    assert "返工或修复" not in d["suggested_action"] and "queue supersede" in d["suggested_action"] and sha in d["suggested_action"]

    # loop 本身: 推导核 candidate_fail = 以 candidate_fail 结束(exit not_derivable), 不拉起、不碰门
    import io

    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown
    from tools.aipos_cli.loop_driver import exit_code_for, run_loop

    f78._write(gov / "5_tasks" / "policies" / "pol_f211_val.md", build_autonomy_policy_markdown(
        policy_id="pol_f211_val", agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f211", task_selector_task_mode="validation", launch_harnesses=["pi"]))
    out = io.StringIO()
    res = run_loop(VAL, gov, actor=f78.DRIVER, policy_id="pol_f211_val", out=out, interval=0.05, max_wait=2, max_steps=3, no_launch=True)
    _show(f"[验收③ lybra loop 输出原文] exit {res.exit_code} reason {res.reason}\n{out.getvalue()}")
    assert res.reason == "candidate_fail" and res.exit_code == exit_code_for(load_loop_contract(), "not_derivable"), out.getvalue()
    assert "固定候选审计 FAIL" in out.getvalue()
    from test_aipos_f131_loop_run_status import _cli as _status_cli

    rc, status_text, err = _status_cli(["loop", "status", "--task-id", VAL, "--workspace-root", str(gov)])
    _rcj, status_json, _e = _status_cli(["loop", "status", "--task-id", VAL, "--workspace-root", str(gov), "--json"])
    _show(f"[验收③ lybra loop status 原文] exit {rc}\n{status_text}{err}")
    real = json.loads(status_json)["next_action"]
    assert real["action"] == "advisor_fix" and real["reason"] == "candidate_fail", real
    assert f"queue supersede --task-id {VAL} --by <新验证卡号>" in status_text

    contract = load_loop_contract()
    run_decl = lrr.run_record_declaration(contract)
    rules = lrr.next_action_declaration(run_decl)
    assert "candidate_fail" in run_decl["end_reasons"] and rules["by_end_reason"]["candidate_fail"] == "advisor_fix"
    view = {"task_id": VAL, "state": "ended", "end_reason": "candidate_fail", "end_message": d["suggested_action"],
            "driver": f78.DRIVER, "envelope": "pol_fixture"}
    # 前一次运行以 investigate 类原因结束: 旧规则下会连成 consecutive_failures → owner_needed; 现 = advisor_fix
    na = lrr.next_action(view, rules, governance_root=gov, earlier_end_reasons=["not_derivable"])
    _show("[验收③ loop status 下一动作原文]\n" + json.dumps({k: na.get(k) for k in ("action", "reason", "detail")}, ensure_ascii=False)
          + "\n" + str(na.get("hint")))
    assert na["action"] == "advisor_fix" and na["reason"] == "candidate_fail", na
    hint = str(na["hint"])
    assert summary[:30] in hint and f"来源卡 {SRC}(本卡 FAIL 的固定候选 {sha})" in hint
    assert f"queue supersede --task-id {VAL} --by <新验证卡号>" in hint and "--dry-run" in hint
    # 旧形(未登记本出口时)对照: not_derivable 接在 investigate 后 = 连败升 Owner
    old = lrr.next_action({**view, "end_reason": "not_derivable"}, rules, governance_root=gov, earlier_end_reasons=["not_derivable"])
    assert old["action"] == "owner_needed" and old["reason"] == "consecutive_failures"
    # 非候选卡 FAIL 不受影响(仍旧口径), BLOCK 仍走 audit_blocked(先判)
    assert nr.candidate_fail_state(gov, K, _fm_of(gov, K)) is None


# ===========================================================================
# 声明 / 单一兜底
# ===========================================================================

def test_declarations_missing_default_single_fallback():
    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]
    md = enums["missing_default"]
    _show(f"[件② missing_default] { {k: v for k, v in md.items() if k != 'description'} }")
    assert md["writes_product_repo"] is False and md["independent_audit"] is False and md["requires_tests"] is False
    assert md["key"] not in {v["value"] for v in enums["values"]}
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))["intent_face"]["harness"]
    assert md["key"] in card["default_by_task_mode"] and "loop_driver.card_harness" in card["default_rule"]
    entry = nr._task_mode_entry("")
    assert entry["missing"] is True and entry["value"] == md["key"]
    assert nr._task_mode_entry("bogus") is None
    assert [nr.task_mode_writes_product_repo(m) for m in ("", "bogus", "code")] == [False, False, True]
    assert [nr.task_mode_independent_audit(m) for m in ("", "bogus", "code", "validation")] == [False, False, True, True]
    assert enums["requires_tests"]["single_implementation"] == "tools/aipos_cli/next_resolver.task_mode_requires_tests"
    for ref in (enums["requires_tests"]["single_implementation"], enums["independent_audit"]["single_implementation"]):
        assert callable(getattr(nr, ref.rsplit(".", 1)[1]))
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count("candidate_fail_state(workspace_root, task_id, fm)") == 1, "N4 分支一处判定"


# ===========================================================================
# 棘轮: R1 task_mode 与声明模式值字面比较 / R2 缺值补声明模式值, 只减不增
# ===========================================================================

def _modes() -> set[str]:
    return {v["value"] for v in json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]["task_mode"]["values"]}


def _mentions_task_mode(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if (isinstance(n, ast.Name) and "task_mode" in n.id) or (isinstance(n, ast.Attribute) and "task_mode" in n.attr) \
                or (isinstance(n, ast.Constant) and n.value == "task_mode"):
            return True
    return False


def _str_consts(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _enclosing(tree: ast.Module) -> dict[int, str]:
    out: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else name
            if hasattr(child, "lineno"):
                for line in range(child.lineno, (getattr(child, "end_lineno", None) or child.lineno) + 1):
                    out.setdefault(line, child_name)
            visit(child, child_name)

    visit(tree, "<module>")
    return out


def scan_source(rel: str, text: str, modes: set[str] | None = None) -> list[dict[str, str]]:
    """两类式样:
    mode_literal   比较(== / != / in / not in)一侧涉及 task_mode, 另一侧为声明的 task_mode 值(或含其的元组 / 列表 / 集合)——
                   写仓 / 独立审计 / 测试等判定改调 next_resolver 唯一读取口(_task_mode_entry 族);
    missing_fill   缺 task_mode 补声明模式值: `<涉 task_mode> or "<值>"`、`.get("task_mode", "<值>")`、参数 `task_mode="<值>"`
                   缺省——缺值兜底只在 next_resolver._task_mode_entry(enums.schema task_mode.missing_default)。"""
    modes = modes if modes is not None else _modes()
    tree = ast.parse(text)
    funcs = _enclosing(tree)
    hits: list[dict[str, str]] = []

    def add(node: ast.AST, kind: str, shown: str) -> None:
        hits.append({"file": rel, "func": funcs.get(node.lineno, "<module>"), "kind": kind,
                     "fp": hashlib.sha256(shown.encode("utf-8")).hexdigest()[:12], "text": shown[:160]})

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(_mentions_task_mode(a) and any(c.strip().lower() in modes for c in _str_consts(b))
                   for a in sides for b in sides if a is not b):
                add(node, "mode_literal", ast.unparse(node))
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and _mentions_task_mode(node.values[0]) \
                and any(c in modes for v in node.values[1:] for c in _str_consts(v)):
            add(node, "missing_fill", ast.unparse(node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) == 2 \
                and _str_consts(node.args[0]) == ["task_mode"] and any(c in modes for c in _str_consts(node.args[1])):
            add(node, "missing_fill", ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            pos = a.posonlyargs + a.args
            pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults)) + [(k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults) if d]
            for arg, default in pairs:
                if "task_mode" in arg.arg and any(c in modes for c in _str_consts(default)):
                    add(node, "missing_fill", f"def {node.name}({arg.arg}={ast.unparse(default)})")
    return hits


def scan_repo(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    code, _templates = scoped_files(repo_root)
    modes = _modes()
    hits: list[dict[str, str]] = []
    for rel in code:  # 解码 / 语法失败 = 抛(fail-closed)
        hits.extend(scan_source(rel, (repo_root / rel).read_text(encoding="utf-8"), modes))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(entry["file"]), str(entry["func"]), str(entry["kind"]), str(entry["fp"]))


def test_ratchet_baseline_well_formed():
    for entry in read_entries(BASELINE_PATH):
        assert set(entry) == {"file", "func", "kind", "fp", "text", "reason"}, entry
        assert str(entry["reason"]).strip(), entry


def test_ratchet_task_mode_literal_and_missing_fill_baseline_only_shrinks():
    hits = scan_repo()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new, stale = current - allowed, allowed - current
    code, _t = scoped_files()
    _show(f"[棘轮] 扫描面 {len(code)} 个产品源文件; task_mode 字面比较 / 缺值补值命中 {sum(current.values())} 处; 基线 {len(baseline)} 条")
    for hit in hits:
        _show(f"  命中 [{hit['kind']}] {hit['file']}::{hit['func']} {hit['text']}")
    assert not new, ("新增 task_mode 字面判据 / 缺值补值(写仓 / 建树 / 独立审计 / 测试改调 next_resolver 唯一读取口; 缺值兜底只在 "
                     "_task_mode_entry): " + "; ".join(f"[{h['kind']}] {h['file']}::{h['func']} {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修 / 已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"
    assert not any(h["kind"] == "missing_fill" for h in hits), "缺值补值已清零(件②), 不得回潮"


def test_ratchet_pattern_catches_known_shapes_and_spares_benign():
    modes = {"code", "docs", "audit", "validation"}
    bad = '''
def f(fm, task_mode="code"):
    a = fm.get("task_mode") or "code"
    b = fm.get("task_mode", "code")
    if task_mode == "code":
        pass
    if str(fm.get("task_mode") or "").strip() in ("docs", "validation"):
        pass
    return a, b
'''
    kinds = sorted((h["kind"], h["text"]) for h in scan_source("x.py", bad, modes))
    _show(f"[棘轮式样自检] {kinds}")
    assert [k for k, _ in kinds] == ["missing_fill", "missing_fill", "missing_fill", "mode_literal", "mode_literal"], kinds
    clean = '''
def ok(fm, root):
    mode = str(fm.get("task_mode") or "").strip()
    shown = mode or "(缺)"
    return card_writes_product_repo(root, fm), task_mode_independent_audit(mode), shown, fm.get("task_mode", "")
'''
    assert scan_source("y.py", clean, modes) == []


def test_f211_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import is_test_file

    assert is_test_file("tests/test_aipos_f211_task_mode_single_judgment.py", default_test_contract())
