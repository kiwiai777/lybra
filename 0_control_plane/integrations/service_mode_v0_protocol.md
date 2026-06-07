# Service Mode v0 Protocol

## Status

AIPOS-189 defines the protocol-only boundary for Phase 1 service mode v0.

This document does not implement `lybra serve`, add process supervision code, add token minting code, change MCP tool visibility, change capability-token handling in `lybra mcp`, change Board behavior, change queue / records / validator semantics, add a daemon, add a runtime launcher, add polling, add heartbeat behavior, add deployment behavior, expose a public endpoint, or start any agent.

## Purpose

AIPOS-186 showed that the current productized startup path works, but the manual multi-role setup is still too easy to misconfigure:

- each role currently needs a correctly scoped MCP process environment;
- multiple loopback ports make role / token alignment error-prone;
- cross-terminal token sharing requires manual environment handling;
- users need to know too much about `LYBRA_MCP_TOKEN`, `LYBRA_CAPABILITY_TOKEN`, and per-process scope.

AIPOS-189 defines a future local-first service mode that opens Lybra's own gate surfaces with one command while preserving every existing authority boundary.

The goal is:

```text
start local Lybra gates -> inspect redacted connection table -> connect agents explicitly
```

The goal is not:

```text
start agents -> schedule work -> poll queues -> automate the loop
```

## Relationship To Existing Protocols

AIPOS-189 composes:

- AIPOS-96 MCP server protocol and naming boundary;
- AIPOS-123 / AIPOS-124 HTTP/SSE transport;
- AIPOS-164 MCP task claim and autonomy dial;
- AIPOS-165 / 166 Supervised MCP explicit claim;
- AIPOS-168 / 169 Supervised MCP work return;
- AIPOS-177 / 178 audit dispatch and verdict;
- AIPOS-181 startup CLI ergonomics;
- AIPOS-182 / 183 Board runtime surfacing;
- AIPOS-184 Board closed-loop lifecycle surfacing;
- AIPOS-186 manual heterogeneous dogfood evidence;
- AIPOS-188 Phase 0 DX cheap fixes.

It does not reopen those protocols. It defines a service-mode wrapper around the existing local gate surfaces.

## Service Mode Boundary

Service mode may manage only Lybra-owned gate processes:

```yaml
managed_processes:
  board:
    purpose: local human review surface
  mcp:
    purpose: local MCP gate surface
```

Service mode must not manage:

```yaml
forbidden_processes:
  - agent workers
  - model runtimes
  - external harnesses
  - schedulers
  - queue pollers
  - background task runners
  - heartbeat senders
  - deployment services
```

Lybra remains the gate, not the engine.

## Command Contract

The future orchestration entry point is:

```text
lybra serve
```

Subcommands:

```text
lybra serve start
lybra serve status
lybra serve stop
lybra serve rotate
```

Rules:

- `lybra serve` is an upper-layer local orchestration command;
- `lybra board` remains the low-level single-component Board startup command;
- `lybra mcp` remains the low-level single-component MCP startup command using the legacy process-level capability model;
- `lybra serve` must not replace or break those lower-level commands;
- `start` opens only the Board and MCP gate surfaces;
- `status` reports service-mode state from durable local state such as pidfile / connection config;
- `stop` stops only processes that service mode started and owns;
- `rotate` rotates service-mode local role tokens and updates the local connection config.

`status` must not depend on probing agents, polling queues, or inferring worker liveness. If a future implementation performs a local socket reachability check for Board / MCP, it must be reported as gate-process reachability only, not agent availability.

## Default Local Surface

Service mode defaults:

```yaml
board:
  host: 127.0.0.1
  port: 7117
mcp:
  host: 127.0.0.1
  port: 7118
```

Rules:

- loopback-only by default;
- no default bind to `0.0.0.0`;
- no public endpoint;
- no bundled TLS, OAuth, reverse proxy, tunnel, DNS, or cloud deployment;
- remote access remains user-managed and requires a separate deployment/security gate before being productized.

## Single MCP Endpoint With Server-Side Scope Registry

Service mode canonicalizes the local MCP gate as one endpoint:

```text
http://127.0.0.1:7118/mcp
```

It must not use separate MCP ports as the primary role separation model.

Role separation is provided by opaque role tokens minted by `lybra serve` and resolved server-side:

```yaml
role_tokens:
  executor:
    token_ref: svc-executor
    token_value: opaque_random_secret
    scopes:
      - queue_claim
      - queue_return
  owner_dispatch:
    token_ref: svc-owner-dispatch
    token_value: opaque_random_secret
    scopes:
      - audit_dispatch
  auditor:
    token_ref: svc-auditor
    token_value: opaque_random_secret
    scopes:
      - queue_claim
      - audit_verdict
```

Rules:

- tokens are opaque random strings, not self-describing JSON;
- clients present opaque role tokens;
- clients never self-report `operations`;
- the server maintains the authoritative `token -> scope` registry;
- tool visibility and tool-call authorization derive from that server-side registry;
- unknown, expired, rotated, or scope-mismatched role tokens block without exposing raw secrets;
- role token scope must be evaluated by the MCP server at `tools/list` and `tools/call` boundaries, not trusted from client payloads.

This is a service-mode model only. The existing `lybra mcp` command keeps the legacy process-level `LYBRA_CAPABILITY_TOKEN` environment model for backwards compatibility and single-component debugging.

## Transport Auth And Role Scope

Service mode distinguishes:

```yaml
transport_auth:
  meaning: client may connect to the local MCP endpoint
role_scope:
  meaning: client may see and call specific scoped write tools
```

The future implementation may choose one of two local-only request forms, but must preserve the distinction:

```yaml
allowed_v0_forms:
  two_token:
    Authorization: Bearer ${LYBRA_MCP_TOKEN}
    role_token_header: Lybra-Role-Token: <opaque role token>
  combined_local_token:
    Authorization: Bearer <opaque role token>
    server_interpretation: token grants transport access and maps to server-side role scope
```

The implementation task must choose one form explicitly and test it. In either form, client-submitted JSON capability operations are not authoritative in service mode.

## Local Connection Config

Service mode writes a local connection config under the workspace:

```text
.lybra/local/connection.json
```

Rules:

- `.lybra/local/` must be gitignored;
- `connection.json` may contain opaque role token values because clients need a local source to read them;
- file mode should be `0600` where the platform supports POSIX permissions;
- console output must never print raw token values;
- console output may print redacted fingerprints such as `sha256:<prefix>`;
- the file must include a clear warning that anyone who can read it has the role scopes encoded by those tokens;
- this model is for local single-user loopback use only.

Recommended shape:

```json
{
  "config_version": 1,
  "workspace_root": "/path/to/workspace",
  "mode": "service_v0",
  "local_only": true,
  "created_at": "2026-06-07T00:00:00Z",
  "rotated_at": null,
  "board": {
    "url": "http://127.0.0.1:7117"
  },
  "mcp": {
    "rpc_url": "http://127.0.0.1:7118/mcp",
    "sse_url": "http://127.0.0.1:7118/sse"
  },
  "tokens": [
    {
      "role": "executor",
      "token_ref": "svc-executor",
      "token_env_ref": "LYBRA_EXECUTOR_ROLE_TOKEN",
      "scopes": ["queue_claim", "queue_return"],
      "fingerprint": "sha256:example",
      "token": "<opaque local secret>"
    }
  ],
  "secrets_notice": "Raw role tokens are local secrets. Anyone who can read this file can use the listed local role scopes."
}
```

The exact field names may be refined by the implementation task, but it must preserve:

- endpoint refs;
- role names;
- token refs;
- server-side scope mapping;
- redacted fingerprints;
- no public endpoint posture;
- no credentials unrelated to Lybra's local gate tokens.

## Redacted Connection Table

After `lybra serve start`, the console should print a redacted table:

```text
Lybra service mode

Board: http://127.0.0.1:7117
MCP:   http://127.0.0.1:7118/mcp

Role             Scopes                         Token ref              Fingerprint
executor         queue_claim, queue_return       svc-executor           sha256:...
owner-dispatch   audit_dispatch                  svc-owner-dispatch     sha256:...
auditor          queue_claim, audit_verdict      svc-auditor            sha256:...

Local config: .lybra/local/connection.json
Raw tokens are not printed.
```

This table is informational. It does not prove a worker is online and must not imply agent availability.

## Process Model

Approved protocol direction: one foreground supervisor process manages Board and MCP child processes.

Rules:

- not a daemon;
- not a background service by default;
- not a system service manager;
- not an auto-restart loop;
- no queue polling;
- no heartbeat;
- no worker launch;
- no agent lifecycle management;
- `stop` terminates only Board / MCP child processes that service mode owns;
- child-process ownership must be recorded in durable local state such as `.lybra/local/service_state.json` or pidfiles.

If the supervisor exits unexpectedly, no autonomous recovery loop is implied. A user may run `lybra serve status` or `lybra serve start` again.

## Status Contract

`lybra serve status` should report:

```yaml
workspace_root:
mode: service_v0
board:
  configured_url:
  pid:
  status_from_pidfile:
mcp:
  configured_url:
  pid:
  status_from_pidfile:
tokens:
  - role:
    token_ref:
    scopes:
    fingerprint:
connection_config:
  path:
  permissions_status:
warnings:
```

Rules:

- raw tokens are never printed;
- status reads local service-mode state, not agent presence;
- stale pidfiles are warnings, not proof of an active runtime;
- status must clearly distinguish gate-process state from agent availability.

