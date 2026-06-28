---
task_id: AIPOS-228
title: Governance Home Slice 4 — capability token `projects` dimension (mint + echo, zero enforcement)
status: draft
authority: NONE
parent: AIPOS-223
slice: 4 of 6
depends_on: [AIPOS-224 (Slice 0)]
created_by: cc
created_at: '2026-06-28'
phase: DRAFT (R direction-audit folded → Owner approve → implement → cc glm → finalize) — NOT implemented this session
r_reinforcements_folded: [R-a gate-inert flip-case, R-b project-naming parity, R-c existing-prose disclosure align, R-d display-point marker]
---

# AIPOS-228 — Governance Home Slice 4: token `projects` dimension (mint + echo) (DRAFT)

> **DRAFT / authority: NONE.** No product code, no commit. R direction-audit is **folded in**
> (R-a/R-b/R-c required, R-d evaluated-in → included; marked ★ R-* inline) — this goes straight
> to the Owner for approval, not back to R. Symbols/line refs read against current `main`
> (post-AIPOS-227 finalize, product `610440e`).

## 0. Thesis (what this slice is, and is not)

Slice 4 gives the capability token a **`projects` dimension — mint/echo only, ZERO enforcement.**
A role token gains an optional `projects` field; `serve rotate --project` can mint it into the
chosen role token(s); the field round-trips through `connection.json` → the request capability →
the `scope_basis` echo and is visible to read paths (TUI). **The gate makes NO decision from it
in this slice** — enforcement (`PROJECT_SCOPE_DENIED`) is Slice 5. This is the pure-additive
preparation片 that fixes the field shape + absence semantics so Slice 5 adds exactly one gate
check and changes no default.

## 1. Scope (what changes)

- **`ROLE_SPECS`** (`service_mode.py:40`): a spec MAY carry an optional `projects` list. Default
  = **absent** (specs as-is stay byte-identical).
- **`_role_token_entry`** (`service_mode.py:261`): when a spec carries `projects`, copy it into
  the minted token entry; when absent, the entry has no `projects` key (byte-stable).
- **`build_connection_config`** (`service_mode.py:272`): accept the `--project` selection and
  inject `projects` into the target role token entries; specs without it are unchanged.
- **`redacted_connection`** (`service_mode.py:332`): echo `projects` in the safe token view
  (descriptive, never the raw token).
- **`rotate_report`** (`service_mode.py:436`) + **`serve rotate` CLI** (`aipos_cli.py:872`): add
  `--project`. No `--project` → today's exact output.
- **capability echo** (`http_sse.py:136` → `tools.py:108` `scope_basis`): carry `projects` from
  the token entry into the request capability and echo it in `scope_basis`. **Echo only — no
  branch reads it for allow/deny.**
- **TUI** (`state.py:70`): already reads `scope_basis` verbatim; `projects` becomes visible with
  **no new decision branch**.

## 2. ★ Design constraints (AIPOS-223 §3 — must be fixed in the DRAFT)

1. **`projects` ⊥ `operations`, and "narrows only, never grants".** `projects` can only *shrink*
   the set of projects a token may act on; it can NEVER widen `operation` scope. The Slice-5
   ordering (project gate → operation-scope gate (★A1) → controlled-execute) is **not built
   here**; Slice 4 changes **zero bytes** of ★A1 / the operation-scope gate.
2. **Absence semantics = "do not narrow by project"** (operation scope still gates as today).
   This makes Slice 5 a pure addition that is fail-closed-safe:
   - `projects` present AND `active_project ∉ projects` → (Slice 5) `PROJECT_SCOPE_DENIED`.
   - `projects` absent → not narrowed by project (operation gate decides), unchanged.
   Slice 4 must shape the field + the absent-default so Slice 5 only **adds one check** and
   **changes no default**.
3. **Back-compat.** An existing token with NO `projects` field still mints/works byte-identically.
4. **★ Forward-naming parity (R-b).** The value space of `token.projects` is pinned to the SAME
   naming that `resolve_active_project` yields (`workspace_config.resolve_active_project`, used via
   `board_adapter._resolve_active_project_for`, ~`board_adapter.py:500-508`): a home project
   directory slug / the config `active_project` value — NOT a display name. The DRAFT fixes this
   so Slice 5's `active_project ∈ token.projects` is a like-for-like comparison, with no
   "display-name vs slug" mismatch. `serve rotate --project X` records `X` verbatim in that same
   slug space; no normalization layer is introduced (or, if any sanitization is unavoidable, it is
   identical to the project-slug rule and documented).

## 3. ★ Mint/echo-only red line (slice-specific, easiest to violate)

- **The field is never enforced.** The gate must produce **byte-identical allow/deny** with or
  without `projects`. A test asserts the field is **inert** (gate behavior unchanged) — see §5.
- **Honest disclosure (claims ⊆ disclosure).** README / docs MUST state that `projects` in Slice
  4 is **descriptive, not yet enforced**; enforcement arrives in Slice 5. A field that looks like
  a scope but is not enforced is **misleading if undisclosed** — this is an accountability red
  line. No "projects are isolated" claim until Slice 5.
