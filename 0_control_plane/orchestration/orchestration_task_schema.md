# Orchestration Task Schema

## Purpose

This schema defines optional task-card fields for planner-orchestrated parent tasks and planner-created subtasks.

These fields are protocol metadata only. AIPOS-25 does not create live pending tasks, move queue files, or implement a task writer.

Planner is a task-scoped optional role. Normal tasks do not require planner fields. A missing orchestration block is equivalent to `orchestration.enabled == false`.

Task cards may independently select workflow rigor:

```yaml
task_class: simple
complexity_note:
```

Missing `task_class` defaults to effective `simple`. Complex-class parent requirements and complex orchestrated tasks require the governed planner loop. `task_mode` remains orthogonal content or operation metadata.

## Parent Task Fields

An orchestrated parent task should include:

```yaml
orchestration_parent: true
requirement_id:
owner_goal:
forum_thread_ref:
intake_status: planner_assigned
orchestration_status: planning
orchestration_state_ref:
planner_loop_state_ref:
subtask_index_ref:
subtask_dag_ref:
planner_iterations_ref:
orchestration_events_ref:
artifact_links_ref:
session_tree_ref:
session_tree_policy_ref:
last_planner_tick_id:
last_planner_tick_verdict:
next_expected_action:
```

The parent task must include:

```yaml
orchestration:
  enabled: true
  planner_required: true
  orchestration_id: orch_<project>_<date>_<slug>
  planner_agent:
  planner_agent_instance:
  planner_runtime_profile:
  planner_model_tier: L3
  planner_assignment_scope: parent_task_only
  planner_assignment_status: proposed
  planner_assignment_started_at:
  planner_assignment_ends_at:
  planner_continuity_policy: sticky_until_parent_complete
  continuity_planner_agent:
  continuity_planner_agent_instance:
  continuity_started_at:
  continuity_ends_at:
  planner_handoff_policy: owner_approved_only
  planner_handoff_reason:
  previous_planner_agent:
  previous_planner_agent_instance:
  planner_permissions:
    can_create_subtasks: true
    can_recommend_handoff: true
    can_mark_needs_owner: true
    can_finalize: false
    can_self_audit: false
  planner_autonomy:
    autonomy_tier: A0
    autonomy_status: proposed
    scope: tick
    approved_by_owner: false
    owner_approval_ref:
    downgrade_to: A0
    downgrade_triggers: []
    failure_threshold:
    max_ticks_without_owner_confirm: 1
    max_subtasks_without_owner_confirm: 0
    max_dag_nodes_without_owner_confirm: 0
    max_session_tree_operations_without_owner_confirm: 0
  max_iterations:
  max_open_subtasks:
  max_subtasks_total:
  subtask_dag_policy:
    enabled: false
    dag_id:
    max_parallel_subtasks:
    default_join_policy: all_successful
    cycle_policy: block_publish
    owner_confirmation_required_for_partial_join: true
  polling_cadence:
  stop_conditions:
  owner_approval_required_for:
  session_tree_policy:
    enabled: false
    allowed_operations: []
    owner_confirmation_required: true
    current_session_tree_id:
    root_session_node_id:
    active_session_node_id:
  failure_policy:
  quota_policy:
```

Allowed `orchestration_status` values:

```text
planning
running
paused
blocked
needs_owner
completed
cancelled
failed
```

## Parent Field Definitions

