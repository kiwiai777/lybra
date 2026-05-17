from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.records import check_task_record_refs, find_records_for_task, load_records


class RecordsReaderRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_record(self, relative_path: str, content: str) -> Path:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def make_task(self, task_id: str, **metadata: object) -> dict[str, object]:
        task_metadata = {"task_id": task_id, **metadata}
        return {"task_id": task_id, "metadata": task_metadata}

    def test_missing_records_directory_returns_empty_report(self) -> None:
        records = load_records(self.repo_root)

        self.assertEqual(records["summary"]["session_records"], 0)
        self.assertEqual(records["summary"]["claim_logs"], 0)
        self.assertEqual(records["records_root"], "5_tasks/records")
        self.assertEqual(records["sessions"], [])
        self.assertEqual(records["claims"], [])
        self.assertEqual(records["warnings"], [])
        self.assertEqual(records["parse_errors"], [])

    def test_valid_session_and_claim_records_are_loaded_and_linked(self) -> None:
        self.write_record(
            "5_tasks/records/sessions/AIPOS-23/session_AIPOS-23_001_dev.md",
            """---
task_id: AIPOS-23
session_id: session_AIPOS-23_001_dev
claim_id: claim_AIPOS-23_001_dev
session_status: running
created_at: 2026-04-28T10:00:00Z
---
Session body
""",
        )
        self.write_record(
            "5_tasks/records/claims/AIPOS-23/claim_AIPOS-23_001_dev.md",
            """---
task_id: AIPOS-23
claim_id: claim_AIPOS-23_001_dev
session_id: session_AIPOS-23_001_dev
claimed_by: dev.claude.cc_glm.local
claimed_at: 2026-04-28T09:59:00Z
claim_source: preview
---
Claim body
""",
        )

        records = load_records(self.repo_root)
        linked = find_records_for_task(records, "AIPOS-23")
        ref_report = check_task_record_refs(
            self.make_task(
                "AIPOS-23",
                claim_id="claim_AIPOS-23_001_dev",
                active_session_id="session_AIPOS-23_001_dev",
                last_session_id="session_AIPOS-23_001_dev",
            ),
            records,
        )

        self.assertEqual(records["summary"]["session_records"], 1)
        self.assertEqual(records["summary"]["claim_logs"], 1)
        self.assertEqual(records["summary"]["parse_errors"], 0)
        self.assertEqual(linked["sessions"][0]["session_status"], "running")
        self.assertEqual(linked["claims"][0]["claimed_by"], "dev.claude.cc_glm.local")
        self.assertEqual([item["status"] for item in ref_report["checks"]], ["ok", "ok", "ok"])
        self.assertEqual(ref_report["warnings"], [])
        self.assertEqual(ref_report["needs_owner_reasons"], [])

    def test_task_id_mismatch_is_reported_as_warning(self) -> None:
        self.write_record(
            "5_tasks/records/sessions/AIPOS-23/session_AIPOS-23_wrong_task.md",
            """---
task_id: OTHER-999
session_id: session_AIPOS-23_wrong_task
created_at: 2026-04-28T10:00:00Z
---
Session body
""",
        )

        records = load_records(self.repo_root)

        self.assertIn(
            "5_tasks/records/sessions/AIPOS-23/session_AIPOS-23_wrong_task.md: session record task_id mismatch: directory=AIPOS-23 metadata=OTHER-999",
            records["warnings"],
        )

    def test_duplicate_ids_are_reported(self) -> None:
        self.write_record(
            "5_tasks/records/sessions/AIPOS-23/session_a.md",
            """---
task_id: AIPOS-23
session_id: session_duplicate
created_at: 2026-04-28T10:00:00Z
---
One
""",
        )
        self.write_record(
            "5_tasks/records/sessions/AIPOS-24/session_b.md",
            """---
task_id: AIPOS-24
session_id: session_duplicate
created_at: 2026-04-28T11:00:00Z
---
Two
""",
        )
        self.write_record(
            "5_tasks/records/claims/AIPOS-23/claim_a.md",
            """---
task_id: AIPOS-23
claim_id: claim_duplicate
claimed_at: 2026-04-28T10:00:00Z
---
One
""",
        )
        self.write_record(
            "5_tasks/records/claims/AIPOS-24/claim_b.md",
            """---
task_id: AIPOS-24
claim_id: claim_duplicate
claimed_at: 2026-04-28T11:00:00Z
---
Two
""",
        )

        records = load_records(self.repo_root)

        self.assertIn("Duplicate session_id found: session_duplicate", records["warnings"])
        self.assertIn("Duplicate claim_id found: claim_duplicate", records["warnings"])

    def test_malformed_frontmatter_is_reported_without_failing_load(self) -> None:
        self.write_record(
            "5_tasks/records/sessions/AIPOS-23/session_broken.md",
            """---
task_id: AIPOS-23
session_id: session_broken
created_at: 2026-04-28T10:00:00Z
Broken body without closing frontmatter
""",
        )

        records = load_records(self.repo_root)

        self.assertEqual(records["summary"]["session_records"], 1)
        self.assertEqual(records["summary"]["parse_errors"], 1)
        self.assertIn(
            "5_tasks/records/sessions/AIPOS-23/session_broken.md: Frontmatter start found without closing delimiter",
            records["parse_errors"],
        )

    def test_record_reference_checks_cover_absent_missing_and_conflict_states(self) -> None:
        self.write_record(
            "5_tasks/records/sessions/AIPOS-24/session_conflict_a.md",
            """---
task_id: AIPOS-24
session_id: session_conflict
created_at: 2026-04-28T10:00:00Z
---
One
""",
        )
        self.write_record(
            "5_tasks/records/sessions/AIPOS-25/session_conflict_b.md",
            """---
task_id: AIPOS-25
session_id: session_conflict
created_at: 2026-04-28T11:00:00Z
---
Two
""",
        )

        records = load_records(self.repo_root)
        report = check_task_record_refs(
            self.make_task(
                "AIPOS-23",
                claim_id="claim_missing",
                active_session_id="session_conflict",
            ),
            records,
        )

        statuses = {item["reference"]: item["status"] for item in report["checks"]}
        levels = {item["reference"]: item["level"] for item in report["checks"]}

        self.assertEqual(statuses["claim_id"], "missing")
        self.assertEqual(statuses["active_session_id"], "conflict")
        self.assertEqual(statuses["last_session_id"], "absent")
        self.assertEqual(levels["claim_id"], "warn")
        self.assertEqual(levels["active_session_id"], "needs_owner")
        self.assertIn("claim_id references missing claim record", report["warnings"])
        self.assertIn(
            "active_session_id points to session record with mismatched task_id",
            report["needs_owner_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
