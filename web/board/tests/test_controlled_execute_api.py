from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_post_routes, _api_routes, dispatch_api_request


class ControlledExecuteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.write_task()
        self.routes = _api_routes(self.repo_root)
        self.post_routes = _api_post_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self) -> None:
        path = self.repo_root / "5_tasks/queue/pending/example_task.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    "task_id: EXAMPLE-001",
                    "title: Example Task",
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
                    "output_target: web/board/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: web/board/",
                    "memory_scope: web board tests",
                    "---",
                    "Task body",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def data_paths(self) -> list[str]:
        roots = [
            self.repo_root / "5_tasks/queue",
            self.repo_root / "5_tasks/drafts",
            self.repo_root / "5_tasks/records",
            self.repo_root / "5_tasks/orchestration",
        ]
        values: list[str] = []
        for root in roots:
            if root.exists():
                values.extend(path.relative_to(self.repo_root).as_posix() for path in root.rglob("*"))
        return sorted(values)

    def post(self, path: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
        return dispatch_api_request(
            method="POST",
            path=path,
            routes=self.routes,
            post_routes=self.post_routes,
            body=body,
        )

    def test_execute_dry_run_claim_returns_token_without_writing(self) -> None:
        before = self.data_paths()
        status, data = self.post(
            "/api/execute/dry-run",
            {"operation": "queue_claim", "task_id": "EXAMPLE-001", "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "queue_claim")
        self.assertIn("dry_run_id", data)
        self.assertTrue(data["execute_allowed"])
        self.assertEqual(before, self.data_paths())

    def test_execute_confirm_moves_claim_after_valid_dry_run(self) -> None:
        _status, dry = self.post(
            "/api/execute/dry-run",
            {"operation": "queue_claim", "task_id": "EXAMPLE-001", "actor": "dev.codex.local"},
        )

        status, executed = self.post(
            "/api/execute/confirm",
            {"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "queue_claim")
        self.assertTrue((self.repo_root / "5_tasks/queue/claimed/example_task.md").exists())
        self.assertFalse((self.repo_root / "5_tasks/queue/pending/example_task.md").exists())

    def draft_payload(self, task_id: str = "AIPOS-56-DRAFT") -> dict[str, object]:
        return {
            "frontmatter": {
                "task_id": task_id,
                "title": "Draft From UI",
                "project": "ai-project-os",
                "assigned_to": "dev.codex.local",
                "agent_instance": "dev.codex.local",
                "context_bundle": "dev.codex.local",
                "task_mode": "code",
                "model_tier": "L2",
                "priority": "medium",
                "status": "pending",
                "created_by": "dev.codex.local",
                "needs_owner": False,
                "output_target": "web/board/",
                "artifact_policy": "formal_write",
                "task_type": "one_shot",
                "polling_mode": "agent_polling",
                "claim_policy": "assigned_agent_only",
                "report_mode": "forum_reply",
                "recurrence": "none",
            },
            "body": "## Goal\n\nDraft creation UI test.\n",
        }

    def write_draft(self, task_id: str = "AIPOS-57-PUBLISH") -> Path:
        slug = task_id.lower()
        path = self.repo_root / f"5_tasks/drafts/{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    "title: Draft Publish From UI",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: dev.codex.local",
                    "needs_owner: false",
                    "output_target: web/board/",
                    "artifact_policy: formal_write",
                    "task_type: one_shot",
                    "polling_mode: agent_polling",
                    "claim_policy: assigned_agent_only",
                    "report_mode: forum_reply",
                    "recurrence: none",
                    "artifact_scope: web/board/",
                    "memory_scope: web board tests",
                    "---",
                    "## Goal",
                    "",
                    "Draft publish UI test.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def event_payload(self, event_id: str = "evt_web_controlled_001") -> dict[str, object]:
        return {
            "event_id": event_id,
            "orchestration_id": "orch_web_controlled",
            "event_type": "planner_tick_completed",
            "timestamp": "2026-05-10T00:00:00Z",
            "actor": "dev.codex.local",
            "source": "web_board_persistence_test",
            "related_task_id": "PARENT-001",
            "related_iteration_id": "iter_web_controlled_001",
            "severity": "info",
            "summary": "Planner tick completed.",
            "details": "Controlled persistence UI dry-run.",
            "forum_thread_ref": "forum://orch_web_controlled",
            "refs": ["forum://orch_web_controlled"],
        }

    def iteration_payload(self, iteration_id: str = "iter_web_controlled_001") -> dict[str, object]:
        return {
            "iteration_id": iteration_id,
            "orchestration_id": "orch_web_controlled",
            "iteration_number": 1,
            "planner_agent": "dev_codex",
            "planner_agent_instance": "dev.codex.local",
            "planner_model_tier": "L3",
            "started_at": "2026-05-10T00:00:00Z",
            "ended_at": "2026-05-10T00:05:00Z",
            "forum_thread_ref": "forum://orch_web_controlled",
            "parent_task_id": "PARENT-001",
            "input_refs": ["forum://orch_web_controlled"],
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

    def test_execute_dry_run_draft_create_returns_token_without_writing(self) -> None:
        before = self.data_paths()
        status, data = self.post(
            "/api/execute/dry-run",
            {"operation": "draft_create", "actor": "dev.codex.local", "payload": self.draft_payload()},
        )

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "draft_create")
        self.assertIn("dry_run_id", data)
        self.assertIn("rendered_markdown", data["data"])
        self.assertEqual(before, self.data_paths())

    def test_execute_confirm_creates_draft_after_valid_dry_run(self) -> None:
        _status, dry = self.post(
            "/api/execute/dry-run",
            {"operation": "draft_create", "actor": "dev.codex.local", "payload": self.draft_payload("AIPOS-56-CREATE")},
        )

        status, executed = self.post(
            "/api/execute/confirm",
            {"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "draft_create")
        self.assertTrue((self.repo_root / "5_tasks/drafts/aipos-56-create.md").exists())

    def test_execute_dry_run_draft_publish_returns_token_without_writing(self) -> None:
        source = self.write_draft()
        before = self.data_paths()
        status, data = self.post(
            "/api/execute/dry-run",
            {"operation": "draft_publish", "path": "5_tasks/drafts/aipos-57-publish.md", "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "draft_publish")
        self.assertIn("dry_run_id", data)
        self.assertIn("rendered_markdown", data["data"])
        self.assertTrue(data["execute_allowed"])
        self.assertEqual(source.relative_to(self.repo_root).as_posix(), data["data"]["source_path"])
        self.assertEqual(before, self.data_paths())

    def test_execute_confirm_publishes_draft_after_valid_dry_run(self) -> None:
        source = self.write_draft("AIPOS-57-PUBLISH-RUN")
        _status, dry = self.post(
            "/api/execute/dry-run",
            {"operation": "draft_publish", "path": source.relative_to(self.repo_root).as_posix(), "actor": "dev.codex.local"},
        )

        status, executed = self.post(
            "/api/execute/confirm",
            {"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "draft_publish")
        self.assertTrue(source.exists())
        self.assertTrue((self.repo_root / "5_tasks/queue/pending/aipos-57-publish-run.md").exists())

    def test_execute_dry_run_orchestration_event_append_returns_token_without_writing(self) -> None:
        before = self.data_paths()
        status, data = self.post(
            "/api/execute/dry-run",
            {
                "operation": "orchestration_event_append",
                "actor": "dev.codex.local",
                "payload": self.event_payload(),
            },
        )

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "orchestration_event_append")
        self.assertIn("dry_run_id", data)
        self.assertTrue(data["execute_allowed"])
        self.assertTrue(data["owner_confirmation_required"])
        self.assertEqual(before, self.data_paths())

    def test_execute_confirm_orchestration_event_append_requires_owner_confirmation(self) -> None:
        _status, dry = self.post(
            "/api/execute/dry-run",
            {
                "operation": "orchestration_event_append",
                "actor": "dev.codex.local",
                "payload": self.event_payload("evt_web_controlled_confirm"),
            },
        )

        _blocked_status, blocked = self.post(
            "/api/execute/confirm",
            {"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local"},
        )
        status, executed = self.post(
            "/api/execute/confirm",
            {"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local", "owner_confirmed": True},
        )

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["errors"][0]["category"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertEqual(status, 200)
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "orchestration_event_append")
        self.assertTrue((self.repo_root / "5_tasks/orchestration/orch_web_controlled/orchestration_events.md").exists())

    def test_execute_confirm_planner_iteration_append_after_valid_dry_run(self) -> None:
        _status, dry = self.post(
            "/api/execute/dry-run",
            {
                "operation": "planner_iteration_append",
                "actor": "dev.codex.local",
                "payload": self.iteration_payload(),
            },
        )

        status, executed = self.post(
            "/api/execute/confirm",
            {"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local", "owner_confirmed": True},
        )

        self.assertEqual(status, 200)
        self.assertTrue(dry["execute_allowed"])
        self.assertTrue(dry["owner_confirmation_required"])
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "planner_iteration_append")
        text = (self.repo_root / "5_tasks/orchestration/orch_web_controlled/planner_iterations.md").read_text(encoding="utf-8")
        self.assertIn("iteration_id: iter_web_controlled_001", text)

    def test_execute_dry_run_blocks_non_claim_draft_create_or_draft_publish_operations(self) -> None:
        status, data = self.post(
            "/api/execute/dry-run",
            {"operation": "queue_complete", "path": "5_tasks/queue/pending/example_task.md", "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")

    def test_execute_confirm_missing_token_blocks(self) -> None:
        status, data = self.post("/api/execute/confirm", {"actor": "dev.codex.local"})

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["category"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
