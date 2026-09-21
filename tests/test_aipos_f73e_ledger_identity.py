"""AIPOS-F73E — loop 账务动词身份定案落地夹具(靶场分根: tmp 治理根, schema 从产品根读)。

F73C 定案: token 归驱动方(advisor), actor/agent_instance 归该卡认领实例。F78 前置零② 把 close/finalize 的 actor 也写成了
驱动方, 2026-09-21 活体 `lybra loop --task-id AIPOS-F78` 派生 close 被门拒(「Task is claimed by another actor」)。

覆盖(对应卡面件①–③):
件① 推导核派生的 return/verdict/finalize/close 命令: --actor/--agent-instance = 该卡 claim 记录 agent_instance
    (审计卡=审计实例, 执行卡=执行实例); 无 claim 记录 → 不可推导(loop exit 4)点名 claim 记录; 驱动方实例永不进账务命令 --actor
件② 门侧: close 的 actor 校验按 actor==claimer 判(驱动方当 actor 即拒, 认领实例放行——判据既有, 不改);
    提交身份另记 submitted_by(MCP=capability token 实例, CLI=工位声明驱动方); 声明在 transitions record_authenticity.submission_identity 一处
件③ 靶场: 执行卡+审计卡真实记录推到 finalize 后 → run_loop(门替身) 派生 close actor==执行实例、verdict actor==审计实例、
    driver==驱动方; 先红(main: close --actor 驱动方)后绿。
靶场/替身复用 F73D 夹具(禁第二份靶场)。
"""
from __future__ import annotations

import inspect
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f73d_loop_driver as f73d  # noqa: E402  — 靶场 + GateDouble 唯一来源
from test_aipos_f73d_loop_driver import (  # noqa: E402
    AUDIT,
    AUDITOR,
    DRIVER,
    EXEC,
    POLICY,
    TASK,
    GateDouble,
    _card,
    _claim_record,
    _fm,
    _policy,
    _substantive_return,
    _ts,
    _write,
    gov,  # noqa: F401  — pytest fixture re-export
)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.board_adapter import audit_verdict_task, close_task  # noqa: E402
from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract, run_loop  # noqa: E402
from tools.aipos_cli.next_resolver import derive_next_step  # noqa: E402
from tools.aipos_cli.record_writer import (  # noqa: E402
    _confirmer_fields,
    build_closure_record_markdown,
    build_mcp_audit_verdict_record_markdown,
    build_return_skeleton_markdown,
)

SHA = "c" * 40


