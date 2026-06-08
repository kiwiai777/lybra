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

from tools.mcp_server.http_sse import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    DEFAULT_KEEPALIVE_SECONDS,
    HttpSseConfig,
    MCP_RPC_PATH,
    MCP_SSE_PATH,
    build_http_server,
    run_http_server,
)


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

    def capability_token(self, operations: list[str] | None = None) -> str:
        return json.dumps(
            {
                "token_ref": "cap_http_test",
                "operations": operations if operations is not None else ["intake_submit"],
                "projects": ["acme_client"],
                "expires_at": "2999-01-01T00:00:00Z",
            }
        )

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
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}
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
                thread.join(timeout=2)
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
                thread.join(timeout=2)
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
        with request.urlopen(req, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

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
            with request.urlopen(req, timeout=3) as response:
                body = response.read().decode("utf-8")
            after = self.data_paths()
        self.assertEqual(before, after)
        self.assertIn("event: ping", body)
        self.assertIn('"type":"keepalive"', body)


if __name__ == "__main__":
    unittest.main()
