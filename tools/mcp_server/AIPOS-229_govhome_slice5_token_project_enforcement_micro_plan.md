---
task_id: AIPOS-229
title: Governance Home Slice 5 — token project ENFORCEMENT (PROJECT_SCOPE_DENIED) + de-hardcode "lybra"
status: draft
authority: NONE
parent: AIPOS-223
slice: 5 of 6
depends_on: [AIPOS-224 (Slice 0), AIPOS-228 (Slice 4)]
created_by: cc
created_at: '2026-06-28'
phase: DRAFT (R direction-audit folded → Owner approve → implement → cc glm → finalize) — NOT implemented this session
r_reinforcements_folded: [R-i enforcement-completeness choke-point + enumerated coverage test, R-ii gate-order projects-present-first backcompat, R-α 18-gated/0-exempt incl. read tools (binding)]
status_note: Owner-approved for implementation (2026-06-28) with R-α binding.
---

# AIPOS-229 — Governance Home Slice 5: token project ENFORCEMENT (DRAFT)

> **DRAFT / authority: NONE.** No product code, no commit. R direction-audit is **folded in**
> (R-i/R-ii required, marked ★ inline) — this goes straight to the Owner for approval, not back to
> R. Symbols/line refs read against current `main` (post-AIPOS-228 finalize, product `160c6e0`).
> This is the most decision-sensitive slice — ★A1 is touched only by gaining a new gate IN FRONT
> of it, never weakened.

## 0. Thesis (what this slice is, and is not)

Slice 5 **flips the Slice-4 inert `projects` field into enforcement** — a pure addition: a new
`_capability_in_project` + a per-tool **project gate** that yields **`PROJECT_SCOPE_DENIED`**,
ordered BEFORE the operation-scope (★A1) gate. It also **de-hardcodes the `"lybra"` literal** at
`board_adapter.py:2181` (audit-dispatch). No default changes for tokens without `projects`.

## 1. ★ Ordering & ★A1 not-weakened (the crux of the whole slice)

- **Order = project gate → operation-scope gate (★A1) → controlled-execute.** The project gate is
  a NEW pre-check ADDED in front of ★A1; it can ONLY `DENY` (narrow), never bypass or relax ★A1.

- **★ R-α (binding, Owner) — ALL 18 `TOOL_HANDLERS` are project-gated, including the 4 read tools
  (`lybra_queue_list` / `lybra_task_preview` / `lybra_validate` / `lybra_context_pack_build`).**
  Reads need truth-isolation too — a project-A token must not READ project-B truth. **Zero
  exemptions** (a deliberate decision, not an oversight: `initialize` is JSON-RPC `initialize`, not
  `tools/call`; capability introspection goes through descriptors, not `dispatch_tool`). The
  enumerated test encodes "18 gated / 0 exempt": iterate `TOOL_HANDLERS`, assert `len == 18`, each
  returns `PROJECT_SCOPE_DENIED` under project-mismatch, and there is NO exemption list (a new tool
  left off the choke-point → count mismatch or a non-DENY entry → red).

- **★ R-i — enforce at the single dispatch CHOKE-POINT, not per-handler.** Every tool call passes
  through ONE point: `server.py:_handle_tools_call` (`:56-68`), which does
  `handler = TOOL_HANDLERS.get(name); return handler(arguments)`. `TOOL_HANDLERS` (`tools.py:1597`)
  is the complete registry of all 18 read+write tools. Slice 5 routes dispatch through a single
  `tools.dispatch_tool(name, arguments)` that runs `_project_gate_denied()` FIRST, then calls the
  handler — so the project gate is **structurally unavoidable for every current AND future tool**,
  not a line copied into ~20 handlers (where one miss = a silent cross-project hole). `server.py`
  calls `dispatch_tool`; the per-handler `_<op>_scope_allowed()` ★A1 checks stay exactly where they
  are, AFTER the choke-point. Ordering holds: choke-point project gate → handler's ★A1 gate →
  controlled-execute.
  - **Completeness is proven, not asserted by inspection (mandatory test, §6):** a program­matic
    test enumerates **every** entry in `TOOL_HANDLERS` and asserts each returns
    `PROJECT_SCOPE_DENIED` under a project-mismatch capability — and that the gated set count
    equals `len(TOOL_HANDLERS)` (a new tool added without the choke-point turns this red).
  - **Introspection surface (`visible_tool_descriptors`, `tools.py:2031` + the `2033-2041`
    `_<op>_scope_allowed()` block):** reuses the SAME `_capability_in_project` so a
    project-mismatched token's listing reflects the narrowing (no second logic). Decision for
    R/Owner: either (a) project-mismatch → list only read descriptors / empty (visibility mirrors
    enforcement), or (b) keep listing operation-scoped tools but every call is choke-point-denied
    (call-time authoritative, mirroring how an expired token still lists then denies). DRAFT
    proposes (a) for honesty; either way the authoritative gate is the choke-point.

