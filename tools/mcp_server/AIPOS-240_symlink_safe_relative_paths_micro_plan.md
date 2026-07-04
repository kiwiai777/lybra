# AIPOS-240 (Slice α / F-o3-19) — symlink-safe `relative_to(repo_root)` (macOS release BLOCKER)

- **status: draft** — R direction-audit **PASS** folded (rulings ①–⑤, below); awaiting Owner approval.
- **authority: NONE** — no product code, no commit until Owner approves.
- **R rulings folded:** ① Option 2 + per-function local `root = repo_root.resolve()`, no cross-module
  helper (§3). ② scope rule = only fix LHS-may-be-resolved; `agent_profiles:269` cleared as safe (§2).
  ③ message-string sites 356/358/400 fixed too (§2 FIX set). ④ tests unchanged (§4). ⑤ disclose +
  pin the symlink-escape containment tightening (§4a, §5 test case b).
- **parent:** macOS Track-2 O3 finding **F-o3-19** (highest priority; macOS release blocker).
- **severity:** substantive — **must fix before the macOS release.**
- **scope:** product path-rendering sites only. **Tests are NOT changed** (they must independently
  go green on macOS). Zero accountability / gate / ★A1 / two-root / zero-dep-core semantics change.

## §0 Symptom (Owner O3 evidence)

macOS full suite: **685 tests → 51 errors (48 `ValueError`)**, all on CLI **write** paths, e.g.
`draft_writer.py:377`:
```
ValueError: '/private/var/folders/…/5_tasks/drafts/x.md' is not in the subpath of '/var/folders/…'
```

## §1 Root cause + mechanism self-repro (reproduced on Linux via a symlink)

`Path.relative_to` is a **pure lexical** operation — it does NOT resolve symlinks. When the LHS is
already `.resolve()`d (symlink-expanded) but the RHS `repo_root` is not, they diverge on any host with
a symlinked temp/space. macOS makes this the default: `/tmp → /private/tmp`, `/var → /private/var`
are **system symlinks**, so a repo under `TMPDIR`/`/var/folders/…` (what `tempfile` returns in tests
AND what real macOS runtime dirs use) resolves to a `/private/…` prefix on one side only.

At `draft_writer.publish_draft`: `source_path = resolve_draft_path(repo_root, draft_path)` returns a
**resolved** path (`/private/var/…`); then `:377 source_path.relative_to(repo_root)` with an
**unresolved** `repo_root` (`/var/…`) → `ValueError`. Same shape at every listed site.

**Mechanism reproduced (Linux, symlink standing in for `/var→/private/var`):**
```
repo_root          : /tmp/…/symrepo            (unresolved, as passed from CLI)
resolved_src       : /tmp/…/realrepo/5_tasks/drafts/x.md   (LHS .resolve()d)
BROKEN relative_to : ValueError: '…/realrepo/5_tasks/drafts/x.md' is not in the subpath of '…/symrepo'
FIXED  relative_to : 5_tasks/drafts/x.md        (resolve BOTH sides)
```
This is the **exact** `ValueError: … is not in the subpath of …` signature from the macOS traceback,
and the both-sides-resolve fix yields the correct repo-relative string.

## §2 Site inventory — classified by LHS origin (R ruling ②: only fix LHS-may-be-resolved)

