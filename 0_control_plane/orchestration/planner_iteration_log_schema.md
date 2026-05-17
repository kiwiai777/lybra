# Planner Iteration Log Schema

## Purpose

This document defines the future planner iteration log for orchestration auditability.

Suggested future path:

```text
5_tasks/orchestration/{orchestration_id}/planner_iterations.md
```

## Entry Shape

Entries are append-only by policy.

Required fields:

```yaml
- iteration_id:
  orchestration_id:
  iteration_number:
  planner_agent:
  planner_model_tier:
  started_at:
  ended_at:
  input_refs: []
  observed_queue_state:
  observed_subtask_summary:
  decisions: []
  created_subtasks: []
  updated_recommendations: []
  failure_observations: []
  quota_observations: []
  needs_owner_reasons: []
  next_check_after:
  verdict: continue
```

Optional AIPOS-66 continuity and visibility fields:

```yaml
planner_agent_instance:
forum_thread_ref:
parent_task_id:
requirement_id:
active_session_id:
prior_session_id:
session_resume_ref:
role_continuity_preference:
owner_decision_required:
  audit_handoff_required:
  session_tree_refs:
  session_tree_recommendations:
  subtask_dag_refs:
  fanout_groups_proposed:
  join_gates_proposed:
  dag_validation_summary:
  autonomy_tier:
  autonomy_scope:
  autonomy_window_id:
  owner_confirm_required:
  owner_confirm_ref:
  autonomy_actions_taken:
  autonomy_blockers:
  autonomy_downgrade_triggered:
  autonomy_downgrade_reason:
```

Allowed `verdict` values:

```text
continue
draft_subtasks
publish_ready
wait_for_audit
repair
pause
needs_owner
blocked
complete
cancel
failed
```

## AIPOS-54 Tick Semantics

AIPOS-54 treats one planner iteration entry as one manual planner tick.

Each tick should record:

- what inputs were read
- what changed since the prior tick
- the single primary verdict
- whether Owner decision is required
- whether audit handoff is required
- which subtask drafts or publish candidates were identified
- which stop conditions were hit
- whether a Session Tree fork, rollback, or clone is recommended for Owner review

The tick must not silently create claimable work, publish drafts, launch runtimes, or mark an Owner-only decision as resolved.

## AIPOS-93 DAG Recommendations

Planner iteration entries may recommend a future subtask DAG shape using advisory fields:

```yaml
subtask_dag_refs:
  - dag_id:
    dag_ref:
    source_iteration_id:
fanout_groups_proposed:
  - fanout_group_id:
    member_node_ids: []
    publish_policy:
    owner_decision_required: false
join_gates_proposed:
  - join_gate_id:
    input_node_ids: []
    output_node_ids: []
    join_policy:
    owner_decision_required: false
dag_validation_summary:
  cycle_check: not_run | pass | fail
  missing_node_refs: []
  blocking_reasons: []
  owner_gate_reasons: []
```

These recommendations are not executable. They do not create `subtask_dag.md`, write `subtask_index.md`, publish drafts, mutate queues, launch runtimes, or expand controlled execute. Future persistence or scheduling requires a separate Owner-approved implementation task.

## AIPOS-94 Autonomy Evidence

Planner iteration entries may record autonomy tier evidence:

```yaml
autonomy_tier: A0
autonomy_scope: tick
autonomy_window_id:
owner_confirm_required: true
owner_confirm_ref:
autonomy_actions_taken: []
autonomy_blockers: []
autonomy_downgrade_triggered: false
autonomy_downgrade_reason:
```

These fields are audit evidence only. They do not authorize the planner to continue, write files, publish drafts, claim tasks, execute Session Tree operations, launch runtimes, commit, push, finalize, or bypass Owner gates.

Missing or ambiguous autonomy metadata means A0.

## Rules

- iteration log is append-only by policy
- planner must not rewrite previous iteration entries except by explicit correction entry
- iteration log is for auditability
- iteration log does not execute tasks
- iteration entries should point to existing queue/report/state artifacts rather than duplicate them

## Correction Entries

If an earlier iteration needs correction, append a new entry such as:

```yaml
- iteration_id: iter_orch_ai_project_os_20260428_board_ui_004_correction
  corrects_iteration_id: iter_orch_ai_project_os_20260428_board_ui_004
  verdict: continue
  decisions:
    - Corrected prior subtask classification from docs to validation.
```

This keeps the log append-only.

## AIPOS-62 Persistence Boundary

AIPOS-62 defines future writer rules in `planner_loop_writer_forum_persistence_boundary.md`.

Planner iteration persistence remains a future implementation. When implemented, iteration writes must be append-only by default, path-safe under `5_tasks/orchestration/{orchestration_id}/`, and tied to a visible planner tick payload. A writer must not use an iteration entry to create claimable work, publish drafts, move queue tasks, launch runtimes, or resolve Owner-only decisions.

## AIPOS-66 Append Writer

AIPOS-66 implements the first narrow planner iteration writer as a CLI-only append operation:

```text
python3 tools/aipos_cli/aipos_cli.py orchestration iteration append --from-json <payload> --actor <actor> --dry-run --json
python3 tools/aipos_cli/aipos_cli.py orchestration iteration append --from-json <payload> --actor <actor> --expected-hash <write_snapshot_hash> --json
```

The writer appends one entry to:

```text
5_tasks/orchestration/{orchestration_id}/planner_iterations.md
```

Required AIPOS-66 writer checks:

- explicit JSON payload
- path-safe `orchestration_id`
- target path remains under `5_tasks/orchestration/{orchestration_id}/planner_iterations.md`
- required iteration fields are present
- `planner_model_tier` is L3 or L4
- `verdict` is one of the allowed values in this schema
- `forum_thread_ref` is present
- `parent_task_id` or `requirement_id` is present
- CLI `--actor` matches `planner_agent` or `planner_agent_instance`
- duplicate `iteration_id` is blocked
- dry-run returns planned writes and `write_snapshot_hash`
- non-dry-run append requires matching `--expected-hash`

AIPOS-66 may record `active_session_id`, `prior_session_id`, `session_resume_ref`, and `role_continuity_preference` when the planner tick payload includes them. These fields are visible continuity hints only. They do not resume a Codex, Claude, or other runtime session; they do not create a session lease; and they do not bypass matching, claim, review, audit, or Owner gates.

AIPOS-66 does not write `orchestration_events.md`, `loop_state.md`, `orchestration_state.md`, `subtask_index.md`, `artifact_links.md`, queue files, draft files, records, project docs, shared memory, or git state.

## AIPOS-91 Session Tree Recommendations

Planner iteration entries may recommend a future Session Tree operation using advisory fields:

```yaml
session_tree_recommendations:
  - operation: session_fork | session_rollback | session_clone
    source_session_id:
    source_session_node_id:
    target_session_node_id:
    rollback_target_session_id:
    reason:
    owner_decision_required: true
    refs: []
```

These recommendations are not executable. They do not create session nodes, append `session_tree_event`, mutate queues, create records, launch runtimes, or change git state. Future execution requires a separate Owner-approved controlled execute implementation.