## Provenance Requirements

Under service mode, MCP mutation responses and durable records must be able to explain which server-side role scope authorized the call.

Future implementations must include at least:

```yaml
scope_basis:
  mode: service_v0
  token_ref:
  role:
  scopes:
  mcp_endpoint_ref:
```

Rules:

- provenance records must not include raw token values;
- `token_ref` / role identity is evidence, not authority by itself;
- operation authority still depends on dry-run / confirm / Owner gates / controlled execute;
- scope provenance is required so a single endpoint does not obscure which role scope entered the chain.

This requirement applies to future service-mode claim / return / audit-dispatch / audit-verdict paths when they write or link durable records.

## Rotation

`lybra serve rotate` is a future local maintenance operation.

Rules:

- rotation creates new opaque role tokens;
- rotation updates the server-side token registry and `.lybra/local/connection.json`;
- console output remains redacted;
- old tokens become invalid after rotation unless a future implementation explicitly supports a grace window;
- rotation does not rewrite historical records;
- historical records keep their original `token_ref` / role provenance.

## Board Integration

Existing Board runtime surfaces may read service-mode metadata in the future to show:

- service mode active / inactive;
- Board and MCP configured endpoints;
- role token refs;
- scope visibility by role;
- fingerprints only;
- config path and permission warnings.

Board must not show raw tokens, start agents, rotate tokens, or mutate service state unless a later Board-specific Owner gate approves those controls.

## Compatibility

Compatibility requirements:

- `lybra board` continues to start only Board;
- `lybra mcp` continues to start only MCP with legacy process-level `LYBRA_CAPABILITY_TOKEN`;
- `lybra mcp-config` remains useful for low-level MCP setup;
- `lybra mcp doctor` remains useful for legacy `lybra mcp` and may later gain service-mode diagnostics;
- direct Python entry points remain compatible.

Service mode may add new config files under `.lybra/local/`, but it must not rewrite existing task history or product protocol history.

## Security Boundary

Service mode v0 is local single-user tooling.

Security constraints:

- loopback-only;
- no public endpoint;
- no remote multi-tenant security claim;
- no cloud deployment claim;
- no credentials proxy;
- no third-party API key management;
- raw role tokens may exist only in gitignored local config;
- raw role tokens are never printed to console, Board, logs, task cards, records, provenance, or git-tracked files;
- anyone who can read `.lybra/local/connection.json` can use those local role scopes;
- file permissions must be checked and surfaced when unsafe;
- cloud / team / multi-user MCP requires a separate security model and Owner gate.

## Non-Goals

AIPOS-189 does not introduce:

- implementation code;
- a daemon;
- service installation;
- auto-restart;
- worker runtime launch;
- scheduler behavior;
- queue polling;
- heartbeat behavior;
- agent registration;
- agent availability tracking;
- finalize writer;
- accepted-work unblock;
- active lease writer;
- Delegated mode;
- Standing mode;
- Trace-Native Audit;
- credentials proxy;
- deployment;
- public endpoint;
- live BYO-LLM behavior;
- external-intake behavior;
- new MCP tools;
- new controlled execute operations;
- relaxed Owner confirmation;
- relaxed dry-run / confirm discipline.

## Recommended v0 Implementation Slice

The first implementation slice should be narrow:

1. Add `lybra serve start/status/stop/rotate`.
2. Start Board and one MCP HTTP/SSE endpoint on loopback using supervised child processes.
3. Mint opaque local role tokens for executor, owner-dispatch, and auditor.
4. Store token registry / connection config under `.lybra/local/` with gitignore and `0600` permission checks.
5. Teach the service-mode MCP endpoint to resolve role tokens through server-side registry for tool visibility and tool-call authorization.
6. Print a redacted connection table and expose redacted status.
7. Add tests proving:
   - raw tokens are not printed;
   - client-provided `operations` are ignored / rejected as authority;
   - token refs map to expected scopes server-side;
   - wrong role token cannot see or call scoped tools;
   - legacy `lybra mcp` behavior remains unchanged;
   - stop only terminates service-owned Board / MCP child processes.

The implementation slice must not add agent launch, polling, heartbeat, daemon installation, finalize writer, accepted-work unblock, active lease writer, Delegated / Standing behavior, Trace-Native Audit, public endpoint, deployment, or credential handling.

## Next Gates

Separate Owner gates remain required for:

- AIPOS-189 implementation;
- service-mode Board controls beyond read-only surfacing;
- cloud / multi-user / public MCP security model;
- finalize writer;
- accepted-work unblock;
- active lease writer / lease activation;
- Delegated / Standing automation;
- Trace-Native Audit;
- agent harness launch / runtime integration;
- deployment or public endpoint support.
