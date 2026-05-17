from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.adapter_response import ENVELOPE_FIELDS
from tools.aipos_cli.board_adapter import (
    claim_task,
    create_draft,
    get_context_pack_preview,
    get_health,
    get_queue,
    get_task,
    get_validate,
    publish_draft,
)


class BoardAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for queue_state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / queue_state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "0_control_plane" / "agents").mkdir(parents=True, exist_ok=True)

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
            "project: ai-project-os",
            f"assigned_to: {metadata.get('assigned_to', 'dev.codex.local')}",
            f"agent_instance: {metadata.get('agent_instance', 'dev.codex.local')}",
            "context_bundle: dev.codex.local",
            "task_mode: code",
            "model_tier: L2",
            "priority: medium",
            f"status: {metadata.get('status', queue_state)}",
            "created_by: tester",
            f"needs_owner: {str(metadata.get('needs_owner', False)).lower()}",
            "output_target: tools/aipos_cli/",
            "artifact_policy: formal_write",
            "session_policy: single_task_session",
            "context_isolation: strict",
            "artifact_scope: tools/aipos_cli/",
            "memory_scope: adapter tests",
            "---",
            "Task body",
            "",
        ]
        filename = str(metadata.get("filename", f"{task_id.lower()}.md"))
        return self.write_file(f"5_tasks/queue/{queue_state}/{filename}", "\n".join(lines))

    def draft_payload(self, task_id: str = "AIPOS-36-DRAFT") -> dict[str, object]:
        return {
            "frontmatter": {
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
            },
            "body": "## Goal\n\nAdapter dry-run.\n",
        }

    def assert_envelope(self, result: dict[str, object]) -> None:
        self.assertEqual(set(ENVELOPE_FIELDS), set(result.keys()))
        json.dumps(result)

    def test_health_response_is_json_serializable(self) -> None:
        result = get_health(repo_root=self.repo_root)

        self.assert_envelope(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "PASS")

    def test_queue_and_validate_do_not_mutate_repo(self) -> None:
        self.write_task("AIPOS-36-READ")
        before = sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))

        queue_result = get_queue(repo_root=self.repo_root)
        validate_result = get_validate(repo_root=self.repo_root)

        after = sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))
        self.assert_envelope(queue_result)
        self.assert_envelope(validate_result)
        self.assertEqual(before, after)

    def test_context_pack_preview_returns_read_only_envelope(self) -> None:
        self.write_task("AIPOS-78-CONTEXT")
        self.write_file(
            "3_context_bundles/examples/dev.codex.local.md",
            "\n".join(
                [
                    "role_instance: dev.codex.local",
                    "environment: local_wsl_ubuntu",
                    "description: local engineering agent",
                    "allowed_task_modes:",
                    "  - code",
                    "preferred_model_tiers:",
                    "  - L3",
                    "allowed_model_tiers:",
                    "  - L3",
                    "memory_access:",
                    "  - 2_projects/lybra/",
                    "output_target:",
                    "  - repository",
                    "escalation_rules:",
                    "  - preserve Owner gates",
                    "",
                ]
            ),
        )
        before = sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))

        result = get_context_pack_preview(task_id="AIPOS-78-CONTEXT", repo_root=self.repo_root)

        after = sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))
        self.assert_envelope(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["operation"], "context_pack_preview")
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["execute_allowed"])
        self.assertIsNone(result["dry_run_token"])
        self.assertEqual(result["planned_writes"], [])
        self.assertEqual(result["data"]["task"]["task_id"], "AIPOS-78-CONTEXT")
        self.assertTrue(result["data"]["context_bundle"]["found"])
        self.assertEqual(before, after)

    def test_invalid_task_lookup_returns_not_found(self) -> None:
        result = get_task(task_id="MISSING", repo_root=self.repo_root)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["category"], "NOT_FOUND")

    def test_absolute_path_rejected(self) -> None:
        result = get_task(path="/tmp/outside.md", repo_root=self.repo_root)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["category"], "PATH_UNSAFE")

    def test_path_traversal_rejected(self) -> None:
        result = publish_draft("../escape.md", repo_root=self.repo_root)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["category"], "PATH_UNSAFE")

    def test_create_draft_dry_run_does_not_write(self) -> None:
        result = create_draft(self.draft_payload(), repo_root=self.repo_root, dry_run=True)

        self.assert_envelope(result)
        self.assertTrue(result["ok"])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())
        self.assertEqual(result["performed_writes"], [])

    def test_create_draft_execute_is_blocked(self) -> None:
        result = create_draft(self.draft_payload(), repo_root=self.repo_root, dry_run=False)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertEqual(result["errors"][0]["category"], "DRY_RUN_REQUIRED")

    def test_publish_draft_dry_run_does_not_write_pending(self) -> None:
        create_draft(self.draft_payload("AIPOS-36-PUBLISH"), repo_root=self.repo_root, dry_run=True)
        self.write_file(
            "5_tasks/drafts/aipos-36-publish.md",
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-36-PUBLISH",
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

        result = publish_draft("5_tasks/drafts/aipos-36-publish.md", repo_root=self.repo_root, dry_run=True)

        self.assert_envelope(result)
        self.assertTrue(result["ok"])
        self.assertFalse((self.repo_root / "5_tasks/queue/pending/aipos-36-publish.md").exists())

    def test_publish_draft_execute_is_blocked(self) -> None:
        result = publish_draft("5_tasks/drafts/missing.md", repo_root=self.repo_root, dry_run=False)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["category"], "DRY_RUN_REQUIRED")

    def test_queue_claim_dry_run_returns_envelope(self) -> None:
        self.write_task("AIPOS-36-CLAIM")

        result = claim_task(task_id="AIPOS-36-CLAIM", actor="dev.codex.local", repo_root=self.repo_root, dry_run=True)

        self.assert_envelope(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["operation"], "queue_claim")
        self.assertEqual(result["performed_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks/queue/claimed/aipos-36-claim.md").exists())

    def test_queue_claim_execute_is_blocked(self) -> None:
        self.write_task("AIPOS-36-CLAIM-BLOCK")

        result = claim_task(task_id="AIPOS-36-CLAIM-BLOCK", actor="dev.codex.local", repo_root=self.repo_root, dry_run=False)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["category"], "DRY_RUN_REQUIRED")

    def test_adapter_module_does_not_require_subprocess(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "board_adapter.py").read_text(encoding="utf-8")

        self.assertNotIn("subprocess", source)
        self.assertNotIn("Popen", source)
        self.assertNotIn("shell=True", source)

    def test_adapter_error_normalization_catches_backend_exception(self) -> None:
        with patch("tools.aipos_cli.board_adapter.load_all_tasks", side_effect=RuntimeError("boom")):
            result = get_queue(repo_root=self.repo_root)

        self.assert_envelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["category"], "INTERNAL_ERROR")
