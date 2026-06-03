# Supervised MCP Explicit Claim Protocol

## Status

AIPOS-165 defines the protocol-only surface for a supervised MCP explicit task-claim tool pair.

This document does not implement MCP tools, change tool visibility, add a capability scope, expand controlled execute, change queue mutation, add a lease writer, add a records writer, change CLI or Board behavior, add runtime launch, add scheduler behavior, add polling, add heartbeat behavior, change deployment, change credentials, or expose a public endpoint.

## Purpose

AIPOS-164 establishes that MCP transport connection is not task claim and that the first deferred implementation candidate is a narrow Supervised explicit-claim path.

AIPOS-165 defines that first path as a future MCP controlled mutation pair:

```text
lybra_queue_claim_dry_run
lybra_queue_claim_confirm
```

The pair is for Supervised mode only. It lets an MCP-aware client request one explicit task claim after human-visible preview and Owner confirmation. It must preserve the existing AIPOS-48 atomic claim, AIPOS-50 session lease boundary, AIPOS-145 strict instance enforcement, AIPOS-147 opaque identity model, AIPOS-164 transport/claim separation, and controlled execute discipline.

## Relationship To Existing Protocols

AIPOS-165 composes:

- AIPOS-48 task matching and atomic claim;
- AIPOS-50 task-session lease and runtime binding;
- AIPOS-96 MCP server boundary and controlled mutation naming;
- AIPOS-109 and AIPOS-113 MCP-native write discipline;
- AIPOS-123 and AIPOS-124 HTTP/SSE transport;
- AIPOS-145 strict `specific_instance_only` and dependency-state behavior;
- AIPOS-147 opaque canonical instance identity and `distinct_*` independence evaluation;
- AIPOS-164 MCP claim and autonomy dial protocol.

It does not reopen those protocols. It defines the exact Supervised MCP claim contract that a later implementation may build.

## Supervised-Only Scope

This protocol applies only to:

```yaml
autonomy_mode: Supervised
operation: queue_claim
surface: mcp
```

Rules:

- one explicit claim request targets one task;
- one concrete `agent_instance` is the claimant;
- the caller must present a visible dry-run preview;
- confirm requires explicit Owner confirmation proof;
- no automatic claim loop is allowed;
- no automatic task selection is allowed;
- no Delegated or Standing behavior is enabled;
- no audit task dispatch is enabled by this claim pair;
- no execution beyond claim is enabled by this claim pair.

If a request includes `Delegated`, `Standing`, policy-budget, background-worker, batch, or auto-select semantics, the MCP claim tool must return `BLOCK` or unavailable until a separate Owner-gated implementation exists.

## Tool Names And Visibility

Future tool names:

```text
lybra_queue_claim_dry_run
lybra_queue_claim_confirm
```

Future capability scope:

```text
queue_claim
```

Visibility rule:

- read tools remain visible by default;
- `lybra_queue_claim_dry_run` and `lybra_queue_claim_confirm` are visible only when the connection capability includes `queue_claim`;
- missing, expired, malformed, or ambiguous capability returns the same structured teaching-error style as existing MCP write tools;
- HTTP/SSE Bearer transport authentication is not enough to expose claim tools;
- capability visibility does not grant claim authority, execution authority, lease authority, or Owner approval.

This protocol does not add the scope or tools to the implementation.

## Transport Boundary

MCP transport remains separate from task claim.

Rules:

- `initialize`, `tools/list`, `ping`, and `/sse` keepalive must not claim work;
- `/sse` ping must not renew leases or prove worker liveness;
- each `lybra_queue_claim_*` call is an explicit JSON-RPC tool call;
- stdio and HTTP/SSE must preserve the same tool semantics when the future implementation supports both;
- no claim may be inferred from a long-lived connection.

## Dry-Run Request Contract

Recommended `lybra_queue_claim_dry_run` arguments:

```yaml
task_id:
task_path:
actor:
agent_instance:
autonomy_mode: Supervised
owner_policy_ref:
runtime_profile:
active_session_id:
context_bundle_ack:
with_records: true
claim_reason:
```

