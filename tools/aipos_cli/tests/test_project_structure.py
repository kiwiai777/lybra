"""AIPOS-293: Tests for project structure file schema + export/import.

Coverage:
- Schema validation (required fields, credential detection)
- YAML emit/parse roundtrip
- Export on a mock workspace
- Import creates standard five-piece set + migration checklist
- Import idempotent re-run
- Non-empty directory protection
- Zero credential values in exported structure
- Export-import full roundtrip
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.project_structure import (
    CANONICAL_GOVERNANCE_FILES,
    MIGRATION_CHECKLIST_FILENAME,
    SCHEMA_VERSION,
    STANDARD_FIVE_PIECE,
    _check_no_credentials,
    emit_yaml,
    export_project_structure,
    export_project_to_yaml,
    import_project_structure,
    parse_yaml,
    validate_structure,
)


class SchemaValidationTests(unittest.TestCase):
    """S1: Schema validation tests."""

    def test_valid_minimal_structure(self) -> None:
        data = {"schema_version": 1, "project_name": "test-project"}
        errors = validate_structure(data)
        self.assertEqual(errors, [])

    def test_missing_schema_version(self) -> None:
        data = {"project_name": "test"}
        errors = validate_structure(data)
        self.assertIn("missing required field: schema_version", errors)

    def test_wrong_schema_version(self) -> None:
        data = {"schema_version": 99, "project_name": "test"}
        errors = validate_structure(data)
        self.assertTrue(any("unsupported schema_version" in e for e in errors))

    def test_missing_project_name(self) -> None:
        data = {"schema_version": 1}
        errors = validate_structure(data)
        self.assertIn("missing required field: project_name", errors)

    def test_empty_project_name(self) -> None:
        data = {"schema_version": 1, "project_name": ""}
        errors = validate_structure(data)
        self.assertTrue(any("project_name must be a non-empty string" in e for e in errors))

    def test_code_repos_must_be_list(self) -> None:
        data = {"schema_version": 1, "project_name": "test", "code_repos": "not-a-list"}
        errors = validate_structure(data)
        self.assertTrue(any("code_repos must be a list" in e for e in errors))

    def test_governance_files_must_be_dict(self) -> None:
        data = {"schema_version": 1, "project_name": "test", "governance_files": "not-a-dict"}
        errors = validate_structure(data)
        self.assertTrue(any("governance_files must be a mapping" in e for e in errors))

    def test_credential_detection_in_keys(self) -> None:
        data = {
            "schema_version": 1,
            "project_name": "test",
            "token": "sk-1234567890abcdef",  # credential-like key with value
        }
        errors = validate_structure(data)
        self.assertTrue(any("credential-like key" in e for e in errors))

    def test_no_false_positive_on_empty_token(self) -> None:
        data = {
            "schema_version": 1,
            "project_name": "test",
            "token": "",  # empty is fine
        }
        errors = validate_structure(data)
        self.assertEqual(errors, [])


class YamlRoundtripTests(unittest.TestCase):
    """S1: YAML emit/parse roundtrip tests."""

    def test_simple_dict_roundtrip(self) -> None:
        data = {
            "schema_version": 1,
            "project_name": "test-project",
            "description": "A test project",
        }
        yaml_text = emit_yaml(data)
        parsed = parse_yaml(yaml_text)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertEqual(parsed["project_name"], "test-project")
        self.assertEqual(parsed["description"], "A test project")

    def test_list_roundtrip(self) -> None:
        data = {
            "code_repos": ["/path/a", "/path/b"],
        }
        yaml_text = emit_yaml(data)
        parsed = parse_yaml(yaml_text)
        self.assertEqual(parsed["code_repos"], ["/path/a", "/path/b"])

    def test_nested_dict_roundtrip(self) -> None:
        data = {
            "governance_files": {
                "decision_log": "governance/decision_log.md",
                "project_status": "governance/project_status.md",
            },
        }
        yaml_text = emit_yaml(data)
        parsed = parse_yaml(yaml_text)
        self.assertEqual(parsed["governance_files"]["decision_log"], "governance/decision_log.md")

    def test_list_of_dicts_roundtrip(self) -> None:
        data = {
            "roles": [
                {"file": "CLAUDE.md", "kind": "role_charter"},
                {"file": "AGENTS.md", "kind": "executor"},
            ],
        }
        yaml_text = emit_yaml(data)
        parsed = parse_yaml(yaml_text)
        self.assertEqual(len(parsed["roles"]), 2)
        self.assertEqual(parsed["roles"][0]["file"], "CLAUDE.md")
        self.assertEqual(parsed["roles"][0]["kind"], "role_charter")
        self.assertEqual(parsed["roles"][1]["file"], "AGENTS.md")

    def test_full_structure_roundtrip(self) -> None:
        data = {
            "schema_version": 1,
            "project_name": "lybra",
            "description": "AI Project OS",
            "code_repos": ["/home/kiwi/lybra"],
            "governance_files": {
                "decision_log": "governance/decision_log.md",
                "project_status": "governance/project_status.md",
            },
            "roles": [
                {"file": "CLAUDE.md", "kind": "charter"},
            ],
            "queue_summary": {"pending": 5, "claimed": 2},
        }
        yaml_text = emit_yaml(data)
        parsed = parse_yaml(yaml_text)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertEqual(parsed["project_name"], "lybra")
        self.assertEqual(parsed["code_repos"], ["/home/kiwi/lybra"])
        self.assertEqual(parsed["governance_files"]["decision_log"], "governance/decision_log.md")
        self.assertEqual(len(parsed["roles"]), 1)
        self.assertEqual(parsed["roles"][0]["kind"], "charter")
        self.assertEqual(parsed["queue_summary"]["pending"], 5)


class ExportTests(unittest.TestCase):
    """S2: Export tests."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        # Create a minimal workspace structure
        (self.root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (self.root / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
        (self.root / "5_tasks" / "queue" / "completed").mkdir(parents=True)
        (self.root / "5_tasks" / "queue" / "blocked").mkdir(parents=True)
        (self.root / "governance").mkdir(parents=True)
        (self.root / "stage_archive").mkdir(parents=True)
        (self.root / "workspace_artifacts").mkdir(parents=True)
        # project.json
        (self.root / "project.json").write_text(json.dumps({
            "project": "test-export",
            "code_repo": "/code/test",
            "registered_at": "2026-01-01T00:00:00Z",
            "registered_by": "owner",
            "config_version": 1,
        }))
        # Governance files
        (self.root / "governance" / "decision_log.md").write_text("# Decision Log\n")
        (self.root / "governance" / "project_status.md").write_text("# Status\n")
        # README
        (self.root / "README.md").write_text("# Test Project\n\nA test description.\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_export_captures_project_name(self) -> None:
        structure = export_project_structure(self.root)
        self.assertEqual(structure["project_name"], "test-export")

    def test_export_captures_code_repos(self) -> None:
        structure = export_project_structure(self.root)
        self.assertIn("/code/test", structure["code_repos"])

    def test_export_captures_governance_files(self) -> None:
        structure = export_project_structure(self.root)
        self.assertIn("decision_log", structure["governance_files"])
        self.assertIn("project_status", structure["governance_files"])

    def test_export_captures_description(self) -> None:
        structure = export_project_structure(self.root)
        self.assertEqual(structure["description"], "A test description.")

    def test_export_no_credentials(self) -> None:
        """Red line: exported structure must contain zero credential values."""
        structure = export_project_structure(self.root)
        findings = _check_no_credentials(structure)
        self.assertEqual(findings, [])

    def test_export_to_yaml_creates_file(self) -> None:
        output = self.root / "lybra-project.yaml"
        result = export_project_to_yaml(self.root)
        self.assertTrue(result["ok"])
        self.assertTrue(output.exists())
        self.assertGreater(result["yaml_byte_size"], 0)

    def test_export_to_yaml_custom_output(self) -> None:
        custom_output = Path(self.temp_dir.name) / "custom" / "output.yaml"
        result = export_project_to_yaml(self.root, output_path=custom_output)
        self.assertTrue(result["ok"])
        self.assertTrue(custom_output.exists())


class ImportTests(unittest.TestCase):
    """S3: Import tests."""

    def _make_structure_file(self, tmpdir: Path, name: str = "test-import") -> Path:
        """Create a minimal structure file for import testing."""
        data = {
            "schema_version": 1,
            "project_name": name,
            "code_repos": ["/code/test"],
            "governance_files": {
                "decision_log": "governance/decision_log.md",
            },
            "doc_manifest": [
                {"source_path": "governance/notes.md", "target_path": "governance/notes.md", "kind": "governance"},
                {"source_path": "README.md", "target_path": "README.md", "kind": "general"},
            ],
        }
        yaml_text = emit_yaml(data)
        structure_file = tmpdir / "lybra-project.yaml"
        structure_file.write_text(yaml_text, encoding="utf-8")
        return structure_file

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_creates_standard_five_piece(self) -> None:
        """S3: Import creates the standard five-piece set."""
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "output"
        result = import_project_structure(structure_file, output)
        self.assertTrue(result["ok"])
        # Check all standard directories exist
        for rel_dir in STANDARD_FIVE_PIECE:
            self.assertTrue((output / rel_dir).is_dir(), f"Missing: {rel_dir}")

    def test_import_creates_project_json(self) -> None:
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "output"
        import_project_structure(structure_file, output)
        pj = output / "project.json"
        self.assertTrue(pj.is_file())
        data = json.loads(pj.read_text())
        self.assertEqual(data["project"], "test-import")

    def test_import_creates_lybra_ignore(self) -> None:
        """S3: .lybra/ with ignore rules for leak prevention."""
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "output"
        import_project_structure(structure_file, output)
        gitignore = output / ".lybra" / ".gitignore"
        self.assertTrue(gitignore.is_file())
        content = gitignore.read_text()
        self.assertIn("*.env", content)
        self.assertIn("*.secret", content)
        self.assertIn("connection.json", content)

    def test_import_creates_migration_checklist(self) -> None:
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "output"
        result = import_project_structure(structure_file, output)
        self.assertTrue(result["ok"])
        checklist = output / MIGRATION_CHECKLIST_FILENAME
        self.assertTrue(checklist.is_file())
        content = checklist.read_text()
        self.assertIn("Migration Checklist", content)
        self.assertIn("NEVER deletes", content)

    def test_import_idempotent_rerun(self) -> None:
        """S3: Re-running import on the same output is safe (skips existing)."""
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "output"
        # First run
        result1 = import_project_structure(structure_file, output)
        self.assertTrue(result1["ok"])
        self.assertEqual(len(result1["skipped_existing"]), 0)
        # Second run (idempotent)
        result2 = import_project_structure(structure_file, output)
        self.assertTrue(result2["ok"])
        self.assertGreater(len(result2["skipped_existing"]), 0)

    def test_import_non_empty_directory_protection(self) -> None:
        """S3: Import refuses non-empty non-workspace directories."""
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "nonempty"
        output.mkdir()
        (output / "existing.txt").write_text("I exist")
        result = import_project_structure(structure_file, output)
        self.assertFalse(result["ok"])
        self.assertTrue(any("non-empty" in r for r in result["blocking_reasons"]))

    def test_import_dry_run_no_writes(self) -> None:
        """S3: Dry run doesn't create anything."""
        structure_file = self._make_structure_file(self.tmpdir)
        output = self.tmpdir / "dryrun"
        result = import_project_structure(structure_file, output, dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertFalse(output.exists())

    def test_import_missing_structure_file(self) -> None:
        result = import_project_structure("/nonexistent/file.yaml", self.tmpdir / "out")
        self.assertFalse(result["ok"])
        self.assertTrue(any("not found" in r for r in result["blocking_reasons"]))

    def test_import_invalid_structure_file(self) -> None:
        bad_file = self.tmpdir / "bad.yaml"
        bad_file.write_text("schema_version: 99\nproject_name: test\n", encoding="utf-8")
        result = import_project_structure(bad_file, self.tmpdir / "out")
        self.assertFalse(result["ok"])
        self.assertTrue(any("validation failed" in r for r in result["blocking_reasons"]))

    def test_import_credential_in_structure_file_blocked(self) -> None:
        """Red line: import blocks if structure file contains credential values."""
        data = {
            "schema_version": 1,
            "project_name": "evil",
            "token": "sk-1234567890abcdef",  # credential value!
        }
        yaml_text = emit_yaml(data)
        bad_file = self.tmpdir / "evil.yaml"
        bad_file.write_text(yaml_text, encoding="utf-8")
        result = import_project_structure(bad_file, self.tmpdir / "out")
        self.assertFalse(result["ok"])
        self.assertTrue(any("credential" in r.lower() or "Credential" in r for r in result["blocking_reasons"]))


class RoundtripTests(unittest.TestCase):
    """S5: Full export-import roundtrip tests."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source = Path(self.temp_dir.name) / "source"
        self.source.mkdir(parents=True)
        # Create a realistic workspace
        (self.source / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (self.source / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
        (self.source / "5_tasks" / "queue" / "completed").mkdir(parents=True)
        (self.source / "5_tasks" / "queue" / "blocked").mkdir(parents=True)
        (self.source / "5_tasks" / "records").mkdir(parents=True)
        (self.source / "5_tasks" / "drafts").mkdir(parents=True)
        (self.source / "governance").mkdir(parents=True)
        (self.source / "stage_archive").mkdir(parents=True)
        (self.source / "workspace_artifacts").mkdir(parents=True)
        (self.source / "project.json").write_text(json.dumps({
            "project": "roundtrip-test",
            "code_repo": "/code/roundtrip",
            "registered_at": "2026-01-01T00:00:00Z",
            "registered_by": "owner",
            "config_version": 1,
        }))
        (self.source / "governance" / "decision_log.md").write_text("# Decisions\n")
        (self.source / "governance" / "project_status.md").write_text("# Status\n")
        (self.source / "governance" / "roadmap.md").write_text("# Roadmap\n")
        (self.source / "README.md").write_text("# Roundtrip\n\nTest project for roundtrip.\n")
        # A task card
        (self.source / "5_tasks" / "queue" / "pending" / "test-task.md").write_text(
            "---\ntask_id: TEST-1\ntitle: Test task\n---\n# Test\n"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_export_import_roundtrip_preserves_project_name(self) -> None:
        """S5: Export then import preserves project name."""
        # Export
        export_result = export_project_to_yaml(self.source)
        self.assertTrue(export_result["ok"])
        structure_file = Path(export_result["output_path"])

        # Import
        output = Path(self.temp_dir.name) / "imported"
        import_result = import_project_structure(structure_file, output)
        self.assertTrue(import_result["ok"])

        # Verify project name
        pj = json.loads((output / "project.json").read_text())
        self.assertEqual(pj["project"], "roundtrip-test")

    def test_export_import_roundtrip_creates_full_skeleton(self) -> None:
        """S5: Roundtrip creates a complete skeleton with queue dirs."""
        export_result = export_project_to_yaml(self.source)
        structure_file = Path(export_result["output_path"])
        output = Path(self.temp_dir.name) / "imported"
        import_project_structure(structure_file, output)

        # Verify standard directories
        for state in ("pending", "claimed", "completed", "blocked"):
            self.assertTrue((output / "5_tasks" / "queue" / state).is_dir())
        self.assertTrue((output / "governance").is_dir())
        self.assertTrue((output / "stage_archive").is_dir())

    def test_export_no_credentials_in_yaml(self) -> None:
        """S5: Exported YAML contains zero credential values."""
        export_result = export_project_to_yaml(self.source)
        yaml_text = Path(export_result["output_path"]).read_text()
        # Parse it back and check
        parsed = parse_yaml(yaml_text)
        findings = _check_no_credentials(parsed)
        self.assertEqual(findings, [])


class NonRemovalTests(unittest.TestCase):
    """Red line: import never removes user files."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_does_not_delete_existing_workspace_files(self) -> None:
        """Red line verification: re-import on existing workspace doesn't delete files."""
        # Create a structure file
        data = {
            "schema_version": 1,
            "project_name": "safe-test",
            "governance_files": {},
        }
        yaml_text = emit_yaml(data)
        structure_file = self.tmpdir / "structure.yaml"
        structure_file.write_text(yaml_text)

        # Create an existing workspace with user files
        output = self.tmpdir / "workspace"
        (output / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (output / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
        (output / "5_tasks" / "queue" / "completed").mkdir(parents=True)
        (output / "5_tasks" / "queue" / "blocked").mkdir(parents=True)
        # Add project.json so it's recognized as an existing Lybra workspace
        (output / "project.json").write_text(json.dumps({
            "project": "safe-test",
            "config_version": 1,
        }))
        user_file = output / "5_tasks" / "queue" / "pending" / "user-task.md"
        user_file.write_text("---\ntask_id: USER-1\n---\n# User's task\n")

        # Import (re-run on existing workspace)
        result = import_project_structure(structure_file, output)
        self.assertTrue(result["ok"])

        # User file must still exist
        self.assertTrue(user_file.exists())
        self.assertEqual(user_file.read_text(), "---\ntask_id: USER-1\n---\n# User's task\n")


if __name__ == "__main__":
    unittest.main()
