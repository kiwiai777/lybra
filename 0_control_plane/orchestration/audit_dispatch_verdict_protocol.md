# Audit Dispatch And Verdict Protocol

## Status

AIPOS-177 defines the protocol-only boundary for dispatching audit-ready work to an independent auditor and recording the resulting audit verdict as durable evidence.

This document does not implement MCP tools, expose capability scopes, add controlled execute behavior, change validators, change queue mutation code, write audit-dispatch records, write audit-verdict records, create audit tasks, claim audit tasks, record audit PASS, finalize work, unblock accepted-work dependencies, activate leases, add a records backfill path, change CLI or Board behavior, add runtime launch, add scheduling, add polling, add heartbeat behavior, change credentials, change deployment, or expose a public endpoint.

## Purpose

AIPOS-176 proved this durable chain over real HTTP/SSE MCP transport:

```text
claim -> session -> return -> audit readiness
```

The remaining expected state is:

```text
audit_readiness: ready
dependency_audit_status: pending
```

AIPOS-177 defines the next protocol boundary:

```text
audit-ready executor work
-> Owner-confirmed audit dispatch
-> distinct auditor claims audit task through the existing claim path
-> auditor returns a verdict
-> verdict evidence links into the provenance chain
-> PASS may set audit_pass state
```

It keeps the following separate:

- audit dispatch is not autonomous;
- audit verdict is not finalize;
- audit PASS does not unblock accepted work unless a later gate implements that unblock;
- claim-only lease posture remains non-active until a separate lease writer exists.

## Relationship To Existing Protocols

AIPOS-177 composes and does not reopen:

- AIPOS-48 atomic task claim and matching;
- AIPOS-50 task-session lease and runtime binding, while keeping active lease writing deferred;
- AIPOS-96 MCP naming and controlled mutation discipline;
- AIPOS-109 and AIPOS-113 MCP dry-run / confirm write discipline;
- AIPOS-144 and AIPOS-145 strict instance identity and dependency-state split;
- AIPOS-146 and AIPOS-147 opaque instance IDs, open-vocabulary provenance, and explicit `distinct_*` independence evaluation;
- AIPOS-164 autonomy dial, two audit classes, and gate-not-engine boundary;
- AIPOS-165 and AIPOS-166 Supervised MCP claim protocol and implementation;
- AIPOS-168 and AIPOS-169 Supervised MCP work-return protocol and implementation;
- AIPOS-172 and AIPOS-173 read-time staleness and provenance preview;
- AIPOS-174 and AIPOS-175 claim/session/return records writer discipline;
- AIPOS-176 records-writer dogfood evidence.

## Supervised-Only Scope

AIPOS-177 applies only to:

```yaml
autonomy_mode: Supervised
surface: mcp
operations:
  - audit_dispatch
  - audit_verdict
```

Rules:

- every durable mutation requires a dry-run preview and explicit Owner confirmation;
- no audit task is created without Owner-confirmed dispatch;
- no audit task is claimed by Lybra automatically;
- no audit runtime is launched;
- no background loop, timer, scheduler, heartbeat, or worker maintenance is introduced;
- no Delegated or Standing behavior is enabled;
- no finalize or accepted-work unblock is performed.

Requests that include `Delegated`, `Standing`, batch automation, auto-select, background-worker, lease activation, finalize, accepted-work unblock, credential, bearer-token, raw prompt, raw response, or raw transcript semantics must `BLOCK` or remain unavailable until separately approved.

## Audit Dispatch Surface

### Tool Names And Scope

Future MCP tool names:

```text
lybra_audit_dispatch_dry_run
lybra_audit_dispatch_confirm
```

Future capability scope:

```text
audit_dispatch
```

Visibility rule:

- read tools remain visible by default;
- audit-dispatch tools are visible only when the connection capability includes `audit_dispatch`;
- HTTP/SSE Bearer transport authentication is not enough to expose audit-dispatch tools;
- `queue_claim` and `queue_return` do not imply `audit_dispatch`;
- `audit_dispatch` does not grant verdict authority, claim authority, lease authority, finalize authority, accepted-work unblock authority, Owner authority, or runtime authority.

