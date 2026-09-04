from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# AIPOS-R4B-1: 端口默认值的权威来源是 schema/config.schema.json(board_default=7117,
# mcp_server_default=7118),经 tools/schema_loader.py:get_config_port() 读取。
# workspace_config 在 CLI 导入链早期被导入,为避免 editable-install 环境下 namespace
# package 'tools' 无法解析顶层模块 'schema_loader' 导致 ModuleNotFoundError(见 AUDIT-R4B-1
# F-R4B1-1),此处保留常量镜像,与 config.schema 值保持同步。
# 调用方应优先直接调用 schema_loader.get_config_port() 读单一源;仅在 workspace_config
# 场景(如 default_workspace_config 生成)使用此常量。loader 仍唯一,config.schema 仍是单一源。
CONFIG_RELATIVE_PATH = Path(".lybra") / "config.json"
DEFAULT_BOARD_HOST = "127.0.0.1"
DEFAULT_BOARD_PORT = 7117  # config.schema board_default (镜像,非权威)
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 7118    # config.schema mcp_server_default (镜像,非权威)

# AIPOS-224 (governance home, Slice 0): home-root + active-project resolution.
# This block is ADDITIVE and UNWIRED — no existing resolver/caller behaviour changes in this
# slice. `default_workspace_config()` intentionally still emits config_version 1 (M1: read v2
# now, default-write v2 deferred to Slice 2). Pure functions, fail-closed, stdlib only.
DEFAULT_HOME_ROOT = Path("~/.lybra/projects")
HOME_ROOT_ENV = "LYBRA_HOME_ROOT"
ACTIVE_PROJECT_ENV = "LYBRA_ACTIVE_PROJECT"
LEGACY_WORKSPACE_ROOT_ENV = "AIPOS_WORKSPACE_ROOT"

# AIPOS-226 (Slice 2): the global Lybra runtime root. Lybra's own runtime state (the
# runtime config that points at the truth home + names the active project, and the role
# tokens) lives here so it NEVER enters a user truth repo. No secrets in config.json.
GLOBAL_LYBRA_DIR = Path("~/.lybra")
GLOBAL_CONFIG_REL = Path("config.json")


def has_workspace_queue(path: Path) -> bool:
    return (path / "5_tasks" / "queue").exists()


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

    raw_env_root = str(source_env.get("AIPOS_WORKSPACE_ROOT") or "").strip()
    if raw_env_root:
        return _validate_workspace_root(Path(raw_env_root), source="AIPOS_WORKSPACE_ROOT"), None

    # ---------------------------------------------------------------------------------
    # AIPOS-226 resolution precedence (AIPOS-223 §1.4, highest first). The two-root home
    # model is folded in WITHOUT displacing the documented back-compat order — a LOCAL
    # workspace signal (an in-workspace .lybra/config.json, then a bare 5_tasks/queue
    # subtree at/above the start) wins over the GLOBAL ~/.lybra/config.json home model.
    #
    #   1. --workspace-root / explicit_root           (handled above)
    #   2. AIPOS_WORKSPACE_ROOT env                    (handled above)
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


def default_workspace_config(workspace_root: Path) -> dict[str, Any]:
    return {
        "config_version": 1,
        "workspace_root": ".",
        "board": {"host": DEFAULT_BOARD_HOST, "port": DEFAULT_BOARD_PORT},
        "mcp": {
            "host": DEFAULT_MCP_HOST,
            "port": DEFAULT_MCP_PORT,
            "transport_token_env": "LYBRA_MCP_TOKEN",
            "capability_token_env": "LYBRA_CAPABILITY_TOKEN",
        },
        "notes": "Token values are referenced by environment variable only; do not store raw secrets in this file.",
    }


