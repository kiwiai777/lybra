# AIPOS-200 Micro-Plan — Confined-worker default tool policy for /scratch (RF-6 / 196b follow-up)

Status: micro-plan (awaiting review) — not implemented
Date: 2026-06-19
Origin: AIPOS-191B clean rerun RF-6
Loop: implement → full tests → cc glm audit (incl. real-container e2e) → Owner approve → finalize

## 0. Problem (empirical, from the 191B rerun)

The confined worker's default `allowed_tools = "mcp__lybra__*"` means the in-container claude
can call ONLY MCP tools — it has no `Write`/`Edit`/`Read` tool, so it cannot write
`/scratch/three_layer_overview.md` (its own session permission gate blocks the write) nor read
`$LYBRA_SCRATCH_HOST_DIR`. The scratch → queue_return → AIPOS-196a path is unusable under the
default policy. The rerun worked around this with a CLI flag (`--allowed-tools
"Write,Read,Edit,mcp__lybra__*"`) + the scratch path in the prompt.

## 1. Why widening the tool policy is safe (security rationale)

L2 truth protection relies on **mount-exclusion** (truth paths, `.lybra`, product repo, host fs
are simply never mounted) + the **gate-only confirm** path — NOT on the tool allowlist. `/scratch`
is the only writable bind; the read-only rootfs + `--tmpfs /tmp` bound everything else. So
allowing file tools lets the agent write ONLY `/scratch` (+ tmpfs) — it still cannot write truth
(not mounted). Widening the default tool policy does not weaken the envelope.

## 2. Scope

- `tools/sandbox_runtime/confined_worker.py` — change the default `allowed_tools` so the agent
  can produce its scratch artifact out of the box.
- `tools/sandbox_runtime/tests/test_confined_worker.py` — update/add tests.

NOT in scope: AIPOS-197 scope gate, 196a ingestion, projection scan, Layer-3, service_mode,
network posture; no per-op nonce.

## 3. Fix (decide the default at review)

Change the default `allowed_tools` from `"mcp__lybra__*"` to a policy that includes the file
tools needed for /scratch plus the gate tools, e.g. `"Write,Read,Edit,mcp__lybra__*"`. Options
to confirm at review:
- (a) Add `Write,Read,Edit` (no `Bash`) — minimal; agent writes files but no shell.
- (b) Also include a constrained `Bash` — broader (reads env like `$LYBRA_SCRATCH_HOST_DIR`),
  but larger surface; default OFF unless Owner wants it.
- Keep `--allowed-tools` overridable (operators can still tighten/loosen per run).
- Recommended: (a) as the new default; document that truth-write is impossible by mount
  exclusion regardless of tools.
- Also consider injecting the scratch host path into the projection/prompt by default so the
  agent need not read it from env (reduces the need for Bash).

## 4. Tests

1. **Real-container e2e (make-or-break, in audit env):** with the DEFAULT tool policy (no
   `--allowed-tools` override), the in-container agent writes `/scratch/<artifact>.md` and a
   subsequent `queue_return` + Owner confirm gets it AIPOS-196a-ingested into
   `workspace_artifacts/...` (sha match). This is the inverse of the RF-6 failure.
2. argv: default `--allowedTools` value includes the file tools + `mcp__lybra__*`; `--allowed-tools`
   override still honored.
3. Envelope unchanged: truth paths still not mounted; read-only rootfs; scratch the only writable
   bind; teardown verified.
4. Full `tools/` suite green; update existing 196b argv assertions that pin the old default
   (deliberately, not loosened).

## 5. cc glm audit points

- Real-container smoke proves the agent writes /scratch under the DEFAULT policy and the artifact
  is 196a-ingested (not just an argv check).
- The widened tool policy does NOT enable any truth write (mount-exclusion holds): attempt an
  in-container write to a truth path → fails (not mounted / read-only).
- No 197/196a/Layer-3/scope change; no secret leak.

## 6. Loop
micro-plan → review (confirm default tool set) → approve → implement → full tests →
cc glm audit (real-container e2e + envelope) → Owner approve → finalize.
