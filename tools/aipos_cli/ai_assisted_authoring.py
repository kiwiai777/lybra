from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, make_response
from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN, snapshot_hash
from tools.aipos_cli.draft_writer import create_draft
from tools.aipos_cli.record_writer import render_markdown

AUTHORING_OPERATION = "ai_assisted_fixture_authoring"
PROVENANCE_ROOT = Path("5_tasks/records/authoring_provenance")
PROMPT_TEMPLATE_REF = "0_control_plane/tasks/prompt_templates/ai_assisted_task_authoring_v1.md"
PROMPT_TEMPLATE_VERSION = "1"
SAFETY_NOTICE = (
    "AIPOS-151 is fixture-only: it performs no network call, reads no credential, persists no raw prompt or raw "
    "response, and requires explicit Owner confirmation before writing a standard draft and non-secret provenance sidecar."
)
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROHIBITED_POLICY_REQUESTS = {
    "bypass_owner_review",
    "request_credentials",
    "authority_expansion",
    "publish_immediately",
    "queue_mutation",
    "runtime_launch",
    "profile_mutation",
}


def product_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def fixtures_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "ai_authoring"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _actor_payload(actor: str | None) -> dict[str, str] | None:
    value = str(actor or "").strip()
    return {"actor": value} if value else None


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{field} must use letters, numbers, dot, underscore, or dash")
    return text


