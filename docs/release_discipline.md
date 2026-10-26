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
  Commands that write governance truth land it themselves (next item), so do not follow their `--confirm` with a
  `lybra governance-commit`; it would be a no-op. Other non-card governance changes (hand-edited governance documents,
  `lybra owner-decision` records, the files `lybra project new` creates, and re-landing after a failed landing) use
  `lybra governance-commit --actor <advisor> --paths <path> [--paths <path> ...]`. Never hand-run `git add`/`git commit` in the governance repo.
- Commands that write governance records land them themselves through that same commit path (AIPOS-F158, declared in
  `schema/verbs.schema.json` `lybra_governance_commit.post_write_landing`): `lybra envelope mint --confirm` commits and pushes
  the records the gate wrote for the signing (decision record, envelope, decision_log pointer; message names the policy ids),
  `lybra migrate record-type --confirm` commits the records it backfilled, and `lybra migrate decision-log-pointers --confirm`
  commits the pointers it generated (AIPOS-F160). Since AIPOS-F159 the project commands that write
  project.json (`lybra project set-paths` / `set-repo` / `set-repos` / `set-workstation` / `set-meta` / `set-execution` /
  `set-model` / `set-return-summary`, plus `freeze-legacy` and `dispatch-mode set`) land what they wrote the same way after `--confirm`
  (commit identity `--actor`, default `$USER` or owner; message names the subcommand and project); `--dry-run` still writes
  and lands nothing. If landing fails the write still stands (do not sign again): the command prints the reason and the exact
  `lybra governance-commit --paths …` to re-run, and exits 3.
- The gate's decision_log auto-pointer is named after the full decision id (`YYYY-MM-DD-<decision_id>-auto-pointer.md`;
  an envelope's is `envelope-<policy_id>`), so several envelopes signed on one day each get one (AIPOS-F159). Older pointers keep
  their names and are read by directory and content, not by file name. `lybra migrate decision-log-pointers` (the `migrate`
  family, same two phases as `record-type`) previews by default: it lists decision records that have no pointer and the pointer
  file it would create for each, and writes nothing. `--confirm --actor <instance>` creates those pointers with the gate's own
  pointer generator (dated by the decision's `decided_at`, marked as backfilled) and lands them. Only records the gate wrote as
  `owner_decision_record` with a decision id, a dated `decided_at`, `decided_by_ref` and a summary line are filled; anything
  else is listed with the reason and left alone. A second run finds nothing to fill (AIPOS-F160).
- Records that the commit guard (B④) refuses for a missing `record_type` are listed by `lybra migrate record-type` (preview,
  writes nothing): a type is derived only from the declared record locations; only kinds with a reconstructable record header
  (audit report snapshots, checked against the verdict record's sha256) are rewritten on `--confirm`, everything else is listed
  and left untouched.

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
  Exception (AIPOS-F166): while `lybra loop` is in a manual wait step (waiting for an executor's return), a new deploy does
  not stop it — the loop re-execs itself on the new snapshot (same pid, `deploy_reexec` / `continues_run` in the run records)
  and keeps waiting at the same step. Since AIPOS-F241 the same happens between two steps (before the next derivation, no gate
  verb running): the loop re-execs on the new snapshot and continues from the current derivation point (run record
  `reexec.kind = step`), logging the old → new commit and any unread Lybra update notice. It still stops with `deploy_changed`
  when the deploy keeps changing (more consecutive step re-execs without a landed step than
  `lybra_loop.deploy_consistency.step_reexec.max_consecutive`) or the new snapshot is unusable.
- **Post-merge regression logs live next to the run records** (AIPOS-F166): async regression logs go under the governance
  root's `loop_runs_root/<card>/` (git-excluded), not `/tmp`; a regression whose process vanished is reported as unfinished
  by `lybra loop status` / `brief` / `next` with the re-run command `lybra regression run --sha <commit>`.

## Advisor update notes (AIPOS-F167)
- **A card that changes what advisors, auditors or executors see declares it on the card.** The one field is
  `advisor_notice: {audience: [advisor|auditor|executor], summary, actions[]}` (`schema/card.schema.json`
  `fields.advisor_notice`). `draft create` / `validate` / `publish` reject a malformed one (`ADVISOR_NOTICE_INVALID`) and warn,
  without refusing, when a card whose lane touches the product's advisor-visible paths (CLI, schema, charters) has none
  (`ADVISOR_NOTICE_MISSING`).
- **The notes are compiled at deploy time, in one place.** After `finalize --deploy` writes a finalization record with
  `deploy_status: deployed`, the card's `advisor_notice` (plus any not-yet-compiled entry of `docs/advisor_notice_backfill.json`)
  is written as one update note under the deployed product repo's deploy directory (`.deploy/advisor_updates/`, outside every
  governance root, shared by all projects). One note per deploy, keyed by deploy commit and release; a card is never compiled
  twice. A failed compile does not undo the merge or deploy; rerunning the same `finalize --deploy` compiles what is missing.
- **Advisors see unread notes where they already look.** `lybra charter` (advisor role) starts with, and `lybra loop status`,
  `lybra next` and `lybra brief` end with, `Lybra 有 N 条未读更新(自 <release>): …; 读完后继续推进, 不必等 Owner`.
  `lybra updates --actor <instance>` prints the full text; `lybra updates --ack --actor <instance> --confirm` records the
  instance as having read them (an append-only record under the governance root's `records/advisor_update_acks/<instance>/`,
  landed through the same governance-commit path as other write commands). Read state is per project and per instance.
  Only the deployed snapshot and `--dev-tree` read the notes; card worktree tests never see the real ones.

## Secrets
- Tokens / API keys are **fingerprint-only** in any output, log, record, report, or commit.
- `connection.json` is local (`0600`), never committed. LLM keys come from an env var
  (`LYBRA_PLANCHAT_LLM_KEY`), never argv, never `connection.json`, never git.
