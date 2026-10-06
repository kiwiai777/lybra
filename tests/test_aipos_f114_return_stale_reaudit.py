"""AIPOS-F114 — 交回后、出裁决前卡分支前进的处置(gap #74, F112 同族): 推导核派生重交回并作废在途审计轮, 审计开工以分支当前 tip 为准。

靶场 = AIPOS-F95/F112 同一靶场(F90 tmp 治理根 + tmp 产品仓 + tmp HOME + 进程内门 InProcessGate 经真产品 CLI 薄壳; tmp 工位;
假 harness tests/fake_harness.py 拉起执行体/审计体; 禁真门/真治理根/真工位/真 pi)。finalize 步 = 真 finalize_task(F112 RealFinalize,
真 F70 核对 + 真卡分支整合, 只去 push/deploy)。

验收(卡面 ★验收 / ★件③):
 ① 靶场全链(F107 实况复演): 交回 → 门派审 R 并认领(取证树建于交回 tip)→ 审计体写了审旧 tip 的报告 → 执行体合 main(卡分支前进)
    → 推导核 return_stale(不再等 R)→ Return 未更新 = artifact_invalid(loop exit 4)→ 更新后重交回(`lybra artifact ingest --kind return`)
    → 门按卡号演进派 R2(派审记录 supersedes=R)→ 假 harness 审计 R2 → 裁决绑新 tip → 真 finalize → close → N6 落账
 ② 开工拒因: R 在交回过期时 RETURN_STALE「交回已过期, 等驱动方重交回」(不出开工提示); R 被取代后 ROUND_SUPERSEDED;
    R 的旧 tip 报告入门拒 INGEST_TIP_MISMATCH 附出口(重交回命令 / 已被 R2 取代)
 ③ 单元: 门交回记录绑 tip(artifact_subject); 绑定判据优先、存量无绑定退回取证树; R2 在途分支再前进 → R3(supersedes=R2);
    已重交回但门未派下一轮 = 不可推导不重复交回; 声明/登记/无吞异常
"""
from __future__ import annotations

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
import test_aipos_f112_verdict_stale_reaudit as f112  # noqa: E402  — RealFinalize / 产物入口 CLI 夹具唯一来源
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _git, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop, workstation_kickoff  # noqa: E402

TASK = "PROBE-F114-1"
R1, R2, R3 = f"{TASK}R", f"{TASK}R2", f"{TASK}R3"


def _show(line: str) -> None:
    print(line, flush=True)


def _setup(lrig, monkeypatch) -> "f112.RealFinalize":
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    _write(lrig.gov / "stage_archive" / "2026-10-06_stage.md", "# stage(靶场阶段快照: 真 finalize 的阶段门票)\n")
    init_governance_repo(lrig.gov)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, f95.AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    real = f112.RealFinalize(lrig.code_repo, conflict_once=False)
    monkeypatch.setattr(f90, "_finalize_double", real)
    return real


def _loop(r, card: str = TASK, max_steps: int = 30):
    out = io.StringIO()
    res = run_loop(card, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=max_steps)
    return res, out.getvalue()


def _records(gov: Path, kind: str, card: str) -> list[Path]:
    d = gov / "5_tasks" / "records" / kind / card
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _queue_file(gov: Path, queue: str, card: str) -> Path:
    return gov / "5_tasks" / "queue" / queue / f"{card.lower()}.md"


def _fm(path: Path) -> dict:
    return nr._read_frontmatter(path)


def _return_path(r) -> Path:
    return r.gov / "task_cards" / TASK / "RETURN.md"


def _update_return(r, sha: str, tree: str) -> None:
    text = _return_path(r).read_text(encoding="utf-8")
    meta, body = text.split("---\n", 2)[1], text.split("---\n", 2)[2]
    lines = [{"commit_sha": f"commit_sha: {sha}", "tree_hash": f"tree_hash: {tree}"}.get(line.split(":", 1)[0], line)
             for line in meta.splitlines()]
    _write(_return_path(r), "---\n" + "\n".join(lines) + "\n---\n" + body)


