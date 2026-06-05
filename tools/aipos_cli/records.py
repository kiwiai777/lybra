from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter


def expected_session_record_path(repo_root: Path, task_id: str, session_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "sessions" / task_id / f"{session_id}.md"


def expected_claim_log_path(repo_root: Path, task_id: str, claim_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "claims" / task_id / f"{claim_id}.md"


def expected_return_record_path(repo_root: Path, task_id: str, return_id: str) -> Path:
    return repo_root / "5_tasks" / "records" / "returns" / task_id / f"{return_id}.md"


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    metadata = record.get("metadata", {})
    timestamp = (
        metadata.get("created_at")
        or metadata.get("session_started_at")
        or metadata.get("claimed_at")
        or metadata.get("returned_at")
        or metadata.get("decided_at")
        or ""
    )
    return (str(timestamp), str(record.get("path") or ""))


def _build_record(
    path: Path,
    repo_root: Path,
    record_type: str,
    directory_task_id: str,
) -> dict[str, Any]:
    parse_errors: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        metadata: dict[str, Any] = {}
        body = ""
        parse_errors.append(f"Read failed: {exc}")
    else:
        metadata, body, parse_warnings = parse_markdown_frontmatter(text)
        parse_errors.extend(parse_warnings)

    id_field = {
        "session": "session_id",
        "claim": "claim_id",
        "return": "return_id",
    }[record_type]
    task_id = metadata.get("task_id") or directory_task_id
    record_id = metadata.get(id_field) or path.stem

    if metadata.get("task_id") and metadata.get("task_id") != directory_task_id:
        warnings.append(
            f"{record_type} record task_id mismatch: directory={directory_task_id} metadata={metadata.get('task_id')}"
        )
    if metadata.get(id_field) and metadata.get(id_field) != path.stem:
        warnings.append(
            f"{record_type} record filename mismatch: filename={path.stem} metadata={metadata.get(id_field)}"
        )

    record = {
        "record_type": record_type,
        "record_id": record_id,
        "task_id": task_id,
        "path": str(path.relative_to(repo_root)),
        "metadata": metadata,
        "body": body,
        "parse_errors": parse_errors,
        "warnings": warnings,
    }
    if record_type == "session":
        record.update(
            {
                "session_id": record_id,
                "session_status": metadata.get("session_status") or metadata.get("status"),
                "claim_id": metadata.get("claim_id"),
                "created_at": metadata.get("created_at") or metadata.get("session_started_at"),
            }
        )
    elif record_type == "claim":
        record.update(
            {
                "claim_id": record_id,
                "session_id": metadata.get("session_id"),
                "claimed_by": metadata.get("claimed_by") or metadata.get("actor"),
                "claimed_at": metadata.get("claimed_at") or metadata.get("created_at"),
                "claim_source": metadata.get("claim_source"),
            }
        )
    else:
        record.update(
            {
                "return_id": record_id,
                "claim_id": metadata.get("claim_id"),
                "session_id": metadata.get("session_id"),
                "returned_by": metadata.get("returned_by") or metadata.get("actor"),
                "returned_at": metadata.get("returned_at") or metadata.get("created_at"),
                "executor_status": metadata.get("executor_status"),
                "audit_readiness": metadata.get("audit_readiness"),
            }
        )
    return record


def _iter_record_files(root: Path) -> list[tuple[Path, str]]:
    if not root.exists():
        return []
    files: list[tuple[Path, str]] = []
    for task_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for path in sorted(task_dir.iterdir()):
            if path.is_file() and path.suffix == ".md":
                files.append((path, task_dir.name))
    return files


def _build_owner_decision_record(path: Path, repo_root: Path) -> dict[str, Any]:
    parse_errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        metadata: dict[str, Any] = {}
        body = ""
        parse_errors.append(f"Read failed: {exc}")
    else:
        metadata, body, parse_warnings = parse_markdown_frontmatter(text)
        parse_errors.extend(parse_warnings)

    decision_id = metadata.get("decision_id") or path.stem
    warnings: list[str] = []
    if metadata.get("decision_id") and metadata.get("decision_id") != path.stem:
        warnings.append(
            f"owner decision record filename mismatch: filename={path.stem} metadata={metadata.get('decision_id')}"
        )
    if metadata.get("record_type") not in (None, "owner_decision_record"):
        warnings.append(f"owner decision record_type mismatch: {metadata.get('record_type')}")

    return {
        "record_type": "owner_decision_record",
        "record_id": decision_id,
        "decision_id": decision_id,
        "decision_type": metadata.get("decision_type"),
        "decision_status": metadata.get("decision_status"),
        "decided_at": metadata.get("decided_at"),
        "decided_by_ref": metadata.get("decided_by_ref"),
        "captured_by": metadata.get("captured_by"),
        "capture_surface": metadata.get("capture_surface"),
        "project": metadata.get("project"),
        "task_id": metadata.get("task_id"),
        "draft_path": metadata.get("draft_path"),
        "orchestration_id": metadata.get("orchestration_id"),
        "external_ref": metadata.get("external_ref"),
        "approval_operation": metadata.get("approval_operation"),
        "allowed_next_action": metadata.get("allowed_next_action"),
        "evidence_id": metadata.get("evidence_id"),
        "evidence_hash": metadata.get("evidence_hash"),
        "source_tag": metadata.get("source_tag"),
        "client_tag": metadata.get("client_tag"),
        "path": str(path.relative_to(repo_root)),
        "metadata": metadata,
        "body": body,
        "parse_errors": parse_errors,
        "warnings": warnings,
    }


def _iter_owner_decision_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_file() and path.suffix == ".md")


def load_records(repo_root: Path) -> dict[str, Any]:
    records_root = repo_root / "5_tasks" / "records"
    sessions_root = records_root / "sessions"
    claims_root = records_root / "claims"
    returns_root = records_root / "returns"
    owner_decisions_root = records_root / "owner_decisions"
    sessions = [
        _build_record(path, repo_root, "session", directory_task_id)
        for path, directory_task_id in _iter_record_files(sessions_root)
    ]
    claims = [
        _build_record(path, repo_root, "claim", directory_task_id)
        for path, directory_task_id in _iter_record_files(claims_root)
    ]
    returns = [
        _build_record(path, repo_root, "return", directory_task_id)
        for path, directory_task_id in _iter_record_files(returns_root)
    ]
    owner_decisions = [
        _build_owner_decision_record(path, repo_root)
        for path in _iter_owner_decision_files(owner_decisions_root)
    ]

    warnings: list[str] = []
    parse_errors: list[str] = []
    session_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    claim_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    return_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    owner_decision_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_returns: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in sessions:
        if record.get("session_id"):
            session_index[str(record["session_id"])].append(record)
        if record.get("task_id"):
            task_sessions[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in claims:
        if record.get("claim_id"):
            claim_index[str(record["claim_id"])].append(record)
        if record.get("task_id"):
            task_claims[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in returns:
        if record.get("return_id"):
            return_index[str(record["return_id"])].append(record)
        if record.get("task_id"):
            task_returns[str(record["task_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])
    for record in owner_decisions:
        if record.get("decision_id"):
            owner_decision_index[str(record["decision_id"])].append(record)
        parse_errors.extend([f"{record['path']}: {item}" for item in record.get("parse_errors", [])])
        warnings.extend([f"{record['path']}: {item}" for item in record.get("warnings", [])])

    for record_id, items in session_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate session_id found: {record_id}")
    for record_id, items in claim_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate claim_id found: {record_id}")
    for record_id, items in return_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate return_id found: {record_id}")
    for record_id, items in owner_decision_index.items():
        if len(items) > 1:
            warnings.append(f"Duplicate decision_id found: {record_id}")

    for items in task_sessions.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_claims.values():
        items.sort(key=_record_sort_key, reverse=True)
    for items in task_returns.values():
        items.sort(key=_record_sort_key, reverse=True)

    summary = {
        "session_records": len(sessions),
        "claim_logs": len(claims),
        "return_records": len(returns),
        "owner_decision_records": len(owner_decisions),
        "tasks_with_session_records": len(task_sessions),
        "tasks_with_claim_logs": len(task_claims),
        "tasks_with_return_records": len(task_returns),
        "parse_errors": len(parse_errors),
    }
    return {
        "scope": "records",
        "summary": summary,
        "records_root": str(records_root.relative_to(repo_root)),
        "records_root_exists": records_root.exists(),
        "sessions_root_exists": sessions_root.exists(),
        "claims_root_exists": claims_root.exists(),
        "returns_root_exists": returns_root.exists(),
        "owner_decisions_root_exists": owner_decisions_root.exists(),
        "sessions": sorted(sessions, key=_record_sort_key, reverse=True),
        "claims": sorted(claims, key=_record_sort_key, reverse=True),
        "returns": sorted(returns, key=_record_sort_key, reverse=True),
        "owner_decisions": sorted(owner_decisions, key=_record_sort_key, reverse=True),
        "warnings": warnings,
        "parse_errors": parse_errors,
        "session_index": dict(session_index),
        "claim_index": dict(claim_index),
        "return_index": dict(return_index),
        "owner_decision_index": dict(owner_decision_index),
        "task_sessions": dict(task_sessions),
        "task_claims": dict(task_claims),
        "task_returns": dict(task_returns),
    }


def find_records_for_task(records: dict[str, Any], task_id: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "sessions": list(records.get("task_sessions", {}).get(task_id, [])),
        "claims": list(records.get("task_claims", {}).get(task_id, [])),
        "returns": list(records.get("task_returns", {}).get(task_id, [])),
    }


def _check_ref(
    ref_name: str,
    task_id: str | None,
    record_id: Any,
    record_type: str,
    records: dict[str, Any],
) -> dict[str, Any]:
    if not record_id:
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": None,
            "status": "absent",
            "level": "info",
            "message": f"{ref_name} not set",
            "matches": [],
        }

    index_name = {
        "session": "session_index",
        "claim": "claim_index",
        "return": "return_index",
    }[record_type]
    matches = list(records.get(index_name, {}).get(str(record_id), []))
    normalized_matches = [
        {
            "path": item.get("path"),
            "task_id": item.get("task_id"),
            "record_id": item.get("record_id"),
            "parse_errors": item.get("parse_errors", []),
        }
        for item in matches
    ]
    if not matches:
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": record_id,
            "status": "missing",
            "level": "warn",
            "message": f"{ref_name} references missing {record_type} record",
            "matches": [],
        }

    if any(task_id and item.get("task_id") != task_id for item in matches):
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": record_id,
            "status": "conflict",
            "level": "needs_owner",
            "message": f"{ref_name} points to {record_type} record with mismatched task_id",
            "matches": normalized_matches,
        }

    if len(matches) > 1:
        return {
            "reference": ref_name,
            "record_type": record_type,
            "record_id": record_id,
            "status": "conflict",
            "level": "needs_owner",
            "message": f"{ref_name} matches duplicate {record_type} records",
            "matches": normalized_matches,
        }

    return {
        "reference": ref_name,
        "record_type": record_type,
        "record_id": record_id,
        "status": "ok",
        "level": "info",
        "message": f"{ref_name} references an existing {record_type} record",
        "matches": normalized_matches,
    }


def check_task_record_refs(task: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata", {})
    task_id = task.get("task_id") or metadata.get("task_id")
    checks = [
        _check_ref("claim_id", task_id, metadata.get("claim_id"), "claim", records),
        _check_ref("active_session_id", task_id, metadata.get("active_session_id"), "session", records),
        _check_ref("last_session_id", task_id, metadata.get("last_session_id"), "session", records),
    ]
    return_ref = metadata.get("return_record_ref") or metadata.get("return_event_ref")
    if return_ref:
        checks.append(_check_ref("return_record_ref", task_id, return_ref, "return", records))

    warnings = [item["message"] for item in checks if item["level"] == "warn"]
    needs_owner_reasons = [item["message"] for item in checks if item["level"] == "needs_owner"]
    return {
        "checks": checks,
        "warnings": warnings,
        "needs_owner_reasons": needs_owner_reasons,
    }
