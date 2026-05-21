from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.aipos_cli.board_adapter import (
    execute_dry_run,
    get_context_pack_preview,
    get_preview,
    get_queue,
    get_validate,
    submit_external_intake,
)
from tools.aipos_cli.task_loader import find_repo_root


READ_ONLY_NOTICE = "Lybra MCP exposes read tools by default. Write tools are visible only with scoped capability."
CAPABILITY_ENV_VAR = "LYBRA_CAPABILITY_TOKEN"
INTAKE_SCOPE = "intake_submit"
DISCIPLINE_DOC_REF = "AIPOS-109 MCP-native discipline; AIPOS-108 controlled external intake writer"


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


def _teaching_error(
    error_code: str,
    message: str,
    suggested_next_action: str,
    *,
    doc_ref: str = DISCIPLINE_DOC_REF,
) -> dict[str, Any]:
    return _tool_result(
        {
            "ok": False,
            "verdict": "BLOCK",
            "operation": "mcp_write_tool",
            "error_code": error_code,
            "message": message,
            "suggested_next_action": suggested_next_action,
            "doc_ref": doc_ref,
            "errors": [
                {
                    "category": error_code,
                    "message": message,
                    "details": {
                        "suggested_next_action": suggested_next_action,
                        "doc_ref": doc_ref,
                    },
                }
            ],
        },
        is_error=True,
    )


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


def _capability_token() -> dict[str, Any]:
    raw = os.environ.get(CAPABILITY_ENV_VAR, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _capability_has_scope(scope: str) -> bool:
    token = _capability_token()
    operations = token.get("operations")
    if not isinstance(operations, list) or scope not in operations:
        return False
    if not bool(token.get("token_ref") or token.get("token_id")):
        return False
    expires_at_raw = str(token.get("expires_at") or "").strip()
    if not expires_at_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _scope_denied_result() -> dict[str, Any]:
    return _teaching_error(
        "SCOPE_DENIED",
        f"Connection capability does not include {INTAKE_SCOPE}; intake submit tools are not available.",
        "Restart the MCP server with LYBRA_CAPABILITY_TOKEN containing operations: [\"intake_submit\"].",
        doc_ref="AIPOS-109 capability token scope-gated tool visibility",
    )


def _intake_scope_allowed() -> bool:
    return _capability_has_scope(INTAKE_SCOPE)


def _map_controlled_execute_error(response: dict[str, Any]) -> dict[str, Any]:
    blocking = " ".join(str(item) for item in response.get("blocking_reasons", []))
    errors = response.get("errors") if isinstance(response.get("errors"), list) else []
    first_message = ""
    if errors and isinstance(errors[0], dict):
        first_message = str(errors[0].get("message") or "")
    text = f"{blocking} {first_message}".lower()
    if "expired" in text:
        return _teaching_error(
            "TOKEN_EXPIRED",
            "The dry_run_token expired before confirm.",
            "Run lybra_intake_submit_dry_run again and confirm with the new dry_run_token.",
        )
    if "snapshot mismatch" in text:
        return _teaching_error(
            "SNAPSHOT_MISMATCH",
            "The workspace state no longer matches the dry-run snapshot.",
            "Run lybra_intake_submit_dry_run again before confirming.",
        )
    return _teaching_error(
        "CONTROLLED_EXECUTE_REJECTED",
        first_message or blocking or "Controlled execute rejected the confirm request.",
        "Inspect the dry-run response, then run lybra_intake_submit_dry_run again if appropriate.",
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


def lybra_intake_submit_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _intake_scope_allowed():
        return _scope_denied_result()
    args = arguments or {}
    response = submit_external_intake(args, dry_run=True, repo_root=_repo_root(), actor=str(args.get("actor") or "mcp.client"))
    text = " ".join(str(item) for item in response.get("blocking_reasons", []))
    if response.get("verdict") == "BLOCK" and "Invalid source_tag format" in text:
        return _teaching_error(
            "INVALID_SOURCE",
            "source_tag is invalid for external intake.",
            "Use a lowercase registered source_tag from external_intake_registry.md, then call lybra_intake_submit_dry_run again.",
            doc_ref="AIPOS-106 External Intake Registry Protocol; AIPOS-107 source_tag field",
        )
    return _tool_result(response, is_error=not bool(response.get("ok", False)) or response.get("verdict") == "BLOCK")


def lybra_intake_submit_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _intake_scope_allowed():
        return _scope_denied_result()
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _teaching_error(
            "MISSING_DRY_RUN_TOKEN",
            "lybra_intake_submit_confirm requires dry_run_token from a prior dry-run response.",
            "Call lybra_intake_submit_dry_run first, then pass its dry_run_token to lybra_intake_submit_confirm.",
        )
    response = execute_dry_run(
        dry_run_token,
        str(args.get("actor") or "mcp.client"),
        owner_confirmation_token=str(args.get("owner_confirmation_token") or "") or None,
        repo_root=_repo_root(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response)
    return _tool_result(response, is_error=False)


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any] | None], dict[str, Any]]] = {
    "lybra_queue_list": lybra_queue_list,
    "lybra_task_preview": lybra_task_preview,
    "lybra_validate": lybra_validate,
    "lybra_context_pack_build": lybra_context_pack_build,
    "lybra_intake_submit_dry_run": lybra_intake_submit_dry_run,
    "lybra_intake_submit_confirm": lybra_intake_submit_confirm,
}


