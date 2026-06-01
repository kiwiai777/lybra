from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.ai_assisted_authoring import (
    build_authoring_draft,
    confirm_authoring_draft,
)
from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter


class AiAssistedAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def intent(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "intent_id": "intent-demo-001",
            "submitted_at": "2026-06-01T12:00:00Z",
            "submitted_by": "owner",
            "requirement": "Prepare a concise status summary.",
        }
        data.update(overrides)
        return data

    def confirm(self, preview: dict[str, object]) -> dict[str, object]:
        return confirm_authoring_draft(
            self.repo_root,
            preview,
            actor="owner",
            owner_confirmation_token="OWNER_CONFIRMED",
        )

    def test_preview_writes_nothing_then_confirm_writes_standard_draft_and_provenance(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(),
            fixture_id="standard-simple",
            actor="owner",
        )

        self.assertEqual(preview["verdict"], "NEEDS_OWNER")
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "records").exists())

        confirmed = self.confirm(preview)

        self.assertEqual(confirmed["verdict"], "PASS")
        draft = self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md"
        provenance = self.repo_root / confirmed["data"]["provenance_path"]
        self.assertTrue(draft.exists())
        self.assertTrue(provenance.exists())
        metadata, _body, warnings = parse_markdown_frontmatter(draft.read_text(encoding="utf-8"))
        self.assertEqual(warnings, [])
        self.assertEqual(metadata["task_class"], "simple")
        self.assertTrue(metadata["needs_owner"])

    def test_provenance_is_non_secret_sidecar_without_raw_prompt_or_response(self) -> None:
        confirmed = self.confirm(
            build_authoring_draft(self.repo_root, self.intent(), fixture_id="standard-simple", actor="owner")
        )
        provenance = self.repo_root / confirmed["data"]["provenance_path"]
        text = provenance.read_text(encoding="utf-8")

        self.assertIn("adapter_id: fixture-only-v1", text)
        self.assertIn("prompt_template_version: 1", text)
        self.assertIn("network_call_performed: false", text)
        self.assertIn("credential_read_performed: false", text)
        self.assertIn("raw_prompt_persisted: false", text)
        self.assertIn("raw_response_persisted: false", text)
        self.assertNotIn(self.intent()["requirement"], text)

    def test_injection_escalation_fixture_blocks_deterministically(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(intent_id="intent-injection"),
            fixture_id="injection-escalation",
            actor="owner",
        )

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("AI-assisted proposal must keep needs_owner: true until Owner review", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: bypass_owner_review", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: request_credentials", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: authority_expansion", preview["blocking_reasons"])
        self.assertEqual(preview["planned_writes"], [])

    def test_fixture_adapter_failure_is_visible_and_writes_nothing(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(intent_id="intent-failure"),
            fixture_id="adapter-failure",
            actor="owner",
        )

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("Fixture adapter failure for visible degradation testing.", preview["blocking_reasons"])
        self.assertEqual(preview["planned_writes"], [])
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())

    def test_manual_retry_relationship_is_recorded(self) -> None:
        preview = build_authoring_draft(
            self.repo_root,
            self.intent(intent_id="intent-retry", retry_of="authoring_previous"),
            fixture_id="standard-simple",
            actor="owner",
        )
        confirmed = self.confirm(preview)
        provenance = self.repo_root / confirmed["data"]["provenance_path"]

        self.assertIn("retry_of: authoring_previous", provenance.read_text(encoding="utf-8"))

    def test_stale_preview_blocks_confirm(self) -> None:
        preview = build_authoring_draft(self.repo_root, self.intent(), fixture_id="standard-simple", actor="owner")
        target = self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md"
        target.parent.mkdir(parents=True)
        target.write_text("occupied\n", encoding="utf-8")

        confirmed = self.confirm(preview)

        self.assertEqual(confirmed["verdict"], "BLOCK")
        self.assertIn("AI authoring preview snapshot mismatch; run draft again", confirmed["blocking_reasons"])

    def test_wrong_owner_token_blocks_confirm(self) -> None:
        preview = build_authoring_draft(self.repo_root, self.intent(), fixture_id="standard-simple", actor="owner")

        result = confirm_authoring_draft(
            self.repo_root,
            preview,
            actor="owner",
            owner_confirmation_token="WRONG",
        )

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("owner confirmation token is required", result["blocking_reasons"][0])

    def test_fixture_only_flow_does_not_read_environment_credentials(self) -> None:
        with patch.dict(os.environ, {"LYBRA_LLM_API_KEY": "must-not-be-read"}):
            preview = build_authoring_draft(
                self.repo_root,
                self.intent(intent_id="intent-no-creds"),
                fixture_id="standard-simple",
                actor="owner",
            )

        self.assertFalse(preview["data"]["credential_read_performed"])
        self.assertNotIn("must-not-be-read", json.dumps(preview))

    def test_cli_draft_and_confirm(self) -> None:
        intent_path = self.repo_root / "intent.json"
        intent_path.write_text(json.dumps(self.intent()), encoding="utf-8")
        previous_cwd = Path.cwd()
        try:
            os.chdir(self.repo_root)
            preview = self.cli_json(
                [
                    "ai-author",
                    "draft",
                    "--intent-json",
                    str(intent_path),
                    "--fixture",
                    "standard-simple",
                    "--actor",
                    "owner",
                    "--json",
                ]
            )
            preview_path = self.repo_root / "preview.json"
            preview_path.write_text(json.dumps(preview), encoding="utf-8")
            confirmed = self.cli_json(
                [
                    "ai-author",
                    "confirm",
                    "--from-json",
                    str(preview_path),
                    "--actor",
                    "owner",
                    "--owner-confirmation-token",
                    "OWNER_CONFIRMED",
                    "--json",
                ]
            )
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(confirmed["verdict"], "PASS")
        self.assertTrue((self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md").exists())

    def cli_json(self, argv: list[str]) -> dict[str, object]:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(argv)
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
