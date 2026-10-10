"""AIPOS-A1 大项A: lybra governance add — 治理写入 CLI

产生侧与出口侧共用同一份声明(config.schema governance_structure.file_declarations)。
顾问只填内容, 格式/命名/落点由声明驱动。

子命令:
  lybra governance add decision   — 生成 decision_log 条目骨架
  lybra governance add stage      — 生成 stage_archive 快照骨架
  lybra governance add doc        — 生成 governance doc 骨架
  lybra governance add record     — 生成 record 骨架

所有路径/格式/必填字段从 config.schema 声明读取, 零硬编码。
"""

from __future__ import annotations

import json
import re
import sys
from tools.aipos_cli.clock import file_slug, iso_z, local_now
from pathlib import Path
from typing import Any

from tools.schema_loader import load_schema, resolve_governance_path, get_governance_structure


def _today_str() -> str:
    return file_slug("date", local_now())


def _get_file_declaration(declaration_key: str, repo_root: Path | None = None) -> dict[str, Any]:
    """从 config.schema 读取文件声明(单一源)。"""
    gs = get_governance_structure(repo_root)
    declarations = gs.get("file_declarations", {}) or {}
    decl = declarations.get(declaration_key)
    if not decl:
        raise ValueError(
            f"config.schema governance_structure.file_declarations.{declaration_key} not found. "
            f"Available: {list(declarations.keys())}"
        )
    return decl


def _resolve_target_dir(path_key: str, governance_root: Path, repo_root: Path | None = None) -> Path:
    """从声明的 path_key 解析目标目录。"""
    return resolve_governance_path(path_key, governance_root, repo_root)


def _slugify(text: str) -> str:
    """将文本转为 URL-safe slug。"""
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[-\s]+', '-', slug).strip('-')
    return slug or "untitled"


def _render_frontmatter(fields: dict[str, Any]) -> str:
    """渲染 YAML frontmatter 块(``---``…``---``)。

    AIPOS-F87 件①: 经单源 record_writer.render_frontmatter_block(safe_dump + 写后回读校验), 原逐行 f"{key}: {value}" 拼接退役
    (标题/理由类值含冒号、`**`、`#` 时曾写出不可解析的头)。字段序 = 声明模板的插入序。
    """
    from tools.aipos_cli.record_writer import render_frontmatter_block

    return render_frontmatter_block(fields, list(fields))


def governance_doc_frontmatter(*, status: str = "active", repo_root: Path | None = None) -> str:
    """治理文档(governance/*.md)frontmatter 的唯一渲染(AIPOS-F94 N6): 声明 file_declarations.governance_doc 的
    template_frontmatter(status 可覆盖); 渲染结果缺 required_frontmatter 任一键 = ValueError(fail-closed)。
    add_doc / project new 的 decision_log 桩 / 门写的 enrollment_log 首建同此, 与提交门 B② 同一声明。"""
    decl = _get_file_declaration("governance_doc", repo_root)
    fields = dict(decl.get("template_frontmatter", {}))
    fields["status"] = status
    missing = [k for k in decl.get("required_frontmatter", []) if k not in fields]
    if missing:
        raise ValueError(f"config.schema file_declarations.governance_doc.template_frontmatter 缺必填键 {missing}")
    return _render_frontmatter(fields)


def append_governance_doc_line(trail: Path, line: str, *, title: str, repo_root: Path | None = None) -> Path:
    """AIPOS-F140 件③(gap #122): 产品写 governance/ 下追加型治理文档(接入 / 自定义角色 / 命名档 / 派发模式等日志)的唯一写口。

    只追加(open "a"); 文件首建(不存在或空)时先写声明头 governance_doc_frontmatter(config.schema file_declarations.governance_doc
    template_frontmatter 单源, 与提交门 B② 同一声明)+ ``# <title>``; 既有文件(含存量无头文件)不动、不补头。头先渲染后开文件:
    声明读不出 = 原异常上抛且不留空文件(fail-closed)。"""
    trail = Path(trail)
    header = None
    if not trail.is_file() or trail.stat().st_size == 0:
        header = governance_doc_frontmatter(repo_root=repo_root) + f"\n# {title}\n\n"
    trail.parent.mkdir(parents=True, exist_ok=True)
    with trail.open("a", encoding="utf-8") as fh:
        if header is not None and trail.stat().st_size == 0:
            fh.write(header)
        fh.write(line)
    return trail


