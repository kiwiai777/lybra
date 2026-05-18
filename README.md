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
| MCP Server | MCP-aware clients | Protocol finalized in AIPOS-96; implementation pending |

No surface is privileged. Future surfaces should translate to the same backend semantics instead of bypassing them.

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

Protocol finalized, implementation pending or partial:

- MCP server boundary and tool model: AIPOS-96
- Sandbox runtime abstraction: AIPOS-90
- SessionStore schema and credential boundary: AIPOS-92
- Vendor-neutral role catalog: AIPOS-97
- Planner autonomy tiers, session tree primitives, and related governance

Lybra does not currently ship a public hosted service, managed cloud runtime, remote database, autonomous planner runtime, or implemented MCP server.

## Getting Started / Workspace Root

Run checks from the product repository:

```bash
python3 -m unittest discover -s tools/aipos_cli/tests
python3 -m unittest discover -s web/board/tests
```

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

## Public Repo Boundary

Do not commit private workspace data, secrets, local task cards, generated caches, or operating-system metadata. Local task-card mirrors may exist under ignored `task_cards/`.

Use placeholders such as `<workspace>`, `<project>`, and `<product-repo>` in public examples. Keep private hostnames, paths, endpoints, credentials, and runtime state outside the public repo.

## License

License: TBD by Owner.

## Acknowledgements

Lybra draws on file-authoritative configuration, event sourcing, append-only logs, dry-run execution, human decision gates, and independent audit.
