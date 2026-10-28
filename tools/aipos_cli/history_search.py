"""AIPOS-F247 — 历史与审计: 按关键字检索各项目已结案卡「某件事当时怎么做、怎么验证、谁审的」。

一句话(Owner 10-28「历史账本、历史任务卡归到另一个地方用于审计即可, 可以查询搜索关键字查到历史里某个任务怎么做的」):
`lybra history search <关键字…>` 与看板「历史与审计」页是同一实现(search), 同一份索引, 同一组展开内容(card_entry)。

- 范围 = home 根注册项目(owner_inbox.home_projects 唯一枚举)的已结案卡(machine_zone.card_state_groups closed)+ 冻结的历史存量
  (legacy_baseline.frozen_tasks); 审计卡不单列(随被审卡展开)。
- 每张卡的展开内容只调既有解析(声明 verbs.schema verbs.lybra_history_search): 卡面目标 card_render.card_goal、门记录
  next_resolver._read_task_records、结案 task_complexity.closure_state / board_home.closed_at、归属 machine_zone.card_owner、
  交回 / 审计一句话结论 next_resolver.extract_return_summary_text、验证证据 roadmap_drift.card_evidence、产物 roadmap.card_artifacts。
- 索引 = 可丢弃的派生缓存(全局 Lybra 目录下, 不进治理根): 每卡条目连同推导时读到的真相身份(task_loader.truth_record, AIPOS-F245
  同一记录器与判据)存盘, 每次检索逐卡复核(truth_identity_changed), 变了才重算。删了自动重建。
- 中文按子串匹配(casefold), 标准库, 不引分词与第三方依赖。只读: 不写治理根、不连门、不读凭据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VERB = "lybra_history_search"
LEGACY_ENUM = "history_legacy_filter"  # enums.schema: the legacy-backlog filter values (include / only / exclude)
_KEYS = ("legacy_default", "since_days_suffix", "result_limit", "field_chars", "excerpt_chars", "evidence_limit", "credential_mask", "index",
         "texts", "exit_codes", "parameters")
_TEXT_KEYS = ("header", "header_all", "scope_all", "row", "legacy_mark", "goal", "done", "verify", "verify_none", "audit", "audit_rounds",
              "rejected", "reports", "artifacts", "none", "more", "index", "index_write_failed", "errors", "unknown_project", "bad_since",
              "verdict_words")
VERDICT_WORDS = ("pass", "pass_with_notes", "fail", "block", "needs_owner", "other", "none", "conclusion", "legacy")

_INDEX_LOCKS: dict[str, threading.Lock] = {}
_INDEX_LOCKS_GUARD = threading.Lock()
_LOADED: dict[str, tuple[Any, dict[str, Any]]] = {}  # index path -> (file identity, parsed index): the board keeps one parsed copy


class HistoryUsageError(ValueError):
    """参数不合(--since / --project / --legacy): CLI 出口 usage。"""


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_history_search(fail-closed): 键齐; legacy 值域 = parameters.legacy.enum 且缺省在内; 数值正整数。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(VERB)
    where = f"verbs.schema.json verbs.{VERB}"
    if not isinstance(decl, dict) or any(k not in decl for k in _KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_KEYS)}")
    texts = decl["texts"]
    if not isinstance(texts, dict) or any(not texts.get(k) for k in _TEXT_KEYS) or \
            any(not str((texts["verdict_words"] or {}).get(k) or "") for k in VERDICT_WORDS):
        raise SchemaLoadError(f"{where}.texts 未声明齐 {list(_TEXT_KEYS)} / verdict_words {list(VERDICT_WORDS)}")
    ref = (((decl["parameters"].get("properties") or {}).get("legacy") or {}).get("$enum"))
    if ref != LEGACY_ENUM or decl["legacy_default"] not in legacy_choices():
        raise SchemaLoadError(f"{where}.parameters.legacy 须以 $enum 引 enums.schema {LEGACY_ENUM}, legacy_default 在其值域内")
    for key in ("result_limit", "field_chars", "excerpt_chars", "evidence_limit"):
        if not isinstance(decl[key], int) or isinstance(decl[key], bool) or decl[key] < 1:
            raise SchemaLoadError(f"{where}.{key} 须为正整数")
    index = decl["index"]
    if (not isinstance(index, dict) or not str(index.get("dir") or "").strip() or "{project}" not in str(index.get("file_template") or "")
            or not isinstance(index.get("format_version"), int) or not isinstance(index.get("root_digest_chars"), int)):
        raise SchemaLoadError(f"{where}.index(dir / file_template 含 {{project}} / format_version / root_digest_chars)未声明齐")
    if not str(decl["since_days_suffix"] or "").strip():
        raise SchemaLoadError(f"{where}.since_days_suffix 未声明")
    return decl


def legacy_choices() -> tuple[str, ...]:
    """历史存量筛选值域唯一读取口(enums.schema history_legacy_filter 的投影; CLI choices / 看板筛选 / 检索同读)。"""
    from tools.schema_loader import code_repo_schema_root, get_enum_values

    return tuple(get_enum_values(LEGACY_ENUM, code_repo_schema_root()))


def _decl_digest(decl: dict[str, Any]) -> str:
    """索引头的声明摘要(声明里影响条目内容的键变了 = 整份重建)。"""
    basis = {k: decl[k] for k in ("field_chars", "evidence_limit", "credential_mask", "index")}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def index_dir(decl: dict[str, Any] | None = None) -> Path:
    """索引目录 = <全局 Lybra 目录(workspace_config.GLOBAL_LYBRA_DIR, 随 $HOME)>/<index.dir>(不在任何治理根内)。"""
    from tools.aipos_cli.workspace_config import GLOBAL_LYBRA_DIR

    d = decl or declaration()
    return Path(GLOBAL_LYBRA_DIR).expanduser() / str(d["index"]["dir"])


def index_path(project: str, governance_root: Path, decl: dict[str, Any] | None = None) -> Path:
    d = decl or declaration()
    digest = hashlib.sha256(str(Path(governance_root).resolve()).encode("utf-8")).hexdigest()[: int(d["index"]["root_digest_chars"])]
    name = str(d["index"]["file_template"]).format(project=re.sub(r"[^\w.-]", "_", project), root_digest=digest)
    return index_dir(d) / name


# ---------------------------------------------------------------------------
# 单卡展开内容(唯一推导; CLI 与看板同读)
# ---------------------------------------------------------------------------

def _plain(text: Any, limit: int) -> str:
    """一段 markdown → 一行人话(去标题行 / 列表符 / 强调符与反引号, 合并空白), 截到 limit 字(超出加 …)。"""
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```") or line.startswith("|---"):
            continue
        lines.append(re.sub(r"^[-*>]+\s*", "", line))
    out = re.sub(r"\s+", " ", " ".join(lines)).replace("**", "").replace("`", "").strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def _rel(path: Path, root: Path) -> str:
    text, base = os.fspath(path), os.fspath(root).rstrip(os.sep) + os.sep
    if text.startswith(base):
        return text[len(base):].replace(os.sep, "/")
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def _summary_of(path: Path | None, source: dict[str, Any] | None) -> str:
    """交回报告一句话结论(next_resolver.extract_return_summary_text 唯一解析, paragraph 模式取折行写的整句)。文件不在 = ""。"""
    from tools.aipos_cli.next_resolver import extract_return_summary_text

    if path is None or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return extract_return_summary_text(text, source, paragraph=True) or ""


def _regular_verdicts(root: Path, task_id: str) -> list[tuple[Path, dict[str, Any]]]:
    """本卡全部常规审计裁决 [(记录文件, frontmatter)](门生记录文件 record_writer.record_files_with_prefix; 补审 supplementary_of 不算,
    与 next_resolver._read_task_records 同一分流), 按 verdict_at 先后。"""
    from tools.aipos_cli.next_resolver import _read_frontmatter
    from tools.aipos_cli.record_writer import record_dir, record_file_prefix, record_files_with_prefix

    out = []
    for path in record_files_with_prefix(record_dir(root, "audit_verdicts", task_id), record_file_prefix("audit_verdicts")):
        fm = _read_frontmatter(path) or {}
        if not str(fm.get("supplementary_of") or "").strip():
            out.append((path, fm))
    return sorted(out, key=lambda pair: (str(pair[1].get("verdict_at") or ""), pair[0].name))


def _evidence_section(report: dict[str, Any] | None) -> str:
    """审计报告「独立运行证据」节(标题 = artifact_ingest.runtime_evidence_declaration section_heading; 切节 card_render._split_sections)
    的一行人话摘录——节内没有机器可解析的运行记录块时「怎么验证」用它。"""
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration
    from tools.aipos_cli.card_render import _split_sections

    if not report or report.get("problem"):
        return ""
    heading = str(runtime_evidence_declaration()["section_heading"])
    return next((t for h, t in _split_sections(str(report.get("body") or "")) if h.strip() == heading), "")


def card_entry(governance_root: Path, path: Path, queue_state: str, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """一张卡的历史条目(唯一推导): {task_id, title, queue_state, path, audit_card, closed_at(epoch | None), owner, goal, done,
    return_ref, verification{entries[{command, exit_code}], report_ref, problem}, audit{verdict, verdict_id, rounds, rejected,
    reject_reason, report_ref, summary, closure_kind}, artifacts[{name, media}], problem}。读不出 = problem 点名(其余字段照给能给的)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.board_home import closed_at
    from tools.aipos_cli.card_render import card_goal
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.machine_zone import card_owner
    from tools.aipos_cli.next_resolver import (
        _PASS_VERDICTS, _read_task_records, _verdict_reason_excerpt, find_return_artifact, verdict_report,
    )
    from tools.aipos_cli.roadmap import card_artifacts
    from tools.aipos_cli.roadmap_drift import card_evidence
    from tools.aipos_cli.task_complexity import closure_state
    from tools.aipos_cli.workspace_config import return_summary_source

    from tools.aipos_cli.artifact_ingest import credential_guard_declaration, credential_redact

    d = decl or declaration()
    chars = int(d["field_chars"])
    guard = credential_guard_declaration()

    def shown(text: Any) -> str:  # report text shown to the Owner: credential values masked (the ingest judgement), one line
        return _plain(credential_redact(str(text or ""), guard, str(d["credential_mask"])), chars)

    root = Path(governance_root)
    entry: dict[str, Any] = {"task_id": path.stem.upper(), "title": "", "queue_state": queue_state, "path": _rel(path, root),
                             "audit_card": False, "closed_at": None, "owner": None, "goal": "", "done": "", "return_ref": None,
                             "verification": {"entries": [], "excerpt": "", "report_ref": None, "problem": None},
                             "audit": {"verdict": None, "verdict_id": None, "rounds": 0, "rejected": False, "reject_reason": "",
                                       "report_ref": None, "record_ref": None, "summary": "", "closure_kind": None},
                             "artifacts": [], "problem": None}
    try:
        meta, body = require_frontmatter(path)
        task_id = str(meta.get("task_id") or "").strip() or entry["task_id"]
        entry.update(task_id=task_id, title=str(meta.get("title") or "").strip())
        if is_audit_card(task_id, meta):
            entry["audit_card"] = True
            return entry
        entry["goal"] = shown(card_goal(str(body or "")))
        located = (path, queue_state)
        entry["closed_at"] = closed_at(root, task_id, located=located)
        entry["audit"]["closure_kind"] = closure_state(root, task_id, located=located)["kind"]
        entry["owner"] = card_owner(root, task_id, meta)["owner"]
        records = _read_task_records(root, task_id)
        ret = records.get("latest_return") or {}
        ref = str(ret.get("completion_report_ref") or "").strip()
        report = (root / ref) if ref and not Path(ref).is_absolute() else (Path(ref) if ref else find_return_artifact(root, task_id))
        if report is not None and report.is_file():
            entry["return_ref"] = _rel(report, root)
            entry["done"] = shown(_summary_of(report, return_summary_source(root)))
        verdicts = _regular_verdicts(root, task_id)
        latest_path, latest = verdicts[-1] if verdicts else (None, {})
        audit = entry["audit"]
        audit.update(verdict=str(latest.get("verdict") or "").upper() or None, verdict_id=latest.get("verdict_id"), rounds=len(verdicts),
                     record_ref=_rel(latest_path, root) if latest_path is not None else None)
        report = verdict_report(root, latest) if latest else None
        if report is not None:
            audit["report_ref"] = _rel(Path(report["path"]), root)
        if latest_path is not None:
            audit["summary"] = shown(_verdict_reason_excerpt(latest_path, latest, workspace_root=root))
        failed = [(p, v) for p, v in verdicts if str(v.get("verdict") or "").upper() not in _PASS_VERDICTS]
        rounds = meta.get("rework_rounds") if isinstance(meta.get("rework_rounds"), list) else []
        audit["rejected"] = bool(failed or rounds)
        if rounds and isinstance(rounds[0], dict):
            items = rounds[0].get("focus_items")
            audit["reject_reason"] = shown((items or [""])[0] if isinstance(items, list) else items)
        if not audit["reject_reason"] and failed:
            audit["reject_reason"] = shown(_verdict_reason_excerpt(failed[0][0], failed[0][1], workspace_root=root)) \
                or str(failed[0][1].get("verdict") or "")
        evidence = card_evidence(root, {"task_id": task_id, "queue_state": queue_state, "metadata": meta},
                                 {"records": {**records, "latest_verdict": latest or None}, "unreadable": None})
        ver = entry["verification"]
        ver["report_ref"] = audit["report_ref"]
        ver["problem"] = evidence.get("evidence_problem")
        for item in (evidence.get("evidence") or [])[: int(d["evidence_limit"])]:
            if isinstance(item, dict):
                ver["entries"].append({"command": shown(item.get("command")), "exit_code": item.get("exit_code")})
        if not ver["entries"]:
            ver["excerpt"] = shown(_evidence_section(report))
        for spec in card_artifacts(root, meta)["entries"]:
            for item in spec["files"]:
                entry["artifacts"].append({"name": item["name"], "media": item["media"]})
    except (OSError, ValueError, LookupError) as exc:  # FrontmatterReadError / AmbiguousTaskCard ⊂ ValueError: named, not guessed
        entry["problem"] = f"{type(exc).__name__}: {exc}"[:400]
    return entry


