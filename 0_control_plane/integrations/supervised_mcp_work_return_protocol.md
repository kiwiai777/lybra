# Supervised MCP Work Return Path Protocol

## Status

AIPOS-168 defines the protocol-only surface for returning work through MCP after a task has already been claimed through the Supervised MCP claim path.

This document does not implement MCP tools, expose a capability scope, add controlled execute behavior, change queue mutation code, add a lease writer, add a records writer, dispatch audit, finalize work, change CLI or Board behavior, change validator behavior, change runtime behavior, change deployment, change credentials, or expose a public endpoint.

## Purpose

AIPOS-166 implemented Supervised MCP explicit claim as a claim-only path:

```text
pending -> claimed
lease_status: proposed
```

AIPOS-167 dogfood validated that path over real HTTP/SSE and exposed practical friction around capability setup, generic-client Owner confirmation, cross-process dry-run-token diagnostics, and multi-instance claimant semantics.

AIPOS-168 defines the next protocol boundary: how an MCP client may return completed executor work for an already claimed task without pretending that Lybra has an active lease writer or autonomous worker runtime.

The work-return path means:

```text
claimed task
-> executor returns result evidence
-> task records executor completion
-> task becomes audit-ready
```

It does not mean:

- active lease activation;
- audit dispatch;
- audit PASS;
- finalize;
- dependent accepted-work unblock.

## Relationship To Existing Protocols

AIPOS-168 composes:

- AIPOS-48 atomic queue mutation and actor matching;
- AIPOS-50 task-session lease and runtime binding, while keeping active lease writing deferred;
- AIPOS-96 MCP naming and controlled mutation discipline;
- AIPOS-109 and AIPOS-113 MCP dry-run / confirm write discipline;
- AIPOS-123 and AIPOS-124 loopback HTTP/SSE transport;
- AIPOS-144 and AIPOS-145 strict `specific_instance_only` and dependency-state split;
- AIPOS-146 and AIPOS-147 opaque canonical instance identity;
- AIPOS-164 MCP claim and autonomy dial protocol;
- AIPOS-165 Supervised MCP explicit claim protocol;
- AIPOS-166 Supervised MCP explicit claim implementation;
- AIPOS-167 Supervised MCP explicit claim dogfood evidence.

It does not reopen those protocols.

## Supervised-Only Scope

AIPOS-168 applies only to:

```yaml
autonomy_mode: Supervised
operation: queue_return
surface: mcp
task_precondition: claimed
lease_status: proposed
```

Rules:

- one request targets one already claimed task;
- one concrete canonical `agent_instance` returns the work;
- the task must be claimed by the same canonical instance, unless a later separately approved handoff protocol says otherwise;
- the caller must present a visible dry-run preview;
- confirm requires explicit Owner confirmation proof;
- no automatic return loop is allowed;
- no audit task is created or claimed;
- no finalize action is performed;
- no active lease is written or renewed;
- no Delegated or Standing behavior is enabled.

Requests that include Delegated, Standing, batch, auto-select, background-worker, lease activation, audit dispatch, finalize, credential, bearer-token, raw prompt, or raw response semantics must return `BLOCK` or remain unavailable until a separate Owner-gated slice exists.

## Tool Names And Visibility

Future tool names:

```text
lybra_queue_return_dry_run
lybra_queue_return_confirm
```

Future capability scope:

```text
queue_return
```

Visibility rule:

- read tools remain visible by default;
- `lybra_queue_return_dry_run` and `lybra_queue_return_confirm` are visible only when the connection capability includes `queue_return`;
- HTTP/SSE Bearer transport authentication is not enough to expose return tools;
- `queue_claim` does not imply `queue_return`;
- `queue_return` does not grant claim authority, lease authority, audit authority, finalize authority, Owner authority, or execution authority beyond the approved return mutation.

This protocol only reserves names and scope. It does not enable them.

## Transport Boundary

MCP transport remains separate from work return.

Rules:

- `initialize`, `tools/list`, `ping`, and `/sse` keepalive must not return work;
- `/sse` ping must not prove progress, completion, liveness, or lease renewal;
- each `lybra_queue_return_*` call is an explicit JSON-RPC tool call;
- stdio and HTTP/SSE should preserve the same tool semantics if implemented;
- no return may be inferred from a long-lived connection.

