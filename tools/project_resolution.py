"""AIPOS-F66: 项目域解析单一化 — 统一解析源

收敛 ≥9 处各自解析为 1 处解析 + 1 处执法。

设计原则:
- 显式优先级,禁静默回落到全局 active_project
- 支持多项目域 token (projects: [A, B])
- 项目无关:无任何项目名硬编码
- 兼容性:无 projects 字段的 token 行为不变
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class ProjectResolver:
    """统一的项目域解析器 (AIPOS-F66)
    
    单一真相源:所有"这是哪个项目"的判断都收敛到这里。
    """
    
    @staticmethod
    def resolve_project(
        *,
        explicit_project: str | None = None,
        token_data: dict[str, Any] | None = None,
        workspace_root: Path | None = None,
        home_root: Path | None = None,
        env: dict[str, str] | None = None,
        global_config: dict[str, Any] | None = None,
    ) -> str:
        """解析项目名,显式优先级(AIPOS-F66 + F52 根治)
        
        优先级顺序:
        1. 显式参数 (--project / explicit_project)
        2. token 的 projects 字段 (单项目 or default_project)
        3. workspace_root/project.json (F52: 从该路径直接读取,不回落全局)
        4. env: LYBRA_ACTIVE_PROJECT
        5. global ~/.lybra/config.json active_project
        6. 单项目回落 (home_root 下唯一项目)
        7. 失败: PROJECT_AMBIGUOUS
        
        Args:
            explicit_project: 显式指定的项目名 (--project)
            token_data: token 数据 (含 projects 字段)
            workspace_root: 工作区根路径
            home_root: 项目家目录 (用于单项目回落)
            env: 环境变量字典 (默认 os.environ)
            global_config: 全局配置字典 (默认自动加载)
        
        Returns:
            项目名 (非空字符串)
        
        Raises:
            ValueError: PROJECT_AMBIGUOUS / PROJECT_NOT_FOUND
        """
        source_env = env if env is not None else os.environ
        
        # 1. 显式参数 (最高优先级)
        if explicit_project and str(explicit_project).strip():
            return str(explicit_project).strip()
        
        # 2. token 的 projects 字段
        if token_data:
            project_from_token = ProjectResolver._extract_project_from_token(token_data)
            if project_from_token:
                return project_from_token
        
        # 3. workspace_root/project.json (F52: 显式 workspace_root 时从此读取,禁回落全局)
        if workspace_root:
            project_from_workspace = ProjectResolver._read_project_from_workspace(workspace_root)
            if project_from_workspace:
                return project_from_workspace
        
        # 4. env: LYBRA_ACTIVE_PROJECT
        # 注意:如果 env 包含 __skip_env_check__(来自 resolve_active_project),跳过此步骤
        skip_env = isinstance(env, dict) and "__skip_env_check__" in env
        if not skip_env:
            env_project = str(source_env.get("LYBRA_ACTIVE_PROJECT", "")).strip()
            if env_project:
                return env_project
        
        # 5. global ~/.lybra/config.json active_project
        if global_config is None:
            # 延迟导入避免循环依赖
            from tools.aipos_cli.workspace_config import load_global_config
            global_config = load_global_config(source_env)
        
        from tools.aipos_cli.workspace_config import active_project_from_config
        global_project = active_project_from_config(global_config)
        if global_project:
            return global_project
        
        # 6. 单项目回落 (home_root 下唯一项目)
        if home_root:
            single_project = ProjectResolver._single_project_fallback(home_root)
            if single_project:
                return single_project
        
        # 7. 失败: PROJECT_AMBIGUOUS
        raise ValueError(
            "PROJECT_AMBIGUOUS: could not resolve project via --project, token, "
            "workspace project.json, LYBRA_ACTIVE_PROJECT, global config, or single-project fallback. "
            "Specify project explicitly or ensure workspace is properly configured."
        )
    
    @staticmethod
    def _extract_project_from_token(token_data: dict[str, Any]) -> str | None:
        """从 token 提取项目名 (支持多项目域 token)
        
        逻辑:
        - 无 projects 字段 → None (兼容旧 token)
        - projects: [A] → "A" (单项目)
        - projects: [A, B], default_project: A → "A" (多项目 + 默认)
        - projects: [A, B], 无 default_project → None (需显式指定)
        """
        projects = token_data.get("projects")
        if not projects:
            return None
        
        if isinstance(projects, list):
            if len(projects) == 1:
                return str(projects[0])
            
            # 多项目 token: 检查 default_project
            default_project = token_data.get("default_project")
            if default_project:
                return str(default_project)
        
        return None
    
    @staticmethod
    def _read_project_from_workspace(workspace_root: Path) -> str | None:
        """从 workspace_root/project.json 读取项目名 (F52 核心修复)
        
        Raises:
            FileNotFoundError: project.json 不存在
            ValueError: project.json 存在但缺 project 字段
        """
        from tools.aipos_cli.workspace_config import read_project_json
        
        try:
            project_data = read_project_json(str(workspace_root))
            project_name = str(project_data.get("project") or project_data.get("name") or "").strip()
            
            if project_name:
                return project_name
            else:
                raise ValueError(
                    f"PROJECT_NOT_FOUND: {workspace_root}/project.json exists but missing 'project' field. "
                    f"Add 'project' field to project.json or ensure the workspace is properly initialized."
                )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"PROJECT_NOT_FOUND: {workspace_root}/project.json not found. "
                f"Ensure the workspace path is correct and contains a valid project.json."
            )
    
    @staticmethod
    def _single_project_fallback(home_root: Path) -> str | None:
        """单项目回落:home_root 下唯一项目
        
        Returns:
            项目名 (唯一项目) 或 None (0个或多个项目)
        """
        from tools.aipos_cli.workspace_config import has_workspace_queue
        
        if not home_root.exists():
            return None
        
        candidates = [
            child.name
            for child in home_root.iterdir()
            if child.is_dir() 
            and has_workspace_queue(child) 
            and (child / "project.json").exists()
        ]
        
        if len(candidates) == 1:
            return candidates[0]
        
        return None


def role_is_cross_project(entry: dict[str, Any]) -> bool:
    """AIPOS-F92 件② / AIPOS-F274: 凭据条目(或门 capability)的角色(role_class 优先, 否则角色名)在 roles.schema 声明
    project_scope=cross_project → True(Owner 角色类)。唯一读取口(门 http_sse._role_is_cross_project 委托本函数)。
    注册表不可读 = SchemaLoadError 上抛(fail-closed: 不猜)。"""
    from tools.schema_loader import get_role_spec

    role = str(entry.get("role_class") or entry.get("role") or "").strip()
    spec = get_role_spec(role) if role else None
    return bool(spec) and str(spec.get("project_scope") or "") == "cross_project"


def owner_explicit_target_declaration() -> dict[str, Any]:
    """AIPOS-F274: roles.schema project_scope_semantics.owner_explicit_target(Owner 显式目标跨项目放宽的规则与开关)。
    缺节 / 缺 enabled 布尔 = SchemaLoadError(fail-closed: 调用方按「不放宽」处理并出声)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("roles").get("project_scope_semantics") or {}).get("owner_explicit_target")
    if not isinstance(decl, dict) or not isinstance(decl.get("enabled"), bool):
        raise SchemaLoadError("roles.schema project_scope_semantics.owner_explicit_target 缺失或 enabled 非布尔(AIPOS-F274)")
    return decl


