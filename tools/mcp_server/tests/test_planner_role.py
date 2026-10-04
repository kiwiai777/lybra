"""AIPOS-249 + AIPOS-342 — planner token role + draft_submit/draft_publish write surface (gate-side, real HTTP gate).

Red lines pinned (card §1):
- 红线2: planner token (scopes=[draft_submit, draft_publish]) is structurally SCOPE_DENIED on
  claim/return/confirm(close)/audit — and leaves ZERO records on those attempts.
  AIPOS-342 (甲案): planner gains draft_publish so the advisor can publish its own drafts
  without the Owner manually running a publish command (Owner裁定 DL 05-10: publishing a card
  is NOT a gate — the card lands in pending and waits for an agent to claim).
- 红线1: draft_submit lands ONLY under 5_tasks/drafts/ — the path is DRAFTS_DIR +
  draft_slug(task_id) (constant dir + regex-locked slug); a caller passes no path and
  cannot escape drafts/ (`..`, absolute, separators are slugged away).
- R-2: lybra_task_preview surfaces existing_audit_verdicts (+ existing_returns) so the
  planner can read audit outcomes for round-end scoring via a read-only tool.
- R-4: a task in the drafts zone is NOT claimable — only queue/pending is. Proven with a
  claim-scoped (executor) token, so it's a STRUCTURAL fact, not a scope denial.
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import unittest

from tools.aipos_cli.confirm_client import GateClient
from tools.aipos_cli.records import load_records
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server


def _registry() -> dict[str, dict[str, Any]]:
    return {
        "planner-secret": {
            "role": "planner",
            "token_ref": "svc-planner",
            # AIPOS-342 (甲案): planner gains draft_publish so the advisor can publish
            # its own drafts without the Owner manually running a publish command.
            "scopes": ["draft_submit", "draft_publish"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:plfp249",
        },
        "owner-secret": {
            "role": "owner",
            "token_ref": "svc-owner",
            "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:ownfp249",
        },
        "executor-secret": {
            "role": "executor",
            "token_ref": "svc-executor",
            "scopes": ["queue_claim", "queue_return"],
            "expires_at": "2999-01-01T00:00:00Z",
            "fingerprint": "sha256:exfp249",
        },
    }


def _draft_frontmatter(task_id: str = "AIPOS-PLTEST") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": "Planner-drafted card",
        "project": "lybra",
        "assigned_to": "exec.cc",
        "context_bundle": "exec.cc",
        "task_mode": "code",
        "priority": "medium",
        "status": "pending",
        "created_by": "planner",
        "needs_owner": False,
        "output_target": "docs/",
        "artifact_policy": "formal_write",
    }


class PlannerRoleGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # AIPOS-343: inject active policy so contract section can resolve envelopes
        policies_dir = self.repo_root / "5_tasks" / "policies"
        policies_dir.mkdir(parents=True, exist_ok=True)
        (policies_dir / "pol_lybra_dev_7.md").write_text(
            "---\npolicy_id: pol_lybra_dev_7\nstatus: active\nrole: exec\npolicy_type: dev\nagent_or_role: exec\n---\n# Dev\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST,
            port=0,
            token="",
            keepalive_seconds=0.01,
            max_keepalive_events=1,
            service_role_registry=_registry(),
        )
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(timeout=2)
                httpd.server_close()

    def _tree_files(self) -> set[str]:
        return {str(p.relative_to(self.repo_root)) for p in self.repo_root.rglob("*") if p.is_file()}

    # --- 只读可用: planner (no scope) reads truth ---

    def test_planner_can_use_read_tools(self) -> None:
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            for tool in ("lybra_queue_list", "lybra_project_status", "lybra_validate"):
                result = planner.call_tool(tool, {})
                self.assertTrue(result.get("ok"), f"{tool} should be readable by planner: {result}")

    # --- 红线1 + 免门: draft_submit lands under drafts/, no owner_confirm needed ---

    def test_planner_draft_submit_lands_in_drafts_zone(self) -> None:
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            dry = planner.call_tool(
                "lybra_draft_submit_dry_run",
                {"frontmatter": _draft_frontmatter(), "body": "Do the thing.", "actor": "planner"},
            )
            self.assertTrue(dry.get("dry_run_token"), dry)
            self.assertTrue(str(dry.get("data", {}).get("target_path", "")).startswith("5_tasks/drafts/"))
            confirm = planner.call_tool(
                "lybra_draft_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "planner"}
            )
            self.assertTrue(confirm.get("ok"), confirm)
        drafts = [f for f in self._tree_files() if f.startswith("5_tasks/drafts/")]
        self.assertEqual(len(drafts), 1, f"exactly one draft landed under drafts/: {drafts}")
        self.assertTrue(drafts[0].endswith(".md"))

    def test_draft_submit_path_cannot_escape_drafts(self) -> None:
        """红线1: a task_id carrying path-escape chars is slugged — the draft NEVER lands
        outside 5_tasks/drafts/ (draft_slug regex-locks it; caller passes no path field)."""
        before = self._tree_files()
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            for evil in ("../../etc/passwd", "/abs/escape", "AIPOS/../../x"):
                fm = _draft_frontmatter(task_id=evil)
                dry = planner.call_tool(
                    "lybra_draft_submit_dry_run", {"frontmatter": fm, "body": "x", "actor": "planner"}
                )
                target = str(dry.get("data", {}).get("target_path") or "")
                if target:  # a slug was derivable → it MUST be inside drafts/
                    self.assertTrue(
                        target.startswith("5_tasks/drafts/") and ".." not in target,
                        f"escape attempt {evil!r} produced out-of-drafts target {target!r}",
                    )
        # nothing landed outside drafts/ (dry-runs only; and even a confirm is slug-locked)
        after = self._tree_files()
        new_outside = {f for f in (after - before) if not f.startswith("5_tasks/drafts/")}
        self.assertEqual(new_outside, set(), f"draft_submit wrote outside drafts/: {new_outside}")

    # --- 红线2: planner is SCOPE_DENIED on every non-draft-submit write, ZERO records ---

    def test_planner_denied_on_all_gated_writes_zero_records(self) -> None:
        # AIPOS-342 (甲案): draft_publish removed from denials — planner now has that scope.
        # Planner still denied on claim/return/audit/owner_decision_record.
        denials = [
            ("lybra_queue_claim_dry_run", {"task_id": "X", "actor": "planner", "agent_instance": "planner", "autonomy_mode": "Supervised", "owner_policy_ref": "op"}),
            ("lybra_queue_claim_confirm", {"dry_run_token": "t", "actor": "planner"}),
            ("lybra_queue_return_dry_run", {"actor": "planner"}),
            ("lybra_queue_return_confirm", {"dry_run_token": "t", "actor": "planner"}),
            ("lybra_audit_dispatch_dry_run", {"actor": "planner"}),
            ("lybra_audit_verdict_dry_run", {"actor": "planner"}),
            ("lybra_owner_decision_record_dry_run", {"actor": "planner"}),
        ]
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            for tool, arg in denials:
                result = planner.call_tool(tool, arg)
                self.assertEqual(result.get("error_code"), "SCOPE_DENIED", f"{tool} must be SCOPE_DENIED: {result}")
        records_dir = self.repo_root / "5_tasks" / "records"
        wrote = list(records_dir.rglob("*.md")) if records_dir.exists() else []
        self.assertEqual(wrote, [], f"denied writes must leave ZERO records: {wrote}")

    # --- AIPOS-342 (甲案): planner CAN publish its own draft (出卡不是门) ---

    def test_planner_can_publish_own_draft(self) -> None:
        """AIPOS-342: planner has draft_publish scope and can complete the full publish flow
        (dry_run + confirm) without owner_confirm. The card lands in queue/pending."""
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            dry = planner.call_tool(
                "lybra_draft_submit_dry_run",
                {"frontmatter": _draft_frontmatter("AIPOS-PUBTEST"), "body": "b", "actor": "planner"},
            )
            submit_confirm = planner.call_tool("lybra_draft_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "planner"})
            self.assertTrue(submit_confirm.get("ok"), f"submit confirm should succeed: {submit_confirm}")
            draft_path = dry["data"]["target_path"]
            # planner CAN publish its own draft (AIPOS-342 甲案)
            publish_dry = planner.call_tool("lybra_draft_publish_dry_run", {"path": draft_path, "actor": "planner"})
            self.assertTrue(publish_dry.get("dry_run_token"), f"planner should be able to dry_run publish: {publish_dry}")
            self.assertFalse(publish_dry.get("owner_confirmation_required"), "draft_publish should NOT require owner_confirm after AIPOS-342")
            publish_confirm = planner.call_tool(
                "lybra_draft_publish_confirm",
                {"dry_run_token": publish_dry["dry_run_token"], "actor": "planner"},
            )
            self.assertTrue(publish_confirm.get("ok"), f"planner should be able to confirm publish: {publish_confirm}")
            self.assertNotEqual(publish_confirm.get("verdict"), "BLOCK", f"publish should not be BLOCK: {publish_confirm}")
        # the card is now in queue/pending
        pending = list((self.repo_root / "5_tasks" / "queue" / "pending").glob("*.md"))
        self.assertTrue(any("aipos-pubtest" in f.name.lower() for f in pending), f"published card should be in queue/pending, found: {[f.name for f in pending]}")

    # --- R-2: task_preview surfaces audit verdicts for planner scoring ---

    def test_task_preview_surfaces_audit_verdicts(self) -> None:
        task_id = "AIPOS-AUDREAD"
        (self.repo_root / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md").write_text(
            "\n".join(
                ["---", f"task_id: {task_id}", "title: Audit-read test", "project: lybra",
                 "assigned_to: exec.cc", "context_bundle: exec.cc", "task_mode: code", "priority: medium",
                 "status: pending", "created_by: t", "needs_owner: false", "output_target: docs/",
                 "artifact_policy: formal_write", "---", "body"]
            ),
            encoding="utf-8",
        )
        av_dir = self.repo_root / "5_tasks" / "records" / "audit_verdicts" / task_id
        av_dir.mkdir(parents=True, exist_ok=True)
        (av_dir / "verdict_1.md").write_text(
            "\n".join(
                ["---", "record_type: audit_verdict", f"task_id: {task_id}", "verdict: PASS",
                 "auditor: aud.cc", "created_at: '2026-07-12T00:00:00Z'", "---", "looks good"]
            ),
            encoding="utf-8",
        )
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            preview = planner.call_tool("lybra_task_preview", {"task_id": task_id})
        data = preview.get("data") if isinstance(preview.get("data"), dict) else preview
        self.assertIn("existing_audit_verdicts", data, "R-2: preview must surface audit verdicts")
        self.assertIn("existing_returns", data)
        verdicts = data["existing_audit_verdicts"]
        self.assertTrue(verdicts, "the seeded audit verdict must be visible to the planner")

    # --- R-4: a drafts-zone task is NOT claimable (only queue/pending is) ---

    def test_r4_drafts_zone_task_is_not_claimable(self) -> None:
        """R-4 structural pin: prove with a CLAIM-scoped (executor) token — even with claim
        authority, a task sitting in the drafts zone cannot be claimed, because claim only
        resolves tasks from 5_tasks/queue/. A draft must be Owner-published into the queue
        first. This is structural (queue vs drafts), not a scope denial."""
        with self.gate() as url:
            planner = GateClient(url, "planner-secret")
            planner.initialize()
            dry = planner.call_tool(
                "lybra_draft_submit_dry_run",
                {"frontmatter": _draft_frontmatter("AIPOS-DRAFTONLY"), "body": "b", "actor": "planner"},
            )
            planner.call_tool("lybra_draft_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "planner"})
            # executor (HAS queue_claim) tries to claim the drafts-only task by id → not claimable
            executor = GateClient(url, "executor-secret")
            executor.initialize()
            claim = executor.call_tool(
                "lybra_queue_claim_dry_run",
                {"task_id": "AIPOS-DRAFTONLY", "actor": "exec.cc", "agent_instance": "exec.cc",
                 "autonomy_mode": "Supervised", "owner_policy_ref": "owner_policy:supervised"},
            )
            self.assertNotEqual(claim.get("verdict"), "PASS", "a drafts-zone task must not be claimable")
            self.assertFalse(claim.get("ok") and claim.get("verdict") == "PASS")
        # and the draft is still in drafts/, never moved into the queue by the claim attempt
        self.assertTrue(any(f.startswith("5_tasks/drafts/") for f in self._tree_files()))
        claimed = list((self.repo_root / "5_tasks" / "queue" / "claimed").glob("*.md"))
        self.assertEqual(claimed, [], "no draft leaked into the claimed queue")


# AIPOS-F103 件②: 原 PlannerSkillDeliverableTests(锁仓根旧技能 Owner 控制台 / 第三方规划顾问 的字面)随两份旧技能删除
# (教执行会话自认领/交回/确认的旧做法, 无分发声明——顾问技能经 distribution.schema 从 agents/skills/ 分发)。


if __name__ == "__main__":
    unittest.main()
