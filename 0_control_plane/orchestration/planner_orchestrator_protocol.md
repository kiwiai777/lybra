# Planner-Orchestrator Task Loop Protocol

## Purpose

This protocol defines a bounded planner-orchestrator loop for AI Project OS.

The planner is a planning and orchestration agent that creates, updates, and evaluates task plans. It coordinates work through task cards, queue state, reports, records, and owner decisions. It is not an unconstrained autonomous runtime.

AIPOS-25 defines protocol and schema only. It does not implement an automatic planner loop, scheduler, task writer CLI, runtime launcher, service website polling, quota scraping, database, web UI, or agent execution.

## Direct Owner Requirement Intake

Owner may create a parent requirement directly from a fuzzy goal without using ChatGPT or Claude.ai as routine relay. AIPOS-51 defines this intake path in `parent_requirement_intake_policy.md`.

Direct intake creates a forum-visible parent requirement first. It does not immediately create coding subtasks or execute work. The planner assignment must be task-scoped, visible, and bound to L3/L4 for planning decisions.

Recommended parent intake fields include:

```yaml
requirement_id:
owner_goal:
forum_thread_ref:
planning_required: true
min_planner_model_tier: L3
allowed_planner_agents:
  - dev_codex
  - dev_claude
assigned_planner:
planner_assignment_status: proposed
```

## Task-Scoped Optional Planner Role

Planner is task-scoped and optional.

Normal tasks do not require `planner_agent`, `planner_model_tier`, or planner assignment fields. A missing orchestration block is equivalent to `orchestration.enabled == false`.

Planner assignment is tied to a specific parent orchestration task or orchestration group. It does not create permanent system-wide planning authority, and the planner does not become a permanent role by being assigned once.

Planner authority ends when the parent orchestration task is completed, cancelled, superseded, or when Owner replaces the planner through an explicit handoff.

For complex-class parent requirements, AIPOS-64 adds a continuity rule: once a concrete L3/L4 planner instance reaches `planner_assignment_status: active`, that planner remains the continuity planner for the parent requirement until completion, cancellation, supersession, or Owner-approved handoff. This keeps the planning thread stable across planner ticks, subtask draft batches, audit cycles, repairs, and finalize recommendations.

Planner continuity is parent-level only. It does not pin coder, reviewer, or auditor assignment on individual subtasks. Subtask execution and review remain governed by AIPOS-48 task matching, AIPOS-50 session lease binding, role policy, review separation, audit separation, and Owner gates.

Planner fields are required only when orchestration policy requires planner involvement:

```yaml
orchestration:
  enabled: false
  planner_required: false
  planner_agent:
  planner_model_tier:
```

Interpretation:

- `orchestration.enabled` missing or false: planner not required.
- `orchestration.enabled: true` and `planner_required: true`: `planner_agent` and `planner_model_tier` are required.
- `orchestration.enabled: true` and `planner_required: false`: planner fields are optional.
- `orchestration.enabled: true` with automated or planner-driven subtask creation: `planner_agent` is required.

Planner permissions are protocol permissions, not OS, GitHub, shell, or service-account permissions. `planner_permissions` does not allow runtime execution or file writes by itself.

Recommended task-scoped planner fields:

```yaml
planner_agent:
planner_agent_instance:
planner_runtime_profile:
planner_model_tier:
planner_assignment_scope: parent_task_only
planner_assignment_status: proposed
planner_assignment_started_at:
planner_assignment_ends_at:
planner_continuity_policy: sticky_until_parent_complete
continuity_planner_agent:
continuity_planner_agent_instance:
planner_handoff_policy: owner_approved_only
planner_permissions:
  can_create_subtasks: true
  can_recommend_handoff: true
  can_mark_needs_owner: true
  can_finalize: false
  can_self_audit: false
```

Allowed `planner_assignment_scope` values:

```text
parent_task_only
orchestration_group
```

Allowed `planner_assignment_status` values:

```text
proposed
active
paused
completed
cancelled
superseded
```

Lifecycle transitions:

```text
proposed -> active
active -> paused
paused -> active
active -> completed
active -> cancelled
active -> superseded
```

Termination conditions:

- parent task completed
- parent task cancelled
- Owner replaces planner
- planner unavailable and handoff approved
- max_iterations reached
- stop condition hit

