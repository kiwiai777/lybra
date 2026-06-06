from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.task_loader import find_repo_root
from tools.aipos_cli.workspace_config import write_workspace_config


class WorkspaceRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace_root = self.root / "workspace"
        self.product_root = self.root / "product"
        for queue_state in ("pending", "claimed", "completed", "blocked"):
            (self.workspace_root / "5_tasks" / "queue" / queue_state).mkdir(parents=True, exist_ok=True)
        self.product_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_find_repo_root_uses_aipos_workspace_root_when_no_start_is_provided(self) -> None:
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.workspace_root)}), patch.object(Path, "cwd", return_value=self.product_root):
            self.assertEqual(find_repo_root(), self.workspace_root.resolve())

    def test_find_repo_root_rejects_invalid_aipos_workspace_root(self) -> None:
        invalid_root = self.root / "missing-workspace"
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(invalid_root)}), self.assertRaises(FileNotFoundError) as cm:
            find_repo_root()
        self.assertIn("AIPOS_WORKSPACE_ROOT does not contain 5_tasks/queue", str(cm.exception))

    def test_explicit_start_preserves_existing_parent_search_behavior(self) -> None:
        nested = self.workspace_root / "nested" / "child"
        nested.mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.product_root)}):
            self.assertEqual(find_repo_root(nested), self.workspace_root.resolve())

    def test_find_repo_root_uses_lybra_config_from_nested_cwd(self) -> None:
        write_workspace_config(self.workspace_root)
        nested = self.workspace_root / "2_projects" / "demo"
        nested.mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "cwd", return_value=nested):
            self.assertEqual(find_repo_root(), self.workspace_root.resolve())

    def test_find_repo_root_resolves_config_workspace_root_relative_to_config(self) -> None:
        config_path = self.workspace_root / ".lybra" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"workspace_root": "."}), encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "cwd", return_value=self.workspace_root / "5_tasks"):
            self.assertEqual(find_repo_root(), self.workspace_root.resolve())


if __name__ == "__main__":
    unittest.main()
