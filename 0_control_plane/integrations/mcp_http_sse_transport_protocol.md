# MCP HTTP/SSE Transport Protocol

## Purpose

AIPOS-123 defines the protocol boundary for adding HTTP/SSE transport to the Lybra MCP server.

The goal is to let MCP-aware agents actively connect to Lybra over a network transport while preserving the same authority model already used by stdio MCP, CLI, and the local Board. Lybra does not spawn agents, poll external agents, or auto-register clients. Agents initiate the connection.

AIPOS-123 is protocol-only. It does not implement an HTTP server, add dependencies, open ports, add TLS, add reverse proxy configuration, add authentication code, change stdio behavior, add MCP tools, change controlled execute, mutate workspace files, or deploy a service.

## Relationship To Existing Protocols

- AIPOS-96 defines MCP as a sibling surface to CLI and Board.
- AIPOS-100 implements the current stdio MCP MVP.
- AIPOS-109 defines MCP-native write discipline for `intake_submit`.
- AIPOS-113 extends that discipline to `owner_decision_record`.
- AIPOS-77 controls all approved durable mutations through dry-run, token, snapshot revalidation, and explicit confirm.
- DL-20260516-01 Decision 11 keeps local dashboard and future MCP surfaces peer surfaces, not privileged entry points.

This protocol narrows the HTTP/SSE transport boundary. It does not replace AIPOS-96.

## Transport Model

Lybra recognizes two MCP transport families:

```yaml
transport_families:
  stdio:
    status: implemented
    default_scope: local_process
  http_sse:
    status: protocol_defined_implementation_pending
    default_scope: loopback_only
```

HTTP/SSE transport must use the same tool registry and tool handlers as stdio unless a later audited implementation task proves that a transport-specific adapter is required. Tool semantics must not differ by transport.

## Default Bind Boundary

The HTTP/SSE server default bind address must be:

```text
127.0.0.1
```

Rules:

- no default bind to `0.0.0.0`
- no public endpoint by default
- no built-in TLS or certificate management
- no bundled nginx, Caddy, tunnel, or reverse proxy configuration
- no automatic DNS or service-domain configuration
- no client auto-discovery or auto-registration

Remote access, if desired by the Owner, must be provided by user-managed reverse proxy, tunnel, private network, or deployment infrastructure outside the product default. Documentation may include examples only after a separate Owner-approved deployment/security task.

## Authentication Boundary

HTTP/SSE authentication is an unresolved Owner Decision Gate before implementation.

Allowed candidate families for the implementation planning discussion:

```yaml
auth_candidates:
  bearer_token:
    description: static or managed token carried in Authorization header
    implementation_status: undecided
  hmac:
    description: signed request or session challenge
    implementation_status: undecided
  mtls:
    description: mutual TLS terminated by a proxy or the server
    implementation_status: undecided
  ip_allowlist:
    description: network-level allowlist, usually proxy-owned
    implementation_status: undecided
  local_only_no_auth:
    description: loopback-only development prototype
    implementation_status: undecided
```

AIPOS-123 does not choose one. AIPOS-124, or any future implementation task, must stop for Owner approval before selecting and implementing an authentication method.

Authentication must be transport access control only. It must not grant write authority. Write authority remains controlled by MCP tool scope visibility and controlled execute.

## Capability Token Delivery

The stdio MVP currently uses `LYBRA_CAPABILITY_TOKEN` to scope visible write tools.

HTTP/SSE needs a future Owner-approved delivery mechanism. Candidate delivery paths are:

```yaml
capability_token_delivery_candidates:
  authorization_header:
    example: Authorization: Bearer <capability-token>
    status: undecided
  mcp_initialize_metadata:
    example: initialize request metadata contains capability token
    status: undecided
  per_tool_call_argument:
    example: each write tool call includes capability token
    status: discouraged_for_default_but_undecided
```

The selected delivery mechanism must preserve the AIPOS-109/AIPOS-113 rule:

- read tools remain visible by default
- write tools are visible only when the connection/session capability includes the matching operation scope
- scope denial returns structured teaching errors
- hidden tools are not callable through alternate transport routes

## Tool Visibility

Tool list behavior must be transport-invariant.

```yaml
tool_visibility:
  read_tools:
    visible_without_write_scope: true
  intake_submit_tools:
    visible_when_scope_contains: intake_submit
  owner_decision_record_tools:
    visible_when_scope_contains: owner_decision_record
  publish_or_queue_mutation_tools:
    visible_when_scope_contains: future_owner_approved_scope
    status: not_implemented
```

HTTP/SSE must not expose a write tool that stdio would hide for the same capability state.

If capability changes during a live session, the MVP implementation should prefer a simple reconnect/reinitialize requirement unless Owner approves dynamic scope refresh. Dynamic tool-list mutation is not required by this protocol.

## Controlled Execute Preservation

HTTP/SSE changes only how MCP messages are transported. It does not change how writes are authorized or executed.

Every write-capable MCP tool must preserve:

1. dry-run request
2. planned writes / planned moves preview
3. blocking reasons
4. warnings
5. dry-run token
6. snapshot hash
7. explicit confirm call
8. immediate token and snapshot revalidation
9. actor / capability validation
10. backend controlled writer execution only

HTTP/SSE must not:

- auto-confirm a dry-run
- infer Owner approval from a network credential
- turn blocking reasons into warnings
- bypass token expiry
- bypass snapshot mismatch checks
- write files directly
- add controlled execute allowlist operations
- publish drafts or mutate queues without a separately approved tool and backend writer

## Session Lifecycle

HTTP/SSE session semantics require an implementation decision. AIPOS-123 defines the required questions, not the answer.

Future implementation must define:

- whether each HTTP request is stateless or bound to a server-side MCP session
- how the SSE stream maps to JSON-RPC request/response lifecycle
- whether tool-list scope is evaluated once at initialize or on every list/call
- session timeout
- reconnect behavior
- duplicate request handling
- shutdown behavior
- whether any session state is durable

Default recommendation for AIPOS-124 MVP:

```yaml
recommended_mvp_session_model:
  state: in_memory_only
  durability: none
  reconnect: client_reinitialize_required
  scope_refresh: reconnect_required
  timeout: owner_decision_required
```

No session state may become a source of truth. Files remain authoritative.

## Port Convention

The default HTTP/SSE port is an Owner Decision Gate.

Candidate convention:

```yaml
candidate_default:
  host: 127.0.0.1
  port: 8766
```

AIPOS-123 does not finalize this port. The implementation task must request Owner approval before choosing a default port and before documenting user-facing startup commands.

## Logging Boundary

HTTP/SSE must treat protocol responses as structured client traffic and operational logs as separate diagnostics.

Rules:

- no durable log file by default
- no logging of raw capability tokens
- no logging of raw secret-bearing payload fields
- no logging to stdout if stdout is a protocol channel in a selected implementation mode
- stderr diagnostics are acceptable for local development
- any persistent log path requires a later Owner Decision Gate

If a future reverse proxy is used, proxy logs are outside Lybra's product default and must be documented as deployment responsibility.

## Error Response Discipline

HTTP/SSE errors must preserve MCP-native teaching responses.

Structured error responses should include:

```json
{
  "error_code": "SCOPE_DENIED",
  "message": "Human-readable explanation.",
  "suggested_next_action": "Reconnect with a capability token that includes the required scope.",
  "doc_ref": "AIPOS-109 MCP-native discipline; AIPOS-123 HTTP/SSE transport"
}
```

Transport-level errors must not leak stack traces, filesystem paths beyond approved public diagnostics, raw tokens, credentials, or private payloads.

## Security Defaults

HTTP/SSE implementation must be conservative by default:

- loopback bind
- no TLS by product default
- no reverse proxy by product default
- no remote origin allowlist by product default
- no browser-based CORS exposure unless separately approved
- no credential minting
- no multi-user auth/RBAC
- no public SaaS assumption
- no long-running daemon install

If a future task adds CORS, TLS, proxy examples, service files, remote auth, or multi-user behavior, that task requires its own Owner Decision Gate and independent audit.

## Implementation Slice Recommendation

AIPOS-123 should be followed by a separate implementation task only after Owner approval.

Recommended AIPOS-124 MVP boundary:

- add HTTP/SSE transport beside existing stdio transport
- bind `127.0.0.1` only
- choose one Owner-approved auth method
- reuse the existing MCP tool registry and handlers
- preserve current read tools
- preserve current `intake_submit` and `owner_decision_record` write tool pairs
- preserve capability-gated tool visibility
- add unit tests for auth accept/reject, tool listing, read tool call, dry-run write call, confirm sequence rejection paths, and loopback bind default
- add docs for local startup only

AIPOS-124 should not:

- add new tools
- add publish or queue mutation tools
- add HTTP routes outside MCP transport
- add TLS/cert management
- add reverse proxy config
- add service files
- add auto-registration
- add multi-workspace or multi-user auth
- change controlled execute

## Non-Goals

AIPOS-123 does not:

- implement HTTP/SSE transport
- modify stdio transport
- modify MCP tool code
- modify CLI commands
- modify Board UI
- modify backend routes
- add package dependencies
- add authentication implementation
- add capability token minting, signing, or revocation
- add controlled execute operations
- add write tools
- expose public endpoints
- add TLS, reverse proxy, service files, Docker, or deployment config
- add polling, scheduling, queue claiming, draft publishing, planner runtime, or agent spawning
- write SessionStore, queue, records, orchestration, or private workspace data

## Audit Checklist

An independent audit should confirm:

- the task is protocol-only
- only the protocol document and required project-management notes changed
- HTTP/SSE is loopback-first and not public by default
- authentication remains an Owner Decision Gate
- controlled execute discipline is unchanged
- capability-scoped tool visibility is unchanged
- no implementation code, dependency, route, port listener, service, TLS, or proxy config was added
- no new MCP tools were created
- no private workspace data was copied into the product repo
