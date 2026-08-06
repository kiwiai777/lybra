"""AIPOS-343 — 契约节静默吞错修复测试。

验收断言:
1. 跨工作区策略解析: agency 工作区能找到 pol_agency_1
2. 无有效信封时 publish BLOCK(不再产出哑卡)
3. 选择器空值 = 不限类型(match_claim_envelope 已有正确语义)
4. lybra-dev 侧零回归
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.policy_resolver import find_active_policy
from tools.aipos_cli.draft_writer import (
    ContractSectionError,
    _append_gate_contract_section,
    create_draft,
    publish_draft,
)


def _meta(task_id, **overrides):
    base = {
        "title": "Example Draft",
        "project": "lybra",
        "assigned_to": "agent-01",
        "agent_instance": "agent-01",
        "context_bundle": "agent-01",
        "task_mode": "code",
        "model_tier": "L2",
        "priority": "medium",
        "status": "pending",
        "created_by": "tester",
        "needs_owner": False,
        "output_target": "tools/aipos_cli/",
        "artifact_policy": "formal_write",
        "task_type": "one_shot",
        "polling_mode": "agent_polling",
        "claim_policy": "assigned_agent_only",
        "report_mode": "forum_reply",
        "recurrence": "none",
    }
    base.update(overrides)
    base["task_id"] = task_id
    return base


class TestCrossWorkspacePolicyResolution(unittest.TestCase):
    """AIPOS-343 验收 #1 + #3: 非 lybra 工作区也能找到策略信封。"""

    def test_agency_workspace_finds_pol_agency_1(self):
        """agency 工作区的 exec 策略能被找到(文件名不匹配 pol_lybra_* 模式)。"""
        agency_root = Path("/home/kiwi/ai-project-os/2_projects/kiwiaiagency")
        if not agency_root.exists():
            self.skipTest("agency 工作区不存在")
        result = find_active_policy(agency_root, role="exec", policy_type="dev")
        self.assertEqual(result, "pol_agency_1")

    def test_lybra_workspace_still_finds_pol_lybra_dev(self):
        """lybra 工作区零回归。"""
        lybra_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
        if not lybra_root.exists():
            self.skipTest("lybra 工作区不存在")
        result = find_active_policy(lybra_root, role="exec", policy_type="dev")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("pol_lybra_dev_"))

    def test_policy_resolver_matches_by_frontmatter_not_filename(self):
        """策略解析基于 frontmatter 的 agent_or_role,不是文件名前缀。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            policies_dir = root / "5_tasks" / "policies"
            policies_dir.mkdir(parents=True)
            # Non-standard filename, but correct frontmatter
            (policies_dir / "my_custom_policy.md").write_text(
                "---\n"
                "record_type: owner_autonomy_policy\n"
                "policy_id: my_custom_exec_policy\n"
                "status: active\n"
                "agent_or_role: exec.myproject.local\n"
                "active_from: '2020-01-01T00:00:00Z'\n"
                "expires_at: '2099-12-31T23:59:59Z'\n"
                "max_tasks: 100\n"
                "---\n"
                "# Custom Policy\n",
                encoding="utf-8",
            )
            result = find_active_policy(root, role="exec", policy_type="dev")
            self.assertEqual(result, "my_custom_exec_policy")

    def test_policy_resolver_skips_expired(self):
        """过期的策略不应被返回。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            policies_dir = root / "5_tasks" / "policies"
            policies_dir.mkdir(parents=True)
            (policies_dir / "expired_policy.md").write_text(
                "---\n"
                "record_type: owner_autonomy_policy\n"
                "policy_id: expired_pol\n"
                "status: active\n"
                "agent_or_role: exec.test.local\n"
                "expires_at: '2020-01-01T00:00:00Z'\n"
                "---\n"
                "# Expired\n",
                encoding="utf-8",
            )
            result = find_active_policy(root, role="exec", policy_type="dev")
            self.assertIsNone(result)

    def test_policy_resolver_skips_wrong_role(self):
        """角色不匹配的策略不应被返回。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            policies_dir = root / "5_tasks" / "policies"
            policies_dir.mkdir(parents=True)
            (policies_dir / "audit_only.md").write_text(
                "---\n"
                "record_type: owner_autonomy_policy\n"
                "policy_id: audit_only_pol\n"
                "status: active\n"
                "agent_or_role: audit.test.local\n"
                "expires_at: '2099-12-31T23:59:59Z'\n"
                "---\n"
                "# Audit Only\n",
                encoding="utf-8",
            )
            # Looking for exec, should NOT find audit-only policy
            result = find_active_policy(root, role="exec", policy_type="dev")
            self.assertIsNone(result)
            # Looking for audit, should find it
            result = find_active_policy(root, role="audit", policy_type="audit")
            self.assertEqual(result, "audit_only_pol")


class TestContractSectionErrorPropagation(unittest.TestCase):
    """AIPOS-343 验收 #2: 无有效信封时 publish 明确失败。"""

    def test_no_policies_causes_block_not_silent_omission(self):
        """无策略文件时,publish 应 BLOCK(不是静默省略契约节)。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("pending", "claimed", "completed", "blocked"):
                (root / "5_tasks" / "queue" / d).mkdir(parents=True, exist_ok=True)
            # NO policies directory at all
            create_draft(root, _meta("AIPOS-343-NO-POL"), "## Goal\n\nTest.")
            result = publish_draft(root, "5_tasks/drafts/aipos-343-no-pol.md", actor="agent-01")
            self.assertEqual(result["verdict"], "BLOCK", 
                f"Expected BLOCK when no policies exist, got {result['verdict']}. "
                f"AIPOS-343: contract section failure must be loud, not silent.")
            # The blocking reason should mention the contract section failure
            blocking_text = " ".join(result["blocking_reasons"])
            self.assertIn("contract section", blocking_text.lower())

    def test_contract_section_error_contains_diagnostic_info(self):
        """ContractSectionError 应包含诊断信息(哪一步失败、如何修复)。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No project.json, no policies — everything missing
            metadata = _meta("AIPOS-343-DIAG")
            with self.assertRaises(ContractSectionError) as ctx:
                _append_gate_contract_section(root, metadata, "AIPOS-343-DIAG", "## Body\n\nTest.")
            error_msg = str(ctx.exception)
            # Must say which step failed
            self.assertIn("AIPOS-343", error_msg)
            # Must include the workspace root for diagnosis
            self.assertIn(str(root), error_msg)
            # Must include fix guidance
            self.assertIn("Fix", error_msg)

    def test_idempotency_still_works(self):
        """已有契约节的卡不重复追加(即使环境残缺)。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = "## Body\n\n【认领与交回】\n\nAlready present.\n"
            result = _append_gate_contract_section(root, {}, "TEST", existing)
            self.assertEqual(result, existing)


class TestSelectorEmptySemantics(unittest.TestCase):
    """AIPOS-343 验收 #3: task_selector_task_mode 空 = 不限类型。"""

    def test_empty_task_mode_matches_any_task_type(self):
        """pol_agency_1 的 task_selector_task_mode 为空,应匹配任意 task_mode。"""
        from tools.aipos_cli.autonomy_policy import match_claim_envelope
        from datetime import datetime, timezone

        policy = {
            "mode": "PreAuthorized",
            "status": "active",
            "approved_by_owner": True,
            "active_from": "2020-01-01T00:00:00Z",
            "expires_at": "2099-12-31T23:59:59Z",
            "agent_or_role": "exec.kiwiaiagency.kiwiai-dev",
            "task_selector_task_mode": "",  # empty = any
            "task_selector_project": "kiwiaiagency",
            "task_selector_task_ids": [],
            "max_tasks": 30,
        }
        now = datetime(2026, 8, 5, tzinfo=timezone.utc)

        # code task should match
        matched, reason = match_claim_envelope(
            policy=policy, task_id="TEST-1", task_mode="code",
            project="kiwiaiagency", agent_instance="exec.kiwiaiagency.kiwiai-dev",
            actor="exec.kiwiaiagency.kiwiai-dev", now=now, released_count=0,
        )
        self.assertTrue(matched, f"code task should match: {reason}")

        # content task should also match (empty task_mode = any)
        matched, reason = match_claim_envelope(
            policy=policy, task_id="TEST-2", task_mode="content",
            project="kiwiaiagency", agent_instance="exec.kiwiaiagency.kiwiai-dev",
            actor="exec.kiwiaiagency.kiwiai-dev", now=now, released_count=1,
        )
        self.assertTrue(matched, f"content task should match: {reason}")

        # research task should also match
        matched, reason = match_claim_envelope(
            policy=policy, task_id="TEST-3", task_mode="research",
            project="kiwiaiagency", agent_instance="exec.kiwiaiagency.kiwiai-dev",
            actor="exec.kiwiaiagency.kiwiai-dev", now=now, released_count=2,
        )
        self.assertTrue(matched, f"research task should match: {reason}")


