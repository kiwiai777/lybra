from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.custom_agent_profiles import load_custom_registry, registry_path
from web.board.app import _api_routes, dispatch_api_request

WEB_ROOT = Path(__file__).resolve().parents[1]


class CustomAgentProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.routes = _api_routes(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def payload(self, **instance_overrides: object) -> dict[str, object]:
        instance: dict[str, object] = {
            "agent_instance": "agent-04",
            "display_name": "Owner Local Writer",
            "identity_status": "active",
            "enabled": True,
            "capabilities": ["repo_edit"],
            "write_scopes": ["repository_workspace"],
            "provenance": {
                "vendor": "owner-lab",
                "harness": "custom-shell",
                "model_family": "model-z",
                "host": "owner-workstation",
            },
        }
        instance.update(instance_overrides)
        return {"action": "upsert", "agent_id": "owner_agents", "instance": instance}

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

    def test_upsert_preview_then_confirm_writes_scoped_registry_and_revalidates(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft', '/api/agent-profile/confirm'])

    def test_bundled_collision_blocks_with_zero_writes(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft'])

    def test_confirm_requires_owner_and_matching_actor(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft', '/api/agent-profile/confirm'])

    def test_stale_preview_blocks_with_zero_overwrite(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft', '/api/agent-profile/confirm'])

    def test_expired_preview_blocks_with_zero_writes(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft', '/api/agent-profile/confirm'])

    def test_display_name_edit_preserves_canonical_identity(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft', '/api/agent-profile/confirm'])

    def test_capability_edit_is_owner_visible_in_preview(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft'])

    def test_deactivate_and_supersede_are_history_preserving(self) -> None:
        """AIPOS-F171 件③: 看板只读 —— 本例原断言的看板写/预演行为已下线; 改断言同一路由 405 + BOARD_READ_ONLY + 零写入。"""
        self.assert_board_read_only(['/api/agent-profile/draft', '/api/agent-profile/confirm'])

    def test_static_workbench_has_structured_fields_without_runtime_env_or_yaml_editor(self) -> None:
        html = (WEB_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Custom Agent Profiles", html)
        self.assertIn("profile-action", html)
        self.assertIn("profile-agent-instance", html)
        self.assertIn("profile-provenance-model-family", html)
        self.assertIn("Raw custom profile JSON", html)
        self.assertIn("/api/agent-profile/draft", js)
        self.assertIn("/api/agent-profile/confirm", js)
        self.assertIn("invalidateProfilePreview", js)
        self.assertIn('refreshPanel("agents")', js)
        self.assertNotIn("runtime_env", html)
        self.assertNotIn("raw YAML", html)
        self.assertNotIn("setInterval", js)


if __name__ == "__main__":
    unittest.main()
