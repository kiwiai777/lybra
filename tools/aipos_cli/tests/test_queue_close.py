"""AIPOS-283/289: tests for the gate close verb (lybra_queue_close_dry_run/confirm).

Tests cover:
- S1: closure evidence validation (missing evidence rejected)
- S1: task must be in claimed/ with return record
- S1: confirm moves card claimed/ -> completed/ + writes closure record
- S5: duplicate close rejected (idempotency guard)
- S5: audit-derived card auto-close
- AIPOS-289 S1: decision_log account check (missing entry -> WARN)
- AIPOS-289 S2: stage_archive freshness check (stale -> WARN)
- AIPOS-289 S4: zero regression on existing close flow
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tools.aipos_cli.board_adapter import close_task
from tools.aipos_cli.records import expected_closure_record_path, load_records


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a minimal repo with queue structure and a claimed task with return record."""
    root = tmp_path
    queue_root = root / "5_tasks" / "queue"
    for state in ("pending", "claimed", "completed", "blocked"):
        (queue_root / state).mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "records" / "returns" / "AIPOS-TEST-001").mkdir(parents=True, exist_ok=True)
    (root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)

    # AIPOS-289: create governance structure with decision_log/ and stage_archive/
    (root / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)
    (root / "stage_archive").mkdir(parents=True, exist_ok=True)
    
    # Create a decision_log entry for AIPOS-TEST-001
    decision_log = root / "governance" / "decision_log" / "2026-08.md"
    decision_log.write_text(
        "# August 2026 Decisions\n\n"
        "## AIPOS-TEST-001\n\n"
        "Test decision entry.\n",
        encoding="utf-8",
    )
    
    # Create a recent stage_archive entry
    stage_file = root / "stage_archive" / "2026-08-01_test_stage.md"
    stage_file.write_text("# Test Stage\n", encoding="utf-8")

    # Create a claimed task card (with all REQUIRED_FIELDS from validator.py)
    task_card = queue_root / "claimed" / "aipos-test-001.md"
    task_card.write_text(
        "---\n"
        "task_id: AIPOS-TEST-001\n"
        "title: Test task for close verb\n"
        "project: lybra\n"
        "status: claimed\n"
        "claimed_by: exec.test\n"
        "claimed_at: '2026-07-30T12:00:00Z'\n"
        "claim_id: claim_AIPOS-TEST-001_20260730_120000Z_exec-test\n"
        "active_session_id: session_AIPOS-TEST-001_20260730_120000Z_exec-test\n"
        "assigned_to: exec.lybra.kiwiai-dev\n"
        "agent_instance: exec.lybra.kiwiai-dev\n"
        "context_bundle: exec.lybra.kiwiai-dev\n"
        "task_mode: code\n"
        "priority: high\n"
        "created_by: advisor.test\n"
        "needs_owner: false\n"
        "output_target: tools/\n"
        "artifact_policy: formal_write\n"
        "---\n"
        "# Test task\n",
        encoding="utf-8",
    )

    # Create a return record for the task
    return_record = root / "5_tasks" / "records" / "returns" / "AIPOS-TEST-001" / "return_test_001.md"
    return_record.write_text(
        "---\n"
        "record_type: return_record\n"
        "task_id: AIPOS-TEST-001\n"
        "return_id: return_test_001\n"
        "returned_at: '2026-07-30T13:00:00Z'\n"
        "---\n"
        "# Return record\n",
        encoding="utf-8",
    )

    # Create project.json so _resolve_active_project_for works
    (root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

    return root


class TestCloseTaskDryRun:
    """S1: dry-run validation tests."""

    def test_missing_closure_evidence_rejected(self, repo: Path) -> None:
        """S5: missing evidence → BLOCK."""
        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence=None,
            dry_run=True,
            repo_root=repo,
        )
        assert result.get("verdict") == "BLOCK"
        assert any("closure_evidence" in str(e).lower() for e in result.get("blocking_reasons", [])) or \
               result.get("error_code") == "MISSING_CLOSURE_EVIDENCE" or \
               not result.get("ok", True)

    def test_empty_closure_evidence_rejected(self, repo: Path) -> None:
        """S5: empty evidence object → BLOCK."""
        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={},
            dry_run=True,
            repo_root=repo,
        )
        assert not result.get("ok", True) or result.get("verdict") == "BLOCK"

    def test_missing_actor_rejected(self, repo: Path) -> None:
        result = close_task(
            task_id="AIPOS-TEST-001",
            actor=None,
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=True,
            repo_root=repo,
        )
        assert not result.get("ok", True) or result.get("verdict") == "BLOCK"

    def test_valid_dry_run_passes(self, repo: Path) -> None:
        """S1: valid inputs → dry-run PASS with closure preview."""
        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123def"},
            dry_run=True,
            repo_root=repo,
        )
        assert result.get("ok", True)
        data = result.get("data", {})
        assert data.get("task_id") == "AIPOS-TEST-001"
        assert data.get("from_state") == "claimed"
        assert data.get("to_state") == "completed"
        assert data.get("closure_id")
        assert data.get("closure_record_path")

    def test_task_not_in_claimed_rejected(self, repo: Path) -> None:
        """S1: task must be in claimed/."""
        # Move the task to completed/
        claimed = repo / "5_tasks" / "queue" / "claimed" / "aipos-test-001.md"
        completed = repo / "5_tasks" / "queue" / "completed" / "aipos-test-001.md"
        shutil.move(str(claimed), str(completed))

        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=True,
            repo_root=repo,
        )
        assert not result.get("ok", True) or result.get("verdict") == "BLOCK"

    def test_task_without_return_record_rejected(self, tmp_path: Path) -> None:
        """S1: task must have a return record."""
        root = tmp_path
        queue_root = root / "5_tasks" / "queue"
        for state in ("pending", "claimed", "completed", "blocked"):
            (queue_root / state).mkdir(parents=True, exist_ok=True)
        (root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)
        (root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

        # Create a claimed task WITHOUT a return record (with all REQUIRED_FIELDS)
        task_card = queue_root / "claimed" / "aipos-test-002.md"
        task_card.write_text(
            "---\n"
            "task_id: AIPOS-TEST-002\n"
            "title: Task without return\n"
            "project: lybra\n"
            "status: claimed\n"
            "claimed_by: exec.test\n"
            "claimed_at: '2026-07-30T12:00:00Z'\n"
            "assigned_to: exec.lybra.kiwiai-dev\n"
            "context_bundle: exec.lybra.kiwiai-dev\n"
            "task_mode: code\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# No return\n",
            encoding="utf-8",
        )

        result = close_task(
            task_id="AIPOS-TEST-002",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=True,
            repo_root=root,
        )
        assert not result.get("ok", True) or result.get("verdict") == "BLOCK"


class TestCloseTaskConfirm:
    """S1: confirm execution tests."""

    def test_confirm_moves_card_and_writes_closure(self, repo: Path) -> None:
        """S1: confirm moves claimed/ -> completed/ and writes closure record."""
        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123def456"},
            dry_run=False,
            repo_root=repo,
        )
        assert result.get("ok", True)
        data = result.get("data", {})
        assert data.get("from_state") == "claimed"
        assert data.get("to_state") == "completed"

        # Card moved to completed/
        completed_card = repo / "5_tasks" / "queue" / "completed" / "aipos-test-001.md"
        assert completed_card.exists()
        claimed_card = repo / "5_tasks" / "queue" / "claimed" / "aipos-test-001.md"
        assert not claimed_card.exists()

        # Closure record written
        records = load_records(repo)
        task_closures = records.get("task_closures", {}).get("AIPOS-TEST-001", [])
        assert len(task_closures) == 1
        assert task_closures[0].get("task_id") == "AIPOS-TEST-001"

    def test_duplicate_close_rejected(self, repo: Path) -> None:
        """S5: duplicate close is rejected (idempotency guard)."""
        # First close
        result1 = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=repo,
        )
        assert result1.get("ok", True)

        # The task is now in completed/, so a second close should fail
        # because it's no longer in claimed/
        result2 = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "def456"},
            dry_run=False,
            repo_root=repo,
        )
        assert not result2.get("ok", True) or result2.get("verdict") == "BLOCK"

    def test_different_evidence_types_accepted(self, repo: Path) -> None:
        """S1: all three evidence types work."""
        for evidence_key in ("finalize_commit_hash", "finalize_return_ref", "owner_verification_ref"):
            # Reset: move card back to claimed
            completed = repo / "5_tasks" / "queue" / "completed" / "aipos-test-001.md"
            claimed = repo / "5_tasks" / "queue" / "claimed" / "aipos-test-001.md"
            if completed.exists():
                # Rewrite the card as claimed
                completed.unlink()
            if not claimed.exists():
                claimed.write_text(
                    "---\n"
                    "task_id: AIPOS-TEST-001\n"
                    "title: Test task for close verb\n"
                    "project: lybra\n"
                    "status: claimed\n"
                    "claimed_by: exec.test\n"
                    "claimed_at: '2026-07-30T12:00:00Z'\n"
                    "claim_id: claim_AIPOS-TEST-001_20260730_120000Z_exec-test\n"
                    "active_session_id: session_AIPOS-TEST-001_20260730_120000Z_exec-test\n"
                    "assigned_to: exec.lybra.kiwiai-dev\n"
                    "agent_instance: exec.lybra.kiwiai-dev\n"
                    "context_bundle: exec.lybra.kiwiai-dev\n"
                    "task_mode: code\n"
                    "priority: high\n"
                    "created_by: advisor.test\n"
                    "needs_owner: false\n"
                    "output_target: tools/\n"
                    "artifact_policy: formal_write\n"
                    "---\n"
                    "# Test task\n",
                    encoding="utf-8",
                )
            # Remove any existing closure records
            closure_dir = repo / "5_tasks" / "records" / "closures" / "AIPOS-TEST-001"
            if closure_dir.exists():
                shutil.rmtree(str(closure_dir))

            evidence = {evidence_key: f"test_ref_{evidence_key}"}
            result = close_task(
                task_id="AIPOS-TEST-001",
                actor="exec.test",
                closure_evidence=evidence,
                dry_run=False,
                repo_root=repo,
            )
            assert result.get("ok", True), f"Failed with evidence type: {evidence_key}"


