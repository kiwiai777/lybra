"""AIPOS-F78 — 引擎无关: 意图渲染 + 产物入口 + harness/lane 声明 + 落点全读项目声明(0 迁移接入)。

靶场分根: tmp 治理根(队列/记录/落点) + tmp 产品 git 仓; schema 只从产品根(本仓)读。
两种项目形: lybra 形(project.json 无 paths → 缺省 task_cards/<ID>/RETURN.md) 与 chris 形
(paths.return_root=5_tasks/records/returns, verdict_root=5_tasks/records/audit_verdicts, 目录一字不改)。

覆盖(每条对应卡面前置零①–⑨ / 件①–④ / 验收①–⑤; 在 main 上逐条先红——见 RETURN.md 原文):
前置零① 账务动词一律驱动方 token(roles.schema driver), actor=卡实例; claim 经信封一阶段放行; 门侧信封身份=驱动方
前置零② loop/next 的驱动方身份读工位声明/驱动方 token 实例, 禁占位 advisor
前置零③ close 三字段自填(finalization 记录 merge_commit), 缺一不派生
前置零④ 交回判据读卡分支(git show / 三点 diff), 不读共用检出
前置零⑤ lane.paths 作车道目录; advisor 对 claimed 卡受限 amend(声明字段含 output_target/lane)
前置零⑥ 承接世系扫描含 withdrawn; dev_override 基线认 Owner 授权记录/世系
前置零⑦ finalize 对 JSON 声明文件键级三方合并; PASS 后 tip 变化放行复审
前置零⑧ publish 门动词校验只匹配注册 MCP 动词全名、只扫正文
前置零⑨ PASS 卡 mark-concluded 登记承接; close 写 conclusion_note
件①  card.schema harness/lane 声明 + create/publish/regen 派生
件②  单一渲染器三输出, grep lybra_ = 0
件③  artifact ingest 两种项目形; 缺 commit_sha 拒; tip 不符拒
件④  transitions artifact_ingest / config project_json.paths 声明; next_resolver RETURN 判断读声明
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.next_resolver import derive_next_step  # noqa: E402

EXEC = "exec.lybra.test"
AUDITOR = "audit.lybra.test"
DRIVER = "advisor.lybra.test"
POLICY = "pol_lybra_loop_f78"
CHRIS_EXEC = "hbj-coder.chris-huibojin.test"
SHA_A = "a" * 40


# ---------------------------------------------------------------------------
# 靶场
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fm(meta: dict, body: str = "") -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, dict)):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _init_product_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _write(path / "README.md", "# product\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _make_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, shape: str, project: str = "lybra") -> Path:
    """shape=lybra: 无 paths 段(缺省); shape=chris: chris 现行 resource-locations 落点声明(目录一字不改)。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = tmp_path / f"gov-{shape}"
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "owner_decisions"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "5_tasks" / "policies").mkdir()
    (root / "task_cards").mkdir()
    code_repo = _init_product_repo(tmp_path / f"product-{shape}")
    decl = {"project": project, "code_repo": str(code_repo), "config_version": 1}
    if shape == "chris":
        decl["paths"] = {
            "return_root": "5_tasks/records/returns",
            "verdict_root": "5_tasks/records/audit_verdicts",
            "queue_root": "5_tasks/queue",
            "task_cards_root": "task_cards",
        }
    _write(root / "project.json", json.dumps(decl))
    _write(root / ".lybra" / "role", json.dumps({"role": "advisor", "instance": DRIVER}))
    return root


def _card(gov: Path, task_id: str, queue: str, *, assigned: str = EXEC, task_mode: str = "code",
          extra: dict | None = None, body: str = "") -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} test", "project": "lybra", "task_mode": task_mode,
            "assigned_to": assigned, "agent_instance": assigned, "status": queue, "audit": "required",
            "audit_by": AUDITOR, "output_target": "tools/aipos_cli/, tests/", "priority": "high",
            "created_by": DRIVER, "needs_owner": False, "context_bundle": "t", "artifact_policy": "formal_write"}
    meta.update(extra or {})
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, body or f"# {task_id}\n\n## Goal\n把 {task_id} 做完。\n\n## 验收\n- 夹具绿\n"))
    return path


def _claim_record(gov: Path, task_id: str, agent: str) -> None:
    _write(gov / "5_tasks" / "records" / "claims" / task_id / f"claim_{task_id}_x_{agent}.md",
           _fm({"record_type": "claim_record", "event_type": "claim", "claim_id": f"claim_{task_id}_x", "task_id": task_id,
                "agent_instance": agent, "actor": agent, "owner_policy_ref": POLICY, "claimed_at": "2026-09-20T00:00:00Z"}))


def _branch_with_commit(code_repo: Path, task_id: str, rel: str = "tools/aipos_cli/x.py") -> tuple[str, str]:
    _git(code_repo, "checkout", "-q", "-b", f"card/{task_id}")
    _write(code_repo / rel, f"# {task_id}\n")
    _git(code_repo, "add", rel)
    _git(code_repo, "commit", "-q", "-m", f"{task_id}: work")
    sha = _git(code_repo, "rev-parse", "HEAD")
    tree = _git(code_repo, "rev-parse", "HEAD^{tree}")
    _git(code_repo, "checkout", "-q", "main")
    return sha, tree


def _return_text(task_id: str, sha: str, tree: str, *, drop: set[str] | None = None) -> str:
    fm = {"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}", "model": "fixture-model"}
    for k in drop or ():
        fm.pop(k)
    return _fm(fm, f"# RETURN — {task_id}\n\n## 一句话结论\n完成。\n\n## 改动清单\n- x\n")


def _policy(gov: Path, *, agent_or_role: str = "advisor") -> None:
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    _write(gov / "5_tasks" / "policies" / f"{POLICY}.md", build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role=agent_or_role, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="test-envelope", task_selector_task_mode="code"))


