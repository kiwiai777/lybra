from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TextIO
from urllib.parse import urlparse

from .server import JsonRpcError, _error, handle_request

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8766
DEFAULT_KEEPALIVE_SECONDS = 30.0
TOKEN_ENV_VAR = "LYBRA_MCP_TOKEN"
MCP_RPC_PATH = "/mcp"
MCP_SSE_PATH = "/sse"


@dataclass(frozen=True)
class HttpSseConfig:
    host: str = DEFAULT_HTTP_HOST
    port: int = DEFAULT_HTTP_PORT
    token: str = ""
    keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS
    max_keepalive_events: int | None = None


def _structured_error(
    error_code: str,
    message: str,
    suggested_next_action: str,
    *,
    doc_ref: str = "AIPOS-123 MCP HTTP/SSE Transport Protocol",
) -> dict[str, Any]:
    return {
        "ok": False,
        "verdict": "BLOCK",
        "error_code": error_code,
        "message": message,
        "suggested_next_action": suggested_next_action,
        "doc_ref": doc_ref,
    }


def _authorization_error(header_value: str | None, expected_token: str) -> dict[str, Any] | None:
    if not expected_token:
        return _structured_error(
            "SERVER_TOKEN_NOT_CONFIGURED",
            f"{TOKEN_ENV_VAR} is required before starting the HTTP/SSE transport.",
            f"Set {TOKEN_ENV_VAR} and restart the MCP HTTP/SSE server.",
        )
    if not header_value:
        return _structured_error(
            "MISSING_BEARER_TOKEN",
            "HTTP/SSE requests require Authorization: Bearer <token>.",
            f"Retry with Authorization: Bearer <value from {TOKEN_ENV_VAR}>.",
        )
    prefix = "Bearer "
    if not header_value.startswith(prefix):
        return _structured_error(
            "INVALID_AUTH_SCHEME",
            "Authorization header must use the Bearer scheme.",
            "Retry with Authorization: Bearer <token>.",
        )
    supplied = header_value[len(prefix) :].strip()
    if supplied != expected_token:
        return _structured_error(
            "INVALID_BEARER_TOKEN",
            "Bearer token did not match the configured MCP HTTP/SSE token.",
            "Check the client token and reconnect.",
        )
    return None


def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _rpc_response(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id: Any = None
    try:
        request_id = message.get("id")
        return handle_request(message)
    except JsonRpcError as exc:
        return _error(request_id, exc.code, exc.message, exc.data)


class LybraMcpHttpSseHandler(BaseHTTPRequestHandler):
    server_version = "LybraMcpHttpSse/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stdout/stderr quiet by default and avoid logging bearer tokens.
        return

    @property
    def config(self) -> HttpSseConfig:
        return self.server.lybra_config  # type: ignore[attr-defined]

    def _authorize(self) -> bool:
        error = _authorization_error(self.headers.get("Authorization"), self.config.token)
        if error is None:
            return True
        _json_response(self, HTTPStatus.UNAUTHORIZED, error)
        return False

    def do_POST(self) -> None:
        if urlparse(self.path).path != MCP_RPC_PATH:
            _json_response(
                self,
                HTTPStatus.NOT_FOUND,
                _structured_error("NOT_FOUND", "Unknown MCP HTTP/SSE endpoint.", f"POST JSON-RPC requests to {MCP_RPC_PATH}."),
            )
            return
        if not self._authorize():
            return
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            length = 0
        if length <= 0:
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                _structured_error("EMPTY_REQUEST_BODY", "JSON-RPC request body is required.", f"POST a JSON-RPC object to {MCP_RPC_PATH}."),
            )
            return
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, _error(None, -32700, "Parse error", {"detail": str(exc)}))
            return
        if not isinstance(message, dict):
            _json_response(self, HTTPStatus.BAD_REQUEST, _error(None, -32600, "JSON-RPC message must be an object"))
            return
        response = _rpc_response(message)
        if response is None:
            _json_response(self, HTTPStatus.ACCEPTED, {"ok": True, "notification": True})
            return
        _json_response(self, HTTPStatus.OK, response)

    def do_GET(self) -> None:
        if urlparse(self.path).path != MCP_SSE_PATH:
            _json_response(
                self,
                HTTPStatus.NOT_FOUND,
                _structured_error("NOT_FOUND", "Unknown MCP HTTP/SSE endpoint.", f"Open {MCP_SSE_PATH} for keepalive events."),
            )
            return
        if not self._authorize():
            return
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close" if self.config.max_keepalive_events is not None else "keep-alive")
        self.end_headers()
        count = 0
        while self.config.max_keepalive_events is None or count < self.config.max_keepalive_events:
            payload = json.dumps({"type": "keepalive", "transport": "http_sse"}, separators=(",", ":"))
            try:
                self.wfile.write(f"event: ping\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            count += 1
            if self.config.max_keepalive_events is not None and count >= self.config.max_keepalive_events:
                break
            time.sleep(max(self.config.keepalive_seconds, 0.001))
        if self.config.max_keepalive_events is not None:
            self.close_connection = True


class LybraMcpHttpSseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: HttpSseConfig) -> None:
        self.lybra_config = config
        super().__init__(server_address, LybraMcpHttpSseHandler)


def build_http_server(config: HttpSseConfig) -> LybraMcpHttpSseServer:
    return LybraMcpHttpSseServer((config.host, config.port), config)


def run_http_server(config: HttpSseConfig, *, error_stream: TextIO = sys.stderr) -> int:
    if not config.token:
        print(f"{TOKEN_ENV_VAR} is required for MCP HTTP/SSE transport.", file=error_stream)
        return 2
    with build_http_server(config) as httpd:
        print(f"Lybra MCP HTTP/SSE listening on http://{config.host}:{config.port}", file=error_stream)
        httpd.serve_forever()
    return 0


def config_from_env(host: str, port: int, keepalive_seconds: float) -> HttpSseConfig:
    return HttpSseConfig(
        host=host,
        port=port,
        token=os.environ.get(TOKEN_ENV_VAR, "").strip(),
        keepalive_seconds=keepalive_seconds,
    )
