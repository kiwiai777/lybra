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
        """Extract project from token data (AIPOS-F66: 调用统一解析器)."""
        from tools.project_resolution import ProjectResolver
        return ProjectResolver._extract_project_from_token(token_data)
    
    @staticmethod
    def resolve_role(
        *,
        workspace_root: Path | None = None,
        env: dict[str, str] | None = None,
        explicit_role: str | None = None,
    ) -> str | None:
        """AIPOS-C2 大项A: 解析 role (与 actor 同源)。无静默缺省 —— 解析不到返回 None, 由调用方出声并停。
        
        Precedence: 显式 → .lybra/role.role → env:LYBRA_ROLE (仅兜底)。
        """
        result = ConnectionResolver.resolve_identity(
            workspace_root=workspace_root,
            env=env,
            explicit={"role": explicit_role} if explicit_role else None,
        )
        return result["role"]["value"]
    
    @staticmethod
    def resolve_identity(
        *,
        workspace_root: Path | None = None,
        env: dict[str, str] | None = None,
        explicit: dict[str, str] | None = None,
        schema_gate_url: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """AIPOS-C2 大项A/C: 一次性解析全部身份/连接键, 带来源自曝 (provenance)。
        
        声明权威: schema/config.schema.json#identity_resolution (config.schema 是身份配置域唯一真相)。
        总序: 显式参数 → 工位 .lybra (role 文件 + connection.json) → env (仅兜底)。
        铁律: role/actor/agent_instance/owner_policy_ref 从同一次 .lybra/role 加载 (同源);
        无静默缺省 —— 解析不到 value=None, 由调用方出声并停。
        
        返回每个键: {"key", "value", "source", "via_env", "env_downgraded"}。
        """
        source_env = env if env is not None else os.environ
        ex = explicit or {}
        if schema_gate_url is None:
            # AIPOS-R4B-1: gate URL 单源在 config.schema (urls.gate_local)
            schema_gate_url = get_config_default_gate_url()
        schema_gate_url = schema_gate_url.rstrip("/mcp").rstrip("/")
        
        def mk(key: str) -> dict[str, Any]:
            return {"key": key, "value": None, "source": "unresolved", "via_env": False, "env_downgraded": False}
        
        role = mk("role")
        actor = mk("actor")
        agent_instance = mk("agent_instance")
        owner_policy_ref = mk("owner_policy_ref")
        token = mk("token")
        workspace = mk("workspace_root")
        gate_url = mk("gate_url")
        
        # 一次自发现 + 一次加载: role/actor/instance/policy 同源于此
        lybra_dir = None
        if workspace_root:
            lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
        role_data: dict[str, Any] = {}
        conn: dict[str, Any] | None = None
        actor_text: str | None = None
        policy_text: str | None = None
        if lybra_dir:
            role_file = lybra_dir / "role"
            if role_file.is_file():
                try:
                    content = role_file.read_text(encoding="utf-8").strip()
                    if content.startswith("{"):
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            role_data = parsed
                    else:
                        role_data = {"role": content}
                except Exception:
                    role_data = {}
            try:
                conn = ConnectionResolver.load_connection_config(lybra_dir)
            except Exception:
                conn = None
            actor_file = lybra_dir / "actor"
            if actor_file.is_file():
                try:
                    actor_text = actor_file.read_text(encoding="utf-8").strip() or None
                except Exception:
                    actor_text = None
            policy_file = lybra_dir / "policy"
            if policy_file.is_file():
                try:
                    policy_text = policy_file.read_text(encoding="utf-8").strip() or None
                except Exception:
                    policy_text = None
        
        env_role = (source_env.get("LYBRA_ROLE") or "").strip()
        env_actor = (source_env.get("LYBRA_ACTOR") or "").strip()
        env_instance = (source_env.get("LYBRA_AGENT_INSTANCE") or "").strip()
        env_policy = (source_env.get("LYBRA_OWNER_POLICY_REF") or "").strip()
        env_token = (source_env.get("LYBRA_TOKEN") or "").strip()
        env_root = (source_env.get("LYBRA_WORKSPACE_ROOT") or "").strip()
        env_gate_url = (source_env.get("LYBRA_GATE_URL") or "").strip()
        
        # --- role: 显式 → .lybra/role.role → env (无缺省) ---
        if ex.get("role"):
            role.update(value=ex["role"], source="explicit", env_downgraded=bool(env_role))
        elif role_data.get("role"):
            role.update(value=str(role_data["role"]), source=".lybra/role", env_downgraded=bool(env_role))
        elif env_role:
            role.update(value=env_role, source="env:LYBRA_ROLE", via_env=True)
        
        # --- actor: 显式 → .lybra/role.instance → .lybra/actor → env ---
        if ex.get("actor"):
            actor.update(value=ex["actor"], source="explicit", env_downgraded=bool(env_actor))
        elif role_data.get("instance"):
            actor.update(value=str(role_data["instance"]), source=".lybra/role", env_downgraded=bool(env_actor))
        elif actor_text:
            actor.update(value=actor_text, source=".lybra/actor", env_downgraded=bool(env_actor))
        elif env_actor:
            actor.update(value=env_actor, source="env:LYBRA_ACTOR", via_env=True)
        
        # --- agent_instance: 显式 → .lybra/role.instance → env → 回退 actor(同一身份名) ---
        if ex.get("agent_instance"):
            agent_instance.update(value=ex["agent_instance"], source="explicit", env_downgraded=bool(env_instance))
        elif role_data.get("instance"):
            agent_instance.update(value=str(role_data["instance"]), source=".lybra/role", env_downgraded=bool(env_instance))
        elif env_instance:
            agent_instance.update(value=env_instance, source="env:LYBRA_AGENT_INSTANCE", via_env=True)
        elif actor["value"]:
            agent_instance.update(
                value=actor["value"], source=actor["source"],
                via_env=actor["via_env"], env_downgraded=actor["env_downgraded"],
            )
        
        # --- owner_policy_ref: 显式 → .lybra/role.owner_policy_ref → .lybra/policy → env ---
        if ex.get("owner_policy_ref"):
            owner_policy_ref.update(value=ex["owner_policy_ref"], source="explicit", env_downgraded=bool(env_policy))
        elif role_data.get("owner_policy_ref"):
            owner_policy_ref.update(value=str(role_data["owner_policy_ref"]), source=".lybra/role", env_downgraded=bool(env_policy))
        elif policy_text:
            owner_policy_ref.update(value=policy_text, source=".lybra/policy", env_downgraded=bool(env_policy))
        elif env_policy:
            owner_policy_ref.update(value=env_policy, source="env:LYBRA_OWNER_POLICY_REF", via_env=True)
        
        # --- workspace_root: 显式 → .lybra/connection.json.workspace_root → env ---
        conn_root = conn.get("workspace_root") if isinstance(conn, dict) else None
        if ex.get("workspace_root"):
            workspace.update(value=ex["workspace_root"], source="explicit", env_downgraded=bool(env_root))
        elif conn_root:
            workspace.update(value=str(conn_root), source=".lybra/connection.json", env_downgraded=bool(env_root))
        elif env_root:
            workspace.update(value=env_root, source="env:LYBRA_WORKSPACE_ROOT", via_env=True)
        
        # --- gate_url: 显式 → .lybra/connection.json.mcp.rpc_url → env → schema 缺省 ---
        conn_gate = None
        if isinstance(conn, dict):
            mcp_cfg = conn.get("mcp") or {}
            if isinstance(mcp_cfg, dict):
                conn_gate = mcp_cfg.get("rpc_url")
        if ex.get("gate_url"):
            gate_url.update(value=ex["gate_url"], source="explicit", env_downgraded=bool(env_gate_url))
        elif conn_gate:
            gate_url.update(value=str(conn_gate), source=".lybra/connection.json", env_downgraded=bool(env_gate_url))
        elif env_gate_url:
            gate_url.update(value=env_gate_url, source="env:LYBRA_GATE_URL", via_env=True)
        else:
            gate_url.update(value=schema_gate_url, source="schema:urls.gate_local")
        
        # --- token: 显式 → .lybra/connection.json.tokens (instance 匹配 → role 匹配) → env ---
        if ex.get("token"):
            token.update(value=ex["token"], source="explicit", env_downgraded=bool(env_token))
        else:
            matched = None
            if isinstance(conn, dict):
                tokens = conn.get("tokens") or []
                if isinstance(tokens, list):
                    ai = agent_instance["value"]
                    rl = role["value"]
                    if ai:
                        for t in tokens:
                            if isinstance(t, dict) and t.get("agent_instance") == ai and t.get("token"):
                                matched = t["token"]
                                break
                    if not matched and rl:
                        for t in tokens:
                            if isinstance(t, dict) and t.get("role") == rl and t.get("token"):
                                matched = t["token"]
                                break
            if matched:
                token.update(value=str(matched), source=".lybra/connection.json", env_downgraded=bool(env_token))
            elif env_token:
                token.update(value=env_token, source="env:LYBRA_TOKEN", via_env=True)
        
        return {
            "role": role,
            "actor": actor,
            "agent_instance": agent_instance,
            "owner_policy_ref": owner_policy_ref,
            "token": token,
            "workspace_root": workspace,
            "gate_url": gate_url,
        }