AIPOS-177 reserves these names and scope only. It does not enable them.

### Dispatch Meaning

Audit dispatch means creating one audit task that reviews one audit-ready executor task.

It is an Owner-confirmed Supervised write, not autonomous scheduling.

Recommended precondition:

```yaml
source_task:
  queue_state: claimed
  executor_status: completed
  audit_readiness: ready
  dependency_executor_status: completed
  dependency_audit_readiness: ready
  dependency_audit_status: pending
  return_record_ref:
```

The dispatch task must preserve:

```yaml
reviewed_task_id:
reviewed_task_path:
reviewed_return_record_ref:
reviewed_executor_instance:
reviewed_executor_claim_id:
reviewed_executor_session_id:
reviewed_executor_owner_policy_ref:
audit_subject_condition: audit_readiness
required_verdict_condition: audit_pass
```

The created audit task should be a normal queue task that later uses the existing claim path. It must not be pre-claimed by dispatch unless a later separately approved slice says so.

### Dispatch Dry-Run Request

Recommended `lybra_audit_dispatch_dry_run` arguments:

```yaml
source_task_id:
source_task_path:
actor:
agent_instance:
autonomy_mode: Supervised
owner_policy_ref:
audit_task_id:
audit_task_title:
audit_task_class: complex
audit_task_mode:
audit_by:
audit_agent_instance:
independence_requirements:
  distinct_instance: true
  distinct_runtime_profile:
  distinct_harness:
  distinct_model_family:
  distinct_vendor:
  distinct_host:
dispatch_reason:
```

Rules:

- exactly one of `source_task_id` or `source_task_path` is required;
- `actor` is required;
- `agent_instance` is required and must resolve to one canonical opaque instance;
- `autonomy_mode` must be `Supervised`;
- `owner_policy_ref` is required;
- source task must be audit-ready and not already audit-dispatched through an active non-superseded dispatch record;
- source task must have durable claim/session/return provenance, or dispatch must `BLOCK` / `NEEDS_OWNER` according to the implementation boundary;
- `reviewed_executor_instance` must be derived from durable source task and record evidence, not supplied as untrusted override authority;
- the audit task must encode that the eventual auditor must be distinct from `reviewed_executor_instance`;
- the requested audit target must not hide finalize, accepted-work unblock, publication, destructive action, credential, deployment, or non-adjustable Owner-floor authority.

Dry-run must not create a task, write records, claim anything, move queue files, mark audit PASS, finalize, or unblock accepted work.

### Dispatch Dry-Run Response

The response should preserve the controlled execute envelope shape:

```yaml
ok:
verdict: PASS | NEEDS_OWNER | BLOCK
operation: audit_dispatch
surface: mcp
autonomy_mode: Supervised
source_task:
  task_id:
  task_path:
  executor_status:
  audit_readiness:
  dependency_audit_status:
  return_record_ref:
reviewed_executor:
  canonical_agent_instance:
  claim_id:
  session_id:
audit_task_preview:
  task_id:
  task_path:
  task_class:
  task_mode:
  claim_policy:
  audit_by:
  audit_agent_instance:
  independence_requirements:
planned_writes:
planned_moves:
planned_records:
blocking_reasons:
warnings:
needs_owner_reasons:
owner_confirmation_required: true
owner_confirmation_reasons:
dry_run_id:
dry_run_token:
dry_run_snapshot_hash:
expires_at:
confirmation_preview:
```

The `confirmation_preview` should be client-presentable and include copyable confirm arguments. It is review material, not authority.

### Dispatch Confirm

Recommended `lybra_audit_dispatch_confirm` arguments:

```yaml
dry_run_token:
actor:
agent_instance:
owner_policy_ref:
owner_confirmation_token:
```

Rules:

