"""AIPOS-273F1: End-to-end tests for owner verification record routes.

Tests the full HTTP → controlled-execute → disk write flow.
P0 fix validation: approve/reject must write actual files to disk.

AIPOS-F171 件③(gap #155, Owner 10-16: the Board is read-only): the Board's verify approve/reject write routes are offline
(cross-project write + weak auth). The three route tests now assert POST /api/verify/approve|reject → 405 BOARD_READ_ONLY and
zero writes; the controlled-execute plumbing test (backend, not the Board) is unchanged.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import (
    execute_dry_run,
    record_owner_verification,
)
from web.board.app import _api_routes, dispatch_api_request


class TestOwnerVerificationE2E(unittest.TestCase):
    """End-to-end tests for owner verification HTTP routes -> disk writes."""

    def setUp(self) -> None:
        """Create isolated test repo."""
        self.test_dir = Path(tempfile.mkdtemp(prefix="lybra_test_verify_e2e_"))
        # Create minimal workspace structure for _resolve_repo_root
        self.queue_dir = self.test_dir / "5_tasks" / "queue"
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir = self.test_dir / "5_tasks" / "records" / "owner_verifications"
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def _assert_read_only(self, route: str) -> None:
        def _snapshot() -> list[str]:
            return sorted(p.relative_to(self.test_dir).as_posix() for p in self.test_dir.rglob("*"))
        before = _snapshot()
        status, data = dispatch_api_request(method="POST", path=route, routes=_api_routes(self.test_dir))
        print(f"[F171 read-only] POST {route} -> {status} {data.get('error')} {data.get('message')}")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"], "BOARD_READ_ONLY")
        self.assertEqual(before, _snapshot(), "read-only board must write nothing")

    def tearDown(self) -> None:
        """Clean up test repo."""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_approve_route_writes_record_to_disk(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 原断言看板核验写路由落盘; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self._assert_read_only("/api/verify/approve")

    def test_reject_route_writes_record_to_disk(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 原断言看板核验写路由落盘; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self._assert_read_only("/api/verify/reject")

    def test_reject_without_reason_blocks(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 原断言看板核验写路由落盘; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self._assert_read_only("/api/verify/reject")

    def test_controlled_execute_flow_approve(self) -> None:
        """Verify controlled-execute plumbing: dry-run → confirm → write."""
        task_id = "TEST-CE-APPROVE"
        actor = "test_owner"

        verification_payload = {
            "task_id": task_id,
            "decision": "approve",
            "reason": "",
            "decided_via": "web_session",
        }

        # Step 1: dry-run
        dry_run_response = record_owner_verification(
            verification_payload,
            dry_run=True,
            repo_root=self.test_dir,
            actor=actor,
        )

        self.assertTrue(dry_run_response.get("ok"), f"Dry-run failed: {dry_run_response}")
        self.assertIn(dry_run_response.get("verdict"), ["OK", "PASS"])
        dry_run_id = dry_run_response.get("dry_run_id")
        self.assertIsNotNone(dry_run_id, "No dry_run_id returned")

        # Step 2: confirm with owner token
        from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
        execute_response = execute_dry_run(
            dry_run_id,
            actor,
            owner_confirmation_token=OWNER_CONFIRMATION_TOKEN,
            repo_root=self.test_dir,
        )

        self.assertTrue(execute_response.get("ok"), f"Execute failed: {execute_response}")
        self.assertIn(execute_response.get("verdict"), ["OK", "PASS"])
        self.assertTrue(execute_response.get("data", {}).get("wrote"), "No write reported")

        # Step 3: verify file on disk
        task_records_dir = self.records_dir / task_id
        self.assertTrue(task_records_dir.exists())
        record_files = list(task_records_dir.glob("*.md"))
        self.assertEqual(len(record_files), 1)


if __name__ == "__main__":
    unittest.main()
