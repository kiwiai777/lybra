"""AIPOS-F102 身份与角色类单源(族 B-b:H5/M4/M21)验收夹具。

件① 派审 actor = 驱动方实例(roles.schema driver.role_class); 审计卡认领实例 = 被审卡 audit_by(缺则按项目推导);
     修复卡执行实例承继被审卡声明(缺则按项目推导); 产品代码零 lybra 身份字面。靶场 = 非 lybra 项目(probe 形)。
件② 角色 → 角色类只经 custom_roles.resolve_role_to_class(统一失败语义 = 拒 UnknownRoleClass);
     角色类分组声明在 roles.schema(class_groups), 6 处写死元组改读声明。
件③ naming_profile.default_instance_name 缺 project 即拒(或读治理根 project.json#project), 无 "lybra" 缺省。
"""
from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT = "lybra-probe"
DRIVER = "advisor.lybra-probe.probehost"
EXEC = "exec.lybra-probe.probehost"
AUDITOR = "audit.lybra-probe.probehost"
HOST = socket.gethostname().split(".")[0]
LYBRA_IDENTITY = re.compile(r"\.lybra\.kiwiai-dev|kiwiai-dev")


def _fm(meta: dict, body: str = "# card\n") -> str:
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n" + body


def _probe_gov(tmp_path: Path, *, driver: bool = True, project_json: dict | None = None) -> Path:
    """非 lybra 项目治理根(probe 形): project.json#project=lybra-probe, 驱动方经 .lybra/role 声明。"""
    gov = tmp_path / "home" / PROJECT
    for sub in ("5_tasks/queue/pending", "5_tasks/queue/claimed", "5_tasks/queue/completed",
                "5_tasks/records/claims", "5_tasks/records/returns", "5_tasks/records/audit_dispatches",
                "5_tasks/records/audit_verdicts", "5_tasks/records/closures"):
        (gov / sub).mkdir(parents=True, exist_ok=True)
    (gov / "project.json").write_text(json.dumps(project_json if project_json is not None else {"project": PROJECT}),
                                      encoding="utf-8")
    if driver:
        (gov / ".lybra").mkdir(parents=True, exist_ok=True)
        (gov / ".lybra" / "role").write_text(json.dumps({"role": "advisor", "instance": DRIVER}), encoding="utf-8")
    return gov


def _returned_card(gov: Path, task_id: str, **extra) -> None:
    meta = {"task_id": task_id, "title": task_id, "project": PROJECT, "assigned_to": EXEC, "agent_instance": EXEC,
            "task_mode": "code", "audit": "required", "status": "claimed", "claimed_by": EXEC}
    meta.update(extra)
    meta = {k: v for k, v in meta.items() if v is not None}
    (gov / "5_tasks/queue/claimed" / f"{task_id.lower()}.md").write_text(_fm(meta), encoding="utf-8")
    rec = gov / "5_tasks/records/returns" / task_id
    rec.mkdir(parents=True, exist_ok=True)
    (rec / "return_1.md").write_text(_fm({
        "record_type": "return_record", "return_id": f"return_{task_id}_1", "task_id": task_id,
        "actor": EXEC, "agent_instance": EXEC, "executor_status": "completed",
        "returned_at": "2026-10-04T00:00:00Z", "recorded_by_gate": "true"}), encoding="utf-8")


# ---------------------------------------------------------------------------
# 件① 派审 / 审计认领 / 修复卡身份(非 lybra 项目靶场)
# ---------------------------------------------------------------------------

def test_item1_dispatch_actor_is_driver_and_audit_instance_is_card_audit_by(tmp_path, capsys):
    from tools.aipos_cli.next_resolver import derive_next_step

    gov = _probe_gov(tmp_path)
    _returned_card(gov, "PROBE-F102-1", audit_by=AUDITOR)
    res = derive_next_step("PROBE-F102-1", gov)
    print("靶场① 派审推导:", json.dumps({k: res[k] for k in ("derivable", "current_node", "command")}, ensure_ascii=False))
    assert res["derivable"] is True and res["current_node"] == "return", res
    cmd = res["command"]
    assert cmd.startswith("lybra audit dispatch --source-task-id PROBE-F102-1 ")
    assert f"--actor {DRIVER} --agent-instance {DRIVER} " in cmd, cmd
    assert f"--audit-agent-instance {AUDITOR}" in cmd, cmd
    assert not LYBRA_IDENTITY.search(cmd) and "owner-dispatch" not in cmd, cmd


def test_item1_audit_instance_without_audit_by_derived_from_card_project(tmp_path):
    from tools.aipos_cli.next_resolver import derive_next_step

    gov = _probe_gov(tmp_path)
    _returned_card(gov, "PROBE-F102-2")  # 无 audit_by
    res = derive_next_step("PROBE-F102-2", gov)
    print("靶场① 缺 audit_by:", res["command"])
    assert res["derivable"] is True, res
    assert f"--audit-agent-instance audit.{PROJECT}.{HOST}" in res["command"], res["command"]


