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
    (policies_dir / "pol_lybra_dev_7.md").write_text(
        "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n",
        encoding="utf-8",
    )
    (policies_dir / "pol_lybra_audit_2.md").write_text(
        "---\npolicy_id: pol_lybra_audit_2\nstatus: active\nrole: audit\npolicy_type: audit\n---\n# Audit\n",
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
        # report location (verdict under reviewed-card ID dir)
        self.assertIn("audit_verdicts/AIPOS-200/verdict_*.md", body)
        # honest reporting red line
        self.assertIn("如实报红线", body)

    def test_r_card_carries_auditor_contract_section_when_repo_root(self):
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
