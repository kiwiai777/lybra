<p align="center">
  <img src="docs/assets/lybra-banner.png" alt="Lybra — the accountability harness for AI agents" width="880">
</p>

<p align="center">
  <b>The accountability harness for AI agents.</b><br/>
  面向 AI Agent 的治理型 harness —— 文件为真相源，Owner 掌握每一道决策闸门，每一步皆可审计、可复现。
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/lybra"><img alt="npm" src="https://img.shields.io/npm/v/lybra?color=1A7A52&amp;label=npm"></a>
  <img alt="node" src="https://img.shields.io/node/v/lybra?color=1A7A52">
  <img alt="license" src="https://img.shields.io/github/license/kiwiai777/lybra?color=1A7A52">
  <img alt="status" src="https://img.shields.io/badge/status-early%20access-1A7A52">
</p>

---

## What is Lybra

Lybra keeps **one project truth in files** for long-running, multi-role AI projects — cards, records,
verdicts and decisions live in a governance root, not in any agent's memory. Agents come and go; the
truth and the gate stay. It optimizes for **accountability**, not raw autonomy.

Three principles are welded in:

- **Gate, not engine.** The gate does not run or wake agents. The Owner starts the gate; agents run in
  their own harness (Claude Code, pi, Codex …) and only produce artifacts. A workstation harness is
  started either by the Owner (`/go`) or — only when the Owner's envelope explicitly authorizes it — by
  the advisor's `lybra loop`.
- **Files are truth.** State lives in durable files, not in compressed conversation memory — it
  outlives the model.
- **Drafter ≠ executor ≠ auditor.** The advisor drafts and drives; an executor does the work; an
  independent auditor judges it. **Executors and auditors never touch the gate** — every gate action
  (claim, return, dispatch, verdict, finalize, close) is done by the product.

> The model isn't the bottleneck. The harness is.

## Install

```bash
npm install -g lybra        # Node 18+ and Python 3 on PATH; the gate core has no Python dependencies
lybra --help
```

## How a project runs

| Who | Does |
|---|---|
| **Owner** | Starts the gate (`lybra serve`), issues enrollment codes, signs autonomy envelopes (`lybra envelope mint`), and per card either presses **`/go`** in the workstation (manual mode, the default) or authorizes `lybra loop` to start the workstation harness (`lybra envelope mint … --launch-harness pi`). |
| **Advisor** (a Claude Code session with the advisor skills) | Onboards the project, drafts and publishes cards, and advances each card with **`lybra loop`**. |
| **Executor / auditor workstations** | Opened with `/go`, or launched by an authorized `lybra loop`; commit on the card branch and write the report to the project's declared location. No gate verbs, no credentials pasted. |

**1. Onboard a project** — the product prints every step for your project, in order, with failure exits:

```bash
lybra onboarding guide my_project            # steps 1–9: who runs each command, how to verify it
lybra project new my_project                 # step 1: governance root + project.json under the home root
lybra project set-repos my_project --repo app=/abs/path/to/app   # step 2: declare product repos
```

The guide's remaining steps cover the Owner's two one-time actions (an advisor enrollment code and
one command that signs the envelopes), advisor and workstation enrollment (`lybra roles enroll`), and
distribution (`lybra sync`). `lybra onboarding check my_project --step <n>` diagnoses any step.

**2. Run a card** — the advisor publishes a card and drives it to completion with one command:

```bash
lybra draft create --from-json card.json
lybra draft publish --path 5_tasks/drafts/<card>.md
lybra loop --task-id <CARD-ID>
```

```
publish → lybra loop: claim (gate builds the card worktree)
            → executor workstation (/go, or launched by loop): commits on card/<ID> + writes RETURN
            → return ingest → audit dispatch
            → auditor workstation (/go, or launched by loop): writes the audit report
            → verdict ingest → finalize → close → governance commit (N6)
```

Two kickoff modes (AIPOS-F95):

- **Manual (default).** On an agent step `lybra loop` prints which workstation to open, waits for the
  artifact, and exits 3 if it is not there yet — press `/go` in that workstation and re-run the same
  command.
- **Authorized launch.** If the Owner agreed and minted the envelope with `--launch-harness pi`, the
  loop starts the harness in the (local) workstation once per agent step from the declared launch
  template, shows a one-line progress summary, and cleans up the whole process group when the artifact is
  ready or on timeout / early exit / interrupt. `lybra loop --no-launch` forces manual mode; a
  workstation on another machine always falls back to manual.

Exit codes are declared in `schema/verbs.schema.json` (`lybra_loop.exit_codes`).

**3. Wait and inspect** — `lybra agent watch --workspace-root <governance-root>` is a pure-client,
bounded filesystem sentinel any bash-capable agent can use (exit codes:
[`docs/agent_watch_exit_codes.md`](docs/agent_watch_exit_codes.md)). `lybra next --task-id <ID>`
shows the next step of a card, `lybra state lint` checks queue / frontmatter / records consistency
(including truth not yet committed), and `lybra brief` gives a cold-start summary.

Other surfaces: `lybra board` (local dashboard, default port 7117) and `lybra tui` (optional terminal
client; install `textual>=4.0` from PyPI — `lybra` itself is npm-only). On macOS pythons with empty
default CA paths, install `certifi` (or set `SSL_CERT_FILE`) for the TUI's HTTPS; Lybra never disables
verification.

## Legacy entry points (retirement pending)

These still exist in the CLI but are **not** part of the flow above; follow-up cards remove them and
this section together.

- **Old workspace bootstrap** — superseded by `lybra onboarding guide` + `lybra project new`:

  ```bash
  lybra init ./ws --project-id my_project
  ```

- **Old agent-side pull connector (AIPOS-248)** — `skills/lybra-executor` had an agent say
  **`lybra on`** / **`lybra off`** to poll the gate for claimable tasks. Executors no longer claim
  work; claiming is done by the advisor's `lybra loop`; workstations are opened with `/go` or launched by an
  authorized `lybra loop`.
- **Earlier gate-console skills** — `skills/owner-console/` (Owner console with the owner token) and
  `skills/lybra-planner/` (third-party planner: read-only truth + draft-submit) predate `lybra loop`;
  the advisor skills now ship through `lybra sync` from `agents/skills/`.

## Scope & limits

Every disclosed-deferred / discipline-held item is catalogued honestly, with the structure or
discipline that holds it, in **[`docs/v1_disclosure.md`](docs/v1_disclosure.md)**.

## Development

```bash
git clone https://github.com/kiwiai777/lybra && cd lybra
bash tests/run-all.sh                # resident fixture suite
```

## Contributing

Changes that affect workflow gates, persistence boundaries, default ports, release surfaces, or audit
rules stay within the Owner decision flow and are validated before finalize. Keep product changes
file-authoritative and narrowly scoped (see [`docs/release_discipline.md`](docs/release_discipline.md)).

## About

Built by **KIWIAI**. Lybra turns AI from a personal trial tool into a **manageable, reusable,
traceable** system you can hold accountable.

## License

Apache-2.0. See [LICENSE](LICENSE).
