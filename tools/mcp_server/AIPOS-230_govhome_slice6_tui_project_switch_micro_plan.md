---
task_id: AIPOS-230
title: Governance Home Slice 6 — TUI /project switch + global active-project resolution fix (govhome closeout)
status: draft
authority: NONE
parent: AIPOS-223
slice: 6 of 6 (final)
depends_on: [AIPOS-224 (Slice 0), AIPOS-229 (Slice 5)]
created_by: cc
created_at: '2026-06-28'
phase: DRAFT v2 (R direction-audit found a load-bearing defect → re-drafted → re-submit to R) — NOT implemented this session
r_directionfix_folded: [resolution sequential-fallback global active_project, slice re-scoped not-pure-client, multi-project round-trip verify]
---

# AIPOS-230 — Governance Home Slice 6: TUI /project switch (DRAFT v2)

> **DRAFT / authority: NONE.** No product code, no commit. **v2:** R direction-audit found the v1
> mechanism (write global `active_project`, gate reads it) does NOT work as the code stands, and
> that the slice is mis-scoped. This re-draft fixes both. Symbols/line refs read against `main`
> (post-AIPOS-229 finalize, product `0da9ada`). The final govhome slice.

## Background — the load-bearing defect R caught (stated plainly)

The v1 §1 mechanism assumed the gate would read the global `~/.lybra/config.json` `active_project`
after a switch. It does NOT, today:

- `_resolve_active_project_for` (`board_adapter.py:500-508`) ALWAYS passes a non-`None` in-workspace
  `config` (an empty `{}` when no file) to `resolve_active_project`.
- `resolve_active_project` (`workspace_config.py:366-375`) step 3 is **mutually exclusive**:
  `if config is not None: <try in-workspace> else: <try global>`. A non-`None` (even empty) config
  therefore **always skips the global branch** — the global `active_project` is never read by the
  gate.
- Consequence: in a MULTI-project home, `active_project` does not resolve at all today
  (in-workspace empty → global skipped → single-project fallback fails with `PROJECT_AMBIGUOUS`).
  And `/project switch` only matters with ≥2 projects. So the v1 mechanism is a no-op exactly where
  it must work.

## 0. Thesis (corrected scope)

Slice 6 is **NOT "pure client, zero gate change."** It contains **one gate-side
resolution-precedence change** — making the global `active_project` actually reachable — which is
the real core of this slice and sits in the **D1/D2 resolution red-line zone**. Plus the
client-side `/project switch` + per-project copilot session. This DRAFT scopes the gate change
explicitly; the earlier "zero gate change" claim is withdrawn.

## 1a. ★ Gate-side fix — sequential (not mutually-exclusive) active_project precedence

Change `resolve_active_project` from the `if config / else global` mutual exclusion to a **sequential
fallback**:

```
explicit
  → LYBRA_ACTIVE_PROJECT env
  → in-workspace config active_project (if present)
  → global ~/.lybra/config.json active_project
  → single-project fallback (exactly one <home>/<child> with the marker)
  → fail-closed PROJECT_AMBIGUOUS
```

i.e. after the `if config: from_config = ...; if from_config: return` block, **drop the `else`** and
ALWAYS continue to the global lookup when still unresolved; `_resolve_active_project_for` must let
the global be reached (its empty `{}` config must no longer dead-end the chain).

**Constraints (binding):**
- **Slice-1 byte-compat preserved:** when the in-workspace config HAS `active_project`, it still
  wins first — the AIPOS-225 path is byte-unchanged.
- **New behavior:** when the in-workspace config has NO `active_project`, fall through to the global
  `active_project` — this is what makes `/project switch` effective.
- **fail-closed unchanged (D1/D2 iron rule):** only when ALL sources are empty →
  `PROJECT_AMBIGUOUS`; no silent default.
- **v1 byte-identity regression-lock:** legacy / single-project / env / explicit paths byte-unchanged.

## 1b. Mechanism (TUI side — unchanged from v1, confirmed still valid post-fix)

`/project switch <name>` = a **local Owner action** in the TUI app layer (like `/project new`,
`app.py:_cmd_project:642` — no gate confirm, no token, never routed through the copilot) that:
1. `workspace_config.set_active_project(name)` writes the global `~/.lybra/config.json`
   `active_project` (runtime config — NOT truth, NOT code; reversible); now ACTUALLY read by the
   gate thanks to 1a; AND
2. updates `TuiSession.active_project` + rebinds the copilot session's `project` (client display +
   DRAFT-landing context).

