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

## Deploy snapshot and the `lybra` CLI entry (AIPOS-F152)
- **The `lybra` command runs the deployed snapshot, not the product repo working tree.** The entry resolves
  `<product repo>/.deploy/current` to its real release directory once at start-up and loads every module from that one
  immutable snapshot, so a working tree that is mid-merge, dirty, or wrongly edited is never picked up by gate commands or
  advisor commands. `lybra --version` prints which code is running (snapshot release + commit, or dev mode / work tree).
- **Install / upgrade the entry:** `tools/lybra-deploy install-entry [--bin-dir <dir>]` writes the entry to
  `~/.local/bin` (or `--bin-dir`; a previous non-snapshot entry such as a `pip install -e` console script is kept as
  `lybra.pre-snapshot-entry`). Every deploy rewrites it (Step 3b), so after the first install nothing else is needed: the
  next deploy is what `lybra` runs. A `pip install -e .` entry is only a trampoline into the same snapshot entry.
- **Local debugging:** `lybra --dev-tree <work tree> <subcommand> ...` (it must be the first argument) runs that tree; the
  first stderr line says `[lybra] 开发模式(--dev-tree)`. The switch is command-line only, so an inherited environment never
  silently moves another command off the snapshot. Card worktree tests and `python3 -m tools.aipos_cli` keep using the work
  tree code.
- **Long-running commands follow the deploy.** `lybra loop`, `lybra agent watch` and `lybra loop status --wait` remember the
  snapshot commit they started on and compare it with `.deploy/current`; once a newer deploy lands they stop cleanly
  (`lybra loop`: end reason `deploy_changed`, old and new commits in the run record; exit codes in
  `schema/verbs.schema.json`). Re-run the same command: the loop resumes from where the records stand.

## Secrets
- Tokens / API keys are **fingerprint-only** in any output, log, record, report, or commit.
- `connection.json` is local (`0600`), never committed. LLM keys come from an env var
  (`LYBRA_PLANCHAT_LLM_KEY`), never argv, never `connection.json`, never git.
