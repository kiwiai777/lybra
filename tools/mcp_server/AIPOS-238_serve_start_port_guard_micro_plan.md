---
id: AIPOS-238
title: serve lifecycle fail-closed — orphan/stop/port hardening (F-o3-13; v1.0 macOS blocker)
status: draft
authority: NONE
task_class: simple
phase: micro-plan DRAFT (R deep-dive folded; pre direction-audit)
parent: release-gate O3 finding F-o3-13 (the 401's real root cause = serve lifecycle)
symbols_read_from: main @ 6fbe7c7 (post-AIPOS-237)
r_reinforcements_folded:
  - "R deep-dive: real root cause is serve LIFECYCLE fail-closed, not just port pre-check"
  - "Part 1 (lifecycle) BEFORE Part 2 (startup validation)"
  - "D1 uses a connect() probe (active-listener), NOT SO_REUSEADDR-off (real server allow_reuse=1 → TIME_WAIT false-BLOCK)"
  - "C1b (must-fix) B×D2: classify by child EXIT REASON (returncode<0 signal → PASS, incl. external serve stop; >0 self-exit → BLOCK) — NOT a self-set flag (misses external stop)"
  - "C2 stop locates the state file via connection_target (--connection-json), never project resolution"
  - "C3 connect() pre-check covers BOTH board+mcp; SIGTERM/SIGINT cleanup idempotent"
  - "F-NEW-c RETRACTED — R misread a singular field; actual projects:['lybra'], scope correct; not logged"
---

# AIPOS-238 — serve lifecycle fail-closed hardening (F-o3-13)

> **Nature.** DRAFT only. authority: NONE. No product code, no commit. DRAFT → R direction-audit →
> Owner approves → implement (Part 1 → Part 2) → cc-glm audit → `cc glm` → R re-check → **Owner macOS
> O3 confirm** → finalize (F-o3-13 CLOSED). Symbols read from `main` @ `6fbe7c7`.

## §0 Thesis

The 401 is deeper than "a stale server on the port": **`serve`'s lifecycle fail-closes**, leaving
orphaned children that keep the ports with OLD tokens, and a `serve stop` that cannot clean them.
On macOS the script's `fuser -k` fallback does not exist, so **the 401 reproduces reliably on
Track-2 — a v1.0 macOS blocker.** Fix the product lifecycle (Part 1) first, then startup validation
(Part 2).

## §1 Grounding — independently reproduced (per R's clues, on `main` @ 6fbe7c7)

- **F-NEW-b (orphans on SIGTERM):** `start_report` supervises with `except KeyboardInterrupt` +
  `finally: _terminate_processes` (`service_mode.py:~602-625`) — cleanup runs **only on SIGINT**.
  **Reproduced:** `kill -TERM <parent>` → the board + mcp children are **STILL ALIVE (orphans)**,
  holding :7118/:7117 with the old tokens. (Any non-Ctrl-C death — plain `kill`, a script crash, a
  non-SIGINT trap — orphans them.)
- **F-NEW-a (stop can't kill without a project):** `aipos_cli.py:1167` runs
  `_resolve_workspace_for_command(args)` for **every** serve subcommand incl. `stop`, and
  `stop_report` (`:1183`) is keyed on the resolved `workspace_root`. **Reproduced:** `serve stop`
  with no `LYBRA_HOME_ROOT` → `Error: PROJECT_NOT_ESTABLISHED …` and the recorded PIDs are **NOT
  killed** (mcp/board still ALIVE). Stop is a pure lifecycle op; it must not depend on project
  establishment.
- **Fake PASS (D2):** `start_report` returns `{"ok": True, "verdict": "PASS"}` (`:626-635`)
  regardless of why a child exited — a bind-failed MCP child (OSError from
  `http_sse.build_http_server` → `ThreadingHTTPServer` bind, `:362-372`, before the "listening" line)
  is still reported PASS.
- **allow_reuse_address = 1** on the real server (`ThreadingHTTPServer`, verified) → a bind-based
  availability probe with `SO_REUSEADDR` off would **falsely BLOCK** a legitimate restart while the
  just-stopped port is in `TIME_WAIT`. The correct probe is a **`connect()`** ("is anyone actively
  listening?").

## Part 1 — lifecycle integrity (do first; the real fix)

- **B — supervisor cleans on SIGTERM (+ SIGHUP), not just SIGINT.** Install signal handlers for
  `SIGTERM` (and `SIGHUP`) that route to the SAME `_terminate_processes` cleanup then exit — pure
  cleanup on shutdown, **not** a daemon / not a long-running handler. So a plain `kill` / script exit
  reaps board + mcp (no orphans). **C3: SIGTERM/SIGHUP and the existing KeyboardInterrupt path share
  one idempotent cleanup** (safe if both fire — terminate is a no-op on an already-exited child).
- **A — `serve stop` is project-agnostic (C2).** `stop` locates the recorded `service_state.json`
  **via `connection_target` (the `--connection-json` path), NOT via workspace/project resolution**,
  and SIGTERMs the `service_owned: true` PIDs — **without** project resolution and **without**
  fail-closing on `PROJECT_NOT_ESTABLISHED`; so `serve stop --connection-json X` works even with no
  `LYBRA_HOME_ROOT` / no established project. (Requires lifting the `_resolve_workspace_for_command`
  precondition off the `stop` subcommand in `aipos_cli.py:1167`, which currently fail-closes before
  `stop_report` at `:1183`.) **Safety boundary (unchanged/strengthened):** still ONLY kills PIDs
  recorded `service_owned: true` in *this* state file (never escalates to arbitrary PIDs); the state
  file's `0600` check is kept.

## Part 2 — startup validation

- **Pre-check + D1 — refuse an occupied port (connect() probe; C3 = BOTH ports).** Before spawning,
  probe **both** the MCP and the Board `(host, port)` with a **`connect()`** (active-listener test —
  immune to `TIME_WAIT`, matches "is an old serve still answering?"). If a listener is present on
  either → **loud BLOCK** (`ok: False`, `verdict: BLOCK`, blocking_reason naming the exact port(s) +
  remedy: `lybra serve stop` / a different `--mcp-port`), and **do NOT spawn**. Do **not** use a
  `SO_REUSEADDR`-off bind (would false-BLOCK a legit restart, §1).
- **D2 — supervisor distinguishes crash vs clean shutdown by the child's EXIT REASON (★ C1b).** A
  naive "a child exited → BLOCK" would falsely BLOCK a deliberate shutdown. A self-set
  `deliberate_shutdown` flag is **insufficient** — it only covers the supervisor receiving a signal;
  an **external `serve stop`** kills the *children* directly (by state-file PID) **without signaling
  the supervisor**, so the still-alive supervisor would see them exit with no flag → false BLOCK.
  **Fix = classify by the child's `returncode`** (`Popen.poll()`): **killed-by-signal
  (`returncode < 0`, e.g. `-SIGTERM` from our own `_terminate_processes` OR from an external
  `serve stop`) → deliberate → PASS; clean `returncode == 0` → PASS; a self non-zero exit
  (`returncode > 0`, a real crash / bind failure that slipped the pre-check) → `ok: False` /
  `verdict: BLOCK` naming the child + exit code + `serve.log` tail.** Exit-reason naturally covers
  both internal shutdown and external stop — no flag needed.
- **D3 (suggested) — bounded one-shot readiness.** After spawn, a **single** bounded readiness probe
  (ideally an authenticated 200 with our own token); failure → BLOCK. **No continuous health polling
  / heartbeat / scheduler** (gate-not-engine).

## §Red lines

- `service_mode` + stdlib (`socket`, `signal`) only; **no new dependency**. **No gate / ★A1 /
  dual-root / zero-dep / copilot / accountability change.**
- **Happy path byte-equivalent in behavior:** a normal `serve start` (free ports) still runs in the
  foreground + supervises, and **Ctrl-C still exits cleanly** exactly as today.
- `stop` **never** escalates to killing arbitrary PIDs (only `service_owned` in the state file).
- All probes are **bounded, one-shot** — no runtime / daemon / scheduler added.
- DRAFT writes no code, commits nothing; cc holds no owner token / never confirms; evidence
  zero-touch.

## §verify = positive truth (tests must assert)

- **(i) F-NEW-b:** after `kill -SIGTERM <parent>`, board + mcp have **both exited** (counter-proof:
  today they orphan-survive).
- **(ii) F-NEW-a:** `serve stop` with no `LYBRA_HOME_ROOT` / no established project **still kills the
  recorded PIDs**, and kills **only** `service_owned` PIDs (a non-service PID in a tampered state
  file is NOT killed).
- **(iii) D2:** a child that self-exits with a **non-zero** code (`returncode > 0`, real crash /
  bind failure) → `verdict: BLOCK` (not PASS), naming the child + exit code.
- **(iii-C1b) exit-reason:** a child **killed by signal** (`returncode < 0`) → **PASS** — covers BOTH
  our own `_terminate_processes` (SIGTERM/Ctrl-C) AND an **external `serve stop`** that kills the
  children while the supervisor is still alive (no flag involved); a clean `returncode == 0` → PASS.
- **(iv) D1:** an active listener on the port → pre-check **BLOCK + named port + no spawn**; a port
  just stopped / in `TIME_WAIT` does **NOT** false-BLOCK (regression guard for the connect() probe).
- **(v) happy path:** free ports → PASS + supervise; Ctrl-C → clean exit — both unchanged.
- three lanes green + ACCEPTANCE; `git diff` = `service_mode.py` (+ its test) only.
- **★ O3 real hardware (final verdict):** on the macOS npm-installed prefix, running
  `~/o3-launch.sh` **twice in a row (incl. a mid-run exit)** → no 401, no orphaned port holders.

## §F-NEW-c — RETRACTED (verified false alarm)

R's direction-audit retracted F-NEW-c: it was a misread of a *singular* `project` field. The minted
tokens correctly carry `projects: ['lybra'], projects_enforced: True` (confirmed in the F-o3-13
connection.json dump) — the scope landed correctly. **Nothing to fix; no decision_log entry.**

## §Downstream (F-launch — after this product fix; NOT this slice)

Once Part 1 lands, `~/o3-launch.sh` simplifies: give the cleanup line `LYBRA_HOME_ROOT` /
`--workspace-root`, switch teardown to a clean `serve stop` (no raw `kill`), and **drop the `fuser`
dependency** (rely on the fixed project-agnostic `serve stop`). Not part of this slice.

**Flow:** DRAFT **(R direction-audit PASS; C1/C2/C3 folded, F-NEW-c retracted)** → **Owner approves**
→ implement Part 1 → Part 2 → `cc glm` → R re-check → **Owner macOS O3 confirm** → finalize
(F-o3-13 CLOSED).
