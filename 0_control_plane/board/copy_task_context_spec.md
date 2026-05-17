# Copy Task Context Spec

## Purpose

This document defines how Board v0 should build a safe Copy Task Context payload from Start Task Session Preview.

## Core Rule

Copy Task Context must copy only the selected task session context.

It must not copy all tasks assigned to the same agent.

It must not include unrelated project memory unless explicitly referenced.

## Copy Payload

Copy payload should include:

- Preview header
- selected task file path
- task frontmatter
- task markdown body
- proposed or active `session_id`
- proposed or active `claim_id`
- project
- assigned role and agent instance
- task mode
- model tier
- context bundle reference
- artifact scope
- memory scope
- output target
- artifact policy
- input refs
- memory refs
- artifact links
- report instructions
- validation verdict
- warnings
- blocking reasons if any
- Needs Owner reasons if any

## Behavior by Verdict

If verdict is `PASS`:

- copy is allowed
- payload is executable after claim or session start confirmation

If verdict is `WARN`:

- copy is allowed only after warning acknowledgment
- payload should clearly mark the warning section

If verdict is `BLOCK`:

- copy should refuse by default
- or copy a diagnostic-only context marked `NOT EXECUTABLE`

If verdict is `NEEDS_OWNER`:

- copy should include an Owner review request
- payload should be marked not ready for execution unless Owner approves

## Session Isolation Rules

Copy Task Context must preserve one-task isolation.

It must not:

- merge multiple pending tasks into one payload
- merge another task's artifact links into the selected task
- merge another task's output target
- import another project's memory unless explicitly referenced
- carry stale context from a prior session unless resume is explicit

## Local Manual Workflow

Local manual workflow:

1. Open Board v0 My Tasks.
2. Select exactly one task.
3. Click Start Task Session.
4. Review preview fields.
5. Resolve `BLOCK` or `NEEDS_OWNER` issues.
6. Confirm claim or session start if allowed.
7. Copy Task Context.
8. Paste into Codex, Claude Code, local CLI, or manual workflow.
9. Execute only inside the selected session boundary.
10. Return report for the selected task only.
11. Mark completed, blocked, or abandoned.

Clarifications:

- local agents may manually switch model
- actual model used should be reported if different from task card
- local agent must not reuse stale context from previous session unless resume is explicit
- Copy Task Context after switching selected task must regenerate payload

## Remote Polling Workflow

Remote workflow:

1. Poll pending tasks.
2. Match `assigned_to` and `agent_instance`.
3. For each candidate, generate Start Task Session Preview.
4. Skip `BLOCK` tasks.
5. Surface `NEEDS_OWNER` tasks.
6. Prefer `PASS` over `WARN` tasks.
7. Select one task.
8. Claim task and write runtime metadata.
9. Start Task Session.
10. Execute within session boundary.
11. Report and transition task state.

Clarifications:

- remote agents may see multiple tasks but claim one task per session
- preview is the guardrail before claim
- remote agents should not claim tasks with unresolved `BLOCK` conditions
- remote agents should not claim `NEEDS_OWNER` tasks unless Owner approval is present

## Recommended Payload Shape

Recommended high-level payload sections:

- preview summary
- execution envelope
- selected task content
- explicit refs and links
- validation result
- execution instruction boundary

## File-driven Boundary

Copy Task Context reads task files, context references, and preview validation results.

It does not write queue state by itself.

Any write, claim, or move happens only after explicit confirmation and according to task state transition policy.
