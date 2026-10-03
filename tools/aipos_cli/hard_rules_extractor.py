"""AIPOS-F41: 硬规矩提取器 — 从项目声明的硬规矩来源手册提取硬规矩内容。

设计权威: AIPOS-F41 大项A(硬规矩下发工位,单源生成)。

AIPOS-F89 件② M17: 来源 = 项目 project.json paths.hard_rules_source(唯一读取口 workspace_config.project_paths), 手册内
"## 0.5. 硬规矩" 节(诊断清单另取 "## 6.9 阻塞分诊与排查路径" 节)。产品不再假设任何治理文档名(原写死命令手册文件名 +
config.schema governance_docs.files 契约删除)。项目未声明 = 跳过提取并 warning(章程以母本自带的硬规矩条文为准, 模板自带),
不 BLOCK。
消费方:
  - 章程分发(agents/roles/*/AGENTS.md 红线节「单一真相源」行 = hard_rules_source_ref, 经 charter_render 占位 {{hard_rules_source}})
  - 派审注入(audit task card 自带硬规矩提醒)

红线: 手册是唯一源,禁在章程/注入处各写一份 → 修改手册一处,分发与注入同步跟随。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

HARD_RULES_SECTION = "0.5"
DIAGNOSTIC_SECTION = "6.9"
UNDECLARED_REF = "本章程(项目未声明 project.json paths.hard_rules_source, 以本章程条文为准)"


def _hard_rules_handbook_path(governance_root: Path) -> Path | None:
    """硬规矩来源手册 = project.json paths.hard_rules_source(AIPOS-F89 件② M17 唯一读取口 project_paths); 未声明 = None。"""
    from tools.aipos_cli.workspace_config import project_paths

    value = project_paths(Path(governance_root))["hard_rules_source"]
    return Path(value) if value is not None else None


def hard_rules_source_ref(governance_root: Path, section: str = HARD_RULES_SECTION) -> str:
    """「单一真相源」行引用文本(章程渲染占位 {{hard_rules_source}} 与提取器渲染同读此函数): 已声明 = `<相对治理根路径> § <节>`;
    未声明 = UNDECLARED_REF。"""
    handbook = _hard_rules_handbook_path(governance_root)
    if handbook is None:
        return UNDECLARED_REF
    root = Path(governance_root)
    for base, cand in ((root, handbook), (root.resolve(), handbook.resolve())):
        try:
            return f"{cand.relative_to(base).as_posix()} § {section}"
        except ValueError:
            continue
    return f"{handbook} § {section}"


def _undeclared_result(extra_keys: dict[str, Any]) -> dict[str, Any]:
    reason = "HARD_RULES_SOURCE_UNDECLARED: 项目未声明 project.json paths.hard_rules_source, 硬规矩提取跳过(章程以母本自带条文为准)"
    print(f"Warning: {reason}", file=sys.stderr)
    return {"ok": False, "section_found": False, "raw_content": "", "skipped": True, "error": reason, **extra_keys}


def extract_hard_rules_from_handbook(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """从顾问手册提取硬规矩节内容(单一真相源)。

    Returns:
        {
            "ok": bool,
            "section_found": bool,
            "raw_content": str,  # 完整节内容(含标题)
            "rules_list": list[str],  # 编号规矩列表
            "background": str,  # 实撞背景段
            "error": str | None,
        }
    """
    handbook = _hard_rules_handbook_path(governance_root)
    if handbook is None:
        return _undeclared_result({"rules_list": [], "background": ""})
    if not handbook.is_file():
        return {
            "ok": False,
            "section_found": False,
            "raw_content": "",
            "rules_list": [],
            "background": "",
            "error": f"Handbook not found: {handbook}",
        }

    try:
        text = handbook.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {
            "ok": False,
            "section_found": False,
            "raw_content": "",
            "rules_list": [],
            "background": "",
            "error": f"Failed to read handbook: {e}",
        }

    # 提取 "## 0.5. 硬规矩" 节(从标题到下一个 ## 标题)
    pattern = r"^## 0\.5\. 硬规矩.*?\n(.*?)(?=^## [0-9]|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not match:
        return {
            "ok": False,
            "section_found": False,
            "raw_content": "",
            "rules_list": [],
            "background": "",
            "error": "Hard rules section (## 0.5. 硬规矩) not found in handbook",
        }

    raw_content = match.group(0)
    section_body = match.group(1)

    # 提取编号规矩(1. ... 6. ...)
    rules_pattern = r"^(\d+)\.\s+\*\*(.*?)\*\*.*?$"
    rules = []
    for line in section_body.split("\n"):
        m = re.match(rules_pattern, line)
        if m:
            num = m.group(1)
            title = m.group(2).strip()
            # 提取完整条目(标题+说明,到下一条或段落结束)
            rules.append(line.strip())

    # 提取实撞背景段(以"**实撞背景**:"开头的段落)
    background = ""
    bg_match = re.search(r"\*\*实撞背景\*\*:(.*?)(?=\n\n|\Z)", section_body, re.DOTALL)
    if bg_match:
        background = bg_match.group(0).strip()

    return {
        "ok": True,
        "section_found": True,
        "raw_content": raw_content,
        "rules_list": rules,
        "background": background,
        "error": None,
    }


def extract_diagnostic_checklist_from_handbook(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """从顾问手册提取诊断清单节内容(单一真相源,AIPOS-F41 大项B1)。

    Returns:
        {
            "ok": bool,
            "section_found": bool,
            "raw_content": str,  # 完整节内容
            "三查步骤": str,
            "卡点对照表": str,
            "escalation路径": str,
            "error": str | None,
        }
    """
    handbook = _hard_rules_handbook_path(governance_root)
    if handbook is None:
        return _undeclared_result({"三查步骤": "", "卡点对照表": "", "escalation路径": ""})
    if not handbook.is_file():
        return {
            "ok": False,
            "section_found": False,
            "raw_content": "",
            "三查步骤": "",
            "卡点对照表": "",
            "escalation路径": "",
            "error": f"Handbook not found: {handbook}",
        }

    try:
        text = handbook.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {
            "ok": False,
            "section_found": False,
            "raw_content": "",
            "三查步骤": "",
            "卡点对照表": "",
            "escalation路径": "",
            "error": f"Failed to read handbook: {e}",
        }

    # 提取 "## 6.9 阻塞分诊与排查路径" 节
    pattern = r"^## 6\.9 阻塞分诊与排查路径.*?\n(.*?)(?=^## [0-9]|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not match:
        return {
            "ok": False,
            "section_found": False,
            "raw_content": "",
            "三查步骤": "",
            "卡点对照表": "",
            "escalation路径": "",
            "error": "Diagnostic checklist section (## 6.9) not found in handbook",
        }

    raw_content = match.group(0)
    section_body = match.group(1)

    # 提取三查步骤
    三查 = ""
    m = re.search(r"\*\*链条卡点三查.*?\*\*:(.*?)(?=\n\*\*|\Z)", section_body, re.DOTALL)
    if m:
        三查 = m.group(0).strip()

    # 提取卡点对照表(markdown表格)
    表 = ""
    table_match = re.search(r"\| 症状 \|.*?\n\|.*?\n((?:\|.*?\n)+)", section_body, re.DOTALL)
    if table_match:
        表 = table_match.group(0).strip()

    # 提取escalation路径
    esc = ""
    e_match = re.search(r"\*\*escalation 路径\*\*.*?:(.*?)(?=\n\n|\Z)", section_body, re.DOTALL)
    if e_match:
        esc = e_match.group(0).strip()

    return {
        "ok": True,
        "section_found": True,
        "raw_content": raw_content,
        "三查步骤": 三查,
        "卡点对照表": 表,
        "escalation路径": esc,
        "error": None,
    }


def _resolve_governance_root() -> Path:
    """解析治理仓根路径 —— AIPOS-F88 件③: 委托唯一命名入口 workspace_config.governance_workspace_root。

    序: LYBRA_WORKSPACE_ROOT 环境变量(作显式值)→ 自 cwd 向上 .lybra/connection.json 声明(governance_root/workspace_root)
    → 结构识别(AIPOS-226 优先级梯)。原「按 lybra 布局标准位置降级」与吞异常的 connection.json 读取退役;
    解析不到 = FileNotFoundError(fail-closed, 带出口)。
    """
    import os

    from tools.aipos_cli.workspace_config import governance_workspace_root

    return governance_workspace_root(os.environ.get("LYBRA_WORKSPACE_ROOT", "").strip() or None)


def render_hard_rules_for_charter() -> str:
    """渲染硬规矩节供章程红线节使用(追加到现有红线后)。

    AIPOS-F41 大项A: 章程红线节追加硬规矩(与派审注入同源)。
    """
    gov_root = _resolve_governance_root()

    result = extract_hard_rules_from_handbook(gov_root)
    if not result["ok"]:
        return f"<!-- AIPOS-F41: 硬规矩提取失败: {result['error']} -->\n"

    lines = [
        "",
        "## 🟡 硬规矩(门交互与职责边界 — AIPOS-F41 下发)",
        "",
        f"> **单一真相源**: {hard_rules_source_ref(gov_root)}。修改该来源 → 章程与派审注入同步跟随。",
        "",
    ]

    for rule in result["rules_list"]:
        lines.append(rule)

    if result["background"]:
        lines.append("")
        lines.append(result["background"])

    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_diagnostic_checklist_for_advisor_skill() -> str:
    """渲染诊断清单供顾问技能使用(AIPOS-F41 大项B1)。"""
    gov_root = _resolve_governance_root()

    result = extract_diagnostic_checklist_from_handbook(gov_root)
    if not result["ok"]:
        return f"<!-- AIPOS-F41 B1: 诊断清单提取失败: {result['error']} -->\n"

    lines = [
        "## 阻塞分诊速查(AIPOS-F41 B1 — 30秒定位卡点)",
        "",
        f"> **单一真相源**: {hard_rules_source_ref(gov_root, DIAGNOSTIC_SECTION)}。修改该来源 → 工位技能自动同步。",
        "",
    ]

    if result["三查步骤"]:
        lines.append(result["三查步骤"])
        lines.append("")

    if result["卡点对照表"]:
        lines.append(result["卡点对照表"])
        lines.append("")

    if result["escalation路径"]:
        lines.append(result["escalation路径"])
        lines.append("")

    return "\n".join(lines)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
