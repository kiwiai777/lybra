"""
AIPOS-253: Audit task derivation on return_confirm.

Gate mechanically derives audit tasks after successful return, eliminating
executor self-authoring of audit cards. Zero LLM, zero new dependencies.
"""

from __future__ import annotations

import hashlib
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_writer import render_publish_record, stable_publish_id
from tools.aipos_cli.queue_mutation import render_task_markdown
from tools.aipos_cli.records import expected_publish_record_path
from tools.aipos_cli.task_loader import find_task_by_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _task_filename_for(task_id: str) -> str:
    """Generate normalized filename for task_id (matches board_adapter convention)."""
    value = "".join(char.lower() if char.isalnum() else "-" for char in task_id).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return (value or "task") + ".md"


def _derive_audit_instance(project: str) -> str:
    """
    Derive audit agent_instance per convention: audit.<project>.<hostname>
    
    Example: audit.lybra.kiwiai-dev
    """
    hostname = socket.gethostname().split(".")[0]  # short hostname
    return f"audit.{project}.{hostname}"


def _derive_audit_assigned_to(project: str) -> str:
    """Derive assigned_to short name: audit_<project>"""
    return f"audit_{project}"


def should_derive_audit(source_metadata: dict[str, Any]) -> bool:
    """
    Check if audit task should be derived.
    
    Returns False if:
    - audit: none in frontmatter
    - already has related_audit_task_ref or audit_dispatch_record_ref (idempotency)
    """
    # Explicit opt-out
    if str(source_metadata.get("audit", "")).strip().lower() == "none":
        return False
    
    # Already dispatched (idempotency)
    if source_metadata.get("related_audit_task_ref") or source_metadata.get("audit_dispatch_record_ref"):
        return False
    
    return True


def derive_audit_task_id(source_task_id: str) -> str:
    """Generate audit task ID: <SOURCE_ID>R"""
    return f"{source_task_id}R"


def build_derived_audit_task(
    *,
    source_task_id: str,
    source_metadata: dict[str, Any],
    source_path: str,
    return_record_ref: str,
    artifact_refs: list[str],
) -> dict[str, Any]:
    """
    Build derived audit task frontmatter and body.
    
    Returns dict with keys: metadata, body, audit_task_id, audit_task_path
    """
    audit_task_id = derive_audit_task_id(source_task_id)
    project = str(source_metadata.get("project") or "lybra")
    
    audit_metadata = {
        "task_id": audit_task_id,
        "title": f"Audit {source_metadata.get('title', source_task_id)}",
        "project": project,
        "assigned_to": _derive_audit_assigned_to(project),
        "agent_instance": _derive_audit_instance(project),
        "context_bundle": source_metadata.get("context_bundle", "default"),
        "task_mode": "audit",
        "task_class": "complex",
        "priority": source_metadata.get("priority", "medium"),
        "status": "pending",
        "created_by": "gate_derivation",
        "needs_owner": False,
        "derived_from": source_task_id,
        "reviewed_task_id": source_task_id,
        "reviewed_task_path": source_path,
        "reviewed_return_record_ref": return_record_ref,
    }
    
    # Copy relevant fields if present
    for key in ["output_target", "artifact_policy", "session_policy", "context_isolation"]:
        if key in source_metadata:
            audit_metadata[key] = source_metadata[key]
    
    # Build body (mechanical signpost)
    artifact_list = "\n".join(f"- `{ref}`" for ref in artifact_refs) if artifact_refs else "- (see return record)"
    
    audit_body = f"""## Audit Subject
Independent audit of task `{source_task_id}`.

**Audit criterion**: the original task card (source of truth).

## References
- Original task: `{source_path}`
- Return record: `{return_record_ref}`

## Delivery Artifacts
{artifact_list}

## Audit Instructions
Review the returned work evidence against the original task card requirements and produce an independent verdict.
"""
    
    audit_task_path = f"5_tasks/queue/pending/{_task_filename_for(audit_task_id)}"
    
    return {
        "metadata": audit_metadata,
        "body": audit_body,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
    }


def derive_audit_task_on_return(
    *,
    repo_root: Path,
    source_task_id: str,
    source_metadata: dict[str, Any],
    source_path: str,
    return_record_ref: str,
    artifact_refs: list[str],
) -> dict[str, Any]:
    """
    Derive audit task after successful return_confirm.
    
    Returns dict with:
    - derived: bool (whether derivation occurred)
    - reason: str (skip reason if not derived)
    - audit_task_id: str (if derived)
    - audit_task_path: str (if derived)
    - performed_writes: list[dict] (if derived)
    """
    # Check if should derive
    if not should_derive_audit(source_metadata):
        audit_opt = str(source_metadata.get("audit", "")).strip().lower()
        if audit_opt == "none":
            return {"derived": False, "reason": "audit: none in source task frontmatter"}
        return {"derived": False, "reason": "audit already dispatched (idempotency)"}
    
    # Build audit task
    audit_spec = build_derived_audit_task(
        source_task_id=source_task_id,
        source_metadata=source_metadata,
        source_path=source_path,
        return_record_ref=return_record_ref,
        artifact_refs=artifact_refs,
    )
    
    audit_task_id = audit_spec["audit_task_id"]
    audit_task_path = audit_spec["audit_task_path"]
    audit_task_file = repo_root / audit_task_path
    
    # Idempotency: check if audit task already exists
    existing_task, matches = find_task_by_id(audit_task_id, repo_root)
    if existing_task or matches:
        return {
            "derived": False,
            "reason": f"audit task {audit_task_id} already exists (idempotency)",
        }
    
    # Write audit task
    audit_markdown = render_task_markdown(audit_spec["metadata"], audit_spec["body"])
    audit_task_file.parent.mkdir(parents=True, exist_ok=True)
    audit_task_file.write_text(audit_markdown, encoding="utf-8")
    
    # Write publish record for authority_scanner VALID
    publish_id = stable_publish_id(audit_task_id)
    published_at = _utc_now()
    
    # Calculate checksums
    source_sha256 = hashlib.sha256(b"").hexdigest()  # No source draft for mechanical derivation
    published_sha256 = hashlib.sha256(audit_markdown.encode("utf-8")).hexdigest()
    
    publish_record_markdown = render_publish_record(
        task_id=audit_task_id,
        publish_id=publish_id,
        actor="gate_derivation",
        source_draft_ref="(mechanical derivation from return)",
        published_task_ref=audit_task_path,
        source_sha256=source_sha256,
        published_sha256=published_sha256,
        published_at=published_at,
        confirmer=None,  # No confirmer for mechanical derivation
    )
    
    publish_record_path = expected_publish_record_path(repo_root, audit_task_id, publish_id)
    publish_record_path.parent.mkdir(parents=True, exist_ok=True)
    publish_record_path.write_text(publish_record_markdown, encoding="utf-8")
    
    return {
        "derived": True,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "publish_record_path": str(publish_record_path.relative_to(repo_root)),
        "performed_writes": [
            {
                "path": audit_task_path,
                "kind": "create",
                "type": "derived_audit_task",
            },
            {
                "path": str(publish_record_path.relative_to(repo_root)),
                "kind": "create",
                "type": "publish_record",
                "record_type": "publish_record",
            },
        ],
    }
