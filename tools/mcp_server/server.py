from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from .tools import READ_ONLY_NOTICE, TOOL_HANDLERS, visible_tool_descriptors

JSONRPC_VERSION = "2.0"
SERVER_NAME = "lybra-mcp"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"
# AIPOS-201: protocol versions the gate can speak. The default stays 2024-11-05
# (stdio + legacy HTTP clients are unchanged); a Streamable-HTTP client that
# negotiates a newer version (e.g. codex) gets its requested version echoed back.
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _initialize_result(requested_version: str | None = None) -> dict[str, Any]:
    # AIPOS-201: echo the client's requested protocolVersion when supported,
    # otherwise fall back to the historical default. Backward compatible: a
    # client that omits or requests 2024-11-05 still receives 2024-11-05.
    protocol_version = (
        requested_version
        if requested_version in SUPPORTED_PROTOCOL_VERSIONS
        else PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol_version,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {"tools": {}},
        "instructions": READ_ONLY_NOTICE,
    }


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name") or "").strip()
    if not name:
        raise JsonRpcError(-32602, "tools/call requires a tool name")
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise JsonRpcError(-32601, f"Unknown tool: {name}")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "tools/call arguments must be an object")
    return handler(arguments)


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcError(-32600, "Invalid JSON-RPC version")

    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") or {}
    if params is not None and not isinstance(params, dict):
        raise JsonRpcError(-32602, "params must be an object")

    # Notifications do not receive responses.
    if request_id is None and method in {"notifications/initialized", "notifications/cancelled"}:
        return None

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        return _success(
            request_id,
            _initialize_result(requested_version if isinstance(requested_version, str) else None),
        )
    if method == "tools/list":
        return _success(request_id, {"tools": visible_tool_descriptors()})
    if method == "tools/call":
        return _success(request_id, _handle_tools_call(params))
    if method == "ping":
        return _success(request_id, {})

    raise JsonRpcError(-32601, f"Method not found: {method}")


def serve(input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout, error_stream: TextIO = sys.stderr) -> int:
    for line in input_stream:
        raw = line.strip()
        if not raw:
            continue
        request_id: Any = None
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise JsonRpcError(-32600, "JSON-RPC message must be an object")
            request_id = message.get("id")
            response = handle_request(message)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, "Parse error", {"detail": str(exc)})
        except JsonRpcError as exc:
            response = _error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive stdio boundary
            print(f"{SERVER_NAME}: internal error: {exc}", file=error_stream)
            response = _error(request_id, -32603, "Internal error")

        if response is not None:
            output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    from .http_sse import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, DEFAULT_KEEPALIVE_SECONDS

    parser = argparse.ArgumentParser(description="Lybra MCP server")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the read-only MCP server over stdio")
    http_parser = subparsers.add_parser("serve-http", help="Run the MCP server over loopback HTTP/SSE")
    http_parser.add_argument("--host", default=DEFAULT_HTTP_HOST, help="Bind host; defaults to 127.0.0.1")
    http_parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help="Bind port; defaults to 7118")
    http_parser.add_argument(
        "--keepalive-seconds",
        type=float,
        default=DEFAULT_KEEPALIVE_SECONDS,
        help="SSE ping interval; defaults to 30 seconds",
    )
    http_parser.add_argument(
        "--service-connection-json",
        help="Service mode v0 connection config; enables server-side opaque role-token scope resolution",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from .http_sse import config_from_env, run_http_server, service_config_from_connection

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve()
    if args.command == "serve-http":
        if getattr(args, "service_connection_json", None):
            return run_http_server(
                service_config_from_connection(
                    str(args.host),
                    int(args.port),
                    float(args.keepalive_seconds),
                    str(args.service_connection_json),
                )
            )
        return run_http_server(config_from_env(str(args.host), int(args.port), float(args.keepalive_seconds)))
    parser.print_help()
    return 2
