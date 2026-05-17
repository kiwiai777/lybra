# Planner Autonomy Tier Protocol

## Purpose

AIPOS-94 defines Planner Autonomy Tier metadata for Lybra orchestration.

Autonomy tiers describe how much planner loop progress may occur between explicit Owner confirmations after a future implementation is separately approved.

AIPOS-94 is protocol-only. It does not implement an autonomous planner runtime, scheduler, queue polling, task writer, draft writer, draft publish automation, controlled execute allowlist change, backend route, Web UI control, CLI command, Session Tree operation execution, sandbox runtime launch, agent execution UI, deployment configuration, auth/RBAC, database, public endpoint behavior, git automation, automatic commit/push, automatic finalize, or self-audit.

## Source Decision

AIPOS-94 implements the direction recorded in `DL-20260513-04` Decision 5:

```text
A0: Owner confirms every tick.
A1: Planner may self-iterate inside one subtask; Owner confirms between subtasks.
A2: Planner may self-run inside one approved subtask DAG; Owner confirms between DAGs.
A3: Planner may experiment inside one orchestration, including future approved fork/rollback/clone flows; Owner confirms between orchestrations.
A4: Fully autonomous; not implemented.
```

## Core Rule

Autonomy tier changes confirmation cadence only.

Autonomy tier never grants:

- file write authority
- queue mutation authority
- draft publish authority
- controlled execute authority
- reviewer authority
- auditor authority
- Owner decision authority
- credential access
- runtime launch authority
- git commit or push authority
- finalize authority

All durable behavior remains governed by the existing file-authoritative control plane, AIPOS-52 draft/publish gates, AIPOS-48 matching, AIPOS-50 session lease binding, AIPOS-53/54 Owner gates, AIPOS-77 controlled persistence gates, AIPOS-90 sandbox runtime boundaries, AIPOS-91 Session Tree protocol, AIPOS-92 credential boundaries, and AIPOS-93 DAG rules.

## Tier Values

Allowed autonomy tier values:

```text
A0
A1
A2
A3
A4
```

### A0: Owner Confirm Every Tick

A0 is the current default.

Rules:

- every planner tick requires explicit Owner or human invocation
- each tick emits one visible verdict
- no background polling is allowed
- no automatic draft creation, publish, queue claim, execution, record writing, or finalize is allowed
- Owner confirms before each next tick or action boundary

A0 is the required fallback tier when policy is missing, ambiguous, blocked, or downgraded after failures.

### A1: Subtask-Local Self-Iteration

A1 may be used only after a future Owner-approved implementation task.

Intended scope:

- planner/executor may iterate inside one approved subtask boundary
- Owner confirmation is required before moving to another subtask
- subtask scope, artifact scope, memory scope, context bundle, model tier, and output target must remain unchanged

A1 must stop for Owner decision when any critical fork appears.

A1 does not publish drafts, claim new tasks, alter queue state, change auditor/reviewer assignment, or finalize.

### A2: Approved Subtask DAG Self-Run

A2 may be used only after a future Owner-approved implementation task.

Intended scope:

- planner may progress inside one approved AIPOS-93 subtask DAG
- Owner confirmation is required before starting a different DAG
- DAG nodes, fanout groups, join gates, and conditions must already be approved or explicitly represented
- DAG execution must remain bounded by `max_open_subtasks`, `max_subtasks_total`, failure thresholds, and Owner gates

A2 must stop when a DAG cycle, missing node reference, ambiguous join, partial completion policy, dependency conflict, audit gap, or Owner gate appears.

A2 does not bypass AIPOS-52 publish gates, AIPOS-48 matching, AIPOS-50 leases, independent audit, or controlled execute.

### A3: Orchestration-Local Experimentation

A3 may be used only after a future Owner-approved implementation task.

Intended scope:

- planner may progress inside one approved orchestration
- Owner confirmation is required before starting a different orchestration
- future approved Session Tree fork, rollback, or clone flows may be part of the orchestration-local strategy only when their own controlled execute implementation exists

A3 must preserve every Owner Decision Gate. It must not treat Session Tree operations as free internal actions unless a later audited implementation explicitly defines that behavior and Owner approves the tier policy.

A3 does not grant commit, push, external publish, finalize, credential expansion, deployment, new service, database, model-tier escalation, audit-boundary change, or architecture direction authority.

### A4: Fully Autonomous

A4 is reserved and forbidden in current Lybra.

Rules:

- no implementation may enable A4 under AIPOS-94
- A4 cannot be selected by default
- A4 cannot be inferred from missing policy
- A4 requires a separate future Owner Decision Gate, protocol task, implementation task, independent audit, and explicit finalize

## Recommended Policy Shape

Parent orchestration tasks may include:

