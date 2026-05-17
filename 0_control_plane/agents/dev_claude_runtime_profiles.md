# Dev Claude Runtime Profiles

These are configuration defaults, not locked roles.

Runtime args are editable and should not be treated as a stable API.

```yaml
agent_id: dev_claude
display_name: Dev Claude
description: Code-layer implementation and audit identity with multiple local runtime variants.
enabled: true
availability_status: online

aliases:
  - dev_claude
  - dev.claude.local
  - dev.claude.cc.local
  - dev.claude.cc_glm.local
  - dev.claude.command.local

instances:
  - agent_instance: dev.claude.cc.local
    runtime_profile: cc
    runtime_entrypoint: claude_code
    runtime_command: cc
    runtime_args: []
    runtime_env: {}
    launch_notes: Standard Claude Code entrypoint.
    default_task_modes:
      - coding
      - code_reviewer
    enabled: true
    availability_status: online

  - agent_instance: dev.claude.cc_glm.local
    runtime_profile: cc_glm
    runtime_entrypoint: claude_code
    runtime_command: cc
    runtime_args:
      - glm
    runtime_env: {}
    launch_notes: Claude Code-compatible entrypoint using GLM profile or relay. Parameters are configurable and may change.
    default_task_modes:
      - code_reviewer
      - auditor
    enabled: true
    availability_status: unknown

  - agent_instance: dev.claude.command.local
    runtime_profile: claude_command
    runtime_entrypoint: claude_cli
    runtime_command: claude
    runtime_args: []
    runtime_env: {}
    launch_notes: Direct claude command entrypoint.
    default_task_modes:
      - code_reviewer
    enabled: true
    availability_status: unknown

runtime_profiles:
  - cc
  - cc_glm
  - claude_command
default_instance: dev.claude.cc.local
default_runtime_profile: cc
allowed_task_modes:
  - coding
  - code_reviewer
  - auditor
preferred_task_modes:
  - coding
  - code_reviewer
preferred_model_tier: L2
runtime_entrypoint: claude_code
runtime_command: cc
runtime_args: []
runtime_env: {}
launch_notes: Declarative launch defaults only. The CLI never executes this configuration.
environment: local_wsl_ubuntu
workspace: shared_repo_workspace
notes: Runtime variants share a logical agent identity but not a running conversation context.
```

These defaults are editable and should not be treated as a live heartbeat.
