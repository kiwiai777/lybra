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

from web.board.app import _api_routes, dispatch_api_request


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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def assert_board_read_only(self, paths: list[str]) -> None:
        """AIPOS-F171 件③(gap #155): 看板写接口全部下线 —— 同一路由 POST 一律 405 + BOARD_READ_ONLY(文案读 verbs.schema
        board_readonly), 不调用任何写实现, 工作区零写入。"""
        root = Path(self.repo_root)
        def _snapshot() -> list[tuple[str, int]]:
            return sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
        before = _snapshot()
        for path in paths:
            status, data = dispatch_api_request(method="POST", path=path, routes=_api_routes(root))
            print(f"[F171 read-only] POST {path} -> {status} {data.get('error')} {data.get('message')}")
            self.assertEqual(status, 405, path)
            self.assertEqual(data["error"], "BOARD_READ_ONLY", path)
            self.assertFalse(data["ok"])
        self.assertEqual(before, _snapshot(), "read-only board must write nothing")

    # -----------------------------------------------------------------------
    # Mode: directory (existing behavior, zero regression)
    # -----------------------------------------------------------------------

    def test_directory_mode_preview_success(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    def test_directory_mode_preview_default_mode(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

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
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    def test_file_mode_preview_path_not_exists(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    def test_file_mode_preview_not_yaml(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    def test_file_mode_preview_not_file(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    def test_file_mode_preview_schema_validation_failure(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    # -----------------------------------------------------------------------
    # Smart hint: .yaml in directory mode
    # -----------------------------------------------------------------------

    def test_directory_mode_yaml_hint(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    # -----------------------------------------------------------------------
    # Error humanization: all error paths carry error_code + error_i18n_key
    # -----------------------------------------------------------------------

    def test_all_error_paths_have_i18n_key(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview', '/api/project-structure/import'])

    def test_no_unknown_error_in_responses(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/preview'])

    # -----------------------------------------------------------------------
    # Import route: file mode
    # -----------------------------------------------------------------------

    def test_file_mode_import_missing_project_id(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/import'])

    def test_file_mode_import_invalid_project_id(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/import'])

    def test_directory_mode_import_yaml_hint(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/project-structure/import'])


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
