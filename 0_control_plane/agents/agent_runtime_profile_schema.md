# Agent Runtime Profile Schema

## Purpose

This schema defines how AI Project OS models one logical agent identity and multiple concrete runtime variants.

Runtime configuration is declarative only.

AIPOS CLI must not execute `runtime_command` or `runtime_args` in AIPOS-21.

Secrets must not be stored in `runtime_env`.

## Availability Status

Add and define:

- `availability_status`

Allowed values:

- `online`
- `offline`
- `busy`
- `maintenance`
- `unknown`

Status semantics:

- `online` = known available
- `offline` = known not currently available
- `busy` = available but currently occupied
- `maintenance` = intentionally unavailable / under maintenance
- `unknown` = no reliable availability signal

Rules:

- `availability_status` is future user-editable operational state
- `availability_status` is distinct from `enabled`
- `availability_status` is visibility/warning only in AIPOS-22
- `availability_status` does not block alias matching
- `availability_status` does not hide tasks
- `availability_status` does not execute or stop agents

## Core Meanings

- `assigned_to` is the logical agent identity
- `agent_instance` is the concrete executable instance
- `agent_instance` is an opaque canonical key; its string must not be parsed for role, vendor, harness, model, host, runtime, or authority
- `legacy_instance_ids` are explicit additive compatibility references for historical IDs
- `provenance.vendor`, `provenance.harness`, `provenance.model_family`, and `provenance.host` are optional open-vocabulary free-form strings
- `runtime_profile` is the configurable profile name / UI selector name
- `runtime_entrypoint` is the entrypoint type / tool family
- `runtime_command` is the actual command string
- `runtime_args` is the configurable command-line argument list
- `runtime_env` is optional non-secret environment configuration
- `launch_notes` is human-readable launch guidance
- `model_tier` is still selected by task risk, not hard-coded by role

## Top-Level Fields

- `agent_id`
- `display_name`
- `description`
- `enabled`
- `availability_status`
- `aliases`
- `instances`
- `runtime_profiles`
- `default_instance`
- `default_runtime_profile`
- `allowed_task_modes`
- `preferred_task_modes`
- `preferred_model_tier`
- `runtime_entrypoint`
- `runtime_command`
- `runtime_args`
- `runtime_env`
- `launch_notes`
- `environment`
- `workspace`
- `notes`
- `sdk_compatibility`

## Future User-Editable Fields

- `display_name`
- `enabled`
- `availability_status`
- `aliases`
- `instances`
- `default_instance`
- `default_runtime_profile`
- `allowed_task_modes`
- `preferred_task_modes`
- `runtime_entrypoint`
- `runtime_command`
- `runtime_args`
- `runtime_env`
- `launch_notes`
- `environment`
- `workspace`
- `notes`

## Matching Semantics

- direct match always wins
- disabled profiles should not match unless direct match exists
- missing profiles should fall back to direct match
- unknown actor should still work via direct match only
- aliases are case-sensitive for now
- matching must not mutate task data
- runtime command fields must not be executed

## Recommended Shape

```yaml
agent_id:
display_name:
description:
enabled:
availability_status:
aliases:
  - logical_alias
instances:
  - agent_instance:
    legacy_instance_ids: []
    provenance:
      vendor:
      harness:
      model_family:
      host:
    runtime_profile:
    runtime_entrypoint:
    runtime_command:
    runtime_args:
      - arg
    runtime_env: {}
    launch_notes:
    default_task_modes:
      - coding
    enabled:
    availability_status:
runtime_profiles:
  - profile_name
default_instance:
default_runtime_profile:
allowed_task_modes:
  - coding
preferred_task_modes:
  - code_reviewer
preferred_model_tier:
runtime_entrypoint:
runtime_command:
runtime_args: []
runtime_env: {}
launch_notes:
environment:
workspace:
notes:
sdk_compatibility:
  adapter_ref: 0_control_plane/integrations/anthropic_sdk_compatibility_adapter_protocol.md
  sdk_environment_ref:
  sdk_session_ref:
  sdk_skill_refs: []
  sdk_vault_id_refs: []
  sdk_dependency_required: false
```

## AIPOS-95 SDK Compatibility

Runtime profiles may include optional SDK compatibility references for future adapter translation.

These references are non-secret metadata. They do not execute SDK clients, enable `anthropic_managed`, grant credentials, create environments, launch agents, or change the runtime profile source of truth.

If SDK-shaped runtime data and Lybra runtime profile data disagree, Lybra runtime profiles win.
