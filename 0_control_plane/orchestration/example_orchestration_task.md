# Example Orchestration Task

## Purpose

This is a fictional safe example showing how planner-orchestrator metadata can be represented.

It is not a live task to execute and should not be copied into `5_tasks/queue/` without Owner review.

## Scenario

Build a read-only Board UI data contract.

## Normal Task Without Planner

Normal tasks do not require planner fields. Missing orchestration is equivalent to `orchestration.enabled == false`.

```yaml
---
task_id: EXAMPLE-NORMAL-001
title: Update README wording
project: ai-project-os
task_type: one_shot
assigned_to: dev.codex.local
agent_instance: dev.codex.local
reviewer: dev_claude
audit_by: dev_claude
context_bundle: dev.codex.local
task_mode: docs
model_tier: L2
priority: medium
status: pending
created_by: owner
needs_owner: false
output_target: tools/aipos_cli/README.md
artifact_policy: formal_write
---
```

## Parent Orchestration Task

```yaml
---
task_id: EXAMPLE-ORCH-001
title: Read-only Board UI Data Contract
project: ai-project-os
task_type: one_shot
assigned_to: planner.claude.l3.local
agent_instance: planner.claude.l3.local
reviewer: dev_claude
audit_by: dev_claude
context_bundle: dev.codex.local
task_mode: design
model_tier: L3
priority: high
status: pending
created_by: owner
needs_owner: false
output_target: 0_control_plane/board/
artifact_policy: formal_write
session_policy: single_task_session
context_isolation: strict
artifact_scope: 0_control_plane/board/, tools/aipos_cli/
memory_scope: board ui data contract

orchestration_parent: true
orchestration_status: planning
orchestration_state_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/orchestration_state.md
planner_loop_state_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/loop_state.md
subtask_index_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/subtask_index.md
planner_iterations_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/planner_iterations.md
orchestration_events_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/orchestration_events.md
artifact_links_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/artifact_links.md

orchestration:
  enabled: true
  planner_required: true
  orchestration_id: orch_ai_project_os_20260428_board_ui
  planner_agent: planner.claude.l3.local
  planner_agent_instance: planner.claude.l3.local
  planner_runtime_profile: claude_code
  planner_model_tier: L3
  planner_assignment_scope: parent_task_only
  planner_assignment_status: active
  planner_assignment_started_at: 2026-04-28T10:00:00Z
  planner_assignment_ends_at:
  planner_permissions:
    can_create_subtasks: true
    can_recommend_handoff: true
    can_mark_needs_owner: true
    can_finalize: false
    can_self_audit: false
  max_iterations: 8
  max_open_subtasks: 2
  max_subtasks_total: 10
  polling_cadence:
    mode: after_report
    interval: 30m
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
  owner_approval_required_for:
    - high_risk_architecture_change
    - paid_resource
    - model_tier_escalation
    - quota_exhausted_without_fallback
  failure_policy: default_orchestration_failure_policy
  quota_policy: default_runtime_budget_policy
---
```

## Runtime Budget Policy Example

```yaml
runtime_budget_policy:
  enabled: true
  source: config
  status_provider_ref: manual_runtime_status
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

## Orchestration State Example

```yaml
---
orchestration_id: orch_ai_project_os_20260428_board_ui
parent_task_id: EXAMPLE-ORCH-001
title: Read-only Board UI Data Contract
project: ai-project-os
status: running
planner_agent: planner.claude.l3.local
planner_agent_instance: planner.claude.l3.local
planner_runtime_profile: claude_code
planner_model_tier: L3
planner_assignment_status: active
planner_assignment_scope: parent_task_only
current_iteration: 1
max_iterations: 8
max_open_subtasks: 2
max_subtasks_total: 10
open_subtask_count: 1
completed_subtask_count: 0
blocked_subtask_count: 0
failed_subtask_count: 0
needs_owner: false
needs_owner_reasons: []
stop_condition_hits: []
pause_reason:
resume_requirements: []
runtime_budget_policy_ref: default_runtime_budget_policy
runtime_status_provider_refs:
  - manual_runtime_status
subtask_index_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/subtask_index.md
planner_iterations_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/planner_iterations.md
orchestration_events_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/orchestration_events.md
artifact_links_ref: 5_tasks/orchestration/orch_ai_project_os_20260428_board_ui/artifact_links.md
created_by: owner
created_at: 2026-04-28T10:00:00Z
updated_at: 2026-04-28T10:30:00Z
last_planner_run_at: 2026-04-28T10:30:00Z
next_planner_check_after: 2026-04-28T11:00:00Z
---
```

## Subtask Index Example

```yaml
- subtask_id: EXAMPLE-ORCH-001-S01
  task_id: EXAMPLE-ORCH-001-S01
  task_path: 5_tasks/queue/pending/example_orch_001_s01.md
  title: Draft Board UI Data Contract
  subtask_type: docs
  assigned_to: dev.codex.local
  agent_instance: dev.codex.local
  reviewer: dev_claude
  audit_by: dev_claude
  status: pending
  queue_state: pending
  created_by_planner: true
  planner_agent: planner.claude.l3.local
  iteration: 1
  subtask_sequence: 1
  depends_on: []
  blocks:
    - EXAMPLE-ORCH-001-S02
  artifact_links: []
  report_refs: []
  needs_owner: false
  last_updated_at: 2026-04-28T10:30:00Z
