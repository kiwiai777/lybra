from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.draft_validator import list_drafts, validate_draft_file
from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.records import load_records


class DraftWriterTests(unittest.TestCase):
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

    def write_task(self, task_id: str, queue_state: str = "pending") -> Path:
        return self.write_file(
            f"5_tasks/queue/{queue_state}/{task_id.lower()}.md",
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    f"title: {task_id}",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "priority: medium",
                    f"status: {queue_state}",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/aipos_cli/",
                    "artifact_policy: formal_write",
                    "---",
                    "Queue body",
                    "",
                ]
            ),
        )

    def draft_metadata(self, task_id: str = "AIPOS-29-DRAFT") -> dict[str, object]:
        return {
            "task_id": task_id,
            "title": "Example Draft",
            "project": "ai-project-os",
            "assigned_to": "dev.codex.local",
            "agent_instance": "dev.codex.local",
            "context_bundle": "dev.codex.local",
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

    def test_dry_run_create_does_not_create_drafts_directory(self) -> None:
        result = create_draft(self.repo_root, self.draft_metadata(), "Body", dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["would_write"])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_create_from_json_writes_only_under_drafts(self) -> None:
        payload = {
            "frontmatter": self.draft_metadata("AIPOS-29-WRITE"),
            "body": "## Goal\n\nWrite me.\n",
        }
        json_path = self.write_file("payload.json", json.dumps(payload))

        exit_code, output = self.run_cli_json(["draft", "create", "--from-json", str(json_path), "--json"])
        draft_path = self.repo_root / "5_tasks" / "drafts" / "aipos-29-write.md"

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["verdict"], "PASS")
        self.assertTrue(output["wrote"])
        self.assertTrue(draft_path.exists())
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-29-write.md").exists())

    def test_duplicate_task_id_is_blocked(self) -> None:
        create_draft(self.repo_root, self.draft_metadata("AIPOS-29-DUPE"), "Body")
        result = create_draft(self.repo_root, self.draft_metadata("AIPOS-29-DUPE"), "Body")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Duplicate task_id already exists: AIPOS-29-DUPE", result["blocking_reasons"])

    def test_path_traversal_task_id_is_blocked(self) -> None:
        metadata = self.draft_metadata("../escape")
        result = create_draft(self.repo_root, metadata, "Body", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Invalid task_id format or path-unsafe task_id", result["blocking_reasons"])

    def test_validate_rejects_path_outside_drafts(self) -> None:
        outside_path = self.write_file("outside.md", "---\ntask_id: OUTSIDE\n---\n")

        with self.assertRaisesRegex(ValueError, "outside 5_tasks/drafts"):
            validate_draft_file(self.repo_root, outside_path)

    def test_validate_detects_collision_with_queue_task_id(self) -> None:
        self.write_task("AIPOS-29-COLLIDE", queue_state="pending")
        self.write_file(
            "5_tasks/drafts/aipos-29-collide.md",
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-29-COLLIDE",
                    "title: Example Draft",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/aipos_cli/",
                    "artifact_policy: formal_write",
                    "task_type: one_shot",
                    "polling_mode: agent_polling",
                    "claim_policy: assigned_agent_only",
                    "report_mode: forum_reply",
                    "recurrence: none",
                    "---",
                    "Body",
                    "",
                ]
            ),
        )

        result = validate_draft_file(self.repo_root, "5_tasks/drafts/aipos-29-collide.md")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Duplicate task_id already exists: AIPOS-29-COLLIDE", result["blocking_reasons"])

    def test_list_handles_missing_drafts_directory(self) -> None:
        result = list_drafts(self.repo_root)

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["drafts"], [])

    def test_json_output_is_valid_for_create_validate_and_list(self) -> None:
        payload = {
            "frontmatter": self.draft_metadata("AIPOS-29-JSON"),
            "body": "## Goal\n\nJSON body.\n",
        }
        json_path = self.write_file("payload.json", json.dumps(payload))

        create_exit, create_output = self.run_cli_json(
            ["draft", "create", "--from-json", str(json_path), "--dry-run", "--json"]
        )
        validate_write = create_draft(self.repo_root, self.draft_metadata("AIPOS-29-JSON"), "## Goal\n\nJSON body.\n")
        validate_exit, validate_output = self.run_cli_json(
            ["draft", "validate", "--path", "5_tasks/drafts/aipos-29-json.md", "--json"]
        )
        list_exit, list_output = self.run_cli_json(["draft", "list", "--json"])

        self.assertEqual(create_exit, 0)
        self.assertEqual(create_output["action"], "draft_create")
        self.assertTrue(create_output["dry_run"])
        self.assertIn("rendered_markdown", create_output)
        self.assertEqual(validate_write["verdict"], "PASS")
        self.assertEqual(validate_exit, 0)
        self.assertEqual(validate_output["action"], "draft_validate")
        self.assertEqual(validate_output["verdict"], "PASS")
        self.assertEqual(list_exit, 0)
        self.assertEqual(list_output["action"], "draft_list")
        self.assertEqual(list_output["total"], 1)

    def test_publish_dry_run_writes_nothing(self) -> None:
        create_draft(self.repo_root, self.draft_metadata("AIPOS-30-DRYRUN"), "## Goal\n\nDry run.\n")

        source_path = self.repo_root / "5_tasks" / "drafts" / "aipos-30-dryrun.md"
        before_source = source_path.read_text(encoding="utf-8")
        result = publish_draft(self.repo_root, "5_tasks/drafts/aipos-30-dryrun.md", dry_run=True)

        self.assertEqual(result["action"], "draft_publish")
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["would_write"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["target_path"], "5_tasks/queue/pending/aipos-30-dryrun.md")
        self.assertEqual(result["publish_record_path"], "5_tasks/records/publishes/AIPOS-30-DRYRUN/publish_aipos-30-dryrun.md")
        self.assertEqual(result["rendered_markdown"], before_source)
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-30-dryrun.md").exists())
        self.assertFalse(
            (self.repo_root / "5_tasks" / "records" / "publishes" / "AIPOS-30-DRYRUN" / "publish_aipos-30-dryrun.md").exists()
        )
        self.assertEqual(source_path.read_text(encoding="utf-8"), before_source)

    def test_publish_writes_pending_file_and_publish_record_under_temp_repo(self) -> None:
        create_draft(self.repo_root, self.draft_metadata("AIPOS-30-WRITE"), "## Goal\n\nPublish me.\n")

        source_path = self.repo_root / "5_tasks" / "drafts" / "aipos-30-write.md"
        source_text = source_path.read_text(encoding="utf-8")
        result = publish_draft(self.repo_root, "5_tasks/drafts/aipos-30-write.md", actor="agent-01")
        pending_path = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-30-write.md"
        publish_record_path = self.repo_root / "5_tasks" / "records" / "publishes" / "AIPOS-30-WRITE" / "publish_aipos-30-write.md"

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["wrote"])
        self.assertTrue(pending_path.exists())
        self.assertTrue(publish_record_path.exists())
        self.assertEqual(pending_path.read_text(encoding="utf-8"), source_text)
        self.assertEqual(source_path.read_text(encoding="utf-8"), source_text)
        records = load_records(self.repo_root)
        self.assertEqual(records["summary"]["publish_records"], 1)
        self.assertEqual(records["publishes"][0]["publish_id"], "publish_aipos-30-write")
        self.assertEqual(records["publishes"][0]["published_by"], "agent-01")
        self.assertEqual(records["publishes"][0]["source_draft_ref"], "5_tasks/drafts/aipos-30-write.md")

    def test_publish_external_intake_draft_converts_to_execution_handoff(self) -> None:
        draft_path = self.write_file(
            "5_tasks/drafts/external_intake/sample.md",
            "\n".join(
                [
                    "---",
                    "task_id: EXT-ACME-1234",
                    "title: 'Review external intake: Build the demo tool'",
                    "project: acme_client",
                    "task_type: one_shot",
                    "assigned_to: planner",
                    "agent_instance: planner",
                    "context_bundle: external_intake",
                    "task_mode: planning",
                    "model_tier: L3",
                    "priority: low",
                    "status: pending",
                    "created_by: bot.local",
                    "needs_owner: true",
                    "output_target: 5_tasks/drafts/external_intake",
                    "artifact_policy: draft_only",
                    "polling_mode: manual_owner_review",
                    "claim_policy: owner_review_required",
                    "report_mode: completion_summary",
                    "recurrence: none",
                    "draft_id: external_intake_sample",
                    "draft_status: draft",
                    "draft_created_by: bot.local",
                    "draft_created_at: 2026-05-29T00:00:00Z",
                    "draft_updated_at: 2026-05-29T00:00:00Z",
                    "draft_publish_target: 5_tasks/queue/pending/",
                    "source_tag: wechat_bot",
                    "client_tag: acme_client",
                    "external_ref: 'wechat:msg-1001'",
                    "---",
                    "## Normalized Request",
                    "",
                    "Build a tiny demo tool.",
                    "",
                ]
            ),
        )

        dry = publish_draft(self.repo_root, draft_path.relative_to(self.repo_root), dry_run=True)
        self.assertEqual(dry["verdict"], "PASS")
        self.assertIn("assigned_to: agent-01", dry["rendered_markdown"])
        self.assertIn("agent_instance: agent-01", dry["rendered_markdown"])
        self.assertIn("context_bundle: external_intake_execution", dry["rendered_markdown"])
        self.assertIn("task_mode: coding", dry["rendered_markdown"])
        self.assertIn("needs_owner: false", dry["rendered_markdown"])
        self.assertIn("output_target: workspace_artifacts/external_intake", dry["rendered_markdown"])
        self.assertIn("artifact_policy: formal_write", dry["rendered_markdown"])
        self.assertIn("title: Build the demo tool", dry["rendered_markdown"])

        result = publish_draft(self.repo_root, draft_path.relative_to(self.repo_root))
        pending = self.repo_root / "5_tasks/queue/pending/ext-acme-1234.md"
        self.assertTrue(result["wrote"])
        self.assertTrue(pending.exists())
        text = pending.read_text(encoding="utf-8")
        self.assertIn("assigned_to: agent-01", text)
        self.assertIn("needs_owner: false", text)

    def test_publish_blocks_when_validation_fails(self) -> None:
        self.write_file(
            "5_tasks/drafts/aipos-30-invalid.md",
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-30-INVALID",
                    "title: Invalid Draft",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "priority: medium",
                    "status: claimed",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/aipos_cli/",
                    "artifact_policy: formal_write",
                    "---",
                    "Body",
                    "",
                ]
            ),
        )

        result = publish_draft(self.repo_root, "5_tasks/drafts/aipos-30-invalid.md", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertFalse(result["would_write"])
        self.assertIn("Draft status must be pending", result["blocking_reasons"])

    def test_publish_blocks_duplicate_task_id_in_pending(self) -> None:
        create_draft(self.repo_root, self.draft_metadata("AIPOS-30-DUPE-PENDING"), "Body")
        self.write_task("AIPOS-30-DUPE-PENDING", queue_state="pending")

        result = publish_draft(self.repo_root, "5_tasks/drafts/aipos-30-dupe-pending.md", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Duplicate task_id already exists: AIPOS-30-DUPE-PENDING", result["blocking_reasons"])

    def test_publish_blocks_duplicate_task_id_in_other_queue_states(self) -> None:
        for queue_state in ("claimed", "completed", "blocked"):
            task_id = f"AIPOS-30-DUPE-{queue_state.upper()}"
            create_draft(self.repo_root, self.draft_metadata(task_id), "Body")
            self.write_task(task_id, queue_state=queue_state)

            result = publish_draft(self.repo_root, f"5_tasks/drafts/{task_id.lower()}.md", dry_run=True)

            self.assertEqual(result["verdict"], "BLOCK")
            self.assertIn(f"Duplicate task_id already exists: {task_id}", result["blocking_reasons"])

    def test_publish_rejects_source_outside_drafts(self) -> None:
        outside_path = self.write_file("outside.md", "---\ntask_id: OUTSIDE\n---\n")

        with self.assertRaisesRegex(ValueError, "outside 5_tasks/drafts"):
            publish_draft(self.repo_root, outside_path, dry_run=True)

    def test_publish_rejects_path_traversal(self) -> None:
        self.write_file("outside.md", "---\ntask_id: OUTSIDE\n---\n")

        with self.assertRaisesRegex(ValueError, "outside 5_tasks/drafts"):
            publish_draft(self.repo_root, "5_tasks/drafts/../../outside.md", dry_run=True)

    def test_publish_rejects_non_markdown_file(self) -> None:
        self.write_file("5_tasks/drafts/not-markdown.txt", "task_id: TXT")

        with self.assertRaisesRegex(ValueError, "not a markdown file"):
            publish_draft(self.repo_root, "5_tasks/drafts/not-markdown.txt", dry_run=True)

    def test_publish_never_overwrites_existing_pending_file(self) -> None:
        create_draft(self.repo_root, self.draft_metadata("AIPOS-30-EXISTING"), "Body")
        pending_path = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-30-existing.md"
        pending_path.write_text("existing", encoding="utf-8")

        result = publish_draft(self.repo_root, "5_tasks/drafts/aipos-30-existing.md")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertFalse(result["wrote"])
        self.assertIn("Pending target already exists: 5_tasks/queue/pending/aipos-30-existing.md", result["blocking_reasons"])
        self.assertEqual(pending_path.read_text(encoding="utf-8"), "existing")

    def test_publish_json_output_is_valid_for_dry_run_and_write(self) -> None:
        create_draft(self.repo_root, self.draft_metadata("AIPOS-30-JSON"), "## Goal\n\nJSON publish.\n")

        dry_exit, dry_output = self.run_cli_json(
            ["draft", "publish", "--path", "5_tasks/drafts/aipos-30-json.md", "--dry-run", "--json"]
        )
        write_exit, write_output = self.run_cli_json(
            ["draft", "publish", "--path", "5_tasks/drafts/aipos-30-json.md", "--json"]
        )

        self.assertEqual(dry_exit, 0)
        self.assertEqual(dry_output["action"], "draft_publish")
        self.assertTrue(dry_output["dry_run"])
        self.assertTrue(dry_output["would_write"])
        self.assertFalse(dry_output["wrote"])
        self.assertIn("rendered_markdown", dry_output)
        self.assertEqual(write_exit, 0)
        self.assertEqual(write_output["action"], "draft_publish")
        self.assertFalse(write_output["dry_run"])
        self.assertTrue(write_output["would_write"])
        self.assertTrue(write_output["wrote"])


if __name__ == "__main__":
    unittest.main()
