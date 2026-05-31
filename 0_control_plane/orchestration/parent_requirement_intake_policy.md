# Parent Requirement Intake and Planner Assignment Protocol

## Purpose

AIPOS-51 defines how Owner can send a fuzzy goal or high-level requirement directly into AI Project OS and assign it to an L3 planner without using ChatGPT or Claude.ai as a routine relay.

This protocol creates the intake and assignment boundary for planner-led work. It does not implement a planner loop, queue polling runtime, task writer automation, CLI command, Web UI behavior, database, server, or background worker.

## Target Scenario

The intended flow is:

```text
Owner gives a fuzzy goal or parent requirement
AIPOS records the parent requirement as a visible forum/control-plane item
Owner assigns or allows assignment to an L3 planner
Planner clarifies, decomposes, and drafts subtasks
Coder claims and executes published subtasks
Reviewer or auditor reviews subtasks
Planner summarizes progress and decides next step
Owner decides architecture, risk, scope, or authority forks
All steps are visible in the AIPOS forum/control-plane record
```

## Parent Requirement Definition

A parent requirement is a high-level Owner goal that is not yet a dispatchable coding task.

It may be fuzzy, incomplete, or strategic. Its first job is to create a bounded planning session, not to launch execution.

Recommended parent requirement fields:

```yaml
requirement_id:
title:
owner_goal:
created_by: Owner
created_at:
project:
task_class: complex
complexity_note:
intake_status: received
forum_thread_ref:
visibility: forum_visible
planning_required: true
min_planner_model_tier: L3
allowed_planner_agents:
  - dev_codex
  - dev_claude
assigned_planner:
assigned_planner_instance:
planner_runtime_profile:
planner_assignment_status: proposed
planner_continuity_policy:
continuity_planner_agent:
continuity_planner_agent_instance:
planner_handoff_policy:
orchestration_id:
parent_task_id:
risk_level:
scope_boundary:
known_constraints:
owner_decision_required_for:
needs_owner:
needs_owner_reasons:
```

Allowed `intake_status` values:

```text
received
triage
needs_owner
planner_assigned
planning
drafting_subtasks
awaiting_owner_decision
awaiting_publish
running
completed
cancelled
superseded
```

## Planner Assignment Rule

Planner assignment is task-scoped and requirement-scoped.

A planner may be `dev_codex` or `dev_claude` when the concrete instance satisfies task requirements, runtime profile, context bundle, and model tier policy.

Strategic planning decisions require:

```yaml
planner_model_tier: L3
```

L4 may satisfy the planner requirement if the model tier registry and Owner policy allow it. L1 or L2 may only format or summarize an already approved plan; they must not perform decomposition, architecture route selection, risk handling, subtask assignment, stop-condition evaluation, or handoff decisions.

For complex-class parent requirements, AIPOS-64 requires planner continuity after first active assignment. The assigned L3/L4 planner instance remains responsible for the parent requirement until completion, cancellation, supersession, or Owner-approved handoff.

This continuity applies to the parent-level planner role only. Coder, reviewer, and auditor agents for individual published subtasks may vary per subtask according to dispatch matching, session lease binding, reviewer separation, audit separation, task mode, model tier, and Owner gates.

## Planner Permissions

A parent requirement may grant these protocol permissions:

```yaml
planner_permissions:
  can_clarify_requirement: true
  can_create_subtask_drafts: true
  can_recommend_assignment: true
  can_recommend_reviewer: true
  can_recommend_auditor: true
  can_mark_needs_owner: true
  can_publish_subtasks: false
  can_finalize: false
  can_self_audit: false
```

Planner permissions do not grant OS permissions, GitHub permissions, file-write authority, queue mutation authority, audit approval, finalize approval, or Owner approval by themselves.

## Combined Planner/Executor Intake

AIPOS-53 permits Owner to assign a parent requirement to a combined planner/executor agent instance, such as a local UI plus remote execution host workflow or managed command-line workflow, when the instance satisfies model tier, task mode, runtime profile, context bundle, and capability policy.

