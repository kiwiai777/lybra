# Task Session Schema

## Purpose

This document defines the minimal schema for a Task Session in AI Project OS.

## Schema Role

The Task Session schema captures the execution envelope for one selected task.

It is separate from queue state and separate from Board rendering state.

## Required Fields

```yaml
session_id:
task_id:
project:
assigned_to:
agent_instance:
task_mode:
context_bundle:
model_tier:
artifact_scope:
memory_scope:
output_target:
artifact_policy:
claim_id:
runtime_profile:
execution_host:
repo_host:
validation_host:
git_host:
canonical_repo_path:
lease_status:
lease_started_at:
lease_expires_at:
heartbeat_at:
session_started_at:
session_status:
created_by:
```

## Optional Fields

```yaml
session_ended_at:
session_owner:
working_directory:
input_refs:
memory_refs:
artifact_links:
report_target:
resume_allowed:
resume_context_path:
parent_session_id:
related_sessions:
handoff_to:
escalation_target:
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
lease_duration_seconds:
renewal_count:
renewal_policy:
abandoned_by:
abandon_reason:
handoff_reason:
notes:
```

## Required / Defaultable Fields

```yaml
session_status: selected
resume_allowed: false
context_isolation: strict
```

## Field Definitions

- `session_id`: Unique identifier for one execution context.
- `task_id`: Task identifier that this session is bound to.
- `project`: Project context used for this session. May be a named project or an explicit cross-project/general label.
- `assigned_to`: Role instance that owns the task assignment boundary.
- `agent_instance`: Concrete agent instance selected for this session.
- `task_mode`: Task-scoped execution mode used in this session.
- `context_bundle`: Context bundle reference used to inject environment and operating boundaries.
- `model_tier`: Model tier selected for this session.
- `artifact_scope`: Allowed artifact boundary for reading, drafting, or linking artifacts.
- `memory_scope`: Allowed memory boundary for reading, drafting, or proposing memory updates.
- `output_target`: Destination for task output or report artifacts.
- `artifact_policy`: Policy that governs what kind of artifact writes or links are allowed.
- `claim_id`: Identifier for the queue claim event associated with this session.
- `runtime_profile`: Runtime profile bound to this session.
- `execution_host`: Host where execution is authorized for this session.
- `repo_host`: Host where canonical repository state lives for this session.
- `validation_host`: Host where validation output is authoritative.
- `git_host`: Host where git operations are authoritative.
- `canonical_repo_path`: Repository path on the authoritative repo host.
- `lease_status`: Lease lifecycle state for active execution, such as `active`, `expired`, `abandoned`, `completed`, or `blocked`.
- `lease_started_at`: Timestamp when active execution lease began.
- `lease_expires_at`: Timestamp after which active execution authority is no longer assumed.
- `heartbeat_at`: Last observed heartbeat for the active lease.
- `session_started_at`: Timestamp when the session became active or was prepared for active execution.
- `session_status`: Session lifecycle state. Default is `selected`.
- `created_by`: Human or agent that created the session record.
- `session_ended_at`: Timestamp when the session stopped through completion, block, or abandonment.
- `session_owner`: Current owner of the session record or execution responsibility.
- `working_directory`: Local or remote working path prepared for this session when relevant.
- `input_refs`: Explicit task inputs from files, docs, or linked materials.
- `memory_refs`: Explicit memory sources allowed for this session.
- `artifact_links`: Explicit cross-referenced artifact links allowed for this session.
- `report_target`: Concrete reporting destination when more specific than `output_target`.
- `resume_allowed`: Whether this session may be resumed later. Default is `false`.
- `resume_context_path`: Path to resume notes, transcript, or session-specific state.
- `parent_session_id`: Parent session when this session is a resume, retry, or derived execution.
- `related_sessions`: Other sessions explicitly linked to this one.
- `handoff_to`: Explicit next owner or receiving agent if the work is handed off.
- `escalation_target`: Explicit escalation destination for higher model tier or another owner.
- `session_tree_id`: Logical Session Tree lineage identifier when the session participates in a future Session Tree.
- `session_node_id`: Logical node identifier for this session within a Session Tree.
- `session_tree_parent_node_id`: Parent node in the Session Tree lineage.
- `session_tree_root_node_id`: Root node for the Session Tree lineage.
- `session_tree_operation`: Operation that created or proposed this node, such as `session_fork`, `session_rollback`, or `session_clone`.
- `session_tree_event_id`: Append-only event id that records the Session Tree operation.
- `source_session_id`: Source session for fork, clone, or rollback metadata.
- `source_session_node_id`: Source Session Tree node for fork, clone, or rollback metadata.
- `target_session_node_id`: Target Session Tree node proposed or created by the operation.
- `rollback_target_session_id`: Prior session targeted by a rollback recovery branch.
- `rollback_target_session_node_id`: Prior Session Tree node targeted by a rollback recovery branch.
- `branch_status`: Metadata lifecycle for the branch, such as `proposed`, `active`, `abandoned`, `superseded`, `merged`, `rejected`, `rolled_back`, or `completed`.
- `branch_reason`: Reason for fork, clone, or rollback.
- `branch_scope`: Scope of the branch operation.
- `owner_confirmation_ref`: Reference to Owner confirmation evidence when the branch operation is confirmed.
- `lease_duration_seconds`: Intended duration of the active lease.
- `renewal_count`: Number of times the active lease has been renewed.
- `renewal_policy`: Renewal rule, such as `same_agent_instance_only`.
- `abandoned_by`: Actor that abandoned the session, when applicable.
- `abandon_reason`: Reason the session was abandoned.
- `handoff_reason`: Reason for handoff, when applicable.
- `notes`: Free-form notes about setup, constraints, or decisions.

## Lifecycle Values

Recommended `session_status` values:

- `discovered`
- `selected`
- `claimed`
- `active`
- `blocked`
- `completed`
- `abandoned`
- `expired`
- `handed_off`

## Session ID and Claim ID

Recommended `session_id` format:

`session_{task_id}_{YYYYMMDD_HHMMSS}_{agent_slug}`

Example:

`session_AIPOS-13_20260427_143000_dev_codex_local`

Recommended `claim_id` format:

`claim_{task_id}_{YYYYMMDD_HHMMSS}_{agent_slug}`

Clarifications:

- `task_id` identifies the task
- `claim_id` identifies the queue claim event
- `session_id` identifies the execution context
- one task may have multiple sessions over time if retried, resumed, or handed off

## Context Isolation Rule

`context_isolation: strict` means the selected Task Session is the only active execution context.

The agent must not silently import another task's instructions, artifacts, output target, or report chain into this session.


## Lease Semantics

AIPOS-50 defines lease fields as protocol targets for binding a claimed task to a concrete running execution session. The schema records lease state, but does not implement heartbeat, renewal, recovery, or any writer behavior.
