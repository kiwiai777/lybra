from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.orchestration_event_writer import append_orchestration_event


class OrchestrationEventWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        (self.repo_root / "5_tasks/queue/pending").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def payload(self, event_id: str = "evt_orch_test_001") -> dict[str, object]:
        return {
            "event_id": event_id,
            "orchestration_id": "orch_test",
            "event_type": "planner_verdict_recorded",
            "timestamp": "2026-05-04T10:00:00Z",
            "actor": "dev.codex.local",
            "source": "test",
            "related_task_id": "PARENT-001",
            "related_subtask_id": "",
            "related_iteration_id": "iter_001",
            "severity": "info",
            "summary": "Planner verdict recorded.",
            "details": "Continue.",
            "forum_thread_ref": "forum://orch_test",
            "refs": ["forum://orch_test"],
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
        result = append_orchestration_event(self.repo_root, self.payload(), dry_run=True)

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["would_write"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["target_path"], "5_tasks/orchestration/orch_test/orchestration_events.md")
        self.assertEqual(result["planned_writes"][0]["kind"], "append")
        self.assertIn("write_snapshot_hash", result)
        self.assertEqual(before, self.data_paths())

    def test_write_requires_expected_hash(self) -> None:
        result = append_orchestration_event(self.repo_root, self.payload())

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertFalse(result["wrote"])
        self.assertIn("expected hash is required for non-dry-run append", result["blocking_reasons"])

    def test_write_appends_event_after_hash_revalidation(self) -> None:
        dry = append_orchestration_event(self.repo_root, self.payload(), dry_run=True)
        written = append_orchestration_event(
            self.repo_root,
            self.payload(),
            expected_hash=str(dry["write_snapshot_hash"]),
        )
        target = self.repo_root / "5_tasks/orchestration/orch_test/orchestration_events.md"

        self.assertEqual(written["verdict"], "PASS")
        self.assertTrue(written["wrote"])
        text = target.read_text(encoding="utf-8")
        self.assertIn("event_id: evt_orch_test_001", text)
        self.assertIn("event_type: planner_verdict_recorded", text)

    def test_duplicate_event_id_is_blocked(self) -> None:
        dry = append_orchestration_event(self.repo_root, self.payload(), dry_run=True)
        append_orchestration_event(self.repo_root, self.payload(), expected_hash=str(dry["write_snapshot_hash"]))

        duplicate = append_orchestration_event(self.repo_root, self.payload(), dry_run=True)

        self.assertEqual(duplicate["verdict"], "BLOCK")
        self.assertIn("Duplicate event_id already exists: evt_orch_test_001", duplicate["blocking_reasons"])

    def test_path_unsafe_orchestration_id_is_blocked(self) -> None:
        payload = self.payload()
        payload["orchestration_id"] = "../escape"

        result = append_orchestration_event(self.repo_root, payload, dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("orchestration_id is path-unsafe", result["blocking_reasons"])

    def test_owner_decision_recorded_requires_evidence_ref(self) -> None:
        payload = self.payload("evt_owner_decision")
        payload["event_type"] = "owner_decision_recorded"
        payload["refs"] = ["forum://orch_test"]
        payload["forum_thread_ref"] = "forum://orch_test"

        result = append_orchestration_event(self.repo_root, payload, dry_run=True)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("owner_decision_recorded requires Owner decision evidence in refs", result["blocking_reasons"])

    def test_actor_mismatch_is_blocked(self) -> None:
        result = append_orchestration_event(
            self.repo_root,
            self.payload(),
            actor="another.actor",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("actor must match payload actor", result["blocking_reasons"])

    def test_cli_json_dry_run_and_write(self) -> None:
        payload_path = self.write_json(self.payload("evt_cli_001"))
        dry_exit, dry_output = self.run_cli_json(
            [
                "orchestration",
                "event",
                "append",
                "--from-json",
                str(payload_path),
                "--actor",
                "dev.codex.local",
                "--dry-run",
                "--json",
            ]
        )
        self.assertFalse((self.repo_root / "5_tasks/orchestration/orch_test/orchestration_events.md").exists())
        write_exit, write_output = self.run_cli_json(
            [
                "orchestration",
                "event",
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
        self.assertEqual(dry_output["action"], "orchestration_event_append")
        self.assertEqual(write_exit, 0)
        self.assertTrue(write_output["wrote"])


if __name__ == "__main__":
    unittest.main()
