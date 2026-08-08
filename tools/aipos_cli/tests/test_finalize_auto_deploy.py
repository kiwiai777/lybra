"""Integration tests for AIPOS-FND-9 finalize auto-deploy."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from tools.aipos_cli.finalize import finalize_task


@pytest.fixture
def mock_git_setup(tmp_path):
    """Setup a mock git repository structure."""
    # Create task_cards structure (display-only report; NOT judged by finalize anymore)
    task_dir = tmp_path / "task_cards" / "TEST-TASK"
    task_dir.mkdir(parents=True)
    audit_report = task_dir / "AUDIT-REPORT-001.md"
    audit_report.write_text("# Audit Report\n(no frontmatter, as real reports ship today)\n")

    # AIPOS-FND-14: finalize eligibility now reads the AUTHORITATIVE gate audit_verdict_record
    # under governance_root/5_tasks/records/audit_verdicts/<task_id>/*.md, not the report above.
    # This fixture uses tmp_path as BOTH governance_root and workspace_root (single-root test
    # setup), so the verdict record lives under tmp_path/5_tasks/records/audit_verdicts/.
    verdicts_dir = tmp_path / "5_tasks" / "records" / "audit_verdicts" / "TEST-TASK"
    verdicts_dir.mkdir(parents=True)
    verdict_record = verdicts_dir / "verdict_TEST-TASK_20260101_000000_audit-test.md"
    verdict_record.write_text(
        "---\n"
        "record_type: audit_verdict_record\n"
        "verdict: PASS\n"
        "reviewed_task_id: TEST-TASK\n"
        "verdict_at: '2026-01-01T00:00:00Z'\n"
        "---\n"
        "# MCP Audit Verdict Record\n"
    )

    # Create .deploy structure
    deploy_dir = tmp_path / ".deploy"
    current_dir = deploy_dir / "releases" / "20260808_120000-abc1234"
    current_dir.mkdir(parents=True)
    
    version_file = current_dir / "VERSION"
    version_file.write_text(
        "git_commit: abc1234567890abcdef1234567890abcdef1234\n"
    )
    
    current_link = deploy_dir / "current"
    current_link.symlink_to(current_dir)
    
    # Create lybra-deploy script
    deploy_script = tmp_path / "tools" / "lybra-deploy"
    deploy_script.parent.mkdir(parents=True)
    deploy_script.write_text("#!/bin/bash\necho 'Deployed successfully'\n")
    deploy_script.chmod(0o755)
    
    return tmp_path


@patch("tools.aipos_cli.finalize.subprocess.run")
def test_finalize_auto_deploy_gate_side_changes(mock_subprocess, mock_git_setup):
    """Test that finalize auto-deploys when gate-side changes are detected."""
    workspace_root = mock_git_setup
    
    # Mock git commands
    def subprocess_side_effect(*args, **kwargs):
        cmd = args[0]
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        
        if cmd == ["git", "rev-parse", "HEAD"]:
            # Before commit: HEAD matches deployed (integrity OK)
            # After commit: HEAD advances (drift detected)
            if mock_subprocess.call_count <= 3:  # First few calls (integrity check)
                result.stdout = "abc1234567890abcdef1234567890abcdef1234"
            else:  # After commit
                result.stdout = "new1234567890abcdef1234567890abcdef1234"
        elif cmd == ["git", "status", "--porcelain"]:
            # Has changes to commit
            result.stdout = "M tools/mcp_server/tools.py\n"
        elif cmd[0:2] == ["git", "add"]:
            pass  # Success
        elif cmd[0:2] == ["git", "commit"] or (len(cmd) > 2 and cmd[2] == "commit"):
            result.stdout = "Committed\n"
        elif cmd == ["git", "rev-list", "--count", "abc1234567890abcdef1234567890abcdef1234..new1234567890abcdef1234567890abcdef1234"]:
            result.stdout = "1"
        elif cmd[0:2] == ["git", "log"]:
            result.stdout = "new1234 feat: gate change\n"
        elif cmd[0:2] == ["git", "diff"]:
            result.stdout = "tools/mcp_server/tools.py\n"
        elif str(workspace_root / "tools" / "lybra-deploy") in str(cmd):
            # Mock deployment
            result.stdout = "[lybra-deploy] Deployed successfully\n"
        
        return result
    
    mock_subprocess.side_effect = subprocess_side_effect
    
    # Run finalize
    result = finalize_task(
        task_id="TEST-TASK",
        actor="test-actor",
        workspace_root=workspace_root,
        governance_root=workspace_root,
        dry_run=False,
        push=False,
    )
    
    # Verify finalize succeeded
    assert result["verdict"] == "PASS"
    assert result["committed"] is True
    
    # Verify auto-deploy was triggered
    assert result["deployed"] is True
    assert result["deployment_skipped"] is False
    assert result["deployment_error"] is None
    
    # Verify operations include deployment
    operations = result["operations"]
    assert any("auto-deploy" in op.lower() for op in operations)
    assert any("deployed successfully" in op.lower() for op in operations)


@patch("tools.aipos_cli.finalize.subprocess.run")
def test_finalize_skip_deploy_cli_side_only(mock_subprocess, mock_git_setup):
    """Test that finalize skips deployment for CLI-side only changes."""
    workspace_root = mock_git_setup
    
    # Mock git commands - CLI-side changes only
    def subprocess_side_effect(*args, **kwargs):
        cmd = args[0]
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        
        if cmd == ["git", "rev-parse", "HEAD"]:
            # Before commit: matches deployed; after: advanced
            if mock_subprocess.call_count <= 3:
                result.stdout = "abc1234567890abcdef1234567890abcdef1234"
            else:
                result.stdout = "new1234567890abcdef1234567890abcdef1234"
        elif cmd == ["git", "status", "--porcelain"]:
            result.stdout = "M tools/aipos_cli/finalize.py\n"
        elif cmd[0:2] == ["git", "add"]:
            pass
        elif cmd[0:2] == ["git", "commit"] or (len(cmd) > 2 and cmd[2] == "commit"):
            result.stdout = "Committed\n"
        elif cmd == ["git", "rev-list", "--count", "abc1234567890abcdef1234567890abcdef1234..new1234567890abcdef1234567890abcdef1234"]:
            result.stdout = "1"
        elif cmd[0:2] == ["git", "log"]:
            result.stdout = "new1234 feat: CLI change\n"
        elif cmd[0:2] == ["git", "diff"]:
            result.stdout = "tools/aipos_cli/finalize.py\n"
        
        return result
    
    mock_subprocess.side_effect = subprocess_side_effect
    
    # Run finalize
    result = finalize_task(
        task_id="TEST-TASK",
        actor="test-actor",
        workspace_root=workspace_root,
        governance_root=workspace_root,
        dry_run=False,
        push=False,
    )
    
    # Verify finalize succeeded
    assert result["verdict"] == "PASS"
    assert result["committed"] is True
    
    # Verify deployment was skipped (CLI-side only)
    assert result["deployed"] is False
    assert result["deployment_skipped"] is True
    assert result["deployment_error"] is None
    
    # Verify operations show deployment skipped
    operations = result["operations"]
    assert any("CLI-side" in op for op in operations)


@patch("tools.aipos_cli.finalize.subprocess.run")
def test_finalize_deploy_failure_does_not_block_finalize(mock_subprocess, mock_git_setup):
    """Test that deployment failure doesn't block finalize (commit still succeeds)."""
    workspace_root = mock_git_setup
    
    # Mock git commands with deployment failure
    def subprocess_side_effect(*args, **kwargs):
        cmd = args[0]
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        
        if cmd == ["git", "rev-parse", "HEAD"]:
            if mock_subprocess.call_count <= 3:
                result.stdout = "abc1234567890abcdef1234567890abcdef1234"
            else:
                result.stdout = "new1234567890abcdef1234567890abcdef1234"
        elif cmd == ["git", "status", "--porcelain"]:
            result.stdout = "M tools/mcp_server/tools.py\n"
        elif cmd[0:2] == ["git", "add"]:
            pass
        elif cmd[0:2] == ["git", "commit"] or (len(cmd) > 2 and cmd[2] == "commit"):
            result.stdout = "Committed\n"
        elif cmd == ["git", "rev-list", "--count", "abc1234567890abcdef1234567890abcdef1234..new1234567890abcdef1234567890abcdef1234"]:
            result.stdout = "1"
        elif cmd[0:2] == ["git", "log"]:
            result.stdout = "new1234 feat: gate change\n"
        elif cmd[0:2] == ["git", "diff"]:
            result.stdout = "tools/mcp_server/tools.py\n"
        elif str(workspace_root / "tools" / "lybra-deploy") in str(cmd):
            # Mock deployment failure
            result.returncode = 1
            result.stderr = "Deployment validation failed"
            raise subprocess.CalledProcessError(1, cmd, stderr="Deployment validation failed")
        
        return result
    
    mock_subprocess.side_effect = subprocess_side_effect
    
    # Run finalize
    result = finalize_task(
        task_id="TEST-TASK",
        actor="test-actor",
        workspace_root=workspace_root,
        governance_root=workspace_root,
        dry_run=False,
        push=False,
    )
    
    # Verify finalize succeeded (commit went through)
    assert result["verdict"] == "PASS"
    assert result["committed"] is True
    
    # Verify deployment failed but was recorded
    assert result["deployed"] is False
    assert result["deployment_skipped"] is False
    assert result["deployment_error"] is not None
    assert "Deployment validation failed" in result["deployment_error"]
    
    # Verify operations show deployment failure
    operations = result["operations"]
    assert any("Deployment FAILED" in op for op in operations)


