from __future__ import annotations

import json
import os
from tools.aipos_cli.clock import iso_z, utc_now
from pathlib import Path
from typing import Any

from tools.schema_loader import get_config_port

# AIPOS-R4B-1 → AIPOS-F106 件②(M3): 端口缺省值唯一来源 = schema/config.schema.json ports(board_default / gate_default),
# 经 tools/schema_loader.get_config_port() 读取。原「为规避 editable-install namespace package 而保留的常量镜像」已无必要
# (AIPOS-R4B-1 FIX-2 已让 tools 成为普通 package, board_login / http_sse / confirm_client 均模块级读取), 镜像字面删除。
CONFIG_RELATIVE_PATH = Path(".lybra") / "config.json"
DEFAULT_BOARD_HOST = "127.0.0.1"
DEFAULT_BOARD_PORT = get_config_port("board_default")
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = get_config_port("gate_default")  # 门进程即 MCP 服务: 同一个端口事实(原 mcp_server_default 键已合并)

# AIPOS-224 (governance home, Slice 0): home-root + active-project resolution.
# This block is ADDITIVE and UNWIRED — no existing resolver/caller behaviour changes in this
# slice. (AIPOS-F117: the v1 writer default_workspace_config()/write_workspace_config() had zero product
# callers and was deleted; .lybra/config.json is an optional hand-written override read by workspace_runtime_config.) Pure functions, fail-closed, stdlib only.
DEFAULT_HOME_ROOT = Path("~/.lybra/projects")
HOME_ROOT_ENV = "LYBRA_HOME_ROOT"
ACTIVE_PROJECT_ENV = "LYBRA_ACTIVE_PROJECT"
# AIPOS-F106 件③(M15): 治理工作区根的环境变量统一为 LYBRA_WORKSPACE_ROOT(与 config.schema identity_resolution.keys.workspace_root
# 及其余 LYBRA_* 同名族)。旧名 AIPOS_WORKSPACE_ROOT 只在 workspace_root_from_env 这一个兼容读取点识别并打废弃告警;
# 产品代码只写新名(serve 子进程 / board / mcp 进程内设置), 其余读取一律经本函数。两名均在 config.schema environment_variables 声明。
WORKSPACE_ROOT_ENV = "LYBRA_WORKSPACE_ROOT"
LEGACY_WORKSPACE_ROOT_ENV = "AIPOS_WORKSPACE_ROOT"
_LEGACY_WORKSPACE_ROOT_WARNED: set[str] = set()


