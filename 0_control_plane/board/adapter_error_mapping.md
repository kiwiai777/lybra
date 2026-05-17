# Adapter Error Mapping

## Purpose

This document defines stable error categories for the local Board adapter.

All categories below are safe-to-execute `false` unless a later revalidation returns `PASS`.

## Error Categories

| Category | Meaning | Typical Source | Message Guidance | Retryable |
| --- | --- | --- | --- | --- |
| `VALIDATION_ERROR` | Request payload or backend validation failed | adapter payload validation, backend validator | explain which field or rule failed | false |
| `NOT_FOUND` | Target task, draft, record, or repo input not found | task lookup, path lookup, repo discovery | name the missing target and selector | false |
| `DUPLICATE_ID` | Multiple tasks or drafts resolve to the same identifier | task index, draft lookup | ask caller to resolve duplicate IDs before retry | false |
| `PATH_UNSAFE` | Path is absolute, traverses upward, or escapes allowed roots | adapter path normalization, backend path guard | state that only safe repo-relative paths are allowed | false |
| `ACTOR_MISMATCH` | Caller identity does not match task assignment or alias rules | preview, claim, complete, block, reopen | show requested actor and mismatch reason | false |
| `STATUS_MISMATCH` | Task state or directory does not match requested operation | queue mutation validation | state expected versus actual status | false |
| `OWNER_CONFIRMATION_REQUIRED` | Operation needs explicit owner confirmation | mutation safety policy, backend warning escalation | explain the owner-gated reasons and stop execute | false |
| `RECORD_COLLISION` | Claim or session record would conflict with existing records | queue mutation with records | identify record collision target and require user review | false |
| `UNSUPPORTED_OPERATION` | Endpoint or mutation type is not available in this stage | adapter routing | state that the operation is outside current adapter scope | false |
| `INTERNAL_ERROR` | Unexpected internal failure with no better mapping | adapter or backend unexpected exception | provide concise failure summary and preserve diagnostics | maybe |
| `ADAPTER_INVOCATION_ERROR` | Adapter failed to invoke backend correctly | subprocess launch error, missing executable, invalid argv | say backend invocation failed before semantic execution | maybe |
| `BACKEND_TIMEOUT` | Backend call exceeded adapter timeout | module timeout wrapper, subprocess timeout | tell caller the backend did not finish in time | true |
| `BACKEND_PARSE_ERROR` | Backend returned malformed or non-JSON data where structured data was required | subprocess JSON decode, schema parse failure | state that backend output was not consumable | maybe |
| `BACKEND_CONTRACT_MISMATCH` | Backend returned data that does not match expected semantic contract | missing fields, parity mismatch, inconsistent dry-run data | explain which contract field was missing or inconsistent | false |
| `REVALIDATION_FAILED` | Execute-time validation rerun failed | dry-run replay and immediate execute check | explain why the revalidation no longer allows execute | false |
| `DRY_RUN_REQUIRED` | Controlled mutation execute requested without prior dry-run proof | adapter execute gate | instruct caller to perform dry-run first | false |
| `STALE_DRY_RUN` | Prior dry-run proof is expired or no longer matches current state | stale token or hash, actor drift, task drift | tell caller to re-run dry-run because preview is stale | false |

## Mapping Guidance

Top-level adapter guidance:

- prefer semantic categories over generic `INTERNAL_ERROR`
- preserve backend-native diagnostics in `errors[].details`
- keep user-facing `message` concise and action-oriented
- never treat an unmapped backend failure as safe-to-execute

## Error Object Shape

```json
{
  "category": "STALE_DRY_RUN",
  "message": "Dry-run preview no longer matches current queue state.",
  "field": "dry_run_snapshot_hash",
  "retryable": false,
  "safe_to_execute": false,
  "details": {
    "task_id": "EXAMPLE-001"
  }
}
```
