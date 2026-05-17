# Board API Endpoints

## Endpoint Naming

Endpoint names are HTTP-like stable semantic names.

Implementation in later stages may be:

- direct local adapter around CLI
- embedded Python adapter
- localhost-only server

The contract does not require a server implementation in AIPOS-34.

## Adapter Invocation

The future local adapter may run in one of these modes:

- `module`
- `cli_subprocess`
- `hybrid`

AIPOS-35 recommends:

- `hybrid` as the transition model
- `module` as the long-term target

Rules:

- module mode should call stable Python functions that return structured objects
- cli_subprocess mode should require JSON output for authoritative operations
- the adapter must not parse human-readable CLI text as authoritative state
- response envelopes must stay identical across invocation modes

## Endpoint to CLI Mapping

| Endpoint | Purpose | Current Mapping | Status |
| --- | --- | --- | --- |
| `GET /health` | backend availability check | adapter-level repo and command sanity check | `needs_adapter` |
| `GET /queue` | queue view data | `aipos_cli.py queue --json` | `implemented_now` |
| `GET /tasks/{task_id}` | task detail by ID | `aipos_cli.py task --task-id <task_id> --json` | `implemented_now` |
| `GET /tasks/by-path` | task detail by path | `aipos_cli.py task --path <path> --json` | `implemented_now` |
| `GET /preview/{task_id}` | session preview by task ID | `aipos_cli.py preview --task-id <task_id> --actor <actor> --json` | `implemented_now` |
| `GET /needs-owner` | tasks requiring owner review | `aipos_cli.py needs-owner --json` | `implemented_now` |
| `GET /records` | records summary and parsed records | `aipos_cli.py records --json` | `implemented_now` |
| `GET /agents` | agent/runtime profile data | `aipos_cli.py agents --json` | `implemented_now` |
| `GET /drafts` | drafts list | `aipos_cli.py draft list --json` | `implemented_now` |
| `GET /planner-drafts/review` | planner-created draft review desk | read-only draft metadata plus existing draft publish dry-run compatibility checks | `implemented_now` |
| `GET /owner-decisions/review` | Owner decision gate review desk | read-only needs-owner task and orchestration timeline aggregation | `implemented_now` |
| `POST /owner-decision/resolve:review` | Owner decision resolution review | validates explicit Owner evidence and previews `owner_decision_recorded` append plan without writing | `implemented_now` |
| `POST /planner-tick/manual-flow:preview` | minimal manual planner tick flow preview | planner tick preview plus orchestration summary, timeline, and Owner decision context; no writes or execute token | `implemented_now` |
| `POST /planner-draft/publish:dry-run` | approved planner draft publish dry-run | planner draft review plus Owner gate check delegating to existing controlled `draft_publish` | `implemented_now` |
| `GET /orchestration-summary` | orchestration summary preview | `aipos_cli.py orchestration summary preview --orchestration-id <id> --json` | `implemented_now` |
| `GET /orchestration/summary` | read-only dogfood alias for orchestration summary preview | same backend as `/orchestration-summary` | `implemented_now` |
| `GET /orchestration-timeline` | orchestration event and planner iteration timeline | append-only `planner_iterations.md` and `orchestration_events.md` reader | `implemented_now` |
| `GET /orchestration/timeline` | read-only dogfood alias for orchestration timeline preview | same backend as `/orchestration-timeline` | `implemented_now` |
| `GET /planner-loop/mvp` | semi-automated planner loop control desk | single-step coordinator preview over summary, timeline, Owner gates, planner drafts, and controlled publish handoff | `implemented_now` |
| `GET /context-pack/preview` | read-only context pack preview | `aipos_cli.py context-pack preview --task-id/--path/--orchestration-id ... --json` | `implemented_now` |
| `POST /drafts/create:dry-run` | preview draft create | `aipos_cli.py draft create --dry-run --json` | `implemented_now` |
| `POST /drafts/create` | execute draft create | `aipos_cli.py draft create --json` | `implemented_now` |
| `POST /drafts/validate` | validate draft | `aipos_cli.py draft validate --json` | `implemented_now` |
| `POST /drafts/publish:dry-run` | preview draft publish | `aipos_cli.py draft publish --dry-run --json` | `implemented_now` |
| `POST /drafts/publish` | execute draft publish | `aipos_cli.py draft publish --json` | `implemented_now` |
| `POST /queue/claim:dry-run` | preview queue claim | `aipos_cli.py queue claim --dry-run --json` | `implemented_now` |
| `POST /queue/claim` | execute queue claim | `aipos_cli.py queue claim --json` | `implemented_now` |
| `POST /queue/block:dry-run` | preview queue block | `aipos_cli.py queue block --dry-run --json` | `implemented_now` |
| `POST /queue/block` | execute queue block | `aipos_cli.py queue block --json` | `implemented_now` |
| `POST /queue/complete:dry-run` | preview queue complete | `aipos_cli.py queue complete --dry-run --json` | `implemented_now` |
| `POST /queue/complete` | execute queue complete | `aipos_cli.py queue complete --json` | `implemented_now` |
| `POST /queue/reopen:dry-run` | preview queue reopen | `aipos_cli.py queue reopen --dry-run --json` | `implemented_now` |
| `POST /queue/reopen` | execute queue reopen | `aipos_cli.py queue reopen --json` | `implemented_now` |

## Adapter Responsibilities

Where `needs_adapter` or `implemented_now` appears, the future Board adapter is responsible for:

- building CLI arguments from structured request JSON
- or calling stable backend module functions when available
- invoking the correct local backend capability
- parsing CLI JSON output into the common response envelope
- normalizing errors into stable error categories
- enforcing dry-run-first flow for controlled mutations
- revalidating immediately before execute

## Request Shape Conventions

### Read Requests

Read requests should use simple path or query parameters and have no side effects.

Examples:

```json
{
  "task_id": "EXAMPLE-001",
  "actor": "dev.codex.local"
}
```

Normalization constraints:

- reject absolute paths
- reject path traversal
- reject non-repo paths
- reject unknown mutation types
- reject execute requests that omit required dry-run proof

### Draft Mutation Requests

Example dry-run payload:

```json
{
  "frontmatter": {
    "task_id": "AIPOS-29-EXAMPLE",
    "title": "Example Draft",
    "assigned_to": "dev.codex.local",
    "context_bundle": "dev.codex.local",
    "task_mode": "code",
    "priority": "medium",
    "created_by": "Kiwi_gpt",
    "output_target": "tools/aipos_cli/",
    "artifact_policy": "formal_write"
  },
  "body": "## Goal\n\nExample.\n"
}
```

### Queue Mutation Requests

All queue mutation requests must accept identity and records options explicitly.

Example:

```json
{
  "task_id": "EXAMPLE-001",
  "actor": "dev.codex.local",
  "agent_instance": "dev.claude.cc.local",
  "runtime_profile": "cc",
  "with_records": true,
  "reason": "Waiting on owner"
}
```

## Status Semantics

- `implemented_now`: existing CLI capability already supports the contract semantics closely enough for an adapter
- `needs_adapter`: no direct CLI command exists, but no new backend writer primitive is required
- `future_only`: planned semantic endpoint but not part of current contract execution path
- `forbidden`: not allowed in this stage

## Forbidden Endpoint Classes

These endpoint classes are forbidden in AIPOS-34:

- orchestration writer endpoints
- runtime launch endpoints
- agent execution endpoints
- scheduler endpoints
- database administration endpoints
- remote multi-user auth endpoints
