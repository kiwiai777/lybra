# AI Project OS CLI MVP

## Purpose

`tools/aipos_cli/` contains the CLI / renderer MVP for AI Project OS.

It scans the file-driven task queue, parses Markdown frontmatter, runs a minimal validator subset, and now includes tightly scoped draft create/validate/list/publish, controlled queue mutation, opt-in records writing, append-only orchestration event writing, and append-only planner iteration writing.

## Commands

Run from repo root:

```bash
python tools/aipos_cli/aipos_cli.py queue
python tools/aipos_cli/aipos_cli.py my-tasks --actor dev.codex.local
python tools/aipos_cli/aipos_cli.py needs-owner
python tools/aipos_cli/aipos_cli.py validate
python tools/aipos_cli/aipos_cli.py validate --json
python tools/aipos_cli/aipos_cli.py records
python tools/aipos_cli/aipos_cli.py records --json
python tools/aipos_cli/aipos_cli.py agents
python tools/aipos_cli/aipos_cli.py agents --json
python tools/aipos_cli/aipos_cli.py task --task-id EXAMPLE-001
python tools/aipos_cli/aipos_cli.py task --path 5_tasks/queue/pending/example_task.md
python tools/aipos_cli/aipos_cli.py preview --task-id EXAMPLE-001 --actor dev.codex.local
python tools/aipos_cli/aipos_cli.py preview --path 5_tasks/queue/pending/example_task.md --actor dev.codex.local
python tools/aipos_cli/aipos_cli.py preview --task-id EXAMPLE-001 --actor dev.codex.local --json
python tools/aipos_cli/aipos_cli.py draft create --from-json /tmp/draft_payload.json --dry-run --json
python tools/aipos_cli/aipos_cli.py draft create --from-template basic --task-id AIPOS-29-EXAMPLE --title "Example Draft" --assigned-to dev.codex.local --context-bundle dev.codex.local --task-mode code --priority medium --created-by Kiwi_gpt --output-target tools/aipos_cli/ --artifact-policy formal_write
python tools/aipos_cli/aipos_cli.py draft validate --path 5_tasks/drafts/aipos-29-example.md --json
python tools/aipos_cli/aipos_cli.py draft list --json
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --dry-run --json
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --json
python tools/aipos_cli/aipos_cli.py queue claim --task-id EXAMPLE-001 --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py queue claim --task-id EXAMPLE-001 --actor dev.codex.local --with-records --dry-run --json
python tools/aipos_cli/aipos_cli.py queue block --path 5_tasks/queue/claimed/example-001.md --actor dev.codex.local --reason "Waiting on owner" --dry-run --json
python tools/aipos_cli/aipos_cli.py queue complete --task-id EXAMPLE-001 --actor dev.codex.local --report-link https://example.com/report --dry-run --json
python tools/aipos_cli/aipos_cli.py queue reopen --path 5_tasks/queue/blocked/example-001.md --actor dev.codex.local --reason "Inputs arrived" --dry-run --json
python tools/aipos_cli/aipos_cli.py orchestration event append --from-json /tmp/orchestration_event.json --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py orchestration event append --from-json /tmp/orchestration_event.json --actor dev.codex.local --expected-hash <write_snapshot_hash> --json
python tools/aipos_cli/aipos_cli.py orchestration iteration append --from-json /tmp/planner_iteration.json --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py orchestration iteration append --from-json /tmp/planner_iteration.json --actor dev.codex.local --expected-hash <write_snapshot_hash> --json
python tools/aipos_cli/aipos_cli.py orchestration summary preview --orchestration-id orch_example --json
```

Module execution also works:

```bash
python -m tools.aipos_cli.aipos_cli queue
```

Optional JSON output:

