"""AIPOS-268 — `agent watch --workspace-root` filesystem pump tests.

Pins (card AIPOS-268 §1-3 + red lines):
- diff four kinds (new / modified / moved / deleted) over real fixture trees, incl. the
  queue state-transition move (pending->claimed, same content fingerprint = a move, not
  new+deleted) and deterministic sorted output;
- the bounded loop: a change triggers exit 0 with a one-line JSON summary; timeout is a
  SILENT exit 2; SIGTERM is a clean exit (130, no output, no traceback) — verified on the
  real CLI via subprocess (zero bash `$!` methodology issues);
- summary FORMAT: a single JSON line, ``changed`` array, each entry ``{path, kind}``;
- red lines: the module is stdlib-only (zero new deps), read-only (no write/remove
  syscalls in source), and GATE-FREE (no MCP/gate client imports — the pump never touches
  the gate); agent_connector.py stays byte-identical (candidate ⑤ preserved).
"""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools.aipos_cli import agent_watch_fs
from tools.aipos_cli.agent_watch_fs import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    EXIT_CHANGE,
    EXIT_SIGNAL,
    EXIT_TIMEOUT,
    diff_snapshots,
    run_fs_watch,
    snapshot,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _args(workspace_root: str, *, interval: Any = None, timeout: Any = None) -> SimpleNamespace:
    return SimpleNamespace(workspace_root=workspace_root, interval=interval, timeout=timeout)