def haystack(entry: dict[str, Any]) -> str:
    """检索对象(casefold): 卡号 / 标题 / 目标 / 做了什么 / 审计一句话 / 打回原因。"""
    audit = entry.get("audit") or {}
    return "\n".join(str(x or "") for x in (entry.get("task_id"), entry.get("title"), entry.get("goal"), entry.get("done"),
                                            audit.get("summary"), audit.get("reject_reason"))).casefold()


# ---------------------------------------------------------------------------
# 索引(可丢弃的派生缓存; 逐卡按真相身份增量)
# ---------------------------------------------------------------------------

def _keep_truth(root: Path, queue_root: Path, own: Path, project_json: Path) -> Any:
    """单卡指纹只计治理根内真相: 队列根内他卡文件与目录列表(卡集合每次检索现列)、project.json(项目级设置折进索引头)不计。"""
    root_s, queue_s, own_s, pj_s = (os.path.abspath(str(p)) for p in (root, queue_root, own, project_json))

    def keep(path: str, _mode: Any) -> bool:
        if path == own_s:
            return True
        if path == pj_s or not (path == root_s or path.startswith(root_s + os.sep)):
            return False
        return not (path == queue_s or path.startswith(queue_s + os.sep))

    return keep


def _context(root: Path) -> dict[str, Any]:
    """索引头 context: 影响条目内容的项目级设置(落点 + 交回摘要来源); 不等 = 整份重建。"""
    from tools.aipos_cli.workspace_config import project_paths, return_summary_source

    paths = project_paths(root)
    return {"return_root": str(paths.get("return_root")), "verdict_root": str(paths.get("verdict_root")),
            "summary_source": {k: v for k, v in return_summary_source(root).items() if k != "declared"}}


