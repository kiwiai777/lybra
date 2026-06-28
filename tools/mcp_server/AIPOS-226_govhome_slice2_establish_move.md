---
id: AIPOS-226
parent: AIPOS-223
slice: 2
title: Governance Home — Slice 2, two-root separation + establish + topology-aware git-init
status: draft
authority: NONE
date: 2026-06-27
depends_on: AIPOS-224 (Slice 0 resolution core), AIPOS-225 (Slice 1 de-hardcode)
red_lines:
  - controlled-execute / AIPOS-197/199/204 semantics frozen
  - "★A1 scope split (executor/copilot cannot owner_confirm / draft_publish)"
  - canonical-only fail-closed identity; executor != auditor independence
  - zero-dep (stdlib-only gate core; git-init shells system git via subprocess — Owner-explicit, local, no push)
  - "★ touch only governance-home establish/move; ZERO contact with the three evidence sites (191b / rerun / formb / copilot)"
  - "git: Owner explicit one-shot only — NO background / scheduler / on-change / auto-commit / auto-push; Owner action, not a copilot capability"
  - "v1 legacy resolution byte-identical (AIPOS_WORKSPACE_ROOT + in-workspace .lybra/config.json upward search); two-root only affects the new home model (M1)"
---

# AIPOS-226 — Slice 2: two-root separation + establish (micro-plan DRAFT, authority: NONE)

> Folds the Owner ruling of 2026-06-27 into Phase 2a (not yet finalized). **Code provides the
> tools; the live flagship migration is an Owner-executed, reversible deploy (NOT code).**

## Phasing (M4)
- **Phase 2a (this implementation):** two-root separation + resolution rework + rename +
  `project new`/`set-repo` + topology-aware `home git-init` + docs + tests. **Legacy fallback
  kept** (removed only in Phase 2b, after the migration is verified-and-switched).
- **Owner migration (§6):** copy-verify-switch of the flagship truth (topology C).
- **Phase 2b:** remove the Slice-1 legacy fallback + no-residue dogfood → cc glm(2b) → finalize.

## 1. ★ Two-root separation (core design)

Lybra's own runtime state is kept **out of** the user's truth repo. Two distinct roots:

### 1.1 `~/.lybra/` — Lybra runtime root (fixed, global; NEVER enters a user truth repo)
- `~/.lybra/config.json` — `{config_version, home_root, active_project}`. **No secrets.** Points
  at the truth home + names the active project.
- `~/.lybra/local/connection.json` — role tokens (mode `0600`, fingerprint-only; raw tokens
  never printed). Lives here so tokens are never committed into a truth repo.

### 1.2 `LYBRA_HOME_ROOT` — truth home (default `~/.lybra/projects`, overridable)
- Holds ONLY project-related truth. Per project:
  `<project>/{governance/, 5_tasks/, stage_archive/, workspace_artifacts/, project.json}`.
- **No `.lybra/` inside the home.** (Runtime state + tokens live in `~/.lybra/`, §1.1.)
- `project.json` stays in the home (`<project>/project.json`, project→code_repo, no secret —
  committable alongside the project truth).

### 1.3 Resolution
- **home_root** = `LYBRA_HOME_ROOT` env → `~/.lybra/config.json .home_root` → default
  `~/.lybra/projects`.
- **active_project** = `--project` → `LYBRA_ACTIVE_PROJECT` env → `~/.lybra/config.json
  .active_project` → single-project fallback (scan `<home>/*/` for the **marker** =
  `5_tasks/queue` AND `project.json`) → fail-closed `PROJECT_AMBIGUOUS`.
- `serve rotate` writes `~/.lybra/local/connection.json`; TUI `default_connection_json` →
  `~/.lybra/local/connection.json`; `--connection-json` still overrides.

### 1.4 Legacy v1 untouched (M1)
`AIPOS_WORKSPACE_ROOT` env + the in-workspace `<ws>/.lybra/config.json` upward search remain
byte-identical back-compat. The two-root model + the new resolution only activate on the new
signals (`LYBRA_HOME_ROOT` env OR `~/.lybra/config.json` with `home_root`). v1 inputs and the
evidence workspaces resolve exactly as before.

## 2. Rename (brand + fix inconsistency)
`AIPOS_HOME_ROOT` → **`LYBRA_HOME_ROOT`** (aligns with the existing `LYBRA_ACTIVE_PROJECT`).
Change the `HOME_ROOT_ENV` constant + the 2 CLI/TUI prints + 1 test + docs (223/224/226).
**Keep legacy `AIPOS_WORKSPACE_ROOT` unchanged.**

## 3. `lybra home git-init` — topology-aware (product supports 3 topologies)
- **★ Refuse if the target (home root, or a project root) is already inside a git repo** (walk
  up for `.git`) → teaching error (no nested repos; this is what makes the Owner dogfood
  topology C safe — it detects it is already inside the `ai-project-os` repo and declines).
- **Granularity:**
  - workspace-level (the whole home = one repo) — **topology A**.
  - per-project (`<project>/` is its own repo) via `--project <name>` — **topology B**.
- Still: one-shot, transparent (prints the exact commands first), **no remote config, no push**
  (only prints the push commands), no background/scheduler/auto. Owner action; the copilot
  cannot invoke it.
- **223 §8 rewrite:** home persistence = git-backed via one of **three topologies**
  {workspace-repo (A) / per-project-repo (B) / external-existing-repo (C)} — not a single shape.

## 4. Owner dogfood = topology C (example in this plan; realized by the §6 migration)
- `LYBRA_HOME_ROOT=~/ai-project-os/2_projects`; `lybra` reorganized in-place to the home layout
  (§5). `~/.lybra/` (config + tokens) lives **outside** `ai-project-os`.
