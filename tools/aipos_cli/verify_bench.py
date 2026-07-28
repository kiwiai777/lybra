"""AIPOS-262B: Owner verification bench read surface (read-only).

Surfaces ``owner_verify: required`` cards as a verification bench on the
workspace page:

- **stations (待验)**: a main card whose audit has recorded PASS but is not yet
  finalized/closed — the Owner must真人核验 then finalize (S6). Each station
  carries the acceptance assertions (original text) + three rings of evidence:
    1. ``machine_judgment`` — the executor's return record (self-reported tests)
    2. ``audit_verdict``    — the auditor's verdict + findings excerpt
    3. ``prior_fixes``      — prior-round FIX cards in the same closure unit
- **previewable (进行中)**: an owner_verify card still in flight — the Owner can
  see "它将被怎么验" (the acceptance criteria) ahead of the verdict.

Read-only everywhere. Pass/reject buttons are intentionally NOT wired (deferred
to candidate ⑬, pending board auth) — ``resolution_note`` says so.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import make_response
from tools.aipos_cli.owner_truth_view import (
    _derive_closure_root,
    _member_kind,
    derive_true_stage,
)
from tools.aipos_cli.records import find_records_for_task, load_records
from tools.aipos_cli.task_loader import load_all_tasks

READ_SAFETY_NOTICE = "Read-only local Board adapter call. No files are written."

_EXCERPT_CHARS = 320
_ASSERTION_HEADINGS = ("验收断言", "acceptance", "验收标准")


def _as_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _excerpt(body: str, limit: int = _EXCERPT_CHARS) -> str:
    text = _as_str(body)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _section_excerpt(body: str, heading: str, limit: int = _EXCERPT_CHARS) -> str:
    """Excerpt a named ``## <heading>`` section, skipping the H1 title line."""
    lines = (_as_str(body)).splitlines()
    start: int | None = None
    target = heading.strip().lower()
    for idx, line in enumerate(lines):
        if line.strip().startswith("##") and line.strip().lstrip("#").strip().lower() == target:
            start = idx + 1
            break
    if start is None:
        # Fall back to the first non-H1 content line.
        start = 0
        for idx, line in enumerate(lines):
            if line.strip() and not line.strip().startswith("#"):
                start = idx
                break
    collected: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("##"):
            break
        collected.append(line)
    return _excerpt("\n".join(collected).strip(), limit)


def _extract_acceptance_assertions(body: str) -> list[str]:
    """Pull the ``## 验收断言`` (or Acceptance) bullet list from a card body.

    Returns the assertion lines verbatim (Owner reads the ORIGINAL wording — no
    paraphrase). Falls back to the section's raw text if it is not a bullet list.
    """
    lines = (_as_str(body)).splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip().lower()
        if stripped and any(stripped.startswith(h) for h in _ASSERTION_HEADINGS):
            start = idx
            break
    if start is None:
        return []
    section: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("##"):
            break
        section.append(line)
    bullets = [
        line.strip().lstrip("-").strip()
        for line in section
        if line.strip().startswith("-")
    ]
    if bullets:
        return [b for b in bullets if b]
    # Non-bulleted section: return non-blank lines.
    return [line.strip() for line in section if line.strip()]


