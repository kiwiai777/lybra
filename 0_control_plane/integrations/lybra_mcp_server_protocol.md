# Lybra MCP Server Protocol and Boundary

## Purpose

AIPOS-96 defines the protocol boundary for a future Lybra MCP server surface.

The MCP server is a wrapper around existing Lybra Board/API and controlled execute semantics. It is a sibling surface to CLI and the local Dashboard / Board UI. It does not replace either surface and does not become the source of truth.

AIPOS-96 is protocol-only. It does not implement an MCP server, define live tool code, add transports, add backend routes, add CLI commands, add Web UI controls, expand controlled execute, create credentials, deploy services, expose public endpoints, register remote clients, mutate queues, write records, or enable autonomous planner runtime.

## Strategic Source

AIPOS-96 implements the MCP wrapping direction recorded in `DL-20260515-06` Decision 7:

```text
Lybra adds an MCP server wrapping layer while keeping Web UI, CLI, and Board adapter paths unchanged.
MCP server is a sibling surface to the Web Adapter, not a replacement.
Read tools may be available by default.
Write tools must round-trip through existing controlled execute.
MCP is protocol translation only and must not bypass owner_confirmed gates or expand the allowlist.
Tool names use lybra_<area>_<action>.
stdio and HTTP/SSE are supported as future transport options.
```

## Surface Model

Lybra has three peer surfaces:

- CLI for scripting, CI-friendly, and headless workflows
- Local Dashboard / Board UI for human review in a browser
- future MCP server for MCP-aware clients

All three surfaces must preserve the same backend authority model:

- files remain authoritative
- the Board/API contract remains the shared response envelope target
- controlled execute remains the only approved write gate for exposed mutations
- Owner Decision Gates remain non-delegable
- independent audit remains non-delegable

The MCP server must not add a hidden privileged path. If an operation is not available through the approved backend allowlist, the MCP server cannot expose it as a write operation.

## Transport Boundary

AIPOS-96 recognizes two future transport classes:

- `stdio` for local client integration
- `http_sse` for HTTP/SSE-style client integration

Transport support is a protocol option only. AIPOS-96 does not implement either transport.

Default posture remains local-first:

- no public endpoint by default
- no automatic remote client registration
- no bundled reverse proxy, TLS, OAuth, SSO, or API gateway
- no default bind to `0.0.0.0`
- no remote exposure without a later Owner-approved task

Any HTTP/SSE remote exposure, MCP service domain use, reverse proxy, tunnel, TLS, auth/RBAC, or client registration requires a separate Owner Decision Gate and independent audit.

## Tool Naming Convention

MCP tool names must use:

```text
lybra_<area>_<action>
```

Rules:

- use lowercase snake case
- keep names functional, not vendor-specific
- keep read and mutation verbs clear
- do not encode host names, transport names, model names, or client names
- do not use a tool name to imply authority that the backend does not grant

## Read Tool Classes

Read tools may be exposed by default only when they delegate to existing read-only backend/API semantics and produce no durable writes.

Recommended read tool names:

```yaml
read_tools:
  lybra_queue_list:
    delegates_to: board_api_queue_read
    writes_allowed: false
  lybra_task_preview:
    delegates_to: board_api_task_preview
    writes_allowed: false
  lybra_task_get:
    delegates_to: board_api_task_detail
    writes_allowed: false
  lybra_drafts_list:
    delegates_to: board_api_drafts_read
    writes_allowed: false
  lybra_records_get:
    delegates_to: board_api_records_read
    writes_allowed: false
  lybra_agents_list:
    delegates_to: board_api_agents_read
    writes_allowed: false
  lybra_validate:
    delegates_to: validation_read
    writes_allowed: false
  lybra_orchestration_summary:
    delegates_to: orchestration_summary_preview
    writes_allowed: false
  lybra_orchestration_timeline:
    delegates_to: orchestration_timeline_read
    writes_allowed: false
  lybra_context_pack_build:
    delegates_to: context_pack_preview
    writes_allowed: false
  lybra_sessionstore_search:
    delegates_to: future_sessionstore_read_only_search
    writes_allowed: false
```

`lybra_sessionstore_search` remains future-facing until the SessionStore read path is implemented by a later approved task. AIPOS-96 does not implement that path.

Read tool outputs must preserve existing redaction, safety notice, warning, and error behavior. MCP clients must not receive secrets that the backend would not show through CLI or Dashboard surfaces.

## Controlled Mutation Tool Classes

Mutation tools may be exposed only as protocol translations to existing controlled execute paths.

Reserved mutation tool names:

```yaml
controlled_mutation_tools:
  lybra_intake_submit:
    operation: intake_submit
    controlled_execute_required: true
  lybra_owner_decision_record:
    operation: owner_decision_record
    controlled_execute_required: true
  lybra_queue_claim:
    operation: queue_claim
    controlled_execute_required: true
  lybra_draft_create:
    operation: draft_create
    controlled_execute_required: true
  lybra_draft_publish:
    operation: draft_publish
    controlled_execute_required: true
  lybra_planner_iteration_append:
    operation: planner_iteration_append
    controlled_execute_required: true
  lybra_orchestration_event_append:
    operation: orchestration_event_append
    controlled_execute_required: true
  lybra_sessionstore_append:
    operation: future_sessionstore_append
    controlled_execute_required: true
```

Reserved names do not enable operations. They describe future mapping targets only.

AIPOS-109 implements the first stdio MCP controlled write-tool pair for `intake_submit`:

```text
lybra_intake_submit_dry_run
lybra_intake_submit_confirm
```

AIPOS-113 implements the stdio MCP controlled write-tool pair for `owner_decision_record` after AIPOS-112 enabled the backend writer:

