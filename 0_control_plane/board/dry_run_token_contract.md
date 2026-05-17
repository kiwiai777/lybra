# Dry-run Token Contract

## Purpose

This document defines the dry-run token object for future controlled execute.

## Token Fields

Required dry-run token fields:

```yaml
dry_run_id:
operation:
actor:
task_id:
task_path:
request_hash:
dry_run_snapshot_hash:
planned_writes:
planned_moves:
blocking_reasons:
warnings:
needs_owner_reasons:
owner_confirmation_required:
owner_confirmation_reasons:
created_at:
expires_at:
repo_root_fingerprint:
backend_version:
adapter_version:
```

Recommended extension fields:

```yaml
with_records:
source_path:
destination_path:
queue_state:
frontmatter_status:
```

## Token Semantics

Token semantics:

- token is local-only
- token is not a credential
- token is not auth
- token does not grant permission
- token only proves that a compatible dry-run was reviewed
- token may be stored in memory, temp file, or future board state
- token persistence is future implementation detail

This is not an auth system.

## Snapshot Hash Definition

`dry_run_snapshot_hash` should cover at minimum:

- operation
- actor
- task_id
- task_path
- source path
- destination path
- queue_state
- frontmatter status
- task frontmatter subset relevant to operation
- active_session_id
- claim_id
- last_session_id
- planned_writes
- planned_moves
- with_records flag
- owner_confirmation_required
- owner_confirmation_reasons

Hash rules:

- hash must be deterministic
- hash must not include wall-clock timestamp except token expiry metadata outside the hash body
- hash must not include machine-specific absolute repo_root
- hash should include normalized repo-relative paths
- hash should include operation-specific safety fields

## Request Hash

`request_hash` should represent the normalized dry-run request shape.

Recommended inputs:

- operation
- actor
- selector fields
- with_records
- reason
- report_link
- normalized mutation options

`request_hash` is useful for confirming that an execute request references the same logical request family as the prior dry-run.

## Expiration Policy

Recommended expiration policy:

- default ttl: 10 minutes
- maximum ttl: 30 minutes
- expired dry-run must be rejected
- expired token can be refreshed only by running dry-run again

Clock behavior:

- no clock-skew grace should be assumed for the MVP contract
- if tolerance is ever added later, it should be explicit and small

## Storage Policy

Storage policy for future implementation:

- in-memory storage is acceptable for a local session MVP
- temp-file storage is acceptable if repo data is not mutated and files remain local-only
- future Board state storage is acceptable if it remains local-only and auditable
- token storage location is an implementation detail, not a protocol guarantee

## Non-auth Disclaimer

This is not an auth system.

This token does not:

- prove user identity
- grant operating system permission
- grant Git permission
- grant GitHub permission
- replace owner confirmation

## JSON Example

```json
{
  "dry_run_id": "dryrun_queue_claim_example_001_2026-04-30T12:00:00Z",
  "operation": "queue_claim",
  "actor": "dev.codex.local",
  "task_id": "EXAMPLE-001",
  "task_path": "5_tasks/queue/pending/example_task.md",
  "request_hash": "reqhash_4a2c",
  "dry_run_snapshot_hash": "snap_91ff",
  "planned_writes": [
    {
      "path": "5_tasks/queue/claimed/example_task.md",
      "kind": "create",
      "type": "task_markdown"
    }
  ],
  "planned_moves": [
    {
      "from": "5_tasks/queue/pending/example_task.md",
      "to": "5_tasks/queue/claimed/example_task.md",
      "kind": "queue_state_move"
    }
  ],
  "blocking_reasons": [],
  "warnings": [],
  "needs_owner_reasons": [],
  "owner_confirmation_required": false,
  "owner_confirmation_reasons": [],
  "created_at": "2026-04-30T12:00:00Z",
  "expires_at": "2026-04-30T12:10:00Z",
  "repo_root_fingerprint": "repo_ai_project_os",
  "backend_version": "aipos_cli_mvp",
  "adapter_version": "board_adapter_mvp"
}
```

## AIPOS-38 MVP Token Store (2026-04-30)

AIPOS-38 uses process-local in-memory token storage only.

- no token persistence file
- no DB/session server
- default token TTL: 10 minutes
- max accepted TTL cap: 30 minutes
- expired tokens require a new dry-run
