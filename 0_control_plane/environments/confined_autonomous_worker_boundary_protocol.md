# Confined Autonomous Worker Boundary Protocol

## Status

AIPOS-195 defines the protocol-only Layer 2 filesystem and credential wall for running full-capability autonomous agent harnesses under Lybra governance.

This document does not implement a sandbox launcher, Docker adapter change, worker runtime, scheduler, polling loop, heartbeat, daemon, credential proxy, network proxy, public endpoint, MCP tool, controlled-execute operation, queue mutation, records writer, finalize writer, accepted-work unblock, active lease writer, Delegated or Standing automation, Trace-Native Audit, live BYO-LLM behavior, external-intake behavior, adoption manifest, signature system, or remediation writer.

## 1. Purpose

AIPOS-191 exposed a concrete failure mode: a capable autonomous harness can bypass Lybra gates when it has ordinary shell and filesystem access to the workspace. It can write queue files, records-looking artifacts, reports, or product code directly.

AIPOS-193 and AIPOS-194 provide Layer 3 detection:

```text
bypass write exists
-> provenance authority scanner detects the gap
-> invalid or quarantined file is excluded from effective truth
```

AIPOS-195 defines Layer 2 prevention:

```text
full-capability agent runs inside a confined worker boundary
truth files and privileged credentials are outside the writable sandbox
the only consequential write path is the MCP gate
```

The goal is to make the Lybra gate the only available route for queue, record, audit, and workspace-truth mutations while preserving Lybra's core posture: Lybra is a gate, not an engine.

## 2. Threat Model

Target harnesses include Claude Code, Codex, Hermes, OpenClaw, and similar full-capability autonomous or semi-autonomous tools.

Assumed capabilities inside an unconstrained environment:

- shell command execution;
- file reads and writes;
- network calls;
- local process launches allowed by the host environment;
- ability to modify queue files, records, product code, or local configuration if those paths are mounted writable;
- ability to use any credentials visible in the process environment or filesystem.

AIPOS-195 addresses local filesystem and credential boundaries. It does not solve data-loss prevention or general network egress control in v0.

## 3. Relationship To Existing Protocols

### AIPOS-90

AIPOS-90 defines the provider-agnostic sandbox runtime abstraction. It keeps the file-authoritative control plane as durable truth and requires provider-specific Owner gates before enabling a provider or expanding runtime authority.

AIPOS-195 is a specialized Layer 2 boundary on top of that abstraction for autonomous workers that need to connect to the Lybra MCP gate and, when required, an LLM provider.

### AIPOS-101

AIPOS-101 implements the first `local_docker` MVP as a bounded `ephemeral_worker` with:

- explicit image selection;
- shell-free Docker argv construction;
- `--pull never`;
- `--network none`;
- read-only workspace mounts;
- no credential injection;
- in-memory structured reporting.

AIPOS-195 does not change AIPOS-101. AIPOS-196 may implement a separate confined-autonomous-worker slice because the AIPOS-101 `--network none` default is too strict for a worker that must reach the MCP gate and an LLM API.

### AIPOS-193 / AIPOS-194

AIPOS-193/194 define and implement Layer 3 provenance-authority detection.

Layer 2 and Layer 3 compose as follows:

```text
Layer 2 wall:
  prevent direct writes to truth files and privileged credentials

Layer 3 detection:
  if the wall has a hole, detect gate-bypass files and exclude them from effective truth
```

Neither layer grants finalize authority, accepted-work unblock authority, active lease authority, or autonomous delegation authority.

## 4. Confined Autonomous Worker Model

A confined autonomous worker is a short-lived sandboxed agent process created for one explicit, bounded Owner-approved work interval.

The worker contains the agent harness. The Lybra gate runs outside the worker.

Required model:

```text
host:
  Lybra service-mode gate:
    Board
    MCP HTTP/SSE
    token registry
    workspace truth

sandbox:
  agent harness
  read-only task/context projection
  writable scratch/artifacts directory
  role-scoped MCP token
  optional agent-owned LLM key
```

