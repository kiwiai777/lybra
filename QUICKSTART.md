# Lybra Quick Start — from install to your first card closed

This walkthrough takes a **new machine** to a closed first card with the **current** flow: one
project standing authorization, advisor-driven loops, sub-agents for code, **pi** workstations for
everything else. All names below are examples — replace `my-project`, `advisor.my-project.h1`, …
with your own.

**English** · [简体中文](QUICKSTART.zh-CN.md) · [Overview](README.md)

## Roles at a glance

| Role | Runs on | Does |
|---|---|---|
| Owner | the gate machine | Starts the gate; signs enrollment and the **project standing grant**; reads the Owner inbox; watches the board. Signs **once per project**, not once per card. |
| Advisor | a local command-line agent session (Claude Code by default, Codex supported) | Onboards the project; drafts and publishes cards; drives each card with **`lybra loop`** and follows through with `lybra loop status`; maintains the board headline. |
| Sub-agent executor | spawned inside the advisor session | Works **code** cards from the `lybra kickoff` brief. |
| pi executor workstation | a separate directory, running **pi** | Works **every non-code card** (docs / governance / config / validation). Started by `/go` or, when the grant allows, launched by `lybra loop`. |
| pi auditor workstation | another separate directory, running **pi** | Audits returns. **Never** the executor. |

Executors and auditors never talk to the gate — the product does every gate action for them.

## Prerequisites

- **Node.js 18+**, **Python 3** and **git** on `PATH`.
- **pi** (for the executor and auditor workstations), with at least one model configured — check with
  `pi --list-models`.
- An advisor session: **Claude Code** (default) or **Codex**.
- The advisor must be able to run local commands on the governance machine (or ssh into it).

## Step 1 — Install

```bash
npm install -g lybra
lybra --help
```

`lybra --help` prints the usage tree (serve, onboarding, project, roles, sync, draft, loop, board,
owner, release, …). Any subcommand explains itself with `--help`; every command is read-only unless
you pass `--confirm`.

## Step 2 — Start the gate (Owner)

Pick a directory that will hold the Owner credential (the central gate registry) and start the gate
there:

```bash
lybra serve --workspace-root ~/lybra-gate start
lybra serve --workspace-root ~/lybra-gate status
```

Expected: the **board** serves on `127.0.0.1:7117` and the **gate** (MCP) on `127.0.0.1:7118`, with a
redacted credential status — token values are never printed. `status` shows both listeners and the
credential directory. Port busy? Rebind both:

```bash
lybra serve --workspace-root ~/lybra-gate start --board-port 7119 --mcp-port 7120
```

