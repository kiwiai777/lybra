"""
AIPOS-257 — Derived audit task verdict provenance.

The verdict chain (AIPOS-177) used to hard-bind audit_dispatch records. Derived
audit tasks (AIPOS-253, created_by=gate_derivation) carry a publish record as
their provenance and produce no dispatch record, so they were un-verdictable.
These tests pin the fix: derived audits accept their publish record as the
equivalent dispatch source; non-derived audits keep the original dispatch check.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.audit_derivation import _derive_audit_instance
from tools.aipos_cli.board_adapter import audit_verdict_task


def _fm(lines: list[str], body: str = "body\n") -> str:
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


class DerivedAuditVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for d in (
            "queue/pending", "queue/claimed", "queue/completed", "queue/blocked",
            "records/publishes", "records/returns", "records/sessions",
            "records/claims", "records/audit_dispatches", "records/audit_verdicts",
        ):
            (self.repo_root / "5_tasks" / d).mkdir(parents=True, exist_ok=True)
        self.audit_instance = _derive_audit_instance("lybra")
        self.reviewed_id = "AIPOS-MCP-RETURN"
        self.audit_id = "AIPOS-MCP-RETURNR"
        self.return_id = "return_AIPOS-MCP-RETURN_20260726_agent-01"
        self.publish_id = "publish_aipos-mcp-returnr"
        self.claim_id = "claim_AIPOS-MCP-RETURNR_20260726_audit"
        self.session_id = "session_AIPOS-MCP-RETURNR_20260726_audit"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, rel: str, text: str) -> None:
        p = self.repo_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _build_reviewed_task(self, executor_instance: str = "agent-01") -> None:
        self._write("5_tasks/queue/claimed/aipos-mcp-return.md", _fm([
            f"task_id: {self.reviewed_id}", "title: MCP Return", "project: lybra",
            "assigned_to: dev_claude", f"agent_instance: {executor_instance}",
            "context_bundle: dev_claude", "task_mode: code", "model_tier: L2",
            "priority: medium", "status: claimed", "created_by: tester",
            "needs_owner: false", "output_target: tools/",
            "artifact_policy: formal_write",
            f"executor_completed_by: {executor_instance}", "executor_registry_verified: true",
            f"return_record_ref: {self.return_id}", "audit_readiness: ready",
            "dependency_executor_status: completed",
            "claim_id: claim_AIPOS-MCP-RETURN_20260603_agent-01",
            f"claimed_by: {executor_instance}", "claimed_at: 2026-06-03T00:00:00Z",
            "active_session_id: session_AIPOS-MCP-RETURN_20260603_agent-01",
        ]))
        self._write(f"5_tasks/records/returns/{self.reviewed_id}/{self.return_id}.md", _fm([
            "record_type: return_record", "event_type: mcp_queue_return",
            f"return_id: {self.return_id}", f"task_id: {self.reviewed_id}",
            "surface: mcp", "operation: queue_return", "autonomy_mode: Supervised",
            f"actor: {executor_instance}", f"canonical_agent_instance: {executor_instance}",
        ]))

    def _build_derived_audit_task(self) -> None:
        self._write("5_tasks/queue/claimed/aipos-mcp-returnr.md", _fm([
            f"task_id: {self.audit_id}", "title: Audit return", "project: lybra",
            "assigned_to: audit_lybra", f"agent_instance: {self.audit_instance}",
            "context_bundle: audit.lybra", "task_mode: audit", "task_class: simple",
            "priority: medium", "status: claimed", "needs_owner: false",
            "output_target: tools/", "artifact_policy: formal_write",
            "created_by: gate_derivation", f"derived_from: {self.reviewed_id}",
            f"reviewed_task_id: {self.reviewed_id}",
            "reviewed_task_path: 5_tasks/queue/claimed/aipos-mcp-return.md",
            f"reviewed_return_record_ref: {self.return_id}",
            f"claim_id: {self.claim_id}", f"claimed_by: {self.audit_instance}",
            "claimed_at: 2026-07-26T00:00:00Z",
            f"active_session_id: {self.session_id}", "audit: none",
        ]))
        self._write(f"5_tasks/records/publishes/{self.audit_id}/{self.publish_id}.md", _fm([
            "record_type: publish_record", f"publish_id: {self.publish_id}",
            f"task_id: {self.audit_id}", "actor: gate_derivation",
            "published_task_ref: 5_tasks/queue/pending/aipos-mcp-returnr.md",
        ]))
        self._write(f"5_tasks/records/sessions/{self.audit_id}/{self.session_id}.md", _fm([
            "record_type: session_record", f"session_id: {self.session_id}",
            f"task_id: {self.audit_id}", "session_status: active",
        ]))

    def _verdict_kwargs(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = dict(
            audit_task_id=self.audit_id, reviewed_task_id=self.reviewed_id,
            actor=self.audit_instance, agent_instance=self.audit_instance,
            owner_policy_ref="owner_policy:aipos-257-derived-verdict-test",
            audit_claim_id=self.claim_id, audit_session_id=self.session_id,
            reviewed_return_record_ref=self.return_id,
            verdict="PASS", findings_summary="independent review passed",
            repo_root=self.repo_root, dry_run=True,
        )
        base.update(overrides)
        return base

    def test_derived_verdict_pass_accepts_publish_provenance(self) -> None:
        self._build_reviewed_task()
        self._build_derived_audit_task()
        resp = audit_verdict_task(**self._verdict_kwargs())
        self.assertNotEqual(resp["verdict"], "BLOCK", resp.get("blocking_reasons"))
        self.assertEqual(resp["data"]["verdict"], "PASS")
        # AIPOS-257: derived audits attribute to the publish record (equivalent dispatch)
        self.assertEqual(resp["data"]["audit_provenance_type"], "derivation")
        self.assertEqual(resp["data"]["audit_dispatch_record_ref"], self.publish_id)
        verdict_md = resp["data"]["record_previews"][0]["rendered_markdown"]
        self.assertIn("audit_provenance_type: derivation", verdict_md)
        self.assertIn(f"audit_dispatch_record_ref: {self.publish_id}", verdict_md)

    def test_derived_verdict_blocks_when_publish_record_missing(self) -> None:
        """AIPOS-257: derived path still fail-closes when the publish record is absent."""
        self._build_reviewed_task()
        self._build_derived_audit_task()
        (self.repo_root / f"5_tasks/records/publishes/{self.audit_id}/{self.publish_id}.md").unlink()
        resp = audit_verdict_task(**self._verdict_kwargs())
        self.assertEqual(resp["verdict"], "BLOCK", resp.get("blocking_reasons"))
        self.assertTrue(
            any("derivation publish ref does not resolve" in r for r in resp.get("blocking_reasons", [])),
            resp.get("blocking_reasons"),
        )

    def test_dispatched_verdict_zero_regression(self) -> None:
        """AIPOS-257 S3: non-derived (manual dispatch) verdict path is unchanged."""
        self._build_reviewed_task()
        dispatch_id = "dispatch_AIPOS-MCP-RETURN_20260726_agent-02"
        dispatch_audit_id = "AIPOS-MCP-AUDIT-01"
        claim_id = f"claim_{dispatch_audit_id}_20260726_auditor"
        session_id = f"session_{dispatch_audit_id}_20260726_auditor"
        self._write("5_tasks/queue/claimed/aipos-mcp-audit-01.md", _fm([
            f"task_id: {dispatch_audit_id}", "title: Audit", "project: lybra",
            f"assigned_to: audit_lybra", f"agent_instance: {self.audit_instance}",
            "context_bundle: audit.lybra", "task_mode: audit", "task_class: simple",
            "priority: medium", "status: claimed", "needs_owner: false",
            "output_target: tools/", "artifact_policy: formal_write",
            "created_by: agent-02", f"reviewed_task_id: {self.reviewed_id}",
            "reviewed_task_path: 5_tasks/queue/claimed/aipos-mcp-return.md",
            f"reviewed_return_record_ref: {self.return_id}",
            f"audit_dispatch_record_ref: {dispatch_id}",
            "reviewed_executor_instance: agent-01",
            f"claim_id: {claim_id}", f"claimed_by: {self.audit_instance}",
            "claimed_at: 2026-07-26T00:00:00Z",
            f"active_session_id: {session_id}",
        ]))
        self._write(f"5_tasks/records/audit_dispatches/{self.reviewed_id}/{dispatch_id}.md", _fm([
            "record_type: audit_dispatch_record", "event_type: mcp_audit_dispatch",
            f"dispatch_id: {dispatch_id}", f"reviewed_task_id: {self.reviewed_id}",
            f"audit_task_id: {dispatch_audit_id}", "surface: mcp",
            "operation: audit_dispatch", "autonomy_mode: Supervised",
        ]))
        self._write(f"5_tasks/records/sessions/{dispatch_audit_id}/{session_id}.md", _fm([
            "record_type: session_record", f"session_id: {session_id}",
            f"task_id: {dispatch_audit_id}", "session_status: active",
        ]))
        resp = audit_verdict_task(
            audit_task_id=dispatch_audit_id, reviewed_task_id=self.reviewed_id,
            actor=self.audit_instance, agent_instance=self.audit_instance,
            owner_policy_ref="owner_policy:aipos-257-dispatch-regression",
            audit_claim_id=claim_id, audit_session_id=session_id,
            audit_dispatch_record_ref=dispatch_id,
            reviewed_return_record_ref=self.return_id,
            verdict="PASS", findings_summary="manual dispatch path unchanged",
            repo_root=self.repo_root, dry_run=True,
        )
        self.assertNotEqual(resp["verdict"], "BLOCK", resp.get("blocking_reasons"))
        self.assertEqual(resp["data"]["audit_provenance_type"], "dispatch")
        self.assertEqual(resp["data"]["audit_dispatch_record_ref"], dispatch_id)

    def test_derived_verdict_confirm_writes_record_and_attributes(self) -> None:
        """AIPOS-257 S2: confirm lands the verdict record with derivation provenance."""
        self._build_reviewed_task()
        self._build_derived_audit_task()
        resp = audit_verdict_task(**self._verdict_kwargs(dry_run=False))
        self.assertNotEqual(resp["verdict"], "BLOCK", resp.get("blocking_reasons"))
        verdict_path = self.repo_root / str(resp["data"]["audit_verdict_record_path"])
        self.assertTrue(verdict_path.exists(), resp["data"]["audit_verdict_record_path"])
        vtext = verdict_path.read_text(encoding="utf-8")
        self.assertIn("record_type: audit_verdict_record", vtext)
        self.assertIn("audit_provenance_type: derivation", vtext)
        self.assertIn(f"audit_dispatch_record_ref: {self.publish_id}", vtext)
        # reviewed task reflects the PASS verdict
        rtext = (self.repo_root / "5_tasks/queue/claimed/aipos-mcp-return.md").read_text(encoding="utf-8")
        self.assertIn("dependency_audit_status: PASS", rtext)
        self.assertIn("audit_status: PASS", rtext)
