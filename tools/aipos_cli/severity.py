"""AIPOS-F4 大项B/C: 严重级单一映射 — 出声点只引用此模块, 禁现场定级。

设计出处: 2026-08-20-错误输出分级红色error次数真需要人出手的次数.md (Owner 两次拍板)
声明单一真相: schema/transitions.schema.json 的 severity_semantics 段。
渲染层(CLI/连接器/应答)一律经 `severity_to_level` 机械映射, 不得自定级别。
"""
from __future__ import annotations

from typing import Any

# 三档严重级语义(与 schema/transitions.schema.json severity_semantics.tiers 对齐)。
# auto_recoverable = 流程自动重试/让路 → warn;needs_human = 需人出手 → error;bug = 真 bug → error 且修。
SEVERITY_LEVELS: dict[str, str] = {
    "auto_recoverable": "warn",
    "needs_human": "error",
    "bug": "error",
}

# 未声明严重级时的保守默认(未知一律按需人出手 → error, 不静默降级)。
DEFAULT_SEVERITY = "needs_human"
DEFAULT_LEVEL = "error"

# 渲染层支持的级别(对齐连接器 notify 与 CLI 输出)。
LEVELS = ("info", "warn", "error")


def severity_to_level(severity: str | None) -> str:
    """严重级 → 渲染级别(声明→级别机械映射, 单一实现)。

    auto_recoverable → warn;needs_human/bug → error;未知/缺省 → error(保守)。
    """
    if not severity:
        return DEFAULT_LEVEL
    return SEVERITY_LEVELS.get(severity, DEFAULT_LEVEL)


def load_severity_semantics(repo_root: Any = None) -> dict[str, Any]:
    """从 transitions.schema.json 读 severity_semantics(单一真相), 失败回退内置表。

    渲染层可选调用以校验映射与声明一致; 主路径用 severity_to_level 即可。
    """
    try:
        from tools.schema_loader import load_schema

        schema = load_schema("transitions", repo_root=repo_root)
        semantics = schema.get("severity_semantics") or {}
        mapping = semantics.get("mapping") or {}
        if mapping:
            return dict(mapping)
    except Exception:
        pass
    return dict(SEVERITY_LEVELS)


# AIPOS-316: 防直接调用护栏
from tools.aipos_cli._cli_entry_guard import check_direct_invocation

check_direct_invocation(__name__)
