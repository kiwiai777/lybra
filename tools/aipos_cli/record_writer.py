from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.records import expected_claim_log_path, expected_closure_record_path, expected_return_record_path, expected_session_record_path
from tools.schema_loader import get_enum_values
from tools.schema_constants import RecordType, Verdict

# AIPOS-F22B: YAML 序列化器 (可选依赖, zerodep 核心使用 stdlib fallback)
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None  # noqa: F841

# Record type constants from enums.schema.json (single source)
# FND-47: 禁字面量漂移 - 所有 record_type 值从 enums.schema 读取
_RECORD_TYPE_ENUM: list[str] | None = None

def _get_record_type(name: str) -> str:
    """Get record_type value from enums.schema.json.
    
    Args:
        name: enum value name (e.g., 'audit_verdict', 'claim', 'return')
    
    Returns:
        The value from enums.schema (single source)
    
    Raises:
        ValueError: If the record type is not defined in enums.schema
    """
    global _RECORD_TYPE_ENUM
    if _RECORD_TYPE_ENUM is None:
        _RECORD_TYPE_ENUM = get_enum_values("record_type")
    if name not in _RECORD_TYPE_ENUM:
        raise ValueError(
            f"record_type '{name}' not found in enums.schema.json. "
            f"Available: {_RECORD_TYPE_ENUM}"
        )
    return name




RECORDS_ROOT = Path("5_tasks/records")
CLAIMS_ROOT = RECORDS_ROOT / "claims"
SESSIONS_ROOT = RECORDS_ROOT / "sessions"
RETURNS_ROOT = RECORDS_ROOT / "returns"
AUDIT_DISPATCHES_ROOT = RECORDS_ROOT / "audit_dispatches"
AUDIT_VERDICTS_ROOT = RECORDS_ROOT / "audit_verdicts"
CLOSURES_ROOT = RECORDS_ROOT / "closures"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return value


