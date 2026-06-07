from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

ALLOWED_AVAILABILITY_STATUSES = {"online", "offline", "busy", "maintenance", "unknown"}
INSTANCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PROVENANCE_FIELDS = ("vendor", "harness", "model_family", "host")
CUSTOM_REGISTRY_RELATIVE_PATH = Path("0_control_plane/agents/custom_agent_profiles.yaml")
INDEPENDENCE_DIMENSIONS = {
    "distinct_runtime_profile": ("runtime_profile",),
    "distinct_harness": ("provenance", "harness"),
    "distinct_model_family": ("provenance", "model_family"),
    "distinct_vendor": ("provenance", "vendor"),
    "distinct_host": ("provenance", "host"),
}

FALLBACK_PROFILE = {
    "agent_id": "dev_claude",
    "display_name": "Dev Claude",
    "description": "Fallback runtime profile for alias-aware CLI matching.",
    "enabled": True,
    "availability_status": "online",
    "aliases": [
        "dev_claude",
        "dev.claude.local",
        "dev.claude.cc.local",
        "dev.claude.cc_glm.local",
        "dev.claude.command.local",
    ],
    "instances": [
        {
            "agent_instance": "agent-01",
            "legacy_instance_ids": ["dev.claude.cc.local"],
            "provenance": {
                "vendor": "anthropic",
                "harness": "claude-code",
                "model_family": "claude",
                "host": "local",
            },
            "runtime_profile": "cc",
            "runtime_entrypoint": "claude_code",
            "runtime_command": "cc",
            "runtime_args": [],
            "runtime_env": {},
            "launch_notes": "Standard Claude Code entrypoint.",
            "default_task_modes": ["coding", "code_reviewer"],
            "enabled": True,
            "availability_status": "online",
        },
        {
            "agent_instance": "agent-02",
            "legacy_instance_ids": ["dev.claude.cc_glm.local"],
            "provenance": {
                "vendor": "anthropic",
                "harness": "claude-code",
                "model_family": "glm",
                "host": "local",
            },
            "runtime_profile": "cc_glm",
            "runtime_entrypoint": "claude_code",
            "runtime_command": "cc",
            "runtime_args": ["glm"],
            "runtime_env": {},
            "launch_notes": (
                "Claude Code-compatible entrypoint using GLM profile or relay. "
                "Parameters are configurable and may change."
            ),
            "default_task_modes": ["code_reviewer", "auditor"],
            "enabled": True,
            "availability_status": "unknown",
        },
        {
            "agent_instance": "agent-03",
            "legacy_instance_ids": ["dev.claude.command.local"],
            "provenance": {
                "vendor": "anthropic",
                "harness": "claude-command",
                "model_family": "claude",
                "host": "local",
            },
            "runtime_profile": "claude_command",
            "runtime_entrypoint": "claude_cli",
            "runtime_command": "claude",
            "runtime_args": [],
            "runtime_env": {},
            "launch_notes": "Direct claude command entrypoint.",
            "default_task_modes": ["code_reviewer"],
            "enabled": True,
            "availability_status": "unknown",
        },
    ],
    "runtime_profiles": ["cc", "cc_glm", "claude_command"],
    "default_instance": "agent-01",
    "default_runtime_profile": "cc",
    "allowed_task_modes": ["coding", "code_reviewer", "auditor"],
    "preferred_task_modes": ["coding", "code_reviewer"],
    "preferred_model_tier": "L2",
    "runtime_entrypoint": "claude_code",
    "runtime_command": "cc",
    "runtime_args": [],
    "runtime_env": {},
    "launch_notes": "Fallback declarative runtime configuration. Never executed by the CLI.",
    "environment": "local_wsl_ubuntu",
    "workspace": "shared_repo_workspace",
    "notes": "Configuration defaults only, not locked roles.",
}


def normalize_availability_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ALLOWED_AVAILABILITY_STATUSES:
        return text
    return "unknown"


