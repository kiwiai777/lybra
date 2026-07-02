# AIPOS-239 (F-launch) — simplify `~/o3-launch.sh` teardown/clean-slate (drop `fuser`, no raw wrapper-kill)

- **status: applied** — R direction-audit PASS; the diff below was applied to `~/o3-launch.sh`
  (Owner tool). No product code changed. This doc is the record of the change + its self-repro basis.
- **authority: Owner-tool only** — touches ONLY `~/o3-launch.sh` (NOT in the `lybra` repo); the
  `lybra` product is byte-unchanged. Basis: self-repro on WSL (build f2589a9, §1/§4). Live macOS
  confirmation is PENDING — the Owner runs `docs/v1_macos_track2_exercise.md` (native Mac, no fuser).
- **parent:** AIPOS-238 follow-up **F-launch** (roadmap:1664). Premise: 238 made `serve stop`
  project-agnostic and the supervisor reap children on SIGTERM/SIGHUP.
- **scope:** teardown + clean-slate of the launcher. Product = **none**. Goal: macOS-native
  (no `fuser`) can run twice incl. a mid-run exit → no 401 / no orphans.

## §0 Context

The launcher (`~/o3-launch.sh`) backgrounds `serve start` and records its PID as `$SERVE_PID`.
Two spots use platform/mechanism-fragile teardown:
- **teardown()** (lines 47–62): `serve stop --workspace-root $HOME_DIR/$PROJECT_A ...` **then raw
  `kill "$SERVE_PID"`**.
- **clean-slate** (lines 170–173): `serve stop --connection-json ...` **then `fuser -k
  <ports>`** (macOS has no `fuser`).

## §1 Self-reproduction findings (build f2589a9, on THIS WSL) — the design driver

