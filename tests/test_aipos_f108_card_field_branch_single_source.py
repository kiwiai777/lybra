"""AIPOS-F108 卡字段与分支声明单源(族 C-d: M9 / M18 / H6)回归夹具。

件① card.schema 补全: 落盘键(frontmatter_order 42 键 + 复核点名的 12 键)全部在 fields 声明; 字段序由 schema 投影
       (record_writer.CARD_FRONTMATTER_ORDER); 代码缺省改读 fields.<键>.default(record_writer.card_field_defaults)。
件② 分支名与基线分支读声明(transitions N5.branch_integration.branch_pattern / base_branch): 产品代码零 f"card/{…}" /
       "card/{task_id}" 回落 / "main" 基线写死; 改声明 → 建树 / 交回判据 / 改动集跟随(靶场)。
件③ 草稿 project 缺省读治理根 project.json#project, 缺则拒; CLI 与看板去掉 "ai-project-os" 缺省。
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CARD_SCHEMA = REPO_ROOT / "schema" / "card.schema.json"
TRANSITIONS_SCHEMA = REPO_ROOT / "schema" / "transitions.schema.json"

# 复核 10-04 点名、card.schema 原未声明的键(9 个落盘序键 + 3 个另写键)
F108_DECLARED_KEYS = {
    "recurrence", "blocked_by", "blocked_at", "block_reason", "reopened_by", "reopen_reason",
    "withdrawn_by", "withdrawn_at", "withdrawal_reason",
    "finalize_task_id", "conclusion_note", "draft_validation_summary",
}
# 转移引擎写入、card.schema 仍未声明的键 = 登记缺口(F108 RETURN 缺口清单; 只减不增, 补声明后从这里删)
TRANSITION_UNDECLARED_GAP = {"round"}


def _card_schema() -> dict:
    return json.loads(CARD_SCHEMA.read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.strip()


# ===========================================================================
# 件① card.schema 声明键 ⊇ 落盘键; 字段序 / 缺省值由 schema 投影
# ===========================================================================

def test_item1_frontmatter_order_projected_from_schema_and_declared():
    from tools.aipos_cli import draft_writer, queue_mutation, record_writer

    schema = _card_schema()
    order = schema["frontmatter_order"]["keys"]
    assert record_writer.CARD_FRONTMATTER_ORDER == order
    assert draft_writer.FRONTMATTER_ORDER is record_writer.CARD_FRONTMATTER_ORDER
    assert queue_mutation.FRONTMATTER_ORDER is record_writer.CARD_FRONTMATTER_ORDER
    assert len(order) == len(set(order)) == 42
    missing = [k for k in order if k not in schema["fields"]]
    assert missing == [], missing


def test_item1_named_keys_declared_with_type_writer_and_finalize_card_field():
    schema = _card_schema()
    fields = schema["fields"]
    for key in sorted(F108_DECLARED_KEYS):
        spec = fields.get(key)
        assert isinstance(spec, dict), key
        assert spec.get("type") and spec.get("required") is False and spec.get("written_by"), (key, spec)
    # transitions 声明的卡字段 finalize_task_id 指向 card.schema 已声明的键
    transitions = json.loads(TRANSITIONS_SCHEMA.read_text(encoding="utf-8"))
    card_field = transitions["artifact_ingest"]["finalization"]["finalize_task_id"]["card_field"]
    assert card_field in fields


def test_item1_transition_writers_only_write_declared_keys():
    """靶场: 转移引擎 block / withdraw / reopen(带理由)实写的键 ⊆ card.schema 声明(登记缺口除外)。"""
    from tools.aipos_cli.transition_engine import apply_transition_metadata

    fields = set(_card_schema()["fields"])
    base = {"task_id": "F108-T", "status": "claimed", "active_session_id": "s1", "claim_id": "c1"}
    written: set[str] = set()
    for name in ("block", "withdraw", "reopen"):
        out = apply_transition_metadata(metadata=dict(base), transition_name=name, actor="a", timestamp="2026-10-04T00:00:00Z",
                                        reason="r")
        written |= {k for k in out if out.get(k) != base.get(k)}
    assert {"blocked_by", "blocked_at", "block_reason", "withdrawn_by", "withdrawn_at", "withdrawal_reason",
            "reopened_by", "reopened_at", "reopen_reason"} <= written
    undeclared = written - fields
    assert undeclared == TRANSITION_UNDECLARED_GAP, undeclared


def test_item1_defaults_read_declaration_not_code():
    from tools.aipos_cli import draft_writer
    from tools.aipos_cli.record_writer import card_field_defaults
    from tools.schema_loader import validate_field_value

    defaults = card_field_defaults()
    fields = _card_schema()["fields"]
    assert defaults == {k: v["default"] for k, v in fields.items() if "default" in v}
    for key in ("status", "needs_owner", "task_type", "polling_mode", "claim_policy", "report_mode", "recurrence",
                "task_class", "output_target", "artifact_policy"):
        assert key in defaults, key
        if defaults[key] != "":
            ok, err = validate_field_value(key, defaults[key])
            assert ok, err
    assert not hasattr(draft_writer, "DEFAULT_TEMPLATE_VALUES")  # 写死缺省表已退役
    # 草稿模板缺省 = 声明投影
    meta, _body = draft_writer.build_template_payload("basic", {"task_id": "F108-D"})
    for key, value in defaults.items():
        assert meta[key] == value, key


def test_item1_audit_derivation_has_no_hardcoded_defaults():
    """audit_derivation 原写死缺省(assigned_to=executor_lybra / agent_instance=executor.lybra.kiwiai-dev / _inherit_defaults 三键)退役。"""
    src = (REPO_ROOT / "tools" / "aipos_cli" / "audit_derivation.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "executor.lybra.kiwiai-dev" not in consts and "executor_lybra" not in consts
    assert src.count("_inherit_defaults = card_field_defaults()") == 2


def _gov_with_card(tmp_path: Path, fm: dict) -> Path:
    from tools.aipos_cli.queue_mutation import render_task_markdown

    gov = tmp_path / "gov"
    for state in ("pending", "claimed", "completed", "blocked"):
        (gov / "5_tasks" / "queue" / state).mkdir(parents=True)
    (gov / "5_tasks" / "queue" / "completed" / f"{fm['task_id'].lower()}.md").write_text(
        render_task_markdown(fm, "# src\n"), encoding="utf-8")
    return gov


def _source_fm(**extra) -> dict:
    fm = {"task_id": "F108-SRC", "title": "src", "project": "probe-x", "context_bundle": "b", "task_mode": "code",
          "priority": "high", "status": "completed", "created_by": "adv", "needs_owner": False, "output_target": "tools/",
          "artifact_policy": "formal_write"}
    fm.update(extra)
    return fm


def test_item1_repair_card_inherits_identity_without_hardcoded_fallback(tmp_path):
    """靶场: 修复卡承继原卡 assigned_to / agent_instance; 原卡无 agent_instance = 不写; 原卡缺必填 assigned_to = 产前自检拒。"""
    from tools.aipos_cli.audit_derivation import derive_repair_card_on_fail
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    def derive(gov: Path) -> dict:
        res = derive_repair_card_on_fail(governance_root=gov, reviewed_task_id="F108-SRC", audit_task_id="F108-SRCR",
                                         verdict_id="verdict_x", fail_reason="r", actor="gate")
        assert res["derived"], res
        meta, _body, _warn = parse_markdown_frontmatter((gov / res["repair_task_path"]).read_text(encoding="utf-8"))
        return meta

    meta = derive(_gov_with_card(tmp_path / "a", _source_fm(assigned_to="hbj-coder", agent_instance="hbj-coder.probe-x.m1")))
    assert meta.get("assigned_to") == "hbj-coder" and meta.get("agent_instance") == "hbj-coder.probe-x.m1", meta

    meta2 = derive(_gov_with_card(tmp_path / "b", _source_fm(assigned_to="hbj-coder")))
    assert meta2.get("assigned_to") == "hbj-coder" and "agent_instance" not in meta2, meta2

    with pytest.raises(ValueError, match="assigned_to"):
        derive(_gov_with_card(tmp_path / "c", _source_fm()))


# ===========================================================================
# 件② 分支名 / 基线分支读声明
# ===========================================================================

def _scan_branch_hardcodes() -> list[str]:
    """产品代码(tools/aipos_cli 非测试 + web 下 .py)中分支名 / 基线写死: f-string 以 card/ 起头接占位、
    字面量 "card/{task_id}"(回落缺省)、含 main.. / main... 的 diff 区间、基线变量赋 "main"、git 命令参数 "main"。docstring 不计。"""
    files = [p for p in (REPO_ROOT / "tools" / "aipos_cli").glob("*.py") if not p.name.startswith("test_")]
    files += [p for p in (REPO_ROOT / "web").rglob("*.py") if "tests" not in p.parts and not p.name.startswith("test_")]
    hits: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                parts = node.values
                for i, part in enumerate(parts):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        text = part.value
                        nxt = parts[i + 1] if i + 1 < len(parts) else None
                        if text.endswith("card/") and isinstance(nxt, ast.FormattedValue):
                            hits.append(f"{rel}:{node.lineno} f-string card/{{…}}")
                        if "main.." in text:
                            hits.append(f"{rel}:{node.lineno} f-string main..")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if node.value == "card/{task_id}":
                    hits.append(f"{rel}:{node.lineno} literal card/{{task_id}}")
                if node.value.startswith("main..") or "main...card/" in node.value:
                    hits.append(f"{rel}:{node.lineno} literal main..")
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value == "main":
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any("base" in n or "branch" in n for n in names) and rel.name != "governance_guardrails.py":
                    hits.append(f"{rel}:{node.lineno} {names} = 'main'")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args + node.args.kwonlyargs
                defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults) + list(node.args.kw_defaults)
                for arg, default in zip(args, defaults):
                    if isinstance(default, ast.Constant) and default.value == "main" and "branch" in arg.arg:
                        hits.append(f"{rel}:{node.lineno} 参数 {arg.arg}='main'")
            elif isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "git":
                if any(isinstance(e, ast.Constant) and e.value == "main" for e in node.elts[1:]):
                    hits.append(f"{rel}:{node.lineno} git 命令参数 'main'")
    return hits


def test_item2_no_branch_or_base_hardcodes_in_product_code():
    hits = _scan_branch_hardcodes()
    assert hits == [], hits


def test_item2_declaration_has_branch_pattern_and_base_branch():
    from tools.aipos_cli.next_resolver import card_base_branch, card_branch_name

    bi = json.loads(TRANSITIONS_SCHEMA.read_text(encoding="utf-8"))["nodes"]["N5"]["branch_integration"]
    assert "{task_id}" in bi["branch_pattern"] and bi["base_branch"]
    assert card_branch_name("X-1") == bi["branch_pattern"].replace("{task_id}", "X-1")
    assert card_base_branch() == bi["base_branch"]


def test_item2_missing_declaration_fails_closed(monkeypatch):
    from tools import schema_loader
    from tools.aipos_cli.next_resolver import card_base_branch, card_branch_name
    from tools.schema_loader import SchemaLoadError

    monkeypatch.setattr(schema_loader, "get_branch_integration", lambda repo_root=None: {"branch_pattern": "card/{task_id}"})
    with pytest.raises(SchemaLoadError, match="base_branch"):
        card_base_branch()
    monkeypatch.setattr(schema_loader, "get_branch_integration", lambda repo_root=None: {"base_branch": "main"})
    with pytest.raises(SchemaLoadError, match="branch_pattern"):
        card_branch_name("X-1")


@pytest.fixture
def trunk_repo(tmp_path, monkeypatch):
    """靶场: 声明改为 lane/{task_id} + 基线 trunk; 产品仓只有 trunk(无 main)。"""
    from tools import schema_loader

    real = schema_loader.get_branch_integration()
    custom = {**real, "branch_pattern": "lane/{task_id}", "base_branch": "trunk"}
    monkeypatch.setattr(schema_loader, "get_branch_integration", lambda repo_root=None: custom)
    repo = tmp_path / "prod"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def test_item2_branch_checks_follow_declaration(trunk_repo):
    from tools.aipos_cli import board_adapter, next_resolver

    repo = trunk_repo
    assert next_resolver._check_branch_has_commits(repo, "F108-B") is False  # 声明分支不存在
    _git(repo, "checkout", "-qb", "lane/F108-B")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "work")
    _git(repo, "checkout", "-q", "trunk")
    assert next_resolver._check_branch_has_commits(repo, "F108-B") is True  # 相对声明基线 trunk 有提交
    assert board_adapter._card_branch_changed_files(repo, "F108-B") == ["tests/test_x.py"]
    assert board_adapter._git_show_on_branch(repo, "F108-B", "tests/test_x.py") == "x\n"
    # 交回判据⑤ 分支合规: 分支名 / 基座 = 声明值(仓里没有 main 也不误拒)
    from unittest import mock

    with mock.patch.object(board_adapter, "_card_product_repo", lambda root, fm: repo):
        reasons = board_adapter._check_branch_compliance(task_id="F108-B", task_metadata={}, repo_root=repo)
    assert reasons == [], reasons
    with mock.patch.object(board_adapter, "_card_product_repo", lambda root, fm: repo):
        reasons = board_adapter._check_branch_compliance(task_id="F108-NONE", task_metadata={}, repo_root=repo)
    assert reasons and "lane/F108-NONE" in reasons[0], reasons


def test_item2_worktree_created_from_declared_base(trunk_repo, tmp_path):
    from tools.aipos_cli import next_resolver

    repo = trunk_repo
    gov = tmp_path / "gov2"
    (gov / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
    (gov / "project.json").write_text(json.dumps({"project": "probe-x", "code_repo": str(repo)}), encoding="utf-8")
    fm = {"task_id": "F108-W", "task_mode": "code", "project": "probe-x"}
    res = next_resolver._ensure_worktree(gov, "F108-W", fm)
    assert res["ok"], res
    assert res["branch"] == "lane/F108-W"
    assert _git(repo, "rev-parse", "lane/F108-W") == _git(repo, "rev-parse", "trunk")
    _git(repo, "worktree", "remove", "--force", res["worktree_path"])


# ===========================================================================
# 件③ 草稿 project 缺省读治理根 project.json#project, 缺则拒
# ===========================================================================

def _draft_gov(tmp_path: Path, project: str | None) -> Path:
    gov = tmp_path / "draftgov"
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    if project is not None:
        (gov / "project.json").write_text(json.dumps({"project": project}), encoding="utf-8")
    return gov


_TEMPLATE_VALUES = {"task_id": "F108-P", "title": "t", "project": None, "assigned_to": "exec", "context_bundle": "b",
                    "task_mode": "code", "priority": "high", "created_by": "adv", "output_target": "tools/",
                    "artifact_policy": "formal_write"}


def _fm_project(markdown: str) -> str | None:
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    meta, _body, _warn = parse_markdown_frontmatter(markdown)
    return meta.get("project")


def test_item3_cli_template_draft_project_reads_declaration(tmp_path):
    from tools.aipos_cli.draft_writer import build_template_payload, create_draft

    gov = _draft_gov(tmp_path, "probe-x")
    meta, body = build_template_payload("basic", dict(_TEMPLATE_VALUES))
    res = create_draft(gov, meta, body, dry_run=True)
    assert res["verdict"] != "BLOCK", res["blocking_reasons"]
    assert _fm_project(res["rendered_markdown"]) == "probe-x"
    # 显式给 project = 原样保留
    meta, body = build_template_payload("basic", {**_TEMPLATE_VALUES, "project": "other"})
    assert _fm_project(create_draft(gov, meta, body, dry_run=True)["rendered_markdown"]) == "other"


def test_item3_undeclared_project_is_refused(tmp_path):
    from tools.aipos_cli.draft_writer import build_template_payload, create_draft

    for project_json in (None, ""):
        gov = _draft_gov(tmp_path / f"p{project_json is None}", project_json)
        meta, body = build_template_payload("basic", dict(_TEMPLATE_VALUES))
        res = create_draft(gov, meta, body, dry_run=False)
        assert res["verdict"] == "BLOCK" and res.get("wrote") is False, res
        assert any(r.startswith("DRAFT_PROJECT_UNDECLARED") for r in res["blocking_reasons"]), res["blocking_reasons"]
        assert not (gov / "5_tasks" / "drafts").exists()


def test_item3_board_draft_without_project_reads_declaration(tmp_path):
    from tools.aipos_cli import board_adapter

    gov = _draft_gov(tmp_path, "probe-x")
    fm = {k: v for k, v in _TEMPLATE_VALUES.items() if k != "project"}
    fm.update({"status": "pending", "needs_owner": False})
    res = board_adapter.create_draft({"frontmatter": fm, "body": "# x\n"}, dry_run=True, repo_root=gov, actor="adv")
    assert res["verdict"] != "BLOCK", res
    assert _fm_project(res["data"]["rendered_markdown"]) == "probe-x"


def test_item3_cli_and_board_carry_no_project_default():
    from tools.aipos_cli.aipos_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["draft", "create", "--from-template", "basic", "--task-id", "X"])
    assert args.project is None
    app_js = (REPO_ROOT / "web" / "board" / "static" / "app.js").read_text(encoding="utf-8")
    assert '"ai-project-os"' not in app_js
    for rel in ("tools/aipos_cli/draft_writer.py", "tools/aipos_cli/aipos_cli.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert not any(isinstance(n, ast.Constant) and n.value == "ai-project-os" for n in ast.walk(tree)), rel
