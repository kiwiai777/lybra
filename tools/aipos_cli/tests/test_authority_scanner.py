from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.authority_scanner import build_authority_report
from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks
from tools.aipos_cli.validator import validate_tasks


class AuthorityScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_file(self, relative_path: str, content: str) -> Path:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def task_frontmatter(self, task_id: str, status: str = "pending", extra: list[str] | None = None) -> str:
        lines = [
            "---",
            f"task_id: {task_id}",
            f"title: {task_id}",
            "project: ai-project-os",
            "assigned_to: agent-01",
            "agent_instance: agent-01",
            "context_bundle: test",
            "task_mode: docs",
            "task_class: simple",
            "priority: medium",
            f"status: {status}",
            "created_by: tester",
            "needs_owner: false",
            "output_target: workspace_artifacts/",
            "artifact_policy: formal_write",
            "model_tier: L2",
        ]
        lines.extend(extra or [])
        lines.extend(["---", "Body", ""])
        return "\n".join(lines)

    def draft_metadata(self, task_id: str) -> dict[str, object]:
        return {
            "task_id": task_id,
            "title": task_id,
            "project": "ai-project-os",
            "assigned_to": "agent-01",
            "agent_instance": "agent-01",
            "context_bundle": "test",
            "task_mode": "docs",
            "task_class": "simple",
            "model_tier": "L2",
            "priority": "medium",
            "status": "pending",
            "created_by": "tester",
            "needs_owner": False,
            "output_target": "workspace_artifacts/",
            "artifact_policy": "formal_write",
            "task_type": "one_shot",
            "polling_mode": "agent_polling",
            "claim_policy": "assigned_agent_only",
            "report_mode": "completion_summary",
            "recurrence": "none",
        }

    def publish_valid_task(self, task_id: str) -> None:
        create_draft(self.repo_root, self.draft_metadata(task_id), "## Goal\n\nValid.\n")
        result = publish_draft(self.repo_root, f"5_tasks/drafts/{task_id.lower()}.md", actor="owner.local")
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["wrote"])

    def validate_report(self) -> dict[str, object]:
        tasks = load_all_tasks(self.repo_root)
        records = load_records(self.repo_root)
        return validate_tasks(tasks, records=records, profiles={})

    def test_fs_injected_pending_orphan_is_quarantined_without_dosing_valid_task(self) -> None:
        self.publish_valid_task("AIPOS-194-VALID")
        self.write_file(
            "5_tasks/queue/pending/aipos-194-orphan.md",
            self.task_frontmatter("AIPOS-194-ORPHAN", "pending"),
        )

        report = self.validate_report()
        by_id = {item["task_id"]: item for item in report["tasks"]}

        self.assertEqual(by_id["AIPOS-194-ORPHAN"]["authority_verdict"], "QUARANTINED")
        self.assertFalse(by_id["AIPOS-194-ORPHAN"]["effective_truth"])
        self.assertEqual(by_id["AIPOS-194-VALID"]["authority_verdict"], "VALID")
        self.assertTrue(by_id["AIPOS-194-VALID"]["effective_truth"])
        self.assertEqual(report["effective_queue_summary"]["total_tasks"], 2)
        self.assertEqual(report["effective_queue_summary"]["effective_tasks"], 1)
        self.assertEqual(report["effective_queue_summary"]["excluded_authority_invalid"], 1)

    def test_fs_injected_claimed_orphan_is_orphan_invalid(self) -> None:
        self.write_file(
            "5_tasks/queue/claimed/aipos-194-claimed-orphan.md",
            self.task_frontmatter(
                "AIPOS-194-CLAIMED-ORPHAN",
                "claimed",
                [
                    "claimed_by: agent-01",
                    "claim_id: claim_AIPOS-194-CLAIMED-ORPHAN_001_agent-01",
                    "active_session_id: session_AIPOS-194-CLAIMED-ORPHAN_001_agent-01",
                ],
            ),
        )

        report = self.validate_report()
        task = report["tasks"][0]

        self.assertEqual(task["authority_verdict"], "ORPHAN_INVALID")
        self.assertFalse(task["effective_truth"])
        self.assertEqual(report["authority_summary"]["orphan_invalid"], 1)

    def test_fs_injected_orphan_record_is_reported(self) -> None:
        self.write_file(
            "5_tasks/records/claims/MISSING-TASK/claim_missing_001.md",
            """---
task_id: MISSING-TASK
claim_id: claim_missing_001
claimed_by: agent-01
claimed_at: 2026-06-10T01:00:00Z
---
Claim
""",
        )

        report = build_authority_report(tasks=[], records=load_records(self.repo_root), repo_root=self.repo_root)

        self.assertTrue(
            any(
                item.get("reason_code") == "ORPHAN_RECORD_WITHOUT_TASK"
                and item.get("subject_ref") == "5_tasks/records/claims/MISSING-TASK/claim_missing_001.md"
                for item in report["authority_findings"]
            )
        )

    def test_draft_is_pre_authority_warn_not_invalid(self) -> None:
        self.write_file(
            "5_tasks/drafts/aipos-194-draft.md",
            self.task_frontmatter("AIPOS-194-DRAFT", "pending"),
        )

        report = build_authority_report(tasks=[], records=load_records(self.repo_root), repo_root=self.repo_root)
        finding = report["authority_findings"][0]

        self.assertEqual(finding["authority_verdict"], "PRE_AUTHORITY_WARN")
        self.assertEqual(finding["reason_code"], "DRAFT_PRE_AUTHORITY")
        self.assertTrue(finding["effective_truth"])

    def test_completed_and_blocked_are_no_provenance_class_in_v0(self) -> None:
        self.write_file(
            "5_tasks/queue/completed/aipos-194-completed.md",
            self.task_frontmatter(
                "AIPOS-194-COMPLETED",
                "completed",
                [
                    "completed_by: agent-01",
                    "completed_at: 2026-06-10T01:00:00Z",
                    "last_session_id: session_AIPOS-194-COMPLETED_001_agent-01",
                ],
            ),
        )
        self.write_file(
            "5_tasks/queue/blocked/aipos-194-blocked.md",
            self.task_frontmatter(
                "AIPOS-194-BLOCKED",
                "blocked",
                [
                    "blocked_by: owner",
                    "blocked_at: 2026-06-10T01:00:00Z",
                    "block_reason: test",
                    "last_session_id: session_AIPOS-194-BLOCKED_001_agent-01",
                ],
            ),
        )

        report = self.validate_report()
        by_id = {item["task_id"]: item for item in report["tasks"]}

        self.assertEqual(by_id["AIPOS-194-COMPLETED"]["authority_verdict"], "PRE_AUTHORITY_WARN")
        self.assertTrue(by_id["AIPOS-194-COMPLETED"]["effective_truth"])
        self.assertEqual(by_id["AIPOS-194-BLOCKED"]["authority_verdict"], "PRE_AUTHORITY_WARN")
        self.assertTrue(by_id["AIPOS-194-BLOCKED"]["effective_truth"])


if __name__ == "__main__":
    unittest.main()