def actor_slug(actor: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", actor.lower()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if not value:
        raise ValueError(f"Actor cannot be converted to a safe slug: {actor}")
    return value


def validate_safe_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError(f"Unsafe task_id for records path: {task_id}")
    if task_id in {".", ".."} or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError(f"Unsafe task_id for records path: {task_id}")


def build_runtime_id(prefix: str, task_id: str, timestamp: str, actor: str) -> str:
    validate_safe_task_id(task_id)
    return f"{prefix}_{task_id}_{timestamp.replace('-', '').replace(':', '').replace('T', '_').replace('Z', '')}_{actor_slug(actor)}"


def _resolved_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def ensure_safe_record_path(repo_root: Path, path: Path, record_type: str, task_id: str) -> Path:
    validate_safe_task_id(task_id)
    if record_type == RecordType.CLAIM_LOG:
        root = (repo_root / CLAIMS_ROOT / task_id).resolve()
    elif record_type == RecordType.SESSION_RECORD:
        root = (repo_root / SESSIONS_ROOT / task_id).resolve()
    elif record_type == RecordType.RETURN_RECORD:
        root = (repo_root / RETURNS_ROOT / task_id).resolve()
    elif record_type == RecordType.AUDIT_DISPATCH_RECORD:
        root = (repo_root / AUDIT_DISPATCHES_ROOT / task_id).resolve()
    elif record_type == RecordType.AUDIT_VERDICT_RECORD:
        root = (repo_root / AUDIT_VERDICTS_ROOT / task_id).resolve()
    elif record_type == RecordType.CLOSURE_RECORD:
        root = (repo_root / CLOSURES_ROOT / task_id).resolve()
    else:
        raise ValueError(f"Unsupported record_type: {record_type}")
    resolved = path.resolve()
    if not _resolved_within(root, resolved):
        raise ValueError(f"Record path resolves outside allowed records root: {path}")
    if resolved.suffix.lower() != ".md":
        raise ValueError(f"Record path is not a markdown file: {path}")
    return resolved


def _stdlib_yaml_scalar(value: Any) -> str:
    """stdlib YAML scalar emitter — 使用 json.dumps 转义字符串 (AIPOS-F22B).

    json.dumps 输出的双引号字符串是合法的 YAML 双引号标量, 且被 zerodep 回退解析器支持.
    """
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    # 字符串: 使用 json.dumps 输出双引号 YAML 标量
    text = str(value)
    return json.dumps(text)


def _self_check_yaml(yaml_text: str, ordered_meta: dict[str, Any]) -> None:
    """AIPOS-F46 末道自检: 渲染后 safe_load 回读, 失败即报错拒写, 禁落坏卡.

    验证:
    1. safe_load 能解析(不抛异常)
    2. 解析结果是 dict
    3. 关键标量值 roundtrip 一致(字符串值不被类型误判)
    """
    try:
        if yaml is not None:
            parsed = yaml.safe_load(yaml_text)
        else:
            from tools.aipos_cli.frontmatter import _fallback_parse
            parsed, _warnings = _fallback_parse(yaml_text)
    except Exception as exc:
        raise ValueError(
            f"AIPOS-F46 self-check FAIL: rendered YAML is unparseable: {exc}. "
            f"This means a poison field (e.g. **bold**:colon\"quote) was not properly "
            f"escaped. Refusing to write bad card."
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"AIPOS-F46 self-check FAIL: rendered YAML parsed to {type(parsed).__name__}, "
            f"expected dict. Refusing to write bad card."
        )
    # Spot-check: string values must roundtrip as strings (not be coerced to bool/int/null)
    for key, original_value in ordered_meta.items():
        if isinstance(original_value, str) and original_value:
            parsed_value = parsed.get(key)
            if not isinstance(parsed_value, str):
                raise ValueError(
                    f"AIPOS-F46 self-check FAIL: field '{key}' was string {original_value!r} "
                    f"but roundtripped as {type(parsed_value).__name__} ({parsed_value!r}). "
                    f"Refusing to write bad card."
                )


def render_markdown(metadata: dict[str, Any], body: str, order: list[str] | None = None) -> str:
    """Render markdown with YAML frontmatter.

    AIPOS-F22B: frontmatter 一律经 YAML 序列化器 (safe_dump 或等价) 输出, 禁字符串拼接.
    使用 yaml.safe_dump (PyYAML 可用时) 或 stdlib fallback.

    AIPOS-F46: 末道自检——渲染后 safe_load 回读, 失败即报错拒写, 禁落坏卡.
    """
    ordered_keys = [key for key in (order or []) if key in metadata]
    ordered_keys.extend(sorted(key for key in metadata if key not in ordered_keys))

    # 构建保持插入顺序的 dict (Python 3.7+ dict 有序)
    ordered_meta: dict[str, Any] = {}
    for key in ordered_keys:
        ordered_meta[key] = _normalize_value(metadata[key])

    if yaml is not None:
        # 主路径: 使用 yaml.safe_dump
        yaml_text = yaml.safe_dump(
            ordered_meta,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=1000000,  # 禁换行
        )
        # safe_dump 输出末尾有换行, 去除后再拼接
        yaml_text = yaml_text.rstrip("\n")
        # AIPOS-F46 末道自检
        _self_check_yaml(yaml_text, ordered_meta)
        return f"---\n{yaml_text}\n---\n{body.rstrip()}\n"

    # stdlib fallback: 逐行构建 (列表/嵌套映射复用原有逻辑, 标量使用 _stdlib_yaml_scalar)
    lines = ["---"]
    for key, value in ordered_meta.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"- {_stdlib_yaml_scalar(item)}")
            continue
        if isinstance(value, dict):
            if not value:
                lines.append(f"{key}: " + "{}")
                continue
            lines.append(f"{key}:")
            for sub_key, sub_val in value.items():
                lines.append(f"  {sub_key}: {_stdlib_yaml_scalar(sub_val)}")
            continue
        lines.append(f"{key}: {_stdlib_yaml_scalar(value)}")
    # AIPOS-F46 末道自检 (stdlib fallback path)
    fallback_yaml_text = "\n".join(lines[1:-2])  # strip --- delimiters and body
    _self_check_yaml(fallback_yaml_text, ordered_meta)
    lines.extend(["---", body.rstrip(), ""])
    return "\n".join(lines)



