# Backend Sufficiency Review and Dogfood Friction Fixes

## Purpose

AIPOS-87 reviews whether the current local Board backend is sufficient for the AIPOS-86 first remote agent dogfood path.

This is not a live remote agent connection, deployment task, MCP integration, public endpoint task, autonomous runtime task, queue mutation task, or git automation task.

## AIPOS-86 Dogfood Requirement

The first remote agent dogfood is read-only and report-oriented. It needs stable read paths for:

- health
- queue
- agents
- records
- drafts
- orchestration summary
- orchestration timeline
- context pack preview

## Sufficiency Finding

The backend already has enough read-only primitives for the first dogfood rehearsal:

- `GET /api/health`
- `GET /api/queue`
- `GET /api/agents`
- `GET /api/records`
- `GET /api/drafts`
- `GET /api/context-pack/preview`
- `GET /api/orchestration-summary`
- `GET /api/orchestration-timeline`

All of these return structured JSON envelopes and preserve the existing no-hidden-mutation boundary.

## Friction Found

AIPOS-86 dogfood documentation used nested endpoint names:

```text
GET /api/orchestration/summary
GET /api/orchestration/timeline
```

The existing Board implementation used dash-form endpoint names:

```text
GET /api/orchestration-summary
GET /api/orchestration-timeline
```

That mismatch is small but annoying for remote dogfood because an agent following the AIPOS-86 checklist would hit `404 NOT_FOUND` for the nested names.

## Fix Applied

AIPOS-87 adds read-only route aliases:

```text
GET /api/orchestration/summary
GET /api/orchestration/timeline
```

The aliases call the same backend functions as the existing dash-form routes. They do not add writers, dry-run tokens, confirm buttons, queue mutation, planner runtime launch, polling, MCP execution, git operations, or public endpoint behavior.

`GET /api/health` also exposes a `remote_dogfood_readiness` block so a remote dogfood report can confirm:

- the intended first dogfood mode is read-only and report-oriented
- live agent connection is not enabled
- autonomous runtime is not enabled
- queue polling is not enabled
- public endpoint access is not required
- dogfood read paths and legacy aliases are discoverable

## Remaining Friction for Later

These are candidates for later work after actual dogfood feedback:

- a dedicated read-only dogfood status endpoint
- clearer per-endpoint no-write proof in the UI
- endpoint-level latency or freshness metadata
- explicit remote agent identity display in health or agents output
- a dogfood report export view

## Non-Goals

AIPOS-87 does not implement:

- live cloud agent connection
- credentials, tokens, service accounts, or secret storage
- reverse proxy configuration
- TLS/certificate setup
- MCP service deployment
- auth/RBAC
- database
- task claim
- queue mutation
- draft creation or publish
- orchestration event append
- planner iteration append
- records writing
- background queue polling
- autonomous planner runtime
- automatic git commit, push, or finalize
