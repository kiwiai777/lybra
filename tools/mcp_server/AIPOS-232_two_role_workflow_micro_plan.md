---
id: AIPOS-232
title: Two-role (executor + independent auditor) workflow — formalize on existing primitives
status: draft
authority: NONE
task_class: complex
phase: micro-plan DRAFT (R direction-audit folded; pre Owner approval)
parent: v1.0 — execution-layer workflow formalization
symbols_read_from: main @ ac11f2d (post-AIPOS-231)
r_reinforcements_folded:
  - "R-1 universal instance-level independence (verdict-time, any class) + two-layer seam"
  - "R-2 1-role non-complex-only + complex cannot be silently 1-role'd + explicit audit-skip"
  - "R-3 honest heuristic suggestion (pure function, no new classifier)"
  - "R-4 dogfood structural-independence three-state proof"
---

# AIPOS-232 — 2-role workflow (executor + independent auditor)

> **Nature.** DRAFT only. authority: NONE. Writes no product code, commits nothing.
> Output is this file → R direction-audit (cc folds R's reinforcements in) → Owner approves →
> implement + tests + auto-write cc-glm audit card → `cc glm` → Owner spot-check / R verdict →
> Owner finalize. All symbols below were read from `main` @ `ac11f2d` (post-231); each named
> symbol was verified to exist before being cited (positive-truth, not assumed).

---

## §0 Thesis

Formalize the **execution-layer** workflow choice:

- **1-role** — executor only (no independent audit gate).
- **2-role** — executor + an **independent** auditor, with **executor ≠ auditor structurally
  enforced** (not by convention).

**plan-mode direction-audit + Owner gate is a constant *meta* layer that sits ABOVE every
workflow** — it is NOT a role and is NOT compiled into the workflow graph. Whatever the
execution-layer workflow (1- or 2-role), the meta layer is unchanged: R reviews direction, Owner
holds the gate.

Do **2-role first**, **dogfood-first** (Lybra's own development runs it: `cc` = executor,
`cc glm` = independent auditor, each a registered agent). A complexity *suggestion* (not a
selection) recommends 1- vs 2-role; the Owner always decides.

---

## §1 ★ Grounding — every claim lands on a real symbol (REQUIRED)

The 2-role workflow is **already latent** in the codebase as separate enforced primitives. This
slice's job is to **name and template their composition**, building the *minimum* on top — it does
**not** build an engine and does **not** re-implement independence. Inventory (all verified on
`main` @ `ac11f2d`):

| Primitive | Real symbol(s) | Current behavior |
|---|---|---|
| **Audit dispatch / verdict tools** | `tools/mcp_server/tools.py`: `lybra_audit_dispatch_dry_run` / `_confirm`, `lybra_audit_verdict_dry_run` / `_confirm` (registered in TOOL_HANDLERS) | The 4-call dry-run→confirm audit path already exists end-to-end. |
| **Role scopes on the capability token** | `tools.py:39` `AUDIT_DISPATCH_SCOPE = "audit_dispatch"`, `:40` `AUDIT_VERDICT_SCOPE = "audit_verdict"`; gates `_audit_dispatch_scope_allowed()` (`:343`), `_audit_verdict_scope_allowed()` (`:347`) | The auditor leg requires its own scope; dispatch and verdict are scope-separated already. |
| **executor ≠ auditor (instance level, fail-closed)** | `board_adapter.py:2142-2143` `INDEPENDENCE_FAILED` when `audit_canonical == reviewed_executor_instance`; `:2150-2155` `INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY` fail-closed when either side's identity is registry-unverified; `reviewed_executor_instance` built from `executor_completed_by` (`:2134`); `executor_registry_verified` (`:2149`) | Canonical-instance distinctness is **already** structurally enforced and fail-closed — not a convention. |
| **executor ≠ auditor (role level, complex-class)** | `task_complexity.py:89-92` `assigned_to` must not equal `reviewer` / `audit_by`; `:85-88` `planner_agent` must not equal `reviewer` / `audit_by` (blocking_reasons) | For `task_class: complex`, the executor (`assigned_to`) and auditor (`audit_by`) being distinct is **already** a blocking governance rule. |
| **role / complexity card fields** | `task_loader.py:8,87` `from tools.aipos_cli.task_complexity import complexity_payload`; `task_complexity.py:27` `effective_task_class` (default `simple`), `:32` `complexity_payload` emits `task_class` / `effective_task_class` / `task_class_explicit` / `complexity_note` | The card already carries the complexity signal that 2-role keys off. |
| **queue / readiness state machine** | `board_adapter.py:2117-2122` audit-readiness gate: `queue_state == "claimed"`, `executor_status == "completed"`, `audit_readiness == "ready"`; `:2123-2126` AUDIT_ALREADY_PASSED / AUDIT_ALREADY_DISPATCHED idempotency | The card-flow states that sequence execute→ready→audit already exist. |

**Conclusion the DRAFT asserts:** a 2-role workflow can be **fully assembled from the above with
zero new mechanism** — it is a *template + suggestion* over `task_class`, `assigned_to`/`audit_by`,
the audit_dispatch/verdict scopes, and the existing instance-level independence check. The
implementation surface is **naming + a suggestion helper + dogfood proof**, not a runtime.

---

## §2 2-role workflow definition (pure accountability mechanism, NOT an orchestration engine)

```
executor claims  → executes → returns            (queue_state: claimed, executor_status: completed,
                                                   audit_readiness: ready)
independent auditor (different identity) →        (audit_dispatch scope; INDEPENDENCE_FAILED /
  lybra_audit_dispatch → lybra_audit_verdict        INDEPENDENCE_UNVERIFIABLE fail-closed if same
  → PASS | finding                                  identity / unverifiable)
PASS → Owner finalize                             (Owner gate — meta layer, unchanged)
```

- **executor ≠ auditor** is enforced by the *existing* gate, never by a new convention.
- The **meta layer** (plan-mode direction-audit + Owner gate) sits above this and is not encoded as
  a role or a workflow node.
- 2-role = **the discipline/template for how a card flows through the two roles**, materialized as
  card fields + the existing dispatch/verdict gates. Nothing in this slice *runs* the agents.

### R-1 — where 2-role's independence guarantee actually lives (universal vs class-scoped)

The two independence layers are **not** interchangeable, and 2-role's real guarantee must rest on
the **universal** one:

- **Backstop layer (universal, the real guarantee):** the **instance-level, verdict-time** check at
  `board_adapter.py:2143` (`INDEPENDENCE_FAILED` when `audit_canonical == reviewed_executor_instance`)
  and `:2150` (`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY` fail-closed) **runs on every `audit_verdict`
  regardless of `task_class`**. This is what makes 2-role safe even when chosen on a **non-complex**
  task — the role-level rule would not fire there.
- **Declaration layer (class-scoped, an extra card check):** the role-level rule at
  `task_complexity.py:87-92` (`assigned_to ≠ audit_by`, `planner_agent ≠ reviewer`/`audit_by`)
  fires **only for `task_class: complex`**. It validates the card's *declared* assignment up front.
- **Seam (no gap):** the declaration layer asserts `assigned_to ≠ audit_by` on the card; the
  backstop layer asserts the *real* canonical instances differ at verdict time. **The backstop
  cannot be bypassed by the declaration** — even a card that declares distinct roles but is then
  executed+audited by the same canonical instance fails closed at `board_adapter.py:2143/2150`.
  2-role therefore inherits a guarantee that holds for **any** class, with complex-class adding an
  earlier declarative check.

---

## §3 ★ TOP RED LINE — gate-not-engine (the easiest "workflow" trap)

**A "workflow" must NOT smuggle in any runtime / scheduler / launcher / polling / heartbeat /
driver / daemon / loop.** Lybra is a **gate, not an engine**: it records and gates the
`execute → audit → finalize` card transitions; it **never drives, schedules, or runs an agent**.
The agents (`cc`, `cc glm`) act on their own; Lybra only gates their actions.

- The 2-role "workflow" is the **template for how a card flows through roles**, NOT an orchestration
  runtime.
- cc **must not** introduce any `scheduler` / `poller` / `while True` run-loop / `daemon` /
  `threading.Timer` / `asyncio` event loop / heartbeat.
- **Grounding for the baseline:** even the existing `planner_loop_mvp.py` is, by its own docstring,
  a *"single-step coordinator **preview**… writes nothing"* — the only "loop" in the tree is
  already a preview, not a runtime. 2-role must hold that same line.
- **This is the #1 audit focus of the slice.** Verify proves the absence of any new runtime
  primitive by grep witness (§6).

---

## §4 Complexity suggestion (recommend, never auto-select)

Use the **existing** complexity metadata (`task_class` via `effective_task_class` /
`complexity_payload`) to *suggest* 1- vs 2-role:

- `simple` (e.g. doc research, server config) → **suggest 1-role** (no independent audit needed).
- `complex` (ops, design docs, code) → **suggest 2-role**.

### R-2 — 1-role is non-complex-only; complex cannot be silently 1-role'd; audit-skip is explicit

The complexity model already closes the dangerous hole:

- **complex-class structurally requires `audit_by`** (`task_complexity.py:83-84` — missing `audit_by`
  is a blocking_reason). So a **complex** task **cannot be silently turned into 1-role** — dropping
  the auditor leg makes the card fail validation.
- **1-role is legal only for non-complex-class** tasks.
- Choosing 1-role (i.e. forgoing the independent audit) must be an **explicit, recorded Owner
  decision — never a silent default.** The suggestion must **loudly recommend 2-role for complex
  work** so a complex task is never accidentally under-audited.

### R-3 — the suggestion is an honest heuristic, not a classifier

- It reads the **existing** `task_class` signal (`complex → suggest 2-role`; `simple → suggest
  1-role`).
- **Honesty:** this is a heuristic *hint derived from an existing signal*. It **approximates**
  "doc/config vs ops/design/code" but is **not precise** — `task_class` is a *complexity tier*, not
  a *task type*. The DRAFT states this plainly rather than implying the mapping is exact.
- The Owner **always decides**; there is **no auto-select** (preserves the Owner gate).
- The suggestion helper is a **pure function** — no background work, no polling, no stored state
  (reinforces §3 gate-not-engine).
- It does **not** add a new complexity/classification model (out of scope, §7).

**Honesty (summary):** suggestion, not enforcement; no auto-select; reads the field that already
exists; adds no new complexity model.

---

## §5 dogfood-first

Lybra's own development runs 2-role as the **first real self-bootstrap test**:

- `cc` = executor (registered agent), `cc glm` = independent auditor (registered agent).
- Identities are **structurally independent**: the gate enforces `executor ≠ auditor`
  (`board_adapter.py:2142`); it is emphatically **not** "one agent wearing two hats" — if the two
  resolve to the same canonical instance, the independence check **fails closed**
  (`INDEPENDENCE_FAILED` / `INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`).

---

## §6 verify = positive truth (each asserts content/identity/count — never a proxy a default can fake)

1. **executor ≠ auditor truly enforced** — an executor identity attempting `audit_verdict` on its
   own task is **rejected** (`INDEPENDENCE_FAILED`); a registry-unverified pairing **fails closed**
   (`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`). Assert the *denial reason string*, not merely a
   non-PASS.
2. **suggestion recommends, does not select** — assert the suggestion helper returns a recommended
   role-count **and that nothing is auto-applied** (no card mutation, no role auto-assigned); assert
   the field name says `suggested_*`, and that an opposite Owner choice is honored. Assert the
   helper is a pure function (same input → same output, no side effect).
3. **complex cannot be silently 1-role'd (R-2)** — a `task_class: complex` card with **no
   `audit_by`** yields a **blocking_reason** (`task_complexity.py:83-84`); assert the blocking
   string, proving a complex task cannot drop to 1-role unnoticed.
4. **gate-not-engine holds** — grep witness asserts **no** new `scheduler` / `poller` /
   `daemon` / `while True` / `Timer` / `asyncio` / `heartbeat` in the slice's diff (positive
   absence assertion against the actual diff, not a wish).
