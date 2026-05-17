# Anthropic SDK Compatibility Adapter Protocol

## Purpose

AIPOS-95 defines the protocol boundary for future compatibility with Anthropic managed agents and SDK-shaped concepts.

The adapter is shallow. Lybra remains the source of truth. SDK-shaped resources are translation inputs and outputs, not the core data model.

AIPOS-95 is protocol-only. It does not import `anthropic-sdk-python`, add package dependencies, call Anthropic APIs, create managed agents, create environments, connect MCP, create credentials, add backend routes, add Web UI controls, add CLI commands, launch sandbox runtimes, enable `anthropic_managed`, mutate queues, write records, expand controlled execute, or enable autonomous planner runtime.

## Strategic Source

AIPOS-95 implements the SDK compatibility direction recorded in `DL-20260513-04` Decision 4:

```text
Lybra protocol remains source of truth.
SDK shape is a translation entrypoint.
SDK -> Lybra supports migration from SDK users.
Lybra -> SDK supports future sandbox runtime pass-through for anthropic_managed provider.
Lybra schema naming should consciously align with SDK-shaped names where useful.
Do not introduce anthropic-sdk-python as a runtime dependency.
Do not let SDK types define Lybra core data models.
SDK changes affect adapter layer only.
```

## Core Principle

Lybra owns the canonical control-plane entities:

- tasks
- parent requirements
- planner assignments
- orchestration records
- planner iterations
- orchestration events
- task session leases
- Session Tree metadata
- SessionStore entries
- agent capability profiles
- runtime profiles
- context packs
- Owner decisions
- audit reports

SDK-shaped resources may map to these entities, but they must not replace them.

## Adapter Directions

### SDK To Lybra

`SDK -> Lybra` maps SDK-shaped declarations into Lybra protocol metadata.

Intended use:

- migrate users familiar with SDK-managed agents into Lybra task/orchestration concepts
- import or preview SDK-shaped agent/session/environment metadata
- generate Lybra-compatible draft declarations for Owner review

This direction must not auto-create tasks, auto-publish drafts, claim queues, create credentials, or enable runtimes.

### Lybra To SDK

`Lybra -> SDK` maps Lybra-approved protocol metadata into SDK-shaped requests for a future `anthropic_managed` sandbox runtime adapter.

Intended use:

- pass approved agent/session/environment/skill/vault references to a provider adapter
- keep Lybra task and orchestration state authoritative
- preserve file round-trip requirements from AIPOS-90

This direction must not be used until the `anthropic_managed` provider passes its own Owner Decision Gate and implementation task.

## Naming Alignment

Lybra schemas may consciously use or expose adapter fields that reduce translation cost with SDK-shaped concepts:

```text
agents
sessions
environments
skills
vault_ids
multiagent
```

These names are adapter-friendly terms only. They do not import SDK type authority into Lybra.

## Recommended Adapter Descriptor

Future adapter declarations may use:

```yaml
adapter_id:
adapter_kind: anthropic_sdk_compatibility
adapter_status: proposed
direction:
  sdk_to_lybra: true
  lybra_to_sdk: false
sdk_surface:
  agents: mapped
  sessions: mapped
  environments: mapped
  skills: mapped
  vault_ids: referenced
  multiagent: mapped
lybra_authority:
  source_of_truth: file_control_plane
  task_schema_authoritative: true
  orchestration_schema_authoritative: true
  session_schema_authoritative: true
  capability_profile_authoritative: true
  runtime_profile_authoritative: true
credential_boundary:
  secrets_in_adapter_payload: forbidden
  vault_ids_are_references_only: true
  owner_approval_required: true
provider_boundary:
  provider: anthropic_managed
  provider_enabled: false
  owner_gate_required: true
dependency_boundary:
  anthropic_sdk_python_required: false
  sdk_types_define_core_model: false
audit:
  independent_audit_required: true
```

AIPOS-95 does not create a live adapter descriptor file.

## Concept Mapping

Recommended non-normative mapping:

```yaml
sdk_agent:
  lybra_agent_capability_profile:
  lybra_runtime_profile:
  lybra_allowed_modes:
  lybra_model_tier_policy:
sdk_session:
  lybra_task_session:
  lybra_session_tree_node:
  lybra_planner_iteration_ref:
sdk_environment:
  lybra_sandbox_runtime_adapter:
  lybra_workspace_boundary:
  lybra_network_boundary:
sdk_skill:
  lybra_context_pack:
  lybra_role_template:
  lybra_allowed_task_modes:
sdk_vault_id:
  lybra_credential_ref:
  lybra_capability_token_ref:
sdk_multiagent:
  lybra_orchestration:
  lybra_planner_assignment:
  lybra_subtask_dag:
  lybra_review_audit_separation:
```

The mapping is advisory until a later implementation task defines exact request/response schemas.

## Source Of Truth Rules

The adapter must preserve these rules:

- task cards and queue directories remain authoritative for task state
- orchestration append logs remain authoritative for durable planner events
- SessionStore protocol remains authoritative for memory entries
- agent capability profiles remain authoritative for matching
- runtime profiles remain authoritative for topology
- Owner decisions remain non-delegable
- independent audit remains non-delegable

If SDK-shaped state and Lybra files disagree, Lybra files win.

## Credential Boundary

SDK-shaped `vault_ids` or similar secret references must remain references only.

The adapter must not:

- store raw API keys
- serialize secrets into task cards
- write `.env`
- mint tokens
- rotate credentials
- grant provider credentials
- pass secrets into reports
- log secret values

Any future provider credential integration requires a separate Owner Decision Gate and independent audit.

## Provider Boundary

`anthropic_managed` remains a candidate provider from AIPOS-90.

AIPOS-95 does not enable that provider.

Provider enablement requires a later task that defines:

- provider-specific lifecycle mapping
- dependency choice
- authentication boundary
- network boundary
- workspace injection boundary
- report format
- teardown behavior
- cost and paid-resource controls
- audit handoff requirements

## MCP Boundary

AIPOS-96 defines MCP as a separate protocol surface for MCP-aware clients.

The SDK compatibility adapter must not treat MCP exposure as implied by SDK compatibility. MCP tools, transports, client registration, remote exposure, and controlled execute mappings require their own Owner-approved implementation task.

## Runtime Dependency Boundary

AIPOS-95 does not add `anthropic-sdk-python` or any SDK package as a dependency.

Future implementations may choose:

- no SDK dependency, with pure schema translation
- optional SDK dependency isolated behind an adapter package
- provider-specific SDK dependency only in a provider plugin

Any dependency choice requires Owner approval and independent audit.

## Owner Decision Gates

Owner approval is required before:

- enabling `anthropic_managed`
- adding SDK dependencies
- creating SDK-managed agents
- creating SDK-managed environments
- connecting provider APIs
- creating or referencing credential vault integrations
- expanding network access
- granting workspace write scope
- changing model tier or agent authority
- changing audit boundaries
- allowing paid-resource usage
- mapping SDK state into claimable tasks
- exposing SDK adapter controls in CLI, Web UI, MCP, or backend routes
- external publish, commit, push, or finalize

## Relationship To Existing Protocols

- AIPOS-47 model routing remains authoritative for model tier decisions.
- AIPOS-48 dispatch matching remains authoritative for task claim eligibility.
- AIPOS-50 session lease binding remains authoritative for active task sessions.
- AIPOS-53 and AIPOS-54 preserve Owner-gated planner loop behavior.
- AIPOS-90 keeps sandbox runtime adapters provider-agnostic and does not enable `anthropic_managed`.
- AIPOS-91 Session Tree remains separate from SDK sessions.
- AIPOS-92 SessionStore remains separate from SDK session state.
- AIPOS-93 Subtask DAG remains Lybra's dependency graph model.
- AIPOS-94 autonomy tiers remain Lybra confirmation-cadence metadata.

## Non-Goals

AIPOS-95 does not implement:

- `anthropic-sdk-python` import
- SDK package dependency
- SDK client code
- Anthropic API calls
- managed agent creation
- managed environment creation
- provider credential storage
- vault integration
- MCP integration
- provider enablement
- sandbox runtime launch
- backend route
- Web UI control
- CLI command
- MCP tool
- controlled execute allowlist expansion
- queue mutation
- draft writer
- draft publish automation
- records writer
- orchestration writer
- SessionStore writer
- autonomous planner runtime
- background polling
- agent execution UI
- auth/RBAC
- database
- deployment configuration
- public endpoint behavior
- git automation
- automatic commit/push
- automatic finalize
- self-audit