Combined mode reduces relay overhead by allowing one execution-authority agent to plan, draft, implement, validate, prepare audit handoff, repair after REQUEST_CHANGES, and finalize after audit PASS.

It does not reduce governance separation. The combined agent must not self-audit, must not become Owner decider, and must not continue past architecture, risk, scope, authority, workflow, audit-boundary, or implementation-boundary forks without Owner decision.

For non-code parent requirements, the same rule applies with the selected `task_mode`, such as documentation, research, operations, content, presentation, or other AIPOS task modes.

## Intake Flow

Minimum intake flow:

1. Owner posts a fuzzy goal or parent requirement.
2. AIPOS records the requirement with `intake_status: received` and a `forum_thread_ref`.
3. A planner assignment is proposed with `min_planner_model_tier: L3`.
4. Owner confirms the planner when assignment is ambiguous, high-risk, or policy requires approval.
5. Planner starts a planning Task Session bound to the parent requirement.
6. For complex-class parent requirements, AIPOS records the active planner as the continuity planner.
7. Planner asks clarifying questions only when required to avoid unsafe scope, architecture, risk, or authority assumptions.
8. Planner emits a bounded plan and proposed subtask drafts.
9. Subtasks remain drafts until AIPOS-52 planner subtask draft and publish policy allows queue publication.

## Forum Visibility Rule

Every parent requirement must have a visible forum/control-plane thread reference.

At minimum, the thread should expose:

- requirement received
- planner assigned
- continuity planner activated
- planner handoff requested or approved
- clarification requested
- Owner decision requested
- plan proposed
- subtask draft created
- subtask publish requested
- subtask published
- coder claimed
- reviewer or auditor assigned
- audit result
- repair requested
- parent requirement completed or cancelled

AIPOS-51 requires the visibility fields and event expectations. AIPOS-53 should define the full Owner Decision Gate and Forum Visibility Protocol.

## Owner Decision Gate

Planner must pause and request Owner decision before continuing when any critical fork appears:

- architecture route split
- scope expansion
- risk escalation
- new runtime, service, database, or deployment boundary
- security or credential boundary change
- audit boundary change
- workflow mode change
- model tier or agent authority expansion
- turning protocol into implementation
- skipping audit or finalize gate
- paid resource or external service requirement
- data loss or irreversible action risk

The planner may recommend options, tradeoffs, and a default path, but Owner decides the fork.

## Parent Task Conversion

A parent requirement may be represented as an orchestration parent task when it needs queue visibility or formal tracking.

The parent task should include:

```yaml
orchestration_parent: true
requirement_id:
owner_goal:
forum_thread_ref:
orchestration:
  enabled: true
  planner_required: true
  planner_model_tier: L3
```

The parent task is not the same as a coding subtask. It owns planning context and orchestration boundaries.

## Boundary With AIPOS-52 And AIPOS-54

AIPOS-51 defines intake and planner assignment.

AIPOS-52 should define how planner-created subtask drafts are reviewed, published, and linked back to the parent requirement.

AIPOS-53 defines combined planner/executor governance, Owner Decision Gate, and Forum Visibility Protocol.

AIPOS-54 defines the Minimal Planner Loop MVP as a manual, visible planner tick protocol. It does not implement autonomous queue polling, task writing, draft publishing, records writing, orchestration writing, CLI commands, Web UI behavior, or runtime launch.

AIPOS-64 defines planner assignment continuity for complex-class parent requirements. It keeps the parent-level planner stable after first active assignment while leaving coder, reviewer, and auditor assignment flexible per subtask.

## Non-Goals

AIPOS-51 does not implement:

- planner loop runtime
- parent requirement UI
- queue polling runtime
- subtask draft writer
- subtask publish automation
- forum backend
- CLI command changes
- Web UI behavior changes
- database
- server
- deployment config
