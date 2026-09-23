"""AIPOS-F66B: 多项目接入固化(收窄两件 + 09-23 并入件③)

件① 分发按工位项目归属过滤 + 章程=声明渲染物(seed_only 退役):
    目标工位按其 .lybra/role 实例的项目段过滤, 非本项目工位跳过并在 manifest 记 skipped(靶场: 他项目工位 AGENTS.md 字节不变);
    章程 = 母本({{占位}}) + 项目声明(project.json paths/repos + roles.schema write_boundary/hard_rules)→ 工位副本;
    母本/声明变则重渲染; 工位本地改动 = 声明缺口(报 diff + 覆盖); sync 与 distribute_tools 同一渲染函数。
件② 护栏读声明: roles.schema write_boundary(角色类 × 目标面 × read/append/mutate 三级)+ 唯一读取口
    tools/aipos_cli/write_boundary.py(build_write_boundary / check_access / CLI `lybra roles write-boundary`);
    「拒后禁换方式重试」= 角色硬规矩, 随章程渲染下发。
件③ 审计卡「报告落点」文案单源: audit_derivation.render_audit_report_location 唯一渲染(读 verdict_root 声明 +
    artifact_ingest.verdict 候选), 派生卡所有落点句子同源, 不再出现被审卡目录; card render 对审计卡输出声明位。

靶场全部临时目录(含假工位 .lybra/role); 禁碰真实工位 /home/kiwi/projects/kiwiai-pi/*。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import audit_derivation as ad  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402

EXEC = "exec.lybra.test"
AUDITOR = "audit.lybra.test"
DRIVER = "advisor.lybra.test"
CHRIS_EXEC = "hbj-coder.chris-huibojin.test"
CHRIS_AUDITOR = "hbj-auditor.chris-huibojin.test"


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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, shape: str, project: str = "lybra") -> Path:
    """shape=lybra: 无 paths 段(缺省 task_cards); shape=chris: chris 现行落点声明(verdict_root=5_tasks/records/audit_verdicts)。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    root = tmp_path / f"gov-{project}-{shape}"
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "owner_decisions"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "5_tasks" / "policies").mkdir()
    (root / "5_tasks" / "drafts").mkdir()
    (root / "task_cards").mkdir()
    (root / "governance" / "decision_log").mkdir(parents=True)
    code_repo = _init_product_repo(tmp_path / f"product-{project}-{shape}")
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
          project: str = "lybra", extra: dict | None = None, body: str = "") -> Path:
    meta = {"task_id": task_id, "title": f"{task_id} test", "project": project, "task_mode": task_mode,
            "assigned_to": assigned, "agent_instance": assigned, "status": queue, "audit": "required",
            "audit_by": AUDITOR, "output_target": "tools/aipos_cli/, tests/", "priority": "high",
            "created_by": DRIVER, "needs_owner": False, "context_bundle": "t", "artifact_policy": "formal_write"}
    meta.update(extra or {})
    path = gov / "5_tasks" / "queue" / queue / f"{task_id.lower()}.md"
    _write(path, _fm(meta, body or f"# {task_id}\n\n## Goal\n把 {task_id} 做完。\n\n## 验收\n- 夹具绿\n"))
    return path


def _src_meta(task_id: str, project: str = "lybra") -> dict:
    return {"task_id": task_id, "title": f"{task_id} test", "project": project, "task_mode": "code",
            "audit": "required", "priority": "high", "output_target": "tools/aipos_cli/", "artifact_policy": "formal_write",
            "context_bundle": "t", "assigned_to": EXEC, "agent_instance": EXEC}


# ===========================================================================
# 件③ 审计卡「报告落点」文案单源
# ===========================================================================

# AIPOS-F80 件①: 零门审计卡以「交付纪律」节(报告写到 …)取代门领地纪律段(manual_gate_mode 项目仍为门领地纪律段)
_LOCATION_SENTENCES = ("报告落点绝对路径", "审计报告草稿只能落", "报告落位", "报告写到")


def _location_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if any(key in line for key in _LOCATION_SENTENCES)]