## Work Return Inputs

The return path should accept normalized executor evidence, not raw runtime transcripts by default.

Recommended dry-run arguments:

```yaml
task_id:
task_path:
actor:
agent_instance:
autonomy_mode: Supervised
owner_policy_ref:
claim_id:
active_session_id:
result_summary:
artifact_refs:
completion_report_ref:
executor_status: completed
audit_readiness: ready
return_reason:
```

Rules:

- exactly one of `task_id` or `task_path` is required;
- `actor` is required;
- `agent_instance` is required and must resolve to one canonical opaque instance;
- `actor` must match the canonical `agent_instance` for the first implementation slice, matching AIPOS-166's conservative rule;
- `autonomy_mode` must be `Supervised`;
- `owner_policy_ref` must identify the supervised policy or approval context;
- `executor_status` must be `completed` for a successful return;
- `audit_readiness` must be `ready` for a successful return;
- at least one of `result_summary`, `artifact_refs`, or `completion_report_ref` should be present;
- raw credentials, bearer tokens, API keys, raw prompts, raw model responses, shell histories, or full runtime transcripts must not be accepted by default;
- audit verdicts, finalize approvals, publication flags, destructive-action approvals, or lease activation requests must not be accepted in this return payload.

## Dry-Run Validation

Dry-run must validate at least:

- task exists in `5_tasks/queue/claimed`;
- task frontmatter status is `claimed`;
- the claimed task's `claimed_by`, `agent_instance`, or claim metadata resolves to the same canonical instance as the returning `agent_instance`;
- `specific_instance_only`, when present, uses AIPOS-145 exact canonical equality;
- the request's `claim_id`, when provided, matches the claimed task;
- no active conflicting return, completion, block, or finalize state is visible;
- `owner_policy_ref` is present and compatible with `Supervised`;
- `executor_status: completed` is explicit;
- `audit_readiness: ready` is explicit;
- returned evidence refs are repo-relative or approved workspace-relative and do not contain secrets;
- planned mutation is bounded to the claimed task card and approved return metadata;
- no non-adjustable Owner floor action is hidden in the return request;
- controlled execute dry-run token and snapshot semantics can cover the plan.

Dry-run must not write files, move queue state, append records, dispatch audit, or activate a lease.

## Completion Semantics

Successful work return marks executor completion and audit readiness.

The return path maps to the AIPOS-144/AIPOS-145 dependency-state split:

```yaml
executor_completion:
  dependency_executor_status: completed
audit_readiness:
  dependency_audit_readiness: ready
audit_pass:
  dependency_audit_status: pending
```

Interpretation:

- executor completion means the executor claims the work is done and has provided evidence;
- audit readiness means the work has enough evidence for an independent auditor to start;
- audit PASS remains absent until a distinct auditor returns PASS through a separate audited path;
- accepted-work follow-ons and finalize remain blocked until `audit_pass` is satisfied where required.

For the claimed task itself, a future implementation may choose one of two separately audited state models:

1. keep the task in `claimed` with explicit return metadata such as `executor_status: completed` and `audit_readiness: ready`;
2. move the task to `completed` while explicitly setting `audit_readiness: ready` and preserving that audit PASS is still pending.

AIPOS-168 does not choose or implement either model. The implementation slice must choose one and prove that it preserves AIPOS-145 dependency semantics.

## Dry-Run Response Contract

The response should preserve the Board/API controlled execute envelope shape and include:

```yaml
ok:
verdict: PASS | NEEDS_OWNER | BLOCK
operation: queue_return
surface: mcp
autonomy_mode: Supervised
task_id:
task_path:
actor:
agent_instance:
canonical_agent_instance:
owner_policy_ref:
claim_id:
claimed_by:
executor_status: completed
audit_readiness: ready
audit_status: pending
lease_preview:
return_preview:
confirmation_preview:
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
```

`owner_confirmation_required` must be `true` for the Supervised MCP return surface.

## Confirmation Preview Envelope

AIPOS-167-FRICTION-02 showed that generic MCP clients need a clean, client-presentable envelope that keeps the reviewed preview and confirm arguments together.

