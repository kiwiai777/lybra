from __future__ import annotations

import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.task_loader import _serialize_dates
from tools.schema_constants import RecordType

# ---------------------------------------------------------------------------
# AIPOS-F55: 进程内缓存 + 子目录增量 + 按需加载
#
# 唯一缓存实现(Owner 防碎片化红线①):挂在 load_records 唯一入口, 禁在
# board_adapter / mcp_server / CLI 另加一层。
# 失效判据 = 文件系统事实(每个子目录的 (relpath, mtime_ns, size) 指纹; 红线⑤:
# 禁手工版本号/写入点通知)——同进程写后立即可读与跨进程可见同源(红线①②)。
# 防半写(红线③):组内扫描前后双指纹, 不一致则重扫一次(有界重试)。
# 零新持久物(红线②):缓存只在进程内存, 进程退出即失效; 不写索引文件/不建缓存目录。
# ---------------------------------------------------------------------------

_RECORDS_CACHE_LOCK = threading.RLock()
# key=(repo_root_str, group) → (fingerprint, parsed_records_list, hand_written_warnings)
_RECORDS_GROUP_CACHE: dict[tuple[str, str], tuple[tuple, list[dict[str, Any]], list[str]]] = {}

#: 分组名 → (子目录名, 记录构造方式)。standard 类直接用 kind 作为 record_type;
#: owner_decisions 为平铺目录(无任务子目录), owner_verification/owner_decision 为专用构造器。
_GROUP_KINDS: dict[str, tuple[str, str]] = {
    "sessions": ("sessions", "session"),
    "publishes": ("publishes", RecordType.PUBLISH),
    "claims": ("claims", RecordType.CLAIM),
    "returns": ("returns", RecordType.RETURN),
    "audit_dispatches": ("audit_dispatches", RecordType.AUDIT_DISPATCH),
    "audit_verdicts": ("audit_verdicts", RecordType.AUDIT_VERDICT),
    "owner_decisions": ("owner_decisions", "owner_decision"),
    "owner_verifications": ("owner_verifications", "owner_verification"),
    "closures": ("closures", RecordType.CLOSURE),
}


