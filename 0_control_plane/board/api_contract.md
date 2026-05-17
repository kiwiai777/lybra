# Backend API Contract for Board UI

## Purpose

This document defines the Board/API contract for future Board UI work against the existing file-driven AIPOS backend.

The contract is protocol-only in AIPOS-34.

No API server, Web UI, database, daemon, or agent runtime is implemented here.

## Core Contract Principles

- Board UI must not write files directly.
- Board UI must call backend contract / adapter.
- Adapter only calls existing backend primitives.
- Adapter never invents new writer behavior.
- Backend remains file-driven.
- No database in this stage.
- Local-only first.
- Every mutation must support dry-run.
- High-risk mutation should be dry-run-first.
- Mutation response must expose `planned_writes`, `planned_moves`, `warnings`, and `blocking_reasons`.
- Execute after dry-run must revalidate, not blindly trust stale preview.
- No agent execution through Board UI in this contract.
- No orchestration runtime in this contract.

## Adapter Boundary Summary

AIPOS-35 defines the local adapter boundary for this contract.

- Phase 1 adapter should call Python backend modules directly when stable module APIs exist.
- `cli_subprocess` is allowed as a compatibility fallback only.
- A `hybrid` adapter is the recommended transition model.
- Long-term target remains `module`.
- Board UI never writes files directly.
- Adapter must not execute agents or runtime commands.
- Adapter must not write files directly.
- Adapter must normalize all backend results into the common response envelope.
- Adapter execute flows must require dry-run proof and immediate revalidation before mutation.

## MCP Surface Compatibility

AIPOS-96 defines a future MCP server surface that should preserve this Board/API contract.

MCP read tools should map to existing read endpoint semantics and response envelopes where applicable. MCP mutation tools must map only to existing controlled execute operations and must not introduce direct writes, hidden allowlist expansion, Owner-gate bypass, runtime launch, agent execution, or deployment behavior.

AIPOS-96 does not add API endpoints, MCP tool code, transports, CLI commands, Web UI controls, credentials, or live server behavior.

## Endpoint Groups

The Board/API contract is organized into three groups:

- read endpoints
- draft mutation endpoints
- queue mutation endpoints

## Read Endpoints

### `GET /health`

Purpose: report whether the local backend adapter can locate repo root and read core inputs.

Request params: none

Response shape:

```json
{
  "ok": true,
  "verdict": "PASS",
  "operation": "health_check",
  "data": {
    "repo_root_found": true,
    "queue_root_found": true,
    "records_root_found": false
  },
  "errors": []
}
```

Source backend CLI/module: adapter-level check using repo discovery, no current direct CLI command

Errors:

- `NOT_FOUND`
- `INTERNAL_ERROR`

Side effects: none

### `GET /queue`

Purpose: render queue state for Board queue view.

Request params:

- `actor` optional
- `include_records` optional future flag

Response shape: queue validation JSON envelope with queue tasks and records summary.

Source backend CLI/module: `aipos_cli.py queue --json`

Errors:

- `INTERNAL_ERROR`

Side effects: none

### `GET /tasks/{task_id}`

Purpose: fetch task detail by canonical task ID.

Request params:

- path param: `task_id`

Response shape: task detail JSON for one resolved task.

Source backend CLI/module: `aipos_cli.py task --task-id <task_id> --json`

Errors:

- `NOT_FOUND`
- `DUPLICATE_ID`
- `INTERNAL_ERROR`

Side effects: none

### `GET /tasks/by-path`

Purpose: fetch task detail by repo-relative task path.

Request params:

- query param: `path`

Response shape: task detail JSON for one resolved task path.

Source backend CLI/module: `aipos_cli.py task --path <path> --json`

Errors:

- `NOT_FOUND`
- `PATH_UNSAFE`
- `INTERNAL_ERROR`

Side effects: none

### `GET /preview/{task_id}`

Purpose: preview start-task session details for a task/actor pair.

Request params:

- path param: `task_id`
- query param: `actor` required
- query param: `agent_instance` optional
- query param: `runtime_profile` optional