class TestAutoCloseAuditCards:
    """S2: audit-derived cards <ID>R auto-close with main card."""

    def test_audit_r_card_auto_closed(self, repo: Path) -> None:
        """When main card is closed, its <ID>R audit card is auto-closed if claimed."""
        # Create an audit-derived R card in claimed/
        r_card = repo / "5_tasks" / "queue" / "claimed" / "aipos-test-001r.md"
        r_card.write_text(
            "---\n"
            "task_id: AIPOS-TEST-001R\n"
            "title: Audit of test task\n"
            "project: lybra\n"
            "status: claimed\n"
            "claimed_by: auditor.test\n"
            "claimed_at: '2026-07-30T14:00:00Z'\n"
            "assigned_to: auditor.kiwiai-dev\n"
            "context_bundle: auditor.kiwiai-dev\n"
            "task_mode: audit\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# Audit card\n",
            encoding="utf-8",
        )

        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=repo,
        )
        assert result.get("ok", True)
        data = result.get("data", {})
        auto_closed = data.get("auto_closed_audit_cards", [])
        assert "AIPOS-TEST-001R" in auto_closed

        # R card should now be in completed/
        r_completed = repo / "5_tasks" / "queue" / "completed" / "aipos-test-001r.md"
        assert r_completed.exists()


