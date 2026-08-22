"""AIPOS-F21: credential rotation as a product action — `lybra roles rotate`.

Two-phase service-token rotation over the workspace connection.json (the gate's
credential source of truth), plus instance-token removal
(`lybra roles remove --instance`).

Design anchors (task card AIPOS-F21):
- Two-phase: ``--dry-run`` previews the fingerprint list and lands NO change;
  execution requires ``--owner-authorization-ref`` (recorded; the token
  plaintext NEVER appears in any output or record — fingerprints only).
- Rotation = regenerate a same-structure token for every selected entry
  (all roles by default, ``--role`` subset), atomic write-back to the SAME
  file, timestamped backup of the old file (0600).
- Audit trail: record_type=token_rotation records under 5_tasks/records/
  following the deployments/ convention (frontmatter machine markers per
  AIPOS-R6M; fingerprints only, never plaintext).
- Gate process: hot-reload first — call the gate MCP verb
  ``lybra_roles_reload`` authenticated with the PRE-rotation owner token
  (held in memory only); when not feasible, emit the restart path.
- Closing guidance: every workstation holding an old token must re-enroll
  (enroll-code issuance hint + enroll command).

This module is workspace-local (same machine as the gate service). It does
not manage gate lifecycle; restart stays an Owner/ops action by design.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.schema_constants import Verdict

REQUIRED_CONNECTION_MODE = 0o600
RELOAD_VERB = "lybra_roles_reload"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def secret_fingerprint(raw: str) -> str:
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _connection_path(workspace_root: Path, *, connection_target: Path | None = None) -> Path:
    if connection_target is not None:
        return Path(connection_target).expanduser().resolve()
    return (Path(workspace_root).expanduser().resolve() / ".lybra" / "connection.json")


def _records_dir(workspace_root: Path) -> Path:
    return Path(workspace_root).expanduser().resolve() / "5_tasks" / "records" / "token_rotations"


def _load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Connection config must be a JSON object: {path}")
    return data


def _token_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = config.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError("Connection config 'tokens' must be a list")
    return [item for item in tokens if isinstance(item, dict)]


def _safe_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Non-secret projection of a token entry (never the raw token)."""
    safe = {
        "role": entry.get("role", ""),
        "instance": entry.get("agent_instance"),
        "token_ref": entry.get("token_ref", ""),
        "fingerprint": entry.get("fingerprint") or secret_fingerprint(str(entry.get("token") or "")),
    }
    return safe


def _atomic_write_config(path: Path, config: dict[str, Any]) -> None:
    """Write connection.json atomically (temp + rename) with 0600 perms."""
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, REQUIRED_CONNECTION_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if os.name == "posix":
            os.chmod(tmp, REQUIRED_CONNECTION_MODE)
    os.replace(tmp, path)
    os.chmod(path, REQUIRED_CONNECTION_MODE)


def _backup_config(path: Path) -> Path:
    """Timestamped 0600 backup of the current connection.json (pre-rotation)."""
    backup = path.with_name(f"{path.name}.bak-{_stamp_now()}")
    shutil.copy2(path, backup)
    os.chmod(backup, REQUIRED_CONNECTION_MODE)
    return backup


def _gate_base_url(config: dict[str, Any]) -> str | None:
    mcp = config.get("mcp")
    if isinstance(mcp, dict):
        url = str(mcp.get("rpc_url") or mcp.get("sse_url") or "").strip()
        if url:
            return url.rstrip("/").removesuffix("/mcp").removesuffix("/sse")
    url = str(config.get("gate_url") or "").strip()
    if url:
        return url.rstrip("/").removesuffix("/mcp").removesuffix("/sse")
    return None


def _owner_token_from(config: dict[str, Any]) -> str | None:
    """Pre-rotation owner token, in-memory only (never printed)."""
    for entry in _token_list(config):
        if entry.get("role") == "owner":
            token = str(entry.get("token") or "").strip()
            if token:
                return token
    return None


