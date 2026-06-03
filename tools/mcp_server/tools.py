from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.aipos_cli.board_adapter import (
    claim_task,
    execute_dry_run,
    get_context_pack_preview,
    get_preview,
    get_queue,
    get_validate,
    record_owner_decision,
    return_task,
    submit_external_intake,
)
from tools.aipos_cli.agent_profiles import load_agent_profiles, resolve_instance_id
from tools.aipos_cli.controlled_execute import get_dry_run
from tools.aipos_cli.task_loader import find_repo_root


READ_ONLY_NOTICE = "Lybra MCP exposes read tools by default. Write tools are visible only with scoped capability."
CAPABILITY_ENV_VAR = "LYBRA_CAPABILITY_TOKEN"
INTAKE_SCOPE = "intake_submit"
OWNER_DECISION_SCOPE = "owner_decision_record"
QUEUE_CLAIM_SCOPE = "queue_claim"
QUEUE_RETURN_SCOPE = "queue_return"
DISCIPLINE_DOC_REF = "AIPOS-109 MCP-native discipline"
SUPERVISED_CLAIM_DOC_REF = "AIPOS-165 Supervised MCP Explicit Claim Protocol"
OWNER_CONFIRMATION_TOKEN = "OWNER_CONFIRMED"
FORBIDDEN_QUEUE_CLAIM_FIELDS = {
    "api_key",
    "auto_pick",
    "auto_select",
    "background_worker",
    "batch",
    "bearer_token",
    "credential",
    "credentials",
    "delegated_policy",
    "llm_raw_prompt",
    "llm_raw_response",
    "policy_budget",
    "raw_prompt",
    "raw_response",
    "standing_policy",
    "token",
}
FORBIDDEN_QUEUE_RETURN_FIELDS = {
    *FORBIDDEN_QUEUE_CLAIM_FIELDS,
    "audit_dispatch",
    "audit_pass",
    "audit_verdict",
    "finalize",
    "finalize_approval",
    "lease_activation",
    "lease_writer",
    "raw_transcript",
}


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
    return _scope_denied_result_for(INTAKE_SCOPE, "intake submit tools")


def _scope_denied_result_for(scope: str, label: str) -> dict[str, Any]:
    return _teaching_error(
        "SCOPE_DENIED",
        f"Connection capability does not include {scope}; {label} are not available.",
        f"Restart the MCP server with LYBRA_CAPABILITY_TOKEN containing operations: [\"{scope}\"].",
        doc_ref="AIPOS-109 capability token scope-gated tool visibility",
    )


def _intake_scope_allowed() -> bool:
    return _capability_has_scope(INTAKE_SCOPE)


def _owner_decision_scope_allowed() -> bool:
    return _capability_has_scope(OWNER_DECISION_SCOPE)


def _queue_claim_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_CLAIM_SCOPE)


def _queue_return_scope_allowed() -> bool:
    return _capability_has_scope(QUEUE_RETURN_SCOPE)


def _map_controlled_execute_error(response: dict[str, Any], *, dry_run_tool: str = "lybra_intake_submit_dry_run") -> dict[str, Any]:
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
            f"Run {dry_run_tool} again and confirm with the new dry_run_token.",
        )
    if "snapshot mismatch" in text:
        return _teaching_error(
            "SNAPSHOT_MISMATCH",
            "The workspace state no longer matches the dry-run snapshot.",
            f"Run {dry_run_tool} again before confirming.",
        )
    return _teaching_error(
        "CONTROLLED_EXECUTE_REJECTED",
        first_message or blocking or "Controlled execute rejected the confirm request.",
        f"Inspect the dry-run response, then run {dry_run_tool} again if appropriate.",
    )


def _queue_claim_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref=SUPERVISED_CLAIM_DOC_REF,
    )


def _queue_return_error(error_code: str, message: str, suggested_next_action: str) -> dict[str, Any]:
    return _teaching_error(
        error_code,
        message,
        suggested_next_action,
        doc_ref="AIPOS-168 Supervised MCP Work Return Path Protocol",
    )


def _normalize_selector(args: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any] | None]:
    task_id = str(args.get("task_id") or "").strip()
    task_path = str(args.get("task_path") or args.get("path") or "").strip()
    if bool(task_id) == bool(task_path):
        return None, None, _queue_claim_error(
            "INVALID_TASK_SELECTOR",
            "Exactly one of task_id or task_path is required.",
            "Call lybra_queue_claim_dry_run with exactly one task selector.",
        )
    return task_id or None, task_path or None, None


def _forbidden_queue_claim_fields(args: dict[str, Any]) -> list[str]:
    return sorted(key for key in args if key in FORBIDDEN_QUEUE_CLAIM_FIELDS)


def _forbidden_queue_return_fields(args: dict[str, Any]) -> list[str]:
    return sorted(key for key in args if key in FORBIDDEN_QUEUE_RETURN_FIELDS)


