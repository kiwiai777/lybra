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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.schema_loader import load_schema, resolve_governance_path, get_governance_structure


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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
    """渲染 YAML frontmatter。"""
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


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
    template_fm["decided_at"] = decided_at or _utc_now()
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
    decl = _get_file_declaration("stage_archive_snapshot", repo_root)
    target_dir = _resolve_target_dir(decl["path_key"], governance_root, repo_root)

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

    # 构建 frontmatter
    template_fm = dict(decl.get("template_frontmatter", {}))
    template_fm["status"] = status

    frontmatter = _render_frontmatter(template_fm)

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
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
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