def write_workspace_config(workspace_root: Path, *, overwrite: bool = False) -> Path:
    root = workspace_root.expanduser().resolve()
    config_path = root / CONFIG_RELATIVE_PATH
    if config_path.exists() and not overwrite:
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(default_workspace_config(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path


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
        if child.is_dir() and has_workspace_queue(child) and (child / "project.json").exists()
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
    source_env = env if env is not None else os.environ
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()

    raw_home = str(source_env.get(HOME_ROOT_ENV) or "").strip()
    if raw_home:
        return Path(raw_home).expanduser().resolve()

    configured = global_config_home_root(load_global_config(source_env))
    if configured is not None:
        return configured.expanduser().resolve()

    # Default ~/.lybra/projects. Honor a patched HOME in `env` (consistent with
    # global_config_path) so callers/tests can isolate from the real home.
    if env is not None:
        home = str(env.get("HOME") or "").strip()
        if home:
            return Path(home) / ".lybra" / "projects"
    return DEFAULT_HOME_ROOT.expanduser()


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
    if not has_workspace_queue(root) or not (root / "project.json").exists():
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
        "project_status": governance / "project_status.md",
        "roadmap": governance / "roadmap.md",
        "stage_archive": root / "stage_archive",
        "workspace_artifacts": root / "workspace_artifacts",
    }


# ---------------------------------------------------------------------------
# AIPOS-226 governance home — Slice 2 (Phase 2a): Owner scaffold + project.json
#
# `project new` / `project set-repo` are LOCAL OWNER scaffolds (ruling 2=a) — not gate
# operations: they mint no token, perform no gate confirm, and the gate has no "create project"
# op. Writing to disk here is intended (like `lybra init`). project.json is the SOLE authority
# for the project<->code-repo mapping (ruling 6) and carries provenance (M3: project creation
# is non-anonymous). Stdlib only.
# ---------------------------------------------------------------------------

_QUEUE_STATES = ("pending", "claimed", "completed", "blocked")


# ---------------------------------------------------------------------------
# AIPOS-335: collaboration_profile schema (AIPOS-304 阶段一)
#
# project.json 新增 collaboration_profile 字段，记录项目协作能力配置。
# 向后兼容：缺字段时不报错，提供默认行为。
# ---------------------------------------------------------------------------

def default_collaboration_profile() -> dict[str, Any]:
    """AIPOS-335: collaboration_profile 默认值（向后兼容现状）。
    
    按 AIPOS-304 D1 schema:
    - code_enabled: 默认 True（现状所有项目都跑代码任务）
    - deploy_gate_enabled: 默认 False（现状无部署门）
    - default_audit_mode: "agent"（现状所有任务都走完整 agent 审计）
    - output_locations: ["product_repo_worktree", "workspace_records"]（现状默认产出位置）
    
    这个默认值使老项目行为零改变。
    """
    return {
        "code_enabled": True,
        "deploy_gate_enabled": False,
        "default_audit_mode": "agent",
        "output_locations": ["product_repo_worktree", "workspace_records"],
    }


def get_collaboration_profile(project_root: str | Path) -> dict[str, Any]:
    """AIPOS-335: 读取项目的 collaboration_profile，缺失时返回默认值。

    向后兼容：老项目不存在该字段时，返回 default_collaboration_profile()，
    不报错、不阻断任何操作。
    """
    project_json = read_project_json(project_root)
    profile = project_json.get("collaboration_profile")
    if profile is None or not isinstance(profile, dict):
        return default_collaboration_profile()
    # 补齐缺失的字段（部分填写的场景）
    result = default_collaboration_profile()
    result.update(profile)
    return result


# ---------------------------------------------------------------------------
# AIPOS-338 S5: workspace-level dispatch_mode (auto | manual)
#
# Owner-only switch. Truth lives in project.json (NOT conversation). manual =
# "turn OFF auto-dispatch" (the pump refuses); auto = manual /claim still works.
# Default auto; old workspaces without the field are treated as auto (zero error).
# Switching is append-only logged (who / when / why). Judgment stays with the
# Owner — product/advisor only PROPOSE a switch (e.g. on repeated pump failures).
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
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        "registered_at": registered_at or _utc_now_iso(),
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
        "created_at": datetime.now(timezone.utc).isoformat(),
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

    for state in _QUEUE_STATES:
        (root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
    for sub in ("records", "drafts", "orchestration"):
        (root / "5_tasks" / sub).mkdir(parents=True, exist_ok=True)
    (root / "governance").mkdir(parents=True, exist_ok=True)

    paths = governance_paths(root)
    paths["stage_archive"].mkdir(parents=True, exist_ok=True)
    paths["workspace_artifacts"].mkdir(parents=True, exist_ok=True)

    decision_log = paths["decision_log"]  # ruling 1=B: single file
    if not decision_log.exists():
        decision_log.write_text(f"# {clean} Decision Log\n", encoding="utf-8")

    write_project_json(root, clean, code_repo=code_repo, registered_by=registered_by, collaboration_profile=collaboration_profile)
    
    # AIPOS-F24 大项D: connection.json 骨架(含 mcp.rpc_url)
    if gate_rpc_url:
        _write_connection_skeleton(root, gate_rpc_url)
    
    return root


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
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