def _lock_for(path: Path) -> threading.Lock:
    with _INDEX_LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(str(path), threading.Lock())


def _load_index(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """(索引, 读不出的原因)。看板进程内按文件身份复用已解析副本。索引坏 / 不在 = (None, 原因): 调用方整份重建(它只是派生缓存)。"""
    from tools.aipos_cli.task_loader import _path_identity

    ident = _path_identity(str(path))
    if ident is None:
        return None, "absent"
    cached = _LOADED.get(str(path))
    if cached is not None and cached[0] == ident:
        return cached[1], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("cards"), dict):
        return None, "malformed"
    _LOADED[str(path)] = (ident, data)
    return data, None


def _write_index(path: Path, data: dict[str, Any]) -> str | None:
    """原子写(临时文件 + os.replace)。失败 = 原因(本次结果照用)。"""
    from tools.aipos_cli.task_loader import _path_identity

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    _LOADED[str(path)] = (_path_identity(str(path)), data)
    return None


def _candidates(root: Path, previous: dict[str, Any], frozen: dict[str, Any]) -> tuple[list[tuple[Path, str]], dict[str, Any]]:
    """检索候选卡 = 已结案目录全部卡 + 其余非终态目录里的冻结卡(卡号按 frontmatter, 索引里按文件身份记住)。
    返回 ([(卡路径, 队列状态)], 新的非结案卡号表 {相对路径: [身份, 卡号]})。"""
    from tools.aipos_cli.machine_zone import card_state_groups
    from tools.aipos_cli.task_loader import QUEUE_STATES, QUEUE_TERMINAL_STATES, _frontmatter_task_id, _path_identity, iter_queue_task_paths

    closed = set(card_state_groups()["closed"])
    out: list[tuple[Path, str]] = []
    ids: dict[str, Any] = {}
    for state in QUEUE_STATES:
        if state in closed:
            out.extend((p, state) for p in iter_queue_task_paths(root, states=(state,)))
            continue
        if state in QUEUE_TERMINAL_STATES or not frozen:
            continue
        for p in iter_queue_task_paths(root, states=(state,)):
            rel = _rel(p, root)
            ident = _path_identity(str(p))
            ident_l = list(ident) if ident is not None else None
            seen = previous.get(rel)
            task_id = seen[1] if isinstance(seen, list) and len(seen) == 2 and seen[0] == ident_l else _frontmatter_task_id(p)
            ids[rel] = [ident_l, task_id]
            if task_id and str(task_id).strip().upper() in frozen:
                out.append((p, state))
    return out, ids


