"""AIPOS-260: Owner truth summary read surface — records-only additive aggregation.

Pure read-only aggregation over ``load_records`` + ``load_all_tasks``. Derives, per
task, a *true stage* badge and a per-round summary timeline straight from already
recorded truth (publish / claim / return / audit_dispatch / audit_verdict). It also
emits a cross-task activity feed (the "动态流") with a real verb + summary snippet per
record_type, and stage counts that reflect record-derived reality instead of the raw
queue folder.

Red lines honoured:
- gate-not-engine: only already-recorded truth is read; no runtime polling / live tail;
- queue state machine untouched, no files moved, no records rewritten;
- zero new dependencies (stdlib only).

Contract keys this surface exposes (pinned by test_board_adapter_contract.py):
- task row: task_id, title, purpose, path, queue_state, true_stage, stage_label, verdict
- timeline / feed event: record_type, record_id, actor, timestamp, verb, summary, verdict
- return-record event additionally carries: result_summary
- verdict-record event additionally carries: findings_summary
- top-level: record_field_keys lists the records field names this surface depends on.
"""
from __future__ import annotations

from typing import Any

from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks

# True-stage taxonomy (display order). Derived purely from records + queue_state.
TRUE_STAGE_LABELS: dict[str, str] = {
    "published": "已发布",
    "executing": "执行中",
    "delivered": "已交付待审",
    "auditing": "审计中",
    "verdict_pass": "判决 PASS",
    "verdict_fail": "判决 FAIL",
    "closed": "已闭环",
}

TRUE_STAGE_ORDER: list[str] = [
    "published",
    "executing",
    "delivered",
    "auditing",
    "verdict_pass",
    "verdict_fail",
    "closed",
]

# Records field names this read surface depends on (AIPOS-260 S4: pin the keys).
RECORD_FIELD_KEYS: tuple[str, ...] = (
    "record_type",
    "result_summary",
    "findings_summary",
    "verdict",
)


def _extract_summary_field(body: str, labels: tuple[str, ...]) -> str | None:
    """Pull a one-line summary labelled like 'Result summary:' / 'Findings summary:'
    out of a record's markdown body. Returns the trimmed text after the label, or
    None when absent. Only already-recorded text is read."""
    if not body:
        return None
    for raw_line in body.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        for label in labels:
            prefix = label
            if line.lower().startswith(prefix.lower()):
                rest = line[len(prefix):].lstrip(": ").strip()
                if rest:
                    return rest
    return None


def _extract_purpose(body: str) -> str | None:
    """First substantive paragraph of the task card body (after frontmatter) as a
    one-line purpose. Markdown headings and blank lines are skipped."""
    if not body:
        return None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Trim leading list / quote markers for a clean one-liner.
        clean = line.lstrip("-*> ").strip()
        if clean:
            return clean[:200]
    return None


# Per record_type Chinese verb for the activity feed (AIPOS-260 #3: real verb).
RECORD_VERBS: dict[str, str] = {
    "publish": "发布了",
    "claim": "认领了",
    "return": "交付了",
    "audit_dispatch": "派审了",
    "audit_verdict": "判决",
    "owner_decision": "裁定",
    "owner_decision_record": "裁定",
    "session": "开启会话",
}

# Which timestamp metadata field to prefer, per record_type.
_RECORD_TIMESTAMP_FIELDS: dict[str, tuple[str, ...]] = {
    "session": ("created_at", "session_started_at"),
    "publish": ("published_at", "created_at"),
    "claim": ("claimed_at", "created_at"),
    "return": ("returned_at", "created_at"),
    "audit_dispatch": ("dispatched_at", "created_at"),
    "audit_verdict": ("verdict_at", "created_at"),
    "owner_decision_record": ("decided_at", "created_at"),
}