def _phase1_returned_and_round_claimed(lrig) -> str:
    """阶段 1: loop 走到「交回 → 门派审 R → 驱动方认领 R(取证树建于交回 tip)」即停(max_steps), 不拉起审计体。返回交回 tip。"""
    res, text = _loop(lrig, max_steps=4)
    _show(f"---- 阶段1 lybra loop {TASK}(max_steps=4)输出原文 ----\n{text}")
    assert _records(lrig.gov, "returns", TASK) and _records(lrig.gov, "claims", R1), text
    tip = nr._extract_artifact_subject_from_branch(lrig.code_repo, TASK, "code")["commit_sha"]
    _code, forensic = nr.card_worktree_location(lrig.gov, R1)
    assert _git(forensic, "rev-parse", "HEAD") == tip  # 取证树 = 交回 tip
    return tip


def _branch_advances_after_return(r) -> tuple[str, str]:
    """F107 实况: 交回后他卡先合入 main, 执行体在卡工作树 `git merge main`(无冲突)→ 卡分支前进。返回 (新 tip, 新 tree)。"""
    n = len(list((r.code_repo / "docs").glob("other_card_*.md"))) if (r.code_repo / "docs").is_dir() else 0
    _write(r.code_repo / "docs" / f"other_card_{n}.md", f"他卡 {n} 已合入 main\n")
    _git(r.code_repo, "add", f"docs/other_card_{n}.md")
    _git(r.code_repo, "commit", "-q", "-m", "OTHER-CARD: 先合入 main")
    _code, worktree = nr.card_worktree_location(r.gov, TASK)
    merged = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "merge", "--no-edit", nr.card_base_branch()], cwd=worktree,
                            capture_output=True, text=True)
    assert merged.returncode == 0, merged.stdout + merged.stderr
    return _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")


def nr_superseding(gov: Path, card: str):
    from tools.aipos_cli.audit_derivation import superseding_round

    return superseding_round(card, gov)


def _old_round_report(r, old_tip: str) -> Path:
    """审计体已对旧 tip 写完报告(F107R 实况: 审了交回记录 tip, 尚未入门)。"""
    report = nr.card_report_path(r.gov, R1)
    _write(report, f"---\ntask_id: {R1}\nreviewed_task_id: {TASK}\nverdict: PASS\ncommit_sha: {old_tip}\n---\n"
                   "# 审计报告\n\n## 一句话结论\n审了交回时的 tip(假审计体)。\n")
    return report


# ===========================================================================
# ① 靶场全链 + ② 开工/入口拒因
# ===========================================================================

