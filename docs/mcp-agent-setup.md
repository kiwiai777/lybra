# MCP Gate Setup

Lybra's gate is a local-first MCP server started by the Owner. Connecting to it does not claim work,
return work, launch a worker, dispatch audit, or finalize anything.

## Who talks to the gate

| Party | Gate access |
|---|---|
| **Owner** | Starts the gate (`lybra serve start`), signs envelopes (`lybra envelope mint --confirm`), issues enrollment codes. |
| **Advisor** | Drives every card through product commands — `lybra loop --task-id <card>` derives and runs claim / return ingest / audit dispatch / verdict ingest / finalize / close with the advisor's own credential. The advisor never hand-writes gate calls. |
| **Executor / auditor workstations** | **None.** A workstation is opened with `/go` (or launched by the advisor's `lybra loop` when the Owner's envelope authorizes it with `--launch-harness`), commits on the card branch and writes its report to the project's declared location; the advisor's `lybra loop` picks the artifact up. Workstations hold no claim / return / confirm scope. |

Credentials are never typed or pasted: the advisor and each workstation redeem a one-time enrollment
code (`lybra roles enroll --code …`), which writes a local `.lybra/connection.json` (`0600`). The
per-project steps are printed by `lybra onboarding guide <project>`.

The advisor session may be Claude Code (default) or Codex. `lybra onboarding guide <project> --advisor-harness codex
--advisor-host <session host>` prints the advisor's enrollment as `lybra roles enroll … --harness codex [--harness-dir <dir>]
[--harness-host <host>]`: the advisor credential lands in the governance root's `.lybra/` (run on the governance-root machine;
a Codex session on another machine runs it over ssh), `.lybra/role` records `harness: {kind: codex, dir, host}` as given (a
directory on another host is not checked locally), and no `.pi` wiring or `.claude/skills` delivery happens. Legal `--harness`
values are the keys of `schema/distribution.schema.json` `harness_semantics.kinds`; an unknown value is refused with that
list before the code is redeemed. Registering a Codex advisor does not extend the Owner-confirm surface (see
`docs/v1_disclosure.md` row 14).

## Start the gate

```bash
lybra serve --workspace-root <gate workspace> start   # Board (7117) + MCP gate (7118), foreground
lybra serve status                                      # redacted status
```

MCP clients that need a raw connection use `http://127.0.0.1:7118/mcp` with HTTP Bearer auth;
`lybra mcp-config` prints a redacted client configuration.

## Diagnostics

```bash
lybra mcp doctor
lybra mcp doctor --json
```

The doctor command prints only redacted SHA-256 fingerprints. It never prints raw token values.

- `SCOPE_DENIED`: the connection is authenticated, but its role lacks the operation (expected for
  workstations on every gate verb).
- `STALE_DRY_RUN`: the dry-run token is unknown to this gate process or expired; re-run the step
  (`lybra loop` re-derives it).
- `INCOMPATIBLE_DRY_RUN`: the token belongs to a different operation or surface.
- `OWNER_CONFIRMATION_REQUIRED`: the operation is Owner-only (for example signing an envelope); the
  Owner runs it with the Owner credential.

## Executors never pull work

Executors do not look for, claim or return work themselves (the earlier agent-side pull connector was
removed in AIPOS-F103). Workstations are opened with `/go` or launched by an authorized `lybra loop`;
claiming is done by the advisor's `lybra loop` under an Owner-signed envelope.
