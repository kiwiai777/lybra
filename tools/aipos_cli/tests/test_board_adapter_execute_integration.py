from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.board_adapter import (
    block_task,
    claim_task,
    complete_task,
    create_draft,
    execute_dry_run,
    publish_draft,
    reopen_task,
    return_task,
)
from tools.aipos_cli.controlled_execute import get_dry_run
from tools.aipos_cli.draft_validator import validate_draft_file
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.queue_mutation import render_task_markdown

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


class BoardAdapterExecuteIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # AIPOS-343: active policies so contract section can resolve envelopes
        policies_dir = self.repo_root / "5_tasks" / "policies"
        policies_dir.mkdir(parents=True, exist_ok=True)
        (policies_dir / "pol_lybra_dev_7.md").write_text(
            "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\n---\n# Dev\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def read_json_fixture(self, rel: str) -> dict[str, object]:
        return json.loads((FIXTURE_ROOT / rel).read_text(encoding="utf-8"))

    def copy_fixture(self, rel: str, dst: str) -> Path:
        target = self.repo_root / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE_ROOT / rel, target)
        return target

    def assert_execute_contract(self, response: dict[str, object]) -> None:
        for key in (
            "ok",
            "verdict",
            "operation",
            "actor",
            "dry_run_id",
            "dry_run_snapshot_hash",
            "performed_writes",
            "performed_moves",
            "blocking_reasons",
            "warnings",
            "errors",
        ):
            self.assertIn(key, response)
        if response.get("errors"):
            self.assertIn("category", response["errors"][0])

    def test_draft_create_flow_integration(self) -> None:
        payload = self.read_json_fixture("task_cards/valid_draft_create_payload.json")
        dry = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertIn("dry_run_id", dry)
        self.assertIn("dry_run_snapshot_hash", dry)

        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assert_execute_contract(executed)
        self.assertTrue(executed["ok"])

        created = self.repo_root / "5_tasks/drafts/aipos-39-draft-create.md"
        self.assertTrue(created.exists())
        validated = validate_draft_file(self.repo_root, "5_tasks/drafts/aipos-39-draft-create.md")
        self.assertEqual(validated["verdict"], "PASS")

    def test_draft_create_execute_without_dry_run_blocks(self) -> None:
        blocked = execute_dry_run("missing", "dev.codex.local", repo_root=self.repo_root)
        self.assert_execute_contract(blocked)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["errors"][0]["category"], "DRY_RUN_REQUIRED")

    def test_draft_create_expired_and_stale_and_wrong_actor(self) -> None:
        payload = self.read_json_fixture("task_cards/valid_draft_create_payload.json")
        dry = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")

        token = get_dry_run(dry["dry_run_id"])
        assert token is not None
        token.expires_at = "2000-01-01T00:00:00Z"
        expired = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertEqual(expired["errors"][0]["category"], "REVALIDATION_FAILED")

        dry2 = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        (self.repo_root / "5_tasks/drafts").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks/drafts/aipos-39-draft-create.md").write_text("collision", encoding="utf-8")
        stale = execute_dry_run(dry2["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertFalse(stale["ok"])
        if stale.get("errors"):
            self.assertIn(stale["errors"][0]["category"], {"REVALIDATION_FAILED", "DRY_RUN_REQUIRED", "VALIDATION_ERROR"})

        dry3 = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        mismatch = execute_dry_run(dry3["dry_run_id"], "dev.claude.local", repo_root=self.repo_root)
        self.assertEqual(mismatch["errors"][0]["category"], "ACTOR_MISMATCH")

    def test_draft_create_invalid_payload_and_duplicate_and_traversal(self) -> None:
        invalid = self.read_json_fixture("task_cards/invalid_missing_required_payload.json")
        blocked = create_draft(invalid, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertEqual(blocked["verdict"], "BLOCK")

        payload = self.read_json_fixture("task_cards/valid_draft_create_payload.json")
        first = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertTrue(first["ok"])
        _ = execute_dry_run(first["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        second = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertEqual(second["verdict"], "BLOCK")

        unsafe = dict(payload)
        unsafe["frontmatter"] = dict(payload["frontmatter"])
        unsafe["frontmatter"]["task_id"] = "../escape"
        unsafe_result = create_draft(unsafe, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertEqual(unsafe_result["verdict"], "BLOCK")

    def test_draft_publish_flow_and_failures(self) -> None:
        source = self.copy_fixture("drafts/valid_publishable_draft.md", "5_tasks/drafts/aipos-39-publish-valid.md")
        before = source.read_text(encoding="utf-8")

        dry = publish_draft("5_tasks/drafts/aipos-39-publish-valid.md", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertIn("dry_run_id", dry)
        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assert_execute_contract(executed)
        self.assertTrue(executed["ok"])
        self.assertEqual(source.read_text(encoding="utf-8"), before)

        pending = self.repo_root / "5_tasks/queue/pending/aipos-39-publish-valid.md"
        self.assertTrue(pending.exists())

        # stale token after destination appears
        dry2 = publish_draft("5_tasks/drafts/aipos-39-publish-valid.md", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        stale = execute_dry_run(dry2["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertFalse(stale["ok"])

    def test_draft_publish_duplicate_task_id_outside_drafts_non_markdown_and_traversal(self) -> None:
        self.copy_fixture("drafts/duplicate_task_id_draft.md", "5_tasks/drafts/duplicate_task_id_draft.md")
        self.copy_fixture("queue/pending_assigned_to_dev_codex.md", "5_tasks/queue/pending/existing_duplicate.md")

        dup = publish_draft("5_tasks/drafts/duplicate_task_id_draft.md", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertEqual(dup["verdict"], "BLOCK")

        outside = self.copy_fixture("drafts/valid_publishable_draft.md", "outside.md")
        outside_result = publish_draft(str(outside.relative_to(self.repo_root)), dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertFalse(outside_result["ok"])

        self.copy_fixture("drafts/valid_publishable_draft.md", "5_tasks/drafts/not_markdown.txt")
        non_md = publish_draft("5_tasks/drafts/not_markdown.txt", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertFalse(non_md["ok"])

        traversal = publish_draft("../escape.md", dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        self.assertFalse(traversal["ok"])

    def test_queue_claim_flow_and_failures(self) -> None:
        self.copy_fixture("queue/pending_assigned_to_dev_codex.md", "5_tasks/queue/pending/pending_assigned_to_dev_codex.md")
        dry = claim_task(task_id="AIPOS-39-QUEUE-CODEX", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        self.assertIn("dry_run_id", dry)

        executed = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertTrue(executed["ok"])
        claimed = self.repo_root / "5_tasks/queue/claimed/pending_assigned_to_dev_codex.md"
        text = claimed.read_text(encoding="utf-8")
        self.assertIn("status: claimed", text)
        self.assertIn("claimed_by: dev.codex.local", text)
        self.assertIn("claimed_at:", text)
        self.assertIn("claim_id:", text)

        wrong_actor = claim_task(task_id="AIPOS-39-QUEUE-CODEX", actor="dev.claude.local", dry_run=True, repo_root=self.repo_root)
        self.assertEqual(wrong_actor["verdict"], "BLOCK")

    def test_queue_claim_states_collisions_and_with_records(self) -> None:
        self.copy_fixture("queue/claimed_task.md", "5_tasks/queue/claimed/claimed_task.md")
        self.copy_fixture("queue/completed_task.md", "5_tasks/queue/completed/completed_task.md")
        self.copy_fixture("queue/blocked_task.md", "5_tasks/queue/blocked/blocked_task.md")
        self.copy_fixture("queue/pending_assigned_to_dev_claude.md", "5_tasks/queue/pending/pending_assigned_to_dev_claude.md")

        already_claimed = claim_task(task_id="AIPOS-39-QUEUE-CLAIMED", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        completed = claim_task(task_id="AIPOS-39-QUEUE-COMPLETED", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        blocked = claim_task(task_id="AIPOS-39-QUEUE-BLOCKED", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        mismatch = claim_task(task_id="AIPOS-39-QUEUE-CLAUDE", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        records = claim_task(task_id="AIPOS-39-QUEUE-CLAUDE", actor="dev.claude.local", dry_run=True, with_records=True, repo_root=self.repo_root)

        self.assertEqual(already_claimed["verdict"], "BLOCK")
        self.assertEqual(completed["verdict"], "BLOCK")
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertEqual(mismatch["verdict"], "BLOCK")
        self.assertFalse(records.get("execute_allowed", True))

        # destination collision
        self.copy_fixture("queue/pending_assigned_to_dev_codex.md", "5_tasks/queue/pending/shared.md")
        self.copy_fixture("queue/claimed_task.md", "5_tasks/queue/claimed/SHARED.md")
        collision = claim_task(path="5_tasks/queue/pending/shared.md", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)
        self.assertEqual(collision["verdict"], "BLOCK")

    def test_queue_claim_execute_wrong_actor_and_stale(self) -> None:
        self.copy_fixture("queue/pending_assigned_to_dev_codex.md", "5_tasks/queue/pending/stale_claim.md")
        dry = claim_task(path="5_tasks/queue/pending/stale_claim.md", actor="dev.codex.local", dry_run=True, repo_root=self.repo_root)

        mismatch = execute_dry_run(dry["dry_run_id"], "dev.claude.local", repo_root=self.repo_root)
        self.assertEqual(mismatch["errors"][0]["category"], "ACTOR_MISMATCH")

        # move the task out-of-band before execute to force stale
        moved_target = self.repo_root / "5_tasks/queue/claimed/stale_claim.md"
        moved_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(self.repo_root / "5_tasks/queue/pending/stale_claim.md", moved_target)
        stale = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assertFalse(stale["ok"])
        self.assertTrue(stale.get("errors") or stale.get("blocking_reasons"))

    def test_unsupported_execute_paths_remain_blocked(self) -> None:
        self.copy_fixture("queue/claimed_task.md", "5_tasks/queue/claimed/claimed_task.md")
        b = block_task(task_id="AIPOS-39-QUEUE-CLAIMED", actor="dev.codex.local", reason="hold", dry_run=False, repo_root=self.repo_root)
        c = complete_task(task_id="AIPOS-39-QUEUE-CLAIMED", actor="dev.codex.local", report_link="https://example.com", dry_run=False, repo_root=self.repo_root)
        r = reopen_task(task_id="AIPOS-39-QUEUE-CLAIMED", actor="dev.codex.local", reason="retry", dry_run=False, repo_root=self.repo_root)

        self.assertEqual(b["errors"][0]["category"], "DRY_RUN_REQUIRED")
        self.assertEqual(c["errors"][0]["category"], "DRY_RUN_REQUIRED")
        self.assertEqual(r["errors"][0]["category"], "DRY_RUN_REQUIRED")

    def test_response_contract_has_revalidation_hashes_when_stale(self) -> None:
        payload = self.read_json_fixture("task_cards/valid_draft_create_payload.json")
        dry = create_draft(payload, dry_run=True, repo_root=self.repo_root, actor="dev.codex.local")
        token = get_dry_run(dry["dry_run_id"])
        assert token is not None
        token.snapshot_hash = "invalid_hash"

        blocked = execute_dry_run(dry["dry_run_id"], "dev.codex.local", repo_root=self.repo_root)
        self.assert_execute_contract(blocked)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["errors"][0]["category"], "REVALIDATION_FAILED")
        self.assertIn("current_snapshot_hash", blocked["data"])
        self.assertIn("expected_dry_run_snapshot_hash", blocked["data"])
        self.assertEqual(blocked["data"].get("recommended_action"), "run dry-run again")

    # AIPOS-256F3 F-256R-3-R1: Real E2E for return→derive→backref with body preservation.
    # Function-level call (dry_run=False); no MCP/owner-token mock required (proven path).
    def test_return_derive_backref_body_survives(self) -> None:
        task_id = "AIPOS-V01"
        session_id = "session_AIPOS-V01_x"
        claim_id = "claim_AIPOS-V01_x"
        actor = "exec.lybra.test"

        # Records scaffolding required by the return flow (not created by setUp).
        for rel in (
            f"5_tasks/records/sessions/{task_id}",
            f"5_tasks/records/claims/{task_id}",
            f"5_tasks/records/returns/{task_id}",
        ):
            (self.repo_root / rel).mkdir(parents=True, exist_ok=True)
        (self.repo_root / f"5_tasks/records/sessions/{task_id}/{session_id}.md").write_text(
            f"---\nrecord_type: session_record\ntask_id: {task_id}\nsession_id: {session_id}\n---\n",
            encoding="utf-8",
        )
        (self.repo_root / f"5_tasks/records/claims/{task_id}/{claim_id}.md").write_text(
            f"---\nrecord_type: claim_record\ntask_id: {task_id}\nclaim_id: {claim_id}\n---\n",
            encoding="utf-8",
        )

        source_metadata = {
            "task_id": task_id,
            "title": "T",
            "project": "lybra",
            "task_mode": "code",
            "task_class": "simple",
            "status": "claimed",
            "assigned_to": actor,
            "agent_instance": actor,
            "context_bundle": "default",
            "priority": "medium",
            "created_by": "advisor",
            "needs_owner": False,
            "output_target": "tools/",
            "artifact_policy": "formal_write",
            "claimed_by": actor,
            "claim_id": claim_id,
            "active_session_id": session_id,
            "claimed_at": "2026-07-26T13:00:00Z",
        }
        # Distinctive body marker — must survive the return/derive/backref rewrite byte-for-byte.
        body_before = "## Important Task Body\nDistinctive-MARKER-9f3c21\nMust survive.\n"
        source_card = self.repo_root / "5_tasks/queue/claimed/aipos-v01.md"
        source_card.write_text(render_task_markdown(source_metadata, body_before), encoding="utf-8")

        # Body as it lives in the source card immediately before return (byte-level baseline).
        _, parsed_body_before, _ = parse_markdown_frontmatter(source_card.read_text(encoding="utf-8"))

        response = return_task(
            task_id=task_id,
            actor=actor,
            agent_instance=actor,
            owner_policy_ref="owner_policy:test",
            claim_id=claim_id,
            active_session_id=session_id,
            result_summary="done",
            dry_run=False,
            repo_root=self.repo_root,
            mcp_return_metadata={"identity_provenance": {"registry_available": True}},
        )

        # (a) verdict non-BLOCK (the F-256R-1-R1 crash was swallowed into BLOCK).
        self.assertNotEqual(response.get("verdict"), "BLOCK", msg=f"return blocked: {response.get('blocking_reasons')!r}")
        self.assertNotIn(
            "'tuple' object has no attribute 'get'",
            str(response.get("blocking_reasons")),
            msg="F-256R-1-R1 tuple crash regressed",
        )

        # (b) derived audit card exists on disk (locate via performed_writes, then assert file).
        derivation_writes = [
            w for w in response.get("performed_writes", []) if w.get("type") == "derived_audit_task"
        ]
        self.assertTrue(derivation_writes, msg="no derived_audit_task write in performed_writes")
        audit_card_path = self.repo_root / derivation_writes[0]["path"]
        self.assertTrue(audit_card_path.exists(), msg=f"derived audit card missing at {audit_card_path}")

        # (c) source card backref written (the F-253-1 closure the crash was silently breaking).
        after_text = source_card.read_text(encoding="utf-8")
        self.assertIn("related_audit_task_ref: AIPOS-V01R", after_text, msg="backref not written to source card")

        # (d) source card body byte-identical before vs after the rewrite.
        _, parsed_body_after, _ = parse_markdown_frontmatter(after_text)
        self.assertEqual(parsed_body_before, parsed_body_after, msg="source card body changed across return")
        self.assertIn("Distinctive-MARKER-9f3c21", parsed_body_after, msg="distinctive body marker lost")


if __name__ == "__main__":
    unittest.main()
