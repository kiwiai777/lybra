from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools.aipos_cli import aipos_cli
from tools.aipos_cli.workspace_templates import (
    build_workspace_init_plan,
    discover_templates,
    execute_workspace_init,
    parse_var_items,
    product_repo_root,
)


class WorkspaceTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_discover_templates_lists_three_bundled_templates(self) -> None:
        discovered = discover_templates()
        self.assertEqual(set(discovered), {"blank", "consulting-engagement", "software-development"})
        for item in discovered.values():
            self.assertTrue(item["valid"])
            self.assertEqual(item["template_kind"], "workspace_project_skeleton")

    def test_parse_var_items_requires_key_value_form(self) -> None:
        self.assertEqual(parse_var_items(["project_id=demo", "client_name=Demo Client"]), {"project_id": "demo", "client_name": "Demo Client"})
        with self.assertRaises(ValueError):
            parse_var_items(["project_id"])

    def test_dry_run_renders_paths_and_writes_nothing(self) -> None:
        output = self.root / "workspace"
        result = build_workspace_init_plan(
            template="blank",
            output=output,
            variables={"project_id": "demo_project"},
            actor="owner",
            dry_run=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["owner_confirmation_required"])
        self.assertFalse(output.exists())
        paths = {item["path"] for item in result["planned_writes"]}
        self.assertIn("2_projects/demo_project/project_status.md", paths)
        self.assertIn("5_tasks/queue/pending/.keep", paths)

    def test_missing_required_variable_blocks_dry_run(self) -> None:
        result = build_workspace_init_plan(
            template="blank",
            output=self.root / "workspace",
            variables={},
            actor="owner",
            dry_run=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("missing required variable: project_id", result["blocking_reasons"])

    def test_existing_non_empty_output_blocks_dry_run(self) -> None:
        output = self.root / "workspace"
        output.mkdir()
        (output / "existing.txt").write_text("occupied", encoding="utf-8")
        result = build_workspace_init_plan(
            template="blank",
            output=output,
            variables={"project_id": "demo_project"},
            actor="owner",
            dry_run=True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("output path must be absent or an empty directory", result["blocking_reasons"])

    def test_unknown_placeholder_blocks_dry_run(self) -> None:
        template_root = self.root / "product"
        template_dir = template_root / "templates" / "bad-template"
        (template_dir / "tree").mkdir(parents=True)
        (template_dir / "manifest.md").write_text(
            "\n".join(
                [
                    "---",
                    "template_id: bad-template",
                    "template_version: 1",
                    "template_status: bundled",
                    "template_kind: workspace_project_skeleton",
                    "display_name: Bad Template",
                    "description: Bad placeholder test.",
                    "required_variables:",
                    "  - project_id",
                    "optional_variables: []",
                    "output_policy:",
                    "  output_must_be_absent_or_empty: true",
                    "  overwrite_existing_files: false",
                    "  remote_fetch_allowed: false",
                    "controlled_execute:",
                    "  dry_run_required: true",
                    "  confirm_required: true",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (template_dir / "tree" / "README.md").write_text("{{ missing_value }}\n", encoding="utf-8")
        result = build_workspace_init_plan(
            template="bad-template",
            output=self.root / "workspace",
            variables={"project_id": "demo_project"},
            actor="owner",
            dry_run=True,
            template_repo_root=template_root,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("unknown placeholder missing_value" in reason for reason in result["blocking_reasons"]))

    def test_execute_workspace_init_writes_expected_files(self) -> None:
        output = self.root / "workspace"
        result = execute_workspace_init(
            template="consulting-engagement",
            output=output,
            variables={
                "project_id": "acme_project",
                "client_id": "acme_client",
                "client_name": "Acme Client",
                "source_tag": "wechat_bot",
                "external_ref": "chat:redacted:001",
            },
            actor="owner",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["wrote"])
        self.assertTrue((output / "2_projects" / "acme_project" / "decision_log.md").exists())
        sample = output / "5_tasks" / "drafts" / "external_intake" / "sample-intake.md"
        self.assertIn("client_tag: acme_client", sample.read_text(encoding="utf-8"))

    def _cli_json(self, args: list[str]) -> dict[str, object]:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = aipos_cli.main(args)
        self.assertEqual(code, 0, stdout.getvalue())
        return json.loads(stdout.getvalue())

    def test_cli_dry_run_and_confirm_from_envelope(self) -> None:
        output = self.root / "cli-workspace"
        dry = self._cli_json(
            [
                "workspace",
                "init",
                "--template",
                "blank",
                "--output",
                str(output),
                "--actor",
                "owner",
                "--var",
                "project_id=cli_project",
                "--dry-run",
                "--json",
            ]
        )
        self.assertIn("dry_run_token", dry)
        self.assertFalse(output.exists())
        envelope_path = self.root / "dry.json"
        envelope_path.write_text(json.dumps(dry), encoding="utf-8")
        confirm = self._cli_json(
            [
                "workspace",
                "init",
                "--confirm",
                "--from-json",
                str(envelope_path),
                "--actor",
                "owner",
                "--owner-confirmation-token",
                "OWNER_CONFIRMED",
                "--json",
            ]
        )
        self.assertTrue(confirm["ok"])
        self.assertTrue((output / "2_projects" / "cli_project" / "roadmap.md").exists())

    def test_cli_confirm_actor_mismatch_blocks(self) -> None:
        output = self.root / "actor-workspace"
        dry = build_workspace_init_plan(
            template="blank",
            output=output,
            variables={"project_id": "actor_project"},
            actor="owner",
            dry_run=True,
        )
        dry["dry_run_snapshot_hash"] = "placeholder"
        envelope_path = self.root / "dry.json"
        envelope_path.write_text(json.dumps(dry), encoding="utf-8")
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = aipos_cli.main(
                [
                    "workspace",
                    "init",
                    "--confirm",
                    "--from-json",
                    str(envelope_path),
                    "--actor",
                    "other",
                    "--owner-confirmation-token",
                    "OWNER_CONFIRMED",
                    "--json",
                ]
            )
        self.assertEqual(code, 1)
        response = json.loads(stdout.getvalue())
        self.assertEqual(response["errors"][0]["category"], "ACTOR_MISMATCH")

    def test_cli_confirm_snapshot_mismatch_blocks(self) -> None:
        output = self.root / "snapshot-workspace"
        dry = self._cli_json(
            [
                "workspace",
                "init",
                "--template",
                "blank",
                "--output",
                str(output),
                "--actor",
                "owner",
                "--var",
                "project_id=snapshot_project",
                "--dry-run",
                "--json",
            ]
        )
        output.mkdir()
        (output / "collision.txt").write_text("changed", encoding="utf-8")
        envelope_path = self.root / "dry.json"
        envelope_path.write_text(json.dumps(dry), encoding="utf-8")
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = aipos_cli.main(
                [
                    "workspace",
                    "init",
                    "--confirm",
                    "--from-json",
                    str(envelope_path),
                    "--actor",
                    "owner",
                    "--owner-confirmation-token",
                    "OWNER_CONFIRMED",
                    "--json",
                ]
            )
        self.assertEqual(code, 1)
        response = json.loads(stdout.getvalue())
        self.assertEqual(response["errors"][0]["category"], "REVALIDATION_FAILED")

    def test_bundled_templates_do_not_reference_private_paths(self) -> None:
        root = product_repo_root() / "templates"
        forbidden = ["/home/kiwi", "kw7home", "kiwiai.cloud", "/opt/kiwiai"]
        for path in root.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for value in forbidden:
                    self.assertNotIn(value, text, path.as_posix())


if __name__ == "__main__":
    unittest.main()
