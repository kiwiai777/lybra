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
import re

from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import load_all_tasks
from tools.schema_constants import RecordType, Verdict




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


# AIPOS-261: Chinese role label per record_type (人话化). The raw agent instance
# (e.g. exec.lybra.kiwiai-dev) stays on the event as `actor` for attribution; the
# prominent face shows the human role instead of a bare instance string.
ROLE_LABELS: dict[str, str] = {
    "publish": "Owner",
    "claim": "执行者",
    "return": "执行者",
    "audit_dispatch": "审计员",
    "audit_verdict": "审计员",
    "session": "系统",
    "owner_decision": "Owner",
    "owner_decision_record": "Owner",
}


def _role_for_event(record_type: str | None) -> str:
    return ROLE_LABELS.get(record_type or "", "系统")


def _phrase_for_event(record_type: str | None, metadata: dict[str, Any]) -> str:
    """Full human sentence for one event (人话化). Falls back to verb + record_type."""
    rt = record_type or ""
    if rt == RecordType.PUBLISH:
        return "Owner 发布了任务"
    if rt == RecordType.CLAIM:
        return "执行者领取了任务（信封自动放行）"
    if rt == RecordType.RETURN:
        return "执行者交付了任务"
    if rt == RecordType.AUDIT_DISPATCH:
        return "审计员受理了审计"
    if rt == RecordType.AUDIT_VERDICT:
        verdict = str(metadata.get("verdict") or "").strip().upper() or "待判决"
        return f"审计员判决：{verdict}"
    if rt in ("owner_decision", "owner_decision_record"):
        return "Owner 作出裁定"
    if rt == "session":
        return "系统开启了会话"
    return "记录了事件"


# AIPOS-261: closure-unit genealogy. The root of a card is derived from explicit
# provenance fields first (derived_from / reviewed_task_id), then from the ID suffix
# convention (R = audit, F<digits> = fix round, FZ = finalize, -FIX<digits> = fix).
_SUFFIX_RE = re.compile(r"(?P<root>.+?)(?P<suffix>R|F\d+|FZ|-FIX\d+)$")
_FIX_SUFFIX_RE = re.compile(r"F\d+$|-FIX\d+$")


def _derive_closure_root(task_id: str | None, metadata: dict[str, Any]) -> str:
    """Resolve the closure-unit root task_id for one card. Explicit provenance wins;
    ID-suffix convention is the fallback (F/FZ/R cards have no derived_from)."""
    tid = str(task_id or "").strip()
    if not tid:
        return ""
    derived = str(metadata.get("derived_from") or "").strip()
    if derived:
        return derived
    reviewed = str(metadata.get("reviewed_task_id") or "").strip()
    if reviewed and reviewed != tid:
        return reviewed
    m = _SUFFIX_RE.match(tid)
    if m and m.group("suffix"):
        return m.group("root")
    return tid


def _member_kind(task_id: str | None, metadata: dict[str, Any], root: str) -> str:
    """Classify a card within its closure unit: main / audit / fix / finalize."""
    tid = str(task_id or "").strip()
    if not tid:
        return "main"
    if str(metadata.get("task_mode") or "").strip() == "audit" or tid.endswith("R"):
        return "audit"
    if tid.endswith("FZ"):
        return "finalize"
    if _FIX_SUFFIX_RE.search(tid):
        return "fix"
    return "main"


# AIPOS-261 FIX-1 (人话化): shared markdown-stripper for the *display layer*. Only the
# derived display string is cleaned — the recorded source file is never rewritten.
_MARKDOWN_CODE_SPAN = re.compile(r"`{1,3}([^`]*?)`{1,3}")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LEADING_MARKER = re.compile(r"^\s*(?:#+\s*|>\s*|[-*+]\s*|\d+[.)]\s*)+")


def _strip_markdown_inline(text: str) -> str:
    """Strip markdown noise into a plain display string: inline code backticks
    (`` `x` `` -> x), link URLs (label kept), bold/italic ``*`` markers, stray
    heading ``#``, and leading list/quote/numbering markers. ``_`` is left intact
    so snake_case identifiers survive. Read-only on the string."""
    if not text:
        return ""
    s = _MARKDOWN_CODE_SPAN.sub(r"\1", text)
    s = _MARKDOWN_LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("*", "")
    s = s.replace("#", "")
    s = _LEADING_MARKER.sub("", s)
    return s.strip()


# FIX-1: a one-line purpose is capped at 60 字 (characters); longer -> ellipsis.
PURPOSE_MAX_CHARS = 60
_SENTENCE_END = re.compile(r"[。!?;!?]")


def _first_sentence(text: str) -> str:
    """Text up to the first sentence terminator (中/英); the terminator is dropped.
    No terminator -> the whole cleaned text."""
    if not text:
        return ""
    m = _SENTENCE_END.search(text)
    return (text[: m.start()] if m else text).strip()


