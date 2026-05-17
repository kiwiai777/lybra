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
QUEUE_STATES = {"pending", "claimed", "completed", "blocked"}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _is_safe_orchestration_id(value: str) -> bool:
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


def _task_belongs_to_orchestration(task: dict[str, Any], orchestration_id: str) -> bool:
    metadata = task.get("metadata", {})
    if metadata.get("orchestration_id") == orchestration_id:
        return True
    orchestration = metadata.get("orchestration")
    if isinstance(orchestration, dict) and orchestration.get("orchestration_id") == orchestration_id:
        return True
    return False


def _list_needs_owner_reasons(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, "", False):
        return []
    if value is True:
        return ["Task marked needs_owner"]
    return [str(value).strip()]


def _iteration_sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
    number = entry.get("iteration_number")
    try:
        numeric = int(number)
    except (TypeError, ValueError):
        numeric = -1
    return (numeric, _safe_text(entry.get("ended_at")), _safe_text(entry.get("iteration_id")))


def _derive_status(
    counts: dict[str, int],
    needs_owner_reasons: list[str],
    latest_iteration: dict[str, Any] | None,
    events: list[dict[str, Any]],
) -> str:
    if needs_owner_reasons:
        return "needs_owner"
    if latest_iteration and latest_iteration.get("verdict") == "failed":
        return "failed"
    if any(event.get("event_type") == "orchestration_failed" for event in events):
        return "failed"
    if latest_iteration and latest_iteration.get("verdict") == "cancel":
        return "cancelled"
    if any(event.get("event_type") == "orchestration_cancelled" for event in events):
        return "cancelled"
    if latest_iteration and latest_iteration.get("verdict") == "complete":
        return "completed"
    if any(event.get("event_type") == "orchestration_completed" for event in events):
        return "completed"
    if counts["blocked_subtask_count"]:
        return "blocked"
    if counts["open_subtask_count"]:
        return "running"
    return "planning"


