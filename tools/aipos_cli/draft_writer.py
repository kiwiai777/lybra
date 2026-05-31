from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_validator import (
    DRAFTS_DIR,
    PENDING_QUEUE_DIR,
    draft_slug,
    expected_pending_relative_path,
    find_case_insensitive_path_collision,
    read_draft_markdown,
    resolve_draft_path,
    resolve_pending_target_path,
    validate_draft_metadata,
)
from tools.aipos_cli.task_complexity import validate_task_complexity

EXTERNAL_INTAKE_EXECUTION_ASSIGNED_TO = "dev.claude.cc.local"
EXTERNAL_INTAKE_EXECUTION_OUTPUT_TARGET = "workspace_artifacts/external_intake"

DEFAULT_TEMPLATE_VALUES = {
    "project": "ai-project-os",
    "status": "pending",
    "needs_owner": False,
    "task_type": "one_shot",
    "polling_mode": "agent_polling",
    "claim_policy": "assigned_agent_only",
    "report_mode": "forum_reply",
    "recurrence": "none",
}

FRONTMATTER_ORDER = [
    "task_id",
    "title",
    "project",
    "task_type",
    "assigned_to",
    "agent_instance",
    "context_bundle",
    "task_mode",
    "task_class",
    "complexity_note",
    "model_tier",
    "priority",
    "status",
    "created_by",
    "needs_owner",
    "output_target",
    "artifact_policy",
    "polling_mode",
    "claim_policy",
    "report_mode",
    "recurrence",
    "draft_id",
    "draft_status",
    "draft_created_by",
    "draft_created_at",
    "draft_updated_at",
    "draft_publish_target",
    "draft_validation_summary",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return ""
    if any(char in text for char in [":", "#", "[", "]", "{", "}", "\n"]) or text != text.strip():
        return "'" + text.replace("'", "''") + "'"
    return text


def render_markdown_task_card(metadata: dict[str, Any], body: str) -> str:
    ordered_keys = [key for key in FRONTMATTER_ORDER if key in metadata]
    ordered_keys.extend(sorted(key for key in metadata if key not in ordered_keys))

    lines = ["---"]
    for key in ordered_keys:
        value = metadata[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"- {_yaml_scalar(item)}")
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.extend(["---", body.rstrip(), ""])
    return "\n".join(lines)


def default_draft_body() -> str:
    return "\n".join(
        [
            "## Goal",
            "",
            "- Describe the concrete task goal.",
            "",
            "## Context",
            "",
            "- Add relevant constraints, links, or prior decisions.",
            "",
            "## Acceptance Criteria",
            "",
            "- Define the minimum observable outcomes.",
            "",
            "## Completion Report Instructions",
            "",
            "- Summarize what changed, what was verified, and any remaining risks.",
            "",
        ]
    )


def load_create_payload_from_json(path: str | Path) -> tuple[dict[str, Any], str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Draft JSON payload must be an object")
    frontmatter = data.get("frontmatter")
    if not isinstance(frontmatter, dict):
        raise ValueError("Draft JSON payload must include a frontmatter object")
    body = data.get("body", default_draft_body())
    if body is None:
        body = default_draft_body()
    if not isinstance(body, str):
        raise ValueError("Draft JSON body must be a string when provided")
    return dict(frontmatter), body


def build_template_payload(template_name: str, values: dict[str, Any], body: str | None = None) -> tuple[dict[str, Any], str]:
    if template_name != "basic":
        raise ValueError(f"Unsupported draft template: {template_name}")
    metadata = {**DEFAULT_TEMPLATE_VALUES, **values}
    return metadata, body if body is not None else default_draft_body()


def load_body_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalized_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(metadata)
    normalized.setdefault("status", "pending")
    normalized.setdefault("needs_owner", False)
    if normalized.get("task_class") in (None, ""):
        normalized["task_class"] = "simple"
    if normalized.get("complexity_note") in (None, ""):
        normalized.pop("complexity_note", None)
    task_id = normalized.get("task_id")
    created_by = normalized.get("created_by")
    timestamp = _utc_now()
    if isinstance(task_id, str) and task_id:
        normalized.setdefault("draft_id", f"draft_{draft_slug(task_id)}")
    normalized.setdefault("draft_status", "draft")
    if created_by not in (None, ""):
        normalized.setdefault("draft_created_by", created_by)
    normalized.setdefault("draft_created_at", timestamp)
    normalized.setdefault("draft_updated_at", timestamp)
    normalized.setdefault("draft_publish_target", "5_tasks/queue/pending/")
    return normalized


def _is_external_intake_draft(source_path: Path, repo_root: Path, metadata: dict[str, Any]) -> bool:
    try:
        rel_parts = source_path.resolve().relative_to((repo_root / DRAFTS_DIR / "external_intake").resolve()).parts
        if rel_parts:
            return True
    except ValueError:
        pass
    return metadata.get("context_bundle") == "external_intake" or metadata.get("draft_id", "").startswith("external_intake_")


def _external_intake_execution_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata)
    title = str(updated.get("title") or "")
    prefix = "Review external intake: "
    if title.startswith(prefix):
        updated["title"] = title[len(prefix) :]
    updated["assigned_to"] = updated.get("handoff_assigned_to") or EXTERNAL_INTAKE_EXECUTION_ASSIGNED_TO
    updated["agent_instance"] = updated.get("handoff_agent_instance") or updated["assigned_to"]
    updated["context_bundle"] = "external_intake_execution"
    updated["task_mode"] = "coding"
    updated["model_tier"] = updated.get("model_tier") or "L2"
    updated["needs_owner"] = False
    updated["output_target"] = updated.get("handoff_output_target") or EXTERNAL_INTAKE_EXECUTION_OUTPUT_TARGET
    updated["artifact_policy"] = "formal_write"
    updated["polling_mode"] = "agent_polling"
    updated["claim_policy"] = "assigned_agent_only"
    updated["report_mode"] = "completion_summary"
    updated["handoff_source"] = "external_intake"
    updated["owner_review_completed"] = True
    return updated


def create_draft(
    repo_root: Path,
    metadata: dict[str, Any],
    body: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized = _normalized_metadata(metadata)
    rendered_markdown = render_markdown_task_card(normalized, body or default_draft_body())
    validation = validate_draft_metadata(repo_root, normalized)
    target_path = validation["target_path"]
    planned_writes = []

    if target_path:
        planned_writes.append(
            {
                "path": target_path,
                "kind": "create",
                "type": "draft_markdown",
            }
        )

    result: dict[str, Any] = {
        "action": "draft_create",
        "dry_run": dry_run,
        "task_id": normalized.get("task_id"),
        "verdict": validation["verdict"],
        "blocking_reasons": validation["blocking_reasons"],
        "warnings": validation["warnings"],
        "classification_warnings": list(validation.get("classification_warnings", [])),
        "target_path": target_path,
        "planned_writes": planned_writes,
    }

    if dry_run:
        result["would_write"] = validation["verdict"] != "BLOCK" and bool(target_path)
        result["rendered_markdown"] = rendered_markdown
        return result

    if validation["verdict"] == "BLOCK" or not target_path:
        result["wrote"] = False
        return result

    drafts_root = repo_root / DRAFTS_DIR
    target_file = repo_root / target_path
    if target_file.exists():
        result["verdict"] = "BLOCK"
        result["wrote"] = False
        result["blocking_reasons"] = [*result["blocking_reasons"], f"Draft file already exists: {target_path}"]
        return result

    drafts_root.mkdir(parents=True, exist_ok=True)
    target_file.write_text(rendered_markdown, encoding="utf-8")
    result["wrote"] = True
    return result


def publish_draft(
    repo_root: Path,
    draft_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    source_path = resolve_draft_path(repo_root, draft_path)
    validation = {
        "action": "draft_validate",
        "path": str(Path(draft_path)),
        "task_id": None,
        "verdict": "BLOCK",
        "blocking_reasons": [],
        "warnings": [],
        "frontmatter": {},
    }
    result: dict[str, Any] = {
        "action": "draft_publish",
        "dry_run": dry_run,
        "source_path": str(source_path.relative_to(repo_root)),
        "target_path": None,
        "task_id": None,
        "verdict": "BLOCK",
        "blocking_reasons": [],
        "warnings": [],
        "planned_writes": [],
        "validation": validation,
    }

    if not source_path.exists():
        reason = f"Draft path does not exist: {draft_path}"
        validation["blocking_reasons"] = [reason]
        result["blocking_reasons"] = [reason]
        result["would_write"] = False
        result["wrote"] = False
        return result
    if not source_path.is_file():
        reason = f"Draft path is not a file: {draft_path}"
        validation["blocking_reasons"] = [reason]
        result["blocking_reasons"] = [reason]
        result["would_write"] = False
        result["wrote"] = False
        return result

    metadata, body, parse_errors = read_draft_markdown(source_path)
    source_markdown = source_path.read_text(encoding="utf-8")
    is_external_intake = _is_external_intake_draft(source_path, repo_root, metadata)
    publish_metadata = _external_intake_execution_metadata(metadata) if is_external_intake else metadata
    rendered_markdown = render_markdown_task_card(publish_metadata, body) if is_external_intake else source_markdown
    validation = validate_draft_metadata(repo_root, metadata, actual_path=source_path, parse_errors=parse_errors)
    publish_complexity = validate_task_complexity(publish_metadata, enforce_dependency_gate=True)
    for reason in publish_complexity["blocking_reasons"]:
        if reason not in validation["blocking_reasons"]:
            validation["blocking_reasons"].append(reason)
    for warning in publish_complexity["warnings"]:
        if warning not in validation["warnings"]:
            validation["warnings"].append(warning)
        if warning not in validation.setdefault("classification_warnings", []):
            validation["classification_warnings"].append(warning)
    result["task_id"] = validation["task_id"]
    result["warnings"] = list(validation["warnings"])

    task_id = validation["task_id"]
    if isinstance(task_id, str) and task_id:
        target_path = expected_pending_relative_path(task_id)
        target_file = resolve_pending_target_path(repo_root, task_id)
        result["target_path"] = target_path
        result["planned_writes"] = [
            {
                "path": target_path,
                "kind": "create",
                "type": "pending_markdown",
            }
        ]

        pending_root = repo_root / PENDING_QUEUE_DIR
        case_collision = find_case_insensitive_path_collision(pending_root, target_file.name)
        if case_collision is not None:
            collision_rel = str(case_collision.resolve().relative_to(repo_root.resolve()))
            if case_collision.resolve() != target_file.resolve():
                validation["blocking_reasons"].append(
                    f"Case-insensitive pending filename collision: {collision_rel}"
                )
            elif target_file.exists():
                validation["blocking_reasons"].append(f"Pending target already exists: {target_path}")

    classification_warnings = list(validation.get("classification_warnings", []))
    verdict_warnings = [warning for warning in validation["warnings"] if warning not in classification_warnings]
    result["verdict"] = "BLOCK" if validation["blocking_reasons"] else ("WARN" if verdict_warnings else "PASS")
    result["blocking_reasons"] = list(validation["blocking_reasons"])
    result["classification_warnings"] = classification_warnings
    result["would_write"] = result["verdict"] != "BLOCK" and bool(result["target_path"])
    result["validation"] = {
        "action": "draft_validate",
        "path": str(source_path.relative_to(repo_root)),
        "task_id": validation["task_id"],
        "verdict": result["verdict"],
        "blocking_reasons": list(validation["blocking_reasons"]),
        "warnings": list(validation["warnings"]),
        "frontmatter": metadata,
        "published_frontmatter": publish_metadata,
    }
    if dry_run:
        result["wrote"] = False
        result["rendered_markdown"] = rendered_markdown
        return result

    if result["verdict"] == "BLOCK" or not result["target_path"]:
        result["wrote"] = False
        return result

    pending_root = repo_root / PENDING_QUEUE_DIR
    pending_root.mkdir(parents=True, exist_ok=True)
    target_file = repo_root / result["target_path"]
    target_file.write_text(rendered_markdown, encoding="utf-8")
    result["wrote"] = True
    return result
