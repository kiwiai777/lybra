# Lybra v1.0 — macOS release-gate runbook (AIPOS-220 Track 2)

Owner-run, out-of-band. Mirrors the Linux/WSL2 Track-1 run on macOS to confirm the **install
product** is correct on a Mac. Reads as concrete commands; every mutating gate is **Owner-confirmed**.

> **Red line:** the owner token is read at runtime from `connection.json` by role — never typed on a
> command line. Tokens/keys fingerprint-only in any output. Use a **fresh disposable** workspace; do
> not touch any evidence workspace. **Form A Wall (confined_worker) is NOT covered on macOS**
> (Linux-only container/uid features) — this runbook covers gate / copilot / TUI / Form B only.

## ★ Bare-python condition matrix (do not mis-judge)
| Path | Bare python (NO PyYAML) | Verdict |
|---|---|---|
| init / serve / claim / return / publish / L3 | must be correct, zero-dep | core — error = FAIL |
| audit-verdict independence (if triggered) | **fail-closed BLOCK = CORRECT** | not a gate failure; a false PASS would be |
| legacy-alias resolution | needs PyYAML, else BLOCK | expected |
| TUI (O3) | needs `pip install textual` | separate from the zero-dep core |

## Prereqs (macOS)
- Node 18+ and a Python 3.10+ on PATH (`node -v`, `python3 -V`).
- A **bare** Python venv with **NO PyYAML** for the gate (proves zero-dep), and (separately) Textual
  for the TUI:
```bash
python3 -m venv ~/lybra-bare-venv          # gate interpreter: NO PyYAML, NO textual
~/lybra-bare-venv/bin/python -c "import yaml" 2>&1 | tail -1   # must say: No module named 'yaml'
```
- LLM key in a 0600 file (fingerprint-only; never echo):
```bash
printf '%s' 'sk-...your-key...' > ~/.lybra_planchat_key && chmod 600 ~/.lybra_planchat_key
shasum -a 256 ~/.lybra_planchat_key | cut -c1-12     # record the fingerprint only
```

## R0 — install the product on bare python, assert correctness (NO PyYAML)
```bash
cd <lybra repo clone>
npm pack                                            # -> lybra-0.2.0.tgz
PREFIX=/tmp/lybra-rg-mac-prefix; rm -rf "$PREFIX"; mkdir -p "$PREFIX"
npm install -g --prefix "$PREFIX" ./lybra-0.2.0.tgz
PKG="$PREFIX/lib/node_modules/lybra"
export LYBRA_PYTHON=~/lybra-bare-venv/bin/python    # the gate runs on bare python
WS=~/lybra-rg-mac-workspace; rm -rf "$WS"
"$PREFIX/bin/lybra" init "$WS" --project-id rgmac   # must succeed with NO PyYAML
```
Correctness A/B (bare parse must equal a PyYAML baseline) — run the repo's bare-python checks:
```bash
# A/B/C are also asserted by the acceptance probe (blocks ALL third-party):
PYTHONPATH=<repo> python3 -m tools.acceptance.v1_acceptance     # expect: ACCEPTANCE: PASS
```
**Evidence:** `lybra init` succeeds on bare python; `ACCEPTANCE: PASS` (correctness A/B/C with all
third-party blocked).

## R1 — serve (bare python)
```bash
"$PREFIX/bin/lybra" serve --workspace-root "$WS" rotate --json   # mints executor/owner/copilot…
stat -f '%Lp' "$WS/.lybra/local/connection.json"                 # expect 600
"$PREFIX/bin/lybra" serve --workspace-root "$WS" start &         # gate on 127.0.0.1:7118
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7118/mcp -X POST -d '{}'   # 401 = up+auth
```
**Evidence:** rotate PASS; connection.json `600`; MCP `401`; serves with NO PyYAML.

## R2 — first screen (bounded)
Banner + copilot-first-screen are verified by the suite; the live screen is R3/O3.

## R3 — real-LLM conformant card (bare python)
Use the copilot via the installed product on bare python (copilot uses urllib, not PyYAML). Either
through the TUI (O3 below) or a short driver; the card must be **conformant** and write **nothing**.
**Owner judges the 5 quality anchors:** field semantics fit; title on-point + body actionable;
`context_bundle` not fabricated (surfaced as needs_bundle); passes `draft_publish_dry_run`; no
hallucinated fields / no secrets.

