from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, make_response
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

TEMPLATE_OPERATION = "workspace_init"
TEMPLATE_KIND = "workspace_project_skeleton"
SAFETY_NOTICE = (
    "Workspace init is controlled execute only: dry-run previews all planned files, "
    "then confirm revalidates the snapshot before writing."
)
PLACEHOLDER_RE = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")
ANY_PLACEHOLDER_RE = re.compile(r"{{.*?}}|{%.*?%}|\${.*?}")
PROJECT_ID_RE = re.compile(r"^[a-z0-9_-]+$")
TEMPLATE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def product_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def templates_root(repo_root: Path | None = None) -> Path:
    return (repo_root or product_repo_root()) / "templates"


def _actor_payload(actor: str | None) -> dict[str, str] | None:
    text = str(actor or "").strip()
    return {"actor": text} if text else None


def _normalize_template_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("template name is required")
    if not TEMPLATE_ID_RE.fullmatch(value):
        raise ValueError("template name must use lowercase kebab-case")
    return value


def _parse_var_items(items: list[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError("--var values must use k=v")
        key, value = item.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"invalid variable name: {key}")
        values[key] = value
    return values


def parse_var_items(items: list[str] | None) -> dict[str, str]:
    return _parse_var_items(items)


def _load_manifest(template_dir: Path) -> tuple[dict[str, Any], list[str]]:
    manifest_path = template_dir / "manifest.md"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Template manifest missing: {manifest_path}")
    metadata, _body, warnings = parse_markdown_frontmatter(manifest_path.read_text(encoding="utf-8"))
    return metadata, warnings


def discover_templates(repo_root: Path | None = None) -> dict[str, dict[str, Any]]:
    root = templates_root(repo_root)
    discovered: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return discovered
    for template_dir in sorted(path for path in root.iterdir() if path.is_dir() and not _is_ignored_template_metadata(path)):
        try:
            metadata, warnings = _load_manifest(template_dir)
        except Exception as exc:
            discovered[template_dir.name] = {
                "template_id": template_dir.name,
                "path": str(template_dir.relative_to(root.parent)),
                "valid": False,
                "warnings": [],
                "blocking_reasons": [str(exc)],
            }
            continue
        discovered[template_dir.name] = {
            "template_id": metadata.get("template_id") or template_dir.name,
            "display_name": metadata.get("display_name") or template_dir.name,
            "description": metadata.get("description") or "",
            "template_version": metadata.get("template_version"),
            "template_status": metadata.get("template_status"),
            "template_kind": metadata.get("template_kind"),
            "path": str(template_dir.relative_to(root.parent)),
            "valid": True,
            "warnings": warnings,
            "blocking_reasons": [],
        }
    return discovered


def _manifest_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _manifest_bool(metadata: dict[str, Any], section: str, key: str, default: bool) -> bool:
    raw = metadata.get(section)
    if isinstance(raw, dict) and key in raw:
        return bool(raw[key])
    return default


def _validate_manifest(template_name: str, template_dir: Path, metadata: dict[str, Any]) -> list[str]:
    blocking: list[str] = []
    if metadata.get("template_id") != template_name:
        blocking.append("manifest template_id must match directory name")
    if not isinstance(metadata.get("template_version"), int):
        blocking.append("manifest template_version must be an integer")
    if metadata.get("template_kind") != TEMPLATE_KIND:
        blocking.append(f"manifest template_kind must be {TEMPLATE_KIND}")
    if _manifest_bool(metadata, "output_policy", "remote_fetch_allowed", True):
        blocking.append("manifest output_policy.remote_fetch_allowed must be false")
    if not _manifest_bool(metadata, "output_policy", "output_must_be_absent_or_empty", False):
        blocking.append("manifest output_policy.output_must_be_absent_or_empty must be true")
    if _manifest_bool(metadata, "output_policy", "overwrite_existing_files", True):
        blocking.append("manifest output_policy.overwrite_existing_files must be false")
    if not _manifest_bool(metadata, "controlled_execute", "dry_run_required", False):
        blocking.append("manifest controlled_execute.dry_run_required must be true")
    if not _manifest_bool(metadata, "controlled_execute", "confirm_required", False):
        blocking.append("manifest controlled_execute.confirm_required must be true")
    if not (template_dir / "tree").is_dir():
        blocking.append("template tree/ directory is required")
    return blocking


def _normalize_variables(metadata: dict[str, Any], variables: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    warnings: list[str] = []
    blocking: list[str] = []
    values = {str(key): str(value) for key, value in variables.items()}
    required = _manifest_list(metadata.get("required_variables"))
    optional = _manifest_list(metadata.get("optional_variables"))
    allowed = set(required + optional)

    if "project_id" in required:
        project_id = values.get("project_id", "").strip()
        if not project_id:
            blocking.append("missing required variable: project_id")
        elif not PROJECT_ID_RE.fullmatch(project_id):
            blocking.append("project_id must use lowercase letters, numbers, dash, or underscore")

    if "client_id" in optional and "client_id" not in values and values.get("project_id"):
        values["client_id"] = values["project_id"]
    if "client_name" in optional and "client_name" not in values and values.get("client_id"):
        values["client_name"] = values["client_id"]

    for key in required:
        if not values.get(key):
            blocking.append(f"missing required variable: {key}")
    for key in values:
        if key not in allowed:
            blocking.append(f"unknown variable supplied: {key}")
    return values, warnings, blocking


def _render_text(text: str, values: dict[str, str], *, context: str) -> tuple[str, list[str]]:
    blocking: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            blocking.append(f"unknown placeholder {key} in {context}")
            return match.group(0)
        return values[key]

    rendered = PLACEHOLDER_RE.sub(replace, text)
    for leftover in ANY_PLACEHOLDER_RE.findall(rendered):
        blocking.append(f"unresolved or unsupported placeholder syntax in {context}: {leftover}")
    return rendered, blocking


def _safe_rendered_relpath(source_rel: Path, values: dict[str, str]) -> tuple[Path | None, list[str]]:
    rendered, blocking = _render_text(source_rel.as_posix(), values, context=f"path {source_rel.as_posix()}")
    candidate = Path(rendered)
    if candidate.is_absolute() or ".." in candidate.parts:
        blocking.append(f"rendered path is unsafe: {rendered}")
    if any(part == "" for part in candidate.parts):
        blocking.append(f"rendered path contains an empty segment: {rendered}")
    return (candidate if not blocking else None), blocking


def _output_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    return next(path.iterdir(), None) is None


def _is_ignored_template_metadata(path: Path) -> bool:
    return path.name == ".DS_Store" or path.name.startswith("._")


def _path_state(output_root: Path) -> dict[str, Any]:
    if not output_root.exists():
        return {"path": str(output_root), "exists": False, "is_dir": False, "empty": True}
    return {
        "path": str(output_root),
        "exists": True,
        "is_dir": output_root.is_dir(),
        "empty": _output_is_empty(output_root),
    }


def _build_plan(
    *,
    template: str,
    output: str | Path,
    variables: dict[str, str],
    template_repo_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[str], dict[str, str], dict[str, Any]]:
    template_name = _normalize_template_name(template)
    root = templates_root(template_repo_root)
    template_dir = root / template_name
    if not template_dir.is_dir():
        raise FileNotFoundError(f"Template not found: {template_name}")
    metadata, manifest_warnings = _load_manifest(template_dir)
    warnings = list(manifest_warnings)
    blocking = _validate_manifest(template_name, template_dir, metadata)
    rendered_variables, variable_warnings, variable_blocking = _normalize_variables(metadata, variables)
    warnings.extend(variable_warnings)
    blocking.extend(variable_blocking)

    output_root = Path(output).expanduser().resolve()
    output_state = _path_state(output_root)
    if output_state["exists"] and (not output_state["is_dir"] or not output_state["empty"]):
        blocking.append("output path must be absent or an empty directory")

    planned_writes: list[dict[str, Any]] = []
    tree_root = template_dir / "tree"
    if tree_root.is_dir():
        for source_path in sorted(
            path
            for path in tree_root.rglob("*")
            if path.is_file() and not any(_is_ignored_template_metadata(part) for part in [path, *path.parents])
        ):
            if source_path.is_symlink():
                blocking.append(f"template symlink is out of scope: {source_path.relative_to(tree_root).as_posix()}")
                continue
            source_rel = source_path.relative_to(tree_root)
            target_rel, path_blocking = _safe_rendered_relpath(source_rel, rendered_variables)
            blocking.extend(path_blocking)
            if target_rel is None:
                continue
            target_path = (output_root / target_rel).resolve()
            try:
                target_path.relative_to(output_root)
            except ValueError:
                blocking.append(f"rendered path escapes output root: {target_rel.as_posix()}")
                continue
            rendered_content, content_blocking = _render_text(
                source_path.read_text(encoding="utf-8"),
                rendered_variables,
                context=f"file {source_rel.as_posix()}",
            )
            blocking.extend(content_blocking)
            if target_path.exists():
                blocking.append(f"target file already exists: {target_rel.as_posix()}")
            planned_writes.append(
                {
                    "path": target_rel.as_posix(),
                    "kind": "file",
                    "type": "workspace_template_file",
                    "source_template_path": source_rel.as_posix(),
                    "content": rendered_content,
                    "byte_size": len(rendered_content.encode("utf-8")),
                }
            )
    summary = {
        "template": template_name,
        "output_path": str(output_root),
        "planned_file_count": len(planned_writes),
        "output_exists": output_state["exists"],
        "output_empty": output_state["empty"],
    }
    data = {
        "template": template_name,
        "manifest": metadata,
        "variables": rendered_variables,
        "output_path": str(output_root),
        "output_state": output_state,
        "original_payload": {
            "template": template_name,
            "output": str(output_root),
            "variables": rendered_variables,
        },
    }
    return summary, planned_writes, warnings, blocking, rendered_variables, data


def build_workspace_init_plan(
    *,
    template: str,
    output: str | Path,
    variables: dict[str, str],
    actor: str | None,
    dry_run: bool,
    template_repo_root: Path | None = None,
) -> dict[str, Any]:
    try:
        summary, planned_writes, warnings, blocking, _rendered_variables, data = _build_plan(
            template=template,
            output=output,
            variables=variables,
            template_repo_root=template_repo_root,
        )
        verdict = derive_verdict(blocking_reasons=blocking, warnings=warnings)
        return make_response(
            ok=not blocking,
            verdict=verdict,
            operation=TEMPLATE_OPERATION,
            dry_run=dry_run,
            actor=_actor_payload(actor),
            data=data,
            summary=summary,
            planned_writes=[
                {key: value for key, value in item.items() if key != "content"}
                for item in planned_writes
            ],
            warnings=warnings,
            blocking_reasons=blocking,
            owner_confirmation_required=not blocking,
            owner_confirmation_reasons=["Owner confirmation required before workspace template files are written"] if not blocking else [],
            execute_allowed=not blocking if dry_run else None,
            execute_blocking_reasons=blocking if dry_run else [],
            safety_notice=SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:
        return blocked_response(
            operation=TEMPLATE_OPERATION,
            dry_run=dry_run,
            category="VALIDATION_ERROR",
            message=str(exc),
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )


def execute_workspace_init(
    *,
    template: str,
    output: str | Path,
    variables: dict[str, str],
    actor: str | None,
    template_repo_root: Path | None = None,
) -> dict[str, Any]:
    plan = build_workspace_init_plan(
        template=template,
        output=output,
        variables=variables,
        actor=actor,
        dry_run=False,
        template_repo_root=template_repo_root,
    )
    if plan.get("blocking_reasons"):
        return plan
    data = plan.get("data") if isinstance(plan.get("data"), dict) else {}
    output_root = Path(str(data.get("output_path") or output)).expanduser().resolve()
    performed: list[dict[str, Any]] = []
    for item in _build_plan(template=template, output=output, variables=variables, template_repo_root=template_repo_root)[1]:
        target = output_root / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(item["content"]), encoding="utf-8")
        performed.append({key: value for key, value in item.items() if key != "content"})
    plan["ok"] = True
    plan["dry_run"] = False
    plan["wrote"] = True
    plan["performed_writes"] = performed
    plan["data"]["wrote"] = True
    plan["summary"]["wrote"] = True
    return plan