Every successful return dry-run should include:

```yaml
confirmation_preview:
  envelope_version: aipos-168.v1
  operation: queue_return
  surface: mcp
  autonomy_mode: Supervised
  task:
    task_id:
    task_path:
    current_status: claimed
  actor:
    actor:
    agent_instance:
    canonical_agent_instance:
  owner_policy_ref:
  claim:
    claim_id:
    claimed_by:
  return:
    executor_status: completed
    audit_readiness: ready
    audit_status_after_return: pending
    result_summary:
    artifact_refs:
    completion_report_ref:
  lease:
    lease_path: claim_only
    lease_status: proposed
    active_lease_written: false
  preview:
    planned_writes:
    planned_moves:
    planned_records:
    blocking_reasons:
    warnings:
  confirm:
    tool_name: lybra_queue_return_confirm
    required_owner_confirmation_token: OWNER_CONFIRMED
    dry_run_token:
    actor:
    agent_instance:
    canonical_agent_instance:
    owner_policy_ref:
```

Rules:

- the envelope is review material, not authority;
- `OWNER_CONFIRMED` remains explicit and must still be supplied on confirm;
- confirm must revalidate the dry-run token, actor, canonical instance, Owner policy reference, and snapshot;
- the envelope must not contain raw credentials, bearer tokens, raw prompts, raw responses, or secret runtime transcripts;
- clients may copy/paste or render the envelope, but the backend remains authoritative.

## Confirm Request Contract

Recommended confirm arguments:

```yaml
dry_run_token:
actor:
agent_instance:
owner_policy_ref:
owner_confirmation_token:
```

Rules:

- `dry_run_token` is required from a prior compatible `lybra_queue_return_dry_run`;
- `actor` must match the dry-run actor;
- `agent_instance` must resolve to the dry-run canonical instance;
- `owner_policy_ref` must match the reviewed dry-run;
- `owner_confirmation_token` must be `OWNER_CONFIRMED`;
- confirm must perform immediate revalidation before any write;
- stale, expired, actor-mismatched, instance-mismatched, policy-mismatched, snapshot-mismatched, or non-return dry-run tokens must `BLOCK` with zero writes.

MCP confirm must not self-confirm on behalf of Owner.

## Confirm Execution Semantics

On successful confirm, a future implementation may only execute the approved work-return mutation for the already claimed task.

Allowed effects:

- update executor completion metadata;
- update audit readiness metadata;
- attach normalized artifact or completion report refs;
- preserve claim identity and Owner policy attribution;
- return provenance minimums in the response.

Forbidden effects:

- active lease creation or renewal;
- records writing unless a records writer is separately approved;
- audit task creation, assignment, claim, or dispatch;
- audit PASS recording;
- finalize recommendation or finalize mutation;
- dependent accepted-work unblock;
- queue claim of any other task;
- runtime launch, scheduler, polling, heartbeat, or worker maintenance.

## Lease Boundary

AIPOS-168 is built on claim-only AIPOS-166 behavior.

For this protocol:

```yaml
lease_path: claim_only
lease_status: proposed
active_lease_written: false
```

Rules:

- work return may operate on a claimed task whose lease remains proposed;
- work return must not pretend an active lease exists;
- work return must not renew a lease;
- `/sse` ping remains transport-only and never proves lease liveness or completion;
- any future active lease writer or lease activation path requires a separate Owner gate, protocol or implementation task, and independent audit.

## Structured Error Codes

Recommended error codes:

```yaml
SCOPE_DENIED:
INVALID_TASK_SELECTOR:
TASK_NOT_CLAIMED:
CLAIMANT_MISMATCH:
AMBIGUOUS_LEGACY_INSTANCE:
INSTANCE_REQUIRED:
INSTANCE_MISMATCH:
OWNER_POLICY_REF_REQUIRED:
INVALID_AUTONOMY_MODE:
OWNER_CONFIRMATION_REQUIRED:
UNSUPPORTED_QUEUE_RETURN_FIELD:
MISSING_RETURN_EVIDENCE:
INVALID_EXECUTOR_STATUS:
INVALID_AUDIT_READINESS:
INCOMPATIBLE_DRY_RUN:
STALE_DRY_RUN:
TOKEN_EXPIRED:
SNAPSHOT_MISMATCH:
CONTROLLED_EXECUTE_REJECTED:
LEASE_WRITER_DEFERRED:
AUDIT_DISPATCH_DEFERRED:
FINALIZE_DEFERRED:
```

