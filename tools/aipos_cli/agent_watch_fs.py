"""AIPOS-268 + AIPOS-284 + AIPOS-284C + AIPOS-295 — ``agent watch --workspace-root``: filesystem pump v2 + health monitoring.

This is candidate ⑫ of the 候选⑤⑫合流 — the ``agent watch`` verb carries TWO
mutually-exclusive harness modes (selected on the CLI):

- candidate ⑫ (this module, AIPOS-268 + AIPOS-284 + AIPOS-284C + AIPOS-295): ``--workspace-root`` — a PURE CLIENT
  read-only mtime+path sentinel. No gate, no MCP, no token.
- candidate ⑤ (AIPOS-248, ``agent_connector.py``): ``--gate-url`` — a stateless
  pull for claimable tasks over the gate read tool.

A PURE CLIENT, read-only mtime+path sentinel. It snapshots two subtrees of a Lybra
workspace — ``5_tasks/queue/**`` and ``5_tasks/records/**`` — and block-polls until a
change appears, then prints a ONE-LINE JSON change summary and exits 0. On timeout
with no change it exits 2 SILENTLY; on SIGTERM/SIGINT it exits cleanly (130).

AIPOS-284C --stream mode: a PERSISTENT observer that prints JSON event lines (line-buffered,
immediate flush) and continues running. Only SIGTERM/SIGINT/--timeout terminate the process.
Event deduplication: each expect file is reported at most once (new appearance only).

AIPOS-295 --health mode: health monitoring with periodic heartbeat events. When combined with
--stream mode, emits ``kind:health`` events at regular intervals reporting process liveness,
CPU delta, session file activity, and worktree changes. Supports ``kind:unhealthy`` detection
when the monitored process exhibits signs of death (process gone OR sustained silence across
all observation surfaces).

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

AIPOS-284 v2 enhancements ("listening for death silences"):
- ``--expect <glob>`` (multi):布防即检 — check immediately on startup; exit 0 if any match.
- ``--run-log <path>`` + ``--end-pattern <regex>``: 结束无产物 — exit 3 if end-pattern
  appears in run-log but expect is NOT satisfied (after a one-poll grace period).
- ``--stall-secs <n>``: 静默停滞 — exit 4 if run-log (or observation surface if no run-log)
  has not changed for ≥n seconds.

AIPOS-284C --stream mode event kinds:
- ``expect``   — expect pattern matched (new file only; deduplicated)
- ``change``   — filesystem change detected (new/modified/moved/deleted)
- ``stall``    — observation surface silent beyond threshold
- ``run_end``  — end-pattern seen but expect not satisfied
- ``end``      — stream terminated (AIPOS-284D S2: any exit reason; reason field explains)

AIPOS-284D enhancements:
- ``--events expect|change|all``: F-284C-1 抑噪 — when --expect is given, default to 
  'expect' (only expect events); 'change' = only filesystem changes; 'all' = both.
- ``--stream`` + ``--timeout 0``: F-284C-2 常驻豁免 — timeout=0 means infinite (never exit on timeout).
- Stream exit: always emit ``kind:end`` event with reason before process termination.

Exit codes (AIPOS-284 S4 + AIPOS-284C S2 + AIPOS-284D S2):
- 0: change detected (expect satisfied OR diff non-empty) — DEFAULT MODE ONLY
- 2: timeout (silent in default mode; 'end' event in stream mode) — both modes
- 3: end-pattern seen, expect NOT satisfied — DEFAULT MODE ONLY
- 4: stall detected — DEFAULT MODE ONLY
- 130: SIGTERM/SIGINT clean exit (stream mode: 'end' event with reason=signal) — both modes

In --stream mode: exit 0/3/4 become JSON event lines; timeout/signal emit 'end' event then exit.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import signal
import stat as stat_mod
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable




try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

# Candidate ⑫ pump defaults (card AIPOS-268): 15s poll, 30min bounded timeout.
DEFAULT_INTERVAL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 1800.0

# Exit codes (AIPOS-268 + AIPOS-284 + F-284B-1): 0 = change/expect satisfied; 2 = timeout (silent);
# 3 = end-pattern seen but expect NOT satisfied; 4 = stall detected;
# 5 = usage error (布防拒绝: invalid --expect pattern);
# 130 = SIGTERM/SIGINT clean exit (no output, no traceback).
EXIT_CHANGE = 0
EXIT_TIMEOUT = 2
EXIT_END_NO_PRODUCT = 3
EXIT_STALL = 4
EXIT_USAGE = 5
EXIT_SIGNAL = 130

# The two subtrees the advisor sentinel watches (relative to --workspace-root):
# queue/** = task cards moving through states (pending→claimed→completed = moves);
# records/** = session/claim/return records being written.
_WATCH_SUBTREES = ("5_tasks/queue", "5_tasks/records")

# AIPOS-284: default stall threshold (10 minutes = 600 seconds).
DEFAULT_STALL_SECONDS = 600

# AIPOS-295: health monitoring defaults (5 minutes = 300 seconds).
DEFAULT_HEALTH_INTERVAL = 300
DEFAULT_UNHEALTHY_CYCLES = 2  # Consecutive silent cycles before declaring unhealthy


def _find_pi_processes(pid_file: str | None = None, proc_pattern: str | None = None) -> list[int]:
    """AIPOS-295 S1: find pi subprocess tree PIDs (excluding timeout wrapper).
    
    Args:
        pid_file: Path to PID file (read parent PID from file)
        proc_pattern: Process name pattern (e.g., 'node')
    
    Returns:
        List of PIDs matching criteria (pi children, not timeout shell)
    """
    if psutil is None:
        return []
    
    pids = []
    
    if pid_file and os.path.isfile(pid_file):
        try:
            parent_pid = int(Path(pid_file).read_text().strip())
            parent = psutil.Process(parent_pid)
            # Get all children recursively
            for child in parent.children(recursive=True):
                try:
                    # Skip shell/timeout processes (cmdline contains 'timeout' or 'bash')
                    cmdline = ' '.join(child.cmdline()).lower()
                    if 'timeout' not in cmdline and 'bash' not in cmdline:
                        pids.append(child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (ValueError, OSError, psutil.NoSuchProcess):
            pass
    
    if proc_pattern:
        # Find all processes matching pattern
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = proc.info['name'] or ''
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()
                if proc_pattern.lower() in name.lower() or proc_pattern.lower() in cmdline:
                    # Exclude timeout/bash wrappers
                    if 'timeout' not in cmdline and 'bash' not in cmdline:
                        pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    
    return list(set(pids))  # Deduplicate


def _get_process_cpu_time(pids: list[int]) -> float:
    """AIPOS-295 S1: get total CPU time (user+system) for a list of PIDs.
    
    Returns sum of cpu_times (seconds) across all PIDs. Returns 0.0 if psutil unavailable.
    """
    if psutil is None or not pids:
        return 0.0
    
    total = 0.0
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            cpu_times = proc.cpu_times()
            total += cpu_times.user + cpu_times.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _count_new_session_files(session_dirs: list[str], last_check: float) -> int:
    """AIPOS-295 S1: count new files in session directories since last_check timestamp.
    
    Args:
        session_dirs: List of session directory paths
        last_check: Unix timestamp of last check
    
    Returns:
        Number of files created/modified after last_check
    """
    count = 0
    for dir_path in session_dirs:
        if not os.path.isdir(dir_path):
            continue
        for root, _dirs, files in os.walk(dir_path):
            for name in files:
                full = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(full)
                    if mtime > last_check:
                        count += 1
                except OSError:
                    continue
    return count


def _count_worktree_changes(worktree_path: str, since_timestamp: float) -> int:
    """AIPOS-295 S1: count git worktree changes (new/modified files) since timestamp.
    
    Uses git status --porcelain to detect changes. Returns 0 if not a git repo or git unavailable.
    """
    if not os.path.isdir(worktree_path):
        return 0
    
    try:
        # Use git status to detect changes
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            lines = [l for l in result.stdout.strip().split('\n') if l]
            return len(lines)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    
    return 0


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


def _extract_static_prefix(pattern: str) -> str:
    """Extract the static directory prefix from a glob pattern (before any wildcard).
    Examples: 'task_cards/AIPOS-284/*.md' -> 'task_cards/AIPOS-284',
              '5_tasks/*/foo.md' -> '5_tasks', 'foo/*.md' -> 'foo', '*.md' -> ''"""
    parts = pattern.split("/")
    static_parts = []
    for part in parts:
        if any(c in part for c in "*?[]"):
            break
        static_parts.append(part)
    return "/".join(static_parts)


def _validate_expect_pattern(workspace_root: Path, pattern: str) -> tuple[bool, str]:
    """F-284B-1: validate that an expect pattern can potentially match within workspace-root.
    Returns (is_valid, error_message). A pattern is INVALID only if:
    - it starts with '/' (absolute path)
    - it contains '..' (escape attempt)
    
    A pattern whose static prefix does NOT exist yet is VALID (the修向: "静态前缀目前不存在
    是合法的未来事件,watch 的本義就是等它出現"). check_expect_patterns handles dynamic
    prefix appearance on each poll."""
    # Reject absolute paths
    if pattern.startswith("/"):
        return False, f"--expect pattern must be relative to workspace-root: {pattern}"
    
    # Reject patterns that escape workspace via '..'
    if ".." in pattern:
        return False, f"--expect pattern cannot escape workspace-root: {pattern}"
    
    return True, ""


def check_expect_patterns(workspace_root: Path, patterns: list[str]) -> list[str]:
    """AIPOS-284 S1 + F-284-1: check --expect globs against the workspace. Supports any
    relative glob within workspace-root. Walk scope = static prefix directory (not full tree).
    Returns list of matched paths (POSIX relative). Empty list = no match."""
    if not patterns:
        return []
    root_str = str(workspace_root)
    matches: list[str] = []
    
    for pat in patterns:
        # Extract static prefix to limit walk scope (not full tree scan)
        static_prefix = _extract_static_prefix(pat)
        if static_prefix:
            # If static prefix is the entire pattern (no wildcards), check directly
            if static_prefix == pat:
                full_path = os.path.join(root_str, pat)
                try:
                    st = os.stat(full_path)
                    if stat_mod.S_ISREG(st.st_mode):
                        matches.append(pat)
                except OSError:
                    pass  # File doesn't exist
                continue
            
            # Walk from the static prefix directory
            base = os.path.join(root_str, static_prefix)
        else:
            # Pattern like '*.md' at root: walk from workspace root
            base = root_str
        
        if not os.path.isdir(base):
            continue  # Prefix doesn't exist yet, no matches
        
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if not stat_mod.S_ISREG(st.st_mode):
                    continue
                rel = os.path.relpath(full, root_str).replace(os.sep, "/")
                if fnmatch.fnmatch(rel, pat):
                    matches.append(rel)
    
    return sorted(set(matches))


def get_run_log_tail(run_log_path: str | None, lines: int = 20) -> str:
    """AIPOS-284 S2/S3: read last N lines of run-log (or empty if not exists/not readable)."""
    if not run_log_path:
        return ""
    try:
        with open(run_log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.readlines()
            return "".join(content[-lines:])
    except Exception:
        return ""


def get_observation_mtime(workspace_root: Path, run_log_path: str | None) -> float:
    """AIPOS-284 S3: get the latest mtime of the observation surface.
    If run-log is given, return its mtime; otherwise return max mtime across all watched files.
    Returns 0.0 if nothing is observable."""
    if run_log_path:
        try:
            return os.path.getmtime(run_log_path)
        except OSError:
            return 0.0
    # No run-log: scan the entire observation surface (queue + records)
    snap = snapshot(workspace_root)
    if not snap:
        return 0.0
    return max(mtime_ns / 1e9 for mtime_ns, _size in snap.values())


class _SignalExit(SystemExit):
    """Raised by SIGTERM/SIGINT handlers for a clean, traceback-free exit."""


def run_fs_watch(
    args: Any,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Core bounded poll loop (testable: sleeper/clock injectable, no global signal
    state). Returns EXIT_CHANGE (0) on the first change OR expect satisfaction — a
    one-line JSON summary is printed to stdout; returns EXIT_TIMEOUT (2) silently when
    no change occurs within --timeout; returns EXIT_END_NO_PRODUCT (3) when end-pattern
    is seen but expect is NOT satisfied; returns EXIT_STALL (4) when the observation
    surface is silent beyond --stall-secs. Arg/workspace errors print to stderr and
    also return 2 (the umbrella 'did not detect a change' code; timeout is distinguished
    by being SILENT).
    
    AIPOS-284 S1-S4:布防即检 / 结束无产物 / 静默停滞 / 超时仍exit2.
    AIPOS-284C --stream mode: emit JSON event lines and continue (no exit 0/3/4).
    AIPOS-284D: --events expect|change|all (F-284C-1抑噪); --timeout 0 = infinite (F-284C-2常驻豁免); kind:end event on exit.
    Event deduplication (S3): track reported expect files, only report new appearances.
    """
    interval = DEFAULT_INTERVAL_SECONDS if getattr(args, "interval", None) is None else float(args.interval)
    timeout_arg = getattr(args, "timeout", None)
    stream_mode = getattr(args, "stream", False)
    
    # AIPOS-284D S2: --stream defaults to infinite timeout; --timeout 0 = explicit infinite
    if timeout_arg is None:
        timeout = float('inf') if stream_mode else DEFAULT_TIMEOUT_SECONDS
    else:
        timeout_val = float(timeout_arg)
        if timeout_val == 0:
            # F-284C-2: --timeout 0 = explicit infinite (常驻永挂)
            timeout = float('inf')
        elif timeout_val < 0:
            print("lybra agent watch: --timeout must be non-negative (0 = infinite).", file=sys.stderr)
            return EXIT_TIMEOUT
        else:
            timeout = timeout_val
    
    if interval <= 0:
        print("lybra agent watch: --interval must be positive.", file=sys.stderr)
        return EXIT_TIMEOUT
    ws = Path(args.workspace_root)
    if not ws.is_dir():
        print(f"lybra agent watch: --workspace-root is not a directory: {ws}", file=sys.stderr)
        return EXIT_TIMEOUT
    
    # AIPOS-284D S1: --events filter (F-284C-1 抑噪)
    events_filter = str(getattr(args, "events", None) or "")
    if not events_filter:
        # Default: if --expect is given, only emit expect events; otherwise emit all
        expect_patterns = getattr(args, "expect", None) or []
        events_filter = "expect" if expect_patterns else "all"
    
    if events_filter not in ("expect", "change", "all"):
        print(f"lybra agent watch: --events must be one of: expect, change, all (got: {events_filter})", file=sys.stderr)
        return EXIT_USAGE
    
    # AIPOS-284 v2 parameters
    expect_patterns: list[str] = getattr(args, "expect", None) or []
    # F-284B-1: validate expect patterns (reject 绝对路径/.. only; 不存在的前缀 = 合法未来)
    for pat in expect_patterns:
        is_valid, error_msg = _validate_expect_pattern(ws, pat)
        if not is_valid:
            print(f"lybra agent watch: {error_msg}", file=sys.stderr)
            return EXIT_USAGE
    run_log_path: str | None = getattr(args, "run_log", None)
    end_pattern_str: str | None = getattr(args, "end_pattern", None)
    end_pattern: re.Pattern | None = None
    if end_pattern_str:
        try:
            end_pattern = re.compile(end_pattern_str)
        except re.error as e:
            print(f"lybra agent watch: invalid --end-pattern regex: {e}", file=sys.stderr)
            return EXIT_TIMEOUT
    # Stall detection: only enabled if user explicitly sets --stall-secs OR --run-log is given
    stall_secs_arg = getattr(args, "stall_secs", None)
    stall_enabled = stall_secs_arg is not None or run_log_path is not None
    if stall_enabled:
        if stall_secs_arg is None:
            stall_secs = DEFAULT_STALL_SECONDS
        else:
            stall_secs = float(stall_secs_arg)
            if stall_secs <= 0:
                print("lybra agent watch: --stall-secs must be positive.", file=sys.stderr)
                return EXIT_TIMEOUT
    else:
        stall_secs = float('inf')  # Effectively disabled
    
    # AIPOS-284C S3: event deduplication (track reported expect files)
    reported_expect_files: set[str] = set()
    
    # AIPOS-295: health monitoring parameters
    health_interval_arg = getattr(args, "health", None)
    health_enabled = health_interval_arg is not None
    if health_enabled:
        if not stream_mode:
            print("lybra agent watch: --health requires --stream mode.", file=sys.stderr)
            return EXIT_USAGE
        health_interval = float(health_interval_arg) if health_interval_arg else DEFAULT_HEALTH_INTERVAL
        if health_interval <= 0:
            print("lybra agent watch: --health interval must be positive.", file=sys.stderr)
            return EXIT_USAGE
        
        # Health observation parameters
        pid_file = getattr(args, "pid_file", None)
        proc_pattern = getattr(args, "proc_pattern", None)
        session_dirs_arg = getattr(args, "session_dirs", None)
        session_dirs = session_dirs_arg.split(",") if session_dirs_arg else []
        worktree_path = getattr(args, "worktree_path", None) or str(ws.parent)  # Default to parent of workspace
        
        # Health state tracking
        last_health_check = 0.0
        last_cpu_time = 0.0
        last_health_timestamp = clock()
        silent_cycles = 0
        unhealthy_threshold = getattr(args, "unhealthy_cycles", DEFAULT_UNHEALTHY_CYCLES)
    else:
        health_interval = 0.0
        last_health_check = 0.0

    start = clock()
    prev = snapshot(ws)
    
    # S1: 布防即检 — check expect patterns IMMEDIATELY on startup
    if expect_patterns:
        matched = check_expect_patterns(ws, expect_patterns)
        if matched:
            if stream_mode:
                # AIPOS-284C: emit event and continue, track reported files
                new_files = [f for f in matched if f not in reported_expect_files]
                if new_files:
                    print(json.dumps({"kind": "expect", "paths": new_files}, ensure_ascii=False), flush=True)
                    reported_expect_files.update(new_files)
            else:
                # Default mode: exit 0
                print(json.dumps({"expect_satisfied": matched}, ensure_ascii=False))
                return EXIT_CHANGE
    
    # Track last observation mtime for stall detection (initialized to current time if no observable surface)
    last_obs_mtime = get_observation_mtime(ws, run_log_path)
    initial_obs_mtime = last_obs_mtime  # AIPOS-316: track initial state for never-changed detection
    last_obs_check = start  # Track when we last checked observation mtime
    stall_hint_emitted = False  # AIPOS-316: emit observation surface hint only once
    end_pattern_seen = False
    grace_after_end = False  # S2: one-poll grace period after end-pattern

    while True:
        # AIPOS-295: Health monitoring (periodic heartbeat)
        if health_enabled:
            elapsed_since_health = clock() - last_health_check
            if elapsed_since_health >= health_interval:
                # Perform health check
                pids = _find_pi_processes(pid_file, proc_pattern) if health_enabled else []
                proc_alive = len(pids) > 0
                
                # CPU delta
                current_cpu_time = _get_process_cpu_time(pids)
                cpu_delta = current_cpu_time - last_cpu_time
                last_cpu_time = current_cpu_time
                
                # Session files
                new_session_files = _count_new_session_files(session_dirs, last_health_timestamp)
                
                # Worktree changes
                worktree_changes = _count_worktree_changes(worktree_path, last_health_timestamp) if worktree_path else 0
                
                # Silent duration (time since last health check)
                silent_secs = int(elapsed_since_health)
                
                # Health event
                health_data = {
                    "kind": "health",
                    "proc_alive": proc_alive,
                    "cpu_delta": round(cpu_delta, 2),
                    "new_session_files": new_session_files,
                    "worktree_changes": worktree_changes,
                    "silent_secs": silent_secs
                }
                print(json.dumps(health_data, ensure_ascii=False), flush=True)
                
                # AIPOS-295 S2: Unhealthy detection
                # Process gone OR (cpu_delta ≈ 0 AND new_session_files = 0 AND worktree_changes = 0)
                if not proc_alive or (cpu_delta < 0.01 and new_session_files == 0 and worktree_changes == 0):
                    silent_cycles += 1
                    if silent_cycles >= unhealthy_threshold:
                        # Emit unhealthy event
                        unhealthy_data = {
                            "kind": "unhealthy",
                            "reason": "process_gone" if not proc_alive else "sustained_silence",
                            "silent_cycles": silent_cycles,
                            "proc_alive": proc_alive,
                            "cpu_delta": round(cpu_delta, 2),
                            "new_session_files": new_session_files,
                            "worktree_changes": worktree_changes
                        }
                        print(json.dumps(unhealthy_data, ensure_ascii=False), flush=True)
                        # Note: unhealthy event is emitted but watch continues
                        # Respawn logic is handled by supervise command, not watch
                        silent_cycles = 0  # Reset after reporting
                else:
                    silent_cycles = 0  # Reset on any activity
                
                last_health_check = clock()
                last_health_timestamp = time.time()  # Wall clock for file timestamps
        
        curr = snapshot(ws)
        changed = diff_snapshots(prev, curr)
        
        # Check expect patterns on every poll (S1: running检 same as 布防检)
        if expect_patterns and events_filter in ("expect", "all"):
            matched = check_expect_patterns(ws, expect_patterns)
            if matched:
                if stream_mode:
                    # AIPOS-284C S3: deduplicate — only report new files
                    new_files = [f for f in matched if f not in reported_expect_files]
                    if new_files:
                        print(json.dumps({"kind": "expect", "paths": new_files}, ensure_ascii=False), flush=True)
                        reported_expect_files.update(new_files)
                else:
                    print(json.dumps({"expect_satisfied": matched}, ensure_ascii=False))
                    return EXIT_CHANGE
        
        # Regular diff change (AIPOS-284D S1: filtered by --events)
        if changed and events_filter in ("change", "all"):
            if stream_mode:
                # AIPOS-284C: emit event and continue
                print(json.dumps({"kind": "change", "changed": changed}, ensure_ascii=False), flush=True)
                prev = curr  # Advance snapshot to avoid re-reporting same change
            else:
                print(json.dumps({"changed": changed}, ensure_ascii=False))
                return EXIT_CHANGE
        
        # S2: 结束无产物 — end-pattern logic
        if end_pattern and run_log_path:
            tail = get_run_log_tail(run_log_path)
            if end_pattern.search(tail):
                if not end_pattern_seen:
                    # First time seeing end-pattern: mark and give one grace poll
                    end_pattern_seen = True
                    grace_after_end = True
                elif grace_after_end:
                    # Grace poll done, still no expect match
                    grace_after_end = False
                    # In stream mode, emit run_end and continue; in default mode, fall through to else on next poll
                else:
                    # Already past grace, still no product
                    if stream_mode:
                        # AIPOS-284C: emit run_end event and continue (only once, then reset)
                        print(json.dumps({"kind": "run_end", "run_log_tail": tail.strip().split("\n")[-1] if tail.strip() else ""}, ensure_ascii=False), flush=True)
                        # Reset to allow future detection
                        end_pattern_seen = False
                    else:
                        # Default mode: exit 3
                        print(json.dumps({"end_no_product": {"run_log_tail": tail.strip().split("\n")[-1] if tail.strip() else ""}}, ensure_ascii=False))
                        return EXIT_END_NO_PRODUCT
        
        # S3: 静默停滞 — stall detection
        current_obs_mtime = get_observation_mtime(ws, run_log_path)
        if current_obs_mtime > last_obs_mtime:
            # Observation surface changed, reset stall tracking
            last_obs_mtime = current_obs_mtime
            last_obs_check = clock()
        else:
            # Observation surface hasn't changed since last_obs_check
            silence_duration = clock() - last_obs_check
            
            # AIPOS-316 S1.2: emit operational hint if observation surface never changed since startup
            if not stall_hint_emitted and current_obs_mtime == initial_obs_mtime and silence_duration >= stall_secs:
                hint_msg = (
                    "WARNING: Observation surface has not changed since watch started. "
                    "If the monitored process buffers output (e.g., pi), consider using "
                    "--worktree-path, --proc-pattern, or --session-dirs for more responsive monitoring."
                )
                print(hint_msg, file=sys.stderr)
                stall_hint_emitted = True
            
            if silence_duration >= stall_secs:
                tail = get_run_log_tail(run_log_path) if run_log_path else ""
                last_line = tail.strip().split("\n")[-1] if tail.strip() else ""
                if stream_mode:
                    # AIPOS-284C: emit stall event and continue, reset timer
                    print(json.dumps({"kind": "stall", "silence_seconds": int(silence_duration), "run_log_tail": last_line}, ensure_ascii=False), flush=True)
                    last_obs_check = clock()  # Reset to avoid repeated stall events
                else:
                    print(json.dumps({"stall": {"silence_seconds": int(silence_duration), "run_log_tail": last_line}}, ensure_ascii=False))
                    return EXIT_STALL
        
        # Bounded: stop before the next poll would exceed the timeout (the loop must end).
        # AIPOS-284D S2: infinite timeout (timeout == float('inf')) means never exit on timeout
        if not (timeout == float('inf')) and clock() - start + interval >= timeout:
            if stream_mode:
                # AIPOS-284D S2: emit 'end' event before exit
                print(json.dumps({"kind": "end", "reason": "timeout"}, ensure_ascii=False), flush=True)
            return EXIT_TIMEOUT  # silent in default mode
        sleeper(interval)
        if not stream_mode:
            prev = curr  # Default mode: advance snapshot each poll
        
        # Reset grace flag after one poll
        if grace_after_end:
            grace_after_end = False


def run_fs_watch_cli(args: Any) -> int:
    """CLI entry: installs clean SIGTERM/SIGINT handlers around the core loop (the
    card's 'SIGTERM 干净退出' — no Python traceback), then restores the prior handlers.
    Only valid in the main thread (where the CLI runs).
    
    AIPOS-284D S2: in --stream mode, emit 'kind:end' event before signal exit.
    """
    stream_mode = getattr(args, "stream", False)
    
    def _handler(signum: int, frame: Any) -> None:
        # AIPOS-284D S2: emit end event in stream mode before exit
        if stream_mode:
            try:
                print(json.dumps({"kind": "end", "reason": "signal"}, ensure_ascii=False), flush=True)
            except Exception:
                pass  # Best effort: don't crash if stdout is broken
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
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
