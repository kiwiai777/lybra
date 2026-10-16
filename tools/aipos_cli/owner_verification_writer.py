"""AIPOS-273: Owner verification record writer (approve/reject).

Writes owner verification records to ``5_tasks/records/owner_verifications/<task_id>/``
in append-only fashion. Each record is timestamped and captures the Owner's
decision (approve/reject) with optional reason. Records are consumed by the
watch mechanism and inform the true-stage derivation.

Record structure mirrors existing record types (returns/verdicts) — frontmatter
+ markdown body. Append-only: never modifies existing records; repeats allowed
(last record wins for UI display, but all records preserved for audit trail).

AIPOS-F175 item 3: the same record type / writer carries the Owner spot check of a
roadmap acceptance criterion (``lybra roadmap verify``). Optional fields
``workspace`` / ``milestone`` / ``criterion`` / ``result`` / ``relayed_by`` /
``owner_words`` / ``note`` / ``fix_task_id`` (attribution consistent with
``lybra owner decide``: decided_by = actor, relayed_by = relaying advisor,
owner_words = the Owner's own words). When ``criterion`` is given, ``result``,
``relayed_by`` and ``owner_words`` are required. Old records without them keep
their meaning. A target file that already exists is refused (append-only: never
overwrite).
"""
from __future__ import annotations

import re
from tools.aipos_cli.clock import file_slug, iso_z
from pathlib import Path
from typing import Any

from tools.aipos_cli.record_writer import render_markdown
from tools.schema_constants import RecordType, Verdict




TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
ALLOWED_DECISIONS = {"approve", "reject"}
ALLOWED_DECIDED_VIA = {"web_session", "cli", "mcp", "external"}
#: AIPOS-F175: spot-check result -> decision (the result vocabulary is declared in
#: verbs.schema lybra_roadmap.verify.results; this map is checked against it by the caller)
RESULT_DECISIONS = {"confirm": "approve", "mismatch": "reject"}
#: AIPOS-F175: optional criterion spot-check fields (frontmatter order)
CRITERION_FIELDS = ("workspace", "milestone", "criterion", "result", "relayed_by", "owner_words", "note", "fix_task_id")


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
    meta = {
        "record_type": RecordType.OWNER_VERIFICATION,
        "task_id": record["task_id"],
        "decision": record["decision"],
        "decided_by": record["decided_by"],
        "decided_at": record["decided_at"],
        "decided_via": record["decided_via"],
        "reason": record.get("reason") or None,
    }
    # AIPOS-F175: criterion spot-check fields only when given (old record shape unchanged)
    for key in CRITERION_FIELDS:
        if record.get(key):
            meta[key] = record[key]
    return meta


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
    if record.get("criterion"):
        # AIPOS-F175: roadmap criterion spot check (who relayed, the Owner's words)
        lines[2:2] = [
            f"**验收标准**: {record['criterion']}(里程碑 {record.get('milestone') or '?'}, 项目 {record.get('workspace') or '?'})",
            f"**抽查结论**: {record.get('result')}",
            f"**转达**: {record.get('relayed_by')}",
            f"**Owner 原话**: 「{record.get('owner_words')}」",
        ]
        if record.get("fix_task_id"):
            lines[6:6] = [f"**修复卡**: {record['fix_task_id']}"]
    
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
    decided_at = iso_z()
    # AIPOS-F175: criterion spot-check fields (single-line; criterion given = result / relayed_by / owner_words required)
    extra: dict[str, str] = {}
    spot_check = bool(payload.get("criterion"))
    for key in CRITERION_FIELDS:
        required = spot_check and key in ("criterion", "result", "relayed_by", "owner_words")
        text = _normalize_text(payload.get(key), key, blocking_reasons, max_length=2000 if key in ("owner_words", "note") else 200,
                               required=required)
        if text and key != "note" and "\n" in text:
            blocking_reasons.append(f"Invalid {key}: must be a single line")
        if text:
            extra[key] = text
    if spot_check and extra.get("result") and RESULT_DECISIONS.get(extra["result"]) != decision:
        blocking_reasons.append(f"Invalid result: {extra.get('result')!r} does not match decision {decision!r} ({RESULT_DECISIONS})")
    
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
        **extra,
    }
    
    # Generate target path with timestamp for uniqueness (append-only)
    # Format: 5_tasks/records/owner_verifications/<task_id>/verify_<task_id>_<timestamp>.md
    timestamp_slug = file_slug("compact_t", decided_at)
    filename = f"verify_{task_id}_{timestamp_slug}.md"
    # AIPOS-F109 件①: 落点 = transitions.schema record_locations.kinds.owner_verifications(record_root; 原模块常量写死退役)
    from tools.aipos_cli.record_writer import record_root

    target_path = str(record_root("owner_verifications") / task_id / filename) if task_id else None
    target_file = repo_root / target_path if target_path else None
    
    # AIPOS-F175: append-only = never overwrite; a timestamp collision is refused (retry in the next second)
    if target_file is not None and target_file.exists():
        blocking_reasons.append(f"Verification record already exists (timestamp collision, append-only, not overwritten): {target_path}")
    
    # Render markdown
    rendered_markdown = render_markdown(_metadata(normalized_record), _render_body(normalized_record))
    
    # Build planned writes
    planned_writes = []
    if target_path:
        planned_writes.append({
            "path": target_path,
            "kind": "create",
            "type": "record_markdown",
            "record_type": RecordType.OWNER_VERIFICATION,
        })
    
    verdict = Verdict.BLOCK if blocking_reasons else (Verdict.WARN if warnings else Verdict.PASS)
    
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
        "would_write": verdict != Verdict.BLOCK and bool(target_path),
        "rendered_markdown": rendered_markdown,
        "original_payload": normalized_record,
    }
    
    # Write file if not dry_run and no blocking reasons
    if not dry_run:
        if verdict == Verdict.BLOCK or target_file is None:
            result["wrote"] = False
            return result
        
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(rendered_markdown, encoding="utf-8")
        result["wrote"] = True
    
    return result
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
