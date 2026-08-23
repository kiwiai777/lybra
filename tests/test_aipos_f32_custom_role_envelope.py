"""AIPOS-F32 回归夹具: 信封解析认自定义角色——按 roles 注册表 class 匹配。

病根(顾问实撞): policy_resolver._policy_matches_role 只做 agent_or_role 点分量
对固定词 exec/audit 的直配 → 自定义角色信封(hbj-coder.chris-huibojin.kiwiai-dev)
永不匹配 → chris 发卡链 BLOCK "cannot resolve policy envelope"。

修法(与 F26C 分发类展开同一修法同一单源): 自定义角色分量经工作区 roles 注册表
(project.json custom_roles, 单源=tools/aipos_cli/custom_roles.load_custom_roles)
解析所属内建类后匹配; 既有直配语义(exec↔exec)原样保留。

验收覆盖:
- ① chris 形工作区(夹具, 逐形拷贝真 chris-huibojin 信封): 自定义角色卡经
  bin/lybra draft validate + publish --dry-run 通过, 信封解析到
  pol_chris_coder_1(exec)/pol_chris_audit_1(audit);
- ② lybra 工作区内建角色零回归(exec.lybra.kiwiai-dev 直配照常, 活体值不变);
- ③ 注册表内改某自定义角色的 class → 匹配跟随(验完还原);
- ④ 单元级: 未注册分量不匹配 / 不越类 / legacy role 字段 / 空目录;
- ⑥ 与 run-all TS 夹具(tests/f32-custom-role-envelope.test.ts)同源场景。

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

# 注册表形态 = 真 chris 发卡链目标态(hbj-coder→executor, hbj-auditor→auditor;
# 与 lybra 工作区 project.json 现存登记同形)
PROJECT_JSON_WITH_CUSTOM_ROLES = {
    "code_repo": "/tmp/nonexistent/chris-huibojin",
    "config_version": 1,
    "project": "chris-huibojin",
    "registered_at": "2026-08-10T00:00:00Z",
    "registered_by": "kiwi",
    "custom_roles": {
        "hbj-auditor": {"class": "auditor"},
        "hbj-coder": {"class": "executor"},
    },
}


def make_fixture_workspace(tmp_path: Path, *, custom_roles: dict | None = None) -> Path:
    """chris 形工作区夹具: project.json(自定义角色注册表)+两份信封+草稿+连接骨架。"""
    ws = tmp_path / "chris-huibojin-fx"
    (ws / "5_tasks" / "policies").mkdir(parents=True)
    (ws / "5_tasks" / "drafts").mkdir(parents=True)
    (ws / "5_tasks" / "queue").mkdir(parents=True)
    (ws / ".lybra").mkdir()

    project = dict(PROJECT_JSON_WITH_CUSTOM_ROLES)
    if custom_roles is not None:
        project["custom_roles"] = custom_roles
    (ws / "project.json").write_text(json.dumps(project, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    (ws / "5_tasks" / "policies" / "pol_chris_coder_1.md").write_text(POLICY_CODER, encoding="utf-8")
    (ws / "5_tasks" / "policies" / "pol_chris_audit_1.md").write_text(POLICY_AUDIT, encoding="utf-8")
    (ws / "5_tasks" / "drafts" / "hbj-f32-fx-1.md").write_text(DRAFT_CUSTOM_ROLE, encoding="utf-8")

    # 连接骨架: 只需 mcp.rpc_url 形状(信封解析不连门)
    (ws / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1,
        "mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"},
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

    def test_audit_envelope_resolves_to_pol_chris_audit_1(self, tmp_path):
        """审计侧信封(audit 类)同样按注册表 class 解析。"""
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_fixture_workspace(tmp_path)
        assert find_active_policy(ws, role="audit", policy_type="audit") == "pol_chris_audit_1"
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

    def test_audit_card_contract_section_carries_pol_chris_audit_1(self, tmp_path):
        """审计 R 卡契约节(auditor 角色)带 pol_chris_audit_1 信封。"""
        from tools.aipos_cli.gate_contract_section import render_gate_contract_section

        ws = make_fixture_workspace(tmp_path)
        section = render_gate_contract_section(
            {}, {"task_mode": "code", "audit": "required"}, role="auditor",
            gate_url="http://127.0.0.1:7999", connection_json_rel=".lybra/connection.json",
            workspace_display=str(ws), task_id="HBJ-F32-FX-1R",
            workspace_root=ws,
        )
        assert "pol_chris_audit_1" in section


# ── 验收③: 注册表改 class → 匹配跟随(验完还原) ───────────────────────────────


class TestAcceptance3RegistryClassFlip:
    def test_flip_hbj_auditor_class_and_back(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_fixture_workspace(tmp_path)
        project_json = ws / "project.json"

        # 基态: audit 类信封可解析
        assert find_active_policy(ws, role="audit", policy_type="audit") == "pol_chris_audit_1"

        # 翻转: hbj-auditor 改挂 executor → audit 类无信封可解析(匹配跟随注册表)
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["custom_roles"]["hbj-auditor"]["class"] = "executor"
        project_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert find_active_policy(ws, role="audit", policy_type="audit") is None

        # 还原 → 恢复
        data["custom_roles"]["hbj-auditor"]["class"] = "auditor"
        project_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert find_active_policy(ws, role="audit", policy_type="audit") == "pol_chris_audit_1"

    def test_flip_hbj_coder_class_and_back(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_fixture_workspace(tmp_path)
        project_json = ws / "project.json"

        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

        # 翻转: hbj-coder 改挂 auditor → exec 类无信封可解析
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["custom_roles"]["hbj-coder"]["class"] = "auditor"
        project_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert find_active_policy(ws, role="exec", policy_type="dev") is None

        # 还原 → 恢复
        data["custom_roles"]["hbj-coder"]["class"] = "executor"
        project_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"


# ── 验收②: 内建角色零回归 + 边界语义 ──────────────────────────────────────────


class TestAcceptance2BuiltinZeroRegression:
    def test_direct_component_match_preserved(self):
        from tools.aipos_cli.policy_resolver import _policy_matches_role

        # 既有直配语义: exec.lybra.kiwiai-dev ↔ exec(无注册表也匹配)
        assert _policy_matches_role({"agent_or_role": "exec.lybra.kiwiai-dev"}, "exec") is True
        assert _policy_matches_role({"agent_or_role": "audit.lybra.kiwiai-dev"}, "audit") is True
        assert _policy_matches_role({"agent_or_role": "audit.lybra.kiwiai-dev"}, "exec") is False

    def test_legacy_role_field_fallback_preserved(self):
        from tools.aipos_cli.policy_resolver import _policy_matches_role

        assert _policy_matches_role({"role": "exec"}, "exec") is True
        assert _policy_matches_role({"role": "exec"}, "audit") is False

    def test_live_lybra_workspace_resolution_unchanged(self):
        """活体回归锚: 真 lybra 工作区解析值与修复前一致(直配优先, 不受注册表影响)。"""
        from tools.aipos_cli.policy_resolver import find_active_policy

        governance_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
        if not (governance_root / "5_tasks" / "policies").exists():
            pytest.skip("真实 lybra 治理仓不存在, 跳过活体回归")
        assert find_active_policy(governance_root, role="exec", policy_type="dev") == "pol_lybra_dev_9"
        assert find_active_policy(governance_root, role="audit", policy_type="audit") == "pol_lybra_audit_6"

    def test_no_policies_dir_returns_none(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        assert find_active_policy(tmp_path, role="exec") is None


class TestCustomRoleMatchingBoundaries:
    """单元级边界: 未注册分量不匹配 / 不越类 / 自定义角色名裸用也匹配。"""

    REGISTRY = {
        "hbj-coder": {"class": "executor"},
        "hbj-auditor": {"class": "auditor"},
    }

    def test_unregistered_component_never_matches(self):
        from tools.aipos_cli.policy_resolver import _policy_matches_role

        # 实例名/项目名分量不在注册表 → 不匹配(防提权: 注册表外零授权)
        assert _policy_matches_role(
            {"agent_or_role": "chris-huibojin.kiwiai-dev"}, "exec", custom_roles=self.REGISTRY
        ) is False
        assert _policy_matches_role(
            {"agent_or_role": "kiwiai-dev"}, "exec", custom_roles=self.REGISTRY
        ) is False

    def test_custom_role_matches_only_its_class(self):
        from tools.aipos_cli.policy_resolver import _policy_matches_role

        assert _policy_matches_role(
            {"agent_or_role": "hbj-coder.chris-huibojin.kiwiai-dev"}, "exec", custom_roles=self.REGISTRY
        ) is True
        assert _policy_matches_role(
            {"agent_or_role": "hbj-coder.chris-huibojin.kiwiai-dev"}, "audit", custom_roles=self.REGISTRY
        ) is False
        assert _policy_matches_role(
            {"agent_or_role": "hbj-auditor.chris-huibojin.kiwiai-dev"}, "audit", custom_roles=self.REGISTRY
        ) is True

    def test_bare_custom_role_name_matches(self):
        from tools.aipos_cli.policy_resolver import _policy_matches_role

        # agent_or_role 裸用自定义角色名(无点分量)也按注册表类匹配
        assert _policy_matches_role(
            {"agent_or_role": "hbj-coder"}, "exec", custom_roles=self.REGISTRY
        ) is True

    def test_without_registry_behavior_is_legacy(self):
        from tools.aipos_cli.policy_resolver import _policy_matches_role

        # custom_roles=None / {} → 与旧版完全一致(不匹配自定义角色分量)
        assert _policy_matches_role(
            {"agent_or_role": "hbj-coder.chris-huibojin.kiwiai-dev"}, "exec"
        ) is False
        assert _policy_matches_role(
            {"agent_or_role": "hbj-coder.chris-huibojin.kiwiai-dev"}, "exec", custom_roles={}
        ) is False

    def test_builtin_class_candidates_derived_from_registry(self):
        """类候选来自 roles 注册表单源(schema), 非本文件写死。"""
        from tools.aipos_cli.policy_resolver import _builtin_class_candidates

        assert _builtin_class_candidates("exec") == {"executor"}
        assert _builtin_class_candidates("audit") == {"auditor"}
        assert _builtin_class_candidates("executor") == {"executor"}
        assert _builtin_class_candidates("") == set()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
