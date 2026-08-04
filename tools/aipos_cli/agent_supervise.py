"""AIPOS-295 — lybra agent supervise: health monitoring with bounded auto-restart.

The supervise command wraps an agent execution command (typically spawning pi with timeout),
monitors its health via `agent watch --stream --health`, and implements bounded self-healing:

1. Spawns the target command (e.g., `timeout 3600 pi --prompt '{kickoff}'`)
2. Monitors health events from a parallel `agent watch --stream --health` process
3. On `kind:unhealthy` event:
   - Kill the process tree (including timeout wrapper and pi subprocess)
   - Respawn ONCE (emit `kind:respawn` event with attempt=1)
4. On second unhealthy event:
   - DO NOT respawn again
   - Write ESCALATE file with diagnosis (three-model history style from AIPOS-293)
   - Emit `kind:escalate` event
   - Exit and wait for manual intervention (Owner authorization)

Red lines (AIPOS-295 S4):
- All logic is observer/agent-side (gate zero involvement)
- Respawn template MUST include timeout wrapper
- Only measure pi subprocess tree (not timeout shell)
- Model switching requires explicit Owner authorization or pre-authorized policy reference

Exit codes:
- 0: clean exit (signal received)
- 1: configuration error
- 75: escalation (second failure, needs authorization) — RestartPreventExitStatus
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.confirm_client import GateClient, GateError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ESCALATE = 75  # systemd RestartPreventExitStatus


def log(msg: str, *, stream: Any = sys.stderr) -> None:
    """Timestamped log."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[supervise {timestamp}] {msg}", file=stream, flush=True)


def kill_process_tree(pid: int) -> None:
    """Kill process and all its descendants (including timeout wrapper and pi subprocess)."""
    if psutil is None:
        # Fallback: try to kill just the PID
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        
        # Send SIGTERM to all
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            pass
        
        # Wait briefly
        time.sleep(2)
        
        # SIGKILL any survivors
        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            if parent.is_running():
                parent.kill()
        except psutil.NoSuchProcess:
            pass
    except psutil.NoSuchProcess:
        pass


def write_escalate_file(
    product_repo: Path,
    card_id: str,
    spawn_cmd: str,
    failure_history: list[dict[str, Any]],
) -> Path:
    """Write ESCALATE file when bounded restart limit is reached.
    
    Similar to BLOCK file pattern but for runtime failures requiring authorization.
    """
    escalate_dir = product_repo / "task_cards" / card_id
    escalate_dir.mkdir(parents=True, exist_ok=True)
    
    n = 1
    while (escalate_dir / f"ESCALATE-{n}.md").exists():
        n += 1
    
    escalate_file = escalate_dir / f"ESCALATE-{n}.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Format failure history (three-model style diagnosis)
    history_lines = []
    for i, failure in enumerate(failure_history, 1):
        history_lines.append(f"### Attempt {i}")
        history_lines.append(f"- Time: {failure.get('timestamp', 'unknown')}")
        history_lines.append(f"- Reason: {failure.get('reason', 'unknown')}")
        history_lines.append(f"- Process alive: {failure.get('proc_alive', False)}")
        history_lines.append(f"- CPU delta: {failure.get('cpu_delta', 0)}")
        history_lines.append(f"- Session files: {failure.get('new_session_files', 0)}")
        history_lines.append(f"- Worktree changes: {failure.get('worktree_changes', 0)}")
        history_lines.append("")
    
    content = f"""# ESCALATE — {card_id} supervise

- Time: {timestamp}
- Spawn command: `{spawn_cmd}`
- Trigger: Consecutive failures exceed bounded restart limit (1 respawn allowed)

## Failure History (Three-派 Style Diagnosis)

{chr(10).join(history_lines)}

## Death Signature

Pattern observed: {failure_history[-1].get('reason', 'unknown')} after {len(failure_history)} attempt(s).

Possible causes:
- Model capacity exhausted (静默死: CPU不爬 + 零会话文件 + 零工作树增量)
- Process crash (进程消失)
- Route/provider failure
- Prompt/task incompatible with model

## Required Action

**Owner/Advisor decision required**. Supervise will NOT auto-restart again.

Options:
1. **Switch model** (requires Owner authorization or pre-authorized policy):
   - Modify spawn command to use different model (e.g., qwen → claude)
   - Update model substitution policy if pattern recurs
2. **Investigate route**: Check provider logs, quota, connectivity
3. **Manual intervention**: Review task card, adjust prompt, check logs

To resume after decision:
```bash
# If switching model is authorized:
lybra agent supervise --spawn-cmd '<new_command_with_different_model>' ...

# Or manually fix and restart service:
systemctl --user restart <service_name>
```

Supervise has exited {EXIT_ESCALATE} → systemd RestartPreventExitStatus={EXIT_ESCALATE} prevents auto-restart loop.

Next step: Advisor/Owner reviews this file + decides model switch or other intervention.
"""
    
    escalate_file.write_text(content, encoding="utf-8")
    log(f"ESCALATE file written: {escalate_file}")
    return escalate_file


