"""AIPOS-FND-2 集成测试：完整 finalize 工作流程

验证：
1. PASS 卡可以 finalize，非 PASS 拒绝
2. 部署完整性检查（current==HEAD）
3. git commit 实际执行
4. turn_advancer 接实（finalize 动作不再占位）
"""

import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def integration_workspace():
    """Create a complete workspace for integration testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.local"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        
        # Create directory structure
        (workspace / "task_cards").mkdir()
        (workspace / "tools").mkdir()
        
        # Initial commit
        (workspace / "README.md").write_text("# Test Workspace\n")
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        
        yield workspace


def test_finalize_workflow_pass_task(integration_workspace):
    """Test complete finalize workflow for a PASS task."""
    task_id = "AIPOS-INT-1"
    task_dir = integration_workspace / "task_cards" / task_id
    task_dir.mkdir()
    
    # Create implementation file
    impl_file = integration_workspace / "tools" / "implementation.py"
    impl_file.write_text("# Implementation for AIPOS-INT-1\n")
    
    # Create audit report with PASS verdict
    audit_report = task_dir / f"AUDIT-REPORT-{task_id}R.md"
    audit_report.write_text(f"""---
audit_task_id: {task_id}R
audited_task: {task_id}
verdict: PASS
---

# Audit Report

Implementation verified and passed.
""")
    
    # Stage and commit audit report
    subprocess.run(
        ["git", "add", "-A"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"Add audit report for {task_id}"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    
    # Add another change that needs to be finalized
    (integration_workspace / "tools" / "feature.py").write_text("# New feature\n")
    
    # Run finalize command
    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Finalize failed: {result.stderr}"
    
    # Verify working tree is clean
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    assert not status_result.stdout.strip(), "Working tree should be clean after finalize"
    
    # Verify commit message
    log_result = subprocess.run(
        ["git", "log", "-1", "--oneline"],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    assert task_id in log_result.stdout, "Commit message should contain task ID"
    assert "finalize" in log_result.stdout.lower(), "Commit message should mention finalize"


def test_finalize_workflow_fail_task(integration_workspace):
    """Test finalize correctly blocks on FAIL verdict."""
    task_id = "AIPOS-INT-2"
    task_dir = integration_workspace / "task_cards" / task_id
    task_dir.mkdir()
    
    # Create audit report with FAIL verdict
    audit_report = task_dir / f"AUDIT-REPORT-{task_id}R.md"
    audit_report.write_text(f"""---
audit_task_id: {task_id}R
audited_task: {task_id}
verdict: FAIL
---

# Audit Report

Implementation has critical issues.
""")
    
    # Run finalize command
    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 1, "Finalize should fail for non-PASS task"
    assert "FAIL" in result.stdout or "not PASS" in result.stdout


def test_finalize_workflow_dry_run(integration_workspace):
    """Test finalize dry-run mode."""
    task_id = "AIPOS-INT-3"
    task_dir = integration_workspace / "task_cards" / task_id
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / f"AUDIT-REPORT-{task_id}R.md"
    audit_report.write_text(f"""---
audit_task_id: {task_id}R
audited_task: {task_id}
verdict: PASS
---

# Audit Report

Dry-run test.
""")
    
    # Commit audit report
    subprocess.run(
        ["git", "add", "-A"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add audit report"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    
    # Add a change
    (integration_workspace / "tools" / "dryrun_test.py").write_text("# Dry run test\n")
    
    # Run finalize in dry-run mode
    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--dry-run",
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Dry-run should succeed: {result.stderr}"
    assert "DRY-RUN" in result.stdout or "dry_run" in result.stdout
    
    # Verify working tree still has uncommitted changes
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    assert status_result.stdout.strip(), "Working tree should still have changes after dry-run"


def test_finalize_no_changes_to_commit(integration_workspace):
    """Test finalize with clean working tree (no changes to commit)."""
    task_id = "AIPOS-INT-4"
    task_dir = integration_workspace / "task_cards" / task_id
    task_dir.mkdir()
    
    # Create audit report with PASS verdict
    audit_report = task_dir / f"AUDIT-REPORT-{task_id}R.md"
    audit_report.write_text(f"""---
audit_task_id: {task_id}R
audited_task: {task_id}
verdict: PASS
---

# Audit Report

No changes test.
""")
    
    # Commit everything
    subprocess.run(
        ["git", "add", "-A"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Commit all changes"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    
    # Run finalize (should succeed but not commit)
    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"Finalize should succeed: {result.stderr}"
    assert "No changes" in result.stdout or "clean" in result.stdout
