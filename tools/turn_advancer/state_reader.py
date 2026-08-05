"""AIPOS-340 — 任务状态读取器。

从 queue/、records/claims/、records/returns/、records/events/ 读取任务当前状态，
为 resolver 提供输入。
"""
from pathlib import Path
from typing import Any
import json
import re


_EVENT_KIND_FROM_FILENAME = re.compile(r"^(.+?)_\d{8}_\d{6}")


def _normalize_event_type(fm: dict[str, Any], filename: str) -> str | None:
    """统一三来源事件类型为单个 type 字段(rules.py 与 failure_kinds 消费用)。

    三来源真实落盘格式(AIPOS-340F4):
    - task_progress_event:frontmatter ``event_type``(started/progress/completed/blocked)
    - launch_check_event: frontmatter ``event_kind``(launch_failed/blocked),``source: launch_check``
    - audit_event(守护): frontmatter ``event_kind``(blocked/audit_incomplete)

    文件名统一为 ``<kind>_<YYYYMMDD>_<HHMMSS>.md``。优先取 frontmatter 字段;
    字段缺失时回退文件名前缀,保证个别文件 frontmatter 漂移也能读出(容错,不判活只读事实)。
    """
    kind = str(fm.get("event_type") or fm.get("event_kind") or "").strip()
    if kind:
        return kind
    match = _EVENT_KIND_FROM_FILENAME.match(filename)
    return match.group(1) if match else None


def read_task_state(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """读取任务完整状态（queue 位置、claims、returns、events、verdicts）。
    
    返回:
    {
        "task_id": "AIPOS-340",
        "queue_status": "claimed" | "pending" | "completed" | None,
        "task_path": Path | None,
        "task_frontmatter": {...},
        "latest_claim": {...} | None,
        "latest_return": {...} | None,
        "latest_verdict": {...} | None,
        "events": [{"type": "started", "timestamp": ...}, ...],
        "has_return_artifact": bool,  # RETURN.md 存在
        "has_audit_card": bool,  # AUDIT-*.md 存在
    }
    """
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    
    state = {
        "task_id": task_id,
        "queue_status": None,
        "task_path": None,
        "task_frontmatter": {},
        "latest_claim": None,
        "latest_return": None,
        "latest_verdict": None,
        "events": [],
        "has_return_artifact": False,
        "has_audit_card": False,
    }
    
    # 1. 找任务卡在哪个 queue 子目录
    queue_root = workspace_root / "5_tasks" / "queue"
    for status_dir in ["pending", "claimed", "completed", "blocked"]:
        task_file = queue_root / status_dir / f"{task_id.lower()}.md"
        if task_file.is_file():
            state["queue_status"] = status_dir
            state["task_path"] = task_file
            try:
                fm, _, _ = parse_markdown_frontmatter(task_file.read_text(encoding="utf-8"))
                state["task_frontmatter"] = fm if isinstance(fm, dict) else {}
            except Exception:
                pass
            break
    
    # 2. 读最新 claim record
    claims_dir = workspace_root / "5_tasks" / "records" / "claims" / task_id
    if claims_dir.is_dir():
        claim_files = sorted(claims_dir.glob("claim_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if claim_files:
            try:
                fm, _, _ = parse_markdown_frontmatter(claim_files[0].read_text(encoding="utf-8"))
                state["latest_claim"] = fm if isinstance(fm, dict) else {}
            except Exception:
                pass
    
    # 3. 读最新 return record
    returns_dir = workspace_root / "5_tasks" / "records" / "returns" / task_id
    if returns_dir.is_dir():
        return_files = sorted(returns_dir.glob("return_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if return_files:
            try:
                fm, _, _ = parse_markdown_frontmatter(return_files[0].read_text(encoding="utf-8"))
                state["latest_return"] = fm if isinstance(fm, dict) else {}
            except Exception:
                pass
    
    # 4. 读最新 verdict (审计结论)
    verdicts_dir = workspace_root / "5_tasks" / "records" / "verdicts" / task_id
    if verdicts_dir.is_dir():
        verdict_files = sorted(verdicts_dir.glob("verdict_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if verdict_files:
            try:
                fm, _, _ = parse_markdown_frontmatter(verdict_files[0].read_text(encoding="utf-8"))
                state["latest_verdict"] = fm if isinstance(fm, dict) else {}
            except Exception:
                pass
    
    # 5. 读 events —— 三来源真实落盘格式并容错(AIPOS-340F4):
    #    task_progress_event(event_type)/ launch_check_event(event_kind)/
    #    audit_event 守护(event_kind);文件名 <kind>_<ts>.md。type 归一见 _normalize_event_type。
    events_dir = workspace_root / "5_tasks" / "records" / "events" / task_id
    if events_dir.is_dir():
        event_files = sorted(events_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
        for ef in event_files:
            try:
                fm, _, _ = parse_markdown_frontmatter(ef.read_text(encoding="utf-8"))
                if not isinstance(fm, dict):
                    continue
                state["events"].append({
                    "type": _normalize_event_type(fm, ef.name),
                    "timestamp": fm.get("timestamp") or fm.get("reported_at"),
                    "actor": fm.get("actor"),
                    "reason": fm.get("reason"),
                    "source": fm.get("source") or fm.get("record_type"),
                })
            except Exception:
                pass
    
    # 6. 检查工作产物（task_cards/<ID>/RETURN.md, AUDIT-*.md）
    # 产品仓 task_cards 是 git 忽略区，executor 工作产物在这里
    product_repo = Path.home() / "projects" / "lybra"  # 硬编码产品仓位置（卡内默认）
    task_work_dir = product_repo / "task_cards" / task_id
    if task_work_dir.is_dir():
        state["has_return_artifact"] = (task_work_dir / "RETURN.md").is_file()
        state["has_audit_card"] = bool(list(task_work_dir.glob("AUDIT-*.md")))
    
    return state