- token must come from a compatible `lybra_audit_dispatch_dry_run`;
- `actor`, canonical `agent_instance`, and `owner_policy_ref` must match the dry-run;
- `owner_confirmation_token` must be `OWNER_CONFIRMED`;
- confirm must revalidate source task status, return provenance, audit-readiness state, absence of existing active dispatch, and planned target paths;
- stale, expired, wrong-surface, actor-mismatched, instance-mismatched, policy-mismatched, or snapshot-mismatched confirm must `BLOCK` with zero writes.

Allowed effects for the future implementation:

- create one audit task under the pending queue or approved draft/publish path;
- write one audit-dispatch record when a records writer for this surface is approved;
- link `related_audit_task_ref` / `audit_dispatch_record_ref` from the source task or return record where the implementation slice explicitly allows it;
- preserve source task `dependency_audit_status: pending`.

Forbidden effects:

- claim the audit task automatically;
- launch an auditor;
- activate or renew a lease;
- record a verdict;
- record audit PASS;
- finalize;
- unblock accepted-work dependents;
- dispatch multiple audit tasks unless each has a distinct Owner-confirmed dry-run.

## Auditor Claim And Independence

The audit task is claimed through the existing AIPOS-166 queue claim path or its future compatible claim path.

The audit task must carry enough metadata to enforce independence at claim time:

```yaml
audit_subject:
  reviewed_task_id:
  reviewed_return_record_ref:
  reviewed_executor_instance:
  reviewed_executor_claim_id:
  reviewed_executor_session_id:
independence_requirements:
  distinct_instance: true
  distinct_runtime_profile:
  distinct_harness:
  distinct_model_family:
  distinct_vendor:
  distinct_host:
```

Minimum rule:

```text
auditor_canonical_agent_instance != reviewed_executor_instance
```

If stronger dimensions are set, the claim path must evaluate them using explicit profile fields and provenance metadata under AIPOS-147:

- `distinct_runtime_profile`;
- `distinct_harness`;
- `distinct_model_family`;
- `distinct_vendor`;
- `distinct_host`.

Missing or `unknown` values must not be treated as passing a required stronger dimension. The safe result is `BLOCK` or `NEEDS_OWNER` according to the future implementation boundary.

The system must not infer independence from instance-ID substrings, prefixes, apparent roles, display names, vendor-looking tokens, host-looking suffixes, or logical-agent aliases.

An audit task claimed by the same canonical instance as the executor must be rejected.

## Verdict Surface

### Tool Names And Scope

Future MCP tool names:

```text
lybra_audit_verdict_dry_run
lybra_audit_verdict_confirm
```

Future capability scope:

```text
audit_verdict
```

Visibility rule:

- verdict tools are visible only when the connection capability includes `audit_verdict`;
- `audit_dispatch` does not imply `audit_verdict`;
- `queue_return` does not imply `audit_verdict`;
- verdict authority does not grant finalize, accepted-work unblock, lease activation, runtime launch, or Owner authority.

AIPOS-177 reserves these names and scope only. It does not enable them.

### Why Verdict Is A Dedicated Tool

Verdict must use a dedicated surface rather than reusing `queue_return`.

Reasons:

- `queue_return` is executor completion plus audit readiness from the executor side;
- audit verdict is independent correctness assessment from a distinct auditor;
- verdict changes `dependency_audit_status`, while return must keep it pending;
- verdict requires distinctness checks against the reviewed executor;
- verdict evidence has different required fields, references, and error codes;
- keeping verdict separate avoids accidental self-audit or treating an executor return as audit PASS.

### Verdict Values

Recommended normalized verdicts:

```yaml
verdict: PASS | FAIL | REQUEST_CHANGES | BLOCKED | WAIVED
```

Meanings:

- `PASS`: independent audit passed under the required policy.
- `REQUEST_CHANGES`: auditor found changes needed; may support a repair task but does not satisfy audit PASS.
- `FAIL`: auditor found the work unacceptable or invalid.
- `BLOCKED`: auditor could not complete audit due to missing evidence, contradiction, access boundary, or scope issue.
- `WAIVED`: only valid with explicit Owner waiver evidence. It is not equivalent to PASS unless a separate Owner decision allows it for the specific dependency.

If the future implementation prefers `CHANGES` as a short display value, it must normalize to `REQUEST_CHANGES` in durable records.