def _availability_warning(status: str, scope: str) -> str | None:
    if status == "online":
        return None
    if status == "offline":
        return f"{scope} is offline"
    if status == "busy":
        return f"{scope} is busy"
    if status == "maintenance":
        return f"{scope} is in maintenance"
    return f"{scope} availability is unknown because Lybra does not track live agent presence or heartbeat state; this is expected for a gate-not-engine runtime."


def _profile_copy(profile: dict[str, Any], source: str, source_path: str) -> dict[str, Any]:
    copied = dict(profile)
    warnings: list[str] = []
    copied.setdefault("agent_id", "")
    copied.setdefault("display_name", copied.get("agent_id") or "Unknown Agent")
    copied.setdefault("description", "")
    copied.setdefault("enabled", True)
    raw_profile_status = copied.get("availability_status")
    copied["availability_status"] = normalize_availability_status(raw_profile_status)
    if raw_profile_status not in (None, "") and copied["availability_status"] == "unknown":
        raw_text = str(raw_profile_status).strip().lower()
        if raw_text not in ALLOWED_AVAILABILITY_STATUSES:
            warnings.append(
                f"Invalid agent availability_status normalized to unknown: {raw_profile_status}"
            )
    copied["aliases"] = list(dict.fromkeys(copied.get("aliases", []) or []))
    copied["instances"] = [dict(instance) for instance in copied.get("instances", []) or []]
    copied.setdefault("runtime_profiles", [])
    copied.setdefault("default_instance", None)
    copied.setdefault("default_runtime_profile", None)
    copied.setdefault("allowed_task_modes", [])
    copied.setdefault("preferred_task_modes", [])
    copied.setdefault("preferred_model_tier", None)
    copied.setdefault("runtime_entrypoint", None)
    copied.setdefault("runtime_command", None)
    copied.setdefault("runtime_args", [])
    copied.setdefault("runtime_env", {})
    copied.setdefault("launch_notes", "")
    copied.setdefault("environment", None)
    copied.setdefault("workspace", None)
    copied.setdefault("notes", "")
    copied["warnings"] = warnings
    copied["source"] = source
    copied["source_path"] = source_path
    if copied["agent_id"] and copied["agent_id"] not in copied["aliases"]:
        copied["aliases"].insert(0, copied["agent_id"])
    runtime_profiles = list(copied.get("runtime_profiles", []) or [])
    for instance in copied["instances"]:
        instance.setdefault("agent_instance", None)
        instance.setdefault("display_name", instance.get("agent_instance") or "")
        instance["legacy_instance_ids"] = list(dict.fromkeys(instance.get("legacy_instance_ids", []) or []))
        instance["supersedes_instance_ids"] = list(dict.fromkeys(instance.get("supersedes_instance_ids", []) or []))
        instance.setdefault("identity_status", "active")
        provenance = instance.get("provenance")
        instance["provenance"] = dict(provenance) if isinstance(provenance, dict) else {}
        instance.setdefault("runtime_profile", None)
        instance.setdefault("runtime_entrypoint", None)
        instance.setdefault("runtime_command", None)
        instance.setdefault("runtime_args", [])
        instance.setdefault("runtime_env", {})
        instance.setdefault("launch_notes", "")
        instance.setdefault("default_task_modes", [])
        instance.setdefault("enabled", True)
        raw_instance_status = instance.get("availability_status")
        instance["availability_status"] = normalize_availability_status(raw_instance_status)
        if raw_instance_status not in (None, "") and instance["availability_status"] == "unknown":
            raw_text = str(raw_instance_status).strip().lower()
            if raw_text not in ALLOWED_AVAILABILITY_STATUSES:
                warnings.append(
                    "Invalid instance availability_status normalized to unknown: "
                    f"{instance.get('agent_instance') or '<unknown-instance>'}={raw_instance_status}"
                )
        if instance.get("runtime_profile") and instance["runtime_profile"] not in runtime_profiles:
            runtime_profiles.append(instance["runtime_profile"])
    copied["runtime_profiles"] = runtime_profiles
    return copied