def _clip_purpose(text: str) -> str:
    """Truncate a purpose line to PURPOSE_MAX_CHARS, appending an ellipsis when cut."""
    text = (text or "").strip()
    if len(text) <= PURPOSE_MAX_CHARS:
        return text
    return text[:PURPOSE_MAX_CHARS].rstrip() + "…"


def _title_colon_suffix(title: str | None) -> str:
    """Short phrase after a title's colon when the prefix is a short tag, e.g.
    'FIX-1 打回轮:任务摘要再通俗化' -> '任务摘要再通俗化'. A colon buried in a long
    clause is ignored — only a top-level tag separator counts."""
    if not title:
        return ""
    clean = _strip_markdown_inline(str(title))
    if ":" in clean:
        head, _, tail = clean.partition(":")
        tail = _strip_markdown_inline(tail).strip()
        if tail and len(head.strip()) <= 24:
            return tail
    return ""


def _body_first_purpose(body: str) -> str:
    """First complete sentence of the card body with markdown / numbering stripped."""
    if not body:
        return ""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        clean = _strip_markdown_inline(line)
        if clean:
            return _first_sentence(clean)
    return ""


def _extract_purpose(title: str | None, body: str) -> str | None:
    """One-line human purpose (AIPOS-261 FIX-1: 人话化, no markdown裸露).

    Priority 1 — title colon suffix ('FIX-1:任务摘要再通俗化' -> '任务摘要再通俗化').
    Priority 2 — first complete sentence of the body, markdown / backticks /
    numbering stripped. Truncated to PURPOSE_MAX_CHARS with an ellipsis when longer.
    Returns None when neither yields anything (UI then shows '(无目的摘要)')."""
    suffix = _title_colon_suffix(title)
    if suffix:
        return _clip_purpose(suffix)
    sentence = _body_first_purpose(body)
    if sentence:
        return _clip_purpose(sentence)
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
    if record_type == RecordType.RETURN:
        return _extract_summary_field(body, ("Result summary", "结果摘要"))
    if record_type == RecordType.AUDIT_VERDICT:
        return _extract_summary_field(body, ("Findings summary", "审计结论"))
    if record_type == RecordType.OWNER_DECISION_RECORD:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return _extract_summary_field(body, ("Summary", "Decision")) or (
            str(metadata.get("decision_status")) if metadata.get("decision_status") else None
        )
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and not line.startswith("```"):
            return line.lstrip("-*> ").strip()[:200]
    return None


