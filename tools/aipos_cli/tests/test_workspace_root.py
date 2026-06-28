from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.task_loader import find_repo_root
from tools.aipos_cli.workspace_config import (
    ACTIVE_PROJECT_ENV,
    HOME_ROOT_ENV,
    LEGACY_WORKSPACE_ROOT_ENV,
    active_project_from_config,
    default_workspace_config,
    governance_paths,
    home_root_from_config,
    resolve_active_project,
    resolve_home_root,
    resolve_project_root,
    resolve_workspace_context,
    resolve_workspace_root,
    set_active_project,
    write_workspace_config,
)


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
        # AIPOS-226 FIX C②: AIPOS_WORKSPACE_ROOT is STILL ignored on the explicit-start path.
        # We patch HOME to an empty temp dir so the real ~/.lybra/config.json home model does
        # NOT leak in (no global config => upward legacy search applies), and clear the
        # home-model env signals. The explicit start (a non-workspace nested subdir) then
        # resolves via the legacy upward 5_tasks/queue marker search to the workspace root.
        empty_home = self.root / "empty-home-A"
        empty_home.mkdir(parents=True, exist_ok=True)
        with patch.dict(
            os.environ,
            {"AIPOS_WORKSPACE_ROOT": str(self.product_root), "HOME": str(empty_home)},
            clear=True,
        ):
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


