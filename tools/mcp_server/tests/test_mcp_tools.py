from __future__ import annotations

import os
import tempfile
import unittest
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.aipos_cli.controlled_execute import clear_tokens, get_dry_run
from tools.aipos_cli.board_adapter import claim_task
from tools.mcp_server.server import handle_request, serve
from tools.mcp_server.tools import TOOL_DESCRIPTORS


class McpToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "2_projects" / "acme_client").mkdir(parents=True, exist_ok=True)
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

    def capability_token(self, operations: list[str] | None = None) -> str:
        return json.dumps(
            {
                "token_ref": "cap_mcp_test",
                "operations": operations if operations is not None else ["intake_submit"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
            }
        )

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

        names_with_claim_scope = self.list_tool_names(capability_token=self.capability_token(operations=["queue_claim"]))
        self.assertIn("lybra_queue_claim_dry_run", names_with_claim_scope)
        self.assertIn("lybra_queue_claim_confirm", names_with_claim_scope)
        self.assertNotIn("lybra_intake_submit_dry_run", names_with_claim_scope)

    def test_write_tool_descriptions_are_self_documenting(self) -> None:
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["intake_submit", "owner_decision_record", "queue_claim"]),
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

    def test_queue_claim_dry_run_requires_scope_and_supervised_mode(self) -> None:
        self.write_claim_task()
        no_scope = self.assert_tool_ok(self.call_tool("lybra_queue_claim_dry_run", self.claim_payload()))
        self.assertEqual(no_scope["error_code"], "SCOPE_DENIED")

        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim"]),
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
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim"]),
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
        self.assertIn("dry_run_token", dry)

    def test_queue_claim_confirm_requires_owner_confirmation_then_moves_claim_only(self) -> None:
        self.write_claim_task()
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim"]),
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
        self.assertTrue((self.repo_root / "5_tasks" / "queue" / "claimed" / "aipos-mcp-claim.md").exists())
        self.assertFalse((self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-mcp-claim.md").exists())

    def test_queue_claim_blocks_wrong_specific_instance_and_forbidden_fields(self) -> None:
        self.write_claim_task(agent_instance="dev.claude.cc.local")
        env = {
            "AIPOS_WORKSPACE_ROOT": str(self.repo_root),
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim"]),
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
            "LYBRA_CAPABILITY_TOKEN": self.capability_token(operations=["queue_claim"]),
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