- **★A1 logic is byte-unchanged.** `_capability_has_scope` (`tools.py:197`) keeps reading only
  `operations`/`token_ref`/`expires_at`. executor/copilot remain `SCOPE_DENIED` on
  `*_confirm` / `draft_publish` REGARDLESS of project — the project gate NEVER grants an operation
  a role lacks; it only adds one more way to be denied. (Slice 4 proved the field inert; Slice 5
  adds the decision strictly in front.)

## 2. Flip semantics (carries Slice 4 §2)

- `projects` present AND `active_project ∉ projects` → **`PROJECT_SCOPE_DENIED`** (fail-closed).
- `projects` absent → not narrowed by project (operation gate decides) → **byte-identical to
  today** (back-compat). This is exactly the pure-addition Slice 4 prepared.
- `_capability_in_project(active_project)` = `projects` absent → `True` (not narrowed); else
  `active_project in projects`.

## 3. ★ Request-side `active_project` source + fail-closed

- The gate's "request target project" is resolved by the SAME resolver as everything else:
  `_resolve_active_project_for(_repo_root(), None)` (`board_adapter.py:500-508`) → Slice 0
  `resolve_active_project` (home model). Its naming is the SAME slug space `token.projects` was
  pinned to in Slice 4 (R-b), so membership is like-for-like.
- **R-iii (confirmed feasible):** `_repo_root()` is already available inside the gate layer
  (`tools.py`, e.g. used at `:814`); `_project_gate_denied` uses the same `_repo_root()` — no new
  plumbing.
- **★ R-ii — gate-internal order is `projects`-present-FIRST (back-compat critical).**
  `_project_gate_denied()` MUST be written exactly:
  1. `projects = _capability_token().get("projects")`; **if not `projects` → `return None`
     (allow) — do NOT resolve `active_project` at all.**
  2. ONLY when `projects` is present → resolve `active_project`; on resolution failure →
     fail-closed `PROJECT_SCOPE_DENIED` (this deny is reachable ONLY in this branch); else
     membership test (`active_project in projects` → allow, else `PROJECT_SCOPE_DENIED`).
  - Rationale: a token with NO `projects` field, in a project-AMBIGUOUS workspace, is allowed
    TODAY. If "resolution failure → deny" were unconditional (resolve THEN branch), such a token
    would newly be denied — violating "absent = byte-identical to today". The §2 "absent → True"
    short-circuit and this ordering are the SAME rule: never resolve before the presence check.
- **Single-project-per-connection model (R4):** the connection resolves ONE `active_project` for
  the workspace; the token's `projects` must contain it. (Per-task / multi-project targeting is a
  future refinement — out of scope, §7.)
- **Fail-closed (D1/D2 iron rule):** when `projects` IS present and `active_project` cannot be
  resolved (ambiguous / not established), the gate returns **loudly** (`PROJECT_AMBIGUOUS` surfaced
  as `PROJECT_SCOPE_DENIED` / BLOCK) — **never a silent allow**. (Reachable only via step 2 above.)

## 4. De-hardcode the `"lybra"` literal

- `board_adapter.py:2181` `"project": source_metadata.get("project") or "lybra"` (audit-dispatch
  record builder) → use the **resolved** active project:
  `source_metadata.get("project") or _resolve_active_project_for(repo_root, None)`. No `"lybra"`
  fallback; if neither the source metadata nor resolution yields a project, **fail closed** (BLOCK),
  not a silent `"lybra"`. (`grep -n '"lybra"' board_adapter.py` → this is the sole remaining
  literal; re-run at implementation to confirm none survive.)

## 5. Disclosure flip (carries Slice 4 R-c/R-d)

- Remove the `"(project dimension not yet enforced — Slice 5)"` qualifier from the two `tools.py`
  prose sites (it IS enforced now). Flip the marker: `projects_enforced: true` (or drop the
  marker) in `redacted_connection` / `scope_basis` echo. Update README/docs to state **"projects
  are now enforced."** `claims ⊆ disclosure`: only NOW may "project isolation" be claimed.

## 6. Verify = positive truth (carries the B lesson + the inverse of R-a)

- **★ Inverse flip-case (the crux):** the exact case Slice 4 proved ALLOW (token carries
  `projects` NOT containing the active project) must now return **`PROJECT_SCOPE_DENIED`** —
  precise inverse assertion of `test_token_projects_gate_inert::test_flip_case_*`.
