# Advisor Operations Guide

This guide provides operational instructions for advisor agents working with Lybra. It complements `advisor-charter.md` (which defines authority boundaries and red lines) with practical "how to" guidance.

## Table of Contents

1. [Agent Watch: Four Exit Codes and What They Mean](#agent-watch-four-exit-codes)
2. [Choosing the Right Observation Surface](#choosing-observation-surface)
3. [Launch Check: Pre-Flight Validation](#launch-check)
4. [Common Pitfalls and How to Avoid Them](#common-pitfalls)

---

## Agent Watch: Four Exit Codes and What They Mean {#agent-watch-four-exit-codes}

`lybra agent watch` is your primary tool for monitoring task queue changes and agent execution. It operates in two modes:

### Mode 1: Filesystem Pump (--workspace-root)

Pure client-side monitoring. No gate connection required.

```bash
lybra agent watch --workspace-root /path/to/workspace [options]
```

**Exit codes:**
- **0 (EXIT_CHANGE)**: Change detected or `--expect` pattern satisfied. **This is success.**
- **2 (EXIT_TIMEOUT)**: No change within `--timeout` seconds (default: 30 minutes). Silent exit.
- **3 (EXIT_END_NO_PRODUCT)**: `--end-pattern` seen in `--run-log` but `--expect` NOT satisfied (execution ended without producing expected artifact).
- **4 (EXIT_STALL)**: Observation surface silent beyond `--stall-secs` threshold (default: 600s).

**When to use each exit code:**
- Exit 0 → proceed to next step (change happened, artifact appeared)
- Exit 2 → timeout is **normal** for bounded waiting; retry or escalate
- Exit 3 → execution ended prematurely; check run log for errors
- Exit 4 → execution may be stuck; investigate or try different observation surface

### Mode 2: Gate Pull (--gate-url)

Queries the gate for claimable tasks. Requires `--actor`, `--connection-json` or `--token-env`.

```bash
lybra agent watch --gate-url http://127.0.0.1:7118 --actor advisor.{{ project_id }}.local \
  --connection-json .lybra/local/connection.json [options]
```

**Exit codes:** Same as filesystem pump (0/2/3/4).

---

## Choosing the Right Observation Surface {#choosing-observation-surface}

Different agent harnesses buffer output differently. Choose the observation surface that matches your execution environment:

### Decision Matrix

| Agent Harness | Recommended Surface | Why |
|---------------|---------------------|-----|
| **pi** (default) | `--worktree-path` or `--proc-pattern` or `--session-dirs` | pi **buffers stdout**; run-log may not update until exit. Worktree changes (file writes) are immediate. |
| **Custom harness (unbuffered)** | `--run-log` | If your harness flushes output line-by-line, run-log is responsive. |
| **No specific harness** | Default (queue + records) | Watches `5_tasks/queue/**` and `5_tasks/records/**` for card moves and record writes. |

### Examples

**Watching pi (buffered output):**
```bash
# Monitor git worktree changes (file writes)
lybra agent watch --workspace-root ~/lybra \
  --worktree-path ~/lybra/my_feature \
  --expect "deliverables/feature-*.md" \
  --stall-secs 600

# Monitor process activity + session files
lybra agent watch --workspace-root ~/lybra \
  --proc-pattern "pi" \
  --session-dirs ".pi/sessions" \
  --health 300 --stream
```

**Watching unbuffered harness:**
```bash
lybra agent watch --workspace-root ~/lybra \
  --run-log /tmp/agent.log \
  --end-pattern "DONE|FAILED" \
  --expect "task_cards/*/RETURN.md" \
  --stall-secs 600
```

**WARNING:** If you use `--run-log` with a buffered harness (like pi), the observation surface may **never change** during execution. Watch will emit:
```
WARNING: Observation surface has not changed since watch started. 
If the monitored process buffers output (e.g., pi), consider using 
--worktree-path, --proc-pattern, or --session-dirs for more responsive monitoring.
```

This is a **hint**, not an error. The watch behavior (timeout/stall thresholds) does not change; the hint tells you there may be a better observation surface.

---

## Launch Check: Pre-Flight Validation {#launch-check}

Before launching an agent, run `launch-check` to validate the environment and gate connection:

```bash
lybra agent launch-check \
  --actor advisor.{{ project_id }}.local \
  --connection-json .lybra/local/connection.json \
  --gate-url http://127.0.0.1:7118 \
  [--fix]
```

**What it checks:**
- Gate reachable and responding
- Token valid and scopes sufficient
- Workspace structure valid
- No stale leases blocking claim

**Exit codes:**
- **0**: All checks passed, safe to launch
- **1**: Validation failed; see output for blocking reasons
- **2**: Validation passed with warnings; `--fix` can auto-resolve some issues

**When to use:**
- Before spawning a new agent (especially after gate restart or token rotation)
- After manual workspace changes
- When debugging "why won't my agent claim tasks?"

**With `--fix`:**
Launch-check can auto-resolve bounded issues:
- Clear stale leases (if lease expired)
- Create missing directories
- Does NOT write tokens or modify gate state beyond lease cleanup

---

## Common Pitfalls and How to Avoid Them {#common-pitfalls}

### Pitfall 1: Direct Invocation of Internal Modules

**DON'T:**
```bash
python3 -m tools.aipos_cli.agent_watch_fs  # Silent exit 0, zero output
```

**WHY IT FAILS:** `agent_watch_fs` is an internal module with no `__main__` block. Running it directly does nothing.

**DO:**
```bash
lybra agent watch --workspace-root ~/lybra  # Correct CLI entry point
```

**SYMPTOM:** Exit 0 with no output. You may misinterpret this as "task completed successfully."

**FIX:** Always use `lybra <command>` entry points. Run `lybra --help` to see available commands.

---

### Pitfall 2: Using --run-log with Buffered Output (pi)

**DON'T:**
```bash
lybra agent watch --workspace-root ~/lybra \
  --run-log /tmp/pi.log \
  --stall-secs 600
```

**WHY IT FAILS:** `pi` buffers stdout. The log file does **not update** during execution (only at exit). Watch sees a static log for 600+ seconds → false STALL.

**DO:**
```bash
# Use worktree or process monitoring instead
lybra agent watch --workspace-root ~/lybra \
  --worktree-path ~/lybra/deliverables \
  --stall-secs 600
```

**SYMPTOM:** Exit 4 (STALL) after 10 minutes, but the agent is actually running fine and writing files.

**FIX:** Choose the observation surface that matches your harness. If the harness buffers, monitor **side effects** (file writes, process CPU) instead of stdout.

---

### Pitfall 3: Shell Pipelines and Agent Spawning

**DON'T:**
```bash
echo "task prompt" | lybra agent spawn executor  # pi dies immediately
```

**WHY IT FAILS:** Shell pipelines close stdin. If the spawned process expects interactive input or uses stdin for control flow, it may exit immediately.

**DO:**
```bash
# Spawn with explicit input redirection
lybra agent spawn executor --prompt-file /tmp/task.txt

# Or use heredoc for non-interactive input
lybra agent spawn executor <<EOF
Task prompt here
EOF
```

**SYMPTOM:** Agent exits instantly (< 1 second). Run log shows "stdin closed" or process termination without error.

**FIX:** Avoid shell `|` with interactive processes. Use files or heredocs for input.

---

### Pitfall 4: Rotating Tokens Without Preserving Instance Bindings

**DON'T:**
```bash
lybra serve rotate  # Silently drops existing agent_instance bindings
```

**WHY IT FAILS:** `serve rotate` regenerates ALL tokens. If you previously bound tokens to agent instances (`--executor-instance`, `--role-instance`), those bindings are lost. **PreAuthorized autonomy becomes unavailable** → all claims fall back to Supervised (requiring Owner confirm per task). This can break automation.

**SYMPTOM (2026-08-03 incident):**
- PreAuthorized envelopes (e.g., `pol_lybra_dev_6`) downgrade to Supervised
- All task claims block waiting for Owner confirmation
- Agent workflow stalls for 40+ minutes
- Root cause is distant from symptom (token rotation happened hours earlier)

**DO:**
```bash
# Preserve bindings when rotating
lybra serve rotate \
  --executor-instance exec.{{ project_id }}.local \
  --role-instance auditor=audit.{{ project_id }}.local
```

**FIX:** As of AIPOS-316, `rotate` will **block** if it detects you're about to lose instance bindings:
```
Error: serve rotate would lose existing instance bindings: executor=exec.lybra.local, auditor=audit.lybra.local.
PreAuthorized autonomy would become unavailable for these roles.
Specify --executor-instance and/or --role-instance to preserve bindings,
or confirm this is intentional (e.g., rotating to unbind for testing).
```

To proceed intentionally (e.g., testing Supervised mode), you must explicitly omit the bindings. The error prevents **accidental** unbinding.

---

### Pitfall 5: Forgetting to Check Launch-Check Before Spawning

**DON'T:**
```bash
# Spawn immediately after gate restart
lybra agent spawn executor "implement AIPOS-123"
```

**WHY IT FAILS:** Gate may be up but not fully ready (state recovery in progress). Agent spawns, immediately tries to claim task, gets 503 Service Unavailable, exits.

**DO:**
```bash
# Pre-flight check
lybra agent launch-check --actor advisor.{{ project_id }}.local \
  --connection-json .lybra/local/connection.json \
  --gate-url http://127.0.0.1:7118

# Only spawn if launch-check exits 0
if [ $? -eq 0 ]; then
  lybra agent spawn executor "implement AIPOS-123"
fi
```

**SYMPTOM:** Agent spawns but immediately fails with connection/permission errors.

**FIX:** Always run `launch-check` before spawn, especially after:
- `lybra serve start` (gate restart)
- `lybra serve rotate` (token regeneration)
- Manual changes to workspace or gate config

---

## Quick Reference: Decision Tree

```
┌─ Need to wait for task/artifact? ────────────────────────────────────┐
│                                                                       │
│  ┌─ Harness buffers output? (e.g., pi)                              │
│  │   YES → use --worktree-path / --proc-pattern / --session-dirs    │
│  │   NO  → use --run-log                                             │
│  │                                                                    │
│  └─ Need gate query? (check claimable tasks)                        │
│      YES → lybra agent watch --gate-url ...                          │
│      NO  → lybra agent watch --workspace-root ...                    │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘

┌─ About to spawn agent? ───────────────────────────────────────────────┐
│                                                                       │
│  1. Run lybra agent launch-check --actor <name> [--fix]              │
│  2. If exit 0 → spawn                                                 │
│  3. If exit 1/2 → fix issues first                                    │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘

┌─ Token rotation needed? ──────────────────────────────────────────────┐
│                                                                       │
│  ┌─ Have instance bindings (PreAuthorized autonomy)?                 │
│  │   YES → lybra serve rotate --executor-instance <name> \           │
│  │                            --role-instance <role>=<instance>       │
│  │   NO  → lybra serve rotate  (simple rotation)                     │
│  │                                                                    │
│  └─ AIPOS-316: rotate will BLOCK if bindings would be lost           │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Further Reading

- **advisor-charter.md**: Authority boundaries, scope limits, red lines
- **AGENTS.md**: Role definitions, responsibility matrix
- **docs/agent_watch_exit_codes.md** (in product repo): Full exit code specification
- **docs/service_mode.md** (in product repo): Token management, instance binding details

---

**Document revision:** AIPOS-316 (顾问侧护栏: 误用即响 + 手册随 init 交付)

**Last updated:** 2026-08-03