def _is_expired(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _utc_now() > parsed


def _file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": None}
    return {"exists": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _load_json_object(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON fixture must be an object: {path}")
    return parsed


def load_fixture(fixture_id: str) -> dict[str, Any]:
    safe_fixture = _safe_id(fixture_id, "fixture_id")
    path = (fixtures_root() / f"{safe_fixture}.json").resolve()
    try:
        path.relative_to(fixtures_root().resolve())
    except ValueError as exc:
        raise ValueError("fixture path escapes bundled fixtures root") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Fixture not found: {safe_fixture}")
    return _load_json_object(path)


def load_intent_payload(path: str | Path) -> dict[str, Any]:
    data = _load_json_object(Path(path))
    requirement = str(data.get("requirement") or "").strip()
    if not requirement:
        raise ValueError("intent.requirement is required")
    _safe_id(data.get("intent_id"), "intent_id")
    submitted_at = str(data.get("submitted_at") or "").strip()
    if not submitted_at:
        raise ValueError("intent.submitted_at is required for stable provenance")
    try:
        datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("intent.submitted_at must be an ISO timestamp") from exc
    return data


def _attempt_id(intent_id: str, fixture_id: str, retry_of: str | None) -> str:
    digest = hashlib.sha256(f"{intent_id}\n{fixture_id}\n{retry_of or ''}".encode("utf-8")).hexdigest()[:16]
    return f"authoring_{digest}"


def _provenance_path(attempt_id: str) -> Path:
    return PROVENANCE_ROOT / f"{_safe_id(attempt_id, 'attempt_id')}.md"


def _proposal_policy_blocks(fixture: dict[str, Any], frontmatter: dict[str, Any]) -> list[str]:
    blocking: list[str] = []
    policy_requests = fixture.get("policy_requests", {})
    if policy_requests not in ({}, None) and not isinstance(policy_requests, dict):
        blocking.append("proposal policy_requests must be a mapping")
        return blocking
    for key, value in (policy_requests or {}).items():
        if key in PROHIBITED_POLICY_REQUESTS and bool(value):
            blocking.append(f"AI proposal requests prohibited policy action: {key}")
    if frontmatter.get("needs_owner") is not True:
        blocking.append("AI-assisted proposal must keep needs_owner: true until Owner review")
    return blocking


def _provenance_metadata(attempt: dict[str, Any]) -> dict[str, Any]:
    estimate = attempt.get("token_cost_estimate")
    return {
        "record_type": "ai_authoring_provenance",
        "attempt_id": attempt["attempt_id"],
        "intent_id": attempt["intent_id"],
        "adapter_id": attempt["adapter_id"],
        "endpoint_ref": attempt["endpoint_ref"],
        "model_ref": attempt["model_ref"],
        "prompt_template_ref": PROMPT_TEMPLATE_REF,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "request_config_ref": attempt["request_config_ref"],
        "attempt_timestamp": attempt["attempt_timestamp"],
        "attempt_status": attempt["attempt_status"],
        "retry_of": attempt.get("retry_of"),
        "source_intent_ref": attempt["source_intent_ref"],
        "token_cost_estimate": json.dumps(estimate, sort_keys=True) if estimate is not None else None,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "network_call_performed": False,
        "credential_read_performed": False,
    }


def _provenance_markdown(attempt: dict[str, Any]) -> str:
    metadata = _provenance_metadata(attempt)
    body = "\n".join(
        [
            f"# AI Authoring Provenance: {attempt['attempt_id']}",
            "",
            "## Boundary",
            "",
            "- Fixture-only adapter.",
            "- No network call.",
            "- No credential read.",
            "- Raw prompt and raw response were not persisted.",
            "",
        ]
    )
    return render_markdown(metadata, body)


def _fixture_attempt(intent: dict[str, Any], fixture: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    intent_id = _safe_id(intent.get("intent_id"), "intent_id")
    retry_of = str(intent.get("retry_of") or "").strip() or None
    if retry_of is not None:
        _safe_id(retry_of, "retry_of")
    return {
        "attempt_id": _attempt_id(intent_id, fixture_id, retry_of),
        "intent_id": intent_id,
        "adapter_id": str(fixture.get("adapter_id") or "fixture-only-v1"),
        "endpoint_ref": str(fixture.get("endpoint_ref") or f"fixture://{fixture_id}"),
        "model_ref": str(fixture.get("model_ref") or "fixture-model"),
        "request_config_ref": str(fixture.get("request_config_ref") or "fixture-default"),
        "attempt_timestamp": str(intent.get("submitted_at") or _iso_z(_utc_now())),
        "attempt_status": str(fixture.get("status") or "failed"),
        "retry_of": retry_of,
        "source_intent_ref": f"intent:{intent_id}",
        "token_cost_estimate": deepcopy(fixture.get("token_cost_estimate")),
    }


def build_authoring_plan(
    repo_root: Path,
    intent: dict[str, Any],
    *,
    fixture_id: str,
    actor: str,
    dry_run: bool,
) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    fixture = load_fixture(fixture_id)
    attempt = _fixture_attempt(intent, fixture, fixture_id)
    proposal = fixture.get("proposal")
    draft_preview: dict[str, Any] | None = None
    if attempt["attempt_status"] != "drafted":
        blocking.append(str(fixture.get("failure_reason") or "Fixture adapter did not produce a draft"))
    elif not isinstance(proposal, dict):
        blocking.append("Fixture proposal must be a mapping")
    else:
        frontmatter = proposal.get("frontmatter")
        body = proposal.get("body")
        if not isinstance(frontmatter, dict):
            blocking.append("Fixture proposal.frontmatter must be a mapping")
        elif not isinstance(body, str):
            blocking.append("Fixture proposal.body must be a string")
        else:
            blocking.extend(_proposal_policy_blocks(fixture, frontmatter))
            draft_preview = create_draft(repo_root, frontmatter, body, dry_run=True)
            blocking.extend(draft_preview.get("blocking_reasons", []))
            warnings.extend(draft_preview.get("warnings", []))

    provenance_rel = _provenance_path(attempt["attempt_id"])
    provenance_target = repo_root / provenance_rel
    if provenance_target.exists():
        blocking.append(f"AI authoring provenance already exists: {provenance_rel.as_posix()}")
    draft_target = str((draft_preview or {}).get("target_path") or "")
    planned_writes = []
    if not blocking and draft_target:
        planned_writes = [
            {"path": draft_target, "kind": "create", "type": "draft_markdown"},
            {"path": provenance_rel.as_posix(), "kind": "create", "type": "ai_authoring_provenance"},
        ]
    verdict = derive_verdict(blocking_reasons=blocking, warnings=warnings, needs_owner_reasons=["Owner review and confirmation required before AI-assisted draft write"] if not blocking else [])
    data = {
        "original_payload": {"intent": intent, "fixture_id": fixture_id},
        "attempt": attempt,
        "proposal": deepcopy(proposal) if isinstance(proposal, dict) else None,
        "draft_preview": draft_preview,
        "target_path": draft_target or None,
        "target_file_state": _file_state(repo_root / draft_target) if draft_target else None,
        "provenance_path": provenance_rel.as_posix(),
        "provenance_file_state": _file_state(provenance_target),
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "network_call_performed": False,
        "credential_read_performed": False,
    }
    return make_response(
        ok=not blocking,
        verdict=verdict,
        operation=AUTHORING_OPERATION,
        dry_run=dry_run,
        actor=_actor_payload(actor),
        data=data,
        summary={"attempt_id": attempt["attempt_id"], "attempt_status": attempt["attempt_status"], "fixture_id": fixture_id},
        planned_writes=planned_writes,
        warnings=warnings,
        blocking_reasons=blocking,
        needs_owner_reasons=["Owner review and confirmation required before AI-assisted draft write"] if not blocking else [],
        owner_confirmation_required=not blocking,
        owner_confirmation_reasons=["Owner review and confirmation required before AI-assisted draft write"] if not blocking else [],
        execute_allowed=not blocking if dry_run else None,
        execute_blocking_reasons=blocking if dry_run else [],
        safety_notice=SAFETY_NOTICE,
    )


def build_authoring_draft(repo_root: Path, intent: dict[str, Any], *, fixture_id: str, actor: str) -> dict[str, Any]:
    result = build_authoring_plan(repo_root, intent, fixture_id=fixture_id, actor=actor, dry_run=True)
    if result.get("blocking_reasons"):
        return result
    now = _utc_now()
    result.update(
        {
            "dry_run_id": f"dryrun_{uuid.uuid4().hex}",
            "dry_run_snapshot_hash": snapshot_hash(AUTHORING_OPERATION, actor, result),
            "dry_run_created_at": _iso_z(now),
            "dry_run_expires_at": _iso_z(now + timedelta(minutes=10)),
        }
    )
    result["dry_run_token"] = result["dry_run_id"]
    return result


def confirm_authoring_draft(
    repo_root: Path,
    envelope: dict[str, Any],
    *,
    actor: str,
    owner_confirmation_token: str | None,
) -> dict[str, Any]:
    if envelope.get("operation") != AUTHORING_OPERATION:
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="UNSUPPORTED_OPERATION", message="AI authoring confirm requires an ai_assisted_fixture_authoring preview", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    if (envelope.get("actor") or {}).get("actor") != actor:
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="ACTOR_MISMATCH", message="confirm actor does not match AI authoring preview actor", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    if _is_expired(envelope.get("dry_run_expires_at")):
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="AI authoring preview expired; run draft again", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="OWNER_CONFIRMATION_REQUIRED", message="owner confirmation token is required and must equal OWNER_CONFIRMED", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    original = (envelope.get("data") or {}).get("original_payload")
    if not isinstance(original, dict) or not isinstance(original.get("intent"), dict):
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="AI authoring preview is missing original payload", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    rebuilt = build_authoring_plan(repo_root, original["intent"], fixture_id=str(original.get("fixture_id") or ""), actor=actor, dry_run=True)
    if rebuilt.get("blocking_reasons"):
        return rebuilt
    if snapshot_hash(AUTHORING_OPERATION, actor, rebuilt) != envelope.get("dry_run_snapshot_hash"):
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="AI authoring preview snapshot mismatch; run draft again", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    proposal = rebuilt["data"]["proposal"]
    draft_result = create_draft(repo_root, proposal["frontmatter"], proposal["body"], dry_run=False)
    if draft_result.get("verdict") == "BLOCK" or not draft_result.get("wrote"):
        return blocked_response(operation=AUTHORING_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="standard draft write failed during AI authoring confirm", actor=_actor_payload(actor), data={"draft_result": draft_result}, safety_notice=SAFETY_NOTICE)
    provenance_rel = Path(rebuilt["data"]["provenance_path"])
    provenance_target = repo_root / provenance_rel
    provenance_target.parent.mkdir(parents=True, exist_ok=True)
    provenance_target.write_text(_provenance_markdown(rebuilt["data"]["attempt"]), encoding="utf-8")
    rebuilt.update(
        {
            "dry_run": False,
            "verdict": "PASS",
            "performed_writes": rebuilt["planned_writes"],
            "execute_allowed": None,
            "data": {**rebuilt["data"], "draft_result": draft_result, "wrote": True},
        }
    )
    return rebuilt
