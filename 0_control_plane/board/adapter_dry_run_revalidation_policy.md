# Adapter Dry-run Revalidation Policy

## Purpose

This document defines the dry-run-first and immediate revalidation policy for a future local Board adapter.

## Core Rules

- controlled mutations require dry-run first
- execute request must include `dry_run_token` or `dry_run_snapshot_hash` in future contract
- AIPOS-35 token or hash is protocol-only
- adapter must re-run validation immediately before execute
- execute must not trust stale dry-run
- `planned_writes` and `planned_moves` from dry-run must be compared to execute plan where possible
- actor and task status must be rechecked
- record collision checks must be rerun

## Dry-run Proof Fields

Future protocol fields:

```yaml
dry_run_id:
dry_run_snapshot_hash:
dry_run_created_at:
dry_run_expires_at:
execute_requested_at:
revalidation_performed: true
revalidation_summary:
```

Additional future execute request fields:

```yaml
dry_run_token:
owner_confirmation_token:
```

Current AIPOS-35 rule:

- these fields are protocol-only
- no token generation is implemented
- no stored confirmation state is implemented
- AIPOS-36 adapter execute remains blocked
- AIPOS-37 defines execute contract only
- AIPOS-38 may implement controlled execute

## Execute Gate

For controlled mutations, execute must fail with `DRY_RUN_REQUIRED` when:

- no prior dry-run proof is supplied
- supplied proof cannot be matched to the requested operation
- supplied proof lacks required identity or selector data

Execute must fail with `STALE_DRY_RUN` when:

- dry-run proof is expired
- target task status changed
- target path changed
- actor identity changed
- owner-confirmation conditions changed
- record collision conditions changed
- planned writes or moves no longer match

Recommended expiry policy:

- default ttl: 10 minutes
- maximum ttl: 30 minutes
- expired dry-run must be rejected
- expired token can be refreshed only by running dry-run again

## Revalidation Sequence

The adapter should enforce this order:

1. normalize execute request
2. verify dry-run proof fields exist
3. verify same operation, selector, actor, and major options
4. rerun backend validation in dry-run-equivalent mode
5. compare dry-run plan and current plan where possible
6. confirm owner confirmation requirements are unchanged or newly surfaced
7. execute only if verdict remains `PASS` or allowed `WARN`

## Owner Confirmation Boundary

Rules:

- adapter surfaces `owner_confirmation_required`
- adapter does not bypass owner confirmation
- adapter must not auto-confirm
- `owner_confirmation_token` is protocol-only for now
- manual Owner confirmation may be represented later

If revalidation surfaces new owner-required conditions, execute must stop even if a prior preview appeared safe.

This is not an auth system.

## Response Requirements

Dry-run and execute responses should expose:

- `dry_run`
- `planned_writes`
- `planned_moves`
- `performed_writes`
- `performed_moves`
- `owner_confirmation_required`
- `owner_confirmation_reasons`
- `blocking_reasons`
- `errors`

Execute responses should also expose:

- `revalidation_performed: true`
- `revalidation_summary`

## Future Test Coverage

Future implementation tests must cover:

- dry-run then execute happy path
- stale dry-run rejection
- actor mismatch after preview
- status mismatch after preview
- record collision introduced after preview
- owner confirmation requirement introduced after preview
- plan comparison mismatch
