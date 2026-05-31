# Context Isolation Policy

## Purpose

This policy defines how AI Project OS prevents cross-task, cross-project, cross-role, and cross-output confusion when one agent can discover multiple tasks.

## Strict Context Rule

The selected Task Session is the only active execution context.

The agent must not silently mix:

- another pending task's instructions
- another project's background
- another role's assumptions
- another context bundle
- another task's artifact links
- another task's output target
- another task's completion report
- another task's memory candidates

## Isolation Boundaries

### Project Boundary

Project context applies only to the selected task unless the session explicitly references another project.

If `project` is missing and the task is not clearly cross-project, the item should surface in Needs Owner.

### Role / Task Mode Boundary

`assigned_to`, `task_mode`, and `task_class` are task-scoped execution boundaries. `task_mode` describes the work; `task_class` selects workflow rigor.

The agent must not assume that a reviewer task for one project permits coder behavior for another project.

### Context Bundle Boundary

The session may use only the selected `context_bundle` unless explicit cross-references are provided.

Context bundles define operating boundaries, not free-floating global context.

### Memory Boundary

The session may use only approved `memory_refs` and allowed shared memory domains.

Memory promotion candidates from one project must not be promoted into another project without explicit reference and approval.

### Artifact Boundary

The session may use only the selected `artifact_scope` and explicit `artifact_links`.

Using one task's artifact scope for another task is forbidden.

### Output Boundary

The session must write reports, drafts, or links only to the selected `output_target` unless an explicit report target or owner override exists.

### Reporting Boundary

The session must report against the selected task only.

It must not append another task's report chain or completion evidence to the current task.

### Model Tier Boundary

`model_tier` is selected per task and per session.

The agent must not treat model tier as permanently tied to a role.

If the actual model differs from the task card, the report should state that explicitly.

## Allowed Cross-references

Cross-project or cross-task references are allowed only when explicit.

Allowed if referenced through:

- `memory_refs`
- `artifact_links`
- `input_refs`
- `related_sessions`
- Owner instruction

The policy requires explicit citation or mention when using cross-task or cross-project context.

Example:

Using Cortex architecture notes as an input reference for a Loom task is allowed only if the task card or session includes that reference.

## Local Context Safety

Local manual work must not reuse stale context from a previous task session without explicit resume context.

Copying text from a previous terminal, notebook, or draft into a new session without explicit resume or reference is forbidden.

## Resume and Handoff Safety

Resume is allowed only when the session explicitly permits it and the resume context is still aligned with the same task or an explicit parent session.

Handoff is allowed only when the receiving target, summary, artifact scope, and memory scope are explicit.

## Forbidden Patterns

Forbidden patterns include:

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

## Compatibility Notes

This policy is compatible with:

- task-scoped `task_mode`
- task-scoped `model_tier`
- flexible role instances
- Board v0 multi-task visibility
- polling-based task dispatch

It does not require runtime code, database state, or UI implementation.
