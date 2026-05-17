# Planner Subtask Draft and Publish Flow

## Purpose

AIPOS-52 defines how an assigned L3 planner converts a parent requirement into visible subtask drafts and how those drafts become queue-visible pending tasks.

This protocol connects AIPOS-51 parent requirement intake to future planner loop work without implementing a planner runtime, queue polling daemon, subtask writer automation, CLI command, Web UI behavior, forum backend, database, or server.

## Core Principle

Planner output must pass through a draft stage before it becomes executable queue work.

```text
parent requirement -> planner plan -> subtask drafts -> publish gate -> pending queue tasks
```

A planner may propose and draft subtasks. A planner must not silently publish tasks into the queue unless the publish policy, Owner decision gates, and controlled execute rules allow it.

## Draft vs Published Task

A subtask draft is a proposed task card that is not yet claimable.

A published subtask is a formal pending task under `5_tasks/queue/pending/` and can be matched and claimed under AIPOS-48.

Recommended draft location:

```text
5_tasks/drafts/planner/{orchestration_id}/{subtask_id}.md
```

Recommended publish target:

```text
5_tasks/queue/pending/{subtask_id}.md
```

AIPOS-52 defines the protocol for these paths. It does not write files or create directories by itself.

## Required Draft Metadata

Planner-created subtask drafts must include:

```yaml
draft_id:
draft_status: planner_draft
draft_created_by:
draft_created_at:
draft_source: planner
publish_status: proposed
publish_target: 5_tasks/queue/pending/
requirement_id:
orchestration_id:
parent_task_id:
created_by_planner: true
planner_agent:
planner_agent_instance:
planner_model_tier: L3
planner_iteration_id:
iteration:
subtask_sequence:
subtask_type:
depends_on:
dag_id:
dag_node_id:
dag_node_type:
dag_layer:
fanout_group_id:
join_gate_id:
depends_on_nodes:
blocks_nodes:
join_input_for:
join_output_from:
dependency_condition:
reviewer:
audit_by:
assigned_to:
agent_instance:
context_bundle:
task_mode:
model_tier:
output_target:
artifact_policy:
session_policy:
context_isolation:
artifact_scope:
memory_scope:
forum_thread_ref:
```

Coding, repair, and finalize subtasks must also define the relevant review, audit, dependency, and acceptance criteria fields described in `planner_subtask_policy.md`.

## Draft Lifecycle

Allowed draft lifecycle values:

```text
proposed
needs_owner
approved_for_publish
published
rejected
superseded
blocked
```

Lifecycle meanings:

- `proposed`: planner created a draft that has not been approved for queue publication.
- `needs_owner`: draft requires Owner decision before it can publish.
- `approved_for_publish`: policy and Owner gates allow publish.
- `published`: draft has been copied or written into pending queue by an approved writer path.
- `rejected`: draft should not be published.
- `superseded`: a newer draft replaces this draft.
- `blocked`: draft cannot proceed because dependency, metadata, or safety checks failed.

## Planner Draft Flow

Minimum flow:

1. Planner reads the parent requirement, forum thread, context bundle, roadmap, queue state, records, and relevant reports.
2. Planner produces a bounded plan with stop conditions and open questions.
3. Planner creates one or more subtask drafts with required metadata.
4. Planner links each draft to the parent requirement and forum thread.
5. Planner marks drafts that require Owner decision as `needs_owner`.
6. Planner does not publish drafts until publish preconditions pass.

## Publish Preconditions

A draft may be published only when all required checks pass:

- parent requirement is active and not cancelled or superseded
- planner assignment is active and L3/L4 for planning decisions
- draft has required metadata and task body sections
- `assigned_to`, `reviewer`, and `audit_by` are explicit when required
- planner is not the reviewer or auditor for its own planned coding work
- dependencies are satisfied or explicitly represented
- DAG node, edge, fanout, and join metadata is acyclic, internally consistent, and explicitly represented when present
- downstream DAG nodes are not marked publish-ready until required upstream conditions are satisfied or represented as blocking
- `max_open_subtasks`, `max_subtasks_total`, and `max_iterations` are not exceeded
- Owner decision gates are not pending
- draft does not introduce scope expansion without Owner approval
- artifact scope, memory scope, output target, and context bundle are clear
- target pending task path is safe and has no collision
- duplicate task_id is not introduced
- dry-run preview and execute-time revalidation pass when a writer path is used

