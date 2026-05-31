# Custom Agent Instance And Profile Authoring Protocol

## Status

AIPOS-148 defines a protocol-only authoring boundary for user-managed agent instances and profiles.

This document does not implement validators, writers, CLI commands, Board forms, MCP tools, registry files, profile migrations, queue mutation, controlled execute allowlist changes, runtime launch, autonomous orchestration, agent launch, scheduler behavior, heartbeat behavior, credential handling, deployment changes, or workspace mutations.

## Purpose

AIPOS-146 separated stable opaque instance identity from explicit capabilities and optional provenance metadata. AIPOS-147 implemented the first bundled profile migration:

```text
agent-01
agent-02
agent-03
```

Those bundled values are defaults, not immutable product constants.

Real users need a governed way to register their own instances, describe provenance, choose human-readable labels, and evolve profiles without rewriting historical evidence or granting hidden authority.

AIPOS-148 defines that authoring contract before any user-facing implementation.

## Core Model

One custom agent instance separates:

1. stable opaque canonical ID
2. editable human-readable display name
3. explicit capability and runtime metadata
4. optional free-form provenance metadata
5. additive legacy and supersession references

Example:

```yaml
agent_instance: agent-01
display_name: Primary Local Coding Session
legacy_instance_ids:
  - dev.claude.cc.local
identity_status: active
runtime_profile: cc
provenance:
  vendor: anthropic
  harness: claude-code
  model_family: claude
  host: local
capabilities:
  - repo_edit
  - test_run
```

The canonical ID is the durable key. `display_name` is presentation metadata. Provenance is descriptive metadata. Capabilities are matching declarations. None grants runtime or external authority by itself.

## Canonical ID Lifecycle

### Creation

New custom canonical IDs must satisfy the AIPOS-146 format:

```text
^[a-z0-9][a-z0-9._-]{0,63}$
```

Rules:

- Owner or an explicitly approved operator chooses the ID.
- The ID is unique within the effective workspace profile registry.
- The authoring surface must not generate semantic meaning from ID segments.
- Suggested generated IDs should use role-neutral opaque labels such as `agent-04`.
- The system must not infer role, capability, vendor, harness, model family, host, authority, independence, or trust level from the ID.

### Stability

After an ID appears in a task, claim, session, orchestration event, decision record, or audit artifact, it must not be silently renamed in place.

### Replacement

Renaming a stable identity means additive replacement:

```yaml
agent_instance: agent-04
supersedes_instance_ids:
  - agent-01
legacy_instance_ids:
  - dev.claude.cc.local
identity_status: active
```

The previous canonical ID should become `superseded` or `inactive`, not disappear.

Historical records remain unchanged.

## Display Name

Each instance may define:

```yaml
display_name:
```

Rules:

- editable without changing canonical identity
- intended for CLI and Board human-readable display
- may contain spaces and Unicode where the profile file encoding supports it
- must not contain secrets, tokens, passwords, private keys, or credential material
- must not be used for exact claim enforcement
- must not be used as a unique key
- must not satisfy `specific_instance_only`
- must not become an alias automatically
- duplicate display names are allowed but should trigger an operator-facing warning

Recommended UI pattern:

```text
Primary Local Coding Session
agent-01
```

Lists should prefer `display_name` as the primary label and keep canonical `agent_instance` visible as secondary audit text or in details.

## Provenance Metadata

Optional instance provenance:

```yaml
provenance:
  vendor:
  harness:
  model_family:
  host:
```

All provenance fields are open-vocabulary free-form strings.

They must not be closed enums. This keeps Lybra compatible with uncommon vendors, local models, relays, custom harnesses, private adapters, and user-defined environments.

Rules:

- optional for registration
- descriptive and auditable
- editable as deployment facts evolve
- must not contain secrets
- may support explicit independence checks
- must not grant capabilities, claim eligibility, filesystem writes, network authority, runtime launch, external actions, audit authority, Owner authority, or credential access
- must not be inferred from canonical IDs, display names, aliases, runtime commands, or hostnames embedded in unrelated fields

