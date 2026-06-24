# Lybra release discipline (5a)

Process discipline for committing/finalizing Lybra work. No code — a checklist future
finalizes reference. Codifies the `53a2a42` lesson (an unrelated change swept into a
finalize commit via a broad `git add`).

## Commit hygiene
- **Precise pathspec.** Stage with explicit paths: `git add <path> [<path> ...]`.
  **Never `git add -A` / `git add .`** — a dirty tree can sweep unrelated changes into a
  finalize commit.
- **Verify the staged set before committing:** `git diff --cached --name-only` must list
  exactly the files for this change. Untracked files (`??`) stay untracked.
- **One logical change per commit.** A finalize commit contains only that slice's files.

## Two-repo boundary
- The **product repo** (`lybra`, npm/bin distribution) and the **governance repo**
  (`ai-project-os`) are committed separately. Never cross-contaminate: a product commit
  carries no governance files and vice versa (the `53a2a42` KPRX contamination lesson).

## Branch / push
- Work on a branch; `git pull --rebase` before `git push` to keep history linear.
- Don't push from a tree with unrelated staged/dirty changes.

## Finalize gate
- **Manual finalize only.** Commit/push a finalize only after: the slice's independent
  audit PASS **and** explicit Owner approval. No self-finalize.
- Governance finalize records: a report, a decision-log entry, and project_status/roadmap
  updates. A stage archive is written only when a stage closes.

## Secrets
- Tokens / API keys are **fingerprint-only** in any output, log, record, report, or commit.
- `connection.json` is local (`0600`), never committed. LLM keys come from an env var
  (`LYBRA_PLANCHAT_LLM_KEY`), never argv, never `connection.json`, never git.
