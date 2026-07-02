# Lybra v1.0 — macOS release-gate runbook (AIPOS-220 Track 2; extended by AIPOS-235 v2)

Owner-run, out-of-band. Mirrors the Linux/WSL2 Track-1 run on macOS to confirm the **install
product** is correct on a Mac. Reads as concrete commands; every mutating gate is **Owner-confirmed**.

> **AIPOS-235 (release-gate v2) extension.** R0–R6 + O3 below are the AIPOS-220 baseline. The
> **New surfaces (N0–N7)** section near the end adds the post-220 product: dual-root / R2,
> topology-C, `home git-init`, `serve rotate --project`, `/project switch`, `/agents`, the 2-role
> flow, copilot chat, and the **#2 transport-flake re-measurement on macOS** (fills the WSL2 gap).

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
  --llm-base-url https://goswitch.online/v1 --llm-model claude-sonnet-4-6 --llm-key-env LYBRA_PLANCHAT_LLM_KEY
```
With an LLM config the **first screen is copilot** (status line ends `· mode copilot`; no Shift+Tab
needed). Confirm (AIPOS-221 plan-chat UX): **no `build_app` TypeError at launch** (AIPOS-216), the
banner renders, and **typing one sentence in plain language (no `draft` prefix) and pressing Enter**
produces a conformant card — with an **immediate working spinner, no freeze** (the LLM call runs off
the event loop). Typing `/` shows the command autocomplete; `/help` lists commands. Each step prints
an explicit next-step line. **`/proceed`** lands the draft + stages a publish dry-run; it does **not**
publish — the line says so and that is the OOB Owner confirm. `/mode [observe|confirm|copilot]`
switches mode without Shift+Tab. `Ctrl+C` to quit.

**CJK input (AIPOS-237):** the chat box accepts Chinese/Japanese/Korean by **direct IME typing AND
paste.** (Earlier builds only pasted: Textual's kitty-protocol `REPORT_ASSOCIATED_TEXT` parsing
dropped IME-typed CJK to an empty character. Lybra now enables the kitty protocol **DISAMBIGUATE
only**, so typing works and Shift+Enter is preserved.) The TUI runs with **mouse capture off**
(`run(mouse=False)`), so iTerm2 keeps native selection + scrollback + Cmd/Ctrl+C copy of any text —
Claude-Code parity. Verify on the npm-installed prefix: type 中文 directly (it lands); Shift+Enter and
Ctrl+J both newline; **select any text with the mouse and Cmd+C copies it**. Quit = **Ctrl+C** /
`/quit`.

## New surfaces (post-AIPOS-220 — AIPOS-235 release-gate v2)

> Run these on the same installed `$PKG` / `$WS` from R0 (tarball product, NOT a dev tree), on a
> **clean `/tmp`** (a stray `/tmp/.git` makes `home git-init` refuse — the AIPOS-233 diagnostic will
> name it: *"stray ancestor .git at … NOT a home_git regression"*; remove it, it is an env problem).

### N0 — R-2 gate step: shipped == tested (the standing three-part proof; run on Linux closeout AND macOS)
The npm tarball ships **product code + the acceptance module, but NOT the unit test suite**
(`tools/**/tests/`). So "run the full suite from the installed prefix" is **not literal** — the
shipped acceptance's test-driven anchors report `NO TESTS RAN` from the install. **Owner verdict
(F-rg2-1 CLOSED, as-designed):** do NOT ship the unit suite into the tarball (non-standard + bloat);
**byte-identity to the tested dev source is the stronger guarantee.** The standing R-2 proof is these
**three parts**, re-run every release:
```bash
# (1) shipped product code is byte-identical to the green-tested dev source — PIN the file count:
diff -r --exclude=tests --exclude=__pycache__ --exclude='*.pyc' <repo clone>/tools "$PKG/tools"
echo "exit $? — EMPTY diff == shipped == tested"
find "$PKG/tools" -name '*.py' | grep -vE 'tests|__pycache__' | wc -l   # expect 53 (pin; investigate any change)
# (2) the self-contained correctness probe runs on the INSTALLED product with ALL third-party blocked:
PYTHONPATH="$PKG" "$LYBRA_PYTHON" -c "import os;os.chdir('$PKG'); \
import tools.acceptance.v1_acceptance as A; print(A.check_isolation_textual_absent())"
# expect: (True, 'gate imports + runs with ALL third-party blocked; correctness A/B/C pass')
# (3) acceptance-on-installed: the self-contained checks pass from the install; the test-driven
#     anchors + full-suite report NO TESTS RAN — that is EXPECTED (tests not shipped), not a failure:
PYTHONPATH="$PKG" python3 -c "import os;os.chdir('$PKG'); \
import runpy; runpy.run_module('tools.acceptance.v1_acceptance', run_name='__main__')" 2>&1 | \
  grep -E 'dependency isolation|correctness|NO TESTS RAN' || true
