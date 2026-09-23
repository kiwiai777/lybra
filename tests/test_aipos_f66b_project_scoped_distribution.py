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

_LOCATION_SENTENCES = ("报告落点绝对路径", "审计报告草稿只能落", "报告落位")


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
    assert len(lines) == 3, lines  # 正文三句落点(取证锚点/门领地纪律/审计指令)
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
