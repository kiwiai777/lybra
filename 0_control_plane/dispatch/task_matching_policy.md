# Task Matching Policy

## Purpose

This policy defines how a pending task is matched to eligible concrete agent instances before a claim attempt.

AIPOS-48 is protocol documentation only. It does not implement an agent polling runtime, scheduler, queue daemon, CLI behavior change, web UI behavior, or runtime session lease.

## Matching Goal

Matching answers one question:

Which currently visible agent instances are allowed and suitable to attempt claiming this pending task?

Matching does not make a task executable. A task becomes owned only when the claim transition succeeds under `0_control_plane/dispatch/task_claim_protocol.md`.

## Task-Side Inputs

Existing task fields remain valid:

```yaml
assigned_to:
agent_instance:
context_bundle:
task_mode:
model_tier:
polling_mode:
claim_policy:
output_target:
artifact_policy:
expires_at:
needs_owner:
```

Tasks may also include an optional requirements block:

```yaml
requirements:
  role:
  allowed_roles:
    - dev_claude
  preferred_agent_instance:
  runtime:
  runtime_profiles:
    - local_codex
  min_model_tier:
  task_modes:
    - coding
  capabilities:
    - repo_edit
  environment:
    local_workspace: true
  availability_required: idle
  allow_busy_instance: false
  max_concurrent_tasks_per_instance: 1
  local_only: false
  cloud_allowed: true
```

This block is backward-compatible. When a requirements field is missing, the matcher falls back to the existing top-level task field or to the referenced context bundle policy.

## Agent Profile Inputs

Matching reads declarative agent capability profiles, as defined in:

- `0_control_plane/agents/agent_capability_profile_schema.md`
- `0_control_plane/agents/agent_runtime_profile_schema.md`
- `0_control_plane/agents/model_routing_policy.md`

The matcher may consider:

- concrete `agent_instance`
- `logical_agent` and aliases
- `role`
- `runtime` and `runtime_profile`
- `availability_status`
- `model_tiers_available` and `current_model_tier`
- supported task modes
- declared capabilities
- environment
- supported context bundles
- write scopes
- max concurrent task count
- active task ids
- heartbeat freshness
- `claiming_enabled`

Capability profiles are declarations and visibility inputs. They do not grant OS, GitHub, file-write, network, or runtime permissions.

## Hard Match Rules

An agent instance may attempt a claim only when all applicable hard rules pass.

### Role Match

- `requirements.role`, when present, must match the agent role or one of its aliases.
- `requirements.allowed_roles`, when present, must contain the agent role or alias.
- When no requirements role is present, `assigned_to` must match the logical agent, role, or configured alias.

### Instance Match

- If `claim_policy: specific_instance_only`, the task `agent_instance` or `requirements.preferred_agent_instance` must exactly match the concrete agent instance.
- If `agent_instance` is present under another claim policy, it is a strong preference unless the policy explicitly makes it mandatory.

### Capability Match

- Every required capability in `requirements.capabilities` must be declared by the agent profile.
- Missing capability data is not a match for required capability.

### Runtime Profile Match

- If `requirements.runtime` is present, the agent runtime must match.
- If `requirements.runtime_profiles` is present, the agent `runtime_profile` must be included.
- Runtime profile match does not execute commands or grant permissions.

### Model Tier Match

- `requirements.min_model_tier`, when present, defines the minimum acceptable tier.
- Otherwise `model_tier` defines the required tier for the task.
- The agent must expose the requested tier in `model_tiers_available`, or a current local manual tier must be explicitly recorded as suitable.
- A higher tier may satisfy a lower minimum only when AIPOS-47 model routing and Owner policy allow it.
- A lower tier must not claim a higher-tier task.

### Availability Match

- `availability_required: idle` requires no active task and an availability state of `online` or equivalent idle state.
- `allow_busy_instance: false` blocks profiles with `availability_status: busy`.
- `max_concurrent_tasks_per_instance` must not be exceeded.
- `claiming_enabled: false` blocks claim attempts.
- Expired tasks are not claimable.

### Context Bundle Match

