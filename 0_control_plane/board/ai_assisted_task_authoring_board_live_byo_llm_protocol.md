# AI-Assisted Task Authoring Board Live BYO-LLM Protocol

## Status

AIPOS-160 defines a protocol-only Board live workbench for AI-assisted task authoring.

This document does not implement a Board panel, backend endpoint, validator, writer, adapter, credential source, network call, controlled execute operation, MCP tool, custom-profile form, queue mutation, runtime launch, autonomous orchestration, scheduler, polling, heartbeat, deployment change, or public endpoint.

## Purpose

AIPOS-154 established the Board surface contract for fixture-only AI-assisted task authoring. AIPOS-156 and AIPOS-157 established the first live BYO-LLM CLI slice and its conservative implementation boundary.

The next bounded slice is the Board workbench that exposes that live edge without collapsing the fixture-only surface, without merging custom-profile authoring into the same surface, and without reopening deterministic validation and Owner-confirmation discipline.

The Board remains an authoring and review surface. It does not create a privileged task format, publish tasks automatically, or grant LLM output any authority beyond proposal generation.

## Relationship To Existing Protocols

- AIPOS-154 defines the fixture-only Board surface contract.
- AIPOS-156 defines the live BYO-LLM CLI protocol boundary.
- AIPOS-157 implements the first live BYO-LLM CLI slice.
- AIPOS-158 covers live-adapter error-path coverage.
- AIPOS-149 and AIPOS-148 keep custom-profile authoring separate and presentation-only for display names.

The Board live slice must reuse the same deterministic confirmation discipline as the CLI live slice rather than inventing a second execution model.

## Board Workbench Placement

The future panel should appear in the existing Decision Desk area as the same `AI Task Authoring` workbench with an explicit mode choice:

- fixture-only mode;
- live BYO-LLM mode.

The workbench should keep the existing natural-language-first entry, readable Owner review, and explicit confirm flow. The new live mode must be visibly separate from custom-profile authoring and from external-intake review.

## Live Mode Inputs

The Board live mode may surface only provider-neutral references and hints, not raw secrets:

- requirement text;
- actor;
- optional project hint;
- optional task-mode hint;
- optional task-class hint;
- optional priority hint;
- optional output-target hint;
- optional context-bundle hint;
- explicit bundled fixture selector when the operator is previewing or retrying;
- `endpoint_ref`;
- `credential_ref` as an `env:NAME` reference only;
- `provider_ref`;
- `model_ref`;
- `request_config_ref`;
- `request_timeout_seconds`;
- `max_output_tokens`;
- visible `retry_of` reference.

Rules:

- requirement text remains required;
- `credential_ref` must be a reference, not a secret;
- raw credential material must never be typed into or stored by the Board;
- any edit invalidates the previous preview;
- no preview runs automatically while typing, on page load, or on a timer.

## Readable Owner Review

The Board review panel should prioritize a concise human-readable summary of the live attempt:

- original natural-language requirement;
- proposed title and body preview;
- proposed task metadata;
- recommended `task_class`;
- complexity rationale;
- proposed assignee and canonical `agent_instance` when present;
- editable `display_name` only as presentation metadata when available;
- proposed reviewer or auditor when present;
- assumptions;
- missing-information questions;
- possible Owner Decision Gates;
- deterministic validation findings;
- live attempt status;
- non-secret provenance summary;
- planned standard draft path;
- planned provenance-sidecar path.

Raw structured proposal, request, and response envelopes remain available only as collapsed diagnostics.

## Authority And Mutation Boundary

The Board may request a live preview and may submit an explicit confirmation for that exact preview.

Confirmation must preserve the AIPOS-157 discipline:

```text
live proposal request
-> deterministic policy validation
-> readable dry-run preview
-> actor match
-> expiry check
-> OWNER_CONFIRMED
-> snapshot revalidation
-> standard draft write
-> non-secret provenance-sidecar write
-> post-write validation
```

Rules:

- live adapter output remains proposal-only;
- Owner confirms the authoritative draft shape;
- stale, expired, actor-mismatched, mutated, or invalid previews BLOCK;
- confirmation writes only the previewed standard draft and provenance sidecar;
- confirmation must not publish the draft;
- draft publication remains the existing separate `draft_publish` controlled flow;
- no queue, claim, completion, reopen, runtime, profile, or policy mutation is authorized.