- `orchestration_parent`: Marks the task as a parent requirement managed by planner protocol.
- `requirement_id`: Stable identifier for the Owner parent requirement or fuzzy goal.
- `owner_goal`: Owner-provided high-level goal before decomposition.
- `forum_thread_ref`: Visible forum/control-plane thread for the parent requirement.
- `intake_status`: Parent requirement intake state before or during planning.
- `orchestration_status`: Current parent orchestration lifecycle state.
- `orchestration_state_ref`: Future path to `orchestration_state.md`.
- `planner_loop_state_ref`: Future path to loop state, usually `5_tasks/orchestration/{orchestration_id}/loop_state.md`.
- `subtask_index_ref`: Future path to a subtask index for this orchestration.
- `subtask_dag_ref`: Future path to a rebuildable subtask DAG/fanout/join index for this orchestration.
- `planner_iterations_ref`: Future path to planner iteration log.
- `orchestration_events_ref`: Future path to orchestration event log.
- `artifact_links_ref`: Future path to orchestration artifact-links record.
- `session_tree_ref`: Future reference to Session Tree lineage evidence, usually the orchestration event log entries with `event_type: session_tree_event`.
- `session_tree_policy_ref`: Optional reference to `0_control_plane/orchestration/session_tree_primitives_protocol.md`.
- `last_planner_tick_id`: Latest AIPOS-54 planner tick identifier, when present.
- `last_planner_tick_verdict`: Latest planner tick verdict.
- `next_expected_action`: Human-readable next action after the latest tick.
- `orchestration.enabled`: Enables protocol metadata for this parent task.
- `orchestration.planner_required`: Requires planner assignment for this parent orchestration task.
- `orchestration.orchestration_id`: Stable orchestration ID.
- `orchestration.planner_agent`: Logical or concrete planner identity.
- `orchestration.planner_agent_instance`: Preferred concrete planner runtime instance.
- `orchestration.planner_runtime_profile`: Planner runtime profile selector.
- `orchestration.planner_model_tier`: Must be L3 or L4 for planning decisions.
- `orchestration.planner_assignment_scope`: `parent_task_only` or `orchestration_group`.
- `orchestration.planner_assignment_status`: `proposed`, `active`, `paused`, `completed`, `cancelled`, or `superseded`.
- `orchestration.planner_assignment_started_at`: When the task-scoped planner assignment became active.
- `orchestration.planner_assignment_ends_at`: Planned or actual end of planner assignment authority.
- `orchestration.planner_continuity_policy`: Continuity rule for the parent planner. Complex-class parent requirements should use `sticky_until_parent_complete`.
- `orchestration.continuity_planner_agent`: Logical continuity planner identity after first active assignment.
- `orchestration.continuity_planner_agent_instance`: Concrete planner instance expected to continue the parent requirement.
- `orchestration.continuity_started_at`: When planner continuity became active.
- `orchestration.continuity_ends_at`: When planner continuity ended, if terminal or handed off.
- `orchestration.planner_handoff_policy`: Handoff rule. Complex-class parent requirements should use `owner_approved_only`.
- `orchestration.planner_handoff_reason`: Reason for Owner-approved planner handoff.
- `orchestration.previous_planner_agent`: Prior planner identity when handoff occurs.
- `orchestration.previous_planner_agent_instance`: Prior concrete planner instance when handoff occurs.
- `orchestration.planner_permissions`: Protocol-level permissions only, not OS/GitHub permissions.
- `orchestration.planner_autonomy`: Optional AIPOS-94 Planner Autonomy Tier metadata. Missing or unapproved policy means A0.
- `orchestration.max_iterations`: Maximum planner iterations.
- `orchestration.max_open_subtasks`: Maximum subtasks open at one time.
- `orchestration.max_subtasks_total`: Maximum subtasks over the whole orchestration.
- `orchestration.subtask_dag_policy`: Optional AIPOS-93 policy metadata for future DAG/fanout/join validation. It does not enable a scheduler or writer.
- `orchestration.polling_cadence`: Advisory future check cadence.
- `orchestration.stop_conditions`: Explicit stop conditions.
- `orchestration.owner_approval_required_for`: Actions requiring Owner approval.
- `orchestration.session_tree_policy`: Optional protocol metadata for future Session Tree primitive eligibility. It does not enable execution by itself.
- `orchestration.failure_policy`: Reference or inline policy for interruption handling.
- `orchestration.quota_policy`: Reference or inline runtime budget and quota policy.

Planner gating rules:

- `orchestration.enabled` missing or false: planner not required.
- `orchestration.enabled: true` and `planner_required: true`: `planner_agent` and `planner_model_tier` required.
- `orchestration.enabled: true` and `planner_required: false`: planner fields optional.
- `orchestration.enabled: true` with automated or planner-driven subtask creation: `planner_agent` required.
- complex-class parent requirement with active planner assignment: continuity planner fields are expected.
- active planner identity change: requires Owner-approved handoff metadata.
- autonomy tier above A0: requires Owner-approved autonomy metadata.
- missing, ambiguous, or unapproved autonomy tier: treat as A0.
- autonomy tier A4: forbidden until a separate future Owner-approved protocol and implementation task.

## Subtask Fields

Planner-created subtasks must include:

```yaml
task_class:
complexity_note:
orchestration_id:
parent_task_id:
created_by_planner: true
planner_agent:
continuity_planner_agent:
iteration:
subtask_sequence:
subtask_type: coding
depends_on: []
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

Subtasks inherit `orchestration_id` and `parent_task_id`. Subtasks should reference `planner_agent` when created by planner, but they do not necessarily require their own independent planner assignment.

Allowed `subtask_type` values:

```text
coding
audit
research
docs
finalize
repair
```

## Subtask Field Definitions

- `orchestration_id`: Links the subtask to the parent orchestration.
- `parent_task_id`: Parent requirement task ID.
- `created_by_planner`: Whether planner created or proposed this subtask.
- `planner_agent`: Planner identity responsible for the decomposition.
- `iteration`: Planner iteration that emitted this subtask.
- `subtask_sequence`: Stable sequence number within parent task, used in `subtask_id`.
- `subtask_type`: Work type for routing and audit rules.
- `task_class`: `simple` or `complex`; complex-class subtasks preserve the governed planner and independent audit loop.
- `complexity_note`: Optional advisory explanation for the selected task class.
- `depends_on`: Prior subtasks or records that must complete first.
- `dag_id`: Optional AIPOS-93 subtask DAG identifier scoped to the orchestration.
- `dag_node_id`: Optional node id for this draft or published subtask inside the DAG.
- `dag_node_type`: Optional node type such as `draft_subtask`, `published_subtask`, `join_gate`, `owner_decision`, `audit_gate`, `record_evidence`, or `external_dependency`.
- `dag_layer`: Optional display or planning layer in the DAG.
- `fanout_group_id`: Optional fanout group membership.
- `join_gate_id`: Optional join gate this subtask represents or depends on.
- `depends_on_nodes` / `blocks_nodes`: Optional structured DAG node dependencies.
- `join_input_for` / `join_output_from`: Optional join-gate relationship refs.
- `dependency_condition`: Optional condition such as `completed`, `audit_pass`, `owner_approved`, or `artifact_available`.

Planner-created subtasks must not silently grant planner rights to coder or reviewer. Planner assignment stays with the parent orchestration unless explicitly delegated by Owner-approved policy.

AIPOS-93 DAG fields are optional protocol metadata. They do not make a task claimable, publish a draft, launch a scheduler, or bypass publish, matching, lease, review, audit, or Owner gates.

Future file-driven orchestration layout is expected to include:

```text
5_tasks/orchestration/
  {orchestration_id}/
    orchestration_state.md
    subtask_index.md
    planner_iterations.md
    orchestration_events.md
    artifact_links.md
    subtask_dag.md
```

## Required Standard Task Fields

Planner-created tasks still need normal task fields:

```yaml
task_id:
title:
project:
task_type: one_shot
assigned_to:
agent_instance:
reviewer:
audit_by:
role_continuity_preference:
  role_family:
  preferred_from_last_success:
  prior_agent_instance:
  prior_task_id:
  preference_scope: parent_orchestration
  preference_status: advisory
context_bundle:
task_mode:
task_class:
complexity_note:
model_tier:
priority:
status: pending
created_by:
needs_owner:
output_target:
artifact_policy:
session_policy:
context_isolation:
artifact_scope:
memory_scope:
```

Complex-class tasks must declare independent review fields such as `reviewer` and `audit_by`. Planner-created complex-class tasks must require audit before finalize.

`reviewer` and `audit_by` are separate field names for related independent review gates. `reviewer` usually covers process or artifact review; `audit_by` usually covers the formal PASS / REQUEST_CHANGES gate before finalize.

`role_continuity_preference` is optional advisory metadata. It may point to the most recent successful same-family coder or independent reviewer within the parent orchestration, but it does not create a hard assignment or bypass matching, claim, lease, review, audit, or Owner gates.

## Owner Approval Fields

Recommended owner approval configuration:

```yaml
owner_approval_required_for:
  - new_external_service
  - paid_resource
  - model_tier_escalation
  - high_risk_architecture_change
  - quota_exhausted_without_fallback
  - reviewer_conflict
  - executor_conflict
```

## Runtime Budget Policy Reference

Parent tasks may inline `runtime_budget_policy` or point to a policy document:

```yaml
runtime_budget_policy_ref: 0_control_plane/orchestration/runtime_budget_policy.md
```

If both inline and reference exist, the inline task field should only narrow or override the referenced policy in explicit ways.
