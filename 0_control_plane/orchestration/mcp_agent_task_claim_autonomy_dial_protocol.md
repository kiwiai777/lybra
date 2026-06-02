# MCP Agent Task Claim and Autonomy Dial Protocol

## Status

AIPOS-164 defines the protocol boundary for explicit MCP-served task claim and one canonical user-facing autonomy dial.

This document is protocol-only. It does not implement an MCP claim tool, capability scope, controlled execute operation, validator, queue mutation, lease writer, records writer, CLI command, Board control, external worker harness, runtime launch, scheduler, timer, background polling, heartbeat daemon, deployment change, credential change, or public endpoint.

## Purpose

Lybra already defines declarative matching, atomic task claim, task-session lease binding, strict opaque instance identity, complex dependency states, and explicit audit independence. It also implements an HTTP/SSE MCP transport.

AIPOS-164 joins those foundations into one bounded interaction model:

1. an MCP-aware agent may explicitly request one task claim without treating a transport connection as execution authority;
2. Lybra remains a pull-served gate, not an autonomous execution engine;
3. Owner policy may later permit bounded external worker automation through one user-facing autonomy dial;
4. every automatic action remains attributable, auditable, fail-safe, and file-authoritative.

## Existing Foundation

AIPOS-164 preserves and composes:

- AIPOS-47 model-tier and agent-instance routing;
- AIPOS-48 declarative dispatch matching and atomic `pending -> claimed` transition;
- AIPOS-50 task-session lease and runtime binding;
- AIPOS-94 Planner Autonomy Tier historical policy;
- AIPOS-123 and AIPOS-124 loopback HTTP/SSE transport;
- AIPOS-144 and AIPOS-145 strict `specific_instance_only` enforcement and `executor_completion | audit_readiness | audit_pass` dependency semantics;
- AIPOS-146 and AIPOS-147 opaque canonical instance identity, open-vocabulary provenance, and explicit `distinct_*` independence evaluation.

This protocol does not reopen those implementations. It refines their composition before any new implementation slice.

## Lybra Is A Gate, Not An Engine

Lybra remains file-authoritative and pull-served.

Lybra may validate, preview, authorize, record, and serve explicit requests. It must not:

- launch an agent runtime;
- maintain an agent process;
- schedule work;
- poll the queue on behalf of an agent;
- push a task into an agent session;
- run a hidden server-side claim loop;
- renew a lease from a background timer;
- use transport keepalive as execution authority;
- silently move work between instances.

Any future background or parallel work is driven only by an Owner-explicitly-started external worker harness. That harness is outside the Lybra server process and remains subject to Lybra gates on each action.

## Transport Connection Is Not A Task Claim

### Connection

An MCP client may connect once and keep its HTTP/SSE transport open.

Current HTTP/SSE transport behavior remains:

```yaml
http_sse:
  default_host: 127.0.0.1
  default_port: 7118
  authentication: bearer_token
  token_env: LYBRA_MCP_TOKEN
  rpc_endpoint: POST /mcp
  sse_endpoint: GET /sse
  sse_lifecycle: stateless_per_connection
```

Opening or retaining a connection means only that the client can issue MCP requests. It does not:

- discover or reserve a task automatically;
- claim a task;
- create a lease;
- renew a lease;
- prove agent liveness;
- grant execution authority;
- grant Owner approval;
- imply an autonomy policy.

### SSE Ping

`/sse` ping events are transport keepalive only.

They must never be interpreted as:

- agent heartbeat;
- runtime heartbeat;
- lease renewal;
- task activity;
- task progress;
- worker health;
- authority evidence.

### Explicit Claim Action

Task claim is a separate explicit action, conceptually:

```text
claim_task(task_ref, agent_instance, owner_policy_ref, ...)
```

The exact future MCP tool name, schema, capability scope, controlled execute mapping, and writer integration remain deferred to an implementation protocol or implementation slice.

The action may be invoked by:

- an Owner or user;
- the agent's current interactive harness;
- an Owner-explicitly-started external worker harness acting within an active Owner policy.

The Lybra service must not invoke it for the client.

## Pull-Served Claim Model

### Baseline Rule

Claim is pull-based and explicit.

The baseline layer has no timer trigger. A future MCP-aware client may list or inspect available tasks, select a task, and explicitly request claim. Lybra validates that request against existing matching and claim rules.

### One Task, One Concrete Instance, One Active Lease

A successful claim creates or prepares one task-session lease for one concrete canonical `agent_instance`.

The authoritative composition is:

