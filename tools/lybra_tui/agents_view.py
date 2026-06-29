"""AIPOS-234 — /agents minimal monitoring: a pure, read-only projection of queue truth.

`/agents` is a read-only SNAPSHOT: it groups the rows the existing `lybra_queue_list` read already
returns (each carries `queue_state` / `assigned_to` / `agent_instance` / `claimed_by`, see
`task_loader.py:80-85`) BY the owning agent. It is a projection of RECORDED truth — there is no
live presence, no polling, no heartbeat, no auto-refresh (gate-not-engine; see
`agent_profiles.py`: "Lybra does not track live agent presence or heartbeat state").

This module is pure stdlib (NO textual import) so the projection is unit-testable and the textual
dependency stays confined to `app.py`.

Grouping key (AIPOS-234 R-1): the owning canonical instance, taken from `claimed_by` else
`agent_instance`. The gate canonicalizes the instance id at claim time (Slice-5 canonical-instance
identity), so the recorded `claimed_by`/`agent_instance` IS the canonical instance — grouping on it
needs no extra resolution and stays a pure client (no profiles read, no new gate tool). When the
intended `assigned_to` diverges from the owning instance, BOTH are shown (a meaningful truth, never
silently collapsed). Tasks with no owning instance fall into an explicit `unassigned` bucket. Every
input row appears in exactly one group (faithful partition — no double-count, no drop).
"""

from __future__ import annotations

from typing import Any

UNASSIGNED = "unassigned"
NOT_LIVE_LABEL = (
    "as recorded (the queue's last-known recorded state) — "
    "Lybra does not track live presence"
)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _owning_instance(task: dict[str, Any]) -> str:
    """The canonical owning instance: claimed_by, else agent_instance, else '' (unassigned)."""
    return _text(task.get("claimed_by")) or _text(task.get("agent_instance"))


def aggregate_agents(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group queue rows by owning instance. Faithful partition: each task appears exactly once.

    Returns an ordered list of groups: ``[{"agent": <key>, "tasks": [entry, ...]}, ...]`` where the
    ``unassigned`` bucket, if any, is placed last. Each entry echoes the row's RECORDED fields only
    (never a fabricated/inferred state); recorded timestamps are surfaced only when present.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for task in tasks:
        owner = _owning_instance(task) or UNASSIGNED
        if owner not in groups:
            groups[owner] = []
            order.append(owner)
        metadata = task.get("metadata") or {}
        assigned_to = _text(task.get("assigned_to"))
        entry = {
            "task_id": _text(task.get("task_id")),
            "queue_state": _text(task.get("queue_state")),
            "assigned_to": assigned_to,
            # divergence is meaningful truth — surfaced, never silently collapsed (R-1)
            "divergence": bool(assigned_to and owner != UNASSIGNED and assigned_to != owner),
        }
        # recorded-only extras: include ONLY when present on the row (R-4 — never fabricate)
        for key in ("audit_readiness", "claimed_at", "returned_at"):
            value = _text(task.get(key)) or _text(metadata.get(key))
            if value:
                entry[key] = value
        groups[owner].append(entry)

    ordered_keys = [k for k in order if k != UNASSIGNED]
    if UNASSIGNED in groups:
        ordered_keys.append(UNASSIGNED)
    return [{"agent": key, "tasks": groups[key]} for key in ordered_keys]


def render_agents(tasks: list[dict[str, Any]]) -> str:
    """Render the read-only /agents snapshot table (pure text)."""
    grouped = aggregate_agents(tasks)
    lines = [f"/agents — {NOT_LIVE_LABEL}"]
    if not grouped:
        lines.append("  (no tasks in the active project's queue)")
        return "\n".join(lines)
    for group in grouped:
        lines.append(f"\n{group['agent']}  ({len(group['tasks'])} task(s)):")
        for entry in group["tasks"]:
            parts = [f"  - {entry['task_id'] or '(no id)'} [{entry['queue_state'] or '?'}]"]
            if entry.get("divergence"):
                parts.append(f"assigned_to={entry['assigned_to']}≠owner")
            for key in ("audit_readiness", "claimed_at", "returned_at"):
                if entry.get(key):
                    parts.append(f"{key}={entry[key]}")
            lines.append(" ".join(parts))
    return "\n".join(lines)
