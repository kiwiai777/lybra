"""AIPOS-F230 — 门只守底线、不规定路线(10-23/24 chris 连续卡在「非正常状态只给一条路」: F211 验证卡 FAIL 只能换候选、F197 快车道
复核不通过只能结案、返工上限只能找 Owner、多主机只取最后一台; LANTU-DEMO1 config 卡交付 1400 行程序; HBLX-42VC2 只补 host;
HBLX-31 送审前报告非终版; HBLX-24BV/35V/39V supersede 后审计卡被晾; HBLX-45V 只补材料的返工轮审计仍须完整重跑)。

件① 出口全集: 声明 transitions nodes.N4.legal_exits(底线不变式 + 「非通过状态 × 交付主体」表, 每格 ≥2 出口或写明为何唯一 / 不适用);
    next(legal_exits 键 + 文本 Exits 段)与 loop status(next_action.legal_exits)同读 next_resolver.legal_exits 唯一渲染, 推荐按证据改判。
件② 卡型守卫: task_mode ∈ config / docs 的卡交回产物含可执行程序(代码扩展名或 shebang 且行数 > 阈值或带可执行位, 阈值进 schema)=
    交回入口拒并指路改走代码卡; 发卡同判据预警。
件③ 返工自动放行「范围未变」只看验收 / 候选 / 范围字段; 只改非验收字段(audit_runtime_evidence.host 等, 白名单进 schema)不算范围变化。
件④ 同轮补正: 已交回、审计未开始的卡顾问 `mark-concluded --outcome same_round` 打回同一轮(不新增返工轮、记一条顾问复核、撤本轮未认领
    审计卡), 执行体改后重交回(判据复用 F218/F219 晚于登记), 门派下一轮取代被撤轮。
件⑤ supersede / withdraw 业务卡连带撤其在途审计卡(预演列出, 同一操作撤回并随业务卡落账)。
件⑥ 同一候选、返工焦点只涉交付物(上一轮 fail_scope=deliverable)时, 审计可引用自己上一轮对同一候选的运行证据(run_id / 指纹核对)。

靶场 = F90 进程内门 + loop 全链(临时治理根 + 临时产品仓 + 临时 HOME; 禁真门 / 真治理根 / 真工位 / 起 pi), 卡面自造人肉期形态。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import io
import json
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402  — 验证卡 / 治理根产出卡靶场唯一来源
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402  — 快车道卡构件
import test_aipos_f222_rework_limit_arbitration as f222  # noqa: E402  — 裁决记录构件
import test_aipos_f225_owner_intervention_tiers as f225  # noqa: E402  — 发布基线构件
import test_aipos_f227_candidate_fail_scope as f227  # noqa: E402  — 固定候选 FAIL 构件
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f218_candidate_rework_recovery import VAL, _pick, _quiet, _show, vrig  # noqa: E402,F401
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

CODE = "PROBE-F230-K"        # 卡分支主体(code 卡)
GOVC = f221.CARD             # 治理根产出快车道卡(LANTU-DEMO1 形态 config)
DOCS = f218.GOV              # 治理根产出 + 独立审计 docs 卡(HBLX-31 形态)


def _exit_lines(exits: dict) -> str:
    return "\n".join(nr.render_legal_exit_lines(exits))


# ===========================================================================
# 件① 出口全集: 声明守卫(每格 ≥2 或写明唯一 / 不适用)+ 加载 fail-closed 反例
# ===========================================================================

def test_item1_declaration_every_cell_two_exits_or_reason(monkeypatch):
    decl = nr.legal_exits_declaration()
    kinds = set(nr.delivery_subject_kinds())
    rows = []
    for state, row in decl["states"].items():
        assert set(row) == kinds, state
        for subject, cell in row.items():
            if isinstance(cell, list):
                assert len(cell) >= 2 and len(set(cell)) == len(cell) and set(cell) <= set(decl["exits"]), (state, subject, cell)
            else:
                assert set(cell) <= {"sole_reason", "not_applicable"} and next(iter(cell.values())).strip(), (state, subject, cell)
            rows.append(f"{state:28s} × {subject:18s} = {cell if isinstance(cell, list) else cell}")
    _show("---- 件① 出口全集表(transitions nodes.N4.legal_exits.states)----\n" + "\n".join(rows))
    # 卡面要求的 7 类非通过状态全在表内; 出口含五类(同主体返工 / 换新主体 / 重新派审 / 改范围 / 撤卡)
    assert set(decl["states"]) == {"FAIL", "BLOCK", "candidate_fail", "candidate_deliverable_fail", "fast_lane_review_fail",
                                   "rework_limit", "audit_blocked_repeated"}
    assert {"rework_same_subject", "new_subject", "reaudit", "change_scope", "withdraw"} <= set(decl["exits"])
    assert set(decl["invariants"]) == {"independent_audit", "evidence_bound", "traceable", "no_forbidden_zone"}
    # 反例: 某格只剩一条路且未写明原因 = 加载即拒(fail-closed)
    real = nr._transition_node

    def one_way(node_id):
        node = json.loads(json.dumps(real(node_id)))
        if node_id == "N4":
            node["legal_exits"]["states"]["FAIL"]["branch"] = ["rework_same_subject"]
        return node

    monkeypatch.setattr(nr, "_transition_node", one_way)
    with pytest.raises(SchemaLoadError) as exc:
        nr.legal_exits_declaration()
    _show(f"[件① 反例·FAIL × branch 只给一条路] {exc.value}")
    assert "states.FAIL.branch 须为 ≥2 个不重复的已声明出口" in str(exc.value)


# ===========================================================================
# 件① 各非通过状态 × 三种交付主体的 next 原文(真 loop 走出的态 + 表内其余格逐格渲染)
# ===========================================================================

def _code_fail(rig) -> dict:
    """code 卡(卡分支主体)走 loop: 执行交回 → 审计 FAIL → next 列全部合法出口。"""
    f90._card(rig.gov, CODE, "pending")
    init_governance_repo(rig.gov)

    def watch(args, expect_ready):
        patterns = " ".join(args.expect)
        if f"/{CODE}R/" in patterns or patterns.startswith(f"task_cards/{CODE}R"):
            tip = f78._git(rig.code_repo, "rev-parse", f"card/{CODE}")
            report = rig.gov / "task_cards" / f"{CODE}R" / "audit_report.md"
            _write(report, _fm({"task_id": f"{CODE}R", "reviewed_task_id": CODE, "verdict": "FAIL", "commit_sha": tip,
                                "model": f90.SELF_AUDIT_MODEL}, "# 审计报告\n\n## 一句话结论\nFAIL — 边界用例未覆盖\n\n## 证据\n- 夹具\n"))
            f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL, name="sess-code-r.jsonl")
        else:
            f90._executor_work(rig, CODE)
        return 0 if expect_ready([]) else 3

    out = io.StringIO()
    res = run_loop(CODE, rig.gov, actor=DRIVER, policy_id=f90.POLICY, out=out, watch=watch, interval=0.05, max_wait=5, max_steps=30)
    _show(f"---- 件① code 卡 loop(审计 FAIL)exit {res.exit_code} reason {res.reason} ----\n" + _quiet(out.getvalue())[-1500:])
    d = nr.derive_next_step(CODE, rig.gov)
    _show("---- 件① lybra next(FAIL × branch)文本原文 ----\n" + nr.format_output(d))
    return d


def test_item1_code_card_fail_lists_all_exits_and_loop_status(rig):  # noqa: F811
    d = _code_fail(rig)
    ex = d["legal_exits"]
    keys = [e["key"] for e in ex["exits"]]
    assert ex["state"] == "FAIL" and ex["subject"] == "branch" and len(keys) >= 2
    assert keys == ["rework_same_subject", "reaudit", "new_subject", "change_scope", "withdraw"], keys
    assert ex["recommended"] == "rework_same_subject" and "queue rework --task-id" in ex["exits"][0]["command"]
    assert "audit dispatch" in ex["exits"][1]["command"] and "queue supersede" in ex["exits"][2]["command"]
    owner = {e["key"]: e["owner_tier1"] for e in ex["exits"]}
    assert owner == {"rework_same_subject": None, "reaudit": None, "new_subject": "rescope", "change_scope": "rescope", "withdraw": "withdraw"}
    text = nr.format_output(d)
    assert "Exits(FAIL × branch" in text and "推荐: rework_same_subject" in text
    # loop status 同读同一渲染(next_action.legal_exits + hint 末行)
    _rc, out, err = _cli(["loop", "status", "--task-id", CODE, "--workspace-root", str(rig.gov)])
    _rcj, raw, _e = _cli(["loop", "status", "--task-id", CODE, "--workspace-root", str(rig.gov), "--json"])
    _show("---- 件① lybra loop status(FAIL × branch)原文 ----\n" + out + err)
    na = json.loads(raw)["runs"][0]["next_action"] if "runs" in json.loads(raw) else json.loads(raw)["next_action"]
    assert [e["key"] for e in na["legal_exits"]["exits"]] == keys and "合法出口(FAIL × branch" in out + err

    # 返工额度用尽(已登记 2 轮, 无发布基线不满足第二档自动追加)= 改判 rework_limit: 同主体返工换成带第一档事由升 Owner
    path, _q = nr._find_task_in_queue(rig.gov, CODE)
    text0 = path.read_text(encoding="utf-8")
    rounds = "rework_rounds:\n" + "".join(f"- round: {n}\n  verdict_ref: verdict_old_{n}\n  created_at: '2026-10-0{n}T00:00:00Z'\n"
                                           f"  created_by: {DRIVER}\n  focus_items:\n  - x\n" for n in (1, 2))
    path.write_text(text0.replace("---\n", "---\n" + rounds, 1) if not text0.startswith("---\nrework") else text0, encoding="utf-8")
    d2 = nr.derive_next_step(CODE, rig.gov)
    _show("---- 件① lybra next(返工额度用尽 → rework_limit × branch)文本原文 ----\n" + nr.format_output(d2))
    ex2 = d2["legal_exits"]
    assert ex2["state"] == "rework_limit" and ex2["requested_state"] == "FAIL" and ex2["budget"]["exhausted"]
    keys2 = [e["key"] for e in ex2["exits"]]
    assert keys2[0] == "owner_extension" and "--owner-reason rework_cap_exceeded" in ex2["exits"][0]["command"] and len(keys2) >= 2
    assert "rework_same_subject" not in keys2 and ex2["recommended"] == "new_subject"


def test_item1_candidate_states_fail_scope_recommendation_and_block(vrig):
    rig = vrig
    _res, _text, d = f227._first_fail(rig, "deliverable")
    ex = d["legal_exits"]
    _show("---- 件① lybra next(candidate_deliverable_fail × candidate)Exits 原文 ----\n" + _exit_lines(ex))
    assert ex["state"] == "candidate_deliverable_fail" and ex["subject"] == "candidate" and ex["recommended"] == "rework_same_subject"
    assert [e["key"] for e in ex["exits"]] == ["rework_same_subject", "new_subject", "reaudit", "change_scope", "withdraw"]
    # 推荐主路径(suggested_action)仍只给同候选补正; 全集另列
    assert "queue supersede" not in d["suggested_action"]
    # 同一张卡按未声明 / candidate 判: 表内 candidate_fail 格(推荐换新主体; 未声明另注证据改判依据)
    st = nr.non_pass_fail_state(rig.gov, VAL)
    ex_c = nr.legal_exits(rig.gov, VAL, f218._card_fm(rig.gov, VAL), "candidate_fail", verdict_state=st, actor=DRIVER)
    _show("---- 件① candidate_fail × candidate Exits 原文 ----\n" + _exit_lines(ex_c))
    assert ex_c["recommended"] == "new_subject" and len(ex_c["exits"]) >= 2 and "来源卡修复出新候选" in ex_c["exits"][0]["command"]


def test_item1_candidate_block_lists_exits_with_evidence_recommendation(vrig):
    rig = vrig
    d, status_text = f218._block_then_rework(rig)
    ex = d["legal_exits"]
    _show("---- 件① lybra next(BLOCK × candidate)Exits 原文 ----\n" + _exit_lines(ex))
    assert ex["state"] == "BLOCK" and ex["subject"] == "candidate" and len(ex["exits"]) >= 2
    # 执行产物本身是 BLOCK 报告 = 证据改判推荐同主体返工(non_pass_recovery 同一判据)
    assert ex["recommended"] == "rework_same_subject" and "执行产物本身是 BLOCK 报告" in ex["why"]
    assert "合法出口(BLOCK × candidate" in status_text


def test_item1_fast_lane_review_fail_and_matrix_every_cell(rig):  # noqa: F811
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(rig.gov / "5_tasks" / "policies" / f"{f221.POLICY}.md", build_autonomy_policy_markdown(
        policy_id=f221.POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f230", task_selector_task_mode="config", launch_harnesses=["pi"]))
    f78._card(rig.gov, GOVC, "pending", task_mode="config",
              extra={"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{GOVC}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{GOVC}/artifacts/"], "roles": ["executor"]}})
    f78._card(rig.gov, CODE, "pending")
    sha, _tree = f78._branch_with_commit(rig.code_repo, "PROBE-F230-S", rel="app/x.txt")
    f78._card(rig.gov, "PROBE-F230-S", "completed", extra={"harness": "claude-code"})
    f78._card(rig.gov, "PROBE-F230-V", "pending", task_mode="validation",
              extra={"harness": "claude-code", "audit_by": AUDITOR, "validation_source_task": "PROBE-F230-S", "validation_candidate": sha,
                     "output_target": "task_cards/PROBE-F230-V/artifacts/"})
    init_governance_repo(rig.gov)
    seen: list[str] = []
    res, text = f221._loop(rig, seen, lambda r: f221._report(r, "v1"))
    assert seen == ["executor"], text
    d = nr.derive_next_step(GOVC, rig.gov)
    _show("---- 件① lybra next(快车道交回待复核: fast_lane_review_fail × governance_outputs 出口)文本原文 ----\n" + nr.format_output(d))
    ex = d["legal_exits"]
    keys = [e["key"] for e in ex["exits"]]
    assert ex["state"] == "fast_lane_review_fail" and keys == ["rework_same_subject", "same_round", "new_subject", "change_scope", "withdraw"]
    assert "--outcome rework" in ex["exits"][0]["command"] and "--outcome same_round" in ex["exits"][1]["command"]

    # 表内每格逐格渲染(三种交付主体各一张卡): 适用格 ≥2 出口, 不适用格写明原因
    cards = {"branch": CODE, "candidate": "PROBE-F230-V", "governance_outputs": GOVC}
    lines = []
    for state, row in nr.legal_exits_declaration()["states"].items():
        for subject, card in cards.items():
            fm = f218._card_fm(rig.gov, card)
            got = nr.legal_exits(rig.gov, card, fm, state, actor=DRIVER)
            assert got["subject"] == subject, (card, got["subject"])
            if isinstance(row[subject], dict):
                assert not got["exits"] and got.get("not_applicable"), (state, subject)
                lines.append(f"{state} × {subject}: 不适用 — {got['not_applicable']}")
                continue
            assert len(got["exits"]) >= 2, (state, subject, got)
            lines.append(f"{state} × {subject}({got['state']}): " + ", ".join(e["key"] for e in got["exits"])
                         + (f"; 剔除 {[x['key'] for x in got['dropped']]}" if got["dropped"] else "") + f"; 推荐 {got['recommended']}")
    _show("---- 件① 非通过状态 × 三种交付主体逐格出口(各列 ≥2)----\n" + "\n".join(lines))


# ===========================================================================
# 件② config / docs 卡交付可执行程序 = 交回入口拒(LANTU-DEMO1 形态)+ 发卡预警
# ===========================================================================

def test_item2_config_card_delivering_program_refused_at_return(rig, tmp_path, monkeypatch):  # noqa: F811
    from tools.aipos_cli.artifact_ingest import program_files, validate_task_artifact
    from tools.aipos_cli.draft_validator import program_target_warning

    f78._card(rig.gov, GOVC, "claimed", task_mode="config",
              extra={"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{GOVC}/artifacts/", "claimed_by": "exec.lybra.test",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{GOVC}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    ok = validate_task_artifact(GOVC, rig.gov)
    assert ok["ok"], ok
    # 1400 行进程管理程序(无扩展名 + shebang + 可执行位)写进报告落点 artifacts/ = 拒(扩展名代码文件另由 F195 报告落点规则先拒)
    launcher = rig.gov / "task_cards" / GOVC / "artifacts" / "demo-launcher"
    _write(launcher, "#!/usr/bin/env python3\n" + "".join(f"print('step {i}')\n" for i in range(1400)))
    launcher.chmod(0o755)
    got = validate_task_artifact(GOVC, rig.gov)
    _show(f"---- 件② LANTU-DEMO1 形态交回入口原文 ok={got['ok']} category={got['category']} ----\n" + "\n".join(got.get("reasons") or []))
    assert not got["ok"] and got["category"] == "INGEST_PROGRAM_IN_NON_CODE_CARD"
    assert "1401 行, 可执行位" in got["reasons"][0] and "改走代码卡" in got["reasons"][0] and "task_mode=code" in got["reasons"][0]
    rc, out, err = _cli(["artifact", "ingest", "--task-id", GOVC, "--kind", "return", "--workspace-root", str(rig.gov), "--dry-run"])
    _show(f"---- 件② lybra artifact ingest --kind return --dry-run exit {rc} ----\n{out}{err}")
    assert rc != 0 and "INGEST_PROGRAM_IN_NON_CODE_CARD" in out + err
    # 验证根 run 目录里的程序同判(真实 LANTU-DEMO1 落点: <base>/<卡>/run-1/demo-launcher.py)
    launcher.unlink()
    run_dir = tmp_path / "vr" / GOVC / "run-1"
    _write(run_dir / "demo-launcher.py", "".join(f"x{i} = {i}\n" for i in range(1200)))
    _write(run_dir / "src" / "vendor_big.py", "".join(f"y{i} = {i}\n" for i in range(5000)))  # 送达候选源码目录 = 跳过
    _write(run_dir / "small.sh", "echo ok\n")  # 小脚本无可执行位 = 不算
    from tools.aipos_cli import validation_root

    entry = {"task_id": GOVC, "host": validation_root.local_host_label(), "runs": [{"run_id": "run-1"}]}
    real_dir = validation_root.run_directory
    monkeypatch.setattr(validation_root, "project_validation_roots", lambda root, **kw: [entry])
    monkeypatch.setattr(validation_root, "run_directory", lambda e, rid: str(run_dir) if e is entry else real_dir(e, rid))
    hits = program_files(rig.gov, GOVC, f218._card_fm(rig.gov, GOVC), {})
    monkeypatch.undo()
    _show(f"[件② 验证根 run 目录扫描(src/ 跳过, 小脚本不算)] {hits}")
    assert [Path(h["path"]).name for h in hits] == ["demo-launcher.py"] and hits[0]["lines"] == 1200
    # code / validation 卡不受限(验证驱动脚本照常)
    assert program_files(rig.gov, CODE, {"task_mode": "code"}) == [] and program_files(rig.gov, VAL, {"task_mode": "validation"}) == []
    # 发卡同判据预警
    warn = program_target_warning({"task_mode": "config", "output_target": "task_cards/X/artifacts/demo-launcher.py"})
    _show(f"[件② 发卡预警] {warn}")
    assert warn and "改出 task_mode=code" in warn
    assert program_target_warning({"task_mode": "code", "output_target": "tools/x.py"}) is None


# ===========================================================================
# 件③ 只改非验收字段(host)后返工到上限仍自动追加; 改验收命令则不自动
# ===========================================================================

def _evidence_card(rig, task: str) -> Path:
    rounds = [{"round": n, "verdict_ref": f"verdict_old_{n}", "created_at": f"2026-10-0{n}T00:00:00Z", "created_by": DRIVER,
               "focus_items": ["x"]} for n in (1, 2)]
    evidence = {"required": True, "commands": ["aapt dump badging {run_dir}/evidence/app.apk"]}
    pending = f78._card(rig.gov, task, "pending", extra={"audit_runtime_evidence": evidence})
    f225._publish_baseline(rig.gov, task, pending)
    pending.unlink()
    return f78._card(rig.gov, task, "claimed", extra={"claimed_by": "exec.lybra.test", "claimed_at": "'2026-10-01T00:00:00Z'",
                                                      "rework_rounds": rounds, "audit_runtime_evidence": evidence})


def test_item3_host_only_amend_keeps_auto_extension(rig):  # noqa: F811
    from tools.aipos_cli.queue_mutation import auto_extension_declaration

    host_card, cmd_card = "PROBE-F230-H", "PROBE-F230-C"
    _evidence_card(rig, host_card)
    _evidence_card(rig, cmd_card)
    init_governance_repo(rig.gov)
    decl = auto_extension_declaration()
    assert "audit_runtime_evidence" in decl["fingerprint_fields"] and "audit_runtime_evidence.host" in decl["scope_neutral_fields"]
    amend = {"audit_runtime_evidence": {"required": True, "commands": ["aapt dump badging {run_dir}/evidence/app.apk"], "host": "kiwiai-dev"}}
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "queue", "amend", "--task-id", host_card, "--restricted", "--actor", DRIVER,
                         "--amendments", json.dumps(amend), "--amendment-reason", "按 AIPOS-F223 仅声明运行证据执行主机"])
    _show(f"---- 件③ HBLX-42VC2 形态: queue amend --restricted 只补 audit_runtime_evidence.host exit {rc} ----\n{out[-600:]}{err}")
    assert rc == 0, out + err
    assert f218._card_fm(rig.gov, host_card)["audit_runtime_evidence"]["host"] == "kiwiai-dev"
    now = datetime.now(timezone.utc)
    vid = f222._verdict(rig.gov, host_card, 3, f221._iso(now + timedelta(seconds=5)))
    res = f222._queue_rework(rig.gov, host_card, vid, dry_run=False)
    data = res.get("data") or {}
    card = f218._card_fm(rig.gov, host_card)
    _show("---- 件③ 只改 host 后第 3 轮返工(上限 2 之后)原文 ----\n" + json.dumps(
        {"ok": res.get("ok"), "warnings": res.get("warnings"), "auto_release": data.get("auto_release"), "round": card["rework_rounds"][-1]},
        ensure_ascii=False, indent=1))
    assert res.get("ok") and card["rework_rounds"][-1]["round"] == 3 and card["rework_rounds"][-1]["auto_extension_ref"]
    assert "只改非验收字段" in json.dumps(data.get("auto_release"), ensure_ascii=False)
    # 反例: 改验收命令(commands)= 范围变化, 不自动追加(变动键点名 audit_runtime_evidence)
    changed = {"audit_runtime_evidence": {"required": True, "commands": ["echo 降级为只看文件存在"]}}
    path, _q = nr._find_task_in_queue(rig.gov, cmd_card)
    text = path.read_text(encoding="utf-8").replace("aapt dump badging {run_dir}/evidence/app.apk", changed["audit_runtime_evidence"]["commands"][0])
    path.write_text(text, encoding="utf-8")
    vid2 = f222._verdict(rig.gov, cmd_card, 3, f221._iso(now + timedelta(seconds=6)))
    res2 = f222._queue_rework(rig.gov, cmd_card, vid2, dry_run=True)
    _show(f"---- 件③ 反例·改了运行证据命令(验收)后第 3 轮 ----\n{res2.get('blocking_reasons')}")
    assert res2["verdict"] == "BLOCK" and any("自发布起已变" in r and "audit_runtime_evidence" in r for r in res2["blocking_reasons"])


# ===========================================================================
# 件④ 同轮补正(HBLX-31 形态: 治理根 docs 卡, 独立审计; 交回后审计卡未认领时顾问发现报告非终版)
# ===========================================================================

def _docs_setup(rig, monkeypatch):
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    monkeypatch.setattr(f90, "_finalize_double", f218._real_finalize)
    _write(rig.gov / "5_tasks" / "policies" / f"{f218.POLICY_D}.md", build_autonomy_policy_markdown(
        policy_id=f218.POLICY_D, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f230", task_selector_task_mode="docs", launch_harnesses=["pi"]))
    f78._card(rig.gov, DOCS, "pending", task_mode="docs",
              extra={"harness": "claude-code", "audit": "required", "audit_by": AUDITOR, "output_target": f"task_cards/{DOCS}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{DOCS}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)


def _docs_loop(rig, seen: list[str], executor, verdicts: dict, **kw):
    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        audit_id = next((a for a in sorted(verdicts, key=len, reverse=True) if f"task_cards/{a}/" in patterns), None)
        if audit_id:
            seen.append(f"auditor:{audit_id}:{verdicts[audit_id][0]}")
            f218._gov_audit(rig, audit_id, *verdicts[audit_id])
        elif any(f"task_cards/{DOCS}R" in p for p in args.expect):
            seen.append("auditor-not-started")
            return 3  # 审计尚未开始(本轮审计卡未认领 / 未出报告): 停在这里交给顾问
        else:
            seen.append("executor")
            executor(rig)
        return 0 if expect_ready([]) else 3

    out = io.StringIO()
    res = run_loop(DOCS, rig.gov, actor=DRIVER, policy_id=f218.POLICY_D, out=out, watch=watch, interval=0.05, max_wait=kw.pop("max_wait", 5),
                   max_steps=kw.pop("max_steps", 40), no_launch=True)
    return res, out.getvalue()


def test_item4_same_round_before_audit_starts(rig, monkeypatch):  # noqa: F811
    _docs_setup(rig, monkeypatch)
    from tools.aipos_cli.board_adapter import mark_concluded_task

    seen: list[str] = []
    res, text = _docs_loop(rig, seen, lambda r: f218._gov_return(r, "草稿版(非终版)"), {}, max_steps=3)
    _show(f"---- 件④ HBLX-31 形态首跑(交回 → 门派审 R)exit {res.exit_code} reason {res.reason} ----\n" + _quiet(text))
    recs = nr._read_task_records(rig.gov, DOCS)
    first_return = str(recs["latest_return"]["return_id"])
    _p, r_state = nr._find_task_in_queue(rig.gov, f"{DOCS}R")
    assert r_state == "pending", f"本轮审计卡 {DOCS}R 应未认领(审计未开始), 实为 {r_state}"
    rounds_before = f218._card_fm(rig.gov, DOCS).get("rework_rounds") or []

    focus = json.dumps(["报告是草稿版, 补完第 3 节结论并更新 findings.md"], ensure_ascii=False)
    dry = mark_concluded_task(task_id=DOCS, actor=DRIVER, report_path=f"task_cards/{DOCS}/RETURN.md", dry_run=True, repo_root=rig.gov,
                              outcome="same_round", focus_items=focus)
    _show("---- 件④ mark-concluded --outcome same_round --dry-run 原文 ----\n"
          + json.dumps({k: dry.get(k) for k in ("ok", "verdict", "warnings")} | {"data": dry.get("data")}, ensure_ascii=False, indent=1)[:2500])
    assert dry["ok"], json.dumps(dry.get("errors") or dry, ensure_ascii=False)[:1500]
    assert dry["data"]["linked_audits"][0]["audit_task_id"] == f"{DOCS}R" and dry["data"]["linked_audits"][0]["planned"]
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "mark-concluded", "--task-id", DOCS, "--actor", DRIVER, "--report-path",
                         f"task_cards/{DOCS}/RETURN.md", "--outcome", "same_round", "--focus-items", focus, "--json"])
    payload = json.loads(out)
    _show(f"---- 件④ mark-concluded --outcome same_round(写入)exit {rc} ----\n" + json.dumps(payload.get("data"), ensure_ascii=False, indent=1)[:2500])
    assert rc == 0 and payload["ok"]
    assert (f218._card_fm(rig.gov, DOCS).get("rework_rounds") or []) == rounds_before, "同轮补正不新增返工轮"
    _p, r_state = nr._find_task_in_queue(rig.gov, f"{DOCS}R")
    assert r_state == "withdrawn" and payload["data"]["review_record"]["frontmatter"]["withdrawn_audit_task_id"] == f"{DOCS}R"
    wait = nr.derive_next_step(DOCS, rig.gov)
    _show("---- 件④ 同轮打回后 lybra next 原文 ----\n" + nr.format_output(wait))
    assert wait["action"]["type"] == "await_rework" and wait["current_node"] == "same_round" and "不占返工轮" in wait["missing_records"][0]
    assert "同轮打回于" in wait["suggested_action"] and "第 ? 轮" not in wait["suggested_action"]
    # 同一交回重复同轮打回 = 拒
    again = mark_concluded_task(task_id=DOCS, actor=DRIVER, dry_run=True, repo_root=rig.gov, outcome="same_round", focus_items=focus)
    assert not again["ok"] and "SAME_ROUND_ALREADY_PENDING" in json.dumps(again, ensure_ascii=False)

    # 执行体补正重写交回 → loop 重交回 → 门派 R2 取代被撤 R → PASS → 结案
    seen2: list[str] = []
    res2, text2 = _docs_loop(rig, seen2, lambda r: f218._gov_return(r, "终版"), {f"{DOCS}R2": ("PASS", "逐条复核通过")})
    _show(f"---- 件④ loop 续跑(补正 → 重交回 → R2 → 结案)exit {res2.exit_code} ----\n" + _quiet(text2))
    assert res2.exit_code == 0 and res2.outcome == "completed", text2
    assert seen2 == ["executor", f"auditor:{DOCS}R2:PASS"] and f"run return @ same_round/claimed ({DOCS})" in text2
    recs = nr._read_task_records(rig.gov, DOCS)
    assert recs["latest_return"]["return_id"] != first_return and recs["latest_verdict"]["audit_task_id"] == f"{DOCS}R2"
    dispatch = nr._find_latest_record(rig.gov / "5_tasks" / "records" / "audit_dispatches" / f"{DOCS}R2", "dispatch") or {}
    _show(f"[件④ R2 派审记录 supersedes] {dispatch.get('supersedes')}")
    assert dispatch.get("supersedes") == f"{DOCS}R"
    assert (f218._card_fm(rig.gov, DOCS).get("rework_rounds") or []) == rounds_before
    assert f90._records(rig.gov, "closures", DOCS)


def test_item4_same_round_refused_once_audit_started(rig, monkeypatch):  # noqa: F811
    _docs_setup(rig, monkeypatch)
    from tools.aipos_cli.board_adapter import mark_concluded_task

    seen: list[str] = []
    res, text = _docs_loop(rig, seen, lambda r: f218._gov_return(r, "v1"), {f"{DOCS}R": ("FAIL", "FAIL — 遗漏两处密钥引用点")})
    assert f"auditor:{DOCS}R:FAIL" in seen, text
    got = mark_concluded_task(task_id=DOCS, actor=DRIVER, dry_run=True, repo_root=rig.gov, outcome="same_round", focus_items='["x"]')
    reason = json.dumps(got, ensure_ascii=False)
    _show(f"[件④ 审计已出裁决后同轮补正 = 拒] {got.get('errors')}")
    assert not got["ok"] and "SAME_ROUND_AUDIT_STARTED" in reason and "合法出口" in reason


def test_item4_same_round_fast_lane_back_to_review(rig):  # noqa: F811
    """快车道卡(LANTU-DEMO1 形态, 无独立审计): 复核前发现报告非终版 = 同轮补正, 不占返工轮; 补正重交回后回到顾问复核。"""
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(rig.gov / "5_tasks" / "policies" / f"{f221.POLICY}.md", build_autonomy_policy_markdown(
        policy_id=f221.POLICY, agent_or_role="advisor", active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope-f230", task_selector_task_mode="config", launch_harnesses=["pi"]))
    f78._card(rig.gov, GOVC, "pending", task_mode="config",
              extra={"harness": "claude-code", "audit": "none", "output_target": f"task_cards/{GOVC}/artifacts/",
                     "lane": {"repo": "governance_root", "paths": [f"task_cards/{GOVC}/artifacts/"], "roles": ["executor"]}})
    init_governance_repo(rig.gov)
    seen: list[str] = []
    f221._loop(rig, seen, lambda r: f221._report(r, "草稿"))
    first = str(nr._read_task_records(rig.gov, GOVC)["latest_return"]["return_id"])
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "mark-concluded", "--task-id", GOVC, "--actor", DRIVER, "--report-path",
                         f"task_cards/{GOVC}/RETURN.md", "--outcome", "same_round", "--focus-items", '["补完配置说明第 2 节"]', "--json"])
    payload = json.loads(out)
    _show(f"---- 件④ 快车道卡同轮补正 exit {rc} ----\n" + json.dumps(payload.get("data", {}).get("review_record"), ensure_ascii=False)[:800])
    assert rc == 0 and payload["ok"] and not payload["data"]["linked_audits"]
    wait = nr.derive_next_step(GOVC, rig.gov)
    assert wait["action"]["type"] == "await_rework" and "回到顾问复核" in wait["suggested_action"]
    seen2: list[str] = []
    res2, text2 = f221._loop(rig, seen2, lambda r: f221._report(r, "终版"))
    _show(f"---- 件④ 快车道卡补正后续跑 exit {res2.exit_code} reason {res2.reason} ----\n" + _quiet(text2))
    back = nr.derive_next_step(GOVC, rig.gov)
    assert seen2 == ["executor"] and f"run return @ same_round/claimed ({GOVC})" in text2
    assert str(nr._read_task_records(rig.gov, GOVC)["latest_return"]["return_id"]) != first
    assert back["verb"] == "lybra_mark_concluded" and not (f218._card_fm(rig.gov, GOVC).get("rework_rounds") or [])


# ===========================================================================
# 件⑤ supersede / withdraw 业务卡连带撤在途审计卡
# ===========================================================================

def test_item5_supersede_and_withdraw_take_in_flight_audit_cards(rig):  # noqa: F811
    biz, new, other = "PROBE-F230-B", "PROBE-F230-N", "PROBE-F230-W"
    def claim(task: str, actor: str) -> dict:
        slug = actor.replace(".", "-")
        return {"claimed_by": actor, "claim_id": f"claim_{task}_20261024_000000_{slug}", "claimed_at": "'2026-10-24T00:00:00Z'",
                "active_session_id": f"session_{task}_20261024_000000_{slug}"}

    f78._card(rig.gov, biz, "claimed", extra=claim(biz, "exec.lybra.test"))
    f78._card(rig.gov, new, "pending")
    f78._card(rig.gov, f"{biz}R", "claimed", task_mode="audit",
              extra={"reviewed_task_id": biz, "audit": "", "audit_by": "", **claim(f"{biz}R", AUDITOR)})
    f78._card(rig.gov, other, "pending")
    f78._card(rig.gov, f"{other}R", "pending", task_mode="audit", extra={"reviewed_task_id": other, "audit": "", "audit_by": ""})
    init_governance_repo(rig.gov)
    base = ["--workspace-root", str(rig.gov), "queue", "supersede", "--task-id", biz, "--by", new, "--actor", DRIVER,
            "--owner-words", "Owner: 本卡目标改为新卡承接", "--json"]
    rc, out, err = _cli(base)
    pre = json.loads(out)
    _show(f"---- 件⑤ queue supersede 预演 exit {rc} ----\n" + json.dumps({k: pre.get(k) for k in ("line", "warnings", "linked_audits")},
                                                                       ensure_ascii=False, indent=1))
    assert rc == 0 and [x["audit_task_id"] for x in pre["linked_audits"]] == [f"{biz}R"] and pre["linked_audits"][0]["planned"]
    assert nr._find_task_in_queue(rig.gov, f"{biz}R")[1] == "claimed", "预演零写入"
    rc, out, err = _cli(base + ["--confirm"])
    done = json.loads(out)
    _show(f"---- 件⑤ queue supersede --confirm exit {rc} ----\n" + json.dumps(
        {k: done.get(k) for k in ("line", "warnings", "linked_audits", "landing")}, ensure_ascii=False, indent=1)[:3000])
    assert rc == 0 and done["ok"] and nr._find_task_in_queue(rig.gov, biz)[1] == "withdrawn"
    assert nr._find_task_in_queue(rig.gov, f"{biz}R")[1] == "withdrawn" and done["linked_audits"][0]["ok"]
    audit_fm = f218._card_fm(rig.gov, f"{biz}R")
    assert f"随业务卡 {biz} supersede 连带撤回在途审计卡" in str(audit_fm.get("withdrawal_reason"))
    assert any(f"{biz.lower()}r.md" in str(p) for p in done["written"]), "连带撤回的审计卡随业务卡落账"

    rc, out, err = _cli(["--workspace-root", str(rig.gov), "queue", "withdraw", "--task-id", other, "--actor", DRIVER, "--reason",
                         "Owner: 不做了", "--dry-run", "--json"])
    pre_w = json.loads(out)
    _show(f"---- 件⑤ queue withdraw 预演 exit {rc} ----\n" + json.dumps(pre_w.get("data", {}).get("linked_audits"), ensure_ascii=False))
    assert [x["audit_task_id"] for x in pre_w["data"]["linked_audits"]] == [f"{other}R"]
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "queue", "withdraw", "--task-id", other, "--actor", DRIVER, "--reason",
                         "Owner: 不做了", "--json"])
    done_w = json.loads(out)
    _show(f"---- 件⑤ queue withdraw 写入 exit {rc} ----\n" + json.dumps(done_w.get("data", {}).get("linked_audits"), ensure_ascii=False)[:1500])
    assert rc == 0 and nr._find_task_in_queue(rig.gov, f"{other}R")[1] == "withdrawn"


def test_f230_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f230_legal_exits.py"
    assert rel in discover_test_files([rel], default_test_contract())