def workspace_root_from_env(env: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """AIPOS-F106 件③: 工作区根环境变量唯一读取口 → (原始值, 变量名); 都未设 = (None, None)。

    新名 LYBRA_WORKSPACE_ROOT 优先; 只有旧名 AIPOS_WORKSPACE_ROOT 时照用并向 stderr 打一次废弃告警(每个值一次);
    两名并存且不同 = 用新名并告警旧名被忽略。"""
    import sys

    source_env = env if env is not None else os.environ
    new_value = str(source_env.get(WORKSPACE_ROOT_ENV) or "").strip()
    legacy_value = str(source_env.get(LEGACY_WORKSPACE_ROOT_ENV) or "").strip()
    if legacy_value and legacy_value != new_value and legacy_value not in _LEGACY_WORKSPACE_ROOT_WARNED:
        _LEGACY_WORKSPACE_ROOT_WARNED.add(legacy_value)
        action = f"已被 {WORKSPACE_ROOT_ENV} 覆盖, 忽略" if new_value else "仍按其值解析"
        print(
            f"Warning: 环境变量 {LEGACY_WORKSPACE_ROOT_ENV} 已废弃(AIPOS-F106), 请改用 {WORKSPACE_ROOT_ENV}; 本次{action}",
            file=sys.stderr,
        )
    if new_value:
        return new_value, WORKSPACE_ROOT_ENV
    if legacy_value:
        return legacy_value, LEGACY_WORKSPACE_ROOT_ENV
    return None, None

# AIPOS-226 (Slice 2): the global Lybra runtime root. Lybra's own runtime state (the
# runtime config that points at the truth home + names the active project, and the role
# tokens) lives here so it NEVER enters a user truth repo. No secrets in config.json.
GLOBAL_LYBRA_DIR = Path("~/.lybra")
GLOBAL_CONFIG_REL = Path("config.json")


def has_workspace_queue(path: Path, *, established: bool = False) -> bool:
    """AIPOS-F88 件②: 治理工作区/项目根「结构识别」的唯一判据——结构签名, 不看路径名(换机器/目录名无关)。

    签名 = 队列根是目录。队列根读声明(project_paths 单一读取口: project.json paths.queue_root, 缺省取
    config.schema configuration_sources.project_json.schema.paths.queue_root.default), 代码不写死。
    established=True: 另要求 project.json 存在(已建项目双标记, AIPOS-226 home 扫描 / 项目注册表 / resolve_project_root)。

    委托方(禁第二实现): enroll_client.is_governance_workspace(②)、enroll_deliver.validate_workspace_root、
    task_loader._has_queue_root、mcp_server 项目注册表扫描、home 候选扫描、governance_workspace_root 结构识别。
    (原 TS 镜像 lybra-loop/loop-context.ts isGovernanceWorkspace 随 lybra-loop 扩展 AIPOS-F91 退役删除; 现仅此一处。)
    """
    root = Path(path)
    if established and not project_json_path(root).is_file():
        return False
    return Path(project_paths(root)["queue_root"]).is_dir()


def _validate_workspace_root(path: Path, *, source: str) -> Path:
    resolved = path.expanduser().resolve()
    if not has_workspace_queue(resolved):
        raise FileNotFoundError(f"{source} does not contain 5_tasks/queue: {resolved}")
    return resolved


def load_workspace_config(config_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Lybra workspace config JSON: {config_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Lybra workspace config must be a JSON object: {config_path}")
    return data


def workspace_root_from_config(config_path: Path) -> Path:
    data = load_workspace_config(config_path)
    raw = str(data.get("workspace_root") or ".").strip()
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = config_path.parent.parent / root
    return _validate_workspace_root(root, source=f"Lybra config {config_path}")


def find_workspace_config(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        config_path = candidate / CONFIG_RELATIVE_PATH
        if config_path.is_file():
            return config_path
    return None


def resolve_workspace_root(
    start: Path | None = None,
    *,
    explicit_root: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve the project/workspace root.

    Thin wrapper over ``resolve_workspace_context`` (the single AIPOS-226 precedence ladder);
    returns only the project root for the many existing callers. Behavior is byte-identical to
    the pre-AIPOS-227 implementation.
    """
    return resolve_workspace_context(start, explicit_root=explicit_root, env=env)[0]


def resolve_workspace_context(
    start: Path | None = None,
    *,
    explicit_root: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Path, Path | None]:
    """Resolve ``(project_root, home_root)`` via the single AIPOS-226 precedence ladder.

    ``home_root`` is the survivable truth home **when the home model resolves the workspace**
    (``LYBRA_HOME_ROOT`` env / a v2 in-workspace config carrying ``home_root`` / the global
    ``~/.lybra/config.json`` ``home_root``), else ``None`` for the legacy / explicit / marker
    paths.

    AIPOS-227: this is the ONE place the precedence ladder lives, so the 196a ingestion
    home-guard and ``resolve_workspace_root`` can never drift. ``home_root`` is ``None`` IFF the
    home model is NOT the resolution path — so a ``None`` home_root unambiguously means
    legacy-v1 / explicit / direct, never "home model with an unresolved home" (R-1). On a home
    path the project is resolved eagerly and a misresolution raises loudly
    (``PROJECT_AMBIGUOUS`` / ``PROJECT_NOT_ESTABLISHED``) before any caller proceeds.
    """
    source_env = env if env is not None else os.environ
    if explicit_root:
        return _validate_workspace_root(Path(explicit_root), source="--workspace-root"), None

    raw_env_root, env_name = workspace_root_from_env(source_env)
    if raw_env_root:
        return _validate_workspace_root(Path(raw_env_root), source=str(env_name)), None

    # ---------------------------------------------------------------------------------
    # AIPOS-226 resolution precedence (AIPOS-223 §1.4, highest first). The two-root home
    # model is folded in WITHOUT displacing the documented back-compat order — a LOCAL
    # workspace signal (an in-workspace .lybra/config.json, then a bare 5_tasks/queue
    # subtree at/above the start) wins over the GLOBAL ~/.lybra/config.json home model.
    #
    #   1. --workspace-root / explicit_root           (handled above)
    #   2. LYBRA_WORKSPACE_ROOT env (旧名 AIPOS_WORKSPACE_ROOT 废弃兼容)  (handled above)
    #   3. LYBRA_HOME_ROOT env                         -> home model
    #   4. in-workspace .lybra/config.json (upward):   v2 (home_root) -> home model
    #                                                  v1 (workspace_root) -> that root
    #   5. upward 5_tasks/queue marker (bare subtree)  -> that root (legacy back-compat)
    #   6. global ~/.lybra/config.json .home_root      -> home model
    #   7. fail closed
    #
    # FIX D — SCHEMA DISTINCTION: the global runtime config (~/.lybra/config.json, carries a
    # `home_root`) is a DIFFERENT schema from a legacy v1 in-workspace config
    # (<ws>/.lybra/config.json, carries `workspace_root`, NO `home_root`). The upward search
    # (find_workspace_config) can land on either. A found config carrying `home_root` routes to
    # the home model and is NEVER misread as a v1 workspace_root config; only a genuine v1
    # config (no home_root) drives workspace_root_from_config.
    global_config = load_global_config(source_env)
    config_path = find_workspace_config(start)
    found_config: dict[str, Any] = {}
    if config_path is not None:
        found_config = load_workspace_config(config_path)
    found_home_root = home_root_from_config(found_config)

    # 3. LYBRA_HOME_ROOT env -> home model (the brand-aligned home env, highest home signal).
    if str(source_env.get(HOME_ROOT_ENV) or "").strip():
        home = resolve_home_root(env=source_env)
        project = resolve_active_project(home, env=source_env, global_config=global_config)
        return resolve_project_root(home, project), home

    # 4. In-workspace config (upward search). A v2 config (home_root) routes to the home model
    #    using ITS home_root + active_project; a v1 config (no home_root) drives the legacy
    #    workspace_root_from_config. Either way a LOCAL config beats the global home model.
    if config_path is not None:
        if found_home_root is not None:
            home = resolve_home_root(explicit_root=found_home_root, env=source_env)
            project = resolve_active_project(home, env=source_env, config=found_config)
            return resolve_project_root(home, project), home
        return workspace_root_from_config(config_path), None

    # 5. Upward 5_tasks/queue marker (legacy bare project subtree). A local workspace at/above
    #    the start wins over the global home model so v1 inputs / evidence workspaces / bare-cwd
    #    callers stay byte-identical and are never hijacked by the global runtime config.
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if has_workspace_queue(candidate):
            return candidate, None

    # 6. Global ~/.lybra/config.json .home_root -> home model. This is the production path when
    #    the caller's cwd is the code repo (no local workspace signal): the global runtime config
    #    names the truth home + active project. Fails LOUDLY (PROJECT_NOT_ESTABLISHED /
    #    PROJECT_AMBIGUOUS) on misresolution — never a silent default.
    if global_config_home_root(global_config) is not None:
        home = resolve_home_root(env=source_env)
        project = resolve_active_project(home, env=source_env, global_config=global_config)
        return resolve_project_root(home, project), home

    raise FileNotFoundError("Could not locate Lybra workspace root containing .lybra/config.json or 5_tasks/queue")


#: AIPOS-F117 件①(gap #52): .lybra/config.json 未写 mcp.*_token_env 时的缺省环境变量名(config.schema environment_variables 声明同名)
DEFAULT_TRANSPORT_TOKEN_ENV = "LYBRA_MCP_TOKEN"
DEFAULT_CAPABILITY_TOKEN_ENV = "LYBRA_CAPABILITY_TOKEN"


def workspace_runtime_config(workspace_root: Path, *, strict: bool = True) -> dict[str, Any]:
    """AIPOS-F117 件①(gap #52): 治理根 `.lybra/config.json` 运行时字段(board / mcp 地址与令牌环境变量名)的唯一读取口——
    serve / board / mcp 包装(aipos_cli._config_defaults)与看板运行时状态面(web/board _runtime_config_defaults)同读, 原两份
    逐字段重复的读取 + 缺省删并为此一处。

    该文件是可选的手写覆盖(config.schema configuration_sources.workspace_config 声明其键); 产品不再生成它——原写入方
    write_workspace_config / default_workspace_config 产品零调用方(只剩测试), 随建项目收敛到 `lybra project new`
    (不产此文件)删除。文件缺 = 全取缺省(端口 = config.schema ports 经 get_config_port, 令牌环境变量名 = 上方缺省)。
    文件坏: strict=True(serve 等启动路径)= 原样抛 ValueError(fail-closed); strict=False(只读状态面)= 取缺省并把错误放进
    config_error 供展示(不静默)。返回键: config_path(绝对路径串 / None)、config_error、board_declared(文件写了 board 段)、
    board_url、board_host/port、mcp_host/port、transport_token_env、capability_token_env。
    读取方: aipos_cli._config_defaults(serve/board/mcp 包装)、web/board _runtime_config_defaults(状态面)、
    board_login.resolve_board_url(看板登录地址)。"""
    config_path = Path(workspace_root) / CONFIG_RELATIVE_PATH
    config: dict[str, Any] = {}
    config_error: str | None = None
    if config_path.is_file():
        try:
            config = load_workspace_config(config_path)
        except (ValueError, OSError) as exc:
            if strict:
                raise
            config_error = str(exc)
            config = {}
    board = config.get("board") if isinstance(config.get("board"), dict) else {}
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    return {
        "config_path": str(config_path) if config_path.is_file() else None,
        "config_error": config_error,
        "board_declared": isinstance(config.get("board"), dict),
        "board_url": str(board.get("url") or "").strip() or None,
        "board_host": str(board.get("host") or DEFAULT_BOARD_HOST),
        "board_port": int(board.get("port") or DEFAULT_BOARD_PORT),
        "mcp_host": str(mcp.get("host") or DEFAULT_MCP_HOST),
        "mcp_port": int(mcp.get("port") or DEFAULT_MCP_PORT),
        "transport_token_env": str(mcp.get("transport_token_env") or DEFAULT_TRANSPORT_TOKEN_ENV),
        "capability_token_env": str(mcp.get("capability_token_env") or DEFAULT_CAPABILITY_TOKEN_ENV),
    }


# ---------------------------------------------------------------------------
# AIPOS-224 governance home — Slice 0 resolution core (additive, unwired)
#
# Truth lives in a survivable HOME ROOT (default ~/.lybra/projects) holding one subtree per
# PROJECT. These functions resolve (home, project) -> concrete paths with the precedence and
# fail-closed errors specified in AIPOS-223 §"Resolution algorithm". They are NOT yet wired
# into resolve_workspace_root / find_repo_root / any caller — wiring lands in later slices.
# Per ruling 6, project.json (project root) is the sole authority for code_repo; per M2 there
# is no home-config projects{} map here. No disk is created or moved by this module.
# ---------------------------------------------------------------------------


def home_root_from_config(config: dict[str, Any]) -> Path | None:
    """Read the optional v2 `home_root` field. Absent/blank -> None (legacy preserved)."""
    raw = config.get("home_root")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return Path(text).expanduser()


def active_project_from_config(config: dict[str, Any]) -> str | None:
    """Read the optional v2 `active_project` field. Absent/blank -> None."""
    raw = config.get("active_project")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _project_candidates(home_root: Path) -> list[str]:
    """Names of immediate subdirs of `home_root` that look like established projects.

    AIPOS-226: the establishment marker is now BOTH `5_tasks/queue` AND `project.json`.
    A directory with a queue but no project.json is NOT a candidate (and vice versa).
    """
    if not home_root.exists():
        return []
    return sorted(
        child.name
        for child in home_root.iterdir()
        if child.is_dir() and has_workspace_queue(child, established=True)
    )


# ---------------------------------------------------------------------------
# AIPOS-226 governance home — Slice 2: global Lybra runtime config (~/.lybra/config.json)
#
# The two-root model keeps Lybra runtime state OUT of the user's truth repo. The global
# config at ~/.lybra/config.json carries {config_version, home_root, active_project} (no
# secrets). These readers mirror the per-config readers above but operate on the GLOBAL
# config dict. `$HOME` is honored via expanduser so tests can patch HOME to a temp dir.
# ---------------------------------------------------------------------------


def global_config_path(env: dict[str, str] | None = None) -> Path | None:
    """Return ~/.lybra/config.json. Honors $HOME (via expanduser) so tests can patch it.

    When an explicit `env` dict is supplied WITHOUT a HOME key, returns None — an explicit env
    is an isolation request (tests / the v1 byte-identical locks), so the resolver must NOT read
    the real user's ~/.lybra. When `env` is None, the process environment (with its real HOME)
    is used via expanduser.
    """
    if env is not None:
        home = str(env.get("HOME") or "").strip()
        if not home:
            return None
        return Path(home) / ".lybra" / GLOBAL_CONFIG_REL
    return (GLOBAL_LYBRA_DIR / GLOBAL_CONFIG_REL).expanduser()


def load_global_config(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Read ~/.lybra/config.json if present (JSON object), else {}."""
    path = global_config_path(env)
    if path is None or not path.is_file():
        return {}
    return load_workspace_config(path)


def global_config_home_root(config: dict[str, Any]) -> Path | None:
    """Read the global config's `home_root` field. Absent/blank -> None."""
    return home_root_from_config(config)


def global_config_active_project(config: dict[str, Any]) -> str | None:
    """Read the global config's `active_project` field. Absent/blank -> None."""
    return active_project_from_config(config)


def set_active_project(name: str, *, env: dict[str, str] | None = None) -> Path:
    """Owner-side runtime-config write: set ~/.lybra/config.json `active_project`.

    AIPOS-230 §1b: the TUI `/project switch` local Owner action updates the GLOBAL runtime config
    (NOT truth, NOT code; reversible) so the gate resolves the switched project via the §1a
    sequential fallback. Preserves config_version / home_root / other keys. This is the app
    Owner-action layer — never the copilot credential; no token, no gate confirm.
    """
    project = str(name or "").strip()
    if not project:
        raise ValueError("set_active_project requires a non-empty project name")
    path = global_config_path(env)
    if path is None:
        raise ValueError("HOME_NOT_RESOLVED: cannot locate ~/.lybra/config.json to set active_project")
    config = load_global_config(env)
    if not config:
        config = {"config_version": 2}
    config["active_project"] = project
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def resolve_home_root(
    start: Path | None = None,
    *,
    explicit_root: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve the survivable home root (container of project subtrees).

    AIPOS-226 §1.3 precedence:
      1. explicit flag (--home-root / --workspace-root)  — treated AS the home
      2. LYBRA_HOME_ROOT env                             (the brand-aligned home env)
      3. ~/.lybra/config.json .home_root                 (global runtime config)
      4. default ~/.lybra/projects                       (need NOT exist — `project new`
                                                           creates project subtrees under it)
    `start` is accepted for signature stability but no longer drives resolution (the v1
    upward/marker home inference moved out of the home model). This function never creates
    anything.
    """
    return resolve_home_root_with_source(explicit_root=explicit_root, env=env)[0]


def resolve_home_root_with_source(
    *,
    explicit_root: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Path, str]:
    """AIPOS-F92 件②: resolve_home_root 的同一优先级梯(唯一实现), 另返回命中的来源说明(向导 / project new 打印给用户看,
    home 根要改只能经此梯: --home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json home_root > 缺省 ~/.lybra/projects)。"""
    source_env = env if env is not None else os.environ
    if explicit_root:
        return Path(explicit_root).expanduser().resolve(), "显式 --home-root"

    raw_home = str(source_env.get(HOME_ROOT_ENV) or "").strip()
    if raw_home:
        return Path(raw_home).expanduser().resolve(), f"环境变量 {HOME_ROOT_ENV}"

    configured = global_config_home_root(load_global_config(source_env))
    if configured is not None:
        return configured.expanduser().resolve(), "全局配置 ~/.lybra/config.json home_root"

    # Default ~/.lybra/projects. Honor a patched HOME in `env` (consistent with
    # global_config_path) so callers/tests can isolate from the real home.
    if env is not None:
        home = str(env.get("HOME") or "").strip()
        if home:
            return Path(home) / ".lybra" / "projects", "缺省 ~/.lybra/projects"
    return DEFAULT_HOME_ROOT.expanduser(), "缺省 ~/.lybra/projects"


def resolve_active_project(
    home_root: str | Path,
    *,
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    global_config: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Resolve the active project name (AIPOS-F66: 调用统一解析器).

    AIPOS-226 §1.3 + AIPOS-230 §1a precedence (SEQUENTIAL fallback):
      1. --project / explicit
      2. LYBRA_ACTIVE_PROJECT env
      3. in-workspace config .active_project  (AIPOS-225 Slice-1 dict, if supplied & set)
      4. global ~/.lybra/config.json .active_project  (loaded here if `global_config` is not
         supplied) — reached even when an EMPTY in-workspace config is passed (AIPOS-230 fix)
      5. single-project fallback (exactly one <home>/<child> with 5_tasks/queue AND project.json)
      6. else fail-closed ValueError("PROJECT_AMBIGUOUS: ...")

    AIPOS-F66: 收敛到统一解析器 ProjectResolver。
    """
    from tools.project_resolution import ProjectResolver
    
    source_env = env if env is not None else os.environ
    
    # AIPOS-226 §1.3 优先级: explicit > env > in-workspace config > global > single-project
    # 1. 显式参数
    if explicit:
        return explicit
    
    # 2. env
    env_project = source_env.get("LYBRA_ACTIVE_PROJECT", "").strip() or None
    if env_project:
        return env_project
    
    # 3. in-workspace config
    in_workspace_project = None
    if config is not None:
        in_workspace_project = active_project_from_config(config)
    if in_workspace_project:
        return in_workspace_project
    
    # 4-6: 让 ProjectResolver 处理 global config 和 single-project fallback
    # 传入原始 env(包含 HOME),但 ProjectResolver 会跳过 LYBRA_ACTIVE_PROJECT 检查(因为已在上面处理)
    # 注意:必须传入 source_env 让 load_global_config 能找到 HOME
    return ProjectResolver.resolve_project(
        explicit_project=None,
        home_root=Path(home_root).expanduser().resolve(),
        env={"__skip_env_check__": "true", **source_env},  # 特殊标记 + 保留 HOME 等环境变量
        global_config=global_config  # 可能是 None,让 ProjectResolver 自动加载
    )


def resolve_project_root(home_root: str | Path, project: str) -> Path:
    """Resolve <home>/<project>, asserting the 5_tasks/queue marker.

    Fail-closed FileNotFoundError("PROJECT_NOT_ESTABLISHED: ...") when the project subtree is
    missing — there is NO lazy-create (ruling 2=(a)); the error points at `lybra project new`.
    """
    home = Path(home_root).expanduser().resolve()
    name = str(project).strip()
    if not name:
        raise ValueError("PROJECT_NOT_ESTABLISHED: empty project name")
    root = home / name
    # AIPOS-226: the establishment marker is BOTH 5_tasks/queue AND project.json.
    if not has_workspace_queue(root, established=True):
        raise FileNotFoundError(
            f"PROJECT_NOT_ESTABLISHED: project {name!r} is missing the 5_tasks/queue + "
            f"project.json marker under {home}; run `lybra project new {name}` (no lazy-create)."
        )
    return root


def governance_paths(project_root: str | Path) -> dict[str, Path]:
    """Per-project governance + archive + artifact paths under a resolved project root.

    Ruling 1=B: decision_log is a single file `governance/decision_log.md` (directory-ization
    is a separate later slice). Ruling 7: workspace_artifacts is truth and lives under the
    project root. Returns absolute Paths; not yet consumed (board_adapter adoption is Slice 1).
    """
    root = Path(project_root)
    governance = root / "governance"
    return {
        "decision_log": governance / "decision_log.md",
        "stage_archive": root / "stage_archive",
        "workspace_artifacts": root / "workspace_artifacts",
    }


# ---------------------------------------------------------------------------
# AIPOS-226 governance home — Slice 2 (Phase 2a): Owner scaffold + project.json
#
# `project new` / `project set-repo` are LOCAL OWNER scaffolds (ruling 2=a) — not gate
# operations: they mint no token, perform no gate confirm, and the gate has no "create project"
# op. Writing to disk here is intended (the single project-creation entry, AIPOS-F105). project.json is the SOLE authority
# for the project<->code-repo mapping (ruling 6) and carries provenance (M3: project creation
# is non-anonymous). Stdlib only.
# ---------------------------------------------------------------------------

# AIPOS-F104 件②: 原 _QUEUE_STATES 手写副本删除——脚手架预建队列目录读 task_loader.QUEUE_SKELETON_STATES(enums queue_state skeleton 投影;
# task_loader 模块级依赖本模块, 故在调用处惰性导入)


# ---------------------------------------------------------------------------
# AIPOS-335: collaboration_profile schema (AIPOS-304 阶段一)
#
# project.json 新增 collaboration_profile 字段，记录项目协作能力配置。
# 向后兼容：缺字段时不报错，提供默认行为。
# ---------------------------------------------------------------------------

def default_collaboration_profile() -> dict[str, Any]:
    """AIPOS-335: collaboration_profile 默认值(向后兼容现状, 老项目行为零改变)。

    AIPOS-F89 件① M14: 缺省值唯一声明 = config.schema configuration_sources.project_json.schema.collaboration_profile.default
    (原本函数与 flow_description 各写一份缺省, 已收一)。声明缺 = SchemaLoadError(fail-closed)。
    """
    import copy

    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (
        load_schema("config")
        .get("configuration_sources", {})
        .get("project_json", {})
        .get("schema", {})
        .get("collaboration_profile", {})
        .get("default")
    )
    if not isinstance(decl, dict) or not decl:
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.collaboration_profile.default 未声明")
    return copy.deepcopy(decl)


def get_collaboration_profile(project_root: str | Path) -> dict[str, Any]:
    """AIPOS-335: 读取项目的 collaboration_profile, 缺失时返回默认值(逐键补齐部分填写)。

    AIPOS-F89 件① M14: 唯一读取口(flow_description.resolve_collaboration_profile 委托本函数)。
    project.json 不可读 = 精确捕获 + warning + 视为未声明(与 project_paths 同语义, 不静默吞)。
    """
    try:
        project_json = read_project_json(project_root)
    except (OSError, ValueError) as exc:
        import sys

        print(f"Warning: project.json unreadable at {project_root}, using declared default collaboration_profile: {exc}",
              file=sys.stderr)
        project_json = {}
    profile = project_json.get("collaboration_profile")
    result = default_collaboration_profile()
    if isinstance(profile, dict):
        result.update(profile)
    return result


# ---------------------------------------------------------------------------
# AIPOS-338 S5: workspace-level dispatch_mode (auto | manual)
#
# Owner-only switch. Truth lives in project.json (NOT conversation). manual =
# "turn OFF auto-dispatch" (gate guidance surfaces it; the pump that refused under
# manual was retired by AIPOS-F91); auto = manual /claim still works.
# Default auto; old workspaces without the field are treated as auto (zero error).
# Switching is append-only logged (who / when / why). Judgment stays with the
# Owner — product/advisor only PROPOSE a switch (e.g. on repeated dispatch failures).
# ---------------------------------------------------------------------------

DEFAULT_DISPATCH_MODE = "auto"
_DISPATCH_MODES = ("auto", "manual")


def default_dispatch_mode() -> str:
    return DEFAULT_DISPATCH_MODE


def get_dispatch_mode(project_root: str | Path) -> str:
    """Read dispatch_mode from project.json. Absent/invalid -> 'auto' (back-comat)."""
    project_json = read_project_json(project_root)
    mode = str(project_json.get("dispatch_mode") or "").strip().lower()
    return mode if mode in _DISPATCH_MODES else DEFAULT_DISPATCH_MODE


def dispatch_mode_trail_path(project_root: str | Path) -> Path:
    """Append-only switch trail location: <project_root>/governance/dispatch_mode_log.md."""
    return governance_paths(project_root)["decision_log"].parent / "dispatch_mode_log.md"


def set_dispatch_mode(
    project_root: str | Path,
    mode: str,
    *,
    by: str = "owner",
    reason: str = "",
) -> tuple[str, Path]:
    """Owner-only switch of dispatch_mode. Writes project.json (preserve all fields)
    and appends an append-only trail entry. Returns (new_mode, trail_path).

    Refuses invalid modes. Does not mint tokens or confirm gates (local Owner action,
    like set_active_project).
    """
    clean = str(mode or "").strip().lower()
    if clean not in _DISPATCH_MODES:
        raise ValueError(f"dispatch_mode must be one of {list(_DISPATCH_MODES)}, got: {mode!r}")
    root = Path(project_root)
    path = project_json_path(root)
    data = read_project_json(root)
    previous = str(data.get("dispatch_mode") or "").strip().lower() or DEFAULT_DISPATCH_MODE
    if previous not in _DISPATCH_MODES:
        previous = DEFAULT_DISPATCH_MODE
    data["dispatch_mode"] = clean
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # append-only trail
    trail = dispatch_mode_trail_path(root)
    trail.parent.mkdir(parents=True, exist_ok=True)
    ts = iso_z()
    line = f"- {ts}  `{previous}` -> `{clean}`  by={by}  reason={reason or '(none)'}\n"
    with trail.open("a", encoding="utf-8") as fh:
        if trail.stat().st_size == 0:
            fh.write("# Dispatch Mode Switch Log (append-only)\n\n")
        fh.write(line)
    return clean, trail


def project_root_for(home_root: str | Path, name: str) -> Path:
    """The intended <home>/<name> root for a project (no existence assertion)."""
    return Path(home_root).expanduser().resolve() / str(name).strip()


def project_json_path(project_root: str | Path) -> Path:
    return Path(project_root) / "project.json"


def read_project_json(project_root: str | Path) -> dict[str, Any]:
    """Read project.json; returns {} if absent. Sole authority for code_repo (ruling 6)."""
    path = project_json_path(project_root)
    if not path.is_file():
        return {}
    return load_workspace_config(path)


def declared_project_id(governance_root: str | Path) -> str:
    """AIPOS-F108 件③(H6): 治理根 project.json#project(config.schema project_json.project, required)。
    经唯一读取口 read_project_json; 缺文件/缺键/空值/读失败 = ValueError(DRAFT_PROJECT_UNDECLARED, 带出口), 禁回落写死项目 ID。"""
    root = Path(governance_root)
    try:
        value = read_project_json(root).get("project")
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"DRAFT_PROJECT_UNDECLARED: 治理根 {root} 的 project.json 读取失败({exc})。出口: 修正 project.json 或显式给 project"
        ) from exc
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError(
            f"DRAFT_PROJECT_UNDECLARED: 治理根 {root} 的 project.json 未声明 project(config.schema project_json.project)。"
            "出口: 在 project.json 写 \"project\": \"<项目ID>\", 或草稿显式给 project"
        )
    return text


# ---------------------------------------------------------------------------
# AIPOS-F78 件④: 项目落点声明(project.json paths 段)的唯一读取口。
# 声明表(键名/默认值)在 config.schema.json configuration_sources.project_json.schema.paths 一处;
# 缺段/缺键取 default(=现行路径, 0 迁移)。推导核/loop/ingest/渲染器/骨架写入全部经此函数,
# 禁写死 task_cards/…/RETURN.md 或 5_tasks/records/returns。
# ---------------------------------------------------------------------------

PROJECT_PATH_KEYS = ("return_root", "verdict_root", "queue_root", "task_cards_root", "manual_gate_mode", "finalize_mode",
                     "foundation_backlog", "hard_rules_source", "policies_root")  # AIPOS-F103 件④: 信封目录
# AIPOS-F78B 件②: 非路径键(值域读声明 enum), 与布尔 manual_gate_mode 一样不做路径解析
PROJECT_ENUM_KEYS = ("finalize_mode",)


def _project_paths_declaration() -> dict[str, dict[str, Any]]:
    """读 config.schema configuration_sources.project_json.schema.paths.schema(声明缺 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (
        load_schema("config")
        .get("configuration_sources", {})
        .get("project_json", {})
        .get("schema", {})
        .get("paths", {})
        .get("schema")
    )
    if not isinstance(decl, dict) or not decl:
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.paths.schema 未声明")
    return decl


def project_paths(governance_root: str | Path) -> dict[str, Any]:
    """AIPOS-F78: 解析项目落点声明。返回 {return_root: Path, verdict_root: Path, queue_root: Path,
    task_cards_root: Path, manual_gate_mode: bool, finalize_mode: str, foundation_backlog: Path | None,
    hard_rules_source: Path | None, policies_root: Path, declared: {key: bool}}。

    AIPOS-F89 件② M17: 项目治理文档位(foundation_backlog / hard_rules_source)为可选声明(声明表 optional=true, 无 default):
    未声明 = None, 产品不假设任何治理文档名存在。

    - 相对路径相对治理根; 绝对路径原样(chris 形声明用绝对路径)。
    - manual_gate_mode: paths 段优先, 兼容顶层 project.json manual_gate_mode(F73C 件①)。
    - project.json 读失败 = 精确捕获 + warning + 视为未声明(不静默吞)。
    """
    root = Path(governance_root)
    decl = _project_paths_declaration()
    try:
        project = read_project_json(root)
    except (OSError, ValueError) as exc:
        import sys

        print(f"Warning: project.json unreadable at {root}, using declared default paths: {exc}", file=sys.stderr)
        project = {}
    raw_paths = project.get("paths") if isinstance(project.get("paths"), dict) else {}
    result: dict[str, Any] = {"declared": {}}
    for key in PROJECT_PATH_KEYS:
        spec = decl.get(key) or {}
        default = spec.get("default")
        declared = key in raw_paths and raw_paths.get(key) not in (None, "")
        value = raw_paths.get(key) if declared else default
        if key == "manual_gate_mode":
            if not declared and "manual_gate_mode" in project:
                value, declared = project.get("manual_gate_mode"), True
            result[key] = bool(value)
        elif key in PROJECT_ENUM_KEYS:
            allowed = [str(v) for v in (spec.get("enum") or [])]
            text = str(value or "").strip()
            if allowed and text not in allowed:
                raise ValueError(
                    f"project.json paths.{key}={text!r} 不在声明值域 {allowed}(config.schema project_json.paths.{key}.enum)"
                )
            result[key] = text
        else:
            if value in (None, "") and spec.get("optional") is True:
                # AIPOS-F89 件② M17: 可选落点(项目治理文档, 如卡编年史 / 硬规矩来源)未声明 = None, 消费方按声明的缺省行为处理
                result[key] = None
                result["declared"][key] = False
                continue
            if value in (None, ""):
                from tools.schema_loader import SchemaLoadError

                raise SchemaLoadError(f"config.schema.json project_json.paths.{key} 无 default 且 project.json 未声明")
            path = Path(str(value)).expanduser()
            result[key] = path if path.is_absolute() else root / path
        result["declared"][key] = declared
    return result


# ---------------------------------------------------------------------------
# AIPOS-F78C 件①②: 仓清单声明(project.json repos 段)与「按卡取仓」的唯一解析函数。
# 声明表在 config.schema.json configuration_sources.project_json.schema.repos 一处(形/拒因码);
# code_repo 降为兼容别名(= repos.items[repos.default]); 卡面 lane.repo(card.schema lane)写仓名或绝对路径。
# 凡「按卡取仓」(worktree 落点/ingest 核 tip/finalize --workspace-root/审计卡产品仓/交回判据/claim 上下文/渲染)
# 一律 resolve_card_repo(); 「项目级」用途(注册/结构校验/看板列全部仓)读 project_repos()。禁第二读法。
# ---------------------------------------------------------------------------

REPOS_DECL_CODES = ("REPOS_CONFLICT", "LANE_REPO_UNDECLARED", "INGEST_REPO_MISMATCH")


class CardRepoUnresolved(ValueError):
    """按卡取仓解析失败(fail-closed)。code ∈ config.schema project_json.schema.repos.reject_codes(+ REPO_PATH_MISSING)。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.reason = message


def _project_repos_declaration() -> dict[str, Any]:
    """读 config.schema configuration_sources.project_json.schema.repos(声明缺 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (
        load_schema("config")
        .get("configuration_sources", {})
        .get("project_json", {})
        .get("schema", {})
        .get("repos")
    )
    if not isinstance(decl, dict) or not isinstance(decl.get("schema"), dict) or not decl.get("reject_codes"):
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.repos 未声明")
    return decl


def _same_path(a: Path, b: Path) -> bool:
    a, b = Path(a).expanduser(), Path(b).expanduser()
    if a == b:
        return True
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def project_repos(governance_root: str | Path) -> dict[str, Any]:
    """AIPOS-F78C 件①: 解析项目仓清单(唯一读取口)。

    返回 {declared: bool, default: str|None, items: {仓名: Path}, code_repo: Path|None, project_json_exists: bool}。
    - repos 段缺 = declared False, items 为空; code_repo 单独给出(单仓项目零感知)。
    - repos 段在: default 必在 items 内, items 值必须是非空绝对路径, code_repo(若写)必须 = items[default],
      否则 CardRepoUnresolved(REPOS_CONFLICT)。project.json 读失败 = OSError/ValueError 原样抛(不吞)。
    """
    root = Path(governance_root)
    _project_repos_declaration()  # 声明缺 = SchemaLoadError(fail-closed), 代码不写死第二份形
    exists = project_json_path(root).is_file()
    project = read_project_json(root)
    code_repo_raw = str(project.get("code_repo") or "").strip()
    code_repo = Path(code_repo_raw).expanduser() if code_repo_raw else None
    raw = project.get("repos")
    if raw in (None, {}):
        return {"declared": False, "default": None, "items": {}, "code_repo": code_repo, "project_json_exists": exists}
    where = f"{project_json_path(root)} repos"
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict) or not raw.get("items"):
        raise CardRepoUnresolved("REPOS_CONFLICT", f"{where} 须为 {{default: <仓名>, items: {{<仓名>: <绝对路径>}}}}(config.schema project_json.repos)")
    items: dict[str, Path] = {}
    for name, value in raw["items"].items():
        text = str(value or "").strip()
        if not str(name).strip() or not text or not Path(text).expanduser().is_absolute():
            raise CardRepoUnresolved("REPOS_CONFLICT", f"{where}.items[{name!r}]={value!r} 须为非空绝对路径")
        items[str(name).strip()] = Path(text).expanduser()
    default = str(raw.get("default") or "").strip()
    if default not in items:
        raise CardRepoUnresolved("REPOS_CONFLICT", f"{where}.default={default!r} 不在 items {sorted(items)} 内")
    if code_repo is not None and not _same_path(code_repo, items[default]):
        raise CardRepoUnresolved(
            "REPOS_CONFLICT",
            f"{where}: code_repo={code_repo} ≠ items[{default!r}]={items[default]}(code_repo 是 repos.default 的兼容别名, 须一致或缺省)",
        )
    return {"declared": True, "default": default, "items": items, "code_repo": code_repo, "project_json_exists": exists}


TEST_CONTRACT_KEYS = ("runall_path", "require_tests", "test_file_globs", "post_merge_regression")


def _post_merge_regression_decl(test_contract_decl: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F118 件②: config.schema test_contract.schema.post_merge_regression 声明(键值域 + 缺省)。声明缺/形坏 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError

    decl = (test_contract_decl.get("schema") or {}).get("post_merge_regression")
    keys = (decl or {}).get("schema") if isinstance(decl, dict) else None
    if not isinstance(keys, dict) or set(keys) != {"mode", "execution", "timeout_seconds"}:
        raise SchemaLoadError("config.schema.json test_contract.schema.post_merge_regression.schema 未声明或键不为 mode/execution/timeout_seconds")
    for key in ("mode", "execution"):
        values = keys[key].get("values")
        if not isinstance(values, list) or keys[key].get("default") not in values:
            raise SchemaLoadError(f"config.schema.json post_merge_regression.{key} 的 values/default 未声明或 default 不在 values 内")
    default_timeout = keys["timeout_seconds"].get("default")
    if not isinstance(default_timeout, int) or isinstance(default_timeout, bool) or default_timeout < 1:
        raise SchemaLoadError("config.schema.json post_merge_regression.timeout_seconds.default 未声明或非正整数")
    return keys


def _apply_post_merge_regression(current: dict[str, Any], keys: dict[str, Any], raw: Any, label: str) -> dict[str, Any]:
    """AIPOS-F118 件②: 项目声明逐键覆盖 current(缺省或上层已解析值)。形不合声明 = ValueError(TEST_CONTRACT_INVALID)。"""
    if not isinstance(raw, dict):
        raise ValueError(f"TEST_CONTRACT_INVALID: {label}.post_merge_regression 须为对象(config.schema test_contract.post_merge_regression)")
    unknown = sorted(set(raw) - set(keys))
    if unknown:
        raise ValueError(f"TEST_CONTRACT_INVALID: {label}.post_merge_regression 含未声明键 {unknown}(允许 {sorted(keys)})")
    resolved = dict(current)
    for key in ("mode", "execution"):
        if key in raw:
            if raw[key] not in keys[key]["values"]:
                raise ValueError(f"TEST_CONTRACT_INVALID: {label}.post_merge_regression.{key}={raw[key]!r} 不在声明值域 {keys[key]['values']}")
            resolved[key] = raw[key]
    if "timeout_seconds" in raw:
        value = raw["timeout_seconds"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"TEST_CONTRACT_INVALID: {label}.post_merge_regression.timeout_seconds={value!r} 须为正整数(秒)")
        resolved["timeout_seconds"] = value
    if resolved["mode"] == "block" and resolved["execution"] == "async":
        raise ValueError(
            f"TEST_CONTRACT_INVALID: {label}.post_merge_regression mode=block 不可配 execution=async"
            "(异步结果出来时部署已发生, 无从撤销合并); block 须 sync, 或改 mode=warn"
        )
    resolved["source"] = f"{label}.post_merge_regression"
    return resolved


def project_test_contract(governance_root: str | Path, repo_path: str | Path | None = None) -> dict[str, Any]:
    """AIPOS-F93 件③: 交回检查测试约定的唯一读取口(声明 config.schema project_json.schema.test_contract)。

    返回 {runall_path: str|None, require_tests: bool|None, test_file_globs: list[str], test_file_globs_source: str, source: str}:
    顶层声明, 卡仓(repo_path, 调用方已按卡解析)在 repos.items 内且 test_contract.repos 有该仓覆盖时逐键覆盖。
    runall_path / require_tests 未声明 = None(调用方跳过该判据并 warning)。
    AIPOS-F97 件②: test_file_globs 未声明 = schema 声明的缺省(test_contract.schema.test_file_globs.default; 缺/形坏 = SchemaLoadError,
    代码不写死式样), 项目声明 = 整体替换。
    AIPOS-F118 件②: post_merge_regression = {mode, execution, timeout_seconds, source}(finalize 合并后回归策略; 未声明键取
    config.schema 各键 default, 顶层与 repos 覆盖逐键覆盖)。
    形不合声明 = ValueError("TEST_CONTRACT_INVALID: …")(fail-closed, 调用方拒并给出口); project.json 读失败原样抛。
    """
    from tools.schema_loader import SchemaLoadError, load_schema

    root = Path(governance_root)
    decl = (
        load_schema("config").get("configuration_sources", {}).get("project_json", {}).get("schema", {}).get("test_contract")
    )
    if not isinstance(decl, dict) or not isinstance(decl.get("schema"), dict):
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.test_contract 未声明")
    globs_decl = decl["schema"].get("test_file_globs")
    default_globs = globs_decl.get("default") if isinstance(globs_decl, dict) else None
    if _glob_list_problem(default_globs) is not None:
        raise SchemaLoadError(
            "config.schema.json configuration_sources.project_json.schema.test_contract.schema.test_file_globs.default "
            f"未声明或形坏({_glob_list_problem(default_globs)}); 「测试文件」式样无缺省不可判"
        )
    where = f"{project_json_path(root)} test_contract"
    raw = read_project_json(root).get("test_contract")
    pmr_keys = _post_merge_regression_decl(decl)
    result: dict[str, Any] = {
        "runall_path": None,
        "require_tests": None,
        "test_file_globs": list(default_globs),
        "test_file_globs_source": "config.schema test_contract.test_file_globs.default",
        "post_merge_regression": {
            **{key: pmr_keys[key]["default"] for key in ("mode", "execution", "timeout_seconds")},
            "source": "config.schema test_contract.post_merge_regression 缺省",
        },
        "source": f"{where}(未声明)",
    }
    if raw in (None, {}):
        return result
    if not isinstance(raw, dict):
        raise ValueError(f"TEST_CONTRACT_INVALID: {where} 须为对象(config.schema project_json.test_contract)")

    def _apply(spec: dict[str, Any], label: str) -> None:
        unknown = sorted(set(spec) - set(TEST_CONTRACT_KEYS) - ({"repos"} if label == where else set()))
        if unknown:
            raise ValueError(f"TEST_CONTRACT_INVALID: {label} 含未声明键 {unknown}(允许 {list(TEST_CONTRACT_KEYS)})")
        if "runall_path" in spec:
            text = str(spec.get("runall_path") or "").strip()
            if not text or Path(text).is_absolute() or ".." in Path(text).parts:
                raise ValueError(f"TEST_CONTRACT_INVALID: {label}.runall_path={spec.get('runall_path')!r} 须为相对产品仓根的非空路径")
            result["runall_path"] = text
        if "require_tests" in spec:
            if not isinstance(spec.get("require_tests"), bool):
                raise ValueError(f"TEST_CONTRACT_INVALID: {label}.require_tests={spec.get('require_tests')!r} 须为 true/false")
            result["require_tests"] = spec["require_tests"]
        if "test_file_globs" in spec:
            problem = _glob_list_problem(spec.get("test_file_globs"))
            if problem is not None:
                raise ValueError(f"TEST_CONTRACT_INVALID: {label}.test_file_globs={spec.get('test_file_globs')!r} {problem}")
            result["test_file_globs"] = list(spec["test_file_globs"])
            result["test_file_globs_source"] = f"{label}.test_file_globs"
        if "post_merge_regression" in spec:
            result["post_merge_regression"] = _apply_post_merge_regression(
                result["post_merge_regression"], pmr_keys, spec["post_merge_regression"], label
            )
        result["source"] = label

    _apply(raw, where)
    overrides = raw.get("repos")
    if overrides in (None, {}):
        return result
    if not isinstance(overrides, dict):
        raise ValueError(f"TEST_CONTRACT_INVALID: {where}.repos 须为 {{仓名: {{…}}}}")
    repos = project_repos(root)
    for name, spec in overrides.items():
        if name not in repos["items"]:
            raise ValueError(f"TEST_CONTRACT_INVALID: {where}.repos 键 {name!r} 不在 project.json repos.items {sorted(repos['items'])} 内")
        if not isinstance(spec, dict):
            raise ValueError(f"TEST_CONTRACT_INVALID: {where}.repos[{name!r}] 须为对象")
        if repo_path is not None and _same_path(Path(repo_path), repos["items"][name]):
            _apply(spec, f"{where}.repos[{name!r}]")
    return result


def _glob_list_problem(value: Any) -> str | None:
    """test_file_globs 形校验(声明缺省与项目声明同一规则): 非空列表, 每项非空字符串且不含 /(按文件名匹配)。合规 = None。"""
    if not isinstance(value, list) or not value:
        return "须为非空列表(文件名 glob)"
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return f"项 {item!r} 须为非空字符串"
        if "/" in item:
            return f"项 {item!r} 含 /(式样按文件名匹配, 不含目录)"
    return None


# git diff --name-status 状态字母: 改动后文件仍在卡分支上(A 新增 / M 修改 / R 重命名·取新路径 / C 复制·取新路径 / T 类型变更)
# 与删除(D)。其余字母(U 未合并 / X 未知 / B 损坏配对)在提交间 diff 中不应出现 = 判不了, fail-closed。
_CHANGE_EXISTS_STATUSES = frozenset("AMRCT")
_CHANGE_DELETED_STATUSES = frozenset("D")


def card_test_files(changes: list[tuple[str, str]], contract: dict[str, Any]) -> list[str]:
    """AIPOS-F97 件①: 「本卡测试文件」唯一判据(交回检查 TEST_NOT_IN_RUNALL 与 NO_TESTS 共用, 禁第二实现)。

    changes = 卡分支改动集 [(状态字母, 改动后路径)](board_adapter._card_branch_changed_files(with_status=True), 即
    `git diff --name-status main...card/<ID>`; 重命名/复制已取新路径)。contract = project_test_contract 的返回。
    规则: 删除项(D)排除——删除的文件无法登记, 也不是「本卡测试改动」; 仍存在的文件(A/M/R/C/T)经 is_test_file(文件名
    fnmatchcase 命中 contract["test_file_globs"] 任一式样, 且不是测试清单 runall_path 本身)= 测试文件。保持输入顺序。
    未识别状态 / 式样缺 = ValueError("TEST_FILES_UNRESOLVED: …")(fail-closed, 调用方拒并给出口)。"""
    is_test_file("", contract)  # 式样缺/形坏先拒(空改动集也不放过形坏约定)
    result: list[str] = []
    for status, path in changes:
        letter = str(status or "")[:1]
        if letter in _CHANGE_DELETED_STATUSES:
            continue
        if letter not in _CHANGE_EXISTS_STATUSES:
            raise ValueError(
                f"TEST_FILES_UNRESOLVED: 改动集状态 {status!r}({path}) 不在可判集合 "
                f"{sorted(_CHANGE_EXISTS_STATUSES | _CHANGE_DELETED_STATUSES)} 内"
            )
        if is_test_file(path, contract):
            result.append(path)
    return result


def is_test_file(path: str, contract: dict[str, Any]) -> bool:
    """AIPOS-F109 件④: 「是测试文件」唯一判定(card_test_files 与 run-all 自动发现 discover_test_files 共用, 同一声明
    test_contract.test_file_globs): 文件名(路径最后一段) fnmatchcase 命中任一式样, 且不是测试清单 runall_path 本身。
    式样缺/形坏 = ValueError("TEST_FILES_UNRESOLVED: …")。"""
    import fnmatch

    globs = contract.get("test_file_globs") if isinstance(contract, dict) else None
    problem = _glob_list_problem(globs)
    if problem is not None:
        raise ValueError(f"TEST_FILES_UNRESOLVED: 测试约定缺 test_file_globs({problem}); 须经 workspace_config.project_test_contract 取约定")
    if path == contract.get("runall_path"):
        return False
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in globs)


def discover_test_files(paths: list[str], contract: dict[str, Any]) -> list[str]:
    """AIPOS-F109 件④: run-all 自动发现——产品仓文件清单(相对路径)中「是测试文件」者(is_test_file), 去重、按路径排序。
    声明式样即登记: 命中 test_file_globs 的文件不需在清单里逐个登记(并行卡各加测试不再改同一文件 → 合并零冲突, gap #40)。"""
    return sorted({path for path in paths if is_test_file(path, contract)})


def default_test_contract() -> dict[str, Any]:
    """AIPOS-F109 件④: 不依赖任何治理根的测试约定 = config.schema test_contract 缺省(test_file_globs.default)。
    run-all 在产品仓内自运行(审计工作树/他机)时取此; 项目 project.json 覆盖式样时由调用方给出 project_test_contract。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("config").get("configuration_sources", {}).get("project_json", {}).get("schema", {}).get("test_contract")) or {}
    globs = ((decl.get("schema") or {}).get("test_file_globs") or {}).get("default")
    if _glob_list_problem(globs) is not None:
        raise SchemaLoadError("config.schema.json test_contract.schema.test_file_globs.default 未声明或形坏; 「测试文件」式样无缺省不可判")
    return {
        "runall_path": None,
        "require_tests": None,
        "test_file_globs": list(globs),
        "test_file_globs_source": "config.schema test_contract.test_file_globs.default",
        "source": "config.schema test_contract(缺省)",
    }


def runall_unregistered(test_files: list[str], runall_text: str) -> tuple[list[str], dict[str, Any]]:
    """AIPOS-F109 件④: 「测试文件已登记进测试清单」唯一判据(门交回检查 TEST_NOT_IN_RUNALL 与各夹具自检共用, 禁第二实现)。

    test_files = 已按 is_test_file 判定的测试文件(相对产品仓根)。清单声明 discover(runall_directives)= 命中声明式样即登记,
    只有文件级 exclude 的算未登记; 无 discover = 原判据(完整路径或文件名出现在清单文本中)。
    返回 (未登记的测试文件, 声明行解析结果)。声明行形坏 = ValueError(RUNALL_DIRECTIVE_INVALID)。"""
    directives = runall_directives(runall_text)
    if directives["discover"]:
        return [path for path in test_files if path in directives["exclude"]], directives
    return [path for path in test_files if path not in runall_text and path.rsplit("/", 1)[-1] not in runall_text], directives


def _runall_directive_declaration() -> dict[str, Any]:
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (
        load_schema("config").get("configuration_sources", {}).get("project_json", {}).get("schema", {}).get("test_contract", {})
    ).get("runall_directives")
    if not isinstance(decl, dict) or not str(decl.get("prefix") or "").strip() or not isinstance(decl.get("directives"), dict):
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.test_contract.runall_directives 未声明或形坏")
    return decl


def runall_directives(text: str) -> dict[str, Any]:
    """AIPOS-F109 件④: 测试清单文件(test_contract.runall_path)内声明行的唯一解析(门交回检查与 run-all 执行器共用, 禁第二实现)。

    声明 = config.schema test_contract.runall_directives(行首注释 + 前缀 + 指令词)。返回:
      {discover: bool, exclude: {目标: 理由}, known_failures: {目标: 理由}}
    目标 = 相对产品仓根的文件路径, 或 pytest 节点 `<文件>::<节点>`。
    形坏(未声明的指令词 / 缺目标 / exclude 缺理由 / 重复目标 / discover 带参数)= ValueError("RUNALL_DIRECTIVE_INVALID: …")(fail-closed)。"""
    import re

    decl = _runall_directive_declaration()
    prefix = str(decl["prefix"]).strip()
    allowed = set(decl["directives"])
    line_re = re.compile(r"^\s*#\s*" + re.escape(prefix) + r"\s*(.*)$")
    out: dict[str, Any] = {"discover": False, "exclude": {}, "known_failures": {}}
    for lineno, line in enumerate(str(text).splitlines(), start=1):
        m = line_re.match(line)
        if not m:
            continue
        parts = m.group(1).strip().split(None, 2)
        word = parts[0] if parts else ""
        where = f"第 {lineno} 行 {line.strip()!r}"
        if word not in allowed:
            raise ValueError(f"RUNALL_DIRECTIVE_INVALID: {where} 指令词 {word!r} 未声明(允许 {sorted(allowed)}; config.schema test_contract.runall_directives)")
        if word == "discover":
            if len(parts) > 1:
                raise ValueError(f"RUNALL_DIRECTIVE_INVALID: {where} discover 不带参数")
            out["discover"] = True
            continue
        if len(parts) < 2:
            raise ValueError(f"RUNALL_DIRECTIVE_INVALID: {where} 缺目标(文件路径或 <文件>::<节点>)")
        target = parts[1]
        reason = parts[2].strip() if len(parts) > 2 else ""
        bucket = "exclude" if word == "exclude" else "known_failures"
        if word == "exclude" and not reason:
            raise ValueError(f"RUNALL_DIRECTIVE_INVALID: {where} exclude 须写理由(不执行的测试必须说明为何不执行)")
        if target in out["exclude"] or target in out["known_failures"]:
            raise ValueError(f"RUNALL_DIRECTIVE_INVALID: {where} 目标 {target!r} 重复声明")
        out[bucket][target] = reason
    return out


def default_lane_repo(governance_root: str | Path) -> str:
    """AIPOS-F78C: 卡缺 lane.repo 时的派生值(machine_zone 发布派生与 resolve_card_repo 同源):
    有清单 → repos.default 仓名; 无清单 → code_repo 路径; 缺声明 → 治理根自身(F78 现行)。"""
    repos = project_repos(governance_root)
    if repos["declared"]:
        return str(repos["default"])
    if repos["code_repo"] is not None:
        return str(repos["code_repo"])
    return str(governance_root)


def _match_repo_ref(ref: str, repos: dict[str, Any], governance_root: Path) -> Path | None:
    """仓引用(仓名或绝对路径)→ 清单内路径; 无清单时只允许 code_repo(缺则治理根自身)。匹配不到 = None。"""
    if repos["declared"]:
        if ref in repos["items"]:
            return repos["items"][ref]
        for path in repos["items"].values():
            if _same_path(Path(ref), path):
                return path
        return None
    allowed = repos["code_repo"] if repos["code_repo"] is not None else governance_root
    return allowed if _same_path(Path(ref), allowed) else None


def resolve_card_repo(
    governance_root: str | Path,
    card_frontmatter: dict[str, Any] | None,
    *,
    allow_governance_root: bool = True,
) -> Path:
    """AIPOS-F78C 件②: 按卡取仓的唯一解析函数(卡 frontmatter → 产品仓根 Path)。

    序: 卡 lane.repo(仓名/绝对路径)→ project.json repos 清单 → 路径;
        缺 lane.repo → repos.default → code_repo → 治理根自身(与 F78 lane 派生一致; 但 project.json 已建且无任何仓声明、
        治理根又不是 git 仓 = LANE_REPO_UNDECLARED, 不猜路径)。
    allow_governance_root=False: 最后一级「治理根自身」也不接受(F73D 前置一②: finalize --workspace-root 必须是声明的产品仓,
        禁把治理根当产品仓)= LANE_REPO_UNDECLARED。
    fail-closed: 解析不到清单内一项 = CardRepoUnresolved(LANE_REPO_UNDECLARED); 清单自身不一致 = REPOS_CONFLICT;
        解析到的路径不在盘上 = REPO_PATH_MISSING(出口 lybra project set-repo)。
    """
    root = Path(governance_root)
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    lane = fm.get("lane") if isinstance(fm.get("lane"), dict) else {}
    ref = str(lane.get("repo") or "").strip()
    task_id = str(fm.get("task_id") or "<card>")
    repos = project_repos(root)
    if ref:
        matched = _match_repo_ref(ref, repos, root)
        if matched is None:
            if repos["declared"]:
                exit_text = f"出口: 卡 lane.repo 改为 repos.items 内仓名 {sorted(repos['items'])} 之一, 或在 project.json repos.items 声明该仓"
            else:
                allowed = repos["code_repo"] if repos["code_repo"] is not None else root
                exit_text = (f"project.json 无 repos 清单时 lane.repo 只允许等于 code_repo({allowed})或缺省。"
                             f"出口: 多仓项目在 project.json 声明 repos {{default, items}} 清单, 卡 lane.repo 写仓名(一卡一仓)")
            raise CardRepoUnresolved("LANE_REPO_UNDECLARED", f"卡 {task_id} lane.repo={ref!r} 不在项目仓清单内。{exit_text}")
        path = matched
    elif repos["declared"]:
        path = repos["items"][repos["default"]]
    elif repos["code_repo"] is not None:
        path = repos["code_repo"]
    elif allow_governance_root and (not repos["project_json_exists"] or (root / ".git").exists()):
        path = root  # 未注册的靶场根 / 治理根自身即产品仓(单根)
    elif not allow_governance_root:
        raise CardRepoUnresolved(
            "LANE_REPO_UNDECLARED",
            f"卡 {task_id} 无 lane.repo 且 project.json 无 repos/code_repo 声明, 治理根 {root} 不作产品仓(禁把治理根当产品仓)。"
            "出口: `lybra project set-repo <name> --code-repo <path>`(单仓)或在 project.json 声明 repos 清单(多仓)",
        )
    else:
        raise CardRepoUnresolved(
            "LANE_REPO_UNDECLARED",
            f"卡 {task_id} 无 lane.repo, project.json 无 repos/code_repo 声明, 治理根 {root} 亦不是 git 仓, 不猜路径。"
            "出口: `lybra project set-repo <name> --code-repo <path>`(单仓)或在 project.json 声明 repos 清单(多仓)",
        )
    if not path.is_dir():
        raise CardRepoUnresolved(
            "REPO_PATH_MISSING",
            f"卡 {task_id} 解析到的产品仓 {path} 不在盘上。出口: `lybra project set-repo <name> --code-repo <path>` 或修正 project.json repos.items",
        )
    return path


# ---------------------------------------------------------------------------
# AIPOS-F88 件③(承接 F65B): 根路径语义分域——两个命名函数各一处实现, 全仓「去哪找治理工作区 / 产品仓」只经此二者
# (home 根 = resolve_home_root, 既有, 非第三个根概念)。禁写死任何机器路径, 禁按某项目目录布局回退。
# ---------------------------------------------------------------------------


def _declared_root_from_connection(start: Path | None) -> Path | None:
    """自 start(缺省 cwd)向上首个 .lybra/connection.json 的治理根声明: governance_root, 缺则 workspace_root; 都缺 = None。
    connection.json 不可读/非 JSON = ValueError(fail-closed, 声明坏了不猜)。"""
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    from tools.aipos_cli.service_mode import connection_path  # AIPOS-F106 件④: connection.json 定位唯一实现(惰性导入防环)

    for candidate in [current, *current.parents]:
        conn = connection_path(candidate)
        if not conn.is_file():
            continue
        data = load_workspace_config(conn)
        declared = str(data.get("governance_root") or data.get("workspace_root") or "").strip()
        return Path(declared).expanduser() if declared else None
    return None


def governance_workspace_root(
    explicit: str | Path | None = None,
    *,
    start: Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """AIPOS-F88 件③: 「治理工作区根」唯一命名入口(队列 / 卡 / 记录 / 信封 / project.json 所在根)。

    序(每级都以唯一结构判据 has_workspace_queue 验证, 不看路径名; 禁按任何项目布局回退):
      1. explicit: CLI --workspace-root / --governance-root / --repo-root, 或调用方转交的 env 值(向后兼容: 显式永远最高)
      2. 声明: 自 start(缺省 cwd)向上首个 .lybra/connection.json 的 governance_root(缺则 workspace_root)
      3. 结构识别: resolve_workspace_root(AIPOS-226 唯一优先级梯: LYBRA_WORKSPACE_ROOT / LYBRA_HOME_ROOT /
         in-workspace config / 向上队列结构 / 全局 ~/.lybra/config.json home_root + active_project)
    显式或声明指向非治理工作区 = FileNotFoundError(声明错了不猜); 全不可解析 = FileNotFoundError(带出口)。
    """
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not has_workspace_queue(root):
            raise FileNotFoundError(f"显式治理根 {root} 不是治理工作区(无声明的队列根); 出口: 传入项目治理根(含 project.json 与队列)")
        return root
    declared = _declared_root_from_connection(start)
    if declared is not None:
        root = declared.resolve()
        if not has_workspace_queue(root):
            raise FileNotFoundError(f".lybra/connection.json 声明的治理根 {root} 不是治理工作区(无声明的队列根); 出口: 修正 connection.json#governance_root")
        return root
    try:
        return resolve_workspace_root(start, env=env)
    except (FileNotFoundError, ValueError) as exc:
        raise FileNotFoundError(
            f"治理工作区根不可解析(无显式参数 / 无 connection.json 声明 / 结构识别失败: {exc}); "
            "出口: 传 --workspace-root <治理根>, 或在工位 .lybra/connection.json 声明 governance_root"
        ) from exc


def product_repo_root(
    governance_root: str | Path | None = None,
    card_frontmatter: dict[str, Any] | None = None,
    *,
    allow_governance_root: bool = True,
) -> Path:
    """AIPOS-F88 件③: 「产品仓根」唯一命名入口。两种来源按是否给治理根区分, 均为既有单源(禁第二实现):
      - 给治理根 = 该项目(该卡)声明的产品仓: resolve_card_repo(卡 lane.repo → project.json repos → code_repo → 单根靶场);
        git / 工作树 / 产物所在。解析不到 = CardRepoUnresolved(fail-closed)。
      - 不给治理根 = 运行中 Lybra 代码所在仓: schema_loader.code_repo_schema_root()(schema/ 所在; 声明类读取用)。
    禁写死任何机器路径(原写死的产品仓机器路径缺省全部退役)。"""
    if governance_root is None:
        from tools.schema_loader import code_repo_schema_root

        return code_repo_schema_root()
    return resolve_card_repo(governance_root, card_frontmatter or {}, allow_governance_root=allow_governance_root)


def write_project_json(
    project_root: str | Path,
    name: str,
    *,
    code_repo: str | Path | None = None,
    registered_by: str = "owner",
    registered_at: str | None = None,
    preserve_registered_at: bool = True,
    collaboration_profile: dict[str, Any] | None = None,
    preserve_collaboration_profile: bool = True,
) -> Path:
    """Write <project_root>/project.json with provenance (M3).

    Schema: {project, code_repo, registered_at, registered_by, config_version:1,
    collaboration_profile} (sorted, 2-indent, trailing newline). `code_repo` is stored as an
    expanded absolute-ish string or null. When `preserve_registered_at` and an existing
    project.json already carries a `registered_at`, it is kept (so set-repo never clobbers the
    original creation provenance).
    
    AIPOS-335: collaboration_profile is optional. When preserve_collaboration_profile=True
    (default) and an existing project.json has collaboration_profile, it is preserved unless
    an explicit new value is passed. When collaboration_profile=None and no existing value,
    the field is omitted (backward compatible: old projects stay unchanged).
    """
    root = Path(project_root)
    path = project_json_path(root)

    existing = read_project_json(root) if (preserve_registered_at or preserve_collaboration_profile) else {}
    
    if preserve_registered_at:
        prior = str(existing.get("registered_at") or "").strip()
        if prior:
            registered_at = prior

    repo_value = str(Path(code_repo).expanduser()) if code_repo else None
    payload = {
        "project": str(name).strip(),
        "code_repo": repo_value,
        "registered_at": registered_at or iso_z(),
        "registered_by": registered_by,
        "config_version": 1,
    }
    
    # AIPOS-335: collaboration_profile 向后兼容处理
    # 优先级：显式传入 > 保留旧值 > 不写入（老项目维持原状）
    if collaboration_profile is not None:
        payload["collaboration_profile"] = collaboration_profile
    elif preserve_collaboration_profile and "collaboration_profile" in existing:
        payload["collaboration_profile"] = existing["collaboration_profile"]
    # else: 不写入 collaboration_profile（老项目维持无此字段状态）
    
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_connection_skeleton(workspace_root: Path, rpc_url: str) -> None:
    """AIPOS-F24 大项D: Write connection.json skeleton with mcp.rpc_url.
    
    Minimal skeleton for project initialization (before lybra serve). Contains:
    - config_version, mode, workspace_root
    - mcp.rpc_url (from gate's own config)
    - empty tokens list (populated by lybra serve or enroll)
    
    This allows project advisors to use gate verbs immediately after project creation.
    """
    local_dir = workspace_root / ".lybra"
    local_dir.mkdir(parents=True, exist_ok=True)
    
    connection_file = local_dir / "connection.json"
    skeleton = {
        "config_version": 1,
        "mode": "service_v0",
        "workspace_root": str(workspace_root),
        "local_only": True,
        "created_at": utc_now().isoformat(),
        "mcp": {
            "rpc_url": rpc_url
        },
        "tokens": [],
        "secrets_notice": "Raw role tokens are local secrets. Anyone who can read this file can use the listed local role scopes."
    }
    
    connection_file.write_text(json.dumps(skeleton, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def scaffold_project(
    home_root: str | Path,
    name: str,
    *,
    code_repo: str | Path | None = None,
    registered_by: str = "owner",
    collaboration_profile: dict[str, Any] | None = None,
    gate_rpc_url: str | None = None,
) -> Path:
    """Owner scaffold of a fresh per-project truth root under the home.

    Creates the full project tree (queue 4 states, records/drafts/orchestration, governance/,
    stage_archive/, workspace_artifacts/), a single-file governance/decision_log.md (ruling
    1=B) stub if absent, and project.json. Refuses to overwrite a non-empty existing root
    (teaching error). Directory shape is sourced from governance_paths() so there is one
    definition.
    
    AIPOS-335: collaboration_profile is optional. When provided, it will be written to
    project.json; when None, project.json will not have this field (for backward compatibility).
    
    AIPOS-F24 (大项D): gate_rpc_url is optional. When provided (from gate verb), writes a
    connection.json skeleton with mcp.rpc_url. CLI callers omit this (backward compat).
    """
    clean = str(name).strip()
    if not clean:
        raise ValueError("PROJECT_NAME_EMPTY: project name must be non-empty")
    root = project_root_for(home_root, clean)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"PROJECT_EXISTS: project root not empty: {root}")

    # AIPOS-F89 件① M8: 队列根 = 项目声明 paths.queue_root(新项目尚无 project.json = 声明 default), 唯一读取口 project_paths
    queue_root = Path(project_paths(root)["queue_root"])
    from tools.aipos_cli.task_loader import QUEUE_SKELETON_STATES

    for state in QUEUE_SKELETON_STATES:
        (queue_root / state).mkdir(parents=True, exist_ok=True)
    for sub in ("records", "drafts", "orchestration"):
        (root / "5_tasks" / sub).mkdir(parents=True, exist_ok=True)
    (root / "governance").mkdir(parents=True, exist_ok=True)

    paths = governance_paths(root)
    paths["stage_archive"].mkdir(parents=True, exist_ok=True)
    paths["workspace_artifacts"].mkdir(parents=True, exist_ok=True)

    decision_log = paths["decision_log"]  # ruling 1=B: single file
    if not decision_log.exists():
        # AIPOS-F94 件②: 治理文档经声明驱动的唯一写入口 governance add doc 写(带 file_declarations.governance_doc 必填
        # frontmatter)——原裸写无 frontmatter 的桩被治理仓提交门 B② 拒, 向导第 1 步之后的落账(governance-commit --paths)必挡。
        from tools.aipos_cli.governance_add import add_doc

        written = add_doc(root, name=decision_log.stem, title=f"{clean} Decision Log",
                          body="决策粒度条目见 decision_log 目录(lybra governance add decision)。")
        if not written.get("ok") or Path(written["target_path"]) != decision_log:
            raise RuntimeError(f"DECISION_LOG_WRITE_FAILED: {written.get('error') or written.get('message')} "
                               f"(声明落点 {written.get('target_path')} ≠ {decision_log})")

    write_project_json(root, clean, code_repo=code_repo, registered_by=registered_by, collaboration_profile=collaboration_profile)

    # AIPOS-F92 件③: 首份阶段快照「项目创建」经既有单源 governance add stage(config.schema file_declarations.stage_archive_snapshot)
    # 写入 —— 阶段门判据不变(有快照才可转换), 新项目建成即满足(否则首次 finalize 必 BLOCK「no stage snapshot」, F89 N2)。
    write_project_created_snapshot(root, clean, registered_by=registered_by)

    # AIPOS-F24 大项D: connection.json 骨架(含 mcp.rpc_url)
    if gate_rpc_url:
        _write_connection_skeleton(root, gate_rpc_url)

    return root


#: AIPOS-F92 件③: 新项目首份阶段快照的阶段名(project new 建项目即写; 阶段门 finalize.check_stage_archive_gate 判据不变)
PROJECT_CREATED_STAGE_NAME = "项目创建"


def write_project_created_snapshot(project_root: str | Path, name: str, *, registered_by: str) -> Path:
    """AIPOS-F92 件③: 经 governance_add.add_stage(唯一写入口, 声明驱动)写首份阶段快照「项目创建」。

    失败 = 抛 RuntimeError(fail-closed: 无快照的新项目首次 finalize 必被阶段门拦, 不静默放过)。返回快照路径。
    """
    from tools.aipos_cli.governance_add import add_stage

    root = Path(project_root)
    body = "\n".join([
        "## Summary",
        "",
        f"项目 `{name}` 由 `lybra project new` 创建(registered_by: {registered_by})。治理树(队列 / 记录 / 治理文档 / 阶段快照)已就位,",
        "本篇是项目的第一份阶段快照, 标记「项目创建」阶段关账: 此后的卡可经 finalize 阶段门(有快照才可转换)。",
        "",
        "## Next Stage",
        "",
        "按 `lybra onboarding guide` 接入: 声明产品仓 → 顾问凭据 → 信封 → 工位 → 首卡。",
    ])
    result = add_stage(root, stage_name=PROJECT_CREATED_STAGE_NAME, body=body)
    if not result.get("ok"):
        raise RuntimeError(f"STAGE_SNAPSHOT_WRITE_FAILED: {result.get('error') or result.get('message')}")
    return Path(result["target_path"])


# ---------------------------------------------------------------------------
# AIPOS-F123 件②: project.json 唯一写路径(set-repos / set-workstation / set-paths 共用; 禁第二份写实现)。
# 排他锁 = fcntl.flock 加在 project.json 自身(inode 不变: 原位改写, 不用 rename, 并发写方排队而非各写各的);
# 写后可选复核(读取口)不过 = 还原原文并原样上抛; dry_run = 只算 diff 零写入。
# ---------------------------------------------------------------------------

def update_project_json(
    project_root: str | Path,
    mutate: Any,
    *,
    verify: Any = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """读-改-写 project.json 的唯一实现。mutate(data) 原地修改 JSON 对象(可抛声明错误 = 不写);
    verify(project_root) 在写后以产品读取口复核(抛 = 还原原文后上抛)。

    返回 {project_json, changed, written, diff(unified diff 文本, 无改动为空串)}。
    项目未建(无 project.json)= FileNotFoundError; 文件不是 JSON 对象 = ValueError。"""
    import difflib
    import fcntl

    path = project_json_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(f"{path} 不存在(项目未建; 先 lybra project new)")
    with open(path, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            original = handle.read()
            data = json.loads(original)
            if not isinstance(data, dict):
                raise ValueError(f"{path} 不是 JSON 对象")
            mutate(data)
            rendered = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            diff = "".join(difflib.unified_diff(
                original.splitlines(keepends=True), rendered.splitlines(keepends=True),
                fromfile=f"{path} (当前)", tofile=f"{path} (写入后)"))
            result = {"project_json": str(path), "changed": rendered != original, "written": False, "diff": diff}
            if dry_run or rendered == original:
                return result

            def _rewrite(text: str) -> None:
                handle.seek(0)
                handle.truncate()
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

            _rewrite(rendered)
            if verify is not None:
                try:
                    verify(Path(project_root))
                except BaseException:
                    _rewrite(original)  # 复核不过: 还原原文(不留半成品), 原异常上抛
                    raise
            result["written"] = True
            return result
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class PathsDeclarationError(ValueError):
    """`lybra project set-paths` 的键/值不合 config.schema project_json.paths 声明(fail-closed, project.json 不动)。
    code ∈ config.schema project_json.paths.reject_codes。problems = 全部问题(一次列全)。"""

    def __init__(self, code: str, problems: list[str]):
        super().__init__(f"{code}: " + "; ".join(problems))
        self.code = code
        self.problems = list(problems)


def _paths_reject_codes() -> dict[str, str]:
    from tools.schema_loader import SchemaLoadError, load_schema

    codes = ((((load_schema("config").get("configuration_sources") or {}).get("project_json") or {}).get("schema") or {})
             .get("paths") or {}).get("reject_codes")
    if not isinstance(codes, dict) or not {"PATHS_KEY_UNKNOWN", "PATHS_VALUE_INVALID"} <= set(codes):
        raise SchemaLoadError("config.schema.json project_json.paths.reject_codes(PATHS_KEY_UNKNOWN / PATHS_VALUE_INVALID)未声明")
    return codes


def adoption_paths_declaration() -> dict[str, Any]:
    """AIPOS-F124 件②: config.schema project_json.paths.adoption(收编前须显式声明的落点键 + 拒因 ADOPT_PATHS_UNDECLARED 文案)。
    键须是 paths.schema 声明的键; 缺 / 形不合 = SchemaLoadError(fail-closed)。读取方 queue_mutation.adoption_paths_refusal。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((((load_schema("config").get("configuration_sources") or {}).get("project_json") or {}).get("schema") or {})
            .get("paths") or {}).get("adoption")
    keys = decl.get("required_declared_keys") if isinstance(decl, dict) else None
    guard = ((decl.get("guards") or {}).get("ADOPT_PATHS_UNDECLARED") if isinstance(decl, dict) else None)
    known = _project_paths_declaration()
    if (not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k in known for k in keys)
            or not isinstance(guard, dict) or not guard.get("error_message") or not guard.get("next_step")):
        raise SchemaLoadError("config.schema.json project_json.paths.adoption(required_declared_keys ⊆ paths.schema / "
                              "guards.ADOPT_PATHS_UNDECLARED{error_message, next_step})未声明")
    return decl


def _coerce_paths_value(key: str, raw: str, spec: dict[str, Any]) -> tuple[Any, str | None]:
    """按声明把 CLI 串值转为 project.json 值: boolean 只认 true/false; enum 须在值域; 其余为非空单行串。返回 (值, 问题|None)。"""
    text = str(raw if raw is not None else "")
    kind = str(spec.get("type") or "string")
    if kind == "boolean":
        lowered = text.strip().lower()
        if lowered not in ("true", "false"):
            return None, f"{key}={text!r} 须为 true 或 false(声明 type=boolean)"
        return lowered == "true", None
    if not text.strip() or "\n" in text or "\r" in text:
        return None, f"{key} 的值须为非空单行串, 得到 {text!r}"
    enum = [str(v) for v in (spec.get("enum") or [])]
    if enum and text.strip() not in enum:
        return None, f"{key}={text.strip()!r} 不在声明值域 {enum}"
    return text.strip(), None


def set_project_paths(project_root: str | Path, pairs: list[tuple[str, str]], *, dry_run: bool = True) -> dict[str, Any]:
    """AIPOS-F123 件②: `lybra project set-paths` 的唯一实现——写 project.json paths.<键>(可多对, 其余键原样保留)。

    校验 = config.schema project_json.paths.schema 声明(未知键 PATHS_KEY_UNKNOWN; 值不合 PATHS_VALUE_INVALID; 问题一次列全,
    任一不合 = 整批不写)。写入经 update_project_json(与 set-repos 同一写路径与锁), 写后经唯一读取口 project_paths 复核。
    dry_run(缺省)= 只给 diff 零写入。返回 {project_json, dry_run, written, changes:[{key, before, after}], diff}。"""
    codes = _paths_reject_codes()
    decl = _project_paths_declaration()
    if not pairs:
        raise PathsDeclarationError("PATHS_VALUE_INVALID", ["至少给一对 --key/--value"])
    unknown = [str(k) for k, _ in pairs if str(k) not in decl]
    if unknown:
        raise PathsDeclarationError("PATHS_KEY_UNKNOWN", [
            f"{k}: {codes['PATHS_KEY_UNKNOWN']}(可写键: {', '.join(sorted(decl))})" for k in unknown])
    problems: list[str] = []
    values: dict[str, Any] = {}
    for key, raw in pairs:
        if key in values:
            problems.append(f"{key} 重复给出")
            continue
        value, problem = _coerce_paths_value(key, raw, decl[key] if isinstance(decl[key], dict) else {})
        if problem:
            problems.append(problem)
        else:
            values[key] = value
    if problems:
        raise PathsDeclarationError("PATHS_VALUE_INVALID", problems)
    path = project_json_path(project_root)
    changes: list[dict[str, Any]] = []

    def _mutate(data: dict[str, Any]) -> None:
        current = data.get("paths") if data.get("paths") is not None else {}
        if not isinstance(current, dict):
            raise PathsDeclarationError("PATHS_VALUE_INVALID", [f"{path} 现有 paths 段不是 JSON 对象, 先人工核对"])
        for key, value in values.items():
            changes.append({"key": key, "before": current.get(key), "after": value})
            current[key] = value
        data["paths"] = current

    outcome = update_project_json(project_root, _mutate, verify=project_paths, dry_run=dry_run)
    return {"project_json": str(path), "dry_run": dry_run, "written": outcome["written"], "changed": outcome["changed"],
            "changes": changes, "diff": outcome["diff"]}


def set_project_repos(
    home_root: str | Path,
    name: str,
    items: dict[str, str | Path],
    *,
    default: str,
) -> dict[str, Any]:
    """AIPOS-F92 件②: 声明项目产品仓 —— project.json `repos {default, items}` + `code_repo`(= items[default] 兼容别名)。

    项目须已建(resolve_project_root, 无 lazy-create)。其余 project.json 键原样保留(只改 repos / code_repo)。
    校验 = 唯一读取口 project_repos()(config.schema project_json.repos 声明: 绝对路径 / default ∈ items / code_repo 一致);
    写后校验不过 = 原文件还原 + 抛 CardRepoUnresolved(REPOS_CONFLICT), 不留半成品。返回 project_repos() 结果 + project_json 路径。
    """
    root = resolve_project_root(home_root, name)
    clean_items = {str(k).strip(): str(Path(str(v).strip()).expanduser()) for k, v in items.items()}

    def _mutate(data: dict[str, Any]) -> None:
        data["repos"] = {"default": str(default).strip(), "items": clean_items}
        data["code_repo"] = clean_items.get(str(default).strip())

    # AIPOS-F123 件②: 写入经 project.json 唯一写路径(锁 + 写后经读取口 project_repos 复核, 不合 = 还原原文件)
    update_project_json(root, _mutate, verify=project_repos)
    return {**project_repos(root), "project_json": project_json_path(root)}


def set_project_repo(
    home_root: str | Path,
    name: str,
    code_repo: str | Path,
    *,
    registered_by: str = "owner",
) -> Path:
    """Update an established project's code_repo mapping, preserving registered_at.

    The project must already exist; otherwise resolve_project_root's PROJECT_NOT_ESTABLISHED
    propagates (no lazy-create — ruling 2=a).
    """
    root = resolve_project_root(home_root, name)
    write_project_json(root, name, code_repo=code_repo, registered_by=registered_by)
    return root


# ---------------------------------------------------------------------------
# AIPOS-F110 件②: 跨机工位开工材料声明(project.json workstations.<实例>; 声明表 config.schema
# configuration_sources.project_json.schema.workstations)。唯一读取口 project_workstation / 唯一写入口 set_project_workstation。
# ---------------------------------------------------------------------------

class WorkstationDeclarationError(ValueError):
    """跨机工位材料声明不可用(fail-closed)。code ∈ config.schema project_json.schema.workstations.reject_codes。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.reason = message


def _workstations_declaration() -> dict[str, Any]:
    """config.schema project_json.schema.workstations(键表 + 拒因码)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("config").get("configuration_sources") or {}).get("project_json") or {}).get("schema") or {}).get("workstations")
    if not isinstance(decl, dict) or not isinstance(decl.get("schema"), dict) or not isinstance(decl.get("reject_codes"), dict):
        raise SchemaLoadError("config.schema.json configuration_sources.project_json.schema.workstations(schema / reject_codes)未声明")
    return decl


def _validate_workstation_entry(instance: str, entry: Any, decl: dict[str, Any]) -> dict[str, str]:
    from tools.aipos_cli.harness_launch import redact_progress

    if not isinstance(entry, dict):
        raise WorkstationDeclarationError("WORKSTATION_MATERIAL_INVALID", f"workstations.{instance} 须为 JSON 对象, 得到 {type(entry).__name__}")
    out: dict[str, str] = {}
    for key, spec in decl["schema"].items():
        if not (isinstance(spec, dict) and spec.get("required")):
            continue
        value = entry.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise WorkstationDeclarationError("WORKSTATION_MATERIAL_UNDECLARED", f"workstations.{instance}.{key} 未声明")
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise WorkstationDeclarationError("WORKSTATION_MATERIAL_INVALID", f"workstations.{instance}.{key} 须为非空单行串")
        if redact_progress(value) != value:  # 凭据判据与进度打码同一实现(凭据字样 / 长不透明串)
            raise WorkstationDeclarationError("WORKSTATION_MATERIAL_INVALID",
                                              f"workstations.{instance}.{key} 含凭据字样或长不透明串(开工提示禁含凭据)")
        out[key] = value.strip()
    return out


def project_workstation(project_root: str | Path, instance: str) -> dict[str, str]:
    """跨机工位 instance 的开工材料声明 {gate_ssh_alias, material_access}。未声明/形不合 = WorkstationDeclarationError;
    project.json 不可读 = 原异常上抛(OSError / ValueError, 不吞成未声明)。只读。"""
    decl = _workstations_declaration()
    data = read_project_json(project_root)
    workstations = data.get("workstations")
    if workstations is None:
        raise WorkstationDeclarationError("WORKSTATION_MATERIAL_UNDECLARED",
                                          f"{project_json_path(project_root)} 无 workstations 段(实例 {instance} 未声明)")
    if not isinstance(workstations, dict):
        raise WorkstationDeclarationError("WORKSTATION_MATERIAL_INVALID", "project.json workstations 须为 JSON 对象(键 = 实例名)")
    if instance not in workstations:
        raise WorkstationDeclarationError("WORKSTATION_MATERIAL_UNDECLARED",
                                          f"{project_json_path(project_root)} workstations 下无实例 {instance}")
    return _validate_workstation_entry(instance, workstations[instance], decl)


def set_project_workstation(project_root: str | Path, instance: str, *, gate_ssh_alias: str, material_access: str) -> dict[str, Any]:
    """写 project.json workstations.<instance>(其余键原样保留); 写前按声明校验(不合 = 不写, 抛 WorkstationDeclarationError)。
    返回 {project_json, instance, 声明值}。"""
    decl = _workstations_declaration()
    instance = str(instance or "").strip()
    if not instance or any(c.isspace() for c in instance):
        raise WorkstationDeclarationError("WORKSTATION_MATERIAL_INVALID", f"实例名须为非空且不含空白: {instance!r}")
    entry = _validate_workstation_entry(instance, {"gate_ssh_alias": gate_ssh_alias, "material_access": material_access}, decl)
    path = project_json_path(project_root)

    def _mutate(data: dict[str, Any]) -> None:
        workstations = data.get("workstations") if data.get("workstations") is not None else {}
        if not isinstance(workstations, dict):
            raise WorkstationDeclarationError("WORKSTATION_MATERIAL_INVALID", f"{path} workstations 须为 JSON 对象")
        workstations[instance] = entry
        data["workstations"] = workstations

    # AIPOS-F123 件②: 写入经 project.json 唯一写路径(与 set-repos / set-paths 同一把锁)
    update_project_json(project_root, _mutate)
    return {"project_json": str(path), "instance": instance, **entry}


# ---------------------------------------------------------------------------
# AIPOS-F122 件①: 存量冻结清单落点声明(project.json legacy_baseline; 声明表 config.schema
# configuration_sources.project_json.schema.legacy_baseline)。唯一读取口 project_legacy_baseline / 唯一写入口 set_project_legacy_baseline。
# ---------------------------------------------------------------------------

def project_legacy_baseline(governance_root: str | Path) -> dict[str, Path] | None:
    """project.json legacy_baseline 段 → {manifest_dir: 绝对 Path}; 缺段 = None(无冻结, 行为不变)。
    形不合(非对象 / manifest_dir 空或非串 / 解析后不在治理根内)或 project.json 读不出 = LegacyBaselineError(LEGACY_BASELINE_INVALID;
    读不出不吞成「未声明」——冻结判定不可得即 fail-closed)。只读。"""
    from tools.aipos_cli.legacy_baseline import LEGACY_BASELINE_INVALID, LegacyBaselineError, declaration

    declaration()  # 声明缺 = SchemaLoadError(fail-closed)
    root = Path(governance_root)
    try:
        project = read_project_json(root)
    except (OSError, ValueError) as exc:
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{project_json_path(root)} 读不出, 冻结判定不可得: {exc}") from exc
    raw = project.get("legacy_baseline") if isinstance(project, dict) else None
    if raw is None:
        return None
    where = f"{project_json_path(root)} legacy_baseline"
    if not isinstance(raw, dict):
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{where} 须为 JSON 对象 {{manifest_dir: <相对治理根路径>}}")
    value = raw.get("manifest_dir")
    if not isinstance(value, str) or not value.strip():
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{where}.manifest_dir 须为非空串")
    path = Path(value.strip()).expanduser()
    path = path if path.is_absolute() else root / path
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise LegacyBaselineError(LEGACY_BASELINE_INVALID, f"{where}.manifest_dir={value!r} 不在治理根 {root} 内") from exc
    return {"manifest_dir": path}


def set_project_legacy_baseline(governance_root: str | Path, manifest_dir: str) -> dict[str, Any]:
    """写 project.json legacy_baseline.manifest_dir(其余键原样保留); 写后经 project_legacy_baseline 校验, 不合 = 还原原文并抛。"""
    root = Path(governance_root)
    path = project_json_path(root)
    if not path.is_file():
        raise FileNotFoundError(f"{path} 不存在(项目未建)")
    original = path.read_text(encoding="utf-8")
    data = json.loads(original)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 不是 JSON 对象")
    data["legacy_baseline"] = {"manifest_dir": str(manifest_dir)}
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        declared = project_legacy_baseline(root)
    except ValueError:
        path.write_text(original, encoding="utf-8")
        raise
    return {"project_json": str(path), "manifest_dir": str(declared["manifest_dir"]) if declared else None}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
