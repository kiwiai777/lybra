"""AIPOS-F122 接入既有项目①: 存量冻结(迁移基线)靶场夹具。

靶场 = 临时治理根自造「人肉期」样本(通用项目, 不涉任何真实项目): claimed 无 claim、completed 无 closure、门生名记录无 frontmatter、
人手写 RETURN 与门记录同目录(project.json paths.return_root = 5_tasks/records/returns)。

件① 声明: project.json legacy_baseline 缺段 = 行为不变; 清单 = 带 record_type 的只追加记录(每批一条, 解冻 = 追加 unfreeze 条目)。
件② 命令: lybra project freeze-legacy 干跑零写入列卡与 lint 计数; --confirm 写清单并声明落点; 未知卡号拒; 重复冻结幂等。
件③ 单源判定 legacy_baseline.frozen_tasks: state lint(全项目 ERROR→0 + frozen N; 单卡照实报标 frozen)、next 扫描不列、
      门写动作(claim/return/dispatch/verdict/finalize)与 loop --task-id 拒 LEGACY_FROZEN 附解冻命令; 清单读不出 = fail-closed。
件④ lint 认门记录 = transitions record_locations.gate_record_file_criterion(与推导核同一实现 record_writer.gate_record_files)。

跑法: python3 -m pytest tests/test_aipos_f122_legacy_baseline.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAME_DIR_RETURN_ROOT = "5_tasks/records/returns"


def _card(gov: Path, state: str, tid: str, status: str | None = None) -> Path:
    path = gov / "5_tasks" / "queue" / state / f"{tid.lower()}.md"
    path.write_text(
        f"---\ntask_id: {tid}\ntitle: {tid} sample\nproject: sample\nstatus: {status or state}\ntask_mode: code\n---\n# {tid}\n",
        encoding="utf-8",
    )
    return path


def _make_gov(tmp_path: Path, monkeypatch, *, prefix: str = "DEMO", return_root: str | None = SAME_DIR_RETURN_ROOT) -> Path:
    """人肉期样本治理根。prefix 参数化卡号前缀(产品不得写死卡号前缀/项目 ID)。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    gov = tmp_path / "gov"
    for state in ("pending", "claimed", "completed", "blocked"):
        (gov / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    (gov / "governance").mkdir(parents=True, exist_ok=True)
    project: dict = {"project": f"{prefix.lower()}-proj", "config_version": 1}
    if return_root:
        project["paths"] = {"return_root": return_root, "verdict_root": return_root}
    (gov / "project.json").write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")
    rec = gov / "5_tasks" / "records"
    returns_dir = gov / (return_root or "task_cards")
    _card(gov, "claimed", f"{prefix}-1")                               # claimed 无 claim
    _card(gov, "completed", f"{prefix}-2")                             # completed 无 closure
    _card(gov, "claimed", f"{prefix}-3")                               # 门生名记录无 frontmatter
    (rec / "claims" / f"{prefix}-3").mkdir(parents=True)
    (rec / "claims" / f"{prefix}-3" / f"claim_{prefix}-3_20260801_000000_human.md").write_text("人肉期手写, 无 frontmatter\n", encoding="utf-8")
    _card(gov, "blocked", f"{prefix}-5")
    _card(gov, "completed", f"{prefix}-6")                             # 人手写 RETURN(无 frontmatter)
    (returns_dir / f"{prefix}-6").mkdir(parents=True, exist_ok=True)
    (returns_dir / f"{prefix}-6" / "RETURN.md").write_text("# 人手写 Return\n", encoding="utf-8")
    _card(gov, "claimed", f"{prefix}-4")                               # 门流程卡: 门生 claim + 同目录人手写 RETURN
    (rec / "claims" / f"{prefix}-4").mkdir(parents=True)
    (rec / "claims" / f"{prefix}-4" / f"claim_{prefix}-4_20261001_000000_exec.md").write_text(
        f"---\nrecord_type: claim\ntask_id: {prefix}-4\nclaim_id: c1\nagent_instance: exec.sample\nclaimed_at: '2026-10-01T00:00:00Z'\n---\nclaim\n",
        encoding="utf-8")
    (returns_dir / f"{prefix}-4").mkdir(parents=True, exist_ok=True)
    (returns_dir / f"{prefix}-4" / "RETURN.md").write_text("# 执行体手写 Return(无 frontmatter)\n", encoding="utf-8")
    _card(gov, "pending", f"{prefix}-9")                               # 活跃卡(不冻结)
    return gov


def _cli(argv: list[str], capsys) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    capsys.readouterr()
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _freeze(gov: Path, capsys, *extra: str, prefix: str = "DEMO") -> tuple[int, str, str]:
    return _cli(["project", "freeze-legacy", "--workspace-root", str(gov), "--queue", "claimed,completed,blocked",
                 "--exclude", f"{prefix}-4", "--reason", "人肉期存量", "--actor", "advisor.sample", *extra], capsys)


def _lint(gov: Path, capsys, *extra: str) -> tuple[int, dict]:
    rc, out, _err = _cli(["state", "lint", "--workspace-root", str(gov), "--json", *extra], capsys)
    return rc, json.loads(out)


def _tree_digest(gov: Path) -> dict[str, str]:
    return {p.relative_to(gov).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(gov.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------
# 件④ 判据单源
# ---------------------------------------------------------------------------

def test_item4_gate_record_prefix_read_from_declaration_and_shared():
    from tools.aipos_cli.record_writer import CLOSURE_ID_PREFIX, record_file_prefix

    expected = {"returns": "return", "claims": "claim", "closures": "close", "publishes": "publish",
                "audit_dispatches": "dispatch", "audit_verdicts": "verdict"}
    assert {k: record_file_prefix(k) for k in expected} == expected
    assert record_file_prefix("closures") == CLOSURE_ID_PREFIX  # 写侧常量与声明读侧一致
    transitions = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    assert "gate_record_files" in transitions["record_locations"]["gate_record_file_criterion"]
    lint_src = (REPO_ROOT / "tools" / "aipos_cli" / "state_lint.py").read_text(encoding="utf-8")
    assert "gate_record_files(type_dir, record_type)" in lint_src and 'type_dir.glob("*.md")' not in lint_src


@pytest.mark.parametrize("return_root", [SAME_DIR_RETURN_ROOT, None])
def test_item4_hand_return_beside_gate_records_is_not_a_gate_record(tmp_path, monkeypatch, capsys, return_root):
    gov = _make_gov(tmp_path, monkeypatch, return_root=return_root)
    from tools.aipos_cli.next_resolver import _read_task_records

    rc, lint = _lint(gov, capsys, "--task-id", "DEMO-4")
    assert lint["issues"] == [], lint
    assert _read_task_records(gov, "DEMO-4")["latest_return"] is None
    # 人手写 RETURN 带 returned_at 也不让 lint 把 completed 卡推成 returned(不当门记录)
    rc, lint6 = _lint(gov, capsys, "--task-id", "DEMO-6")
    assert [i["message"] for i in lint6["issues"] if i["severity"] == "ERROR"] == ["卡在 completed/ 但无 closure 记录(断层)"], lint6


# ---------------------------------------------------------------------------
# 件① 声明 / 件② 命令
# ---------------------------------------------------------------------------

def test_item1_undeclared_is_behavior_unchanged(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    from tools.aipos_cli.legacy_baseline import frozen_tasks, frozen_rejection
    from tools.aipos_cli.workspace_config import project_legacy_baseline

    assert project_legacy_baseline(gov) is None and frozen_tasks(gov) == {}
    assert frozen_rejection(gov, ["DEMO-1"], action="claim") is None
    rc, lint = _lint(gov, capsys)
    assert rc == 1 and lint["frozen"] == 0 and len([i for i in lint["issues"] if i["severity"] == "ERROR"]) == 4
    decl = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    lb = decl["configuration_sources"]["project_json"]["schema"]["legacy_baseline"]
    assert lb["required"] is False and lb["schema"]["manifest_dir"]["default"]
    assert set(lb["reject_codes"]) >= {"LEGACY_FROZEN", "LEGACY_UNKNOWN_TASK", "LEGACY_BASELINE_INVALID"}


def test_item2_dry_run_lists_cards_with_lint_counts_and_writes_nothing(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    before = _tree_digest(gov)
    rc, out, _err = _freeze(gov, capsys, "--dry-run")
    print(out)
    assert rc == 0 and _tree_digest(gov) == before
    for line in ("DEMO-1  queue=claimed  status=claimed  lint_issues=1", "DEMO-2  queue=completed  status=completed  lint_issues=2",
                 "DEMO-3  queue=claimed  status=claimed  lint_issues=1", "DEMO-5  queue=blocked  status=blocked  lint_issues=0",
                 "排除: DEMO-4", "dry-run 零写入"):
        assert line in out, line
    assert "DEMO-9" not in out  # pending 不在 --queue 范围


def test_item2_unknown_task_refused_with_zero_writes(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    before = _tree_digest(gov)
    rc, _out, err = _cli(["project", "freeze-legacy", "--workspace-root", str(gov), "--task-ids", "DEMO-1", "DEMO-404",
                          "--reason", "x", "--actor", "advisor.sample", "--confirm"], capsys)
    assert rc == 1 and "LEGACY_UNKNOWN_TASK" in err and "DEMO-404" in err
    rc, _out, err = _cli(["project", "freeze-legacy", "--workspace-root", str(gov), "--queue", "claimed,nosuch",
                          "--reason", "x", "--actor", "advisor.sample", "--confirm"], capsys)
    assert rc == 1 and "LEGACY_UNKNOWN_TASK" in err
    assert _tree_digest(gov) == before


def test_item2_confirm_appends_manifest_declares_location_and_is_idempotent(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    cards_before = {k: v for k, v in _tree_digest(gov).items() if k != "project.json"}
    rc, out, _err = _freeze(gov, capsys, "--owner-policy-ref", "owner-decision-sample", "--confirm")
    assert rc == 0, out
    project = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    assert project["legacy_baseline"] == {"manifest_dir": "5_tasks/records/legacy_baseline"}
    assert project["paths"]["return_root"] == SAME_DIR_RETURN_ROOT  # 其余键原样
    entries = sorted((gov / "5_tasks/records/legacy_baseline").glob("*.md"))
    assert len(entries) == 1
    from tools.aipos_cli.frontmatter import require_frontmatter

    meta, _body = require_frontmatter(entries[0])
    assert meta["record_type"] == "legacy_baseline" and meta["action"] == "freeze"
    assert meta["frozen_by"] == "advisor.sample" and meta["reason"] == "人肉期存量" and meta["frozen_at"]
    assert meta["owner_policy_ref"] == "owner-decision-sample"
    assert meta["task_ids"] == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-5", "DEMO-6"]
    assert {"task_id": "DEMO-5", "queue_state": "blocked", "card_status": "blocked"} in meta["tasks"]
    # 不移动、不改写任何卡文件与既有记录(只新增清单条目 + project.json 声明)
    after = _tree_digest(gov)
    assert all(after.get(k) == v for k, v in cards_before.items())
    # 经既有入库护栏 B④(records/** 新增须携 record_type)
    from tools.aipos_cli.governance_guardrails import check_entries, load_guardrail_declarations

    rel = entries[0].relative_to(gov).as_posix()
    report = check_entries(gov, [("A", rel)], load_guardrail_declarations(REPO_ROOT / "schema"), current_branch="main")
    assert report.ok, report.to_dict()
    # 重复冻结 = 幂等提示, 不写第二条
    rc, out, _err = _cli(["project", "freeze-legacy", "--workspace-root", str(gov), "--task-ids", "DEMO-1",
                          "--reason", "again", "--actor", "advisor.sample", "--confirm"], capsys)
    assert rc == 0 and "已冻结(幂等, 不重复写): DEMO-1" in out
    assert len(list((gov / "5_tasks/records/legacy_baseline").glob("*.md"))) == 1


# ---------------------------------------------------------------------------
# 件③ 读取方单源
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["DEMO", "XQ7"])
def test_item3_lint_before_after_error_to_zero_with_frozen_summary(tmp_path, monkeypatch, capsys, prefix):
    gov = _make_gov(tmp_path, monkeypatch, prefix=prefix)
    rc, before = _lint(gov, capsys)
    errors_before = [i for i in before["issues"] if i["severity"] == "ERROR"]
    assert rc == 1 and len(errors_before) == 4, before
    assert _freeze(gov, capsys, "--confirm", prefix=prefix)[0] == 0
    rc, after = _lint(gov, capsys)
    assert rc == 0 and after["issues"] == [] and after["frozen"] == 5, after
    rc, out, _err = _cli(["state", "lint", "--workspace-root", str(gov)], capsys)
    assert rc == 0 and "frozen 5" in out
    # --task-id 单卡照实报并标注 frozen
    rc, single = _lint(gov, capsys, "--task-id", f"{prefix}-1")
    assert rc == 1 and single["frozen"] == 1 and single["issues"] and all(i["frozen"] for i in single["issues"])
    rc, out, _err = _cli(["state", "lint", "--workspace-root", str(gov), "--task-id", f"{prefix}-1"], capsys)
    assert f"{prefix}-1 (frozen)" in out


def test_item3_next_scan_skips_frozen_and_single_derive_stops(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    from tools.aipos_cli.next_resolver import derive_next_step, scan_project

    before = {r["task_id"] for r in scan_project(gov)}
    assert {"DEMO-1", "DEMO-3", "DEMO-5", "DEMO-4", "DEMO-9"} <= before
    _freeze(gov, capsys, "--confirm")
    after = {r["task_id"] for r in scan_project(gov)}
    assert after == {"DEMO-4", "DEMO-9"}, after
    rc, out, _err = _cli(["next", "--workspace-root", str(gov)], capsys)
    # AIPOS-F141: 汇总行经四视图唯一可见卡入口 = 本视图(活跃队列 pending/claimed/blocked)内未列的冻结卡数(DEMO-1/3/5)
    assert rc == 0 and "冻结 3 张未列" in out and "DEMO-1" not in out
    stop = derive_next_step("DEMO-1", gov)
    assert stop["derivable"] is False and stop["current_state"] == "legacy_frozen"
    assert "--unfreeze DEMO-1" in stop["suggested_action"]


def test_item3_gate_writes_and_loop_refuse_frozen_with_unfreeze_command(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    _freeze(gov, capsys, "--confirm")
    from tools.aipos_cli.board_adapter import audit_dispatch_task, audit_verdict_task, claim_task, return_task
    from tools.aipos_cli.finalize import finalize_task
    from tools.aipos_cli.loop_driver import run_loop

    unfreeze = f"--workspace-root {gov} --unfreeze DEMO-1"
    claim = claim_task(task_id="DEMO-1", actor="exec.sample", dry_run=True, repo_root=gov)
    assert claim["verdict"] == "BLOCK" and any(r.startswith("LEGACY_FROZEN") and unfreeze in r for r in claim["blocking_reasons"])
    responses = {
        "return": return_task(task_id="DEMO-1", actor="exec.sample", agent_instance="exec.sample", owner_policy_ref="pol",
                              result_summary="x", dry_run=True, repo_root=gov),
        "dispatch": audit_dispatch_task(source_task_id="DEMO-1", actor="advisor.sample", agent_instance="advisor.sample",
                                        owner_policy_ref="pol", audit_task_id="DEMO-1R", audit_agent_instance="audit.sample",
                                        dry_run=True, repo_root=gov),
        "verdict": audit_verdict_task(audit_task_id="DEMO-1R", reviewed_task_id="DEMO-1", actor="audit.sample",
                                      agent_instance="audit.sample", owner_policy_ref="pol", verdict="PASS", dry_run=True, repo_root=gov),
    }
    for verb, resp in responses.items():
        assert resp["verdict"] == "BLOCK" and resp["error_code"] == "LEGACY_FROZEN", (verb, resp)
        assert unfreeze in resp["unfreeze_command"], verb
    fin = finalize_task("DEMO-1", "exec.sample", tmp_path / "product", governance_root=gov, dry_run=True)
    assert fin["verdict"] == "BLOCK" and fin["category"] == "LEGACY_FROZEN" and unfreeze in fin["message"]
    out = io.StringIO()
    loop = run_loop("DEMO-1", gov, actor="advisor.sample", out=out)
    assert loop.outcome == "not_derivable" and "LEGACY_FROZEN" in loop.message and unfreeze in loop.suggested_action
    assert "LEGACY_FROZEN" in out.getvalue()
    # 非冻结卡不受影响(拒因里无 LEGACY_FROZEN)
    ok = return_task(task_id="DEMO-4", actor="exec.sample", agent_instance="exec.sample", owner_policy_ref="pol",
                     result_summary="x", dry_run=True, repo_root=gov)
    assert ok.get("error_code") != "LEGACY_FROZEN" and not any("LEGACY_FROZEN" in r for r in ok.get("blocking_reasons", []))


def test_item3_unfreeze_appends_entry_and_restores(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    _freeze(gov, capsys, "--confirm")
    manifest = gov / "5_tasks/records/legacy_baseline"
    first = {p.name: p.read_bytes() for p in manifest.glob("*.md")}
    rc, out, _err = _cli(["project", "freeze-legacy", "--workspace-root", str(gov), "--unfreeze", "DEMO-1",
                          "--reason", "重新推进", "--actor", "advisor.sample", "--confirm"], capsys)
    assert rc == 0, out
    files = sorted(manifest.glob("*.md"))
    assert len(files) == 2 and all((manifest / n).read_bytes() == b for n, b in first.items())  # 只追加, 既有条目不改
    from tools.aipos_cli.legacy_baseline import frozen_tasks
    from tools.aipos_cli.next_resolver import derive_next_step, scan_project

    assert "DEMO-1" not in frozen_tasks(gov) and len(frozen_tasks(gov)) == 4
    rc, lint = _lint(gov, capsys)
    assert [i["task_id"] for i in lint["issues"] if i["severity"] == "ERROR"] == ["DEMO-1"] and lint["frozen"] == 4
    assert "DEMO-1" in {r["task_id"] for r in scan_project(gov)}
    assert derive_next_step("DEMO-1", gov)["current_state"] != "legacy_frozen"
    rc, out, _err = _cli(["project", "freeze-legacy", "--workspace-root", str(gov), "--unfreeze", "DEMO-1",
                          "--reason", "again", "--actor", "advisor.sample", "--confirm"], capsys)
    assert rc == 0 and "未冻结(幂等, 不重复写): DEMO-1" in out and len(list(manifest.glob("*.md"))) == 2


def test_item3_broken_manifest_is_fail_closed_everywhere(tmp_path, monkeypatch, capsys):
    gov = _make_gov(tmp_path, monkeypatch)
    _freeze(gov, capsys, "--confirm")
    (gov / "5_tasks/records/legacy_baseline/legacy_freeze_broken.md").write_text("no frontmatter\n", encoding="utf-8")
    from tools.aipos_cli.board_adapter import return_task
    from tools.aipos_cli.next_resolver import scan_project

    rc, lint = _lint(gov, capsys)
    assert rc == 1 and any(i.get("code") == "LEGACY_BASELINE_INVALID" for i in lint["issues"])
    assert len([i for i in lint["issues"] if i["severity"] == "ERROR"]) == 5  # 冻结不生效, 4 张人肉期断层不被隐藏
    rows = scan_project(gov)
    assert rows[0]["current_state"] == "legacy_baseline_invalid" and "DEMO-1" in {r["task_id"] for r in rows}
    resp = return_task(task_id="DEMO-4", actor="exec.sample", agent_instance="exec.sample", owner_policy_ref="pol",
                       result_summary="x", dry_run=True, repo_root=gov)
    assert resp["verdict"] == "BLOCK" and resp["error_code"] == "LEGACY_BASELINE_INVALID"
    rc, _out, err = _freeze(gov, capsys, "--dry-run")
    assert rc == 1 and "LEGACY_BASELINE_INVALID" in err


def test_item1_manifest_dir_outside_governance_root_rejected(tmp_path, monkeypatch):
    gov = _make_gov(tmp_path, monkeypatch)
    project = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    project["legacy_baseline"] = {"manifest_dir": str(tmp_path / "elsewhere")}
    (gov / "project.json").write_text(json.dumps(project), encoding="utf-8")
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_tasks

    with pytest.raises(LegacyBaselineError) as exc:
        frozen_tasks(gov)
    assert exc.value.code == "LEGACY_BASELINE_INVALID"
