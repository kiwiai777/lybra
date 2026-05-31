# Task Creation Flow

## Purpose

This document defines how tasks are created, posted, and prepared for dispatch in AI Project OS.

It covers the product protocol for creating formal task objects without embedding large background context into each task file.

## Product Model

AI Project OS uses a forum-style control console plus file-based queue model.

Planning happens first, then a formal task object is published into the queue.

The task object is the machine-readable unit used for assignment, polling, execution, and reporting.

## Task Creation Sources

Tasks may be created by Owner directly, by an assigned planner, or by legacy ChatGPT / Claude.ai planning.

Owner may skip ChatGPT and Claude.ai relay by posting a parent requirement directly into AIPOS and assigning an L3 planner.

These creators may:

- define a new task directly
- refine an existing task draft
- convert planning output into a formal queue task

The planner decides what should be done. The queue task defines how an agent can pick it up.

## Forum / Console Posting Model

The forum / console is the task creation and status center.

It is the primary place where a task is proposed, reviewed, and published.

The queue is the execution-facing representation of that decision.

In early versions, the forum / console may be represented by file-based documentation and queue files rather than a UI.

## Project Selection

A task can be attached to:

- an existing project
- a new project
- a random / general task

The `project` field is therefore optional.

This allows queue tasks to support both structured project work and cross-project operational work.

## External Intake Metadata

Task cards may include optional external intake metadata when a request is created from a normalized external intake source approved by a future registry:

- `source_tag`: Stable, functional source identifier defined by the external intake registry.
- `client_tag`: Project routing tag supplied by the external client.
- `external_ref`: Stable external evidence reference, such as a redacted message or approval evidence reference.

These fields are optional. Missing values must not block ordinary task validation. They do not grant write authority, routing authority, Owner approval, audit authority, credential access, or permission to create projects. External intake writers, capability tokens, Owner approval evidence persistence, and controlled execute allowlist entries require separate Owner-approved implementation tasks.

## Context Bundle Selection

Task cards should not copy large background context.

Instead, tasks reference:

- `context_bundle`
- role instance
- environment
- model tier
- shared memory domain

Context Bundle acts as one-click background injection.

The context bundle itself must remain editable and extensible.

Users should be able to add / edit / remove context bundles later.

## Assignee Selection

Each task should define who it is for through:

- `assigned_to` for the target role instance
- `agent_instance` when a concrete instance is preferred

This separates logical assignment from runtime routing.

## Task Mode and Model Tier Selection

Task posting should support:

- choosing project
- choosing assignee
- choosing context bundle
- choosing task_mode
- choosing model_tier
- choosing one-shot or recurring

`task_mode` is task-scoped and reflects the work being requested.

`model_tier` defaults from the selected bundle unless the planner overrides it for this task.

## One-shot Tasks

One-shot tasks are discrete tasks intended to run once.

They should use:

- `task_type: one_shot`
- `recurrence: none`

Examples:

- one-time repo review
- one-time draft generation
- one-time queue cleanup request

## Recurring Tasks

Recurring tasks describe repeated work that should happen on a schedule-like cadence.

They should use:

- `task_type: recurring`
- `recurrence: hourly` / `daily` / `weekly` / `custom`
- `schedule_hint` to describe intended cadence

In v0, recurring tasks are protocol definitions only. Automation will be added later.

## Background Injection

The task object should stay small.

Instead of copying long prompts or large background notes into every task, the system injects background through references:

- context bundle for environment and boundaries
- role instance for operating identity
- shared memory domain for durable knowledge
- project path or linked artifacts for task-specific materials

This keeps the task object editable, diffable, and reusable.

## Publishing Flow

Recommended flow:

1. Owner, GPT, Claude, or assigned planner produces a task proposal.
2. For fuzzy goals, Owner creates a parent requirement and assigns an L3 planner.
3. Planner selects project, assignee, context bundle, task mode, task class, and model tier.
4. Planner decides whether the task is one-shot, recurring, or a planner-created subtask draft.
5. Task metadata is written into the task schema format.
6. Publishing writes the task into `5_tasks/queue/pending/`.

At that point, the task becomes visible to polling agents, but not yet executed.

## Owner Review Loop

Owner reviews reports and forum replies after execution.

Owner or assigned planner use reports to create next tasks. GPT / Claude.ai may advise, but they are no longer required as routine relay in the mixed-host workspace workflow.

This closes the loop between planning, dispatch, execution, and follow-up planning.

## What Task Cards Should Not Contain

Task cards should not contain:

- copied full context bundles
- large duplicated background briefs
- hardcoded agent runtime behavior
- embedded database state
- long-form memory that belongs in shared memory or project files

Tasks should point to context, not duplicate it.

## Future UI Notes

Future UI or board views should expose task creation as structured form fields rather than free-form prompt dumping.

The UI should support editable selectors for project, assignee, context bundle, task mode, task class, optional complexity note, model tier, and recurrence, while preserving the file-based queue as the source of truth.

`task_mode` describes content or operation type. `task_class: simple | complex` independently selects workflow rigor and defaults to effective `simple` when omitted. Complex-class work uses the governed planner, independent audit, repair/re-audit, and PASS-before-finalize loop defined in `task_complexity_class_protocol.md`.


## Planner Subtask Drafts

AIPOS-52 defines planner subtask drafts as proposed task cards that are not yet dispatchable. Planner-created drafts should live under a draft path until the publish policy approves them for `5_tasks/queue/pending/`.

Planner-created drafts must preserve parent requirement references, forum visibility references, reviewer/auditor separation, dependency metadata, and Owner decision gate state.

Publishing a planner draft is a controlled transition from proposed work to queue-visible work. It must not bypass dry-run, revalidation, Owner decision gates, or audit requirements.
