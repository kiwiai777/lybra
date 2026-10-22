"""
AIPOS-F63: Unified fail-closed validation utilities.

统一的必填/非空校验实现，可被schema声明驱动。
所有"跳过检查"分支都是缺陷，缺失即拒。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPORT_PLACEHOLDER_KEYS = ("skeleton_prefix", "token_suffixes", "slot_markers", "slot_separators", "slot_strip", "rejection_category",
                           "rejection", "line_item", "writers", "temp_suffix", "write_last_rule")
_FENCE = "```"
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_QUOTED_RE = re.compile(r"`[^`]*`|\"[^\"]*\"")


def report_placeholder_declaration() -> dict[str, Any]:
    """AIPOS-F215 件①: 报告正文占位判据声明 transitions.schema artifact_ingest.report_placeholder 的唯一读取口(AIPOS-F63 原
    PLACEHOLDER_PATTERNS 代码常量收进该声明 slot_markers; 骨架前缀 skeleton_prefix 与 frontmatter 值判据同读)。缺键 / 形不合 =
    SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("transitions").get("artifact_ingest") or {}).get("report_placeholder")
    lists = ("token_suffixes", "slot_markers", "slot_separators")
    if (not isinstance(decl, dict) or any(k not in decl for k in REPORT_PLACEHOLDER_KEYS)
            or any(not isinstance(decl[k], list) or not decl[k] or any(not isinstance(i, str) or not i for i in decl[k]) for k in lists)
            or any(not isinstance(decl[k], str) or not decl[k] for k in REPORT_PLACEHOLDER_KEYS if k not in lists and k != "writers")
            or not isinstance(decl["writers"], dict) or set(decl["writers"]) != {"return", "verdict"}):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.report_placeholder({list(REPORT_PLACEHOLDER_KEYS)})未声明齐")
    return decl


def report_skeleton_prefix() -> str:
    """认领骨架占位前缀(record_writer.build_<return|verdict>_skeleton_markdown 的 `(待填写`), 读 report_placeholder.skeleton_prefix。"""
    return str(report_placeholder_declaration()["skeleton_prefix"])


def _slots(content: str, separators: list[str]) -> list[tuple[str, bool]]:
    """一行的填空位 [(内容, 是否比对 slot_markers)]: 整行内容、首个标签分隔符之后的值(两者全判); 表格行各单元格(只判骨架前缀:
    表头 / 清单列名可正当写 TBD 等字样, 存量报告实例 chris HBJOTA-22)。"""
    slots = [(content, True)]
    cuts = [(content.index(sep), sep) for sep in separators if sep in content]
    if cuts:
        pos, sep = min(cuts)
        slots.append((content[pos + len(sep):].strip(), True))
    if content.startswith("|"):
        slots.extend((cell.strip(), False) for cell in content.strip("|").split("|"))
    return slots


def _fill_word(word: str, suffixes: list[str]) -> bool:
    return any(word.endswith(sfx) and len(word) > len(sfx) and word == word.upper() and any(c.isalnum() for c in word[:-len(sfx)])
               for sfx in suffixes)