def test_item1_full_chain_return_branch_advances_return_stale_rereturn_r2_audit_finalize_close(lrig, monkeypatch):
    real = _setup(lrig, monkeypatch)
    old_tip = _phase1_returned_and_round_claimed(lrig)
    ret1 = _fm(_records(lrig.gov, "returns", TASK)[0])
    _show(f"[①] 门交回记录 {ret1['return_id']} 绑定 artifact_subject={ret1.get('artifact_subject')}")
    assert ret1["artifact_subject"]["commit_sha"] == old_tip and ret1["artifact_subject"]["branch"] == nr.card_branch_name(TASK)

    report = _old_round_report(lrig, old_tip)
    new_tip, new_tree = _branch_advances_after_return(lrig)
    _show(f"[①] 卡分支 {nr.card_branch_name(TASK)}: 交回绑 {old_tip[:12]} → 执行体合 main 后 tip {new_tip[:12]}; R 已有审旧 tip 的报告 {report.name}")
    assert new_tip != old_tip

    # 对照: F114 前推导核在此派 R 的裁决入门, 入口必拒 INGEST_TIP_MISMATCH(F107 实况)——本卡起推导核先判交回过期
    stale = nr.return_staleness(lrig.gov, TASK, _fm(_queue_file(lrig.gov, "claimed", TASK)))
    _show(f"[①] return_staleness = {json.dumps(stale, ensure_ascii=False)}")
    assert stale and stale["bound_source"] == "return_record" and stale["bound_commit_sha"] == old_tip and stale["tip"] == new_tip
    assert stale["audit_task_id"] == R1 and stale["return_id"] == ret1["return_id"]

    # ② 审计开工拒因: R 交回已过期 → RETURN_STALE(工位 my-tasks --task-id 同一产品输出), 不出开工提示
    d_r = nr.derive_next_step(R1, lrig.gov)
    _show(f"[②] 推导核 {R1}: node={d_r['current_node']} derivable={d_r['derivable']} triggered_by={d_r['triggered_by']}\n"
          f"    suggested={d_r['suggested_action']}")
    assert d_r["current_node"] == "return_stale" and not d_r["derivable"] and d_r["triggered_by"] == "advisor"
    assert d_r["action"] == {"type": "return_stale", "card": TASK, "command": d_r["action"]["command"]}
    assert d_r["action"]["command"].startswith(f"lybra artifact ingest --task-id {TASK} --kind return")
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), R1)
    _show(f"[②] 开工拒因原文(工位 my-tasks --task-id {R1}): {why}")
    assert kickoff == "" and f"{R1} RETURN_STALE: 交回已过期, 等驱动方重交回" in why and old_tip[:12] in why and new_tip[:12] in why
    assert f"lybra artifact ingest --task-id {TASK} --kind return" in why
    # ② 裁决入口: 旧 tip 报告拒 INGEST_TIP_MISMATCH, 拒因附出口(重交回命令)
    rc, cli = f112._ingest_cli(lrig.gov, R1, "verdict")
    _show(f"---- ② lybra artifact ingest --task-id {R1} --kind verdict(审旧 tip 的报告)原文 rc={rc} ----\n{cli}")
    assert rc != 0 and "INGEST_TIP_MISMATCH" in cli and "交回已过期" in cli and f"--task-id {TASK} --kind return" in cli, cli
    assert not _records(lrig.gov, "audit_verdicts", TASK)

    # 被审卡: Return 未更新(commit_sha 仍是旧 tip)→ return_stale + artifact_invalid, loop exit 4; 不派 R 的裁决
    d = nr.derive_next_step(TASK, lrig.gov)
    _show(f"[①] Return 未更新时推导核 {TASK}: node={d['current_node']} derivable={d['derivable']} action={d.get('action')}\n"
          f"    missing={d['missing_records']}\n    notes={d['notes']}")
    assert d["current_node"] == "return_stale" and not d["derivable"] and d["action"]["type"] == "artifact_invalid"
    assert any("INGEST_TIP_MISMATCH" in m and old_tip[:12] in m for m in d["missing_records"]), d
    res, text = _loop(lrig)
    _show(f"---- ① Return 未更新 lybra loop 输出原文 ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable") and "INGEST_TIP_MISMATCH" in text, text
    assert "verdict" not in [s.action_type for s in res.steps] and len(_records(lrig.gov, "returns", TASK)) == 1

    # 执行体按卡分支 tip 更新 Return → 推导核派生重交回 → 阶段 2 loop 一路走完
    _update_return(lrig, new_tip, new_tree)
    d = nr.derive_next_step(TASK, lrig.gov)
    _show(f"[①] Return 已更新后推导核: node={d['current_node']} verb={d['verb']} command={d['command']}\n    notes={d['notes']}")
    assert d["derivable"] and d["current_node"] == "return_stale" and d["verb"] == "lybra_queue_return_dry_run"
    assert d["command"].startswith(f"lybra artifact ingest --task-id {TASK} --kind return")

    res, text = _loop(lrig)
    _show(f"---- ① 阶段2 lybra loop {TASK} 输出原文 ----\n{text}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    nodes = [(s.kind, s.node, s.action_type, s.card) for s in res.steps]
    _show(f"[①] 阶段2 步骤: {nodes}")
    assert ("execute", "return_stale", "return", TASK) in nodes
    launched = [s.launch["card"] for s in res.steps if s.launch and s.launch.get("launched")]
    assert launched == [R2], launched
    assert [s.card for s in res.steps if s.action_type == "verdict"] == [R2]

    # 记录链: 两份 return(各绑其 tip), R2 派审 supersedes=R, 仅 R2 一份裁决(绑新 tip), finalization 授权 = R2 裁决
    returns = [_fm(p) for p in _records(lrig.gov, "returns", TASK)]
    ret2 = [x for x in returns if x["return_id"] != ret1["return_id"]][0]
    assert ret2["artifact_subject"]["commit_sha"] == new_tip
    disp = _fm(_records(lrig.gov, "audit_dispatches", R2)[0])
    _show(f"[①] R2 派审记录: audit_task_id={disp['audit_task_id']} supersedes={disp.get('supersedes')} "
          f"reviewed_return_record_ref={disp['reviewed_return_record_ref']}")
    assert disp["supersedes"] == R1 and disp["reviewed_return_record_ref"] == ret2["return_id"]
    verdicts = [_fm(p) for p in _records(lrig.gov, "audit_verdicts", TASK)]
    _show(f"[①] 裁决: {[(v['audit_task_id'], v['artifact_subject']['commit_sha'][:12], v['verdict']) for v in verdicts]}")
    assert [(v["audit_task_id"], v["artifact_subject"]["commit_sha"]) for v in verdicts] == [(R2, new_tip)]
    r2_fm = _fm(_queue_file(lrig.gov, "completed", R2))
    note = [g for g in r2_fm["governance_refs"] if "复审说理" in g]
    _show(f"[①] R2 卡 governance_refs 复审说理: {note}")
    assert note and "return_stale" in note[0] and R1 in note[0] and old_tip[:12] in note[0] and new_tip[:12] in note[0]
    kickoff_r2 = (lrig.log / f"{R2}.kickoff").read_text(encoding="utf-8")
    assert f"- commit_sha: {new_tip}(" in kickoff_r2 and old_tip not in kickoff_r2
    assert not (lrig.log / f"{R1}.kickoff").exists()  # 旧轮从未拉起审计体
    _show(f"[①] 真 finalize: {[(c.get('verdict'), c.get('message')) for c in real.calls]}")
    assert [c.get("verdict") for c in real.calls] == ["PASS"]
    assert _git(lrig.code_repo, "merge-base", "--is-ancestor", new_tip, nr.card_base_branch()) == ""
    fin = _fm(_records(lrig.gov, "finalizations", TASK)[0])
    assert fin["authorization_ref"] == verdicts[0]["verdict_id"]
    assert _records(lrig.gov, "closures", TASK) and _queue_file(lrig.gov, "completed", TASK).is_file()

    # 在途旧轮作废: 派审时不改写卡面/不移队列(取代关系只记 R2 派审记录 supersedes); 被审卡 close 时随既有「结案联动审计卡」移入 completed
    r1_fm = _fm(_queue_file(lrig.gov, "completed", R1))
    _show(f"[①] 旧轮 {R1}: completed auto_closed_with_parent={r1_fm.get('auto_closed_with_parent')} "
          f"superseding_round={nr_superseding(lrig.gov, R1)}")
    assert r1_fm.get("auto_closed_with_parent") == TASK and nr_superseding(lrig.gov, R1)["audit_task_id"] == R2
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), R1)
    _show(f"[②] 结案后旧轮开工拒因原文: {why}")
    assert kickoff == "" and f"{R1} CONCLUDED" in why, why
    rc, cli = f112._ingest_cli(lrig.gov, R1, "verdict")
    _show(f"---- ② 旧轮报告入门(已被 R2 取代)原文 rc={rc} ----\n{cli}")
    assert rc != 0 and f"已被 {R2} 取代" in cli, cli
    status = _git(lrig.gov, "status", "--porcelain")
    _show(f"[①] 落账后治理仓 git status --porcelain={status!r}")
    assert R2 not in status and R2.lower() not in status


