from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks
from tools.aipos_cli.authority_scanner import build_authority_report
from tools.mcp_server.tools import (
    lybra_draft_publish_confirm,
    lybra_draft_publish_dry_run,
    visible_tool_descriptors,
)

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "aipos_cli" / "tests" / "fixtures"
DRAFT_REL = "5_tasks/drafts/aipos-39-publish-valid.md"
TASK_ID = "AIPOS-39-PUBLISH-VALID"
PENDING_REL = "5_tasks/queue/pending/aipos-39-publish-valid.md"
PUBLISHER = "dev.codex.local"


class Aipos204GatedPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "drafts").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE_ROOT / "drafts/valid_publishable_draft.md", self.repo_root / DRAFT_REL)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def capability(self, operations: list[str], *, role: str | None = None, fingerprint: str | None = None) -> str:
        payload: dict[str, object] = {
            "token_ref": "cap_pub_test",
            "operations": operations,
            "projects": ["ai-project-os"],
            "expires_at": "2999-01-01T00:00:00Z",
        }
        if role is not None:
            payload["role"] = role
        if fingerprint is not None:
            payload["fingerprint"] = fingerprint
        return json.dumps(payload)

    def call(self, fn, arguments, capability):
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": capability}
        with patch.dict(os.environ, env, clear=True):
            result = fn(arguments)
        return result["structuredContent"]

    def data_paths(self) -> list[str]:
        return sorted(p.relative_to(self.repo_root).as_posix() for p in self.repo_root.rglob("*"))

    # --- visibility (draft_publish scope) ---

    def test_publish_tools_visible_only_with_draft_publish_scope(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability(["queue_claim"])}
        with patch.dict(os.environ, env, clear=True):
            names = [t["name"] for t in visible_tool_descriptors()]
        self.assertNotIn("lybra_draft_publish_dry_run", names)
        env["LYBRA_CAPABILITY_TOKEN"] = self.capability(["draft_publish"])
        with patch.dict(os.environ, env, clear=True):
            names = [t["name"] for t in visible_tool_descriptors()]
        self.assertIn("lybra_draft_publish_dry_run", names)
        self.assertIn("lybra_draft_publish_confirm", names)

    # --- dry-run is zero-write + requires owner confirm + previews the publish record ---

    def test_publish_dry_run_is_zero_write_and_owner_gated(self) -> None:
        before = self.data_paths()
        cap = self.capability(["draft_publish"])
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, cap)
        after = self.data_paths()
        self.assertEqual(before, after)  # zero write
        self.assertTrue(dry.get("dry_run_token"))
        self.assertTrue(dry.get("owner_confirmation_required"))
        kinds = {(w.get("type") or w.get("record_type")) for w in dry.get("planned_writes", [])}
        self.assertIn("publish_record", kinds)

    # --- ★A1 on the publish surface: a publisher-only token cannot self-publish ---

    def test_publisher_only_token_cannot_self_confirm_publish(self) -> None:
        cap = self.capability(["draft_publish"])  # NO owner_confirm
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, cap)
        denied = self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER, "owner_confirmation_token": "OWNER_CONFIRMED"},
            cap,
        )
        self.assertEqual(denied.get("error_code"), "SCOPE_DENIED")
        # nothing published
        self.assertFalse((self.repo_root / PENDING_REL).exists())
        self.assertEqual(load_records(self.repo_root).get("publishes", []), [])

    # --- owner confirm publishes AND stamps confirmer on the on-disk publish record ---

    def test_owner_confirm_publishes_and_records_confirmer(self) -> None:
        owner_cap = self.capability(
            ["draft_publish", "owner_confirm"], role="owner", fingerprint="sha256:ownerpub01"
        )
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, owner_cap)
        confirmed = self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER, "owner_confirmation_token": "OWNER_CONFIRMED"},
            owner_cap,
        )
        self.assertTrue(confirmed.get("ok"), confirmed)
        self.assertTrue((self.repo_root / PENDING_REL).exists())
        records = load_records(self.repo_root)
        meta = records["publishes"][0]["metadata"]
        self.assertEqual(meta.get("confirmer_role"), "owner")
        self.assertEqual(meta.get("confirmer_token_ref"), "cap_pub_test")
        self.assertEqual(meta.get("confirmer_token_fingerprint"), "sha256:ownerpub01")
        self.assertIn("gate_signature", meta)  # §9 placeholder present

    def test_publish_confirm_requires_owner_literal(self) -> None:
        owner_cap = self.capability(["draft_publish", "owner_confirm"], role="owner", fingerprint="sha256:ownerpub01")
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, owner_cap)
        denied = self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER, "owner_confirmation_token": "no"},
            owner_cap,
        )
        self.assertEqual(denied.get("error_code"), "OWNER_CONFIRMATION_REQUIRED")
        self.assertFalse((self.repo_root / PENDING_REL).exists())

    # --- L3 link: a gate-published pending task is VALID provenance ---

    def test_gated_publish_makes_pending_task_l3_valid(self) -> None:
        owner_cap = self.capability(["draft_publish", "owner_confirm"], role="owner", fingerprint="sha256:ownerpub01")
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, owner_cap)
        self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER, "owner_confirmation_token": "OWNER_CONFIRMED"},
            owner_cap,
        )
        report = build_authority_report(
            tasks=load_all_tasks(self.repo_root), records=load_records(self.repo_root), repo_root=self.repo_root
        )
        verdicts = {t["task_id"]: (t["authority_verdict"], t["effective_truth"]) for t in report["task_authority"]}
        self.assertEqual(verdicts.get(TASK_ID), ("VALID", True))


if __name__ == "__main__":
    unittest.main()