Response shape: preview JSON envelope with actor match details, records references, and warnings.

Source backend CLI/module: `aipos_cli.py preview --task-id <task_id> --actor <actor> --json`

Errors:

- `NOT_FOUND`
- `ACTOR_MISMATCH`
- `INTERNAL_ERROR`

Side effects: none

### `GET /needs-owner`

Purpose: return tasks requiring owner attention.

Request params: none

Response shape: needs-owner queue subset.

Source backend CLI/module: `aipos_cli.py needs-owner --json`

Errors:

- `INTERNAL_ERROR`

Side effects: none

### `GET /records`

Purpose: return records summary and parsed claim/session records.

Request params: none

Response shape: records JSON envelope.

Source backend CLI/module: `aipos_cli.py records --json`

Errors:

- `INTERNAL_ERROR`

Side effects: none

### `GET /agents`

Purpose: return logical agent, alias, instance, and availability information for Board selectors.

Request params: none

Response shape: agents JSON envelope.

Source backend CLI/module: `aipos_cli.py agents --json`

Errors:

- `INTERNAL_ERROR`

Side effects: none

### `GET /drafts`

Purpose: return visible drafts and draft validation verdicts.

Request params: none

Response shape: draft list JSON envelope.

Source backend CLI/module: `aipos_cli.py draft list --json`

Errors:

- `INTERNAL_ERROR`

Side effects: none

## Draft Mutation Endpoints

### `POST /drafts/create:dry-run`

Purpose: validate and preview a draft before creating a draft file.

Required request body fields:

- `source_mode`: `from_json` or `from_template`

Optional request body fields:

- `frontmatter`
- `body`
- `template`

Dry-run behavior:

- validate request payload
- compute `target_path`
- compute `planned_writes`
- return `rendered_markdown`
- do not write files

Execute behavior: not applicable, dry-run only

Validation requirements:

- same as `draft create --dry-run`

Possible blocking reasons:

- missing required frontmatter fields
- duplicate draft task ID
- path-unsafe task ID

Side effects: none

### `POST /drafts/create`

Purpose: create a new draft file under `5_tasks/drafts/`.

Required request body fields:

- same payload family as dry-run

Optional request body fields:

- same as dry-run

Dry-run behavior:

- client should call `POST /drafts/create:dry-run` first

Execute behavior:

- revalidate full request
- create draft file only if still safe

Validation requirements:

- same as dry-run

Response shape:

- common mutation envelope
- includes `target_path`
- includes `planned_writes`

Possible blocking reasons:

- duplicate draft path
- duplicate task ID
- validation failure

Side effects:

- write draft file only

### `POST /drafts/validate`

Purpose: validate one draft path.

Required request body fields:

- `path`

Optional request body fields: none

Dry-run behavior: not applicable because endpoint is read/validate only

Execute behavior:

- validate the supplied draft path

Validation requirements:

- draft path must stay inside `5_tasks/drafts/`

Response shape:

- common envelope with validation data only

Possible blocking reasons:

- draft missing
- non-markdown
- frontmatter invalid

Side effects: none

### `POST /drafts/publish:dry-run`

Purpose: preview draft publish to pending queue.

Required request body fields:

- `path`

Optional request body fields: none

Dry-run behavior:

- validate draft
- compute `target_path`
- compute `planned_writes`
- return `rendered_markdown`
- do not write queue files

Execute behavior: not applicable, dry-run only

Validation requirements:

- full draft publish validation

Response shape:

- common mutation envelope
- includes `planned_writes`
- includes `blocking_reasons`

Possible blocking reasons:

- invalid draft
- duplicate task ID
- publish target exists

Side effects: none

### `POST /drafts/publish`

Purpose: publish a validated draft to pending queue.

Required request body fields:

- `path`

Optional request body fields: none

Dry-run behavior:

- client must call `POST /drafts/publish:dry-run` first

Execute behavior:

- revalidate
- write pending queue file only if still safe