CLAIM_FRONTMATTER_ORDER = [
    "record_type",
    "claim_id",
    "task_id",
    "task_path",
    "actor",
    "claim_action",
    "created_at",
    "from_state",
    "to_state",
    "session_id",
]

SESSION_FRONTMATTER_ORDER = [
    "record_type",
    "session_id",
    "task_id",
    "task_path",
    "actor",
    "created_at",
    "updated_at",
    "status",
    "claim_id",
    "current_state",
    "event_count",
]

MCP_CLAIM_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "claim_id",
    "task_id",
    "task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "actual_model",
    "reported_tokens",
    "claimed_at",
    "from_state",
    "to_state",
    "claim_policy",
    "claim_match_basis",
    "claim_requirements_hash",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "confirmer_role",
    "confirmer_token_ref",
    "confirmer_token_fingerprint",
    "gate_signature",
    "authority_seal",
    "signature_key_ref",
    "signed_payload_hash",
    "signed_at",
    "session_id",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_SESSION_FRONTMATTER_ORDER = [
    "record_type",
    "session_id",
    "task_id",
    "task_path",
    "surface",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "claim_id",
    "created_at",
    "updated_at",
    "session_status",
    "current_state",
    "lease_status",
    "lease_path",
    "active_lease_written",
    "event_count",
]

MCP_RETURN_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "return_id",
    "task_id",
    "task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "actual_model",
    "reported_tokens",
    "agent_runtime",
    "claim_id",
    "session_id",
    "returned_at",
    "executor_status",
    "audit_readiness",
    "dependency_executor_status",
    "dependency_audit_readiness",
    "dependency_audit_status",
    "result_summary_present",
    "artifact_refs",
    "completion_report_ref",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "confirmer_role",
    "confirmer_token_ref",
    "confirmer_token_fingerprint",
    "gate_signature",
    "authority_seal",
    "signature_key_ref",
    "signed_payload_hash",
    "signed_at",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_AUDIT_DISPATCH_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "dispatch_id",
    "reviewed_task_id",
    "reviewed_task_path",
    "reviewed_return_record_ref",
    "reviewed_executor_instance",
    "reviewed_executor_claim_id",
    "reviewed_executor_session_id",
    "audit_task_id",
    "audit_task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "dispatched_at",
    "independence_distinct_instance",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "dependency_executor_status",
    "dependency_audit_readiness",
    "dependency_audit_status",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_AUDIT_VERDICT_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "verdict_id",
    "verdict",
    "reviewed_task_id",
    "reviewed_task_path",
    "reviewed_return_record_ref",
    "audit_dispatch_record_ref",
    "audit_provenance_type",
    "audit_task_id",
    "audit_task_path",
    "audit_claim_id",
    "audit_session_id",
    "reviewed_executor_instance",
    "auditor_instance",
    "independence_distinct_instance",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "agent_runtime",
    "verdict_at",
    "findings_summary_present",
    "evidence_refs",
    "recommended_next_action",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "dependency_audit_status_after",
    "finalize_performed",
    "accepted_work_unblocked",
    "lease_status",
    "lease_path",
    "active_lease_written",
]


def build_claim_log_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    claim_id: str,
    session_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": RecordType.CLAIM_LOG,
        "claim_id": claim_id,
        "task_id": task_id,
        "task_path": task_path,
        "actor": actor,
        "claim_action": "claimed",
        "created_at": created_at,
        "from_state": "pending",
        "to_state": "claimed",
        "session_id": session_id,
    }
    body = "\n".join(
        [
            f"# Claim Log: {claim_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` claimed by `{actor}`.",
            "",
            "## Safety",
            "",
            "This claim log was created by AIPOS queue mutation with records enabled.",
            "",
        ]
    )
    return render_markdown(metadata, body, CLAIM_FRONTMATTER_ORDER)


