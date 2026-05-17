# Orchestration Summary State Writer Scope Decision

## Purpose

AIPOS-67 decides the safe next scope for orchestration summary state persistence after the append-only event and planner iteration writers.

This is a scope decision only. It does not implement a summary state writer, CLI command, Web UI behavior, controlled execute operation, forum backend, database, server, deployment configuration, queue polling runtime, planner runtime launcher, or autonomous planner loop.

## Decision

Do not implement a summary state writer in AIPOS-67.

The next safe implementation slice should be a dry-run summary rebuild preview, not a write operation.

Recommended next slice:

```text
read queue + read append-only orchestration logs -> build summary preview -> show planned summary state
```

Forbidden in AIPOS-67:

```text
summary preview -> write orchestration_state.md
summary preview -> overwrite loop_state.md
summary preview -> rewrite subtask_index.md
summary preview -> update artifact_links.md
summary preview -> move queue tasks
summary preview -> resolve Owner decisions
summary preview -> launch planner runtime
```

## Summary State Files

Summary state files are reconstructable views, not primary sources of truth.

Potential future files:

```text
5_tasks/orchestration/{orchestration_id}/orchestration_state.md
5_tasks/orchestration/{orchestration_id}/loop_state.md
5_tasks/orchestration/{orchestration_id}/subtask_index.md
5_tasks/orchestration/{orchestration_id}/artifact_links.md
```

AIPOS-67 keeps all four files read-only and unimplemented.

## Source Of Truth Order

Future summary generation must treat sources in this order:

1. queue task cards and queue directory state
2. draft task cards when explicitly referenced
3. claim and session records
4. append-only `planner_iterations.md`
5. append-only `orchestration_events.md`
6. completion reports and audit reports
7. existing summary state files, only as stale cache or repair input

If a summary file disagrees with task cards or queue directories, task cards and queue directories win.

If append-only logs disagree with queue state, the summary preview must surface the mismatch rather than silently pick one.

## Future Writer Choice

When Owner later approves a writer, the first summary writer should target only:

```text
orchestration_state.md
```

Rationale:

- it is the broadest human-readable summary
- it can point to append-only logs instead of duplicating them
- it can expose counts, latest iteration, Owner attention state, and next action
- it avoids prematurely choosing a subtask index serialization format

`loop_state.md`, `subtask_index.md`, and `artifact_links.md` should remain deferred until `orchestration_state.md` preview semantics are stable.

## Future Dry-Run Preview Requirements

A future preview operation should return:

```yaml
action: orchestration_summary_preview
orchestration_id:
verdict:
blocking_reasons: []
warnings: []
source_refs: []
planned_summary:
  target_path: 5_tasks/orchestration/{orchestration_id}/orchestration_state.md
  status:
  current_iteration:
  open_subtask_count:
  completed_subtask_count:
  blocked_subtask_count:
  failed_subtask_count:
  needs_owner:
  needs_owner_reasons: []
  last_planner_run_at:
  next_planner_check_after:
  planner_iterations_ref:
  orchestration_events_ref:
rebuild_notes: []
conflicts: []
writes_enabled: false
execute_allowed: false
```

The preview must not return a dry-run token for write confirmation until a later writer task explicitly approves summary writes.

## Future Writer Preconditions

A future `orchestration_state_write` operation requires a separate Owner-approved implementation task and independent audit.

Required checks:

- explicit orchestration id
- path-safe target under `5_tasks/orchestration/{orchestration_id}/orchestration_state.md`
- source refs listed and revalidated
- latest planner iteration id reflected
- event log references preserved
- queue counts derived from queue state, not guessed
- Owner decision gates preserved
- conflicts surfaced rather than hidden
- dry-run preview before write
- planned write listed explicitly
- snapshot hash or equivalent revalidation
- no queue, draft, records, event log, planner iteration log, project, shared-memory, or git writes
- no runtime launch

## Owner Decision Gates

Summary state may report an Owner decision requirement, but it must not resolve one.

Future preview or writer output must preserve:

- `needs_owner`
- `needs_owner_reasons`
- owner decision refs
- architecture, risk, scope, workflow, model-tier, agent-permission, audit-boundary, and implementation-boundary forks

If a conflict appears between summary state and Owner decision evidence, the operation should return `NEEDS_OWNER` or `BLOCK` and require human review.

## Relationship To Existing AIPOS Work

- AIPOS-62 defines the persistence boundary.
- AIPOS-65 appends orchestration events.
- AIPOS-66 appends planner iterations.
- AIPOS-67 keeps summary state writing deferred and selects preview/rebuild planning as the next safe slice.
- AIPOS-68 implements read-only orchestration summary dry-run preview without writing summary state.

## AIPOS-68 Preview Contract

AIPOS-68 may read:

- queue task cards and queue directory state
- claim and session records
- append-only `planner_iterations.md`
- append-only `orchestration_events.md`

AIPOS-68 must return:

```yaml
action: orchestration_summary_preview
orchestration_id:
verdict:
blocking_reasons: []
warnings: []
source_refs: []
planned_summary: {}
rebuild_notes: []
conflicts: []
dry_run: true
would_write: false
writes_enabled: false
execute_allowed: false
dry_run_token:
```

AIPOS-68 must not write `orchestration_state.md`, `loop_state.md`, `subtask_index.md`, `artifact_links.md`, queue files, draft files, records, project docs, shared memory, runtime state, or git state.

Conflicts between append-only logs, queue state, records, or Owner evidence must be surfaced in `conflicts` and must not be resolved by the preview.

## Non-Goals

AIPOS-67 does not implement:

- summary state writer
- `orchestration_state.md` writer
- `loop_state.md` writer
- `subtask_index.md` writer
- `artifact_links.md` writer
- generic `orchestration_write`
- Web UI behavior
- controlled execute allowlist expansion
- forum backend
- network posting
- session lease writer
- automatic session resume
- agent launcher
- planner loop runtime
- queue polling
- autonomous execution
- task movement
- draft creation or publish automation
- records writer changes
- database
- server
- deployment configuration
- auth/RBAC
- git operations
