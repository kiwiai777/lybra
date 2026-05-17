from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.planner_iteration_writer import append_planner_iteration


class PlannerIterationWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        (self.repo_root / "5_tasks/queue/pending").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def payload(self, iteration_id: str = "iter_orch_test_001") -> dict[str, object]:
        return {
            "iteration_id": iteration_id,
            "orchestration_id": "orch_test",
            "iteration_number": 1,
            "planner_agent": "dev_codex",
            "planner_agent_instance": "dev.codex.local",
            "planner_model_tier": "L3",
            "started_at": "2026-05-07T10:00:00Z",
            "ended_at": "2026-05-07T10:03:00Z",
            "forum_thread_ref": "forum://orch_test",
            "parent_task_id": "PARENT-001",
            "input_refs": ["forum://orch_test", "5_tasks/queue/pending/example.md"],
            "observed_queue_state": "1 pending subtask.",
            "observed_subtask_summary": "No completed subtasks yet.",
            "decisions": ["Continue planning."],
            "created_subtasks": [],
            "updated_recommendations": ["Keep cc_glm as preferred independent review family."],
            "failure_observations": [],
            "quota_observations": [],
            "needs_owner_reasons": [],
            "next_check_after": "manual",
            "verdict": "continue",
            "active_session_id": "session_PARENT-001_20260507_dev-codex-local",
            "prior_session_id": "session_PARENT-001_20260506_dev-codex-local",
            "session_resume_ref": "codex://session/session_PARENT-001_20260507_dev-codex-local",
            "role_continuity_preference": {
                "role_family": "independent_review",
                "preferred_from_last_success": True,
                "prior_agent_instance": "dev.claude.cc_glm.local",
                "prior_session_id": "session_AIPOS-65R_20260504_cc-glm",
                "prior_task_id": "AIPOS-65R",
                "preference_scope": "parent_orchestration",
                "preference_status": "advisory",
            },
            "owner_decision_required": False,
            "audit_handoff_required": False,
        }

    def write_json(self, data: dict[str, object]) -> Path:
        path = self.repo_root / "payload.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

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

    def data_paths(self) -> list[str]:
        root = self.repo_root / "5_tasks"
        if not root.exists():
            return []
        return sorted(path.relative_to(self.repo_root).as_posix() for path in root.rglob("*"))

    def test_dry_run_returns_append_plan_without_writing(self) -> None:
        before = self.data_paths()
        result = append_planner_iteration(self.repo_root, self.payload(), actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["would_write"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["target_path"], "5_tasks/orchestration/orch_test/planner_iterations.md")
        self.assertEqual(result["planned_writes"][0]["kind"], "append")
        self.assertIn("write_snapshot_hash", result)
        self.assertEqual(before, self.data_paths())

    def test_write_requires_expected_hash(self) -> None:
        result = append_planner_iteration(self.repo_root, self.payload(), actor="dev.codex.local")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertFalse(result["wrote"])
        self.assertIn("expected hash is required for non-dry-run append", result["blocking_reasons"])

    def test_write_appends_iteration_after_hash_revalidation(self) -> None:
        dry = append_planner_iteration(self.repo_root, self.payload(), actor="dev.codex.local", dry_run=True)
        written = append_planner_iteration(
            self.repo_root,
            self.payload(),
            actor="dev.codex.local",
            expected_hash=str(dry["write_snapshot_hash"]),
        )
        target = self.repo_root / "5_tasks/orchestration/orch_test/planner_iterations.md"

        self.assertEqual(written["verdict"], "PASS")
        self.assertTrue(written["wrote"])
        text = target.read_text(encoding="utf-8")
        self.assertIn("iteration_id: iter_orch_test_001", text)
        self.assertIn("planner_model_tier: L3", text)
        self.assertIn("prior_session_id: session_PARENT-001_20260506_dev-codex-local", text)
        self.assertIn("codex://session/session_PARENT-001_20260507_dev-codex-local", text)
        self.assertIn("dev.claude.cc_glm.local", text)

    def test_duplicate_iteration_id_is_blocked(self) -> None:
        dry = append_planner_iteration(self.repo_root, self.payload(), actor="dev.codex.local", dry_run=True)
        append_planner_iteration(
            self.repo_root,
            self.payload(),
            actor="dev.codex.local",
            expected_hash=str(dry["write_snapshot_hash"]),
        )

        duplicate = append_planner_iteration(self.repo_root, self.payload(), actor="dev.codex.local", dry_run=True)

        self.assertEqual(duplicate["verdict"], "BLOCK")
        self.assertIn("Duplicate iteration_id already exists: iter_orch_test_001", duplicate["blocking_reasons"])

    def test_path_unsafe_orchestration_id_is_blocked(self) -> None:
        payload = self.payload()
        payload["orchestration_id"] = "../escape"

        result = append_planner_iteration(self.repo_root, payload, actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("orchestration_id is path-unsafe", result["blocking_reasons"])

    def test_l2_planner_tier_is_blocked(self) -> None:
        payload = self.payload()
        payload["planner_model_tier"] = "L2"

        result = append_planner_iteration(self.repo_root, payload, actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("planner_model_tier must be L3 or L4", result["blocking_reasons"])

    def test_unsupported_verdict_is_blocked(self) -> None:
        payload = self.payload()
        payload["verdict"] = "invented"

        result = append_planner_iteration(self.repo_root, payload, actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Unsupported verdict: invented", result["blocking_reasons"])

    def test_missing_required_field_is_blocked(self) -> None:
        payload = self.payload()
        payload.pop("input_refs")

        result = append_planner_iteration(self.repo_root, payload, actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Missing required field: input_refs", result["blocking_reasons"])

    def test_actor_must_match_planner_identity(self) -> None:
        result = append_planner_iteration(self.repo_root, self.payload(), actor="another.actor", dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("actor must match planner_agent or planner_agent_instance", result["blocking_reasons"])

    def test_needs_owner_requires_visible_reason_and_preserves_gate(self) -> None:
        payload = self.payload()
        payload["verdict"] = "needs_owner"
        payload["owner_decision_required"] = True
        payload["needs_owner_reasons"] = ["Architecture fork requires Owner decision."]

        result = append_planner_iteration(self.repo_root, payload, actor="dev.codex.local", dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["owner_confirmation_required"])

    def test_cli_json_dry_run_and_write(self) -> None:
        payload_path = self.write_json(self.payload("iter_cli_001"))
        dry_exit, dry_output = self.run_cli_json(
            [
                "orchestration",
                "iteration",
                "append",
                "--from-json",
                str(payload_path),
                "--actor",
                "dev.codex.local",
                "--dry-run",
                "--json",
            ]
        )
        self.assertFalse((self.repo_root / "5_tasks/orchestration/orch_test/planner_iterations.md").exists())
        write_exit, write_output = self.run_cli_json(
            [
                "orchestration",
                "iteration",
                "append",
                "--from-json",
                str(payload_path),
                "--actor",
                "dev.codex.local",
                "--expected-hash",
                str(dry_output["write_snapshot_hash"]),
                "--json",
            ]
        )

        self.assertEqual(dry_exit, 0)
        self.assertEqual(dry_output["action"], "planner_iteration_append")
        self.assertEqual(write_exit, 0)
        self.assertTrue(write_output["wrote"])


if __name__ == "__main__":
    unittest.main()
