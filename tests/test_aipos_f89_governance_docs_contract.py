"""AIPOS-F89 件② — 治理文档名不再是产品契约(M17)。靶场复用 F44A/F80/F86 既有构件(禁第二份靶场构造逻辑)。

- config.schema governance_docs.files(固定 7 份 lybra 治理文档名)与 governance_structure.paths.foundation_backlog 删除;
  项目需要被产品读取的治理文档改为 project.json paths 可选声明: foundation_backlog(卡编年史) / hard_rules_source(硬规矩来源)。
- board_adapter close 的编年史检查读 paths.foundation_backlog: 未声明 = 跳过 + warning(不 BLOCK, 不替项目建文件);
  声明了 = 原语义(缺条目自动生成, excuse_ref 豁免)。
- hard_rules_extractor 来源读 paths.hard_rules_source: 未声明 = 跳过 + warning(章程以母本自带条文为准); 章程母本「单一真相源」
  行改占位 {{hard_rules_source}}(charter_render 与提取器同读 hard_rules_source_ref)。
- 新项目(lybra project new 骨架 + init 模板)不含也不要求任何 lybra 治理文档; 渲染给新项目的执行/审计章程零 lybra 文档名。

验收②: 无 lybra 治理文档的新项目靶场 close 不 BLOCK、硬规矩来源缺省行为明确。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write  # noqa: E402  — 靶场唯一来源
from test_aipos_f80_zero_gate_charter import _identity, _make_gov as _charter_gov  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for  # noqa: E402
from tools.aipos_cli.workspace_config import project_paths, scaffold_project  # noqa: E402
from tools.schema_loader import load_schema  # noqa: E402

LYBRA_DOC_NAMES = re.compile(r"COMMANDS\.md|DISCIPLINE\.md|ROLES\.md|DESIGN\.md|PRINCIPLES\.md|LEDGER\.md|FOUNDATION-BACKLOG|CONVERGENCE")


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _new_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "probe") -> Path:
    """新项目 = 产品骨架(workspace_config.scaffold_project, 即 lybra project new), 不拷任何 lybra 治理文档。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = scaffold_project(tmp_path / "home", name)
    for sub in ("returns", "closures"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True, exist_ok=True)
    return root


def _claimed_card_with_return(root: Path, task_id: str) -> None:
    """close 前置(与 F44A close 夹具同形): claimed 卡 + return 记录。"""
    _write(queue_root_for(root) / "claimed" / f"{task_id.lower()}.md", _fm({
        "task_id": task_id, "title": f"{task_id} probe close", "status": "claimed", "project": "probe",
        "assigned_to": "exec.probe.test", "agent_instance": "exec.probe.test", "context_bundle": "t", "task_mode": "code",
        "task_class": "simple", "priority": "normal", "created_by": "advisor.probe.test", "needs_owner": False,
        "output_target": "tests/", "artifact_policy": "formal_write", "claim_id": f"claim_{task_id}_t",
        "claimed_by": "exec.probe.test", "claimed_at": "2026-10-03T00:00:00Z", "active_session_id": f"session_{task_id}_t",
    }, f"# {task_id}\n"))
    _write(root / "5_tasks" / "records" / "returns" / task_id / f"return_{task_id.lower()}_20261003_000000_exec.md",
           _fm({"record_type": "task_return", "task_id": task_id, "actor": "exec.probe.test"}, "# Return\n"))


# ===========================================================================
# 声明: 产品 schema 不含任何治理文档名契约
# ===========================================================================