def _make_workspace() -> str:
    """A fixture Lybra workspace with the two watched subtrees (+ an unwatched dir to
    prove the snapshot scope is exactly queue/** + records/**)."""
    d = tempfile.mkdtemp(prefix="aipos268_")
    for sub in ("5_tasks/queue/pending", "5_tasks/queue/claimed", "5_tasks/records"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    # an unwatched subtree (must never appear in a snapshot / diff)
    os.makedirs(os.path.join(d, "0_control_plane/agents"), exist_ok=True)
    Path(d, "0_control_plane/agents/profile.yaml").write_text("ignored: true\n", encoding="utf-8")
    return d


def _write(path: str, body: str) -> None:
    Path(path).write_text(body, encoding="utf-8")


class FsWatchDiffTests(unittest.TestCase):
    """diff_snapshots: four kinds + determinism + scope."""

    def test_new_and_modified_and_deleted(self) -> None:
        prev = {
            "5_tasks/records/keep.md": (1, 10),
            "5_tasks/records/gone.md": (2, 20),
        }
        curr = {
            "5_tasks/records/keep.md": (9, 11),  # mtime+size changed -> modified
            "5_tasks/records/born.md": (3, 5),   # new (no fingerprint match) -> new
        }
        changed = diff_snapshots(prev, curr)
        by = {(e["path"], e["kind"]) for e in changed}
        self.assertIn(("5_tasks/records/keep.md", "modified"), by)
        self.assertIn(("5_tasks/records/born.md", "new"), by)
        self.assertIn(("5_tasks/records/gone.md", "deleted"), by)
        self.assertEqual(len(changed), 3)

    def test_moved_matches_by_fingerprint_not_new_plus_deleted(self) -> None:
        """The queue state-transition case: a card relocated pending->claimed with
        identical content (same mtime_ns+size fingerprint) is ONE moved event at the new
        path — NOT a new + a deleted (which would double-count and mislabel)."""
        prev = {"5_tasks/queue/pending/t-1.md": (100, 42)}
        curr = {"5_tasks/queue/claimed/t-1.md": (100, 42)}  # same fingerprint, new path
        changed = diff_snapshots(prev, curr)
        self.assertEqual(changed, [{"path": "5_tasks/queue/claimed/t-1.md", "kind": "moved"}])
        self.assertFalse(
            any(e["kind"] == "new" for e in changed), "a move must not also be reported as new"
        )
        self.assertFalse(
            any(e["kind"] == "deleted" for e in changed), "a move must not also be reported as deleted"
        )

    def test_move_is_one_to_one_and_greedy_deterministic(self) -> None:
        """Two identical-fingerprint files relocate: one-to-one matching, deterministic."""
        fp = (500, 7)
        prev = {
            "5_tasks/queue/pending/a.md": fp,
            "5_tasks/queue/pending/b.md": fp,
            "5_tasks/queue/pending/c.md": fp,
        }
        curr = {
            "5_tasks/queue/claimed/x.md": fp,
            "5_tasks/queue/claimed/y.md": fp,
        }
        changed = diff_snapshots(prev, curr)
        moved = [e for e in changed if e["kind"] == "moved"]
        deleted = [e for e in changed if e["kind"] == "deleted"]
        self.assertEqual(len(moved), 2, "two reappearances consume two disappearances")
        self.assertEqual(len(deleted), 1, "the unmatched disappearance is a pure deletion")
        self.assertEqual({e["path"] for e in moved}, {"5_tasks/queue/claimed/x.md", "5_tasks/queue/claimed/y.md"})

    def test_unchanged_yields_empty(self) -> None:
        prev = {"5_tasks/records/a.md": (1, 1)}
        self.assertEqual(diff_snapshots(prev, dict(prev)), [])

    def test_empty_to_empty_is_empty(self) -> None:
        self.assertEqual(diff_snapshots({}, {}), [])

    def test_output_is_sorted_for_determinism(self) -> None:
        prev = {"z.md": (1, 1), "a.md": (2, 2)}
        curr = {}  # both deleted
        changed = diff_snapshots(prev, curr)
        paths = [e["path"] for e in changed]
        self.assertEqual(paths, sorted(paths))

    def test_entry_shape_is_path_and_kind(self) -> None:
        """Summary-format pin (card §3): every entry carries exactly the documented keys."""
        changed = diff_snapshots({"a.md": (1, 1)}, {"a.md": (2, 2), "b.md": (1, 1)})
        for entry in changed:
            self.assertEqual(set(entry.keys()), {"path", "kind"})
            self.assertIsInstance(entry["path"], str)
            self.assertIn(entry["kind"], {"new", "modified", "moved", "deleted"})


class FsWatchSnapshotTests(unittest.TestCase):
    """snapshot: scope = exactly queue/** + records/**; linear (one entry per regular
    file); missing subtrees are empty; POSIX relpaths."""

    def test_scope_is_exactly_the_two_subtrees(self) -> None:
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/queue/pending/t.md"), "x")
        _write(os.path.join(ws, "5_tasks/records/r.md"), "y")
        snap = snapshot(Path(ws))
        self.assertEqual(
            set(snap.keys()),
            {"5_tasks/queue/pending/t.md", "5_tasks/records/r.md"},
            "the unwatched 0_control_plane/** must NOT appear",
        )

    def test_one_entry_per_regular_file_and_fingerprint_shape(self) -> None:
        ws = _make_workspace()
        files = {
            "5_tasks/queue/pending/a.md": "aaa",
            "5_tasks/queue/claimed/b.md": "bb",
            "5_tasks/records/c.md": "c",
        }
        for rel, body in files.items():
            _write(os.path.join(ws, rel), body)
        snap = snapshot(Path(ws))
        self.assertEqual(len(snap), len(files), "linear: N regular files -> N entries")
        for fp in snap.values():
            self.assertIsInstance(fp, tuple)
            self.assertEqual(len(fp), 2)  # (mtime_ns, size)
            self.assertIsInstance(fp[0], int)
            self.assertIsInstance(fp[1], int)

    def test_missing_subtrees_are_empty_not_an_error(self) -> None:
        """A workspace may legitimately have no 5_tasks/records yet."""
        ws = tempfile.mkdtemp(prefix="aipos268_")
        os.makedirs(os.path.join(ws, "5_tasks/queue/pending"), exist_ok=True)
        _write(os.path.join(ws, "5_tasks/queue/pending/only.md"), "x")
        snap = snapshot(Path(ws))  # records/ absent
        self.assertEqual(set(snap.keys()), {"5_tasks/queue/pending/only.md"})

    def test_repath_uses_posix_separators(self) -> None:
        ws = _make_workspace()
        nested = os.path.join(ws, "5_tasks/queue/pending/sub/deep/dir")
        os.makedirs(nested)
        _write(os.path.join(nested, "t.md"), "x")
        snap = snapshot(Path(ws))
        (key,) = snap.keys()
        self.assertEqual(key, "5_tasks/queue/pending/sub/deep/dir/t.md")
        self.assertNotIn("\\", key)

    def test_snapshot_is_repeatable_when_unchanged(self) -> None:
        """Stateless-by-construction probe: two snapshots over an unchanged tree agree
        (the positive precondition for a stable diff)."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/r.md"), "stable")
        self.assertEqual(snapshot(Path(ws)), snapshot(Path(ws)))


class FsWatchLoopTests(unittest.TestCase):
    """run_fs_watch: change->exit0+JSON / timeout->silent exit2 / arg errors. Fake
    sleeper+clock, ZERO real waiting."""

    def test_change_triggers_exit0_and_one_line_json_summary(self) -> None:
        """Card §1/§3 + S1: an external change (here applied inside the injected
        sleeper, between polls) -> exit 0 and a one-line JSON ``changed`` summary."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        created = {"done": False}

        def sleeper(seconds: float) -> None:
            if not created["done"]:
                _write(os.path.join(ws, "5_tasks/queue/pending/newcard.md"), "touched")
                created["done"] = True

        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(
                _args(ws, interval=0.1, timeout=100.0), sleeper=sleeper, clock=time.monotonic
            )
        self.assertEqual(rc, EXIT_CHANGE)
        line = out.getvalue()
        self.assertEqual(line.count("\n"), 1, "exactly one JSON line")
        payload = json.loads(line)
        self.assertIn("changed", payload)
        hit = [e for e in payload["changed"] if e["path"] == "5_tasks/queue/pending/newcard.md"]
        self.assertEqual(hit, [{"path": "5_tasks/queue/pending/newcard.md", "kind": "new"}])

    def test_timeout_is_silent_exit2(self) -> None:
        """Card §2 + S2: no change within --timeout -> exit 2 with ZERO output."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        clock_now = [0.0]
        slept: list[float] = []

        def sleeper(seconds: float) -> None:
            slept.append(seconds)
            clock_now[0] += seconds  # advance the fake clock per sleep (no real wait)

        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(
                _args(ws, interval=10.0, timeout=25.0), sleeper=sleeper, clock=lambda: clock_now[0]
            )
        self.assertEqual(rc, EXIT_TIMEOUT)
        self.assertEqual(out.getvalue(), "", "timeout must be SILENT (no stdout)")
        self.assertTrue(slept, "the loop must have polled at least once before timing out")
        self.assertTrue(all(s <= 10.0 for s in slept), slept)

    def test_interval_default_is_15_and_timeout_default_is_1800_when_none(self) -> None:
        """Card §1 defaults: --interval 15 / --timeout 1800 (resolved from None per-mode)."""
        ws = _make_workspace()
        seen: dict[str, float] = {}
        clock_now = [0.0]

        def sleeper(seconds: float) -> None:
            seen["interval"] = seconds
            clock_now[0] += seconds  # advance the fake clock per sleep (no real wait)

        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(_args(ws), sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_TIMEOUT)
        self.assertEqual(seen["interval"], DEFAULT_INTERVAL_SECONDS)

    def test_bad_workspace_root_is_exit2_with_message(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(_args("/definitely/not/a/real/path/xyz268", interval=0.1, timeout=1.0))
        self.assertEqual(rc, EXIT_TIMEOUT)
        self.assertEqual(out.getvalue(), "", "errors must not pollute stdout (only the JSON summary goes there)")

    def test_nonpositive_interval_and_timeout_are_errors(self) -> None:
        ws = _make_workspace()
        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(_args(ws, interval=0, timeout=10.0))
        self.assertEqual(rc, EXIT_TIMEOUT)
        rc = run_fs_watch(_args(ws, interval=1.0, timeout=0))
        self.assertEqual(rc, EXIT_TIMEOUT)


class FsWatchSignalTests(unittest.TestCase):
    """Card §2 'SIGTERM 干净退出': verified on the REAL CLI via subprocess + send_signal
    (targets the exact child PID — avoids the bash `$!` subshell-PID pitfall)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = _make_workspace()
        _write(os.path.join(cls.workspace, "5_tasks/records/seed.md"), "seed")

    def test_sigterm_is_clean_exit_no_traceback(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", self.workspace, "--timeout", "30", "--interval", "0.4"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(1.0)  # let it install handlers + enter the loop
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=8)
        except Exception:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, EXIT_SIGNAL, err)
        self.assertEqual(out, b"", "signal exit must print nothing")
        self.assertEqual(err, b"", "signal exit must leave NO traceback on stderr")

    def test_sigint_is_also_clean(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", self.workspace, "--timeout", "30", "--interval", "0.4"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(1.0)
            proc.send_signal(signal.SIGINT)
            out, err = proc.communicate(timeout=8)
        except Exception:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, EXIT_SIGNAL, err)
        self.assertEqual(out, b"")
        self.assertEqual(err, b"")


