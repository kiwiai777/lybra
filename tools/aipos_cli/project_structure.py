"""AIPOS-293: Project structure file schema + export/import verbs.

Schema (lybra-project.yaml): versioned YAML capturing project name, description
(bilingual optional), code_repos[], governance file mappings, role declarations,
and existing document source manifest (path->target mapping).

Red lines:
- import NEVER removes user files (project created by `lybra project new`'s single implementation
  workspace_config.scaffold_project + a migration checklist; AIPOS-F117 件①)
- structure file contains ZERO credential values
- read-before-write discipline
- non-empty target protection on import (PROJECT_EXISTS, same refusal as project new)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# AIPOS-R4B-1 FIX-2: schema_loader 导入移至函数内(惰性),避免 CLI 早期导入链在 editable-install
# 环境下 ModuleNotFoundError。见 AUDIT-R4B-1 F-R4B1-1。




# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
STRUCTURE_FILENAME = "lybra-project.yaml"
MIGRATION_CHECKLIST_FILENAME = "migration-checklist.md"

# AIPOS-F117 件①(Owner 10-04 裁定 gap #53): 原 import 自建项目骨架的常量(STANDARD_FIVE_PIECE 标准五件套目录、
# LYBRA_IGNORE_PATTERNS .lybra/.gitignore 内容)随第二实现删除——导入建项目一律经 workspace_config.scaffold_project
# (`lybra project new` 唯一实现), 目录形状只在 governance_paths / 队列 skeleton 声明一处。

# Canonical governance files mapping
CANONICAL_GOVERNANCE_FILES = {
    "decision_log": "governance/decision_log.md",
    "project_map": "governance/project-map.md",
}

# Credential-detection patterns (red line: never include in structure file)
_CREDENTIAL_PATTERNS = re.compile(
    r"(api[_-]?key|secret|password|token|credential|private[_-]?key|bearer)",
    re.IGNORECASE,
)

# Safe YAML subset parser (zero-dep: we parse our own generated YAML)
_YAML_LINE_RE = re.compile(r"^(\s*)(- )?([^:]+?)\s*:\s*(.*)$")
_YAML_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Zero-dependency YAML emitter (stdlib only, matches our schema shape)
# ---------------------------------------------------------------------------

def _yaml_emit_value(value: Any, indent: int = 0) -> list[str]:
    """Emit a YAML value. Handles str/int/bool/None/list/dict."""
    prefix = "  " * indent
    lines: list[str] = []
    if value is None:
        lines.append("null")
    elif isinstance(value, bool):
        lines.append("true" if value else "false")
    elif isinstance(value, (int, float)):
        lines.append(str(value))
    elif isinstance(value, str):
        # Quote strings that contain special chars or look like booleans/null
        if (
            not value
            or value.lower() in ("true", "false", "null", "yes", "no")
            or any(c in value for c in ":#{}[]|>&*!%@`\"'\\")
            or value.startswith((" ", "- "))
        ):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'"{escaped}"')
        else:
            lines.append(value)
    elif isinstance(value, list):
        if not value:
            lines.append("[]")
        else:
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    if i == 0:
                        sub = _yaml_emit_dict(item, indent)
                        lines.append(f"- {sub[0].lstrip()}")
                        lines.extend(sub[1:])
                    else:
                        sub = _yaml_emit_dict(item, indent)
                        lines.append(f"- {sub[0].lstrip()}")
                        lines.extend(sub[1:])
                else:
                    emitted = _yaml_emit_value(item, indent + 1)[0]
                    lines.append(f"- {emitted}")
    elif isinstance(value, dict):
        if not value:
            lines.append("{}")
        else:
            sub = _yaml_emit_dict(value, indent)
            lines.extend(sub)
    else:
        lines.append(str(value))
    return lines


def _yaml_emit_dict(d: dict[str, Any], indent: int = 0) -> list[str]:
    """Emit a YAML dict."""
    prefix = "  " * indent
    lines: list[str] = []
    for key, value in d.items():
        if isinstance(value, (dict, list)) and value:
            lines.append(f"{prefix}{key}:")
            sub = _yaml_emit_value(value, indent + 1)
            if isinstance(value, list):
                for s in sub:
                    lines.append(f"{prefix}  {s}")
            else:
                lines.extend(sub)
        else:
            emitted = _yaml_emit_value(value, indent + 1)[0]
            lines.append(f"{prefix}{key}: {emitted}")
    return lines


def emit_yaml(data: dict[str, Any]) -> str:
    """Emit a complete YAML document from a dict."""
    lines = ["# Lybra project structure file", f"# Schema version: {SCHEMA_VERSION}", f"# Generated: {_utc_now_iso()}", ""]
    lines.extend(_yaml_emit_dict(data))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Zero-dependency YAML parser (handles our schema's subset)
# ---------------------------------------------------------------------------

def parse_yaml(text: str) -> dict[str, Any]:
    """Parse the YAML subset we generate. Handles scalars, lists, nested dicts.

    Uses a line-by-line approach with an explicit stack of (indent, container) pairs.
    The container is the dict or list being filled at that indent level.
    """
    result: dict[str, Any] = {}
    # Stack: list of (indent_level, container_being_filled)
    # The container is always a dict or list.
    stack: list[tuple[int, Any]] = [(-1, result)]
    # Track the last key set at each level so we know what to convert when list items appear
    last_key_at: dict[int, tuple[Any, str]] = {}  # indent -> (parent_dict, key)

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Pop stack to find the correct parent for this indent level
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        _, parent = stack[-1]

        # List item
        list_match = _YAML_LIST_ITEM_RE.match(raw_line)
        if list_match:
            item_content = list_match.group(1).strip()
            # Check if the list item starts a dict (contains a key: value)
            item_kv = _YAML_LINE_RE.match(item_content)
            if item_kv:
                # List item is a dict: "- key: value"
                item_dict: dict[str, Any] = {}
                k = item_kv.group(3).strip()
                v = item_kv.group(4).strip()
                item_dict[k] = _parse_scalar(v) if v else {}

                # Find or create the parent list
                target_list = None
                if isinstance(parent, list):
                    target_list = parent
                elif isinstance(parent, dict) and not parent:
                    # Convert empty dict to list (same as scalar list items)
                    for prev_indent in sorted(last_key_at.keys(), reverse=True):
                        if prev_indent < indent:
                            prev_parent, prev_key = last_key_at[prev_indent]
                            if prev_parent.get(prev_key) is parent:
                                prev_parent[prev_key] = []
                                target_list = prev_parent[prev_key]
                                stack[-1] = (stack[-1][0], target_list)
                                break
                if target_list is not None:
                    target_list.append(item_dict)
                    # Push the dict so subsequent indented keys go into it.
                    # Use the same indent as the "- " line so that keys at deeper
                    # indents (e.g., indent+2) don't trigger a pop.
                    stack.append((indent, item_dict))
                continue

            item_value = _parse_scalar(item_content)
            # Check if parent is an empty dict that was set as a value of some key
            # in an ancestor dict — if so, convert it to a list
            if isinstance(parent, dict) and not parent:
                # Find the key in an ancestor dict that points to this empty dict
                converted = False
                for prev_indent in sorted(last_key_at.keys(), reverse=True):
                    if prev_indent < indent:
                        prev_parent, prev_key = last_key_at[prev_indent]
                        if prev_parent.get(prev_key) is parent:
                            # This empty dict was the value of prev_key — convert to list
                            prev_parent[prev_key] = []
                            the_list = prev_parent[prev_key]
                            the_list.append(item_value)
                            # Replace this stack entry with the list
                            stack[-1] = (stack[-1][0], the_list)
                            converted = True
                            break
                if not converted:
                    pass  # orphan empty dict, skip
            elif isinstance(parent, list):
                parent.append(item_value)
            continue

        # Key-value pair
        kv_match = _YAML_LINE_RE.match(raw_line)
        if kv_match:
            key = kv_match.group(3).strip()
            raw_value = kv_match.group(4).strip()

            if raw_value:
                # Inline scalar value
                value = _parse_scalar(raw_value)
                if isinstance(parent, dict):
                    parent[key] = value
            else:
                # Value on following lines — tentatively set as {} (could become [] if list items follow)
                if isinstance(parent, dict):
                    parent[key] = {}
                    last_key_at[indent] = (parent, key)
                    # Push the new dict as the container for subsequent indented lines
                    stack.append((indent, parent[key]))
            continue

    return result


def _parse_scalar(value: str) -> Any:
    """Parse a YAML scalar value."""
    if not value:
        return ""
    # Quoted string
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    # Boolean
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    # Null
    if value.lower() in ("null", "~"):
        return None
    # Integer
    try:
        return int(value)
    except ValueError:
        pass
    # Float
    try:
        return float(value)
    except ValueError:
        pass
    # Inline list [a, b, c]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        if not inner.strip():
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    # Inline empty dict {}
    if value == "{}":
        return {}
    return value


# ---------------------------------------------------------------------------
# Credential safety scanner (red line: zero credential values in structure file)
# ---------------------------------------------------------------------------

def _scan_for_credentials(value: Any, path: str = "") -> list[str]:
    """Scan a value tree for anything that looks like a credential."""
    findings: list[str] = []
    if isinstance(value, str):
        if _CREDENTIAL_PATTERNS.search(value) and len(value) > 8:
            # Heuristic: looks like a key name + value combo
            findings.append(f"{path}: possible credential value (contains secret-like pattern)")
    elif isinstance(value, dict):
        for k, v in value.items():
            findings.extend(_scan_for_credentials(v, f"{path}.{k}" if path else k))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            findings.extend(_scan_for_credentials(item, f"{path}[{i}]"))
    return findings


def _check_no_credentials(data: dict[str, Any]) -> list[str]:
    """Verify the structure file data contains no credential values."""
    # Check specific known fields that should never contain secrets
    sensitive_keys = {"token", "secret", "password", "api_key", "private_key", "credential", "bearer"}
    findings: list[str] = []

    def walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                current_path = f"{path}.{k}" if path else k
                if k.lower() in sensitive_keys and isinstance(v, str) and v.strip():
                    findings.append(f"{current_path}: credential-like key with non-empty value")
                walk(v, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]")

    walk(data)
    return findings


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def validate_structure(data: dict[str, Any]) -> list[str]:
    """Validate a project structure dict against the schema. Returns list of errors."""
    errors: list[str] = []

    # Required fields
    if "schema_version" not in data:
        errors.append("missing required field: schema_version")
    elif data["schema_version"] != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {data['schema_version']} (expected {SCHEMA_VERSION})")

    if "project_name" not in data:
        errors.append("missing required field: project_name")
    elif not isinstance(data["project_name"], str) or not data["project_name"].strip():
        errors.append("project_name must be a non-empty string")

    # code_repos must be a list if present
    if "code_repos" in data and not isinstance(data["code_repos"], list):
        errors.append("code_repos must be a list")

    # governance_files must be a dict if present
    if "governance_files" in data and not isinstance(data["governance_files"], dict):
        errors.append("governance_files must be a mapping")

    # Credential scan (red line)
    cred_findings = _check_no_credentials(data)
    errors.extend(cred_findings)

    return errors


# ---------------------------------------------------------------------------
# Export: walk a workspace and generate the structure file
# ---------------------------------------------------------------------------

def export_project_structure(
    workspace_root: str | Path,
    *,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Export a workspace's structure into a project structure dict.

    Walks the workspace at `workspace_root` and captures:
    - project name (from project.json or explicit)
    - description (from README.md first paragraph if available)
    - code_repos (from project.json)
    - governance file mappings (which canonical files exist)
    - existing document manifest (all .md files with relative paths)
    - role declarations (from AGENTS.md if present)
    """
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Workspace root not found: {root}")

    # Read project.json if present
    project_json_path = root / "project.json"
    project_data: dict[str, Any] = {}
    if project_json_path.is_file():
        project_data = json.loads(project_json_path.read_text(encoding="utf-8"))

    name = project_name or project_data.get("project") or root.name

    # Description from README
    description = ""
    description_en = ""
    readme_path = root / "README.md"
    if readme_path.is_file():
        readme_text = readme_path.read_text(encoding="utf-8")
        # First non-heading line as description
        for line in readme_text.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped[:200]
                break

    # Code repos(AIPOS-F78C: 项目级读 repos 清单列全部仓, 缺省仓在首位; 单仓项目 = code_repo 别名一项)
    from tools.aipos_cli.workspace_config import project_repos

    repos = project_repos(root)  # 清单不一致 = CardRepoUnresolved(REPOS_CONFLICT) 出声, 不吞
    code_repos: list[str] = []
    if repos["declared"]:
        code_repos.append(str(repos["items"][repos["default"]]))
        code_repos.extend(str(path) for name, path in repos["items"].items() if name != repos["default"])
    elif repos["code_repo"] is not None:
        code_repos.append(str(repos["code_repo"]))

    # Governance file mappings (which canonical files exist)
    governance_files: dict[str, str] = {}
    for key, rel_path in CANONICAL_GOVERNANCE_FILES.items():
        full_path = root / rel_path
        if full_path.is_file():
            governance_files[key] = rel_path
    # Also check for decision_log as directory
    decision_log_dir = root / "governance" / "decision_log"
    if decision_log_dir.is_dir():
        governance_files["decision_log_dir"] = "governance/decision_log/"

    # Existing document manifest (all .md files, relative paths)
    doc_manifest: list[dict[str, str]] = []
    for md_file in sorted(root.rglob("*.md")):
        # Skip hidden dirs and node_modules
        rel = md_file.relative_to(root)
        if any(part.startswith(".") or part == "node_modules" for part in rel.parts):
            continue
        doc_manifest.append({
            "source_path": rel.as_posix(),
            "target_path": rel.as_posix(),
            "kind": _classify_doc(rel.as_posix()),
        })

    # Role declarations (from AGENTS.md or CLAUDE.md)
    roles: list[dict[str, str]] = []
    for agents_file in ("governance/AGENTS.md", "CLAUDE.md", "AGENTS.md"):
        agents_path = root / agents_file
        if agents_path.is_file():
            roles.append({
                "file": agents_file,
                "kind": "role_charter",
            })

    # Queue state counts (summary)
    queue_summary: dict[str, int] = {}
    from tools.aipos_cli.task_loader import QUEUE_STATES, queue_root_for  # AIPOS-F89 件① M8 + AIPOS-F104 件②: 队列根/状态唯一读取口(惰性导入, 见文件头)

    for state in QUEUE_STATES:
        state_dir = queue_root_for(root) / state
        if state_dir.is_dir():
            count = sum(1 for f in state_dir.iterdir() if f.is_file() and f.suffix == ".md")
            queue_summary[state] = count

    structure: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": name,
        "description": description,
        "description_en": description_en,
        "code_repos": code_repos,
        "registered_at": project_data.get("registered_at"),
        "registered_by": project_data.get("registered_by"),
        "governance_files": governance_files,
        "roles": roles,
        "doc_manifest": doc_manifest,
        "queue_summary": queue_summary,
        "exported_at": _utc_now_iso(),
        "export_source": str(root),
    }

    return structure


