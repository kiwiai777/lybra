"""AIPOS-FND-2 / AIPOS-FND-14 集成测试：完整 finalize 工作流程

验证：
1. gate 权威裁决 PASS → finalize 通过；无 gate 裁决 / 非 PASS → BLOCK
2. 手写 markdown（无 record_type: audit_verdict_record）→ 拒绝，不被骗
3. 部署完整性检查（current==HEAD）
4. git commit 实际执行
5. lybra CLI 活体断言（--workspace-root + --governance-root 分别指定两仓）

AIPOS-FND-14: integration_workspace 同时充当产品仓（workspace_root，git 操作）与治理仓
（governance_root，5_tasks/records/audit_verdicts/ 落在此处）。lybra CLI 传
--governance-root=<same path> 使测试 workspace 同时承担两个角色。
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest


def _write_gate_verdict(
    governance_root: Path,
    task_id: str,
    verdict: str,
    *,
    verdict_id: str | None = None,
    verdict_at: str = "2026-01-01T00:00:00Z",
    record_type: str = "audit_verdict_record",
) -> Path:
    """Write a gate-shaped audit_verdict_record fixture under
    <governance_root>/5_tasks/records/audit_verdicts/<task_id>/<verdict_id>.md
    """
    if verdict_id is None:
        verdict_id = f"verdict_{task_id}_20260101_000000_audit"
    verdicts_dir = governance_root / "5_tasks" / "records" / "audit_verdicts" / task_id
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    path = verdicts_dir / f"{verdict_id}.md"
    path.write_text(
        "---\n"
        f"record_type: {record_type}\n"
        "event_type: mcp_audit_verdict\n"
        f"verdict_id: {verdict_id}\n"
        f"verdict: {verdict}\n"
        f"reviewed_task_id: {task_id}\n"
        f"verdict_at: '{verdict_at}'\n"
        "---\n"
        f"# MCP Audit Verdict Record: {verdict_id}\n"
    )
    return path


@pytest.fixture
def integration_workspace():
    """Create a complete workspace for integration testing.

    This tempdir acts as BOTH the product code repo (workspace_root for git ops)
    AND the governance workspace (governance_root owning 5_tasks/records/).  That
    mirrors a single-repo setup and keeps the fixture self-contained.
    """
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

        # Create directory structure (product-side)
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
    """Test complete finalize workflow for a task with a real gate PASS verdict."""
    task_id = "AIPOS-INT-1"

    # Create gate audit_verdict_record (authoritative, in governance 5_tasks/records/)
    _write_gate_verdict(integration_workspace, task_id, "PASS")

    # Also create a product-side task_cards dir and implementation change
    (integration_workspace / "task_cards" / task_id).mkdir()
    (integration_workspace / "tools" / "feature.py").write_text("# New feature\n")

    # Run finalize command (both --workspace-root and --governance-root point at the same
    # workspace since the fixture is single-repo; this also exercises the CLI wiring)
    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Finalize failed: {result.stderr}\n{result.stdout}"

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


def test_finalize_workflow_blocks_no_gate_verdict(integration_workspace):
    """AIPOS-FND-14: finalize blocks when there is NO gate audit_verdict_record at all.
    The reason must clearly point at the missing gate record (not a task_cards AUDIT-REPORT).
    """
    task_id = "AIPOS-INT-NO-VERDICT"

    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "Finalize should fail when no gate verdict exists"
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "BLOCK"
    assert "gate audit verdict record" in payload["message"].lower()


def test_finalize_workflow_blocks_handwritten_markdown(integration_workspace):
    """AIPOS-FND-14: a hand-written markdown with verdict: PASS but no
    record_type: audit_verdict_record must NOT be accepted as finalize evidence."""
    task_id = "AIPOS-INT-FAKE"
    verdicts_dir = integration_workspace / "5_tasks" / "records" / "audit_verdicts" / task_id
    verdicts_dir.mkdir(parents=True)
    # Hand-written: no record_type field
    (verdicts_dir / "handwritten.md").write_text("---\nverdict: PASS\n---\n# Fake\n")

    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "Finalize should reject hand-written markdown"
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "BLOCK"


def test_finalize_workflow_fail_task(integration_workspace):
    """Finalize correctly blocks when gate audit verdict is FAIL."""
    task_id = "AIPOS-INT-2"
    _write_gate_verdict(integration_workspace, task_id, "FAIL")

    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "Finalize should fail for non-PASS task"
    payload = json.loads(result.stdout)
    assert "FAIL" in payload["message"] or "not PASS" in payload["message"]


def test_finalize_workflow_dry_run(integration_workspace):
    """Test finalize dry-run mode."""
    task_id = "AIPOS-INT-3"
    _write_gate_verdict(integration_workspace, task_id, "PASS")

    # Add a change (uncommitted)
    (integration_workspace / "tools" / "dryrun_test.py").write_text("# Dry run test\n")

    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(integration_workspace),
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
    """Test finalize with clean working tree (no changes to commit) -> PASS, not committed."""
    task_id = "AIPOS-INT-4"
    _write_gate_verdict(integration_workspace, task_id, "PASS")

    # Commit the gate record so tree is clean
    subprocess.run(
        ["git", "add", "-A"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add gate verdict record"],
        cwd=integration_workspace,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        [
            "lybra",
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(integration_workspace),
            "--json",
        ],
        cwd=integration_workspace,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Finalize should succeed: {result.stderr}"
    assert "No changes" in result.stdout or "clean" in result.stdout