```text
lybra_owner_decision_record_dry_run
lybra_owner_decision_record_confirm
```

Both pairs remain scope-gated by `LYBRA_CAPABILITY_TOKEN`, preserve dry-run/confirm sequencing, and delegate to controlled execute. They do not enable HTTP/SSE transport, client auto-registration, publish tools, queue mutation tools, direct file writes, token minting, or credential verification.

The MCP layer must not:

- add an operation to the controlled execute allowlist
- infer Owner confirmation
- self-confirm on behalf of Owner
- bypass dry-run
- bypass token and snapshot revalidation
- bypass actor matching
- bypass blocking reasons
- bypass Owner Decision Gates
- convert a blocked operation into a warning
- perform direct file writes

If an operation is not enabled by the backend controlled execute allowlist, the MCP tool must return a blocked or unavailable response.

## Controlled Execute Round Trip

For any enabled mutation, the MCP round trip must preserve the existing lifecycle:

1. caller requests dry-run preview through the MCP tool
2. backend returns the normalized preview envelope
3. caller reviews warnings, planned writes, planned moves, Owner requirements, blocking reasons, and dry-run token metadata
4. caller submits explicit confirm request with dry-run proof
5. backend performs immediate revalidation
6. backend blocks stale, mismatched, unsafe, unauthorized, or Owner-gated requests
7. backend executes only through the approved writer
8. backend returns the normalized execute envelope

The MCP server is only a transport and schema translation surface. It must not become a writer.

## Suggested Tool Descriptor Shape

Future MCP tool descriptors may use:

```yaml
mcp_tool:
  name:
  surface: mcp
  transport:
    stdio_supported: true
    http_sse_supported: false
  operation_class: read_only
  backend_delegate:
  controlled_execute_operation:
  dry_run_required:
  confirmation_required:
  owner_gate_preserved: true
  writes_allowed: false
  secrets_allowed_in_payload: false
  response_envelope: board_api
  file_authority_preserved: true
  audit:
    independent_audit_required_for_enablement: true
```

AIPOS-96 does not create live descriptor files.

## Client Registration Boundary

MCP-aware clients may later be configured by the Owner or user to call Lybra.

AIPOS-96 does not:

- auto-register tools with Claude Code, Codex, or other clients
- write client config files
- install desktop plugins
- push tool schemas to remote services
- expose discovery endpoints
- create access tokens
- grant remote execution authority

Client registration is a future deployment or local setup concern and requires its own Owner approval when it affects credentials, network exposure, or remote access.

## Endpoint Convention Relationship

AIPOS-85 records `http://mcp.kiwiai.cloud` as an Owner-private MCP service domain convention.

AIPOS-96 does not implement that domain, route traffic to it, configure DNS, configure reverse proxy, configure TLS, or deploy MCP services. The endpoint convention remains documentation until a later deployment task.

## Relationship To Existing Protocols

- AIPOS-34 defines the Board/API response contract that MCP responses should preserve where applicable.
- AIPOS-35 defines the Local Board Adapter boundary that MCP must not bypass.
- AIPOS-37 and AIPOS-38 define controlled execute lifecycle and MVP behavior.
- AIPOS-55 through AIPOS-80 define existing Web UI and controlled execute paths that MCP must not expand secretly.
- AIPOS-77 defines controlled append persistence gates for planner iteration and orchestration event appends.
- AIPOS-88 defines the Kiwiai server directory policy as an environment policy, not an MCP deployment.
- AIPOS-89 defines private dogfood runbook checks, not MCP enablement.
- AIPOS-90 defines sandbox runtime provider boundaries; MCP is not a sandbox provider.
- AIPOS-91D defines the local Dashboard as a peer surface beside CLI and future MCP.
- AIPOS-92 defines SessionStore protocol boundaries; MCP SessionStore tools remain future and must preserve those boundaries.
- AIPOS-95 defines Anthropic SDK compatibility; MCP integration remains separate from SDK compatibility.
- AIPOS-109 defines the first MCP-native controlled write-tool discipline through `intake_submit`.
- AIPOS-112 defines the controlled `owner_decision_record` writer that AIPOS-113 exposes through MCP.
- AIPOS-113 adds the stdio MCP `owner_decision_record` dry-run/confirm wrapper without adding HTTP/SSE, Board UI, or direct writes.

## Owner Decision Gates

Owner approval is required before:

- implementing MCP server code
- enabling any transport
- exposing HTTP/SSE beyond loopback
- registering clients
- adding MCP tools to runtime configuration outside approved local stdio slices
- adding write-capable MCP tools beyond approved controlled execute wrappers
- expanding controlled execute allowlist
- connecting MCP to cloud agents or remote clients
- using `http://mcp.kiwiai.cloud`
- adding auth/RBAC, TLS, reverse proxy, or tunnel configuration
- granting credential access through MCP
- routing SessionStore writes through MCP
- enabling polling, scheduling, publishing, claiming, or planner ticks through MCP

## Non-Goals

AIPOS-96 does not implement:

- MCP server code
- MCP tool definition code
- stdio transport
- HTTP/SSE transport
- backend routes
- Web UI controls
- CLI commands
- controlled execute allowlist expansion
- new write operations
- queue mutation
- draft mutation beyond existing controlled execute boundaries
- records writing
- orchestration writing
- SessionStore writing
- SessionStore search implementation
- sandbox runtime launch
- SDK client integration
- client auto-registration
- remote agent connection
- automatic polling
- automatic scheduling
- automatic publish
- automatic claim
- autonomous planner runtime
- credentials
- token minting
- auth/RBAC
- database
- reverse proxy
- TLS
- DNS configuration
- deployment configuration
- public endpoint behavior
- IPFS plugin marketplace
- federation
- git automation
- automatic commit or push
- automatic finalize
- self-audit
