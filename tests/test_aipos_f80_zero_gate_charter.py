"""AIPOS-F80: 章程与卡模板零门收口两件(F66BR F-2/F-4 + 09-22 F79DR 报告错落实撞)

件① 派生审计卡零门: 「哪类卡带认领与交回节」唯一判据 draft_writer.card_carries_gate_contract_section
    (roles 注册表角色类 executor/auditor = 零门卡面; manual_gate_mode 项目两类卡都保留现行为);
    派生审计卡不再追加「认领与交回」节、不再注入门动词提交配方(门领地纪律节), 报告落位句 =
    「报告写到 <落点>, 写完即止, 认领与裁决提交由驱动方完成」, 落点只出自 render_audit_report_location;
    regen 入口对存量 pending 审计卡同口径去节。
件② 三份章程母本占位化: 项目/实例/机器/仓路径/信封 id 字面 → {{key}}(键只用 charter_render_context 声明),
    报告落点改为卡面声明的落点(治理根 return_root / verdict_root), 删「默认 ~/projects/lybra/task_cards」。

靶场全部临时目录(lybra 形 + chris 形 manual_gate_mode=true 两个渲染上下文); 不碰真实工位 /home/kiwi/projects/kiwiai-pi/*。
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import audit_derivation as ad  # noqa: E402
from tools.aipos_cli import draft_writer as dw  # noqa: E402
from tools.aipos_cli.charter_render import charter_render_context, render_charter  # noqa: E402

GATE_VERBS_RE = re.compile(r"lybra_queue_claim|lybra_audit_verdict|lybra_task_progress|records/audit_verdicts")
MASTER_LITERAL_RE = re.compile(r"~/projects/lybra|lybra\.kiwiai|pol_lybra_|kiwiai-dev")
ROLES = ("executor", "auditor", "advisor")
PROFILE = {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}


# ---------------------------------------------------------------------------
# 靶场
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    return path


def _make_gov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, shape: str) -> Path:
    """shape=lybra: lybra 形(paths 缺省 task_cards, manual_gate_mode 在 paths 段=false);
    shape=chris: chris 形(manual_gate_mode=true 顶层, return/verdict_root 落 5_tasks/records/…, 自家产品仓)。"""
    monkeypatch.delenv("LYBRA_CONNECTION_JSON", raising=False)
    monkeypatch.delenv("AIPOS_WORKSPACE_ROOT", raising=False)
    project = "lybra" if shape == "lybra" else "chris-huibojin"
    root = tmp_path / f"gov-{shape}"
    for sub in ("pending", "claimed", "completed", "blocked", "withdrawn"):
        (root / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("returns", "audit_verdicts", "audit_dispatches", "publishes"):
        (root / "5_tasks" / "records" / sub).mkdir(parents=True)
    (root / "task_cards").mkdir()
    code_repo = _init_repo(tmp_path / f"product-{shape}")
    decl: dict = {"project": project, "code_repo": str(code_repo), "config_version": 1}
    if shape == "lybra":
        decl["paths"] = {"manual_gate_mode": False, "return_root": "task_cards", "verdict_root": "task_cards",
                         "queue_root": "5_tasks/queue", "task_cards_root": "task_cards"}
    else:
        decl["manual_gate_mode"] = True
        decl["paths"] = {"return_root": "5_tasks/records/returns", "verdict_root": "5_tasks/records/audit_verdicts",
                         "queue_root": "5_tasks/queue", "task_cards_root": "task_cards"}
    _write(root / "project.json", json.dumps(decl))
    tag = "lybra" if shape == "lybra" else "chris"
    _write(root / "5_tasks" / "policies" / f"pol_{tag}_dev_1.md",
           f"---\npolicy_id: pol_{tag}_dev_1\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n")
    _write(root / "5_tasks" / "policies" / f"pol_{tag}_audit_1.md",
           f"---\npolicy_id: pol_{tag}_audit_1\nstatus: active\nrole: audit\npolicy_type: audit\n---\n# Audit\n")
    return root


def _src_meta(task_id: str, project: str) -> dict:
    return {"task_id": task_id, "title": f"{task_id} test", "project": project, "task_mode": "code",
            "audit": "required", "priority": "high", "output_target": "tools/aipos_cli/", "artifact_policy": "formal_write",
            "context_bundle": "t", "assigned_to": f"exec.{project}.host", "agent_instance": f"exec.{project}.host"}


def _derive(gov: Path, src: str) -> dict:
    project = json.loads((gov / "project.json").read_text())["project"]
    return ad.build_derived_audit_task(
        source_task_id=src, source_metadata=_src_meta(src, project), source_path=f"5_tasks/queue/claimed/{src.lower()}.md",
        return_record_ref="return_x", artifact_refs=[], collaboration_profile=PROFILE, repo_root=gov,
    )


# ===========================================================================
# 件① 派生审计卡零门
# ===========================================================================

def test_item1_derived_audit_card_zero_gate_lybra_shape(tmp_path, monkeypatch):
    """验收①: 新派生审计卡(lybra 形)无「认领与交回」节、无门动词、无 records/audit_verdicts;
    落点 = 治理根 task_cards/{审计卡ID}/RETURN.md, 句子 = 报告写到 <落点>, 写完即止, 认领与裁决提交由驱动方完成。"""
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    spec = _derive(gov, "F80-S1")
    body = spec["body"]
    audit_id = spec["audit_task_id"]
    expected = str(gov / "task_cards" / audit_id / "RETURN.md")
    assert audit_id == "F80-S1R"
    assert ad.render_audit_report_location(gov, audit_id) == expected
    assert "【认领与交回】" not in body
    assert GATE_VERBS_RE.findall(body) == []
    assert "门领地纪律" not in body and "精确提交配方" not in body and "owner_confirmation_token" not in body
    sentence = f"报告写到 `{expected}`, 写完即止, 认领与裁决提交由驱动方完成。"
    assert f"- **报告落位**:{sentence}" in body
    assert f"## 交付纪律(AIPOS-F80 件①: 审计体零门)\n\n- {sentence}" in body
    # frontmatter 取证锚点亦同一落点, 且整卡(含 frontmatter)零门动词
    from tools.aipos_cli.queue_mutation import render_task_markdown

    card = render_task_markdown(spec["metadata"], body)
    assert GATE_VERBS_RE.findall(card) == [] and f"报告落点={expected}" in card
    print("\n----- F80 靶场派生审计卡(lybra 形, 全文) -----\n" + card)


def test_item1_manual_gate_mode_audit_card_keeps_section(tmp_path, monkeypatch):
    """验收②(回归): manual_gate_mode 项目(chris 形)派生审计卡仍保留「认领与交回」节 + 门领地纪律配方(同一判据)。"""
    gov = _make_gov(tmp_path, monkeypatch, shape="chris")
    spec = _derive(gov, "F80-C1")
    body = spec["body"]
    expected = str(gov / "5_tasks" / "records" / "audit_verdicts" / spec["audit_task_id"] / "RETURN.md")
    assert "## 【认领与交回】(审计体必读" in body
    assert "门领地纪律" in body and "精确提交配方" in body
    assert "lybra_audit_verdict_dry_run" in body and "lybra_queue_claim" in body
    assert f"`{expected}`" in body and "交付纪律(AIPOS-F80" not in body
    print("\n----- F80 靶场派生审计卡(chris 形 manual_gate_mode=true, 全文) -----\n" + body)


def test_item1_single_criterion_truth_table(tmp_path, monkeypatch):
    """唯一判据: 执行卡/审计卡零门(非 manual)、manual_gate_mode 两类都保留、非执行/审计角色保留;
    manual_gate_mode 读 paths 段(lybra 形)与顶层(chris 形)同一读取口 project_paths。"""
    gov_l = _make_gov(tmp_path, monkeypatch, shape="lybra")
    gov_c = _make_gov(tmp_path, monkeypatch, shape="chris")
    exec_card = {"assigned_to": "exec.lybra.host", "agent_instance": "exec.lybra.host"}
    audit_card = {"assigned_to": "audit_lybra", "agent_instance": "audit.lybra.host", "task_mode": "audit"}
    advisor_card = {"assigned_to": "advisor.lybra.host"}
    assert dw.card_carries_gate_contract_section(exec_card, gov_l) is False
    assert dw.card_carries_gate_contract_section(audit_card, gov_l) is False
    assert dw.card_carries_gate_contract_section(advisor_card, gov_l) is True
    assert dw.card_carries_gate_contract_section(exec_card, gov_c) is True
    assert dw.card_carries_gate_contract_section(audit_card, gov_c) is True
    # paths 段声明 true 同样生效(此前 _manual_gate_mode 只读顶层, 漏读 paths 段)
    decl = json.loads((gov_l / "project.json").read_text())
    decl["paths"]["manual_gate_mode"] = True
    (gov_l / "project.json").write_text(json.dumps(decl))
    assert dw.card_carries_gate_contract_section(audit_card, gov_l) is True
    # 发布追加函数走同一判据: 零门 → 原样返回; manual → 追加
    assert dw._append_gate_contract_section(gov_c, exec_card, "F80-X", "## Body\n").count("【认领与交回】") == 1
    decl["paths"]["manual_gate_mode"] = False
    (gov_l / "project.json").write_text(json.dumps(decl))
    assert dw._append_gate_contract_section(gov_l, exec_card, "F80-X", "## Body\n") == "## Body\n"


def _calls_by_function(path: Path, names: set[str]) -> dict[str, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                    if callee in names:
                        found.setdefault(callee, []).append(fn.name)
    return found


def test_item1_no_second_criterion_and_no_silent_swallow():
    """禁第二判据: _manual_gate_mode / _card_role_class 的「带不带节」用途只在 card_carries_gate_contract_section 内调用;
    audit_derivation 只调唯一判据; 两文件无 except Exception: pass。"""
    dw_calls = _calls_by_function(REPO_ROOT / "tools/aipos_cli/draft_writer.py", {"_manual_gate_mode", "_card_role_class", "card_carries_gate_contract_section"})
    assert set(dw_calls["_manual_gate_mode"]) == {"card_carries_gate_contract_section"}
    assert set(dw_calls["_card_role_class"]) == {"card_carries_gate_contract_section"}
    assert set(dw_calls["card_carries_gate_contract_section"]) >= {"_append_gate_contract_section", "publish_draft", "regen_machine_zone_for_pending"}
    ad_calls = _calls_by_function(REPO_ROOT / "tools/aipos_cli/audit_derivation.py", {"_manual_gate_mode", "_card_role_class", "card_carries_gate_contract_section"})
    assert "_manual_gate_mode" not in ad_calls and "_card_role_class" not in ad_calls
    assert ad_calls["card_carries_gate_contract_section"] == ["build_derived_audit_task"]
    # 本卡触及的函数内无「except Exception(或裸 except): pass」(fail-closed; 存量他函数不在本卡车道判据内)
    touched = {
        "tools/aipos_cli/audit_derivation.py": {"build_derived_audit_task", "build_zero_gate_delivery_section", "zero_gate_audit_body", "zero_gate_report_sentence"},
        "tools/aipos_cli/draft_writer.py": {"card_carries_gate_contract_section", "_manual_gate_mode", "_append_gate_contract_section"},
        "tools/aipos_cli/charter_render.py": {"charter_render_context", "render_charter", "_substitute"},
    }
    for rel, fns in touched.items():
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        seen = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in fns:
                seen.add(fn.name)
                for h in ast.walk(fn):
                    if isinstance(h, ast.ExceptHandler):
                        broad = h.type is None or (isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"))
                        assert not (broad and all(isinstance(b, ast.Pass) for b in h.body)), (rel, fn.name, h.lineno)
        assert seen == fns, (rel, fns - seen)


def test_item1_regen_strips_gate_sections_from_stored_audit_card(tmp_path, monkeypatch):
    """regen 入口(F73C 同款): 存量 pending 审计卡(旧形: 认领与交回 + 门领地纪律配方 + records 落位句)→ 零门收口;
    manual_gate_mode 项目同一张卡保留该节。dry_run 零写入。"""
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.queue_mutation import render_task_markdown

    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    decl = json.loads((gov / "project.json").read_text())
    decl["paths"]["manual_gate_mode"] = True  # 先按旧形(带节)派生出存量卡
    (gov / "project.json").write_text(json.dumps(decl))
    spec = _derive(gov, "F80-R1")
    assert "【认领与交回】" in spec["body"] and "门领地纪律" in spec["body"]
    card_path = gov / "5_tasks" / "queue" / "pending" / "f80-r1r.md"
    card_path.write_text(render_task_markdown(spec["metadata"], spec["body"]), encoding="utf-8")
    before = card_path.read_bytes()

    # manual 项目: 同一判据保留该节(不删)
    kept = dw.regen_machine_zone_for_pending(gov, REPO_ROOT, task_id="F80-R1R", actor="advisor.lybra.host", dry_run=True)
    for item in kept["data"]["amendments"]:
        assert "【认领与交回】" in item["amendments"].get("body", spec["body"])

    decl["paths"]["manual_gate_mode"] = False
    (gov / "project.json").write_text(json.dumps(decl))
    result = dw.regen_machine_zone_for_pending(gov, REPO_ROOT, task_id="F80-R1R", actor="advisor.lybra.host", dry_run=True)
    assert result["data"]["updated_cards"] == ["F80-R1R"], result
    new_body = result["data"]["amendments"][0]["amendments"]["body"]
    expected = str(gov / "task_cards" / "F80-R1R" / "RETURN.md")
    assert "【认领与交回】" not in new_body and "门领地纪律" not in new_body
    assert GATE_VERBS_RE.findall(new_body) == []
    assert f"- **报告落位**:报告写到 `{expected}`, 写完即止, 认领与裁决提交由驱动方完成。" in new_body
    assert "交付纪律(AIPOS-F80 件①: 审计体零门)" in new_body
    assert ad.zero_gate_audit_body(new_body, gov, "F80-R1R") == new_body  # 幂等
    assert card_path.read_bytes() == before  # dry_run 零写入
    _meta, _b, _w = parse_markdown_frontmatter(before.decode("utf-8"))
    assert _meta["task_id"] == "F80-R1R"


# ===========================================================================
# 件② 章程母本占位化
# ===========================================================================

def test_item2_masters_have_no_project_literals():
    """验收③: 三份母本 grep `~/projects/lybra|lybra\\.kiwiai|pol_lybra_|kiwiai-dev` 零命中;
    也无 /home/ 绝对路径、无旧默认落点 task_cards/<R卡号>、无门动词。README 为说明文档(同样零命中)。"""
    for role in ROLES:
        text = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert MASTER_LITERAL_RE.findall(text) == [], role
        assert "/home/" not in text and "~/ai-project-os" not in text and "127.0.0.1" not in text, role
        assert "task_cards/<R卡号>" not in text and "默认 `~/" not in text, role
        assert GATE_VERBS_RE.findall(text) == [], role
    readme = (REPO_ROOT / "agents" / "roles" / "README.md").read_text(encoding="utf-8")
    assert MASTER_LITERAL_RE.findall(readme) == []


def _identity(harness_root: Path, role: str, instance: str, project: str) -> dict:
    return {"harness_root": str(harness_root), "role": role, "instance": instance, "project": project,
            "owner_policy_ref": None, "governance_root_declared": None, "token_projects": [], "gate_url": None}


FOREIGN_TO_CHRIS = re.compile(r"/home/kiwi/projects/lybra|~/projects/lybra|lybra\.kiwiai|pol_lybra_|kiwiai-dev|2_projects/lybra|\b(?:exec|audit|advisor)\.lybra\.")


@pytest.mark.parametrize("shape", ["lybra", "chris"])
def test_item2_render_three_masters_two_contexts(tmp_path, monkeypatch, shape):
    """验收③: lybra 形 / chris 形两个渲染上下文各渲染三份: 无未解析占位、无对方项目字面、落点指治理根。"""
    gov = _make_gov(tmp_path, monkeypatch, shape=shape)
    other = _make_gov(tmp_path, monkeypatch, shape="chris" if shape == "lybra" else "lybra")
    project = "lybra" if shape == "lybra" else "chris-huibojin"
    machine = "hostl" if shape == "lybra" else "kiwi-mac"
    prefixes = {"executor": "exec", "auditor": "audit", "advisor": "advisor"}
    for role in ROLES:
        instance = f"{prefixes[role]}.{project}.{machine}"
        harness = tmp_path / f"pi-{shape}" / f"{project}-{role}"
        harness.mkdir(parents=True)
        ctx = charter_render_context(gov, identity=_identity(harness, role, instance, project), product_commit="deadbeef")
        assert ctx["machine"] == machine
        assert ctx["executor_instance"] == f"exec.{project}.{machine}" and ctx["auditor_instance"] == f"audit.{project}.{machine}"
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        rendered = render_charter(master, ctx)
        assert "{{" not in rendered and "}}" not in rendered, role
        assert str(other) not in rendered and str(tmp_path / f"product-{'chris' if shape == 'lybra' else 'lybra'}") not in rendered
        if shape == "chris":
            assert FOREIGN_TO_CHRIS.findall(rendered) == [], (role, FOREIGN_TO_CHRIS.findall(rendered))
        else:
            assert "chris-huibojin" not in rendered and "kiwi-mac" not in rendered
        assert str(gov) in rendered and f"`{project}` 项目的" in rendered
        if role == "executor":
            assert f"`{ctx['return_root']}/<卡号>/RETURN.md`" in rendered
            assert ctx["return_root"].startswith(str(gov))
        if role == "auditor":
            assert f"`{ctx['verdict_root']}/<审计卡号>/RETURN.md`" in rendered
            assert ctx["verdict_root"].startswith(str(gov))
            assert f"{ctx['code_repo']}/task_cards" not in rendered  # 根因: 报告落点不再指产品仓
        if role == "advisor":
            assert f"--auditor-instance {ctx['auditor_instance']}" in rendered
            assert f"--agent-or-role {ctx['executor_instance']}" in rendered
            assert f"--project {project} --machine {machine}" in rendered
        print(f"\n----- F80 {shape} 形渲染 {role}(母本段, 前 40 行) -----\n" + "\n".join(rendered.splitlines()[:40]))


def test_item2_undeclared_placeholder_rejected(tmp_path, monkeypatch):
    """占位键只用 charter_render_context 声明: 未声明占位 = 拒(fail-closed, 不渲染半成品)。"""
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    harness = tmp_path / "pi" / "lybra-executor"
    harness.mkdir(parents=True)
    ctx = charter_render_context(gov, identity=_identity(harness, "executor", "exec.lybra.hostl", "lybra"))
    with pytest.raises(ValueError, match="未声明占位"):
        render_charter("产品仓 {{product_repo_path}}\n", ctx)
    assert "token" not in json.dumps(ctx).lower()  # token 永不入上下文
