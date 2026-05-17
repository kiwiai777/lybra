# Owner Private Access Boundary

## Default Boundary

AIPOS-84 defaults to owner-only access over SSH tunnel or a private network.

The Board service binds to:

```text
127.0.0.1:8765
```

The Owner accesses it through:

```text
ssh -N -L 8765:127.0.0.1:8765 owner@private-host
```

## Required Properties

- Owner-controlled identity boundary
- no anonymous public access
- no public signup
- no public mutation surface
- no autonomous planner runtime
- no automatic agent launch
- no automatic git push
- no automatic finalize
- explicit Owner confirmation remains required for controlled execute
- independent audit remains required before finalize

## Supported Private Network Options

These are allowed only after Owner approval of the concrete host and access path:

- SSH local tunnel
- Tailscale
- WireGuard
- Zero Trust proxy with Owner-only identity enforcement

## Not Approved in AIPOS-84

- public `0.0.0.0` binding
- untrusted public reverse proxy
- unauthenticated public URL
- multi-user SaaS mode
- database-backed hosted product
- autonomous cloud agents with 24h access

Cloud 24h agent access shifts to AIPOS-86 after the AIPOS-85 endpoint convention alignment task.

AIPOS-86 defines this as a separate boundary and first dogfood plan. It does not approve live cloud agent connection, credentials, queue polling, automatic claim, automatic execute, or autonomous planner runtime.

## Endpoint Convention

AIPOS-85 records endpoint names for future private deployment alignment:

```text
https://www.kiwiai.cloud/lybra
https://www.kiwiai.cloud/{project}
http://mcp.kiwiai.cloud
```

These names do not approve public access, reverse proxy configuration, TLS setup, MCP deployment, or cloud agent connection.
