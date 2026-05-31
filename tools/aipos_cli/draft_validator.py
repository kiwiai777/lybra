from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.task_loader import QUEUE_STATES
from tools.aipos_cli.task_complexity import complexity_payload, validate_task_complexity

DRAFTS_DIR = Path("5_tasks/drafts")
QUEUE_DIR = Path("5_tasks/queue")
PENDING_QUEUE_DIR = QUEUE_DIR / "pending"

DRAFT_REQUIRED_FIELDS = [
    "task_id",
    "title",
    "project",
    "assigned_to",
    "context_bundle",
    "task_mode",
    "priority",
    "status",
    "created_by",
    "needs_owner",
    "output_target",
    "artifact_policy",
]

RECOMMENDED_FIELDS = [
    "agent_instance",
    "model_tier",
    "task_type",
    "polling_mode",
    "claim_policy",
    "report_mode",
    "recurrence",
]

FORBIDDEN_RUNTIME_FIELDS = [
    "claim_id",
    "claimed_by",
    "claimed_at",
    "active_session_id",
    "last_session_id",
    "completed_by",
    "completed_at",
    "blocked_by",
    "blocked_at",
]

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_missing(value: Any) -> bool:
    return value in (None, "")


def _add(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _path_parts_lower(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.parts)


def draft_slug(task_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise ValueError("task_id cannot be converted to a safe draft filename")
    return slug


def expected_draft_relative_path(task_id: str) -> str:
    return str(DRAFTS_DIR / f"{draft_slug(task_id)}.md")


def expected_pending_relative_path(task_id: str) -> str:
    return str(PENDING_QUEUE_DIR / f"{draft_slug(task_id)}.md")


def _is_safe_task_id(task_id: Any) -> bool:
    if not isinstance(task_id, str) or not task_id:
        return False
    if task_id in {".", ".."}:
        return False
    if "/" in task_id or "\\" in task_id:
        return False
    if ".." in task_id:
        return False
    return bool(TASK_ID_PATTERN.fullmatch(task_id))


def _resolved_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def resolve_draft_path(repo_root: Path, provided_path: str | Path) -> Path:
    raw_path = Path(provided_path)
    path = raw_path.resolve() if raw_path.is_absolute() else (repo_root / raw_path).resolve()
    drafts_root = (repo_root / DRAFTS_DIR).resolve()
    if not _resolved_within(drafts_root, path):
        raise ValueError(f"Draft path is outside 5_tasks/drafts: {provided_path}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Draft path is not a markdown file: {provided_path}")
    return path


def resolve_pending_target_path(repo_root: Path, task_id: str) -> Path:
    pending_root = (repo_root / PENDING_QUEUE_DIR).resolve()
    target_path = (repo_root / expected_pending_relative_path(task_id)).resolve()
    if not _resolved_within(pending_root, target_path):
        raise ValueError(f"Pending target resolves outside 5_tasks/queue/pending/: {task_id}")
    if target_path.suffix.lower() != ".md":
        raise ValueError(f"Pending target is not a markdown file: {target_path}")
    return target_path


def read_draft_markdown(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body, warnings = parse_markdown_frontmatter(text)
    return metadata, body, warnings


def iter_draft_paths(repo_root: Path) -> list[Path]:
    drafts_root = repo_root / DRAFTS_DIR
    if not drafts_root.exists():
        return []
    return sorted(path for path in drafts_root.rglob("*.md") if path.is_file())


def _iter_collision_candidate_paths(repo_root: Path) -> list[Path]:
    paths = iter_draft_paths(repo_root)
    queue_root = repo_root / QUEUE_DIR
    for queue_state in QUEUE_STATES:
        state_dir = queue_root / queue_state
        if not state_dir.exists():
            continue
        paths.extend(sorted(path for path in state_dir.iterdir() if path.is_file() and path.suffix.lower() == ".md"))
    return paths


def find_task_id_collisions(repo_root: Path, task_id: str, ignore_path: Path | None = None) -> list[str]:
    normalized = task_id.lower()
    resolved_ignore = ignore_path.resolve() if ignore_path is not None and ignore_path.exists() else None
    matches: list[str] = []
    for path in _iter_collision_candidate_paths(repo_root):
        resolved_path = path.resolve()
        if resolved_ignore is not None and resolved_path == resolved_ignore:
            continue
        try:
            metadata, _body, _warnings = read_draft_markdown(path)
        except Exception:
            continue
        existing_task_id = metadata.get("task_id")
        if isinstance(existing_task_id, str) and existing_task_id.lower() == normalized:
            matches.append(str(path.relative_to(repo_root)))
    return sorted(matches)


def validate_draft_metadata(
    repo_root: Path,
    metadata: dict[str, Any],
    *,
    actual_path: Path | None = None,
    parse_errors: list[str] | None = None,
) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    task_id = metadata.get("task_id")

    for error in parse_errors or []:
        _add(blocking_reasons, f"Frontmatter parse issue: {error}")

    for field in DRAFT_REQUIRED_FIELDS:
        if _is_missing(metadata.get(field)):
            _add(blocking_reasons, f"Missing required field: {field}")

    if not _is_missing(task_id) and not _is_safe_task_id(task_id):
        _add(blocking_reasons, "Invalid task_id format or path-unsafe task_id")

    if metadata.get("status") not in (None, "pending"):
        _add(blocking_reasons, "Draft status must be pending")

    for field in FORBIDDEN_RUNTIME_FIELDS:
        if not _is_missing(metadata.get(field)):
            _add(blocking_reasons, f"Draft contains forbidden runtime-state field: {field}")

    if _is_missing(metadata.get("body")):
        pass

    target_path: str | None = None
    if isinstance(task_id, str) and task_id and _is_safe_task_id(task_id):
        expected_rel = expected_draft_relative_path(task_id)
        target_path = expected_rel
        if actual_path is not None:
            actual_rel = str(actual_path.resolve().relative_to(repo_root.resolve()))
            actual_parts = _path_parts_lower(Path(actual_rel))
            external_intake_parts = _path_parts_lower(DRAFTS_DIR / "external_intake")
            is_external_intake_draft = actual_parts[: len(external_intake_parts)] == external_intake_parts
            if not is_external_intake_draft and actual_parts != _path_parts_lower(Path(expected_rel)):
                _add(blocking_reasons, f"Draft path does not match task_id slug: expected {expected_rel}")
        collisions = find_task_id_collisions(repo_root, task_id, ignore_path=actual_path)
        if collisions:
            _add(blocking_reasons, f"Duplicate task_id already exists: {task_id}")
            for collision in collisions:
                _add(warnings, f"task_id collision path: {collision}")

    for field in RECOMMENDED_FIELDS:
        if _is_missing(metadata.get(field)):
            _add(warnings, f"Missing recommended field: {field}")

    complexity = validate_task_complexity(metadata, enforce_dependency_gate=False)
    for message in complexity["blocking_reasons"]:
        _add(blocking_reasons, message)
    classification_warnings = list(complexity["warnings"])

    verdict = "BLOCK" if blocking_reasons else ("WARN" if warnings else "PASS")
    return {
        "task_id": task_id,
        "verdict": verdict,
        "blocking_reasons": blocking_reasons,
        "warnings": [*warnings, *classification_warnings],
        "classification_warnings": classification_warnings,
        "frontmatter": metadata,
        "target_path": target_path,
    }


def validate_draft_file(repo_root: Path, provided_path: str | Path) -> dict[str, Any]:
    drafts_root = repo_root / DRAFTS_DIR
    path = resolve_draft_path(repo_root, provided_path)
    if not path.exists():
        return {
            "action": "draft_validate",
            "path": str(Path(provided_path)),
            "task_id": None,
            "verdict": "BLOCK",
            "blocking_reasons": [f"Draft path does not exist: {provided_path}"],
            "warnings": [],
            "frontmatter": {},
        }

    metadata, _body, parse_errors = read_draft_markdown(path)
    result = validate_draft_metadata(repo_root, metadata, actual_path=path, parse_errors=parse_errors)
    return {
        "action": "draft_validate",
        "path": str(path.relative_to(repo_root)) if _resolved_within(repo_root, path) else str(path),
        "drafts_dir": str(drafts_root.relative_to(repo_root)),
        "task_id": result["task_id"],
        "verdict": result["verdict"],
        "blocking_reasons": result["blocking_reasons"],
        "warnings": result["warnings"],
        "classification_warnings": result["classification_warnings"],
        "frontmatter": metadata,
    }


def find_case_insensitive_path_collision(directory: Path, candidate_name: str) -> Path | None:
    if not directory.exists():
        return None
    normalized = candidate_name.lower()
    for path in directory.iterdir():
        if path.name.lower() == normalized:
            return path
    return None


def list_drafts(repo_root: Path) -> dict[str, Any]:
    drafts: list[dict[str, Any]] = []
    for path in iter_draft_paths(repo_root):
        validation = validate_draft_file(repo_root, path)
        frontmatter = validation["frontmatter"]
        drafts.append(
            {
                "task_id": frontmatter.get("task_id"),
                "title": frontmatter.get("title"),
                "status": frontmatter.get("status"),
                "assigned_to": frontmatter.get("assigned_to"),
                "project": frontmatter.get("project"),
                "priority": frontmatter.get("priority"),
                "needs_owner": frontmatter.get("needs_owner"),
                **complexity_payload(frontmatter),
                "source_tag": frontmatter.get("source_tag"),
                "client_tag": frontmatter.get("client_tag"),
                "external_ref": frontmatter.get("external_ref"),
                "draft_id": frontmatter.get("draft_id"),
                "path": str(path.relative_to(repo_root)),
                "verdict": validation["verdict"],
            }
        )

    return {
        "action": "draft_list",
        "drafts_dir": str(DRAFTS_DIR),
        "total": len(drafts),
        "drafts": drafts,
    }
