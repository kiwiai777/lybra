from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 1800
OWNER_CONFIRMATION_TOKEN = "OWNER_CONFIRMED"
SUPPORTED_OPERATIONS = {
    "draft_create",
    "draft_publish",
    "queue_claim",
    "queue_return",
    "orchestration_event_append",
    "planner_iteration_append",
    "intake_submit",
    "owner_decision_record",
    "workspace_init",
}


@dataclass
class DryRunToken:
    dry_run_id: str
    operation: str
    actor: str
    created_at: str
    expires_at: str
    snapshot_hash: str
    plan: dict[str, Any]


_TOKEN_STORE: dict[str, DryRunToken] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_actor(actor: str | None) -> str:
    value = str(actor or "").strip()
    if not value:
        raise ValueError("actor is required")
    return value


def _normalize_relpath(path: Any) -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    pure = Path(text)
    return pure.as_posix()


def _normalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_for_hash(value[key]) for key in sorted(value.keys())}
    if isinstance(value, list):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _stable_planned_writes(items: Any) -> list[dict[str, Any]]:
    stable: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return stable
    for item in items:
        if not isinstance(item, dict):
            continue
        stable.append(
            {
                "path": _normalize_relpath(item.get("path")),
                "kind": item.get("kind"),
                "type": item.get("type"),
                "record_type": item.get("record_type"),
            }
        )
    return stable


def _stable_planned_moves(items: Any) -> list[dict[str, Any]]:
    stable: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return stable
    for item in items:
        if not isinstance(item, dict):
            continue
        stable.append(
            {
                "from": _normalize_relpath(item.get("from")),
                "to": _normalize_relpath(item.get("to")),
                "kind": item.get("kind"),
                "type": item.get("type"),
            }
        )
    return stable


def build_snapshot_payload(operation: str, actor: str, plan: dict[str, Any]) -> dict[str, Any]:
    data = plan.get("data") or {}
    payload = {
        "operation": operation,
        "actor": actor,
        "verdict": plan.get("verdict"),
        "task_id": data.get("task_id"),
        "task_path": _normalize_relpath(data.get("source_path")) or _normalize_relpath(data.get("target_path")),
        "source_path": _normalize_relpath(data.get("source_path")),
        "destination_path": _normalize_relpath(data.get("target_path")),
        "queue_state": {
            "from": data.get("from_state"),
            "to": data.get("to_state"),
        },
        "frontmatter_status": ((data.get("updated_frontmatter") or {}).get("status") if isinstance(data.get("updated_frontmatter"), dict) else None),
        "planned_writes": _stable_planned_writes(plan.get("planned_writes", [])),
        "planned_moves": _stable_planned_moves(plan.get("planned_moves", [])),
        "target_path": _normalize_relpath(data.get("target_path")),
        "event_entry": _normalize_for_hash(data.get("event_entry")),
        "iteration_entry": _normalize_for_hash(data.get("iteration_entry")),
        "original_payload": _normalize_for_hash(data.get("original_payload")),
        "write_snapshot_hash": data.get("write_snapshot_hash"),
        "target_file_state": _normalize_for_hash(data.get("target_file_state")),
        "with_records": bool(data.get("with_records", False)),
        "owner_confirmation_required": bool(plan.get("owner_confirmation_required", False)),
        "owner_confirmation_reasons": list(plan.get("owner_confirmation_reasons", [])),
        "blocking_reasons": list(plan.get("blocking_reasons", [])),
    }
    return _normalize_for_hash(payload)


def snapshot_hash(operation: str, actor: str, plan: dict[str, Any]) -> str:
    payload = build_snapshot_payload(operation, actor, plan)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def register_dry_run(
    *,
    operation: str,
    actor: str,
    plan: dict[str, Any],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"Unsupported controlled execute operation: {operation}")
    actor_normalized = _normalize_actor(actor)
    ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    created_at_dt = _utc_now()
    expires_at_dt = created_at_dt + timedelta(seconds=ttl)

    plan_copy = deepcopy(plan)
    dr_id = f"dryrun_{uuid.uuid4().hex}"
    dr_hash = snapshot_hash(operation, actor_normalized, plan_copy)
    token = DryRunToken(
        dry_run_id=dr_id,
        operation=operation,
        actor=actor_normalized,
        created_at=_iso_z(created_at_dt),
        expires_at=_iso_z(expires_at_dt),
        snapshot_hash=dr_hash,
        plan=plan_copy,
    )
    _TOKEN_STORE[dr_id] = token

    return {
        "dry_run_id": token.dry_run_id,
        "dry_run_snapshot_hash": token.snapshot_hash,
        "dry_run_created_at": token.created_at,
        "dry_run_expires_at": token.expires_at,
    }


def get_dry_run(dry_run_id: str) -> DryRunToken | None:
    return _TOKEN_STORE.get(str(dry_run_id))


def is_expired(token: DryRunToken) -> bool:
    expires_at = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00"))
    return _utc_now() > expires_at


def validate_owner_confirmation(*, required: bool, owner_confirmation_token: str | None) -> tuple[bool, str | None]:
    if not required:
        return True, None
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return False, "owner confirmation token is required and must equal OWNER_CONFIRMED"
    return True, None


def clear_tokens() -> None:
    _TOKEN_STORE.clear()
