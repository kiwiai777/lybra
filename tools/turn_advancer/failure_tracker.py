"""AIPOS-340F1 S4 — 连败计数器与有界重试。

从 claims/returns/events 记录统计同卡连败次数,达阈值触发顶替或 blocked。
"""
from pathlib import Path
from typing import Any
import yaml


def count_consecutive_failures(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """统计任务连败次数(纯读记录,不造状态)。
    
    Args:
        workspace_root: 治理仓根目录
        task_id: 任务 ID
    
    Returns:
        {
            "consecutive_failures": int,  # 连败次数
            "last_failure_claim_id": str | None,
            "total_attempts": int,  # 总尝试次数
            "failure_history": [{"claim_id": str, "verdict": str, "timestamp": str}],
        }
    """
    records_root = workspace_root / "5_tasks" / "records"
    verdicts_dir = records_root / "audit_verdicts" / task_id
    claims_dir = records_root / "claims" / task_id
    
    if not verdicts_dir.exists() or not claims_dir.exists():
        return {
            "consecutive_failures": 0,
            "last_failure_claim_id": None,
            "total_attempts": 0,
            "failure_history": [],
        }
    
    # 读取所有审计裁决,按时间排序
    verdicts = []
    for verdict_file in sorted(verdicts_dir.glob("verdict_*.md")):
        try:
            content = verdict_file.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            parts = content.split("---", 2)
            if len(parts) < 3:
                continue
            meta = yaml.safe_load(parts[1])
            verdicts.append({
                "verdict": meta.get("verdict_result") or meta.get("result"),
                "timestamp": meta.get("verdict_at") or meta.get("created_at"),
                "claim_id": meta.get("claim_id"),
            })
        except Exception:
            continue
    
    if not verdicts:
        return {
            "consecutive_failures": 0,
            "last_failure_claim_id": None,
            "total_attempts": len(list(claims_dir.glob("claim_*.md"))),
            "failure_history": [],
        }
    
    # 从最新往前统计连败
    verdicts_sorted = sorted(verdicts, key=lambda v: v["timestamp"], reverse=True)
    consecutive_failures = 0
    failure_history = []
    
    for v in verdicts_sorted:
        if v["verdict"] == "FAIL":
            consecutive_failures += 1
            failure_history.append(v)
        else:
            break  # 遇到非 FAIL 停止
    
    return {
        "consecutive_failures": consecutive_failures,
        "last_failure_claim_id": failure_history[0]["claim_id"] if failure_history else None,
        "total_attempts": len(list(claims_dir.glob("claim_*.md"))),
        "failure_history": failure_history,
    }


def check_retry_limit(
    workspace_root: Path,
    task_id: str,
    max_consecutive_failures: int = 3,
    max_total_attempts: int = 10,
) -> dict[str, Any]:
    """检查是否超限,返回动作建议。
    
    Args:
        workspace_root: 治理仓根目录
        task_id: 任务 ID
        max_consecutive_failures: 连败阈值(默认 3)
        max_total_attempts: 总尝试上限(默认 10)
    
    Returns:
        {
            "action": "allow_retry" | "trigger_substitution" | "block_escalate",
            "reason": str,
            "stats": {...},  # count_consecutive_failures 返回值
        }
    """
    stats = count_consecutive_failures(workspace_root, task_id)
    
    # 超过总尝试上限 → blocked + 等 Owner
    if stats["total_attempts"] >= max_total_attempts:
        return {
            "action": "block_escalate",
            "reason": f"总尝试次数({stats['total_attempts']})达上限({max_total_attempts}),需 Owner 裁定",
            "stats": stats,
        }
    
    # 连败达阈值 → 触发顶替表(或等人工)
    if stats["consecutive_failures"] >= max_consecutive_failures:
        return {
            "action": "trigger_substitution",
            "reason": f"连败{stats['consecutive_failures']}次达阈值({max_consecutive_failures}),触发模型顶替",
            "stats": stats,
        }
    
    # 允许重试
    return {
        "action": "allow_retry",
        "reason": f"连败{stats['consecutive_failures']}次,允许继续重试",
        "stats": stats,
    }
