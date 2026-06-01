from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.agent_profiles import load_agent_profiles
from tools.aipos_cli.custom_agent_profiles import (
    build_profile_draft,
    confirm_profile_draft,
    load_custom_registry,
    registry_path,
)


class CustomAgentProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def payload(self, **instance_overrides: object) -> dict[str, object]:
        instance: dict[str, object] = {
            "agent_instance": "agent-04",
            "display_name": "My Local Writer",
            "identity_status": "active",
            "enabled": True,
            "capabilities": ["repo_edit"],
            "write_scopes": ["repository_workspace"],
            "provenance": {
                "vendor": "rare-local-vendor",
                "harness": "private-adapter-x",
                "model_family": "model-family-z",
                "host": "owner-workstation",
            },
        }
        instance.update(instance_overrides)
        return {"action": "upsert", "agent_id": "owner_agents", "instance": instance}

    def confirm(self, draft: dict[str, object]) -> dict[str, object]:
        return confirm_profile_draft(
            self.repo_root,
            draft,
            actor="owner",
            owner_confirmation_token="OWNER_CONFIRMED",
        )

    def test_draft_writes_nothing_then_confirm_writes_and_revalidates(self) -> None:
        target = registry_path(self.repo_root)
        draft = build_profile_draft(self.repo_root, self.payload(), actor="owner")

        self.assertTrue(draft["ok"])
        self.assertEqual(draft["verdict"], "NEEDS_OWNER")
        self.assertFalse(target.exists())
        self.assertTrue(draft["owner_confirmation_required"])

        confirmed = self.confirm(draft)

        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["verdict"], "PASS")
        self.assertTrue(target.exists())
        self.assertTrue(confirmed["data"]["registry_validation"]["ok"])

    def test_custom_profile_loader_keeps_bundled_defaults_and_adds_custom_instance(self) -> None:
        self.confirm(build_profile_draft(self.repo_root, self.payload(), actor="owner"))

        profiles = load_agent_profiles(self.repo_root)

        self.assertIn("agent-01", profiles["instance_index"])
        self.assertIn("agent-04", profiles["instance_index"])
        self.assertEqual(
            profiles["instance_index"]["agent-04"]["instance"]["display_name"],
            "My Local Writer",
        )

    def test_bundled_canonical_id_collision_blocks(self) -> None:
        draft = build_profile_draft(
            self.repo_root,
            self.payload(agent_instance="agent-01"),
            actor="owner",
        )

        self.assertEqual(draft["verdict"], "BLOCK")
        self.assertIn(
            "custom canonical agent_instance conflicts with bundled default: agent-01",
            draft["blocking_reasons"],
        )

    def test_bundled_legacy_id_collision_blocks(self) -> None:
        draft = build_profile_draft(
            self.repo_root,
            self.payload(legacy_instance_ids=["dev.claude.cc.local"]),
            actor="owner",
        )

        self.assertEqual(draft["verdict"], "BLOCK")
        self.assertIn(
            "custom legacy agent_instance conflicts with bundled legacy ID: dev.claude.cc.local",
            draft["blocking_reasons"],
        )

    def test_non_empty_runtime_env_blocks_secret_prone_registry_data(self) -> None:
        draft = build_profile_draft(
            self.repo_root,
            self.payload(runtime_env={"API_KEY": "do-not-store"}),
            actor="owner",
        )

        self.assertEqual(draft["verdict"], "BLOCK")
        self.assertTrue(any("runtime_env must remain empty" in reason for reason in draft["blocking_reasons"]))

    def test_display_name_edit_preserves_canonical_identity(self) -> None:
        self.confirm(build_profile_draft(self.repo_root, self.payload(), actor="owner"))

        update = build_profile_draft(
            self.repo_root,
            self.payload(display_name="Renamed Session"),
            actor="owner",
        )
        self.confirm(update)
        registry, blocking = load_custom_registry(self.repo_root)

        self.assertEqual(blocking, [])
        instances = registry["profiles"][0]["instances"]
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0]["agent_instance"], "agent-04")
        self.assertEqual(instances[0]["display_name"], "Renamed Session")

    def test_provenance_accepts_open_vocabulary_strings(self) -> None:
        draft = build_profile_draft(
            self.repo_root,
            self.payload(
                provenance={
                    "vendor": "small-lab-42",
                    "harness": "custom-shell-wrapper",
                    "model_family": "experimental-family",
                    "host": "edge-box-alpha",
                }
            ),
            actor="owner",
        )

        self.assertNotEqual(draft["verdict"], "BLOCK")

    def test_supersession_is_additive_and_does_not_rewrite_history(self) -> None:
        self.confirm(build_profile_draft(self.repo_root, self.payload(), actor="owner"))
        historical = self.repo_root / "5_tasks" / "queue" / "completed" / "historical.md"
        historical.write_text("agent_instance: agent-04\n", encoding="utf-8")

        supersede = build_profile_draft(
            self.repo_root,
            {
                "action": "supersede",
                "agent_id": "owner_agents",
                "agent_instance": "agent-04",
                "replacement": {
                    "agent_instance": "agent-05",
                    "display_name": "Replacement Session",
                    "capabilities": ["repo_edit"],
                    "provenance": {"vendor": "replacement-vendor"},
                },
            },
            actor="owner",
        )
        self.confirm(supersede)
        registry, _blocking = load_custom_registry(self.repo_root)
        instances = registry["profiles"][0]["instances"]

        self.assertEqual([item["agent_instance"] for item in instances], ["agent-04", "agent-05"])
        self.assertEqual(instances[0]["identity_status"], "superseded")
        self.assertFalse(instances[0]["enabled"])
        self.assertEqual(instances[1]["supersedes_instance_ids"], ["agent-04"])
        self.assertEqual(historical.read_text(encoding="utf-8"), "agent_instance: agent-04\n")

    def test_capability_edit_is_owner_visible(self) -> None:
        self.confirm(build_profile_draft(self.repo_root, self.payload(), actor="owner"))

        draft = build_profile_draft(
            self.repo_root,
            self.payload(capabilities=["repo_edit", "test_run"]),
            actor="owner",
        )

        self.assertIn("capabilities", draft["summary"]["owner_visible_fields"])
        self.assertTrue(any("capabilities" in reason for reason in draft["owner_confirmation_reasons"]))

    def test_stale_preview_blocks_confirm(self) -> None:
        draft = build_profile_draft(self.repo_root, self.payload(), actor="owner")
        self.confirm(build_profile_draft(self.repo_root, self.payload(agent_instance="agent-06"), actor="owner"))

        result = self.confirm(draft)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("profile draft snapshot mismatch; run draft again", result["blocking_reasons"])

    def test_cli_draft_confirm_list_inspect_and_validate(self) -> None:
        payload_path = self.repo_root / "payload.json"
        payload_path.write_text(json.dumps(self.payload()), encoding="utf-8")
        original_cwd = Path.cwd()
        try:
            import os

            os.chdir(self.repo_root)
            dry = self.cli_json(["agent-profile", "draft", "--from-json", str(payload_path), "--actor", "owner", "--json"])
            dry_path = self.repo_root / "dry.json"
            dry_path.write_text(json.dumps(dry), encoding="utf-8")
            confirmed = self.cli_json(
                [
                    "agent-profile",
                    "confirm",
                    "--from-json",
                    str(dry_path),
                    "--actor",
                    "owner",
                    "--owner-confirmation-token",
                    "OWNER_CONFIRMED",
                    "--json",
                ]
            )
            listed = self.cli_json(["agent-profile", "list", "--json"])
            inspected = self.cli_json(["agent-profile", "inspect", "--agent-instance", "agent-04", "--json"])
            validated = self.cli_json(["agent-profile", "validate", "--json"])
        finally:
            os.chdir(original_cwd)

        self.assertTrue(confirmed["ok"])
        self.assertEqual(listed["profiles"][0]["instances"][0]["display_name"], "My Local Writer")
        self.assertEqual(inspected["instance"]["agent_instance"], "agent-04")
        self.assertTrue(validated["ok"])

    def cli_json(self, argv: list[str]) -> dict[str, object]:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(argv)
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
