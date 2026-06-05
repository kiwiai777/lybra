# Records Writer Audit Chain Protocol

## Status

AIPOS-174 defines the protocol-only boundary for filling the durable audit chain gaps exposed by AIPOS-171 and surfaced by AIPOS-173.

This document does not implement a records writer change, enable records on MCP claim or return, add a return record writer, add a lease writer, activate leases, add staleness writers, dispatch audits, record audit PASS, finalize work, unblock accepted-work dependencies, add MCP tools or capability scopes, change CLI or Board behavior, change validators, change queue mutation semantics, launch runtimes, schedule workers, poll queues, add heartbeats, change credentials, change deployment, or expose a public endpoint.

## Purpose

AIPOS-171 proved that the Supervised MCP claim plus work-return path works over real HTTP/SSE. It also exposed a state-estimation gap:

- claimed task cards can contain `claim_id` and `active_session_id` while the corresponding claim/session record files are absent;
- work return can mark executor completion and audit readiness without a durable return event;
- AIPOS-173 correctly reports those gaps as partial provenance, but it can only preview what durable files contain.

AIPOS-174 defines how future writer slices should append the missing durable records so Lybra can reconstruct:

```text
claim -> session -> return -> audit readiness -> future audit verdict -> future finalize
```

The goal is to complete the provenance trail, not to grant execution authority.

## Relationship To Existing Protocols

AIPOS-174 composes and does not reopen:

- AIPOS-48 task claim protocol and atomic `pending -> claimed` transition;
- AIPOS-50 task-session schema, while keeping active lease writing deferred;
- AIPOS-96 MCP naming and controlled mutation discipline;
- AIPOS-109 and AIPOS-113 MCP-native dry-run / confirm write discipline;
- AIPOS-144 and AIPOS-145 dependency-state split: executor completion, audit readiness, and audit PASS remain distinct;
- AIPOS-146 and AIPOS-147 opaque instance identity and provenance metadata;
- AIPOS-164 autonomy dial, transport/claim separation, and action-bound lease renewal;
- AIPOS-165 and AIPOS-166 Supervised MCP claim protocol / implementation;
- AIPOS-168 and AIPOS-169 Supervised MCP work-return protocol / implementation;
- AIPOS-170 MCP DX diagnostics;
- AIPOS-171 dogfood evidence;
- AIPOS-172 read-time staleness and provenance semantics;
- AIPOS-173 read-only recovery / provenance preview.

The existing product already has a local `with_records` queue-mutation writer for claim/session records. AIPOS-174 treats that as prior art, not as sufficient current MCP behavior. Future implementation must align any reused writer with the current MCP claim-only and work-return semantics.

## Source-Of-Truth Model

Records are appendable durable evidence. They are not a separate authority that can override task cards or queue directories.

Authoritative interpretation remains:

```text
task cards + queue directories + append-only records
-> derived state / provenance preview
```

Rules:

- task card and queue placement still define current queue state;
- record files support provenance, replay, auditability, and contradiction detection;
- missing records are provenance gaps;
- conflicting records are surfaced as contradictions or Owner-review cases;
- a record must never silently repair, move, finalize, dispatch audit, activate a lease, or grant new authority.

## Record Classes

### Claim Record

Purpose: durable evidence that one canonical instance won a specific claim.

Recommended path:

```text
5_tasks/records/claims/<task_id>/<claim_id>.md
```

Minimum frontmatter:

```yaml
record_type: claim_record
event_type: mcp_queue_claim
claim_id:
task_id:
task_path:
surface: mcp
operation: queue_claim
autonomy_mode: Supervised
actor:
canonical_agent_instance:
owner_policy_ref:
claimed_at:
from_state: pending
to_state: claimed
claim_policy:
claim_match_basis:
claim_requirements_hash:
dry_run_id:
dry_run_snapshot_hash:
confirmation_ref:
session_id:
lease_status: proposed
lease_path: claim_only
active_lease_written: false
```

The record may include non-secret capability / provenance metadata such as profile refs, model family, harness, host, or capability profile refs. It must not include Bearer tokens, capability-token raw text, API keys, raw prompts, raw model responses, shell transcripts, or credentials.

### Session Record

Purpose: durable execution-context provenance for the claimed task.

Recommended path:

```text
5_tasks/records/sessions/<task_id>/<session_id>.md
```

Minimum frontmatter for MCP claim-only first slices:

```yaml
record_type: session_record
session_id:
task_id:
task_path:
surface: mcp
autonomy_mode: Supervised
actor:
canonical_agent_instance:
owner_policy_ref:
claim_id:
created_at:
updated_at:
session_status: claimed
current_state: claimed
lease_status: proposed
lease_path: claim_only
active_lease_written: false
event_count:
```

