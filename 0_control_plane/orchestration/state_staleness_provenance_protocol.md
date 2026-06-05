# State Staleness and Provenance Protocol

## Status

AIPOS-172 defines the protocol-only boundary for deriving state freshness, provenance chains, contradiction handling, and file-authoritative recovery from durable Lybra files.

This document does not implement a staleness engine, records writer, staleness writer, lease writer, recovery command, MCP tool, capability scope, CLI command, Board behavior, validator change, queue mutation, runtime launch, scheduler, polling, heartbeat, credential handling, deployment change, or public endpoint.

## Purpose

Lybra already treats files as the source of truth, but AIPOS-171 showed concrete cases where a reader needs more than raw task status:

- a process-local MCP dry-run token can be stale after process restart or expiration;
- a returned task can be `audit_readiness: ready` while audit dispatch remains intentionally deferred;
- a claimed task may reference `claim_id` and `active_session_id` values while claim/session record files are absent because the records writer remains deferred;
- state must be recoverable from durable files when in-memory server state, process-local tokens, or an interactive session disappear.

AIPOS-172 answers those cases with read-time state estimation:

```text
durable files -> derived freshness/provenance view -> warnings, blocks, and recovery guidance
```

The derived view is not a new authority. It is a conservative interpretation of task cards, queue directories, records, append-only logs, Owner decisions, and artifact refs.

## Relationship To Existing Protocols

AIPOS-172 composes and does not reopen:

- AIPOS-48 task matching and atomic claim;
- AIPOS-50 task-session lease and runtime binding, while keeping active lease writing deferred;
- AIPOS-67 and AIPOS-68 summary preview discipline: rebuild from files and surface conflicts instead of hiding them;
- AIPOS-91D and AIPOS-92 Pattern B source-of-truth discipline: append-only file records replay into derived state and indexes;
- AIPOS-144 and AIPOS-145 dependency-state split: `executor_completion`, `audit_readiness`, and `audit_pass`;
- AIPOS-164 action-bound lease renewal, fail-safe expiration, and transport-only `/sse` ping;
- AIPOS-166 Supervised MCP claim implementation, including claim-only `lease_status: proposed`;
- AIPOS-168 work-return provenance minimums and the rule that old cards lacking work-return metadata must not be silently treated as audit-ready;
- AIPOS-169 Supervised MCP work-return implementation;
- AIPOS-170 MCP DX diagnostics, including `STALE_DRY_RUN` and `INCOMPATIBLE_DRY_RUN`;
- AIPOS-171 dogfood evidence.

## Source-Of-Truth Model

Lybra follows Pattern B:

```text
append-only file records + task cards + queue directories
-> replay / rebuild
-> derived state, freshness markers, and indexes
```

Authoritative sources, in conservative order:

1. queue directory placement and task-card frontmatter;
2. explicit Owner Decision Records;
3. claim logs and session records when present;
4. append-only orchestration events and planner iterations;
5. completion reports, audit reports, and artifact refs;
6. generated summary or index files only as stale cache or repair input.

If a derived cache, summary, index, in-memory token registry, or client-side state disagrees with durable task and record files, the durable files win and the disagreement must be surfaced.

## Staleness Semantics

Staleness means a state claim may have been true earlier but is no longer sufficient authority now.

Recommended derived staleness shape:

```yaml
staleness:
  verdict: current | stale | unknown | contradictory
  severity: info | warn | block | needs_owner
  subject_type:
  subject_ref:
  observed_at:
  evaluated_at:
  reason_code:
  reason:
  source_refs:
  suggested_next_action:
```

Rules:

- staleness is preferably computed at read time;
- a staleness marker does not grant authority;
- missing timestamps, missing records, expired fields, and process-local token loss must not be treated as current by default;
- unknown freshness is not proof of freshness;
- `stale` or `contradictory` must fail safe when the operation would mutate state, bypass audit, finalize, execute, or widen authority.

### Dry-Run Token Staleness

MCP dry-run tokens are process-local in AIPOS-166 through AIPOS-170.

Derived interpretation:

```yaml
subject_type: dry_run_token
reason_code: PROCESS_LOCAL_TOKEN_STALE
verdict: stale
severity: block
```

Triggers:

- token is absent from the current server process;
- token expired according to `expires_at`;
- token belongs to another operation surface, such as claim token used for return confirm;
- snapshot hash cannot be revalidated.

Required behavior for future readers:

- report `STALE_DRY_RUN` for unknown or expired tokens without becoming a token oracle;
- report `INCOMPATIBLE_DRY_RUN` for recognized wrong-surface tokens where the current process can safely know that fact;
- require a fresh dry-run, Owner review, and explicit confirm;
- do not persist raw dry-run tokens as provenance by default.

Durable token provenance may record non-secret metadata only:

