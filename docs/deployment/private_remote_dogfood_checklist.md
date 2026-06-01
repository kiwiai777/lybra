# Private Remote Dogfood Checklist

## Before Host Setup

- AIPOS-83 product/workspace boundary finalized.
- AIPOS-84P project docs cutover finalized.
- `2_projects/lybra` is the canonical project management path.
- Owner selects the remote host.
- Owner approves the access method.
- No public Board exposure is planned.

## Host Layout

Expected owner-only host layout:

```text
/home/owner/lybra
/home/owner/ai-project-os
```

The product repo and private workspace must remain separate.

## Board Service

- Bind host: `127.0.0.1`
- Port: `7117`
- Workspace root: `/home/owner/ai-project-os`
- Product root: `/home/owner/lybra`
- Service user: non-root owner-controlled user

## Access Verification

- Remote host can reach `http://127.0.0.1:7117/api/health`.
- Owner machine can reach the same API through SSH tunnel or private network.
- No public internet client can reach the Board endpoint.
- Future endpoint convention is documented as `https://www.kiwiai.cloud/lybra` without implementing routing in AIPOS-85.

## Runtime Non-goals

- no autonomous planner loop runtime
- no automatic queue polling
- no agent launcher
- no records writer expansion
- no queue block/complete/reopen UI expansion
- no database
- no public auth/RBAC

## Ready for AIPOS-86 Only When

- private service is stable for Owner dogfood
- no duplicate workspace source of truth exists
- access boundary is documented
- rollback path has been tested or manually rehearsed
- Owner approves first remote agent access boundary

## AIPOS-86 Remote Agent Dogfood Boundary

AIPOS-86 defines cloud 24h agent access as a supervised, Owner-approved boundary. The first dogfood step should be read-only and report-oriented.

Before any live remote agent is connected, Owner must approve:

- concrete host
- private access path
- agent instance id
- model tier
- workspace scope
- credential storage location
- allowed operations
- rollback method

AIPOS-86 does not enable task claim, draft publish, controlled execute, append writers, background queue polling, MCP tool execution, git operations, or autonomous planner runtime.

## AIPOS-89 Owner Runbook

AIPOS-89 adds the first Owner-facing private remote dogfood execution runbook:

```text
docs/deployment/private_remote_dogfood_execution_runbook.md
```

The runbook is manual and documentation-only. It preserves the AIPOS-84 sample first-dogfood layout under `/home/owner/lybra` and `/home/owner/ai-project-os` while acknowledging the AIPOS-88 shared `/opt/kiwiai/` policy for future long-running production service placement.

AIPOS-89 does not migrate paths, create `/opt/kiwiai/`, install services, expose public endpoints, add credentials, connect live agents, enable MCP, claim tasks, publish drafts, append records or orchestration data, run git operations, or enable autonomous planner runtime.
