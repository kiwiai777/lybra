# Lybra Remote Endpoint Convention

## Purpose

AIPOS-85 records the Owner-approved endpoint convention for Lybra private remote deployment and future project routing.

These endpoints are private/Owner-oriented deployment conventions. They are not public SaaS launch commitments.

## Accepted Convention

```text
Lybra private remote deployment path:
https://www.kiwiai.cloud/lybra

Future remote project path convention:
https://www.kiwiai.cloud/{project}

MCP service domain:
http://mcp.kiwiai.cloud
```

## Routing Intent

- Lybra uses `/lybra` under the main `www.kiwiai.cloud` host.
- Future remote projects use the same path convention with project slugs.
- MCP is separated as a service-level subdomain.
- Project paths remain Owner-oriented private deployment routes unless a later task explicitly approves a public product surface.

## Non-goals

AIPOS-85 does not implement:

- server deployment
- reverse proxy config
- TLS or certificate setup
- Cloudflare or Nginx config
- MCP service deployment
- cloud agent connection
- auth/RBAC
- database
- public SaaS launch
- multi-tenant routing
- new runtime behavior
- autonomous planner loop
- automatic git push/finalize
