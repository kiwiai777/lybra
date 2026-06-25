# AIPOS-219 — Zero-dependency identity & independence policy (unblocks AIPOS-218 WS3) — DESIGN micro-plan

**status: draft**
**authority: NONE**

DESIGN DRAFT only. No product code / tests / docs changed by this slice. This draft investigates and
specifies the zero-dependency policy for the **profile-coupled** invariants (identity provenance +
executor≠auditor independence) that paused AIPOS-218 at WS3. Owner 复核 of §6 + §7 rulings precedes any
implementation. After approval this returns to AIPOS-218 (WS3 → WS8) carrying the ruled policy.

---

## 0. Why this slice exists (restated)

AIPOS-218's read-point audit found that agent **profiles** — read from PyYAML-only
sequences-of-mappings docs — participate in **identity provenance** and **executor≠auditor
independence** decisions **on the gate path**. The gate core is claimed zero-Python-dependency, but
profiles are read via PyYAML; without PyYAML a bare-python npm user gets **empty** profiles. The risk
is **silent weakening**: a legacy alias resolves to its own raw value (mis-attributed as canonical),
and independence comparisons run against a possibly mis-attributed string. The Owner red line: the
independence invariant must **never be silently weakened**, and recorded canonical identity provenance
must **never be mis-attributed** — fail-closed (raise / BLOCK) instead.

The AIPOS-218 rev.2 WS3 refinement (sequences-of-mapping **read = loud warn + empty**, never silent)
is correct for *availability/runtime* fields but is **insufficient on its own** for the
identity/independence invariants: a "warn + empty profiles" state still *produces a decision*
(unregistered→raw canonical; independence compared against that raw value), and that decision can be
wrong. This slice supplies the missing policy: **which decision points must additionally raise/BLOCK
when the empty-profiles state would otherwise mis-attribute or under-enforce.**

---

## 1. Complete participation inventory (verified file:line)

Source of truth: read of `tools/aipos_cli/agent_profiles.py`, `tools/mcp_server/tools.py`,
`tools/aipos_cli/board_adapter.py` (current `main`). The supplied evidence was **largely correct**;
two corrections are flagged inline (★C1, ★C2).

### 1a. Identity resolution primitive

`agent_profiles.py:345 resolve_instance_id(instance_id, profiles)` — the single resolution kernel.
Four outcomes:
- input ∈ `profiles["instance_index"]` → `resolution="canonical"`, canonical = input (`:349-350`).
- input ∈ `profiles["legacy_instance_index"]` with exactly one match → `resolution="legacy"`,
  canonical = the true canonical (`:351-354`).
- > 1 legacy match → `resolution="ambiguous"`, canonical = None (`:355-356`).
- else → `resolution="unregistered"`, **canonical = raw input** (`:357`).

**Empty-profiles behavior (the core hazard):**
- A **canonical input** still returns *its own value* as canonical; only the `resolution` label
  changes from `"canonical"` to `"unregistered"`. → **value correct, label degraded.**
- A **legacy alias** input returns `unregistered` / **raw alias as canonical** — i.e. the alias is
  recorded as if it were itself canonical. → **mis-attribution.** (Matches supplied evidence.)
- Ambiguous can never occur with empty profiles (needs ≥2 legacy entries).

### 1b. Gate entry — `tools.py` (claim / return / audit)

`tools.py:350 _resolve_claim_instance` → `load_agent_profiles` + `resolve_instance_id` →
`canonical_agent_instance`. Called at `:927, :999, :1154, :1232, :1345` (claim dry/confirm, return
dry/confirm, audit-arg validation). Each gate entry then enforces, in order:
1. `resolution == "ambiguous"` → BLOCK (`AMBIGUOUS_LEGACY_INSTANCE`) — `:930, :1002, :1235, :1348`.
2. `not canonical_agent_instance` → BLOCK (`INSTANCE_REQUIRED`) — `:936, :1163, :1354`.
3. **`actor != canonical_agent_instance` → BLOCK (`INSTANCE_MISMATCH`)** — `:942, :1169, :1360`.

`canonical_agent_instance` is then **recorded** in claim/return/audit metadata and the verdict record:
`_claim_metadata:391`, `_return_metadata:411`, `_decorate_*:431/:516/:599`,
confirm responses `:1057/:1290/:1456/:1553`, and board records via
`actor=canonical_agent_instance` (`:952/:1179`). So **identity provenance depends on profiles.**