The worker may perform cognition, edit scratch artifacts, and call the MCP gate. It must not have direct writable access to Lybra truth.

## 5. Filesystem Boundary

The sandbox filesystem boundary must separate context, scratch output, and authoritative truth.

### Read-Only Context Projection

The agent must receive a read-only task/context projection, not a writable Lybra workspace.

The projection should be derived from the existing Context Pack path where practical. It may include:

- the current task card or selected safe task fields;
- task-specific acceptance criteria;
- relevant context bundle excerpts;
- explicitly approved source refs or summarized file excerpts;
- non-secret Board or validation summaries;
- the MCP endpoint reference and non-secret role metadata.

The projection must explicitly exclude:

- `.lybra/local/connection.json`;
- raw service-mode role tokens;
- `5_tasks/records/**`;
- other tasks' queue cards unless explicitly included as read-only context;
- `5_tasks/queue/**` as a writable tree;
- product repository write paths;
- private workspace management directories outside the task context;
- git credentials, deployment credentials, npm tokens, SSH keys, cloud credentials, or other state-changing secrets.

This projection boundary is a role-isolation requirement, not a convenience feature. If the worker can read every role token or all records, it can confuse accountability even when it cannot write truth.

### Truth Paths Not Writable

The worker must not be able to write:

```text
5_tasks/queue/**
5_tasks/records/**
5_tasks/drafts/**
5_tasks/orchestration/**
.lybra/local/**
product repo paths
host filesystem paths outside approved mounts
```

For v0, it is acceptable to provide no direct access to those paths at all. If a future implementation provides read-only access to selected truth files, the selection must be explicit, task-scoped, and recorded in the worker launch report.

### Writable Scratch

The worker may receive a writable scratch directory.

Required properties:

- agent can write;
- gate can read after the worker reports completion;
- scratch is outside Lybra truth paths;
- scratch is scoped to one task, role, and worker run;
- scratch content is not effective Lybra truth until the gate ingests or links it through an Owner-confirmed return path.

The worker must not be able to use scratch as a symlink or bind-mount escape into truth paths or host filesystem paths.

### No Privilege Escalation

The worker must not have:

- sudo;
- Docker socket access;
- host PID namespace control;
- host mount control;
- permission to modify the sandbox configuration;
- permission to read host secrets;
- permission to reconfigure the Lybra gate.

Any implementation exception requires an Owner Decision Gate and must be treated as a security boundary change.

## 6. Artifact Handoff Model

The worker writes proposed outputs into scratch.

The gate is responsible for accepting those outputs into a workspace-controlled artifact location during an Owner-confirmed return operation.

Canonical handoff:

```text
1. worker writes scratch artifact
2. worker calls queue_return dry-run with scratch artifact refs
3. Owner reviews preview and confirms
4. gate reads scratch artifact
5. gate copies or records the artifact into a workspace-controlled artifact path
6. task card / return record references the gate-written artifact path
```

In this model, `artifact_refs` in durable queue/record state should point to gate-ingested artifact paths, not arbitrary sandbox-internal paths.

AIPOS-196 should define the concrete local path convention and copying discipline for this gate ingestion step. This is an implementation point, not a new authority class. It must preserve dry-run, Owner confirmation, snapshot revalidation, and provenance record discipline.

## 7. Credential Boundary

The sandbox may receive only the credentials required for its role.

Allowed in v0:

- the worker role's MCP token, injected at launch time;
- the agent harness's own LLM key, if the harness requires one and the Owner approves that specific injection;
- non-secret endpoint refs and role metadata.

Forbidden in v0:

- git push credentials;
- deployment credentials;
- cloud control-plane credentials;
- npm publish tokens;
- SSH private keys for unrelated systems;
- database credentials;
- service account credentials;
- raw service-mode tokens for other roles;
- `.lybra/local/connection.json`;
- long-lived broad `.env` files.

