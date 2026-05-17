# Board File Mapping

## Purpose

This document defines how Board v0 reads and writes formal task state using repository files.

## Read Sources

Board v0 should read:

- `5_tasks/queue/pending/`
- `5_tasks/queue/claimed/`
- `5_tasks/queue/completed/`
- `5_tasks/queue/blocked/`
- `4_inbox/`
- `1_shared_memory/`
- `3_context_bundles/`
- `0_control_plane/agents/`
- `0_control_plane/roles/`
- `0_control_plane/environments/`

## Write Target

Board v0 should write new task cards to:

- `5_tasks/queue/pending/`

## State Moves

Board v0 should move task cards among:

- `pending -> claimed`
- `claimed -> completed`
- `claimed -> blocked`
- `blocked -> pending`
- `blocked -> completed`

## State Representation

State is represented by both:

- file location
- frontmatter `status`

If they conflict, directory location is the operational state.

Frontmatter status should be repaired to match the directory.

Needs Owner should surface the mismatch.

## Claim Semantics

On claim:

- move `pending -> claimed`
- set `status: claimed`
- set `claimed_by`
- set `claimed_at`
- optionally append Claim Log

## Complete Semantics

On complete:

- move `claimed -> completed`
- set `status: completed`
- set `completed_by`
- set `completed_at`
- append Completion Report or link to local report

Temporary completion reports do not enter Git by default.

If a report has long-term value, useful conclusions should be promoted into formal docs or shared memory after Owner approval.

## Block Semantics

On block:

- move `claimed -> blocked`
- set `status: blocked`
- set `blocked_by`
- set `blocked_at`
- set `block_reason`
- set `needs_owner: true` when owner action is required

## View Mapping

Recommended mapping from files to views:

- Task Queue: all files under `5_tasks/queue/`
- My Tasks: filtered queue files based on assignee or agent instance
- Activity Feed: queue files plus `4_inbox/` plus `1_shared_memory/`
- Needs Owner: blocked queue items plus explicit owner-review markers plus mismatch cases

## Conflict Handling

Board v0 should treat directory location as authoritative for operational state.

If a file is in `completed/` but says `status: pending`, Board v0 should:

1. render the item as completed
2. surface a mismatch warning in Needs Owner
3. recommend repairing frontmatter to `status: completed`

This avoids hidden UI state diverging from repository state.
