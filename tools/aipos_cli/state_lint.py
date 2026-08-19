"""AIPOS-C3B 大项C①③: state lint + state repair

state lint: 卡状态三方一致检查(队列目录 × frontmatter status × records)
state repair: 按 records 重建卡的一致状态(坏卡修复)

三方来源(transitions.schema.json#state_consistency):
  1. 队列目录 (5_tasks/queue/{pending|claimed|completed}/)
  2. frontmatter status 字段
  3. records 目录最新记录推导的状态
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter


QUEUE_DIRS = {
    "pending": "5_tasks/queue/pending",
    "claimed": "5_tasks/queue/claimed",
    "completed": "5_tasks/queue/completed",
    "blocked": "5_tasks/queue/blocked",
    "withdrawn": "5_tasks/queue/withdrawn",
}

RECORD_TYPE_TO_STATE = {
    "closure": "completed",
    "claim": "claimed",
    "return": "returned",
    "publish": "pending",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _derive_state_from_records(governance_root: Path, task_id: str) -> str | None:
    """从 records 推导任务的真实状态(以最新记录为准)。"""
    records_dir = governance_root / "5_tasks" / "records"
    
    # 按优先级检查各类记录(最新优先)
    # closure > return > claim > publish
    checks = [
        ("closures", "completed"),
        ("returns", "returned"),
        ("claims", "claimed"),
        ("publishes", "pending"),
    ]
    
    latest_state = None
    latest_ts = ""
    
    for record_type, state in checks:
        type_dir = records_dir / record_type / task_id
        if not type_dir.is_dir():
            continue
        for f in type_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                fm, _, _ = parse_markdown_frontmatter(text)
                # 取时间戳最大的记录
                ts = str(fm.get("closed_at") or fm.get("returned_at") or fm.get("claimed_at") or fm.get("published_at") or fm.get("timestamp") or "")
                if ts > latest_ts:
                    latest_ts = ts
                    latest_state = state
            except Exception:
                continue
    
    return latest_state


def _get_queue_state(governance_root: Path, task_id: str) -> tuple[str | None, Path | None]:
    """获取任务在队列目录中的状态。"""
    for state, rel_dir in QUEUE_DIRS.items():
        queue_dir = governance_root / rel_dir
        if not queue_dir.is_dir():
            continue
        card_path = queue_dir / f"{task_id.lower()}.md"
        if card_path.exists():
            return state, card_path
    return None, None


def _get_frontmatter_state(card_path: Path) -> str | None:
    """从卡 frontmatter 读取 status 字段。"""
    try:
        text = card_path.read_text(encoding="utf-8")
        fm, _, _ = parse_markdown_frontmatter(text)
        return str(fm.get("status") or "").strip() or None
    except Exception:
        return None


def _list_all_task_ids(governance_root: Path) -> set[str]:
    """列出所有在队列目录中的 task_id。"""
    task_ids: set[str] = set()
    for state, rel_dir in QUEUE_DIRS.items():
        queue_dir = governance_root / rel_dir
        if not queue_dir.is_dir():
            continue
        for f in queue_dir.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                fm, _, _ = parse_markdown_frontmatter(text)
                tid = str(fm.get("task_id") or "").strip()
                if tid:
                    task_ids.add(tid)
                else:
                    # fallback: 从文件名提取
                    task_ids.add(f.stem.upper())
            except Exception:
                task_ids.add(f.stem.upper())
    return task_ids


def run_state_lint(
    governance_root: Path,
    task_id_filter: str | None = None,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C①: 卡状态三方一致 lint。
    
    检查每个任务的三方一致性:
      1. 队列目录位置
      2. frontmatter status
      3. records 推导状态
    
    Returns:
        {
            "scanned": int,
            "issues": [{"task_id": str, "severity": str, "message": str, ...}],
        }
    """
    issues: list[dict[str, Any]] = []
    
    if task_id_filter:
        task_ids = {task_id_filter.upper()}
    else:
        task_ids = _list_all_task_ids(governance_root)
    
    for task_id in sorted(task_ids):
        queue_state, card_path = _get_queue_state(governance_root, task_id)
        fm_state = _get_frontmatter_state(card_path) if card_path else None
        record_state = _derive_state_from_records(governance_root, task_id)
        
        # 检查: completed 卡必须有 closure 记录
        if queue_state == "completed" and record_state != "completed":
            issues.append({
                "task_id": task_id,
                "severity": "ERROR",
                "message": f"卡在 completed/ 但无 closure 记录(断层)",
                "queue_state": queue_state,
                "fm_state": fm_state,
                "record_state": record_state,
            })
        
        # 检查: claimed 卡必须有 claim 记录
        if queue_state == "claimed" and record_state not in ("claimed", "returned"):
            issues.append({
                "task_id": task_id,
                "severity": "ERROR",
                "message": f"卡在 claimed/ 但无 claim 记录或记录状态不一致(断层)",
                "queue_state": queue_state,
                "fm_state": fm_state,
                "record_state": record_state,
            })
        
        # 检查: frontmatter status 与队列目录不一致
        if queue_state and fm_state and fm_state != queue_state:
            # 允许某些合法组合(如 returned 状态卡仍在 claimed/ 等审计)
            allowed_combos = {
                ("claimed", "returned"),  # 卡等审计
            }
            if (queue_state, fm_state) not in allowed_combos:
                issues.append({
                    "task_id": task_id,
                    "severity": "WARN",
                    "message": f"frontmatter status={fm_state} 与队列目录位置={queue_state} 不一致",
                    "queue_state": queue_state,
                    "fm_state": fm_state,
                    "record_state": record_state,
                })
        
        # 检查: 有 records 但卡不在对应队列
        if record_state == "completed" and queue_state != "completed":
            issues.append({
                "task_id": task_id,
                "severity": "WARN",
                "message": f"有 closure 记录但卡不在 completed/(在 {queue_state or 'unknown'})",
                "queue_state": queue_state,
                "fm_state": fm_state,
                "record_state": record_state,
            })
    
    return {
        "scanned": len(task_ids),
        "issues": issues,
    }