def _load_profiles_from_registry(repo_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = repo_root / CUSTOM_REGISTRY_RELATIVE_PATH
    if yaml is None or not path.exists():
        return [], []
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [], [f"Custom agent registry parse failed: {exc}"]
    if not isinstance(parsed, dict) or not isinstance(parsed.get("profiles"), list):
        return [], ["Custom agent registry must contain a profiles list"]
    profiles: list[dict[str, Any]] = []
    for profile in parsed["profiles"]:
        if isinstance(profile, dict) and profile.get("agent_id"):
            profiles.append(
                _profile_copy(
                    profile,
                    source="workspace_registry",
                    source_path=CUSTOM_REGISTRY_RELATIVE_PATH.as_posix(),
                )
            )
    return profiles, []


def _extract_yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\s*\n(.*?)\n```", text, flags=re.DOTALL)


def _load_profiles_from_docs(docs_root: Path, repo_root: Path) -> list[dict[str, Any]]:
    if yaml is None or not docs_root.exists():
        return []

    profiles: list[dict[str, Any]] = []
    for path in sorted(docs_root.glob("*_runtime_profiles.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for block in _extract_yaml_blocks(text):
            try:
                parsed = yaml.safe_load(block) or {}
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("agent_id"):
                profiles.append(_profile_copy(parsed, source="docs", source_path=str(path.relative_to(repo_root))))
    return profiles


def load_agent_profiles(repo_root: Path) -> dict[str, Any]:
    docs_root = repo_root / "0_control_plane" / "agents"
    custom_profiles, registry_warnings = _load_profiles_from_registry(repo_root)
    docs_profiles = _load_profiles_from_docs(docs_root, repo_root)
    product_root = Path(__file__).resolve().parents[2]
    if not docs_profiles and repo_root.resolve() != product_root.resolve():
        docs_profiles = _load_profiles_from_docs(product_root / "0_control_plane" / "agents", product_root)
    profiles = [*custom_profiles, *docs_profiles]
    if not profiles:
        profiles = [_profile_copy(FALLBACK_PROFILE, source="fallback", source_path="fallback:dev_claude")]

    agents_by_id: dict[str, dict[str, Any]] = {}
    duplicate_agent_ids: set[str] = set()
    for profile in profiles:
        agent_id = str(profile.get("agent_id") or "")
        if not agent_id:
            continue
        if agent_id in agents_by_id:
            duplicate_agent_ids.add(agent_id)
            profile["warnings"].append(f"Duplicate agent_id ignored: {agent_id}")
            agents_by_id[agent_id]["warnings"].append(f"Duplicate agent_id ignored: {agent_id}")
            continue
        agents_by_id[agent_id] = profile
    alias_index: dict[str, str] = {}
    instance_index: dict[str, dict[str, Any]] = {}
    legacy_instance_index: dict[str, list[dict[str, Any]]] = {}
    seen_instance_ids: set[str] = set()
    duplicate_instance_ids: set[str] = set()
    for profile in profiles:
        if not profile.get("enabled", True):
            continue
        agent_id = str(profile.get("agent_id") or "")
        if agent_id in duplicate_agent_ids:
            continue
        for alias in profile.get("aliases", []):
            alias_index[str(alias)] = agent_id
        for instance in profile.get("instances", []):
            if instance.get("enabled", True) and instance.get("agent_instance"):
                instance_id = str(instance["agent_instance"])
                if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
                    profile["warnings"].append(f"Invalid canonical agent_instance ignored: {instance_id}")
                    continue
                if instance_id in seen_instance_ids:
                    profile["warnings"].append(f"Duplicate canonical agent_instance ignored: {instance_id}")
                    duplicate_instance_ids.add(instance_id)
                    instance_index.pop(instance_id, None)
                    continue
                seen_instance_ids.add(instance_id)
                instance_index[instance_id] = {"agent_id": agent_id, "instance": instance}
                for legacy_id in instance.get("legacy_instance_ids", []):
                    legacy_text = str(legacy_id or "").strip()
                    if legacy_text:
                        legacy_instance_index.setdefault(legacy_text, []).append(
                            {"agent_id": agent_id, "instance": instance}
                        )

    for instance_id in duplicate_instance_ids:
        instance_index.pop(instance_id, None)

    ambiguous_legacy_instance_ids = sorted(
        legacy_id for legacy_id, items in legacy_instance_index.items() if len(items) != 1
    )
    for legacy_id in ambiguous_legacy_instance_ids:
        for item in legacy_instance_index[legacy_id]:
            profile = agents_by_id.get(item["agent_id"])
            if profile is not None:
                profile["warnings"].append(f"Ambiguous legacy agent_instance mapping: {legacy_id}")

    return {
        "scope": "agents",
        "profiles": profiles,
        "summary": {
            "profiles": len(profiles),
            "enabled_profiles": sum(1 for profile in profiles if profile.get("enabled", True)),
            "instances": sum(len(profile.get("instances", [])) for profile in profiles),
            "source": (
                "workspace_registry+docs"
                if custom_profiles and any(profile.get("source") == "docs" for profile in profiles)
                else "workspace_registry"
                if custom_profiles
                else "docs"
                if any(profile.get("source") == "docs" for profile in profiles)
                else "fallback"
            ),
            "warnings": len(registry_warnings) + sum(len(profile.get("warnings", [])) for profile in profiles),
        },
        "agents_by_id": agents_by_id,
        "alias_index": alias_index,
        "instance_index": instance_index,
        "legacy_instance_index": legacy_instance_index,
        "ambiguous_legacy_instance_ids": ambiguous_legacy_instance_ids,
        "registry_warnings": registry_warnings,
    }


def resolve_instance_id(instance_id: str | None, profiles: dict[str, Any]) -> dict[str, Any]:
    value = str(instance_id or "").strip()
    if not value:
        return {"input_instance_id": None, "canonical_instance_id": None, "resolution": "missing"}
    if value in profiles.get("instance_index", {}):
        return {"input_instance_id": value, "canonical_instance_id": value, "resolution": "canonical"}
    legacy_matches = profiles.get("legacy_instance_index", {}).get(value, [])
    if len(legacy_matches) == 1:
        canonical = str(legacy_matches[0]["instance"].get("agent_instance") or "")
        return {"input_instance_id": value, "canonical_instance_id": canonical, "resolution": "legacy"}
    if len(legacy_matches) > 1:
        return {"input_instance_id": value, "canonical_instance_id": None, "resolution": "ambiguous"}
    return {"input_instance_id": value, "canonical_instance_id": value, "resolution": "unregistered"}


def _resolved_instance_config(instance_id: str | None, profiles: dict[str, Any]) -> dict[str, Any] | None:
    resolved = resolve_instance_id(instance_id, profiles)
    canonical = resolved.get("canonical_instance_id")
    if not canonical or resolved.get("resolution") == "ambiguous":
        return None
    return profiles.get("instance_index", {}).get(str(canonical), {}).get("instance")


def _nested_value(data: dict[str, Any], path: tuple[str, ...]) -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "").strip()


def evaluate_instance_independence(
    executor_instance_id: str | None,
    auditor_instance_id: str | None,
    profiles: dict[str, Any],
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested = {"distinct_instance": True, **(requirements or {})}
    executor = resolve_instance_id(executor_instance_id, profiles)
    auditor = resolve_instance_id(auditor_instance_id, profiles)
    checks: dict[str, dict[str, Any]] = {}
    blocking_reasons: list[str] = []

    if executor["resolution"] == "ambiguous" or auditor["resolution"] == "ambiguous":
        blocking_reasons.append("Independent instance resolution is ambiguous")
    executor_canonical = executor.get("canonical_instance_id")
    auditor_canonical = auditor.get("canonical_instance_id")
    distinct_instance = bool(executor_canonical and auditor_canonical and executor_canonical != auditor_canonical)
    checks["distinct_instance"] = {
        "required": bool(requested.get("distinct_instance", True)),
        "matched": distinct_instance,
        "executor": executor_canonical,
        "auditor": auditor_canonical,
    }
    if requested.get("distinct_instance", True) and not distinct_instance:
        blocking_reasons.append("Independent auditor requires a distinct canonical instance")

    executor_config = _resolved_instance_config(executor_instance_id, profiles)
    auditor_config = _resolved_instance_config(auditor_instance_id, profiles)
    for dimension, path in INDEPENDENCE_DIMENSIONS.items():
        if not requested.get(dimension, False):
            continue
        executor_value = _nested_value(executor_config or {}, path)
        auditor_value = _nested_value(auditor_config or {}, path)
        known = bool(
            executor_value
            and auditor_value
            and executor_value.lower() != "unknown"
            and auditor_value.lower() != "unknown"
        )
        matched = known and executor_value != auditor_value
        checks[dimension] = {
            "required": True,
            "matched": matched,
            "executor": executor_value or None,
            "auditor": auditor_value or None,
        }
        if not matched:
            blocking_reasons.append(f"Independent auditor requirement failed: {dimension}")

    return {
        "matched": not blocking_reasons,
        "executor": executor,
        "auditor": auditor,
        "checks": checks,
        "blocking_reasons": blocking_reasons,
    }


def canonical_agent(actor: str | None, profiles: dict[str, Any]) -> str:
    if not actor:
        return ""
    actor_text = str(actor)
    if actor_text in profiles.get("alias_index", {}):
        return str(profiles["alias_index"][actor_text])
    if actor_text in profiles.get("instance_index", {}):
        return str(profiles["instance_index"][actor_text]["agent_id"])
    legacy_matches = profiles.get("legacy_instance_index", {}).get(actor_text, [])
    if len(legacy_matches) == 1:
        return str(legacy_matches[0]["agent_id"])
    return actor_text


def actor_aliases(actor: str | None, profiles: dict[str, Any]) -> set[str]:
    if not actor:
        return set()
    actor_text = str(actor)
    aliases = {actor_text}
    canonical = canonical_agent(actor_text, profiles)
    aliases.add(canonical)
    profile = profiles.get("agents_by_id", {}).get(canonical)
    if profile and profile.get("enabled", True):
        aliases.update(str(alias) for alias in profile.get("aliases", []))
        aliases.update(
            str(instance.get("agent_instance"))
            for instance in profile.get("instances", [])
            if instance.get("enabled", True) and instance.get("agent_instance")
        )
        for instance in profile.get("instances", []):
            if instance.get("enabled", True):
                aliases.update(str(item) for item in instance.get("legacy_instance_ids", []) if item)
    return aliases


def actor_matches_task_actor(actor: str | None, task_value: str | None, profiles: dict[str, Any]) -> bool:
    if not actor or not task_value:
        return False
    actor_text = str(actor)
    task_text = str(task_value)
    if actor_text == task_text:
        return True
    return bool(actor_aliases(actor_text, profiles) & actor_aliases(task_text, profiles))


def actor_matches_task(task: dict[str, Any], actor: str | None, profiles: dict[str, Any]) -> bool:
    metadata = task.get("metadata", {})
    return any(
        actor_matches_task_actor(actor, value, profiles)
        for value in (
            task.get("assigned_to"),
            task.get("agent_instance"),
            task.get("claimed_by"),
            metadata.get("claimed_by"),
        )
    )


def required_specific_instance(metadata: dict[str, Any]) -> str:
    owner_override = metadata.get("owner_override")
    if isinstance(owner_override, dict) and str(owner_override.get("allowed_agent_instance") or "").strip():
        return str(owner_override.get("allowed_agent_instance")).strip()
    if str(metadata.get("agent_instance") or "").strip():
        return str(metadata.get("agent_instance")).strip()
    requirements = metadata.get("requirements")
    if isinstance(requirements, dict) and str(requirements.get("preferred_agent_instance") or "").strip():
        return str(requirements.get("preferred_agent_instance")).strip()
    return ""


def explicit_claimant_instance(actor: str | None, required_instance: str, profiles: dict[str, Any]) -> str:
    actor_text = str(actor or "").strip()
    if not actor_text:
        return ""
    resolved = resolve_instance_id(actor_text, profiles)
    if resolved.get("resolution") == "ambiguous":
        return ""
    if actor_text == required_instance or resolved.get("canonical_instance_id"):
        return str(resolved.get("canonical_instance_id") or actor_text)
    return ""


def specific_instance_match_details(
    metadata: dict[str, Any],
    actor: str | None,
    profiles: dict[str, Any],
) -> dict[str, Any]:
    required_instance = required_specific_instance(metadata)
    required_resolution = resolve_instance_id(required_instance, profiles)
    claimant_resolution = resolve_instance_id(actor, profiles)
    required_canonical = required_resolution.get("canonical_instance_id")
    claimant_instance = explicit_claimant_instance(actor, required_instance, profiles)
    if not required_instance:
        result = "missing_required"
    elif required_resolution.get("resolution") == "ambiguous" or claimant_resolution.get("resolution") == "ambiguous":
        result = "ambiguous"
    elif not claimant_instance:
        result = "missing_claimant"
    elif claimant_instance == required_canonical:
        result = "exact"
    else:
        result = "mismatch"
    return {
        "matched": result == "exact",
        "reason": "specific_instance_only",
        "required_instance_id": required_instance or None,
        "required_canonical_instance_id": required_canonical,
        "claimant_instance_id": str(actor or "").strip() or None,
        "claimant_canonical_instance_id": claimant_resolution.get("canonical_instance_id"),
        "instance_match_policy": "exact",
        "instance_match_result": result,
    }


def actor_match_details(task: dict[str, Any], actor: str | None, profiles: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata", {})
    assigned_to = task.get("assigned_to") or metadata.get("assigned_to")
    agent_instance = task.get("agent_instance") or metadata.get("agent_instance")
    claimed_by = task.get("claimed_by") or metadata.get("claimed_by")
    actor_text = str(actor or "")
    actor_canonical = canonical_agent(actor_text, profiles) if actor_text else ""
    assigned_canonical = canonical_agent(str(assigned_to or ""), profiles) if assigned_to else ""
    aliases = sorted(actor_aliases(actor_text, profiles)) if actor_text else []

    checks = [
        ("direct_assigned_to", assigned_to, actor_text == str(assigned_to or "")),
        ("direct_agent_instance", agent_instance, actor_text == str(agent_instance or "")),
        ("direct_claimed_by", claimed_by, actor_text == str(claimed_by or "")),
        (
            "alias_assigned_to",
            assigned_to,
            actor_matches_task_actor(actor_text, str(assigned_to or ""), profiles) if assigned_to else False,
        ),
        (
            "alias_agent_instance",
            agent_instance,
            actor_matches_task_actor(actor_text, str(agent_instance or ""), profiles) if agent_instance else False,
        ),
        (
            "alias_claimed_by",
            claimed_by,
            actor_matches_task_actor(actor_text, str(claimed_by or ""), profiles) if claimed_by else False,
        ),
    ]
    for reason, matched_value, matched in checks:
        if matched:
            return {
                "matched": True,
                "reason": reason,
                "matched_value": matched_value,
                "actor_canonical_agent": actor_canonical or actor_text,
                "task_assigned_to_canonical": assigned_canonical or assigned_to,
                "actor_aliases": aliases,
            }
    return {
        "matched": False,
        "reason": "no_match",
        "matched_value": None,
        "actor_canonical_agent": actor_canonical or actor_text,
        "task_assigned_to_canonical": assigned_canonical or assigned_to,
        "actor_aliases": aliases,
    }


def runtime_config_for_actor(actor: str | None, profiles: dict[str, Any]) -> dict[str, Any]:
    actor_text = str(actor or "")
    canonical = canonical_agent(actor_text, profiles) if actor_text else ""
    profile = profiles.get("agents_by_id", {}).get(canonical)
    if not profile:
        return {
            "agent_id": canonical or actor_text or None,
            "display_name": None,
            "actor_profile_matched": False,
            "actor_agent_availability_status": "unknown",
            "actor_instance_availability_status": "unknown",
            "actor_availability_status": "unknown",
            "availability_warning": _availability_warning("unknown", "actor"),
            "runtime_profile": None,
            "runtime_entrypoint": None,
            "runtime_command": None,
            "runtime_args": [],
            "runtime_env": {},
            "launch_notes": "",
            "source": "none",
        }

    matched_instance = None
    resolved_actor_instance = resolve_instance_id(actor_text, profiles).get("canonical_instance_id")
    for instance in profile.get("instances", []):
        if resolved_actor_instance and resolved_actor_instance == str(instance.get("agent_instance") or ""):
            matched_instance = instance
            break
    if matched_instance is None and profile.get("default_instance"):
        for instance in profile.get("instances", []):
            if instance.get("agent_instance") == profile.get("default_instance"):
                matched_instance = instance
                break
    if matched_instance is None and profile.get("instances"):
        matched_instance = profile["instances"][0]

    agent_status = normalize_availability_status(profile.get("availability_status"))
    instance_status = normalize_availability_status(
        matched_instance.get("availability_status") if matched_instance else None
    )
    effective_status = instance_status if matched_instance else agent_status
    warning = _availability_warning(
        effective_status,
        f"actor {matched_instance.get('agent_instance') if matched_instance else canonical or actor_text}",
    )

    return {
        "agent_id": profile.get("agent_id"),
        "display_name": profile.get("display_name"),
        "description": profile.get("description"),
        "enabled": profile.get("enabled"),
        "aliases": list(profile.get("aliases", [])),
        "instances": list(profile.get("instances", [])),
        "default_instance": profile.get("default_instance"),
        "default_runtime_profile": profile.get("default_runtime_profile"),
        "preferred_model_tier": profile.get("preferred_model_tier"),
        "availability_status": agent_status,
        "actor_agent_availability_status": agent_status,
        "actor_instance_availability_status": instance_status,
        "actor_availability_status": effective_status,
        "availability_warning": warning,
        "runtime_profile": matched_instance.get("runtime_profile") if matched_instance else profile.get("default_runtime_profile"),
        "runtime_entrypoint": matched_instance.get("runtime_entrypoint") if matched_instance else profile.get("runtime_entrypoint"),
        "runtime_command": matched_instance.get("runtime_command") if matched_instance else profile.get("runtime_command"),
        "runtime_args": list(matched_instance.get("runtime_args", [])) if matched_instance else list(profile.get("runtime_args", [])),
        "runtime_env": dict(matched_instance.get("runtime_env", {})) if matched_instance else dict(profile.get("runtime_env", {})),
        "launch_notes": matched_instance.get("launch_notes") if matched_instance else profile.get("launch_notes"),
        "matched_instance": matched_instance.get("agent_instance") if matched_instance else None,
        "source": profile.get("source"),
        "source_path": profile.get("source_path"),
        "actor_profile_matched": True,
    }


def availability_for_actor(actor: str | None, profiles: dict[str, Any]) -> dict[str, Any]:
    runtime = runtime_config_for_actor(actor, profiles)
    return {
        "actor_availability_status": runtime.get("actor_availability_status", "unknown"),
        "actor_instance_availability_status": runtime.get("actor_instance_availability_status", "unknown"),
        "actor_agent_availability_status": runtime.get("actor_agent_availability_status", "unknown"),
        "availability_warning": runtime.get("availability_warning"),
    }


def availability_warning_for_actor(actor: str | None, profiles: dict[str, Any]) -> str | None:
    return availability_for_actor(actor, profiles).get("availability_warning")