def report_body_placeholders(text: str | None, decl: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """AIPOS-F215 件①: 报告正文占位的唯一判定(规则见 transitions.schema artifact_ingest.report_placeholder.rule)。
    返回 [{line: 文件行号, text: 该行原文(去首尾空白), marker: 命中的占位}], 无占位 = []。产物入口(交回 / 审计报告同判)与门交回自检同读。"""
    decl = decl if decl is not None else report_placeholder_declaration()
    prefix, suffixes = str(decl["skeleton_prefix"]), list(decl["token_suffixes"])
    markers, separators, strip = list(decl["slot_markers"]), list(decl["slot_separators"]), str(decl["slot_strip"])
    hits: list[dict[str, Any]] = []
    in_fence = False
    for lineno, raw in enumerate(str(text or "").split("\n"), start=1):
        stripped = raw.strip()
        if stripped.startswith(_FENCE):
            in_fence = not in_fence
            continue
        content = stripped.lstrip("-*>+ ").strip().strip("*").strip()
        marker = None
        if in_fence:
            if content.startswith(prefix):
                marker = content
            elif _WORD_RE.fullmatch(content) and _fill_word(content, suffixes):
                marker = content
        else:
            for slot, check_markers in _slots(content, separators):
                if slot.startswith(prefix):
                    marker = slot
                    break
                bare = slot.strip(strip).strip()
                if check_markers and bare in markers:
                    marker = bare
                    break
            if marker is None:
                words = [w for w in _WORD_RE.findall(_QUOTED_RE.sub(" ", content)) if _fill_word(w, suffixes)]
                marker = words[0] if words else None
        if marker is not None:
            hits.append({"line": lineno, "text": stripped, "marker": marker})
    return hits


def report_placeholder_refusal(text: str | None, path: Any, kind: str) -> dict[str, Any] | None:
    """AIPOS-F215 件①: 产物入口的正文占位拒收(kind = return | verdict, 同一判定 report_body_placeholders)。
    无占位 = None; 有 = {category, reasons: [拒因原文(码起首, 逐条列行号与原文)], placeholders}。"""
    decl = report_placeholder_declaration()
    hits = report_body_placeholders(text, decl)
    if not hits:
        return None
    category = str(decl["rejection_category"])
    lines = "; ".join(str(decl["line_item"]).format(line=h["line"], text=h["text"]) for h in hits)
    reason = str(decl["rejection"]).format(category=category, path=path, count=len(hits), lines=lines, writer=decl["writers"][kind])
    return {"category": category, "reasons": [reason], "placeholders": hits}


def report_write_last_rule(report: str) -> str:
    """AIPOS-F215 件②: 「报告最后落盘」规则句(开工稿与执行 / 审计章程同读, 唯一渲染)。report = 报告路径(模板自带反引号)。"""
    decl = report_placeholder_declaration()
    return str(decl["write_last_rule"]).format(report=report, temp=f"{report}{decl['temp_suffix']}")


def check_placeholder_in_text(text: str | None, field_name: str) -> list[str]:
    """
    检查文本字段是否包含占位符(门交回自检 / 必填字段同用)。

    AIPOS-F63: 占位符检测作用于所有文本字段，不只文件。
    AIPOS-F65C 件④: 区分「引用」与「实际空白」——占位须是填空位本身, 句中引用、反引号 / 引号包住、代码行内出现不命中。
    AIPOS-F215 件①: 判定改读唯一实现 report_body_placeholders(声明 transitions.schema artifact_ingest.report_placeholder;
    原代码常量 PLACEHOLDER_PATTERNS 收进声明 slot_markers), 拒因附行号。

    Returns:
        blocking_reasons: 发现占位符时返回拒绝理由列表
    """
    hits = report_body_placeholders(text)
    if not hits:
        return []
    found = list(dict.fromkeys(h["marker"] for h in hits))
    where = "、".join(str(h["line"]) for h in hits)
    return [f"PLACEHOLDER_DETECTED: {field_name} 包含占位符: {', '.join(found)}(第 {where} 行)。"
            f"出口: 将 {field_name} 中所有占位符替换为实际内容。"]


def check_required_field(
    value: Any,
    field_name: str,
    *,
    allow_empty_string: bool = False,
    check_placeholders: bool = True,
) -> list[str]:
    """
    检查必填字段是否非空且非占位符。
    
    AIPOS-F63: 统一必填/非空校验，缺失即拒（fail-closed）。
    
    Args:
        value: 字段值
        field_name: 字段名（用于错误消息）
        allow_empty_string: 是否允许空字符串（默认False）
        check_placeholders: 是否检查占位符（默认True）
    
    Returns:
        blocking_reasons: 字段缺失或无效时返回拒绝理由列表
    """
    blocking_reasons = []
    
    if value is None:
        blocking_reasons.append(
            f"REQUIRED_FIELD_MISSING: {field_name} 为必填字段但值为 None。"
            f"出口: 提供有效的 {field_name} 值。"
        )
        return blocking_reasons
    
    if isinstance(value, str):
        if not allow_empty_string and not value.strip():
            blocking_reasons.append(
                f"REQUIRED_FIELD_EMPTY: {field_name} 为必填字段但为空字符串。"
                f"出口: 提供非空的 {field_name} 值。"
            )
            return blocking_reasons
        
        if check_placeholders:
            blocking_reasons.extend(check_placeholder_in_text(value, field_name))
    
    elif isinstance(value, (list, dict)):
        if len(value) == 0:
            blocking_reasons.append(
                f"REQUIRED_FIELD_EMPTY: {field_name} 为必填字段但为空列表/字典。"
                f"出口: 提供至少一项 {field_name} 内容。"
            )
    
    return blocking_reasons


def check_file_ref_exists(
    file_ref: str | None,
    field_name: str,
    repo_root: Path,
    *,
    required: bool = True,
) -> list[str]:
    """
    检查文件引用是否存在。
    
    AIPOS-F63: 文件不存在不跳过检查，而是拒绝并给出口。
    
    Args:
        file_ref: 文件路径引用
        field_name: 字段名
        repo_root: 仓库根目录
        required: 是否必填
    
    Returns:
        blocking_reasons: 文件不存在时返回拒绝理由列表
    """
    blocking_reasons = []
    
    if not file_ref or not file_ref.strip():
        if required:
            blocking_reasons.append(
                f"FILE_REF_MISSING: {field_name} 文件引用为必填但为空。"
                f"出口: 提供有效的 {field_name} 文件路径。"
            )
        return blocking_reasons
    
    file_path = repo_root / file_ref
    if not file_path.exists():
        blocking_reasons.append(
            f"FILE_NOT_FOUND: {field_name} 引用的文件不存在: {file_ref}。"
            f"出口: 确保文件 {file_ref} 已创建，或更正 {field_name} 引用。"
        )
        return blocking_reasons
    
    return blocking_reasons


def check_evidence_refs_non_empty(
    evidence_refs: list[str] | None,
    findings_summary: str | None,
) -> list[str]:
    """
    检查审计证据是否非空。
    
    AIPOS-F63: PASS类裁决必须带非空证据（evidence_refs 或 findings_summary 至少一项非空）。
    
    Returns:
        blocking_reasons: 证据为空时返回拒绝理由列表
    """
    blocking_reasons = []
    
    evidence_empty = not evidence_refs or len(evidence_refs) == 0
    findings_empty = not findings_summary or not findings_summary.strip()
    
    if evidence_empty and findings_empty:
        blocking_reasons.append(
            "EMPTY_EVIDENCE: PASS 类裁决必须带非空证据。"
            "evidence_refs 和 findings_summary 至少一项必须非空。"
            "出口: 提供审计证据引用（evidence_refs）或审计发现总结（findings_summary）。"
        )
    
    return blocking_reasons
