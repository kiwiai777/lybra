# Lybra

> Local-first, file-authoritative agent workbench with audit-first governance.

Lybra coordinates AI-assisted code, research, documentation, operations, and review. Its control plane keeps tasks, decisions, events, records, and handoffs visible as plain files.

Lybra is extracted from the AI Project OS control-plane work. AI Project OS remains the architecture and history name; Lybra is the product name and standalone repository.

## Why Lybra

- **Files are the source of truth.** Task cards, decisions, events, iterations, and records live in a workspace. Files remain authoritative.
- **History is explicit.** Append-only logs plus derived state make changes reviewable.
- **Authorities stay separated.** Execution, independent audit, and human Owner decision authority do not collapse into one actor.
- **Writes cross controlled gates.** Approved mutations use dry-run preview, proof, revalidation, and explicit confirmation.
- **Forks pause for Owner decisions.** Architecture, risk, credential, deployment, audit, runtime, and long-term direction changes are gated.

## Surfaces

| Surface | Audience | Status |
|---|---|---|
| CLI | Scripting, CI-friendly checks, headless workflows | Implemented |
| Local Dashboard / Board UI | Human review in a browser | Implemented for read paths and selected controlled write paths |
| MCP Server | MCP-aware clients | Stdio MVP and loopback HTTP/SSE MVP implemented with read tools and selected controlled write tools |

No surface is privileged. Future surfaces should translate to the same backend semantics instead of bypassing them.

### MCP Server MVP

The current MCP MVP has two local transports:

- stdio, for same-host MCP clients
- loopback HTTP/SSE, bound to `127.0.0.1` by default on port `8766`

Both transports expose the same tool registry. HTTP/SSE requires a static Bearer token from `LYBRA_MCP_TOKEN`; write-tool visibility still depends on `LYBRA_CAPABILITY_TOKEN`.

Both transports include four read-only tools:

- `lybra_queue_list`
- `lybra_task_preview`
- `lybra_validate`
- `lybra_context_pack_build`

It also exposes selected controlled write-tool pairs when `LYBRA_CAPABILITY_TOKEN` includes the matching operation scope:

- `lybra_intake_submit_dry_run` / `lybra_intake_submit_confirm`
- `lybra_owner_decision_record_dry_run` / `lybra_owner_decision_record_confirm`

These tools wrap the same controlled execute path as the CLI: dry-run first, token proof, snapshot revalidation, then confirm. They do not publish drafts, mutate queues, append orchestration events as side effects, or launch runtimes.

Manual client configuration example:

```json
{
  "mcpServers": {
    "lybra": {
      "command": "python3",
      "args": ["-m", "tools.mcp_server", "serve"],
      "cwd": "<product-repo>",
      "env": {
        "AIPOS_WORKSPACE_ROOT": "<workspace>",
        "LYBRA_CAPABILITY_TOKEN": "{\"token_ref\":\"<token>\",\"operations\":[\"intake_submit\"],\"projects\":[\"<project>\"],\"expires_at\":\"<iso-timestamp>\"}"
      }
    }
  }
}
```

Loopback HTTP/SSE startup example:

```bash
export AIPOS_WORKSPACE_ROOT=<workspace>
export LYBRA_MCP_TOKEN=<local-http-token>
export LYBRA_CAPABILITY_TOKEN='{"token_ref":"<token>","operations":["intake_submit"],"projects":["<project>"],"expires_at":"<iso-timestamp>"}'
python3 -m tools.mcp_server serve-http --host 127.0.0.1 --port 8766
```

HTTP JSON-RPC requests are sent to `/mcp` with `Authorization: Bearer <local-http-token>`. The `/sse` endpoint emits keepalive ping events for local clients.

The MVP does not register itself with clients, mint tokens, verify signatures, manage TLS, install service files, provide reverse proxy configuration, or provide publish/queue/runtime tools.

### Local Docker Sandbox MVP

The current sandbox runtime MVP is a local Docker adapter for one bounded ephemeral worker run:

```bash
python3 -m tools.sandbox_runtime local-docker run --dry-run --image <local-image> -- echo hello
```

The adapter requires an explicit image, uses `--pull never`, defaults to `--network none`, mounts any supplied workspace path read-only, injects no credentials, and returns an in-memory structured report. It does not create Dockerfiles, Docker Compose files, services, write mounts, scheduler loops, MCP bridges, or durable runtime records.

### Workspace Templates

Lybra includes local bundled workspace templates for starting a new file-authoritative project workflow:

- `blank`
- `consulting-engagement`
- `software-development`

Workspace init is a controlled execute operation. Dry-run previews every planned file, then confirm revalidates the snapshot before writing:

```bash
python3 tools/aipos_cli/aipos_cli.py workspace init \
  --template blank \
  --output <workspace> \
  --actor <actor> \
  --var project_id=<project> \
  --dry-run \
  --json
```

Confirm uses the prior dry-run envelope and explicit Owner confirmation:

```bash
python3 tools/aipos_cli/aipos_cli.py workspace init \
  --confirm \
  --from-json <dry-run-envelope.json> \
  --actor <actor> \
  --owner-confirmation-token OWNER_CONFIRMED \
  --json
```

Templates are local product assets. Lybra does not fetch templates from remote URLs, run template scripts, provide a template marketplace, overwrite existing files, or initialize non-empty output directories.

## Core Concepts