def _n5_records(gov_root: Path, task_id: str, *, executor: str | None) -> None:
    """执行卡推到 finalize 后(N5): return/dispatch/verdict(PASS)/finalization(merge_commit) 记录齐; executor=None 即无 claim 记录。"""
    rec = gov_root / "5_tasks" / "records"
    if executor:
        _claim_record(gov_root, task_id, executor)
    _write(rec / "returns" / task_id / f"return_{task_id}_{_ts()}_{EXEC}.md",
           _fm({"record_type": "return", "task_id": task_id, "return_id": f"return_{task_id}_x", "agent_instance": EXEC,
                "returned_at": "2026-09-21T01:00:00Z", "return_status": "returned"}))
    _write(rec / "audit_dispatches" / f"{task_id}R" / "dispatch_x.md",
           _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": task_id, "dispatched_at": "2026-09-21T02:00:00Z"}))
    _write(rec / "audit_verdicts" / task_id / "verdict_x.md",
           _fm({"record_type": "audit_verdict_record", "verdict_id": f"verdict_{task_id}_x", "verdict": "PASS",
                "reviewed_task_id": task_id, "auditor_instance": AUDITOR, "verdict_at": "2026-09-21T03:00:00Z"}))
    _write(rec / "finalizations" / task_id / "finalization_20260921_040000.md",
           _fm({"record_type": "finalization_record", "task_id": task_id, "finalized_at": "2026-09-21T04:00:00Z",
                "deploy_status": "deployed", "commit": SHA, "merge_commit": SHA}))


# ---------------------------------------------------------------------------
# 件① 派生命令身份
# ---------------------------------------------------------------------------

def test_f73e_item1_close_and_finalize_actor_is_claim_instance_never_driver(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _n5_records(gov, TASK, executor=EXEC)
    assert nr._driver_actor(gov) == DRIVER  # 工位声明在, 驱动方身份可解析——但不得进账务命令 --actor
    d = derive_next_step(TASK, gov)
    assert d["derivable"] and d["verb"] == "lybra_queue_close_dry_run", d
    assert f"--actor {EXEC}" in d["command"] and f"--actor {DRIVER}" not in d["command"], d["command"]
    assert d["triggered_by"] == "advisor"
    # N4→N5 finalize 同款: 去掉 finalization 记录 → 派生 finalize --actor 执行实例
    for f in (gov / "5_tasks" / "records" / "finalizations" / TASK).glob("*.md"):
        f.unlink()
    d2 = derive_next_step(TASK, gov)
    assert d2["derivable"] and d2["verb"] == "lybra_finalize", d2
    assert f"--actor {EXEC}" in d2["command"] and f"--actor {DRIVER}" not in d2["command"], d2["command"]


def test_f73e_item1_verdict_actor_is_audit_claim_instance_not_report_self_claim(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _card(gov, AUDIT, "claimed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": TASK})
    _claim_record(gov, AUDIT, AUDITOR)
    # 报告自报 actor=驱动方(错的): 推导核不读它, 只读审计卡 claim 记录
    _write(gov / "task_cards" / AUDIT / "audit_report.md",
           _fm({"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS", "actor": DRIVER, "agent_instance": DRIVER},
               "# audit\n\n## 一句话结论\nPASS\n"))
    d = derive_next_step(AUDIT, gov)
    assert d["derivable"] and d["verb"] == "lybra_audit_verdict_dry_run", d
    assert f"--actor {AUDITOR}" in d["command"] and f"--agent-instance {AUDITOR}" in d["command"], d["command"]
    assert DRIVER not in d["command"], d["command"]


def test_f73e_item1_return_actor_is_claim_instance(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
    d = derive_next_step(TASK, gov)
    assert d["derivable"] and d["verb"] == "lybra_queue_return_dry_run", d
    assert f"--actor {EXEC}" in d["command"] and f"--agent-instance {EXEC}" in d["command"], d["command"]


@pytest.mark.parametrize("stage", ["close", "finalize", "return", "verdict"])
def test_f73e_item1_no_claim_record_not_derivable_names_claim_record(gov: Path, stage: str):
    """无 claim 记录 = 不可推导(禁回退卡面 assigned_to / 驱动方), 点名 claim 记录, loop exit 4。"""
    if stage == "verdict":
        card = AUDIT
        _card(gov, TASK, "claimed", assigned=EXEC)
        _claim_record(gov, TASK, EXEC)
        rec = gov / "5_tasks" / "records"
        _write(rec / "returns" / TASK / f"return_{TASK}_x_{EXEC}.md",
               _fm({"record_type": "return", "task_id": TASK, "return_id": f"return_{TASK}_x", "agent_instance": EXEC,
                    "returned_at": "2026-09-21T01:00:00Z", "return_status": "returned"}))
        _write(rec / "audit_dispatches" / AUDIT / "dispatch_x.md",
               _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": TASK, "dispatched_at": "2026-09-21T02:00:00Z"}))
        _card(gov, AUDIT, "claimed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": TASK})
        _write(gov / "task_cards" / AUDIT / "audit_report.md",
               _fm({"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS"}, "# audit\n\n## 一句话结论\nPASS\n"))
    else:
        card = TASK
        _card(gov, TASK, "claimed", assigned=EXEC)
        if stage == "return":
            _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
        else:
            _n5_records(gov, TASK, executor=None)
            if stage == "finalize":
                for f in (gov / "5_tasks" / "records" / "finalizations" / TASK).glob("*.md"):
                    f.unlink()
    d = derive_next_step(card, gov)
    assert d["derivable"] is False, d
    assert d["command"] == "" and EXEC not in d["command"] and DRIVER not in d["command"]
    assert any("claim 记录" in m and f"claims/{card}/" in m for m in d["missing_records"]), d["missing_records"]
    assert "queue claim" in d["suggested_action"]
    assert (d.get("action") or {}).get("type") == "record_missing"
    # loop(从执行卡驱动; 审计卡经 N3 await_artifact 派生): 同一推导核 → 硬停 exit 4(verbs.schema lybra_loop.exit_codes.not_derivable)
    # 带出口——不把「缺 claim 记录」误当「agent 还在干活」空等到 exit 3
    _policy(gov)
    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=GateDouble(gov), interval=0.03, max_wait=1, max_steps=3)
    assert res.exit_code == exit_code_for(load_loop_contract(), "not_derivable"), out.getvalue()
    assert res.outcome == "not_derivable" and any("claim 记录" in m and f"claims/{card}/" in m for m in res.missing_records), res
    assert "记录缺失" in res.message and f"({card})" in res.message, res.message


# ---------------------------------------------------------------------------
# 件③ 靶场全链: run_loop(门替身) 派生的账务命令身份
# ---------------------------------------------------------------------------

def test_f73e_item3_loop_chain_close_actor_exec_verdict_actor_auditor_driver_only_for_envelope(gov: Path):
    _card(gov, TASK, "claimed", assigned=EXEC)
    _claim_record(gov, TASK, EXEC)
    _policy(gov)
    _write(gov / "task_cards" / TASK / "RETURN.md", build_return_skeleton_markdown(TASK))
    gate = GateDouble(gov)
    out = io.StringIO()

    def agents() -> None:
        time.sleep(0.15)
        _write(gov / "task_cards" / TASK / "RETURN.md", _substantive_return(TASK))
        deadline = time.time() + 5
        while time.time() < deadline and not (gov / "task_cards" / AUDIT / "RETURN.md").exists():
            time.sleep(0.02)
        time.sleep(0.15)
        # 审计体报告自报 actor 写成驱动方(错): 派生 verdict 仍按审计卡 claim 记录(GateDouble claim 落 AUDITOR)
        _write(gov / "task_cards" / AUDIT / "audit_report.md",
               _fm({"task_id": AUDIT, "reviewed_task_id": TASK, "verdict": "PASS", "actor": DRIVER, "agent_instance": DRIVER},
                   "# audit\n\n## 一句话结论\nPASS\n"))

    t = threading.Thread(target=agents, daemon=True)
    t.start()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=gate, interval=0.03, max_wait=8, max_steps=20)
    t.join(timeout=10)
    text = out.getvalue()
    assert res.exit_code == 0 and res.outcome == "completed", text
    assert [c[0] for c in gate.calls] == ["return", "dispatch", "claim", "verdict", "finalize", "close"], gate.calls
    assert f"driver={DRIVER}" in text  # 驱动方身份只用于信封
    by_action = {s.action_type: s.command for s in res.steps if s.kind == "execute" and s.command}
    assert f"--actor {EXEC} --confirm" in by_action["return"] and f"--agent-instance {EXEC}" in by_action["return"]
    assert f"--actor {AUDITOR}" in by_action["verdict"] and f"--agent-instance {AUDITOR}" in by_action["verdict"], by_action["verdict"]
    assert f"--actor {EXEC}" in by_action["finalize"], by_action["finalize"]
    assert f"lybra queue close --task-id {TASK} --actor {EXEC} " in by_action["close"], by_action["close"]
    assert "owner-dispatch" in by_action["dispatch"]  # 派审身份声明不改
    for action, cmd in by_action.items():
        assert f"--actor {DRIVER}" not in cmd, (action, cmd)  # 驱动方实例永不进账务命令 --actor
    assert "token" not in text.lower().replace("token 永不上屏", "")


# ---------------------------------------------------------------------------
# 件② 门侧: actor==claimer 判据(既有)+ submitted_by 记录
# ---------------------------------------------------------------------------

def _closable_root(tmp_path: Path, task_id: str) -> Path:
    root = tmp_path / "gate"
    q = root / "5_tasks" / "queue"
    for state in ("pending", "claimed", "completed", "blocked"):
        (q / state).mkdir(parents=True, exist_ok=True)
    (root / "governance" / "decision_log").mkdir(parents=True)
    (root / "governance" / "stage_archives").mkdir(parents=True)
    _write(root / "governance" / "decision_log" / "2026-09.md", f"# 2026-09\n\n## {task_id}\n\nx\n")
    _write(root / "governance" / "stage_archives" / "2026-09-21_stage.md", "# stage\n")
    _write(root / "project.json", json.dumps({"project": "lybra", "config_version": 1}))
    _write(root / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    _write(q / "claimed" / f"{task_id.lower()}.md", _fm({
        "task_id": task_id, "title": f"{task_id} close identity", "project": "lybra", "status": "claimed",
        "claimed_by": EXEC, "claimed_at": "'2026-09-21T00:00:00Z'",
        "claim_id": f"claim_{task_id}_20260921_000000Z_exec-lybra-test",
        "active_session_id": f"session_{task_id}_20260921_000000Z_exec-lybra-test",
        "assigned_to": EXEC, "agent_instance": EXEC, "context_bundle": EXEC, "task_mode": "code", "priority": "high",
        "created_by": DRIVER, "needs_owner": False, "output_target": "tools/", "artifact_policy": "formal_write",
        "audit": "required",
    }, f"# {task_id}\n"))
    rec = root / "5_tasks" / "records"
    _write(rec / "returns" / task_id / f"return_{task_id}_x.md",
           _fm({"record_type": "return_record", "task_id": task_id, "return_id": f"return_{task_id}_x", "returned_at": "'2026-09-21T01:00:00Z'"}))
    _write(rec / "audit_verdicts" / task_id / f"verdict_{task_id}_x.md",
           _fm({"record_type": "audit_verdict_record", "verdict_id": f"verdict_{task_id}_x", "verdict": "PASS",
                "reviewed_task_id": task_id, "verdict_at": "'2026-09-21T03:00:00Z'"}))
    return root


def _evidence(task_id: str) -> dict:
    return {"finalize_commit_hash": SHA, "finalize_return_ref": f"return_{task_id}_x", "verdict_ref": f"verdict_{task_id}_x"}


def test_f73e_item2_gate_close_rejects_driver_as_actor_accepts_claimer_records_submitted_by(tmp_path: Path):
    task_id = "AIPOS-F73EG1"
    root = _closable_root(tmp_path, task_id)
    # 红(F78 原派生): actor=驱动方 → 门拒(判据既有: actor==claimer)
    rejected = close_task(task_id=task_id, actor=DRIVER, closure_evidence=_evidence(task_id), dry_run=True, repo_root=root)
    reasons = " | ".join(str(r) for r in rejected.get("blocking_reasons") or [])
    assert rejected.get("verdict") == "BLOCK" and "claimed by another actor" in reasons, rejected
    # 绿: actor=认领实例, submitted_by=驱动方 → 放行, closure 记录两字段分离
    accepted = close_task(task_id=task_id, actor=EXEC, closure_evidence=_evidence(task_id), dry_run=False, repo_root=root, submitted_by=DRIVER)
    assert accepted.get("ok") and accepted.get("verdict") != "BLOCK", accepted
    closures = list((root / "5_tasks" / "records" / "closures" / task_id).glob("*.md"))
    assert len(closures) == 1, closures
    fm = nr._read_frontmatter(closures[0])
    assert fm.get("actor") == EXEC and fm.get("submitted_by") == DRIVER, fm
    assert (root / "5_tasks" / "queue" / "completed" / f"{task_id.lower()}.md").is_file()


def test_f73e_item2_cli_close_thin_shell_stamps_submitted_by_from_workstation_driver(tmp_path: Path):
    """`lybra queue close`(本地薄壳, 无 token): submitted_by = 工位声明驱动方(_driver_actor 唯一实现), actor 仍=认领实例。"""
    task_id = "AIPOS-F73EG2"
    root = _closable_root(tmp_path, task_id)
    env = {k: v for k, v in os.environ.items() if k not in ("LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(root), "queue", "close",
         "--task-id", task_id, "--actor", EXEC, "--closure-evidence", json.dumps(_evidence(task_id))],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    closures = list((root / "5_tasks" / "records" / "closures" / task_id).glob("*.md"))
    assert len(closures) == 1, proc.stdout + proc.stderr
    fm = nr._read_frontmatter(closures[0])
    assert fm.get("actor") == EXEC and fm.get("submitted_by") == DRIVER, fm


def test_f73e_item2_record_builders_and_mcp_attribution_carry_submitted_by(monkeypatch: pytest.MonkeyPatch):
    closure = build_closure_record_markdown(task_id="X", task_path="5_tasks/queue/claimed/x.md", actor=EXEC, closure_id="closure_X_x",
                                            closed_at="2026-09-21T05:00:00Z", closure_evidence={"type": "finalize", "ref": SHA}, submitted_by=DRIVER)
    assert f"\nactor: {EXEC}\n" in closure and f"\nsubmitted_by: {DRIVER}\n" in closure, closure
    verdict = build_mcp_audit_verdict_record_markdown(
        verdict_id="verdict_X_x", verdict="PASS", reviewed_task_id="X", reviewed_task_path="p", reviewed_return_record_ref="r",
        audit_dispatch_record_ref="d", audit_task_id="XR", audit_task_path="q", audit_claim_id="c", audit_session_id="s",
        reviewed_executor_instance=EXEC, auditor_instance=AUDITOR, actor=AUDITOR, canonical_agent_instance=AUDITOR,
        owner_policy_ref=POLICY, verdict_at="2026-09-21T03:00:00Z", findings_summary="ok", evidence_refs=[], recommended_next_action=None,
        submitted_by=DRIVER)
    assert f"\nactor: {AUDITOR}\n" in verdict and f"\nsubmitted_by: {DRIVER}\n" in verdict, verdict
    # return/claim 记录: confirmer 字段族 → submitted_by 同源
    assert _confirmer_fields({"submitted_by": DRIVER})["submitted_by"] == DRIVER
    assert _confirmer_fields(None)["submitted_by"] == ""
    # MCP: 提交身份 = confirming capability token 的 agent_instance 绑定, 缺绑定则角色名
    from tools.mcp_server import tools as mcp_tools

    with mcp_tools.request_capability_scope({"role": "advisor", "agent_instance": DRIVER, "fingerprint": "fp"}):
        att = mcp_tools._confirmer_attribution()
    assert att["submitted_by"] == DRIVER and att["confirmer_role"] == "advisor"
    with mcp_tools.request_capability_scope({"role": "advisor"}):
        assert mcp_tools._confirmer_attribution()["submitted_by"] == "advisor"
    # 裁决门: submitted_by 进 dry-run 载荷, confirm 原样承接(快照一致); close 门两相都带
    assert "submitted_by" in inspect.signature(audit_verdict_task).parameters
    assert "submitted_by" in inspect.signature(close_task).parameters
    ba_src = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    assert ba_src.count('submitted_by=payload.get("submitted_by")') == 2
    mcp_src = (REPO_ROOT / "tools/mcp_server/tools.py").read_text(encoding="utf-8")
    assert mcp_src.count('submitted_by=_confirmer_attribution().get("submitted_by")') == 3  # verdict dry-run + close dry-run/confirm


def test_f73e_item2_identity_split_declared_once_in_transitions_schema():
    text = (REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8")
    decl = json.loads(text)["record_authenticity"]["submission_identity"]
    assert text.count('"submission_identity"') == 1
    assert decl["field"] == "submitted_by"
    assert {"return", "audit_verdict", "closure"} <= set(decl["applies_to"])
    assert "actor" in decl["actor_rule"] and "claim" in decl["actor_rule"] and "驱动方" in decl["actor_rule"]
    assert "只记不判" in decl["submitted_by_rule"]
    assert "owner-dispatch" in decl["dispatch_exception"]


def test_f73e_fixture_registered_in_runall_and_no_swallowed_exceptions():
    runall = (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f73e_ledger_identity.py" in runall
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/aipos_cli/loop_driver.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"except Exception:\s*\n\s*pass", text), rel
    # 单一实现: 推导核只有一处读 claim 记录当 actor; 账务命令模板不再读驱动方身份
    nr_src = (REPO_ROOT / "tools/aipos_cli/next_resolver.py").read_text(encoding="utf-8")
    assert nr_src.count("def _claimer_instance(") == 1
    derive_src = inspect.getsource(derive_next_step)
    assert "_driver_actor(" not in derive_src, "推导核账务命令禁读驱动方身份当 actor"