```text
explicit claim request
-> AIPOS-48 hard matching
-> AIPOS-145 strict canonical instance check where required
-> atomic pending -> claimed transition
-> AIPOS-50 session lease binding
-> append-only claim and policy-attribution evidence
```

One claimed task may have at most one active execution lease.

Each lease binds at minimum:

```yaml
task_id:
claim_id:
active_session_id:
agent_instance:
runtime_profile:
execution_host:
canonical_repo_path:
lease_status:
lease_started_at:
lease_expires_at:
owner_policy_ref:
```

### Action-Bound Renewal

AIPOS-164 refines the AIPOS-50 lease-renewal direction:

- lease renewal may occur only as an explicit action-bound request;
- renewal must revalidate the same concrete instance, active claim, session binding, runtime binding, Owner policy, and absence of blocking conditions;
- renewal must append an evidence event;
- no background heartbeat daemon or timer-driven renewal is allowed;
- `/sse` ping must not renew a lease.

Historical `heartbeat_at` fields remain readable as historical or compatibility metadata. New implementation must not require a background heartbeat signal.

### Lease Expiration

When a lease expires:

- execution authority stops;
- the expiration must append an event;
- the task becomes eligible for an explicit reclaim or recovery action;
- Lybra must not automatically reassign the task;
- Lybra must not silently launch another worker;
- Lybra must not silently resume the expired worker;
- Lybra must not infer recovery from `/sse` ping.

Returning to claimable eligibility does not authorize hidden queue mutation. A future implementation slice must define the explicit, audited reclaim or recovery transition and preserve historical claim and lease evidence.

## Session Isolation

Each agent works in its own explicit session and context.

Rules:

- a claimed task binds to the claiming concrete instance's task session;
- an agent must not silently take over an unrelated Owner interaction session;
- when work runs in the current interactive session by explicit request, foreground progress remains visible;
- background or parallel work requires an explicitly started separate worker instance and its own session;
- handoff requires explicit metadata and must preserve AIPOS-50 recovery and handoff discipline;
- no autonomy policy grants permission to merge unrelated session contexts.

## Submission Discipline

Claim authority is not finalize authority.

Every durable execution submission must preserve:

```text
dry-run -> confirm -> independent correctness audit -> finalize
```

Rules:

- dry-run shows the exact planned mutation and relevant evidence;
- confirm revalidates actor, concrete instance, Owner policy, input snapshot, lease, scope, and blocking conditions;
- complex or consequential work proceeds through independent correctness audit by a distinct instance;
- finalize is allowed only after the required audit PASS and only within the active Owner policy;
- non-adjustable floor actions still escalate to Owner regardless of audit PASS or dial mode.

## Canonical Owner Autonomy Dial

### Additive Supersession Of AIPOS-94 User-Facing Tiers

AIPOS-164 introduces one canonical user-facing autonomy dial:

```text
Supervised
Delegated
Standing
```

This additively supersedes AIPOS-94 `A0..A4` as the user-facing policy vocabulary. Historical AIPOS-94 records remain valid and readable. Existing planner-specific tier metadata may remain in historical evidence or compatibility adapters.

The unified dial may be applied separately to planner and worker planes:

```yaml
autonomy_policy:
  planner_mode: Supervised | Delegated | Standing
  worker_mode: Supervised | Delegated | Standing
```

The two planes share vocabulary and invariants but may receive different Owner-approved policy windows.

### Historical Compatibility Mapping

The mapping is conservative and descriptive:

| Historical AIPOS-94 tier | Canonical interpretation |
| --- | --- |
| `A0` | `Supervised` |
| `A1` | `Delegated` only within one approved subtask boundary |
| `A2` | `Delegated` only within one approved bounded DAG |
| `A3` | `Delegated` only within one approved orchestration boundary |
| `A4` | no enabled equivalent; remains reserved and forbidden |

`Standing` is new protocol vocabulary. It is not an alias for historical `A4`.

Missing, ambiguous, expired, or invalid policy falls back to `Supervised`.

## Non-Adjustable Owner Escalation Floor

The following actions always require Owner escalation under every dial mode:

- external publication or public content;
- transfer of funds or assets;
- procurement or purchase decisions;
- data deletion or irreversible destruction;
- access-control, permission, or sharing-setting changes;
- architecture, scope, security, authorization, audit-boundary, deployment-boundary, or long-term-direction decisions.

This floor is not a dial position and must not be made configurable by a future implementation.

Metered usage of Owner-configured external APIs or LLMs is not part of this floor and is not a Lybra autonomy-budget dimension. Cost policy belongs to the user's external cost-control system. Lybra provenance should still record available non-secret cost estimates.

## Dial Modes

### Supervised

