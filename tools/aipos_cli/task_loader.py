from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.task_complexity import complexity_payload
from tools.aipos_cli.workspace_config import (



    has_workspace_queue,
    resolve_workspace_context,
    resolve_workspace_root,
)

QUEUE_STATES = ("pending", "claimed", "completed", "blocked", "withdrawn")  # AIPOS-315: withdrawn for revoked/cancelled tasks


def _serialize_dates(obj: Any) -> Any:
    """递归转换 date/datetime 对象为 ISO 格式字符串,确保 JSON 可序列化。
    
    AIPOS-R1-FIX2: YAML 库会自动把 'created_at: 2026-08-11' 解析为 date 对象,
    导致 MCP 返回时 JSON 序列化失败。
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, date):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _serialize_dates(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_dates(item) for item in obj]
    else:
        return obj


def _has_queue_root(path: Path) -> bool:
    return has_workspace_queue(path)


def _workspace_root_from_env() -> Path | None:
    raw = os.environ.get("AIPOS_WORKSPACE_ROOT", "").strip()
    if not raw:
        return None
    workspace_root = Path(raw).expanduser().resolve()
    if not _has_queue_root(workspace_root):
        raise FileNotFoundError(f"AIPOS_WORKSPACE_ROOT does not contain 5_tasks/queue: {workspace_root}")
    return workspace_root


def find_repo_root(start: Path | None = None) -> Path:
    """Resolve the repo/project root. Thin wrapper over ``find_repo_context`` (byte-identical)."""
    return find_repo_context(start)[0]


def find_repo_context(start: Path | None = None) -> tuple[Path, Path | None]:
    """``(repo_root, home_root)`` — mirrors ``find_repo_root`` and also surfaces the truth home
    when the home model resolves the workspace (AIPOS-227). ``home_root`` is ``None`` for the
    env-root and explicit-valid-workspace short-circuits (direct/legacy) and for the legacy
    resolution paths, exactly as in ``resolve_workspace_context``."""
    if start is None:
        env_root = _workspace_root_from_env()
        if env_root is not None:
            return env_root, None
        return resolve_workspace_context(start)
    # AIPOS-226 FIX C① (parity): an explicit start that is ALREADY a valid workspace
    # (has 5_tasks/queue) is used DIRECTLY — no upward / home-model re-resolution. This mirrors
    # board_adapter._resolve_repo_root: an already-resolved root must never be silently
    # re-resolved to a different root. Internal callers (load_all_tasks(resolved_root), etc.)
    # rely on this so a valid root is honored verbatim.
    if _has_queue_root(start):
        return start.expanduser().resolve(), None
    # AIPOS-226 FIX C②: when the explicit start is NOT itself a workspace (e.g. a nested subdir),
    # resolution must STILL honor the home model (LYBRA_HOME_ROOT, the global ~/.lybra/config.json,
    # a found home_root config). The previous `env={}` dropped the entire home model, forcing a
    # legacy upward .lybra/config.json search that could misread the GLOBAL ~/.lybra/config.json
    # as a v1 workspace config and misresolve silently. We pass the REAL environment with only
    # AIPOS_WORKSPACE_ROOT stripped, so the legacy explicit-start contract (AIPOS_WORKSPACE_ROOT
    # ignored when a start is given) is preserved.
    env = {k: v for k, v in os.environ.items() if k != "AIPOS_WORKSPACE_ROOT"}
    return resolve_workspace_context(start, env=env)


def _normalize_task(
    path: Path,
    repo_root: Path,
    queue_state: str,
    metadata: dict[str, Any],
    body: str,
    parse_errors: list[str],
) -> dict[str, Any]:
    frontmatter_status = metadata.get("status")
    status_consistent = frontmatter_status == queue_state
    # AIPOS-R1-FIX2: 转换 metadata 中的 date/datetime 对象为字符串
    serialized_metadata = _serialize_dates(metadata)
    return {
        "task_id": metadata.get("task_id"),
        "title": metadata.get("title"),
        "path": str(path.resolve().relative_to(repo_root.resolve())),  # AIPOS-240 (F-o3-19): symlink-safe
        "repo_root": str(repo_root),
        "queue_state": queue_state,
        "frontmatter_status": frontmatter_status,
        "status_consistent": status_consistent,
        "assigned_to": metadata.get("assigned_to"),
        "agent_instance": metadata.get("agent_instance"),
        "claimed_by": metadata.get("claimed_by"),
        "task_mode": metadata.get("task_mode"),
        **complexity_payload(metadata),
        "model_tier": metadata.get("model_tier"),
        "needs_owner": metadata.get("needs_owner"),
        "metadata": serialized_metadata,
        "body": body,
        "parse_errors": parse_errors,
    }


def load_task_file(path: Path, repo_root: Path) -> dict[str, Any]:
    queue_state = path.parent.name
    parse_errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return _normalize_task(path, repo_root, queue_state, {}, "", [f"Read failed: {exc}"])

    metadata, body, warnings = parse_markdown_frontmatter(text)
    parse_errors.extend(warnings)
    return _normalize_task(path, repo_root, queue_state, metadata, body, parse_errors)


def queue_root_for(repo_root: Path) -> Path:
    """队列根: 项目声明 project.json paths.queue_root(config.schema 声明表, 缺省 5_tasks/queue)。
    无 project.json 的根(靶场/裸治理根)直接取声明默认, 不出 warning。"""
    root = Path(repo_root)
    if (root / "project.json").is_file():
        from tools.aipos_cli.workspace_config import project_paths

        return Path(project_paths(root)["queue_root"])
    return root / "5_tasks" / "queue"


def iter_queue_task_paths(repo_root: Path, *, states: tuple[str, ...] | None = None) -> list[Path]:
    queue_root = queue_root_for(repo_root)
    paths: list[Path] = []
    for queue_state in (states or QUEUE_STATES):
        state_dir = queue_root / queue_state
        if not state_dir.exists():
            continue
        for path in sorted(state_dir.iterdir()):
            if path.is_file() and path.suffix == ".md":
                paths.append(path)
    return paths


_TASK_ID_LINE = re.compile(r"^task_id:\s*['\"]?([^'\"\s]+)['\"]?\s*$", re.MULTILINE)


def _frontmatter_task_id(path: Path) -> str | None:
    """读卡文件 frontmatter 的 task_id。YAML 解析失败(坏卡, `queue repair` 场景)时退到正则读首个 `task_id:` 行,
    仍是 frontmatter 声明而非文件名。读失败 = 精确捕获 + warning + None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        import sys

        print(f"Warning: task card unreadable, skipped in lookup: {path}: {exc}", file=sys.stderr)
        return None
    metadata, _body, _warnings = parse_markdown_frontmatter(text)
    value = metadata.get("task_id") if isinstance(metadata, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if text.startswith("---"):
        head = text.split("\n---", 1)[0]
        match = _TASK_ID_LINE.search(head)
        if match:
            return match.group(1).strip()
    return None


class AmbiguousTaskCard(ValueError):
    """同一 task_id 在 queue 中命中多个文件(fail-closed: 拒, 由人裁定)。"""


def find_task_card_matches(repo_root: Path, task_id: str, *, states: tuple[str, ...] | None = None) -> list[Path]:
    """AIPOS-F78B 件①: 扫 queue 各状态目录, 按 frontmatter task_id 精确匹配(文件名不限, chris 形带 slug 亦可)。"""
    wanted = str(task_id or "").strip()
    if not wanted:
        return []
    return [p for p in iter_queue_task_paths(Path(repo_root), states=states) if _frontmatter_task_id(p) == wanted]


def find_task_card(repo_root: Path, task_id: str, *, states: tuple[str, ...] | None = None) -> tuple[Path | None, str | None]:
    """AIPOS-F78B 件①: 卡文件唯一查找——返回 (path, queue_dir_name); 找不到 (None, None); 多义 raise AmbiguousTaskCard。
    next_resolver/artifact_ingest/loop_driver/queue 薄壳/state_lint/flow_description 等全部经此, 禁 `<id>.md` 文件名拼接第二路径。"""
    matches = find_task_card_matches(repo_root, task_id, states=states)
    if not matches:
        return None, None
    if len(matches) > 1:
        raise AmbiguousTaskCard(
            f"task_id {task_id} 在 queue 中命中 {len(matches)} 个文件(禁多义): " + ", ".join(str(p) for p in matches)
        )
    return matches[0], matches[0].parent.name


def load_all_tasks(repo_root: Path | None = None) -> list[dict[str, Any]]:
    resolved_root = find_repo_root(repo_root)
    return [load_task_file(path, resolved_root) for path in iter_queue_task_paths(resolved_root)]


def find_task_by_id(task_id: str, repo_root: Path | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """字典形查找: 与 find_task_card 同一匹配规则(frontmatter task_id 精确匹配), 返回 (唯一命中 | None, 全部命中)。"""
    resolved_root = find_repo_root(repo_root)
    matches = [load_task_file(path, resolved_root) for path in find_task_card_matches(resolved_root, task_id)]
    if len(matches) == 1:
        return matches[0], matches
    return None, matches


def load_task_by_path(task_path: str, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_root = find_repo_root(repo_root)
    path = (resolved_root / task_path).resolve() if not Path(task_path).is_absolute() else Path(task_path).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise FileNotFoundError(f"Task path is outside repo root: {task_path}") from exc
    if not path.exists():
        raise FileNotFoundError(f"Task path does not exist: {task_path}")
    if not path.is_file():
        raise FileNotFoundError(f"Task path is not a file: {task_path}")
    if path.suffix != ".md":
        raise ValueError(f"Task path is not a markdown file: {task_path}")
    return load_task_file(path, resolved_root)
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
