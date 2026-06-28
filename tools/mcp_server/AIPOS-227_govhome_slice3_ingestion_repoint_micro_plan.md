---
task_id: AIPOS-227
title: Governance Home Slice 3 — 196a ingestion repoint (truth root + home guard)
status: draft
authority: NONE
parent: AIPOS-223
slice: 3 of 6
depends_on: [AIPOS-224 (Slice 0), AIPOS-226 (Slice 2)]
created_by: cc
created_at: '2026-06-28'
phase: DRAFT (R direction-audit folded → Owner approve → implement → cc glm → finalize)
r_reinforcements_folded: [R-1 home_root=None narrow, R-2 single-caller invariant, R-3 approved-scratch not harmed, R-4 absence assertion]
---

# AIPOS-227 — Governance Home Slice 3: 196a ingestion repoint (DRAFT)

> **DRAFT / authority: NONE.** No product code, no commit. R direction-audit is **folded in**
> (4 reinforcements, marked ★ R-1..R-4 inline) — this goes straight to the Owner for approval,
> not back to R. Numbers/line refs are read against current `main` (post-AIPOS-226 finalize,
> product `ee3469e`).

## 0. Thesis (what this slice is, and is not)

Slice 3 finishes the "project ↔ code-repo separation" for the **196a ingestion boundary**:
the destination of gate-side scratch ingestion must be the **project truth root** (in the
survivable home), never the code repo. This is **wiring + one guard extension + positive-truth
tests** — NOT a new resolver (the resolver shipped in Slice 0, was wired for board/governance
reads in Slice 2).

**Already true after Slice 2 (to be asserted, not re-built):** ingestion repo_root flows
`queue_return(repo_root)` → `_resolve_repo_root` → `find_repo_root` → `resolve_workspace_root`
(home model) → **project truth root**, then into `plan_scratch_ingestion(repo_root=…)`
(`board_adapter.py:1773`) and `perform_scratch_ingestion(resolved_root, …)`
(`board_adapter.py:2006`). So `<project_truth_root>/workspace_artifacts/<task>/<return>/` already
lands in the home. Slice 3 makes this **explicit, guarded, and proven**, and closes the one gap
the two-root model opens (the home root is not yet a refused scratch source).

## 1. repo_root redirection (truth root, not code repo)

- **Change**: none to the data flow — `_resolve_repo_root` already yields the project truth root
  post-Slice 2. Slice 3 adds an **assertion-level test** (not new branching) that, under the home
  model, the `repo_root` reaching `plan_scratch_ingestion` / `perform_scratch_ingestion` is the
  resolved **project truth root** (`<home>/<project>`), and that the ingestion destination is
  `<project_truth_root>/workspace_artifacts/...` — i.e. inside the home, surviving code-repo loss.
- **Eliminate the implicit "code-repo == truth-root"**: audit `board_adapter` queue_return path
  for any second path that reconstructs a root from cwd / code-repo markers and bypasses
  `_resolve_repo_root`. If found → route through `_resolve_repo_root`. (Expected: none; this is a
  confirm-or-fix line, documented in the report either way.)
- **★ "single caller resolves the root" is an explicit invariant (R-2):** `_build_return_preview`
  / `plan_scratch_ingestion` deliberately **do not resolve** — they trust the caller-passed,
  already-resolved root. Today's sole caller (`return_task` → `_resolve_repo_root`, line 1969)
  resolves correctly (verified), but a trusted-input contract is a D1-class risk if a *second*
  caller ever feeds a raw/unresolved root. Slice 3 records this as a written invariant (a comment
  at both functions + a boundary assertion that the passed root is an existing directory
  containing the project marker) so a future second caller fails fast instead of ingesting into a
  wrong root. The §6 misresolution test exercises the **plan** path, not only `perform`.

## 2. `_TRUTH_PREFIXES` — extend only, never relax (the net-new guard)

Current (`artifact_ingest.py:25`): `_TRUTH_PREFIXES = ("5_tasks", ".lybra")`, evaluated
relative to `repo_root` in `_within_truth` (line 67). In the two-root model:

- `5_tasks` under `repo_root` (= project truth root) — **kept** (a scratch source inside the
  project's own truth must stay refused).
- `.lybra` under `repo_root` — now **vestigial** (runtime `.lybra/` lives at `~/.lybra/`, never
  inside the home). **Kept anyway** (defense-in-depth; "extend, never relax").
- **NEW — the home root itself.** A scratch source anywhere under `<home>` (any project's truth,
  or the home root directly) must be refused, so ingestion can never confuse one project's truth
  for another's scratch. This is the "one prefix-set extension to cover the home" of AIPOS-223
  §2.3.

**Proposed wiring (for R/Owner ruling):**
- The caller passes `home_root` (from Slice 0 `resolve_home_root`, the same resolution already
  used in Slice 2) into `plan_scratch_ingestion(..., home_root=Path)`.
- `plan_scratch_ingestion` adds a guard: `if home_root is not None and _is_within(home_root,
  scratch_root): blocking.append(ARTIFACT_INGEST_BLOCKED: scratch_dir resolves into the Lybra
  truth home)`. Placed alongside the existing `_within_truth` / `workspace_artifacts` checks
  (after line 126), **before** any hashing.
- `home_root` is **optional** (default `None`) so the function stays pure and the existing
  call-shape/tests are byte-stable; the board caller always supplies it. (Alternative —
  derive `home = repo_root.parent` — is **rejected** as a topology assumption; explicit
  `home_root` from the resolver is the fail-closed choice. **R-backed.**)
- **★ `home_root=None` semantics are narrow (R-1):** `None` may mean **only** legacy-v1 (no home
  model in play). It must **never** be reachable as "home model active but `resolve_home_root`
  returned `None`". The board caller, when on the home model, resolves the home **eagerly** and
  on failure **raises loudly** (`HOME_NOT_RESOLVED`) *before* ingestion — it must not pass
  `home_root=None` and thereby silently skip the new `<home>` guard (that would be a D2-class
  silent-skip). See §5.

**New prefixes listed + why safe:** the only added refusal surface is `<home>` (a superset that
includes other projects' `5_tasks`). It **only widens what is refused**, never what is accepted —
no currently-legal scratch root (which must already be under `LYBRA_APPROVED_SCRATCH_ROOT`,
outside truth) becomes illegal unless the operator pointed the approved root *inside the home*,
which is exactly the confused-deputy case we must refuse.

**★ approved-scratch not harmed (R-3):** the `<home>` refusal must **not** reject a legitimate
`LYBRA_APPROVED_SCRATCH_ROOT` that lives **outside** the home. In the two-root model the approved
scratch root belongs anywhere outside both `~/.lybra/` (runtime) and `<home>` (truth) — e.g. a
per-engagement work dir under the code repo or `/tmp`. Documented as such, plus a **positive
control test**: approved-scratch outside `<home>` → ingestion **green**; only a scratch path that
resolves **inside** `<home>` → **`ARTIFACT_INGEST_BLOCKED`**. (Both directions tested, so the new
guard is proven to refuse exactly the confused-deputy case and nothing legal.)

## 3. artifact_refs stay project-truth-relative (no code-repo absolute leak)

- **Already true**: `dest_rel = dest_abs.relative_to(repo_root).as_posix()` (`:160`); persisted
  `artifact_refs` are `workspace_artifacts/<task>/<return>/<name>` relative to the project truth
  root (`board_adapter.py:1782-1788`).
- **Slice 3 adds a positive assertion test**: after an ingestion under the home, every persisted
  `artifact_ref` (and every `workspace_rel` / `performed.path`) is **relative**, contains **no**
  absolute path and **no** code-repo path component, and re-resolves to a real file under
  `<project_truth_root>/workspace_artifacts/`. Negative control: an absolute or code-repo-rooted
  ref must never appear.

## 4. project.json is the sole code_repo authority (M2 / ruling 6)

- Ingestion references the code repo **nowhere** (source is the out-of-band
  `LYBRA_APPROVED_SCRATCH_ROOT`; destination is the truth root). Slice 3 **introduces no
  code-repo path** and **adds no `projects{}` table** to any config.
- Guard test: the ingestion path reads neither a code-repo location nor a home-config project
  table; any future project→code_repo mapping is read-only from `project.json` (out of scope
  here, asserted by absence). **(R-4: confirmed — keep as an absence assertion, no design
  change.)**

## 5. Fail-closed (inherit the D1/D2 iron rule)

- Root resolution failure (no established project / ambiguous / no home) → the existing loud
  errors from Slice 0/2 (`PROJECT_NOT_ESTABLISHED`, `PROJECT_AMBIGUOUS`, `HOME_NOT_RESOLVED`,
  `GOVERNANCE_NOT_FOUND`) propagate; **no silent fallback to cwd / code-repo / "."**.
- Ingestion-specific: missing `LYBRA_APPROVED_SCRATCH_ROOT`, scratch outside the approved root,
  scratch inside truth/home/workspace_artifacts, symlink/`..` escape, oversize, duplicate, or
  pre-existing destination → already fail closed (`ARTIFACT_INGEST_BLOCKED`, R-A/R-C). Slice 3
  keeps every one and adds the home-source refusal.
- **No silent default may stand in for a resolved root** (the AIPOS-226 lesson): a test asserts
  that an unestablished/misresolved root raises loudly rather than ingesting into a wrong/empty
  location — covering the **plan** path (`_build_return_preview` / `plan_scratch_ingestion`), not
  only `perform`.
- **★ home model + home unresolvable → raise, never skip the guard (R-1):** when the home model is
  active and `resolve_home_root` cannot resolve, the caller raises loudly (`HOME_NOT_RESOLVED`)
  **before** ingestion. It must **not** degrade to `home_root=None` and silently bypass the new
  `<home>` refusal (a D2-class silent-skip). Test: home model + unresolvable home → loud error;
  the `<home>` guard is never silently absent on the home path.

## 6. Verify = positive truth (inherit the B lesson)

Tests must assert **content / identity / counts**, never a proxy a default could fake:

- **Identity**: the resolved ingestion root **equals** the project truth root
  `<home>/<project>` (path equality), and the destination **equals**
  `<project_truth_root>/workspace_artifacts/<task>/<return>/<name>` (not merely "ends with
  workspace_artifacts").
- **Content**: after `perform_scratch_ingestion`, the copied file **exists at the home path**,
  its bytes **sha256-match** the scratch source, and the persisted `content_sha256` equals the
  recomputed hash. Count: `len(performed) == len(planned ingestions) == number of scratch refs`.
- **Home survives code-repo loss** (the thesis, proven): an artifact ingested with the home root
  distinct from the code repo lands under the **home**, and is readable with the code-repo path
  absent/irrelevant.
- **v1 byte-identical regression-lock**: with no home model in play (legacy `repo_root` ==
  workspace), ingestion plan/perform output (paths, digest, blocking) is **byte-unchanged** vs
  pre-Slice-3 — the existing 196a tests pass untouched, and `home_root=None` reproduces today's
  exact behavior.
- **Symlink/escape suite green**: all existing R-A/R-C tests (symlink, `..`, confused-deputy,
  oversize, duplicate, overwrite) stay green under the home model.

## Out of scope (explicit — later slices)

- Token **project dimension** mint/echo (Slice 4) and **enforcement** / `PROJECT_SCOPE_DENIED`
  (Slice 5).
- `board_adapter.py:~2132` audit-dispatch `"lybra"` literal — **scheduled into Slice 5**.
- TUI `/project switch` + per-project copilot session (Slice 6).
- `decision_log` directory-ization (R5) — a **separate slice after 3–6 / or v1.1**; NOT here
  (single-file decision_log stays the stable audit witness across 3–6; no governance-read
  structure change stacked onto a post-incident slice).

## Red lines (carried, must not regress)

- **Two-root separation unchanged**: config/token at `~/.lybra/`; no `.lybra/` and no token
  inside the home. `connection.json` untouched.
- **★A1 / canonical-fail-closed / executor≠auditor / copilot scopes `[]` / zero-dep gate core /
  gate-not-engine** — all unchanged. `confined_worker` / L2 Wall **byte-unchanged** (Slice 3
  touches only the gate-side ingest caller + `artifact_ingest.py` guard, never the worker).
- **DRAFT phase**: no product code, no commit; evidence sites
  (191b/rerun/formb/copilot/release-gate) zero contact; cc holds no owner token, does not
  confirm.
- Open follow-up (Owner-waived, tracked): rotate `LYBRA_PLANCHAT_LLM_KEY`.

## Expected blast radius (for the eventual implementation, not this DRAFT)

- `tools/aipos_cli/artifact_ingest.py`: optional `home_root` param + one home-source guard
  (extend-only).
- `tools/aipos_cli/board_adapter.py`: pass `home_root=resolve_home_root(...)` into
  `plan_scratch_ingestion` at the queue_return site (+ confirm `perform` root provenance).
- Tests: new positive-truth + home-guard + v1 regression-lock tests; existing 196a suite
  untouched.
- stdlib only; no new dependency; no new operation class.

## Verify commands (planned, for the implement phase)

```
# full gate, three lanes + acceptance (as Slice 2)
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD python3                      -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD /tmp/lybra-216-venv/bin/python -m unittest discover -s tools/lybra_tui/tests -p "test_*.py"
PYTHONPATH=$PWD python3 -m tools.acceptance.v1_acceptance        # ACCEPTANCE: PASS
```
