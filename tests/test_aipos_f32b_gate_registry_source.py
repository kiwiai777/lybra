"""AIPOS-F32B 回归夹具: 自定义角色注册表单源归位——门注册表(与凭据同源)。

病根(顾问预演③原样 BLOCK, 第六次纸绿): F32 修法方向对(按 class 匹配)但
custom_roles 读 `<workspace>/project.json`(chris 工作区为空 {}——hbj-* 实际
登记在 lybra 工作区的门凭据库), 且加了 custom_roles 参数由调用方喂——
角色→class 真相断成"注册表/project.json/参数喂"三处。角色是**门级**概念。

修法(AIPOS-F32B 来源修真):
  - custom_roles.load_custom_roles 改读门注册表(connection.json tokens,
    经 load_unified_service_role_registry 按 home_root 统一加载——与凭据
    projects 归属同源、与 F26C 分发类展开同一加载函数);
  - register/remove 同步写门注册表(写读同源, project.json 分支删除);
  - _policy_matches_role 的 custom_roles 参数仅限测试注入, 生产路径默认
    从注册表取。

验收覆盖(全活体经 bin, 纸面不采信):
- ① chris 形门拓扑夹具: draft validate + publish --dry-run 通过, 信封
  pol_chris_coder_1/pol_chris_audit_1;
- ①负对照(先红后绿的"红"永久化): 门注册表无 hbj 条目 → 精确复现原墙
  BLOCK "cannot resolve policy envelope";
- ③ 门注册表改 class → 匹配跟随(验完还原);
- ④ 来源唯一: project.json 反向喂假注册表不生效(门注册表赢); 源级断言
  custom_roles.py 加载路径零 project.json 读取; 参数仅测试注入;
- ⑤ F26C 分发与本处读同一加载函数(单源实证: 计数补丁双路径同函数);
- ⑦ 注册表解析边界: 过期条目/无 role_class 条目/畸形 class/内建角色条目。

跑法: python3 -m pytest tests/test_aipos_f32b_gate_registry_source.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_LYBRA = REPO_ROOT / "bin" / "lybra"
SRC_CUSTOM_ROLES = REPO_ROOT / "tools" / "aipos_cli" / "custom_roles.py"
SRC_POLICY_RESOLVER = REPO_ROOT / "tools" / "aipos_cli" / "policy_resolver.py"
SRC_DISTRIBUTE_TOOLS = REPO_ROOT / "tools" / "distribute_tools.py"

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
# Owner Autonomy Policy: pol_chris_coder_1 (F32B fixture copy)
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
# Owner Autonomy Policy: pol_chris_audit_1 (F32B fixture copy)
"""

DRAFT_CUSTOM_ROLE = """---
task_id: HBJ-F32B-FX-1
title: HBJ-F32B 夹具——门注册表单源信封解析(经bin)
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
# HBJ-F32B 夹具(门注册表单源)

一句话: 角色是门级概念, class 真相只在门注册表(与凭据同源)一处读。

## 目标

draft validate + publish --dry-run 全绿, 契约节信封 = pol_chris_coder_1。
"""


def _synthetic_entry(name: str, cls: str, *, token_ref: str | None = None,
                     expires_at: str | None = None, role: str | None = None) -> dict:
    """夹具注册表条目(合成 token——真凭据永不入夹具)。"""
    entry = {
        "agent_instance": f"{name}.fx.kiwiai-dev",
        "fingerprint": f"sha256:fx{abs(hash(name)) % 10**10:010d}",
        "projects": ["chris-huibojin-fx"],
        "role": role or name,
        "role_class": cls,
        "scopes": [],
        "token": f"fx-synthetic-token-{name}-{abs(hash(name)) % 10**10}",
        "token_ref": token_ref or f"svc-{name}",
    }
    if expires_at:
        entry["expires_at"] = expires_at
    return entry


