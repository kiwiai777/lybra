from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

ORCHESTRATION_ROOT = Path("5_tasks/orchestration")
ORCHESTRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _safe_orchestration_id(value: str) -> bool:
    if not value or "/" in value or "\\" in value or ".." in value:
        return False
    return bool(ORCHESTRATION_ID_PATTERN.fullmatch(value))


def _load_yaml_entries(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"missing source: {path.name}"]
    if not path.is_file():
        return [], [f"source is not a file: {path.name}"]
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return [], [f"empty source: {path.name}"]
    if yaml is None:
        return [], ["PyYAML is unavailable; cannot parse append-only orchestration logs"]
    try:
        loaded = yaml.safe_load(text)
    except Exception as exc:
        return [], [f"{path.name} parse failed: {exc}"]
    if loaded is None:
        return [], [f"empty source: {path.name}"]
    if not isinstance(loaded, list):
        return [], [f"{path.name} did not parse to a list"]
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, item in enumerate(loaded, start=1):
        if isinstance(item, dict):
            entries.append(item)
        else:
            warnings.append(f"{path.name} entry {index} did not parse to a mapping")
    return entries, warnings


def _iteration_severity(entry: dict[str, Any]) -> str:
    verdict = _safe_text(entry.get("verdict"))
    if verdict == "needs_owner" or entry.get("owner_decision_required") is True:
        return "needs_owner"
    if verdict in {"blocked", "failed"}:
        return "blocking"
    if verdict in {"repair", "wait_for_audit"}:
        return "warning"
    return "info"


def _event_needs_owner(entry: dict[str, Any]) -> bool:
    return entry.get("event_type") in {"needs_owner_raised", "owner_decision_recorded"} or entry.get("severity") == "needs_owner"


def _event_blocking(entry: dict[str, Any]) -> bool:
    return entry.get("severity") == "blocking" or entry.get("event_type") in {"runtime_unavailable", "quota_exhausted", "orchestration_failed"}


def _timeline_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (_safe_text(item.get("timestamp")), _safe_text(item.get("kind")), _safe_text(item.get("id")))


def build_orchestration_timeline_preview(repo_root: Path, orchestration_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    conflicts: list[str] = []
    timeline: list[dict[str, Any]] = []
    orchestration_id = _safe_text(orchestration_id)
    iteration_rel = ORCHESTRATION_ROOT / orchestration_id / "planner_iterations.md"
    event_rel = ORCHESTRATION_ROOT / orchestration_id / "orchestration_events.md"

    if not _safe_orchestration_id(orchestration_id):
        blocking_reasons.append("orchestration_id is path-unsafe")

    for rel_path in [iteration_rel, event_rel]:
        try:
            (repo_root / rel_path).resolve().relative_to((repo_root / ORCHESTRATION_ROOT).resolve())
        except ValueError:
            blocking_reasons.append(f"path resolves outside 5_tasks/orchestration: {rel_path.as_posix()}")

    iterations, iteration_warnings = _load_yaml_entries(repo_root / iteration_rel)
    events, event_warnings = _load_yaml_entries(repo_root / event_rel)
    warnings.extend(iteration_warnings)
    warnings.extend(event_warnings)

    for entry in iterations:
        if entry.get("orchestration_id") != orchestration_id:
            conflicts.append(f"planner_iterations.md has entry for another orchestration: {entry.get('iteration_id')}")
            continue
        severity = _iteration_severity(entry)
        decisions = entry.get("decisions") if isinstance(entry.get("decisions"), list) else []
        decision_text = "; ".join(
            _safe_text(decision.get("decision")) for decision in decisions if isinstance(decision, dict) and _safe_text(decision.get("decision"))
        )
        timeline.append(
            {
                "kind": "planner_iteration",
                "id": _safe_text(entry.get("iteration_id")),
                "timestamp": _safe_text(entry.get("ended_at")) or _safe_text(entry.get("started_at")),
                "severity": severity,
                "title": f"Planner iteration {entry.get('iteration_number') or '-'}: {_safe_text(entry.get('verdict')) or 'unknown'}",
                "summary": decision_text or _safe_text(entry.get("verdict")) or "Planner iteration recorded.",
                "actor": _safe_text(entry.get("planner_agent_instance")) or _safe_text(entry.get("planner_agent")),
                "refs": _as_list(entry.get("input_refs")) + _as_list(entry.get("forum_thread_ref")),
                "owner_attention_required": severity == "needs_owner",
                "blocking": severity == "blocking",
                "source_ref": iteration_rel.as_posix(),
                "raw": entry,
            }
        )

    for entry in events:
        if entry.get("orchestration_id") != orchestration_id:
            conflicts.append(f"orchestration_events.md has entry for another orchestration: {entry.get('event_id')}")
            continue
        needs_owner = _event_needs_owner(entry)
        is_blocking = _event_blocking(entry)
        severity = "needs_owner" if needs_owner else "blocking" if is_blocking else _safe_text(entry.get("severity")) or "info"
        timeline.append(
            {
                "kind": "orchestration_event",
                "id": _safe_text(entry.get("event_id")),
                "timestamp": _safe_text(entry.get("timestamp")),
                "severity": severity,
                "title": _safe_text(entry.get("event_type")) or "orchestration_event",
                "summary": _safe_text(entry.get("summary")) or _safe_text(entry.get("event_id")) or "Orchestration event recorded.",
                "actor": _safe_text(entry.get("actor")),
                "refs": _as_list(entry.get("refs")),
                "owner_attention_required": needs_owner,
                "blocking": is_blocking,
                "source_ref": event_rel.as_posix(),
                "related_task_id": _safe_text(entry.get("related_task_id")),
                "related_iteration_id": _safe_text(entry.get("related_iteration_id")),
                "raw": entry,
            }
        )

    timeline.sort(key=_timeline_sort_key)
    if not timeline and not blocking_reasons:
        warnings.append("no append-only timeline entries found for orchestration_id")

    owner_attention_items = [item for item in timeline if item.get("owner_attention_required")]
    blocking_items = [item for item in timeline if item.get("blocking")]
    return {
        "action": "orchestration_timeline_preview",
        "orchestration_id": orchestration_id,
        "verdict": "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if owner_attention_items or conflicts else ("WARN" if warnings else "PASS")),
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "conflicts": conflicts,
        "source_refs": [iteration_rel.as_posix(), event_rel.as_posix()],
        "timeline": timeline,
        "summary": {
            "orchestration_id": orchestration_id,
            "timeline_items": len(timeline),
            "planner_iterations": sum(1 for item in timeline if item.get("kind") == "planner_iteration"),
            "orchestration_events": sum(1 for item in timeline if item.get("kind") == "orchestration_event"),
            "owner_attention_count": len(owner_attention_items),
            "blocking_count": len(blocking_items),
            "conflict_count": len(conflicts),
            "first_event_at": timeline[0].get("timestamp") if timeline else "",
            "latest_event_at": timeline[-1].get("timestamp") if timeline else "",
        },
        "dry_run": True,
        "would_write": False,
        "writes_enabled": False,
        "execute_allowed": False,
        "dry_run_token": None,
        "owner_confirmation_required": bool(owner_attention_items or conflicts),
        "safety_notice": "AIPOS-70 reads append-only orchestration timeline sources only. It does not write events, iterations, summary state, queue tasks, drafts, records, runtime state, forum backends, or git state.",
    }
