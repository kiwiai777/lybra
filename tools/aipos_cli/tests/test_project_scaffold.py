from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.task_loader import find_repo_root
from tools.aipos_cli.workspace_config import (
    HOME_ROOT_ENV,
    read_project_json,
    resolve_workspace_root,
    scaffold_project,
    set_project_repo,
    write_workspace_config,
)


class ProjectScaffoldTests(unittest.TestCase):
    """AIPOS-226 Slice 2 (Phase 2a): Owner scaffold + project.json + home-aware resolution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- scaffold_project --------------------------------------------------------

    def test_scaffold_creates_full_tree_and_project_json(self) -> None:
        root = scaffold_project(self.home, "demo", code_repo="~/code/demo", registered_by="alice")
        # queue 4 states
        for state in ("pending", "claimed", "completed", "blocked"):
            self.assertTrue((root / "5_tasks" / "queue" / state).is_dir(), state)
        # records/drafts/orchestration
        for sub in ("records", "drafts", "orchestration"):
            self.assertTrue((root / "5_tasks" / sub).is_dir(), sub)
        # governance single-file decision_log (ruling 1=B)
        decision_log = root / "governance" / "decision_log.md"
        self.assertTrue(decision_log.is_file())
        self.assertIn("# demo Decision Log", decision_log.read_text(encoding="utf-8"))
        # stage_archive + workspace_artifacts
        self.assertTrue((root / "stage_archive").is_dir())
        self.assertTrue((root / "workspace_artifacts").is_dir())
        # project.json with all 5 fields incl. provenance
        data = read_project_json(root)
        self.assertEqual(data["project"], "demo")
        self.assertEqual(data["code_repo"], str(Path("~/code/demo").expanduser()))
        self.assertEqual(data["registered_by"], "alice")
        self.assertEqual(data["config_version"], 1)
        self.assertRegex(data["registered_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_scaffold_code_repo_null_when_omitted(self) -> None:
        root = scaffold_project(self.home, "norepo")
        self.assertIsNone(read_project_json(root)["code_repo"])

    def test_scaffold_refuses_non_empty_root(self) -> None:
        root = self.home / "occupied"
        root.mkdir(parents=True)
        (root / "stuff.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(FileExistsError) as cm:
            scaffold_project(self.home, "occupied")
        self.assertIn("PROJECT_EXISTS", str(cm.exception))

    def test_scaffold_rejects_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            scaffold_project(self.home, "   ")

    # --- set_project_repo --------------------------------------------------------

    def test_set_repo_updates_code_repo_preserves_registered_at(self) -> None:
        root = scaffold_project(self.home, "demo")
        original = read_project_json(root)["registered_at"]
        set_project_repo(self.home, "demo", "~/code/elsewhere", registered_by="bob")
        updated = read_project_json(root)
        self.assertEqual(updated["code_repo"], str(Path("~/code/elsewhere").expanduser()))
        self.assertEqual(updated["registered_at"], original)  # provenance preserved
        self.assertEqual(updated["registered_by"], "bob")

    def test_set_repo_on_missing_project_fails_closed(self) -> None:
        with self.assertRaises(FileNotFoundError) as cm:
            set_project_repo(self.home, "ghost", "~/code/ghost")
        self.assertIn("PROJECT_NOT_ESTABLISHED", str(cm.exception))

    # --- home-aware resolve_workspace_root ---------------------------------------

    def test_resolve_workspace_root_global_config_resolves_home_project(self) -> None:
        # AIPOS-226: the home trigger is now the GLOBAL ~/.lybra/config.json (patched HOME).
        scaffold_project(self.home, "lybra")
        fake_home = self.root / "userhome"
        (fake_home / ".lybra").mkdir(parents=True)
        (fake_home / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home), "active_project": "lybra"}),
            encoding="utf-8",
        )
        self.assertEqual(
            resolve_workspace_root(self.root, env={"HOME": str(fake_home)}),
            (self.home / "lybra").resolve(),
        )

    def test_resolve_workspace_root_lybra_home_root_env_resolves(self) -> None:
        scaffold_project(self.home, "solo")
        empty_home = self.root / "emptyhome"
        self.assertEqual(
            resolve_workspace_root(
                self.root, env={"HOME": str(empty_home), HOME_ROOT_ENV: str(self.home)}
            ),
            (self.home / "solo").resolve(),
        )

    # --- FIX C②: find_repo_root(start) honors the home model on the explicit-start path ----

    def test_find_repo_root_explicit_start_honors_global_home_model(self) -> None:
        # AIPOS-226 FIX C②: an explicit start that is NOT itself a workspace must resolve via
        # the home model (global ~/.lybra/config.json), NOT be dropped (the prior env={} bug
        # re-resolved via legacy upward search and misread the global config).
        scaffold_project(self.home, "lybra")
        fake_home = self.root / "userhome-c2"
        (fake_home / ".lybra").mkdir(parents=True)
        (fake_home / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home), "active_project": "lybra"}),
            encoding="utf-8",
        )
        # `start` is a non-workspace subdir; resolution must land on the home project root.
        start = self.root / "some" / "explicit" / "elsewhere"
        start.mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {"HOME": str(fake_home)}, clear=True):
            self.assertEqual(find_repo_root(start), (self.home / "lybra").resolve())

    def test_find_repo_root_explicit_start_ignores_aipos_workspace_root(self) -> None:
        # AIPOS-226 FIX C②: AIPOS_WORKSPACE_ROOT is STILL ignored when an explicit start is
        # given (legacy explicit-start contract preserved). The home model decides instead.
        scaffold_project(self.home, "lybra")
        fake_home = self.root / "userhome-c2b"
        (fake_home / ".lybra").mkdir(parents=True)
        (fake_home / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home), "active_project": "lybra"}),
            encoding="utf-8",
        )
        decoy = self.root / "decoy-ws"
        for state in ("pending", "claimed", "completed", "blocked"):
            (decoy / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (decoy / "project.json").write_text(json.dumps({"project": "decoy"}), encoding="utf-8")
        start = self.root / "another" / "elsewhere"
        start.mkdir(parents=True, exist_ok=True)
        with patch.dict(
            os.environ,
            {"HOME": str(fake_home), "AIPOS_WORKSPACE_ROOT": str(decoy)},
            clear=True,
        ):
            self.assertEqual(find_repo_root(start), (self.home / "lybra").resolve())

    # --- FIX D: home_root-bearing config encountered by upward search routes to home model --

    def test_found_home_root_config_routes_to_home_never_misread_as_workspace_root(self) -> None:
        # AIPOS-226 FIX D: a config carrying `home_root` (v2 runtime schema) found by the upward
        # search must route to the home model and resolve to the home PROJECT root — it must
        # NEVER be misread as a v1 workspace_root config (which would resolve to the config's
        # own directory). No env/global signal: the FOUND config alone signals home.
        scaffold_project(self.home, "lybra")
        wsdir = self.root / "wsdir"
        (wsdir / ".lybra").mkdir(parents=True)
        (wsdir / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home), "active_project": "lybra"}),
            encoding="utf-8",
        )
        empty_home = self.root / "empty-home-D"
        # env has only HOME (empty, no global config) so the ONLY home signal is the found config.
        self.assertEqual(
            resolve_workspace_root(wsdir, env={"HOME": str(empty_home)}),
            (self.home / "lybra").resolve(),
        )

    def test_found_home_root_config_missing_project_fails_loud_not_wrong_root(self) -> None:
        # AIPOS-226 FIX D: when the found home_root config points at a project that is NOT
        # established, resolution fails LOUDLY (PROJECT_NOT_ESTABLISHED) — it must NOT silently
        # fall back to the config dir as a v1 workspace_root.
        wsdir = self.root / "wsdir2"
        (wsdir / ".lybra").mkdir(parents=True)
        (wsdir / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home), "active_project": "ghost"}),
            encoding="utf-8",
        )
        empty_home = self.root / "empty-home-D2"
        with self.assertRaises(FileNotFoundError) as cm:
            resolve_workspace_root(wsdir, env={"HOME": str(empty_home)})
        self.assertIn("PROJECT_NOT_ESTABLISHED", str(cm.exception))

    # --- v1 byte-identical (regression-locked) -----------------------------------

    def test_resolve_workspace_root_v1_config_is_byte_identical_legacy(self) -> None:
        ws = self.root / "v1ws"
        for state in ("pending", "claimed", "completed", "blocked"):
            (ws / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        write_workspace_config(ws)  # writes config_version 1, workspace_root "."
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_workspace_root(ws, env={}), ws.resolve())

    def test_resolve_workspace_root_legacy_env_is_byte_identical_legacy(self) -> None:
        ws = self.root / "legacyws"
        for state in ("pending", "claimed", "completed", "blocked"):
            (ws / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                resolve_workspace_root(self.root, env={"AIPOS_WORKSPACE_ROOT": str(ws)}),
                ws.resolve(),
            )

    def test_resolve_workspace_root_explicit_is_byte_identical_legacy(self) -> None:
        ws = self.root / "explicitws"
        for state in ("pending", "claimed", "completed", "blocked"):
            (ws / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                resolve_workspace_root(self.root, explicit_root=str(ws), env={}),
                ws.resolve(),
            )

    # --- round-trip: scaffold then draft create -> publish under <home>/<project> --

    def test_draft_round_trip_under_scaffolded_project(self) -> None:
        root = scaffold_project(self.home, "rt")
        metadata = {
            "task_id": "AIPOS-RT-1",
            "title": "round trip",
            "project": "rt",
            "assigned_to": "executor",
            "context_bundle": "none",
            "task_mode": "single",
            "task_class": "simple",
            "model_tier": "standard",
            "priority": "P2",
            "created_by": "owner",
            "output_target": "report",
            "artifact_policy": "ephemeral",
        }
        created = create_draft(root, metadata, "# body\n")
        self.assertNotEqual(created.get("verdict"), "BLOCK", created.get("blocking_reasons"))
        draft_rel = created["target_path"]
        # draft landed under <home>/<project>/5_tasks/drafts/
        self.assertTrue((root / draft_rel).is_file(), f"draft not written: {draft_rel}")
        published = publish_draft(root, draft_rel, dry_run=True)
        self.assertNotEqual(published.get("verdict"), "BLOCK", published.get("blocking_reasons"))


if __name__ == "__main__":
    unittest.main()
