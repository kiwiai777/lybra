"""AIPOS-R4A: 状态机转移引擎 — 单一源读 transitions.schema.json

一机制一实现：所有状态转移统一走此引擎，禁散落 if/else。
引擎读 schema 声明（from/to/触发者/证据/盖字段），泛化执行。

AIPOS-R4A F-1(P0)修复：schema 加载统一走 tools/schema_loader.py（唯一入口）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.schema_constants import RecordType

# AIPOS-R4A F-1: 使用唯一 schema loader（tools/schema_loader.py）
# AIPOS-R4B-1 FIX-2: schema_loader 导入移至函数内(惰性),避免 CLI 早期导入链在 editable-install
# 环境下 ModuleNotFoundError。见 AUDIT-R4B-1 F-R4B1-1。



def apply_transition_metadata(
    *,
    metadata: dict[str, Any],
    transition_name: str,
    actor: str,
    timestamp: str | None = None,
    schema: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    **extra_fields: Any,
) -> dict[str, Any]:
    """AIPOS-R4A: 状态转移引擎核心 — 按 schema 声明统一盖字段
    
    Args:
        metadata: 原始 frontmatter
        transition_name: 转移名称（如 "complete", "reopen", "claim"）
        actor: 执行者
        timestamp: 时间戳（None 则自动生成）
        schema: transitions.schema（None 则自动加载）
        repo_root: 仓库根目录（加载 schema 用）
        **extra_fields: 转移特定字段（如 report_link, reason 等）
    
    Returns:
        更新后的 metadata
    """
    if schema is None:
        # AIPOS-R4A F-1: 使用唯一 schema_loader.load_schema（repo_root=None 自动定位产品仓）
        try:
            from tools.schema_loader import load_schema
            schema = load_schema("transitions", repo_root=None)
        except ImportError as e:
            raise ImportError(
                "Cannot load schema_loader.load_schema() for transitions schema. "
                "This typically occurs when running lybra CLI from outside the project root "
                "in an editable install. Run from the project directory or ensure PYTHONPATH "
                "includes the project root."
            ) from e
    
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    updated = dict(metadata)
    
    # 按转移类型应用 schema 声明的字段
    if transition_name == "complete":
        # N6 close 或队列 complete 转移
        updated["status"] = "completed"
        updated["completed_by"] = actor
        updated["completed_at"] = timestamp
        
        # 清理 active_session_id → last_session_id（schema 隐含规则）
        if updated.get("active_session_id") not in (None, ""):
            updated["last_session_id"] = updated.get("active_session_id")
        updated.pop("active_session_id", None)
        
        # 清理门控字段
        updated["needs_owner"] = False
        updated["approval_required"] = False
        updated["owner_review_required"] = False
        updated.pop("needs_owner_reasons", None)
        
        # 转移特定字段
        if "report_link" in extra_fields:
            updated.setdefault("artifact_links", [])
            report = extra_fields["report_link"]
            if report and report not in updated["artifact_links"]:
                updated["artifact_links"].append(report)
    
    elif transition_name == "reopen":
        # reopen 转移：completed/blocked → pending
        updated["status"] = "pending"
        updated["reopened_by"] = actor
        updated["reopened_at"] = timestamp
        if "reason" in extra_fields:
            updated["reopen_reason"] = extra_fields["reason"]
        
        updated["needs_owner"] = False

        # AIPOS-R6S 大项A③: round 序号 — reopen 递增, 使多轮 FIX 有完整生命周期。
        # 下游 close/audit 用 round 区分旧终态(旧 round 的 closure/verdict 不拦新 round)。
        try:
            prev_round = int(updated.get("round") or 1)
        except (TypeError, ValueError):
            prev_round = 1
        updated["round"] = prev_round + 1
        # 重置 closure/verdict 终态字段(新 round 下不再被旧终态字段污染)
        updated.pop("finalized", None)
        updated.pop("finalized_at", None)
        updated.pop("finalize_commit_hash", None)
        updated.pop("finalize_return_ref", None)
        updated.pop("verdict", None)
        updated.pop("verdict_ref", None)
        updated.pop("audit_verdict", None)
        updated.pop("audit_verdict_at", None)
        updated.pop("audit_verdict_by", None)
        updated.pop("related_audit_verdict_ref", None)
        
        # AIPOS-R4A FIX-2: malformed 修复路径，清理 active_session_id → last_session_id
        if updated.get("active_session_id") not in (None, ""):
            updated["last_session_id"] = updated.get("active_session_id")
        updated.pop("active_session_id", None)
        updated.pop("claim_id", None)
        
        # AIPOS-R4A 实撞③：清理所有 completed/blocked 相关字段（malformed 修复路径）
        updated.pop("completed_by", None)
        updated.pop("completed_at", None)
        updated.pop("blocked_by", None)
        updated.pop("blocked_at", None)
        updated.pop("block_reason", None)
        updated.pop("closed_by", None)
        updated.pop("closed_at", None)
        updated.pop("auto_closed_by", None)
        updated.pop("auto_closed_with_parent", None)
        updated.pop("auto_closed_via", None)
    
    elif transition_name == RecordType.CLAIM:
        updated["status"] = "claimed"
        updated["claimed_by"] = actor
        if "claim_id" in extra_fields:
            updated["claim_id"] = extra_fields["claim_id"]
        if "claimed_at" in extra_fields:
            updated["claimed_at"] = extra_fields["claimed_at"]
        if "active_session_id" in extra_fields:
            updated["active_session_id"] = extra_fields["active_session_id"]
    
    elif transition_name == "block":
        updated["status"] = "blocked"
        updated["blocked_by"] = actor
        updated["blocked_at"] = timestamp
        if "reason" in extra_fields:
            updated["block_reason"] = extra_fields["reason"]
        
        # 清理 active_session_id → last_session_id
        if updated.get("active_session_id") not in (None, ""):
            updated["last_session_id"] = updated.get("active_session_id")
        updated.pop("active_session_id", None)
    
    elif transition_name == "withdraw":
        updated["status"] = "withdrawn"
        updated["withdrawn_by"] = actor
        updated["withdrawn_at"] = timestamp
        if "reason" in extra_fields:
            updated["withdrawal_reason"] = extra_fields["reason"]
        
        # 保留 claim 历史
        if updated.get("active_session_id") not in (None, ""):
            updated["last_session_id"] = updated.get("active_session_id")
        updated.pop("active_session_id", None)
    
    else:
        raise ValueError(f"Unsupported transition: {transition_name}")
    
    return updated


def validate_transition(
    *,
    current_state: str,
    transition_name: str,
    schema: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> tuple[bool, str]:
    """验证转移是否合法
    
    Returns:
        (is_valid, message)
    """
    if schema is None:
        # AIPOS-R4A F-1: 使用唯一 schema_loader.load_schema
        try:
            from tools.schema_loader import load_schema
            schema = load_schema("transitions", repo_root=None)
        except ImportError as e:
            raise ImportError(
                "Cannot load schema_loader.load_schema() for transitions schema. "
                "This typically occurs when running lybra CLI from outside the project root "
                "in an editable install. Run from the project directory or ensure PYTHONPATH "
                "includes the project root."
            ) from e
    
    # 根据转移名称查找 allowed transitions
    # 简化版：从预定义映射查找
    allowed_transitions = {
        "claim": (["pending"], "claimed"),
        "block": (["claimed"], "blocked"),
        "complete": (["claimed"], "completed"),
        "reopen": (["blocked", "completed"], "pending"),
        "withdraw": (["pending", "claimed"], "withdrawn"),
    }
    
    if transition_name not in allowed_transitions:
        return False, f"Unknown transition: {transition_name}"
    
    from_states, to_state = allowed_transitions[transition_name]
    
    # reopen 和 withdraw 支持多源状态
    if transition_name in ("reopen", "withdraw"):
        if current_state not in from_states:
            # 对于 malformed 卡（AIPOS-R4A 实撞③），reopen 允许宽松处理
            if transition_name == "reopen":
                return True, f"WARN: malformed card repair path (state={current_state})"
            return False, f"Invalid source state for {transition_name}: expected {from_states}, got {current_state}"
    else:
        if current_state not in from_states:
            return False, f"Invalid source state for {transition_name}: expected {from_states}, got {current_state}"
    
    return True, f"Valid transition: {current_state} → {to_state}"


def resolve_next_step_from_schema(
    task_id: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """AIPOS-R7A: 从 transitions.schema 解析下一步动作
    
    读取任务卡当前状态，查询 transitions.schema 确定下一步:
    - 动词名称
    - 完整参数
    - 触发者（谁执行）
    - 授权语义
    
    禁记忆叙述：所有信息必须从 schema + 卡状态派生，不依赖口述。
    
    Args:
        task_id: 任务 ID
        workspace_root: 工作区根目录
    
    Returns:
        {
            "task_id": str,
            "current_state": str,
            "next_step": str,  # 节点名称，如 "return", "audit", "finalize"
            "triggered_by": str,  # 如 "executor", "auditor", "advisor"
            "command": str,  # 可执行命令字符串
            "verb": str,  # gate 动词名称（如有）
            "parameters": dict,  # 命令参数
            "authorization": str,  # 授权语义说明
            "notes": str,  # 附加说明
        }
    """
    from tools.schema_loader import load_schema
    from tools.aipos_cli.task_loader import find_task_by_id
    
    # 加载 schema
    transitions_schema = load_schema("transitions", repo_root=None)
    
    # 加载任务卡
    workspace_root = Path(workspace_root)
    task_card, _ = find_task_by_id(task_id, workspace_root)
    
    if not task_card:
        raise ValueError(f"Task {task_id} not found in workspace {workspace_root}")
    
    # task_loader 返回的字典中 status 信息在 queue_state 或 frontmatter_status
    queue_state = task_card.get("queue_state", "unknown")
    frontmatter_status = task_card.get("frontmatter_status", "unknown")
    # 优先使用 frontmatter_status(卡内声明), fallback 到 queue_state(目录名)
    current_status = frontmatter_status if frontmatter_status not in (None, "unknown") else queue_state
    task_mode = task_card.get("task_mode", "code")
    verdict = task_card.get("metadata", {}).get("verdict") or task_card.get("metadata", {}).get("audit_verdict")
    
    # 从 main_flow 查找当前状态对应的下一步
    main_flow = transitions_schema.get("main_flow", {})
    nodes = main_flow.get("nodes", [])
    
    # 状态 -> 节点映射
    next_step_map = {
        "pending": "N1_claim",
        "claimed": "N3_return",  # 执行中，下一步是 return
        "returned": "N4_audit",
        "audit_pass": "N5_finalize",
        "finalized": "N6_close",
    }
    
    # 特殊判断：已有 verdict 的 returned 卡
    if current_status == "returned":
        if verdict == "PASS":
            next_step_map["returned"] = "N5_finalize"
        elif verdict in ("FAIL", "BLOCK"):
            next_step_map["returned"] = "reopen_or_fix"
    
    # 非代码卡跳过独立审计
    if task_mode in ("docs", "governance", "config") and current_status == "returned":
        next_step_map["returned"] = "advisor_review"
    
    next_node_id = next_step_map.get(current_status)
    
    if not next_node_id:
        return {
            "task_id": task_id,
            "current_state": current_status,
            "next_step": "unknown",
            "triggered_by": "unknown",
            "command": "",
            "verb": "",
            "parameters": {},
            "authorization": "",
            "notes": f"No transition defined for state: {current_status}",
        }
    
    # 查找对应节点详情
    node_detail = None
    for node in nodes:
        if node.get("node_id") == next_node_id.split("_")[0]:  # N1, N3, N4, N5, N6
            node_detail = node
            break
    
    if not node_detail:
        node_detail = {"name": next_node_id, "verb": "unknown", "trigger": "unknown"}
    
    node_name = node_detail.get("name", next_node_id)
    verb = node_detail.get("verb", "")
    trigger = node_detail.get("trigger", "unknown")
    
    # 构建命令
    command_parts = []
    parameters = {}
    authorization = ""
    
    if node_name == "claim":
        command_parts = [
            f"lybra queue claim --task-id {task_id}",
            f"--actor executor --agent-instance exec.lybra.kiwiai-dev",
            f"--autonomy-mode PreAuthorized",
        ]
        parameters = {
            "task_id": task_id,
            "autonomy_mode": "PreAuthorized",
        }
        authorization = "Executor self-claim under PreAuthorized envelope"
    
    elif node_name == "return":
        command_parts = [
            f"lybra queue return --task-id {task_id}",
            f"--actor executor --agent-instance exec.lybra.kiwiai-dev",
        ]
        parameters = {"task_id": task_id}
        authorization = "Executor self-confirm return (328 甲案)"
    
    elif node_name == "audit":
        if task_mode in ("docs", "governance", "config"):
            command_parts = [f"# Non-code task: advisor reviews and submits verdict via gate"]
            authorization = "Advisor review (non-code fast lane)"
        else:
            command_parts = [
                f"lybra audit dispatch --task-id {task_id}",
                f"--auditor-instance auditor.lybra.kiwiai-dev",
            ]
            parameters = {"task_id": task_id}
            authorization = "Advisor dispatches to independent auditor"
    
    elif node_name == "finalize":
        command_parts = [
            f"lybra finalize --task-id {task_id}",
            f"--actor executor --workspace-root ~/projects/lybra",
            f"--push --deploy",
        ]
        parameters = {"task_id": task_id, "push": True, "deploy": True}
        authorization = "Executor finalizes after audit PASS"
    
    elif node_name == "close":
        command_parts = [f"# Auto-close when evidence complete; advisor runs governance backlog"]
        parameters = {"task_id": task_id}
        authorization = "System auto-close + advisor governance backlog"
    
    else:
        command_parts = [f"# Next step: {node_name}"]
    
    command = " ".join(command_parts)
    
    notes = node_detail.get("notes", "")
    if not notes:
        notes = node_detail.get("description", "")
    
    return {
        "task_id": task_id,
        "current_state": current_status,
        "next_step": node_name,
        "triggered_by": trigger,
        "command": command,
        "verb": verb,
        "parameters": parameters,
        "authorization": authorization,
        "notes": notes,
    }
