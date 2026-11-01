# Lybra Quick Start — from install to your first card closed

This walkthrough takes a **new machine** to a closed first card with the **current** flow: an
eleven-step onboarding guide, one project standing authorization, advisor-driven loops,
sub-agents for code, **pi** workstations for everything else. All names below are examples —
replace `my-project`, `advisor.my-project.h1`, … with your own.

**English** · [简体中文](QUICKSTART.zh-CN.md) · [Overview](README.md)

## Roles at a glance

| Role | Runs on | Does |
|---|---|---|
| Owner | the gate machine | Runs the installation self-check; starts the gate (foreground or resident install); signs enrollment and the **project standing grant**; reads the Owner inbox; watches the board. Signs **once per project**, not once per card. |
| Advisor | a local command-line agent session (Claude Code by default, Codex supported) | Onboards the project; drafts and publishes cards; drives each card with **`lybra loop`** and follows through with `lybra loop status`; maintains the board headline. |
| Sub-agent executor | spawned inside the advisor session (`exec.<project>.<host>`) | Works **code** cards from the `lybra kickoff` brief. |
| pi executor workstation | a workstation directory (default `~/lybra-workstations/<project>/executor`), running **pi** — instance `exec.<project>.<host>-pi` | Works **every non-code card** (docs / governance / config / validation). Started by `/go` or, when the grant allows, launched by `lybra loop`. |
| pi auditor workstation | another workstation directory (default `~/lybra-workstations/<project>/auditor`), running **pi** | Audits returns. **Never** the executor. |

Executors and auditors never talk to the gate — the product does every gate action for them.

## Prerequisites

- **Node.js 18+**, **Python 3.10+** and **git** on `PATH`.
- **pi** (for the executor and auditor workstations), with at least one model configured — check with
  `pi --list-models`.
- An advisor session: **Claude Code** (default) or **Codex**.
- The advisor must be able to run local commands on the governance machine (or ssh into it).

`lybra setup check` (Step 1) verifies all of this in one command.

## Step 1 — Install and self-check

```bash
npm install -g lybra
lybra --help
lybra setup check
```

- `lybra --help` prints the usage tree (setup, serve, onboarding, project, roles, sync, draft,
  loop, board, owner, release, …). Any subcommand explains itself with `--help`; every command
  is read-only unless you pass `--confirm`.
- `lybra setup check` is the read-only installation self-check (it never reads tokens or key
  files) — the same pre-flight the onboarding guide runs at its top. Seven items, one line each
  with ✓ / ✗ / ⚠ plus a closing exit line; any ✗ exits 1: **Python ≥ 3.10** · **Node.js ≥ 18** ·
  **git** · **pi** on PATH · **pi's configured models** · **gate up and reachable** · **resident
  gate service installed**. Any ✗ exits 1; failure lines carry their exit — the unreachable-gate
  line names both the foreground start and the resident install commands. Probe another gate
  address with `--gate-url`; `--json` prints the structured result the board reuses. The board's
  **Setup check** page shows the same result — the board home and onboarding pages carry a
  one-line ✓N · ✗N · ⚠N summary linking to it, and when pi or its models are missing it shows
  the install command. With a resident gate, its gate-service item also checks the installed
  unit's `PATH` covers pi's directory.

## Step 2 — Create the project (advisor)

**Guide steps 1–2.** Generate the project's onboarding guide — it prints **all eleven steps**
for *your* project, in order, each with the exact command, how to verify it, and failure exits.
Its pre-flight has already run `lybra setup check` (add `--skip-preflight` when generating the
guide on a machine other than the one being set up):

```bash
lybra onboarding guide my-project --repo app=~/code/my-project
lybra project new my-project
lybra project set-repos my-project --repo app=~/code/my-project --dry-run
lybra project set-repos my-project --repo app=~/code/my-project --confirm
```

- Guide step 1, `project new`, creates the project under the governance home (default
  `~/.lybra/projects`) and prints the one governance landing line to run for this step. First
  time on a machine that says "not a git repository"? See the FAQ.
- Guide step 2, `set-repos`, is two-phase: `--dry-run` (default) previews repo validation with
  zero writes; `--confirm` writes `project.json` and auto-lands it in governance.
- A **Codex** advisor: add `--advisor-harness codex` to the guide command.
- Check any step's state: `lybra onboarding check my-project --step 2` (`--step 0` = the
  pre-flight's ✗ items; steps use the guide's 1–11 numbering).
