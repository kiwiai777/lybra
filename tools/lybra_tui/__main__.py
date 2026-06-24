"""AIPOS-205 — TUI entry. Invoked by `lybra tui` (via aipos_cli, lazy-imported).

Textual is required only here / in app.py (the `tui` extra). The gate and the rest of
the CLI never import this module at top level, so they stay stdlib/zero-dependency.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tools.lybra_tui.state import TuiSession

# Default location serve-rotate writes the local connection config (0600).
CONNECTION_REL = ".lybra/local/connection.json"


def default_connection_json(workspace_root: str | None) -> str | None:
    """AIPOS-209: resolve the serve-rotate connection.json under the workspace, if present.

    Minimal convenience so `lybra tui` after `lybra serve` needs no explicit token source.
    Returns the path string if the file exists, else None. Reads no token (path only).
    """
    base = Path(workspace_root).expanduser() if workspace_root else Path.cwd()
    candidate = base / CONNECTION_REL
    return str(candidate) if candidate.is_file() else None


def _maybe_build_copilot(
    *,
    gate_url: str,
    connection_json: str | None,
    project: str | None,
    llm_base_url: str | None,
    llm_key_env: str | None,
    llm_model: str | None,
):
    """Build a read-only CopilotSession iff an LLM config is supplied. Returns None otherwise.

    The copilot uses the read-only `copilot` service role (scopes []); its api key is read
    from an env var (never the command line) and held fingerprint-only.
    """
    if not (llm_base_url and llm_key_env and project):
        return None
    api_key = os.environ.get(llm_key_env, "").strip()
    if not api_key:
        print(f"lybra tui: copilot disabled — {llm_key_env} is empty/unset", file=sys.stderr)
        return None
    from tools.lybra_tui.copilot import CopilotSession, LLMClient, LLMConfig

    config = LLMConfig(base_url=llm_base_url, api_key=api_key, model=llm_model or "gpt-4o-mini")
    return CopilotSession.connect(
        gate_url, llm=LLMClient(config), project=project, connection_json=connection_json, role="copilot"
    )


def run_tui(
    *,
    gate_url: str,
    connection_json: str | None = None,
    token_env: str | None = None,
    role: str = "owner",
    workspace_root: str | None = None,
    project: str | None = None,
    llm_base_url: str | None = None,
    llm_key_env: str | None = None,
    llm_model: str | None = None,
) -> int:
    # AIPOS-209: if no explicit token source is given, fall back to the serve-rotate
    # connection.json under the workspace (Owner ran `lybra serve` first).
    if connection_json is None and token_env is None:
        connection_json = default_connection_json(workspace_root)
        if connection_json is None:
            print(
                "lybra tui: no token source. Run `lybra serve` first (writes "
                f"{CONNECTION_REL}), or pass --connection-json / --token-env.",
                file=sys.stderr,
            )
            return 2
    try:
        session = TuiSession.connect(
            gate_url, connection_json=connection_json, token_env=token_env, role=role
        )
        copilot = _maybe_build_copilot(
            gate_url=gate_url,
            connection_json=connection_json,
            project=project,
            llm_base_url=llm_base_url,
            llm_key_env=llm_key_env,
            llm_model=llm_model,
        )
    except (ValueError, OSError) as exc:
        print(f"lybra tui: could not connect: {exc}", file=sys.stderr)
        return 2
    # AIPOS-208: when the copilot is enabled, the first screen IS chat-to-task (DG-8); Shift+Tab
    # still cycles to observe/confirm. Without an LLM config we stay on the observe first screen.
    if copilot is not None:
        from tools.lybra_tui.state import COPILOT_MODE
        session.mode = COPILOT_MODE
    try:
        from tools.lybra_tui.app import build_app  # Textual import isolated here
    except ImportError:
        print(
            "lybra tui requires Textual. Install it with: pip install textual "
            "(lybra is npm-distributed, not on PyPI; see README Quick start).",
            file=sys.stderr,
        )
        return 2
    build_app(session, copilot, workspace_root=workspace_root).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="lybra tui", description="Lybra TUI client over an Owner-started gate.")
    parser.add_argument("--gate-url", required=True)
    # AIPOS-209: token source optional — defaults to the serve-rotate connection.json
    # under the workspace when neither is given.
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument("--connection-json")
    src.add_argument("--token-env")
    parser.add_argument("--role", default="owner")
    parser.add_argument("--workspace-root", help="workspace root used to land copilot DRAFTs under 5_tasks/drafts/")
    parser.add_argument("--project", help="project the copilot session is scoped to (R4, single-project)")
    parser.add_argument("--llm-base-url", help="OpenAI-compatible base URL; enables the read-only planning copilot")
    parser.add_argument("--llm-key-env", help="env var holding the LLM api key (never passed on the command line)")
    parser.add_argument("--llm-model", help="LLM model id (default gpt-4o-mini)")
    args = parser.parse_args(argv)
    return run_tui(
        gate_url=args.gate_url,
        connection_json=args.connection_json,
        token_env=args.token_env,
        role=args.role,
        workspace_root=args.workspace_root,
        project=args.project,
        llm_base_url=args.llm_base_url,
        llm_key_env=args.llm_key_env,
        llm_model=args.llm_model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
