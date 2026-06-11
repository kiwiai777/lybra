"""AIPOS-196b Layer 2 confined autonomous worker (local_docker).

Confines a full-capability agent harness (Claude Code) inside a one-shot Docker
worker whose only consequential write path into Lybra truth is the MCP gate. The
worker gets a read-only context projection and a writable scratch directory; it
cannot write Lybra truth, `.lybra`, the product repo, or the host filesystem
because those paths are simply never mounted.

This module is a shell-free argv / mount / report builder plus a guarded one-shot
runner with teardown. It is NOT a runtime, scheduler, poller, heartbeat, daemon,
or auto-restart loop: it builds and runs exactly one explicitly requested worker
and then tears it down. It does not modify the AIPOS-101 `local_docker.py` MVP or
the AIPOS-196a `artifact_ingest.py` ingestion path.

Protocol: 0_control_plane/environments/confined_autonomous_worker_boundary_protocol.md
(AIPOS-195 sections 5/6/8/13/14).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.aipos_cli.service_mode import secret_fingerprint

PROVIDER = "local_docker"
EXECUTION_MODEL = "confined_autonomous_worker"

PROJECTION_TARGET = "/projection"
SCRATCH_TARGET = "/scratch"
MCP_CONFIG_TARGET = "/etc/lybra/mcp.json"
SCRATCH_HOST_ENV = "LYBRA_SCRATCH_HOST_DIR"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

# Network modes that defeat the dedicated-bridge posture must be refused.
_FORBIDDEN_NETWORKS = {"", "none", "host", "bridge", "container"}
# Truth / control-plane / product roots that must never be a bind-mount source.
_TRUTH_PREFIXES = ("5_tasks", ".lybra")


class ConfinedWorkerError(ValueError):
    """Raised when a confined-worker request violates the Layer 2 boundary."""


@dataclass(frozen=True)
class ConfinedWorkerRequest:
    image: str
    prompt: str
    run_id: str
    network: str
    gate_url: str
    approved_scratch_root: Path
    scratch_run_dir: Path
    projection_dir: Path
    mcp_config_path: Path
    mcp_token: str
    mcp_token_fingerprint: str
    gate_ip: str | None = None
    anthropic_key_present: bool = False
    anthropic_key_fingerprint: str | None = None
    allowed_tools: str = "mcp__lybra__*"
    timeout_seconds: int = 900
    cpus: str | None = None
    memory: str | None = None
    pids_limit: int = 512
    repo_root: Path | None = None
    dry_run: bool = False
    claim_task_id: str | None = None


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def generate_run_id() -> str:
    return f"cw_{_utc_compact()}_{secrets.token_hex(4)}"


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def no_proxy_value(gate_ip: str | None) -> str:
    parts = ["127.0.0.1", "localhost", "::1"]
    if gate_ip and gate_ip not in parts:
        parts.append(gate_ip)
    return ",".join(parts)


# --------------------------------------------------------------------------- #
# Credentials                                                                  #
# --------------------------------------------------------------------------- #

def load_role_tokens(connection_json: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(connection_json).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tokens"), list):
        raise ConfinedWorkerError(f"connection.json has no usable tokens: {connection_json}")
    return [item for item in data["tokens"] if isinstance(item, dict)]


def executor_token(connection_json: Path) -> str:
    for item in load_role_tokens(connection_json):
        if str(item.get("role") or "") == "executor":
            token = str(item.get("token") or "").strip()
            if not token:
                raise ConfinedWorkerError("executor role token is empty in connection.json")
            return token
    raise ConfinedWorkerError("no executor role token found in connection.json")


def all_raw_secrets(connection_json: Path) -> list[str]:
    """Every raw role token in connection.json — used to scan the projection."""
    secrets_found = []
    for item in load_role_tokens(connection_json):
        token = str(item.get("token") or "").strip()
        if token:
            secrets_found.append(token)
    return secrets_found


# --------------------------------------------------------------------------- #
# MCP client config                                                           #
# --------------------------------------------------------------------------- #

def build_mcp_client_config(gate_url: str, token: str) -> dict[str, Any]:
    return {
        "mcpServers": {
            "lybra": {
                "type": "http",
                "url": gate_url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }


def write_mcp_config_file(path: Path, config: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, sort_keys=True)
            handle.write("\n")
    finally:
        if os.name == "posix":
            os.chmod(path, 0o600)
    return path


# --------------------------------------------------------------------------- #
# Projection                                                                  #
# --------------------------------------------------------------------------- #

def render_projection(context_pack: dict[str, Any], dest: Path) -> list[str]:
    """Render a minimal read-only projection from a context-pack preview dict.

    v0: the task card fields and a declared-input summary only. No secrets, no
    records, no other tasks' queue, no product repo. Returns written rel names.
    """
    dest.mkdir(parents=True, exist_ok=True)
    task = context_pack.get("task") if isinstance(context_pack.get("task"), dict) else {}
    bundle = context_pack.get("context_bundle") if isinstance(context_pack.get("context_bundle"), dict) else {}
    safe_task = {
        key: task.get(key)
        for key in (
            "task_id",
            "title",
            "task_mode",
            "model_tier",
            "priority",
            "status",
            "context_bundle_ref",
            "acceptance_criteria",
            "path",
        )
        if key in task
    }
    summary = {
        "projection_kind": "lybra_confined_worker_projection_v0",
        "writes_enabled": False,
        "scope": context_pack.get("scope"),
        "task": safe_task,
        "context_bundle_ref": bundle.get("ref"),
        "source_refs": [str(ref) for ref in context_pack.get("source_refs", []) if str(ref)],
        "notice": (
            "Read-only context projection. The only consequential write path is the "
            "Lybra MCP gate. Write scratch to /scratch; pass the host scratch dir "
            f"(env {SCRATCH_HOST_ENV}) to queue_return so the gate ingests it."
        ),
    }
    (dest / "context_pack.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Confined worker projection — {safe_task.get('task_id') or 'unknown'}",
        "",
        f"- Title: {safe_task.get('title') or ''}",
        f"- Task mode: {safe_task.get('task_mode') or ''}",
        f"- Status: {safe_task.get('status') or ''}",
        "",
        "This is a read-only projection. You cannot write Lybra truth directly.",
        "Write outputs to /scratch, then return them through the MCP gate "
        "(queue_return) so the gate ingests them into workspace_artifacts/.",
        "",
    ]
    (dest / "TASK.md").write_text("\n".join(lines), encoding="utf-8")
    return ["context_pack.json", "TASK.md"]


def assert_no_secrets(directory: Path, secrets_to_scan: Sequence[str]) -> None:
    """Abort if any raw secret literal appears anywhere under `directory`."""
    needles = [s for s in secrets_to_scan if s]
    if not needles:
        return
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in needles:
            if needle in text:
                raise ConfinedWorkerError(
                    f"projection leak: a raw secret value appears in {path.relative_to(directory)}"
                )


def build_projection(
    repo_root: Path,
    dest: Path,
    *,
    task_id: str | None = None,
    task_path: str | None = None,
    connection_json: Path | None = None,
    anthropic_key: str | None = None,
) -> dict[str, Any]:
    from tools.aipos_cli.context_pack_builder import build_context_pack_preview

    context_pack = build_context_pack_preview(repo_root, task_id=task_id, path=task_path)
    written = render_projection(context_pack, dest)
    scan: list[str] = []
    if connection_json is not None:
        scan.extend(all_raw_secrets(connection_json))
    if anthropic_key:
        scan.append(anthropic_key)
    assert_no_secrets(dest, scan)
    return {"projection_dir": str(dest), "files": written, "context_pack_verdict": context_pack.get("verdict")}


# --------------------------------------------------------------------------- #
# Docker argv                                                                 #
# --------------------------------------------------------------------------- #

def _readonly_mount(source: Path, target: str) -> str:
    return f"type=bind,source={Path(source).resolve()},target={target},readonly"


def _writable_mount(source: Path, target: str) -> str:
    return f"type=bind,source={Path(source).resolve()},target={target}"


def validate_request(request: ConfinedWorkerRequest) -> None:
    if not request.image.strip():
        raise ConfinedWorkerError("an explicit docker image is required")
    if not request.prompt.strip():
        raise ConfinedWorkerError("a worker prompt is required")
    if request.network in _FORBIDDEN_NETWORKS:
        raise ConfinedWorkerError(
            f"network must be a dedicated user-defined bridge, not '{request.network}' "
            "(host/none/default-bridge defeat the confined-worker posture)"
        )
    if not request.gate_url.strip():
        raise ConfinedWorkerError("gate_url is required so the worker can reach the MCP gate")
    if request.gate_url.startswith(("http://0.0.0.0", "https://0.0.0.0")):
        raise ConfinedWorkerError("gate_url must not point at 0.0.0.0; the gate must stay non-public")
    if not request.mcp_token.strip():
        raise ConfinedWorkerError("executor MCP token is required")
    if request.timeout_seconds <= 0:
        raise ConfinedWorkerError("timeout_seconds must be positive")

    approved = request.approved_scratch_root.resolve()
    scratch = request.scratch_run_dir.resolve()
    if scratch != (approved / request.run_id).resolve() or not _is_within(approved, scratch):
        raise ConfinedWorkerError("scratch_run_dir must be <approved_scratch_root>/<run_id>")

    # No bind-mount source may sit inside Lybra truth, .lybra, or the product repo.
    bind_sources = [request.projection_dir, request.scratch_run_dir, request.mcp_config_path]
    if request.repo_root is not None:
        repo_root = request.repo_root.resolve()
        for source in bind_sources:
            resolved = Path(source).resolve()
            if _is_within(repo_root, resolved):
                raise ConfinedWorkerError(f"bind-mount source must be outside the product repo: {resolved}")
        for prefix in _TRUTH_PREFIXES:
            truth = (repo_root / prefix).resolve()
            for source in bind_sources:
                if _is_within(truth, Path(source).resolve()):
                    raise ConfinedWorkerError(f"bind-mount source must be outside {prefix}: {source}")

    # The token file must not live inside scratch or projection (agent-visible).
    cfg = request.mcp_config_path.resolve()
    if _is_within(approved, cfg) or _is_within(request.projection_dir.resolve(), cfg):
        raise ConfinedWorkerError("mcp config file must live outside scratch and projection")


def build_docker_argv(request: ConfinedWorkerRequest) -> list[str]:
    validate_request(request)
    argv: list[str] = [
        "docker",
        "run",
        "--rm",
        "--network",
        request.network,
        "--pull",
        "never",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        str(request.pids_limit),
        "--read-only",
        "--tmpfs",
        "/tmp",
    ]
    if request.cpus:
        argv.extend(["--cpus", request.cpus])
    if request.memory:
        argv.extend(["--memory", request.memory])
    argv.extend(["--mount", _readonly_mount(request.projection_dir, PROJECTION_TARGET)])
    argv.extend(["--mount", _writable_mount(request.scratch_run_dir, SCRATCH_TARGET)])
    argv.extend(["--mount", _readonly_mount(request.mcp_config_path, MCP_CONFIG_TARGET)])
    # LLM key is passed by env passthrough so its value never appears in argv.
    argv.extend(["--env", ANTHROPIC_KEY_ENV])
    argv.extend(["--env", f"{SCRATCH_HOST_ENV}={request.scratch_run_dir.resolve()}"])
    argv.extend(["--env", f"NO_PROXY={no_proxy_value(request.gate_ip)}"])
    argv.extend(["--workdir", PROJECTION_TARGET])
    argv.append(request.image)
    argv.extend(
        [
            "claude",
            "-p",
            request.prompt,
            "--mcp-config",
            MCP_CONFIG_TARGET,
            "--allowedTools",
            request.allowed_tools,
        ]
    )
    return argv


# --------------------------------------------------------------------------- #
# Worker report                                                               #
# --------------------------------------------------------------------------- #

def build_worker_report(
    request: ConfinedWorkerRequest,
    *,
    docker_argv: list[str],
    executed: bool,
    status: str,
    exit_code: int | None,
    timed_out: bool,
    token_file_removed: bool,
    teardown_verified: bool,
) -> dict[str, Any]:
    return {
        "ok": (not executed) or (exit_code == 0),
        "provider": PROVIDER,
        "execution_model": EXECUTION_MODEL,
        "lifecycle": "ephemeral_one_shot",
        "run_id": request.run_id,
        "image": request.image,
        "dry_run": request.dry_run,
        "status": status,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "network": {
            "posture": "dedicated_bridge",
            "network": request.network,
            "gate_url": request.gate_url,
            "gate_ip": request.gate_ip,
            "gate_public": False,
            "llm_egress": "nat",
            "no_proxy": no_proxy_value(request.gate_ip),
        },
        "mounts": {
            "projection": {"source": str(request.projection_dir.resolve()), "target": PROJECTION_TARGET, "mode": "ro"},
            "scratch": {"source": str(request.scratch_run_dir.resolve()), "target": SCRATCH_TARGET, "mode": "rw"},
            "mcp_config": {"source": str(request.mcp_config_path.resolve()), "target": MCP_CONFIG_TARGET, "mode": "ro"},
        },
        "unmounted_truth_paths": ["5_tasks/**", "5_tasks/records/**", ".lybra/**", "product_repo", "host_fs"],
        "credentials": {
            "mcp_token_fingerprint": request.mcp_token_fingerprint,
            "mcp_token_injected_via": f"readonly_mount({MCP_CONFIG_TARGET})",
            "anthropic_key_fingerprint": request.anthropic_key_fingerprint,
            "anthropic_key_injected_via": f"env_passthrough({ANTHROPIC_KEY_ENV})",
            "baked_into_image": False,
            "in_projection": False,
            "in_scratch": False,
        },
        "scratch": {
            "host_dir": str(request.scratch_run_dir.resolve()),
            "container_dir": SCRATCH_TARGET,
            "approved_root": str(request.approved_scratch_root.resolve()),
            "mapping_env": SCRATCH_HOST_ENV,
            "ingestion": "queue_return confirm copies into workspace_artifacts/<task>/<return_id>/ (AIPOS-196a)",
        },
        "security": [
            "--rm",
            "no-new-privileges",
            "cap-drop ALL",
            "read-only rootfs",
            "tmpfs /tmp",
            f"pids-limit {request.pids_limit}",
            "no docker.sock mount",
            "no privileged",
            "no pid=host",
            "no root escalation",
        ],
        "docker_argv": docker_argv,
        "teardown": {
            "container_rm": True,
            "token_file_path": str(request.mcp_config_path.resolve()),
            "token_file_removed": token_file_removed,
            "verified": teardown_verified,
        },
    }


# --------------------------------------------------------------------------- #
# Run / teardown                                                              #
# --------------------------------------------------------------------------- #

def teardown_token_file(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not path.exists()


def run_confined_worker(request: ConfinedWorkerRequest) -> dict[str, Any]:
    docker_argv = build_docker_argv(request)
    if request.dry_run:
        return build_worker_report(
            request,
            docker_argv=docker_argv,
            executed=False,
            status="dry_run",
            exit_code=None,
            timed_out=False,
            token_file_removed=False,
            teardown_verified=False,
        )

    request.scratch_run_dir.mkdir(parents=True, exist_ok=True)
    write_mcp_config_file(request.mcp_config_path, build_mcp_client_config(request.gate_url, request.mcp_token))

    status = "completed"
    exit_code: int | None = None
    timed_out = False
    env = os.environ.copy()
    try:
        completed = subprocess.run(
            docker_argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=env,
        )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        status = "timeout"
        timed_out = True
    except FileNotFoundError:
        status = "docker_unavailable"
    finally:
        token_file_removed = teardown_token_file(request.mcp_config_path)
        teardown_verified = token_file_removed and not request.mcp_config_path.exists()

    return build_worker_report(
        request,
        docker_argv=docker_argv,
        executed=True,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        token_file_removed=token_file_removed,
        teardown_verified=teardown_verified,
    )


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def build_request_from_args(args: argparse.Namespace) -> ConfinedWorkerRequest:
    from tools.aipos_cli.task_loader import find_repo_root

    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root()
    run_id = args.run_id or generate_run_id()
    approved_scratch_root = Path(args.approved_scratch_root).expanduser().resolve()
    scratch_run_dir = approved_scratch_root / run_id

    tmp_root = Path(args.tmp_root).expanduser().resolve() if args.tmp_root else Path(args.approved_scratch_root).expanduser().resolve().parent / "lybra_worker_tmp"
    projection_dir = (tmp_root / run_id / "projection").resolve()
    mcp_config_dir = (tmp_root / run_id / "control").resolve()
    mcp_config_path = mcp_config_dir / f"{run_id}.json"

    connection_json = Path(args.connection_json).expanduser().resolve()
    token = executor_token(connection_json)

    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    anthropic_key = os.environ.get(ANTHROPIC_KEY_ENV, "")

    build_projection(
        repo_root,
        projection_dir,
        task_id=args.task_id,
        task_path=args.task_path,
        connection_json=connection_json,
        anthropic_key=anthropic_key or None,
    )

    return ConfinedWorkerRequest(
        image=args.image,
        prompt=prompt or "",
        run_id=run_id,
        network=args.network,
        gate_url=args.gate_url,
        gate_ip=args.gate_ip,
        approved_scratch_root=approved_scratch_root,
        scratch_run_dir=scratch_run_dir,
        projection_dir=projection_dir,
        mcp_config_path=mcp_config_path,
        mcp_token=token,
        mcp_token_fingerprint=secret_fingerprint(token),
        anthropic_key_present=bool(anthropic_key),
        anthropic_key_fingerprint=secret_fingerprint(anthropic_key) if anthropic_key else None,
        allowed_tools=args.allowed_tools,
        timeout_seconds=args.timeout,
        cpus=args.cpus,
        memory=args.memory,
        pids_limit=args.pids_limit,
        repo_root=repo_root,
        dry_run=args.dry_run,
        claim_task_id=args.task_id,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one Lybra Layer 2 confined autonomous worker (local_docker)")
    parser.add_argument("--image", required=True, help="Explicit docker image containing the Claude Code harness")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt", help="Worker prompt passed to claude -p")
    group.add_argument("--prompt-file", help="File whose contents are the worker prompt")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--task-id", help="Task id to project read-only")
    selector.add_argument("--task-path", help="Task path to project read-only")
    parser.add_argument("--connection-json", required=True, help="Service-mode connection.json (reads executor role token)")
    parser.add_argument("--approved-scratch-root", required=True, help="Host approved scratch root (= LYBRA_APPROVED_SCRATCH_ROOT)")
    parser.add_argument("--network", required=True, help="Dedicated user-defined docker bridge name")
    parser.add_argument("--gate-url", required=True, help="MCP gate URL reachable from the worker, e.g. http://172.18.0.1:7118/mcp")
    parser.add_argument("--gate-ip", help="Gate IP for NO_PROXY (e.g. 172.18.0.1)")
    parser.add_argument("--tmp-root", help="Controlled host temp root for projection + token file (outside repo, scratch, projection)")
    parser.add_argument("--run-id", help="Explicit run id; default generated")
    parser.add_argument("--allowed-tools", default="mcp__lybra__*", help="claude --allowedTools value")
    parser.add_argument("--timeout", type=int, default=900, help="Wall-clock timeout seconds")
    parser.add_argument("--cpus", help="Optional docker --cpus")
    parser.add_argument("--memory", help="Optional docker --memory")
    parser.add_argument("--pids-limit", type=int, default=512, help="docker --pids-limit")
    parser.add_argument("--repo-root", help="Lybra workspace root; default auto-detected")
    parser.add_argument("--dry-run", action="store_true", help="Build argv + report without running docker or writing secrets")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = build_request_from_args(args)
        report = run_confined_worker(request)
    except ConfinedWorkerError as exc:
        print(json.dumps({"ok": False, "status": "validation_error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