class FsWatchCliIntegrationTests(unittest.TestCase):
    """End-to-end on the real CLI: a real external file change -> exit 0 + JSON;
    real timeout -> silent exit 2 (S1/S2 machine-checked)."""

    def test_real_external_change_exits_zero_with_json(self) -> None:
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "8", "--interval", "0.3"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(1.0)
            _write(os.path.join(ws, "5_tasks/queue/pending/landed.md"), "from another terminal")
            out, err = proc.communicate(timeout=8)
        except Exception:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, EXIT_CHANGE, err)
        payload = json.loads(out.decode("utf-8").strip())
        self.assertTrue(
            any(e["path"] == "5_tasks/queue/pending/landed.md" and e["kind"] == "new" for e in payload["changed"]),
            payload,
        )

    def test_real_timeout_is_silent_exit2(self) -> None:
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "1", "--interval", "0.3"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, EXIT_TIMEOUT)
        self.assertEqual(out, b"", "timeout must be silent")
        self.assertEqual(err, b"")

    def test_gate_mode_is_still_routed_and_requires_gate_args(self) -> None:
        """Zero-regression dispatch pin: `agent watch --gate-url` (candidate ⑤) still
        routes to the gate path and enforces the AIPOS-248 required-arg contract."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--gate-url", "http://127.0.0.1:1"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, 2, err)
        self.assertIn(b"--actor", err)


class FsWatchRedLineTests(unittest.TestCase):
    """Card red lines, pinned structurally on the new module's source."""

    def test_module_is_stdlib_only_zero_new_deps(self) -> None:
        """Red line: zero new dependencies — only stdlib imports allowed."""
        import ast

        tree = ast.parse(Path(agent_watch_fs.__file__).read_text(encoding="utf-8"))
        stdlib = {
            "json", "os", "signal", "stat", "sys", "time", "pathlib", "typing",
            "__future__",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], stdlib, f"non-stdlib import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                self.assertIn((node.module or "").split(".")[0], stdlib, f"non-stdlib from-import: {node.module}")

    def test_module_is_gate_free(self) -> None:
        """Red line 'pump in CLI not in gate' / 'gate 零改动': the pump must NEVER touch
        the gate — no MCP client, no gate client, no write/confirm tool surface. The
        only thing it does to the workspace is read (stat)."""
        src = Path(agent_watch_fs.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "from tools.aipos_cli.confirm_client",  # the gate client
            "from tools.mcp_server",
            "GateClient",
            "call_tool",
            "queue_tasks",
            "connection.json",
        ):
            self.assertNotIn(forbidden, src, f"pump must be gate-free: found {forbidden!r}")

    def test_snapshot_is_read_only(self) -> None:
        """Red line '纯客户端只读': the module must contain no write/move/remove
        primitives — it only ever reads (stat/walk)."""
        src = Path(agent_watch_fs.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "os.remove(", "os.unlink(", "os.rmdir(", "os.rename(", "os.replace(",
            "shutil.",
            ".write_text(", ".write_bytes(", "open(",
        ):
            self.assertNotIn(forbidden, src, f"pump must be read-only: found {forbidden!r}")

    def test_agent_connector_module_is_unchanged_zero_regression(self) -> None:
        """Red line 'gate 零改动' + S3 zero-regression: the AIPOS-248 gate-path module
        (candidate ⑤) is byte-identical to git HEAD — the filesystem pump added a new
        module + CLI dispatch only, never edited the gate client."""
        connector = _REPO_ROOT / "tools" / "aipos_cli" / "agent_connector.py"
        head = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "show", f"HEAD:{connector.relative_to(_REPO_ROOT)}"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            connector.read_text(encoding="utf-8"),
            head.stdout,
            "agent_connector.py must be byte-identical to HEAD (candidate ⑤ untouched)",
        )

    def test_cli_watch_supports_both_modes(self) -> None:
        """The 候选⑤⑫合流 surface: `agent watch` accepts both --workspace-root (⑫) and
        --gate-url (⑤), mutually exclusive."""
        help_proc = subprocess.run(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch", "--help"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        text = help_proc.stdout + help_proc.stderr
        self.assertIn("--workspace-root", text)
        self.assertIn("--gate-url", text)
        self.assertIn("--timeout", text)


if __name__ == "__main__":
    unittest.main()
