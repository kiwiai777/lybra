<p align="center">
  <img src="docs/assets/lybra-banner.png" alt="Lybra — the accountability harness for AI agents" width="880">
</p>

<p align="center">
  <b>The accountability harness for long-running, multi-role AI projects.</b><br/>
  面向长期多角色 AI 项目的治理型 harness —— 项目真相固化在文件里，Owner 掌握每一道决策闸门，每一步皆可审计、可复现。
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/lybra"><img alt="npm" src="https://img.shields.io/npm/v/lybra?color=1A7A52&amp;label=npm"></a>
  <img alt="node" src="https://img.shields.io/node/v/lybra?color=1A7A52">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-1A7A52">
  <img alt="status" src="https://img.shields.io/badge/status-early%20access-1A7A52">
</p>

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">简体中文</a> · <a href="QUICKSTART.md">Quick start</a> · <a href="QUICKSTART.zh-CN.md">快速上手(中文)</a>
</p>

---

## What is Lybra

Lybra keeps **one project truth in files** for long-running, multi-role AI projects — cards, records,
verdicts and decisions live in a governance root, not in any agent's memory. Agents come and go; the
truth and the gate stay. It optimizes for **accountability**, not raw autonomy.

Three principles are welded in:

- **Gate, not engine.** The gate does not run or wake agents. The Owner starts the gate; agents run in
  their own harness and only produce artifacts. A workstation harness is started either by the Owner
  (`/go`) or — only when the project authorization explicitly allows it — by the advisor's `lybra loop`.
- **Files are truth.** State lives in durable files, not in compressed conversation memory — it
  outlives the model.
- **Drafter ≠ executor ≠ auditor.** The advisor drafts and drives; executors do the work; an
  independent auditor judges it. **Executors and auditors never touch the gate** — every gate action
  (claim, return, dispatch, verdict, finalize, close) is done by the product.

> The model isn't the bottleneck. The harness is.

## Roles and who does what

| Who | Does |
|---|---|
| **Owner** | Starts the gate (`lybra serve`), issues enrollment, signs the **project standing authorization** once per project (`lybra project grant sign`), decides what the inbox brings (`lybra owner inbox`), and watches the board. No per-card signatures — one grant covers the project's lanes, task modes, drivers, hosts and hard bounds. |
| **Advisor** — a local command-line agent session (Claude Code by default, Codex supported; `--harness codex`) | Onboards the project, drafts and publishes cards, and advances each card with **`lybra loop`**, following it with `lybra loop status --task-id <id> --wait <seconds>` and stopping only when the product says the Owner is needed. |
| **Code cards → advisor-spawned sub-agent** | The advisor drives its own sub-agent with the self-contained brief from `lybra kickoff --task-id <id>`; the sub-agent commits on the card branch and writes the report. |
| **Non-code cards → pi executor workstation** | Every docs / governance / config / validation card goes to a dedicated **pi** workstation. The loop launches pi there (when the authorization allows it) or the Owner presses `/go`. |
| **Audits → independent pi auditor workstation** | Audits are always done by a separate pi workstation that is not the executor. No independent audit PASS, no finalize. |

Executors and auditors run on **pi** (you install pi and configure a model); the advisor runs on the
local command-line agent of your choice. See the [Quick start](QUICKSTART.md) for the full walkthrough
(中文版:[快速上手](QUICKSTART.zh-CN.md)).

## Install

Prerequisites: **Node.js 18+**, **Python 3** and **git** on `PATH` (the gate core has no Python
dependencies); for executor / auditor workstations, **pi** with at least one configured model
(`pi --list-models`); an advisor session — Claude Code or Codex.

```bash
npm install -g lybra
lybra --help
lybra setup check
```

`lybra setup check` is the read-only installation self-check the onboarding guide runs as its
pre-flight: seven items — Python ≥ 3.10, Node.js ≥ 18, git, **pi** on PATH with configured models,
the gate up and reachable, the resident gate service — each with ✓ / ✗ / ⚠ and an exit line; any ✗
exits 1 with the fix hint. It never reads tokens or key files. The board's **Setup check** page
shows the same result (a one-line ✓N · ✗N · ⚠N summary on the board home and onboarding pages
links to it); with a resident gate, its gate-service item also checks the installed unit's `PATH`
covers pi's directory.

## How a project runs

**1. Onboard a project** — the product prints every step for *your* project, in order, with
verification and failure exits:

```bash
lybra onboarding guide my-project --repo app=~/code/my-project
lybra project new my-project
lybra project set-repos my-project --repo app=~/code/my-project --dry-run
lybra project set-repos my-project --repo app=~/code/my-project --confirm
```

Every command that writes `project.json` (`set-repos`, `set-execution`, `set-model`, …) is two-phase:
`--dry-run` (default) previews the diff and validation with zero writes; `--confirm` writes and lands
it in the governance repository. `lybra onboarding check my-project --step 8` diagnoses any step.
A Codex advisor session: `lybra onboarding guide my-project --advisor-harness codex`.

