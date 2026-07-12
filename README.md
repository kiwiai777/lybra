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

Lybra is **an accountable single-agent autonomy loop, plus an accountability gate that any MCP
agent can reach via Form B.** It optimizes for **accountability**, not raw autonomy.

Three principles are welded in:

- **Gate, not engine.** Clients (TUI / agents) connect to an Owner-started gate; the gate does not
  run agents or stream model turns on its own.
- **Files are truth.** State lives in durable files, not in compressed conversation memory — it
  outlives the model.
- **Drafter ≠ confirmer ≠ executor.** Planning is read-only; the Owner confirms through the gate; an
  executor does the work. No party collapses into another.

> The model isn't the bottleneck. The harness is.

## Quick start

Lybra's **gate core (init, task/record I/O, and claim/return/audit with canonical opaque
`agent_instance` IDs) ships via npm and is zero Python runtime dependency and correct on bare
python.** Legacy-alias resolution and custom-profile registries require PyYAML; without it the gate
**fails closed (blocks) rather than mis-attributing identity or weakening auditor independence** —
it never silently degrades an accountability decision. The TUI client adds
[Textual](https://pypi.org/project/textual/) on top. **`lybra` itself is distributed via npm and is
NOT on PyPI** — install the TUI's `textual` separately.

**npm end users:**

```bash
npm install -g lybra                 # gate core (Node 18+ and Python 3 on PATH)
pip install "textual>=4.0"          # enable the TUI (textual is on PyPI; lybra is npm-only)
lybra init ./ws --project-id my_project
lybra serve --workspace-root ./ws    # Owner starts the gate (rotates roles, incl. read-only copilot)
lybra tui --gate-url http://127.0.0.1:7118 --workspace-root ./ws --project my_project \
          --llm-base-url <openai-compatible-url> --llm-model <model> --llm-key-env LYBRA_PLANCHAT_LLM_KEY
```

The LLM key is read from the `LYBRA_PLANCHAT_LLM_KEY` environment variable (never passed on the
command line). Without an LLM config, `lybra tui` opens in read-only observe mode.

**Hook up an agent (executor):** give this SKILL to your agent —
`ln -s "$(pwd)/skills/lybra-executor" ~/.claude/skills/lybra-executor` (Claude Code) or
`ln -s "$(pwd)/skills/lybra-executor" ~/.codex/skills/lybra-executor` (Codex). The agent then says
**`lybra on`** (plain text, no leading slash — Claude Code's slash-command resolver only
recognizes registered command names like `/lybra-executor`, not an arbitrary `/lybra`, so a typed
`/lybra on` fails; the bare phrase triggers the skill's natural-language match instead) to start
pulling for claimable tasks (`lybra agent watch` — a stateless, agent-side, bounded foreground
loop; Lybra never pushes and never tracks agent presence) and **`lybra off`** to stop. See
`skills/lybra-executor/SKILL.md` and `docs/mcp-agent-setup.md`.

**macOS TLS note:** bare macOS venv pythons ship empty default CA paths, so copilot HTTPS fails
`CERTIFICATE_VERIFY_FAILED` (an environment property of macOS pythons, not a Lybra defect). Install
`certifi` into the TUI's python (or set `SSL_CERT_FILE`) — Lybra picks certifi up automatically
(an explicit `SSL_CERT_FILE`/`SSL_CERT_DIR` always wins) and **never disables verification**.

The TUI chat box accepts non-Latin / CJK input (Chinese, Japanese, Korean, etc.) — **both typing via
an IME and pasting work.** (Earlier builds could only paste CJK: Textual's kitty-keyboard-protocol
`REPORT_ASSOCIATED_TEXT` parsing dropped IME-typed CJK to an empty character. Lybra now enables the
kitty protocol with **DISAMBIGUATE only**, so direct CJK typing works **and** Shift+Enter is
preserved.) The TUI runs with **mouse capture off** — so your terminal keeps native mouse
(selection, scrollback, and Cmd/Ctrl+C copy of any text), exactly like Claude Code; the `/` menu is
keyboard-navigable (↑/↓ + Enter). *Verified on Linux + macOS (incl. iTerm2 → SSH → WSL); Windows is
out of scope.*

**Source / dev (from a clone):**

```bash
git clone https://github.com/kiwiai777/lybra && cd lybra
pip install ".[tui]"                 # installs the textual extra
python3 -m unittest discover -s tools -p "test_*.py"
```

## Capabilities (v1.0)

- **Chat-to-task first screen.** Launch the TUI, describe a task in one sentence, and the read-only
  Planning Copilot drafts a **conformant** task card — its publishable structure is guaranteed by
  code, not by LLM luck.
- **Read-only Planning Copilot.** The copilot holds no write/confirm/publish scope (it connects with
  a `scopes: []` role); every mutation it could attempt is structurally denied at the gate.
- **Owner-gated publish.** The only path to truth is `draft → Owner proceed → gate confirm`; the
  publish record attributes the confirming Owner (`confirmer_role=owner`).
- **Supervised closed loop.** Every truth mutation passes an Owner confirm; an executor can never
  self-confirm or audit its own work.
- **Form A / Form B.** Form A is the supervised single-harness loop (Claude); Form B lets any MCP
  agent reach the same accountability gate.

## How it works

```
draft (read-only)  →  Owner proceed  →  gate confirm (OWNER_CONFIRMED)  →  written to files
                                            │
                                            └─ executor claim → work → return → independent audit → L3 VALID
```

The executor and the auditor are **different parties**; nothing is finalized without an audit pass.
Workspace commands auto-discover the workspace from the current directory upward; explicit flags and
`AIPOS_WORKSPACE_ROOT` override discovery.

## Scope & limits

Lybra v1.0 is deliberately scoped. Every disclosed-deferred / discipline-held item — RF-3, gate
signing (§9), CLI publish, scope exemptions, network egress, autonomy modes, the single-harness Wall,
heterogeneous mutual audit, and the LLM key — is catalogued honestly, with the structure or
discipline that holds it and the plan to address it, in:

- **[`docs/v1_disclosure.md`](docs/v1_disclosure.md)** — the honest disclosure ledger.
- **[`docs/v1_acceptance_runbook.md`](docs/v1_acceptance_runbook.md)** — the manual release
  walkthrough (every mutating gate is Owner-out-of-band).

Automated acceptance gate (no confirm needed):

```bash
python -m tools.acceptance.v1_acceptance   # expect: ACCEPTANCE: PASS
```

Lybra is **not** a "heterogeneous accountability loop" — heterogeneous dual-harness mutual audit is
deferred (see the ledger).

## Contributing

Changes that affect workflow gates, persistence boundaries, default ports, release surfaces, or audit
rules stay within the Owner decision flow and are validated before finalize. Keep product changes
file-authoritative and narrowly scoped (see [`docs/release_discipline.md`](docs/release_discipline.md)).

## About

Built by **KIWIAI**. Lybra turns AI from a personal trial tool into a **manageable, reusable,
traceable** system you can hold accountable.

## License

Apache-2.0. See [LICENSE](LICENSE).
