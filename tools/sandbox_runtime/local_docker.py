from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


PROVIDER = "local_docker"
EXECUTION_MODEL = "ephemeral_worker"
DEFAULT_NETWORK = "none"


class SandboxValidationError(ValueError):
    """Raised when a sandbox request violates the MVP boundary."""


@dataclass(frozen=True)
class LocalDockerRequest:
    image: str
    command: tuple[str, ...]
    workspace: Path | None = None
    timeout_seconds: int = 60
    network: str = DEFAULT_NETWORK
    dry_run: bool = False
    cpus: str | None = None
    memory: str | None = None
    extra_readonly_mounts: tuple[Path, ...] = field(default_factory=tuple)


def provider_descriptor() -> dict[str, Any]:
    return {
        "adapter_id": "local_docker.default",
        "provider": PROVIDER,
        "provider_status": "mvp_enabled",
        "execution_model": EXECUTION_MODEL,
        "lifecycle": {
            "create": "implemented_for_ephemeral_run",
            "inject": "implemented_as_read_only_mounts",
            "execute": "implemented_as_bounded_docker_run",
            "report": "implemented_as_in_memory_structured_report",
            "destroy": "implemented_via_docker_rm_after_run",
        },
        "resource_limits": {
            "cpu_limit": "explicit_argument_optional",
            "memory_limit": "explicit_argument_optional",
            "disk_limit": "not_configured_in_mvp",
            "wall_clock_timeout": "required",
            "idle_timeout": "not_applicable_ephemeral_worker",
            "network_policy": "none_by_default",
        },
        "credential_boundary": {
            "credential_mode": "none",
            "token_scope": "not_implemented",
            "token_ttl": "not_implemented",
            "long_lived_env_allowed": False,
        },
        "workspace_boundary": {
            "product_repo_ref": "explicit_read_only_mount_when_provided",
            "workspace_root_ref": "not_required",
            "writable_paths": "not_supported_in_mvp",
            "private_data_scope": "not_injected",
        },
        "file_authority": {
            "tick_round_trip_required": True,
            "decision_round_trip_required": True,
            "fork_round_trip_required": True,
            "report_round_trip_required": True,
        },
        "owner_gates": {
            "provider_enablement": "satisfied_for_local_docker_mvp_only",
            "credential_boundary": "required_for_any_credentials",
            "network_expansion": "required",
            "write_scope": "required",
            "model_or_agent_authority_expansion": "required",
        },
        "audit": {"independent_audit_required": True},
    }


def validate_request(request: LocalDockerRequest) -> None:
    if not request.image.strip():
        raise SandboxValidationError("An explicit Docker image is required")
    if not request.command:
        raise SandboxValidationError("A command after -- is required")
    if request.network != DEFAULT_NETWORK:
        raise SandboxValidationError("AIPOS-101 MVP only allows --network none")
    if request.timeout_seconds <= 0:
        raise SandboxValidationError("timeout_seconds must be positive")
    paths = [request.workspace] if request.workspace else []
    paths.extend(request.extra_readonly_mounts)
    for path in paths:
        if path is not None and not path.exists():
            raise SandboxValidationError(f"mount path does not exist: {path}")


def build_docker_argv(request: LocalDockerRequest) -> list[str]:
    validate_request(request)
    argv = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        request.network,
    ]
    if request.cpus:
        argv.extend(["--cpus", request.cpus])
    if request.memory:
        argv.extend(["--memory", request.memory])
    if request.workspace:
        argv.extend(["--mount", _readonly_mount(request.workspace, "/workspace")])
        argv.extend(["--workdir", "/workspace"])
    for index, path in enumerate(request.extra_readonly_mounts, start=1):
        argv.extend(["--mount", _readonly_mount(path, f"/readonly/{index}")])
    argv.append(request.image)
    argv.extend(request.command)
    return argv


def _readonly_mount(source: Path, target: str) -> str:
    return f"type=bind,source={source.resolve()},target={target},readonly"


def run_local_docker(request: LocalDockerRequest) -> dict[str, Any]:
    argv = build_docker_argv(request)
    report: dict[str, Any] = {
        "ok": True,
        "provider": PROVIDER,
        "execution_model": EXECUTION_MODEL,
        "dry_run": request.dry_run,
        "image": request.image,
        "command": list(request.command),
        "docker_argv": argv,
        "network": request.network,
        "workspace_mount": str(request.workspace.resolve()) if request.workspace else None,
        "readonly_mounts": [str(path.resolve()) for path in request.extra_readonly_mounts],
        "timeout_seconds": request.timeout_seconds,
        "credentials_injected": False,
        "writes_enabled": False,
        "report_persisted": False,
    }
    if request.dry_run:
        report.update({"status": "dry_run", "exit_code": None, "stdout": "", "stderr": "", "timed_out": False})
        return report
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        report.update(
            {
                "ok": False,
                "status": "timeout",
                "exit_code": None,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
                "timed_out": True,
            }
        )
        return report
    except FileNotFoundError as exc:
        report.update(
            {
                "ok": False,
                "status": "docker_unavailable",
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc),
                "timed_out": False,
            }
        )
        return report

    report.update(
        {
            "ok": completed.returncode == 0,
            "status": "completed",
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lybra sandbox runtime tools")
    subparsers = parser.add_subparsers(dest="provider")
    local = subparsers.add_parser("local-docker", help="Run the local Docker sandbox adapter")
    local_sub = local.add_subparsers(dest="command_name")
    run = local_sub.add_parser("run", help="Run one ephemeral local Docker worker")
    run.add_argument("--image", required=True, help="Explicit Docker image to run")
    run.add_argument("--workspace", type=Path, help="Optional read-only workspace mount at /workspace")
    run.add_argument("--timeout", type=int, default=60, help="Wall-clock timeout in seconds")
    run.add_argument("--network", default=DEFAULT_NETWORK, help="Network mode; MVP only allows none")
    run.add_argument("--dry-run", action="store_true", help="Print the planned report without running Docker")
    run.add_argument("--cpus", help="Optional Docker --cpus value")
    run.add_argument("--memory", help="Optional Docker --memory value")
    run.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    return parser


def request_from_args(args: argparse.Namespace) -> LocalDockerRequest:
    command = tuple(args.command)
    if command and command[0] == "--":
        command = command[1:]
    return LocalDockerRequest(
        image=args.image,
        command=command,
        workspace=args.workspace,
        timeout_seconds=args.timeout,
        network=args.network,
        dry_run=args.dry_run,
        cpus=args.cpus,
        memory=args.memory,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.provider != "local-docker" or args.command_name != "run":
        parser.print_help()
        return 2
    try:
        report = run_local_docker(request_from_args(args))
    except SandboxValidationError as exc:
        print(json.dumps({"ok": False, "status": "validation_error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1
