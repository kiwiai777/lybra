from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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
    """Resolve the active project name.

    AIPOS-226 §1.3 precedence:
      1. --project / explicit
      2. LYBRA_ACTIVE_PROJECT env
      3. ~/.lybra/config.json .active_project  (the GLOBAL runtime config; loaded here if
         `global_config` is not supplied)
      4. single-project fallback (exactly one <home>/<child> with 5_tasks/queue AND project.json)
      5. else fail-closed ValueError("PROJECT_AMBIGUOUS: ...")

    Compatibility: `config` is the AIPOS-225 Slice-1 in-workspace config dict (board_adapter
    passes it). When supplied it is honored as the active_project source in step 3 (instead of
    loading the global config) so the Slice-1 fallback path stays byte-identical.
    """
    source_env = env if env is not None else os.environ
    if explicit and str(explicit).strip():
        return str(explicit).strip()

    env_val = str(source_env.get(ACTIVE_PROJECT_ENV) or "").strip()
    if env_val:
        return env_val

    if config is not None:
        from_config = active_project_from_config(config)
        if from_config:
            return from_config
    else:
        if global_config is None:
            global_config = load_global_config(source_env)
        from_global = global_config_active_project(global_config)
        if from_global:
            return from_global

    home = Path(home_root).expanduser().resolve()
    candidates = _project_candidates(home)
    if len(candidates) == 1:
        return candidates[0]

    raise ValueError(
        "PROJECT_AMBIGUOUS: could not resolve an active project via --project, "
        f"{ACTIVE_PROJECT_ENV}, ~/.lybra/config.json active_project, or a single-project "
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
) -> Path:
    """Write <project_root>/project.json with provenance (M3).

    Schema: {project, code_repo, registered_at, registered_by, config_version:1} (sorted,
    2-indent, trailing newline). `code_repo` is stored as an expanded absolute-ish string or
    null. When `preserve_registered_at` and an existing project.json already carries a
    `registered_at`, it is kept (so set-repo never clobbers the original creation provenance).
    """
    root = Path(project_root)
    path = project_json_path(root)

    if preserve_registered_at:
        existing = read_project_json(root)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def scaffold_project(
    home_root: str | Path,
    name: str,
    *,
    code_repo: str | Path | None = None,
    registered_by: str = "owner",
) -> Path:
    """Owner scaffold of a fresh per-project truth root under the home.

    Creates the full project tree (queue 4 states, records/drafts/orchestration, governance/,
    stage_archive/, workspace_artifacts/), a single-file governance/decision_log.md (ruling
    1=B) stub if absent, and project.json. Refuses to overwrite a non-empty existing root
    (teaching error). Directory shape is sourced from governance_paths() so there is one
    definition.
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

    write_project_json(root, clean, code_repo=code_repo, registered_by=registered_by)
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
