# Execute Allowed Operations

## Purpose

This document defines the first recommended execute allowlist for future controlled execute.

## Allowed Future Operations

Recommended allowlist for future AIPOS-38:

- `draft_create`
- `draft_publish`
- `queue_claim`
- `queue_block`
- `queue_complete`
- `queue_reopen`
- `orchestration_event_append`
- `planner_iteration_append`
- `owner_decision_record`

These operations are allowed only with:

- dry-run token reference
- snapshot-hash match
- execute-time revalidation
- local-only execution

## Forbidden Operations

Still forbidden:

- `queue_delete`
- `record_delete`
- `draft_overwrite`
- `force_publish`
- `force_claim`
- `force_complete`
- `orchestration_write`
- `orchestration_state_write`
- `loop_state_write`
- `subtask_index_write`
- `artifact_links_write`
- `shared_memory_write`
- `project_management_write`
- `git_commit`
- `git_push`
- `agent_execute`
- `runtime_launch`

## AIPOS-91 Reserved Session Tree Operations

AIPOS-91 defines these future controlled execute operation names:

- `session_fork`
- `session_rollback`
- `session_clone`

They are not enabled by AIPOS-91 and are not added to the current implemented allowlist.

Before any future implementation, a separate Owner-approved task must define dry-run preview shape, token and snapshot handling, source session resolution, target node collision checks, append-only `session_tree_event` behavior, rollback safety semantics, UI or CLI surface, and independent audit requirements.

In AIPOS-91, the operations must be treated as forbidden for execution.

## Operation-specific Required Checks

### `draft_create`

Required checks:

- draft target path still safe
- no draft overwrite
- no duplicate task_id
- current validation result still compatible with reviewed dry-run

Planned writes or moves:

- write one draft markdown file under `5_tasks/drafts/`
- no moves

Post-state expectation:

- draft exists only in `5_tasks/drafts/`

### `draft_publish`

Planner subtask draft publish must also satisfy AIPOS-52 planner publish preconditions when `draft_source: planner` or `created_by_planner: true`.

Required checks:

- source draft still exists
- draft validation still passes
- pending target path still safe
- no pending collision
- no duplicate task_id
- planner-created draft has no pending Owner decision
- planner-created draft has explicit reviewer and audit_by when required
- planner is not reviewer or auditor for its own planned complex-class work

Owner confirmation triggers:

- dry-run had `NEEDS_OWNER`
- publish introduces owner-required conditions

Planned writes or moves:

- write one pending markdown file under `5_tasks/queue/pending/`
- no delete of source draft

Post-state expectation:

- source draft remains unchanged
- pending copy exists

### `queue_claim`

Required checks:

- source task still resolves in `pending`
- actor still matches
- destination claimed path safe
- no collision
- record paths still available when `with_records == true`

Owner confirmation triggers:

- `with_records == true`
- actor alias ambiguity
- dry-run had `NEEDS_OWNER`

Planned writes or moves:

- one task markdown write under `5_tasks/queue/claimed/`
- one queue move from `pending` to `claimed`
- optional claim and session record writes

Post-state expectation:

- task is in claimed state
- runtime fields are consistent

### `queue_block`

Required checks:

- source task still resolves in `claimed`
- actor still matches active claimant
- block reason still present
- optional record update path safe

Owner confirmation triggers:

- `with_records == true`
- block introduces owner-review conditions
- dry-run had `NEEDS_OWNER`

Planned writes or moves:

- one task markdown write under `5_tasks/queue/blocked/`
- one queue move from `claimed` to `blocked`
- optional session record update

Post-state expectation:

- task is in blocked state
- `needs_owner` semantics remain visible

### `queue_complete`

Required checks:

- source task still resolves in `claimed`
- actor still matches active claimant
- completion report link or equivalent required field still present
- optional record update path safe

Owner confirmation triggers:

- `with_records == true`
- operation affects completed-task semantics
- dry-run had `NEEDS_OWNER`

Planned writes or moves:

- one task markdown write under `5_tasks/queue/completed/`
- one queue move from `claimed` to `completed`
- optional session record update

Post-state expectation:

- task is in completed state
- completion metadata is present

### `queue_reopen`

Required checks:

- source task still resolves in `blocked`
- actor still matches allowed reopen rules
- reopen reason still present
- destination pending path safe

Owner confirmation triggers:

- `with_records == true`
- reopen from completed is requested later
- dry-run had `NEEDS_OWNER`

Planned writes or moves:

- one task markdown write under `5_tasks/queue/pending/`
- one queue move from `blocked` to `pending`
- optional session record update

Post-state expectation:

- task is back in pending state
- reopened metadata is visible

### `orchestration_event_append`

AIPOS-65 introduces this as a narrow CLI writer operation. AIPOS-77 permits it behind the local controlled execute backend/API gate, with no visible Web UI button until a later approved task.

Required checks:

- event payload is explicit JSON
- `orchestration_id` is present and path-safe
- target resolves under `5_tasks/orchestration/{orchestration_id}/orchestration_events.md`
- event type is allowed by `orchestration_event_log_schema.md`
- severity is one of `info`, `warning`, `needs_owner`, or `blocking`
- `forum_thread_ref` is present and preserved in `refs`
- actor supplied to CLI matches payload actor
- duplicate `event_id` is blocked
- dry-run returns `write_snapshot_hash`
- non-dry-run append requires matching `--expected-hash`
- `owner_decision_recorded` includes Owner decision evidence in `refs`
- future `owner_decision_recorded` events should align with AIPOS-111 `owner_decision_record` when they claim to resolve an Owner gate

Planned writes or moves:

- append one event entry under `5_tasks/orchestration/{orchestration_id}/orchestration_events.md`
- no moves

Post-state expectation:

- event log has one additional append-only event entry
- planner iteration logs and summary state files remain unchanged

### `planner_iteration_append`

AIPOS-66 introduces this as a narrow CLI writer operation. AIPOS-77 permits it behind the local controlled execute backend/API gate, with no visible Web UI button until a later approved task.

Required checks:

- planner iteration payload is explicit JSON
- `orchestration_id` is present and path-safe
- target resolves under `5_tasks/orchestration/{orchestration_id}/planner_iterations.md`
- `forum_thread_ref` is present
- `parent_task_id` or `requirement_id` is present
- planner model tier is L3 or L4
- planner verdict is allowed by `planner_iteration_log_schema.md`
- actor supplied to CLI matches `planner_agent` or `planner_agent_instance`
- duplicate `iteration_id` is blocked
- dry-run returns `write_snapshot_hash`
- non-dry-run append requires matching `--expected-hash`
- Owner decision gates remain visible when `verdict: needs_owner`, `owner_decision_required: true`, or `needs_owner_reasons` is non-empty
- session continuity metadata is advisory only when present

Planned writes or moves:

- append one planner iteration entry under `5_tasks/orchestration/{orchestration_id}/planner_iterations.md`
- no moves

Post-state expectation:

- planner iteration log has one additional append-only iteration entry
- orchestration event logs and summary state files remain unchanged

### `owner_decision_record`

AIPOS-112 introduces this as a narrow controlled writer operation. It records a scoped Owner decision as a records artifact only.

Required checks:

- payload is explicit JSON
- `decision_id` is present and path-safe
- target resolves under `5_tasks/records/owner_decisions/<decision_id>.md`
- duplicate `decision_id` is blocked
- `owner_approval_evidence` is present and aligned with AIPOS-110
- evidence `client_tag` matches `applies_to.project` when both are present
- evidence `external_ref` matches `applies_to.external_ref` when both are present
- capability scope includes `owner_decision_record`
- capability scope includes `applies_to.project` when present
- dry-run returns token and snapshot hash
- confirm revalidates the same plan before writing

Planned writes or moves:

- write one markdown record under `5_tasks/records/owner_decisions/`
- no moves

Post-state expectation:

- Owner decision record exists as source-of-truth records artifact
- no draft publish, queue mutation, orchestration event append, SessionStore write, runtime launch, MCP side effect, or external client side effect occurs

## AIPOS-38 Execute Surface (2026-04-30)

Enabled execute operations:

- `draft_create`
- `draft_publish`
- `queue_claim`

Blocked in AIPOS-38 execute path:

- `queue_block`
- `queue_complete`
- `queue_reopen`
- any `with_records` execute
- records/orchestration/project/shared-memory writes
- server/runtime/auth/database/git operations

## AIPOS-58 Web UI Scope Decision (2026-05-03)

AIPOS-58 does not expand the Web UI controlled execute surface or the backend controlled execute allowlist.

Current local Web UI controlled execute operations remain:

- `queue_claim`
- `draft_create`
- `draft_publish`

Deferred Web UI operations:

- `queue_complete`
- `queue_block`
- `queue_reopen`

The Owner selected Planner loop UI / forum visibility as the next priority before queue `block`, `complete`, and `reopen` UI expansion.

AIPOS-58 is a scope decision only. It does not implement Web UI behavior, backend execute behavior, CLI commands, records writing, orchestration writing, queue polling, runtime launch, auth/RBAC, deployment configuration, database changes, or git automation.

