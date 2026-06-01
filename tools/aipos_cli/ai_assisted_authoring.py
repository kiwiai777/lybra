from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, make_response
from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN, snapshot_hash
from tools.aipos_cli.draft_writer import create_draft
from tools.aipos_cli.record_writer import render_markdown

AUTHORING_OPERATION = "ai_assisted_fixture_authoring"
PROVENANCE_ROOT = Path("5_tasks/records/authoring_provenance")
PROMPT_TEMPLATE_REF = "0_control_plane/tasks/prompt_templates/ai_assisted_task_authoring_v1.md"
PROMPT_TEMPLATE_VERSION = "1"
LIVE_OPERATION = "ai_assisted_live_authoring"
LIVE_ADAPTER_ID = "live-http-json-v1"
LIVE_PROVIDER_REF = "provider-neutral"
DEFAULT_LIVE_REQUEST_CONFIG_REF = "live-default"
DEFAULT_LIVE_TIMEOUT_SECONDS = 30
DEFAULT_LIVE_MAX_OUTPUT_TOKENS = 768
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


def _load_prompt_template_text() -> str:
    path = product_repo_root() / PROMPT_TEMPLATE_REF
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {PROMPT_TEMPLATE_REF}")
    return path.read_text(encoding="utf-8")


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


def _attempt_signature(
    intent_id: str,
    source_ref: str,
    retry_of: str | None,
) -> str:
    digest = hashlib.sha256(f"{intent_id}\n{source_ref}\n{retry_of or ''}".encode("utf-8")).hexdigest()[:16]
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
        "provider_ref": attempt.get("provider_ref"),
        "endpoint_ref": attempt["endpoint_ref"],
        "model_ref": attempt["model_ref"],
        "credential_ref": attempt.get("credential_ref"),
        "prompt_template_ref": PROMPT_TEMPLATE_REF,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "request_config_ref": attempt["request_config_ref"],
        "request_timeout_seconds": attempt.get("request_timeout_seconds"),
        "max_output_tokens": attempt.get("max_output_tokens"),
        "attempt_timestamp": attempt["attempt_timestamp"],
        "attempt_status": attempt["attempt_status"],
        "retry_of": attempt.get("retry_of"),
        "source_intent_ref": attempt["source_intent_ref"],
        "token_cost_estimate": json.dumps(estimate, sort_keys=True) if estimate is not None else None,
        "raw_prompt_persisted": bool(attempt.get("raw_prompt_persisted", False)),
        "raw_response_persisted": bool(attempt.get("raw_response_persisted", False)),
        "network_call_performed": bool(attempt.get("network_call_performed", False)),
        "credential_read_performed": bool(attempt.get("credential_read_performed", False)),
    }


def _provenance_markdown(attempt: dict[str, Any]) -> str:
    metadata = _provenance_metadata(attempt)
    is_live = bool(attempt.get("network_call_performed"))
    body = "\n".join(
        [
            f"# AI Authoring Provenance: {attempt['attempt_id']}",
            "",
            "## Boundary",
            "",
            "- Live adapter." if is_live else "- Fixture-only adapter.",
            "- Network call performed." if is_live else "- No network call.",
            "- Credential read performed." if bool(attempt.get("credential_read_performed")) else "- No credential read.",
            "- Raw prompt and raw response were not persisted.",
            "",
        ]
    )
    return render_markdown(metadata, body)


