"""AIPOS-F100 件② — frontmatter「读不出即拒」: 每个改动调用方一个靶场用例(坏卡/坏记录 → 明确拒因, 不放行、不取缺省、不重写)。

坏 frontmatter 一律用真实坏卡同款(F87): `result_summary: **…**` 未加引号——PyYAML 与零依赖兜底解析器都报 YAML 错误
(有 PyYAML 时 safe_load 失败后兜底只丢坏键 + 告警), 读取口 parse_markdown_frontmatter 给出告警 → 必须读出入口
frontmatter.require_frontmatter 抛 FrontmatterReadError(「读不出: <路径>:<行>: <原因>」)。

每个用例打印「[F100 件②] <调用方> | <拒因原文>」供报告摘录; 同一用例集在 main 上跑即为「改前行为」对照。
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

TASK = "AIPOS-F100T"
EXEC = "exec.f100.test"
DRIVER = "advisor.f100.test"
BAD_LINE = "result_summary: **完成**: 坏卡(真实坏卡同款, 未加引号)"


def _show(caller: str, detail: object) -> None:
    print(f"[F100 件②] {caller} | {detail}")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _card_text(task_id: str, status: str, *, bad: bool, extra: list[str] | None = None) -> str:
    lines = ["---", f"task_id: {task_id}", f"title: {task_id} 靶场", "project: lybra", f"assigned_to: {EXEC}",
             f"agent_instance: {EXEC}", "task_mode: code", f"status: {status}", "needs_owner: false", "audit: required",
             f"claimed_by: {EXEC}", "lane:", "  repo: PRODUCT_REPO", "  paths:", "  - tools/", "  roles: []",
             *(extra or [])]
    if bad:
        lines.append(BAD_LINE)
    return "\n".join(lines + ["---", f"# {task_id}", "", "正文。", ""])


@pytest.fixture
def gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = tmp_path / "gov"
    for sub in ("pending", "claimed", "completed", "blocked"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "sessions"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "5_tasks" / "policies").mkdir()
    product = tmp_path / "product"
    product.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=product, check=True)
    _write(root / "project.json", json.dumps({"project": "lybra", "code_repo": str(product), "config_version": 1}))
    _write(root / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    return root


def _put_card(gov: Path, status: str, *, bad: bool, task_id: str = TASK, extra: list[str] | None = None) -> Path:
    product = json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"]
    text = _card_text(task_id, status, bad=bad, extra=extra).replace("PRODUCT_REPO", product)
    return _write(gov / "5_tasks" / "queue" / status / f"{task_id.lower()}.md", text)


# ---------------------------------------------------------------------------
# 入口本身
# ---------------------------------------------------------------------------

def test_require_frontmatter_is_a_thin_wrapper_with_path_line_and_reason(gov: Path):
    from tools.aipos_cli.frontmatter import FrontmatterReadError, parse_markdown_frontmatter, require_frontmatter

    card = _put_card(gov, "claimed", bad=True)
    with pytest.raises(FrontmatterReadError) as ctx:
        require_frontmatter(card)
    _show("frontmatter.require_frontmatter", ctx.value)
    assert str(ctx.value).startswith(f"读不出: {card}:17: ") and ctx.value.line_no == 17 and "result_summary" in ctx.value.reason
    # 同一读取口: 能读出时与 parse_markdown_frontmatter 同值
    good = _put_card(gov, "claimed", bad=False, task_id="AIPOS-F100G")
    data, body = require_frontmatter(good)
    assert (data, body, []) == parse_markdown_frontmatter(good.read_text(encoding="utf-8"))
    # 无块 / 文件不可读 / 未闭合
    plain = _write(gov / "notes.md", "# 无 frontmatter\n")
    with pytest.raises(FrontmatterReadError, match="无 frontmatter 块"):
        require_frontmatter(plain)
    assert require_frontmatter(plain, allow_missing_block=True) == ({}, "# 无 frontmatter\n")
    with pytest.raises(FrontmatterReadError, match="文件不可读"):
        require_frontmatter(gov / "missing.md")
    with pytest.raises(FrontmatterReadError) as ctx2:
        require_frontmatter(_write(gov / "open.md", "---\na: 1\n"))
    assert ctx2.value.line_no == 1


# ---------------------------------------------------------------------------
# 推导核 / loop(缺口 #2)
# ---------------------------------------------------------------------------

def test_derive_next_step_hard_stops_on_unreadable_card(gov: Path):
    from tools.aipos_cli.next_resolver import derive_next_step

    _put_card(gov, "claimed", bad=True)
    d = derive_next_step(TASK, gov)
    _show("next_resolver.derive_next_step(坏卡)", {"action": d["action"], "missing": d["missing_records"][0][:160], "exit": d["suggested_action"]})
    assert d["derivable"] is False and d["command"] == "" and d["action"]["type"] == "frontmatter_unreadable"
    assert d["missing_records"][0].startswith("读不出: ") and d["action"]["path"].endswith(f"{TASK.lower()}.md")
    assert f"lybra state repair --task-id {TASK}" in d["suggested_action"]


def test_derive_next_step_hard_stops_on_unreadable_record(gov: Path):
    from tools.aipos_cli.next_resolver import derive_next_step

    _put_card(gov, "claimed", bad=False)
    bad_claim = _write(gov / "5_tasks" / "records" / "claims" / TASK / f"claim_{TASK}_x_{EXEC}.md",
                       f"---\nrecord_type: claim_record\ntask_id: {TASK}\nagent_instance: {EXEC}\n{BAD_LINE}\n---\n")
    d = derive_next_step(TASK, gov)
    _show("next_resolver.derive_next_step(坏 claim 记录)", {"action": d["action"], "missing": d["missing_records"][0][:160]})
    assert d["action"] == {"type": "frontmatter_unreadable", "card": TASK, "path": str(bad_claim)}
    assert "记录/产物由写它的一方重写" in d["suggested_action"]


def test_scan_project_lists_unreadable_card_as_hard_stop(gov: Path):
    from tools.aipos_cli.next_resolver import scan_project

    _put_card(gov, "claimed", bad=True)
    rows = scan_project(gov)
    _show("next_resolver.scan_project", [(r["task_id"], (r.get("action") or {}).get("type"), r["current_state"]) for r in rows])
    assert [(r["task_id"], r["action"]["type"], r["current_state"]) for r in rows] == [(TASK, "frontmatter_unreadable", "claimed")]


def test_loop_exit4_names_file_for_unreadable_card(gov: Path):
    from tools.aipos_cli.loop_driver import HARD_STOP_ACTIONS, run_loop

    _put_card(gov, "claimed", bad=True)
    out = io.StringIO()
    res = run_loop(TASK, gov, actor=DRIVER, out=out, execute=lambda *a, **k: pytest.fail("must not execute"),
                   interval=0.01, max_wait=0.05)
    _show("loop_driver.run_loop", {"exit": res.exit_code, "outcome": res.outcome, "message": res.message[:160]})
    assert res.exit_code == 4 and res.outcome == "not_derivable" and res.message.startswith("读不出: ")
    assert "lybra state repair" in res.suggested_action and "frontmatter_unreadable" in HARD_STOP_ACTIONS


def test_plan_launch_refuses_for_unreadable_card(gov: Path):
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract, plan_launch

    _put_card(gov, "claimed", bad=True)
    plan = plan_launch(gov, TASK, policy={"launch_harnesses": ["claude-code"]}, no_launch=False,
                       decl=launch_declaration(load_loop_contract()), already_launched=set())
    _show("loop_driver.plan_launch", plan.refusal[:160])
    assert plan.refusal.startswith("读不出: ") and not plan.harness


def test_execute_derived_action_fails_closed_when_card_unreadable(gov: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import next_resolver as nr

    _put_card(gov, "claimed", bad=True)
    monkeypatch.setattr(nr, "_run_product_command", lambda *a, **k: pytest.fail("must not run a product command"))
    res = nr.execute_derived_action({"derivable": True, "task_id": TASK, "command": f"lybra queue close --task-id {TASK} --actor {EXEC} --closure-evidence '{{}}'"}, gov)
    _show("next_resolver.execute_derived_action", {"ok": res["ok"], "exit": res["exit_code"], "message": res["message"][:160]})
    assert res["ok"] is False and res["exit_code"] == 4 and "读不出: " in res["message"]


def test_kickoff_and_worktree_refuse_unreadable_card(gov: Path):
    from tools.aipos_cli.next_resolver import _ensure_worktree, kickoff_refusal

    _put_card(gov, "claimed", bad=True)
    refusal = kickoff_refusal(gov, TASK, EXEC, queue_state="claimed", card_frontmatter={})
    wt = _ensure_worktree(gov, TASK)
    _show("next_resolver.kickoff_refusal / _ensure_worktree", {"kickoff": refusal, "worktree": wt["message"][:120]})
    assert refusal["code"] == "FRONTMATTER_UNREADABLE" and "读不出: " in refusal["reason"]
    assert wt["ok"] is False and "读不出: " in wt["message"]


def test_artifact_ingest_rejects_unreadable_card(gov: Path):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    _put_card(gov, "claimed", bad=True)
    check = validate_task_artifact(TASK, gov)
    _show("artifact_ingest.validate_task_artifact", {"category": check["category"], "reason": check["reasons"][0][:160]})
    assert check["ok"] is False and check["category"] == "INGEST_FRONTMATTER_UNREADABLE" and check["reasons"][0].startswith("读不出: ")


def test_write_boundary_refuses_unreadable_card(gov: Path):
    from tools.aipos_cli.write_boundary import WriteBoundaryError, _card_lane_paths

    _put_card(gov, "claimed", bad=True)
    with pytest.raises(WriteBoundaryError) as ctx:
        _card_lane_paths(gov, TASK)
    _show("write_boundary._card_lane_paths", str(ctx.value)[:160])
    assert "读不出: " in str(ctx.value)


def test_card_render_refuses_unreadable_card(gov: Path):
    from tools.aipos_cli.card_render import build_intent_model
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    _put_card(gov, "claimed", bad=True)
    with pytest.raises(FrontmatterReadError) as ctx:
        build_intent_model(TASK, gov)
    _show("card_render.build_intent_model", str(ctx.value)[:160])


# ---------------------------------------------------------------------------
# 把空值当「不判/未派」放行(缺口 #3)
# ---------------------------------------------------------------------------

def test_finalize_claimer_check_refuses_unreadable_card(gov: Path):
    from tools.aipos_cli.finalize import _actor_is_claimer

    _put_card(gov, "claimed", bad=True)
    res = _actor_is_claimer(gov, TASK, "someone.else")
    _show("finalize._actor_is_claimer", res)
    assert res["ok"] is False and "读不出: " in res["reason"]


def test_finalize_report_display_shows_unreadable(gov: Path):
    from tools.aipos_cli.finalize import _report_frontmatter_verdict_for_display

    _put_card(gov, "claimed", bad=False)
    report = _write(gov / "task_cards" / f"{TASK}R" / "RETURN.md", f"---\nverdict: PASS\ncommit_sha: abc\n{BAD_LINE}\n---\n# r\n")
    shown = _report_frontmatter_verdict_for_display(gov, TASK)
    _show("finalize._report_frontmatter_verdict_for_display", shown)
    assert shown["report_path"] == str(report) and str(shown["report_verdict"]).startswith("读不出: ")


def test_dispatch_chain_unreadable_dispatch_record_blocks(gov: Path):
    from tools.aipos_cli.audit_helpers import is_dispatch_chain_valid

    ref = "5_tasks/records/audit_dispatches/AIPOS-F100T/dispatch_x.md"
    _write(gov / ref, f"---\nrecord_type: audit_dispatch\naudit_task_id: {TASK}R\n{BAD_LINE}\n---\n")
    valid, superseded = is_dispatch_chain_valid({"audit_dispatch_record_ref": ref}, [], gov)
    _show("audit_helpers.is_dispatch_chain_valid", {"chain_valid(=阻止重派)": valid, "superseded": superseded})
    assert (valid, superseded) == (True, None)


# ---------------------------------------------------------------------------
# 读-改-写(缺口 #4/#5/#6)
# ---------------------------------------------------------------------------

def test_rework_round_blocks_on_unreadable_card_and_on_corrupt_rounds(gov: Path):
    from tools.aipos_cli.queue_mutation import build_rework_round

    card = _put_card(gov, "claimed", bad=True)
    res = build_rework_round(repo_root=gov, task_id=TASK, verdict_ref="v", focus_items=["x"], actor=DRIVER)
    _show("queue_mutation.build_rework_round(坏卡)", res["blocking_reasons"][0][:160])
    assert res["verdict"] == "BLOCK" and "读不出: " in res["blocking_reasons"][0]
    card.write_text(_card_text(TASK, "claimed", bad=False, extra=["rework_rounds: two rounds"]), encoding="utf-8")
    res2 = build_rework_round(repo_root=gov, task_id=TASK, verdict_ref="v", focus_items=["x"], actor=DRIVER)
    _show("queue_mutation.build_rework_round(rework_rounds 非列表)", res2["blocking_reasons"][0][:160])
    assert res2["verdict"] == "BLOCK" and "不是列表" in res2["blocking_reasons"][0]


def test_amend_refuses_and_does_not_rewrite_unreadable_card(gov: Path):
    from tools.aipos_cli.board_adapter import amend_task

    card = _put_card(gov, "pending", bad=True)
    before = card.read_bytes()
    res = amend_task(task_id=TASK, actor=DRIVER, amendments={"priority": "high"}, amendment_reason="r", dry_run=False, repo_root=gov)
    _show("board_adapter.amend_task", {"verdict": res.get("verdict"), "blocking": res["blocking_reasons"][0][:200]})
    assert res.get("verdict") == "BLOCK" and res["blocking_reasons"][0].startswith("FRONTMATTER_UNREADABLE: ")
    assert "读不出: " in res["blocking_reasons"][0]
    assert card.read_bytes() == before


def test_state_repair_refuses_and_does_not_rewrite_unreadable_card(gov: Path):
    from tools.aipos_cli.state_lint import _repair_queue_state

    card = _put_card(gov, "pending", bad=True)
    _write(gov / "5_tasks" / "records" / "claims" / TASK / f"claim_{TASK}_x.md",
           f"---\nrecord_type: claim_record\ntask_id: {TASK}\nclaimed_at: '2026-10-04T00:00:00Z'\n---\n")
    before = card.read_bytes()
    res = _repair_queue_state(gov, TASK, dry_run=False)
    _show("state_lint._repair_queue_state", {"repaired": res["repaired"], "message": res["message"][:160]})
    assert res["repaired"] is False and "读不出: " in res["message"] and card.read_bytes() == before


def test_session_progress_update_refuses_unreadable_record(gov: Path):
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.task_progress_writer import _update_session_record

    session = _write(gov / "5_tasks" / "records" / "sessions" / TASK / f"session_{TASK}_x.md",
                     f"---\nrecord_type: session_record\nsession_id: s\n{BAD_LINE}\n---\n# s\n")
    before = session.read_bytes()
    with pytest.raises(FrontmatterReadError) as ctx:
        _update_session_record(session, repo_root=gov, task_id=TASK, actor=EXEC, event_type="progress",
                               timestamp="2026-10-04T00:00:00Z", summary="x")
    _show("task_progress_writer._update_session_record", str(ctx.value)[:160])
    assert session.read_bytes() == before


# ---------------------------------------------------------------------------
# 静默跳过的检查(缺口 #7)
# ---------------------------------------------------------------------------

def test_project_map_staleness_names_unreadable_files(gov: Path):
    from tools.aipos_cli.draft_writer import _check_project_map_staleness

    _write(gov / "governance" / "project-map.md", "---\nupdated: '2026-09-01T00:00:00Z'\n---\n# map\n")
    _write(gov / "5_tasks" / "records" / "returns" / TASK / "return_x.md", f"---\nreturned_at: '2026-10-01T00:00:00Z'\n{BAD_LINE}\n---\n")
    _write(gov / "5_tasks" / "records" / "returns" / "OTHER" / "return_y.md", "---\nreturned_at: '2026-10-02T00:00:00Z'\n---\n")
    validation = {"warnings": []}
    _check_project_map_staleness(gov, validation)
    _show("draft_writer._check_project_map_staleness", validation["warnings"])
    assert any(w.startswith("PROJECT_MAP_STALENESS_PARTIAL") and "读不出: " in w for w in validation["warnings"])
    assert any(w.startswith("PROJECT_MAP_STALE ") for w in validation["warnings"])
    _write(gov / "governance" / "project-map.md", f"---\nupdated: '2026-09-01T00:00:00Z'\n{BAD_LINE}\n---\n")
    validation2 = {"warnings": []}
    _check_project_map_staleness(gov, validation2)
    assert validation2["warnings"] and validation2["warnings"][0].startswith("PROJECT_MAP_UNREADABLE (新鲜度未检查: 读不出: ")


def test_lineage_chain_raises_instead_of_ending_early(gov: Path):
    from tools.aipos_cli.deployment_authorization import _find_continuation_task, _find_fix_chain_terminal
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    _write(gov / "5_tasks" / "records" / "fix_closures" / TASK / "derivation_1.md",
           f"---\nsource_task_id: {TASK}\nfix_task_id: {TASK}-FIX\n{BAD_LINE}\n---\n")
    with pytest.raises(FrontmatterReadError) as ctx:
        _find_fix_chain_terminal(TASK, gov)
    _put_card(gov, "completed", bad=True)
    with pytest.raises(FrontmatterReadError) as ctx2:
        _find_continuation_task(TASK, gov)
    _show("deployment_authorization._find_fix_chain_terminal / _find_continuation_task", [str(ctx.value)[:100], str(ctx2.value)[:100]])


# ---------------------------------------------------------------------------
# 展示面(缺口 #8): 显示「读不出: <路径>: <原因>」而非省略
# ---------------------------------------------------------------------------

def test_brief_lists_unreadable_files(gov: Path, capsys: pytest.CaptureFixture[str]):
    from tools.aipos_cli.brief import run_brief

    (gov / "governance" / "decision_log").mkdir(parents=True)
    (gov / "governance" / "governance_docs").mkdir(parents=True)
    _put_card(gov, "claimed", bad=True)
    _put_card(gov, "claimed", bad=False, task_id="AIPOS-F100G")
    rc = run_brief(gov, output_format="json")
    payload = json.loads(capsys.readouterr().out)
    _show("brief.run_brief", payload["unreadable"])
    assert rc == 0 and len(payload["unreadable"]) == 1 and payload["unreadable"][0].startswith("读不出: ")
    rc2 = run_brief(gov, output_format="text")
    text = capsys.readouterr().out
    assert rc2 == 0 and "【6. 读不出的文件】" in text and "  - 读不出: " in text


def test_authority_scanner_names_unreadable_draft(gov: Path):
    from tools.aipos_cli.authority_scanner import _draft_findings

    _write(gov / "5_tasks" / "drafts" / "bad.md", f"---\ntask_id: X\n{BAD_LINE}\n---\n")
    findings = _draft_findings(gov)
    _show("authority_scanner._draft_findings", [(f["reason_code"], f["reason"][:100]) for f in findings])
    assert [f["reason_code"] for f in findings] == ["DRAFT_FRONTMATTER_UNREADABLE"] and findings[0]["reason"].startswith("读不出: ")


def test_settlement_status_does_not_count_unreadable_closure(gov: Path):
    from tools.aipos_cli.board_adapter import read_settlement_status

    _put_card(gov, "completed", bad=False)
    _write(gov / "5_tasks" / "records" / "closures" / TASK / f"close_{TASK}_x.md", f"---\nrecord_type: closure\n{BAD_LINE}\n---\n")
    _write(gov / "5_tasks" / "records" / "finalizations" / TASK / "finalization_x.md", "---\nrecord_type: finalization\n---\n")
    status = read_settlement_status(TASK, gov)
    _show("board_adapter.read_settlement_status", {"is_settled": status["is_settled"], "missing": status["missing"], "unreadable": status["unreadable"]})
    assert status["is_settled"] is False and "closure_record" in status["missing"]
    assert len(status["unreadable"]) == 1 and status["unreadable"][0].startswith("读不出: ")


# ---------------------------------------------------------------------------
# 读取口统一(零依赖链路发现: 原各自 import yaml 的读点)
# ---------------------------------------------------------------------------

def test_envelope_reader_reads_through_single_reader(gov: Path):
    """AIPOS-F103 件④ 合并改写: 信封读取唯一实现 = autonomy_policy.load_policy(经 parse_markdown_frontmatter; 旧按角色词挑选的
    解析模块已删, F100 对其 import yaml 的修复随之落在唯一实现上)。读不出(任何解析告警)= None = 不算有效信封(fail-closed)。"""
    from tools.aipos_cli import autonomy_policy

    import re

    src = Path(autonomy_policy.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import yaml|from yaml)", src, re.M)
    assert "parse_markdown_frontmatter(" in src
    _write(gov / "5_tasks" / "policies" / "pol_bad.md",
           f"---\nrecord_type: owner_autonomy_policy\npolicy_id: pol_bad\nstatus: active\nagent_or_role: exec\n{BAD_LINE}\n---\n")
    assert autonomy_policy.load_policy(gov, "pol_bad") is None
    _write(gov / "5_tasks" / "policies" / "pol_ok.md",
           "---\nrecord_type: owner_autonomy_policy\npolicy_id: pol_ok\nstatus: active\nmode: PreAuthorized\nagent_or_role: exec\n---\n")
    ok = autonomy_policy.load_policy(gov, "pol_ok")
    assert ok is not None and ok["policy_id"] == "pol_ok" and ok["status"] == "active"


def test_agent_profiles_name_unreadable_profile_block(gov: Path):
    from tools.aipos_cli.agent_profiles import load_agent_profiles, registry_available

    _write(gov / "0_control_plane" / "agents" / "x_runtime_profiles.md",
           "# profiles\n\n```yaml\nagent_id: a1\ninstances:\n  - instance_id: i1\n```\n\n```yaml\nagent_id: a2\nbad: **x**\n```\n")
    profiles = load_agent_profiles(gov)
    _show("agent_profiles.load_agent_profiles", profiles["registry_warnings"])
    assert registry_available() is True
    assert any("x_runtime_profiles.md" in w and "unreadable" in w for w in profiles["registry_warnings"])
