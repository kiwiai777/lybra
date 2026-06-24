# AIPOS-214 — v1.0 release gate: execution report (live run on the npm install product)

- Date: 2026-06-24
- Kind: governed live run (no product code change). Run plan: `AIPOS-214_v1_release_gate_run_plan.md`
  (Owner 复核 PASS; O1=(a) post-gate reconcile, O3=bounded cc proof + Owner eyeball, O2=env key).
- **Verdict: PASS (R0–R6 all green on the install product).** One install-path finding (F-rg-1) +
  one doc-drift reconcile (O1) triaged, neither hard-fixed in-gate.
- Red lines held: cc never held the owner token, never ran a confirm, never touched publish creds
  (R4/R5/R6 confirms all Owner OOB). Secrets fingerprint-only. Evidence sites untouched.

## Environment
- Toolchain: node v24.15.0, python 3.13.11, npm 11.13.0.
- Disposable npm prefix: `/tmp/lybra-rg-prefix` (no real global mutation). Fresh workspace:
  `~/lybra-release-gate-workspace`. LLM: `https://xchai.xyz/v1` · `claude-sonnet-4-6`, key via
  `LYBRA_PLANCHAT_LLM_KEY` (fp `sha256:5c75db387b52`, 0600 file, never in argv/logs).
- Gate served from the installed product (`/tmp/lybra-rg-prefix/bin/lybra serve`); on loopback 7118.

## Step results (each with on-disk re-check)
**R0 — install (PASS).** `npm pack` → `lybra-0.2.0.tgz`, 279 files, manifest **zero leaks** (ships
`tools/`+README+`bin/`; no task_cards/`._*`/`__pycache__`/tests/connection.json). Installed the
tarball into the disposable prefix; `lybra` resolves from the tarball (symlink → unpacked product).
Gate core **zero-dep**: system python3 has **no textual**, yet gate modules import OK (`textual` not
in `sys.modules`) → `lybra serve` runs without textual. The **TUI extra** path was also proven: a
disposable venv `pip install "textual>=0.50"` (got textual 8.2.7) → `lybra tui` resolves through
`LYBRA_PYTHON` (the npm-user "install textual separately" path, end to end).

**R1 — serve (PASS).** `serve rotate` minted 5 roles; **owner carries `draft_publish`** (AIPOS-207
present in the product), copilot `scopes: []`. `connection.json` perm **0600**; status prints "Raw
tokens are not printed". Gate listening on 7118; MCP returns **401** unauthenticated (auth enforced).

**R2 — first screen (PASS, bounded per O3).** `presentation.banner()` renders from the product —
10 lines all visual-width 65 (aligned), version `v0.2.0`, llama mark + identity panel; narrow →
`LYBRA`. `color_enabled` honours NO_COLOR / non-TTY. `__main__` selects `COPILOT_MODE` as the first
screen iff an LLM config + non-empty key is supplied (`run_tui` lines 98–100). *(Full interactive
screen = Owner eyeball, per O3.)*

**R3 — real-LLM card (PASS).** Real chain (xchai.xyz · claude-sonnet-4-6) → `draft_task_card` →
`AIPOS-DOC-1`, `task_mode: docs`, `priority: medium`, `output_target: CONTRIBUTING.md` (semantically
on-point). **ZERO-WRITE**: workspace file-set hash identical before/after (×2, incl. `finalize_card`).
Copilot did **not** fabricate `context_bundle` → `needs_bundle: True`; once the Owner supplied a
bundle, `conformant: True / blocking: []`. cc-side conformance via the copilot's in-memory
`draft_validator`; the gate-side `draft_publish_dry_run` ran in R4 (owner-scoped).
- **Owner 5-anchor quality pass (R3 §c):** _to be scored by the Owner_ — (1) field semantics fit;
  (2) title on-point + body actionable; (3) no fabricated bundle (✓ surfaced needs_bundle); (4)
  passes dry_run (✓ confirmed in R4); (5) no hallucinated fields / no secrets.

**★A1 — live-gate structural denial (PASS).** Against the live gate: copilot→`draft_publish_dry_run`
and `draft_publish_confirm` = **SCOPE_DENIED**; executor→both = **SCOPE_DENIED**. Control:
executor→`queue_list` = PASS (denial is scope-specific, not a blanket failure — RF-5).