★C1 — **correction to supplied evidence.** The gate does **not** silently pass a legacy alias through
as its own canonical *when profiles are present* — with profiles, a legacy alias resolves to the
*true* canonical at `:353`, which then **fails** the `actor != canonical` guard (`:942` etc.) unless
the caller also set `actor` to the true canonical. The genuine mis-attribution vector is narrower and
specific to **empty profiles**: a caller passing `agent_instance = <legacy alias>` **and**
`actor = <same legacy alias>` passes guards 1–3 (unregistered→raw, actor==canonical==alias) and the
**alias is recorded as canonical_agent_instance**. That is the silent mis-attribution this slice must
close. (A canonical input with empty profiles records the correct value — only the label degrades.)

### 1c. Independence enforcement — `board_adapter.py` (the real enforcement site)

★C2 — **correction to supplied evidence.** `evaluate_instance_independence`
(`agent_profiles.py:377`) and `INDEPENDENCE_DIMENSIONS` (`:16`) are **defined and unit-tested**
(`tools/aipos_cli/tests/test_instance_identity.py`) but **NOT wired into the gate** (grep over
non-test `tools/` shows no caller). The gate enforces executor≠auditor by a **direct canonical
string comparison**, in two places:

- **Audit dispatch** (`board_adapter.py:2030-2041`):
  `reviewed_executor_instance = source_metadata["executor_completed_by"] or agent_instance or
  claimed_by` (`:2030`); the auditor's `audit_agent_instance` is resolved via `resolve_instance_id`
  (`:2034`); BLOCK if `audit_resolved` ambiguous/empty (`:2036-2037`), else BLOCK if
  `audit_canonical == reviewed_executor_instance` (`INDEPENDENCE_FAILED`, `:2038-2039`).
- **Audit verdict** (`board_adapter.py:2354-2358`):
  `reviewed_executor_instance = audit_metadata["reviewed_executor_instance"] or
  reviewed_metadata["executor_completed_by"]` (`:2354`); BLOCK if
  `canonical_agent_instance == reviewed_executor_instance` (`INDEPENDENCE_FAILED`, `:2357-2358`).

The **distinctness comparison is between two strings**: the auditor's resolved canonical and a
**previously-stored** `reviewed_executor_instance` string. That stored string was written at return
time: `board_adapter.py:1678 updated_metadata["executor_completed_by"] = canonical_agent_instance or
actor`. **So the independence check is only as trustworthy as the canonical recorded at executor
return time.** If the executor returned under empty profiles using a legacy alias, the recorded
`executor_completed_by` is the **alias** (mis-attributed). A later auditor presenting the *true
canonical for the same physical agent* would compare `true_canonical != alias` → **falsely PASS the
independence check** (same agent, two different strings → looks distinct). This is the concrete
silent-weakening path and the reason "warn+empty" alone is insufficient.

### 1d. Other profile reads (confirm NOT identity/independence invariants)

`board_adapter.py:161/397/410/440/461/1223` load profiles for **actor-alias task matching**
(`actor_matches_task_actor`, `:10`) and runtime/availability surfacing — these gate *who may claim a
task* via alias equivalence, not provenance recording or independence. With empty profiles,
`actor_matches_task_actor` falls back to **exact string match** (`agent_profiles.py:475`), which is
*stricter* (never broader) — so empty profiles cannot **widen** access here. (Verify in WS-test that
the only effect is stricter matching, never a false match.)

### 1e. Confirmed NOT profile-coupled (the boundary holds)

- **Scope / capability** — `_*_scope_allowed` gates on `capability_token` scopes (tools.py
  `:1633/:1650/:1668/:1788`…), **not profiles**. ✓
- **★A1 confirm≠execute split + confirmer attribution** — Owner-only `owner_confirm` scope
  (`:971`) + `owner_confirmation_token == OWNER_CONFIRMED` (`:982`); confirmer attribution is
  `role/token_ref/fingerprint` (`board_adapter.py:1289`, v1_disclosure.md row 1), **not profiles**. ✓
- **Service-role registry / reachability** — token-scope based, **not profiles**. ✓

**Inventory conclusion — no third blind spot.** Exactly **two** profile-coupled invariants reach the
gate: (A) recorded `canonical_agent_instance` **provenance** (tools.py entry + board records), and
(B) **executor≠auditor independence** (board_adapter dispatch `:2038` + verdict `:2357`). Both reduce
to `resolve_instance_id` + a recorded canonical string. Scope/★A1/confirmer are token-based and
profile-independent.

