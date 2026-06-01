from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, make_response
from tools.aipos_cli.agent_profiles import INSTANCE_ID_PATTERN, PROVENANCE_FIELDS, load_agent_profiles
from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN, snapshot_hash

REGISTRY_RELATIVE_PATH = Path("0_control_plane/agents/custom_agent_profiles.yaml")
REGISTRY_VERSION = "0.1"
PROFILE_OPERATION = "custom_agent_profile_write"
SAFETY_NOTICE = (
    "Custom agent profile authoring is CLI-only and workspace-local: draft previews the exact registry write, "
    "confirm requires explicit Owner confirmation, then the written registry is revalidated."
)
ALLOWED_ACTIONS = {"upsert", "deactivate", "supersede"}
ALLOWED_IDENTITY_STATUSES = {"active", "inactive", "superseded"}
SECRET_KEY_RE = re.compile(r"(?:secret|token|password|private[_-]?key|credential|api[_-]?key)", re.IGNORECASE)
OWNER_VISIBLE_FIELDS = {
    "capabilities",
    "write_scopes",
    "allowed_modes",
    "forbidden_modes",
    "runtime_profile",
    "runtime_entrypoint",
    "runtime_command",
    "runtime_args",
    "runtime_env",
    "legacy_instance_ids",
    "supersedes_instance_ids",
    "provenance",
    "enabled",
    "identity_status",
}


def registry_path(repo_root: Path) -> Path:
    return repo_root / REGISTRY_RELATIVE_PATH


def _actor_payload(actor: str | None) -> dict[str, str] | None:
    value = str(actor or "").strip()
    return {"actor": value} if value else None


