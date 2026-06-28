from __future__ import annotations

import os
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

# AIPOS-233 (test hermeticity). `home_git.git_repo_ancestor` self-walks Path.parents to `/`
# (correct topology-C nested-repo safety — product behavior is BYTE-UNCHANGED here). A stray
# `.git` in an ancestor of the system temp dir (e.g. a polluted `/tmp/.git`, which is EXTERNAL
# environment pollution — grep-proven: Lybra never `git init`s outside these tests' own
# cwd=self.home calls) would make a /tmp-rooted test home walk up to it and fail. A clean /tmp
# is a genuine PREMISE that the tests cannot engineer away. This guard makes a polluted env
# announce itself with ONE clear message naming the stray .git, instead of masquerading as a
# home_git regression (the prior cryptic 3-ERROR/1-FAIL pattern).
HERMETIC_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def assert_no_ancestor_git(temp_root: Path) -> None:
    stray = git_repo_ancestor(temp_root)
    if stray is not None:
        raise AssertionError(
            f"non-hermetic env: stray ancestor .git at {stray}; remove it "
            f"(the shared /tmp is polluted) — this is NOT a home_git regression"
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
        # AIPOS-233 git-config hermeticity (defense-in-depth): isolate user/system gitconfig so
        # the product's inherited-env git subprocesses + the test helper are deterministic. HOME
        # points at the temp dir; restored in tearDown. (commit identity already uses -c, so this
        # is hardening, not a behavior change.)
        self._saved_env = {k: os.environ.get(k) for k in (*HERMETIC_GIT_ENV, "HOME")}
        os.environ.update(HERMETIC_GIT_ENV)
        os.environ["HOME"] = str(self.home)
        # AIPOS-233 diagnostic: a clean /tmp is a PREMISE; if it is polluted, fail with ONE clear
        # message naming the stray .git rather than the cryptic ALREADY_IN_GIT_REPO pattern.
        assert_no_ancestor_git(self.home)
        # something to commit (truth)
        (self.home / "5_tasks" / "queue" / "pending").mkdir(parents=True)
        (self.home / "5_tasks" / "queue" / "pending" / ".keep").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
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


class HermeticGuardTests(unittest.TestCase):
    """AIPOS-233: the diagnostic turns a polluted env into ONE clear, named message — not the
    cryptic 3-ERROR/1-FAIL home_git pattern. Deterministic + self-hermetic: the stray `.git` is
    created inside this test's OWN tempdir (NOT `/tmp/.git`), so it never pollutes other tests.
    """

    def test_guard_passes_on_clean_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            child = Path(base) / "home"
            child.mkdir()
            # No ancestor .git within this controlled subtree -> guard is silent.
            try:
                assert_no_ancestor_git(child)
            except AssertionError:  # pragma: no cover - only if the shared /tmp is itself polluted
                self.skipTest("shared /tmp is polluted by an external .git; clean it to run this")

    def test_guard_names_stray_ancestor_git(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            base_path = Path(base).resolve()
            (base_path / ".git").mkdir()  # controlled stray, nearest ancestor
            child = base_path / "home"
            child.mkdir()
            with self.assertRaises(AssertionError) as cm:
                assert_no_ancestor_git(child)
            msg = str(cm.exception)
            # names the stray .git location (positive truth, not a generic failure)
            self.assertIn(str(base_path), msg)
            # states it is an environment problem, not a product regression
            self.assertIn("NOT a home_git regression", msg)


if __name__ == "__main__":
    unittest.main()
