"""AIPOS-F79D: 治理仓提交门·护栏缺口四件(2026-09-22 chris/lybra 实撞四案)

件① 四检单源: tools/aipos_cli/governance_guardrails.py 一份规则, hook bash 壳只取 staged 清单并调它;
    governance-commit 的 dry-run 与正式提交前对同一「将提交清单」跑同一模块 → dry-run 拒 = 正式拒, 文案逐字相同。
件② hook 工作区根按 staged 文件各自归属判(禁 os.walk 首命中): 共享仓两工作区(lybra 形 + chris 形)交叉不误判, ws_prefix 按文件列。
件③ hook 拒后清暂存: commit 子进程非零退出也走 _unstage_this_run(预暂存快照精确还原), 结果带 index_restored=true 与被拒清单。
件④ session 记录只落声明位: CLI 认领(with_records)与 MCP 两跳同走 record_writer.session_record_path;
    0 字节记录拒写; lint RECORD_EMPTY 带出口; repair 按声明位重铸(sessions/ 空文件 + claims/ 下同名正常份)。

靶场全部临时 git 仓(禁碰真实治理根); 母本 hook 装进临时仓 .git/hooks 走真 pre-commit。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.aipos_cli import governance_commit as gc
from tools.aipos_cli.governance_guardrails import (
    GuardrailDeclarationError,
    check_entries,
    format_report,
    load_guardrail_declarations,
    manifest_entries,
    read_staged_entries_from_bytes,
    workspace_prefix_for,
)
from tools.aipos_cli.state_lint import RECORD_EMPTY, repair_task_state, run_state_lint
from tools.schema_constants import Verdict

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SRC = REPO_ROOT / "tools" / "hooks" / "governance-pre-commit"
ACTOR = "advisor.test.f79d"
LYBRA_REL = "2_projects/lybra"
CHRIS_REL = "2_projects/chris"
FM = "---\nstatus: active\n---\n"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd), check=check, capture_output=True, text=True,
    )


def cached(repo: Path) -> str:
    return _git(repo, "diff", "--cached", "--name-only", "--no-renames").stdout


def head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _make_workspace(ws: Path) -> None:
    for d in ("governance/decision_log/2026-09", "5_tasks/queue/pending", "5_tasks/records", "task_cards", "stage_archive", "notes"):
        (ws / d).mkdir(parents=True)
    (ws / "governance" / "README.md").write_text(FM + "# governance\n", encoding="utf-8")
    (ws / "governance" / "decision_log" / "2026-09" / "2026-09-01-d.md").write_text(FM + "# decision\n", encoding="utf-8")
    for d in ("5_tasks/queue/pending", "5_tasks/records", "task_cards", "stage_archive"):
        (ws / d / ".gitkeep").write_text("", encoding="utf-8")
    (ws / "notes" / "a.md").write_text("# note a\n", encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """共享治理仓: 2_projects/lybra(lybra 形) + 2_projects/chris(chris 形), 母本 hook 装入 .git/hooks/pre-commit。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Rig User")
    _git(repo, "config", "user.email", "rig@example.com")
    lybra = repo / LYBRA_REL
    chris = repo / CHRIS_REL
    _make_workspace(lybra)
    _make_workspace(chris)
    _git(repo, "add", "--", "2_projects")
    _git(repo, "commit", "-q", "-m", "rig: shared governance repo with two workspaces")

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    shutil.copy(HOOK_SRC, hooks_dir / "pre-commit")
    os.chmod(hooks_dir / "pre-commit", 0o755)
    monkeypatch.setenv("LYBRA_SCHEMA_DIR", str(REPO_ROOT / "schema"))

    def mock_resolve_governance_path(key, governance_root, repo_root=None):
        path_map = {
            "task_cards": governance_root / "task_cards",
            "decision_log_dir": governance_root / "governance" / "decision_log",
            "stage_archive": governance_root / "stage_archive",
        }
        return path_map.get(key, governance_root / key)

    monkeypatch.setattr("tools.schema_loader.resolve_governance_path", mock_resolve_governance_path)
    return {"repo": repo, "lybra": lybra, "chris": chris}


