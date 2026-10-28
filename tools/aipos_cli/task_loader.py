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
    sub.external, sub.quiet = tracker.external, tracker.quiet
    token = _TRUTH_TRACKER.set(sub)
    try:
        value = compute()
    finally:
        _TRUTH_TRACKER.reset(token)
        tracker.merge(sub.stats, sub.fingerprints, sub.refused)
    memo[key] = (value, (sub.stats, sub.fingerprints, sub.refused))
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

        return list(_scope_memo(scope["by_states"], tuple(states or QUEUE_STATES), build).get(wanted, []))
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
#
# A cached derivation (truth_cached) is stored with the fingerprint of every piece of truth it read, recorded while it ran:
#   - every path it stat-ed (os.stat / Path.exists / is_file / is_dir / the F177 per-file read caches) or opened (audit event
#     "open"), with the F177 level-1 stat identity of a file (frontmatter._stat_identity: dev, ino, size, mtime_ns, ctime_ns); a
#     directory only stat-ed = "is a directory" (its entries are not what was read); a missing path is recorded as missing;
#   - every directory it listed (audit events "os.listdir" / "os.scandir": glob, iterdir, walk) = (dev, ino, mtime_ns), so an
#     added / removed / renamed entry changes it;
#   - explicit fingerprints computed by their own single implementation (truth_note_fingerprint: the F55 / F229 records group
#     fingerprint records._group_fingerprint, whose scan is not recorded path by path);
#   - the wall-clock minute it started in (时间相关项按分钟粒度纳入: overdue / envelope expiry / stale days are never served
#     from an earlier minute).
# A stored result is returned only while the minute is the same and every recorded identity / fingerprint is unchanged (checked
# on every request, so any truth change is reflected by the very next request; a file the derivation itself writes is recorded as
# it was before the open, so a writing derivation is never served from the cache and its writes still happen). Fail-closed = not
# stored: a subprocess / socket / thread / signal the derivation was not declared for (truth_external_read), or any truth younger
# than the F177 racy window (frontmatter.within_racy_window: same-tick rewrites can never be served stale).
# Read-scope memos (request_memo / lookup_scope_memo / the card index) keep the truth their value was derived from, so cached
# derivations nested in one request share them and each still records everything it depends on; a nested derivation's
# fingerprint is folded into the enclosing one (the overview contains the inbox, the inbox each card's loop status). Off unless the board server process
# switches it on (enable_truth_cache, web.board.app.run_server): CLI, gate and in-process callers compute every call as before.
# Process memory only, bounded (least-recently-used), one lock; every caller gets its own deep copy.
# ---------------------------------------------------------------------------

import stat as _stat_module
import sys as _sys
import threading as _threading
import time as _time
from collections import OrderedDict as _OrderedDict

_TRUTH_TRACKER: "ContextVar[_TruthTracker | None]" = ContextVar("lybra_truth_tracker", default=None)
_TRUTH_LOCK = _threading.Lock()
_TRUTH_STATE: dict[str, Any] = {"enabled": False, "hooks": False}
_TRUTH_MAX_ENTRIES = 256  # pages + per-project roadmap / card statistics + per in-flight card loop status
_TRUTH_ENTRIES: "_OrderedDict[tuple[Any, ...], dict[str, Any]]" = _OrderedDict()
_TRUTH_STATS: dict[str, Any] = {"hits": 0, "misses": 0, "stored": 0, "not_stored": 0, "last_not_stored": None, "last_changed": None}
_REAL_STAT = os.stat
#: audit events whose outcome is not a file the derivation read: refused unless declared (truth_external_read)
_TRUTH_EXTERNAL_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec", "os.fork",
                                    "os.forkpty", "socket.connect", "os.kill", "os.killpg", "_thread.start_new_thread",
                                    "_thread.start_joinable_thread"})


