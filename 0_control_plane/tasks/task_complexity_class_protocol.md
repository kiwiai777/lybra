# Task Complexity Class Protocol

## Status

AIPOS-139 defines task complexity classification as protocol documentation only.

This document does not implement validators, task writers, queue mutation, Board behavior, CLI behavior, MCP behavior, runtime behavior, autonomous orchestration, agent launch, heartbeat expansion, credential changes, deployment changes, or workspace changes.

## Purpose

Lybra separates the content of a task from the workflow rigor required to complete it.

The prior code-class rule in DL-20260504-07 made planner continuity and the full planning, execution, audit, repair, and finalize loop mandatory for code-class parent requirements. That rule correctly preserved governance for complex engineering work, but its trigger was too narrow: non-code work can also be complex, and trivial code changes do not always need a parent-level planner loop.

This protocol replaces the content-type trigger with an explicit complexity trigger.

## Task Fields

Task cards may include:

```yaml
task_mode:
task_class: simple
complexity_note:
```

### `task_mode`

`task_mode` remains the task-scoped content or operational mode. Examples include:

```text
code
coding
docs
design
planning
research
operations
code_reviewer
auditor
```

`task_mode` continues to inform capability matching, model routing, context selection, and execution expectations. It does not by itself select the full closed-loop governance workflow.

### `task_class`

`task_class` selects workflow complexity.

Allowed values:

```text
simple
complex
```

When omitted, `task_class` defaults to:

```text
simple
```

The default preserves backward compatibility for historical task cards and ordinary standalone tasks. New task-creation and preview surfaces should make the effective value visible so an omitted value is not mistaken for an explicit classification.

### `complexity_note`

`complexity_note` is optional free text. Owner or planner may use it to record why a task is classified as simple or complex.

`complexity_note` is advisory metadata. It does not grant authority, override `task_class`, bypass Owner gates, bypass audit, change model tier, change runtime permissions, or mutate queue state.

## Orthogonality Rule

`task_mode` and `task_class` are orthogonal.

Examples:

```yaml
task_mode: docs
task_class: simple
```

```yaml
task_mode: docs
task_class: complex
complexity_note: Multi-agent release documentation requires independent audit and coordinated repair.
```

```yaml
task_mode: code
task_class: simple
complexity_note: Trivial typo correction with no runtime behavior change.
```

```yaml
task_mode: code
task_class: complex
complexity_note: Shared validator behavior changes across CLI and Board surfaces.
```

Code work should usually be classified as `complex` when it changes product behavior, shared contracts, runtime behavior, governance behavior, public interfaces, or release artifacts. Trivial code changes may be explicitly classified as `simple`.

This guidance must not become an implicit validator rewrite from `task_mode: code` to `task_class: complex`. The classification remains explicit.

## Simple-Class Workflow

`task_class: simple` means the task is an independently executable unit of work.

Simple-class tasks:

- do not require a parent-level planner loop by default
- do not require a continuity planner by default
- may be claimed and completed as a standalone task
- close when their task-specific acceptance criteria and required checks pass
- may still require independent audit when Owner direction or another policy explicitly requires it
- remain subject to Owner Decision Gates for architecture, scope, risk, security, credential, runtime, deployment, external publication, irreversible actions, authority expansion, audit-boundary changes, and long-term direction forks

Simple classification does not grant permission to skip a gate required by another policy or explicit Owner direction.

## Complex-Class Workflow

`task_class: complex` means the task requires the full governed closed loop.

Complex-class parent requirements and complex orchestrated tasks require:

1. visible planner intake or planner coordination
2. stable parent-level continuity planner after active assignment
3. explicit decomposition or visible execution plan
4. explicit executor or coder claim for executable work
5. executor self-validation before audit handoff
6. independent reviewer or auditor assignment separate from the work being reviewed
7. independent audit result before finalize
8. repair task and re-audit after `REQUEST_CHANGES`
9. finalize only after required audit PASS and clear Owner gates
10. Owner-approved planner handoff with preserved parent context when planner identity changes

The continuity planner may plan, coordinate, prepare audit handoff, recommend repair, and recommend finalize. The continuity planner must not self-audit or self-finalize.

Combined planner/executor mode remains allowed when policy permits. It combines execution authority only. It does not combine independent audit authority or Owner decision authority.

## Dependency And Audit Gate Rule

A planner must not create or publish a dependent next task as though prior work were accepted while a required audit remains pending.

For complex-class work:

```text
required audit pending
-> dependent next task remains draft, paused, blocked, or needs_owner
```

```text
required audit REQUEST_CHANGES
-> repair task may be created
-> finalize remains blocked
-> dependent accepted-work task remains blocked until re-audit PASS
```

```text
required audit PASS
-> finalize recommendation may proceed when other gates are clear
```

This rule applies to code and non-code task modes when `task_class: complex`.

