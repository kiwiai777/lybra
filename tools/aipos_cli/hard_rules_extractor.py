"""AIPOS-F41: 硬规矩提取器 — 从顾问手册单一真相源提取硬规矩内容。

设计权威: AIPOS-F41 大项A(硬规矩下发工位,单源生成)。

单一真相源: governance/COMMANDS.md (契约层固化名,AIPOS-F67) 的 "## 0.5. 硬规矩" 节。
消费方:
  - 章程分发(agents/roles/*/AGENTS.md 红线节追加)
  - 派审注入(audit task card 自带硬规矩提醒)

红线: 手册是唯一源,禁在章程/注入处各写一份 → 修改手册一处,分发与注入同步跟随。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _get_commands_handbook_path(governance_root: Path, repo_root: Path | None = None) -> Path:
    """从 config.schema 契约文档声明获取 COMMANDS.md 路径 (AIPOS-F67 契约层固化)。
    
    优先使用声明名 'commands',回退到旧名 'COMMANDS' (兼容存量项目)。
    """
    try:
        from tools.schema_loader import get_governance_structure, resolve_governance_path
        
        gs = get_governance_structure(repo_root)
        gov_docs_entry = gs.get("paths", {}).get("governance_docs", {})
        files = gov_docs_entry.get("files", {})
        
        # 优先使用声明名
        commands_filename = files.get("commands", "COMMANDS.md")
        
        governance_docs_dir = resolve_governance_path("governance_docs", governance_root, repo_root)
        return governance_docs_dir / commands_filename
    except Exception:
        # 回退到旧名 (兼容)
        return governance_root / "governance" / "COMMANDS.md"


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
    handbook = _get_commands_handbook_path(governance_root, repo_root)
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
    handbook = _get_commands_handbook_path(governance_root, repo_root)
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


def _resolve_governance_root() -> Path:
    """解析治理仓根路径(与 C2 身份解析单源同处取值)。

    解析链:LYBRA_WORKSPACE_ROOT 环境变量 → .lybra/connection.json workspace_root → 标准位置降级。
    禁新建解析函数,复用 loop_context 既有 ConnectionResolver 取值逻辑。
    """
    import os
    import json

    # ① 环境变量(与 C2 identity resolution 同源)
    env_root = os.environ.get("LYBRA_WORKSPACE_ROOT", "").strip()
    if env_root:
        return Path(env_root)

    # ② .lybra/connection.json(与 C2 同源)
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        conn_file = parent / ".lybra" / "connection.json"
        if conn_file.is_file():
            try:
                conn = json.loads(conn_file.read_text(encoding="utf-8"))
                ws = conn.get("workspace_root", "").strip()
                if ws:
                    return Path(ws)
            except Exception:
                pass
            # .lybra 找到了但无 workspace_root → 其父目录即治理仓
            return parent

    # ③ 降级:标准位置
    return Path.home() / "ai-project-os" / "2_projects" / "lybra"


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
        "> **单一真相源**: governance/COMMANDS.md § 0.5。修改手册 → 章程与派审注入同步跟随。",
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
        "> **单一真相源**: governance/COMMANDS.md § 6.9。修改手册 → 工位技能自动同步。",
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
