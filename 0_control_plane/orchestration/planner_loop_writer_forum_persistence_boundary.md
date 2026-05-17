# Planner Loop Writer and Forum Event Persistence Boundary

## Purpose

AIPOS-62 defines the safety boundary for future persistence of planner loop output and forum-visible orchestration events.

This document closes the gap between AIPOS-54 planner tick preview and a future approved writer. It does not implement a writer, forum backend, CLI command, Web UI behavior, database, server, deployment configuration, queue polling runtime, planner runtime launcher, or autonomous planner loop.

## Boundary Principle

Planner loop persistence must remain append-oriented, reviewable, and reconstructable.

Future persistence may record:

```text
planner tick preview -> approved persistence writer -> append-only orchestration files
```

Future persistence must not:

```text
planner tick preview -> hidden task creation
planner tick preview -> draft publish
planner tick preview -> queue movement
planner tick preview -> owner decision resolution
planner tick preview -> planner runtime launch
```

## Future Writer Scope

A future approved writer may write only within:

```text
5_tasks/orchestration/{orchestration_id}/
```

Allowed future target files:

```text
planner_iterations.md
orchestration_events.md
loop_state.md
orchestration_state.md
subtask_index.md
artifact_links.md
```

The first future writer should be narrower than the full list. A safe first slice should append planner iteration entries and orchestration event entries only, then derive or update summary state in a later task after audit.

## Source Of Truth

Task cards and queue directories remain the source of truth for individual task state.

The orchestration persistence layer is a visibility and repairability layer. It records planner-visible decisions, event history, and rebuildable summaries. It must not replace:

- pending, claimed, blocked, completed queue paths
- task card frontmatter
- draft task cards
- claim/session records
- audit reports
- Owner decisions

## Required Inputs

Future persistence must be driven by an explicit payload equivalent to the AIPOS-60 planner tick preview output:

```yaml
planner_iteration:
visible_report:
event_log_preview:
orchestration_id:
parent_task_id:
forum_thread_ref:
planner_agent:
planner_agent_instance:
planner_model_tier:
decision:
decision_reason:
next_expected_action:
owner_decision_required:
needs_owner_reasons: []
```

The writer must not infer missing Owner decisions, missing planner assignment, missing model tier, or missing forum/control-plane references.

## Required Preconditions

Future persistence may proceed only when all checks pass:

- `orchestration_id` is present and path-safe
- target path resolves under `5_tasks/orchestration/{orchestration_id}/`
- parent task or parent requirement reference is present
- `forum_thread_ref` or equivalent control-plane visibility reference is present
- planner model tier is L3/L4 for planning decisions
- planner assignment is visible and task-scoped
- event types are allowed by `orchestration_event_log_schema.md`
- event severities are allowed by `orchestration_event_log_schema.md`
- planner verdict is allowed by `planner_iteration_log_schema.md`
- persistence is append-only for iteration and event logs
- no generated entry duplicates an existing `iteration_id` or `event_id`
- no task queue, draft, records, project, shared-memory, or git path is included in planned writes
- Owner decision gates are preserved when `owner_decision_required` or `needs_owner_reasons` are present

## Dry-Run And Revalidation

Future writer implementation must follow controlled execute discipline:

- dry-run preview before write
- planned writes listed explicitly
- snapshot hash or equivalent input hash
- execute-time revalidation
- actor match check
- safe path resolution
- no overwrite unless the target is an explicitly allowed summary file and the update is derived from append-only sources
- clear `execute_allowed` flag
- clear blocking reasons and Owner confirmation requirements

AIPOS-62 does not add this operation to the current controlled execute allowlist.

## Append-Only Logs

These files must be append-only by default:

```text
planner_iterations.md
orchestration_events.md
```

Correction must be represented as a new correction entry, not by rewriting prior entries.

Allowed append behavior:

- append one planner iteration entry for one planner tick
- append one or more orchestration event entries linked to that tick
- append a correction entry referencing the corrected entry

Forbidden append behavior:

- silently deleting prior entries
- mutating a prior verdict
- marking Owner-only decisions resolved without Owner decision evidence
- inventing queue state transitions not backed by queue state or records

## Summary State Files

These files are summary or index targets:

```text
loop_state.md
orchestration_state.md
subtask_index.md
artifact_links.md
```

Future updates to summary files must be reconstructable from task cards, queue state, records, planner iteration log, orchestration event log, and reports.