`Supervised` is the default and fail-safe mode.

Rules:

- Owner explicitly authorizes one worker session;
- the worker handles one claimed task at a time;
- routine read and draft preparation may proceed without extra friction;
- every durable mutation requires visible preview and explicit Owner confirmation;
- finalize requires visible Owner confirmation after required independent audit PASS;
- no automatic claim, automatic execution continuation, or automatic finalize occurs.

### Delegated

`Delegated` allows bounded automation only after an explicit Owner policy is active and an external worker harness has been explicitly started by Owner.

The policy must bound the automation window with non-cost filters such as:

```yaml
delegated_budget:
  max_tasks:
  expires_at:
  task_classes:
  projects:
  capabilities:
  write_scopes:
  allowed_reversible_actions:
```

Within that window, the external harness may explicitly request:

- claim of one eligible task at a time per concrete worker instance;
- execution of routine in-scope work;
- append-only provenance recording;
- preparation and dispatch of an independent audit task to a distinct eligible instance;
- reversible finalize after required independent audit PASS when the Owner policy explicitly delegates that finalize class.

Delegated must stop and escalate when:

- any non-adjustable floor action appears;
- budget is exhausted or expired;
- task scope, policy scope, capability scope, or write scope is ambiguous;
- matching, strict identity, lease, dependency, provenance, or audit state is missing or contradictory;
- any validator, writer, or policy returns `BLOCK`;
- independent audit returns `REQUEST_CHANGES`, cannot run, or is ambiguous;
- a required distinct auditor instance is unavailable;
- recovery, resume, or handoff is ambiguous;
- a new architecture, security, credential, deployment, or runtime boundary appears.

Delegated does not make Lybra autonomous. Every action remains an explicit pull request from an external harness and passes the same Lybra gates.

### Standing

`Standing` applies the Delegated model through a renewable Owner policy that remains active until Owner revocation.

Standing inherits:

- the non-adjustable Owner escalation floor;
- explicit external harness startup;
- pull-served Lybra requests;
- one-task-per-lease behavior;
- action-bound renewal only;
- fail-safe stop rules;
- append-only provenance;
- distinct-instance correctness audit;
- file-authoritative replayability.

Standing is protocol-defined but implementation-deferred to a separate future stage. AIPOS-164 does not authorize implementation.

## Owner Policy Record

The autonomy dial is itself an Owner policy and must be auditable.

Recommended shape:

```yaml
owner_autonomy_policy:
  policy_id:
  policy_version:
  mode: Supervised | Delegated | Standing
  plane: planner | worker
  status: proposed | active | paused | expired | revoked | superseded
  approved_by_owner:
  owner_approval_ref:
  created_at:
  active_from:
  expires_at:
  max_tasks:
  task_classes:
  projects:
  capabilities:
  write_scopes:
  allowed_reversible_actions:
  escalation_floor_ref: AIPOS-164
  supersedes_policy_ref:
```

Rules:

- activation and upgrade are Owner actions;
- downgrade, pause, expiry, and revocation must be visible;
- policy widening requires a new Owner decision;
- missing or ambiguous policy means `Supervised`;
- a policy must not grant OS permission, credential access, external publication, destructive authority, or any non-adjustable floor action.

## Two Audit Classes

### Provenance Trail Audit

Provenance trail audit is append-only operational evidence. It is always present and independent of dial mode.

It answers:

- who requested an action;
- which concrete instance acted;
- which Owner policy authorized the attempt;
- what input state was used;
- what result occurred;
- which follow-on audit or escalation was linked.

Delegated after-the-fact Owner review depends on this trail.

### Independent Correctness Audit

Independent correctness audit evaluates work quality and acceptance.

Rules:

- executor and auditor must be distinct canonical instances;
- AIPOS-147 `distinct_*` requirements remain explicit and conservatively evaluated;
- complex or consequential work must not skip independent audit;
- no agent may self-audit;
- audit PASS is required before accepted-work follow-ons or delegated reversible finalize;
- `REQUEST_CHANGES` must route to repair and re-audit where required;
- independent correctness audit scales with automation and is not replaced by human after-the-fact review.

## Append-Only Provenance Requirements

Every automatic action under Delegated or future Standing must append an event.

Minimum event fields:

```yaml
event_id:
event_type:
occurred_at:
requested_by:
actor_instance_id:
actor_session_id:
owner_policy_ref:
owner_policy_version:
autonomy_mode:
autonomy_plane:
task_id:
claim_id:
lease_ref:
input_refs:
input_snapshot_ref:
action:
result: PASS | NEEDS_OWNER | BLOCK | REQUEST_CHANGES | FAILED
result_refs:
blocking_reasons:
warnings:
cost_estimate:
related_audit_task_ref:
related_audit_verdict_ref:
parent_event_ref:
retry_of:
```

