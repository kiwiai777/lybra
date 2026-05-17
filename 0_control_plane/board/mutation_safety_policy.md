# Mutation Safety Policy

## Purpose

This document defines the dry-run-first and owner-confirmation policy for Board/API mutation flows.

## Core Rules

- Board UI must not write files directly.
- Mutations must go through backend contract or adapter.
- Execute after dry-run must revalidate.
- Stale dry-run output must not be trusted as authorization to mutate.
- `planned_writes`, `planned_moves`, `warnings`, and `blocking_reasons` must be shown before execute.
- `with_records` must clearly show record writes and record updates before execute.
- controlled mutations require dry-run proof at execute time
- adapter must re-run validation immediately before execute

## Dry-run-first Classes

### Read-only

Dry-run needed: no

Examples:

- `GET /health`
- `GET /queue`
- `GET /tasks/{task_id}`
- `GET /records`
- `GET /agents`

### Low-risk write

Dry-run needed: recommended

Examples:

- `POST /drafts/create`

Rule:

- execute allowed after validation
- UI should still offer dry-run first

### Controlled mutation

Dry-run needed: required

Examples:

- `POST /drafts/publish`
- `POST /queue/claim`
- `POST /queue/block`
- `POST /queue/complete`
- `POST /queue/reopen`

Rule:

- Board should require a successful dry-run before exposing execute

### Owner-confirmed mutation

Dry-run needed: required plus owner confirmation

Examples:

- queue mutation with records plus owner-risk conditions
- publish or queue mutation where owner-review flags are present

Rule:

- dry-run first
- explicit owner confirmation contract fields required before execute

### Forbidden

Not exposed by Board/API contract:

- orchestration writer
- agent execution
- database write
- scheduler mutation
- runtime launch

## Required Dry-run-first Operations

- draft create: dry-run recommended, execute allowed after validation
- draft publish: dry-run required
- queue claim: dry-run required
- queue block: dry-run required
- queue complete: dry-run required
- queue reopen: dry-run required
- queue mutation with records: dry-run required + show record writes
- orchestration writer: forbidden
- agent execution: forbidden
- database write: forbidden

## Owner Confirmation

Owner confirmation is required when any of the following conditions are present:

- `needs_owner == true`
- `risk_level == high`
- `approval_required == true`
- `owner_review_required == true`
- `blocking_reasons` non-empty
- actor mismatch
- task status or directory mismatch
- record overwrite or collision
- orchestration parent mutation
- planner-created subtask publish

## Confirmation Fields

Board/API contract fields:

```yaml
owner_confirmation_required: true | false
owner_confirmation_reasons: []
owner_confirmation_token: optional future field
confirmation_summary:
confirmed_by:
confirmed_at:
```

Current stage rules:

- confirmation token is protocol-only
- Board does not implement confirmation UI yet
- backend adapter does not implement stored confirmation state yet
- This is not an auth system.

## Dry-run Proof And Revalidation

Future execute requests should carry:

```yaml
dry_run_id:
dry_run_snapshot_hash:
dry_run_created_at:
dry_run_expires_at:
execute_requested_at:
dry_run_token:
owner_confirmation_token:
revalidation_performed:
revalidation_summary:
```

Current stage rules:

- dry-run token and snapshot hash are protocol-only
- adapter must not trust stale dry-run output
- adapter should compare current plan versus prior `planned_writes` and `planned_moves` where possible
- actor and status must be rechecked before execute
- record collision checks must be rerun before execute
- missing proof maps to `DRY_RUN_REQUIRED`
- stale proof maps to `STALE_DRY_RUN`
- AIPOS-36 adapter execute remains blocked
- AIPOS-37 defines execute contract only
- AIPOS-38 may implement controlled execute MVP

Recommended token policy:

- default ttl: 10 minutes
- maximum ttl: 30 minutes
- expired dry-run must be rejected
- refresh requires running dry-run again

## Actor and Runtime Identity

Board should pass identity as:

```yaml
actor:
agent_instance:
runtime_profile:
current_role:
current_user_label:
```

Rules:

- `actor` is required for queue mutations
- `agent_instance` and `runtime_profile` are optional but recommended
- alias-aware matching remains backend responsibility
- Board may display `availability_status` but must not hide tasks solely because status is offline or unknown
- Board must not execute `runtime_command`

## Local-only Boundary

- AIPOS Board API should start as local-only.
- local-only adapter must not expose remote mutation endpoints.
- If a server is later implemented, default bind should be `localhost`.
- No remote exposure until auth and permission model are designed.
- No database in this stage.
- No multi-user auth in this stage.
- No shell command passthrough.
- No runtime command execution.
- No agent launch.

## Out of Scope

- Web UI implementation
- API server implementation
- auth system
- RBAC
- database persistence
- remote deployment
- agent execution
- scheduler
- quota polling
- runtime launch
- orchestration writer
- planner loop runtime
- notification system
- chat system
- RAG