def _classify_doc(rel_path: str) -> str:
    """Classify a document by its path."""
    if rel_path.startswith("governance/"):
        return "governance"
    if rel_path.startswith("5_tasks/"):
        return "task"
    if rel_path.startswith("stage_archive/"):
        return "archive"
    if rel_path.startswith("workspace_artifacts/"):
        return "artifact"
    return "general"


def export_project_to_yaml(
    workspace_root: str | Path,
    *,
    project_name: str | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Export workspace structure to YAML file. Returns result dict."""
    root = Path(workspace_root).expanduser().resolve()
    structure = export_project_structure(root, project_name=project_name)

    # Validate before emitting (red line: no credentials)
    errors = validate_structure(structure)
    if errors:
        return {
            "ok": False,
            "operation": "project_export",
            "blocking_reasons": errors,
            "structure": None,
            "output_path": None,
        }

    yaml_text = emit_yaml(structure)

    # Determine output path
    if output_path:
        out = Path(output_path).expanduser().resolve()
    else:
        out = root / STRUCTURE_FILENAME

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml_text, encoding="utf-8")

    return {
        "ok": True,
        "operation": "project_export",
        "structure": structure,
        "output_path": str(out),
        "yaml_byte_size": len(yaml_text.encode("utf-8")),
        "doc_count": len(structure.get("doc_manifest", [])),
        "governance_files": list(structure.get("governance_files", {}).keys()),
    }


# ---------------------------------------------------------------------------
# Import: project new (scaffold_project) + migration checklist (AIPOS-F117 件①)
# ---------------------------------------------------------------------------

def _load_structure_from_yaml(yaml_text: str) -> dict[str, Any]:
    """Load and validate structure from YAML text."""
    data = parse_yaml(yaml_text)
    errors = validate_structure(data)
    if errors:
        raise ValueError(f"Structure file validation failed: {'; '.join(errors)}")
    return data


def import_project_structure(
    structure_file: str | Path,
    home_root: str | Path,
    *,
    name: str | None = None,
    dry_run: bool = False,
    actor: str | None = None,
) -> dict[str, Any]:
    """从结构文件导入既有项目 = `lybra project new` 同一实现建项目 + 迁移清单(AIPOS-F117 件①, Owner 10-04 裁定 gap #53)。

    建项目只有一个实现: workspace_config.scaffold_project(`lybra project new` 本地脚手架与门动词 lybra_project_new 同调)。
    本函数只做导入特有的两件事: ① 读结构文件(schema 校验 + 零凭据红线)取项目名与代码仓; ② 建成后在项目根写迁移清单
    (结构文件 doc_manifest 中待人工迁移的文档, 只列不搬, 源文件永不删除)。原自写 project.json / .lybra/config.json /
    队列骨架 / 治理桩 / README 的第二实现已删。

    落点 = <home_root>/<项目名>(project_root_for, 与 project new 同一推导); home_root 由调用方经 AIPOS-226 优先级梯
    (workspace_config.resolve_home_root: --home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json home_root > 缺省 ~/.lybra/projects)
    解析——看板不再自建 ~/.lybra/workspaces/。项目名 = name(显式)或结构文件 project_name。目标非空 = 拒(PROJECT_EXISTS,
    与 project new 同一拒因, 不再「幂等跳过」)。dry_run = 只读预览, 零写入。
    """
    from tools.aipos_cli.workspace_config import project_json_path, project_root_for, scaffold_project

    def blocked(reason: str) -> dict[str, Any]:
        return {"ok": False, "operation": "project_import", "blocking_reasons": [reason], "dry_run": dry_run}

    structure_path = Path(structure_file).expanduser().resolve()
    if not structure_path.is_file():
        return blocked(f"Structure file not found: {structure_path}")

    try:
        structure = _load_structure_from_yaml(structure_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return blocked(str(exc))

    # Final credential check (red line)
    cred_findings = _check_no_credentials(structure)
    if cred_findings:
        return blocked(f"Credential values detected in structure file: {cred_findings}")

    project_name = str(name or structure.get("project_name") or "").strip()
    if not project_name:
        return blocked("PROJECT_NAME_EMPTY: 结构文件无 project_name 且未显式给项目名")
    home = Path(home_root).expanduser().resolve()
    project_root = project_root_for(home, project_name)
    if project_root.exists() and (not project_root.is_dir() or next(project_root.iterdir(), None) is not None):
        return blocked(f"PROJECT_EXISTS: project root not empty: {project_root}(导入不覆盖、不删除既有文件; 换项目名)")

    code_repos = structure.get("code_repos") or []
    code_repo = str(code_repos[0]) if code_repos else None
    doc_manifest = structure.get("doc_manifest", [])
    migration_items = [item for item in doc_manifest if item.get("kind") in ("governance", "general")]

    result: dict[str, Any] = {
        "ok": True,
        "operation": "project_import",
        "dry_run": dry_run,
        "project_name": project_name,
        "home_root": str(home),
        "project_root": str(project_root),
        "output_root": str(project_root),
        "project_json": str(project_json_path(project_root)),
        "code_repo": code_repo,
        "created_by": "workspace_config.scaffold_project(lybra project new 唯一实现)",
        "migration_checklist": MIGRATION_CHECKLIST_FILENAME,
        "migration_item_count": len(migration_items),
        "actor": actor,
        "structure_file": str(structure_path),
    }
    if dry_run:
        result["verdict"] = Verdict.PASS
        return result

    try:
        root = scaffold_project(home, project_name, code_repo=code_repo, registered_by=actor or "import")
    except (FileExistsError, ValueError, RuntimeError, OSError) as exc:
        return blocked(f"project new 脚手架拒绝: {exc}")
    checklist = root / MIGRATION_CHECKLIST_FILENAME
    checklist.write_text(_build_migration_checklist(project_name, migration_items, structure), encoding="utf-8")
    result["wrote"] = True
    return result


def _build_migration_checklist(
    project_name: str,
    items: list[dict[str, str]],
    structure: dict[str, Any],
) -> str:
    """Build a migration checklist markdown document."""
    lines = [
        f"# {project_name} — Migration Checklist",
        "",
        "This checklist was auto-generated from the project structure file.",
        "An advisor should review each item and execute the migration.",
        "",
        f"**Generated:** {_utc_now_iso()}",
        f"**Source:** {structure.get('export_source', 'unknown')}",
        "",
    ]

    if not items:
        lines.append("_No documents require migration._")
        return "\n".join(lines) + "\n"

    lines.append("## Documents to Review")
    lines.append("")
    lines.append("| # | Source Path | Kind | Target Path | Status |")
    lines.append("|---|---|---|---|---|")

    for i, item in enumerate(items, 1):
        source = item.get("source_path", "?")
        kind = item.get("kind", "?")
        target = item.get("target_path", source)
        lines.append(f"| {i} | `{source}` | {kind} | `{target}` | ⬜ pending |")

    lines.append("")
    lines.append("## Instructions")
    lines.append("")
    lines.append("1. Review each document above for relevance and currency")
    lines.append("2. Copy/move documents to their target paths as needed")
    lines.append("3. Update governance files (decision_log, etc.)")
    lines.append("4. Mark items as ✅ done when migration is complete")
    lines.append("")
    lines.append("**Red line:** This import process NEVER deletes source files.")
    lines.append("All migration is copy-based; original files remain untouched.")
    lines.append("")

    return "\n".join(lines) + "\n"


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
from tools.schema_constants import Verdict
check_direct_invocation(__name__)
