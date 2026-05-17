from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.queue_mutation import mutate_queue_task
from tools.aipos_cli.records import load_records


class RecordWriterTests(unittest.TestCase):
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

    def write_task(self, task_id: str, queue_state: str = "pending", **metadata: object) -> Path:
        lines = [
            "---",
            f"task_id: {task_id}",
            f"title: {metadata.get('title', task_id)}",
            f"project: {metadata.get('project', 'ai-project-os')}",
            f"assigned_to: {metadata.get('assigned_to', 'dev.codex.local')}",
            f"agent_instance: {metadata.get('agent_instance', 'dev.codex.local')}",
            f"context_bundle: {metadata.get('context_bundle', 'dev.codex.local')}",
            f"task_mode: {metadata.get('task_mode', 'code')}",
            f"model_tier: {metadata.get('model_tier', 'L2')}",
            f"priority: {metadata.get('priority', 'high')}",
            f"status: {metadata.get('status', queue_state)}",
            f"created_by: {metadata.get('created_by', 'tester')}",
            f"needs_owner: {str(metadata.get('needs_owner', False)).lower()}",
            f"output_target: {metadata.get('output_target', 'tools/aipos_cli/')}",
            f"artifact_policy: {metadata.get('artifact_policy', 'formal_write')}",
            f"session_policy: {metadata.get('session_policy', 'single_task_session')}",
            f"context_isolation: {metadata.get('context_isolation', 'strict')}",
            f"artifact_scope: {metadata.get('artifact_scope', 'tools/aipos_cli/')}",
            f"memory_scope: {metadata.get('memory_scope', 'record writer testing')}",
        ]
        for key in (
            "claim_id",
            "active_session_id",
            "last_session_id",
            "claimed_by",
            "claimed_at",
            "blocked_by",
            "blocked_at",
            "block_reason",
            "completed_by",
            "completed_at",
            "reopened_by",
            "reopened_at",
            "reopen_reason",
        ):
            if key in metadata and metadata[key] is not None:
                lines.append(f"{key}: {metadata[key]}")
        lines.extend(["---", "Task body", ""])
        return self.write_file(f"5_tasks/queue/{queue_state}/{task_id.lower()}.md", "\n".join(lines))

    def test_claim_with_records_dry_run_writes_nothing_and_creates_no_records_dir(self) -> None:
        source = self.write_task("AIPOS-32-DRYRUN")
        before = source.read_text(encoding="utf-8")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-DRYRUN", actor="dev.codex.local", dry_run=True, with_records=True)

        self.assertTrue(result["with_records"])
        self.assertTrue(result["records_enabled"])
        self.assertFalse(result["wrote"])
        self.assertFalse(result["moved"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)
        self.assertFalse((self.repo_root / "5_tasks/records").exists())
        self.assertIn("record_writes", result)
        self.assertIn("record_previews", result)

    def test_claim_with_records_creates_claim_log_and_session_record(self) -> None:
        self.write_task("AIPOS-32-CLAIM")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-CLAIM", actor="dev.codex.local", with_records=True)
        claimed_task = self.repo_root / "5_tasks/queue/claimed/aipos-32-claim.md"
        records = load_records(self.repo_root)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(claimed_task.exists())
        self.assertTrue((self.repo_root / result["claim_log_path"]).exists())
        self.assertTrue((self.repo_root / result["session_record_path"]).exists())
        text = claimed_task.read_text(encoding="utf-8")
        self.assertIn(f"claim_id: {result['proposed_claim_id']}", text)
        self.assertIn(f"active_session_id: {result['proposed_session_id']}", text)
        self.assertEqual(records["summary"]["claim_logs"], 1)
        self.assertEqual(records["summary"]["session_records"], 1)
        self.assertEqual(records["claims"][0]["claim_id"], result["proposed_claim_id"])
        self.assertEqual(records["sessions"][0]["session_id"], result["proposed_session_id"])

    def test_claim_with_records_blocks_if_claim_log_target_exists(self) -> None:
        self.write_task("AIPOS-32-CLAIM-EXISTS")
        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-CLAIM-EXISTS", actor="dev.codex.local", dry_run=True, with_records=True)
        self.write_file(result["claim_log_path"], "existing")

        blocked = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-CLAIM-EXISTS", actor="dev.codex.local", dry_run=True, with_records=True)

        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertTrue(any("Claim log already exists" in item for item in blocked["blocking_reasons"]))

    def test_claim_with_records_blocks_if_session_record_target_exists(self) -> None:
        self.write_task("AIPOS-32-SESSION-EXISTS")
        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-SESSION-EXISTS", actor="dev.codex.local", dry_run=True, with_records=True)
        self.write_file(result["session_record_path"], "existing")

        blocked = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-SESSION-EXISTS", actor="dev.codex.local", dry_run=True, with_records=True)

        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertTrue(any("Session record already exists" in item for item in blocked["blocking_reasons"]))

    def test_claim_without_records_preserves_aipos31_behavior(self) -> None:
        self.write_task("AIPOS-32-NO-RECORDS")
        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-NO-RECORDS", actor="dev.codex.local", dry_run=True)
        self.assertFalse(result["with_records"])
        self.assertFalse(result["records_enabled"])
        self.assertNotIn("proposed_claim_id", result)

    def test_block_with_records_updates_existing_session_record(self) -> None:
        self.write_task("AIPOS-32-BLOCK")
        claim_result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-BLOCK", actor="dev.codex.local", with_records=True)

        blocked = mutate_queue_task(self.repo_root, "block", task_id="AIPOS-32-BLOCK", actor="dev.codex.local", reason="Waiting", with_records=True)
        session_text = (self.repo_root / blocked["session_record_path"]).read_text(encoding="utf-8")

        self.assertEqual(blocked["verdict"], "NEEDS_OWNER")
        self.assertIn("status: blocked", session_text)
        self.assertIn("current_state: blocked", session_text)
        self.assertIn("blocked by dev.codex.local: Waiting", session_text)
        self.assertTrue((self.repo_root / claim_result["claim_log_path"]).exists())

    def test_block_with_records_blocks_when_active_session_id_missing(self) -> None:
        self.write_task("AIPOS-32-BLOCK-MISSING", queue_state="claimed", claim_id="claim_AIPOS-32-BLOCK-MISSING_1_dev", claimed_by="dev.codex.local", claimed_at="2026-04-30T00:00:00Z")
        result = mutate_queue_task(self.repo_root, "block", task_id="AIPOS-32-BLOCK-MISSING", actor="dev.codex.local", reason="Waiting", dry_run=True, with_records=True)
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertTrue(any("requires active_session_id" in item for item in result["blocking_reasons"]))

    def test_complete_with_records_updates_existing_session_record(self) -> None:
        self.write_task("AIPOS-32-COMPLETE")
        mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-COMPLETE", actor="dev.codex.local", with_records=True)
        result = mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-32-COMPLETE", actor="dev.codex.local", report_link="https://example.com/report", with_records=True)
        session_text = (self.repo_root / result["session_record_path"]).read_text(encoding="utf-8")
        self.assertEqual(result["verdict"], "PASS")
        self.assertIn("status: completed", session_text)
        self.assertIn("completed by dev.codex.local: https://example.com/report", session_text)

    def test_complete_with_records_blocks_when_active_session_id_missing(self) -> None:
        self.write_task("AIPOS-32-COMPLETE-MISSING", queue_state="claimed", claim_id="claim_AIPOS-32-COMPLETE-MISSING_1_dev", claimed_by="dev.codex.local", claimed_at="2026-04-30T00:00:00Z")
        result = mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-32-COMPLETE-MISSING", actor="dev.codex.local", report_link="https://example.com/report", dry_run=True, with_records=True)
        self.assertEqual(result["verdict"], "BLOCK")

    def test_reopen_with_records_updates_session_when_reference_exists(self) -> None:
        self.write_task("AIPOS-32-REOPEN")
        mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-REOPEN", actor="dev.codex.local", with_records=True)
        mutate_queue_task(self.repo_root, "block", task_id="AIPOS-32-REOPEN", actor="dev.codex.local", reason="Waiting", with_records=True)
        result = mutate_queue_task(self.repo_root, "reopen", task_id="AIPOS-32-REOPEN", actor="dev.codex.local", reason="Unblocked", with_records=True)
        session_text = (self.repo_root / result["session_record_path"]).read_text(encoding="utf-8")
        self.assertEqual(result["verdict"], "PASS")
        self.assertIn("status: reopened", session_text)
        self.assertIn("reopened by dev.codex.local: Unblocked", session_text)

    def test_reopen_with_records_warns_when_no_session_reference_exists(self) -> None:
        self.write_task("AIPOS-32-REOPEN-WARN", queue_state="blocked", blocked_by="dev.codex.local", blocked_at="2026-04-30T00:00:00Z", block_reason="Waiting")
        result = mutate_queue_task(self.repo_root, "reopen", task_id="AIPOS-32-REOPEN-WARN", actor="dev.codex.local", reason="Unblocked", dry_run=True, with_records=True)
        self.assertEqual(result["verdict"], "WARN")
        self.assertTrue(any("no active_session_id or last_session_id" in item for item in result["warnings"]))

    def test_record_paths_reject_unsafe_task_id_and_actor_slug(self) -> None:
        self.write_task("AIPOS-32-UNSAFE")
        with self.assertRaisesRegex(ValueError, "safe slug"):
            mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-UNSAFE", actor="!!!", dry_run=True, with_records=True)
        self.write_task("BAD/ID", queue_state="pending")
        with self.assertRaisesRegex(ValueError, "Unsafe task_id"):
            mutate_queue_task(self.repo_root, "claim", task_path="5_tasks/queue/pending/bad/id.md", actor="dev.codex.local", dry_run=True, with_records=True)

    def test_non_dry_run_json_shape_is_record_reader_compatible(self) -> None:
        self.write_task("AIPOS-32-READER")
        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-32-READER", actor="dev.codex.local", with_records=True)
        records = load_records(self.repo_root)
        self.assertTrue(result["record_writes"][0]["wrote"])
        self.assertTrue(result["record_writes"][1]["wrote"])
        self.assertEqual(records["claims"][0]["claimed_by"], "dev.codex.local")
        self.assertEqual(records["sessions"][0]["session_status"], "active")


if __name__ == "__main__":
    unittest.main()
