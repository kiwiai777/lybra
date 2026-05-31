# Planner Subtask Policy

## Purpose

This policy defines how a planner may decompose parent requirements into concrete subtasks.

AIPOS-25 defines protocol only. AIPOS-52 adds draft and publish flow policy. These documents do not create live queue files or implement a task writer.

Planner is task-scoped and optional. Normal tasks do not require planner fields. Planner assignment stays with the parent orchestration unless explicitly delegated.

For complex-class parent requirements, AIPOS-64 requires the active L3/L4 planner instance to remain the continuity planner until the parent requirement completes, is cancelled, is superseded, or receives an Owner-approved handoff.

## Creation Limits

Planner may create at most:

```yaml
max_open_subtasks:
max_subtasks_total:
max_iterations:
```

Planner must not publish the next complex-class task if a prior required audit is pending and the next task depends on that audit.

## Required Subtask Metadata

Each planner-created subtask or subtask draft must include:

```yaml
task_class:
orchestration_id:
parent_task_id:
created_by_planner: true
planner_agent:
iteration:
subtask_sequence:
subtask_type:
depends_on:
reviewer:
audit_by:
context_bundle:
task_mode:
model_tier:
output_target:
artifact_policy:
```

The task body must describe expected output, constraints, and acceptance checks.

Subtasks inherit `orchestration_id` and `parent_task_id` from the parent orchestration. Subtasks should reference `planner_agent` when created by planner, but they do not automatically get an independent planner assignment.

Planner-created subtasks must not silently grant planner rights to coder or reviewer. A coder or reviewer becomes planner only through explicit task-scoped planner assignment on a parent orchestration task.

Planner-created subtasks should preserve the parent continuity planner reference. This reference is provenance and coordination metadata; it does not make the coder or independent reviewer the planner.

AIPOS-53 permits a single agent instance to be explicitly assigned as combined planner/executor for a scoped parent requirement or task. That assignment must be visible and must not grant independent audit or Owner decision authority.

## Context Rules

Planner must include context_bundle references and linked artifact refs instead of copying large context into every subtask.

Planner-created tasks should point to:

- parent task
- orchestration_id
- relevant docs
- required files or directories
- output target
- prior report or audit artifact

## Assignment Rules

Planner must:

- avoid ambiguous assignee
- use task_mode and model_tier routing rules
- classify workflow rigor explicitly with `task_class`
- declare independent review fields such as `reviewer` and `audit_by` for complex-class tasks
- require audit before finalize for complex-class tasks
- not assign tasks to offline or maintenance agent unless Owner allows
- consider runtime_budget_policy before choosing executor
- prefer lower-quota-sensitivity runtimes for lower-risk review tasks when configured
- prefer the most recent successful same-family agent instance within the parent orchestration when it remains idle, eligible, and conflict-free

Planner cannot be the independent reviewer or auditor for its own planned work.

Reviewer and auditor belong to the same independent review family. `reviewer` usually means process or artifact review; `audit_by` usually means the formal PASS / REQUEST_CHANGES gate before finalize. They may be served by the same eligible agent family when policy allows, but they must remain independent from the coder/planner work being reviewed.

When planner and executor are the same instance, independent review separation becomes more important. The combined planner/executor must not review or audit its own executed work unless Owner explicitly classifies the task as not requiring independent review or audit.

Coder and independent review assignments may vary from subtask to subtask. Planner continuity does not grant the continuity planner a right to claim every subtask, review every subtask, audit every subtask, or bypass normal AIPOS-48 matching and AIPOS-50 session lease binding.

When a coder or independent reviewer successfully handled the previous compatible subtask in the same parent requirement, the planner should treat that same-family instance as the preferred recommendation for the next compatible subtask if it is idle and still satisfies task mode, model tier, runtime profile, context, review separation, audit separation, and Owner policy. This preference is advisory and may be overridden by availability, conflict, risk, quota, context, or Owner decision.

Coder self-validation is part of the coder role, not independent review. A coder is expected to run relevant tests, linters, type checks, local validation commands, and repair failures discovered by those checks before handing work to review or audit.

Normal tasks do not require planner_agent. A missing orchestration block is equivalent to orchestration disabled and should not create planner validation warnings.

## Dependency Rules

Use `depends_on` for required prior work:

