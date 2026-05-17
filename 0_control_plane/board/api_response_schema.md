# Board API Response Schema

## Common Envelope

All Board/API responses should normalize to this envelope:

```yaml
ok: true | false
verdict: PASS | WARN | NEEDS_OWNER | BLOCK
operation:
dry_run: true | false
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
```

Future execute-time revalidation fields:

```yaml
dry_run_id:
dry_run_snapshot_hash:
dry_run_created_at:
dry_run_expires_at:
execute_requested_at:
revalidation_performed:
revalidation_summary:
```

Future controlled execute fields:

```yaml
current_snapshot_hash:
revalidation_result:
owner_confirmation_checked:
owner_confirmation_result:
executed_operation:
post_state:
rollback_available:
```

## Field Definitions

- `ok`: transport and contract-level success flag
- `verdict`: semantic outcome for display and mutation gating
- `operation`: stable operation name such as `queue_claim` or `draft_publish`
- `dry_run`: whether response is preview-only
- `actor`: caller identity payload when relevant
- `actor_match`: normalized actor-match result from backend when available
- `timestamp`: response creation time in UTC ISO format
- `data`: primary payload, such as queue tasks, task detail, or mutation result
- `summary`: compact totals for list endpoints
- `planned_writes`: writes proposed during dry-run
- `planned_moves`: moves proposed during dry-run
- `performed_writes`: writes actually performed during execute
- `performed_moves`: moves actually performed during execute
- `warnings`: non-blocking issues that UI must display
- `blocking_reasons`: reasons that must stop execute
- `needs_owner_reasons`: reasons that escalate to owner review
- `owner_confirmation_required`: whether execute requires explicit owner confirmation
- `owner_confirmation_reasons`: machine-readable reasons for owner confirmation
- `safety_notice`: human-readable safety boundary
- `errors`: stable error objects

## Verdict Conventions

- `PASS` means operation is safe under current contract.
- `WARN` means operation is allowed but warnings must be displayed.
- `NEEDS_OWNER` means Owner review or confirmation is required.
- `BLOCK` means operation must not execute.

## Error Categories

Stable error categories:

- `VALIDATION_ERROR`
- `NOT_FOUND`
- `DUPLICATE_ID`
- `PATH_UNSAFE`
- `ACTOR_MISMATCH`
- `STATUS_MISMATCH`
- `OWNER_CONFIRMATION_REQUIRED`
- `RECORD_COLLISION`
- `UNSUPPORTED_OPERATION`
- `INTERNAL_ERROR`
- `ADAPTER_INVOCATION_ERROR`
- `BACKEND_TIMEOUT`
- `BACKEND_PARSE_ERROR`
- `BACKEND_CONTRACT_MISMATCH`
- `REVALIDATION_FAILED`
- `DRY_RUN_REQUIRED`
- `STALE_DRY_RUN`

## Error Object Shape

```json
{
  "category": "VALIDATION_ERROR",
  "message": "Draft path is not a markdown file",
  "field": "path",
  "details": {}
}
```

## AIPOS-36 Notes

- AIPOS-36 implements this envelope through `tools.aipos_cli.adapter_response`
- read adapter calls return JSON-serializable envelope objects directly from a local Python module
- mutation preview calls keep `performed_writes` and `performed_moves` empty
- execute mutation calls are blocked by default in AIPOS-36 and should surface `DRY_RUN_REQUIRED`

## Read Response Example

```json
{
  "ok": true,
  "verdict": "PASS",
  "operation": "get_queue",
  "dry_run": false,
  "actor": null,
  "actor_match": null,
  "timestamp": "2026-04-30T00:00:00Z",
  "data": {
    "tasks": []
  },
  "summary": {
    "total_tasks": 0
  },
  "planned_writes": [],
  "planned_moves": [],
  "performed_writes": [],
  "performed_moves": [],
  "warnings": [],
  "blocking_reasons": [],
  "needs_owner_reasons": [],
  "owner_confirmation_required": false,
  "owner_confirmation_reasons": [],
  "safety_notice": "Read-only endpoint.",
  "errors": []
}
```

## Mutation Dry-run Example

```json
{
  "ok": true,
  "verdict": "WARN",
  "operation": "queue_claim",
  "dry_run": true,
  "actor": {
    "actor": "info.hermes.cloud",
    "agent_instance": "info.hermes.cloud.l1",
    "runtime_profile": null
  },
  "actor_match": {
    "matched": true
  },
  "timestamp": "2026-04-30T00:00:00Z",
  "data": {
    "task_id": "EXAMPLE-001",
    "with_records": true
  },
  "summary": null,
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
  "performed_writes": [],
  "performed_moves": [],
  "warnings": [
    "Missing artifact_scope"
  ],
  "blocking_reasons": [],
  "needs_owner_reasons": [],
  "owner_confirmation_required": false,
  "owner_confirmation_reasons": [],
  "safety_notice": "Controlled mutation dry-run only.",
  "errors": []
}
```

## Owner Confirmation Fields

Contract fields for owner confirmation:

```yaml
owner_confirmation_required: true | false
owner_confirmation_reasons: []
owner_confirmation_token: optional future field
confirmation_summary:
confirmed_by:
confirmed_at:
```

In AIPOS-34:

- `owner_confirmation_token` is protocol-only
- no confirmation UI is implemented
- no confirmation persistence is implemented

Additional AIPOS-35 protocol-only fields:

- `dry_run_id`
- `dry_run_snapshot_hash`
- `dry_run_created_at`
- `dry_run_expires_at`
- `execute_requested_at`
- `revalidation_performed`
- `revalidation_summary`

Additional AIPOS-37 protocol-only fields:

- `current_snapshot_hash`
- `revalidation_result`
- `owner_confirmation_checked`
- `owner_confirmation_result`
- `executed_operation`
- `post_state`
- `rollback_available`

## Records-aware Mutation Fields

For queue mutation with `with_records == true`, `data` should additionally expose:

- `with_records`
- `records_enabled`
- `proposed_claim_id`
- `proposed_session_id`
- `record_writes`
- `record_updates`
- `record_blocking_reasons`
- `record_warnings`

This keeps `planned_writes`, `planned_moves`, and records plans visible in one envelope.
