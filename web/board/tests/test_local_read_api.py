from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_post_routes, _api_routes, dispatch_api_request

WEB_ROOT = Path(__file__).resolve().parents[1]


class LocalReadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "0_control_plane" / "agents").mkdir(parents=True, exist_ok=True)
        self.routes = _api_routes(self.repo_root)
        self.post_routes = _api_post_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_route_returns_json(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/health", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertIn("ok", data)
        self.assertIn("verdict", data)
        self.assertIn("remote_dogfood_readiness", data["data"])
        self.assertFalse(data["data"]["remote_dogfood_readiness"]["live_agent_connection_enabled"])
        self.assertIn("/api/governance", data["data"]["remote_dogfood_readiness"]["read_paths"])
        self.assertIn("/api/orchestration/summary", data["data"]["remote_dogfood_readiness"]["read_paths"])

    def test_read_routes_return_json_envelopes(self) -> None:
        for path in (
            "/api/queue",
            "/api/needs-owner",
            "/api/validate",
            "/api/governance",
            "/api/agents",
            "/api/drafts",
            "/api/planner-drafts/review",
            "/api/owner-decisions/review",
            "/api/records",
        ):
            status, data = dispatch_api_request(method="GET", path=path, routes=self.routes)
            self.assertEqual(status, 200)
            self.assertIn("ok", data)
            self.assertIn("verdict", data)

    def test_governance_route_reads_lybra_project_docs_without_writing(self) -> None:
        project_dir = self.repo_root / "2_projects" / "lybra"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "decision_log.md").write_text("# Decision Log\n\n## DL-1\nAccepted.\n", encoding="utf-8")
        (project_dir / "project_status.md").write_text("# Status\n\nHealthy.\n", encoding="utf-8")
        (project_dir / "roadmap.md").write_text("# Roadmap\n\nNext.\n", encoding="utf-8")
        before = self.data_paths()

        status, data = dispatch_api_request(method="GET", path="/api/governance", routes=self.routes)
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "get_governance")
        self.assertEqual(data["verdict"], "PASS")
        self.assertEqual(data["summary"]["documents_present"], 3)
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["raw_json_default_visible"])
        self.assertEqual([doc["path"] for doc in data["data"]["documents"]], [
            "2_projects/lybra/decision_log.md",
            "2_projects/lybra/project_status.md",
            "2_projects/lybra/roadmap.md",
        ])
        self.assertIn("DL-1", data["data"]["documents"][0]["excerpt"])
        self.assertEqual(before, after)

    def test_governance_route_handles_missing_files_as_warn(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/governance", routes=self.routes)

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["verdict"], "WARN")
        self.assertEqual(data["summary"]["documents_missing"], 3)
        self.assertEqual(len(data["warnings"]), 3)
        self.assertFalse(data["data"]["documents"][0]["exists"])

    def test_unsupported_method_rejected(self) -> None:
        status, data = dispatch_api_request(method="POST", path="/api/queue", routes=self.routes)
        self.assertEqual(status, 405)
        self.assertEqual(data.get("error"), "METHOD_NOT_ALLOWED")

    def test_execute_dry_run_route_requires_post(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/execute/dry-run", routes=self.routes)
        self.assertEqual(status, 404)
        self.assertEqual(data.get("error"), "NOT_FOUND")

    def test_execute_dry_run_post_returns_envelope(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/execute/dry-run",
            routes=self.routes,
            post_routes=self.post_routes,
            body={},
        )
        self.assertEqual(status, 200)
        self.assertIn("ok", data)
        self.assertIn("verdict", data)

    def test_parent_requirement_preview_returns_envelope_without_writing(self) -> None:
        before = sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))
        status, data = dispatch_api_request(
            method="POST",
            path="/api/parent-requirement/preview",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "title": "Planner Loop UI",
                "project": "ai-project-os",
                "owner_goal": "Create a parent requirement entry point.",
                "forum_thread_ref": "forum://aipos/59",
                "planner_agent": "dev_codex",
                "planner_agent_instance": "dev.codex.local",
                "planner_runtime_profile": "local_process",
                "planner_model_tier": "L3",
            },
        )
        after = sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "parent_requirement_preview")
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["execute_allowed"])
        self.assertEqual(data["data"]["parent_requirement"]["min_planner_model_tier"], "L3")
        self.assertEqual(before, after)

    def test_parent_requirement_preview_requires_l3_or_l4(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/parent-requirement/preview",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "title": "Planner Loop UI",
                "owner_goal": "Create a parent requirement entry point.",
                "forum_thread_ref": "forum://aipos/59",
                "planner_model_tier": "L2",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")

    def test_planner_tick_preview_returns_forum_visible_metadata_without_writing(self) -> None:
        before = sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-tick/preview",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260503_planner_loop",
                "parent_task_id": "REQ-20260503-planner-loop-PARENT",
                "forum_thread_ref": "forum://aipos/60",
                "planner_agent": "dev_codex",
                "planner_agent_instance": "dev.codex.local",
                "planner_model_tier": "L3",
                "iteration_number": "1",
                "decision": "needs_owner",
                "decision_reason": "Architecture route split requires Owner approval.",
                "next_expected_action": "Owner decides whether to publish subtasks.",
                "inputs_read": "parent requirement\nqueue summary",
                "observations": "No running subtasks.",
                "needs_owner_reasons": "architecture_route_split",
            },
        )
        after = sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_tick_preview")
        self.assertEqual(data["verdict"], "NEEDS_OWNER")
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["forum_backend_enabled"])
        self.assertFalse(data["data"]["planner_runtime_launch_enabled"])
        self.assertFalse(data["data"]["orchestration_writer_enabled"])
        self.assertFalse(data["execute_allowed"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["data"]["planner_iteration"]["verdict"], "needs_owner")
        self.assertTrue(data["data"]["visible_report"]["owner_decision_required"])
        self.assertIn("planner_tick_started", [event["event_type"] for event in data["data"]["event_log_preview"]])
        self.assertIn("planner_verdict_recorded", [event["event_type"] for event in data["data"]["event_log_preview"]])
        self.assertIn("needs_owner_raised", [event["event_type"] for event in data["data"]["event_log_preview"]])
        self.assertEqual(before, after)

    def test_planner_tick_preview_requires_l3_or_l4(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-tick/preview",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260503_planner_loop",
                "parent_task_id": "REQ-20260503-planner-loop-PARENT",
                "forum_thread_ref": "forum://aipos/60",
                "planner_model_tier": "L2",
                "decision": "continue",
                "decision_reason": "Continue observing.",
                "next_expected_action": "Run another tick later.",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")

    def test_planner_tick_preview_requires_aipos54_verdict(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-tick/preview",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260503_planner_loop",
                "parent_task_id": "REQ-20260503-planner-loop-PARENT",
                "forum_thread_ref": "forum://aipos/60",
                "planner_model_tier": "L3",
                "decision": "launch_runtime",
                "decision_reason": "Invalid runtime launch attempt.",
                "next_expected_action": "Launch runtime.",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")

    def test_manual_planner_tick_flow_preview_stops_on_critical_fork_without_writing(self) -> None:
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-tick/manual-flow/preview",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260509_manual_tick",
                "parent_task_id": "REQ-20260509-manual-tick-PARENT",
                "forum_thread_ref": "forum://aipos/74",
                "planner_agent": "dev_codex",
                "planner_agent_instance": "dev.codex.local",
                "planner_model_tier": "L3",
                "iteration_number": "2",
                "decision": "continue",
                "decision_reason": "Architecture route split requires Owner approval before continuing.",
                "next_expected_action": "Owner decides the route before any publish.",
                "inputs_read": "parent requirement\norchestration summary\nplanner timeline",
                "observations": "No autonomous runtime is active.",
                "publish_candidates": "AIPOS-74R_audit_minimal_manual_planner_tick_flow.md",
                "audit_handoff_needed": True,
                "stop_condition_hits": "architecture route split",
            },
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_tick_manual_flow_preview")
        self.assertEqual(data["verdict"], "NEEDS_OWNER")
        self.assertTrue(data["dry_run"])
        self.assertTrue(data["data"]["manual_flow"])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["planner_iteration_append_enabled"])
        self.assertFalse(data["data"]["forum_event_append_enabled"])
        self.assertFalse(data["data"]["planner_runtime_launch_enabled"])
        self.assertFalse(data["data"]["queue_mutation_enabled"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["planned_moves"], [])
        self.assertIn("architecture", data["summary"]["critical_fork_hits"])
        self.assertTrue(data["summary"]["owner_decision_required"])
        self.assertIn("critical fork requires Owner decision: architecture", data["needs_owner_reasons"])
        self.assertEqual(data["data"]["visible_report"]["manual_flow_next_step"], "stop_for_owner")
        self.assertEqual(before, after)

    def write_planner_draft(self, *, publish_status: str = "approved_for_publish", extra_lines: list[str] | None = None) -> Path:
        path = self.repo_root / "5_tasks/drafts/aipos-61-planner-draft.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            "task_id: AIPOS-61-PLANNER-DRAFT",
            "title: Planner Draft Review Test",
            "project: ai-project-os",
            "assigned_to: dev.codex.local",
            "agent_instance: dev.codex.local",
            "context_bundle: dev.codex.local",
            "task_mode: code",
            "model_tier: L2",
            "priority: medium",
            "status: pending",
            "created_by: dev_codex",
            "needs_owner: false",
            "output_target: web/board/",
            "artifact_policy: formal_write",
            "task_type: one_shot",
            "polling_mode: agent_polling",
            "claim_policy: assigned_agent_only",
            "report_mode: forum_reply",
            "recurrence: none",
            "session_policy: single_task_session",
            "context_isolation: strict",
            "artifact_scope: web/board/",
            "memory_scope: web board tests",
            "draft_id: draft_aipos_61_planner_draft",
            "draft_status: planner_draft",
            "draft_created_by: dev_codex",
            "draft_created_at: 2026-05-04T00:00:00Z",
            "draft_source: planner",
            f"publish_status: {publish_status}",
            "publish_target: 5_tasks/queue/pending/",
            "draft_publish_target: 5_tasks/queue/pending/",
            "requirement_id: REQ-20260504-planner-draft-review",
            "orchestration_id: orch_ai_project_os_20260504_planner_draft_review",
            "parent_task_id: REQ-20260504-planner-draft-review-PARENT",
            "created_by_planner: true",
            "planner_agent: dev_codex",
            "planner_agent_instance: dev.codex.local",
            "planner_model_tier: L3",
            "planner_iteration_id: iter_20260504_planner_draft_review_001",
            "iteration: 1",
            "subtask_sequence: 1",
            "subtask_type: coding",
            "depends_on: AIPOS-60",
            "reviewer: dev_claude",
            "audit_by: cc_glm",
            "forum_thread_ref: forum://aipos/61",
        ]
        lines.extend(extra_lines or [])
        lines.extend(
            [
                "---",
                "## Goal",
                "",
                "Review planner draft publish readiness.",
                "",
            ]
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

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

    def event_payload(self) -> dict[str, object]:
        return {
            "event_id": "evt_web_controlled_001",
            "orchestration_id": "orch_web_controlled",
            "event_type": "planner_verdict_recorded",
            "timestamp": "2026-05-09T10:00:00Z",
            "actor": "dev.codex.local",
            "source": "web_board_test",
            "related_task_id": "PARENT-001",
            "related_iteration_id": "iter_web_controlled_001",
            "severity": "info",
            "summary": "Planner verdict recorded.",
            "details": "Continue planning.",
            "forum_thread_ref": "forum://orch_web_controlled",
            "refs": ["forum://orch_web_controlled"],
        }

    def iteration_payload(self) -> dict[str, object]:
        return {
            "iteration_id": "iter_web_controlled_001",
            "orchestration_id": "orch_web_controlled",
            "iteration_number": 1,
            "planner_agent": "dev_codex",
            "planner_agent_instance": "dev.codex.local",
            "planner_model_tier": "L3",
            "started_at": "2026-05-09T10:00:00Z",
            "ended_at": "2026-05-09T10:04:00Z",
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

    def test_planner_drafts_review_route_lists_metadata_without_writing(self) -> None:
        self.write_planner_draft(publish_status="needs_owner")
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path="/api/planner-drafts/review",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_drafts_review")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertTrue(data["data"]["review_only"])
        self.assertFalse(data["data"]["controlled_mutation_allowed"])
        self.assertTrue(data["data"]["publish_execute_disabled"])
        self.assertTrue(data["data"]["mobile_responsive_required"])
        self.assertEqual(data["summary"]["planner_created_total"], 1)
        self.assertEqual(data["summary"]["needs_owner"], 1)
        draft = data["data"]["drafts"][0]
        self.assertEqual(draft["task_id"], "AIPOS-61-PLANNER-DRAFT")
        self.assertEqual(draft["assigned_to"], "dev.codex.local")
        self.assertEqual(draft["reviewer"], "dev_claude")
        self.assertEqual(draft["audit_by"], "cc_glm")
        self.assertEqual(draft["depends_on"], "AIPOS-60")
        self.assertEqual(draft["review_status"], "needs_owner")
        self.assertTrue(draft["owner_gate"])
        self.assertFalse(draft["publish_ready"])
        self.assertTrue(draft["publish_execute_disabled"])
        self.assertEqual(before, after)

    def test_append_event_controlled_execute_dry_run_returns_token_without_writing(self) -> None:
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/execute/dry-run",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"operation": "orchestration_event_append", "actor": "dev.codex.local", "payload": self.event_payload()},
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "orchestration_event_append")
        self.assertTrue(data["execute_allowed"])
        self.assertTrue(data["owner_confirmation_required"])
        self.assertIn("dry_run_id", data)
        self.assertEqual(data["data"]["target_path"], "5_tasks/orchestration/orch_web_controlled/orchestration_events.md")
        self.assertEqual(before, after)

    def test_append_iteration_controlled_execute_dry_run_returns_token_without_writing(self) -> None:
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/execute/dry-run",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"operation": "planner_iteration_append", "actor": "dev.codex.local", "payload": self.iteration_payload()},
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_iteration_append")
        self.assertTrue(data["execute_allowed"])
        self.assertTrue(data["owner_confirmation_required"])
        self.assertIn("dry_run_id", data)
        self.assertEqual(data["data"]["target_path"], "5_tasks/orchestration/orch_web_controlled/planner_iterations.md")
        self.assertEqual(before, after)

    def test_context_pack_preview_route_is_read_only(self) -> None:
        task_path = self.write_planner_draft()
        (self.repo_root / "3_context_bundles/examples").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles/examples/dev.codex.local.md").write_text(
            "\n".join(
                [
                    "role_instance: dev.codex.local",
                    "environment: local_wsl_ubuntu",
                    "description: local engineering agent",
                    "allowed_task_modes:",
                    "  - code",
                    "preferred_model_tiers:",
                    "  - L3",
                    "allowed_model_tiers:",
                    "  - L3",
                    "memory_access:",
                    "  - 2_projects/lybra/",
                    "output_target:",
                    "  - repository",
                    "escalation_rules:",
                    "  - preserve Owner gates",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path=f"/api/context-pack/preview?path={task_path.relative_to(self.repo_root).as_posix()}",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "context_pack_preview")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["data"]["task"]["task_id"], "AIPOS-61-PLANNER-DRAFT")
        self.assertTrue(data["data"]["context_bundle"]["found"])
        self.assertEqual(before, after)

    def test_planner_draft_review_returns_publish_readiness_without_writing(self) -> None:
        draft_path = self.write_planner_draft()
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-draft/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"path": draft_path.relative_to(self.repo_root).as_posix(), "actor": "dev.codex.local"},
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_draft_review")
        self.assertEqual(data["verdict"], "PASS")
        self.assertFalse(data["execute_allowed"])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertTrue(data["data"]["review_only"])
        self.assertFalse(data["data"]["controlled_execute_expanded"])
        self.assertTrue(data["data"]["handoff_to_draft_publish"]["enabled"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["data"]["publish_preview"]["target_path"], "5_tasks/queue/pending/aipos-61-planner-draft.md")
        self.assertEqual(before, after)

    def test_approved_planner_draft_publish_dry_run_returns_token_without_writing(self) -> None:
        draft_path = self.write_planner_draft()
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-draft/publish/dry-run",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"path": draft_path.relative_to(self.repo_root).as_posix(), "actor": "dev.codex.local"},
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_draft_publish")
        self.assertEqual(data["data"]["controlled_execute_operation"], "draft_publish")
        self.assertTrue(data["data"]["owner_decision_gate"]["clear"])
        self.assertTrue(data["data"]["second_confirmation_required"])
        self.assertTrue(data["execute_allowed"])
        self.assertIn("dry_run_id", data)
        self.assertEqual(data["planned_writes"][0]["path"], "5_tasks/queue/pending/aipos-61-planner-draft.md")
        self.assertEqual(before, after)

    def test_approved_planner_draft_publish_blocks_owner_gate_without_token(self) -> None:
        draft_path = self.write_planner_draft(publish_status="needs_owner")
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-draft/publish/dry-run",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"path": draft_path.relative_to(self.repo_root).as_posix(), "actor": "dev.codex.local"},
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "NEEDS_OWNER")
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertIn("Draft has a pending Owner decision gate.", data["needs_owner_reasons"])

    def test_approved_planner_draft_publish_confirm_uses_existing_controlled_execute(self) -> None:
        draft_path = self.write_planner_draft()
        _status, dry = dispatch_api_request(
            method="POST",
            path="/api/planner-draft/publish/dry-run",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"path": draft_path.relative_to(self.repo_root).as_posix(), "actor": "dev.codex.local"},
        )
        status, executed = dispatch_api_request(
            method="POST",
            path="/api/execute/confirm",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"dry_run_id": dry["dry_run_id"], "actor": "dev.codex.local", "owner_confirmed": True},
        )

        self.assertEqual(status, 200)
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["operation"], "draft_publish")
        self.assertTrue((self.repo_root / "5_tasks/queue/pending/aipos-61-planner-draft.md").exists())

    def test_planner_draft_review_routes_owner_gate_to_needs_owner(self) -> None:
        draft_path = self.write_planner_draft(publish_status="needs_owner")
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-draft/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"path": draft_path.relative_to(self.repo_root).as_posix()},
        )

        self.assertEqual(status, 200)
        self.assertEqual(data["verdict"], "NEEDS_OWNER")
        self.assertFalse(data["data"]["handoff_to_draft_publish"]["enabled"])
        self.assertIn("Draft has a pending Owner decision gate.", data["needs_owner_reasons"])

    def test_planner_draft_review_blocks_non_planner_draft(self) -> None:
        draft_path = self.write_planner_draft(extra_lines=["draft_source: manual", "created_by_planner: false"])
        status, data = dispatch_api_request(
            method="POST",
            path="/api/planner-draft/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={"path": draft_path.relative_to(self.repo_root).as_posix()},
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertIn("Draft must be marked as planner-created for Planner Draft Review.", data["blocking_reasons"])

    def test_forum_event_review_returns_append_plan_without_writing(self) -> None:
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/forum-event/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260504_forum_event",
                "event_type": "planner_verdict_recorded",
                "severity": "info",
                "actor": "dev.codex.local",
                "source": "web_board_forum_event_review",
                "forum_thread_ref": "forum://aipos/63",
                "related_task_id": "REQ-20260504-forum-event-PARENT",
                "related_iteration_id": "iter_20260504_forum_event_001",
                "summary": "Planner verdict is ready for review.",
                "details": "Review-only event persistence plan.",
                "refs": "forum://aipos/63",
            },
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "forum_event_persistence_review")
        self.assertEqual(data["verdict"], "PASS")
        self.assertFalse(data["execute_allowed"])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["forum_backend_enabled"])
        self.assertFalse(data["data"]["network_posting_enabled"])
        self.assertFalse(data["data"]["controlled_execute_expanded"])
        self.assertTrue(data["data"]["writer_review_only"])
        self.assertTrue(data["data"]["append_plan"]["append_only"])
        self.assertEqual(
            data["data"]["append_plan"]["target_path"],
            "5_tasks/orchestration/orch_ai_project_os_20260504_forum_event/orchestration_events.md",
        )
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(before, after)

    def test_forum_event_review_rejects_invalid_event_type(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/forum-event/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260504_forum_event",
                "event_type": "launch_runtime",
                "severity": "info",
                "actor": "dev.codex.local",
                "forum_thread_ref": "forum://aipos/63",
                "summary": "Invalid event.",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertIn("event_type must be allowed by orchestration_event_log_schema.md", data["blocking_reasons"])


    def write_orchestration_fixture(self, orchestration_id: str = "orch_aipos_69_summary_ui") -> None:
        task_path = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-69-summary-ui.md"
        task_path.write_text(
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-69-SUBTASK",
                    "title: Summary UI fixture",
                    "project: ai-project-os",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: dev_codex",
                    "needs_owner: true",
                    "needs_owner_reasons:",
                    "  - architecture_route_split",
                    "output_target: web/board/",
                    "artifact_policy: formal_write",
                    f"orchestration_id: {orchestration_id}",
                    "parent_task_id: REQ-AIPOS-69-PARENT",
                    "planner_agent: dev_codex",
                    "planner_agent_instance: dev.codex.local",
                    "planner_model_tier: L3",
                    "---",
                    "## Goal",
                    "Fixture task for orchestration summary preview UI.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        orch_dir = self.repo_root / "5_tasks" / "orchestration" / orchestration_id
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "planner_iterations.md").write_text(
            "\n".join(
                [
                    "- iteration_id: iter_aipos_69_002",
                    f"  orchestration_id: {orchestration_id}",
                    "  iteration_number: 2",
                    "  parent_task_id: REQ-AIPOS-69-PARENT",
                    "  planner_agent: dev_codex",
                    "  planner_agent_instance: dev.codex.local",
                    "  planner_model_tier: L3",
                    "  ended_at: 2026-05-08T00:00:00Z",
                    "  verdict: needs_owner",
                    "  owner_decision_required: true",
                    "  needs_owner_reasons:",
                    "    - architecture_route_split",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (orch_dir / "orchestration_events.md").write_text(
            "\n".join(
                [
                    "- event_id: evt_aipos_69_needs_owner",
                    f"  orchestration_id: {orchestration_id}",
                    "  event_type: needs_owner_raised",
                    "  timestamp: 2026-05-08T00:00:00Z",
                    "  severity: needs_owner",
                    "  summary: Owner decision required for architecture route split.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def test_orchestration_summary_preview_route_returns_read_only_summary_without_writing(self) -> None:
        orchestration_id = "orch_aipos_69_summary_ui"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path=f"/api/orchestration-summary?orchestration_id={orchestration_id}",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "orchestration_summary_preview")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["execute_allowed"])
        self.assertEqual(data["summary"]["orchestration_id"], orchestration_id)
        self.assertEqual(data["summary"]["status"], "needs_owner")
        self.assertEqual(data["summary"]["current_iteration"], 2)
        self.assertEqual(data["summary"]["open_subtask_count"], 1)
        self.assertIn("5_tasks/queue/", data["data"]["source_refs"])
        self.assertIn("architecture_route_split", data["summary"]["needs_owner_reasons"])
        self.assertTrue(data["owner_confirmation_required"])
        self.assertEqual(before, after)

    def test_orchestration_summary_dogfood_alias_returns_same_read_only_summary(self) -> None:
        orchestration_id = "orch_aipos_87_summary_alias"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path=f"/api/orchestration/summary?orchestration_id={orchestration_id}",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "orchestration_summary_preview")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["summary"]["orchestration_id"], orchestration_id)
        self.assertEqual(before, after)

    def test_orchestration_summary_preview_route_requires_id(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/orchestration-summary", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertIn("orchestration_id is required", data["blocking_reasons"])

    def test_owner_decisions_review_route_returns_read_only_gate_cards(self) -> None:
        orchestration_id = "orch_aipos_72_owner_gate"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path="/api/owner-decisions/review",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "owner_decisions_review")
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["verdict"], "NEEDS_OWNER")
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertTrue(data["data"]["review_only"])
        self.assertFalse(data["data"]["controlled_mutation_allowed"])
        self.assertFalse(data["data"]["resolution_enabled"])
        self.assertTrue(data["data"]["mobile_responsive_required"])
        self.assertGreaterEqual(data["summary"]["total"], 2)
        decision_types = {row["decision_type"] for row in data["data"]["decision_requests"]}
        self.assertIn("architecture", decision_types)
        self.assertTrue(any(row["source"] == "queue_task" for row in data["data"]["decision_requests"]))
        self.assertTrue(any(row["source"] == "orchestration_event" for row in data["data"]["decision_requests"]))
        self.assertTrue(all(row["review_only"] for row in data["data"]["decision_requests"]))
        self.assertTrue(all(not row["resolution_enabled"] for row in data["data"]["decision_requests"]))
        self.assertEqual(before, after)

    def test_owner_decision_resolution_review_returns_append_plan_without_writing(self) -> None:
        orchestration_id = "orch_aipos_76_owner_resolution"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="POST",
            path="/api/owner-decision/resolve/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "request_id": "timeline:orch_aipos_76_owner_resolution:evt_aipos_69_needs_owner",
                "orchestration_id": orchestration_id,
                "actor": "owner",
                "forum_thread_ref": "forum://aipos/76",
                "evidence_ref": "owner_decision:forum://aipos/76#approved",
                "decision_type": "architecture",
                "related_task_id": "REQ-AIPOS-76-PARENT",
                "related_iteration_id": "iter_aipos_69_002",
                "decision": "approved",
                "decision_reason": "Proceed with the preview-only resolution flow.",
            },
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "owner_decision_resolution_review")
        self.assertEqual(data["verdict"], "PASS")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["planned_moves"], [])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["decision_persistence_enabled"])
        self.assertFalse(data["data"]["controlled_mutation_allowed"])
        self.assertTrue(data["data"]["resolution_review_only"])
        self.assertEqual(data["data"]["event_entry"]["event_type"], "owner_decision_recorded")
        self.assertEqual(data["data"]["append_plan"]["target_path"], f"5_tasks/orchestration/{orchestration_id}/orchestration_events.md")
        self.assertEqual(data["summary"]["decision"], "approved")
        self.assertEqual(data["summary"]["decision_type"], "architecture")
        self.assertTrue(data["summary"]["writer_review_passed"])
        self.assertEqual(before, after)

    def test_owner_decision_resolution_review_requires_evidence(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/owner-decision/resolve/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "request_id": "timeline:orch:evt",
                "orchestration_id": "orch_aipos_76_owner_resolution",
                "actor": "owner",
                "forum_thread_ref": "forum://aipos/76",
                "decision": "approved",
                "decision_reason": "Approve route.",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertIn("evidence_ref is required", data["blocking_reasons"])

    def test_orchestration_timeline_preview_route_returns_read_only_chronological_items(self) -> None:
        orchestration_id = "orch_aipos_70_timeline_ui"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path=f"/api/orchestration-timeline?orchestration_id={orchestration_id}",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "orchestration_timeline_preview")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["execute_allowed"])
        self.assertEqual(data["summary"]["orchestration_id"], orchestration_id)
        self.assertEqual(data["summary"]["timeline_items"], 2)
        self.assertEqual(data["summary"]["planner_iterations"], 1)
        self.assertEqual(data["summary"]["orchestration_events"], 1)
        self.assertEqual(data["summary"]["owner_attention_count"], 2)
        self.assertCountEqual([item["kind"] for item in data["data"]["timeline"]], ["planner_iteration", "orchestration_event"])
        self.assertTrue(any("Owner decision required" in reason for reason in data["needs_owner_reasons"]))
        self.assertTrue(data["owner_confirmation_required"])
        self.assertEqual(before, after)

    def test_orchestration_timeline_dogfood_alias_returns_same_read_only_timeline(self) -> None:
        orchestration_id = "orch_aipos_87_timeline_alias"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path=f"/api/orchestration/timeline?orchestration_id={orchestration_id}",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "orchestration_timeline_preview")
        self.assertTrue(data["dry_run"])
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["summary"]["orchestration_id"], orchestration_id)
        self.assertEqual(before, after)

    def test_orchestration_timeline_preview_route_requires_id(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/orchestration-timeline", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertIn("orchestration_id is required", data["blocking_reasons"])

    def test_planner_loop_mvp_route_returns_single_step_preview_without_writing(self) -> None:
        orchestration_id = "orch_aipos_75_loop_mvp"
        self.write_orchestration_fixture(orchestration_id)
        before = self.data_paths()
        status, data = dispatch_api_request(
            method="GET",
            path=f"/api/planner-loop/mvp?orchestration_id={orchestration_id}&actor=owner",
            routes=self.routes,
        )
        after = self.data_paths()

        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["operation"], "planner_loop_mvp_preview")
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["verdict"], "NEEDS_OWNER")
        self.assertFalse(data["execute_allowed"])
        self.assertIsNone(data["dry_run_token"])
        self.assertEqual(data["planned_writes"], [])
        self.assertEqual(data["planned_moves"], [])
        self.assertFalse(data["data"]["writes_enabled"])
        self.assertFalse(data["data"]["controlled_mutation_enabled"])
        self.assertFalse(data["data"]["autonomous_runtime_enabled"])
        self.assertFalse(data["data"]["automatic_polling_enabled"])
        self.assertFalse(data["data"]["automatic_agent_execution_enabled"])
        self.assertFalse(data["data"]["automatic_publish_enabled"])
        self.assertFalse(data["data"]["automatic_claim_enabled"])
        self.assertFalse(data["data"]["automatic_push_enabled"])
        self.assertFalse(data["data"]["self_audit_enabled"])
        self.assertEqual(data["summary"]["recommended_step"], "stop_for_owner_decision")
        self.assertEqual(data["summary"]["recommended_route"], "owner_decision_gate")
        self.assertEqual(before, after)

    def test_planner_loop_mvp_route_requires_id(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/planner-loop/mvp", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertIn("orchestration_id is required", data["blocking_reasons"])

    def test_forum_event_review_owner_decision_recorded_requires_evidence_ref(self) -> None:
        status, data = dispatch_api_request(
            method="POST",
            path="/api/forum-event/review",
            routes=self.routes,
            post_routes=self.post_routes,
            body={
                "orchestration_id": "orch_ai_project_os_20260504_forum_event",
                "event_type": "owner_decision_recorded",
                "severity": "needs_owner",
                "actor": "Owner",
                "forum_thread_ref": "forum://aipos/63",
                "summary": "Owner decision recorded without evidence ref.",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["verdict"], "BLOCK")
        self.assertIn("owner_decision_recorded requires an Owner decision evidence ref", data["blocking_reasons"])

    def test_unknown_route_not_found(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/nope", routes=self.routes)
        self.assertEqual(status, 404)
        self.assertEqual(data.get("error"), "NOT_FOUND")

    def test_route_calls_do_not_write_task_data(self) -> None:
        before = sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))
        dispatch_api_request(method="GET", path="/api/health", routes=self.routes)
        dispatch_api_request(method="GET", path="/api/queue", routes=self.routes)
        dispatch_api_request(method="GET", path="/api/agents", routes=self.routes)
        dispatch_api_request(method="GET", path="/api/records", routes=self.routes)
        after = sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))
        self.assertEqual(before, after)

    def test_needs_owner_route_returns_json_envelope(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/needs-owner", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertIn("ok", data)
        self.assertIn("verdict", data)

    def test_validate_route_returns_json_envelope(self) -> None:
        status, data = dispatch_api_request(method="GET", path="/api/validate", routes=self.routes)
        self.assertEqual(status, 200)
        self.assertIn("ok", data)
        self.assertIn("verdict", data)

    def test_static_markup_contains_detail_panel_markers(self) -> None:
        html = (WEB_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Needs Owner Detail", html)
        self.assertIn("Validation Detail", html)
        self.assertIn("needs-owner-detail", html)
        self.assertIn("validation-detail", html)
        self.assertIn("renderNeedsOwnerDetails", js)
        self.assertIn("renderValidationDetails", js)

    def test_static_markup_contains_records_and_agents_detail_markers(self) -> None:
        html = (WEB_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Records Summary", html)
        self.assertIn("Records Detail", html)
        self.assertIn("Agents Detail", html)
        self.assertIn("mobile-review-nav", html)
        self.assertIn("owner-review-queue", html)
        self.assertIn("owner-review-needs-owner", html)
        self.assertIn("owner-review-decisions", html)
        self.assertIn("owner-review-timeline", html)
        self.assertIn("owner-review-drafts", html)
        self.assertIn("owner-review-records", html)
        self.assertIn("owner-review-agents", html)
        self.assertIn("Draft Create", html)
        self.assertIn("Draft Publish", html)
        self.assertIn("Planner Draft Review", html)
        self.assertIn("Approved Planner Draft Publish", html)
        self.assertIn("approved-planner-draft-confirmed", html)
        self.assertIn("Planner Draft Review Desk", html)
        self.assertIn("planner-drafts-review-list", html)
        self.assertIn("planner-drafts-review-detail", html)
        self.assertIn("Parent Requirement", html)
        self.assertIn("Planner Tick", html)
        self.assertIn("Preview Manual Flow", html)
        self.assertIn("manual-planner-tick-card", html)
        self.assertIn("manual-planner-tick-result", html)
        self.assertIn("tick-audit-handoff", html)
        self.assertIn("tick-stop-conditions", html)
        self.assertIn("Planner Loop Persistence", html)
        self.assertIn("planner-persist-operation", html)
        self.assertIn("planner-persist-payload", html)
        self.assertIn("planner-persist-owner-confirmed", html)
        self.assertIn("planner-persist-confirm", html)
        self.assertIn("planner-persist-refresh-handoff", html)
        self.assertIn("planner-persist-handoff-card", html)
        self.assertIn("Forum Event Review", html)
        self.assertIn("Orchestration Summary Preview", html)
        self.assertIn("Orchestration Timeline", html)
        self.assertIn("Context Pack Preview", html)
        self.assertIn("context-pack-card", html)
        self.assertIn("context-pack-result", html)
        self.assertIn("context-pack-preview", html)
        self.assertIn("Planner Loop Control Desk", html)
        self.assertIn("planner-loop-card", html)
        self.assertIn("planner-loop-result", html)
        self.assertIn("load-planner-loop", html)
        self.assertIn("Owner Decision Gate", html)
        self.assertIn("Owner Decision Resolution Review", html)
        self.assertIn("owner-resolution-card", html)
        self.assertIn("owner-resolution-result", html)
        self.assertIn("owner-resolution-review", html)
        self.assertIn("owner-decisions-list", html)
        self.assertIn("owner-decisions-detail", html)
        self.assertIn("orchestration-summary-card", html)
        self.assertIn("orchestration-timeline-list", html)
        self.assertIn("records-list", html)
        self.assertIn("records-detail", html)
        self.assertIn("agents-list", html)
        self.assertIn("agents-detail", html)
        self.assertIn('["records", "/api/records"]', js)
        self.assertIn('["agents", "/api/agents"]', js)
        self.assertIn("renderRecordsDetails", js)
        self.assertIn("renderAgentsDetails", js)
        self.assertIn("draftCreatePayload", js)
        self.assertIn("draftPublishPayload", js)
        self.assertIn("plannerDraftReviewPayload", js)
        self.assertIn("summarizePlannerDraftReview", js)
        self.assertIn("renderPlannerDraftReviewDesk", js)
        self.assertIn("reviewPlannerDraftPath", js)
        self.assertIn("/api/planner-drafts/review", js)
        self.assertIn("publish_ready", js)
        self.assertIn("approvedPlannerDraftPublishPayload", js)
        self.assertIn("parentRequirementPayload", js)
        self.assertIn("summarizeParentRequirement", js)
        self.assertIn("plannerTickPayload", js)
        self.assertIn("summarizePlannerTick", js)
        self.assertIn("summarizeManualPlannerTick", js)
        self.assertIn("renderManualPlannerTickCard", js)
        self.assertIn("previewManualPlannerTickFlow", js)
        self.assertIn("planner_iteration_append_enabled", js)
        self.assertIn("plannerLoopPersistencePayload", js)
        self.assertIn("summarizePlannerLoopPersistenceHandoff", js)
        self.assertIn("renderPlannerLoopPersistenceHandoff", js)
        self.assertIn("refreshPlannerLoopPersistenceHandoffViews", js)
        self.assertIn("runPlannerLoopPersistenceDryRun", js)
        self.assertIn("confirmPlannerLoopPersistenceAppend", js)
        self.assertIn('operation: "planner_iteration_append"', js)
        self.assertIn('operation: "orchestration_event_append"', js)
        self.assertIn("forumEventReviewPayload", js)
        self.assertIn("summarizeForumEventReview", js)
        self.assertIn("summarizeOrchestrationSummary", js)
        self.assertIn("renderOrchestrationSummaryCard", js)
        self.assertIn("/api/orchestration-summary", js)
        self.assertIn("summarizeOrchestrationTimeline", js)
        self.assertIn("renderOrchestrationTimeline", js)
        self.assertIn("/api/orchestration-timeline", js)
        self.assertIn("/api/planner-loop/mvp", js)
        self.assertIn("/api/context-pack/preview", js)
        self.assertIn("summarizeContextPack", js)
        self.assertIn("renderContextPackPreview", js)
        self.assertIn("loadContextPackPreview", js)
        self.assertIn("external_rag_enabled", js)
        self.assertIn("agent_execution_enabled", js)
        self.assertIn("git_automation_enabled", js)
        self.assertIn("summarizePlannerLoopMvp", js)
        self.assertIn("renderPlannerLoopMvp", js)
        self.assertIn("loadPlannerLoopMvp", js)
        self.assertIn("automatic_agent_execution_enabled", js)
        self.assertIn("/api/owner-decisions/review", js)
        self.assertIn("/api/owner-decision/resolve/review", js)
        self.assertIn("ownerDecisionResolutionPayload", js)
        self.assertIn("summarizeOwnerDecisionResolution", js)
        self.assertIn("renderOwnerDecisionResolution", js)
        self.assertIn("reviewOwnerDecisionResolution", js)
        self.assertIn("decision_persistence_enabled", js)
        self.assertIn("renderOwnerDecisionGate", js)
        self.assertIn("renderOwnerDecisionDetail", js)
        self.assertIn("resolution_enabled", js)

    def test_static_markup_contains_refresh_and_debug_controls(self) -> None:
        html = (WEB_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Refresh All", html)
        self.assertIn('data-route-id="queue"', html)
        self.assertIn('data-route-id="needs-owner"', html)
        self.assertIn('data-route-id="validate"', html)
        self.assertIn('data-route-id="agents"', html)
        self.assertIn('data-route-id="drafts"', html)
        self.assertIn('data-route-id="records"', html)
        self.assertIn("debug-toggle", html)
        self.assertIn("Show Debug", html)

    def test_static_js_contains_actor_persistence_logic(self) -> None:
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("localStorage.getItem", js)
        self.assertIn("localStorage.setItem", js)
        self.assertIn("aipos.board.previewActor", js)

    def test_static_js_references_only_aipos55_execute_endpoints(self) -> None:
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/execute/dry-run", js)
        self.assertIn("/api/execute/confirm", js)
        self.assertIn("/api/parent-requirement/preview", js)
        self.assertIn("/api/planner-tick/preview", js)
        self.assertIn("/api/planner-tick/manual-flow/preview", js)
        self.assertIn("/api/planner-draft/review", js)
        self.assertIn("/api/forum-event/review", js)
        self.assertIn('operation: "draft_create"', js)
        self.assertIn('operation: "draft_publish"', js)
        self.assertIn('operation: "planner_iteration_append"', js)
        self.assertIn('operation: "orchestration_event_append"', js)
        self.assertIn("source_path", js)
        forbidden = [
            "/api/mutate",
            "/api/claim",
            "/api/block",
            "/api/complete",
            "/api/reopen",
            "/api/publish",
            "/api/draft/create",
            "/api/draft/publish",
            "/api/queue/claim",
            "/api/queue/block",
            "/api/queue/complete",
            "/api/queue/reopen",
        ]
        for endpoint in forbidden:
            self.assertNotIn(endpoint, js)

    def test_static_js_uses_safe_text_rendering_for_detail_payloads(self) -> None:
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("inner" + "HTML", js)
        self.assertIn("textContent", js)

    def test_static_css_contains_mobile_owner_review_polish(self) -> None:
        css = (WEB_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".mobile-review-nav", css)
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("position: sticky", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("scroll-margin-top", css)


if __name__ == "__main__":
    unittest.main()
