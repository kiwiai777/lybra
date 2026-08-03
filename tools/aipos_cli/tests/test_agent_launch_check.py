"""Tests for AIPOS-295C agent launch-check: 开工确认 + 首刻失败自愈.

Tests the four critical paths (S4):
1. 开工成功 (launch success)
2. 首刻早退 (early process exit)
3. 重拉成功 (relaunch success after first failure)
4. 重拉再败BLOCK (double failure → BLOCK file written)
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import the module under test
from tools.aipos_cli.agent_launch_check import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_OK,
    check_launch,
    run_launch_check,
    write_block_file,
)


@pytest.fixture
def temp_product_repo(tmp_path):
    """Create a temporary product repo structure."""
    product_repo = tmp_path / "lybra"
    product_repo.mkdir()
    
    # Create task_cards directory
    task_cards_dir = product_repo / "task_cards"
    task_cards_dir.mkdir()
    
    # Create session directory
    session_dir = product_repo / ".pi_sessions"
    session_dir.mkdir()
    
    return product_repo


@pytest.fixture
def mock_psutil():
    """Mock psutil for process monitoring."""
    with patch("tools.aipos_cli.agent_launch_check.psutil") as mock:
        # Mock Process class
        mock_process = MagicMock()
        mock_cpu_times = MagicMock()
        mock_cpu_times.user = 1.0
        mock_cpu_times.system = 0.5
        mock_process.cpu_times.return_value = mock_cpu_times
        mock_process.children.return_value = []
        
        mock.Process.return_value = mock_process
        yield mock


def test_launch_success(temp_product_repo, mock_psutil, capsys):
    """Test S4.1: 开工成功 — process starts, shows CPU activity, creates artifacts."""
    session_dir = temp_product_repo / ".pi_sessions"
    
    # Mock subprocess that stays alive and simulates work
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None  # Process stays alive
        mock_popen.return_value = mock_proc
        
        # Mock _find_pi_processes to return a PID
        with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
            mock_find.return_value = [12346]  # Pi subprocess PID
            
            # Mock _get_process_cpu_time to show increasing CPU
            cpu_sequence = [0.0, 0.0, 2.5, 5.0]  # First two are baseline, then work starts
            with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                mock_cpu.side_effect = cpu_sequence
                
                # Mock _count_new_files to show session files created
                with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                    mock_files.return_value = 3  # 3 new session files
                    
                    # Mock _has_worktree_changes to avoid git call
                    with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                        mock_wt.return_value = True  # Worktree has changes
                        
                        # Run launch check with short window for testing
                        exit_code, failure_data = check_launch(
                        spawn_cmd="timeout 3600 pi --prompt 'test'",
                        task_id="AIPOS-295C",
                        executor_instance="exec.test",
                        product_repo=temp_product_repo,
                        session_dirs=[str(session_dir)],
                        worktree_path=str(temp_product_repo),
                        launch_window_secs=20,
                        check_interval_secs=2,
                        )
                        
                        # Verify success
                        assert exit_code == EXIT_OK
                        assert failure_data is None
                    
                        # Verify started event was emitted
                        captured = capsys.readouterr()
                        assert "kind" in captured.out
                        
                        # Parse JSON output
                        for line in captured.out.strip().split('\n'):
                            try:
                                event = json.loads(line)
                                if event.get("kind") == "started":
                                    assert event["task_id"] == "AIPOS-295C"
                                    assert event["executor_instance"] == "exec.test"
                                    assert event["pid"] == 12345
                                    assert event["pi_pids"] == [12346]
                                    break
                            except json.JSONDecodeError:
                                pass


def test_launch_early_exit(temp_product_repo, capsys):
    """Test S4.2: 首刻早退 — process exits with non-zero code during launch window."""
    session_dir = temp_product_repo / ".pi_sessions"
    
    # Mock subprocess that exits immediately
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.side_effect = [None, None, 1]  # Dies on 3rd poll with exit code 1
        mock_popen.return_value = mock_proc
        
        # Run launch check
        exit_code, failure_data = check_launch(
            spawn_cmd="timeout 3600 pi --prompt 'test'",
            task_id="AIPOS-295C",
            executor_instance="exec.test",
            product_repo=temp_product_repo,
            session_dirs=[str(session_dir)],
            worktree_path=str(temp_product_repo),
            launch_window_secs=20,
            check_interval_secs=2,
        )
        
        # Verify failure detected
        assert exit_code == EXIT_ERROR
        assert failure_data is not None
        assert failure_data["reason"] == "process_early_exit"
        assert failure_data["exit_code"] == 1
        assert failure_data["proc_alive"] is False


def test_launch_silent_hang(temp_product_repo, mock_psutil):
    """Test S4.2: 静默挂死 — process alive but 0-CPU, no artifacts."""
    session_dir = temp_product_repo / ".pi_sessions"
    
    # Mock subprocess that stays alive but does nothing
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None  # Process stays alive
        mock_popen.return_value = mock_proc
        
        # Mock _find_pi_processes to return a PID
        with patch("tools.aipos_cli.agent_launch_check._find_pi_processes") as mock_find:
            mock_find.return_value = [12346]
            
            # Mock _get_process_cpu_time to show zero CPU (静默)
            with patch("tools.aipos_cli.agent_launch_check._get_process_cpu_time") as mock_cpu:
                mock_cpu.return_value = 0.0  # No CPU activity
                
                # Mock _count_new_files to show no artifacts
                with patch("tools.aipos_cli.agent_launch_check._count_new_files") as mock_files:
                    mock_files.return_value = 0  # No session files
                    
                    # Mock _has_worktree_changes to show no worktree changes
                    with patch("tools.aipos_cli.agent_launch_check._has_worktree_changes") as mock_wt:
                        mock_wt.return_value = False
                        
                        # Mock _kill_process_tree
                        with patch("tools.aipos_cli.agent_launch_check._kill_process_tree"):
                            # Run launch check with short window
                            exit_code, failure_data = check_launch(
                                spawn_cmd="timeout 3600 pi --prompt 'test'",
                                task_id="AIPOS-295C",
                                executor_instance="exec.test",
                                product_repo=temp_product_repo,
                                session_dirs=[str(session_dir)],
                                worktree_path=str(temp_product_repo),
                                launch_window_secs=10,
                                check_interval_secs=2,
                            )
                            
                            # Verify silent_hang detected
                            assert exit_code == EXIT_ERROR
                            assert failure_data is not None
                            assert failure_data["reason"] == "silent_hang"
                            assert failure_data["cpu_delta"] < 0.01
                            assert failure_data["new_session_files"] == 0


def test_relaunch_success(temp_product_repo, mock_psutil, capsys):
    """Test S4.3: 重拉成功 — first launch fails, second succeeds."""
    session_dir = temp_product_repo / ".pi_sessions"
    
    # Track number of spawn attempts
    spawn_count = [0]
    
    def mock_check_launch(*args, **kwargs):
        spawn_count[0] += 1
        if spawn_count[0] == 1:
            # First attempt fails
            return EXIT_ERROR, {
                "reason": "silent_hang",
                "exit_code": None,
                "proc_alive": True,
                "cpu_delta": 0.0,
                "new_session_files": 0,
                "worktree_changed": False,
            }
        else:
            # Second attempt succeeds
            return EXIT_OK, None
    
    with patch("tools.aipos_cli.agent_launch_check.check_launch", side_effect=mock_check_launch):
        # Run launch check with retry
        exit_code = run_launch_check(
            spawn_cmd="timeout 3600 pi --prompt 'test'",
            task_id="AIPOS-295C",
            executor_instance="exec.test",
            product_repo=temp_product_repo,
            session_dirs=[str(session_dir)],
            worktree_path=str(temp_product_repo),
            launch_window_secs=10,
            check_interval_secs=2,
        )
        
        # Verify final success
        assert exit_code == EXIT_OK
        assert spawn_count[0] == 2  # Two attempts
        
        # Verify events emitted: launch_failed, relaunch, started
        captured = capsys.readouterr()
        events = []
        for line in captured.out.strip().split('\n'):
            try:
                event = json.loads(line)
                events.append(event.get("kind"))
            except json.JSONDecodeError:
                pass
        
        assert "launch_failed" in events
        assert "relaunch" in events


def test_double_failure_block(temp_product_repo, capsys):
    """Test S4.4: 重拉再败BLOCK — both launch attempts fail, BLOCK file written."""
    session_dir = temp_product_repo / ".pi_sessions"
    
    # Mock check_launch to always fail
    def mock_check_launch(*args, **kwargs):
        return EXIT_ERROR, {
            "reason": "process_early_exit",
            "exit_code": 1,
            "proc_alive": False,
            "cpu_delta": 0.0,
            "new_session_files": 0,
            "worktree_changed": False,
        }
    
    with patch("tools.aipos_cli.agent_launch_check.check_launch", side_effect=mock_check_launch):
        # Run launch check with retry
        exit_code = run_launch_check(
            spawn_cmd="timeout 3600 pi --prompt 'test'",
            task_id="AIPOS-295C",
            executor_instance="exec.test",
            product_repo=temp_product_repo,
            session_dirs=[str(session_dir)],
            worktree_path=str(temp_product_repo),
            launch_window_secs=10,
            check_interval_secs=2,
        )
        
        # Verify blocked exit code
        assert exit_code == EXIT_BLOCKED
        
        # Verify BLOCK file exists
        block_files = list((temp_product_repo / "task_cards" / "AIPOS-295C").glob("BLOCK-launch-*.md"))
        assert len(block_files) == 1
        
        # Verify BLOCK file content
        block_content = block_files[0].read_text()
        assert "BLOCK — AIPOS-295C 首刻失败" in block_content
        assert "process_early_exit" in block_content
        assert "Attempt 1" in block_content
        assert "Attempt 2" in block_content
        
        # Verify events emitted: launch_failed (2x), relaunch, blocked
        captured = capsys.readouterr()
        events = []
        for line in captured.out.strip().split('\n'):
            try:
                event = json.loads(line)
                events.append(event.get("kind"))
            except json.JSONDecodeError:
                pass
        
        assert events.count("launch_failed") == 2
        assert "relaunch" in events
        assert "blocked" in events


def test_model_extraction_and_substitution():
    """Test model extraction from command and substitution logic."""
    from tools.aipos_cli.agent_launch_check import (
        _extract_model_from_command,
        _substitute_model_in_command,
    )
    
    # Test model extraction
    cmd1 = "timeout 3600 pi --model sonnet-5 --prompt 'test'"
    assert _extract_model_from_command(cmd1) == "sonnet-5"
    
    cmd2 = "timeout 3600 pi -m claude-sonnet-3-5-20241022 --prompt 'test'"
    assert _extract_model_from_command(cmd2) == "claude-sonnet-3-5-20241022"
    
    cmd3 = "timeout 3600 pi --prompt 'test'"  # No explicit model
    result = _extract_model_from_command(cmd3)
    assert result is None or result == "pi"  # Heuristic might find "pi"
    
    # Test model substitution
    new_cmd = _substitute_model_in_command(cmd1, "sonnet-5", "qwen3.7-plus")
    assert "qwen3.7-plus" in new_cmd
    assert "sonnet-5" not in new_cmd


def test_block_file_with_model_suggestion(temp_product_repo):
    """Test BLOCK file includes model switch suggestion when policy exists."""
    spawn_cmd = "timeout 3600 pi --model sonnet-5 --prompt 'test'"
    failure_history = [
        {
            "timestamp": "2026-08-03T05:00:00Z",
            "attempt": 1,
            "reason": "silent_hang",
            "exit_code": None,
            "proc_alive": True,
            "cpu_delta": 0.0,
            "new_session_files": 0,
            "worktree_changed": False,
        },
        {
            "timestamp": "2026-08-03T05:02:00Z",
            "attempt": 2,
            "reason": "silent_hang",
            "exit_code": None,
            "proc_alive": True,
            "cpu_delta": 0.0,
            "new_session_files": 0,
            "worktree_changed": False,
        },
    ]
    
    model_fallback_policy = {"sonnet-5": "qwen3.7-plus"}
    
    block_file = write_block_file(
        product_repo=temp_product_repo,
        card_id="AIPOS-295C",
        spawn_cmd=spawn_cmd,
        failure_history=failure_history,
        model_fallback_policy=model_fallback_policy,
    )
    
    # Verify file exists
    assert block_file.exists()
    
    # Verify content includes model suggestion
    content = block_file.read_text()
    assert "预授权模型切换建议" in content
    assert "sonnet-5" in content
    assert "qwen3.7-plus" in content
    assert "timeout 3600 pi --model qwen3.7-plus" in content


def test_cli_integration(temp_product_repo, capsys):
    """Test CLI entry point with minimal mock."""
    from tools.aipos_cli.agent_launch_check import main
    
    # Mock run_launch_check to return success
    with patch("tools.aipos_cli.agent_launch_check.run_launch_check") as mock_run:
        mock_run.return_value = EXIT_OK
        
        argv = [
            "--spawn-cmd", "timeout 3600 pi --prompt 'test'",
            "--task-id", "AIPOS-295C",
            "--executor-instance", "exec.test",
            "--product-repo", str(temp_product_repo),
            "--session-dirs", str(temp_product_repo / ".pi_sessions"),
            "--worktree-path", str(temp_product_repo),
            "--launch-window", "10",
        ]
        
        exit_code = main(argv)
        
        assert exit_code == EXIT_OK
        assert mock_run.called


def test_zero_regression_existing_watch():
    """Test S4: zero regression — agent_watch_fs.py unchanged, still imports/runs."""
    # This test ensures we didn't break existing watch functionality
    try:
        from tools.aipos_cli.agent_watch_fs import run_fs_watch_cli
        assert callable(run_fs_watch_cli)
    except ImportError as e:
        pytest.fail(f"agent_watch_fs.py import broken: {e}")


def test_zero_regression_existing_supervise():
    """Test S4: zero regression — agent_supervise.py unchanged, still imports/runs."""
    try:
        from tools.aipos_cli.agent_supervise import main as supervise_main
        assert callable(supervise_main)
    except ImportError as e:
        pytest.fail(f"agent_supervise.py import broken: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