def _connection(tmp_path: Path, roles: list[str], *, bound: dict | None = None) -> Path:
    conn = tmp_path / "connection.json"
    tokens = []
    for r in roles:
        tok = {"role": r, "role_class": r, "token": f"fixture-not-a-secret-{r}", "scopes": []}
        if bound and r in bound:
            tok["agent_instance"] = bound[r]
        tokens.append(tok)
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:1/mcp"}, "tokens": tokens}), encoding="utf-8")
    return conn


# ---------------------------------------------------------------------------
# 件④ 声明
# ---------------------------------------------------------------------------

def test_f78_item4_declarations_exist_in_schema_single_source():
    transitions = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    ingest = transitions["artifact_ingest"]
    assert ingest["return"]["required_frontmatter"] == ["commit_sha", "tree_hash", "branch", "model"]
    assert ingest["return"]["root_key"] == "return_root" and ingest["verdict"]["root_key"] == "verdict_root"
    assert "merge_commit" in transitions["nodes"]["N5"]["record"]
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    paths = config["configuration_sources"]["project_json"]["schema"]["paths"]["schema"]
    assert set(paths) == {"return_root", "verdict_root", "queue_root", "task_cards_root", "manual_gate_mode"}
    assert paths["return_root"]["default"] == "task_cards"  # lybra 默认 = 现行路径, 0 迁移
    card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
    assert card["fields"]["harness"]["$enum"] == "harness" and card["fields"]["lane"]["type"] == "object"
    assert card["intent_face"]["harness"]["allowed"] == ["pi", "codex", "claude-code"]
    assert set(card["restricted_amend"]["claimed_card_fields"]) == {"rework_rounds", "output_target", "lane"}
    roles = json.loads((REPO_ROOT / "schema" / "roles.schema.json").read_text(encoding="utf-8"))
    assert roles["driver"]["role_class"] == "advisor"
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]
    assert verbs["lybra_card_render"]["cli_command"] == "lybra card render"
    assert verbs["lybra_artifact_ingest"]["cli_command"] == "lybra artifact ingest"


def test_f78_item4_project_paths_reader_defaults_and_chris_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli.workspace_config import project_paths

    lybra = _make_gov(tmp_path, monkeypatch, shape="lybra")
    p = project_paths(lybra)
    assert p["return_root"] == lybra / "task_cards" and p["verdict_root"] == lybra / "task_cards"
    assert p["queue_root"] == lybra / "5_tasks" / "queue" and p["manual_gate_mode"] is False
    assert p["declared"]["return_root"] is False
    chris = _make_gov(tmp_path, monkeypatch, shape="chris")
    c = project_paths(chris)
    assert c["return_root"] == chris / "5_tasks" / "records" / "returns"
    assert c["verdict_root"] == chris / "5_tasks" / "records" / "audit_verdicts"
    assert c["declared"]["return_root"] is True


def test_f78_item4_next_resolver_return_judgement_reads_declaration_both_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """验收④: 「RETURN 已落盘」判断改读声明后, 对 lybra 形与 chris 形都成立。"""
    for shape, rel in (("lybra", "task_cards/{id}/RETURN.md"), ("chris", "5_tasks/records/returns/{id}/return-hbj-coder-20260921T000000Z.md")):
        gov = _make_gov(tmp_path, monkeypatch, shape=shape)
        task_id = f"F78-{shape.upper()}-1"
        _card(gov, task_id, "claimed")
        _claim_record(gov, task_id, EXEC)
        assert nr._check_return_artifact(gov, task_id) is False
        d0 = derive_next_step(task_id, gov)
        assert d0["derivable"] is False and "RETURN.md 工作产物" in d0["missing_records"]
        code_repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
        sha, tree = _branch_with_commit(code_repo, task_id)
        _write(gov / rel.format(id=task_id), _return_text(task_id, sha, tree))
        assert nr._check_return_artifact(gov, task_id) is True, shape
        found = nr.find_return_artifact(gov, task_id)
        assert found == gov / rel.format(id=task_id), (shape, found)
        d = derive_next_step(task_id, gov)
        assert d["derivable"] and d["verb"] == "lybra_queue_return_dry_run", (shape, d)
        assert f"--completion-report-ref {rel.format(id=task_id)}" in d["command"], d["command"]
        assert f"card/{task_id}@{sha}" in d["command"] and "--actual-model" in d["command"]
        root, patterns = nr.executor_artifact_watch(gov, task_id)
        assert root == gov and patterns[0] == str(Path(rel.format(id=task_id)).parent / "RETURN.md")
        assert all(not p.startswith("/") for p in patterns)


# ---------------------------------------------------------------------------
# 件③ artifact ingest
# ---------------------------------------------------------------------------

def _ingest_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, task_id: str):
    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    _card(gov, task_id, "claimed")
    _claim_record(gov, task_id, EXEC)
    code_repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    sha, tree = _branch_with_commit(code_repo, task_id)
    if shape == "chris":
        ret = gov / "5_tasks" / "records" / "returns" / task_id / "return-hbj-coder-20260921T010000Z.md"
    else:
        ret = gov / "task_cards" / task_id / "RETURN.md"
    return gov, code_repo, sha, tree, ret


