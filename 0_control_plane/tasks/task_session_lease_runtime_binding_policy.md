# Task Session Lease and Runtime Binding Policy

## Purpose

AIPOS-50 defines how a claimed task binds to a concrete running execution session.

This policy turns the previously reserved relationship between `claim_id`, `active_session_id`, runtime profile, and execution host into an auditable protocol boundary. It is protocol documentation only.

AIPOS-50 does not implement a queue daemon, polling runtime, scheduler, CLI command, Web UI behavior, database, server, deployment config, or background worker.

## Core Definitions

- Claim: the atomic task ownership transition defined by AIPOS-48.
- Task Session: the isolated execution envelope defined by `task_session_policy.md` and `task_session_schema.md`.
- Session Lease: a time-bounded authority record that says one concrete agent instance may actively execute one claimed task session.
- Runtime Binding: the link from a session lease to the concrete runtime profile, execution host, repository path, validation host, and git host.

## Lease Binding Rule

A claimed task may have at most one active session lease.

A session lease binds:

```yaml
task_id:
claim_id:
active_session_id:
agent_instance:
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
```

The binding is valid only when the claim still belongs to the same actor and the task has not moved to completed, blocked, abandoned, or reopened state.

## Lease Lifecycle

Recommended lease lifecycle values:

- `proposed`: preview or local preparation has proposed a session and lease, but no authority is active.
- `active`: the claimed task is bound to one active execution session.
- `renewing`: the same agent instance is extending the active lease.
- `expired`: the lease passed `lease_expires_at` without renewal.
- `abandoned`: the operator or agent intentionally stopped execution without completion.
- `completed`: execution finished and reported.
- `blocked`: execution cannot continue without fix, external dependency, or Owner decision.
- `handed_off`: responsibility moved to another explicit agent or Owner-approved receiver.

`proposed` is not execution authority. `active` is the only state that permits execution under this policy.

## Required Lease Preconditions

A session lease may become active only after:

- the task is selected as exactly one Task Session
- Start Task Session preview passes or warnings are explicitly acknowledged
- unresolved BLOCK and NEEDS_OWNER findings are absent
- the task is claimed by the same actor or instance that will execute
- `claim_id` exists or is created by the claim flow
- `active_session_id` is absent or belongs to the same active session
- runtime profile fields identify execution, repo, validation, and git hosts
- artifact scope, memory scope, output target, context bundle, task mode, and model tier are clear

## Runtime Binding Fields

Runtime binding should reuse fields from `runtime_profile_policy.md`:

```yaml
runtime_profile:
agent_ui_host:
execution_host:
repo_host:
validation_host:
git_host:
connection_method:
ssh_target:
canonical_repo_path:
validation_required_on:
git_operations_allowed_on:
```

For `local_process_workspace`, validation and git operations are authoritative on `workspace-host`.

Runtime binding fields describe topology. They do not grant OS permissions, file-write authority, GitHub authority, audit approval, finalize approval, or Owner approval by themselves.

## Heartbeat and Renewal

A running agent should refresh `heartbeat_at` before `lease_expires_at`.

Recommended fields:

```yaml
lease_duration_seconds:
lease_started_at:
lease_expires_at:
heartbeat_at:
renewal_count:
renewal_policy:
```

Suggested default policy:

```yaml
lease_duration_seconds: 7200
renewal_policy: same_agent_instance_only
```

Renewal is allowed when:

- the same `agent_instance` owns the claim and active session
- the task is still claimed by that actor
- no conflicting active session exists
- Owner decision gates are not pending
- the runtime binding still matches the active execution environment

Renewal must not silently widen scope, change artifact scope, change memory scope, change model tier authority, or change execution host.

## Expiration and Recovery

If a lease expires, execution authority is no longer assumed active.

Recovery options are policy decisions, not automatic runtime behavior in AIPOS-50:

- same agent resumes after preview and stale-context check
- task moves to blocked for Owner review
- task is abandoned and later reopened
- task is handed off to another explicit agent
- a new claim/session pair is created after the old lease is closed

AIPOS-50 does not implement automatic recovery, task movement, or record repair.

## Handoff Rule

Handoff requires explicit metadata:

```yaml
handoff_to:
handoff_from:
handoff_reason:
parent_session_id:
```

A handoff must preserve or explicitly change artifact scope, memory scope, context bundle, output target, model tier, and runtime binding. Scope or authority changes require Owner approval when they affect risk, credentials, external systems, model tier, audit boundary, or workflow mode.

## Abandonment Rule

A session may be abandoned when the operator or agent stops before completion.

Abandonment should record:

```yaml
lease_status: abandoned
session_status: abandoned
session_ended_at:
abandoned_by:
abandon_reason:
last_session_id:
```

Abandonment does not automatically return the task to pending or reassign it. Future queue mutation policy must decide any task-state transition.

## Needs Owner Triggers

A session binding should route to Owner when:

- two active sessions claim the same task
- `active_session_id`, claim metadata, or record files conflict
- runtime binding host differs from the runtime profile
- lease expired and resume context is stale or ambiguous
- handoff target is unclear
- recovery would change scope, model tier, credentials, audit boundary, workflow mode, or execution host
- an agent requests audit/finalize bypass

## Relationship To Records

Task frontmatter stores the latest operational summary. Session records and claim logs store durable history.

Recommended task frontmatter fields:

```yaml
active_session_id:
last_session_id:
claim_id:
lease_status:
lease_started_at:
lease_expires_at:
heartbeat_at:
claimed_runtime_profile:
execution_host:
validation_host:
git_host:
```

Recommended session record fields:

```yaml
lease_status:
lease_started_at:
lease_expires_at:
heartbeat_at:
lease_duration_seconds:
renewal_count:
renewal_policy:
runtime_profile:
execution_host:
repo_host:
validation_host:
git_host:
canonical_repo_path:
```

These fields are protocol targets. AIPOS-50 does not require existing task cards or record files to be rewritten.

## Relationship To AIPOS-48 and AIPOS-49

AIPOS-48 defines task matching and atomic claim. AIPOS-49 defines the mixed-host workspace workflow and mixed-host runtime profile vocabulary. AIPOS-50 defines how a claimed task binds to a concrete running execution session through a lease and runtime binding.

## Non-Goals

AIPOS-50 does not implement:

- lease writer
- heartbeat daemon
- scheduler
- queue polling runtime
- automatic recovery
- task movement
- CLI command changes
- Web UI behavior changes
- records writer changes
- database or server
