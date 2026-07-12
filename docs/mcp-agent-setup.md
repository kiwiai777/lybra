# MCP Agent Setup

Lybra's MCP server is a local-first gate. Connecting to the transport does not claim work, return work, launch a worker, activate a lease, dispatch audit, or finalize anything.

> **Agent-side pull loop (AIPOS-248):** for the `lybra on`/`lybra off` fetch-for-work pattern
> (plain text, no leading slash — see F-248-o3-3 below), give your agent
> `skills/lybra-executor/SKILL.md` (symlink into `~/.claude/skills` or `~/.codex/skills`) and use
> `lybra agent fetch|watch` — a stateless, role-agnostic client pull over `lybra_queue_list`.
> Lybra never pushes, schedules, or records agent presence; claiming stays the supervised
> dry-run → Owner confirm chain below.
>
> **F-248-o3-3 (real-machine finding):** typing `/lybra on` fails in Claude Code — the slash
> resolver only matches REGISTERED command names (the skill's own invocable name is
> `/lybra-executor`, from its directory basename), and there is no separate `lybra` command
> registered, so the literal slash form errors out instead of falling back to natural-language
> skill matching. Say the bare phrase `lybra on` / `lybra off` instead.

## Start The Server

```bash
export LYBRA_MCP_TOKEN="choose-a-local-transport-token"
export LYBRA_CAPABILITY_TOKEN='{"token_ref":"local-claim-return","operations":["queue_claim","queue_return"],"expires_at":"2999-01-01T00:00:00Z"}'
lybra mcp doctor
python3 -m tools.mcp_server serve-http --host 127.0.0.1 --port 7118
```

Connect MCP clients to:

```text
http://127.0.0.1:7118/mcp
```

Use HTTP Bearer auth with `LYBRA_MCP_TOKEN`.

## Two Separate Authority Layers

`LYBRA_MCP_TOKEN` is transport authentication. It lets a client connect to HTTP/SSE.

`LYBRA_CAPABILITY_TOKEN.operations` controls scoped mutation tool visibility:

- `queue_claim` exposes `lybra_queue_claim_dry_run` and `lybra_queue_claim_confirm`.
- `queue_return` exposes `lybra_queue_return_dry_run` and `lybra_queue_return_confirm`.

If the client connects but claim or return tools are missing, check capability operations first:

```bash
lybra mcp doctor
lybra mcp doctor --json
```

The doctor command prints only redacted SHA-256 fingerprints. It never prints raw token values.

## Claim And Return Discipline

Claim flow:

```text
lybra_queue_claim_dry_run -> Owner reviews preview -> lybra_queue_claim_confirm
```

Return flow:

```text
lybra_queue_return_dry_run -> Owner reviews confirmation_preview -> lybra_queue_return_confirm
```

Both confirm tools require:

```text
owner_confirmation_token: OWNER_CONFIRMED
```

The `confirmation_preview` includes copyable confirm arguments for generic MCP clients, but the Owner confirmation token remains explicit.

## Common Diagnostics

- `SCOPE_DENIED`: the MCP connection may be authenticated, but the capability token lacks the required operation.
- `STALE_DRY_RUN`: the dry-run token is unknown to this MCP server process or expired. Run the dry-run again on this connection.
- `INCOMPATIBLE_DRY_RUN`: the token was recognized but belongs to a different operation or surface.
- `OWNER_CONFIRMATION_REQUIRED`: present the exact preview to Owner, then confirm with `OWNER_CONFIRMED`.

## Boundaries

The current MCP claim and return tools remain Supervised-only.

They do not add lease activation, records writing, audit dispatch, audit PASS, finalize, Delegated or Standing automation, runtime launch, scheduler, polling, heartbeat, credentials handling, live BYO-LLM, external-intake assist, deployment, or public endpoint behavior.