def credential_project_label(token_data: dict[str, Any]) -> str | None:
    """AIPOS-F274 件④: 签发凭据所属项目(记录追溯用): default_project(home 联合注册表按来源项目给 Owner 凭据的路由缺省 / 铸凭据时绑定),
    否则 projects 列表(逗号连接)。两者皆无 = None(不可归属)。"""
    default = str(token_data.get("default_project") or "").strip()
    if default:
        return default
    projects = token_data.get("projects")
    if isinstance(projects, list):
        names = [str(p).strip() for p in projects if str(p).strip()]
        if names:
            return ",".join(names)
    return None


def cross_project_trace(token_data: dict[str, Any], target_project: str) -> dict[str, str] | None:
    """AIPOS-F274 件④: 跨项目签发追溯字段 {credential_project, target_project}; 凭据所属项目不可归属或目标即在其中 = None(非跨项目)。"""
    label = credential_project_label(token_data)
    target = str(target_project or "").strip()
    if not label or not target or target in label.split(","):
        return None
    return {"credential_project": label, "target_project": target}


class ProjectEnforcer:
    """统一的项目域执法点 (AIPOS-F66)
    
    检查 token 的 projects 字段与解析出的项目是否匹配。
    AIPOS-F274: Owner 角色类的显式目标跨项目放宽也只在这里判(owner_explicit_target), 门各判定点同调。
    """
    
    @staticmethod
    def check_project_scope(
        token_data: dict[str, Any],
        active_project: str,
        *,
        explicit: bool = False,
        target_root: str | Path | None = None,
        home_root: str | Path | None = None,
    ) -> tuple[bool, str | None]:
        """检查 token 是否有权访问指定项目
        
        Args:
            token_data: token 数据
            active_project: 当前活跃项目名
            explicit: 调用方是否显式带了目标项目(AIPOS-F274: project 参数 / 显式 workspace_root / 显式 governance_root)
            target_root: 显式目标根(有则须正是 <home>/<active_project>)
            home_root: 门 home 根(缺 = workspace_config.resolve_home_root 唯一解析)
        
        Returns:
            (allowed, error_detail) 
            - allowed=True: 允许访问
            - allowed=False: 拒绝访问,error_detail 包含原因
        """
        projects = token_data.get("projects")
        
        # 无 projects 字段 → 旧 token,兼容:允许访问 (AIPOS-229)
        if not projects:
            return (True, None)
        
        # 有 projects 字段 → 检查成员资格
        if not isinstance(projects, list):
            return (False, f"token 'projects' field must be a list, got: {type(projects).__name__}")
        
        project_list = [str(p) for p in projects]
        
        if active_project in project_list:
            return (True, None)
        allowed, why = ProjectEnforcer.owner_explicit_target(
            token_data, active_project, explicit=explicit, target_root=target_root, home_root=home_root)
        if allowed:
            return (True, None)
        detail = (f"token is scoped to projects {project_list}, but active project is '{active_project}'. "
                  f"Rotate a token scoped to this project or operate within an authorized project.")
        if why:
            detail += f" [owner_explicit_target: {why}]"
        if "*" in project_list:
            from tools.schema_loader import SchemaLoadError

            try:
                wildcard = owner_explicit_target_declaration().get("wildcard") or {}
                detail += f" [\"*\" is not a wildcard; exit: {wildcard.get('exit')}]"
            except SchemaLoadError as exc:  # 声明读不出: 拒因照出, 不吞
                detail += f" [\"*\" is not a wildcard; declaration unreadable: {exc}]"
        return (False, detail)

    @staticmethod
    def owner_explicit_target(
        token_data: dict[str, Any],
        target_project: str,
        *,
        explicit: bool,
        target_root: str | Path | None = None,
        home_root: str | Path | None = None,
    ) -> tuple[bool, str | None]:
        """AIPOS-F274 件②: Owner 角色类显式目标跨项目放宽(规则与开关读 roles.schema project_scope_semantics.owner_explicit_target)。
        放行须同时满足: 开关开; 凭据角色类 project_scope=cross_project; 调用方显式带目标; 目标是 home 根内已注册项目
        (workspace_config._project_candidates); 显式根(若给)解析后正是 <home>/<目标>。任一不满足 = (False, 原因)。
        声明 / 注册表读不出 = 不放宽(fail-closed), 原因带原文。"""
        from tools.schema_loader import SchemaLoadError

        try:
            decl = owner_explicit_target_declaration()
            if not decl["enabled"]:
                return (False, "disabled by roles.schema project_scope_semantics.owner_explicit_target.enabled")
            if not role_is_cross_project(token_data):
                return (False, None)  # 非 Owner 角色类: 不放宽(拒因照成员判定原文)
        except SchemaLoadError as exc:
            return (False, f"declaration unreadable ({exc}); not relaxed")
        if not explicit:
            return (False, "Owner credential may act on another registered project only when the call names it explicitly "
                           "(project / workspace_root / governance_root)")
        target = str(target_project or "").strip()
        from tools.aipos_cli.workspace_config import _project_candidates, resolve_home_root

        home = Path(home_root).expanduser().resolve() if home_root else resolve_home_root().expanduser().resolve()
        if not target or target not in _project_candidates(home):
            return (False, f"target project {target!r} is not a registered project under home root {home} "
                           "(registry = 5_tasks/queue + project.json)")
        if target_root is not None and Path(target_root).expanduser().resolve() != (home / target).resolve():
            return (False, f"explicit root {target_root} is not the registered project root {home / target}")
        return (True, None)