- Prefer the board? The Owner can also create the project on the board's onboarding page —
  **New project** (an Owner login session only) takes the project name and an optional
  code-repo path, previews the directory tree and the full `project.json` in a throwaway copy
  (zero real writes), then confirms: same implementation as `lybra project new` plus landing,
  after which the onboarding checklist lists the project with the next step marked. Continue
  from there with the guide command above.

## Step 3 — Start the gate (Owner)

**Guide interlude — after step 2, before step 3.** From the grant on, everything talks to the
gate. The guide prints both ways for *your* governance root. Foreground (the terminal stays
open; Ctrl-C stops the gate) — fine for a trial run:

```bash
lybra serve --workspace-root ~/.lybra/projects/my-project start
```

Resident — Linux installs a systemd --user unit, macOS a launchd agent (the launchd path is not
yet verified on real macOS hardware). The default is `--dry-run`: it prints the full unit file
and the exact install commands with zero writes; `--confirm` writes the unit, enables it now and
reads its state back:

```bash
lybra serve --workspace-root ~/.lybra/projects/my-project install --dry-run
lybra serve --workspace-root ~/.lybra/projects/my-project install --confirm
```

- An existing same-name unit is never overwritten; a hand-written unit already running
  `serve start` is reported and never taken over (`--confirm` refused). Remove what install
  built with `lybra serve --workspace-root ~/.lybra/projects/my-project uninstall --confirm`
  (hand-written units are never touched).
- The unit carries the installer's `PATH` at install time — the preview lists every directory
  it will write and marks whether pi / node / git resolve inside it. If pi lives under nvm, an
  npm global prefix or `~/.local/bin`, run `serve install` from a terminal where `pi` is found;
  if pi moves afterwards, refresh the unit with `uninstall --confirm` then `install --confirm`
  (the existing unit is never overwritten in place).
- Expected: the **board** serves on `127.0.0.1:7117` and the **gate** (MCP) on `127.0.0.1:7118`;
  `lybra serve --workspace-root ~/.lybra/projects/my-project status` prints the redacted status
  (token values are never printed). Verify with `lybra setup check` — the gate item turns ✓.
  Port busy? See the FAQ.
- Stopping the gate — foreground: Ctrl-C, or `lybra serve --workspace-root ~/.lybra/projects/my-project stop`,
  which stops the supervisor first and then any remaining child processes, reporting each
  final status (exit 1 if any could not be stopped); after a clean stop, `serve status` shows
  "not running". Resident unit: `serve stop` sends no signal (the unit's Restart= would relaunch
  it) — it exits 1 printing the manager's exit (`systemctl --user stop <unit>` on Linux,
  `launchctl bootout gui/<uid>/<label>` on macOS); disable and remove what install built with
  `uninstall --confirm`.

## Step 4 — Sign the project standing authorization (Owner)

**Guide step 3 — the first of the Owner's two one-time actions.** One signature covers the whole
project — lanes, task modes, drivers, hosts, launch permission and hard bounds — replacing
per-card envelopes:

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

## Step 5 — Enroll the advisor and the three workstations

**Guide steps 4–8 — step 4 is the Owner's second one-time action.** The product enrolls roles
with **`lybra roles enroll-deliver`** — the one-time enrollment code never appears on a command
line (no code literals in shell history). Run the exact commands **your guide prints**; they
carry the real instance names and authorization refs. The shapes:

Owner signs the advisor into the governance root (guide step 4) — the advisor session directory
defaults to the governance root itself, so `.claude/skills` lands right there:

```bash
lybra roles enroll-deliver --role advisor --instance advisor.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --harness claude-code --harness-dir ~/.lybra/projects/my-project --token-role owner --owner-authorization-ref auth-2026-11-01-01 --confirm
```

Advisor readiness (guide step 5) — the new advisor checks its own prerequisites and that the
delivered advisor files are stable, then opens the session:

```bash
lybra onboarding check my-project --step 5
lybra sync --harness-root ~/.lybra/projects/my-project --workspace-root ~/.lybra/projects/my-project --dry-run
```

- **Claude Code:** start the session in the session directory (default = the governance root);
  the Lybra skills are already delivered into `.claude/skills` — (re)start the session to load
  them.
- **Codex** (on the governance machine or over ssh): at session start, pull the charter and
  skills into the session directory:

```bash
lybra charter --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
lybra skills --role advisor --harness codex --workspace-root ~/.lybra/projects/my-project
```

Pipe the skills stream to `tar -xf -` in the session directory (the command's `--help` prints the
exact one-liner). Then start Codex there.

The advisor then enrolls the three workstations (guide steps 6–8) — workstation directories
default to `~/lybra-workstations/<project>/…`; the pi executor's instance name ends in `-pi`; the
sub-agent executor's credentials live in the governance root:

```bash
lybra roles enroll-deliver --role auditor --instance audit.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/lybra-workstations/my-project/auditor --executor-mode workstation --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance exec.my-project.h1-pi --governance-root ~/.lybra/projects/my-project --workspace ~/lybra-workstations/my-project/executor --executor-mode workstation --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra roles enroll-deliver --role executor --instance exec.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/.lybra/projects/my-project --executor-mode subagent --harness claude-code --owner-authorization-ref auth-2026-11-01-01 --confirm
```

- `--executor-mode workstation` + a workstation directory enrolls a **pi workstation** (auditor,
  pi executor); `--executor-mode subagent` + `--harness claude-code` enrolls the **sub-agent
  executor** (code cards) inside the advisor's session. A remote workstation on another host
  records `--harness-host` at enrollment (the guide handles it).

## Step 6 — Declare the division of execution

**Guide step 9.** Declare, once per project, who executes what — code cards to the sub-agent,
every non-code card to the pi executor, audits to the independent pi auditor:

```bash
lybra project set-execution my-project --subagent-executor exec.my-project.h1 --pi-executor exec.my-project.h1-pi --auditor audit.my-project.h1 --confirm
lybra project workstation-plan my-project
```

Preview without `--confirm`. `workstation-plan` prints the read-only workstation plan (the same view
the onboarding guide shows).

## Step 7 — Sync the workstations and self-check

**Guide step 10.** Deliver each pi workstation's charters, skills and extensions, re-check the
steady state, and run the workstation self-check:

```bash
lybra sync --harness-root ~/lybra-workstations/my-project/executor --workspace-root ~/.lybra/projects/my-project
lybra sync --harness-root ~/lybra-workstations/my-project/auditor --workspace-root ~/.lybra/projects/my-project
lybra onboarding check my-project --step 10 --workspace-dir ~/lybra-workstations/my-project/executor
lybra onboarding check my-project --step 10 --workspace-dir ~/lybra-workstations/my-project/auditor
```

- Expected: a `sync --dry-run` re-run prints `up-to-date: 0 file(s) to fetch/render`; the checks
  print `✓ Step 10 prerequisites satisfied`; `lybra project workstation-plan my-project` (Step 6)
  shows all three instances enrolled with fresh charters.
- `sync` is safe to re-run (idempotent); it lists what was delivered and what stays undelivered.

## Step 8 — Publish the first card and drive it with lybra loop (advisor)

**Guide step 11.** Before the very first card the guide also has you confirm the card-id pattern
(`lybra project card-policy` — see Declarations). Then:

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

The onboarding guide lists these at its end — **listed, not forced**; each `--dry-run` previewable
and governance-landed on `--confirm`:

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
- **Gate, not engine.** The gate never runs or wakes agents. The Owner starts the gate (foreground
  or resident install); a workstation harness is started by `/go` or — only when the grant allows —
  by the advisor's loop.
- **Executor zero-gate.** Executors and auditors never touch the gate; every gate action is the
  product's.
- **Drafter ≠ executor ≠ auditor.** The advisor drafts and drives, executors work, an independent pi
  auditor judges. No independent audit PASS, no finalize.
- **One grant, many cards.** A single standing Owner authorization covers the project; per-card
  envelope juggling is retired.

## FAQ

**Port 7117 / 7118 already in use.** Start with `--board-port 7119 --mcp-port 7120` (Step 3) and the
connection file is updated for clients.

**`lybra setup check` shows the gate as ✗ / ⚠.** The gate isn't running or isn't reachable — the
✗ / ⚠ line itself names both exits: start it in the foreground (`lybra serve --workspace-root <governance
root> start`) or install it resident (`lybra serve install`, Step 3). `--gate-url` probes another
address.

**`serve install` reports a hand-written unit.** Install never takes over a unit it didn't generate —
keep yours, or stop and remove it yourself, then re-run install.

**`lybra loop` exits 3 while the card is "running".** The workstation hasn't returned yet — check the
board or the workstation, then re-run `lybra loop status --task-id MYPROJ-1 --wait 540`. If an early
exit is reported there, it says why; to see why a return was refused, preview ingestion:
`lybra artifact ingest --task-id MYPROJ-1 --kind return --dry-run`.

**A landing command says "not a git repository".** The governance home isn't a git repo yet — one-shot
local init: `lybra home git-init --home-root ~/.lybra/projects`, then the Owner adds the remote and
pushes the initial commit.

**Switch the board language.** Use the language toggle on the board page — English / 简体中文 both
built in.
