# `lybra agent watch` Exit Codes (AIPOS-284 v2 + AIPOS-284C)

## Overview

`lybra agent watch --workspace-root` is a pure-client filesystem sentinel that polls the
project's declared subtrees for changes — the queue root (`project.json` `paths.queue_root`), the records
root and the loop-run records root (`paths.loop_runs_root`; defaults `5_tasks/queue/**`, `5_tasks/records/**`,
`5_tasks/records/loop_runs/**`). AIPOS-284 v2 enhances it with
three "death silence" detection semantics, giving advisors and harnesses precise signals
about task execution outcomes.

**Where it sits in the flow:** card advancement does not call this by hand. The advisor runs
`lybra loop --task-id <card>`, which waits for each agent-side artifact through a bounded
`agent watch --expect` on the declared report location and maps the outcome to its own exit codes
(`lybra loop` exit 3 = waiting for an artifact timed out / stalled). Use `agent watch` directly when a
harness or an operator wants a bounded wait or an event stream of its own.

To follow a running loop, the advisor does not call `agent watch` either: `lybra loop status --task-id <card>
--wait <seconds>` (AIPOS-F136) waits through the same primitive on the card's loop-run record and returns the
advisor's next action (`continue_wait` / `owner_needed` / `card_done_take_next` / `investigate` / `advisor_fix`; exit codes in
`schema/verbs.schema.json` `lybra_loop_status.exit_codes`). `advisor_fix` (AIPOS-F150) = the gate rejected with error codes
that are all declared routine (`lybra_loop_status.next_action.routine_rejections`, e.g. `BRANCH_WRONG_BASE`): the output
carries the fixed remedy and the resume command; the advisor handles it without asking the Owner.

**AIPOS-284C --stream mode**: A persistent observer that emits JSON event lines (line-buffered,
immediate flush) and continues running. Only `--timeout` or SIGTERM/SIGINT terminate the process.
Event deduplication: each expect file is reported at most once (new appearance only).

## Operating Modes

### Default Mode (one-shot)
Detect a single event and exit with a status code:
```bash
lybra agent watch --workspace-root <governance-root> --timeout 600
```

### Stream Mode (persistent, AIPOS-284C)
Emit JSON event lines and continue running:
```bash
lybra agent watch --workspace-root <governance-root> --stream --timeout 1800
```

## Exit Codes

| Code | Meaning | Output | When to Use |
|------|---------|--------|-------------|
| **0** | Change detected / Expect satisfied | JSON: `{"changed": [...]}` or `{"expect_satisfied": [...]}` | **Default mode only.** Normal success: either a filesystem change was detected, or `--expect` pattern matched |
| **2** | Timeout (silent) | None | **Both modes.** No change within `--timeout` seconds. Clean bounded wait exhausted. |
| **3** | End pattern seen, no product | JSON: `{"end_no_product": {"run_log_tail": "..."}}` | **Default mode only.** `--end-pattern` appeared in `--run-log`, but `--expect` was NOT satisfied (after one grace poll). The execution claims to be done but produced no artifact. |
| **4** | Stall detected | JSON: `{"stall": {"silence_seconds": N, "run_log_tail": "..."}}` | **Default mode only.** Observation surface (run-log or queue+records) silent for ≥ `--stall-secs`. The execution is stuck. |
| **5** | Usage error (布防拒绝) | stderr: single-line error message | **Both modes.** `--expect` pattern is invalid: absolute path (starts with `/`) or escape attempt (contains `..`). Fix the pattern and retry. (F-284B-1) |
| **130** | SIGTERM/SIGINT | None | **Both modes.** Clean signal exit (no Python traceback) |

**Note**: In `--stream` mode, exit codes 0/3/4 become JSON event lines instead of process exits. Only timeout (exit 2) or signals (exit 130) terminate the stream.

## Observation Surface

**Diff detection (exit 0 with `{"changed": [...]}`)**:
- Scope: the declared queue root, records root and loop-run records root only (AIPOS-F136: read from
  the project declarations, never hard-coded; a `loop_runs_root` declared outside the records root is still watched)
- Any new/modified/moved/deleted file in these subtrees triggers exit 0