READ_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
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


WRITE_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "lybra_intake_submit_dry_run",
        "description": (
            "When to use: create a controlled execute preview for normalized external intake that should become an Owner-reviewed draft. "
            "Prerequisites: this MCP connection must have a capability_token with intake_submit scope; source_tag must match an approved external intake source from external_intake_registry.md; client_tag must map to an existing project; this tool does not publish or execute work. "
            "Return structure: a controlled execute envelope with verdict, planned_writes, dry_run_token, dry_run_snapshot_hash, dry_run_created_at, dry_run_expires_at, and rendered draft content. "
            "Next-step hint: pass dry_run_token to lybra_intake_submit_confirm; the resulting draft waits for Owner publish and no agent takes automatic follow-up action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "source_tag": {"type": "string"},
                "client_tag": {"type": "string"},
                "external_ref": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "submitted_at": {"type": "string"},
                "submitter_ref": {"type": "string"},
                "capability_scope": {"type": "object"},
                "priority_hint": {"type": "string"},
                "requested_due_date": {"type": "string"},
                "source_thread_ref": {"type": "string"},
                "owner_approval_evidence": {"type": "string"},
            },
            "required": [
                "source_tag",
                "client_tag",
                "external_ref",
                "title",
                "body",
                "submitted_at",
                "submitter_ref",
                "capability_scope",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_intake_submit_confirm",
        "description": (
            "When to use: confirm a prior lybra_intake_submit_dry_run after reviewing its planned_writes and rendered draft. "
            "Prerequisites: this MCP connection must have a capability_token with intake_submit scope; dry_run_token is required and must come from the immediately preceding dry-run flow; the dry-run token must be unexpired and its snapshot must still match. "
            "Return structure: a controlled execute result with performed_writes when the external intake draft is written, or a structured teaching error with error_code, message, suggested_next_action, and doc_ref. "
            "Next-step hint: confirm only writes a draft under 5_tasks/drafts/external_intake; the draft waits for Owner publish and no agent takes automatic follow-up action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token"],
            "additionalProperties": False,
        },
    },
]


def visible_tool_descriptors() -> list[dict[str, Any]]:
    descriptors = list(READ_TOOL_DESCRIPTORS)
    if _intake_scope_allowed():
        descriptors.extend(WRITE_TOOL_DESCRIPTORS)
    return descriptors


TOOL_DESCRIPTORS = READ_TOOL_DESCRIPTORS
