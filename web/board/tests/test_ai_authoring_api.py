from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web.board.app import _api_post_routes, _api_routes, dispatch_api_request

WEB_ROOT = Path(__file__).resolve().parents[1]


class AiAuthoringApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        self.routes = _api_routes(self.repo_root)
        self.post_routes = _api_post_routes(self.repo_root)

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

    def preview(self, *, fixture_id: str = "standard-simple", actor: str = "owner", intent: dict[str, object] | None = None) -> dict[str, object]:
        return self.post(
            "/api/ai-author/preview",
            {
                "actor": actor,
                "fixture_id": fixture_id,
                "intent": intent or self.intent(),
            },
        )

    def data_paths(self) -> list[str]:
        values: list[str] = []
        for root in (self.repo_root / "5_tasks" / "drafts", self.repo_root / "5_tasks" / "records"):
            if root.exists():
                values.extend(path.relative_to(self.repo_root).as_posix() for path in root.rglob("*"))
        return sorted(values)

    def test_preview_then_confirm_writes_standard_draft_and_sidecar_only(self) -> None:
        before = self.data_paths()
        preview = self.preview()

        self.assertEqual(preview["verdict"], "NEEDS_OWNER")
        self.assertEqual(before, self.data_paths())
        self.assertEqual(len(preview["planned_writes"]), 2)

        confirmed = self.post(
            "/api/ai-author/confirm",
            {"actor": "owner", "owner_confirmed": True, "preview": preview},
        )

        self.assertEqual(confirmed["verdict"], "PASS")
        self.assertTrue((self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md").exists())
        self.assertTrue((self.repo_root / confirmed["data"]["provenance_path"]).exists())
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "pending" / "fixture-simple-01.md").exists())

    def test_injection_fixture_blocks_with_zero_writes(self) -> None:
        before = self.data_paths()
        preview = self.preview(fixture_id="injection-escalation", intent=self.intent(intent_id="board-injection"))

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("AI proposal requests prohibited policy action: bypass_owner_review", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: request_credentials", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: authority_expansion", preview["blocking_reasons"])
        self.assertIn("AI proposal requests prohibited policy action: publish_immediately", preview["blocking_reasons"])
        self.assertIn("AI-assisted proposal must keep needs_owner: true until Owner review", preview["blocking_reasons"])
        self.assertEqual(before, self.data_paths())

    def test_adapter_failure_is_visible_with_zero_writes(self) -> None:
        before = self.data_paths()
        preview = self.preview(fixture_id="adapter-failure", intent=self.intent(intent_id="board-failure"))

        self.assertEqual(preview["verdict"], "BLOCK")
        self.assertIn("Fixture adapter failure for visible degradation testing.", preview["blocking_reasons"])
        self.assertEqual(before, self.data_paths())

    def test_confirm_requires_owner_confirmation_and_matching_actor(self) -> None:
        preview = self.preview()

        missing_confirmation = self.post(
            "/api/ai-author/confirm",
            {"actor": "owner", "owner_confirmed": False, "preview": preview},
        )
        wrong_actor = self.post(
            "/api/ai-author/confirm",
            {"actor": "other-owner", "owner_confirmed": True, "preview": preview},
        )

        self.assertEqual(missing_confirmation["verdict"], "BLOCK")
        self.assertIn("owner confirmation token is required", missing_confirmation["blocking_reasons"][0])
        self.assertEqual(wrong_actor["verdict"], "BLOCK")
        self.assertIn("confirm actor does not match", wrong_actor["blocking_reasons"][0])
        self.assertEqual(self.data_paths(), [])

    def test_stale_and_expired_previews_block_with_zero_writes(self) -> None:
        stale_preview = self.preview()
        target = self.repo_root / "5_tasks" / "drafts" / "fixture-simple-01.md"
        target.parent.mkdir(parents=True)
        target.write_text("occupied\n", encoding="utf-8")

        stale = self.post(
            "/api/ai-author/confirm",
            {"actor": "owner", "owner_confirmed": True, "preview": stale_preview},
        )
        target.unlink()
        expired_preview = self.preview(intent=self.intent(intent_id="board-expired"))
        expired_preview["dry_run_expires_at"] = "2000-01-01T00:00:00Z"
        expired = self.post(
            "/api/ai-author/confirm",
            {"actor": "owner", "owner_confirmed": True, "preview": expired_preview},
        )

        self.assertEqual(stale["verdict"], "BLOCK")
        self.assertIn("snapshot mismatch", stale["blocking_reasons"][0])
        self.assertEqual(expired["verdict"], "BLOCK")
        self.assertIn("preview expired", expired["blocking_reasons"][0])
        self.assertEqual(self.data_paths(), [])

    def test_manual_retry_is_explicit_and_persists_retry_relationship(self) -> None:
        preview = self.preview(intent=self.intent(intent_id="board-retry", retry_of="authoring_previous"))
        confirmed = self.post(
            "/api/ai-author/confirm",
            {"actor": "owner", "owner_confirmed": True, "preview": preview},
        )

        provenance = self.repo_root / confirmed["data"]["provenance_path"]
        self.assertIn("retry_of: authoring_previous", provenance.read_text(encoding="utf-8"))

    def test_static_workbench_keeps_fixture_only_and_separate_publish_contract(self) -> None:
        html = (WEB_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (WEB_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("AI Task Authoring", html)
        self.assertIn("ai-author-requirement", html)
        self.assertIn("ai-author-owner-confirmed", html)
        self.assertIn("Raw AI authoring JSON", html)
        self.assertIn("/api/ai-author/preview", js)
        self.assertIn("/api/ai-author/confirm", js)
        self.assertIn("invalidateAiAuthorPreview", js)
        self.assertNotIn("LYBRA_LLM_API_KEY", js)
        self.assertNotIn("credential_ref", js)
        self.assertNotIn("setInterval", js)


if __name__ == "__main__":
    unittest.main()
