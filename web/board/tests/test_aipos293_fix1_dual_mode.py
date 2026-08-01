"""AIPOS-293 FIX-1: Option C dual mode (directory + structure file) + humanized errors.

Contract tests:
- Mode "directory": existing workspace directory preview/import walk-through
- Mode "file": structure file path preview/import walk-through
- Error humanization: 3+ error types assert i18n error_code + error_i18n_key present
- Smart hint: .yaml in directory mode returns suggest_file_mode=True
- Zero regression: existing directory mode behavior unchanged
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_post_routes, _api_routes, dispatch_api_request


class DualModeContractTests(unittest.TestCase):
    """Contract tests for AIPOS-293 FIX-1 dual mode import."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        # Minimal workspace structure
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "records").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "drafts").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "governance").mkdir(parents=True, exist_ok=True)
        # project.json
        (self.repo_root / "project.json").write_text(
            json.dumps({"project": "test-project", "code_repo": "/tmp/test-repo"}),
            encoding="utf-8",
        )
        self.routes = _api_routes(self.repo_root)
        self.post_routes = _api_post_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def post(self, path: str, body: dict) -> dict:
        status, data = dispatch_api_request(
            method="POST",
            path=path,
            routes=self.routes,
            post_routes=self.post_routes,
            body=body,
        )
        self.assertEqual(status, 200)
        return data

    # -----------------------------------------------------------------------
    # Mode: directory (existing behavior, zero regression)
    # -----------------------------------------------------------------------

    def test_directory_mode_preview_success(self) -> None:
        """Directory mode preview should work as before (zero regression)."""
        result = self.post("/api/project-structure/preview", {
            "mode": "directory",
            "workspace_path": str(self.repo_root),
        })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
        self.assertEqual(result.get("mode"), "directory")
        self.assertIn("project_name", result)
        self.assertIn("doc_count", result)

    def test_directory_mode_preview_default_mode(self) -> None:
        """Omitting mode should default to directory (backward compat)."""
        result = self.post("/api/project-structure/preview", {
            "workspace_path": str(self.repo_root),
        })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
        self.assertEqual(result.get("mode"), "directory")

    # -----------------------------------------------------------------------
    # Mode: file (new in FIX-1)
    # -----------------------------------------------------------------------

    def _create_structure_yaml(self, path: Path) -> None:
        """Create a minimal valid structure YAML file."""
        yaml_content = """# Lybra project structure file
# Schema version: 1
schema_version: 1
project_name: test-from-file
description: A test project from structure file
code_repos:
  - /tmp/test-repo
governance_files:
  decision_log: governance/decision_log.md
doc_manifest: []
queue_summary:
  pending: 2
  claimed: 1
"""
        path.write_text(yaml_content, encoding="utf-8")

    def test_file_mode_preview_success(self) -> None:
        """File mode preview should read and validate a structure file."""
        yaml_path = Path(self.temp_dir.name) / "test-structure.yaml"
        self._create_structure_yaml(yaml_path)

        result = self.post("/api/project-structure/preview", {
            "mode": "file",
            "structure_file_path": str(yaml_path),
        })
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
        self.assertEqual(result.get("mode"), "file")
        self.assertEqual(result.get("project_name"), "test-from-file")

    def test_file_mode_preview_path_not_exists(self) -> None:
        """File mode preview with non-existent path should return humanized error."""
        result = self.post("/api/project-structure/preview", {
            "mode": "file",
            "structure_file_path": "/nonexistent/path/structure.yaml",
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "path_not_exists")
        self.assertIn("error_i18n_key", result)
        self.assertEqual(result["error_i18n_key"], "error.import.path_not_exists")

    def test_file_mode_preview_not_yaml(self) -> None:
        """File mode preview with non-YAML file should return humanized error."""
        txt_path = Path(self.temp_dir.name) / "not-yaml.txt"
        txt_path.write_text("hello world", encoding="utf-8")

        result = self.post("/api/project-structure/preview", {
            "mode": "file",
            "structure_file_path": str(txt_path),
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "path_not_yaml")
        self.assertIn("error_i18n_key", result)

    def test_file_mode_preview_not_file(self) -> None:
        """File mode preview with directory path should return humanized error."""
        result = self.post("/api/project-structure/preview", {
            "mode": "file",
            "structure_file_path": str(self.repo_root),
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "path_not_file")
        self.assertIn("error_i18n_key", result)

    def test_file_mode_preview_schema_validation_failure(self) -> None:
        """File mode preview with invalid YAML should return schema validation error."""
        bad_yaml = Path(self.temp_dir.name) / "bad-structure.yaml"
        bad_yaml.write_text("foo: bar\n", encoding="utf-8")

        result = self.post("/api/project-structure/preview", {
            "mode": "file",
            "structure_file_path": str(bad_yaml),
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "schema_validation_failed")
        self.assertIn("error_i18n_key", result)
        self.assertIn("validation_errors", result)

    # -----------------------------------------------------------------------
    # Smart hint: .yaml in directory mode
    # -----------------------------------------------------------------------

    def test_directory_mode_yaml_hint(self) -> None:
        """Typing .yaml path in directory mode should suggest file mode."""
        yaml_path = Path(self.temp_dir.name) / "structure.yaml"
        yaml_path.write_text("# dummy", encoding="utf-8")

        result = self.post("/api/project-structure/preview", {
            "mode": "directory",
            "workspace_path": str(yaml_path),
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "path_not_directory")
        self.assertTrue(result.get("suggest_file_mode"), "Expected suggest_file_mode=True")

    # -----------------------------------------------------------------------
    # Error humanization: all error paths carry error_code + error_i18n_key
    # -----------------------------------------------------------------------

    def test_all_error_paths_have_i18n_key(self) -> None:
        """Every error response must carry error_code and error_i18n_key."""
        error_cases = [
            # (path, payload, expected_error_code)
            ("/api/project-structure/preview", {"mode": "directory"}, "path_required"),
            ("/api/project-structure/preview", {"mode": "file"}, "path_required"),
            ("/api/project-structure/preview", {"mode": "directory", "workspace_path": "/no/such/path"}, "path_not_exists"),
            ("/api/project-structure/preview", {"mode": "file", "structure_file_path": "/no/such/file.yaml"}, "path_not_exists"),
            # Import route validates project_id before path
            ("/api/project-structure/import", {"mode": "directory"}, "project_id_required"),
            ("/api/project-structure/import", {"mode": "file"}, "project_id_required"),
            ("/api/project-structure/import", {"mode": "directory", "project_id": "test"}, "path_required"),
            ("/api/project-structure/import", {"mode": "file", "project_id": "test"}, "path_required"),
        ]
        for path, payload, expected_code in error_cases:
            result = self.post(path, payload)
            self.assertFalse(result.get("ok"), f"Expected error for {payload}")
            self.assertEqual(
                result.get("error_code"), expected_code,
                f"Expected error_code={expected_code} for {payload}, got {result.get('error_code')}"
            )
            self.assertIn(
                "error_i18n_key", result,
                f"Missing error_i18n_key for {payload}"
            )

    def test_no_unknown_error_in_responses(self) -> None:
        """No error response should contain 'Unknown error' as its primary message."""
        error_cases = [
            {"mode": "directory"},
            {"mode": "directory", "workspace_path": "/no/such/path"},
            {"mode": "file", "structure_file_path": "/no/such/file.yaml"},
        ]
        for payload in error_cases:
            result = self.post("/api/project-structure/preview", payload)
            # The message/detail should not be "Unknown error"
            detail = result.get("error_detail", "")
            message = result.get("message", "")
            self.assertNotEqual(detail, "Unknown error", f"Found 'Unknown error' in detail for {payload}")
            self.assertNotEqual(message, "Unknown error", f"Found 'Unknown error' in message for {payload}")

    # -----------------------------------------------------------------------
    # Import route: file mode
    # -----------------------------------------------------------------------

    def test_file_mode_import_missing_project_id(self) -> None:
        """File mode import without project_id should return humanized error."""
        yaml_path = Path(self.temp_dir.name) / "structure.yaml"
        self._create_structure_yaml(yaml_path)

        result = self.post("/api/project-structure/import", {
            "mode": "file",
            "structure_file_path": str(yaml_path),
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "project_id_required")
        self.assertIn("error_i18n_key", result)

    def test_file_mode_import_invalid_project_id(self) -> None:
        """File mode import with invalid project_id should return humanized error."""
        yaml_path = Path(self.temp_dir.name) / "structure.yaml"
        self._create_structure_yaml(yaml_path)

        result = self.post("/api/project-structure/import", {
            "mode": "file",
            "structure_file_path": str(yaml_path),
            "project_id": "INVALID NAME!",
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "project_id_invalid")
        self.assertIn("error_i18n_key", result)

    def test_directory_mode_import_yaml_hint(self) -> None:
        """Directory mode import with .yaml path should suggest file mode."""
        yaml_path = Path(self.temp_dir.name) / "structure.yaml"
        yaml_path.write_text("# dummy", encoding="utf-8")

        result = self.post("/api/project-structure/import", {
            "mode": "directory",
            "workspace_path": str(yaml_path),
            "project_id": "test-project",
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error_code"), "path_not_directory")
        self.assertTrue(result.get("suggest_file_mode"))


class I18nKeyCompletenessTests(unittest.TestCase):
    """Ensure all error i18n keys exist in both zh and en dictionaries."""

    def test_error_keys_exist_in_both_locales(self) -> None:
        """All error.import.* keys must exist in both zh and en i18n dictionaries."""
        # Read the i18n.js file and check key presence
        i18n_path = Path(__file__).resolve().parents[1] / "static" / "i18n.js"
        content = i18n_path.read_text(encoding="utf-8")

        error_keys = [
            "error.import.path_required",
            "error.import.path_not_exists",
            "error.import.path_not_directory",
            "error.import.path_not_file",
            "error.import.path_not_yaml",
            "error.import.file_read_failed",
            "error.import.schema_validation_failed",
            "error.import.project_id_required",
            "error.import.project_id_invalid",
            "error.import.workspace_not_empty",
            "error.import.export_failed",
            "error.import.import_failed",
            "error.import.unexpected_error",
        ]

        for key in error_keys:
            # Count occurrences - should be at least 2 (zh + en)
            count = content.count(f"'{key}'")
            self.assertGreaterEqual(
                count, 2,
                f"Key '{key}' should appear in both zh and en dictionaries, found {count} times"
            )

    def test_mode_toggle_keys_exist_in_both_locales(self) -> None:
        """Mode toggle i18n keys must exist in both zh and en."""
        i18n_path = Path(__file__).resolve().parents[1] / "static" / "i18n.js"
        content = i18n_path.read_text(encoding="utf-8")

        mode_keys = [
            "overview.new_project_modal.import_mode_directory",
            "overview.new_project_modal.import_mode_file",
            "overview.new_project_modal.import_structure_file_path",
            "overview.new_project_modal.import_structure_file_hint",
            "overview.new_project_modal.import_structure_file_upload",
            "overview.new_project_modal.import_yaml_hint",
        ]

        for key in mode_keys:
            count = content.count(f"'{key}'")
            self.assertGreaterEqual(
                count, 2,
                f"Key '{key}' should appear in both zh and en dictionaries, found {count} times"
            )


if __name__ == "__main__":
    unittest.main()