def build_orchestration_summary_preview(
    repo_root: Path,
    orchestration_id: str,
    *,
    tasks: list[dict[str, Any]] | None = None,
    records: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    blocking: list[str] = []
    conflicts: list[str] = []
    orchestration_id = _safe_text(orchestration_id)
    target_rel = ORCHESTRATION_ROOT / orchestration_id / "orchestration_state.md"
    iteration_rel = ORCHESTRATION_ROOT / orchestration_id / "planner_iterations.md"
    event_rel = ORCHESTRATION_ROOT / orchestration_id / "orchestration_events.md"

    if not _is_safe_orchestration_id(orchestration_id):
        blocking.append("orchestration_id is path-unsafe")

    for rel_path in [target_rel, iteration_rel, event_rel]:
        try:
            (repo_root / rel_path).resolve().relative_to((repo_root / ORCHESTRATION_ROOT).resolve())
        except ValueError:
            blocking.append(f"path resolves outside 5_tasks/orchestration: {rel_path.as_posix()}")

    all_tasks = tasks or []
    matching_tasks = [task for task in all_tasks if _task_belongs_to_orchestration(task, orchestration_id)]
    for task in matching_tasks:
        if not task.get("status_consistent", True):
            conflicts.append(f"{task.get('task_id') or task.get('path')} directory/status mismatch")

    iterations, iteration_warnings = _load_yaml_entries(repo_root / iteration_rel)
    events, event_warnings = _load_yaml_entries(repo_root / event_rel)
    warnings.extend(iteration_warnings)
    warnings.extend(event_warnings)

    filtered_iterations: list[dict[str, Any]] = []
    for entry in iterations:
        if entry.get("orchestration_id") == orchestration_id:
            filtered_iterations.append(entry)
        else:
            conflicts.append(f"planner_iterations.md has entry for another orchestration: {entry.get('iteration_id')}")

    filtered_events: list[dict[str, Any]] = []
    for entry in events:
        if entry.get("orchestration_id") == orchestration_id:
            filtered_events.append(entry)
        else:
            conflicts.append(f"orchestration_events.md has entry for another orchestration: {entry.get('event_id')}")

    latest_iteration = max(filtered_iterations, key=_iteration_sort_key) if filtered_iterations else None
    queue_state_counts = {state: 0 for state in QUEUE_STATES}
    for task in matching_tasks:
        queue_state = _safe_text(task.get("queue_state"))
        if queue_state in queue_state_counts:
            queue_state_counts[queue_state] += 1

    counts = {
        "open_subtask_count": queue_state_counts["pending"] + queue_state_counts["claimed"],
        "completed_subtask_count": queue_state_counts["completed"],
        "blocked_subtask_count": queue_state_counts["blocked"],
        "failed_subtask_count": 0,
    }

    needs_owner_reasons: list[str] = []
    for task in matching_tasks:
        metadata = task.get("metadata", {})
        if metadata.get("needs_owner") is True:
            needs_owner_reasons.append(f"{task.get('task_id')}: task marked needs_owner")
        needs_owner_reasons.extend(
            f"{task.get('task_id')}: {reason}" for reason in _list_needs_owner_reasons(metadata.get("needs_owner_reasons"))
        )
    for entry in filtered_iterations:
        if entry.get("verdict") == "needs_owner" or entry.get("owner_decision_required") is True:
            needs_owner_reasons.extend(_as_list(entry.get("needs_owner_reasons")) or ["Planner iteration needs Owner"])
    for event in filtered_events:
        if event.get("event_type") == "needs_owner_raised" or event.get("severity") in {"needs_owner", "blocking"}:
            summary = _safe_text(event.get("summary")) or _safe_text(event.get("event_id")) or "Event needs Owner"
            needs_owner_reasons.append(summary)
    needs_owner_reasons = list(dict.fromkeys(reason for reason in needs_owner_reasons if reason))

    parent_task_id = ""
    planner_agent = ""
    planner_agent_instance = ""
    planner_model_tier = ""
    if latest_iteration:
        parent_task_id = _safe_text(latest_iteration.get("parent_task_id"))
        planner_agent = _safe_text(latest_iteration.get("planner_agent"))
        planner_agent_instance = _safe_text(latest_iteration.get("planner_agent_instance"))
        planner_model_tier = _safe_text(latest_iteration.get("planner_model_tier"))
    for task in matching_tasks:
        metadata = task.get("metadata", {})
        parent_task_id = parent_task_id or _safe_text(metadata.get("parent_task_id")) or _safe_text(metadata.get("task_id"))
        planner_agent = planner_agent or _safe_text(metadata.get("planner_agent"))
        planner_agent_instance = planner_agent_instance or _safe_text(metadata.get("planner_agent_instance"))
        planner_model_tier = planner_model_tier or _safe_text(metadata.get("planner_model_tier"))

    latest_event_at = ""
    for event in filtered_events:
        latest_event_at = max(latest_event_at, _safe_text(event.get("timestamp")))

    current_iteration = latest_iteration.get("iteration_number") if latest_iteration else 0
    planned_summary = {
        "target_path": target_rel.as_posix(),
        "orchestration_id": orchestration_id,
        "parent_task_id": parent_task_id,
        "status": _derive_status(counts, needs_owner_reasons, latest_iteration, filtered_events),
        "planner_agent": planner_agent,
        "planner_agent_instance": planner_agent_instance,
        "planner_model_tier": planner_model_tier,
        "current_iteration": current_iteration,
        **counts,
        "needs_owner": bool(needs_owner_reasons),
        "needs_owner_reasons": needs_owner_reasons,
        "last_planner_run_at": _safe_text(latest_iteration.get("ended_at")) if latest_iteration else "",
        "next_planner_check_after": _safe_text(latest_iteration.get("next_check_after")) if latest_iteration else "",
        "latest_event_at": latest_event_at,
        "subtask_index_ref": "",
        "planner_iterations_ref": iteration_rel.as_posix(),
        "orchestration_events_ref": event_rel.as_posix(),
        "artifact_links_ref": "",
    }

    source_refs = [
        "5_tasks/queue/",
        "5_tasks/records/",
        iteration_rel.as_posix(),
        event_rel.as_posix(),
    ]
    rebuild_notes = [
        f"matched {len(matching_tasks)} queue task(s)",
        f"read {len(filtered_iterations)} planner iteration entr{'y' if len(filtered_iterations) == 1 else 'ies'}",
        f"read {len(filtered_events)} orchestration event entr{'y' if len(filtered_events) == 1 else 'ies'}",
    ]
    if records is not None:
        rebuild_notes.append(f"records summary: {records.get('summary', {})}")

    if not matching_tasks and not filtered_iterations and not filtered_events:
        warnings.append("no queue tasks or append-only logs found for orchestration_id")

    result = {
        "action": "orchestration_summary_preview",
        "orchestration_id": orchestration_id,
        "verdict": "BLOCK" if blocking else ("NEEDS_OWNER" if conflicts else "PASS"),
        "blocking_reasons": blocking,
        "warnings": warnings,
        "source_refs": source_refs,
        "planned_summary": planned_summary,
        "rebuild_notes": rebuild_notes,
        "conflicts": conflicts,
        "dry_run": True,
        "would_write": False,
        "writes_enabled": False,
        "execute_allowed": False,
        "dry_run_token": None,
        "owner_confirmation_required": bool(needs_owner_reasons or conflicts),
        "safety_notice": (
            "AIPOS-68 previews reconstructable orchestration summary state only. "
            "It does not write orchestration_state.md, loop_state.md, subtask_index.md, artifact_links.md, "
            "queue tasks, drafts, records, runtime state, or git state."
        ),
    }
    return result