def _iso_z(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_expired(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > parsed


def _registry_file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": None}
    content = path.read_bytes()
    return {"exists": True, "sha256": hashlib.sha256(content).hexdigest()}


def _empty_registry() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "profiles": []}


def load_custom_registry(repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    path = registry_path(repo_root)
    if not path.exists():
        return _empty_registry(), []
    if yaml is None:
        return _empty_registry(), ["PyYAML is required to read custom agent profiles"]
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return _empty_registry(), [f"Custom agent registry parse failed: {exc}"]
    if not isinstance(parsed, dict):
        return _empty_registry(), ["Custom agent registry must be a mapping"]
    profiles = parsed.get("profiles")
    if not isinstance(profiles, list):
        return _empty_registry(), ["Custom agent registry profiles must be a list"]
    return {"version": str(parsed.get("version") or ""), "profiles": profiles}, []


def _contains_secret_like_key(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if SECRET_KEY_RE.search(key_text):
                findings.append(f"secret-like field is prohibited: {child_path}")
            findings.extend(_contains_secret_like_key(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_contains_secret_like_key(item, f"{path}[{index}]"))
    return findings


def validate_custom_registry_data(data: dict[str, Any]) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    if str(data.get("version") or "") != REGISTRY_VERSION:
        blocking.append(f"registry version must equal {REGISTRY_VERSION}")
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        blocking.append("registry profiles must be a list")
        profiles = []

    seen_instances: set[str] = set()
    seen_legacy: dict[str, str] = {}
    display_names: dict[str, list[str]] = {}
    for profile_index, profile in enumerate(profiles):
        prefix = f"profiles[{profile_index}]"
        if not isinstance(profile, dict):
            blocking.append(f"{prefix} must be a mapping")
            continue
        if not str(profile.get("agent_id") or "").strip():
            blocking.append(f"{prefix}.agent_id is required")
        instances = profile.get("instances")
        if not isinstance(instances, list):
            blocking.append(f"{prefix}.instances must be a list")
            continue
        for instance_index, instance in enumerate(instances):
            instance_prefix = f"{prefix}.instances[{instance_index}]"
            if not isinstance(instance, dict):
                blocking.append(f"{instance_prefix} must be a mapping")
                continue
            instance_id = str(instance.get("agent_instance") or "").strip()
            if not instance_id:
                blocking.append(f"{instance_prefix}.agent_instance is required")
                continue
            if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
                blocking.append(f"invalid canonical agent_instance: {instance_id}")
            if instance_id in seen_instances:
                blocking.append(f"duplicate canonical agent_instance: {instance_id}")
            seen_instances.add(instance_id)
            status = str(instance.get("identity_status") or "active")
            if status not in ALLOWED_IDENTITY_STATUSES:
                blocking.append(f"invalid identity_status for {instance_id}: {status}")
            display_name = str(instance.get("display_name") or "").strip()
            if not display_name:
                warnings.append(f"display_name is missing for {instance_id}")
            else:
                display_names.setdefault(display_name, []).append(instance_id)
            provenance = instance.get("provenance", {})
            if not isinstance(provenance, dict):
                blocking.append(f"provenance must be a mapping for {instance_id}")
            else:
                for key, value in provenance.items():
                    if key not in PROVENANCE_FIELDS:
                        blocking.append(f"unsupported provenance field for {instance_id}: {key}")
                    elif value is not None and not isinstance(value, str):
                        blocking.append(f"provenance.{key} must be a free-form string for {instance_id}")
                if not provenance:
                    warnings.append(f"optional provenance is missing for {instance_id}")
            runtime_env = instance.get("runtime_env", {})
            if runtime_env not in ({}, None):
                blocking.append(f"runtime_env must remain empty in custom profile authoring for {instance_id}")
            for legacy_id in instance.get("legacy_instance_ids", []) or []:
                legacy_text = str(legacy_id or "").strip()
                if not legacy_text:
                    continue
                previous = seen_legacy.get(legacy_text)
                if previous and previous != instance_id:
                    blocking.append(f"ambiguous legacy agent_instance mapping: {legacy_text}")
                seen_legacy[legacy_text] = instance_id
            blocking.extend(_contains_secret_like_key(instance, instance_prefix))

    for display_name, instance_ids in display_names.items():
        if len(instance_ids) > 1:
            warnings.append(f"duplicate display_name: {display_name}")
    verdict = derive_verdict(blocking_reasons=blocking, warnings=warnings)
    return {
        "ok": not blocking,
        "verdict": verdict,
        "blocking_reasons": blocking,
        "warnings": warnings,
        "summary": {"profiles": len(profiles), "instances": len(seen_instances)},
    }


def validate_custom_registry(repo_root: Path) -> dict[str, Any]:
    data, parse_blocking = load_custom_registry(repo_root)
    result = validate_custom_registry_data(data)
    result["blocking_reasons"] = [*parse_blocking, *result["blocking_reasons"]]
    result["ok"] = not result["blocking_reasons"]
    result["verdict"] = derive_verdict(
        blocking_reasons=result["blocking_reasons"],
        warnings=result["warnings"],
    )
    return {
        "scope": "custom_agent_profiles",
        "path": REGISTRY_RELATIVE_PATH.as_posix(),
        **result,
    }


def _find_instance(data: dict[str, Any], instance_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for profile in data.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        for instance in profile.get("instances", []) or []:
            if isinstance(instance, dict) and instance.get("agent_instance") == instance_id:
                return profile, instance
    return None, None


def _ensure_profile(data: dict[str, Any], agent_id: str) -> dict[str, Any]:
    for profile in data["profiles"]:
        if isinstance(profile, dict) and profile.get("agent_id") == agent_id:
            return profile
    profile = {"agent_id": agent_id, "display_name": agent_id, "enabled": True, "instances": []}
    data["profiles"].append(profile)
    return profile


def _normalize_instance(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("instance must be a mapping")
    instance = deepcopy(raw)
    instance_id = str(instance.get("agent_instance") or "").strip()
    if not instance_id:
        raise ValueError("instance.agent_instance is required")
    instance["agent_instance"] = instance_id
    instance.setdefault("display_name", "")
    instance["legacy_instance_ids"] = list(dict.fromkeys(instance.get("legacy_instance_ids", []) or []))
    instance["supersedes_instance_ids"] = list(dict.fromkeys(instance.get("supersedes_instance_ids", []) or []))
    instance.setdefault("identity_status", "active")
    instance.setdefault("enabled", instance["identity_status"] == "active")
    instance.setdefault("capabilities", [])
    instance.setdefault("provenance", {})
    return instance


def _semantic_changes(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    if before is None:
        return sorted(after)
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def apply_profile_payload(current: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    action = str(payload.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(sorted(ALLOWED_ACTIONS))}")
    updated = deepcopy(current)
    updated.setdefault("version", REGISTRY_VERSION)
    updated.setdefault("profiles", [])
    agent_id = str(payload.get("agent_id") or "custom_agents").strip()
    if not agent_id:
        raise ValueError("agent_id is required")

    if action == "upsert":
        proposed = _normalize_instance(payload.get("instance"))
        profile, existing = _find_instance(updated, proposed["agent_instance"])
        target_profile = profile or _ensure_profile(updated, agent_id)
        changes = _semantic_changes(existing, proposed)
        if existing is None:
            target_profile["instances"].append(proposed)
        else:
            existing.clear()
            existing.update(proposed)
        target_id = proposed["agent_instance"]
    else:
        target_id = str(payload.get("agent_instance") or "").strip()
        profile, existing = _find_instance(updated, target_id)
        if existing is None:
            raise ValueError(f"custom agent_instance not found: {target_id}")
        if action == "deactivate":
            before = deepcopy(existing)
            existing["identity_status"] = "inactive"
            existing["enabled"] = False
            changes = _semantic_changes(before, existing)
        else:
            proposed = _normalize_instance(payload.get("replacement"))
            if proposed["agent_instance"] == target_id:
                raise ValueError("supersession replacement must use a new canonical agent_instance")
            before = deepcopy(existing)
            existing["identity_status"] = "superseded"
            existing["enabled"] = False
            proposed["supersedes_instance_ids"] = list(
                dict.fromkeys([*(proposed.get("supersedes_instance_ids") or []), target_id])
            )
            _ensure_profile(updated, agent_id)["instances"].append(proposed)
            changes = sorted(set(_semantic_changes(before, existing) + ["supersedes_instance_ids"]))

    owner_visible = sorted(set(changes) & OWNER_VISIBLE_FIELDS)
    return updated, {
        "action": action,
        "agent_instance": target_id,
        "changed_fields": changes,
        "owner_visible_fields": owner_visible,
    }


def _yaml_text(data: dict[str, Any]) -> str:
    if yaml is None:
        raise ValueError("PyYAML is required to write custom agent profiles")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _bundled_collision_reasons(updated: dict[str, Any]) -> list[str]:
    product_root = Path(__file__).resolve().parents[2]
    bundled = load_agent_profiles(product_root)
    bundled_ids = set(bundled.get("instance_index", {}))
    bundled_legacy_ids = set(bundled.get("legacy_instance_index", {}))
    custom_ids = {
        str(instance.get("agent_instance"))
        for profile in updated.get("profiles", [])
        if isinstance(profile, dict)
        for instance in profile.get("instances", []) or []
        if isinstance(instance, dict) and instance.get("agent_instance")
    }
    custom_legacy_ids = {
        str(legacy_id)
        for profile in updated.get("profiles", [])
        if isinstance(profile, dict)
        for instance in profile.get("instances", []) or []
        if isinstance(instance, dict)
        for legacy_id in instance.get("legacy_instance_ids", []) or []
        if str(legacy_id or "").strip()
    }
    reasons = [
        f"custom canonical agent_instance conflicts with bundled default: {instance_id}"
        for instance_id in sorted(custom_ids & bundled_ids)
    ]
    reasons.extend(
        f"custom canonical agent_instance conflicts with bundled legacy ID: {instance_id}"
        for instance_id in sorted(custom_ids & bundled_legacy_ids)
    )
    reasons.extend(
        f"custom legacy agent_instance conflicts with bundled default: {instance_id}"
        for instance_id in sorted(custom_legacy_ids & bundled_ids)
    )
    reasons.extend(
        f"custom legacy agent_instance conflicts with bundled legacy ID: {instance_id}"
        for instance_id in sorted(custom_legacy_ids & bundled_legacy_ids)
    )
    return reasons


def build_profile_write_plan(repo_root: Path, payload: dict[str, Any], *, actor: str, dry_run: bool) -> dict[str, Any]:
    current, parse_blocking = load_custom_registry(repo_root)
    blocking = list(parse_blocking)
    warnings: list[str] = []
    needs_owner: list[str] = []
    updated: dict[str, Any] | None = None
    change_summary: dict[str, Any] = {}
    try:
        updated, change_summary = apply_profile_payload(current, payload)
        validation = validate_custom_registry_data(updated)
        blocking.extend(validation["blocking_reasons"])
        blocking.extend(_bundled_collision_reasons(updated))
        warnings.extend(validation["warnings"])
        if change_summary.get("owner_visible_fields"):
            needs_owner.append(
                "Owner confirmation required for profile fields: "
                + ", ".join(change_summary["owner_visible_fields"])
            )
        needs_owner.append("Owner confirmation required before workspace-local custom agent registry write")
        content = _yaml_text(updated)
    except (OSError, ValueError) as exc:
        blocking.append(str(exc))
        content = ""
    verdict = derive_verdict(blocking_reasons=blocking, warnings=warnings, needs_owner_reasons=needs_owner)
    path = registry_path(repo_root)
    data = {
        "target_path": REGISTRY_RELATIVE_PATH.as_posix(),
        "target_file_state": _registry_file_state(path),
        "original_payload": payload,
        "change_summary": change_summary,
        "proposed_registry": updated,
    }
    return make_response(
        ok=not blocking,
        verdict=verdict,
        operation=PROFILE_OPERATION,
        dry_run=dry_run,
        actor=_actor_payload(actor),
        data=data,
        summary=change_summary,
        planned_writes=[{"path": REGISTRY_RELATIVE_PATH.as_posix(), "kind": "file", "type": "custom_agent_profile_registry"}] if not blocking else [],
        warnings=warnings,
        blocking_reasons=blocking,
        needs_owner_reasons=needs_owner if not blocking else [],
        owner_confirmation_required=not blocking,
        owner_confirmation_reasons=needs_owner if not blocking else [],
        execute_allowed=not blocking if dry_run else None,
        execute_blocking_reasons=blocking if dry_run else [],
        safety_notice=SAFETY_NOTICE,
    )


def build_profile_draft(repo_root: Path, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    result = build_profile_write_plan(repo_root, payload, actor=actor, dry_run=True)
    if result.get("blocking_reasons"):
        return result
    now = datetime.now(timezone.utc)
    result.update(
        {
            "dry_run_id": f"dryrun_{uuid.uuid4().hex}",
            "dry_run_snapshot_hash": snapshot_hash(PROFILE_OPERATION, actor, result),
            "dry_run_created_at": _iso_z(now),
            "dry_run_expires_at": _iso_z(now + timedelta(minutes=10)),
        }
    )
    result["dry_run_token"] = result["dry_run_id"]
    return result


def confirm_profile_draft(
    repo_root: Path,
    envelope: dict[str, Any],
    *,
    actor: str,
    owner_confirmation_token: str | None,
) -> dict[str, Any]:
    if envelope.get("operation") != PROFILE_OPERATION:
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="UNSUPPORTED_OPERATION", message="profile confirm requires a custom_agent_profile_write draft", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    if (envelope.get("actor") or {}).get("actor") != actor:
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="ACTOR_MISMATCH", message="confirm actor does not match profile draft actor", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    if _is_expired(envelope.get("dry_run_expires_at")):
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="profile draft expired; run draft again", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    if owner_confirmation_token != OWNER_CONFIRMATION_TOKEN:
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="OWNER_CONFIRMATION_REQUIRED", message="owner confirmation token is required and must equal OWNER_CONFIRMED", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    payload = data.get("original_payload")
    if not isinstance(payload, dict):
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="profile draft is missing data.original_payload", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    rebuilt = build_profile_write_plan(repo_root, payload, actor=actor, dry_run=True)
    if rebuilt.get("blocking_reasons"):
        return rebuilt
    if snapshot_hash(PROFILE_OPERATION, actor, rebuilt) != envelope.get("dry_run_snapshot_hash"):
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="profile draft snapshot mismatch; run draft again", actor=_actor_payload(actor), safety_notice=SAFETY_NOTICE)
    proposed = rebuilt["data"]["proposed_registry"]
    target = registry_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_yaml_text(proposed), encoding="utf-8")
    revalidated = validate_custom_registry(repo_root)
    if not revalidated["ok"]:
        return blocked_response(operation=PROFILE_OPERATION, dry_run=False, category="REVALIDATION_FAILED", message="written custom agent registry failed revalidation", actor=_actor_payload(actor), data={"registry_validation": revalidated}, safety_notice=SAFETY_NOTICE)
    rebuilt.update(
        {
            "dry_run": False,
            "verdict": "PASS",
            "performed_writes": rebuilt["planned_writes"],
            "execute_allowed": None,
            "data": {**rebuilt["data"], "registry_validation": revalidated, "wrote": True},
        }
    )
    return rebuilt
