# Subtask DAG Fanout/Join Schema Extension

## Purpose

AIPOS-93 defines protocol metadata for planner-created subtask dependency graphs, fanout groups, and join gates.

This protocol lets a planner represent parallelizable subtask sets and explicit join conditions without turning the planner into an autonomous scheduler.

AIPOS-93 is protocol-only. It does not create task files, write drafts, publish subtasks, mutate queue state, launch runtimes, poll agents, implement a DAG scheduler, add backend routes, add Web UI controls, expand controlled execute, or write `subtask_index.md`.

## Relationship To Existing Protocols

AIPOS-52 remains the draft and publish gate for planner-created subtasks.

AIPOS-54 remains the manual planner tick protocol.

AIPOS-67 keeps summary-state writers deferred.

AIPOS-91 keeps Session Tree operations separate from subtask DAG dependency management.

Task cards, draft task cards, append-only planner iterations, append-only orchestration events, and records remain the file-authoritative evidence. Any future DAG index is derived state and must be rebuildable.

## Suggested Future Path

If a future implementation writes a DAG index, the recommended path is:

```text
5_tasks/orchestration/{orchestration_id}/subtask_dag.md
```

AIPOS-93 does not create this file or directory.

## Core Concepts

### DAG

A subtask DAG is a directed acyclic graph whose nodes represent planner-created work units, draft subtasks, published subtasks, join gates, or evidence gates.

Edges represent prerequisite relationships. An edge never executes work by itself.

### Fanout Group

A fanout group is a bounded set of sibling subtasks that may be prepared or published in parallel when their shared prerequisites are satisfied and orchestration limits allow it.

Fanout does not bypass:

- `max_open_subtasks`
- `max_subtasks_total`
- AIPOS-52 draft publish preconditions
- AIPOS-48 dispatch matching
- AIPOS-50 session lease binding
- reviewer and auditor separation
- Owner decision gates

### Join Gate

A join gate is a non-executable coordination node that waits for one or more upstream nodes to satisfy explicit conditions before downstream subtasks may proceed.

A join gate is not a queue task unless a later planner intentionally creates a normal review, audit, validation, or finalize task card to represent that work.

## DAG Metadata Shape

Planner-created parent tasks, drafts, subtasks, planner iteration entries, or future DAG indexes may include:

```yaml
subtask_dag:
  dag_id:
  orchestration_id:
  parent_task_id:
  dag_version:
  dag_status: proposed
  source_planner_iteration_id:
  source_refs: []
  nodes: []
  edges: []
  fanout_groups: []
  join_gates: []
  validation:
    cycle_check: not_run
    missing_node_refs: []
    blocked_refs: []
    owner_gate_required: false
    owner_gate_reasons: []
  created_by_planner:
  created_at:
  supersedes_dag_id:
  superseded_by_dag_id:
```

Allowed `dag_status` values:

```text
proposed
active
blocked
needs_owner
superseded
completed
cancelled
```

The preferred first-phase status is `proposed`. AIPOS-93 does not define a writer that can mark a DAG `active`.

## Node Shape

Recommended node shape:

```yaml
- node_id:
  node_type: subtask
  task_id:
  draft_id:
  task_path:
  title:
  subtask_type:
  node_status: proposed
  fanout_group_id:
  join_gate_id:
  planner_iteration_id:
  owner_gate_required: false
  owner_gate_reasons: []
  refs: []
```

Allowed `node_type` values:

```text
draft_subtask
published_subtask
join_gate
owner_decision
audit_gate
record_evidence
external_dependency
```

Allowed `node_status` values:

```text
proposed
pending
running
blocked
needs_owner
satisfied
failed
superseded
completed
cancelled
```

Node status is derived from task cards, draft metadata, records, append-only logs, and Owner decision evidence. It must not override queue directory state or task status.

## Edge Shape

Recommended edge shape:

```yaml
- edge_id:
  from_node_id:
  to_node_id:
  edge_type: depends_on
  condition: completed
  required: true
  owner_gate_required: false
  refs: []
```

Allowed `edge_type` values:

```text
depends_on
blocks
fanout_input
fanout_output
join_input
join_output
audit_required
owner_decision_required
evidence_required
```

Allowed `condition` values:

```text
draft_exists
published
claimed
completed
audit_pass
owner_approved
record_exists
artifact_available
manually_satisfied
```