---

## 2. Two scoping questions (for Owner ruling)

Cite: AIPOS-147 instance-identity work; v0.2.0 release notes "custom agent identities use **opaque
IDs** with explicit provenance" (`docs/distribution/github_release_v0.2.0.md:20,41`;
`docs/start.html:180`); decision log (`2_projects/lybra/decision_log.md`).

**Q1 — Is `agent_instance` input canonical-only for v1.0, or must legacy aliases be supported at the
gate?**
Recommendation: **canonical-only at the gate for v1.0.** Rationale: the gate already *requires*
`actor == canonical_agent_instance` (tools.py:942 etc.), so a legacy alias only succeeds today in the
narrow empty-profiles mis-attribution case — i.e. legacy-alias-at-gate is **already effectively
unsupported** when profiles load, and **only "works" in the broken (mis-attributing) case** when they
don't. Making canonical-only explicit costs almost nothing and removes the hazard. Legacy-alias
resolution remains available off the gate path (CLI alias matching, custom-profile features) where
PyYAML is present. **Owner to confirm.**

**Q2 — Is audit-verdict / executor≠auditor independence a v1.0 core invariant, or v1.1?**
Recommendation: **v1.0 core invariant.** It is already enforced in product code
(`board_adapter.py:2038, :2357`) and is named in the tool contract
(`tools.py:1939` "distinct from reviewed_executor_instance"). It must therefore be **correct or
fail-closed** on bare python, not silently weakened. (Heterogeneous dual-harness *mutual* audit stays
deferred per v1_disclosure.md row 8 — that is a different claim; single-loop executor≠auditor
distinctness is in-scope.) **Owner to confirm.**

These two answers set the zero-dep boundary: **canonical-only + fail-closed independence** ⇒ the gate
needs PyYAML *only* for legacy-alias translation and custom-profile instances, both of which can
raise/BLOCK when absent without breaking the canonical-input core loop.

---

## 3. Zero-dep identity & independence policy (the design)

Aligned to the Owner steer. Decision points and their no-PyYAML behavior:

### P1 — Canonical input, profiles empty (no PyYAML) → **zero-dep CORRECT**
A canonical `agent_instance` resolves to itself (`resolve_instance_id:357` returns `canonical = raw`),
`actor == canonical` passes, and the **recorded provenance is correct**. The only loss is the
`resolution` *label* ("unregistered" vs "canonical"). **Policy:** accept; the recorded
`canonical_agent_instance` is correct. (Optionally record a `resolution_provenance` marker — see P5 —
so downstream can tell "verified-against-registry" from "accepted-as-canonical-without-registry".)

### P2 — Legacy alias input, profiles empty (no PyYAML) → **RAISE / BLOCK (never record raw)**
Today: alias → `unregistered`/raw → recorded as canonical (mis-attribution). **Policy:** when an input
is **not** a confirmed canonical and **cannot be confirmed** because the registry is unavailable
(`yaml is None` AND profiles degraded-empty), the gate must **BLOCK** with a clear code
(`IDENTITY_UNRESOLVABLE_NO_REGISTRY`) rather than record a raw/legacy value as canonical. Decision
point: `tools.py:_resolve_claim_instance:350` (or its callers `:927/:999/:1154/:1232/:1345`). Combined
with Q1 (canonical-only), a legacy alias at the gate is rejected anyway — but the **registry-absent**
flavor must be a distinct, honest message ("legacy alias needs PyYAML registry") so the user knows the
remedy is installing PyYAML, not picking a different value.

### P3 — Independence unresolvable → **FAIL-CLOSED BLOCK (never false PASS)**
The independence check (`board_adapter.py:2038, :2357`) must BLOCK when distinctness **cannot be
confidently established**. Specifically BLOCK when **either** side's identity is registry-unverified
under empty profiles **and** the stored `reviewed_executor_instance` (or the auditor input) is not a
confirmed canonical:
- already BLOCKs if auditor resolution is ambiguous/empty (`:2036`) — keep.
- **add:** BLOCK if `yaml is None`/profiles-degraded **and** `reviewed_executor_instance` was recorded
  without registry verification (see P5 marker) — because a string-inequality between a true canonical
  and a mis-attributed alias is **not** proof of distinctness. New code, e.g.
  `INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`. Never downgrade to PASS on absence of evidence.

