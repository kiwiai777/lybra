# Session Tree Primitives Protocol

## Purpose

AIPOS-91 defines the protocol boundary for Session Tree primitives:

- `session_fork`
- `session_rollback`
- `session_clone`

These primitives support future branch, recovery, and comparison workflows for planner-managed sessions while preserving the file-authoritative control plane.

AIPOS-91 is protocol documentation only. It does not implement controlled execute operations, writers, CLI commands, Web UI controls, runtime launch, sandbox behavior, SessionStore behavior, queue mutation, records mutation, database state, git automation, or autonomous planner execution.

## Strategic Source

DL-20260513-04 accepted Session Tree primitives as a future direction:

```text
Session Tree three controlled execute operations: session_fork, session_rollback, session_clone.
Append-only session_tree_event records every branch operation.
```

DL-20260515-06 later refined adjacent SessionStore and agent catalog direction without implementing Session Tree behavior.

## Scope

AIPOS-91 defines:

- primitive names
- session tree identity fields
- required dry-run and confirmation lifecycle
- append-only event shape
- Owner gates
- safety rules
- schema alignment targets
- non-goals

AIPOS-91 does not add the primitives to any active runtime allowlist.

## Core Concepts

### Session Tree

A Session Tree is a logical lineage of task or orchestration execution sessions.

It records how one session-derived working state relates to another:

```text
root session
  -> forked exploration branch
  -> cloned comparison branch
  -> rollback recovery branch
```

The tree is metadata and audit structure. It is not a live runtime object.

### Session Node

A Session Node represents one visible execution context in the lineage.

Recommended identifier format:

```text
session_node_{orchestration_id}_{NN}
```

The node should reference a concrete `session_id` when a session record exists, but the node does not create a session record by itself.

### Session Tree Event

A `session_tree_event` is the append-only evidence record for a fork, rollback, or clone decision.

The preferred persistence target is the existing orchestration event log:

```text
5_tasks/orchestration/{orchestration_id}/orchestration_events.md
```

AIPOS-91 does not create a separate `session_tree_events.md` writer. A dedicated file may be proposed by a later task only if the existing event log becomes insufficient.

## Primitive Semantics

### `session_fork`

Purpose:

- create a proposed branch from an existing session or session node for alternate implementation, repair, exploration, or comparison.

Required inputs:

```yaml
operation: session_fork
orchestration_id:
parent_task_id:
source_session_id:
source_session_node_id:
proposed_child_session_node_id:
fork_reason:
fork_scope:
actor:
forum_thread_ref:
owner_confirmation_token:
```

Rules:

- fork must preserve source refs and lineage metadata
- fork must state whether it is exploration, repair, alternative implementation, or comparison
- fork does not claim a task
- fork does not launch a runtime
- fork does not copy local working directories
- fork does not create queue tasks or records by itself
- fork does not bypass reviewer, auditor, or Owner gates

### `session_rollback`

Purpose:

- create a proposed recovery branch from a prior session node after a failed or undesirable branch.

Required inputs:

```yaml
operation: session_rollback
orchestration_id:
parent_task_id:
current_session_id:
current_session_node_id:
rollback_target_session_id:
rollback_target_session_node_id:
proposed_recovery_session_node_id:
rollback_reason:
rollback_scope:
actor:
forum_thread_ref:
owner_confirmation_token:
```

Rules:

- rollback means "continue from a prior known-good session lineage point"
- rollback does not mean `git reset`, destructive file revert, database restore, deletion, or queue rewind
- rollback must list what state is being abandoned and what evidence supports the target
- rollback must preserve the abandoned branch in append-only history
- rollback must not erase reports, audits, task cards, or event logs
- rollback must not resolve an Owner decision by itself
- rollback must not modify source files unless a later approved implementation task explicitly defines a safe writer

### `session_clone`

Purpose:

- create a proposed sibling node with the same source context for side-by-side comparison, reviewer reproduction, controlled handoff, or model/provider comparison.

Required inputs:

```yaml
operation: session_clone
orchestration_id:
parent_task_id:
source_session_id:
source_session_node_id:
proposed_clone_session_node_id:
clone_reason:
clone_scope:
actor:
forum_thread_ref:
owner_confirmation_token:
```

Rules:

- clone must state whether it is for reproduction, comparison, handoff, review, or controlled retry
- clone may reference the same source context but must not silently share mutable runtime state
- clone does not duplicate credentials
- clone does not duplicate `.env`
- clone does not bypass context isolation
- clone does not bypass claim, lease, audit, or Owner gates