Next gate call resolves the switched project: in-scope (in the token's `projects`) → succeeds;
out-of-scope → the Slice-5 gate returns `PROJECT_SCOPE_DENIED`, surfaced verbatim in the TUI.
**Rejected:** a per-request `active_project` arg read by the gate — that WOULD be new gate decision
logic; the runtime-config write + 1a keep all ENFORCEMENT in the unchanged Slice-5 gate.

**Scope caveat (document):** the runtime `active_project` is process-global (single Owner / single
machine, R4), not per-TUI-session — a future refinement (§5). The copilot stays zero-write (the
config write is the app Owner-action layer, never the copilot credential).

## 2. State

- `TuiSession` gains `active_project` state, mirroring `mode` / `set_mode` (`state.py:85-96`).
  `/project switch <name>` sets it; `/project` (no arg) lists candidate projects
  (`_project_candidates(home)`) + shows the current active project; status line (`state.py:99-104`)
  shows it. DRAFTs land under the active project. `/project new <name>` (Slice 2) unchanged.

## 3. Red lines (corrected + tightened)

- **The ONLY gate-side change is the §1a resolution sequential-fallback.** Beyond it, NO new gate
  decision logic; the **Slice-5 enforcement is byte-unchanged** (the project gate / `dispatch_tool`
  / `_capability_in_project` untouched). ★A1 / two-root / zero-dep / gate-not-engine /
  `confined_worker` byte-unchanged.
- **copilot stays scopes `[]` / read-only / zero file-write.** Per-project session does not change
  this; switching mints no token, holds no owner token, never confirms. The config write is the app
  Owner-action layer.
- **No client-side fake isolation.** The TUI only sets `active_project` (runtime config + session)
  + displays; real isolation is the gate (Slice 5). Scope-exceeded MUST surface as the gate's
  `PROJECT_SCOPE_DENIED`, never a client-side hide.
- Only `app.py` imports `textual`; `copilot.py` textual-free; stdlib-only; no new dependency.

## 4. Verify = positive truth

- **★ Multi-project round-trip (crux — catches the v1 false-pass):** with a home holding **≥2
  established projects**, after `/project switch X` the gate resolves to X via the global config —
  assert `_resolve_active_project_for(...) == X` (identity, via the runtime-config round-trip, not
  the client state). It MUST be ≥2 projects: a single-project fallback would mask the bug by
  returning the right answer for the wrong reason.
- **Sequential-precedence unit tests:** in-workspace `active_project` present → returned first
  (Slice-1 byte-compat); in-workspace absent + global present → global returned (the new path);
  all empty → `PROJECT_AMBIGUOUS` (fail-closed); explicit / env / single-project / legacy paths
  byte-unchanged (v1 regression-lock).
- **In-scope switch:** DRAFT lands under X (identity equality); gate read/draft calls succeed.
- **Out-of-scope switch:** a subsequent gate call returns `PROJECT_SCOPE_DENIED`, surfaced in the
  TUI verbatim (not swallowed / not client-hidden).
- **State mirror:** `TuiSession.active_project` reflects the switch; `/project` lists + shows current.
- **copilot invariants:** still role=copilot / scopes `[]`; zero file-write (workspace content hash
  unchanged across chat/switch); only `app.py` imports textual (subprocess test).
- Three lanes green + ACCEPTANCE; WSL2 transport flake handled per the established transport-lane
  procedure.

## 5. Out of scope (explicit)

- Per-task / multi-project targeting within one connection; per-TUI-session (vs process-global)
  active project — future refinements.
- `decision_log` directory-ization (R5) — separate slice.

## Closeout note

When Slice 6 finalizes, the governance-home epic (AIPOS-223 / R2) is COMPLETE (Slices 0–6). The
finalize MUST mark **R2 closed** in decision_log + roadmap (epic-level closure entry) and confirm
the standing follow-ups carry forward (rotate the Owner-pasted LLM key; bare-suite WSL2 transport
flake).

## Expected blast radius (for the eventual implementation, not this DRAFT)

- `tools/aipos_cli/workspace_config.py`: §1a sequential fallback in `resolve_active_project`;
  `set_active_project(name)` runtime-config writer (`~/.lybra/config.json`), reversible.
- `tools/aipos_cli/board_adapter.py`: ensure `_resolve_active_project_for` lets the global be
  reached (don't dead-end on the empty `{}` config).
- `tools/lybra_tui/state.py`: `TuiSession.active_project` + setter.
- `tools/lybra_tui/app.py`: `/project switch <name>` + `/project` list/show; status-line active
  project; rebind copilot session `project` on switch.
- Tests: multi-project round-trip; sequential-precedence + Slice-1/v1 byte-lock; in/out-of-scope
  switch; state-mirror; copilot zero-write + scopes `[]`; only app.py imports textual.

## Verify commands (planned, for the implement phase)

```
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD python3                         -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD /tmp/lybra-216-venv/bin/python  -m unittest discover -s tools/lybra_tui/tests -p "test_*.py"
PYTHONPATH=$PWD python3 -m tools.acceptance.v1_acceptance        # ACCEPTANCE: PASS
PYTHONPATH=$PWD python3 -m unittest tools.aipos_cli.tests.test_workspace_root   # resolution precedence
```
