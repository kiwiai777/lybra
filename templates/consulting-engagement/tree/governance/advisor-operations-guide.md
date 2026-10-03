# Advisor Operations Guide

This guide provides operational instructions for advisor agents working with Lybra. It complements `advisor-charter.md` (which defines authority boundaries and red lines) with practical "how to" guidance.

## Table of Contents

1. [Agent Watch: Four Exit Codes and What They Mean](#agent-watch-four-exit-codes)
2. [Choosing the Right Observation Surface](#choosing-observation-surface)
3. [Driving a Card: Product Commands Only](#driving-a-card)
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
  --connection-json .lybra/connection.json [options]
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
lybra agent watch --workspace-root <workspace> \
  --worktree-path <card-worktree> \
  --expect "deliverables/feature-*.md" \
  --stall-secs 600

# Monitor process activity + session files
lybra agent watch --workspace-root <workspace> \
  --proc-pattern "pi" \
  --session-dirs ".pi/sessions" \
  --health 300 --stream
```

**Watching unbuffered harness:**
```bash
lybra agent watch --workspace-root <workspace> \
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

## Driving a Card: Product Commands Only {#driving-a-card}

The advisor (driver) advances cards **only through product commands**. Never hand-write scripts that call
gate interfaces directly (raw JSON-RPC / curl / ad-hoc gate clients). If a step cannot be done with a product
command, that is a product gap: record it and open a card — do not work around it.

```bash
# Publish a card
lybra draft create --from-json <draft.json>
lybra draft publish --path 5_tasks/drafts/<id>.md

# Advance one card end to end (claim -> return -> audit dispatch -> verdict -> finalize -> close)
lybra loop --task-id <TASK-ID> --envelope <driver-envelope-id> --actor <advisor-instance>
```

- **Claim**: `lybra loop` claims in one stage under the envelope; the gate builds the card worktree and
  **refuses the claim if the worktree cannot be built**.
- **Return / verdict**: once the executor/auditor has written its artifact, `lybra loop` ingests it
  (`lybra artifact ingest`); the model field is filled by the product from the harness session record.
- **Workstations** (executor / auditor) only type `/go`. The product picks the card (`lybra my-tasks`
  next_card) and gives the worktree and report location. Workstations never claim, return, or submit verdicts.
- **Waiting** for an artifact: `lybra agent watch --workspace-root <workspace> --expect <path>` (bounded,
  foreground). Never write `until`/`sleep` polling loops.
- **Check what comes next** (read-only): `lybra next --task-id <TASK-ID>`.

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
lybra agent watch --workspace-root <workspace>  # Correct CLI entry point
```

**SYMPTOM:** Exit 0 with no output. You may misinterpret this as "task completed successfully."

**FIX:** Always use `lybra <command>` entry points. Run `lybra --help` to see available commands.

---

### Pitfall 2: Using --run-log with Buffered Output (pi)

**DON'T:**
```bash
lybra agent watch --workspace-root <workspace> \
  --run-log /tmp/pi.log \
  --stall-secs 600
```

**WHY IT FAILS:** `pi` buffers stdout. The log file does **not update** during execution (only at exit). Watch sees a static log for 600+ seconds → false STALL.

**DO:**
```bash
# Use worktree or process monitoring instead
lybra agent watch --workspace-root <workspace> \
  --worktree-path <card-worktree> \
  --stall-secs 600
```

**SYMPTOM:** Exit 4 (STALL) after 10 minutes, but the agent is actually running fine and writing files.

**FIX:** Choose the observation surface that matches your harness. If the harness buffers, monitor **side effects** (file writes, process CPU) instead of stdout.

---

### Pitfall 3: Hand-Rolled Polling Loops

**DON'T:**
```bash
until [ -f task_cards/<TASK-ID>/RETURN.md ]; do sleep 30; done  # unbounded, silent on failure
```

**WHY IT FAILS:** An unbounded shell loop never reports a stall, a premature end, or a timeout, and it
keeps running after the session that started it is gone.

**DO:**
```bash
lybra agent watch --workspace-root <workspace> --expect "task_cards/<TASK-ID>/RETURN.md" --timeout 1800
```

**SYMPTOM:** Orphaned `sleep` processes; a "waiting" advisor that never notices the executor died.

**FIX:** Wait only with `lybra agent watch` (exit codes 0/2/3/4 above), or let `lybra loop` do the waiting.

---

### Pitfall 4: Rotating Tokens Without Preserving Instance Bindings

**DON'T:**
```bash
lybra serve rotate  # Silently drops existing agent_instance bindings
```

**WHY IT FAILS:** `serve rotate` regenerates ALL tokens. If you previously bound tokens to agent instances (`--executor-instance`, `--role-instance`), those bindings are lost. **PreAuthorized autonomy becomes unavailable** → all claims fall back to Supervised (requiring Owner confirm per task). This can break automation.

**SYMPTOM (2026-08-03 incident):**
- PreAuthorized envelopes (e.g., `pol_<project>_dev_1`) downgrade to Supervised
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
Error: serve rotate would lose existing instance bindings: executor=exec.{{ project_id }}.local, auditor=audit.{{ project_id }}.local.
PreAuthorized autonomy would become unavailable for these roles.
Specify --executor-instance and/or --role-instance to preserve bindings,
or confirm this is intentional (e.g., rotating to unbind for testing).
```

To proceed intentionally (e.g., testing Supervised mode), you must explicitly omit the bindings. The error prevents **accidental** unbinding.

---

### Pitfall 5: Pasting Cards or Claiming From the Workstation

**DON'T:**
```text
(paste the card body into the executor session and ask it to claim / return by itself)
```

**WHY IT FAILS:** Workstations are zero-gate: they only produce artifacts. A workstation that claims or
returns by itself bypasses the driver's envelope and the product's worktree / report-location derivation,
so records and worktree drift apart.

**DO:**
```bash
# Driver side
lybra loop --task-id <TASK-ID> --envelope <driver-envelope-id> --actor <advisor-instance>
# Workstation side: just type /go
```

**SYMPTOM:** Claims without a worktree, reports written to the wrong place, records that do not match the card.

**FIX:** Drive with `lybra loop`; workstations start with `/go` only.

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

┌─ About to advance a card? ────────────────────────────────────────────┐
│                                                                       │
│  1. Driver: lybra loop --task-id <ID> --envelope <id> --actor <name>  │
│  2. Workstation: /go  (zero gate verbs)                               │
│  3. Step fails → record the product gap; never hand-roll around it    │
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

**Document revision:** AIPOS-316 (顾问侧护栏: 误用即响 + 手册随 init 交付); AIPOS-F91 (已退役的预检/拉起命令改为现行 lybra loop + /go)

**Last updated:** 2026-10-03