### Verdict Dry-Run Request

Recommended `lybra_audit_verdict_dry_run` arguments:

```yaml
audit_task_id:
audit_task_path:
reviewed_task_id:
actor:
agent_instance:
autonomy_mode: Supervised
owner_policy_ref:
audit_claim_id:
audit_session_id:
audit_dispatch_record_ref:
reviewed_return_record_ref:
verdict:
findings_summary:
evidence_refs:
recommended_next_action:
owner_waiver_ref:
```

Rules:

- exactly one of `audit_task_id` or `audit_task_path` is required;
- `actor` is required;
- `agent_instance` is required and must resolve to one canonical opaque instance;
- `autonomy_mode` must be `Supervised`;
- `owner_policy_ref` is required;
- the audit task must be claimed by the same canonical auditor instance;
- the auditor must satisfy the audit task's `independence_requirements` against `reviewed_executor_instance`;
- `reviewed_task_id`, return evidence, dispatch evidence, and audit task metadata must agree;
- verdict must be one of the normalized values;
- `PASS` must require enough evidence refs or findings summary for a future reader to understand the basis;
- `WAIVED` must require explicit Owner waiver evidence;
- verdict payload must not contain raw credentials, raw prompts, raw model responses, bearer tokens, private keys, or full runtime transcripts by default.

Dry-run must not write records, change source task audit status, move tasks, finalize, or unblock accepted work.

### Verdict Confirm

Recommended `lybra_audit_verdict_confirm` arguments:

```yaml
dry_run_token:
actor:
agent_instance:
owner_policy_ref:
owner_confirmation_token:
```

Rules:

- token must come from a compatible `lybra_audit_verdict_dry_run`;
- `actor`, canonical `agent_instance`, and `owner_policy_ref` must match the dry-run;
- `owner_confirmation_token` must be `OWNER_CONFIRMED`;
- confirm must revalidate audit task claim, distinctness, reviewed task state, dispatch link, return link, and snapshot;
- stale, expired, wrong-surface, actor-mismatched, instance-mismatched, policy-mismatched, snapshot-mismatched, or distinctness-failed confirm must `BLOCK` with zero writes.

Allowed effects for the future implementation:

- write one audit-verdict record;
- append one audit-verdict event to the auditor session record if a compatible session record exists;
- link `related_audit_verdict_ref` from the reviewed source task, audit task, dispatch record, or return record where the implementation slice explicitly allows it;
- if verdict is `PASS`, set the reviewed task's audit status to the AIPOS-144/145 `audit_pass` state;
- if verdict is not `PASS`, preserve that accepted-work follow-ons and finalize remain blocked.

Forbidden effects:

- finalize;
- accepted-work unblock;
- publish external content;
- waive non-adjustable Owner floor decisions;
- create repair tasks automatically;
- dispatch another audit automatically;
- activate or renew a lease;
- launch a runtime or auditor;
- rewrite historical records.

## Audit Pass Semantics

AIPOS-177 preserves the AIPOS-144/AIPOS-145 split:

```yaml
executor_completion:
  dependency_executor_status: completed
audit_readiness:
  dependency_audit_readiness: ready
audit_pass:
  dependency_audit_status: PASS
```

Only a valid independent verdict of `PASS` may satisfy `audit_pass`.

However:

- `audit_pass` does not finalize work;
- `audit_pass` does not automatically move accepted-work dependents;
- `audit_pass` does not publish external content;
- `audit_pass` does not approve destructive or security-sensitive actions;
- `audit_pass` does not bypass the non-adjustable Owner escalation floor.

Finalize and accepted-work unblock are separate gates and must consume audit PASS evidence through their own protocols or implementation slices.

## Records And Provenance

Audit dispatch and verdict records extend the AIPOS-174 chain:

```text
claim -> session -> return -> audit-dispatch -> audit-claim/session -> audit-verdict -> future finalize
```

### Audit Dispatch Record

Recommended path:

```text
5_tasks/records/audit_dispatches/<reviewed_task_id>/<dispatch_id>.md
```

