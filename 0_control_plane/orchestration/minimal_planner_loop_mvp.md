# Minimal Planner Loop MVP

## Purpose

AIPOS-54 defines the minimum viable planner loop for AI Project OS.

The MVP is a manual, visible, bounded planning loop. It lets Owner assign a parent requirement to an L3 planner, lets the planner inspect current AIPOS state, choose the next safe action, and emit visible recommendations or subtask drafts without introducing an autonomous runtime.

This is protocol documentation only. It does not implement a scheduler, daemon, automatic queue polling, task writer, draft writer, publish automation, records writer, orchestration writer, CLI command, Web UI behavior, forum backend, database, server, deployment configuration, or runtime launcher.

## MVP Boundary

The minimal planner loop is an explicit planner tick:

```text
observe -> decide -> emit -> wait
```

The loop advances only when a human or approved future tool invokes the next planner tick. AIPOS-54 does not create background execution or continuous polling.

## Autonomy Tier Baseline

AIPOS-94 defines Planner Autonomy Tier metadata after the minimal loop.

The AIPOS-54 MVP operates at:

```yaml
autonomy_tier: A0
autonomy_scope: tick
```

A0 means Owner or a human operator confirms each planner tick boundary. Higher tiers are not part of AIPOS-54 and are not implemented by AIPOS-94.

Any future tier above A0 must preserve this document's Owner Decision Gate, draft/publish, audit, review, controlled execute, and non-goal boundaries.

## Preconditions

A planner tick may run only when:

- a parent requirement or parent orchestration task exists
- `forum_thread_ref` or equivalent visible control-plane reference exists
- planner assignment is active, task-scoped, and L3/L4 for planning decisions
- for code-class parent requirements, the active planner matches the continuity planner or has Owner-approved handoff metadata
- orchestration limits are declared
- stop conditions are declared
- Owner decision gates are clear
- no required audit is pending for dependent work
- no blocking task, dependency, or reviewer state prevents the next decision

If any precondition is missing, the tick must emit `needs_owner` or `blocked` instead of creating new execution work.

## Inputs

Each tick should read the smallest sufficient context:

- parent requirement or parent task
- forum/control-plane thread summary
- project status, roadmap, and decision log
- relevant task cards and queue state
- planner subtask drafts and publish status
- audit or review results
- records/session/claim summaries when present
- Needs Owner items
- configured limits and stop conditions
- runtime budget or availability notes when relevant

The planner should reference source paths and summaries instead of copying large artifacts into every iteration.

## Decisions

Each tick must choose exactly one primary verdict:

```text
continue
draft_subtasks
publish_ready
wait_for_audit
repair
needs_owner
blocked
complete
cancel
failed
```

## Emitted Output

Each tick should emit a compact visible report:

```yaml
planner_iteration_id:
orchestration_id:
parent_task_id:
planner_agent:
planner_agent_instance:
planner_model_tier:
continuity_planner_agent:
continuity_planner_agent_instance:
combined_planner_executor: false
autonomy_tier: A0
autonomy_scope: tick
owner_confirm_required: true
inputs_read: []
observations: []
decision:
decision_reason:
owner_decision_required: false
needs_owner_reasons: []
subtask_drafts_proposed: []
publish_candidates: []
repair_recommendations: []
audit_handoff_needed: false
next_expected_action:
stop_condition_hits: []
```

This output may be returned in the active conversation, posted to a visible forum/control-plane thread, or later written by an approved orchestration writer. AIPOS-54 does not implement that writer.

AIPOS-62 defines the safety boundary for that future writer in `planner_loop_writer_forum_persistence_boundary.md`. That boundary keeps persistence explicit, append-oriented, and subject to Owner gates. It does not add a writer to AIPOS-54.

## State Update Expectations

AIPOS-54 uses the existing file-driven state schemas as future targets:

```text
5_tasks/orchestration/{orchestration_id}/orchestration_state.md
5_tasks/orchestration/{orchestration_id}/loop_state.md
5_tasks/orchestration/{orchestration_id}/planner_iterations.md
5_tasks/orchestration/{orchestration_id}/orchestration_events.md
5_tasks/orchestration/{orchestration_id}/subtask_index.md
```

The MVP does not write these files. A future writer may persist the tick result only after a separate approved implementation task.

The first persistence implementation should prefer append-only planner iteration and orchestration event entries before summary-state rewrites. Summary files must remain reconstructable from append-only logs, queue state, records, and reports.

## Owner Decision Gate

The planner tick must stop with `needs_owner` when a critical fork appears:

- architecture route split
- scope expansion
- risk escalation
- new runtime, service, database, deployment, or credential boundary
- security or credential boundary change
- audit boundary change
- workflow mode change
- model tier or agent authority expansion
- turning protocol into implementation
- skipping reviewer, audit, publish, claim, session lease, or finalize gate
- paid resource or external service requirement
- data loss or irreversible action risk
- ambiguous assignment, reviewer, auditor, dependency, or publish authority

The planner may recommend options and a default path. Owner decides.

## Combined Planner/Executor Compatibility

AIPOS-53 combined planner/executor mode may be used inside the minimal planner loop.

When the same agent is both planner and executor:

- the tick output must declare `combined_planner_executor: true`
- independent audit remains required when policy requires audit
- Owner decision gates remain unchanged
- internal subagents remain internal execution delegation only
- the agent must not use the loop to self-audit or self-authorize scope expansion

## AIPOS-52 Draft/Publish Compatibility

Planner-created subtasks still follow AIPOS-52:

- drafts are not claimable
- publish requires AIPOS-52 preconditions
- publish must not skip Owner gates
- published tasks enter `5_tasks/queue/pending/` only through an approved writer path
- published tasks remain subject to AIPOS-48 task matching and AIPOS-50 session lease binding

A planner tick may recommend `draft_subtasks` or `publish_ready`, but it does not publish tasks by itself.

## Stop Conditions

The loop must stop when any configured stop condition is hit:

- parent requirement completed
- max iterations reached
- max open subtasks reached
- max subtasks total reached
- repeated failure threshold reached
- Owner decision required
- high-risk change detected
- quota exhausted without fallback
- runtime status unknown beyond threshold
- reviewer or auditor unavailable
- dependency blocked

Stopping means no new subtasks are drafted or published until the configured resolution is visible.

## Non-Code Task Compatibility

The minimal planner loop is not code-only.

It may plan documentation, research, operational, content, sales-support, presentation, image, video, material-production, or other AIPOS task modes when the role instance and task card allow the selected mode.

Non-code work still follows the same loop gates: visible plan, bounded output, Owner decision for critical forks, independent audit when required, and no hidden execution.

## Non-Goals

AIPOS-54 does not implement:

- autonomous planner loop runtime
- scheduled queue polling
- agent execution loop
- task movement
- subtask draft writer
- draft publish automation
- records writer
- orchestration writer
- forum backend
- CLI command changes
- Web UI behavior changes
- database
- server
- deployment configuration
- runtime launch
- provider quota scraping
