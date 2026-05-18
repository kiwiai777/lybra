from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from tools.aipos_cli.board_adapter import get_context_pack_preview, get_preview, get_queue, get_validate
from tools.aipos_cli.task_loader import find_repo_root


READ_ONLY_NOTICE = "Lybra MCP MVP is read-only. It exposes no write tools and performs no durable writes."


def _repo_root() -> Path:
    return find_repo_root()


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": _json_text(payload)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _error_result(message: str, *, category: str = "VALIDATION_ERROR") -> dict[str, Any]:
    return _tool_result(
        {
            "ok": False,
            "verdict": "BLOCK",
            "operation": "mcp_tool_call",
            "safety_notice": READ_ONLY_NOTICE,
            "errors": [{"category": category, "message": message, "details": {}}],
        },
        is_error=True,
    )


def lybra_queue_list(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = arguments or {}
    return _tool_result(get_queue(repo_root=_repo_root()))


def lybra_validate(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = arguments or {}
    return _tool_result(get_validate(repo_root=_repo_root()))


def lybra_task_preview(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    task_id = args.get("task_id")
    path = args.get("path")
    if bool(str(task_id or "").strip()) == bool(str(path or "").strip()):
        return _error_result("Exactly one of task_id or path is required")
    response = get_preview(
        task_id=str(task_id).strip() if task_id else None,
        path=str(path).strip() if path else None,
        actor=str(args.get("actor")).strip() if args.get("actor") else None,
        repo_root=_repo_root(),
    )
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_context_pack_build(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    task_id = str(args.get("task_id") or "").strip()
    path = str(args.get("path") or "").strip()
    orchestration_id = str(args.get("orchestration_id") or "").strip()
    if sum(bool(value) for value in (task_id, path, orchestration_id)) != 1:
        return _error_result("Exactly one of task_id, path, or orchestration_id is required")
    response = get_context_pack_preview(
        task_id=task_id or None,
        path=path or None,
        orchestration_id=orchestration_id or None,
        repo_root=_repo_root(),
    )
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any] | None], dict[str, Any]]] = {
    "lybra_queue_list": lybra_queue_list,
    "lybra_task_preview": lybra_task_preview,
    "lybra_validate": lybra_validate,
    "lybra_context_pack_build": lybra_context_pack_build,
}


TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "lybra_queue_list",
        "description": "List Lybra task queue state using existing read-only backend semantics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_task_preview",
        "description": "Build a read-only task session preview for one task by task_id or path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "path": {"type": "string"},
                "actor": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_validate",
        "description": "Run Lybra validation using existing read-only backend semantics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_context_pack_build",
        "description": "Build a read-only Context Pack preview by task_id, path, or orchestration_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "path": {"type": "string"},
                "orchestration_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]

