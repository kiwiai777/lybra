from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.task_complexity import complexity_payload
from tools.aipos_cli.workspace_config import (



    has_workspace_queue,
    resolve_workspace_context,
    resolve_workspace_root,
)

def _queue_state_projection(flag: str) -> tuple[str, ...]:
    """AIPOS-F104 件②: 队列状态集合唯一投影——读 enums.schema queue_state 每值的布尔 flag(queue_dir / skeleton / terminal)。
    缺 flag 或非布尔 = 声明缺失, SchemaLoadError fail-closed(禁回退手写集合)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    entries = ((load_schema("enums").get("enums") or {}).get("queue_state") or {}).get("values") or []
    if not entries:
        raise SchemaLoadError("enums.schema.json queue_state 未声明")
    out: list[str] = []
    for entry in entries:
        marker = entry.get(flag) if isinstance(entry, dict) else None
        if not isinstance(marker, bool):
            raise SchemaLoadError(f"enums.schema.json queue_state 值 {entry!r} 缺布尔声明 {flag}")
        if marker:
            out.append(str(entry["value"]))
    return tuple(out)


# 队列状态目录名(<queue_root>/<状态>/)= enums queue_state 中 queue_dir=true 的值(returned 为记录推导态, 无目录)
QUEUE_STATES = _queue_state_projection("queue_dir")
# 新项目/脚手架预建的队列目录 = enums queue_state 中 skeleton=true 的值(withdrawn 按需建)
QUEUE_SKELETON_STATES = _queue_state_projection("skeleton")
# AIPOS-F117 件③(gap #62): 终态(卡生命周期已结束, 推导核不再派生推进步)= enums queue_state 中 terminal=true 的值(completed / withdrawn)
QUEUE_TERMINAL_STATES = _queue_state_projection("terminal")


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
    # AIPOS-F106 件③: 工作区根环境变量经 workspace_config.workspace_root_from_env 唯一读取口(LYBRA_WORKSPACE_ROOT; 旧名废弃兼容)
    from tools.aipos_cli.workspace_config import workspace_root_from_env

    raw, env_name = workspace_root_from_env()
    if not raw:
        return None
    workspace_root = Path(raw).expanduser().resolve()
    if not _has_queue_root(workspace_root):
        raise FileNotFoundError(f"{env_name} does not contain 5_tasks/queue: {workspace_root}")
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
    # workspace-root env stripped (AIPOS-F106: 新名 LYBRA_WORKSPACE_ROOT 与废弃旧名两者), so the legacy explicit-start
    # contract (workspace-root env ignored when a start is given) is preserved.
    from tools.aipos_cli.workspace_config import LEGACY_WORKSPACE_ROOT_ENV, WORKSPACE_ROOT_ENV

    env = {k: v for k, v in os.environ.items() if k not in (WORKSPACE_ROOT_ENV, LEGACY_WORKSPACE_ROOT_ENV)}
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
    """队列根的唯一读取口(AIPOS-F89 件① M8): 项目声明 project.json paths.queue_root, 经 workspace_config.project_paths
    (缺 project.json / 缺键 = config.schema configuration_sources.project_json.schema.paths.queue_root.default)。
    代码禁写死队列目录字面, 禁第二份声明(config.schema governance_structure.paths.queue 与 queue_mutation.QUEUE_ROOT 已删)。"""
    from tools.aipos_cli.workspace_config import project_paths

    return Path(project_paths(Path(repo_root))["queue_root"])


def queue_state_ref(repo_root: Path | None, state: str) -> str:
    """队列状态子目录的治理根相对引用(卡面 draft_publish_target / 计划写入展示, 形 `<queue_root>/<state>/`)。
    队列根声明在治理根外(绝对路径) = 返回绝对路径。唯一来源 queue_root_for。
    repo_root=None(调用方尚无治理根, 如离线派生预览)= 声明 default(config.schema project_json.paths.queue_root.default)。"""
    if repo_root is None:
        from tools.aipos_cli.workspace_config import _project_paths_declaration

        default = str((_project_paths_declaration().get("queue_root") or {}).get("default") or "").strip().strip("/")
        if not default:
            from tools.schema_loader import SchemaLoadError

            raise SchemaLoadError("config.schema.json project_json.paths.queue_root.default 未声明")
        return f"{default}/{state}/" if state else f"{default}/"
    root = Path(repo_root)
    target = queue_root_for(root) / state
    for base, cand in ((root, target), (root.resolve(), target.resolve())):
        try:
            return cand.relative_to(base).as_posix().rstrip("/") + "/"
        except ValueError:
            continue
    return target.as_posix().rstrip("/") + "/"


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


# AIPOS-F94: 只读全量扫描(state lint 对数百张卡逐卡查找 = 每卡全量读队列, lybra 治理根 900+ 卡 10 分钟跑不完)期间的一趟索引。
# 判据与逐卡查找完全相同(iter_queue_task_paths 同序 + _frontmatter_task_id 精确匹配), 只是同一作用域内只扫一遍; 作用域内调用方
# 不得改队列(只读专用)。作用域外 find_task_card 行为不变。
_LOOKUP_INDEX: "ContextVar[dict[str, Any] | None]" = ContextVar("lybra_task_card_lookup_index", default=None)


@contextmanager
def task_card_lookup_scope(repo_root: Path) -> Iterator[None]:
    """只读批量调用方用: 作用域内对同一治理根的 find_task_card / find_task_card_matches 共用一趟队列索引。"""
    token = _LOOKUP_INDEX.set({"root": Path(repo_root).resolve(), "by_states": {}})
    try:
        yield
    finally:
        _LOOKUP_INDEX.reset(token)


def find_task_card_matches(repo_root: Path, task_id: str, *, states: tuple[str, ...] | None = None) -> list[Path]:
    """AIPOS-F78B 件①: 扫 queue 各状态目录, 按 frontmatter task_id 精确匹配(文件名不限, chris 形带 slug 亦可)。"""
    wanted = str(task_id or "").strip()
    if not wanted:
        return []
    scope = _LOOKUP_INDEX.get()
    if scope is not None and Path(repo_root).resolve() == scope["root"]:
        key = tuple(states or QUEUE_STATES)
        index = scope["by_states"].get(key)
        if index is None:
            index = {}
            for path in iter_queue_task_paths(Path(repo_root), states=states):
                found = _frontmatter_task_id(path)
                if found:
                    index.setdefault(found, []).append(path)
            scope["by_states"][key] = index
        return list(index.get(wanted, []))
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
