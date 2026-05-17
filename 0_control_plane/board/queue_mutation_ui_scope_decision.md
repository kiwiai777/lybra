# Queue Mutation UI Scope Decision

## Purpose

AIPOS-58 records the Owner decision for whether to expose queue `block`, `complete`, and `reopen` mutations in the local Web UI at this point in the roadmap.

This document is a scope decision only. It does not implement Web UI behavior, backend execute behavior, CLI commands, records writing, orchestration writing, queue polling, runtime launch, auth/RBAC, deployment configuration, database changes, or git automation.

## Decision

Do not expand the local Web UI controlled execute surface for `queue_block`, `queue_complete`, or `queue_reopen` during AIPOS-58.

Do not expand the backend controlled execute allowlist during AIPOS-58.

Keep the current Web UI mutation surface limited to:

- `queue_claim`
- `draft_create`
- `draft_publish`

Keep `with_records` execute disabled for Web UI controlled execute.

Defer queue `block`, `complete`, and `reopen` UI work until after the Planner loop UI / forum visibility path has advanced.

## Rationale

The Web UI already supports the minimum task intake and task activation loop:

- claim a pending task
- create a draft task card
- publish a draft task card into pending

Queue `block`, `complete`, and `reopen` are useful task lifecycle actions, but they are not the primary bottleneck for reducing manual relay in the AIPOS workflow.

The Owner-selected priority is to advance the Planner loop UI / forum visibility path so that fuzzy Owner requirements can enter AIPOS directly, become visible planner work, produce reviewable subtask drafts, and move toward the coder/reviewer/auditor loop with fewer manual copy/paste handoffs.

## Deferred Queue Mutation UI Candidates

The deferred queue mutation UI sequence should be revisited later in this order unless the Owner changes priority:

1. `queue_complete` UI with dry-run preview and confirmation
2. `queue_block` UI with block reason, dry-run preview, and confirmation
3. `queue_reopen` UI with reopen reason, dry-run preview, and confirmation

Future implementation must preserve:

- dry-run before execute
- dry-run token reference
- snapshot-hash match
- execute-time revalidation
- matching actor
- visible planned writes and planned moves
- visible warnings and blocking reasons
- no `with_records` execute unless separately approved
- no records or orchestration writes unless separately approved
- no runtime launch, agent execution, git commit/push, database, auth/RBAC, or deployment expansion
- independent audit before finalize

## Planner UI Priority

The next planned task after AIPOS-58 is:

- AIPOS-59: Parent Requirement / Planner Loop UI Entry

Expected follow-on candidates:

- AIPOS-60: Forum-visible Planner Tick / Event Log UI
- AIPOS-61: Planner Draft Subtask Review / Publish UI

These names may be refined by later task cards, but the priority direction is stable: Planner loop UI and forum visibility come before queue `block`, `complete`, and `reopen` Web UI expansion.

## Boundary

AIPOS-58 must not modify:

- `web/`
- `tools/aipos_cli/*.py`
- `5_tasks/queue/`
- `5_tasks/drafts/`
- `5_tasks/records/`
- `5_tasks/orchestration/`

AIPOS-58 may update governance and project-management documents to record the Owner decision and next roadmap direction.
