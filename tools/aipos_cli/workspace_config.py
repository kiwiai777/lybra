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
