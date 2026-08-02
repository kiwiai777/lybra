"""AIPOS-268 + AIPOS-284 — `agent watch --workspace-root` filesystem pump tests.

Pins (card AIPOS-268 §1-3 + red lines + AIPOS-284 S1-S5):
- diff four kinds (new / modified / moved / deleted) over real fixture trees, incl. the
  queue state-transition move (pending->claimed, same content fingerprint = a move, not
  new+deleted) and deterministic sorted output;
- the bounded loop: a change triggers exit 0 with a one-line JSON summary; timeout is a
  SILENT exit 2; SIGTERM is a clean exit (130, no output, no traceback) — verified on the
  real CLI via subprocess (zero bash `$!` methodology issues);
- summary FORMAT: a single JSON line, ``changed`` array, each entry ``{path, kind}``;
- AIPOS-284 v2: four exit codes (0/2/3/4) with dedicated semantics:
  - exit 0: expect satisfied OR diff non-empty
  - exit 2: timeout (silent, v1 behavior)
  - exit 3: end-pattern seen but expect NOT satisfied (结束无产物)
  - exit 4: stall detected (静默停滞)
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
    DEFAULT_STALL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    EXIT_CHANGE,
    EXIT_END_NO_PRODUCT,
    EXIT_SIGNAL,
    EXIT_STALL,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    _SignalExit,
    check_expect_patterns,
    diff_snapshots,
    get_observation_mtime,
    get_run_log_tail,
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
        # AIPOS-284D: --timeout 0 = infinite (no longer an error); negative is still error
        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(_args(ws, interval=1.0, timeout=-1.0))
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
            "__future__", "fnmatch", "re", "subprocess",  # AIPOS-295: git worktree detection
        }
        # AIPOS-295: psutil is conditionally imported (try/except), allowed for health monitoring
        allowed_optional = {"psutil"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    self.assertTrue(
                        module in stdlib or module in allowed_optional,
                        f"non-stdlib import: {alias.name} (allowed optional: {allowed_optional})"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".")[0]
                self.assertTrue(
                    module in stdlib or module in allowed_optional,
                    f"non-stdlib from-import: {node.module} (allowed optional: {allowed_optional})"
                )

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
        primitives — it only ever reads (stat/walk). AIPOS-284: open() is allowed in
        read mode for run-log tail reading."""
        src = Path(agent_watch_fs.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "os.remove(", "os.unlink(", "os.rmdir(", "os.rename(", "os.replace(",
            "shutil.",
            ".write_text(", ".write_bytes(",
        ):
            self.assertNotIn(forbidden, src, f"pump must be read-only: found {forbidden!r}")
        # Verify open() is only used in read mode (AIPOS-284 run-log tail reading)
        import re
        open_calls = re.findall(r'open\([^)]+\)', src)
        for call in open_calls:
            self.assertIn('"r"', call, f"open() must be read-only: {call}")

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


