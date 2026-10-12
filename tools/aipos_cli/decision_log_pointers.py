"""AIPOS-F159 件②(gap #142): decision_log 自动指针的读取侧与历史缺指针预演(只列不补)。

指针由门在 owner_decision_record 落库时生成(唯一生成实现 board_adapter._auto_generate_decision_log_pointer; 文件名唯一键 =
完整 decision_id, 声明 config.schema timeline_enforcement.decision_level.path_pattern)。F159 前的旧名指针(decision_id 首个 `_`
前段, 同日多张信封只留首张)不重命名: 本模块按 decision_log 月目录 + 指针 frontmatter decision_type + 正文 Record 行判定
「哪份决策记录已有指针」, 新旧文件名同判, 不依赖文件名。

缺指针预演(`lybra migrate decision-log-pointers`): 只读、零写入、无 --confirm(声明 decision_level.missing_pointer_preview.policy =
list_only)——候选 = 决策记录落点(transitions.schema record_locations.kinds.owner_decisions)下每份决策记录, 无任何指针指向即列出。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: 指针 frontmatter 的 decision_type(与生成实现写出的值一致)
POINTER_DECISION_TYPE = "auto_pointer"
#: 指针正文指向决策记录的行(生成实现写 "**Record**: `<治理根相对路径>`")
_RECORD_LINE = re.compile(r"^\*\*Record\*\*:\s*`([^`]+)`\s*$", re.M)
#: 决策记录类(transitions.schema record_locations.kinds 键)
DECISION_RECORD_KIND = "owner_decisions"


class DecisionPointerError(ValueError):
    """缺指针预演不可进行(声明不可读 / 治理根不对)。fail-closed, 带出口。"""


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def preview_declaration() -> dict[str, Any]:
    """config.schema governance_structure.timeline_enforcement.decision_level.missing_pointer_preview(唯一声明)。缺键 / policy 非 list_only = SchemaLoadError。"""
    from tools.aipos_cli.next_resolver import REPO_ROOT
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((((load_schema("config", REPO_ROOT).get("governance_structure") or {}).get("timeline_enforcement") or {})
             .get("decision_level") or {}).get("missing_pointer_preview"))
    if not isinstance(decl, dict) or decl.get("policy") != "list_only" or not str(decl.get("command") or "").strip():
        raise SchemaLoadError("config.schema.json governance_structure.timeline_enforcement.decision_level.missing_pointer_preview"
                              "(command / policy=list_only)未声明齐")
    return decl


def pointer_index(governance_root: Path | str) -> dict[str, Any]:
    """decision_log 月目录下全部自动指针(frontmatter decision_type = auto_pointer, 文件名不限)→ 其 Record 行指向的决策记录。

    返回 {"decision_log_dir": 治理根相对, "by_record": {决策记录相对路径: [指针相对路径…]}, "pointers": n, "unreadable": [拒因…]}。
    目录未建 = 无指针(by_record 为空)。读不出 frontmatter 的 .md 进 unreadable(出声, 不静默跳过); 非指针条目(人写决策条目)不计。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.schema_loader import resolve_governance_path

    root = Path(governance_root).resolve()
    log_dir = resolve_governance_path("decision_log_dir", root)
    by_record: dict[str, list[str]] = {}
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
            for target in _RECORD_LINE.findall(body):
                by_record.setdefault(Path(target.strip()).as_posix(), []).append(_rel(path, root))
    return {"decision_log_dir": _rel(log_dir, root) if log_dir.is_relative_to(root) else str(log_dir),
            "by_record": by_record, "pointers": count, "unreadable": unreadable}


def missing_pointer_plan(governance_root: Path | str) -> dict[str, Any]:
    """历史缺指针预演(只列不补): 决策记录落点下每份记录, 无任何指针(新旧文件名同判)指向即列入 missing。零写入。

    返回 {governance_root, records_dir, decision_log_dir, decisions, pointed, pointers, missing: [{record, decision_id}], unreadable, policy, command}。"""
    from tools.aipos_cli.record_writer import record_root

    root = Path(governance_root).resolve()
    if not root.is_dir():
        raise DecisionPointerError(f"治理根不存在: {root}")
    decl = preview_declaration()
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