- **Task cards** describe units of work under a workspace task queue.
- **Context bundles** define role and environment boundaries for agent instances.
- **Context Packs** are read-only briefing artifacts assembled from task cards, bundles, orchestration data, records, and source refs. The builder preview follows AIPOS-78.
- **Orchestration events and planner iterations** are append-only coordination records.
- **Records** capture formal session and claim history.
- **Role catalog** is protocol-finalized in AIPOS-97. It separates vendor-neutral role templates from concrete agent instances and keeps template names functional.
- **Coordinator contract** keeps Planner as a governance role rather than an ordinary worker template, preserving audit separation.

## Project Context Contract

Cross-agent startup needs one source of truth that can render project constraints for different agents without hand-maintaining one template per agent.

Lybra is introducing this as a finalized protocol direction: a vendor-neutral master contract rendered through adapters. It is planned as a future Context Pack rendering extension, not a separate agent-initialization command, and is not implemented here yet.

## Repository Layout

```text
<product-repo>/
  tools/                 CLI and backend adapter code
  web/                   Local dashboard / Board UI
  0_control_plane/       Generic protocol and governance docs
  3_context_bundles/     Generic role/context schemas
  docs/                  Product, extraction, and deployment docs
  config/                Example configuration
  examples/              Non-private sample workspace data

<workspace>/
  2_projects/<project>/        Project docs, decision log, roadmap
  5_tasks/queue/               Task queue
  5_tasks/drafts/              Planner-created draft task cards
  5_tasks/records/             Formal session and claim records
  5_tasks/orchestration/<id>/  Append-only events and iterations
  0_control_plane/             Workspace-specific control-plane configuration
```

The product repo holds reusable code, generic protocols, examples, and tests. The workspace holds project data. Product code reads workspace state through explicit root configuration.

## Status

Lybra is pre-MVP and protocol-heavy.

Implemented today:

- CLI validation and queue/records/agent read surfaces
- Local Board read paths and selected controlled write paths
- Context Pack read-only preview path
- Product/workspace root separation through environment configuration
- MCP stdio and loopback HTTP/SSE MVPs for queue, task preview, validation, Context Pack tools, and selected controlled write-tool pairs
- Local Docker sandbox runtime MVP for explicit, bounded, read-only ephemeral worker runs
- Workspace Template MVP with local bundled templates and controlled execute initialization

Protocol finalized, implementation pending or partial:

- MCP server boundary and tool model beyond the stdio read-only MVP: AIPOS-96
- Sandbox runtime abstraction beyond the local Docker MVP: AIPOS-90
- SessionStore schema and credential boundary: AIPOS-92
- Vendor-neutral role catalog: AIPOS-97
- Planner autonomy tiers, session tree primitives, and related governance

Lybra does not currently ship a public hosted service, managed cloud runtime, remote database, autonomous planner runtime, remote MCP deployment profile, MCP publish tools, MCP queue mutation tools, remote template registry, or template marketplace.

## Getting Started / Workspace Root

### Source Checkout

Run checks from the product repository:

```bash
python3 -m unittest discover -s tools/aipos_cli/tests
python3 -m unittest discover -s web/board/tests
```

For new local Lybra runtime workspaces, use:

```text
~/.lybra/workspaces/<workspace_id>/
```

This convention is for product runtime workspaces created or operated by Lybra. It does not replace an existing private project-management source of truth. For example, an Owner may keep durable private project records under a separate workspace such as `/home/kiwi/ai-project-os/2_projects/lybra/` while using `~/.lybra/workspaces/<workspace_id>/` for runtime dogfood and execution artifacts.

Set `AIPOS_WORKSPACE_ROOT` when running from the product repo against a separate workspace:

```bash
export AIPOS_WORKSPACE_ROOT=<workspace>
python3 tools/aipos_cli/aipos_cli.py validate --json
python3 tools/aipos_cli/aipos_cli.py queue
python3 tools/aipos_cli/aipos_cli.py agents --json
python3 tools/aipos_cli/aipos_cli.py records --json
```

The Board server also accepts an explicit workspace path:

```bash
python3 web/board/app.py --repo-root <workspace>
```

Without `AIPOS_WORKSPACE_ROOT` or `--repo-root`, CLI commands preserve legacy behavior by searching upward from the current directory for `5_tasks/queue`.

### npm Install

Lybra can be installed as an npm command distribution. The npm package installs a `lybra` command that delegates to the bundled Python implementation, so Python must be available on `PATH`.

```bash
npm install -g lybra
lybra --help
```

Local tarball smoke for release validation:

```bash
npm pack
npm install -g ./lybra-0.1.0.tgz
lybra --help
```

Local install smoke without global writes:

```bash
npm pack --pack-destination /tmp/lybra-pack
npm install --global --prefix /tmp/lybra-install /tmp/lybra-pack/lybra-0.1.0.tgz
/tmp/lybra-install/bin/lybra --help
```

## Public Repo Boundary

Do not commit private workspace data, secrets, local task cards, generated caches, or operating-system metadata. Local task-card mirrors may exist under ignored `task_cards/`.

Use placeholders such as `<workspace>`, `<project>`, and `<product-repo>` in public examples. Keep private hostnames, paths, endpoints, credentials, and runtime state outside the public repo.

## License

License: Apache-2.0. See [LICENSE](LICENSE).

## Acknowledgements

Lybra draws on file-authoritative configuration, event sourcing, append-only logs, dry-run execution, human decision gates, and independent audit.
