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


def should_derive_audit(source_metadata: dict[str, Any], *, branch_id: str | None = None) -> bool:
    """
    Check if audit task should be derived.
    
    Returns False if:
    - audit: none in frontmatter
    - task_mode is audit (AIPOS-256 F-253-3: prevent infinite R chain)
    - already has related_audit_task_ref or audit_dispatch_record_ref (idempotency)
    - AIPOS-338 S6②: non-code branch does NOT derive an independent R card
      (it walks the bench path described in the card's own contract section)
    """
    # Explicit opt-out
    if str(source_metadata.get("audit", "")).strip().lower() == "none":
        return False
    
    # AIPOS-256 F-253-3: Prevent infinite audit chain (audit tasks do not derive audits)
    if str(source_metadata.get("task_mode", "")).strip().lower() == "audit":
        return False
    
    # Already dispatched (idempotency)
    if source_metadata.get("related_audit_task_ref") or source_metadata.get("audit_dispatch_record_ref"):
        return False
    
    # AIPOS-338 S6②: non-code branch → bench audit path, no independent R card
    if branch_id == "noncode_bench_audit":
        return False
    
    return True


def derive_audit_task_id(source_task_id: str) -> str:
    """Generate audit task ID: <SOURCE_ID>R"""
    return f"{source_task_id}R"


def _resolve_profile(
    source_metadata: dict[str, Any], collaboration_profile: dict[str, Any] | None, repo_root: Path | None
) -> dict[str, Any]:
    """AIPOS-338 S6: resolve the collaboration profile for branch determination."""
    from tools.aipos_cli.flow_description import resolve_collaboration_profile
    if collaboration_profile is not None:
        return collaboration_profile
    if repo_root is not None:
        project_json = repo_root / "project.json"
        if not project_json.is_file():
            project_json = repo_root / "2_projects" / "lybra" / "project.json"
        return resolve_collaboration_profile(project_json)
    return {"code_enabled": True, "deploy_gate_enabled": False, "default_audit_mode": "agent"}


def _resolve_branch_id(
    source_metadata: dict[str, Any], collaboration_profile: dict[str, Any] | None, repo_root: Path | None
) -> str:
    """AIPOS-338 S6: resolve the gate-chain branch from the single source (flow_description)."""
    from tools.aipos_cli.flow_description import resolve_gate_chain
    profile = _resolve_profile(source_metadata, collaboration_profile, repo_root)
    chain = resolve_gate_chain(profile, source_metadata)
    return getattr(chain, "branch_id", "")