def run(rig, ws: Path, **kw):
    kw.setdefault("task_id", None)
    kw.setdefault("actor", ACTOR)
    kw.setdefault("repo_root", REPO_ROOT)
    kw.setdefault("push", False)
    return gc.governance_commit(ws, **kw)


def violations_block(text: str) -> str:
    """拒因段(Violations: 起到 Guardrails 段前)——hook 输出与 CLI 消息逐字比对的对象。"""
    start = text.index("Violations:")
    end = text.index("Guardrails (", start)
    return text[start:end].strip()


# ---------------------------------------------------------------------------
# 件①: dry-run 拒因 == 正式拒因(逐字), .py 案与 record_type 缺失案
# ---------------------------------------------------------------------------
def test_piece1_py_file_dry_run_and_formal_reject_with_identical_reason(rig):
    repo, lybra = rig["repo"], rig["lybra"]
    (lybra / "notes" / "tool.py").write_text("print('x')\n", encoding="utf-8")
    (lybra / "notes" / "a.md").write_text("# note a\nchanged\n", encoding="utf-8")
    head_before = head(repo)

    dry = run(rig, lybra, dry_run=True, paths=["notes"])
    real = run(rig, lybra, dry_run=False, paths=["notes"])

    assert dry["verdict"] == Verdict.BLOCK and real["verdict"] == Verdict.BLOCK
    assert dry["dry_run"] is True and real["dry_run"] is False
    assert dry["message"] == real["message"], "dry-run 与正式提交拒因必须逐字相同"
    assert f"  - {LYBRA_REL}/notes/tool.py: Code file forbidden in governance repo (B①)" in dry["message"]
    assert dry["rejected_files"] == real["rejected_files"] == [f"{LYBRA_REL}/notes/tool.py"]
    assert dry["guardrail_report"]["ws_prefixes"][f"{LYBRA_REL}/notes/tool.py"] == f"{LYBRA_REL}/"
    assert head(repo) == head_before and real["committed"] is False
    assert cached(repo) == "", "四检拒在暂存之前, 暂存区为空"


def test_piece1_record_missing_record_type_dry_run_and_formal_identical(rig):
    repo, lybra = rig["repo"], rig["lybra"]
    rec_dir = lybra / "5_tasks" / "records" / "claims" / "T-1"
    rec_dir.mkdir(parents=True)
    (rec_dir / "claim_T-1_20260922_120000_x.md").write_text("---\nclaim_id: claim_T-1\n---\n# no record_type\n", encoding="utf-8")

    dry = run(rig, lybra, dry_run=True, paths=["5_tasks/records/claims/T-1"])
    real = run(rig, lybra, dry_run=False, paths=["5_tasks/records/claims/T-1"])

    assert dry["verdict"] == real["verdict"] == Verdict.BLOCK
    assert dry["message"] == real["message"]
    assert (
        f"  - {LYBRA_REL}/5_tasks/records/claims/T-1/claim_T-1_20260922_120000_x.md: "
        "New record lacks required field 'record_type' (B④, declared in config.schema file_declarations.record_file)"
    ) in dry["message"]
    assert real["committed"] is False and cached(repo) == ""

    # 补 record_type 后同一命令通过(并经真 hook)
    (rec_dir / "claim_T-1_20260922_120000_x.md").write_text("---\nrecord_type: claim\nclaim_id: claim_T-1\n---\n# ok\n", encoding="utf-8")
    ok = run(rig, lybra, dry_run=False, paths=["5_tasks/records/claims/T-1"])
    assert ok["verdict"] == Verdict.PASS and ok["committed"] is True, ok["message"]
    assert "Guardrails (governance_guardrails, same module as pre-commit hook): PASS" in " ".join(ok["operations"])


