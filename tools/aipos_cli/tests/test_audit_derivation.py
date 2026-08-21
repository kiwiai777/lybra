"""
AIPOS-253 unit tests: audit task derivation on return_confirm.
"""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.audit_derivation import (
    build_derived_audit_task,
    derive_audit_task_id,
    derive_audit_task_on_return,
    should_derive_audit,
)
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.task_loader import find_task_by_id, load_all_tasks


class TestAuditDerivation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.queue_pending = self.repo_root / "5_tasks" / "queue" / "pending"
        self.queue_claimed = self.repo_root / "5_tasks" / "queue" / "claimed"
        self.records_publishes = self.repo_root / "5_tasks" / "records" / "publishes"
        self.queue_pending.mkdir(parents=True, exist_ok=True)
        self.queue_claimed.mkdir(parents=True, exist_ok=True)
        self.records_publishes.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_derive_audit_task_id(self) -> None:
        self.assertEqual(derive_audit_task_id("AIPOS-123"), "AIPOS-123R")
        self.assertEqual(derive_audit_task_id("SC-45"), "SC-45R")

    def test_should_derive_audit_default_true(self) -> None:
        self.assertTrue(should_derive_audit({}))
        self.assertTrue(should_derive_audit({"audit": ""}))
        self.assertTrue(should_derive_audit({"audit": "yes"}))

    def test_should_derive_audit_opt_out(self) -> None:
        self.assertFalse(should_derive_audit({"audit": "none"}))
        self.assertFalse(should_derive_audit({"audit": "None"}))
        self.assertFalse(should_derive_audit({"audit": "NONE"}))

    def test_should_derive_audit_audit_mode_blocked(self) -> None:
        # AIPOS-256 F-253-3: audit tasks do not derive audit tasks (infinite R chain guard)
        self.assertFalse(should_derive_audit({"task_mode": "audit"}))
        self.assertFalse(should_derive_audit({"task_mode": "AUDIT"}))
        self.assertFalse(should_derive_audit({"task_mode": "Audit"}))

    def test_should_derive_audit_idempotency_checks(self) -> None:
        self.assertFalse(should_derive_audit({"related_audit_task_ref": "AIPOS-123R"}))
        self.assertFalse(should_derive_audit({"audit_dispatch_record_ref": "dispatch_xyz"}))
        self.assertFalse(
            should_derive_audit(
                {
                    "related_audit_task_ref": "AIPOS-123R",
                    "audit_dispatch_record_ref": "dispatch_xyz",
                }
            )
        )

    def test_build_derived_audit_task(self) -> None:
        source_metadata = {
            "task_id": "AIPOS-123",
            "title": "Test Task",
            "project": "lybra",
            "context_bundle": "test_bundle",
            "priority": "high",
            "output_target": "tools/",
        }
        result = build_derived_audit_task(
            source_task_id="AIPOS-123",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos_123.md",
            return_record_ref="return_AIPOS-123_20260726_agent-01",
            artifact_refs=["task_cards/AIPOS-123/file.py"],
        )

        self.assertEqual(result["audit_task_id"], "AIPOS-123R")
        self.assertEqual(result["audit_task_path"], "5_tasks/queue/pending/aipos-123r.md")

        metadata = result["metadata"]
        self.assertEqual(metadata["task_id"], "AIPOS-123R")
        self.assertEqual(metadata["title"], "Audit Test Task")
        self.assertEqual(metadata["project"], "lybra")
        self.assertEqual(metadata["task_mode"], "audit")
        self.assertEqual(metadata["task_class"], "simple")  # AIPOS-256F1 F-256-1: simple per enum
        self.assertEqual(metadata["audit"], "none")  # AIPOS-256 F-253-3: audit: none prevents R chain
        self.assertEqual(metadata["status"], "pending")
        self.assertEqual(metadata["created_by"], "gate_derivation")
        self.assertEqual(metadata["derived_from"], "AIPOS-123")
        self.assertEqual(metadata["reviewed_task_id"], "AIPOS-123")
        self.assertEqual(metadata["reviewed_task_path"], "5_tasks/queue/claimed/aipos_123.md")
        self.assertEqual(metadata["reviewed_return_record_ref"], "return_AIPOS-123_20260726_agent-01")
        self.assertIn("audit.lybra.", metadata["agent_instance"])
        self.assertEqual(metadata["assigned_to"], "audit_lybra")
        self.assertEqual(metadata["context_bundle"], "test_bundle")
        self.assertEqual(metadata["priority"], "high")
        self.assertEqual(metadata["output_target"], "tools/")

        body = result["body"]
        self.assertIn("AIPOS-123", body)
        self.assertIn("5_tasks/queue/claimed/aipos_123.md", body)
        self.assertIn("return_AIPOS-123_20260726_agent-01", body)
        self.assertIn("task_cards/AIPOS-123/file.py", body)

    def test_derive_audit_task_on_return_success(self) -> None:
        source_metadata = {
            "task_id": "AIPOS-123",
            "title": "Test Task",
            "project": "lybra",
        }
        result = derive_audit_task_on_return(
            repo_root=self.repo_root,
            source_task_id="AIPOS-123",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos_123.md",
            return_record_ref="return_AIPOS-123_20260726_agent-01",
            artifact_refs=["task_cards/AIPOS-123/file.py"],
        )

        self.assertTrue(result["derived"])
        self.assertEqual(result["audit_task_id"], "AIPOS-123R")
        self.assertEqual(result["audit_task_path"], "5_tasks/queue/pending/aipos-123r.md")

        # Verify audit task was written
        audit_task_file = self.repo_root / result["audit_task_path"]
        self.assertTrue(audit_task_file.exists())

        # Parse and verify frontmatter
        audit_content = audit_task_file.read_text(encoding="utf-8")
        audit_metadata, audit_body, _ = parse_markdown_frontmatter(audit_content)
        self.assertEqual(audit_metadata["task_id"], "AIPOS-123R")
        self.assertEqual(audit_metadata["task_mode"], "audit")
        self.assertEqual(audit_metadata["status"], "pending")
        self.assertEqual(audit_metadata["reviewed_task_id"], "AIPOS-123")

        # Verify publish record was written
        publish_record_path = self.repo_root / result["publish_record_path"]
        self.assertTrue(publish_record_path.exists())

        publish_content = publish_record_path.read_text(encoding="utf-8")
        publish_metadata, _, _ = parse_markdown_frontmatter(publish_content)
        self.assertEqual(publish_metadata["record_type"], "publish_record")
        self.assertEqual(publish_metadata["task_id"], "AIPOS-123R")
        self.assertEqual(publish_metadata["actor"], "gate_derivation")
        self.assertEqual(publish_metadata["published_task_ref"], "5_tasks/queue/pending/aipos-123r.md")

        # Verify performed_writes
        # (AIPOS-F18-fix2 F-D-1: 修陈旧断言——return自动派审特性(auto_derivation_on_return)
        #  新增第3写 audit_dispatch_record, 时间线实证如 dispatch_AIPOS-F18R_20260821T105459;
        #  原断言停在2写系特性落地后未同步)
        self.assertEqual(len(result["performed_writes"]), 3)
        self.assertEqual(result["performed_writes"][0]["type"], "derived_audit_task")
        self.assertEqual(result["performed_writes"][1]["type"], "publish_record")
        self.assertEqual(result["performed_writes"][2]["type"], "audit_dispatch_record")

    def test_derive_audit_task_on_return_idempotency_existing_task(self) -> None:
        # First derivation
        source_metadata = {"task_id": "AIPOS-123", "title": "Test Task", "project": "lybra"}
        result1 = derive_audit_task_on_return(
            repo_root=self.repo_root,
            source_task_id="AIPOS-123",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos_123.md",
            return_record_ref="return_AIPOS-123_20260726_agent-01",
            artifact_refs=[],
        )
        self.assertTrue(result1["derived"])

        # Second attempt should not derive
        result2 = derive_audit_task_on_return(
            repo_root=self.repo_root,
            source_task_id="AIPOS-123",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos_123.md",
            return_record_ref="return_AIPOS-123_20260726_agent-01",
            artifact_refs=[],
        )
        self.assertFalse(result2["derived"])
        self.assertIn("already exists", result2["reason"])

    def test_derive_audit_task_on_return_opt_out(self) -> None:
        source_metadata = {
            "task_id": "AIPOS-123",
            "title": "Test Task",
            "project": "lybra",
            "audit": "none",
        }
        result = derive_audit_task_on_return(
            repo_root=self.repo_root,
            source_task_id="AIPOS-123",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos_123.md",
            return_record_ref="return_AIPOS-123_20260726_agent-01",
            artifact_refs=[],
        )

        self.assertFalse(result["derived"])
        self.assertIn("audit: none", result["reason"])

        # Verify no files were written
        audit_task_file = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-123r.md"
        self.assertFalse(audit_task_file.exists())

    def test_derive_audit_task_on_return_already_dispatched(self) -> None:
        source_metadata = {
            "task_id": "AIPOS-123",
            "title": "Test Task",
            "project": "lybra",
            "related_audit_task_ref": "AIPOS-123R",
        }
        result = derive_audit_task_on_return(
            repo_root=self.repo_root,
            source_task_id="AIPOS-123",
            source_metadata=source_metadata,
            source_path="5_tasks/queue/claimed/aipos_123.md",
            return_record_ref="return_AIPOS-123_20260726_agent-01",
            artifact_refs=[],
        )

        self.assertFalse(result["derived"])
        self.assertIn("already dispatched", result["reason"])


if __name__ == "__main__":
    unittest.main()
