# Local Board API Adapter Boundary

## Purpose

This document defines the local adapter boundary between a future Board UI or local API server and the existing AIPOS backend capabilities.

AIPOS-35 is architecture and protocol only.

It does not implement:

- API server
- FastAPI
- Flask
- Django
- Next.js
- React
- web UI
- database
- auth system
- RBAC
- background daemon
- scheduler
- agent execution
- runtime launch
- orchestration runtime
- planner runtime
- new writer primitive

## Layer Model

The Board stack is divided into four layers:

1. Board UI or local API server
2. Local Board API Adapter
3. Backend service functions / CLI module functions
4. File-driven repository state

Responsibilities:

- Board UI never writes files directly.
- Board UI sends structured read or mutation requests only.
- Adapter validates request shape, enforces safety policy, and normalizes responses.
- Adapter never invents new writer behavior.
- Adapter only calls existing backend primitives.
- Backend functions or CLI commands remain the only mutation executors.
- Repository files remain the source of truth.

## MCP Surface Relationship

AIPOS-96 defines a future MCP server as a sibling surface to CLI and the local Dashboard / Board UI.

The MCP surface must preserve this adapter boundary:

- MCP tools may delegate to approved Board/API read semantics.
- MCP write-capable tools may only translate requests into existing controlled execute semantics.
- MCP must not write files directly.
- MCP must not add operations to the controlled execute allowlist.
- MCP must not bypass actor matching, dry-run proof, snapshot revalidation, blocking reasons, or Owner Decision Gates.
- MCP must not become a runtime launcher, agent execution bridge, scheduler, queue poller, or deployment surface.

AIPOS-96 does not implement the MCP server, transports, tool code, backend routes, Web UI controls, CLI commands, credentials, deployment, or client registration.

## Recommended Adapter Strategy

Recommended default:

- Phase 1 adapter should call Python backend modules directly when stable module APIs exist.
- `cli_subprocess` is allowed only as a compatibility fallback or smoke boundary.
- Adapter should not parse human-readable CLI text for authoritative state.
- Adapter should prefer JSON-returning functions or structured Python objects.

Why this is the preferred direction:

- module calls are easier to test
- module calls avoid shell quoting and path handling risks
- module calls avoid parsing terminal text
- module calls make request and response typing clearer
- CLI remains useful for manual operations and smoke testing

## AIPOS-36 Implementation Status

AIPOS-36 implements the first local Python module adapter under `tools/aipos_cli/board_adapter.py`.

Current implementation status:

- `module` mode is now implemented for read endpoints and dry-run mutation previews
- no CLI runtime bridge is used in the adapter implementation
- no API server or Web UI is introduced
- execute mutations remain disabled until dry-run token and revalidation contract fields are implemented end to end
- AIPOS-36 adapter execute remains blocked
- AIPOS-37 defines execute contract only
- AIPOS-38 may implement controlled execute MVP
- no execute without dry-run token + revalidation
- local-only boundary remains
- no server, UI, auth, or database is introduced

## Adapter Mode

Supported adapter mode values:

- `module`
- `cli_subprocess`
- `hybrid`

Mode meanings:

- `module`: preferred future implementation using stable Python service functions
- `cli_subprocess`: compatibility fallback using explicit CLI JSON commands
- `hybrid`: module preferred, CLI fallback for endpoints not yet exposed as modules

AIPOS-35 recommendation:

- transition model: `hybrid`
- long-term target: `module`

## Request Normalization

The adapter must normalize incoming Board requests into one internal request model before invoking backend logic.

Required normalization areas:

- `task_id` versus `path`
- `actor`
- `agent_instance`
- `runtime_profile`
- `with_records`
- `dry_run`
- `owner_confirmation`
- payload validation
- safe repo-relative paths

Rules:

- exactly one of `task_id` or `path` should identify a task target where applicable
- `path` must be repo-relative and rooted under allowed task directories
- `actor` is required for queue mutations
- `agent_instance` and `runtime_profile` are pass-through metadata and must remain visible in the normalized request
- `with_records` defaults to `false`
- `dry_run` defaults per endpoint policy, not per UI assumption
- owner confirmation fields are protocol-only unless a future implementation adds explicit confirmation state

The adapter must reject:

- absolute paths
- path traversal
- non-repo paths
- unsupported operations
- unknown mutation types
- execute requests without required dry-run proof

## Identity and Availability Pass-through

The adapter must preserve caller identity instead of re-inventing it.

Pass-through fields:

- `actor`
- `agent_instance`
- `runtime_profile`
- optional current availability snapshot when supplied by a caller or backend

Rules:

- adapter must not rewrite actor identity into a different canonical value without surfacing the backend match result
- actor matching remains a backend validation responsibility
- availability data is advisory and must not become hidden authorization logic
- response envelope must surface `actor` and `actor_match`

## Response Normalization

All read and mutation results must normalize into the AIPOS-34 response envelope:

```yaml
ok:
verdict:
operation:
dry_run:
actor:
actor_match:
timestamp:
data:
summary:
planned_writes:
planned_moves:
performed_writes:
performed_moves:
warnings:
blocking_reasons:
needs_owner_reasons:
owner_confirmation_required:
owner_confirmation_reasons:
safety_notice:
errors:
```

Mapping rules:

- `PASS`: backend validation passes and execute is allowed under current policy
- `WARN`: operation is allowed but warnings or non-blocking caveats exist
- `NEEDS_OWNER`: owner review or confirmation is required before execute
- `BLOCK`: execute must not proceed

Normalization rules:

- read endpoints set `dry_run: false` and keep write or move arrays empty
- dry-run mutations fill `planned_writes` and `planned_moves` only
- execute mutations fill `performed_writes` and `performed_moves`
- warnings remain displayable strings or structured warning objects
- backend-native diagnostic fields may stay inside `data`, but top-level contract fields must be stable

## Local-only Security Boundary

The adapter starts local-only.

Rules:

- future server must bind localhost by default
- no remote network exposure
- no auth or RBAC yet
- no secrets stored
- no runtime command execution
- no agent launch
- no shell command passthrough

## Write Boundary

Allowed write delegation:

- adapter may delegate to existing draft writer
- adapter may delegate to existing draft publish
- adapter may delegate to existing queue mutation
- adapter may delegate to existing records writer only through `--with-records` or equivalent opt-in

Forbidden:

- adapter direct file writes
- adapter direct queue moves
- adapter direct records writes
- adapter orchestration writes
- adapter shared memory writes
- adapter project management writes
- adapter Git operations
- adapter agent execution

## Future Test Strategy

Future adapter tests should cover:

- tempfile repository fixtures
- dry-run then execute paths
- stale dry-run rejection
- path traversal rejection
- actor mismatch
- record collision
- subprocess timeout when `cli_subprocess` mode is enabled
- module and CLI parity
- response envelope conformance
- no live repository mutation during tests