5. **dogfood = structural independence, three states (R-4)** — prove independence comes from
   **registry identity**, not from distinct labels:
   - **(neg) same canonical instance** executes and audits → **`INDEPENDENCE_FAILED`** (assert the
     reason string);
   - **(fail-closed) registry unverifiable** → **`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`** (assert
     the reason string);
   - **(pos) `cc` / `cc glm` resolve to distinct canonical instances** → dispatch proceeds, card
     reaches `audit_readiness: ready`, verdict renders, Owner finalizes. Assert the two canonical
     instance ids and their inequality.
6. **★A1 byte-unchanged** — `_capability_has_scope` (`tools.py`) and the operation-gate path are
   byte-unchanged; executor/copilot tokens still structurally cannot `owner_confirm`/`draft_publish`.
7. **three lanes green + ACCEPTANCE; zero-dep** — BARE / SYSTEM / TUI suites pass;
   `python3 -m tools.acceptance.v1_acceptance` PASS; stdlib-only, no new dependency.

---

## §7 Out of scope

- 3-role / planner role (v1.1).
- `/agents` monitoring panel (a separate v1.0 item, its own slice).
- auto-relay closed-loop pipe (v1.2) — would be an engine; explicitly excluded here.
- Upgrading the complexity *model* itself — reuse the existing `task_class` field as-is.

---

## Red lines / deliverable

- `executor ≠ auditor` gate **truly** enforced (reuse `board_adapter.py:2142` +
  `task_complexity.py:89-92`; assert denial strings).
- plan-mode / Owner is the **meta layer**, not a role; not compiled into the workflow.
- copilot scopes `[]` / read-only / zero-write **unchanged**; ★A1 **byte-unchanged**.
- suggestion **recommends, never auto-selects**.
- **gate-not-engine**: NO new runtime / scheduler / poller / daemon / loop (grep-witnessed).
- two-root model unchanged; **zero-dep / stdlib-only**; `confined_worker` untouched.
- DRAFT writes no code and commits nothing; evidence zero-touch; cc holds no owner token and never
  confirms.

**Flow:** DRAFT **(R direction-audit folded — R-1…R-4 in)** → **Owner approves DRAFT** (no second
R pass — direction-audit complete) → implement → `cc glm` executes the audit (focus: no
engine/scheduler · 2-role independence rests on the universal instance-level check · 1-role only on
non-complex + audit-skip explicit/recorded · suggestion honest heuristic, not auto-select · dogfood
three-state structural independence · ★A1 byte-unchanged) → R re-checks the `cc glm` verdict →
Owner finalize.
