# Start Task Session Validation

## Purpose

This document defines how Board v0 validates whether a selected task can safely enter a Task Session.

## Validation Model

Validation classifies preview results into:

- `PASS`
- `WARN`
- `BLOCK`
- `NEEDS_OWNER`

Validation happens before claim or execution.

## PASS

Preview passes if:

- one task is selected
- directory state and frontmatter status match
- `assigned_to` or `agent_instance` matches current actor
- `context_bundle` exists or is resolvable
- `task_mode` is present
- `model_tier` is present or defaultable
- `output_target` is present
- `artifact_policy` is present
- `session_policy` is present or defaultable
- `context_isolation` is present or defaultable
- `artifact_scope` is clear or not required
- `memory_scope` is clear or not required
- no `active_session_id` conflict exists

## WARN

Warnings should include:

- `project` is missing but task may be cross-project
- `agent_instance` is missing but `assigned_to` matches
- `model_tier` is defaulted from context bundle
- `artifact_scope` is empty on single-project low-risk task
- `memory_scope` is empty on single-project low-risk task
- `last_session_id` exists from prior blocked or completed attempt
- `claim_id` exists but task is pending and appears reopened

WARN means the task may proceed only after the actor acknowledges the warnings.

## BLOCK

Blocking conditions should include:

- no task selected
- multiple tasks selected for one session
- status or directory mismatch
- `assigned_to` and `agent_instance` do not match current actor
- missing `context_bundle`
- missing `task_mode`
- missing `output_target`
- missing `artifact_policy`
- `active_session_id` exists on a different active task or session
- status is `completed`
- status is `claimed` by another actor
- task requires approval before claim
- `claim_id` format is invalid and task is already claimed
- `session_id` would collide with existing session reference

BLOCK means the task must not be claimed or executed.

## NEEDS_OWNER

Needs Owner conditions should include:

- `approval_required == true`
- `needs_owner == true` before claim
- high-risk task lacks explicit `model_tier`
- cross-project task lacks `artifact_scope` or `memory_scope`
- ambiguous project
- ambiguous assignee
- ambiguous `context_bundle`
- stale resume context
- handoff target unclear
- runtime metadata conflicts with task card

NEEDS_OWNER means the task should not proceed until Owner review is recorded.

## Validation Output Behavior

Validation should produce:

- one verdict
- zero or more blocking reasons
- zero or more warnings
- zero or more Needs Owner reasons
- recommended next action

Recommended next actions:

- `start_session`
- `acknowledge_and_continue`
- `fix_metadata_then_retry`
- `send_to_needs_owner`
- `do_not_execute`

## Blocking vs Warning vs Needs Owner

Interpretation rules:

- `PASS`: safe to proceed
- `WARN`: safe to proceed only with explicit acknowledgment
- `BLOCK`: not executable
- `NEEDS_OWNER`: not executable until Owner approval or clarification

If multiple issue classes exist, the effective result should prefer the strongest restriction:

`BLOCK` > `NEEDS_OWNER` > `WARN` > `PASS`

## Needs Owner Routing

Preview must route to Needs Owner if:

- approval is required before claim
- `needs_owner` is already true before claim
- high-risk task has defaulted or missing `model_tier`
- cross-project boundaries are unclear
- `artifact_scope` is missing for cross-project task
- `memory_scope` is missing for cross-project task
- assignee is ambiguous
- `context_bundle` is missing or ambiguous
- `active_session_id` conflicts
- runtime metadata conflicts
- status or directory mismatch exists
- resume context is stale
- handoff target is unclear

## Local Workflow Guidance

Local manual agents should open preview, resolve `BLOCK` and `NEEDS_OWNER`, then confirm session start if allowed.

If the selected task changes, preview and copy payload must be regenerated.

## Remote Workflow Guidance

Remote polling agents should generate preview for each candidate task before claim.

They should:

- skip `BLOCK` tasks
- surface `NEEDS_OWNER` tasks
- prefer `PASS` over `WARN`
- avoid claiming tasks with unresolved conflicts
