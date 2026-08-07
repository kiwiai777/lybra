"""AIPOS-336 S1 — Bench audit record writer (审结提交).

Writes bench audit conclusion records to
``5_tasks/records/bench_audit/<task_id>/bench_<task_id>_<timestamp>.md``
in append-only fashion. Mirrors the existing record writers
(returns/verdicts/owner_verifications) so the watch mechanism and the
verification station read it the same way.

This is the "结论同样落工作区记录,与 audit_verdict 同级可查" deliverable (card S1):
非代码不等于无据可依 —— the bench conclusion is a durable workspace record,
queryable alongside audit_verdict records.

Two-stage (advisor note #5):
  - build_bench_audit_record(dry_run=True) → preview + planned_writes (no write)
  - build_bench_audit_record(dry_run=False) → writes the file
The dry_run token persistence (351) is handled by the gate's controlled_execute
machinery; this writer is the deterministic builder both stages call.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.bench_evidence import run_ring2_checks, checklist_human_summary
from tools.aipos_cli.record_writer import render_markdown


BENCH_AUDIT_DIR = Path("5_tasks/records/bench_audit")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
ALLOWED_CONCLUSIONS = {"pass", "pass_with_notes", "fail", "needs_human"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_task_id(value: Any, blocking_reasons: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blocking_reasons.append("Missing required field: task_id")
        return ""
    text = value.strip()
    if not TASK_ID_PATTERN.fullmatch(text):
        blocking_reasons.append("Invalid task_id format")
        return ""
    return text


def _normalize_text(value: Any, field: str, blocking_reasons: list[str], *, required: bool = False, max_length: int | None = None) -> str:
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
    return text


def _normalize_conclusion(value: Any, blocking_reasons: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blocking_reasons.append("Missing required field: conclusion")
        return ""
    text = value.strip().lower()
    if text not in ALLOWED_CONCLUSIONS:
        blocking_reasons.append(f"Invalid conclusion: must be one of {sorted(ALLOWED_CONCLUSIONS)}")
        return ""
    return text


def _normalize_evidence_refs(value: Any, blocking_reasons: list[str]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        blocking_reasons.append("Invalid evidence_refs: must be a list")
        return []
    refs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            blocking_reasons.append("Invalid evidence_refs entry: must be an object")
            continue
        cid = str(item.get("check_id") or "").strip()
        ref = str(item.get("ref") or "").strip()
        if not cid:
            blocking_reasons.append("Invalid evidence_refs entry: missing check_id")
            continue
        refs.append({"check_id": cid, "ref": ref, "note": str(item.get("note") or "").strip() or None})
    return refs


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_type": "bench_audit",
        "task_id": record["task_id"],
        "evidence_type": record.get("evidence_type") or None,
        "conclusion": record["conclusion"],
        "submitted_by": record.get("submitted_by") or None,
        "submitted_at": record["submitted_at"],
        "confirmed_by": record.get("confirmed_by") or None,
        "confirmation_ref": record.get("confirmation_ref") or None,
        "ring2_summary": record.get("ring2_summary") or None,
    }


def _render_body(record: dict[str, Any]) -> str:
    checklist = record.get("checklist") or {}
    conclusion_label = {
        "pass": "✓ 通过",
        "pass_with_notes": "✓ 通过(附注)",
        "fail": "✗ 不通过",
        "needs_human": "⧗ 需人判",
    }.get(record["conclusion"], record["conclusion"])
    lines = [
        f"# Bench 审计记录: {record['task_id']}",
        "",
        f"**结论**: {conclusion_label}",
        f"**证据类型**: {record.get('label') or record.get('evidence_type') or '(未指定)'}",
        f"**提交人**: {record.get('submitted_by') or '(未记录)'}",
        f"**提交时间**: {record['submitted_at']}",
        f"**审结人**: {record.get('confirmed_by') or '(未记录)'}",
        f"**分层结论**: {record.get('ring2_summary') or '(无)'}",
        "",
    ]
    # ring2 evidence checklist
    checks = checklist.get("checks") or []
    if checks:
        lines += ["## 证据清单(ring2)", ""]
        for c in checks:
            status_mark = {"pass": "✅", "fail": "❌", "missing": "⚠️", "needs_human": "🧑"}.get(c.get("status"), "·")
            ref_str = f" → `{c.get('ref')}`" if c.get("ref") else ""
            lines.append(f"- {status_mark} **{c.get('label')}** [{c.get('status')}]{ref_str}")
            if c.get("detail"):
                lines.append(f"  - {c['detail']}")
        lines.append("")
    # missing items (clearly listed — acceptance #3)
    missing = checklist.get("missing_items") or []
    if missing:
        lines += ["## 缺项(需补证据)", ""]
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")
    # ring3 human-judgment items (acceptance #7: 需人判, 不自动判定)
    ring3 = checklist.get("ring3_human") or []
    if ring3:
        lines += ["## 需人判(ring3 Owner 眼验)", ""]
        for item in ring3:
            lines.append(f"- {item}")
        lines.append("")
    notes = record.get("notes")
    if notes:
        lines += ["## 附注", "", notes, ""]
    lines += [
        "---",
        "",
        "本记录为 bench 审计结论,append-only 落工作区(`5_tasks/records/bench_audit/`)。",
        "与 audit_verdict 同级可查;非代码走台不等于无据可依。",
        "Owner 眼验(ring3)结论另行落 `5_tasks/records/owner_verifications/`。",
    ]
    return "\n".join(lines)


def build_bench_audit_record(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    actor: str | None = None,
    confirmer: str | None = None,
    confirmation_ref: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build a bench audit conclusion record (审结提交).

    Runs the ring2 evidence checklist (data-driven, same source as the card
    template and the verification station) and renders the record markdown.

    Args:
        repo_root: workspace root.
        payload: {task_id, evidence_type?, task_mode?, conclusion, evidence_refs?, notes?}
        actor: who submits (executor/advisor).
        confirmer: who confirms/审结 (advisor/owner).
        confirmation_ref: dry_run token ref (provenance).
        dry_run: if True, no file write (preview).

    Returns a dict with verdict/blocking_reasons/warnings/target_path/
    rendered_markdown/checklist/planned_writes/wrote.
    """
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    task_id = _normalize_task_id(payload.get("task_id"), blocking_reasons)
    conclusion = _normalize_conclusion(payload.get("conclusion"), blocking_reasons)
    evidence_type = str(payload.get("evidence_type") or "").strip().lower() or None
    task_mode = str(payload.get("task_mode") or "").strip().lower() or None
    notes = _normalize_text(payload.get("notes"), "notes", blocking_reasons, max_length=4000)
    submitted_by = _normalize_text(actor or "executor", "submitted_by", blocking_reasons, max_length=160, required=True)
    submitted_at = _utc_now()
    evidence_refs = _normalize_evidence_refs(payload.get("evidence_refs"), blocking_reasons)

    # Run the ring2 checklist (data-driven). If evidence type is unresolvable AND
    # task_mode doesn't map, warn (not block) — the record can still land with an
    # empty checklist, but we surface that no auto-checks ran.
    checklist = run_ring2_checks(
        repo_root,
        evidence_type=evidence_type,
        task_mode=task_mode,
        evidence_refs=evidence_refs,
    )
    if not checklist.get("resolved"):
        warnings.append(f"证据类型未解析(evidence_type={evidence_type!r}, task_mode={task_mode!r});未跑自动检查。")

    ring2_summary = checklist_human_summary(checklist)

    record = {
        "task_id": task_id,
        "evidence_type": evidence_type or checklist.get("evidence_type") or None,
        "label": checklist.get("label") or None,
        "conclusion": conclusion,
        "submitted_by": submitted_by,
        "submitted_at": submitted_at,
        "confirmed_by": confirmer,
        "confirmation_ref": confirmation_ref,
        "notes": notes or None,
        "checklist": checklist,
        "ring2_summary": ring2_summary,
    }

    # Path: 5_tasks/records/bench_audit/<task_id>/bench_<task_id>_<timestamp>.md
    timestamp_slug = submitted_at.replace(":", "").replace("-", "").replace("Z", "")
    filename = f"bench_{task_id}_{timestamp_slug}.md" if task_id else None
    target_path = str(BENCH_AUDIT_DIR / task_id / filename) if task_id and filename else None
    target_file = repo_root / target_path if target_path else None

    if target_file is not None and target_file.exists():
        warnings.append(f"Bench audit record may collide (timestamp): {target_path}")

    rendered_markdown = render_markdown(_metadata(record), _render_body(record))

    planned_writes: list[dict[str, Any]] = []
    if target_path:
        planned_writes.append({
            "path": target_path,
            "kind": "create",
            "type": "record_markdown",
            "record_type": "bench_audit",
        })

    verdict = "BLOCK" if blocking_reasons else ("WARN" if warnings else "PASS")

    result: dict[str, Any] = {
        "action": "bench_audit_record",
        "dry_run": dry_run,
        "task_id": task_id or None,
        "evidence_type": record["evidence_type"],
        "conclusion": conclusion or None,
        "target_path": target_path,
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "planned_writes": planned_writes,
        "would_write": verdict != "BLOCK" and bool(target_path),
        "rendered_markdown": rendered_markdown,
        "checklist": checklist,
        "ring2_summary": ring2_summary,
        "original_payload": {
            "task_id": task_id,
            "evidence_type": evidence_type,
            "task_mode": task_mode,
            "conclusion": conclusion,
            "evidence_refs": evidence_refs,
            "notes": notes or None,
        },
    }

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