Future validator and preview rules should use this gating:

- normal tasks missing planner_agent: no warning
- `orchestration.enabled` false and missing planner_agent: no warning
- `orchestration.enabled` true plus `planner_required` true plus missing planner_agent: BLOCK or NEEDS_OWNER depending policy
- planner_model_tier below L3 for planning decisions: NEEDS_OWNER or BLOCK
- planner_agent same as audit_by for planner-created tasks: NEEDS_OWNER
- planner_assignment_status completed or cancelled should prevent new planner-created subtasks
- complex-class parent requirement with active planner assignment but missing continuity planner fields: NEEDS_OWNER or BLOCK depending policy
- active planner identity changes without Owner-approved handoff metadata: NEEDS_OWNER or BLOCK

## Planner Role

The planner may:

- read AIPOS docs, queue state, reports, inbox summaries, shared memory, and records
- create proposed subtask cards
- recommend assignment, task_mode, task_class, complexity_note, model_tier, reviewer, and audit_by
- recommend stop, pause, handoff, repair, or needs_owner escalation
- summarize progress and open risks
- evaluate whether planned subtasks satisfy the parent task

The planner must not:

- write production code directly
- audit its own tasks
- bypass reviewer or audit_by
- auto-push, auto-merge, or finalize without audit
- execute runtime_command, runtime_args, external runtimes, or subprocess launchers
- modify task reports or silently delete or overwrite agent reports
- create infinite tasks or continue past configured limits
- override Owner decisions
- treat quota status scraping or provider polling as implemented behavior

In short, planner cannot become a hidden executor, hidden reviewer, or hidden runtime scheduler.

Planner-created recommendations become actionable only when represented as explicit task cards, loop state, reports, or Owner-approved updates.

## Relationship To Worker Role Catalog

AIPOS-97 defines a Worker role catalog for vendor-neutral functional templates.

Planner is not a Worker catalog template. Planner is the fixed coordinator contract for orchestration. This preserves the protocol separation between the actor that decomposes and routes work and the worker templates that may execute, review, audit, report, research, design, or support scoped tasks.

The Worker catalog may include scoped planning templates such as roadmap, sprint, release, or dependency planning, but those templates do not replace the coordinator Planner contract and do not gain planner-orchestrator authority by name alone.

## Combined Planner/Executor Mode

AIPOS-53 allows the same concrete agent instance to act as planner and executor when the task card, capability profile, runtime profile, and Owner policy allow it.

Combined planner/executor mode is execution authority only. It does not permit the agent to audit its own work, act as Owner decider, skip AIPOS-52 draft/publish policy, bypass AIPOS-48 task matching and claim, bypass AIPOS-50 session lease binding, or finalize before independent audit PASS when audit is required.

The combined agent must pause for Owner decision on architecture, scope, risk, authority, workflow, audit-boundary, model-tier, implementation-boundary, external-service, paid-resource, credential, or irreversible-action forks.

This mode is not limited to coding tasks. Planner/executor may be combined for documentation, research, operations, content, sales support, or other task modes when the selected role instance supports the task mode.

## Planner Model Tier

Core planning decisions require:

```yaml
planner_model_tier: L3
```

Allowed values for planning decisions are `L3` or `L4` when L4 exists in the local model registry.

Lower tiers may be used only for:

- formatting
- summarization
- non-decision rendering
- mechanical conversion of an already approved plan

Any strategic planning, decomposition, assignment, risk handling, stop condition evaluation, handoff, or needs_owner decision requires L3/L4.

## Agent Trio Pattern

Standard orchestration uses:

```yaml
planner_agent: planner.claude.l3.local
coder_agent: dev.codex.local
reviewer_agent: dev_claude
```

Rules:

- planner_agent, coder_agent, and reviewer_agent are explicit fields on orchestration work.
- planner cannot be the same logical agent as reviewer for its own plan.
- coder and reviewer should be separate logical agents when possible.
- planner-created complex-class tasks must declare reviewer and audit_by.
- planner-created complex-class tasks require audit before finalize.
- reviewer must not be silently replaced by planner.
- Owner may approve exceptions, but the exception must be visible in the task or loop state.

## Loop Overview