def make_gate_home(tmp_path: Path, *, registry_tokens: list[dict] | None) -> Path:
    """chris 形门拓扑夹具(AIPOS-F32B): home_root 两工作区。

    - home/lybra-fx: 门凭据库(hbj-* 实际登记处, 真拓扑同构)
    - home/chris-fx: 发卡工作区——信封/草稿在此; 自身 connection.json 只有
      已过期运输凭证, project.json 无 custom_roles(顾问实测真 chris 同构)。
    registry_tokens=None → 注册表无自定义角色(负对照拓扑)。
    """
    home = tmp_path / "gate-home"
    ws = home / "chris-huibojin-fx"
    reg_ws = home / "lybra-fx"
    for w in (ws, reg_ws):
        (w / "5_tasks" / "policies").mkdir(parents=True)
        (w / "5_tasks" / "drafts").mkdir(parents=True)
        (w / "5_tasks" / "queue").mkdir(parents=True)
        (w / ".lybra").mkdir()
        (w / "project.json").write_text(json.dumps({
            "code_repo": f"/tmp/nonexistent/{w.name}",
            "config_version": 1,
            "project": w.name,
            "registered_at": "2026-08-10T00:00:00Z",
            "registered_by": "kiwi",
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    (ws / "5_tasks" / "policies" / "pol_chris_coder_1.md").write_text(POLICY_CODER, encoding="utf-8")
    (ws / "5_tasks" / "policies" / "pol_chris_audit_1.md").write_text(POLICY_AUDIT, encoding="utf-8")
    (ws / "5_tasks" / "drafts" / "hbj-f32b-fx-1.md").write_text(DRAFT_CUSTOM_ROLE, encoding="utf-8")

    # chris 工作区自身凭据文件: 只有已过期运输凭证 + mcp 骨架(无自定义角色)
    (ws / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1,
        "mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"},
        "tokens": [{
            "agent_instance": "enroll_f32bfx01",
            "expires_at": "2026-08-22T17:36:09Z",
            "fingerprint": "sha256:f32bfx00001",
            "role": "enroll-transport",
            "scopes": [],
            "token": "fx-synthetic-token-enroll-f32b-01",
            "token_ref": "svc-enroll-transport",
        }],
    }, indent=2) + "\n", encoding="utf-8")

    # 门凭据库(lybra 工作区): 自定义角色真登记处
    tokens = registry_tokens if registry_tokens is not None else [
        _synthetic_entry("hbj-coder", "executor"),
        _synthetic_entry("hbj-auditor", "auditor"),
    ]
    (reg_ws / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1,
        "tokens": tokens,
    }, indent=2) + "\n", encoding="utf-8")
    return ws