Important correction for current MCP semantics:

- `session_status: active` must not be used unless an active lease writer is separately approved and actually writes active lease evidence.
- A claim-only session record may say `claimed`, `returned`, `blocked`, `completed`, or `abandoned` as provenance status, but not active execution authority.
- `lease_status: proposed` is an explicit non-active posture.

The session record body should contain an append-only event list. Each event line should include timestamp, event type, actor, canonical instance, Owner policy ref, and linked record id.

### Return Record

Purpose: durable evidence that the executor returned normalized work evidence and marked the task audit-ready.

Recommended path:

```text
5_tasks/records/returns/<task_id>/<return_id>.md
```

Minimum frontmatter:

```yaml
record_type: return_record
event_type: mcp_queue_return
return_id:
task_id:
task_path:
surface: mcp
operation: queue_return
autonomy_mode: Supervised
actor:
canonical_agent_instance:
owner_policy_ref:
claim_id:
session_id:
returned_at:
executor_status: completed
audit_readiness: ready
dependency_executor_status: completed
dependency_audit_readiness: ready
dependency_audit_status: pending
result_summary_ref:
artifact_refs:
completion_report_ref:
dry_run_id:
dry_run_snapshot_hash:
confirmation_ref:
lease_status: proposed
lease_path: claim_only
active_lease_written: false
```

The task card should link the return record through `return_event_ref` or `return_record_ref` in the same confirm operation that writes executor completion / audit readiness metadata.

The return record does not record audit PASS, does not dispatch audit, and does not finalize.

### Future Records

The following are separate gates:

- audit dispatch records;
- audit verdict records;
- finalize records;
- accepted-work dependency unblock records;
- active lease records;
- staleness marker records;
- trace-native audit indexes.

AIPOS-174 defines link points for these records but does not implement or authorize them.

## Writer Discipline

Future records writer implementation must follow the same controlled discipline as existing write surfaces:

```text
draft -> validate -> preview -> Owner confirmation -> write -> revalidate
```

Required behavior:

- dry-run writes nothing;
- dry-run returns planned record writes and rendered markdown previews;
- confirm requires the correct dry-run token and explicit `OWNER_CONFIRMED` where the surface is MCP Supervised;
- confirm revalidates the task snapshot before writing;
- record targets must be repo-relative and constrained to `5_tasks/records/<record_class>/<task_id>/`;
- existing target files block; writers must not overwrite historical records;
- task-card metadata and record files must be written as one bounded operation where possible;
- after confirm, reader validation must see the new record refs and classify provenance as complete unless another genuine gap remains;
- failures must be fail-safe with zero partial queue authority.

If filesystem atomicity is limited, the implementation must choose a conservative order and surface partial-write recovery guidance. It must not claim provenance completeness if either the task card link or record file write failed.

## Claim Writer Semantics

When a future MCP claim implementation enables records:

- successful `lybra_queue_claim_confirm` may write one claim record and one session record;
- the claimed task card must reference the created `claim_id` and `active_session_id`;
- record fields must use the resolved canonical opaque `agent_instance`;
- legacy instance ids may appear only as compatibility metadata, not matching authority;
- the record must include `owner_policy_ref`, `autonomy_mode: Supervised`, and confirmation metadata;
- the record must preserve `lease_status: proposed` and `active_lease_written: false`;
- the record must not launch work, activate lease, dispatch audit, or set audit readiness.

For existing local queue mutation writers, future implementation may either adapt the current `with_records` path or add a narrower MCP-specific writer. In either case, it must not keep legacy `session_status: active` semantics for claim-only MCP records.

## Return Writer Semantics

When a future MCP return implementation enables records:

- successful `lybra_queue_return_confirm` may write one return record and append one session-record event;
- the claimed task card must reference the return record through `return_event_ref` or `return_record_ref`;
- executor completion and audit readiness metadata must remain on the task card, as AIPOS-169 currently does;
- `dependency_audit_status` remains `pending`;
- the session record may transition from `claimed` to `returned`;
- the return record must include normalized result evidence, not raw transcripts by default;
- the writer must not dispatch audit, record audit PASS, finalize, or unblock accepted-work dependencies.

If the session record is absent, return-record writing may either:

1. `BLOCK`, requiring claim/session provenance to be created through a separate approved repair path; or
2. route to Owner as `NEEDS_OWNER` if an explicitly approved additive backfill protocol exists.

The first implementation slice should prefer `BLOCK` for absent session records to avoid inventing history.

