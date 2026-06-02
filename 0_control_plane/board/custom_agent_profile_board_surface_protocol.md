# Custom Agent Profile Board Surface Protocol

## Status

AIPOS-162 defines a protocol-only Board surface for workspace-local custom agent profile authoring.

This document does not implement Board controls, backend routes, validators, writers, registry migrations, matching changes, claim changes, controlled execute allowlist changes, MCP tools, runtime launch, autonomous orchestration, scheduler behavior, polling, heartbeat behavior, credential handling, deployment changes, or public endpoints.

## Purpose

AIPOS-148 defined the governed authoring contract for user-managed custom agent instances and profiles. AIPOS-149 implemented the CLI-only workspace-local writer:

```text
draft
-> validate
-> readable preview
-> explicit Owner confirmation
-> scoped registry write
-> revalidate
```

The next bounded slice should expose that existing writer through the local Board so users can register and evolve their own agents without hand-writing JSON files or YAML registries.

The Board is an authoring and review surface. It must reuse the AIPOS-149 writer and must not invent a second profile mutation path.

## Relationship To Existing Protocols

- AIPOS-146 defines opaque canonical instance IDs and explicit provenance.
- AIPOS-147 implements bundled canonical IDs and additive legacy compatibility.
- AIPOS-148 defines custom agent instance and profile authoring semantics.
- AIPOS-149 implements CLI-only workspace-local profile authoring.
- AIPOS-160 and AIPOS-161 expose AI-assisted task authoring through the Board while keeping custom-profile authoring separate.

This Board slice must remain separate from AI-assisted task authoring. AI authoring may read registered profiles for recommendation context, but it must not create or modify profiles.

## Board Workbench Placement

The future panel should appear under `Advanced / Debug`, adjacent to the existing read-only `Agents Summary` and `Agents Detail` panels.

This placement is deliberate:

- profile authoring is low-frequency workspace administration;
- capability, write-scope, legacy mapping, and supersession changes are Owner-visible;
- the daily Decision Desk should stay focused on task decisions, authoring, and publication.

The panel should be named `Custom Agent Profiles`.

## Supported Operations

The first Board implementation should expose the existing AIPOS-149 operations:

- `upsert`;
- `deactivate`;
- `supersede`.

Read-only list, inspect, and validate views may reuse the existing Agents Summary / Detail route and refresh behavior where sufficient.

### Upsert

Create a new custom instance or update an existing instance by canonical ID.

### Deactivate

Preserve the custom profile while setting the target instance inactive and disabled. Do not delete historical profile data or reassign tasks.

### Supersede

Create a replacement canonical ID and add explicit supersession links. Do not rename in place or rewrite historical task, claim, session, orchestration, decision, or audit artifacts.

## Structured Inputs

The future Board slice should use structured inputs for common profile fields:

- action;
- actor;
- logical `agent_id`;
- stable opaque canonical `agent_instance`;
- editable `display_name`;
- `identity_status`;
- `enabled`;
- optional `description`;
- optional open-vocabulary provenance fields:
  - `vendor`;
  - `harness`;
  - `model_family`;
  - `host`;
- optional explicit declaration lists:
  - `capabilities`;
  - `write_scopes`;
  - `default_task_modes`;
  - `model_tiers_available`;
  - `context_bundles_supported`;
  - `allowed_modes`;
  - `forbidden_modes`;
  - `legacy_instance_ids`;
  - `supersedes_instance_ids`.

The supersede operation should visibly separate:

- existing canonical `agent_instance`;
- replacement canonical `agent_instance`;
- replacement presentation and declaration fields.

Rules:

- canonical IDs remain stable opaque keys;
- `display_name` is presentation-only;
- provenance is open-vocabulary descriptive metadata;
- declarations do not grant runtime or external authority;
- list inputs must be normalized explicitly before preview;
- no field may infer identity, capability, trust, independence, vendor, harness, model family, host, or authority from another field.

## Credential And Runtime Boundary

The Board must not accept credential material.

The first slice must not expose `runtime_env` editing. AIPOS-149 already blocks non-empty `runtime_env` because profile registries are not credential stores.

Any future runtime metadata authoring expansion requires a separate Owner gate. Profile declarations remain inert data and must not:

- launch a process;
- execute a runtime command;
- open network access;
- read environment credentials;
- grant filesystem access;
- grant GitHub authority;
- widen controlled execute;
- claim tasks automatically.

## Readable Owner Review

The Board should prioritize a readable profile mutation preview.

At minimum, show:

- action;
- target registry path;
- actor;
- canonical `agent_instance`;
- `display_name`;
- logical `agent_id`;
- semantic change summary;
- Owner-visible fields;
- identity status;
- enabled state;
- provenance;
- capability and write-scope declarations;
- legacy and supersession references;
- validation warnings;
- blocking reasons;
- planned writes.