Edges must not form cycles. A detected cycle is blocking and must be surfaced to Owner or independent review before publication or execution continues.

## Fanout Group Shape

Recommended fanout group shape:

```yaml
fanout_groups:
  - fanout_group_id:
    source_node_id:
    fanout_reason:
    member_node_ids: []
    max_parallel_subtasks:
    publish_policy: publish_when_each_ready
    owner_gate_required: false
    owner_gate_reasons: []
    status: proposed
```

Allowed `publish_policy` values:

```text
publish_when_each_ready
publish_as_batch
owner_approved_batch
manual_only
```

Allowed fanout `status` values:

```text
proposed
approved_for_publish
partially_published
published
blocked
needs_owner
superseded
completed
cancelled
```

Fanout groups must stay within parent orchestration limits. Increasing parallelism beyond configured limits requires Owner approval.

## Join Gate Shape

Recommended join gate shape:

```yaml
join_gates:
  - join_gate_id:
    input_node_ids: []
    output_node_ids: []
    join_policy: all_successful
    quorum_count:
    owner_gate_required: false
    owner_gate_reasons: []
    status: proposed
    evidence_refs: []
    satisfied_at:
```

Allowed `join_policy` values:

```text
all_successful
all_completed
any_successful
quorum
owner_approved
manual_review
```

Allowed join `status` values:

```text
proposed
waiting
satisfied
blocked
needs_owner
superseded
cancelled
```

`any_successful`, `quorum`, `owner_approved`, and `manual_review` join policies require explicit rationale. They may require Owner approval when they skip or down-scope upstream work.

## Task And Draft Metadata

Planner-created drafts and published subtasks may include compact DAG metadata:

```yaml
dag_id:
dag_node_id:
dag_node_type: draft_subtask
dag_layer:
fanout_group_id:
join_gate_id:
depends_on_nodes: []
blocks_nodes: []
join_input_for: []
join_output_from: []
dependency_condition:
```

These fields are optional. Missing DAG metadata means the task remains a normal linear or simple-dependency subtask.

## Validation Rules

Future validators, previews, or planners should apply these rules:

- every `dag_id` must be scoped to one `orchestration_id`
- every node id must be unique within a DAG
- every edge must reference existing nodes
- the graph must be acyclic
- every fanout group must reference existing member nodes
- every join gate must reference existing input and output nodes
- `quorum` join gates must define `quorum_count`
- downstream nodes must not publish as ready until required upstream conditions are satisfied or explicitly represented as blocking
- unresolved Owner gates must set `needs_owner`
- conflicting queue state and DAG state must prefer task cards and queue directory state
- ambiguous dependency, fanout, or join status must block publication or become `needs_owner`

## Owner Decision Gates

Planner must stop and request Owner decision when a DAG introduces:

- architecture route split
- scope expansion
- risk escalation
- new runtime, service, database, deployment, credential, or external dependency boundary
- increased parallelism beyond parent orchestration limits
- model tier or agent authority expansion
- reviewer, auditor, or dependency ambiguity
- join policy that accepts partial completion or skips upstream work
- paid resource, external service, data loss, or irreversible action risk
- publish/finalize bypass attempt

Owner may approve, reject, narrow, or request revision of a DAG, fanout group, or join gate.

## Rebuildability

A future DAG view must be rebuildable from:

- planner-created draft metadata
- published task frontmatter with matching `orchestration_id`
- queue directory state
- append-only planner iterations
- append-only orchestration events
- Owner decision records
- audit reports and records
- explicit artifact refs

If a DAG index exists and conflicts with source files, source files win.

## Relationship To Session Tree

Subtask DAG handles work dependency and join semantics.

Session Tree handles execution lineage, fork, rollback, and clone semantics.

A fanout group may later recommend Session Tree branches, but AIPOS-93 does not create session nodes, invoke session primitives, or add controlled execute operations.

## Non-Goals

AIPOS-93 does not implement:

- DAG scheduler
- autonomous planner runtime
- queue polling
- draft writer
- draft publish automation
- queue mutation
- records writer
- orchestration summary writer
- `subtask_dag.md` writer
- `subtask_index.md` writer
- backend routes
- Web UI controls
- CLI commands
- controlled execute allowlist changes
- Session Tree operation execution
- sandbox runtime launch
- agent execution UI
- database
- deployment configuration
- public endpoint behavior
- auth/RBAC
- git automation
- automatic commit/push
- self-audit
