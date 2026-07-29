"""AIPOS-268 — ``agent watch --workspace-root``: the harness-agnostic filesystem pump.

This is candidate ⑫ of the 候选⑤⑫合流 — the ``agent watch`` verb carries TWO
mutually-exclusive harness modes (selected on the CLI):

- candidate ⑫ (this module, AIPOS-268): ``--workspace-root`` — a PURE CLIENT
  read-only mtime+path sentinel. No gate, no MCP, no token.
- candidate ⑤ (AIPOS-248, ``agent_connector.py``): ``--gate-url`` — a stateless
  pull for claimable tasks over the gate read tool.

A PURE CLIENT, read-only mtime+path sentinel. It snapshots two subtrees of a Lybra
workspace — ``5_tasks/queue/**`` and ``5_tasks/records/**`` — and block-polls until a
change appears, then prints a ONE-LINE JSON change summary and exits 0. On timeout
with no change it exits 2 SILENTLY; on SIGTERM/SIGINT it exits cleanly (130).

Why this exists (governance ref ⑫, pinned 2026-07-26): the gate has ZERO alarm — the
pump that wakes an advisor lives in the PRODUCT CLI, not the gate, so ANY agent that
can run bash can detect queue/records movement without a gate connection, an MCP
mount, or a token. Switching the advisor agent no longer depends on the gate being up
(Owner question 2026-07-29: "换顾问 agent 如何自动往下走").

Red lines (card AIPOS-268):
- pure client read-only: no MCP write surface, no gate process touched (gate zero change);
- zero new dependencies (stdlib only);
- a single snapshot's cost is LINEAR in the number of files (one ``stat`` per file).

Change kinds reported (``changed[].kind``):
- ``new``      — path present now, absent before;
- ``modified`` — path present in both, mtime or size changed;
- ``moved``    — a path disappeared and a path with an identical (mtime_ns, size)
                 fingerprint appeared elsewhere (the queue state-transition case:
                 pending→claimed→completed are different directories = a move);
- ``deleted``  — path disappeared with no matching reappearance. The card's example
                 enumerated new/modified/moved; ``deleted`` is a minimal honest
                 extension — silently dropping deletions would miss real changes and
                 defeat the "any change wakes the pump" purpose.
"""
from __future__ import annotations

import json
import os
import signal
import stat as stat_mod
import sys
import time
from pathlib import Path
from typing import Any, Callable

# Candidate ⑫ pump defaults (card AIPOS-268): 15s poll, 30min bounded timeout.
DEFAULT_INTERVAL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 1800.0

# Exit codes (card AIPOS-268 §2): 0 = change printed; 2 = timeout (silent);
# 130 = SIGTERM/SIGINT clean exit (no output, no traceback).
EXIT_CHANGE = 0
EXIT_TIMEOUT = 2
EXIT_SIGNAL = 130

# The two subtrees the advisor sentinel watches (relative to --workspace-root):
# queue/** = task cards moving through states (pending→claimed→completed = moves);
# records/** = session/claim/return records being written.
_WATCH_SUBTREES = ("5_tasks/queue", "5_tasks/records")