def test_item1_no_driver_identity_is_not_derivable_fail_closed(tmp_path):
    from tools.aipos_cli.next_resolver import DRIVER_ACTOR_MISSING, derive_next_step

    gov = _probe_gov(tmp_path, driver=False)
    _returned_card(gov, "PROBE-F102-3", audit_by=AUDITOR)
    res = derive_next_step("PROBE-F102-3", gov)
    print("靶场① 无驱动方:", json.dumps({k: res[k] for k in ("derivable", "missing_records", "action")}, ensure_ascii=False))
    assert res["derivable"] is False and res["command"] == ""
    assert res["missing_records"] == [DRIVER_ACTOR_MISSING]
    assert res["action"] == {"type": "record_missing", "card": "PROBE-F102-3", "record": "driver_actor"}


def test_item1_no_audit_by_and_no_project_is_not_derivable(tmp_path):
    from tools.aipos_cli.next_resolver import derive_next_step

    gov = _probe_gov(tmp_path, project_json={})
    _returned_card(gov, "PROBE-F102-4", project=None)
    res = derive_next_step("PROBE-F102-4", gov)
    print("靶场① 无 audit_by 无项目:", res["missing_records"])
    assert res["derivable"] is False and res["action"]["record"] == "audit_by"
    assert "项目段无据" in res["missing_records"][0]


def test_item1_derived_audit_card_on_return_claims_as_audit_by(tmp_path):
    from tools.aipos_cli import audit_derivation as AD
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov = _probe_gov(tmp_path)
    _returned_card(gov, "PROBE-F102-5", audit_by=AUDITOR)
    rel = "5_tasks/queue/claimed/probe-f102-5.md"
    meta, _, _ = parse_markdown_frontmatter((gov / rel).read_text(encoding="utf-8"))
    r = AD.derive_audit_task_on_return(repo_root=gov, source_task_id="PROBE-F102-5", source_metadata=meta,
                                       source_path=rel, return_record_ref="return_PROBE-F102-5_1", artifact_refs=[])
    card = gov / "5_tasks/queue/pending/probe-f102-5r.md"
    am, _, _ = parse_markdown_frontmatter(card.read_text(encoding="utf-8"))
    print("靶场① 交回派生审计卡:", json.dumps({"derived": r.get("derived"), "agent_instance": am.get("agent_instance")}, ensure_ascii=False))
    assert r.get("derived") is True, r
    assert am["agent_instance"] == AUDITOR  # 审计卡认领实例 = 被审卡 audit_by(next_resolver 认领取卡面 agent_instance)
    assert not LYBRA_IDENTITY.search(card.read_text(encoding="utf-8"))


def test_item1_audit_by_equal_to_executor_refused(tmp_path):
    from tools.aipos_cli import audit_derivation as AD
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov = _probe_gov(tmp_path)
    _returned_card(gov, "PROBE-F102-6", audit_by=EXEC)
    rel = "5_tasks/queue/claimed/probe-f102-6.md"
    meta, _, _ = parse_markdown_frontmatter((gov / rel).read_text(encoding="utf-8"))
    r = AD.derive_audit_task_on_return(repo_root=gov, source_task_id="PROBE-F102-6", source_metadata=meta,
                                       source_path=rel, return_record_ref="return_PROBE-F102-6_1", artifact_refs=[])
    print("靶场① audit_by=执行实例:", r)
    assert r.get("derived") is False and "独立性不成立" in r["reason"]
    assert not (gov / "5_tasks/queue/pending/probe-f102-6r.md").exists()


def test_item1_repair_card_executor_inherits_or_derives_from_project(tmp_path):
    from tools.aipos_cli import audit_derivation as AD
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    gov = _probe_gov(tmp_path)
    _returned_card(gov, "PROBE-F102-7")
    r1 = AD.derive_repair_card_on_fail(governance_root=gov, reviewed_task_id="PROBE-F102-7", audit_task_id="PROBE-F102-7R",
                                       verdict_id="verdict_x", fail_reason="x", actor=DRIVER)
    m1, _, _ = parse_markdown_frontmatter((gov / r1["repair_task_path"]).read_text(encoding="utf-8"))
    # 被审卡缺 assigned_to/agent_instance → 按项目推导执行角色实例名
    _returned_card(gov, "PROBE-F102-8", assigned_to=None, agent_instance=None, claimed_by=None)
    r2 = AD.derive_repair_card_on_fail(governance_root=gov, reviewed_task_id="PROBE-F102-8", audit_task_id="PROBE-F102-8R",
                                       verdict_id="verdict_y", fail_reason="y", actor=DRIVER)
    m2, _, _ = parse_markdown_frontmatter((gov / r2["repair_task_path"]).read_text(encoding="utf-8"))
    print("靶场① 修复卡:", m1["assigned_to"], m1["agent_instance"], "|", m2["assigned_to"], m2["agent_instance"])
    assert (m1["assigned_to"], m1["agent_instance"]) == (EXEC, EXEC)
    assert (m2["assigned_to"], m2["agent_instance"]) == (f"exec.{PROJECT}.{HOST}",) * 2


