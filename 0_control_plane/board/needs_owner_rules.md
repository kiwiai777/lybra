# Needs Owner Rules

## Purpose

Needs Owner is the board surface for items that require manual owner review, approval, clarification, escalation, or promotion.

## Trigger Conditions

An item should surface in Needs Owner when any of the following is true:

- `status == blocked`
- `needs_owner == true`
- `owner_review_required == true`
- `approval_required == true`
- `risk_level == high`
- model tier escalation requested
- architecture, scope, risk, authority, workflow, audit-boundary, or implementation-boundary fork
- combined planner/executor requests Owner-only decision
- minimal planner loop tick returns `needs_owner`
- planner tick preconditions are missing or ambiguous
- planned action would skip reviewer, audit, finalize, claim, session lease, or publish gate
- internal subagent is proposed as independent auditor
- missing required selector
- directory/status mismatch
- ambiguous assignee
- artifact link missing after completion
- formal memory promotion candidate

## Source Types

Needs Owner may gather items from:

- `5_tasks/queue/blocked/`
- queue tasks with owner-review markers
- inbox reports requesting clarification
- shared memory promotion candidates
- completed tasks missing required output evidence

## Required Presentation

Each Needs Owner item should show at least:

- item type
- source file
- task or report identifier
- reason surfaced
- current state
- suggested next action

## Action Categories

Common owner actions include:

- approve
- reject
- clarify
- reassign
- escalate model tier
- promote to shared memory
- request artifact link
- reopen or return to pending
- approve or reject architecture/risk/scope fork
- approve or reject workflow, model-tier, agent-authority, or audit-boundary change
- approve, narrow, or reject combined planner/executor plan

## Priority Guidance

Board v0 should prioritize Needs Owner items that are:

- blocking active work
- high risk
- missing required selectors
- preventing report or artifact closure

## Relationship to Queue Repair

Needs Owner is also the repair surface for protocol inconsistencies such as:

- queue directory and frontmatter `status` mismatch
- claim metadata missing after file move
- completed task without evidence of output target or artifact link

## Relationship to Temporary Reports

Temporary reports do not need to be committed into Git to appear in Needs Owner.

Board v0 may reference inbox reports or local review outputs, then only promote durable conclusions after owner approval.

## Relationship To Combined Planner/Executor Mode

AIPOS-53 uses Needs Owner as the visibility surface for critical forks raised by a combined planner/executor.

The combined execution agent may recommend a path, but it must stop when Owner decision is required. Needs Owner should show the decision reason, affected task or parent requirement, recommended options, and the consequence of approving or rejecting the fork.

## Relationship To Minimal Planner Loop

AIPOS-54 uses Needs Owner as the required stop surface for planner ticks that cannot safely continue.

When a tick emits `needs_owner`, the item should show the parent requirement or orchestration id, tick id, decision reason, options considered, recommended default when available, and the next action after Owner decision.
