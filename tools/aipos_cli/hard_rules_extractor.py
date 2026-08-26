"""AIPOS-F41: 硬规矩提取器 — 从顾问手册单一真相源提取硬规矩内容。

设计权威: AIPOS-F41 大项A(硬规矩下发工位,单源生成)。

单一真相源: governance/ADVISOR-COMMANDS.md 的 "## 0.5. 硬规矩" 节。
消费方:
  - 章程分发(agents/roles/*/AGENTS.md 红线节追加)
  - 派审注入(audit task card 自带硬规矩提醒)

红线: 手册是唯一源,禁在章程/注入处各写一份 → 修改手册一处,分发与注入同步跟随。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def extract_hard_rules_from_handbook(governance_root: Path) -> dict[str, Any]:
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
    handbook = governance_root / "governance" / "ADVISOR-COMMANDS.md"
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
    except Exception as e:
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


def extract_diagnostic_checklist_from_handbook(governance_root: Path) -> dict[str, Any]:
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
    handbook = governance_root / "governance" / "ADVISOR-COMMANDS.md"
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
    except Exception as e:
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


def render_hard_rules_for_charter() -> str:
    """渲染硬规矩节供章程红线节使用(追加到现有红线后)。

    AIPOS-F41 大项A: 章程红线节追加硬规矩(与派审注入同源)。
    """
    # 假设从workspace根调用,先定位治理仓
    from tools.loop_context import discover_governance_root
    try:
        gov_root = discover_governance_root()
    except Exception:
        # 降级:假设调用者在产品仓,治理仓在标准位置
        gov_root = Path.home() / "ai-project-os" / "2_projects" / "lybra"

    result = extract_hard_rules_from_handbook(gov_root)
    if not result["ok"]:
        return f"<!-- AIPOS-F41: 硬规矩提取失败: {result['error']} -->\n"

    lines = [
        "",
        "## 🟡 硬规矩(门交互与职责边界 — AIPOS-F41 下发)",
        "",
        "> **单一真相源**: governance/ADVISOR-COMMANDS.md § 0.5。修改手册 → 章程与派审注入同步跟随。",
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
    from tools.loop_context import discover_governance_root
    try:
        gov_root = discover_governance_root()
    except Exception:
        gov_root = Path.home() / "ai-project-os" / "2_projects" / "lybra"

    result = extract_diagnostic_checklist_from_handbook(gov_root)
    if not result["ok"]:
        return f"<!-- AIPOS-F41 B1: 诊断清单提取失败: {result['error']} -->\n"

    lines = [
        "## 阻塞分诊速查(AIPOS-F41 B1 — 30秒定位卡点)",
        "",
        "> **单一真相源**: governance/ADVISOR-COMMANDS.md § 6.9。修改手册 → 工位技能自动同步。",
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
