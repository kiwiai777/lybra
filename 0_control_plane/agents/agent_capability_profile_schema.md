# Agent Capability Profile Schema

## Purpose

This schema defines how a concrete agent instance declares what it can see, match, and claim.

Capability profiles are declarative visibility and matching inputs. They do not grant OS permissions, GitHub permissions, file-write authority, network authority, runtime execution permission, audit approval, finalize approval, or Owner approval by themselves.

## Recommended Shape

```yaml
agent_instance:
display_name:
legacy_instance_ids: []
supersedes_instance_ids: []
identity_status: active
logical_agent:
role:
runtime:
runtime_profile:
provenance:
  vendor:
  harness:
  model_family:
  host:
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
availability_status:
model_tiers_available:
  - L2
current_model_tier:
task_modes_supported:
  - coding
capabilities:
  - repo_edit
governance_authority: execution
allowed_modes:
  - planner
  - executor
  - finalize_operator
forbidden_modes:
  - independent_auditor
  - owner_decider
mode_separation:
  planner:
    governance_authority: execution
  executor:
    governance_authority: execution
  finalize_operator:
    governance_authority: execution
  auditor:
    governance_authority: audit
    required_independent: true
  owner_decider:
    governance_authority: owner
    required_human_owner: true
environment:
  location: local
  workspace:
  cloud: false
context_bundles_supported:
  - default_dev
write_scopes:
  - 2_projects/
max_concurrent_tasks:
active_task_ids:
  - AIPOS-00
heartbeat_at:
claiming_enabled:
aliases:
  - dev_claude
sdk_compatibility:
  sdk_agent_ref:
  sdk_session_ref:
  sdk_environment_ref:
  sdk_skill_refs: []
  sdk_vault_id_refs: []
  sdk_multiagent_ref:
  adapter_ref: 0_control_plane/integrations/anthropic_sdk_compatibility_adapter_protocol.md
  sdk_shape_alignment: advisory
  sdk_dependency_required: false
role_catalog:
  role_template_id:
  role_template_ref:
  template_layer: 3_context_bundles/roles/
  instance_layer: 0_control_plane/agents/agent_registry.yaml
  template_binding_status: advisory
```

## Field Definitions