- **★ R-c — align EXISTING over-claiming prose (required).** The code has NO project allow/deny
  branch (proven: only 2 `"project"` mentions in `tools.py`, both prose, never a decision). But
  two existing tool descriptions already *imply* project-scoping, contradicting this slice's
  "descriptive, not yet enforced" stance. `claims ⊆ disclosure` applies to these EXISTING strings,
  not only the README. Audited set (full `grep -niE 'project' tools/mcp_server/tools.py` → exactly
  these two):
  - **`tools.py:1702`** `intake_submit`: *"client_tag must map to an existing project"* — this is
    an **operation-scope** prerequisite of `intake_submit` itself (the client must resolve to a
    known project), NOT a per-token `projects` gate. Add a `(project dimension not yet enforced —
    Slice 5)` qualifier so it cannot be read as token-level project enforcement.
  - **`tools.py:1759`** `owner_decision_record`: *"capability_scope must include … the target
    project when present"* — likewise an operation-scope statement about the record's `applies_to`,
    NOT a `token.projects` check. Add the same `(project dimension not yet enforced — Slice 5)`
    qualifier; soften "must include the target project" so it does not imply an enforced token gate.
  - Implementation must re-run the `grep` and qualify EVERY same-class `"project"` prose hit (today
    exactly these two), so no description outruns the (still-zero) enforcement.
- **★ R-d — disclosure at the display point (evaluated → INCLUDED, low cost).** The "not yet
  enforced" marker also appears at the read surfaces, not only the README: `redacted_connection`
  (`service_mode.py:332`) and the `scope_basis` echo (`tools.py:108`) carry a sibling note (e.g.
  `projects_enforced: false`, or a `(not yet enforced)` annotation beside `projects`) so anyone
  reading `connection.json` / `scope_basis` is not misled without reading docs. **The marker is
  emitted ONLY alongside a present `projects` field** — a token without `projects` gets neither
  field, so the absence path stays byte-identical (§5). Cost is a single static field on each echo
  (no decision logic) → folded into this slice. (If, at implementation, it proves to perturb the
  gate-inert byte-equality, downgrade to README-only + a Slice-6 display item and record the
  reason here.)

## 4. Secret discipline (this slice mints tokens — directly relevant)

- `connection.json` stays `0600` (`REQUIRED_CONNECTION_MODE`, `write_connection_config`).
- Raw tokens are fingerprint-only in any visible output; never into argv / logs / git / records.
- `serve rotate --project` writes **no secret** into stdout/JSON (only `redacted_connection`).

## 5. Verify = positive truth (the B lesson)

- **Identity**: after `serve rotate --project X`, the target role's `connection.json` token entry
  has `projects` containing exactly `X` (equality, not "contains something"); file mode `0600`;
  token fingerprint-only (raw token absent from redacted output).
- **Absence stability**: `serve rotate` (no `--project`) → **no `projects` field** / safe default;
  output **byte-identical** to today.
- **★ gate inert (the crux)**: with AND without a `projects` field on the capability, the gate's
  allow/deny, ★A1, and `queue_claim` / `queue_return` / `owner_confirm` / `draft_publish` /
  `audit_verdict` / `audit_dispatch` behavior is **byte-unchanged** — assert the field is inert
  (no allow/deny delta attributable to `projects`).
- **★ R-a — gate-inert MUST cover the Slice-5 flip-case.** The inert assertion may NOT only test
  "field present AND matches → allow". It MUST add: a token carrying `projects` whose value does
  **NOT** contain the requested `active_project` (exactly the case Slice 5 will turn into
  `PROJECT_SCOPE_DENIED`) → Slice 4 still **ALLOWS**, byte-identical to the no-field case. This is
  the hardest proof that no latent read of `projects` has slipped into a decision; without it,
  "inert" is undertested precisely where it matters most.
- **Echo round-trip**: `projects` minted into the token entry appears verbatim in
  `redacted_connection` and in the `scope_basis` echo; the TUI surfaces it with no new branch.
- **v1 / zero-dep / three lanes**: BARE + SYSTEM + TUI gate + ACCEPTANCE all green; stdlib only;
  no new dependency.

## 6. Out of scope (explicit — later slices)

- Enforcement / `PROJECT_SCOPE_DENIED` + `_capability_in_project` + per-tool project gate (Slice 5).
- `board_adapter.py:~2132` audit-dispatch `"lybra"` literal (Slice 5).
- TUI `/project switch` + per-project copilot session (Slice 6).
- `decision_log` directory-ization (R5) — separate slice after 3–6 / v1.1.

## 7. Red lines (carried, must not regress)

- Two-root separation / ★A1 / canonical-fail-closed / executor≠auditor / copilot scopes `[]` /
  zero-dep gate core / gate-not-engine / `confined_worker` byte-unchanged — all untouched.
- stdlib-only; no new dependency; no new operation class.
- DRAFT phase: no product code, no commit; evidence sites
  (191b/rerun/formb/copilot/release-gate) zero contact; cc holds no owner token, does not confirm.
- Open follow-up (Owner-waived, tracked): rotate `LYBRA_PLANCHAT_LLM_KEY`.

## Expected blast radius (for the eventual implementation, not this DRAFT)

- `tools/aipos_cli/service_mode.py`: optional `projects` in `ROLE_SPECS` / `_role_token_entry` /
  `build_connection_config` / `redacted_connection`; `rotate_report` `--project` plumbing.
- `tools/aipos_cli/aipos_cli.py`: `serve rotate --project`.
- `tools/mcp_server/http_sse.py` + `tools/mcp_server/tools.py`: carry + echo `projects` in the
  capability / `scope_basis` (echo only, no decision).
- Docs/README: the "descriptive, not yet enforced" disclosure.
- Tests: mint/echo identity, absence byte-stability, **gate-inert** assertion, secret discipline.
- No change to `tools.py` gate decision logic, ★A1, `confined_worker`, or any operation scope.

## Verify commands (planned, for the implement phase)

```
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD python3                       -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD /tmp/lybra-216-venv/bin/python -m unittest discover -s tools/lybra_tui/tests -p "test_*.py"
PYTHONPATH=$PWD python3 -m tools.acceptance.v1_acceptance        # ACCEPTANCE: PASS
```