The controlled loop is:

```text
Owner creates parent requirement directly in AIPOS
AIPOS records forum-visible parent requirement
L3 planner is assigned
Planner reads AIPOS state and parent task
Planner creates bounded execution plan
Planner emits proposed subtask drafts
Coder executes subtask
Reviewer audits subtask
Planner polls queue/reports/records/needs-owner state
Planner decides next subtask, pause, repair, handoff, or escalation
Loop stops when done, blocked, paused, failed, cancelled, or Owner decision is required
```

Planner polling is advisory protocol in AIPOS-25. No scheduler or runtime loop is implemented.

When AIPOS-53 combined planner/executor mode is selected, the planner and executor steps may be performed by the same execution-authority agent instance. Reviewer/auditor separation and Owner decision gates remain unchanged.

## Minimal Planner Loop MVP

AIPOS-54 defines the first minimal planner loop as a manual, visible planner tick:

```text
observe -> decide -> emit -> wait
```

The tick reads the parent requirement, visible forum/control-plane context, queue state, drafts, audits, records, Needs Owner items, configured limits, and stop conditions. It then emits one primary verdict: `continue`, `draft_subtasks`, `publish_ready`, `wait_for_audit`, `repair`, `needs_owner`, `blocked`, `complete`, `cancel`, or `failed`.

AIPOS-54 does not implement autonomous queue polling, scheduling, file writing, task movement, CLI commands, Web UI behavior, records writing, orchestration writing, or runtime launch. It defines the smallest auditable loop contract that a human or future approved tool may run.

When a planner tick hits an Owner decision gate, it must emit `needs_owner` and stop creating new subtasks until Owner decision is recorded.

## Planner Autonomy Tier

AIPOS-94 defines optional Planner Autonomy Tier metadata.

The default autonomy tier is:

```yaml
planner_autonomy:
  autonomy_tier: A0
  scope: tick
  approved_by_owner: false
```

A0 matches the current AIPOS-54 behavior: every planner tick is explicit, visible, and human-invoked.

Higher tiers are protocol-only future confirmation cadences:

```text
A1: subtask-local self-iteration, Owner confirms between subtasks
A2: approved subtask DAG self-run, Owner confirms between DAGs
A3: orchestration-local experimentation, Owner confirms between orchestrations
A4: fully autonomous, reserved and forbidden
```

Autonomy tier changes confirmation cadence only. It does not grant write authority, draft publish authority, queue claim authority, runtime launch authority, controlled execute authority, reviewer authority, auditor authority, Owner decision authority, credential access, git commit/push authority, or finalize authority.

Tier upgrade is always an Owner Decision Gate. Missing, ambiguous, or unapproved autonomy policy falls back to A0.

Any tier must downgrade to A0 when failure thresholds, critical forks, Owner gates, audit ambiguity, credential ambiguity, controlled execute failure, dependency ambiguity, or external publish/commit/push/finalize requests appear.

## Required Limits

Every orchestration must define:

```yaml
max_iterations:
max_open_subtasks:
max_subtasks_total:
polling_cadence:
stop_conditions:
```

Planner must not run indefinitely. Missing limits are schema issues and should trigger needs_owner before execution.

## Polling Cadence

`polling_cadence` describes how often a future orchestrator may check state.

Recommended values:

```yaml
polling_cadence:
  mode: manual | interval | after_report
  interval:
  jitter:
  owner_review_required: false
```

Current AIPOS does not schedule these checks. A human or future tool may use the cadence as advisory configuration.

## Naming Convention

Use stable IDs:

```text
orchestration_id: orch_<project>_<YYYYMMDD>_<slug>
subtask_id: <parent_task_id>-S<NN>
planner_iteration_id: iter_<orchestration_id>_<NN>
```

Example:

```text
orch_ai_project_os_20260428_board_ui
AIPOS-25-S01
iter_orch_ai_project_os_20260428_board_ui_001
```

## Status Values

Allowed orchestration statuses:

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

## Stop Conditions

Required stop conditions:

```yaml
stop_conditions:
  - parent_task_completed
  - max_iterations_reached
  - max_subtasks_total_reached
  - repeated_failure_threshold_reached
  - owner_decision_required
  - high_risk_change_detected
  - quota_exhausted_without_fallback
  - runtime_status_unknown_beyond_threshold
  - reviewer_unavailable
```

