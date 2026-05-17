# Planner Loop State Schema

## Purpose

This document defines the future loop state document format for planner-orchestrated work.

AIPOS-25 does not create live loop state records. The suggested future path is:

```text
5_tasks/orchestration/{orchestration_id}/loop_state.md
```

After AIPOS-27, this loop-state schema should be read together with:

- `0_control_plane/orchestration/orchestration_state_schema.md`
- `0_control_plane/orchestration/subtask_index_schema.md`
- `0_control_plane/orchestration/planner_iteration_log_schema.md`
- `0_control_plane/orchestration/orchestration_event_log_schema.md`

`planner_loop_state_schema.md` focuses on planner-facing loop progress. `orchestration_state_schema.md` is the broader summary/index record. `subtask_index`, planner iteration log, and orchestration event log carry the durable rebuildable views that make the state repairable.

## Loop State Frontmatter

```yaml
---
orchestration_id:
parent_task_id:
status: planning
current_iteration: 0
max_iterations:
max_open_subtasks:
max_subtasks_total:
open_subtasks: []
completed_subtasks: []
blocked_subtasks: []
failed_subtasks: []
last_planner_run_at:
next_planner_check_after:
runtime_state_refs: []
quota_state:
  summary: unknown
  last_checked_at:
  status_provider_refs: []
failure_state:
  active_failures: []
  repeated_failure_count: 0
needs_owner_reasons: []
stop_condition_hits: []
planner_autonomy:
  autonomy_tier: A0
  autonomy_status: proposed
  autonomy_scope: tick
  owner_confirm_required: true
  owner_confirm_ref:
  downgrade_to: A0
  downgrade_triggers: []
  current_autonomy_window_id:
  autonomy_downgrade_reason:
---
```

## AIPOS-54 Minimal Tick Fields

AIPOS-54 defines the minimum visible planner tick shape for manual MVP operation:

```yaml
last_tick_id:
last_tick_verdict:
last_tick_actor:
last_tick_model_tier:
combined_planner_executor: false
last_inputs_read: []
last_observations: []
last_decision_reason:
next_expected_action:
owner_decision_required: false
publish_candidates: []
repair_recommendations: []
audit_handoff_needed: false
autonomy_tier: A0
autonomy_scope: tick
owner_confirm_required: true
autonomy_downgrade_triggered: false
```

These fields are future state targets only. AIPOS-54 does not write loop state files.

## State Fields

- `orchestration_id`: Stable ID such as `orch_ai_project_os_20260428_board_ui`.
- `parent_task_id`: Parent task card ID.
- `status`: One of `planning`, `running`, `paused`, `blocked`, `needs_owner`, `completed`, `cancelled`, or `failed`.
- `current_iteration`: Current planner iteration number.
- `max_iterations`: Configured cap.
- `max_open_subtasks`: Configured open-subtask cap.
- `max_subtasks_total`: Configured lifetime-subtask cap.
- `open_subtasks`: Task IDs currently pending or claimed.
- `completed_subtasks`: Task IDs completed and accepted.
- `blocked_subtasks`: Task IDs blocked by dependency, executor, reviewer, quota, or Owner decision.
- `failed_subtasks`: Task IDs that failed terminally or exceeded retry policy.
- `last_planner_run_at`: Last time planner evaluated state.
- `next_planner_check_after`: Advisory next check time.
- `runtime_state_refs`: References to runtime status provider records or manual status notes.
- `quota_state`: Current configured quota/budget view.
- `failure_state`: Active interruption and repeated-failure state.
- `needs_owner_reasons`: Reasons waiting for Owner decision.
- `stop_condition_hits`: Stop conditions already triggered.
- `planner_autonomy`: Optional AIPOS-94 autonomy tier state. It is evidence and summary metadata only.

## AIPOS-94 Autonomy Metadata

AIPOS-94 adds optional autonomy metadata to planner loop state.

Loop state may summarize the current tier, autonomy window, Owner confirmation requirement, and downgrade reason. It must not authorize execution. Missing or ambiguous autonomy metadata means A0.

Autonomy tier above A0 must be backed by visible Owner approval evidence. If a failure threshold, Owner gate, dependency ambiguity, audit ambiguity, credential ambiguity, controlled execute failure, or external publish/commit/push/finalize request appears, the effective tier downgrades to A0.

## Runtime Budget State

Loop state may include advisory runtime budget information:

```yaml
quota_state:
  summary: ok | warning | exhausted | unknown
  active_windows:
    - quota_id: codex_5h
      status: ok | warning | exhausted | unknown
      reset_at:
      confidence: manual | estimated | provider_reported
    - quota_id: claude_code_5h
      status: ok | warning | exhausted | unknown
      reset_at:
      confidence: manual | estimated | provider_reported
```

Quota state is advisory unless the configured policy says otherwise. Missing provider data must become `unknown`, not a crash.

## Runtime Status Provider Extension Point

Future tools may write or reference runtime status provider records:

```yaml
runtime_status_provider:
  provider_id:
  provider_type: manual | local_file | api | service_dashboard | future_tool
  target_runtime_profiles: []
  polling_allowed: false
  polling_cadence:
  auth_required:
  data_fields:
    - availability_status
    - quota_remaining
    - quota_reset_at
    - rate_limit_state
    - login_state
    - network_state
  last_checked_at:
  confidence: manual | estimated | provider_reported
```

Current AIPOS does not fetch service websites, provider dashboards, or APIs. Future status fetching requires Owner approval and an explicit implementation task.

## Pause State

Pause reasons:

```text
quota_exhausted
quota_status_unknown
owner_requested
review_pending
executor_unavailable
too_many_failures
high_risk_decision
external_dependency
network_failure
login_state_invalid
```

Resume requirements:

```text
Owner approval
availability restored
quota restored
quota status manually acknowledged
blocked dependency resolved
review completed
network/login restored
```

## Iteration Records

Each planner iteration should be traceable:

```yaml
planner_iteration_id: iter_<orchestration_id>_<NN>
started_at:
completed_at:
planner_agent:
planner_model_tier: L3
inputs_read: []
decisions: []
subtasks_created: []
stop_condition_hits: []
needs_owner_reasons: []
```

The planner cannot silently discard iteration records. If an iteration fails, the failure belongs in `failure_state`.

Task cards remain source of truth for individual task state. Loop state is a repairable summary and should be reconstructable from queue state, subtask index, reports, and append-only logs.

## AIPOS-62 Persistence Boundary

AIPOS-62 defines future writer rules in `planner_loop_writer_forum_persistence_boundary.md`.

Loop state is a summary target, not the first preferred persistence surface. Future implementations should persist append-only planner iteration and orchestration event entries first, then update `loop_state.md` only when summary-state rewrite rules, reconstructability, and execute-time revalidation are explicitly approved.

## AIPOS-67 Scope Decision

AIPOS-67 keeps `loop_state.md` deferred.

The next summary-state implementation slice should preview rebuildable `orchestration_state.md` output before any loop-state writer is approved. `loop_state.md` should not be written until planner loop runtime semantics, pause/resume handling, quota state confidence, and failure-state rebuild rules are separately approved.
