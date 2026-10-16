from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_routes, dispatch_api_request


class ControlledExecuteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.write_task()
        self.routes = _api_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self) -> None:
        path = self.repo_root / "5_tasks/queue/pending/example_task.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    "task_id: EXAMPLE-001",
                    "title: Example Task",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: web/board/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: web/board/",
                    "memory_scope: web board tests",
                    "---",
                    "Task body",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def data_paths(self) -> list[str]:
        roots = [
            self.repo_root / "5_tasks/queue",
            self.repo_root / "5_tasks/drafts",
            self.repo_root / "5_tasks/records",
            self.repo_root / "5_tasks/orchestration",
        ]
        values: list[str] = []
        for root in roots:
            if root.exists():
                values.extend(path.relative_to(self.repo_root).as_posix() for path in root.rglob("*"))
        return sorted(values)

    def assert_board_read_only(self, paths: list[str]) -> None:
        """AIPOS-F171 件③(gap #155): 看板写接口全部下线 —— 同一路由 POST 一律 405 + BOARD_READ_ONLY(文案读 verbs.schema
        board_readonly), 不调用任何写实现, 工作区零写入。"""
        root = Path(self.repo_root)
        def _snapshot() -> list[tuple[str, int]]:
            return sorted((p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file())
        before = _snapshot()
        for path in paths:
            status, data = dispatch_api_request(method="POST", path=path, routes=_api_routes(root))
            print(f"[F171 read-only] POST {path} -> {status} {data.get('error')} {data.get('message')}")
            self.assertEqual(status, 405, path)
            self.assertEqual(data["error"], "BOARD_READ_ONLY", path)
            self.assertFalse(data["ok"])
        self.assertEqual(before, _snapshot(), "read-only board must write nothing")

    def test_execute_dry_run_claim_returns_token_without_writing(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run'])

    def test_execute_confirm_moves_claim_after_valid_dry_run(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run', '/api/execute/confirm'])

    def draft_payload(self, task_id: str = "AIPOS-56-DRAFT") -> dict[str, object]:
        return {
            "frontmatter": {
                "task_id": task_id,
                "title": "Draft From UI",
                "project": "ai-project-os",
                "assigned_to": "dev.codex.local",
                "agent_instance": "dev.codex.local",
                "context_bundle": "dev.codex.local",
                "task_mode": "code",
                "model_tier": "L2",
                "priority": "medium",
                "status": "pending",
                "created_by": "dev.codex.local",
                "needs_owner": False,
                "output_target": "web/board/",
                "artifact_policy": "formal_write",
                "task_type": "one_shot",
                "polling_mode": "agent_polling",
                "claim_policy": "assigned_agent_only",
                "report_mode": "forum_reply",
                "recurrence": "none",
            },
            "body": "## Goal\n\nDraft creation UI test.\n",
        }

    def write_draft(self, task_id: str = "AIPOS-57-PUBLISH") -> Path:
        slug = task_id.lower()
        path = self.repo_root / f"5_tasks/drafts/{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    "title: Draft Publish From UI",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: dev.codex.local",
                    "needs_owner: false",
                    "output_target: web/board/",
                    "artifact_policy: formal_write",
                    "task_type: one_shot",
                    "polling_mode: agent_polling",
                    "claim_policy: assigned_agent_only",
                    "report_mode: forum_reply",
                    "recurrence: none",
                    "artifact_scope: web/board/",
                    "memory_scope: web board tests",
                    "---",
                    "## Goal",
                    "",
                    "Draft publish UI test.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def event_payload(self, event_id: str = "evt_web_controlled_001") -> dict[str, object]:
        return {
            "event_id": event_id,
            "orchestration_id": "orch_web_controlled",
            "event_type": "planner_tick_completed",
            "timestamp": "2026-05-10T00:00:00Z",
            "actor": "dev.codex.local",
            "source": "web_board_persistence_test",
            "related_task_id": "PARENT-001",
            "related_iteration_id": "iter_web_controlled_001",
            "severity": "info",
            "summary": "Planner tick completed.",
            "details": "Controlled persistence UI dry-run.",
            "forum_thread_ref": "forum://orch_web_controlled",
            "refs": ["forum://orch_web_controlled"],
        }

    def iteration_payload(self, iteration_id: str = "iter_web_controlled_001") -> dict[str, object]:
        return {
            "iteration_id": iteration_id,
            "orchestration_id": "orch_web_controlled",
            "iteration_number": 1,
            "planner_agent": "dev_codex",
            "planner_agent_instance": "dev.codex.local",
            "planner_model_tier": "L3",
            "started_at": "2026-05-10T00:00:00Z",
            "ended_at": "2026-05-10T00:05:00Z",
            "forum_thread_ref": "forum://orch_web_controlled",
            "parent_task_id": "PARENT-001",
            "input_refs": ["forum://orch_web_controlled"],
            "observed_queue_state": "No open subtasks.",
            "observed_subtask_summary": "No subtasks yet.",
            "decisions": ["Continue planning."],
            "created_subtasks": [],
            "updated_recommendations": [],
            "failure_observations": [],
            "quota_observations": [],
            "needs_owner_reasons": [],
            "next_check_after": "manual",
            "verdict": "continue",
            "owner_decision_required": False,
            "audit_handoff_required": False,
        }

    def owner_decision_payload(self, decision_id: str = "decision-web-001") -> dict[str, object]:
        return {
            "decision_id": decision_id,
            "decision_type": "external_intake_review",
            "decision_status": "approved",
            "decided_at": "2026-05-21T00:00:00Z",
            "decided_by_ref": "owner",
            "captured_by": "owner",
            "capture_surface": "board",
            "decision_summary": "Approve external intake for review.",
            "decision_rationale": "Owner approved from Board.",
            "applies_to": {
                "project": "client_alpha",
                "task_id": "EXT-CLIENT-ALPHA-001",
                "draft_path": "5_tasks/drafts/external_intake/example.md",
                "external_ref": "chat://message/1",
            },
            "approval_scope": {
                "operation": "owner_decision_record",
                "authority_boundary": "Board controlled execute owner_decision_record only",
                "allowed_next_action": "review_external_intake_draft",
                "expires_at": "2099-01-01T00:00:00Z",
            },
            "owner_approval_evidence": {
                "evidence_id": "evidence-web-001",
                "source_tag": "board",
                "client_tag": "client_alpha",
                "external_ref": "chat://message/1",
                "approval_actor_ref": "owner",
                "approval_timestamp": "2026-05-21T00:00:00Z",
                "approval_intent": "record_owner_decision",
                "evidence_hash": "board-ui:evidence-web-001",
                "evidence_ref": "chat://message/1",
                "captured_by": "owner",
                "capture_method": "board_controlled_execute",
                "redaction_status": "redacted_or_normalized",
                "refs": ["chat://message/1", "5_tasks/drafts/external_intake/example.md"],
            },
            "refs": ["chat://message/1", "5_tasks/drafts/external_intake/example.md"],
            "capability_scope": {
                "token_ref": "board_owner_decision_record",
                "operations": ["owner_decision_record"],
                "projects": ["client_alpha"],
                "expires_at": "2099-01-01T00:00:00Z",
                "evidence_ref": "chat://message/1",
            },
        }

    def test_execute_dry_run_draft_create_returns_token_without_writing(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run'])

    def test_execute_confirm_creates_draft_after_valid_dry_run(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run', '/api/execute/confirm'])

    def test_execute_dry_run_draft_publish_returns_token_without_writing(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run'])

    def test_execute_confirm_publishes_draft_after_valid_dry_run(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run', '/api/execute/confirm'])

    def test_execute_dry_run_orchestration_event_append_returns_token_without_writing(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run'])

    def test_execute_confirm_orchestration_event_append_requires_owner_confirmation(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run', '/api/execute/confirm'])

    def test_execute_confirm_planner_iteration_append_after_valid_dry_run(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run', '/api/execute/confirm'])

    def test_execute_dry_run_owner_decision_record_returns_token_without_writing(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run'])

    def test_execute_confirm_owner_decision_record_writes_record_only(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run', '/api/execute/confirm'])

    def test_execute_dry_run_blocks_non_claim_draft_create_or_draft_publish_operations(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/dry-run'])

    def test_execute_confirm_missing_token_blocks(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/execute/confirm'])


if __name__ == "__main__":
    unittest.main()