**2. Authorize and divide** — one Owner signature per project, then declare who executes what:

```bash
lybra project grant sign my-project --grant-lane app --task-mode code --task-mode docs --driver advisor.my-project.h1 --launch-harness pi --max-tasks 50 --expires-at 2027-01-31T00:00:00Z --confirm
lybra roles enroll-deliver --role auditor --instance audit.my-project.h1 --governance-root ~/.lybra/projects/my-project --workspace ~/lybra-workstations/my-project/auditor --owner-authorization-ref auth-2026-11-01-01 --confirm
lybra project set-execution my-project --subagent-executor exec.my-project.h1 --pi-executor exec.my-project.h1-pi --auditor audit.my-project.h1 --confirm
```

The grant replaces per-card envelopes: lanes × task modes × drivers / hosts / harness launch, plus
total-card and expiry hard bounds (`lybra project grant show` inspects it; `extend` / `revoke` revise
it). `set-execution` declares the division of labor — code cards to the advisor's sub-agent, every
non-code card to the pi executor workstation, audits always to the independent pi auditor.
Enrollment happens with `lybra roles enroll-deliver` (the one-time code never appears on a command
line); workstations pull their charters, skills and extensions with `lybra sync`.

**3. Run cards** — the advisor publishes a card and drives it to completion with one command:

```bash
lybra draft create --from-json card.json
lybra draft publish --path 5_tasks/drafts/myproj-1.md
lybra loop --task-id MYPROJ-1
lybra loop status --task-id MYPROJ-1 --wait 540
```

```
publish → lybra loop: claim (gate builds the card worktree)
            → code card: advisor's sub-agent works from lybra kickoff brief
              non-code card: pi executor workstation (launched by loop, or /go)
            → return ingest → audit dispatch
            → pi auditor workstation: writes the audit report
            → verdict ingest → finalize → close → governance commit
```

Follow and inspect: `lybra loop status --task-id MYPROJ-1 --wait <seconds>` waits (bounded) and
returns the advisor's next action — `continue_wait`, `owner_needed`, `card_done_take_next`,
`investigate` or `advisor_fix`; `lybra agent watch --workspace-root ~/.lybra/projects/my-project` is a
pure-client, bounded filesystem sentinel any bash-capable agent can use (exit codes:
[`docs/agent_watch_exit_codes.md`](docs/agent_watch_exit_codes.md)); `lybra next --task-id MYPROJ-1`
shows a card's next step, `lybra state lint` checks queue / frontmatter / records consistency, and
`lybra brief` gives a cold-start summary. On-demand declarations — card id policy, post-merge
regression time limits, production release targets, the pi model per project, board headlines — are
covered in the [Quick start](QUICKSTART.md).

Other surfaces: `lybra board` (local dashboard, default port 7117; `lybra board open` opens it with a
one-time ticket) and `lybra tui` (optional terminal client; install `textual>=4.0` from PyPI —
`lybra` itself is npm-only). The board's onboarding page also offers **New project** — an Owner
login session previews the directory tree and `project.json` in a throwaway copy (zero writes)
and confirms; same implementation as `lybra project new` plus landing, with the checklist then
marking the next step. On macOS pythons with empty default CA paths, install `certifi` (or set
`SSL_CERT_FILE`) for the TUI's HTTPS; Lybra never disables verification.

## Scope & limits

Honest boundaries: the **resident gate install is productized** — `lybra serve install` (Linux: a
systemd --user unit; macOS: a launchd agent, not yet verified on real macOS hardware) previews with
`--dry-run` and installs with `--confirm`; hand-written units are reported, never taken over. The
unit carries the installer's `PATH` at install time — run `serve install` from a terminal where
`pi` is found, and if pi moves, refresh the unit with `lybra serve uninstall --confirm` then
`lybra serve install --confirm`. Not
yet productized: cross-machine tunnels
(manual ssh configuration), and cloud-platform advisors that cannot reach the governance-root machine
(the advisor must be able to run local commands on, or ssh into, the governance machine). Every
disclosed-deferred / discipline-held item is catalogued in
**[`docs/v1_disclosure.md`](docs/v1_disclosure.md)**.

## Development

Clone this repository, then:

```bash
bash tests/run-all.sh        # resident fixture suite
npm run preflight            # version single-source + packaging check + clean-HOME smoke + full suite
```

## Contributing

Changes that affect workflow gates, persistence boundaries, default ports, release surfaces, or audit
rules stay within the Owner decision flow and are validated before finalize. Keep product changes
file-authoritative and narrowly scoped (see [`docs/release_discipline.md`](docs/release_discipline.md)).

## About

Lybra turns AI from a personal trial tool into a **manageable, reusable, traceable** system you can
hold accountable.

## License

Apache-2.0. See [LICENSE](LICENSE).
