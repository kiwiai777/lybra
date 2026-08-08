# Cross-Machine Executor Materialization (AIPOS-363)

**Baseline for any harness that can read a local file.** The connector (`lybra agent`) is the
ONLY adaptation layer between a cross-machine executor and the gate. The agent stays a DUMB
executor: it reads/writes LOCAL files only and learns ZERO gate verbs.

## The problem this solves

A remote executor (e.g. on a Mac, with no local Lybra workspace) cold-starts and is told to
`read <workspace>/5_tasks/queue/pending/<id>.md` → ENOENT (the card lives on the gate machine).
Worse, a pi/Claude-Code/codex executor session has NO `lybra_*` MCP tools, so it cannot call
`lybra_task_preview(include_body)` / `lybra_return_content` itself. Result: the executor has
credentials but no work it can reach. (See governance ref AIPOS-362 feedback, 坑1.)

## The flow (connector-mediated)

```
connector materialize  ──claim──►  gate
                      ◄─body(319)──  gate
                      └─► ~/.lybra/work/<id>/card.md  (LOCAL material)
launch ANY harness reading card.md   (pi / claude / codex / bash)
agent writes ~/.lybra/work/<id>/RETURN.md  (LOCAL)
connector pushback    ──return_body(320)──►  gate
                      ──confirm(328)──────►  gate   (executor self-confirm)
```

- **materialize (S1)**: `lybra agent materialize --gate-url … --connection-json … --role executor
  --actor <you> --task-id <ID> --owner-policy-ref <pol>`. Claims (PreAuthorized auto-release),
  pulls the body via `lybra_task_preview(include_body)`, drops `card.md` + `MANIFEST.json` under
  `~/.lybra/work/<ID>/`, and prints a **zero-gate-verb kickoff** that points only at the local
  `card.md` / `RETURN.md`. Pipe that kickoff to your harness.
- **pushback (S2)**: `lybra agent pushback …` reads the local `RETURN.md`, relays it as
  `return_body` via `lybra_queue_return_dry_run`, and self-confirms (328). The local RETURN is
  retained as a copy. **Any failure emits a blocked progress event (323) — never silent.**

Material area = one card per directory under `~/.lybra/work/` (env `LYBRA_MATERIAL_ROOT` or
`--material-root`). The MANIFEST holds only NON-SECRET provenance (task_id, claim_id,
active_session_id, gate_url) — never a token. task_id is sanitized to a single path segment
(no traversal).

## Harness-agnostic by construction (S3)

Adding a harness = adding one entry to `config/runtime_cmds.yaml` (a `cmd` template with the
`{kickoff}` placeholder); the materialization protocol changes ZERO lines. Verified templates:
`pi`, `cc`, `claude_code`, `codex` (slot), `generic_bash`. The materialized kickoff is multi-line,
so launchers transmit it via `@file` (the shared `kickoff_safe` hazard contract).

## Envelope covers custom roles (S4)

An Owner-signed PreAuthorized envelope may name an AIPOS-352 custom role in `agent_or_role`
(e.g. `kaia-asst`); it matches an agent whose capability-token role is exactly that role, even
when the concrete `agent_instance` differs. Agents of any OTHER role still fall back to Supervised
(偏窄 fail-safe — uncovered roles still stop). No role-class auto-matching (owner must name the role).

## S5 — MCP-direct is an OPTIONAL enhancement (not the baseline)

Materialization is the BASELINE because every agent can read a local file. An MCP-capable harness
(e.g. one with a `lybra_*` MCP client mounted) MAY skip the local file hop and call the gate verbs
directly:

- fetch body: `lybra_task_preview(task_id, include_body=true)` (needs `queue_claim` scope)
- push return: `lybra_queue_return_dry_run(..., return_body=<text>)` then `lybra_queue_return_confirm`

The CONFIG PORT for that path is exactly what materialize/pushback consume: the gate URL +
`connection.json` (role → token). This card does NOT implement in-session MCP tool mounting — it
leaves the port. The baseline (file materialization) works with zero MCP knowledge on the agent side.

## Zero regression

Materialization is **gate-url mode only** (the new `materialize` / `pushback` subcommands). The
existing local paths (`fetch`, `watch` filesystem pump, local-workspace claim) are untouched and
remain byte-identical. A machine WITH a local workspace keeps using the local path; materialization
engages only when there is no local workspace / explicit gate-url mode.
