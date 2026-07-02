# Lybra v1.0 — macOS Track-2 exercise scaffold

- **status: final** — R-PASS (B1 fail-loud probe + B2 native-macOS pin folded). Refreshes
  `docs/v1_release_macos_runbook.md` (N0–N7 spine) with the post-238/239 live exercise.
- **★ B2 — Track-2 = NATIVE macOS, run ON THE MAC ITSELF. NOT SSH → WSL.** The point of Track-2 is to
  exercise the **install product on real macOS** (BSD `stat`/`shasum`, no `fuser`, mac terminal +
  clipboard). Running it over `ssh` into a WSL/Linux box re-tests Track-1 and proves nothing about
  macOS portability. Open a terminal **locally on the Mac** (iTerm2/Terminal.app) and run there.
- **Owner-run, out-of-band, R guiding live.** Mirrors the WSL2 Track-1 on a real Mac to prove the
  **install product** is correct macOS-native (no `fuser`, BSD `stat`/`shasum`).
- **Red lines:** owner token read at runtime from `connection.json` by role — never typed on a CLI.
  Tokens/keys **fingerprint-only** in any output. Fresh **disposable** home; never touch an evidence
  workspace. **Form A Wall (`confined_worker`) is Linux-only — NOT covered on macOS.**

> Most nodes below are **bundled into `~/o3-launch.sh`** (the AIPOS-239 launcher: clean-slate via
> project-agnostic `serve stop`, no `fuser`; teardown same). The launcher is the *driver*; this
> scaffold is the **verify checklist** for what to watch inside/around each run. LLM endpoint =
> `https://goswitch.online/v1`, model `claude-sonnet-4-6` (already set in the launcher).

## Pre-flight (once)
- `node -v` (18+), `python3 -V` (3.10+).
- **Bare** gate venv (NO PyYAML) + a **textual** venv:
  ```bash
  python3 -m venv /tmp/lybra-bare-venv
  /tmp/lybra-bare-venv/bin/python -c "import yaml" 2>&1 | tail -1   # must say: No module named 'yaml'
  python3 -m venv ~/o3-textual-venv && ~/o3-textual-venv/bin/python -m pip -q install "textual>=0.50"
  ```
- LLM key, fingerprint-only:
  ```bash
  printf '%s' 'sk-...' > ~/.lybra_planchat_key && chmod 600 ~/.lybra_planchat_key
  shasum -a 256 ~/.lybra_planchat_key | cut -c1-12    # record fp only (BSD shasum, not sha256sum)
  ```
- **B1 — fail-loud endpoint + key probe (run BEFORE anything else).** Catches a dead endpoint / bad
  key / missing-UA WAF block up front, instead of discovering it at N3 after the whole setup. Requires
  **200**; sends the same static `User-Agent` copilot uses (F-o3-1) so it validates the real WAF path.
  Key stays fingerprint-only (only the status code is printed):
  ```bash
  code=$(curl -s -o /dev/null -w '%{http_code}' https://goswitch.online/v1/models \
    -H "Authorization: Bearer $(cat ~/.lybra_planchat_key)" -H "User-Agent: lybra-planchat/1.0")
  echo "endpoint /v1/models status: $code   (expect 200)"
  [ "$code" = "200" ] || { echo "FATAL(B1): endpoint/key not 200 (got $code) — a 403 = WAF/UA, 401 = bad key, 000 = unreachable. Fix BEFORE Track-2."; exit 1; }
  ```
  *(If this endpoint doesn't implement `/v1/models`, swap in a 1-token `POST /v1/chat/completions`
  with `{"model":"claude-sonnet-4-6","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}` and
  require 200 the same way. Either way it must be **200 before you proceed**.)*

