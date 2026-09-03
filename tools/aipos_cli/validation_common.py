"""
AIPOS-F63: Unified fail-closed validation utilities.

统一的必填/非空校验实现，可被schema声明驱动。
所有"跳过检查"分支都是缺陷，缺失即拒。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# AIPOS-F63: 占位符字典单一源（禁两处各写）
PLACEHOLDER_PATTERNS = [
    "(待填写)",
    "(PASS / FAIL",
    "(无验收清单)",
    "TODO",
    "FIXME",
    "TBD",
    "PLACEHOLDER",
]


def check_placeholder_in_text(text: str | None, field_name: str) -> list[str]:
    """
    检查文本字段是否包含占位符。
    
    AIPOS-F63: 占位符检测作用于所有文本字段，不只文件。
    AIPOS-F65C 件④: 区分「引用」与「实际空白」——占位符须独占一行或为节内容全部，
    反引号代码块与引用上下文不命中。
    
    Returns:
        blocking_reasons: 发现占位符时返回拒绝理由列表
    """
    blocking_reasons = []
    if not text:
        return blocking_reasons
    
    # AIPOS-F65C 件④: 逐行检查,占位符独占一行才命中(不在代码块、不在描述中引用)
    lines = text.split('\n')
    in_code_block = False
    found_standalone_placeholders = []
    
    for line in lines:
        # 跟踪代码块边界(```)
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            continue
        
        # 代码块内不检查
        if in_code_block:
            continue
        
        stripped = line.strip()
        # 占位符独占一行(前后只有空白、标点、引号)
        # 判据: 去除常见前导(列表标记、引号)后,整行只是一个占位符
        content = stripped.lstrip('- *>"\' ').rstrip('"\' ')
        
        for placeholder in PLACEHOLDER_PATTERNS:
            # 整行内容就是占位符(或占位符+少量标点)
            if content == placeholder or content.strip('.,;:!?') == placeholder:
                if placeholder not in found_standalone_placeholders:
                    found_standalone_placeholders.append(placeholder)
    
    if found_standalone_placeholders:
        blocking_reasons.append(
            f"PLACEHOLDER_DETECTED: {field_name} 包含占位符: {', '.join(found_standalone_placeholders)}。"
            f"出口: 将 {field_name} 中所有占位符替换为实际内容。"
        )
    return blocking_reasons


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
