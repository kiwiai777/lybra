from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.records import check_task_record_refs

AUTHORITY_VERDICTS = {
    "VALID",
    "GRANDFATHERED",
    "PRE_AUTHORITY_WARN",
    "ORPHAN_INVALID",
    "QUARANTINED",
    "DANGLING",
    "CONTRADICTORY",
}


def _task_id(task: dict[str, Any]) -> str | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    value = task.get("task_id") or metadata.get("task_id")
    text = str(value or "").strip()
    return text or None


def _finding(
    *,
    verdict: str,
    severity: str,
    subject_type: str,
    subject_ref: str | None,
    reason_code: str,
    reason: str,
    effective_truth: bool,
    task_id: str | None = None,
    source_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "authority_verdict": verdict,
        "severity": severity,
        "subject_type": subject_type,
        "subject_ref": subject_ref,
        "task_id": task_id,
        "reason_code": reason_code,
        "reason": reason,
        "effective_truth": effective_truth,
        "source_refs": source_refs or ([subject_ref] if subject_ref else []),
    }


def _ok_record_ref(task: dict[str, Any], records: dict[str, Any], reference: str) -> bool:
    report = check_task_record_refs(task, records)
    return any(check.get("reference") == reference and check.get("status") == "ok" for check in report.get("checks", []))


def _records_for_task(records: dict[str, Any], record_group: str, task_id: str) -> list[dict[str, Any]]:
    return list(records.get(record_group, {}).get(task_id, []))


def _publish_record_matches_task(record: dict[str, Any], task: dict[str, Any]) -> bool:
    task_id = _task_id(task)
    if not task_id or record.get("task_id") != task_id:
        return False
    published_ref = str(record.get("published_task_ref") or record.get("metadata", {}).get("published_task_ref") or "")
    return not published_ref or published_ref == str(task.get("path") or "")