When any stop condition is hit, planner must stop creating new subtasks until the configured action or Owner decision is recorded.

## Runtime Budget and Quota Policy

Runtime quota and budget behavior must be declarative, configurable, and user-editable. Planner logic must not hard-code provider limits.

Recommended policy shape:

```yaml
runtime_budget_policy:
  enabled: true
  source: manual | config | future_status_provider
  status_provider_ref:
  quota_windows:
    - quota_id: codex_5h
      runtime_profiles: [codex_cli]
      window_type: rolling
      window_duration: 5h
      sensitivity: high
      on_warning: reduce_scope
      on_exhausted: pause_orchestration
    - quota_id: codex_weekly
      runtime_profiles: [codex_cli]
      window_type: weekly
      sensitivity: high
      on_warning: reduce_scope
      on_exhausted: pause_orchestration
    - quota_id: claude_code_5h
      runtime_profiles: [claude_command, claude_code]
      window_type: rolling
      window_duration: 5h
      sensitivity: high
      on_warning: reduce_scope
      on_exhausted: pause_orchestration
    - quota_id: claude_code_weekly
      runtime_profiles: [claude_command, claude_code]
      window_type: weekly
      sensitivity: high
      on_warning: reduce_scope
      on_exhausted: pause_orchestration
    - quota_id: cc_proxy_api
      runtime_profiles: [cc]
      source: proxy_api
      sensitivity: medium
      on_warning: continue_with_warning
      on_exhausted: handoff_or_pause
    - quota_id: cc_glm_proxy_api
      runtime_profiles: [cc_glm]
      source: proxy_api
      sensitivity: medium
      on_warning: continue_with_warning
      on_exhausted: handoff_or_pause
```

Generic custom quota rules are allowed:

```yaml
custom_quota_rules:
  - quota_id:
    applies_to:
    provider:
    source:
    window_type:
    window_duration:
    sensitivity:
    warning_threshold:
    exhausted_threshold:
    status_fetch_mode: manual | file | api | service_dashboard | future_tool
    status_ref:
    on_warning:
    on_exhausted:
```

Rules:

- Codex CLI quotas must be configurable, including 5-hour and weekly windows.
- Direct claude command / Claude Code quotas must be configurable, including 5-hour and weekly windows.
- cc and cc_glm proxy/API quota must be configurable and represented even when lower sensitivity.
- quota_exhausted must not be treated as task failure by default.
- quota_exhausted should pause orchestration, request Owner decision, reduce scope, or handoff according to config.
- Planner may recommend handoff but must preserve task context isolation.
- Future status fetching from service websites or provider dashboards is an extension point only.
- AIPOS-25 does not implement quota scraping, scheduled polling, website login, or API calls.

## Runtime Status Provider Extension Point

Future runtime status providers may use:

```yaml
runtime_status_provider:
  provider_id:
  provider_type: manual | local_file | api | service_dashboard | future_tool
  target_runtime_profiles:
  polling_allowed: false
  polling_cadence:
  auth_required:
  data_fields:
    - availability_status
    - quota_remaining
    - quota_reset_at
    - rate_limit_state
    - login_state
    - network_state
  last_checked_at:
  confidence: manual | estimated | provider_reported
```

Current AIPOS does not fetch service websites. Future tools may periodically fetch status from provider/service websites only if Owner approves a dedicated implementation task. Missing provider data should produce `unknown`, not crash.

## Owner Escalation

Planner must set or request needs_owner when any of these occur:

- high-risk architecture decision
- security-sensitive code
- permission or credential issue
- ambiguous requirement
- quota_exhausted without fallback
- runtime status unknown beyond configured threshold
- repeated failure
- reviewer conflict
- executor conflict
- model tier escalation beyond allowed tier
- external service or paid resource required
- data loss risk

## Audit and Finalize Boundaries

Coding tasks need audit before finalize. Repair tasks need re-audit. Planner may recommend finalize but cannot self-approve or bypass audit.

Finalize remains a separate boundary that includes:

- git status check
- forbidden path check
- validation commands
- commit
- push
- completion report

Planner cannot self-approve its own plan, reviewer output, or finalization.
