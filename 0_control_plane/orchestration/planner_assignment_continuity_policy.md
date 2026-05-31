# Planner Assignment Continuity Policy

## Purpose

AIPOS-64 defines continuity rules for complex-class parent requirements after an L3/L4 planner accepts the planner role.

The goal is to keep one planner responsible for the parent requirement from first active assignment through completion, while allowing coder and independent review assignments to vary per subtask.

This policy is protocol documentation only. It does not implement a planner runtime, queue polling, orchestration writer, session lease writer, task movement, Web UI behavior, CLI command, forum backend, database, server, deployment configuration, or agent launcher.

## Complex-Class Parent Requirement

A complex-class parent requirement is a parent requirement or orchestration parent task explicitly classified:

```yaml
task_class: complex
```

Complex classification is orthogonal to `task_mode`. It may apply to code, docs, design, planning, research, operations, release, repair, or other task modes. AIPOS-64 makes continuity mandatory only for complex-class parent requirements.

## Continuity Rule

When a complex-class parent requirement reaches an active planner assignment, that planner becomes the continuity planner for the parent requirement.

The continuity planner remains responsible until one of these terminal or explicit transition conditions occurs:

- parent requirement completed
- parent requirement cancelled
- parent requirement superseded
- Owner-approved planner handoff
- Owner-approved planner replacement

The continuity planner must not silently change between planner ticks, subtask draft batches, repair cycles, audit cycles, or finalize recommendations.

## Required Metadata

Complex-class parent requirements should record:

```yaml
planner_continuity_policy: sticky_until_parent_complete
continuity_planner_agent:
continuity_planner_agent_instance:
continuity_started_at:
continuity_ends_at:
planner_handoff_policy: owner_approved_only
planner_handoff_reason:
previous_planner_agent:
previous_planner_agent_instance:
```

The continuity fields may mirror `planner_agent` and `planner_agent_instance` while the active planner assignment is stable.

## Allowed Continuity States

```text
proposed
active
paused
handoff_requested
handoff_approved
completed
cancelled
superseded
```

`active` means the continuity planner is the expected planner for future planner ticks and parent-level decisions. `paused` preserves the same planner identity and does not authorize replacement by itself.

## Handoff Rule

A planner handoff is an Owner decision gate for complex-class parent requirements.

The planner may recommend handoff when:

- the planner is unavailable
- the planner lacks required context or runtime access
- the model tier or runtime profile is no longer adequate
- the parent requirement is superseded by a new direction
- Owner requests a different planner

Handoff must preserve:

- `requirement_id`
- `orchestration_id`
- `parent_task_id`
- `forum_thread_ref`
- latest planner tick summary
- open subtask drafts and publish candidates
- open Owner decisions
- pending audit or repair states
- known risks and constraints

The replacement planner must be L3/L4 for planning decisions and must receive the inherited context bundle before emitting new subtasks or parent-level recommendations.

## Coder And Independent Review Fluidity

Planner continuity does not pin coder or independent review assignments.

Published subtasks may be claimed by different coder agents according to AIPOS-48 task matching and AIPOS-50 session lease binding. Independent review work may also vary per subtask, subject to role policy, model tier, task mode, review separation, audit separation, and Owner gates.

Reviewer and auditor belong to the same independent review family. `reviewer` is usually process or artifact review; `auditor` is usually the formal PASS / REQUEST_CHANGES gate before finalize. The same eligible agent family, such as `cc_glm`, may serve either role when policy, task mode, model tier, and independence rules allow it.

When multiple eligible agents can satisfy the same subtask role family, the planner should prefer the most recent successful agent instance for that same family within the same parent requirement or orchestration, if that instance is idle, still eligible, and not blocked by review/audit separation or Owner policy. For example, if `cc_glm` or `dev.claude.cc_glm.local` most recently completed an audit task and is now idle, it should be preferred for the next compatible independent review or audit task before selecting a different eligible reviewer.

This same-family continuity preference may also record the prior task session when known. `prior_session_id`, `last_session_id`, or a runtime-specific `session_resume_ref` helps the next compatible agent instance resume context when the runtime supports it.

Session continuity references are advisory. They must not become a hidden lease, permanent assignment, runtime launch instruction, or claim bypass. A different coder or independent reviewer may be selected when the prior same-family instance is busy, unavailable, mismatched, over budget, conflicted, below required model tier, outside required context, lacking a resumable session, or rejected by Owner policy.

The continuity planner may recommend coder and independent review assignments, but those recommendations do not bypass normal task claim, review, audit, or finalize gates.

Coder self-validation is part of the coder execution boundary. A coder may run tests, lint, type checks, local validation, and repair issues found by those checks without becoming a reviewer or auditor. Independent review begins only when a separate review/audit role or gate evaluates the completed coder output.

Recommended subtask assignment metadata may include:

```yaml
role_continuity_preference:
  role_family: independent_review
  preferred_from_last_success: true
  prior_agent_instance:
  prior_session_id:
  session_resume_ref:
  prior_task_id:
  preference_scope: parent_orchestration
  preference_status: advisory
```

## Combined Planner/Executor Interaction

When Owner permits combined planner/executor mode for a complex-class parent requirement, the continuity planner may also execute selected subtasks only when the subtask card, claim policy, session lease policy, runtime profile, and Owner gates allow it.

Combined planner/executor mode does not allow the continuity planner to:

- self-audit
- act as Owner decider
- bypass review or audit separation
- skip AIPOS-52 draft/publish rules
- claim unrelated subtasks outside the parent scope
- continue through architecture, risk, scope, authority, workflow, audit-boundary, or implementation-boundary forks without Owner decision

## Validation Expectations

Future validators and preview surfaces should treat these conditions as governance issues:

- complex-class parent requirement has `planner_assignment_status: active` but missing continuity planner fields
- active continuity planner differs from active `planner_agent_instance` without Owner-approved handoff metadata
- planner tick is emitted by a different planner instance without `handoff_approved`
- subtask draft batch changes planner identity without an Owner decision reference
- handoff clears or loses parent context, open Owner decisions, pending audit state, or forum thread reference
- same-family prior successful coder or independent reviewer is available but the planner recommendation changes role holder without a visible reason

These checks are future validation expectations. AIPOS-64 does not implement validator behavior.

## Non-Goals

AIPOS-64 does not implement:

- planner runtime
- automatic planner loop
- queue polling
- agent launcher
- session lease writer
- orchestration writer
- planner iteration writer
- forum event writer
- subtask draft writer
- draft publish automation
- Web UI behavior
- CLI command changes
- database
- server
- deployment configuration
- auth/RBAC