def _resolve_claim_instance(agent_instance: str, repo_root: Path) -> dict[str, Any]:
    profiles = load_agent_profiles(repo_root)
    resolution = resolve_instance_id(agent_instance, profiles)
    return {
        "profiles": profiles,
        "resolution": resolution,
        "canonical_agent_instance": resolution.get("canonical_instance_id"),
    }


def _claim_owner_reasons() -> list[str]:
    return [
        "MCP Supervised queue_claim requires explicit Owner confirmation for this dry-run preview",
    ]


def _return_owner_reasons() -> list[str]:
    return [
        "MCP Supervised queue_return requires explicit Owner confirmation for this dry-run preview",
    ]


def _claim_metadata(args: dict[str, Any], *, canonical_agent_instance: str) -> dict[str, Any]:
    return {
        "surface": "mcp",
        "operation": "queue_claim",
        "autonomy_mode": "Supervised",
        "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        "agent_instance": str(args.get("agent_instance") or "").strip(),
        "canonical_agent_instance": canonical_agent_instance,
        "runtime_profile": str(args.get("runtime_profile") or "").strip() or None,
        "active_session_id": str(args.get("active_session_id") or "").strip() or None,
        "context_bundle_ack": str(args.get("context_bundle_ack") or "").strip() or None,
        "claim_reason": str(args.get("claim_reason") or "").strip() or None,
        "with_records_requested": bool(args.get("with_records", True)),
        "owner_confirmation_required": True,
        "owner_confirmation_reasons": _claim_owner_reasons(),
        "lease_path": "claim_only",
        "lease_status": "proposed",
    }


def _return_metadata(args: dict[str, Any], *, canonical_agent_instance: str) -> dict[str, Any]:
    return {
        "surface": "mcp",
        "operation": "queue_return",
        "autonomy_mode": "Supervised",
        "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        "agent_instance": str(args.get("agent_instance") or "").strip(),
        "canonical_agent_instance": canonical_agent_instance,
        "claim_id": str(args.get("claim_id") or "").strip() or None,
        "active_session_id": str(args.get("active_session_id") or "").strip() or None,
        "return_reason": str(args.get("return_reason") or "").strip() or None,
        "owner_confirmation_required": True,
        "owner_confirmation_reasons": _return_owner_reasons(),
        "lease_path": "claim_only",
        "lease_status": "proposed",
    }


def _decorate_queue_claim_dry_run(response: dict[str, Any], *, args: dict[str, Any], canonical_agent_instance: str) -> dict[str, Any]:
    data = response.setdefault("data", {})
    if isinstance(data, dict):
        data.setdefault("mcp_claim", _claim_metadata(args, canonical_agent_instance=canonical_agent_instance))
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = str(args.get("agent_instance") or "").strip()
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = str(args.get("owner_policy_ref") or "").strip()
    response["claim_policy"] = (
        data.get("updated_frontmatter", {}).get("claim_policy")
        if isinstance(data.get("updated_frontmatter"), dict)
        else None
    )
    response["claim_match_basis"] = response.get("actor_match")
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
        "next_required_action": "separate explicit lease activation before execution",
    }
    response["planned_records"] = []
    response["owner_confirmation_required"] = True
    response["owner_confirmation_reasons"] = _claim_owner_reasons()
    response["owner_confirmation_token_required"] = OWNER_CONFIRMATION_TOKEN
    response["dry_run_token"] = response.get("dry_run_token") or response.get("dry_run_id")
    response["expires_at"] = response.get("dry_run_expires_at")
    return response


def _decorate_queue_return_dry_run(response: dict[str, Any], *, args: dict[str, Any], canonical_agent_instance: str) -> dict[str, Any]:
    data = response.setdefault("data", {})
    if isinstance(data, dict):
        data.setdefault("mcp_return", _return_metadata(args, canonical_agent_instance=canonical_agent_instance))
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = str(args.get("agent_instance") or "").strip()
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = str(args.get("owner_policy_ref") or "").strip()
    response["executor_status"] = "completed"
    response["audit_readiness"] = "ready"
    response["audit_status"] = "pending"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
    }
    response["planned_records"] = []
    response["owner_confirmation_required"] = True
    response["owner_confirmation_reasons"] = _return_owner_reasons()
    response["owner_confirmation_token_required"] = OWNER_CONFIRMATION_TOKEN
    response["dry_run_token"] = response.get("dry_run_token") or response.get("dry_run_id")
    response["expires_at"] = response.get("dry_run_expires_at")
    response["confirmation_preview"] = {
        "envelope_version": "aipos-168.v1",
        "operation": "queue_return",
        "surface": "mcp",
        "autonomy_mode": "Supervised",
        "task": {
            "task_id": response.get("data", {}).get("task_id") if isinstance(response.get("data"), dict) else args.get("task_id"),
            "task_path": response.get("data", {}).get("source_path") if isinstance(response.get("data"), dict) else args.get("task_path"),
            "current_status": "claimed",
        },
        "actor": {
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
        },
        "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        "return": response.get("return_preview"),
        "lease": response["lease_preview"],
        "preview": {
            "planned_writes": response.get("planned_writes", []),
            "planned_moves": response.get("planned_moves", []),
            "planned_records": [],
            "blocking_reasons": response.get("blocking_reasons", []),
            "warnings": response.get("warnings", []),
        },
        "confirm": {
            "tool_name": "lybra_queue_return_confirm",
            "required_owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
            "dry_run_token": response.get("dry_run_token"),
            "actor": str(args.get("actor") or "").strip(),
            "agent_instance": str(args.get("agent_instance") or "").strip(),
            "canonical_agent_instance": canonical_agent_instance,
            "owner_policy_ref": str(args.get("owner_policy_ref") or "").strip(),
        },
    }
    return response


