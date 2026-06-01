# AI-Assisted Task Authoring Protocol

## Status

AIPOS-150 defines a protocol-only semantic-intent authoring boundary.

This document does not implement an LLM adapter, provider client, endpoint configuration, credential storage, prompt template, validator, writer, CLI command, Board form, MCP tool, controlled execute operation, queue mutation, profile mutation, runtime launch, autonomous orchestration, scheduler, polling, heartbeat, deployment change, or public endpoint.

## Purpose

Lybra task cards are explicit, file-authoritative, diffable work objects. That structure is valuable after a task exists, but asking every Owner to manually fill structured task metadata creates avoidable authoring friction.

AIPOS-150 adds a semantic-intent layer:

```text
natural-language requirement
-> optional AI-assisted normalization and triage recommendation
-> Lybra standard task-card draft
-> deterministic validation
-> Owner review and confirmation
-> existing publish and execution workflow
```

The LLM is a drafter, not an authority. The authoritative path begins only after a generated proposal becomes an ordinary Lybra draft and passes the same deterministic review and confirmation boundaries as any other draft.

## Why This Matters For Simple Tasks

`task_class: simple` remains useful in Lybra even when no planner loop is required.

The value is not extra ceremony. The value is a lightweight governed record:

```text
natural-language intent
-> reviewable draft
-> explicit Owner-approved task class
-> provenance
-> deterministic task card
-> auditable completion trail
```

A bare prompt sent directly to an agent does not provide the same durable provenance, explicit assignment, review boundary, or reconstructable task state.

## Peer Intake Surface

AI-assisted task authoring is a new intake surface beside external intake.

It does not replace:

- direct Owner-authored drafts
- planner-authored drafts
- normalized external intake drafts
- deterministic task validation
- Owner Decision Gates
- controlled publish
- queue state

Peer-surface model:

```text
direct Owner input -----------\
planner proposal -------------+-> standard Lybra draft -> deterministic review path
normalized external intake ---+
AI-assisted authoring --------/
```

AI-assisted authoring must converge on the existing draft shape. It must not create a privileged shadow task format or a second publication path.

## Authority Model

### LLM-As-Drafter

The configured LLM may:

- summarize the Owner's natural-language requirement
- propose a concise title
- propose a task-card body
- recommend `task_class: simple | complex`
- explain the complexity recommendation
- recommend `task_mode`
- recommend project, context bundle, model tier, priority, and output target when enough local metadata is supplied
- recommend `assigned_to`, `agent_instance`, reviewer, and auditor candidates from an explicit supplied candidate set
- identify missing information
- identify possible Owner Decision Gates
- return confidence notes and assumptions

The configured LLM must not:

- approve its own draft
- decide the authoritative `task_class`
- publish a draft
- write a task card
- mutate a queue
- claim, complete, block, or reopen a task
- create or edit custom agent profiles
- silently expand capabilities, write scopes, allowed modes, or controlled execute allowlists
- infer agent capabilities from instance-ID strings or display names
- grant audit independence
- satisfy an Owner Decision Gate
- act as an independent auditor
- act as the Owner
- launch runtimes or tools
- perform autonomous follow-up

### Owner Confirmation

The Owner confirms the authoritative task shape.

At minimum, review must expose:

- original natural-language requirement
- normalized title and body
- proposed task metadata
- recommended `task_class`
- `complexity_note` or recommendation rationale
- proposed assignee and concrete instance when present
- proposed reviewer or auditor when present
- assumptions
- missing-information questions
- possible Owner Decision Gates
- provenance for the authoring attempt

The Owner may accept, edit, reject, or request a new draft. Accepting a draft does not bypass the existing deterministic validator or publish gate.

## Semantic Intent Contract

### Input Shape

A future implementation may accept an intent envelope shaped like:

```yaml
intent_id:
submitted_at:
submitted_by:
requirement:
project_hint:
task_mode_hint:
task_class_hint:
priority_hint:
output_target_hint:
context_bundle_hint:
candidate_agent_instances: []
candidate_reviewers: []
candidate_auditors: []
linked_refs: []
```

Rules:

- `requirement` is required natural-language text.
- Hints are advisory.
- Candidate lists are explicit bounded inputs, not an invitation for the LLM to invent profiles.
- Input should include only the minimum context needed to draft the task.
- Secrets, credentials, private keys, raw tokens, and unrelated private workspace content must not be included.
- External-intake platform-native payloads remain outside this surface.

### Output Shape

A future adapter should return a structured proposal shaped like:

```yaml
authoring_attempt:
  attempt_id:
  intent_id:
  adapter_id:
  generated_at:
  provider_ref:
  model_ref:
  prompt_template_ref:
  request_config_ref:
  source_refs: []
  status: drafted | blocked | failed | timed_out
proposal:
  frontmatter:
    task_id:
    title:
    project:
    assigned_to:
    agent_instance:
    context_bundle:
    task_mode:
    task_class:
    complexity_note:
    model_tier:
    priority:
    status: pending
    created_by:
    needs_owner: true
    output_target:
    artifact_policy:
  body:
triage:
  recommended_task_class:
  rationale:
  confidence:
  assumptions: []
  missing_information: []
  possible_owner_gates: []
assignment_recommendations:
  assigned_to:
  agent_instance:
  reviewer:
  audit_by:
  rationale:
warnings: []
```