```bash
python tools/aipos_cli/aipos_cli.py queue --json
python tools/aipos_cli/aipos_cli.py my-tasks --actor dev.codex.local --json
python tools/aipos_cli/aipos_cli.py needs-owner --json
python tools/aipos_cli/aipos_cli.py records --json
python tools/aipos_cli/aipos_cli.py agents --json
python tools/aipos_cli/aipos_cli.py task --task-id EXAMPLE-001 --json
python tools/aipos_cli/aipos_cli.py preview --task-id EXAMPLE-001 --actor dev.codex.local --json
python tools/aipos_cli/aipos_cli.py draft create --from-json /tmp/draft_payload.json --dry-run --json
python tools/aipos_cli/aipos_cli.py draft validate --path 5_tasks/drafts/aipos-29-example.md --json
python tools/aipos_cli/aipos_cli.py draft list --json
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --dry-run --json
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --json
python tools/aipos_cli/aipos_cli.py queue claim --task-id EXAMPLE-001 --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py queue claim --task-id EXAMPLE-001 --actor dev.codex.local --with-records --dry-run --json
python tools/aipos_cli/aipos_cli.py queue block --task-id EXAMPLE-001 --actor dev.codex.local --reason "Waiting on owner" --dry-run --json
python tools/aipos_cli/aipos_cli.py queue complete --task-id EXAMPLE-001 --actor dev.codex.local --report-link https://example.com/report --dry-run --json
python tools/aipos_cli/aipos_cli.py queue reopen --task-id EXAMPLE-001 --actor dev.codex.local --reason "Inputs arrived" --dry-run --json
python tools/aipos_cli/aipos_cli.py orchestration event append --from-json /tmp/orchestration_event.json --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py orchestration iteration append --from-json /tmp/planner_iteration.json --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py orchestration summary preview --orchestration-id orch_example --json
```

`validate --json` is the central machine-readable validator report. In addition to queue verdicts, it includes top-level `records_summary`, top-level `records_diagnostics`, and per-task `record_ref_checks` plus compact `records` counts for Board/UI consumers.

## Board Adapter Boundary

AIPOS-35 defines how a future local Board adapter should sit in front of this CLI and related backend modules.

- preferred long-term adapter mode is `module`
- recommended transition mode is `hybrid`
- CLI subprocess calls remain a compatibility fallback and smoke boundary
- authoritative adapter operations should use structured Python results or CLI `--json`
- human-readable CLI output is not an authoritative adapter contract
- Board UI never writes files directly
- adapter must not execute agents or runtime commands
- adapter must not perform Git operations

## Local Board API Adapter MVP

AIPOS-36 implements the first local importable adapter module:

- module path: `tools.aipos_cli.board_adapter`
- response helpers: `tools.aipos_cli.adapter_response`
- adapter is not a server
- adapter is not a Web UI
- adapter does not use a CLI runtime bridge
- adapter does not write files directly
- adapter execute mutations are blocked by default
- AIPOS-37 defines the controlled execute contract only
- execute remains blocked until dry-run token and revalidation contract are implemented
- AIPOS-38 may implement controlled execute MVP

Read adapter functions:

- `get_health`
- `get_queue`
- `get_needs_owner`
- `get_validate`
- `get_records`
- `get_agents`
- `get_task`
- `get_preview`
- `get_drafts`

Dry-run mutation adapter functions:

- `create_draft`
- `validate_draft`
- `publish_draft`
- `claim_task`
- `block_task`
- `complete_task`
- `reopen_task`

Example:

```python
from tools.aipos_cli.board_adapter import get_health, get_queue, claim_task

health = get_health()
queue = get_queue()
claim_preview = claim_task(task_id="EXAMPLE-001", actor="dev.codex.local", dry_run=True)
```

## Safe Writer Boundary

Records, orchestration, and preview commands remain read-only.

`draft create`, `draft validate`, `draft list`, `draft publish`, `queue claim/block/complete/reopen`, opt-in records writing, append-only orchestration event writing, and append-only planner iteration writing now define the safe writer boundary:

