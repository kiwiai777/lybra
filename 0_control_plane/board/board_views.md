# Board Views

## Purpose

This document defines the default views for Board v0 and how each view should interpret file-driven queue and report state.

## Task Queue

Task Queue shows the full formal queue:

- `pending`
- `claimed`
- `blocked`
- `completed`

Each task card should display at least:

- `task_id`
- `title`
- `project`
- `task_type`
- `assigned_to`
- `agent_instance`
- `context_bundle`
- `task_mode`
- `model_tier`
- `priority`
- `status`
- `schedule_hint`
- `recurrence`
- `created_by`
- `needs_owner`
- `output_target`
- `artifact_policy`

Future orchestration-aware Board views may show an orchestration badge when `orchestration.enabled == true` or `orchestration_parent == true`.

Recommended grouping:

- pending
- claimed
- blocked
- completed

## My Tasks

My Tasks shows tasks relevant to the current user or current agent instance.

Matching logic should include:

- `assigned_to == current_role_instance`
- OR `agent_instance == current_agent_instance`
- OR task-scoped `planner_agent == current_actor` for planner-visible orchestrated parent tasks

Avoid over-broad matching that would let unrelated roles claim tasks.

Planner matching is task-scoped. A planner assignment on one parent orchestration task must not make that actor match unrelated normal tasks.

Recommended groups:

- `available_to_claim`
- `claimed_by_me`
- `blocked_waiting_owner`
- `recently_completed`

`available_to_claim` should only show tasks whose queue state is `pending` and whose selectors match the current role or instance.

`claimed_by_me` should prefer explicit claim metadata such as `claimed_by` or the current task file location plus claim log.

### Local Agent Manual Trigger Path

My Tasks should reserve a local manual execution path for local non-24h agents.

Recommended actions:

- `Copy Task Context`
- `Run Locally`
- `Mark Claimed`
- `Mark Blocked`
- `Mark Completed`

`Copy Task Context` should copy:

- task card frontmatter
- task body
- `context_bundle` reference
- `assigned_to`
- `agent_instance`
- `task_mode`
- `model_tier`
- `output_target`
- `artifact_policy`
- `memory_refs`
- report instructions

`Run Locally` is a future hook only.

It may later map to:

- Codex
- Claude Code
- local CLI
- manual paste workflow

Board v0 should not implement real execution in this phase.

## Activity Feed

Activity Feed should be derived from file state, frontmatter, and modified time.

Primary sources:

- `5_tasks/queue/`
- `4_inbox/`
- `1_shared_memory/`

Activity types:

- `task_created`
- `task_claimed`
- `task_completed`
- `task_blocked`
- `report_submitted`
- `inbox_item_added`
- `memory_updated`
- `owner_review_needed`
- `planner_assignment_changed` in future orchestration-aware views

The feed should prefer links back to the originating file rather than copying the full content into the board.

## Needs Owner

Needs Owner shows items requiring manual decision, approval, clarification, escalation, or promotion.

Possible sources:

- blocked tasks
- `needs_owner: true`
- `owner_review_required` markers
- `approval_required` markers
- high-risk task markers
- memory promotion candidates
- artifact missing after completion
- ambiguous task assignment
- missing required selector
- missing planner fields only for planner-required orchestration tasks

Needs Owner is not limited to one directory. It is a cross-cutting view over queue state, inbox reports, and promotion candidates.

Normal tasks missing planner_agent should not appear in Needs Owner. `orchestration.enabled` false or missing means planner fields are ignored for Needs Owner purposes.

## View Principles

All views should stay file-driven.

Views may cache rendered summaries in memory during a session, but they should not create an independent persistence layer.

If a view needs a state decision, directory location wins over inferred UI state.
