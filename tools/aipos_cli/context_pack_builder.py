from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.records import load_records
from tools.aipos_cli.task_loader import find_task_by_id, load_all_tasks, load_task_by_path
from tools.aipos_cli.validator import validate_single_task

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


MAX_BODY_EXCERPT = 1200
CONTEXT_BUNDLE_ROOT = Path("3_context_bundles")


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return text or "context"


def _safe_ref(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _body_excerpt(body: str) -> str:
    text = str(body or "").strip()
    if len(text) <= MAX_BODY_EXCERPT:
        return text
    return text[:MAX_BODY_EXCERPT].rstrip() + "\n..."


def _parse_bundle_text(text: str) -> tuple[dict[str, Any], list[str]]:
    metadata, body, warnings = parse_markdown_frontmatter(text)
    if metadata:
        return {**metadata, "body": body.strip()}, warnings
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
            if isinstance(data, dict):
                return data, warnings
            return {}, ["Context bundle did not parse to a mapping"]
        except Exception as exc:
            warnings.append(f"Context bundle YAML parse failed: {exc}")
    return {}, warnings


def _candidate_bundle_paths(ref: str) -> list[Path]:
    if not ref:
        return []
    raw = Path(ref)
    candidates: list[Path] = []
    if raw.suffix == ".md":
        candidates.append(raw)
    candidates.extend(
        [
            CONTEXT_BUNDLE_ROOT / "examples" / f"{ref}.md",
            CONTEXT_BUNDLE_ROOT / "agents" / f"{ref}.md",
            CONTEXT_BUNDLE_ROOT / "roles" / f"{ref}.md",
            CONTEXT_BUNDLE_ROOT / f"{ref}.md",
        ]
    )
    return candidates


def _resolve_context_bundle(repo_root: Path, ref: str) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not ref:
        return {"ref": None, "found": False, "path": None}, ["context_bundle is missing"]
    for candidate in _candidate_bundle_paths(ref):
        if candidate.is_absolute() or ".." in candidate.parts:
            warnings.append(f"Skipping unsafe context bundle ref candidate: {candidate.as_posix()}")
            continue
        path = repo_root / candidate
        if not path.exists():
            continue
        if not path.is_file():
            warnings.append(f"Context bundle candidate is not a file: {candidate.as_posix()}")
            continue
        data, parse_warnings = _parse_bundle_text(path.read_text(encoding="utf-8"))
        bundle = {
            "ref": ref,
            "found": True,
            "path": candidate.as_posix(),
            "role_instance": data.get("role_instance"),
            "agent_instance": data.get("agent_instance"),
            "environment": data.get("environment"),
            "description": data.get("description"),
            "allowed_task_modes": _as_list(data.get("allowed_task_modes")),
            "preferred_model_tiers": _as_list(data.get("preferred_model_tiers")),
            "allowed_model_tiers": _as_list(data.get("allowed_model_tiers")),
            "memory_access": _as_list(data.get("memory_access")),
            "output_target": _as_list(data.get("output_target")),
            "escalation_rules": _as_list(data.get("escalation_rules")),
            "constraints": _as_list(data.get("constraints")),
        }
        return bundle, warnings + parse_warnings
    return {"ref": ref, "found": False, "path": None}, [f"context_bundle not found: {ref}"] + warnings


def _select_task(repo_root: Path, *, task_id: str | None, path: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    if bool(task_id) == bool(path):
        return None, ["Exactly one of task_id or path is required for task-scoped context pack preview"]
    if task_id:
        selected, matches = find_task_by_id(task_id, repo_root)
        if not matches:
            return None, [f"No task found for task_id: {task_id}"]
        if len(matches) > 1:
            paths = ", ".join(sorted(str(match.get("path")) for match in matches))
            return None, [f"Duplicate task_id {task_id} found in: {paths}"]
        return selected, []
    assert path is not None
    try:
        return load_task_by_path(path, repo_root), []
    except (OSError, ValueError) as exc:
        return None, [str(exc) or exc.__class__.__name__]


def _task_payload(task: dict[str, Any], validated: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(task.get("metadata") or {})
    return {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "path": task.get("path"),
        "queue_state": task.get("queue_state"),
        "status": metadata.get("status") or task.get("frontmatter_status"),
        "project": metadata.get("project"),
        "assigned_to": metadata.get("assigned_to"),
        "agent_instance": metadata.get("agent_instance"),
        "task_mode": metadata.get("task_mode"),
        "model_tier": metadata.get("model_tier"),
        "context_bundle_ref": metadata.get("context_bundle"),
        "artifact_scope": metadata.get("artifact_scope"),
        "memory_scope": metadata.get("memory_scope"),
        "source_tag": metadata.get("source_tag"),
        "client_tag": metadata.get("client_tag"),
        "external_ref": metadata.get("external_ref"),
        "output_target": metadata.get("output_target"),
        "orchestration_id": metadata.get("orchestration_id"),
        "forum_thread_ref": metadata.get("forum_thread_ref"),
        "verdict": validated.get("verdict"),
        "warnings": list(validated.get("warnings", [])),
        "blocking_reasons": list(validated.get("blocking_reasons", [])),
        "needs_owner_reasons": list(validated.get("needs_owner_reasons", [])),
        "body_excerpt": _body_excerpt(str(task.get("body") or "")),
    }


def _orchestration_payload(
    repo_root: Path,
    orchestration_id: str | None,
    *,
    tasks: list[dict[str, Any]],
    records: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    if not orchestration_id:
        return {"orchestration_id": None, "summary_available": False}, [], []
    try:
        summary = build_orchestration_summary_preview(repo_root, orchestration_id, tasks=tasks, records=records)
    except (OSError, ValueError) as exc:
        return {"orchestration_id": orchestration_id, "summary_available": False}, [str(exc) or exc.__class__.__name__], []
    planned = dict(summary.get("planned_summary") or {})
    owner_reasons = list(planned.get("needs_owner_reasons", []))
    return (
        {
            "orchestration_id": orchestration_id,
            "summary_available": True,
            "summary": planned,
            "owner_attention": bool(owner_reasons or summary.get("owner_confirmation_required")),
            "source_refs": list(summary.get("source_refs", [])),
            "conflicts": list(summary.get("conflicts", [])),
            "verdict": summary.get("verdict"),
        },
        list(summary.get("warnings", [])),
        owner_reasons,
    )


def _pack_id(source: str, refs: list[str]) -> str:
    digest = hashlib.sha256("|".join(refs).encode("utf-8")).hexdigest()[:10]
    return f"ctxpack_{_slug(source)}_{digest}"


def build_context_pack_preview(
    repo_root: Path,
    *,
    task_id: str | None = None,
    path: str | None = None,
    orchestration_id: str | None = None,
) -> dict[str, Any]:
    tasks = load_all_tasks(repo_root)
    records = load_records(repo_root)
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    needs_owner_reasons: list[str] = []
    task_payload: dict[str, Any] | None = None
    bundle_payload: dict[str, Any] = {"ref": None, "found": False, "path": None}
    selected_task: dict[str, Any] | None = None
    source_type = "orchestration" if orchestration_id and not (task_id or path) else "queue_task"

    if task_id or path or not orchestration_id:
        selected_task, select_errors = _select_task(repo_root, task_id=task_id, path=path)
        if select_errors:
            blocking_reasons.extend(select_errors)
        if selected_task is not None:
            validated = validate_single_task(selected_task, tasks=tasks, records=records)
            task_payload = _task_payload(selected_task, validated)
            warnings.extend(task_payload.get("warnings", []))
            needs_owner_reasons.extend(task_payload.get("needs_owner_reasons", []))
            bundle_payload, bundle_warnings = _resolve_context_bundle(
                repo_root,
                _safe_ref(task_payload.get("context_bundle_ref")),
            )
            warnings.extend(bundle_warnings)
            if not orchestration_id:
                orchestration_id = _safe_ref(task_payload.get("orchestration_id")) or None

    orchestration_payload, orchestration_warnings, orchestration_owner = _orchestration_payload(
        repo_root,
        orchestration_id,
        tasks=tasks,
        records=records,
    )
    warnings.extend(orchestration_warnings)
    needs_owner_reasons.extend(orchestration_owner)

    source_refs = []
    if task_payload and task_payload.get("path"):
        source_refs.append(str(task_payload["path"]))
    if bundle_payload.get("path"):
        source_refs.append(str(bundle_payload["path"]))
    source_refs.extend(str(item) for item in orchestration_payload.get("source_refs", []))
    source_refs = list(dict.fromkeys(source_refs))
    source = task_payload.get("task_id") if task_payload else orchestration_id or "unknown"

    if blocking_reasons:
        verdict = "BLOCK"
    elif needs_owner_reasons:
        verdict = "NEEDS_OWNER"
    elif warnings:
        verdict = "WARN"
    else:
        verdict = "PASS"

    disabled_capabilities = [
        "writes",
        "controlled_mutation",
        "external_rag",
        "agent_execution",
        "queue_mutation",
        "record_write",
        "orchestration_append",
        "summary_state_write",
        "git_automation",
    ]
    return {
        "action": "context_pack_preview",
        "verdict": verdict,
        "scope": "task" if task_payload else "orchestration",
        "source_type": source_type,
        "pack_id": _pack_id(str(source or "unknown"), source_refs or [str(source or "unknown")]),
        "dry_run": True,
        "writes_enabled": False,
        "execute_allowed": False,
        "controlled_mutation_enabled": False,
        "external_rag_enabled": False,
        "agent_execution_enabled": False,
        "git_automation_enabled": False,
        "dry_run_token": None,
        "planned_writes": [],
        "planned_moves": [],
        "task": task_payload or {},
        "context_bundle": bundle_payload,
        "orchestration": orchestration_payload,
        "source_refs": source_refs,
        "governance": {
            "owner_decision_gates_preserved": True,
            "independent_audit_required_for_changes": True,
            "self_audit_allowed": False,
            "hidden_queue_mutation_allowed": False,
            "autonomous_push_allowed": False,
            "external_rag_allowed": False,
            "cortex_replacement": False,
        },
        "disabled_capabilities": disabled_capabilities,
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": needs_owner_reasons,
        "safety_notice": (
            "AIPOS-78 Context Pack preview is read-only. It does not write files, call external RAG, "
            "execute agents, mutate queues, append orchestration logs, or automate git."
        ),
    }
