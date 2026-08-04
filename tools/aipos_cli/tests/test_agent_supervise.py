"""AIPOS-327F2 — Tests for safe kickoff transmission in agent_supervise.

Mirrors the launch-check kickoff-safety tests (AIPOS-327F1) for the supervise path.
AUDIT-REPORT-327F1R.md F-327F1R-01 found the supervise path was completely missed
by 327F1: it still used pi-unsupported `--prompt @tempfile` and silently fell back to
the unsafe command on failure. 327F2 applies the same fix as launch-check:

- Supervise extracts kickoff from `--append-system-prompt '...'` and, when it contains
  shell hazards (backticks, $(), ${}, newlines), writes it to a temp file and rewrites
  the spawn command to `--append-system-prompt @<file>` (pi-supported, no shell eval).
- On transmission failure, run_supervise returns EXIT_ERROR instead of falling back to
  the unsafe original command (F-327R-02).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.aipos_cli.agent_supervise import (
    EXIT_ERROR,
    EXIT_OK,
    run_supervise,
)


@pytest.fixture
def temp_product_repo(tmp_path):
    """Minimal product repo path required by run_supervise (cwd of spawned cmd)."""
    product_repo = tmp_path / "lybra"
    product_repo.mkdir()
    (product_repo / "task_cards").mkdir()
    return product_repo


def _supervise_kwargs(spawn_cmd, product_repo, **overrides):
    """Build minimal valid kwargs for run_supervise (no pid_file / patterns)."""
    base = dict(
        spawn_cmd=spawn_cmd,
        workspace_root=product_repo,
        product_repo=product_repo,
        card_id="AIPOS-327F2-TEST",
        health_interval=1.0,
        pid_file=None,
        proc_pattern=None,
        session_dirs=None,
        worktree_path=None,
        run_log=None,
        gate_client=None,
        actor="exec.test",
    )
    base.update(overrides)
    return base


def test_supervise_kickoff_transmission_failure_aborts(temp_product_repo):
    """AIPOS-327F2 F-327R-02: transmission failure must abort, not fall back.

    When kickoff contains hazards but the temp-file write fails, run_supervise must
    return EXIT_ERROR and MUST NOT spawn the unsafe original command.
    """
    spawn_cmd = (
        "timeout 3600 pi --append-system-prompt "
        "'kick `echo DANGER` $(whoami) ${HOME}' -p 'run'"
    )

    with patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("tools.aipos_cli.agent_supervise.subprocess.Popen") as mock_popen:
        mock_mkstemp.side_effect = OSError("Disk full")
        exit_code = run_supervise(**_supervise_kwargs(spawn_cmd, temp_product_repo))

    # Must abort with error ...
    assert exit_code == EXIT_ERROR
    # ... and must NOT have spawned the unsafe original command (no silent fallback)
    assert not mock_popen.called, "supervise fell back to unsafe spawn_cmd"


def _make_proc_pair():
    """A target proc that exits cleanly + a watch proc, for driving run_supervise.

    Target poll() -> 0 makes the monitor loop break immediately on the
    'target process died' check, yielding a clean EXIT_OK without exercising
    health monitoring.
    """
    target = MagicMock()
    target.pid = 12345
    target.poll.return_value = 0  # already exited -> clean exit path
    target.returncode = 0
    watch = MagicMock()
    watch.pid = 99999
    watch.poll.return_value = None
    watch.stdout = MagicMock()
    return target, watch


def test_supervise_hazardous_kickoff_moves_to_append_system_prompt_file(temp_product_repo):
    """AIPOS-327F2 F-327R-01: hazardous kickoff is rewritten to @file.

    The spawned command must use `--append-system-prompt @<file>` and the hazards
    (backticks, $(), ${}) must NOT appear inline in the command actually executed.
    """
    hazards = "do `echo PWNED_327F2` then $(whoami) then ${HOME}"
    spawn_cmd = f"timeout 3600 pi --append-system-prompt '{hazards}' -p 'run'"

    target, watch = _make_proc_pair()
    with patch("tools.aipos_cli.agent_supervise.subprocess.Popen",
               side_effect=[target, watch]) as mock_popen, \
         patch("tools.aipos_cli.agent_supervise.kill_process_tree"), \
         patch("tools.aipos_cli.agent_supervise.time.sleep"):
        exit_code = run_supervise(**_supervise_kwargs(spawn_cmd, temp_product_repo))

    assert exit_code == EXIT_OK
    # First Popen call is the target spawn; its command must use @file
    actual_cmd = mock_popen.call_args_list[0].args[0]
    assert "--append-system-prompt @" in actual_cmd
    assert "lybra_kickoff_" in actual_cmd  # our temp-file prefix
    # Hazards must NOT be shell-evaluated inline in the spawned command
    assert "`echo PWNED_327F2`" not in actual_cmd
    assert "$(whoami)" not in actual_cmd
    assert "${HOME}" not in actual_cmd


def test_supervise_no_hazards_passthrough(temp_product_repo):
    """AIPOS-327F2: kickoff without hazards passes through unchanged (no @file)."""
    spawn_cmd = "timeout 3600 pi --append-system-prompt 'safe kickoff text' -p 'run'"

    target, watch = _make_proc_pair()
    with patch("tools.aipos_cli.agent_supervise.subprocess.Popen",
               side_effect=[target, watch]) as mock_popen, \
         patch("tools.aipos_cli.agent_supervise.kill_process_tree"), \
         patch("tools.aipos_cli.agent_supervise.time.sleep"):
        exit_code = run_supervise(**_supervise_kwargs(spawn_cmd, temp_product_repo))

    assert exit_code == EXIT_OK
    # Original command used as-is, no temp-file substitution
    assert mock_popen.call_args_list[0].args[0] == spawn_cmd
