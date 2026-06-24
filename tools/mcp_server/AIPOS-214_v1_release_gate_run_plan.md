# AIPOS-214 — v1.0 release gate: live run on the npm install product (RUN PLAN)

- **status: draft**
- **authority: NONE** (run plan for Owner 复核; no live execution, no product code change until "run plan 复核 PASS, 批准执行")
- Date: 2026-06-24
- Epic: v1.0 Scope B — release gate (the live half of acceptance, run against the *install product*, not the dev tree)
- Kind: **governed live run, not a code slice** — no product code change; findings get registered + triaged, never hard-fixed in-gate; manual finalize.

## Why this gate (the delta over dogfood)
The copilot dogfood proved the **dev-tree** chain (real LLM → conformant card → ★A1 read-only →
zero-write → RF-5). This gate proves the **install product path** — what a v1.0 user actually runs:
`npm pack` tarball → `npm i -g <tgz>` → `pip install "textual>=0.50"` → `lybra` command → real chain.
It runs `docs/v1_acceptance_runbook.md` R0–R6 end to end and emits a governed execution report.

## Canonical path ruling (R0 doc-drift — Owner pre-ruled)
`docs/v1_acceptance_runbook.md` R0 (line 20) reads `pip install .[tui]` — the **dev-clone** path.
The 213 canonical user path is **npm-tarball + `pip install textual`**. Per Owner ruling: run the
**213 canonical path**, **register the drift**, **do not hard-fix in-gate**.
- **Open item O1 (Owner ruling sought):** register the drift as **(a)** a tiny post-gate reconcile
  edit to runbook R0 (add the npm-tarball path alongside the dev-clone path), or **(b)** report-note
  only (leave R0, record the drift in the execution report + a follow-up). Default if unspecified: **(a)**
  small reconcile as its own trivial doc edit *after* the gate passes (not inside the live run).

## Workspace & evidence discipline (red lines)
- Fresh disposable workspace: **`~/lybra-release-gate-workspace`** (created once for this gate).
- **Zero contact** with evidence sites: `~/lybra-191b-workspace`, `~/lybra-191b-rerun-workspace`,
  `~/lybra-formb-workspace`, `~/lybra-copilot-workspace`.
- Disposable global install prefix for the tarball so the gate does not mutate any real global env
  (npm `--prefix` into a temp dir; or a throwaway venv for `pip install textual`). Recorded in report.
- **★A1 red line:** cc NEVER holds the owner token, NEVER runs any confirm, NEVER touches publish
  creds. R4/R5/R6 are **[Owner OOB]**, run by the Owner via the existing runtime-token helpers
  (`~/copilot_publish.sh`, `~/copilot_claim_confirm.sh`) which read the owner token at run time.
- cc holds only: **copilot (read-only)** + **executor** tokens, and the **LLM key via env**
  (`LYBRA_PLANCHAT_LLM_KEY`, fingerprint-only — never in argv/logs/report).
- Secrets fingerprint-only everywhere; connection.json `0600`.

