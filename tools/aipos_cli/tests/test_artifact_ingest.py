from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.artifact_ingest import (
    APPROVED_SCRATCH_ROOT_ENV,
    perform_scratch_ingestion,
    plan_scratch_ingestion,
)


class ArtifactIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.scratch_tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.repo_tmp.name).resolve()
        self.approved_root = Path(self.scratch_tmp.name).resolve()
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".lybra" / "local").mkdir(parents=True, exist_ok=True)
        self.scratch_dir = self.approved_root / "run-1"
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.env = {APPROVED_SCRATCH_ROOT_ENV: str(self.approved_root)}

    def tearDown(self) -> None:
        self.repo_tmp.cleanup()
        self.scratch_tmp.cleanup()

    def _write_scratch(self, name: str, content: bytes = b"artifact-bytes") -> Path:
        path = self.scratch_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _plan(self, refs, **overrides):
        kwargs = dict(
            repo_root=self.repo_root,
            task_id="AIPOS-196A",
            return_id="return_AIPOS-196A_20260610_000000_agent-01",
            scratch_dir=str(self.scratch_dir),
            scratch_artifact_refs=refs,
            env=self.env,
        )
        kwargs.update(overrides)
        return plan_scratch_ingestion(**kwargs)

    def test_no_request_is_noop(self) -> None:
        plan = self._plan(None, scratch_dir=None)
        self.assertEqual(plan["blocking_reasons"], [])
        self.assertEqual(plan["ingestions"], [])
        self.assertEqual(plan["workspace_refs"], [])

    def test_happy_path_plans_workspace_dest_and_digest(self) -> None:
        self._write_scratch("out.txt")
        plan = self._plan(["out.txt"])
        self.assertEqual(plan["blocking_reasons"], [])
        self.assertEqual(len(plan["ingestions"]), 1)
        rel = plan["workspace_refs"][0]
        self.assertTrue(
            rel.startswith("workspace_artifacts/AIPOS-196A/return_AIPOS-196A_20260610_000000_agent-01/"),
            rel,
        )
        self.assertTrue(plan["digest"])

    def test_missing_approved_root_blocks(self) -> None:
        self._write_scratch("out.txt")
        plan = self._plan(["out.txt"], env={})
        self.assertTrue(plan["blocking_reasons"])
        self.assertIn("approved scratch root", plan["blocking_reasons"][0])
        self.assertEqual(plan["ingestions"], [])

    def test_scratch_dir_outside_approved_root_blocks(self) -> None:
        outside = Path(tempfile.mkdtemp())
        try:
            (outside / "out.txt").write_bytes(b"x")
            plan = self._plan(["out.txt"], scratch_dir=str(outside))
            self.assertTrue(any("approved scratch root" in r for r in plan["blocking_reasons"]))
        finally:
            __import__("shutil").rmtree(outside)

    def test_scratch_dir_inside_truth_blocks(self) -> None:
        truth_scratch = self.repo_root / "5_tasks" / "queue" / "pending"
        plan = self._plan(["x"], scratch_dir=str(truth_scratch), env={APPROVED_SCRATCH_ROOT_ENV: str(self.repo_root)})
        self.assertTrue(any("truth" in r for r in plan["blocking_reasons"]))

    def test_relative_parent_escape_blocks(self) -> None:
        plan = self._plan(["../../5_tasks/queue/pending/orphan.md"])
        self.assertTrue(any("escapes scratch_dir" in r for r in plan["blocking_reasons"]))
        self.assertEqual(plan["ingestions"], [])

    def test_symlink_escape_to_truth_blocks(self) -> None:
        target = self.repo_root / "5_tasks" / "queue" / "pending" / "secret.md"
        target.write_text("truth", encoding="utf-8")
        link = self.scratch_dir / "evil.md"
        os.symlink(target, link)
        plan = self._plan(["evil.md"])
        self.assertTrue(any("escapes scratch_dir" in r for r in plan["blocking_reasons"]))

    def test_symlink_escape_to_host_blocks(self) -> None:
        link = self.scratch_dir / "passwd"
        os.symlink("/etc/passwd", link)
        plan = self._plan(["passwd"])
        self.assertTrue(any("escapes scratch_dir" in r for r in plan["blocking_reasons"]))

    def test_existing_dest_blocks_overwrite(self) -> None:
        self._write_scratch("out.txt")
        plan = self._plan(["out.txt"])
        dest = self.repo_root / plan["workspace_refs"][0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("already here", encoding="utf-8")
        plan2 = self._plan(["out.txt"])
        self.assertTrue(any("already exists" in r for r in plan2["blocking_reasons"]))

    def test_perform_copies_and_sets_content(self) -> None:
        self._write_scratch("out.txt", b"hello-world")
        plan = self._plan(["out.txt"])
        performed = perform_scratch_ingestion(
            self.repo_root,
            plan["ingestions"],
            scratch_root=str(self.scratch_dir),
            approved_root=self.approved_root,
        )
        self.assertEqual(len(performed), 1)
        dest = self.repo_root / plan["workspace_refs"][0]
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), b"hello-world")

    def test_perform_rejects_content_swap_toctou(self) -> None:
        scratch_file = self._write_scratch("out.txt", b"original")
        plan = self._plan(["out.txt"])
        # Swap content after planning/hashing.
        scratch_file.write_bytes(b"tampered-after-plan")
        with self.assertRaises(ValueError):
            perform_scratch_ingestion(
                self.repo_root,
                plan["ingestions"],
                scratch_root=str(self.scratch_dir),
                approved_root=self.approved_root,
            )
        dest = self.repo_root / plan["workspace_refs"][0]
        self.assertFalse(dest.exists())

    def test_perform_rejects_symlink_swap_toctou(self) -> None:
        scratch_file = self._write_scratch("out.txt", b"original")
        plan = self._plan(["out.txt"])
        # Replace the regular file with a symlink pointing outside scratch.
        scratch_file.unlink()
        os.symlink("/etc/passwd", scratch_file)
        with self.assertRaises(ValueError):
            perform_scratch_ingestion(
                self.repo_root,
                plan["ingestions"],
                scratch_root=str(self.scratch_dir),
                approved_root=self.approved_root,
            )


if __name__ == "__main__":
    unittest.main()
