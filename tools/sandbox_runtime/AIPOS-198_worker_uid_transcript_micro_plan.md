# AIPOS-198 Micro-Plan — Confined-worker UID match (F-c9) + redacted transcript capture (F-c10)

Status: micro-plan (awaiting Owner review)
Date: 2026-06-18
Scope: Layer-2 Wall only (`tools/sandbox_runtime/confined_worker.py` + its tests)
Loop: implement → full tests → cc glm audit (incl. real-container smoke + redaction proof) → Owner spot-check → finalize

## 0. Why (191B node-3 evidence)

- **F-c9 (HIGH, loop-blocker).** The container runs as the image's default uid (root).
  `/etc/lybra/mcp.json` is bind-mounted `readonly` and written `0600` owned by the host
  orchestrator uid. On WSL2 the bind-mount applies host-owner semantics; claude-code's
  own owner check on the MCP config rejects it (EACCES) → the worker never reaches the
  gate. Proven fix direction in node 3: run the container as the **host file owner's uid**
  (manual `--user 1000:1000` made the read succeed).
- **F-c10 (evidence gap).** `run_confined_worker` already does `capture_output=True` but
  **discards** stdout/stderr. A failed run leaves no diagnostic in the report — F-c9 was
  only found by a manual re-run, not from the worker report. We must persist the
  transcript into the report, but only **after redaction**.

## 1. F-c9 — `--user` derived from the mounted token-file owner

### Behaviour
- `build_docker_argv` inserts `--user <uid>:<gid>` into the `docker run` argv.
- Default uid/gid = **owner of `request.mcp_config_path`** (the file claude-code owner-checks).
  Rationale: matching that file's owner is exactly what makes the readonly `0600` mount
  readable inside the container; deriving from the file (not a constant) is correct on any
  host and **never hardcodes 1000**.
- CLI override `--run-as-uid` / `--run-as-gid` (or a single `--run-as UID[:GID]`); when set,
  use those instead of the derived owner. Optional `--no-user` escape hatch documented but
  **off by default** (kept only for images that genuinely need root and a non-WSL host).
- Implementation note: the token file is written by `write_mcp_config_file` inside
  `run_confined_worker` *after* argv is built today. So derive the uid/gid from the
  **intended owner** at request-build time:
  - `build_request_from_args`: stat the orchestrator (current process) — `os.getuid()/os.getgid()`
    — since `write_mcp_config_file` runs as this process and the file inherits this owner.
    Store as `request.run_as_uid` / `request.run_as_gid` (new optional fields).
  - This keeps `build_docker_argv` pure (no filesystem stat of a not-yet-written file) and
    makes the derivation deterministic + testable. The token file owner == orchestrator uid
    by construction, so "derive from token-file owner" and "derive from orchestrator uid"
    are the same value; we document it as **token-file-owner match** (the security-relevant
    framing).
- New `ConfinedWorkerRequest` fields: `run_as_uid: int | None = None`, `run_as_gid: int | None = None`.
  When both None and `--no-user` not set → default to orchestrator uid/gid in
  `build_request_from_args`. `build_docker_argv` emits `--user` iff `run_as_uid is not None`.

### Why `--user <non-root>` is *more* secure and compatible with the envelope
- Drops the last ambient-root surface inside an already `--cap-drop ALL` +
  `no-new-privileges` + `--read-only` container — strictly hardens posture.
- Writable paths still work: `--tmpfs /tmp` is world-writable; `HOME=/tmp` and
  `CLAUDE_CONFIG_DIR=/tmp/.claude` sit on it; `/scratch` is provisioned `0777`
  (`provision_scratch_dir`) so any uid writes outputs. Read-only rootfs unaffected.
- The readonly `0600` mcp.json becomes readable because container uid == file owner.
- No new mount, no new capability, no network change. Pure uid alignment.

### Why not env-Bearer delivery (the alternative)
- Discussed and **rejected for v0**. Putting the gate Bearer in an env var (vs the readonly
  0600 mount) would (a) widen exposure to `docker inspect` / child-process env, and (b)
  require teaching the projected claude harness to build mcp config from env — more surface.
  The `--user` match keeps the existing 0600-mount delivery (already audited in 196b) and
  only fixes the readability mismatch. Env-Bearer stays a documented fallback for hosts
  where uid match is impossible (none currently).

## 2. F-c10 — capture container stdout/stderr, redact before it touches the report

### Behaviour
- `run_confined_worker` keeps `capture_output=True`; on every branch (completed / timeout
  via `TimeoutExpired.stdout|stderr` / docker_unavailable) collect whatever stdout/stderr
  exists.