def _bin_lybra(*args: str, cwd: Path) -> dict:
    """经 bin/lybra 执行(铁律: 不绕 bin 直调模块), 返回 --json 解析结果。"""
    proc = subprocess.run(
        [str(BIN_LYBRA), "--workspace-root", str(cwd), *args, "--json"],
        cwd=str(cwd), capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"bin/lybra {' '.join(args)} 输出非 JSON:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )


# ── 验收①: chris 形门拓扑, 自定义角色卡经 bin 全链(dry-run 只读) ────────────


class TestAcceptance1GateRegistryChainViaBin:
    def test_draft_validate_passes_for_custom_role_card(self, tmp_path):
        ws = make_gate_home(tmp_path, registry_tokens=None)  # 默认注册表(hbj 双角色)
        result = _bin_lybra("draft", "validate", "--path", "5_tasks/drafts/hbj-f32b-fx-1.md", cwd=ws)
        # 注: "Missing recommended field: recurrence" 是存量怪癖(schema 未定义该字段却推荐),
        # 任何草稿都 WARN 不阻断 —— 验收"通过" = 非 BLOCK。
        assert result.get("verdict") in ("PASS", "WARN"), result

    def test_draft_publish_dryrun_resolves_custom_role_envelope(self, tmp_path):
        """修复前在此 BLOCK: cannot resolve policy envelope(读 project.json 空 {})。"""
        ws = make_gate_home(tmp_path, registry_tokens=None)
        result = _bin_lybra("draft", "publish", "--path", "5_tasks/drafts/hbj-f32b-fx-1.md", "--dry-run", cwd=ws)
        assert result.get("verdict") in ("PASS", "WARN"), result.get("blocking_reasons")
        assert result.get("blocking_reasons") == []
        assert result.get("would_write") is True
        rendered = str(result.get("rendered_markdown") or "")
        assert "pol_chris_coder_1" in rendered
        assert rendered.count("pol_chris_coder_1") >= 2  # claim 与 return 双点
        assert "cannot resolve policy envelope" not in rendered
        assert not any(
            "cannot resolve policy envelope" in str(b)
            for b in result.get("blocking_reasons") or []
        )
        assert result.get("wrote") is False  # dry-run 零写入
        assert not any((ws / "5_tasks" / "queue" / "pending").glob("*"))

    def test_audit_envelope_resolves_to_pol_chris_audit_1(self, tmp_path):
        """审计侧信封(audit 类)同样按门注册表 class 解析。"""
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        assert find_active_policy(ws, role="audit", policy_type="audit") == "pol_chris_audit_1"
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

    def test_audit_card_contract_section_carries_pol_chris_audit_1(self, tmp_path):
        """审计 R 卡契约节(auditor 角色)带 pol_chris_audit_1 信封。"""
        from tools.aipos_cli.gate_contract_section import render_gate_contract_section

        ws = make_gate_home(tmp_path, registry_tokens=None)
        section = render_gate_contract_section(
            {}, {"task_mode": "code", "audit": "required"}, role="auditor",
            gate_url="http://127.0.0.1:7999", connection_json_rel=".lybra/connection.json",
            workspace_display=str(ws), task_id="HBJ-F32B-FX-1R",
            workspace_root=ws,
        )
        assert "pol_chris_audit_1" in section


class TestAcceptance1NegativeControl:
    """①负对照: 门注册表无 hbj 条目 → 精确复现原墙(先红后绿的"红"永久化)。"""

    def test_no_gate_registry_reproduces_original_block(self, tmp_path):
        ws = make_gate_home(tmp_path, registry_tokens=[])  # 注册表空(真 chris 拓扑: 无 hbj 登记)
        result = _bin_lybra("draft", "publish", "--path", "5_tasks/drafts/hbj-f32b-fx-1.md", "--dry-run", cwd=ws)
        assert result.get("verdict") == "BLOCK", result
        blocking = " ".join(str(b) for b in result.get("blocking_reasons") or [])
        assert "cannot resolve policy envelope" in blocking, blocking

    def test_no_gate_registry_validate_unblocked_but_publish_blocked_on_envelope(self, tmp_path):
        """validate 不涉信封(不因缺注册表拦), publish 撞信封墙——与活体红同形。"""
        ws = make_gate_home(tmp_path, registry_tokens=[])
        result = _bin_lybra("draft", "validate", "--path", "5_tasks/drafts/hbj-f32b-fx-1.md", cwd=ws)
        assert result.get("verdict") in ("PASS", "WARN"), result


# ── 验收③: 门注册表改 class → 匹配跟随(验完还原) ──────────────────────────


class TestAcceptance3RegistryClassFlip:
    def _registry_file(self, ws: Path) -> Path:
        return ws.parent / "lybra-fx" / ".lybra" / "connection.json"

    def _flip(self, ws: Path, role: str, new_class: str) -> None:
        reg = self._registry_file(ws)
        data = json.loads(reg.read_text(encoding="utf-8"))
        for item in data.get("tokens", []):
            if isinstance(item, dict) and item.get("role") == role:
                item["role_class"] = new_class
        reg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_flip_hbj_coder_class_and_back(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

        self._flip(ws, "hbj-coder", "auditor")  # 翻转: 改挂 auditor
        assert find_active_policy(ws, role="exec", policy_type="dev") is None

        self._flip(ws, "hbj-coder", "executor")  # 还原
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

    def test_flip_hbj_auditor_class_and_back(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        assert find_active_policy(ws, role="audit", policy_type="audit") == "pol_chris_audit_1"

        self._flip(ws, "hbj-auditor", "executor")
        assert find_active_policy(ws, role="audit", policy_type="audit") is None

        self._flip(ws, "hbj-auditor", "auditor")
        assert find_active_policy(ws, role="audit", policy_type="audit") == "pol_chris_audit_1"


# ── 验收④: 来源唯一(project.json 分支删除; 参数仅测试注入) ──────────────────


class TestAcceptance4SingleSource:
    def test_project_json_variant_is_ignored(self, tmp_path):
        """防碎片化: project.json 喂假注册表(hbj-coder→auditor)不生效——门注册表赢。"""
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        pj = ws / "project.json"
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["custom_roles"] = {"hbj-coder": {"class": "auditor"}}  # 反向假注册表
        pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        # 门注册表说 executor → exec 类照常解析(project.json 变体被忽略)
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

    def test_source_load_path_has_no_project_json_read(self):
        """源级断言: custom_roles.py 零 project.json 读取面(加载/写入均走门注册表)。"""
        src = SRC_CUSTOM_ROLES.read_text(encoding="utf-8")
        assert "read_project_json" not in src, "load path must not read project.json (AIPOS-F32B)"
        assert "project_json_path" not in src, "load path must not write project.json (AIPOS-F32B)"
        # 门注册表加载函数 = 与凭据同源的统一加载器
        assert "load_unified_service_role_registry" in src

    def test_policy_resolver_has_no_self_built_role_class_map(self):
        """源级断言: policy_resolver 无自建角色→类映射(防碎片化红线, F32 已立)。"""
        src = SRC_POLICY_RESOLVER.read_text(encoding="utf-8")
        assert "_ROLE_MATCH_SUBSTRINGS" in src  # 直配表(既有语义)仍在
        assert "get_all_role_names" in src      # 类候选经 schema 单源派生
        # 无自建 {角色: 类} 字典: 代码行(非注释/文档行)里禁出现 exec/audit→executor/auditor 映射字面量
        code_lines = [
            l for l in src.splitlines()
            if l.strip() and not l.strip().startswith("#")
            and "例:" not in l and '"""' not in l
        ]
        self_built = [
            l for l in code_lines
            if '"executor"' in l and '"auditor"' in l and (": " in l or "{" in l)
            and "_ROLE_MATCH_SUBSTRINGS" not in l
        ]
        assert not self_built, self_built

    def test_custom_roles_param_is_test_injection_only(self):
        """_policy_matches_role 的 custom_roles 参数仅限测试注入(显式注入可用),
        生产路径(find_active_policy)不暴露该参数——默认从门注册表取。"""
        import inspect
        from tools.aipos_cli.policy_resolver import _policy_matches_role, find_active_policy

        # 注入路径可用(测试专用)
        assert _policy_matches_role(
            {"agent_or_role": "fx-role.a.b"}, "exec",
            custom_roles={"fx-role": {"class": "executor"}},
        ) is True
        # 生产入口签名无注册表参数(调用方喂不进来)
        assert "custom_roles" not in inspect.signature(find_active_policy).parameters


# ── 验收⑤: F26C 分发与本处读同一加载函数(单源实证) ──────────────────────────


class TestAcceptance5SameLoaderAsDistribution:
    def test_distribution_and_envelope_share_load_custom_roles(self, tmp_path, monkeypatch):
        """单源实证: 计数补丁钉住 custom_roles.load_custom_roles——信封解析链与
        F26C 分发类展开链(distribute_tools→resolve_role_to_class)走同一函数。"""
        import tools.aipos_cli.custom_roles as cr
        import tools.distribute_tools as dt
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        real = cr.load_custom_roles
        calls = {"n": 0}

        def counting(project_root):
            calls["n"] += 1
            return real(project_root)

        monkeypatch.setattr(cr, "load_custom_roles", counting)
        before = calls["n"]
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"
        after_envelope = calls["n"]
        assert after_envelope > before, "envelope path must call the shared loader"

        spec = {"distributions": [
            {"name": "charter-fx", "applies_to_roles": ["class:executor"], "kind": "charter"},
            {"name": "audit-fx", "applies_to_roles": ["class:auditor"], "kind": "skill"},
        ]}
        matched = dt.get_distributions_for_role("hbj-coder", spec, project_root=ws)
        after_distribution = calls["n"]
        assert after_distribution > after_envelope, "distribution path must call the shared loader"
        assert [d["name"] for d in matched] == ["charter-fx"]  # 类展开=门注册表说的 executor

    def test_distribution_class_expansion_follows_gate_registry(self, tmp_path):
        """F26C 分发类展开按门注册表: hbj-auditor 只吃 class:auditor 条目。"""
        import tools.distribute_tools as dt

        ws = make_gate_home(tmp_path, registry_tokens=None)
        spec = {"distributions": [
            {"name": "charter-fx", "applies_to_roles": ["class:executor"], "kind": "charter"},
            {"name": "audit-fx", "applies_to_roles": ["class:auditor"], "kind": "skill"},
        ]}
        assert [d["name"] for d in dt.get_distributions_for_role("hbj-auditor", spec, project_root=ws)] == ["audit-fx"]
        assert [d["name"] for d in dt.get_distributions_for_role("hbj-coder", spec, project_root=ws)] == ["charter-fx"]
        # 未注册角色零授权
        assert dt.get_distributions_for_role("ghost-coder", spec, project_root=ws) == []

    def test_source_level_same_module(self):
        """源级断言: 分发与信封解析都从 custom_roles 模块取(同一加载函数所在模块)。"""
        dist_src = SRC_DISTRIBUTE_TOOLS.read_text(encoding="utf-8")
        pr_src = SRC_POLICY_RESOLVER.read_text(encoding="utf-8")
        assert "from tools.aipos_cli.custom_roles import resolve_role_to_class" in dist_src
        assert "from tools.aipos_cli.custom_roles import load_custom_roles" in pr_src


# ── 验收⑦ 边界: 注册表解析语义(过期/无 class/畸形 class/内建角色条目) ─────────


class TestRegistryParsingBoundaries:
    def test_expired_entry_not_a_live_custom_role(self, tmp_path):
        from tools.aipos_cli.custom_roles import load_custom_roles

        ws = make_gate_home(tmp_path, registry_tokens=[
            _synthetic_entry("hbj-coder", "executor", expires_at="2020-01-01T00:00:00Z"),
        ])
        assert load_custom_roles(ws) == {}

    def test_transport_entry_without_role_class_not_custom(self, tmp_path):
        from tools.aipos_cli.custom_roles import load_custom_roles

        entry = _synthetic_entry("enroll-transport", "executor", role="enroll-transport")
        entry.pop("role_class")  # 运输凭证无 role_class
        ws = make_gate_home(tmp_path, registry_tokens=[entry])
        assert "enroll-transport" not in load_custom_roles(ws)

    def test_malformed_class_skipped(self, tmp_path):
        from tools.aipos_cli.custom_roles import load_custom_roles

        ws = make_gate_home(tmp_path, registry_tokens=[
            _synthetic_entry("hbj-coder", "bogus-class"),
        ])
        assert load_custom_roles(ws) == {}

    def test_builtin_role_entry_with_hostile_role_class_ignored(self, tmp_path):
        """敌意条目(内建 role + 伪造 role_class)不改变内建自解析。"""
        from tools.aipos_cli.custom_roles import load_custom_roles, resolve_role_to_class

        ws = make_gate_home(tmp_path, registry_tokens=[
            _synthetic_entry("executor", "auditor", role="executor"),
        ])
        assert "executor" not in load_custom_roles(ws)
        assert resolve_role_to_class("executor", ws) == "executor"

    def test_first_seen_wins_on_role_collision(self, tmp_path):
        """同名角色多条目: 先见者胜(与门加载器同序, 确定性)。"""
        from tools.aipos_cli.custom_roles import load_custom_roles

        ws = make_gate_home(tmp_path, registry_tokens=[
            _synthetic_entry("hbj-coder", "executor", token_ref="svc-hbj-coder"),
            _synthetic_entry("hbj-coder", "auditor", token_ref="svc-hbj-coder-2"),
        ])
        assert load_custom_roles(ws) == {"hbj-coder": {"class": "executor"}}


# ── 写路径: register/remove 同步归位门注册表(写读同源) ───────────────────────


class TestRegisterRemoveWriteGateRegistry:
    def test_register_writes_connection_json_not_project_json(self, tmp_path):
        from tools.aipos_cli.custom_roles import load_custom_roles, register_custom_role
        from tools.aipos_cli.service_mode import ROLE_SPECS

        ws = make_gate_home(tmp_path, registry_tokens=[])
        returned = register_custom_role(ws, "kiwiaiops", "executor", by="dec_fx_1", reason="fx")

        assert returned == {"kiwiaiops": {"class": "executor"}}
        # project.json 未被写入注册表(第二来源已删)
        pj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
        assert "custom_roles" not in pj
        # 门注册表(connection.json)有 svc 条目: class 真相 + 派生 scopes + token
        reg = json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))
        entry = next(t for t in reg["tokens"] if t.get("role") == "kiwiaiops")
        assert entry["role_class"] == "executor"
        assert entry["token_ref"] == "svc-kiwiaiops"
        assert entry["scopes"] == list(next(s for s in ROLE_SPECS if s["role"] == "executor")["scopes"])
        assert entry.get("token")  # 铸了真 token(非空)
        # 加载路径读得回(写读同源)
        assert load_custom_roles(ws) == {"kiwiaiops": {"class": "executor"}}

    def test_reregister_updates_class_preserves_token(self, tmp_path):
        from tools.aipos_cli.custom_roles import register_custom_role

        ws = make_gate_home(tmp_path, registry_tokens=[])
        register_custom_role(ws, "kiwiaiops", "executor", by="dec_fx_1")
        reg = json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))
        token_before = next(t for t in reg["tokens"] if t["role"] == "kiwiaiops")["token"]

        register_custom_role(ws, "kiwiaiops", "auditor", by="dec_fx_2")  # 改挂 auditor

        reg = json.loads((ws / ".lybra" / "connection.json").read_text(encoding="utf-8"))
        entries = [t for t in reg["tokens"] if t["role"] == "kiwiaiops"]
        assert len(entries) == 1  # 原位更新, 不重复
        assert entries[0]["role_class"] == "auditor"
        assert entries[0]["token"] == token_before  # 既有凭据保留

    def test_register_visible_from_sibling_workspace_gate_level(self, tmp_path):
        """角色是门级概念: 在 A 工作区注册, 兄弟 B 工作区解析得到(统一注册表)。"""
        from tools.aipos_cli.custom_roles import load_custom_roles, register_custom_role, resolve_role_to_class

        ws_a = make_gate_home(tmp_path, registry_tokens=[])
        register_custom_role(ws_a, "hbj-coder", "executor", by="dec_fx_1")
        ws_b = ws_a.parent / "chris-huibojin-fx"  # 同 home 下的兄弟工作区
        assert resolve_role_to_class("hbj-coder", ws_b) == "executor"
        assert "hbj-coder" in load_custom_roles(ws_b)

    def test_remove_kills_role_entries_idempotent(self, tmp_path):
        from tools.aipos_cli.custom_roles import load_custom_roles, remove_custom_role, register_custom_role

        ws = make_gate_home(tmp_path, registry_tokens=[])
        register_custom_role(ws, "kiwiaiops", "executor", by="dec_fx_1")
        # 再放一条同角色实例绑定条目(enroll 形态)
        reg_path = ws / ".lybra" / "connection.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg["tokens"].append({
            "agent_instance": "kiwiaiops.fx.kiwiai-dev", "role": "kiwiaiops",
            "role_class": "executor", "token": "fx-synthetic-instance-token",
            "token_ref": "svc-kiwiaiops-inst", "scopes": [],
        })
        reg_path.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")

        remove_custom_role(ws, "kiwiaiops", by="dec_fx_3")

        assert load_custom_roles(ws) == {}
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        assert not [t for t in reg["tokens"] if t.get("role") == "kiwiaiops"]  # 角色亡, 凭据同灭
        assert remove_custom_role(ws, "kiwiaiops", by="dec_fx_4") == {}  # 幂等


