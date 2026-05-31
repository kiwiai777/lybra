# Task Claim Protocol

## Purpose

This protocol defines how one eligible agent instance safely claims a pending task when multiple local or remote instances may be polling at the same time.

AIPOS-48 decides who may claim a task and how one claim wins.

AIPOS-50 decides how a claimed task binds to a running execution session through a session lease and runtime binding.

This document does not implement polling, scheduling, queue daemon behavior, web UI behavior, CLI behavior changes, controlled execute behavior, records writer behavior, or session lease semantics.

## Claim Lifecycle

```text
discover -> evaluate -> attempt_claim -> claimed | claim_lost | blocked | expired
```

Meanings:

- `discover`: an agent sees a pending task through an allowed polling or listing mechanism.
- `evaluate`: the agent applies `task_matching_policy.md`.
- `attempt_claim`: the agent attempts the atomic state transition.
- `claimed`: the agent won the transition and owns the task.
- `claim_lost`: another claimant won first, or the task is no longer pending.
- `blocked`: policy or metadata prevents claim.
- `expired`: the task is past `expires_at` and is not claimable.

## Minimum Safe Claim Flow

1. Agent reads a pending task.
2. Agent evaluates hard match rules.
3. Agent confirms availability and model tier suitability.
4. Agent attempts atomic claim.
5. If claim succeeds, task moves `pending -> claimed`.
6. Runtime metadata is written.
7. Claim event is recorded when records are enabled.
8. If claim fails, agent treats the task as unavailable and continues polling later.

## Supported Claim Policies

### assigned_agent_only

Only instances matching `assigned_to`, the logical agent, or configured aliases may claim.

### specific_instance_only

Only the exact canonical opaque instance named by `agent_instance` or an Owner override may claim.

Historical IDs may resolve through explicit one-to-one `legacy_instance_ids` mappings before exact canonical comparison. Ambiguous mappings must block. Claim enforcement must not parse identifier strings or infer role, runtime, vendor, harness, model family, host, authority, or independence from names.

### first_matching_instance

Any instance that passes hard match rules may attempt an atomic claim. The first successful atomic transition wins.

### owner_manual_assign

Only the Owner-selected actor or instance may claim. The assignment must be explicit in task metadata or future Owner override metadata.

## Future Claim Policies

These policies are future work only and are not required or implemented by AIPOS-48:

- `best_matching_instance`
- `planner_assigned_pool`
- `round_robin`
- `quota_aware_matching`

## Atomic Claim Rule

A claim is not successful unless the task state transition succeeds atomically.

For file-driven AIPOS, the first implementation target remains:

```text
5_tasks/queue/pending/{task}.md
-> 5_tasks/queue/claimed/{task}.md
```

Only one claimant may win.

Concurrent attempts must result in:

```text
one claimed
others claim_lost or no-op
```

An agent must not treat an in-memory decision, preview response, UI selection, or partial metadata write as a successful claim.

## Claim Preconditions

Before attempting claim, the agent must verify:

- task state is `pending`
- task is not expired
- claim policy allows this actor and instance
- hard match rules pass
- availability requirements pass
- model tier requirements pass
- required context bundle and write-scope policy pass
- Owner manual assignment is present when required

## Claim Metadata

On successful claim, runtime metadata should include:

```yaml
claim_id:
claimed_by:
claimed_agent_instance:
claimed_runtime_profile:
claimed_at:
claim_policy:
claim_match_basis:
claim_requirements_hash:
active_session_id:
session_policy:
context_isolation:
```

`active_session_id` is reserved for AIPOS-50 session lease and runtime binding semantics. AIPOS-48 may record or reserve the field but does not define full Task Session lease behavior.

`claim_match_basis` should summarize why the instance was allowed to claim, such as:

```yaml
claim_match_basis:
  role_match: assigned_to
  instance_match: preferred
  model_tier_match: exact
  capability_match: all_required
  availability_match: idle
```

`claim_requirements_hash` may identify the requirements snapshot evaluated at claim time. The hash is a protocol field and does not require a new hashing implementation in AIPOS-48.

## Claim Event Schema

When records writing is enabled by existing behavior, claim events should follow `5_tasks/claim_event_schema.md`.

Claim event writing may remain optional or future depending on current records writer capability. AIPOS-48 must not widen records writing behavior.

## Claim Failure Handling

When claim fails, the agent must treat the task as unavailable for that attempt.

Common failure outcomes:

- `claim_lost`: another agent claimed first
- `claim_rejected`: policy, match, or availability no longer passes
- `claim_expired`: `expires_at` passed before claim
- `no-op`: task was already moved or no longer visible

The losing agent must not repair, overwrite, or move the task unless a separate approved policy allows that operation.

## Claimed Task Behavior

Claimed tasks are not claimable by another instance.

Blocked, completed, and expired tasks are not claimable.

Reopened tasks may become pending again under the existing reopen policy and may then be matched and claimed again with a new claim event.

## Planner Compatibility

Planner-created subtasks use this same claim protocol.

Planner recommendations for `assigned_to`, `agent_instance`, `requirements`, `claim_policy`, `model_tier`, `reviewer`, or `audit_by` do not bypass matching, claim, review, audit, or Owner approval policy.

## Boundary With AIPOS-50

AIPOS-48 ends when a task is safely claimed by one eligible instance.

AIPOS-49 defines the mixed-host workspace workflow profile and mixed-host runtime profile vocabulary. AIPOS-50 begins when the system defines how a claimed task binds to a running execution session, lease duration, heartbeat, renewal, abandonment, handoff, and recovery behavior.
