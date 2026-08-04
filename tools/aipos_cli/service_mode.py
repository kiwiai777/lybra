from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.workspace_config import DEFAULT_BOARD_HOST, DEFAULT_BOARD_PORT, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT




LOCAL_DIR_REL = Path(".lybra") / "local"
CONNECTION_REL = LOCAL_DIR_REL / "connection.json"
SERVICE_STATE_REL = LOCAL_DIR_REL / "service_state.json"

# AIPOS-226 (Slice 2): the connection.json (role tokens, 0600) now defaults to the GLOBAL
# Lybra runtime root (~/.lybra/local/) so tokens are never committed into a user truth repo.
# This is ★A1-adjacent: ONLY the file LOCATION changes — token minting/scopes/ROLE_SPECS are
# unchanged. `--connection-json` still overrides; the workspace-local path stays honored for
# legacy reads (TUI fallback).
RUNTIME_ROOT = Path("~/.lybra")
RUNTIME_LOCAL_REL = Path("local")
RUNTIME_CONNECTION_REL = RUNTIME_LOCAL_REL / "connection.json"
WORKSPACE_GITIGNORE_REL = Path(".gitignore")
REQUIRED_LOCAL_DIR_MODE = 0o700
REQUIRED_CONNECTION_MODE = 0o600
SERVICE_MODE_VERSION = 1
SERVICE_MODE = "service_v0"
LOOPBACK_NO_PROXY_VALUES = {"127.0.0.1", "localhost", "::1"}
PROXY_ENV_NAMES = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
NO_PROXY_ENV_NAMES = ("no_proxy", "NO_PROXY")

ROLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "role": "executor",
        "token_ref": "svc-executor",
        # AIPOS-283: executor also holds queue_close (the gate close verb — finalize
        # settlement step). This is NOT owner-gated: it requires closure_evidence and
        # a prior return record, but not owner_confirm.
        "scopes": ["queue_claim", "queue_return", "queue_close"],
    },
    {
        # AIPOS-197: Owner-only confirm authority. The Owner uses this token,
        # out of band, to CONFIRM claim/return dry-runs. The executor token does
        # not hold owner_confirm, so a confined agent cannot self-confirm.
        "role": "owner",
        "token_ref": "svc-owner",
        # AIPOS-207 (F-cop-204scope-1): the Owner holds draft_publish so the AIPOS-204
        # gated publish surface is reachable via serve-rotate creds (DG-11: the Owner runs
        # dry_run + confirm in one proceed action). Only owner — NOT executor/copilot.
        # The two-scope rule is unchanged: publish confirm still also needs owner_confirm.
        # AIPOS-250: the Owner also holds owner_decision_record so the Owner can DRAFT+ARM a
        # PreAuthorized autonomy envelope (owner_autonomy_policy) via serve-rotate creds — it is
        # an Owner-only write surface (OWNER_DECISION_SCOPE) whose confirm additionally requires
        # owner_confirm when it grants a policy. Without this scope the owner-console SKILL's
        # envelope flow is unreachable (the dry_run/confirm tools never appear). Moved off the
        # path-B-only exemption (test_scope_reachability CAPABILITY_TOKEN_EXEMPT) accordingly.
        # AIPOS-318: the Owner also holds queue_amend and queue_withdraw (顾问侧治理动作:
        # 修订未认领的卡、撤回卡). These are Owner-only governance operations per AIPOS-315;
        # executor/auditor/planner should NOT be able to amend/withdraw task cards.
        "scopes": ["queue_claim", "queue_return", "owner_confirm", "draft_publish", "owner_decision_record", "queue_amend", "queue_withdraw"],
    },
    {
        "role": "owner-dispatch",
        "token_ref": "svc-owner-dispatch",
        "scopes": ["audit_dispatch"],
    },
    {
        "role": "auditor",
        "token_ref": "svc-auditor",
        "scopes": ["queue_claim", "audit_verdict"],
    },
    {
        # AIPOS-206 (DG-11): the Planning Copilot's read-only credential. scopes [] is
        # verified-sufficient — read tools are exposed by default (no scope required;
        # tools.py READ_ONLY_NOTICE), and every write/confirm/publish op is structurally
        # SCOPE_DENIED. This is the copilot-side ★A1 boundary as a credential, not policy.
        "role": "copilot",
        "token_ref": "svc-copilot",
        "scopes": [],
    },
    {
        # AIPOS-249 (planner slice): the BYO external planning advisor's credential.
        # scopes = [draft_submit] ONLY — read tools are exposed by default (no scope),
        # and draft_submit lets the planner land task-card DRAFTS into 5_tasks/drafts/
        # (a proposal zone, structurally path-locked). The planner holds NO
        # queue_claim/queue_return/owner_confirm/draft_publish/audit_* — so it can never
        # claim/return/confirm/publish/audit: every such op is structurally SCOPE_DENIED.
        # Landing a draft into truth (drafts -> queue/pending) is draft_publish, which the
        # planner lacks and which additionally requires owner_confirm — the gate stays with
        # the Owner. This is the planner-side ★A1 boundary as a credential, not policy.
        "role": "planner",
        "token_ref": "svc-planner",
        "scopes": ["draft_submit"],
    },
)