def _authoring_preview_response(
    repo_root: Path,
    *,
    operation: str,
    actor: str,
    source_payload: dict[str, Any],
    attempt: dict[str, Any],
    proposal: dict[str, Any] | None,
    triage: dict[str, Any] | None,
    assignment_recommendations: dict[str, Any] | None,
    warnings: list[str] | None,
    safety_notice: str,
    draft_result_suffix: str = "AI-assisted draft write",
) -> dict[str, Any]:
    blocking: list[str] = []
    warnings = list(warnings or [])
    draft_preview: dict[str, Any] | None = None

    if attempt.get("attempt_status") != "drafted":
        blocking.append(str(source_payload.get("failure_reason") or f"Live authoring adapter returned status {attempt.get('attempt_status')!r}"))
    elif not isinstance(proposal, dict):
        blocking.append("AI authoring response proposal must be a mapping")
    else:
        frontmatter = proposal.get("frontmatter")
        body = proposal.get("body")
        if not isinstance(frontmatter, dict):
            blocking.append("AI authoring response proposal.frontmatter must be a mapping")
        elif not isinstance(body, str):
            blocking.append("AI authoring response proposal.body must be a string")
        else:
            blocking.extend(_proposal_policy_blocks(source_payload, frontmatter))
            draft_preview = create_draft(repo_root, frontmatter, body, dry_run=True)
            blocking.extend(draft_preview.get("blocking_reasons", []))
            warnings.extend(draft_preview.get("warnings", []))

    provenance_rel = _provenance_path(attempt["attempt_id"])
    provenance_target = repo_root / provenance_rel
    if provenance_target.exists():
        blocking.append(f"AI authoring provenance already exists: {provenance_rel.as_posix()}")

    draft_target = str((draft_preview or {}).get("target_path") or "")
    planned_writes: list[dict[str, Any]] = []
    if not blocking and draft_target:
        planned_writes = [
            {"path": draft_target, "kind": "create", "type": "draft_markdown"},
            {"path": provenance_rel.as_posix(), "kind": "create", "type": "ai_authoring_provenance"},
        ]

    verdict = derive_verdict(
        blocking_reasons=blocking,
        warnings=warnings,
        needs_owner_reasons=["Owner review and confirmation required before AI-assisted draft write"] if not blocking else [],
    )
    data = {
        "original_payload": source_payload,
        "attempt": attempt,
        "proposal": deepcopy(proposal) if isinstance(proposal, dict) else None,
        "triage": deepcopy(triage) if isinstance(triage, dict) else None,
        "assignment_recommendations": deepcopy(assignment_recommendations) if isinstance(assignment_recommendations, dict) else None,
        "draft_preview": draft_preview,
        "target_path": draft_target or None,
        "target_file_state": _file_state(repo_root / draft_target) if draft_target else None,
        "provenance_path": provenance_rel.as_posix(),
        "provenance_file_state": _file_state(provenance_target),
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "network_call_performed": bool(source_payload.get("network_call_performed")),
        "credential_read_performed": bool(source_payload.get("credential_read_performed")),
        "provider_ref": attempt.get("provider_ref"),
        "credential_ref": attempt.get("credential_ref"),
        "request_timeout_seconds": attempt.get("request_timeout_seconds"),
        "max_output_tokens": attempt.get("max_output_tokens"),
        "response_status": source_payload.get("response_status"),
    }
    return make_response(
        ok=not blocking,
        verdict=verdict,
        operation=operation,
        dry_run=True,
        actor=_actor_payload(actor),
        data=data,
        summary={
            "attempt_id": attempt["attempt_id"],
            "attempt_status": attempt["attempt_status"],
            "fixture_id": source_payload.get("fixture_id"),
            "provider_ref": attempt.get("provider_ref"),
        },
        planned_writes=planned_writes,
        warnings=warnings,
        blocking_reasons=blocking,
        needs_owner_reasons=["Owner review and confirmation required before AI-assisted draft write"] if not blocking else [],
        owner_confirmation_required=not blocking,
        owner_confirmation_reasons=["Owner review and confirmation required before AI-assisted draft write"] if not blocking else [],
        execute_allowed=not blocking,
        execute_blocking_reasons=blocking,
        safety_notice=safety_notice,
    )


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
    fixture = load_fixture(fixture_id)
    attempt = _fixture_attempt(intent, fixture, fixture_id)
    proposal = fixture.get("proposal")
    return _authoring_preview_response(
        repo_root,
        operation=AUTHORING_OPERATION,
        actor=actor,
        source_payload={
            "intent": intent,
            "fixture_id": fixture_id,
            "failure_reason": fixture.get("failure_reason"),
            "policy_requests": fixture.get("policy_requests"),
            "network_call_performed": False,
            "credential_read_performed": False,
        },
        attempt=attempt,
        proposal=proposal if isinstance(proposal, dict) else None,
        triage=fixture.get("triage") if isinstance(fixture.get("triage"), dict) else None,
        assignment_recommendations=fixture.get("assignment_recommendations") if isinstance(fixture.get("assignment_recommendations"), dict) else None,
        warnings=list(fixture.get("warnings") or []),
        safety_notice=SAFETY_NOTICE,
    )


def _resolve_env_credential_ref(credential_ref: str) -> tuple[str, str]:
    ref = str(credential_ref or "").strip()
    if not ref.startswith("env:"):
        raise ValueError("credential_ref must use env:NAME")
    env_name = ref[4:].strip()
    _safe_id(env_name, "credential_ref")
    secret = os.environ.get(env_name)
    if not secret:
        raise ValueError(f"credential_ref environment variable is missing: {env_name}")
    return ref, secret