def _record_timestamp(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    record_type = record.get("record_type")
    fields = _RECORD_TIMESTAMP_FIELDS.get(record_type or "", ("created_at",))
    for field in fields:
        value = metadata.get(field)
        if value:
            return str(value)
    # Records already expose convenience fields (published_at, returned_at, ...).
    for field in fields:
        value = record.get(field)
        if value:
            return str(value)
    return ""


def _record_actor(record: dict[str, Any]) -> str | None:
    if record.get("actor"):
        return str(record.get("actor"))
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for field in ("actor", "published_by", "returned_by", "claimed_by", "auditor_instance", "decided_by_ref", "captured_by"):
        value = metadata.get(field)
        if value:
            return str(value)
    return None


def _record_summary(record: dict[str, Any]) -> str | None:
    """Best-effort one-line summary already recorded in the record body.
    For return records this is the result_summary; for verdict records the
    findings_summary; otherwise the first substantive body line."""
    body = record.get("body") or ""
    record_type = record.get("record_type")
    if record_type == "return":
        return _extract_summary_field(body, ("Result summary", "结果摘要"))
    if record_type == "audit_verdict":
        return _extract_summary_field(body, ("Findings summary", "审计结论"))
    if record_type == "owner_decision_record":
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return _extract_summary_field(body, ("Summary", "Decision")) or (
            str(metadata.get("decision_status")) if metadata.get("decision_status") else None
        )
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and not line.startswith("```"):
            return line.lstrip("-*> ").strip()[:200]
    return None


def build_timeline_event(record: dict[str, Any]) -> dict[str, Any]:
    """Project one record into a timeline/feed event with a pinned key set.

    Keys (AIPOS-260 S4 contract): record_type, record_id, actor, timestamp,
    verb, summary, verdict. Return records additionally carry result_summary;
    verdict records additionally carry findings_summary.
    """
    record_type = record.get("record_type") or ""
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    summary = _record_summary(record)
    event: dict[str, Any] = {
        "record_type": record_type,
        "record_id": record.get("record_id") or record.get("record_id"),
        "task_id": record.get("task_id"),
        "actor": _record_actor(record),
        "timestamp": _record_timestamp(record),
        "verb": RECORD_VERBS.get(record_type, record_type or "记录"),
        "summary": summary,
        "verdict": metadata.get("verdict") if record_type == "audit_verdict" else None,
    }
    if record_type == "return":
        # result_summary is the already-recorded return evidence (pinned key).
        event["result_summary"] = summary
    if record_type == "audit_verdict":
        # findings_summary is the already-recorded audit evidence (pinned key).
        event["findings_summary"] = summary
    return event


def derive_true_stage(
    task_id: str | None,
    recs: dict[str, list[dict[str, Any]]],
    queue_state: str | None,
    main_verdict: str | None = None,
) -> str:
    """Derive the record-truth stage badge for one task. Pure display-layer
    derivation: does not touch the queue state machine or move any file.

    Order (terminal-first): closed -> verdict -> auditing -> delivered ->
    executing -> published; falls back to queue_state (pending/blocked) when no
    records exist yet.

    ``main_verdict`` is the verdict recorded against this card's reviewed main
    card (used for audit 'R' execution cards whose verdict is filed under the
    reviewed task_id, not the audit card id)."""
    if queue_state == "completed":
        return "closed"
    upper_id = (task_id or "").upper()
    # A finalize (FZ) execution card that has returned = closed loop.
    if upper_id.endswith("FZ") and recs.get("returns"):
        return "closed"
    # An audit (R) execution card: closed once the reviewed main card is judged.
    if upper_id.endswith("R") and main_verdict:
        return "verdict_pass" if str(main_verdict).upper() == "PASS" else "verdict_fail"
    verdicts = recs.get("audit_verdicts") or []
    if verdicts:
        values = [
            str((v.get("metadata") or {}).get("verdict") or "").strip().upper()
            for v in verdicts
        ]
        if any(v == "FAIL" for v in values):
            return "verdict_fail"
        return "verdict_pass"
    if recs.get("audit_dispatches"):
        return "auditing"
    if recs.get("returns"):
        return "delivered"
    if recs.get("claims"):
        return "executing"
    if recs.get("publishes"):
        return "published"
    # No recorded truth yet: fall back to the queue folder (待认领 / 受阻).
    if queue_state == "blocked":
        return "blocked"
    return "pending"


# Full display labels (true stages + queue fallbacks 待认领/受阻).
STAGE_LABELS_FULL: dict[str, str] = {
    **TRUE_STAGE_LABELS,
    "pending": "待认领",
    "blocked": "受阻",
    "unknown": "未知",
}

_RECORD_KINDS_FOR_TIMELINE: tuple[str, ...] = (
    "publishes",
    "claims",
    "returns",
    "audit_dispatches",
    "audit_verdicts",
)


def _sort_key_timestamp(event: dict[str, Any]) -> str:
    return str(event.get("timestamp") or "")


def build_owner_truth_view(repo_root: str | Any) -> dict[str, Any]:
    """Aggregate the Owner truth summary read surface. Read-only."""
    from pathlib import Path  # local import keeps module top clean

    from tools.aipos_cli.records import find_records_for_task

    resolved = Path(repo_root).resolve() if repo_root else None
    records_report = load_records(resolved) if resolved else load_records()
    tasks = load_all_tasks(resolved) if resolved else load_all_tasks()

    # verdict filed per reviewed main card (newest first in records_report).
    verdict_by_task: dict[str, str] = {}
    for tid, verdicts in (records_report.get("task_audit_verdicts") or {}).items():
        if verdicts:
            value = str((verdicts[0].get("metadata") or {}).get("verdict") or "").strip().upper()
            if value:
                verdict_by_task[str(tid)] = value

    task_rows: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task.get("task_id")
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        recs = find_records_for_task(records_report, task_id) if task_id else {}
        queue_state = task.get("queue_state") or metadata.get("status")
        upper_id = (task_id or "").upper()
        main_verdict = None
        if upper_id.endswith("R") and len(upper_id) > 1:
            main_verdict = verdict_by_task.get(upper_id[:-1])
        true_stage = derive_true_stage(task_id, recs, queue_state, main_verdict)

        timeline_raw: list[dict[str, Any]] = []
        for kind in _RECORD_KINDS_FOR_TIMELINE:
            for record in recs.get(kind, []):
                timeline_raw.append(build_timeline_event(record))
        timeline_raw.sort(key=_sort_key_timestamp)  # earliest -> latest

        verdict_value = None
        for verdict_record in recs.get("audit_verdicts", []):
            value = (verdict_record.get("metadata") or {}).get("verdict")
            if value:
                verdict_value = value
                break

        task_rows.append(
            {
                "task_id": task_id,
                "title": task.get("title") or metadata.get("title"),
                "purpose": _extract_purpose(task.get("body") or ""),
                "path": task.get("path"),
                "queue_state": queue_state,
                "true_stage": true_stage,
                "stage_label": STAGE_LABELS_FULL.get(true_stage, true_stage),
                "verdict": verdict_value,
                "timeline": timeline_raw,
            }
        )

    # Stable ordering: true-stage order, then task_id, so the board reads top-down.
    stage_rank = {stage: idx for idx, stage in enumerate(TRUE_STAGE_ORDER)}
    task_rows.sort(
        key=lambda row: (stage_rank.get(row["true_stage"], 99), str(row.get("task_id") or ""))
    )

    stage_counts: dict[str, int] = {}
    for row in task_rows:
        stage = str(row["true_stage"] or "unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    # Cross-task activity feed: every record, newest first.
    activity_feed: list[dict[str, Any]] = []
    for kind in _RECORD_KINDS_FOR_TIMELINE:
        for record in records_report.get(kind, []):
            activity_feed.append(build_timeline_event(record))
    for record in records_report.get("owner_decisions", []):
        activity_feed.append(build_timeline_event(record))
    activity_feed.sort(key=_sort_key_timestamp, reverse=True)

    return {
        "ok": True,
        "operation": "get_owner_truth_view",
        "dry_run": False,
        "actor": None,
        "data": {
            "tasks": task_rows,
            "stage_counts": stage_counts,
            "stage_labels": STAGE_LABELS_FULL,
            "true_stage_order": list(TRUE_STAGE_ORDER),
            "activity_feed": activity_feed,
            "record_field_keys": list(RECORD_FIELD_KEYS),
            "records_summary": records_report.get("summary"),
        },
        "summary": {
            "total_tasks": len(task_rows),
            "stage_counts": stage_counts,
            "activity_events": len(activity_feed),
        },
        "warnings": [],
        "errors": [],
        "safety_notice": (
            "Read-only Owner truth summary surface. Stages are derived from already "
            "recorded truth (records + queue_state); the queue state machine is not "
            "mutated and no records are rewritten."
        ),
    }