## N0 — install (tarball → npm i -g on bare python; zero-dep gate)
```bash
cd <lybra repo clone>; npm pack
PREFIX=/tmp/lybra-rg2-prefix; rm -rf "$PREFIX"; mkdir -p "$PREFIX"
npm i -g --prefix "$PREFIX" ./lybra-*.tgz
```
Or let the launcher do it: `REBUILD=1 ~/o3-launch.sh` (packs the working tree + reinstalls).
**Verify:** `"$PREFIX/bin/lybra" --help` runs on bare python (no PyYAML import error).
**Shipped == tested (R-2 three-part proof):** `diff -r --exclude=tests --exclude=__pycache__
--exclude='*.pyc' <repo>/tools "$PREFIX/lib/node_modules/lybra/tools"` → empty; pin the product
`.py` count; acceptance-on-installed = isolation + A/B/C PASS (test-driven anchors `NO TESTS RAN` =
expected, tests not shipped).

## N1 — serve lifecycle (AIPOS-238)
Launched inside `~/o3-launch.sh` §5. **Verify in the launcher output:** clean-slate `serve stop`
(no `fuser` line); D1 connect() pre-check does not false-BLOCK a first start; **authenticated-200
readiness** ("gate up + OUR owner token accepted (authenticated 200)") — NOT a bare "401=up".
`connection.json` perms echo = `600` (via the new cross-platform `perms()` → BSD `stat -f`).

## N2 — banner + first screen + gate connected
In the TUI (launcher §6): banner renders; **no `build_app` TypeError** (AIPOS-216); status line ends
`· mode copilot` (first screen IS copilot when LLM configured); gate indicator shows **Connected**.

## N3 — copilot chat (F-o3-1: 403→200 on goswitch.online/v1)
Type one plain-language sentence (no `/` prefix), Enter. **Verify:** an **immediate spinner, no
freeze** (LLM off the event loop); a **conformant** task card; **writes NOTHING** to disk;
`context_bundle` **not fabricated** (surfaced as `needs_bundle`, not invented); no hallucinated
fields / no secrets. **This is the F-o3-1 close condition** — the outbound LLM request now carries a
`User-Agent`, so the WAF returns **200 not 403**. (Owner: judge the 5 quality anchors.)

## ★A1 — copilot credential cannot confirm/publish
With a **copilot**-role token, attempt any `*_confirm` / `draft_publish`. **MUST** return
`SCOPE_DENIED` (copilot scopes `[]`; structurally cannot owner_confirm / draft_publish). A PASS here
would be a release-blocking regression.

## N4–N6 — gated publish loop [Owner OOB]
Owner confirms with the **owner** token (read at runtime by role): stage card → `draft_publish`
**dry-run** (no write) → **confirm** publish (`OWNER_CONFIRMED`) → executor claim confirm → return
confirm (`owner_policy:supervised`) → **L3 authority scan = VALID**. **Verify on disk:** publish/claim
records `confirmer_role: owner`; pending→claimed→returned; L3 **VALID**. ★ If an audit-independence
path BLOCKs on bare python (`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`), that is the **correct
fail-closed** outcome — to show a *passing* independent audit, run that one segment with PyYAML.

## CJK + native copy (AIPOS-237)
In the chat box: **type 中文 directly** (it lands — DISAMBIGUATE-only kitty protocol; earlier builds
only pasted); **Shift+Enter AND Ctrl+J** both newline; the TUI runs **mouse capture off**
(`run(mouse=False)`) so iTerm2 keeps native selection + scrollback + **Cmd+C copies any selected
text** (Claude-Code parity). Quit = **Ctrl+C** or `/quit`.

## Enforcement — /project switch → PROJECT_SCOPE_DENIED (R2 Slice 5)
Token is scoped to `$PROJECT_A` (launcher mints `rotate --project`). `/project switch demo` then a
gated read (`/queue` or `/agents`) → **`PROJECT_SCOPE_DENIED`** (18 gated / 0 exempt; owner included —
no client-side fake isolation). `/project switch $PROJECT_A` → back in scope, reads work.

## /agents monitoring (AIPOS-234)
`/agents` → the 4 seeded fixtures: **alice** + **bob** (1 each), **carol** (assigned_to=dave ≠ owner —
divergence shown, not collapsed), + **unassigned** bucket; labelled **"as recorded — not live"**;
renders **once** (no auto-refresh / timer / heartbeat).