def _map_owner_decision_dry_run_error(response: dict[str, Any]) -> dict[str, Any]:
    blocking = " ".join(str(item) for item in response.get("blocking_reasons", []))
    if "owner_approval_evidence" in blocking:
        return _teaching_error(
            "MISSING_OWNER_APPROVAL_EVIDENCE",
            "owner_decision_record requires structured owner_approval_evidence.",
            "Add an AIPOS-110 owner_approval_evidence envelope, then call lybra_owner_decision_record_dry_run again.",
            doc_ref="AIPOS-110 Owner Approval Evidence; AIPOS-111 Owner Decision Record; AIPOS-112 writer",
        )
    if "scope" in blocking.lower() or "must match" in blocking:
        return _teaching_error(
            "DECISION_SCOPE_MISMATCH",
            "The decision record scope does not match its evidence or capability scope.",
            "Align applies_to, owner_approval_evidence, and capability_scope, then call lybra_owner_decision_record_dry_run again.",
            doc_ref="AIPOS-111 Owner Decision Record; AIPOS-112 writer",
        )
    return _teaching_error(
        "INVALID_OWNER_DECISION_RECORD",
        blocking or "owner_decision_record dry-run was rejected.",
        "Inspect blocking_reasons, fix the payload, then call lybra_owner_decision_record_dry_run again.",
        doc_ref="AIPOS-111 Owner Decision Record; AIPOS-112 writer",
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
        return _map_controlled_execute_error(response, dry_run_tool="lybra_intake_submit_dry_run")
    return _tool_result(response, is_error=False)


def lybra_owner_decision_record_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _owner_decision_scope_allowed():
        return _scope_denied_result_for(OWNER_DECISION_SCOPE, "owner decision record tools")
    args = arguments or {}
    response = record_owner_decision(args, dry_run=True, repo_root=_repo_root(), actor=str(args.get("actor") or "mcp.client"))
    if response.get("verdict") == "BLOCK":
        return _map_owner_decision_dry_run_error(response)
    return _tool_result(response, is_error=not bool(response.get("ok", False)))


def lybra_owner_decision_record_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _owner_decision_scope_allowed():
        return _scope_denied_result_for(OWNER_DECISION_SCOPE, "owner decision record tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _teaching_error(
            "MISSING_DRY_RUN_TOKEN",
            "lybra_owner_decision_record_confirm requires dry_run_token from a prior dry-run response.",
            "Call lybra_owner_decision_record_dry_run first, then pass its dry_run_token to lybra_owner_decision_record_confirm.",
            doc_ref="AIPOS-109 MCP-native discipline; AIPOS-112 owner_decision_record writer",
        )
    response = execute_dry_run(
        dry_run_token,
        str(args.get("actor") or "mcp.client"),
        owner_confirmation_token=str(args.get("owner_confirmation_token") or "") or None,
        repo_root=_repo_root(),
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_owner_decision_record_dry_run")
    return _tool_result(response, is_error=False)


def lybra_queue_claim_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_claim_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLAIM_SCOPE, "supervised queue claim tools")
    args = arguments or {}
    forbidden = _forbidden_queue_claim_fields(args)
    if forbidden:
        return _queue_claim_error(
            "UNSUPPORTED_QUEUE_CLAIM_FIELD",
            f"Supervised MCP queue_claim does not accept these fields: {', '.join(forbidden)}.",
            "Remove automatic, batch, credential, bearer-token, raw prompt, and raw response fields; then run dry-run again.",
        )
    if str(args.get("autonomy_mode") or "").strip() != "Supervised":
        return _queue_claim_error(
            "INVALID_AUTONOMY_MODE",
            "lybra_queue_claim_dry_run supports only autonomy_mode: Supervised.",
            "Use autonomy_mode: Supervised. Delegated and Standing remain behind separate Owner gates.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _queue_claim_error("ACTOR_REQUIRED", "actor is required.", "Pass the visible claimant actor.")
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not owner_policy_ref:
        return _queue_claim_error(
            "OWNER_POLICY_REF_REQUIRED",
            "owner_policy_ref is required for Supervised MCP queue_claim.",
            "Pass the Owner approval or policy reference authorizing this supervised session.",
        )
    agent_instance = str(args.get("agent_instance") or "").strip()
    if not agent_instance:
        return _queue_claim_error(
            "INSTANCE_REQUIRED",
            "agent_instance is required and must resolve to one canonical concrete instance.",
            "Pass the canonical agent_instance or a non-ambiguous legacy instance ID.",
        )
    task_id, task_path, selector_error = _normalize_selector(args)
    if selector_error is not None:
        return selector_error

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous":
        return _queue_claim_error(
            "AMBIGUOUS_LEGACY_INSTANCE",
            f"agent_instance resolves ambiguously: {agent_instance}.",
            "Use a canonical opaque agent_instance before requesting claim.",
        )
    if not canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_REQUIRED",
            "agent_instance did not resolve to a concrete claimant.",
            "Pass a canonical opaque agent_instance before requesting claim.",
        )
    if actor != canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP claim slice, actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the same canonical opaque instance used for agent_instance.",
        )

    response = claim_task(
        task_id=task_id,
        path=task_path,
        actor=canonical_agent_instance,
        dry_run=True,
        with_records=False,
        repo_root=repo_root,
        owner_confirmation_required_override=True,
        owner_confirmation_reasons_override=_claim_owner_reasons(),
        mcp_claim_metadata=_claim_metadata(args, canonical_agent_instance=canonical_agent_instance),
    )
    decorated = _decorate_queue_claim_dry_run(response, args=args, canonical_agent_instance=canonical_agent_instance)
    if decorated.get("verdict") == "BLOCK":
        return _tool_result(decorated, is_error=True)
    return _tool_result(decorated, is_error=not bool(decorated.get("ok", False)))


