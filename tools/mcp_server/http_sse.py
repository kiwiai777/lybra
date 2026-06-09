from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from .server import JsonRpcError, _error, handle_request
from .tools import request_capability_scope

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 7118
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
    service_role_registry: dict[str, dict[str, Any]] | None = None


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


def _extract_bearer(header_value: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not header_value:
        return None, _structured_error(
            "MISSING_BEARER_TOKEN",
            "HTTP/SSE requests require Authorization: Bearer <token>.",
            "Retry with Authorization: Bearer <local service role token or LYBRA_MCP_TOKEN>.",
        )
    prefix = "Bearer "
    if not header_value.startswith(prefix):
        return None, _structured_error(
            "INVALID_AUTH_SCHEME",
            "Authorization header must use the Bearer scheme.",
            "Retry with Authorization: Bearer <token>.",
        )
    return header_value[len(prefix) :].strip(), None


def _authorization_error(header_value: str | None, expected_token: str) -> dict[str, Any] | None:
    if not expected_token:
        return _structured_error(
            "SERVER_TOKEN_NOT_CONFIGURED",
            f"{TOKEN_ENV_VAR} is required before starting the HTTP/SSE transport.",
            f"Set {TOKEN_ENV_VAR} and restart the MCP HTTP/SSE server.",
        )
    supplied, error = _extract_bearer(header_value)
    if error is not None:
        return error
    if supplied != expected_token:
        return _structured_error(
            "INVALID_BEARER_TOKEN",
            "Bearer token did not match the configured MCP HTTP/SSE token.",
            "Check the client token and reconnect.",
        )
    return None


def _service_role_capability(header_value: str | None, registry: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    supplied, error = _extract_bearer(header_value)
    if error is not None:
        return None, error
    entry = registry.get(str(supplied or ""))
    if not isinstance(entry, dict):
        return None, _structured_error(
            "INVALID_BEARER_TOKEN",
            "Bearer token did not match a configured local service role token.",
            "Use a token from .lybra/local/connection.json for the intended local role, or run `lybra serve status` to inspect redacted refs.",
            doc_ref="AIPOS-189 Service Mode v0 Protocol",
        )
    expires_at = str(entry.get("expires_at") or "2999-01-01T00:00:00Z")
    try:
        parsed_expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed_expires.tzinfo is None:
            parsed_expires = parsed_expires.replace(tzinfo=timezone.utc)
    except ValueError:
        return None, _structured_error(
            "INVALID_SERVICE_ROLE_TOKEN_EXPIRY",
            "Configured local service role token has an invalid expires_at value.",
            "Run `lybra serve rotate` to mint a fresh local role token registry.",
            doc_ref="AIPOS-189 Service Mode v0 Protocol",
        )
    if parsed_expires <= datetime.now(timezone.utc):
        return None, _structured_error(
            "EXPIRED_BEARER_TOKEN",
            "Local service role token is expired.",
            "Run `lybra serve rotate` to mint fresh local role tokens and reconnect.",
            doc_ref="AIPOS-189 Service Mode v0 Protocol",
        )
    scopes = entry.get("scopes") if isinstance(entry.get("scopes"), list) else []
    return {
        "token_ref": str(entry.get("token_ref") or ""),
        "role": str(entry.get("role") or ""),
        "operations": [str(item) for item in scopes],
        "expires_at": expires_at,
        "source": "service_v0",
    }, None


def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _rpc_response(message: dict[str, Any], *, capability: dict[str, Any] | None = None) -> dict[str, Any] | None:
    request_id: Any = None
    try:
        request_id = message.get("id")
        with request_capability_scope(capability):
            return handle_request(message)
    except JsonRpcError as exc:
        return _error(request_id, exc.code, exc.message, exc.data)
    except Exception as exc:
        return _error(
            request_id,
            -32603,
            "Internal error",
            {"error_code": "INTERNAL_TOOL_ERROR", "error_type": exc.__class__.__name__},
        )


class LybraMcpHttpSseHandler(BaseHTTPRequestHandler):
    server_version = "LybraMcpHttpSse/0.2.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stdout/stderr quiet by default and avoid logging bearer tokens.
        return

    @property
    def config(self) -> HttpSseConfig:
        return self.server.lybra_config  # type: ignore[attr-defined]

    def _authorize(self) -> bool:
        error = self._authorization_error()
        if error is None:
            return True
        _json_response(self, HTTPStatus.UNAUTHORIZED, error)
        return False

    def _authorization_error(self) -> dict[str, Any] | None:
        if self.config.service_role_registry is not None:
            _capability, error = _service_role_capability(self.headers.get("Authorization"), self.config.service_role_registry)
            return error
        return _authorization_error(self.headers.get("Authorization"), self.config.token)

    def _request_capability(self) -> dict[str, Any] | None:
        if self.config.service_role_registry is None:
            return None
        capability, _error_payload = _service_role_capability(self.headers.get("Authorization"), self.config.service_role_registry)
        return capability

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
        response = _rpc_response(message, capability=self._request_capability())
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
    if not config.token and config.service_role_registry is None:
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


def load_service_role_registry(connection_json: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(connection_json).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Service connection config must be a JSON object: {path}")
    registry: dict[str, dict[str, Any]] = {}
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError(f"Service connection config tokens must be a list: {path}")
    for item in tokens:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "").strip()
        if not token:
            continue
        registry[token] = {
            "role": str(item.get("role") or ""),
            "token_ref": str(item.get("token_ref") or ""),
            "scopes": [str(scope) for scope in item.get("scopes", []) if str(scope).strip()] if isinstance(item.get("scopes"), list) else [],
            "expires_at": str(item.get("expires_at") or "2999-01-01T00:00:00Z"),
        }
    if not registry:
        raise ValueError(f"Service connection config contains no usable role tokens: {path}")
    return registry


def service_config_from_connection(host: str, port: int, keepalive_seconds: float, connection_json: str | Path) -> HttpSseConfig:
    return HttpSseConfig(
        host=host,
        port=port,
        token="",
        keepalive_seconds=keepalive_seconds,
        service_role_registry=load_service_role_registry(connection_json),
    )