def test_piece1_hook_is_a_shell_over_the_same_module(rig):
    """hook 母本不再自带规则: 无 CODE_EXTENSIONS/under_prefix/os.walk, 只取 staged 清单并调模块。"""
    text = "\n".join(line for line in HOOK_SRC.read_text(encoding="utf-8").split("\n") if not line.lstrip().startswith("#"))
    assert "python3 -m tools.aipos_cli.governance_guardrails" in text
    assert "--staged-stdin" in text
    for forbidden in ("CODE_EXTENSIONS=(", "under_prefix()", "os.walk", "blocked_reasons+=", "diff --cached --name-only --diff-filter"):
        assert forbidden not in text, f"hook 壳不得自带规则: {forbidden}"
    assert "diff --cached --name-status --no-renames -z" in text


# ---------------------------------------------------------------------------
# 件① + 件③: 真 hook 拒(绕过前置四检模拟旧 CLI) → hook 拒因段与 dry-run 逐字相同; 拒后 index 精确还原
# ---------------------------------------------------------------------------
def test_piece3_hook_reject_restores_index_and_hook_reason_equals_dry_run(rig, monkeypatch):
    repo, lybra = rig["repo"], rig["lybra"]
    # --paths 内预暂存: 暂存 X, 工作树随后改 Y(还原须还原成 X)
    pre = lybra / "notes" / "pre.md"
    pre.write_text("X\n", encoding="utf-8")
    _git(repo, "add", "--", f"{LYBRA_REL}/notes/pre.md")
    pre.write_text("Y\n", encoding="utf-8")
    (lybra / "notes" / "tool.py").write_text("print('x')\n", encoding="utf-8")
    cached_before = cached(repo)
    assert cached_before == f"{LYBRA_REL}/notes/pre.md\n"
    head_before = head(repo)

    dry = run(rig, lybra, dry_run=True, paths=["notes"])
    assert dry["verdict"] == Verdict.BLOCK

    # 模拟「前置四检缺席」(旧 CLI / 规则漂移), 让提交真正撞 hook
    monkeypatch.setattr(gc, "_run_guardrails_on_manifest", lambda *a, **k: None)
    real = run(rig, lybra, dry_run=False, paths=["notes"])

    assert real["verdict"] == Verdict.BLOCK and real["committed"] is False and head(repo) == head_before
    assert real["index_restored"] is True
    assert real["rejected_files"] == sorted({f"{LYBRA_REL}/notes/pre.md", f"{LYBRA_REL}/notes/tool.py"})
    assert "Commit rejected by git/pre-commit hook (exit 1)" in real["operations"]
    assert "Restored 1 pre-staged" in " ".join(real["operations"])
    # 拒因段逐字相同(同一 format_report)
    assert violations_block(real["hook_output"]) == violations_block(dry["message"])
    assert f"ws_prefix: {LYBRA_REL}/notes/tool.py → {LYBRA_REL}/" in real["hook_output"]
    # index 精确还原: 只剩预暂存条目, 且内容为 X(非工作树的 Y)
    assert cached(repo) == cached_before
    assert _git(repo, "show", f":{LYBRA_REL}/notes/pre.md").stdout == "X\n"
    assert pre.read_text(encoding="utf-8") == "Y\n"