class _TruthTracker:
    """What one cached derivation read: stats {(absolute path, mode): identity | None} (mode = True stat / False lstat / "list"
    directory listing), fingerprints {key: (value, recompute, newest_ns)}, refused (reasons not to store), external (declared
    subprocess depth), quiet (recording suspended)."""

    __slots__ = ("stats", "fingerprints", "refused", "external", "quiet", "merged")

    def __init__(self) -> None:
        self.stats: dict[tuple[str, Any], tuple[Any, ...] | None] = {}
        self.fingerprints: dict[Any, tuple[Any, Callable[[], Any], int]] = {}
        self.refused: list[str] = []
        self.external = 0
        self.quiet = 0
        self.merged: set[int] = set()  # ids of truth bundles already folded in (a memo reused 4000 times is merged once)

    def merge(self, stats: dict[tuple[str, Any], Any], fingerprints: dict[Any, Any], refused: list[str]) -> None:
        """Fold an inner derivation's truth in (first observation wins: a change between the two reads invalidates next time).
        The same bundle (same stats dict object, kept alive by its memo slot / stored entry) is folded in once."""
        if id(stats) in self.merged:
            return
        self.merged.add(id(stats))
        for key, ident in stats.items():
            self.stats.setdefault(key, ident)
        for key, value in fingerprints.items():
            self.fingerprints.setdefault(key, value)
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


def _record_path(tracker: _TruthTracker, path: Any, mode: Any = True) -> None:
    key = (_abs(path), mode)
    if key not in tracker.stats:
        tracker.stats[key] = _path_identity(key[0], key[1])


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
                _record_path(tracker, name)
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


def enable_truth_cache(enabled: bool = True) -> None:
    """Board server switch (web.board.app.run_server). Installs the os.stat recorder and the audit hook once per process (audit
    hooks cannot be removed; both only read a ContextVar outside a cached derivation); enabled=False = compute every call again
    and drop the stored results and the counters."""
    if enabled:
        _install_truth_hooks()
    with _TRUTH_LOCK:
        _TRUTH_STATE["enabled"] = bool(enabled)
        if not enabled:
            _TRUTH_ENTRIES.clear()
            _TRUTH_STATS.update(hits=0, misses=0, stored=0, not_stored=0, last_not_stored=None, last_changed=None)


def truth_cache_stats() -> dict[str, Any]:
    """Counters for the board perf log (hits / misses / stored / not stored and the last reason, the last truth seen changed, entries)."""
    with _TRUTH_LOCK:
        return {**_TRUTH_STATS, "entries": len(_TRUTH_ENTRIES), "enabled": bool(_TRUTH_STATE["enabled"])}


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


def _truth_changed(entry: dict[str, Any]) -> str | None:
    """The first recorded truth that is no longer what the derivation read (None = all unchanged)."""
    for (path, mode), ident in entry["stats"].items():
        if _path_identity(path, mode) != ident:
            return path
    for key, (value, recompute, _newest) in entry["fingerprints"].items():
        if recompute() != value:
            return str(key)
    return None


def _unsettled(tracker: _TruthTracker) -> str | None:
    """A recorded truth younger than the F177 racy window (frontmatter.within_racy_window; None = settled)."""
    from tools.aipos_cli.frontmatter import within_racy_window

    for (path, _mode), ident in tracker.stats.items():
        if ident and ident[0] == "listing" and within_racy_window(ident[3]):
            return f"unsettled {path}"
        if ident and ident[0] == "file" and within_racy_window(max(ident[4], ident[5])):
            return f"unsettled {path}"
    for key, (_value, _recompute, newest) in tracker.fingerprints.items():
        if newest and within_racy_window(newest):
            return f"unsettled {key}"
    return None