- **★ Enumerated completeness (R-i + R-α): "18 gated / 0 exempt".** Programmatically iterate
  **every** `TOOL_HANDLERS` entry; assert `len(TOOL_HANDLERS) == 18`, each returns
  `PROJECT_SCOPE_DENIED` under a project-mismatch capability (INCLUDING the 4 read tools), and
  there is NO exemption list. A future tool added outside the choke-point → count mismatch or a
  non-DENY entry → red (completeness proven, not inspected).
- **Back-compat byte-stable:** token with NO `projects` + matching/any project → still ALLOW
  (operation gate decides), byte-identical to today.
- **★ Absent does NOT resolve (R-ii):** with NO `projects` field, the gate allows WITHOUT calling
  `_resolve_active_project_for` — assert via a patched resolver that RAISES: a no-`projects` token
  still passes (the resolver is never reached), proving an ambiguous-workspace token is not newly
  denied.
- **★A1 independent assertion:** executor/copilot `*_confirm` / `draft_publish` still
  `SCOPE_DENIED` by operation scope — with `projects` matching AND with it absent — proving the
  project gate did not weaken or substitute ★A1.
- **Order proof:** a request that fails BOTH gates is denied by the PROJECT gate first
  (project-deny precedes operation-deny) — assert the returned error is `PROJECT_SCOPE_DENIED`,
  confirming the ordering.
- **Fail-closed:** unresolvable `active_project` → loud deny, never silent allow.
- **Literal gone:** audit-dispatch project = resolved active project (positive truth: identity ==
  resolved project), and no `"lybra"` literal remains.
- **Flake pre-handling:** first characterize the bare-suite WSL2 transport flake (DL-20260628-03
  follow-up) blast radius — if it touches the gate-decision lane, fix/stabilize FIRST; otherwise
  inspect each flaky failure to confirm it is the known transport timeout, not a real Slice-5
  failure. Then three lanes green + ACCEPTANCE + zero-dep.

## 7. Out of scope (explicit — later)

- TUI `/project switch` + per-project copilot session (Slice 6).
- Per-task / multi-project targeting within one connection (future refinement).
- `decision_log` directory-ization (R5) — separate slice.

## Red lines (carried, must not regress)

- **★A1 logic byte-unchanged** — the project gate is ADDED in front, only DENIES, never bypasses
  or grants. executor≠auditor / canonical-fail-closed / copilot scopes `[]` unchanged.
- Two-root separation / zero-dep gate core / gate-not-engine / `confined_worker` byte-unchanged;
  stdlib-only; no new dependency; no new operation class (PROJECT_SCOPE_DENIED is a denial, not a
  new operation).
- DRAFT phase: no product code, no commit; evidence sites zero contact; cc holds no owner token,
  does not confirm.
- Open follow-ups (tracked): rotate `LYBRA_PLANCHAT_LLM_KEY`; bare-suite WSL2 transport flake.

## Expected blast radius (for the eventual implementation, not this DRAFT)

- `tools/mcp_server/tools.py`: `_capability_in_project` + `_project_gate_denied` (returns a
  `PROJECT_SCOPE_DENIED` teaching error or None) + a single `dispatch_tool(name, arguments)`
  choke-point that runs the project gate before `TOOL_HANDLERS[name]`; `visible_tool_descriptors`
  reuses `_capability_in_project`; `scope_basis`/prose disclosure flip. **No change to
  `_capability_has_scope` / ★A1 / the per-handler operation-scope checks.**
- `tools/mcp_server/server.py`: `_handle_tools_call` calls `dispatch_tool` instead of indexing
  `TOOL_HANDLERS` directly (the choke-point).
- `tools/aipos_cli/board_adapter.py`: de-hardcode `:2181` to the resolved active project,
  fail-closed.
- `tools/aipos_cli/service_mode.py` (+ `http_sse.py`): `projects_enforced: true` (or drop marker).
- Docs/README: "projects are now enforced."
- Tests: inverse flip-case (DENY), back-compat ALLOW, ★A1-independent, ordering proof,
  fail-closed, literal-gone; the Slice-4 inert tests updated to their enforced counterparts.

## Verify commands (planned, for the implement phase)

```
PYTHONPATH=$PWD /tmp/lybra-bare-venv/bin/python -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD python3                       -m unittest discover -s tools -p "test_*.py"
PYTHONPATH=$PWD /tmp/lybra-216-venv/bin/python -m unittest discover -s tools/lybra_tui/tests -p "test_*.py"
PYTHONPATH=$PWD python3 -m tools.acceptance.v1_acceptance        # ACCEPTANCE: PASS
PYTHONPATH=$PWD python3 -m unittest tools.mcp_server.tests.test_scope_reachability   # ★A1 / scope anchors
```
