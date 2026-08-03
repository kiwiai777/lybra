"""AIPOS-296B: POST /mcp content negotiation (JSON ↔ SSE) test matrix.

Accept header drives response format:
- Contains text/event-stream → SSE single-event response
- application/json only (or absent) → JSON response (zero regression)

Matrix: Accept {json-only, sse-capable} × {initialize, tool call, notification}
"""
from __future__ import annotations

import json
import unittest
from urllib import request

from tools.mcp_server.tests.test_http_sse_transport import HttpSseTransportTests


class ContentNegotiationTests(HttpSseTransportTests):
    """AIPOS-296B: Content negotiation matrix (Accept → JSON/SSE, zero regression)."""

    def post_rpc_raw(
        self,
        base_url: str,
        payload: dict[str, object],
        *,
        token: str | None = "secret",
        accept: str = "application/json",
    ) -> tuple[bytes, str, int]:
        """Raw POST that returns (body_bytes, Content-Type, status_code)."""
        headers = {"Content-Type": "application/json", "Accept": accept}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(
            f"{base_url}/mcp",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=15.0) as response:
            body = response.read()
            content_type = response.getheader("Content-Type") or ""
            return body, content_type, response.status

    def test_json_accept_initialize_returns_json(self) -> None:
        """Accept: application/json → initialize returns application/json."""
        with self.server() as base_url:
            body, ct, status = self.post_rpc_raw(
                base_url,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                accept="application/json",
            )
        self.assertEqual(status, 200)
        self.assertIn("application/json", ct)
        result = json.loads(body.decode("utf-8"))
        self.assertIn("result", result)
        self.assertEqual(result["result"]["protocolVersion"], "2025-03-26")

    def test_sse_accept_initialize_returns_sse(self) -> None:
        """Accept: text/event-stream → initialize returns SSE single-event."""
        with self.server() as base_url:
            body, ct, status = self.post_rpc_raw(
                base_url,
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                accept="application/json, text/event-stream",
            )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", ct)
        body_str = body.decode("utf-8")
        # SSE format: "data: <JSON>\n\n"
        self.assertTrue(body_str.startswith("data: "))
        self.assertTrue(body_str.endswith("\n\n"))
        # Extract JSON payload from SSE envelope
        json_line = body_str.split("\n")[0]  # "data: {...}"
        json_payload = json_line[len("data: "):]
        result = json.loads(json_payload)
        self.assertIn("result", result)
        self.assertEqual(result["result"]["protocolVersion"], "2025-03-26")

    def test_json_accept_tool_call_returns_json(self) -> None:
        """Accept: application/json → tools/call returns application/json (codex/pi zero regression)."""
        with self.server() as base_url:
            body, ct, status = self.post_rpc_raw(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lybra_queue_list", "arguments": {}}},
                accept="application/json",
            )
        self.assertEqual(status, 200)
        self.assertIn("application/json", ct)
        result = json.loads(body.decode("utf-8"))
        self.assertIn("result", result)
        self.assertEqual(result["result"]["structuredContent"]["operation"], "get_queue")

    def test_sse_accept_tool_call_returns_sse(self) -> None:
        """Accept: text/event-stream → tools/call returns SSE single-event."""
        with self.server() as base_url:
            body, ct, status = self.post_rpc_raw(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lybra_queue_list", "arguments": {}}},
                accept="application/json, text/event-stream",
            )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", ct)
        body_str = body.decode("utf-8")
        self.assertTrue(body_str.startswith("data: "))
        self.assertTrue(body_str.endswith("\n\n"))
        json_payload = body_str.split("\n")[0][len("data: "):]
        result = json.loads(json_payload)
        self.assertIn("result", result)
        self.assertEqual(result["result"]["structuredContent"]["operation"], "get_queue")

    def test_json_accept_notification_returns_accepted_json(self) -> None:
        """Accept: application/json → notification returns 202 + JSON (zero regression)."""
        with self.server() as base_url:
            body, ct, status = self.post_rpc_raw(
                base_url,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                accept="application/json",
            )
        self.assertEqual(status, 202)
        self.assertIn("application/json", ct)
        result = json.loads(body.decode("utf-8"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["notification"])

    def test_sse_accept_notification_returns_empty_sse(self) -> None:
        """Accept: text/event-stream → notification returns 202 + empty SSE stream."""
        with self.server() as base_url:
            body, ct, status = self.post_rpc_raw(
                base_url,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                accept="application/json, text/event-stream",
            )
        self.assertEqual(status, 202)
        self.assertIn("text/event-stream", ct)
        # Notification SSE: empty stream (no data events)
        body_str = body.decode("utf-8")
        self.assertEqual(body_str, "")

    def test_sse_response_uses_chunked_transfer_encoding(self) -> None:
        """AIPOS-296C: SSE response uses Transfer-Encoding: chunked (undici keep-alive)."""
        with self.server() as base_url:
            headers_obj = {"Content-Type": "application/json", "Accept": "text/event-stream", "Authorization": "Bearer secret"}
            req = request.Request(
                f"{base_url}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
                headers=headers_obj,
                method="POST",
            )
            with request.urlopen(req, timeout=15.0) as response:
                transfer_encoding = response.getheader("Transfer-Encoding")
                connection_header = response.getheader("Connection")
                content_length = response.getheader("Content-Length")
                self.assertEqual(transfer_encoding, "chunked")
                # No Connection: close (default keep-alive)
                self.assertNotEqual(connection_header, "close")
                # No Content-Length with chunked
                self.assertIsNone(content_length)

    def test_mcp_session_id_issued_in_both_paths(self) -> None:
        """Mcp-Session-Id issued on initialize in both JSON and SSE paths."""
        with self.server() as base_url:
            # JSON path
            json_req = request.Request(
                f"{base_url}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json", "Authorization": "Bearer secret"},
                method="POST",
            )
            with request.urlopen(json_req, timeout=15.0) as response:
                json_session = response.getheader("Mcp-Session-Id")
                self.assertTrue(json_session)

            # SSE path
            sse_req = request.Request(
                f"{base_url}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream", "Authorization": "Bearer secret"},
                method="POST",
            )
            with request.urlopen(sse_req, timeout=15.0) as response:
                sse_session = response.getheader("Mcp-Session-Id")
                self.assertTrue(sse_session)


if __name__ == "__main__":
    unittest.main()
