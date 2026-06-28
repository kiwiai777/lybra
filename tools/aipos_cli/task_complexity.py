from __future__ import annotations

from typing import Any

ALLOWED_TASK_CLASSES = {"simple", "complex"}
CODE_TASK_MODES = {"code", "coding"}
AUDIT_PASS_VALUES = {"pass", "passed"}
DEPENDENCY_CONDITIONS = {"executor_completion", "audit_readiness", "audit_pass"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if value in (None, ""):
        return []
    return [_text(value)]


def effective_task_class(metadata: dict[str, Any]) -> str:
    raw = _lower(metadata.get("task_class"))
    return raw or "simple"


# AIPOS-232 — execution-layer workflow role counts. These are LABELS for the
# accountability template (executor-only vs executor + independent auditor), NOT
# an engine: nothing here runs, schedules, or launches an agent (gate-not-engine).
ONE_ROLE = "1-role"
TWO_ROLE = "2-role"


def suggest_workflow_roles(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pure heuristic hint (NOT enforcement) for 1-role vs 2-role.

    Reads only the EXISTING ``task_class`` complexity signal: complex -> suggest
    2-role (independent audit recommended); otherwise -> suggest 1-role.

    Honesty (AIPOS-232 R-3): ``task_class`` is a *complexity tier*, not a *task
    type*, so this only APPROXIMATES "doc/config vs ops/design/code" and is not
    precise. The Owner always decides; there is NO auto-select and this never
    mutates the card (``auto_selected`` is always False). Pure function: same
    input -> same output, no side effect, no stored state, no background work.
    """
    task_class = effective_task_class(metadata)
    if task_class == "complex":
        suggested = TWO_ROLE
        rationale = (
            "complex-class -> independent audit recommended (executor + distinct auditor)"
        )
    else:
        suggested = ONE_ROLE
        rationale = (
            "non-complex-class -> single role acceptable; choose 2-role explicitly "
            "if an independent audit is wanted"
        )
    return {
        "suggested_workflow": suggested,
        "suggested_role_count": 2 if suggested == TWO_ROLE else 1,
        "suggestion_basis": "task_class",
        "suggestion_is_heuristic": True,
        "suggestion_rationale": rationale,
        "auto_selected": False,
    }


def complexity_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("task_class")
    return {
        "task_class": raw,
        "effective_task_class": effective_task_class(metadata),
        "task_class_explicit": raw not in (None, ""),
        "complexity_note": metadata.get("complexity_note"),
        # AIPOS-232: advisory-only role-count suggestion; never auto-applied.
        "workflow_suggestion": suggest_workflow_roles(metadata),
    }


def validate_task_complexity(
    metadata: dict[str, Any],
    *,
    enforce_dependency_gate: bool,
) -> dict[str, list[str]]:
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    needs_owner_reasons: list[str] = []
    raw_class = _lower(metadata.get("task_class"))
    task_class = effective_task_class(metadata)
    task_mode = _lower(metadata.get("task_mode"))

    if raw_class and raw_class not in ALLOWED_TASK_CLASSES:
        blocking_reasons.append("task_class must be simple or complex")
        return {
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "needs_owner_reasons": needs_owner_reasons,
        }

    if task_mode in CODE_TASK_MODES and task_class == "simple":
        if raw_class:
            warnings.append("Code-mode task is explicitly classified simple; review whether complex-class governance is required")
        else:
            warnings.append("Code-mode task omits task_class and defaults to simple; review whether complex-class governance is required")

    if task_class != "complex":
        return {
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "needs_owner_reasons": needs_owner_reasons,
        }

    planner_agent = _text(metadata.get("planner_agent"))
    assigned_to = _text(metadata.get("assigned_to"))
    reviewer = _text(metadata.get("reviewer"))
    audit_by = _text(metadata.get("audit_by"))
    if not planner_agent:
        blocking_reasons.append("Complex-class task missing planner_agent")
    if not reviewer:
        blocking_reasons.append("Complex-class task missing reviewer")
    if not audit_by:
        blocking_reasons.append("Complex-class task missing audit_by")
    if planner_agent and reviewer and planner_agent == reviewer:
        blocking_reasons.append("Complex-class planner_agent must not equal reviewer")
    if planner_agent and audit_by and planner_agent == audit_by:
        blocking_reasons.append("Complex-class planner_agent must not equal audit_by")
    if assigned_to and reviewer and assigned_to == reviewer:
        blocking_reasons.append("Complex-class assigned_to must not equal reviewer")
    if assigned_to and audit_by and assigned_to == audit_by:
        blocking_reasons.append("Complex-class assigned_to must not equal audit_by")

    orchestration = metadata.get("orchestration")
    if isinstance(orchestration, dict) and orchestration.get("enabled") is True:
        assignment_status = _lower(orchestration.get("planner_assignment_status"))
        if assignment_status == "active":
            if not _text(orchestration.get("continuity_planner_agent")):
                blocking_reasons.append("Complex-class active orchestration missing continuity_planner_agent")
            if not _text(orchestration.get("continuity_planner_agent_instance")):
                blocking_reasons.append("Complex-class active orchestration missing continuity_planner_agent_instance")

    depends_on = _as_list(metadata.get("depends_on"))
    if enforce_dependency_gate and depends_on:
        dependency_condition = _lower(metadata.get("dependency_condition"))
        if dependency_condition not in DEPENDENCY_CONDITIONS:
            blocking_reasons.append(
                "Complex-class dependent task requires dependency_condition: executor_completion, audit_readiness, or audit_pass"
            )
        elif dependency_condition == "executor_completion":
            dependency_executor_status = _lower(metadata.get("dependency_executor_status"))
            if dependency_executor_status != "completed":
                blocking_reasons.append(
                    "Complex-class dependent task is blocked until dependency_executor_status is completed"
                )
        elif dependency_condition == "audit_readiness":
            dependency_audit_readiness = _lower(metadata.get("dependency_audit_readiness"))
            if dependency_audit_readiness != "ready":
                blocking_reasons.append(
                    "Complex-class dependent task is blocked until dependency_audit_readiness is ready"
                )
        else:
            dependency_audit_status = _lower(metadata.get("dependency_audit_status"))
            if dependency_audit_status not in AUDIT_PASS_VALUES:
                blocking_reasons.append("Complex-class dependent task is blocked until dependency_audit_status is PASS")

    return {
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "needs_owner_reasons": needs_owner_reasons,
    }
