# Cloud 24h Agent Access Boundary

## Purpose

AIPOS-86 defines the boundary for future cloud-hosted agents that may remain available for 24h access during Owner dogfood.

This document does not connect an agent, create credentials, enable a service, launch a runtime, poll the queue, claim tasks, write records, push git changes, or finalize work.

## Boundary Summary

Cloud 24h access means a named agent instance may be reachable for supervised Lybra work over an Owner-approved private access path.

It does not mean:

- autonomous queue polling
- autonomous planner runtime
- automatic task claim
- automatic draft publish
- automatic controlled execute
- automatic git commit or push
- automatic finalize
- bypassing independent audit
- bypassing Owner decision gates

## Approved Endpoint Convention

AIPOS-85 records endpoint names for private deployment alignment:

```text
https://www.kiwiai.cloud/lybra
https://www.kiwiai.cloud/{project}
http://mcp.kiwiai.cloud
```

These endpoint names are conventions only. AIPOS-86 does not implement routing, TLS, reverse proxy configuration, MCP deployment, authentication, authorization, or a live cloud agent connection.

## Required Owner Approval Before Any Live Agent Access

Owner must approve the concrete values before a cloud 24h agent can be connected:

- remote host
- private access path
- agent instance id
- logical agent identity
- model tier
- allowed role modes
- workspace root
- product repo root
- endpoint or tunnel path
- credential storage location
- log and heartbeat location
- maximum concurrent tasks
- claiming and writing permissions
- rollback method

Default permission before that approval is no live access.

## Agent Identity Requirements

Every cloud 24h agent must declare:

- stable `agent_instance`
- logical agent name
- runtime profile
- model tier
- execution host
- repo host
- validation host
- git host
- allowed task classes
- allowed operations
- forbidden operations
- heartbeat expectation
- max concurrency
- claiming status

`claiming_enabled` defaults to `false` until Owner explicitly approves a live dogfood step.

## First Dogfood Boundary

The first remote agent dogfood should be read-only and report-oriented.

Allowed first dogfood activities:

- verify private access to the Lybra Board health endpoint
- read queue, records, agents, drafts, orchestration summary, timeline, and context pack preview endpoints
- produce a human-readable dogfood report for Owner review
- identify missing backend/UI affordances for AIPOS-87

Disallowed first dogfood activities:

- task claim
- queue mutation
- draft creation
- draft publish
- orchestration event append
- planner iteration append
- records writing
- file writing in the private workspace
- git commit or push
- autonomous planner tick
- background polling
- MCP tool execution
- credential creation or rotation
- public endpoint exposure

## Escalation Stops

The agent access plan must stop for Owner approval if it needs:

- a public or semi-public endpoint
- a new credential, token, service account, or secret
- write access to the private workspace
- queue claim, publish, execute, or append permissions
- model tier or authority expansion
- new service, database, reverse proxy, TLS, MCP deployment, or agent daemon
- access to projects outside the approved workspace scope
- changes to audit or finalize boundaries
- automatic git operations
- any long-running background loop

## Cortex Boundary

The private workspace may contain projects unrelated to Lybra. AIPOS-86 does not approve cloud agent access to `2_projects/private-example` or any other non-Lybra private project unless Owner separately approves that scope.

## Rollback

Rollback must be service-first and data-preserving:

1. Disable the agent connection or service.
2. Revoke or rotate the agent credential outside the repository.
3. Close the tunnel, private network ACL, or endpoint route.
4. Set `claiming_enabled` to `false` for the agent profile if a profile exists.
5. Preserve private workspace data.
6. Resume the local WSL workflow.

## Relationship to Later Tasks

AIPOS-86 defines the boundary and first dogfood plan only.

AIPOS-87 may review backend sufficiency and dogfood friction after the first supervised remote access plan is accepted or rehearsed.