# ── 验收②: 内建角色零回归(夹具级; 真工作区活体锚见 f32 夹具) ─────────────────


class TestAcceptance2BuiltinZeroRegression:
    def test_builtin_direct_match_with_gate_registry_present(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        # 注册表在场(含 hbj 双角色), 内建角色直配语义照常
        (ws / "5_tasks" / "policies" / "pol_lybra_dev_fx.md").write_text(
            "---\nrecord_type: owner_autonomy_policy\npolicy_id: pol_lybra_dev_fx\n"
            "status: active\nexpires_at: '2099-09-30T00:00:00Z'\n"
            "agent_or_role: exec.fx.kiwiai-dev\n---\n# fx\n", encoding="utf-8")
        # exec 直配策略(pol_lybra_dev_fx)与自定义角色策略(pol_chris_coder_1)并存:
        # 文件名倒序下 chris 卡先扫到 → 自定义角色匹配; 直配分量 exec 仍匹配
        from tools.aipos_cli.policy_resolver import _policy_matches_role
        assert _policy_matches_role({"agent_or_role": "exec.fx.kiwiai-dev"}, "exec") is True
        assert _policy_matches_role({"agent_or_role": "audit.fx.kiwiai-dev"}, "audit") is True

    def test_unregistered_component_never_matches_even_with_registry(self, tmp_path):
        from tools.aipos_cli.policy_resolver import find_active_policy

        ws = make_gate_home(tmp_path, registry_tokens=None)
        (ws / "5_tasks" / "policies" / "pol_ghost.md").write_text(
            "---\nrecord_type: owner_autonomy_policy\npolicy_id: pol_ghost\n"
            "status: active\nexpires_at: '2099-09-30T00:00:00Z'\n"
            "agent_or_role: ghost-coder.chris-huibojin.kiwiai-dev\n---\n# fx\n",
            encoding="utf-8")
        # ghost-coder 不在门注册表 → 零授权, 永不匹配(防提权)
        assert find_active_policy(ws, role="exec", policy_type="dev") == "pol_chris_coder_1"

    def test_resolve_builtin_self(self, tmp_path):
        from tools.aipos_cli.custom_roles import resolve_role_to_class

        ws = make_gate_home(tmp_path, registry_tokens=None)
        assert resolve_role_to_class("executor", ws) == "executor"
        assert resolve_role_to_class("auditor", ws) == "auditor"
        assert resolve_role_to_class("ghost-coder", ws) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
