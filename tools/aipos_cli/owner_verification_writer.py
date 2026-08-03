"""AIPOS-273: Owner verification record writer (approve/reject).

Writes owner verification records to ``5_tasks/records/owner_verifications/<task_id>/``
in append-only fashion. Each record is timestamped and captures the Owner's
decision (approve/reject) with optional reason. Records are consumed by the
watch mechanism and inform the true-stage derivation.

Record structure mirrors existing record types (returns/verdicts) — frontmatter
+ markdown body. Append-only: never modifies existing records; repeats allowed
(last record wins for UI display, but all records preserved for audit trail).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.record_writer import render_markdown




OWNER_VERIFICATIONS_DIR = Path("5_tasks/records/owner_verifications")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
ALLOWED_DECISIONS = {"approve", "reject"}
ALLOWED_DECIDED_VIA = {"web_session", "cli", "mcp", "external"}


def _utc_now() -> str:
    """Returns current UTC timestamp in ISO format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_task_id(value: Any, blocking_reasons: list[str]) -> str:
    """Validates and normalizes task_id."""
    if not isinstance(value, str) or not value.strip():
        blocking_reasons.append("Missing required field: task_id")
        return ""
    text = value.strip()
    if not TASK_ID_PATTERN.fullmatch(text):
        blocking_reasons.append("Invalid task_id format")
        return ""
    return text


def _normalize_decision(value: Any, blocking_reasons: list[str]) -> str:
    """Validates decision is approve or reject."""
    if not isinstance(value, str) or not value.strip():
        blocking_reasons.append("Missing required field: decision")
        return ""
    text = value.strip().lower()
    if text not in ALLOWED_DECISIONS:
        blocking_reasons.append(f"Invalid decision: must be one of {ALLOWED_DECISIONS}")
        return ""
    return text


def _normalize_decided_via(value: Any, blocking_reasons: list[str]) -> str:
    """Validates decided_via source."""
    if not isinstance(value, str) or not value.strip():
        blocking_reasons.append("Missing required field: decided_via")
        return ""
    text = value.strip().lower()
    if text not in ALLOWED_DECIDED_VIA:
        blocking_reasons.append(f"Invalid decided_via: must be one of {ALLOWED_DECIDED_VIA}")
        return ""
    return text


def _normalize_text(value: Any, field: str, blocking_reasons: list[str], max_length: int | None = None, required: bool = False) -> str:
    """Normalizes text field with optional length and requirement constraints."""
    if value in (None, ""):
        if required:
            blocking_reasons.append(f"Missing required field: {field}")
        return ""
    if not isinstance(value, str):
        blocking_reasons.append(f"Invalid {field}: must be string")
        return ""
    text = value.strip()
    if max_length is not None and len(text) > max_length:
        blocking_reasons.append(f"Invalid {field}: length exceeds {max_length}")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        blocking_reasons.append(f"Invalid {field}: control characters not allowed")
    return text


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Builds frontmatter metadata for the verification record."""
    return {
        "record_type": "owner_verification",
        "task_id": record["task_id"],
        "decision": record["decision"],
        "decided_by": record["decided_by"],
        "decided_at": record["decided_at"],
        "decided_via": record["decided_via"],
        "reason": record.get("reason") or None,
    }


def _render_body(record: dict[str, Any]) -> str:
    """Renders markdown body for the verification record."""
    decision_label = "✓ 通过" if record["decision"] == "approve" else "✗ 打回"
    lines = [
        f"# Owner 核验记录: {record['task_id']}",
        "",
        f"**决策**: {decision_label}",
        f"**决策人**: {record['decided_by']}",
        f"**决策时间**: {record['decided_at']}",
        f"**决策方式**: {record['decided_via']}",
        "",
    ]
    
    if record.get("reason"):
        lines.extend([
            "## 理由",
            "",
            record["reason"],
            "",
        ])
    else:
        lines.extend([
            "## 理由",
            "",
            "(无)",
            "",
        ])
    
    lines.extend([
        "---",
        "",
        "本记录为 append-only 核验记录，由 Owner 通过核验站按钮或等效 API 产生。",
        "记录即文件，供 watch 机制自动读取并驱动后续流程。",
    ])
    
    return "\n".join(lines)


def build_owner_verification_record(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    actor: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Builds an owner verification record (approve/reject).
    
    Args:
        repo_root: Repository root path
        payload: Dict with keys: task_id, decision, reason (optional), decided_via
        actor: Optional actor identifier (defaults to "owner")
        dry_run: If True, doesn't write files (default True)
    
    Returns:
        Dict with verdict, blocking_reasons, warnings, target_path, rendered_markdown, etc.
    """
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    
    # Normalize and validate fields
    task_id = _normalize_task_id(payload.get("task_id"), blocking_reasons)
    decision = _normalize_decision(payload.get("decision"), blocking_reasons)
    decided_via = _normalize_decided_via(payload.get("decided_via"), blocking_reasons)
    reason = _normalize_text(payload.get("reason"), "reason", blocking_reasons, max_length=2000, required=False)
    decided_by = _normalize_text(actor or "owner", "decided_by", blocking_reasons, max_length=160, required=True)
    decided_at = _utc_now()
    
    # reject decision requires reason
    if decision == "reject" and not reason:
        blocking_reasons.append("reject decision requires a reason")
    
    # Build normalized record
    normalized_record = {
        "task_id": task_id,
        "decision": decision,
        "reason": reason or None,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "decided_via": decided_via,
    }
    
    # Generate target path with timestamp for uniqueness (append-only)
    # Format: 5_tasks/records/owner_verifications/<task_id>/verify_<task_id>_<timestamp>.md
    timestamp_slug = decided_at.replace(":", "").replace("-", "").replace("Z", "")
    filename = f"verify_{task_id}_{timestamp_slug}.md"
    target_path = str(OWNER_VERIFICATIONS_DIR / task_id / filename) if task_id else None
    target_file = repo_root / target_path if target_path else None
    
    # Check if file already exists (should be rare due to timestamp)
    if target_file is not None and target_file.exists():
        warnings.append(f"Verification record already exists (timestamp collision): {target_path}")
    
    # Render markdown
    rendered_markdown = render_markdown(_metadata(normalized_record), _render_body(normalized_record))
    
    # Build planned writes
    planned_writes = []
    if target_path:
        planned_writes.append({
            "path": target_path,
            "kind": "create",
            "type": "record_markdown",
            "record_type": "owner_verification",
        })
    
    verdict = "BLOCK" if blocking_reasons else ("WARN" if warnings else "PASS")
    
    result: dict[str, Any] = {
        "action": "owner_verification_record",
        "dry_run": dry_run,
        "task_id": task_id or None,
        "decision": decision or None,
        "target_path": target_path,
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "planned_writes": planned_writes,
        "would_write": verdict != "BLOCK" and bool(target_path),
        "rendered_markdown": rendered_markdown,
        "original_payload": normalized_record,
    }
    
    # Write file if not dry_run and no blocking reasons
    if not dry_run:
        if verdict == "BLOCK" or target_file is None:
            result["wrote"] = False
            return result
        
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(rendered_markdown, encoding="utf-8")
        result["wrote"] = True
    
    return result
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
