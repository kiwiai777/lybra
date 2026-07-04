"""AIPOS-240 (F-o3-19) — symlink-safe repo-relative rendering.

Cross-platform mechanism guard for the two-sided `.resolve()` fix. Uses a REAL product function at a
FIX site (`queue_mutation._base_result`, :293) driven through a symlinked repo_root — a standing
symlink stands in for macOS `/var → /private/var`, so this pins the fix on Linux CI without a Mac.

Two cases (DRAFT §5):
  (a) FIX      — a repo_root reached via a symlink prefix + a resolved LHS renders the correct
                 repo-relative string, byte-identical to the no-symlink render (no ValueError).
  (b) TIGHTEN  — an INTERNAL symlink subdir pointing OUT of repo_root makes the render raise loudly
                 (write refused), proving the containment tightening (R ruling ⑤) is real.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.queue_mutation import _base_result

REL = "5_tasks/queue/pending/T-1.md"


def _render_source_path(repo_root: Path, source_path: Path) -> str:
    """Drive the real FIX-site render (_base_result :293 -> source_path rel)."""
    source_task = {"task_id": "T-1", "queue_state": "pending"}
    result = _base_result(
        source_path, repo_root, source_task,
        action="claim", dry_run=True, actor="alice.local", to_state="claimed",
    )
    return result["source_path"]


class SymlinkSafeRenderTests(unittest.TestCase):
    def test_a_symlinked_repo_root_renders_correct_relative_and_matches_plain(self) -> None:
        # --- plain (no symlink) baseline ---
        with tempfile.TemporaryDirectory() as plain:
            plain_root = Path(plain)
            (plain_root / "5_tasks/queue/pending").mkdir(parents=True)
            plain_src = plain_root / REL
            plain_src.write_text("x")
            plain_rendered = _render_source_path(plain_root, plain_src)

        # --- symlinked prefix (mimics macOS /var -> /private/var): repo_root passed UNRESOLVED,
        #     source_path RESOLVED (as the product does upstream) -> the pre-fix ValueError case ---
        with tempfile.TemporaryDirectory() as base:
            real = Path(base) / "real"
            (real / "5_tasks/queue/pending").mkdir(parents=True)
            (real / REL).write_text("x")
            sym = Path(base) / "sym"
            os.symlink(real, sym)               # sym -> real
            repo_root = sym                      # UNRESOLVED, as passed from the CLI
            source_path = (sym / REL).resolve()  # RESOLVED -> /.../real/... (diverged prefix)
            rendered = _render_source_path(repo_root, source_path)

        self.assertEqual(rendered, REL)               # correct repo-relative, no ValueError
        self.assertEqual(rendered, plain_rendered)    # byte-identical to the no-symlink render

    def test_b_internal_symlink_pointing_out_raises_loudly(self) -> None:
        # An internal subdir that is itself a symlink OUT of the repo. Lexically the path string is
        # still under repo_root/5_tasks/... (pre-fix: silently passes). Post-fix the LHS is resolved
        # first, so it lands OUTSIDE repo_root -> relative_to raises -> the write is refused.
        with tempfile.TemporaryDirectory() as base:
            repo_root = Path(base) / "repo"
            (repo_root / "5_tasks/queue").mkdir(parents=True)
            outside = Path(base) / "outside" / "pending"
            outside.mkdir(parents=True)
            # repo/5_tasks/queue/pending  ->  <base>/outside/pending  (escapes the truth zone)
            os.symlink(outside, repo_root / "5_tasks/queue/pending")
            escaping_src = (repo_root / REL).resolve()   # resolves to <base>/outside/pending/T-1.md
            escaping_src.write_text("x")

            with self.assertRaises(ValueError):
                _render_source_path(repo_root, escaping_src)


if __name__ == "__main__":
    unittest.main()
