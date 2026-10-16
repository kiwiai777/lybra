"""AIPOS-F159 件②(gap #142): decision_log 自动指针的读取侧与历史缺指针预演; AIPOS-F160 件③(gap #145): 缺指针补生成。

指针由门在 owner_decision_record 落库时生成(唯一生成实现 board_adapter._auto_generate_decision_log_pointer; 文件名唯一键 =
完整 decision_id, 声明 config.schema timeline_enforcement.decision_level.path_pattern)。F159 前的旧名指针(decision_id 首个 `_`
前段, 同日多张信封只留首张)不重命名: 本模块按 decision_log 月目录 + 指针 frontmatter decision_type + 正文 Record 行判定
「哪份决策记录已有指针」, 新旧文件名同判, 不依赖文件名。

缺指针补生成(`lybra migrate decision-log-pointers`; 声明 decision_level.missing_pointer_backfill, 归 migrate 族, 与 record-type
同一两阶段 verbs.schema two_phase_protocol.records_migration_writers): 缺省预演零写入——候选 = 决策记录落点
(transitions.schema record_locations.kinds.owner_decisions)下每份决策记录, 无任何指针指向即列入, 逐份推导补生成所需字段与将生成的
指针名(出自生成实现 dry_run, 同一命名); --confirm 经同一生成实现写出, 再经 F158 写后落账唯一实现 governance_commit.land_written_paths
(提交范围 = 本次写出的指针)。只补门 owner_decision_record 落库写出的决策记录(推导判据见 derive_backfill); 推导不出的列出不补。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: 指针 frontmatter 的 decision_type(与生成实现写出的值一致)
POINTER_DECISION_TYPE = "auto_pointer"
#: 指针正文指向决策记录的行(生成实现写 "**Record**: `<治理根相对路径>`")
_RECORD_LINE = re.compile(r"^\*\*Record\*\*:\s*`([^`]+)`\s*$", re.M)
#: 指针正文的决策人行(生成实现写 "**Decided by**: <actor>")
_DECIDED_BY_LINE = re.compile(r"^\*\*Decided by\*\*:\s*(.+?)\s*$", re.M)
#: 决策记录类(transitions.schema record_locations.kinds 键)
DECISION_RECORD_KIND = "owner_decisions"


class DecisionPointerError(ValueError):
    """缺指针预演不可进行(声明不可读 / 治理根不对)。fail-closed, 带出口。"""


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def backfill_declaration() -> dict[str, Any]:
    """config.schema governance_structure.timeline_enforcement.decision_level.missing_pointer_backfill(唯一声明)。
    缺键 / policy 非 two_phase / 两阶段族或落账命令未登记 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import REPO_ROOT
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((((load_schema("config", REPO_ROOT).get("governance_structure") or {}).get("timeline_enforcement") or {})
             .get("decision_level") or {}).get("missing_pointer_backfill"))
    if (not isinstance(decl, dict) or decl.get("policy") != "two_phase"
            or not all(str(decl.get(k) or "").strip() for k in ("command", "two_phase_family", "landing_command"))):
        raise SchemaLoadError("config.schema.json governance_structure.timeline_enforcement.decision_level.missing_pointer_backfill"
                              "(command / policy=two_phase / two_phase_family / landing_command)未声明齐")
    return decl