def classify_task_authority(task: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    task_id = _task_id(task)
    path = str(task.get("path") or "")
    queue_state = str(task.get("queue_state") or "")
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    findings: list[dict[str, Any]] = []

    if not task_id:
        findings.append(
            _finding(
                verdict="QUARANTINED",
                severity="needs_owner",
                subject_type="queue_task",
                subject_ref=path,
                task_id=None,
                reason_code="AUTHORITY_TASK_ID_MISSING",
                reason="Queue task has no explicit task_id, so authority cannot be established.",
                effective_truth=False,
            )
        )
    elif queue_state == "pending":
        publishes = [
            item
            for item in _records_for_task(records, "task_publishes", task_id)
            if _publish_record_matches_task(item, task)
        ]
        if len(publishes) == 1:
            findings.append(
                _finding(
                    verdict="VALID",
                    severity="info",
                    subject_type="queue_task",
                    subject_ref=path,
                    task_id=task_id,
                    reason_code="PUBLISH_PROVENANCE_PRESENT",
                    reason="Pending task has matching draft_publish provenance.",
                    effective_truth=True,
                    source_refs=[path, str(publishes[0].get("path") or "")],
                )
            )
        elif len(publishes) > 1:
            findings.append(
                _finding(
                    verdict="CONTRADICTORY",
                    severity="needs_owner",
                    subject_type="queue_task",
                    subject_ref=path,
                    task_id=task_id,
                    reason_code="DUPLICATE_PUBLISH_PROVENANCE",
                    reason="Pending task has multiple matching publish records.",
                    effective_truth=False,
                    source_refs=[path, *[str(item.get("path") or "") for item in publishes]],
                )
            )
        else:
            findings.append(
                _finding(
                    verdict="QUARANTINED",
                    severity="needs_owner",
                    subject_type="queue_task",
                    subject_ref=path,
                    task_id=task_id,
                    reason_code="PENDING_PUBLISH_PROVENANCE_MISSING",
                    reason=(
                        "Pending task has no matching draft_publish provenance. "
                        "Legacy workspaces require a deferred adoption manifest before treating this as grandfathered."
                    ),
                    effective_truth=False,
                )
            )
    elif queue_state == "claimed":
        returned = metadata.get("executor_status") == "completed" or metadata.get("audit_readiness") == "ready"
        if returned:
            if metadata.get("return_record_ref") and _ok_record_ref(task, records, "return_record_ref"):
                findings.append(
                    _finding(
                        verdict="VALID",
                        severity="info",
                        subject_type="queue_task",
                        subject_ref=path,
                        task_id=task_id,
                        reason_code="RETURN_PROVENANCE_PRESENT",
                        reason="Returned claimed task has matching return provenance.",
                        effective_truth=True,
                    )
                )
            else:
                findings.append(
                    _finding(
                        verdict="ORPHAN_INVALID",
                        severity="block",
                        subject_type="queue_task",
                        subject_ref=path,
                        task_id=task_id,
                        reason_code="RETURN_PROVENANCE_MISSING",
                        reason="Claimed task reports executor completion or audit readiness without matching return provenance.",
                        effective_truth=False,
                    )
                )
        elif metadata.get("claim_id") and metadata.get("active_session_id") and _ok_record_ref(task, records, "claim_id") and _ok_record_ref(task, records, "active_session_id"):
            findings.append(
                _finding(
                    verdict="VALID",
                    severity="info",
                    subject_type="queue_task",
                    subject_ref=path,
                    task_id=task_id,
                    reason_code="CLAIM_PROVENANCE_PRESENT",
                    reason="Claimed task has matching claim and session provenance.",
                    effective_truth=True,
                )
            )
        else:
            findings.append(
                _finding(
                    verdict="ORPHAN_INVALID",
                    severity="block",
                    subject_type="queue_task",
                    subject_ref=path,
                    task_id=task_id,
                    reason_code="CLAIM_PROVENANCE_MISSING",
                    reason="Claimed task has no matching claim/session provenance.",
                    effective_truth=False,
                )
            )
    elif queue_state in {"completed", "blocked"}:
        findings.append(
            _finding(
                verdict="PRE_AUTHORITY_WARN",
                severity="info",
                subject_type="queue_task",
                subject_ref=path,
                task_id=task_id,
                reason_code="NO_PROVENANCE_CLASS_V0",
                reason=f"{queue_state} tasks do not yet have a dedicated provenance writer in AIPOS-194 v0.",
                effective_truth=True,
            )
        )
    else:
        findings.append(
            _finding(
                verdict="QUARANTINED",
                severity="needs_owner",
                subject_type="queue_task",
                subject_ref=path,
                task_id=task_id,
                reason_code="UNKNOWN_QUEUE_STATE",
                reason="Queue task is outside the known queue-state authority mapping.",
                effective_truth=False,
            )
        )

    effective_truth = all(item.get("effective_truth") for item in findings)
    severity_rank = {"info": 0, "warn": 1, "needs_owner": 2, "block": 3}
    primary = max(findings, key=lambda item: severity_rank.get(str(item.get("severity")), 0))
    return {
        "subject_ref": path,
        "task_id": task_id,
        "queue_state": queue_state,
        "authority_verdict": primary.get("authority_verdict"),
        "effective_truth": effective_truth,
        "authority_findings": findings,
    }


def _record_subject_known(record: dict[str, Any], task_ids: set[str]) -> bool:
    record_type = record.get("record_type")
    task_id = str(record.get("task_id") or "")
    if record_type in {"session", "publish", "claim", "return"}:
        return task_id in task_ids
    if record_type in {"audit_dispatch", "audit_verdict"}:
        reviewed = str(record.get("reviewed_task_id") or task_id or "")
        audit_task = str(record.get("audit_task_id") or "")
        return reviewed in task_ids and (not audit_task or audit_task in task_ids)
    if record_type == "owner_decision_record":
        related_task_id = str(record.get("task_id") or "")
        return not related_task_id or related_task_id in task_ids
    return True


def _record_findings(records: dict[str, Any], task_ids: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for group in (
        "publishes",
        "sessions",
        "claims",
        "returns",
        "audit_dispatches",
        "audit_verdicts",
        "owner_decisions",
    ):
        for record in records.get(group, []):
            if _record_subject_known(record, task_ids):
                continue
            findings.append(
                _finding(
                    verdict="QUARANTINED",
                    severity="needs_owner",
                    subject_type=f"{record.get('record_type')}_record",
                    subject_ref=str(record.get("path") or ""),
                    task_id=str(record.get("task_id") or "") or None,
                    reason_code="ORPHAN_RECORD_WITHOUT_TASK",
                    reason="Record points to no known task in the current queue scan.",
                    effective_truth=False,
                )
            )
    return findings


def _draft_findings(repo_root: Path | None) -> list[dict[str, Any]]:
    if repo_root is None:
        return []
    drafts_root = repo_root / "5_tasks" / "drafts"
    if not drafts_root.exists():
        return []
    findings: list[dict[str, Any]] = []
    for path in sorted(drafts_root.rglob("*.md")):
        try:
            metadata, _body, _warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}
        rel = str(path.relative_to(repo_root))
        findings.append(
            _finding(
                verdict="PRE_AUTHORITY_WARN",
                severity="info",
                subject_type="draft",
                subject_ref=rel,
                task_id=str(metadata.get("task_id") or "") or None,
                reason_code="DRAFT_PRE_AUTHORITY",
                reason="Drafts are pre-authority inputs until a publish gate creates queue truth.",
                effective_truth=True,
            )
        )
    return findings


def build_authority_report(
    *,
    tasks: list[dict[str, Any]],
    records: dict[str, Any],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    task_authority = [classify_task_authority(task, records) for task in tasks]
    task_ids = {str(item.get("task_id")) for item in task_authority if item.get("task_id")}
    task_findings = [finding for item in task_authority for finding in item.get("authority_findings", [])]
    record_findings = _record_findings(records, task_ids)
    draft_findings = _draft_findings(repo_root)
    findings = [*task_findings, *record_findings, *draft_findings]
    counts = Counter(str(item.get("authority_verdict")) for item in findings)
    effective_tasks = [item for item in task_authority if item.get("effective_truth")]
    return {
        "scope": "authority",
        "authority_summary": {
            "valid": counts.get("VALID", 0),
            "grandfathered": counts.get("GRANDFATHERED", 0),
            "pre_authority_warn": counts.get("PRE_AUTHORITY_WARN", 0),
            "orphan_invalid": counts.get("ORPHAN_INVALID", 0),
            "quarantined": counts.get("QUARANTINED", 0),
            "dangling": counts.get("DANGLING", 0),
            "contradictory": counts.get("CONTRADICTORY", 0),
            "findings_total": len(findings),
        },
        "effective_queue_summary": {
            "total_tasks": len(task_authority),
            "effective_tasks": len(effective_tasks),
            "excluded_authority_invalid": len(task_authority) - len(effective_tasks),
        },
        "task_authority": task_authority,
        "authority_findings": findings,
    }