When an independence requirement explicitly requests one provenance dimension, comparison uses exact strings. Missing or `unknown` required values fail conservatively.

## Capability And Runtime Metadata

Custom profiles may declare explicit matching and inspection metadata:

```yaml
logical_agent:
role:
runtime:
runtime_profile:
runtime_entrypoint:
runtime_command:
runtime_args: []
runtime_env: {}
default_task_modes: []
model_tiers_available: []
capabilities: []
context_bundles_supported: []
write_scopes: []
allowed_modes: []
forbidden_modes: []
availability_status:
enabled:
```

These fields are declarations.

They do not:

- execute commands
- launch runtimes
- grant OS permissions
- grant network access
- grant credentials
- grant GitHub authority
- widen controlled execute allowlists
- bypass task matching
- bypass independent audit
- bypass Owner gates

`runtime_env` must not store secrets.

## Authoritative Configuration Layers

Lybra should distinguish product defaults from user-owned workspace configuration.

### Product Bundled Defaults

Bundled defaults remain product-controlled examples and fallback profiles:

```text
0_control_plane/agents/*_runtime_profiles.md
```

They are not the preferred target for end-user edits after npm installation.

### Workspace-Local Registry Target

Future authoring implementation should write a workspace-local, file-authoritative registry under:

```text
<workspace_root>/0_control_plane/agents/
```

Recommended future file:

```text
<workspace_root>/0_control_plane/agents/custom_agent_profiles.yaml
```

This path and filename are protocol targets, not an implemented writer contract. The implementation slice must confirm ownership, merge precedence, workspace template coverage, and migration behavior before writing files.

### Precedence Direction

Recommended future read precedence:

```text
workspace-local custom profiles
-> product bundled defaults
-> code fallback only when profile files are unavailable
```

The implementation slice must define conflict handling explicitly. It must not silently merge two canonical instances with the same ID.

## Authoring Operations

Future user-facing surfaces may support:

```text
create
preview_update
confirm_update
deactivate
supersede
list
inspect
validate
```

### Create

Create a new canonical instance with explicit fields.

Creation must validate:

- canonical ID format
- canonical ID uniqueness
- no ambiguous legacy mapping
- provenance scalar shape
- no secret-like fields in prohibited locations
- allowed status values
- declared capabilities and runtime fields are data only

### Update

Routine updates may edit:

- `display_name`
- description or notes
- provenance
- declared capabilities
- runtime metadata
- availability visibility
- enabled status

Changes to capability, write scope, allowed modes, runtime topology, or legacy mapping may affect authority or matching. They require a visible preview and an explicit Owner confirmation boundary in the later implementation.

### Deactivate

Deactivation should preserve the profile:

```yaml
identity_status: inactive
enabled: false
```

Deactivation must not erase historical records or silently reassign tasks.

### Supersede

Supersession creates a new canonical ID and links the old identity explicitly. It does not rewrite history.

## Validation Semantics

The future validator should classify findings conservatively.

### BLOCK

- invalid canonical ID format
- duplicate canonical ID
- ambiguous legacy mapping
- canonical ID missing
- malformed provenance object
- legacy ID or canonical ID collision that cannot resolve uniquely
- attempt to overwrite historical records
- attempt to store secrets in prohibited fields
- unsupported mutation outside the workspace-local registry boundary

### NEEDS_OWNER

- capability expansion
- write-scope expansion
- allowed-mode expansion
- runtime topology change
- legacy mapping addition or removal
- canonical-ID supersession
- provenance change used by active stronger-independence policy
- deactivating an instance referenced by pending or claimed work

### WARN

- duplicate display name
- missing display name
- missing optional provenance
- `unknown` provenance where no stronger-independence requirement currently depends on it
- inactive legacy profile retained for history

## Draft, Preview, Confirm Discipline

Any later writer must use a visible mutation flow:

```text
draft
-> validate
-> preview
-> Owner confirm when required
-> write workspace-local registry
-> validate again
```

