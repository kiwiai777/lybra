---
id: AIPOS-236
slice: A
title: fix F-o3-1 — copilot LLM request missing User-Agent → 403 from WAF-protected endpoints
status: draft
authority: NONE
task_class: simple
phase: micro-plan DRAFT (pre direction-audit)
parent: release-gate O3 finding F-o3-1 (AIPOS-235)
symbols_read_from: main @ c282aa4 (post-AIPOS-234)
---

# AIPOS-236 Slice A — copilot LLM User-Agent (fix F-o3-1)

> **Nature.** DRAFT only. authority: NONE. Writes no product code, commits nothing.
> Output is this file → R direction-audit → Owner approves → implement + test → auto-write
> cc-glm audit card → `cc glm` → R re-checks → Owner finalize. Symbols read from `main` @ `c282aa4`.

---

## §0 Thesis

Release-gate O3 surfaced **F-o3-1**: the copilot chat is completely unusable against a WAF-protected
OpenAI-compatible endpoint — the TUI shows *"Copilot error: HTTP Error 403"*. **Owner reproduced it**
with the same `urllib` direct-call shape copilot uses against `goswitch.online/v1`:
- copilot-style (**no `User-Agent`**) → **HTTP 403 Forbidden** (exact repro).
- with `User-Agent: curl/8.0` → **HTTP 200 OK**; with `User-Agent: lybra-copilot/0.2` → **200 OK**.

**Root cause:** copilot's outbound `/chat/completions` request sets only `Authorization` +
`Content-Type` and **no `User-Agent`**, so `urllib` sends the default `Python-urllib/3.x`, which a
Cloudflare-class WAF rejects with 403. Endpoint / key / model are all correct (direct `curl` = 200).

**Fix = the minimal one line:** add a standard `User-Agent` header to that one outbound call.

---

## §1 Grounding (verified on `main` @ c282aa4)

- **The exact site:** `tools/lybra_tui/copilot.py`, `complete()` — `headers = {"Authorization": …,
  "Content-Type": "application/json"}` (no `User-Agent`); the request goes out via
  `self._opener.open(req, …)` (a plain `urllib.request` opener with an empty ProxyHandler). This is
  the only outbound LLM call.
- **No reusable version constant exists:** a tree-wide search for `__version__` / `VERSION =` /
  `LYBRA_VERSION` in `tools/**` returns nothing. `package.json` carries `0.2.0`, but reading it from
  Python at request time would add file coupling for no benefit. ⇒ per the Owner's rule ("reuse an
  existing version if present, else static"), use a **static** value.

---

## §2 Change (minimal — one header, one site)

Add a `User-Agent` to the `complete()` request headers in `copilot.py`, via a module-level constant:

```python
_USER_AGENT = "lybra-copilot"          # static; no version coupling, no external dependency
...
headers = {
    "Authorization": f"Bearer {self._config.api_key}",
    "Content-Type": "application/json",
    "User-Agent": _USER_AGENT,
}
```

- Value = `lybra-copilot` (a clean product token — **not** a random/hardcoded junk string, **not**
  coupled to `package.json`). Named constant so it is single-sourced + directly assertable in a test.
- Nothing else changes: same URL, method, body, opener, timeout, telemetry.

---

## §3 Red lines (must not cross)

- copilot stays **read-only / scopes `[]` / zero file write**; **no accountability-logic change** —
  this is purely one standard HTTP header added to an outbound LLM call.
- Does **not** touch gate / ★A1 / dual-root / zero-dep; **stdlib-only** (`urllib` already in use, no
  new dependency).
- raw key/token remain **fingerprint-only** — never in argv / logs / records; unaffected (the header
  carries the static UA, not the key).
- DRAFT writes no product code and commits nothing; evidence zero-touch; cc holds no owner token and
  never confirms.

---

## §4 verify = positive truth

- **unit test:** the request copilot builds carries a **non-empty `User-Agent`** header — assert the
  header is present AND equals the constant (`lybra-copilot`), not merely truthy. (Assert by
  intercepting the built `Request` / opener, so it verifies the actual outbound headers.)
- **existing copilot tests stay green:** chat/draft remain read-only, scopes `[]`, zero-write
  (workspace content hash unchanged) — no assertion dropped.
- **three lanes green + ACCEPTANCE;** `git diff` = `copilot.py` (+ its test) only.
- **(WAF not unit-testable)** record one O3 re-test item: after restarting the O3 script, copilot
  chat against the `goswitch` endpoint should go **403 → 200** — confirmed on real hardware by the
  Owner (this DRAFT does not claim the live WAF outcome, only the header presence).

---

## §5 Out of scope

- Any other copilot behavior; retry/backoff; configurable UA; provider-specific headers.
- The release-gate itself (this is one triaged O3 finding fixed as a micro-slice).

---

## Red lines / deliverable

- one `User-Agent` header at one site · copilot read-only/scopes `[]`/zero-write byte-unchanged ·
  UA value sane + static (no junk, no version coupling, no new dep) · test asserts the header is
  present with the expected value · stdlib-only · gate/★A1/dual-root untouched.
- DRAFT writes no code, commits nothing; cc holds no owner token, never confirms; evidence
  zero-touch.

**Flow:** DRAFT → R direction-audit (core: only a UA header added · copilot read-only/scopes[]/
zero-write unchanged · UA value sane, no hardcoded junk · test asserts header present · stdlib-only)
→ Owner approves → implement → `cc glm` → R re-checks the verdict → Owner finalize.
