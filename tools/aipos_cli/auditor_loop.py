"""AIPOS-358 — Auditor thin shell (守护薄壳).

The auditor daemon is now a thin shell: it does NOT make any decisions about
what to do next. It simply calls ``turn-advancer scan --mode auto`` on a timer.
All decision logic (which cards to process, verdict checking, skip/block logic)
lives in the turn_advancer rules/state_reader (AIPOS-340) and gate (AIPOS-354).

Previous private decision functions (find_pending_audit_cards, check_verdict_landed,
process_pending_audits, etc.) have been retired — their logic is now expressed
in 340 rules data.

Systemd ExecStart points here. The daemon never exits due to business results
(a single card failure = event + card state, handled by 340; the daemon keeps running).
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def log(msg: str) -> None:
    """Timestamped log to stderr."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[auditor-shell {timestamp}] {msg}", file=sys.stderr)


def run_daemon(
    workspace_root: Path,
    interval: float = 20.0,
) -> int:
    """Thin shell daemon loop. Never exits due to business results.

    Calls ``turn-advancer scan --workspace-root <ws> --mode auto`` every interval.
    Returns only on KeyboardInterrupt or fatal startup error.
    """
    cmd = [
        sys.executable, "-m", "tools.aipos_cli.aipos_cli",
        "turn-advancer", "scan",
        "--workspace-root", str(workspace_root),
        "--mode", "auto",
    ]
    log(f"start ws={workspace_root} interval={interval}s")

    while True:
        try:
            result = subprocess.run(cmd, capture_output=False)
            if result.returncode != 0:
                log(f"scan exit={result.returncode} (continuing, daemon never exits)")
        except KeyboardInterrupt:
            log("收到中断信号, 退出")
            return 130
        except Exception as exc:
            log(f"scan error: {exc} (continuing)")

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log("收到中断信号, 退出")
            return 130


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `lybra auditor loop` (AIPOS-358 thin shell)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="lybra auditor loop",
        description="AIPOS-358: Auditor thin shell daemon. "
                    "Calls turn-advancer scan --mode auto on a timer. "
                    "Never exits due to business results.",
    )
    parser.add_argument(
        "--workspace-root", required=True, type=Path,
        help="Lybra workspace root (治理仓)",
    )
    parser.add_argument(
        "--interval", type=float, default=20.0,
        help="Scan interval seconds (default: 20)",
    )
    # Retained args for backward compat (systemd unit may pass them); ignored.
    parser.add_argument("--product-repo", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--gate-url", help=argparse.SUPPRESS)
    parser.add_argument("--connection-json", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--auditor-instance", help=argparse.SUPPRESS)
    parser.add_argument("--policy", "--envelope", dest="envelope", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-cmd", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--claim-transient-tries", type=int, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    workspace_root = args.workspace_root.expanduser().resolve()

    if not workspace_root.is_dir():
        print(f"ERROR: workspace-root does not exist: {workspace_root}", file=sys.stderr)
        return 1

    try:
        return run_daemon(workspace_root, interval=args.interval)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