# ---------------------------------------------------------------------------
# 件②: 双工作区靶场 B③ 交叉判定 + ws_prefix 按文件
# ---------------------------------------------------------------------------
def test_piece2_decision_log_append_only_judged_per_workspace(rig):
    repo, lybra, chris = rig["repo"], rig["lybra"], rig["chris"]
    chris_dl = chris / "governance" / "decision_log" / "2026-09" / "2026-09-01-d.md"
    lybra_dl = lybra / "governance" / "decision_log" / "2026-09" / "2026-09-01-d.md"
    chris_dl.write_text(FM + "# decision (modified in chris)\n", encoding="utf-8")
    lybra_dl.write_text(FM + "# decision (modified in lybra)\n", encoding="utf-8")

    chris_res = run(rig, chris, dry_run=True, paths=["governance/decision_log"])
    lybra_res = run(rig, lybra, dry_run=True, paths=["governance/decision_log"])

    chris_file = f"{CHRIS_REL}/governance/decision_log/2026-09/2026-09-01-d.md"
    lybra_file = f"{LYBRA_REL}/governance/decision_log/2026-09/2026-09-01-d.md"
    assert chris_res["verdict"] == Verdict.BLOCK
    assert f"  - {chris_file}: decision_log/ is append-only, modifications forbidden (B③)" in chris_res["message"]
    assert f"  ws_prefix: {chris_file} → {CHRIS_REL}/" in chris_res["message"]
    assert chris_res["guardrail_report"]["ws_prefixes"] == {chris_file: f"{CHRIS_REL}/"}
    assert lybra_res["verdict"] == Verdict.BLOCK
    assert f"  - {lybra_file}: decision_log/ is append-only, modifications forbidden (B③)" in lybra_res["message"]
    assert f"  ws_prefix: {lybra_file} → {LYBRA_REL}/" in lybra_res["message"]

    # 真 hook 同判: 直接在临时仓对 chris 形工作区 git commit → 拒, 输出带按文件 ws_prefix(旧 hook 在此处 os.walk 命中 lybra → B③ 漏判)
    _git(repo, "add", "--", chris_file)
    proc = _git(repo, "commit", "-q", "-m", "chris: modify decision", check=False)
    hook_out = proc.stdout + proc.stderr  # git 把 hook 的 stdout 接到 stderr
    assert proc.returncode == 1, hook_out
    assert f"  - {chris_file}: decision_log/ is append-only, modifications forbidden (B③)" in hook_out
    assert f"  ws_prefix: {chris_file} → {CHRIS_REL}/" in hook_out
    assert violations_block(hook_out) == violations_block(chris_res["message"])
    _git(repo, "reset", "-q", "--", chris_file)


def test_piece2_cross_workspace_commit_uses_each_files_own_root(rig):
    """同一提交跨两工作区: chris 的 governance 文档缺 frontmatter 按 chris 根判 B②, lybra 的合规文档不误判; 找不到根的文件 warning。"""
    repo, lybra, chris = rig["repo"], rig["lybra"], rig["chris"]
    (chris / "governance" / "NOFM.md").write_text("# no frontmatter\n", encoding="utf-8")
    (lybra / "governance" / "README.md").write_text(FM + "# governance (edited)\n", encoding="utf-8")
    (repo / "loose.md").write_text("# loose file at repo root\n", encoding="utf-8")
    _git(repo, "add", "--", f"{CHRIS_REL}/governance/NOFM.md", f"{LYBRA_REL}/governance/README.md", "loose.md")

    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    entries = read_staged_entries_from_bytes(
        subprocess.run(["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-status", "--no-renames", "-z"],
                       cwd=str(repo), check=True, capture_output=True).stdout
    )
    report = check_entries(repo, entries, decls, current_branch="main")
    assert report.ws_prefixes == {
        f"{CHRIS_REL}/governance/NOFM.md": f"{CHRIS_REL}/",
        f"{LYBRA_REL}/governance/README.md": f"{LYBRA_REL}/",
        "loose.md": "",
    }
    assert [v["file"] for v in report.violations] == [f"{CHRIS_REL}/governance/NOFM.md"]
    assert report.violations[0]["reason"] == "Missing frontmatter (B②, required: status)"
    assert any("no governance workspace root found above loose.md" in w for w in report.warnings)
    assert workspace_prefix_for(repo, "loose.md", decls) == ("", False)
    assert workspace_prefix_for(repo, f"{CHRIS_REL}/governance/NOFM.md", decls) == (f"{CHRIS_REL}/", True)

    proc = _git(repo, "commit", "-q", "-m", "cross-workspace", check=False)
    assert proc.returncode == 1
    assert violations_block(proc.stdout + proc.stderr) == violations_block(format_report(report, decls))
    _git(repo, "reset", "-q")


def test_piece1_declaration_missing_is_fail_closed(tmp_path):
    with pytest.raises(GuardrailDeclarationError):
        load_guardrail_declarations(tmp_path)
    decls = load_guardrail_declarations(REPO_ROOT / "schema")
    assert "py" in decls.code_extensions and "sh" in decls.code_extensions
    assert decls.gov_doc_required_fm == ("status",) and decls.record_required_fm == ("record_type",)
    assert manifest_entries({"files": [{"repo_path": "a", "status": "untracked_selected"}]}) == [("A", "a")]
    with pytest.raises(ValueError):
        manifest_entries({"files": [{"repo_path": "a", "status": "weird"}]})