## cc-side vs Owner-OOB split
| Step | Who | What |
|---|---|---|
| R0 install | **cc** | `npm pack` → install tarball into disposable prefix → `pip install "textual>=0.50"`; verify `lybra` command resolves; verify gate core installs **zero-dep** (`npm i -g <tgz>` with no textual still runs `lybra serve`). Evidence: pack manifest, resolved command path, no-textual serve boot. |
| R1 serve | **cc** | `lybra serve --workspace-root ~/lybra-release-gate-workspace` → `serve rotate` mints executor/owner/copilot; connection.json `0600`; tokens fingerprint-only. cc keeps copilot+executor; **does not use owner token**. |
| R2 first screen | **cc (bounded)** | TUI is interactive/textual — cannot fully drive headless here. Prove the renderable pieces: AIPOS-210 `presentation.banner()` renders in a real terminal width; `__main__` selects copilot first-screen (COPILOT_MODE) when LLM config supplied. Full interactive screen = Owner eyeball (optional [Owner OOB] confirm in report). |
| R3 real-LLM card | **cc** | Drive the real copilot chain against the **installed** product: one NL ask → `xchai.xyz/v1` / `claude-sonnet-4-6` → conformant card; assert it passes real `lybra_draft_publish_dry_run`. **Owner judges the 5 §c quality anchors** (field semantics / title-on-point + body actionable / no fabricated context_bundle / passes dry_run / no hallucinated fields·no secrets). |
| R4 gated publish | **[Owner OOB]** | Owner proceed → card lands `5_tasks/drafts/` → `draft_publish_dry_run` → **Owner owner-token confirm**. cc then reads disk: task in `5_tasks/queue/pending/`, publish record `confirmer_role=owner` (+token_ref/fp). |
| R5 executor pickup | **[Owner OOB]** | executor `queue_claim` dry-run → **Owner claim confirm** → cc reads disk: pending→claimed, claim record `confirmer_role=owner` (★A1: executor cannot self-confirm). |
| R6 → L3 VALID | **[Owner OOB]** | executor return dry-run → **Owner return confirm** → 196a ingestion → cc runs L3 scan → task **VALID**. |

## Prerequisite from Owner (live config)
- **LLM key:** the dogfood-era `~/.lybra_planchat_key` was deleted at dogfood closure, and the
  previously-pasted key is pending rotation. R2/R3 need `LYBRA_PLANCHAT_LLM_KEY` set in cc's env at
  run time. **Open item O2 (Owner action):** provide a *rotated* key for this gate (set it in env /
  re-create the 0600 key file; cc reads env, records fingerprint-only). Same endpoint config as
  dogfood unless changed: `--llm-base-url https://xchai.xyz/v1 --llm-model claude-sonnet-4-6`.
- R4/R5/R6: Owner runs the OOB confirm helpers when cc signals each dry-run is staged.

## Verdict & evidence (the report cc produces)
Per-step PASS/FAIL with **immediate on-disk re-check** as evidence:
- R0: install is the **tarball product** (manifest + resolved `lybra` path), not the dev tree; zero-dep gate boot.
- R1: connection.json `0600`; roles minted; tokens fingerprint-only.
- R2: banner renders in a real terminal; copilot first-screen selected.
- R3: conformant card from the **installed** product + passes dry_run; Owner anchor scores.
- R4: `confirmer_role=owner` read off disk. R6: L3 **VALID**.
- Cross-evidence: cite `ACCEPTANCE: PASS` (layer a / automated) as the parallel structural proof.
- Evidence sites verified untouched; cc never held the owner token; secrets fingerprint-only.

## Finding triage (no hard-fix in-gate)
- **Install-path finding** (tarball drops a file, `lybra` unresolved, banner breaks in a real
  terminal, textual version floor wrong) → register finding **F-rg-***, open a **separate fix slice**,
  do not patch inside the gate.
- **Doc-drift** (R0 canonical path) → O1 above.
- **Environment** (proxy/key flakiness, registry reachability) → record as environment note, **not** a
  product finding.

## Non-goals (★ explicitly NOT in this gate)
- **`npm publish`** — separate, irreversible, Owner-authorized release (5a); only considered *after*
  this gate passes, on separate authorization.
- product code change; self-confirm; cc finalize; AIPOS-206b / R2 / R5 deferred slices; CI wiring.

## Open items for Owner ruling
- **O1** R0 doc-drift handling: (a) tiny post-gate reconcile edit [default] | (b) report-note only.
- **O2** rotated LLM key for R2/R3 (provide in env / 0600 file; cc records fingerprint-only), and
  confirm the endpoint/model config is unchanged (xchai.xyz/v1 · claude-sonnet-4-6).
- **O3** R2 scope: accept the bounded cc proof (banner + first-screen selection) + optional Owner
  eyeball of the full interactive screen, or require a full interactive-screen [Owner OOB] step.

## Exit
Run R0–R6 → cc produces the execution report → Owner 复核 → PASS / finding. If PASS, **and only on
separate Owner authorization**, cc drafts the 5a `npm publish` steps for the Owner to execute (cc
never touches the publish credentials).