- writes are limited to `5_tasks/drafts/`
- `draft publish` may additionally write only to `5_tasks/queue/pending/`
- queue mutation may write only to `5_tasks/queue/pending/`, `claimed/`, `blocked/`, and `completed/`
- queue mutation with `--with-records` may additionally write only to `5_tasks/records/claims/` and `5_tasks/records/sessions/`
- `orchestration event append` may write only one append-only event entry to `5_tasks/orchestration/{orchestration_id}/orchestration_events.md`
- `orchestration iteration append` may write only one append-only planner iteration entry to `5_tasks/orchestration/{orchestration_id}/planner_iterations.md`
- `draft create --dry-run` never creates directories or files
- `draft publish --dry-run` never creates directories or files
- queue mutation `--dry-run` never creates directories or files
- `orchestration event append --dry-run` never creates directories or files
- `orchestration iteration append --dry-run` never creates directories or files
- draft output target is always `5_tasks/drafts/{task_id_slug}.md`
- publish target is always `5_tasks/queue/pending/{task_id_slug}.md`
- queue mutation target is always the same filename under the destination queue state directory
- no overwrite or force flag exists
- path traversal and out-of-drafts validation paths are blocked
- publish validates before write
- publish copies the validated draft and does not delete or modify the source draft
- queue mutation validates before write and only allows `pending -> claimed`, `claimed -> blocked`, `claimed -> completed`, and `blocked -> pending`
- queue mutation updates task frontmatter and moves task files only within `5_tasks/queue/`
- records writing is opt-in via `--with-records`
- orchestration event append requires a dry-run `write_snapshot_hash`, execute-time `--expected-hash`, and actor match against the payload actor
- planner iteration append requires a dry-run `write_snapshot_hash`, execute-time `--expected-hash`, L3/L4 planner tier, visible forum/control-plane reference, allowed verdict, and actor match against `planner_agent` or `planner_agent_instance`
- planner iteration append may preserve advisory session continuity metadata such as `active_session_id`, `prior_session_id`, `session_resume_ref`, and `role_continuity_preference`, but it does not resume, launch, lease, or assign agent sessions
- claim with records creates one claim log and one session record
- block / complete update the existing session record when records are enabled
- reopen updates an existing session record when a session reference exists and otherwise warns safely
- no summary orchestration state, forum backend, queue tasks, drafts, or records are written by orchestration event append or planner iteration append
- `orchestration summary preview` is read-only and never returns a write token, dry-run token, or confirmation path
- no agents are run

The CLI still does not:

- create session records
- create claim logs
- write record files
- execute runtime commands
- repair YAML
- call external services
- execute agents
- persist proposed preview IDs

## Current Coverage

The MVP can:

- scan `5_tasks/queue/*`
- scan `5_tasks/records/sessions/` and `5_tasks/records/claims/`
- load declarative agent runtime profiles
- parse Markdown task frontmatter
- infer queue state from directory
- run basic validator checks
- render read-only records summary
- render Agent Profiles
- render Task Queue
- render My Tasks
- render Needs Owner
- output JSON for `validate --json`
- render single task detail by `task_id` or `path`
- render read-only Start Task Session Preview
- show linked records and record-reference checks in task detail / preview
- support alias-aware actor matching for `my-tasks` and `preview`
- create draft task cards from JSON payloads
- create draft task cards from the built-in `basic` template
- validate draft task cards under `5_tasks/drafts/`
- list draft task cards with validation verdicts
- publish validated draft task cards to `5_tasks/queue/pending/`
- claim tasks from `pending` into `claimed`
- block tasks from `claimed` into `blocked`
- complete tasks from `claimed` into `completed`
- reopen tasks from `blocked` into `pending`
- optionally create claim/session records during queue mutation when `--with-records` is set
- append one orchestration event entry to `5_tasks/orchestration/{orchestration_id}/orchestration_events.md` after dry-run hash revalidation
- append one planner iteration entry to `5_tasks/orchestration/{orchestration_id}/planner_iterations.md` after dry-run hash revalidation
- preview reconstructable orchestration summary state from queue tasks, records, planner iterations, and orchestration events without writing files

## Draft Commands

Draft create from JSON:

```bash
python tools/aipos_cli/aipos_cli.py draft create --from-json /tmp/draft_payload.json
python tools/aipos_cli/aipos_cli.py draft create --from-json /tmp/draft_payload.json --dry-run
python tools/aipos_cli/aipos_cli.py draft create --from-json /tmp/draft_payload.json --dry-run --json
```

Draft create from template:

```bash
python tools/aipos_cli/aipos_cli.py draft create \
  --from-template basic \
  --task-id AIPOS-29-EXAMPLE \
  --title "Example Draft Task" \
  --assigned-to dev.codex.local \
  --context-bundle dev.codex.local \
  --task-mode code \
  --priority medium \
  --created-by Kiwi_gpt \
  --output-target tools/aipos_cli/ \
  --artifact-policy formal_write \
  --dry-run --json
```

Draft validate and list:

```bash
python tools/aipos_cli/aipos_cli.py draft validate --path 5_tasks/drafts/aipos-29-example.md
python tools/aipos_cli/aipos_cli.py draft validate --path 5_tasks/drafts/aipos-29-example.md --json
python tools/aipos_cli/aipos_cli.py draft list
python tools/aipos_cli/aipos_cli.py draft list --json
```

Draft publish:

```bash
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --dry-run
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --json
python tools/aipos_cli/aipos_cli.py draft publish --path 5_tasks/drafts/aipos-30-example.md --dry-run --json
```

Queue mutation:

```bash
python tools/aipos_cli/aipos_cli.py queue claim --task-id EXAMPLE-001 --actor dev.codex.local
python tools/aipos_cli/aipos_cli.py queue claim --path 5_tasks/queue/pending/example-001.md --actor dev.codex.local --dry-run --json
python tools/aipos_cli/aipos_cli.py queue claim --task-id EXAMPLE-001 --actor dev.codex.local --with-records --dry-run --json
python tools/aipos_cli/aipos_cli.py queue block --task-id EXAMPLE-001 --actor dev.codex.local --reason "Waiting on owner"
python tools/aipos_cli/aipos_cli.py queue complete --task-id EXAMPLE-001 --actor dev.codex.local --report-link https://example.com/report
python tools/aipos_cli/aipos_cli.py queue reopen --path 5_tasks/queue/blocked/example-001.md --actor dev.codex.local --reason "Inputs arrived"
```

Allowed transitions:

- `pending -> claimed`
- `claimed -> blocked`
- `claimed -> completed`
- `blocked -> pending`

Forbidden transitions:

- `completed -> anything`
- `pending -> completed`
- `pending -> blocked`
- `blocked -> claimed`
- `claimed -> pending`

Create dry-run JSON includes:

- `action`
- `dry_run`
- `would_write`
- `target_path`
- `task_id`
- `verdict`
- `blocking_reasons`
- `warnings`
- `rendered_markdown`
- `planned_writes`

Validate JSON includes:

- `action`
- `path`
- `task_id`
- `verdict`
- `blocking_reasons`
- `warnings`
- `frontmatter`

List JSON includes:

- `action`
- `drafts_dir`
- `total`
- `drafts`

Publish dry-run JSON includes:

- `action`
- `dry_run`
- `would_write`
- `wrote`
- `source_path`
- `target_path`
- `task_id`
- `verdict`
- `blocking_reasons`
- `warnings`
- `planned_writes`
- `rendered_markdown`
- `validation`

Publish write JSON includes:

- `action`
- `dry_run`
- `would_write`
- `wrote`
- `source_path`
- `target_path`
- `task_id`
- `verdict`
- `blocking_reasons`
- `warnings`
- `planned_writes`
- `validation`

Publish validation boundary:

- source must resolve inside `5_tasks/drafts/`
- source must be an existing markdown file
- frontmatter must include required fields and `status: pending`
- forbidden runtime-state fields are blocked
- duplicate `task_id` in drafts or any queue state is blocked
- target must resolve inside `5_tasks/queue/pending/`
- existing target or case-insensitive filename collision is blocked
- publish copies the draft markdown exactly
- source draft is never deleted or modified
- only `5_tasks/queue/pending/` may be written by publish

Queue mutation JSON includes:

- `action`
- `dry_run`
- `would_write`
- `wrote`
- `would_move`
- `moved`
- `task_id`
- `source_path`
- `target_path`
- `from_state`
- `to_state`
- `actor`
- `verdict`
- `blocking_reasons`
- `warnings`
- `planned_writes`
- `planned_moves`
- `updated_frontmatter`
- `rendered_markdown` for dry-run
- `safety_notice`

When `--with-records` is enabled, queue mutation JSON also includes:

- `with_records`
- `records_enabled`
- `proposed_claim_id`
- `proposed_session_id`
- `claim_log_path`
- `session_record_path`
- `record_writes`
- `record_updates`
- `record_blocking_reasons`
- `record_warnings`
- `record_previews` for dry-run

Queue mutation runtime metadata:

- `queue claim`: writes `status`, `claimed_by`, `claimed_at`, `claim_id`, `active_session_id`
- `queue block`: writes `status`, `blocked_by`, `blocked_at`, `block_reason`, `needs_owner: true`, and rolls `active_session_id` into `last_session_id`
- `queue complete`: writes `status`, `completed_by`, `completed_at`, appends `artifact_links`, and rolls `active_session_id` into `last_session_id`
- `queue reopen`: writes `status`, `reopened_by`, `reopened_at`, `reopen_reason`, `needs_owner: false`, and clears active claim state

Records writer behavior:

- default queue mutation remains AIPOS-31-compatible when `--with-records` is omitted
- `queue claim --with-records` creates:
  - `5_tasks/records/claims/{task_id}/{claim_id}.md`
  - `5_tasks/records/sessions/{task_id}/{session_id}.md`
- `queue block --with-records` updates the existing session record and preserves the claim log
- `queue complete --with-records` updates the existing session record and preserves the claim log
- `queue reopen --with-records` updates the referenced session record when present, otherwise warns and proceeds safely
- dry-run with records writes nothing and does not create `5_tasks/records/`

Queue mutation safety boundary:

- source path must resolve inside `5_tasks/queue/*/`
- `--task-id` must resolve to exactly one queue task
- directory and frontmatter status must match before mutation
- target file must not already exist
- case-insensitive filename collision blocks
- queue mutation writes no records
- queue mutation writes no orchestration state

## Agent Profiles

Agent profile docs live under `0_control_plane/agents/`.

The CLI distinguishes:

- `assigned_to`: logical agent identity
- `agent_instance`: concrete runtime instance
- `runtime_profile`: configurable profile name / UI selector name
- `runtime_entrypoint`: entrypoint type / tool family
- `runtime_command`: declarative command string
- `runtime_args`: declarative command-line args

`my-tasks --actor` and preview actor validation use alias-aware matching when agent profiles are available. If no profile docs can be loaded, the CLI falls back to direct matching.

Runtime configs are read-only and never executed by the CLI. `runtime_command`, `runtime_args`, and `runtime_env` are displayed as declarative configuration only.

`availability_status` is also read-only in the CLI. Allowed values are `online`, `offline`, `busy`, `maintenance`, and `unknown`.

`enabled` is a configuration switch. `availability_status` is an operational visibility field. In AIPOS-22, availability does not hide tasks, does not block alias matching, and does not execute or stop runtimes. It is warning/visibility only in `agents`, `my-tasks`, and `preview`.

## Orchestration Protocol Status

Planner-orchestrator protocol docs live under `0_control_plane/orchestration/`.

The current CLI is not an orchestrator. It does not run planner loops, create subtasks, poll quotas, fetch service website status, call provider dashboards, execute runtime commands, or mutate task state. Future CLI or Board surfaces may consume the orchestration schemas as read-only protocol inputs.

Current CLI can append orchestration event and planner iteration records under tightly scoped writer commands. It can also preview reconstructable orchestration summary state as read-only JSON. It still does not write orchestration summary state, subtask index, artifact links, forum backends, planner runtimes, or autonomous planner loops.

AIPOS-67 keeps summary state writing out of scope. The next safe slice should be a dry-run summary preview that computes proposed `orchestration_state.md` contents without writing files, returning execute-disabled metadata rather than a dry-run token.

AIPOS-68 implements that preview as:

```bash
python tools/aipos_cli/aipos_cli.py orchestration summary preview --orchestration-id orch_example --json
```

The preview returns `writes_enabled: false`, `execute_allowed: false`, `dry_run_token: null`, a `planned_summary` object, source refs, rebuild notes, warnings, and conflicts. It does not write `orchestration_state.md`, `loop_state.md`, `subtask_index.md`, `artifact_links.md`, queue files, draft files, records, runtime state, or git state.

