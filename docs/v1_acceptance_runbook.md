# Lybra v1.0 acceptance runbook (manual release walkthrough — layer b)

The automated gate (layer a) covers the structural invariants that need no confirm — run it first:

```
PYTHONPATH=<repo> python -m tools.acceptance.v1_acceptance      # expect: ACCEPTANCE: PASS
```

This runbook is the **manual** half: the live release walkthrough whose Owner-confirm gates are, by
design, **out-of-band** (Supervised ★A1 — no script may hold the owner token or self-confirm). Run it
against a **fresh, disposable workspace**; never touch the evidence workspaces (`~/lybra-191b-*`,
`~/lybra-formb-workspace`, `~/lybra-copilot-workspace`).

> **Red line:** cc / any script NEVER holds the owner token and NEVER runs a confirm. Every
> `[Owner OOB]` gate below is performed by the Owner out of band (e.g. the runtime-token helpers
> `~/copilot_publish.sh` / `~/copilot_claim_confirm.sh`, which read the owner token at run time and
> are never given it on the command line). Tokens/keys are fingerprint-only in any output.

## R0 — install
Two audiences, two install paths (lybra is **npm-distributed, not on PyPI**):
- **npm end users (the shipped product path):** `npm install -g lybra` (gate core, zero Python deps)
  → `pip install "textual>=0.50"` to enable the TUI client.
- **Source / dev (from a clone):** `pip install ".[tui]"` → installs Textual (the TUI extra).
- Confirm the gate core still installs with **zero** Python deps (no Textual needed to `lybra serve`).

## R1 — serve (Owner starts the gate)
- `lybra serve --workspace-root <fresh-ws>` → `serve rotate` mints executor / owner / **copilot**
  (scopes []) roles; gate on loopback. `connection.json` is `0600`; tokens shown fingerprint-only.

## R2 — TUI first screen
- `lybra tui --gate-url http://127.0.0.1:7118 --workspace-root <fresh-ws> --project <name> \`
  `--llm-base-url <openai-compat-url> --llm-model <model> --llm-key-env LYBRA_PLANCHAT_LLM_KEY`
- Expect: the AIPOS-210 banner + the AIPOS-208 chat-to-task first screen (copilot enabled because an
  LLM config is supplied; the key is read from the env var, never argv).

## R3 — real-LLM draft + quality check
- Type one natural-language ask → the copilot produces a conformant task card (read-only).
- **Manual quality pass (the §c anchors — Owner judges each):**
  1. card fields are semantically sensible (`task_mode` / `priority` / `output_target` fit the ask);
  2. `title` is on-point and the body is actionable;
  3. `context_bundle` is NOT fabricated — it is an existing bundle or surfaced as `needs_bundle`;
  4. the card passes `draft_publish_dry_run` (structure is guaranteed; judge the semantics);
  5. no hallucinated fields, no secrets in the card.

## R4 — Owner gated publish  **[Owner OOB]**
- Owner proceeds → the card lands under `5_tasks/drafts/` → `draft_publish_dry_run` →
  **Owner confirms with the owner token (OWNER_CONFIRMED), out of band.**
- Verify on disk: the task lands in `5_tasks/queue/pending/`; the publish record has
  `confirmer_role=owner` (+ token_ref/fingerprint). [confirmer = provably the Owner — F-c4 closed.]

## R5 — executor pickup  **[Owner OOB]**
- Executor token: `queue_claim` dry-run on the published task → **Owner confirms the claim out of
  band.** Verify: task moves pending → claimed; claim record `confirmer_role=owner`. (★A1: the
  executor cannot self-confirm.)

## R6 — through to L3 VALID  **[Owner OOB]**
- Executor return dry-run → **Owner confirms the return out of band** → 196a ingestion → Layer-3
  scan reports the task **VALID** (effective truth). (Minimal path acceptable; the executor lifecycle
  is already proven in 191B / AIPOS-202.)

## Acceptance verdict
- **Layer a** (automated) `ACCEPTANCE: PASS` **and**
- **Layer b** (this runbook) all `[Owner OOB]` gates confirmed by the Owner with confirmer=owner on
  disk, and the R3 real-LLM quality anchors judged acceptable.
- Evidence sites untouched; cc never held the owner token; secrets fingerprint-only.
