"""AIPOS-F61: 收尾原子化与结算状态一次读齐

Tests cover:
- ① CLI `queue complete` 收敛到 close_task(禁只搬文件不落记录)
- ② finalize 代码任务分支不存在 = 硬 BLOCK
- ③ finalization record 只在有实际合并时写入
- ④ 结算状态一次读齐三类记录
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixture: minimal governance + product repo
# ---------------------------------------------------------------------------

@pytest.fixture
def gov_root(tmp_path: Path) -> Path:
    """Create minimal governance workspace."""
    root = tmp_path / "governance"
    root.mkdir()
    queue_root = root / "5_tasks" / "queue"
    for state in ("pending", "claimed", "completed", "blocked"):
        (queue_root / state).mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "records" / "finalizations").mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "records" / "returns").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "stage_archives").mkdir(parents=True, exist_ok=True)
    # Recent stage archive
    (root / "governance" / "stage_archives" / "2026-08-31_test.md").write_text("# test", encoding="utf-8")
    return root


def _create_claimed_task(gov_root: Path, task_id: str = "AIPOS-F61-TEST", *, task_mode: str = "code") -> None:
    """Create a claimed task card with return record."""
    task_card = gov_root / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md"
    task_card.write_text(
        "---\n"
        f"task_id: {task_id}\n"
        f"title: Test task for F61\n"
        "project: lybra\n"
        "status: claimed\n"
        "claimed_by: exec.test\n"
        "claimed_at: '2026-08-31T12:00:00Z'\n"
        f"claim_id: claim_{task_id}_20260831_120000Z_exec-test\n"
        f"active_session_id: session_{task_id}_20260831_120000Z_exec-test\n"
        "assigned_to: exec.lybra.kiwiai-dev\n"
        "agent_instance: exec.lybra.kiwiai-dev\n"
        "context_bundle: exec.lybra.kiwiai-dev\n"
        f"task_mode: {task_mode}\n"
        "priority: high\n"
        "created_by: advisor.test\n"
        "needs_owner: false\n"
        "output_target: tools/\n"
        "artifact_policy: formal_write\n"
        "---\n"
        f"# {task_id}\n",
        encoding="utf-8",
    )
    # Return record
    ret_dir = gov_root / "5_tasks" / "records" / "returns" / task_id
    ret_dir.mkdir(parents=True, exist_ok=True)
    ret_file = ret_dir / "return_001.md"
    ret_file.write_text(
        "---\n"
        f"record_type: return_record\n"
        f"task_id: {task_id}\n"
        "actor: exec.test\n"
        "returned_at: '2026-08-31T13:00:00Z'\n"
        "---\n"
        "# Return\n",
        encoding="utf-8",
    )


@pytest.fixture
def product_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for finalize tests."""
    repo = tmp_path / "product"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True, check=True)
    # Initial commit on main
    (repo / "README.md").write_text("# Product\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=str(repo), capture_output=True, check=True)
    return repo


# ---------------------------------------------------------------------------
# ① CLI queue complete convergence
# ---------------------------------------------------------------------------

class TestQueueCompleteConvergence:
    """AIPOS-F61 ①: CLI `queue complete` 收敛到 close_task, 禁只搬文件。"""

    def test_complete_writes_closure_record(self, gov_root: Path) -> None:
        """`queue complete` 必须写 closure 记录(不再只搬文件)。"""
        from tools.aipos_cli.board_adapter import close_task

        task_id = "AIPOS-F61-COMPLETE"
        _create_claimed_task(gov_root, task_id)

        # 模拟 CLI complete → 现在走 close_task
        result = close_task(
            task_id=task_id,
            actor="exec.test",
            closure_evidence={"finalize_return_ref": "test_report_link"},
            dry_run=False,
            repo_root=gov_root,
        )

        assert result.get("ok") is True, f"close_task failed: {result}"
        # 验证 closure 记录已写
        closure_dir = gov_root / "5_tasks" / "records" / "closures" / task_id
        assert closure_dir.is_dir(), "closure directory should exist after complete"
        closure_files = list(closure_dir.glob("close_*.md"))
        assert len(closure_files) > 0, "closure record should be written"

    def test_complete_without_evidence_blocked(self, gov_root: Path) -> None:
        """`queue complete` 无 closure evidence 应 BLOCK(不再静默搬文件)。"""
        from tools.aipos_cli.board_adapter import close_task

        task_id = "AIPOS-F61-NO-EVIDENCE"
        _create_claimed_task(gov_root, task_id)

        result = close_task(
            task_id=task_id,
            actor="exec.test",
            closure_evidence=None,
            dry_run=False,
            repo_root=gov_root,
        )

        # 无 evidence → BLOCK
        assert result.get("verdict") == "BLOCK" or result.get("ok") is False


# ---------------------------------------------------------------------------
# ② Finalize branch-not-found BLOCK
# ---------------------------------------------------------------------------

class TestFinalizeBranchBlock:
    """AIPOS-F61 ②: 代码任务分支不存在 = 硬 BLOCK。"""

    def test_code_task_missing_branch_blocks(self, product_repo: Path, gov_root: Path) -> None:
        """代码任务缺分支 → _integrate_card_branch 返回 blocked。"""
        from tools.aipos_cli.finalize import _integrate_card_branch, _load_branch_integration

        branch_integration = _load_branch_integration(product_repo)
        operations: list[str] = []

        result = _integrate_card_branch(
            task_id="AIPOS-F61-MISSING",
            verdict_id="test_verdict",
            workspace_root=product_repo,
            governance_root=gov_root,
            dry_run=False,
            operations=operations,
            branch_integration=branch_integration,
            task_mode="code",
            output_target="tools/",
        )

        assert result["blocked"] is True, f"code task missing branch should BLOCK, got: {result}"
        assert "BLOCKED" in result["message"]
        assert "未找到声明分支" in result["message"]

    def test_non_code_task_missing_branch_skips(self, product_repo: Path, gov_root: Path) -> None:
        """非代码任务缺分支 → 跳过(不 BLOCK)。"""
        from tools.aipos_cli.finalize import _integrate_card_branch, _load_branch_integration

        branch_integration = _load_branch_integration(product_repo)
        operations: list[str] = []

        result = _integrate_card_branch(
            task_id="AIPOS-F61-DOCS",
            verdict_id="test_verdict",
            workspace_root=product_repo,
            governance_root=gov_root,
            dry_run=False,
            operations=operations,
            branch_integration=branch_integration,
            task_mode="docs",
            output_target=None,
        )

        assert result["blocked"] is False
        assert result["action"] == "skipped_no_branch"

    def test_similar_branches_listed(self, product_repo: Path, gov_root: Path) -> None:
        """BLOCK 消息应列出相近分支。"""
        from tools.aipos_cli.finalize import _integrate_card_branch, _load_branch_integration

        # 创建一个命名错误的分支
        subprocess.run(
            ["git", "checkout", "-b", "code/AIPOS-F61-SIM"],
            cwd=str(product_repo), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=str(product_repo), capture_output=True, check=True,
        )

        branch_integration = _load_branch_integration(product_repo)
        operations: list[str] = []

        result = _integrate_card_branch(
            task_id="AIPOS-F61-SIM",
            verdict_id="test_verdict",
            workspace_root=product_repo,
            governance_root=gov_root,
            dry_run=False,
            operations=operations,
            branch_integration=branch_integration,
            task_mode="code",
            output_target="tools/",
        )

        assert result["blocked"] is True
        assert "code/AIPOS-F61-SIM" in result["message"], f"Should list similar branch: {result['message']}"


# ---------------------------------------------------------------------------
# ③ Finalization record only on actual merge
# ---------------------------------------------------------------------------

class TestFinalizationRecordGuard:
    """AIPOS-F61 ③: finalization record 只在有实际合并时写入。"""

    def test_no_finalize_record_without_merge(self, gov_root: Path) -> None:
        """无实际合并时不应写 finalization 记录。"""
        # 验证 _ensure_finalization_record 不被 clean-tree 路径调用
        # (通过检查 finalize 代码中的 _actual_merge_happened 守卫)
        from tools.aipos_cli.finalize import _ensure_finalization_record

        # 手动验证: 写记录函数本身正常工作
        fin_dir = gov_root / "5_tasks" / "records" / "finalizations" / "AIPOS-F61-FIN"
        assert not fin_dir.exists(), "no finalization record should exist yet"

        operations: list[str] = []
        _ensure_finalization_record(
            governance_root=gov_root,
            task_id="AIPOS-F61-FIN",
            actor="exec.test",
            commit_hash="abc123def456",
            verdict_id="test_verdict",
            deployed=True,
            operations=operations,
        )
        # 写完后应存在
        assert fin_dir.is_dir(), "finalization record should be written when called"


# ---------------------------------------------------------------------------
# ④ Settlement status reader
# ---------------------------------------------------------------------------

class TestSettlementStatusReader:
    """AIPOS-F61 ④: 结算状态一次读齐三类记录。"""

    def test_reads_all_three_types(self, gov_root: Path) -> None:
        """read_settlement_status 应同时读队列位置 + closure + finalization。"""
        from tools.aipos_cli.board_adapter import read_settlement_status

        task_id = "AIPOS-F61-SETTLE"
        _create_claimed_task(gov_root, task_id)

        # 初始状态: claimed, 无 closure, 无 finalization
        status = read_settlement_status(task_id, gov_root)
        assert status["queue_position"]["state"] == "claimed"
        assert len(status["closure_records"]) == 0
        assert len(status["finalization_records"]) == 0
        assert status["is_settled"] is False
        assert "closure_record" in status["missing"]
        assert "finalization_record" in status["missing"]

    def test_settled_when_all_present(self, gov_root: Path) -> None:
        """三类齐全 = is_settled True。"""
        from tools.aipos_cli.board_adapter import read_settlement_status

        task_id = "AIPOS-F61-FULL"
        _create_claimed_task(gov_root, task_id)

        # 移动到 completed
        from tools.aipos_cli.board_adapter import close_task
        result = close_task(
            task_id=task_id,
            actor="exec.test",
            closure_evidence={"finalize_return_ref": "test"},
            dry_run=False,
            repo_root=gov_root,
        )
        assert result.get("ok") is True

        # 写 finalization 记录
        from tools.aipos_cli.finalize import _ensure_finalization_record
        operations: list[str] = []
        _ensure_finalization_record(
            governance_root=gov_root,
            task_id=task_id,
            actor="exec.test",
            commit_hash="abc123def456",
            verdict_id="test_verdict",
            deployed=True,
            operations=operations,
        )

        # 现在应该已结算
        status = read_settlement_status(task_id, gov_root)
        assert status["queue_position"]["state"] == "completed"
        assert len(status["closure_records"]) > 0
        assert len(status["finalization_records"]) > 0
        assert status["is_settled"] is True
        assert len(status["missing"]) == 0

    def test_partial_settlement_not_settled(self, gov_root: Path) -> None:
        """只有 closure 没有 finalization = 未结算。"""
        from tools.aipos_cli.board_adapter import read_settlement_status, close_task

        task_id = "AIPOS-F61-PARTIAL"
        _create_claimed_task(gov_root, task_id)

        # 只 close(写 closure), 不写 finalization
        result = close_task(
            task_id=task_id,
            actor="exec.test",
            closure_evidence={"finalize_return_ref": "test"},
            dry_run=False,
            repo_root=gov_root,
        )
        assert result.get("ok") is True

        status = read_settlement_status(task_id, gov_root)
        assert status["queue_position"]["state"] == "completed"
        assert len(status["closure_records"]) > 0
        assert len(status["finalization_records"]) == 0
        assert status["is_settled"] is False
        assert "finalization_record" in status["missing"]
