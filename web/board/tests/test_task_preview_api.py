from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_routes, dispatch_api_request


class TaskPreviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.write_task()
        self.routes = _api_routes(self.repo_root)

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

    def snapshot_data_paths(self) -> list[str]:
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

    def test_task_with_valid_task_id_returns_json_envelope(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/task?task_id=EXAMPLE-001", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "get_task")
        self.assertIn("verdict", data)

    def test_task_missing_selector_returns_clean_error(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/task", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertEqual(data["errors"][0]["category"], "VALIDATION_ERROR")

    def test_task_unknown_task_returns_clean_error(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/task?task_id=MISSING", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["category"], "NOT_FOUND")

    def test_preview_with_valid_task_id_and_actor_returns_json_envelope(self) -> None:
        status, data = dispatch_api_request(
            method="GET",
            path="/api/preview?task_id=EXAMPLE-001&actor=dev.codex.local",
            routes=self.routes,
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "get_preview")
        self.assertIn("actor_match", data)

    def test_preview_missing_actor_returns_clean_error(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/preview?task_id=EXAMPLE-001", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["errors"][0]["category"], "VALIDATION_ERROR")

    def test_non_get_remains_rejected(self) -> None:
        status, data = dispatch_api_request(method="POST", path="/api/task?task_id=EXAMPLE-001", routes=self.routes)
        self.assertEqual(status, 405)
        self.assertEqual(data["error"], "METHOD_NOT_ALLOWED")

    def test_unknown_route_remains_404(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/not-real", routes=self.routes)
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "NOT_FOUND")

    def test_task_and_preview_routes_do_not_write_task_data(self) -> None:
        before = self.snapshot_data_paths()
        dispatch_api_request(method="GET", path="/api/task?task_id=EXAMPLE-001", routes=self.routes)
        dispatch_api_request(method="GET", path="/api/preview?task_id=EXAMPLE-001&actor=dev.codex.local", routes=self.routes)
        after = self.snapshot_data_paths()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