AIPOS-69 exposes the same reconstruction output in the local Board UI as `GET /api/orchestration-summary?orchestration_id=<orchestration_id>`. The UI panel is read-only, mobile-responsive for review cards, and does not return execute tokens, write summary state, mutate queues, launch planner runtimes, or poll autonomously.

AIPOS-70 adds a read-only local Board timeline endpoint, `GET /api/orchestration-timeline?orchestration_id=<orchestration_id>`, backed by append-only `planner_iterations.md` and `orchestration_events.md`. It returns chronological timeline items, owner-attention markers, blocking markers, source refs, and no execute tokens or planned writes.

## Upcoming Safe Writer Scope

AIPOS-32 adds opt-in records writing for queue mutation.

Summary orchestration writing remains later.

## Record Directory Conventions

The CLI reads these paths when present:

- `5_tasks/records/sessions/{task_id}/{session_id}.md`
- `5_tasks/records/claims/{task_id}/{claim_id}.md`

Missing `5_tasks/records/`, `sessions/`, or `claims/` is treated as zero records without failing.

`records` and `records --json` parse frontmatter, summarize record counts, surface parse issues, and report duplicate or mismatched IDs as warnings. The CLI remains read-only and does not create records.

Regression coverage for the records reader lives in `tools/aipos_cli/tests/test_records_reader.py`.
The tests use temporary repository fixtures and cover missing `5_tasks/records`, valid session/claim parsing, `task_id` mismatch warnings, duplicate IDs, malformed frontmatter, and task-to-record reference checks.

`validate --json` exposes records visibility without changing verdict semantics for unrelated rules:

- missing records root: zero-summary only, not a failure
- absent `claim_id` / `active_session_id` / `last_session_id`: `absent` / `info`
- missing referenced record: `missing` / `warn`
- parse error in record file: `warn`
- duplicate record ID: `needs_owner`
- record `task_id` mismatch: `needs_owner`

Validator JSON regression coverage for this contract lives in `tools/aipos_cli/tests/test_validator_records_json.py`.

## Current Limitations

- validator is intentionally a subset of AIPOS-16, not the full rule matrix
- record validation is limited to read-only existence and conflict checks
- no standalone records create/update command exists yet
- no task repair action exists
- no Board UI or web server exists
- no database or background process exists
- fallback frontmatter parsing is simple and aimed at current task-card structure
- preview proposes `session_id` and `claim_id` but does not persist them
- preview proposes record paths but does not create them

Draft writer regression coverage lives in `tools/aipos_cli/tests/test_draft_writer.py`.
The tests use temporary repository fixtures and cover dry-run safety, draft-only writes, publish-to-pending writes under temp repos only, duplicate task IDs, path traversal blocking, out-of-drafts validation rejection, non-markdown rejection, overwrite blocking, queue collision detection, missing drafts directory handling, and JSON output validity.

Queue mutation regression coverage lives in `tools/aipos_cli/tests/test_queue_mutation.py`.
The tests use temporary repository fixtures and cover dry-run safety, allowed transitions, actor mismatch blocking, directory/status mismatch blocking, duplicate task ID blocking, overwrite blocking, path traversal rejection, non-markdown rejection, JSON output validity, and preservation of read-only commands.

Records writer regression coverage lives in `tools/aipos_cli/tests/test_record_writer.py`.
The tests use temporary repository fixtures and cover records dry-run safety, claim/session record creation, session record updates for block/complete/reopen, no-overwrite enforcement, unsafe path rejection, and records-reader compatibility for generated files.

## Notes

Future command names or record-writing flows are not implemented here.

This package is still an early backend MVP with tightly scoped draft, publish, queue-mutation, append-only orchestration log, and opt-in records writers.

## AIPOS-38 Controlled Execute MVP

AIPOS-38 adds local module-only controlled execute for adapter dry-runs:

- supported execute operations: `draft_create`, `draft_publish`, `queue_claim`
- execute requires a valid adapter-issued `dry_run_id`
- execute enforces actor match, token expiry, and immediate dry-run revalidation
- execute compares `dry_run_snapshot_hash` before write
- owner confirmation marker is protocol-only: `OWNER_CONFIRMED`
- `with_records` execute is blocked in AIPOS-38
- queue `block/complete/reopen` execute remains blocked in AIPOS-38