@pytest.mark.parametrize("shape", ["lybra", "chris"])
def test_f78_item3_ingest_two_project_shapes_mints_return_via_existing_shell(tmp_path, monkeypatch, shape):
    """验收①: Return 落盘 → artifact ingest → 走既有 queue return 薄壳(靶场注入执行体记录命令), 记录落地。"""
    from tools.aipos_cli.artifact_ingest import ingest_task_artifact, validate_task_artifact

    task_id = f"F78-ING-{shape.upper()}"
    gov, code_repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, shape, task_id)
    _write(ret, _return_text(task_id, sha, tree))
    check = validate_task_artifact(task_id, gov)
    assert check["ok"] and check["category"] == "OK" and check["path"] == str(ret), check

    executed: list[str] = []

    def fake_execute(derivation, workspace_root, connection_json=None):
        executed.append(derivation["command"])
        # 门侧效果替身: 落 return 记录(与 transitions N2.record 同形)
        _write(gov / "5_tasks" / "records" / "returns" / task_id / f"return_{task_id}_x_{EXEC}.md",
               _fm({"record_type": "return", "return_id": f"return_{task_id}_x", "task_id": task_id, "agent_instance": EXEC,
                    "returned_at": "2026-09-21T01:00:00Z", "return_status": "returned"}))
        return {"ok": True, "action_type": "return", "message": "return 成功", "command": derivation["command"], "exit_code": 0, "output": "ok"}

    res = ingest_task_artifact(task_id, gov, execute=fake_execute)
    assert res["ok"] and res["exit_code"] == 0 and res["kind"] == "return", res
    assert len(executed) == 1 and executed[0].startswith("lybra queue return --task-id " + task_id), executed
    assert f"--actor {EXEC}" in executed[0] and "--confirm" in executed[0]
    assert f"card/{task_id}@{sha}" in executed[0]
    assert list((gov / "5_tasks" / "records" / "returns" / task_id).glob("return_*.md"))
    # 记录已落 → 推导核前进到派审
    assert derive_next_step(task_id, gov)["verb"] == "lybra_audit_dispatch_dry_run"


def test_f78_item3_ingest_rejects_missing_commit_sha_and_tip_mismatch(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import INGEST_EXIT_REJECTED, ingest_task_artifact, validate_task_artifact

    task_id = "F78-ING-REJ"
    gov, code_repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, "chris", task_id)
    # 缺 commit_sha → 拒, 出口点名
    _write(ret, _return_text(task_id, sha, tree, drop={"commit_sha"}))
    check = validate_task_artifact(task_id, gov)
    assert check["ok"] is False and check["category"] == "INGEST_FRONTMATTER_MISSING" and check["exit_code"] == INGEST_EXIT_REJECTED
    assert "commit_sha" in check["reasons"][0] and "required_frontmatter" in check["reasons"][0]
    d = derive_next_step(task_id, gov)
    assert d["derivable"] is False and d["action"]["type"] == "artifact_invalid" and "Return frontmatter 缺 commit_sha" in d["missing_records"]
    calls: list = []
    res = ingest_task_artifact(task_id, gov, execute=lambda *a: calls.append(a) or {"ok": True})
    assert res["ok"] is False and res["exit_code"] == INGEST_EXIT_REJECTED and not calls, res
    # tip 不符(Return 自述旧 sha) → 拒
    _write(ret, _return_text(task_id, SHA_A, tree))
    check2 = validate_task_artifact(task_id, gov)
    assert check2["ok"] is False and check2["category"] == "INGEST_TIP_MISMATCH", check2
    # next --run 同一入口: execute_derived_action 对 return 步经 ingest 校验拒, 不执行薄壳
    derivation = {"derivable": True, "task_id": task_id, "current_node": "claim",
                  "command": f"lybra queue return --task-id {task_id} --actor {EXEC} --confirm"}
    monkeypatch.setattr(nr, "_check_branch_has_commits", lambda *a, **k: True)
    out = nr.execute_derived_action(derivation, gov, None)
    assert out["ok"] is False and "INGEST_TIP_MISMATCH" in out["message"], out


def test_f78_item3_ingest_verdict_requires_reviewed_tip_match(tmp_path, monkeypatch):
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    task_id = "F78-ING-V"
    audit = f"{task_id}R"
    gov, code_repo, sha, tree, _ret = _ingest_setup(tmp_path, monkeypatch, "chris", task_id)
    _card(gov, audit, "claimed", assigned=AUDITOR, task_mode="audit", extra={"reviewed_task_id": task_id})
    report = gov / "5_tasks" / "records" / "audit_verdicts" / audit / "audit-hbj-auditor-20260921T020000Z.md"
    _write(report, _fm({"verdict": "PASS", "reviewed_task_id": task_id, "commit_sha": SHA_A}, "# audit\n\n## 一句话结论\nPASS\n"))
    bad = validate_task_artifact(audit, gov)
    assert bad["ok"] is False and bad["category"] == "INGEST_TIP_MISMATCH", bad
    _write(report, _fm({"verdict": "PASS", "reviewed_task_id": task_id, "commit_sha": sha}, "# audit\n\n## 一句话结论\nPASS\n"))
    good = validate_task_artifact(audit, gov)
    assert good["ok"] and good["kind"] == "verdict" and good["path"] == str(report), good
    assert nr._check_verdict_artifact(gov, audit) == report  # 推导核裁决产物判据读 verdict_root 声明


def test_f78_item3_cli_entry_parses_and_dry_run_reports(tmp_path, monkeypatch):
    from tools.aipos_cli.aipos_cli import build_parser, main

    task_id = "F78-ING-CLI"
    gov, code_repo, sha, tree, ret = _ingest_setup(tmp_path, monkeypatch, "lybra", task_id)
    _write(ret, _return_text(task_id, sha, tree))
    args = build_parser().parse_args(["artifact", "ingest", "--task-id", task_id, "--dry-run", "--json"])
    assert args.artifact_command == "ingest"
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    rc = main(["artifact", "ingest", "--task-id", task_id, "--workspace-root", str(gov), "--dry-run", "--json"])
    monkeypatch.setattr(sys, "stdout", sys.__stdout__)
    data = json.loads(buf.getvalue())
    assert rc == 0 and data["category"] == "DRY_RUN" and "lybra queue return" in data["command"], data
    assert "token" not in buf.getvalue().lower()


# ---------------------------------------------------------------------------
# 件② 渲染
# ---------------------------------------------------------------------------

