# Orchestration State Schema

## Purpose

This document defines the future file-driven orchestration state record for AI Project OS.

Suggested future path:

```text
5_tasks/orchestration/{orchestration_id}/orchestration_state.md
```

AIPOS-27 defines protocol only. It does not create runtime records under `5_tasks/orchestration/`.

## Frontmatter

```yaml
---
orchestration_id:
parent_task_id:
title:
project:
status: planning
planner_agent:
planner_agent_instance:
planner_runtime_profile:
planner_model_tier:
planner_assignment_status:
planner_assignment_scope:
current_iteration:
max_iterations:
max_open_subtasks:
max_subtasks_total:
open_subtask_count:
completed_subtask_count:
blocked_subtask_count:
failed_subtask_count:
needs_owner:
needs_owner_reasons: []
stop_condition_hits: []
pause_reason:
resume_requirements: []
runtime_budget_policy_ref:
runtime_status_provider_refs: []
subtask_index_ref:
planner_iterations_ref:
orchestration_events_ref:
artifact_links_ref:
created_by:
created_at:
updated_at:
last_planner_run_at:
next_planner_check_after:
---
```

## Body Sections

Recommended sections:

- `## Purpose`
- `## Current Summary`
- `## Progress`
- `## Open Risks`
- `## Owner Notes`

## Field Semantics

- `orchestration_id`: Stable orchestration group identifier.
- `parent_task_id`: Parent orchestration task.
- `status`: One of `planning`, `running`, `paused`, `blocked`, `needs_owner`, `completed`, `cancelled`, or `failed`.
- `planner_*`: Task-scoped planner assignment metadata.
- `current_iteration`: Latest planner iteration number reflected in this summary.
- `max_*`: Configured orchestration limits.
- `*_subtask_count`: Summary counters derived from queue state and subtask index.
- `needs_owner`: Summary flag for current Owner attention state.
- `needs_owner_reasons`: Current Owner decision reasons.
- `stop_condition_hits`: Stop conditions already triggered.
- `pause_reason`: Current pause reason when paused.
- `resume_requirements`: Conditions that must be met before continuing.
- `runtime_budget_policy_ref`: Policy reference for budget/quota handling.
- `runtime_status_provider_refs`: Advisory provider refs only.
- `subtask_index_ref`: Future ref to `subtask_index.md`.
- `planner_iterations_ref`: Future ref to `planner_iterations.md`.
- `orchestration_events_ref`: Future ref to `orchestration_events.md`.
- `artifact_links_ref`: Future ref to `artifact_links.md`.

## Rules

- `orchestration_state` is a summary/index record, not a source of truth for individual task completion.
- Task cards remain source of truth for individual task state.
- Directory location remains operational state for tasks.
- `orchestration_state` must be repairable from task queue, `subtask_index`, and reports.
- Missing summary counts should be recomputable.
- State file should avoid embedding large artifacts; keep links only.

## AIPOS-67 Summary Writer Scope

AIPOS-67 keeps `orchestration_state.md` as a future summary target. It does not implement writing this file.

The next safe slice should be a dry-run rebuild preview that derives the proposed state from task cards, queue directories, records, append-only planner iterations, append-only orchestration events, reports, and explicit Owner decision evidence.

Future `orchestration_state.md` writing requires a separate approved implementation task and independent audit. The writer must preserve reconstructability, list source refs, revalidate input snapshots, preserve Owner gates, and surface conflicts instead of overwriting them.

If a future writer is approved, `orchestration_state.md` should be the first summary target. `loop_state.md`, `subtask_index.md`, and `artifact_links.md` should remain deferred until the orchestration state preview contract is stable.

## AIPOS-68 Dry-Run Preview

AIPOS-68 previews the proposed `orchestration_state.md` content as structured JSON only.

The preview may compute:

- current status
- current iteration
- subtask counts
- Owner attention state
- latest planner run time
- next planner check hint
- refs to append-only planner iteration and orchestration event logs

The preview must return `writes_enabled: false`, `execute_allowed: false`, and no dry-run token. It is a source inspection and rebuild plan, not a summary writer.

## Artifact Links

Artifact links may be kept inline or in a dedicated file:

```yaml
artifact_links:
  - artifact_id:
    label:
    url_or_path:
    artifact_type:
    related_task_id:
    related_iteration_id:
    permanence: temporary
    promoted_to_memory: false
```

Rules:

- large artifacts remain in external project repos, docs, or storage
- AIPOS stores links and indexes only
- temporary artifacts are not automatically promoted to shared memory
- Owner approval is required for memory promotion