## Supersession Of DL-20260504-07

This protocol supersedes only the trigger condition in DL-20260504-07.

Prior trigger:

```text
code-class parent requirement
-> mandatory planner continuity and closed-loop governance
```

New trigger:

```text
complex-class parent requirement or complex orchestrated task
-> mandatory planner continuity and closed-loop governance
```

The following DL-20260504-07 rules remain load-bearing and are preserved:

- stable parent-level L3/L4 continuity planner
- Owner-approved planner handoff
- context preservation during handoff
- coder or executor self-validation is not independent review
- coder and independent-review assignments remain subtask-scoped
- same-family role continuity remains advisory only
- independent reviewer and auditor separation
- planner must not self-audit
- planner must not self-finalize
- required audit PASS before finalize
- repair followed by re-audit after `REQUEST_CHANGES`
- no dependent accepted-work task while required audit remains pending

## Compatibility

- Historical task cards without `task_class` remain valid and behave as `simple`.
- Existing `task_mode` values remain valid.
- Existing orchestration metadata remains valid.
- Existing complex code work may be explicitly classified as `task_class: complex` when touched or migrated.
- No automatic migration of historical task cards is required.
- No validator may silently infer `task_class: complex` solely from `task_mode`.
- New surfaces should display the effective default when `task_class` is omitted.
- New surfaces should warn, without blocking, when a code-mode task is omitted or explicitly classified as `simple`, so the operator can review the classification.

## Affected Finalized Protocol Inventory

AIPOS-140 should update these finalized protocol documents to replace code-class triggers with complex-class triggers and add the new fields where applicable:

- `0_control_plane/orchestration/planner_assignment_continuity_policy.md`
- `0_control_plane/orchestration/parent_requirement_intake_policy.md`
- `0_control_plane/orchestration/minimal_planner_loop_mvp.md`
- `0_control_plane/orchestration/planner_orchestrator_protocol.md`
- `0_control_plane/orchestration/planner_subtask_policy.md`
- `0_control_plane/orchestration/planner_subtask_draft_publish_flow.md`
- `0_control_plane/orchestration/orchestration_task_schema.md`
- `0_control_plane/dispatch/task_matching_policy.md`
- `0_control_plane/tasks/task_creation_flow.md`

AIPOS-140 should inspect and update these related protocol surfaces when their examples, schema descriptions, or preview contracts need alignment:

- `0_control_plane/roles/task_mode_policy.md`
- `0_control_plane/tasks/task_session_schema.md`
- `0_control_plane/tasks/context_isolation_policy.md`
- `0_control_plane/board/task_creation_form.md`
- `0_control_plane/board/start_task_session_preview.md`
- `0_control_plane/orchestration/example_orchestration_task.md`
- `0_control_plane/workflows/combined_planner_executor_mode.md`

## AIPOS-140 Implementation Expectations

The implementation slice should:

- accept optional `task_class` and `complexity_note` task-card fields
- normalize missing `task_class` to effective `simple`
- preserve the explicit raw field and expose the effective classification
- surface task class in queue, task detail, context pack, start-task preview, draft review, and Board views where applicable
- surface a non-blocking warning for code-mode tasks with missing or explicit `simple` classification
- replace code-class mechanical triggers with complex-class triggers
- enforce complex-class continuity planner expectations
- enforce complex-class planner, reviewer, and auditor separation expectations
- enforce pending-audit dependency blocking for complex-class dependent work
- keep explicit Owner gates and other policy-required audits active for simple-class tasks
- add regression coverage for code-simple, code-complex, docs-simple, docs-complex, omitted-task-class compatibility, independent audit separation, and pending-audit dependency blocking

Implementation should inspect the current mechanical paths rather than introduce a new abstraction only for naming consistency. Current paths include CLI validators, draft validation, task loading, previews, rendering, context packs, Board routes, Board static UI, and their tests.

## Owner Decision Gate

Owner approved the AIPOS-139 direction before protocol drafting:

1. supersede the code-class trigger in DL-20260504-07
2. add `task_class: simple | complex`
3. default missing `task_class` to `simple`
4. add optional `complexity_note`
5. stop mechanically forcing all code-mode tasks into the full closed loop
6. preserve complex-class continuity, audit separation, repair/re-audit, and finalize rules
7. require AIPOS-139 independent audit PASS and finalize before AIPOS-140 implementation begins

## Non-Goals

AIPOS-139 does not implement:

- validator changes
- schema implementation changes
- task writer changes
- queue mutation
- Board UI changes
- CLI changes
- MCP changes
- controlled execute allowlist changes
- autonomous runtime
- automatic planner loop
- agent launcher
- heartbeat expansion
- automatic task classification
- hidden task creation
- automatic finalize
- self-audit
- State Staleness and Provenance Protocol
- Trace-Native Audit Protocol
- Adaptive Simplification and Gate Intensity Protocol
