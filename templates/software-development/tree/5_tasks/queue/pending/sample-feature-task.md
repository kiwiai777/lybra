---
task_id: SAMPLE-FEATURE-001
title: Replace sample feature task
project: {{ project_id }}
assigned_to: generic_worker
agent_instance: generic_worker
context_bundle: generic_worker
task_mode: code
model_tier: L2
priority: medium
status: pending
created_by: workspace_template
needs_owner: false
source_tag: {{ source_tag }}
client_tag: {{ client_id }}
external_ref: {{ external_ref }}
output_target: repository
artifact_policy: formal_write
session_policy: single_task_session
context_isolation: strict
artifact_scope: repository
memory_scope: {{ project_id }}
---

## Goal

Replace this sample with the first real implementation task.

## Audit Handoff

After implementation, prepare an independent audit handoff with changed files, validation commands, and known risks.
