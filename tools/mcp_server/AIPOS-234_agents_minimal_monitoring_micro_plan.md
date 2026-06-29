---
id: AIPOS-234
title: /agents minimal monitoring — read-only client projection of queue truth
status: draft
authority: NONE
task_class: simple
phase: micro-plan DRAFT (R direction-audit folded; pre Owner approval)
parent: v1.0 — remaining backlog item (/agents monitoring panel, minimal)
symbols_read_from: main @ 61b02c4 (post-AIPOS-233)
r_reinforcements_folded:
  - "R-1 canonical grouping key + divergence shown + faithful projection (each task once, unassigned bucket)"
  - "R-2 data.tasks source (not data.summary) + project-scope inherited from the gated read"
  - "R-3 one-shot on-command, NO timer/auto-refresh (gate-not-engine, command level)"
  - "R-4 show recorded timestamps if present (claimed_at/returned_at); never fabricate"
---

# AIPOS-234 — /agents minimal monitoring (read-only)

> **Nature.** DRAFT only. authority: NONE. Writes no product code, commits nothing.
> Output is this file → R direction-audit → Owner approves → implement + tests → auto-write
> cc-glm audit card → `cc glm` → Owner finalize. All symbols read from `main` @ `61b02c4`; each
> cited symbol was verified to exist before being written (positive-truth, not assumed).

---

## §0 Thesis

`/agents` = a **read-only snapshot**: the client aggregates the data the existing `queue_list`
read already returns (every task carries `queue_state` / `assigned_to` / `claimed_by` /
`agent_instance`), grouped **by agent**. **No new gate state, no new write, no live presence.**
This is the **minimal** version (rich monitoring = v1.1, Owner-decided).

---

## §1 Grounding — every claim lands on a real symbol (R pre-checked, cc verified on `main` @ 61b02c4)

- **Per-task agent fields already exist:** `task_loader.py:80-85` emits `queue_state`,
  `assigned_to`, `agent_instance`, `claimed_by` on every loaded task.
- **The queue read already surfaces those rows:** `board_adapter.get_queue` (`board_adapter.py:424`)
  → `validate_tasks` → a response whose `data` carries a per-task list. **Verified live:**
  `get_queue('.')` returns `data` with keys incl. `tasks` (alongside `summary`,
  `effective_queue_summary`, `scope`); each `tasks[*]` carries `task_id` / `queue_state` /
  `assigned_to` / `agent_instance` / `claimed_by`. ⇒ the aggregation source is **already present**;
  no new gate tool is required.
- **The TUI already reads it, read-only:** `state.py:121-123` maps `observe("queue")` →
  `lybra_queue_list`; `app.py:561` already renders `/queue` from `observe("queue").data.summary`.
  `/agents` is a sibling that instead groups `observe("queue").data.tasks` by agent.
- **Lybra explicitly does NOT track liveness:** `agent_profiles.py:~132` —
  *"Lybra does not track live agent presence or heartbeat state; this is expected for a
  gate-not-engine runtime."* Monitoring here is a **projection of recorded truth, not a new
  mechanism.**

---

## §2 Minimal view

A TUI `/agents` command:

- **Source (R-2):** reads `observe("queue").data.tasks` — the **per-task rows**, NOT the
  `data.summary` that `/queue` currently renders (`app.py:561`). Same single read the TUI already
  performs; no new observation.
- **Pure read-only render — a snapshot table.** No mutation, no new observation beyond the queue
  read.

### §2a Grouping key — canonical, unambiguous (★ R-1)

`assigned_to`, `agent_instance`, `claimed_by` (`task_loader.py:83-85`) are **three independent
fields that can disagree**. The grouping must be exact:

- **Group key = the row's resolved *canonical* owning instance.** Resolve `claimed_by` (else
  `agent_instance`) via `resolve_instance_id(...)` against the profiles the queue read already loads
  (read-only — the SAME Slice-5 canonical-instance identity used by the gate), and group on the
  resulting `canonical_instance_id`. If it does not resolve (or both fields are absent), fall back to
  the raw string; if there is no owning field at all, the task goes into an explicit **`unassigned`**
  bucket.
  - *Implementation obligation (positive-truth):* confirm whether `data.tasks[*]` already carries a
    resolved canonical field; if not, resolve client-side via the profiles read (still read-only, no
    new gate tool). Do not assume a field that isn't there.
- **Show divergence, never silently pick one.** When `assigned_to` (intended) differs from the
  resolved owning instance (`claimed_by`/`agent_instance`), the row **displays both** — an
  `assigned_to ≠ claimed_by` divergence is meaningful truth, not noise to hide.
- **Faithful partition — no double-count, no drop.** Every task in `data.tasks` appears in **exactly
  one** group; the total of grouped tasks equals the number of input rows in scope; unassigned/
  unclaimed land in the explicit `unassigned` bucket.

### §2b Per-agent rows + recorded timestamps (R-4)

- For each agent group, list the tasks it holds + each task's **recorded** state (`queue_state`, and
  `audit_readiness` / returned status **when present on the row**).
