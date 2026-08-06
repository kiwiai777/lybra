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
        # AIPOS-343: inject active policy so contract section can resolve envelopes
        policies_dir = self.repo_root / "5_tasks" / "policies"
        policies_dir.mkdir(parents=True, exist_ok=True)
        (policies_dir / "pol_lybra_dev_7.md").write_text(
            "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\nagent_or_role: exec\n---\n# Dev\n",
            encoding="utf-8",
        )
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

    # --- dry-run is zero-write + previews the publish record ---
    # AIPOS-342 (甲案): owner_confirmation_required is now False — publishing a card is NOT
    # a gate (Owner裁定 DL 05-10). The card lands in pending and waits for an agent to claim.

    def test_publish_dry_run_is_zero_write_and_no_owner_gate(self) -> None:
        before = self.data_paths()
        cap = self.capability(["draft_publish"])
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, cap)
        after = self.data_paths()
        self.assertEqual(before, after)  # zero write
        self.assertTrue(dry.get("dry_run_token"))
        self.assertFalse(dry.get("owner_confirmation_required"))  # AIPOS-342: no owner gate
        kinds = {(w.get("type") or w.get("record_type")) for w in dry.get("planned_writes", [])}
        self.assertIn("publish_record", kinds)

    # --- AIPOS-342 (甲案): a publisher-only token CAN self-publish (出卡不是门) ---

    def test_publisher_only_token_can_self_confirm_publish(self) -> None:
        """AIPOS-342: Owner裁定 — publishing a card is NOT a gate. A draft_publish-scoped
        token can complete the full publish flow (dry_run + confirm) without owner_confirm."""
        cap = self.capability(["draft_publish"])  # NO owner_confirm needed
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, cap)
        self.assertTrue(dry.get("dry_run_token"), dry)
        self.assertFalse(dry.get("owner_confirmation_required"))
        confirmed = self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER},
            cap,
        )
        self.assertTrue(confirmed.get("ok"), f"publisher-only token should be able to publish: {confirmed}")
        self.assertTrue((self.repo_root / PENDING_REL).exists())
        records = load_records(self.repo_root)
        self.assertEqual(len(records.get("publishes", [])), 1)

    # --- AIPOS-342: owner confirm is no longer required for draft_publish ---
    # The owner can still publish with owner_confirm scope, but it's not required.

    def test_owner_can_still_publish_with_owner_confirm(self) -> None:
        """Owner with draft_publish+owner_confirm can still publish; the owner_confirmation_token
        is accepted but no longer required."""
        owner_cap = self.capability(
            ["draft_publish", "owner_confirm"], role="owner", fingerprint="sha256:ownerpub01"
        )
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, owner_cap)
        confirmed = self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER},
            owner_cap,
        )
        self.assertTrue(confirmed.get("ok"), confirmed)
        self.assertTrue((self.repo_root / PENDING_REL).exists())
        records = load_records(self.repo_root)
        self.assertEqual(len(records.get("publishes", [])), 1)

    # --- L3 link: a published pending task is VALID provenance ---

    def test_published_pending_task_l3_valid(self) -> None:
        """AIPOS-342: a draft_publish-scoped token can publish; the resulting pending task
        is L3-valid (authority=VALID, effective_truth=True)."""
        cap = self.capability(["draft_publish"])
        dry = self.call(lybra_draft_publish_dry_run, {"path": DRAFT_REL, "actor": PUBLISHER}, cap)
        self.call(
            lybra_draft_publish_confirm,
            {"dry_run_token": dry["dry_run_token"], "actor": PUBLISHER},
            cap,
        )
        report = build_authority_report(
            tasks=load_all_tasks(self.repo_root), records=load_records(self.repo_root), repo_root=self.repo_root
        )
        verdicts = {t["task_id"]: (t["authority_verdict"], t["effective_truth"]) for t in report["task_authority"]}
        self.assertEqual(verdicts.get(TASK_ID), ("VALID", True))


if __name__ == "__main__":
    unittest.main()