def _group_fingerprint(group_root: Path) -> tuple | None:
    """组指纹 = 按 (相对路径, mtime_ns, size) 排序元组(纯文件系统事实, 跨进程生效)。

    None = 目录不存在。成本 = 一次 scandir 遍历(不读文件内容), 千级文件毫秒量级。
    """
    if not group_root.is_dir():
        return None
    entries: list[tuple[str, int, int]] = []
    stack = [group_root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                        entries.append((entry.path, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    entries.sort()
    return tuple(entries)


def _build_group_records(
    repo_root: Path,
    group_root: Path,
    group: str,
    kind: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """解析单个分组(语义与 F55 前完全一致, 含裁决门生过滤)。"""
    if kind == "owner_decision":
        return (
            [_build_owner_decision_record(path, repo_root) for path in _iter_owner_decision_files(group_root)],
            [],
        )
    if kind == "owner_verification":
        return (
            [
                _build_owner_verification_record(path, repo_root, directory_task_id)
                for path, directory_task_id in _iter_record_files(group_root)
            ],
            [],
        )
    records = [
        _build_record(path, repo_root, kind, directory_task_id)
        for path, directory_task_id in _iter_record_files(group_root)
    ]
    if group == "audit_verdicts":
        from tools.aipos_cli.audit_helpers import is_gate_born_verdict_record

        kept: list[dict[str, Any]] = []
        hand_written: list[str] = []
        for rec in records:
            if is_gate_born_verdict_record(rec):
                kept.append(rec)
            else:
                hand_written.append(
                    f"hand-written verdict ignored: {rec.get('path', '?')} "
                    f"(缺少门生标记 record_type/verdict_id/verdict_at;"
                    f"裁决只经门产生,勿手写落盘)"
                )
        return kept, hand_written
    return records, []


def _load_group_cached(repo_root: Path, group: str) -> tuple[list[dict[str, Any]], list[str]]:
    """带指纹缓存的分组加载(增量: 未变组复用, 变更组重扫+双指纹防半写)。"""
    subdir, kind = _GROUP_KINDS[group]
    group_root = repo_root / "5_tasks" / "records" / subdir
    cache_key = (str(repo_root), group)
    records: list[dict[str, Any]] = []
    hand_written: list[str] = []
    for _attempt in range(2):  # 红线③: 双指纹有界重试
        fp_before = _group_fingerprint(group_root)
        if fp_before is None:
            with _RECORDS_CACHE_LOCK:
                _RECORDS_GROUP_CACHE.pop(cache_key, None)
            return [], []
        with _RECORDS_CACHE_LOCK:
            cached = _RECORDS_GROUP_CACHE.get(cache_key)
            if cached is not None and cached[0] == fp_before:
                return list(cached[1]), list(cached[2])
        records, hand_written = _build_group_records(repo_root, group_root, group, kind)
        fp_after = _group_fingerprint(group_root)
        if fp_after == fp_before:
            with _RECORDS_CACHE_LOCK:
                _RECORDS_GROUP_CACHE[cache_key] = (fp_before, records, hand_written)
            return list(records), list(hand_written)
        # 扫描期间组内发生变更(可能读到半写): 重扫一次
    return records, hand_written


def clear_records_cache() -> None:
    """清空进程内记录缓存(测试靶场用; 生产路径靠指纹自动失效)。"""
    with _RECORDS_CACHE_LOCK:
        _RECORDS_GROUP_CACHE.clear()


def expected_session_record_path(repo_root: Path, task_id: str, session_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "sessions" / task_id / f"{session_id}.md"


def expected_claim_log_path(repo_root: Path, task_id: str, claim_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "claims" / task_id / f"{claim_id}.md"


def expected_publish_record_path(repo_root: Path, task_id: str, publish_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "publishes" / task_id / f"{publish_id}.md"


def expected_return_record_path(repo_root: Path, task_id: str, return_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "returns" / task_id / f"{return_id}.md"


def expected_audit_dispatch_record_path(repo_root: Path, task_id: str, dispatch_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "audit_dispatches" / task_id / f"{dispatch_id}.md"


def expected_audit_verdict_record_path(repo_root: Path, task_id: str, verdict_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{verdict_id}.md"


def expected_owner_verification_record_path(repo_root: Path, task_id: str, filename: str) -> Path:
    return repo_root / "5_tasks" / "records" / "owner_verifications" / task_id / filename


def expected_closure_record_path(repo_root: Path, task_id: str, closure_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "closures" / task_id / f"{closure_id}.md"


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    metadata = record.get("metadata", {})
    timestamp = (
        metadata.get("created_at")
        or metadata.get("published_at")
        or metadata.get("session_started_at")
        or metadata.get("claimed_at")
        or metadata.get("returned_at")
        or metadata.get("dispatched_at")
        or metadata.get("verdict_at")
        or metadata.get("decided_at")
        or ""
    )
    return (str(timestamp), str(record.get("path") or ""))


def _build_record(
    path: Path,
    repo_root: Path,
    record_type: str,
    directory_task_id: str,
) -> dict[str, Any]:
    parse_errors: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        metadata: dict[str, Any] = {}
        body = ""
        parse_errors.append(f"Read failed: {exc}")
    else:
        metadata, body, parse_warnings = parse_markdown_frontmatter(text)
        parse_errors.extend(parse_warnings)

    id_field = {
        "session": "session_id",
        RecordType.CLAIM: "claim_id",
        RecordType.PUBLISH: "publish_id",
        RecordType.RETURN: "return_id",
        RecordType.AUDIT_DISPATCH: "dispatch_id",
        RecordType.AUDIT_VERDICT: "verdict_id",
        RecordType.CLOSURE: "closure_id",
    }[record_type]
    task_id = metadata.get("task_id") or directory_task_id
    record_id = metadata.get(id_field) or path.stem

    if metadata.get("task_id") and metadata.get("task_id") != directory_task_id:
        warnings.append(
            f"{record_type} record task_id mismatch: directory={directory_task_id} metadata={metadata.get('task_id')}"
        )
    if metadata.get(id_field) and metadata.get(id_field) != path.stem:
        warnings.append(
            f"{record_type} record filename mismatch: filename={path.stem} metadata={metadata.get(id_field)}"
        )

    # AIPOS-289: merge frontmatter warnings (governance account drift) into record warnings
    frontmatter_warnings = metadata.get("warnings")
    if isinstance(frontmatter_warnings, list):
        warnings.extend(frontmatter_warnings)

    record = {
        "record_type": record_type,
        "record_id": record_id,
        "task_id": task_id,
        "path": str(path.relative_to(repo_root)),
        "metadata": metadata,
        "body": body,
        "parse_errors": parse_errors,
        "warnings": warnings,
    }
    if record_type == "session":
        record.update(
            {
                "session_id": record_id,
                "session_status": metadata.get("session_status") or metadata.get("status"),
                "claim_id": metadata.get("claim_id"),
                "created_at": metadata.get("created_at") or metadata.get("session_started_at"),
                # AIPOS-255 F-BOARD-2: expose actor for timeline rendering
                "actor": metadata.get("actor"),
            }
        )
    elif record_type == RecordType.CLAIM:
        record.update(
            {
                "claim_id": record_id,
                "session_id": metadata.get("session_id"),
                "claimed_by": metadata.get("claimed_by") or metadata.get("actor"),
                "claimed_at": metadata.get("claimed_at") or metadata.get("created_at"),
                "claim_source": metadata.get("claim_source"),
            }
        )
    elif record_type == RecordType.PUBLISH:
        record.update(
            {
                "publish_id": record_id,
                "actor": metadata.get("actor") or metadata.get("published_by"),
                "published_by": metadata.get("published_by") or metadata.get("actor"),
                "published_at": metadata.get("published_at") or metadata.get("created_at"),
                "source_draft_ref": metadata.get("source_draft_ref"),
                "published_task_ref": metadata.get("published_task_ref"),
            }
        )
    elif record_type == RecordType.RETURN:
        record.update(
            {
                "return_id": record_id,
                "claim_id": metadata.get("claim_id"),
                "session_id": metadata.get("session_id"),
                "returned_by": metadata.get("returned_by") or metadata.get("actor"),
                "returned_at": metadata.get("returned_at") or metadata.get("created_at"),
                "executor_status": metadata.get("executor_status"),
                "audit_readiness": metadata.get("audit_readiness"),
                # AIPOS-255 F-BOARD-2: expose actor for timeline rendering
                "actor": metadata.get("actor") or metadata.get("returned_by"),
            }
        )
    elif record_type == RecordType.AUDIT_DISPATCH:
        record.update(
            {
                "dispatch_id": record_id,
                "reviewed_task_id": metadata.get("reviewed_task_id") or task_id,
                "audit_task_id": metadata.get("audit_task_id"),
                "reviewed_executor_instance": metadata.get("reviewed_executor_instance"),
                "reviewed_return_record_ref": metadata.get("reviewed_return_record_ref"),
                "dispatched_at": metadata.get("dispatched_at"),
                # AIPOS-255 F-BOARD-2: expose actor for timeline rendering
                "actor": metadata.get("actor"),
            }
        )
    elif record_type == RecordType.CLOSURE:
        record.update(
            {
                "closure_id": record_id,
                "closed_at": metadata.get("closed_at"),
                "closure_evidence_type": metadata.get("closure_evidence_type"),
                "closure_evidence_ref": metadata.get("closure_evidence_ref"),
                "return_record_ref": metadata.get("return_record_ref"),
                "actor": metadata.get("actor"),
            }
        )
    else:
        record.update(
            {
                "verdict_id": record_id,
                "verdict": metadata.get("verdict"),
                "reviewed_task_id": metadata.get("reviewed_task_id") or task_id,
                "audit_task_id": metadata.get("audit_task_id"),
                "reviewed_executor_instance": metadata.get("reviewed_executor_instance"),
                "auditor_instance": metadata.get("auditor_instance"),
                "verdict_at": metadata.get("verdict_at"),
                # AIPOS-255 F-BOARD-2: expose actor for timeline rendering
                "actor": metadata.get("actor") or metadata.get("auditor_instance"),
            }
        )
    # AIPOS-R1-FIX2: 转换所有 date/datetime 对象为 ISO 字符串
    return _serialize_dates(record)


def _iter_record_files(root: Path, task_filter: str | None = None) -> list[tuple[Path, str]]:
    if not root.exists():
        return []
    files: list[tuple[Path, str]] = []
    task_dirs = (
        [root / task_filter] if task_filter else sorted(path for path in root.iterdir() if path.is_dir())
    )
    for task_dir in task_dirs:
        if not task_dir.is_dir():
            continue
        for path in sorted(task_dir.iterdir()):
            if path.is_file() and path.suffix == ".md":
                files.append((path, task_dir.name))
    return files


def _build_owner_decision_record(path: Path, repo_root: Path) -> dict[str, Any]:
    parse_errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        metadata: dict[str, Any] = {}
        body = ""
        parse_errors.append(f"Read failed: {exc}")
    else:
        metadata, body, parse_warnings = parse_markdown_frontmatter(text)
        parse_errors.extend(parse_warnings)

    decision_id = metadata.get("decision_id") or path.stem
    warnings: list[str] = []
    if metadata.get("decision_id") and metadata.get("decision_id") != path.stem:
        warnings.append(
            f"owner decision record filename mismatch: filename={path.stem} metadata={metadata.get('decision_id')}"
        )
    if metadata.get("record_type") not in (None, RecordType.OWNER_DECISION_RECORD):
        warnings.append(f"owner decision record_type mismatch: {metadata.get('record_type')}")

    return _serialize_dates({
        "record_type": RecordType.OWNER_DECISION_RECORD,
        "record_id": decision_id,
        "decision_id": decision_id,
        "decision_type": metadata.get("decision_type"),
        "decision_status": metadata.get("decision_status"),
        "decided_at": metadata.get("decided_at"),
        "decided_by_ref": metadata.get("decided_by_ref"),
        "captured_by": metadata.get("captured_by"),
        "capture_surface": metadata.get("capture_surface"),
        "project": metadata.get("project"),
        "task_id": metadata.get("task_id"),
        "draft_path": metadata.get("draft_path"),
        "orchestration_id": metadata.get("orchestration_id"),
        "external_ref": metadata.get("external_ref"),
        "approval_operation": metadata.get("approval_operation"),
        "allowed_next_action": metadata.get("allowed_next_action"),
        "evidence_id": metadata.get("evidence_id"),
        "evidence_hash": metadata.get("evidence_hash"),
        "source_tag": metadata.get("source_tag"),
        "client_tag": metadata.get("client_tag"),
        "path": str(path.relative_to(repo_root)),
        "metadata": metadata,
        "body": body,
        "parse_errors": parse_errors,
        "warnings": warnings,
    })


def _iter_owner_decision_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_file() and path.suffix == ".md")


def _build_owner_verification_record(
    path: Path, repo_root: Path, directory_task_id: str
) -> dict[str, Any]:
    """AIPOS-274F1: parse one ``5_tasks/records/owner_verifications/<task_id>/*.md``
    file (written by owner_verification_writer.py — approve/reject, append-only).

    Mirrors the shape of ``_build_record`` for the other record kinds so it can
    ride the same sort/index/find_records_for_task plumbing.
    """
    parse_errors: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        metadata: dict[str, Any] = {}
        body = ""
        parse_errors.append(f"Read failed: {exc}")
    else:
        metadata, body, parse_warnings = parse_markdown_frontmatter(text)
        parse_errors.extend(parse_warnings)

    task_id = metadata.get("task_id") or directory_task_id
    if metadata.get("task_id") and metadata.get("task_id") != directory_task_id:
        warnings.append(
            f"owner_verification record task_id mismatch: directory={directory_task_id} metadata={metadata.get('task_id')}"
        )

    record = {
        "record_type": RecordType.OWNER_VERIFICATION,
        "record_id": path.stem,
        "task_id": task_id,
        "decision": metadata.get("decision"),
        "decided_by": metadata.get("decided_by"),
        "decided_at": metadata.get("decided_at"),
        "decided_via": metadata.get("decided_via"),
        "reason": metadata.get("reason"),
        # AIPOS-255 F-BOARD-2 convention: expose actor for timeline rendering.
        "actor": metadata.get("decided_by"),
        "path": str(path.relative_to(repo_root)),
        "metadata": metadata,
        "body": body,
        "parse_errors": parse_errors,
        "warnings": warnings,
    }
    # AIPOS-R1-FIX2: 转换所有 date/datetime 对象
    return _serialize_dates(record)


def load_records(
    repo_root: Path,
    *,
    groups: Iterable[str] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """加载门记录(唯一入口; AIPOS-F55 起带进程内指纹缓存与增量)。

    默认行为与 F55 前完全一致(红线④):全量加载, 返回结构不变。
    可选参数(均不影响默认):
      groups: 只加载指定分组(如 {"claims", "returns"});未列分组为空集。
      task_id: 只加载该任务名下子目录(标准分组按 <group>/<task_id>/ 取; 平铺的
               owner_decisions 不受此约束)。子集加载不走缓存(子集本身便宜)。
    """
    records_root = repo_root / "5_tasks" / "records"
    sessions_root = records_root / "sessions"
    publishes_root = records_root / "publishes"
    claims_root = records_root / "claims"
    returns_root = records_root / "returns"
    audit_dispatches_root = records_root / "audit_dispatches"
    audit_verdicts_root = records_root / "audit_verdicts"
    owner_decisions_root = records_root / "owner_decisions"
    owner_verifications_root = records_root / "owner_verifications"
    closures_root = records_root / "closures"

    requested = list(groups) if groups is not None else None
    if requested is not None:
        unknown = [g for g in requested if g not in _GROUP_KINDS]
        if unknown:
            raise ValueError(
                f"load_records(groups=...): 未知分组 {unknown}; 合法分组 = {sorted(_GROUP_KINDS)}"
            )
    subset_mode = requested is not None or task_id is not None
    active_groups = set(requested) if requested is not None else set(_GROUP_KINDS)

    def _group(name: str) -> tuple[list[dict[str, Any]], list[str]]:
        if name not in active_groups:
            return [], []
        if subset_mode:
            subdir, kind = _GROUP_KINDS[name]
            group_root = records_root / subdir
            if task_id is not None and kind not in ("owner_decision",):
                # 按需: 只迭代该任务子目录
                if kind == "owner_verification":
                    recs = [
                        _build_owner_verification_record(p, repo_root, tid)
                        for p, tid in _iter_record_files(group_root, task_filter=task_id)
                    ]
                    return recs, []
                recs = [
                    _build_record(p, repo_root, kind, tid)
                    for p, tid in _iter_record_files(group_root, task_filter=task_id)
                ]
                if name == "audit_verdicts":
                    from tools.aipos_cli.audit_helpers import is_gate_born_verdict_record

                    kept = [r for r in recs if is_gate_born_verdict_record(r)]
                    hw = [
                        f"hand-written verdict ignored: {r.get('path', '?')} "
                        f"(缺少门生标记 record_type/verdict_id/verdict_at;"
                        f"裁决只经门产生,勿手写落盘)"
                        for r in recs if not is_gate_born_verdict_record(r)
                    ]
                    return kept, hw
                return recs, []
            return _build_group_records(repo_root, group_root, name, kind)
        return _load_group_cached(repo_root, name)

    sessions, _ = _group("sessions")
    publishes, _ = _group("publishes")
    claims, _ = _group("claims")
    returns, _ = _group("returns")
    audit_dispatches, _ = _group("audit_dispatches")
    audit_verdicts, hand_written_verdict_warnings = _group("audit_verdicts")
    owner_decisions, _ = _group("owner_decisions")
    owner_verifications, _ = _group("owner_verifications")
    closures, _ = _group("closures")

    warnings: list[str] = []
    parse_errors: list[str] = []
    session_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    publish_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    claim_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    return_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audit_dispatch_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audit_verdict_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    owner_decision_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_publishes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_returns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_audit_dispatches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_audit_verdicts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_owner_verifications: dict[str, list[dict[str, Any]]] = defaultdict(list)
    closure_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_closures: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in sessions:
        if record.get("session_id"):
            session_index[str(record["session_id"])].append(record)
        if record.get("task_id"):
            task_sessions[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in publishes:
        if record.get("publish_id"):
            publish_index[str(record["publish_id"])].append(record)
        if record.get("task_id"):
            task_publishes[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in claims:
        if record.get("claim_id"):
            claim_index[str(record["claim_id"])].append(record)
        if record.get("task_id"):
            task_claims[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in returns:
        if record.get("return_id"):
            return_index[str(record["return_id"])].append(record)
        if record.get("task_id"):
            task_returns[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in audit_dispatches:
        if record.get("dispatch_id"):
            audit_dispatch_index[str(record["dispatch_id"])].append(record)
        if record.get("reviewed_task_id"):
            task_audit_dispatches[str(record["reviewed_task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in audit_verdicts:
        if record.get("verdict_id"):
            audit_verdict_index[str(record["verdict_id"])].append(record)
        if record.get("reviewed_task_id"):
            task_audit_verdicts[str(record["reviewed_task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in owner_decisions:
        if record.get("decision_id"):
            owner_decision_index[str(record["decision_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in owner_verifications:
        if record.get("task_id"):
            task_owner_verifications[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in closures:
        if record.get("closure_id"):
            closure_index[str(record["closure_id"])].append(record)
        if record.get("task_id"):
            task_closures[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])

    for record_id, items in session_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate session_id found: {record_id}")
    for record_id, items in publish_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate publish_id found: {record_id}")
    for record_id, items in claim_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate claim_id found: {record_id}")
    for record_id, items in return_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate return_id found: {record_id}")
    for record_id, items in audit_dispatch_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate dispatch_id found: {record_id}")
    for record_id, items in audit_verdict_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate verdict_id found: {record_id}")
    for record_id, items in owner_decision_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate decision_id found: {record_id}")
    for record_id, items in closure_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate closure_id found: {record_id}")

    for items in task_sessions.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_publishes.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_claims.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_returns.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_audit_dispatches.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_audit_verdicts.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_owner_verifications.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_closures.values():
        items.sort(key=_record_sort_key, reverse=True)

    summary = {
        "session_records": len(sessions),
        "publish_records": len(publishes),
        "claim_logs": len(claims),
        "return_records": len(returns),
        "audit_dispatch_records": len(audit_dispatches),
        "audit_verdict_records": len(audit_verdicts),
        "owner_decision_records": len(owner_decisions),
        "owner_verification_records": len(owner_verifications),
        "closure_records": len(closures),
        "tasks_with_session_records": len(task_sessions),
        "tasks_with_publish_records": len(task_publishes),
        "tasks_with_claim_logs": len(task_claims),
        "tasks_with_return_records": len(task_returns),
        "tasks_with_audit_dispatch_records": len(task_audit_dispatches),
        "tasks_with_audit_verdict_records": len(task_audit_verdicts),
        "tasks_with_owner_verification_records": len(task_owner_verifications),
        "tasks_with_closure_records": len(task_closures),
        "parse_errors": len(parse_errors),
    }
    return {
        "scope": "records",
        "summary": summary,
        "records_root": str(records_root.relative_to(repo_root)),
        "records_root_exists": records_root.exists(),
        "sessions_root_exists": sessions_root.exists(),
        "publishes_root_exists": publishes_root.exists(),
        "claims_root_exists": claims_root.exists(),
        "returns_root_exists": returns_root.exists(),
        "audit_dispatches_root_exists": audit_dispatches_root.exists(),
        "audit_verdicts_root_exists": audit_verdicts_root.exists(),
        "owner_decisions_root_exists": owner_decisions_root.exists(),
        "owner_verifications_root_exists": owner_verifications_root.exists(),
        "closures_root_exists": closures_root.exists(),
        "sessions": sorted(sessions, key=_record_sort_key, reverse=True),
        "publishes": sorted(publishes, key=_record_sort_key, reverse=True),
        "claims": sorted(claims, key=_record_sort_key, reverse=True),
        "returns": sorted(returns, key=_record_sort_key, reverse=True),
        "audit_dispatches": sorted(audit_dispatches, key=_record_sort_key, reverse=True),
        "audit_verdicts": sorted(audit_verdicts, key=_record_sort_key, reverse=True),
        "owner_decisions": sorted(owner_decisions, key=_record_sort_key, reverse=True),
        "owner_verifications": sorted(owner_verifications, key=_record_sort_key, reverse=True),
        "closures": sorted(closures, key=_record_sort_key, reverse=True),
        "warnings": warnings,
        "parse_errors": parse_errors,
        "session_index": dict(session_index),
        "publish_index": dict(publish_index),
        "claim_index": dict(claim_index),
        "return_index": dict(return_index),
        "audit_dispatch_index": dict(audit_dispatch_index),
        "audit_verdict_index": dict(audit_verdict_index),
        "owner_decision_index": dict(owner_decision_index),
        "task_sessions": dict(task_sessions),
        "task_publishes": dict(task_publishes),
        "task_claims": dict(task_claims),
        "task_returns": dict(task_returns),
        "task_audit_dispatches": dict(task_audit_dispatches),
        "task_audit_verdicts": dict(task_audit_verdicts),
        "task_owner_verifications": dict(task_owner_verifications),
        "closure_index": dict(closure_index),
        "task_closures": dict(task_closures),
        "hand_written_verdict_warnings": hand_written_verdict_warnings,
    }


def find_records_for_task(records: dict[str, Any], task_id: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "sessions": list(records.get("task_sessions", {}).get(task_id, [])),
        "publishes": list(records.get("task_publishes", {}).get(task_id, [])),
        "claims": list(records.get("task_claims", {}).get(task_id, [])),
        "returns": list(records.get("task_returns", {}).get(task_id, [])),
        "audit_dispatches": list(records.get("task_audit_dispatches", {}).get(task_id, [])),
        "audit_verdicts": list(records.get("task_audit_verdicts", {}).get(task_id, [])),
        "owner_verifications": list(records.get("task_owner_verifications", {}).get(task_id, [])),
        "closures": list(records.get("task_closures", {}).get(task_id, [])),
    }


def _check_ref(
    ref_name: str,
    task_id: str | None,
    record_id: Any,
    record_type: str,
    records: dict[str, Any],
    *,
    reviewed_task_id: str | None = None,
    audit_task_id: str | None = None,
) -> dict[str, Any]:
    if not record_id:
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": None,
            "status": "absent",
            "level": "info",
            "message": f"{ref_name} not set",
            "matches": [],
        }

    index_name = {
        "session": "session_index",
        RecordType.CLAIM: "claim_index",
        RecordType.RETURN: "return_index",
        RecordType.AUDIT_DISPATCH: "audit_dispatch_index",
        RecordType.AUDIT_VERDICT: "audit_verdict_index",
    }[record_type]
    matches = list(records.get(index_name, {}).get(str(record_id), []))
    normalized_matches = [
        {
            "path": item.get("path"),
            "task_id": item.get("task_id"),
            "reviewed_task_id": item.get("reviewed_task_id"),
            "audit_task_id": item.get("audit_task_id"),
            "record_id": item.get("record_id"),
            "parse_errors": item.get("parse_errors", []),
        }
        for item in matches
    ]
    if not matches:
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": record_id,
            "status": "missing",
            "level": "warn",
            "message": f"{ref_name} references missing {record_type} record",
            "matches": [],
        }

    if any(
        not _record_ref_matches_task_context(
            item,
            task_id=task_id,
            record_type=record_type,
            reviewed_task_id=reviewed_task_id,
            audit_task_id=audit_task_id,
        )
        for item in matches
    ):
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": record_id,
            "status": "conflict",
            "level": "needs_owner",
            "message": f"{ref_name} points to {record_type} record with mismatched task_id",
            "matches": normalized_matches,
        }

    if len(matches) > 1:
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": record_id,
            "status": "conflict",
            "level": "needs_owner",
            "message": f"{ref_name} matches duplicate {record_type} records",
            "matches": normalized_matches,
        }

    return {
        "reference": ref_name,
        "record_type": record_type,
        "record_id": record_id,
        "status": "ok",
        "level": "info",
        "message": f"{ref_name} references an existing {record_type} record",
        "matches": normalized_matches,
    }


def _record_ref_matches_task_context(
    record: dict[str, Any],
    *,
    task_id: str | None,
    record_type: str,
    reviewed_task_id: str | None,
    audit_task_id: str | None,
) -> bool:
    if task_id and record.get("task_id") == task_id:
        return True
    if record_type not in {RecordType.AUDIT_DISPATCH, RecordType.AUDIT_VERDICT}:
        return False
    if not (task_id and reviewed_task_id and audit_task_id):
        return False
    if record.get("task_id") != reviewed_task_id:
        return False
    if record.get("reviewed_task_id") != reviewed_task_id:
        return False
    record_audit_task_id = record.get("audit_task_id")
    return not record_audit_task_id or record_audit_task_id == audit_task_id


def check_task_record_refs(task: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata", {})
    task_id = task.get("task_id") or metadata.get("task_id")
    reviewed_task_id = metadata.get("reviewed_task_id")
    audit_context = {}
    if reviewed_task_id and reviewed_task_id != task_id:
        audit_context = {
            "reviewed_task_id": str(reviewed_task_id),
            "audit_task_id": str(task_id),
        }
    checks = [
        _check_ref("claim_id", task_id, metadata.get("claim_id"), RecordType.CLAIM, records),
        _check_ref("active_session_id", task_id, metadata.get("active_session_id"), "session", records),
        _check_ref("last_session_id", task_id, metadata.get("last_session_id"), "session", records),
    ]
    return_ref = metadata.get("return_record_ref") or metadata.get("return_event_ref")
    if return_ref:
        checks.append(_check_ref("return_record_ref", task_id, return_ref, RecordType.RETURN, records))
    dispatch_ref = metadata.get("audit_dispatch_record_ref")
    if dispatch_ref:
        checks.append(
            _check_ref("audit_dispatch_record_ref", task_id, dispatch_ref, RecordType.AUDIT_DISPATCH, records, **audit_context)
        )
    verdict_ref = metadata.get("related_audit_verdict_ref")
    if verdict_ref:
        checks.append(
            _check_ref("related_audit_verdict_ref", task_id, verdict_ref, RecordType.AUDIT_VERDICT, records, **audit_context)
        )

    warnings = [item["message"] for item in checks if item["level"] == "warn"]
    needs_owner_reasons = [item["message"] for item in checks if item["level"] == "needs_owner"]
    return {
        "checks": checks,
        "warnings": warnings,
        "needs_owner_reasons": needs_owner_reasons,
    }
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