@dataclass(frozen=True)
class PermissionIssue:
    path: Path
    observed_mode: int | None
    required_mode: int
    severity: str
    message: str
    fix_command: str

    def to_dict(self) -> dict[str, Any]:
        observed = f"{self.observed_mode:04o}" if self.observed_mode is not None else "unknown"
        required = f"{self.required_mode:04o}"
        return {
            "path": str(self.path),
            "observed_mode": observed,
            "required_mode": required,
            "severity": self.severity,
            "message": self.message,
            "fix_command": self.fix_command,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def secret_fingerprint(raw: str) -> str:
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _is_probably_non_posix(path: Path) -> bool:
    if os.name != "posix":
        return True
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return len(resolved.parts) >= 3 and resolved.parts[1] == "mnt"


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _permission_issue(path: Path, required_mode: int, *, target_label: str, severity: str) -> PermissionIssue | None:
    if not path.exists():
        return None
    observed = _mode(path)
    if observed is None:
        return PermissionIssue(
            path=path,
            observed_mode=None,
            required_mode=required_mode,
            severity="WARN",
            message=f"Could not inspect {target_label} permissions; treat it as a local secret path.",
            fix_command=f"chmod {required_mode:03o} {path}",
        )
    if observed & 0o077 == 0:
        return None
    downgraded = _is_probably_non_posix(path)
    return PermissionIssue(
        path=path,
        observed_mode=observed,
        required_mode=required_mode,
        severity="WARN" if downgraded else severity,
        message=(
            f"{target_label} permissions are too broad. Required {required_mode:04o}; observed {observed:04o}. "
            + ("Permissions may not be faithfully enforceable on this filesystem; warning only." if downgraded else "Fix before loading or writing service tokens.")
        ),
        fix_command=f"chmod {required_mode:03o} {path}",
    )


def runtime_connection_path(env: dict[str, str] | None = None) -> Path:
    """The default connection.json location: ~/.lybra/local/connection.json.

    Honors $HOME (via expanduser) so tests can patch HOME to a temp dir."""
    if env is not None:
        home = str(env.get("HOME") or "").strip()
        if home:
            return Path(home) / ".lybra" / RUNTIME_CONNECTION_REL
    return (RUNTIME_ROOT / RUNTIME_CONNECTION_REL).expanduser()


def _resolve_connection_target(workspace_root: Path, connection_target: Path | None) -> Path:
    """Resolve the connection.json path.

    AIPOS-226: when a `connection_target` is supplied it wins (override OR the runtime default
    that the report functions thread through). When None this falls back to the LEGACY
    in-workspace path (<workspace_root>/.lybra/local/connection.json) — the serve REPORT
    functions (rotate/start/status/stop) default `connection_target` to the runtime root so the
    user-facing default is ~/.lybra/local/, while the low-level writers keep their v1 contract
    for direct callers (e.g. the scope-reachability fixture)."""
    if connection_target is not None:
        return Path(connection_target).expanduser()
    return workspace_root / CONNECTION_REL


def check_service_permissions(
    workspace_root: Path, *, for_secret_use: bool, connection_target: Path | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = _resolve_connection_target(workspace_root, connection_target)
    local_dir = conn.parent
    connection_path = conn
    issues: list[PermissionIssue] = []
    dir_issue = _permission_issue(
        local_dir,
        REQUIRED_LOCAL_DIR_MODE,
        target_label=".lybra/local directory",
        severity="BLOCK" if for_secret_use else "WARN",
    )
    if dir_issue:
        issues.append(dir_issue)
    file_issue = _permission_issue(
        connection_path,
        REQUIRED_CONNECTION_MODE,
        target_label="connection.json",
        severity="BLOCK" if for_secret_use else "WARN",
    )
    if file_issue:
        issues.append(file_issue)
    blocking = [issue.to_dict() for issue in issues if issue.severity == "BLOCK"]
    warnings = [issue.to_dict() for issue in issues if issue.severity != "BLOCK"]
    return blocking, warnings


def _proxy_loopback_warnings(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    source_env = env if env is not None else os.environ
    active_proxy_names = sorted(
        name
        for name in PROXY_ENV_NAMES
        if str(source_env.get(name) or "").strip()
        and "127.0.0.1" not in str(source_env.get(name) or "")
        and "localhost" not in str(source_env.get(name) or "").lower()
    )
    if not active_proxy_names:
        return []
    raw_no_proxy = ",".join(str(source_env.get(name) or "") for name in NO_PROXY_ENV_NAMES)
    no_proxy_values = {part.strip() for part in raw_no_proxy.split(",") if part.strip()}
    missing = sorted(LOOPBACK_NO_PROXY_VALUES - no_proxy_values)
    if not missing:
        return []
    return [
        {
            "message": (
                "Loopback MCP/Board requests may be intercepted by a configured proxy. "
                "Set NO_PROXY=127.0.0.1,localhost,::1 before using local service mode."
            ),
            "proxy_env": active_proxy_names,
            "missing_no_proxy": missing,
            "fix_command": "export NO_PROXY=127.0.0.1,localhost,::1",
        }
    ]


def ensure_local_dir(workspace_root: Path, *, connection_target: Path | None = None) -> Path:
    local_dir = _resolve_connection_target(workspace_root, connection_target).parent
    local_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix" and not _is_probably_non_posix(local_dir):
        os.chmod(local_dir, REQUIRED_LOCAL_DIR_MODE)
    return local_dir


def ensure_workspace_gitignore(workspace_root: Path) -> Path:
    gitignore = workspace_root / WORKSPACE_GITIGNORE_REL
    entry = ".lybra/local/"
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8")
        lines = text.splitlines()
        if entry in [line.strip() for line in lines]:
            return gitignore
        suffix = "" if text.endswith("\n") or not text else "\n"
        gitignore.write_text(text + suffix + entry + "\n", encoding="utf-8")
    else:
        gitignore.write_text(entry + "\n", encoding="utf-8")
    return gitignore


def _role_token_entry(spec: dict[str, Any], *, projects: list[str] | None = None, executor_instance: str | None = None, role_instances: dict[str, str] | None = None) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    entry = {
        "role": spec["role"],
        "token_ref": spec["token_ref"],
        "scopes": list(spec["scopes"]),
        "fingerprint": secret_fingerprint(token),
        "token": token,
    }
    # AIPOS-228 (Slice 4) / AIPOS-229 (Slice 5): optional `projects` dimension. The runtime
    # --project selection wins; otherwise a spec MAY carry its own `projects`. The field is
    # orthogonal to `scopes` and can only NARROW (never widen operation scope). As of Slice 5 it is
    # ENFORCED at the gate (active_project ∈ projects -> else PROJECT_SCOPE_DENIED); the
    # `projects_enforced: true` sibling marker is emitted ONLY alongside a present `projects` field,
    # so a token without it stays byte-identical.
    effective = list(projects) if projects else (list(spec["projects"]) if spec.get("projects") else None)
    if effective:
        entry["projects"] = effective
        entry["projects_enforced"] = True
    # AIPOS-254: generalized role-instance binding for PreAuthorized identity authority.
    # Check role_instances dict first (any role), then executor_instance (backward-compat alias).
    # No binding -> PreAuthorized unavailable for that token (backward-compatible: falls back Supervised).
    bound_instance = None
    if role_instances and spec["role"] in role_instances:
        bound_instance = str(role_instances[spec["role"]]).strip()
    elif executor_instance and spec["role"] == "executor":
        bound_instance = str(executor_instance).strip()
    if bound_instance:
        entry["agent_instance"] = bound_instance
    return entry


# AIPOS-259: bind-all wildcards. A 0.0.0.0 (or ::) bind is CORRECT for listening on every
# interface, but a 0.0.0.0 URL is unusable for clients (the confined worker refuses it, and no
# client can dial it). So the advertise address — what goes into rpc_url/sse_url/board url — must
# be a concrete host whenever the bind is a wildcard. This is the F-258-2 bind/advertise split.
WILDCARD_BIND_HOSTS = frozenset({"0.0.0.0", "::", "::0"})


def _normalize_host(value: str | None) -> str:
    return str(value).strip() if value is not None else ""


def _advertise_or_bind(bind_host: str, advertise_host: str | None) -> str:
    """Advertise host to embed in client URLs; falls back to the bind host when not given.

    Builder default (build_connection_config) — does NOT enforce fail-closed. The wildcard-
    requires-advertise check (AIPOS-259 S3) lives in start_report / rotate_report, which gate
    before any usable config is written. A directly-built wildcard config therefore carries a
    wildcard URL; that is a builder artifact, never something start/rotate will emit to disk."""
    return _normalize_host(advertise_host) or _normalize_host(bind_host)


def _board_block(bind_host: str, advertise_host: str, port: int) -> dict[str, Any]:
    """board surface block: url uses ADVERTISE, host is the BIND, advertise_host stored only
    when it differs from bind (byte-identical to pre-259 output when advertise == bind)."""
    block = {"url": f"http://{advertise_host}:{port}", "host": bind_host, "port": port}
    if advertise_host != bind_host:
        block["advertise_host"] = advertise_host
    return block


def _mcp_block(bind_host: str, advertise_host: str, port: int) -> dict[str, Any]:
    """mcp surface block: rpc_url/sse_url use ADVERTISE, host is the BIND, advertise_host stored
    only when it differs from bind (byte-identical to pre-259 output when advertise == bind)."""
    block = {
        "rpc_url": f"http://{advertise_host}:{port}/mcp",
        "sse_url": f"http://{advertise_host}:{port}/sse",
        "host": bind_host,
        "port": port,
    }
    if advertise_host != bind_host:
        block["advertise_host"] = advertise_host
    return block


def _resolve_bind_advertise(
    *,
    label: str,
    workspace_root: Path,
    param_host: str | None,
    param_advertise: str | None,
    stored_host: str | None,
    stored_advertise: str | None,
    default_host: str,
) -> tuple[str, str, dict[str, Any] | None]:
    """Resolve (bind, advertise, block_reason) for one surface (board or mcp).

    Returns concrete usable hosts when block_reason is None; otherwise the caller must BLOCK
    (fail-closed: a wildcard bind with no usable advertise — clients cannot dial a 0.0.0.0 URL,
    AIPOS-259 S3). Shared by start_report + rotate_report so both paths stay consistent (S4).

    Precedence (F-258-1): bind = explicit param > stored config > default. Advertise = explicit
    param; elif no bind override this run, a previously-stored advertise still applies; else
    default to bind. A stored advertise is IGNORED when a bind param is given this run (it
    belonged to the OLD bind); the user re-states --advertise if they still want one.
    """
    bind = _normalize_host(param_host) or _normalize_host(stored_host) or default_host
    param_adv = _normalize_host(param_advertise)
    stored_adv = _normalize_host(stored_advertise)
    if param_adv:
        advertise = param_adv
    elif param_host is None and stored_adv:
        advertise = stored_adv
    else:
        advertise = ""
    if bind in WILDCARD_BIND_HOSTS and not (advertise and advertise not in WILDCARD_BIND_HOSTS):
        reason = {
            "message": (
                f"{label} bind {bind!r} is a wildcard — correct for listening on every interface, "
                "but clients cannot dial a 0.0.0.0 URL. Pass an explicit advertise host (the address "
                f"clients should reach, e.g. a tailnet IP or DNS name) via --{label}-advertise."
            ),
            "path": str(workspace_root),
            "fix_command": f"lybra serve start --{label}-host {bind} --{label}-advertise <client-reachable-host>",
        }
        return bind, advertise, reason
    return bind, advertise or bind, None


def build_connection_config(
    workspace_root: Path,
    *,
    board_host: str,
    board_port: int,
    mcp_host: str,
    mcp_port: int,
    project: str | None = None,
    executor_instance: str | None = None,
    role_instances: dict[str, str] | None = None,
    board_advertise_host: str | None = None,
    mcp_advertise_host: str | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    # AIPOS-228: a single --project selection scopes every minted role token to that project
    # (mint/echo only). No --project -> no `projects` field anywhere (byte-identical).
    projects = [project] if project and str(project).strip() else None
    # AIPOS-254: generalized --role-instance binding (any role) for PreAuthorized identity authority.
    # --executor-instance is kept as a backward-compatible alias for executor role.
    # No binding -> no `agent_instance` field (backward-compatible: PreAuthorized unavailable, falls back Supervised).
    exec_instance = str(executor_instance).strip() if executor_instance else None
    role_inst_map = dict(role_instances) if role_instances else None
    # AIPOS-259 (F-258-2): URLs (url/rpc_url/sse_url) carry the ADVERTISE host; the `host` field
    # stays the BIND host (consumed by the child --host flag + the port-in-use probe).
    board_adv = _advertise_or_bind(board_host, board_advertise_host)
    mcp_adv = _advertise_or_bind(mcp_host, mcp_advertise_host)
    return {
        "config_version": SERVICE_MODE_VERSION,
        "mode": SERVICE_MODE,
        "workspace_root": str(workspace_root),
        "local_only": True,
        "created_at": now,
        "rotated_at": None,
        "board": _board_block(board_host, board_adv, board_port),
        "mcp": _mcp_block(mcp_host, mcp_adv, mcp_port),
        "tokens": [_role_token_entry(spec, projects=projects, executor_instance=exec_instance, role_instances=role_inst_map) for spec in ROLE_SPECS],
        "secrets_notice": "Raw role tokens are local secrets. Anyone who can read this file can use the listed local role scopes.",
    }


def connection_path(workspace_root: Path, *, connection_target: Path | None = None) -> Path:
    return _resolve_connection_target(workspace_root, connection_target)


def service_state_path(workspace_root: Path, *, connection_target: Path | None = None) -> Path:
    return _resolve_connection_target(workspace_root, connection_target).parent / "service_state.json"


def write_connection_config(
    workspace_root: Path, config: dict[str, Any], *, connection_target: Path | None = None
) -> Path:
    ensure_local_dir(workspace_root, connection_target=connection_target)
    # Backstop: if the workspace itself is being versioned, keep its .gitignore ignoring the
    # legacy in-workspace local dir. (Tokens now live in the runtime root by default.)
    try:
        ensure_workspace_gitignore(workspace_root)
    except OSError:
        pass
    path = connection_path(workspace_root, connection_target=connection_target)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, REQUIRED_CONNECTION_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
            # AIPOS-272 FIX-1: flush + fsync before returning so子进程 spawn 后读取时文件已完整可读。
            # 首启竞态:write 返回但 OS 缓存未落盘 → board/mcp 启动时读空/旧 registry → 401/崩溃。
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if os.name == "posix" and not _is_probably_non_posix(path):
            os.chmod(path, REQUIRED_CONNECTION_MODE)
    return path


def load_connection_config(workspace_root: Path, *, connection_target: Path | None = None) -> dict[str, Any]:
    path = connection_path(workspace_root, connection_target=connection_target)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Lybra service connection config must be an object: {path}")
    return data


def redacted_connection(config: dict[str, Any]) -> dict[str, Any]:
    safe_tokens = []
    for token in config.get("tokens", []) if isinstance(config.get("tokens"), list) else []:
        if not isinstance(token, dict):
            continue
        safe = {
            "role": token.get("role"),
            "token_ref": token.get("token_ref"),
            "scopes": list(token.get("scopes") or []),
            "fingerprint": token.get("fingerprint") or secret_fingerprint(str(token.get("token") or "")),
        }
        # AIPOS-228/229: echo the `projects` dimension + its enforcement marker — ONLY when
        # present, so tokens without it stay byte-identical. As of Slice 5 the gate enforces it.
        if token.get("projects"):
            safe["projects"] = list(token.get("projects") or [])
            safe["projects_enforced"] = True
        safe_tokens.append(safe)
    return {
        "mode": config.get("mode"),
        "workspace_root": config.get("workspace_root"),
        "local_only": config.get("local_only"),
        "board": config.get("board"),
        "mcp": config.get("mcp"),
        "tokens": safe_tokens,
        "secrets_notice": "Raw tokens are not printed. Read ~/.lybra/local/connection.json only from trusted local clients.",
    }


def render_connection_table(report: dict[str, Any]) -> str:
    connection = report.get("connection") if isinstance(report.get("connection"), dict) else {}
    board = connection.get("board") if isinstance(connection.get("board"), dict) else {}
    mcp = connection.get("mcp") if isinstance(connection.get("mcp"), dict) else {}
    lines = [
        "Lybra service mode",
        "",
        f"Workspace: {connection.get('workspace_root') or report.get('workspace_root')}",
        f"Board: {board.get('url') or '(missing)'}",
        f"MCP:   {mcp.get('rpc_url') or '(missing)'}",
        "",
        "Role             Scopes                         Token ref              Fingerprint",
    ]
    for token in connection.get("tokens", []) if isinstance(connection.get("tokens"), list) else []:
        scopes = ", ".join(str(item) for item in token.get("scopes", []))
        lines.append(f"{str(token.get('role') or ''):<16} {scopes:<30} {str(token.get('token_ref') or ''):<22} {token.get('fingerprint') or '(missing)'}")
    if report.get("warnings"):
        lines.extend(["", "Warnings:"])
        for warning in report["warnings"]:
            lines.append(f"- {warning.get('message')}")
            lines.append(f"  fix: {warning.get('fix_command')}")
    if report.get("blocking_reasons"):
        lines.extend(["", "Blocking:"])
        for reason in report["blocking_reasons"]:
            lines.append(f"- {reason.get('message')}")
            lines.append(f"  fix: {reason.get('fix_command')}")
    runtime_loc = report.get("connection_path") or "~/.lybra/local/connection.json"
    lines.extend(["", f"Local config: {runtime_loc}", "Raw tokens are not printed."])
    return "\n".join(lines)


def status_report(workspace_root: Path, *, connection_target: Path | None = None) -> dict[str, Any]:
    if connection_target is None:
        connection_target = runtime_connection_path()
    warnings, blocking = [], []
    permission_blocks, permission_warnings = check_service_permissions(
        workspace_root, for_secret_use=False, connection_target=connection_target
    )
    warnings.extend(permission_warnings)
    warnings.extend(permission_blocks)
    warnings.extend(_proxy_loopback_warnings())
    config: dict[str, Any] | None = None
    path = connection_path(workspace_root, connection_target=connection_target)
    if path.exists():
        config = load_connection_config(workspace_root, connection_target=connection_target)
    state: dict[str, Any] = {}
    state_path = service_state_path(workspace_root, connection_target=connection_target)
    if state_path.exists():
        try:
            parsed = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                state = parsed
        except json.JSONDecodeError as exc:
            warnings.append({"message": f"Could not parse service state: {exc}", "path": str(state_path)})
    return {
        "operation": "serve_status",
        "ok": not blocking,
        "verdict": "PASS",
        "workspace_root": str(workspace_root),
        "connection_path": str(path),
        "connection": redacted_connection(config) if config else None,
        "service_state": redacted_service_state(state),
        "warnings": warnings,
        "blocking_reasons": blocking,
        "secrets_notice": "Status never prints raw role tokens.",
    }


def redacted_service_state(state: dict[str, Any]) -> dict[str, Any]:
    processes = []
    for proc in state.get("processes", []) if isinstance(state.get("processes"), list) else []:
        if isinstance(proc, dict):
            processes.append({key: proc.get(key) for key in ("name", "pid", "service_owned", "started_at")})
    return {
        "mode": state.get("mode"),
        "started_at": state.get("started_at"),
        "processes": processes,
    }


def rotate_report(
    workspace_root: Path,
    *,
    board_host: str,
    board_port: int,
    mcp_host: str,
    mcp_port: int,
    connection_target: Path | None = None,
    project: str | None = None,
    executor_instance: str | None = None,
    role_instances: dict[str, str] | None = None,
    board_advertise_host: str | None = None,
    mcp_advertise_host: str | None = None,
) -> dict[str, Any]:
    if connection_target is None:
        connection_target = runtime_connection_path()
    blocking, warnings = check_service_permissions(
        workspace_root, for_secret_use=True, connection_target=connection_target
    )
    if blocking:
        return _blocked("serve_rotate", workspace_root, blocking, warnings, connection_target=connection_target)
    
    # AIPOS-316 S1.3: detect existing instance bindings that would be lost
    conn_path = connection_path(workspace_root, connection_target=connection_target)
    if conn_path.exists():
        try:
            existing_config = load_connection_config(workspace_root, connection_target=connection_target)
            existing_tokens = existing_config.get("tokens", []) if isinstance(existing_config, dict) else []
            
            # Collect existing bindings
            existing_bindings = {}
            for token in existing_tokens:
                if isinstance(token, dict) and "agent_instance" in token:
                    role = token.get("role")
                    if role:
                        existing_bindings[role] = token["agent_instance"]
            
            # Check if rotation would lose bindings
            if existing_bindings:
                # Build new bindings map
                new_bindings = dict(role_instances) if role_instances else {}
                if executor_instance:
                    new_bindings["executor"] = executor_instance
                
                # Find bindings that would be lost
                lost_bindings = {}
                for role, instance in existing_bindings.items():
                    if role not in new_bindings:
                        lost_bindings[role] = instance
                
                if lost_bindings:
                    lost_list = ", ".join(f"{role}={instance}" for role, instance in sorted(lost_bindings.items()))
                    blocking.append(
                        f"serve rotate would lose existing instance bindings: {lost_list}. "
                        f"PreAuthorized autonomy would become unavailable for these roles. "
                        f"Specify --executor-instance and/or --role-instance to preserve bindings, "
                        f"or confirm this is intentional (e.g., rotating to unbind for testing)."
                    )
                    return _blocked("serve_rotate", workspace_root, blocking, warnings, connection_target=connection_target)
        except Exception:
            # Best effort: if we can't read existing config, proceed (file may be corrupted)
            pass
    
    # AIPOS-259 (F-258-2): fail-closed before minting — a wildcard bind with no usable advertise
    # would write 0.0.0.0 URLs no client can dial. rotate rebuilds fresh, so no stored values apply.
    board_bind, board_adv, board_block = _resolve_bind_advertise(
        label="board", workspace_root=workspace_root, param_host=board_host,
        param_advertise=board_advertise_host, stored_host=None, stored_advertise=None,
        default_host=DEFAULT_BOARD_HOST,
    )
    mcp_bind, mcp_adv, mcp_block = _resolve_bind_advertise(
        label="mcp", workspace_root=workspace_root, param_host=mcp_host,
        param_advertise=mcp_advertise_host, stored_host=None, stored_advertise=None,
        default_host=DEFAULT_MCP_HOST,
    )
    advertise_blocks = [b for b in (board_block, mcp_block) if b]
    if advertise_blocks:
        return _blocked("serve_rotate", workspace_root, advertise_blocks, warnings, connection_target=connection_target)
    previous_created = None
    if connection_path(workspace_root, connection_target=connection_target).exists():
        previous_created = load_connection_config(
            workspace_root, connection_target=connection_target
        ).get("created_at")
    config = build_connection_config(
        workspace_root, board_host=board_bind, board_port=board_port, mcp_host=mcp_bind, mcp_port=mcp_port,
        project=project, executor_instance=executor_instance, role_instances=role_instances,
        board_advertise_host=board_adv, mcp_advertise_host=mcp_adv,
    )
    if previous_created:
        config["created_at"] = previous_created
    config["rotated_at"] = _utc_now()
    write_connection_config(workspace_root, config, connection_target=connection_target)
    return {
        "operation": "serve_rotate",
        "ok": True,
        "verdict": "PASS",
        "workspace_root": str(workspace_root),
        "connection_path": str(connection_path(workspace_root, connection_target=connection_target)),
        "connection": redacted_connection(config),
        "warnings": warnings,
        "blocking_reasons": [],
        "secrets_notice": "Raw role tokens were written only to ~/.lybra/local/connection.json and are not printed.",
    }


def start_report(
    workspace_root: Path,
    *,
    board_host: str | None = None,
    board_port: int,
    mcp_host: str | None = None,
    mcp_port: int,
    start_processes: bool = True,
    connection_target: Path | None = None,
    board_advertise_host: str | None = None,
    mcp_advertise_host: str | None = None,
) -> dict[str, Any]:
    if connection_target is None:
        connection_target = runtime_connection_path()
    # AIPOS-258/259: --mcp-host/--board-host pass through to the child processes so the gate can
    # bind a tailnet address for cross-machine access. AIPOS-259 adds two fixes on top:
    #  (F-258-1) an explicit CLI host param now OVERRIDES a stored connection.json (the stored
    #    value used to win silently, zero warning); no param -> stored behavior is byte-identical
    #    (the file is not rewritten).
    #  (F-258-2) bind (child --host) and advertise (client URLs) are split: rpc_url/sse_url/board
    #    url carry the ADVERTISE host; a wildcard bind (0.0.0.0) REQUIRES an explicit advertise
    #    or the call BLOCKs fail-closed (a 0.0.0.0 URL is unusable by clients).
    blocking, warnings = check_service_permissions(
        workspace_root, for_secret_use=True, connection_target=connection_target
    )
    warnings.extend(_proxy_loopback_warnings())
    if blocking:
        return _blocked("serve_start", workspace_root, blocking, warnings, connection_target=connection_target)
    conn_exists = connection_path(workspace_root, connection_target=connection_target).exists()
    stored = (
        load_connection_config(workspace_root, connection_target=connection_target) if conn_exists else None
    )
    stored_board = stored.get("board") if isinstance(stored, dict) and isinstance(stored.get("board"), dict) else {}
    stored_mcp = stored.get("mcp") if isinstance(stored, dict) and isinstance(stored.get("mcp"), dict) else {}
    board_bind, board_adv, board_block = _resolve_bind_advertise(
        label="board", workspace_root=workspace_root, param_host=board_host,
        param_advertise=board_advertise_host, stored_host=stored_board.get("host"),
        stored_advertise=stored_board.get("advertise_host"), default_host=DEFAULT_BOARD_HOST,
    )
    mcp_bind, mcp_adv, mcp_block = _resolve_bind_advertise(
        label="mcp", workspace_root=workspace_root, param_host=mcp_host,
        param_advertise=mcp_advertise_host, stored_host=stored_mcp.get("host"),
        stored_advertise=stored_mcp.get("advertise_host"), default_host=DEFAULT_MCP_HOST,
    )
    advertise_blocks = [b for b in (board_block, mcp_block) if b]
    if advertise_blocks:
        return _blocked("serve_start", workspace_root, advertise_blocks, warnings, connection_target=connection_target)
    # F-258-1: a host/advertise param appearing -> it overrides the stored value and is written
    # back (the process leaves a trail: `serve status` then shows the new host). No param at all
    # -> the stored config is untouched (byte-identical, S1).
    host_param_given = any(p is not None for p in (board_host, mcp_host, board_advertise_host, mcp_advertise_host))
    if conn_exists:
        config = stored
        if host_param_given:
            config["board"] = _board_block(board_bind, board_adv, int(stored_board.get("port") or DEFAULT_BOARD_PORT))
            config["mcp"] = _mcp_block(mcp_bind, mcp_adv, int(stored_mcp.get("port") or DEFAULT_MCP_PORT))
            write_connection_config(workspace_root, config, connection_target=connection_target)
    else:
        config = build_connection_config(
            workspace_root,
            board_host=board_bind,
            board_port=board_port,
            mcp_host=mcp_bind,
            mcp_port=mcp_port,
            board_advertise_host=board_adv,
            mcp_advertise_host=mcp_adv,
        )
        write_connection_config(workspace_root, config, connection_target=connection_target)
    if not start_processes:
        return {
            "operation": "serve_start",
            "ok": True,
            "verdict": "PASS",
            "workspace_root": str(workspace_root),
            "connection_path": str(connection_path(workspace_root, connection_target=connection_target)),
            "connection": redacted_connection(config),
            "service_state": None,
            "warnings": warnings,
            "blocking_reasons": [],
            "secrets_notice": "Raw role tokens were written only to ~/.lybra/local/connection.json and are not printed.",
        }
    return _run_supervisor(workspace_root, config, warnings=warnings, connection_target=connection_target)


def _build_child_commands(
    config: dict[str, Any],
    *,
    child_workspace_root: Path,
    connection_path_str: str,
) -> tuple[list[str], list[str]]:
    """Build the (board, mcp) child-process argv from the connection config.

    Pure/testable: AIPOS-258 asserts --mcp-host/--board-host reach the child --host flags
    without spawning a real subprocess. Defaults to DEFAULT_*_HOST when config omits a value
    (byte-identical to the prior inline construction).
    """
    board = config.get("board") if isinstance(config.get("board"), dict) else {}
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    board_cmd = [
        sys.executable,
        "-m",
        "web.board.app",
        "--host",
        str(board.get("host") or DEFAULT_BOARD_HOST),
        "--port",
        str(board.get("port") or DEFAULT_BOARD_PORT),
        "--repo-root",
        str(child_workspace_root),
    ]
    mcp_cmd = [
        sys.executable,
        "-m",
        "tools.mcp_server",
        "serve-http",
        "--host",
        str(mcp.get("host") or DEFAULT_MCP_HOST),
        "--port",
        str(mcp.get("port") or DEFAULT_MCP_PORT),
        "--service-connection-json",
        connection_path_str,
    ]
    return board_cmd, mcp_cmd


def _run_supervisor(
    workspace_root: Path,
    config: dict[str, Any],
    *,
    warnings: list[dict[str, Any]],
    connection_target: Path | None = None,
) -> dict[str, Any]:
    board = config.get("board") if isinstance(config.get("board"), dict) else {}
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    child_workspace_root = Path(str(config.get("workspace_root") or workspace_root)).expanduser().resolve()
    env = os.environ.copy()
    env["AIPOS_WORKSPACE_ROOT"] = str(child_workspace_root)
    board_cmd, mcp_cmd = _build_child_commands(
        config,
        child_workspace_root=child_workspace_root,
        connection_path_str=str(connection_path(workspace_root, connection_target=connection_target)),
    )
    # AIPOS-238 (F-o3-13 D1): refuse an already-OCCUPIED port up front. A stale serve still answering
    # would otherwise keep OLD tokens (→ downstream 401) while we falsely report success. Probe BOTH
    # board + mcp with a connect() active-listener test (see _ports_in_use).
    board_host_v = str(board.get("host") or DEFAULT_BOARD_HOST)
    board_port_v = int(board.get("port") or DEFAULT_BOARD_PORT)
    mcp_host_v = str(mcp.get("host") or DEFAULT_MCP_HOST)
    mcp_port_v = int(mcp.get("port") or DEFAULT_MCP_PORT)
    occupied = _ports_in_use([(board_host_v, board_port_v, "board"), (mcp_host_v, mcp_port_v, "mcp")])
    if occupied:
        names = ", ".join(f"{n} {h}:{p}" for h, p, n in occupied)
        return _blocked(
            "serve_start",
            workspace_root,
            [
                {
                    "message": (
                        f"Port already in use: {names}. A serve is likely already running — stop it "
                        "with `lybra serve stop`, or start on a different --mcp-port/--board-port."
                    ),
                    "path": str(workspace_root),
                    "fix_command": "lybra serve stop",
                }
            ],
            warnings,
            connection_target=connection_target,
        )

    processes: list[subprocess.Popen[Any]] = []
    started_at = _utc_now()
    # AIPOS-238 (F-o3-13 B): reap children on SIGTERM/SIGHUP too, not just Ctrl-C (SIGINT). Without
    # this, a plain `kill` / script exit / non-SIGINT trap orphans board+mcp (they keep the ports with
    # old tokens). We route the signals into the SAME KeyboardInterrupt cleanup path — pure shutdown
    # cleanup, NOT a daemon — and restore the prior handlers afterward.
    prev_handlers: dict[int, Any] = {}

    def _shutdown(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    for _sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if _sig is None:
            continue
        try:
            prev_handlers[_sig] = signal.signal(_sig, _shutdown)
        except (ValueError, OSError):
            pass  # not the main thread / unsupported platform — best-effort
    # AIPOS-272 FIX-1: supervisor 子进程重拉机制（有界退避），防止首启竞态导致子进程崩溃后不重启。
    # 重启策略：每个子进程独立计数，最多重启 5 次，退避延迟 1/2/4/8/16 秒。systemd 模式不冲突（该逻辑
    # 仅在前台 demo 模式生效；systemd 有自己的 Restart=）。
    MAX_RESTARTS = 5
    BACKOFF_DELAYS = [1, 2, 4, 8, 16]
    restart_counts = {"board": 0, "mcp": 0}
    child_specs = [("board", board_cmd), ("mcp", mcp_cmd)]
    should_exit = False
    try:
        processes.append(subprocess.Popen(board_cmd, env=env))
        processes.append(subprocess.Popen(mcp_cmd, env=env))
        state = {
            "mode": SERVICE_MODE,
            "started_at": started_at,
            "connection_path": str(connection_path(workspace_root, connection_target=connection_target)),
            "processes": [
                {"name": "board", "pid": processes[0].pid, "service_owned": True, "started_at": started_at},
                {"name": "mcp", "pid": processes[1].pid, "service_owned": True, "started_at": started_at},
            ],
        }
        _write_service_state(workspace_root, state, connection_target=connection_target)
        print(render_connection_table({"workspace_root": str(workspace_root), "connection": redacted_connection(config), "warnings": warnings, "blocking_reasons": []}))
        while not should_exit:
            for i, (name, cmd) in enumerate(child_specs):
                proc = processes[i]
                if proc.poll() is not None:
                    # 子进程已退出
                    if restart_counts[name] >= MAX_RESTARTS:
                        # 达到重启上限，标记退出
                        print(f"Warning: {name} exited and reached restart limit ({MAX_RESTARTS})", file=sys.stderr)
                        should_exit = True
                        break
                    # 退避延迟
                    delay = BACKOFF_DELAYS[min(restart_counts[name], len(BACKOFF_DELAYS) - 1)]
                    print(f"Warning: {name} (pid {proc.pid}) exited with code {proc.returncode}; restarting in {delay}s (attempt {restart_counts[name] + 1}/{MAX_RESTARTS})...", file=sys.stderr)
                    time.sleep(delay)
                    # 重启
                    new_proc = subprocess.Popen(cmd, env=env)
                    processes[i] = new_proc
                    restart_counts[name] += 1
                    print(f"{name} restarted as pid {new_proc.pid}", file=sys.stderr)
            if not should_exit:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _terminate_processes(processes)
        for _sig, _handler in prev_handlers.items():
            try:
                signal.signal(_sig, _handler)
            except (ValueError, OSError):
                pass
    # AIPOS-238 (F-o3-13 C1b/D2): classify by the child's EXIT REASON. A self non-zero exit
    # (returncode > 0 — a real crash / bind failure) → BLOCK. Killed-by-signal (returncode < 0, our
    # own _terminate_processes OR an external `serve stop` that killed the children directly) or a
    # clean 0 → PASS. Exit-reason covers external stop with no flag needed.
    crashed = [
        (name, proc.returncode)
        for name, proc in zip(("board", "mcp"), processes)
        if proc.returncode is not None and proc.returncode > 0
    ]
    if crashed:
        detail = ", ".join(f"{name} exit={rc}" for name, rc in crashed)
        return _blocked(
            "serve_start",
            workspace_root,
            [
                {
                    "message": (
                        f"serve child exited abnormally ({detail}) — likely a bind failure or startup "
                        "error. Check the serve log; if a stale serve holds the port, `lybra serve stop`."
                    ),
                    "path": str(workspace_root),
                    "fix_command": "lybra serve stop",
                }
            ],
            warnings,
            connection_target=connection_target,
        )
    return {
        "operation": "serve_start",
        "ok": True,
        "verdict": "PASS",
        "workspace_root": str(workspace_root),
        "connection": redacted_connection(config),
        "supervisor_printed": True,
        "warnings": warnings,
        "blocking_reasons": [],
    }


def _write_service_state(
    workspace_root: Path, state: dict[str, Any], *, connection_target: Path | None = None
) -> None:
    ensure_local_dir(workspace_root, connection_target=connection_target)
    path = service_state_path(workspace_root, connection_target=connection_target)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name == "posix" and not _is_probably_non_posix(path):
        os.chmod(path, REQUIRED_CONNECTION_MODE)


def stop_report(workspace_root: Path, *, connection_target: Path | None = None) -> dict[str, Any]:
    if connection_target is None:
        connection_target = runtime_connection_path()
    path = service_state_path(workspace_root, connection_target=connection_target)
    warnings: list[dict[str, Any]] = []
    stopped: list[dict[str, Any]] = []
    if not path.exists():
        return {"operation": "serve_stop", "ok": True, "verdict": "PASS", "workspace_root": str(workspace_root), "stopped": [], "warnings": [{"message": "No service_state.json found; nothing to stop."}], "blocking_reasons": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    processes = data.get("processes") if isinstance(data, dict) else []
    for proc in processes if isinstance(processes, list) else []:
        if not isinstance(proc, dict) or not proc.get("service_owned"):
            continue
        pid = int(proc.get("pid") or 0)
        if pid <= 0:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append({"name": proc.get("name"), "pid": pid, "signal": "SIGTERM"})
        except ProcessLookupError:
            warnings.append({"message": f"Process already exited: {pid}", "pid": pid})
        except PermissionError:
            warnings.append({"message": f"Permission denied stopping service-owned process: {pid}", "pid": pid})
    return {"operation": "serve_stop", "ok": True, "verdict": "PASS", "workspace_root": str(workspace_root), "stopped": stopped, "warnings": warnings, "blocking_reasons": []}


def _ports_in_use(targets: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """AIPOS-238 (F-o3-13 D1): which of (host, port, name) already have an ACTIVE listener.

    Uses a `connect()` probe — an active-listener test that matches "is an old serve still
    answering?" and is immune to `TIME_WAIT` on a just-stopped port. (A `SO_REUSEADDR`-off bind probe
    would false-BLOCK a legit restart, because the real server sets `allow_reuse_address`.)
    """
    occupied: list[tuple[str, int, str]] = []
    for host, port, name in targets:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect((host, port))
            occupied.append((host, port, name))  # connected → someone is listening
        except OSError:
            pass  # refused / no listener → free
        finally:
            probe.close()
    return occupied


def _terminate_processes(processes: list[subprocess.Popen[Any]]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _blocked(
    operation: str,
    workspace_root: Path,
    blocking: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    connection_target: Path | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "ok": False,
        "verdict": "BLOCK",
        "workspace_root": str(workspace_root),
        "connection_path": str(connection_path(workspace_root, connection_target=connection_target)),
        "connection": None,
        "warnings": warnings,
        "blocking_reasons": blocking,
        "secrets_notice": "Raw tokens are not printed. Fix local secret file permissions before using service mode tokens.",
    }
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