**Scope rule (R ②):** fix ONLY the sites where the LHS may already be `.resolve()`d while the
same-function `repo_root` is unresolved (those throw on macOS). Sites where the LHS derives from the
SAME unresolved `repo_root` (e.g. `expected_*_record_path(repo_root,…)` → `repo_root/…` unresolved, or
an `iterdir`/`rglob` over an unresolved root) are **lexically self-consistent** → do not throw →
**EXCLUDE** (touching them would be churn AND would apply the §7 tightening where it isn't wanted).

The RESOLVED sites all trace to one of three resolve points: `record_writer.ensure_safe_record_path`
(`→ path.resolve()`, reached via `claim_record_paths` / `session_record_path` / `return_record_path` /
`audit_dispatch_record_path` / `audit_verdict_record_path`), `resolve_draft_path` /
`resolve_pending_target_path` (draft_validator, `→ .resolve()`), and queue_mutation's own
`(repo_root / …).resolve()` (:473). None of those enclosing functions re-resolve `repo_root`.

**FIX set (17 — LHS resolved, `repo_root` unresolved → the macOS `ValueError` sites):**
- `draft_writer.py`: **377, 478** (`source_path` ← `resolve_draft_path` → resolved)
- `queue_mutation.py`: **293, 353, 354, 356, 358, 398, 400** (incl. the message-string sites 356/358/400 — same resolved vars; R ③)
- `board_adapter.py`: **1485, 1486, 1571, 1572, 2174, 2527, 2573** (all via `ensure_safe_record_path`)
- `task_loader.py`: **78** (CONDITIONAL → FIX: safe on `load_all_tasks`/`load_task_by_path`, but throws on the `resolve_queue_path`-fed path from `queue_mutation._select_task` where the LHS is resolved and in-function `repo_root` is not — two-sided resolve is byte-identical on the safe paths and fixes the throwing one)

**EXCLUDE set (11 — LHS + `repo_root` both unresolved, lexically consistent → SAFE, do not touch):**
- `draft_writer.py`: 439, 522 (`expected_publish_record_path` → `repo_root/…` unresolved)
- `queue_mutation.py`: 478, 565 (`repo_root / DIR / name` lexical)
- `records.py`: 92, 219, 389 (`iterdir` over unresolved `repo_root/…`)
- `authority_scanner.py`: 291 (`rglob` over unresolved `repo_root/5_tasks/drafts`)
- `preview.py`: 82, 85 (`expected_*_path(repo_root,…)` unresolved; `repo_root=Path(task["repo_root"])` not resolved)
- **`agent_profiles.py`: 269 (R ② clearance)** — a **read**-path render; `path` comes from
  `docs_root.glob` where `docs_root = repo_root/"0_control_plane"/"agents"` (2nd call pairs
  `product_root/…` with `product_root`). LHS shares the SAME unresolved base as the `relative_to`
  argument → **lexically self-consistent, never resolved independently → cannot throw. SAFE.**

**Already-correct reference sites (the target pattern — do NOT churn):** `draft_writer.py:281, 460`;
`queue_mutation.py:139, 513`; `draft_validator.py:98, 203`; `task_loader.py:139`; the orchestration
writers/previews; `board_adapter.py:542, 546` (`resolved_root`). Already `X.resolve().relative_to(Y.resolve())`.

> Positive-truth reconciliation (implementation): the FIX set is the **predicted** throwing set. On
> macOS, reconcile it against the **actual 48-`ValueError` list** — fix exactly the sites that throw;
> if a predicted-EXCLUDE site throws, re-classify and fix it (and note why the static trace missed it).

## §3 Fix — Option 2, per-function local resolved root (R ruling ①)

**R ①: Option 2 (resolve both sides), and it IS allowed to hoist a one-time local
`root = repo_root.resolve()` inside each function — NO cross-module helper.** Pure
path-normalization; the returned relative string is **byte-identical on Linux** (no symlink prefix →
`.resolve()` is identity) and becomes **correct on macOS**.

Per FIX-set function, resolve `repo_root` once to a local and resolve the LHS at each site:
```python
def publish_draft(repo_root: Path, ...):
    root = repo_root.resolve()                       # once, local; repo_root arg untouched elsewhere
    source_path = resolve_draft_path(repo_root, draft_path)   # already resolved
    source_rel = str(source_path.resolve().relative_to(root))  # :377  (LHS .resolve() = idempotent here)
    ...
    "path": str(source_path.resolve().relative_to(root)),      # :478
```
Same shape in `_select_task`/claim/return record functions (queue_mutation 293/353/354/356/358/398/400),
the `board_adapter` claim/return/dispatch/verdict record plans (1485/1486/1571/1572/2174/2527/2573),
and `task_loader._normalize_task` (:78). For the **message-string** sites (356/358/400) the same local
`root` + `X.resolve()` is interpolated. No new symbol, no new module API — exactly the existing
correct-reference pattern (`draft_writer:281/460`), just applied at the FIX set.

Rationale for not hoisting `repo_root` reassignment: keep the `repo_root` **parameter** unchanged
(other calls in the function pass it onward to helpers that expect the caller's form); introduce a
distinct local `root` used ONLY for the `relative_to` renders. This is minimal and side-effect-free.

## §4 Red lines (R make-or-break)

- **Product only. Tests unchanged** — macOS 685 must go green because the *product* got symlink-safe,
  not because a test was relaxed. `git diff` touches no `**/tests/**`.
- **Relative result unchanged on Linux** — every rendered `relative_to` string is byte-identical on
  Linux (prove: full Linux suite still 685 green, no snapshot/record string churn).
- **Genuinely out-of-tree paths still raise** — the contract that a path *not* under `repo_root` is a
  hard error is preserved (both-resolve still raises `ValueError` for a real escape; we only fix the
  symlink false-negative). Do not swallow it into a silent `str(path)` fallback at write sites.
- **No accountability / gate / ★A1 / two-root / zero-dep-core / gate-not-engine change.** Path
  rendering only; no confirm/scope/token/independence logic touched.

## §4a Disclosed semantic tightening (R ruling ⑤) — a hole plugged, not just a bug fixed

Two-sided `.resolve()` is not purely cosmetic at the FIX sites: it **tightens** one case. A
subdirectory inside the workspace that is itself a **symlink pointing OUT** of `repo_root` (e.g.
`<repo>/5_tasks/records → /elsewhere`) previously passed the **lexical** `relative_to(repo_root)`
(the un-expanded path string is still under `repo_root/…`), so a record could be **silently written
through the symlink to outside the truth zone**. After the fix the LHS is `.resolve()`d first, so the
escape is detected and the `relative_to` **raises loudly** — the write is refused instead of leaking
out of the accountable tree. This is a **desirable containment tightening** (fail-loud > silent
escape), scoped to the FIX (write/record) sites; the EXCLUDE (read/lexical) sites keep their current
behavior. **Disclose in `docs/v1_disclosure.md`** (one row: symlink-subdir-escape now fail-closed at
record write) and pin it with the §5 mechanism test.

## §5 Verify — positive truth (not a proxy a green default can fake)

- **macOS:** full suite **685 all green**, and specifically **51 errors → 0** with the **48
  `ValueError` sites eliminated** (assert the error count drops to 0 AND grep the run for
  `is not in the subpath of` → none). Not "tests pass" in aggregate only — name the previously-failing
  IDs and show them green.
- **Linux (WSL, v1 no-regression):** full suite still **685 green**; a targeted check that the
  rendered relative strings are unchanged (no record/preview snapshot diff).
- **ACCEPTANCE PASS** on both; BARE + SYSTEM + TUI lanes green.
- Mechanism regression test (product-side unit, cross-platform via a real symlink so it guards on
  Linux CI without a Mac): **two cases** —
  (a) **fix:** a `repo_root` reached via a symlink prefix (`/tmp/sym → /tmp/real`) with a `.resolve()`d
      LHS renders the correct repo-relative string (no `ValueError`);
  (b) **tightening (R ⑤):** an **internal** symlink subdir that points OUT of `repo_root`
      (`<repo>/sub → /elsewhere`) → the FIX-site render **raises** `ValueError` (write refused), proving
      the containment tightening is real, not incidental.

## §8 Round-2 calibration record (§6 iterate-to-green; Owner-approved WS1+WS2, F-o3-20 folded in)

**Round 1 → macOS:** 17 FIX applied → macOS went **51 err + 1 FAIL → 7 err** (mechanism tests both
green; the old FAIL vanished = also symlink collateral). Masking effect confirmed (R): the first
throw on a path hides downstream sites.

**Round 2 classification (WS1):**
- **Funnel proven empirically (Linux symlink repro):** all three Owner-cited frames —
  `draft_validator:278` (list_drafts, external_intake), the aipos-29-collide traceback, and
  `planner_loop_mvp:137` — raise at the SINGLE site **`draft_validator.py:254`**
  (frames reproduced exactly: `planner_loop_mvp:137 → :33 → draft_validator:278 → :254`). Its shape
  is the guard/render split: `_resolved_within` both-resolves and passes, then the render calls
  one-sided `path.relative_to(repo_root)` with `path` resolved via `resolve_draft_path`.
- **Full re-scan:** 58 non-test `.relative_to(` sites in `tools/`. Round-1's 28-site inventory was
  derived from the finding's file list — `draft_validator`, `planner_loop_mvp`, `artifact_ingest`,
  `workspace_templates`, `state_recovery`, `confined_worker`, `record_writer`, orchestration
  writers/previews, `ai_assisted_authoring`, `v1_acceptance` were never classified (breadth-audit
  miss, owned).
- **Round-2 FIX (+2):** `draft_validator:254` (the funnel; inline both-resolve, guard semantics
  unchanged) · `artifact_ingest:178` (SAME guard/render shape: `dest_abs` resolved at :174, guard
  :175 both-resolved passes, render vs unresolved `repo_root` → **latent** thrower — not among the 7
  only because the reaching tests pass resolved roots; production CLI form can be unresolved).
- **Amended inventory: 58 = 19 FIX (17 r1 + 2 r2) + 39 EXCLUDE, zero unclassified.** EXCLUDE breakdown:
  - *Already both-resolved (correct pattern, 18):* draft_validator 98/203 · draft_writer 281/464 ·
    queue_mutation 139/514 · task_loader 139 · record_writer 54 · orchestration_event_writer 193 ·
    orchestration_summary_preview 139 · orchestration_timeline_preview 99 · planner_iteration_writer
    257 · ai_assisted_authoring 110 · workspace_templates 257 (output_root resolved at :234) ·
    board_adapter 542/546 (resolved_root) · confined_worker 127 (all four callers pass both sides
    resolved, :359-380) · artifact_ingest 61 (all callers both-resolved: :69/135/163/175).
  - *Lexically self-consistent — LHS derives from the same unresolved root (21):* v1_acceptance 170
    (rglob over REPO_ROOT) · agent_profiles 269 · draft_validator 163/255/294 (iteration-derived) ·
    draft_writer 443/526 · queue_mutation 479/566 · records 92/219/389 · authority_scanner 291 ·
    preview 82/85 · state_recovery 51 (handled-except status probe; resolving would change read-side
    semantics — out of scope) · workspace_templates 80/93/248/250 · confined_worker 279.

**WS2 (F-o3-20, repo hygiene):** ironclad proof `git check-ignore -v` → `.gitignore:7 task_cards/`
(unanchored) swallowed `tools/aipos_cli/tests/fixtures/task_cards/` → the two payload JSONs were
never tracked → every fresh clone fails 4 tests (platform-independent; dev trees false-green via
local untracked files). Fixed: anchored to **`/task_cards/`** (root audit-card dir still ignored,
proven); both fixture JSONs tracked (in the finalize pathspec). All other unanchored dir patterns
audited: `__pycache__/`, `node_modules/`, `playwright-report/`, `test-results/` intentionally global
(build artifacts); `.vscode/`/`.idea/`/`.codex/` no nested instances in-tree; `.lybra/local/`
contains an inner slash → anchored by gitignore semantics. **No other mis-swallow.**

**Round-2 verify:** Linux symlink repro of all 3 frames → fixed, rendered strings byte-identical to
the no-symlink baseline, collide semantics intact (Duplicate blocked). Dev tree BARE 687 / SYSTEM
687 / TUI 105 / ACCEPTANCE PASS. **Fresh-clone semantics** (git checkout-index of the staged
pathspec): BARE **687 OK** + **ACCEPTANCE PASS** (the 4 fixture tests pass from the exported tree).
macOS re-run (patch channel, fresh-clone-equivalent) expected **7 → 0**; if a round-3 site emerges →
continue §6 iterate-to-green and extend this record.

## §6 Remaining verification (post-approval)

R direction-audit is folded (header). The one open item is empirical, done at implementation:
- **Reconcile the FIX set (17) against the macOS 48-`ValueError` list** (positive truth): fix exactly
  the throwing sites; if a predicted-EXCLUDE throws, re-classify + fix + note why the static trace
  missed it; if a predicted-FIX does NOT throw on macOS, confirm it's on a genuinely-safe path and
  leave the two-sided resolve (byte-identical) or drop it — record the decision.
- Confirm on macOS: full suite **685 green**, error count **51 → 0**, grep `is not in the subpath of`
  → none; Linux still **685 green** with no rendered-string churn; then cc glm audit → R re-check →
  finalize → Owner macOS re-run.
