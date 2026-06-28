from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.home_git import (
    execute_home_git_init,
    git_repo_ancestor,
    is_git_repo,
    plan_home_git_init,
)


class HomeGitPlanTests(unittest.TestCase):
    """AIPOS-226 Slice 2 (Phase 2a): one-shot, transparent, local-only home git setup."""

    def test_plan_gitignore_ignores_local_tracks_truth(self) -> None:
        plan = plan_home_git_init("/some/home", "owner")
        gi = plan["gitignore"]
        # ignores local-only / ephemeral artifacts
        self.assertIn(".lybra/local/", gi)
        self.assertIn("*.tgz", gi)
        self.assertIn("__pycache__/", gi)
        # does NOT ignore truth
        for tracked in ("5_tasks", "governance", "stage_archive", "project.json"):
            for line in gi.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                self.assertNotEqual(stripped, tracked, f"{tracked} must not be ignored")
                self.assertNotEqual(stripped, tracked + "/", f"{tracked}/ must not be ignored")

    def test_plan_commands_and_push_hint(self) -> None:
        plan = plan_home_git_init("/some/home", "alice")
        self.assertEqual(plan["commands"][0], ["git", "init"])
        self.assertEqual(plan["commands"][1], ["git", "add", "."])
        commit = plan["commands"][2]
        self.assertIn("user.name=alice", commit)
        self.assertIn("user.email=alice@lybra.local", commit)
        self.assertIn("commit", commit)
        # push hint is informational only (never executed by git-init)
        self.assertEqual(len(plan["push_hint"]), 2)
        self.assertTrue(plan["push_hint"][0].startswith("git remote add origin"))
        self.assertTrue(plan["push_hint"][1].startswith("git push"))

    def test_is_git_repo_false_on_fresh_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(is_git_repo(d))


@unittest.skipIf(shutil.which("git") is None, "system git not available")
class HomeGitExecuteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        # something to commit (truth)
        (self.home / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (self.home / "5_tasks" / "queue" / "pending" / ".keep").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(self.home), check=True, capture_output=True, text=True
        ).stdout

    def test_execute_creates_repo_one_commit_no_remote(self) -> None:
        result = execute_home_git_init(self.home, actor="owner")
        self.assertTrue((self.home / ".git").exists())
        self.assertTrue((self.home / ".gitignore").is_file())
        # exactly one commit
        log = self._git("log", "--oneline").strip().splitlines()
        self.assertEqual(len(log), 1, log)
        # NO remote configured
        self.assertEqual(self._git("remote").strip(), "")
        # push hint surfaced
        self.assertTrue(result["push_hint"])

    def test_execute_twice_raises_file_exists(self) -> None:
        execute_home_git_init(self.home, actor="owner")
        with self.assertRaises(FileExistsError) as cm:
            execute_home_git_init(self.home, actor="owner")
        self.assertIn("HOME_ALREADY_GIT", str(cm.exception))

    def test_execute_missing_home_raises(self) -> None:
        missing = self.home / "nope"
        with self.assertRaises(FileNotFoundError):
            execute_home_git_init(missing, actor="owner")

    def test_git_repo_ancestor_detects_existing_repo(self) -> None:
        # self.home is a plain dir (no repo yet)
        self.assertIsNone(git_repo_ancestor(self.home))
        # init a repo, then a nested child should detect the ancestor repo root
        subprocess.run(["git", "init"], cwd=str(self.home), check=True, capture_output=True, text=True)
        nested = self.home / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(git_repo_ancestor(nested), self.home.resolve())

    def test_execute_refuses_inside_existing_repo(self) -> None:
        # AIPOS-226 §3 (topology C safety): a target INSIDE an existing repo is refused.
        subprocess.run(["git", "init"], cwd=str(self.home), check=True, capture_output=True, text=True)
        project = self.home / "lybra"
        (project / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        with self.assertRaises(FileExistsError) as cm:
            execute_home_git_init(project, actor="owner")
        self.assertIn("ALREADY_IN_GIT_REPO", str(cm.exception))

    def test_execute_project_scope_target(self) -> None:
        # --project scope (topology B): init a per-project repo at <home>/<project>, when the
        # home itself is NOT a repo.
        project = self.home / "proj"
        (project / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (project / "project.json").write_text("{}", encoding="utf-8")
        result = execute_home_git_init(project, actor="owner")
        self.assertTrue((project / ".git").exists())
        self.assertFalse((self.home / ".git").exists())  # home not a repo
        self.assertTrue(result["push_hint"])


if __name__ == "__main__":
    unittest.main()
