"""AIPOS-F32/F32B 回归夹具: 信封解析认自定义角色——按门注册表 class 匹配。

病根(顾问实撞): 旧信封解析模块的角色匹配只做 agent_or_role 点分量
对固定词 exec/audit 的直配 → 自定义角色信封(hbj-coder.chris-huibojin.kiwiai-dev)
永不匹配 → chris 发卡链 BLOCK "cannot resolve policy envelope"。

修法(与 F26C 分发类展开同一修法同一单源): 自定义角色分量经**门注册表**
(connection.json tokens, 与凭据同源; AIPOS-F32B 从 project.json 归位到此处)
解析所属内建类后匹配; 既有直配语义(exec↔exec)原样保留。

验收覆盖:
- ① chris 形工作区(夹具, 逐形拷贝真 chris-huibojin 信封): 自定义角色卡经
  bin/lybra draft validate + publish --dry-run 通过, 信封解析到
  pol_chris_coder_1(exec)/pol_chris_audit_1(audit);
- ② lybra 工作区内建角色零回归(exec.lybra.kiwiai-dev 直配照常, 活体值不变);
- ③ 注册表内改某自定义角色的 class → 匹配跟随(验完还原);
- ④ 单元级: 未注册分量不匹配 / 不越类 / legacy role 字段 / 空目录;
- ⑥ 与 run-all TS 夹具(tests/f32-custom-role-envelope.test.ts)同源场景。

AIPOS-F103 件④ 改写: 信封挑选只留一个判据 autonomy_policy.match_claim_envelope(唯一挑选 autonomy_policy.select_envelope,
与门同一判据)——信封 agent_or_role 精确覆盖实例/角色名/角色类才算覆盖; 旧「点分量 + 注册表 class 猜覆盖」的解析模块删除。
本文件锁的「自定义角色卡发卡链能解析出 chris 信封」照常成立(卡面实例 = 信封 agent_or_role); 依赖旧猜法的
注册表翻转/点分量/legacy role 字段/活体解析用例随模块删除, 改为下方唯一判据断言。

跑法: python3 -m pytest tests/test_aipos_f32_custom_role_envelope.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_LYBRA = REPO_ROOT / "bin" / "lybra"

# 真 chris-huibojin 工作区两份信封的逐形拷贝(2026-08-23 快照, 只留判定字段)
POLICY_CODER = """---
record_type: owner_autonomy_policy
policy_id: pol_chris_coder_1
mode: PreAuthorized
status: active
approved_by_owner: true
owner_approval_ref: dec_pol_chris_coder_1
active_from: '2026-08-23T00:00:00Z'
expires_at: '2099-09-30T00:00:00Z'
agent_or_role: hbj-coder.chris-huibojin.kiwiai-dev
task_selector_task_mode: code
task_selector_project: chris-huibojin
task_selector_task_ids: []
max_tasks: 30
---
# Owner Autonomy Policy: pol_chris_coder_1 (F32 fixture copy)
"""

POLICY_AUDIT = """---
record_type: owner_autonomy_policy
policy_id: pol_chris_audit_1
mode: PreAuthorized
status: active
approved_by_owner: true
owner_approval_ref: dec_pol_chris_audit_1
active_from: '2026-08-23T00:00:00Z'
expires_at: '2099-09-30T00:00:00Z'
agent_or_role: hbj-auditor.chris-huibojin.kiwiai-dev
task_selector_task_mode: audit
task_selector_project: chris-huibojin
task_selector_task_ids: []
max_tasks: 30
---
# Owner Autonomy Policy: pol_chris_audit_1 (F32 fixture copy)
"""

# 自定义角色执行卡草稿(F30 草稿同形, assigned_to 换成注册表内自定义角色)
DRAFT_CUSTOM_ROLE = """---
task_id: HBJ-F32-FX-1
title: HBJ-F32 夹具——自定义角色发卡链信封解析(经bin)
project: chris-huibojin
status: pending
assigned_to: hbj-coder.chris-huibojin.kiwiai-dev
agent_instance: hbj-coder.chris-huibojin.kiwiai-dev
context_bundle: hbj-coder.chris-huibojin.kiwiai-dev
task_mode: code
task_class: simple
priority: high
created_by: advisor.chris-huibojin.kiwiai-dev
needs_owner: false
output_target: tests/(夹具)
artifact_policy: formal_write
audit: required
audit_by: hbj-auditor.chris-huibojin.kiwiai-dev
claim_policy: assigned_agent_only
model_tier: default
task_type: one_shot
polling_mode: agent_polling
report_mode: separate_doc
---
# HBJ-F32 夹具(自定义角色发卡链)