## Lifecycle — no 401 / no orphans across two runs (AIPOS-238 + 239, macOS = no fuser)
```
REBUILD=1 ~/o3-launch.sh      # run 1 → quit the TUI with Ctrl+C (teardown via `serve stop`)
REBUILD=1 ~/o3-launch.sh      # run 2 IMMEDIATELY
```
**Verify:** run 2's clean-slate finds ports free **without `fuser`**; authenticated-200 readiness
passes; **no 401**. After each quit, **no orphaned board/mcp** holding `:7117/:7118`
(`lsof -iTCP:7117 -sTCP:LISTEN` and `:7118` → empty; mac has no `fuser`). Also do a **mid-run exit**
variant (quit run 1 partway) → run 2 still clean. *(Note F-wrapper-sig: `kill -TERM <lybra-pid>` is
NOT a supported teardown — the node wrapper orphans children; use Ctrl-C or `serve stop`.)*

## Correctness leg — macOS bare-python acceptance/bare lane (correctness, NOT import)
From a **repo clone** (tests aren't shipped), on the **bare** venv:
```bash
cd <repo clone>
PYTHONPATH=. /tmp/lybra-bare-venv/bin/python -m tools.acceptance.v1_acceptance   # expect: ACCEPTANCE: PASS
PYTHONPATH=. /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"
```
**Verify:** `ACCEPTANCE: PASS` (correctness A/B/C with all third-party blocked — this is the
zero-dep *correctness* proof, not mere import success); bare unit lane green.

## #2 transport flake — macOS multi-environment measurement
```bash
cd <repo clone>; f=0; for i in $(seq 1 30); do \
  PYTHONPATH=. /tmp/lybra-bare-venv/bin/python -m unittest tools.mcp_server.tests.test_http_sse_transport \
  >/dev/null 2>&1 || { f=$((f+1)); echo "round $i FAIL"; }; done; echo "#2 macOS flake: $f/30"
```
**Verify:** record the rate honestly. WSL2 = 0/30 (clean `/tmp`). If it reproduces on macOS,
**characterize the exact root cause on the spot** (accept-race already refuted — do not assume). If
0/30, **#2 stays OPEN** (do not claim fixed).

---

## F-* finding template (use for anything surfaced live)
For each finding, capture:
- **id:** `F-o3-<n>` (O3/live) or `F-<slug>` (mechanism).
- **现象 (symptom):** what was seen, verbatim (error text / screenshot note).
- **复现 (repro):** exact steps + environment (macOS ver, terminal=iTerm2 → SSH → WSL? or native).
- **影响面 (blast radius):** which surface (gate / copilot / TUI / serve / launcher); does it touch an
  invariant (★A1 / two-root / gate-not-engine / zero-dep)?
- **严重度 (severity):** blocker / substantive / cosmetic.
- **triage:**
  - **cosmetic** → batch (one cleanup slice at the end).
  - **substantive** → its own micro-slice (DRAFT → R → impl → cc glm → finalize).
  - **defer** → register OPEN with a disclosure sign-off (why v1.0-safe), like F-wrapper-sig.

## What to report back
Per node: key output (fingerprint-only) + on-disk re-check. Headline set: N0 diff-empty + acceptance
PASS; N1 authed-200 + `600`; N3 conformant card + 5-anchor scores + **F-o3-1 200 not 403**; ★A1
`SCOPE_DENIED`; N4–N6 `confirmer_role: owner` + **L3 VALID** (+ any independence fail-closed noted
correct); CJK direct-type + Cmd+C copy OK; enforcement `PROJECT_SCOPE_DENIED`; /agents 4-row one-shot;
**two-run lifecycle: no 401 / no orphans (no fuser)**; correctness leg `ACCEPTANCE: PASS`; **#2 rate
`f/30`**. Any macOS finding → F-* template (register, do not hard-fix live).
