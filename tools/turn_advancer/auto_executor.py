"""AIPOS-340F6 — turn-advancer ``--mode auto`` 真执行出口。

把 resolver 解析出的命令在 auto 模式下**真执行**(``subprocess``),退出码透传为 CLI
退出码;执行前后各落一条事件(append-only,一文件一事件,时间戳命名永不覆盖);
``requires_human_judgment=true`` 或命令含"等待人工"占位时**拒绝执行**并明确说明(判断留人)。

manual 模式不经过本模块(零回归):resolver 保持纯解析,执行是 CLI 层在 auto 模式下对
解析结果的后置动作。

信任边界:被执行的 ``copyable_line`` 由 ``command_builder`` 内部生成(非任意用户输入),
等价于人工逐字粘贴执行(dogfood 实证 2026-08-05 的兜底动作);故用 ``shell=True`` 执行。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# 拒绝执行时的专属退出码。选 0 太乐观(与"执行成功"混淆);选 1 易与子进程失败码 1 混淆。
# 用 60 明确标记"auto 判定不执行(判断留人/占位/终态非 done)",CLI 可据码区分。
REFUSE_EXIT_CODE = 60

# 可执行命令类型:mcp_verb(经 copyable_line 调 gate)/ cli(shell 命令)。
# wait_human / done / unknown 一律不执行(占位或终态)。
EXECUTABLE_COMMAND_TYPES = {"mcp_verb", "cli"}

# "等待人工"占位标记:copyable_line 命中任一即拒绝(判断留人)。
HUMAN_PLACEHOLDER_MARKERS = (
    "等待人工",
    "wait_human",
    "<等待人工",
    "等待执行体完成",
    "等待人工判断",
)


def _is_human_placeholder(copyable_line: str) -> bool:
    text = copyable_line or ""
    return any(marker in text for marker in HUMAN_PLACEHOLDER_MARKERS)


def should_execute(result: dict[str, Any]) -> tuple[bool, str]:
    """决定 auto 模式是否真执行;返回 ``(是否执行, 不执行原因)``。

    拒绝(返回 False)的三类情形,均"判断留人":
      1. ``requires_human_judgment=true``;
      2. ``command_type`` 非可执行类型(``wait_human``/``done``/``unknown``);
      3. ``copyable_line`` 含"等待人工"占位。
    """
    if result.get("requires_human_judgment"):
        reason = result.get("human_judgment_reason") or "requires_human_judgment=true"
        return False, f"requires_human_judgment(判断留人,auto 拒绝执行): {reason}"
    ctype = result.get("command_type")
    if ctype not in EXECUTABLE_COMMAND_TYPES:
        return False, f"command_type={ctype!r} 非可执行类型(占位/终态/未知),不执行"
    copyable = result.get("copyable_line") or ""
    if _is_human_placeholder(copyable):
        return False, "命令含'等待人工'占位(auto 拒绝执行,判断留人)"
    if not copyable.strip():
        return False, "copyable_line 为空,无可执行命令"
    return True, ""


def _events_dir(workspace_root: Path, task_id: str) -> Path:
    """执行留痕事件目录(与 state_reader 读事件同位:<ws>/5_tasks/records/events/<id>)."""
    return Path(workspace_root) / "5_tasks" / "records" / "events" / task_id


def _write_event(
    events_dir: Path,
    *,
    task_id: str,
    event_type: str,
    actor: str,
    reason: str,
    command: str,
    result_detail: str,
    now: Callable[[], datetime],
) -> Path | None:
    """落一条执行留痕事件。append-only:一文件一事件,时间戳命名,撞名加序号,永不覆盖。

    frontmatter 字段对齐 state_reader._normalize_event_type(优先读 event_type),
    故这些事件会被 state_reader 正常读回(可观测、可审计)。
    """
    events_dir.mkdir(parents=True, exist_ok=True)
    moment = now()
    stamp = moment.strftime("%Y%m%d_%H%M%S")
    iso = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = events_dir / f"{event_type}_{stamp}.md"
    seq = 1
    while path.exists():  # 同秒撞名 → 加序号,保证 append-only 不覆盖
        path = events_dir / f"{event_type}_{stamp}_{seq}.md"
        seq += 1
    # command / detail 可能含反引号/换行,用代码块包裹,避免破坏 frontmatter 与正文结构。
    content = (
        "---\n"
        f"record_type: auto_exec_event\n"
        f"event_type: {event_type}\n"
        f"task_id: {task_id}\n"
        f"actor: {actor}\n"
        f"timestamp: '{iso}'\n"
        "source: turn_advancer_auto\n"
        "---\n\n"
        f"# Auto-Exec Event: {event_type}\n\n"
        f"- task: `{task_id}`\n"
        f"- actor: `{actor}`\n"
        f"- reason: {reason}\n"
        f"- command:\n  ```\n  {command}\n  ```\n"
        f"- result: {result_detail}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def execute_auto(
    result: dict[str, Any],
    workspace_root: Path,
    *,
    actor: str = "turn_advancer_auto",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    now: Callable[[], datetime] | None = None,
    write_events: bool = True,
) -> dict[str, Any]:
    """auto 模式执行出口:真执行 + 退出码透传 + 执行前后留痕。

    参数:
        result: ``resolve_next_command`` 的返回(纯解析结果)。
        workspace_root: 工作区根(执行留痕事件落点)。
        actor: 留痕事件里的 actor(谁跑了这次 auto 派工)。
        runner: 子进程执行器(默认 ``subprocess.run``;测试可注入桩)。
        now: 时钟(测试可注入固定时间)。
        write_events: 是否落留痕事件(测试可关)。

    返回::

        {
          "execution_status": "executed" | "refused" | "skipped",
          "exit_code": int,            # executed→子进程码透传;refused→REFUSE_EXIT_CODE;skipped→0
          "stdout": str, "stderr": str,
          "reason": str,
          "command": str,              # 实际执行的 copyable_line
          "events": {"before": path|None, "after": path|None},
        }
    """
    clock = now or (lambda: datetime.now(timezone.utc))
    task_id = result.get("task_id", "unknown")
    copyable = result.get("copyable_line") or ""
    rule = result.get("rule", "")
    events_dir = _events_dir(workspace_root, task_id)

    do_it, reason = should_execute(result)
    if not do_it:
        # done(终态,无事可做)→ skipped/exit 0;其余(wait_human/占位/判断留人)→ refused
        is_done = result.get("command_type") == "done"
        status = "skipped" if is_done else "refused"
        after_path = None
        if write_events and status == "refused":
            after_path = _write_event(
                events_dir, task_id=task_id, event_type="auto_exec_refused",
                actor=actor, reason=reason, command=copyable,
                result_detail="not executed (judgment left to human)", now=clock,
            )
        return {
            "execution_status": status,
            "exit_code": 0 if status == "skipped" else REFUSE_EXIT_CODE,
            "stdout": "",
            "stderr": reason,
            "reason": reason,
            "command": copyable,
            "events": {"before": None, "after": str(after_path) if after_path else None},
        }

    # —— 真执行 ——
    before_path = None
    after_path = None
    if write_events:
        before_path = _write_event(
            events_dir, task_id=task_id, event_type="auto_exec_start",
            actor=actor, reason=rule, command=copyable,
            result_detail="dispatched (about to execute)", now=clock,
        )

    completed = runner(copyable, shell=True, capture_output=True, text=True)
    exit_code = int(completed.returncode)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    outcome = "succeeded" if exit_code == 0 else f"failed(exit={exit_code})"

    if write_events:
        after_path = _write_event(
            events_dir, task_id=task_id, event_type="auto_exec_complete",
            actor=actor, reason=rule, command=copyable,
            result_detail=(
                f"{outcome}; exit_code={exit_code}; "
                f"stdout={stdout[:400]!r}; stderr={stderr[:400]!r}"
            ),
            now=clock,
        )

    return {
        "execution_status": "executed",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "reason": outcome,
        "command": copyable,
        "events": {
            "before": str(before_path) if before_path else None,
            "after": str(after_path) if after_path else None,
        },
    }