**Expect matching (exit 0 with `{"expect_satisfied": [...]}`)**:
- Scope: entire workspace-root (F-284-1)
- Walk range: static prefix directory of each glob pattern (not full tree)
- Example: `--expect "task_cards/AIPOS-284/*.md"` walks only `task_cards/AIPOS-284/`
- Patterns must be relative; absolute paths and `..` escapes are rejected (exit 5)
- **F-284B-1**: Nonexistent prefix is VALID (future event). Pattern rejected ONLY if absolute/`..` escape.

**Stall detection (exit 4)**:
- With `--run-log`: observes run-log mtime only
- Without `--run-log`: observes the declared subtrees (same as diff scope)

## v2 Parameters (AIPOS-284)

### `--expect <glob>` (repeatable)
**布防即检** — Check immediately on startup AND on every poll. If any file matches the glob pattern, exit 0 with `{"expect_satisfied": [paths]}` (default mode) or emit `{"kind": "expect", "paths": [paths]}` event line (stream mode).

**Observation surface (F-284-1):** `--expect` patterns are matched against the ENTIRE workspace-root, not just `5_tasks/queue/**` and `5_tasks/records/**`. The walk scope is limited to the static prefix directory of each glob pattern (e.g., `task_cards/AIPOS-284/*.md` walks only `task_cards/AIPOS-284/`, not the entire tree). Patterns must be relative to workspace-root; absolute paths and `..` escapes are rejected (exit 5).

**F-284B-1 修向:** A pattern whose static prefix does NOT exist yet is VALID ("未来前缀合法化"). The prefix is checked dynamically on each poll—if it appears and files match, exit 0 (default) or emit event (stream). Rejection happens ONLY for absolute paths or `..` escapes (永不可能匹配的模式). This fixes the dogfood case where executor布防 `task_cards/AIPOS-277/RETURN.md` before creating the directory.

**AIPOS-284C stream mode deduplication:** Each expect file is reported at most once. Only new appearances trigger an event; repeated polls of the same matched file are silent.

**Use case:** Wait for a specific artifact (e.g., `--expect "5_tasks/records/RETURN.md"` or `--expect "task_cards/AIPOS-284/RETURN.md"`).

**Example:**
```bash
lybra agent watch --workspace-root <governance-root> \
  --expect "5_tasks/records/RETURN-*.md" \
  --expect "5_tasks/queue/completed/*.md" \
  --timeout 600
```

**Stream mode example:**
```bash
lybra agent watch --workspace-root <governance-root> \
  --stream \
  --expect "task_cards/*/RETURN.md" \
  --timeout 1800
```

### `--run-log <path>` + `--end-pattern <regex>`
**结束无产物** — If the regex matches content in `--run-log` but `--expect` is NOT satisfied, wait one poll (grace period), then exit 3 if still no match (default mode) or emit `{"kind": "run_end", "run_log_tail": "..."}` event line (stream mode).

**Use case:** Detect when a harness logs "execution finished" but the expected artifact never appears.

**Example:**
```bash
lybra agent watch --workspace-root <governance-root> \
  --expect "5_tasks/records/RETURN.md" \
  --run-log /tmp/agent.log \
  --end-pattern "Session.*finished" \
  --timeout 600
```

### `--stall-secs <N>` (default: 600 when `--run-log` is set)
**静默停滞** — If the observation surface (run-log mtime, or entire queue+records if no run-log) is unchanged for ≥ N seconds, exit 4 (default mode) or emit `{"kind": "stall", "silence_seconds": N, "run_log_tail": "..."}` event line (stream mode).

**Use case:** Detect stuck/frozen executions that neither finish nor produce output.

**Note:** Stall detection is only enabled if you explicitly set `--stall-secs` OR provide `--run-log`. Without these, stall detection is disabled (v1 behavior preserved).

**Example:**
```bash
lybra agent watch --workspace-root <governance-root> \
  --run-log /tmp/agent.log \
  --stall-secs 300 \
  --timeout 1800
```

### `--stream` (AIPOS-284C)
**常驻模式** — Persistent observer mode. Emit JSON event lines (line-buffered, immediate flush) and continue running. Only `--timeout` or SIGTERM/SIGINT terminate the process.

**Event kinds:**
- `{"kind": "expect", "paths": [...]}` — expect pattern matched (new files only; deduplicated)
- `{"kind": "change", "changed": [{"path": "...", "kind": "new|modified|moved|deleted"}, ...]}` — filesystem change detected
- `{"kind": "stall", "silence_seconds": N, "run_log_tail": "..."}` — observation surface silent beyond threshold
- `{"kind": "run_end", "run_log_tail": "..."}` — end-pattern seen but expect not satisfied

