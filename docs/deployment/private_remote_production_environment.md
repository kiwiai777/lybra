# Private Remote Production Environment for Owner Dogfood

## Purpose

AIPOS-84 defines the owner-only private remote production environment for Lybra dogfood.

This is not public SaaS deployment. It does not introduce public signup, multi-tenancy, billing, database migration, public execution surfaces, autonomous planner runtime, automatic git push, or automatic finalize.

## Deployment Shape

```text
remote owner host
  /home/owner/lybra          # product/core repo
  /home/owner/ai-project-os  # private workspace repo
    2_projects/lybra
    5_tasks
    private records/orchestration/context packs
```

The product process reads product code from the Lybra repo and workspace data from the private workspace root.

```text
AIPOS_REPO_ROOT=/home/owner/lybra
AIPOS_WORKSPACE_ROOT=/home/owner/ai-project-os
```

## Private Access Boundary

Default access is through an owner-controlled private channel:

```text
ssh -N -L 7117:127.0.0.1:7117 owner@private-host
```

Then open:

```text
http://127.0.0.1:7117/
```

Allowed private access patterns:

- SSH local port forwarding
- Tailscale, WireGuard, or equivalent private network
- Zero Trust access that requires Owner identity and does not expose an untrusted public execution surface

Disallowed in AIPOS-84:

- public `0.0.0.0` Board binding
- public unauthenticated URL
- public signup or external user onboarding
- exposing controlled execute endpoints to untrusted networks
- autonomous planner runtime
- automatic queue polling
- automatic agent execution
- automatic git commit, push, or finalize

## Endpoint Convention

AIPOS-85 records the Owner-approved remote endpoint convention:

```text
Lybra private remote deployment path:
https://www.kiwiai.cloud/lybra

Future remote project path convention:
https://www.kiwiai.cloud/{project}

MCP service domain:
http://mcp.kiwiai.cloud
```

These endpoints are private/Owner-oriented deployment conventions. They do not implement reverse proxy routing, TLS, access control, MCP deployment, cloud agent connection, or public SaaS launch.

## Board Process

The Board process should bind to localhost on the remote host.

```bash
python3 /home/owner/lybra/web/board/app.py \
  --host 127.0.0.1 \
  --port 7117 \
  --repo-root /home/owner/ai-project-os
```

`--repo-root` points at the private workspace root. It must not point at a public sample workspace for dogfood.

## Systemd Template

AIPOS-84 provides templates only. It does not install or enable the service.

Use:

- `config/deployment/lybra-board.example.env`
- `config/deployment/lybra-board.example.service`

Copy templates into host-local private configuration only after Owner approval of the actual host and access method.

## Validation

On the remote host:

```bash
cd /home/owner/lybra
python3 -m unittest discover -s tools/aipos_cli/tests -p "test_*.py"
python3 -m unittest discover -s web/board/tests -p "test_*.py"
python3 tools/aipos_cli/aipos_cli.py validate --json
```

With the service running locally on the remote host:

```bash
curl http://127.0.0.1:7117/api/health
curl http://127.0.0.1:7117/api/queue
curl http://127.0.0.1:7117/api/agents
```

From the Owner machine through SSH tunnel:

```bash
curl http://127.0.0.1:7117/api/health
```

## Data Safety

The private workspace remains the source of truth for:

- `2_projects/lybra`
- `5_tasks`
- records
- orchestration logs
- context packs
- private runtime state
- private agent/workflow configuration

The product repo must not copy real private workspace data.

## Owner Gates

The following remain hard stops:

- architecture route fork
- scope expansion
- permission or security boundary change
- new service, database, deployment shape, or public endpoint
- model tier or agent authority expansion
- audit boundary change
- high-risk refactor
- external publish, commit, push, or finalize
- any long-term direction change

## Cloud 24h Agent Access

AIPOS-86 defines the cloud 24h agent access boundary and first remote agent dogfood plan as a separate step after private endpoint convention alignment.

The first remote agent dogfood is read-only and report-oriented by default. It may verify private access and inspect existing Board read paths, but it must not claim tasks, publish drafts, append orchestration data, write records, run controlled execute confirmation, poll queues, connect MCP tools, perform git operations, or launch an autonomous planner runtime.

## First Owner Dogfood Runbook

AIPOS-89 defines the manual Owner runbook for the first private remote dogfood pass:

```text
docs/deployment/private_remote_dogfood_execution_runbook.md
```

The runbook uses the private access and localhost Board binding described above. It does not install, enable, or start services from the repository; it only documents the Owner-supervised sequence, stop conditions, report template, and data-preserving rollback.

## Rollback

Rollback is service-first and data-preserving:

1. Stop the private Board service.
2. Close SSH tunnel or private network entry.
3. Leave the workspace data untouched.
4. Resume local WSL workflow from `~/ai-project-os`.
5. Revert host-local service/config changes if needed.

Do not fork or duplicate the private workspace to recover service availability.