# ---------------------------------------------------------------------------
# 件④: session 记录只在声明位落一份非空(CLI 认领 with_records 与 MCP 两跳同一 session_record_path)
# ---------------------------------------------------------------------------
def _workspace(tmp_path: Path, task_id: str) -> Path:
    ws = tmp_path / "ws"
    for d in ("5_tasks/queue/pending", "5_tasks/queue/claimed", "5_tasks/records/claims", "5_tasks/records/sessions", "task_cards", "3_context_bundles"):
        (ws / d).mkdir(parents=True)
    (ws / "5_tasks" / "queue" / "pending" / f"{task_id}.md").write_text(
        f"""---
task_id: {task_id}
title: F79D session record position
project: test
status: pending
task_mode: code
priority: normal
assigned_to: test-agent
agent_instance: test-agent
context_bundle: test-bundle
created_by: test-creator
needs_owner: false
output_target: tests/
artifact_policy: formal_write
task_class: simple
---
# card
""",
        encoding="utf-8",
    )
    (ws / "project.json").write_text(json.dumps({"project": "test", "governance_root": str(ws)}), encoding="utf-8")
    (ws / "3_context_bundles" / "agent_profiles.json").write_text(json.dumps({"profiles": []}), encoding="utf-8")
    return ws


def _session_files(ws: Path, task_id: str) -> tuple[list[Path], list[Path]]:
    sessions = sorted((ws / "5_tasks" / "records" / "sessions" / task_id).glob("*.md")) if (ws / "5_tasks" / "records" / "sessions" / task_id).is_dir() else []
    claims_dir = ws / "5_tasks" / "records" / "claims" / task_id
    misplaced = sorted(p for p in claims_dir.glob("session_*.md")) if claims_dir.is_dir() else []
    return sessions, misplaced


def test_piece4_cli_claim_with_records_writes_session_only_at_declared_position(tmp_path):
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    task_id = "TEST-F79D-001"
    ws = _workspace(tmp_path, task_id)
    result = mutate_queue_task(repo_root=ws, action="claim", task_id=task_id, actor="test-agent", dry_run=False, with_records=True, profiles=[])
    assert result.get("wrote") is True, result

    sessions, misplaced = _session_files(ws, task_id)
    assert len(sessions) == 1 and sessions[0].stat().st_size > 0, "sessions/ 一份非空"
    assert misplaced == [], "claims/ 下不得有 session 副本(分叉点根治)"
    assert sessions[0].name == result["proposed_session_id"] + ".md"
    assert "record_type: session_record" in sessions[0].read_text(encoding="utf-8")
    claim_files = sorted((ws / "5_tasks" / "records" / "claims" / task_id).glob("*.md"))
    assert [p.name for p in claim_files] == [result["proposed_claim_id"] + ".md"]


def test_piece4_mcp_two_hop_claim_records_land_at_same_declared_position(tmp_path):
    from tools.aipos_cli.board_adapter import _mcp_claim_record_plan, _write_mcp_claim_records
    from tools.aipos_cli.record_writer import session_record_path

    task_id = "TEST-F79D-002"
    ws = _workspace(tmp_path, task_id)
    updated = {
        "claim_id": f"claim_{task_id}_20260922_120000_test-agent",
        "active_session_id": f"session_{task_id}_20260922_120000_test-agent",
        "claimed_at": "2026-09-22T12:00:00Z",
    }
    plan = _mcp_claim_record_plan(
        repo_root=ws, task_id=task_id, task_path=f"5_tasks/queue/claimed/{task_id}.md", actor="test-agent",
        canonical_agent_instance="test-agent", owner_policy_ref="pol_test", updated_metadata=updated,
    )
    _write_mcp_claim_records(ws, plan, task_id=None)
    sessions, misplaced = _session_files(ws, task_id)
    assert len(sessions) == 1 and sessions[0].stat().st_size > 0 and misplaced == []
    assert sessions[0] == session_record_path(ws, task_id, updated["active_session_id"])


