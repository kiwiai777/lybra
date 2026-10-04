# Lybra release discipline

Process discipline for committing and finalizing work under Lybra. Codifies the lesson of an
unrelated change swept into a finalize commit via a broad `git add`.

## Commit hygiene
- **Precise pathspec.** Stage with explicit paths: `git add <path> [<path> ...]`.
  **Never `git add -A` / `git add .`** — a dirty tree can sweep unrelated changes into a
  finalize commit.
- **Verify the staged set before committing:** `git diff --cached --name-only` must list
  exactly the files for this change. Untracked files (`??`) stay untracked.
- **One logical change per commit.** A card's commits contain only that card's files, inside the
  card's declared lane.

## Two-repo boundary
- The **product repo** (the code a card changes) and the project's **governance repo** (the
  governance root under the Lybra home root: queue, records, decision log) are committed
  separately. Never cross-contaminate: a product commit carries no governance files and vice versa.

## Branch / push
- Each card works on its own card branch (`card/<card ID>`), created by the gate in the card
  worktree when the card is claimed. The executor commits there only — no merge to `main`, no push.
- Merging to `main` and pushing happen only through finalize (below).

## Finalize gate
- **Manual finalize is retired.** Finalize runs only inside the advisor's `lybra loop --task-id <card>`,
  after an independent auditor's PASS verdict has been ingested, under an Owner-signed envelope. Nobody
  hand-merges or hand-pushes a finalize, and no party finalizes or audits its own work.
- Governance truth is landed by the same loop: after close, its N6 step runs
  `lybra governance-commit --task-id <card> --actor <driver>` (task-scoped precise commit + push).
  Non-card governance changes (project declarations, governance documents, envelopes) use
  `lybra governance-commit --actor <advisor> --paths <path> [--paths <path> ...]`. Never hand-run `git add`/`git commit` in the governance repo.

## Secrets
- Tokens / API keys are **fingerprint-only** in any output, log, record, report, or commit.
- `connection.json` is local (`0600`), never committed. LLM keys come from an env var
  (`LYBRA_PLANCHAT_LLM_KEY`), never argv, never `connection.json`, never git.
