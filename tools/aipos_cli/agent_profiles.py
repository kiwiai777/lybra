from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

ALLOWED_AVAILABILITY_STATUSES = {"online", "offline", "busy", "maintenance", "unknown"}

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
            "agent_instance": "dev.claude.cc.local",
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
            "agent_instance": "dev.claude.cc_glm.local",
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
            "agent_instance": "dev.claude.command.local",
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
    "default_instance": "dev.claude.cc.local",
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
    return f"{scope} availability is unknown"


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
    profiles = _load_profiles_from_docs(docs_root, repo_root)
    if not profiles:
        profiles = [_profile_copy(FALLBACK_PROFILE, source="fallback", source_path="fallback:dev_claude")]

    agents_by_id = {profile["agent_id"]: profile for profile in profiles if profile.get("agent_id")}
    alias_index: dict[str, str] = {}
    instance_index: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        if not profile.get("enabled", True):
            continue
        agent_id = str(profile.get("agent_id") or "")
        for alias in profile.get("aliases", []):
            alias_index[str(alias)] = agent_id
        for instance in profile.get("instances", []):
            if instance.get("enabled", True) and instance.get("agent_instance"):
                instance_index[str(instance["agent_instance"])] = {"agent_id": agent_id, "instance": instance}

    return {
        "scope": "agents",
        "profiles": profiles,
        "summary": {
            "profiles": len(profiles),
            "enabled_profiles": sum(1 for profile in profiles if profile.get("enabled", True)),
            "instances": sum(len(profile.get("instances", [])) for profile in profiles),
            "source": "docs" if any(profile.get("source") == "docs" for profile in profiles) else "fallback",
            "warnings": sum(len(profile.get("warnings", [])) for profile in profiles),
        },
        "agents_by_id": agents_by_id,
        "alias_index": alias_index,
        "instance_index": instance_index,
    }


def canonical_agent(actor: str | None, profiles: dict[str, Any]) -> str:
    if not actor:
        return ""
    actor_text = str(actor)
    if actor_text in profiles.get("alias_index", {}):
        return str(profiles["alias_index"][actor_text])
    if actor_text in profiles.get("instance_index", {}):
        return str(profiles["instance_index"][actor_text]["agent_id"])
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
            "availability_warning": "actor availability is unknown",
            "runtime_profile": None,
            "runtime_entrypoint": None,
            "runtime_command": None,
            "runtime_args": [],
            "runtime_env": {},
            "launch_notes": "",
            "source": "none",
        }

    matched_instance = None
    for instance in profile.get("instances", []):
        if actor_text and actor_text == str(instance.get("agent_instance") or ""):
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