## Owner Decision Gate

Planner must route draft publication to Owner before publish when:

- architecture route split exists
- scope expansion is proposed
- risk escalates
- new runtime, service, database, deployment, or credential boundary is introduced
- audit boundary changes
- workflow mode changes
- model tier or agent authority expands
- planner wants to skip reviewer, audit, or finalize gate
- paid resource, external service, data loss, or irreversible action risk appears
- assignment, reviewer, auditor, or dependency is ambiguous
- DAG cycle, missing node reference, fanout limit conflict, or join gate ambiguity is present
- partial join policy would skip, down-scope, or accept incomplete upstream work

Owner may approve, reject, narrow, or request revision of the draft set.

## Combined Planner/Executor Boundary

AIPOS-53 allows the planner and executor to be the same concrete execution-authority agent instance.

That combined identity does not change publish safety:

- planner-created drafts are still not claimable until published
- publish still requires all AIPOS-52 preconditions
- the agent must not use combined mode to publish around an Owner decision gate
- the agent must not audit its own planner-created or executed work
- finalize still requires independent audit PASS when audit is required
- code and non-code subtasks both preserve task-mode, output, artifact, and memory boundaries

## Publish Operation Boundary

AIPOS-52 does not implement publish. Future implementation should use the controlled execute path for `draft_publish`.

Publishing must preserve these rules:

- source draft remains unchanged unless a separate approved operation updates it
- pending target is written only after revalidation
- dry-run snapshot must match execute-time snapshot
- publish result must be forum-visible
- publish must not move directly to claimed or active session state
- published subtasks become normal pending tasks subject to AIPOS-48 matching and AIPOS-50 session lease binding

## Forum Visibility Expectations

Each draft/publish step should emit or reference forum-visible events:

- planner plan proposed
- subtask draft created
- draft needs Owner decision
- Owner decision recorded
- draft approved for publish
- draft published to pending queue
- draft rejected or superseded
- dependency or audit gate blocks publish

AIPOS-52 defines expected event names and references. AIPOS-53 should define the full Owner Decision Gate and Forum Visibility Protocol.

## AIPOS-93 DAG Metadata

AIPOS-93 extends planner-created subtask drafts with optional DAG, fanout, and join metadata.

The metadata can describe that a draft belongs to a fanout group, waits on a join gate, or provides evidence for a downstream task. It does not make the draft executable or claimable. Publication still requires all AIPOS-52 preconditions, including dependency clarity, Owner gates, safe target path, no duplicate `task_id`, and dry-run/revalidation when a writer path is used.

If a DAG is present, the publish review should confirm:

- the graph is acyclic
- all referenced nodes exist or are explicitly external dependencies
- fanout stays within orchestration limits
- join policies have explicit satisfaction criteria
- partial joins have rationale and Owner approval when required
- task cards and queue directory state remain authoritative over any DAG or index view

## Relationship To AIPOS-51 and AIPOS-53

AIPOS-51 defines parent requirement intake and planner assignment.

AIPOS-52 defines how planner-created subtask drafts become publishable task cards.

AIPOS-53 should define the full Owner Decision Gate and Forum Visibility Protocol.

AIPOS-54 should define the Minimal Planner Loop MVP.

## Non-Goals

AIPOS-52 does not implement:

- subtask draft writer
- draft publish automation
- planner loop runtime
- queue polling runtime
- forum backend
- CLI command changes
- Web UI behavior changes
- database
- server
- deployment config
- direct writes to `5_tasks/drafts/` or `5_tasks/queue/`