def build_derived_audit_task(
    *,
    source_task_id: str,
    source_metadata: dict[str, Any],
    source_path: str,
    return_record_ref: str,
    artifact_refs: list[str],
    collaboration_profile: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """
    Build derived audit task frontmatter and body.
    
    AIPOS-338 S2: the audit body now carries the fixed audit instructions
    (criterion = original card full text, independent evidence, two bottom-line
    assertions per AIPOS-314, report location, honest-reporting red line) and,
    when repo_root is provided, the auditor's card-bound contract section.
    
    Returns dict with keys: metadata, body, audit_task_id, audit_task_path
    """
    audit_task_id = derive_audit_task_id(source_task_id)
    project = str(source_metadata.get("project") or "lybra")
    branch_id = _resolve_branch_id(source_metadata, collaboration_profile, repo_root)
    
    audit_metadata = {
        "task_id": audit_task_id,
        "title": f"Audit {source_metadata.get('title', source_task_id)}",
        "project": project,
        "assigned_to": _derive_audit_assigned_to(project),
        "agent_instance": _derive_audit_instance(project),
        "context_bundle": source_metadata.get("context_bundle", "default"),
        "task_mode": "audit",
        "task_class": "simple",
        "priority": source_metadata.get("priority", "medium"),
        "status": "pending",
        "created_by": "gate_derivation",
        "needs_owner": False,
        "audit": "none",
        "derived_from": source_task_id,
        "reviewed_task_id": source_task_id,
        "reviewed_task_path": source_path,
        "reviewed_return_record_ref": return_record_ref,
    }
    
    # Copy relevant fields if present
    for key in ["output_target", "artifact_policy", "session_policy", "context_isolation"]:
        if key in source_metadata:
            audit_metadata[key] = source_metadata[key]
    
    # Build body (mechanical signpost) — AIPOS-338 S2: fixed audit instructions
    artifact_list = "\n".join(f"- `{ref}`" for ref in artifact_refs) if artifact_refs else "- (see return record)"
    
    audit_body = f"""## Audit Subject
Independent audit of task `{source_task_id}`.

## References
- Original task: `{source_path}`
- Return record: `{return_record_ref}`

## Delivery Artifacts
{artifact_list}

## Audit Instructions (准绳与取证)
- **准绳 = 原执行卡全文**(`{source_path}`):验收断言与红线以原卡为准,执行体自述只作线索,不作准绳。
- **独立取证**:不采纳执行体自报结论;逐条核验原卡验收断言,附可复核证据(命令 + 输出摘录)。
- **两条底线断言(AIPOS-314,必判)**:
  1. **起得来**:产物能拉起/运行(代码能 import 或起服务;命令能跑通)。
  2. **产物可用**:产物满足原卡验收断言(不是"看起来对",是"断言过")。
  两条任一不过 → FAIL。
- **报告落位**:`<workspace>/5_tasks/records/audit_verdicts/{source_task_id}/verdict_*.md`(裁决归被审卡 ID 目录)。
- **如实报红线**:结论三值 PASS / PASS_WITH_NOTES / FAIL(附 F-* 清单);失败如实报,禁止"应该没问题"。
"""
    if branch_id == "code_with_deploy":
        audit_body += (
            "\n## 部署门提醒(AIPOS-338 S6)\n"
            "本被审卡 `deploy: true`。审计 PASS ≠ 可部署 —— 部署确认属 Owner"
            "(`owner_verify: required` 的不可逆确认,判断在 Owner)。仅生产级部署触发,开发环回部署不触发。\n"
        )
    
    # AIPOS-338 S2: append the auditor's card-bound contract section (single-source)
    # AIPOS-340F2: ValueError (envelope resolution failure) must propagate; other errors swallowed.
    if repo_root is not None:
        try:
            from tools.aipos_cli.gate_contract_section import (
                render_gate_contract_section, workspace_connection_info,
            )
            conn = workspace_connection_info(repo_root)
            section = render_gate_contract_section(
                _resolve_profile(source_metadata, collaboration_profile, repo_root),
                source_metadata, role="auditor",
                gate_url=conn["gate_url"], connection_json_rel=conn["connection_json_rel"],
                workspace_display=conn["workspace_display"], task_id=audit_task_id,
                workspace_root=repo_root,
            )
            audit_body = audit_body.rstrip() + "\n\n" + section + "\n"
        except Exception:
            # AIPOS-340F2: render_gate_contract_section no longer has hardcoded fallbacks.
            # If envelope resolution fails, the section is omitted. Production workspaces
            # always have active policies; this only triggers in broken/test environments.
            pass
    
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
    collaboration_profile: dict[str, Any] | None = None,
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
    # AIPOS-338 S6: resolve the branch; non-code branches do not derive an R card
    branch_id = _resolve_branch_id(source_metadata, collaboration_profile, repo_root)
    # Check if should derive
    if not should_derive_audit(source_metadata, branch_id=branch_id):
        audit_opt = str(source_metadata.get("audit", "")).strip().lower()
        if audit_opt == "none":
            return {"derived": False, "reason": "audit: none in source task frontmatter"}
        if branch_id == "noncode_bench_audit":
            return {"derived": False, "reason": "non-code branch uses bench audit path (no independent R card)"}
        return {"derived": False, "reason": "audit already dispatched (idempotency)"}
    
    # Build audit task
    audit_spec = build_derived_audit_task(
        source_task_id=source_task_id,
        source_metadata=source_metadata,
        source_path=source_path,
        return_record_ref=return_record_ref,
        artifact_refs=artifact_refs,
        collaboration_profile=collaboration_profile,
        repo_root=repo_root,
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
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