def test_item1_no_lybra_identity_literals_in_touched_derivation_code():
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/aipos_cli/audit_derivation.py", "tools/aipos_cli/naming_profile.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        hits = [ln for ln in text.splitlines() if LYBRA_IDENTITY.search(ln)]
        assert hits == [], (rel, hits)
        assert "owner-dispatch.lybra" not in text and "executor_lybra" not in text


# ---------------------------------------------------------------------------
# 件② 角色类解析唯一 + 分组读声明
# ---------------------------------------------------------------------------

def test_item2_class_groups_declared_on_every_role_and_read_in_registry_order():
    from tools.aipos_cli.custom_roles import role_classes_in_group
    from tools.schema_loader import SchemaLoadError, load_schema

    roles = load_schema("roles")
    assert set(roles["class_groups"]["values"]) == {"workstation", "governance_seat"}
    assert all(isinstance(r.get("class_groups"), list) for r in roles["roles"])
    assert role_classes_in_group("workstation") == ("executor", "auditor")
    assert role_classes_in_group("governance_seat") == ("planner", "advisor")
    with pytest.raises(SchemaLoadError):
        role_classes_in_group("no-such-group")


def test_item2_unknown_role_rejected_uniformly_by_every_former_wrapper(tmp_path):
    from tools.aipos_cli.custom_roles import UnknownRoleClass, resolve_role_to_class
    from tools.aipos_cli.draft_writer import ContractSectionError, _card_role_class, card_carries_gate_contract_section
    from tools.aipos_cli.enroll_deliver import validate_workspace_root
    from tools.aipos_cli.workstation_wiring import resolve_role_class
    from tools.aipos_cli.write_boundary import WriteBoundaryError, resolve_role_class_for

    ws = tmp_path / "home" / "ws"
    ws.mkdir(parents=True)
    assert resolve_role_to_class("ghost-coder", ws) is None  # 非 required 调用方语义不变(None 由其自行出声)
    rejections = {}
    for name, call in {
        "resolve_role_to_class(required)": lambda: resolve_role_to_class("ghost-coder", ws, required=True),
        "enroll_deliver.validate_workspace_root": lambda: validate_workspace_root(str(ws), "ghost-coder"),
        "workstation_wiring.resolve_role_class": lambda: resolve_role_class("ghost-coder", None, project_root=ws),
        "workstation_wiring.resolve_role_class(token)": lambda: resolve_role_class("ghost-coder", {"role_class": "bogus"}),
        "draft_writer._card_role_class": lambda: _card_role_class({"assigned_to": "ghost-coder.lybra-probe.h"}, ws),
    }.items():
        with pytest.raises(UnknownRoleClass) as exc:
            call()
        rejections[name] = str(exc.value)
    with pytest.raises(WriteBoundaryError) as wb:
        resolve_role_class_for(ws, "ghost-coder")
    assert isinstance(wb.value.__cause__, UnknownRoleClass)
    with pytest.raises(ContractSectionError) as cs:
        card_carries_gate_contract_section({"assigned_to": "ghost-coder.lybra-probe.h"}, ws)
    assert isinstance(cs.value.__cause__, UnknownRoleClass)
    print("件② 统一拒:", json.dumps(rejections, ensure_ascii=False, indent=1))