def lybra_queue_claim_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_claim_scope_allowed():
        return _scope_denied_result_for(QUEUE_CLAIM_SCOPE, "supervised queue claim tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _queue_claim_error(
            "DRY_RUN_REQUIRED",
            "lybra_queue_claim_confirm requires dry_run_token from a prior lybra_queue_claim_dry_run response.",
            "Call lybra_queue_claim_dry_run first, review the preview, then confirm with its dry_run_token.",
        )
    owner_confirmation_token = str(args.get("owner_confirmation_token") or "").strip()
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return _queue_claim_error(
            "OWNER_CONFIRMATION_REQUIRED",
            "Supervised MCP queue_claim confirm requires owner_confirmation_token: OWNER_CONFIRMED.",
            "Present the dry-run preview to Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
        )
    actor = str(args.get("actor") or "").strip()
    agent_instance = str(args.get("agent_instance") or "").strip()
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not actor or not agent_instance or not owner_policy_ref:
        return _queue_claim_error(
            "CONFIRM_ARGUMENTS_REQUIRED",
            "actor, agent_instance, and owner_policy_ref are required on confirm.",
            "Pass the same actor, agent_instance, and owner_policy_ref reviewed in the dry-run preview.",
        )

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous" or not canonical_agent_instance:
        return _queue_claim_error(
            "AMBIGUOUS_LEGACY_INSTANCE" if resolution.get("resolution") == "ambiguous" else "INSTANCE_REQUIRED",
            f"agent_instance did not resolve to one concrete instance: {agent_instance}.",
            "Use the canonical opaque agent_instance from the dry-run preview.",
        )
    if actor != canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP claim slice, confirm actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the canonical opaque instance from the dry-run preview.",
        )

    token = get_dry_run(dry_run_token)
    if token is None:
        return _queue_claim_error(
            "STALE_DRY_RUN",
            "dry_run_token was not found or is no longer available.",
            "Run lybra_queue_claim_dry_run again, review the new preview, then confirm.",
        )
    source_data = token.plan.get("data") if isinstance(token.plan, dict) else {}
    mcp_claim = source_data.get("mcp_claim") if isinstance(source_data, dict) else None
    if token.operation != "queue_claim" or not isinstance(mcp_claim, dict):
        return _queue_claim_error(
            "INCOMPATIBLE_DRY_RUN",
            "dry_run_token did not come from lybra_queue_claim_dry_run.",
            "Run lybra_queue_claim_dry_run and confirm with that MCP dry_run_token.",
        )
    token_policy = str(mcp_claim.get("owner_policy_ref") or "").strip()
    token_instance = str(mcp_claim.get("canonical_agent_instance") or "").strip()
    if token_policy != owner_policy_ref:
        return _queue_claim_error(
            "OWNER_POLICY_MISMATCH",
            "owner_policy_ref does not match the dry-run preview.",
            "Run lybra_queue_claim_dry_run again or confirm with the reviewed owner_policy_ref.",
        )
    if token_instance != canonical_agent_instance:
        return _queue_claim_error(
            "INSTANCE_MISMATCH",
            "agent_instance does not match the dry-run preview.",
            "Run lybra_queue_claim_dry_run again or confirm with the reviewed agent_instance.",
        )

    response = execute_dry_run(
        dry_run_token,
        canonical_agent_instance,
        owner_confirmation_token=owner_confirmation_token,
        repo_root=repo_root,
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_queue_claim_dry_run")
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = agent_instance
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = owner_policy_ref
    response["lease_status"] = "proposed"
    response["lease_path"] = "claim_only"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
        "next_required_action": "separate explicit lease activation before execution",
    }
    response["provenance"] = {
        "event_type": "mcp_queue_claim",
        "actor": canonical_agent_instance,
        "actor_instance_id": canonical_agent_instance,
        "surface": "mcp",
        "transport": "mcp",
        "owner_policy_ref": owner_policy_ref,
        "autonomy_mode": "Supervised",
        "result": response.get("verdict"),
        "dry_run_id": dry_run_token,
    }
    return _tool_result(response, is_error=False)