- **Redact before storing.** Reuse the Slice-A truth-scan approach:
  - Scan needles = `all_raw_secrets(connection_json)` (every raw role token) **+** the LLM
    key (`anthropic_key` from the cred env) **+** the live `request.mcp_token`.
  - New `redact_transcript(text, secrets) -> (clean_text, hits)`: replace each raw needle
    with `«redacted:<fingerprint>»` (`secret_fingerprint`); record which fingerprints were
    hit. Empty/None-safe; never emits a raw needle even in the "hits" list.
- Report gains a `transcript` block:
  ```
  "transcript": {
    "captured": true|false,
    "stdout": <redacted>, "stderr": <redacted>,
    "stdout_bytes": <int>, "stderr_bytes": <int>,   # original sizes (pre-truncation)
    "truncated": bool, "max_bytes": <cap>,           # tail-kept cap, e.g. 64KiB each
    "redaction": {"scanned_needles": <count>, "redacted_fingerprints": [<fp>...]}
  }
  ```
- Truncation: cap each stream (e.g. 64 KiB, keep tail) to bound report size; redact **before**
  truncation so a secret split across the cut can't survive. Note the cap in the report.
- `build_worker_report` takes new `stdout`/`stderr` (already-redacted) params; dry-run →
  `captured:false`, empty strings. The transcript is the redacted string the report holds;
  the raw bytes are never written anywhere (not logged, not to disk, not to git).

### Belt check
- A final `assert_no_secrets`-style guard on the assembled transcript block (reuse needle
  list) — if any raw needle still present after redaction, raise `ConfinedWorkerError`
  rather than emit the report (fail-closed, same philosophy as projection scan).

## 3. Explicitly NOT in scope

- No change to AIPOS-197 scope/confirm logic, `service_mode.py` roles, or the gate.
- No change to AIPOS-196a ingestion, the projection secret scan, scratch perms (0777 stays),
  or the Layer-3 scanner.
- No per-op Owner nonce / gate signature (AIPOS-193 §9 stays deferred).
- Nothing outside `confined_worker.py` + its test module. No 191B evidence-workspace touch.

## 4. Tests (`tools/sandbox_runtime/tests/...`)

1. **`--user` present + correct value.** `build_docker_argv` with `run_as_uid/gid` set →
   argv contains `--user <uid>:<gid>`; assert the value equals the derived owner.
2. **uid derived from token-file owner.** `build_request_from_args` (or a focused unit) →
   `run_as_uid == os.getuid()` by default; `--run-as-uid N` overrides; `--no-user` → no
   `--user` in argv.
3. **Transcript captured + redacted.** Feed fake stdout/stderr containing a planted fake
   token + fake LLM key (from a synthetic connection.json) through `redact_transcript` /
   report build → planted secrets absent; `«redacted:<fp>»` present; `redacted_fingerprints`
   lists the right fingerprints; raw value appears nowhere in the report dict (deep scan).
4. **Truncation + redaction order.** Oversized stream with a secret straddling the cut →
   still redacted, `truncated:true`, size fields correct.
5. **Fail-closed guard.** If a needle survives redaction (simulate) → `ConfinedWorkerError`.
6. **Regression.** Full `tools/` suite green (executor baseline 368 + new tests); existing
   196b/196c/preflight argv/report tests still pass (argv now has `--user` — update the
   affected assertions deliberately, not by loosening them).

## 5. cc glm audit points (independent; no self-audit)

- **Real-container smoke (make-or-break):** with `--user` derived from the 0600 token-file
  owner, an in-container `claude` actually **reads** `/etc/lybra/mcp.json` and **reaches the
  gate** (e.g. a benign `mcp__lybra__*` call resolves; no EACCES). This is the empirical
  closure of F-c9.
- **Redaction has no leak:** inject known fake secrets into the worker's stdout/stderr; prove
  the report/log/git contain only fingerprints, never the raw value (including the straddle-
  truncation case).
- **Security envelope unchanged:** still `--rm`, `no-new-privileges`, `cap-drop ALL`,
  `--read-only`, `--tmpfs /tmp`, dedicated bridge, 0600 readonly token mount, env-passthrough
  LLM cred, teardown verified — only `--user` added (non-root) and transcript added (redacted).
  No scope/196a/L3 change.

## 6. Loop

micro-plan (this) → Owner review → Owner approve → implement → full `tools/` tests →
cc glm audit (real-container smoke + redaction proof + envelope) → Owner spot-check →
Owner approve → finalize (product commit + governance docs: AIPOS-198 report, decision_log
entry, project_status/roadmap update; no new stage archive).