- **If a row carries a recorded timestamp** (`claimed_at` / `returned_at`), display it (a stale
  claim then reads as record-age — reinforcing honesty). **If a row does not, do NOT fabricate one
  and do NOT add a new read to fetch it** — the "as recorded — Lybra does not track live presence"
  label (§5) carries the honesty. Keep it minimal.

### §2c Scope — inherited from the gated read (R-2)

- **Scope = the current active project, inherited for free.** `queue_list` / `get_queue` is already
  Slice-5 project-gated, so `/agents` — built on that already-gated read — **naturally sees only the
  active project**; it adds **no scope logic of its own**.
- **Cross-project aggregation needs an owner-token and is v1.1 (out of scope).**

---

## §3 ★ Top red line: gate-not-engine (command level — R-3)

- **No live presence** ("agent X is online") · **no polling** · **no heartbeat** · **no background /
  auto-refresh** · **no daemon.** Holds `agent_profiles.py:~132`.
- **`/agents` is one-shot on-command — exactly like `/queue`** (`app.py:560-561`): it renders once
  when the Owner runs it, and never automatically.
- **Do NOT add any `set_interval` / auto-refresh timer.** The only `set_interval` in the TUI
  (`app.py:809`, `_thinking_timer`) is the thinking-spinner animation — **unrelated to `/queue`, and
  must NOT be imitated** for `/agents`.
- copilot scopes `[]` / read-only unchanged; **no new tool that makes any decision** is added.

---

## §4 Surface (minimal — prefer pure client)

- **Prefer a pure-client aggregation:** reuse the TUI's existing `observe("queue")` read and group in
  the client — **zero new gate tool, zero new gate surface.** (§1 verified the data already carries
  the per-task agent fields, so this is sufficient.)
- **Only if the data were genuinely insufficient** would a read-only projection tool be added — and
  even then it would be **read-only (scopes `[]`)** and routed through the Slice-5 dispatch
  choke-point (`server.py:_handle_tools_call → tools.dispatch_tool`, project gate first). But the
  **default is pure client**, to keep the change minimal and add no new gate surface.

---

## §5 Honest disclosure

- The view is labelled **"as recorded (the queue's last-known recorded state) — Lybra does not track
  live presence"** (echoing `agent_profiles.py:~132`).
- **claims ⊆ disclosure:** the view must NOT imply liveness/online-ness in any wording.

---

## §6 verify = positive truth

- **view == real queue truth (faithful projection, R-1):** every agent row / task row in `/agents`
  equals the actual `observe("queue").data.tasks` content (identity equality + state echoed verbatim
  — never a fabricated or inferred state). Assert the grouping is a faithful **partition**: each task
  appears in **exactly one** group, `sum(len(group)) == len(input rows in scope)`, the grouping key is
  the resolved canonical instance, and an `assigned_to ≠ claimed_by` divergence is **visible** (assert
  both values render). Assert the `unassigned` bucket catches unowned rows.
- **project-scope inherited (R-2):** a project-scoped token's `/agents` shows only the active
  project's rows — assert it inherits the Slice-5 gated read (no rows from another project; no extra
  scope logic in `/agents`).
- **one-shot, no timer (R-3):** grep positive-absence assertion over the slice's diff — `/agents`
  adds **no** `set_interval` / auto-refresh / `while True` / `Timer` / `asyncio` loop / `threading`
  refresh / `poll` / `heartbeat` / scheduler. `/agents` renders once per invocation.
- **read-only / zero-write:** workspace truth hash unchanged after `/agents`; if any tool is
  involved it is `scopes []` read-only; copilot scopes `[]` unchanged.
- **"not live" disclosure present (R-4 / §5):** assert the "as recorded — Lybra does not track live
  presence" label string is rendered; recorded timestamps shown only when present on the row (no
  fabrication).
- **dependency isolation** — only `app.py` imports textual (acceptance grep); three lanes green +
  ACCEPTANCE.

---

## §7 Out of scope

- Rich monitoring (v1.1).
- Live presence / heartbeat (NEVER — gate-not-engine).
- Auto-refresh / polling.
- Cross-project aggregation (owner-token, v1.1).

---

## Red lines / deliverable

- gate-not-engine (no live / poll / daemon / auto-refresh) · copilot scopes `[]` / read-only /
  zero-write · two-root / ★A1 / zero-dep / `confined_worker` byte-unchanged · prefer client (no new
  gate tool; if a tool is unavoidable it is read-only via the choke-point) · stdlib-only · only
  `app.py` imports textual.
- DRAFT writes no code and commits nothing; cc holds no owner token and never confirms; evidence
  zero-touch.

**Flow:** DRAFT **(R direction-audit folded — R-1…R-4 in)** → **Owner approves DRAFT** (no second R
pass) → implement → `cc glm` executes the audit (focus: canonical grouping unambiguous + divergence
visible + every task in exactly one group · no timer/poll/daemon · project-scope inherited · "not
live" label · pure client, zero new gate surface · copilot zero-write · only `app.py` imports
textual) → R re-checks the verdict → Owner finalize.
