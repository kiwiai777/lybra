from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from web.board.app import _api_routes, dispatch_api_request

WEB_ROOT = Path(__file__).resolve().parents[1]


class _FakeLiveAdapterResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeLiveAdapterResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class AiAuthoringApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.routes = _api_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def intent(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "intent_id": "board-intent-001",
            "submitted_at": "2026-06-01T12:00:00Z",
            "submitted_by": "owner",
            "requirement": "Prepare a concise status summary.",
        }
        data.update(overrides)
        return data

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

    def live_response(self, *, task_id: str = "BOARD-LIVE-01") -> dict[str, object]:
        return {
            "status": "drafted",
            "provider_ref": "provider-neutral",
            "model_ref": "demo-model",
            "request_config_ref": "live-default",
            "token_cost_estimate": {"input_tokens": 12, "output_tokens": 24},
            "proposal": {
                "frontmatter": {
                    "task_id": task_id,
                    "title": "Prepare a Board live status summary",
                    "project": "demo_project",
                    "assigned_to": "agent-01",
                    "agent_instance": "agent-01",
                    "context_bundle": "default_dev",
                    "task_mode": "docs",
                    "task_class": "simple",
                    "complexity_note": "Small documentation summary from Board live mode.",
                    "model_tier": "L1",
                    "priority": "medium",
                    "status": "pending",
                    "created_by": "ai-assisted-live",
                    "needs_owner": True,
                    "output_target": "workspace_artifacts/board-live-summary.md",
                    "artifact_policy": "formal_write",
                    "task_type": "one_shot",
                    "polling_mode": "agent_polling",
                    "claim_policy": "assigned_agent_only",
                    "report_mode": "completion_summary",
                    "recurrence": "none",
                },
                "body": "## Goal\n\nPrepare one concise Board live status summary.\n",
            },
            "triage": {
                "recommended_task_class": "simple",
                "rationale": "Bounded documentation task.",
                "assumptions": [],
                "missing_information": [],
                "possible_owner_gates": [],
            },
            "assignment_recommendations": {
                "assigned_to": "agent-01",
                "agent_instance": "agent-01",
                "reviewer": None,
                "audit_by": None,
            },
        }

    def data_paths(self) -> list[str]:
        values: list[str] = []
        for root in (self.repo_root / "5_tasks" / "drafts", self.repo_root / "5_tasks" / "records"):
            if root.exists():
                values.extend(path.relative_to(self.repo_root).as_posix() for path in root.rglob("*"))
        return sorted(values)

    def test_preview_then_confirm_writes_standard_draft_and_sidecar_only(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/confirm', '/api/ai-author/preview'])

    def test_injection_fixture_blocks_with_zero_writes(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/preview'])

    def test_adapter_failure_is_visible_with_zero_writes(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/preview'])

    def test_confirm_requires_owner_confirmation_and_matching_actor(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/confirm', '/api/ai-author/preview'])

    def test_stale_and_expired_previews_block_with_zero_writes(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/confirm', '/api/ai-author/preview'])

    def test_manual_retry_is_explicit_and_persists_retry_relationship(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/confirm', '/api/ai-author/preview'])

    def test_live_preview_then_confirm_writes_standard_draft_and_non_secret_sidecar(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/live/confirm', '/api/ai-author/live/preview'])

    def test_live_missing_credential_and_network_failure_block_with_zero_writes(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/ai-author/live/preview'])

    def test_static_workbench_keeps_modes_and_separate_publish_contract(self) -> None:
        html = (WEB_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("AI Task Authoring", html)
        self.assertIn("ai-author-requirement", html)
        self.assertIn("ai-author-mode", html)
        self.assertIn("Live BYO-LLM", html)
        self.assertIn("env:LYBRA_LLM_API_KEY", html)
        self.assertIn("ai-author-owner-confirmed", html)
        self.assertIn("Raw AI authoring JSON", html)
        self.assertIn("/api/ai-author/preview", js)
        self.assertIn("/api/ai-author/confirm", js)
        self.assertIn("/api/ai-author/live/preview", js)
        self.assertIn("/api/ai-author/live/confirm", js)
        self.assertIn("invalidateAiAuthorPreview", js)
        self.assertNotIn("live-secret", html)
        self.assertNotIn("live-secret", js)
        self.assertNotIn("setInterval", js)


if __name__ == "__main__":
    unittest.main()
