# Task Session Policy

## Purpose

This policy defines how AI Project OS isolates execution context when one agent can discover multiple tasks.

## Task Session Definition

A Task Session is:

> A task-scoped execution envelope that binds one agent execution to one task, one project context, one task mode, one context bundle, one artifact scope, one memory scope, and one output target.

## Core Rule

An agent may discover multiple tasks, but each execution must bind to exactly one active Task Session.

The agent can see multiple tasks.

The agent can only execute one active Task Session at a time unless explicitly configured otherwise.

The active Task Session is selected before execution.

The active Task Session defines the execution boundary.

## Required Behavioral Rules

The agent must not merge instructions from multiple pending tasks.

The agent must not use one task's output target for another task.

The agent must not assume one task's role applies to another task.

The agent must report against the selected task only.

## Task Session Lifecycle

Task Session lifecycle:

- `discovered`
- `selected`
- `claimed`
- `active`
- `blocked`
- `completed`
- `abandoned`

Minimum meanings:

- `discovered`: task appears in My Tasks or queue view
- `selected`: user or agent chooses this task for execution context preparation
- `claimed`: task is claimed from queue
- `active`: execution has started inside an isolated session
- `blocked`: execution cannot proceed
- `completed`: execution finished and report returned
- `abandoned`: session was started but intentionally stopped without completion

## Relationship to Task Queue State

Task state and session state are related but not identical.

Examples:

- A task may be claimed while no active local model session is currently running.
- A task session may be abandoned while the task returns to pending or blocked.

The queue expresses dispatch state.

The Task Session expresses the currently bound execution context.

## Session Selection Rule

Before execution starts, the active Task Session must bind:

- `task_id`
- `project`
- `assigned_to`
- `agent_instance`
- `task_mode`
- `context_bundle`
- `model_tier`
- `artifact_scope`
- `memory_scope`
- `output_target`
- `artifact_policy`
- `claim_id`

If any boundary-critical field is missing or ambiguous, the session should not become active without clarification.

## Board v0 Integration

Board v0 My Tasks may show multiple tasks assigned to the same agent.

Board v0 should therefore provide an explicit action:

`Start Task Session`

Starting a Task Session should:

1. select exactly one task
2. confirm `project`
3. confirm `assigned_to`
4. confirm `agent_instance`
5. confirm `task_mode`
6. confirm `context_bundle`
7. confirm `model_tier`
8. confirm `artifact_scope`
9. confirm `memory_scope`
10. confirm `output_target`
11. prepare or create `session_id`
12. prepare or create `claim_id` if claim happens

## Copy Task Context Integration

`Copy Task Context` must copy only the selected Task Session context.

It must not copy all tasks assigned to the same agent.

Copy Task Context should include:

- session header
- task card frontmatter
- task body
- `context_bundle` reference
- `project`
- `assigned_to`
- `agent_instance`
- `task_mode`
- `model_tier`
- `artifact_scope`
- `memory_scope`
- `output_target`
- `artifact_policy`
- `input_refs`
- `memory_refs`
- `artifact_links`
- report instructions

## Local Agent Manual Workflow

Local manual workflow:

1. Open My Tasks.
2. Select one task.
3. Start Task Session.
4. Copy selected task session context.
5. Paste into Codex / Claude Code / local CLI / manual workflow.
6. Execute only within that session boundary.
7. Return report for that task only.
8. Mark completed / blocked / abandoned.

Clarifications:

- local agents may manually switch models
- actual model used should be reported if different from task card
- local manual work must not reuse stale context from previous task sessions without explicit resume context

## Remote Agent Polling Workflow

Remote polling workflow:

1. Poll pending tasks.
2. Match `assigned_to` / `agent_instance`.
3. Select one task.
4. Create Task Session.
5. Claim task.
6. Execute within session boundary.
7. Report result.
8. Complete / block / abandon session.

Clarifications:

- remote agents may see many tasks but should claim one task per session
- remote agents with multiple model tiers should use task-level `model_tier` routing
- L1/L2 sessions must not write formal memory unless allowed by `artifact_policy` or escalated

## Resume and Handoff

Resume is allowed only if:

- `resume_allowed: true`
- `resume_context_path` is provided
- same `task_id` or explicit `parent_session_id` is provided
- Owner or task policy allows resume

Handoff is allowed only if:

- `handoff_to` is explicit
- handoff summary is written
- `artifact_scope` and `memory_scope` are preserved or explicitly changed

## Anti-confusion Rules

Forbidden patterns:

- Mixing Loom coder context with Cortex reviewer context in the same session.
- Using one task's artifact scope for another task.
- Writing reports to another task's output target.
- Promoting memory from one project into another project without explicit reference.
- Treating `assigned_to` as a permanent role instead of task-scoped execution identity.
- Treating `task_mode` as a permanent role identity.
- Treating `model_tier` as permanently tied to a role.
- Using stale local context from a previous task without explicit resume.
- Claiming multiple unrelated tasks into one session.

## Needs Owner Integration

A session should enter Needs Owner if:

- session context conflicts with task card
- project is missing and task is not explicitly cross-project
- `assigned_to` / `agent_instance` mismatch
- `context_bundle` missing
- `artifact_scope` unclear
- `memory_scope` unclear
- `output_target` missing
- `model_tier` mismatch for high-risk task
- agent detects cross-task ambiguity
- resume context is stale or conflicting
- handoff target unclear

## Future Schema Recommendations

Future task frontmatter may add:

```yaml
session_policy: single_task_session
context_isolation: strict
artifact_scope:
memory_scope:
active_session_id:
last_session_id:
claim_id:
claimed_by:
claimed_at:
completed_by:
completed_at:
blocked_by:
blocked_at:
block_reason:
```

This policy recommends those fields but does not require immediate task schema modification. AIPOS-50 adds lease and runtime binding fields as protocol targets, not as immediate writer behavior.


## Lease and Runtime Binding

AIPOS-50 defines the session lease and runtime binding layer for active execution.

A Task Session may be selected or claimed without an active lease, but execution should not proceed unless a valid active lease binds the task, claim, session, agent instance, runtime profile, execution host, repository host, validation host, and git host.

The active lease is represented by fields such as:

```yaml
lease_status:
lease_started_at:
lease_expires_at:
heartbeat_at:
runtime_profile:
execution_host:
repo_host:
validation_host:
git_host:
canonical_repo_path:
```

AIPOS-50 does not implement lease writing, heartbeat refresh, automatic recovery, queue polling, CLI behavior, or Web UI behavior.