```

## Planner Iteration Log Example

```yaml
- iteration_id: iter_orch_ai_project_os_20260428_board_ui_001
  orchestration_id: orch_ai_project_os_20260428_board_ui
  iteration_number: 1
  planner_agent: planner.claude.l3.local
  planner_model_tier: L3
  started_at: 2026-04-28T10:00:00Z
  ended_at: 2026-04-28T10:30:00Z
  input_refs:
    - EXAMPLE-ORCH-001
  observed_queue_state: pending
  observed_subtask_summary: 0 completed / 0 blocked / 0 failed
  decisions:
    - Create initial docs subtask and audit subtask.
  created_subtasks:
    - EXAMPLE-ORCH-001-S01
    - EXAMPLE-ORCH-001-S02
  updated_recommendations: []
  failure_observations: []
  quota_observations: []
  needs_owner_reasons: []
  next_check_after: 2026-04-28T11:00:00Z
  verdict: continue
```

## Orchestration Event Log Example

```yaml
- event_id: evt_orch_ai_project_os_20260428_board_ui_001
  orchestration_id: orch_ai_project_os_20260428_board_ui
  event_type: orchestration_created
  timestamp: 2026-04-28T10:00:00Z
  actor: owner
  source: parent_task
  related_task_id: EXAMPLE-ORCH-001
  related_subtask_id:
  related_iteration_id:
  severity: info
  summary: Parent orchestration task created.
  details: Initial read-only Board UI contract orchestration opened.
  refs:
    - EXAMPLE-ORCH-001
```

## Artifact Links Example

```yaml
artifact_links:
  - artifact_id: art_board_contract_v1
    label: Draft board data contract
    url_or_path: 0_control_plane/board/board_data_contract_v1.md
    artifact_type: design_doc
    related_task_id: EXAMPLE-ORCH-001-S01
    related_iteration_id: iter_orch_ai_project_os_20260428_board_ui_001
    permanence: durable
    promoted_to_memory: false
```

## Runtime Status Provider Example

```yaml
runtime_status_provider:
  provider_id: manual_runtime_status
  provider_type: manual
  target_runtime_profiles:
    - codex_cli
    - claude_command
    - claude_code
    - cc
    - cc_glm
  polling_allowed: false
  polling_cadence:
  auth_required: false
  data_fields:
    - availability_status
    - quota_remaining
    - quota_reset_at
    - rate_limit_state
    - login_state
    - network_state
  last_checked_at:
  confidence: manual
```

Future service_dashboard or api providers are extension points only. AIPOS-25 does not implement quota scraping, website login, API calls, or scheduled polling.

## Coder Subtask Example

This subtask references the parent planner but does not have its own independent planner assignment.

```yaml
---
task_id: EXAMPLE-ORCH-001-S01
title: Draft Board UI Data Contract
project: ai-project-os
task_type: one_shot
assigned_to: dev.codex.local
agent_instance: dev.codex.local
reviewer: dev_claude
audit_by: dev_claude
context_bundle: dev.codex.local
task_mode: design
model_tier: L2
priority: high
status: pending
created_by: planner.claude.l3.local
needs_owner: false
output_target: 0_control_plane/board/
artifact_policy: formal_write
session_policy: single_task_session
context_isolation: strict
artifact_scope: 0_control_plane/board/
memory_scope: board ui data contract
orchestration_id: orch_ai_project_os_20260428_board_ui
parent_task_id: EXAMPLE-ORCH-001
created_by_planner: true
planner_agent: planner.claude.l3.local
iteration: 1
subtask_sequence: 1
subtask_type: docs
depends_on: []
---
```

## Planner Assignment Lifecycle Example

```yaml
planner_assignment_lifecycle:
  current_status: active
  transitions:
    - proposed -> active
    - active -> paused
    - paused -> active
    - active -> completed
    - active -> cancelled
    - active -> superseded
  termination_conditions:
    - parent task completed
    - parent task cancelled
    - Owner replaces planner
    - planner unavailable and handoff approved
    - max_iterations reached
    - stop condition hit
```

## Reviewer Subtask Example

```yaml
---
task_id: EXAMPLE-ORCH-001-S02
title: Audit Board UI Data Contract
assigned_to: dev_claude
agent_instance: dev.claude.cc.local
task_mode: code_reviewer
model_tier: L3
status: pending
orchestration_id: orch_ai_project_os_20260428_board_ui
parent_task_id: EXAMPLE-ORCH-001
created_by_planner: true
planner_agent: planner.claude.l3.local
iteration: 1
subtask_sequence: 2
subtask_type: audit
depends_on:
  - EXAMPLE-ORCH-001-S01
review_target_task_id: EXAMPLE-ORCH-001-S01
---
```

## Failure Handling Example

```yaml
failure_state:
  active_failures:
    - class: quota_exhausted
      runtime_profile: codex_cli
      quota_id: codex_5h
      severity: needs_owner
      action: pause_orchestration
      message: Codex 5-hour quota exhausted and no fallback executor configured.
  needs_owner_reasons:
    - quota_exhausted_without_fallback
```

## Pause Condition Example

```yaml
orchestration_status: paused
pause_reason: reviewer_unavailable
resume_requirements:
  - review completed
  - Owner approval if reviewer changes
```

## Handoff Example

```yaml
handoff_recommendation:
  reason: quota_exhausted
  from_executor: dev.codex.local
  to_executor: dev.claude.cc_glm.local
  required_action: create_explicit_task_update
  preserve_context_isolation: true
  owner_approval_required: true
```
