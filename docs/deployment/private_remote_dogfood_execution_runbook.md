# Private Remote Dogfood Execution Runbook

## Purpose

AIPOS-89 defines the first Owner-run private remote dogfood execution checklist and runbook for Lybra.

This is an operator-facing runbook. It tells the Owner what to verify before and during the first private remote dogfood pass. It does not execute deployment, start services, create credentials, connect live agents, mutate queues, write records, or enable autonomous planner runtime.

## Scope

The first dogfood pass is:

- Owner-supervised
- private-access only
- read-first and report-oriented
- based on the AIPOS-83 product/workspace boundary
- aligned with the AIPOS-84 private deployment preparation docs
- aligned with the AIPOS-86 cloud agent access boundary
- compatible with the AIPOS-88 shared server directory policy without creating `/opt/kiwiai/`

The goal is to prove that the Owner can reach the private Board, inspect the real private workspace through read paths, and capture friction before enabling any live remote agent execution.

## Required Preconditions

Before the first run, confirm:

- AIPOS-83 is finalized.
- AIPOS-84P is finalized and `2_projects/lybra` is the canonical project management path.
- AIPOS-84 is finalized.
- AIPOS-85 endpoint conventions are finalized.
- AIPOS-86 cloud 24h agent access boundary is finalized.
- AIPOS-87 read-only dogfood route aliases are finalized.
- AIPOS-88 shared server directory policy is finalized.
- AIPOS-88D strategic direction decisions are finalized.
- The Owner has approved the concrete remote host.
- The Owner has approved the private access method.
- The Owner has approved the exact workspace root used for dogfood.
- No public Board exposure is planned.
- No cloud agent credential has been added for this run.
- No claim, publish, append writer, controlled execute dry-run/confirm, or git operation is in scope for this run.

## Path Model

For the first private dogfood pass, the AIPOS-84 sample layout remains valid:

```text
/home/owner/lybra
/home/owner/ai-project-os
```

The product repo and private workspace remain separate:

```text
AIPOS_REPO_ROOT=/home/owner/lybra
AIPOS_WORKSPACE_ROOT=/home/owner/ai-project-os
```

AIPOS-88 separately defines `/opt/kiwiai/` as the shared root for long-running Kiwiai production services. AIPOS-89 does not migrate the first dogfood layout into `/opt/kiwiai/`, create `/opt/kiwiai/`, or decide a long-running service directory. Any change from the AIPOS-84 sample layout to `/opt/kiwiai/lybra/` is a future Owner Decision Gate.

## Owner Run Sequence

### 1. Local Baseline

From the canonical workspace, verify the current repo state before comparing it with the remote host:

```bash
git status -sb
python3 tools/aipos_cli/aipos_cli.py validate --json
python3 tools/aipos_cli/aipos_cli.py queue
python3 tools/aipos_cli/aipos_cli.py agents --json
python3 tools/aipos_cli/aipos_cli.py records --json
python3 -m unittest discover -s tools/aipos_cli/tests
python3 -m unittest discover -s web/board/tests
```

This baseline is informational. It does not authorize remote mutation.

### 2. Remote Workspace Boundary Check

On the remote host, verify that the product repo and private workspace are separate:

```bash
test -d /home/owner/lybra
test -d /home/owner/ai-project-os
test -d /home/owner/ai-project-os/2_projects/lybra
test -d /home/owner/ai-project-os/5_tasks
```

Confirm that the Board process will read private workspace data from:

```text
/home/owner/ai-project-os
```

Do not point the dogfood run at `examples/sample_workspace/`.

### 3. Remote Readiness Check

From the product repo on the remote host, run read-only validation:

```bash
cd /home/owner/lybra
python3 -m unittest discover -s tools/aipos_cli/tests -p "test_*.py"
python3 -m unittest discover -s web/board/tests -p "test_*.py"
python3 tools/aipos_cli/aipos_cli.py validate --json
```

If this fails because the product repo does not yet support the external workspace layout on the selected host, stop and record the failure. Do not patch the host ad hoc without a follow-up task.

### 4. Private Board Start

Start the Board bound to localhost only:

```bash
python3 /home/owner/lybra/web/board/app.py \
  --host 127.0.0.1 \
  --port 8765 \
  --repo-root /home/owner/ai-project-os
```

The service must not bind to `0.0.0.0`.

AIPOS-89 does not install or enable systemd. If the Owner chooses to use a service manager, that is a separate approval gate using host-local configuration only.