def test_item2_kickoff_refused_before_audit_starts_and_superseded_round_after_rereturn(lrig, monkeypatch):
    """R 认领后、审计体尚未写报告时卡分支前进: R 开工拒 RETURN_STALE; 重交回派 R2 后 R 开工拒 ROUND_SUPERSEDED, R2 开工提示绑新 tip。"""
    _setup(lrig, monkeypatch)
    old_tip = _phase1_returned_and_round_claimed(lrig)
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), R1)
    assert why == "" and f"- commit_sha: {old_tip}(" in kickoff  # 未过期: 正常开工提示
    new_tip, new_tree = _branch_advances_after_return(lrig)
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), R1)
    _show(f"[②] 开工拒因原文(审计未开工即过期): {why}")
    assert kickoff == "" and f"{R1} RETURN_STALE: 交回已过期, 等驱动方重交回" in why
    _update_return(lrig, new_tip, new_tree)
    rc, cli = f112._ingest_cli(lrig.gov, TASK, "return")
    _show(f"[②] 重交回 ingest rc={rc}\n{cli}")
    assert rc == 0 and _queue_file(lrig.gov, "pending", R2).is_file()
    assert _fm(_records(lrig.gov, "audit_dispatches", R2)[0])["supersedes"] == R1
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), R1)
    _show(f"[②] 开工拒因原文(旧轮已被取代): {why}")
    assert kickoff == "" and f"{R1} ROUND_SUPERSEDED: 本轮审计已被下一轮取代" in why and R2 in why
    d = nr.derive_next_step(TASK, lrig.gov)
    assert d["action"] == {"type": "await_artifact", "card": R2}, d  # 推导核等新一轮
    # 认领 R2(既有认领, 驱动方)→ 取证树建于新 tip, 开工提示被审 tip 实值 = 新 tip
    res, text = _loop(lrig, card=R2, max_steps=1)
    _show(f"---- ② lybra loop {R2}(认领)输出原文 ----\n{text}")
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), R2)
    assert why == "" and f"- commit_sha: {new_tip}(" in kickoff and old_tip not in kickoff, why