Errors should use the same teaching-error style as existing MCP write tools.

## Provenance Requirements

Because AIPOS-168 remains protocol-only and does not add a records writer, provenance may be returned in responses and later written only through separately approved writers.

Minimum successful-return provenance:

```yaml
event_type: mcp_queue_return
occurred_at:
actor:
actor_instance_id:
canonical_agent_instance:
surface: mcp
transport:
autonomy_mode: Supervised
owner_policy_ref:
task_id:
task_path:
claim_id:
dry_run_id:
result: PASS
executor_status: completed
audit_readiness: ready
audit_status_after_return: pending
artifact_refs:
completion_report_ref:
lease_status: proposed
related_claim_ref:
related_audit_task_ref:
related_audit_verdict_ref:
```

Provenance does not grant authority and must not contain secrets.

## Backward Compatibility

- Historical AIPOS-166 claim-only behavior remains valid.
- Historical AIPOS-167 dogfood evidence remains evidence and is not rewritten.
- Existing `lybra_queue_claim_*` tools remain unchanged.
- Existing Board and CLI claim behavior remains unchanged.
- Existing dependency semantics remain unchanged: `audit_pass` is still required for accepted-work follow-ons where the dependent task requires accepted upstream work.
- Existing task cards that lack work-return metadata must not be silently treated as audit-ready.

## Next Gates

AIPOS-168 does not approve implementation.

Future Owner-gated slices should remain separated:

1. Supervised MCP Work Return implementation:
   implement `lybra_queue_return_dry_run`, `lybra_queue_return_confirm`, `queue_return` scope, confirmation preview envelope, zero-write dry-run, Owner-confirmed confirm, and the chosen executor-completion / audit-readiness task mutation.
2. Lease writer / active lease activation:
   deferred to a separate Owner gate. This must be explicitly recorded in the implementation task, roadmap, and decision log before any active lease writer is designed or implemented.
3. Records writer for MCP return provenance:
   deferred to a separate Owner gate.
4. Audit dispatch:
   deferred to a separate Owner gate. Work return may make a task audit-ready, but must not create or claim audit work.
5. Finalize path:
   deferred to a separate Owner gate and still requires the appropriate independent audit PASS.
6. Delegated and Standing work-return behavior:
   deferred to separate Owner gates.
7. Thin MCP DX pass:
   address AIPOS-167-FRICTION-01 through FRICTION-03, including setup helper guidance, clearer transport-auth versus capability-scope messaging, confirmation envelope surfacing, and token diagnostic wording.
8. Multi-instance allowed-claimants semantics:
   address AIPOS-167-FRICTION-04 in a separate task-matching slice. Do not infer claimant eligibility from ID names.
9. State Staleness and Provenance:
   use claim and work-return evidence to define explicit staleness markers, provenance chains, and contradiction handling.
10. Trace-Native Audit:
   consume claim and return traces as first-class audit input in a later stage.

Finalize discipline:

- When AIPOS-168 is finalized, the decision log and roadmap must explicitly record that lease writer / active lease activation is deferred to a separate Owner gate.
- The same finalize records should also preserve the separation between work return, audit dispatch, records writing, and finalize.

## Non-Goals

AIPOS-168 does not introduce:

- live MCP return tools;
- `queue_return` capability handling;
- controlled execute allowlist expansion;
- queue mutation implementation;
- lease writer;
- lease activation;
- records writer;
- audit dispatch;
- audit PASS recording;
- finalize;
- dependent accepted-work unblock;
- Delegated or Standing behavior;
- external worker harness;
- autonomous Lybra server runtime;
- launcher;
- scheduler;
- polling loop;
- heartbeat;
- worker maintenance;
- credential handling;
- live BYO-LLM behavior;
- external-intake assist behavior;
- CLI or Board changes;
- validator changes;
- deployment or public endpoint changes.