def _attempt_gate_reload(
    config_before: dict[str, Any],
    *,
    owner_authorization_ref: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Ask the running gate to hot-reload its token registry.

    Auth uses the PRE-rotation owner token (still valid until the reload
    lands). On any failure the caller falls back to restart guidance.
    """
    base_url = _gate_base_url(config_before)
    owner_token = _owner_token_from(config_before)
    if not base_url or not owner_token:
        return {"ok": False, "reason": "no gate URL or no pre-rotation owner token in connection.json"}
    try:
        from tools.aipos_cli.confirm_client import GateClient

        client = GateClient(base_url, owner_token, timeout=timeout)
        client.initialize()
        result = client.call_tool(RELOAD_VERB, {"owner_authorization_ref": owner_authorization_ref})
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        if not data.get("ok"):
            return {"ok": False, "reason": f"gate verb {RELOAD_VERB} returned ok=false", "detail": data}
        return {"ok": True, "detail": data}
    except Exception as exc:  # noqa: BLE001 — reload must never crash rotation
        return {"ok": False, "reason": str(exc)}


def _restart_guidance(config: dict[str, Any]) -> list[str]:
    base_url = _gate_base_url(config) or "(gate url unknown)"
    return [
        f"Gate ({base_url}) did NOT hot-reload the new tokens. Restart the gate service so it "
        "re-reads connection.json:",
        "  - systemd deployment:  sudo systemctl restart lybra-dev-gate.service",
        "  - manual serve mode:   lybra serve stop && lybra serve start --workspace-root <workspace-root>",
        "Until restarted, the gate still accepts the OLD tokens (backup) and rejects the new ones.",
    ]


def _write_record(
    workspace_root: Path,
    *,
    record_type: str,
    filename: str,
    frontmatter: dict[str, Any],
    body_lines: list[str],
) -> Path:
    """Write a machine-marked record under 5_tasks/records/token_rotations/.

    Follows the deployments/ record convention (frontmatter machine markers per
    AIPOS-R6M: record_type + operational fields; naming <kind>_<timestamp>.md).
    NEVER contains token plaintext — callers pass fingerprints only.
    """
    records_dir = _records_dir(workspace_root)
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / filename
    fm_lines = ["---"]
    for key in sorted(frontmatter):
        fm_lines.append(f"{key}: {frontmatter[key]}")
    fm_lines.append("---")
    text = "\n".join(fm_lines) + "\n\n" + "\n".join(body_lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _reenroll_guidance(entries: list[dict[str, Any]], config: dict[str, Any]) -> list[str]:
    base_url = _gate_base_url(config) or "(gate url unknown)"
    lines = [
        "All rotated tokens below are now INVALID. Every workstation holding an old token",
        "must re-enroll with a fresh enrollment code:",
        "  1) code issuer (owner-side): lybra roles enroll-code --role <role> [--instance <instance>] --owner-authorization-ref <ref>",
        "  2) workstation:             lybra roles enroll --code <code> --workspace-root <seat-root> --gate-url " + base_url,
        "Invalidated fingerprints:",
    ]
    for entry in entries:
        label = entry.get("instance") or "(unbound)"
        lines.append(f"  - {entry.get('role', ''):<16} {label:<36} {entry.get('fingerprint', '')}")
    return lines


def rotate_tokens_report(
    workspace_root: Path,
    *,
    dry_run: bool,
    roles: list[str] | None = None,
    owner_authorization_ref: str | None = None,
    actor: str | None = None,
    reason: str = "",
    connection_target: Path | None = None,
    reload_gate: bool = True,
) -> dict[str, Any]:
    """AIPOS-F21: `lybra roles rotate` core.

    dry_run=True  → preview only (fingerprints that would rotate; zero writes).
    dry_run=False → requires owner_authorization_ref; backs up, regenerates
                    same-structure tokens, atomic write, rotation record,
                    gate hot-reload (fallback: restart guidance + re-enroll
                    guidance).
    """
    workspace_root = Path(workspace_root).expanduser().resolve()
    conn_path = _connection_path(workspace_root, connection_target=connection_target)
    operation = "roles_rotate"
    base: dict[str, Any] = {
        "operation": operation,
        "workspace_root": str(workspace_root),
        "connection_path": str(conn_path),
        "dry_run": bool(dry_run),
    }
    if not conn_path.exists():
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": f"Connection config not found at {conn_path}"}]}
    if not dry_run and not (owner_authorization_ref or "").strip():
        return {**base, "ok": False, "verdict": Verdict.BLOCK, "blocking_reasons": [{
            "message": "Token rotation execution is owner-gated: --owner-authorization-ref is required "
                       "(run --dry-run first for the preview)."}]}
    try:
        config = _load_config(conn_path)
        tokens = _token_list(config)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": f"Cannot load connection config: {exc}"}]}

    known_roles = sorted({str(t.get("role") or "") for t in tokens if t.get("role")})
    if roles:
        unknown = [r for r in roles if r not in known_roles]
        if unknown:
            return {**base, "ok": False, "verdict": Verdict.BLOCK, "blocking_reasons": [{
                "message": f"Unknown role(s) {unknown}. Roles present in connection.json: {known_roles}"}]}

    selected = [i for i, t in enumerate(tokens) if (not roles) or (t.get("role") in roles)]
    if not selected:
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": "No token entries selected for rotation."}]}

    preview = [_safe_entry(tokens[i]) for i in selected]
    if dry_run:
        return {
            **base,
            "ok": True,
            "verdict": Verdict.PASS,
            "would_rotate": preview,
            "roles": roles or known_roles,
            "notice": "Dry-run only: nothing was written. Re-run with --owner-authorization-ref to execute.",
        }

    # ---- execution path ----
    now = _utc_now()
    mapping: list[dict[str, Any]] = []
    for idx in selected:
        entry = dict(tokens[idx])
        old_token = str(entry.get("token") or "")
        if not old_token:
            return {**base, "ok": False, "verdict": Verdict.BLOCK, "blocking_reasons": [{
                "message": f"Token entry (role={entry.get('role')}, instance={entry.get('agent_instance')}) "
                           "has no token value; refusing to rotate a malformed registry."}]}
        new_token = secrets.token_urlsafe(32)
        entry["token"] = new_token
        entry["fingerprint"] = secret_fingerprint(new_token)
        tokens[idx] = entry
        mapping.append({
            "role": entry.get("role", ""),
            "instance": entry.get("agent_instance"),
            "old_fingerprint": secret_fingerprint(old_token),
            "new_fingerprint": entry["fingerprint"],
        })
    config["tokens"] = tokens
    config["rotated_at"] = now

    try:
        backup_path = _backup_config(conn_path)
        _atomic_write_config(conn_path, config)
    except OSError as exc:
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": f"Failed to write rotated config: {exc}"}]}

    reload_result: dict[str, Any] = {"ok": False, "reason": "skipped"}
    if reload_gate:
        reload_result = _attempt_gate_reload(config, owner_authorization_ref=str(owner_authorization_ref))
    gate_reload = "hot_reload_ok" if reload_result.get("ok") else "restart_required"

    record_frontmatter = {
        "record_type": "token_rotation",
        "operation": operation,
        "actor": actor or owner_authorization_ref or "owner",
        "owner_authorization_ref": str(owner_authorization_ref),
        "reason": reason or "(none)",
        "rotated_at": now,
        "connection_path": str(conn_path),
        "backup_path": str(backup_path),
        "gate_reload": gate_reload,
    }
    body = ["# Token Rotation Record (AIPOS-F21)", "",
            "Old → new fingerprints (plaintext tokens never recorded):", ""]
    for item in mapping:
        label = item.get("instance") or "(unbound)"
        body.append(f"- {item['role']:<16} {label:<36} {item['old_fingerprint']} -> {item['new_fingerprint']}")
    body += ["", f"- backup: `{backup_path.name}` (0600)", f"- gate reload: {gate_reload}",
             "- security notice: token plaintext lives only in connection.json / its backup."]
    record_path = _write_record(
        workspace_root, record_type="token_rotation",
        filename=f"rotation_{_stamp_now()}.md",
        frontmatter=record_frontmatter, body_lines=body,
    )

    result: dict[str, Any] = {
        **base,
        "ok": True,
        "verdict": Verdict.PASS,
        "rotated": mapping,
        "backup_path": str(backup_path),
        "rotation_record": str(record_path),
        "gate_reload": gate_reload,
        "reload_detail": reload_result.get("detail") if reload_result.get("ok") else reload_result.get("reason"),
    }
    if gate_reload != "hot_reload_ok":
        result["restart_guidance"] = _restart_guidance(config)
    result["next_steps"] = _reenroll_guidance(preview, config)
    return result


def remove_instance_report(
    workspace_root: Path,
    *,
    instance: str,
    owner_authorization_ref: str | None = None,
    actor: str | None = None,
    reason: str = "",
    connection_target: Path | None = None,
    reload_gate: bool = True,
) -> dict[str, Any]:
    """AIPOS-F21: `lybra roles remove --instance <name>` — remove token entry(ies)
    bound to an agent_instance from connection.json (with record + reload).

    Owner-gated (owner_authorization_ref required) like the roles family.
    """
    workspace_root = Path(workspace_root).expanduser().resolve()
    conn_path = _connection_path(workspace_root, connection_target=connection_target)
    operation = "roles_remove_instance"
    base: dict[str, Any] = {
        "operation": operation,
        "workspace_root": str(workspace_root),
        "connection_path": str(conn_path),
        "instance": instance,
    }
    if not (owner_authorization_ref or "").strip():
        return {**base, "ok": False, "verdict": Verdict.BLOCK, "blocking_reasons": [{
            "message": "Instance token removal is owner-gated: --owner-authorization-ref is required."}]}
    if not conn_path.exists():
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": f"Connection config not found at {conn_path}"}]}
    try:
        config = _load_config(conn_path)
        tokens = _token_list(config)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": f"Cannot load connection config: {exc}"}]}

    kept = [t for t in tokens if t.get("agent_instance") != instance]
    removed = [t for t in tokens if t.get("agent_instance") == instance]
    if not removed:
        return {**base, "ok": False, "verdict": Verdict.BLOCK, "blocking_reasons": [{
            "message": f"No token entry bound to instance {instance!r} in {conn_path}."}]}

    now = _utc_now()
    config["tokens"] = kept
    try:
        backup_path = _backup_config(conn_path)
        _atomic_write_config(conn_path, config)
    except OSError as exc:
        return {**base, "ok": False, "verdict": Verdict.BLOCK,
                "blocking_reasons": [{"message": f"Failed to write updated config: {exc}"}]}

    reload_result: dict[str, Any] = {"ok": False, "reason": "skipped"}
    if reload_gate:
        reload_result = _attempt_gate_reload(config, owner_authorization_ref=str(owner_authorization_ref))
    gate_reload = "hot_reload_ok" if reload_result.get("ok") else "restart_required"

    removed_view = [_safe_entry(t) for t in removed]
    record_path = _write_record(
        workspace_root,
        record_type="token_removal",
        filename=f"removal_{_stamp_now()}.md",
        frontmatter={
            "record_type": "token_removal",
            "operation": operation,
            "actor": actor or owner_authorization_ref or "owner",
            "owner_authorization_ref": str(owner_authorization_ref),
            "reason": reason or "(none)",
            "removed_at": now,
            "connection_path": str(conn_path),
            "instance": instance,
            "backup_path": str(backup_path),
            "gate_reload": gate_reload,
        },
        body_lines=[
            "# Token Removal Record (AIPOS-F21)", "",
            f"Removed token entries bound to instance `{instance}` (fingerprints only):", "",
            *[f"- {e['role']:<16} {e['instance']:<36} {e['fingerprint']}" for e in removed_view],
            "", f"- backup: `{backup_path.name}` (0600)", f"- gate reload: {gate_reload}",
        ],
    )
    result: dict[str, Any] = {
        **base,
        "ok": True,
        "verdict": Verdict.PASS,
        "removed": removed_view,
        "backup_path": str(backup_path),
        "removal_record": str(record_path),
        "gate_reload": gate_reload,
        "reload_detail": reload_result.get("detail") if reload_result.get("ok") else reload_result.get("reason"),
    }
    if gate_reload != "hot_reload_ok":
        result["restart_guidance"] = _restart_guidance(config)
    return result