class FsWatchV2ExpectTests(unittest.TestCase):
    """AIPOS-284 S1: --expect glob布防即检 + 运行中检. Exit 0 when matched."""

    def test_expect_satisfied_immediately_on_startup(self):
        """S1布防即检: --expect pattern exists at startup -> exit 0 immediately."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
        args = _args(ws, interval=0.5, timeout=10.0)
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=lambda s: None, clock=time.monotonic)
        self.assertEqual(rc, EXIT_CHANGE)
        payload = json.loads(out.getvalue())
        self.assertIn("expect_satisfied", payload)
        self.assertIn("5_tasks/records/RETURN.md", payload["expect_satisfied"])

    def test_expect_satisfied_during_poll(self):
        """S1运行中检: expect appears after startup -> exit 0 on next poll."""
        ws = _make_workspace()
        created = {"done": False}

        def sleeper(seconds: float):
            if not created["done"]:
                _write(os.path.join(ws, "5_tasks/queue/claimed/artifact.json"), "output")
                created["done"] = True

        args = _args(ws, interval=0.1, timeout=10.0)
        args.expect = ["5_tasks/queue/claimed/*.json"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
        self.assertEqual(rc, EXIT_CHANGE)
        payload = json.loads(out.getvalue())
        self.assertIn("expect_satisfied", payload)
        self.assertIn("5_tasks/queue/claimed/artifact.json", payload["expect_satisfied"])

    def test_expect_glob_matches_multiple(self):
        """--expect can be a glob matching multiple files."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/a.md"), "x")
        _write(os.path.join(ws, "5_tasks/records/b.md"), "y")
        matched = check_expect_patterns(Path(ws), ["5_tasks/records/*.md"])
        self.assertEqual(sorted(matched), ["5_tasks/records/a.md", "5_tasks/records/b.md"])

    def test_multiple_expect_patterns(self):
        """--expect can be repeated; any match triggers exit 0."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/queue/pending/task.md"), "pending")
        args = _args(ws, interval=0.1, timeout=5.0)
        args.expect = ["5_tasks/records/RETURN.md", "5_tasks/queue/pending/*.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args)
        self.assertEqual(rc, EXIT_CHANGE)
        payload = json.loads(out.getvalue())
        self.assertIn("5_tasks/queue/pending/task.md", payload["expect_satisfied"])


class FsWatchV2EndNoProductTests(unittest.TestCase):
    """AIPOS-284 S2: --run-log + --end-pattern结束无产物. Exit 3 when end seen but expect NOT satisfied."""

    def test_end_pattern_seen_but_no_expect_triggers_exit3(self):
        """S2结束无产物: end-pattern appears in run-log, expect not satisfied -> exit 3 after grace."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "starting...\n")
        polls = [0]

        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] == 1:
                # First poll: append end-pattern
                with open(run_log, "a") as f:
                    f.write("execution finished\n")
            # Second poll: grace period, still no expect -> exit 3

        args = _args(ws, interval=0.1, timeout=10.0)
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = run_log
        args.end_pattern = "execution finished"
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
        self.assertEqual(rc, EXIT_END_NO_PRODUCT)
        payload = json.loads(out.getvalue())
        self.assertIn("end_no_product", payload)
        self.assertIn("execution finished", payload["end_no_product"]["run_log_tail"])

    def test_end_pattern_with_expect_satisfied_exits_0(self):
        """If expect IS satisfied after end-pattern, exit 0 (not 3)."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "starting...\n")
        polls = [0]

        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] == 1:
                with open(run_log, "a") as f:
                    f.write("execution finished\n")
                _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")

        args = _args(ws, interval=0.1, timeout=10.0)
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = run_log
        args.end_pattern = "execution finished"
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
        self.assertEqual(rc, EXIT_CHANGE)  # expect satisfied, not exit 3
        payload = json.loads(out.getvalue())
        self.assertIn("expect_satisfied", payload)

    def test_no_end_pattern_means_no_exit3(self):
        """Without --end-pattern, exit 3 never happens."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "done done done\n")
        args = _args(ws, interval=0.5, timeout=1.5)
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = run_log
        args.end_pattern = None  # no end-pattern
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=time.sleep, clock=time.monotonic)
        self.assertEqual(rc, EXIT_TIMEOUT)  # just times out


class FsWatchV2StallTests(unittest.TestCase):
    """AIPOS-284 S3: --stall-secs静默停滞. Exit 4 when observation surface silent beyond threshold."""

    def test_stall_on_run_log_silence(self):
        """S3静默停滞: run-log mtime unchanged for >= stall-secs -> exit 4."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "started\n")
        # Ensure run-log mtime is in the past
        time.sleep(0.1)
        clock_now = [0.0]

        def sleeper(seconds: float):
            clock_now[0] += seconds

        args = _args(ws, interval=0.5, timeout=100.0)
        args.expect = None
        args.run_log = run_log
        args.end_pattern = None
        args.stall_secs = 2.0  # 2 seconds stall threshold
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_STALL)
        payload = json.loads(out.getvalue())
        self.assertIn("stall", payload)
        self.assertGreaterEqual(payload["stall"]["silence_seconds"], 2)

    def test_stall_on_observation_surface_when_no_run_log(self):
        """Without run-log, stall detection uses the entire observation surface (queue+records)."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        time.sleep(0.1)
        clock_now = [0.0]

        def sleeper(seconds: float):
            clock_now[0] += seconds

        args = _args(ws, interval=0.5, timeout=100.0)
        args.expect = None
        args.run_log = None  # no run-log
        args.end_pattern = None
        args.stall_secs = 1.5
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_STALL)

    def test_stall_default_is_600_seconds(self):
        """S3: default stall threshold is 600 seconds."""
        self.assertEqual(DEFAULT_STALL_SECONDS, 600)

    def test_observation_surface_change_resets_stall_timer(self):
        """If observation surface changes, stall timer resets."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/initial.md"), "start")
        clock_now = [0.0]
        polls = [0]

        def sleeper(seconds: float):
            clock_now[0] += seconds
            polls[0] += 1
            if polls[0] == 3:
                # Add a new file to reset stall timer
                _write(os.path.join(ws, "5_tasks/records/progress.md"), "updated")

        args = _args(ws, interval=0.5, timeout=10.0)
        args.expect = None
        args.run_log = None  # Use observation surface, not run-log
        args.end_pattern = None
        args.stall_secs = 1.5
        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        # Should exit 0 with change detection, not stall
        self.assertEqual(rc, EXIT_CHANGE)


class FsWatchV2TimeoutTests(unittest.TestCase):
    """AIPOS-284 S4: timeout仍exit 2 (v1 behavior零回归)."""

    def test_timeout_with_v2_params_still_exit2(self):
        """Even with v2 params set, timeout still produces silent exit 2."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "starting\n")
        clock_now = [0.0]

        def sleeper(seconds: float):
            clock_now[0] += seconds

        args = _args(ws, interval=0.3, timeout=1.0)
        args.expect = ["5_tasks/records/NEVER_EXISTS.md"]
        args.run_log = run_log
        args.end_pattern = "will never match"
        args.stall_secs = 999.0  # won't trigger
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_TIMEOUT)
        self.assertEqual(out.getvalue(), "", "timeout must be silent")