```yaml
dry_run_id:
operation:
surface:
actor:
canonical_agent_instance:
owner_policy_ref:
created_at:
expires_at:
snapshot_hash:
result: expired | confirmed | abandoned | superseded | unknown
```

Writing that metadata requires a separate records-writer or token-provenance writer gate.

### Claim And Lease Staleness

A claimed task can be current as a queue fact while still lacking active execution authority.

For AIPOS-166 through AIPOS-171:

```yaml
status: claimed
lease_path: claim_only
lease_status: proposed
active_lease_written: false
```

Derived interpretation:

- the task is claimed by the recorded canonical instance if queue state and claim metadata agree;
- execution lease authority is not active;
- `lease_status: proposed` is not stale by itself, but it is insufficient execution authority;
- absent claim/session records are provenance gaps, not proof that the claim is invalid;
- if an operation requires active lease authority, proposed or missing lease evidence must BLOCK.

Future active lease staleness may be derived from AIPOS-50 fields:

```yaml
lease_status:
lease_started_at:
lease_expires_at:
heartbeat_at:
renewal_count:
renewal_policy:
```

But AIPOS-172 does not define or enable active lease writing. Lease writer / active lease activation remains a separate Owner gate.

### Audit-Readiness Staleness

Audit readiness is not audit acceptance.

Required interpretation:

- `executor_status: completed` plus `audit_readiness: ready` may support creating or claiming an audit task;
- it does not satisfy `audit_pass`;
- `dependency_audit_status: pending` remains pending after MCP work return;
- accepted-work follow-ons and finalize remain blocked until explicit independent audit PASS where required;
- a task lacking explicit work-return metadata must not be silently treated as audit-ready.

If a task claims audit readiness but has no result evidence, no completion report ref, no artifact refs, or contradictory executor metadata, the derived view must return WARN or BLOCK depending on the attempted operation.

## Provenance Chain

The provenance chain answers:

```text
who did what, when, under which concrete instance and Owner policy, using which input refs, with which result, and which later audit/finalize evidence depends on it
```

Minimum chain nodes:

```yaml
task:
  task_id:
  task_path:
  queue_state:
claim:
  claim_id:
  claimed_by:
  claimed_agent_instance:
  claimed_at:
  owner_policy_ref:
  claim_record_ref:
session:
  active_session_id:
  session_record_ref:
  lease_status:
  lease_path:
return:
  return_event_ref:
  executor_completed_by:
  executor_completed_at:
  executor_status:
  audit_readiness:
  artifact_refs:
  completion_report_ref:
  return_owner_policy_ref:
audit:
  related_audit_task_ref:
  related_audit_verdict_ref:
  dependency_audit_status:
finalize:
  finalize_ref:
  finalize_status:
```

Recommended edge names:

```text
task_declares_claim
claim_binds_session
claim_precedes_return
return_sets_executor_completion
return_sets_audit_readiness
audit_reviews_return
audit_pass_unblocks_finalize
finalize_accepts_audit_pass
```

Fields such as `task_id`, `claim_id`, `active_session_id`, `dry_run_id`, `owner_policy_ref`, `related_audit_task_ref`, `related_audit_verdict_ref`, `artifact_refs`, and `completion_report_ref` are link keys. They do not grant authority by themselves.

## Provenance Completeness Levels

Readers should classify provenance completeness rather than collapse it into pass/fail.

```yaml
provenance_completeness: complete | partial | missing | contradictory
```

Recommended meaning:

- `complete`: all expected records and refs exist and agree with the task card;
- `partial`: core queue/task state is readable but some expected records are missing, as in AIPOS-171 claim/session record warnings;
- `missing`: required evidence for the requested operation is absent;
- `contradictory`: two or more durable files assert incompatible state.

For read-only inspection, `partial` may be WARN. For mutation, acceptance, finalize, audit bypass, active execution, or dependency unblock, `partial` must either BLOCK or route to Owner depending on the operation.

## Contradiction And Missing-Record Handling

Contradictions must be surfaced, not silently resolved.

Examples:

| Condition | Conservative derived result |
| --- | --- |
| task file is in `claimed/` but frontmatter says `pending` | `contradictory`, `block` for mutation |
| task says `claimed_by: agent-01` but claim record says `agent-02` | `contradictory`, `needs_owner` |
| task has `claim_id` but claim record is absent | `partial`, `warn` for read/return, `block` for active-lease or audit-acceptance operations |
| task has `active_session_id` but session record is absent | `partial`, `warn` for read/return, `block` for resume/active execution |
| task has `audit_readiness: ready` but no executor completion evidence | `partial` or `contradictory`; do not dispatch or accept without review |
| task has `dependency_audit_status: PASS` but no audit verdict ref where required | `partial`, `needs_owner` or `block` for finalize |
| dry-run token is missing after server restart | `stale`, `block`, require fresh dry-run |
| `/sse` connection is alive but lease expired or proposed | transport-only; no liveness or lease authority |

