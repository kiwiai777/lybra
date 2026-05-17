# Sandbox Runtime Abstraction Protocol

## Purpose

AIPOS-90 defines the protocol boundary for future Lybra sandbox runtime adapters.

The sandbox runtime adapter must be provider-agnostic. Lybra must not bind its core control plane to a single sandbox or managed-agent provider.

This protocol is documentation and governance only. It does not launch containers, create sandboxes, add SDK clients, configure cloud providers, create credentials, connect agents, add backend routes, add queue polling, or enable autonomous runtime.

## Strategic Source

AIPOS-90 implements the sandbox-runtime portion of `DL-20260513-04`.

Accepted provider evaluation order:

```text
local_docker -> e2b -> cubesandbox -> anthropic_managed
```

Each provider requires a separate Owner Decision Gate and a separate AIPOS task before implementation.

## Core Principle

The file-authoritative control plane remains the source of truth.

Sandbox runtimes may accelerate execution, hold hot-path state, or run isolated work, but they must not become the durable state authority. Every approved runtime tick boundary, decision point, fork, result report, and handoff must round-trip through approved file-backed append or record paths before it is considered durable.

Runtime state must be rebuildable or discardable from files and approved external state references.

## Adapter Descriptor

Future adapter declarations should use a descriptor with these fields:

```yaml
adapter_id: local_docker.default
provider: local_docker
provider_status: candidate
execution_model: idle_agent
lifecycle:
  create: defined
  inject: defined
  execute: defined
  report: defined
  destroy: defined
resource_limits:
  cpu_limit: owner_approved_required
  memory_limit: owner_approved_required
  disk_limit: owner_approved_required
  wall_clock_timeout: owner_approved_required
  idle_timeout: owner_approved_required
  network_policy: localhost_or_private_by_default
credential_boundary:
  credential_mode: owner_approved_required
  token_scope: task_scoped_when_available
  token_ttl: expires_on_destroy_when_available
  long_lived_env_allowed: false_for_sandbox_worker_sessionstore_access
workspace_boundary:
  product_repo_ref: explicit
  workspace_root_ref: explicit
  writable_paths: owner_approved_required
  private_data_scope: explicit
file_authority:
  tick_round_trip_required: true
  decision_round_trip_required: true
  fork_round_trip_required: true
  report_round_trip_required: true
owner_gates:
  provider_enablement: required
  credential_boundary: required
  network_expansion: required
  write_scope: required
  model_or_agent_authority_expansion: required
audit:
  independent_audit_required: true
```

AIPOS-90 does not create live adapter descriptor files. The example above is a schema target, not an enabled provider.

## Provider Values

Supported protocol-level provider values are:

- `local_docker`
- `e2b`
- `cubesandbox`
- `anthropic_managed`

No provider is enabled by AIPOS-90.

Provider implementation tasks must define:

- provider-specific lifecycle mapping
- credential source and revocation model
- network boundary
- filesystem mount or workspace injection boundary
- resource limits
- report format
- teardown behavior
- rollback behavior
- audit expectations

## Lifecycle Phases

The adapter lifecycle has five required protocol phases.

### create

`create` allocates or selects an execution environment.

It must not imply public network exposure, long-running service enablement, credential creation, or task claim by itself.

Required future outputs:

- provider
- sandbox/session/environment identifier
- execution model
- resource limits
- credential boundary reference
- network boundary
- workspace boundary
- expiration or teardown expectation

### inject

`inject` supplies approved task context, context packs, workspace references, and scoped credentials to the runtime.

It must not inject unredacted secrets into reports or durable logs. It must not broaden workspace write scope without Owner approval.

Required future inputs:

- task id or orchestration id
- context bundle or context pack reference
- workspace root reference
- allowed artifact scope
- allowed memory scope
- credential boundary reference

### execute

`execute` performs a bounded unit of runtime work.

Execution must respect task scope, resource limits, credential boundaries, model-tier boundaries, and Owner gates. Runtime work does not become durable until the approved report or append path records it.

`execute` must stop before architecture, scope, risk, security, new-service, model-tier, credential, audit-boundary, paid-resource, irreversible, external publish, commit, push, or finalize forks.

### report

`report` returns runtime output to the file-authoritative control plane.

Reports must be explicit about:

- command or action attempted
- artifacts created
- files read or proposed for write
- credentials used by reference only
- network access used
- failures and retry count
- required Owner decisions
- recommended next action

AIPOS-90 does not implement any report writer. Future tasks must route reports through approved append-only writers, records, or controlled execute paths.

### destroy

`destroy` tears down or releases the runtime boundary.

It must revoke or expire task-scoped capability tokens when available, stop ephemeral workers, and produce enough report metadata for post-run audit.

Destroy failure is a needs-owner condition when it may leave credentials, public ports, writable mounts, or paid resources active.

## Runtime Execution Models