def lybra_queue_return_dry_run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_return_scope_allowed():
        return _scope_denied_result_for(QUEUE_RETURN_SCOPE, "supervised queue return tools")
    args = arguments or {}
    forbidden = _forbidden_queue_return_fields(args)
    if forbidden:
        return _queue_return_error(
            "UNSUPPORTED_QUEUE_RETURN_FIELD",
            f"Supervised MCP queue_return does not accept these fields: {', '.join(forbidden)}.",
            "Remove automatic, batch, credential, raw, lease, audit-dispatch, and finalize fields; then run dry-run again.",
        )
    if str(args.get("autonomy_mode") or "").strip() != "Supervised":
        return _queue_return_error(
            "INVALID_AUTONOMY_MODE",
            "lybra_queue_return_dry_run supports only autonomy_mode: Supervised.",
            "Use autonomy_mode: Supervised. Delegated and Standing remain behind separate Owner gates.",
        )
    actor = str(args.get("actor") or "").strip()
    if not actor:
        return _queue_return_error("ACTOR_REQUIRED", "actor is required.", "Pass the visible returning actor.")
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not owner_policy_ref:
        return _queue_return_error(
            "OWNER_POLICY_REF_REQUIRED",
            "owner_policy_ref is required for Supervised MCP queue_return.",
            "Pass the Owner approval or policy reference authorizing this supervised return.",
        )
    agent_instance = str(args.get("agent_instance") or "").strip()
    if not agent_instance:
        return _queue_return_error(
            "INSTANCE_REQUIRED",
            "agent_instance is required and must resolve to one canonical concrete instance.",
            "Pass the canonical agent_instance or a non-ambiguous legacy instance ID.",
        )
    if str(args.get("executor_status") or "completed").strip() != "completed":
        return _queue_return_error(
            "INVALID_EXECUTOR_STATUS",
            "queue_return requires executor_status: completed.",
            "Return only completed executor work through this first slice.",
        )
    if str(args.get("audit_readiness") or "ready").strip() != "ready":
        return _queue_return_error(
            "INVALID_AUDIT_READINESS",
            "queue_return requires audit_readiness: ready.",
            "Return only audit-ready executor work through this first slice.",
        )
    if not (str(args.get("result_summary") or "").strip() or args.get("artifact_refs") or str(args.get("completion_report_ref") or "").strip()):
        return _queue_return_error(
            "MISSING_RETURN_EVIDENCE",
            "result_summary, artifact_refs, or completion_report_ref is required.",
            "Provide normalized non-secret executor evidence before returning work.",
        )
    task_id = str(args.get("task_id") or "").strip()
    task_path = str(args.get("task_path") or args.get("path") or "").strip()
    if bool(task_id) == bool(task_path):
        return _queue_return_error(
            "INVALID_TASK_SELECTOR",
            "Exactly one of task_id or task_path is required.",
            "Call lybra_queue_return_dry_run with exactly one task selector.",
        )

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous":
        return _queue_return_error(
            "AMBIGUOUS_LEGACY_INSTANCE",
            f"agent_instance resolves ambiguously: {agent_instance}.",
            "Use a canonical opaque agent_instance before returning work.",
        )
    if not canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_REQUIRED",
            "agent_instance did not resolve to a concrete returning instance.",
            "Pass a canonical opaque agent_instance before returning work.",
        )
    if actor != canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP return slice, actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the same canonical opaque instance used for agent_instance.",
        )

    response = return_task(
        task_id=task_id or None,
        path=task_path or None,
        actor=canonical_agent_instance,
        agent_instance=agent_instance,
        owner_policy_ref=owner_policy_ref,
        claim_id=str(args.get("claim_id") or "").strip() or None,
        active_session_id=str(args.get("active_session_id") or "").strip() or None,
        result_summary=str(args.get("result_summary") or "").strip() or None,
        artifact_refs=args.get("artifact_refs"),
        completion_report_ref=str(args.get("completion_report_ref") or "").strip() or None,
        return_reason=str(args.get("return_reason") or "").strip() or None,
        dry_run=True,
        repo_root=repo_root,
        mcp_return_metadata=_return_metadata(args, canonical_agent_instance=canonical_agent_instance),
    )
    decorated = _decorate_queue_return_dry_run(response, args=args, canonical_agent_instance=canonical_agent_instance)
    if decorated.get("verdict") == "BLOCK":
        return _tool_result(decorated, is_error=True)
    return _tool_result(decorated, is_error=not bool(decorated.get("ok", False)))