Additional rules:

- exact field shape may be refined by a later provenance protocol;
- events are append-only and replayable from files;
- raw credentials, secrets, and raw LLM prompt or response payloads must not be persisted by default;
- cost estimate is descriptive provenance only, not a Lybra autonomy-budget control;
- missing required provenance for an automatic action must BLOCK or escalate;
- provenance does not grant authority.

These fields embed the minimum evidence needed by the future State Staleness and Provenance and Trace-Native Audit stages without implementing either stage prematurely.

## Dependency And Finalize Discipline

AIPOS-145 dependency states remain authoritative:

```text
executor_completion
audit_readiness
audit_pass
```

Rules:

- executor completion may enable audit preparation;
- audit readiness may enable an independent audit claim;
- only independent `audit_pass` may enable accepted-work follow-ons;
- delegated reversible finalize requires both independent audit PASS and an explicit active Owner policy that delegates that finalize class;
- non-adjustable floor actions always escalate to Owner;
- external publication, destructive finalize, access changes, procurement, and funds or asset transfer are never delegated.

## MCP Surface Direction

A future implementation slice may define an MCP-native explicit claim pair or equivalent controlled flow.

That future slice must decide:

- exact MCP tool names;
- dry-run and confirm schema;
- capability-token scope;
- controlled execute allowlist mapping;
- explicit claimant instance field;
- Owner policy reference validation;
- lease-writer integration;
- reclaim and recovery transition after lease expiration;
- append-only event-writer integration;
- teaching-error mapping;
- stdio and HTTP/SSE parity.

No such tool or behavior is enabled by AIPOS-164.

## Fail-Safe Rules

The safe outcome is stop and escalate.

An automatic action must not continue when:

- policy is missing, expired, revoked, ambiguous, or wider than its approval;
- claim, lease, session, dependency, provenance, or audit state conflicts;
- actor identity cannot resolve explicitly;
- required independence dimensions are missing or `unknown`;
- a non-adjustable floor action appears;
- any `BLOCK` occurs;
- rollback or replay path is unclear;
- file-authoritative evidence cannot be written or re-read;
- implementation would require a hidden server-side loop.

## Backward Compatibility

- Historical AIPOS-94 `A0..A4` records remain valid and readable.
- Historical `heartbeat_at` fields remain readable but do not authorize renewal.
- AIPOS-48 matching and atomic claim remain authoritative.
- AIPOS-50 lease history remains authoritative; renewal semantics are narrowed to explicit action-bound requests.
- AIPOS-145 strict identity enforcement and dependency-state split remain unchanged.
- AIPOS-147 canonical identities and `distinct_*` evaluator remain unchanged.
- AIPOS-124 HTTP/SSE Bearer authentication and stateless SSE keepalive remain unchanged.
- Historical cards, claims, sessions, records, and evidence are not rewritten.

## Deferred Implementation Slices

AIPOS-164 does not approve implementation.

Future Owner-gated slices should remain separated:

1. Supervised MCP explicit claim protocol and implementation:
   MCP claim dry-run and confirm, explicit claimant instance, Owner policy reference, atomic claim reuse, lease-writer boundary, expiration event, and explicit reclaim flow.
2. Delegated external-harness protocol and implementation:
   Owner policy records, bounded non-cost filters, append-only action attribution, distinct-audit dispatch requests, fail-safe pause, and after-the-fact review surface.
3. Standing protocol refinement and implementation:
   deferred to a separate stage after Delegated dogfood evidence.
4. State Staleness and Provenance:
   refine contradiction and staleness semantics using explicit claim, lease, policy, and provenance evidence.
5. Trace-Native Audit:
   consume append-only operational trace as a first-class audit input.

## Non-Goals

AIPOS-164 does not introduce:

- autonomous Lybra server runtime;
- launcher;
- scheduler;
- timer-driven claim;
- queue polling daemon;
- heartbeat daemon;
- transport-ping-based liveness;
- server-side worker maintenance;
- silent reassignment;
- automatic recovery;
- live Standing implementation;
- tunable escalation floor;
- MCP claim tools;
- MCP authoring tools;
- controlled execute allowlist expansion;
- CLI behavior;
- Board behavior;
- validator behavior;
- queue behavior;
- lease writer;
- records writer;
- deployment behavior;
- credential handling change;
- live BYO-LLM change;
- external-intake assist;
- raw prompt or response persistence;
- public endpoint;
- historical evidence rewrite.