def project_entries(name: str, governance_root: Path, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个项目的全部历史条目(经索引增量): {entries, frozen, errors, index{path, cards, reused, rebuilt, build_ms, rebuilt_reason,
    write_error}}。entries 每条带 project / legacy(冻结判定现读 legacy_baseline.frozen_tasks, 不进缓存)。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_tasks
    from tools.aipos_cli.task_loader import queue_root_for, task_card_lookup_scope, truth_identity_changed, truth_record
    from tools.aipos_cli.workspace_config import project_json_path

    d = decl or declaration()
    root = Path(governance_root)
    path = index_path(name, root, d)
    errors: list[str] = []
    try:
        frozen = frozen_tasks(root)
    except LegacyBaselineError as exc:
        frozen = {}
        errors.append(f"{name}: legacy_baseline: {exc}")
    header = {"format_version": int(d["index"]["format_version"]), "declaration": _decl_digest(d), "context": _context(root),
              "governance_root": str(root.resolve())}
    with _lock_for(path):
        data, reason = _load_index(path)
        if data is not None and any(data.get(k) != v for k, v in header.items()):
            data, reason = None, "header changed"
        old_cards: dict[str, Any] = (data or {}).get("cards") or {}
        cards_list, ids = _candidates(root, (data or {}).get("queue_ids") or {}, frozen)
        seen: dict[tuple[str, Any], Any] = {}
        new_cards: dict[str, Any] = {}
        reused = rebuilt = 0
        dirty = data is None or (data or {}).get("queue_ids") != ids
        started = time.perf_counter()
        queue_root = queue_root_for(root)
        pj = project_json_path(root)
        with task_card_lookup_scope(root):
            for card_path, state in cards_list:
                rel = _rel(card_path, root)
                old = old_cards.get(rel)
                if isinstance(old, dict) and old.get("queue_state") == state and isinstance(old.get("truth"), list) \
                        and truth_identity_changed(old["truth"], seen) is None:
                    new_cards[rel] = old
                    reused += 1
                    continue
                entry, truth = truth_record(lambda: card_entry(root, card_path, state, d),
                                            keep=_keep_truth(root, queue_root, card_path, pj))
                rebuilt += 1
                dirty = True
                if truth["refused"] is None and truth["unsettled"] is None:
                    new_cards[rel] = {**entry, "truth": truth["stats"]}
                else:
                    new_cards[rel] = {**entry, "truth": None}  # used now, not kept: next search derives it again
        build_ms = round((time.perf_counter() - started) * 1000)
        if set(new_cards) != set(old_cards):
            dirty = True
        write_error = None
        if dirty:
            kept = {k: v for k, v in new_cards.items() if isinstance(v.get("truth"), list)}
            write_error = _write_index(path, {**header, "built_at": iso_z(),
                                              "queue_ids": ids, "cards": kept})
    entries = []
    for rel, card in new_cards.items():
        if card.get("audit_card"):
            continue
        item = {k: v for k, v in card.items() if k != "truth"}
        item.update(project=name, legacy=str(card.get("task_id") or "").strip().upper() in frozen)
        if item.get("problem"):
            errors.append(f"{name}/{rel}: {item['problem']}")
        entries.append(item)
    return {"entries": entries, "errors": errors,
            "index": {"path": str(path), "cards": len(new_cards), "reused": reused, "rebuilt": rebuilt, "build_ms": build_ms,
                      "rebuilt_reason": reason, "write_error": write_error}}


# ---------------------------------------------------------------------------
# 检索(CLI 与看板同一入口)
# ---------------------------------------------------------------------------

def split_keywords(values: Any) -> list[str]:
    """关键字(列表或一段文本)→ 按空白拆开、去空(全角空格同)。"""
    items = values if isinstance(values, (list, tuple)) else [values]
    return [w for v in items for w in re.split(r"\s+", str(v or "").replace("　", " ").strip()) if w]


def parse_since(value: str | None, decl: dict[str, Any], today: date | None = None) -> date | None:
    """--since: YYYY-MM-DD 或 <N><since_days_suffix>(近 N 天)。不合 = HistoryUsageError。"""
    from tools.aipos_cli.clock import utc_now

    text = str(value or "").strip()
    if not text:
        return None
    suffix = str(decl["since_days_suffix"])
    now = today or utc_now().date()
    if text.endswith(suffix) and text[: -len(suffix)].isdigit():
        return now - timedelta(days=int(text[: -len(suffix)]))
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HistoryUsageError(str(decl["texts"]["bad_since"]).format(value=text)) from exc


def verdict_key(entry: dict[str, Any]) -> str:
    """审计结论的人话键(verdict_words): 有常规裁决 = 按值; 无裁决 = 顾问复核结案 / 历史存量 / 无。"""
    verdict = str((entry.get("audit") or {}).get("verdict") or "").lower()
    if verdict:
        return verdict if verdict in VERDICT_WORDS else "other"
    if (entry.get("audit") or {}).get("closure_kind") == "conclusion":
        return "conclusion"
    return "legacy" if entry.get("legacy") else "none"


def search(home_root: str | Path | None = None, keywords: Any = (), *, project: str | None = None, advisor: str | None = None,
           since: str | None = None, rejected_once: bool = False, legacy: str | None = None, all_projects: bool = False,
           limit: int | None = None) -> dict[str, Any]:
    """检索(唯一实现): 返回 {keywords, filters, total, results(按结案时刻新的在前, 至多 limit), projects[{name, advisors}],
    errors, index[…], took_ms}。参数不合 = HistoryUsageError。整次检索一个请求级只读作用域(task_loader.read_request_scope: 接入登记 /
    角色表等每次检索只读一遍)。"""
    from tools.aipos_cli.task_loader import read_request_scope

    with read_request_scope():
        return _search(home_root, keywords, project=project, advisor=advisor, since=since, rejected_once=rejected_once, legacy=legacy,
                       all_projects=all_projects, limit=limit)


def _search(home_root: str | Path | None, keywords: Any, *, project: str | None, advisor: str | None, since: str | None,
            rejected_once: bool, legacy: str | None, all_projects: bool, limit: int | None) -> dict[str, Any]:
    from tools.aipos_cli.advisor_headline import latest_headlines
    from tools.aipos_cli.advisor_updates_cli import advisor_instances
    from tools.aipos_cli.board_home import _display_name
    from tools.aipos_cli.board_pages import home_entries, visible_entries

    started = time.perf_counter()
    decl = declaration()
    words = split_keywords(keywords)
    legacy_mode = legacy or str(decl["legacy_default"])
    if legacy_mode not in legacy_choices():
        raise HistoryUsageError(f"--legacy ∈ {list(legacy_choices())}, 得到 {legacy_mode!r}")
    since_day = parse_since(since, decl)
    home = home_entries(home_root)
    shown, _hidden = visible_entries(home, all_projects)
    if project:
        chosen = [e for e in home["projects"] if e["name"] == project]
        if not chosen:
            raise HistoryUsageError(str(decl["texts"]["unknown_project"]).format(
                project=project, choices=", ".join(e["name"] for e in home["projects"])))
    else:
        chosen = shown
    errors: list[str] = []
    indexes: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []
    for e in chosen:
        root = Path(e["root"])
        got = project_entries(str(e["name"]), root, decl)
        errors += got["errors"]
        indexes.append({"project": e["name"], **got["index"]})
        try:
            advisors = advisor_instances(root)
            heads = latest_headlines(root)
        except (OSError, ValueError, LookupError) as exc:
            advisors, heads = [], {}
            errors.append(f"{e['name']}: advisors: {type(exc).__name__}: {exc}")
        displays = {a: _display_name(str(e["name"]), a, advisors, heads.get(a)) for a in advisors}
        projects.append({"name": e["name"], "advisors": [{"instance": a, "display": displays[a]} for a in advisors]})
        for item in got["entries"]:
            item["advisor_display"] = displays.get(str(item.get("owner") or ""), "")
            pool.append(item)
    needles = [w.casefold() for w in words]
    matched = []
    for item in pool:
        if advisor and str(item.get("owner") or "") != advisor:
            continue
        if legacy_mode == "only" and not item["legacy"] or legacy_mode == "exclude" and item["legacy"]:
            continue
        if rejected_once and not (item.get("audit") or {}).get("rejected"):
            continue
        if since_day is not None:
            at = item.get("closed_at")
            if at is None or datetime.fromtimestamp(float(at), tz=timezone.utc).date() < since_day:
                continue
        if needles:
            hay = haystack(item)
            if not all(n in hay for n in needles):
                continue
        item["verdict_key"] = verdict_key(item)
        item["closed_day"] = (datetime.fromtimestamp(float(item["closed_at"]), tz=timezone.utc).date().isoformat()
                              if item.get("closed_at") is not None else None)
        matched.append(item)
    matched.sort(key=lambda r: (-(float(r["closed_at"]) if r.get("closed_at") is not None else 0.0), str(r.get("task_id"))))
    cap = int(limit) if limit else int(decl["result_limit"])
    return {"keywords": words, "filters": {"project": project, "advisor": advisor, "since": since, "since_day": since_day.isoformat()
                                           if since_day else None, "rejected_once": bool(rejected_once), "legacy": legacy_mode,
                                           "all_projects": bool(all_projects)},
            "total": len(matched), "results": matched[:cap], "projects": projects, "errors": errors, "index": indexes,
            "home_root": str(home["home_root"]), "took_ms": round((time.perf_counter() - started) * 1000)}


def report_doc(project: str, governance_root: Path | None, task_id: str, doc: str) -> dict[str, Any]:
    """看板报告视图的数据: {task_id, kind, title, ref, body(markdown 正文), problem}。只认该卡由 card_entry 推导给出的那一份:
    交回 = return_ref(frontmatter 去掉); 审计 = 最新常规裁决记录 record_ref 经 next_resolver.verdict_report(门快照优先, 校验后去记录头)。
    推导没有 / 读不出 = problem(不收任意路径)。"""
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.next_resolver import _read_frontmatter, verdict_report

    out: dict[str, Any] = {"task_id": task_id, "kind": doc, "title": None, "ref": None, "body": None, "problem": None}
    if governance_root is None:
        out["problem"] = f"project {project!r}"
        return out
    root = Path(governance_root)
    card = next((c for c in project_entries(project, root)["entries"] if str(c["task_id"]) == task_id), None)
    if card is None:
        out["problem"] = f"{project}/{task_id}"
        return out
    out["title"] = card.get("title")
    try:
        if doc == "return" and card.get("return_ref"):
            out["ref"] = card["return_ref"]
            _fm, body, _warnings = parse_markdown_frontmatter((root / card["return_ref"]).read_text(encoding="utf-8", errors="replace"))
            out["body"] = body
        elif doc == "audit" and (card.get("audit") or {}).get("record_ref"):
            report = verdict_report(root, _read_frontmatter(root / card["audit"]["record_ref"]))
            if report is None or report.get("problem"):
                out["problem"] = (report or {}).get("problem") or card["audit"]["record_ref"]
            else:
                out["ref"] = _rel(Path(report["path"]), root)
                out["body"] = str(report["body"])
        else:
            out["problem"] = f"{task_id}: {doc}"
    except (OSError, ValueError) as exc:
        out["problem"] = f"{type(exc).__name__}: {exc}"
    return out


def highlight_spans(text: str, keywords: list[str]) -> list[tuple[str, bool]]:
    """把 text 切成 [(片段, 是否命中)](casefold 子串, 重叠合并); 看板高亮与测试同读。"""
    low = text.casefold()
    marks = [False] * len(text)
    for word in keywords:
        needle = word.casefold()
        if not needle or len(low) != len(text):
            continue
        start = low.find(needle)
        while start >= 0:
            for i in range(start, start + len(needle)):
                marks[i] = True
            start = low.find(needle, start + 1)
    spans: list[tuple[str, bool]] = []
    for ch, mark in zip(text, marks):
        if spans and spans[-1][1] == mark:
            spans[-1] = (spans[-1][0] + ch, mark)
        else:
            spans.append((ch, mark))
    return spans


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _verify_text(entry: dict[str, Any], texts: dict[str, Any]) -> str:
    ver = entry.get("verification") or {}
    if ver.get("entries"):
        return "; ".join(e["command"] + (f" → {e['exit_code']}" if e.get("exit_code") is not None else "") for e in ver["entries"])
    return str(ver.get("excerpt") or texts["verify_none"])


def render_text(result: dict[str, Any]) -> str:
    from tools.aipos_cli.board_home import human_title

    decl = declaration()
    texts = decl["texts"]
    words = texts["verdict_words"]
    scope = result["filters"]["project"] or str(texts["scope_all"])
    head_key = "header" if result["keywords"] else "header_all"
    lines = [str(texts[head_key]).format(keywords=" ".join(result["keywords"]), scope=scope, total=result["total"],
                                         shown=len(result["results"]), ms=result["took_ms"]), ""]
    if not result["results"]:
        lines.append(str(texts["none"]))
    for r in result["results"]:
        audit = r.get("audit") or {}
        verdict = str(words[r["verdict_key"]]).format(verdict=audit.get("verdict") or "")
        lines.append(str(texts["row"]).format(
            closed=r.get("closed_day") or "-", project=r["project"], advisor=(" · " + r["advisor_display"]) if r.get("advisor_display") else "",
            verdict=verdict, task_id=r["task_id"], legacy=texts["legacy_mark"] if r.get("legacy") else "",
            title=human_title(r["task_id"], r.get("title"), int(decl["excerpt_chars"])) or r["task_id"]))
        if r.get("goal"):
            lines.append(str(texts["goal"]).format(text=_plain(r["goal"], int(decl["excerpt_chars"]))))
        if r.get("done"):
            lines.append(str(texts["done"]).format(text=_plain(r["done"], int(decl["excerpt_chars"]))))
        lines.append(str(texts["verify"]).format(text=_verify_text(r, texts)))
        if audit.get("verdict"):
            body = str(texts["audit_rounds"]).format(verdict=verdict, rounds=audit.get("rounds") or 1)
            if audit.get("summary"):
                body += " · " + _plain(audit["summary"], int(decl["excerpt_chars"]))
            lines.append(str(texts["audit"]).format(text=body))
        if audit.get("rejected"):
            lines.append(str(texts["rejected"]).format(text=audit.get("reject_reason") or "-"))
        refs = [x for x in (r.get("return_ref"), audit.get("report_ref")) if x]
        if refs:
            lines.append(str(texts["reports"]).format(refs=" · ".join(refs)))
        if r.get("artifacts"):
            lines.append(str(texts["artifacts"]).format(names=", ".join(a["name"] for a in r["artifacts"])))
        lines.append("")
    if result["total"] > len(result["results"]):
        lines.append(str(texts["more"]).format(count=result["total"] - len(result["results"])))
    idx = result["index"]
    lines.append(str(texts["index"]).format(projects=len(idx), cards=sum(i["cards"] for i in idx), rebuilt=sum(i["rebuilt"] for i in idx),
                                            build_ms=sum(i["build_ms"] for i in idx), reused=sum(i["reused"] for i in idx),
                                            paths=", ".join(i["path"] for i in idx)))
    for i in idx:
        if i.get("write_error"):
            lines.append(str(texts["index_write_failed"]).format(error=i["write_error"]))
    if result["errors"]:
        lines.append(str(texts["errors"]).format(count=len(result["errors"]), items="; ".join(result["errors"][:5])))
    return "\n".join(lines)


def run_history_cli(args: Any) -> int:
    """`lybra history search` 薄壳: 退出码读 verbs.schema lybra_history_search.exit_codes。"""
    import sys

    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    try:
        result = search(getattr(args, "home_root", None), list(getattr(args, "keywords", None) or []),
                        project=getattr(args, "project", None), advisor=getattr(args, "advisor", None), since=getattr(args, "since", None),
                        rejected_once=bool(getattr(args, "rejected_once", False)), legacy=getattr(args, "legacy", None),
                        all_projects=bool(getattr(args, "all_projects", False)), limit=getattr(args, "limit", None))
    except HistoryUsageError as exc:
        print(f"lybra history search: {exc}", file=sys.stderr)
        return declared_exit_code(VERB, "usage")
    except (OSError, ValueError, LookupError, SchemaLoadError) as exc:
        print(f"lybra history search: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(VERB, "unreadable")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text(result))
    return declared_exit_code(VERB, "ok" if result["results"] else "no_results")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
