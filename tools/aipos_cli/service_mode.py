from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
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
WORKSPACE_GITIGNORE_REL = Path(".gitignore")
REQUIRED_LOCAL_DIR_MODE = 0o700
REQUIRED_CONNECTION_MODE = 0o600
SERVICE_MODE_VERSION = 1
SERVICE_MODE = "service_v0"

ROLE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "role": "executor",
        "token_ref": "svc-executor",
        "scopes": ["queue_claim", "queue_return"],
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


def check_service_permissions(workspace_root: Path, *, for_secret_use: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    local_dir = workspace_root / LOCAL_DIR_REL
    connection_path = workspace_root / CONNECTION_REL
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


def ensure_local_dir(workspace_root: Path) -> Path:
    local_dir = workspace_root / LOCAL_DIR_REL
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


def _role_token_entry(spec: dict[str, Any]) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    return {
        "role": spec["role"],
        "token_ref": spec["token_ref"],
        "scopes": list(spec["scopes"]),
        "fingerprint": secret_fingerprint(token),
        "token": token,
    }


def build_connection_config(workspace_root: Path, *, board_host: str, board_port: int, mcp_host: str, mcp_port: int) -> dict[str, Any]:
    now = _utc_now()
    return {
        "config_version": SERVICE_MODE_VERSION,
        "mode": SERVICE_MODE,
        "workspace_root": str(workspace_root),
        "local_only": True,
        "created_at": now,
        "rotated_at": None,
        "board": {"url": f"http://{board_host}:{board_port}", "host": board_host, "port": board_port},
        "mcp": {
            "rpc_url": f"http://{mcp_host}:{mcp_port}/mcp",
            "sse_url": f"http://{mcp_host}:{mcp_port}/sse",
            "host": mcp_host,
            "port": mcp_port,
        },
        "tokens": [_role_token_entry(spec) for spec in ROLE_SPECS],
        "secrets_notice": "Raw role tokens are local secrets. Anyone who can read this file can use the listed local role scopes.",
    }


def connection_path(workspace_root: Path) -> Path:
    return workspace_root / CONNECTION_REL


def service_state_path(workspace_root: Path) -> Path:
    return workspace_root / SERVICE_STATE_REL


def write_connection_config(workspace_root: Path, config: dict[str, Any]) -> Path:
    ensure_local_dir(workspace_root)
    ensure_workspace_gitignore(workspace_root)
    path = connection_path(workspace_root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, REQUIRED_CONNECTION_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
    finally:
        if os.name == "posix" and not _is_probably_non_posix(path):
            os.chmod(path, REQUIRED_CONNECTION_MODE)
    return path


def load_connection_config(workspace_root: Path) -> dict[str, Any]:
    path = connection_path(workspace_root)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Lybra service connection config must be an object: {path}")
    return data


def redacted_connection(config: dict[str, Any]) -> dict[str, Any]:
    safe_tokens = []
    for token in config.get("tokens", []) if isinstance(config.get("tokens"), list) else []:
        if not isinstance(token, dict):
            continue
        safe_tokens.append(
            {
                "role": token.get("role"),
                "token_ref": token.get("token_ref"),
                "scopes": list(token.get("scopes") or []),
                "fingerprint": token.get("fingerprint") or secret_fingerprint(str(token.get("token") or "")),
            }
        )
    return {
        "mode": config.get("mode"),
        "workspace_root": config.get("workspace_root"),
        "local_only": config.get("local_only"),
        "board": config.get("board"),
        "mcp": config.get("mcp"),
        "tokens": safe_tokens,
        "secrets_notice": "Raw tokens are not printed. Read .lybra/local/connection.json only from trusted local clients.",
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
    lines.extend(["", "Local config: .lybra/local/connection.json", "Raw tokens are not printed."])
    return "\n".join(lines)


def status_report(workspace_root: Path) -> dict[str, Any]:
    warnings, blocking = [], []
    permission_blocks, permission_warnings = check_service_permissions(workspace_root, for_secret_use=False)
    warnings.extend(permission_warnings)
    warnings.extend(permission_blocks)
    config: dict[str, Any] | None = None
    path = connection_path(workspace_root)
    if path.exists():
        config = load_connection_config(workspace_root)
    state: dict[str, Any] = {}
    state_path = service_state_path(workspace_root)
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


def rotate_report(workspace_root: Path, *, board_host: str, board_port: int, mcp_host: str, mcp_port: int) -> dict[str, Any]:
    blocking, warnings = check_service_permissions(workspace_root, for_secret_use=True)
    if blocking:
        return _blocked("serve_rotate", workspace_root, blocking, warnings)
    previous_created = None
    if connection_path(workspace_root).exists():
        previous_created = load_connection_config(workspace_root).get("created_at")
    config = build_connection_config(workspace_root, board_host=board_host, board_port=board_port, mcp_host=mcp_host, mcp_port=mcp_port)
    if previous_created:
        config["created_at"] = previous_created
    config["rotated_at"] = _utc_now()
    write_connection_config(workspace_root, config)
    return {
        "operation": "serve_rotate",
        "ok": True,
        "verdict": "PASS",
        "workspace_root": str(workspace_root),
        "connection_path": str(connection_path(workspace_root)),
        "connection": redacted_connection(config),
        "warnings": warnings,
        "blocking_reasons": [],
        "secrets_notice": "Raw role tokens were written only to .lybra/local/connection.json and are not printed.",
    }


def start_report(
    workspace_root: Path,
    *,
    board_host: str,
    board_port: int,
    mcp_host: str,
    mcp_port: int,
    start_processes: bool = True,
) -> dict[str, Any]:
    if board_host != "127.0.0.1" or mcp_host != "127.0.0.1":
        return _blocked(
            "serve_start",
            workspace_root,
            [
                {
                    "message": "Service mode v0 is loopback-only; host must be 127.0.0.1.",
                    "path": str(workspace_root),
                    "fix_command": "Use --board-host 127.0.0.1 and --mcp-host 127.0.0.1.",
                }
            ],
            [],
        )
    blocking, warnings = check_service_permissions(workspace_root, for_secret_use=True)
    if blocking:
        return _blocked("serve_start", workspace_root, blocking, warnings)
    config = load_connection_config(workspace_root) if connection_path(workspace_root).exists() else build_connection_config(
        workspace_root,
        board_host=board_host,
        board_port=board_port,
        mcp_host=mcp_host,
        mcp_port=mcp_port,
    )
    if not connection_path(workspace_root).exists():
        write_connection_config(workspace_root, config)
    if not start_processes:
        return {
            "operation": "serve_start",
            "ok": True,
            "verdict": "PASS",
            "workspace_root": str(workspace_root),
            "connection_path": str(connection_path(workspace_root)),
            "connection": redacted_connection(config),
            "service_state": None,
            "warnings": warnings,
            "blocking_reasons": [],
            "secrets_notice": "Raw role tokens were written only to .lybra/local/connection.json and are not printed.",
        }
    return _run_supervisor(workspace_root, config, warnings=warnings)


def _run_supervisor(workspace_root: Path, config: dict[str, Any], *, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    board = config.get("board") if isinstance(config.get("board"), dict) else {}
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    env = os.environ.copy()
    env["AIPOS_WORKSPACE_ROOT"] = str(workspace_root)
    board_cmd = [
        sys.executable,
        "-m",
        "web.board.app",
        "--host",
        str(board.get("host") or DEFAULT_BOARD_HOST),
        "--port",
        str(board.get("port") or DEFAULT_BOARD_PORT),
        "--repo-root",
        str(workspace_root),
    ]
    mcp_cmd = [
        sys.executable,
        "-m",
        "tools.mcp_server.server",
        "serve-http",
        "--host",
        str(mcp.get("host") or DEFAULT_MCP_HOST),
        "--port",
        str(mcp.get("port") or DEFAULT_MCP_PORT),
        "--service-connection-json",
        str(connection_path(workspace_root)),
    ]
    processes: list[subprocess.Popen[Any]] = []
    started_at = _utc_now()
    try:
        processes.append(subprocess.Popen(board_cmd, env=env))
        processes.append(subprocess.Popen(mcp_cmd, env=env))
        state = {
            "mode": SERVICE_MODE,
            "started_at": started_at,
            "connection_path": str(connection_path(workspace_root)),
            "processes": [
                {"name": "board", "pid": processes[0].pid, "service_owned": True, "started_at": started_at},
                {"name": "mcp", "pid": processes[1].pid, "service_owned": True, "started_at": started_at},
            ],
        }
        _write_service_state(workspace_root, state)
        print(render_connection_table({"workspace_root": str(workspace_root), "connection": redacted_connection(config), "warnings": warnings, "blocking_reasons": []}))
        while True:
            exited = [proc for proc in processes if proc.poll() is not None]
            if exited:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _terminate_processes(processes)
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


def _write_service_state(workspace_root: Path, state: dict[str, Any]) -> None:
    ensure_local_dir(workspace_root)
    path = service_state_path(workspace_root)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name == "posix" and not _is_probably_non_posix(path):
        os.chmod(path, REQUIRED_CONNECTION_MODE)


def stop_report(workspace_root: Path) -> dict[str, Any]:
    path = service_state_path(workspace_root)
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


def _terminate_processes(processes: list[subprocess.Popen[Any]]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _blocked(operation: str, workspace_root: Path, blocking: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "operation": operation,
        "ok": False,
        "verdict": "BLOCK",
        "workspace_root": str(workspace_root),
        "connection_path": str(connection_path(workspace_root)),
        "connection": None,
        "warnings": warnings,
        "blocking_reasons": blocking,
        "secrets_notice": "Raw tokens are not printed. Fix local secret file permissions before using service mode tokens.",
    }