### P4 — Independence with both sides confirmed-canonical → **zero-dep CORRECT**
If both the auditor and the recorded executor are confirmed-canonical opaque IDs (the v1.0
canonical-only norm, Q1), the string comparison at `:2038/:2357` is **sound without profiles** (two
distinct opaque canonicals are genuinely distinct agents; equal means same). **Policy:** accept —
this is the common path and stays zero-dep.

### P5 — Provenance marker (enabler for P1/P3, recommended)
Record alongside `canonical_agent_instance` a small `identity_provenance` field, e.g.
`{"resolution": "<canonical|unregistered>", "registry_available": <bool>}`, written at claim/return
(`_claim_metadata`/`_return_metadata`, and persisted into `executor_completed_by` siblings). This lets
the verdict-time independence check (P3) distinguish "compared two registry-verified canonicals"
(sound) from "compared an unverified string" (must BLOCK). Without this marker, the verdict path
cannot know whether the stored executor string was registry-verified. **This is the key new data
element; Owner to confirm it is acceptable to add a frontmatter field (FLAT scalar/bounded-map —
fits the AIPOS-218 frozen contract).**

**Net effect:** in *all* cases the independence invariant is preserved — correct when verifiable
(P1/P4), fail-closed BLOCK when not (P2/P3) — and no raw/legacy value is ever recorded as canonical.

---

## 4. Redrawn zero-dep boundary + WS8 wording

### Truly zero-dep core loop (correct on bare python, no PyYAML)
- `lybra init`; task/record I/O; FLAT + bounded-nested (≤2) frontmatter (AIPOS-218 WS1a/WS1b);
  L3 authority scan; scope / ★A1 confirm / confirmer attribution (token-based);
- **canonical-input** claim / return / audit dispatch / audit verdict, including
  **executor≠auditor independence between two confirmed-canonical opaque IDs** (P1, P4).

### Requires PyYAML (fails loudly — raise/BLOCK, never silent)
- **legacy-alias → canonical translation** at the gate (P2): BLOCK `…_NO_REGISTRY`;
- **independence when identity is registry-unverified** (P3): fail-closed BLOCK;
- custom-profile instances / registry; orchestration previews; context-pack bundles (per AIPOS-218
  WS3 — loud warn+empty for non-invariant reads, raise on writes).

### WS8 wording (README:37 + docs/v1_disclosure.md) — to draft in AIPOS-218 WS8
- README:37 ("gate core … zero Python runtime dependencies"): qualify to —
  *"The gate core (init, task/record I/O, and claim/return/audit with **canonical opaque
  agent_instance IDs**) is zero-dependency and correct on bare python. **Legacy-alias resolution and
  custom-profile registries require PyYAML**; without it the gate **fails closed (blocks) rather than
  mis-attributing identity or weakening auditor independence** — it never silently degrades an
  accountability decision."*
- `docs/v1_disclosure.md`: **add a new row** (the file currently has **no** identity/PyYAML row —
  rows 1–9 do not cover this) recording: *legacy-alias / custom-profile identity resolution is
  PyYAML-dependent; the canonical-input core is zero-dep; identity/independence is **fail-closed**
  when the registry is unavailable.* Mark honestly as **structure-held** (the BLOCK is structural).
  Keep **claims ⊆ disclosure** so the AIPOS-213 README↔disclosure guard still passes (the README
  qualification must be backed by this disclosure row).

---

## 5. Test strategy (mirror the AIPOS-218 `sys.meta_path` block-finder)

With `yaml` blocked via the `sys.meta_path` finder (same pattern as AIPOS-218 WS5/WS6,
`v1_acceptance.py:34-52`), prove on **bare python**:

1. **P1 canonical-correct:** claim/return/audit with a canonical opaque `agent_instance` (==actor)
   succeeds; recorded `canonical_agent_instance` equals the input; `identity_provenance.resolution`
   reflects unregistered-but-accepted; **no warning that implies a wrong value.**
2. **P2 legacy fail-closed:** claim/return with a known legacy alias (from
   `dev_claude_runtime_profiles.md`, e.g. `dev.claude.cc.local`) under blocked yaml → **BLOCK**
   `…_NO_REGISTRY`; assert **no record is written** with the alias as canonical.
3. **P3 independence fail-closed:** construct an audit-verdict scenario where the stored
   `reviewed_executor_instance` was recorded registry-unverified and the auditor presents a different
   string → assert **BLOCK** (`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`), **never PASS**. Negative
   control: the legacy-alias-vs-true-canonical "looks distinct" case must **not** yield a false PASS.
