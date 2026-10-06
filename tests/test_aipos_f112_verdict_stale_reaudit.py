"""AIPOS-F112 — 裁决后卡分支 tip 变化的复审出口(gap #69): 推导核派生「重交回 + 复审(R2…)」, loop 自动走完, 不再卡死。

靶场 = AIPOS-F95 同一靶场(F90 tmp 治理根 + tmp 产品仓 + tmp HOME + 进程内门 InProcessGate 经真产品 CLI 薄壳; tmp 工位;
假 harness tests/fake_harness.py 拉起执行体/审计体; 禁真门/真治理根/真工位/真 pi)。finalize 步换成真 finalize_task(真 F70 核对 +
真卡分支整合, 只去掉 push/deploy: 靶场产品仓无远端/无部署机制), 以便演示「合并冲突 → 卡分支前进 → F70 BLOCK」的真实因果。

验收(卡面 ★验收):
 ① 靶场全链: 卡 PASS → finalize 合并冲突 → 执行体在卡工作树合 main 解冲突(卡分支前进)→ 推导核 verdict_stale(不派 finalize)
    → 重交回(`lybra artifact ingest --kind return`)→ 门按卡号演进派 R2(派审记录 supersedes=R)→ 假 harness 审计写报告
    → 裁决绑新 tip → finalize 成功 → close → N6 落账(R2 的队列/记录/报告入落账范围)
 ② RETURN 未随卡分支更新: 推导核 artifact_invalid(loop exit 4)、产物入口 INGEST_TIP_MISMATCH 点名 commit_sha, 不派审
 ③ 单元: 判据与 finalize F70 同源(过期 ⇔ finalize BLOCK「须复审」); 已重交回未派下一轮 = 不可推导不重复交回; R2 在途幂等不演进 R3;
    审计卡判据认得 R2(推导核/产物入口/loop 回读同一实现)
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
from test_aipos_f78_engine_agnostic import DRIVER, EXEC, _fm, _git, _write  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402

TASK = "PROBE-F112-1"
R1, R2, R3 = f"{TASK}R", f"{TASK}R2", f"{TASK}R3"
WORK_REL = f"tests/test_{TASK.lower().replace('-', '_')}.py"  # 假 harness 执行体写的文件(与 fake_harness.do_work 同名)


def _show(line: str) -> None:
    print(line, flush=True)


# ---------------------------------------------------------------------------
# 靶场补件
# ---------------------------------------------------------------------------

class RealFinalize:
    """finalize 步 = 真 finalize_task(F70 核对 + 卡分支整合 + finalization 记录), 只去 push/deploy。
    conflict_once: 第一次 finalize 前在产品仓 main 先落一个与卡分支同路径不同内容的提交(他卡先合入)→ 真整合撞冲突 BLOCK。"""

    def __init__(self, code_repo: Path, *, conflict_once: bool) -> None:
        self.code_repo = code_repo
        self.conflict_once = conflict_once
        self.calls: list[dict] = []

    def __call__(self, gov: Path, code_repo: Path, argv: list[str]) -> subprocess.CompletedProcess:
        from tools.aipos_cli.finalize import finalize_task

        task_id = argv[argv.index("--task-id") + 1]
        actor = argv[argv.index("--actor") + 1]
        if self.conflict_once:
            self.conflict_once = False
            _write(code_repo / WORK_REL, "def test_other_card():\n    assert 1 == 1\n")
            _git(code_repo, "add", WORK_REL)
            _git(code_repo, "commit", "-q", "-m", "OTHER-CARD: 同路径先合入 main")
        res = finalize_task(task_id, actor, code_repo, governance_root=gov, push=False, deploy=False)
        self.calls.append(res)
        text = f"finalize {task_id}: verdict={res.get('verdict')} message={res.get('message')}\n" + "\n".join(
            f"  - {op}" for op in res.get("operations") or [])
        return subprocess.CompletedProcess(argv, 0 if res.get("verdict") == "PASS" else 1, text, "")


def _setup(lrig, monkeypatch, *, conflict_once: bool = True) -> RealFinalize:
    f90._card(lrig.gov, TASK, "pending", harness="pi")
    _write(lrig.gov / "stage_archive" / "2026-10-06_stage.md", "# stage(靶场阶段快照: 真 finalize 的阶段门票)\n")
    init_governance_repo(lrig.gov)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, f95.AUDIT_CLAIMER, "auditor", lrig.ws_audit)
    real = RealFinalize(lrig.code_repo, conflict_once=conflict_once)
    monkeypatch.setattr(f90, "_finalize_double", real)
    return real


def _loop(r, card: str = TASK, max_steps: int = 30):
    out = io.StringIO()
    res = run_loop(card, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=max_steps)
    return res, out.getvalue()


def _resolve_conflict_on_card_branch(r) -> tuple[str, str]:
    """执行体在卡工作树 `git merge main` 解冲突(保留两边)后提交 → 卡分支 tip 前进。返回 (新 tip, 新 tree)。"""
    _code, worktree = nr.card_worktree_location(r.gov, TASK)
    merged = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "merge", "main"], cwd=worktree,
                            capture_output=True, text=True)
    assert merged.returncode != 0 and "CONFLICT" in merged.stdout, merged.stdout + merged.stderr
    _write(worktree / WORK_REL, "def test_ok():\n    assert True\n\n\ndef test_other_card():\n    assert 1 == 1\n")
    _git(worktree, "add", WORK_REL)
    _git(worktree, "commit", "-q", "--no-edit")
    return _git(worktree, "rev-parse", "HEAD"), _git(worktree, "rev-parse", "HEAD^{tree}")


def _return_path(r) -> Path:
    return r.gov / "task_cards" / TASK / "RETURN.md"


def _update_return(r, sha: str, tree: str) -> None:
    text = _return_path(r).read_text(encoding="utf-8")
    meta, body = text.split("---\n", 2)[1], text.split("---\n", 2)[2]
    lines = []
    for line in meta.splitlines():
        key = line.split(":", 1)[0]
        lines.append({"commit_sha": f"commit_sha: {sha}", "tree_hash": f"tree_hash: {tree}"}.get(key, line))
    _write(_return_path(r), "---\n" + "\n".join(lines) + "\n---\n" + body)


def _records(gov: Path, kind: str, card: str) -> list[Path]:
    d = gov / "5_tasks" / "records" / kind / card
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _ingest_cli(gov: Path, task_id: str, kind: str) -> tuple[int, str]:
    import contextlib

    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(["artifact", "ingest", "--task-id", task_id, "--kind", kind, "--workspace-root", str(gov)])
    return int(rc), out.getvalue() + err.getvalue()


def _phase1_pass_then_conflict(lrig) -> tuple[str, str]:
    """阶段 1: loop 走到 PASS → 真 finalize 撞合并冲突 BLOCK(exit 2); 返回 R 裁决覆盖的旧 tip。"""
    res, text = _loop(lrig)
    _show(f"---- 阶段1 lybra loop {TASK} 输出原文 ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "gate_rejected"), text
    assert "门拒 @ finalize" in res.message and "合并冲突" in res.message, res.message
    verdict_fm = nr._read_frontmatter(_records(lrig.gov, "audit_verdicts", TASK)[0])
    old_tip = verdict_fm["artifact_subject"]["commit_sha"]
    assert verdict_fm["verdict"] == "PASS" and verdict_fm["audit_task_id"] == R1
    return old_tip, text


# ===========================================================================
# ① 靶场全链 + ② RETURN 未更新拒
# ===========================================================================

def test_item1_full_chain_pass_branch_advances_stale_rereturn_r2_audit_finalize_close(lrig, monkeypatch):
    real = _setup(lrig, monkeypatch)
    old_tip, _ = _phase1_pass_then_conflict(lrig)

    # 执行体在卡工作树合 main 解冲突 → 卡分支前进(F106/F102/F103/F108 实况)
    new_tip, new_tree = _resolve_conflict_on_card_branch(lrig)
    _show(f"[①] 卡分支 card/{TASK}: 裁决覆盖 {old_tip[:12]} → 合 main 解冲突后 tip {new_tip[:12]}")
    assert new_tip != old_tip

    # 对照: F112 前推导核在此派 finalize, 真 finalize 照 F70 BLOCK「须复审」(本卡判据与之同源)
    from tools.aipos_cli.finalize import check_task_can_finalize

    f70 = check_task_can_finalize(TASK, lrig.gov, commit_sha=new_tip)
    _show(f"[①] 真 finalize F70 核对(新 tip): can_finalize={f70['can_finalize']} reason={f70['reason']}")
    assert not f70["can_finalize"] and "须复审" in f70["reason"]

    # ② RETURN 未更新(commit_sha 仍是旧 tip): 推导核 verdict_stale + artifact_invalid, loop exit 4; 产物入口同一判据拒
    d = nr.derive_next_step(TASK, lrig.gov)
    _show(f"[②] RETURN 未更新时推导核: node={d['current_node']} derivable={d['derivable']} action={d.get('action')}\n"
          f"    missing={d['missing_records']}\n    suggested={d['suggested_action']}\n    notes={d['notes']}")
    assert d["current_node"] == "verdict_stale" and not d["derivable"] and d["action"]["type"] == "artifact_invalid"
    assert any("INGEST_TIP_MISMATCH" in m and old_tip[:12] in m for m in d["missing_records"]), d
    assert d["verb"] != "lybra_finalize"
    res, text = _loop(lrig)
    _show(f"---- ② RETURN 未更新 lybra loop 输出原文 ----\n{text}")
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable") and "INGEST_TIP_MISMATCH" in text, text
    rc, cli = _ingest_cli(lrig.gov, TASK, "return")
    _show(f"---- ② lybra artifact ingest --kind return(RETURN 未更新)原文 rc={rc} ----\n{cli}")
    assert rc != 0 and "INGEST_TIP_MISMATCH" in cli and "commit_sha" in cli, cli
    assert len(_records(lrig.gov, "returns", TASK)) == 1 and not (lrig.gov / "5_tasks" / "queue" / "pending" / f"{R2.lower()}.md").exists()

    # 执行体按卡分支 tip 更新 RETURN → 阶段 2: loop 一路走完
    _update_return(lrig, new_tip, new_tree)
    d = nr.derive_next_step(TASK, lrig.gov)
    _show(f"[①] RETURN 已更新后推导核: node={d['current_node']} verb={d['verb']} command={d['command']}\n    notes={d['notes']}")
    assert d["derivable"] and d["current_node"] == "verdict_stale" and d["verb"] == "lybra_queue_return_dry_run"
    assert d["command"].startswith(f"lybra artifact ingest --task-id {TASK} --kind return")

    res, text = _loop(lrig)
    _show(f"---- ① 阶段2 lybra loop {TASK} 输出原文 ----\n{text}")
    assert res.exit_code == 0 and res.outcome == "completed", text
    nodes = [(s.kind, s.node, s.action_type, s.card) for s in res.steps]
    _show(f"[①] 阶段2 步骤: {nodes}")
    assert ("execute", "verdict_stale", "return", TASK) in nodes
    launched = [s.launch["card"] for s in res.steps if s.launch and s.launch.get("launched")]
    assert launched == [R2], launched  # loop 认最新一轮审计卡等待/拉起
    assert [s.card for s in res.steps if s.action_type == "verdict"] == [R2]

    # 记录链: 两份 return, R2 派审记录 supersedes=R, 两份裁决(R 旧 tip / R2 新 tip), finalization + closure
    returns = _records(lrig.gov, "returns", TASK)
    assert len(returns) == 2
    disp = nr._read_frontmatter(_records(lrig.gov, "audit_dispatches", R2)[0])
    _show(f"[①] R2 派审记录: audit_task_id={disp['audit_task_id']} supersedes={disp.get('supersedes')} "
          f"reviewed_return_record_ref={disp['reviewed_return_record_ref']}")
    assert disp["supersedes"] == R1 and disp["reviewed_return_record_ref"] == nr._read_frontmatter(returns[-1])["return_id"]
    verdicts = {nr._read_frontmatter(p)["audit_task_id"]: nr._read_frontmatter(p) for p in _records(lrig.gov, "audit_verdicts", TASK)}
    _show(f"[①] 裁决: R={verdicts[R1]['artifact_subject']['commit_sha'][:12]} R2={verdicts[R2]['artifact_subject']['commit_sha'][:12]}")
    assert verdicts[R1]["artifact_subject"]["commit_sha"] == old_tip and verdicts[R2]["artifact_subject"]["commit_sha"] == new_tip
    assert verdicts[R2]["verdict"] == "PASS"
    r2_card = lrig.gov / "5_tasks" / "queue" / "completed" / f"{R2.lower()}.md"
    r2_fm = nr._read_frontmatter(r2_card)
    note = [g for g in r2_fm["governance_refs"] if "复审说理" in g]
    _show(f"[①] R2 卡 governance_refs 复审说理: {note}")
    assert note and R1 in note[0] and old_tip[:12] in note[0] and new_tip[:12] in note[0]
    assert (lrig.gov / "5_tasks" / "queue" / "completed" / f"{R1.lower()}.md").is_file()  # 上一轮随其裁决结案
    kickoff = (lrig.log / f"{R2}.kickoff").read_text(encoding="utf-8")
    assert f"- commit_sha: {new_tip}(" in kickoff and f"已认领任务卡 {R2}。" in kickoff
    # finalize: 第一次冲突 BLOCK, 第二次绑新 tip 的 R2 裁决 → PASS, 合入 main
    _show(f"[①] 真 finalize 两次: {[(c.get('verdict'), c.get('message')) for c in real.calls]}")
    assert [c.get("verdict") for c in real.calls] == ["BLOCK", "PASS"]
    assert _git(lrig.code_repo, "merge-base", "--is-ancestor", new_tip, "main") == ""
    fin = nr._read_frontmatter(_records(lrig.gov, "finalizations", TASK)[0])
    assert fin["authorization_ref"] == verdicts[R2]["verdict_id"]
    assert _records(lrig.gov, "closures", TASK)
    assert (lrig.gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md").is_file()
    # N6 落账范围含复审轮(R2 的队列文件/记录/报告): 治理仓干净
    from tools.aipos_cli.governance_commit import task_scope_candidates

    scope = task_scope_candidates(lrig.gov, TASK)
    assert any(f"/{R2}" in p or p.endswith(f"{R2.lower()}.md") for p in scope["audit"]), scope["audit"]
    status = _git(lrig.gov, "status", "--porcelain")
    _show(f"[①] 落账后治理仓 git status --porcelain={status!r}")
    assert R2 not in status and R2.lower() not in status


def test_item1_rereturn_without_new_round_is_not_derivable_no_repeat(lrig, monkeypatch):
    """已重交回(return 记录晚于过期裁决)但门没派下一轮(如门为旧版本)= 不可推导点名, 不重复交回。"""
    from tools.aipos_cli import audit_derivation

    _setup(lrig, monkeypatch)
    _phase1_pass_then_conflict(lrig)
    new_tip, new_tree = _resolve_conflict_on_card_branch(lrig)
    _update_return(lrig, new_tip, new_tree)
    monkeypatch.setattr(audit_derivation, "_reaudit_round", lambda *a, **k: {"skip": "simulated old gate"})
    rc, cli = _ingest_cli(lrig.gov, TASK, "return")
    _show(f"[③] 旧门重交回(不派下一轮) ingest rc={rc}\n{cli}")
    assert rc == 0 and len(_records(lrig.gov, "returns", TASK)) == 2
    d = nr.derive_next_step(TASK, lrig.gov)
    _show(f"[③] 推导核: derivable={d['derivable']} missing={d['missing_records']} suggested={d['suggested_action']}")
    assert d["current_node"] == "verdict_stale" and not d["derivable"] and "门未派生下一轮审计卡" in d["missing_records"][0]


# ===========================================================================
# ③ 单元: 判据同源 / 幂等 / 审计卡判据
# ===========================================================================

def test_item3_staleness_same_source_as_finalize_f70_and_idempotent_round(lrig, monkeypatch):
    from tools.aipos_cli import audit_derivation as ad
    from tools.aipos_cli.finalize import check_task_can_finalize

    _setup(lrig, monkeypatch)
    old_tip, _ = _phase1_pass_then_conflict(lrig)
    fm = nr._read_frontmatter(lrig.gov / "5_tasks" / "queue" / "claimed" / f"{TASK.lower()}.md")
    assert nr.verdict_staleness(lrig.gov, TASK, fm) is None  # tip 未变: 裁决仍覆盖 → 不过期(finalize 可过 F70)
    assert check_task_can_finalize(TASK, lrig.gov, commit_sha=old_tip)["can_finalize"]
    new_tip, _tree = _resolve_conflict_on_card_branch(lrig)
    stale = nr.verdict_staleness(lrig.gov, TASK, fm)
    _show(f"[③] verdict_staleness: {stale}")
    assert stale and stale["verdict_commit_sha"] == old_tip and stale["tip"] == new_tip and stale["audit_task_id"] == R1
    assert "须复审" in stale["reason"]  # 判定原文 = finalize F70 同一函数的拒因
    # 门侧派生: 当前一轮(R)已裁 → 演进 R2; 造出 R2(在途)后 → 幂等不演进 R3
    nxt = ad._reaudit_round(lrig.gov, TASK, fm, branch_id="code")
    assert nxt["audit_task_id"] == R2 and nxt["superseded_audit_task_id"] == R1
    f90._card(lrig.gov, R2, "pending", task_mode="audit", reviewed_task_id=TASK, derived_from=TASK)
    again = ad._reaudit_round(lrig.gov, TASK, fm, branch_id="code")
    _show(f"[③] R2 在途时再交回: {again}")
    assert again == {"skip": f"re-audit round {R2} already derived and awaiting its verdict (idempotency, AIPOS-F112)"}
    assert ad.current_audit_task_id(TASK, lrig.gov) == R2 and ad.audit_round_ids(TASK, lrig.gov) == [R1, R2]
    # 推导核等最新一轮 R2(不拿 R 的旧裁决派 finalize / 重交回)
    d = nr.derive_next_step(TASK, lrig.gov)
    assert d["action"] == {"type": "await_artifact", "card": R2} and d["current_node"] == "audit_dispatch", d
    # 分支已并入 main → finalize 不再要求精确覆盖 → 不过期
    _git(lrig.code_repo, "merge", "-q", "--no-ff", "-m", "merge", f"card/{TASK}")
    assert nr.verdict_staleness(lrig.gov, TASK, fm) is None


def test_item3_audit_card_predicate_single_source_knows_rounds():
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card, reviewed_task_id_of

    assert reviewed_task_id_of("AIPOS-F106R2") == "AIPOS-F106" and reviewed_task_id_of("AIPOS-F106R") == "AIPOS-F106"
    assert reviewed_task_id_of("AIPOS-F106R13") == "AIPOS-F106" and reviewed_task_id_of("AIPOS-F112") is None
    assert is_audit_card("AIPOS-F106R2") and not is_audit_card("AIPOS-F106")
    # 卡面为准: 存量执行卡号尾似轮号(AIPOS-R8)但卡面非审计 → 不是审计卡; 审计卡卡面 reviewed_task_id 优先
    assert not is_audit_card("AIPOS-R8", {"task_mode": "code"}) and is_audit_card("X-R2", {"task_mode": "audit"})
    assert audit_card_reviewed_id("X-R2", {"task_mode": "audit", "reviewed_task_id": "X"}) == "X"
    # 原各处 endswith("R") / [:-1] 已收口到唯一实现
    for rel in ("tools/aipos_cli/artifact_ingest.py", "tools/aipos_cli/loop_driver.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert 'endswith("R")' not in src and "task_id[:-1]" not in src and "target_card[:-1]" not in src, rel
    nr_src = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert 'task_id.upper().endswith("R")' not in nr_src and 'rstrip("Rr")' not in nr_src and 'branch_name = f"card/{' not in nr_src


def test_item3_verdict_stale_declared_and_no_swallowed_exceptions():
    from tools.aipos_cli.next_resolver import verdict_stale_declaration

    decl = verdict_stale_declaration()
    assert decl["state"] == "verdict_stale"
    for key in ("re_return", "legal_re_return_premise", "re_audit_derivation", "loop_wait_and_launch", "superseded_round"):
        assert decl["outlet"][key], key
    assert "INGEST_TIP_MISMATCH" in decl["return_not_updated_guard"]
    src = (REPO_ROOT / "tests" / "test_aipos_f112_verdict_stale_reaudit.py").read_text(encoding="utf-8")
    swallow, skip = "except " + "Exception", "pytest." + "skip"
    assert swallow not in src and skip not in src


def test_f112_fixture_registered_in_runall():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert ('run_pytest "tests/test_aipos_f112_verdict_stale_reaudit.py" '
            '"$REPO_ROOT/tests/test_aipos_f112_verdict_stale_reaudit.py"') in runall