No server, auth, database, subprocess runtime bridge, or orchestration execute surface was added.

## AIPOS-77 Planner Loop Controlled Persistence Gate

AIPOS-77 extends the local controlled execute backend/API gate to the existing append-only planner loop writers:

- `orchestration_event_append`
- `planner_iteration_append`

These operations require adapter dry-run tokens, actor match, execute-time snapshot revalidation, explicit Owner confirmation, and writer-level `expected_hash` validation using the writer dry-run `write_snapshot_hash`.

AIPOS-77 does not add visible Web UI persistence buttons, planner runtime launch, automatic polling, automatic agent execution, automatic publish/claim, summary state writing, forum backend posting, database/deployment/auth changes, or git automation.

## AIPOS-78 Context Pack Preview

AIPOS-78 adds a read-only Context Pack builder preview:

```bash
python3 tools/aipos_cli/aipos_cli.py context-pack preview --task-id <TASK_ID> --json
python3 tools/aipos_cli/aipos_cli.py context-pack preview --path <TASK_PATH> --json
python3 tools/aipos_cli/aipos_cli.py context-pack preview --orchestration-id <ORCHESTRATION_ID> --json
```

The preview gathers task metadata, context bundle declarations, orchestration summary references, source refs, governance flags, and disabled capability flags. It returns no dry-run token and performs no writes.

AIPOS-78 does not add a context pack writer, visible Board panel, external RAG/search call, Cortex replacement behavior, agent execution, queue mutation, records write, orchestration append, summary state write, database/deployment/auth changes, or git automation.

## AIPOS-39 Execute Integration Fixtures

AIPOS-39 adds fixture-backed integration tests for adapter controlled execute:

- fixtures live under `tools/aipos_cli/tests/fixtures/`
- tests copy fixtures into temporary repos only
- tests cover dry-run -> token -> execute -> revalidation flow for:
  - `draft_create`
  - `draft_publish`
  - `queue_claim`
- tests confirm unsupported execute paths remain blocked

## AIPOS-41 Local Web UI Skeleton

A minimal local read-only board UI skeleton is provided at `web/board/`.

- local-only development/debugging use
- default bind: `127.0.0.1`
- read routes only; no mutation routes
- uses `tools.aipos_cli.board_adapter` read functions
- no execute route is exposed in the web skeleton

## AIPOS-42 Task Detail and Preview Panels

The local web skeleton now includes read-only task detail and preview panels.

- `/api/task` uses `tools.aipos_cli.board_adapter.get_task`
- `/api/preview` uses `tools.aipos_cli.board_adapter.get_preview`
- UI remains local-only and read-only
- no mutation or execute route is exposed
- implementation commit/push waits for audit/finalize approval

## AIPOS-43 Needs Owner and Validation Detail Panels

The local web skeleton now renders read-only detail panels for Needs Owner and Validation data.

- Needs Owner detail uses `/api/needs-owner`
- Validation detail uses `/api/validate`
- cross-link actions load task detail through existing `/api/task`
- no mutation or execute UI is exposed
- implementation remains uncommitted until audit/finalize approval

## AIPOS-44 Local UI Interaction Polish

The local web skeleton now includes read-only interaction polish for development review.

- per-panel refresh controls call existing GET routes only
- loading, error, and empty states are visible
- adapter debug output can be expanded or collapsed
- preview actor input is persisted in browser-local storage
- selected queue, Needs Owner, and Validation rows are visually distinguished
- no mutation or execute UI is exposed
- implementation remains uncommitted until audit/finalize approval

## AIPOS-45 Records and Agents Detail Panels

The local web skeleton now includes read-only Records Detail and Agents Detail panels.

- Records Detail uses the existing `/api/records` route backed by `get_records`
- Agents Detail uses the existing `/api/agents` route backed by `get_agents`
- session records, claim logs, agent profiles, aliases, and runtime instances are displayed as inspection data only
- runtime command and args remain declarative text and are never executed by the UI
- no new backend route, mutation route, execute route, deployment path, or runtime bridge is exposed
- implementation remains uncommitted until audit/finalize approval