Minimum frontmatter:

```yaml
record_type: audit_dispatch_record
event_type: mcp_audit_dispatch
dispatch_id:
reviewed_task_id:
reviewed_task_path:
reviewed_return_record_ref:
reviewed_executor_instance:
reviewed_executor_claim_id:
reviewed_executor_session_id:
audit_task_id:
audit_task_path:
surface: mcp
operation: audit_dispatch
autonomy_mode: Supervised
actor:
canonical_agent_instance:
owner_policy_ref:
dispatched_at:
independence_requirements:
dry_run_id:
dry_run_snapshot_hash:
confirmation_ref:
dependency_executor_status: completed
dependency_audit_readiness: ready
dependency_audit_status: pending
lease_status: proposed
lease_path: claim_only
active_lease_written: false
```

### Audit Verdict Record

Recommended path:

```text
5_tasks/records/audit_verdicts/<reviewed_task_id>/<verdict_id>.md
```

Minimum frontmatter:

```yaml
record_type: audit_verdict_record
event_type: mcp_audit_verdict
verdict_id:
verdict:
reviewed_task_id:
reviewed_task_path:
reviewed_return_record_ref:
audit_dispatch_record_ref:
audit_task_id:
audit_task_path:
audit_claim_id:
audit_session_id:
reviewed_executor_instance:
auditor_instance:
independence_result:
surface: mcp
operation: audit_verdict
autonomy_mode: Supervised
actor:
canonical_agent_instance:
owner_policy_ref:
verdict_at:
findings_summary_ref:
evidence_refs:
recommended_next_action:
dry_run_id:
dry_run_snapshot_hash:
confirmation_ref:
dependency_audit_status_after:
finalize_performed: false
accepted_work_unblocked: false
lease_status: proposed
lease_path: claim_only
active_lease_written: false
```

Records are append-only evidence. They do not override task cards or queue directories. Missing records are provenance gaps. Contradictory records must be surfaced as `BLOCK` or `NEEDS_OWNER`; writers must not silently repair them.

Records must not store raw Bearer tokens, raw capability tokens, API keys, credentials, raw prompts, raw model responses, full runtime transcripts by default, or private workspace data copied into the public repo.

## Structured Error Codes

Recommended audit-dispatch errors:

```yaml
SCOPE_DENIED:
INVALID_TASK_SELECTOR:
SOURCE_TASK_NOT_AUDIT_READY:
MISSING_RETURN_RECORD:
MISSING_CLAIM_RECORD:
MISSING_SESSION_RECORD:
AUDIT_ALREADY_DISPATCHED:
OWNER_POLICY_REF_REQUIRED:
INVALID_AUTONOMY_MODE:
INSTANCE_REQUIRED:
AMBIGUOUS_LEGACY_INSTANCE:
INSTANCE_MISMATCH:
UNSUPPORTED_AUDIT_DISPATCH_FIELD:
OWNER_CONFIRMATION_REQUIRED:
INCOMPATIBLE_DRY_RUN:
STALE_DRY_RUN:
TOKEN_EXPIRED:
SNAPSHOT_MISMATCH:
CONTROLLED_EXECUTE_REJECTED:
LEASE_WRITER_DEFERRED:
FINALIZE_DEFERRED:
```

Recommended audit-verdict errors:

```yaml
SCOPE_DENIED:
INVALID_TASK_SELECTOR:
AUDIT_TASK_NOT_CLAIMED:
REVIEWED_TASK_MISMATCH:
MISSING_AUDIT_DISPATCH_RECORD:
MISSING_RETURN_RECORD:
MISSING_AUDIT_SESSION_RECORD:
INVALID_VERDICT:
WAIVER_REQUIRES_OWNER_EVIDENCE:
INDEPENDENCE_FAILED:
INDEPENDENCE_UNKNOWN:
OWNER_POLICY_REF_REQUIRED:
INVALID_AUTONOMY_MODE:
INSTANCE_REQUIRED:
AMBIGUOUS_LEGACY_INSTANCE:
INSTANCE_MISMATCH:
UNSUPPORTED_AUDIT_VERDICT_FIELD:
OWNER_CONFIRMATION_REQUIRED:
INCOMPATIBLE_DRY_RUN:
STALE_DRY_RUN:
TOKEN_EXPIRED:
SNAPSHOT_MISMATCH:
CONTROLLED_EXECUTE_REJECTED:
FINALIZE_DEFERRED:
ACCEPTED_WORK_UNLOCK_DEFERRED:
```