```yaml
depends_on:
  - AIPOS-25-S01
```

Planner must not bypass dependencies by creating follow-on tasks as if prior subtasks were accepted.

For complex-class dependent tasks, publication and execution require:

```yaml
dependency_condition: audit_pass
dependency_audit_status: PASS
```

Planner may retain a blocked dependent draft while audit is pending or after `REQUEST_CHANGES`, but must not publish it as accepted-work follow-on execution.

If dependency status is unclear, planner should pause or set needs_owner according to policy.

## DAG, Fanout, and Join Rules

AIPOS-93 allows planner-created subtasks and drafts to include optional DAG metadata:

```yaml
dag_id:
dag_node_id:
dag_node_type:
fanout_group_id:
join_gate_id:
depends_on_nodes: []
blocks_nodes: []
join_input_for: []
join_output_from: []
dependency_condition:
```

The simple `depends_on` list remains valid for normal linear dependencies. DAG metadata is used only when the planner needs to represent fanout, parallel branches, join gates, evidence gates, or more explicit dependency conditions.

Planner must keep DAGs acyclic. If a cycle, missing node reference, ambiguous join condition, or fanout limit conflict is detected, the planner must pause, mark the draft set blocked, or raise `needs_owner`.

Fanout groups must not exceed `max_open_subtasks`, `max_subtasks_total`, reviewer/auditor capacity, runtime limits, or Owner-approved scope. Parallelization is an optimization recommendation, not permission to skip AIPOS-52 publish preconditions.

Join gates are coordination metadata. They are not executable queue tasks unless separately represented as a normal validation, audit, review, or finalize task card. Partial join policies such as `any_successful` or `quorum` require rationale and may require Owner approval when they skip or down-scope upstream work.

AIPOS-93 DAG metadata does not implement a scheduler, writer, queue mutation, draft publish automation, runtime launch, Session Tree operation, or controlled execute expansion.

## Audit Task Rules

Audit subtasks should include:

```yaml
subtask_type: audit
task_mode: code_reviewer
review_target_task_id:
review_target_artifacts:
```

Audit tasks must be assigned to an independent review agent separate from the coder when possible.

## Repair Task Rules

Repair subtasks should include:

```yaml
subtask_type: repair
repair_source:
repair_target_task_id:
requires_reaudit: true
```

Repair tasks need re-audit before finalize.

## Finalize Task Rules

Finalize subtasks may be recommended only after required audit passes.

Finalize still includes:

- git status
- forbidden path check
- validation commands
- commit
- push
- completion report

Planner may recommend finalize but cannot self-approve. Finalize subtasks must not be published before required audit passes.

## Runtime Budget Rules

Planner should consult `runtime_budget_policy` before selecting executor/runtime.

Rules:

- Codex CLI quotas must be configurable, including 5-hour and weekly windows.
- Direct claude command / Claude Code quotas must be configurable, including 5-hour and weekly windows.
- cc and cc_glm proxy/API quota must be configurable, but may be lower sensitivity than direct CLI quotas.
- quota_exhausted should pause, handoff, reduce scope, or request Owner decision according to config.
- quota_exhausted must not be treated as task failure by default.
- future_status_provider data is advisory unless policy says otherwise.

## Prohibited Patterns

Planner must not:

- create infinite tasks
- create hidden tasks
- silently reassign claimed tasks
- delete reports
- overwrite agent output
- auto-merge
- auto-push
- bypass reviewer
- execute runtime_command
- ignore reviewer_unavailable or executor_stuck states


## Draft and Publish Flow

AIPOS-52 requires planner-created subtasks to pass through a draft stage before they become claimable pending tasks.

Recommended draft path:

```text
5_tasks/drafts/planner/{orchestration_id}/{subtask_id}.md
```

Recommended publish target:

```text
5_tasks/queue/pending/{subtask_id}.md
```

Planner may create or recommend drafts by protocol, but publication requires the publish preconditions in `planner_subtask_draft_publish_flow.md`. Planner-created drafts must not be treated as executable or claimable until an approved writer path publishes them to pending queue.

AIPOS-52 does not implement a draft writer, publish automation, queue movement, CLI command, Web UI behavior, or forum backend.

Combined planner/executor mode in AIPOS-53 does not change this boundary.
