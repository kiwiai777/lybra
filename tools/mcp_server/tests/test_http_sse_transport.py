from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
from urllib import error, request

from tools.aipos_cli.records import load_records
from tools.mcp_server.http_sse import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    DEFAULT_KEEPALIVE_SECONDS,
    SESSION_HEADER,
    HttpSseConfig,
    MCP_RPC_PATH,
    MCP_SSE_PATH,
    build_http_server,
    run_http_server,
)

# AIPOS-231 (no-regret test-harness hardening): centralize the previously-scattered magic timeouts
# into one point of control, and widen the client budget with a stated rationale. Under WSL2 load the
# REAL response latency can exceed a 3 s client budget — the product response is correct; the test's
# latency bound was simply too tight. The server-thread join (after httpd.shutdown(), which stops
# serve_forever) normally returns promptly; the cap is only a deadlock backstop. This is no-regret
# hardening, NOT a flake fix: the accept-race hypothesis was refuted by evidence and the root cause
# is unreproducible in the dev environment (item #2 stays OPEN, measured at the release-gate).
_HTTP_CLIENT_TIMEOUT = 15.0  # urlopen connect+read budget (was a tight 3 s)
_SERVER_JOIN_TIMEOUT = 10.0  # server-thread join backstop after shutdown() (was a 2 s cap)


class HttpSseTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "2_projects" / "acme_client").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "queue" / "pending" / "example.md").write_text(
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-MCP-HTTP",
                    "title: MCP HTTP Read Test",
                    "project: lybra",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "---",
                    "Read-only MCP HTTP test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def capability_token(
        self,
        operations: list[str] | None = None,
        *,
        role: str | None = None,
        fingerprint: str | None = None,
        token_ref: str = "cap_http_test",
    ) -> str:
        payload: dict[str, object] = {
            "token_ref": token_ref,
            "operations": operations if operations is not None else ["intake_submit"],
            "projects": ["acme_client"],
            "expires_at": "2999-01-01T00:00:00Z",
        }
        if role is not None:
            payload["role"] = role
        if fingerprint is not None:
            payload["fingerprint"] = fingerprint
        return json.dumps(payload)

    def write_claim_task(self, task_id: str = "AIPOS-MCP-CLAIM", *, agent_instance: str = "agent-01") -> None:
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
                    "claim_policy: specific_instance_only",
                    "---",
                    "Supervised MCP claim test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

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

    def intake_payload(self) -> dict[str, object]:
        return {
            "actor": "mcp.http.client",
            "source_tag": "wechat_bot",
            "client_tag": "acme_client",
            "external_ref": "wechat:http-124",
            "title": "HTTP MCP intake test",
            "body": "A normalized intake request from HTTP MCP.",
            "submitted_at": "2026-05-26T10:00:00Z",
            "submitter_ref": "contact_hash_http_124",
            "capability_scope": {
                "token_ref": "cap_http_test",
                "operations": ["intake_submit"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
        }

    def data_paths(self) -> list[str]:
        return sorted(path.relative_to(self.repo_root).as_posix() for path in self.repo_root.rglob("*"))

    @contextmanager
    def server(self, *, token: str = "secret", capability_token: str | None = None) -> Iterator[str]:
        # AIPOS-229 (Slice 5): the test bearer/capability is project-scoped (acme_client); set the
        # active project so the now-enforced project gate matches (project-match-PASS path).
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root), "LYBRA_ACTIVE_PROJECT": "acme_client"}
        if capability_token is not None:
            env["LYBRA_CAPABILITY_TOKEN"] = capability_token
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST,
            port=0,
            token=token,
            keepalive_seconds=0.01,
            max_keepalive_events=1,
        )
        with patch.dict(os.environ, env, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(_SERVER_JOIN_TIMEOUT)
                httpd.server_close()

    @contextmanager
    def service_server(self, *, expired_executor: bool = False) -> Iterator[str]:
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}
        registry = {
            "executor-secret": {
                "role": "executor",
                "token_ref": "svc-executor",
                "scopes": ["queue_claim", "queue_return"],
                "expires_at": "2000-01-01T00:00:00Z" if expired_executor else "2999-01-01T00:00:00Z",
            },
            "owner-dispatch-secret": {
                "role": "owner-dispatch",
                "token_ref": "svc-owner-dispatch",
                "scopes": ["audit_dispatch"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
            "auditor-secret": {
                "role": "auditor",
                "token_ref": "svc-auditor",
                "scopes": ["queue_claim", "audit_verdict"],
                "expires_at": "2999-01-01T00:00:00Z",
            },
        }
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST,
            port=0,
            token="",
            keepalive_seconds=0.01,
            max_keepalive_events=1,
            service_role_registry=registry,
        )
        with patch.dict(os.environ, env, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(_SERVER_JOIN_TIMEOUT)
                httpd.server_close()

    def post_rpc(self, base_url: str, payload: dict[str, object], *, token: str | None = "secret") -> dict[str, object]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(
            f"{base_url}{MCP_RPC_PATH}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=_HTTP_CLIENT_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_rpc_full(
        self,
        base_url: str,
        payload: dict[str, object],
        *,
        token: str | None = "secret",
        session_id: str | None = None,
        accept: str = "application/json, text/event-stream",
    ) -> tuple[dict[str, object], dict[str, str]]:
        # AIPOS-201: like post_rpc but exposes response headers (for Mcp-Session-Id)
        # and sends a Streamable-HTTP-style Accept + optional session header.
        headers = {"Content-Type": "application/json", "Accept": accept}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if session_id is not None:
            headers[SESSION_HEADER] = session_id
        req = request.Request(
            f"{base_url}{MCP_RPC_PATH}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=_HTTP_CLIENT_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
            resp_headers = {key: value for key, value in response.getheaders()}
            return body, resp_headers

    def streamable_handshake(self, base_url: str, *, token: str | None = "secret", version: str = "2025-03-26") -> str:
        # AIPOS-201: perform a Streamable-HTTP initialize and return the issued session id.
        result, headers = self.post_rpc_full(
            base_url,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version}},
            token=token,
        )
        session_id = headers.get(SESSION_HEADER)
        assert session_id, "initialize must issue an Mcp-Session-Id"
        return session_id

    def test_default_bind_port_and_keepalive_values(self) -> None:
        self.assertEqual(DEFAULT_HTTP_HOST, "127.0.0.1")
        self.assertEqual(DEFAULT_HTTP_PORT, 7118)
        self.assertEqual(DEFAULT_KEEPALIVE_SECONDS, 30.0)

    def test_startup_requires_bearer_token_env_value(self) -> None:
        stderr = StringIO()
        code = run_http_server(HttpSseConfig(host=DEFAULT_HTTP_HOST, port=0, token=""), error_stream=stderr)
        self.assertEqual(code, 2)
        self.assertIn("LYBRA_MCP_TOKEN", stderr.getvalue())

    def test_missing_and_invalid_bearer_tokens_are_rejected(self) -> None:
        with self.server() as base_url:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            with self.assertRaises(error.HTTPError) as missing:
                self.post_rpc(base_url, payload, token=None)
            self.assertEqual(missing.exception.code, 401)
            missing_body = json.loads(missing.exception.read().decode("utf-8"))
            self.assertEqual(missing_body["error_code"], "MISSING_BEARER_TOKEN")

            with self.assertRaises(error.HTTPError) as invalid:
                self.post_rpc(base_url, payload, token="wrong")
            self.assertEqual(invalid.exception.code, 401)
            invalid_body = json.loads(invalid.exception.read().decode("utf-8"))
            self.assertEqual(invalid_body["error_code"], "INVALID_BEARER_TOKEN")

    def test_read_tool_call_over_http_succeeds(self) -> None:
        with self.server() as base_url:
            response = self.post_rpc(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "lybra_queue_list", "arguments": {}},
                },
            )
        self.assertNotIn("error", response)
        structured = response["result"]["structuredContent"]  # type: ignore[index]
        self.assertEqual(structured["operation"], "get_queue")  # type: ignore[index]

    def test_unhandled_tool_exception_returns_internal_error_without_disconnect_or_paths(self) -> None:
        with self.server() as base_url:
            with patch("tools.mcp_server.http_sse.handle_request", side_effect=FileNotFoundError("/secret/workspace/path")):
                response = self.post_rpc(
                    base_url,
                    {
                        "jsonrpc": "2.0",
                        "id": "internal-error",
                        "method": "tools/call",
                        "params": {"name": "lybra_queue_list", "arguments": {}},
                    },
                )
        raw = json.dumps(response)
        self.assertEqual(response["error"]["code"], -32603)  # type: ignore[index]
        self.assertEqual(response["error"]["message"], "Internal error")  # type: ignore[index]
        self.assertEqual(response["error"]["data"]["error_code"], "INTERNAL_TOOL_ERROR")  # type: ignore[index]
        self.assertEqual(response["error"]["data"]["error_type"], "FileNotFoundError")  # type: ignore[index]
        self.assertNotIn("/secret/workspace/path", raw)

    def test_write_tool_visibility_respects_existing_capability_scope(self) -> None:
        with self.server(capability_token=self.capability_token(operations=[])) as base_url:
            response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            names = [tool["name"] for tool in response["result"]["tools"]]  # type: ignore[index]
            self.assertNotIn("lybra_intake_submit_dry_run", names)
            self.assertNotIn("lybra_queue_claim_dry_run", names)
            self.assertNotIn("lybra_queue_return_dry_run", names)

        with self.server(capability_token=self.capability_token()) as base_url:
            response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            names = [tool["name"] for tool in response["result"]["tools"]]  # type: ignore[index]
            self.assertIn("lybra_intake_submit_dry_run", names)
            self.assertIn("lybra_intake_submit_confirm", names)
            self.assertNotIn("lybra_queue_claim_dry_run", names)
            self.assertNotIn("lybra_queue_return_dry_run", names)

        with self.server(capability_token=self.capability_token(operations=["queue_claim"])) as base_url:
            response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            names = [tool["name"] for tool in response["result"]["tools"]]  # type: ignore[index]
            self.assertIn("lybra_queue_claim_dry_run", names)
            self.assertIn("lybra_queue_claim_confirm", names)
            self.assertNotIn("lybra_intake_submit_dry_run", names)
            self.assertNotIn("lybra_queue_return_dry_run", names)

        with self.server(capability_token=self.capability_token(operations=["queue_return"])) as base_url:
            response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            names = [tool["name"] for tool in response["result"]["tools"]]  # type: ignore[index]
            self.assertIn("lybra_queue_return_dry_run", names)
            self.assertIn("lybra_queue_return_confirm", names)
            self.assertNotIn("lybra_queue_claim_dry_run", names)

    def test_service_mode_single_endpoint_uses_bearer_role_scope_registry(self) -> None:
        with self.service_server() as base_url:
            executor_response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="executor-secret")
            auditor_response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token="auditor-secret")
            dispatch_response = self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, token="owner-dispatch-secret")

        executor_names = [tool["name"] for tool in executor_response["result"]["tools"]]  # type: ignore[index]
        auditor_names = [tool["name"] for tool in auditor_response["result"]["tools"]]  # type: ignore[index]
        dispatch_names = [tool["name"] for tool in dispatch_response["result"]["tools"]]  # type: ignore[index]

        self.assertIn("lybra_queue_claim_dry_run", executor_names)
        self.assertIn("lybra_queue_return_dry_run", executor_names)
        self.assertNotIn("lybra_audit_verdict_dry_run", executor_names)
        self.assertIn("lybra_queue_claim_dry_run", auditor_names)
        self.assertIn("lybra_audit_verdict_dry_run", auditor_names)
        self.assertNotIn("lybra_queue_return_dry_run", auditor_names)
        self.assertIn("lybra_audit_dispatch_dry_run", dispatch_names)
        self.assertNotIn("lybra_queue_claim_dry_run", dispatch_names)

    def test_service_mode_wrong_role_tool_calls_are_denied(self) -> None:
        with self.service_server() as base_url:
            executor_verdict = self.post_rpc(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "lybra_audit_verdict_dry_run",
                        "arguments": {"operations": ["audit_verdict"], "actor": "agent-01"},
                    },
                },
                token="executor-secret",
            )
            auditor_return = self.post_rpc(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "lybra_queue_return_dry_run",
                        "arguments": {"operations": ["queue_return"], "actor": "agent-02"},
                    },
                },
                token="auditor-secret",
            )

        executor_structured = executor_verdict["result"]["structuredContent"]  # type: ignore[index]
        auditor_structured = auditor_return["result"]["structuredContent"]  # type: ignore[index]
        self.assertEqual(executor_structured["error_code"], "SCOPE_DENIED")  # type: ignore[index]
        self.assertEqual(auditor_structured["error_code"], "SCOPE_DENIED")  # type: ignore[index]

    def test_service_mode_scope_basis_is_server_side_and_redacted(self) -> None:
        with self.service_server() as base_url:
            response = self.post_rpc(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "lybra_queue_list",
                        "arguments": {"operations": ["audit_verdict", "queue_return"]},
                    },
                },
                token="executor-secret",
            )
        raw = json.dumps(response)
        structured = response["result"]["structuredContent"]  # type: ignore[index]
        scope_basis = structured["scope_basis"]  # type: ignore[index]
        self.assertEqual(scope_basis["role"], "executor")  # type: ignore[index]
        self.assertEqual(scope_basis["token_ref"], "svc-executor")  # type: ignore[index]
        self.assertEqual(scope_basis["scopes"], ["queue_claim", "queue_return"])  # type: ignore[index]
        self.assertNotIn("executor-secret", raw)

    def test_service_mode_expired_role_token_is_rejected_at_transport_boundary(self) -> None:
        with self.service_server(expired_executor=True) as base_url:
            with self.assertRaises(error.HTTPError) as expired:
                self.post_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="executor-secret")
        self.assertEqual(expired.exception.code, 401)
        body = json.loads(expired.exception.read().decode("utf-8"))
        self.assertEqual(body["error_code"], "EXPIRED_BEARER_TOKEN")

    def test_intake_submit_dry_run_over_http_writes_nothing(self) -> None:
        with self.server(capability_token=self.capability_token()) as base_url:
            before = self.data_paths()
            response = self.post_rpc(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "lybra_intake_submit_dry_run", "arguments": self.intake_payload()},
                },
            )
            after = self.data_paths()
        self.assertEqual(before, after)
        structured = response["result"]["structuredContent"]  # type: ignore[index]
        self.assertEqual(structured["operation"], "intake_submit")  # type: ignore[index]
        self.assertIn("dry_run_token", structured)

    def test_confirm_without_dry_run_token_returns_structured_error(self) -> None:
        with self.server(capability_token=self.capability_token()) as base_url:
            response = self.post_rpc(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "lybra_intake_submit_confirm", "arguments": {}},
                },
            )
        structured = response["result"]["structuredContent"]  # type: ignore[index]
        self.assertEqual(structured["error_code"], "MISSING_DRY_RUN_TOKEN")  # type: ignore[index]

    def test_sse_keepalive_is_stateless_and_writes_no_files(self) -> None:
        with self.server() as base_url:
            before = self.data_paths()
            req = request.Request(f"{base_url}{MCP_SSE_PATH}", headers={"Authorization": "Bearer secret"})
            with request.urlopen(req, timeout=_HTTP_CLIENT_TIMEOUT) as response:
                body = response.read().decode("utf-8")
            after = self.data_paths()
        self.assertEqual(before, after)
        self.assertIn("event: ping", body)
        self.assertIn('"type":"keepalive"', body)

    # --- AIPOS-201: Streamable-HTTP transport (additive; reuses scope/confirmer) ---

    def test_aipos201_initialize_issues_session_and_negotiates_protocol_version(self) -> None:
        with self.server() as base_url:
            result, headers = self.post_rpc_full(
                base_url,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            )
        self.assertNotIn("error", result)
        self.assertEqual(result["result"]["protocolVersion"], "2025-03-26")  # type: ignore[index]
        self.assertTrue(headers.get(SESSION_HEADER), "initialize must issue Mcp-Session-Id")

    def test_aipos201_initialize_defaults_to_legacy_protocol_version(self) -> None:
        # Backward compatibility: a client that omits/requests the legacy version
        # still receives 2024-11-05 (the AIPOS-123 behavior is unchanged).
        with self.server() as base_url:
            no_version, _ = self.post_rpc_full(
                base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
            legacy, _ = self.post_rpc_full(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            )
        self.assertEqual(no_version["result"]["protocolVersion"], "2024-11-05")  # type: ignore[index]
        self.assertEqual(legacy["result"]["protocolVersion"], "2024-11-05")  # type: ignore[index]

    def test_aipos201_get_mcp_serves_sse_keepalive_for_streamable_clients(self) -> None:
        with self.server() as base_url:
            req = request.Request(f"{base_url}{MCP_RPC_PATH}", headers={"Authorization": "Bearer secret", "Accept": "text/event-stream"})
            with request.urlopen(req, timeout=_HTTP_CLIENT_TIMEOUT) as response:
                body = response.read().decode("utf-8")
        self.assertIn("event: ping", body)
        self.assertIn('"type":"keepalive"', body)

    def test_aipos201_delete_mcp_ends_session_returns_200(self) -> None:
        with self.server() as base_url:
            session_id = self.streamable_handshake(base_url)
            req = request.Request(
                f"{base_url}{MCP_RPC_PATH}",
                headers={"Authorization": "Bearer secret", SESSION_HEADER: session_id},
                method="DELETE",
            )
            with request.urlopen(req, timeout=_HTTP_CLIENT_TIMEOUT) as response:
                self.assertEqual(response.status, 200)

    def test_aipos201_unauthenticated_streamable_requests_are_rejected(self) -> None:
        with self.server() as base_url:
            with self.assertRaises(error.HTTPError) as missing:
                self.post_rpc_full(
                    base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token=None
                )
            self.assertEqual(missing.exception.code, 401)
            # GET /mcp and DELETE /mcp also require auth.
            get_req = request.Request(f"{base_url}{MCP_RPC_PATH}", headers={"Accept": "text/event-stream"})
            with self.assertRaises(error.HTTPError) as get_missing:
                request.urlopen(get_req, timeout=_HTTP_CLIENT_TIMEOUT)
            self.assertEqual(get_missing.exception.code, 401)

    def test_aipos201_tools_call_with_session_succeeds(self) -> None:
        with self.server() as base_url:
            session_id = self.streamable_handshake(base_url)
            result, _ = self.post_rpc_full(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lybra_queue_list", "arguments": {}}},
                session_id=session_id,
            )
        self.assertNotIn("error", result)
        self.assertEqual(result["result"]["structuredContent"]["operation"], "get_queue")  # type: ignore[index]

    def test_aipos201_scope_denied_through_streamable_handshake(self) -> None:
        # ★A1 over the new transport: an executor-scope token (queue_claim, NO
        # owner_confirm) that completes a claim dry-run cannot self-confirm.
        self.write_claim_task()
        with self.server(capability_token=self.capability_token(operations=["queue_claim"])) as base_url:
            session_id = self.streamable_handshake(base_url)
            dry, _ = self.post_rpc_full(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lybra_queue_claim_dry_run", "arguments": self.claim_payload()}},
                session_id=session_id,
            )
            dry_token = dry["result"]["structuredContent"]["dry_run_token"]  # type: ignore[index]
            denied, _ = self.post_rpc_full(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "lybra_queue_claim_confirm",
                        "arguments": {
                            "dry_run_token": dry_token,
                            "actor": "agent-01",
                            "agent_instance": "agent-01",
                            "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                            "owner_confirmation_token": "OWNER_CONFIRMED",
                        },
                    },
                },
                session_id=session_id,
            )
        self.assertEqual(denied["result"]["structuredContent"]["error_code"], "SCOPE_DENIED")  # type: ignore[index]

    def test_aipos201_claim_confirm_records_confirmer_through_streamable(self) -> None:
        # AIPOS-199 over the new transport: an owner-scope token (owner_confirm)
        # completing claim dry-run -> confirm through the Streamable-HTTP handshake
        # still stamps the confirmer onto the on-disk claim record.
        self.write_claim_task()
        owner_token = self.capability_token(
            operations=["queue_claim", "owner_confirm"], role="owner", fingerprint="sha256:ownerfp01"
        )
        with self.server(capability_token=owner_token) as base_url:
            session_id = self.streamable_handshake(base_url)
            dry, _ = self.post_rpc_full(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lybra_queue_claim_dry_run", "arguments": self.claim_payload()}},
                session_id=session_id,
            )
            dry_token = dry["result"]["structuredContent"]["dry_run_token"]  # type: ignore[index]
            confirmed, _ = self.post_rpc_full(
                base_url,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "lybra_queue_claim_confirm",
                        "arguments": {
                            "dry_run_token": dry_token,
                            "actor": "agent-01",
                            "agent_instance": "agent-01",
                            "owner_policy_ref": "owner_policy:aipos-166-supervised-test",
                            "owner_confirmation_token": "OWNER_CONFIRMED",
                        },
                    },
                },
                session_id=session_id,
            )
        self.assertTrue(confirmed["result"]["structuredContent"]["ok"], confirmed)  # type: ignore[index]
        records = load_records(self.repo_root)
        rec = records["claims"][0]["metadata"]
        self.assertEqual(rec.get("confirmer_role"), "owner")
        self.assertEqual(rec.get("confirmer_token_ref"), "cap_http_test")
        self.assertEqual(rec.get("confirmer_token_fingerprint"), "sha256:ownerfp01")
        self.assertIn("gate_signature", rec)


if __name__ == "__main__":
    unittest.main()
