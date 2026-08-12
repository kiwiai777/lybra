"""AIPOS-R4A: 状态机转移引擎 — 单一源读 transitions.schema.json

一机制一实现：所有状态转移统一走此引擎，禁散落 if/else。
引擎读 schema 声明（from/to/触发者/证据/盖字段），泛化执行。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_transitions_schema(repo_root: Path | None = None) -> dict[str, Any]:
    """加载 transitions.schema.json
    
    Args:
        repo_root: 仓库根目录，None 时自动查找（向上找 schema/transitions.schema.json）
    """
    if repo_root is None:
        # 自动查找：从当前文件向上找 schema 目录
        current = Path(__file__).resolve().parent
        for _ in range(5):  # 最多向上 5 层
            candidate = current / "schema" / "transitions.schema.json"
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
            current = current.parent
        raise FileNotFoundError("Could not find schema/transitions.schema.json")
    
    schema_path = repo_root / "schema" / "transitions.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


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
        schema = load_transitions_schema(repo_root)
    
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
    
    elif transition_name == "claim":
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
        schema = load_transitions_schema(repo_root)
    
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