```
*(Linux WSL2 result folded into the gate: `diff` empty over **53** product .py; probe `True`;
acceptance-on-installed = isolation + A/B/C PASS, test-driven anchors `NO TESTS RAN` as expected.)*

### N1 — dual-root + R2
```bash
export LYBRA_HOME_ROOT=~/lybra-rg-mac-home        # truth home = project truth ONLY
ls -la ~/.lybra ~/.lybra/local/connection.json    # config + token live here (NOT in the truth tree)
stat -f '%Lp' ~/.lybra/local/connection.json      # expect 600
# no secret leaks into the truth tree (grep the project truth for any token fingerprint → none):
grep -rIl "$(shasum -a 256 ~/.lybra/local/connection.json | cut -c1-12)" "$LYBRA_HOME_ROOT" || echo "no secret in truth tree — OK"
```
**Evidence:** config/token under `~/.lybra`; truth tree carries **no secret**; `600`.

### N2 — topology-C (home inside an existing repo → refuse to nest)
```bash
mkdir -p ~/tc-outer && (cd ~/tc-outer && git init -q)     # an existing repo
mkdir -p ~/tc-outer/home
"$PREFIX/bin/lybra" home git-init --home-root ~/tc-outer/home    # MUST refuse
# expect: ALREADY_IN_GIT_REPO — Lybra will not nest a git repo (commit via the existing repo)
```

### N3 — `lybra home git-init` (topology A / B, one-shot, no remote/push)
```bash
FRESH=~/lybra-rg-mac-home2; rm -rf "$FRESH"; mkdir -p "$FRESH/proj/5_tasks/queue/pending"
"$PREFIX/bin/lybra" home git-init --home-root "$FRESH"           # one commit, NO remote, NO push
(cd "$FRESH" && git log --oneline && git remote)                # exactly 1 commit; empty remote
"$PREFIX/bin/lybra" home git-init --home-root "$FRESH"           # re-run MUST refuse: HOME_ALREADY_GIT
```
**Evidence:** one commit / no remote / no push; second run refused. (Copilot, scopes `[]`, can never
invoke this — Owner-only.)

### N4 — `serve rotate --project` (enforced project token)
```bash
"$PREFIX/bin/lybra" serve --workspace-root "$WS" rotate --project rgmac --json | \
  python3 -c "import sys,json;d=json.load(sys.stdin);print('projects_enforced=',d.get('projects_enforced'))"
stat -f '%Lp' "$WS/.lybra/local/connection.json"   # 600; tokens fingerprint-only
```
A call scoped to another project must return **`PROJECT_SCOPE_DENIED`** (enforcement, 18 gated / 0
exempt). **Evidence:** `projects_enforced=True`; cross-project → denied; `600`.

### N5 — `/project switch` + `/agents` (TUI — Owner O3)
In the TUI (O3 launch below/above): `/project` lists projects; `/project switch <name>` is a **local
Owner action** (rebinds the active project; an out-of-scope op then surfaces `PROJECT_SCOPE_DENIED` —
no client-side fake isolation). `/agents` prints a **read-only snapshot grouped by agent**, labelled
**"as recorded — Lybra does not track live presence"**; it renders **once** (no auto-refresh / timer).

### N6 — 2-role flow + copilot chat (O3)
Run one **complex-class** task end to end: executor claims → returns; an **independent auditor**
(a *distinct* canonical instance) dispatches + renders the verdict — same-instance would fail-closed
(`INDEPENDENCE_FAILED` / `INDEPENDENCE_UNVERIFIABLE_NO_REGISTRY`). The complexity **suggestion
recommends** 2-role (never auto-selects). copilot chat produces a conformant card (writes nothing).

### N7 — #2 transport-flake re-measurement (macOS = the multi-environment fill)
Tests are not shipped (N0), so run from a **repo clone** on macOS (bare venv), multi-round:
```bash
cd <repo clone>; f=0; for i in $(seq 1 30); do \
  PYTHONPATH=. ~/lybra-bare-venv/bin/python -m unittest tools.mcp_server.tests.test_http_sse_transport \
  >/dev/null 2>&1 || { f=$((f+1)); echo "round $i FAIL"; }; done; echo "#2 macOS flake: $f/30"
```
**WSL2 measured 0/30 (clean `/tmp`).** Record the macOS rate. **If it reproduces on macOS, THIS is the
reproducible environment → characterize the exact root cause on the spot** (the accept-race is already
refuted; do not assume). If 0/30 again, record honestly — **#2 stays OPEN** until it reproduces
somewhere measurable; do **not** claim it fixed.

### Form A Wall (confined_worker) — Linux-only (unchanged)
Not exercised on macOS (container/uid isolation). No macOS evidence expected for it.

## What to report back (evidence)
Per step: the command's key output (fingerprint-only) + the on-disk re-check. Specifically:
`lybra init` ok on bare python; `ACCEPTANCE: PASS`; connection.json `600`; MCP `401`; R3 conformant
card + your 5-anchor scores; R4 `confirmer_role: owner`; R6 **L3 VALID** (and any audit-independence
**fail-closed BLOCK** noted as correct); O3 TUI launches (no crash) + `draft`→card. Note any macOS
finding (path/shim/keychain/terminal) — register, do not hard-fix.

**New surfaces (N0–N7):** N0 `diff` empty (shipped == tested) + probe `True`; N1 config/token under
`~/.lybra`, no secret in the truth tree, `600`; N2 `ALREADY_IN_GIT_REPO` refusal; N3 one-commit /
no-remote / second-run `HOME_ALREADY_GIT`; N4 `projects_enforced=True` + cross-project
`PROJECT_SCOPE_DENIED` + `600`; N5 `/project switch` + `/agents` one-shot read-only + "not live"
label; N6 2-role independent-auditor pass (+ same-instance fail-closed noted correct) + suggestion
recommends-not-selects; **N7 the macOS `#2` flake rate `f/30`** (fills the WSL2 `0/30`). Form A Wall:
no macOS evidence (Linux-only).

## Cleanup (after evidence captured)
Stop the gate; remove `/tmp/lybra-rg-mac-prefix`, the TUI textual venv if disposable, the tarball, and
`~/.lybra_planchat_key`. Keep the workspace + your evidence until publish.
