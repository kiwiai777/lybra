from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# ENV-AWARE: bare-python asserts LOUD/FAIL-CLOSED behavior, not skip.
_HAS_YAML = importlib.util.find_spec("yaml") is not None

from tools.aipos_cli.controlled_execute import clear_tokens, get_dry_run
from tools.aipos_cli.board_adapter import claim_task, return_task
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.records import load_records
from tools.aipos_cli.state_recovery import build_state_recovery_preview
from tools.mcp_server.server import handle_request, serve
from tools.mcp_server.tools import TOOL_DESCRIPTORS


class McpToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        # AIPOS-226 Phase 2b: project existence is the home 5_tasks/queue marker (legacy
        # 2_projects/<tag> probe removed).
        (self.repo_root / "acme_client" / "5_tasks" / "queue").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles" / "examples").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "3_context_bundles" / "examples" / "dev.codex.local.md").write_text(
            "\n".join(
                [
                    "role_instance: dev.codex.local",
                    "environment: local_wsl_ubuntu",
                    "description: test bundle",
                    "allowed_task_modes:",
                    "  - code",
                    "preferred_model_tiers:",
                    "  - L2",
                    "allowed_model_tiers:",
                    "  - L2",
                    "memory_access:",
                    "  - 2_projects/lybra/",
                    "output_target:",
                    "  - repository",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.write_task()
        clear_tokens()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_task(self) -> None:
        (self.repo_root / "5_tasks" / "queue" / "pending" / "example.md").write_text(
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-MCP-READ",
                    "title: MCP Read Test",
                    "project: lybra",
                    "assigned_to: dev.codex.local",
                    "agent_instance: dev.codex.local",
                    "context_bundle: dev.codex.local",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/mcp_server/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/mcp_server/",
                    "memory_scope: mcp tests",
                    "---",
                    "Read-only MCP test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        with patch.dict(os.environ, {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}):
            response = handle_request(request)
        assert response is not None
        return response

    def capability_token(self, operations: list[str] | None = None, role: str | None = None, fingerprint: str | None = None) -> str:
        payload: dict[str, object] = {
            "token_ref": "cap_mcp_test",
            "operations": operations if operations is not None else ["intake_submit"],
            "projects": ["acme_client"],
            "expires_at": "2999-01-01T00:00:00Z",
        }
        if role is not None:
            payload["role"] = role
        if fingerprint is not None:
            payload["fingerprint"] = fingerprint
        return json.dumps(payload)

    def write_claim_task(self, task_id: str = "AIPOS-MCP-CLAIM", *, agent_instance: str = "agent-01", claim_policy: str = "specific_instance_only") -> None:
        (self.repo_root / "5_tasks" / "queue" / "pending" / f"{task_id.lower()}.md").write_text(
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    "title: MCP Claim Test",
                    "project: lybra",
                    "assigned_to: dev_claude",
                    f"agent_instance: {agent_instance}",
                    "context_bundle: dev_claude",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/mcp_server/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/mcp_server/",
                    "memory_scope: mcp claim tests",
                    f"claim_policy: {claim_policy}",
                    "---",
                    "Supervised MCP claim test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_return_task(self, task_id: str = "AIPOS-MCP-RETURN", *, claimed_by: str = "agent-01", agent_instance: str = "agent-01") -> None:
        (self.repo_root / "5_tasks" / "queue" / "claimed" / f"{task_id.lower()}.md").write_text(
            "\n".join(
                [
                    "---",
                    f"task_id: {task_id}",
                    "title: MCP Return Test",
                    "project: lybra",
                    "assigned_to: dev_claude",
                    f"agent_instance: {agent_instance}",
                    "context_bundle: dev_claude",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: claimed",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/mcp_server/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/mcp_server/",
                    "memory_scope: mcp return tests",
                    "claim_policy: specific_instance_only",
                    "claim_id: claim_AIPOS-MCP-RETURN_20260603_agent-01",
                    f"claimed_by: {claimed_by}",
                    "claimed_at: 2026-06-03T00:00:00Z",
                    "active_session_id: session_AIPOS-MCP-RETURN_20260603_agent-01",
                    "---",
                    "Supervised MCP return test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        session_id = f"session_{task_id}_20260603_agent-01"
        claim_id = f"claim_{task_id}_20260603_agent-01"
        claim_path = self.repo_root / "5_tasks" / "records" / "claims" / task_id / f"{claim_id}.md"
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        claim_path.write_text(
            "\n".join(
                [
                    "---",
                    "record_type: claim_record",
                    "event_type: mcp_queue_claim",
                    f"claim_id: {claim_id}",
                    f"task_id: {task_id}",
                    f"task_path: 5_tasks/queue/claimed/{task_id.lower()}.md",
                    "surface: mcp",
                    "operation: queue_claim",
                    "autonomy_mode: Supervised",
                    f"actor: {claimed_by}",
                    f"canonical_agent_instance: {claimed_by}",
                    "owner_policy_ref: owner_policy:aipos-169-supervised-return-test",
                    "claimed_at: 2026-06-03T00:00:00Z",
                    "from_state: pending",
                    "to_state: claimed",
                    "session_id: " + session_id,
                    "lease_status: proposed",
                    "lease_path: claim_only",
                    "active_lease_written: false",
                    "---",
                    "# MCP Claim Record",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        session_path = self.repo_root / "5_tasks" / "records" / "sessions" / task_id / f"{session_id}.md"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            "\n".join(
                [
                    "---",
                    "record_type: session_record",
                    f"session_id: {session_id}",
                    f"task_id: {task_id}",
                    f"task_path: 5_tasks/queue/claimed/{task_id.lower()}.md",
                    "surface: mcp",
                    "autonomy_mode: Supervised",
                    f"actor: {claimed_by}",
                    f"canonical_agent_instance: {claimed_by}",
                    "owner_policy_ref: owner_policy:aipos-169-supervised-return-test",
                    f"claim_id: claim_{task_id}_20260603_agent-01",
                    "created_at: 2026-06-03T00:00:00Z",
                    "updated_at: 2026-06-03T00:00:00Z",
                    "session_status: claimed",
                    "current_state: claimed",
                    "lease_status: proposed",
                    "lease_path: claim_only",
                    "active_lease_written: false",
                    "event_count: 1",
                    "---",
                    "# MCP Session Record",
                    "",
                    "## Events",
                    "",
                    f"- 2026-06-03T00:00:00Z mcp_queue_claim by {claimed_by}; claim_id=claim_{task_id}_20260603_agent-01.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def intake_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "actor": "mcp.client",
            "source_tag": "wechat_bot",
            "client_tag": "acme_client",
            "external_ref": "wechat:msg-109",
            "title": "MCP intake test",
            "body": "A normalized intake request from MCP.",
            "submitted_at": "2026-05-21T10:00:00Z",
            "submitter_ref": "contact_hash_109",
            "capability_scope": {
                "token_ref": "cap_mcp_test",
                "operations": ["intake_submit"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
        }
        payload.update(overrides)
        return payload

    def owner_decision_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "actor": "mcp.client",
            "decision_id": "owner-decision-mcp-001",
            "decision_type": "approve_intake",
            "decision_status": "approved",
            "decided_at": "2026-05-21T10:00:00Z",
            "decided_by_ref": "owner_ref",
            "captured_by": "mcp.client",
            "capture_surface": "mcp",
            "decision_summary": "Approve the external intake for review.",
            "decision_rationale": "The request is within the client engagement scope.",
            "applies_to": {
                "project": "acme_client",
                "task_id": "EXT-ACME-001",
                "draft_path": "5_tasks/drafts/external_intake/example.md",
                "external_ref": "chat:msg-113",
            },
            "approval_scope": {
                "operation": "owner_decision_record",
                "authority_boundary": "record decision only",
                "allowed_next_action": "review_draft",
            },
            "owner_approval_evidence": {
                "evidence_id": "evidence-113",
                "source_tag": "wechat_bot",
                "client_tag": "acme_client",
                "external_ref": "chat:msg-113",
                "approval_actor_ref": "owner_ref",
                "approval_timestamp": "2026-05-21T10:00:00Z",
                "approval_intent": "approve_owner_decision_record",
                "evidence_hash": "sha256:mcp113",
                "evidence_ref": "chat:redacted:msg-113",
                "captured_by": "bot.local",
                "capture_method": "external_client",
                "redaction_status": "redacted",
                "refs": ["5_tasks/drafts/external_intake/example.md"],
            },
            "refs": ["5_tasks/drafts/external_intake/example.md"],
            "capability_scope": {
                "token_ref": "cap_mcp_test",
                "operations": ["owner_decision_record"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
        }
        payload.update(overrides)
        return payload

    def list_tool_names(self, capability_token: str | None = None) -> list[str]:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}
        if capability_token is not None:
            env["LYBRA_CAPABILITY_TOKEN"] = capability_token
        with patch.dict(os.environ, env, clear=True):
            response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response is not None
        return [tool["name"] for tool in response["result"]["tools"]]  # type: ignore[index]

    def data_paths(self) -> list[str]:
        return sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))

    def assert_tool_ok(self, response: dict[str, object]) -> dict[str, object]:
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertIsInstance(result, dict)
        structured = result["structuredContent"]  # type: ignore[index]
        self.assertIsInstance(structured, dict)
        return structured  # type: ignore[return-value]

    def test_tools_list_contains_only_mvp_read_tools(self) -> None:
        names = [tool["name"] for tool in TOOL_DESCRIPTORS]
        self.assertEqual(
            names,
            [
                "lybra_queue_list",
                "lybra_task_preview",
                "lybra_validate",
                "lybra_context_pack_build",
            ],
        )

    def test_tools_list_scope_gates_intake_write_tools(self) -> None:
        names_without_scope = self.list_tool_names(capability_token=self.capability_token(operations=[]))
        self.assertEqual(
            names_without_scope,
            [
                "lybra_queue_list",
                "lybra_task_preview",
                "lybra_validate",
                "lybra_context_pack_build",
            ],
        )

        names_with_scope = self.list_tool_names(capability_token=self.capability_token())
        self.assertIn("lybra_intake_submit_dry_run", names_with_scope)
        self.assertIn("lybra_intake_submit_confirm", names_with_scope)
        self.assertNotIn("lybra_owner_decision_record_dry_run", names_with_scope)

        names_with_owner_scope = self.list_tool_names(capability_token=self.capability_token(operations=["owner_decision_record"]))
        self.assertIn("lybra_owner_decision_record_dry_run", names_with_owner_scope)
        self.assertIn("lybra_owner_decision_record_confirm", names_with_owner_scope)
        self.assertNotIn("lybra_intake_submit_dry_run", names_with_owner_scope)

        names_with_claim_scope = self.list_tool_names(capability_token=self.capability_token(operations=["queue_claim", "owner_confirm"]))
        self.assertIn("lybra_queue_claim_dry_run", names_with_claim_scope)
        self.assertIn("lybra_queue_claim_confirm", names_with_claim_scope)
        self.assertNotIn("lybra_intake_submit_dry_run", names_with_claim_scope)

        names_with_return_scope = self.list_tool_names(capability_token=self.capability_token(operations=["queue_return", "owner_confirm"]))
        self.assertIn("lybra_queue_return_dry_run", names_with_return_scope)
        self.assertIn("lybra_queue_return_confirm", names_with_return_scope)
        self.assertNotIn("lybra_queue_claim_dry_run", names_with_return_scope)

        names_with_dispatch_scope = self.list_tool_names(capability_token=self.capability_token(operations=["audit_dispatch"]))
        self.assertIn("lybra_audit_dispatch_dry_run", names_with_dispatch_scope)
        self.assertIn("lybra_audit_dispatch_confirm", names_with_dispatch_scope)
        self.assertNotIn("lybra_audit_verdict_dry_run", names_with_dispatch_scope)

        names_with_verdict_scope = self.list_tool_names(capability_token=self.capability_token(operations=["audit_verdict"]))
        self.assertIn("lybra_audit_verdict_dry_run", names_with_verdict_scope)
        self.assertIn("lybra_audit_verdict_confirm", names_with_verdict_scope)
        self.assertNotIn("lybra_audit_dispatch_dry_run", names_with_verdict_scope)

    def test_write_tool_descriptions_are_self_documenting(self) -> None:
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["intake_submit", "owner_decision_record", "queue_claim", "queue_return", "audit_dispatch", "audit_verdict"]),
        }
        with patch.dict(os.environ, env, clear=True):
            response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response is not None
        descriptions = {
            tool["name"]: tool["description"]
            for tool in response["result"]["tools"]  # type: ignore[index]
            if (
                tool["name"].startswith("lybra_intake_submit")
                or tool["name"].startswith("lybra_owner_decision_record")
                or tool["name"].startswith("lybra_queue_claim")
                or tool["name"].startswith("lybra_queue_return")
                or tool["name"].startswith("lybra_audit_dispatch")
                or tool["name"].startswith("lybra_audit_verdict")
            )
        }
        self.assertEqual(
            set(descriptions),
            {
                "lybra_intake_submit_dry_run",
                "lybra_intake_submit_confirm",
                "lybra_owner_decision_record_dry_run",
                "lybra_owner_decision_record_confirm",
                "lybra_queue_claim_dry_run",
                "lybra_queue_claim_confirm",
                "lybra_queue_return_dry_run",
                "lybra_queue_return_confirm",
                "lybra_audit_dispatch_dry_run",
                "lybra_audit_dispatch_confirm",
                "lybra_audit_verdict_dry_run",
                "lybra_audit_verdict_confirm",
            },
        )
        for description in descriptions.values():
            self.assertIn("When to use", description)
            self.assertIn("Prerequisites", description)
            self.assertIn("Return structure", description)
            self.assertIn("Next-step hint", description)

    def test_queue_list_is_read_only(self) -> None:
        before = self.data_paths()
        structured = self.assert_tool_ok(self.call_tool("lybra_queue_list"))
        after = self.data_paths()
        self.assertEqual(before, after)
        self.assertEqual(structured["operation"], "get_queue")
        self.assertIn("tasks", structured["data"])  # type: ignore[operator]

    def test_task_preview_happy_path_and_missing_selector_error(self) -> None:
        structured = self.assert_tool_ok(
            self.call_tool("lybra_task_preview", {"task_id": "AIPOS-MCP-READ", "actor": "dev.codex.local"})
        )
        self.assertEqual(structured["operation"], "get_preview")
        self.assertEqual(structured["data"]["task_id"], "AIPOS-MCP-READ")  # type: ignore[index]

        error = self.assert_tool_ok(self.call_tool("lybra_task_preview", {}))
        self.assertFalse(error["ok"])
        self.assertEqual(error["verdict"], "BLOCK")

    def test_validate_happy_path(self) -> None:
        structured = self.assert_tool_ok(self.call_tool("lybra_validate"))
        self.assertEqual(structured["operation"], "get_validate")
        self.assertIn("summary", structured["data"])  # type: ignore[operator]

    def test_context_pack_build_happy_path_and_missing_selector_error(self) -> None:
        structured = self.assert_tool_ok(self.call_tool("lybra_context_pack_build", {"task_id": "AIPOS-MCP-READ"}))
        self.assertEqual(structured["operation"], "context_pack_preview")
        self.assertEqual(structured["data"]["task"]["task_id"], "AIPOS-MCP-READ")  # type: ignore[index]
        self.assertFalse(structured["data"]["writes_enabled"])  # type: ignore[index]

        error = self.assert_tool_ok(self.call_tool("lybra_context_pack_build", {}))
        self.assertFalse(error["ok"])
        self.assertEqual(error["verdict"], "BLOCK")

    def test_unknown_tool_returns_json_rpc_error(self) -> None:
        stdin = StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lybra_queue_claim","arguments":{}}}\n'
        )
        stdout = StringIO()
        stderr = StringIO()

        serve(stdin, stdout, stderr)

        response = json.loads(stdout.getvalue())
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32601)  # type: ignore[index]

    def test_intake_submit_dry_run_and_confirm_happy_path(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token()}
        pending_before = self.data_paths()
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_intake_submit_dry_run", self.intake_payload()))
            self.assertEqual(dry["operation"], "intake_submit")
            token = dry["dry_run_token"]
            confirm = self.assert_tool_ok(
                self.call_tool("lybra_intake_submit_confirm", {"dry_run_token": token, "actor": "mcp.client"})
            )

        self.assertEqual(confirm["operation"], "intake_submit")
        self.assertTrue(confirm["data"]["wrote"])  # type: ignore[index]
        self.assertTrue((self.repo_root / confirm["data"]["target_path"]).exists())  # type: ignore[index]
        pending_after = sorted(
            path.relative_to(self.repo_root).as_posix()
            for path in (self.repo_root / "5_tasks" / "queue" / "pending").iterdir()
        )
        self.assertEqual(pending_after, [path for path in pending_before if path.startswith("5_tasks/queue/pending/")])

    def claim_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "task_id": "AIPOS-MCP-CLAIM",
            "actor": "agent-01",
            "agent_instance": "agent-01",
            "autonomy_mode": "Supervised",
            "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
            "runtime_profile": "cc",
            "active_session_id": "session_mcp_claim_test",
            "context_bundle_ack": "ack",
            "with_records": True,
            "claim_reason": "test supervised explicit claim",
        }
        payload.update(overrides)
        return payload

    def return_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "task_id": "AIPOS-MCP-RETURN",
            "actor": "agent-01",
            "agent_instance": "agent-01",
            "autonomy_mode": "Supervised",
            "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
            "claim_id": "claim_AIPOS-MCP-RETURN_20260603_agent-01",
            "active_session_id": "session_AIPOS-MCP-RETURN_20260603_agent-01",
            "result_summary": "Executor completed the synthetic return task.",
            "artifact_refs": ["tools/mcp_server/tests/test_mcp_tools.py"],
            "completion_report_ref": "reports/aipos-169-return.md",
            "executor_status": "completed",
            "audit_readiness": "ready",
            "return_reason": "test supervised work return",
        }
        payload.update(overrides)
        return payload

    def dispatch_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_task_id": "AIPOS-MCP-RETURN",
            "actor": "agent-02",
            "agent_instance": "agent-02",
            "autonomy_mode": "Supervised",
            "owner_policy_ref": "owner_policy:aipos-178-supervised-audit-test",
            "audit_task_id": "AIPOS-MCP-AUDIT-01",
            "audit_task_title": "Audit MCP returned work",
            "audit_by": "dev_claude",
            "audit_agent_instance": "agent-02",
            "dispatch_reason": "test supervised audit dispatch",
        }
        payload.update(overrides)
        return payload

    def verdict_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "audit_task_id": "AIPOS-MCP-AUDIT-01",
            "reviewed_task_id": "AIPOS-MCP-RETURN",
            "actor": "agent-02",
            "agent_instance": "agent-02",
            "autonomy_mode": "Supervised",
            "owner_policy_ref": "owner_policy:aipos-178-supervised-verdict-test",
            "verdict": "PASS",
            "findings_summary": "Independent audit passed the returned work.",
            "evidence_refs": ["reports/aipos-178-audit.md"],
            "recommended_next_action": "ready_for_finalize_gate",
        }
        payload.update(overrides)
        return payload

    def prepare_returned_source(self) -> dict[str, object]:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
        return confirmed

    def dispatch_audit_task(self) -> dict[str, object]:
        self.prepare_returned_source()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
            self.assertTrue(dry["ok"], dry.get("blocking_reasons"))
            self.assertTrue(dry.get("dry_run_token"), dry)
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_audit_dispatch_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-02",
                        "agent_instance": "agent-02",
                        "owner_policy_ref": "owner_policy:aipos-178-supervised-audit-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
            self.assertTrue(confirmed["ok"], confirmed)
        return confirmed

    def claim_audit_task(self) -> dict[str, object]:
        self.dispatch_audit_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_dry_run",
                    self.claim_payload(
                        task_id="AIPOS-MCP-AUDIT-01",
                        actor="agent-02",
                        agent_instance="agent-02",
                        owner_policy_ref="owner_policy:aipos-178-audit-claim-test",
                    ),
                )
            )
            self.assertTrue(dry["ok"], dry.get("blocking_reasons"))
            self.assertTrue(dry.get("dry_run_token"), dry)
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-02",
                        "agent_instance": "agent-02",
                        "owner_policy_ref": "owner_policy:aipos-178-audit-claim-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
            self.assertTrue(confirmed["ok"], confirmed)
        return confirmed

    def test_queue_claim_dry_run_requires_scope_and_supervised_mode(self) -> None:
        self.write_claim_task()
        no_scope = self.assert_tool_ok(self.call_tool("lybra_queue_claim_dry_run", self.claim_payload()))
        self.assertEqual(no_scope["error_code"], "SCOPE_DENIED")

        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            delegated = self.assert_tool_ok(
                self.call_tool("lybra_queue_claim_dry_run", self.claim_payload(autonomy_mode="Delegated"))
            )
        self.assertEqual(delegated["error_code"], "INVALID_AUTONOMY_MODE")

    def test_queue_claim_dry_run_is_zero_write_and_owner_confirmed(self) -> None:
        self.write_claim_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        before = self.data_paths()
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_claim_dry_run", self.claim_payload()))
        after = self.data_paths()

        self.assertEqual(before, after)
        self.assertEqual(dry["operation"], "queue_claim")
        self.assertEqual(dry["surface"], "mcp")
        self.assertEqual(dry["autonomy_mode"], "Supervised")
        self.assertEqual(dry["canonical_agent_instance"], "agent-01")
        self.assertTrue(dry["owner_confirmation_required"])
        self.assertEqual(dry["owner_confirmation_token_required"], "OWNER_CONFIRMED")
        self.assertEqual(dry["lease_preview"]["lease_status"], "proposed")  # type: ignore[index]
        self.assertEqual(dry["confirmation_preview"]["confirm"]["tool_name"], "lybra_queue_claim_confirm")  # type: ignore[index]
        self.assertEqual(
            dry["confirmation_preview"]["copyable_confirm_arguments"]["owner_confirmation_token"],  # type: ignore[index]
            "OWNER_CONFIRMED",
        )
        self.assertIn("dry_run_token", dry)

    def test_queue_claim_confirm_requires_owner_confirmation_then_moves_claim_only(self) -> None:
        self.write_claim_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_claim_dry_run", self.claim_payload()))
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                    },
                )
            )
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(blocked["error_code"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["lease_status"], "proposed")
        self.assertTrue(all(item["wrote"] for item in confirmed["data"]["record_writes"]))  # type: ignore[index]
        self.assertTrue((self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-claim.md").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-mcp-claim.md").exists())
        records = load_records(self.repo_root)
        self.assertEqual(records["summary"]["claim_logs"], 1)
        self.assertEqual(records["summary"]["session_records"], 1)
        claim_record = records["claims"][0]["metadata"]
        session_record = records["sessions"][0]["metadata"]
        self.assertEqual(claim_record["record_type"], "claim_record")
        self.assertEqual(claim_record["lease_status"], "proposed")
        self.assertFalse(claim_record["active_lease_written"])
        self.assertEqual(session_record["session_status"], "claimed")
        self.assertEqual(session_record["lease_status"], "proposed")
        self.assertFalse(session_record["active_lease_written"])

    def test_queue_claim_blocks_wrong_specific_instance_and_forbidden_fields(self) -> None:
        self.write_claim_task(agent_instance="dev.claude.cc.local")
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            wrong_instance = self.assert_tool_ok(
                self.call_tool("lybra_queue_claim_dry_run", self.claim_payload(actor="agent-02", agent_instance="agent-02"))
            )
            forbidden = self.assert_tool_ok(
                self.call_tool("lybra_queue_claim_dry_run", self.claim_payload(raw_prompt="secret"))
            )

        self.assertEqual(wrong_instance["verdict"], "BLOCK")
        self.assertTrue(any("specific_instance_only" in item for item in wrong_instance["blocking_reasons"]))  # type: ignore[index]
        self.assertEqual(forbidden["error_code"], "UNSUPPORTED_QUEUE_CLAIM_FIELD")

    def test_queue_claim_confirm_rejects_non_mcp_dry_run_token(self) -> None:
        self.write_claim_task()
        local_dry = claim_task(task_id="AIPOS-MCP-CLAIM", actor="agent-01", dry_run=True, repo_root=self.repo_root)
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            rejected = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_confirm",
                    {
                        "dry_run_token": local_dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(rejected["error_code"], "INCOMPATIBLE_DRY_RUN")
        self.assertTrue((self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-mcp-claim.md").exists())

    def test_queue_return_dry_run_requires_scope_and_supervised_mode(self) -> None:
        self.write_return_task()
        no_scope = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
        self.assertEqual(no_scope["error_code"], "SCOPE_DENIED")

        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            delegated = self.assert_tool_ok(
                self.call_tool("lybra_queue_return_dry_run", self.return_payload(autonomy_mode="Delegated"))
            )
        self.assertEqual(delegated["error_code"], "INVALID_AUTONOMY_MODE")

    def test_queue_return_dry_run_is_zero_write_and_has_confirmation_preview(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        before = self.data_paths()
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
        after = self.data_paths()

        self.assertEqual(before, after)
        self.assertEqual(dry["operation"], "queue_return")
        self.assertEqual(dry["surface"], "mcp")
        self.assertEqual(dry["autonomy_mode"], "Supervised")
        self.assertEqual(dry["canonical_agent_instance"], "agent-01")
        self.assertTrue(dry["owner_confirmation_required"])
        self.assertEqual(dry["owner_confirmation_token_required"], "OWNER_CONFIRMED")
        self.assertEqual(dry["lease_preview"]["lease_status"], "proposed")  # type: ignore[index]
        self.assertEqual(dry["confirmation_preview"]["confirm"]["tool_name"], "lybra_queue_return_confirm")  # type: ignore[index]
        self.assertEqual(
            dry["confirmation_preview"]["copyable_confirm_arguments"]["owner_confirmation_token"],  # type: ignore[index]
            "OWNER_CONFIRMED",
        )
        self.assertIn("Verify returned work evidence", " ".join(dry["confirmation_preview"]["review_checklist"]))  # type: ignore[index]
        self.assertIn("dry_run_token", dry)

    def test_queue_return_confirm_requires_owner_confirmation_then_updates_claimed_task_only(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                    },
                )
            )
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(blocked["error_code"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["lease_status"], "proposed")
        self.assertTrue(all(item["wrote"] for item in confirmed["data"]["record_writes"]))  # type: ignore[index]
        path = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md"
        self.assertTrue(path.exists())
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "completed" / "aipos-mcp-return.md").exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("executor_status: completed", text)
        self.assertIn("audit_readiness: ready", text)
        self.assertIn("dependency_executor_status: completed", text)
        self.assertIn("dependency_audit_readiness: ready", text)
        self.assertIn("dependency_audit_status: pending", text)
        self.assertIn("return_record_ref: return_AIPOS-MCP-RETURN_", text)
        records = load_records(self.repo_root)
        self.assertEqual(records["summary"]["return_records"], 1)
        return_record = records["returns"][0]["metadata"]
        self.assertEqual(return_record["record_type"], "return_record")
        self.assertEqual(return_record["executor_status"], "completed")
        self.assertEqual(return_record["audit_readiness"], "ready")
        self.assertEqual(return_record["dependency_audit_status"], "pending")
        session_text = (
            self.repo_root
            / "5_tasks"
            / "records"
            / "sessions"
            / "AIPOS-MCP-RETURN"
            / "session_AIPOS-MCP-RETURN_20260603_agent-01.md"
        ).read_text(encoding="utf-8")
        self.assertIn("session_status: returned", session_text)
        self.assertIn("mcp_queue_return", session_text)
        recovery = build_state_recovery_preview(self.repo_root, task_id="AIPOS-MCP-RETURN")
        self.assertEqual(recovery["provenance_completeness"], "complete")
        self.assertEqual(recovery["provenance_chain"]["return"]["record_status"], "ok")

    def test_aipos197_return_confirm_denied_without_owner_confirm_scope(self) -> None:
        # AIPOS-197 / F-candidate-1: a token that can dry-run (queue_return) but lacks
        # owner_confirm is structurally denied confirm — even with OWNER_CONFIRMED.
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return"]),  # NO owner_confirm
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            self.assertTrue(dry["ok"], dry.get("blocking_reasons"))  # dry-run still allowed
            denied = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",  # knows the literal, still denied
                    },
                )
            )
        self.assertEqual(denied["error_code"], "SCOPE_DENIED")
        # No truth mutation: task stays claimed-only with no return record.
        self.assertEqual(load_records(self.repo_root)["summary"]["return_records"], 0)

    def test_aipos197_claim_confirm_denied_without_owner_confirm_scope(self) -> None:
        self.write_claim_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim"]),  # NO owner_confirm
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_claim_dry_run", self.claim_payload()))
            self.assertTrue(dry["ok"], dry.get("blocking_reasons"))
            denied = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
        self.assertEqual(denied["error_code"], "SCOPE_DENIED")

    def test_aipos197_return_confirm_records_confirmer_attribution(self) -> None:
        # owner_confirm token confirms OK and the return record attributes the confirmer
        # (role + non-secret fingerprint), so L3 can tell Owner-confirm from self-confirm.
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(
                operations=["queue_return", "owner_confirm"], role="owner", fingerprint="sha256:ownerfp01"
            ),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
        self.assertTrue(confirmed["ok"], confirmed)
        records = load_records(self.repo_root)
        rec = records["returns"][0]["metadata"]
        self.assertEqual(rec.get("confirmer_role"), "owner")
        self.assertEqual(rec.get("confirmer_token_fingerprint"), "sha256:ownerfp01")
        # §9 signature-ready placeholders present (empty in v0).
        self.assertIn("gate_signature", rec)

    def test_aipos199_claim_confirm_records_confirmer_attribution(self) -> None:
        # AIPOS-199 / RF-5: the on-disk CLAIM record (written THROUGH the confirm tool
        # handler, not a direct function call) must attribute the confirmer — the live
        # 191B rerun showed these fields empty on the claim path while the return path
        # filled them. Reading load_records() asserts what landed on disk at confirm.
        self.write_claim_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(
                operations=["queue_claim", "owner_confirm"], role="owner", fingerprint="sha256:ownerfp01"
            ),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_claim_dry_run", self.claim_payload()))
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
        self.assertTrue(confirmed["ok"], confirmed)
        records = load_records(self.repo_root)
        rec = records["claims"][0]["metadata"]
        self.assertEqual(rec.get("confirmer_role"), "owner")
        self.assertEqual(rec.get("confirmer_token_ref"), "cap_mcp_test")
        self.assertEqual(rec.get("confirmer_token_fingerprint"), "sha256:ownerfp01")
        # §9 signature-ready placeholders present (empty in v0).
        self.assertIn("gate_signature", rec)

    def test_queue_return_confirm_reuses_planned_timestamp_for_stable_snapshot(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            time.sleep(1.1)
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertTrue(confirmed["ok"])
        self.assertNotIn("error_code", confirmed)

    def test_queue_return_dry_run_snapshot_ignores_generated_return_timestamp(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            first = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            time.sleep(1.1)
            second = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))

        self.assertNotEqual(
            first["data"]["original_payload"]["planned_returned_at"],  # type: ignore[index]
            second["data"]["original_payload"]["planned_returned_at"],  # type: ignore[index]
        )
        self.assertEqual(first["dry_run_snapshot_hash"], second["dry_run_snapshot_hash"])

    def test_queue_return_confirm_blocks_when_claimed_task_changes_after_dry_run(self) -> None:
        self.write_return_task()
        task_path = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md"
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_queue_return_dry_run", self.return_payload()))
            task_path.write_text(task_path.read_text(encoding="utf-8") + "\nStale source mutation.\n", encoding="utf-8")
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(blocked["error_code"], "SNAPSHOT_MISMATCH")
        text = task_path.read_text(encoding="utf-8")
        self.assertNotIn("executor_status: completed", text)
        records = load_records(self.repo_root)
        self.assertEqual(records["summary"]["return_records"], 0)

    def _make_scratch_root(self) -> Path:
        scratch_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(scratch_tmp.cleanup)
        return Path(scratch_tmp.name).resolve()

    def _scratch_return_env(self, approved_root: Path) -> dict[str, str]:
        return {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
            "LYBRA_APPROVED_SCRATCH_ROOT": str(approved_root),
        }

    def test_queue_return_ingests_scratch_artifact_into_workspace(self) -> None:
        self.write_return_task()
        approved_root = self._make_scratch_root()
        scratch_dir = approved_root / "worker-run"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "result.md").write_text("# gate ingested artifact\n", encoding="utf-8")
        env = self._scratch_return_env(approved_root)
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(
                        artifact_refs=[],
                        completion_report_ref="",
                        scratch_dir=str(scratch_dir),
                        scratch_artifact_refs=["result.md"],
                    ),
                )
            )
            self.assertTrue(dry["ok"], dry.get("blocking_reasons"))
            ingest_writes = [
                item
                for item in dry["data"]["original_payload"].get("scratch_artifact_refs", [])  # type: ignore[index]
            ]
            self.assertEqual(ingest_writes, ["result.md"])
            ingested_refs = dry["data"]["ingested_artifact_refs"]  # type: ignore[index]
            self.assertEqual(len(ingested_refs), 1)
            workspace_rel = ingested_refs[0]
            self.assertTrue(workspace_rel.startswith("workspace_artifacts/AIPOS-MCP-RETURN/"), workspace_rel)
            # Not effective truth before ingestion.
            self.assertFalse((self.repo_root / workspace_rel).exists())

            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertTrue(confirmed["ok"], confirmed)
        dest = self.repo_root / workspace_rel
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(encoding="utf-8"), "# gate ingested artifact\n")
        # Persisted artifact_refs point at the gate-written workspace path, not scratch.
        task_text = (self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md").read_text(encoding="utf-8")
        self.assertIn(workspace_rel, task_text)
        self.assertNotIn(str(scratch_dir), task_text)
        records = load_records(self.repo_root)
        return_record = records["returns"][0]["metadata"]
        self.assertIn(workspace_rel, return_record.get("artifact_refs", []))

    def test_queue_return_scratch_requires_owner_confirmation(self) -> None:
        self.write_return_task()
        approved_root = self._make_scratch_root()
        scratch_dir = approved_root / "worker-run"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "result.md").write_text("artifact\n", encoding="utf-8")
        env = self._scratch_return_env(approved_root)
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(scratch_dir=str(scratch_dir), scratch_artifact_refs=["result.md"]),
                )
            )
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                    },
                )
            )
        self.assertEqual(blocked["error_code"], "OWNER_CONFIRMATION_REQUIRED")
        ingested = dry["data"]["ingested_artifact_refs"][0]  # type: ignore[index]
        self.assertFalse((self.repo_root / ingested).exists())

    def test_queue_return_scratch_requires_approved_root(self) -> None:
        self.write_return_task()
        approved_root = self._make_scratch_root()
        scratch_dir = approved_root / "worker-run"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        (scratch_dir / "result.md").write_text("artifact\n", encoding="utf-8")
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(scratch_dir=str(scratch_dir), scratch_artifact_refs=["result.md"]),
                )
            )
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertTrue(any("approved scratch root" in r for r in blocked["blocking_reasons"]))  # type: ignore[index]

    def test_queue_return_scratch_symlink_escape_blocked(self) -> None:
        self.write_return_task()
        approved_root = self._make_scratch_root()
        scratch_dir = approved_root / "worker-run"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        truth_target = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md"
        os.symlink(truth_target, scratch_dir / "evil.md")
        env = self._scratch_return_env(approved_root)
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(scratch_dir=str(scratch_dir), scratch_artifact_refs=["evil.md"]),
                )
            )
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertTrue(any("escapes scratch_dir" in r for r in blocked["blocking_reasons"]))  # type: ignore[index]

    def test_queue_return_scratch_parent_escape_blocked(self) -> None:
        self.write_return_task()
        approved_root = self._make_scratch_root()
        scratch_dir = approved_root / "worker-run"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        env = self._scratch_return_env(approved_root)
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(
                        scratch_dir=str(scratch_dir),
                        scratch_artifact_refs=["../../5_tasks/queue/claimed/aipos-mcp-return.md"],
                    ),
                )
            )
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertTrue(any("escapes scratch_dir" in r for r in blocked["blocking_reasons"]))  # type: ignore[index]

    def test_queue_return_scratch_content_swap_blocks_confirm(self) -> None:
        self.write_return_task()
        approved_root = self._make_scratch_root()
        scratch_dir = approved_root / "worker-run"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        scratch_file = scratch_dir / "result.md"
        scratch_file.write_text("original\n", encoding="utf-8")
        env = self._scratch_return_env(approved_root)
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(scratch_dir=str(scratch_dir), scratch_artifact_refs=["result.md"]),
                )
            )
            scratch_file.write_text("tampered\n", encoding="utf-8")
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )
        self.assertEqual(blocked["error_code"], "SNAPSHOT_MISMATCH")
        ingested = dry["data"]["ingested_artifact_refs"][0]  # type: ignore[index]
        self.assertFalse((self.repo_root / ingested).exists())

    def test_queue_return_blocks_wrong_claimant_and_forbidden_fields(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            wrong_instance = self.assert_tool_ok(
                self.call_tool("lybra_queue_return_dry_run", self.return_payload(actor="agent-02", agent_instance="agent-02"))
            )
            forbidden = self.assert_tool_ok(
                self.call_tool("lybra_queue_return_dry_run", self.return_payload(raw_response="secret"))
            )

        self.assertEqual(wrong_instance["verdict"], "BLOCK")
        self.assertTrue(any("CLAIMANT_MISMATCH" in item or "specific_instance_only" in item for item in wrong_instance["blocking_reasons"]))  # type: ignore[index]
        self.assertEqual(forbidden["error_code"], "UNSUPPORTED_QUEUE_RETURN_FIELD")

    def test_queue_return_blocks_missing_evidence(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_dry_run",
                    self.return_payload(result_summary="", artifact_refs=[], completion_report_ref=""),
                )
            )

        self.assertEqual(blocked["error_code"], "MISSING_RETURN_EVIDENCE")
        self.assertIn("non-secret executor evidence", blocked["suggested_next_action"])

    def test_queue_return_blocks_invalid_executor_status(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool("lybra_queue_return_dry_run", self.return_payload(executor_status="in_progress"))
            )

        self.assertEqual(blocked["error_code"], "INVALID_EXECUTOR_STATUS")

    def test_queue_return_blocks_invalid_audit_readiness(self) -> None:
        self.write_return_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool("lybra_queue_return_dry_run", self.return_payload(audit_readiness="not_ready"))
            )

        self.assertEqual(blocked["error_code"], "INVALID_AUDIT_READINESS")

    def test_queue_return_confirm_rejects_non_mcp_dry_run_token(self) -> None:
        self.write_return_task()
        payload = self.return_payload()
        local_dry = return_task(
            task_id=str(payload["task_id"]),
            actor=str(payload["actor"]),
            agent_instance=str(payload["agent_instance"]),
            owner_policy_ref=str(payload["owner_policy_ref"]),
            claim_id=str(payload["claim_id"]),
            active_session_id=str(payload["active_session_id"]),
            result_summary=str(payload["result_summary"]),
            artifact_refs=payload["artifact_refs"],
            completion_report_ref=str(payload["completion_report_ref"]),
            return_reason=str(payload["return_reason"]),
            dry_run=True,
            repo_root=self.repo_root,
        )
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_return", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            rejected = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_return_confirm",
                    {
                        "dry_run_token": local_dry["dry_run_token"],
                        "actor": "agent-01",
                        "agent_instance": "agent-01",
                        "owner_policy_ref": "owner_policy:aipos-169-supervised-return-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(rejected["error_code"], "INCOMPATIBLE_DRY_RUN")
        text = (self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md").read_text(encoding="utf-8")
        self.assertNotIn("executor_status: completed", text)

    def test_audit_dispatch_dry_run_requires_scope_and_supervised_mode(self) -> None:
        self.prepare_returned_source()
        no_scope = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
        self.assertEqual(no_scope["error_code"], "SCOPE_DENIED")

        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
        }
        with patch.dict(os.environ, env, clear=True):
            delegated = self.assert_tool_ok(
                self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload(autonomy_mode="Delegated"))
            )
        self.assertEqual(delegated["error_code"], "INVALID_AUTONOMY_MODE")

    def test_audit_dispatch_confirm_requires_owner_confirmation_then_creates_pending_audit_task(self) -> None:
        self.prepare_returned_source()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
        }
        before = self.data_paths()
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
        after = self.data_paths()
        self.assertEqual(before, after)

        if not _HAS_YAML:
            # BARE: executor_registry_verified=False → INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY
            # blocks the dry-run. Fail-closed: no dispatch token is issued.
            self.assertEqual(dry["verdict"], "BLOCK", dry)
            self.assertEqual(dry["error_code"], "AUDIT_ACTION_BLOCKED", dry)
            self.assertTrue(
                any("INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY" in str(r) for r in dry.get("blocking_reasons", [])),
                f"Expected INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY in blocking_reasons; got: {dry.get('blocking_reasons')}",
            )
            self.assertIsNone(dry.get("dry_run_token"), "No dispatch token must be issued when blocked")
            return

        self.assertEqual(dry["operation"], "audit_dispatch")
        self.assertEqual(dry["surface"], "mcp")
        self.assertEqual(dry["autonomy_mode"], "Supervised")
        self.assertEqual(dry["canonical_agent_instance"], "agent-02")
        self.assertEqual(dry["reviewed_executor_instance"], "agent-01")
        self.assertEqual(dry["confirmation_preview"]["confirm"]["tool_name"], "lybra_audit_dispatch_confirm")  # type: ignore[index]
        self.assertEqual(
            dry["confirmation_preview"]["copyable_confirm_arguments"]["owner_confirmation_token"],  # type: ignore[index]
            "OWNER_CONFIRMED",
        )

        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_audit_dispatch_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-02",
                        "agent_instance": "agent-02",
                        "owner_policy_ref": "owner_policy:aipos-178-supervised-audit-test",
                    },
                )
            )
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_audit_dispatch_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-02",
                        "agent_instance": "agent-02",
                        "owner_policy_ref": "owner_policy:aipos-178-supervised-audit-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(blocked["error_code"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(confirmed["ok"])
        self.assertTrue(all(item["wrote"] for item in confirmed["data"]["record_writes"]))  # type: ignore[index]
        audit_path = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-mcp-audit-01.md"
        self.assertTrue(audit_path.exists())
        audit_metadata, _, _ = parse_markdown_frontmatter(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit_metadata["task_mode"], "audit")
        self.assertEqual(audit_metadata["agent_instance"], "agent-02")
        self.assertEqual(audit_metadata["reviewed_task_id"], "AIPOS-MCP-RETURN")
        self.assertEqual(audit_metadata["reviewed_executor_instance"], "agent-01")
        self.assertTrue(audit_metadata["independence_distinct_instance"])
        source_text = (self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md").read_text(encoding="utf-8")
        self.assertIn("related_audit_task_ref: AIPOS-MCP-AUDIT-01", source_text)
        self.assertIn("audit_dispatch_record_ref: dispatch_AIPOS-MCP-RETURN_", source_text)
        records = load_records(self.repo_root)
        self.assertEqual(records["summary"]["audit_dispatch_records"], 1)
        self.assertEqual(records["audit_dispatches"][0]["metadata"]["record_type"], "audit_dispatch_record")

    def test_audit_dispatch_blocks_same_executor_auditor(self) -> None:
        self.prepare_returned_source()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_audit_dispatch_dry_run",
                    self.dispatch_payload(actor="agent-01", agent_instance="agent-01", audit_agent_instance="agent-01"),
                )
            )
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertEqual(blocked["error_code"], "INDEPENDENCE_FAILED")

    def test_audit_task_claim_blocks_same_executor_instance(self) -> None:
        self.write_claim_task(task_id="AIPOS-MCP-AUDIT-SELF", agent_instance="agent-01")
        audit_path = self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-mcp-audit-self.md"
        text = audit_path.read_text(encoding="utf-8")
        text = text.replace("task_mode: code", "task_mode: audit")
        text = text.replace(
            "---\nSupervised MCP claim test task.",
            "reviewed_executor_instance: agent-01\nindependence_distinct_instance: true\n---\nSupervised MCP audit claim test task.",
        )
        audit_path.write_text(text, encoding="utf-8")
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim", "owner_confirm"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_queue_claim_dry_run",
                    self.claim_payload(task_id="AIPOS-MCP-AUDIT-SELF", actor="agent-01", agent_instance="agent-01"),
                )
            )
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertTrue(
            any("independence_distinct_instance" in item for item in blocked["blocking_reasons"])  # type: ignore[index]
        )

    def test_audit_verdict_confirm_requires_owner_confirmation_then_records_pass_without_finalize(self) -> None:
        if not _HAS_YAML:
            # BARE: audit dispatch is fail-closed (INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY) →
            # claim_audit_task() cannot proceed. Assert the dispatch block directly, then return.
            self.prepare_returned_source()
            env = {
                "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
                "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
            }
            with patch.dict(os.environ, env, clear=True):
                dry = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
            self.assertEqual(dry["verdict"], "BLOCK", dry)
            self.assertEqual(dry["error_code"], "AUDIT_ACTION_BLOCKED", dry)
            self.assertTrue(
                any("INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY" in str(r) for r in dry.get("blocking_reasons", [])),
                f"Expected INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY; got: {dry.get('blocking_reasons')}",
            )
            return
        self.claim_audit_task()
        audit_path = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-audit-01.md"
        source_path = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md"
        audit_metadata, _, _ = parse_markdown_frontmatter(audit_path.read_text(encoding="utf-8"))
        source_metadata, _, _ = parse_markdown_frontmatter(source_path.read_text(encoding="utf-8"))
        payload = self.verdict_payload(
            claim_id=audit_metadata["claim_id"],
            active_session_id=audit_metadata["active_session_id"],
            audit_dispatch_record_ref=source_metadata["audit_dispatch_record_ref"],
            reviewed_return_record_ref=source_metadata["return_record_ref"],
        )
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_verdict"]),
        }
        before = self.data_paths()
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_audit_verdict_dry_run", payload))
        after = self.data_paths()
        self.assertEqual(before, after)
        self.assertEqual(dry["operation"], "audit_verdict")
        self.assertEqual(dry["surface"], "mcp")
        self.assertEqual(dry["autonomy_mode"], "Supervised")
        self.assertEqual(dry["canonical_agent_instance"], "agent-02")
        self.assertEqual(dry["reviewed_executor_instance"], "agent-01")
        self.assertEqual(dry["data"]["verdict"], "PASS")  # type: ignore[index]
        self.assertEqual(dry["confirmation_preview"]["confirm"]["tool_name"], "lybra_audit_verdict_confirm")  # type: ignore[index]

        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(
                self.call_tool(
                    "lybra_audit_verdict_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-02",
                        "agent_instance": "agent-02",
                        "owner_policy_ref": "owner_policy:aipos-178-supervised-verdict-test",
                    },
                )
            )
            confirmed = self.assert_tool_ok(
                self.call_tool(
                    "lybra_audit_verdict_confirm",
                    {
                        "dry_run_token": dry["dry_run_token"],
                        "actor": "agent-02",
                        "agent_instance": "agent-02",
                        "owner_policy_ref": "owner_policy:aipos-178-supervised-verdict-test",
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                    },
                )
            )

        self.assertEqual(blocked["error_code"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(confirmed["ok"])
        record_reports = confirmed["data"]["record_writes"] + confirmed["data"]["record_updates"]  # type: ignore[index]
        self.assertTrue(all(item.get("wrote") or item.get("updated") for item in record_reports))
        source_text = source_path.read_text(encoding="utf-8")
        self.assertIn("dependency_audit_status: PASS", source_text)
        self.assertIn("audit_status: PASS", source_text)
        self.assertIn("related_audit_verdict_ref: verdict_AIPOS-MCP-RETURN_", source_text)
        self.assertNotIn("finalize_performed: true", source_text)
        self.assertNotIn("accepted_work_unblocked: true", source_text)
        records = load_records(self.repo_root)
        self.assertEqual(records["summary"]["audit_verdict_records"], 1)
        self.assertEqual(records["audit_verdicts"][0]["metadata"]["record_type"], "audit_verdict_record")
        session_text = (
            self.repo_root
            / "5_tasks"
            / "records"
            / "sessions"
            / "AIPOS-MCP-AUDIT-01"
            / str(audit_metadata["active_session_id"] + ".md")
        ).read_text(encoding="utf-8")
        self.assertIn("session_status: audit_verdict", session_text)
        self.assertIn("mcp_audit_verdict", session_text)

    def test_audit_dispatch_blocks_registry_unverified_executor_real_path(self) -> None:
        """AIPOS-219 §6b condition ③ — REAL-PATH negative control (closes ★C2).

        An executor that returned registry-unverified (e.g. on bare python) has
        ``executor_registry_verified: false`` stored on its source record. A later audit dispatch —
        even WITH PyYAML present (auditor side verified) and a *distinct* auditor instance — must
        BLOCK ``INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`` rather than falsely accept distinctness. This
        drives the real board_adapter dispatch path (not a re-implementation of the check): with
        PyYAML present the block can ONLY come from the executor-side guard, so deleting that guard
        makes this test fail.
        """
        self.prepare_returned_source()
        # Ensure the source records a registry-UNVERIFIED executor. With PyYAML the return records
        # `true`, so flip it (simulating a bare-python return); on bare python it is already `false`.
        marked_unverified = False
        for path in (self.repo_root / "5_tasks").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if "executor_registry_verified: true" in text:
                path.write_text(text.replace("executor_registry_verified: true", "executor_registry_verified: false"), encoding="utf-8")
                marked_unverified = True
            elif "executor_registry_verified: false" in text:
                marked_unverified = True
        self.assertTrue(marked_unverified, "return path must record executor_registry_verified on the source")
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
        self.assertEqual(dry["verdict"], "BLOCK", dry)
        self.assertTrue(
            any("INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY" in str(r) for r in dry.get("blocking_reasons", [])),
            f"registry-unverified executor must fail-closed; got: {dry.get('blocking_reasons')}",
        )

    def test_audit_dispatch_passes_when_both_registry_verified_real_path(self) -> None:
        """Positive control (real path): with PyYAML present and both sides registry-verified, a
        distinct auditor dispatch is NOT blocked by the P3 guard — proving the guard is specific to
        the unverified case and does not over-block the normal path."""
        if not _HAS_YAML:
            self.skipTest("positive control requires PyYAML (registry-verified executor side)")
        self.prepare_returned_source()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
        }
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
        self.assertFalse(
            any("INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY" in str(r) for r in dry.get("blocking_reasons", [])),
            f"both sides registry-verified must NOT trigger the no-registry block; got: {dry.get('blocking_reasons')}",
        )

    def test_audit_verdict_blocks_same_executor_auditor(self) -> None:
        if not _HAS_YAML:
            # BARE: audit dispatch is fail-closed (INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY) →
            # claim_audit_task() cannot proceed; audit verdict is unreachable.
            # Assert that the dispatch itself blocks with the correct error code.
            self.prepare_returned_source()
            env = {
                "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
                "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_dispatch"]),
            }
            with patch.dict(os.environ, env, clear=True):
                dry = self.assert_tool_ok(self.call_tool("lybra_audit_dispatch_dry_run", self.dispatch_payload()))
            self.assertEqual(dry["verdict"], "BLOCK", dry)
            self.assertEqual(dry["error_code"], "AUDIT_ACTION_BLOCKED", dry)
            self.assertTrue(
                any("INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY" in str(r) for r in dry.get("blocking_reasons", [])),
                f"Expected INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY; got: {dry.get('blocking_reasons')}",
            )
            return
        self.claim_audit_task()
        audit_path = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-audit-01.md"
        source_path = self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-return.md"
        text = audit_path.read_text(encoding="utf-8").replace("reviewed_executor_instance: agent-01", "reviewed_executor_instance: agent-02")
        audit_path.write_text(text, encoding="utf-8")
        audit_metadata, _, _ = parse_markdown_frontmatter(audit_path.read_text(encoding="utf-8"))
        source_metadata, _, _ = parse_markdown_frontmatter(source_path.read_text(encoding="utf-8"))
        payload = self.verdict_payload(
            claim_id=audit_metadata["claim_id"],
            active_session_id=audit_metadata["active_session_id"],
            audit_dispatch_record_ref=source_metadata["audit_dispatch_record_ref"],
            reviewed_return_record_ref=source_metadata["return_record_ref"],
        )
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["audit_verdict"]),
        }
        with patch.dict(os.environ, env, clear=True):
            blocked = self.assert_tool_ok(self.call_tool("lybra_audit_verdict_dry_run", payload))
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertEqual(blocked["error_code"], "INDEPENDENCE_FAILED")

    def test_scope_denied_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=[])}
        with patch.dict(os.environ, env, clear=True):
            response = self.assert_tool_ok(self.call_tool("lybra_intake_submit_dry_run", self.intake_payload()))
        self.assertEqual(response["error_code"], "SCOPE_DENIED")
        self.assertIn("suggested_next_action", response)

    def test_confirm_missing_token_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token()}
        with patch.dict(os.environ, env, clear=True):
            response = self.assert_tool_ok(self.call_tool("lybra_intake_submit_confirm", {}))
        self.assertEqual(response["error_code"], "MISSING_DRY_RUN_TOKEN")
        self.assertIn("lybra_intake_submit_dry_run", response["suggested_next_action"])

    def test_dry_run_invalid_source_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token()}
        with patch.dict(os.environ, env, clear=True):
            response = self.assert_tool_ok(
                self.call_tool("lybra_intake_submit_dry_run", self.intake_payload(source_tag="WeChat Bot"))
            )
        self.assertEqual(response["error_code"], "INVALID_SOURCE")
        self.assertIn("doc_ref", response)

    def test_confirm_expired_token_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token()}
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_intake_submit_dry_run", self.intake_payload()))
            token = get_dry_run(str(dry["dry_run_token"]))
            assert token is not None
            token.expires_at = "2000-01-01T00:00:00Z"
            response = self.assert_tool_ok(
                self.call_tool("lybra_intake_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "mcp.client"})
            )
        self.assertEqual(response["error_code"], "TOKEN_EXPIRED")

    def test_confirm_snapshot_mismatch_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token()}
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_intake_submit_dry_run", self.intake_payload()))
            target_path = self.repo_root / dry["data"]["target_path"]  # type: ignore[index]
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("collision", encoding="utf-8")
            response = self.assert_tool_ok(
                self.call_tool("lybra_intake_submit_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "mcp.client"})
            )
        self.assertEqual(response["error_code"], "SNAPSHOT_MISMATCH")

    def test_owner_decision_record_dry_run_and_confirm_happy_path(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["owner_decision_record"])}
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_owner_decision_record_dry_run", self.owner_decision_payload()))
            self.assertEqual(dry["operation"], "owner_decision_record")
            token = dry["dry_run_token"]
            confirm = self.assert_tool_ok(
                self.call_tool("lybra_owner_decision_record_confirm", {"dry_run_token": token, "actor": "mcp.client"})
            )

        self.assertEqual(confirm["operation"], "owner_decision_record")
        self.assertTrue(confirm["data"]["wrote"])  # type: ignore[index]
        self.assertTrue((self.repo_root / confirm["data"]["target_path"]).exists())  # type: ignore[index]
        self.assertFalse((self.repo_root / "5_tasks" / "drafts").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "orchestration").exists())

    def test_owner_decision_scope_denied_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=[])}
        with patch.dict(os.environ, env, clear=True):
            response = self.assert_tool_ok(self.call_tool("lybra_owner_decision_record_dry_run", self.owner_decision_payload()))
        self.assertEqual(response["error_code"], "SCOPE_DENIED")
        self.assertIn("owner_decision_record", response["suggested_next_action"])

    def test_owner_decision_confirm_missing_token_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["owner_decision_record"])}
        with patch.dict(os.environ, env, clear=True):
            response = self.assert_tool_ok(self.call_tool("lybra_owner_decision_record_confirm", {}))
        self.assertEqual(response["error_code"], "MISSING_DRY_RUN_TOKEN")
        self.assertIn("lybra_owner_decision_record_dry_run", response["suggested_next_action"])

    def test_owner_decision_missing_evidence_returns_structured_teaching_error(self) -> None:
        payload = self.owner_decision_payload()
        payload.pop("owner_approval_evidence")
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["owner_decision_record"])}
        with patch.dict(os.environ, env, clear=True):
            response = self.assert_tool_ok(self.call_tool("lybra_owner_decision_record_dry_run", payload))
        self.assertEqual(response["error_code"], "MISSING_OWNER_APPROVAL_EVIDENCE")
        self.assertIn("AIPOS-110", response["doc_ref"])

    def test_owner_decision_confirm_expired_token_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["owner_decision_record"])}
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_owner_decision_record_dry_run", self.owner_decision_payload()))
            token = get_dry_run(str(dry["dry_run_token"]))
            assert token is not None
            token.expires_at = "2000-01-01T00:00:00Z"
            response = self.assert_tool_ok(
                self.call_tool("lybra_owner_decision_record_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "mcp.client"})
            )
        self.assertEqual(response["error_code"], "TOKEN_EXPIRED")

    def test_owner_decision_confirm_snapshot_mismatch_returns_structured_teaching_error(self) -> None:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["owner_decision_record"])}
        with patch.dict(os.environ, env, clear=True):
            dry = self.assert_tool_ok(self.call_tool("lybra_owner_decision_record_dry_run", self.owner_decision_payload()))
            target_path = self.repo_root / dry["data"]["target_path"]  # type: ignore[index]
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("collision", encoding="utf-8")
            response = self.assert_tool_ok(
                self.call_tool("lybra_owner_decision_record_confirm", {"dry_run_token": dry["dry_run_token"], "actor": "mcp.client"})
            )
        self.assertEqual(response["error_code"], "SNAPSHOT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