- Lybra sees **only `lybra`** via the marker (the other 8 projects under `2_projects/` have no
  per-project `5_tasks/queue`+`project.json` marker → invisible to the gate).
- Pushing uses the **normal `ai-project-os` flow, the same single remote, no new repo.**
- `lybra home git-init` here detects it is already inside the `ai-project-os` git repo →
  **refuses** (no nesting).

## 5. Layout + decision_log
Per-project root:
```
<project>/
├─ project.json                  (harness/tool config — project→code_repo; NOT governance truth)
├─ CLAUDE.md                     (★ harness role contract — auto-loaded ONLY from root/parents;
│                                   stays at root like project.json, NEVER moved to governance/)
├─ governance/
│  ├─ decision_log.md            (single file — directory-ization is the separate R5 slice,
│  ├─ project_status.md           after migration verify)
│  ├─ roadmap.md
│  ├─ README.md SC1_*.md v1.0_scope_parking.md   (meta docs → governance/; these do NOT
│  │                              participate in harness auto-load — only CLAUDE.md is critical)
│  └─ reports/                    (AIPOS-*.md + *_evidence/)
├─ 5_tasks/                       (queue/records/drafts/orchestration; .gitkeep so empty queue
│                                   marker dirs survive git round-trips)
├─ stage_archive/
└─ workspace_artifacts/
```
★ Project root holds exactly: `project.json` + `CLAUDE.md` (both harness config) + the 4
category dirs. `CLAUDE.md` at root is load-bearing — Claude Code auto-loads it only from the
project root + parents, so moving it into `governance/` would silently drop the planning-session
read-only discipline (functional regression).

## 6. Migration = Owner reversible deploy (NOT code), topology C safe
Code provides only scaffold / resolution / `lybra project new` / `git-init`. The Owner moves
the real data, reversibly:
- **(a)** Confirm the root `ai-project-os/5_tasks` is **lybra-only** (grep tasks' `project`
  field); only then move it into `2_projects/lybra/5_tasks/`.
- **(b)** **Copy (not mv)** the lybra truth into the home layout (governance/ + reports/ +
  5_tasks/ + stage_archive/ + workspace_artifacts/ + project.json); the old flat content stays.
- **(c)** Verify the gate reads the full truth from the home (decision_log/status/roadmap +
  queue/records/drafts content correct); the script does **verify → STOP** (no config switch,
  no change to the old location).
- **(d)** Owner + cc eyeball the verify output; only then switch `~/.lybra/config.json` →
  home + freeze the old location (NOT delete — rollback-able).
- **(e)** Commit = a **normal `ai-project-os` commit** + manual review of the precise diff
  (only `2_projects/lybra/` + the `5_tasks` move + `.gitignore`; `.gitignore` backstops any
  secret; tokens are in `~/.lybra/`, outside the repo). **Never force/overwrite**; the other 8
  projects + the framework are preserved.

## 7. Red lines + verification
- No controlled-execute / ★A1 / canonical-fail-closed / zero-dep change; stdlib only (git via
  subprocess, Owner-explicit, local, no push).
- The three evidence sites (191b/rerun/formb/copilot) untouched; **`ai-project-os` is zero-touch
  and zero-push until the Owner confirms.**
- Dogfood: `project new` round-trip; with `LYBRA_HOME_ROOT=<existing dir>` the gate reads
  correctly and tokens land in `~/.lybra/` (not in the home); v1 byte-identical; bare-venv full
  suite green + ACCEPTANCE PASS; `git diff` only this slice's files.
- Sequence: fold-in → re-run Phase 2a gate → cc glm (AIPOS-226R) → Owner approve → Owner runs
  the reversible migration (§6) → verify → Phase 2b (remove legacy + no-residue dogfood) →
  cc glm (2b) → finalize.

## 8. Code scope (Phase 2a)
- `tools/aipos_cli/workspace_config.py`: rename `HOME_ROOT_ENV`→`LYBRA_HOME_ROOT`; add
  `~/.lybra/config.json` global-config reader; rework `resolve_home_root` /
  `resolve_active_project` (§1.3 precedence; marker = `5_tasks/queue`+`project.json`);
  `resolve_workspace_root` home trigger = `LYBRA_HOME_ROOT` env OR global-config `home_root`;
  keep `scaffold_project` / `write_project_json` / `set_project_repo` (no `.lybra/` in home).
- `tools/aipos_cli/service_mode.py`: connection.json default location → `~/.lybra/local/`
  (global runtime root), `--connection-json` override intact; v1 workspace-local path still
  honored for legacy. (★A1-adjacent — token minting/scopes unchanged; only the file location.)
- `tools/lybra_tui/__main__.py`: `default_connection_json` → `~/.lybra/local/connection.json`.
- `tools/aipos_cli/home_git.py`: topology detection (refuse-if-in-repo) + scope
  (workspace / `--project`).
- `tools/aipos_cli/aipos_cli.py`: rename in prints; `home git-init` scope flag.
- Docs: 223 §8 (3 topologies + two-root), 224 (rename note), 226 (this).
- Tests: rename; two-root resolution; marker; global config; connection.json location; git-init
  topology; v1 regression locks.

## 9. Non-goals
Remove the legacy fallback (Phase 2b); run the migration; 196a repoint (Slice 3); token project
dimension + `board_adapter:2132` (Slices 4-5); TUI `/project` switch+session (Slice 6);
decision_log directory-ization (R5); any controlled-execute/scope/identity change; automating
git; deleting any truth.
