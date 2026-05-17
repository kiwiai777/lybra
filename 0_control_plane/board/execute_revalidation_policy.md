# Execute Revalidation Policy

## Purpose

This document defines the execute-time revalidation policy for future controlled execute.

## Revalidation Checklist

Execute-time checks must verify:

- `dry_run_id` exists and matches operation
- token not expired
- actor unchanged or explicitly allowed alias match
- `task_id` or `task_path` still resolves to the same task
- queue directory state unchanged as required
- frontmatter status still valid
- planned target paths still safe
- no destination collision
- no duplicate task_id introduced
- records paths still available when `with_records == true`
- blocking reasons still empty
- owner confirmation satisfied when required
- new dry-run plan hash matches original `dry_run_snapshot_hash`

## Hash Comparison

Hash comparison rules:

- recompute current snapshot hash immediately before execute
- compare current hash with expected `dry_run_snapshot_hash`
- compare recomputed planned writes and planned moves with prior dry-run plan
- treat any material mismatch as a stale or failed revalidation result

Material mismatch examples:

- destination path changed
- queue state changed
- relevant task frontmatter changed
- `with_records` flag changed
- owner confirmation requirement changed
- record plan changed

## Stale Dry-run Behavior

Expired or mismatched dry-run behavior:

- return `BLOCK`
- use `STALE_DRY_RUN` when the prior preview is no longer current
- use `REVALIDATION_FAILED` when the current validation path no longer matches the old approval surface
- keep `performed_writes` empty
- keep `performed_moves` empty
- include `current_snapshot_hash` and expected `dry_run_snapshot_hash`
- recommend running dry-run again

## Owner Confirmation Behavior

Owner confirmation checks must verify:

- whether owner confirmation was required during dry-run
- whether new owner confirmation reasons surfaced during revalidation
- whether provided `owner_confirmation_token` exists when required
- whether owner confirmation metadata is still compatible with the current request

If owner confirmation becomes required during revalidation:

- execute must stop
- verdict must be `BLOCK` or `NEEDS_OWNER`
- response must explain the new owner-confirmation reason set

This is not an auth system.

## Actor, Status, Path, And Record Rechecks

Actor rechecks:

- actor identity must match the reviewed dry-run actor
- alias compatibility must be explicit, not implied silently
- actor alias ambiguity should escalate to owner confirmation or block

Status rechecks:

- queue directory state must still match the required source state
- frontmatter status must remain valid for the operation
- operations affecting completed tasks require stronger owner confirmation rules

Path rechecks:

- resolved paths must remain repo-relative and safe
- source and destination paths must remain within allowed writer roots
- destination collision checks must be rerun

Record collision rechecks:

- record paths must still be available
- duplicate claim or session record IDs must still be absent
- record warnings promoted to owner-risk must be rerun

## Failure Categories

Recommended categories for revalidation failure:

- `DRY_RUN_REQUIRED`
- `STALE_DRY_RUN`
- `REVALIDATION_FAILED`
- `ACTOR_MISMATCH`
- `STATUS_MISMATCH`
- `PATH_UNSAFE`
- `RECORD_COLLISION`
- `OWNER_CONFIRMATION_REQUIRED`

## Recommended Recovery Messages

Suggested user-facing recovery guidance:

- "Dry-run preview expired. Run dry-run again before execute."
- "Task state changed since preview. Review the latest dry-run."
- "Owner confirmation is now required. Review updated warnings."
- "Record collision detected during execute revalidation. Resolve record state and retry."
- "Actor identity no longer matches the reviewed dry-run. Start a fresh dry-run with the current actor."

## AIPOS-38 Revalidation Behavior (2026-04-30)

Before write, execute recomputes current dry-run plan and snapshot hash.

Mismatch result:

- verdict `BLOCK`
- category `REVALIDATION_FAILED`
- no performed writes/moves
- include expected and current snapshot hashes
- recommend running dry-run again

## AIPOS-39 Revalidation Test Coverage (2026-04-30)

Integration fixtures now validate revalidation outcomes across draft create, draft publish, and queue claim execute paths.

Tested block cases include expired token, stale snapshot mismatch, actor mismatch, and destination collision.
