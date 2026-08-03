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
                escalate_event = {
                    "kind": "escalate",
                    "reason": "max_respawn_exceeded",
                    "attempts": attempt,
                    "failure_history": failure_history,
                    "timestamp": timestamp
                }
                print(json.dumps(escalate_event, ensure_ascii=False), flush=True)
                log(f"Maximum respawn attempts ({max_attempts}) exceeded, escalating...")
                
                write_escalate_file(product_repo, card_id, spawn_cmd, failure_history)
                return EXIT_ESCALATE
        else:
            # Process ended normally (not unhealthy)
            log("Process ended normally (no unhealthy detected)")
            return EXIT_OK
    
    return EXIT_OK


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