def _latest(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    return records[-1]


def _machine_judgment(returns: list[dict[str, Any]]) -> dict[str, Any]:
    record = _latest(returns)
    if not record:
        return {"present": False}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return {
        "present": True,
        "record_id": record.get("return_id") or metadata.get("return_id"),
        "actor": _as_str(record.get("actor") or metadata.get("returned_by")),
        "returned_at": _as_str(record.get("returned_at") or metadata.get("returned_at")),
        "executor_status": _as_str(record.get("executor_status") or metadata.get("executor_status")),
        "audit_readiness": _as_str(record.get("audit_readiness") or metadata.get("audit_readiness")),
        "summary_excerpt": _section_excerpt(record.get("body"), "Summary"),
    }


def _audit_verdict(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    record = _latest(verdicts)
    if not record:
        return {"present": False}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return {
        "present": True,
        "record_id": record.get("verdict_id") or metadata.get("verdict_id"),
        "verdict": _as_str(record.get("verdict") or metadata.get("verdict")),
        "auditor_instance": _as_str(record.get("auditor_instance") or metadata.get("auditor_instance")),
        "verdict_at": _as_str(record.get("verdict_at") or metadata.get("verdict_at")),
        "findings_excerpt": _section_excerpt(record.get("body"), "Summary"),
    }


def _closure_unit_finalized(
    root: str,
    members_by_root: dict[str, list[dict[str, Any]]],
    records: dict[str, Any],
) -> bool:
    """F-262B-4: True when the closure unit has a finalize (FZ) member that returned.

    Mirrors ``derive_true_stage``'s "FZ returned = closed loop" rule, applied at
    the *unit* level so a main card knows its own finalize completed (the
    per-card rule only fires for the FZ card itself). 260/261/265 are finalized
    in the product repo + carry FZ return records, yet sit in ``claimed/`` with
    no ``completed/<ID>/`` dossier — so ``derive_true_stage`` leaves them at
    ``verdict_pass`` and they wrongly hang on the 待验站. 闭环即退站.
    """
    for member in members_by_root.get(str(root), []):
        m_meta = member.get("metadata") if isinstance(member.get("metadata"), dict) else {}
        m_tid = member.get("task_id")
        if _member_kind(m_tid, m_meta, root) != "finalize":
            continue
        m_recs = find_records_for_task(records, m_tid) if m_tid else {}
        if m_recs.get("returns"):
            return True
    return False


def _prior_fixes(fix_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": t.get("task_id"),
            "title": _as_str(t.get("title")),
            "queue_state": t.get("queue_state"),
        }
        for t in fix_tasks
    ]


def get_verify_bench(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Read-only Owner verification bench. Never raises."""
    operation = "get_verify_bench"
    try:
        resolved = Path(repo_root).resolve() if repo_root is not None else None
        if resolved is None:
            return _empty(operation)
        tasks = load_all_tasks(resolved)
        records = load_records(resolved)
        completed_root = resolved / "5_tasks" / "queue" / "completed"

        # verdict filed per reviewed main card (newest first in records).
        verdict_by_task: dict[str, str] = {}
        for tid, verdicts in (records.get("task_audit_verdicts") or {}).items():
            if verdicts:
                value = _as_str((verdicts[0].get("metadata") or {}).get("verdict")).upper()
                if value:
                    verdict_by_task[str(tid)] = value

        # closure grouping: root -> member tasks (for prior_fixes lookup).
        members_by_root: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            tid = task.get("task_id")
            root = _derive_closure_root(tid, metadata)
            members_by_root.setdefault(str(root), []).append(task)

        stations: list[dict[str, Any]] = []
        previewable: list[dict[str, Any]] = []
        closed_excluded: list[dict[str, Any]] = []
        warnings: list[str] = []

        for task in tasks:
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            if _as_str(metadata.get("owner_verify")).lower() != "required":
                continue
            tid = task.get("task_id")
            root = _derive_closure_root(tid, metadata)
            kind = _member_kind(tid, metadata, root)
            if kind != "main":
                continue
            recs = find_records_for_task(records, tid) if tid else {}
            queue_state = task.get("queue_state") or metadata.get("status")
            main_verdict = verdict_by_task.get(str(root))
            completed_dossier = bool(tid and (completed_root / str(tid)).is_dir())
            stage = derive_true_stage(tid, recs, queue_state, main_verdict, completed_dossier)
            assertions = _extract_acceptance_assertions(task.get("body") or "")
            base = {
                "task_id": tid,
                "title": _as_str(task.get("title") or metadata.get("title")),
                "path": task.get("path"),
                "queue_state": queue_state,
                "true_stage": stage,
                "acceptance_assertions": assertions,
            }
            # F-262B-4: 闭环即退站 — a verdict-PASS main card whose closure unit
            # already finalized (FZ returned / 收编) is no longer 待验; exclude it
            # from the station (and from previewable — it is done, not in flight).
            if (
                stage == "verdict_pass"
                and _closure_unit_finalized(root, members_by_root, records)
            ):
                closed_excluded.append({"task_id": tid, "title": base["title"]})
                continue
            if stage == "verdict_pass":
                # 待验站: audit PASS, awaiting Owner真人核验 + finalize.
                fix_tasks = [
                    t for t in members_by_root.get(str(root), [])
                    if _member_kind(t.get("task_id"), t.get("metadata") or {}, root) == "fix"
                ]
                stations.append({
                    **base,
                    "evidence": {
                        "machine_judgment": _machine_judgment(recs.get("returns", [])),
                        "audit_verdict": _audit_verdict(recs.get("audit_verdicts", [])),
                        "prior_fixes": _prior_fixes(fix_tasks),
                    },
                })
            else:
                # 进行中: Owner can preview how it will be verified.
                previewable.append({**base, "stage_note": _stage_note(stage)})
            if not assertions:
                warnings.append(f"{tid}: no 验收断言 section found in card body.")

        stations.sort(key=lambda s: str(s.get("task_id") or ""))
        previewable.sort(key=lambda s: str(s.get("task_id") or ""))

        data = {
            "stations": stations,
            "previewable": previewable,
            "closed_excluded": closed_excluded,
            "writes_enabled": False,
            "resolution_enabled": False,
            "resolution_note": "通过/打回按键留候选⑬(board 鉴权后);当前为只读核验面。",
        }
        return make_response(
            ok=True,
            verdict="WARN" if warnings else "PASS",
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "stations": len(stations),
                "previewable": len(previewable),
                "closed_excluded": len(closed_excluded),
                "owner_verify_total": len(stations) + len(previewable) + len(closed_excluded),
            },
            warnings=warnings,
            blocking_reasons=[],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return make_response(
            ok=False,
            verdict="BLOCK",
            operation=operation,
            dry_run=False,
            data={"stations": [], "previewable": [], "closed_excluded": [], "writes_enabled": False},
            summary={"stations": 0, "previewable": 0, "closed_excluded": 0, "owner_verify_total": 0},
            warnings=[],
            blocking_reasons=[str(exc) or exc.__class__.__name__],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[{"category": "INTERNAL_ERROR", "message": str(exc) or exc.__class__.__name__}],
        )


def _stage_note(stage: str | None) -> str:
    notes = {
        "executing": "执行中——尚未提交返回,验收标准预览。",
        "delivered": "已返回——等待审计判决。",
        "auditing": "审计进行中——等待判决。",
        "verdict_fail": "上轮判决 FAIL——修复中,重验标准预览。",
        "pending": "待认领——验收标准预览。",
        "blocked": "受阻——验收标准预览。",
    }
    return notes.get(stage or "", "进行中——验收标准预览。")


def _empty(operation: str) -> dict[str, Any]:
    return make_response(
        ok=True,
        verdict="PASS",
        operation=operation,
        dry_run=False,
        data={"stations": [], "previewable": [], "closed_excluded": [], "writes_enabled": False, "resolution_enabled": False},
        summary={"stations": 0, "previewable": 0, "closed_excluded": 0, "owner_verify_total": 0},
        warnings=[],
        blocking_reasons=[],
        needs_owner_reasons=[],
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
        safety_notice=READ_SAFETY_NOTICE,
        errors=[],
    )
