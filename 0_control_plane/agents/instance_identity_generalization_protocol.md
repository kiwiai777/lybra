# Instance Identity Generalization Protocol

## Status

AIPOS-146 defines a protocol-only identity-model cleanup for concrete agent instances.

This document does not implement validators, matching code, queue mutation, Board behavior, MCP behavior, records writers, profile migrations, registry migrations, task-card rewrites, runtime behavior, autonomous orchestration, agent launch, heartbeat behavior, credential changes, deployment changes, or workspace mutations.

## Purpose

Lybra currently uses concrete instance identifiers such as:

```text
dev.claude.cc.local
dev.claude.cc_glm.local
```

These identifiers mix several distinct concerns into one string:

- a stable instance key
- functional role or logical-agent hints
- vendor or model-family hints
- harness or command-entrypoint hints
- host or deployment hints

That coupling is unnecessary and creates pressure to infer authority or compatibility by parsing names. AIPOS-143 exposed the resulting sibling-instance claim bug. AIPOS-144 and AIPOS-145 closed that enforcement gap by requiring explicit concrete-instance equality for `specific_instance_only`.

AIPOS-146 generalizes the identity model so future instance IDs are Owner-defined, stable, opaque keys. Capabilities and provenance remain explicit profile metadata.

## Owner-Locked Three-Part Model

Concrete instance configuration separates three concepts.

### 1. Opaque Instance ID

`agent_instance` is the stable unique key for one concrete instance.

Examples:

```text
coder-primary
auditor-independent
agent-1
```

The system treats the value as an opaque identifier. It must not infer role, capability, vendor, harness, model family, host, runtime, authority, independence, or trust level from substrings, prefixes, suffixes, separators, or apparent naming conventions.

### 2. Explicit Capabilities

Capability and execution suitability remain explicit fields in the capability profile.

Examples:

```yaml
model_tiers_available:
  - L2
task_modes_supported:
  - coding
capabilities:
  - repo_edit
  - test_run
context_bundles_supported:
  - default_dev
write_scopes:
  - repository_workspace
```

Matching uses declared fields and policy. It does not use instance-ID semantics.

### 3. Optional Provenance Metadata

Deployment provenance may be declared as optional descriptive metadata:

```yaml
provenance:
  vendor:
  harness:
  model_family:
  host:
```

These fields support human-readable audit evidence and explicit independence checks. They do not enter the identifier, grant authority, or become a substitute for capability declarations.

## Opaque Instance ID Rules

### Format

New canonical instance IDs should satisfy:

```text
^[a-z0-9][a-z0-9._-]{0,63}$
```

Rules:

- lowercase ASCII letters, digits, `.`, `_`, and `-` only
- first character must be a lowercase letter or digit
- maximum length: 64 characters
- no whitespace
- no path separators
- no secret, credential, token, email address, or personal data
- unique within one Lybra workspace instance registry

The allowed separators are syntax only. They do not create parseable segments.

### Stability

An instance ID is stable after use in a task, claim, session, audit, or orchestration record.

Changing the display label, vendor, harness, model family, host, runtime profile, or capability metadata must not silently rewrite the instance ID.

If an identity must be replaced, the later migration slice should add a new canonical instance ID and record explicit supersession or alias metadata. Historical records remain immutable.

### Prohibited Semantic Parsing

Core policy, matching, claim enforcement, validation, rendering, Board adapters, MCP adapters, and records readers must not derive semantics from instance-ID strings.

Forbidden inference examples:

- treating `dev.` as an engineering role
- treating `.glm.` as a model-family declaration
- treating `.cc.` as a harness declaration
- treating `.local` as a host or runtime declaration
- accepting two sibling instances because they share a prefix
- rejecting independent instances because their labels look similar

Exact key comparison is allowed and required where policy names a concrete instance.

## Capability Profile Shape

The later implementation slice should align the capability-profile shape conservatively:

```yaml
agent_instance: coder-primary
logical_agent: dev_claude
role: engineering
runtime: local_manual
runtime_profile: cc
provenance:
  vendor: anthropic
  harness: claude_code
  model_family:
  host: wsl-workspace
model_tiers_available:
  - L2
task_modes_supported:
  - coding
capabilities:
  - repo_edit
  - test_run
context_bundles_supported:
  - default_dev
write_scopes:
  - repository_workspace
aliases:
  - dev.claude.cc.local
```

`logical_agent`, `role`, `runtime`, `runtime_profile`, capabilities, context bundles, and write scopes retain their existing explicit meanings.

