"""AIPOS-218 WS4 — writer flat-contract guard.

Drives the real record + publish/claim/return/audit writers and asserts that every
emitted frontmatter line uses only the frozen-contract shapes:

  scalar | bool | int | float | empty-value | single-quoted scalar
  key: []   (explicit empty list)
  key:      (block list header)
  - scalar  (block list item)

No maps (except depth-1 nested maps emitted by gate metadata, per ruling 1),
no nested sequences, no block scalars (``|`` / ``>``).
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.draft_writer import create_draft, publish_draft
from tools.aipos_cli.record_writer import (
    build_mcp_audit_dispatch_record_markdown,
    build_mcp_audit_verdict_record_markdown,
    build_mcp_claim_record_markdown,
    build_mcp_return_record_markdown,
)

# ---------------------------------------------------------------------------
# Regex patterns for each permitted line shape.
# ---------------------------------------------------------------------------
# Top-level key: with a scalar value (including empty/whitespace-only value after ": ")
_KEY_SCALAR = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*: .*$")
# key: (bare — starts a block mapping or block list, no space after colon)
_KEY_BARE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*:$")
# key: []  (explicit empty list)
_KEY_EMPTY_LIST = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*: \[\]$")
# - scalar  (block list item, any indent)
_LIST_ITEM = re.compile(r"^( *- ).+$")
# depth-1 nested map line: "  subkey: value" (2-space indent, no further nesting)
_NESTED_MAP_LINE = re.compile(r"^  [a-zA-Z_][a-zA-Z0-9_]*: .*$")
# blank line inside frontmatter (unusual but OK)
_BLANK = re.compile(r"^\s*$")

# FORBIDDEN shapes
_BLOCK_SCALAR_HEADER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*: [|>]")
_SEQUENCE_OF_MAPS = re.compile(r"^- [a-zA-Z_][a-zA-Z0-9_]*:")  # "- key:" at any level


def _extract_frontmatter(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return []
    return lines[1:end]


def _assert_flat_contract(test_case: unittest.TestCase, label: str, text: str) -> None:
    fm_lines = _extract_frontmatter(text)
    test_case.assertGreater(len(fm_lines), 0, f"{label}: no frontmatter found")
    for raw_line in fm_lines:
        line = raw_line
        # Check forbidden shapes first.
        test_case.assertFalse(
            _BLOCK_SCALAR_HEADER.match(line),
            f"{label}: block scalar forbidden: {line!r}",
        )
        test_case.assertFalse(
            _SEQUENCE_OF_MAPS.match(line.lstrip()),
            f"{label}: sequence-of-mappings forbidden: {line!r}",
        )
        # Must match at least one permitted shape.
        ok = (
            _BLANK.match(line)
            or _KEY_EMPTY_LIST.match(line)
            or _KEY_BARE.match(line)
            or _KEY_SCALAR.match(line)
            or _LIST_ITEM.match(line)
            or _NESTED_MAP_LINE.match(line)  # depth-1 nested map (gate metadata, ruling 1)
        )
        test_case.assertTrue(
            ok,
            f"{label}: line does not match flat contract: {line!r}",
        )


class WriterFlatContractTests(unittest.TestCase):
    def _tmp(self) -> tempfile.TemporaryDirectory:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td

    def test_claim_record_is_flat(self) -> None:
        text = build_mcp_claim_record_markdown(
            task_id="AIPOS-WS4-001",
            task_path="5_tasks/queue/claimed/aipos-ws4-001.md",
            actor="agent-01",
            canonical_agent_instance="agent-01",
            owner_policy_ref="DL-20260625-01",
            claim_id="claim-001",
            session_id="session-001",
            claimed_at="2026-06-25T00:00:00Z",
            claim_policy="assigned_agent_only",
            claim_match_basis="canonical_instance",
        )
        _assert_flat_contract(self, "claim_record", text)

    def test_return_record_with_artifact_refs_is_flat(self) -> None:
        text = build_mcp_return_record_markdown(
            task_id="AIPOS-WS4-001",
            task_path="5_tasks/queue/claimed/aipos-ws4-001.md",
            actor="agent-01",
            canonical_agent_instance="agent-01",
            owner_policy_ref="DL-20260625-01",
            return_id="return-001",
            claim_id="claim-001",
            session_id="session-001",
            returned_at="2026-06-25T00:01:00Z",
            result_summary="Done",
            artifact_refs=["5_tasks/records/returns/aipos-ws4-001/report.md", "docs/output.md"],
            completion_report_ref="5_tasks/records/returns/aipos-ws4-001/completion.md",
        )
        _assert_flat_contract(self, "return_record_with_artifacts", text)

    def test_return_record_empty_artifact_refs_is_flat(self) -> None:
        text = build_mcp_return_record_markdown(
            task_id="AIPOS-WS4-002",
            task_path="5_tasks/queue/claimed/aipos-ws4-002.md",
            actor="agent-01",
            canonical_agent_instance="agent-01",
            owner_policy_ref="DL-20260625-01",
            return_id="return-002",
            claim_id="claim-002",
            session_id="session-002",
            returned_at="2026-06-25T00:01:00Z",
            result_summary=None,
            artifact_refs=[],
            completion_report_ref=None,
        )
        _assert_flat_contract(self, "return_record_empty_artifacts", text)

    def test_audit_dispatch_record_is_flat(self) -> None:
        text = build_mcp_audit_dispatch_record_markdown(
            dispatch_id="dispatch-001",
            reviewed_task_id="AIPOS-WS4-001",
            reviewed_task_path="5_tasks/queue/completed/aipos-ws4-001.md",
            reviewed_return_record_ref="5_tasks/records/returns/aipos-ws4-001/return-001.md",
            reviewed_executor_instance="agent-01",
            reviewed_executor_claim_id="claim-001",
            reviewed_executor_session_id="session-001",
            audit_task_id="AIPOS-WS4-AUDIT-001",
            audit_task_path="5_tasks/queue/pending/aipos-ws4-audit-001.md",
            actor="agent-02",
            canonical_agent_instance="agent-02",
            owner_policy_ref="DL-20260625-01",
            dispatched_at="2026-06-25T00:02:00Z",
        )
        _assert_flat_contract(self, "audit_dispatch_record", text)

    def test_audit_verdict_record_with_evidence_refs_is_flat(self) -> None:
        text = build_mcp_audit_verdict_record_markdown(
            verdict_id="verdict-001",
            verdict="PASS",
            reviewed_task_id="AIPOS-WS4-001",
            reviewed_task_path="5_tasks/queue/completed/aipos-ws4-001.md",
            reviewed_return_record_ref="5_tasks/records/returns/aipos-ws4-001/return-001.md",
            audit_dispatch_record_ref="5_tasks/records/audit_dispatches/dispatch-001.md",
            audit_task_id="AIPOS-WS4-AUDIT-001",
            audit_task_path="5_tasks/queue/completed/aipos-ws4-audit-001.md",
            audit_claim_id="claim-002",
            audit_session_id="session-002",
            reviewed_executor_instance="agent-01",
            auditor_instance="agent-02",
            actor="agent-02",
            canonical_agent_instance="agent-02",
            owner_policy_ref="DL-20260625-01",
            verdict_at="2026-06-25T00:03:00Z",
            findings_summary="All checks passed.",
            evidence_refs=["5_tasks/records/sessions/session-002.md", "docs/evidence.md"],
            recommended_next_action="finalize",
        )
        _assert_flat_contract(self, "audit_verdict_record_with_refs", text)

    def test_audit_verdict_record_empty_evidence_is_flat(self) -> None:
        text = build_mcp_audit_verdict_record_markdown(
            verdict_id="verdict-002",
            verdict="FAIL",
            reviewed_task_id="AIPOS-WS4-001",
            reviewed_task_path="5_tasks/queue/completed/aipos-ws4-001.md",
            reviewed_return_record_ref="5_tasks/records/returns/aipos-ws4-001/return-001.md",
            audit_dispatch_record_ref="5_tasks/records/audit_dispatches/dispatch-001.md",
            audit_task_id="AIPOS-WS4-AUDIT-001",
            audit_task_path="5_tasks/queue/completed/aipos-ws4-audit-001.md",
            audit_claim_id="claim-002",
            audit_session_id="session-002",
            reviewed_executor_instance="agent-01",
            auditor_instance="agent-02",
            actor="agent-02",
            canonical_agent_instance="agent-02",
            owner_policy_ref="DL-20260625-01",
            verdict_at="2026-06-25T00:03:00Z",
            findings_summary=None,
            evidence_refs=[],
            recommended_next_action=None,
        )
        _assert_flat_contract(self, "audit_verdict_record_empty_evidence", text)

    def test_publish_record_is_flat(self) -> None:
        td = self._tmp()
        repo_root = Path(td.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (repo_root / "5_tasks" / "queue" / state).mkdir(parents=True)
        metadata = {
            "task_id": "AIPOS-WS4-PUB",
            "title": "WS4 publish test",
            "project": "lybra",
            "assigned_to": "dev_claude",
            "agent_instance": "agent-01",
            "context_bundle": "default",
            "task_mode": "coding",
            "priority": "medium",
            "status": "pending",
            "created_by": "tester",
            "needs_owner": False,
            "artifact_policy": "formal_write",
            "model_tier": "L2",
            "output_target": "tools/",
        }
        draft = create_draft(repo_root, metadata, "WS4 test body.")
        self.assertNotEqual(draft.get("verdict"), "BLOCK", draft)
        self.assertTrue(draft.get("wrote"), draft)
        draft_path = repo_root / draft["target_path"]
        result = publish_draft(repo_root, draft_path, actor="tester", dry_run=False)
        self.assertNotEqual(result.get("verdict"), "BLOCK", result)
        # After publish, the task lives at target_path in the queue.
        task_path = repo_root / result["target_path"]
        _assert_flat_contract(self, "publish_draft", task_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