def test_item2_schema_carries_no_governance_doc_name_contract():
    raw = (REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8")
    hits = [ln.strip()[:120] for ln in raw.splitlines() if LYBRA_DOC_NAMES.search(ln)]
    _show(f"[②·schema] config.schema lybra 治理文档名命中: {hits or 0}")
    assert hits == []
    config = load_schema("config")
    gs = config["governance_structure"]["paths"]
    assert "files" not in gs["governance_docs"] and "foundation_backlog" not in gs
    pj = config["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]
    for key in ("foundation_backlog", "hard_rules_source"):
        assert pj[key]["optional"] is True and "default" not in pj[key], key


# ===========================================================================
# 验收②: 新项目 close 不 BLOCK; 编年史 / 硬规矩来源缺省行为明确
# ===========================================================================

def test_item2_new_project_without_lybra_docs_close_not_blocked_and_creates_nothing(tmp_path, monkeypatch):
    from tools.aipos_cli.board_adapter import close_task

    root = _new_project(tmp_path, monkeypatch)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert not any(LYBRA_DOC_NAMES.search(p) for p in before), before
    assert project_paths(root)["foundation_backlog"] is None and project_paths(root)["hard_rules_source"] is None
    task_id = "PROBE-1"
    _claimed_card_with_return(root, task_id)
    result = close_task(task_id=task_id, actor="exec.probe.test", closure_evidence={"finalize_commit_hash": "abc123"},
                        dry_run=False, repo_root=root)
    warnings = (result.get("data") or {}).get("governance_warnings") or []
    _show(f"[②·新项目 close] ok={result.get('ok')} verdict={result.get('verdict')} governance_warnings={warnings}")
    assert result.get("ok") is True and result.get("verdict") != "BLOCK", result
    assert any(w.startswith("FOUNDATION_BACKLOG_UNDECLARED") for w in warnings), warnings
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert not any(LYBRA_DOC_NAMES.search(p) or "BACKLOG" in p.upper() for p in after), after
    assert (queue_root_for(root) / "completed" / f"{task_id.lower()}.md").is_file()


def test_item2_declared_chronicle_keeps_auto_generation_semantics(tmp_path, monkeypatch):
    """声明了 paths.foundation_backlog(任意文件名) = 原语义: 缺条目自动生成进该文件, 无 UNDECLARED warning。"""
    from tools.aipos_cli.board_adapter import close_task

    root = _new_project(tmp_path, monkeypatch)
    decl = json.loads((root / "project.json").read_text(encoding="utf-8"))
    decl["paths"] = {"foundation_backlog": "docs/chronicle.md"}
    (root / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    task_id = "PROBE-2"
    _claimed_card_with_return(root, task_id)
    result = close_task(task_id=task_id, actor="exec.probe.test", closure_evidence={"finalize_commit_hash": "abc123"},
                        dry_run=False, repo_root=root)
    warnings = (result.get("data") or {}).get("governance_warnings") or []
    chronicle = root / "docs" / "chronicle.md"
    _show(f"[②·声明编年史] ok={result.get('ok')} warnings={warnings} chronicle_exists={chronicle.is_file()}")
    assert result.get("ok") is True and not any("UNDECLARED" in w for w in warnings)
    assert chronicle.is_file() and task_id in chronicle.read_text(encoding="utf-8")
    assert not (root / "governance" / "FOUNDATION-BACKLOG.md").exists()


def test_item2_hard_rules_source_undeclared_skips_with_warning_declared_reads_it(tmp_path, monkeypatch, capsys):
    from tools.aipos_cli.hard_rules_extractor import (
        UNDECLARED_REF,
        extract_hard_rules_from_handbook,
        hard_rules_source_ref,
        render_hard_rules_for_charter,
    )

    root = _new_project(tmp_path, monkeypatch)
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(root))
    result = extract_hard_rules_from_handbook(root)
    err = capsys.readouterr().err
    _show(f"[②·硬规矩未声明] ok={result['ok']} skipped={result.get('skipped')} error={result['error']} stderr={err.strip()}")
    assert result["ok"] is False and result["skipped"] is True and result["error"].startswith("HARD_RULES_SOURCE_UNDECLARED")
    assert "HARD_RULES_SOURCE_UNDECLARED" in err
    assert hard_rules_source_ref(root) == UNDECLARED_REF
    assert "HARD_RULES_SOURCE_UNDECLARED" in render_hard_rules_for_charter()

    _write(root / "manuals" / "rules.md", "# m\n\n## 0.5. 硬规矩\n\n1. **规矩一** — 说明。\n2. **规矩二** — 说明。\n\n## 1. 其他\n")
    decl = json.loads((root / "project.json").read_text(encoding="utf-8"))
    decl["paths"] = {"hard_rules_source": "manuals/rules.md"}
    (root / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    result = extract_hard_rules_from_handbook(root)
    assert result["ok"] and len(result["rules_list"]) == 2
    rendered = render_hard_rules_for_charter()
    _show("[②·硬规矩已声明] " + rendered.strip().splitlines()[2])
    assert "> **单一真相源**: manuals/rules.md § 0.5。修改该来源 → 章程与派审注入同步跟随。" in rendered


def test_item2_new_project_charters_templates_skills_zero_lybra_doc_names(tmp_path, monkeypatch):
    """新项目(未声明任何治理文档)渲染执行/审计章程零 lybra 治理文档名; init 模板与顾问/导航技能不再把 lybra 文档名当约定。"""
    from tools.aipos_cli.charter_render import charter_render_context, render_charter

    gov = _charter_gov(tmp_path, monkeypatch, shape="lybra")
    decl = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    decl["project"] = "probe"
    (gov / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    for role, prefix in (("executor", "exec"), ("auditor", "audit")):
        harness = tmp_path / "pi" / f"probe-{role}"
        harness.mkdir(parents=True)
        ctx = charter_render_context(gov, identity=_identity(harness, role, f"{prefix}.probe.hostl", "probe"))
        rendered = render_charter((REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8"), ctx)
        hits = [ln for ln in rendered.splitlines() if LYBRA_DOC_NAMES.search(ln)]
        _show(f"[②·probe {role} 章程] lybra 文档名命中: {hits or 0}")
        assert hits == []
    template_hits = [str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "templates").rglob("*")
                     if p.is_file() and LYBRA_DOC_NAMES.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert template_hits == [], template_hits
    for skill in ("advisor-commands", "truth-navigator"):
        text = (REPO_ROOT / "agents" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "FOUNDATION-BACKLOG" not in text and "files.design" not in text, skill


def test_item2_fixture_registered_in_runall():
    from tools.aipos_cli.board_adapter import RUNALL_RELATIVE_PATH

    text = (REPO_ROOT / RUNALL_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "tests/test_aipos_f89_governance_docs_contract.py" in text
