# Kiwiai Shared Server Directory Policy

## Purpose

AIPOS-88 defines the canonical server directory policy for long-running Kiwiai production services on `kiwiai.cloud` and similar Owner-controlled remote production environments.

This is a Control Plane environment policy. It is intended for all current and future Kiwiai projects, including Lybra, Cortex, Loom, Hermes, OpenClaw, LiteLLM, New API, reports, scripts, backups, and future Kiwiai services.

This policy is documentation and governance only. It does not create directories, configure services, deploy infrastructure, expose ports, create credentials, run Docker, enable MCP, modify databases, launch agents, or change runtime behavior.

## Canonical Root

Long-running Kiwiai production services should live under:

```text
/opt/kiwiai/
```

Service-specific directories should use stable lowercase names:

```text
/opt/kiwiai/litellm/
/opt/kiwiai/cortex/
/opt/kiwiai/loom/
/opt/kiwiai/lybra/
/opt/kiwiai/hermes/
/opt/kiwiai/openclaw/
/opt/kiwiai/new-api/
/opt/kiwiai/reports/
/opt/kiwiai/scripts/
/opt/kiwiai/backups/
```

Project-specific repositories should reference this policy instead of redefining their own server root layout. A project may document service-specific details under its own repository, but it should not fork the shared `/opt/kiwiai/` root policy without explicit Owner approval.

## User and Ownership

The normal login user is:

```text
kiwi
```

The shared root should be owned by:

```text
kiwi:kiwi
```

System-level changes remain sudo-only. Examples include package installation, systemd unit installation, firewall changes, reverse proxy changes, TLS certificate setup, disk mounts, user/group creation, and ownership changes outside the approved service root.

## Service Layout

Each service should keep its own runtime files under its service directory.

When Docker Compose is used, each service should own its own compose file:

```text
/opt/kiwiai/<service>/docker-compose.yml
```

Shared scripts may live under:

```text
/opt/kiwiai/scripts/
```

Reports may live under:

```text
/opt/kiwiai/reports/
```

Backups may live under:

```text
/opt/kiwiai/backups/
```

Backups must not be treated as a substitute for a tested restore plan.

## Secret Handling

Each service keeps `.env` out of Git.

Service `.env` files should be host-local and permissioned:

```text
chmod 600 .env
```

Reports, handoffs, logs, and audit artifacts must redact secrets before they are shared or committed.

Do not store API keys, tokens, service account credentials, private keys, database passwords, or OAuth secrets in this repository.

## Port and Network Defaults

Public ports should not be exposed by default.

Service ports should bind to localhost by default:

```text
127.0.0.1
```

Databases should not expose public ports.

Any exception requires explicit Owner approval, including:

- public HTTP(S) exposure
- reverse proxy routing
- TLS/certificate setup
- Cloudflare/Nginx changes
- external database exposure
- MCP endpoint exposure
- cross-service network access that expands the security boundary

## Data and Disk Policy

The system disk may be small. Large data, logs, backups, embeddings, datasets, and model files should move to a future data mount instead of the 40GB system disk:

```text
/data/kiwiai/
```

The `/data/kiwiai/` mount is a future storage policy target. AIPOS-88 does not create, mount, format, or migrate data to that path.

## Relationship to Lybra Deployment Docs

Lybra private deployment docs may reference this shared policy, but AIPOS-88 does not broaden Lybra deployment scope.

AIPOS-84, AIPOS-85, AIPOS-86, and AIPOS-87 boundaries remain intact:

- no live service enablement
- no public endpoint behavior
- no reverse proxy or TLS configuration
- no MCP deployment
- no database
- no auth/RBAC
- no live cloud agent connection
- no autonomous planner runtime
- no queue claiming
- no git automation

## Owner Approval Gates

Owner approval is required before:

- creating or changing production service directories
- changing ownership or permissions outside a service root
- installing or enabling systemd services
- exposing any public port
- configuring reverse proxy, TLS, DNS, or Cloudflare
- creating or rotating production credentials
- mounting or migrating data to `/data/kiwiai/`
- changing database exposure
- connecting MCP services
- enabling autonomous agents or background runtimes