def add_decision(
    governance_root: Path,
    *,
    title: str = "",
    status: str = "active",
    decided_at: str | None = None,
    body: str = "",
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """生成 decision_log 条目骨架。

    声明来源: config.schema governance_structure.file_declarations.decision_log_entry
    """
    decl = _get_file_declaration("decision_log_entry", repo_root)
    target_dir = _resolve_target_dir(decl["path_key"], governance_root, repo_root)

    # 命名: YYYY-MM/YYYY-MM-DD-<slug>.md
    today = _today_str()
    month_dir = today[:7]  # YYYY-MM
    slug = _slugify(title) if title else "decision"
    filename = f"{today}-{slug}.md"

    # 构建 frontmatter
    template_fm = dict(decl.get("template_frontmatter", {}))
    template_fm["status"] = status
    template_fm["decided_at"] = decided_at or iso_z()
    if "superseded_by" not in template_fm:
        template_fm["superseded_by"] = None

    frontmatter = _render_frontmatter(template_fm)

    # 构建内容
    content_parts = [frontmatter, ""]
    if title:
        content_parts.append(f"# {title}")
        content_parts.append("")
    if body:
        content_parts.append(body)
    else:
        content_parts.append("## Decision")
        content_parts.append("")
        content_parts.append("<!-- 填写决策内容 -->")
        content_parts.append("")
        content_parts.append("## Rationale")
        content_parts.append("")
        content_parts.append("<!-- 填写决策理由 -->")

    content = "\n".join(content_parts) + "\n"

    # 目标路径
    file_dir = target_dir / month_dir
    file_path = file_dir / filename

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "declaration_key": "decision_log_entry",
            "target_path": str(file_path),
            "required_frontmatter": decl.get("required_frontmatter", []),
            "append_only": decl.get("append_only", False),
            "content_preview": content[:500],
            "message": f"DRY-RUN: Would create decision_log entry at {file_path}",
        }

    # 写入
    file_dir.mkdir(parents=True, exist_ok=True)
    if file_path.exists():
        return {
            "ok": False,
            "error": f"File already exists: {file_path}",
            "message": "Decision log entry already exists at this path. Use a different title/slug.",
        }

    file_path.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "dry_run": False,
        "declaration_key": "decision_log_entry",
        "target_path": str(file_path),
        "required_frontmatter": decl.get("required_frontmatter", []),
        "append_only": decl.get("append_only", False),
        "message": f"Created decision_log entry: {file_path}",
    }


def add_stage(
    governance_root: Path,
    *,
    stage_name: str = "",
    status: str = "archived",
    snapshot_date: str | None = None,
    body: str = "",
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """生成 stage_archive 快照骨架。

    声明来源: config.schema governance_structure.file_declarations.stage_archive_snapshot
    """
    from tools.aipos_cli.workspace_config import stage_archive_root

    decl = _get_file_declaration("stage_archive_snapshot", repo_root)
    # AIPOS-F145 件①: 落点经唯一读取口 stage_archive_root(project.json paths.stage_archive_root 声明; 未声明 = 声明 path_key 所指缺省)
    target_dir = stage_archive_root(governance_root)

    # 命名: <date>_<stage-name>.md
    today = _today_str()
    slug = _slugify(stage_name) if stage_name else "stage"
    filename = f"{today.replace('-', '')}_{slug}.md"

    # 构建 frontmatter
    template_fm = dict(decl.get("template_frontmatter", {}))
    template_fm["status"] = status
    template_fm["stage_name"] = stage_name or "<stage-name>"
    template_fm["snapshot_date"] = snapshot_date or today

    frontmatter = _render_frontmatter(template_fm)

    # 构建内容
    content_parts = [frontmatter, ""]
    content_parts.append(f"# Stage Snapshot: {stage_name or '<stage-name>'}")
    content_parts.append("")
    content_parts.append(f"**Snapshot Date**: {snapshot_date or today}")
    content_parts.append(f"**Stage**: {stage_name or '<stage-name>'}")
    content_parts.append("")
    if body:
        content_parts.append(body)
    else:
        content_parts.append("## Summary")
        content_parts.append("")
        content_parts.append("<!-- 填写阶段关账摘要 -->")
        content_parts.append("")
        content_parts.append("## Deliverables")
        content_parts.append("")
        content_parts.append("<!-- 列出本阶段交付物 -->")
        content_parts.append("")
        content_parts.append("## Next Stage")
        content_parts.append("")
        content_parts.append("<!-- 填写下一阶段计划 -->")

    content = "\n".join(content_parts) + "\n"

    file_path = target_dir / filename

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "declaration_key": "stage_archive_snapshot",
            "target_path": str(file_path),
            "required_frontmatter": decl.get("required_frontmatter", []),
            "append_only": decl.get("append_only", False),
            "content_preview": content[:500],
            "message": f"DRY-RUN: Would create stage archive snapshot at {file_path}",
        }

    # 写入
    target_dir.mkdir(parents=True, exist_ok=True)
    if file_path.exists():
        return {
            "ok": False,
            "error": f"File already exists: {file_path}",
            "message": "Stage archive snapshot already exists at this path.",
        }

    file_path.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "dry_run": False,
        "declaration_key": "stage_archive_snapshot",
        "target_path": str(file_path),
        "required_frontmatter": decl.get("required_frontmatter", []),
        "append_only": decl.get("append_only", False),
        "message": f"Created stage archive snapshot: {file_path}",
    }