def build_session_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    session_id: str,
    claim_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": RecordType.SESSION_RECORD,
        "session_id": session_id,
        "task_id": task_id,
        "task_path": task_path,
        "actor": actor,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "active",
        "claim_id": claim_id,
        "current_state": "claimed",
        "event_count": 1,
    }
    body = "\n".join(
        [
            f"# Session Record: {session_id}",
            "",
            "## Events",
            "",
            f"- {created_at} claimed by {actor}",
            "",
        ]
    )
    return render_markdown(metadata, body, SESSION_FRONTMATTER_ORDER)


def _confirmer_fields(confirmer: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-197 confirmer attribution + AIPOS-193 §9 signature-ready placeholders.

    Records WHO confirmed (role + non-secret token fingerprint) so L3 can tell an
    Owner-role confirmation from an agent self-confirmation. Never stores a raw token.
    """
    c = confirmer or {}
    return {
        "confirmer_role": str(c.get("confirmer_role") or ""),
        "confirmer_token_ref": str(c.get("confirmer_token_ref") or ""),
        "confirmer_token_fingerprint": str(c.get("confirmer_token_fingerprint") or ""),
        "gate_signature": "",
        "authority_seal": "",
        "signature_key_ref": "",
        "signed_payload_hash": "",
        "signed_at": "",
    }


def build_mcp_claim_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    claim_id: str,
    session_id: str,
    claimed_at: str,
    autonomy_mode: str = "Supervised",
    actual_model: str | None = None,
    reported_tokens: int | None = None,
    claim_policy: str | None = None,
    claim_match_basis: str | None = None,
    claim_requirements_hash: str | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> str:
    metadata = {
        "record_type": RecordType.CLAIM_RECORD,
        "event_type": "mcp_queue_claim",
        "claim_id": claim_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_claim",
        # AIPOS-250: autonomy_mode is now read from the caller (Supervised | PreAuthorized),
        # no longer hardcoded — a PreAuthorized envelope auto-release stamps PreAuthorized so the
        # record self-attributes to the policy that permitted it.
        "autonomy_mode": str(autonomy_mode or "Supervised").strip() or "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        # AIPOS-250 (capability ledger): agent-REPORTED, not gate-measured (disclosure #15).
        "actual_model": str(actual_model or "").strip(),
        "reported_tokens": int(reported_tokens) if isinstance(reported_tokens, int) else "",
        "claimed_at": claimed_at,
        "from_state": "pending",
        "to_state": "claimed",
        "claim_policy": claim_policy or "",
        "claim_match_basis": claim_match_basis or "",
        "claim_requirements_hash": claim_requirements_hash or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        **_confirmer_fields(confirmer),
        "session_id": session_id,
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    body = "\n".join(
        [
            f"# MCP Claim Record: {claim_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` was claimed by `{canonical_agent_instance}` through the {metadata['autonomy_mode']} MCP claim surface.",
            f"- Owner policy: `{owner_policy_ref}`.",
            "",
            "## Boundary",
            "",
            "This record is provenance evidence only. It does not activate a lease, launch work, dispatch audit, record audit PASS, finalize, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_CLAIM_FRONTMATTER_ORDER)


def build_mcp_claim_session_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    session_id: str,
    claim_id: str,
    created_at: str,
    autonomy_mode: str = "Supervised",
) -> str:
    metadata = {
        "record_type": RecordType.SESSION_RECORD,
        "session_id": session_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        # AIPOS-250: the session is a claim-side artifact — reflect the claim's autonomy_mode
        # (Supervised | PreAuthorized) so it stays consistent with the claim record.
        "autonomy_mode": str(autonomy_mode or "Supervised").strip() or "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "claim_id": claim_id,
        "created_at": created_at,
        "updated_at": created_at,
        "session_status": "claimed",
        "current_state": "claimed",
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
        "event_count": 1,
    }
    body = "\n".join(
        [
            f"# MCP Session Record: {session_id}",
            "",
            "## Events",
            "",
            f"- {created_at} mcp_queue_claim by {canonical_agent_instance}; claim_id={claim_id}; owner_policy_ref={owner_policy_ref}; lease_status=proposed.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def build_mcp_return_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    return_id: str,
    claim_id: str,
    session_id: str,
    returned_at: str,
    result_summary: str | None,
    artifact_refs: list[str],
    completion_report_ref: str | None,
    actual_model: str | None = None,
    reported_tokens: int | None = None,
    agent_runtime: dict[str, Any] | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    confirmer: dict[str, Any] | None = None,
    self_check_waived: bool = False,
    self_check_waiver_reason: str | None = None,
) -> str:
    metadata = {
        "record_type": RecordType.RETURN_RECORD,
        "event_type": "mcp_queue_return",
        "return_id": return_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_return",
        # AIPOS-250 red line 4: return stays Supervised-only (per-task owner_confirm); the
        # PreAuthorized tier is CLAIM-only this slice. Do NOT parametrize this.
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        # AIPOS-265 (field convergence): agent_runtime is the single new runtime 口径.
        # Legacy actual_model/reported_tokens are persisted ONLY when the caller still
        # sends a value (conditional below); empty values are dropped so new return
        # records no longer carry dead 空 fields. History files are never rewritten.
        "claim_id": claim_id,
        "session_id": session_id,
        "returned_at": returned_at,
        "executor_status": "completed",
        "audit_readiness": "ready",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "result_summary_present": bool(result_summary),
        "artifact_refs": artifact_refs,
        "completion_report_ref": completion_report_ref or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        **_confirmer_fields(confirmer),
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    # AIPOS-265 (field convergence): legacy actual_model/reported_tokens persisted ONLY
    # when non-empty (空置停写). agent_runtime (below) is the single new 口径; these
    # stay for read-side compat with callers still sending a value. Frontmatter order
    # keeps them adjacent to agent_runtime when present, absent otherwise.
    _actual_model = str(actual_model or "").strip()
    if _actual_model:
        metadata["actual_model"] = _actual_model
    if isinstance(reported_tokens, int) and not isinstance(reported_tokens, bool):
        metadata["reported_tokens"] = reported_tokens
    # AIPOS-261 (additive): only persist agent_runtime when at least one sub-value is
    # present, so old records (and returns that did not report runtime) simply lack the
    # key — the popup reads absent-key as 未记录.
    if isinstance(agent_runtime, dict) and agent_runtime:
        metadata["agent_runtime"] = dict(agent_runtime)
    
    # AIPOS-F49-fix1: self_check_waived 标记（Owner 强制放行）
    if self_check_waived:
        metadata["self_check_waived"] = True
        if self_check_waiver_reason:
            metadata["self_check_waiver_reason"] = self_check_waiver_reason
    body = "\n".join(
        [
            f"# MCP Return Record: {return_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` was returned by `{canonical_agent_instance}` through the Supervised MCP return surface.",
            f"- Owner policy: `{owner_policy_ref}`.",
            f"- Result summary: {result_summary or 'not provided'}",
            "",
            "## Boundary",
            "",
            "This record marks executor completion plus audit readiness only. It does not dispatch audit, record audit PASS, finalize, activate a lease, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_RETURN_FRONTMATTER_ORDER)


def build_mcp_audit_dispatch_record_markdown(
    *,
    dispatch_id: str,
    reviewed_task_id: str,
    reviewed_task_path: str,
    reviewed_return_record_ref: str,
    reviewed_executor_instance: str,
    reviewed_executor_claim_id: str,
    reviewed_executor_session_id: str,
    audit_task_id: str,
    audit_task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    dispatched_at: str,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    supersedes: str | None = None,
) -> str:
    metadata = {
        "record_type": RecordType.AUDIT_DISPATCH_RECORD,
        "event_type": "mcp_audit_dispatch",
        "dispatch_id": dispatch_id,
        "reviewed_task_id": reviewed_task_id,
        "reviewed_task_path": reviewed_task_path,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_executor_claim_id": reviewed_executor_claim_id,
        "reviewed_executor_session_id": reviewed_executor_session_id,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "surface": "mcp",
        "operation": RecordType.AUDIT_DISPATCH,
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "dispatched_at": dispatched_at,
        "independence_distinct_instance": True,
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    # AIPOS-F72: supersedes 引用(放行 re-dispatch 时记录旧链)
    if supersedes:
        metadata["supersedes"] = supersedes
    
    body_lines = [
        f"# MCP Audit Dispatch Record: {dispatch_id}",
        "",
        "## Summary",
        "",
        f"- Task `{reviewed_task_id}` was dispatched for independent audit as `{audit_task_id}`.",
        f"- Reviewed executor instance: `{reviewed_executor_instance}`.",
        f"- Owner policy: `{owner_policy_ref}`.",
    ]
    if supersedes:
        body_lines.extend([
            "",
            "## Re-dispatch Note (AIPOS-F72)",
            "",
            f"This dispatch supersedes a prior dead dispatch chain: `{supersedes}`.",
            "The previous audit card was concluded with zero verdicts (e.g., 'no substance to audit').",
        ])
    body_lines.extend([
        "",
        "## Boundary",
        "",
        "This record creates audit-dispatch provenance only. It does not claim the audit task, launch an auditor, record a verdict, finalize, activate a lease, or unblock dependent work.",
        "",
    ])
    body = "\n".join(body_lines)
    return render_markdown(metadata, body, MCP_AUDIT_DISPATCH_FRONTMATTER_ORDER)


def build_mcp_audit_verdict_record_markdown(
    *,
    verdict_id: str,
    verdict: str,
    reviewed_task_id: str,
    reviewed_task_path: str,
    reviewed_return_record_ref: str,
    audit_dispatch_record_ref: str,
    audit_provenance_type: str = "dispatch",
    audit_task_id: str,
    audit_task_path: str,
    audit_claim_id: str,
    audit_session_id: str,
    reviewed_executor_instance: str,
    auditor_instance: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    verdict_at: str,
    findings_summary: str | None,
    evidence_refs: list[str],
    recommended_next_action: str | None,
    owner_waiver_ref: str | None = None,  # AIPOS-R6A 靶子④: 仲裁/豁免一等公民
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    agent_runtime: dict[str, Any] | None = None,
    artifact_subject: dict[str, Any] | None = None,  # AIPOS-F70: 产物指纹
) -> str:
    metadata = {
        "record_type": RecordType.AUDIT_VERDICT_RECORD,
        "event_type": "mcp_audit_verdict",
        "verdict_id": verdict_id,
        "verdict": verdict,
        "reviewed_task_id": reviewed_task_id,
        "reviewed_task_path": reviewed_task_path,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "audit_dispatch_record_ref": audit_dispatch_record_ref,
        "audit_provenance_type": audit_provenance_type,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "audit_claim_id": audit_claim_id,
        "audit_session_id": audit_session_id,
        "reviewed_executor_instance": reviewed_executor_instance,
        "auditor_instance": auditor_instance,
        "independence_distinct_instance": auditor_instance != reviewed_executor_instance,
        "surface": "mcp",
        "operation": RecordType.AUDIT_VERDICT,
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "verdict_at": verdict_at,
        "findings_summary_present": bool(findings_summary),
        "evidence_refs": evidence_refs,
        "recommended_next_action": recommended_next_action or "",
        "owner_waiver_ref": owner_waiver_ref or "",  # AIPOS-R6A 靶子④: 仲裁/豁免引用
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        "dependency_audit_status_after": Verdict.PASS if verdict == Verdict.PASS else verdict,
        "finalize_performed": False,
        "accepted_work_unblocked": False,
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    # AIPOS-265 FIX-1 (additive, symmetric to the return half): only persist
    # agent_runtime when at least one sub-value is present, so verdict records that
    # did not report runtime simply lack the key — the 档案 popup reads absent-key
    # as 未记录, and existing verdict tests/frontmatter stay byte-identical.
    if isinstance(agent_runtime, dict) and agent_runtime:
        metadata["agent_runtime"] = dict(agent_runtime)
    # AIPOS-F70: artifact_subject 写入裁决记录 (只在提供时写入, 存量裁决无此字段)
    # 新裁决: task_mode=code 的被审卡必须提供 (gate 侧已 fail-closed 校验)
    # 存量裁决: 无此字段 -> finalize/deploy 以警告放行并标注 legacy-verdict
    if isinstance(artifact_subject, dict) and artifact_subject:
        metadata["artifact_subject"] = dict(artifact_subject)
    body = "\n".join(
        [
            f"# MCP Audit Verdict Record: {verdict_id}",
            "",
            "## Summary",
            "",
            f"- Audit task `{audit_task_id}` returned verdict `{verdict}` for `{reviewed_task_id}`.",
            f"- Auditor instance: `{auditor_instance}`.",
            f"- Reviewed executor instance: `{reviewed_executor_instance}`.",
            f"- Findings summary: {findings_summary or 'not provided'}",
            "",
            "## Boundary",
            "",
            "This record is independent audit evidence. PASS may satisfy audit_pass only. It does not finalize, activate a lease, or unblock accepted-work dependencies.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_AUDIT_VERDICT_FRONTMATTER_ORDER)


def load_session_record(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body, warnings = parse_markdown_frontmatter(text)
    return _normalize_value(metadata), body, warnings


def update_session_record_markdown(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    timestamp: str,
    status: str,
    current_state: str,
    event_line: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["status"] = status
    metadata["current_state"] = current_state
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("record_type", RecordType.SESSION_RECORD)
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join([body, "", f"- {event_line}", ""])
    return render_markdown(metadata, body, SESSION_FRONTMATTER_ORDER)


def append_mcp_return_session_event(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    timestamp: str,
    return_id: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["session_status"] = "returned"
    metadata["current_state"] = "claimed"
    metadata.setdefault("lease_status", "proposed")
    metadata.setdefault("lease_path", "claim_only")
    metadata.setdefault("active_lease_written", False)
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("canonical_agent_instance", canonical_agent_instance)
    metadata.setdefault("owner_policy_ref", owner_policy_ref)
    metadata.setdefault("record_type", RecordType.SESSION_RECORD)
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join(
        [
            body,
            "",
            f"- {timestamp} mcp_queue_return by {canonical_agent_instance}; return_id={return_id}; owner_policy_ref={owner_policy_ref}; audit_readiness=ready.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def append_mcp_audit_verdict_session_event(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    timestamp: str,
    verdict_id: str,
    verdict: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["session_status"] = RecordType.AUDIT_VERDICT
    metadata["current_state"] = "claimed"
    metadata.setdefault("lease_status", "proposed")
    metadata.setdefault("lease_path", "claim_only")
    metadata.setdefault("active_lease_written", False)
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("canonical_agent_instance", canonical_agent_instance)
    metadata.setdefault("owner_policy_ref", owner_policy_ref)
    metadata.setdefault("record_type", RecordType.SESSION_RECORD)
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join(
        [
            body,
            "",
            f"- {timestamp} mcp_audit_verdict by {canonical_agent_instance}; verdict_id={verdict_id}; verdict={verdict}; owner_policy_ref={owner_policy_ref}.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def claim_record_paths(repo_root: Path, task_id: str, claim_id: str, session_id: str) -> tuple[Path, Path]:
    claim_path = ensure_safe_record_path(repo_root, expected_claim_log_path(repo_root, task_id, claim_id), RecordType.CLAIM_LOG, task_id)
    session_path = ensure_safe_record_path(repo_root, expected_session_record_path(repo_root, task_id, session_id), RecordType.SESSION_RECORD, task_id)
    return claim_path, session_path


def session_record_path(repo_root: Path, task_id: str, session_id: str) -> Path:
    return ensure_safe_record_path(repo_root, expected_session_record_path(repo_root, task_id, session_id), RecordType.SESSION_RECORD, task_id)


def return_record_path(repo_root: Path, task_id: str, return_id: str) -> Path:
    return ensure_safe_record_path(repo_root, expected_return_record_path(repo_root, task_id, return_id), RecordType.RETURN_RECORD, task_id)


def audit_dispatch_record_path(repo_root: Path, task_id: str, dispatch_id: str) -> Path:
    path = repo_root / AUDIT_DISPATCHES_ROOT / task_id / f"{dispatch_id}.md"
    return ensure_safe_record_path(repo_root, path, RecordType.AUDIT_DISPATCH_RECORD, task_id)


def audit_verdict_record_path(repo_root: Path, task_id: str, verdict_id: str) -> Path:
    path = repo_root / AUDIT_VERDICTS_ROOT / task_id / f"{verdict_id}.md"
    return ensure_safe_record_path(repo_root, path, RecordType.AUDIT_VERDICT_RECORD, task_id)


def closure_record_path(repo_root: Path, task_id: str, closure_id: str) -> Path:
    path = repo_root / CLOSURES_ROOT / task_id / f"{closure_id}.md"
    return ensure_safe_record_path(repo_root, path, RecordType.CLOSURE_RECORD, task_id)


def build_closure_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    closure_id: str,
    closed_at: str,
    closure_evidence: dict[str, Any],
    return_record_ref: str | None = None,
    related_audit_task_refs: list[str] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Build a closure record markdown document (AIPOS-283/289).

    The closure record is the append-only proof that a task was formally closed
    (moved from claimed/ to completed/) through the gate's close verb. It records
    who closed it, when, and what evidence justified the closure.

    AIPOS-289: warnings (governance account drift) are written to frontmatter.
    """
    metadata = {
        "record_type": RecordType.CLOSURE_RECORD,
        "event_type": "mcp_queue_close",
        "closure_id": closure_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_close",
        "actor": actor,
        "closed_at": closed_at,
        "closure_evidence_type": closure_evidence.get("type", "unknown"),
        "closure_evidence_ref": closure_evidence.get("ref", ""),
        "return_record_ref": return_record_ref or "",
    }
    if related_audit_task_refs:
        metadata["related_audit_task_refs"] = related_audit_task_refs
    if warnings:
        metadata["warnings"] = warnings
    body = (
        f"# Closure Record: {closure_id}\n\n"
        f"Task `{task_id}` was closed (claimed → completed) by `{actor}` at `{closed_at}`.\n\n"
        f"## Evidence\n\n"
        f"- Type: {closure_evidence.get('type', 'unknown')}\n"
        f"- Ref: {closure_evidence.get('ref', '')}\n\n"
        f"## Return Record\n\n"
        f"- Return record ref: {return_record_ref or 'N/A'}\n"
    )
    if related_audit_task_refs:
        body += f"\n## Related Audit Tasks\n\n"
        for ref in related_audit_task_refs:
            body += f"- {ref}\n"
    if warnings:
        body += f"\n## Warnings\n\n"
        for warning in warnings:
            body += f"- {warning}\n"
    return render_markdown(metadata, body, [
        "record_type", "event_type", "closure_id", "task_id", "task_path",
        "surface", "operation", "actor", "closed_at", "closure_evidence_type",
        "closure_evidence_ref", "return_record_ref", "related_audit_task_refs",
        "warnings",
    ])
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
