from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.board_adapter import execute_dry_run, submit_external_intake
from tools.aipos_cli.draft_validator import list_drafts


class ExternalIntakeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for queue_state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / queue_state).mkdir(parents=True, exist_ok=True)
        # AIPOS-226 Phase 2b: project existence is the home 5_tasks/queue marker (legacy
        # 2_projects/<tag> probe removed).
        (self.repo_root / "acme_client" / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def payload(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "source_tag": "wechat_bot",
            "client_tag": "acme_client",
            "external_ref": "wechat:msg-1001",
            "title": "Prepare weekly delivery summary",
            "body": "Client asked for a concise delivery summary for this week.",
            "submitted_at": "2026-05-21T09:30:00Z",
            "submitter_ref": "contact_hash_123",
            "capability_scope": {
                "token_ref": "cap_external_intake_test",
                "operations": ["intake_submit"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
                "evidence_ref": "chat:msg-1001",
            },
        }
        data.update(overrides)
        return data

    def test_intake_submit_dry_run_and_confirm_writes_external_intake_draft_only(self) -> None:
        dry = submit_external_intake(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        self.assertTrue(dry["ok"])
        self.assertEqual(dry["verdict"], "PASS")
        self.assertIn("dry_run_id", dry)
        self.assertEqual(dry["planned_writes"][0]["path"].split("/")[:3], ["5_tasks", "drafts", "external_intake"])

        executed = execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "intake_submit")
        target = self.repo_root / executed["data"]["target_path"]
        self.assertTrue(target.exists())
        self.assertFalse(any((self.repo_root / "5_tasks" / "queue" / "pending").iterdir()))
        text = target.read_text(encoding="utf-8")
        self.assertIn("source_tag: wechat_bot", text)
        self.assertIn("client_tag: acme_client", text)
        self.assertIn("external_ref: 'wechat:msg-1001'", text)

    def test_missing_required_field_blocks_with_response_envelope(self) -> None:
        payload = self.payload()
        payload.pop("title")

        result = submit_external_intake(payload, dry_run=True, repo_root=self.repo_root, actor="bot.local")

        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Missing required field: title", result["blocking_reasons"])
        self.assertIsNone(result["dry_run_id"])

    def test_invalid_source_tag_blocks(self) -> None:
        result = submit_external_intake(
            self.payload(source_tag="WeChat Bot"),
            dry_run=True,
            repo_root=self.repo_root,
            actor="bot.local",
        )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("Invalid source_tag format", result["blocking_reasons"])

    def test_duplicate_safe_id_blocks_second_intake(self) -> None:
        dry = submit_external_intake(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        executed = execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)
        self.assertTrue(executed["ok"])

        second = submit_external_intake(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")

        self.assertEqual(second["verdict"], "BLOCK")
        self.assertIn("External intake draft already exists", "\n".join(second["blocking_reasons"]))

    def test_confirm_creates_missing_drafts_directory(self) -> None:
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

        dry = submit_external_intake(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        executed = execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)

        self.assertTrue(executed["ok"])
        self.assertTrue((self.repo_root / "5_tasks" / "drafts" / "external_intake").is_dir())

    def test_list_drafts_includes_nested_external_intake_draft(self) -> None:
        dry = submit_external_intake(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        execute_dry_run(dry["dry_run_id"], "bot.local", repo_root=self.repo_root)

        drafts = list_drafts(self.repo_root)

        self.assertEqual(drafts["total"], 1)
        self.assertEqual(drafts["drafts"][0]["verdict"], "PASS")
        self.assertIn("5_tasks/drafts/external_intake/", drafts["drafts"][0]["path"])

    def test_cli_stateless_confirm_uses_dry_run_json_proof(self) -> None:
        dry_path = self.repo_root / "dry.json"
        dry = submit_external_intake(self.payload(), dry_run=True, repo_root=self.repo_root, actor="bot.local")
        dry_path.write_text(json.dumps(dry), encoding="utf-8")

        original_cwd = Path.cwd()
        try:
            import os

            os.chdir(self.repo_root)
            with redirect_stdout(StringIO()):
                rc = main(["controlled-execute", "confirm", "--from-json", str(dry_path), "--actor", "bot.local", "--json"])
        finally:
            os.chdir(original_cwd)

        self.assertEqual(rc, 0)
        self.assertTrue((self.repo_root / dry["data"]["target_path"]).exists())


class ExternalProjectExistsTests(unittest.TestCase):
    """AIPOS-226 Phase 2b — project existence via home 5_tasks/queue marker (legacy removed)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_home_marker_exists(self) -> None:
        from tools.aipos_cli.external_intake_writer import _external_project_exists

        (self.repo_root / "homeproj" / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        self.assertTrue(_external_project_exists(self.repo_root, "homeproj"))

    def test_legacy_2projects_no_longer_exists(self) -> None:
        # AIPOS-226 Phase 2b: the legacy 2_projects/<tag> probe is removed, so a 2_projects
        # dir alone no longer marks a project as existing.
        from tools.aipos_cli.external_intake_writer import _external_project_exists

        (self.repo_root / "2_projects" / "acme_client").mkdir(parents=True, exist_ok=True)
        self.assertFalse(_external_project_exists(self.repo_root, "acme_client"))

    def test_neither_not_exists(self) -> None:
        from tools.aipos_cli.external_intake_writer import _external_project_exists

        self.assertFalse(_external_project_exists(self.repo_root, "ghost"))
        self.assertFalse(_external_project_exists(self.repo_root, ""))


if __name__ == "__main__":
    unittest.main()