def test_f78_item2_single_renderer_three_outputs_zero_gate_verbs(tmp_path, monkeypatch):
    from tools.aipos_cli.card_render import render_card

    gov = _make_gov(tmp_path, monkeypatch, shape="chris")
    task_id = "F78-RND1"
    body = ("# card\n\n## Goal\n把渲染做对。\n\n## 验收\n- 三输出\n\n## 硬约束\n- 禁 lybra_queue_claim_dry_run 之类门动词出现在渲染物\n\n## 里程碑\n1. 写\n2. 测\n")
    _card(gov, task_id, "claimed", extra={"governance_refs": ["依据: pol_lybra_dev_9 信封; 见 lybra_governance_commit 声明"]}, body=body)
    outputs = {}
    for harness in ("pi", "codex", "claude-code"):
        r = render_card(task_id, gov, harness=harness)
        outputs[harness] = r["files"]
        for name, content in r["files"].items():
            assert "lybra_" not in content, (harness, name, content)
            assert "fixture-not-a-secret" not in content and "token" not in content.lower()
    pi = outputs["pi"]["stdout"]
    assert len(pi.strip().splitlines()) == 3 and pi.startswith("工作树: ") and "报告落点: " in pi and "卡路径: " in pi
    assert "5_tasks/records/returns/F78-RND1" in pi  # 落点读 chris 形声明
    assert "commit_sha, tree_hash, branch, model" in pi
    assert set(outputs["codex"]) == {"Prompt.md", "Plan.md"} and "## 车道(lane)" in outputs["codex"]["Prompt.md"]
    assert "tools/aipos_cli/" in outputs["codex"]["Prompt.md"] and "card/F78-RND1" in outputs["codex"]["Plan.md"]
    assert set(outputs["claude-code"]) == {"CLAUDE.md"} and "## 里程碑" in outputs["claude-code"]["CLAUDE.md"]
    # 同一源: 三输出的目标/车道文本一致
    assert "把渲染做对。" in outputs["codex"]["Prompt.md"] and "把渲染做对。" in outputs["claude-code"]["CLAUDE.md"]
    # 缺省 harness 由 task_mode 派生(card.schema intent_face.harness.default_by_task_mode)
    assert render_card(task_id, gov)["harness"] == "pi"
    with pytest.raises(ValueError):
        render_card(task_id, gov, harness="vim")