def _render_live_request_prompt(intent: dict[str, Any], prompt_template_text: str) -> str:
    requirement = str(intent.get("requirement") or "").strip()
    hints = {
        "project_hint": intent.get("project_hint"),
        "task_mode_hint": intent.get("task_mode_hint"),
        "task_class_hint": intent.get("task_class_hint"),
        "priority_hint": intent.get("priority_hint"),
        "output_target_hint": intent.get("output_target_hint"),
        "context_bundle_hint": intent.get("context_bundle_hint"),
        "retry_of": intent.get("retry_of"),
        "candidate_agent_instances": intent.get("candidate_agent_instances") or [],
        "candidate_reviewers": intent.get("candidate_reviewers") or [],
        "candidate_auditors": intent.get("candidate_auditors") or [],
    }
    return "\n".join(
        [
            prompt_template_text.strip(),
            "",
            "## Intent",
            "",
            json.dumps({"requirement": requirement, **{key: value for key, value in hints.items() if value not in (None, "", [])}}, indent=2, sort_keys=True),
            "",
            "Return a JSON object that matches the required proposal shape exactly.",
        ]
    )


def _post_live_adapter_request(
    *,
    endpoint_ref: str,
    credential_secret: str,
    request_payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    request_body = json.dumps(request_payload, sort_keys=True).encode("utf-8")
    request = Request(
        endpoint_ref,
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {credential_secret}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if getattr(exc, "fp", None) is not None else ""
        raise RuntimeError(f"live adapter HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"live adapter network failure: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("live adapter timed out") from exc
    if status != 200:
        raise RuntimeError(f"live adapter HTTP {status}")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("live adapter response must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("live adapter response must be a JSON object")
    return parsed


def _live_source_payload(
    *,
    intent: dict[str, Any],
    endpoint_ref: str,
    provider_ref: str,
    credential_ref: str,
    model_ref: str,
    request_config_ref: str,
    request_timeout_seconds: int,
    max_output_tokens: int,
    response_status: str | None,
) -> dict[str, Any]:
    payload = {
        "intent": intent,
        "endpoint_ref": endpoint_ref,
        "provider_ref": provider_ref,
        "credential_ref": credential_ref,
        "model_ref": model_ref,
        "request_config_ref": request_config_ref,
        "request_timeout_seconds": request_timeout_seconds,
        "max_output_tokens": max_output_tokens,
        "network_call_performed": True,
        "credential_read_performed": True,
        "response_status": response_status,
    }
    return payload


def build_live_authoring_draft(
    repo_root: Path,
    intent: dict[str, Any],
    *,
    endpoint_ref: str,
    credential_ref: str,
    model_ref: str,
    actor: str,
    provider_ref: str = LIVE_PROVIDER_REF,
    request_config_ref: str = DEFAULT_LIVE_REQUEST_CONFIG_REF,
    request_timeout_seconds: int = DEFAULT_LIVE_TIMEOUT_SECONDS,
    max_output_tokens: int = DEFAULT_LIVE_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    endpoint = str(endpoint_ref or "").strip()
    if not endpoint:
        raise ValueError("endpoint_ref is required")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("endpoint_ref must use http or https")
    if not str(model_ref or "").strip():
        raise ValueError("model_ref is required")
    if request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")
    if max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be positive")
    try:
        normalized_credential_ref, credential_secret = _resolve_env_credential_ref(credential_ref)
    except ValueError as exc:
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=True,
            category="VALIDATION_ERROR",
            message=str(exc),
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    prompt_template_text = _load_prompt_template_text()
    live_attempt_source_ref = "|".join(
        [
            "live",
            endpoint,
            str(provider_ref or LIVE_PROVIDER_REF).strip(),
            str(model_ref).strip(),
            normalized_credential_ref,
            str(request_config_ref or DEFAULT_LIVE_REQUEST_CONFIG_REF).strip(),
        ]
    )
    attempt = {
        "attempt_id": _attempt_signature(_safe_id(intent.get("intent_id"), "intent_id"), live_attempt_source_ref, str(intent.get("retry_of") or "").strip() or None),
        "intent_id": _safe_id(intent.get("intent_id"), "intent_id"),
        "adapter_id": LIVE_ADAPTER_ID,
        "provider_ref": str(provider_ref or LIVE_PROVIDER_REF).strip() or LIVE_PROVIDER_REF,
        "endpoint_ref": endpoint,
        "model_ref": str(model_ref).strip(),
        "credential_ref": normalized_credential_ref,
        "request_config_ref": str(request_config_ref or DEFAULT_LIVE_REQUEST_CONFIG_REF).strip(),
        "request_timeout_seconds": int(request_timeout_seconds),
        "max_output_tokens": int(max_output_tokens),
        "attempt_timestamp": str(intent.get("submitted_at") or _iso_z(_utc_now())),
        "attempt_status": "drafted",
        "retry_of": str(intent.get("retry_of") or "").strip() or None,
        "source_intent_ref": f"intent:{_safe_id(intent.get('intent_id'), 'intent_id')}",
        "token_cost_estimate": None,
        "network_call_performed": True,
        "credential_read_performed": True,
    }
    request_payload = {
        "adapter_id": LIVE_ADAPTER_ID,
        "provider_ref": attempt["provider_ref"],
        "endpoint_ref": attempt["endpoint_ref"],
        "credential_ref": attempt["credential_ref"],
        "model_ref": attempt["model_ref"],
        "request_config_ref": attempt["request_config_ref"],
        "prompt_template_ref": PROMPT_TEMPLATE_REF,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "request_timeout_seconds": attempt["request_timeout_seconds"],
        "max_output_tokens": attempt["max_output_tokens"],
        "attempt": {
            "attempt_id": attempt["attempt_id"],
            "intent_id": attempt["intent_id"],
            "retry_of": attempt["retry_of"],
            "source_intent_ref": attempt["source_intent_ref"],
        },
        "intent": intent,
        "messages": [
            {"role": "system", "content": prompt_template_text},
            {"role": "user", "content": _render_live_request_prompt(intent, prompt_template_text)},
        ],
    }
    try:
        response = _post_live_adapter_request(
            endpoint_ref=endpoint,
            credential_secret=credential_secret,
            request_payload=request_payload,
            timeout_seconds=int(request_timeout_seconds),
        )
    except (RuntimeError, ValueError) as exc:
        live_payload = _live_source_payload(
            intent=intent,
            endpoint_ref=endpoint,
            provider_ref=attempt["provider_ref"],
            credential_ref=attempt["credential_ref"],
            model_ref=attempt["model_ref"],
            request_config_ref=attempt["request_config_ref"],
            request_timeout_seconds=attempt["request_timeout_seconds"],
            max_output_tokens=attempt["max_output_tokens"],
            response_status="failed",
        )
        failure = {"failure_reason": str(exc), **live_payload}
        return _authoring_preview_response(
            repo_root,
            operation=LIVE_OPERATION,
            actor=actor,
            source_payload=failure,
            attempt={**attempt, "attempt_status": "failed"},
            proposal=None,
            triage=None,
            assignment_recommendations=None,
            warnings=[str(exc)],
            safety_notice=SAFETY_NOTICE,
        )

    response_status = str(response.get("status") or "").strip().lower()
    if response_status not in {"drafted", "blocked", "failed", "timed_out"}:
        response_status = "drafted" if response.get("proposal") else "failed"
    live_payload = _live_source_payload(
        intent=intent,
        endpoint_ref=endpoint,
        provider_ref=str(response.get("provider_ref") or attempt["provider_ref"]).strip(),
        credential_ref=attempt["credential_ref"],
        model_ref=str(response.get("model_ref") or attempt["model_ref"]).strip(),
        request_config_ref=str(response.get("request_config_ref") or attempt["request_config_ref"]).strip(),
        request_timeout_seconds=attempt["request_timeout_seconds"],
        max_output_tokens=attempt["max_output_tokens"],
        response_status=response_status,
    )
    live_payload["fixture_id"] = response.get("fixture_id")
    live_payload["network_call_performed"] = True
    live_payload["credential_read_performed"] = True

    proposal = response.get("proposal")
    triage = response.get("triage")
    assignment_recommendations = response.get("assignment_recommendations")
    warnings = list(response.get("warnings") or [])
    token_cost_estimate = response.get("token_cost_estimate")
    if token_cost_estimate is not None:
        attempt["token_cost_estimate"] = deepcopy(token_cost_estimate)
    if response_status == "timed_out":
        warnings.append("live adapter timed out")
    if response_status == "blocked":
        warnings.extend(str(item) for item in response.get("blocking_reasons") or [])
    if response_status != "drafted":
        response_reasons = [str(item) for item in response.get("blocking_reasons") or []]
        if response_status == "timed_out" and not response_reasons:
            response_reasons.append("live adapter timed out")
        live_payload["failure_reason"] = "; ".join(response_reasons) if response_reasons else f"Live authoring adapter returned status {response_status!r}"
    result = _authoring_preview_response(
        repo_root,
        operation=LIVE_OPERATION,
        actor=actor,
        source_payload=live_payload,
        attempt=attempt | {"attempt_status": "drafted" if response_status == "drafted" else response_status},
        proposal=proposal if isinstance(proposal, dict) else None,
        triage=triage if isinstance(triage, dict) else None,
        assignment_recommendations=assignment_recommendations if isinstance(assignment_recommendations, dict) else None,
        warnings=warnings,
        safety_notice=SAFETY_NOTICE,
    )
    if result.get("blocking_reasons"):
        return result
    return _attach_dry_run_metadata(result, LIVE_OPERATION, actor)


def confirm_live_authoring_draft(
    repo_root: Path,
    envelope: dict[str, Any],
    *,
    actor: str,
    owner_confirmation_token: str | None,
) -> dict[str, Any]:
    if envelope.get("operation") != LIVE_OPERATION:
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="UNSUPPORTED_OPERATION",
            message="live authoring confirm requires a live BYO-LLM preview",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    if (envelope.get("actor") or {}).get("actor") != actor:
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="ACTOR_MISMATCH",
            message="confirm actor does not match live authoring preview actor",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    if _is_expired(envelope.get("dry_run_expires_at")):
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="live authoring preview expired; run draft again",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="OWNER_CONFIRMATION_REQUIRED",
            message="owner confirmation token is required and must equal OWNER_CONFIRMED",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    original = (envelope.get("data") or {}).get("original_payload")
    if not isinstance(original, dict) or not isinstance(original.get("intent"), dict):
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="live authoring preview is missing original payload",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    proposal = (envelope.get("data") or {}).get("proposal")
    if not isinstance(proposal, dict):
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="live authoring preview is missing proposal payload",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    attempt = (envelope.get("data") or {}).get("attempt")
    if not isinstance(attempt, dict):
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="live authoring preview is missing attempt metadata",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    rebuilt = _authoring_preview_response(
        repo_root,
        operation=LIVE_OPERATION,
        actor=actor,
        source_payload=original,
        attempt=attempt,
        proposal=proposal,
        triage=(envelope.get("data") or {}).get("triage") if isinstance((envelope.get("data") or {}).get("triage"), dict) else None,
        assignment_recommendations=(envelope.get("data") or {}).get("assignment_recommendations") if isinstance((envelope.get("data") or {}).get("assignment_recommendations"), dict) else None,
        warnings=list((envelope.get("warnings") or [])),
        safety_notice=SAFETY_NOTICE,
    )
    if snapshot_hash(LIVE_OPERATION, actor, rebuilt) != envelope.get("dry_run_snapshot_hash"):
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="live authoring preview snapshot mismatch; run draft again",
            actor=_actor_payload(actor),
            safety_notice=SAFETY_NOTICE,
        )
    if rebuilt.get("blocking_reasons"):
        return rebuilt
    proposal_frontmatter = proposal["frontmatter"]
    draft_result = create_draft(repo_root, proposal_frontmatter, proposal["body"], dry_run=False)
    if draft_result.get("verdict") == "BLOCK" or not draft_result.get("wrote"):
        return blocked_response(
            operation=LIVE_OPERATION,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="standard draft write failed during live authoring confirm",
            actor=_actor_payload(actor),
            data={"draft_result": draft_result},
            safety_notice=SAFETY_NOTICE,
        )
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


def _attach_dry_run_metadata(result: dict[str, Any], operation: str, actor: str) -> dict[str, Any]:
    now = _utc_now()
    result.update(
        {
            "dry_run_id": f"dryrun_{uuid.uuid4().hex}",
            "dry_run_snapshot_hash": snapshot_hash(operation, actor, result),
            "dry_run_created_at": _iso_z(now),
            "dry_run_expires_at": _iso_z(now + timedelta(minutes=10)),
        }
    )
    result["dry_run_token"] = result["dry_run_id"]
    return result


def build_authoring_draft(repo_root: Path, intent: dict[str, Any], *, fixture_id: str, actor: str) -> dict[str, Any]:
    result = build_authoring_plan(repo_root, intent, fixture_id=fixture_id, actor=actor, dry_run=True)
    if result.get("blocking_reasons"):
        return result
    return _attach_dry_run_metadata(result, AUTHORING_OPERATION, actor)


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
