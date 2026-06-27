---
id: AIPOS-225
parent: AIPOS-223
slice: 1
title: Governance Home — Slice 1, de-hardcode governance docs + project existence (back-compat)
status: draft
authority: NONE
date: 2026-06-27
depends_on: AIPOS-224 (Slice 0 resolution core)
red_lines:
  - controlled-execute / AIPOS-197/199/204 semantics frozen
  - "★A1 scope split (executor/copilot cannot owner_confirm / draft_publish)"
  - canonical-only fail-closed identity; executor != auditor independence
  - zero-dep (stdlib-only gate core; only lybra_tui/app.py may import textual)
  - "★ touch only governance resolution; ZERO contact with the three evidence sites (191b / rerun / formb / copilot)"
---

# AIPOS-225 — Slice 1: governance de-hardcode (micro-plan DRAFT, authority: NONE)

> Per AIPOS-223 §slicing Slice 1, consuming Slice 0's `governance_paths()`. **This is the
> first slice with observable behavior change.** Nothing is implemented yet.

## 0. One-line contract

**Remove the hardcoded `2_projects/lybra/` literal and the hardcoded `"lybra"` project name
from `get_governance()`, and the hardcoded `2_projects/<client_tag>` probe from
`_external_project_exists()` — resolving the new per-project `governance/` layout (ruling
1=B) and the `5_tasks/queue` marker, with a LEGACY FALLBACK so nothing breaks before Slice 2
performs the actual move.** No truth is moved in this slice.

## 1. The crux — Slice 1 lands *before* the move (Slice 2)

Today `get_governance()` reads `<resolved_root>/2_projects/lybra/{decision_log,project_status,
roadmap}.md` and hardcodes `"project": "lybra"` / `"project_root": "2_projects/lybra"`
(`board_adapter.py:76-80`, `:467-502`). The AIPOS-223 target is
`<project_root>/governance/{decision_log.md (single file, 1=B), project_status.md, roadmap.md}`.
But the move into the home (`~/.lybra/workspace/lybra/governance/`) is **Slice 2**, which
**depends on** Slice 1. So Slice 1 must de-hardcode in a way that is correct **both**:

- **pre-move** (today): `resolved_root` = the live workspace, docs at `2_projects/lybra/`.
- **post-move** (after Slice 2): `resolved_root` = the project root, docs at `governance/`.

**Design: resolve new-first, fall back to legacy.** A governance-dir resolver prefers
`<resolved_root>/governance/` when it holds `decision_log.md`, else falls back to the legacy
`<resolved_root>/2_projects/<project>/`. This makes Slice 1 strictly non-breaking and makes
Slice 2's move a transparent flip from the legacy branch to the new branch. The legacy branch
is a temporary bridge removed in Slice 2 once the move completes (see OPEN ITEM O1).

## 2. Scope — exactly what changes

### 2.1 `tools/aipos_cli/board_adapter.py`

- **`GOVERNANCE_FILES` (`:76-80`)** → from absolute-ish `2_projects/lybra/<x>.md` literals to
  **filenames only** under a resolved governance dir: `{"decision_log": "decision_log.md",
  "project_status": "project_status.md", "roadmap": "roadmap.md"}` (single-file decision_log,
  ruling 1=B). Filenames sourced from Slice 0 `governance_paths()` so there is one definition.
- **New helper** `_resolve_governance_dir(resolved_root, project) -> tuple[Path, str]`:
  returns `(governance_dir, layout)` where `layout ∈ {"home","legacy"}`. Prefers
  `resolved_root / "governance"` if `(.../governance/decision_log.md)` exists; else
  `resolved_root / "2_projects" / project` (legacy). If neither has the file, default to the
  **home** dir (so a fresh/missing project reports the canonical new path, WARN-missing — no
  crash, no legacy-resurrection).
- **`get_governance()` (`:467-502`)**:
  - Resolve the active **project** (de-hardcode the `"lybra"` literal): config
    `active_project` if present, else the documented v1.0 single-project default `"lybra"`
    (OPEN ITEM O2). Accept an optional `project` arg for forward-compat (Slice 5 will pass it
    from the gate); default None → resolve as above.
  - Compute `(governance_dir, layout)`; build docs from `governance_dir / filename`.
  - Report the **resolved** `project` and `project_root` (relative to resolved_root) +
    `governance_layout` (`home`/`legacy`) instead of the hardcoded `"lybra"` /
    `"2_projects/lybra"`. `_governance_doc` keeps reporting repo-relative `path`.