Summary file writes are higher risk than append-only log writes. They should be implemented after append-only persistence passes audit, unless Owner explicitly approves a combined writer scope.

## Forum Visibility Boundary

AIPOS forum visibility may initially be file-backed rather than a networked forum backend.

Allowed future first-phase interpretation:

- `forum_thread_ref` is a visible control-plane reference
- orchestration event entries include `refs` pointing to the forum/control-plane thread
- planner tick reports are visible through local Board UI or file-backed orchestration logs

Forbidden in AIPOS-62:

- network forum posting
- Slack/Discord/GitHub/remote forum integration
- webhook delivery
- background event broadcaster
- database-backed forum service
- auth/RBAC implementation

Any networked forum backend requires a separate Owner-approved architecture decision and audit.

## Owner Decision Gates

Future persistence must preserve Owner gates.

When a planner tick emits `owner_decision_required: true`, `decision: needs_owner`, or non-empty `needs_owner_reasons`, the writer may record the state but must not:

- resolve the Owner decision
- publish drafts
- create claimable work
- mark orchestration completed
- mark high-risk paths approved
- bypass reviewer, auditor, publish, claim, session lease, or finalize gates

Owner decision evidence should be recorded as a later explicit event, such as `owner_decision_recorded`.

## Relationship To Existing AIPOS Work

- AIPOS-54 defines planner tick semantics.
- AIPOS-60 previews planner tick and event log metadata without writing files.
- AIPOS-61 reviews planner draft publish readiness and hands off to the existing `draft_publish` controlled execute path.
- AIPOS-62 defines persistence boundaries for a future writer but does not implement that writer.
- AIPOS-65 implements the first narrow append-only orchestration event writer for `orchestration_events.md` only. It does not implement planner iteration persistence, summary state, Web UI execution, forum backend, planner runtime launch, queue polling, or autonomous execution.
- AIPOS-66 implements a narrow append-only planner iteration writer for `planner_iterations.md` only. It does not implement orchestration event writing, summary state, Web UI execution, forum backend, session resume or lease writing, planner runtime launch, queue polling, or autonomous execution.
- AIPOS-67 decides that summary state writing remains deferred. The next safe summary-state slice should be a dry-run rebuild preview, not a write operation.

## AIPOS-66 Planner Iteration Writer Boundary

AIPOS-66 may append exactly one planner iteration entry under:

```text
5_tasks/orchestration/{orchestration_id}/planner_iterations.md
```

The writer must use explicit payload input, dry-run preview, planned writes, snapshot hash revalidation, actor matching, duplicate `iteration_id` detection, L3/L4 planner tier enforcement, allowed planner verdicts, path-safe target resolution, and Owner gate preservation.

Planner iteration entries may preserve advisory continuity metadata:

```yaml
active_session_id:
prior_session_id:
session_resume_ref:
role_continuity_preference:
```

These fields are visibility and preference metadata only. They do not resume an agent conversation, launch a runtime, create or renew a session lease, pin coder/reviewer/auditor assignments, or bypass AIPOS-48 matching, AIPOS-50 session lease binding, independent review, audit, or Owner decision gates.

## AIPOS-67 Summary State Scope Decision

AIPOS-67 keeps these files out of the current writer surface:

```text
loop_state.md
orchestration_state.md
subtask_index.md
artifact_links.md
```

Summary files are reconstructable views. They must not become the source of truth for task state, claim state, session state, audit results, or Owner decisions.

The recommended next implementation slice is:

```text
orchestration summary dry-run preview -> no writes -> no execute token
```

When a writer is later approved, the first summary writer should target only `orchestration_state.md`. `loop_state.md`, `subtask_index.md`, and `artifact_links.md` remain deferred until the summary preview contract is stable.

Future summary preview or writer logic must derive from queue task cards, queue directory state, records, append-only `planner_iterations.md`, append-only `orchestration_events.md`, reports, and explicit Owner decision evidence. If those sources disagree, the operation must surface the conflict instead of silently overwriting summary state.

## Non-Goals

AIPOS-62 does not implement:

- planner loop writer
- orchestration writer
- forum backend
- forum network posting
- Web UI changes
- CLI command changes
- controlled execute allowlist expansion
- planner runtime launch
- queue polling
- autonomous execution
- task movement
- draft creation or publish automation
- records writer changes
- project/shared-memory writer
- database
- server
- deployment configuration
- auth/RBAC
- git operations