def diagnose_death_cause(
    failure_record: dict[str, Any],
    run_log: str | None,
    session_dirs: str | None,
) -> str:
    """Diagnose death cause from failure signature.
    
    Returns one of: context_exhausted, process_crash, route_failure, unknown
    """
    reason = failure_record.get("reason", "")
    proc_alive = failure_record.get("proc_alive", False)
    cpu_delta = failure_record.get("cpu_delta", 0)
    new_session_files = failure_record.get("new_session_files", 0)
    worktree_changes = failure_record.get("worktree_changes", 0)
    
    # Process crashed/killed
    if not proc_alive:
        return "process_crash"
    
    # Context exhaustion signature: proc alive but silent death
    # (CPU not climbing + zero session files + zero worktree delta)
    if proc_alive and cpu_delta == 0 and new_session_files == 0 and worktree_changes == 0:
        # Try to read run_log for compaction evidence
        if run_log:
            try:
                log_path = Path(run_log)
                if log_path.exists():
                    # Read last 50 lines for compaction events
                    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        last_lines = lines[-50:] if len(lines) > 50 else lines
                        last_text = ''.join(last_lines).lower()
                        
                        # Check for compaction indicators
                        if 'compaction' in last_text or 'context' in last_text:
                            return "context_exhausted"
                        
                        # Check for high cacheRead / low output (typical of context issues)
                        if 'cacheread' in last_text and 'output":0' in last_text:
                            return "context_exhausted"
            except Exception:
                pass
        
        # Default to context_exhausted if signature matches (silent death pattern)
        return "context_exhausted"
    
    # Check for route/provider failure indicators
    if "timeout" in reason.lower() or "connection" in reason.lower():
        return "route_failure"
    
    # Cannot determine
    return "unknown"


def report_death_to_gate(
    gate_client: GateClient | None,
    card_id: str,
    actor: str,
    death_cause: str,
    failure_history: list[dict[str, Any]],
) -> None:
    """Report executor death as blocked event via lybra_task_progress."""
    if gate_client is None:
        log("WARNING: No gate client, cannot report death event")
        return
    
    try:
        # Build reason summary
        last_failure = failure_history[-1] if failure_history else {}
        reason_text = f"Executor process terminated abnormally after {len(failure_history)} failure(s). "
        reason_text += f"Death cause: {death_cause}. "
        
        if death_cause == "context_exhausted":
            reason_text += "Likely context window exhaustion (silent death: no CPU activity, no file changes)."
        elif death_cause == "process_crash":
            reason_text += "Process crashed or was killed."
        elif death_cause == "route_failure":
            reason_text += "Possible route/provider connectivity issue."
        else:
            reason_text += "Cause could not be determined from available signals."
        
        # Call lybra_task_progress with event_type=blocked
        args = {
            "task_id": card_id,
            "event_type": "blocked",
            "actor": actor,
            "reason": death_cause,
            "summary": reason_text,
        }
        
        result = gate_client.call_tool("lybra_task_progress", args)
        
        if result.get("ok"):
            log(f"Death event reported to gate: {card_id} → {death_cause}")
        else:
            log(f"WARNING: Failed to report death event: {result}")
    
    except Exception as exc:
        log(f"ERROR: Failed to report death event to gate: {exc}")


