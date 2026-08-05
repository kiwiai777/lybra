"""AIPOS-340 — 状态机转换规则（S5 回合链的 8 环）。

给定任务状态，返回下一步动作类别 + 规则依据。
判断留人（S3）：卡内容/审计结论/owner_verify 不代产。
"""
from typing import Any


def infer_next_action(state: dict[str, Any]) -> dict[str, Any]:
    """根据任务状态推断下一步动作。
    
    返回:
    {
        "action": "claim_task" | "return_work" | "dispatch_audit" | "fix_dispatch" 
                  | "re_audit" | "finalize" | "wait_human" | "done",
        "rule": "规则依据（哪条状态机转换）",
        "requires_human_judgment": bool,  # S3 边界
        "human_judgment_reason": str | None,
    }
    """
    task_id = state["task_id"]
    queue_status = state["queue_status"]
    latest_claim = state.get("latest_claim") or {}
    latest_return = state.get("latest_return") or {}
    latest_verdict = state.get("latest_verdict") or {}
    has_return = state.get("has_return_artifact", False)
    has_audit_card = state.get("has_audit_card", False)
    events = state.get("events", [])
    
    # 1. pending → 执行派工（claim）
    if queue_status == "pending":
        return {
            "action": "claim_task",
            "rule": "queue_status=pending → 执行轮派工（claim）",
            "requires_human_judgment": False,
            "human_judgment_reason": None,
        }
    
    # 2. claimed + 有 RETURN.md → 交回工作（return_dry_run）
    if queue_status == "claimed" and has_return and not latest_return:
        return {
            "action": "return_work",
            "rule": "queue_status=claimed + RETURN.md 存在 + 未 return → return_dry_run",
            "requires_human_judgment": False,
            "human_judgment_reason": None,
        }
    
    # 3. claimed + 已 return + 未审计 → 审计派工
    # （简化：假设 return 后自动生成审计卡，这里检查是否已派审）
    if queue_status == "claimed" and latest_return and not latest_verdict:
        # 检查是否需要审计（task_mode=code 通常需要）
        task_mode = state["task_frontmatter"].get("task_mode")
        audit_required = state["task_frontmatter"].get("audit") == "required"
        if task_mode == "code" or audit_required:
            # 检查审计卡是否已生成（executor 自产审计卡）
            if not has_audit_card:
                return {
                    "action": "wait_human",
                    "rule": "已 return，审计卡未生成 → 等待 executor 自产审计卡（task-closure-loop）",
                    "requires_human_judgment": True,
                    "human_judgment_reason": "审计卡内容由 executor 自产（按 audit-card-template 填变量），不代写",
                }
            # 审计卡已生成，派审
            return {
                "action": "dispatch_audit",
                "rule": "已 return + 审计卡存在 + 未审计 → audit_dispatch",
                "requires_human_judgment": False,
                "human_judgment_reason": None,
            }
    
    # 4. 审计 FAIL → fix 派工（等顾问出 fix 卡）
    if latest_verdict:
        verdict_result = latest_verdict.get("verdict_result") or latest_verdict.get("result")
        if verdict_result == "FAIL":
            # fix 卡由顾问出（S3：卡内容判断留人）
            # 检查是否已有 fix 卡（queue/pending/ 下的 <ID>F* 卡）
            # 简化：这里只输出"等待顾问出 fix 卡"
            return {
                "action": "wait_human",
                "rule": "审计 FAIL → fix 轮派工（fix 卡内容由顾问出，本解析器不代写）",
                "requires_human_judgment": True,
                "human_judgment_reason": "fix 卡正文内容 = 卡内容判断（S3 边界），顾问出",
            }
        elif verdict_result in ["PASS", "PASS_WITH_NOTES"]:
            # 审计 PASS → finalize（如果授权）
            needs_owner_verify = state["task_frontmatter"].get("owner_verify") == "required"
            if needs_owner_verify:
                return {
                    "action": "wait_human",
                    "rule": "审计 PASS + owner_verify=required → 等待 Owner 真人核验",
                    "requires_human_judgment": True,
                    "human_judgment_reason": "owner_verify 核验（S3 边界），Owner 判断",
                }
            # 可 finalize（生成 finalize 卡 + 派工）
            return {
                "action": "finalize",
                "rule": "审计 PASS + 无 owner_verify → finalize 卡生成与派工",
                "requires_human_judgment": False,
                "human_judgment_reason": None,
            }
    
    # 5. completed → done
    if queue_status == "completed":
        return {
            "action": "done",
            "rule": "queue_status=completed → 任务已完成",
            "requires_human_judgment": False,
            "human_judgment_reason": None,
        }
    
    # 6. blocked → 检查是否死 claim（需释放）
    if queue_status == "blocked":
        # 简化：blocked 的处理（block+reopen）由人工裁定
        return {
            "action": "wait_human",
            "rule": "queue_status=blocked → 等待人工裁定（reopen / 顶替 / 死 claim 释放）",
            "requires_human_judgment": True,
            "human_judgment_reason": "blocked 恢复策略 = 方向裁定（S3 边界）",
        }
    
    # 默认：无法推断下一步（可能状态不完整或未覆盖的边界情况）
    return {
        "action": "unknown",
        "rule": f"未覆盖的状态组合: queue_status={queue_status}, has_return={has_return}, latest_return={bool(latest_return)}, latest_verdict={bool(latest_verdict)}",
        "requires_human_judgment": True,
        "human_judgment_reason": "状态机未覆盖此组合，需人工分析",
    }