class TestGovernanceAccountInspection:
    """AIPOS-289: governance account inspection (decision_log + stage_archive)."""

    def test_missing_decision_log_entry_warns(self, tmp_path: Path) -> None:
        """S1: close without decision_log entry -> WARN in closure record."""
        root = tmp_path
        queue_root = root / "5_tasks" / "queue"
        for state in ("pending", "claimed", "completed", "blocked"):
            (queue_root / state).mkdir(parents=True, exist_ok=True)
        (root / "5_tasks" / "records" / "returns" / "AIPOS-TEST-002").mkdir(parents=True, exist_ok=True)
        (root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)
        (root / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)
        (root / "stage_archive").mkdir(parents=True, exist_ok=True)
        (root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

        # decision_log exists but DOES NOT mention AIPOS-TEST-002
        decision_log = root / "governance" / "decision_log" / "2026-08.md"
        decision_log.write_text("# August 2026 Decisions\n\nNo mention of TEST-002.\n", encoding="utf-8")
        stage_file = root / "stage_archive" / "2026-08-01_test.md"
        stage_file.write_text("# Stage\n", encoding="utf-8")

        task_card = queue_root / "claimed" / "aipos-test-002.md"
        task_card.write_text(
            "---\n"
            "task_id: AIPOS-TEST-002\n"
            "title: Task without decision log entry\n"
            "project: lybra\n"
            "status: claimed\n"
            "claimed_by: exec.test\n"
            "claimed_at: '2026-07-30T12:00:00Z'\n"
            "claim_id: claim_AIPOS-TEST-002_20260730_120000Z_exec-test\n"
            "active_session_id: session_AIPOS-TEST-002_20260730_120000Z_exec-test\n"
            "assigned_to: exec.lybra.kiwiai-dev\n"
            "context_bundle: exec.lybra.kiwiai-dev\n"
            "task_mode: code\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# No decision log\n",
            encoding="utf-8",
        )
        return_record = root / "5_tasks" / "records" / "returns" / "AIPOS-TEST-002" / "return_test.md"
        return_record.write_text(
            "---\n"
            "record_type: return_record\n"
            "task_id: AIPOS-TEST-002\n"
            "return_id: return_test\n"
            "returned_at: '2026-07-30T13:00:00Z'\n"
            "---\n"
            "# Return\n",
            encoding="utf-8",
        )

        result = close_task(
            task_id="AIPOS-TEST-002",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=root,
        )
        assert result.get("ok", True)
        warnings = result.get("warnings", [])
        assert any("decision_log" in str(w).lower() and "AIPOS-TEST-002" in str(w) for w in warnings)

        # Closure record should contain the warning
        records = load_records(root)
        closures = records.get("task_closures", {}).get("AIPOS-TEST-002", [])
        assert len(closures) == 1
        closure_warnings = closures[0].get("warnings", [])
        assert any("decision_log" in str(w).lower() for w in closure_warnings)

    def test_stale_stage_archive_warns(self, tmp_path: Path) -> None:
        """S2: close when stage_archive/ is stale -> WARN in closure record."""
        root = tmp_path
        queue_root = root / "5_tasks" / "queue"
        for state in ("pending", "claimed", "completed", "blocked"):
            (queue_root / state).mkdir(parents=True, exist_ok=True)
        (root / "5_tasks" / "records" / "returns" / "AIPOS-TEST-003").mkdir(parents=True, exist_ok=True)
        (root / "5_tasks" / "records" / "closures").mkdir(parents=True, exist_ok=True)
        (root / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)
        (root / "stage_archive").mkdir(parents=True, exist_ok=True)
        (root / "project.json").write_text('{"project": "lybra"}\n', encoding="utf-8")

        decision_log = root / "governance" / "decision_log" / "2026-08.md"
        decision_log.write_text("# August 2026 Decisions\n\n## AIPOS-TEST-003\n\nDecision entry.\n", encoding="utf-8")
        
        # Create a stage_archive file and backdate it by 60 days
        import time
        stage_file = root / "stage_archive" / "old_stage.md"
        stage_file.write_text("# Old Stage\n", encoding="utf-8")
        old_time = time.time() - (60 * 86400)  # 60 days ago
        import os
        os.utime(str(stage_file), (old_time, old_time))

        task_card = queue_root / "claimed" / "aipos-test-003.md"
        task_card.write_text(
            "---\n"
            "task_id: AIPOS-TEST-003\n"
            "title: Task with stale stage_archive\n"
            "project: lybra\n"
            "status: claimed\n"
            "claimed_by: exec.test\n"
            "claimed_at: '2026-07-30T12:00:00Z'\n"
            "claim_id: claim_AIPOS-TEST-003_20260730_120000Z_exec-test\n"
            "active_session_id: session_AIPOS-TEST-003_20260730_120000Z_exec-test\n"
            "assigned_to: exec.lybra.kiwiai-dev\n"
            "context_bundle: exec.lybra.kiwiai-dev\n"
            "task_mode: code\n"
            "priority: high\n"
            "created_by: advisor.test\n"
            "needs_owner: false\n"
            "output_target: tools/\n"
            "artifact_policy: formal_write\n"
            "---\n"
            "# Stale stage\n",
            encoding="utf-8",
        )
        return_record = root / "5_tasks" / "records" / "returns" / "AIPOS-TEST-003" / "return_test.md"
        return_record.write_text(
            "---\n"
            "record_type: return_record\n"
            "task_id: AIPOS-TEST-003\n"
            "return_id: return_test\n"
            "returned_at: '2026-07-30T13:00:00Z'\n"
            "---\n"
            "# Return\n",
            encoding="utf-8",
        )

        result = close_task(
            task_id="AIPOS-TEST-003",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=root,
        )
        assert result.get("ok", True)
        warnings = result.get("warnings", [])
        assert any("stage_archive" in str(w).lower() and "stale" in str(w).lower() for w in warnings)

        # Closure record should contain the warning
        records = load_records(root)
        closures = records.get("task_closures", {}).get("AIPOS-TEST-003", [])
        assert len(closures) == 1
        closure_warnings = closures[0].get("warnings", [])
        assert any("stage_archive" in str(w).lower() for w in closure_warnings)

    def test_valid_governance_no_warnings(self, repo: Path) -> None:
        """S4: close with valid governance accounts -> no WARN (zero regression)."""
        result = close_task(
            task_id="AIPOS-TEST-001",
            actor="exec.test",
            closure_evidence={"finalize_commit_hash": "abc123"},
            dry_run=False,
            repo_root=repo,
        )
        assert result.get("ok", True)
        data = result.get("data", {})
        governance_warnings = data.get("governance_warnings", [])
        assert len(governance_warnings) == 0, f"Expected no governance warnings, got: {governance_warnings}"

        # Closure record should have no warnings field or empty warnings
        records = load_records(repo)
        closures = records.get("task_closures", {}).get("AIPOS-TEST-001", [])
        assert len(closures) == 1
        closure_warnings = closures[0].get("warnings", [])
        assert len(closure_warnings) == 0