## AIPOS-62 Orchestration Persistence Boundary (2026-05-04)

AIPOS-62 does not expand the controlled execute allowlist.

Still blocked:

- `orchestration_write`
- planner iteration writer
- orchestration event writer
- forum backend write/post
- planner loop runtime launch
- queue polling or autonomous execution

Future orchestration persistence must be introduced through a separate approved operation with dry-run, planned writes, snapshot/revalidation, actor matching, safe path checks, and Owner gate preservation. The first future operation should be narrower than generic `orchestration_write`, preferably append-only persistence for planner iteration and orchestration event entries under `5_tasks/orchestration/{orchestration_id}/`.

## AIPOS-65 Append-Only Planner Event Writer MVP (2026-05-04)

AIPOS-65 enables the narrow CLI operation `orchestration_event_append`.

Still blocked:

- generic `orchestration_write`
- planner iteration writer
- summary state writer
- subtask index writer
- artifact links writer
- Web UI controlled execute for orchestration events
- forum backend write/post
- planner loop runtime launch
- queue polling or autonomous execution

## AIPOS-66 Planner Iteration Append Writer MVP (2026-05-07)

AIPOS-66 enables the narrow CLI operation `planner_iteration_append`.

Still blocked:

- generic `orchestration_write`
- summary state writer
- subtask index writer
- artifact links writer
- Web UI controlled execute for planner iterations
- forum backend write/post
- session lease writer or session resume launcher
- planner loop runtime launch
- queue polling or autonomous execution

## AIPOS-67 Summary State Writer Scope Decision (2026-05-07)

AIPOS-67 does not expand the controlled execute allowlist.

Still blocked:

- generic `orchestration_write`
- `orchestration_state_write`
- `loop_state_write`
- `subtask_index_write`
- `artifact_links_write`
- Web UI controlled execute for summary state
- summary state write confirmation
- forum backend write/post
- session lease writer or session resume launcher
- planner loop runtime launch
- queue polling or autonomous execution

The next safe implementation slice should be an orchestration summary dry-run preview with `writes_enabled: false`, `execute_allowed: false`, no dry-run token, and no confirmation path.

## AIPOS-68 Orchestration Summary Dry-Run Preview (2026-05-07)

AIPOS-68 adds a read-only CLI preview, not a controlled execute operation:

```text
orchestration summary preview
```

This operation is not in the execute allowlist.

Required behavior:

- read queue task cards and queue directory state
- read records when present
- read append-only `planner_iterations.md`
- read append-only `orchestration_events.md`
- return `writes_enabled: false`
- return `execute_allowed: false`
- return no dry-run token
- surface conflicts instead of resolving them
- preserve Owner decision gates

Still blocked:

- `orchestration_state_write`
- `loop_state_write`
- `subtask_index_write`
- `artifact_links_write`
- Web UI controlled execute for summary state
- summary state write confirmation

## AIPOS-77 Planner Loop Controlled Persistence Gate (2026-05-09)

AIPOS-77 expands the backend controlled execute allowlist only for append-only planner loop persistence:

- `orchestration_event_append`
- `planner_iteration_append`

Required gate behavior:

- dry-run token required
- snapshot hash must cover append target, current target file state, append entry content, planned writes, and writer `write_snapshot_hash`
- execute actor must match dry-run actor
- current dry-run must revalidate before writing
- explicit Owner confirmation token is required for all non-blocked append execution
- execution must pass the writer `write_snapshot_hash` as the writer-level `expected_hash`
- performed writes are reported only when the append succeeds

Still blocked:

- visible Web UI persistence buttons
- planner runtime launch
- automatic planner tick
- automatic polling
- automatic agent execution
- automatic publish or claim
- generic orchestration write
- summary state write
- queue block/complete/reopen UI
- records write UI
- forum backend write/post
- database, deployment, auth/RBAC, or git automation

## AIPOS-112 Owner Decision Record Writer MVP (2026-05-21)

AIPOS-112 expands the backend controlled execute allowlist only for scoped Owner decision record persistence:

- `owner_decision_record`

Required gate behavior:

- dry-run token required
- snapshot hash must cover the normalized decision record, evidence envelope, capability scope, planned write, and target path
- execute actor must match dry-run actor
- current dry-run must revalidate before writing
- `owner_approval_evidence` is required
- capability scope must include `owner_decision_record`
- performed writes are reported only when the record file is created

Still blocked:

- MCP `owner_decision_record` tools
- visible Board decision-record write controls
- HTTP/SSE write routes
- draft publish
- queue mutation
- orchestration event append side effects
- SessionStore writes
- runtime launch
- token minting/signing/revocation
- raw platform payload storage
