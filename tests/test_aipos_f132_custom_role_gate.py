"""AIPOS-F132 件①② 验收夹具 — 门侧角色判定改读角色类单源(custom_roles.authorize_instance_class)。

依据(10-08 chris 首张 loop 试点): 审计实例 hbj-auditor.chris-huibojin.kiwiai-dev 是门注册表自定义角色(hbj-auditor → class
auditor), 独立审计 PASS 后 loop ingest verdict 被门按实例名前缀 {audit, auditor} 拒(ROLE_VIOLATION)。

靶场 = 临时 home 下非 lybra 治理根(chris 形, 自造人肉期样本; 不读写任何真实治理根): 门注册表(<治理根>/.lybra/connection.json)
经产品命令 register_custom_role 登记 hbj-auditor(auditor) / hbj-coder(executor) / hbj-advisor(advisor)。
件① 裁决提交: 自定义审计类实例 dry-run 预览 + confirm 落裁决记录; 自定义执行体类 / 未注册前缀 / 内建执行体与顾问类被拒(原文);
     MCP 门动词 lybra_audit_verdict_dry_run(Supervised)同口径; 注册表读不出 = 拒(fail-closed)。
件② 返工节: 自定义顾问类实例追加返工节成功(落卡 + amendment 记录); 自定义执行体/审计类被拒。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROJECT = "chris-fx"
HBJ_AUDITOR = f"hbj-auditor.{PROJECT}.fxhost"
HBJ_CODER = f"hbj-coder.{PROJECT}.fxhost"
HBJ_ADVISOR = f"hbj-advisor.{PROJECT}.fxhost"
GHOST = f"ghost-auditor.{PROJECT}.fxhost"
REVIEWED = "HBJFX-1"
AUDIT = "HBJFX-1R"
RETURN_ID = f"return_{REVIEWED}_20261008_hbj-coder"
PUBLISH_ID = "publish_hbjfx-1r"
CLAIM_ID = f"claim_{AUDIT}_20261008_hbj-auditor"
SESSION_ID = f"session_{AUDIT}_20261008_hbj-auditor"
SUBJECT = {"repository": "/tmp/nonexistent/chris-fx-code", "commit_sha": "a" * 40, "tree_hash": "b" * 40}  # 被审卡 code 类必填(F70)


def _fm(meta: dict, body: str = "body\n") -> str:
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n" + body


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def gov(tmp_path: Path) -> Path:
    """临时 home 下 chris 形治理根 + 门注册表三条自定义角色(经产品登记命令写入)。"""
    from tools.aipos_cli.custom_roles import load_custom_roles, register_custom_role

    root = tmp_path / "home" / PROJECT
    for sub in ("queue/pending", "queue/claimed", "queue/completed", "queue/blocked", "records/publishes", "records/returns",
                "records/sessions", "records/claims", "records/audit_dispatches", "records/audit_verdicts",
                "records/amendments"):
        (root / "5_tasks" / sub).mkdir(parents=True, exist_ok=True)
    (root / ".lybra").mkdir(parents=True, exist_ok=True)
    _write(root / "project.json", json.dumps({"project": PROJECT}))
    for name, cls in (("hbj-auditor", "auditor"), ("hbj-coder", "executor"), ("hbj-advisor", "advisor")):
        register_custom_role(root, name, cls, by="dec_f132_fixture", reason="AIPOS-F132 靶场")
    registry = load_custom_roles(root)
    print("靶场门注册表(自定义角色 → 类):", json.dumps(registry, ensure_ascii=False))
    assert registry == {"hbj-auditor": {"class": "auditor"}, "hbj-coder": {"class": "executor"},
                        "hbj-advisor": {"class": "advisor"}}
    return root


def _reviewed_and_derived_audit(root: Path, audit_instance: str) -> None:
    _write(root / "5_tasks/queue/claimed/hbjfx-1.md", _fm({
        "task_id": REVIEWED, "title": "fx", "project": PROJECT, "assigned_to": HBJ_CODER, "agent_instance": HBJ_CODER,
        "context_bundle": "hbj", "task_mode": "code", "model_tier": "L2", "priority": "medium", "status": "claimed",
        "created_by": HBJ_ADVISOR, "needs_owner": "false", "output_target": "src/", "artifact_policy": "formal_write",
        "executor_completed_by": HBJ_CODER, "executor_registry_verified": "true", "return_record_ref": RETURN_ID,
        "audit_readiness": "ready", "dependency_executor_status": "completed",
        "claim_id": f"claim_{REVIEWED}_20261008_hbj-coder", "claimed_by": HBJ_CODER, "claimed_at": "2026-10-08T00:00:00Z",
        "active_session_id": f"session_{REVIEWED}_20261008_hbj-coder"}))
    _write(root / f"5_tasks/records/returns/{REVIEWED}/{RETURN_ID}.md", _fm({
        "record_type": "return_record", "event_type": "mcp_queue_return", "return_id": RETURN_ID, "task_id": REVIEWED,
        "surface": "mcp", "operation": "queue_return", "autonomy_mode": "Supervised", "actor": HBJ_CODER,
        "canonical_agent_instance": HBJ_CODER}))
    _write(root / "5_tasks/queue/claimed/hbjfx-1r.md", _fm({
        "task_id": AUDIT, "title": "audit fx", "project": PROJECT, "assigned_to": audit_instance,
        "agent_instance": audit_instance, "context_bundle": "hbj-audit", "task_mode": "audit", "task_class": "simple",
        "priority": "medium", "status": "claimed", "needs_owner": "false", "output_target": "src/",
        "artifact_policy": "formal_write", "created_by": "gate_derivation", "derived_from": REVIEWED,
        "reviewed_task_id": REVIEWED, "reviewed_task_path": "5_tasks/queue/claimed/hbjfx-1.md",
        "reviewed_return_record_ref": RETURN_ID, "claim_id": CLAIM_ID, "claimed_by": audit_instance,
        "claimed_at": "2026-10-08T01:00:00Z", "active_session_id": SESSION_ID, "audit": "none"}))
    _write(root / f"5_tasks/records/publishes/{AUDIT}/{PUBLISH_ID}.md", _fm({
        "record_type": "publish_record", "publish_id": PUBLISH_ID, "task_id": AUDIT, "actor": "gate_derivation",
        "published_task_ref": "5_tasks/queue/pending/hbjfx-1r.md"}))
    _write(root / f"5_tasks/records/sessions/{AUDIT}/{SESSION_ID}.md", _fm({
        "record_type": "session_record", "session_id": SESSION_ID, "task_id": AUDIT, "session_status": "active"}))


def _verdict(root: Path, instance: str, *, dry_run: bool = True) -> dict:
    from tools.aipos_cli.board_adapter import audit_verdict_task

    return audit_verdict_task(
        audit_task_id=AUDIT, reviewed_task_id=REVIEWED, actor=instance, agent_instance=instance,
        owner_policy_ref="pol_chris_fx_audit_1", audit_claim_id=CLAIM_ID, audit_session_id=SESSION_ID,
        reviewed_return_record_ref=RETURN_ID, verdict="PASS", findings_summary="独立审计通过(靶场)",
        artifact_subject=SUBJECT, repo_root=root, dry_run=dry_run)


def _show(label: str, resp: dict) -> None:
    print(f"{label}:", json.dumps({k: resp.get(k) for k in ("verdict", "ok", "message", "blocking_reasons")},
                                  ensure_ascii=False))


# ---------------------------------------------------------------------------
# 件① 裁决提交
# ---------------------------------------------------------------------------

def test_item1_custom_auditor_class_submits_verdict_preview_and_confirm(gov: Path):
    _reviewed_and_derived_audit(gov, HBJ_AUDITOR)
    preview = _verdict(gov, HBJ_AUDITOR)
    _show("件① hbj-auditor 裁决 dry-run", preview)
    assert preview["verdict"] != "BLOCK", preview.get("blocking_reasons")
    assert not any("ROLE_VIOLATION" in str(r) for r in preview.get("blocking_reasons") or [])
    assert preview["data"]["verdict"] == "PASS"
    landed = _verdict(gov, HBJ_AUDITOR, dry_run=False)
    _show("件① hbj-auditor 裁决 confirm", landed)
    assert landed["verdict"] != "BLOCK", landed.get("blocking_reasons")
    record = gov / str(landed["data"]["audit_verdict_record_path"])
    text = record.read_text(encoding="utf-8")
    print("件① 落盘裁决记录:", record.relative_to(gov))
    assert "record_type: audit_verdict_record" in text and HBJ_AUDITOR in text
    reviewed = (gov / "5_tasks/queue/claimed/hbjfx-1.md").read_text(encoding="utf-8")
    assert "audit_status: PASS" in reviewed


@pytest.mark.parametrize("instance,expect", [
    (HBJ_CODER, "角色类 'executor'"),                     # 自定义执行体类
    (GHOST, "角色类不可解析"),                              # 未注册前缀
    (f"exec.{PROJECT}.fxhost", "角色类 'executor'"),       # 内建执行体(防篡改语义不变)
    (f"advisor.{PROJECT}.fxhost", "角色类 'advisor'"),     # 内建顾问(防篡改语义不变)
    (HBJ_ADVISOR, "角色类 'advisor'"),                     # 自定义顾问类
])
def test_item1_non_auditor_class_verdict_refused_with_resolution(gov: Path, instance: str, expect: str):
    _reviewed_and_derived_audit(gov, HBJ_AUDITOR)
    resp = _verdict(gov, instance)
    _show(f"件① {instance} 裁决", resp)
    assert resp["verdict"] == "BLOCK"
    reason = " ".join(resp["blocking_reasons"])
    assert reason.startswith("ROLE_VIOLATION") and instance in reason and expect in reason, reason
    assert resp["data"]["role_resolution"]["ok"] is False
    assert not list((gov / "5_tasks/records/audit_verdicts").rglob("*.md"))  # 零写入


def test_item1_registry_unreadable_is_refused_fail_closed(gov: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import custom_roles

    def broken_loader():
        def _load(_home):
            raise OSError("permission denied (fx)")
        return _load

    _reviewed_and_derived_audit(gov, HBJ_AUDITOR)
    monkeypatch.setattr(custom_roles, "_gate_registry_loader", broken_loader)
    resp = _verdict(gov, HBJ_AUDITOR)
    _show("件① 注册表读不出", resp)
    assert resp["verdict"] == "BLOCK" and "门注册表读取失败" in " ".join(resp["blocking_reasons"])


def test_item1_mcp_gate_verb_supervised_accepts_custom_auditor(gov: Path, monkeypatch: pytest.MonkeyPatch):
    """门动词 lybra_audit_verdict_dry_run(Supervised, 持 audit_verdict scope 的 token)对自定义审计类实例出预览不拒;
    自定义执行体类同调用被门拒 ROLE_VIOLATION。"""
    from tools.mcp_server import tools as gate

    _reviewed_and_derived_audit(gov, HBJ_AUDITOR)
    monkeypatch.setattr(gate, "_capability_has_scope", lambda scope: True)

    def call(instance: str) -> dict:
        res = gate.lybra_audit_verdict_dry_run({
            "audit_task_id": AUDIT, "reviewed_task_id": REVIEWED, "actor": instance, "agent_instance": instance,
            "autonomy_mode": "Supervised", "owner_policy_ref": "pol_chris_fx_audit_1", "audit_claim_id": CLAIM_ID,
            "audit_session_id": SESSION_ID, "reviewed_return_record_ref": RETURN_ID, "verdict": "PASS",
            "findings_summary": "独立审计通过(靶场)", "artifact_subject": SUBJECT, "workspace_root": str(gov)})
        return json.loads(res["content"][0]["text"]) if "content" in res else res

    ok = call(HBJ_AUDITOR)
    print("件① MCP 门 hbj-auditor:", json.dumps({k: ok.get(k) for k in ("ok", "verdict", "blocking_reasons")}, ensure_ascii=False))
    assert ok.get("verdict") != "BLOCK" and "ROLE_VIOLATION" not in json.dumps(ok, ensure_ascii=False), ok
    denied = call(HBJ_CODER)
    print("件① MCP 门 hbj-coder:", json.dumps({k: denied.get(k) for k in ("ok", "verdict", "blocking_reasons")},
                                            ensure_ascii=False))
    assert "ROLE_VIOLATION" in json.dumps(denied, ensure_ascii=False), denied


# ---------------------------------------------------------------------------
# 件② 返工节
# ---------------------------------------------------------------------------

def _claimed_card(root: Path, task_id: str) -> None:
    from tools.aipos_cli.queue_mutation import render_task_markdown

    _write(root / "5_tasks/queue/claimed" / f"{task_id.lower()}.md", render_task_markdown({
        "task_id": task_id, "title": f"fx {task_id}", "project": PROJECT, "assigned_to": HBJ_CODER,
        "agent_instance": HBJ_CODER, "context_bundle": "hbj", "task_mode": "code", "task_class": "simple",
        "priority": "high", "status": "claimed", "created_by": HBJ_ADVISOR, "needs_owner": False,
        "output_target": "src/", "artifact_policy": "formal_write"}, "## Task body"))


def _rework(root: Path, task_id: str, instance: str, *, dry_run: bool) -> dict:
    from tools.aipos_cli.board_adapter import queue_rework_task

    return queue_rework_task(task_id=task_id, verdict_ref=f"verdict_{task_id}_fail", focus_items=["修 A", "补测 B"],
                             acceptance_criteria="全绿", actor=instance, agent_instance=instance,
                             owner_policy_ref="pol_chris_fx_loop_1", dry_run=dry_run, repo_root=root)


def test_item2_custom_advisor_class_appends_rework_round(gov: Path):
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    _claimed_card(gov, "HBJFX-2")
    preview = _rework(gov, "HBJFX-2", HBJ_ADVISOR, dry_run=True)
    _show("件② hbj-advisor 返工节 dry-run", preview)
    assert preview.get("verdict") != "BLOCK", preview.get("blocking_reasons")
    landed = _rework(gov, "HBJFX-2", HBJ_ADVISOR, dry_run=False)
    _show("件② hbj-advisor 返工节 confirm", landed)
    assert landed.get("ok") is True and landed["data"]["round_added"] == 1, landed
    meta, _, _ = parse_markdown_frontmatter((gov / "5_tasks/queue/claimed/hbjfx-2.md").read_text(encoding="utf-8"))
    print("件② 卡面 rework_rounds:", json.dumps(meta["rework_rounds"], ensure_ascii=False))
    assert meta["rework_rounds"][0]["focus_items"] == ["修 A", "补测 B"]
    assert list((gov / "5_tasks/records/amendments" / "HBJFX-2").glob("*.md"))


@pytest.mark.parametrize("instance,expect", [
    (HBJ_CODER, "角色类 'executor'"),
    (HBJ_AUDITOR, "角色类 'auditor'"),
    (GHOST, "角色类不可解析"),
])
def test_item2_non_advisor_class_rework_refused(gov: Path, instance: str, expect: str):
    _claimed_card(gov, "HBJFX-3")
    before = (gov / "5_tasks/queue/claimed/hbjfx-3.md").read_text(encoding="utf-8")
    resp = _rework(gov, "HBJFX-3", instance, dry_run=False)
    _show(f"件② {instance} 返工节", resp)
    assert resp["verdict"] == "BLOCK"
    reason = " ".join(resp["blocking_reasons"])
    assert reason.startswith("ROLE_VIOLATION") and instance in reason and expect in reason, reason
    assert (gov / "5_tasks/queue/claimed/hbjfx-3.md").read_text(encoding="utf-8") == before  # 零写入


def test_item2_required_class_read_from_card_schema_declaration():
    from tools.aipos_cli.board_adapter import restricted_amend_actor_class
    from tools.schema_loader import load_schema

    assert restricted_amend_actor_class() == load_schema("card")["restricted_amend"]["actor_role_class"]


# ---------------------------------------------------------------------------
# 单一实现: 卡角色类与门侧判定同一解析
# ---------------------------------------------------------------------------

def test_single_resolver_shared_by_card_role_class_and_gate(gov: Path):
    from tools.aipos_cli.custom_roles import authorize_instance_class, resolve_instance_role_class
    from tools.aipos_cli.draft_writer import _card_role_class

    cases = {HBJ_AUDITOR: ("hbj-auditor", "auditor"), HBJ_CODER: ("hbj-coder", "executor"),
             f"audit.{PROJECT}.h": ("auditor", "auditor"), "audit.test": ("auditor", "auditor"),
             f"exec.{PROJECT}.h": ("executor", "executor"), "advisor": ("advisor", "advisor")}
    for inst, want in cases.items():
        assert resolve_instance_role_class(inst, gov) == want, inst
        assert _card_role_class({"assigned_to": inst}, gov) == want[1], inst
    assert resolve_instance_role_class(GHOST, gov) is None
    bad = authorize_instance_class(HBJ_AUDITOR, "no-such-class", gov)
    assert bad["ok"] is False and "不是 roles.schema 内建角色类" in bad["reason"]
