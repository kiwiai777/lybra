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
_CHECKLIST_HEADINGS = ("owner 核验单", "owner核验单", "核验单")


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


def _extract_owner_verify_checklist(body: str) -> list[str]:
    """AIPOS-274: Extract human-readable checklist from '## Owner 核验单' section.
    
    Looks for section heading variants (Owner 核验单/Owner核验单/核验单), returns
    numbered/bullet list items or non-blank lines. Fallback兼容:if no such section,
    returns empty list (caller uses acceptance_assertions as fallback).
    """
    lines = (_as_str(body)).splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip().lower()
        if stripped and any(stripped.startswith(h) for h in _CHECKLIST_HEADINGS):
            start = idx
            break
    if start is None:
        return []
    section: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("##"):
            break
        section.append(line)
    # Extract numbered (1. 2. ...) or bulleted (- * + ...) list items
    items = []
    for line in section:
        stripped = line.strip()
        if not stripped:
            continue
        # Match numbered list: "1. text" or "1) text"
        import re
        num_match = re.match(r'^\d+[.)\s]\s*(.+)$', stripped)
        if num_match:
            items.append(num_match.group(1).strip())
            continue
        # Match bullet list: "- text" or "* text" or "+ text"
        if stripped.startswith(("-", "*", "+")) and len(stripped) > 1:
            items.append(stripped[1:].strip())
            continue
        # Non-list line: include if not a heading or horizontal rule
        if not stripped.startswith("#") and stripped not in ("---", "***", "___"):
            items.append(stripped)
    return [item for item in items if item]


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
    """Return the latest (newest) record. Records are sorted newest-first by load_records."""
    if not records:
        return None
    return records[0]


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
            # AIPOS-287: audit 字段读取（判断免审计卡）
            audit_policy = _as_str(metadata.get("audit")).lower()
            # AIPOS-274: owner_verify_checklist 字段优先,退而从正文解析 Owner 核验单,再退而用验收断言
            checklist_meta = metadata.get("owner_verify_checklist")
            checklist = (
                checklist_meta if isinstance(checklist_meta, list) and checklist_meta
                else _extract_owner_verify_checklist(task.get("body") or "")
            )
            assertions = _extract_acceptance_assertions(task.get("body") or "")
            # 人话清单为主,断言为备退(前端优先显示 checklist,断言折叠)
            if not checklist and assertions:
                checklist = assertions
            preview_route = _as_str(metadata.get("owner_verify_preview") or "")
            base = {
                "task_id": tid,
                "title": _as_str(task.get("title") or metadata.get("title")),
                "path": task.get("path"),
                "queue_state": queue_state,
                "true_stage": stage,
                "audit_policy": audit_policy,
                "owner_verify_checklist": checklist,
                "acceptance_assertions": assertions,
                "owner_verify_preview": preview_route if preview_route else None,
            }
            # F-262B-4: 闭环即退站 — a verdict-PASS main card whose closure unit
            # already finalized (FZ returned / 收编) is no longer 待验; exclude it
            # from the station (and from previewable — it is done, not in flight).
            # AIPOS-274F1: 已核验即退站 — an owner_verification record with
            # decision=approve filed against this main card means the Owner has
            # already looked at it; it must not reappear on 待验站 while it waits
            # for FZ to catch up (263 复活 bug: FZ was still pending/未收编,
            # closure_unit_finalized() alone missed the earlier approve record).
            owner_approved = any(
                _as_str((v.get("metadata") or {}).get("decision")).lower() == "approve"
                for v in recs.get("owner_verifications", [])
            )
            # AIPOS-287: 站位推导扩展 — audit:none 且有 return 记录也起站
            # 已核验即退站（owner_approved 或闭环完成）
            if (
                stage == "verdict_pass"
                and (owner_approved or _closure_unit_finalized(root, members_by_root, records))
            ):
                closed_excluded.append({"task_id": tid, "title": base["title"]})
                continue
            # AIPOS-287: audit:none 且已 return 且已核验也要退站
            if (
                audit_policy == "none"
                and stage == "delivered"
                and (owner_approved or _closure_unit_finalized(root, members_by_root, records))
            ):
                closed_excluded.append({"task_id": tid, "title": base["title"]})
                continue
            # 站位条件：verdict_pass 或 (audit:none 且 delivered)
            if stage == "verdict_pass" or (audit_policy == "none" and stage == "delivered"):
                # 待验站: audit PASS (或 audit:none 已返回), awaiting Owner真人核验 + finalize.
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
            # AIPOS-274: 既无人话清单也无断言时才警告
            if not checklist and not assertions:
                warnings.append(f"{tid}: no owner_verify_checklist or 验收断言 found.")

        stations.sort(key=lambda s: str(s.get("task_id") or ""))
        previewable.sort(key=lambda s: str(s.get("task_id") or ""))

        # AIPOS-274F2: mirror summary into data.summary so frontend can read
        # either response.summary or response.data.summary.
        summary = {
            "stations": len(stations),
            "previewable": len(previewable),
            "closed_excluded": len(closed_excluded),
            "owner_verify_total": len(stations) + len(previewable) + len(closed_excluded),
        }
        data = {
            "stations": stations,
            "previewable": previewable,
            "closed_excluded": closed_excluded,
            "writes_enabled": True,
            "resolution_enabled": True,
            "resolution_note": "AIPOS-273:通过/打回按钮已接真,写入 owner 核验记录到文件系统(append-only)。",
            # AIPOS-274F2: summary mirrored inside data for frontend alignment.
            "summary": summary,
        }
        return make_response(
            ok=True,
            verdict=Verdict.WARN if warnings else Verdict.PASS,
            operation=operation,
            dry_run=False,
            data=data,
            summary=summary,
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
            verdict=Verdict.BLOCK,
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
        verdict=Verdict.PASS,
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
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
from tools.schema_constants import Verdict
check_direct_invocation(__name__)
