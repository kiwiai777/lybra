from __future__ import annotations

import copy
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.aipos_cli.frontmatter import read_markdown_frontmatter
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


_PLAIN_SCALARS = frozenset((str, int, float, bool, type(None)))


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
        # AIPOS-F177: plain scalars are returned as-is without a recursive call (same result, far fewer calls)
        return {k: v if type(v) in _PLAIN_SCALARS else _serialize_dates(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [item if type(item) in _PLAIN_SCALARS else _serialize_dates(item) for item in obj]
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


def _card_rel_path(path: Path, repo_root: Path) -> str:
    """str(path.resolve().relative_to(repo_root.resolve())), AIPOS-240 symlink-safe. AIPOS-F177: inside a read scope the realpath of the
    governance root and of each queue directory is walked once per scope; a card file that is itself a symlink is always resolved in
    full; the containment check is the same component-wise prefix test relative_to makes (anything else falls back to relative_to,
    which raises as before). Outside a scope = the plain two resolves (unchanged)."""
    scope = _LOOKUP_INDEX.get()
    if scope is None:
        return str(path.resolve().relative_to(repo_root.resolve()))
    real = scope.setdefault("realpaths", {})
    root_key = os.fspath(repo_root)
    root_real = real.get(root_key)
    if root_real is None:
        root_real = real[root_key] = str(repo_root.resolve())
    path_s = os.fspath(path)
    if os.path.islink(path_s):
        file_real = str(path.resolve())
    else:
        parent_key, name = os.path.split(path_s)
        parent_real = real.get(parent_key)
        if parent_real is None:
            parent_real = real[parent_key] = str(Path(parent_key).resolve())
        file_real = f"{parent_real}/{name}" if parent_real != "/" else f"/{name}"
    if root_real != "/" and file_real.startswith(root_real + "/") and len(file_real) > len(root_real) + 1:
        return file_real[len(root_real) + 1:]
    return str(Path(file_real).relative_to(root_real))


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
        "path": _card_rel_path(path, repo_root),  # AIPOS-240 (F-o3-19): symlink-safe
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
        metadata, body, warnings, _fence = read_markdown_frontmatter(path)
    except Exception as exc:
        return _normalize_task(path, repo_root, queue_state, {}, "", [f"Read failed: {exc}"])

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
    """卡遍历唯一口(队列各状态目录下的 .md, 按状态声明序、目录内排序)。AIPOS-F177 件③: 只读作用域内同一治理根同一状态集只列一次目录。"""
    key = f"task_loader.iter_queue_task_paths:{tuple(states or QUEUE_STATES)!r}"
    return list(lookup_scope_memo(repo_root, key, lambda: _list_queue_task_paths(repo_root, states)))


def _list_queue_task_paths(repo_root: Path, states: tuple[str, ...] | None) -> list[Path]:
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
        metadata, _body, _warnings, fence = read_markdown_frontmatter(path)
        value = metadata.get("task_id") if isinstance(metadata, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        text = path.read_text(encoding="utf-8") if fence else ""  # regex fallback only (bad YAML): read the raw head
    except (OSError, UnicodeDecodeError) as exc:
        import sys

        print(f"Warning: task card unreadable, skipped in lookup: {path}: {exc}", file=sys.stderr)
        return None
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


def _root_scope() -> dict[str, Any]:
    return {"by_states": {}, "memo": {}}


def _scope_for(repo_root: Path) -> dict[str, Any] | None:
    """The active scope's per-root entry for this governance root (None = not inside a read scope for it).
    Keys are resolved roots; a caller passing the resolved root already hits without another realpath walk."""
    scope = _LOOKUP_INDEX.get()
    if scope is None:
        return None
    roots = scope["roots"]
    root = Path(repo_root)
    entry = roots.get(root) or roots.get(root.resolve())
    if entry is None and scope["request"]:
        # a request-level scope covers every governance root the request reads (read-only for all of them)
        entry = roots[root.resolve()] = _root_scope()
    return entry


@contextmanager
def task_card_lookup_scope(repo_root: Path) -> Iterator[None]:
    """只读批量调用方用: 作用域内对同一治理根的 find_task_card / find_task_card_matches 共用一趟队列索引。

    AIPOS-F142 件①: 可重入——已在同一治理根的作用域内再进入 = 共用外层那一趟索引与 lookup_scope_memo 缓存(原每次进入都新建,
    视图扫描逐卡 derive_next_step 各建一趟全队列索引 = O(卡数²), lybra 治理根 1000+ 卡单次 loop status 70 秒); 根不同 = 新建。
    作用域只活在一次只读调用内(视图命令 / 一次推导), 退出即弃, 不跨调用缓存。
    AIPOS-F177 件③: 处在请求级作用域(read_request_scope)内时, 进入新根 = 把该根加进请求作用域(请求结束才弃), 同一请求里各视图共用。"""
    current = _LOOKUP_INDEX.get()
    if current is not None and Path(repo_root) in current["roots"]:  # AIPOS-F229: already a resolved key = no realpath walk
        yield
        return
    root = Path(repo_root).resolve()
    if current is not None and root in current["roots"]:
        yield
        return
    if current is not None and current["request"]:
        current["roots"][root] = _root_scope()
        yield
        return
    token = _LOOKUP_INDEX.set({"request": False, "roots": {root: _root_scope()}})
    try:
        yield
    finally:
        _LOOKUP_INDEX.reset(token)


@contextmanager
def read_request_scope() -> Iterator[None]:
    """AIPOS-F177 件③: 一次只读请求(看板总览 / 收件箱 / 路线图 / 接入页, CLI owner inbox)的请求级作用域。
    作用域内每个进入过 task_card_lookup_scope 的治理根各一份索引与 lookup_scope_memo 缓存, 跨项目、跨视图共用, 请求结束即弃
    (不跨请求缓存; 看板多线程各请求各自一份: ContextVar 按线程隔离)。已在任一作用域内 = 共用外层(不新建)。只读专用: 作用域内不得改队列。"""
    if _LOOKUP_INDEX.get() is not None:
        yield
        return
    token = _LOOKUP_INDEX.set({"request": True, "roots": {}})
    try:
        yield
    finally:
        _LOOKUP_INDEX.reset(token)


def request_scope_active() -> bool:
    """AIPOS-F229: True = inside a request-level read scope (read_request_scope), i.e. request_memo memoises."""
    scope = _LOOKUP_INDEX.get()
    return scope is not None and bool(scope["request"])


def request_memo(key: tuple[Any, ...], compute: Callable[[], Any]) -> Any:
    """AIPOS-F177 件③: request-level memo. Inside a request-level read scope (read_request_scope) one value per key per request
    (e.g. the enrollment log split and matched once although every instance check of the onboarding page reads it; the gate
    role registry loaded once for the home root); outside a request scope = compute() every time. A compute that raises is not
    memoised (it raises again next time). Keys start with the owning function's dotted name. The caller owns copying a mutable
    value out."""
    scope = _LOOKUP_INDEX.get()
    if scope is None or not scope["request"]:
        return compute()
    return _scope_memo(scope.setdefault("request_memo", {}), key, compute)


def _scope_memo(memo: dict[Any, tuple[Any, Any]], key: Any, compute: Callable[[], Any]) -> Any:
    """One memo slot of a read scope: {key: (value, truth)}. AIPOS-F245 件①: while a truth-cached derivation is recording
    (truth_cached), the truth a memoised value was derived from is kept with it and folded into every recording derivation that
    reuses it (nested cached derivations share the request's memos and still know everything they depend on); a value memoised while
    nothing was recording is derived again for a recording one. truth = None when nothing was recording."""
    tracker = _TRUTH_TRACKER.get()
    slot = memo.get(key)
    if slot is not None and (tracker is None or slot[1] is not None):
        if tracker is not None:
            tracker.merge(*slot[1])
        return slot[0]
    if tracker is None:
        value = compute()
        memo[key] = (value, None)
        return value
    sub = _TruthTracker()
    sub.external, sub.quiet, sub.flat = tracker.external, tracker.quiet, tracker.flat
    token = _TRUTH_TRACKER.set(sub)
    try:
        value = compute()
    finally:
        _TRUTH_TRACKER.reset(token)
        tracker.merge(*sub.bundle())
    memo[key] = (value, sub.bundle())
    return value


def lookup_scope_memo(repo_root: Path, key: str, compute: Callable[[], Any], *, copy_out: bool = False) -> Any:
    """AIPOS-F142 件①: 只读作用域(task_card_lookup_scope)内同一治理根的整根派生量只算一次(冻结清单重放 / 信封已放行计数等);
    作用域外 = 每次 compute()(门写动作与逐次调用行为不变)。compute 抛错不缓存(下次照常重算并再抛, fail-closed 不变)。
    AIPOS-F177 件③: copy_out=True = 作用域内每次交出缓存值的深拷贝(整份视图类结果: 调用方改动不串到同一请求的别的视图)。"""
    entry = _scope_for(repo_root)
    if entry is None:
        return compute()
    value = _scope_memo(entry["memo"], key, compute)
    return copy.deepcopy(value) if copy_out else value


def find_task_card_matches(repo_root: Path, task_id: str, *, states: tuple[str, ...] | None = None) -> list[Path]:
    """AIPOS-F78B 件①: 扫 queue 各状态目录, 按 frontmatter task_id 精确匹配(文件名不限, chris 形带 slug 亦可)。"""
    wanted = str(task_id or "").strip()
    if not wanted:
        return []
    scope = _scope_for(repo_root)
    if scope is not None:
        def build() -> dict[str, list[Path]]:
            index: dict[str, list[Path]] = {}
            for path in iter_queue_task_paths(Path(repo_root), states=states):
                found = _frontmatter_task_id(path)
                if found:
                    index.setdefault(found, []).append(path)
            return index

        def indexed() -> dict[str, list[Path]]:
            # AIPOS-F249 件③: board server = the index is one truth-cached derivation per root, so every cached per-card derivation that
            # looks a card up depends on the index's value (one nested key) instead of carrying every queue file's identity
            return truth_cached(("task_loader.card_index", str(Path(repo_root)), tuple(states or QUEUE_STATES)), build)

        return list(_scope_memo(scope["by_states"], tuple(states or QUEUE_STATES), indexed).get(wanted, []))
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
    """All queue cards (iter_queue_task_paths order), each normalised by load_task_file. AIPOS-F229 item 3: inside a request-level
    read scope (read_request_scope) one load per governance root per request (the board overview used to load every project's
    queue three times: get_queue / get_needs_owner / owner truth view); every call gets private copies of the task dicts
    (frontmatter._fresh_copy). Outside a request scope = loaded every call (unchanged)."""
    resolved_root = find_repo_root(repo_root)
    if not request_scope_active():
        return [load_task_file(path, resolved_root) for path in iter_queue_task_paths(resolved_root)]
    from tools.aipos_cli.frontmatter import _fresh_copy

    tasks = request_memo(("task_loader.load_all_tasks", str(resolved_root)),
                         lambda: [load_task_file(path, resolved_root) for path in iter_queue_task_paths(resolved_root)])
    return [_fresh_copy(task) for task in tasks]


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

# ---------------------------------------------------------------------------
# AIPOS-F245 件①: truth-fingerprinted result cache (board overview / inbox / roadmap / onboarding pages and their /api data).
# AIPOS-F249: the minute left the fingerprint (time judgments report when they flip), nested results are depended on by their value
# (a re-derived child that came out the same does not re-derive its parents), young truth is fingerprinted by content, and the board
# server keeps the pages it served fresh in the background.
#
# A cached derivation (truth_cached) is stored with the fingerprint of every piece of truth it read, recorded while it ran:
#   - every path it stat-ed (os.stat / Path.exists / is_file / is_dir / the F177 per-file read caches) or opened (audit event
#     "open"), with the F177 level-1 stat identity of a file (frontmatter._stat_identity: dev, ino, size, mtime_ns, ctime_ns); a
#     directory only stat-ed = "is a directory" (its entries are not what was read); a missing path is recorded as missing;
#   - every directory it listed (audit events "os.listdir" / "os.scandir": glob, iterdir, walk) = (dev, ino, mtime_ns), so an
#     added / removed / renamed entry changes it;
#   - AIPOS-F249: truth younger than the F177 racy window (frontmatter.within_racy_window: a same-tick rewrite keeps the stat identity)
#     that the derivation opened or listed is also fingerprinted by content (file bytes / entry names, taken before the derivation
#     read it and compared on every check); a young file only stat-ed needs nothing more (the identity is all that was read);
#   - explicit fingerprints computed by their own single implementation (truth_note_fingerprint: the F55 / F229 records group
#     fingerprint records._group_fingerprint, whose scan is not recorded path by path); one younger than the racy window = not stored;
#   - AIPOS-F249 time: the earliest moment one of its time judgments flips (clock.judged_now + clock.holds_until: overdue, expiry,
#     stalled, stale, windows, whole days); a plain clock read whose judgments do not report (clock.utc_now) = valid to the end of
#     that minute (the F245 granularity, fail-closed for every judgment not yet converted);
#   - AIPOS-F249 件③: every nested cached derivation it used = that derivation's key and the digest of the value it handed over
#     (not the nested truth itself): a check re-checks the nested one first (re-deriving it when its own truth changed) and only a
#     different value invalidates the outer one (early cut-off: a loop run record rewritten every few seconds re-derives the card it
#     belongs to, not the pages built on it while nothing they show changed).
# A stored result is returned only while its time has not run out and every recorded identity / content / fingerprint / nested value
# is unchanged (checked on every request, so any truth change is reflected by the very next request; a file the derivation itself
# writes is recorded as it was before the open, so a writing derivation is never served from the cache and its writes still happen).
# Fail-closed = not stored: a subprocess / socket / thread / signal the derivation was not declared for (truth_external_read), a
# young explicit fingerprint, a young file too large to fingerprint by content, or a nested derivation that was not stored.
# Read-scope memos (request_memo / lookup_scope_memo / the card index) keep the truth their value was derived from, so cached
# derivations sharing one request's memos each still record everything they depend on.
# One derivation per key at a time (single flight): a request that finds a stale result while it is being re-derived waits for that
# (bounded) instead of deriving it twice. AIPOS-F249 件②: the board server re-derives the most recently served pages in the
# background after their truth changed (start_truth_refresher, declared verbs.schema board_readonly_pages.truth_refresh), so a
# request usually finds the latest result already derived; a request never gets a result whose truth is known to have changed.
# Off unless the board server process switches it on (enable_truth_cache, web.board.app.run_server): CLI, gate and in-process
# callers compute every call as before. Process memory only, bounded (least-recently-used), one lock; every caller gets its own
# deep copy.
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import pickle as _pickle
import stat as _stat_module
import sys as _sys
import threading as _threading
import time as _time
from collections import OrderedDict as _OrderedDict

_TRUTH_TRACKER: "ContextVar[_TruthTracker | None]" = ContextVar("lybra_truth_tracker", default=None)
#: AIPOS-F249: identities / contents / nested values checked in the current request or refresh pass (each path checked once per pass)
_TRUTH_PASS: "ContextVar[dict[Any, Any] | None]" = ContextVar("lybra_truth_pass", default=None)
_TRUTH_LOCK = _threading.Lock()
_TRUTH_STATE: dict[str, Any] = {"enabled": False, "hooks": False}
#: pages + per-project roadmap / card statistics + per-card loop status / loop item / stall judgment (AIPOS-F249 件③)
_TRUTH_MAX_ENTRIES = 4096
#: a young file larger than this is not fingerprinted by content (the derivation is then not stored, as before F249)
_TRUTH_YOUNG_CONTENT_MAX_BYTES = 4 * 1024 * 1024
_TRUTH_ENTRIES: "_OrderedDict[tuple[Any, ...], dict[str, Any]]" = _OrderedDict()
_TRUTH_INFLIGHT: dict[tuple[Any, ...], _threading.Event] = {}
#: AIPOS-F249 件②: top-level derivations served to requests (most recent last) = what the background refresher keeps fresh
_TRUTH_PAGES: "_OrderedDict[tuple[Any, ...], Callable[[], Any]]" = _OrderedDict()
_TRUTH_REFRESH: dict[str, Any] = {"thread": None, "stop": None, "decl": None, "last": {}}
_TRUTH_STATS: dict[str, Any] = {}
_REAL_STAT = os.stat
_MISSING = object()
#: audit events whose outcome is not a file the derivation read: refused unless declared (truth_external_read)
_TRUTH_EXTERNAL_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec", "os.fork",
                                    "os.forkpty", "socket.connect", "os.kill", "os.killpg", "_thread.start_new_thread",
                                    "_thread.start_joinable_thread"})


def _reset_truth_stats() -> None:
    _TRUTH_STATS.clear()
    _TRUTH_STATS.update(hits=0, misses=0, stored=0, not_stored=0, last_not_stored=None, last_changed=None, waited=0, refreshed=0,
                        refresh_seconds=0.0, minute_capped=0, last_minute_cap=None)


_reset_truth_stats()


class _TruthTracker:
    """What one cached derivation read: stats {(absolute path, mode): identity | None} (mode = True stat / False lstat / "list"
    directory listing), young {(path, mode): content digest} (AIPOS-F249: young truth opened / listed), fingerprints
    {key: (value, recompute, newest_ns)}, children {nested key: value digest} (AIPOS-F249 件③), valid_until (epoch seconds; AIPOS-F249
    time judgments), refused (reasons not to store), external (declared subprocess depth), quiet (recording suspended)."""

    __slots__ = ("stats", "young", "fingerprints", "children", "valid_until", "minute_cap", "refused", "external", "quiet", "merged", "flat")

    def __init__(self) -> None:
        self.stats: dict[tuple[str, Any], tuple[Any, ...] | None] = {}
        self.young: dict[tuple[str, Any], Any] = {}
        self.fingerprints: dict[Any, tuple[Any, Callable[[], Any], int]] = {}
        self.children: dict[tuple[Any, ...], bytes] = {}
        self.valid_until = float("inf")
        self.minute_cap: str | None = None
        self.refused: list[str] = []
        self.external = 0
        self.quiet = 0
        self.merged: set[int] = set()  # ids of truth bundles already folded in (a memo reused 4000 times is merged once)
        self.flat = False  # AIPOS-F249: truth_record's recorder = nested cached derivations are computed in place (all truth flat)

    def bundle(self) -> tuple[Any, ...]:
        return (self.stats, self.fingerprints, self.refused, self.young, self.children, self.valid_until)

    def merge(self, stats: dict[tuple[str, Any], Any], fingerprints: dict[Any, Any], refused: list[str],
              young: dict[tuple[str, Any], Any] | None = None, children: dict[tuple[Any, ...], bytes] | None = None,
              valid_until: float = float("inf")) -> None:
        """Fold an inner derivation's truth in (first observation wins: a change between the two reads invalidates next time).
        The same bundle (same stats dict object, kept alive by its memo slot / stored entry) is folded in once."""
        if id(stats) in self.merged:
            return
        self.merged.add(id(stats))
        for key, ident in stats.items():
            self.stats.setdefault(key, ident)
        for key, digest in (young or {}).items():
            self.young.setdefault(key, digest)
        for key, value in fingerprints.items():
            self.fingerprints.setdefault(key, value)
        for key, digest in (children or {}).items():
            self.children.setdefault(key, digest)
        self.valid_until = min(self.valid_until, valid_until)
        self.refused.extend(refused)


def _identity_of(st: os.stat_result, mode: Any = True) -> tuple[Any, ...]:
    from tools.aipos_cli.frontmatter import _stat_identity

    if _stat_module.S_ISDIR(st.st_mode):
        return ("listing", st.st_dev, st.st_ino, st.st_mtime_ns) if mode == "list" else ("dir",)
    return ("file", *_stat_identity(st))


def _path_identity(path: str, mode: Any = True) -> tuple[Any, ...] | None:
    """Identity of a path now (None = missing); an unreadable one = ("error", errno) (compared like any other identity).
    mode: True = stat, False = lstat, "list" = a directory listed (its entry set counts)."""
    try:
        st = _REAL_STAT(path, follow_symlinks=mode is not False)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return ("error", exc.errno)
    return _identity_of(st, mode)


def _abs(path: Any) -> str:
    return os.path.abspath(os.fsdecode(path))


def _young_identity(ident: tuple[Any, ...] | None) -> bool:
    """AIPOS-F249: an identity younger than the F177 racy window (a same-tick rewrite would keep it)."""
    from tools.aipos_cli.frontmatter import within_racy_window

    if not ident:
        return False
    if ident[0] == "listing":
        return within_racy_window(ident[3])
    if ident[0] == "file":
        return within_racy_window(max(ident[4], ident[5]))
    return False


def _content_digest(path: str, mode: Any) -> Any:
    """AIPOS-F249: content fingerprint of young truth: a file's bytes / a listed directory's entry names (missing = None, unreadable =
    ("error", errno)). Read with recording suspended by the caller."""
    try:
        if mode == "list":
            data = "\0".join(sorted(os.listdir(path))).encode("utf-8", "surrogateescape")
        else:
            if os.path.getsize(path) > _TRUTH_YOUNG_CONTENT_MAX_BYTES:
                return ("too_large",)
            with open(path, "rb") as handle:
                data = handle.read()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return ("error", exc.errno)
    return _hashlib.blake2b(data, digest_size=16).digest()


def _record_young(tracker: _TruthTracker, key: tuple[str, Any]) -> None:
    """A young file the derivation opens / a young directory it lists: fingerprint its content now (before the derivation reads it, so
    any later change, even one keeping the identity, is seen by the next check)."""
    if key in tracker.young or not _young_identity(tracker.stats.get(key)):
        return
    tracker.quiet += 1
    try:
        digest = _content_digest(key[0], key[1])
    finally:
        tracker.quiet -= 1
    if digest == ("too_large",):
        tracker.refused.append(f"young file too large to fingerprint by content {key[0]}")
    tracker.young[key] = digest


def _record_path(tracker: _TruthTracker, path: Any, mode: Any = True, *, read: bool = False) -> None:
    key = (_abs(path), mode)
    if key not in tracker.stats:
        tracker.stats[key] = _path_identity(key[0], key[1])
    if read or mode == "list":
        _record_young(tracker, key)


def _tracked_stat(path: Any, *, dir_fd: Any = None, follow_symlinks: bool = True) -> os.stat_result:
    """os.stat with recording (installed by enable_truth_cache): inside a cached derivation the identity it saw is kept."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is None or tracker.quiet or isinstance(path, int):
        return _REAL_STAT(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    if dir_fd is not None:
        tracker.refused.append("stat relative to a directory descriptor")
        return _REAL_STAT(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    key = (_abs(path), bool(follow_symlinks))
    try:
        st = _REAL_STAT(path, follow_symlinks=follow_symlinks)
    except (FileNotFoundError, NotADirectoryError):
        tracker.stats.setdefault(key, None)
        raise
    except OSError as exc:
        tracker.stats.setdefault(key, ("error", exc.errno))
        raise
    tracker.stats.setdefault(key, _identity_of(st))
    return st


def _truth_audit_hook(event: str, args: tuple[Any, ...]) -> None:
    """Process audit hook (installed once by enable_truth_cache; outside a cached derivation it only reads one ContextVar)."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is None or tracker.quiet:
        return
    try:
        if event == "open":  # read or write: the identity before the open is kept, so a derivation that writes invalidates itself
            path = args[0]
            if path is None or isinstance(path, int):
                return
            name = os.fsdecode(path)
            if "__pycache__" not in name:  # interpreter bytecode cache of a lazy import is not truth
                _record_path(tracker, name, read=True)
        elif event in ("os.listdir", "os.scandir"):
            path = args[0] if args else None
            if isinstance(path, int):
                tracker.refused.append(f"{event} of a directory descriptor")
                return
            _record_path(tracker, "." if path is None else path, "list")
        elif event in _TRUTH_EXTERNAL_EVENTS and not tracker.external:
            tracker.refused.append(f"{event} {str(args[:2])[:160]}")
    except Exception as exc:  # noqa: BLE001 - an audit hook must never break the audited call; the result is just not stored
        tracker.refused.append(f"audit hook {event}: {type(exc).__name__}: {exc}")


def _truth_clock_observer(kind: str, moment: datetime) -> None:
    """AIPOS-F249 件①: clock reads inside a cached derivation (installed into tools.aipos_cli.clock by enable_truth_cache).
    read = a judgment on it may flip any time: valid to the end of that minute (F245 granularity); until = a reported flip."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is None:
        return
    if kind == "read":
        cap = (int(moment.timestamp() // 60) + 1) * 60.0
        if cap < tracker.valid_until:
            tracker.valid_until = cap
        if tracker.minute_cap is None:
            frame = _sys._getframe(2)  # the caller of clock.utc_now (or of iso_z / iso_ms / file_slug one level up)
            names = []
            for _ in range(3):
                if frame is None:
                    break
                names.append(f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}:{frame.f_code.co_name}")
                frame = frame.f_back
            tracker.minute_cap = " <- ".join(names)
    elif kind == "until":
        stamp = moment.timestamp()
        if stamp < tracker.valid_until:
            tracker.valid_until = stamp


def enable_truth_cache(enabled: bool = True) -> None:
    """Board server switch (web.board.app.run_server). Installs the os.stat recorder, the audit hook and the clock observer once per
    process (audit hooks cannot be removed; all three only read a ContextVar outside a cached derivation); enabled=False = compute
    every call again, stop the background refresher and drop the stored results, the served pages and the counters."""
    if enabled:
        _install_truth_hooks()
    else:
        stop_truth_refresher()
    with _TRUTH_LOCK:
        _TRUTH_STATE["enabled"] = bool(enabled)
        if not enabled:
            _TRUTH_ENTRIES.clear()
            _TRUTH_PAGES.clear()
            _TRUTH_REFRESH["last"] = {}
            _reset_truth_stats()


def truth_cache_stats() -> dict[str, Any]:
    """Counters for the board perf log (hits / misses / stored / not stored and the last reason, the last truth seen changed, entries;
    AIPOS-F249: waits on an in-flight derivation, background re-derivations and their seconds, results bounded to the minute by a plain
    clock read and the last such read's call site, served pages kept fresh)."""
    with _TRUTH_LOCK:
        return {**_TRUTH_STATS, "entries": len(_TRUTH_ENTRIES), "pages": len(_TRUTH_PAGES), "enabled": bool(_TRUTH_STATE["enabled"]),
                "refresher": _TRUTH_REFRESH["thread"] is not None}


@contextmanager
def truth_external_read(inputs: Callable[[], Any] | None = None) -> Iterator[None]:
    """Declare the subprocess(es) run inside as accounted for: their outcome is fixed by the paths ``inputs()`` returns (recorded
    like a stat, missing ones as missing; only called inside a cached derivation) or immutable (no inputs, e.g. the tree of a full
    commit sha once found). Outside a cached derivation = no-op."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is None:
        yield
        return
    for path in (inputs() if inputs is not None else ()):
        _record_path(tracker, path)
    tracker.external += 1
    try:
        yield
    finally:
        tracker.external -= 1


def truth_git_ignore_inputs(base: Path, paths: Any) -> list[Path]:
    """The files `git -C <base> check-ignore` reads to answer for ``paths`` (for truth_external_read): every .gitignore from the
    work tree top down to each path's directory, the repository's info/exclude, config and index (tracked files are never reported
    ignored), and the user / system git config and default global excludes file. A customised core.excludesFile target is not
    followed (its config line is: editing it invalidates). Not inside a work tree = the walked .git candidates (all missing)."""
    from tools.aipos_cli.workspace_config import git_dir_of

    base = Path(base).absolute()
    top = next((d for d in (base, *base.parents) if (d / ".git").exists()), None)
    if top is None:
        return [d / ".git" for d in (base, *base.parents)]
    inputs: list[Path] = [top / ".git"]
    dirs: set[Path] = set()
    for target in [base, *(Path(p).absolute() for p in paths)]:
        for d in (target, *target.parents):
            if d == top or top in d.parents:
                dirs.add(d)
    inputs += [d / ".gitignore" for d in sorted(dirs)]
    gdir = git_dir_of(top)
    if gdir is not None:
        common = gdir
        pointer = gdir / "commondir"
        if pointer.is_file():
            common = (gdir / pointer.read_text(encoding="utf-8").strip()).resolve()
        inputs += [gdir / "index", gdir / "commondir", common / "info" / "exclude", common / "config"]
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    inputs += [Path.home() / ".gitconfig", xdg / "git" / "config", xdg / "git" / "ignore", Path("/etc/gitconfig")]
    return inputs


def truth_refuse(reason: str) -> None:
    """The current cached derivation (if any) must not be stored (its outcome depends on something it cannot fingerprint)."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is not None:
        tracker.refused.append(str(reason))


@contextmanager
def truth_untracked() -> Iterator[None]:
    """Suspend path-by-path recording (the caller notes its own fingerprint instead, truth_note_fingerprint)."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is None:
        yield
        return
    tracker.quiet += 1
    try:
        yield
    finally:
        tracker.quiet -= 1


def truth_note_fingerprint(key: Any, value: Any, recompute: Callable[[], Any], newest_ns: int = 0) -> None:
    """Record an explicit fingerprint (value = recompute() at read time; newest_ns = its newest mtime for the racy window)."""
    tracker = _TRUTH_TRACKER.get()
    if tracker is not None:
        tracker.fingerprints.setdefault(key, (value, recompute, int(newest_ns)))


def _pass_memo() -> dict[Any, Any]:
    memo = _TRUTH_PASS.get()
    return memo if memo is not None else {}


def _entry_stale(entry: dict[str, Any], memo: dict[Any, Any]) -> str | None:
    """The first recorded truth that is no longer what the derivation read (None = all unchanged). Identities / contents are checked
    once per pass (memo); a nested derivation is checked (re-derived when its own truth changed) and compared by value digest."""
    if _time.time() >= entry["valid_until"]:
        return "time judgment flipped" if entry["minute_cap"] is None else f"next minute ({entry['minute_cap']})"
    for key, ident in entry["stats"].items():
        now = memo.get(key, _MISSING)
        if now is _MISSING:
            now = memo[key] = _path_identity(key[0], key[1])
        if now != ident:
            return key[0]
    for key, digest in entry["young"].items():
        ckey = ("content", key)
        now = memo.get(ckey, _MISSING)
        if now is _MISSING:
            now = memo[ckey] = _content_digest(key[0], key[1])
        if now != digest:
            return f"{key[0]} (content)"
    for key, (value, recompute, _newest) in entry["fingerprints"].items():
        fkey = ("fingerprint", key)
        now = memo.get(fkey, _MISSING)
        if now is _MISSING:
            now = memo[fkey] = recompute()
        if now != value:
            return str(key)
    for key, digest in entry["children"].items():
        if _child_digest(key, memo) != digest:
            return f"nested {key[0]}"
    return None


def _child_digest(key: tuple[Any, ...], memo: dict[Any, Any]) -> bytes | None:
    """The current value digest of a nested derivation (checked once per pass; re-derived when stale, by the stored compute; gone from
    the cache = None, i.e. changed)."""
    mkey = ("nested", key)
    if mkey in memo:
        return memo[mkey]
    with _TRUTH_LOCK:
        entry = _TRUTH_ENTRIES.get(key)
    if entry is None:
        return None
    try:
        got, _fresh = _resolve(key, entry["compute"])
    except Exception as exc:  # noqa: BLE001 - not swallowed: = changed, so the outer derivation runs again and meets this error in its own context
        with _TRUTH_LOCK:
            _TRUTH_STATS["last_changed"] = f"{key[0]}: re-derivation raised {type(exc).__name__}: {exc}"[:300]
        memo[mkey] = None
        return None
    digest = got["digest"] if got["stored"] else None
    memo[mkey] = digest
    return digest


def _frozen(value: Any) -> tuple[bytes | None, bytes]:
    """(stored form, value digest a parent depends on; AIPOS-F249 件③): the pickled value (each caller gets its own copy by unpickling;
    faster than a deep copy for large views) and its digest; a value that cannot be pickled = (None, fresh unique digest: never equal),
    kept as a deep copy instead."""
    try:
        data = _pickle.dumps(value, protocol=5)
    except (_pickle.PicklingError, TypeError, AttributeError, RecursionError):
        return None, os.urandom(16)
    return data, _hashlib.blake2b(data, digest_size=16).digest()


def _thaw(entry: dict[str, Any]) -> Any:
    """A private copy of a stored value."""
    return _pickle.loads(entry["blob"]) if entry["blob"] is not None else copy.deepcopy(entry["value"])


def _unsettled(tracker: _TruthTracker) -> str | None:
    """A recorded truth that cannot be trusted yet: an explicit fingerprint younger than the F177 racy window (re-checked by its own
    stat-based recompute, a same-tick rewrite would not show), or young content that could not be fingerprinted (None = settled)."""
    from tools.aipos_cli.frontmatter import within_racy_window

    for key, (_value, _recompute, newest) in tracker.fingerprints.items():
        if newest and within_racy_window(newest):
            return f"unsettled {key}"
    for key, digest in tracker.young.items():
        if isinstance(digest, tuple) and digest and digest[0] in ("too_large", "error"):
            return f"unsettled {key[0]}"
    return None


def _store_limit() -> None:
    while len(_TRUTH_ENTRIES) > _TRUTH_MAX_ENTRIES:
        _TRUTH_ENTRIES.popitem(last=False)


def _derive(key: tuple[Any, ...], compute: Callable[[], Any], changed: str | None) -> tuple[dict[str, Any], Any]:
    """Run compute() under a fresh recorder; store the result when everything it read could be fingerprinted. -> (entry, value)."""
    tracker = _TruthTracker()
    token = _TRUTH_TRACKER.set(tracker)
    try:
        value = compute()
    finally:
        _TRUTH_TRACKER.reset(token)
    reason = (tracker.refused[0] if tracker.refused else None) or _unsettled(tracker)
    blob, digest = _frozen(value)
    entry = {"stats": tracker.stats, "young": tracker.young, "fingerprints": tracker.fingerprints, "children": tracker.children,
             "valid_until": tracker.valid_until, "minute_cap": tracker.minute_cap, "compute": compute, "digest": digest, "blob": blob,
             "stored": reason is None, "refused": list(tracker.refused) or ([reason] if reason else [])}
    if reason is None and blob is None:
        entry["value"] = copy.deepcopy(value)
    with _TRUTH_LOCK:
        _TRUTH_STATS["misses"] += 1
        if changed is not None:
            _TRUTH_STATS["last_changed"] = f"{key[0]}: {changed}"[:300]
        if tracker.minute_cap is not None:
            _TRUTH_STATS["minute_capped"] += 1
            _TRUTH_STATS["last_minute_cap"] = f"{key[0]}: {tracker.minute_cap}"[:300]
        if reason is not None:
            _TRUTH_STATS["not_stored"] += 1
            _TRUTH_STATS["last_not_stored"] = f"{key[0]}: {reason}"[:300]
        elif _TRUTH_STATE["enabled"]:
            _TRUTH_STATS["stored"] += 1
            _TRUTH_ENTRIES[key] = entry
            _TRUTH_ENTRIES.move_to_end(key)
            _store_limit()
    return entry, value


def _wait_seconds() -> float:
    decl = _TRUTH_REFRESH["decl"]
    return float(decl["max_wait_seconds"]) if decl else 30.0


def _resolve(key: tuple[Any, ...], compute: Callable[[], Any]) -> tuple[dict[str, Any], Any]:
    """The current result for key: the stored one while nothing it depends on changed, else derived again (one derivation per key at a
    time: a stale result being re-derived by another thread = wait for it, bounded, then check again). -> (entry, fresh value | _MISSING)."""
    memo = _pass_memo()
    waited = False
    while True:
        with _TRUTH_LOCK:
            entry = _TRUTH_ENTRIES.get(key)
        changed = None
        if entry is not None:
            changed = _entry_stale(entry, memo)
            if changed is None:
                with _TRUTH_LOCK:
                    _TRUTH_STATS["hits"] += 1
                    if key in _TRUTH_ENTRIES:
                        _TRUTH_ENTRIES.move_to_end(key)
                return entry, _MISSING
        with _TRUTH_LOCK:
            event = _TRUTH_INFLIGHT.get(key)
            owner = event is None
            if owner:
                event = _TRUTH_INFLIGHT[key] = _threading.Event()
        if not owner and not waited:
            event.wait(_wait_seconds())
            waited = True
            with _TRUTH_LOCK:
                _TRUTH_STATS["waited"] += 1
            memo.clear()  # identities seen before the wait are older than what the other derivation recorded
            continue
        try:
            return _derive(key, compute, changed)
        finally:
            if owner:
                with _TRUTH_LOCK:
                    _TRUTH_INFLIGHT.pop(key, None)
                event.set()


def truth_cached(key: tuple[Any, ...], compute: Callable[[], Any], *, page: bool = False) -> Any:
    """AIPOS-F245 件①: compute() through the truth-fingerprinted result cache (see the section comment). Off = compute().
    AIPOS-F249: nested inside another cached derivation = that one depends on this result's value; page=True (a board page's data)
    called from a request (no enclosing derivation, not the refresher) = a served page the background refresher keeps fresh."""
    if not _TRUTH_STATE["enabled"]:
        return compute()
    parent = _TRUTH_TRACKER.get()
    if parent is not None and parent.flat:  # inside truth_record: its record must hold every path read (it is kept outside this process)
        return compute()
    pass_token = _TRUTH_PASS.set({}) if _TRUTH_PASS.get() is None else None
    token = _TRUTH_TRACKER.set(None)
    try:
        entry, fresh = _resolve(key, compute)
    finally:
        _TRUTH_TRACKER.reset(token)
        if pass_token is not None:
            _TRUTH_PASS.reset(pass_token)
    if parent is not None:
        if entry["stored"]:
            parent.children.setdefault(key, entry["digest"])
        else:
            parent.refused.extend(entry["refused"] or [f"nested {key[0]} not stored"])
    elif page and _threading.current_thread() is not _TRUTH_REFRESH["thread"]:
        _note_page(key, compute)
    return fresh if fresh is not _MISSING else _thaw(entry)


# ---------------------------------------------------------------------------
# AIPOS-F249 件②: background refresher (board server only). The most recently served pages (top-level cached derivations, at most
# declared max_pages) are checked every check_interval_seconds; a page whose truth changed (or whose time judgment flipped) is derived
# again in the background, at most once per debounce_seconds (changes inside that window are merged into the next derivation). A
# request that meets a stale page being re-derived waits for it (at most max_wait_seconds, then derives it itself); a request never
# gets a result whose truth is known to have changed. Declared in verbs.schema board_readonly_pages.truth_refresh; missing = no
# refresher (requests derive on demand, as F245).
# ---------------------------------------------------------------------------

_REFRESH_KEYS = ("check_interval_seconds", "debounce_seconds", "max_wait_seconds", "max_pages")


def truth_refresh_declaration(decl: dict[str, Any] | None) -> dict[str, Any] | None:
    """verbs.schema board_readonly_pages.truth_refresh (fail-closed when present: every key a positive number, max_pages an integer);
    None / missing = no background refresher."""
    from tools.schema_loader import SchemaLoadError

    spec = (decl or {}).get("truth_refresh")
    if spec is None:
        return None
    if not isinstance(spec, dict) or any(k not in spec for k in _REFRESH_KEYS):
        raise SchemaLoadError(f"verbs.schema.json board_readonly_pages.truth_refresh 未声明齐 {list(_REFRESH_KEYS)}")
    for k in _REFRESH_KEYS:
        if not isinstance(spec[k], (int, float)) or isinstance(spec[k], bool) or spec[k] <= 0:
            raise SchemaLoadError(f"board_readonly_pages.truth_refresh.{k} 须为正数")
    if not isinstance(spec["max_pages"], int):
        raise SchemaLoadError("board_readonly_pages.truth_refresh.max_pages 须为正整数")
    return spec


def _note_page(key: tuple[Any, ...], compute: Callable[[], Any]) -> None:
    decl = _TRUTH_REFRESH["decl"]
    limit = int(decl["max_pages"]) if decl else 8
    with _TRUTH_LOCK:
        _TRUTH_PAGES[key] = compute
        _TRUTH_PAGES.move_to_end(key)
        while len(_TRUTH_PAGES) > limit:
            _TRUTH_PAGES.popitem(last=False)


def truth_refresh_pass() -> int:
    """One background pass: every served page still current is left alone; a stale one is derived again (unless it was derived in the
    background less than debounce_seconds ago). Returns how many pages were derived. One request-level read scope per pass (the pages
    share the governance roots' card indexes, as one request would)."""
    decl = _TRUTH_REFRESH["decl"] or {"debounce_seconds": 5}
    with _TRUTH_LOCK:
        pages = list(_TRUTH_PAGES.items())
    derived = 0
    token = _TRUTH_PASS.set({})
    try:
        with read_request_scope():
            for key, compute in reversed(pages):  # most recently served first
                with _TRUTH_LOCK:
                    entry = _TRUTH_ENTRIES.get(key)
                if entry is not None and _entry_stale(entry, _pass_memo()) is None:
                    continue
                last = _TRUTH_REFRESH["last"].get(key)
                if last is not None and _time.monotonic() - last < float(decl["debounce_seconds"]):
                    continue
                started = _time.perf_counter()
                _resolve(key, compute)
                _TRUTH_REFRESH["last"][key] = _time.monotonic()
                derived += 1
                with _TRUTH_LOCK:
                    _TRUTH_STATS["refreshed"] += 1
                    _TRUTH_STATS["refresh_seconds"] = round(_TRUTH_STATS["refresh_seconds"] + _time.perf_counter() - started, 3)
    finally:
        _TRUTH_PASS.reset(token)
    return derived


def _refresh_loop(stop: _threading.Event, interval: float) -> None:
    while not stop.wait(interval):
        try:
            truth_refresh_pass()
        except Exception as exc:  # noqa: BLE001 - the refresher thread must survive; requests still derive on demand (named on stderr)
            print(f"Warning: board truth refresher pass failed (requests derive on demand): {type(exc).__name__}: {exc}",
                  file=_sys.stderr, flush=True)


def start_truth_refresher(declaration: dict[str, Any] | None) -> bool:
    """Start the background refresher (board server, after enable_truth_cache). declaration = truth_refresh_declaration(...); None =
    not started (False). Idempotent."""
    if declaration is None or not _TRUTH_STATE["enabled"]:
        return False
    with _TRUTH_LOCK:
        if _TRUTH_REFRESH["thread"] is not None:
            return True
        _TRUTH_REFRESH["decl"] = dict(declaration)
        stop = _threading.Event()
        thread = _threading.Thread(target=_refresh_loop, args=(stop, float(declaration["check_interval_seconds"])),
                                   name="board-truth-refresh", daemon=True)
        _TRUTH_REFRESH.update(thread=thread, stop=stop)
    thread.start()
    return True


def stop_truth_refresher() -> None:
    with _TRUTH_LOCK:
        thread, stop = _TRUTH_REFRESH["thread"], _TRUTH_REFRESH["stop"]
        _TRUTH_REFRESH.update(thread=None, stop=None, decl=None)
    if stop is not None:
        stop.set()
    if thread is not None and thread is not _threading.current_thread():
        thread.join(timeout=60)


# ---------------------------------------------------------------------------
# AIPOS-F247 件③: the same truth recorder for a derivation whose result is kept OUTSIDE this process (the history search index,
# a disposable derived cache on disk): truth_record runs compute() under the F245 recorder and hands back what it read as plain
# data (path, mode, identity), so the caller can persist it next to the result; truth_identity_changed re-checks such records with the
# same identity judgement (_path_identity) and truth_unsettled with the same racy window. The recorder hooks are installed on first
# use (enable_truth_cache's own installer); the in-process result cache itself stays off unless the board server switched it on.
# ---------------------------------------------------------------------------

def _install_truth_hooks() -> None:
    from tools.aipos_cli import clock

    with _TRUTH_LOCK:
        if not _TRUTH_STATE["hooks"]:
            os.stat = _tracked_stat
            _sys.addaudithook(_truth_audit_hook)
            clock._observer = _truth_clock_observer
            _TRUTH_STATE["hooks"] = True


def truth_record(compute: Callable[[], Any], *, keep: Callable[[str, Any], bool] | None = None) -> tuple[Any, dict[str, Any]]:
    """Run compute() recording every piece of truth it reads (same recorder as truth_cached) -> (value, truth).
    truth = {"stats": [[absolute path, mode, identity | None], ...] (only those keep(path, mode) accepts; mode True stat / False lstat /
    "list" listing), "refused": first reason the result must not be kept (an undeclared subprocess / socket, an explicit fingerprint
    that cannot be re-checked outside this process) or None, "unsettled": a kept truth younger than the racy window or None}.
    Not nested into an enclosing truth_cached derivation's record (the caller keeps its own)."""
    _install_truth_hooks()
    tracker = _TruthTracker()
    tracker.flat = True
    token = _TRUTH_TRACKER.set(tracker)
    try:
        value = compute()
    finally:
        _TRUTH_TRACKER.reset(token)
    refused = tracker.refused[0] if tracker.refused else None
    flat = _flatten_nested(tracker.children)  # AIPOS-F249: a memo of this request may carry nested cached results by digest
    if flat is None:
        refused = refused or "a nested cached result is no longer in this process (its truth cannot be listed)"
    else:
        for key, ident in flat.items():
            tracker.stats.setdefault(key, ident)
    kept = {key: ident for key, ident in tracker.stats.items() if keep is None or keep(key[0], key[1])}
    if refused is None and tracker.fingerprints:
        refused = f"explicit fingerprint {next(iter(tracker.fingerprints))!r} is only re-checkable in this process"
    young = next((f"unsettled {path}" for (path, _mode), ident in kept.items() if _young_identity(ident)), None)
    return value, {"stats": [[path, mode, list(ident) if ident is not None else None] for (path, mode), ident in kept.items()],
                   "refused": refused, "unsettled": young}


def _flatten_nested(children: dict[tuple[Any, ...], bytes], seen: set[tuple[Any, ...]] | None = None) -> dict[tuple[str, Any], Any] | None:
    """Every path identity the nested cached results (and theirs) recorded; None = one is gone from the cache or has explicit fingerprints
    / young content (not listable as plain identities)."""
    seen = set() if seen is None else seen
    out: dict[tuple[str, Any], Any] = {}
    for key in children:
        if key in seen:
            continue
        seen.add(key)
        with _TRUTH_LOCK:
            entry = _TRUTH_ENTRIES.get(key)
        if entry is None or entry["fingerprints"] or entry["young"] or entry["valid_until"] != float("inf"):
            return None
        out.update(entry["stats"])
        inner = _flatten_nested(entry["children"], seen)
        if inner is None:
            return None
        out.update(inner)
    return out


def truth_identity_changed(stats: list[Any], seen: dict[tuple[str, Any], Any] | None = None) -> str | None:
    """The first recorded [path, mode, identity] (truth_record) whose identity is no longer the same (None = all unchanged).
    seen = a per-call memo {(path, mode): identity now} shared by many records (each path is checked once per call)."""
    memo = seen if seen is not None else {}
    for path, mode, ident in stats:
        key = (str(path), mode)
        if key not in memo:
            memo[key] = _path_identity(key[0], mode)
        now = memo[key]
        if (list(now) if now is not None else None) != ident:
            return key[0]
    return None


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