def test_f78_item2_cli_render_writes_to_out_dir(tmp_path, monkeypatch):
    from tools.aipos_cli.aipos_cli import main

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _card(gov, "F78-RCLI", "pending")
    out_dir = tmp_path / "wt"
    out_dir.mkdir()
    rc = main(["card", "render", "--task-id", "F78-RCLI", "--workspace-root", str(gov), "--harness", "codex", "--out-dir", str(out_dir)])
    assert rc == 0 and (out_dir / "Prompt.md").is_file() and (out_dir / "Plan.md").is_file()
    assert "lybra_" not in (out_dir / "Prompt.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 件① harness/lane 声明: create/publish/regen
# ---------------------------------------------------------------------------

def test_f78_item1_intent_derivation_and_lane_required(tmp_path, monkeypatch):
    from tools.aipos_cli.machine_zone import derive_intent_declarations, parse_output_target_paths

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    meta = {"task_id": "X", "task_mode": "code", "assigned_to": EXEC, "output_target": "tools/aipos_cli/(核), tests/"}
    intent = derive_intent_declarations(meta, gov)
    assert intent["blocking_reasons"] == [] and intent["harness"] == "pi"
    assert intent["lane"]["paths"] == ["tools/aipos_cli/", "tests/"] and intent["lane"]["roles"] == ["executor"]
    assert intent["lane"]["repo"] == json.loads((gov / "project.json").read_text())["code_repo"]
    bad = derive_intent_declarations({"task_id": "Y", "task_mode": "code", "assigned_to": EXEC, "output_target": "改产品"}, gov)
    assert any(r.startswith("LANE_REQUIRED") for r in bad["blocking_reasons"])
    assert parse_output_target_paths("schema/card.schema.json, tools/mcp_server/tools.py 与 agents/skills/") == [
        "schema/card.schema.json", "tools/mcp_server/tools.py", "agents/skills/"]
    kept = derive_intent_declarations({**meta, "harness": "codex", "lane": {"repo": "/r", "paths": ["a/"], "roles": ["executor"]}}, gov)
    assert kept["harness"] == "codex" and kept["lane"]["paths"] == ["a/"] and kept["derived"] == []


def test_f78_item1_regen_backfills_harness_and_lane_on_pending_cards(tmp_path, monkeypatch):
    """验收③: 存量 pending 卡 regen 后含 harness/lane(复用 F76 regen)。"""
    from tools.aipos_cli.draft_writer import regen_machine_zone_for_pending
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    path = _card(gov, "F78-REGEN", "pending")
    before, _b, _w = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    assert "harness" not in before and "lane" not in before
    res = regen_machine_zone_for_pending(gov, REPO_ROOT, task_id="F78-REGEN", actor=DRIVER, dry_run=False)
    assert res["verdict"] == "APPROVE" and "F78-REGEN" in res["data"]["updated_cards"], res
    after, _b2, _w2 = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    assert after["harness"] == "pi" and after["lane"]["paths"] == ["tools/aipos_cli/", "tests/"], after
    assert after.get("status") == "pending" and "draft_status" not in after or after.get("draft_status") != "draft"


def test_f78_item1_create_and_publish_carry_intent_fields(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft, publish_draft
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    meta = {"task_id": "F78-PUB", "title": "t", "project": "lybra", "assigned_to": EXEC, "context_bundle": "t",
            "task_mode": "code", "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False,
            "output_target": "tools/aipos_cli/", "artifact_policy": "formal_write"}
    created = create_draft(gov, meta, "## Goal\n\nx\n", dry_run=False)
    assert created["verdict"] in ("PASS", "WARN"), created
    draft_fm, _b, _w = parse_markdown_frontmatter((gov / created["target_path"]).read_text(encoding="utf-8"))
    assert draft_fm["harness"] == "pi" and draft_fm["lane"]["paths"] == ["tools/aipos_cli/"]
    published = publish_draft(gov, created["target_path"], dry_run=False)
    assert published["verdict"] != "BLOCK", published["blocking_reasons"]
    pub_fm, _pb, _pw = parse_markdown_frontmatter((gov / published["target_path"]).read_text(encoding="utf-8"))
    assert pub_fm["harness"] == "pi" and pub_fm["lane"]["paths"] == ["tools/aipos_cli/"]


# ---------------------------------------------------------------------------
# 前置零① 驱动方 token
# ---------------------------------------------------------------------------

def test_f78_pre0_1_ledger_verbs_use_driver_token(tmp_path):
    from tools.aipos_cli.two_phase_shell_factory import driver_role_class, resolve_driver_role_from_connection

    assert driver_role_class() == "advisor"
    conn = _connection(tmp_path, ["executor", "auditor", "advisor"])
    assert resolve_driver_role_from_connection(connection_json_path=str(conn)) == "advisor"
    with pytest.raises(ValueError) as exc:
        (tmp_path / "b").mkdir()
        resolve_driver_role_from_connection(connection_json_path=str(_connection(tmp_path / "b", ["executor", "auditor"])))
    assert "驱动方" in str(exc.value) and "advisor" in str(exc.value)


def test_f78_pre0_1_claim_derives_preauthorized_with_driver_envelope(tmp_path, monkeypatch):
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _card(gov, "F78-CLAIM", "pending")
    # 无信封 → 不裸撞 Supervised(驱动方无 owner_confirm), exit 5 带原因
    no_env = nr._execute_claim_with_role_token(task_id="F78-CLAIM", workspace_root=gov, connection_json=None)
    assert no_env["ok"] is False and no_env["exit_code"] == 5 and "信封" in no_env["message"], no_env
    # 信封覆盖驱动方角色 advisor → PreAuthorized + policy id, actor/agent_instance=卡实例
    _policy(gov, agent_or_role="advisor")
    seen: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kw):
        seen.append(list(argv))
        return _Proc()

    monkeypatch.setattr(nr.subprocess, "run", fake_run) if hasattr(nr, "subprocess") else None
    import subprocess as _sp

    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(nr, "_ensure_worktree", lambda ws, tid: {"ok": True, "worktree_path": "/wt", "message": "ok"})
    res = nr._execute_claim_with_role_token(task_id="F78-CLAIM", workspace_root=gov, connection_json=None)
    assert res["ok"], res
    cmd = " ".join(seen[0])
    assert "--autonomy-mode PreAuthorized" in cmd and f"--owner-policy-ref {POLICY}" in cmd, cmd
    assert f"--actor {EXEC} --agent-instance {EXEC}" in cmd and "--confirm" in cmd


def test_f78_pre0_1_gate_envelope_identity_is_driver_not_card_instance(tmp_path, monkeypatch):
    """门侧: 驱动方 token(advisor)提交 claim, actor=卡实例 ≠ token 绑定实例 → 不再 binding_mismatch, 按驱动方身份匹配信封。"""
    from tools.mcp_server import tools as gate

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _card(gov, "F78-GATE", "pending")
    _policy(gov, agent_or_role="advisor")
    cap = {"role": "advisor", "role_class": "advisor", "agent_instance": DRIVER, "token_ref": "t", "expires_at": "2999-01-01T00:00:00Z"}
    monkeypatch.setattr(gate, "_capability_token", lambda: cap)
    matched, status, err, quota = gate._match_claim_envelope(
        gov, owner_policy_ref=POLICY, task_id="F78-GATE", task_path=None, canonical_agent_instance=EXEC, actor=EXEC)
    assert matched == POLICY, (matched, status, err)
    # 非驱动方 token(executor)仍走存量身份门: 绑定实例≠actor → binding_mismatch
    cap_exec = {**cap, "role": "executor", "role_class": "executor", "agent_instance": "exec.other"}
    monkeypatch.setattr(gate, "_capability_token", lambda: cap_exec)
    matched2, status2, _e, _q = gate._match_claim_envelope(
        gov, owner_policy_ref=POLICY, task_id="F78-GATE", task_path=None, canonical_agent_instance=EXEC, actor=EXEC)
    assert matched2 is None and status2 == "binding_mismatch"


# ---------------------------------------------------------------------------
# 前置零② 驱动方身份禁占位
# ---------------------------------------------------------------------------

def test_f78_pre0_2_driver_actor_reads_declaration_never_placeholder(tmp_path, monkeypatch):
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    assert nr._driver_actor(gov) == DRIVER
    (gov / ".lybra" / "role").unlink()
    assert nr._driver_actor(gov) == ""  # 无工位声明、无连接文件 → 不占位
    conn = _connection(tmp_path, ["advisor"], bound={"advisor": "advisor.lybra.bound"})
    assert nr._driver_actor(gov, connection_json=str(conn)) == "advisor.lybra.bound"
    # AIPOS-F73E 件①(改写本条后半): finalize/close 的 actor 不再是驱动方而是该卡 claim 记录实例——
    # 无 claim 记录 → 不可推导并点名 claim 记录; 有 → `--actor <执行实例>`; 驱动方身份只服务信封/claim token, 永不进账务命令 --actor
    task_id = "F78-DRV"
    _card(gov, task_id, "claimed")
    rec = gov / "5_tasks" / "records"
    _write(rec / "returns" / task_id / f"return_{task_id}_x_{EXEC}.md", _fm({"record_type": "return", "task_id": task_id, "return_id": "r1"}))
    _write(rec / "audit_dispatches" / f"{task_id}R" / "dispatch_x.md", _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": task_id}))
    _write(rec / "audit_verdicts" / task_id / "verdict_x.md", _fm({"record_type": "audit_verdict_record", "verdict_id": "verdict_F78_v1", "verdict": "PASS", "verdict_at": "2026-09-21T03:00:00Z"}))
    d = derive_next_step(task_id, gov)
    assert d["derivable"] is False and d["verb"] == "lybra_finalize" and any("claim 记录" in m for m in d["missing_records"]), d
    _claim_record(gov, task_id, EXEC)
    d2 = derive_next_step(task_id, gov)
    assert d2["derivable"] and f"--actor {EXEC}" in d2["command"] and f"--actor {DRIVER}" not in d2["command"], d2


# ---------------------------------------------------------------------------
# 前置零③ close 三字段
# ---------------------------------------------------------------------------

def test_f78_pre0_3_close_needs_three_fields_from_finalization_merge_commit(tmp_path, monkeypatch):
    from tools.aipos_cli.finalization_record import build_finalization_record

    rec_fm = build_finalization_record(task_id="X", actor=DRIVER, commit=SHA_A, authorization_type="verdict_ref", authorization_ref="v1")
    assert rec_fm["merge_commit"] == SHA_A and rec_fm["finalize_ref"].startswith("finalization_X_")
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    task_id = "F78-CLOSE"
    _card(gov, task_id, "claimed")
    _claim_record(gov, task_id, EXEC)
    rec = gov / "5_tasks" / "records"
    _write(rec / "returns" / task_id / f"return_{task_id}_x_{EXEC}.md", _fm({"record_type": "return", "task_id": task_id, "return_id": f"return_{task_id}_x"}))
    _write(rec / "audit_dispatches" / f"{task_id}R" / "dispatch_x.md", _fm({"record_type": "audit_dispatch", "dispatch_id": "d", "reviewed_task_id": task_id}))
    _write(rec / "audit_verdicts" / task_id / "verdict_x.md", _fm({"record_type": "audit_verdict_record", "verdict_id": f"verdict_{task_id}_x", "verdict": "PASS", "verdict_at": "2026-09-21T03:00:00Z"}))
    # finalization 记录缺 merge_commit/commit → 不派生 close, 点名 finalize_commit_hash
    fin = rec / "finalizations" / task_id / "finalization_20260921_040000.md"
    _write(fin, _fm({"record_type": "finalization_record", "task_id": task_id, "finalized_at": "2026-09-21T04:00:00Z", "deploy_status": "deployed"}))
    d = derive_next_step(task_id, gov)
    assert d["derivable"] is False and d["verb"] == "lybra_queue_close_dry_run", d
    assert any(m.startswith("finalize_commit_hash") for m in d["missing_records"]), d["missing_records"]
    # 补 merge_commit → 三字段齐 → 派生 close
    _write(fin, _fm({"record_type": "finalization_record", "task_id": task_id, "finalized_at": "2026-09-21T04:00:00Z",
                     "deploy_status": "deployed", "commit": SHA_A, "merge_commit": "c" * 40}))
    d2 = derive_next_step(task_id, gov)
    assert d2["derivable"] and "lybra queue close" in d2["command"], d2
    evidence = json.loads(d2["command"].split("--closure-evidence '")[1].rstrip("'"))
    assert evidence == {"finalize_commit_hash": "c" * 40, "finalize_return_ref": f"return_{task_id}_x", "verdict_ref": f"verdict_{task_id}_x"}


# ---------------------------------------------------------------------------
# 前置零④ 交回判据读卡分支
# ---------------------------------------------------------------------------

def test_f78_pre0_4_return_criteria_read_card_branch_not_checkout(tmp_path, monkeypatch):
    import tools.aipos_cli.board_adapter as adapter

    repo = _init_product_repo(tmp_path / "repo")
    runall = "agents/harness/pi/lybra-loop/tests/run-all.sh"
    _write(repo / runall, "#!/bin/bash\npython3 tests/test_existing.py\n")
    _write(repo / "tools/a.py", "# a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    # 卡分支: 加新测试 + 把它加进分支上的 run-all.sh
    _git(repo, "checkout", "-q", "-b", "card/F78-C4")
    _write(repo / "tests/test_new.py", "def test_x(): pass\n")
    _write(repo / runall, "#!/bin/bash\npython3 tests/test_existing.py\npython3 tests/test_new.py\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "F78-C4: add test")
    # worktree 模型: 共用检出停在 main(其 run-all.sh 不含新测试); main 又前进了一笔别人的越界改动
    _git(repo, "checkout", "-q", "main")
    _write(repo / "docs/other.md", "someone else\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "OTHER-1: main moved on")
    monkeypatch.setattr(adapter, "_resolve_product_code_repo", lambda x: repo)
    assert adapter._check_test_in_runall(task_id="F78-C4", repo_root=tmp_path) == []  # 读卡分支 run-all → 绿
    assert adapter._check_changes_in_scope(task_id="F78-C4", output_target="tests/, tools/", repo_root=tmp_path) == []  # 三点 diff 不含 docs/other.md
    assert adapter._card_branch_changed_files(repo, "F78-C4") == [runall, "tests/test_new.py"]
    # lane.paths 作车道: 不含 tests/ → 越界, 出口指向 amend --restricted
    reasons = adapter._check_changes_in_scope(task_id="F78-C4", output_target="tests/", repo_root=tmp_path, lane_paths=["tools/"])
    assert len(reasons) == 1 and reasons[0].startswith("CHANGES_OUT_OF_SCOPE") and "lane.paths" in reasons[0] and "--restricted" in reasons[0]


# ---------------------------------------------------------------------------
# 前置零⑤ 受限 amend 含 lane
# ---------------------------------------------------------------------------

def test_f78_pre0_5_advisor_restricted_amend_lane_on_claimed_card(tmp_path, monkeypatch):
    from tools.aipos_cli.board_adapter import amend_task, restricted_amend_fields
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    assert restricted_amend_fields() == {"rework_rounds", "output_target", "lane"}
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    path = _card(gov, "F78-AMEND", "claimed", extra={"claimed_by": EXEC, "claim_id": "c1"})
    lane = {"repo": "/r", "paths": ["tools/aipos_cli/", "tests/", "schema/"], "roles": ["executor"]}
    res = amend_task(task_id="F78-AMEND", actor=DRIVER, amendments={"lane": lane}, amendment_reason="补车道",
                     restricted_fields=restricted_amend_fields(), dry_run=False, repo_root=gov)
    assert res.get("verdict") != "BLOCK", res
    fm, _b, _w = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    assert fm["lane"]["paths"] == ["tools/aipos_cli/", "tests/", "schema/"]
    blocked = amend_task(task_id="F78-AMEND", actor=DRIVER, amendments={"title": "x"}, amendment_reason="越界",
                         restricted_fields=restricted_amend_fields(), dry_run=True, repo_root=gov)
    assert blocked.get("verdict") == "BLOCK" and "Restricted mode only allows amending" in json.dumps(blocked, ensure_ascii=False)
    from tools.aipos_cli.aipos_cli import build_parser

    assert build_parser().parse_args(["queue", "amend", "--task-id", "X", "--actor", "a", "--amendments", "{}",
                                      "--amendment-reason", "r", "--restricted"]).restricted is True


# ---------------------------------------------------------------------------
# 前置零⑥ 世系含 withdrawn; dev_override 认授权记录/世系
# ---------------------------------------------------------------------------

def test_f78_pre0_6_lineage_scans_withdrawn_and_dev_override_needs_owner_record(tmp_path, monkeypatch):
    from tools.aipos_cli.deployment_authorization import _find_continuation_task
    from tools.aipos_cli.finalize import _dev_override_base_authorized

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _write(gov / "card_policy.json", json.dumps({"task_id_pattern": "F78-[A-Z0-9]+", "version": 1}))
    _card(gov, "F78-WD", "withdrawn", extra={"conclusion_note": "卡面缺陷撤废, 产物在 card/F78-WD, 由续卡 F78-WD2 承接"})
    assert _find_continuation_task("F78-WD", gov) == "F78-WD2"
    repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    _write(repo / "f.py", "x\n")
    _git(repo, "add", "f.py")
    _git(repo, "commit", "-q", "-m", "OTHER: dev build")
    sha = _git(repo, "rev-parse", "HEAD")
    ok, why = _dev_override_base_authorized(repo, gov, sha)
    assert ok is False and "Owner 授权记录" in why or ok is False
    _write(gov / "5_tasks" / "records" / "owner_decisions" / "od_1.md",
           _fm({"record_type": "owner_decision_record", "decision_id": "od_1", "decided_at": "2026-09-21T00:00:00Z"},
               f"Owner 授权 dev_override 基线 {sha} 作为 finalize 起点。\n"))
    ok2, why2 = _dev_override_base_authorized(repo, gov, sha)
    assert ok2 is True and "od_1.md" in why2


# ---------------------------------------------------------------------------
# 前置零⑦ JSON 键级三方合并; PASS 后 tip 变化放行复审
# ---------------------------------------------------------------------------

def test_f78_pre0_7_finalize_merges_parallel_json_keys_and_rereview_on_tip_change(tmp_path, monkeypatch):
    from tools.aipos_cli import finalize as fz
    import tools.aipos_cli.board_adapter as adapter

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    decl = repo / "schema" / "decl.json"
    _write(decl, json.dumps({"verbs": {"a": 1}}, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base decl")
    # 卡分支加 key b; main 并行加 key c(同一文件, 并列键)
    _git(repo, "checkout", "-q", "-b", "card/F78-J")
    _write(decl, json.dumps({"verbs": {"a": 1, "b": 2}}, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "F78-J: add b")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _write(decl, json.dumps({"verbs": {"a": 1, "c": 3}}, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "F78-K: add c")
    ops: list[str] = []
    res = fz._integrate_card_branch("F78-J", "verdict_x", repo, gov, False, ops,
                                    branch_integration={"branch_pattern": "card/{task_id}", "merge_strategy": "no-ff",
                                                        "merge_message_format": "Merge {branch}: {summary} ({verdict_id})"},
                                    task_mode="code", output_target="schema/")
    assert res["action"] == "merged" and res.get("json_key_merged") == ["schema/decl.json"], (res, ops)
    merged = json.loads((repo / "schema" / "decl.json").read_text(encoding="utf-8"))
    assert merged == {"verbs": {"a": 1, "b": 2, "c": 3}}
    assert _git(repo, "rev-parse", "card/F78-J") == tip  # 分支 tip 不变: 裁决绑定仍成立
    assert _git(repo, "rev-parse", "HEAD^2") == tip and _git(repo, "status", "--porcelain") == ""
    # 同键异值 = 真冲突 → 中止, main 无半合并残留
    _git(repo, "checkout", "-q", "-b", "card/F78-X")
    _write(decl, json.dumps({"verbs": {"a": 99, "b": 2, "c": 3}}, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "F78-X: a=99")
    _git(repo, "checkout", "-q", "main")
    _write(decl, json.dumps({"verbs": {"a": 42, "b": 2, "c": 3}}, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "F78-Y: a=42")
    res2 = fz._integrate_card_branch("F78-X", "v", repo, gov, False, [], branch_integration={"branch_pattern": "card/{task_id}"}, task_mode="code")
    assert res2["blocked"] and res2["action"] == "blocked_conflict" and _git(repo, "status", "--porcelain") == ""
    # ⑦ 后半: PASS 裁决绑旧 sha, tip 已变 → 复审放行(返回说明), tip 相同 → None(维持终态)
    monkeypatch.setattr(adapter, "_resolve_product_code_repo", lambda x: repo)
    changed = adapter._pass_verdict_tip_changed(gov, "F78-J", {"verdict_id": "v1", "artifact_subject": {"commit_sha": SHA_A}})
    assert changed and "复审" in changed and SHA_A[:12] in changed
    assert adapter._pass_verdict_tip_changed(gov, "F78-J", {"verdict_id": "v1", "artifact_subject": {"commit_sha": tip}}) is None
    assert adapter._pass_verdict_tip_changed(gov, "F78-J", {"verdict_id": "legacy"}) is None


# ---------------------------------------------------------------------------
# 前置零⑧ 门动词校验: 注册 MCP 全名 + 只扫正文
# ---------------------------------------------------------------------------

def test_f78_pre0_8_gate_verb_check_matches_registered_mcp_names_in_body_only(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft, find_gate_verbs_in_intent_body, publish_draft, registered_gate_verbs

    names = registered_gate_verbs()
    assert "lybra_queue_claim_dry_run" in names and "lybra_governance_commit" not in names and "lybra_loop" not in names
    assert find_gate_verbs_in_intent_body("owner_policy_ref=pol_lybra_dev_9; 见 lybra_governance_commit 与 lybra_dev_9") == set()
    assert find_gate_verbs_in_intent_body("执行 lybra_queue_claim_dry_run 然后 lybra_queue_return_confirm") == {"lybra_queue_claim_dry_run", "lybra_queue_return_confirm"}
    # publish: frontmatter 里的策略 id / governance_refs 动词键名不再被当门动词拒(F79B 实撞)
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    meta = {"task_id": "F78-VERB", "title": "t", "project": "lybra", "assigned_to": EXEC, "context_bundle": "t",
            "task_mode": "code", "priority": "high", "status": "pending", "created_by": DRIVER, "needs_owner": False,
            "output_target": "tools/aipos_cli/", "artifact_policy": "formal_write", "owner_policy_ref": "pol_lybra_dev_9",
            "governance_refs": ["依据 verbs.schema lybra_governance_commit 动词键; 信封 pol_lybra_dev_9"]}
    created = create_draft(gov, meta, "## Goal\n\n做事, 不提门动词。\n", dry_run=False)
    assert created["verdict"] != "BLOCK", created
    published = publish_draft(gov, created["target_path"], dry_run=True)
    assert not any("门动词" in r for r in published["blocking_reasons"]), published["blocking_reasons"]
    created2 = create_draft(gov, {**meta, "task_id": "F78-VERB2"}, "## Goal\n\n先 lybra_queue_claim_dry_run 再干活。\n", dry_run=False)
    published2 = publish_draft(gov, created2["target_path"], dry_run=True)
    assert any("门动词" in r and "lybra_queue_claim_dry_run" in r for r in published2["blocking_reasons"]), published2["blocking_reasons"]


# ---------------------------------------------------------------------------
# 前置零⑨ PASS 卡 mark-concluded 登记承接; close 写 conclusion_note
# ---------------------------------------------------------------------------

def test_f78_pre0_9_mark_concluded_allows_pass_card_with_continuation(tmp_path, monkeypatch):
    from tools.aipos_cli.board_adapter import mark_concluded_task
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _write(gov / "card_policy.json", json.dumps({"task_id_pattern": "F78-[A-Z0-9]+", "version": 1}))
    task_id = "F78-MC"
    _card(gov, task_id, "claimed", extra={"claimed_by": EXEC, "claim_id": "c1"})
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / task_id / "verdict_x.md",
           _fm({"record_type": "audit_verdict_record", "verdict_id": "verdict_F78_v1", "verdict": "PASS", "reviewed_task_id": task_id,
                "auditor_instance": AUDITOR, "verdict_at": "2026-09-21T03:00:00Z"}))
    blocked = mark_concluded_task(task_id=task_id, actor=DRIVER, conclusion_note="只是结案", dry_run=True, repo_root=gov)
    assert blocked.get("verdict") == "BLOCK" and "formal audit verdict" in json.dumps(blocked, ensure_ascii=False)
    ok = mark_concluded_task(task_id=task_id, actor=DRIVER, conclusion_note="产物在 card/F78-MC, 由续卡 F78-MC2 承接交回", dry_run=False, repo_root=gov)
    assert ok.get("ok") and ok.get("verdict") != "BLOCK", ok
    completed = gov / "5_tasks" / "queue" / "completed" / f"{task_id.lower()}.md"
    fm, _b, _w = parse_markdown_frontmatter(completed.read_text(encoding="utf-8"))
    assert fm["continuation_task_id"] == "F78-MC2" and "承接" in fm["conclusion_note"]
    from tools.aipos_cli.deployment_authorization import _find_continuation_task

    assert _find_continuation_task(task_id, gov) == "F78-MC2"
    # FAIL 仍拒
    task_f = "F78-MCF"
    _card(gov, task_f, "claimed", extra={"claimed_by": EXEC, "claim_id": "c2"})
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / task_f / "verdict_x.md",
           _fm({"record_type": "audit_verdict_record", "verdict_id": "verdict_F78_v2", "verdict": "FAIL", "reviewed_task_id": task_f,
                "auditor_instance": AUDITOR, "verdict_at": "2026-09-21T03:00:00Z"}))
    still = mark_concluded_task(task_id=task_f, actor=DRIVER, conclusion_note="由续卡 F78-MC3 承接", dry_run=True, repo_root=gov)
    assert still.get("verdict") == "BLOCK"


def test_f78_pre0_9_close_accepts_conclusion_note_parser():
    from tools.aipos_cli.aipos_cli import build_parser
    import inspect
    from tools.aipos_cli.board_adapter import close_task

    assert "conclusion_note" in inspect.signature(close_task).parameters
    args = build_parser().parse_args(["queue", "close", "--task-id", "X", "--actor", DRIVER, "--closure-evidence", "{}",
                                      "--conclusion-note", "由续卡 F78-Z 承接"])
    assert args.conclusion_note == "由续卡 F78-Z 承接"


# ---------------------------------------------------------------------------
# 验收⑤ 夹具入常驻 + 硬约束 grep
# ---------------------------------------------------------------------------

def test_f78_fixture_registered_in_runall_and_no_swallowed_exceptions():
    runall = (REPO_ROOT / "agents" / "harness" / "pi" / "lybra-loop" / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "tests/test_aipos_f78_engine_agnostic.py" in runall
    for rel in ("tools/aipos_cli/card_render.py", "tools/aipos_cli/artifact_ingest.py", "tools/aipos_cli/loop_driver.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"except Exception:\s*\n\s*pass", text), rel
        assert "task_cards/" not in (REPO_ROOT / "tools/aipos_cli/card_render.py").read_text(encoding="utf-8").replace("task_cards_root", "")
    # 单一实现: 推导核 / 渲染器 / ingest 都经 workspace_config.project_paths 读落点, 无第二份路径表
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/aipos_cli/card_render.py", "tools/aipos_cli/artifact_ingest.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "5_tasks/records/returns" not in text, rel