- If the task declares `context_bundle`, the agent profile must support it directly, support the bundle class by policy, or be manually assigned by Owner override.
- Context bundle support does not import or execute the context; it only allows a claim attempt.

### Write Target / Artifact Policy Match

- The task `output_target`, `artifact_policy`, and any requirements environment/write-scope rules must be compatible with the agent `write_scopes`.
- L1/L2/L3 write restrictions from AIPOS-47 still apply.
- Formal memory, external publication, pricing commitment, production action, destructive action, and other high-risk writes still require Owner approval where applicable.

## Soft Match Scoring Signals

When more than one instance passes hard rules, a future selector may rank by:

- exact `agent_instance` match
- preferred runtime profile
- freshest heartbeat
- idle before busy-allowed
- fewer active task ids
- lowest safe model tier
- closest context bundle support
- narrower write scope that still satisfies the task
- local instance preference for local-only tasks
- cloud persistent instance preference for recurring remote tasks
- prior successful same-family instance within the same parent orchestration, when idle and still eligible

AIPOS-48 does not implement `best_matching_instance`; scoring is advisory unless a future policy enables it.

## Forbidden Match Cases

An agent must not claim when:

- the task is not in `pending`
- the task is expired
- `claiming_enabled` is false
- the agent is disabled, offline, maintenance, or unknown where idle/online is required
- `assigned_to`, `requirements.role`, or `allowed_roles` excludes the agent
- `specific_instance_only` points to another instance
- the agent lacks a required capability, runtime profile, context bundle, model tier, or write scope
- task policy requires local-only and the agent is cloud
- task policy disallows cloud and the agent is cloud
- the task requires Owner manual assignment and no Owner assignment is present
- matching would bypass review, audit, or Owner approval policy

## Matching Modes

Supported modes:

- `assigned_agent_only`: only instances under the assigned logical agent or alias may claim.
- `specific_instance_only`: only the exact concrete instance may claim.
- `first_matching_instance`: any instance passing hard rules may attempt claim; the atomic claim decides the winner.
- `owner_manual_assign`: only an Owner-selected actor or instance may claim.

Future modes may be documented later but are not active in AIPOS-48:

- `best_matching_instance`
- `planner_assigned_pool`
- `round_robin`
- `quota_aware_matching`

## Same-Family Continuity Preference

For planner-created subtasks under the same parent requirement or orchestration, a future selector may prefer the most recent successful instance for the same role family when all hard match rules pass.

Examples:

- prior coder instance preferred for the next compatible coding subtask
- prior independent review instance preferred for the next compatible review or audit task

Reviewer and auditor are both part of the independent review family. A prior audit-capable instance may be preferred for a compatible review task, and a prior reviewer may be preferred for a compatible audit task, only when task policy allows the same family to satisfy that gate.

This is a soft scoring signal, not a claim policy. It must not override hard matching, review separation, audit separation, model tier, runtime profile, context bundle, availability, active lease state, Owner decision gates, or explicit task assignment. If the prior same-family instance is busy, unavailable, conflicted, mismatched, over budget, or not approved for the task, another eligible instance may claim.

## Model Tier Interaction

Model tier matching follows AIPOS-47:

- tier is selected by task requirements, risk, context, write target, and instance policy
- tier is not permanently fixed by role
- remote 24h agents may expose tier-bound instances
- local manual agents may record an Owner-selected tier per task
- escalation changes recommendation or handoff path; it does not mutate queue state by itself

## Planner Compatibility

Planner-created subtasks use the same matching rules as normal tasks.

A planner may recommend:

```yaml
assigned_to:
agent_instance:
requirements:
claim_policy:
model_tier:
reviewer:
audit_by:
```

Planner recommendation does not bypass matching, atomic claim, review, audit, Owner approval, or write target policy.

## Owner Overrides

Future Owner overrides should be represented explicitly, not inferred:

```yaml
owner_override:
  assigned_by:
  assigned_at:
  allowed_agent_instance:
  reason:
  expires_at:
```

An override may make a manual assignment visible to matching, but it does not grant OS permissions, external account authority, or permission to bypass high-risk approval rules.