# ===========================================================================
# ③ 单元: 绑定 / 存量回退 / R2 在途再前进 → R3 / 旧门不派下一轮
# ===========================================================================

def test_item3_binding_legacy_fallback_and_round_evolution_r2_to_r3(lrig, monkeypatch):
    from tools.aipos_cli import audit_derivation as ad

    _setup(lrig, monkeypatch)
    old_tip = _phase1_returned_and_round_claimed(lrig)
    fm = _fm(_queue_file(lrig.gov, "claimed", TASK))
    assert nr.return_staleness(lrig.gov, TASK, fm) is None  # 未前进: 不过期
    ret_path = _records(lrig.gov, "returns", TASK)[0]
    new_tip, new_tree = _branch_advances_after_return(lrig)
    # 存量交回记录(门早于本卡, 无 artifact_subject)→ 退回本轮取证树 HEAD(F107 活体形)
    original = ret_path.read_text(encoding="utf-8")
    legacy = "\n".join(ln for ln in original.splitlines() if not ln.startswith(("artifact_subject:", "  repository:", "  commit_sha:",
                                                                                   "  tree_hash:", "  branch:"))) + "\n"
    ret_path.write_text(legacy, encoding="utf-8")
    assert "artifact_subject" not in legacy and nr.return_record_subject(lrig.gov, TASK, _fm(ret_path)["return_id"]) is None
    stale = nr.return_staleness(lrig.gov, TASK, fm)
    _show(f"[③] 存量无绑定 → 取证树回退: {stale}")
    assert stale["bound_source"] == "forensic_worktree" and stale["bound_commit_sha"] == old_tip and "存量" in stale["reason"]
    ret_path.write_text(original, encoding="utf-8")
    assert nr.return_staleness(lrig.gov, TASK, fm)["bound_source"] == "return_record"
    # 门侧派生: 在途 R 过期 → R2(kind=return_stale, supersedes R)
    nxt = ad._reaudit_round(lrig.gov, TASK, fm, branch_id="code")
    assert nxt["kind"] == "return_stale" and nxt["audit_task_id"] == R2 and nxt["superseded_audit_task_id"] == R1
    _update_return(lrig, new_tip, new_tree)
    rc, cli = f112._ingest_cli(lrig.gov, TASK, "return")
    assert rc == 0 and ad.audit_round_ids(TASK, lrig.gov) == [R1, R2], cli
    assert nr.return_staleness(lrig.gov, TASK, fm) is None  # R2 所审交回绑新 tip: 不过期
    assert ad._reaudit_round(lrig.gov, TASK, fm, branch_id="code") is None  # 同 tip 再交回: 不演进(走既有幂等)
    # R2 在途时卡分支再前进 → 判据读 R2 派审记录所指交回的绑定 → 再交回演进 R3(supersedes=R2)
    third_tip, third_tree = _branch_advances_after_return(lrig)
    stale = nr.return_staleness(lrig.gov, TASK, fm)
    assert stale["audit_task_id"] == R2 and stale["bound_commit_sha"] == new_tip and stale["tip"] == third_tip
    _update_return(lrig, third_tip, third_tree)
    rc, cli = f112._ingest_cli(lrig.gov, TASK, "return")
    _show(f"[③] R2 在途再前进 → 重交回 rc={rc}\n{cli}")
    assert rc == 0 and ad.audit_round_ids(TASK, lrig.gov) == [R1, R2, R3]
    assert _fm(_records(lrig.gov, "audit_dispatches", R3)[0])["supersedes"] == R2
    assert ad.superseding_round(R1, lrig.gov)["audit_task_id"] == R2 and ad.superseding_round(R2, lrig.gov)["audit_task_id"] == R3
    assert ad.superseding_round(R3, lrig.gov) is None
    # 未认领(pending)的被取代轮同判作废: 不再派认领(R2 从未认领即被 R3 取代)
    assert _queue_file(lrig.gov, "pending", R2).is_file()
    d_r2 = nr.derive_next_step(R2, lrig.gov)
    _show(f"[③] pending 被取代轮 {R2}: derivable={d_r2['derivable']} verb={d_r2['verb']!r} superseded_by={d_r2.get('superseded_by')}")
    assert not d_r2["derivable"] and d_r2["verb"] == "" and d_r2["superseded_by"]["audit_task_id"] == R3
    assert nr.derive_next_step(TASK, lrig.gov)["action"] == {"type": "await_artifact", "card": R3}