class FsWatchF2841OutsideWatchSubtreesTests(unittest.TestCase):
    """F-284-1: --expect supports workspace-root-relative globs outside _WATCH_SUBTREES.
    Four exit codes with task_cards/ fixture (outside queue/records)."""

    def test_expect_match_outside_watch_subtrees_exit0(self):
        """Exit 0: expect pattern matches file in task_cards/ (outside queue/records)."""
        ws = _make_workspace()
        task_cards_dir = os.path.join(ws, "task_cards/AIPOS-284")
        os.makedirs(task_cards_dir, exist_ok=True)
        _write(os.path.join(task_cards_dir, "RETURN.md"), "done")
        
        args = _args(ws, interval=0.5, timeout=10.0)
        args.expect = ["task_cards/AIPOS-284/*.md"]  # Use *.md not exact path
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=lambda s: None, clock=time.monotonic)
        self.assertEqual(rc, EXIT_CHANGE)
        payload = json.loads(out.getvalue())
        self.assertIn("expect_satisfied", payload)
        self.assertIn("task_cards/AIPOS-284/RETURN.md", payload["expect_satisfied"])

    def test_expect_timeout_outside_watch_subtrees_exit2(self):
        """Exit 2: expect pattern for task_cards/ never matches, timeout."""
        ws = _make_workspace()
        task_cards_dir = os.path.join(ws, "task_cards/AIPOS-284")
        os.makedirs(task_cards_dir, exist_ok=True)
        # Create a decoy file that doesn't match pattern
        _write(os.path.join(task_cards_dir, "OTHER.md"), "decoy")
        clock_now = [0.0]

        def sleeper(seconds: float):
            clock_now[0] += seconds

        args = _args(ws, interval=0.3, timeout=1.0)
        args.expect = ["task_cards/AIPOS-284/RETURN.md"]  # doesn't exist
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_TIMEOUT)
        self.assertEqual(out.getvalue(), "", "timeout must be silent")

    def test_expect_end_no_product_outside_watch_subtrees_exit3(self):
        """Exit 3: expect for task_cards/ not satisfied, end-pattern seen."""
        ws = _make_workspace()
        task_cards_dir = os.path.join(ws, "task_cards/AIPOS-284")
        os.makedirs(task_cards_dir, exist_ok=True)
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "starting...\n")
        polls = [0]

        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] == 1:
                with open(run_log, "a") as f:
                    f.write("execution finished\n")

        args = _args(ws, interval=0.1, timeout=10.0)
        args.expect = ["task_cards/AIPOS-284/RETURN.md"]  # never created
        args.run_log = run_log
        args.end_pattern = "execution finished"
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
        self.assertEqual(rc, EXIT_END_NO_PRODUCT)
        payload = json.loads(out.getvalue())
        self.assertIn("end_no_product", payload)

    def test_expect_stall_outside_watch_subtrees_exit4(self):
        """Exit 4: expect for task_cards/ not satisfied, stall detected."""
        ws = _make_workspace()
        task_cards_dir = os.path.join(ws, "task_cards/AIPOS-284")
        os.makedirs(task_cards_dir, exist_ok=True)
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "started\n")
        time.sleep(0.1)  # Ensure mtime is in the past
        clock_now = [0.0]

        def sleeper(seconds: float):
            clock_now[0] += seconds

        args = _args(ws, interval=0.5, timeout=100.0)
        args.expect = ["task_cards/AIPOS-284/RETURN.md"]  # never created
        args.run_log = run_log
        args.end_pattern = None
        args.stall_secs = 2.0
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_STALL)
        payload = json.loads(out.getvalue())
        self.assertIn("stall", payload)

    def test_expect_glob_walk_scope_is_static_prefix(self):
        """Walk scope = static prefix of glob pattern (not full tree)."""
        ws = _make_workspace()
        # Create nested structure
        os.makedirs(os.path.join(ws, "task_cards/AIPOS-284/sub"), exist_ok=True)
        os.makedirs(os.path.join(ws, "task_cards/OTHER/deep"), exist_ok=True)
        _write(os.path.join(ws, "task_cards/AIPOS-284/RETURN.md"), "target")
        _write(os.path.join(ws, "task_cards/AIPOS-284/sub/nested.md"), "nested")
        _write(os.path.join(ws, "task_cards/OTHER/deep/decoy.md"), "decoy")
        
        # Pattern with static prefix 'task_cards/AIPOS-284' should only walk that dir
        matched = check_expect_patterns(Path(ws), ["task_cards/AIPOS-284/*.md", "task_cards/AIPOS-284/**/*.md"])
        self.assertIn("task_cards/AIPOS-284/RETURN.md", matched)
        self.assertIn("task_cards/AIPOS-284/sub/nested.md", matched)
        self.assertNotIn("task_cards/OTHER/deep/decoy.md", matched,
                         "walk must be limited to static prefix, not full tree")

    def test_expect_pattern_validation_rejects_absolute_path(self):
        """F-284B-1: absolute paths are rejected at startup with exit 5."""
        ws = _make_workspace()
        args = _args(ws, interval=0.5, timeout=10.0)
        args.expect = ["/absolute/path/file.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(args, sleeper=lambda s: None, clock=time.monotonic)
        self.assertEqual(rc, EXIT_USAGE)  # F-284B-1: validation failure -> exit 5

    def test_expect_pattern_validation_rejects_dotdot_escape(self):
        """F-284B-1: patterns with '..' are rejected at startup with exit 5."""
        ws = _make_workspace()
        args = _args(ws, interval=0.5, timeout=10.0)
        args.expect = ["../escape/file.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(args, sleeper=lambda s: None, clock=time.monotonic)
        self.assertEqual(rc, EXIT_USAGE)  # F-284B-1: exit 5

    def test_expect_pattern_validation_allows_nonexistent_prefix_inside_workspace(self):
        """F-284B-1 修向: patterns with nonexistent prefix (but inside workspace) are VALID.
        Validation only rejects absolute/.. escapes; nonexistent prefix = future event."""
        ws = _make_workspace()
        args = _args(ws, interval=0.5, timeout=1.0)  # Short timeout to exit quickly
        # Don't create task_cards/FUTURE/ — pattern is valid, just no match yet
        args.expect = ["task_cards/FUTURE/*.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        clock_now = [0.0]
        def sleeper(s: float):
            clock_now[0] += s
        with redirect_stdout(io.StringIO()):
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        # Pattern is VALID (not rejected), just times out with no match
        self.assertEqual(rc, EXIT_TIMEOUT, "nonexistent prefix should be valid, just timeout")

    def test_expect_prefix_created_after_watch_starts_exit0(self):
        """F-284B-1 修向d: 前缀后建场景 — watch starts with nonexistent prefix, prefix+file
        created during poll -> expect satisfied, exit 0. This is the dogfood case:
        executor布防 task_cards/AIPOS-277/RETURN.md before the directory exists."""
        ws = _make_workspace()
        created = {"done": False}
        
        def sleeper(seconds: float):
            if not created["done"]:
                # Simulate executor creating directory + artifact during execution
                task_dir = os.path.join(ws, "task_cards/AIPOS-277")
                os.makedirs(task_dir, exist_ok=True)
                _write(os.path.join(task_dir, "RETURN.md"), "task completed")
                created["done"] = True
        
        args = _args(ws, interval=0.1, timeout=10.0)
        args.expect = ["task_cards/AIPOS-277/RETURN.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
        self.assertEqual(rc, EXIT_CHANGE, "prefix created during watch -> expect satisfied")
        payload = json.loads(out.getvalue())
        self.assertIn("expect_satisfied", payload)
        self.assertIn("task_cards/AIPOS-277/RETURN.md", payload["expect_satisfied"])


class FsWatchV2IntegrationTests(unittest.TestCase):
    """AIPOS-284 S5: four exit codes end-to-end on real CLI."""

    def test_real_cli_exit0_expect_satisfied(self):
        """Real CLI: --expect satisfied -> exit 0."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "5", "--interval", "0.3",
             "--expect", "5_tasks/records/RETURN.md"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, EXIT_CHANGE, err)
        payload = json.loads(out.decode("utf-8").strip())
        self.assertIn("expect_satisfied", payload)

    def test_real_cli_exit2_timeout(self):
        """Real CLI: timeout with no change -> exit 2 (silent)."""
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

    def test_real_cli_exit3_end_no_product(self):
        """Real CLI: end-pattern seen but expect not satisfied -> exit 3."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "starting...\n")
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "8", "--interval", "0.3",
             "--expect", "5_tasks/records/RETURN.md",
             "--run-log", run_log, "--end-pattern", "FINISHED"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.5)
            with open(run_log, "a") as f:
                f.write("FINISHED\n")
            out, err = proc.communicate(timeout=8)
        except Exception:
            proc.kill()
            raise
        self.assertEqual(proc.returncode, EXIT_END_NO_PRODUCT, err)
        payload = json.loads(out.decode("utf-8").strip())
        self.assertIn("end_no_product", payload)

    def test_real_cli_exit4_stall(self):
        """Real CLI: observation surface silent beyond stall-secs -> exit 4."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "started\n")
        # Let run-log settle
        time.sleep(0.2)
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "20", "--interval", "0.3",
             "--run-log", run_log, "--stall-secs", "1.5"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=8)
        self.assertEqual(proc.returncode, EXIT_STALL, err)
        payload = json.loads(out.decode("utf-8").strip())
        self.assertIn("stall", payload)
        self.assertGreaterEqual(payload["stall"]["silence_seconds"], 1)


class FsWatchStreamModeTests(unittest.TestCase):
    """AIPOS-284C: --stream mode (persistent observer, event lines, no exit on change)."""

    def test_stream_mode_expect_emits_event_and_continues(self):
        """Stream mode: expect satisfied -> emit JSON event, continue running."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
        polls = [0]
        
        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] >= 3:  # Let it poll a few times after emitting event
                raise _SignalExit(EXIT_SIGNAL)  # Simulate clean exit
        
        args = _args(ws, interval=0.1, timeout=100.0)
        args.stream = True
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
            except _SignalExit:
                pass
        lines = out.getvalue().strip().split("\n")
        self.assertGreaterEqual(len(lines), 1)
        first = json.loads(lines[0])
        self.assertEqual(first["kind"], "expect")
        self.assertIn("5_tasks/records/RETURN.md", first["paths"])

    def test_stream_mode_change_emits_event_and_continues(self):
        """Stream mode: filesystem change -> emit JSON event, continue running."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        polls = [0]
        
        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] == 1:
                _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "new")
            elif polls[0] >= 3:
                raise _SignalExit(EXIT_SIGNAL)
        
        args = _args(ws, interval=0.1, timeout=100.0)
        args.stream = True
        args.expect = None
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
            except _SignalExit:
                pass
        lines = out.getvalue().strip().split("\n")
        self.assertGreaterEqual(len(lines), 1)
        first = json.loads(lines[0])
        self.assertEqual(first["kind"], "change")
        self.assertTrue(any(c["path"] == "5_tasks/queue/pending/new.md" for c in first["changed"]))

    def test_stream_mode_stall_emits_event_and_continues(self):
        """Stream mode: stall detected -> emit JSON event, continue running."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "started\n")
        time.sleep(0.1)
        clock_now = [0.0]
        polls = [0]
        
        def sleeper(seconds: float):
            clock_now[0] += seconds
            polls[0] += 1
            if polls[0] >= 5:  # After emitting stall event
                raise _SignalExit(EXIT_SIGNAL)
        
        args = _args(ws, interval=0.5, timeout=100.0)
        args.stream = True
        args.expect = None
        args.run_log = run_log
        args.end_pattern = None
        args.stall_secs = 1.5
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
            except _SignalExit:
                pass
        lines = out.getvalue().strip().split("\n")
        self.assertGreaterEqual(len(lines), 1)
        # Find stall event
        stall_event = None
        for line in lines:
            if line:
                evt = json.loads(line)
                if evt.get("kind") == "stall":
                    stall_event = evt
                    break
        self.assertIsNotNone(stall_event, "stall event should be emitted")
        self.assertGreaterEqual(stall_event["silence_seconds"], 1)

    def test_stream_mode_run_end_emits_event_and_continues(self):
        """Stream mode: end-pattern seen -> emit run_end event, continue running."""
        ws = _make_workspace()
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "starting...\n")
        polls = [0]
        
        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] == 1:
                with open(run_log, "a") as f:
                    f.write("execution finished\n")
            elif polls[0] >= 4:  # After grace + run_end event
                raise _SignalExit(EXIT_SIGNAL)
        
        args = _args(ws, interval=0.1, timeout=100.0)
        args.stream = True
        args.expect = ["5_tasks/records/RETURN.md"]  # Never created
        args.run_log = run_log
        args.end_pattern = "execution finished"
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
            except _SignalExit:
                pass
        lines = out.getvalue().strip().split("\n")
        self.assertGreaterEqual(len(lines), 1)
        # Find run_end event
        run_end_event = None
        for line in lines:
            if line:
                evt = json.loads(line)
                if evt.get("kind") == "run_end":
                    run_end_event = evt
                    break
        self.assertIsNotNone(run_end_event, "run_end event should be emitted")
        self.assertIn("execution finished", run_end_event["run_log_tail"])

    def test_stream_mode_expect_deduplication(self):
        """Stream mode S3: same expect file reported only once (new appearance only)."""
        ws = _make_workspace()
        polls = [0]
        
        def sleeper(seconds: float):
            polls[0] += 1
            if polls[0] == 1:
                _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "first")
            elif polls[0] >= 5:  # Multiple polls after first match
                raise _SignalExit(EXIT_SIGNAL)
        
        args = _args(ws, interval=0.1, timeout=100.0)
        args.stream = True
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=sleeper, clock=time.monotonic)
            except _SignalExit:
                pass
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        # Should only have ONE expect event (deduplication)
        expect_events = [json.loads(l) for l in lines if json.loads(l).get("kind") == "expect"]
        self.assertEqual(len(expect_events), 1, "expect file should be reported only once")

    def test_stream_mode_multi_event_sequence(self):
        """Stream mode S4: multiple events in sequence (expect -> change -> stall)."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        run_log = os.path.join(ws, "run.log")
        _write(run_log, "started\n")
        time.sleep(0.1)
        clock_now = [0.0]
        polls = [0]
        
        def sleeper(seconds: float):
            clock_now[0] += seconds
            polls[0] += 1
            if polls[0] == 1:
                _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
            elif polls[0] == 2:
                _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "new")
            elif polls[0] >= 6:  # After stall threshold
                raise _SignalExit(EXIT_SIGNAL)
        
        args = _args(ws, interval=0.3, timeout=100.0)
        args.stream = True
        args.expect = ["5_tasks/records/RETURN.md"]
        args.events = "all"  # AIPOS-284D: need explicit 'all' to see both expect+change
        args.run_log = run_log
        args.end_pattern = None
        args.stall_secs = 1.0
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
            except _SignalExit:
                pass
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        events = [json.loads(l) for l in lines]
        kinds = [e["kind"] for e in events]
        # Should have expect, change, and stall events in order
        self.assertIn("expect", kinds)
        self.assertIn("change", kinds)
        self.assertIn("stall", kinds)
        # Verify order: expect before change
        expect_idx = kinds.index("expect")
        change_idx = kinds.index("change")
        self.assertLess(expect_idx, change_idx, "expect should come before change")

    def test_stream_mode_timeout_exits_2_with_end_event(self):
        """AIPOS-284D S2: stream mode timeout produces exit 2 AND emits kind:end event."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        clock_now = [0.0]
        
        def sleeper(seconds: float):
            clock_now[0] += seconds
        
        args = _args(ws, interval=0.3, timeout=1.0)
        args.stream = True
        args.expect = None
        args.events = None
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, EXIT_TIMEOUT)
        # AIPOS-284D S2: stream mode must emit kind:end before exit (禁无声退)
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        self.assertEqual(len(lines), 1, "should emit exactly one end event")
        event = json.loads(lines[0])
        self.assertEqual(event["kind"], "end")
        self.assertEqual(event["reason"], "timeout")

    def test_default_mode_unchanged_zero_regression(self):
        """S2 zero-regression: default mode (no --stream) behaves exactly as before."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
        args = _args(ws, interval=0.1, timeout=5.0)
        args.stream = False  # Explicit default
        args.expect = ["5_tasks/records/RETURN.md"]
        args.run_log = None
        args.end_pattern = None
        args.stall_secs = None
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args)
        self.assertEqual(rc, EXIT_CHANGE, "default mode should exit 0 on expect")
        payload = json.loads(out.getvalue())
        self.assertIn("expect_satisfied", payload, "default mode uses old JSON format")


class FsWatchStreamModeIntegrationTests(unittest.TestCase):
    """AIPOS-284C S4: stream mode end-to-end on real CLI."""

    def test_real_cli_stream_mode_multi_event(self):
        """Real CLI --stream: multiple events emitted, process continues."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/records/seed.md"), "seed")
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "3", "--interval", "0.3",
             "--stream", "--events", "all", "--expect", "5_tasks/records/*.md"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.5)
            # Trigger multiple events
            _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
            time.sleep(0.5)
            _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "new")
            time.sleep(0.8)
            # Should still be running, terminate it
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=3)
        except Exception:
            proc.kill()
            raise
        # Should exit cleanly via signal
        self.assertEqual(proc.returncode, EXIT_SIGNAL, err)
        lines = [l for l in out.decode("utf-8").strip().split("\n") if l]
        # Should have multiple event lines
        self.assertGreaterEqual(len(lines), 2, "should emit multiple events")
        events = [json.loads(l) for l in lines]
        kinds = [e["kind"] for e in events]
        self.assertIn("expect", kinds)
        self.assertIn("change", kinds)