I reproduced the two teardown mechanisms against the 238-fixed build, on **both** the bash-function
path and the **real node `bin/lybra` wrapper** (the launcher's actual ship path):

| teardown mechanism | board+mcp after | ports after | verdict |
|---|---|---|---|
| **`serve stop --connection-json` ONLY** (project-agnostic; no `--workspace-root`, no `fuser`, no raw kill) | **DEAD** | **free** | ✅ clean; supervisor self-exits (238 C1b: children killed-by-signal → returncode `<0` → PASS → loop breaks → process exits) |
| **`kill -TERM $SERVE_PID`** where `$SERVE_PID` = the backgrounded **node wrapper** | **ALIVE (orphaned)** | **LISTEN (held)** | ❌ node dies; the python supervisor is its *child* and never receives the signal (`spawnSync` does not forward a directed SIGTERM), so 238's handler never fires → board+mcp orphaned |
| **`serve stop` after node-wrapper start** | **DEAD** | **free** | ✅ clean (same as row 1) |

**Conclusion:** teardown must go through **`serve stop`** (which SIGTERMs the *recorded child PIDs*
directly, wrapper-indirection-immune), NOT through a directed `kill` of the backgrounded wrapper PID.
The current launcher only escapes orphaning because it happens to run `serve stop` *before* the raw
`kill`; the raw `kill` is dead weight that would orphan if it ever ran alone. Evidence scripts:
`/tmp/flaunch-selfrepro.sh` (bash-fn: A=PASS, B[SIGTERM-to-bg]=FAIL-orphan, C[restart]=PASS) and
`/tmp/flaunch-node-wrapper.sh` (node: SIGTERM-to-wrapper orphans=YES, serve-stop orphans=no).

## §2 Proposed diff (Owner tool only — reviewable by R)

### (a) teardown() — lines 50–56
```sh
  if [[ -n "$SERVE_PID" ]] && kill -0 "$SERVE_PID" 2>/dev/null; then
    echo "stopping serve (pid $SERVE_PID) via project-agnostic \`serve stop\` …"
    # AIPOS-238: `serve stop` locates service_state.json via --connection-json alone (no project),
    # SIGTERMs the recorded service_owned board+mcp PIDs; the supervisor then self-exits (C1b).
    LYBRA_PYTHON="$BARE_PY" "$LYBRA" serve --connection-json "$CONN_JSON" stop >/dev/null 2>&1 || true
    # let the backgrounded supervisor notice its signalled children and exit on its own.
    for _ in $(seq 1 20); do kill -0 "$SERVE_PID" 2>/dev/null || break; sleep 0.25; done
    wait "$SERVE_PID" 2>/dev/null || true
  fi
```
Removed: the `--workspace-root` arg (project-agnostic now) and the trailing **`kill "$SERVE_PID"`**
(a directed SIGTERM to the node wrapper — proven to orphan children; unnecessary since `serve stop`
already reaps them and the supervisor self-exits).

### (b) clean-slate — lines 170–173
```sh
echo "── clean slate: stop any prior serve via \`serve stop\` (no fuser; F-o3-13 / F-launch) ──"
LYBRA_PYTHON="$BARE_PY" "$LYBRA" serve --connection-json "$CONN_JSON" stop >/dev/null 2>&1 || true
sleep 1
```
Removed: the `fuser -k <ports>` line entirely (macOS has no `fuser`). A genuinely orphaned prior
process holding a port is now caught **loudly by the product** — 238 D1 `_ports_in_use` makes `serve
start` BLOCK naming the port instead of a silent stale-server 401. (With this teardown, a clean prior
run leaves nothing to kill anyway.)

## §3 Follow-up finding to register (NOT fixed here) — F-wrapper-sig

The node CLI wrapper (`bin/lybra`) does **not** forward a *directed* `SIGTERM`/`SIGHUP` to the python
child (`spawnSync`, stdio inherit; it only re-raises a signal the child *already* died from). So
`kill -TERM <lybra-pid>` on a serve started via the node CLI orphans board+mcp. Interactive **Ctrl-C
is unaffected** (SIGINT goes to the whole foreground process group → python gets it → 238 reap). The
documented non-interactive teardown is `serve stop`, which is reliable. Register as **F-wrapper-sig
(OPEN, low priority, product sharp-edge)**; the launcher fix sidesteps it. R to weigh whether a
future product slice should make `bin/lybra` forward directed signals.

## §4 Red lines (R make-or-break)

- **No product code.** `git diff` in the `lybra` repo = at most this DRAFT doc (product = none).
  The behavior change lives in `~/o3-launch.sh` (Owner tool, not versioned here).
- **No `fuser`** anywhere in the launcher (macOS-native must work).
- **No directed `kill` of the backgrounded `$SERVE_PID`** (the wrapper) — teardown via `serve stop`
  only; `wait` lets the supervisor self-exit.
- `serve stop` stays **project-agnostic** (`--connection-json` only) and kills **only**
  `service_owned` PIDs (238 invariant, unchanged).
- Nothing else in the launcher changes (preconditions / scaffold / rotate / readiness probe / TUI).

## §5 Verify plan (Owner runs after R PASS)

On the fixed launcher, **on macOS** (npm prefix; no `fuser`):
`REBUILD=1 ~/o3-launch.sh` → quit the TUI (Ctrl-C) → **immediately** `REBUILD=1 ~/o3-launch.sh` again.
Expect: second run's clean-slate finds ports free (no `fuser` needed), authenticated-200 readiness
passes, **no 401**; after the second quit, no orphaned board/mcp holding `:7117/:7118`. A mid-run
`kill -TERM` of the launcher process is out of scope (F-wrapper-sig; use Ctrl-C or `serve stop`).

## §6 R direction-audit hooks

- Confirm the self-repro evidence supports "serve-stop-only teardown, no wrapper-kill, no fuser" — and
  that the node-wrapper orphan finding is characterized correctly (spawnSync directed-signal gap).
- Confirm the clean-slate relies on 238 D1 (loud BLOCK) as the backstop for a genuinely stale port,
  rather than silently `fuser`-killing — i.e. the product now owns that safety.
- Weigh F-wrapper-sig: register-only vs. a future `bin/lybra` signal-forward product slice.
- Confirm no product-repo change is implied.