The adapter output is a proposal envelope, not an authoritative task card.

Rules:

- A proposal must remain visibly marked as AI-assisted.
- Generated metadata must still pass the existing deterministic draft validator.
- `needs_owner: true` remains set until Owner review resolves the draft.
- Missing or invalid required fields remain visible and block publication.
- Unknown values must stay missing or explicit `unknown`; the adapter must not fabricate certainty.
- The later implementation must preserve the original intent and authoring provenance without storing secrets.

## Complexity Triage Semantics

The LLM may recommend:

```text
simple
complex
```

It must provide a short rationale.

The recommendation is advisory:

```text
LLM recommendation
!= authoritative task_class
```

Owner review confirms or edits `task_class`.

The existing AIPOS-139/AIPOS-140 semantics remain authoritative:

- `task_mode` describes content or operation type.
- `task_class` selects workflow rigor.
- `simple` defaults to standalone completion unless another policy requires more.
- `complex` requires the governed planner, executor, independent audit, repair or re-audit, and PASS-before-finalize loop.
- Code mode does not mechanically force `complex`.
- Non-code mode does not mechanically imply `simple`.

The LLM must not bypass the existing warning for code-mode work classified as `simple`.

## Assignment Recommendation Semantics

The LLM may recommend:

- logical `assigned_to`
- concrete `agent_instance`
- reviewer candidate
- auditor candidate

Recommendations must be grounded in an explicit candidate set derived from currently readable profiles and policy inputs.

Rules:

- Canonical `agent_instance` remains the stable opaque identity key.
- `display_name` may be shown to the Owner for readability.
- The LLM must not infer capabilities, authority, independence, vendor, harness, model family, host, or trust from canonical-ID strings or display names.
- Matching continues to use explicit profile fields.
- Reviewer and auditor independence remains an explicit deterministic policy check.
- Missing suitable candidates must produce a visible unresolved recommendation, not an invented profile.
- AI-assisted authoring must not create, edit, deactivate, or supersede a custom agent profile.

## BYO-LLM Adapter Boundary

### Provider Neutrality

Lybra must not require a specific LLM provider.

A future adapter configuration may describe:

```yaml
adapter_id:
endpoint_ref:
credential_ref:
model_ref:
request_timeout_seconds:
max_output_tokens:
cost_policy_ref:
prompt_template_ref:
enabled:
```

Rules:

- `endpoint_ref` identifies an Owner-configured endpoint or adapter target.
- `credential_ref` is a reference only. Raw keys must not be stored in task cards, drafts, logs, workspace registries, prompts committed to git, or authoring provenance.
- Credentials come from an Owner-configured secret source outside file-authoritative task state.
- Provider-specific request and response translation stays at the adapter edge.
- Core Lybra draft, validator, review, publish, queue, and audit semantics remain provider-neutral.

### Edge Isolation

The LLM call is networked, non-deterministic, and potentially billable. It must stay outside the authoritative deterministic pipeline.

```text
authoring edge:
  optional network call
  variable latency
  non-deterministic output
  explicit cost boundary

deterministic core:
  proposal parsing
  schema validation
  Owner review
  draft write
  publish
  queue state
  audit trail
```

The LLM adapter must not receive filesystem write authority, queue authority, controlled execute authority, MCP write authority, runtime-launch authority, or credentials beyond the narrowly configured provider request credential.

### Failure And Timeout Handling

Failure must degrade safely.

Expected outcomes:

```text
adapter unavailable
-> authoring attempt failed
-> Owner may retry, switch adapter, or author manually

timeout
-> authoring attempt timed_out
-> no draft publish
-> no hidden retry loop

invalid structured output
-> proposal blocked
-> expose parse or validation reasons

partial recommendation
-> preserve visible missing fields
-> Owner may edit manually

provider cost limit reached
-> proposal blocked or failed visibly
-> no automatic fallback that spends against another provider
```

Rules:

- No background polling.
- No autonomous retry loop.
- No provider fallback without explicit Owner policy.
- No hidden repeated billable calls.
- Retry is an explicit user action or a separately Owner-approved bounded policy.
- A failed AI authoring attempt must never block manual deterministic draft creation.

### Non-Determinism And Cost Visibility

A future implementation should preserve non-secret authoring provenance sufficient for review:

- adapter ID
- endpoint reference
- model reference
- prompt-template reference or version
- request configuration reference
- attempt timestamp
- attempt status
- retry relationship when applicable
- source intent reference
- token or cost estimate when available

Exact provider response retention is a separate privacy and storage decision. AIPOS-150 does not authorize raw prompt or raw response persistence by default.

## Prompt Injection And Untrusted Text

Natural-language requirements may contain instructions that conflict with Lybra policy.

Rules:

- Treat requirement text and linked source text as untrusted authoring input.
- The LLM may summarize and propose; it does not receive tool authority.
- Generated output does not bypass deterministic validation.
- Requests to reveal credentials, modify policy, skip Owner review, weaken audit, launch tools, or mutate queues must remain blocked.
- External-intake text must preserve its own normalization and privacy boundary before it is optionally passed into AI-assisted drafting.
- The later implementation must define size limits, redaction behavior, and linked-context allowlists before enabling live calls.

## Draft Review And Decision Handoff

The target integration shape is:

```text
intent envelope
-> optional AI proposal envelope
-> Owner review
-> standard Lybra task draft
-> existing deterministic validation
-> existing decision and publish path
-> existing queue
```

For external intake:

```text
normalized external intake draft
-> optional AI-assisted rewrite or triage proposal
-> Owner review
-> existing external-intake decision path
```

Rules:

- External intake and AI-assisted authoring remain separate peer surfaces.
- AI-assisted drafting may help normalize or rewrite an external-intake draft only after the external-intake privacy boundary has already been satisfied.
- AI-assisted output must not publish an external-intake draft automatically.
- Existing source references such as `source_tag`, `client_tag`, and `external_ref` must remain preserved when relevant.

## Provenance And Auditability

A future implementation should preserve enough information to reconstruct:

- who submitted the intent
- which intent produced the proposal
- whether AI assistance was used
- which adapter and model reference produced the proposal
- when the attempt happened
- whether the attempt drafted, failed, timed out, or was rejected
- which proposal the Owner reviewed
- which fields the Owner changed before confirmation
- which standard Lybra draft was ultimately written

Authoring provenance is evidence. It does not grant authority.

## Relationship To Existing Protocols

### AIPOS-106 External Intake

External intake remains a normalized integration boundary. AI-assisted authoring is a peer intake surface and may optionally assist after external normalization. It does not ingest platform-native payloads or bypass external-intake privacy controls.

### AIPOS-139 And AIPOS-140 Task Complexity

The LLM may recommend `task_class`, but the Owner confirms it. Existing explicit `simple | complex` semantics remain unchanged.

### AIPOS-148 And AIPOS-149 Custom Agent Profiles

AI-assisted assignment recommendations may refer to readable registered instances and `display_name` values for presentation. They must not invent identities, edit profiles, infer capabilities from labels, or bypass deterministic matching and independence checks.

### Existing Draft And Publish Flow

AI-assisted proposals converge on the ordinary task-card draft path. Existing validator, decision, publish, queue, and audit behavior remain authoritative.

### Credential Boundary

BYO-LLM keys remain outside file-authoritative task state. Raw credentials must not enter prompts, task cards, drafts, logs, git, MCP payloads, or authoring provenance.

## Implementation Gates

Any implementation requires a separate Owner-approved, independently audited task.

Pause for Owner decision before selecting:

- first surface: CLI-only, Board-only, or CLI plus Board
- initial adapter contract and provider-neutral configuration shape
- secret-source mechanism for `credential_ref`
- whether live network calls are enabled in the first slice or only adapter dry-run fixtures
- prompt-template ownership and versioning
- input size limits and linked-context allowlist
- redaction policy
- authoring-attempt provenance file location and retention
- raw prompt or response retention policy
- retry policy
- cost visibility shape
- standard draft write integration
- external-intake optional-assist integration timing
- MCP read or write exposure

Each expansion point is a separate Owner Decision Gate where it changes architecture, credentials, network authority, storage, cost, privacy, deployment, or audit boundaries.

## Deferred Implementation Inventory

A later implementation proposal should inspect:

- future BYO-LLM adapter protocol and configuration schema
- future authoring-attempt provenance schema
- future semantic-intent payload schema
- future structured proposal schema
- `tools/aipos_cli/draft_writer.py`
- `tools/aipos_cli/draft_validator.py`
- `tools/aipos_cli/external_intake_writer.py`
- `tools/aipos_cli/aipos_cli.py`
- `0_control_plane/tasks/task_creation_flow.md`
- `0_control_plane/board/task_creation_form.md`
- custom profile reader surfaces from AIPOS-149
- future Board authoring surface only after separate scope approval
- MCP exposure only after separate scope approval

## Non-Goals

AIPOS-150 does not:

- call an LLM
- choose a provider
- define a live endpoint
- store or read a credential
- add a network client
- add an adapter implementation
- add provider SDK dependencies
- define raw prompt or response persistence
- create a live semantic-intent schema file
- write a task draft
- publish a task
- mutate a queue
- create or edit custom agent profiles
- change matching or claim behavior
- change `task_class` validation
- change reviewer or auditor independence
- add CLI commands
- add Board forms
- add MCP tools
- expand controlled execute allowlists
- launch agents or runtimes
- add autonomous orchestration
- add a scheduler, polling loop, heartbeat, background retry, credential, deployment, or public endpoint

## Next Decision

After independent audit, Owner may finalize AIPOS-150 as the protocol gate and choose whether to approve a separately scoped AI-Assisted Task Authoring implementation proposal.

The deferred custom-agent profile Board UI slice remains queued after AI-assisted task authoring is closed out.