def pointer_index(governance_root: Path | str) -> dict[str, Any]:
    """decision_log 月目录下全部自动指针(frontmatter decision_type = auto_pointer, 文件名不限)→ 其 Record 行指向的决策记录。

    返回 {"decision_log_dir": 治理根相对, "by_record": {决策记录相对路径: [指针相对路径…]}, "pointers": n, "unreadable": [拒因…],
    "entries": [{path, decided_at, decided_by, summary, records}]}(AIPOS-F173: entries = 每个指针的时刻 / 决策人 / 一句话 / 所指记录,
    路线图偏差「近期被改」读此, 不另扫目录)。
    目录未建 = 无指针(by_record 为空)。读不出 frontmatter 的 .md 进 unreadable(出声, 不静默跳过); 非指针条目(人写决策条目)不计。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.schema_loader import resolve_governance_path

    root = Path(governance_root).resolve()
    log_dir = resolve_governance_path("decision_log_dir", root)
    by_record: dict[str, list[str]] = {}
    entries: list[dict[str, Any]] = []
    unreadable: list[str] = []
    count = 0
    if log_dir.is_dir():
        for path in sorted(log_dir.rglob("*.md")):
            try:
                meta, body = require_frontmatter(path, allow_missing_block=True)
            except FrontmatterReadError as exc:
                unreadable.append(str(exc))
                continue
            if str(meta.get("decision_type") or "") != POINTER_DECISION_TYPE:
                continue
            count += 1
            records = [Path(target.strip()).as_posix() for target in _RECORD_LINE.findall(body)]
            for target in records:
                by_record.setdefault(target, []).append(_rel(path, root))
            by = _DECIDED_BY_LINE.search(body)
            summary = next((ln[3:].strip() for ln in body.splitlines() if ln.startswith("## ")), "")
            entries.append({"path": _rel(path, root), "decided_at": str(meta.get("decided_at") or "") or None,
                            "decided_by": by.group(1).strip() if by else None, "summary": summary or None, "records": records})
    return {"decision_log_dir": _rel(log_dir, root) if log_dir.is_relative_to(root) else str(log_dir),
            "by_record": by_record, "pointers": count, "unreadable": unreadable, "entries": entries}


def missing_pointer_plan(governance_root: Path | str) -> dict[str, Any]:
    """历史缺指针(只读): 决策记录落点下每份记录, 无任何指针(新旧文件名同判)指向即列入 missing。零写入。

    返回 {governance_root, records_dir, decision_log_dir, decisions, pointed, pointers, missing: [{record, decision_id}], unreadable, policy, command}。"""
    from tools.aipos_cli.record_writer import record_root

    root = Path(governance_root).resolve()
    if not root.is_dir():
        raise DecisionPointerError(f"治理根不存在: {root}")
    decl = backfill_declaration()
    records_rel = record_root(DECISION_RECORD_KIND)
    records_dir = root / records_rel
    index = pointer_index(root)
    decisions = sorted(p for p in records_dir.glob("*.md") if p.is_file() and p.name.lower() != "readme.md") if records_dir.is_dir() else []
    missing: list[dict[str, str]] = []
    pointed = 0
    for path in decisions:
        rel = _rel(path, root)
        if index["by_record"].get(rel):
            pointed += 1
        else:
            missing.append({"record": rel, "decision_id": path.stem})
    return {"governance_root": str(root), "records_dir": records_rel.as_posix(), "decision_log_dir": index["decision_log_dir"],
            "decisions": len(decisions), "pointed": pointed, "pointers": index["pointers"], "missing": missing,
            "unreadable": index["unreadable"], "policy": decl["policy"], "command": decl["command"]}


# ---------------------------------------------------------------------------
# AIPOS-F160 件③(gap #145): 缺指针补生成(预演 / 确认), 生成复用门落库同一实现, 落账复用 F158 同一提交口
# ---------------------------------------------------------------------------

BACKFILL = "derived"        # 推导出, 将补生成(状态名与 records_migration_writers.item_status_labels 同键)
UNDERIVABLE = "underivable"  # 推导不出: 列出原因不补


def derive_backfill(governance_root: Path, record_rel: str) -> dict[str, Any]:
    """一份缺指针决策记录 → 补生成所需字段, 或推导不出的原因。

    只补门 owner_decision_record 落库写出的记录(指针本由该落库触发, 声明 decision_level.trigger): frontmatter record_type =
    owner_decision_record、decision_id = 文件名且合 owner_decision_writer.DECISION_ID_PATTERN、decided_at 为带时区 ISO 时刻、
    decided_by_ref 非空、正文有一句话结论行(owner_decision_writer.SUMMARY_LINE_PREFIX)。缺一 = 推导不出(不猜、不填缺省)。"""
    from datetime import datetime

    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.owner_decision_writer import DECISION_ID_PATTERN, SUMMARY_LINE_PREFIX
    from tools.schema_constants import RecordType

    path = Path(governance_root) / record_rel
    try:
        meta, body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        return {"status": UNDERIVABLE, "reason": f"决策记录 frontmatter 读不出: {exc}"}
    record_type = str(meta.get("record_type") or "")
    if record_type != RecordType.OWNER_DECISION_RECORD:
        return {"status": UNDERIVABLE, "reason": f"record_type={record_type or '(缺)'} 非 {RecordType.OWNER_DECISION_RECORD}"
                                                 "(不是门决策落库写出的记录, 指针不由它触发)"}
    decision_id = str(meta.get("decision_id") or "").strip()
    if decision_id != path.stem or not DECISION_ID_PATTERN.fullmatch(decision_id):
        return {"status": UNDERIVABLE, "reason": f"decision_id={decision_id or '(缺)'} 与文件名 {path.stem} 不符或不合决策 ID 式样"}
    raw_at = meta.get("decided_at")
    try:
        decided_at = raw_at if isinstance(raw_at, datetime) else datetime.fromisoformat(str(raw_at or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return {"status": UNDERIVABLE, "reason": f"decided_at={raw_at!r} 不是 ISO 时刻"}
    if decided_at.tzinfo is None:
        return {"status": UNDERIVABLE, "reason": f"decided_at={raw_at!r} 无时区(月目录 / 日期推导不出)"}
    decided_by = str(meta.get("decided_by_ref") or "").strip()
    if not decided_by:
        return {"status": UNDERIVABLE, "reason": "decided_by_ref 缺"}
    summary = next((ln[len(SUMMARY_LINE_PREFIX):].strip() for ln in body.splitlines() if ln.startswith(SUMMARY_LINE_PREFIX)), "")
    if not summary:
        return {"status": UNDERIVABLE, "reason": f"正文无一句话结论行({SUMMARY_LINE_PREFIX.strip()})"}
    return {"status": BACKFILL, "decision_id": decision_id, "decided_at": decided_at, "decided_by": decided_by, "summary": summary}


def plan_pointer_backfill(governance_root: Path | str) -> dict[str, Any]:
    """只读预演: 缺指针的决策记录逐份推导, 可补者附将生成的指针名(生成实现 dry_run, 同一命名)。零写入。"""
    from tools.aipos_cli.board_adapter import _auto_generate_decision_log_pointer

    plan = missing_pointer_plan(governance_root)
    root = Path(plan["governance_root"])
    items: list[dict[str, Any]] = []
    for miss in plan["missing"]:
        item: dict[str, Any] = {"record": miss["record"], "decision_id": miss["decision_id"], "pointer": None}
        derived = derive_backfill(root, miss["record"])
        if derived["status"] == BACKFILL:
            probe = _auto_generate_decision_log_pointer(root, derived["decision_id"], derived["summary"], derived["decided_by"],
                                                        miss["record"], decided_at=derived["decided_at"], dry_run=True)
            if probe.get("warning"):
                derived = {"status": UNDERIVABLE, "reason": probe["warning"]}
            elif not probe.get("path"):
                derived = {"status": UNDERIVABLE, "reason": "同名指针文件已在但其 Record 行不指向本记录(不覆盖)"}
            else:
                item["pointer"] = probe["path"]
        item.update(derived)
        items.append(item)
    counts = {BACKFILL: sum(1 for i in items if i["status"] == BACKFILL), UNDERIVABLE: sum(1 for i in items if i["status"] == UNDERIVABLE)}
    return {**plan, "items": items, "counts": counts}


def run_pointer_backfill(governance_root: Path | str, *, actor: str | None, confirm: bool, push: bool = True) -> dict[str, Any]:
    """预演(confirm=False): plan 原样返回, 零写入。确认: 可补者经门落库同一生成实现写出, 再经 land_written_paths 落账
    (提交范围 = 本次写出的指针)。生成失败 = 该项改列推导不出(带拒因), 其余照写照落。"""
    from tools.aipos_cli.board_adapter import _auto_generate_decision_log_pointer

    plan = plan_pointer_backfill(governance_root)
    plan.update(confirm=bool(confirm), written=[], landing=None)
    if not confirm:
        return plan
    if not str(actor or "").strip():
        raise DecisionPointerError("--confirm 须给 --actor(落账提交身份)")
    decl = backfill_declaration()
    root = Path(plan["governance_root"])
    for item in plan["items"]:
        if item["status"] != BACKFILL:
            continue
        done = _auto_generate_decision_log_pointer(root, item["decision_id"], item["summary"], item["decided_by"], item["record"],
                                                   decided_at=item["decided_at"], backfilled_by=f"{decl['command']} --confirm")
        if done.get("path"):
            plan["written"].append(done["path"])
        else:
            item.update(status=UNDERIVABLE, pointer=None,
                        reason=done.get("warning") or "同名指针文件已在但其 Record 行不指向本记录(不覆盖)")
    plan["counts"] = {BACKFILL: len(plan["written"]), UNDERIVABLE: sum(1 for i in plan["items"] if i["status"] == UNDERIVABLE)}
    if plan["written"]:
        from tools.aipos_cli.governance_commit import land_written_paths

        plan["landing"] = land_written_paths(root, plan["written"], str(actor), command=str(decl["landing_command"]), push=push,
                                             count=len(plan["written"]))
    return plan
