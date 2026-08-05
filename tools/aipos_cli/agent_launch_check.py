"""AIPOS-295C — lybra agent launch-check: 开工确认 + 首刻失败自愈.

The launch-check command wraps a spawn command and verifies that the agent actually
starts working (not just process exists). It implements first-launch failure detection
and bounded self-healing:

1. **开工确认 (S1)**: Within a short window (default 90s), verify "真开工":
   - CPU increment > 0 (process is computing)
   - AND (session files created OR worktree changed)
   - On success: emit `kind:started` event with {task_id, executor, model, started_at}

2. **首刻失败即报 (S2)**: Within the launch window, detect:
   - Process died early (exit ≠ 0)
   - OR sustained 0-CPU with no artifacts (静默挂死)
   - Emit `kind:launch_failed` event with reason classification

3. **有界自愈 (S3)**: On launch_failed:
   - Auto-relaunch ONCE (emit `kind:relaunch` event, mark attempt=1)
   - Second failure → Write BLOCK file + emit `kind:blocked` event
   - STOP, wait for user decision (no infinite retry)
   - Model switch only via authorization (预授权策略: sonnet-5 → qwen3.7-plus)

4. **衔接 (S4)**:
   - After `started` event: hand off to 295 health monitoring (5-min heartbeat)
   - Integrates with 295B advisor pump (开工确认 is prerequisite for auto-billing)

Design reuse (AIPOS-295C governance):
- Reuses health/proc detection from agent_watch_fs.py (AIPOS-295)
- Complements agent_supervise.py (supervise = 过程中重启; launch-check = 首刻确认)

Exit codes:
- 0: started successfully (handed off to supervise/watch)
- 1: configuration error
- 2: launch failed twice, BLOCK written
- 130: SIGTERM/SIGINT clean exit
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

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2
EXIT_SIGNAL = 130

# Launch window defaults — AIPOS-332F4: 这些仅作为 CLI 未指定时的兆底,
# 实际值应由运行体档案(runtime_profiles.py)提供。代码不写死任何秒数。
DEFAULT_LAUNCH_WINDOW_SECS = 180  # AIPOS-332F4: 从 90→180,慢端点冷启动实测 ~60s 留裕量
DEFAULT_CHECK_INTERVAL_SECS = 5  # Poll interval during launch window
MIN_CPU_DELTA = 0.01  # Minimum CPU time delta to consider "computing"

# Model substitution policy (AIPOS-295C S3: 预授权策略)
DEFAULT_MODEL_FALLBACK_POLICY = {
    "sonnet-5": "qwen3.7-plus",
    "claude-sonnet-3-5-20241022": "qwen3.7-plus",
}


def log(msg: str, *, stream: Any = sys.stderr) -> None:
    """Timestamped log to stderr."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[launch-check {timestamp}] {msg}", file=stream, flush=True)


def emit_event(event: dict[str, Any]) -> None:
    """Emit JSON event to stdout (line-buffered)."""
    print(json.dumps(event, ensure_ascii=False), flush=True)


# F-332-01: 一次性 pump run 路径的失败类事件须落工作区(S10 硬约束2/3)。
# 与 AIPOS-323 事件通道同落位 5_tasks/records/events/<task_id>/;产品仓 BLOCK 文件
# 仅作详情附件(有产品仓才写),工作区事件无条件写。
WORKSPACE_EVENTS_REL = Path("5_tasks") / "records" / "events"


def _fm_yaml_scalar(value: Any) -> str:
    """把值格式化为 YAML 安全标量(frontmatter 用)。"""
    text = str(value or "").strip()
    if text == "":
        return '""'
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", "\n", "'", '"']) or text != text.strip():
        return "'" + text.replace("'", "''") + "'"
    return text


def _event_timestamp_slug(timestamp: str) -> str:
    """ISO8601 Z 时间戳 → 文件名安全 slug(与 AIPOS-323/daemon 同精度:秒)。"""
    return (
        timestamp.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    )


