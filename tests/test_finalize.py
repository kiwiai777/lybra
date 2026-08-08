"""AIPOS-FND-2 / AIPOS-FND-14 — finalize 命令测试

测试 finalize 命令的核心逻辑：
1. 仅 gate 权威裁决记录(audit_verdict_record) verdict=PASS/PASS_WITH_NOTES 才可 finalize
2. 手写 markdown(缺 record_type: audit_verdict_record)不计数
3. 部署完整性检查（current==HEAD）
4. git commit/push 操作
5. dry-run 模式

AIPOS-FND-14: check_task_can_finalize 不再读 task_cards/<task_id>/AUDIT-REPORT-*.md 的
frontmatter verdict 字段（该报告本无 frontmatter、且任何人可手写伪造）。改读治理仓
5_tasks/records/audit_verdicts/<task_id>/ 下的 gate 权威裁决记录，要求 record_type:
audit_verdict_record，取最新终态裁决。这里 temp_repo 同时充当 workspace_root（git 操作）
与 governance_root（裁决记录所在），与单仓测试夹具保持一致；finalize_task 显式传入
governance_root=temp_repo，避免测试环境里 resolve_workspace_root() 的自动发现逻辑生效。
"""

import subprocess
import tempfile
from pathlib import Path

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

        # Create task_cards directory (product-repo side; unused by finalize eligibility now,
        # kept for realism / potential display-only report checks)
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


def _write_gate_verdict(
    governance_root: Path,
    task_id: str,
    verdict: str,
    *,
    verdict_id: str = "verdict_TEST_20260101_000000_audit",
    verdict_at: str = "2026-01-01T00:00:00Z",
    record_type: str = "audit_verdict_record",
) -> Path:
    """Write a gate-shaped audit_verdict_record fixture under
    <governance_root>/5_tasks/records/audit_verdicts/<task_id>/<verdict_id>.md
    """
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
        "# MCP Audit Verdict Record\n"
    )
    return path


def _write_handwritten_markdown(governance_root: Path, task_id: str, verdict: str) -> Path:
    """Write a hand-authored markdown file in the verdicts dir WITHOUT
    record_type: audit_verdict_record — must never count as finalize evidence."""
    verdicts_dir = governance_root / "5_tasks" / "records" / "audit_verdicts" / task_id
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    path = verdicts_dir / "handwritten.md"
    path.write_text(
        "---\n"
        f"verdict: {verdict}\n"
        "---\n"
        "# Hand-written, not a gate record\n"
    )
    return path


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


def test_check_task_can_finalize_no_verdicts_dir(temp_repo):
    """No 5_tasks/records/audit_verdicts/<task_id>/ dir at all -> BLOCK, reason points at
    the missing gate audit verdict record (not a task_cards AUDIT-REPORT)."""
    result = check_task_can_finalize("AIPOS-999", temp_repo)
    assert result["can_finalize"] is False
    assert "No gate audit verdict record" in result["reason"]


def test_check_task_can_finalize_no_verdict_files(temp_repo):
    """Verdicts dir exists but is empty -> BLOCK."""
    verdicts_dir = temp_repo / "5_tasks" / "records" / "audit_verdicts" / "AIPOS-TEST"
    verdicts_dir.mkdir(parents=True)

    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is False
    assert "No gate audit verdict record" in result["reason"]


def test_check_task_can_finalize_pass_verdict(temp_repo):
    """A real gate audit_verdict_record with verdict=PASS -> can_finalize."""
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "PASS")

    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is True
    assert result["verdict"] == "PASS"
    assert result["verdict_record_path"]


def test_check_task_can_finalize_pass_with_notes_verdict(temp_repo):
    """PASS_WITH_NOTES is also an accepted terminal verdict (AIPOS-FND-7F3 parity)."""
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "PASS_WITH_NOTES")

    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is True
    assert result["verdict"] == "PASS_WITH_NOTES"


def test_check_task_can_finalize_fail_verdict(temp_repo):
    """A real gate audit_verdict_record with verdict=FAIL -> cannot finalize."""
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "FAIL")

    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is False
    assert result["verdict"] == "FAIL"
    assert "not PASS" in result["reason"]


def test_check_task_can_finalize_rejects_handwritten_markdown(temp_repo):
    """AIPOS-FND-14: a hand-written markdown file (no record_type: audit_verdict_record)
    sitting in the verdicts dir must NEVER be trusted as finalize evidence, even if it claims
    verdict: PASS."""
    _write_handwritten_markdown(temp_repo, "AIPOS-TEST", "PASS")

    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is False
    assert "record_type: audit_verdict_record" in result["reason"]


def test_check_task_can_finalize_latest_terminal_verdict_wins(temp_repo):
    """When multiple gate verdicts exist, the LATEST by verdict_at governs (re-review flow:
    FAIL then PASS on re-audit -> finalize allowed)."""
    _write_gate_verdict(
        temp_repo, "AIPOS-TEST", "FAIL",
        verdict_id="verdict_1", verdict_at="2026-01-01T00:00:00Z",
    )
    _write_gate_verdict(
        temp_repo, "AIPOS-TEST", "PASS",
        verdict_id="verdict_2", verdict_at="2026-01-02T00:00:00Z",
    )

    result = check_task_can_finalize("AIPOS-TEST", temp_repo)
    assert result["can_finalize"] is True
    assert result["verdict"] == "PASS"


def test_finalize_task_non_pass_blocked(temp_repo):
    """Test finalize blocks on non-PASS gate verdict."""
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "FAIL")

    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        governance_root=temp_repo,
        dry_run=False,
    )

    assert result["verdict"] == "BLOCK"
    assert result["can_finalize"] is False
    assert result["committed"] is False


def test_finalize_task_no_gate_verdict_blocked(temp_repo):
    """Test finalize blocks with an accurate reason when no gate verdict record exists at
    all (must not be confused with 'report not found')."""
    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        governance_root=temp_repo,
        dry_run=False,
    )

    assert result["verdict"] == "BLOCK"
    assert result["can_finalize"] is False
    assert "gate audit verdict record" in result["message"]


def test_finalize_task_clean_working_tree(temp_repo):
    """Test finalize with clean working tree (no changes to commit)."""
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "PASS")

    # Commit the gate verdict record so the working tree is clean
    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add gate verdict record"],
        cwd=temp_repo,
        check=True,
        capture_output=True,
    )

    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        governance_root=temp_repo,
        dry_run=False,
    )

    assert result["verdict"] == "PASS"
    assert result["can_finalize"] is True
    assert result["committed"] is False
    assert "No changes to commit" in result["message"]


def test_finalize_task_commits_changes(temp_repo):
    """Test finalize commits changes for PASS task."""
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "PASS")

    # Add a new file to commit
    (temp_repo / "implementation.py").write_text("# Implementation\n")

    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        governance_root=temp_repo,
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
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "PASS")

    # Add a new file
    (temp_repo / "implementation.py").write_text("# Implementation\n")

    result = finalize_task(
        task_id="AIPOS-TEST",
        actor="test_actor",
        workspace_root=temp_repo,
        governance_root=temp_repo,
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
    _write_gate_verdict(temp_repo, "AIPOS-TEST", "PASS")

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
        governance_root=temp_repo,
        dry_run=False,
    )

    assert result["verdict"] == "BLOCK"
    assert result["committed"] is False
    assert "integrity check failed" in result["message"]
