"""AIPOS-205 — TUI entry. Invoked by `lybra tui` (via aipos_cli, lazy-imported).

Textual is required only here / in app.py (the `tui` extra). The gate and the rest of
the CLI never import this module at top level, so they stay stdlib/zero-dependency.
"""

from __future__ import annotations

import sys

from tools.lybra_tui.state import TuiSession


def run_tui(
    *,
    gate_url: str,
    connection_json: str | None = None,
    token_env: str | None = None,
    role: str = "owner",
) -> int:
    try:
        session = TuiSession.connect(
            gate_url, connection_json=connection_json, token_env=token_env, role=role
        )
    except (ValueError, OSError) as exc:
        print(f"lybra tui: could not connect: {exc}", file=sys.stderr)
        return 2
    try:
        from tools.lybra_tui.app import build_app  # Textual import isolated here
    except ImportError:
        print("lybra tui requires the TUI extra. Install with: pip install lybra[tui]", file=sys.stderr)
        return 2
    build_app(session).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="lybra tui", description="Lybra TUI client over an Owner-started gate.")
    parser.add_argument("--gate-url", required=True)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--connection-json")
    src.add_argument("--token-env")
    parser.add_argument("--role", default="owner")
    args = parser.parse_args(argv)
    return run_tui(gate_url=args.gate_url, connection_json=args.connection_json, token_env=args.token_env, role=args.role)


if __name__ == "__main__":
    raise SystemExit(main())
