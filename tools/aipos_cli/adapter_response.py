from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ENVELOPE_FIELDS = [
    "ok",
    "verdict",
    "operation",
    "dry_run",
    "actor",
    "actor_match",
    "timestamp",
    "data",
    "summary",
    "planned_writes",
    "planned_moves",
    "performed_writes",
    "performed_moves",
    "warnings",
    "blocking_reasons",
    "needs_owner_reasons",
    "owner_confirmation_required",
    "owner_confirmation_reasons",
    "execute_allowed",
    "execute_blocking_reasons",
    "dry_run_id",
    "dry_run_token",
    "dry_run_snapshot_hash",
    "dry_run_created_at",
    "dry_run_expires_at",
    "safety_notice",
    "errors",
]

VALID_VERDICTS = {"PASS", "WARN", "NEEDS_OWNER", "BLOCK"}

ERROR_CATEGORIES = {
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "DUPLICATE_ID",
    "PATH_UNSAFE",
    "ACTOR_MISMATCH",
    "STATUS_MISMATCH",
    "OWNER_CONFIRMATION_REQUIRED",
    "RECORD_COLLISION",
    "UNSUPPORTED_OPERATION",
    "INTERNAL_ERROR",
    "DRY_RUN_REQUIRED",
    "REVALIDATION_FAILED",
    "ADAPTER_INVOCATION_ERROR",
    "BACKEND_CONTRACT_MISMATCH",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def error_entry(
    category: str,
    message: str,
    *,
    field: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = category if category in ERROR_CATEGORIES else "INTERNAL_ERROR"
    entry: dict[str, Any] = {
        "category": normalized,
        "message": message,
        "details": details or {},
    }
    if field is not None:
        entry["field"] = field
    return entry


def derive_verdict(
    *,
    blocking_reasons: list[str] | None = None,
    warnings: list[str] | None = None,
    needs_owner_reasons: list[str] | None = None,
    fallback: str = "PASS",
) -> str:
    if blocking_reasons:
        return "BLOCK"
    if needs_owner_reasons:
        return "NEEDS_OWNER"
    if warnings:
        return "WARN"
    return fallback if fallback in VALID_VERDICTS else "PASS"


def make_response(
    *,
    ok: bool = True,
    verdict: str = "PASS",
    operation: str,
    dry_run: bool,
    actor: Any = None,
    actor_match: Any = None,
    data: Any = None,
    summary: Any = None,
    planned_writes: list[dict[str, Any]] | None = None,
    planned_moves: list[dict[str, Any]] | None = None,
    performed_writes: list[dict[str, Any]] | None = None,
    performed_moves: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    blocking_reasons: list[str] | None = None,
    needs_owner_reasons: list[str] | None = None,
    owner_confirmation_required: bool = False,
    owner_confirmation_reasons: list[str] | None = None,
    execute_allowed: bool | None = None,
    execute_blocking_reasons: list[str] | None = None,
    dry_run_id: str | None = None,
    dry_run_token: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    dry_run_created_at: str | None = None,
    dry_run_expires_at: str | None = None,
    safety_notice: str = "",
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "verdict": verdict if verdict in VALID_VERDICTS else "BLOCK",
        "operation": operation,
        "dry_run": dry_run,
        "actor": actor,
        "actor_match": actor_match,
        "timestamp": utc_timestamp(),
        "data": data,
        "summary": summary,
        "planned_writes": list(planned_writes or []),
        "planned_moves": list(planned_moves or []),
        "performed_writes": list(performed_writes or []),
        "performed_moves": list(performed_moves or []),
        "warnings": list(warnings or []),
        "blocking_reasons": list(blocking_reasons or []),
        "needs_owner_reasons": list(needs_owner_reasons or []),
        "owner_confirmation_required": owner_confirmation_required,
        "owner_confirmation_reasons": list(owner_confirmation_reasons or []),
        "execute_allowed": execute_allowed,
        "execute_blocking_reasons": list(execute_blocking_reasons or []),
        "dry_run_id": dry_run_id,
        "dry_run_token": dry_run_token,
        "dry_run_snapshot_hash": dry_run_snapshot_hash,
        "dry_run_created_at": dry_run_created_at,
        "dry_run_expires_at": dry_run_expires_at,
        "safety_notice": safety_notice,
        "errors": list(errors or []),
    }


def blocked_response(
    *,
    operation: str,
    dry_run: bool,
    category: str,
    message: str,
    actor: Any = None,
    actor_match: Any = None,
    field: str | None = None,
    data: Any = None,
    summary: Any = None,
    planned_writes: list[dict[str, Any]] | None = None,
    planned_moves: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    needs_owner_reasons: list[str] | None = None,
    owner_confirmation_required: bool = False,
    owner_confirmation_reasons: list[str] | None = None,
    safety_notice: str = "",
) -> dict[str, Any]:
    return make_response(
        ok=False,
        verdict="BLOCK",
        operation=operation,
        dry_run=dry_run,
        actor=actor,
        actor_match=actor_match,
        data=data,
        summary=summary,
        planned_writes=planned_writes,
        planned_moves=planned_moves,
        warnings=warnings,
        blocking_reasons=[message],
        needs_owner_reasons=needs_owner_reasons,
        owner_confirmation_required=owner_confirmation_required,
        owner_confirmation_reasons=owner_confirmation_reasons,
        safety_notice=safety_notice,
        errors=[error_entry(category, message, field=field)],
    )