## Controlled Execute Lifecycle

All three primitives are future controlled execute operations.

Required lifecycle:

```text
preview/dry-run -> dry_run_token -> snapshot hash -> Owner review -> confirm -> append session_tree_event
```

Required controlled execute properties:

- operation name must match the reviewed dry-run
- actor must match the reviewed dry-run or an approved alias
- source session refs must still resolve
- source snapshot hash must still match
- target node id must not collide
- Owner confirmation must be explicit
- execute-time revalidation must run immediately before append
- confirmed operation may append only the approved event unless a later task explicitly approves additional writes

AIPOS-91 does not implement this lifecycle. It defines the required lifecycle for future implementation.

## Append-Only Event Shape

Recommended `session_tree_event` detail payload:

```yaml
event_type: session_tree_event
session_tree_event:
  tree_event_id:
  operation: session_fork | session_rollback | session_clone
  orchestration_id:
  parent_task_id:
  source_session_id:
  source_session_node_id:
  target_session_node_id:
  rollback_target_session_id:
  rollback_target_session_node_id:
  prior_branch_status:
  branch_status: proposed
  actor:
  owner_confirmed: true
  owner_confirmation_token:
  dry_run_id:
  dry_run_snapshot_hash:
  reason:
  scope:
  source_refs: []
  created_at:
  notes:
```

Allowed `branch_status` values:

```text
proposed
active
abandoned
superseded
merged
rejected
rolled_back
completed
```

`merged` is metadata only in AIPOS-91. It does not implement merge behavior.

## Session Tree Metadata Fields

Recommended optional fields for orchestration, planner iteration, task session, or session record schemas:

```yaml
session_tree_id:
session_node_id:
session_tree_parent_node_id:
session_tree_root_node_id:
session_tree_operation:
session_tree_event_id:
source_session_id:
source_session_node_id:
target_session_node_id:
rollback_target_session_id:
rollback_target_session_node_id:
branch_status:
branch_reason:
branch_scope:
owner_confirmation_ref:
```

These fields are metadata only. They do not grant execution authority.

## Owner Decision Gates

Session Tree operations require Owner review when they involve:

- architecture route split
- scope expansion
- risk escalation
- new runtime, service, database, deployment, or credential boundary
- model tier escalation
- agent authority expansion
- audit boundary change
- reviewer or auditor independence ambiguity
- rollback from a completed or audited state
- abandoning a branch with unreviewed work
- paid resource use
- external service use
- any destructive or irreversible action

AIPOS-91 treats all three primitives as Owner-confirmed by default. A later task may propose narrower automation only through a new Owner Decision Gate and independent audit.

## Relationship To Existing Protocols

- AIPOS-37/38/55/77 define controlled execute principles. AIPOS-91 reserves future primitive semantics but does not extend the current implementation allowlist.
- AIPOS-50 defines task session lease and runtime binding. Session Tree metadata does not create or renew leases.
- AIPOS-53/54 define Owner-gated planner loop behavior. Session Tree operations must stop on the same Owner decision gates.
- AIPOS-65/66 define append-only orchestration event and planner iteration writers. AIPOS-91 reuses append-only event principles but does not add writer behavior.
- AIPOS-67/68 keep summary state rebuilds read-only or deferred. Session Tree metadata must be reconstructable from append-only events and session records.
- AIPOS-90 defines sandbox runtime abstraction. Session Tree primitives do not enable any sandbox provider.
- AIPOS-94 may later define autonomy tiers. AIPOS-91 does not raise autonomy level.

## Required Future Implementation Preconditions

Before any primitive can be implemented, a later task must define and audit:

- exact controlled execute allowlist update
- dry-run preview output
- confirmation payload
- event writer path
- duplicate and collision checks
- source session resolution
- snapshot hash calculation
- Owner confirmation token handling
- UI or CLI surface, if any
- rollback safety semantics
- audit handoff requirements
- recovery and failure behavior

## Non-Goals

AIPOS-91 does not implement:

- controlled execute allowlist expansion
- backend route
- CLI command
- Web UI control
- event writer changes
- planner iteration writer changes
- queue mutation
- draft mutation
- records mutation
- session lease writer
- automatic session resume
- SessionStore
- sandbox runtime launch
- provider integration
- branch merge behavior
- git reset
- git checkout
- git revert
- git commit
- git push
- database restore
- filesystem rollback
- credential copy
- `.env` copy
- autonomous planner runtime
- background polling
- auth/RBAC
- database
- deployment configuration
- public endpoint behavior
- self-audit