@pytest.mark.parametrize("shape,rel", [("lybra", "task_cards"), ("chris", "5_tasks/records/audit_verdicts")])
def test_item3_derived_audit_card_all_location_sentences_from_one_renderer(tmp_path, monkeypatch, shape, rel):
    """派生审计卡: 取证锚点段 / 门领地纪律段 / 审计指令「报告落位」/ governance_refs 锚点 四处落点 = 同一渲染值
    = <治理根>/<verdict_root 声明>/<审计卡ID>/RETURN.md; 被审卡目录不再作为报告落点出现(lybra 形 + chris 形)。"""
    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    src = "F66B-S1"
    spec = ad.build_derived_audit_task(
        source_task_id=src, source_metadata=_src_meta(src), source_path=f"5_tasks/queue/claimed/{src.lower()}.md",
        return_record_ref="return_x", artifact_refs=[],
        collaboration_profile={"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
        repo_root=gov,
    )
    audit_id = spec["audit_task_id"]
    assert audit_id == f"{src}R"
    expected = str(gov / rel / audit_id / "RETURN.md")
    assert ad.render_audit_report_location(gov, audit_id) == expected
    assert str(nr.audit_report_artifact_path(gov, audit_id)) == expected  # 读取口 = 渲染值同源
    body = spec["body"]
    lines = _location_lines(body)
    assert len(lines) == 3, lines  # 正文三句落点(取证锚点/门领地纪律或交付纪律(F80 零门)/审计指令)
    for line in lines:
        assert expected in line, line
    anchors = [r for r in spec["metadata"]["governance_refs"] if "取证锚点" in str(r)]
    assert len(anchors) == 1 and f"报告落点={expected}" in anchors[0], anchors
    # 被审卡目录 / records 裁决目录不再作为报告落点
    whole = body + "\n".join(anchors)
    assert f"{rel}/{src}/" not in whole and f"task_cards/{src}/" not in whole and f"audit_verdicts/{src}/" not in whole
    assert "{audit_id}" not in whole  # 旧占位模板已删


def test_item3_manual_dispatch_uses_same_renderer_with_explicit_audit_id(tmp_path, monkeypatch):
    """手动派审(board_adapter)传显式审计卡 ID 给同一渲染函数: 锚点与自动派生逐字同形。"""
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    section = ad.build_forensic_anchor_section("F66B-S2", gov, _src_meta("F66B-S2"), audit_task_id="F66B-S2R3")
    assert str(gov / "task_cards" / "F66B-S2R3" / "RETURN.md") in section
    assert "F66B-S2/" not in section
    # 未给 audit_task_id 时缺省 = derive_audit_task_id 首号(与 build_derived_audit_task 同一派生)
    default_section = ad.build_forensic_anchor_section("F66B-S2", gov, _src_meta("F66B-S2"))
    assert str(gov / "task_cards" / "F66B-S2R" / "RETURN.md") in default_section
    with pytest.raises(ValueError):
        ad.render_audit_report_location(gov, "")


def test_item3_no_governance_root_renders_declared_relative_location_not_guess():
    """治理根缺 = 渲染声明相对位(缺省 verdict_root + 首个候选), 不猜绝对路径; 门领地纪律段仍含硬话。"""
    text = ad.render_audit_report_location(None, "X-1R")
    assert text == "<governance_root>/task_cards/X-1R/RETURN.md"
    section = ad.build_gate_territory_discipline_section("X-1", None)
    assert "审计报告草稿只能落" in section and text in section


def test_item3_source_has_no_hardcoded_report_location_templates():
    """存量两个写死模板(取证锚点段 L83 / governance_refs 锚点 L405)已删, 全模块无 task_cards 字面、无被审卡目录模板。"""
    src = (REPO_ROOT / "tools/aipos_cli/audit_derivation.py").read_text(encoding="utf-8")
    assert "_resolve_governance_task_cards_path" not in src
    assert '"task_cards"' not in src and "task_cards/" not in src.replace("task_cards/{被审卡ID}/", "").replace("task_cards/<审计卡ID>/", "")
    assert "audit_verdicts/{source_task_id}" not in src
    assert "{task_cards_path}" not in src
    assert src.count("render_audit_report_location(") >= 4  # 定义 + 三处调用
    ba = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    assert "_resolve_governance_task_cards_path" not in ba and "render_audit_report_location(repo_root, task_id_text)" in ba
    assert not re.search(r"except Exception:\s*\n\s*pass", (REPO_ROOT / "tools/aipos_cli/card_render.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("shape,rel", [("lybra", "task_cards"), ("chris", "5_tasks/records/audit_verdicts")])
def test_item3_card_render_pi_audit_card_location_is_declared_verdict_slot(tmp_path, monkeypatch, shape, rel):
    """`lybra card render --harness pi` 对审计卡(task_mode=audit)输出的报告落点 = 声明位 verdict_root/<审计卡ID>/RETURN.md,
    提示审计报告必填 frontmatter(verdict/commit_sha); 执行卡仍 = return_root/<ID>/RETURN.md。"""
    from tools.aipos_cli.card_render import render_card

    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    src, audit = "F66B-R1", "F66B-R1R"
    _card(gov, src, "completed")
    _card(gov, audit, "pending", assigned=AUDITOR, task_mode="audit",
          extra={"reviewed_task_id": src, "derived_from": src, "audit": "none"})
    pi = render_card(audit, gov, harness="pi")["files"]["stdout"]
    assert f"报告落点: {gov / rel / audit / 'RETURN.md'}" in pi, pi
    assert "verdict, commit_sha" in pi and f"/{src}/" not in pi
    exec_pi = render_card(src, gov, harness="pi")["files"]["stdout"]
    return_rel = "task_cards" if shape == "lybra" else "5_tasks/records/returns"
    assert f"报告落点: {gov / return_rel / src / 'RETURN.md'}" in exec_pi
    for content in (pi, exec_pi):
        assert "lybra_" not in content and "token" not in content.lower()


# ===========================================================================
# 件② 护栏读声明: roles.schema write_boundary + 唯一读取口
# ===========================================================================

def _synthetic_token(name: str, cls: str, project: str) -> dict:
    """夹具注册表条目(合成 token——真凭据永不入夹具)。"""
    return {
        "agent_instance": f"{name}.{project}.test", "fingerprint": f"sha256:fx{abs(hash(name)) % 10**10:010d}",
        "projects": [project], "role": name, "role_class": cls, "scopes": [],
        "token": f"fixture-not-a-secret-{name}", "token_ref": f"svc-{name}",
    }


def _seed_gate_registry(gov: Path, tokens: list[dict]) -> None:
    _write(gov / ".lybra" / "connection.json", json.dumps({"config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"}, "tokens": tokens}, indent=2) + "\n")


def test_item2_declaration_single_source_and_fail_closed(monkeypatch):
    from tools.aipos_cli import write_boundary as wb
    from tools.schema_loader import SchemaLoadError

    decl = wb.load_write_boundary_declaration()
    assert decl["level_order"] == ["read", "append", "mutate"] and set(decl["levels"]) == {"read", "append", "mutate"}
    for cls, row in decl["matrix"].items():
        for surface, level in row.items():
            assert surface in decl["surfaces"], (cls, surface)
            assert level in decl["level_order"], (cls, surface, level)
        assert row.get("governance_root") in decl["level_order"], f"{cls}: 只读治理永远合法须有 governance_root 行"
    assert any(r.get("id") == "no_retry_after_deny" for r in decl["hard_rules"])
    # 声明缺 = SchemaLoadError(禁静默缺省)
    monkeypatch.setattr(wb, "load_schema", lambda kind, root=None: {"roles": []})
    with pytest.raises(SchemaLoadError):
        wb.load_write_boundary_declaration()


def test_item2_readable_face_expands_custom_roles_by_gate_registry(tmp_path, monkeypatch):
    """可读面: 内建角色行 + 门注册表自定义角色按类展开(hbj-coder→executor); 路径全部读声明(chris 形落点跟随)。"""
    from tools.aipos_cli import write_boundary as wb

    gov = _make_gov(tmp_path, monkeypatch, shape="chris", project="chris-huibojin")
    _seed_gate_registry(gov, [_synthetic_token("hbj-coder", "executor", "chris-huibojin")])
    face = wb.build_write_boundary(gov)
    assert face["project"] == "chris-huibojin" and face["roles"]["hbj-coder"] == "executor"
    by = {(r["role"], r["surface"]): r for r in face["rows"]}
    assert by[("hbj-coder", "return_slot")]["level"] == "mutate"
    assert by[("hbj-coder", "return_slot")]["path"] == str(gov / "5_tasks" / "records" / "returns" / "<task_id>")
    assert by[("hbj-coder", "records")]["level"] == "read" and by[("hbj-coder", "records")]["path"] == str(gov / "5_tasks" / "records")
    assert by[("auditor", "verdict_slot")]["path"] == str(gov / "5_tasks" / "records" / "audit_verdicts" / "<task_id>")
    assert by[("advisor", "decision_log")]["level"] == "append" and by[("advisor", "decision_log")]["path"] == str(gov / "governance" / "decision_log")
    assert by[("executor", "product_repo")]["path"] == json.loads((gov / "project.json").read_text())["code_repo"]
    md = wb.render_write_boundary_markdown(face, role="hbj-coder")
    assert "no_retry_after_deny" in md and "| return_slot | **mutate** |" in md and "hbj-auditor" not in md
    with pytest.raises(wb.WriteBoundaryError):
        wb.build_write_boundary(gov, role="nobody-role")


def test_item2_check_access_three_levels_and_fail_closed(tmp_path, monkeypatch):
    """读取口判定: 只读治理永远合法 / 级别不足拒 / append 面禁 mutate / 面外未声明拒 / per_task 面按本卡目录 /
    product_repo mutate 受本卡工作树 + lane.paths 双限。"""
    from tools.aipos_cli import write_boundary as wb

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    _seed_gate_registry(gov, [_synthetic_token("hbj-coder", "executor", "lybra")])
    repo = Path(json.loads((gov / "project.json").read_text())["code_repo"])
    task = "F66B-WB1"
    _card(gov, task, "claimed", extra={"lane": {"repo": str(repo), "paths": ["tools/aipos_cli/", "tests/"], "roles": ["executor"]}})

    def chk(role, path, level, **kw):
        return wb.check_access(gov, role=role, path=path, level=level, **kw)

    assert chk("executor", gov / "governance" / "DISCIPLINE.md", "read")["allowed"]  # 只读治理永远合法
    assert chk("hbj-coder", gov / "governance" / "DISCIPLINE.md", "read")["allowed"]  # 自定义角色按类
    r = chk("executor", gov / "governance" / "DISCIPLINE.md", "mutate")
    assert not r["allowed"] and r["code"] == "LEVEL_EXCEEDED" and r["surface"] == "governance_root"
    r = chk("executor", gov / "5_tasks" / "records" / "claims" / "x.md", "mutate")
    assert not r["allowed"] and r["surface"] == "records"  # 门领地
    assert chk("advisor", gov / "governance" / "decision_log" / "2026-09" / "d.md", "append")["allowed"]
    r = chk("advisor", gov / "governance" / "decision_log" / "2026-09" / "d.md", "mutate")
    assert not r["allowed"] and r["code"] == "LEVEL_EXCEEDED" and r["granted_level"] == "append"
    r = chk("executor", tmp_path / "elsewhere" / "x.md", "read")
    assert not r["allowed"] and r["code"] == "SURFACE_UNDECLARED"
    # per_task 面
    r = chk("executor", gov / "task_cards" / task / "RETURN.md", "mutate")
    assert not r["allowed"] and r["code"] == "TASK_ID_REQUIRED"
    assert chk("executor", gov / "task_cards" / task / "RETURN.md", "mutate", task_id=task)["allowed"]
    r = chk("executor", gov / "task_cards" / "OTHER-1" / "RETURN.md", "mutate", task_id=task)
    assert not r["allowed"] and r["code"] == "NOT_OWN_TASK_DIR"
    assert chk("executor", gov / "task_cards" / "OTHER-1" / "RETURN.md", "read")["allowed"]
    # lybra 形 return_root = verdict_root = task_cards 同根: auditor 走 verdict_slot(本审计卡目录 mutate), 台账根级文件只读
    assert chk("auditor", gov / "task_cards" / task / "RETURN.md", "mutate", task_id=task)["surface"] == "verdict_slot"
    r = chk("auditor", gov / "task_cards" / "OTHER-1" / "RETURN.md", "mutate", task_id=task)
    assert not r["allowed"] and r["code"] == "NOT_OWN_TASK_DIR"
    r = chk("auditor", gov / "task_cards" / "INDEX.md", "mutate", task_id=task)
    assert not r["allowed"] and r["code"] == "NOT_OWN_TASK_DIR" and r["surface"] == "verdict_slot"
    # product_repo: auditor 只读; executor mutate 限本卡工作树 + lane.paths
    assert chk("auditor", repo / "tools" / "x.py", "read")["allowed"]
    assert chk("auditor", repo / "tools" / "x.py", "mutate")["code"] == "LEVEL_EXCEEDED"
    r = chk("executor", repo / "tools" / "aipos_cli" / "x.py", "mutate", task_id=task)
    assert not r["allowed"] and r["code"] == "NOT_CARD_WORKTREE"  # 主检出拒
    wt = repo / ".worktrees" / task
    assert chk("executor", wt / "tools" / "aipos_cli" / "x.py", "mutate", task_id=task)["allowed"]
    r = chk("executor", wt / "agents" / "roles" / "executor" / "AGENTS.md", "mutate", task_id=task)
    assert not r["allowed"] and r["code"] == "LANE_OUT_OF_SCOPE"
    with pytest.raises(wb.WriteBoundaryError):
        chk("executor", gov / "x.md", "delete")
    with pytest.raises(wb.WriteBoundaryError):
        chk("ghost-role", gov / "x.md", "read")
    for result in (r,):
        assert any(h.get("id") == "no_retry_after_deny" for h in result["hard_rules"])


def test_item2_cli_write_boundary_json_and_check_exit_codes(tmp_path, monkeypatch, capsys):
    from tools.aipos_cli.aipos_cli import main

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    rc = main(["roles", "--workspace-root", str(gov), "write-boundary", "--role", "executor", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["roles"] == {"executor": "executor"} and all(r["role"] == "executor" for r in data["rows"])
    assert "fixture-not-a-secret" not in out and '"token"' not in out  # 面不含任何凭据字段
    rc = main(["roles", "--workspace-root", str(gov), "write-boundary", "--role", "executor", "--check", "governance/x.md", "--level", "mutate"])
    assert rc == 3 and "DENY [LEVEL_EXCEEDED]" in capsys.readouterr().out
    rc = main(["roles", "--workspace-root", str(gov), "write-boundary", "--role", "executor", "--check", "governance/x.md", "--level", "read"])
    assert rc == 0 and "ALLOW [OK]" in capsys.readouterr().out
    rc = main(["roles", "--workspace-root", str(gov), "write-boundary", "--role", "auditor", "--markdown"])
    md = capsys.readouterr().out
    assert rc == 0 and md.startswith("## 🔴 写权限边界") and "no_retry_after_deny" in md


# ===========================================================================
# 件① 分发按工位项目归属过滤 + 章程 = 声明渲染物
# ===========================================================================

MASTER_V1 = "# 角色: executor 章程 v1\n\n- 治理根只读: {{governance_root}}\n- 产品仓: {{code_repo}}\n- 门: {{gate_url}}\n"
MASTER_V2 = MASTER_V1 + "\n## 新增: 你不接触门\n"
SKILL_MD = "# block-and-report\n"


def _workstation(parent: Path, name: str, *, role: str, instance: str, gov: Path, project: str, charter_text: str) -> Path:
    ws = parent / name
    _write(ws / ".lybra" / "role", json.dumps({"role": role, "instance": instance, "owner_policy_ref": "pol_x"}))
    _write(ws / ".lybra" / "connection.json", json.dumps({
        "config_version": 1, "mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"}, "governance_root": str(gov),
        "workspace_root": str(gov), "tokens": [_synthetic_token(role, "executor", project) | {"agent_instance": instance}],
    }))
    _write(ws / "AGENTS.md", charter_text)
    return ws


class FakeGate:
    """假门: 按 token 记调用; manifest 按角色给; fetch 按母本给。真门动词名沿用(工位发起 pull, 门被动)。"""
    masters: dict[str, str] = {}
    skills: dict[str, str] = {}
    calls: list[tuple[str, str]] = []

    def __init__(self, base_url: str, token: str, **_: object) -> None:
        self.token = token

    def initialize(self) -> None:
        FakeGate.calls.append((self.token, "initialize"))

    @classmethod
    def _remote(cls, role: str) -> dict:
        return {
            "ok": True, "role": role, "product_commit": "abc123abc123", "harness": "pi",
            "distributions": [
                {"distribution_id": f"{role}-charter", "kind": "charter", "source_commit": "abc123abc123", "source_is_file": True,
                 "target_base": "harness_root", "target_path": "AGENTS.md",
                 "files": [{"path": "AGENTS.md", "sha256": _sha(cls.masters[role].encode()), "size": 1}]},
                {"distribution_id": f"{role}-skills", "kind": "skills", "source_commit": "abc123abc123", "source_is_file": False,
                 "target_base": "harness_parent", "target_path": "_distributed/skills",
                 "files": [{"path": p, "sha256": _sha(c.encode()), "size": len(c)} for p, c in sorted(cls.skills.items())]},
            ],
        }

    def call_tool(self, name: str, args: dict) -> dict:
        FakeGate.calls.append((self.token, name))
        role = "executor" if "executor" in self.token else "hbj-coder"
        if name == "lybra_distribution_manifest":
            return self._remote(role)
        if name == "lybra_distribution_fetch":
            files = []
            for rel in args["paths"]:
                content = self.masters[role] if args["distribution_id"].endswith("-charter") else self.skills[rel]
                files.append({"path": rel, "content_b64": base64.b64encode(content.encode()).decode()})
            return {"ok": True, "distribution_id": args["distribution_id"], "files": files}
        raise AssertionError(f"unexpected verb {name}")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """两治理根(lybra 形 + chris 形)+ 一个工位父根: lybra-executor(lybra) 与 hbj-coder(chris-huibojin)。"""
    from tools.aipos_cli import confirm_client

    gov_l = _make_gov(tmp_path, monkeypatch, shape="lybra", project="lybra")
    gov_c = _make_gov(tmp_path, monkeypatch, shape="chris", project="chris-huibojin")
    # 真实拓扑: 自定义角色 hbj-coder 登记在 lybra 门注册表(home 下 lybra 工作区 connection.json), chris 工作区无自家注册表
    _seed_gate_registry(gov_l, [_synthetic_token("hbj-coder", "executor", "chris-huibojin")])
    parent = tmp_path / "kiwiai-pi"
    ws_l = _workstation(parent, "lybra-executor", role="executor", instance=EXEC, gov=gov_l, project="lybra", charter_text="# lybra 旧副本(seed_only 时代)\n")
    ws_c = _workstation(parent, "hbj-coder", role="hbj-coder", instance=CHRIS_EXEC, gov=gov_c, project="chris-huibojin", charter_text="# chris 自家 coder 章程(禁被 lybra 覆盖)\n")
    (parent / "_shared").mkdir()
    FakeGate.masters = {"executor": MASTER_V1, "hbj-coder": "# hbj-coder 章程 {{project}}\n"}
    FakeGate.skills = {"block-and-report/SKILL.md": SKILL_MD}
    FakeGate.calls = []
    monkeypatch.setattr(confirm_client, "GateClient", FakeGate)
    monkeypatch.delenv("LYBRA_HARNESS_ROOT", raising=False)
    return {"gov_l": gov_l, "gov_c": gov_c, "parent": parent, "ws_l": ws_l, "ws_c": ws_c}


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_item1_multi_sync_skips_foreign_workstation_bytes_unchanged(rig):
    """靶场: 工位父根下放一个他项目工位, 以 lybra 范围 sync → 他项目 AGENTS.md 字节不变、manifest 记 skipped、其 token 未连门;
    本项目工位章程 = 母本渲染物(母本正文 + 项目声明尾节 + 写权限边界节), manifest 记三指纹 + 工位归属。"""
    from tools.aipos_cli import distribution_sync as ds

    foreign_before = _md5(rig["ws_c"] / "AGENTS.md")
    run = ds.sync_many(rig["parent"], governance_root=rig["gov_l"])
    assert run["ok"] and run["mode"] == "multi" and run["scope_project"] == "lybra"
    by = {Path(e["harness_root"]).name: e for e in run["workstations"]}
    assert by["hbj-coder"]["status"] == "skipped" and "非本项目工位" in by["hbj-coder"]["reason"]
    assert by["lybra-executor"]["status"] == "synced"
    assert _md5(rig["ws_c"] / "AGENTS.md") == foreign_before  # 他项目工位字节不变
    assert not any("hbj-coder" in tok for tok, _ in FakeGate.calls)  # 跳过 = 未连门
    # 运行清单记 skipped
    run_manifest = json.loads((rig["parent"] / "_distributed" / ds.SYNC_RUN_MANIFEST).read_text())
    assert [w["status"] for w in run_manifest["workstations"]] == ["skipped", "synced"] or sorted(w["status"] for w in run_manifest["workstations"]) == ["skipped", "synced"]
    assert any(w["status"] == "skipped" and w["workstation"]["project"] == "chris-huibojin" for w in run_manifest["workstations"])
    # 本项目工位: 渲染物
    text = (rig["ws_l"] / "AGENTS.md").read_text()
    assert text.startswith("# 角色: executor 章程 v1")
    assert f"- 治理根只读: {rig['gov_l']}" in text and "{{" not in text
    assert "## 项目声明(声明渲染, AIPOS-F66B 件①)" in text and "no_retry_after_deny" in text
    assert f"`{rig['gov_l'] / 'task_cards'}/<卡ID>/`" in text
    assert "<!-- lybra:charter-render master_sha256=" in text
    assert "fixture-not-a-secret" not in text
    manifest = json.loads((rig["parent"] / "_distributed" / ".version-executor").read_text())
    charter = next(d for d in manifest["distributions"] if d["kind"] == "charter")
    assert charter["rendered"] is True and charter["files"][0]["rendered_sha256"] == _sha(text.encode())
    assert charter["files"][0]["sha256"] == _sha(MASTER_V1.encode()) and charter["files"][0]["render_context_sha256"]
    assert manifest["workstation"] == {"instance": EXEC, "project": "lybra", "role": "executor", "harness_root": str(rig["ws_l"])}
    assert (rig["parent"] / "_distributed" / "skills" / "block-and-report" / "SKILL.md").read_text() == SKILL_MD
    res = by["lybra-executor"]["result"]
    assert [g["reason"] for g in res["declaration_gaps"]] == ["unrendered(无渲染指纹: seed_only 时代副本或首次渲染)"]
    assert "seed_only 时代" in res["declaration_gaps"][0]["diff"]
    assert "fixture-not-a-secret" not in json.dumps(run, ensure_ascii=False)


def test_item1_multi_sync_requires_explicit_scope(rig):
    from tools.aipos_cli import distribution_sync as ds

    with pytest.raises(ValueError, match="多工位 sync 必须显式项目范围"):
        ds.sync_many(rig["parent"])
    empty = rig["parent"].parent / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="工位父根"):
        ds.sync_many(empty)  # 既非工位也无子工位


def test_item1_single_workstation_defaults_to_own_project_and_chris_declaration(rig):
    """单工位 sync 缺省范围 = 工位自身项目(hbj-coder → chris-huibojin), 章程按 chris 形声明渲染; 显式 --project lybra 则跳过。"""
    from tools.aipos_cli import distribution_sync as ds

    run = ds.sync_many(rig["ws_c"])
    assert run["ok"] and run["mode"] == "single" and run["scope_project"] == "chris-huibojin"
    e = run["workstations"][0]
    assert e["status"] == "synced" and e["result"]["governance_root"] == str(rig["gov_c"])
    text = (rig["ws_c"] / "AGENTS.md").read_text()
    assert text.startswith("# hbj-coder 章程 chris-huibojin\n")
    assert f"`{rig['gov_c'] / '5_tasks' / 'records' / 'returns'}/<卡ID>/`" in text  # chris 形落点声明进章程
    assert "角色 `hbj-coder`(类 `executor`)" in text
    before = _md5(rig["ws_c"] / "AGENTS.md")
    run2 = ds.sync_many(rig["ws_c"], project="lybra")
    assert run2["workstations"][0]["status"] == "skipped" and _md5(rig["ws_c"] / "AGENTS.md") == before


def test_item1_charter_rerenders_on_master_or_declaration_change_and_reports_local_edit(rig):
    """更新语义: 同输入二次 sync 零拉取; 母本变 → 重渲染; 工位本地改动 → 声明缺口(报 diff + 覆盖); 声明变 → 重渲染。"""
    from tools.aipos_cli import distribution_sync as ds

    ws, gov = rig["ws_l"], rig["gov_l"]
    first = ds.sync(harness_root=ws, governance_root=gov)
    assert first["status"] == "synced" and first["files_fetched"] == 2
    second = ds.sync(harness_root=ws, governance_root=gov)
    assert second["files_fetched"] == 0 and second["plan"] == [] and second["declaration_gaps"] == []
    manifest_after_second = json.loads((rig["parent"] / "_distributed" / ".version-executor").read_text())
    assert next(d for d in manifest_after_second["distributions"] if d["kind"] == "charter")["files"][0]["rendered_sha256"]  # 指纹沿用不丢
    # 母本变(F73C「你不接触门」到得了工位)
    FakeGate.masters["executor"] = MASTER_V2
    third = ds.sync(harness_root=ws, governance_root=gov)
    assert [c["action"] for c in third["changes"]] == ["rendered"] and third["changes"][0]["reasons"] == {"AGENTS.md": "master_changed(母本变)"}
    assert "## 新增: 你不接触门" in (ws / "AGENTS.md").read_text() and third["declaration_gaps"] == []
    # 工位本地改动 = 声明缺口: 报 diff + 覆盖
    local = (ws / "AGENTS.md").read_text() + "\n- 我私自加的一条\n"
    (ws / "AGENTS.md").write_text(local)
    fourth = ds.sync(harness_root=ws, governance_root=gov)
    assert fourth["changes"][0]["reasons"] == {"AGENTS.md": "local_edit(工位本地改动 = 声明缺口, 将覆盖并报 diff)"}
    assert len(fourth["declaration_gaps"]) == 1 and "+- 我私自加的一条" in fourth["declaration_gaps"][0]["diff"]
    assert "我私自加的一条" not in (ws / "AGENTS.md").read_text()
    # 声明变(project.json paths)→ 重渲染
    decl = json.loads((gov / "project.json").read_text())
    decl["paths"] = {"return_root": "5_tasks/records/returns"}
    _write(gov / "project.json", json.dumps(decl))
    fifth = ds.sync(harness_root=ws, governance_root=gov)
    assert fifth["changes"][0]["reasons"] == {"AGENTS.md": "declaration_changed(项目/角色声明变)"}
    assert f"`{gov / '5_tasks' / 'records' / 'returns'}/<卡ID>/`" in (ws / "AGENTS.md").read_text()


def test_item1_dry_run_writes_nothing_and_lists_would_render(rig, capsys):
    """--dry-run: 零写入, 列 would-render/would-fetch/skipped; 文本面与 JSON 面同源。"""
    from tools.aipos_cli.aipos_cli import main
    from tools.aipos_cli import distribution_sync as ds

    snapshot = {p: _md5(p) for p in rig["parent"].rglob("*") if p.is_file()}
    run = ds.sync_many(rig["parent"], governance_root=rig["gov_l"], dry_run=True)
    assert run["ok"] and run["dry_run"] is True
    by = {Path(e["harness_root"]).name: e for e in run["workstations"]}
    assert by["hbj-coder"]["status"] == "skipped" and by["lybra-executor"]["status"] == "dry-run"
    plan = {p["distribution_id"]: p for p in by["lybra-executor"]["result"]["plan"]}
    assert plan["executor-charter"]["action"] == "would-render" and plan["executor-skills"]["action"] == "would-fetch"
    assert {p: _md5(p) for p in rig["parent"].rglob("*") if p.is_file()} == snapshot  # 零写入
    assert not (rig["parent"] / "_distributed" / ds.SYNC_RUN_MANIFEST).exists()
    rc = main(["sync", "--harness-root", str(rig["parent"]), "--workspace-root", str(rig["gov_l"]), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "hbj-coder: skipped" in out and "lybra-executor: dry-run" in out and "would-render: executor-charter" in out
    assert "fixture-not-a-secret" not in out
    assert {p: _md5(p) for p in rig["parent"].rglob("*") if p.is_file()} == snapshot


def test_item1_push_engine_same_ownership_rule_and_rendered_charter(rig, monkeypatch):
    """推送侧 distribute_tools 同规则: 他项目工位跳过零写入; 角色不符拒; 本项目工位章程渲染 + manifest 三指纹 + 工位归属。"""
    import tools.distribute_tools as dt

    foreign_before = _md5(rig["ws_c"] / "AGENTS.md")
    skipped = dt.distribute_to_harness(rig["ws_c"], "hbj-coder", project="lybra")
    assert skipped["ok"] and skipped["skipped_workstation"] and skipped["distributed"] == [] and _md5(rig["ws_c"] / "AGENTS.md") == foreign_before
    with pytest.raises(ValueError, match="拒绝分发"):
        dt.distribute_to_harness(rig["ws_c"], "executor")
    with pytest.raises(dt.WorkstationIdentityError if hasattr(dt, "WorkstationIdentityError") else ValueError):
        dt.distribute_to_harness(rig["parent"], "executor")  # 父根不是工位
    result = dt.distribute_to_harness(rig["ws_l"], "executor", governance_root=rig["gov_l"])
    assert result["ok"] and result["workstation"]["project"] == "lybra" and result["governance_root"] == str(rig["gov_l"])
    assert any(item.startswith("executor-charter (charter, rendered)") for item in result["distributed"])
    text = (rig["ws_l"] / "AGENTS.md").read_text()
    master = (REPO_ROOT / "agents" / "roles" / "executor" / "AGENTS.md").read_text()
    # AIPOS-F80 件②: 母本已占位化, 渲染物前缀 = 母本按本工位声明上下文替换后的正文(同一 _substitute 实现)
    from tools.aipos_cli.charter_render import _substitute, charter_render_context, workstation_identity

    ctx = charter_render_context(rig["gov_l"], identity=workstation_identity(rig["ws_l"]))
    assert text.startswith(_substitute(master, ctx).rstrip("\n")) and "## 项目声明(声明渲染, AIPOS-F66B 件①)" in text
    assert "{{" not in text
    assert result["declaration_gaps"] and result["declaration_gaps"][0]["distribution_id"] == "executor-charter"
    manifest = json.loads((rig["parent"] / "_distributed" / ".version-executor").read_text())
    charter = next(d for d in manifest["distributions"] if d["kind"] == "charter")
    assert charter["rendered"] and charter["files"][0]["rendered_sha256"] == _sha(text.encode()) and manifest["workstation"]["instance"] == EXEC
    again = dt.distribute_to_harness(rig["ws_l"], "executor", governance_root=rig["gov_l"], force=True)
    assert any(item.startswith("executor-charter (charter, unchanged)") for item in again["distributed"]) and again["declaration_gaps"] == []


def test_item1_single_implementation_and_declarations():
    """单一实现: sync 与 distribute_tools 都经 charter_render.render_charter; 工位归属经 charter_render.workstation_identity;
    distribution.schema charter 条目 = render_charter 且无 seed_only; distribution_sync 无吞异常。"""
    sync_src = (REPO_ROOT / "tools/aipos_cli/distribution_sync.py").read_text(encoding="utf-8")
    push_src = (REPO_ROOT / "tools/distribute_tools.py").read_text(encoding="utf-8")
    for src in (sync_src, push_src):
        assert "render_charter" in src and "workstation_identity" in src and "seed_only_skip" not in src
    assert not re.search(r"except Exception:\s*\n\s*pass", sync_src) and "except Exception:" not in sync_src
    schema = json.loads((REPO_ROOT / "schema/distribution.schema.json").read_text(encoding="utf-8"))
    assert "seed_only_semantics" not in schema and "charter_render_semantics" in schema and "workstation_ownership_semantics" in schema
    for dist in schema["distributions"]:
        if dist["kind"] == "charter":
            assert dist["operation"] == "render_charter" and "seed_only" not in dist
    from tools.aipos_cli.naming_profile import parse_instance_name

    assert parse_instance_name("hbj-coder.chris-huibojin.kiwiai-dev") == {"prefix": "hbj-coder", "project": "chris-huibojin", "host": "kiwiai-dev"}
    assert parse_instance_name("bad") is None and parse_instance_name("a..b") is None