def truth_cached(key: tuple[Any, ...], compute: Callable[[], Any]) -> Any:
    """AIPOS-F245 件①: compute() through the truth-fingerprinted result cache (see the section comment). Off = compute()."""
    if not _TRUTH_STATE["enabled"]:
        return compute()
    parent = _TRUTH_TRACKER.get()
    minute = int(_time.time() // 60)
    with _TRUTH_LOCK:
        entry = _TRUTH_ENTRIES.get(key)
    changed = None if entry is None else ("next minute" if entry["minute"] != minute else _truth_changed(entry))
    if entry is not None and changed is None:
        with _TRUTH_LOCK:
            _TRUTH_STATS["hits"] += 1
            if key in _TRUTH_ENTRIES:
                _TRUTH_ENTRIES.move_to_end(key)
        if parent is not None:
            parent.merge(entry["stats"], entry["fingerprints"], [])
        return copy.deepcopy(entry["value"])
    tracker = _TruthTracker()
    token = _TRUTH_TRACKER.set(tracker)
    try:
        value = compute()
    finally:
        _TRUTH_TRACKER.reset(token)
        if parent is not None:
            parent.merge(tracker.stats, tracker.fingerprints, tracker.refused)
    reason = (tracker.refused[0] if tracker.refused else None) or _unsettled(tracker)
    stored = {"minute": minute, "stats": tracker.stats, "fingerprints": tracker.fingerprints, "value": copy.deepcopy(value)} \
        if reason is None else None
    with _TRUTH_LOCK:
        _TRUTH_STATS["misses"] += 1
        if changed is not None:
            _TRUTH_STATS["last_changed"] = f"{key[0]}: {changed}"[:300]
        if stored is None:
            _TRUTH_STATS["not_stored"] += 1
            _TRUTH_STATS["last_not_stored"] = f"{key[0]}: {reason}"[:300]
        elif _TRUTH_STATE["enabled"]:
            _TRUTH_STATS["stored"] += 1
            _TRUTH_ENTRIES[key] = stored
            _TRUTH_ENTRIES.move_to_end(key)
            while len(_TRUTH_ENTRIES) > _TRUTH_MAX_ENTRIES:
                _TRUTH_ENTRIES.popitem(last=False)
    return value


# ---------------------------------------------------------------------------
# AIPOS-F247 件③: the same truth recorder for a derivation whose result is kept OUTSIDE this process (the history search index,
# a disposable derived cache on disk): truth_record runs compute() under the F245 recorder and hands back what it read as plain
# data (path, mode, identity), so the caller can persist it next to the result; truth_identity_changed re-checks such records with the
# same identity judgement (_path_identity) and truth_unsettled with the same racy window. The recorder hooks are installed on first
# use (enable_truth_cache's own installer); the in-process result cache itself stays off unless the board server switched it on.
# ---------------------------------------------------------------------------

def _install_truth_hooks() -> None:
    with _TRUTH_LOCK:
        if not _TRUTH_STATE["hooks"]:
            os.stat = _tracked_stat
            _sys.addaudithook(_truth_audit_hook)
            _TRUTH_STATE["hooks"] = True


def truth_record(compute: Callable[[], Any], *, keep: Callable[[str, Any], bool] | None = None) -> tuple[Any, dict[str, Any]]:
    """Run compute() recording every piece of truth it reads (same recorder as truth_cached) -> (value, truth).
    truth = {"stats": [[absolute path, mode, identity | None], ...] (only those keep(path, mode) accepts; mode True stat / False lstat /
    "list" listing), "refused": first reason the result must not be kept (an undeclared subprocess / socket, an explicit fingerprint
    that cannot be re-checked outside this process) or None, "unsettled": a kept truth younger than the racy window or None}.
    Not nested into an enclosing truth_cached derivation's record (the caller keeps its own)."""
    _install_truth_hooks()
    tracker = _TruthTracker()
    token = _TRUTH_TRACKER.set(tracker)
    try:
        value = compute()
    finally:
        _TRUTH_TRACKER.reset(token)
    kept = {key: ident for key, ident in tracker.stats.items() if keep is None or keep(key[0], key[1])}
    refused = tracker.refused[0] if tracker.refused else None
    if refused is None and tracker.fingerprints:
        refused = f"explicit fingerprint {next(iter(tracker.fingerprints))!r} is only re-checkable in this process"
    view = _TruthTracker()
    view.stats = kept
    return value, {"stats": [[path, mode, list(ident) if ident is not None else None] for (path, mode), ident in kept.items()],
                   "refused": refused, "unsettled": _unsettled(view)}


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
