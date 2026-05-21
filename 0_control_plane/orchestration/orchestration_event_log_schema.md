# Orchestration Event Log Schema

## Purpose

This document defines the future orchestration event log.

Suggested future path:

```text
5_tasks/orchestration/{orchestration_id}/orchestration_events.md
```

## Event Types

```text
orchestration_created
planner_assigned
planner_tick_started
planner_tick_completed
planner_paused
planner_resumed
planner_verdict_recorded
subtask_created
subtask_draft_proposed
subtask_publish_ready
subtask_claimed
subtask_completed
subtask_blocked
review_submitted
repair_requested
quota_warning
quota_exhausted
runtime_unavailable
needs_owner_raised
owner_decision_recorded
audit_handoff_requested
handoff_recommended
handoff_approved
session_tree_event
orchestration_completed
orchestration_cancelled
orchestration_failed
```

## Event Shape

Entries are append-only by policy.

Required fields:

```yaml
- event_id:
  orchestration_id:
  event_type:
  timestamp:
  actor:
  source:
  related_task_id:
  related_subtask_id:
  related_iteration_id:
  severity: info
  summary:
  details:
  refs: []
```

Allowed `severity` values:

```text
info
warning
needs_owner
blocking
```

## Rules

- event log is append-only by policy
- event log may be derived from queue records and reports in future
- event log does not replace task reports
- event entries should stay compact and point back to detailed artifacts through `refs`

## AIPOS-91 Session Tree Event

AIPOS-91 reserves `session_tree_event` as the append-only event type for future Session Tree primitives:

```text
session_fork
session_rollback
session_clone
```

Recommended `details.session_tree_event` payload:

```yaml
tree_event_id:
operation:
session_tree_id:
source_session_id:
source_session_node_id:
target_session_node_id:
rollback_target_session_id:
rollback_target_session_node_id:
branch_status:
branch_reason:
branch_scope:
owner_confirmed:
owner_confirmation_token:
dry_run_id:
dry_run_snapshot_hash:
source_refs: []
```

`session_tree_event` is metadata only in AIPOS-91. It does not create a writer, mutate queues, create records, launch runtimes, copy credentials, roll back files, or change git state.

## Recommended Sources

Future event writers or readers may derive events from:

- queue transitions
- planner iteration entries
- audit reports
- Owner decisions
- manual orchestration state updates

Even when derived, append-only event history should be preserved for auditability.

## AIPOS-62 Persistence Boundary

AIPOS-62 defines future writer rules in `planner_loop_writer_forum_persistence_boundary.md`.

Orchestration event persistence remains a future implementation. When implemented, event writes must be append-only by default, use only allowed event types and severities from this schema, preserve `refs` back to forum/control-plane evidence, and avoid hidden side effects. Networked forum posting, webhooks, database-backed forum services, and background broadcasters require separate Owner-approved implementation tasks.

## AIPOS-111 Owner Decision Record

AIPOS-111 defines the `owner_decision_record` protocol in `0_control_plane/board/owner_decision_record_protocol.md`.

Future `owner_decision_recorded` events may cite an Owner decision record, but this schema does not implement the writer. Recording an Owner decision must not publish drafts, mutate queues, launch runtimes, bypass controlled execute, or resolve unrelated Owner gates.

## AIPOS-65 Append-Only Writer

AIPOS-65 implements a narrow CLI append writer for this schema.

The writer may create or append only:

```text
5_tasks/orchestration/{orchestration_id}/orchestration_events.md
```

It requires an explicit JSON payload, path-safe `orchestration_id`, allowed event type and severity, `forum_thread_ref`, actor match, duplicate `event_id` check, dry-run `write_snapshot_hash`, and matching non-dry-run `--expected-hash`.

AIPOS-65 does not write planner iterations, summary state files, queue files, drafts, records, forum backend posts, or network events.