def run_supervise(
    spawn_cmd: str,
    workspace_root: Path,
    product_repo: Path,
    card_id: str,
    health_interval: float,
    pid_file: str | None,
    proc_pattern: str | None,
    session_dirs: str | None,
    worktree_path: str | None,
    run_log: str | None,
    gate_client: GateClient | None,
    actor: str,
) -> int:
    """Main supervise loop.
    
    Returns:
        EXIT_OK (0): clean exit
        EXIT_ERROR (1): configuration error
        EXIT_ESCALATE (75): second failure, needs authorization
    """
    failure_history: list[dict[str, Any]] = []
    attempt = 0
    max_attempts = 2  # Initial spawn + 1 respawn
    
    while attempt < max_attempts:
        attempt += 1
        log(f"Spawning command (attempt {attempt}/{max_attempts}): {spawn_cmd[:100]}...")
        
        # Spawn the target command
        try:
            proc = subprocess.Popen(
                spawn_cmd,
                shell=True,
                cwd=str(product_repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
        except Exception as exc:
            log(f"ERROR: Failed to spawn command: {exc}")
            return EXIT_ERROR
        
        log(f"Spawned process PID {proc.pid}")
        
        # If pid_file specified, write it
        if pid_file:
            try:
                Path(pid_file).write_text(str(proc.pid))
            except OSError as exc:
                log(f"WARNING: Failed to write PID file {pid_file}: {exc}")
        
        # Build watch command
        watch_cmd = [
            sys.executable, "-m", "tools.aipos_cli.aipos_cli",
            "agent", "watch",
            "--workspace-root", str(workspace_root),
            "--stream",
            "--health", str(health_interval),
            "--timeout", "0",  # Infinite
        ]
        
        if pid_file:
            watch_cmd.extend(["--pid-file", pid_file])
        if proc_pattern:
            watch_cmd.extend(["--proc-pattern", proc_pattern])
        if session_dirs:
            watch_cmd.extend(["--session-dirs", session_dirs])
        if worktree_path:
            watch_cmd.extend(["--worktree-path", worktree_path])
        if run_log:
            watch_cmd.extend(["--run-log", run_log])
        
        # Spawn watch process
        try:
            watch_proc = subprocess.Popen(
                watch_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
        except Exception as exc:
            log(f"ERROR: Failed to spawn watch process: {exc}")
            kill_process_tree(proc.pid)
            return EXIT_ERROR
        
        log(f"Watch process started PID {watch_proc.pid}")
        
        # Monitor watch output for unhealthy events
        unhealthy_detected = False
        last_unhealthy_data = {}
        
        try:
            while True:
                # Check if target process died
                if proc.poll() is not None:
                    log(f"Target process exited with code {proc.returncode}")
                    break
                
                # Read watch output line by line
                if watch_proc.stdout:
                    line = watch_proc.stdout.readline()
                    if not line:
                        # Watch process ended
                        if watch_proc.poll() is not None:
                            log(f"Watch process exited with code {watch_proc.returncode}")
                            break
                        time.sleep(0.1)
                        continue
                    
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    
                    kind = event.get("kind")
                    
                    if kind == "health":
                        # Forward health event
                        print(json.dumps(event, ensure_ascii=False), flush=True)
                    
                    elif kind == "unhealthy":
                        log(f"UNHEALTHY detected: {event.get('reason')}")
                        unhealthy_detected = True
                        last_unhealthy_data = event
                        break
                    
                    elif kind in ("expect", "change", "stall", "run_end"):
                        # Forward other events
                        print(json.dumps(event, ensure_ascii=False), flush=True)
                    
                    elif kind == "end":
                        log(f"Watch ended: {event.get('reason')}")
                        break
        
        except KeyboardInterrupt:
            log("Received interrupt, cleaning up...")
            kill_process_tree(proc.pid)
            watch_proc.terminate()
            return EXIT_OK
        
        finally:
            # Clean up processes
            if proc.poll() is None:
                kill_process_tree(proc.pid)
            if watch_proc.poll() is None:
                watch_proc.terminate()
        
        # Handle unhealthy detection
        if unhealthy_detected:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            failure_record = {
                "timestamp": timestamp,
                "attempt": attempt,
                "reason": last_unhealthy_data.get("reason", "unknown"),
                "proc_alive": last_unhealthy_data.get("proc_alive", False),
                "cpu_delta": last_unhealthy_data.get("cpu_delta", 0),
                "new_session_files": last_unhealthy_data.get("new_session_files", 0),
                "worktree_changes": last_unhealthy_data.get("worktree_changes", 0),
            }
            failure_history.append(failure_record)
            
            if attempt < max_attempts:
                # Respawn (first failure)
                respawn_event = {
                    "kind": "respawn",
                    "attempt": attempt,
                    "reason": "unhealthy_detected",
                    "timestamp": timestamp
                }
                print(json.dumps(respawn_event, ensure_ascii=False), flush=True)
                log(f"Respawning (attempt {attempt + 1}/{max_attempts})...")
                time.sleep(5)  # Brief backoff
                continue
            else:
                # Escalate (second failure)
                # Diagnose death cause
                death_cause = diagnose_death_cause(failure_record, run_log, session_dirs)
                
                escalate_event = {
                    "kind": "escalate",
                    "reason": "max_respawn_exceeded",
                    "attempts": attempt,
                    "death_cause": death_cause,
                    "failure_history": failure_history,
                    "timestamp": timestamp
                }
                print(json.dumps(escalate_event, ensure_ascii=False), flush=True)
                log(f"Maximum respawn attempts ({max_attempts}) exceeded, escalating...")
                log(f"Death cause diagnosed: {death_cause}")
                
                # Report death to gate via lybra_task_progress
                report_death_to_gate(gate_client, card_id, actor, death_cause, failure_history)
                
                write_escalate_file(product_repo, card_id, spawn_cmd, failure_history)
                return EXIT_ESCALATE
        else:
            # Process ended normally (not unhealthy)
            log("Process ended normally (no unhealthy detected)")
            return EXIT_OK
    
    return EXIT_OK


def init_gate_client(connection_json: Path, gate_url: str) -> GateClient | None:
    """Initialize gate client for death reporting (executor token)."""
    try:
        if not connection_json.exists():
            log(f"WARNING: connection.json not found: {connection_json}")
            return None
        
        conn_data = json.loads(connection_json.read_text(encoding="utf-8"))
        executor_token = None
        for item in conn_data.get("tokens", []):
            if isinstance(item, dict) and item.get("role") == "executor":
                executor_token = item.get("token", "").strip()
                break
        
        if not executor_token:
            log("WARNING: executor token not found in connection.json")
            return None
        
        gate_client = GateClient(gate_url, executor_token)
        gate_client.initialize()
        log(f"Gate client initialized for death reporting")
        return gate_client
    
    except Exception as exc:
        log(f"WARNING: Failed to initialize gate client: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `lybra agent supervise`."""
    import argparse
    
    parser = argparse.ArgumentParser(
        prog="lybra agent supervise",
        description="AIPOS-295: Health monitoring with bounded auto-restart. "
                    "Spawns a command, monitors health via watch --stream --health, "
                    "and implements bounded self-healing (1 respawn, then ESCALATE)."
    )
    parser.add_argument("--spawn-cmd", required=True, help="Command to spawn (must include timeout wrapper)")
    parser.add_argument("--workspace-root", required=True, type=Path, help="Lybra workspace root")
    parser.add_argument("--product-repo", type=Path, help="Product repo root (default: ~/projects/lybra)")
    parser.add_argument("--card-id", required=True, help="Task card ID (for ESCALATE file)")
    parser.add_argument("--health-interval", type=float, default=300, help="Health check interval seconds (default: 300)")
    parser.add_argument("--pid-file", help="PID file path (optional, for process monitoring)")
    parser.add_argument("--proc-pattern", help="Process name pattern (e.g., 'node' for pi)")
    parser.add_argument("--session-dirs", help="Comma-separated session directories")
    parser.add_argument("--worktree-path", help="Git worktree path (default: product-repo)")
    parser.add_argument("--run-log", help="Run log path (for stall detection)")
    parser.add_argument("--gate-url", help="Gate URL (for death reporting, e.g., http://127.0.0.1:7118)")
    parser.add_argument("--connection-json", type=Path, help="Connection.json path (for gate auth)")
    parser.add_argument("--actor", help="Agent instance name (for death reporting, e.g., exec.lybra.kiwiai-dev)")
    
    args = parser.parse_args(argv)
    
    workspace_root = args.workspace_root.expanduser().resolve()
    product_repo = (args.product_repo or Path.home() / "projects" / "lybra").expanduser().resolve()
    
    if not workspace_root.is_dir():
        log(f"ERROR: workspace-root does not exist: {workspace_root}")
        return EXIT_ERROR
    if not product_repo.is_dir():
        log(f"ERROR: product-repo does not exist: {product_repo}")
        return EXIT_ERROR
    
    worktree_path = args.worktree_path or str(product_repo)
    
    # Initialize gate client if credentials provided
    gate_client = None
    if args.gate_url and args.connection_json:
        gate_client = init_gate_client(args.connection_json, args.gate_url)
    
    actor = args.actor or "exec.lybra.kiwiai-dev"
    
    try:
        return run_supervise(
            spawn_cmd=args.spawn_cmd,
            workspace_root=workspace_root,
            product_repo=product_repo,
            card_id=args.card_id,
            health_interval=args.health_interval,
            pid_file=args.pid_file,
            proc_pattern=args.proc_pattern,
            session_dirs=args.session_dirs,
            worktree_path=worktree_path,
            run_log=args.run_log,
            gate_client=gate_client,
            actor=actor,
        )
    except KeyboardInterrupt:
        log("Interrupted by user")
        return EXIT_OK
    except Exception as exc:
        log(f"FATAL: {exc}")
        import traceback
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