def add_doc(
    governance_root: Path,
    *,
    name: str = "",
    title: str = "",
    status: str = "active",
    body: str = "",
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """生成 governance doc 骨架。

    声明来源: config.schema governance_structure.file_declarations.governance_doc
    """
    decl = _get_file_declaration("governance_doc", repo_root)
    target_dir = _resolve_target_dir(decl["path_key"], governance_root, repo_root)

    slug = _slugify(name) if name else "doc"
    filename = f"{slug}.md"

    # 构建 frontmatter(AIPOS-F94 N6: 唯一渲染 governance_doc_frontmatter)
    frontmatter = governance_doc_frontmatter(status=status, repo_root=repo_root)

    # 构建内容
    content_parts = [frontmatter, ""]
    if title:
        content_parts.append(f"# {title}")
    else:
        content_parts.append(f"# {name}")
    content_parts.append("")
    if body:
        content_parts.append(body)
    else:
        content_parts.append("<!-- 填写文档内容 -->")

    content = "\n".join(content_parts) + "\n"

    file_path = target_dir / filename

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "declaration_key": "governance_doc",
            "target_path": str(file_path),
            "required_frontmatter": decl.get("required_frontmatter", []),
            "append_only": decl.get("append_only", False),
            "content_preview": content[:500],
            "message": f"DRY-RUN: Would create governance doc at {file_path}",
        }

    # 写入
    target_dir.mkdir(parents=True, exist_ok=True)
    if file_path.exists():
        return {
            "ok": False,
            "error": f"File already exists: {file_path}",
            "message": "Governance doc already exists at this path.",
        }

    file_path.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "dry_run": False,
        "declaration_key": "governance_doc",
        "target_path": str(file_path),
        "required_frontmatter": decl.get("required_frontmatter", []),
        "append_only": decl.get("append_only", False),
        "message": f"Created governance doc: {file_path}",
    }


def add_record(
    governance_root: Path,
    *,
    record_type: str = "",
    task_id: str = "",
    body: str = "",
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """生成 record 骨架。

    声明来源: config.schema governance_structure.file_declarations.record_file
    """
    decl = _get_file_declaration("record_file", repo_root)
    target_dir = _resolve_target_dir(decl["path_key"], governance_root, repo_root)

    # 命名: <record_type>_<task_id>_<timestamp>_<agent>.md
    timestamp = file_slug("digits", local_now())
    slug_rt = _slugify(record_type) if record_type else "record"
    slug_tid = _slugify(task_id) if task_id else "unknown"
    filename = f"{slug_rt}_{slug_tid}_{timestamp}.md"

    # 构建 frontmatter
    template_fm = dict(decl.get("template_frontmatter", {}))
    template_fm["record_type"] = record_type or "<record_type>"

    frontmatter = _render_frontmatter(template_fm)

    # 构建内容
    content_parts = [frontmatter, ""]
    content_parts.append(f"# Record: {record_type or '<record_type>'}")
    content_parts.append("")
    if task_id:
        content_parts.append(f"**Task**: {task_id}")
        content_parts.append("")
    if body:
        content_parts.append(body)
    else:
        content_parts.append("<!-- 填写记录内容 -->")

    content = "\n".join(content_parts) + "\n"

    # 记录文件放在 task_id 子目录下
    if task_id:
        file_path = target_dir / task_id / filename
    else:
        file_path = target_dir / filename

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "declaration_key": "record_file",
            "target_path": str(file_path),
            "required_frontmatter": decl.get("required_frontmatter", []),
            "append_only": decl.get("append_only", False),
            "content_preview": content[:500],
            "message": f"DRY-RUN: Would create record at {file_path}",
        }

    # 写入
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if file_path.exists():
        return {
            "ok": False,
            "error": f"File already exists: {file_path}",
            "message": "Record already exists at this path.",
        }

    file_path.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "dry_run": False,
        "declaration_key": "record_file",
        "target_path": str(file_path),
        "required_frontmatter": decl.get("required_frontmatter", []),
        "append_only": decl.get("append_only", False),
        "message": f"Created record: {file_path}",
    }


def list_declarations(repo_root: Path | None = None) -> dict[str, Any]:
    """列出所有文件声明(供 CLI 显示)。"""
    gs = get_governance_structure(repo_root)
    declarations = gs.get("file_declarations", {}) or {}
    return {
        "ok": True,
        "declarations": {
            key: {
                "path_key": decl.get("path_key", ""),
                "naming_pattern": decl.get("naming_pattern", ""),
                "required_frontmatter": decl.get("required_frontmatter", []),
                "append_only": decl.get("append_only", False),
                "description": decl.get("description", ""),
            }
            for key, decl in declarations.items()
            if isinstance(decl, dict)  # skip the description string
        },
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