All failure responses must keep performed writes, performed moves, and performed records empty.

## Thin First Implementation Slice

Recommended first implementation after AIPOS-177 approval:

```text
Supervised MCP Audit Dispatch + Verdict MVP
```

Scope:

- add `audit_dispatch` capability scope and the `lybra_audit_dispatch_dry_run` / `lybra_audit_dispatch_confirm` tool pair;
- create exactly one pending audit task from one audit-ready source task after Owner confirmation;
- encode `reviewed_executor_instance` and minimum `independence_requirements: { distinct_instance: true }` on the audit task;
- rely on the existing queue claim path for the auditor to claim the audit task;
- enforce same-instance auditor rejection when claiming or when recording verdict;
- add `audit_verdict` capability scope and the `lybra_audit_verdict_dry_run` / `lybra_audit_verdict_confirm` tool pair;
- write one audit-dispatch record and one audit-verdict record, if the slice includes the corresponding records writer;
- for verdict `PASS`, set only the reviewed task's audit status to audit PASS;
- preserve finalize and accepted-work unblock as deferred gates;
- update AIPOS-173 state recovery preview to recognize dispatch and verdict refs only if included in the approved implementation scope.

First-slice non-scope:

- active lease writer or lease renewal;
- automatic audit dispatch;
- Delegated audit dispatch;
- Standing mode;
- runtime launcher;
- scheduler;
- polling;
- heartbeat;
- finalize;
- accepted-work dependency unblock;
- historical backfill / repair;
- Trace-Native Audit;
- Board UI or diagnostic UI.

## Next Gates

Future work must remain separated:

1. Supervised MCP Audit Dispatch implementation:
   expose dispatch dry-run / confirm only after Owner gate and independent audit.
2. Supervised MCP Audit Verdict implementation:
   expose verdict dry-run / confirm only after Owner gate and independent audit.
3. Audit dispatch / verdict records writer:
   may be included in the implementation MVP or split, but must remain explicit and audited.
4. AIPOS-173 recovery preview extension:
   read dispatch / verdict records and report audit provenance completeness.
5. Lease writer / active lease activation:
   separate Owner gate. Audit tasks remain claim-only until then.
6. Finalize writer:
   separate Owner gate after audit PASS.
7. Accepted-work unblock:
   separate Owner gate after audit PASS and finalize semantics are clear.
8. Delegated audit dispatch:
   separate autonomy gate; budgeted automatic audit assignment is not Supervised.
9. Standing mode:
   separate future stage.
10. Historical records backfill / repair:
   separate additive-only migration gate.
11. Trace-Native Audit:
   separate protocol using claim/session/return/dispatch/verdict records as trace inputs.
12. Board / diagnostic UI:
   separate surface work after CLI / MCP semantics are proven.

## Non-Goals

AIPOS-177 does not introduce:

- implementation code;
- MCP tool exposure;
- capability-scope changes;
- controlled execute allowlist changes;
- queue mutation changes;
- validator changes;
- CLI behavior changes;
- Board behavior changes;
- audit task creation;
- audit task claim changes;
- audit-dispatch record writing;
- audit-verdict record writing;
- audit PASS recording;
- finalize;
- accepted-work unblock;
- lease writer;
- active lease activation;
- lease renewal;
- staleness writer;
- historical backfill or repair;
- automatic audit dispatch;
- Delegated mode;
- Standing mode;
- runtime launcher;
- scheduler;
- polling;
- heartbeat;
- external worker harness;
- credential changes;
- deployment changes;
- live BYO-LLM behavior;
- external-intake assist behavior;
- public endpoint;
- historical rewrite.

