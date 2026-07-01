---
id: AIPOS-235
title: release-gate v2 — full-product run plan (extends/supersedes AIPOS-220)
status: draft
authority: NONE
kind: governed live run plan (NOT a code slice) — no product code, no commit, no live run until Owner approves
phase: RUN PLAN DRAFT (R direction-audit folded; pre Owner approval)
parent: v1.0 release readiness — supersedes AIPOS-220 (re-baselined post-R2/enforcement/2-role//agents)
symbols_read_from: main @ c282aa4 (post-AIPOS-234)
r_reinforcements_folded:
  - "R-1 add ingestion-repoint (227) + home-git-init (226) surfaces"
  - "R-2 tarball-installed product, NOT the dev tree (inherits AIPOS-220 R0)"
  - "R-3 §Surface-coverage ledger — enumerate every surface→verification (provability)"
---

# AIPOS-235 — release-gate v2 full-product RUN PLAN

> **Nature.** RUN PLAN DRAFT. authority: NONE. Writes no product code, commits nothing, runs no
> live gate. Output is this file → R direction-audit → Owner approves → THEN segmented execution
> (cc runs machine-verifiable segments; Owner runs O3 / macOS / all OOB gates) → finding-triage
> loop → on all-green + findings cleared, **separate Owner authorization** for `npm publish`.

---

## §0 Thesis

AIPOS-220 was written **before** R2 / per-project enforcement / 2-role / `/agents` shipped. This
plan **extends the AIPOS-220 matrix to cover the current full product**. Verification matrix =
bare-python correctness + Owner O3 + macOS Track-2 + a **clean `/tmp`** + the **#2 flake real
measurement**, with an explicit **finding-triage loop** (especially O3 interactive polish). **It is
a run plan, not a code slice** — findings are registered + triaged, never hard-fixed in-gate; manual
finalize.

---

## §1 Grounding — AIPOS-220 coverage vs the NEW surfaces this plan adds

**Already covered by AIPOS-220 (carried forward unchanged):**
- bare-python correctness A/B/C with PyYAML absent (the WS5/WS6 oracle).
- R0–R6 install/serve/card/confirm/claim/return; O3 TUI real launch (`build_app` fix + banner +
  chat-first).
- macOS Track-2 runbook; the bare-python **condition matrix** (a fail-closed BLOCK, e.g.
  `INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`, is CORRECT — a *false PASS* would be the failure).
- Disclosure / README platform matrix (Linux+macOS verified / Windows unverified / Form A Linux-only).

**NEW surfaces that must now be verified (post-220):**
- **R2 + dual-root:** `~/.lybra` holds `config.json` v2 (`config_version`/`home_root`/`active_project`)
  + `local/connection.json` (0600) — **no secret in the truth tree**; `LYBRA_HOME_ROOT` holds ONLY
  project truth (`<project>/{governance,5_tasks,stage_archive,workspace_artifacts,project.json}`).
- **topology-C** (home inside an existing repo, e.g. `~/ai-project-os/2_projects`).
- **resolution-order fallback** incl. the global `~/.lybra/config.json active_project` (the AIPOS-230
  §1a sequential fallback).
- **per-project enforcement:** `PROJECT_SCOPE_DENIED`, **18 gated / 0 exempt** (incl. the 4 read
  tools), **★A1 byte-unchanged**.
- **token `--project` mint:** `connection.json` 0600 / fingerprint-only / `projects_enforced: true`.
- **2-role workflow** + complexity suggestion (AIPOS-232).
- **/agents monitoring** (AIPOS-234) — read-only, gate-not-engine.

---

## §2 New verification segments (added to the 220 matrix; bare-python where zero-dep applies)

> **★ Tarball discipline (R-2).** Every segment below runs against the **npm-tarball-installed
> product** (inheriting AIPOS-220 R0: `npm pack` → `npm i -g --prefix <disposable>`), **NOT the dev
> work tree** — the gate verifies the *shipped* artifact. Each ledger row (§2a) carries this same
> prerequisite.

Each surface gets verification steps + a condition-matrix verdict rule. **Tag each step
machine-verifiable (cc runs) vs Owner-OOB.**

- **Dual-root + R2** *(cc, bare-python)*: assert `LYBRA_HOME_ROOT` subtree contains only project
  truth; `~/.lybra` holds config + token and **no secret lands in the truth tree** (grep the truth
  subtree for token/secret fingerprints → none); topology-C resolves correctly; misresolution is
  **fail-closed** (`PROJECT_AMBIGUOUS` / loud `GOVERNANCE_NOT_FOUND`, never a silent default).
- **Resolution-order fallback** *(cc, bare-python)*: explicit → `LYBRA_ACTIVE_PROJECT` →
  in-workspace config → **global `~/.lybra/config.json`** → single-project → fail-closed; assert the
  global step is reachable (the AIPOS-230 2-project round-trip crux).
- **Enforcement** *(cc, bare-python)*: enumerate the 18 `TOOL_HANDLERS` → all project-gated, **0
  exempt**; a cross-project token → `PROJECT_SCOPE_DENIED` (18/18 on mismatch); **absent `projects`
  → byte-identical back-compat**; **★A1 byte-unchanged** (`_capability_has_scope` untouched; order
  project-gate → ★A1 → controlled-execute). Verdict rule: `PROJECT_SCOPE_DENIED` is a **denial, not
  a new scope** (no `*_SCOPE` constant).
- **Token `--project` mint** *(cc + Owner-OOB for owner token)*: `serve rotate --project` mints; echo
  carries `projects` / `projects_enforced: true`; `connection.json` 0600; secrets fingerprint-only.
- **2-role workflow** *(cc, bare-python where possible)*: `executor ≠ auditor` enforced at BOTH the
  instance level (`board_adapter.py:2143/2150`, any class) and the role level
  (`task_complexity.py:83-92`, complex-class); the complexity suggestion **recommends, never
  auto-selects**; dogfood three-state (same-instance → `INDEPENDENCE_FAILED`; registry-unverifiable
  → `INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY` fail-closed; distinct → proceeds). *(The fail-closed legs
  are CORRECT on bare python; show the passing leg with PyYAML present.)*
- **/agents monitoring** *(cc + Owner O3)*: read-only one-shot snapshot; **gate-not-engine** (no
  timer/poll/daemon — grep-witness); faithful partition (each task once, divergence shown, explicit
  unassigned); the "as recorded — not live" label rendered.
- **196a ingestion repoint (AIPOS-227)** *(cc, bare-python)* [R-1]: a published artifact lands under
  the **project truth root**; a scratch source resolving anywhere inside `<home>` is **refused**
  (`ARTIFACT_INGEST_BLOCKED`, `artifact_ingest.py:26`); `artifact_refs` are **project-truth-relative
  with NO code-repo absolute path leak** — assert by positive identity equality (refs point into the
  project truth) **plus a negative control** (a `<home>`/code-repo-absolute source is blocked).
- **`lybra home git-init` (AIPOS-226)** *(cc + Owner-OOB)* [R-1]: topology-aware **refusal when
  already inside a repo** (`ALREADY_IN_GIT_REPO`) and when the target is itself a repo
  (`HOME_ALREADY_GIT`); **no remote / no push / local one-shot / Owner-invoked only** (the copilot,
  scopes `[]`, can never invoke it). Run on a **clean `/tmp`** (the AIPOS-233 diagnostic guards
  against stray-`.git` pollution).

---

## §2a ★ Surface-coverage ledger (R-3) — completeness is PROVABLE, not remembered

The release-gate analogue of the enforcement 18/0 enumeration: every product surface maps to a
verification step, so "no surface went unverified" is shown by the ledger, not trusted to memory.
**All rows run against the npm-tarball product (§2, R-2).** Condition-matrix note: a fail-closed
BLOCK on bare python (marked ‡) is the CORRECT outcome — a false PASS would be the failure.

| # | Surface | Verification step | Who | Verdict rule |
|---|---|---|---|---|
| 1 | **legacy v1 workspace path (M1)** | a pre-R2 single-repo workspace still resolves + claim/return/L3 unchanged | cc / bare | byte-compat: behaves identically; any drift = FAIL |
| 2 | **dual-root + R2** | `LYBRA_HOME_ROOT` = project truth only; `~/.lybra` = config+token; grep truth tree → **no secret**; topology-C resolves | cc / bare | no secret in truth tree; misresolution fail-closed (no silent default) |
| 3 | **topology-C (home in existing repo)** | home under `~/ai-project-os/2_projects` resolves; `git-init` refuses (row 8) | cc + Owner-OOB | correct resolution; nest refused |
| 4 | **resolution-order fallback** | explicit → `LYBRA_ACTIVE_PROJECT` → in-workspace → **global `~/.lybra/config.json`** → single → fail-closed; 2-project round-trip | cc / bare | global step reachable; ambiguity → `PROJECT_AMBIGUOUS` ‡ |
| 5 | **196a ingestion repoint (227)** | artifact lands under project truth; `<home>` scratch source → `ARTIFACT_INGEST_BLOCKED`; refs project-relative, no abs leak (+neg control) | cc / bare | positive identity + negative block both hold |
| 6 | **`lybra home git-init` (226)** | refuse-in-repo `ALREADY_IN_GIT_REPO` / `HOME_ALREADY_GIT`; no remote/push; one-shot; Owner-only | cc + Owner-OOB | refusals fire; copilot cannot invoke |
| 7 | **per-project enforcement** | enumerate 18 `TOOL_HANDLERS` all gated / 0 exempt; cross-project token → `PROJECT_SCOPE_DENIED` (18/18); absent `projects` → byte-identical | cc / bare | 18/0; denial not a new scope ‡ on mismatch |
| 8 | **★A1 byte-unchanged** | `_capability_has_scope` untouched; order project-gate → ★A1 → controlled-execute; executor/copilot cannot `owner_confirm`/`draft_publish` | cc / bare | byte-identical; any change = FAIL |
| 9 | **token `--project` mint** | `serve rotate --project`; echo `projects`/`projects_enforced:true`; `connection.json` 0600; fingerprint-only | cc + Owner-OOB (owner token) | 0600 + fingerprint-only; enforced flag present |
| 10 | **2-role: executor ≠ auditor** | instance level (`board_adapter:2143/2150`, any class) + role level (`task_complexity:83-92`, complex); dogfood three-state | cc / bare | same-instance → `INDEPENDENCE_FAILED`; unverifiable → `…NO_REGISTRY` ‡; distinct → proceeds |
| 11 | **complexity suggestion** | complex→2-role / else→1-role; `auto_selected` False; no card mutation | cc / bare | recommends, never auto-selects |
| 12 | **/agents monitoring (234)** | read-only one-shot; gate-not-engine (no timer/poll grep); faithful partition; not-live label | cc + Owner O3 | projection == queue truth; no runtime primitive |
| 13 | **copilot scopes `[]` / read-only** | copilot credential → `*_confirm`/`draft_publish` → `SCOPE_DENIED`; zero-write | cc / bare | structural read-only; any write = FAIL |
| 14 | **confined_worker (Form A Wall)** | container/uid isolation unchanged | cc / Linux-only | byte-unchanged; **Linux-only** (not in macOS track) |
| 15 | **bare-python correctness A/B/C** | record round-trip / manifest parse / `lybra init` with **PyYAML absent** | cc / bare | A/B/C pass with `import yaml` failing |
| 16 | **TUI real launch (O3)** | `build_app` no crash + banner + chat-first + NL→card + new surfaces | Owner O3 | launches; findings → §3 loop |
| 17 | **macOS Track-2** | rows 2/4/6/10/12 + R0–R6/O3 on macOS | Owner / macOS | evidence returned; Form A excluded |
| 18 | **disclosure / README matrix** | platform matrix + new-feature surfaces; AIPOS-213 guard | cc | claims ⊆ disclosure; guard green |

**Ledger rule:** no row may be silently skipped; a surface that cannot be run is recorded as an
explicit **deferral with Owner sign-off**, never an implicit gap.

### §2a.1 R-2 gate step (standing) — "shipped == tested" three-part proof [F-rg2-1 CLOSED]

The tarball ships product code + the acceptance module but **not** the unit test suite
(`tools/**/tests/`). **Owner verdict — F-rg2-1 CLOSED (as-designed):** do NOT pack the unit suite
(non-standard + bloat); byte-identity to the green-tested dev source is the stronger guarantee. Every
release re-runs this three-part R-2 proof (also runbook **N0**):
1. **byte-identity** — `diff -r --exclude=tests --exclude=__pycache__ <repo>/tools "$PKG/tools"` is
   **empty**, and the shipped product `.py` count is **pinned at 53** (any delta = investigate).
   *(Linux WSL2: verified empty over 53 .py.)*
2. **self-contained probe on the install** — `check_isolation_textual_absent()` on the installed
   product = `True` (imports + runs with ALL third-party blocked; correctness A/B/C pass).
3. **acceptance-on-installed** — the self-contained checks PASS from the install; the test-driven
   anchors + full-suite report `NO TESTS RAN`, which is **EXPECTED** (tests not shipped), not a
   failure.

This is the release-gate's canonical answer to "is the *shipped* artifact correct" — it replaces the
(impossible) "run the unit suite from the install".

---

## §3 ★ Owner O3 = real hands-on verification, EXPECTED to produce findings (the standing lesson)

- **O3 is not a rubber stamp.** Last round it surfaced the `build_app` crash + CJK + chat-first UX →
  became AIPOS-221/222. This round the Owner runs the full **new** surface in a real terminal with
  CJK: `/project switch`, `/agents`, the 2-role flow, copilot chat.
- **Explicit finding-triage loop:** O3 finding → register **F-***→ triage (a UX-polish slice **vs**
  Owner-signed deferral) → fix → re-verify → only then is that part **publish-ready**.
- **The plan budgets ≥ 1 polish round — it does NOT assume a first-pass clean.** *(This is the formal
  home for "as I run the TUI there will be interactive polish.")*

---

## §4 macOS Track-2 (extend the existing runbook to the new surfaces)

Extend `docs/v1_release_macos_runbook.md` to the new surfaces (macOS: R2 home, `/project switch`,
`/agents`, 2-role flow). Owner runs on a Mac and returns evidence (fingerprint-only). **Form A Wall
(confined_worker) stays Linux-only** — not in the macOS track.

---

## §5 Clean `/tmp` + #2 flake real measurement

- **Run the gate on a clean `/tmp`** (no stray ancestor `.git`; the AIPOS-233 diagnostic will
  announce pollution loudly — "stray ancestor .git at … NOT a home_git regression" — so a dirty
  `/tmp` cannot be mistaken for a product failure).
- **#2 WSL2 transport flake — this is the real measurement point** (per AIPOS-231's "deferred to the
  release-gate"): run the transport suite **multi-round / multi-environment** and record the flake
  rate. **If it reproduces, this is the reproducible environment → pin the exact root cause on the
  spot** (the accept-race was already refuted; characterize, don't assume). If it does not reproduce
  across the budgeted rounds, record that honestly and keep #2's disposition explicit.

---

## §6 Verdict & evidence

- Per-surface PASS / finding with immediate on-disk re-check.
- bare-python correctness A/B/C with PyYAML absent; fail-closed BLOCKs recorded as **correct**, with
  the PyYAML-present passing leg shown separately.
- secrets fingerprint-only; `connection.json` 0600; evidence sites zero-contact.
- **cc never holds the owner token** — R4–R6 + publish are all Owner-OOB.
- Cross-evidence: `ACCEPTANCE: PASS` (the all-third-party-absent + correctness probe) on a clean
  `/tmp`.

---

## §7 Not done / red lines / disclosure

- **Actual `npm publish` = separate, irreversible, Owner-authorized** — only after all-green **and**
  every O3 finding cleared (or Owner-signed deferred). cc drafts the steps; **cc never touches
  publish credentials.**
- **Disclosure / README** updated for the platform matrix **and the new feature surfaces**
  (claims ⊆ disclosure: claim only what was verified — Linux+macOS verified / Windows unverified /
  Form A Linux-only; the AIPOS-213 README↔disclosure guard stays green).
- **Red lines:** **no product code change** (this is a plan); dual-root / ★A1 / zero-dep /
  gate-not-engine / `confined_worker` byte-unchanged. DRAFT writes no code and commits nothing;
  evidence zero-touch.

---

## Finding triage (no hard-fix in-gate)

- Correctness / install / enforcement / 2-role finding → register **F-rg2-*** + a separate fix slice;
  do not patch in-gate.
- O3 UX finding → register **F-*** → polish slice vs Owner-signed deferral (§3 loop).
- Platform (macOS) finding → register + triage. Environment (proxy/key/`/tmp` pollution) → record as
  an environment note, not a product finding.

---

## Exit

Run all segments (cc machine-verifiable + Owner O3/macOS/OOB) → cc produces the execution report
(+ the extended macOS runbook) → Owner 复核 → PASS / findings → finding-triage loop until all-green
or Owner-signed deferral → **on separate Owner authorization**, cc drafts the `npm publish` steps for
the Owner to execute.

**Flow:** DRAFT **(R direction-audit folded — R-1…R-3 in)** → **Owner approves** (no second R pass)
→ segmented execution (cc runs the machine-verifiable ledger rows; Owner runs O3 / macOS / all OOB
gates) → finding-triage loop (≥1 polish round budgeted) → all-green + every finding cleared (or
Owner-signed deferral) → **separate Owner authorization** → cc drafts the `npm publish` steps for the
Owner to execute (cc never touches publish credentials).
