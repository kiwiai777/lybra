---
id: AIPOS-224
parent: AIPOS-223
slice: 0
title: Governance Home — Slice 0, resolution core (additive, zero behavior change)
status: draft
authority: NONE
date: 2026-06-27
red_lines:
  - controlled-execute / AIPOS-197/199/204 semantics frozen
  - "★A1 scope split (executor/copilot cannot owner_confirm / draft_publish)"
  - canonical-only fail-closed identity; executor != auditor independence
  - zero-dep (stdlib-only gate core; only lybra_tui/app.py may import textual)
  - "★ touch only governance-home resolution; ZERO contact with the three evidence sites (191b / rerun / formb / copilot)"
---

# AIPOS-224 — Slice 0: resolution core (micro-plan DRAFT, authority: NONE)

> Per AIPOS-223 §"Proposed implementation slicing" Slice 0, and the Owner's 7 rulings
> (1=B single-file decision_log; 2=(a) `/project new` only; 3=move-into-home; 4=require
> re-rotation, project-less → `PROJECT_SCOPE_DENIED`; 5=config_version 2 additive; 6=`project.json`
> in the project root is the **sole authority**, home does NOT duplicate `code_repo`;
> 7=`workspace_artifacts` belongs to the project truth root).
>
> This is a micro-plan DRAFT for ritual review. Nothing is implemented yet.

## 0. One-line contract

**Add the home-root + active-project resolver as a NEW, UNWIRED library API in
`workspace_config.py`, plus the additive `config_version: 2` reader — with the existing
`resolve_workspace_root()` / `find_repo_root()` behaving byte-identically for every v1
input.** No caller is rewired in this slice. Dogfood = new resolver unit tests + proof that
the legacy resolution and the full bare-venv suite are unchanged.

## 1. Why this is the first slice

Every later slice (governance de-hardcode, truth move, 196a repoint, token dimension, TUI
switch) needs one trustworthy answer to "given a home and an active project, what concrete
paths?". Slice 0 delivers exactly that resolver and its fail-closed errors, **without
changing any current behavior**, so it can be merged and audited in isolation.

## 2. Scope — exactly what changes

### 2.1 `tools/aipos_cli/workspace_config.py` (the only production file touched)

All additions are **additive**; no existing function's signature or behavior changes.

- **New constant**: `DEFAULT_HOME_ROOT = Path("~/.lybra/projects")`.
- **New env names** (read-only here, wired later): `LYBRA_HOME_ROOT`, `LYBRA_ACTIVE_PROJECT`.
- **New v2 readers** (all optional; absent ⇒ `None` ⇒ legacy path preserved):
  - `home_root_from_config(config) -> Path | None`
  - `active_project_from_config(config) -> str | None`
  - `projects_from_config(config) -> dict[str, dict] ` (the `projects{}` map; per ruling 6
    this map MAY carry display metadata but is **not** the authority for `code_repo` —
    `project.json` in the project root is, and is read in Slice 2, not here).
- **New resolver API** (the heart of the slice; pure, fail-closed, stdlib-only):
  - `resolve_home_root(start=None, *, explicit_root=None, env=None) -> Path`
    precedence (AIPOS-223 §Resolution algorithm, HOME ROOT): explicit flag → `LYBRA_HOME_ROOT`
    → `AIPOS_WORKSPACE_ROOT` (legacy = project root, back-compat) → `.lybra/config.json
    .home_root` (v2) → `~/.lybra/projects` default → upward `5_tasks/queue` search (legacy
    bare subtree). Fail-closed `FileNotFoundError("HOME_NOT_RESOLVED: ...")` only when none
    resolve **and** the default home does not exist (so a machine with no home yet gets a
    teaching error, not a silent mkdir — Slice 0 never creates anything).
  - `resolve_active_project(home_root, *, explicit=None, request_arg=None, env=None,
    config=None) -> str` precedence (ACTIVE PROJECT): `--project`/explicit →
    `LYBRA_ACTIVE_PROJECT` → request-arg `project` → `.lybra/config.json .active_project` →
    single-project fallback (exactly one `<home>/*/5_tasks/queue` ⇒ that one) → else
    fail-closed `ValueError("PROJECT_AMBIGUOUS: ...")`.
  - `resolve_project_root(home_root, project) -> Path` = `home_root / project`, asserting
    the `5_tasks/queue` marker; missing ⇒ fail-closed `FileNotFoundError("PROJECT_NOT_
    ESTABLISHED: ... run `lybra project new <name>`")`. (Per ruling 2 there is NO
    lazy-create; the teaching error points at `/project new`, implemented in Slice 2.)
  - `governance_paths(project_root) -> dict` returning the per-project governance map
    (`decision_log` = single `governance/decision_log.md` per ruling 1=B; `project_status`,
    `roadmap`), plus `stage_archive`, `workspace_artifacts` (ruling 7). **Returned, not yet
    consumed** — `board_adapter` adoption is Slice 1.
