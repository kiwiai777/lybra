from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ORCHESTRATION_ROOT = Path("5_tasks/orchestration")
ITERATION_LOG_FILENAME = "planner_iterations.md"
ORCHESTRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

ALLOWED_VERDICTS = {
    "continue",
    "draft_subtasks",
    "publish_ready",
    "wait_for_audit",
    "repair",
    "pause",
    "needs_owner",
    "blocked",
    "complete",
    "cancel",
    "failed",
}
ALLOWED_PLANNER_TIERS = {"L3", "L4"}
REQUIRED_FIELDS = [
    "iteration_id",
    "orchestration_id",
    "iteration_number",
    "planner_agent",
    "planner_model_tier",
    "started_at",
    "ended_at",
    "input_refs",
    "observed_queue_state",
    "observed_subtask_summary",
    "decisions",
    "created_subtasks",
    "updated_recommendations",
    "failure_observations",
    "quota_observations",
    "needs_owner_reasons",
    "next_check_after",
    "verdict",
]


def load_iteration_payload_from_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Planner iteration payload JSON must be an object")
    return data


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return ""
    if any(char in text for char in [":", "#", "[", "]", "{", "}", "\n"]) or text != text.strip():
        return "'" + text.replace("'", "''") + "'"
    return text


def _is_safe_orchestration_id(value: str) -> bool:
    if not value or "/" in value or "\\" in value or ".." in value:
        return False
    return bool(ORCHESTRATION_ID_PATTERN.fullmatch(value))


def _target_path_for(orchestration_id: str) -> Path:
    return ORCHESTRATION_ROOT / orchestration_id / ITERATION_LOG_FILENAME


def _contains_iteration_id(log_text: str, iteration_id: str) -> bool:
    pattern = re.compile(
        rf"(?m)^\s*-\s*iteration_id:\s*{re.escape(iteration_id)}\s*$|"
        rf"^\s*iteration_id:\s*{re.escape(iteration_id)}\s*$"
    )
    return bool(pattern.search(log_text))


def _normalize_iteration(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    blocking: list[str] = []
    iteration: dict[str, Any] = {
        "iteration_id": _safe_text(payload.get("iteration_id")),
        "orchestration_id": _safe_text(payload.get("orchestration_id")),
        "iteration_number": payload.get("iteration_number"),
        "planner_agent": _safe_text(payload.get("planner_agent")),
        "planner_agent_instance": _safe_text(payload.get("planner_agent_instance")),
        "planner_model_tier": _safe_text(payload.get("planner_model_tier")),
        "started_at": _safe_text(payload.get("started_at")),
        "ended_at": _safe_text(payload.get("ended_at")),
        "input_refs": _as_list(payload.get("input_refs")),
        "observed_queue_state": _safe_text(payload.get("observed_queue_state")),
        "observed_subtask_summary": _safe_text(payload.get("observed_subtask_summary")),
        "decisions": _as_list(payload.get("decisions")),
        "created_subtasks": _as_list(payload.get("created_subtasks")),
        "updated_recommendations": _as_list(payload.get("updated_recommendations")),
        "failure_observations": _as_list(payload.get("failure_observations")),
        "quota_observations": _as_list(payload.get("quota_observations")),
        "needs_owner_reasons": _as_list(payload.get("needs_owner_reasons")),
        "next_check_after": _safe_text(payload.get("next_check_after")),
        "verdict": _safe_text(payload.get("verdict")) or "continue",
        "forum_thread_ref": _safe_text(payload.get("forum_thread_ref")),
        "parent_task_id": _safe_text(payload.get("parent_task_id")),
        "requirement_id": _safe_text(payload.get("requirement_id")),
        "active_session_id": _safe_text(payload.get("active_session_id")),
        "prior_session_id": _safe_text(payload.get("prior_session_id")),
        "session_resume_ref": _safe_text(payload.get("session_resume_ref")),
        "role_continuity_preference": _safe_text(payload.get("role_continuity_preference")),
        "owner_decision_required": bool(payload.get("owner_decision_required")),
        "audit_handoff_required": bool(payload.get("audit_handoff_required")),
    }
    if isinstance(payload.get("role_continuity_preference"), dict):
        iteration["role_continuity_preference"] = json.dumps(
            payload["role_continuity_preference"],
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )

    for field in REQUIRED_FIELDS:
        if field not in payload:
            blocking.append(f"Missing required field: {field}")
            continue
        value = iteration.get(field)
        if field in {
            "input_refs",
            "decisions",
            "created_subtasks",
            "updated_recommendations",
            "failure_observations",
            "quota_observations",
            "needs_owner_reasons",
        }:
            if not isinstance(value, list):
                blocking.append(f"Missing required field: {field}")
        elif value in (None, ""):
            blocking.append(f"Missing required field: {field}")

    if not _is_safe_orchestration_id(iteration["orchestration_id"]):
        blocking.append("orchestration_id is path-unsafe")
    if iteration["planner_model_tier"] and iteration["planner_model_tier"] not in ALLOWED_PLANNER_TIERS:
        blocking.append("planner_model_tier must be L3 or L4")
    if iteration["verdict"] and iteration["verdict"] not in ALLOWED_VERDICTS:
        blocking.append(f"Unsupported verdict: {iteration['verdict']}")
    if not iteration["forum_thread_ref"]:
        blocking.append("forum_thread_ref is required")
    if not iteration["parent_task_id"] and not iteration["requirement_id"]:
        blocking.append("parent_task_id or requirement_id is required")
    if iteration["owner_decision_required"] and not iteration["needs_owner_reasons"]:
        blocking.append("owner_decision_required requires needs_owner_reasons")
    return iteration, blocking


def render_iteration_entry(iteration: dict[str, Any]) -> str:
    ordered_scalars = [
        "iteration_id",
        "orchestration_id",
        "iteration_number",
        "planner_agent",
        "planner_agent_instance",
        "planner_model_tier",
        "started_at",
        "ended_at",
        "forum_thread_ref",
        "parent_task_id",
        "requirement_id",
        "active_session_id",
        "prior_session_id",
        "session_resume_ref",
        "role_continuity_preference",
        "observed_queue_state",
        "observed_subtask_summary",
        "next_check_after",
        "verdict",
        "owner_decision_required",
        "audit_handoff_required",
    ]
    list_fields = [
        "input_refs",
        "decisions",
        "created_subtasks",
        "updated_recommendations",
        "failure_observations",
        "quota_observations",
        "needs_owner_reasons",
    ]
    lines: list[str] = []
    for index, key in enumerate(ordered_scalars):
        prefix = "- " if index == 0 else "  "
        lines.append(f"{prefix}{key}: {_yaml_scalar(iteration.get(key))}")
    for key in list_fields:
        lines.append(f"  {key}:")
        for item in iteration.get(key, []):
            lines.append(f"    - {_yaml_scalar(item)}")
    return "\n".join(lines) + "\n"


def _snapshot_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": result.get("action"),
        "actor": result.get("actor"),
        "target_path": result.get("target_path"),
        "iteration_entry": result.get("iteration_entry"),
        "planned_writes": result.get("planned_writes", []),
        "blocking_reasons": result.get("blocking_reasons", []),
        "owner_confirmation_required": result.get("owner_confirmation_required", False),
    }


