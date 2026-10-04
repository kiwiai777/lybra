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

## Retired: the agent-side pull connector

The executor pull skill `skills/lybra-executor` (AIPOS-248) and its gate-pull client are retired
together with the old cross-machine connector (Owner ruling; the code is removed by a follow-up card):
executors do not look for, claim or return work themselves. Workstations are opened with `/go` or launched by an authorized `lybra loop`; claiming is done by the advisor's `lybra loop` under an
Owner-signed envelope. (Historical finding F-248-o3-3 from that connector: its slash-prefixed trigger
was never a registered Claude Code command.)