- `agent_instance`: Stable Owner-defined opaque concrete-instance key, such as `agent-01`. The system must not parse semantic meaning from the string.
- `display_name`: Editable human-readable presentation metadata. It is not a unique key, claim identity, or automatic alias.
- `legacy_instance_ids`: Optional explicit historical-ID mappings used for additive compatibility. One legacy ID must not resolve ambiguously to multiple canonical IDs.
- `supersedes_instance_ids`: Optional explicit additive replacement links. Historical records remain unchanged.
- `identity_status`: Instance lifecycle state. Workspace-local authoring supports `active`, `inactive`, and `superseded`.
- `logical_agent`: Stable logical agent identity, such as `dev_claude` or `dev_codex`.
- `role`: Role family used for task assignment and policy matching.
- `runtime`: Runtime family, such as `local_manual`, `cloud_24h`, `codex_cli`, `codex_mac`, or `claude_code`.
- `runtime_profile`: Configured runtime profile name.
- `provenance`: Optional descriptive metadata. `vendor`, `harness`, `model_family`, and `host` are open-vocabulary free-form strings, not closed enums. Provenance may support human-readable audit evidence and explicit independence checks but does not grant authority or replace capabilities.
- `agent_ui_host`: Host where the agent UI or operator-facing session runs.
- `execution_host`: Host where repository commands execute.
- `repo_host`: Host where canonical repository state lives.
- `validation_host`: Host where validation output is authoritative.
- `git_host`: Host where git status, diff, stage, commit, and push are authoritative.
- `connection_method`: Connection method from the UI host to the execution host, such as `ssh`.
- `ssh_target`: SSH target name when `connection_method` is `ssh`.
- `canonical_repo_path`: Repository path on the authoritative repo host.
- `validation_required_on`: Declares which host category must run validation, such as `execution_host`.
- `git_operations_allowed_on`: Declares which host category may perform git operations, such as `git_host`.
- `availability_status`: Current operational state. Recommended values: `online`, `idle`, `busy`, `offline`, `maintenance`, or `unknown`.
- `model_tiers_available`: Model tiers this instance can safely expose under policy.
- `current_model_tier`: Tier currently selected or bound for this instance.
- `task_modes_supported`: Task modes the instance can perform.
- `capabilities`: Fine-grained declared abilities used by matching.
- `governance_authority`: Declared authority class for the instance. Recommended values are `execution`, `audit`, `owner`, or `advisory`.
- `allowed_modes`: Task-scoped modes the instance may perform, such as `planner`, `executor`, `coder`, `reviewer`, `finalize_operator`, or non-code task modes.
- `forbidden_modes`: Modes the instance must not perform, even if other profile data looks compatible.
- `mode_separation`: Optional per-mode governance notes used to preserve planner/executor/auditor/Owner boundaries.
- `environment`: Local/cloud/workspace constraints and other non-secret environment descriptors.
- `context_bundles_supported`: Context bundles or bundle classes this instance may use.
- `write_scopes`: Declared repository or artifact write scopes for matching.
- `max_concurrent_tasks`: Maximum active claimed tasks for the instance.
- `active_task_ids`: Current active claimed tasks known to the profile.
- `heartbeat_at`: Last time the profile was observed or refreshed.
- `claiming_enabled`: Whether this instance may attempt claims.
- `aliases`: Additional names that may match task `assigned_to` or requirements fields.
- `sdk_compatibility`: Optional AIPOS-95 adapter metadata for SDK-shaped references. These fields are references only and do not grant provider credentials, runtime access, claim authority, write authority, or SDK type authority over Lybra.
- `role_catalog`: Optional AIPOS-97 metadata that links a concrete instance to a vendor-neutral role template. Template references are matching context only and do not grant OS permissions, runtime access, claim authority, write authority, audit authority, or Owner authority.

## Mixed-Host Runtime Semantics

Mixed-host fields describe topology and authority, not additional permission.

For example, a mixed-host workspace profile can declare:

```yaml
agent_ui_host: macos
execution_host: workspace-host
repo_host: workspace-host
validation_host: workspace-host
git_host: workspace-host
connection_method: ssh
ssh_target: workspace-host
validation_required_on: execution_host
git_operations_allowed_on: git_host
```

These fields can be used by matching, reporting, and future session binding. They do not execute commands, grant filesystem writes, grant GitHub authority, bypass audit, or bypass Owner approval.

## Availability Semantics

Availability is an input to matching:

- `idle` or `online` may satisfy idle-required tasks when no active task is present.
- `busy` may match only when the task allows busy instances and concurrency limits permit it.
- `offline`, `maintenance`, and `unknown` do not satisfy idle-required tasks.
- stale `heartbeat_at` may make the profile ineligible under future freshness policy.

## Capability Semantics

Capabilities should be concrete enough for matching:

```yaml
capabilities:
  - repo_read
  - repo_edit
  - test_run
  - protocol_authoring
  - audit_review
  - web_ui_read
  - validation_via_ssh
```

Missing capability data does not satisfy a required capability. Broad capability labels do not bypass write-scope, model-tier, context, review, audit, validation host, git host, or Owner approval policy.

## Model Tier Semantics

Model tier follows `0_control_plane/agents/model_routing_policy.md`.

- A task `model_tier` or `requirements.min_model_tier` must be satisfied before claim.
- A tier-bound remote instance should expose its tier through `current_model_tier`.
- A local manual instance may expose multiple available tiers, with the selected tier recorded at claim or task execution time.
- Higher tier use must still respect cost, risk, write target, and Owner policy.

