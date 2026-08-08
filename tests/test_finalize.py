"""AIPOS-FND-2 — finalize 命令测试

测试 finalize 命令的核心逻辑：
1. 仅 PASS 可 finalize（非 PASS 拒绝）
2. 部署完整性检查（current==HEAD）
3. git commit/push 操作
4. dry-run 模式
"""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from tools.aipos_cli.finalize import (
    _check_deployment_integrity,
    _git_rev_parse_head,
    _git_status_clean,
    check_task_can_finalize,
    finalize_task,
)


@pytest.fixture
def temp_repo():
    """Create a temporary git repo for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "test_repo"
        repo_path.mkdir()
        
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.local"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
        
        # Create task_cards directory
        task_cards_dir = repo_path / "task_cards"
        task_cards_dir.mkdir()
        
        # Initial commit
        (repo_path / "README.md").write_text("# Test Repo\n")
        subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
        
        yield repo_path


def test_git_rev_parse_head(temp_repo):
    """Test getting current git HEAD."""
    commit_hash = _git_rev_parse_head(temp_repo)
    assert commit_hash
    assert len(commit_hash) == 40  # Full SHA-1 hash


def test_git_status_clean(temp_repo):
    """Test checking if working tree is clean."""
    # Initially clean
    assert _git_status_clean(temp_repo)
    
    # Add a file
    (temp_repo / "new_file.txt").write_text("test")
    assert not _git_status_clean(temp_repo)
    
    # Clean up
    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
    )
    assert _git_status_clean(temp_repo)


def test_check_deployment_integrity_no_deployment(temp_repo):
    """Test deployment integrity check when no deployment exists."""
    result = _check_deployment_integrity(temp_repo)
    assert result["integrity_ok"] is True
    assert result["current_commit"] is None
    assert "No .deploy/current" in result["message"]


def test_check_deployment_integrity_matching(temp_repo):
    """Test deployment integrity check when current==HEAD."""
    # Create mock deployment
    deploy_dir = temp_repo / ".deploy"
    deploy_dir.mkdir()
    current_dir = deploy_dir / "releases" / "release1"
    current_dir.mkdir(parents=True)
    current_link = deploy_dir / "current"
    current_link.symlink_to(current_dir)
    
    # Write VERSION file
    head_commit = _git_rev_parse_head(temp_repo)
    version_file = current_dir / "VERSION"
    version_file.write_text(f"git_commit: {head_commit}\n")
    
    result = _check_deployment_integrity(temp_repo)
    assert result["integrity_ok"] is True
    assert result["current_commit"] == head_commit
    assert result["head_commit"] == head_commit


def test_check_deployment_integrity_drift(temp_repo):
    """Test deployment integrity check when current!=HEAD."""
    # Create mock deployment with old commit
    deploy_dir = temp_repo / ".deploy"
    deploy_dir.mkdir()
    current_dir = deploy_dir / "releases" / "release1"
    current_dir.mkdir(parents=True)
    current_link = deploy_dir / "current"
    current_link.symlink_to(current_dir)
    
    old_commit = "0" * 40
    version_file = current_dir / "VERSION"
    version_file.write_text(f"git_commit: {old_commit}\n")
    
    result = _check_deployment_integrity(temp_repo)
    assert result["integrity_ok"] is False
    assert "DRIFT" in result["message"]


def test_check_task_can_finalize_no_task_dir(temp_repo):
    """Test finalize check when task directory doesn't exist."""
    result = check_task_can_finalize("AIPOS-999", temp_repo)
    assert result["can_finalize"] is False
    assert "not found" in result["reason"]


def test_check_task_can_finalize_no_audit_report(temp_repo):
    """Test finalize check when no audit report exists."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is False
    assert "No audit report" in result["reason"]


def test_check_task_can_finalize_pass_verdict(temp_repo):
    """Test finalize check with PASS verdict."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: PASS
---

# Audit Report

Test passed.
""")
    
    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is True
    assert result["verdict"] == "PASS"


def test_check_task_can_finalize_fail_verdict(temp_repo):
    """Test finalize check with FAIL verdict."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with FAIL verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: FAIL
---

# Audit Report

Test failed.
""")
    
    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is False
    assert result["verdict"] == "FAIL"
    assert "not PASS" in result["reason"]


def test_finalize_task_non_pass_blocked(temp_repo):
    """Test finalize blocks on non-PASS task."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with FAIL verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: FAIL
---

# Audit Report

Test failed.
""")
    
    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        dry_run=False,
    )
    
    assert result["verdict"] == "BLOCK"
    assert result["can_finalize"] is False
    assert result["committed"] is False


def test_finalize_task_clean_working_tree(temp_repo):
    """Test finalize with clean working tree (no changes to commit)."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: PASS
---

# Audit Report

Test passed.
""")
    
    # Commit the audit report
    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add audit report"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
    )
    
    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        dry_run=False,
    )
    
    assert result["verdict"] == "PASS"
    assert result["can_finalize"] is True
    assert result["committed"] is False
    assert "No changes to commit" in result["message"]


def test_finalize_task_commits_changes(temp_repo):
    """Test finalize commits changes for PASS task."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: PASS
---

# Audit Report

Test passed.
""")
    
    # Commit the audit report first
    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add audit report"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
    )
    
    # Add a new file to commit
    (temp_repo / "implementation.py").write_text("# Implementation\n")
    
    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        dry_run=False,
    )
    
    assert result["verdict"] == "PASS"
    assert result["can_finalize"] is True
    assert result["committed"] is True
    assert result["commit_hash"]
    assert "Successfully committed" in result["message"]
    
    # Verify working tree is clean after commit
    assert _git_status_clean(temp_repo)


def test_finalize_task_dry_run(temp_repo):
    """Test finalize in dry-run mode."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: PASS
---

# Audit Report

Test passed.
""")
    
    # Commit the audit report first
    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add audit report"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
    )
    
    # Add a new file
    (temp_repo / "implementation.py").write_text("# Implementation\n")
    
    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        dry_run=True,
    )
    
    assert result["verdict"] == "PASS"
    assert result["dry_run"] is True
    assert result["committed"] is False
    assert "DRY-RUN" in result["message"]
    
    # Verify working tree still has changes
    assert not _git_status_clean(temp_repo)


def test_finalize_task_deployment_integrity_fail(temp_repo):
    """Test finalize blocks on deployment integrity failure."""
    task_dir = temp_repo / "task_cards" / "AIPOS-TEST"
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / "AUDIT-REPORT-AIPOS-TESTR.md"
    audit_report.write_text("""---
audit_task_id: AIPOS-TESTR
verdict: PASS
---

# Audit Report

Test passed.
""")
    
    # Create mock deployment with drift
    deploy_dir = temp_repo / ".deploy"
    deploy_dir.mkdir()
    current_dir = deploy_dir / "releases" / "release1"
    current_dir.mkdir(parents=True)
    current_link = deploy_dir / "current"
    current_link.symlink_to(current_dir)
    
    old_commit = "0" * 40
    version_file = current_dir / "VERSION"
    version_file.write_text(f"git_commit: {old_commit}\n")
    
    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        dry_run=False,
    )
    
    assert result["verdict"] == "BLOCK"
    assert result["committed"] is False
    assert "integrity check failed" in result["message"]
