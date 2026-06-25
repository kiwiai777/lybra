from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks

# ENV-AWARE: bare-python asserts LOUD/FAIL-CLOSED behavior.
_HAS_YAML = importlib.util.find_spec("yaml") is not None


class OrchestrationSummaryPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        (self.repo_root / "5_tasks/queue/pending").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks/queue/claimed").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks/queue/completed").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks/queue/blocked").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self, state: str, task_id: str, extra: str = "") -> None:
        path = self.repo_root / "5_tasks" / "queue" / state / f"{task_id.lower()}.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    f"title: {task_id}",
                    "project: ai-project-os",
                    "assigned_to: dev_codex",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L3",
                    "priority: medium",
                    f"status: {state}",
                    "created_by: test",
                    "needs_owner: false",
                    "artifact_policy: formal_write",
                    "orchestration_id: orch_test",
                    extra.rstrip(),
                    "---",
                    "",
                    "Body.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_logs(self) -> None:
        root = self.repo_root / "5_tasks/orchestration/orch_test"
        root.mkdir(parents=True, exist_ok=True)
        (root / "planner_iterations.md").write_text(
            "\n".join(
                [
                    "- iteration_id: iter_001",
                    "  orchestration_id: orch_test",
                    "  iteration_number: 1",
                    "  planner_agent: dev_codex",
                    "  planner_agent_instance: dev.codex.local",
                    "  planner_model_tier: L3",
                    "  started_at: '2026-05-07T10:00:00Z'",
                    "  ended_at: '2026-05-07T10:03:00Z'",
                    "  forum_thread_ref: forum://orch_test",
                    "  parent_task_id: PARENT-001",
                    "  observed_queue_state: one task open",
                    "  observed_subtask_summary: one task open",
                    "  next_check_after: manual",
                    "  verdict: continue",
                    "  owner_decision_required: false",
                    "  input_refs:",
                    "    - forum://orch_test",
                    "  decisions:",
                    "    - Continue.",
                    "  created_subtasks:",
                    "  updated_recommendations:",
                    "  failure_observations:",
                    "  quota_observations:",
                    "  needs_owner_reasons:",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "orchestration_events.md").write_text(
            "\n".join(
                [
                    "- event_id: evt_001",
                    "  orchestration_id: orch_test",
                    "  event_type: planner_verdict_recorded",
                    "  timestamp: '2026-05-07T10:03:01Z'",
                    "  actor: dev.codex.local",
                    "  source: test",
                    "  severity: info",
                    "  summary: Planner continued.",
                    "  refs:",
                    "    - forum://orch_test",
                    "",
                ]
            ),
            encoding="utf-8",
        )

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

    def test_summary_preview_rebuilds_from_tasks_and_append_only_logs_without_writing(self) -> None:
        self.write_task("pending", "TASK-1")
        self.write_task("completed", "TASK-2")
        self.write_logs()
        before = self.data_paths()

        result = build_orchestration_summary_preview(
            self.repo_root,
            "orch_test",
            tasks=load_all_tasks(self.repo_root),
            records=load_records(self.repo_root),
        )

        self.assertEqual(result["action"], "orchestration_summary_preview")
        if not _HAS_YAML:
            # BARE: orchestration logs (sequences-of-mappings) can't be parsed → warn+empty.
            # The preview still runs (gate must not hard-fail on read) but current_iteration = 0.
            self.assertIn(result["verdict"], {"PASS", "NEEDS_OWNER"})
            self.assertEqual(result["planned_summary"]["open_subtask_count"], 1)
            self.assertEqual(result["planned_summary"]["completed_subtask_count"], 1)
            self.assertEqual(result["planned_summary"]["current_iteration"], 0)
            # Loud warning must be surfaced.
            all_warnings = result.get("warnings", []) + result.get("planned_summary", {}).get("warnings", [])
            self.assertTrue(
                any("PyYAML" in str(w) for w in all_warnings),
                f"Expected loud PyYAML warning in result; got warnings: {all_warnings}\nresult: {result}",
            )
            self.assertEqual(before, self.data_paths())
            return
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["would_write"])
        self.assertFalse(result["writes_enabled"])
        self.assertFalse(result["execute_allowed"])
        self.assertIsNone(result["dry_run_token"])
        self.assertEqual(result["planned_summary"]["target_path"], "5_tasks/orchestration/orch_test/orchestration_state.md")
        self.assertEqual(result["planned_summary"]["open_subtask_count"], 1)
        self.assertEqual(result["planned_summary"]["completed_subtask_count"], 1)
        self.assertEqual(result["planned_summary"]["current_iteration"], 1)
        self.assertEqual(before, self.data_paths())

    def test_needs_owner_sources_are_preserved_without_resolving_gate(self) -> None:
        self.write_task("pending", "TASK-1", "needs_owner: true\nneeds_owner_reasons:\n  - Architecture fork")
        self.write_logs()

        result = build_orchestration_summary_preview(
            self.repo_root,
            "orch_test",
            tasks=load_all_tasks(self.repo_root),
            records=load_records(self.repo_root),
        )

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["owner_confirmation_required"])
        self.assertTrue(result["planned_summary"]["needs_owner"])
        self.assertIn("TASK-1: task marked needs_owner", result["planned_summary"]["needs_owner_reasons"])
        self.assertFalse(result["execute_allowed"])

    def test_conflicting_log_orchestration_id_returns_needs_owner(self) -> None:
        self.write_task("pending", "TASK-1")
        self.write_logs()
        event_path = self.repo_root / "5_tasks/orchestration/orch_test/orchestration_events.md"
        event_path.write_text(event_path.read_text(encoding="utf-8") + "\n- event_id: evt_bad\n  orchestration_id: other\n", encoding="utf-8")

        result = build_orchestration_summary_preview(
            self.repo_root,
            "orch_test",
            tasks=load_all_tasks(self.repo_root),
            records=load_records(self.repo_root),
        )

        if not _HAS_YAML:
            # BARE: logs can't be parsed → no conflict detected (warn+empty, not hard-fail).
            # verdict may be PASS or NEEDS_OWNER depending on task state; no BLOCK.
            self.assertNotEqual(result["verdict"], "BLOCK")
            self.assertFalse(result["writes_enabled"])
            return
        self.assertEqual(result["verdict"], "NEEDS_OWNER")
        self.assertTrue(result["conflicts"])
        self.assertFalse(result["writes_enabled"])

    def test_path_unsafe_orchestration_id_is_blocked(self) -> None:
        result = build_orchestration_summary_preview(self.repo_root, "../escape", tasks=[], records=load_records(self.repo_root))

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("orchestration_id is path-unsafe", result["blocking_reasons"])
        self.assertFalse(result["execute_allowed"])

    def test_cli_json_preview(self) -> None:
        self.write_task("claimed", "TASK-1")
        self.write_logs()

        exit_code, output = self.run_cli_json(
            [
                "orchestration",
                "summary",
                "preview",
                "--orchestration-id",
                "orch_test",
                "--json",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["action"], "orchestration_summary_preview")
        self.assertFalse(output["writes_enabled"])
        self.assertFalse(output["execute_allowed"])
        self.assertEqual(output["planned_summary"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
