# Failure and Interruption Policy

## Purpose

This policy defines planner-orchestrator failure classes, severities, actions, pause/resume rules, and timeout handling.

The policy is protocol-only in AIPOS-25. No runtime polling, quota scraping, service website login, or automated task mutation is implemented.

## Failure Classes

Supported classes:

```text
quota_exhausted
quota_status_unknown
budget_warning
network_failure
login_state_invalid
executor_unavailable
executor_stuck
reviewer_unavailable
task_timeout
repeated_failure
context_missing
tool_failure
merge_conflict
schema_validation_failure
owner_decision_required
planner_overrun
```

Required runtime examples:

- Codex may have configurable 5-hour quota and weekly quota windows.
- Direct claude command / Claude Code may have configurable 5-hour quota and weekly quota windows.
- cc and cc_glm proxy/API paths may have provider, relay, model, or account quota limits.
- cc and cc_glm quota may be less sensitive but still must be represented in config.
- network or login state may fail.
- executor may be unable to continue.
- future tools may fetch runtime/quota state from service websites or dashboards only after Owner-approved implementation.

## Severity

Allowed severities:

```text
transient
recoverable
needs_owner
terminal
```

Severity guidance:

- `transient`: Retry may be enough.
- `recoverable`: Planner can reduce scope, split task, or wait for dependency.
- `needs_owner`: Owner decision is required before proceeding.
- `terminal`: Stop loop or mark affected subtask failed.

## Actions

Allowed actions:

```text
retry_after
pause_orchestration
handoff_executor
reduce_scope
split_task
request_owner_decision
mark_subtask_blocked
create_repair_task
stop_loop
continue_with_warning
```

Quota exhaustion must not be task failure by default. It should pause orchestration, request Owner decision, reduce scope, or hand off according to configuration.

## Timeout Policy

Recommended fields:

```yaml
timeout_policy:
  subtask_timeout:
  review_timeout:
  planner_check_timeout:
  max_retries:
  repeated_failure_threshold:
```

Rules:

- timeout should not automatically delete a task
- stuck task should become blocked or needs_owner according to future implementation
- planner may create a diagnostic task
- planner may not ignore blocked tasks and continue unlimited
- repeated_failure_threshold_reached is a stop condition

## Failure Policy Example

```yaml
failure_policy:
  quota_exhausted:
    severity: needs_owner
    action: pause_orchestration
  quota_status_unknown:
    severity: recoverable
    action: continue_with_warning
    max_unknown_duration: 2h
  network_failure:
    severity: transient
    action: retry_after
    retry_after: 15m
  login_state_invalid:
    severity: needs_owner
    action: request_owner_decision
  executor_stuck:
    severity: recoverable
    action: mark_subtask_blocked
  reviewer_unavailable:
    severity: needs_owner
    action: pause_orchestration
  repeated_failure:
    severity: needs_owner
    action: stop_loop
```

## Pause and Resume

Pause reasons:

```text
quota_exhausted
quota_status_unknown
owner_requested
review_pending
executor_unavailable
too_many_failures
high_risk_decision
external_dependency
network_failure
login_state_invalid
```

Resume requirements:

```text
Owner approval
availability restored
quota restored
quota status manually acknowledged
blocked dependency resolved
review completed
network/login restored
```

Resume must be explicit in loop state or a future task update. Planner must not silently resume after needs_owner.

## Handoff Policy

Planner may recommend handoff when executor quota is exhausted, executor is unavailable, or runtime profile is unsuitable.

Rules:

- handoff must preserve context isolation
- handoff must create explicit task update or new task
- handoff must not silently reassign a claimed task
- handoff must respect reviewer and audit_by boundaries
- handoff must consider runtime_budget_policy before selecting a new executor

## Stop Conditions

Required stop conditions:

```text
parent_task_completed
max_iterations_reached
max_subtasks_total_reached
repeated_failure_threshold_reached
owner_decision_required
high_risk_change_detected
quota_exhausted_without_fallback
runtime_status_unknown_beyond_threshold
reviewer_unavailable
```

Planner cannot continue creating subtasks after a stop condition without the configured resolution.