Role tokens and LLM keys must not be baked into images, written into the context projection, committed to git, persisted in records, copied into task cards, printed in logs, or surfaced in Board output.

Credential references may appear in reports only by non-secret ref or redacted fingerprint.

## 8. Network Posture

AIPOS-195 v0 does not require `--network none`.

The confined worker must be able to reach:

- the local Lybra MCP gate endpoint;
- the agent's LLM provider endpoint when the selected harness requires it.

The gate itself must remain local-first. It must not bind to a public interface merely to make the sandbox connection work.

### Concrete Reachability

AIPOS-196 must choose and document a concrete local reachability posture.

Acceptable v0 candidates include:

- Docker bridge plus `host-gateway` / `host.docker.internal` mapping to reach a host-bound gate;
- a private Docker network where only the gate endpoint and worker are attached;
- host networking only if explicitly approved and documented as weaker isolation.

Host networking must not be described as filesystem or process isolation. It may simplify loopback reachability, but it weakens network separation and must be called out as such if used.

### Egress

Network egress allowlisting and DLP are deferred.

Residual risk:

```text
accountability is not data-loss prevention.
```

Open egress can still leak information or call external systems. The trigger for the deferred egress gate is a real requirement to prevent disclosure or constrain third-party system access.

## 9. Single Write Gate And Layer 3 Composition

The confined worker's only consequential write channel into Lybra truth is the MCP gate.

Examples:

- claim: `lybra_queue_claim_*`;
- return: `lybra_queue_return_*`;
- audit dispatch: `lybra_audit_dispatch_*`;
- audit verdict: `lybra_audit_verdict_*`.

Each mutation remains:

```text
dry-run -> Owner review -> OWNER_CONFIRMED -> confirm -> durable provenance
```

If a direct write still appears in truth paths, AIPOS-193/194 must classify it through provenance-authority detection and exclude invalid or quarantined files from effective truth.

## 10. Role Isolation And Anti-Self-Audit

Executor and auditor workers must run in separate sandbox instances.

Required separation:

- separate sandbox instance;
- separate role MCP token;
- separate canonical agent instance;
- separate scratch directory;
- no shared writable truth path;
- no access to the other role's raw token;
- no reliance on instance-id string parsing or display-name semantics.

The executor token must not expose `audit_verdict`. The auditor token must not expose executor-only return authority unless explicitly assigned for that role. Owner-dispatch authority must remain separate when used.

Distinctness is enforced by both environment separation and existing AIPOS-146/147 canonical identity / distinctness evaluation.

The executor must not be able to self-audit by writing records directly, claiming the audit task as the same instance, or using an auditor token.

## 11. Lifecycle And Red Lines

A confined autonomous worker is ephemeral.

Allowed lifecycle:

```text
Owner or gate-supervised operator explicitly starts one worker
worker receives bounded task context and role token
worker performs bounded work
worker returns proposed artifacts through the gate
worker is destroyed
```

Forbidden lifecycle behavior:

- Lybra server-side scheduler;
- queue polling;
- heartbeat;
- daemonized agent runtime;
- auto-restart loop;
- background worker fleet;
- autonomous task selection outside explicit gate policy;
- service that continues after the approved worker interval;
- agent launch triggered merely by MCP connection or queue state.

The boundary is a wall around an explicitly started worker, not an engine that drives agents.

## 12. Harness Adaptation Points

The protocol is harness-agnostic.

Each harness implementation should specify:

- image or runtime base;
- how the harness CLI is installed;
- how the task prompt/context projection is passed;
- how the role MCP token is injected;
- how the MCP endpoint is configured;
- how the LLM key is injected, if required;
- where scratch is mounted;
- how artifacts are named;
- how exit status and transcripts are collected without secrets;
- how teardown is verified.

Initial AIPOS-196 recommendation: implement one `local_docker` confined worker path for Claude Code first, because it is the immediate AIPOS-191B target. Other harnesses remain later slices.