Missing records are not automatically corruption. They are explicit provenance gaps. The severity depends on the operation being attempted.

## File-Authoritative Recovery

When process memory, server state, client session, or in-memory tokens are lost, readers should rebuild current state from durable files.

Recovery algorithm:

1. scan queue directories and task cards;
2. parse task frontmatter and normalize `task_id`, queue path, `status`, `claimed_by`, `agent_instance`, `claim_id`, `active_session_id`, `executor_status`, `audit_readiness`, and `dependency_*` fields;
3. read claim records and session records when present;
4. read append-only orchestration events and planner iterations when relevant;
5. verify referenced completion reports, artifact refs, Owner decision refs, and audit refs;
6. derive staleness markers, provenance completeness, and contradictions;
7. produce a recovery view with warnings, blocking reasons, source refs, and suggested next actions;
8. do not mutate queue state, write repair records, renew leases, dispatch audit, or finalize from recovery alone.

Recommended recovery view:

```yaml
task_id:
task_path:
queue_state:
frontmatter_status:
status_consistent:
claimed_by:
canonical_agent_instance:
claim_id:
active_session_id:
lease_status:
lease_path:
executor_status:
audit_readiness:
dependency_audit_status:
provenance_chain:
provenance_completeness:
staleness:
contradictions:
warnings:
blocking_reasons:
source_refs:
recommended_next_action:
derived_at:
writes_enabled: false
```

## Read-Time Derived State Rules

AIPOS-172 prefers read-time derivation over new persistent authority.

Allowed future first-slice behavior:

- read task cards, records, and append-only logs;
- compute staleness markers;
- compute provenance chain summaries;
- classify missing records and contradictions;
- return recovery guidance;
- show source refs and suggested next action.

Forbidden without a separate gate:

- writing staleness markers into task cards;
- appending provenance records;
- repairing claim/session records;
- activating or renewing leases;
- moving queue tasks during recovery;
- dispatching audit tasks;
- recording audit PASS;
- finalizing;
- unblocking accepted-work dependencies;
- adding MCP mutation tools or capability scopes.

## Thin First Implementation Slice

Recommended next implementation after protocol approval:

```text
read-only state recovery preview
```

Scope:

- consume AIPOS-171-shaped claimed task cards and MCP return metadata;
- derive dry-run token staleness categories from current in-process token state when available and durable refs when present;
- classify missing `claim_id` / `active_session_id` records as explicit provenance gaps;
- reconstruct claim -> return -> audit-readiness chain from durable files;
- preserve AIPOS-145 `audit_pass` blocking semantics;
- return a read-only preview object with source refs, warnings, blocking reasons, and recommended next action.

Non-scope for the first slice:

- records writer;
- staleness writer;
- active lease writer;
- recovery mutation;
- audit dispatch;
- trace search UI;
- Board or MCP mutation surface expansion.

## Next Gates

Future work must remain separated:

1. Read-only State Staleness and Provenance implementation:
   build the thin recovery/provenance preview described above.
2. Lease writer / active lease activation:
   deferred to a separate Owner gate. This protocol may reference lease staleness but does not design, implement, activate, or renew leases.
3. Records writer for MCP claim / return provenance:
   deferred to a separate Owner gate.
4. Staleness marker writer:
   deferred. Read-time derived markers should come first.
5. Audit dispatch:
   deferred. Audit readiness may support audit creation, but does not create audit tasks.
6. Audit PASS and finalize paths:
   deferred and still require independent audit PASS and Owner-governed finalize discipline.
7. Accepted-work dependency unblock:
   deferred and must depend on explicit `audit_pass` where required.
8. Delegated and Standing automation:
   deferred and must preserve the non-adjustable Owner escalation floor.
9. Trace-Native Audit:
   deferred. AIPOS-172 defines provenance input shape only; it does not build a trace search, attribution, or regression audit layer.
10. Diagnostic / retrieval UI:
   deferred. AIPOS-172 defines the underlying state-estimation semantics, not a full investigation interface.

## Non-Goals

AIPOS-172 does not introduce:

- implementation code;
- records writer;
- staleness writer;
- lease writer;
- active lease activation;
- heartbeat behavior;
- scheduler;
- polling loop;
- runtime launcher;
- background recovery worker;
- MCP tool or capability-scope changes;
- CLI or Board behavior changes;
- queue mutation;
- validator changes;
- audit dispatch;
- audit PASS recording;
- finalize;
- accepted-work unblock;
- Delegated or Standing behavior;
- live BYO-LLM behavior;
- external-intake assist behavior;
- credential changes;
- deployment or public endpoint changes.