class ResolutionCoreTests(unittest.TestCase):
    """AIPOS-224 Slice 0 — home-root + active-project resolver (additive, unwired)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        # one established project under the home (AIPOS-226 marker = queue AND project.json)
        self.project = "lybra"
        self._add_project(self.project)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _add_project(self, name: str) -> None:
        for queue_state in ("pending", "claimed", "completed", "blocked"):
            (self.home / name / "5_tasks" / "queue" / queue_state).mkdir(parents=True, exist_ok=True)
        (self.home / name / "project.json").write_text(
            json.dumps({"project": name, "config_version": 1}), encoding="utf-8"
        )

    # --- home root precedence ---

    def test_resolve_home_root_explicit_flag_wins(self) -> None:
        self.assertEqual(
            resolve_home_root(explicit_root=str(self.home), env={HOME_ROOT_ENV: "/nope"}),
            self.home.resolve(),
        )

    def test_resolve_home_root_lybra_home_root_env(self) -> None:
        self.assertEqual(
            resolve_home_root(env={HOME_ROOT_ENV: str(self.home)}),
            self.home.resolve(),
        )

    def test_resolve_home_root_from_global_config(self) -> None:
        # AIPOS-226: home_root now comes from the GLOBAL ~/.lybra/config.json (patched HOME).
        fake_home = self.root / "userhome"
        (fake_home / ".lybra").mkdir(parents=True, exist_ok=True)
        (fake_home / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home)}), encoding="utf-8"
        )
        self.assertEqual(
            resolve_home_root(env={"HOME": str(fake_home)}),
            self.home.resolve(),
        )

    def test_resolve_home_root_default_need_not_exist(self) -> None:
        # AIPOS-226: the default ~/.lybra/projects is returned even if it does NOT exist
        # (callers like `project new` create project subtrees under it). No fail-closed.
        fake_home = self.root / "userhome-empty"
        self.assertEqual(
            resolve_home_root(env={"HOME": str(fake_home)}),
            (fake_home / ".lybra" / "projects"),
        )

    def test_resolve_home_root_env_beats_global_config(self) -> None:
        fake_home = self.root / "userhome2"
        (fake_home / ".lybra").mkdir(parents=True, exist_ok=True)
        (fake_home / ".lybra" / "config.json").write_text(
            json.dumps({"home_root": str(self.root / "configured")}), encoding="utf-8"
        )
        self.assertEqual(
            resolve_home_root(env={"HOME": str(fake_home), HOME_ROOT_ENV: str(self.home)}),
            self.home.resolve(),
        )

    # --- active project precedence ---

    def test_resolve_active_project_explicit_wins(self) -> None:
        self.assertEqual(
            resolve_active_project(self.home, explicit="alpha", env={ACTIVE_PROJECT_ENV: "beta"}),
            "alpha",
        )

    def test_resolve_active_project_env(self) -> None:
        self.assertEqual(
            resolve_active_project(self.home, env={ACTIVE_PROJECT_ENV: "alpha"}),
            "alpha",
        )

    def test_resolve_active_project_from_global_config(self) -> None:
        self.assertEqual(
            resolve_active_project(self.home, env={}, global_config={"active_project": "delta"}),
            "delta",
        )

    def test_resolve_active_project_from_in_workspace_config_compat(self) -> None:
        # AIPOS-225 board_adapter compat: when an in-workspace `config` dict is passed it is
        # honored as the active_project source (Slice-1 fallback stays byte-identical).
        self.assertEqual(
            resolve_active_project(self.home, env={}, config={"active_project": "epsilon"}),
            "epsilon",
        )

    def test_resolve_active_project_single_fallback(self) -> None:
        self.assertEqual(resolve_active_project(self.home, env={}, global_config={}), self.project)

    def test_resolve_active_project_ambiguous_fail_closed(self) -> None:
        self._add_project("second")
        with self.assertRaises(ValueError) as cm:
            resolve_active_project(self.home, env={}, global_config={})
        self.assertIn("PROJECT_AMBIGUOUS", str(cm.exception))

    def test_project_candidate_requires_project_json(self) -> None:
        # A dir with a queue but NO project.json is NOT a candidate (marker = both).
        (self.home / "noproj" / "5_tasks" / "queue" / "pending").mkdir(parents=True, exist_ok=True)
        # still resolves to the single real project, ignoring the markerless dir
        self.assertEqual(resolve_active_project(self.home, env={}, global_config={}), self.project)

    # --- project root ---

    def test_resolve_project_root_happy(self) -> None:
        self.assertEqual(
            resolve_project_root(self.home, self.project),
            (self.home / self.project).resolve(),
        )

    def test_resolve_project_root_not_established_fail_closed(self) -> None:
        with self.assertRaises(FileNotFoundError) as cm:
            resolve_project_root(self.home, "ghost")
        msg = str(cm.exception)
        self.assertIn("PROJECT_NOT_ESTABLISHED", msg)
        self.assertIn("lybra project new ghost", msg)

    def test_resolve_project_root_requires_project_json(self) -> None:
        # queue present but project.json missing -> not established (marker = both)
        (self.home / "halfdone" / "5_tasks" / "queue" / "pending").mkdir(parents=True, exist_ok=True)
        with self.assertRaises(FileNotFoundError) as cm:
            resolve_project_root(self.home, "halfdone")
        self.assertIn("PROJECT_NOT_ESTABLISHED", str(cm.exception))

    # --- governance paths (rulings 1=B, 7) ---

    def test_governance_paths_single_file_decision_log_and_artifacts(self) -> None:
        project_root = self.home / self.project
        paths = governance_paths(project_root)
        self.assertEqual(paths["decision_log"], project_root / "governance" / "decision_log.md")
        self.assertEqual(paths["project_status"], project_root / "governance" / "project_status.md")
        self.assertEqual(paths["roadmap"], project_root / "governance" / "roadmap.md")
        self.assertEqual(paths["stage_archive"], project_root / "stage_archive")
        self.assertEqual(paths["workspace_artifacts"], project_root / "workspace_artifacts")
        # ruling 1=B: decision_log is a single .md file, not a directory
        self.assertTrue(str(paths["decision_log"]).endswith("decision_log.md"))

    # --- v2 read capability + M1 (default init still writes v1) ---

    def test_home_root_from_config_absent_returns_none(self) -> None:
        self.assertIsNone(home_root_from_config({"config_version": 1}))
        self.assertIsNone(home_root_from_config({"home_root": "   "}))

    def test_active_project_from_config_absent_returns_none(self) -> None:
        self.assertIsNone(active_project_from_config({"config_version": 1}))
        self.assertIsNone(active_project_from_config({"active_project": ""}))

    def test_v2_reader_honors_hand_written_v2_config(self) -> None:
        self.assertEqual(home_root_from_config({"home_root": str(self.home)}), self.home)
        self.assertEqual(active_project_from_config({"active_project": "lybra"}), "lybra")

    def test_default_workspace_config_still_v1(self) -> None:
        # M1: Slice 0 adds v2 READ capability only; default init keeps writing v1.
        cfg = default_workspace_config(self.home)
        self.assertEqual(cfg["config_version"], 1)
        self.assertNotIn("home_root", cfg)
        self.assertNotIn("active_project", cfg)
        self.assertNotIn("projects", cfg)  # M2: no home-config projects{} table


class WorkspaceContextTests(unittest.TestCase):
    """AIPOS-227 — resolve_workspace_context: the single precedence ladder yields
    (project_root, home_root|None); resolve_workspace_root delegates to it byte-identically."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        for qs in ("pending", "claimed", "completed", "blocked"):
            (self.home / "lybra" / "5_tasks" / "queue" / qs).mkdir(parents=True, exist_ok=True)
        (self.home / "lybra" / "project.json").write_text(
            json.dumps({"project": "lybra", "config_version": 1}), encoding="utf-8"
        )
        self.ws = self.root / "ws"
        for qs in ("pending", "claimed", "completed", "blocked"):
            (self.ws / "5_tasks" / "queue" / qs).mkdir(parents=True, exist_ok=True)
        self.empty_home = self.root / "empty"
        self.empty_home.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_home_model_env_returns_project_root_and_home(self) -> None:
        root, home = resolve_workspace_context(
            self.ws, env={HOME_ROOT_ENV: str(self.home), "HOME": str(self.empty_home)}
        )
        self.assertEqual(root, (self.home / "lybra").resolve())
        self.assertEqual(home, self.home.resolve())

    def test_legacy_marker_returns_none_home(self) -> None:
        root, home = resolve_workspace_context(self.ws, env={"HOME": str(self.empty_home)})
        self.assertEqual(root, self.ws.resolve())
        self.assertIsNone(home)  # legacy bare workspace -> not the home model

    def test_explicit_and_legacy_env_return_none_home(self) -> None:
        # R-1: home_root is None IFF the home model is NOT the resolution path.
        _, h_explicit = resolve_workspace_context(explicit_root=str(self.ws))
        self.assertIsNone(h_explicit)
        _, h_legacy_env = resolve_workspace_context(self.ws, env={LEGACY_WORKSPACE_ROOT_ENV: str(self.ws)})
        self.assertIsNone(h_legacy_env)

    def test_resolve_workspace_root_delegates_byte_identical(self) -> None:
        for env in (
            {HOME_ROOT_ENV: str(self.home), "HOME": str(self.empty_home)},
            {"HOME": str(self.empty_home)},
        ):
            self.assertEqual(
                resolve_workspace_root(self.ws, env=env),
                resolve_workspace_context(self.ws, env=env)[0],
            )