def test_piece4_zero_byte_record_writes_are_refused(tmp_path):
    from tools.aipos_cli.board_adapter import _write_mcp_claim_records
    from tools.aipos_cli.record_writer import write_records_atomic

    ws = tmp_path / "ws"
    (ws / "5_tasks" / "records").mkdir(parents=True)
    with pytest.raises(ValueError, match="0 字节"):
        write_records_atomic(ws, [("session", "session_T-9_20260922_120000_x", "   \n")])
    assert not (ws / "5_tasks" / "records" / "sessions").exists()
    with pytest.raises(RuntimeError, match="0 字节"):
        _write_mcp_claim_records(ws, {"record_previews": [{"path": "5_tasks/records/sessions/T-9/s.md", "record_type": "session_record", "rendered_markdown": ""}]})
    assert not (ws / "5_tasks" / "records" / "sessions" / "T-9" / "s.md").exists()
    # 路径包含校验不再被吞: record_id 越界即拒
    with pytest.raises(ValueError):
        write_records_atomic(ws, [("claim", "claim_T-9_../../x", "# x\n")])


def test_piece4_lint_reports_record_empty_and_repair_recasts_to_declared_position(tmp_path):
    """构造 F78CR 形: sessions/<ID>/ 0 字节同名文件 + claims/<ID>/session_*.md 正常份 → lint RECORD_EMPTY → repair 重铸。"""
    task_id = "TEST-F79D-003"
    ws = _workspace(tmp_path, task_id)
    session_id = f"session_{task_id}_20260922_090043_audit-test"
    claim_id = f"claim_{task_id}_20260922_090043_audit-test"
    # 卡在 claimed/ 且有 claim_log(存量真实形)
    card = ws / "5_tasks" / "queue" / "pending" / f"{task_id}.md"
    card_text = card.read_text(encoding="utf-8").replace("status: pending", f"status: claimed\nclaim_id: {claim_id}\nactive_session_id: {session_id}\nclaimed_at: '2026-09-22T09:00:43Z'")
    (ws / "5_tasks" / "queue" / "claimed" / f"{task_id}.md").write_text(card_text, encoding="utf-8")
    card.unlink()
    claims_dir = ws / "5_tasks" / "records" / "claims" / task_id
    sessions_dir = ws / "5_tasks" / "records" / "sessions" / task_id
    claims_dir.mkdir(parents=True)
    sessions_dir.mkdir(parents=True)
    (claims_dir / f"{claim_id}.md").write_text(
        f"---\nrecord_type: claim_log\nclaim_id: {claim_id}\ntask_id: {task_id}\nactor: audit-test\nclaim_action: claimed\ncreated_at: '2026-09-22T09:00:43Z'\nsession_id: {session_id}\n---\n# Claim Log\n",
        encoding="utf-8",
    )
    session_content = (
        f"---\nrecord_type: session_record\nsession_id: {session_id}\ntask_id: {task_id}\nactor: audit-test\n"
        f"created_at: '2026-09-22T09:00:43Z'\nstatus: active\nclaim_id: {claim_id}\ncurrent_state: claimed\nevent_count: 1\n---\n"
        f"# Session Record: {session_id}\n\n## Events\n\n- 2026-09-22T09:00:43Z claimed by audit-test\n"
    )
    (claims_dir / f"{session_id}.md").write_text(session_content, encoding="utf-8")
    (sessions_dir / f"{session_id}.md").write_text("", encoding="utf-8")

    lint = run_state_lint(ws)
    empties = [i for i in lint["issues"] if i.get("code") == RECORD_EMPTY]
    assert len(empties) == 1, lint
    assert empties[0]["task_id"] == task_id and empties[0]["severity"] == "ERROR"
    assert empties[0]["path"] == f"5_tasks/records/sessions/{task_id}/{session_id}.md"
    assert f"lybra state repair --task-id {task_id}" in empties[0]["message"]
    # 按 task_id 过滤同样点名
    assert any(i.get("code") == RECORD_EMPTY for i in run_state_lint(ws, task_id_filter=task_id)["issues"])

    preview = repair_task_state(ws, task_id, dry_run=True)
    assert preview["repaired"] is False and preview["dry_run"] is True
    assert any("重铸 session 记录到声明位" in a and "(删 0 字节空文件)" in a for a in preview["actions"]), preview
    assert (sessions_dir / f"{session_id}.md").stat().st_size == 0 and (claims_dir / f"{session_id}.md").exists(), "dry-run 不动盘"

    done = repair_task_state(ws, task_id, dry_run=False)
    assert done["repaired"] is True, done
    assert done["record_repair"]["moved"] == [{
        "from": f"5_tasks/records/claims/{task_id}/{session_id}.md",
        "to": f"5_tasks/records/sessions/{task_id}/{session_id}.md",
        "removed_empty": "true",
    }]
    assert (sessions_dir / f"{session_id}.md").read_text(encoding="utf-8") == session_content
    assert not (claims_dir / f"{session_id}.md").exists()
    assert sorted(p.name for p in claims_dir.glob("*.md")) == [f"{claim_id}.md"]
    repair_record = ws / done["record_repair"]["repair_record"]
    assert repair_record.is_file() and repair_record.parent == ws / "5_tasks" / "records" / "events" / task_id
    repair_text = repair_record.read_text(encoding="utf-8")
    assert "record_type: task_progress_event" in repair_text and "event_type: record_repair" in repair_text
    assert f"claims/{task_id}/{session_id}.md" in repair_text

    after = run_state_lint(ws)
    assert not [i for i in after["issues"] if i.get("code") == RECORD_EMPTY], after
    # 幂等: 再 repair 无动作
    again = repair_task_state(ws, task_id, dry_run=False)
    assert again["record_repair"]["moved"] == [] and again["record_repair"]["repaired"] is False