## R4 — gated publish **[Owner OOB]**
With the card staged under `5_tasks/drafts/`, confirm with the owner token (read at runtime by role):
```bash
PYTHONPATH="$PKG" "$LYBRA_PYTHON" - "$WS/.lybra/local/connection.json" 5_tasks/drafts/<card>.md <<'PY'
import sys
from tools.aipos_cli.confirm_client import GateClient, load_owner_token
cj, draft = sys.argv[1], sys.argv[2]
c = GateClient("http://127.0.0.1:7118", load_owner_token(connection_json=cj, role="owner")); c.initialize()
print("owner token fp:", c.token_fingerprint)
prev = c.preview("publish", {"path": draft, "actor": "owner"})
print("ok:", c.confirm(prev, "OWNER_CONFIRMED").get("ok"))
PY
```
**Verify on disk:** task in `5_tasks/queue/pending/`; publish record `confirmer_role: owner`.

## R5 — executor claim **[Owner OOB]**
The executor proposes a claim dry-run; the Owner confirms with the owner token (`role="owner"`,
`lybra_queue_claim_confirm`, `owner_confirmation_token: OWNER_CONFIRMED`). **Verify:** pending→claimed;
claim record `confirmer_role: owner`. (★A1: the executor cannot self-confirm.)

## R6 — return → L3 VALID **[Owner OOB]**
Executor return dry-run (pass `owner_policy_ref: owner_policy:supervised`) → Owner confirms with the
owner token (`lybra_queue_return_confirm`). Then the L3 authority scan must report the task **VALID**.
**★ If an audit-verdict / independence path is exercised on bare python and it BLOCKs
(`INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`), that is the CORRECT fail-closed outcome — not a failure.**
To show a *passing* independent audit, run that one segment with PyYAML installed.

## O3 — TUI real launch (the AIPOS-216 fix)
```bash
pip3 install "textual>=0.50"                          # into the python the TUI will use
export LYBRA_PLANCHAT_LLM_KEY="$(cat ~/.lybra_planchat_key)"
unset LYBRA_PYTHON                                    # use the python that has textual
"$PREFIX/bin/lybra" tui --gate-url http://127.0.0.1:7118 --workspace-root "$WS" --project rgmac \
  --llm-base-url https://xchai.xyz/v1 --llm-model claude-sonnet-4-6 --llm-key-env LYBRA_PLANCHAT_LLM_KEY
```
With an LLM config the **first screen is copilot** (status line ends `· mode copilot`; no Shift+Tab
needed). Confirm (AIPOS-221 plan-chat UX): **no `build_app` TypeError at launch** (AIPOS-216), the
banner renders, and **typing one sentence in plain language (no `draft` prefix) and pressing Enter**
produces a conformant card — with an **immediate working spinner, no freeze** (the LLM call runs off
the event loop). Typing `/` shows the command autocomplete; `/help` lists commands. Each step prints
an explicit next-step line. **`/proceed`** lands the draft + stages a publish dry-run; it does **not**
publish — the line says so and that is the OOB Owner confirm. `/mode [observe|confirm|copilot]`
switches mode without Shift+Tab. `Ctrl+C` to quit.

**CJK input:** the chat box accepts Chinese/Japanese/Korean. **Pasting CJK always works** — that is
the proof the app itself accepts wide characters (Lybra applies no input restriction). *Typing* CJK
via an IME requires a CJK-capable terminal + IME (Terminal.app / iTerm2 deliver it on macOS). If
pasted Chinese appears but typed Chinese does not, the blocker is the host terminal/IME, not Lybra —
register it as a finding, do not patch the app.

## What to report back (evidence)
Per step: the command's key output (fingerprint-only) + the on-disk re-check. Specifically:
`lybra init` ok on bare python; `ACCEPTANCE: PASS`; connection.json `600`; MCP `401`; R3 conformant
card + your 5-anchor scores; R4 `confirmer_role: owner`; R6 **L3 VALID** (and any audit-independence
**fail-closed BLOCK** noted as correct); O3 TUI launches (no crash) + `draft`→card. Note any macOS
finding (path/shim/keychain/terminal) — register, do not hard-fix.

## Cleanup (after evidence captured)
Stop the gate; remove `/tmp/lybra-rg-mac-prefix`, the TUI textual venv if disposable, the tarball, and
`~/.lybra_planchat_key`. Keep the workspace + your evidence until publish.
