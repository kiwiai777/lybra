---
id: AIPOS-233
title: home_git test hermeticity — eliminate the ancestor-.git false failures (TEST-ONLY)
status: draft
authority: NONE
task_class: simple
phase: micro-plan DRAFT (R direction-audit folded; pre Owner approval)
parent: carried-forward item #4 (surfaced at AIPOS-232 finalize, DL-20260629-01)
symbols_read_from: main @ 3f8ae12 (post-AIPOS-232)
r_reinforcements_folded:
  - "R-a drop-unreachable-green (no 'induced /tmp/.git stays green' criterion)"
  - "R-b diagnostic-setUp (name the stray .git) + git-config hermetic"
  - "R-c honest verify criteria (clean→green / polluted→one clear diagnostic)"
  - "R-d external pollution, not Lybra leak; release-gate fix = clean /tmp + diagnostic"
---

# AIPOS-233 — home_git test hermeticity (TEST-ONLY)

> **Nature.** DRAFT only. authority: NONE. Writes no product code, commits nothing.
> Output is this file → R direction-audit → Owner approves → implement + tests → auto-write
> cc-glm audit card → `cc glm` → Owner finalize (**closes carried-forward item #4**;
> acceptance returns to a clean full-green). All symbols read from `main` @ `3f8ae12`.

---

## §0 Thesis

The `home_git` tests are **non-hermetic**: the product's ancestor-`.git` detection (the correct
"am I nested inside an existing repo?" check) walks up to the filesystem root, so a **stray `.git`
in an ancestor of the system temp dir (e.g. `/tmp/.git`)** makes four "fresh / not-a-repo" tests
fail falsely in a stripped / acceptance environment. **Fix the TESTS to be hermetic; do NOT touch
product behavior.** Test-only.

---

## §1 ★ Root cause — characterized with evidence (NOT assumed)

All of the following was reproduced on `main` @ `3f8ae12`:

- **Detection mechanism (decides the hermetic technique):** `home_git.git_repo_ancestor`
  (`home_git.py:46-58`) **self-walks `Path.parents`** — `for candidate in [current, *current.parents]:
  if (candidate / ".git").exists(): return candidate` — it is **NOT** a `git rev-parse` subprocess.
  ⇒ `GIT_CEILING_DIRECTORIES` (which only bounds git-subprocess upward search) **does NOT help** the
  ancestor check; the only lever is **guaranteeing the test temp root has no ancestor `.git`**.
  (The git *subprocess* legs are only `git init` / `git add` / `git commit` in
  `execute_home_git_init` — and the commit already passes `-c user.name`/`-c user.email`, so it does
  not depend on global git config.)
- **The exact failing set (deterministic induced repro — created `/tmp/.git`, ran the suite):**
  **3 ERROR + 1 FAIL = `failures=1, errors=3`**, byte-matching the acceptance core-lane symptom:
  - ERROR `test_execute_creates_repo_one_commit_no_remote` — `execute_home_git_init` hits
    `git_repo_ancestor(home)→/tmp` → raises `ALREADY_IN_GIT_REPO` before doing its work.
  - ERROR `test_execute_project_scope_target` — same ancestor trip on the `<home>/proj` target.
  - ERROR `test_execute_twice_raises_file_exists` — raises `ALREADY_IN_GIT_REPO` instead of the
    expected `HOME_ALREADY_GIT`.
  - FAIL `test_git_repo_ancestor_detects_existing_repo` — `assertIsNone(git_repo_ancestor(self.home))`
    is violated (`/tmp` is returned).
  - **Unaffected:** `test_execute_missing_home_raises` (FileNotFoundError fires first),
    `test_execute_refuses_inside_existing_repo` (it `git init`s `self.home` first, so its own repo is
    found before `/tmp`), and the 3 `HomeGitPlanTests`.
- **Why the temp dirs are exposed:** `setUp` uses `tempfile.TemporaryDirectory()` →
  `self.home = /tmp/tmpXXXX`. The ancestor walk from there reaches `/tmp`; if `/tmp/.git` exists the
  walk returns it.