def repair_task_state(
    governance_root: Path,
    task_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """AIPOS-C3B 大项C③: 按 records 重建卡的一致状态。
    
    读 records 推导真实状态 → 修正 frontmatter + 移动队列目录。
    
    Returns:
        {
            "task_id": str,
            "repaired": bool,
            "dry_run": bool,
            "message": str,
            "actions": list[str],
        }
    """
    task_id = task_id.upper()
    record_state = _derive_state_from_records(governance_root, task_id)
    queue_state, card_path = _get_queue_state(governance_root, task_id)
    
    actions: list[str] = []
    
    if record_state is None:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": "无 records 可推导状态,无法修复",
            "actions": [],
        }
    
    if queue_state == record_state or (queue_state == "claimed" and record_state == "returned"):
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": f"卡已一致(队列={queue_state}, records→{record_state})",
            "actions": [],
        }
    
    if card_path is None:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": f"卡文件不存在于任何队列目录,无法修复(需人工检查)",
            "actions": [],
        }
    
    # 需要移动卡到正确的队列目录
    target_dir_name = QUEUE_DIRS.get(record_state)
    if target_dir_name is None:
        # returned 状态 → 卡应留在 claimed/ 等审计
        if record_state == "returned":
            target_dir_name = QUEUE_DIRS["claimed"]
        else:
            return {
                "task_id": task_id,
                "repaired": False,
                "dry_run": dry_run,
                "message": f"records 推导状态={record_state} 无对应队列目录",
                "actions": [],
            }
    
    target_dir = governance_root / target_dir_name
    target_path = target_dir / card_path.name
    
    actions.append(f"移动卡: {card_path} → {target_path}")
    
    # 修正 frontmatter status
    try:
        text = card_path.read_text(encoding="utf-8")
        fm, body, _ = parse_markdown_frontmatter(text)
        if fm.get("status") != record_state:
            fm["status"] = record_state
            actions.append(f"修正 frontmatter status: {fm.get('status')} → {record_state}")
    except Exception as e:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": dry_run,
            "message": f"读取卡文件失败: {e}",
            "actions": actions,
        }
    
    if dry_run:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": True,
            "message": f"会修复: 队列 {queue_state}→{record_state}",
            "actions": actions,
        }
    
    # 执行修复
    try:
        from tools.aipos_cli.queue_mutation import render_task_markdown
        target_dir.mkdir(parents=True, exist_ok=True)
        rendered = render_task_markdown(fm, body)
        target_path.write_text(rendered, encoding="utf-8")
        if card_path != target_path:
            card_path.unlink()
        return {
            "task_id": task_id,
            "repaired": True,
            "dry_run": False,
            "message": f"已修复: 队列 {queue_state}→{record_state}",
            "actions": actions,
        }
    except Exception as e:
        return {
            "task_id": task_id,
            "repaired": False,
            "dry_run": False,
            "message": f"修复失败: {e}",
            "actions": actions,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