if __name__ == "__main__":
    unittest.main()


class FsWatch284DTests(unittest.TestCase):
    """AIPOS-284D: --events filter + infinite timeout + kind:end event."""

    def test_events_filter_expect_only(self):
        """S1 (F-284C-1): --events expect suppresses change events."""
        ws = _make_workspace()
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=float('inf'),
            stream=True,
            expect=["5_tasks/records/RETURN.md"],
            events="expect",
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        sleeps = []
        
        def mock_sleep(n: float) -> None:
            sleeps.append(n)
            clock_state["t"] += n
            # After first sleep, create a filesystem change (should be filtered)
            if len(sleeps) == 1:
                _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "change")
            # After second sleep, create expect match
            if len(sleeps) == 2:
                _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
            # Stop after third sleep
            if len(sleeps) >= 3:
                raise _SignalExit(EXIT_SIGNAL)
        
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
            except _SignalExit:
                pass
        
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        events = [json.loads(l) for l in lines]
        kinds = [e["kind"] for e in events]
        
        # Should only see expect event, not change
        self.assertIn("expect", kinds, "expect event should be emitted")
        self.assertNotIn("change", kinds, "change event should be suppressed by --events expect")
    
    def test_events_filter_change_only(self):
        """S1: --events change suppresses expect events."""
        ws = _make_workspace()
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=float('inf'),
            stream=True,
            expect=["5_tasks/records/RETURN.md"],
            events="change",
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        sleeps = []
        
        def mock_sleep(n: float) -> None:
            sleeps.append(n)
            clock_state["t"] += n
            # Create expect match (should be filtered)
            if len(sleeps) == 1:
                _write(os.path.join(ws, "5_tasks/records/RETURN.md"), "done")
            # Create filesystem change
            if len(sleeps) == 2:
                _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "change")
            if len(sleeps) >= 3:
                raise _SignalExit(EXIT_SIGNAL)
        
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
            except _SignalExit:
                pass
        
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        events = [json.loads(l) for l in lines]
        kinds = [e["kind"] for e in events]
        
        # Should only see change event, not expect
        self.assertIn("change", kinds, "change event should be emitted")
        self.assertNotIn("expect", kinds, "expect event should be suppressed by --events change")
    
    def test_events_default_expect_when_pattern_given(self):
        """S1: default behavior when --expect given is 'expect' mode (抑噪)."""
        ws = _make_workspace()
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=float('inf'),
            stream=True,
            expect=["5_tasks/records/RETURN.md"],
            events=None,  # No explicit --events
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        sleeps = []
        
        def mock_sleep(n: float) -> None:
            sleeps.append(n)
            clock_state["t"] += n
            # Create a change (should be suppressed in default expect mode)
            if len(sleeps) == 1:
                _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "noise")
            if len(sleeps) >= 2:
                raise _SignalExit(EXIT_SIGNAL)
        
        with redirect_stdout(io.StringIO()) as out:
            try:
                run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
            except _SignalExit:
                pass
        
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        # Should have zero events (change suppressed, no expect match)
        self.assertEqual(len(lines), 0, "default expect mode should suppress change events")
    
    def test_timeout_zero_is_infinite(self):
        """S2 (F-284C-2): --timeout 0 means infinite (永挂)."""
        ws = _make_workspace()
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=0.0,  # Explicit infinite
            stream=True,
            expect=None,
            events=None,
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        sleeps = []
        
        def mock_sleep(n: float) -> None:
            sleeps.append(n)
            clock_state["t"] += 10.0  # Fast-forward time
            if len(sleeps) >= 5:  # After many polls, still no timeout
                raise _SignalExit(EXIT_SIGNAL)
        
        with redirect_stdout(io.StringIO()) as out:
            try:
                rc = run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
            except _SignalExit as e:
                rc = e.code
        
        # Should exit via signal, not timeout
        self.assertEqual(rc, EXIT_SIGNAL, "timeout=0 should never timeout")
    
    def test_stream_mode_defaults_to_infinite_timeout(self):
        """S2: --stream without --timeout defaults to infinite."""
        ws = _make_workspace()
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=None,  # No explicit timeout
            stream=True,
            expect=None,
            events=None,
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        sleeps = []
        
        def mock_sleep(n: float) -> None:
            sleeps.append(n)
            clock_state["t"] += 100.0  # Simulate long time passing
            if len(sleeps) >= 3:
                raise _SignalExit(EXIT_SIGNAL)
        
        with redirect_stdout(io.StringIO()) as out:
            try:
                rc = run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
            except _SignalExit as e:
                rc = e.code
        
        self.assertEqual(rc, EXIT_SIGNAL, "stream mode should default to infinite timeout")
    
    def test_stream_emits_end_event_on_timeout(self):
        """S2: stream mode emits kind:end with reason=timeout before exit."""
        ws = _make_workspace()
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=0.5,
            stream=True,
            expect=None,
            events=None,
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        
        def mock_sleep(n: float) -> None:
            clock_state["t"] += n
        
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
        
        self.assertEqual(rc, EXIT_TIMEOUT)
        lines = [l for l in out.getvalue().strip().split("\n") if l]
        self.assertEqual(len(lines), 1, "should emit one end event")
        event = json.loads(lines[0])
        self.assertEqual(event["kind"], "end")
        self.assertEqual(event["reason"], "timeout")
    
    def test_stream_emits_end_event_on_signal(self):
        """S2: stream mode emits kind:end with reason=signal before exit."""
        ws = _make_workspace()
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch",
             "--workspace-root", ws, "--timeout", "10", "--interval", "0.5", "--stream"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.8)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=3)
        except Exception:
            proc.kill()
            raise
        
        self.assertEqual(proc.returncode, EXIT_SIGNAL, err)
        lines = [l for l in out.decode("utf-8").strip().split("\n") if l]
        # Last line should be end event
        self.assertGreaterEqual(len(lines), 1, "should emit at least end event")
        end_event = json.loads(lines[-1])
        self.assertEqual(end_event["kind"], "end")
        self.assertEqual(end_event["reason"], "signal")
    
    def test_default_mode_unchanged_zero_regression(self):
        """S3: default mode (no --stream) behavior unchanged."""
        ws = _make_workspace()
        _write(os.path.join(ws, "5_tasks/queue/pending/task.md"), "content")
        args = SimpleNamespace(
            workspace_root=ws,
            interval=0.1,
            timeout=1.0,
            stream=False,  # Default mode
            expect=None,
            events=None,
            run_log=None,
            end_pattern=None,
            stall_secs=None,
        )
        clock_state = {"t": 0.0}
        
        def mock_sleep(n: float) -> None:
            clock_state["t"] += n
            # Create a change
            _write(os.path.join(ws, "5_tasks/queue/pending/new.md"), "new")
        
        with redirect_stdout(io.StringIO()) as out:
            rc = run_fs_watch(args, sleeper=mock_sleep, clock=lambda: clock_state["t"])
        
        # Should exit 0 immediately on change
        self.assertEqual(rc, EXIT_CHANGE)
        payload = json.loads(out.getvalue())
        # Old format: {"changed": [...]}
        self.assertIn("changed", payload, "default mode should use old format")
        self.assertNotIn("kind", payload, "default mode should not emit event format")