Required safety properties:

- dry-run writes nothing
- preview shows exact target path and semantic diff
- confirm token is scoped to the preview snapshot
- execute-time revalidation blocks stale previews
- writes stay inside the approved workspace-local registry target
- no hidden queue mutation
- no runtime launch
- no automatic task claim
- no automatic role-registry mutation
- no silent product-repo edits

## UI And CLI Presentation

Future CLI and Board surfaces should:

- show `display_name` as the primary human label when present
- show canonical `agent_instance` as visible secondary text
- expose provenance in inspection details
- expose `identity_status`, legacy IDs, and supersession links in details
- distinguish bundled defaults from workspace-local custom profiles
- show validation warnings before mutation
- avoid treating display names as claim identities

Current Board and CLI direct-ID rendering remains compatible until a separately approved implementation changes it.

## Backward Compatibility

- Existing bundled profiles remain valid.
- Existing canonical IDs remain valid.
- Legacy IDs remain readable through explicit mappings.
- Historical task cards and records remain immutable.
- Existing records without `display_name`, provenance, `identity_status`, or supersession metadata remain valid.
- Existing claim enforcement remains canonical-key based after explicit resolution.
- AIPOS-144/AIPOS-145 dependency-state behavior remains unchanged.

## Relationship To AI-Assisted Task Authoring

Custom Agent Profile Authoring and AI-Assisted Task Authoring are related onboarding surfaces but remain separate tasks.

Custom Agent Profile Authoring defines which user-managed instances exist and what they declare.

AI-Assisted Task Authoring may later help users draft task cards against registered instances and capabilities. It must not create hidden agent profiles, silently expand capabilities, or auto-approve profile mutations.

## Affected Surface Inventory

A later implementation proposal should review:

### Configuration And Schemas

- workspace template `0_control_plane/agents/` coverage
- future workspace-local custom profile registry file
- `0_control_plane/agents/agent_instance_policy.md`
- `0_control_plane/agents/agent_capability_profile_schema.md`
- `0_control_plane/agents/agent_runtime_profile_schema.md`
- `0_control_plane/agents/instance_identity_generalization_protocol.md`

### CLI And Validation

- `tools/aipos_cli/agent_profiles.py`
- future custom profile registry reader
- future custom profile validator
- future custom profile writer
- future dry-run token and confirm integration
- CLI list, inspect, validate, create, update, deactivate, and supersede commands
- regression tests for precedence, collisions, dry-run safety, stale preview blocking, and historical compatibility

### Board And MCP

- Board profile list and detail rendering
- future Board authoring form only after separate scope approval
- MCP read exposure only after separate scope approval
- MCP write exposure remains a separate Owner gate

## Deferred Implementation Decisions

Pause for Owner decision before implementation chooses:

- exact workspace-local registry filename and YAML shape
- whether AIPOS-149 implements CLI-only authoring first or includes Board UI
- whether create/update use existing controlled execute plumbing or a new narrowly scoped writer operation
- which capability changes require confirm versus hard Owner review
- how bundled and workspace-local profile conflicts render
- whether display-name polish ships in the same implementation slice or a smaller follow-up
- whether MCP receives any read or write surface

## Non-Goals

AIPOS-148 does not:

- create a custom profile registry file
- add a profile writer
- add CLI commands
- add Board controls
- add MCP tools
- edit bundled profiles
- migrate historical records
- change matching, claim, dependency, audit, or finalize behavior
- grant runtime authority
- add autonomous orchestration, runtime launch, scheduler, queue polling, heartbeat, background recovery, credentials, deployment behavior, or public endpoints
- approve implementation

## Owner Decision Gates

Independent audit and explicit Owner approval are required before any implementation slice.

Stop for Owner decision on:

- registry file ownership and location
- writer surface
- mutation confirmation model
- capability-authority expansion semantics
- Board or MCP scope
- migration beyond additive compatibility
- runtime, credential, deployment, or public-endpoint changes