def test_piece4_repair_leaves_empty_file_without_source_and_never_overwrites_nonempty(tmp_path):
    task_id = "TEST-F79D-004"
    ws = _workspace(tmp_path, task_id)
    sessions_dir = ws / "5_tasks" / "records" / "sessions" / task_id
    claims_dir = ws / "5_tasks" / "records" / "claims" / task_id
    sessions_dir.mkdir(parents=True)
    claims_dir.mkdir(parents=True)
    (sessions_dir / "session_orphan.md").write_text("", encoding="utf-8")
    (sessions_dir / "session_full.md").write_text("---\nrecord_type: session_record\n---\n# declared\n", encoding="utf-8")
    (claims_dir / "session_full.md").write_text("---\nrecord_type: session_record\n---\n# misplaced copy\n", encoding="utf-8")

    result = repair_task_state(ws, task_id)
    rec = result["record_repair"]
    assert rec["moved"] == [] and rec["repaired"] is False
    assert any("session_orphan.md 为 0 字节" in u for u in rec["unresolved"])
    assert any("session_full.md 已有非空内容" in u for u in rec["unresolved"])
    assert (sessions_dir / "session_full.md").read_text(encoding="utf-8").endswith("# declared\n")
    assert (claims_dir / "session_full.md").exists()
    assert (sessions_dir / "session_orphan.md").stat().st_size == 0


# ---------------------------------------------------------------------------
# 件④ 单一实现断言: 三处 session 落点全经 record_writer.session_record_path / claim_record_paths
# ---------------------------------------------------------------------------
def test_piece4_single_session_position_implementation():
    rw = (REPO_ROOT / "tools" / "aipos_cli" / "record_writer.py").read_text(encoding="utf-8")
    assert "except ValueError as e:" not in rw.split("def write_records_atomic")[1].split("def build_return_skeleton_markdown")[0]
    assert "path = session_record_path(repo_root, task_id, record_id)" in rw
    assert "_get_record_type_for_validation" not in rw
    ba = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    assert "claim_path, session_path = claim_record_paths(repo_root, task_id, claim_id, session_id)" in ba
    assert "if session_markdown else []" in ba, "裁决路径: session 记录缺席不再写空 preview"


# ---------------------------------------------------------------------------
# 件⑤: 本夹具入 run-all
# ---------------------------------------------------------------------------
def test_fixture_registered_in_run_all():
    run_all = (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f79d_commit_gate_guardrails.py" in run_all
