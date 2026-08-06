"""AIPOS-348: completion path integrity tests.

Tests cover:
1. audit: required + no PASS verdict -> complete blocked
2. audit: required + PASS verdict -> complete allowed
3. audit: none/optional -> complete allowed (no regression)
4. completed -> reopen allowed with reason (correction path)
5. reopen from completed writes correct fields, preserves history
6. reopen from blocked still works (no regression)
7. reopen from pending still rejected
8. CLI integration: queue complete audit gate
9. CLI integration: queue reopen from completed
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.queue_mutation import mutate_queue_task, REOPEN_SOURCE_STATES


class Aipos348AuditGateTests(unittest.TestCase):
    """Tests for audit-gated complete."""

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
        # Add audit field if specified
        if "audit" in metadata:
            lines.append(f"audit: {metadata['audit']}")
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
        lines.extend(["---", "Task body", ""])
        filename = str(metadata.get("filename", f"{task_id.lower()}.md"))
        return self.write_file(f"5_tasks/queue/{queue_state}/{filename}", "\n".join(lines))

    def write_verdict(self, task_id: str, verdict_id: str, verdict: str) -> Path:
        """Write an audit verdict record."""
        content = (
            f"---\n"
            f"verdict_id: {verdict_id}\n"
            f"reviewed_task_id: {task_id}\n"
            f"verdict: {verdict}\n"
            f"verdict_at: '2026-08-06T00:00:00Z'\n"
            f"auditor: audit.codex.local\n"
            f"---\n"
            f"Audit verdict body\n"
        )
        return self.write_file(
            f"5_tasks/records/audit_verdicts/{task_id}/{verdict_id}.md",
            content,
        )

    # --- Audit gate: complete blocked without PASS verdict ---

    def test_complete_blocked_when_audit_required_no_verdict(self) -> None:
        """audit: required + no verdict records at all -> complete BLOCK."""
        self.write_task(
            "AIPOS-348-NO-VERDICT",
            queue_state="claimed",
            audit="required",
            claim_id="claim_AIPOS-348-NO-VERDICT_1_dev",
            active_session_id="session_AIPOS-348-NO-VERDICT_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        result = mutate_queue_task(
            self.repo_root,
            "complete",
            task_id="AIPOS-348-NO-VERDICT",
            actor="dev.codex.local",
            report_link="https://example.com/report",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertTrue(any("audit: required" in r and "no PASS audit verdict" in r for r in result["blocking_reasons"]))

    def test_complete_blocked_when_audit_required_only_fail_verdict(self) -> None:
        """audit: required + only FAIL verdict -> complete BLOCK."""
        self.write_task(
            "AIPOS-348-FAIL-VERDICT",
            queue_state="claimed",
            audit="required",
            claim_id="claim_AIPOS-348-FAIL-VERDICT_1_dev",
            active_session_id="session_AIPOS-348-FAIL-VERDICT_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )
        self.write_verdict("AIPOS-348-FAIL-VERDICT", "verdict_FAIL_001", "FAIL")

        result = mutate_queue_task(
            self.repo_root,
            "complete",
            task_id="AIPOS-348-FAIL-VERDICT",
            actor="dev.codex.local",
            report_link="https://example.com/report",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertTrue(any("no PASS audit verdict" in r for r in result["blocking_reasons"]))

    # --- Audit gate: complete allowed with PASS verdict ---

    def test_complete_allowed_when_audit_required_with_pass_verdict(self) -> None:
        """audit: required + PASS verdict -> complete PASS."""
        self.write_task(
            "AIPOS-348-PASS-VERDICT",
            queue_state="claimed",
            audit="required",
            claim_id="claim_AIPOS-348-PASS-VERDICT_1_dev",
            active_session_id="session_AIPOS-348-PASS-VERDICT_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )
        self.write_verdict("AIPOS-348-PASS-VERDICT", "verdict_PASS_001", "PASS")

        result = mutate_queue_task(
            self.repo_root,
            "complete",
            task_id="AIPOS-348-PASS-VERDICT",
            actor="dev.codex.local",
            report_link="https://example.com/report",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "PASS")

    # --- No regression: audit: none/optional -> complete allowed ---

    def test_complete_allowed_when_audit_none(self) -> None:
        """audit: none (or absent) -> complete allowed without verdict."""
        self.write_task(
            "AIPOS-348-NO-AUDIT",
            queue_state="claimed",
            claim_id="claim_AIPOS-348-NO-AUDIT_1_dev",
            active_session_id="session_AIPOS-348-NO-AUDIT_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        result = mutate_queue_task(
            self.repo_root,
            "complete",
            task_id="AIPOS-348-NO-AUDIT",
            actor="dev.codex.local",
            report_link="https://example.com/report",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "PASS")

    def test_complete_allowed_when_audit_optional(self) -> None:
        """audit: optional -> complete allowed without verdict."""
        self.write_task(
            "AIPOS-348-OPTIONAL-AUDIT",
            queue_state="claimed",
            audit="optional",
            claim_id="claim_AIPOS-348-OPTIONAL-AUDIT_1_dev",
            active_session_id="session_AIPOS-348-OPTIONAL-AUDIT_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        result = mutate_queue_task(
            self.repo_root,
            "complete",
            task_id="AIPOS-348-OPTIONAL-AUDIT",
            actor="dev.codex.local",
            report_link="https://example.com/report",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "PASS")


class Aipos348ReopenCompletedTests(unittest.TestCase):
    """Tests for reopen from completed state (correction path)."""

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
        lines.extend(["---", "Task body", ""])
        filename = str(metadata.get("filename", f"{task_id.lower()}.md"))
        return self.write_file(f"5_tasks/queue/{queue_state}/{filename}", "\n".join(lines))

    # --- Reopen from completed is now allowed ---

    def test_reopen_completed_to_pending_dry_run(self) -> None:
        """Reopen from completed -> dry run succeeds."""
        source = self.write_task(
            "AIPOS-348-REOPEN-COMPLETED",
            queue_state="completed",
            completed_by="dev.codex.local",
            completed_at="2026-04-30T00:00:00Z",
            last_session_id="session_AIPOS-348-REOPEN-COMPLETED_1_dev",
        )
        before = source.read_text(encoding="utf-8")

        result = mutate_queue_task(
            self.repo_root,
            "reopen",
            task_id="AIPOS-348-REOPEN-COMPLETED",
            actor="dev.codex.local",
            reason="Incorrect completion, audit needed",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "PASS")
        self.assertFalse(result["wrote"])
        self.assertFalse(result["moved"])
        # Source unchanged in dry run
        self.assertEqual(source.read_text(encoding="utf-8"), before)

    def test_reopen_completed_to_pending_writes_correct_fields(self) -> None:
        """Reopen from completed -> writes correct fields, clears completion."""
        self.write_task(
            "AIPOS-348-REOPEN-WRITE",
            queue_state="completed",
            completed_by="dev.codex.local",
            completed_at="2026-04-30T00:00:00Z",
            last_session_id="session_AIPOS-348-REOPEN-WRITE_1_dev",
        )

        result = mutate_queue_task(
            self.repo_root,
            "reopen",
            task_id="AIPOS-348-REOPEN-WRITE",
            actor="dev.codex.local",
            reason="Incorrect completion, audit needed",
        )

        target = self.repo_root / "5_tasks/queue/pending/aipos-348-reopen-write.md"
        text = target.read_text(encoding="utf-8")

        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["wrote"])
        self.assertTrue(result["moved"])
        self.assertIn("status: pending", text)
        self.assertIn("reopened_by: dev.codex.local", text)
        self.assertIn("reopened_at:", text)
        self.assertIn("reopen_reason: Incorrect completion, audit needed", text)
        self.assertIn("needs_owner: false", text)
        # Completion fields cleared
        self.assertNotIn("completed_by:", text)
        self.assertNotIn("completed_at:", text)
        # Source removed
        source = self.repo_root / "5_tasks/queue/completed/aipos-348-reopen-write.md"
        self.assertFalse(source.exists())

    def test_reopen_blocked_still_works(self) -> None:
        """No regression: reopen from blocked still works."""
        self.write_task(
            "AIPOS-348-REOPEN-BLOCKED",
            queue_state="blocked",
            blocked_by="dev.codex.local",
            blocked_at="2026-04-30T00:00:00Z",
            block_reason="waiting",
            last_session_id="session_AIPOS-348-REOPEN-BLOCKED_1_dev",
        )

        result = mutate_queue_task(
            self.repo_root,
            "reopen",
            task_id="AIPOS-348-REOPEN-BLOCKED",
            actor="dev.codex.local",
            reason="Input arrived",
        )

        self.assertEqual(result["verdict"], "PASS")
        target = self.repo_root / "5_tasks/queue/pending/aipos-348-reopen-blocked.md"
        text = target.read_text(encoding="utf-8")
        self.assertIn("status: pending", text)
        self.assertIn("reopened_by: dev.codex.local", text)

    def test_reopen_pending_still_rejected(self) -> None:
        """No regression: reopen from pending still rejected."""
        self.write_task("AIPOS-348-REOPEN-PENDING", queue_state="pending")

        result = mutate_queue_task(
            self.repo_root,
            "reopen",
            task_id="AIPOS-348-REOPEN-PENDING",
            actor="dev.codex.local",
            reason="Nope",
            dry_run=True,
        )

        self.assertEqual(result["verdict"], "BLOCK")

    def test_reopen_requires_reason(self) -> None:
        """Reopen still requires a non-empty reason."""
        self.write_task(
            "AIPOS-348-REOPEN-NO-REASON",
            queue_state="completed",
            completed_by="dev.codex.local",
            completed_at="2026-04-30T00:00:00Z",
            last_session_id="session_AIPOS-348-REOPEN-NO-REASON_1_dev",
        )

        with self.assertRaisesRegex(ValueError, "reason is required"):
            mutate_queue_task(
                self.repo_root,
                "reopen",
                task_id="AIPOS-348-REOPEN-NO-REASON",
                actor="dev.codex.local",
                reason=" ",
                dry_run=True,
            )


class Aipos348CLIIntegrationTests(unittest.TestCase):
    """CLI integration tests for AIPOS-348."""

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
        if "audit" in metadata:
            lines.append(f"audit: {metadata['audit']}")
        for key in (
            "claim_id",
            "active_session_id",
            "last_session_id",
            "claimed_by",
            "claimed_at",
            "completed_by",
            "completed_at",
        ):
            if key in metadata and metadata[key] is not None:
                lines.append(f"{key}: {metadata[key]}")
        lines.extend(["---", "Task body", ""])
        filename = str(metadata.get("filename", f"{task_id.lower()}.md"))
        return self.write_file(f"5_tasks/queue/{queue_state}/{filename}", "\n".join(lines))

    def write_verdict(self, task_id: str, verdict_id: str, verdict: str) -> Path:
        content = (
            f"---\n"
            f"verdict_id: {verdict_id}\n"
            f"reviewed_task_id: {task_id}\n"
            f"verdict: {verdict}\n"
            f"verdict_at: '2026-08-06T00:00:00Z'\n"
            f"auditor: audit.codex.local\n"
            f"---\n"
            f"Audit verdict body\n"
        )
        return self.write_file(
            f"5_tasks/records/audit_verdicts/{task_id}/{verdict_id}.md",
            content,
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

    def test_cli_complete_blocked_by_audit_gate(self) -> None:
        """CLI: queue complete blocked when audit: required and no PASS verdict."""
        self.write_task(
            "AIPOS-348-CLI-BLOCK",
            queue_state="claimed",
            audit="required",
            claim_id="claim_AIPOS-348-CLI-BLOCK_1_dev",
            active_session_id="session_AIPOS-348-CLI-BLOCK_1_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )

        exit_code, output = self.run_cli_json([
            "queue", "complete",
            "--task-id", "AIPOS-348-CLI-BLOCK",
            "--actor", "dev.codex.local",
            "--report-link", "https://example.com/report",
            "--dry-run",
            "--json",
        ])

        self.assertEqual(exit_code, 1)
        self.assertEqual(output["verdict"], "BLOCK")
        self.assertTrue(any("audit: required" in r for r in output["blocking_reasons"]))

    def test_cli_complete_allowed_with_pass_verdict(self) -> None:
        """CLI: queue complete allowed when audit: required and PASS verdict exists."""
        self.write_task(
            "AIPOS-348-CLI-PASS",
            queue_state="claimed",
            audit="required",
            claim_id="claim_AIPOS-348-CLI-PASS_20260806_000000Z_dev",
            active_session_id="session_AIPOS-348-CLI-PASS_20260806_000000Z_dev",
            claimed_by="dev.codex.local",
            claimed_at="2026-04-30T00:00:00Z",
        )
        self.write_verdict("AIPOS-348-CLI-PASS", "verdict_PASS_cli_001", "PASS")

        exit_code, output = self.run_cli_json([
            "queue", "complete",
            "--task-id", "AIPOS-348-CLI-PASS",
            "--actor", "dev.codex.local",
            "--report-link", "https://example.com/report",
            "--dry-run",
            "--json",
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn(output["verdict"], ("PASS", "WARN"))  # WARN acceptable if non-blocking warnings present
        self.assertFalse(output.get("blocking_reasons"))

    def test_cli_reopen_from_completed(self) -> None:
        """CLI: queue reopen from completed succeeds."""
        self.write_task(
            "AIPOS-348-CLI-REOPEN",
            queue_state="completed",
            completed_by="dev.codex.local",
            completed_at="2026-04-30T00:00:00Z",
            last_session_id="session_AIPOS-348-CLI-REOPEN_20260806_000000Z_dev",
        )

        exit_code, output = self.run_cli_json([
            "queue", "reopen",
            "--task-id", "AIPOS-348-CLI-REOPEN",
            "--actor", "dev.codex.local",
            "--reason", "Incorrect completion",
            "--dry-run",
            "--json",
        ])

        self.assertEqual(exit_code, 0)
        self.assertIn(output["verdict"], ("PASS", "WARN"))  # WARN acceptable if non-blocking warnings present
        self.assertEqual(output["from_state"], "completed")
        self.assertEqual(output["to_state"], "pending")


class Aipos348ReopenSourceStatesConstantTests(unittest.TestCase):
    """Verify the REOPEN_SOURCE_STATES constant."""

    def test_reopen_source_states_includes_blocked_and_completed(self) -> None:
        self.assertIn("blocked", REOPEN_SOURCE_STATES)
        self.assertIn("completed", REOPEN_SOURCE_STATES)
        self.assertNotIn("pending", REOPEN_SOURCE_STATES)
        self.assertNotIn("claimed", REOPEN_SOURCE_STATES)


if __name__ == "__main__":
    unittest.main()