**R4 — gated publish [Owner OOB] (PASS).** Owner ran `~/release_gate_publish.sh` →
`ok: True, verdict: PASS`. On disk: task in `5_tasks/queue/pending/`; publish record
`confirmer_role: owner`, `confirmer_token_ref: svc-owner`, `confirmer_token_fingerprint:
sha256:00173996a805`, `published_by: owner`, `source_sha256 == published_sha256`.
*(`gate_signature`/`authority_seal` empty = §9 disclosed-deferred signing — consistent with the
disclosure ledger.)*

**R5 — executor pickup [Owner OOB] (PASS, WARN).** cc ran the **executor** claim dry-run (executor fp
`d47eab96d1ec`, no confirm); Owner confirmed → `ok: True, verdict: WARN`. On disk: `pending→claimed`,
claim record `confirmer_role: owner` (svc-owner / `00173996a805`), dry-run binding present
(`dry_run_id` match + `dry_run_snapshot_hash`). **WARN** is a benign data artifact: the LLM card set
`assigned_to: owner` while `claim_policy: assigned_agent_only` and the claimant was `agent-01` →
soft policy warn (`claim_match_basis` blank). Gate correctly warns-but-allows under Owner confirm.
★A1 holds (executor cannot self-confirm).

**R6 — through to L3 VALID [Owner OOB] (PASS, WARN).** cc ran the **executor** return dry-run from the
installed product (provenance: `confirm_client.__file__` under `/tmp/lybra-rg-prefix`); Owner confirmed
→ `ok: True, verdict: WARN`. On disk: return record `confirmer_role: owner` (svc-owner /
`00173996a805`), `executor_status: completed`, `audit_readiness: ready`. **L3 authority scan** (from
the product): `AIPOS-DOC-1` → **`authority_verdict: VALID`**, `effective_truth: True`,
`effective_tasks: 1 / excluded_invalid: 0`. (Task reached VALID via the publish+claim+return record
chain; 196a scratch-artifact ingestion is not required on the minimal path — runbook-sanctioned.)

## Cross-evidence
- Layer-a automated gate `ACCEPTANCE: PASS` is the parallel structural proof (this report is layer-b live).
- **Provenance / RF-5:** the gate was served from the installed product; the client modules exercised
  in R3/R5/★A1 are **byte-identical** between the dev tree and the unpacked tarball (`copilot.py`,
  `confirm_client.py`, `draft_validator.py`, `presentation.py`, `__main__.py`, `service_mode.py`,
  `tools.py` all `diff`-clean), so those results are the installed product's behavior; R6 + the L3
  scan were additionally run with imports forced from `/tmp/lybra-rg-prefix` (printed `__file__`).
- Evidence sites untouched (`~/lybra-191b-*`, `~/lybra-formb-workspace`, `~/lybra-copilot-workspace`
  all last-modified ≤ 2026-06-23). Only 6 gate paths created/touched (all 2026-06-24).

## Findings & triage (no hard-fix in-gate)
- **F-rg-1 (install-path correctness).** The CLI still prints the broken install form
  `pip install lybra[tui]` (lybra is npm-only / not on PyPI) in **3 user-facing strings**:
  `tools/lybra_tui/__main__.py:104`, `tools/aipos_cli/aipos_cli.py:711` (tui subparser help),
  `:995`. AIPOS-213 fixed the README + added a README guard but did not guard the CLI runtime strings.
  → **separate fix slice** (rewrite to `pip install textual` / point at the README; extend the guard
  to CLI source). Not hard-fixed here.
- **O1 (doc-drift reconcile, Owner ruled = (a)).** `docs/v1_acceptance_runbook.md` R0 shows only the
  dev-clone `pip install .[tui]`; the npm-tarball canonical path was followed for this gate. → small
  post-gate edit marking both audience paths (npm: `pip install textual`; clone: `.[tui]`).
- **Env note (non-product).** R5/R6 WARN are data artifacts of the LLM card's `assigned_to: owner`
  under `assigned_agent_only`; not a product defect.

## Cleanup (at closure)
- Stop the gate; remove the disposable prefix `/tmp/lybra-rg-prefix`, the build tarball
  `lybra-0.2.0.tgz`, and the 0600 key file `~/.lybra_planchat_key`. (Open follow-up: Owner-side key
  rotation — the raw key is in the transcript.)

## Not done (★)
- `npm publish` (separate, irreversible, Owner-authorized release — only after this gate, on separate
  authorization). No product code change. No self-finalize.
