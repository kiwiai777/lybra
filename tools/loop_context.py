"""AIPOS-R1: LoopContext — 解析一次贯穿动词的不可变上下文。

设计权威: DESIGN v2 §3

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


class WorkstationFileError(ValueError):
    """AIPOS-F115 件②(gap #67): 工位声明文件(.lybra/role / actor / policy / connection.json)存在但不可读或格式坏。

    fail-closed: 「已声明但坏」≠「未声明」, 禁静默降级到 env / schema 缺省(原实现 except 吞错后落下一层)。
    拒因带出口(修复该文件或重跑 lybra enroll 重写工位声明)。ValueError 子类: 既有 `except ValueError` 的调用方照常接住。
    """

    def __init__(self, path: Path, exc: BaseException | str) -> None:
        self.path = path
        super().__init__(
            f"工位声明文件 {path} 不可读/格式坏: {exc} —— 出口: 修复该文件, 或重跑 lybra enroll 重写本工位声明"
            "(已声明但坏不按未声明处理, 不落 env/缺省)"
        )


def _read_declared_text(path: Path) -> str | None:
    """AIPOS-F115 件②: 工位声明文本文件读取——不存在 = None(未声明); 存在但读失败 = WorkstationFileError(fail-closed)。"""
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkstationFileError(path, exc) from exc


def _load_declared_connection(lybra_dir: Path) -> dict[str, Any] | None:
    """AIPOS-F115 件②: 工位 connection.json——不存在 = None(未声明); 存在但坏 = WorkstationFileError(禁静默降级)。"""
    connection_file = lybra_dir / "connection.json"
    if not connection_file.is_file():
        return None
    try:
        return ConnectionResolver.load_connection_config(lybra_dir)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise WorkstationFileError(connection_file, exc) from exc


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
    """连接→token 解析器。

    AIPOS-F81: token 挑选不在此实现 —— resolve_token / resolve_identity 一律委托
    tools/aipos_cli/token_resolver.py(F59 唯一实现: instance → role, 排除 retired)。
    
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
        # AIPOS-F115 件②(gap #67): connection.json 不存在 = 未声明, 落下一层; 存在但坏 = WorkstationFileError(原 except 吞错静默降级到 env/缺省)
        if workspace_root:
            lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
            if lybra_dir:
                config = _load_declared_connection(lybra_dir)
                mcp_config = config.get("mcp", {}) if config is not None else {}
                if isinstance(mcp_config, dict):
                    rpc_url = mcp_config.get("rpc_url")
                    if rpc_url:
                        return str(rpc_url)
        
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
        # AIPOS-F81: 挑选委托 token_resolver 单源(instance → role, 排除 retired); 本处不再自带挑选循环。
        # 无命中(TokenNotFoundError)才落 env 兜底; 命中全 retired / 文件坏 = 抛出(fail-closed, 拒因带重签出口)。
        workstation_miss = ".lybra/connection.json not found"
        if workspace_root:
            lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
            connection_file = lybra_dir / "connection.json" if lybra_dir else None
            if connection_file is not None and connection_file.is_file():
                from tools.aipos_cli.token_resolver import TokenNotFoundError, get_token_for_role_and_project

                try:
                    return get_token_for_role_and_project(
                        connection_file, role, None, agent_instance=agent_instance,
                    )
                except TokenNotFoundError as exc:
                    workstation_miss = str(exc)  # 本层无命中: 按声明优先级落到 env 兜底; 其余错误原样抛出
        
        # Environment override (最低优先级)
        env_token = source_env.get("LYBRA_TOKEN", "").strip()
        if env_token:
            return env_token
        
        raise ValueError(
            f"Cannot resolve token for role={role}, agent_instance={agent_instance} ({workstation_miss}). "
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
        # AIPOS-F115 件②(gap #67): 原 rstrip("/mcp") 按字符集剥(会削掉以 m/c/p 结尾的主机名), 改走门基址换算唯一实现
        from tools.aipos_cli.confirm_client import gate_base_url

        schema_gate_url = gate_base_url(schema_gate_url)
        
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
            # AIPOS-F115 件②(gap #67): 文件不存在 = 未声明(落下一层); 存在但不可读/格式坏 = WorkstationFileError
            # (原四处 except Exception 吞错后按「未声明」静默降级到 env/缺省)。
            role_file = lybra_dir / "role"
            role_text = _read_declared_text(role_file)
            if role_text is not None:
                content = role_text.strip()
                if content.startswith("{"):
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError as exc:
                        raise WorkstationFileError(role_file, exc) from exc
                    if not isinstance(parsed, dict):
                        raise WorkstationFileError(role_file, "须为 JSON 对象(role/instance)")
                    role_data = parsed
                elif content:
                    role_data = {"role": content}
            conn = _load_declared_connection(lybra_dir)
            actor_text = (_read_declared_text(lybra_dir / "actor") or "").strip() or None
            policy_text = (_read_declared_text(lybra_dir / "policy") or "").strip() or None
        
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
        
        # --- token: 显式 → .lybra/connection.json.tokens (instance 匹配 → role 匹配, 排除 retired) → env ---
        if ex.get("token"):
            token.update(value=ex["token"], source="explicit", env_downgraded=bool(env_token))
        else:
            # AIPOS-F81: 挑选委托 token_resolver.select_token_entry 单源(instance → role, 排除 retired)。
            # 无命中才落 env 兜底; 命中全 retired = value None + error(带重签出口), 禁 env 静默顶替(fail-closed)。
            from tools.aipos_cli.token_resolver import (
                TOKEN_ENTRY_FIELDS,
                TokenNotFoundError,
                TokenResolutionError,
                select_token_entry,
            )

            matched = None
            token_error = None
            tokens = conn.get("tokens") if isinstance(conn, dict) else None
            if isinstance(tokens, list):
                try:
                    matched = select_token_entry(
                        tokens,
                        role=role["value"],
                        agent_instance=agent_instance["value"],
                        source=str(lybra_dir / "connection.json") if lybra_dir else ".lybra/connection.json",
                    )[TOKEN_ENTRY_FIELDS["token"]]
                except TokenNotFoundError:
                    matched = None
                except TokenResolutionError as exc:
                    token_error = str(exc)
            if matched:
                token.update(value=str(matched), source=".lybra/connection.json", env_downgraded=bool(env_token))
            elif token_error:
                token.update(error=token_error, env_downgraded=bool(env_token))
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
