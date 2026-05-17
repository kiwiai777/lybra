# Start Task Session Preview

## Purpose

This document defines the Board v0 Start Task Session Preview flow.

## Definition

Start Task Session Preview is:

> A pre-claim or pre-execution Board v0 confirmation view that shows the selected task's execution envelope and validates whether the task can safely enter a Task Session.

## Core Clarifications

- Preview is not execution.
- Preview is not a database record.
- Preview does not itself claim unless the user or agent confirms.
- Preview may generate proposed `session_id` and `claim_id`.
- Preview must bind to exactly one selected task.
- Preview must not include unrelated tasks assigned to the same agent.

## Entry Points

Preview entry points:

- `My Tasks -> Start Task Session`
- `Task Queue -> Start Task Session`
- `Blocked Task -> Resume / Reopen Session Preview`
- `Manual Task Card -> Preview Session`
- `Remote Polling Agent -> Generate Preview Before Claim`

Clarifications:

- local manual agents use preview before copying context into Codex, Claude Code, or local CLI
- remote agents use preview internally before claiming and executing
- preview must work file-first without a database

## Preview Inputs

Preview should read:

- selected task card
- task frontmatter
- task markdown body
- `5_tasks/runtime_metadata_schema.md`
- `5_tasks/claim_event_schema.md`
- `5_tasks/task_state_transition_policy.md`
- `0_control_plane/tasks/task_session_policy.md`
- `0_control_plane/tasks/task_session_schema.md`
- `0_control_plane/tasks/context_isolation_policy.md`
- `3_context_bundles/`
- `0_control_plane/agents/`
- `0_control_plane/roles/`
- `0_control_plane/environments/`
- `1_shared_memory/`
- `4_inbox/`

Preview should not load unrelated task bodies unless explicitly referenced by:

- `memory_refs`
- `artifact_links`
- `input_refs`
- `related_sessions`
- Owner instruction

## Preview Display Fields

Preview must display at least:

- `task_id`
- `title`
- `project`
- `status`
- `assigned_to`
- `agent_instance`
- `task_mode`
- `model_tier`
- `context_bundle`
- `priority`
- `claim_policy`
- `polling_mode`
- `report_mode`
- `output_target`
- `artifact_policy`
- `session_policy`
- `context_isolation`
- `artifact_scope`
- `memory_scope`
- `claim_id` proposed or current
- `active_session_id` current
- `last_session_id` current
- `session_id` proposed or current
- `needs_owner`
- `risk_level` if present
- `approval_required` if present

Preview should also display:

- task file path
- queue directory
- frontmatter status
- directory/status consistency
- missing required fields
- blocking errors
- warnings
- recommended next action

## Proposed ID Generation

Recommended `session_id`:

`session_{task_id}_{YYYYMMDD_HHMMSS}_{agent_slug}`

Recommended `claim_id`:

`claim_{task_id}_{YYYYMMDD_HHMMSS}_{agent_slug}`

Clarifications:

- preview may propose IDs before writing them
- IDs become active only after claim or session start is confirmed
- if a task already has `active_session_id`, preview must warn or block depending on state
- if `claim_id` exists on a pending task, preview must flag stale or inconsistent metadata

## Preview Output Object

Example preview output object:

```yaml
preview_id:
task_id:
task_path:
queue_state:
frontmatter_status:
status_consistent:
current_actor:
assigned_to:
agent_instance:
can_start_session:
verdict: PASS | WARN | BLOCK | NEEDS_OWNER
blocking_reasons:
warnings:
needs_owner_reasons:
proposed_session_id:
proposed_claim_id:
session_policy:
context_isolation:
artifact_scope:
memory_scope:
output_target:
artifact_policy:
copy_context_allowed:
claim_allowed:
run_locally_allowed:
recommended_action:
created_at:
```

Clarifications:

- this object may be rendered transiently
- Board v0 does not need to persist preview objects
- future implementation may save preview logs, but this spec does not require that

## Board Integration

Board v0 should use preview as the safety gate before any claim, local run, or remote polling execution.

Preview powers:

- Start Task Session
- Copy Task Context
- My Tasks claim eligibility
- Needs Owner routing
- future Activity Feed preview or claim events

## File-driven Boundaries

Preview reads files and policies.

Preview may generate a transient preview object in memory.

Preview does not write queue state or runtime metadata unless claim or session start is confirmed.

Preview does not require a database, runtime executor, or scheduler.

## Anti-confusion Rules

- One preview equals one selected task.
- One Task Session equals one selected task.
- Copy Task Context copies one selected task only.
- Do not merge multiple pending tasks into one copied context.
- Do not copy another project's memory unless referenced.
- Do not claim a task merely because the same agent appears in another task.
- Do not execute from a WARN preview without acknowledging warnings.
- Do not execute from a BLOCK preview.
- Do not execute from a NEEDS_OWNER preview without Owner approval.

## Future Implementation Notes

Future implementation options may include:

- CLI preview command
- Board web preview panel
- TUI preview screen
- preview JSON cache
- preview audit log
- session record file
- claim log file
- validator integration

This document does not implement those features.
