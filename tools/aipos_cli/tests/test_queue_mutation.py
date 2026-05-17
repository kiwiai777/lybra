from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.queue_mutation import mutate_queue_task


class QueueMutationTests(unittest.TestCase):
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
            f"memory_scope: {metadata.get('memory_scope', 'queue mutation testing')}",
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
        if "artifact_links" in metadata and metadata["artifact_links"] is not None:
            lines.append("artifact_links:")
            for item in metadata["artifact_links"]:  # type: ignore[index]
                lines.append(f"- {item}")
        lines.extend(["---", "Task body", ""])
        filename = str(metadata.get("filename", f"{task_id.lower()}.md"))
        return self.write_file(f"5_tasks/queue/{queue_state}/{filename}", "\n".join(lines))

    def run_cli_json(self, argv: list[str]) -> tuple[int, dict[str, object]]:
        previous_cwd = Path.cwd()
        stdout = io.StringIO()
        try:
            os.chdir(self.repo_root)
            with redirect_stdout(stdout):
                exit_code = main(argv)
        finally:
            os.chdir(previous_cwd)
        return exit_code, json.loads(stdout.getvalue())

    def test_claim_dry_run_writes_nothing_and_moves_nothing(self) -> None:
        source = self.write_task("AIPOS-31-CLAIM")
        before = source.read_text(encoding="utf-8")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-CLAIM", actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["would_write"])
        self.assertTrue(result["would_move"])
        self.assertFalse(result["wrote"])
        self.assertFalse(result["moved"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)
        self.assertFalse((self.repo_root / "5_tasks/queue/claimed/aipos-31-claim.md").exists())

    def test_claim_pending_to_claimed_writes_runtime_fields(self) -> None:
        self.write_task("AIPOS-31-CLAIM-WRITE")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-CLAIM-WRITE", actor="dev.codex.local")
        target = self.repo_root / "5_tasks/queue/claimed/aipos-31-claim-write.md"

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["wrote"])
        self.assertTrue(result["moved"])
        self.assertTrue(target.exists())
        text = target.read_text(encoding="utf-8")
        self.assertIn("status: claimed", text)
        self.assertIn("claimed_by: dev.codex.local", text)
        self.assertIn("claimed_at:", text)
        self.assertIn("claim_id: claim_AIPOS-31-CLAIM-WRITE_", text)
        self.assertIn("active_session_id: session_AIPOS-31-CLAIM-WRITE_", text)
        self.assertFalse((self.repo_root / "5_tasks/queue/pending/aipos-31-claim-write.md").exists())

    def test_claim_blocks_actor_mismatch(self) -> None:
        self.write_task("AIPOS-31-MISMATCH", assigned_to="other.actor.local", agent_instance="other.actor.local")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-MISMATCH", actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Current actor does not match assigned_to or agent_instance", result["blocking_reasons"])

    def test_claim_blocks_directory_status_mismatch(self) -> None:
        self.write_task("AIPOS-31-MISMATCH-STATE", queue_state="pending", status="claimed")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-MISMATCH-STATE", actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertTrue(any("Queue directory does not match frontmatter status" in item for item in result["blocking_reasons"]))

    def test_claim_blocks_duplicate_target_filename(self) -> None:
        self.write_task("AIPOS-31-TARGET", queue_state="pending", filename="shared.md")
        self.write_task("OTHER-TASK", queue_state="claimed", filename="SHARED.md", claim_id="claim_OTHER-TASK_1_x", active_session_id="session_OTHER-TASK_1_x", claimed_by="dev.codex.local", claimed_at="2026-04-30T00:00:00Z")

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-TARGET", actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertTrue(any("Case-insensitive target filename collision" in item for item in result["blocking_reasons"]))

    def test_claim_blocks_task_id_not_found(self) -> None:
        with self.assertRaisesRegex(ValueError, "No task found for task_id"):
            mutate_queue_task(self.repo_root, "claim", task_id="MISSING", actor="dev.codex.local", dry_run=True)

    def test_claim_blocks_duplicate_task_id_matches(self) -> None:
        self.write_task("AIPOS-31-DUPE", queue_state="pending", filename="one.md")
        self.write_task("AIPOS-31-DUPE", queue_state="blocked", filename="two.md", blocked_by="dev.codex.local", blocked_at="2026-04-30T00:00:00Z", block_reason="waiting", last_session_id="session_AIPOS-31-DUPE_1_x")

        with self.assertRaisesRegex(ValueError, "Duplicate task_id AIPOS-31-DUPE found in"):
            mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-DUPE", actor="dev.codex.local", dry_run=True)

    def test_block_dry_run_writes_nothing_and_moves_nothing(self) -> None:
        source = self.write_task(
            "AIPOS-31-BLOCK",
            queue_state="claimed",
            claim_id="claim_AIPOS-31-BLOCK_1_dev",
            active_session_id="session_AIPOS-31-BLOCK_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )
        before = source.read_text(encoding="utf-8")

        result = mutate_queue_task(self.repo_root, "block", task_id="AIPOS-31-BLOCK", actor="dev.codex.local", reason="Need input", dry_run=True)

        self.assertEqual(result["verdict"], "NEEDS_OWNER")
        self.assertFalse(result["wrote"])
        self.assertFalse(result["moved"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)

    def test_block_claimed_to_blocked_writes_required_fields(self) -> None:
        self.write_task(
            "AIPOS-31-BLOCK-WRITE",
            queue_state="claimed",
            claim_id="claim_AIPOS-31-BLOCK-WRITE_1_dev",
            active_session_id="session_AIPOS-31-BLOCK-WRITE_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        result = mutate_queue_task(self.repo_root, "block", task_id="AIPOS-31-BLOCK-WRITE", actor="dev.codex.local", reason="Waiting on owner")
        target = self.repo_root / "5_tasks/queue/blocked/aipos-31-block-write.md"
        text = target.read_text(encoding="utf-8")

        self.assertEqual(result["verdict"], "NEEDS_OWNER")
        self.assertTrue(result["wrote"])
        self.assertIn("status: blocked", text)
        self.assertIn("blocked_by: dev.codex.local", text)
        self.assertIn("blocked_at:", text)
        self.assertIn("block_reason: Waiting on owner", text)
        self.assertIn("needs_owner: true", text)
        self.assertIn("last_session_id: session_AIPOS-31-BLOCK-WRITE_1_dev", text)
        self.assertNotIn("active_session_id:", text)

    def test_block_requires_non_empty_reason(self) -> None:
        self.write_task(
            "AIPOS-31-BLOCK-REASON",
            queue_state="claimed",
            claim_id="claim_AIPOS-31-BLOCK-REASON_1_dev",
            active_session_id="session_AIPOS-31-BLOCK-REASON_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        with self.assertRaisesRegex(ValueError, "reason is required"):
            mutate_queue_task(self.repo_root, "block", task_id="AIPOS-31-BLOCK-REASON", actor="dev.codex.local", reason=" ", dry_run=True)

    def test_block_rejects_pending_source(self) -> None:
        self.write_task("AIPOS-31-BLOCK-PENDING", queue_state="pending")

        result = mutate_queue_task(self.repo_root, "block", task_id="AIPOS-31-BLOCK-PENDING", actor="dev.codex.local", reason="Nope", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertTrue(any("expected source state claimed" in item for item in result["blocking_reasons"]))

    def test_complete_dry_run_writes_nothing_and_moves_nothing(self) -> None:
        source = self.write_task(
            "AIPOS-31-COMPLETE",
            queue_state="claimed",
            claim_id="claim_AIPOS-31-COMPLETE_1_dev",
            active_session_id="session_AIPOS-31-COMPLETE_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )
        before = source.read_text(encoding="utf-8")

        result = mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-31-COMPLETE", actor="dev.codex.local", report_link="https://example.com/report", dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertFalse(result["wrote"])
        self.assertFalse(result["moved"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)

    def test_complete_claimed_to_completed_writes_required_fields(self) -> None:
        self.write_task(
            "AIPOS-31-COMPLETE-WRITE",
            queue_state="claimed",
            claim_id="claim_AIPOS-31-COMPLETE-WRITE_1_dev",
            active_session_id="session_AIPOS-31-COMPLETE-WRITE_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        result = mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-31-COMPLETE-WRITE", actor="dev.codex.local", report_link="https://example.com/report")
        target = self.repo_root / "5_tasks/queue/completed/aipos-31-complete-write.md"
        text = target.read_text(encoding="utf-8")

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["wrote"])
        self.assertIn("status: completed", text)
        self.assertIn("completed_by: dev.codex.local", text)
        self.assertIn("completed_at:", text)
        self.assertIn("last_session_id: session_AIPOS-31-COMPLETE-WRITE_1_dev", text)
        self.assertIn("artifact_links:", text)
        self.assertIn("- 'https://example.com/report'", text)
        self.assertNotIn("active_session_id:", text)

    def test_complete_requires_non_empty_report_link(self) -> None:
        self.write_task(
            "AIPOS-31-COMPLETE-REPORT",
            queue_state="claimed",
            claim_id="claim_AIPOS-31-COMPLETE-REPORT_1_dev",
            active_session_id="session_AIPOS-31-COMPLETE-REPORT_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        with self.assertRaisesRegex(ValueError, "report-link is required"):
            mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-31-COMPLETE-REPORT", actor="dev.codex.local", report_link=" ", dry_run=True)

    def test_complete_rejects_pending_source(self) -> None:
        self.write_task("AIPOS-31-COMPLETE-PENDING")
        result = mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-31-COMPLETE-PENDING", actor="dev.codex.local", report_link="https://example.com/report", dry_run=True)
        self.assertEqual(result["verdict"], "BLOCK")

    def test_complete_rejects_blocked_source(self) -> None:
        self.write_task("AIPOS-31-COMPLETE-BLOCKED", queue_state="blocked", blocked_by="dev.codex.local", blocked_at="2026-04-30T00:00:00Z", block_reason="stop", last_session_id="session_AIPOS-31-COMPLETE-BLOCKED_1_dev")
        result = mutate_queue_task(self.repo_root, "complete", task_id="AIPOS-31-COMPLETE-BLOCKED", actor="dev.codex.local", report_link="https://example.com/report", dry_run=True)
        self.assertEqual(result["verdict"], "BLOCK")

    def test_reopen_dry_run_writes_nothing_and_moves_nothing(self) -> None:
        source = self.write_task(
            "AIPOS-31-REOPEN",
            queue_state="blocked",
            claim_id="claim_AIPOS-31-REOPEN_1_dev",
            blocked_by="dev.codex.local",
            blocked_at="2026-04-30T00:00:00Z",
            block_reason="waiting",
            last_session_id="session_AIPOS-31-REOPEN_1_dev",
        )
        before = source.read_text(encoding="utf-8")

        result = mutate_queue_task(self.repo_root, "reopen", task_id="AIPOS-31-REOPEN", actor="dev.codex.local", reason="Input arrived", dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertFalse(result["wrote"])
        self.assertFalse(result["moved"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)

    def test_reopen_blocked_to_pending_writes_required_fields(self) -> None:
        self.write_task(
            "AIPOS-31-REOPEN-WRITE",
            queue_state="blocked",
            claim_id="claim_AIPOS-31-REOPEN-WRITE_1_dev",
            blocked_by="dev.codex.local",
            blocked_at="2026-04-30T00:00:00Z",
            block_reason="waiting",
            needs_owner=True,
            last_session_id="session_AIPOS-31-REOPEN-WRITE_1_dev",
        )

        result = mutate_queue_task(self.repo_root, "reopen", task_id="AIPOS-31-REOPEN-WRITE", actor="dev.codex.local", reason="Owner answered")
        target = self.repo_root / "5_tasks/queue/pending/aipos-31-reopen-write.md"
        text = target.read_text(encoding="utf-8")

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["wrote"])
        self.assertIn("status: pending", text)
        self.assertIn("reopened_by: dev.codex.local", text)
        self.assertIn("reopened_at:", text)
        self.assertIn("reopen_reason: Owner answered", text)
        self.assertIn("needs_owner: false", text)
        self.assertNotIn("claim_id:", text)

    def test_reopen_requires_non_empty_reason(self) -> None:
        self.write_task("AIPOS-31-REOPEN-REASON", queue_state="blocked", blocked_by="dev.codex.local", blocked_at="2026-04-30T00:00:00Z", block_reason="waiting", last_session_id="session_AIPOS-31-REOPEN-REASON_1_dev")

        with self.assertRaisesRegex(ValueError, "reason is required"):
            mutate_queue_task(self.repo_root, "reopen", task_id="AIPOS-31-REOPEN-REASON", actor="dev.codex.local", reason=" ", dry_run=True)

    def test_reopen_rejects_completed_source(self) -> None:
        self.write_task("AIPOS-31-REOPEN-COMPLETED", queue_state="completed", completed_by="dev.codex.local", completed_at="2026-04-30T00:00:00Z", last_session_id="session_AIPOS-31-REOPEN-COMPLETED_1_dev")
        result = mutate_queue_task(self.repo_root, "reopen", task_id="AIPOS-31-REOPEN-COMPLETED", actor="dev.codex.local", reason="Nope", dry_run=True)
        self.assertEqual(result["verdict"], "BLOCK")

    def test_path_traversal_outside_queue_non_markdown_and_missing_file_are_rejected(self) -> None:
        self.write_file("outside.md", "x")
        self.write_file("5_tasks/queue/pending/not-markdown.txt", "x")
        with self.assertRaisesRegex(ValueError, "outside 5_tasks/queue"):
            mutate_queue_task(self.repo_root, "claim", task_path="5_tasks/queue/pending/../../outside.md", actor="dev.codex.local", dry_run=True)
        with self.assertRaisesRegex(ValueError, "outside 5_tasks/queue"):
            mutate_queue_task(self.repo_root, "claim", task_path="outside.md", actor="dev.codex.local", dry_run=True)
        with self.assertRaisesRegex(ValueError, "not a markdown file"):
            mutate_queue_task(self.repo_root, "claim", task_path="5_tasks/queue/pending/not-markdown.txt", actor="dev.codex.local", dry_run=True)
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            mutate_queue_task(self.repo_root, "claim", task_path="5_tasks/queue/pending/missing.md", actor="dev.codex.local", dry_run=True)

    def test_no_overwrite_is_enforced(self) -> None:
        self.write_task("AIPOS-31-NO-OVERWRITE", queue_state="pending")
        self.write_task(
            "OTHER-COMPLETE",
            queue_state="claimed",
            filename="aipos-31-no-overwrite.md",
            claim_id="claim_OTHER-COMPLETE_1_dev",
            active_session_id="session_OTHER-COMPLETE_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        result = mutate_queue_task(self.repo_root, "claim", task_id="AIPOS-31-NO-OVERWRITE", actor="dev.codex.local", dry_run=True)
        self.assertEqual(result["verdict"], "BLOCK")

    def test_json_output_is_valid_for_all_mutations(self) -> None:
        self.write_task("AIPOS-31-JSON-CLAIM")
        self.write_task("AIPOS-31-JSON-BLOCK", queue_state="claimed", claim_id="claim_AIPOS-31-JSON-BLOCK_1_dev", active_session_id="session_AIPOS-31-JSON-BLOCK_1_dev", claimed_by="dev.codex.local", claimed_at="2026-04-30T00:00:00Z")
        self.write_task("AIPOS-31-JSON-COMPLETE", queue_state="claimed", claim_id="claim_AIPOS-31-JSON-COMPLETE_1_dev", active_session_id="session_AIPOS-31-JSON-COMPLETE_1_dev", claimed_by="dev.codex.local", claimed_at="2026-04-30T00:00:00Z")
        self.write_task("AIPOS-31-JSON-REOPEN", queue_state="blocked", blocked_by="dev.codex.local", blocked_at="2026-04-30T00:00:00Z", block_reason="waiting", last_session_id="session_AIPOS-31-JSON-REOPEN_1_dev")

        claim_exit, claim_output = self.run_cli_json(["queue", "claim", "--task-id", "AIPOS-31-JSON-CLAIM", "--actor", "dev.codex.local", "--dry-run", "--json"])
        block_exit, block_output = self.run_cli_json(["queue", "block", "--task-id", "AIPOS-31-JSON-BLOCK", "--actor", "dev.codex.local", "--reason", "waiting", "--dry-run", "--json"])
        complete_exit, complete_output = self.run_cli_json(["queue", "complete", "--task-id", "AIPOS-31-JSON-COMPLETE", "--actor", "dev.codex.local", "--report-link", "https://example.com/report", "--dry-run", "--json"])
        reopen_exit, reopen_output = self.run_cli_json(["queue", "reopen", "--task-id", "AIPOS-31-JSON-REOPEN", "--actor", "dev.codex.local", "--reason", "answered", "--dry-run", "--json"])

        self.assertEqual(claim_exit, 0)
        self.assertEqual(block_exit, 0)
        self.assertEqual(complete_exit, 0)
        self.assertEqual(reopen_exit, 0)
        self.assertEqual(claim_output["action"], "queue_claim")
        self.assertEqual(block_output["action"], "queue_block")
        self.assertEqual(complete_output["action"], "queue_complete")
        self.assertEqual(reopen_output["action"], "queue_reopen")
        self.assertIn("rendered_markdown", claim_output)
        self.assertIn("safety_notice", reopen_output)

    def test_existing_read_only_queue_command_still_works(self) -> None:
        self.write_task("AIPOS-31-READONLY")
        exit_code, output = self.run_cli_json(["queue", "--json"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(output["scope"], "queue")


if __name__ == "__main__":
    unittest.main()
