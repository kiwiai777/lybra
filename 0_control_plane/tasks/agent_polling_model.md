# Agent Polling Model

## Purpose

This document defines how agents discover, claim, execute, and report queue tasks in AI Project OS.

It describes a unified polling model for both local and remote agents.

## Core Rule

All agents use polling.

A task becomes executable when an agent polling cycle detects it as a match and claims it.

## Local Agent Polling

Local agents poll while running.

They are non-24h workers and only participate when the local machine and agent session are active.

Local agents do not need a separate startup check task.

Startup check is just the first polling cycle.

## Remote Agent Polling

Remote agents poll continuously.

They are 24h workers and can keep scanning the pending queue even when the local machine is offline.

## Polling Interval

Suggested default polling interval:

- local: every 5–10 minutes while running
- remote: every 5–10 minutes for pending tasks
- recurring jobs may use hourly / daily / weekly schedule hints

These intervals are protocol guidance, not implementation constraints.

## Task Matching

Agent should match tasks by:

- `assigned_to`
- `agent_instance` if present
- availability
- allowed task mode
- model tier if relevant

Matching should be conservative. If a task does not clearly match the agent's identity or constraints, it should not be claimed.

## Claiming

Claiming means moving or marking task:

`pending` -> `claimed`

The exact mechanism may be file move, metadata update, or future console state change.

In v0, this document defines only the protocol contract.

## Execution

After claiming, the agent performs the requested work using the referenced context bundle and task metadata.

Execution should follow the assigned task mode, selected model tier, and output target.

## Reporting

Reports should go to:

- forum reply / control console
- or `4_inbox/<agent>/`
- or both, depending on `report_mode`

Reports should describe outcome, blockers, and any escalation trigger needed for follow-up work.

## Recurring Task Handling

Recurring tasks define an ongoing task contract rather than only a single run.

Recurring task definitions may remain stable while each actual execution creates a run report.

In v0, recurring tasks are protocol-only and do not yet imply automated scheduling.

## Blocked Task Handling

Failure / ambiguity means:

`claimed` -> `blocked`

Blocked tasks should include a clear reason and the information needed for owner review or follow-up replanning.

## Owner Escalation

If a task is ambiguous, missing context, or exceeds allowed scope, the agent should escalate through a report rather than guessing.

Owner / GPT / Claude use reports to create next tasks.

## Future Automation Notes

Future automation may add:

- polling scripts
- queue claim helpers
- recurring run generation
- board visualization
- notification hooks

This document does not implement those mechanisms yet.
