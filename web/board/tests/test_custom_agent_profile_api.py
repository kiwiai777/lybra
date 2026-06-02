from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.custom_agent_profiles import load_custom_registry, registry_path
from web.board.app import _api_post_routes, _api_routes, dispatch_api_request

WEB_ROOT = Path(__file__).resolve().parents[1]


class CustomAgentProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.routes = _api_routes(self.repo_root)
        self.post_routes = _api_post_routes(self.repo_root)

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

    def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        status, data = dispatch_api_request(
            method="POST",
            path=path,
            routes=self.routes,
            post_routes=self.post_routes,
            body=body,
        )
        self.assertEqual(status, 200)
        return data

    def draft(self, payload: dict[str, object] | None = None, *, actor: str = "owner") -> dict[str, object]:
        return self.post("/api/agent-profile/draft", {"actor": actor, "payload": payload or self.payload()})

    def confirm(self, preview: dict[str, object], *, actor: str = "owner", owner_confirmed: bool = True) -> dict[str, object]:
        return self.post(
            "/api/agent-profile/confirm",
            {"actor": actor, "owner_confirmed": owner_confirmed, "preview": preview},
        )

    def test_upsert_preview_then_confirm_writes_scoped_registry_and_revalidates(self) -> None:
        target = registry_path(self.repo_root)
        preview = self.draft()

        self.assertEqual(preview["verdict"], "NEEDS_OWNER")
        self.assertFalse(target.exists())
        self.assertEqual(preview["planned_writes"][0]["path"], "0_control_plane/agents/custom_agent_profiles.yaml")

        confirmed = self.confirm(preview)

        self.assertEqual(confirmed["verdict"], "PASS")
        self.assertTrue(target.exists())
        self.assertTrue(confirmed["data"]["registry_validation"]["ok"])

    def test_bundled_collision_blocks_with_zero_writes(self) -> None:
        preview = self.draft(self.payload(agent_instance="agent-01"))

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("custom canonical agent_instance conflicts with bundled default: agent-01", preview["blocking_reasons"])
        self.assertFalse(registry_path(self.repo_root).exists())

    def test_confirm_requires_owner_and_matching_actor(self) -> None:
        preview = self.draft()

        missing_owner = self.confirm(preview, owner_confirmed=False)
        wrong_actor = self.confirm(preview, actor="other-owner")

        self.assertEqual(missing_owner["verdict"], "BLOCK")
        self.assertIn("owner confirmation token is required", missing_owner["blocking_reasons"][0])
        self.assertEqual(wrong_actor["verdict"], "BLOCK")
        self.assertIn("confirm actor does not match", wrong_actor["blocking_reasons"][0])
        self.assertFalse(registry_path(self.repo_root).exists())

    def test_stale_preview_blocks_with_zero_overwrite(self) -> None:
        stale = self.draft()
        self.confirm(self.draft(self.payload(agent_instance="agent-06")))

        result = self.confirm(stale)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("profile draft snapshot mismatch", result["blocking_reasons"][0])
        registry, _blocking = load_custom_registry(self.repo_root)
        self.assertEqual(registry["profiles"][0]["instances"][0]["agent_instance"], "agent-06")

    def test_expired_preview_blocks_with_zero_writes(self) -> None:
        preview = self.draft()
        preview["dry_run_expires_at"] = "2000-01-01T00:00:00Z"

        result = self.confirm(preview)

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("profile draft expired", result["blocking_reasons"][0])
        self.assertFalse(registry_path(self.repo_root).exists())

    def test_display_name_edit_preserves_canonical_identity(self) -> None:
        self.confirm(self.draft())

        preview = self.draft(self.payload(display_name="Renamed Owner Writer"))
        confirmed = self.confirm(preview)

        self.assertEqual(confirmed["verdict"], "PASS")
        registry, _blocking = load_custom_registry(self.repo_root)
        instance = registry["profiles"][0]["instances"][0]
        self.assertEqual(instance["agent_instance"], "agent-04")
        self.assertEqual(instance["display_name"], "Renamed Owner Writer")

    def test_capability_edit_is_owner_visible_in_preview(self) -> None:
        preview = self.draft(self.payload(capabilities=["repo_edit", "test_run"]))

        self.assertEqual(preview["verdict"], "NEEDS_OWNER")
        self.assertIn("capabilities", preview["summary"]["owner_visible_fields"])
        self.assertFalse(registry_path(self.repo_root).exists())

    def test_deactivate_and_supersede_are_history_preserving(self) -> None:
        self.confirm(self.draft())
        historical = self.repo_root / "5_tasks" / "queue" / "completed" / "historical.md"
        historical.write_text("agent_instance: agent-04\n", encoding="utf-8")

        deactivate = self.draft({"action": "deactivate", "agent_id": "owner_agents", "agent_instance": "agent-04"})
        self.confirm(deactivate)
        supersede = self.draft(
            {
                "action": "supersede",
                "agent_id": "owner_agents",
                "agent_instance": "agent-04",
                "replacement": {
                    "agent_instance": "agent-05",
                    "display_name": "Replacement Session",
                    "capabilities": ["repo_edit"],
                    "provenance": {"vendor": "replacement-vendor"},
                },
            }
        )
        self.confirm(supersede)

        registry, _blocking = load_custom_registry(self.repo_root)
        instances = registry["profiles"][0]["instances"]
        self.assertEqual([item["agent_instance"] for item in instances], ["agent-04", "agent-05"])
        self.assertEqual(instances[0]["identity_status"], "superseded")
        self.assertFalse(instances[0]["enabled"])
        self.assertEqual(instances[1]["supersedes_instance_ids"], ["agent-04"])
        self.assertEqual(historical.read_text(encoding="utf-8"), "agent_instance: agent-04\n")

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