```yaml
planner_autonomy:
  autonomy_tier: A0
  autonomy_status: proposed
  scope: tick
  approved_by_owner: false
  owner_approval_ref:
  allowed_without_owner_confirm: []
  owner_confirm_required_for: []
  downgrade_to: A0
  downgrade_triggers: []
  failure_threshold:
  last_downgrade_reason:
  current_autonomy_window_id:
  autonomy_window_started_at:
  autonomy_window_expires_at:
  max_ticks_without_owner_confirm: 1
  max_subtasks_without_owner_confirm: 0
  max_dag_nodes_without_owner_confirm: 0
  max_session_tree_operations_without_owner_confirm: 0
```

Allowed `autonomy_status` values:

```text
proposed
active
paused
downgraded
blocked
completed
cancelled
superseded
```

Allowed `scope` values:

```text
tick
subtask
subtask_dag
orchestration
reserved
```

The default policy is:

```yaml
autonomy_tier: A0
scope: tick
approved_by_owner: false
max_ticks_without_owner_confirm: 1
max_subtasks_without_owner_confirm: 0
max_dag_nodes_without_owner_confirm: 0
max_session_tree_operations_without_owner_confirm: 0
```

## Planner Iteration Metadata

Planner iteration entries may record autonomy context:

```yaml
autonomy_tier:
autonomy_scope:
autonomy_window_id:
owner_confirm_required: true
owner_confirm_ref:
autonomy_actions_taken: []
autonomy_blockers: []
autonomy_downgrade_triggered: false
autonomy_downgrade_reason:
```

These fields are evidence only. They do not authorize execution.

## Tier Upgrade Rules

Increasing autonomy tier is always an Owner Decision Gate.

Examples:

```text
A0 -> A1
A1 -> A2
A2 -> A3
any tier -> A4
```

Tier upgrade requests must include:

- current tier
- requested tier
- requested scope
- rationale
- source refs
- risk summary
- explicit non-bypass statement for audit, credential, Owner, and controlled execute gates
- rollback/downgrade plan

If approval evidence is missing, the effective tier is A0.

## Downgrade Rules

Any tier must downgrade to A0 when:

- failure threshold is reached
- repeated repair loop is detected
- Owner decision is required
- architecture route split appears
- scope expansion appears
- risk escalates
- new runtime, service, database, deployment, or credential boundary is proposed
- model tier or agent authority escalation is needed
- audit boundary changes
- reviewer or auditor independence is ambiguous
- dependency, DAG, or join state is ambiguous
- quota or runtime status is unknown beyond configured threshold
- controlled execute preconditions fail
- credential boundary is unclear
- external publish, commit, push, or finalize is requested

Downgrade should be recorded in visible planner iteration or orchestration event evidence when a writer exists. Without a writer, the planner must report the downgrade in the visible handoff.

## Owner Decision Gates

No autonomy tier may bypass Owner decision for:

- architecture route split
- scope expansion
- risk escalation
- new runtime, service, database, deployment, or credential boundary
- security or credential boundary change
- audit boundary change
- workflow mode change
- model tier or agent authority expansion
- turning protocol into implementation
- skipping reviewer, auditor, publish, claim, session lease, or finalize gate
- paid resource or external service requirement
- data loss or irreversible action risk
- external publish
- commit or push
- finalize
- any decision that changes system long-term direction

Owner may approve, deny, narrow, or request revision of autonomy tier scope.

## Relationship To Controlled Execute

Autonomy tier does not add operations to the controlled execute allowlist.

If a future action is already controlled execute, autonomy tier may only change when the planner is allowed to request or prepare dry-run previews. It cannot bypass:

- dry-run
- token generation
- snapshot hash
- actor match
- execute-time revalidation
- explicit Owner confirmation where required
- writer-level constraints

## Relationship To Session Tree

AIPOS-91 Session Tree primitives remain separate.

Autonomy tiers may reference future Session Tree workflows, but AIPOS-94 does not execute `session_fork`, `session_rollback`, or `session_clone`.

Any future Session Tree operation must follow its own approved controlled execute lifecycle unless a later Owner-approved task explicitly changes that boundary.

## Relationship To Subtask DAG

AIPOS-93 subtask DAG metadata remains dependency and join metadata.

A2 may refer to an approved DAG boundary, but AIPOS-94 does not schedule DAG execution, publish DAG subtasks, claim tasks, launch workers, or resolve join gates.

## Audit And Review Invariants

No autonomy tier may:

- self-audit
- replace independent review
- mark audit PASS
- mark REQUEST_CHANGES repaired without re-audit when policy requires it
- finalize before required audit
- suppress audit findings
- hide failures from Owner or control-plane records

Planner/executor combined mode does not change these invariants.

## Non-Goals

AIPOS-94 does not implement:

- autonomous planner runtime
- scheduler
- background polling
- queue polling
- agent execution loop
- task movement
- draft writer
- draft publish automation
- queue claim automation
- records writer
- orchestration writer
- summary state writer
- Session Tree operation execution
- subtask DAG scheduler
- controlled execute allowlist expansion
- backend route
- Web UI control
- CLI command
- MCP tool
- sandbox runtime launch
- credential minting or storage
- auth/RBAC
- database
- deployment configuration
- public endpoint behavior
- git commit or push automation
- automatic finalize
- self-audit
