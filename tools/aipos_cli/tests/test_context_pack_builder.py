from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.context_pack_builder import build_context_pack_preview


class ContextPackBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks/queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles/examples").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles/examples/dev.codex.local.md").write_text(
            "\n".join(
                [
                    "role_instance: dev.codex.local",
                    "environment: local_wsl_ubuntu",
                    "description: local engineering agent",
                    "allowed_task_modes:",
                    "  - code",
                    "  - audit",
                    "preferred_model_tiers:",
                    "  - L2",
                    "  - L3",
                    "allowed_model_tiers:",
                    "  - L1",
                    "  - L2",
                    "  - L3",
                    "memory_access:",
                    "  - 2_projects/lybra/",
                    "output_target:",
                    "  - repository",
                    "escalation_rules:",
                    "  - if high risk use L3",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self, *, needs_owner: bool = False) -> Path:
        path = self.repo_root / "5_tasks/queue/pending/aipos-78-context.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-78-CONTEXT",
                    "title: Context Pack Test",
                    "project: ai-project-os",
                    "assigned_to: dev_codex",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L3",
                    "priority: high",
                    "status: pending",
                    "created_by: tester",
                    f"needs_owner: {'true' if needs_owner else 'false'}",
                    "output_target: tools/aipos_cli/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/aipos_cli/",
                    "memory_scope: context pack tests",
                    "source_tag: external_owner_inbox",
                    "client_tag: alpha_client",
                    "external_ref: extmsg:abc123",
                    "orchestration_id: orch_context_pack",
                    "forum_thread_ref: forum://aipos/78",
                    "---",
                    "Build a read-only context pack preview.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def data_paths(self) -> list[str]:
        return sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))

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

    def test_task_context_pack_preview_is_read_only(self) -> None:
        self.write_task()
        before = self.data_paths()
        result = build_context_pack_preview(self.repo_root, task_id="AIPOS-78-CONTEXT")
        after = self.data_paths()

        self.assertEqual(result["action"], "context_pack_preview")
        self.assertEqual(result["verdict"], "WARN")
        self.assertEqual(result["scope"], "task")
        self.assertEqual(result["task"]["task_id"], "AIPOS-78-CONTEXT")
        self.assertEqual(result["task"]["source_tag"], "external_owner_inbox")
        self.assertEqual(result["task"]["client_tag"], "alpha_client")
        self.assertEqual(result["task"]["external_ref"], "extmsg:abc123")
        self.assertTrue(result["context_bundle"]["found"])
        self.assertEqual(result["context_bundle"]["path"], "3_context_bundles/examples/dev.codex.local.md")
        self.assertFalse(result["writes_enabled"])
        self.assertFalse(result["execute_allowed"])
        self.assertFalse(result["external_rag_enabled"])
        self.assertFalse(result["agent_execution_enabled"])
        self.assertFalse(result["git_automation_enabled"])
        self.assertIsNone(result["dry_run_token"])
        self.assertEqual(result["planned_writes"], [])
        self.assertEqual(result["planned_moves"], [])
        self.assertEqual(before, after)

    def test_missing_bundle_is_warning_not_write(self) -> None:
        task_path = self.write_task()
        text = task_path.read_text(encoding="utf-8").replace("context_bundle: dev.codex.local", "context_bundle: missing.bundle")
        task_path.write_text(text, encoding="utf-8")

        result = build_context_pack_preview(self.repo_root, task_id="AIPOS-78-CONTEXT")

        self.assertEqual(result["verdict"], "WARN")
        self.assertFalse(result["context_bundle"]["found"])
        self.assertIn("context_bundle not found: missing.bundle", result["warnings"])

    def test_needs_owner_is_preserved(self) -> None:
        self.write_task(needs_owner=True)
        result = build_context_pack_preview(self.repo_root, task_id="AIPOS-78-CONTEXT")

        self.assertEqual(result["verdict"], "NEEDS_OWNER")
        self.assertTrue(result["governance"]["owner_decision_gates_preserved"])

    def test_orchestration_only_preview_is_read_only(self) -> None:
        before = self.data_paths()
        result = build_context_pack_preview(self.repo_root, orchestration_id="orch_context_pack")
        after = self.data_paths()

        self.assertEqual(result["scope"], "orchestration")
        self.assertEqual(result["orchestration"]["orchestration_id"], "orch_context_pack")
        self.assertFalse(result["writes_enabled"])
        self.assertEqual(before, after)

    def test_cli_context_pack_preview_outputs_json(self) -> None:
        self.write_task()
        before = self.data_paths()
        exit_code, output = self.run_cli_json(["context-pack", "preview", "--task-id", "AIPOS-78-CONTEXT", "--json"])
        after = self.data_paths()

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["action"], "context_pack_preview")
        self.assertEqual(output["task"]["task_id"], "AIPOS-78-CONTEXT")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