class TestLybraRegression(unittest.TestCase):
    """AIPOS-343 验收 #4: lybra-dev 侧零回归。"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("pending", "claimed", "completed", "blocked"):
            (self.root / "5_tasks" / "queue" / d).mkdir(parents=True, exist_ok=True)
        policies_dir = self.root / "5_tasks" / "policies"
        policies_dir.mkdir(parents=True)
        (policies_dir / "pol_lybra_dev_7.md").write_text(
            "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_lybra_publish_still_appends_section(self):
        """lybra 工作区 publish 仍然正常追加契约节。"""
        create_draft(self.root, _meta("AIPOS-343-REG"), "## Goal\n\nRegression test.")
        result = publish_draft(self.root, "5_tasks/drafts/aipos-343-reg.md", actor="agent-01")
        self.assertEqual(result["verdict"], "PASS", result.get("blocking_reasons"))
        published = (self.root / result["target_path"]).read_text(encoding="utf-8")
        self.assertIn("【认领与交回】", published)
        self.assertIn("pol_lybra_dev_7", published)


class TestLiveAgencyWorkspace(unittest.TestCase):
    """AIPOS-343 验收 #1 活体: agency 工作区发卡能拿到契约节。"""

    def test_agency_contract_section_renders_with_pol_agency_1(self):
        """端到端: agency 工作区的契约节能正确渲染,信封为 pol_agency_1。"""
        agency_root = Path("/home/kiwi/ai-project-os/2_projects/kiwiaiagency")
        if not agency_root.exists():
            self.skipTest("agency 工作区不存在")

        from tools.aipos_cli.flow_description import resolve_collaboration_profile
        from tools.aipos_cli.gate_contract_section import render_gate_contract_section

        project_json = agency_root / "project.json"
        profile = resolve_collaboration_profile(project_json)
        task_fields = {"task_mode": "code", "output_target": "tools/", "audit": "required"}

        section = render_gate_contract_section(
            profile, task_fields, role="executor",
            gate_url="http://kiwiai-dev.tail6b5218.ts.net:7118",
            connection_json_rel=".lybra/connection.json",
            workspace_display=str(agency_root),
            task_id="TEST-LIVE-1",
            workspace_root=agency_root,
        )
        self.assertIn("【认领与交回】", section)
        self.assertIn("pol_agency_1", section)
        self.assertIn("lybra_queue_claim_dry_run", section)


if __name__ == "__main__":
    unittest.main()
