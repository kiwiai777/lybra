from __future__ import annotations

import json
import hashlib
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
from tools.aipos_cli.records import expected_publish_record_path
from tools.aipos_cli.record_writer import render_markdown as _render_markdown_single_source
from tools.aipos_cli.task_complexity import validate_task_complexity

# AIPOS-R8C: card_policy placeholder fields for draft create
try:
    from tools.card_policy_loader import get_card_policy_placeholder_fields
    CARD_POLICY_AVAILABLE = True
except ImportError:
    CARD_POLICY_AVAILABLE = False


def _check_project_map_staleness(repo_root: Path, validation: dict[str, Any]) -> None:
    """AIPOS-276: project-map staleness check (publish gate warning hook).
    
    If project-map.md exists and has an 'updated' field that is >3 days older
    than the most recent return record (收编), append a warning to validation["warnings"].
    Non-blocking; gracefully degrades if map absent, no updated field, or no returns.
    """
    map_path = repo_root / "governance" / "project-map.md"
    if not map_path.is_file():
        return  # no map = no check
    
    try:
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        text = map_path.read_text(encoding="utf-8", errors="replace")
        meta, _body, _warnings = parse_markdown_frontmatter(text)
        map_updated_str = str(meta.get("updated") or "").strip()
        if not map_updated_str:
            return  # no updated field = no check
        
        # Parse map updated timestamp
        map_updated = datetime.fromisoformat(map_updated_str.replace("Z", "+00:00"))
        if map_updated.tzinfo is None:
            map_updated = map_updated.replace(tzinfo=timezone.utc)
        
        # Find most recent return record (收编 = finalized delivery)
        returns_root = repo_root / "5_tasks" / "records" / "returns"
        if not returns_root.exists():
            return  # no returns = no check
        
        most_recent_return: datetime | None = None
        for task_dir in returns_root.iterdir():
            if not task_dir.is_dir():
                continue
            for record_file in task_dir.glob("*.md"):
                try:
                    record_text = record_file.read_text(encoding="utf-8", errors="replace")
                    record_meta, _record_body, _record_warnings = parse_markdown_frontmatter(record_text)
                    returned_at_str = str(record_meta.get("returned_at") or record_meta.get("created_at") or "").strip()
                    if not returned_at_str:
                        continue
                    returned_at = datetime.fromisoformat(returned_at_str.replace("Z", "+00:00"))
                    if returned_at.tzinfo is None:
                        returned_at = returned_at.replace(tzinfo=timezone.utc)
                    if most_recent_return is None or returned_at > most_recent_return:
                        most_recent_return = returned_at
                except Exception:
                    continue
        
        if most_recent_return is None:
            return  # no valid return records = no check
        
        # Check staleness: map_updated is >3 days before most_recent_return
        delta = most_recent_return - map_updated
        if delta.total_seconds() > 3 * 24 * 3600:
            map_date = map_updated.strftime("%Y-%m-%d")
            return_date = most_recent_return.strftime("%Y-%m-%d")
            warning = f"PROJECT_MAP_STALE (地图更新于 {map_date}, 最近收编 {return_date})"
            if warning not in validation["warnings"]:
                validation["warnings"].append(warning)
    
    except Exception:
        # Graceful degradation: staleness check is advisory, never fails publish
        pass

EXTERNAL_INTAKE_EXECUTION_ASSIGNED_TO = "agent-01"
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


def _record_frontmatter(metadata: dict[str, Any], order: list[str]) -> str:
    """AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown)."""
    # render_markdown adds body, but _record_frontmatter only needs the frontmatter block
    # We pass empty body and strip trailing content
    result = _render_markdown_single_source(metadata, "", order)
    # render_markdown returns "---\nyaml\n---\nbody\n"; we want just "---\nyaml\n---\n"
    # Extract just the frontmatter block
    parts = result.split("---\n", 2)
    if len(parts) >= 3:
        return f"---\n{parts[1]}---\n"
    return result