The protocol supports two execution models.

### idle_agent

An `idle_agent` is a longer-lived agent or worker that can remain available between tasks.

This model is Docker/systemd friendly, but AIPOS-90 does not install systemd, add Docker Compose, or launch Docker. Idle agents still require explicit task claim, session lease, credential boundary, write scope, and Owner gates before doing work.

### ephemeral_worker

An `ephemeral_worker` is a short-lived sandbox intended to be created for a bounded task or tick and destroyed afterward.

This model is E2B/CubeSandbox/managed-environment friendly. It should prefer task-scoped capability tokens that expire on destroy rather than long-lived `.env` credentials.

## Credential Boundary

Sandbox worker access to future SessionStore must use task-scoped capability tokens when available.

AIPOS-90 preserves the AIPOS-88D decision that SessionStore access should not rely on long-lived `.env` credentials for sandbox workers. The concrete SessionStore schema, physical location, token minting service, revocation service, and credential storage boundary are deferred to AIPOS-92 or a later Owner-approved task.

Provider implementations must never commit:

- API keys
- sandbox provider tokens
- service account credentials
- private keys
- database passwords
- OAuth secrets
- long-lived `.env` files

## Network Boundary

Default network posture is private and minimal:

- no public port exposure by default
- no database public ports
- localhost or private-network binding by default
- no MCP exposure unless separately approved
- no external provider connection unless that provider has passed an Owner Decision Gate

Any public endpoint, reverse proxy, TLS, DNS, Cloudflare/Nginx, cross-service network, MCP, paid provider, or external data egress expansion requires Owner approval.

AIPOS-96 defines MCP as a sibling protocol surface to CLI and the local Dashboard / Board UI. MCP is not a sandbox runtime provider, does not launch sandboxes, and does not grant runtime authority. Connecting MCP to sandbox providers, remote agents, or cloud clients remains a separate Owner Decision Gate.

## File Authority and Round Trip

Future sandbox runtime output must round-trip to files through approved paths.

Required durable events include:

- runtime created
- context injected
- execution started
- execution result reported
- Owner decision requested
- fork detected
- runtime destroyed
- teardown failure

AIPOS-90 does not define the concrete event schema for Session Tree, SessionStore, or Subtask DAG. Those remain future AIPOS tasks.

## Owner Decision Gates

Owner approval is required before:

- enabling any provider
- adding provider SDK dependencies
- creating Dockerfiles or Docker Compose files
- creating or mounting sandbox filesystems
- creating provider credentials
- changing credential storage
- minting capability tokens
- granting workspace write scope
- exposing network ports
- connecting MCP
- allowing paid-resource usage
- enabling autonomous planner/runtime execution
- expanding model tier or agent authority
- changing audit boundaries
- implementing provider-specific runtime code
- performing external publish, commit, push, or finalize

## Relationship to Future Tasks

AIPOS-90 only defines the sandbox runtime abstraction boundary.

Future tasks remain separate:

- AIPOS-91: Session Tree Primitives (fork / rollback / clone)
- AIPOS-92: SessionStore Schema and Credential Boundary Protocol
- AIPOS-93: Subtask DAG Fanout/Join Schema Extension
- AIPOS-94: Planner Autonomy Tier Protocol
- AIPOS-95: Anthropic SDK Compatibility Adapter Protocol

## AIPOS-95 Anthropic SDK Compatibility

AIPOS-95 defines a shallow compatibility adapter for SDK-shaped agent, session, environment, skill, vault, and multiagent concepts.

The adapter may later help an `anthropic_managed` provider implementation translate between Lybra protocol metadata and SDK-shaped requests, but it does not enable the provider.

Sandbox runtime descriptors may reference:

```yaml
sdk_compatibility:
  adapter_ref: 0_control_plane/integrations/anthropic_sdk_compatibility_adapter_protocol.md
  sdk_shape_alignment: advisory
  sdk_dependency_required: false
  sdk_types_define_core_model: false
```

Lybra file-backed tasks, orchestration, session leases, SessionStore, agent capability profiles, runtime profiles, Owner decisions, and audit reports remain authoritative.

Provider-specific implementation tasks must be created separately after Owner approval.

## Non-goals

AIPOS-90 does not implement:

- Docker runtime launch
- Dockerfile or Docker Compose files
- systemd services
- E2B client code
- CubeSandbox client code
- Anthropic managed environment client code
- anthropic-sdk-python dependency
- provider credentials
- SessionStore deployment
- token minting service
- MCP deployment
- backend routes
- Web UI controls
- controlled execute allowlist expansion
- queue claiming
- queue polling
- records writing
- draft publishing
- orchestration append writers
- Session Tree operations
- Subtask DAG execution
- autonomous planner runtime
- public endpoint behavior
- auth/RBAC
- database
- git automation
- automatic commit/push/finalize