def snapshot_hash(result: dict[str, Any]) -> str:
    encoded = json.dumps(_snapshot_payload(result), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_iteration_append_plan(repo_root: Path, payload: dict[str, Any], *, actor: str | None = None) -> dict[str, Any]:
    iteration, blocking = _normalize_iteration(payload)
    actor_value = _safe_text(actor)
    planner_actor_values = {iteration.get("planner_agent"), iteration.get("planner_agent_instance")}
    planner_actor_values.discard("")
    if actor_value and actor_value not in planner_actor_values:
        blocking.append("actor must match planner_agent or planner_agent_instance")

    target_rel = _target_path_for(iteration.get("orchestration_id", "invalid")).as_posix()
    target_path = repo_root / target_rel
    planned_writes = [
        {
            "path": target_rel,
            "kind": "append",
            "type": "planner_iteration_log",
        }
    ]

    try:
        target_path.resolve().relative_to((repo_root / ORCHESTRATION_ROOT).resolve())
    except ValueError:
        blocking.append("target path is outside 5_tasks/orchestration")

    if target_path.exists() and target_path.is_file():
        text = target_path.read_text(encoding="utf-8")
        if _contains_iteration_id(text, iteration.get("iteration_id", "")):
            blocking.append(f"Duplicate iteration_id already exists: {iteration.get('iteration_id')}")
    elif target_path.exists():
        blocking.append(f"Planner iteration target exists but is not a file: {target_rel}")

    result: dict[str, Any] = {
        "action": "planner_iteration_append",
        "actor": actor_value or iteration.get("planner_agent_instance") or iteration.get("planner_agent"),
        "verdict": "BLOCK" if blocking else "PASS",
        "blocking_reasons": blocking,
        "warnings": [],
        "target_path": target_rel,
        "planned_writes": planned_writes,
        "iteration_entry": iteration,
        "append_markdown": render_iteration_entry(iteration),
        "owner_confirmation_required": iteration.get("verdict") == "needs_owner"
        or bool(iteration.get("needs_owner_reasons"))
        or bool(iteration.get("owner_decision_required")),
        "safety_notice": (
            "AIPOS-66 appends one planner iteration under 5_tasks/orchestration/. "
            "It does not write orchestration events, summary state, queue tasks, drafts, records, forum backends, "
            "session leases, runtime launches, or git state."
        ),
    }
    result["write_snapshot_hash"] = snapshot_hash(result)
    return result


def append_planner_iteration(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    actor: str | None = None,
    dry_run: bool = False,
    expected_hash: str | None = None,
) -> dict[str, Any]:
    result = build_iteration_append_plan(repo_root, payload, actor=actor)
    result["dry_run"] = dry_run
    result["would_write"] = result["verdict"] != "BLOCK"

    if dry_run:
        result["wrote"] = False
        return result

    if result["verdict"] == "BLOCK":
        result["wrote"] = False
        return result

    if not expected_hash:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = ["expected hash is required for non-dry-run append"]
        result["would_write"] = False
        result["wrote"] = False
        return result

    if expected_hash != result["write_snapshot_hash"]:
        result["verdict"] = "BLOCK"
        result["blocking_reasons"] = ["expected hash does not match current append plan"]
        result["would_write"] = False
        result["wrote"] = False
        return result

    target_path = repo_root / result["target_path"]
    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    with target_path.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        if existing:
            handle.write("\n")
        handle.write(result["append_markdown"])
    result["wrote"] = True
    return result
