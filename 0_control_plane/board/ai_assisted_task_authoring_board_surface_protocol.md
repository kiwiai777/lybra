# AI-Assisted Task Authoring Board Surface Protocol

## Status

AIPOS-154 defines a protocol-only Board surface for fixture-only AI-assisted task authoring.

This document does not implement a Board panel, backend endpoint, validator, writer, adapter, credential source, network call, controlled execute operation, MCP tool, custom-profile form, queue mutation, runtime launch, autonomous orchestration, scheduler, polling, heartbeat, deployment change, or public endpoint.

## Purpose

AIPOS-152 dogfood confirmed that the fixture-only authoring edge behaves conservatively, but exposed two adoption frictions:

- intent input requires a JSON envelope instead of a natural-language-first surface;
- complete CLI JSON is useful evidence but verbose for routine Owner review.

The next bounded slice should expose the existing deterministic authoring edge through a Board workbench:

```text
natural-language requirement
-> explicit fixture-only preview request
-> readable AI proposal review
-> deterministic validation
-> explicit Owner confirmation
-> standard draft plus non-secret provenance sidecar
-> existing separate draft-publish flow
```

The Board is an authoring and review surface. It does not create a privileged task format, publish tasks automatically, or add LLM authority.

## Relationship To AIPOS-150 Through AIPOS-152

- AIPOS-150 defines the LLM-as-drafter semantic-intent boundary.
- AIPOS-151 implements a fixture-only CLI authoring edge with deterministic fixtures, standard draft convergence, Owner-confirmed write, and non-secret provenance sidecars.
- AIPOS-152 dogfoods that edge and recommends a Board authoring surface before any live BYO-LLM adapter.
- AIPOS-154 defines the Board interaction contract only. It does not reopen adapter, validator, writer, publish, matching, claim, or audit semantics.

## Board Workbench Placement

The future panel should appear as a dedicated `AI Task Authoring` workbench near the existing Board Decision Desk workflow.

It should use the same operator pattern already established by Decision Desk:

- co-locate input, readable preview, validation state, and explicit confirmation;
- keep raw structured JSON collapsed by default;
- surface visible PASS, NEEDS_OWNER, and BLOCK outcomes;
- preserve separate steps for authoring confirmation and later draft publication.

The new panel must not be merged implicitly with custom-profile authoring. Profile authoring remains a separately gated Board slice.

## Natural-Language Intent Input

The primary input is a plain-language task requirement.

The future fixture-only Board slice may expose:

- requirement text;
- actor;
- optional project hint;
- optional task-mode hint;
- optional task-class hint;
- optional priority hint;
- optional output-target hint;
- optional context-bundle hint;
- explicit bundled fixture selector for deterministic testing;
- optional visible `retry_of` reference for explicit manual retry.

Rules:

- requirement text is required;
- input remains an authoring intent, not an authoritative task card;
- secrets, credentials, private keys, raw tokens, and unrelated private workspace content must not be entered;
- candidate agent profiles are read-only inputs when exposed;
- any input edit invalidates the current preview and requires an explicit new preview;
- no preview request runs automatically while typing, on page load, or on a timer.

## Readable Owner Review

The review panel must prioritize a concise human-readable summary.

At minimum, show:

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
- authoring attempt status;
- non-secret provenance summary;
- planned standard draft path;
- planned provenance-sidecar path.

Raw structured proposal and preview envelopes remain available as collapsed diagnostics. They must not be the default review surface.

## Authority And Mutation Boundary

The Board may request an AI-assisted preview and may submit an explicit confirmation for that exact preview.

Confirmation must preserve the AIPOS-151 discipline:

```text
fixture-only proposal
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

- LLM or fixture output remains proposal-only;
- Owner confirms the authoritative draft shape;
- stale, expired, actor-mismatched, mutated, or invalid previews BLOCK;
- confirmation writes only the previewed standard draft and provenance sidecar;
- confirmation must not publish the draft;
- draft publication remains the existing separate `draft_publish` controlled flow;
- no queue, claim, completion, reopen, runtime, profile, or policy mutation is authorized.

## Editing, Discard, And Retry

The Board should support explicit operator actions:

- discard a local preview without writing files;
- edit intent inputs and request a fresh preview;
- visibly review BLOCK reasons;
- explicitly request a manual retry with visible `retry_of` provenance.

Rules:

- no automatic regeneration;
- no automatic retry;
- no provider fallback;
- no hidden repeated adapter invocation;
- changing intent, hints, actor, fixture, or retry metadata invalidates the previous confirmation state;
- retry remains a new visible authoring attempt.

Fixture identity remains deterministic. Any implementation must define collision-safe preview behavior before writing a retry into a workspace that already contains the fixture-generated task ID.

## Fixture-Only Boundary

The first Board implementation must reuse the bounded fixture-only adapter behavior from AIPOS-151.

It must not add:

- live HTTP or SSE calls;
- provider SDK dependencies;
- endpoint configuration;
- credential reads;
- `credential_ref`;
- environment-variable secret lookup;
- raw prompt persistence;
- raw response persistence;
- token billing;
- cost enforcement;
- provider timeout handling;
- provider fallback.

These remain a later live BYO-LLM slice with a separate Owner Decision Gate.

## Provenance Boundary

The Board should display non-secret authoring provenance sufficient for review:

- adapter ID;
- endpoint reference;
- model reference;
- prompt-template reference and version;
- request-config reference;
- attempt timestamp;
- attempt status;
- retry relationship;
- source-intent reference;
- token-cost estimate when available.

The sidecar path remains:

```text
<workspace_root>/5_tasks/records/authoring_provenance/<id>.md
```

Raw prompts and raw responses remain in-memory only and are discarded after the attempt.

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
- `tools/aipos_cli/board_adapter.py`
- `tools/aipos_cli/ai_authoring.py`
- `tools/aipos_cli/aipos_cli.py`
- existing Board tests
- existing AIPOS-151 CLI regression tests

The implementation slice must add focused Board tests for:

- natural-language input to fixture-only preview;
- readable review summary with collapsed raw JSON;
- standard preview `NEEDS_OWNER`;
- Owner-confirmed draft and sidecar write;
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
- adding live BYO-LLM network calls;
- reading any credential reference;
- adding provider-specific behavior;
- persisting raw prompts or raw responses;
- adding automatic retry or fallback;
- adding external-intake assist;
- adding MCP exposure;
- expanding controlled execute allowlists;
- changing publish, queue, claim, dependency, audit, runtime, or deployment behavior.

## Non-Goals

AIPOS-154 does not:

- implement UI or backend code;
- connect a live LLM;
- read or store credentials;
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
