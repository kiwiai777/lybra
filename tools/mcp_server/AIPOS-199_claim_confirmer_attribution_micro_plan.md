# AIPOS-199 Micro-Plan — Claim-confirm confirmer attribution (RF-5 / 197 follow-up)

Status: micro-plan (awaiting review) — not implemented
Date: 2026-06-19
Origin: AIPOS-191B clean rerun RF-5 (F-c12 closed on return, OPEN on claim)
Loop: implement → full tests → cc glm audit → Owner approve → finalize

## 0. Problem (empirical, from the 191B rerun)

After an Owner-token confirm over serve-http, the **return** record correctly carried
`confirmer_role: owner` / `confirmer_token_ref: svc-owner` /
`confirmer_token_fingerprint: sha256:823cfb07c51a`, but the **claim** record left those three
fields (and the AIPOS-193 §9 placeholders) empty — `confirmation_ref` was the generic
`owner_policy:...`. So AIPOS-197's confirmer attribution works on the return path but NOT on
the claim path in the live HTTP/SSE confirm flow, even though the 197 unit test (direct
function call) passes. F-c12 is therefore closed on return, OPEN on claim.

## 1. Scope

- `tools/mcp_server/tools.py` — the `lybra_queue_claim_confirm` path.
- `tools/aipos_cli/board_adapter.py` — `_mcp_claim_record_plan` / `execute_dry_run` claim branch.
- `tools/aipos_cli/record_writer.py` — `build_mcp_claim_record_markdown` (only if the claim
  builder is not receiving/emitting the confirmer; align it to the return builder).
- `tools/mcp_server/http_sse.py` — only if the capability→confirmer threading differs between
  the claim and return confirm requests.

NOT in scope: the scope-split structural denial (★A1, already proven); the return path (works);
196a/Wall/Layer-3; per-op nonce (deferred §9).

## 2. Investigation (first step of implementation)

Diff the claim-confirm vs return-confirm confirmer threading end-to-end:
- Where `_confirmer_attribution()` is read for return vs claim (request-time vs confirm-time).
- Whether the claim record is rebuilt at confirm from the dry-run plan (capturing the
  executor/empty confirmer) while the return record re-captures the confirmer at confirm time.
- Whether `_mcp_claim_record_plan` receives `confirmer` and forwards it to the claim record
  builder the way `_mcp_return_record_plan` does.
Root-cause, then mirror the working return mechanism into the claim path. Keep the two paths
symmetric.

## 3. Fix

- Ensure the **claim** confirm record is stamped, at confirm time, with the confirmer captured
  from the confirming request's owner capability — identical to the return path:
  `confirmer_role`, `confirmer_token_ref`, non-secret `confirmer_token_fingerprint`, plus the
  AIPOS-193 §9 signature-ready placeholders. Raw tokens never in records/logs/reports.
- No change to the structural scope gate (executor still cannot confirm).

## 4. Tests (MUST include an end-to-end test, not only a direct-function unit test)

1. **End-to-end (make-or-break):** start serve-http with an owner role token; perform a real
   `queue_claim` dry-run then `queue_claim_confirm` over the HTTP/SSE transport with the owner
   Bearer; assert the WRITTEN claim record on disk has `confirmer_role: owner` +
   `confirmer_token_ref` + non-secret `confirmer_token_fingerprint`. (Mirror the same assertion
   for `queue_return_confirm` to lock in the return path as a regression guard.)
2. Unit: claim record builder emits confirmer fields when given a confirmer (parity with return).
3. Negative: executor-token claim confirm still → SCOPE_DENIED (★A1 regression).
4. No-leak: no raw token in the record/report/log (fingerprint only).
5. Full `tools/` suite green.

## 5. cc glm audit points

- The end-to-end test actually exercises the serve-http confirm path (a real spawn+probe, not
  a mocked capability) and asserts the on-disk claim record confirmer is filled.
- Symmetry: claim and return confirmer attribution are now identical; ★A1 scope denial intact;
  no raw secret leak; no Wall/196a/Layer-3/scope-gate change.

## 6. Loop
micro-plan → review → approve → implement (investigate → fix → e2e test) → full tests →
cc glm audit → Owner approve → finalize (product commit + governance docs).