def test_item3_rereturn_without_new_round_is_not_derivable_no_repeat(lrig, monkeypatch):
    """已重交回但门没派下一轮(如门为旧版本)= 不可推导点名, 不重复交回(与 F112 同一构建)。"""
    from tools.aipos_cli import audit_derivation

    _setup(lrig, monkeypatch)
    _phase1_returned_and_round_claimed(lrig)
    new_tip, new_tree = _branch_advances_after_return(lrig)
    _update_return(lrig, new_tip, new_tree)
    monkeypatch.setattr(audit_derivation, "_reaudit_round", lambda *a, **k: {"skip": "simulated old gate"})
    rc, cli = f112._ingest_cli(lrig.gov, TASK, "return")
    assert rc == 0 and len(_records(lrig.gov, "returns", TASK)) == 2, cli
    d = nr.derive_next_step(TASK, lrig.gov)
    _show(f"[③] 旧门重交回后推导核: derivable={d['derivable']} missing={d['missing_records']} suggested={d['suggested_action']}")
    assert d["current_node"] == "return_stale" and not d["derivable"] and "门未派生下一轮审计卡" in d["missing_records"][0]
    assert "AIPOS-F114" in d["suggested_action"]


def test_item3_return_stale_declared_single_build_and_no_swallowed_exceptions():
    from tools.aipos_cli.next_resolver import KICKOFF_REFUSAL_CODES, return_stale_declaration

    decl = return_stale_declaration()
    assert decl["state"] == "return_stale"
    for key in ("re_return", "re_audit_derivation", "superseded_round", "loop_wait_and_launch"):
        assert decl["outlet"][key], key
    for key in ("kickoff_guard", "verdict_ingest_guard", "return_not_updated_guard", "re_returned_without_round"):
        assert decl[key], key
    assert {"RETURN_STALE", "ROUND_SUPERSEDED"} <= set(KICKOFF_REFUSAL_CODES)
    src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    # 重交回出口唯一构建(F112 verdict_stale / F114 return_stale 共用), 交回步唯一构建
    assert src.count("def _derive_stale_re_return(") == 1 and src.count("_return_submission_step(\n") + src.count("_return_submission_step(workspace_root") >= 1
    assert src.count("_derive_stale_re_return(\n        workspace_root") == 2
    for rel in ("tests/test_aipos_f114_return_stale_reaudit.py",):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        swallow, skip = "except " + "Exception", "pytest." + "skip"
        assert swallow not in text and skip not in text


def test_f114_fixture_registered_in_runall():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert ('run_pytest "tests/test_aipos_f114_return_stale_reaudit.py" '
            '"$REPO_ROOT/tests/test_aipos_f114_return_stale_reaudit.py"') in runall