def test_item2_custom_role_resolves_through_single_impl_into_declared_group(tmp_path):
    from tools.aipos_cli.custom_roles import register_custom_role, role_in_class_group
    from tools.aipos_cli.draft_writer import _card_role_class, card_carries_gate_contract_section
    from tools.aipos_cli.enroll_deliver import validate_workspace_root

    ws = tmp_path / "home" / "chris-fx"
    (ws / ".lybra").mkdir(parents=True)
    (ws / "5_tasks" / "queue").mkdir(parents=True)  # 门联合注册表只载已建项目(队列根 + project.json)
    (ws / "project.json").write_text(json.dumps({"project": "chris-fx"}), encoding="utf-8")
    register_custom_role(ws, "hbj-coder", "executor", by="dec_f102")
    register_custom_role(ws, "hbj-advisor", "advisor", by="dec_f102")
    assert role_in_class_group("hbj-coder", "workstation", ws) is True
    assert role_in_class_group("hbj-advisor", "governance_seat", ws) is True
    assert _card_role_class({"assigned_to": "hbj-coder.chris-fx.h"}, ws) == "executor"
    assert card_carries_gate_contract_section({"assigned_to": "hbj-coder.chris-fx.h"}, ws) is False  # 工位类 = 零门卡面
    gov_like = tmp_path / "home" / "gov-fx"
    (gov_like / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    validate_workspace_root(str(gov_like), "advisor")  # 治理席位类放行
    with pytest.raises(ValueError, match="cannot be governance repo"):
        validate_workspace_root(str(gov_like), "executor")


def test_item2_single_implementation_and_no_hardcoded_group_tuples():
    src_root = REPO_ROOT / "tools"
    group_tuple = re.compile(r"\(\s*\"(executor|auditor|planner|advisor)\"\s*,\s*\"(executor|auditor|planner|advisor)\"\s*\)")
    offenders, wrappers = [], []
    for sub in ("aipos_cli", "mcp_server"):
        for path in (src_root / sub).rglob("*.py"):
            if "/tests/" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if group_tuple.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
            if re.search(r"def (_get_role_class|_resolve_role_class_for_guard)\(", text):
                wrappers.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], offenders
    assert wrappers == [], wrappers
    cr = (src_root / "aipos_cli" / "custom_roles.py").read_text(encoding="utf-8")
    assert cr.count("def resolve_role_to_class(") == 1
    # enroll_deliver/enroll_client 的同机判定(is_same_host)各有一处存量 except Exception: pass, 属门地址族(B-a), 车道内不顺手改, 见 RETURN 缺口
    for rel in ("write_boundary.py", "draft_writer.py", "workstation_wiring.py",
                "charter_render.py", "onboarding.py", "custom_roles.py", "naming_profile.py", "audit_derivation.py", "machine_zone.py"):
        text = (src_root / "aipos_cli" / rel).read_text(encoding="utf-8")
        assert not re.search(r"except Exception:\s*\n\s*pass", text), rel


def test_item2_registry_file_malformed_refused_not_overwritten(tmp_path):
    from tools.aipos_cli.custom_roles import register_custom_role

    ws = tmp_path / "home" / "fx"
    (ws / ".lybra").mkdir(parents=True)
    bad = ws / ".lybra" / "connection.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="拒绝改写"):
        register_custom_role(ws, "hbj-coder", "executor", by="dec_f102")
    assert bad.read_text(encoding="utf-8") == "{not json"


# ---------------------------------------------------------------------------
# 件③ naming_profile 项目段: 缺即拒(或读治理根 project.json#project)
# ---------------------------------------------------------------------------

def test_item3_default_instance_name_requires_project(tmp_path, monkeypatch):
    from tools.aipos_cli import naming_profile as NP

    monkeypatch.setenv("LYBRA_PROJECT", "lybra")  # 原环境变量缺省退役: 设了也不生效
    assert not hasattr(NP, "DEFAULT_INSTANCE_PROJECT")
    with pytest.raises(NP.ProjectSegmentUnresolved, match="项目段无据"):
        NP.default_instance_name("audit")
    gov = _probe_gov(tmp_path)
    assert NP.default_instance_name("audit", project_root=gov, host="h") == f"audit.{PROJECT}.h"
    assert NP.default_instance_name("audit", project="explicit", project_root=gov, host="h") == "audit.explicit.h"
    empty = _probe_gov(tmp_path / "e", project_json={})
    with pytest.raises(NP.ProjectSegmentUnresolved, match="无 project 字段"):
        NP.default_instance_name("audit", project_root=empty)


# ---------------------------------------------------------------------------
# 验收④ 棘轮不变量 a: 本卡修掉的条目已从基线删除
# ---------------------------------------------------------------------------

REMOVED_BASELINE = {
    ("tools/aipos_cli/audit_derivation.py", "cd3e26d6e2cc"), ("tools/aipos_cli/audit_derivation.py", "84cbc9e9ca6a"),
    ("tools/aipos_cli/naming_profile.py", "74dffbf7c9fe"), ("tools/aipos_cli/next_resolver.py", "768ac5d530a2"),
    ("tools/aipos_cli/next_resolver.py", "bd53db24304b"), ("tools/aipos_cli/next_resolver.py", "860e4b4dc176"),
    ("tools/aipos_cli/token_resolver.py", "b27b342b6e92"),
}


def test_ratchet_invariant_a_entries_removed():
    base = json.loads((REPO_ROOT / "tests" / "f87_fragmentation_baseline.json").read_text(encoding="utf-8"))
    entries = {(e["file"], e["fp"]) for e in base["invariants"]["a"]["entries"]}
    assert not (REMOVED_BASELINE & entries)
    assert base["invariants"]["a"]["count"] == len(base["invariants"]["a"]["entries"])
    print(f"棘轮 a: 删除 {len(REMOVED_BASELINE)} 条, 现存 {base['invariants']['a']['count']} 条; total={base['total']}")