**Event deduplication (S3):** Each expect file is reported at most once. Filesystem changes advance the snapshot, so the same change is not re-reported.

**Use case:** Harness常驻监听 — keep a single watch process running, react to multiple events over time without re-launching.

**Example (owner-press monitoring harness):**
```bash
lybra agent watch --workspace-root <governance-root> \
  --stream \
  --expect "5_tasks/queue/pending/*.md" \
  --expect "5_tasks/records/RETURN-*.md" \
  --run-log /tmp/executor.log \
  --end-pattern "Session.*finished" \
  --stall-secs 600 \
  --timeout 7200 | while IFS= read -r event; do
    kind=$(echo "$event" | jq -r .kind)
    case "$kind" in
      expect)
        echo "Artifact detected: $(echo "$event" | jq -r .paths[])"
        ;;
      change)
        echo "Workspace changed: $(echo "$event" | jq -r '.changed[] | "\(.path) (\(.kind))"')"
        ;;
      stall)
        echo "ALERT: Execution stalled for $(echo "$event" | jq -r .silence_seconds)s"
        ;;
      run_end)
        echo "ALERT: Execution finished but produced no artifact"
        ;;
    esac
  done
```

## Advisor Next-Step Decision Table

### Default Mode
After `lybra agent watch` exits (when you call it directly; inside `lybra loop` the loop does this):

| Exit Code | Advisor Action |
|-----------|----------------|
| 0 | Parse JSON output. If `expect_satisfied`, the artifact is on disk: re-run `lybra loop --task-id <card>` (it ingests the artifact and advances). If `changed`, inspect the change kind and decide. |
| 2 | Timeout is a **normal bounded exit**. Log "no activity in N seconds" and either re-run the same wait or escalate per governance policy. |
| 3 | **Execution claimed completion but produced nothing.** Read `run_log_tail` from JSON. The workstation said "done" without the declared artifact: report it to the Owner (the workstation is reopened with `/go`, or by re-running an authorized `lybra loop`); never write the artifact on the executor's behalf. |
| 4 | **Execution is stalled/frozen.** Read `silence_seconds` and `run_log_tail`. Report the stall to the Owner with this evidence; the Owner decides whether to restart the workstation (`/go`, or re-run an authorized `lybra loop`). |
| 5 | **Usage error (布防拒绝).** The `--expect` pattern is invalid (absolute path or `..` escape). Read stderr for the exact error. Fix the pattern and re-invoke. This is a **caller bug**, not an execution failure. |
| 130 | Signal exit (external interrupt). Clean shutdown, no action needed unless this was unexpected. |

### Stream Mode (AIPOS-284C)
Parse each JSON event line from stdout:

| Event Kind | Advisor Action |
|------------|----------------|
| `expect` | Artifact detected. Parse `paths` array and proceed with next step (e.g., read RETURN.md, invoke auditor). |
| `change` | Workspace activity detected. Parse `changed` array for new/modified/moved/deleted files. Decide whether to act or continue monitoring. |
| `stall` | **Execution stalled.** Read `silence_seconds` and `run_log_tail`. Report the stall to the Owner with this evidence. |
| `run_end` | **Execution finished but produced nothing.** Read `run_log_tail`. Report it to the Owner; never write the artifact on the executor's behalf. |

Process exits (exit 2 timeout or exit 130 signal): terminate the monitoring loop.

## Red Lines (Preserved from AIPOS-268)

- **Pure client, read-only**: No gate, no MCP, no token. Only `stat()` and `walk()`.
- **Stdlib only**: Zero new dependencies (fnmatch, re, os, pathlib, json, etc. are all stdlib).
- **Gate zero change**: All logic is in the CLI client. The gate never touches these semantics.

## Governance References

- AIPOS-268: `agent watch` v1 (mtime pump, exit 0/2/130)
- AIPOS-284: v2 three "death silence" semantics (exit 3/4)
- **AIPOS-284C**: --stream persistent mode (one process, multiple events, deduplication)
- **F-284B-1** (AIPOS-284BF1): 未来前缀合法化 + 布防拒绝独立退出码 5 (dogfood 2026-07-31)
- Roadmap 候选⑫: Promoted to "发布前必须" (Owner 2026-07-30: "不然用不起来")