The exact normalized payload, proposed registry, and full response envelope should remain available as collapsed raw JSON diagnostics.

## Mutation Boundary

The Board may request a profile preview and may submit an explicit confirmation for that exact preview.

Confirmation must preserve the AIPOS-149 discipline:

```text
structured profile intent
-> normalize payload
-> existing profile validator
-> readable dry-run preview
-> actor match
-> expiry check
-> OWNER_CONFIRMED
-> snapshot revalidation
-> scoped workspace-local registry write
-> post-write validation
-> refresh Agents Summary / Detail
```

Rules:

- dry-run writes nothing;
- confirmation writes only:

```text
<workspace_root>/0_control_plane/agents/custom_agent_profiles.yaml
```

- stale, expired, actor-mismatched, mutated, or invalid previews BLOCK;
- input edits invalidate the previous preview;
- confirmation remains explicit and Owner-visible;
- no hidden queue, claim, task, role-registry, runtime, or policy mutation is authorized.

## Validation Semantics

The Board must surface AIPOS-148 / AIPOS-149 findings without weakening them.

### BLOCK

- invalid or missing canonical ID;
- duplicate canonical ID;
- collision with bundled canonical or legacy IDs;
- ambiguous legacy mapping;
- malformed provenance;
- prohibited secret-prone `runtime_env`;
- unsupported mutation outside the workspace-local registry;
- stale or actor-mismatched confirmation.

### NEEDS_OWNER

- any scoped workspace-local registry write;
- capability change;
- write-scope change;
- allowed-mode change;
- runtime-topology declaration change if later exposed;
- legacy mapping change;
- canonical-ID supersession;
- deactivation.

### WARN

- duplicate display name;
- missing display name;
- missing optional provenance;
- inactive historical profile retained for compatibility.

## Display And Audit Semantics

The Board should display:

```text
Primary Local Coding Session
agent-04
```

Rules:

- `display_name` is the primary readable label when present;
- canonical `agent_instance` remains visibly available for audit;
- display names may be duplicated but should warn;
- display names must not become identity aliases automatically;
- claims, matching, and audit identity continue to use canonical IDs after explicit resolution.

## Failure States

The Board must degrade visibly:

```text
invalid structured input
-> BLOCK with deterministic reasons

collision or ambiguous legacy mapping
-> BLOCK with deterministic reasons

stale or actor-mismatched confirm
-> BLOCK
-> zero writes

writer revalidation failure
-> BLOCK
-> visible registry validation result
```

Manual CLI profile authoring remains available when the Board surface is unavailable.

## Implementation Inventory

A later implementation proposal should inspect:

- `web/board/app.py`
- `web/board/static/index.html`
- `web/board/static/app.js`
- `web/board/static/styles.css`
- `web/board/tests/`
- `tests/playwright/board.visual.spec.js`
- `tools/aipos_cli/custom_agent_profiles.py`
- `tools/aipos_cli/agent_profiles.py`
- `tools/aipos_cli/tests/test_custom_agent_profiles.py`

The implementation slice must add focused tests for:

- upsert preview writes nothing;
- confirm writes and revalidates the scoped registry;
- display-name editing preserves canonical identity;
- free-form provenance remains accepted;
- capability changes remain Owner-visible;
- bundled collisions BLOCK with zero writes;
- stale, expired, and actor-mismatched confirms BLOCK;
- deactivate preserves history;
- supersede is additive and does not rewrite history;
- input edits invalidate the preview;
- Agents Summary / Detail refresh after confirm;
- no `runtime_env`, MCP, runtime, queue, claim, dependency, or controlled execute expansion;
- desktop and mobile Board layout.

## Deferred Owner Gates

Pause for a separate Owner decision before:

- implementing the Board panel;
- adding MCP profile authoring;
- adding raw YAML editing;
- adding runtime metadata authoring beyond the bounded form;
- accepting any credential or non-empty `runtime_env`;
- changing matching, claim, dependency, role-registry, or audit semantics;
- expanding controlled execute;
- launching runtimes;
- adding deployment or public endpoints.

## Non-Goals

AIPOS-162 does not:

- implement UI or backend code;
- alter the AIPOS-149 registry shape or writer;
- add raw YAML editing;
- store credentials;
- expose `runtime_env`;
- mutate bundled profiles;
- rewrite historical evidence;
- add MCP tools;
- expand controlled execute;
- change matching, claims, dependencies, audit, or task-class semantics;
- launch runtimes;
- add autonomous orchestration;
- change deployment or public endpoints.