Stop the gate with Ctrl-C (foreground). Today a resident install (systemd / launchd) is not yet
productized — see [README · Scope & limits](README.md#scope--limits).

## Step 3 — Create the project (advisor)

Generate the project's onboarding guide — it prints **every** step for *your* project, in order,
each with the exact command, how to verify it, and failure exits:

```bash
lybra onboarding guide my-project --repo app=~/code/my-project
lybra project new my-project
lybra project set-repos my-project --repo app=~/code/my-project --dry-run
lybra project set-repos my-project --repo app=~/code/my-project --confirm
```

- `project new` creates the project under the governance home (default `~/.lybra/projects`); the
  guide prints the one governance landing line to run for this step. First time on a machine that
  says "not a git repository"? See the FAQ.
- `set-repos` is two-phase: `--dry-run` (default) previews repo validation with zero writes;
  `--confirm` writes `project.json` and auto-lands it in governance.
- A **Codex** advisor: add `--advisor-harness codex` to the guide command.
- Check any step's state: `lybra onboarding check my-project --step 3`.

## Step 4 — Sign the project standing authorization (Owner)

One signature covers the whole project — lanes, task modes, drivers, hosts, launch permission and
hard bounds — replacing per-card envelopes:

```bash
lybra project grant show my-project
lybra project grant sign my-project --grant-lane app --task-mode code --task-mode docs --driver advisor.my-project.h1 --launch-harness pi --max-tasks 50 --expires-at 2027-01-31T00:00:00Z --confirm
```

- `grant show` (read-only) inspects the current grant and migrated envelopes.
- Without `--confirm` the sign is a **local preview**: it prints the full grant diff and validation.
- What the flags mean: `--grant-lane` repo lanes the grant covers; `--task-mode` task modes (code,
  docs, governance, config, validation — audit is its own channel); `--driver` which advisor may
  drive; `--launch-harness pi` allows the loop to launch pi workstations (without it, workstations
  start via `/go` only); `--max-tasks` / `--expires-at` hard bounds; `--host` binds a lane to a host.
- Revise later: `lybra project grant extend` / `lybra project grant revoke`.

## Step 5 — Enroll the advisor and the workstations

The product enrolls roles with **`lybra roles enroll-deliver`** — the one-time enrollment code never
appears on a command line (no code literals in shell history). Run the exact commands **your guide
prints**; they carry the real instance names and authorization refs. The shapes are:

```bash
lybra roles enroll-deliver --role advisor --instance advisor.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --harness claude-code --harness-dir ~/sessions/my-project --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance subexec.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --executor-mode subagent --harness claude-code --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance exec.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/my-project-executor --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role auditor --instance audit.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/my-project-auditor --owner-authorization-ref auth-2026-11-01-01 --confirm
```

- `--role executor --executor-mode subagent` + `--harness claude-code` enrolls the **sub-agent
  executor** (code cards) inside the advisor's session; `--role executor` without it, with a pi
  workspace directory, enrolls the **pi executor workstation**.
- Then, on each pi workstation, pull its charter, skills and extensions:

```bash
lybra sync --harness-root ~/my-project-executor --workspace-root ~/.lybra/projects/my-project
lybra sync --harness-root ~/my-project-auditor --workspace-root ~/.lybra/projects/my-project
```

`sync` is safe to re-run (idempotent); it lists what was delivered and what stays undelivered. A
remote workstation on another host records `--harness-host` at enrollment (the guide handles it).

## Step 6 — Declare the division of execution

Declare, once per project, who executes what — code cards to the sub-agent, every non-code card to
the pi executor, audits to the independent pi auditor:

```bash
lybra project set-execution my-project --subagent-executor subexec.my-project.h1 --pi-executor exec.my-project.h1 --auditor audit.my-project.h1 --confirm
lybra project workstation-plan my-project
```

Preview without `--confirm`. `workstation-plan` prints the read-only workstation plan (the same view
the onboarding guide shows).

## Step 7 — Bring up the advisor session

- **Claude Code:** start the session in the directory you enrolled with `--harness-dir`. The Lybra
  skills are already delivered into `.claude/skills` (re-run `lybra sync` to refresh).
- **Codex** (on the governance machine or over ssh): at session start, pull the charter and skills
  into the session directory:

```bash
lybra charter --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
lybra skills --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
```

Pipe the skills stream to `tar -xf -` in the session directory (the command's `--help` prints the
exact one-liner). Then start Codex there.

## Step 8 — Publish the first card and drive it with lybra loop (advisor)

```bash
lybra draft create --from-json card.json
lybra draft validate --path 5_tasks/drafts/myproj-1.md
lybra draft publish --path 5_tasks/drafts/myproj-1.md
lybra loop --task-id MYPROJ-1
```

- `draft create` writes a draft under `5_tasks/drafts/` from your JSON card (`--help` lists every
  field; CLI flags like `--title` work without JSON). `validate` checks frontmatter and queue
  consistency; `publish` moves it into the queue with the state machine.
- `lybra loop --task-id MYPROJ-1` finds the project standing authorization (no envelope juggling)
  and advances the card: claim (the gate builds the card worktree) → executor works and returns →
  audit dispatch → pi auditor → verdict → finalize → close, landing governance commits.
- For a **code card**, the loop tells the advisor to spawn its sub-agent and hand it the
  self-contained kickoff brief:

```bash
lybra kickoff --task-id MYPROJ-1
```

- For a **non-code card**, the loop launches pi at the executor workstation (grant permitting) or
  prints which workstation needs `/go` (the Owner or operator presses it there).
- Loop exit codes: `0` completed · `2` gate refusal (output carries the reason) · `3` wait
  timeout / stall / steps exhausted · `4` step underivable or command unparseable · `5` no valid
  authorization (sign the grant) · `6` deploy already updated · `7` another loop already runs on
  this card.

## Step 9 — Watch, wait and inspect

```bash
lybra loop status --task-id MYPROJ-1 --wait 540
lybra agent watch --workspace-root ~/.lybra/projects/my-project --timeout 600
lybra next --task-id MYPROJ-1
lybra state lint --workspace-root ~/.lybra/projects/my-project
lybra board open --workspace-root ~/.lybra/projects/my-project
lybra owner inbox
```

- `loop status --wait <seconds>` blocks (bounded) and then returns the advisor's next action with an
  exit code: `0` done or continue, `3` keep waiting (re-run), `5` Owner needed (check
  `lybra owner inbox`), `6` investigate, `7` advisor fix, `8` deploy changed, `2` usage.
- `agent watch` is a pure-client filesystem sentinel — bounded, no gate connection, any bash-capable
  agent can run it (exit codes: [`docs/agent_watch_exit_codes.md`](docs/agent_watch_exit_codes.md)).
- `state lint` verifies queue / frontmatter / records consistency; `next` prints a card's next step
  and its first runnable command; `history search` finds closed cards by keyword.
- The **board** (default `127.0.0.1:7117`) is the dashboard: headline cards (the advisor maintains
  them — see Declarations below), the Owner inbox, the queue, workstation pages and history. `board
  open` opens it with a one-time ticket; no plaintext token in the URL.

## Declarations on demand

Write-what-you-mean, each `--dry-run` previewable and governance-landed on `--confirm`:

```bash
lybra project card-policy show
lybra project card-policy set-task-id-pattern my-project --pattern MYPROJ-\d+ --confirm
lybra project set-test-contract --timeout-seconds 1800 --owner-ref arb-2026-11-05-01 --confirm
lybra project release-target declare --id web --host h1 --repo app --allow dist/ --release-root /srv/my-project --entry current --owner-ref arb-2026-11-05-02 --confirm
lybra project set-model --model PROVIDER/ID --owner-ref arb-2026-11-05-03 --confirm
lybra project headline --instance advisor.my-project.h1 --title "My Customer Project" --now "Closing the first card" --done "Onboarding complete" --confirm
```

- **card-policy** — the project's card-id pattern (affects draft validation).
- **set-test-contract** — the post-merge regression contract (full-suite timeout; reused by
  `lybra regression baseline`, which records the main-side baseline so most cards don't re-run it).
- **release-target** — declare a production host + allowlist + release root; then the card's
  production deploy runs only through the product verbs `lybra release plan` → `apply` → `verify`
  (rollback: `release rollback`) — never raw ssh/scp/rsync. `lybra project release-target list`
  shows declared targets.
- **set-model** — the pi model per project (must be one your pi already has configured; check
  `pi --list-models`).
- **headline** — the advisor's summary card on the board: one title, "now", "done" and an optional
  "next".

## Core concepts

- **Files are truth.** Cards, records, verdicts and decisions live in the governance root — durable,
  reviewable, outliving any model session.
- **Gate, not engine.** The gate never runs or wakes agents. The Owner starts the gate; a workstation
  harness is started by `/go` or — only when the grant allows — by the advisor's loop.
- **Executor zero-gate.** Executors and auditors never touch the gate; every gate action is the
  product's.
- **Drafter ≠ executor ≠ auditor.** The advisor drafts and drives, executors work, an independent pi
  auditor judges. No independent audit PASS, no finalize.
- **One grant, many cards.** A single standing Owner authorization covers the project; per-card
  envelope juggling is retired.

## FAQ

**Port 7117 / 7118 already in use.** Start with `--board-port 7119 --mcp-port 7120` (Step 2) and the
connection file is updated for clients.

**`lybra loop` exits 3 while the card is "running".** The workstation hasn't returned yet — check the
board or the workstation, then re-run `lybra loop status --task-id MYPROJ-1 --wait 540`. If an early
exit is reported there, it says why; to see why a return was refused, preview ingestion:
`lybra artifact ingest --task-id MYPROJ-1 --kind return --dry-run`.

**A landing command says "not a git repository".** The governance home isn't a git repo yet — one-shot
local init: `lybra home git-init --home-root ~/.lybra/projects`, then the Owner adds the remote and
pushes the initial commit.

**Switch the board language.** Use the language toggle on the board page — English / 简体中文 both
built in.