## 13. Recommended AIPOS-196 Thin Implementation Slice

AIPOS-196 should implement a narrow local Docker confined worker slice:

1. Use service-mode MCP as the host-side gate.
2. Create a task-scoped read-only context projection derived from Context Pack.
3. Mount the projection read-only.
4. Mount scratch writable.
5. Do not mount full workspace truth writable.
6. Do not mount `.lybra/local/`.
7. Do not mount the product repo writable.
8. Inject only one role MCP token and, if approved, the harness's LLM key.
9. Use a documented local network posture that reaches gate and LLM without binding the gate to a public address.
10. Run one concrete harness, preferably Claude Code, inside the sandbox.
11. Complete one claim -> work -> return path through MCP.
12. Have the gate ingest scratch artifacts during `queue_return confirm` and write durable refs to gate-controlled artifact paths.
13. Produce a non-secret worker report with mounts, network posture, credential refs, artifact refs, and teardown status.

This slice should not implement egress allowlisting, signatures, adoption manifests, finalize writer, accepted-work unblock, active lease writer, Delegated/Standing, or automated worker orchestration.

## 14. AIPOS-196 Audit Requirements

AIPOS-196 independent audit must include real-path evidence. Mock-only review is insufficient.

Required adversarial checks from inside the sandbox:

- attempt to write `5_tasks/queue/**` and verify failure;
- attempt to write `5_tasks/records/**` and verify failure;
- attempt to write `.lybra/local/**` and verify failure or absence;
- attempt to write product repo paths and verify failure;
- attempt to access host filesystem paths outside approved mounts and verify failure;
- attempt to use executor token for auditor-only tools and verify scope denial;
- attempt to use auditor token for executor-only tools and verify scope denial;
- verify the only successful truth mutation path is the MCP gate;
- verify scratch artifacts become durable only after gate ingestion during Owner-confirmed return;
- verify no raw token or LLM key appears in projection, task cards, records, worker reports, Board output, or git-tracked files.

The audit must also verify:

- the worker reaches the gate and LLM under the documented network posture;
- the gate remains non-public;
- teardown removes or invalidates the worker environment;
- Layer 3 scanner still detects direct filesystem injection if an injection is performed outside the sandbox.

This follows the AIPOS-190 lesson: boundary and launch-like features need real spawn and probe evidence, not only static review or unit tests.

## 15. Deferred Gates And Non-Goals

Deferred gates:

- egress allowlist or DLP;
- cryptographic gate signatures / authority seals;
- adoption manifest implementation;
- quarantine remediation writer;
- finalize writer;
- accepted-work unblock;
- active lease writer / lease activation;
- Delegated / Standing automation;
- Trace-Native Audit;
- runtime loop, daemon, scheduler, polling, heartbeat;
- public endpoint, reverse proxy, TLS/OAuth, cloud or multi-user MCP model;
- credential proxy;
- live BYO-LLM expansion;
- external-intake expansion;
- broad harness fleet support beyond the first approved implementation slice.

Non-goals:

- do not rewrite history;
- do not infer role or authority from agent instance id strings;
- do not narrow Lybra away from full-capability autonomous agent users;
- do not treat scratch files as truth;
- do not let sandbox implementation replace Owner confirmation;
- do not turn Lybra into an agent engine.

## 16. Roadmap Notes

AIPOS-191B true autonomous heterogeneous rerun should wait until:

```text
Layer 3: AIPOS-193/194 provenance-authority detection
+ Layer 2: AIPOS-195/196 confined filesystem and credential boundary
```

Recommended sequence:

1. AIPOS-195 protocol.
2. AIPOS-196 local Docker confined worker implementation for one harness.
3. AIPOS-196 adversarial audit with real sandbox writes and MCP gate path.
4. AIPOS-191B service-mode true heterogeneous autonomous rerun.
5. Consider a service-mode / Gate+Wall+Detection stage archive after durable cross-layer dogfood evidence exists.
