# `lybra agent watch` Exit Codes (AIPOS-284 v2)

## Overview

`lybra agent watch --workspace-root` is a pure-client filesystem sentinel that polls
`5_tasks/queue/**` and `5_tasks/records/**` for changes. AIPOS-284 v2 enhances it with
three "death silence" detection semantics, giving advisors and harnesses precise signals
about task execution outcomes.

## Exit Codes

| Code | Meaning | Output | When to Use |
|------|---------|--------|-------------|
| **0** | Change detected / Expect satisfied | JSON: `{"changed": [...]}` or `{"expect_satisfied": [...]}` | Normal success: either a filesystem change was detected, or `--expect` pattern matched |
| **2** | Timeout (silent) | None | No change within `--timeout` seconds. Clean bounded wait exhausted. |
| **3** | End pattern seen, no product | JSON: `{"end_no_product": {"run_log_tail": "..."}}` | `--end-pattern` appeared in `--run-log`, but `--expect` was NOT satisfied (after one grace poll). The execution claims to be done but produced no artifact. |
| **4** | Stall detected | JSON: `{"stall": {"silence_seconds": N, "run_log_tail": "..."}}` | Observation surface (run-log or queue+records) silent for ≥ `--stall-secs`. The execution is stuck. |
| **5** | Usage error (布防拒绝) | stderr: single-line error message | `--expect` pattern is invalid: absolute path (starts with `/`) or escape attempt (contains `..`). Fix the pattern and retry. (F-284B-1) |
| **130** | SIGTERM/SIGINT | None | Clean signal exit (no Python traceback) |

## Observation Surface

**Diff detection (exit 0 with `{"changed": [...]}`)**:
- Scope: `5_tasks/queue/**` and `5_tasks/records/**` only
- Any new/modified/moved/deleted file in these subtrees triggers exit 0

**Expect matching (exit 0 with `{"expect_satisfied": [...]}`)**:
- Scope: entire workspace-root (F-284-1)
- Walk range: static prefix directory of each glob pattern (not full tree)
- Example: `--expect "task_cards/AIPOS-284/*.md"` walks only `task_cards/AIPOS-284/`
- Patterns must be relative; absolute paths and `..` escapes are rejected (exit 5)
- **F-284B-1**: Nonexistent prefix is VALID (future event). Pattern rejected ONLY if absolute/`..` escape.

**Stall detection (exit 4)**:
- With `--run-log`: observes run-log mtime only
- Without `--run-log`: observes `5_tasks/queue/**` and `5_tasks/records/**` (same as diff scope)

## v2 Parameters (AIPOS-284)

### `--expect <glob>` (repeatable)
**布防即检** — Check immediately on startup AND on every poll. If any file matches the glob pattern, exit 0 with `{"expect_satisfied": [paths]}`.

**Observation surface (F-284-1):** `--expect` patterns are matched against the ENTIRE workspace-root, not just `5_tasks/queue/**` and `5_tasks/records/**`. The walk scope is limited to the static prefix directory of each glob pattern (e.g., `task_cards/AIPOS-284/*.md` walks only `task_cards/AIPOS-284/`, not the entire tree). Patterns must be relative to workspace-root; absolute paths and `..` escapes are rejected (exit 5).

**F-284B-1 修向:** A pattern whose static prefix does NOT exist yet is VALID ("未来前缀合法化"). The prefix is checked dynamically on each poll—if it appears and files match, exit 0. Rejection happens ONLY for absolute paths or `..` escapes (永不可能匹配的模式). This fixes the dogfood case where executor布防 `task_cards/AIPOS-277/RETURN.md` before creating the directory.

**Use case:** Wait for a specific artifact (e.g., `--expect "5_tasks/records/RETURN.md"` or `--expect "task_cards/AIPOS-284/RETURN.md"`).

**Example:**
```bash
lybra agent watch --workspace-root ~/projects/lybra \
  --expect "5_tasks/records/RETURN-*.md" \
  --expect "5_tasks/queue/completed/*.md" \
  --timeout 600
```

### `--run-log <path>` + `--end-pattern <regex>`
**结束无产物** — If the regex matches content in `--run-log` but `--expect` is NOT satisfied, wait one poll (grace period), then exit 3 if still no match.

**Use case:** Detect when a harness logs "execution finished" but the expected artifact never appears.

**Example:**
```bash
lybra agent watch --workspace-root ~/projects/lybra \
  --expect "5_tasks/records/RETURN.md" \
  --run-log /tmp/agent.log \
  --end-pattern "Session.*finished" \
  --timeout 600
```

### `--stall-secs <N>` (default: 600 when `--run-log` is set)
**静默停滞** — If the observation surface (run-log mtime, or entire queue+records if no run-log) is unchanged for ≥ N seconds, exit 4.

**Use case:** Detect stuck/frozen executions that neither finish nor produce output.

**Note:** Stall detection is only enabled if you explicitly set `--stall-secs` OR provide `--run-log`. Without these, stall detection is disabled (v1 behavior preserved).

**Example:**
```bash
lybra agent watch --workspace-root ~/projects/lybra \
  --run-log /tmp/agent.log \
  --stall-secs 300 \
  --timeout 1800
```

## Advisor Next-Step Decision Table

After `lybra agent watch` exits, the advisor should:

| Exit Code | Advisor Action |
|-----------|----------------|
| 0 | Parse JSON output. If `expect_satisfied`, proceed to next step (e.g., read RETURN.md, update board). If `changed`, inspect the change kind and decide. |
| 2 | Timeout is a **normal bounded exit**. Log "no activity in N seconds" and either retry or escalate per governance policy. |
| 3 | **Execution claimed completion but produced nothing.** Read `run_log_tail` from JSON. This is a task failure: the executor said "done" but violated the contract. File a BLOCK report or audit card. |
| 4 | **Execution is stalled/frozen.** Read `silence_seconds` and `run_log_tail`. The executor is stuck (no output, no heartbeat). Kill the executor process (if known) and file a BLOCK report. |
| 5 | **Usage error (布防拒绝).** The `--expect` pattern is invalid (absolute path or `..` escape). Read stderr for the exact error. Fix the pattern and re-invoke. This is a **caller bug**, not an execution failure. |
| 130 | Signal exit (external interrupt). Clean shutdown, no action needed unless this was unexpected. |

## Red Lines (Preserved from AIPOS-268)

- **Pure client, read-only**: No gate, no MCP, no token. Only `stat()` and `walk()`.
- **Stdlib only**: Zero new dependencies (fnmatch, re, os, pathlib, json, etc. are all stdlib).
- **Gate zero change**: All logic is in the CLI client. The gate never touches these semantics.

## Governance References

- AIPOS-268: `agent watch` v1 (mtime pump, exit 0/2/130)
- AIPOS-284: v2 three "death silence" semantics (exit 3/4)
- **F-284B-1** (AIPOS-284BF1): 未来前缀合法化 + 布防拒绝独立退出码 5 (dogfood 2026-07-31)
- 候选⑤⑫合流: `--workspace-root` (⑫) vs `--gate-url` (⑤) are mutually exclusive modes
- Roadmap 候选⑫: Promoted to "发布前必须" (Owner 2026-07-30: "不然用不起来")