def snapshot(workspace_root: Path) -> dict[str, tuple[int, int]]:
    """One LINEAR-cost snapshot: relpath -> (st_mtime_ns, st_size) for every regular
    file under the watched subtrees. Missing subtrees are treated as empty (a workspace
    may legitimately have no records yet). Symlinked directories are not descended
    (os.walk default followlinks=False); each regular file is stat'd exactly once."""
    result: dict[str, tuple[int, int]] = {}
    root_str = str(workspace_root)
    for sub in _WATCH_SUBTREES:
        base = os.path.join(root_str, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue  # dangling symlink / race / permission — skip, never crash
                if not stat_mod.S_ISREG(st.st_mode):
                    continue  # sockets/pipes/devices/fifos are not task artifacts
                rel = os.path.relpath(full, root_str).replace(os.sep, "/")
                result[rel] = (st.st_mtime_ns, st.st_size)
    return result


def diff_snapshots(
    prev: dict[str, tuple[int, int]], curr: dict[str, tuple[int, int]]
) -> list[dict[str, str]]:
    """Compute the change list. ``moved`` = a disappeared path's (mtime_ns, size)
    fingerprint reappearing at a new path (matched one-to-one, greedily, deterministically
    by sorted path so output is stable). The result is sorted by (path, kind) for
    deterministic output and tests."""
    appeared = sorted(p for p in curr if p not in prev)
    disappeared = sorted(p for p in prev if p not in curr)
    # Fingerprint index of disappeared paths (pre-sorted) for deterministic matching.
    gone_by_fp: dict[tuple[int, int], list[str]] = {}
    for old in disappeared:
        gone_by_fp.setdefault(prev[old], []).append(old)

    changed: list[dict[str, str]] = []
    matched_old: set[str] = set()
    for new in appeared:
        candidates = gone_by_fp.get(curr[new])
        if candidates:
            old = candidates.pop(0)
            changed.append({"path": new, "kind": "moved"})
            matched_old.add(old)
        else:
            changed.append({"path": new, "kind": "new"})
    for old in disappeared:
        if old not in matched_old:
            changed.append({"path": old, "kind": "deleted"})
    for path in curr.keys() & prev.keys():
        if prev[path] != curr[path]:
            changed.append({"path": path, "kind": "modified"})

    changed.sort(key=lambda e: (e["path"], e["kind"]))
    return changed


class _SignalExit(SystemExit):
    """Raised by SIGTERM/SIGINT handlers for a clean, traceback-free exit."""


def run_fs_watch(
    args: Any,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Core bounded poll loop (testable: sleeper/clock injectable, no global signal
    state). Returns EXIT_CHANGE (0) on the first change — a one-line JSON summary is
    printed to stdout; returns EXIT_TIMEOUT (2) silently when no change occurs within
    --timeout. Arg/workspace errors print to stderr and also return 2 (the umbrella
    'did not detect a change' code; timeout is distinguished by being SILENT)."""
    interval = DEFAULT_INTERVAL_SECONDS if getattr(args, "interval", None) is None else float(args.interval)
    timeout = DEFAULT_TIMEOUT_SECONDS if getattr(args, "timeout", None) is None else float(args.timeout)
    if interval <= 0:
        print("lybra agent watch: --interval must be positive.", file=sys.stderr)
        return EXIT_TIMEOUT
    if timeout <= 0:
        print("lybra agent watch: --timeout must be positive (the loop must be bounded).", file=sys.stderr)
        return EXIT_TIMEOUT
    ws = Path(args.workspace_root)
    if not ws.is_dir():
        print(f"lybra agent watch: --workspace-root is not a directory: {ws}", file=sys.stderr)
        return EXIT_TIMEOUT

    start = clock()
    prev = snapshot(ws)
    while True:
        curr = snapshot(ws)
        changed = diff_snapshots(prev, curr)
        if changed:
            print(json.dumps({"changed": changed}, ensure_ascii=False))
            return EXIT_CHANGE
        # Bounded: stop before the next poll would exceed the timeout (the loop must end).
        if clock() - start + interval >= timeout:
            return EXIT_TIMEOUT  # silent
        sleeper(interval)
        prev = curr


def run_fs_watch_cli(args: Any) -> int:
    """CLI entry: installs clean SIGTERM/SIGINT handlers around the core loop (the
    card's 'SIGTERM 干净退出' — no Python traceback), then restores the prior handlers.
    Only valid in the main thread (where the CLI runs)."""
    def _handler(signum: int, frame: Any) -> None:
        raise _SignalExit(EXIT_SIGNAL)

    prev_term = signal.signal(signal.SIGTERM, _handler)
    prev_int = signal.signal(signal.SIGINT, _handler)
    try:
        return run_fs_watch(args)
    except _SignalExit as exc:
        return int(exc.code) if exc.code is not None else EXIT_SIGNAL
    finally:
        signal.signal(signal.SIGTERM, prev_term)
        signal.signal(signal.SIGINT, prev_int)