@patch("tools.aipos_cli.finalize.subprocess.run")
def test_finalize_no_drift_no_deploy(mock_subprocess, mock_git_setup):
    """Test that finalize doesn't deploy when there's no drift."""
    workspace_root = mock_git_setup
    
    # Mock git commands - no drift (HEAD == deployed)
    def subprocess_side_effect(*args, **kwargs):
        cmd = args[0]
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        
        if cmd == ["git", "rev-parse", "HEAD"]:
            # Same as deployed
            result.stdout = "abc1234567890abcdef1234567890abcdef1234"
        elif cmd == ["git", "status", "--porcelain"]:
            result.stdout = "M tools/mcp_server/tools.py\n"
        elif cmd[0:2] == ["git", "add"]:
            pass
        elif cmd[0:2] == ["git", "commit"] or (len(cmd) > 2 and cmd[2] == "commit"):
            result.stdout = "Committed\n"
        
        return result
    
    mock_subprocess.side_effect = subprocess_side_effect
    
    # Run finalize
    result = finalize_task(
        task_id="TEST-TASK",
        actor="test-actor",
        workspace_root=workspace_root,
        governance_root=workspace_root,
        dry_run=False,
        push=False,
    )
    
    # Verify finalize succeeded
    assert result["verdict"] == "PASS"
    assert result["committed"] is True
    
    # Verify deployment was skipped (no drift)
    assert result["deployed"] is False
    assert result["deployment_skipped"] is True
    
    # Verify operations show no drift
    operations = result["operations"]
    assert any("up-to-date" in op.lower() for op in operations)
