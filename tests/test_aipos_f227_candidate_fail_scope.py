"""AIPOS-F227 — 固定候选验证卡 FAIL 区分「候选缺陷」与「交付物缺陷」(10-24 chris HBLX-45V 实撞: 58 项测试全过, 仅 3 份声明
artifacts 未落盘被 FAIL, next 却给「候选本身不合格 → 新候选 + 另出验证卡 + supersede」, 原因 not provided)。

件① 审计报告 frontmatter 可声明 fail_scope ∈ {candidate, deliverable}(声明 transitions nodes.N4.candidate_fail.fail_scope; 推导核读该裁决的
    门快照报告, next_resolver.verdict_fail_scope 唯一实现); 值不在声明内 = 裁决入口拒收(INGEST_FAIL_SCOPE_INVALID); 未声明 = 按 candidate
    (兼容)但 next / loop status 同时给同候选补正出口(判据: 审计发现全部指向交付物、无任何候选行为缺陷)。
件② fail_scope=deliverable = 同候选补正出口(action / end_reason = candidate_deliverable_fail, advisor_fix): 登记返工轮(queue rework, 返工命令
    唯一渲染 next_resolver.rework_command_line)→ loop 拉起执行体在同一候选上补交付物 → 重交回(F218 候选主体判据: commit_sha = 卡面候选)
    → 派下一轮审计; candidate_fail 结束原因只在 candidate(或未声明)时出现; 裁决记录无 Findings summary(not provided)= 原因读审计报告
    裁决依据(nodes.N4.candidate_fail.report_reason)。

靶场 = AIPOS-F218 验证卡靶场(F90 进程内门 + loop 全链; 临时治理根 + 临时产品仓 + 临时 HOME; 禁真门 / 真治理根 / 真工位 / 起 pi), 卡面自造
人肉期 HBLX-45V 形态(固定候选验证卡、执行交回测试全过、审计 FAIL 仅因声明产物缺失)。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f218_candidate_rework_recovery as f218  # noqa: E402  — 验证卡靶场唯一来源(禁第二份)
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
from test_aipos_f218_candidate_rework_recovery import VAL, _loop, _pick, _quiet, _show, _status, vrig  # noqa: E402,F401
from test_aipos_f78_engine_agnostic import DRIVER, _fm, _git, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402

PASS_RETURN = "PASS — 5 条声明命令全部执行成功, 58 项测试 0 失败(17 Original + 41 Simulation)"
DELIVERABLE_BASIS = ("**裁决: FAIL** — 验证工作本身全部通过(58 项测试 0 失败), 但被审卡声明的 3 份报告产物未交付到 artifacts 目录。"
                     "执行体需补写 software-readiness.json、interface-coverage.md、installation-readiness.md")
CANDIDATE_BASIS = "**裁决: FAIL** — 候选 OTA 脚本 datetime 解析缺陷, 联合验证第 3 步崩溃(属实, 非环境)"


def _audit_45v(rig, audit_id: str, verdict: str, basis: str, scope: str | None) -> None:
    """HBLX-45VR 形态审计报告(发现清单 + 裁决依据节); scope = frontmatter fail_scope(None = 不写, 旧报告形)。"""
    report = rig.gov / "task_cards" / audit_id / "audit_report.md"
    head = {"task_id": audit_id, "reviewed_task_id": VAL, "verdict": verdict, "commit_sha": rig.candidate, "model": f90.SELF_AUDIT_MODEL}
    if scope is not None:
        head["fail_scope"] = scope
    _write(report, _fm(head, f"# 审计报告 {audit_id}\n\n## 审计概要\n\n- 被审卡 {VAL}\n\n## 发现清单\n\n"
                             "| 编号 | 级别 | 描述 |\n|---|---|---|\n| F-1-1 | P1 | artifacts 缺失: software-readiness.json 未落盘 |\n\n"
                             f"## 裁决依据\n\n**通过项**:\n- 58 项测试全部通过\n\n{basis}\n\n## 模型与资源\n\n- 夹具\n"))
    f90._pi_session(rig.home, rig.home / "workstations" / "probe-auditor", report, f90.RUNTIME_AUDIT_MODEL,
                    name=f"sess-{audit_id}-{verdict}-{time.time_ns()}.jsonl")


def _return_with_artifacts(rig, conclusion: str, *, artifacts: bool, commit_sha: str | None = None) -> Path:
    slot = rig.gov / "task_cards" / VAL
    if artifacts:
        for name in ("software-readiness.json", "interface-coverage.md", "installation-readiness.md"):
            _write(slot / "artifacts" / name, f"{name} 补交\n")
    return f218._return(rig, conclusion, commit_sha=commit_sha)


def _watch(rig, seen: list[str], *, executor, verdicts: dict[str, tuple[str, str, str | None]]):
    def watch(args, expect_ready, deploy_guard=None):
        patterns = " ".join(args.expect)
        audit_id = next((a for a in sorted(verdicts, key=len, reverse=True) if f"task_cards/{a}/" in patterns), None)
        if audit_id:
            verdict, basis, scope = verdicts[audit_id]
            seen.append(f"auditor:{audit_id}:{verdict}:{scope}")
            _audit_45v(rig, audit_id, verdict, basis, scope)
        else:
            seen.append("executor")
            executor(rig)
        return 0 if expect_ready([]) else 3

    return watch


def _first_fail(rig, scope: str | None, basis: str = DELIVERABLE_BASIS):
    """HBLX-45V 形态首跑: 执行交回(测试全过, 声明产物未落盘)→ 审计 FAIL(fail_scope = scope)→ loop 结束。"""
    seen: list[str] = []
    res, text = _loop(rig, _watch(rig, seen, executor=lambda r: _return_with_artifacts(r, PASS_RETURN, artifacts=False),
                                  verdicts={f"{VAL}R": ("FAIL", basis, scope)}))
    _show(f"---- lybra loop 首跑原文(HBLX-45V 形态, 审计 FAIL fail_scope={scope})exit {res.exit_code} reason {res.reason} ----\n"
          + _quiet(text))
    assert seen == ["executor", f"auditor:{VAL}R:FAIL:{scope}"], seen
    d = nr.derive_next_step(VAL, rig.gov)
    _show(f"---- lybra next(N4 FAIL, fail_scope={scope}, 未登记返工)原文 ----\n"
          + _pick(d, "derivable", "current_node", "triggered_by", "suggested_action", "notes", "action"))
    return res, text, d


# ===========================================================================
# 件①②: fail_scope=deliverable → 同候选返工出口 → queue rework → 补交付物重交回(候选不变)→ R2 → PASS → 结案
# ===========================================================================

def test_deliverable_scope_same_candidate_rework_re_return_dispatches_r2(vrig):
    rig = vrig
    res, text, d = _first_fail(rig, "deliverable")
    assert res.reason == "candidate_deliverable_fail" and "candidate_fail" not in (res.reason or "").replace("candidate_deliverable_fail", "")
    assert d["action"]["type"] == "candidate_deliverable_fail" and d["action"]["end_reason"] == "candidate_deliverable_fail"
    assert d["triggered_by"] == "advisor" and d["derivable"] is False
    said = d["suggested_action"]
    assert "queue supersede" not in said and "另出验证卡承接新候选" not in said and "候选本身不合格" not in said
    assert "不换候选、不另出验证卡、不 supersede" in said
    assert f"同一固定候选 {rig.candidate}" in said and "queue rework" in said and "fail_scope=deliverable" in said
    # 原因不再是 not provided: 裁决记录无 Findings summary → 读审计报告裁决依据
    verdict_path, verdict_fm = nr._regular_verdicts(rig.gov, VAL)[0]
    assert verdict_fm.get("findings_summary_present") is False, "靶场裁决记录与 HBLX-45V 同形(Findings summary: not provided)"
    assert "not provided" not in said and "3 份报告产物未交付到 artifacts 目录" in said
    rework = d["action"]["rework_command"]
    assert rework.startswith(f"lybra --workspace-root {rig.gov} queue rework --task-id {VAL} --actor {DRIVER} "
                             f"--verdict-ref {d['action']['verdict_id']} ") and rework in said

    status_text, status = _status(rig.gov)
    _show("---- lybra loop status(fail_scope=deliverable)原文 ----\n" + status_text)
    na = status["next_action"]
    assert na["action"] == "advisor_fix" and na["reason"] == "candidate_deliverable_fail", na
    # AIPOS-F230 件①: loop status 末尾另附全部合法出口(含换新主体 supersede); 例行修法本身仍只给同候选补正
    routine_text = status_text.split("合法出口(")[0]
    assert rework in routine_text and "queue supersede" not in routine_text and "3 份报告产物未交付" in routine_text
    assert "合法出口(candidate_deliverable_fail × candidate" in status_text

    # 顾问照抄返工命令(补 focus / 验收)登记返工轮: 预演 → --confirm
    argv = shlex.split(rework)[1:]
    argv[argv.index("--focus-items") + 1] = json.dumps(["补写 software-readiness.json / interface-coverage.md / installation-readiness.md"],
                                                       ensure_ascii=False)
    argv[argv.index("--acceptance-criteria") + 1] = json.dumps(["3 份声明产物落在报告落点 artifacts/"], ensure_ascii=False)
    rc, out, err = _cli(argv)
    _show(f"---- lybra queue rework(按 next 给出的返工命令, 缺省预演)exit {rc} ----\n{out}{err}")
    assert rc == 0, err
    rc, out, err = _cli(argv + ["--confirm"])
    _show(f"---- lybra queue rework --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0, err
    wait = nr.derive_next_step(VAL, rig.gov)
    _show("---- lybra next(登记返工后, 执行体尚未补交付物)原文 ----\n"
          + _pick(wait, "derivable", "current_node", "triggered_by", "missing_records", "suggested_action", "action"))
    assert wait["action"] == {"type": "await_rework", "card": VAL, "round": 1}
    assert f"同一固定候选 {rig.candidate}" in wait["suggested_action"] and "新 tip" not in wait["suggested_action"]

    # loop 续跑: 执行体在同一候选上补交付物并重写交回 → 重交回(verdict_stale 同一出口)→ 门派 R2 → PASS → 无合入结案
    seen: list[str] = []
    res2, text2 = _loop(rig, _watch(rig, seen, executor=lambda r: _return_with_artifacts(r, PASS_RETURN + "; 3 份声明产物已补交", artifacts=True),
                                    verdicts={f"{VAL}R2": ("PASS", "**裁决: PASS** — 产物齐全, 逐条复核通过", None)}))
    _show(f"---- lybra loop 续跑原文(补交付物 → 重交回 → R2 → 结案)exit {res2.exit_code} ----\n" + _quiet(text2))
    assert res2.exit_code == 0 and res2.outcome == "completed", text2
    assert seen == ["executor", f"auditor:{VAL}R2:PASS:None"], seen
    assert f"run return @ verdict_stale/claimed ({VAL})" in text2
    recs = nr._read_task_records(rig.gov, VAL)
    assert recs["audit_round_ids"][:2] == [f"{VAL}R", f"{VAL}R2"] and recs["latest_verdict"]["audit_task_id"] == f"{VAL}R2"
    subjects = [nr._read_frontmatter(p).get("artifact_subject") for p in sorted(f90._records(rig.gov, "returns", VAL))
                if p.name.startswith("return_")]
    _show(f"[两份门交回记录绑定(候选不变)] {json.dumps(subjects, ensure_ascii=False)}")
    assert len(subjects) == 2 and all(s and s["kind"] == "candidate" and s["commit_sha"] == rig.candidate for s in subjects)
    assert subprocess_free_branch(rig), "固定候选卡全程不建卡分支"
    assert f90._records(rig.gov, "closures", VAL)


def subprocess_free_branch(rig) -> bool:
    import subprocess

    return subprocess.run(["git", "-C", str(rig.code_repo), "rev-parse", "--verify", "--quiet", f"card/{VAL}"],
                          capture_output=True).returncode != 0


def test_deliverable_rework_re_return_other_sha_is_candidate_mismatch(vrig):
    """回归: 同候选补正后重交回的 commit_sha 不是卡面候选 = artifact_invalid(INGEST_CANDIDATE_MISMATCH 原文)。"""
    rig = vrig
    _res, _text, d = _first_fail(rig, "deliverable")
    argv = shlex.split(d["action"]["rework_command"])[1:]
    assert _cli(argv + ["--confirm"])[0] == 0
    other = _git(rig.code_repo, "rev-parse", "main")
    _return_with_artifacts(rig, PASS_RETURN + "(但报告头抄成了别的提交)", artifacts=True, commit_sha=other)
    bad = nr.derive_next_step(VAL, rig.gov)
    _show("---- lybra next(补交付物重交回但 commit_sha 不是卡面固定候选)原文 ----\n"
          + _pick(bad, "derivable", "current_node", "missing_records", "suggested_action", "action"))
    assert bad["action"]["type"] == "artifact_invalid" and bad["current_node"] == "verdict_stale"
    assert any("INGEST_CANDIDATE_MISMATCH" in m and other in m and rig.candidate in m for m in bad["missing_records"])


# ===========================================================================
# 回归: fail_scope=candidate → 仍 candidate_fail + supersede; 未声明 → candidate_fail 兼容 + 两出口并列
# ===========================================================================

def test_candidate_scope_still_candidate_fail_supersede(vrig):
    rig = vrig
    res, _text, d = _first_fail(rig, "candidate", CANDIDATE_BASIS)
    assert res.reason == "candidate_fail" and d["action"]["type"] == "candidate_fail" and d["action"]["end_reason"] == "candidate_fail"
    said = d["suggested_action"]
    assert "候选本身不合格" in said and "queue supersede" in said and "fail_scope=candidate" in said
    assert "queue rework" not in said and "另一出口" not in said, "已声明 candidate 不给同候选补正出口"
    assert "datetime 解析缺陷" in said and "not provided" not in said
    status_text, status = _status(rig.gov)
    _show("---- lybra loop status(fail_scope=candidate)原文 ----\n" + status_text)
    assert status["next_action"]["reason"] == "candidate_fail" and f"queue supersede --task-id {VAL} --by <新验证卡号>" in status_text
    assert "queue rework" not in status_text.split("合法出口(")[0]  # AIPOS-F230 件①: 合法出口全集另列(含同主体返工)


def test_undeclared_scope_compat_candidate_fail_with_both_exits(vrig):
    rig = vrig
    res, _text, d = _first_fail(rig, None)
    assert res.reason == "candidate_fail" and d["action"]["type"] == "candidate_fail", "未声明 = 按 candidate(兼容)"
    said = d["suggested_action"]
    assert "queue supersede" in said and "审计未声明 fail_scope" in said
    assert "另一出口" in said and "无任何候选行为缺陷" in said and d["action"]["rework_command"] in said
    assert "3 份报告产物未交付" in said and "not provided" not in said
    status_text, status = _status(rig.gov)
    _show("---- lybra loop status(未声明 fail_scope)原文 ----\n" + status_text)
    assert status["next_action"]["reason"] == "candidate_fail"
    assert f"queue supersede --task-id {VAL} --by <新验证卡号>" in status_text and d["action"]["rework_command"] in status_text
    # 顾问按判据选同候选补正: 照抄返工命令登记返工轮 → 同一 await_rework(复用 F218 路径)
    assert _cli(shlex.split(d["action"]["rework_command"])[1:] + ["--confirm"])[0] == 0
    wait = nr.derive_next_step(VAL, rig.gov)
    assert wait["action"] == {"type": "await_rework", "card": VAL, "round": 1}


def test_ingest_refuses_undeclared_fail_scope_value(vrig):
    """fail_scope 值不在声明内 = 裁决入口拒收(fail-closed), 拒因点名声明值; loop 不提交裁决。"""
    rig = vrig
    seen: list[str] = []
    res, text = _loop(rig, _watch(rig, seen, executor=lambda r: _return_with_artifacts(r, PASS_RETURN, artifacts=False),
                                  verdicts={f"{VAL}R": ("FAIL", DELIVERABLE_BASIS, "report_only")}), max_steps=8)
    _show(f"---- lybra loop(审计报告 fail_scope=report_only)原文 exit {res.exit_code} reason {res.reason} ----\n" + _quiet(text))
    rc, out, err = _cli(["artifact", "ingest", "--task-id", f"{VAL}R", "--kind", "verdict", "--workspace-root", str(rig.gov), "--json"])
    _show(f"---- lybra artifact ingest --kind verdict(fail_scope=report_only)exit {rc} ----\n{out}{err}")
    assert "INGEST_FAIL_SCOPE_INVALID" in out + err and "report_only" in out + err and "deliverable" in out + err
    assert not nr._regular_verdicts(rig.gov, VAL), "拒收 = 未铸裁决记录"


# ===========================================================================
# 件①: 审计侧说明(审计卡取证锚点 + 审计开工稿交回节, 同一渲染 next_resolver.fail_scope_audit_rule); 非固定候选卡不出
# ===========================================================================

def test_audit_rule_rendered_in_audit_card_and_kickoff(vrig):
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section

    rig = vrig
    _first_fail(rig, "deliverable")
    rule = nr.fail_scope_audit_rule()
    assert "candidate" in rule and "deliverable" in rule and "无任何候选行为缺陷" in rule
    audit_card, _q = nr._find_task_in_queue(rig.gov, f"{VAL}R")
    card_text = audit_card.read_text(encoding="utf-8")
    line = next(ln for ln in card_text.splitlines() if "**FAIL 范围**" in ln)
    _show(f"[审计卡取证锚点 FAIL 范围行] {line}")
    assert rule in line
    # 审计开工稿交回节: 布局声明 return_fail_scope 在报告必填行之后(紧跟报告落点代码文件规则, F195 顺序不动), 值 = 同一渲染
    # (被审卡非固定候选 = None, 该行省略)
    from tools.aipos_cli.subagent_kickoff import _fail_scope_rule

    kickoff = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]["lybra_kickoff"]
    layout = kickoff["layout"]["auditor"]
    assert layout.index("return_fail_scope") == layout.index("return_code_files") + 1 == layout.index("report_field_lines") + 2
    assert kickoff["texts"]["return_fail_scope"] == "- {fail_scope_rule}"
    val_path, _q = nr._find_task_in_queue(rig.gov, VAL)
    src_path, _q = nr._find_task_in_queue(rig.gov, f218.SRC)
    _show(f"[审计开工稿交回节 fail_scope 行] - {_fail_scope_rule(val_path)}")
    assert _fail_scope_rule(val_path) == rule and _fail_scope_rule(src_path) is None and "return_fail_scope" not in kickoff["layout"]["executor"]
    plain = build_forensic_anchor_section(f218.SRC, rig.gov, nr._read_frontmatter(src_path), audit_task_id=f"{f218.SRC}R")
    assert "FAIL 范围" not in plain, "非固定候选被审卡不出 fail_scope 说明"


# ===========================================================================
# 声明单源 / 单一判定
# ===========================================================================

def test_declarations_single_source_and_single_judgement(tmp_path):
    decl = nr.candidate_fail_declaration()
    scope = decl["fail_scope"]
    assert set(scope["values"]) == {"candidate", "deliverable"} and scope["default"] == "candidate" and scope["report_key"] == "fail_scope"
    assert scope["deliverable_end_reason"] != decl["end_reason"]
    assert nr.fail_scope_problem({"fail_scope": "deliverable"}) is None and nr.fail_scope_problem({}) is None
    assert "report_only" in (nr.fail_scope_problem({"fail_scope": "report_only"}) or "")
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    status = verbs["verbs"]["lybra_loop_status"]["next_action"]
    end = scope["deliverable_end_reason"]
    assert status["by_end_reason"][end] == "advisor_fix" and end in status["routine_rejections"]["end_reasons"]
    assert end in verbs["verbs"]["lybra_loop"]["run_record"]["end_reasons"]
    assert {"fail_scope_note", "deliverable_exit"} <= set(status["routine_rejections"]["placeholders"])
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert src.count("candidate_fail_state(workspace_root, task_id, fm)") == 1, "N4 分支一处判定"
    assert src.count("decl[\"rework_command\"].format(") == 1, "返工命令唯一渲染 rework_command_line"
    # 原因摘录兜底: 裁决记录 Findings summary 缺(not provided)= 读报告裁决依据; 有摘要 = 照旧
    report = tmp_path / "r.md"
    report.write_text("---\nverdict: FAIL\n---\n# R\n\n## 裁决依据\n\n**通过项**: x\n\n" + DELIVERABLE_BASIS + "\n", encoding="utf-8")
    rec = tmp_path / "v.md"
    rec.write_text("---\nverdict: FAIL\nfindings_summary_present: false\nreport_source_ref: r.md\n---\n# V\n\n- Findings summary: not provided\n",
                   encoding="utf-8")
    got = nr._verdict_reason_excerpt(rec, nr._read_frontmatter(rec), workspace_root=tmp_path)
    assert got.startswith("裁决: FAIL — 验证工作本身全部通过") and "**" not in got
    rec.write_text("---\nverdict: FAIL\nreport_source_ref: r.md\n---\n# V\n\n- Findings summary: 摘要原文\n", encoding="utf-8")
    assert nr._verdict_reason_excerpt(rec, nr._read_frontmatter(rec), workspace_root=tmp_path) == "摘要原文"


def test_f227_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f227_candidate_fail_scope.py"
    assert rel in discover_test_files([rel], default_test_contract())
