"""AIPOS-340F1 S5 — fix↔复审轮次派生 (F1/F1R/F2/F2R...)。

实现 fix 轮命名与派生规则:
- verdict FAIL + 顾问 fix 卡已出 → 派生 fix 轮 (F1, F2, ...)
- fix 交回 → 派生复审轮 (F1R, F2R, ...)
- 循环有界,超限 → 等 Owner 裁定
"""
from pathlib import Path
from typing import Any
import re

from tools.schema_constants import Verdict


def parse_fix_round(task_id: str) -> dict[str, Any]:
    """解析任务 ID 中的 fix 轮次。
    
    Examples:
        AIPOS-327   -> base_id=AIPOS-327, round=0, is_review=False
        AIPOS-327F1 -> base_id=AIPOS-327, round=1, is_review=False
        AIPOS-327F1R -> base_id=AIPOS-327, round=1, is_review=True
        AIPOS-327F2 -> base_id=AIPOS-327, round=2, is_review=False
    
    Returns:
        {
            "base_id": str,  # 基础任务 ID
            "round": int,    # 轮次(0=初始,1=F1,2=F2,...)
            "is_review": bool,  # 是否复审轮(R)
            "full_suffix": str,  # 完整后缀(空/F1/F1R/F2/...)
        }
    """
    # 匹配模式: <BASE>F<N>R? 或 <BASE>
    match = re.match(r"^(.+?)(F(\d+)(R)?)?$", task_id)
    if not match:
        return {"base_id": task_id, "round": 0, "is_review": False, "full_suffix": ""}
    
    base_id = match.group(1)
    round_num = int(match.group(3)) if match.group(3) else 0
    is_review = bool(match.group(4))
    full_suffix = match.group(2) or ""
    
    return {
        "base_id": base_id,
        "round": round_num,
        "is_review": is_review,
        "full_suffix": full_suffix,
    }


def derive_next_round(
    current_task_id: str,
    verdict_result: str,
    fix_card_exists: bool = False,
) -> dict[str, Any]:
    """推导下一轮任务 ID。
    
    Args:
        current_task_id: 当前任务 ID
        verdict_result: 审计结果 (PASS/FAIL)
        fix_card_exists: fix 卡是否已出(顾问出)
    
    Returns:
        {
            "action": "derive_fix" | "derive_review" | "pass_complete" | "wait_fix_card",
            "next_task_id": str | None,
            "reason": str,
        }
    """
    parsed = parse_fix_round(current_task_id)
    base_id = parsed["base_id"]
    current_round = parsed["round"]
    is_review = parsed["is_review"]
    
    # 场景 1: 审计 PASS → 完成
    if verdict_result in [Verdict.PASS, Verdict.PASS_WITH_NOTES]:
        return {
            "action": "pass_complete",
            "next_task_id": None,
            "reason": f"{current_task_id} 审计 PASS,任务完成",
        }
    
    # 场景 2: 审计 FAIL
    if verdict_result == Verdict.FAIL:
        # 2a. 当前是复审轮(R) → 派生下一 fix 轮
        if is_review:
            next_round = current_round + 1
            next_task_id = f"{base_id}F{next_round}"
            if not fix_card_exists:
                return {
                    "action": "wait_fix_card",
                    "next_task_id": next_task_id,
                    "reason": f"{current_task_id} 复审 FAIL,等待顾问出 {next_task_id} fix 卡",
                }
            return {
                "action": "derive_fix",
                "next_task_id": next_task_id,
                "reason": f"{current_task_id} 复审 FAIL → 派生 {next_task_id}",
            }
        
        # 2b. 当前是初始轮(无后缀) → 派生 F1
        if current_round == 0:
            next_task_id = f"{base_id}F1"
            if not fix_card_exists:
                return {
                    "action": "wait_fix_card",
                    "next_task_id": next_task_id,
                    "reason": f"{current_task_id} 初审 FAIL,等待顾问出 {next_task_id} fix 卡",
                }
            return {
                "action": "derive_fix",
                "next_task_id": next_task_id,
                "reason": f"{current_task_id} 初审 FAIL → 派生 {next_task_id}",
            }
        
        # 2c. 当前是 fix 轮(非 R) → 这不应该发生(fix 卡应该有对应的 R 审计)
        # 但如果发生了,也派生下一 fix 轮
        next_round = current_round + 1
        next_task_id = f"{base_id}F{next_round}"
        if not fix_card_exists:
            return {
                "action": "wait_fix_card",
                "next_task_id": next_task_id,
                "reason": f"{current_task_id} 审计 FAIL,等待顾问出 {next_task_id} fix 卡",
            }
        return {
            "action": "derive_fix",
            "next_task_id": next_task_id,
            "reason": f"{current_task_id} 审计 FAIL → 派生 {next_task_id}",
        }
    
    # 其他情况
    return {
        "action": "unknown",
        "next_task_id": None,
        "reason": f"未知审计结果: {verdict_result}",
    }


def derive_review_round(fix_task_id: str) -> dict[str, Any]:
    """fix 卡交回后,派生复审轮。
    
    Args:
        fix_task_id: fix 任务 ID (如 AIPOS-327F1)
    
    Returns:
        {
            "action": "derive_review",
            "next_task_id": str,  # 如 AIPOS-327F1R
            "reason": str,
        }
    """
    parsed = parse_fix_round(fix_task_id)
    
    if parsed["is_review"]:
        return {
            "action": "error",
            "next_task_id": None,
            "reason": f"{fix_task_id} 已经是复审轮,不应该再派生 R",
        }
    
    if parsed["round"] == 0:
        return {
            "action": "error",
            "next_task_id": None,
            "reason": f"{fix_task_id} 不是 fix 轮,不应该派生 R",
        }
    
    review_task_id = f"{fix_task_id}R"
    return {
        "action": "derive_review",
        "next_task_id": review_task_id,
        "reason": f"{fix_task_id} 交回 → 派生复审轮 {review_task_id}",
    }


def check_fix_round_limit(task_id: str, max_fix_rounds: int = 3) -> dict[str, Any]:
    """检查 fix 轮次是否超限。
    
    Args:
        task_id: 任务 ID
        max_fix_rounds: fix 轮次上限(默认 3)
    
    Returns:
        {
            "within_limit": bool,
            "current_round": int,
            "reason": str,
        }
    """
    parsed = parse_fix_round(task_id)
    current_round = parsed["round"]
    
    if current_round > max_fix_rounds:
        return {
            "within_limit": False,
            "current_round": current_round,
            "reason": f"fix 轮次({current_round})超限({max_fix_rounds}),需 Owner 裁定",
        }
    
    return {
        "within_limit": True,
        "current_round": current_round,
        "reason": f"fix 轮次({current_round})在限内",
    }