`aliases` may preserve backward-compatible lookup for legacy references where policy allows alias-aware matching. An alias must never satisfy `specific_instance_only` unless a later implementation explicitly resolves a legacy reference to one canonical instance before performing exact canonical-key equality.

## Provenance Metadata

### Fields

Recommended optional fields:

```yaml
provenance:
  vendor:
  harness:
  model_family:
  host:
```

Meanings:

- `vendor`: Provider or vendor family used by the instance when known.
- `harness`: Operator-facing tool, adapter, CLI, or execution harness when known.
- `model_family`: Model-family descriptor when known.
- `host`: Human-readable execution-host or deployment-host descriptor when known.

These are optional because local manual workflows, relays, compatible adapters, and future provider-neutral runtimes may not expose every value reliably.

### Boundaries

Provenance metadata:

- is descriptive and auditable
- may be used by explicit independence validation
- may be rendered for operator review
- may be absent or `unknown`
- must not contain secrets
- must not silently grant task eligibility, write authority, runtime authority, audit authority, Owner authority, credentials, deployment access, or external-action permission
- must not be inferred from the instance ID

Capability matching continues to use capability-profile fields, not provenance names.

## Explicit Independence Semantics

Lybra must represent audit and review independence through explicit fields and comparisons.

### Necessary Condition

Independent auditor and executor instances must have different canonical instance IDs:

```text
auditor_instance_id != executor_instance_id
```

Different IDs are necessary for independent audit. They are not always sufficient for stronger separation claims.

### Stronger Assurance Dimensions

When policy requires or evidence benefits from stronger separation, the profile or task may declare explicit dimensions:

```yaml
independence_requirements:
  distinct_instance: true
  distinct_runtime_profile: false
  distinct_harness: false
  distinct_model_family: false
  distinct_vendor: false
  distinct_host: false
```

Each enabled dimension is validated against explicit profile fields:

- `distinct_instance`: canonical instance IDs differ
- `distinct_runtime_profile`: `runtime_profile` values differ
- `distinct_harness`: `provenance.harness` values differ
- `distinct_model_family`: `provenance.model_family` values differ
- `distinct_vendor`: `provenance.vendor` values differ
- `distinct_host`: `provenance.host` values differ

If a required dimension is missing or `unknown`, the system must not claim that assurance level passed. The safe outcome is BLOCK or NEEDS_OWNER according to the workflow boundary.

### No Name-Based Independence

The system must not infer independence from labels looking different. It must not infer non-independence from labels sharing a prefix. It must not infer vendor, harness, runtime, model family, or host separation from identifier text.

### Default Compatibility Rule

Existing independent-audit flows keep their current minimum rule unless a task or policy explicitly requests stronger dimensions:

```yaml
independence_requirements:
  distinct_instance: true
```

This preserves the AIPOS-145 strict-instance enforcement foundation without silently raising audit requirements for historical workflows.

## Relationship To AIPOS-144 And AIPOS-145

AIPOS-144 defined strict specific-instance enforcement as:

```text
claimant_instance_id == required_instance_id
```

AIPOS-145 implemented exact concrete-instance equality before alias-aware matching and split complex dependency semantics into:

```text
executor_completion
audit_readiness
audit_pass
```

AIPOS-146 does not reopen either behavior.

The AIPOS-145 equality rule is the compatibility foundation for generalized IDs: if IDs are opaque keys, exact comparison remains valid without parsing identifier strings.

## Backward Compatibility

### Historical Instance IDs

Legacy IDs such as:

```text
dev.claude.cc.local
dev.claude.cc_glm.local
dev.codex.local
```

remain valid opaque strings during the compatibility window.

Their apparent segments must no longer be treated as semantics. Existing exact equality behavior remains valid.

### Historical Task Cards

Historical task cards are not rewritten by AIPOS-146.

Tasks that name a legacy instance ID continue to resolve under the existing profile registry and AIPOS-145 exact-match rule.

### Historical Claim And Session Records

Historical claim logs, session records, orchestration records, and audit evidence remain immutable.

Readers must continue to display and accept legacy instance-ID strings as historical identity evidence. Readers must not reinterpret old IDs into inferred provenance fields.

Historical records without explicit provenance remain valid records with unknown provenance assurance.

### Migration Strategy

A later Owner-approved implementation slice should use an additive migration:

1. Add canonical opaque instance IDs to the instance registry or capability profiles.
2. Preserve legacy IDs as explicit aliases or migration references.
3. Update new task authoring defaults to emit canonical opaque IDs.
4. Keep readers compatible with historical records.
5. Avoid bulk rewriting historical task cards, claim logs, session records, or audit evidence.
6. Require an explicit migration map where an old ID resolves to a new canonical ID.
7. BLOCK or route to Owner when one legacy ID maps ambiguously to multiple canonical instances.

Recommended migration metadata shape:

```yaml
agent_instance: coder-primary
legacy_instance_ids:
  - dev.claude.cc.local
identity_status: active
supersedes_instance_ids: []
```

This shape is protocol guidance only. AIPOS-146 does not add fields to live schemas or registry data.

## Role Registry Boundary

Role registry identities and concrete instance IDs are separate concepts.

- role registry entries describe configurable actors or role instances
- capability profiles describe concrete execution instances
- role templates remain vendor-neutral functional templates under AIPOS-97
- task cards may name a logical assignment and, when required, one concrete instance

A role-registry `id` must not automatically become a concrete `agent_instance` unless a profile explicitly binds them.

## Affected Surface Inventory

A later implementation and protocol-alignment slice should review:

### Identity, Capability, And Runtime Profiles

- `0_control_plane/agents/agent_instance_policy.md`
- `0_control_plane/agents/agent_capability_profile_schema.md`
- `0_control_plane/agents/agent_runtime_profile_schema.md`
- `0_control_plane/agents/dev_claude_runtime_profiles.md`
- future agent registry or capability-profile files

### Matching, Claim, And Session Policies

- `0_control_plane/dispatch/task_matching_policy.md`
- `0_control_plane/dispatch/task_claim_protocol.md`
- `0_control_plane/tasks/task_session_policy.md`
- `0_control_plane/tasks/task_session_schema.md`
- `0_control_plane/tasks/task_session_lease_runtime_binding_policy.md`
- `5_tasks/claim_event_schema.md` in generated or workspace templates where present
- claim logs and session-record formats produced by approved writers

### Role And Orchestration Surfaces

- `0_control_plane/roles/role_registry_schema.md`
- `0_control_plane/roles/role_registry.yaml`
- `0_control_plane/agents/heterogeneous_agent_role_catalog_protocol.md`
- `0_control_plane/orchestration/planner_assignment_continuity_policy.md`
- `0_control_plane/orchestration/planner_subtask_policy.md`
- `0_control_plane/orchestration/orchestration_task_schema.md`
- `0_control_plane/context_pack/context_pack_preview_schema.md`

### CLI, Board, And Test Surfaces

- `tools/aipos_cli/agent_profiles.py`
- `tools/aipos_cli/validator.py`
- `tools/aipos_cli/queue_mutation.py`
- `tools/aipos_cli/record_writer.py`
- `tools/aipos_cli/records.py`
- `tools/aipos_cli/task_loader.py`
- `tools/aipos_cli/context_pack_builder.py`
- CLI regression tests for profiles, matching, strict claims, records, sessions, and migration compatibility
- Board read and preview surfaces that render logical agents, concrete instances, or record provenance

The inventory is review guidance. AIPOS-146 itself changes none of these behavior surfaces.

## Deferred Implementation Requirements

Any later implementation slice requires separate Owner approval and independent audit.

That slice should:

- add canonical opaque ID validation for newly authored profiles
- preserve legacy IDs as explicit compatibility inputs
- add explicit provenance metadata without inferring it from IDs
- add explicit independence validation without changing default historical minimums
- preserve AIPOS-145 exact-key enforcement for `specific_instance_only`
- define migration-map ownership and ambiguity handling
- update documentation and regression coverage conservatively

It must not silently rename live instances, bulk-rewrite historical records, expand claim authority, add runtime behavior, or treat provenance as capability authority.

## Non-Goals

AIPOS-146 does not:

- rename any instance
- migrate any task card, claim log, session record, registry entry, or profile
- change matching or claim behavior
- change dependency-state behavior
- add a validator
- add Board or MCP features
- add runtime launch, autonomous orchestration, scheduler, queue polling, heartbeat, or background recovery
- expand controlled execute allowlists
- add credentials, deployment behavior, or public endpoints
- approve implementation

## Owner Decision Gates

Pause for Owner decision before:

- selecting canonical instance IDs for live profiles
- choosing the authoritative registry file for canonical IDs and migration maps
- requiring stronger independence dimensions by default
- deciding whether any provenance field becomes required
- changing alias resolution or legacy-ID compatibility behavior
- migrating existing records or task cards
- changing role-registry bindings
- approving any implementation slice
