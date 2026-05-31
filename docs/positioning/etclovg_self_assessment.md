# ETCLOVG Self-Assessment Map

## Status

This document is a positioning and self-assessment snapshot for Lybra.

It maps the current product to the ETCLOVG taxonomy described by *Agent Harness Engineering: A Survey*. It does not add protocol requirements, enable runtime behavior, change authority boundaries, or claim complete coverage of any layer.

Source:

- [Agent Harness Engineering: A Survey](https://picrew.github.io/LLM-Harness/)

Assessment date:

```text
2026-05-31
```

## Positioning Summary

Lybra is a local-first, file-authoritative agent harness for governed work.

Its strongest emphasis is:

```text
Governance + Verification + Observability
```

Lybra is intentionally human-in-the-loop. Files act as the durable state substrate: task cards, drafts, records, decisions, orchestration events, and planner iterations remain inspectable and reconstructable outside any one model session.

Lybra is not an autonomous agent runtime platform. It does not currently aim to maximize unattended execution throughput. Its design priority is to keep work visible, reviewable, and recoverable while allowing selected local mutations through explicit gates.

## Reading The Map

Coverage labels in this document mean:

| Label | Meaning |
|---|---|
| `substantial` | Multiple implemented surfaces exist and the layer is a primary product emphasis. |
| `partial` | Useful implemented primitives exist, but the layer is intentionally incomplete. |
| `protocol-direction` | Public protocols describe a direction, but the implementation remains limited or absent. |
| `deliberate-blank` | Lybra intentionally does not implement the capability in its current boundary. |

The labels are relative assessments, not benchmark scores.

## Layer Map

| Layer | Coverage | Current Lybra mapping | Deliberate boundary |
|---|---|---|---|
| Execution | `partial` | Explicit local Docker sandbox adapter for one bounded ephemeral worker run; read-only workspace mount; no credentials injected; default `--network none`; in-memory structured report. | No autonomous sandbox launch, managed sandbox provider connection, writable mount by default, background worker fleet, or durable runtime authority. |
| Tooling | `partial` | CLI, local Board UI, MCP stdio, and loopback HTTP/SSE are peer surfaces over shared backend semantics. Selected writes use controlled execute. MCP write visibility is capability-token scoped. | No privileged MCP path, public MCP endpoint by default, automatic client registration, broad mutation surface, or automatic allowlist expansion. |
| Context | `partial` | File-authoritative workspaces, task cards, context bundles, read-only Context Pack previews, explicit source refs, product/workspace separation, and task-scoped isolation metadata. | No external RAG backend, semantic retrieval service, automatic memory promotion, explicit contradiction resolver, or general staleness/provenance engine yet. |
| Lifecycle | `partial` | Queue states, draft-to-publish flow, task claims, session and claim records, task complexity classes, append-only orchestration events, planner iterations, and manual planner-loop previews. | No autonomous orchestration loop, scheduler, queue polling daemon, agent launcher, heartbeat implementation, or background recovery worker. |
| Observability | `partial` | Plain-file records, append-only orchestration events and planner iterations, Board Needs Owner surface, validation diagnostics, orchestration summaries, orchestration timelines, and explicit dry-run envelopes. | No unified distributed tracing backend, live telemetry service, cost dashboard, alert manager, or trace-native audit workflow yet. |
| Verification | `substantial` | Queue and draft validators, path and state checks, dry-run previews, snapshot revalidation, explicit confirmation, blocking verdicts, regression tests, and independent audit before finalize for governed work. | No trace-derived outcome attribution, benchmark suite for harness regressions, automatic evaluator authority, or self-audit. |
| Governance | `substantial` | Owner Decision Gates, independent audit separation, task-scoped authority, `task_class: simple \| complex`, controlled execute, capability-token scopes, file-authoritative boundaries, MCP transport token boundary, and sandbox restrictions. | No autonomous policy expansion, self-confirmation, self-finalize, centralized RBAC service, token minting service, or adaptive gate-intensity implementation. |

## Execution

### Implemented

Lybra includes a local Docker sandbox MVP for one explicit bounded run:

```bash
python3 -m tools.sandbox_runtime local-docker run --dry-run --image <local-image> -- echo hello
```

The adapter requires an explicit image, uses `--pull never`, defaults to `--network none`, mounts an optional workspace read-only, injects no credentials, and returns a structured in-memory report.

### Protocol Direction

The sandbox abstraction protocol describes provider-agnostic lifecycle phases:

```text
create -> inject -> execute -> report -> destroy
```

It keeps runtime state subordinate to the file-authoritative control plane.

### Intentionally Not Implemented

Lybra does not launch autonomous workers, connect managed sandbox providers, create long-running agents, or treat hot runtime state as durable authority.

## Tooling

### Implemented

Lybra exposes peer surfaces:

- CLI for scripting and headless checks
- local Board UI for operator review
- MCP stdio for same-host clients
- loopback HTTP/SSE MCP transport

Selected MCP mutations delegate to the same controlled execute path as CLI and Board. They preserve dry-run, token proof, snapshot revalidation, explicit confirm, and backend blocking reasons.

### Intentionally Not Implemented

MCP is not a privileged control channel. Lybra does not expose arbitrary file writes, publish tools, queue mutation tools, runtime launch tools, or public remote endpoints by default.

## Context

### Implemented

Lybra uses files as durable context inputs:

- task cards
- context bundles
- Context Pack previews
- queue state
- records
- orchestration summaries and timelines
- explicit workspace roots

The Context Pack builder is read-only. It assembles bounded briefing material without writing memory, mutating queues, appending logs, executing agents, or automating Git.

### Known Gap

The file-authoritative direction is a useful state-estimation substrate, but Lybra does not yet provide a general staleness, provenance, or contradiction-handling model. That remains a later evidence-driven protocol task.

## Lifecycle

### Implemented

Lybra represents lifecycle through visible files and controlled transitions:

```text
draft -> publish -> pending -> claimed -> completed | blocked
```

It also records claims, sessions, orchestration events, planner iterations, and task complexity classification. Complex-class work carries the governed planner, independent audit, repair/re-audit, and PASS-before-finalize loop.

### Intentionally Not Implemented

Lifecycle advancement is not autonomous. Lybra does not ship a scheduler, queue polling daemon, background planner, agent launcher, heartbeat writer, or automatic recovery loop.

## Observability

### Implemented

Lybra provides inspectable evidence:

- task files and queue directory state
- session records and claim logs
- append-only orchestration events
- append-only planner iterations
- validation diagnostics
- Board Needs Owner items
- orchestration summary and timeline previews
- controlled execute envelopes with planned and performed writes or moves

### Known Gap

These are trace-like primitives, not a complete observability platform. Audit currently consumes final artifacts and explicit records more than execution traces. Trace-native audit remains a later protocol direction.

## Verification

### Implemented

Lybra verification is deliberately layered:

- file and schema validation
- queue state consistency checks
- safe path checks
- draft validation
- dry-run previews
- snapshot comparison and execute-time revalidation
- explicit confirmation for controlled writes
- regression tests
- independent audit before finalize for governed work

### Intentionally Not Implemented

Lybra does not let the executing agent self-audit or self-finalize. It also does not yet compute outcome attribution or regression analysis from traces.

## Governance

### Implemented

Governance is Lybra's primary differentiation:

- Owner Decision Gates stop architecture, scope, risk, credential, runtime, deployment, audit-boundary, publication, and long-term-direction forks.
- Execution authority, independent audit authority, and Owner decision authority remain separate.
- Controlled execute limits mutation paths.
- MCP capability tokens scope selected write-tool visibility.
- HTTP/SSE MCP transport uses a local Bearer-token boundary.
- Sandbox execution remains explicit, local, minimal, and credential-free by default.
- `task_class: simple | complex` selects workflow rigor independently from content-oriented `task_mode`.

### Intentionally Not Implemented

Lybra does not automatically broaden authority, infer Owner confirmation, expand mutation allowlists, mint credentials, lower gate density, or finalize its own work.

## Deliberate Blanks

The following blanks are product boundaries, not accidental omissions:

| Area | Deliberate blank | Reason |
|---|---|---|
| Execution | Autonomous runtime launch | Keep execution authority explicit and Owner-supervised. |
| Lifecycle | Autonomous orchestration loop | Preserve visible planner ticks and stop conditions before adding unattended advancement. |
| Lifecycle | Heartbeat and background lease recovery | Avoid creating hidden runtime state or a daemon before a separate architecture decision. |
| Tooling | Public remote MCP service | Keep the default posture local-first and avoid implicit deployment or credential expansion. |
| Context | General external RAG and automatic memory promotion | Keep context bounded and file-authoritative until a separate retrieval boundary is approved. |
| Governance | Automatic allowlist expansion and self-confirmation | Keep authority changes non-delegable. |
| Governance | Adaptive gate-intensity control | Defer tuning until full multi-agent dogfood produces friction evidence. |

## State Estimation Interpretation

Lybra's file-authoritative design can be interpreted as an early state-estimation strategy:

```text
durable files + append-only records + explicit validation
-> reconstructable operational state
```

This is an inference from the current architecture, not a claim that Lybra already implements a complete state-estimation subsystem.

The next reliability step should be to define explicit staleness markers, provenance chains, and contradiction semantics only after complete multi-agent closed-loop dogfood exposes concrete failure modes.

## Follow-On Questions

This map informs later work without approving implementation:

1. Which state freshness and provenance gaps appear during complete multi-agent closed-loop dogfood?
2. Which existing session, claim, and orchestration artifacts are sufficient inputs for trace-native audit?
3. Which Owner gates are load-bearing, and which produce repeated friction that may justify a later configurable gate-intensity policy?
4. Which deliberate blanks should remain permanent positioning boundaries rather than backlog items?

## Non-Goals

This document does not:

- define a new protocol
- change existing protocol requirements
- implement code
- add a runtime
- add a scheduler
- add an agent launcher
- add heartbeat behavior
- expand MCP tools
- expand controlled execute
- change credentials
- change deployment
- mutate a workspace
- approve State Staleness and Provenance Protocol
- approve Trace-Native Audit Protocol
- approve Adaptive Simplification and Gate Intensity Protocol