- **Leak source is NOT the test suite (grep-proven):** the ONLY `git init` calls in the whole test
  tree are in `test_home_git.py` and every one uses `cwd=str(self.home)` (a `/tmp/tmpXXXX` temp dir
  cleaned in `tearDown`). **No test ever creates `.git` at `/tmp` itself.** The `/tmp/.git` is a
  **stray external / pre-existing artifact** (e.g. left by an unrelated tool or a prior aborted run);
  the tests are simply fragile to *any* ancestor `.git` above the shared system temp dir.
- **Confirmation of the cure direction:** removing the stray `/tmp/.git` makes ACCEPTANCE pass
  (observed at AIPOS-232 finalize). **A clean `/tmp` is therefore a genuine PREMISE, not something
  the tests can engineer away** (R-a): since detection self-walks to `/`, no test trick can make a
  `/tmp`-rooted home ignore a real `/tmp/.git`. The fix's job is to make that premise **explicit and
  self-announcing** (a diagnostic that names the stray `.git`), not to mask it.

> **Implementation §1 obligation (characterize, don't assume):** before coding, re-confirm in the
> actual acceptance stripped env (`env={PYTHONPATH, PATH}`, `cwd=REPO_ROOT`) whether a `/tmp/.git`
> is present/recurring, and record the concrete ancestor path observed. If new evidence contradicts
> "external/pre-existing" (e.g. some path DOES leak it), report honestly and adjust — do not retrofit
> the conclusion.

---

## §2 Fix — diagnostic + git-config hermetic (TEST-ONLY) [R-b: technique decided]

> **R-a — the chased "still green under a stray `/tmp/.git`" goal is dropped.** It is **unreachable
> and wrong to pursue**: if `/tmp/.git` truly exists, the product *correctly* concludes "this path
> is inside a git repo", and a test home under `/tmp` walking up to it **should** fail. Making it
> "still green" would require either escaping the `/tmp` ancestry (impossible to guarantee on a
> shared `/tmp`) or relaxing the product detection (forbidden, §3). So the fix does NOT try to pass
> under pollution — it makes pollution **instantly, unambiguously diagnosable** instead of masquerading
> as a `home_git` regression.

1. **(primary) `setUp` diagnostic assertion.** After creating the per-test temp tree, assert with the
   product function itself: `assert git_repo_ancestor(temp_root) is None`, and on failure raise ONE
   loud message that **names the stray `.git` location** — e.g. `non-hermetic env: stray ancestor
   .git at <path>; remove it (the shared /tmp is polluted), this is NOT a home_git regression`. This
   converts the cryptic "3 ERROR + 1 FAIL that look like a home_git regression" into a single, plain
   "the environment's /tmp has a stray .git — clean it".
2. **(defense-in-depth) git-subprocess hermetic env.** Run `git init/add/commit` (and the test helper
   `_git`) with `HOME=<tempdir>`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null` to remove
   user/system gitconfig variance. (The commit identity already passes `-c user.name`/`-c
   user.email`, so this is hardening, not a behavior change.)
3. **State it plainly in the test + DRAFT:** `/tmp/.git` is **external environment pollution, NOT a
   Lybra leak** (grep-proven: zero `git init` anywhere outside the home_git tests; acceptance never
   git-inits). The acceptance reliability **premise is a clean `/tmp`**, now made explicit and
   self-announcing by the diagnostic rather than silently assumed.

---

## §3 ★ Red line (test-only — do NOT overreach)

- **Product `home_git.py` behavior is BYTE-UNCHANGED.** The ancestor-`.git` walk (topology-C nested-
  repo safety) is **correct product behavior** — it is what makes the Owner's dogfood inside
  `ai-project-os` safe. **Never relax the product's upward detection to dodge a test failure**; that
  would weaken a safety guarantee. We fix test hermeticity, not product logic. (Verify: `git diff`
  shows `home_git.py` untouched — grep-witnessed.)
- **No weakened assertions.** Every existing assertion stays: nested-repo detection,
  `ALREADY_IN_GIT_REPO` / `HOME_ALREADY_GIT` refusals, one-commit / no-remote, project-scope init.
  Not one is dropped or loosened.
- `git diff` is **test file / test helper only**; **stdlib-only**; no gate / product / other-lane
  change.

---

## §4 verify = positive truth [R-c: honest, reachable criteria]

- **clean env → reliable full-green + clean acceptance:** with no stray ancestor `.git`, the
  home_git family is green and `ACCEPTANCE: PASS`, with home_git no longer contributing false
  failures (the actual goal of this slice).
- **polluted env → ONE clear diagnostic, not four cryptic failures:** with a stray ancestor `.git`
  induced, the `setUp` diagnostic raises a single message **naming the stray `.git` path** and
  stating it is an environment problem, NOT a home_git regression. Assert the diagnostic message
  (positive-truth: assert the named path + "not a regression" wording), and that it replaces the
  prior 3-ERROR/1-FAIL pattern. **(R-a: there is NO "induced /tmp/.git stays green" criterion — that
  green is unreachable and is not chased.)**
- **git-config hermetic:** the git-subprocess legs run under `HOME=<tmp>`/`GIT_CONFIG_NOSYSTEM`/
  `GIT_CONFIG_GLOBAL=/dev/null`; assert behavior is independent of any user/system gitconfig.
- **no internal leak (grep, retained):** witness that the only `git init` in the tree is the
  home_git tests' own `cwd=self.home` calls — Lybra never creates `/tmp/.git`.
- **product assertions intact:** nested-repo detection / `ALREADY_IN_GIT_REPO` / `HOME_ALREADY_GIT`
  refusals / one-commit-no-remote / project-scope — none dropped.
- **zero product code:** `git diff` is test-only; grep-witness that `home_git.py` is unchanged.
- **three lanes green + multi-round stability** (BARE / SYSTEM / TUI) + ACCEPTANCE (clean env).
- **honesty:** if any sub-case remains environment-constrained, record it plainly — no false green.

---

## §4a Release-gate / environment hygiene [R-d]

The **true remedy for external pollution is environment hygiene, not product/test gymnastics**:
run the release-gate / acceptance on a **clean `/tmp`** (no stray ancestor `.git`). The `setUp`
diagnostic guarantees that if `/tmp` is dirty, it is **immediately and unambiguously identifiable**
(named path + "not a regression") instead of being mistaken for a `home_git` failure.

- **Note (why the obvious trick fails):** pointing the probe's `TMPDIR` at a freshly-created dir does
  NOT help — that new dir is still **under `/tmp`**, so `/tmp/.git` remains its ancestor and the
  self-walk still finds it. The only real levers are **(1) a clean `/tmp`** and **(2) the diagnostic**.
- This is recorded in the finalize/closeout as the standing operational guidance for the release-gate.

## §5 Out of scope

- Carried-forward item #2 (WSL2 transport flake — independent, release-gate measured).
- **Any change to product `home_git.py` behavior** (the nested-repo detection stays as-is).
- Other carried-forward items (#1 LLM key rotation, #3 decision_log directory-ization).

---

## Red lines / deliverable

- product `home_git.py` byte-unchanged (nested-repo detection NOT relaxed) · root cause evidenced
  (not assumed) · hermetic technique matched to the self-walk mechanism (not a no-op like
  `GIT_CEILING_DIRECTORIES` on a non-subprocess walk) · assertions undiminished · before/after
  quantified · zero product code · stdlib-only · gate/★A1/copilot/transport untouched.
- DRAFT writes no code and commits nothing; evidence zero-touch; cc holds no owner token and never
  confirms.

**Flow:** DRAFT **(R direction-audit folded — R-a…R-d in)** → **Owner approves DRAFT** (no second R
pass) → implement → `cc glm` executes the audit (focus: product `home_git.py` byte-unchanged / no
relaxed detection · `setUp` diagnostic names the stray `.git` · git-config hermetic · assertions
undiminished · §4 criteria honest — NO "induced stays green" · zero product code · external
pollution recorded as not-a-Lybra-leak) → R re-checks the `cc glm` verdict → Owner finalize
(**item #4 closed; acceptance clean full-green**).