一句话: chris 形工作区里自定义角色卡的信封应按注册表 class 解析, 而非点分量写死词。

## 目标

draft validate + publish --dry-run 全绿, 契约节信封 = pol_chris_coder_1。
"""

# 门注册表形态 = 真 chris 发卡链目标态(AIPOS-F32B: connection.json tokens,
# 与凭据同源; hbj-coder→executor, hbj-auditor→auditor, 逐形拷贝真条目字段面,
# token 全部合成——真凭据永不入夹具)。真拓扑: hbj 条目实际登记在 lybra 工作区
# 的凭据库, chris 工作区自己的 connection.json 只有运输凭证——夹具同构复刻。
GATE_REGISTRY_TOKENS = [
    # lybra 工作区凭据库(门中央库的一员): 内建 + 自定义角色条目
    {
        "agent_instance": "exec.fx.kiwiai-dev",
        "fingerprint": "sha256:fx0000000001",
        "projects": ["chris-fx"],
        "projects_enforced": True,
        "role": "hbj-coder",
        "role_class": "executor",
        "scopes": ["queue_claim", "queue_return", "task_progress"],
        "token": "fx-synthetic-token-hbj-coder-0000000001",
        "token_ref": "svc-hbj-coder",
    },
    {
        "agent_instance": "audit.fx.kiwiai-dev",
        "fingerprint": "sha256:fx0000000002",
        "projects": ["chris-fx"],
        "projects_enforced": True,
        "role": "hbj-auditor",
        "role_class": "auditor",
        "scopes": ["queue_claim", "audit_verdict", "task_progress"],
        "token": "fx-synthetic-token-hbj-auditor-0000002",
        "token_ref": "svc-hbj-auditor",
    },
]


def make_fixture_workspace(tmp_path: Path, *, custom_roles: dict | None = None) -> Path:
    """chris 形门拓扑夹具(AIPOS-F32B): home_root 下两工作区。

    - home/lybra-fx: hbj-* 实际登记处(门凭据库条目在 lybra 工作区, 真拓扑同构)
    - home/chris-fx: 发卡工作区——信封/草稿在此; 自身 connection.json 只有
      运输凭证(真 chris 工作区同构), project.json 无 custom_roles(空 {})。
    custom_roles 参数: 覆盖门注册表的自定义角色集({name: class}), 默认 hbj 双角色。
    """
    home = tmp_path / "gate-home"
    ws = home / "chris-fx"          # 发卡工作区(返回值)
    reg_ws = home / "lybra-fx"      # 注册表所在工作区
    for w in (ws, reg_ws):
        (w / "5_tasks" / "policies").mkdir(parents=True)
        (w / "5_tasks" / "drafts").mkdir(parents=True)
        (w / "5_tasks" / "queue").mkdir(parents=True)
        (w / ".lybra").mkdir()

    # chris-fx: project.json 无 custom_roles(顾问实测真 chris 工作区为空 {})
    (ws / "project.json").write_text(json.dumps({
        "code_repo": "/tmp/nonexistent/chris-fx",
        "config_version": 1,
        "project": "chris-fx",
        "manual_gate_mode": True,  # AIPOS-F73C: 人肉 gate 项目显式声明, 执行类卡面才保留「认领与交回」节
        "registered_at": "2026-08-10T00:00:00Z",
        "registered_by": "kiwi",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # chris-fx 自身 connection.json: 只有运输凭证(已过期) + mcp 骨架, 无自定义角色
    (ws / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1,
        "mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"},
        "tokens": [
            {
                "agent_instance": "enroll_fx0001",
                "expires_at": "2026-08-22T17:36:09Z",
                "fingerprint": "sha256:fx0000000003",
                "role": "enroll-transport",
                "scopes": [],
                "token": "fx-synthetic-token-enroll-0000003",
                "token_ref": "svc-enroll-transport",
            },
        ],
    }, indent=2) + "\n", encoding="utf-8")

    (ws / "5_tasks" / "policies" / "pol_chris_coder_1.md").write_text(POLICY_CODER, encoding="utf-8")
    (ws / "5_tasks" / "policies" / "pol_chris_audit_1.md").write_text(POLICY_AUDIT, encoding="utf-8")
    (ws / "5_tasks" / "drafts" / "hbj-f32-fx-1.md").write_text(DRAFT_CUSTOM_ROLE, encoding="utf-8")

    # lybra-fx: 门注册表(自定义角色真登记处)
    (reg_ws / "project.json").write_text(json.dumps({
        "code_repo": "/tmp/nonexistent/lybra-fx",
        "config_version": 1,
        "project": "lybra-fx",
        "registered_at": "2026-08-10T00:00:00Z",
        "registered_by": "kiwi",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry_tokens = list(GATE_REGISTRY_TOKENS)
    if custom_roles is not None:
        registry_tokens = [
            {
                "agent_instance": f"{name}.fx.kiwiai-dev",
                "fingerprint": f"sha256:fx00{name[:8]}",
                "projects": ["chris-fx"],
                "projects_enforced": True,
                "role": name,
                "role_class": entry["class"],
                "scopes": [],
                "token": f"fx-synthetic-token-{name}",
                "token_ref": f"svc-{name}",
            }
            for name, entry in custom_roles.items()
        ]
    (reg_ws / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1,
        "tokens": registry_tokens,
    }, indent=2) + "\n", encoding="utf-8")
    return ws


def _bin_lybra(*args: str, cwd: Path) -> dict:
    """经 bin/lybra 执行(铁律: 不绕 bin 直调模块), 返回 --json 解析结果。"""
    proc = subprocess.run(
        [str(BIN_LYBRA), *args, "--json"],
        cwd=str(cwd), capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"bin/lybra {' '.join(args)} 输出非 JSON:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )


# ── 验收①: chris 形工作区, 自定义角色卡经 bin 全链(dry-run 只读) ──────────────


class TestAcceptance1CustomRoleChainViaBin:
    def test_draft_validate_passes_for_custom_role_card(self, tmp_path):
        ws = make_fixture_workspace(tmp_path)
        result = _bin_lybra("draft", "validate", "--path", "5_tasks/drafts/hbj-f32-fx-1.md", cwd=ws)
        # 注: "Missing recommended field: recurrence" 是存量怪癖(schema 未定义该字段却推荐),
        # 任何草稿都 WARN 不阻断 —— 验收"通过" = 非 BLOCK。
        assert result.get("verdict") in ("PASS", "WARN"), result

    def test_draft_publish_dryrun_resolves_custom_role_envelope(self, tmp_path):
        """修复前在此 BLOCK: cannot resolve policy envelope(cannot resolve...)。"""
        ws = make_fixture_workspace(tmp_path)
        result = _bin_lybra("draft", "publish", "--path", "5_tasks/drafts/hbj-f32-fx-1.md", "--dry-run", cwd=ws)
        # 通过 = 非 BLOCK(would_write 真); recurrence 推荐字段存量怪 quirks 见上注。
        assert result.get("verdict") in ("PASS", "WARN"), result.get("blocking_reasons")
        assert result.get("blocking_reasons") == []
        assert result.get("would_write") is True
        rendered = str(result.get("rendered_markdown") or "")
        # 契约节信封解析到 pol_chris_coder_1(claim 与 return 双点)
        assert "pol_chris_coder_1" in rendered
        assert rendered.count("pol_chris_coder_1") >= 2
        # 不再撞墙
        assert "cannot resolve policy envelope" not in rendered
        assert not any(
            "cannot resolve policy envelope" in str(b)
            for b in result.get("blocking_reasons") or []
        )
        # dry-run 零写入
        assert result.get("wrote") is False
        assert not (ws / "5_tasks" / "queue" / "pending").exists() or not any(
            (ws / "5_tasks" / "queue" / "pending").iterdir()
        )

    def test_envelopes_resolve_by_card_instance_single_criterion(self, tmp_path):
        """AIPOS-F103 件④: 唯一挑选按实例身份精确覆盖(任务无关 = 信封自身 task_selector 为判定对象)。"""
        from tools.aipos_cli.autonomy_policy import select_envelope

        ws = make_fixture_workspace(tmp_path)
        coder, _ = select_envelope(ws, identities=[("hbj-coder.chris-huibojin.kiwiai-dev",) * 2 + (None,)])
        auditor, _ = select_envelope(ws, identities=[("hbj-auditor.chris-huibojin.kiwiai-dev",) * 2 + (None,)])
        assert coder["policy_id"] == "pol_chris_coder_1"
        assert auditor["policy_id"] == "pol_chris_audit_1"
        # 未被任何信封 agent_or_role 覆盖的身份(注册表角色名/实例)= 不匹配(零授权), 原因可读
        none, reasons = select_envelope(ws, identities=[("exec.fx.kiwiai-dev", "exec.fx.kiwiai-dev", "hbj-coder")])
        assert none is None and reasons and all("not covered by policy.agent_or_role" in r for r in reasons), reasons

    def test_audit_card_contract_section_follows_gate_subject_rule(self, tmp_path):
        """审计 R 卡契约节(auditor)信封 = 覆盖审计实例且覆盖被审卡(门 envelope_subject 规则, AIPOS-F90)的信封。
        chris 原样拷贝的审计信封 task_selector_task_mode=audit 不覆盖被审 code 卡 → 出声失败(不烤进门会拒的信封);
        补一张覆盖被审卡的审计信封 → 契约节带它。"""
        from tools.aipos_cli.gate_contract_section import render_gate_contract_section

        ws = make_fixture_workspace(tmp_path)
        reviewed = {
            "task_id": "HBJ-F32-FX-1", "task_mode": "code", "audit": "required", "project": "chris-huibojin",
            "assigned_to": "hbj-coder.chris-huibojin.kiwiai-dev", "agent_instance": "hbj-coder.chris-huibojin.kiwiai-dev",
            "audit_by": "hbj-auditor.chris-huibojin.kiwiai-dev",
        }
        kwargs = dict(role="auditor", gate_url="http://127.0.0.1:7999", connection_json_rel=".lybra/connection.json",
                      workspace_display=str(ws), task_id="HBJ-F32-FX-1R", workspace_root=ws)
        with pytest.raises(ValueError) as exc:
            render_gate_contract_section({}, reviewed, **kwargs)
        assert "audit_envelope" in str(exc.value) and "task_mode does not match" in str(exc.value)

        (ws / "5_tasks" / "policies" / "pol_chris_audit_2.md").write_text(
            POLICY_AUDIT.replace("pol_chris_audit_1", "pol_chris_audit_2").replace("task_selector_task_mode: audit", "task_selector_task_mode: code"),
            encoding="utf-8")
        section = render_gate_contract_section({}, reviewed, **kwargs)
        assert "pol_chris_audit_2" in section and "pol_chris_audit_1" not in section


# ── 验收③(AIPOS-F103 改写): 门注册表 class 不再参与信封挑选 ───────────────────


def _gate_registry_file(ws: Path) -> Path:
    """夹具门注册表文件(lybra-fx 工作区凭据库)。"""
    return ws.parent / "lybra-fx" / ".lybra" / "connection.json"


def _flip_registry_class(ws: Path, role: str, new_class: str) -> None:
    reg = _gate_registry_file(ws)
    data = json.loads(reg.read_text(encoding="utf-8"))
    for item in data.get("tokens", []):
        if isinstance(item, dict) and item.get("role") == role:
            item["role_class"] = new_class
    reg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class TestAcceptance3RegistryClassNotACriterion:
    def test_flip_registry_class_does_not_change_selection(self, tmp_path):
        """唯一判据 = 信封精确覆盖身份; 注册表 class 翻转不改挑选结果(禁第二判据)。"""
        from tools.aipos_cli.autonomy_policy import select_envelope

        ws = make_fixture_workspace(tmp_path)
        ident = [("hbj-coder.chris-huibojin.kiwiai-dev", "hbj-coder.chris-huibojin.kiwiai-dev", None)]
        assert select_envelope(ws, identities=ident)[0]["policy_id"] == "pol_chris_coder_1"
        _flip_registry_class(ws, "hbj-coder", "auditor")
        assert select_envelope(ws, identities=ident)[0]["policy_id"] == "pol_chris_coder_1"


# ── 验收②(AIPOS-F103 改写): 边界语义 ─────────────────────────────────────────


class TestAcceptance2Boundaries:
    def test_no_policies_dir_returns_none_with_reason(self, tmp_path):
        from tools.aipos_cli.autonomy_policy import select_envelope

        policy, reasons = select_envelope(tmp_path, identities=[("exec.x", "exec.x", None)])
        assert policy is None and reasons == ["5_tasks/policies/ 下没有任何信封"]

    def test_expired_policy_never_selected(self, tmp_path):
        from tools.aipos_cli.autonomy_policy import select_envelope

        ws = make_fixture_workspace(tmp_path)
        (ws / "5_tasks" / "policies" / "pol_chris_coder_1.md").write_text(
            POLICY_CODER.replace("2099-09-30T00:00:00Z", "2026-01-01T00:00:00Z"), encoding="utf-8")
        policy, reasons = select_envelope(ws, identities=[("hbj-coder.chris-huibojin.kiwiai-dev",) * 2 + (None,)])
        assert policy is None and any("expired" in r for r in reasons), reasons


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