Rules:

- exactly one of `task_id` or `task_path` is required;
- `actor` is required and must identify the caller-visible actor;
- `agent_instance` is required and must resolve to one canonical opaque concrete instance;
- `autonomy_mode` must be `Supervised`;
- `owner_policy_ref` must identify the Owner policy or approval context authorizing this supervised session;
- `with_records` should default to `true` for MCP claim because provenance is required for supervised remote claim evidence;
- raw credentials, bearer tokens, API keys, raw prompts, or raw model responses must not be accepted as arguments;
- batch task selectors, auto-pick filters, queue scans with mutation intent, or background worker policy fields must block.

If `agent_instance` cannot be resolved explicitly, dry-run must `BLOCK`.

## Dry-Run Validation

Dry-run must validate at least:

- task exists in `5_tasks/queue/pending`;
- task is not expired;
- task is claimable under AIPOS-48 hard matching;
- `specific_instance_only`, when present, resolves through explicit canonical equality before any alias-aware compatibility;
- ambiguous legacy instance resolution blocks;
- required model tier, capability, context bundle, write scope, runtime profile, and availability checks pass or return clear blocking reasons;
- no active conflicting claim or lease is visible;
- planned queue move is `pending -> claimed`;
- planned writes and moves are repo-relative and safe;
- Owner policy reference is present and compatible with `Supervised`;
- no non-adjustable floor action is hidden in the claim request;
- controlled execute dry-run token and snapshot semantics can cover the plan.

Dry-run must not move files or write records.

## Dry-Run Response Contract

The response should preserve the Board/API controlled execute envelope shape and include:

```yaml
ok:
verdict: PASS | NEEDS_OWNER | BLOCK
operation: queue_claim
surface: mcp
autonomy_mode: Supervised
task_id:
task_path:
actor:
agent_instance:
canonical_agent_instance:
owner_policy_ref:
claim_policy:
claim_match_basis:
lease_preview:
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

`owner_confirmation_required` must be `true` for this MCP claim surface. This is intentionally stricter than some existing local Board or CLI claim paths because MCP is an external client surface and Supervised mode requires explicit human confirmation before mutation.

## Confirm Request Contract

Recommended `lybra_queue_claim_confirm` arguments:

```yaml
dry_run_token:
actor:
agent_instance:
owner_confirmation_token:
owner_policy_ref:
```

Rules:

- `dry_run_token` is required from a prior compatible dry-run;
- `actor` must match the dry-run actor;
- `agent_instance` must match the dry-run canonical concrete instance;
- `owner_policy_ref` must match the reviewed dry-run policy reference;
- `owner_confirmation_token` is required and must represent explicit Owner confirmation of this exact preview;
- confirm must perform immediate revalidation before any write;
- stale, expired, actor-mismatched, instance-mismatched, policy-mismatched, or snapshot-mismatched requests must `BLOCK` with zero writes.

MCP confirm must not self-confirm on behalf of Owner.

## Confirm Execution Semantics

On successful confirm, the future implementation may execute only the approved controlled queue claim path:

```text
5_tasks/queue/pending/{task}.md
-> 5_tasks/queue/claimed/{task}.md
```

Post-state expectations:

- task is in claimed state;
- claim metadata names the canonical concrete `agent_instance`;
- Owner policy reference is visible;
- active session or lease fields are either written by an approved lease writer or represented as a clearly bounded lease proposal;
- claim and provenance records are appended when records writer behavior is approved for this surface;
- response reports performed writes and moves.

If a lease writer is not yet implemented in the later implementation slice, the implementation must not pretend an active lease exists. It may return a lease proposal and require a separate approved lease activation path, or the implementation slice must include a separately audited lease writer.

## Lease Boundary

AIPOS-165 does not implement a lease writer.

The future claim confirm must choose one of two audited implementation paths:

1. claim-only path:
   - execute atomic claim;
   - return `lease_status: proposed`;
   - require a separate explicit lease activation before execution starts.
2. claim-plus-lease path:
   - execute atomic claim;
   - write one active lease through an approved scoped writer;
   - record lease expiration and Owner policy metadata;
   - keep renewal action-bound only.

Both paths must preserve AIPOS-164:

- no heartbeat daemon;
- no transport-ping lease renewal;
- no automatic reassignment on expiration;
- explicit reclaim or recovery only.

## Provenance Requirements

Because this is a supervised MCP mutation surface, every successful confirm should record or return evidence sufficient for later append-only records.

Minimum provenance fields:

```yaml
event_type: mcp_queue_claim
occurred_at:
actor:
actor_instance_id:
surface: mcp
transport:
owner_policy_ref:
autonomy_mode: Supervised
task_id:
claim_id:
dry_run_id:
dry_run_snapshot_hash:
result:
planned_moves:
performed_moves:
lease_ref:
warnings:
blocking_reasons:
```

If records writing is not part of the future implementation slice, the response must still return this evidence and clearly state that durable record writing remains deferred.

## Error Mapping

MCP errors should preserve teaching responses.

Recommended error codes:

```text
SCOPE_DENIED
DRY_RUN_REQUIRED
OWNER_CONFIRMATION_REQUIRED
INVALID_AUTONOMY_MODE
TASK_NOT_PENDING
TASK_EXPIRED
INSTANCE_REQUIRED
INSTANCE_MISMATCH
AMBIGUOUS_LEGACY_INSTANCE
MATCHING_FAILED
LEASE_CONFLICT
STALE_DRY_RUN
SNAPSHOT_MISMATCH
CONTROLLED_EXECUTE_REJECTED
CLAIM_LOST
```

All failure responses must keep performed writes and performed moves empty.

## Relationship To Delegated And Standing

AIPOS-165 does not implement Delegated or Standing.

Supervised claim may be a prerequisite for later Delegated work because it defines:

- explicit claimant identity;
- MCP dry-run and confirm shape;
- Owner policy reference handling;
- strict no-transport-claim behavior;
- claim and lease evidence fields.

Delegated batch selection, automatic claim loops, automatic audit dispatch, budget filters, after-the-fact review, external harness policy records, and Standing policy renewal remain future Owner-gated slices.

## Backward Compatibility

- Existing MCP read tools remain unchanged.
- Existing MCP `intake_submit` and `owner_decision_record` write-tool pairs remain unchanged.
- Existing Board and CLI `queue_claim` behavior remains unchanged.
- Existing controlled execute allowlist remains unchanged.
- Existing task cards, claim records, session records, and historical evidence are not rewritten.
- Existing HTTP/SSE transport remains loopback, Bearer-authenticated, and stateless per SSE connection.

## Deferred Implementation Requirements

A future implementation task must separately define and test:

- adding `queue_claim` to MCP capability-token scope handling;
- exposing `lybra_queue_claim_dry_run` and `lybra_queue_claim_confirm`;
- mapping both tools to the existing controlled execute `queue_claim` backend;
- exact Owner confirmation token handling for MCP claim;
- exact canonical instance argument and resolver behavior;
- lease proposal or lease writer scope;
- claim/provenance record writer scope;
- stdio and HTTP/SSE parity;
- structured teaching errors;
- regression coverage for wrong instance, stale token, missing Owner confirmation, claim collision, expired task, and zero-write failures.

## Non-Goals

AIPOS-165 does not introduce:

- MCP claim tool implementation;
- MCP capability-token scope implementation;
- controlled execute allowlist expansion;
- queue mutation behavior;
- lease writer;
- records writer;
- CLI behavior;
- Board behavior;
- validator behavior;
- runtime launch;
- scheduler;
- polling loop;
- heartbeat daemon;
- transport-ping liveness;
- automatic task selection;
- Delegated mode;
- Standing mode;
- external worker harness;
- audit dispatch;
- task execution;
- finalize behavior;
- deployment behavior;
- credential behavior;
- live BYO-LLM behavior;
- external-intake assist;
- public endpoint;
- historical rewrite.