## Credential, Retry, And Failure Boundary

The Board live slice should remain conservative:

- `credential_ref` resolution happens through an environment-based reference only;
- missing or malformed `credential_ref` configuration BLOCKS visibly;
- live adapter failures, network failures, parse failures, timeout failures, and provider mismatch failures fail visibly with zero writes;
- retry remains an explicit manual invocation with visible `retry_of` provenance;
- no automatic retry;
- no provider fallback;
- no hidden repeated adapter invocation.

## Provenance Boundary

The Board should display non-secret authoring provenance sufficient for review:

- adapter ID;
- provider reference;
- endpoint reference;
- model reference;
- prompt-template reference and version;
- request-config reference;
- request timeout;
- max output tokens;
- attempt timestamp;
- attempt status;
- retry relationship;
- source-intent reference;
- token-cost estimate when available.

The provenance sidecar path remains:

```text
<workspace_root>/5_tasks/records/authoring_provenance/<id>.md
```

Raw prompts and raw responses remain in-memory only and are discarded after the attempt unless a later Owner-gated privacy/storage task explicitly changes that policy.

## Custom Profile Boundary

Workspace-local profiles from AIPOS-149 may be read for recommendation context and presentation.

The Board may display:

- `display_name` as the primary readable label;
- canonical `agent_instance` visibly for audit;
- explicit capability or provenance fields when useful for review.

The Board must not:

- create, edit, deactivate, or supersede profiles;
- treat `display_name` as identity;
- infer authority, capability, or independence from labels;
- combine profile authoring into this slice without a separate Owner scope decision.

## Failure States

The Board must degrade visibly:

```text
invalid input
-> BLOCK with deterministic reasons

injection or authority-escalation proposal
-> BLOCK with prohibited actions visible

fixture adapter failure
-> visible failed attempt
-> zero writes

live adapter failure
-> visible failed attempt
-> zero writes

stale or actor-mismatched confirm
-> BLOCK
-> zero writes
```

Manual deterministic draft creation remains available when AI-assisted authoring is unavailable or rejected.

## Implementation Inventory

A later implementation proposal should inspect:

- `web/board/static/index.html`
- `web/board/static/app.js`
- `web/board/static/styles.css`
- `web/board/app.py`
- `tools/aipos_cli/ai_assisted_authoring.py`
- `tools/aipos_cli/aipos_cli.py`
- existing Board tests
- existing AIPOS-151 and AIPOS-157 CLI regression tests

The implementation slice must add focused Board tests for:

- natural-language input to fixture-only preview;
- natural-language input to live preview;
- readable review summary with collapsed raw JSON;
- standard preview `NEEDS_OWNER`;
- Owner-confirmed draft and sidecar write;
- live credential-ref handling through `env:NAME`;
- injection BLOCK with zero writes;
- adapter-failure visibility with zero writes;
- edit invalidates preview;
- stale, expired, and actor-mismatched confirms BLOCK;
- no automatic retry;
- publication remains separate;
- custom-profile mutation remains absent.

## Deferred Owner Gates

Pause for a separate Owner decision before:

- implementing the Board panel;
- combining AI-assisted authoring Board UI with custom-profile Board UI;
- adding raw credential entry;
- adding live adapter secrets anywhere other than environment-based resolution;
- persisting raw prompts or raw responses;
- adding automatic retry or fallback;
- adding external-intake assist;
- adding MCP exposure;
- expanding controlled execute allowlists;
- changing publish, queue, claim, dependency, audit, runtime, or deployment behavior.

## Non-Goals

AIPOS-160 does not:

- implement UI or backend code;
- connect a live LLM;
- read or store raw credentials;
- persist raw prompts or raw responses;
- add automatic retry, fallback, polling, or background work;
- add custom-profile Board authoring;
- add external-intake assist;
- add MCP tools;
- expand controlled execute;
- publish drafts;
- mutate queues;
- change matching, claims, dependencies, audit, or task-class semantics;
- launch runtimes;
- add autonomous orchestration;
- change deployment or public endpoints.