def lybra_queue_return_confirm(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _queue_return_scope_allowed():
        return _scope_denied_result_for(QUEUE_RETURN_SCOPE, "supervised queue return tools")
    args = arguments or {}
    dry_run_token = str(args.get("dry_run_token") or "").strip()
    if not dry_run_token:
        return _queue_return_error(
            "DRY_RUN_REQUIRED",
            "lybra_queue_return_confirm requires dry_run_token from a prior lybra_queue_return_dry_run response.",
            "Call lybra_queue_return_dry_run first, review the confirmation_preview, then confirm.",
        )
    owner_confirmation_token = str(args.get("owner_confirmation_token") or "").strip()
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return _queue_return_error(
            "OWNER_CONFIRMATION_REQUIRED",
            "Supervised MCP queue_return confirm requires owner_confirmation_token: OWNER_CONFIRMED.",
            "Present the confirmation_preview to Owner, then retry confirm with owner_confirmation_token set to OWNER_CONFIRMED.",
        )
    actor = str(args.get("actor") or "").strip()
    agent_instance = str(args.get("agent_instance") or "").strip()
    owner_policy_ref = str(args.get("owner_policy_ref") or "").strip()
    if not actor or not agent_instance or not owner_policy_ref:
        return _queue_return_error(
            "CONFIRM_ARGUMENTS_REQUIRED",
            "actor, agent_instance, and owner_policy_ref are required on confirm.",
            "Pass the same actor, agent_instance, and owner_policy_ref reviewed in the confirmation_preview.",
        )

    repo_root = _repo_root()
    resolved = _resolve_claim_instance(agent_instance, repo_root)
    resolution = resolved["resolution"]
    canonical_agent_instance = str(resolved.get("canonical_agent_instance") or "").strip()
    if resolution.get("resolution") == "ambiguous" or not canonical_agent_instance:
        return _queue_return_error(
            "AMBIGUOUS_LEGACY_INSTANCE" if resolution.get("resolution") == "ambiguous" else "INSTANCE_REQUIRED",
            f"agent_instance did not resolve to one concrete instance: {agent_instance}.",
            "Use the canonical opaque agent_instance from the confirmation_preview.",
        )
    if actor != canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_MISMATCH",
            "For the first Supervised MCP return slice, confirm actor must equal the resolved canonical agent_instance.",
            "Retry with actor set to the canonical opaque instance from the confirmation_preview.",
        )

    token = get_dry_run(dry_run_token)
    if token is None:
        return _queue_return_error(
            "STALE_DRY_RUN",
            "dry_run_token was not found or is no longer available.",
            "Run lybra_queue_return_dry_run again, review the new confirmation_preview, then confirm.",
        )
    source_data = token.plan.get("data") if isinstance(token.plan, dict) else {}
    mcp_return = source_data.get("mcp_return") if isinstance(source_data, dict) else None
    if token.operation != "queue_return" or not isinstance(mcp_return, dict):
        return _queue_return_error(
            "INCOMPATIBLE_DRY_RUN",
            "dry_run_token did not come from lybra_queue_return_dry_run.",
            "Run lybra_queue_return_dry_run and confirm with that MCP dry_run_token.",
        )
    token_policy = str(mcp_return.get("owner_policy_ref") or "").strip()
    token_instance = str(mcp_return.get("canonical_agent_instance") or "").strip()
    if token_policy != owner_policy_ref:
        return _queue_return_error(
            "OWNER_POLICY_MISMATCH",
            "owner_policy_ref does not match the confirmation_preview.",
            "Run lybra_queue_return_dry_run again or confirm with the reviewed owner_policy_ref.",
        )
    if token_instance != canonical_agent_instance:
        return _queue_return_error(
            "INSTANCE_MISMATCH",
            "agent_instance does not match the confirmation_preview.",
            "Run lybra_queue_return_dry_run again or confirm with the reviewed agent_instance.",
        )

    response = execute_dry_run(
        dry_run_token,
        canonical_agent_instance,
        owner_confirmation_token=owner_confirmation_token,
        repo_root=repo_root,
    )
    if not response.get("ok", False):
        return _map_controlled_execute_error(response, dry_run_tool="lybra_queue_return_dry_run")
    response["surface"] = "mcp"
    response["autonomy_mode"] = "Supervised"
    response["agent_instance"] = agent_instance
    response["canonical_agent_instance"] = canonical_agent_instance
    response["owner_policy_ref"] = owner_policy_ref
    response["executor_status"] = "completed"
    response["audit_readiness"] = "ready"
    response["audit_status"] = "pending"
    response["lease_status"] = "proposed"
    response["lease_path"] = "claim_only"
    response["lease_preview"] = {
        "lease_path": "claim_only",
        "lease_status": "proposed",
        "active_lease_written": False,
    }
    response["provenance"] = {
        "event_type": "mcp_queue_return",
        "actor": canonical_agent_instance,
        "actor_instance_id": canonical_agent_instance,
        "surface": "mcp",
        "transport": "mcp",
        "owner_policy_ref": owner_policy_ref,
        "autonomy_mode": "Supervised",
        "result": response.get("verdict"),
        "dry_run_id": dry_run_token,
        "executor_status": "completed",
        "audit_readiness": "ready",
        "audit_status_after_return": "pending",
        "lease_status": "proposed",
    }
    return _tool_result(response, is_error=False)


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any] | None], dict[str, Any]]] = {
    "lybra_queue_list": lybra_queue_list,
    "lybra_task_preview": lybra_task_preview,
    "lybra_validate": lybra_validate,
    "lybra_context_pack_build": lybra_context_pack_build,
    "lybra_intake_submit_dry_run": lybra_intake_submit_dry_run,
    "lybra_intake_submit_confirm": lybra_intake_submit_confirm,
    "lybra_owner_decision_record_dry_run": lybra_owner_decision_record_dry_run,
    "lybra_owner_decision_record_confirm": lybra_owner_decision_record_confirm,
    "lybra_queue_claim_dry_run": lybra_queue_claim_dry_run,
    "lybra_queue_claim_confirm": lybra_queue_claim_confirm,
    "lybra_queue_return_dry_run": lybra_queue_return_dry_run,
    "lybra_queue_return_confirm": lybra_queue_return_confirm,
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
    {
        "name": "lybra_owner_decision_record_dry_run",
        "description": (
            "When to use: create a controlled execute preview for recording a scoped Owner decision after an Owner Decision Gate has explicit evidence. "
            "Prerequisites: this MCP connection must have a capability_token with owner_decision_record scope; owner_approval_evidence is required and must align with applies_to; capability_scope must include owner_decision_record and the target project when present; this tool does not publish, mutate queues, append orchestration events, or execute follow-up work. "
            "Return structure: a controlled execute envelope with verdict, planned_writes, dry_run_token, dry_run_snapshot_hash, dry_run_created_at, dry_run_expires_at, and rendered Owner decision record content. "
            "Next-step hint: pass dry_run_token to lybra_owner_decision_record_confirm; confirm writes only a records artifact under 5_tasks/records/owner_decisions and no agent takes automatic follow-up action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "decision_id": {"type": "string"},
                "decision_type": {"type": "string"},
                "decision_status": {"type": "string"},
                "decided_at": {"type": "string"},
                "decided_by_ref": {"type": "string"},
                "captured_by": {"type": "string"},
                "capture_surface": {"type": "string"},
                "decision_summary": {"type": "string"},
                "decision_rationale": {"type": "string"},
                "applies_to": {"type": "object"},
                "approval_scope": {"type": "object"},
                "owner_approval_evidence": {"type": "object"},
                "refs": {"type": "array", "items": {"type": "string"}},
                "capability_scope": {"type": "object"},
            },
            "required": [
                "decision_id",
                "decision_type",
                "decision_status",
                "decided_at",
                "decided_by_ref",
                "captured_by",
                "capture_surface",
                "decision_summary",
                "applies_to",
                "approval_scope",
                "owner_approval_evidence",
                "capability_scope",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_owner_decision_record_confirm",
        "description": (
            "When to use: confirm a prior lybra_owner_decision_record_dry_run after reviewing its planned_writes and rendered Owner decision record. "
            "Prerequisites: this MCP connection must have a capability_token with owner_decision_record scope; dry_run_token is required and must come from the immediately preceding dry-run flow; the dry-run token must be unexpired and its snapshot must still match. "
            "Return structure: a controlled execute result with performed_writes when the Owner decision record is written, or a structured teaching error with error_code, message, suggested_next_action, and doc_ref. "
            "Next-step hint: confirm only writes a record under 5_tasks/records/owner_decisions; it does not publish drafts, mutate queues, append orchestration events, or continue runtime execution."
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
    {
        "name": "lybra_queue_claim_dry_run",
        "description": (
            "When to use: create a Supervised MCP preview for explicitly claiming one pending task for one concrete agent instance. "
            "Prerequisites: this MCP connection must have a capability_token with queue_claim scope; autonomy_mode must be Supervised; actor must equal the resolved canonical agent_instance in this first slice; owner_policy_ref is required; this tool does not execute work, dispatch audit, write records, or activate a lease. "
            "Return structure: a controlled execute envelope with verdict, planned_moves, dry_run_token, dry_run_snapshot_hash, owner_confirmation_required, canonical_agent_instance, owner_policy_ref, and lease_status proposed. "
            "Next-step hint: present the preview to Owner, then pass dry_run_token to lybra_queue_claim_confirm with owner_confirmation_token OWNER_CONFIRMED; execution still requires a later explicit lease activation path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "task_path": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "autonomy_mode": {"type": "string", "enum": ["Supervised"]},
                "owner_policy_ref": {"type": "string"},
                "runtime_profile": {"type": "string"},
                "active_session_id": {"type": "string"},
                "context_bundle_ack": {"type": "string"},
                "with_records": {"type": "boolean"},
                "claim_reason": {"type": "string"},
            },
            "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_claim_confirm",
        "description": (
            "When to use: confirm a prior Supervised MCP queue-claim dry-run after Owner has reviewed the exact preview. "
            "Prerequisites: this MCP connection must have a capability_token with queue_claim scope; dry_run_token is required; owner_confirmation_token must be OWNER_CONFIRMED; actor, agent_instance, and owner_policy_ref must match the dry-run preview. "
            "Return structure: a controlled execute result with performed_moves for the pending-to-claimed queue move, canonical_agent_instance, owner_policy_ref, provenance minimums, and lease_status proposed. "
            "Next-step hint: confirm only claims the task; it does not launch a worker, renew a lease, dispatch audit, finalize, or execute follow-up work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "owner_policy_ref": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_return_dry_run",
        "description": (
            "When to use: create a Supervised MCP preview for returning completed executor work on one already claimed task. "
            "Prerequisites: this MCP connection must have a capability_token with queue_return scope; autonomy_mode must be Supervised; actor must equal the resolved canonical agent_instance in this first slice; owner_policy_ref is required; normalized non-secret executor evidence is required. "
            "Return structure: a controlled execute envelope with verdict, planned_writes, dry_run_token, dry_run_snapshot_hash, confirmation_preview, canonical_agent_instance, owner_policy_ref, executor_status completed, audit_readiness ready, and lease_status proposed. "
            "Next-step hint: present confirmation_preview to Owner, then pass dry_run_token to lybra_queue_return_confirm with owner_confirmation_token OWNER_CONFIRMED; this tool does not activate leases, dispatch audit, record audit PASS, or finalize."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "task_path": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "autonomy_mode": {"type": "string", "enum": ["Supervised"]},
                "owner_policy_ref": {"type": "string"},
                "claim_id": {"type": "string"},
                "active_session_id": {"type": "string"},
                "result_summary": {"type": "string"},
                "artifact_refs": {"type": "array", "items": {"type": "string"}},
                "completion_report_ref": {"type": "string"},
                "executor_status": {"type": "string", "enum": ["completed"]},
                "audit_readiness": {"type": "string", "enum": ["ready"]},
                "return_reason": {"type": "string"},
            },
            "required": ["actor", "agent_instance", "autonomy_mode", "owner_policy_ref"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lybra_queue_return_confirm",
        "description": (
            "When to use: confirm a prior Supervised MCP queue-return dry-run after Owner has reviewed the exact confirmation_preview. "
            "Prerequisites: this MCP connection must have a capability_token with queue_return scope; dry_run_token is required; owner_confirmation_token must be OWNER_CONFIRMED; actor, agent_instance, and owner_policy_ref must match the dry-run preview. "
            "Return structure: a controlled execute result with performed_writes for the claimed task metadata update, canonical_agent_instance, owner_policy_ref, provenance minimums, executor_status completed, audit_readiness ready, and lease_status proposed. "
            "Next-step hint: confirm only marks executor completion plus audit readiness; it does not activate a lease, write records, dispatch audit, record audit PASS, or finalize."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run_token": {"type": "string"},
                "actor": {"type": "string"},
                "agent_instance": {"type": "string"},
                "owner_policy_ref": {"type": "string"},
                "owner_confirmation_token": {"type": "string"},
            },
            "required": ["dry_run_token", "actor", "agent_instance", "owner_policy_ref", "owner_confirmation_token"],
            "additionalProperties": False,
        },
    },
]


def visible_tool_descriptors() -> list[dict[str, Any]]:
    descriptors = list(READ_TOOL_DESCRIPTORS)
    if _intake_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_intake_submit"))
    if _owner_decision_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_owner_decision_record"))
    if _queue_claim_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_claim"))
    if _queue_return_scope_allowed():
        descriptors.extend(tool for tool in WRITE_TOOL_DESCRIPTORS if tool["name"].startswith("lybra_queue_return"))
    return descriptors


TOOL_DESCRIPTORS = READ_TOOL_DESCRIPTORS