4. **P4 independence-correct:** two distinct confirmed-canonical opaque IDs → audit verdict proceeds
   (distinct); same canonical on both sides → BLOCK `INDEPENDENCE_FAILED` (existing behavior, still
   correct under blocked yaml).
5. **Access not widened:** under empty profiles, `actor_matches_task_actor` only ever **narrows** to
   exact match — assert no alias that matched with profiles still matches without them (no false claim
   authorization).
6. **PyYAML-present parity:** with yaml available, all of the above behave identically to today for
   canonical inputs, and legacy aliases resolve correctly (no regression in the dev runner).

Wire (3) and (4) into the AIPOS-218 WS6 acceptance probe so "R0 zero-dep" permanently means
**identity/independence is correct-or-fail-closed**, never silent.

---

## 6. Open items for Owner ruling (复核)

1. **Q1** — canonical-only `agent_instance` at the gate for v1.0? (Recommended: yes.)
2. **Q2** — executor≠auditor independence a v1.0 core invariant? (Recommended: yes.)
3. **P5** — OK to add an `identity_provenance` frontmatter field (FLAT/bounded-map, within the
   AIPOS-218 frozen contract) so the verdict path can fail-closed correctly?
4. **P2/P3 verdicts** — confirm **BLOCK** (not warn+proceed) is the right failure mode when the
   registry is unavailable and identity/independence cannot be verified — i.e. fail-closed over
   availability, even though it means a bare-python user **must install PyYAML to use legacy aliases
   or to audit work whose executor identity was registry-unverified.**
5. **WS8** — confirm the README qualification + new disclosure row wording (claims ⊆ disclosure;
   AIPOS-213 guard must stay green).
6. **★C1/★C2 corrections** — confirm acceptance of the two evidence corrections (mis-attribution
   vector is empty-profiles-specific; `evaluate_instance_independence` is unwired and the live check
   is a direct string compare). Decide whether to **wire `evaluate_instance_independence`** as the
   single enforcement point (cleaner, supports richer dimensions) or keep the two inline compares and
   add the fail-closed guards there (smaller diff). Recommended: keep inline for v1.0 (smaller, lower
   risk), defer consolidation.

---

## 6b. P2 mechanism correction (Owner-ruled during AIPOS-218 WS3 implementation)

P2 as originally written ("BLOCK a legacy alias at claim time") is **not implementable on bare
python**: without the registry you cannot distinguish a legacy alias from a canonical input — both
resolve to `unregistered`/raw (the registry is exactly what tells them apart). A literal P2 would
either block **every** bare-python claim (contradicting P1) or none. **Ruling: the protection moves
from claim-time detection to label + fail-closed:**
- **Claim/return:** accept the input as identity, and record a **FLAT** `identity_provenance`
  marker `{resolution: <label>, registry_available: <bool>}` where `registry_available` is true iff the
  profile registry was actually loaded (i.e. PyYAML available). Honestly labels the identity as
  registry-verified or not. (FLAT so it is readable on bare python — fail-closed must be decidable
  without PyYAML.)
- **Audit independence (P3):** **fail-closed BLOCK whenever EITHER side has
  `registry_available=false`** (auditor input or the stored reviewed-executor provenance) — distinctness
  cannot be proven without the registry, so never PASS.
The goal is unchanged (no mis-attribution; independence never falsely PASSes). `registry_available`
signal = `agent_profiles.yaml is not None` (all profile sources require PyYAML). Mandatory tests:
**negative control** (a legacy/registry-unverified executor recorded on bare python → audit verdict
**BLOCKs**, never false PASS) + **positive control** (PyYAML present → normal).

## 7. Sequencing / non-goals

this DRAFT (AIPOS-219) → Owner 复核 (§6) → approve → **return to AIPOS-218**: fold the ruled policy
into **WS3** (identity/independence fail-closed guards: P2 at `_resolve_claim_instance`, P3 at
`board_adapter.py:2038/:2357`, P5 marker) and **WS8** (README + disclosure wording) → implement →
cc glm audit (focus: no silent weakening, no mis-attribution, canonical-input zero-dep correct,
legacy/unverifiable fail-closed) → Owner spot-check → finalize.

NOT here: implementation; wiring `evaluate_instance_independence` (deferred unless Owner picks the
consolidation option); heterogeneous dual-harness mutual audit (v1_disclosure.md row 8, stays
deferred); npm publish; R2/R5/206b.
