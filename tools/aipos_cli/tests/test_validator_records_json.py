from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.aipos_cli import build_validate_json_report
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks
from tools.aipos_cli.validator import validate_tasks


class ValidatorRecordsJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for queue_state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / queue_state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_file(self, relative_path: str, content: str) -> Path:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_task(self, task_id: str, body: str = "Task body", **metadata: object) -> Path:
        queue_state = str(metadata.get("status", "pending"))
        lines = [
            "---",
            f"task_id: {task_id}",
            f"title: {metadata.get('title', task_id)}",
            f"project: {metadata.get('project', 'ai-project-os')}",
            f"assigned_to: {metadata.get('assigned_to', 'dev.codex.local')}",
            f"agent_instance: {metadata.get('agent_instance', 'dev.codex.local')}",
            f"context_bundle: {metadata.get('context_bundle', 'dev.codex.local')}",
            f"task_mode: {metadata.get('task_mode', 'coding')}",
            f"priority: {metadata.get('priority', 'high')}",
            f"status: {queue_state}",
            f"created_by: {metadata.get('created_by', 'tester')}",
            f"needs_owner: {str(metadata.get('needs_owner', False)).lower()}",
            f"output_target: {metadata.get('output_target', 'tools/aipos_cli/')}",
            f"artifact_policy: {metadata.get('artifact_policy', 'formal_write')}",
            f"model_tier: {metadata.get('model_tier', 'L2')}",
            f"session_policy: {metadata.get('session_policy', 'single_task_session')}",
            f"context_isolation: {metadata.get('context_isolation', 'strict')}",
            f"artifact_scope: {metadata.get('artifact_scope', 'tools/aipos_cli/')}",
            f"memory_scope: {metadata.get('memory_scope', 'validator json testing')}",
        ]
        optional_keys = (
            "claim_id",
            "active_session_id",
            "last_session_id",
            "claimed_by",
            "claimed_at",
            "approval_required",
            "owner_review_required",
            "risk_level",
            "source_tag",
            "client_tag",
            "external_ref",
        )
        for key in optional_keys:
            if key in metadata and metadata[key] is not None:
                value = metadata[key]
                if isinstance(value, bool):
                    rendered = str(value).lower()
                else:
                    rendered = str(value)
                lines.append(f"{key}: {rendered}")
        lines.extend(["---", body, ""])
        return self.write_file(
            f"5_tasks/queue/{queue_state}/{task_id.lower().replace('_', '-').replace(' ', '-')}.md",
            "\n".join(lines),
        )

    def write_record(self, relative_path: str, content: str) -> Path:
        return self.write_file(relative_path, content)

    def build_validate_json(self) -> dict[str, object]:
        tasks = load_all_tasks(self.repo_root)
        records = load_records(self.repo_root)
        report = validate_tasks(tasks, records=records, profiles={})
        return build_validate_json_report(report, records=records)

    def test_validate_json_includes_records_summary_without_records_directory(self) -> None:
        self.write_task("AIPOS-24")

        report = self.build_validate_json()
        records_summary = report["records_summary"]

        self.assertEqual(records_summary["sessions_total"], 0)
        self.assertEqual(records_summary["claims_total"], 0)
        self.assertEqual(records_summary["parse_errors_total"], 0)
        self.assertEqual(records_summary["warnings_total"], 0)
        self.assertEqual(records_summary["tasks_with_records"], 0)
        self.assertEqual(records_summary["tasks_with_record_issues"], 0)
        self.assertFalse(records_summary["records_root_exists"])
        self.assertEqual(report["records_diagnostics"], [])

    def test_external_intake_metadata_is_optional_and_preserved(self) -> None:
        self.write_task(
            "AIPOS-107",
            source_tag="external_owner_inbox",
            client_tag="alpha_client",
            external_ref="extmsg:abc123",
        )

        report = self.build_validate_json()
        task = report["tasks"][0]

        self.assertEqual(task["verdict"], "PASS")
        self.assertEqual(task["source_tag"], "external_owner_inbox")
        self.assertEqual(task["client_tag"], "alpha_client")
        self.assertEqual(task["external_ref"], "extmsg:abc123")
        self.assertEqual(task["warnings"], [])

    def test_invalid_external_intake_metadata_warns_without_blocking(self) -> None:
        self.write_task(
            "AIPOS-107-WARN",
            source_tag="External Owner Inbox",
            client_tag="client-1",
            external_ref="x" * 257,
        )

        report = self.build_validate_json()
        task = report["tasks"][0]

        self.assertEqual(task["verdict"], "WARN")
        self.assertIn("Invalid source_tag format", task["warnings"])
        self.assertIn("Invalid client_tag format", task["warnings"])
        self.assertIn("Invalid external_ref format", task["warnings"])
        self.assertEqual(task["blocking_reasons"], [])

    def test_validate_json_includes_per_task_record_ref_checks_for_existing_records(self) -> None:
        self.write_task(
            "AIPOS-24",
            claim_id="claim_AIPOS-24_001_dev",
            active_session_id="session_AIPOS-24_001_dev",
            last_session_id="session_AIPOS-24_001_dev",
        )
        self.write_record(
            "5_tasks/records/sessions/AIPOS-24/session_AIPOS-24_001_dev.md",
            """---
task_id: AIPOS-24
session_id: session_AIPOS-24_001_dev
created_at: 2026-04-28T10:00:00Z
---
Session
""",
        )
        self.write_record(
            "5_tasks/records/claims/AIPOS-24/claim_AIPOS-24_001_dev.md",
            """---
task_id: AIPOS-24
claim_id: claim_AIPOS-24_001_dev
claimed_at: 2026-04-28T09:59:00Z
---
Claim
""",
        )

        report = self.build_validate_json()
        task = report["tasks"][0]
        statuses = {item["field"]: item["status"] for item in task["record_ref_checks"]}

        self.assertEqual(statuses["claim_id"], "ok")
        self.assertEqual(statuses["active_session_id"], "ok")
        self.assertEqual(statuses["last_session_id"], "ok")
        self.assertEqual(task["records"]["session_records"], 1)
        self.assertEqual(task["records"]["claim_logs"], 1)
        self.assertFalse(task["records"]["has_record_issues"])

    def test_validate_json_includes_missing_record_diagnostics_without_blocking_task(self) -> None:
        self.write_task(
            "AIPOS-24",
            claim_id="claim_AIPOS-24_missing_dev",
            active_session_id="session_AIPOS-24_missing_dev",
        )

        report = self.build_validate_json()
        task = report["tasks"][0]
        diagnostics = report["records_diagnostics"]

        self.assertEqual(task["verdict"], "WARN")
        self.assertTrue(any(item["status"] == "missing" for item in task["record_ref_checks"]))
        self.assertTrue(any(item["kind"] == "missing_record" for item in diagnostics))
        self.assertTrue(any(item["severity"] == "warn" for item in diagnostics))

    def test_validate_json_includes_duplicate_and_mismatch_diagnostics(self) -> None:
        self.write_task("AIPOS-24")
        self.write_record(
            "5_tasks/records/sessions/AIPOS-24/session_one.md",
            """---
task_id: AIPOS-24
session_id: session_duplicate
created_at: 2026-04-28T10:00:00Z
---
One
""",
        )
        self.write_record(
            "5_tasks/records/sessions/AIPOS-99/session_two.md",
            """---
task_id: OTHER-999
session_id: session_duplicate
created_at: 2026-04-28T11:00:00Z
---
Two
""",
        )

        report = self.build_validate_json()
        records_summary = report["records_summary"]
        diagnostics = report["records_diagnostics"]

        self.assertEqual(records_summary["duplicate_session_ids"], 1)
        self.assertEqual(records_summary["task_id_mismatch_count"], 1)
        self.assertTrue(any(item["kind"] == "duplicate_record_id" for item in diagnostics))
        self.assertTrue(any(item["kind"] == "task_id_mismatch" for item in diagnostics))
        self.assertTrue(any(item["severity"] == "needs_owner" for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