class ActiveProjectSequentialFallbackTests(unittest.TestCase):
    """AIPOS-230 §1a — resolve_active_project sequential fallback reaches the GLOBAL active_project
    even when an empty in-workspace config is passed; set_active_project round-trips through it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.fake_home = self.root / "userhome"
        for name in ("alpha", "beta"):  # ★ TWO projects -> single-project fallback is ambiguous
            for qs in ("pending", "claimed", "completed", "blocked"):
                (self.home / name / "5_tasks" / "queue" / qs).mkdir(parents=True, exist_ok=True)
            (self.home / name / "project.json").write_text(
                json.dumps({"project": name, "config_version": 1}), encoding="utf-8"
            )
        (self.fake_home / ".lybra").mkdir(parents=True, exist_ok=True)
        (self.fake_home / ".lybra" / "config.json").write_text(
            json.dumps({"config_version": 2, "home_root": str(self.home)}), encoding="utf-8"
        )
        self.env = {"HOME": str(self.fake_home)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_multiproject_no_active_is_ambiguous(self) -> None:
        with self.assertRaises(ValueError) as cm:
            resolve_active_project(self.home, env=self.env, config={})
        self.assertIn("PROJECT_AMBIGUOUS", str(cm.exception))

    def test_set_active_project_roundtrip_resolves_via_global(self) -> None:
        # ★ crux (catches the v1 false-pass): empty in-workspace config MUST fall through to the
        # global active_project. ≥2 projects so a single-project fallback can't mask it.
        path = set_active_project("beta", env=self.env)
        self.assertTrue(path.exists())
        self.assertEqual(resolve_active_project(self.home, env=self.env, config={}), "beta")

    def test_set_active_project_preserves_home_root(self) -> None:
        set_active_project("beta", env=self.env)
        cfg = json.loads((self.fake_home / ".lybra" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["active_project"], "beta")
        self.assertEqual(cfg["home_root"], str(self.home))  # preserved, not clobbered

    def test_inworkspace_active_wins_over_global_byte_compat(self) -> None:
        # AIPOS-225 Slice-1 byte-compat: an in-workspace config with active_project still wins.
        set_active_project("beta", env=self.env)
        self.assertEqual(
            resolve_active_project(self.home, env=self.env, config={"active_project": "alpha"}),
            "alpha",
        )

    def test_env_wins_over_global(self) -> None:
        set_active_project("beta", env=self.env)
        env = dict(self.env, LYBRA_ACTIVE_PROJECT="alpha")
        self.assertEqual(resolve_active_project(self.home, env=env, config={}), "alpha")

    def test_all_empty_fail_closed(self) -> None:
        # global has no active_project + 2 projects -> still PROJECT_AMBIGUOUS (no silent default).
        with self.assertRaises(ValueError):
            resolve_active_project(self.home, env=self.env, config={})


if __name__ == "__main__":
    unittest.main()
