from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import (
    append_orchestration_event,
    append_planner_iteration,
    claim_task,
    create_draft,
    execute_dry_run,
    publish_draft,
)
from tools.aipos_cli.controlled_execute import get_dry_run


class ControlledExecuteTests(unittest.TestCase):
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

    def write_task(
        self,
        task_id: str,
        queue_state: str = "pending",
        needs_owner: bool = False,
        **metadata: object,
    ) -> Path:
        lines = [
            "---",
            f"task_id: {task_id}",
            f"title: {task_id}",
            "project: ai-project-os",
            f"assigned_to: {metadata.get('assigned_to', 'dev.codex.local')}",
            f"agent_instance: {metadata.get('agent_instance', 'dev.codex.local')}",
            "context_bundle: dev.codex.local",
            "task_mode: code",
            "model_tier: L2",
            "priority: medium",
            f"status: {queue_state}",
            "created_by: tester",
            f"needs_owner: {'true' if needs_owner else 'false'}",
            "output_target: tools/aipos_cli/",
            "artifact_policy: formal_write",
            "session_policy: single_task_session",
            "context_isolation: strict",
            "artifact_scope: tools/aipos_cli/",
            "memory_scope: controlled execute tests",
        ]
        if metadata.get("claim_policy"):
            lines.append(f"claim_policy: {metadata['claim_policy']}")
        lines.extend(["---", "task body", ""])
        return self.write_file(f"5_tasks/queue/{queue_state}/{task_id.lower()}.md", "\n".join(lines))

    def draft_payload(self, task_id: str = "AIPOS-38-DRAFT") -> dict[str, object]:
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
            "body": "## Goal\n\nAIPOS-38 controlled execute test.\n",
        }

    def event_payload(self, event_id: str = "evt_controlled_001") -> dict[str, object]:
        return {
            "event_id": event_id,
            "orchestration_id": "orch_controlled",
            "event_type": "planner_verdict_recorded",
            "timestamp": "2026-05-09T10:00:00Z",
            "actor": "dev.codex.local",
            "source": "controlled_execute_test",
            "related_task_id": "PARENT-001",
            "related_iteration_id": "iter_controlled_001",
            "severity": "info",
            "summary": "Planner verdict recorded.",
            "details": "Continue planning.",
            "forum_thread_ref": "forum://orch_controlled",
            "refs": ["forum://orch_controlled"],
        }

    def iteration_payload(self, iteration_id: str = "iter_controlled_001") -> dict[str, object]:
        return {
            "iteration_id": iteration_id,
            "orchestration_id": "orch_controlled",
            "iteration_number": 1,
            "planner_agent": "dev_codex",
            "planner_agent_instance": "dev.codex.local",
            "planner_model_tier": "L3",
            "started_at": "2026-05-09T10:00:00Z",
            "ended_at": "2026-05-09T10:05:00Z",
            "forum_thread_ref": "forum://orch_controlled",
            "parent_task_id": "PARENT-001",
            "input_refs": ["forum://orch_controlled"],
            "observed_queue_state": "No open subtasks.",
            "observed_subtask_summary": "No subtasks yet.",
            "decisions": ["Continue planning."],
            "created_subtasks": [],
            "updated_recommendations": [],
            "failure_observations": [],
            "quota_observations": [],
            "needs_owner_reasons": [],
            "next_check_after": "manual",
            "verdict": "continue",
            "owner_decision_required": False,
            "audit_handoff_required": False,
        }

    def test_draft_create_dry_run_returns_token_and_hash(self) -> None:
        result = create_draft(self.draft_payload(), dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertTrue(result["ok"])
        self.assertIn("dry_run_id", result)
        self.assertIn("dry_run_snapshot_hash", result)

    def test_execute_draft_create_writes_after_valid_dry_run(self) -> None:
        dry = create_draft(self.draft_payload("AIPOS-38-CREATE"), dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "draft_create")
        self.assertTrue((self.repo_root / "5_tasks" / "drafts" / "aipos-38-create.md").exists())

    def test_queue_claim_controlled_dry_run_blocks_wrong_specific_instance_without_token(self) -> None:
        self.write_task(
            "AIPOS-145-CLAIM-TOKEN-BLOCK",
            assigned_to="dev_claude",
            agent_instance="dev.claude.cc.local",
            claim_policy="specific_instance_only",
        )

        dry = claim_task(
            task_id="AIPOS-145-CLAIM-TOKEN-BLOCK",
            actor="dev.claude.cc_glm.local",
            dry_run=True,
            repo_root=self.repo_root,
        )

        self.assertEqual(dry["verdict"], "BLOCK")
        self.assertFalse(dry["execute_allowed"])
        self.assertIsNone(dry["dry_run_id"])
        self.assertIsNone(dry["dry_run_snapshot_hash"])
        self.assertIn(
            "specific_instance_only requires dev.claude.cc.local; current instance is dev.claude.cc_glm.local",
            dry["blocking_reasons"],
        )

    def test_execute_draft_create_rejects_expired(self) -> None:
        dry = create_draft(self.draft_payload("AIPOS-38-EXPIRED"), dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        token = get_dry_run(dry["dry_run_id"])
        assert token is not None
        token.expires_at = "2000-01-01T00:00:00Z"

        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertFalse(executed["ok"])
        self.assertEqual(executed["errors"][0]["category"], "REVALIDATION_FAILED")

    def test_execute_draft_create_rejects_stale_hash(self) -> None:
        dry = create_draft(self.draft_payload("AIPOS-38-STALE"), dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.write_file("5_tasks/drafts/aipos-38-stale.md", "collision")

        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertFalse(executed["ok"])
        self.assertEqual(executed["verdict"], "BLOCK")

    def test_draft_publish_dry_run_returns_token_and_hash(self) -> None:
        self.write_file(
            "5_tasks/drafts/aipos-38-publish.md",
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-38-PUBLISH",
                    "title: Example",
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
        dry = publish_draft("5_tasks/drafts/aipos-38-publish.md", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertIn("dry_run_id", dry)
        self.assertIn("dry_run_snapshot_hash", dry)

    def test_execute_draft_publish_writes_pending_and_publish_record_and_keeps_source(self) -> None:
        source = self.write_file(
            "5_tasks/drafts/aipos-38-publish-run.md",
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-38-PUBLISH-RUN",
                    "title: Example",
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
        before = source.read_text(encoding="utf-8")
        dry = publish_draft("5_tasks/drafts/aipos-38-publish-run.md", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertTrue((self.repo_root / "5_tasks/queue/pending/aipos-38-publish-run.md").exists())
        publish_record = (
            self.repo_root
            / "5_tasks/records/publishes/AIPOS-38-PUBLISH-RUN/publish_aipos-38-publish-run.md"
        )
        self.assertTrue(publish_record.exists())
        self.assertIn("published_by: dev.codex.local", publish_record.read_text(encoding="utf-8"))
        self.assertEqual(source.read_text(encoding="utf-8"), before)

    def test_execute_queue_claim_moves_pending_to_claimed(self) -> None:
        self.write_task("AIPOS-38-CLAIM")
        dry = claim_task(task_id="AIPOS-38-CLAIM", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertTrue((self.repo_root / "5_tasks/queue/claimed/aipos-38-claim.md").exists())
        self.assertFalse((self.repo_root / "5_tasks/queue/pending/aipos-38-claim.md").exists())

    def test_execute_queue_claim_rejects_wrong_actor(self) -> None:
        self.write_task("AIPOS-38-ACTOR")
        dry = claim_task(task_id="AIPOS-38-ACTOR", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        executed = execute_dry_run(dry["dry_run_id"], "other.actor", repo_root=self.repo_root)

        self.assertFalse(executed["ok"])
        self.assertEqual(executed["errors"][0]["category"], "ACTOR_MISMATCH")

    def test_owner_confirmation_required_when_needs_owner(self) -> None:
        self.write_task("AIPOS-38-OWNER", needs_owner=True)
        dry = claim_task(task_id="AIPOS-38-OWNER", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        blocked = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        allowed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", owner_confirmation_token="OWNER_CONFIRMED", repo_root=self.repo_root)

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["errors"][0]["category"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(allowed["ok"])

    def test_with_records_execute_is_blocked(self) -> None:
        self.write_task("AIPOS-38-WITH-RECORDS")
        dry = claim_task(
            task_id="AIPOS-38-WITH-RECORDS",
            actor="dev.codex.local",
            dry_run=True,
            with_records=True,
            repo_root=self.repo_root,
        )
        self.assertFalse(dry.get("execute_allowed", True))
        self.assertTrue(any("with_records" in item for item in dry.get("execute_blocking_reasons", [])))

    def test_orchestration_event_append_requires_owner_confirmation(self) -> None:
        dry = append_orchestration_event(
            self.event_payload(),
            dry_run=True,
            repo_root=self.repo_root,
            actor="dev.codex.local",
        )
        blocked = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        allowed = execute_dry_run(
            dry["dry_run_id"],
            "dev.codex.local",
            owner_confirmation_token="OWNER_CONFIRMED",
            repo_root=self.repo_root,
        )

        self.assertTrue(dry["execute_allowed"])
        self.assertTrue(dry["owner_confirmation_required"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["errors"][0]["category"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(allowed["ok"])
        self.assertTrue((self.repo_root / "5_tasks/orchestration/orch_controlled/orchestration_events.md").exists())

    def test_orchestration_event_append_rejects_stale_snapshot(self) -> None:
        dry = append_orchestration_event(
            self.event_payload("evt_controlled_stale"),
            dry_run=True,
            repo_root=self.repo_root,
            actor="dev.codex.local",
        )
        other = append_orchestration_event(
            self.event_payload("evt_controlled_other"),
            dry_run=True,
            repo_root=self.repo_root,
            actor="dev.codex.local",
        )
        execute_dry_run(
            other["dry_run_id"],
            "dev.codex.local",
            owner_confirmation_token="OWNER_CONFIRMED",
            repo_root=self.repo_root,
        )

        executed = execute_dry_run(
            dry["dry_run_id"],
            "dev.codex.local",
            owner_confirmation_token="OWNER_CONFIRMED",
            repo_root=self.repo_root,
        )

        self.assertFalse(executed["ok"])
        self.assertEqual(executed["errors"][0]["category"], "REVALIDATION_FAILED")

    def test_planner_iteration_append_writes_after_owner_confirmation(self) -> None:
        dry = append_planner_iteration(
            self.iteration_payload(),
            dry_run=True,
            repo_root=self.repo_root,
            actor="dev.codex.local",
        )
        executed = execute_dry_run(
            dry["dry_run_id"],
            "dev.codex.local",
            owner_confirmation_token="OWNER_CONFIRMED",
            repo_root=self.repo_root,
        )

        self.assertTrue(dry["owner_confirmation_required"])
        self.assertTrue(executed["ok"])
        text = (self.repo_root / "5_tasks/orchestration/orch_controlled/planner_iterations.md").read_text(encoding="utf-8")
        self.assertIn("iteration_id: iter_controlled_001", text)

    def test_append_controlled_execute_rejects_wrong_actor(self) -> None:
        dry = append_planner_iteration(
            self.iteration_payload("iter_actor_match"),
            dry_run=True,
            repo_root=self.repo_root,
            actor="dev.codex.local",
        )
        executed = execute_dry_run(
            dry["dry_run_id"],
            "other.actor",
            owner_confirmation_token="OWNER_CONFIRMED",
            repo_root=self.repo_root,
        )

        self.assertFalse(executed["ok"])
        self.assertEqual(executed["errors"][0]["category"], "ACTOR_MISMATCH")

    def test_execute_without_dry_run_is_blocked(self) -> None:
        executed = execute_dry_run("dryrun_missing", "dev.codex.local", repo_root=self.repo_root)
        self.assertFalse(executed["ok"])
        self.assertEqual(executed["errors"][0]["category"], "DRY_RUN_REQUIRED")

    def test_execute_response_is_json_serializable(self) -> None:
        dry = create_draft(self.draft_payload("AIPOS-38-JSON"), dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        json.dumps(executed)


if __name__ == "__main__":
    unittest.main()
