from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_RELATIVE_PATH = Path(".lybra") / "config.json"
DEFAULT_BOARD_HOST = "127.0.0.1"
DEFAULT_BOARD_PORT = 7117
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 7118

# AIPOS-224 (governance home, Slice 0): home-root + active-project resolution.
# This block is ADDITIVE and UNWIRED — no existing resolver/caller behaviour changes in this
# slice. `default_workspace_config()` intentionally still emits config_version 1 (M1: read v2
# now, default-write v2 deferred to Slice 2). Pure functions, fail-closed, stdlib only.
DEFAULT_HOME_ROOT = Path("~/.lybra/workspace")
HOME_ROOT_ENV = "AIPOS_HOME_ROOT"
ACTIVE_PROJECT_ENV = "LYBRA_ACTIVE_PROJECT"
LEGACY_WORKSPACE_ROOT_ENV = "AIPOS_WORKSPACE_ROOT"


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
    source_env = env if env is not None else os.environ
    if explicit_root:
        return _validate_workspace_root(Path(explicit_root), source="--workspace-root")

    raw_env_root = str(source_env.get("AIPOS_WORKSPACE_ROOT") or "").strip()
    if raw_env_root:
        return _validate_workspace_root(Path(raw_env_root), source="AIPOS_WORKSPACE_ROOT")

    config_path = find_workspace_config(start)
    if config_path is not None:
        return workspace_root_from_config(config_path)

    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if has_workspace_queue(candidate):
            return candidate
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
# Truth lives in a survivable HOME ROOT (default ~/.lybra/workspace) holding one subtree per
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
    """Names of immediate subdirs of `home_root` that look like established projects."""
    if not home_root.exists():
        return []
    return sorted(
        child.name
        for child in home_root.iterdir()
        if child.is_dir() and has_workspace_queue(child)
    )


def resolve_home_root(
    start: Path | None = None,
    *,
    explicit_root: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve the survivable home root (container of project subtrees).

    Precedence (AIPOS-223 §Resolution algorithm, HOME ROOT):
      1. explicit flag (--home-root / --workspace-root)  — treated AS the home
      2. AIPOS_HOME_ROOT env                             (new)
      3. AIPOS_WORKSPACE_ROOT env                        (legacy = a project root; home = its parent)
      4. .lybra/config.json .home_root                   (v2)
      5. ~/.lybra/workspace default                      (only if it already exists)
      6. upward search for a 5_tasks/queue project root  (legacy bare subtree; home = its parent)
    Fail-closed with FileNotFoundError("HOME_NOT_RESOLVED: ...") when none resolve and the
    default home does not exist. This function never creates anything.
    """
    source_env = env if env is not None else os.environ
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()

    raw_home = str(source_env.get(HOME_ROOT_ENV) or "").strip()
    if raw_home:
        return Path(raw_home).expanduser().resolve()

    legacy = str(source_env.get(LEGACY_WORKSPACE_ROOT_ENV) or "").strip()
    if legacy:
        # Legacy env points at a project root; in the home model its home is the parent.
        return Path(legacy).expanduser().resolve().parent

    config_path = find_workspace_config(start)
    if config_path is not None:
        configured = home_root_from_config(load_workspace_config(config_path))
        if configured is not None:
            if not configured.is_absolute():
                configured = config_path.parent.parent / configured
            return configured.expanduser().resolve()

    default_home = DEFAULT_HOME_ROOT.expanduser()
    if default_home.exists():
        return default_home.resolve()

    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if has_workspace_queue(candidate):
            return candidate.parent

    raise FileNotFoundError(
        "HOME_NOT_RESOLVED: could not resolve a Lybra home root via --home-root, "
        f"{HOME_ROOT_ENV}, {LEGACY_WORKSPACE_ROOT_ENV}, .lybra/config.json (home_root), "
        f"the default {DEFAULT_HOME_ROOT}, or an upward 5_tasks/queue search."
    )


def resolve_active_project(
    home_root: str | Path,
    *,
    explicit: str | None = None,
    request_arg: str | None = None,
    env: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Resolve the active project name.

    Precedence (AIPOS-223 §Resolution algorithm, ACTIVE PROJECT):
      1. --project / explicit
      2. LYBRA_ACTIVE_PROJECT env
      3. request-arg `project`
      4. .lybra/config.json .active_project (passed in via `config`)
      5. single-project fallback (exactly one <home>/*/5_tasks/queue)
      6. else fail-closed ValueError("PROJECT_AMBIGUOUS: ...")
    """
    source_env = env if env is not None else os.environ
    if explicit and str(explicit).strip():
        return str(explicit).strip()

    env_val = str(source_env.get(ACTIVE_PROJECT_ENV) or "").strip()
    if env_val:
        return env_val

    if request_arg and str(request_arg).strip():
        return str(request_arg).strip()

    if config is not None:
        from_config = active_project_from_config(config)
        if from_config:
            return from_config

    home = Path(home_root).expanduser().resolve()
    candidates = _project_candidates(home)
    if len(candidates) == 1:
        return candidates[0]

    raise ValueError(
        "PROJECT_AMBIGUOUS: could not resolve an active project via --project, "
        f"{ACTIVE_PROJECT_ENV}, request arg, config active_project, or a single-project "
        f"fallback under {home} (found {len(candidates)} candidate project(s): {candidates})."
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
    if not has_workspace_queue(root):
        raise FileNotFoundError(
            f"PROJECT_NOT_ESTABLISHED: project {name!r} has no 5_tasks/queue under {home}; "
            f"run `lybra project new {name}` (no lazy-create)."
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