def render_markdown_task_card(metadata: dict[str, Any], body: str) -> str:
    """AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown).

    原实现用本地 _yaml_scalar 拼接, 不处理 **bold**/"/full-width colon 等毒字段.
    """
    return _render_markdown_single_source(metadata, body, FRONTMATTER_ORDER)


def stable_publish_id(task_id: str) -> str:
    return f"publish_{draft_slug(task_id)}"


def render_publish_record(
    *,
    task_id: str,
    publish_id: str,
    actor: str | None,
    source_draft_ref: str,
    published_task_ref: str,
    source_sha256: str,
    published_sha256: str,
    published_at: str,
    confirmer: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> str:
    # AIPOS-204 / F-c4: a gated publish records WHO approved the publish, mirroring the
    # AIPOS-199 claim/return confirmer attribution. `published_by` is the publisher
    # (who drafted/initiated); confirmer_* is the Owner who confirmed the gate. The
    # raw token is never recorded — only the non-secret role/ref/fingerprint. §9
    # signing fields are placeholders (per-op nonce/signature stays deferred).
    confirmer = confirmer if isinstance(confirmer, dict) else {}
    warnings = warnings if isinstance(warnings, list) else []
    metadata = {
        "record_type": RecordType.PUBLISH_RECORD,
        "task_id": task_id,
        "publish_id": publish_id,
        "actor": actor or "unknown",
        "published_by": actor or "unknown",
        "source_draft_ref": source_draft_ref,
        "published_task_ref": published_task_ref,
        "source_sha256": source_sha256,
        "published_sha256": published_sha256,
        "published_at": published_at,
        "created_at": published_at,
        "confirmer_role": confirmer.get("confirmer_role"),
        "confirmer_token_ref": confirmer.get("confirmer_token_ref"),
        "confirmer_token_fingerprint": confirmer.get("confirmer_token_fingerprint"),
        "gate_signature": confirmer.get("gate_signature"),
        "authority_seal": confirmer.get("authority_seal"),
        "signature_key_ref": confirmer.get("signature_key_ref"),
        "signed_payload_hash": confirmer.get("signed_payload_hash"),
        "signed_at": confirmer.get("signed_at"),
        "warnings": warnings if warnings else None,
    }
    body = "\n".join(
        [
            "## Publish Provenance",
            "",
            f"- Source draft: `{source_draft_ref}`",
            f"- Published task: `{published_task_ref}`",
            "- Authority: `draft_publish` controlled gate",
            "",
        ]
    )
    return _record_frontmatter(
        metadata,
        [
            "record_type",
            "task_id",
            "publish_id",
            "actor",
            "published_by",
            "source_draft_ref",
            "published_task_ref",
            "source_sha256",
            "published_sha256",
            "published_at",
            "created_at",
            "confirmer_role",
            "confirmer_token_ref",
            "confirmer_token_fingerprint",
            "gate_signature",
            "authority_seal",
            "signature_key_ref",
            "signed_payload_hash",
            "signed_at",
            "warnings",
        ],
    ) + body


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
    # AIPOS-R8C: pre-populate placeholder fields from project card_policy
    if CARD_POLICY_AVAILABLE:
        placeholders = get_card_policy_placeholder_fields(
            governance_root=repo_root, repo_root=None
        )
        for field_name, placeholder_value in placeholders.items():
            if field_name not in metadata or metadata[field_name] in (None, ""):
                metadata[field_name] = placeholder_value

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
        result["would_write"] = validation["verdict"] != Verdict.BLOCK and bool(target_path)
        result["rendered_markdown"] = rendered_markdown
        return result

    if validation["verdict"] == Verdict.BLOCK or not target_path:
        result["wrote"] = False
        return result

    drafts_root = repo_root / DRAFTS_DIR
    target_file = repo_root / target_path
    if target_file.exists():
        result["verdict"] = Verdict.BLOCK
        result["wrote"] = False
        result["blocking_reasons"] = [*result["blocking_reasons"], f"Draft file already exists: {target_path}"]
        return result

    drafts_root.mkdir(parents=True, exist_ok=True)
    target_file.write_text(rendered_markdown, encoding="utf-8")
    result["wrote"] = True
    return result


def _workspace_gate_url(repo_root: Path) -> str:
    """AIPOS-338 S1: delegate to the single-source connection reader."""
    from tools.aipos_cli.gate_contract_section import workspace_gate_url
    return workspace_gate_url(repo_root)


class ContractSectionError(RuntimeError):
    """AIPOS-343: raised when the contract section cannot be generated.

    The error message includes diagnostic information about which step failed
    and how to fix it, so publish fails loudly instead of producing a silent card.
    """


def _append_gate_contract_section(
    repo_root: Path, metadata: dict[str, Any], task_id: str, rendered_markdown: str
) -> str:
    """AIPOS-338 S1 / AIPOS-343: append the single-source 「认领与交回」 section.

    AIPOS-343: rendering failures are NO LONGER silently swallowed. If the section
    cannot be generated, a ContractSectionError is raised with diagnostic info
    (which step failed, what's missing, how to fix it). The caller (publish_draft)
    propagates this as a BLOCK, so the user gets a clear error instead of a mute card.

    Old cards are not backfilled — only NEW publishes get it.
    """
    if "【认领与交回】" in rendered_markdown:
        return rendered_markdown  # idempotency: never double-append

    from tools.aipos_cli.flow_description import resolve_collaboration_profile
    from tools.aipos_cli.gate_contract_section import render_gate_contract_section

    # AIPOS-343: workspace-agnostic project.json location (no lybra-specific fallback)
    project_json = repo_root / "project.json"
    profile = resolve_collaboration_profile(project_json)

    task_fields = {k: v for k, v in metadata.items() if k in (
        "task_mode", "output_target", "deploy", "audit", "owner_verify", "task_class"
    )}

    # AIPOS-343: each failure mode gets a specific diagnostic message
    gate_url = _workspace_gate_url(repo_root)

    try:
        section = render_gate_contract_section(
            profile, task_fields, role="executor",
            gate_url=gate_url,
            connection_json_rel=".lybra/connection.json",
            workspace_display=str(repo_root), task_id=task_id,
            workspace_root=repo_root,
        )
    except ValueError as exc:
        # Envelope resolution failed (no active policies, missing workspace_root, etc.)
        raise ContractSectionError(
            f"AIPOS-343: contract section generation failed for task {task_id}. "
            f"Policy envelope resolution error: {exc}\n"
            f"  workspace_root={repo_root}\n"
            f"  Fix: ensure active, non-expired policies exist under "
            f"<workspace>/5_tasks/policies/ with agent_or_role matching the executor role."
        ) from exc
    except Exception as exc:
        # Any other unexpected failure — still loud, with context
        raise ContractSectionError(
            f"AIPOS-343: contract section generation failed for task {task_id}. "
            f"Unexpected error during rendering: {type(exc).__name__}: {exc}\n"
            f"  workspace_root={repo_root}\n"
            f"  project_json_exists={project_json.is_file()}\n"
            f"  gate_url={gate_url}\n"
            f"  Fix: check workspace structure (project.json, .lybra/connection.json, policies/)."
        ) from exc

    return rendered_markdown.rstrip() + "\n\n" + section + "\n"


def publish_draft(
    repo_root: Path,
    draft_path: str | Path,
    *,
    dry_run: bool = False,
    actor: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # AIPOS-240 (F-o3-19): resolve once, locally, for symlink-safe repo-relative rendering. macOS
    # /var→/private/var etc. make `source_path` (resolved) mismatch an unresolved `repo_root`. The
    # `repo_root` parameter is left untouched (helpers below expect the caller's form).
    root = repo_root.resolve()
    source_path = resolve_draft_path(repo_root, draft_path)
    source_rel = str(source_path.resolve().relative_to(root))
    validation = {
        "action": "draft_validate",
        "path": str(Path(draft_path)),
        "task_id": None,
        "verdict": Verdict.BLOCK,
        "blocking_reasons": [],
        "warnings": [],
        "frontmatter": {},
    }
    result: dict[str, Any] = {
        "action": "draft_publish",
        "dry_run": dry_run,
        "source_path": source_rel,
        "target_path": None,
        "task_id": None,
        "verdict": Verdict.BLOCK,
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
    
    # AIPOS-276: project-map staleness check (publish gate warning hook)
    # If project-map.md exists and updated > 3 days before most recent return record, warn.
    _check_project_map_staleness(repo_root, validation)
    
    result["task_id"] = validation["task_id"]
    result["warnings"] = list(validation["warnings"])

    task_id = validation["task_id"]
    if isinstance(task_id, str) and task_id:
        target_path = expected_pending_relative_path(task_id)
        target_file = resolve_pending_target_path(repo_root, task_id)
        publish_id = stable_publish_id(task_id)
        publish_record_path = expected_publish_record_path(repo_root, task_id, publish_id)
        publish_record_rel = str(publish_record_path.relative_to(repo_root))
        result["publish_id"] = publish_id
        result["publish_record_path"] = publish_record_rel
        result["target_path"] = target_path
        result["planned_writes"] = [
            {
                "path": target_path,
                "kind": "create",
                "type": "pending_markdown",
            },
            {
                "path": publish_record_rel,
                "kind": "create",
                "type": RecordType.PUBLISH_RECORD,
                "record_type": RecordType.PUBLISH_RECORD,
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
        if publish_record_path.exists():
            validation["blocking_reasons"].append(f"Publish record already exists: {publish_record_rel}")

        # AIPOS-338 S1 / AIPOS-343: append the single-source 「认领与交回」 section.
        # AIPOS-343: failure is now loud — ContractSectionError → BLOCK with diagnostic.
        try:
            rendered_markdown = _append_gate_contract_section(
                repo_root, publish_metadata, str(task_id), rendered_markdown
            )
        except ContractSectionError as exc:
            validation["blocking_reasons"].append(str(exc))

    classification_warnings = list(validation.get("classification_warnings", []))
    verdict_warnings = [warning for warning in validation["warnings"] if warning not in classification_warnings]
    result["verdict"] = Verdict.BLOCK if validation["blocking_reasons"] else (Verdict.WARN if verdict_warnings else Verdict.PASS)
    result["blocking_reasons"] = list(validation["blocking_reasons"])
    result["classification_warnings"] = classification_warnings
    result["would_write"] = result["verdict"] != Verdict.BLOCK and bool(result["target_path"])
    result["validation"] = {
        "action": "draft_validate",
        "path": str(source_path.resolve().relative_to(root)),  # AIPOS-240: symlink-safe
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

    if result["verdict"] == Verdict.BLOCK or not result["target_path"]:
        result["wrote"] = False
        return result

    pending_root = repo_root / PENDING_QUEUE_DIR
    pending_root.mkdir(parents=True, exist_ok=True)
    target_file = repo_root / result["target_path"]
    target_file.write_text(rendered_markdown, encoding="utf-8")
    publish_id = str(result["publish_id"])
    publish_record_path = expected_publish_record_path(repo_root, str(task_id), publish_id)
    publish_record_path.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = hashlib.sha256(source_markdown.encode("utf-8")).hexdigest()
    published_sha256 = hashlib.sha256(rendered_markdown.encode("utf-8")).hexdigest()
    published_at = _utc_now()
    publish_record_path.write_text(
        render_publish_record(
            task_id=str(task_id),
            publish_id=publish_id,
            actor=actor,
            source_draft_ref=source_rel,
            published_task_ref=str(result["target_path"]),
            source_sha256=source_sha256,
            published_sha256=published_sha256,
            published_at=published_at,
            confirmer=confirmer,
            warnings=validation["warnings"],
        ),
        encoding="utf-8",
    )
    result["wrote"] = True
    result["record_writes"] = [
        {
            "path": str(publish_record_path.relative_to(repo_root)),
            "record_type": RecordType.PUBLISH_RECORD,
            "wrote": True,
        }
    ]
    return result
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
from tools.schema_constants import RecordType, Verdict
check_direct_invocation(__name__)