# AIPOS-261 FIX-1 (人话化): 展示层技术词翻译小表 — applied to the *derived display
# summary* only; the recorded source text (5_tasks/records/*) is never modified.
TERM_GLOSSARY: dict[str, str] = {
    "publish": "发布",
    "claim": "领取",
    "return": "交付",
    "audit": "审计",
    "finalize": "收编",
    "dry_run": "预检",
    "PreAuthorized": "自动放行",
    "envelope": "自动放行",
    "verdict": "判决",
}
# Case-insensitive whole-word match, longest key first so 'PreAuthorized' is tried
# before any shorter substring; the matched token is replaced by its Chinese value.
_GLOSSARY_LOWER = {k.lower(): v for k, v in TERM_GLOSSARY.items()}
_GLOSSARY_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(TERM_GLOSSARY, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _glossary_replace(match: re.Match) -> str:
    return _GLOSSARY_LOWER.get(match.group(1).lower(), match.group(1))


def _humanize_summary(summary: str | None) -> str | None:
    """FIX-1: strip markdown + translate English tech terms for a display summary.
    Read-only on the string; the record file is untouched. None stays None."""
    if not summary:
        return summary
    clean = _strip_markdown_inline(summary)
    return _GLOSSARY_RE.sub(_glossary_replace, clean)


def build_timeline_event(record: dict[str, Any]) -> dict[str, Any]:
    """Project one record into a timeline/feed event with a pinned key set.

    Keys (AIPOS-260 S4 contract): record_type, record_id, actor, timestamp,
    verb, summary, verdict. Return records additionally carry result_summary;
    verdict records additionally carry findings_summary.
    """
    record_type = record.get("record_type") or ""
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    # FIX-1 (人话化): the display summary is markdown-stripped + tech-term translated;
    # the recorded source body is never modified (records stay byte-identical).
    summary = _humanize_summary(_record_summary(record))
    event: dict[str, Any] = {
        "record_type": record_type,
        "record_id": record.get("record_id") or record.get("record_id"),
        "task_id": record.get("task_id"),
        "actor": _record_actor(record),
        "timestamp": _record_timestamp(record),
        "verb": RECORD_VERBS.get(record_type, record_type or "记录"),
        "summary": summary,
        "verdict": metadata.get("verdict") if record_type == RecordType.AUDIT_VERDICT else None,
        # AIPOS-261 (人话化): human role + full sentence. The raw instance stays on
        # `actor`; the face leads with the Chinese role so the feed reads like speech.
        "role": _role_for_event(record_type),
        "phrase": _phrase_for_event(record_type, metadata),
    }
    if record_type == RecordType.RETURN:
        # result_summary is the already-recorded return evidence (pinned key).
        event["result_summary"] = summary
    if record_type == RecordType.AUDIT_VERDICT:
        # findings_summary is the already-recorded audit evidence (pinned key).
        event["findings_summary"] = summary
    return event


def derive_true_stage(
    task_id: str | None,
    recs: dict[str, list[dict[str, Any]]],
    queue_state: str | None,
    main_verdict: str | None = None,
    completed_dossier: bool = False,
) -> str:
    """Derive the record-truth stage badge for one task. Pure display-layer
    derivation: does not touch the queue state machine or move any file.

    Order (terminal-first): closed -> verdict -> auditing -> delivered ->
    executing -> published; falls back to queue_state (pending/blocked) when no
    records exist yet.

    ``main_verdict`` is the verdict recorded against this card's reviewed main
    card (used for audit 'R' execution cards whose verdict is filed under the
    reviewed task_id, not the audit card id).

    ``completed_dossier`` (FIX-2 F-261-4) is True when the governance workspace
    already holds an archived dossier dir 5_tasks/queue/completed/<ID>/ — an
    "已收编" (incorporated) terminal signal that the records-only verdict /
    queue_state path misses (e.g. 253/254 judged PASS but still shown as such)."""
    if queue_state == "completed":
        return "closed"
    # FIX-2 F-261-4: archived dossier dir present => already incorporated => closed.
    if completed_dossier:
        return "closed"
    upper_id = (task_id or "").upper()
    # A finalize (FZ) execution card that has returned = closed loop.
    if upper_id.endswith("FZ") and recs.get("returns"):
        return "closed"
    # An audit (R) execution card: closed once the reviewed main card is judged.
    if upper_id.endswith("R") and main_verdict:
        return "verdict_pass" if str(main_verdict).upper() == Verdict.PASS else "verdict_fail"
    verdicts = recs.get("audit_verdicts") or []
    if verdicts:
        # Sort by timestamp to get chronological order (earliest first)
        sorted_verdicts = sorted(verdicts, key=lambda v: (
            (v.get("metadata") or {}).get("verdict_at")
            or (v.get("metadata") or {}).get("created_at")
            or v.get("verdict_at")
            or v.get("created_at")
            or ""
        ))
        # Take the LATEST (last) verdict, not any(FAIL) across all history
        latest_verdict = str(
            (sorted_verdicts[-1].get("metadata") or {}).get("verdict") or ""
        ).strip().upper()
        if latest_verdict == Verdict.FAIL:
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

# FIX-2 F-261-3 (Owner 三态裁定 2026-07-28): three top-level states. The
# fine-grained true_stage collapses to 已发布 / 执行中 / 已闭环 for the overview
# pills + the card badge; the fine-grained sub-state stays on each row as the
# card's retained sub-state. Pure read-side derivation.
TOP_LEVEL_LABELS: dict[str, str] = {
    "published": "已发布",
    "executing": "执行中",
    "closed": "已闭环",
}

TOP_LEVEL_ORDER: list[str] = ["published", "executing", "closed"]


def top_level_state(true_stage: str | None) -> str:
    """Collapse a fine-grained true_stage into one of three top-level buckets.
    进入执行后、未闭环前的一切状态(执行中/已交付待审/审计中/判决 PASS·FAIL)都归
    "执行中"; published/pending/blocked/unknown -> 已发布; closed -> 已闭环."""
    if true_stage in ("executing", "delivered", "auditing", "verdict_pass", "verdict_fail"):
        return "executing"
    if true_stage == "closed":
        return "closed"
    return "published"


def badge_label_for(
    true_stage: str | None, top_level: str, stage_label: str | None
) -> str:
    """Composed card badge text (F-261-3). 执行中 cards carry an "执行中" prefix
    with the fine-grained sub-state retained (e.g. 执行中 · 判决 PASS); the
    executing sub-state itself collapses (执行中 · 执行中 -> 执行中). Other buckets
    show the top-level label alone."""
    if top_level == "executing":
        prefix = TOP_LEVEL_LABELS["executing"]
        if true_stage == "executing":
            return prefix
        sub = stage_label or true_stage or ""
        return f"{prefix} · {sub}" if sub else prefix
    return TOP_LEVEL_LABELS.get(top_level, stage_label or true_stage or "")


_RECORD_KINDS_FOR_TIMELINE: tuple[str, ...] = (
    "publishes",
    "claims",
    "returns",
    "audit_dispatches",
    "audit_verdicts",
)


def _sort_key_timestamp(event: dict[str, Any]) -> str:
    return str(event.get("timestamp") or "")


def _parse_iso(value: str | None) -> str | None:
    """Return an ISO timestamp string trimmed, or None."""
    text = str(value or "").strip()
    return text or None


def _duration_seconds(created: str | None, updated: str | None) -> int | None:
    """Whole-second duration between two ISO timestamps, or None when unset/unparseable.
    Read-only derivation from already-recorded session created/updated fields."""
    import datetime as _dt

    c = _parse_iso(created)
    u = _parse_iso(updated)
    if not c or not u:
        return None
    try:
        start = _dt.datetime.fromisoformat(c.replace("Z", "+00:00"))
        end = _dt.datetime.fromisoformat(u.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (end - start).total_seconds()
    if delta < 0:
        return None
    return int(delta)


def _runtime_bundle_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Normalized runtime bundle for ONE record (AIPOS-265 read-side convergence).

    Prefers the agent_runtime map (the single new 口径 since AIPOS-261) and falls
    back to the legacy actual_model/reported_tokens pair so pre-265 records still
    surface their reported runtime in the 档案 popup. Returns
    {harness?, model_self_reported?, tokens_in?, tokens_out?} or None when the
    record carries no runtime signal at all. Pure read; no file touched."""
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    bundle: dict[str, Any] = {}
    runtime = metadata.get("agent_runtime") if isinstance(metadata.get("agent_runtime"), dict) else None
    if runtime:
        for k in ("harness", "model_self_reported"):
            val = runtime.get(k)
            if val:
                bundle[k] = val
        for k in ("tokens_in", "tokens_out"):
            val = runtime.get(k)
            if isinstance(val, int) and not isinstance(val, bool):
                bundle[k] = val
    if not bundle:
        # Legacy fallback (read-side compat): actual_model / reported_tokens.
        actual_model = str(metadata.get("actual_model") or "").strip()
        reported_tokens = metadata.get("reported_tokens")
        if actual_model:
            bundle["model_self_reported"] = actual_model
        if isinstance(reported_tokens, int) and not isinstance(reported_tokens, bool):
            bundle["tokens_in"] = reported_tokens
    return bundle or None


def _build_instance_profile_index(
    records_report: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Per-instance most-recent-known runtime profile (the 档案, AIPOS-265).

    Scans return AND audit_verdict records (AIPOS-265 FIX-1: auditors file
    verdicts, not returns — a returns-only scan left every auditor's 档案 blank,
    which is exactly what Owner eye-verify打回 caught: "exec 档案全显 / audit 全暂无").
    Groups by actor (canonical instance) and keeps the latest (by timestamp) that
    carries any runtime signal — agent_runtime preferred, legacy
    actual_model/reported_tokens as the read-side fallback for pre-265 returns.
    Each entry records its source record id + time so the popup can attribute the
    档案. Pure read-side derivation; no history file is modified."""
    profiles: dict[str, dict[str, Any]] = {}

    def _consider(record: dict[str, Any], ts_key: str, id_key: str) -> None:
        bundle = _runtime_bundle_from_record(record)
        if not bundle:
            return
        instance = _record_actor(record)
        if not instance:
            return
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        ts = str(metadata.get(ts_key) or record.get(ts_key) or "")
        rid = str(record.get(id_key) or metadata.get(id_key) or "")
        prev = profiles.get(instance)
        prev_ts = str(prev.get("source_returned_at") or "") if prev else ""
        # Replace when first seen, or when this record is strictly later (ISO lex).
        # A record with no timestamp never displaces one that already has one.
        # source_return_id/source_returned_at are the established contract keys
        # (popup renders "来自 <id> · <when>"); for a verdict source the id is the
        # verdict_id — honest attribution, stable contract.
        if prev is None or (bool(ts) and (not prev_ts or ts > prev_ts)):
            profiles[instance] = {
                "harness": bundle.get("harness"),
                "model_self_reported": bundle.get("model_self_reported"),
                "tokens_in": bundle.get("tokens_in"),
                "tokens_out": bundle.get("tokens_out"),
                "source_return_id": rid or None,
                "source_returned_at": ts or None,
            }

    for record in records_report.get("returns", []):
        _consider(record, "returned_at", "return_id")
    for record in records_report.get("audit_verdicts", []):
        _consider(record, "verdict_at", "verdict_id")
    return profiles


def _enrich_event_agent_info(
    event: dict[str, Any],
    record: dict[str, Any] | None,
    records_report: dict[str, Any],
    profile_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Attach agent_info to an event (AIPOS-265: ALL record types, 档案式 semantics).

    - profile: the agent's most recent known runtime 档案 (latest return carrying a
      runtime signal for this instance). None when no such return exists.
    - round: THIS event's own runtime (return/claim records only); None for every
      other record type → the popup shows "本轮未记录".

    Model/token fields are SELF-REPORTED (recorded, never verified); callers must
    label them 自报. Absent sub-fields → None → UI shows 未记录."""
    record_type = (record.get("record_type") if record else None) or event.get("record_type")
    instance = event.get("actor")
    info: dict[str, Any] = {
        "role": _role_for_event(record_type),
        "instance": instance,
        "self_reported": True,  # model/token are agent-reported, not gate-measured
        "profile": dict(profile_index[instance]) if (instance and profile_index.get(instance)) else None,
        "round": None,
    }
    # 本轮: this record's own runtime (return/claim only).
    if record is not None and record_type in ("return", "claim"):
        bundle = _runtime_bundle_from_record(record)
        if bundle:
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            round_info: dict[str, Any] = {
                "harness": bundle.get("harness"),
                "model_self_reported": bundle.get("model_self_reported"),
                "tokens_in": bundle.get("tokens_in"),
                "tokens_out": bundle.get("tokens_out"),
                "duration_seconds": None,
            }
            session_id = metadata.get("session_id") or record.get("session_id")
            sessions = records_report.get("session_index", {}).get(str(session_id), []) if session_id else []
            if sessions:
                smeta = sessions[0].get("metadata") if isinstance(sessions[0].get("metadata"), dict) else {}
                round_info["duration_seconds"] = _duration_seconds(
                    smeta.get("created_at") or sessions[0].get("created_at"),
                    smeta.get("updated_at") or sessions[0].get("updated_at"),
                )
            info["round"] = round_info
    event["agent_info"] = info
    return info


def _verdict_chain_for_root(records_report: dict[str, Any], root: str) -> list[dict[str, Any]]:
    """Audit verdict records filed against the unit's main card (indexed by
    reviewed_task_id == root in records.py), earliest first. Pure records read."""
    verdicts = records_report.get("task_audit_verdicts", {}).get(root, [])
    chain: list[dict[str, Any]] = list(verdicts)
    chain.sort(key=_record_sort_key_asc)
    return chain


def _record_sort_key_asc(record: dict[str, Any]) -> tuple[str, str]:
    metadata = record.get("metadata", {})
    ts = (
        metadata.get("verdict_at")
        or metadata.get("created_at")
        or record.get("verdict_at")
        or record.get("created_at")
        or ""
    )
    return (str(ts), str(record.get("path") or ""))


def _build_stage_chain(
    members: list[dict[str, Any]],
    verdict_chain: list[dict[str, Any]],
    has_finalize: bool,
    overall_stage: str | None = None,
) -> dict[str, Any]:
    """Build the one-line stage-chain FACE for a closure unit, plus structured steps.

    Truth sources (records-only): main-card delivery, fix-card members (each implies
    a prior FAIL that spawned it), recorded verdict chain, and finalize member.
    Intermediate FAIL verdicts that pre-date the verdict-record mechanism are NOT
    fabricated — when only the final verdict is recorded, the face shows the fix-round
    count honestly (e.g. “经 3 轮修复 → PASS”)."""
    verdict_values = [
        str((v.get("metadata") or {}).get("verdict") or "").strip().upper()
        for v in verdict_chain
    ]
    verdict_values = [val for val in verdict_values if val]
    fix_rounds = sum(1 for m in members if m.get("member_kind") == "fix")
    main_delivered = any(m.get("member_kind") == "main" and m.get("has_return") for m in members)
    final_verdict = verdict_values[-1] if verdict_values else None
    has_any_activity = main_delivered or bool(verdict_values) or fix_rounds or has_finalize

    # Legacy / no-record cards: when nothing is recorded yet, the face just names the
    # queue-derived stage (e.g. 已闭环 for old completed cards) instead of inventing a
    # delivery→audit chain that the records do not support.
    if not has_any_activity and overall_stage:
        legacy_label = STAGE_LABELS_FULL.get(overall_stage, overall_stage)
        return {
            "face": legacy_label,
            "steps": [{"key": "stage", "label": legacy_label, "state": overall_stage}],
            "audit_rounds": 0,
            "fix_rounds": 0,
            "verdict_chain": [],
            "final_verdict": None,
        }

    # Face string (人话).
    exec_label = "执行 ✓" if main_delivered else "执行中"
    if verdict_values and len(verdict_values) > 1:
        audit_detail = "→".join(verdict_values)
        audit_label = f"审计 {len(verdict_values)} 轮（{audit_detail}）"
    elif final_verdict:
        if fix_rounds:
            audit_label = f"审计（经 {fix_rounds} 轮修复 → {final_verdict}）"
        else:
            audit_label = f"审计（{final_verdict}）"
    elif fix_rounds:
        audit_label = f"审计（经 {fix_rounds} 轮修复）"
    elif main_delivered:
        audit_label = "审计中"
    else:
        audit_label = "待审计"
    finalize_label = "收编 ✓" if has_finalize else ""
    face = " → ".join(part for part in (exec_label, audit_label, finalize_label) if part)

    steps = [
        {"key": "exec", "label": "执行", "state": "done" if main_delivered else "active"},
    ]
    if verdict_values and len(verdict_values) > 1:
        steps.append({
            "key": "audit",
            "label": f"审计 {len(verdict_values)} 轮",
            "detail": "→".join(verdict_values),
            "state": "done" if final_verdict == Verdict.PASS else "fail",
        })
    elif final_verdict:
        label = f"经 {fix_rounds} 轮修复" if fix_rounds else "审计"
        steps.append({
            "key": "audit",
            "label": label,
            "detail": final_verdict,
            "state": "done" if final_verdict == Verdict.PASS else "fail",
        })
    elif main_delivered:
        steps.append({"key": "audit", "label": "审计", "state": "active"})
    else:
        steps.append({"key": "audit", "label": "审计", "state": "pending"})
    if has_finalize:
        steps.append({"key": "finalize", "label": "收编", "state": "done"})

    return {
        "face": face,
        "steps": steps,
        "audit_rounds": len(verdict_values),
        "fix_rounds": fix_rounds,
        "verdict_chain": verdict_values,
        "final_verdict": final_verdict,
    }


def _build_closure_units(
    task_rows: list[dict[str, Any]],
    closure_meta: dict[str, dict[str, Any]],
    row_by_id: dict[str, dict[str, Any]],
    records_report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Group task rows into closure units (主卡 + R/F*/FZ 全家). One unit per root.

    Each unit exposes: root_task_id, title/purpose (from the main card when present),
    members (per-card kind + delivery flag), a one-line stage_chain face + structured
    steps, the overall true_stage, and a merged family timeline (earliest→latest).
    Cards whose root resolves to themselves AND have no family form a unit of one.
    """
    members_by_root: dict[str, list[str]] = {}
    for tid, meta in closure_meta.items():
        root = str(meta.get("root") or tid or "").strip() or tid
        members_by_root.setdefault(root, []).append(tid)

    units: list[dict[str, Any]] = []
    for root, member_ids in members_by_root.items():
        main_id = root if root in row_by_id else next(
            (tid for tid in member_ids if closure_meta.get(tid, {}).get("member_kind") == "main"),
            None,
        )
        main_row = row_by_id.get(str(main_id)) if main_id else None
        members: list[dict[str, Any]] = []
        for tid in sorted(member_ids):
            meta = closure_meta.get(tid, {})
            members.append({
                "task_id": tid,
                "member_kind": meta.get("member_kind", "main"),
                "has_return": bool(meta.get("has_return")),
            })
        has_finalize = any(m["member_kind"] == "finalize" for m in members)
        verdict_chain = _verdict_chain_for_root(records_report, root)
        stage_chain = _build_stage_chain(members, verdict_chain, has_finalize)

        # Merged family timeline: every member row's timeline events, de-duped by
        # record_id, earliest→latest.
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tid in sorted(member_ids):
            row = row_by_id.get(tid)
            if not row:
                continue
            for ev in row.get("timeline", []):
                key = str(ev.get("record_id") or "") or f"{ev.get('record_type')}:{ev.get('timestamp')}:{tid}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(ev)
        merged.sort(key=_sort_key_timestamp)

        # AIPOS-283 FIX-1: compute unit's latest activity for ordering.
        unit_latest_activity = ""
        if merged:
            timestamps = [str(ev.get("timestamp") or "") for ev in merged]
            timestamps = [ts for ts in timestamps if ts]
            if timestamps:
                unit_latest_activity = max(timestamps)

        overall_stage = main_row.get("true_stage") if main_row else None
        overall_tl = top_level_state(overall_stage)
        overall_label = STAGE_LABELS_FULL.get(overall_stage, overall_stage) if overall_stage else "未知"
        stage_chain = _build_stage_chain(members, verdict_chain, has_finalize, overall_stage=overall_stage)
        unit = {
            "root_task_id": root,
            "title": (main_row.get("title") if main_row else None) or root,
            "purpose": (main_row.get("purpose") if main_row else None) or "",
            "true_stage": overall_stage,
            "stage_label": overall_label,
            # FIX-2 F-261-3: top-level bucket (三态) + composed card badge.
            "top_level_state": overall_tl,
            "badge_label": badge_label_for(overall_stage, overall_tl, overall_label),
            "stage_chain": stage_chain["face"],
            "stage_chain_steps": stage_chain["steps"],
            "audit_rounds": stage_chain["audit_rounds"],
            "fix_rounds": stage_chain["fix_rounds"],
            "verdict_chain": stage_chain["verdict_chain"],
            "final_verdict": stage_chain["final_verdict"],
            "members": members,
            "timeline": merged,
            # AIPOS-283 FIX-1: unit's latest activity timestamp for sorting (internal).
            "_latest_activity": unit_latest_activity,
        }
        units.append(unit)

    # AIPOS-283 FIX-1: order units by latest activity descending (newest first),
    # then root_task_id descending when timestamps tie.
    units.sort(
        key=lambda u: (
            str(u.get("_latest_activity") or ""),
            str(u.get("root_task_id") or ""),
        ),
        reverse=True,
    )
    return units


def build_owner_truth_view(repo_root: str | Any) -> dict[str, Any]:
    """Aggregate the Owner truth summary read surface. Read-only."""
    from pathlib import Path  # local import keeps module top clean

    from tools.aipos_cli.records import find_records_for_task

    resolved = Path(repo_root).resolve() if repo_root else None
    records_report = load_records(resolved) if resolved else load_records()
    tasks = load_all_tasks(resolved) if resolved else load_all_tasks()
    # AIPOS-265: per-instance most-recent-known runtime 档案 (drives the agent popup's
    # profile block for EVERY record type, not just returns). Built once, read-only.
    profile_index = _build_instance_profile_index(records_report)

    # FIX-2 F-261-4: archived-dossier root for the "already incorporated" closed
    # signal (5_tasks/queue/completed/<ID>/ dir). Only read for existence; never
    # written. None when no repo root (derive_true_stage treats falsy as absent).
    completed_root = (resolved / "5_tasks" / "queue" / "completed") if resolved else None

    # verdict filed per reviewed main card (newest first in records_report).
    verdict_by_task: dict[str, str] = {}
    for tid, verdicts in (records_report.get("task_audit_verdicts") or {}).items():
        if verdicts:
            value = str((verdicts[0].get("metadata") or {}).get("verdict") or "").strip().upper()
            if value:
                verdict_by_task[str(tid)] = value

    task_rows: list[dict[str, Any]] = []
    # AIPOS-261: per-task closure metadata for unit grouping (root + member kind +
    # delivery flags). Kept alongside rows so grouping stays a pure read-side derivation.
    closure_meta: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = task.get("task_id")
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        recs = find_records_for_task(records_report, task_id) if task_id else {}
        queue_state = task.get("queue_state") or metadata.get("status")
        upper_id = (task_id or "").upper()
        main_verdict = None
        if upper_id.endswith("R") and len(upper_id) > 1:
            main_verdict = verdict_by_task.get(upper_id[:-1])
        # FIX-2 F-261-4: completed dossier dir present => incorporated (closed).
        completed_dossier = bool(
            completed_root and task_id and (completed_root / str(task_id)).is_dir()
        )
        true_stage = derive_true_stage(
            task_id, recs, queue_state, main_verdict, completed_dossier
        )
        tl_state = top_level_state(true_stage)

        # record_id -> source record, for agent_info enrichment (return/claim carry
        # the round's own runtime; other types enrich via the profile index only).
        record_by_id: dict[str, dict[str, Any]] = {}
        for kind in ("returns", "claims"):
            for record in recs.get(kind, []):
                rid = record.get("record_id")
                if rid:
                    record_by_id[str(rid)] = record

        timeline_raw: list[dict[str, Any]] = []
        for kind in _RECORD_KINDS_FOR_TIMELINE:
            for record in recs.get(kind, []):
                timeline_raw.append(build_timeline_event(record))
        timeline_raw.sort(key=_sort_key_timestamp)  # earliest -> latest
        # AIPOS-265: attach agent_info (档案式: most-recent profile + this round) to
        # EVERY timeline event so all actor names render through one clickable path.
        for ev in timeline_raw:
            src = record_by_id.get(str(ev.get("record_id") or ""))
            _enrich_event_agent_info(ev, src, records_report, profile_index)

        verdict_value = None
        for verdict_record in recs.get("audit_verdicts", []):
            value = (verdict_record.get("metadata") or {}).get("verdict")
            if value:
                verdict_value = value
                break

        # AIPOS-283 FIX-1: compute latest activity time for ordering.
        # Latest activity = max timestamp across all timeline events (publish/claim/return/audit).
        latest_activity = ""
        if timeline_raw:
            timestamps = [str(ev.get("timestamp") or "") for ev in timeline_raw]
            timestamps = [ts for ts in timestamps if ts]  # filter empty
            if timestamps:
                latest_activity = max(timestamps)  # ISO timestamps: lexicographic max = latest

        title = task.get("title") or metadata.get("title")
        row = {
            "task_id": task_id,
            "title": title,
            "purpose": _extract_purpose(title, task.get("body") or ""),
            "path": task.get("path"),
            "queue_state": queue_state,
            "true_stage": true_stage,
            "stage_label": STAGE_LABELS_FULL.get(true_stage, true_stage),
            # FIX-2 F-261-3: top-level bucket (三态) + composed card badge.
            "top_level_state": tl_state,
            "badge_label": badge_label_for(true_stage, tl_state, STAGE_LABELS_FULL.get(true_stage, true_stage)),
            "verdict": verdict_value,
            "timeline": timeline_raw,
            # AIPOS-283 FIX-1: latest activity timestamp for sorting (internal field).
            "_latest_activity": latest_activity,
        }
        task_rows.append(row)
        root = _derive_closure_root(task_id, metadata)
        closure_meta[str(task_id)] = {
            "root": root,
            "member_kind": _member_kind(task_id, metadata, root),
            "has_return": bool(recs.get("returns")),
            "task_mode": str(metadata.get("task_mode") or "").strip(),
        }

    # AIPOS-283 FIX-1: Owner 网页打回 verify_AIPOS-283_20260730T163735 — "任务里不管是
    # 已发布还是正在执行还是已闭环,都以最近一次的任务往下排序,现在是最下面才是最近的任务".
    # Sort by latest activity time descending (newest first), then task_id descending
    # when timestamps tie. ISO timestamps sort lexicographically; negate with reversed().
    task_rows.sort(
        key=lambda r: (
            str(r.get("_latest_activity") or ""),  # empty string sorts first (oldest/no activity)
            str(r.get("task_id") or ""),
        ),
        reverse=True,  # descending: latest activity on top, then task_id Z→A
    )
    row_by_id = {str(r.get("task_id")): r for r in task_rows}

    stage_counts: dict[str, int] = {}
    top_level_counts: dict[str, int] = {}
    for row in task_rows:
        stage = str(row["true_stage"] or "unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        tl = str(row.get("top_level_state") or "published")
        top_level_counts[tl] = top_level_counts.get(tl, 0) + 1

    # AIPOS-261: closure-unit grouping (主卡 + R/F*/FZ 全家收进一张卡). Face = one-line
    # stage chain; members carry per-card kind. Pure read-side grouping over the
    # already-loaded tasks + records; nothing is moved or rewritten.
    closure_units = _build_closure_units(task_rows, closure_meta, row_by_id, records_report)

    # Cross-task activity feed: every record, newest first.
    activity_feed: list[dict[str, Any]] = []
    feed_record_by_id: dict[str, dict[str, Any]] = {}
    for kind in _RECORD_KINDS_FOR_TIMELINE:
        for record in records_report.get(kind, []):
            activity_feed.append(build_timeline_event(record))
            rid = record.get("record_id")
            if rid:
                feed_record_by_id[str(rid)] = record
    for record in records_report.get("owner_decisions", []):
        activity_feed.append(build_timeline_event(record))
    activity_feed.sort(key=_sort_key_timestamp, reverse=True)
    # AIPOS-265: every feed event gets agent_info (档案式) — all actor names clickable.
    for ev in activity_feed:
        src = feed_record_by_id.get(str(ev.get("record_id") or ""))
        _enrich_event_agent_info(ev, src, records_report, profile_index)

    # AIPOS-274F2: mirror summary into data.summary so frontend can read either
    # response.summary or response.data.summary (backend aligns to frontend).
    # Top-level summary retained for backward compat.
    summary = {
        "total_tasks": len(task_rows),
        "closure_units": len(closure_units),
        "stage_counts": stage_counts,
        "top_level_counts": top_level_counts,
        "activity_events": len(activity_feed),
    }
    return {
        "ok": True,
        "operation": "get_owner_truth_view",
        "dry_run": False,
        "actor": None,
        "data": {
            "tasks": task_rows,
            "closure_units": closure_units,
            "stage_counts": stage_counts,
            "stage_labels": STAGE_LABELS_FULL,
            "true_stage_order": list(TRUE_STAGE_ORDER),
            # FIX-2 F-261-3: three top-level states (三态) for overview pills.
            "top_level_counts": top_level_counts,
            "top_level_labels": TOP_LEVEL_LABELS,
            "top_level_order": list(TOP_LEVEL_ORDER),
            "activity_feed": activity_feed,
            "record_field_keys": list(RECORD_FIELD_KEYS),
            "records_summary": records_report.get("summary"),
            # AIPOS-274F2: summary mirrored inside data for frontend alignment.
            "summary": summary,
        },
        "summary": summary,
        "warnings": [],
        "errors": [],
        "safety_notice": (
            "Read-only Owner truth summary surface. Stages are derived from already "
            "recorded truth (records + queue_state); the queue state machine is not "
            "mutated and no records are rewritten."
        ),
    }
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
