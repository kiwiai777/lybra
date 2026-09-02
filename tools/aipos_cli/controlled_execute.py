from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.schema_constants import RecordType

DEFAULT_TTL_SECONDS = 600
MAX_TTL_SECONDS = 1800
OWNER_CONFIRMATION_TOKEN = "OWNER_CONFIRMED"
SUPPORTED_OPERATIONS = {
    "draft_create",
    "draft_publish",
    "queue_claim",
    "queue_return",
    "queue_withdraw",  # AIPOS-315: G2 两阶段动词
    "queue_amend",     # AIPOS-315: G2 两阶段动词
    "orchestration_event_append",
    "planner_iteration_append",
    "intake_submit",
    "owner_decision_record",
    "owner_verification_record",
    "bench_audit_submit",
    "workspace_init",
    "audit_dispatch",
    "audit_verdict",
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

# AIPOS-351: File-based token persistence to survive MCP server process restarts.
# Root cause of STALE_DRY_RUN: _TOKEN_STORE was process-local (in-memory dict).
# When the MCP server restarted between dry_run and confirm, tokens were lost,
# causing get_dry_run() to return None → confirm reported STALE_DRY_RUN.
# Fix: persist tokens to disk; reload on demand when not found in memory.
_TOKEN_PERSIST_DIR: Path | None = None


def _resolve_persist_dir() -> Path | None:
    """Resolve the token persistence directory.
    
    Uses LYBRA_TOKEN_STORE_DIR env var if set, otherwise uses a temp directory
    under the system temp dir. Returns None if persistence is disabled.
    """
    global _TOKEN_PERSIST_DIR
    if _TOKEN_PERSIST_DIR is not None:
        return _TOKEN_PERSIST_DIR
    
    env_dir = os.environ.get("LYBRA_TOKEN_STORE_DIR", "").strip()
    if env_dir:
        persist_dir = Path(env_dir).expanduser().resolve()
    else:
        # Default: use a temp directory that survives process restarts
        import tempfile
        persist_dir = Path(tempfile.gettempdir()) / "lybra_dry_run_tokens"
    
    try:
        persist_dir.mkdir(parents=True, exist_ok=True)
        _TOKEN_PERSIST_DIR = persist_dir
        return persist_dir
    except (OSError, PermissionError):
        # If we can't create the directory, disable persistence
        _TOKEN_PERSIST_DIR = Path("/dev/null")  # Sentinel: tried but failed
        return None


def _persist_token_to_disk(token: DryRunToken) -> None:
    """Write token to disk for crash/restart recovery (AIPOS-351)."""
    persist_dir = _resolve_persist_dir()
    if persist_dir is None or not persist_dir.is_dir():
        return
    
    token_file = persist_dir / f"{token.dry_run_id}.json"
    try:
        # Serialize the token (plan may contain complex objects)
        token_data = {
            "dry_run_id": token.dry_run_id,
            "operation": token.operation,
            "actor": token.actor,
            "created_at": token.created_at,
            "expires_at": token.expires_at,
            "snapshot_hash": token.snapshot_hash,
            "plan": token.plan,
        }
        token_file.write_text(json.dumps(token_data, ensure_ascii=False, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        # Persistence failure is non-fatal; in-memory store is the primary path
        pass


def _load_token_from_disk(dry_run_id: str) -> DryRunToken | None:
    """Load token from disk when not found in memory (AIPOS-351)."""
    persist_dir = _resolve_persist_dir()
    if persist_dir is None or not persist_dir.is_dir():
        return None
    
    token_file = persist_dir / f"{dry_run_id}.json"
    if not token_file.is_file():
        return None
    
    try:
        token_data = json.loads(token_file.read_text(encoding="utf-8"))
        token = DryRunToken(
            dry_run_id=token_data["dry_run_id"],
            operation=token_data["operation"],
            actor=token_data["actor"],
            created_at=token_data["created_at"],
            expires_at=token_data["expires_at"],
            snapshot_hash=token_data["snapshot_hash"],
            plan=token_data["plan"],
        )
        # Restore to in-memory store for subsequent lookups
        _TOKEN_STORE[dry_run_id] = token
        return token
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _cleanup_expired_tokens() -> None:
    """Remove expired tokens from both memory and disk (AIPOS-351)."""
    now = _utc_now()
    expired_ids = [
        dr_id for dr_id, token in _TOKEN_STORE.items()
        if datetime.fromisoformat(token.expires_at.replace("Z", "+00:00")) <= now
    ]
    for dr_id in expired_ids:
        del _TOKEN_STORE[dr_id]
    
    # Also clean up expired files on disk
    persist_dir = _resolve_persist_dir()
    if persist_dir is None or not persist_dir.is_dir():
        return
    
    for token_file in persist_dir.glob("dryrun_*.json"):
        try:
            token_data = json.loads(token_file.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))
            if expires_at <= now:
                token_file.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            # Clean up corrupt files
            try:
                token_file.unlink(missing_ok=True)
            except OSError:
                pass


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


def _stable_planned_writes(items: Any, *, operation: str | None = None) -> list[dict[str, Any]]:
    stable: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return stable
    for item in items:
        if not isinstance(item, dict):
            continue
        path = _normalize_relpath(item.get("path"))
        if operation == "queue_return" and item.get("record_type") in {RecordType.RETURN_RECORD, RecordType.INGESTED_ARTIFACT}:
            # These paths embed the timestamp-derived return_id. Exclude the path
            # from the hash; ingestion content integrity is covered separately by
            # scratch_ingestion_digest (AIPOS-196a R-B).
            path = None
        if operation == "audit_verdict" and item.get("record_type") in {RecordType.AUDIT_VERDICT_RECORD, RecordType.SESSION_RECORD}:
            # AIPOS-F70-fix1: verdict record paths embed timestamp-derived verdict_id.
            # Session records are also updated during verdict dry_run.
            # Exclude both from snapshot hash to align with queue_return behavior.
            path = None
        stable.append(
            {
                "path": path,
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
    original_payload = _normalize_for_hash(data.get("original_payload"))
    if operation == "queue_return" and isinstance(original_payload, dict):
        original_payload = dict(original_payload)
        original_payload.pop("planned_returned_at", None)
    if operation == "audit_verdict" and isinstance(original_payload, dict):
        original_payload = dict(original_payload)
        original_payload.pop("planned_verdict_at", None)
        original_payload.pop("planned_verdict_id", None)
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
        "planned_writes": _stable_planned_writes(plan.get("planned_writes", []), operation=operation),
        "planned_moves": _stable_planned_moves(plan.get("planned_moves", [])),
        "target_path": _normalize_relpath(data.get("target_path")),
        "event_entry": _normalize_for_hash(data.get("event_entry")),
        "iteration_entry": _normalize_for_hash(data.get("iteration_entry")),
        "original_payload": original_payload,
        "write_snapshot_hash": data.get("write_snapshot_hash"),
        "target_file_state": _normalize_for_hash(data.get("target_file_state")),
        "with_records": bool(data.get("with_records", False)),
        "scratch_ingestion_digest": data.get("scratch_ingestion_digest"),
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
    # AIPOS-351: persist to disk so tokens survive process restarts
    _persist_token_to_disk(token)

    return {
        "dry_run_id": token.dry_run_id,
        "dry_run_snapshot_hash": token.snapshot_hash,
        "dry_run_created_at": token.created_at,
        "dry_run_expires_at": token.expires_at,
    }


def get_dry_run(dry_run_id: str) -> DryRunToken | None:
    """Look up a dry-run token by ID.
    
    AIPOS-351: first checks in-memory store, then falls back to disk persistence.
    This ensures tokens survive MCP server process restarts (the root cause of STALE_DRY_RUN).
    """
    dr_id = str(dry_run_id)
    # Fast path: in-memory store
    token = _TOKEN_STORE.get(dr_id)
    if token is not None:
        return token
    # AIPOS-351: slow path: try to load from disk (survives process restarts)
    return _load_token_from_disk(dr_id)


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
    """Clear all tokens from memory and disk (AIPOS-351: also cleans persisted files)."""
    _TOKEN_STORE.clear()
    # Also clean up persisted files
    persist_dir = _resolve_persist_dir()
    if persist_dir is not None and persist_dir.is_dir():
        for token_file in persist_dir.glob("dryrun_*.json"):
            try:
                token_file.unlink(missing_ok=True)
            except OSError:
                pass
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