def write_event_to_workspace(
    workspace_root: Path,
    task_id: str,
    event: dict[str, Any],
    *,
    actor: str = "",
) -> Path:
    """F-332-01: 把 launch-check 的失败类事件持久化到工作区 events 目录。

    与 AIPOS-323 事件通道同落位 ``5_tasks/records/events/<task_id>/<kind>_<ts>.md``。
    工作区事件无条件写(不以产品仓是否存在为前提);产品仓 BLOCK 文件仅作详情附件。

    落库失败抛出(不吞掉,任务卡红线)——调用方据此响,不得静默。
    返回写入的文件路径。
    """
    events_dir = workspace_root / WORKSPACE_EVENTS_REL / task_id
    events_dir.mkdir(parents=True, exist_ok=True)
    kind = str(event.get("kind") or "event")
    timestamp = str(
        event.get("timestamp")
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    slug = _event_timestamp_slug(timestamp)
    event_file = events_dir / f"{kind}_{slug}.md"
    # 同秒内多事件稳妥去重(launch 间隔理论上≥5s,此处仍防御)
    n = 2
    while event_file.exists():
        event_file = events_dir / f"{kind}_{slug}_{n}.md"
        n += 1

    fm_lines = [
        "---",
        "record_type: launch_check_event",
        f"event_kind: {kind}",
        f"task_id: {task_id}",
        f"actor: {_fm_yaml_scalar(actor or 'launch-check')}",
        f"timestamp: {timestamp}",
    ]
    reason = event.get("reason")
    if reason:
        fm_lines.append(f"reason: {_fm_yaml_scalar(reason)}")
    if event.get("exit_code") is not None:
        fm_lines.append(f"exit_code: {event.get('exit_code')}")
    if event.get("attempt") is not None:
        fm_lines.append(f"attempt: {event.get('attempt')}")
    fm_lines.append("source: launch_check")
    fm_lines.append("---")

    body_lines = [
        f"# Launch-check event: {kind}",
        "",
        f"Task `{task_id}` 的 launch-check(一次性 pump run 路径)于 {timestamp} 报出 `{kind}`。",
        "",
    ]
    if reason:
        body_lines.append(f"- reason: {reason}")
    if event.get("attempt") is not None:
        body_lines.append(f"- attempt: {event.get('attempt')}")
    if event.get("exit_code") is not None:
        body_lines.append(f"- exit_code: {event.get('exit_code')}")
    body_lines.append("")
    body_lines.append(
        "> 本事件由 launch-check 写入(F-332-01),与 AIPOS-323 通道同落位;"
        "产品仓 BLOCK 文件仅作详情附件,不得作唯一载体。"
    )
    body_lines.append("")

    event_file.write_text(
        "\n".join(fm_lines) + "\n" + "\n".join(body_lines), encoding="utf-8"
    )
    log(f"Workspace event written: {event_file}")
    return event_file


def _find_pi_processes(pid: int) -> list[int]:
    """Find pi subprocess tree PIDs (excluding timeout wrapper).
    
    Args:
        pid: Parent process PID (typically timeout wrapper)
    
    Returns:
        List of PIDs (pi children, not timeout/bash shells)
    """
    if psutil is None:
        return []
    
    pids = []
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                cmdline = ' '.join(child.cmdline()).lower()
                # Skip timeout/bash wrappers, keep actual pi processes
                if 'timeout' not in cmdline and 'bash' not in cmdline:
                    pids.append(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    
    return pids


def _get_process_cpu_time(pids: list[int]) -> float:
    """Get total CPU time (user+system) for a list of PIDs.
    
    Returns sum of cpu_times (seconds). Returns 0.0 if psutil unavailable.
    """
    if psutil is None or not pids:
        return 0.0
    
    total = 0.0
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            cpu_times = proc.cpu_times()
            total += cpu_times.user + cpu_times.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _count_new_files(directories: list[str], since_timestamp: float) -> int:
    """Count files created/modified in directories since timestamp.
    
    Args:
        directories: List of directory paths to scan
        since_timestamp: Unix timestamp cutoff
    
    Returns:
        Number of files with mtime > since_timestamp
    """
    count = 0
    for dir_path in directories:
        if not os.path.isdir(dir_path):
            continue
        for root, _dirs, files in os.walk(dir_path):
            for name in files:
                full = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(full)
                    if mtime > since_timestamp:
                        count += 1
                except OSError:
                    continue
    return count


def _snapshot_session_dirs(directories: list[str]) -> list[dict[str, Any]]:
    """AIPOS-332F4 修三:判死时刻会话目录快照(便于事后核对)。

    对每个会话目录列出最近修改的文件(最多 10 个),含 mtime 精确到秒。
    用于复现“判死后 N 秒出文件”类问题的取证。
    """
    snapshot: list[dict[str, Any]] = []
    for dir_path in directories:
        entry: dict[str, Any] = {"dir": dir_path, "exists": os.path.isdir(dir_path), "files": []}
        if not entry["exists"]:
            snapshot.append(entry)
            continue
        # 收集目录下最近修改的文件(最多 10 个)
        file_entries: list[dict[str, Any]] = []
        try:
            for root, _dirs, files in os.walk(dir_path):
                for name in files:
                    full = os.path.join(root, name)
                    try:
                        mtime = os.path.getmtime(full)
                        mtime_iso = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        file_entries.append({"path": full, "mtime": mtime_iso, "mtime_ts": mtime})
                    except OSError:
                        continue
        except OSError:
            pass
        # 按 mtime 降序取前 10
        file_entries.sort(key=lambda e: e["mtime_ts"], reverse=True)
        entry["files"] = [{"path": e["path"], "mtime": e["mtime"]} for e in file_entries[:10]]
        entry["total_files"] = len(file_entries)
        snapshot.append(entry)
    return snapshot


def _has_worktree_changes(worktree_path: str) -> bool:
    """Check if git worktree has changes (new/modified files).
    
    Uses git status --porcelain. Returns False if not a git repo or git unavailable.
    """
    if not os.path.isdir(worktree_path):
        return False
    
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    
    return False


def _kill_process_tree(pid: int) -> None:
    """Kill process and all descendants (including timeout wrapper and pi subprocess)."""
    if psutil is None:
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
        
        # SIGTERM all
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            pass
        
        time.sleep(2)
        
        # SIGKILL survivors
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


def _extract_model_from_command(spawn_cmd: str) -> str | None:
    """Extract model identifier from spawn command.
    
    Heuristic: look for --model or -m flags, or common model names in command.
    Returns None if model cannot be determined.
    """
    # Try --model or -m flags
    parts = spawn_cmd.split()
    for i, part in enumerate(parts):
        if part in ('--model', '-m') and i + 1 < len(parts):
            return parts[i + 1].strip('"\'')
    
    # Fallback: look for common model names in command
    common_models = ['sonnet', 'claude', 'gpt', 'qwen', 'o1', 'deepseek']
    for model in common_models:
        if model in spawn_cmd.lower():
            return model
    
    return None


def _substitute_model_in_command(spawn_cmd: str, old_model: str, new_model: str) -> str:
    """Substitute model in spawn command.
    
    Replaces --model <old> with --model <new>, or falls back to simple string replacement.
    """
    # Try to replace --model flag value
    import re
    pattern = r'(--model\s+|^-m\s+)([^\s]+)'
    match = re.search(pattern, spawn_cmd)
    if match:
        return spawn_cmd[:match.start(2)] + new_model + spawn_cmd[match.end(2):]
    
    # Fallback: simple string replacement
    return spawn_cmd.replace(old_model, new_model)


def write_block_file(
    product_repo: Path,
    card_id: str,
    spawn_cmd: str,
    failure_history: list[dict[str, Any]],
    model_fallback_policy: dict[str, str] | None = None,
) -> Path:
    """Write BLOCK file when launch fails twice (bounded retry exhausted).
    
    Similar to ESCALATE but for first-launch failures. Suggests model switch if policy exists.
    """
    block_dir = product_repo / "task_cards" / card_id
    block_dir.mkdir(parents=True, exist_ok=True)
    
    n = 1
    while (block_dir / f"BLOCK-launch-{n}.md").exists():
        n += 1
    
    block_file = block_dir / f"BLOCK-launch-{n}.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Format failure history
    history_lines = []
    for i, failure in enumerate(failure_history, 1):
        history_lines.append(f"### Attempt {i}")
        history_lines.append(f"- Time: {failure.get('timestamp', 'unknown')}")
        history_lines.append(f"- Reason: {failure.get('reason', 'unknown')}")
        history_lines.append(f"- Exit code: {failure.get('exit_code', 'N/A')}")
        history_lines.append(f"- Process alive: {failure.get('proc_alive', False)}")
        history_lines.append(f"- CPU delta: {failure.get('cpu_delta', 0)}")
        history_lines.append(f"- Session files: {failure.get('new_session_files', 0)}")
        history_lines.append(f"- Worktree changes: {failure.get('worktree_changed', False)}")
        # AIPOS-332F4 修二/修三:判死留证字段
        if "actual_waited_secs" in failure:
            history_lines.append(f"- Actual waited (s): {failure['actual_waited_secs']}")
        if "window_config_secs" in failure:
            history_lines.append(f"- Window config (s): {failure['window_config_secs']}")
        if "judged_at" in failure:
            history_lines.append(f"- Judged at: {failure['judged_at']}")
        # 会话目录快照(修三:便于核对“判死后 N 秒出文件”)
        snapshot = failure.get("session_snapshot") or []
        if snapshot:
            history_lines.append("- Session snapshot at judgment:")
            for snap in snapshot:
                dir_exists = snap.get("exists", False)
                total = snap.get("total_files", 0)
                history_lines.append(f"    - dir={snap['dir']} exists={dir_exists} total_files={total}")
                for f_entry in snap.get("files", [])[:5]:
                    history_lines.append(f"      - {f_entry['path']} mtime={f_entry['mtime']}")
        history_lines.append("")
    
    # Model switch suggestion
    current_model = _extract_model_from_command(spawn_cmd)
    suggested_model = None
    if current_model and model_fallback_policy:
        suggested_model = model_fallback_policy.get(current_model)
    
    model_suggestion = ""
    if suggested_model:
        new_cmd = _substitute_model_in_command(spawn_cmd, current_model, suggested_model)
        model_suggestion = f"""
## 预授权模型切换建议

根据 AIPOS-295C 预授权策略，当前模型 `{current_model}` 首刻失败，建议切换到:
- **推荐模型**: `{suggested_model}`
- **新命令**: `{new_cmd}`

切换命令:
```bash
# 使用推荐模型重启
{new_cmd}
```
"""
    else:
        model_suggestion = """
## 需要决策

当前命令未能提取模型信息，或无预授权回退策略。请人工决策:
1. 检查模型可用性（quota/route/provider）
2. 尝试不同模型（需 Owner 授权）
3. 检查任务卡与模型兼容性
"""
    
    content = f"""# BLOCK — {card_id} 首刻失败

- Time: {timestamp}
- Spawn command: `{spawn_cmd}`
- Trigger: 开工确认失败，有界重拉 (1次) 已用尽

## 失败历史

{chr(10).join(history_lines)}

## 失败模式

Pattern: {failure_history[-1].get('reason', 'unknown')} 持续 {len(failure_history)} 次尝试。

可能原因:
- **进程早退**: 模型启动失败、凭据错误、路由不可达
- **静默挂死**: 模型容量耗尽、prompt 过长、任务与模型不兼容
- **环境问题**: 依赖缺失、权限不足、网络故障

{model_suggestion}

## 执行停止

Launch-check 已停止（exit {EXIT_BLOCKED}），不会无限重试。

下一步:
1. **如果切换模型**: 按上述建议修改 spawn 命令，重新派工
2. **如果排查环境**: 检查日志、凭据、网络，修复后重试
3. **如果任务问题**: 检查任务卡，调整 prompt 或车道声明

重启方法:
```bash
# 方案1: 直接重新派工（如果环境已修复）
lybra agent launch-check --spawn-cmd '<原命令>' ...

# 方案2: 切换模型后派工（如果预授权）
lybra agent launch-check --spawn-cmd '<新命令带新模型>' ...
```

AIPOS-295C 红线: 换模型需授权，不擅自提权。本次停止等待人工决策。
"""
    
    block_file.write_text(content, encoding="utf-8")
    log(f"BLOCK file written: {block_file}")
    return block_file


def check_launch(
    spawn_cmd: str,
    task_id: str,
    executor_instance: str,
    product_repo: Path,
    session_dirs: list[str],
    worktree_path: str,
    launch_window_secs: float,
    check_interval_secs: float,
    model_fallback_policy: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Check if spawned process successfully launches (真开工).
    
    Args:
        spawn_cmd: Command to spawn (must include timeout wrapper)
        task_id: Task card ID
        executor_instance: Executor agent instance name
        product_repo: Product repo root path
        session_dirs: List of session directories to monitor
        worktree_path: Git worktree path to monitor
        launch_window_secs: Time window to verify launch (default 180s, AIPOS-332F4)
        check_interval_secs: Poll interval during launch window
        model_fallback_policy: Model substitution policy (optional)
    
    Returns:
        Tuple of (exit_code, failure_data_if_failed)
        - (EXIT_OK, None) on successful launch
        - (EXIT_ERROR, failure_data) on launch failure
    """
    log(f"Spawning command: {spawn_cmd[:100]}...")
    
    # AIPOS-327F1: Safe kickoff transmission using pi-supported channel
    # Extract kickoff from --append-system-prompt and validate safe transmission
    import re
    import tempfile
    
    temp_kickoff_file = None
    safe_spawn_cmd = spawn_cmd
    
    # Match --append-system-prompt '...' or "..." in spawn_cmd
    prompt_pattern = r"--append-system-prompt\s+(['\"])(.+?)\1"
    match = re.search(prompt_pattern, spawn_cmd, re.DOTALL)
    
    if match:
        kickoff_content = match.group(2)
        
        # Check if kickoff contains shell-interpretation hazards
        hazards = ["`", "$(", "${", "\n"]
        has_hazards = any(h in kickoff_content for h in hazards)
        
        if has_hazards:
            log("Kickoff contains shell-interpretation hazards, using safe file transmission")
            
            # Write kickoff to temporary file
            try:
                fd, temp_path = tempfile.mkstemp(suffix=".txt", prefix="lybra_kickoff_")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(kickoff_content)
                temp_kickoff_file = Path(temp_path)
                
                # Replace --append-system-prompt '...' with --append-system-prompt @tempfile
                # AIPOS-327F1: Use pi-supported --append-system-prompt @file syntax
                safe_spawn_cmd = spawn_cmd[:match.start()] + f"--append-system-prompt @{temp_path}" + spawn_cmd[match.end():]
                log(f"Kickoff written to {temp_path}, using --append-system-prompt @file syntax")
            except Exception as exc:
                log(f"ERROR: Failed to write kickoff to temp file: {exc}")
                log("Cannot proceed with unsafe command, aborting")
                # AIPOS-327F1 F-327R-02: Fail instead of falling back to unsafe command
                return EXIT_ERROR, {
                    "reason": "kickoff_transmission_failed",
                    "error": str(exc),
                    "exit_code": None,
                }
    
    # Spawn the command
    try:
        proc = subprocess.Popen(
            safe_spawn_cmd,
            shell=True,
            cwd=str(product_repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except Exception as exc:
        log(f"ERROR: Failed to spawn command: {exc}")
        # Clean up temp file if created
        if temp_kickoff_file and temp_kickoff_file.exists():
            try:
                temp_kickoff_file.unlink()
            except OSError:
                pass
        return EXIT_ERROR, {
            "reason": "spawn_failed",
            "error": str(exc),
            "exit_code": None,
        }
    
    log(f"Spawned process PID {proc.pid}")
    
    # Find pi subprocess PIDs
    start_time = time.time()
    last_cpu_time = 0.0
    initial_cpu_check_done = False
    
    # Give process a moment to settle before first CPU measurement
    time.sleep(2)
    
    while time.time() - start_time < launch_window_secs:
        # Check if process died
        exit_code = proc.poll()
        if exit_code is not None:
            log(f"Process exited early with code {exit_code}")
            
            # Clean up temp kickoff file
            if temp_kickoff_file and temp_kickoff_file.exists():
                try:
                    temp_kickoff_file.unlink()
                except OSError:
                    pass
            
            return EXIT_ERROR, {
                "reason": "process_early_exit",
                "exit_code": exit_code,
                "proc_alive": False,
                "cpu_delta": 0.0,
                "new_session_files": 0,
                "worktree_changed": False,
                # AIPOS-332F4: 留证字段(进程早退也要记录实际等待与窗口配置)
                "actual_waited_secs": round(time.time() - start_time, 1),
                "window_config_secs": launch_window_secs,
                "session_snapshot": _snapshot_session_dirs(session_dirs),
                "judged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        
        # Find pi subprocess PIDs (exclude timeout wrapper)
        pi_pids = _find_pi_processes(proc.pid)
        
        if not pi_pids:
            # No pi subprocess found yet, keep waiting
            time.sleep(check_interval_secs)
            continue
        
        # Measure CPU activity
        current_cpu_time = _get_process_cpu_time(pi_pids)
        
        if not initial_cpu_check_done:
            # First measurement is baseline
            last_cpu_time = current_cpu_time
            initial_cpu_check_done = True
            time.sleep(check_interval_secs)
            continue
        
        cpu_delta = current_cpu_time - last_cpu_time
        last_cpu_time = current_cpu_time
        
        # Check for artifacts (session files or worktree changes)
        new_session_files = _count_new_files(session_dirs, start_time)
        worktree_changed = _has_worktree_changes(worktree_path)
        
        log(f"Launch check: cpu_delta={cpu_delta:.2f}s, session_files={new_session_files}, worktree_changed={worktree_changed}")
        
        # S1: 真开工判据 — CPU增量 > 0 AND (会话文件 OR 工作树变化)
        if cpu_delta >= MIN_CPU_DELTA and (new_session_files > 0 or worktree_changed):
            log(f"Launch confirmed! Process is computing and producing artifacts.")
            
            # Extract model from command
            model = _extract_model_from_command(spawn_cmd)
            
            # Emit started event
            started_event = {
                "kind": "started",
                "task_id": task_id,
                "executor_instance": executor_instance,
                "model": model or "unknown",
                "started_at": datetime.now(timezone.utc).isoformat() + "Z",
                "pid": proc.pid,
                "pi_pids": pi_pids,
            }
            emit_event(started_event)
            
            log(f"Started event emitted. Handing off to supervise/watch for ongoing monitoring.")
            
            # Clean up temp kickoff file
            if temp_kickoff_file and temp_kickoff_file.exists():
                try:
                    temp_kickoff_file.unlink()
                    log(f"Cleaned up temp kickoff file: {temp_kickoff_file}")
                except OSError:
                    pass
            
            return EXIT_OK, None
        
        # Continue polling
        time.sleep(check_interval_secs)
    
    # Launch window expired, still no signs of life
    # AIPOS-332F4 修二:窗口耗尽才可判死;窗口内进程存活不得提前判死。
    # 此处是循环正常出口(时间耗尽),允许判死。
    actual_waited_secs = time.time() - start_time
    exit_code = proc.poll()
    pi_pids = _find_pi_processes(proc.pid)
    proc_alive = exit_code is None and len(pi_pids) > 0

    current_cpu_time = _get_process_cpu_time(pi_pids)
    cpu_delta = current_cpu_time - last_cpu_time if initial_cpu_check_done else 0.0

    new_session_files = _count_new_files(session_dirs, start_time)
    worktree_changed = _has_worktree_changes(worktree_path)

    # AIPOS-332F4 修三:判死时刻会话目录快照(便于事后核对“判死后 N 秒出文件”)
    session_snapshot = _snapshot_session_dirs(session_dirs)

    # Determine failure reason
    if not proc_alive:
        reason = "process_gone"
    elif cpu_delta < MIN_CPU_DELTA and new_session_files == 0 and not worktree_changed:
        reason = "silent_hang"  # 静默挂死
    else:
        reason = "insufficient_activity"

    log(f"Launch FAILED: {reason} (waited {actual_waited_secs:.1f}s / window {launch_window_secs}s)")

    # Kill the hung/failed process
    _kill_process_tree(proc.pid)

    # Clean up temp kickoff file
    if temp_kickoff_file and temp_kickoff_file.exists():
        try:
            temp_kickoff_file.unlink()
            log(f"Cleaned up temp kickoff file: {temp_kickoff_file}")
        except OSError:
            pass

    return EXIT_ERROR, {
        "reason": reason,
        "exit_code": exit_code,
        "proc_alive": proc_alive,
        "cpu_delta": cpu_delta,
        "new_session_files": new_session_files,
        "worktree_changed": worktree_changed,
        # AIPOS-332F4 修二/修三:判死留证字段
        "actual_waited_secs": round(actual_waited_secs, 1),
        "window_config_secs": launch_window_secs,
        "session_snapshot": session_snapshot,
        "judged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def run_launch_check(
    spawn_cmd: str,
    task_id: str,
    executor_instance: str,
    product_repo: Path,
    session_dirs: list[str],
    worktree_path: str,
    launch_window_secs: float,
    check_interval_secs: float,
    model_fallback_policy: dict[str, str] | None = None,
    workspace_root: Path | None = None,
) -> int:
    """Main launch-check loop with bounded retry (S3: 有界自愈).

    workspace_root: 若提供,则把 launch_failed/blocked 失败类事件无条件落工作区
        ``5_tasks/records/events/<task_id>/``(F-332-01,S10 硬约束2/3);为 None 时
        保持旧行为(仅 stdout + 产品仓 BLOCK)——daemon 路径不传,行为不变。

    Returns:
        EXIT_OK (0): launched successfully
        EXIT_BLOCKED (2): failed twice, BLOCK written
        EXIT_ERROR (1): configuration error
    """
    failure_history: list[dict[str, Any]] = []
    max_attempts = 2  # Initial launch + 1 relaunch
    
    for attempt in range(1, max_attempts + 1):
        log(f"Launch attempt {attempt}/{max_attempts}")
        
        exit_code, failure_data = check_launch(
            spawn_cmd=spawn_cmd,
            task_id=task_id,
            executor_instance=executor_instance,
            product_repo=product_repo,
            session_dirs=session_dirs,
            worktree_path=worktree_path,
            launch_window_secs=launch_window_secs,
            check_interval_secs=check_interval_secs,
            model_fallback_policy=model_fallback_policy,
        )
        
        if exit_code == EXIT_OK:
            # Success!
            return EXIT_OK
        
        # Launch failed
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        failure_record = {
            "timestamp": timestamp,
            "attempt": attempt,
            **failure_data,
        }
        failure_history.append(failure_record)
        
        # S2: Emit launch_failed event
        launch_failed_event = {
            "kind": "launch_failed",
            "task_id": task_id,
            "attempt": attempt,
            "reason": failure_data.get("reason"),
            "exit_code": failure_data.get("exit_code"),
            "timestamp": timestamp,
        }
        emit_event(launch_failed_event)
        if workspace_root is not None:
            # F-332-01: 失败类事件无条件落工作区(不以产品仓是否存在为前提)
            write_event_to_workspace(
                workspace_root, task_id, launch_failed_event, actor=executor_instance
            )

        if attempt < max_attempts:
            # S3: Relaunch (first failure)
            relaunch_event = {
                "kind": "relaunch",
                "task_id": task_id,
                "attempt": attempt,
                "reason": "launch_failed",
                "timestamp": timestamp,
            }
            emit_event(relaunch_event)
            log(f"Relaunching (attempt {attempt + 1}/{max_attempts})...")
            time.sleep(5)  # Brief backoff
        else:
            # S3: BLOCK (second failure)
            blocked_event = {
                "kind": "blocked",
                "task_id": task_id,
                "reason": "max_launch_attempts_exceeded",
                "attempts": attempt,
                "failure_history": failure_history,
                "timestamp": timestamp,
            }
            emit_event(blocked_event)
            if workspace_root is not None:
                # F-332-01: 失败类事件无条件落工作区(不以产品仓是否存在为前提)。
                # 落库失败抛出(不吞掉,任务卡红线)——BLOCK 附件仅作详情补充。
                write_event_to_workspace(
                    workspace_root, task_id, blocked_event, actor=executor_instance
                )
            log(f"Maximum launch attempts ({max_attempts}) exceeded. Writing BLOCK file...")
            
            write_block_file(
                product_repo=product_repo,
                card_id=task_id,
                spawn_cmd=spawn_cmd,
                failure_history=failure_history,
                model_fallback_policy=model_fallback_policy,
            )
            
            log("BLOCK file written. Stopping (exit 2). Manual intervention required.")
            return EXIT_BLOCKED
    
    return EXIT_BLOCKED  # Unreachable, but for completeness


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `lybra agent launch-check`."""
    import argparse
    
    parser = argparse.ArgumentParser(
        prog="lybra agent launch-check",
        description="AIPOS-295C: 开工确认 + 首刻失败自愈. "
                    "Spawns a command and verifies the agent actually starts working. "
                    "Implements bounded retry (1 relaunch) and writes BLOCK on double failure."
    )
    parser.add_argument("--spawn-cmd", required=True, help="Command to spawn (must include timeout wrapper)")
    parser.add_argument("--task-id", required=True, help="Task card ID (e.g., AIPOS-295C)")
    parser.add_argument("--executor-instance", required=True, help="Executor agent instance name")
    parser.add_argument("--product-repo", type=Path, help="Product repo root (default: ~/projects/lybra)")
    parser.add_argument("--session-dirs", help="Comma-separated session directories to monitor")
    parser.add_argument("--worktree-path", help="Git worktree path (default: product-repo)")
    parser.add_argument("--launch-window", type=float, default=DEFAULT_LAUNCH_WINDOW_SECS,
                        help=f"Launch verification window seconds (default: {DEFAULT_LAUNCH_WINDOW_SECS})")
    parser.add_argument("--check-interval", type=float, default=DEFAULT_CHECK_INTERVAL_SECS,
                        help=f"Poll interval seconds (default: {DEFAULT_CHECK_INTERVAL_SECS})")
    parser.add_argument("--workspace-root", type=Path, default=None,
                        help="[AIPOS-332F1] 治理工作区根;提供后 launch_failed/blocked "
                             "事件无条件落 5_tasks/records/events/<task_id>/(F-332-01)")
    parser.add_argument("--model-fallback-policy", help="JSON file with model substitution policy (optional)")
    
    args = parser.parse_args(argv)
    
    product_repo = (args.product_repo or Path.home() / "projects" / "lybra").expanduser().resolve()
    
    if not product_repo.is_dir():
        log(f"ERROR: product-repo does not exist: {product_repo}")
        return EXIT_ERROR
    
    session_dirs = args.session_dirs.split(",") if args.session_dirs else []
    worktree_path = args.worktree_path or str(product_repo)
    
    # Load model fallback policy if provided
    model_fallback_policy = None
    if args.model_fallback_policy:
        try:
            policy_path = Path(args.model_fallback_policy).expanduser().resolve()
            model_fallback_policy = json.loads(policy_path.read_text())
            log(f"Loaded model fallback policy: {model_fallback_policy}")
        except Exception as exc:
            log(f"WARNING: Failed to load model fallback policy: {exc}")
            # Use default policy
            model_fallback_policy = DEFAULT_MODEL_FALLBACK_POLICY
    else:
        # Use default policy
        model_fallback_policy = DEFAULT_MODEL_FALLBACK_POLICY
    
    try:
        return run_launch_check(
            spawn_cmd=args.spawn_cmd,
            task_id=args.task_id,
            executor_instance=args.executor_instance,
            product_repo=product_repo,
            session_dirs=session_dirs,
            worktree_path=worktree_path,
            launch_window_secs=args.launch_window,
            check_interval_secs=args.check_interval,
            model_fallback_policy=model_fallback_policy,
            workspace_root=args.workspace_root,
        )
    except KeyboardInterrupt:
        log("Interrupted by user")
        return EXIT_SIGNAL
    except Exception as exc:
        log(f"FATAL: {exc}")
        import traceback
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