Validation requirements:

- same as dry-run

Response shape:

- common mutation envelope
- includes `planned_writes`
- includes `performed_writes`

Possible blocking reasons:

- stale preview
- duplicate task ID
- target collision

Side effects:

- write pending queue file

## Queue Mutation Endpoints

All queue mutation endpoints support:

- `task_id` or `path`
- `actor` required
- `agent_instance` optional
- `runtime_profile` optional
- `with_records` boolean

Board contract rule:

- dry-run is required before execute for all controlled queue mutations
- execute must revalidate actor match, queue state, collisions, and records safety

### `POST /queue/claim:dry-run`

Required request body fields:

- `task_id` or `path`
- `actor`

Optional request body fields:

- `agent_instance`
- `runtime_profile`
- `with_records`

Dry-run behavior:

- validate actor match
- validate `pending -> claimed`
- compute queue `planned_writes` and `planned_moves`
- when `with_records == true`, compute `proposed_claim_id`, `proposed_session_id`, `record_writes`, and record previews

Execute behavior: not applicable, dry-run only

Allowed transition:

- `pending -> claimed`

Side effects: none

### `POST /queue/claim`

Required request body fields:

- same as claim dry-run

Optional request body fields:

- same as claim dry-run

Dry-run behavior:

- caller must invoke `POST /queue/claim:dry-run` first

Execute behavior:

- revalidate source state
- revalidate actor match
- write queue mutation
- optionally create claim/session records when `with_records == true`

Owner confirmation requirement:

- required if owner-confirmation conditions are met in response contract

Side effects:

- queue mutation
- optional records write

### `POST /queue/block:dry-run`

Required request body fields:

- `task_id` or `path`
- `actor`
- `reason`

Optional request body fields:

- `agent_instance`
- `runtime_profile`
- `with_records`

Dry-run behavior:

- validate `claimed -> blocked`
- validate non-empty `reason`
- compute `planned_writes` / `planned_moves`
- if `with_records == true`, show session record update or safe block reason

Execute behavior: not applicable, dry-run only

Allowed transition:

- `claimed -> blocked`

Side effects: none

### `POST /queue/block`

Required request body fields:

- same as block dry-run

Execute behavior:

- revalidate
- update queue task
- optionally update session record when `with_records == true`

Owner confirmation requirement:

- likely true when resulting task has `needs_owner == true`

Side effects:

- queue mutation
- optional session record update

### `POST /queue/complete:dry-run`

Required request body fields:

- `task_id` or `path`
- `actor`
- `report_link`

Optional request body fields:

- `agent_instance`
- `runtime_profile`
- `with_records`

Dry-run behavior:

- validate `claimed -> completed`
- validate non-empty `report_link`
- compute queue mutation plan
- if `with_records == true`, show session record update plan or blocking reason

Execute behavior: not applicable, dry-run only

Allowed transition:

- `claimed -> completed`

Side effects: none

### `POST /queue/complete`

Required request body fields:

- same as complete dry-run

Execute behavior:

- revalidate
- update queue task
- optionally update session record when `with_records == true`

Owner confirmation requirement:

- required when high-risk or owner-review conditions are present

Side effects:

- queue mutation
- optional session record update

### `POST /queue/reopen:dry-run`

Required request body fields:

- `task_id` or `path`
- `actor`
- `reason`

Optional request body fields:

- `agent_instance`
- `runtime_profile`
- `with_records`

Dry-run behavior:

- validate `blocked -> pending`
- validate non-empty `reason`
- compute queue mutation plan
- if `with_records == true`, show session update plan or warning when no session reference exists

Execute behavior: not applicable, dry-run only

Allowed transition:

- `blocked -> pending`

Side effects: none

### `POST /queue/reopen`

Required request body fields:

- same as reopen dry-run

Execute behavior:

- revalidate
- update queue task
- optionally update existing session record when `with_records == true`

Owner confirmation requirement:

- required if owner-review conditions remain true after validation

Side effects:

- queue mutation
- optional session record update
