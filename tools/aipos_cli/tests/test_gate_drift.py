"""Unit tests for AIPOS-FND-9 gate drift detection."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.aipos_cli.gate_drift import (
    GATE_SIDE_PATHS,
    CLI_SIDE_PATHS,
    _classify_changed_paths,
    _read_deployed_commit,
    check_gate_drift,
)


def test_classify_changed_paths_gate_side():
    """Test classification of gate-side changes."""
    changed_paths = [
        "tools/mcp_server/tools.py",
        "tools/turn_advancer/command_builder.py",
        "config/settings.yaml",
    ]
    
    result = _classify_changed_paths(changed_paths)
    
    assert result["has_gate_side_changes"] is True
    assert len(result["gate_side"]) == 3
    assert len(result["cli_side"]) == 0
    assert "tools/mcp_server/tools.py" in result["gate_side"]


def test_classify_changed_paths_cli_side():
    """Test classification of CLI-side changes."""
    changed_paths = [
        "tools/aipos_cli/finalize.py",
        "tools/lybra_tui/main.py",
        "bin/lybra",
    ]
    
    result = _classify_changed_paths(changed_paths)
    
    assert result["has_gate_side_changes"] is False
    assert len(result["gate_side"]) == 0
    assert len(result["cli_side"]) == 3
    assert "tools/aipos_cli/finalize.py" in result["cli_side"]


def test_classify_changed_paths_mixed():
    """Test classification of mixed changes."""
    changed_paths = [
        "tools/mcp_server/tools.py",  # gate-side
        "tools/aipos_cli/finalize.py",  # cli-side
        "README.md",  # other
    ]
    
    result = _classify_changed_paths(changed_paths)
    
    assert result["has_gate_side_changes"] is True
    assert len(result["gate_side"]) == 1
    assert len(result["cli_side"]) == 1
    assert len(result["other"]) == 1


def test_read_deployed_commit_no_deployment(tmp_path):
    """Test reading deployed commit when no deployment exists."""
    result = _read_deployed_commit(tmp_path)
    assert result is None


def test_read_deployed_commit_success(tmp_path):
    """Test reading deployed commit from VERSION file."""
    deploy_dir = tmp_path / ".deploy"
    current_dir = deploy_dir / "releases" / "20260808_120000-abc1234"
    current_dir.mkdir(parents=True)
    
    version_file = current_dir / "VERSION"
    version_file.write_text(
        "release: 20260808_120000-abc1234\n"
        "git_commit: abc1234567890abcdef1234567890abcdef1234\n"
        "git_short: abc1234\n"
    )
    
    current_link = deploy_dir / "current"
    current_link.symlink_to(current_dir)
    
    result = _read_deployed_commit(tmp_path)
    assert result == "abc1234567890abcdef1234567890abcdef1234"


@patch("tools.aipos_cli.gate_drift._git_rev_parse")
def test_check_gate_drift_no_git(mock_git_rev_parse, tmp_path):
    """Test drift check when git is not available."""
    mock_git_rev_parse.return_value = ""
    
    result = check_gate_drift(tmp_path)
    
    assert result["has_drift"] is False
    assert "Unable to read git HEAD" in result["message"]


@patch("tools.aipos_cli.gate_drift._git_rev_parse")
@patch("tools.aipos_cli.gate_drift._read_deployed_commit")
def test_check_gate_drift_no_deployment(mock_read_deployed, mock_git_rev_parse, tmp_path):
    """Test drift check when no deployment exists."""
    mock_git_rev_parse.return_value = "abc1234567890"
    mock_read_deployed.return_value = None
    
    result = check_gate_drift(tmp_path)
    
    assert result["has_drift"] is False
    assert "No deployment found" in result["message"]


@patch("tools.aipos_cli.gate_drift._git_rev_parse")
@patch("tools.aipos_cli.gate_drift._read_deployed_commit")
def test_check_gate_drift_no_drift(mock_read_deployed, mock_git_rev_parse, tmp_path):
    """Test drift check when deployment is up-to-date."""
    commit_hash = "abc1234567890abcdef1234567890abcdef1234"
    mock_git_rev_parse.return_value = commit_hash
    mock_read_deployed.return_value = commit_hash
    
    result = check_gate_drift(tmp_path)
    
    assert result["has_drift"] is False
    assert result["deployed_commit"] == commit_hash
    assert result["head_commit"] == commit_hash
    assert result["commits_ahead"] == 0


@patch("tools.aipos_cli.gate_drift._git_rev_parse")
@patch("tools.aipos_cli.gate_drift._read_deployed_commit")
@patch("tools.aipos_cli.gate_drift._git_log_count")
@patch("tools.aipos_cli.gate_drift._git_log_commits")
@patch("tools.aipos_cli.gate_drift._git_diff_name_only")
def test_check_gate_drift_with_gate_side_changes(
    mock_diff, mock_log_commits, mock_log_count, mock_read_deployed, mock_git_rev_parse, tmp_path
):
    """Test drift check with gate-side changes."""
    deployed_commit = "old1234567890abcdef1234567890abcdef1234"
    head_commit = "new1234567890abcdef1234567890abcdef1234"
    
    mock_git_rev_parse.return_value = head_commit
    mock_read_deployed.return_value = deployed_commit
    mock_log_count.return_value = 2
    mock_log_commits.return_value = [
        {"hash": "new1234", "message": "feat: add feature"},
        {"hash": "mid1234", "message": "fix: bug fix"},
    ]
    mock_diff.return_value = [
        "tools/mcp_server/tools.py",
        "tools/turn_advancer/command_builder.py",
    ]
    
    result = check_gate_drift(tmp_path)
    
    assert result["has_drift"] is True
    assert result["deployed_commit"] == deployed_commit
    assert result["head_commit"] == head_commit
    assert result["commits_ahead"] == 2
    assert result["classification"]["has_gate_side_changes"] is True
    assert len(result["classification"]["gate_side"]) == 2
    assert "Run 'lybra-deploy'" in result["recommendation"]


@patch("tools.aipos_cli.gate_drift._git_rev_parse")
@patch("tools.aipos_cli.gate_drift._read_deployed_commit")
@patch("tools.aipos_cli.gate_drift._git_log_count")
@patch("tools.aipos_cli.gate_drift._git_log_commits")
@patch("tools.aipos_cli.gate_drift._git_diff_name_only")
def test_check_gate_drift_cli_side_only(
    mock_diff, mock_log_commits, mock_log_count, mock_read_deployed, mock_git_rev_parse, tmp_path
):
    """Test drift check with CLI-side changes only."""
    deployed_commit = "old1234567890abcdef1234567890abcdef1234"
    head_commit = "new1234567890abcdef1234567890abcdef1234"
    
    mock_git_rev_parse.return_value = head_commit
    mock_read_deployed.return_value = deployed_commit
    mock_log_count.return_value = 1
    mock_log_commits.return_value = [
        {"hash": "new1234", "message": "feat: CLI update"},
    ]
    mock_diff.return_value = [
        "tools/aipos_cli/finalize.py",
    ]
    
    result = check_gate_drift(tmp_path)
    
    assert result["has_drift"] is True
    assert result["classification"]["has_gate_side_changes"] is False
    assert len(result["classification"]["cli_side"]) == 1
    assert "CLI-side only" in result["recommendation"]