## Provenance Links

Required link keys:

```yaml
task_id:
task_path:
claim_id:
session_id:
return_id:
return_record_ref:
related_audit_task_ref:
related_audit_verdict_ref:
finalize_ref:
owner_policy_ref:
canonical_agent_instance:
dry_run_id:
dry_run_snapshot_hash:
confirmation_ref:
```

Recommended chain edges:

```text
task_declares_claim
claim_binds_session
session_records_claim
claim_precedes_return
return_updates_session
return_sets_executor_completion
return_sets_audit_readiness
future_audit_reviews_return
future_audit_pass_unblocks_finalize
```

AIPOS-173 recovery preview should be able to consume these links and move from `partial` to `complete` provenance for claim/session/return portions of the chain.

## Contradiction Handling

Writers must not silently repair contradictions.

Examples:

| Condition | Required writer behavior |
| --- | --- |
| task has `claim_id` but an existing claim record with that id names another task | BLOCK or NEEDS_OWNER |
| task says `claimed_by: agent-01` but claim record says `agent-02` | BLOCK or NEEDS_OWNER |
| session record exists but has another `claim_id` | BLOCK |
| return confirm references a different `active_session_id` than the task card | BLOCK |
| return record target already exists | BLOCK |
| task has `audit_readiness: ready` but no return evidence and writer is asked to record audit PASS | BLOCK; audit PASS is out of scope |
| existing session record says `session_status: active` without active lease evidence | WARN or NEEDS_OWNER for read; future writer must not amplify it |

Backfill of historical records is a separate Owner-gated migration / repair task. AIPOS-174 does not authorize automatic backfill.

## Privacy And Secret Boundary

Records may store:

- non-secret ids and refs;
- canonical instance id and optional provenance metadata;
- Owner policy refs;
- dry-run id and snapshot hash;
- artifact refs and completion report refs;
- normalized result summaries.

Records must not store:

- raw Bearer tokens;
- raw capability tokens;
- API keys;
- credentials;
- raw prompts;
- raw model responses;
- full shell transcripts by default;
- private workspace data copied into the public product repo;
- absolute paths outside approved workspace / repo boundaries unless an explicit privacy gate allows them.

## Thin First Implementation Slice

Recommended next implementation after AIPOS-174 approval:

```text
Supervised MCP claim/return records writer MVP
```

Scope:

- enable record writing for successful MCP Supervised claim confirm;
- write claim record and claim-only session record using canonical instance identity;
- enable return record writing for successful MCP Supervised return confirm;
- append a return event to the existing session record;
- link task card fields to claim/session/return records;
- update AIPOS-173 state recovery preview to recognize `return_record_ref` / `return_event_ref`;
- keep dry-run zero-write and confirm Owner-confirmed with snapshot revalidation;
- add regression tests proving records are written and AIPOS-173 provenance moves from partial to complete.

First-slice non-scope:

- active lease writer or lease renewal;
- records backfill for historical tasks;
- audit dispatch;
- audit verdict writer;
- finalize writer;
- accepted-work unblock;
- Board UI;
- new MCP tools or new capability scopes;
- Delegated or Standing behavior.

## Next Gates

Future work must remain separated:

1. AIPOS-174 implementation slice:
   Supervised MCP claim/return records writer MVP, bounded to provenance records and task-card refs.
2. Historical records backfill / repair protocol:
   optional and additive only; no in-place history rewrite.
3. Lease writer / active lease activation:
   separate Owner gate. Records may say `lease_status: proposed`; they must not activate leases.
4. Audit dispatch:
   separate gate. Audit readiness may support audit creation; records writer does not create audit tasks.
5. Audit PASS / verdict writer:
   separate gate with distinct-instance correctness audit rules.
6. Finalize and accepted-work unblock:
   separate gates that depend on audit PASS where required.
7. Trace-Native Audit:
   separate protocol using these records as trace inputs.
8. Board / diagnostic UI:
   separate surface work after CLI / MCP semantics are proven.

## Non-Goals

AIPOS-174 does not introduce:

- implementation code;
- records writer enablement;
- historical backfill;
- staleness writer;
- lease writer;
- active lease activation;
- heartbeat behavior;
- scheduler;
- polling loop;
- runtime launcher;
- background worker;
- MCP tool or capability-scope changes;
- CLI or Board behavior changes;
- validator changes;
- queue mutation changes;
- audit dispatch;
- audit PASS recording;
- finalize;
- accepted-work unblock;
- Delegated or Standing behavior;
- live BYO-LLM behavior;
- external-intake assist behavior;
- credential changes;
- deployment or public endpoint changes.
