# AIPOS-220 — v1.0 release-gate rerun: bare-python correctness + TUI O3, dual-platform (RUN PLAN)

- **status: draft**
- **authority: NONE** (run plan for Owner 复核; no live execution, no product code change until "复核 PASS, 批准执行")
- Date: 2026-06-25
- Kind: **governed live run, not a code slice** — no product code change; findings registered + triaged, never hard-fixed in-gate; manual finalize.
- Epic: v1.0 release readiness — supplements AIPOS-214 (gate ②③ of the three publish-blockers).

## Why this supplement
The three publish-blockers (DL-20260625-03):
1. **AIPOS-218 finalize — ✅ DONE** (Owner independently verified: bare venv 517 green + the hardened
   acceptance probe `ACCEPTANCE: PASS` incl. correctness A/B/C).
2. **bare-python install-product correctness** — prove the *shipped* product is correct on a system
   `python3` **without PyYAML** (not just the dev tree). ← this plan.
3. **TUI real launch (O3)** — actually launch `lybra tui` and confirm the AIPOS-216 `build_app` fix
   (no TypeError crash) + banner + first screen. ← this plan.
This plan runs ②③ on **two platforms** (Linux/WSL2 + macOS).

## ★ Bare-python condition matrix (authoritative — prevents mis-judging a correct fail-closed as a gate failure)
| Path | Bare python (NO PyYAML) | Verdict rule |
|---|---|---|
| `init` / `serve` / `claim` / `return` / `publish` / L3 scan | **must be correct, zero-dep** | ② core — any error = gate FAIL (register finding) |
| audit-verdict independence (if R6 triggers it) | **fail-closed BLOCK = CORRECT**, not a gate failure | ★ must NOT be mis-judged as failure; a *false PASS* would be the failure |
| legacy-alias resolution | requires PyYAML, else BLOCK | expected (canonical-only at gate v1.0) |
| TUI (O3) | requires `pip install textual` | ③ — separate from the zero-dep gate core |
| custom-profile registry / orchestration preview | requires PyYAML (loud warn/raise) | expected (disclosed row 10) |

## Track 1 — Linux/WSL2 (cc preps + runs R0–R3; Owner OOB R4–R6; Owner runs O3)
Fresh disposable workspace `~/lybra-rg2-workspace`; disposable npm prefix `/tmp/lybra-rg2-prefix`.
Evidence sites untouched (`~/lybra-191b-*`, `~/lybra-formb-workspace`, `~/lybra-copilot-workspace`,
`~/lybra-release-gate-workspace`).

- **R0 — install the product, NO PyYAML.** `npm pack` → `npm i -g --prefix /tmp/lybra-rg2-prefix
  ./lybra-<v>.tgz`. Use a python that LACKS PyYAML (a clean venv as `LYBRA_PYTHON`, or confirm system
  `python3` has none). Verify `lybra init` + `lybra serve` run. **Correctness assertions (reuse the
  AIPOS-218 WS5/WS6 oracle):** with PyYAML absent — (a) a rendered record round-trips losslessly;
  (b) a bundled manifest parses equal to its PyYAML baseline; (c) `lybra init` succeeds end-to-end.
  Evidence: pack manifest, resolved `lybra` path (from tarball), the three correctness checks pass
  with `yaml` import failing.
- **R1 — serve (bare python).** `serve rotate` mints executor/owner/copilot; `connection.json` 0600;
  MCP 401; tokens fingerprint-only.
- **R2 — first screen (bounded).** banner renders; copilot first-screen selected with LLM config.
- **R3 — real-LLM card (bare python).** Owner re-lands `LYBRA_PLANCHAT_LLM_KEY` (0600 file or env;
  fingerprint-only); copilot produces a **conformant** card on the installed product; Owner scores the
  5 §c quality anchors. (copilot uses urllib, not PyYAML — bare-python clean.)