- **`default_workspace_config()` (`workspace_config.py:83-95`)**: bump `config_version` 1 → 2
  and add the optional keys `active_project` and `projects` **without** adding a live
  `home_root` that would re-route resolution. Rationale: a v2 config emitted by `lybra init`
  must still resolve to the **same** project root as today (it has `workspace_root: "."`),
  so resolution behavior is unchanged; the home wiring lands in Slice 2 (move-into-home).
  *(If the Owner prefers Slice 0 to emit zero schema change and defer the version bump to
  Slice 2, see Micro-decision M1.)*

**Explicitly NOT changed in Slice 0**: `resolve_workspace_root()`, `find_repo_root()`
(`task_loader.py`), `has_workspace_queue`, and every caller
(`aipos_cli.py`, `board_adapter.py`, `service_mode.py`, `tools.py`,
`confined_worker.py`). They keep their exact current behavior. The new API is parallel and
unwired.

### 2.2 Tests

- **Extend** `tools/aipos_cli/tests/test_workspace_root.py` — keep all 5 existing tests green
  (the regression proof) and add a `ResolutionCoreTests` class covering:
  - home-root precedence: explicit flag > `LYBRA_HOME_ROOT` > `AIPOS_WORKSPACE_ROOT` >
    config `.home_root` > default > upward search.
  - active-project precedence incl. single-project fallback **and** the multi-candidate
    `PROJECT_AMBIGUOUS` fail-closed.
  - `resolve_project_root` happy path + `PROJECT_NOT_ESTABLISHED` fail-closed (missing marker).
  - `governance_paths` returns the ruling-1=B single-file `governance/decision_log.md` and
    the ruling-7 `workspace_artifacts` under the project root.
  - `HOME_NOT_RESOLVED` fail-closed when nothing resolves and no default exists.
- **Regression anchors** (must stay green, run in the exit gate): the existing
  `find_repo_root` v1 tests, `test_cli_ergonomics.py`, `test_workspace_templates.py`,
  `test_scope_reachability.py` (proves token/scope model untouched).

### 2.3 Docs

None in Slice 0 (no user-facing behavior yet). README/runbook updates ride with the slice
that first changes behavior (Slice 1/2).

## 3. Red lines — how Slice 0 honors each

- **controlled-execute / 197/199/204**: not touched; `workspace_config.py` has no gate code.
- **★A1**: not touched; no scope/token code in this slice (`test_scope_reachability` green proves it).
- **canonical-fail-closed identity**: not touched; resolver is identity-orthogonal.
- **zero-dep**: only `pathlib`/`os`/`json` (already imported). No new import; acceptance
  third-party probe stays green.
- **★ evidence sites untouched**: `confined_worker.py` (Form B / Wall) is only a *caller* of
  `find_repo_root`, which is unchanged — so 191b / rerun / formb / copilot evidence
  workspaces are byte-for-byte untouched. Slice 0 creates/moves **nothing** on disk.

## 4. Dogfood + exit gate (all must pass before review)

```bash
cd /home/kiwi/lybra
# new + regression unit tests
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest tools.aipos_cli.tests.test_workspace_root -v
# full bare-venv suite — no regression, no skips beyond the known app/textual skips
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"
# acceptance (zero third-party + frontmatter correctness)
PYTHONPATH=$PWD python -m tools.acceptance.v1_acceptance        # ACCEPTANCE: PASS
# proof of minimal blast radius
git diff --stat        # only workspace_config.py + test_workspace_root.py
```

Exit criteria: new resolver tests green; **bare-venv full suite green (no regression)**;
ACCEPTANCE PASS; `git diff` shows only the two files; legacy `find_repo_root`/`resolve_
workspace_root` behavior provably unchanged (existing tests green).

## 5. Micro-decisions for Owner (small, but yours)

- **M1 — version-bump timing**: Slice 0 bumps `default_workspace_config()` to
  `config_version: 2` with additive optional keys (resolution unchanged). Alternative: emit
  no schema change in Slice 0 and bump in Slice 2 alongside the actual home wiring.
  *Recommendation: bump now (additive, harmless, gets the schema in front of the acceptance
  `lybra init` probe early).* Your call.
- **M2 — `projects{}` in home config**: per ruling 6, `project.json` (project root) is the
  sole authority for `code_repo`. I propose the home-config `projects{}` map, if present,
  carries **only** display/ordering metadata (never `code_repo`), to avoid a second source of
  truth. Confirm you want the map at all, or omit it entirely until needed.

## 6. Non-goals (Slice 0)

Wiring the resolver into any caller; creating/moving any directory; `/project new`; token
project dimension; TUI `/project`; governance de-hardcode; 196a repoint; decision_log
directory-ization (ruling 1=B defers it); any disk mutation under `~/.lybra` or the evidence
workspaces.

## 7. After Slice 0

Slice 1 (governance docs de-hardcode in `board_adapter.py` + `external_intake_writer.py`)
consumes `governance_paths()`; it is the first slice that changes observable behavior and
will get its own micro-plan → review → implement → dogfood → finalize.
