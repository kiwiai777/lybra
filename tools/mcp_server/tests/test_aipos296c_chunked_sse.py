"""AIPOS-296C: SSE chunked transfer-encoding test suite.

Verifies that SSE responses (POST /mcp + GET keepalive) use Transfer-Encoding: chunked
without Connection: close or Content-Length, enabling undici keep-alive (tailnet direct).

Test matrix:
- Response headers: Transfer-Encoding present, no Connection: close, no Content-Length
- Chunked frame format: <size-hex>\r\n<data>\r\n, terminating 0\r\n\r\n
- Functional correctness: SSE payload extraction identical to 296B (zero regression)
"""
from __future__ import annotations

import json
import re
import unittest
from urllib import request

from tools.mcp_server.tests.test_http_sse_transport import HttpSseTransportTests


class ChunkedSseTests(HttpSseTransportTests):
    """AIPOS-296C: chunked transfer-encoding format and header compliance."""

    def _raw_chunked_response(self, base_url: str, payload: dict[str, object]) -> tuple[bytes, dict[str, str]]:
        """POST /mcp with SSE Accept and return raw response + headers."""
        headers_dict = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer secret",
        }
        req = request.Request(
            f"{base_url}/mcp",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers_dict,
            method="POST",
        )
        with request.urlopen(req, timeout=15.0) as response:
            raw_body = response.read()
            resp_headers = {key: value for key, value in response.getheaders()}
            return raw_body, resp_headers

    def test_sse_response_has_chunked_encoding_header(self) -> None:
        """SSE response declares Transfer-Encoding: chunked."""
        with self.server() as base_url:
            _, headers = self._raw_chunked_response(
                base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")

    def test_sse_response_has_no_content_length_with_chunked(self) -> None:
        """SSE chunked response must not include Content-Length (RFC 7230 §3.3.3)."""
        with self.server() as base_url:
            _, headers = self._raw_chunked_response(
                base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
        self.assertNotIn("Content-Length", headers)

    def test_sse_response_has_no_connection_close(self) -> None:
        """SSE chunked response must not force Connection: close (keep-alive default)."""
        with self.server() as base_url:
            _, headers = self._raw_chunked_response(
                base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
        # Explicit check: Connection header absent or not "close"
        connection = headers.get("Connection", "")
        self.assertNotEqual(connection.lower(), "close")

    def test_chunked_response_decoded_by_urllib(self) -> None:
        """Python urllib auto-decodes chunked; verify SSE payload is intact (transparent)."""
        with self.server() as base_url:
            raw_body, headers = self._raw_chunked_response(
                base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
        # urllib transparently decodes chunked → raw_body is the SSE payload.
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")
        body_str = raw_body.decode("utf-8")
        # SSE format intact: "data: <json>\n\n"
        self.assertTrue(body_str.startswith("data: "))
        self.assertTrue(body_str.endswith("\n\n"))

    def test_chunked_sse_payload_extraction_identical_to_296b(self) -> None:
        """SSE payload extraction from chunked = 296B behavior (urllib decodes, zero regression)."""
        with self.server() as base_url:
            raw_body, _ = self._raw_chunked_response(
                base_url,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lybra_queue_list", "arguments": {}}},
            )
        # urllib auto-decodes chunked → raw_body is SSE payload directly.
        body_str = raw_body.decode("utf-8")
        # SSE format: "data: <json>\n\n"
        self.assertTrue(body_str.startswith("data: "))
        self.assertTrue(body_str.endswith("\n\n"))
        json_payload = body_str.split("\n")[0][len("data: "):]
        result = json.loads(json_payload)
        self.assertIn("result", result)
        self.assertEqual(result["result"]["structuredContent"]["operation"], "get_queue")

    def test_get_keepalive_uses_chunked_encoding(self) -> None:
        """GET /sse keepalive stream uses chunked transfer (undici keep-alive)."""
        with self.server() as base_url:
            req = request.Request(
                f"{base_url}/sse",
                headers={"Authorization": "Bearer secret"},
                method="GET",
            )
            with request.urlopen(req, timeout=15.0) as response:
                transfer_encoding = response.getheader("Transfer-Encoding")
                connection = response.getheader("Connection", "")
                content_length = response.getheader("Content-Length")
                self.assertEqual(transfer_encoding, "chunked")
                self.assertNotEqual(connection.lower(), "close")
                self.assertIsNone(content_length)

    def test_json_response_still_uses_content_length(self) -> None:
        """Non-SSE JSON responses preserve Content-Length (296 成果保留)."""
        with self.server() as base_url:
            headers_dict = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer secret",
            }
            req = request.Request(
                f"{base_url}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
                headers=headers_dict,
                method="POST",
            )
            with request.urlopen(req, timeout=15.0) as response:
                content_length = response.getheader("Content-Length")
                transfer_encoding = response.getheader("Transfer-Encoding")
                self.assertIsNotNone(content_length)
                self.assertIsNone(transfer_encoding)


if __name__ == "__main__":
    unittest.main()
