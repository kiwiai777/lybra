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


class ProjectEnforcer:
    """统一的项目域执法点 (AIPOS-F66)
    
    检查 token 的 projects 字段与解析出的项目是否匹配。
    """
    
    @staticmethod
    def check_project_scope(token_data: dict[str, Any], active_project: str) -> tuple[bool, str | None]:
        """检查 token 是否有权访问指定项目
        
        Args:
            token_data: token 数据
            active_project: 当前活跃项目名
        
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
        else:
            return (
                False,
                f"token is scoped to projects {project_list}, but active project is '{active_project}'. "
                f"Rotate a token scoped to this project or operate within an authorized project."
            )