### 2.2 `tools/aipos_cli/external_intake_writer.py`

- **`_external_project_exists()` (`:134-135`)** → a project exists iff
  `has_workspace_queue(repo_root / client_tag)` (the home marker, lifted) **OR** the legacy
  `(repo_root / "2_projects" / client_tag).is_dir()` (back-compat bridge, removed in Slice 2).
  Import `has_workspace_queue` from `workspace_config` (stdlib, already in the tree).

### 2.3 Tests

- **`tools/aipos_cli/tests/test_board_adapter.py`**: keep existing governance assertions green
  for the **legacy** layout; ADD a `governance/`-layout case (docs under `<root>/governance/`,
  `project` resolved from config `active_project`, `governance_layout == "home"`); ADD a
  precedence case (both layouts present → home wins).
- **`tools/aipos_cli/tests/test_external_intake_writer.py`**: keep the legacy
  `2_projects/acme_client` "exists" test; ADD a `<root>/acme_client/5_tasks/queue` (home
  marker) "exists" test; ADD a "neither → not exists" test.
- No new test file required; both surfaces have existing suites.

### 2.4 Docs

None user-facing yet (the home layout isn't established until Slice 2). README/runbook
governance-path wording rides with Slice 2's move.

## 3. Red lines — how Slice 1 honors each

- **controlled-execute / 197/199/204**: untouched; `get_governance` is a read-only report;
  `_external_project_exists` is a read-only probe.
- **★A1 / scope / identity**: untouched; no token/scope/identity code in this slice.
- **zero-dep**: only `pathlib`/`hashlib` (already used) + `has_workspace_queue` from
  `workspace_config` (stdlib). No new dependency.
- **★ evidence sites untouched**: only `board_adapter.py` + `external_intake_writer.py` + their
  tests change; `confined_worker` and all evidence workspaces untouched. **No truth is moved or
  created on disk by this slice** (the move is Slice 2).

## 4. Dogfood + exit gate

```bash
cd /home/kiwi/lybra
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest \
  tools.aipos_cli.tests.test_board_adapter tools.aipos_cli.tests.test_external_intake_writer -v
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"   # full bare green
PYTHONPATH=$PWD python -m tools.acceptance.v1_acceptance        # ACCEPTANCE: PASS
git diff --stat   # Slice 1 = board_adapter.py + external_intake_writer.py + their 2 tests ONLY
```

Exit criteria: legacy-layout governance + external-intake tests still green (back-compat
proof); new `governance/`-layout + home-marker tests green; precedence (home > legacy) green;
**bare-venv full suite green (no regression)**; ACCEPTANCE PASS; `git diff` shows only the 4
files (board_adapter + external_intake + their tests).

## 5. Open items for Owner ruling

- **O1 — legacy-fallback lifetime**: the `2_projects/<project>/` (governance) and
  `2_projects/<client_tag>` (external) fallbacks are a temporary bridge so Slice 1 is
  non-breaking before the move. *Recommendation: keep them through Slice 1, and have Slice 2
  REMOVE the legacy branch the moment the move completes (no permanent dual-path).* Confirm, or
  keep the fallback permanently.
- **O2 — interim active-project source**: with the home model not yet wired, `get_governance`
  resolves the project from config `active_project`, else the v1.0 single-project default
  `"lybra"`. *Recommendation: config-else-default-"lybra".* Alternative: require explicit
  config / fail closed if unset (stricter, but breaks the current zero-config live setup).

## 6. Non-goals (Slice 1)

Moving/creating any truth on disk (Slice 2); `/project new`; the home-root resolver wiring
into the live path (Slice 2); token project dimension (Slices 4-5); TUI `/project` (Slice 6);
decision_log directory-ization (ruling 1=B defers it); any controlled-execute/scope/identity
change.

## 7. After Slice 1

Slice 2 — per-project truth establish + one-time move of `lybra` into `~/.lybra/workspace/lybra/`
(governance docs → `governance/`), `/project new`, and removal of the Slice 1 legacy fallback.
Its own micro-plan → review → implement → dogfood → cc glm audit → finalize.