### 5. Remote Host API Check

On the remote host, verify the read paths:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/queue
curl http://127.0.0.1:8765/api/agents
curl http://127.0.0.1:8765/api/records
curl http://127.0.0.1:8765/api/drafts
```

The orchestration routes require a concrete orchestration id. If the workspace has no
`5_tasks/orchestration/` directory, or if that directory contains no orchestration ids,
record the absence in the dogfood report and skip the two orchestration route checks.

If an orchestration id exists, set it explicitly and verify the two routes:

```bash
ORCHESTRATION_ID=<existing-orchestration-id>
curl "http://127.0.0.1:8765/api/orchestration/summary?orchestration_id=${ORCHESTRATION_ID}"
curl "http://127.0.0.1:8765/api/orchestration/timeline?orchestration_id=${ORCHESTRATION_ID}"
```

Expected health metadata should show no live agent connection, no autonomous runtime, no queue polling, and no public endpoint requirement.

### 6. Owner Private Access Check

From the Owner machine, open a private tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 owner@private-host
```

Then verify:

```bash
curl http://127.0.0.1:8765/api/health
```

Open:

```text
http://127.0.0.1:8765/
```

Confirm that the mobile Owner review path remains usable for read/review/approve/confirm surfaces already implemented before AIPOS-89. Do not use this run to add or expose new mutation controls.

### 7. Read-only Dogfood Review

During the first dogfood pass, review:

- current queue summary
- current agent summary
- current records summary
- draft review desk
- orchestration summary
- orchestration timeline
- context pack preview for an Owner-selected task
- Owner decision gate panel
- existing controlled execute surfaces only as gated UI surfaces to avoid during this run

Do not run controlled execute dry-runs or confirmations as part of the AIPOS-89 dogfood rehearsal. Any such action requires a separate Owner decision outside this runbook.

## Dogfood Report Template

Record the report outside secrets-bearing logs:

```text
Run id:
Date:
Owner:
Remote host label:
Private access method:
Product repo path:
Workspace root path:
Board bind:
Board port:

Health result:
Queue read result:
Agents read result:
Records read result:
Drafts read result:
Orchestration summary result:
Orchestration timeline result:
Context pack preview result:
Mobile review path result:

Confirmed no public Board exposure:
Confirmed no new credentials:
Confirmed no controlled execute dry-run/confirm:
Confirmed no claim/publish/append/records/git operation:
Confirmed no MCP execution:
Confirmed no autonomous planner runtime:

Friction found:
Missing backend/UI affordances:
Recommended follow-up task:
Rollback performed:
```

Reports must redact secrets, tokens, host-private credentials, private keys, database passwords, OAuth secrets, and any sensitive endpoint details the Owner does not want committed.

## Stop Conditions

Stop and ask the Owner before any of the following:

- public endpoint exposure
- reverse proxy, TLS, DNS, Nginx, Cloudflare, or Zero Trust config changes
- systemd install or enablement
- Docker Compose service creation
- `/opt/kiwiai/` directory creation or ownership changes
- `/data/kiwiai/` mount or migration
- new `.env` or credential storage
- live cloud agent connection
- MCP deployment or MCP tool execution
- task claim, queue mutation, draft publish, append writer, records write, or controlled execute dry-run/confirmation
- git commit, push, finalize, or release
- autonomous planner runtime, background polling, or agent launcher
- any architecture, scope, risk, security, model-tier, authority, audit-boundary, or long-term direction fork

## Rollback

Rollback is data-preserving:

1. Stop the Board process.
2. Close the SSH tunnel or private network entry.
3. Leave the private workspace untouched.
4. Keep product repo and workspace repo separate.
5. Return to the canonical local WSL workflow.
6. Record friction and rollback notes in the dogfood report.

Do not duplicate the private workspace as a rollback strategy.

## AIPOS-89 Non-goals

AIPOS-89 does not implement:

- server deployment
- reverse proxy or TLS configuration
- Cloudflare/Nginx routing
- MCP service deployment
- public endpoint behavior
- auth/RBAC
- database
- live cloud agent connection
- cloud agent credentials
- task claim
- queue mutation
- draft mutation
- records writing
- append writer expansion
- controlled execute allowlist expansion
- autonomous planner runtime
- background queue polling
- agent launcher
- git automation
- automatic commit/push/finalize
- stage archive closure
