---
task_id: AIPOS-231
title: WSL2 transport test-stability — no-regret harness hardening (no false-green)
status: draft
authority: NONE
parent: carried-forward independent item #2 (from govhome/R2 closeout, DL-20260628-06)
created_by: cc
created_at: '2026-06-28'
phase: DRAFT (converged to (b) per Owner → R direction-audit → Owner approve → implement → cc glm → finalize) — NOT implemented this session
resolution: converged to (b) no-regret hardening; accept-race hypothesis refuted by evidence (build_http_server does not pass bind_and_activate=False -> constructor binds+listens, so connect never races accept); root cause unreproducible in dev env; item #2 stays OPEN, measured at release-gate (AIPOS-220)
---

# AIPOS-231 — WSL2 transport test-stability: no-regret hardening (DRAFT, converged to (b))

> **DRAFT / authority: NONE.** No product code, no commit. Pure TEST-harness scope. Owner chose
> option (b): harden the known fragility, do NOT claim the flake fixed. Symbols/line refs read
> against `main` (post-AIPOS-230).

## 0. Thesis (converged)

The flake is **not reproducible in the current dev environment** (measured: 20/20 isolated transport
runs + ~6 full-suite bare runs, **0 flake**); the ~29% is specific to cc glm's audit environment.
The assumed **accept-race is refuted by evidence**: `LybraMcpHttpSseServer(ThreadingHTTPServer)`
(`http_sse.py:353`) does NOT pass `bind_and_activate=False`, so the constructor binds + listens
before the thread — a client `connect()` is queued in the backlog and never races `accept()`.

**Unreproducible ⇒ we cannot claim "fixed".** This slice does ONLY no-regret hardening of the test
harness (remove known load/timing fragility), does NOT report a false green, and leaves the true
root cause to a measured run at the release-gate across environments.

## 1. Changes (pure test harness — zero product code)

In `tools/mcp_server/tests/test_http_sse_transport.py` only:

1. **Centralize the magic timeouts** scattered across the file —
   `urlopen(timeout=3)` (`:225/:250/:477/:513/:526/:539…`) and `thread.join(timeout=2)`
   (`:169/:212`) — into named module constants (e.g. `_HTTP_CLIENT_TIMEOUT`,
   `_SERVER_JOIN_TIMEOUT`), single point of control.
2. **Widen the client timeout with a stated rationale** — raise the `urlopen` timeout to a
   justified, more generous value. Rationale (documented in the code): under WSL2 load the REAL
   response latency can exceed a 3 s budget; there is no product bug being hidden — the product
   response is correct, only the test's latency assertion was too tight. (Not an arbitrary big
   number — a reasoned bound, e.g. tied to the keepalive/test envelope.)
3. **Deterministic teardown** — replace `thread.join(timeout=2)` with `httpd.shutdown()` (cleanly
   stops `serve_forever`) → `thread.join()` until the thread truly exits (a loose upper cap may
   remain as a deadlock backstop), eliminating the "join timed out but the thread is still winding
   down" contention that can leak sockets/fds across the full suite.
4. **(Optional) a harmless readiness probe** as defense-in-depth ONLY — explicitly NOT the cure
   (the analysis shows connect never races accept), and it must NOT change any test's semantics.

## 2. ★ Red lines (test-stability slice)

- **Zero product code change** — `git diff` is test-file / helper only.
- **No weakened assertions to dodge the flake** — the transport tests still spin a REAL HTTP server,
  really connect, and really assert the same things (scope / token / redaction / zero-write — not
  one dropped). The change is timeout/teardown TIMING, never converting a real transport test to a
  mock or removing an assertion.
- stdlib-only (`socket` / `threading`); no new dependency; no gate-decision / product-behavior /
  other-lane change.

## 3. ★ Honesty (the core discipline of this slice — do not violate)

- **Do NOT claim the flake is fixed.** Item #2 stays **OPEN** in decision_log/roadmap, recorded as:
  *"Hardened the known load/timing fragility (centralized + widened client timeout; deterministic
  teardown). The accept-race hypothesis was refuted by evidence. Root cause UNCONFIRMED and not
  reproducible in the dev environment; deferred to a measured run at the release-gate (AIPOS-220,
  multi-environment / multi-round) where, if it reproduces, the exact root cause will be pinned. No
  fix claimed."*
- The verify (§4) proves **no-regression + removal of known fragility**, NOT flake elimination —
  "green in this environment" must NOT be presented as "flake solved".

## 4. Verify = positive truth (honest)

- `git diff` is test-file only (zero product code).
- three lanes green (BARE/SYSTEM/TUI) + ACCEPTANCE; BARE re-run multiple rounds (≥20×) still green
  — proving the hardening introduces NO regression (NOT proving the flake is gone).
- transport tests retain all original assertions (scope / token / redaction / zero-write).
- **Honest statement in the report:** unreproducible here ⇒ this slice demonstrates only
  "no-regression + known-fragility removed"; it does NOT claim the flake is eliminated; item #2
  remains OPEN, to be measured at the release-gate.

## 5. Out of scope

LLM key rotation (Owner-waived); decision_log directory-ization (R5); any feature/gate change;
pinning the true root cause (deferred to release-gate multi-environment measurement).

## Red lines / delivery

DRAFT: no product code, no commit; evidence sites zero contact; cc holds no owner token, does not
confirm. DRAFT (converged b) → R direction-audit (focus: zero product code · assertions
undiminished · timeout widening is reasoned not arbitrary · teardown made deterministic ·
stdlib-only · honesty preserved — item #2 stays OPEN, no false green) → Owner approve → implement →
cc glm → finalize (item #2 stays OPEN; true measurement at the release-gate).
