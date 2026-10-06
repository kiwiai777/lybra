"""AIPOS-FND-2 / AIPOS-FND-14 集成测试：完整 finalize 工作流程

验证：
1. gate 权威裁决 PASS → finalize 通过；无 gate 裁决 / 非 PASS → BLOCK
2. 手写 markdown（无 record_type: audit_verdict_record）→ 拒绝，不被骗
3. 部署完整性检查（current==HEAD）
4. git commit 实际执行
5. lybra CLI 活体断言（--workspace-root + --governance-root 分别指定两仓）

AIPOS-FND-14: integration_workspace = 产品仓（workspace_root，git 操作）；治理根 = 其同级临时目录
_gov(integration_workspace)（governance_root，5_tasks/records/audit_verdicts/ 落在此处）。
AIPOS-F116(F109R F-1): 原同一目录兼两角——产品已拒「workspace_root 在 governance_root 内」, 各用例在入口即 BLOCK,
「拦手写 markdown」用例因此为错误理由通过(又因 CLI 取 PATH 上的 lybra 随环境抖动)。改为两根分立 + 测本检出 CLI。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 被测 CLI = 本检出(bin/lybra 同款入口 python -m tools.aipos_cli.aipos_cli), 不取 PATH 上的 `lybra`。
#: AIPOS-F116(F109R F-1): 原写死 "lybra" 走 PATH —— PATH 缺 ~/.local/bin 时 FileNotFoundError 全红, 有则跑部署树旧版
#: (结果随环境与部署版本抖动, known-failure 严格检查器转绿即红)。改为确定地测本检出代码。
LYBRA_CLI = [sys.executable, "-m", "tools.aipos_cli.aipos_cli"]


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p)
    return env


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


def _gov(workspace: Path) -> Path:
    """治理根: 产品仓同级的临时目录(不在产品仓内, 也不含产品仓)。"""
    return workspace.parent / "governance"


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
        _gov(workspace).mkdir()

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
    _write_gate_verdict(_gov(integration_workspace), task_id, "PASS")

    # Also create a product-side task_cards dir and implementation change
    (integration_workspace / "task_cards" / task_id).mkdir()
    (integration_workspace / "tools" / "feature.py").write_text("# New feature\n")

    # Run finalize command (both --workspace-root and --governance-root point at the same
    # workspace since the fixture is single-repo; this also exercises the CLI wiring)
    result = subprocess.run(
        [
            *LYBRA_CLI,
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(_gov(integration_workspace)),
            "--json",
        ],
        cwd=integration_workspace,
        env=_cli_env(),
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
            *LYBRA_CLI,
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(_gov(integration_workspace)),
            "--json",
        ],
        cwd=integration_workspace,
        env=_cli_env(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "Finalize should fail when no gate verdict exists"
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "BLOCK"
    # 产品文案已中文化(「无门生裁决记录」); 判据仍是理由直指缺门生裁决记录(不是 task_cards AUDIT-REPORT)
    assert "无门生裁决记录" in payload["message"], payload["message"]


def test_finalize_workflow_blocks_handwritten_markdown(integration_workspace):
    """AIPOS-FND-14: a hand-written markdown with verdict: PASS but no
    record_type: audit_verdict_record must NOT be accepted as finalize evidence."""
    task_id = "AIPOS-INT-FAKE"
    verdicts_dir = _gov(integration_workspace) / "5_tasks" / "records" / "audit_verdicts" / task_id
    verdicts_dir.mkdir(parents=True)
    # Hand-written: no record_type field
    (verdicts_dir / "handwritten.md").write_text("---\nverdict: PASS\n---\n# Fake\n")

    result = subprocess.run(
        [
            *LYBRA_CLI,
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(_gov(integration_workspace)),
            "--json",
        ],
        cwd=integration_workspace,
        env=_cli_env(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "Finalize should reject hand-written markdown"
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "BLOCK"
    # 拦的理由须是「手写件缺门生标记被拒」, 不是入口别的 BLOCK(F116: 原两根同目录时即为入口 BLOCK 而误绿)
    assert "handwritten.md" in payload["message"] and "record_type" in payload["message"], payload["message"]


def test_finalize_workflow_fail_task(integration_workspace):
    """Finalize correctly blocks when gate audit verdict is FAIL."""
    task_id = "AIPOS-INT-2"
    _write_gate_verdict(_gov(integration_workspace), task_id, "FAIL")

    result = subprocess.run(
        [
            *LYBRA_CLI,
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(_gov(integration_workspace)),
            "--json",
        ],
        cwd=integration_workspace,
        env=_cli_env(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, "Finalize should fail for non-PASS task"
    payload = json.loads(result.stdout)
    assert "FAIL" in payload["message"] or "not PASS" in payload["message"]


def test_finalize_workflow_dry_run(integration_workspace):
    """Test finalize dry-run mode."""
    task_id = "AIPOS-INT-3"
    _write_gate_verdict(_gov(integration_workspace), task_id, "PASS")

    # Add a change (uncommitted)
    (integration_workspace / "tools" / "dryrun_test.py").write_text("# Dry run test\n")

    result = subprocess.run(
        [
            *LYBRA_CLI,
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(_gov(integration_workspace)),
            "--dry-run",
            "--json",
        ],
        cwd=integration_workspace,
        env=_cli_env(),
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
    _write_gate_verdict(_gov(integration_workspace), task_id, "PASS")

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
            *LYBRA_CLI,
            "finalize",
            "--task-id", task_id,
            "--actor", "test_actor",
            "--workspace-root", str(integration_workspace),
            "--governance-root", str(_gov(integration_workspace)),
            "--json",
        ],
        cwd=integration_workspace,
        env=_cli_env(),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Finalize should succeed: {result.stderr}"
    assert "No changes" in result.stdout or "clean" in result.stdout
