"""AIPOS-340F1 S6 — 从工作区读取活跃策略信封。

扫描治理仓 5_tasks/policies/ 下未过期未耗尽的策略,返回最新活跃信封。
"""
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import yaml


def find_active_policy(
    workspace_root: Path,
    role: str,
    policy_type: str = "dev",
) -> str | None:
    """从治理仓读取活跃策略信封。
    
    Args:
        workspace_root: 治理仓根目录(~/ai-project-os/2_projects/lybra)
        role: "exec" | "audit"
        policy_type: "dev" | "audit"
    
    Returns:
        策略 ID(如 pol_lybra_dev_7),或 None(无活跃策略)
    """
    policies_dir = workspace_root / "5_tasks" / "policies"
    if not policies_dir.exists():
        return None
    
    pattern = f"pol_lybra_{policy_type}_*.md"
    policy_files = sorted(policies_dir.glob(pattern), reverse=True)  # 最新编号优先
    
    now = datetime.now(timezone.utc)
    
    for policy_file in policy_files:
        try:
            content = policy_file.read_text(encoding="utf-8")
            # 简单解析 frontmatter (--- ... ---)
            if not content.startswith("---"):
                continue
            parts = content.split("---", 2)
            if len(parts) < 3:
                continue
            meta = yaml.safe_load(parts[1])
            
            # 检查状态
            if meta.get("status") != "active":
                continue
            
            # 检查过期时间
            expires_at = meta.get("expires_at")
            if expires_at:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if now > expires_dt:
                    continue
            
            # 检查是否耗尽(简化:暂不统计已用次数,只看 max_tasks)
            # 实际应扫描 claims 记录统计该信封已用次数
            # 这里假设最新编号 = 未耗尽
            
            return meta.get("policy_id")
        except Exception:
            continue
    
    return None