- **R4–R6 — [Owner OOB].** Owner confirms publish / claim / return with the owner token out of band
  (release-gate helpers, re-pointed to `~/lybra-rg2-workspace`); cc reads disk: `confirmer_role=owner`,
  task → **L3 VALID**. **★ If the audit-verdict independence path is exercised on bare python and it
  BLOCKs (`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`), that is the CORRECT fail-closed outcome — not a
  gate failure.** To demonstrate a *passing* independent audit, run that segment once **with PyYAML
  installed** (documented as the legacy/independence path that needs the registry).
- **③ O3 — TUI real launch (Owner runs).** `pip install "textual>=0.50"` → `lybra tui --gate-url …
  --workspace-root ~/lybra-rg2-workspace --project … --llm-*` → confirm: **no `build_app` TypeError**
  (AIPOS-216 fix), the AIPOS-210 banner renders, the AIPOS-208 chat-to-task first screen appears, and a
  one-sentence NL ask produces a card. Owner eyeballs + reports.

## Track 2 — macOS (Owner runs the whole track on a Mac; cc supplies an executable runbook)
cc produces `docs/v1_release_macos_runbook.md` (a NEW doc — the only artifact cc writes for Track 2):
R0–R6 + O3 with concrete macOS commands (bare-python check, `pip install textual`, key via env,
every Owner-OOB gate). Owner runs it on macOS and returns evidence (command output, on-disk record
excerpts fingerprint-only); cc + Owner review the evidence. **Form A Wall (confined_worker) is NOT in
the macOS track — it is Linux-only (container/uid features); the macOS track covers gate / copilot /
TUI / Form-B only.**

## Disclosure / README — platform support matrix (docs update folded into finalize)
Add to `docs/v1_disclosure.md` + README a platform matrix:
- **Core (gate / copilot / TUI / Form B): Linux + macOS — verified** (by this gate's two tracks).
- **Windows: unverified** (the npm `bin/lybra` shim has a `win32` branch but it is not exercised).
- **Form A Wall (confined_worker): Linux-only** (container/uid isolation features).
Keep **claims ⊆ disclosure** (the AIPOS-213 README↔disclosure guard must stay green).

## Verdict & evidence (the report cc produces)
Per-track, per-step PASS/FAIL with immediate on-disk re-check:
- R0 install = tarball product (manifest + resolved path), NOT dev tree; **bare-python correctness
  A/B/C pass with PyYAML absent**.
- R1 connection.json 0600 / roles / 401. R2 banner + first-screen. R3 conformant card + Owner anchors.
- R4 `confirmer_role=owner` on disk; R6 **L3 VALID**; any audit-independence BLOCK on bare python
  recorded as **correct fail-closed** (with the PyYAML-present pass shown separately).
- O3 = TUI launches (no build_app crash) + banner + first screen + NL→card.
- Cross-evidence: `ACCEPTANCE: PASS` (now the ALL-third-party + correctness probe). Evidence sites
  untouched; cc never held the owner token; secrets fingerprint-only.

## Finding triage (no hard-fix in-gate)
- Install/correctness finding (tarball drop, bare-python parse error, `lybra init` fail, TUI crash) →
  register **F-rg-*** + separate fix slice; do not patch in-gate.
- Platform finding (macOS-specific path/shim issue) → register + triage.
- Environment (proxy/key flakiness) → record as environment note, not a product finding.

## Red lines
cc never holds the owner token / never confirms / never touches publish credentials (R4–R6 all Owner
OOB); disposable workspace + prefix; evidence sites zero-contact; **no product code change**; manual
finalize. Secrets fingerprint-only; `connection.json` 0600.

## ★ Not done
- **actual `npm publish`** — separate, irreversible, Owner-authorized release (5a); only after all
  three publish-blockers are green.
- code changes; Form A Wall macOS port; AIPOS-206b / R2 / R5; CI wiring.

## Exit
Run both tracks → cc produces the execution report (+ the macOS runbook for Track 2) → Owner 复核 →
PASS / finding. When ①✅ + ② + ③ are all green (both tracks), the three publish-blockers are cleared
and, **on separate Owner authorization**, cc drafts the 5a `npm publish` steps for the Owner to
execute (cc never touches the publish credentials).
