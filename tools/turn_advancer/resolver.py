"""AIPOS-340 — Turn Advancer 核心解析器。

组装 state_reader + rules + command_builder，产生下一步完整命令。
"""
from pathlib import Path
from typing import Any

from .state_reader import read_task_state
from .rules import infer_next_action
from .command_builder import build_command


def resolve_next_command(
    task_id: str,
    workspace_root: Path,
    dispatch_mode: str = "manual",
    *,
    collaboration_profile_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析任务的下一步命令（auto 执行 / manual 打印）。
    
    参数:
        task_id: 任务 ID
        workspace_root: 工作区根路径
        dispatch_mode: "manual"（打印命令）或 "auto"（执行）
        collaboration_profile_override: 覆盖协作配置（测试用）
    
    返回:
    {
        "task_id": "AIPOS-340",
        "current_status": "claimed",
        "next_action": "return_work",
        "rule": "queue_status=claimed + RETURN.md 存在 → return_dry_run",
        "requires_human_judgment": False,
        "command_type": "mcp_verb" | "cli" | "wait_human" | "done",
        "command": {
            "verb": "lybra_queue_return_dry_run",
            "args": {...},
        },
        "dispatch_mode": "manual",
        "copyable_line": "python3 .../... --args '{...}'",
    }
    """
    # 1. 读取任务状态
    state = read_task_state(workspace_root, task_id)
    
    # 2. 推断下一步动作
    next_action_info = infer_next_action(state)
    
    # 3. 构建完整命令
    command_info = build_command(next_action_info["action"], state, workspace_root)
    
    # 4. 组装结果
    result = {
        "task_id": task_id,
        "current_status": state["queue_status"],
        "next_action": next_action_info["action"],
        "rule": next_action_info["rule"],
        "requires_human_judgment": next_action_info["requires_human_judgment"],
        "human_judgment_reason": next_action_info.get("human_judgment_reason"),
        "command_type": command_info["command_type"],
        "command": {
            "verb": command_info.get("verb"),
            "args": command_info.get("args", {}),
        } if command_info.get("verb") else command_info.get("copyable_line"),
        "dispatch_mode": dispatch_mode,
        "copyable_line": command_info["copyable_line"],
    }
    
    # 5. auto 模式：执行命令（简化版，当前只打印"would execute"）
    if dispatch_mode == "auto" and command_info["command_type"] == "mcp_verb":
        # TODO: 实际执行 MCP verb（调用 gate）
        result["execution_status"] = "would_execute_in_auto_mode"
        result["execution_note"] = "auto 模式执行未实现（需调用 gate/pump）"
    
    return result


def scan_all_tasks(workspace_root: Path, dispatch_mode: str = "manual") -> list[dict[str, Any]]:
    """扫描全队列，返回所有任务的下一步清单（Owner 一眼看清该贴什么）。
    
    S2: "一条产品命令可查'全队列下一步清单'"
    """
    results = []
    queue_root = workspace_root / "5_tasks" / "queue"
    
    for status_dir in ["pending", "claimed", "blocked"]:
        status_path = queue_root / status_dir
        if not status_path.is_dir():
            continue
        
        for task_file in status_path.glob("*.md"):
            task_id_lower = task_file.stem
            # 尝试从文件读取实际 task_id（frontmatter 中可能是大写）
            try:
                from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
                fm, _, _ = parse_markdown_frontmatter(task_file.read_text(encoding="utf-8"))
                task_id = fm.get("task_id") if isinstance(fm, dict) else task_id_lower.upper()
            except Exception:
                task_id = task_id_lower.upper()
            
            try:
                result = resolve_next_command(task_id, workspace_root, dispatch_mode)
                results.append(result)
            except Exception as e:
                results.append({
                    "task_id": task_id,
                    "error": str(e),
                    "current_status": status_dir,
                })
    
    return results
