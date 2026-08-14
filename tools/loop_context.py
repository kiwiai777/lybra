"""AIPOS-R1: LoopContext — 解析一次贯穿动词的不可变上下文。

设计权威: LOOP-REDESIGN v2 §3

LoopContext 包含:
- project: 项目标识
- instance: agent 实例标识 (actor/role)
- workspace_root: 工作区根路径
- code_repo: 代码仓库路径
- connection: 连接信息 (gate_url, token)
- policy: 策略引用
- task_state: 任务状态
- worktree: worktree 路径

客户端只解析连接→token,其余字段由 gate 在 claim 时返回。
每个动词 verb(ctx, args) 只从 ctx 读,禁止自搓解析。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.schema_loader import get_config_default_gate_url  # AIPOS-R4B-1: gate URL single source


@dataclass(frozen=True)
class LoopContext:
    """Loop execution context — immutable per session.
    
    Parsed once at session start, passed to every verb.
    Client resolves connection → token; gate returns remaining fields on claim.
    """
    project: str
    instance: str  # agent_instance (e.g., "exec.lybra.kiwiai-dev")
    workspace_root: Path
    code_repo: Path | None
    gate_url: str
    token: str
    policy: str | None = None
    task_state: str | None = None
    worktree: Path | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "project": self.project,
            "instance": self.instance,
            "workspace_root": str(self.workspace_root),
            "code_repo": str(self.code_repo) if self.code_repo else None,
            "gate_url": self.gate_url,
            "policy": self.policy,
            "task_state": self.task_state,
            "worktree": str(self.worktree) if self.worktree else None,
        }


class ConnectionResolver:
    """连接→token 解析器 (唯一一份逻辑)。
    
    Precedence: 自发现 (.lybra/) → env 覆盖 → 显式参数
    
    消除"每次 source 环境脚本"的需求。
    """
    
    @staticmethod
    def discover_lybra_dir(workspace_root: Path) -> Path | None:
        """Auto-discover .lybra/ directory in workspace."""
        lybra_dir = workspace_root / ".lybra"
        if lybra_dir.is_dir():
            return lybra_dir
        return None
    
    @staticmethod
    def load_connection_config(lybra_dir: Path) -> dict[str, Any]:
        """Load connection.json from .lybra/ directory."""
        connection_file = lybra_dir / "connection.json"
        if not connection_file.is_file():
            raise FileNotFoundError(f"connection.json not found in {lybra_dir}")
        
        try:
            data = json.loads(connection_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {connection_file}") from exc
        
        if not isinstance(data, dict):
            raise ValueError(f"connection.json must be a JSON object: {connection_file}")
        
        return data
    
    @staticmethod
    def resolve_gate_url(
        *,
        workspace_root: Path | None = None,
        env: dict[str, str] | None = None,
        explicit_url: str | None = None,
    ) -> str:
        """Resolve gate URL with precedence: explicit → .lybra/ discovery → env override.
        
        AIPOS-R6H: env降为最低优先级,消除env注入病
        """
        source_env = env if env is not None else os.environ
        
        # Explicit parameter (highest priority)
        if explicit_url:
            return explicit_url
        
        # Auto-discovery from .lybra/ (优先级高于env)
        if workspace_root:
            lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
            if lybra_dir:
                try:
                    config = ConnectionResolver.load_connection_config(lybra_dir)
                    mcp_config = config.get("mcp", {})
                    if isinstance(mcp_config, dict):
                        rpc_url = mcp_config.get("rpc_url")
                        if rpc_url:
                            return str(rpc_url)
                except (FileNotFoundError, ValueError, KeyError):
                    pass
        
        # Environment override (最低优先级)
        env_url = source_env.get("LYBRA_GATE_URL", "").strip()
        if env_url:
            return env_url
        
        # Default fallback (AIPOS-R4B-1: from config.schema)
        return f"{get_config_default_gate_url()}/mcp"
    
    @staticmethod
    def resolve_token(
        *,
        workspace_root: Path | None = None,
        role: str | None = None,
        agent_instance: str | None = None,
        env: dict[str, str] | None = None,
        explicit_token: str | None = None,
    ) -> str:
        """Resolve role token with precedence: explicit → .lybra/ discovery → env override.
        
        AIPOS-R6H: env降为最低优先级
        
        Args:
            workspace_root: Workspace root for .lybra/ discovery
            role: Role name (e.g., "executor", "auditor")
            agent_instance: Agent instance ID (e.g., "exec.lybra.kiwiai-dev")
            env: Environment variables dict (defaults to os.environ)
            explicit_token: Explicitly provided token (highest priority)
        
        Returns:
            Resolved token string
        
        Raises:
            ValueError: If token cannot be resolved
        """
        source_env = env if env is not None else os.environ
        
        # Explicit parameter (highest priority)
        if explicit_token:
            return explicit_token
        
        # Auto-discovery from .lybra/connection.json (优先级高于env)
        if workspace_root:
            lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
            if lybra_dir:
                try:
                    config = ConnectionResolver.load_connection_config(lybra_dir)
                    tokens = config.get("tokens", [])
                    if not isinstance(tokens, list):
                        raise ValueError("tokens must be a list")
                    
                    # Match by agent_instance (most specific)
                    if agent_instance:
                        for token_entry in tokens:
                            if token_entry.get("agent_instance") == agent_instance:
                                return str(token_entry["token"])
                    
                    # Match by role
                    if role:
                        for token_entry in tokens:
                            if token_entry.get("role") == role:
                                return str(token_entry["token"])
                    
                except (FileNotFoundError, ValueError, KeyError) as exc:
                    # Discovery failed, fall through to error
                    pass
        
        # Environment override (最低优先级)
        env_token = source_env.get("LYBRA_TOKEN", "").strip()
        if env_token:
            return env_token
        
        raise ValueError(
            f"Cannot resolve token for role={role}, agent_instance={agent_instance}. "
            "Provide explicit token, set LYBRA_TOKEN env, or ensure .lybra/connection.json exists."
        )
    
    @staticmethod
    def resolve_project_from_token(token_data: dict[str, Any]) -> str | None:
        """Extract project from token data (for multi-project tokens)."""
        projects = token_data.get("projects")
        if not projects:
            return None
        
        if isinstance(projects, list):
            # Single-project token
            if len(projects) == 1:
                return str(projects[0])
            
            # Multi-project token: check default_project
            default_project = token_data.get("default_project")
            if default_project:
                return str(default_project)
        
        return None