## Context and Write Scope Semantics

`context_bundles_supported` and `write_scopes` are matching declarations only.

They do not:

- load context automatically
- grant filesystem writes
- grant GitHub writes
- permit formal memory promotion
- permit external publication
- override task artifact policy

## Relationship To Runtime Profiles

This schema complements `agent_runtime_profile_schema.md` and `runtime_profile_policy.md`.

Runtime profiles describe launch, topology, and authoritative host variants. Capability profiles describe what a concrete observed instance may claim now.

Neither schema requires the CLI, Board, or any agent runtime to execute commands in AIPOS-49.

## Workspace-Local Authoring

AIPOS-149 adds CLI-only custom profile authoring under:

```text
<workspace_root>/0_control_plane/agents/custom_agent_profiles.yaml
```

Workspace-local custom profiles are loaded before product bundled defaults. Canonical and legacy identifier collisions block authoring instead of silently merging identities. The writer uses an explicit draft preview, Owner confirmation, scoped registry write, and post-write revalidation flow. It does not mutate historical records, queues, Board behavior, MCP behavior, runtime state, or controlled execute allowlists.

## AIPOS-95 SDK Compatibility

AIPOS-95 defines a shallow adapter boundary for SDK-shaped concepts.

Capability profiles may carry SDK-shaped references only as translation metadata. Lybra capability profiles remain authoritative for matching, allowed modes, write scopes, governance authority, and model-tier suitability.

SDK-shaped agent or session data must not make an instance eligible to claim work unless normal AIPOS-48 matching, AIPOS-50 session lease binding, model routing, write-scope, review/audit separation, and Owner policy also pass.

## AIPOS-97 Role Catalog

AIPOS-97 defines a vendor-neutral Template Layer for worker role templates and preserves this schema as the Instance Layer for concrete agents.

Capability profiles may reference a role template, but the concrete profile remains authoritative for observed availability, runtime profile, host topology, current model tier, and deployment-specific capability declarations.

Template metadata must not make an instance eligible to claim work unless AIPOS-47 model routing, AIPOS-48 task matching and claim, AIPOS-50 session lease binding, write-scope policy, review/audit separation, and Owner gates also pass.

Coordinator Planner remains a fixed protocol contract and is not an ordinary worker role template.

## Combined Planner/Executor Mode

AIPOS-53 allows a single execution-authority agent instance to declare multiple execution modes, such as `planner`, `executor`, and `finalize_operator`.

Example:

```yaml
governance_authority: execution
allowed_modes:
  - planner
  - executor
  - finalize_operator
forbidden_modes:
  - independent_auditor
  - owner_decider
mode_separation:
  planner:
    governance_authority: execution
    may:
      - inspect_repo_state
      - propose_plan
      - create_task_cards
      - draft_subtasks
      - prepare_audit_cards
    must_not:
      - bypass_owner_decision_gate
      - self_audit
      - silently_expand_scope
      - act_as_owner_decider
  executor:
    governance_authority: execution
    may:
      - edit_files_within_task_scope
      - run_validation
      - prepare_implementation_report
    must_not:
      - expand_scope_without_owner
      - commit_before_audit_pass
      - push_before_audit_pass
      - self_audit
  finalize_operator:
    governance_authority: execution
    may:
      - stage_audited_files
      - run_finalize_checks
      - commit_after_audit_pass
      - push_after_audit_pass
    must_not:
      - add_unreviewed_changes
      - skip_documentation_alignment_check
      - finalize_with_pending_owner_decision
      - finalize_before_audit_pass
  auditor:
    governance_authority: audit
    required_independent: true
  owner_decider:
    governance_authority: owner
    required_human_owner: true
    non_delegable: true
```

These declarations do not grant permissions by themselves. They preserve matching and visibility boundaries for future runtime/session policy. Independent audit and Owner decision gates remain separate from execution authority.
