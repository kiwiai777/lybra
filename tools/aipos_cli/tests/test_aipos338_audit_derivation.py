"""AIPOS-338 S2/S6 — derived audit (R) card instructions + branch behavior."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.audit_derivation import (
    build_derived_audit_task,
    derive_audit_task_on_return,
    should_derive_audit,
)


def _src_meta(**overrides):
    base = {"task_id": "AIPOS-200", "title": "Do thing", "project": "lybra", "task_mode": "code"}
    base.update(overrides)
    return base


def _ensure_test_policies(repo_root: Path) -> None:
    """AIPOS-340F2: create minimal active policies so render_gate_contract_section can resolve."""
    policies_dir = repo_root / "5_tasks" / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)
    # AIPOS-F103 件④: 信封挑选唯一判据 match_claim_envelope(与门同一判据)——夹具信封须是真 owner_autonomy_policy 形;
    # 审计信封覆盖派生审计卡的认领实例(_derive_audit_instance) + 被审 code 卡(envelope_subject: 审计卡 = 被审卡)
    from tools.aipos_cli.audit_derivation import _derive_audit_instance

    for policy_id, agent_or_role in (("pol_lybra_dev_7", "exec.lybra.host"), ("pol_lybra_audit_2", _derive_audit_instance("lybra"))):
        (policies_dir / f"{policy_id}.md").write_text(
            "---\nrecord_type: owner_autonomy_policy\n"
            f"policy_id: {policy_id}\nmode: PreAuthorized\nstatus: active\napproved_by_owner: true\n"
            f"owner_approval_ref: dec_{policy_id}\nactive_from: '2020-01-01T00:00:00Z'\nexpires_at: '2099-01-01T00:00:00Z'\n"
            f"agent_or_role: {agent_or_role}\ntask_selector_task_mode: code\ntask_selector_project: lybra\n"
            "task_selector_task_ids: []\nmax_tasks: 50\n---\n# Policy\n",
            encoding="utf-8",
        )


class TestAuditInstructions(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        _ensure_test_policies(self.repo_root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_r_card_body_has_fixed_audit_instructions(self):
        result = build_derived_audit_task(
            source_task_id="AIPOS-200", source_metadata=_src_meta(),
            source_path="5_tasks/queue/claimed/aipos-200.md",
            return_record_ref="return_x", artifact_refs=[],
            collaboration_profile={"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
        )
        body = result["body"]
        # criterion = original card full text (self-report only as lead)
        self.assertIn("准绳 = 原执行卡全文", body)
        # independent evidence
        self.assertIn("独立取证", body)
        # two bottom-line assertions (AIPOS-314)
        self.assertIn("起得来", body)
        self.assertIn("产物可用", body)
        self.assertIn("AIPOS-314", body)
        # AIPOS-F66B 件③: report location = 审计卡 ID 目录(声明渲染), 不再是被审卡目录 / records 裁决目录
        self.assertIn("/AIPOS-200R/RETURN.md", body)
        self.assertNotIn("audit_verdicts/AIPOS-200/verdict_*.md", body)
        # honest reporting red line
        self.assertIn("如实报红线", body)

    def test_r_card_zero_gate_no_contract_section_when_repo_root(self):
        """AIPOS-F80 件①: 审计体零门——派生审计卡不带「认领与交回」节、无门动词, 落点句出自单源渲染函数。"""
        result = build_derived_audit_task(
            source_task_id="AIPOS-200", source_metadata=_src_meta(),
            source_path="5_tasks/queue/claimed/aipos-200.md",
            return_record_ref="return_x", artifact_refs=[],
            collaboration_profile={"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
            repo_root=self.repo_root,
        )
        body = result["body"]
        self.assertNotIn("【认领与交回】", body)
        for verb in ("lybra_queue_claim", "lybra_audit_verdict", "lybra_task_progress", "records/audit_verdicts"):
            self.assertNotIn(verb, body)
        # AIPOS-F93 件①: 落点句带报告必填字段(声明单源), 句子唯一出自 zero_gate_report_sentence
        from tools.aipos_cli.audit_derivation import zero_gate_report_sentence

        self.assertIn(zero_gate_report_sentence(f"{self.repo_root}/task_cards/AIPOS-200R/RETURN.md", "AIPOS-200"), body)
        self.assertIn("`commit_sha`(被审分支 card/AIPOS-200 tip", body)

    def test_r_card_carries_auditor_contract_section_when_manual_gate_mode(self):
        """AIPOS-F80 件①回归: manual_gate_mode 项目(chris 形)审计卡仍保留该节(同一判据)。"""
        (self.repo_root / "project.json").write_text('{"project": "lybra", "manual_gate_mode": true}', encoding="utf-8")
        result = build_derived_audit_task(
            source_task_id="AIPOS-200", source_metadata=_src_meta(),
            source_path="5_tasks/queue/claimed/aipos-200.md",
            return_record_ref="return_x", artifact_refs=[],
            collaboration_profile={"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
            repo_root=self.repo_root,
        )
        # the auditor contract section is appended
        self.assertIn("【认领与交回】", result["body"])
        self.assertIn("审计体必读", result["body"])
        self.assertIn("lybra_audit_verdict_dry_run", result["body"])

    def test_code_deploy_branch_r_card_has_deploy_reminder(self):
        result = build_derived_audit_task(
            source_task_id="AIPOS-200", source_metadata=_src_meta(deploy=True),
            source_path="5_tasks/queue/claimed/aipos-200.md",
            return_record_ref="return_x", artifact_refs=[],
            collaboration_profile={"code_enabled": True, "deploy_gate_enabled": True, "default_audit_mode": "agent"},
        )
        self.assertIn("部署门提醒", result["body"])
        self.assertIn("审计 PASS ≠ 可部署", result["body"])


class TestBranchAwareDerivation(unittest.TestCase):
    """S6②: non-code branch does NOT derive an independent audit R card."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        (self.repo_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (self.repo_root / "5_tasks" / "queue" / "claimed").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_should_not_derive_for_noncode_branch(self):
        self.assertFalse(should_derive_audit({"task_mode": "content"}, branch_id="noncode_bench_audit"))

    def test_noncode_branch_does_not_derive_r_card(self):
        result = derive_audit_task_on_return(
            repo_root=self.repo_root, source_task_id="AIPOS-300",
            source_metadata={"task_id": "AIPOS-300", "title": "Write doc", "project": "lybra", "task_mode": "content"},
            source_path="5_tasks/queue/claimed/aipos-300.md",
            return_record_ref="return_x", artifact_refs=[],
            collaboration_profile={"code_enabled": False, "deploy_gate_enabled": False, "default_audit_mode": "bench"},
        )
        self.assertFalse(result["derived"])
        self.assertIn("bench", result["reason"])
        # no R card file written
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-300r.md").exists())

    def test_code_branch_still_derives_r_card(self):
        result = derive_audit_task_on_return(
            repo_root=self.repo_root, source_task_id="AIPOS-301",
            source_metadata={"task_id": "AIPOS-301", "title": "Code it", "project": "lybra", "task_mode": "code"},
            source_path="5_tasks/queue/claimed/aipos-301.md",
            return_record_ref="return_x", artifact_refs=[],
            collaboration_profile={"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"},
        )
        self.assertTrue(result["derived"])
        self.assertEqual(result["audit_task_id"], "AIPOS-301R")


if __name__ == "__main__":
    unittest.main()
