# Controlled Execute Contract

## Purpose

This document defines the controlled execute contract for a future local Board adapter write path.

AIPOS-37 is contract-only.

It does not implement real execute behavior.

## Scope

This contract defines:

- controlled execute principle
- execute lifecycle
- required execute inputs
- required execute outputs
- execute response envelope
- failure behavior
- auditability expectations

This contract applies only to the local adapter boundary.

## Out Of Scope

This document does not add:

- server
- HTTP route
- FastAPI
- Flask
- React
- database
- auth system
- daemon
- scheduler
- agent execution
- runtime launcher
- Git automation

## Controlled Execute Principle

Controlled execute rules:

- execute is never allowed directly from arbitrary request payload
- execute must reference a prior dry-run result
- execute must revalidate immediately before write
- execute must compare current plan with dry-run plan
- execute must reject stale dry-runs
- execute must require explicit actor identity
- execute must return standardized envelope
- execute must remain local-only

## MCP Surface Relationship

AIPOS-96 defines a future MCP server as a protocol translation surface for MCP-aware clients.

For mutation tools, MCP must preserve this controlled execute contract:

- dry-run remains required before execute
- dry-run token and snapshot proof remain required
- immediate revalidation remains required
- actor matching remains required
- Owner confirmation remains required where the backend requires it
- blocking reasons remain blocking
- performed writes and moves may only come from approved backend writers

The MCP layer must not add new operations, confirm on behalf of the Owner, bypass revalidation, convert blocked operations into warnings, or execute direct file writes.

## Execute Lifecycle

Recommended lifecycle:

1. caller requests dry-run preview
2. adapter returns preview envelope plus dry-run token fields
3. caller reviews warnings, planned writes, planned moves, and owner requirements
4. caller submits execute request with `dry_run_id` and `dry_run_snapshot_hash`
5. adapter performs execute-time revalidation
6. adapter compares current plan against the prior dry-run plan
7. adapter blocks on mismatch, expiry, or new safety conditions
8. adapter executes only if revalidation remains compatible
9. adapter returns execute envelope with before and after state details

## Required Inputs

Future execute request contract must include:

```yaml
operation:
actor:
task_id:
task_path:
dry_run_id:
dry_run_snapshot_hash:
dry_run_created_at:
dry_run_expires_at:
execute_requested_at:
with_records:
owner_confirmation_token:
```

Input rules:

- `operation` must match the dry-run operation
- `actor` must match the dry-run actor or a compatible allowed alias result
- exactly one of `task_id` or `task_path` should resolve the target when applicable
- `dry_run_id` must identify a reviewed dry-run result
- `dry_run_snapshot_hash` must match the reviewed dry-run snapshot
- `execute_requested_at` records the caller intent timestamp
- `owner_confirmation_token` is required only when owner confirmation is required

## Required Outputs

Execute response must include:

```yaml
ok:
verdict:
operation:
executed_operation:
dry_run:
dry_run_id:
dry_run_snapshot_hash:
current_snapshot_hash:
dry_run_created_at:
dry_run_expires_at:
execute_requested_at:
revalidation_performed:
revalidation_result:
owner_confirmation_checked:
owner_confirmation_result:
actor:
actor_match:
timestamp:
data:
summary:
planned_writes:
planned_moves:
performed_writes:
performed_moves:
warnings:
blocking_reasons:
needs_owner_reasons:
owner_confirmation_required:
owner_confirmation_reasons:
safety_notice:
errors:
post_state:
rollback_available:
```

## Execute Envelope Semantics

Key execute fields:

- `dry_run` must be `false` for real execute responses
- `executed_operation` repeats the normalized operation name
- `current_snapshot_hash` records the hash computed immediately before execute
- `revalidation_performed` must be `true`
- `revalidation_result` must summarize pass, mismatch, or stale outcome
- `owner_confirmation_checked` shows whether owner confirmation gates were evaluated
- `owner_confirmation_result` shows satisfied, not_required, or failed
- `post_state` describes resulting task and record state after successful execute
- `rollback_available` is `false` for MVP unless an explicit rollback primitive exists

## Failure Behavior

Execute must return `BLOCK` when:

- dry-run proof is missing
- token is expired
- snapshot hash mismatches
- actor no longer matches
- task resolution changed
- queue or frontmatter state changed incompatibly
- planned target paths are unsafe
- destination collision exists
- duplicate task ID is introduced
- record collision is detected
- owner confirmation is required but unsatisfied

Failure response rules:

- `performed_writes` must remain empty
- `performed_moves` must remain empty
- `revalidation_performed` must still be `true` after a full execute-time check
- `current_snapshot_hash` and expected `dry_run_snapshot_hash` should both be surfaced where available
- recovery guidance should recommend running dry-run again

## Allowed Future Operations

Recommended first allowlist for future AIPOS-38:

- `draft_create`
- `draft_publish`
- `queue_claim`
- `queue_block`
- `queue_complete`
- `queue_reopen`

Still forbidden:

- `queue_delete`
- `record_delete`
- `draft_overwrite`
- `force_publish`
- `force_claim`
- `force_complete`
- `orchestration_write`
- `shared_memory_write`
- `project_management_write`
- `git_commit`
- `git_push`
- `agent_execute`
- `runtime_launch`

## Auditability

Execute response must expose:

- operation
- actor
- before state
- after state
- planned writes
- planned moves
- performed writes
- performed moves
- record paths created or updated
- timestamp
- verdict
- warnings
- blocking reasons
- owner confirmation metadata

Persistent audit logs remain future work and are not required by AIPOS-37.

## Future Implementation Notes

- AIPOS-36 adapter execute remains blocked
- AIPOS-37 defines execute contract only
- AIPOS-38 may implement controlled execute MVP
- no execute without dry-run token and revalidation
- local-only boundary remains
- no server, UI, auth, or database is introduced here

## AIPOS-38 MVP Implementation Notes (2026-04-30)

AIPOS-38 implements a local controlled execute MVP in `tools/aipos_cli/board_adapter.py` and `tools/aipos_cli/controlled_execute.py`.

Enabled execute allowlist is intentionally narrow:

- `draft_create`
- `draft_publish`
- `queue_claim`

All other execute operations remain blocked.

## AIPOS-39 Test Hardening (2026-04-30)

AIPOS-39 does not expand execute operations.

It adds fixture-based integration tests that verify:

- dry-run token issuance
- execute revalidation behavior
- stale/expired/wrong-actor blocking
- blocked unsupported execute paths
