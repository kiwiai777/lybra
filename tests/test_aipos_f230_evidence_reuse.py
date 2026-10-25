"""AIPOS-F230 件⑥ — 审计可复用自己上一轮对同一候选的运行证据(10-24 chris HBLX-45V 实撞: 只补材料的返工轮, 审计仍须对同一固定候选完整
重新构建 / 重跑)。同一候选、上一轮裁决 fail_scope=deliverable(或返工节声明 focus_scope=deliverable)时, 运行记录条目写 reused_from =
上一轮审计卡, 裁决入口(board_adapter._candidate_run_problem; 资格唯一判定 next_resolver.audit_evidence_reuse)核被引轮次 / 实例 / 资格,
其余照本机 run 同一核对。靶场 = F199 验证根靶场(F196 vrig: 本机模拟目标主机、假 ssh; 禁真门 / 真治理根 / 真工位)。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f199_validation_audit_run_evidence as f199  # noqa: E402  — 验证根运行证据构件唯一来源
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
from test_aipos_f196_validation_to_pi import _show, vrig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f78_engine_agnostic import AUDITOR, _fm, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401
from tools.aipos_cli import next_resolver as nr  # noqa: E402


# ===========================================================================
# 件⑥ deliverable 返工审计复用上一轮对同一候选的运行证据
# ===========================================================================

def test_item6_deliverable_rework_audit_reuses_previous_round_evidence(vrig):  # noqa: F811
    vr = vrig
    gov = vr.gov
    candidate, tree = f199._setup_cards(vr)
    decl = f199._declare(gov, vr.base, f199.VALR, AUDITOR)
    f199._run_start(gov, decl, candidate)
    run_dir = f199._prepare_run(gov, decl)
    entries = f199._exec_commands(run_dir, candidate, tree)
    assert f199._check(gov, f199.VALR, f199.VAL, candidate, entries) is None
    # 上一轮 R 裁决 FAIL, fail_scope=deliverable(只缺交付物); 本轮 R2 为返工后的复审轮
    f222_vid = "verdict_" + f199.VAL + "_r1"
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / f199.VAL / f"{f222_vid}.md", _fm({
        "record_type": "audit_verdict_record", "verdict_id": f222_vid, "verdict": "FAIL", "reviewed_task_id": f199.VAL,
        "audit_task_id": f199.VALR, "verdict_at": "'2026-10-24T05:00:00Z'", "actor": AUDITOR, "fail_scope": "deliverable"}))
    f78._card(gov, f199.VALR, "completed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": f199.VAL, "claimed_by": AUDITOR, "harness": "pi", "audit": "", "audit_by": ""})
    r2 = f"{f199.VAL}R2"
    f78._card(gov, r2, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": f199.VAL, "claimed_by": AUDITOR, "harness": "pi", "audit": "", "audit_by": ""})
    line = nr.audit_evidence_reuse_line(gov, r2, candidate=candidate, reviewed=f199.VAL)
    _show(f"[件⑥ 审计开工稿 / 取证锚点复用说明] {line}")
    assert line and f"上一轮 {f199.VALR}" in line and "fail_scope=deliverable" in line
    # 不写 reused_from = 照旧拒(他卡 run)
    plain = f199._check(gov, r2, f199.VAL, candidate, entries)
    _show("[件⑥ 未声明复用 = 照旧拒]\n  " + "\n  ".join(plain["reasons"]))
    assert plain and "不是本审计卡" in plain["reasons"][0]
    reused = [{**e, "reused_from": f199.VALR} for e in entries]
    ok = f199._check(gov, r2, f199.VAL, candidate, reused)
    _show(f"[件⑥ 声明 reused_from={f199.VALR}: 裁决入口判据] {ok}")
    assert ok is None
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    f199._report(gov, r2, f199.VAL, candidate, reused)
    got = validate_task_artifact(r2, gov)
    _show(f"---- 件⑥ R2 审计报告(复用上一轮证据)入门原文 ok={got['ok']} category={got['category']} reasons={got.get('reasons')} ----")
    assert got["ok"] and got["category"] == "OK"
    # 反例: 上一轮 fail_scope=candidate(候选缺陷)= 不可复用
    path = gov / "5_tasks" / "records" / "audit_verdicts" / f199.VAL / f"{f222_vid}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("fail_scope: deliverable", "fail_scope: candidate"), encoding="utf-8")
    bad = f199._check(gov, r2, f199.VAL, candidate, reused)
    _show("[件⑥ 反例·上一轮判候选缺陷 = 复用拒]\n  " + "\n  ".join(bad["reasons"]))
    assert bad and "本轮返工不只涉交付物" in bad["reasons"][0]
    # 反例: 引用的不是更早一轮
    wrong = [{**e, "reused_from": f199.VAL2R} for e in entries]
    bad2 = f199._check(gov, r2, f199.VAL, candidate, wrong)
    assert bad2 and "不是本审计卡" in bad2["reasons"][0] and "更早一轮的审计卡" in bad2["reasons"][0]


def test_f230_reuse_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f230_evidence_reuse.py"
    assert rel in discover_test_files([rel], default_test_contract())
