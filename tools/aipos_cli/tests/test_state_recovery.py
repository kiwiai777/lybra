from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools.aipos_cli.aipos_cli import main
from tools.aipos_cli.controlled_execute import clear_tokens, register_dry_run
from tools.aipos_cli.records import load_records
from tools.aipos_cli.state_recovery import build_state_recovery_preview


class StateRecoveryPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        clear_tokens()

    def tearDown(self) -> None:
        clear_tokens()
        self.temp_dir.cleanup()

    def write_task(self, queue_state: str, filename: str, frontmatter: str, body: str = "Body\n") -> Path:
        path = self.repo_root / "5_tasks" / "queue" / queue_state / filename
        path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
        return path

    def write_record(self, relative_path: str, frontmatter: str) -> Path:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{frontmatter}---\nRecord body\n", encoding="utf-8")
        return path

    def test_claimed_returned_task_with_missing_records_is_partial_warn(self) -> None:
        self.write_task(
            "claimed",
            "aipos-173-main.md",
            """task_id: AIPOS-173-MAIN
title: Returned task
status: claimed
agent_instance: agent-01
claimed_by: agent-01
claim_id: claim_AIPOS-173-MAIN_001_agent-01
active_session_id: session_AIPOS-173-MAIN_001_agent-01
executor_status: completed
executor_completed_by: agent-01
executor_completed_at: 2026-06-05T01:00:00Z
audit_readiness: ready
dependency_audit_readiness: ready
dependency_audit_status: pending
result_summary: Work returned.
return_owner_policy_ref: owner_policy:aipos-173
""",
        )

        result = build_state_recovery_preview(self.repo_root, task_id="AIPOS-173-MAIN")

        self.assertEqual(result["verdict"], "WARN")
        self.assertEqual(result["provenance_completeness"], "partial")
        self.assertIn("claim_id references missing claim record", result["warnings"])
        self.assertIn("active_session_id references missing session record", result["warnings"])
        self.assertEqual(result["lease_status"], "proposed")
        self.assertEqual(result["lease_path"], "claim_only")
        self.assertEqual(result["provenance_chain"]["return"]["audit_readiness"], "ready")
        self.assertEqual(result["provenance_chain"]["audit"]["dependency_audit_status"], "pending")
        self.assertFalse(result["writes_enabled"])
        self.assertFalse(result["execute_allowed"])

    def test_existing_claim_and_session_records_make_chain_complete(self) -> None:
        self.write_task(
            "claimed",
            "aipos-173-complete.md",
            """task_id: AIPOS-173-COMPLETE
title: Complete provenance
status: claimed
agent_instance: agent-01
claimed_by: agent-01
claim_id: claim_AIPOS-173-COMPLETE_001_agent-01
active_session_id: session_AIPOS-173-COMPLETE_001_agent-01
executor_status: completed
audit_readiness: ready
dependency_audit_status: pending
result_summary: Work returned.
""",
        )
        self.write_record(
            "5_tasks/records/claims/AIPOS-173-COMPLETE/claim_AIPOS-173-COMPLETE_001_agent-01.md",
            """task_id: AIPOS-173-COMPLETE
claim_id: claim_AIPOS-173-COMPLETE_001_agent-01
session_id: session_AIPOS-173-COMPLETE_001_agent-01
claimed_by: agent-01
claimed_at: 2026-06-05T01:00:00Z
""",
        )
        self.write_record(
            "5_tasks/records/sessions/AIPOS-173-COMPLETE/session_AIPOS-173-COMPLETE_001_agent-01.md",
            """task_id: AIPOS-173-COMPLETE
session_id: session_AIPOS-173-COMPLETE_001_agent-01
claim_id: claim_AIPOS-173-COMPLETE_001_agent-01
session_status: claimed
created_at: 2026-06-05T01:00:00Z
""",
        )

        records = load_records(self.repo_root)
        result = build_state_recovery_preview(self.repo_root, task_id="AIPOS-173-COMPLETE", records=records)

        self.assertEqual(result["verdict"], "WARN")
        self.assertEqual(result["provenance_completeness"], "complete")
        self.assertEqual(
            result["provenance_chain"]["claim"]["claim_record_ref"],
            "5_tasks/records/claims/AIPOS-173-COMPLETE/claim_AIPOS-173-COMPLETE_001_agent-01.md",
        )
        self.assertEqual(
            result["provenance_chain"]["session"]["session_record_ref"],
            "5_tasks/records/sessions/AIPOS-173-COMPLETE/session_AIPOS-173-COMPLETE_001_agent-01.md",
        )
        self.assertIn("Create or run the separately gated independent audit path", result["recommended_next_action"])

    def test_status_contradiction_blocks_recovery_preview(self) -> None:
        self.write_task(
            "claimed",
            "aipos-173-contradiction.md",
            """task_id: AIPOS-173-CONTRADICTION
title: Bad state
status: pending
agent_instance: agent-01
""",
        )

        result = build_state_recovery_preview(self.repo_root, task_id="AIPOS-173-CONTRADICTION")

        self.assertEqual(result["verdict"], "BLOCK")
        self.assertEqual(result["provenance_completeness"], "contradictory")
        self.assertEqual(result["contradictions"][0]["reason_code"], "QUEUE_STATUS_CONTRADICTION")

    def test_process_local_dry_run_token_staleness_is_visible(self) -> None:
        self.write_task(
            "pending",
            "aipos-173-token.md",
            """task_id: AIPOS-173-TOKEN
title: Token state
status: pending
""",
        )

        stale = build_state_recovery_preview(
            self.repo_root,
            task_id="AIPOS-173-TOKEN",
            dry_run_token="dryrun_missing",
            expected_operation="queue_return",
        )
        self.assertEqual(stale["staleness"][0]["reason_code"], "PROCESS_LOCAL_TOKEN_STALE")
        self.assertEqual(stale["staleness"][0]["severity"], "block")

        token_meta = register_dry_run(operation="queue_claim", actor="agent-01", plan={"verdict": "PASS", "data": {}})
        incompatible = build_state_recovery_preview(
            self.repo_root,
            task_id="AIPOS-173-TOKEN",
            dry_run_token=token_meta["dry_run_id"],
            expected_operation="queue_return",
        )
        self.assertTrue(
            any(item.get("reason_code") == "INCOMPATIBLE_DRY_RUN" for item in incompatible["staleness"])
        )

    def test_cli_state_recovery_preview_json_returns_nonzero_only_for_block(self) -> None:
        self.write_task(
            "claimed",
            "aipos-173-cli.md",
            """task_id: AIPOS-173-CLI
title: CLI state
status: claimed
agent_instance: agent-01
claimed_by: agent-01
claim_id: claim_AIPOS-173-CLI_001_agent-01
active_session_id: session_AIPOS-173-CLI_001_agent-01
executor_status: completed
audit_readiness: ready
result_summary: Work returned.
""",
        )

        cwd = Path.cwd()
        try:
            import os

            os.chdir(self.repo_root)
            with redirect_stdout(StringIO()):
                code = main(["state", "recovery", "preview", "--task-id", "AIPOS-173-CLI", "--json"])
        finally:
            import os

            os.chdir(cwd)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
