# First Remote Agent Dogfood Plan

## Purpose

AIPOS-86 defines the first safe dogfood path for a cloud 24h agent without enabling autonomous runtime behavior.

The first dogfood is a supervised access rehearsal. It proves that a named remote agent can inspect Lybra state through approved private read paths and return a useful handoff report.

## Preconditions

- AIPOS-83 product/workspace boundary is finalized.
- AIPOS-84P project management cutover to `2_projects/lybra` is finalized.
- AIPOS-84 private remote production boundary is finalized.
- AIPOS-85 endpoint conventions are finalized.
- Owner has approved the concrete remote host and private access path.
- Owner has approved the concrete agent instance and model tier.
- No public execution surface is exposed.
- No credentials are stored in the repository.
- `claiming_enabled` remains `false` unless Owner separately approves it.

## Dogfood Step 0: Access Rehearsal

Goal: prove the remote path can read health and review data.

Allowed checks:

```text
GET /api/health
GET /api/queue
GET /api/agents
GET /api/records
GET /api/drafts
GET /api/orchestration/summary
GET /api/orchestration/timeline
```

AIPOS-87 adds these nested orchestration read aliases for dogfood consistency. Existing dash-form aliases remain available:

```text
GET /api/orchestration-summary
GET /api/orchestration-timeline
```

This plan does not add write routes.

Expected output:

- host and endpoint used
- agent instance id
- model tier
- read paths checked
- failures or missing affordances
- no-write confirmation
- recommended AIPOS-87 friction fixes

## Dogfood Step 1: Review-Only Work Simulation

Goal: have the remote agent prepare a report from existing Lybra state without mutation.

Allowed activities:

- summarize current queue and records state
- review orchestration summary and timeline
- inspect context pack preview for one Owner-selected task
- identify whether a task is ready for human-controlled publish, claim, or append
- recommend but not perform a next action

Forbidden activities:

- write a task card
- write a record
- append orchestration events
- append planner iterations
- claim a task
- publish a draft
- run controlled execute confirm
- run git commit or push
- run background polling
- modify server or deployment config

## Dogfood Report Template

The remote agent should return:

```text
Agent instance:
Logical agent:
Model tier:
Runtime profile:
Access path:
Workspace root:
Product root:
Read endpoints checked:
Observed Lybra state:
Ready-to-act items:
Owner decisions needed:
Blocked items:
Friction found:
No-write confirmation:
Recommended next task:
```

## Owner Approval Before Step 2

Any move beyond read-only dogfood requires a separate Owner decision.

Examples:

- enabling `claiming_enabled`
- granting append-only writer access
- granting draft publish or queue claim controlled execute access
- connecting MCP tools
- starting a persistent service or daemon
- permitting 24h queue polling
- allowing git operations

## AIPOS-87 Input

AIPOS-87 should use the first dogfood report to review backend sufficiency and dogfood friction. Candidate review areas:

- missing read endpoints
- unclear mobile review paths
- insufficient agent identity display
- weak no-write proof
- insufficient endpoint health details
- missing handoff summary fields
- confusing Owner decision routing
- missing rollback evidence
