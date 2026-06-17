# AIPOS-197 Micro-Plan — gate-hardening v0 (confirm scope split + confirmer attribution)

Status: PLAN ONLY. No product code in this step. Implement only after review +
Owner approval, then executor → cc glm audit → Owner approval → finalize.

## Why (evidence)

AIPOS-191B empirically proved **F-candidate-1 FIRED**: a confined full-capability
agent holding the executor MCP token self-issued a `queue_claim` and
**self-confirmed** it (the static public `OWNER_CONFIRMED` literal), because claim
dry-run and confirm gate on the **same scope** and the only confirm-specific gate
is a public constant. And **F-c12**: provenance cannot prove who confirmed.

v0 fix: **(a) scope split** (structural block) + **(c) confirmer attribution**
(provenance). **(b) per-op Owner nonce is explicitly deferred** as later
defense-in-depth.

## Scope (strict)

### (a) Confirm scope split — structural block
- Today: `*_dry_run` and `*_confirm` both call the SAME operation-scope check
  (e.g. `_queue_claim_scope_allowed()` → `_capability_has_scope("queue_claim")` at
  `tools/mcp_server/tools.py`; return mirrors it). The only confirm-specific gate
  is the static literal `OWNER_CONFIRMATION_TOKEN = "OWNER_CONFIRMED"`.
- Target: introduce `OWNER_CONFIRM_SCOPE = "owner_confirm"` and a helper
  `_owner_confirm_scope_allowed()`. Each consequential `*_confirm` additionally
  requires `owner_confirm`. A token that can dry-run (operation scope) but lacks
  `owner_confirm` is structurally denied confirm — independent of whether it knows
  any literal.
- v0 surface (where F-candidate-1 fired): gate **`lybra_queue_claim_confirm`** and
  **`lybra_queue_return_confirm`** with `owner_confirm` (in addition to their
  existing operation-scope check + the static literal, kept as a secondary belt).
- **Decision point for review (do not silently decide):** the brief says "all
  `*_confirm`". `audit_dispatch_confirm` / `audit_verdict_confirm` /
  `intake_submit_confirm` / `owner_decision_record_confirm` / `workspace_init`
  confirm also exist. Gating `audit_verdict_confirm` behind `owner_confirm` would
  force the auditor role to hold `owner_confirm`, collapsing executor≠auditor≠owner
  separation. Proposed v0: gate claim+return confirm now; for each other confirm
  tool, decide per-tool who the legitimate confirmer is (Owner vs role) — default
  keep their current role gate, add `owner_confirm` only where the Owner is the
  intended confirmer. Flag for Owner/auditor sign-off in the slice.

### service-role rotate (`tools/aipos_cli/service_mode.py` ROLE_SPECS)
- Add an `owner` role token holding `owner_confirm` (plus `queue_claim`,
  `queue_return` so it satisfies both the operation-scope and owner_confirm checks
  on confirm). The Owner uses this token out-of-band to confirm.
- `executor` unchanged: `[queue_claim, queue_return]` — **no `owner_confirm`** →
  dry-run only, cannot confirm.
- `auditor` / `owner-dispatch` unchanged in v0.

### Legacy compatibility (explicit, no silent behavior)
- A pre-split merged-scope token (operation scope, no `owner_confirm`) must be
  **explicitly denied** confirm (clear `SCOPE_DENIED` with a re-rotate hint), and
  must **not** be silently granted confirm nor silently fully broken (dry-run still
  works). v0 dogfood uses a fresh `serve rotate` that includes the owner role; the
  product-facing migration note documents re-rotate as the path.
- Keep static `OWNER_CONFIRMED` as a secondary belt; the scope is the real gate.

### (c) Confirmer attribution — provenance
- `tools/mcp_server/http_sse.py` `load_service_role_registry`: add `fingerprint`
  (`secret_fingerprint(token)`) to each registry entry so the confirming token is
  attributable without exposing the raw token.
- Thread the confirming request capability (role + token_ref + fingerprint) from
  the confirm tool (`REQUEST_CAPABILITY`) into the confirm record writer.
- `tools/aipos_cli/board_adapter.py` + `record_writer.py`: confirm records
  (claim/return) gain `confirmer_role`, `confirmer_token_ref`,
  `confirmer_token_fingerprint` (augmenting today's `confirmation_ref:
  owner_policy:<ref>` at board_adapter ~:1400/:1494), plus AIPOS-193 §9
  signature-ready placeholder fields (`gate_signature`, `authority_seal`,
  `signature_key_ref`, `signed_payload_hash`, `signed_at`) left empty.
- Effect: L3 can distinguish an Owner-role confirmation from a non-Owner/agent
  self-confirmation (closes F-c12's blind spot).

## Explicitly NOT doing
- (b) per-op Owner nonce (deferred defense-in-depth).
- No change to the Wall (`confined_worker.py`), AIPOS-196a ingestion, projection
  scan, scratch perms, or Layer 3 scanner semantics.
- Do not break a legitimate owner-scoped token's existing dry-run → confirm flow.
- Red lines unchanged (gate not engine; no runtime/scheduler/etc.).

## Files
- `tools/mcp_server/tools.py` — `OWNER_CONFIRM_SCOPE`, `_owner_confirm_scope_allowed`,
  gate `queue_claim_confirm` + `queue_return_confirm`, pass confirmer capability to
  the confirm path.
- `tools/mcp_server/http_sse.py` — registry entry `fingerprint`.
- `tools/aipos_cli/service_mode.py` — ROLE_SPECS owner role with `owner_confirm`.
- `tools/aipos_cli/board_adapter.py`, `tools/aipos_cli/record_writer.py` — confirmer
  attribution + §9 signature-ready fields in confirm records.

## Tests
- executor-scope token: `queue_claim_dry_run` / `queue_return_dry_run` OK;
  `*_confirm` → SCOPE_DENIED (even with `OWNER_CONFIRMED` supplied).
- owner_confirm-scope token: `*_confirm` OK.
- confirm record carries `confirmer_role` + `confirmer_token_fingerprint`
  (+ §9 ready fields); no raw token in record.
- **concept replay:** an executor token cannot self-confirm a claim it dry-ran —
  F-candidate-1 structurally blocked regardless of literal knowledge.
- regression: existing legitimate Supervised flows (using an owner_confirm-holding
  token) stay green; full `tools/` suite (CLI/Board/MCP) green.

## cc glm audit points
- The executor token **truly cannot confirm** — structural (scope), not reliant on
  keeping any literal secret. Probe with an executor token + correct
  `OWNER_CONFIRMED` → still SCOPE_DENIED.
- Confirmer attribution is complete and non-secret (fingerprint only).
- No Wall / AIPOS-196a / Layer-3 regression.
- Legacy/merged-scope semantics are explicit (denied + re-rotate), not silent.
- The per-tool confirm-gating decision (claim/return vs audit/intake/etc.) is
  recorded and preserves executor≠auditor≠owner separation.

## Loop
micro-plan + governance decision_log entry → review → Owner approval → implement →
full tests → cc glm audit → Owner approval → finalize → fresh `serve rotate` →
then F-c9/F-c10, then a clean instrumented 191B rerun in a NEW workspace.
