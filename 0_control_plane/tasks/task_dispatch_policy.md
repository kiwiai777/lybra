# Task Dispatch Policy

## Task Creation vs Task Dispatch

Task creation happens in forum / console / file-based queue.

Dispatch happens when agent polling detects matching pending tasks.

Task does not become executable because it exists; it becomes executable when an agent claims it.

## Task Lifecycle

`pending` -> `claimed` -> `completed` / `blocked`

## Polling-Based Dispatch

All dispatch is polling-based in v0.

Agents inspect `pending/`, evaluate whether a task matches their identity and constraints, then claim matching work.

Both local and remote agents follow the same polling contract, with different availability behavior.

## Assignment Rule

- `assigned_to` must match the target role instance.
- `agent_instance` is optional but preferred for remote agents or tier-bound execution.

## Context Injection

- `context_bundle` defines environment, memory, and allowed modes.
- Task files must not duplicate full context already defined elsewhere.

## Model Tier

- Default model tier comes from the referenced context bundle.
- A task may override the default tier when the task card requires it.

## Output Rules

- `L1` output should land in inbox-style destinations.
- `L2` output should land in draft-oriented destinations unless explicitly approved otherwise.
- `L3` output may write to formal memory or repository destinations when the task allows it.

## Ownership

- `needs_owner` signals that manual owner review is required before final acceptance or promotion.

## Recurring Task Dispatch

Recurring tasks may remain as recurring definitions and create runs later.

In v0, recurring tasks are protocol-only, not automated scheduling yet.

`schedule_hint` and `recurrence` describe intended cadence but do not implement timers by themselves.

## Forum / Console Integration

The forum / console is the planning and status surface for formal tasks.

Queue files are the execution-facing representation of that planning state.

Reports may return to the forum / console, `4_inbox/`, or both depending on `report_mode`.

## Forbidden

- Tasks without `context_bundle`
- Tasks with embedded full context

## Agent Polling (Future)

Agents will:

- scan `pending/`
- match `assigned_to`
- claim task
- execute
- write report
- update status

No implementation is defined yet. This policy only prepares the queue contract.
