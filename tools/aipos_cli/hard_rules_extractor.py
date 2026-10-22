"""AIPOS-F41: 硬规矩提取器 — 从项目声明的硬规矩来源手册提取硬规矩内容。

设计权威: AIPOS-F41 大项A(硬规矩下发工位,单源生成)。

AIPOS-F89 件② M17: 来源 = 项目 project.json paths.hard_rules_source(唯一读取口 workspace_config.project_paths), 手册内
"## 0.5. 硬规矩" 节(诊断清单另取 "## 6.9 阻塞分诊与排查路径" 节)。产品不再假设任何治理文档名(原写死命令手册文件名 +
config.schema governance_docs.files 契约删除)。项目未声明 = 跳过提取并 warning(章程以母本自带的硬规矩条文为准, 模板自带),
不 BLOCK。
消费方(AIPOS-F212 件②: 门交互硬规矩节只由 gate_hard_rules 一处产出, 章程与开工稿同读同序同数):
  - 章程分发(agents/roles/*/AGENTS.md 占位 {{hard_rules_section}} = render_hard_rules_for_charter, 经 charter_render 渲染; 母本不写条文)
  - lybra kickoff 开工稿门交互硬规矩行(subagent_kickoff, 同一 gate_hard_rules)
  - 项目未声明 paths.hard_rules_source = roles.schema gate_hard_rules.fallback(按角色类), 「单一真相源」行 = UNDECLARED_REF

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
    from tools.aipos_cli.workspace_config import governance_workspace_root, workspace_root_from_env

    # AIPOS-F106 件③: 工作区根环境变量经唯一读取口(LYBRA_WORKSPACE_ROOT; 旧名废弃兼容)
    return governance_workspace_root(workspace_root_from_env()[0])


class HardRulesSourceError(ValueError):
    """AIPOS-F212 件②: 项目声明了 paths.hard_rules_source 却读不出 / 无「## 0.5. 硬规矩」节 / 节内无编号条目(fail-closed;
    章程渲染与 lybra kickoff 同拒, 不退回母本条文、不出缺节的章程或开工稿)。"""


def gate_hard_rules_declaration() -> dict[str, Any]:
    """roles.schema gate_hard_rules(门交互硬规矩节的唯一声明)唯一读取口。缺键 / 形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("roles", code_repo_schema_root()).get("gate_hard_rules")
    fallback = decl.get("fallback") if isinstance(decl, dict) else None
    by_class = fallback.get("rules_by_role_class") if isinstance(fallback, dict) else None
    if (not isinstance(decl, dict) or not isinstance(decl.get("section_title"), str) or "{source}" not in str(decl.get("source_line") or "")
            or not isinstance(by_class, dict) or not isinstance(fallback.get("background"), str)
            or not all(isinstance(v, list) and v and all(isinstance(x, str) for x in v) for v in by_class.values())):
        raise SchemaLoadError("roles.schema.json gate_hard_rules(section_title / source_line 含 {source} / fallback.background / "
                              "fallback.rules_by_role_class.<角色类> 非空串列表)未声明齐")
    return decl


def gate_hard_rules(governance_root: Path, *, role_class: str) -> dict[str, Any]:
    """AIPOS-F212 件②: 门交互硬规矩节的唯一产出(章程 render_hard_rules_for_charter 与 lybra kickoff 同读, 同序同数)。

    返回 {source_ref, rules: [条目原文], background, state}: state = declared(取项目手册 § 0.5 编号条目与实撞背景)/
    undeclared(roles.schema gate_hard_rules.fallback 按角色类, 占位读 project.json paths; source_ref = UNDECLARED_REF)。
    声明了却读不出 / 无条目 = HardRulesSourceError; 未声明且该角色类无 fallback = SchemaLoadError(fail-closed, 不猜)。"""
    from tools.schema_loader import SchemaLoadError

    gov = Path(governance_root)
    ref = hard_rules_source_ref(gov)
    if _hard_rules_handbook_path(gov) is not None:
        result = extract_hard_rules_from_handbook(gov)
        if not result["ok"] or not result["rules_list"]:
            raise HardRulesSourceError(
                f"硬规矩来源 {ref} 不可用({result.get('error') or '节内无编号条目(行首「<n>. **标题**」)'}); 出口: 修 project.json "
                "paths.hard_rules_source(lybra project set-paths --key hard_rules_source)或在该手册补「## 0.5. 硬规矩」节编号条目")
        return {"source_ref": ref, "rules": list(result["rules_list"]), "background": str(result["background"] or ""), "state": "declared"}
    fallback = gate_hard_rules_declaration()["fallback"]
    rules = fallback["rules_by_role_class"].get(role_class)
    if rules is None:
        raise SchemaLoadError(f"roles.schema.json gate_hard_rules.fallback.rules_by_role_class 无角色类 {role_class!r}"
                              f"(已声明 {sorted(fallback['rules_by_role_class'])}), 项目也未声明 paths.hard_rules_source")
    from tools.aipos_cli.workspace_config import project_paths

    paths = project_paths(gov)
    values = {key: str(paths[key]) for key in ("return_root", "verdict_root", "task_cards_root")}
    return {"source_ref": ref, "rules": [rule.format(**values) for rule in rules], "background": fallback["background"], "state": "undeclared"}


def render_hard_rules_for_charter(governance_root: Path | None = None, *, role_class: str = "executor") -> str:
    """AIPOS-F212 件②: 章程「门交互硬规矩」节(母本占位 {{hard_rules_section}} 的值; charter_render.charter_render_context 调用)。
    条文 = gate_hard_rules(与开工稿同读); 标题与「单一真相源」行式 = roles.schema gate_hard_rules。governance_root 缺 = 环境解析。"""
    gov_root = Path(governance_root) if governance_root is not None else _resolve_governance_root()
    decl = gate_hard_rules_declaration()
    block = gate_hard_rules(gov_root, role_class=role_class)
    lines = [decl["section_title"], "", decl["source_line"].format(source=block["source_ref"]), "", *block["rules"]]
    if block["background"]:
        lines += ["", block["background"]]
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
