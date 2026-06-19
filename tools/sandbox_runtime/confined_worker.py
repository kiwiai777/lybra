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
# AIPOS-200 (RF-6): default tool policy must let the agent write its scratch artifact
# (file tools) AND reach the gate (mcp tools). L2 truth protection relies on mount-
# exclusion (truth/.lybra/product/host are never mounted) + gate-only confirm, NOT on the
# tool allowlist — so file tools can only write the writable /scratch (+ tmpfs), never truth.
# `Bash` is intentionally OFF by default (smaller surface; the scratch host path is injected
# into the projection so the agent does not need a shell to discover it). Operators can still
# override via --allowed-tools.
DEFAULT_ALLOWED_TOOLS = "Write,Read,Edit,mcp__lybra__*"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
# AIPOS-196c: claude needs a writable HOME/config dir under the read-only rootfs;
# both point at the tool-mounted tmpfs (/tmp), injected at run time, never baked
# into the image and never a real secret.
HOME_ENV = "HOME"
HOME_VALUE = "/tmp"
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_VALUE = "/tmp/.claude"

# AIPOS-198 F-c10: cap each captured stream (tail-kept) to bound report size.
MAX_TRANSCRIPT_BYTES = 64 * 1024

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
    # AIPOS-196c BYO-LLM wiring. base_url / model / auth_mode are NON-SECRET and are
    # supplied only by the orchestrator/CLI/request — the confined agent cannot set
    # or influence them (they are fixed in the docker argv before the agent runs).
    anthropic_base_url: str | None = None
    model: str | None = None
    auth_mode: str = "api_key"  # "api_key" (x-api-key) | "auth_token" (Bearer)
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS
    timeout_seconds: int = 900
    cpus: str | None = None
    memory: str | None = None
    pids_limit: int = 512
    repo_root: Path | None = None
    dry_run: bool = False
    claim_task_id: str | None = None
    # AIPOS-198 F-c9: run the container as a non-root uid/gid that matches the owner
    # of the 0600 readonly mcp.json mount, so the in-container harness can read it
    # (WSL2 bind-mount applies host-owner semantics; root cannot read a host-owned
    # 0600 file). Derived from the orchestrator at request-build time; None => the
    # `--no-user` escape hatch (no `--user` emitted). NEVER hardcoded.
    run_as_uid: int | None = None
    run_as_gid: int | None = None
    # AIPOS-198 F-c10: raw secrets to redact out of the captured container transcript
    # before it touches the report (all role tokens + the live LLM key). The live
    # mcp_token is always added to the needle list regardless. In-memory only; never
    # serialized into the report.
    redaction_secrets: tuple[str, ...] = ()


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

def render_projection(context_pack: dict[str, Any], dest: Path, *, scratch_host_dir: str | None = None) -> list[str]:
    """Render a minimal read-only projection from a context-pack preview dict.

    v0: the task card fields and a declared-input summary only. No secrets, no
    records, no other tasks' queue, no product repo. Returns written rel names.

    AIPOS-200 (RF-6): inject the host scratch dir so the agent can pass it as
    `scratch_dir` to queue_return without needing a shell to read the env var
    (the default tool policy has no Bash). The host path is non-secret.
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
        "scratch_container_dir": SCRATCH_TARGET,
        "scratch_host_dir": str(scratch_host_dir) if scratch_host_dir else None,
        "notice": (
            "Read-only context projection. The only consequential write path is the "
            f"Lybra MCP gate. Write outputs to {SCRATCH_TARGET}; pass scratch_host_dir "
            "below as queue_return scratch_dir so the gate ingests them."
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
        f"Write outputs to {SCRATCH_TARGET} (the only writable path), then return them",
        "through the MCP gate (queue_return) so the gate ingests them into",
        "workspace_artifacts/. The Owner confirms the return out of band.",
        "",
        f"- scratch_container_dir (write here): {SCRATCH_TARGET}",
        f"- scratch_host_dir (pass as queue_return scratch_dir): {scratch_host_dir or '(unset)'}",
        "",
    ]
    (dest / "TASK.md").write_text("\n".join(lines), encoding="utf-8")
    return ["context_pack.json", "TASK.md"]


def assert_no_secrets(directory: Path, secrets_to_scan: Sequence[str]) -> None:
    """Abort if any raw secret literal appears anywhere under `directory`.

    Scans both projection path names and file contents (belt-and-suspenders on
    top of the structural exclusion already validated in AIPOS-195/196b). Errors
    never echo the raw secret value, only fingerprints / generic locations.
    """
    needles = [s for s in secrets_to_scan if s]
    if not needles:
        return
    for path in sorted(directory.rglob("*")):
        rel = str(path.relative_to(directory))
        for needle in needles:
            if needle in rel:
                # Do not print rel here — it would contain the raw secret.
                raise ConfinedWorkerError(
                    f"projection leak: a raw secret value appears in a projection path name "
                    f"({secret_fingerprint(needle)})"
                )
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in needles:
            if needle in text:
                raise ConfinedWorkerError(
                    f"projection leak: a raw secret value appears in {rel} "
                    f"({secret_fingerprint(needle)})"
                )


def build_projection(
    repo_root: Path,
    dest: Path,
    *,
    task_id: str | None = None,
    task_path: str | None = None,
    connection_json: Path | None = None,
    anthropic_key: str | None = None,
    scratch_host_dir: str | None = None,
) -> dict[str, Any]:
    from tools.aipos_cli.context_pack_builder import build_context_pack_preview

    context_pack = build_context_pack_preview(repo_root, task_id=task_id, path=task_path)
    written = render_projection(context_pack, dest, scratch_host_dir=scratch_host_dir)
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
    if request.auth_mode not in ("api_key", "auth_token"):
        raise ConfinedWorkerError("auth_mode must be 'api_key' or 'auth_token'")
    if request.anthropic_base_url and request.anthropic_base_url.startswith(("http://0.0.0.0", "https://0.0.0.0")):
        raise ConfinedWorkerError("anthropic_base_url must not point at 0.0.0.0")
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
    # AIPOS-198 F-c9: run as the (non-root) owner of the readonly 0600 mcp.json mount
    # so the harness can read it. Strictly hardens the already cap-dropped / no-new-
    # privileges / read-only container (drops ambient root); writable paths still work
    # (/tmp tmpfs is world-writable, /scratch is provisioned 0777). Omitted only under
    # the `--no-user` escape hatch (run_as_uid is None).
    if request.run_as_uid is not None:
        gid = request.run_as_gid if request.run_as_gid is not None else request.run_as_uid
        argv.extend(["--user", f"{request.run_as_uid}:{gid}"])
    if request.cpus:
        argv.extend(["--cpus", request.cpus])
    if request.memory:
        argv.extend(["--memory", request.memory])
    argv.extend(["--mount", _readonly_mount(request.projection_dir, PROJECTION_TARGET)])
    argv.extend(["--mount", _writable_mount(request.scratch_run_dir, SCRATCH_TARGET)])
    argv.extend(["--mount", _readonly_mount(request.mcp_config_path, MCP_CONFIG_TARGET)])
    # LLM credential is passed by env passthrough so its value never appears in argv.
    # Default x-api-key (ANTHROPIC_API_KEY); Bearer (ANTHROPIC_AUTH_TOKEN) only when
    # auth_mode == "auth_token". Exactly one credential env is passed through.
    if request.auth_mode == "auth_token":
        argv.extend(["--env", ANTHROPIC_AUTH_TOKEN_ENV])
    else:
        argv.extend(["--env", ANTHROPIC_KEY_ENV])
    # base_url is NON-SECRET and orchestrator-fixed; the confined agent cannot change
    # it. Omitted when unset (default direct connection).
    if request.anthropic_base_url:
        argv.extend(["--env", f"{ANTHROPIC_BASE_URL_ENV}={request.anthropic_base_url}"])
    # Writable HOME + config dir under the read-only rootfs, both on the tmpfs /tmp.
    argv.extend(["--env", f"{HOME_ENV}={HOME_VALUE}"])
    argv.extend(["--env", f"{CLAUDE_CONFIG_DIR_ENV}={CLAUDE_CONFIG_DIR_VALUE}"])
    argv.extend(["--env", f"{SCRATCH_HOST_ENV}={request.scratch_run_dir.resolve()}"])
    argv.extend(["--env", f"NO_PROXY={no_proxy_value(request.gate_ip)}"])
    argv.extend(["--workdir", PROJECTION_TARGET])
    argv.append(request.image)
    claude_cmd = ["claude", "-p", request.prompt, "--mcp-config", MCP_CONFIG_TARGET, "--allowedTools", request.allowed_tools]
    # Model is NON-SECRET and orchestrator-fixed; selected via --model (the confined
    # agent cannot override the launched command).
    if request.model:
        claude_cmd.extend(["--model", request.model])
    argv.extend(claude_cmd)
    return argv


# --------------------------------------------------------------------------- #
# Transcript capture + redaction (AIPOS-198 F-c10)                            #
# --------------------------------------------------------------------------- #

def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def redaction_needles(request: ConfinedWorkerRequest) -> list[str]:
    """Deduped raw-secret needle list for transcript redaction.

    All declared redaction_secrets (role tokens + live LLM key) plus the live
    mcp_token, which is always scrubbed even if the caller passed nothing.
    """
    needles: list[str] = []
    for value in list(request.redaction_secrets or ()) + [request.mcp_token]:
        text = str(value or "").strip()
        if text and text not in needles:
            needles.append(text)
    return needles


def redact_transcript(text: str, secrets: Sequence[str]) -> tuple[str, list[str]]:
    """Replace every raw secret literal with «redacted:<fingerprint>».

    Returns (clean_text, hit_fingerprints). Never returns or records a raw needle.
    """
    clean = _as_text(text)
    hits: list[str] = []
    for secret in secrets:
        if secret and secret in clean:
            fingerprint = secret_fingerprint(secret)
            clean = clean.replace(secret, f"«redacted:{fingerprint}»")
            if fingerprint not in hits:
                hits.append(fingerprint)
    return clean, hits


def _truncate_tail(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[-max_bytes:].decode("utf-8", errors="ignore"), True


def build_transcript_block(
    stdout: Any,
    stderr: Any,
    secrets: Sequence[str],
    *,
    captured: bool,
    max_bytes: int = MAX_TRANSCRIPT_BYTES,
) -> dict[str, Any]:
    """Build the report transcript block: redact, THEN truncate (tail), fail-closed.

    Redaction runs before truncation so a secret straddling the cut cannot survive.
    A final re-scan of the assembled block raises rather than emit any raw needle.
    """
    out_text = _as_text(stdout)
    err_text = _as_text(stderr)
    out_bytes = len(out_text.encode("utf-8"))
    err_bytes = len(err_text.encode("utf-8"))
    needles = [s for s in secrets if s]

    out_clean, out_hits = redact_transcript(out_text, needles)
    err_clean, err_hits = redact_transcript(err_text, needles)
    out_final, out_trunc = _truncate_tail(out_clean, max_bytes)
    err_final, err_trunc = _truncate_tail(err_clean, max_bytes)

    fingerprints: list[str] = []
    for fingerprint in out_hits + err_hits:
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)

    block = {
        "captured": captured,
        "stdout": out_final,
        "stderr": err_final,
        "stdout_bytes": out_bytes,
        "stderr_bytes": err_bytes,
        "truncated": out_trunc or err_trunc,
        "max_bytes": max_bytes,
        "redaction": {
            "scanned_needles": len(needles),
            "redacted_fingerprints": fingerprints,
        },
    }
    # Fail-closed: no raw needle may survive into the emitted block.
    serialized = json.dumps(block, ensure_ascii=False)
    for secret in needles:
        if secret in serialized:
            raise ConfinedWorkerError(
                f"transcript redaction failed: a raw secret survived "
                f"({secret_fingerprint(secret)})"
            )
    return block


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
    transcript: dict[str, Any] | None = None,
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
            "anthropic_key_injected_via": (
                f"env_passthrough({ANTHROPIC_AUTH_TOKEN_ENV})"
                if request.auth_mode == "auth_token"
                else f"env_passthrough({ANTHROPIC_KEY_ENV})"
            ),
            "baked_into_image": False,
            "in_projection": False,
            "in_scratch": False,
        },
        # AIPOS-196c: non-secret LLM wiring. base_url/model/auth_mode are set only by
        # the orchestrator/CLI/request; the confined agent cannot influence them.
        "llm": {
            "base_url": request.anthropic_base_url or "default(api.anthropic.com)",
            "model": request.model or "harness_default",
            "auth_mode": request.auth_mode,
            "key_fingerprint": request.anthropic_key_fingerprint,
            "config_dir": CLAUDE_CONFIG_DIR_VALUE,
            "home": HOME_VALUE,
            "controlled_by": "orchestrator/cli/request (agent cannot change)",
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
        # AIPOS-198 F-c9: non-root uid/gid the container runs as (matches the 0600
        # token-file owner). null only under the --no-user escape hatch.
        "run_as": {
            "uid": request.run_as_uid,
            "gid": request.run_as_gid if request.run_as_gid is not None else request.run_as_uid,
            "root": request.run_as_uid == 0,
            "derivation": "owner of the readonly 0600 mcp.json mount (orchestrator uid)",
        },
        "docker_argv": docker_argv,
        "teardown": {
            "container_rm": True,
            "token_file_path": str(request.mcp_config_path.resolve()),
            "token_file_removed": token_file_removed,
            "verified": teardown_verified,
        },
        # AIPOS-198 F-c10: redacted container stdout/stderr (raw secrets scrubbed to
        # fingerprints before capture; never logged/written/committed in the raw).
        "transcript": transcript if transcript is not None else build_transcript_block("", "", [], captured=False),
    }


# --------------------------------------------------------------------------- #
# Run / teardown                                                              #
# --------------------------------------------------------------------------- #

def provision_scratch_dir(path: Path) -> None:
    """Create the per-run scratch dir writable by the container process.

    AIPOS-191B pre-flight (Slice B): the worker runs without `--user`, so the
    container writes `/scratch` as the image's default uid. On WSL2/Linux, when
    that uid differs from the host scratch-dir owner the bind-mounted dir is not
    writable (reproduced: mismatched non-root uid -> Permission denied). Docker
    bind-mounts this run dir directly, so only the run dir's own mode gates
    container access; the approved-root parent mode is not changed here, keeping
    the operator's `LYBRA_APPROVED_SCRATCH_ROOT` permissions intact. Set the run
    dir world-writable so any image uid can write its scratch outputs.
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o777)


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
    needles = redaction_needles(request)
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
            transcript=build_transcript_block("", "", needles, captured=False),
        )

    provision_scratch_dir(request.scratch_run_dir)
    write_mcp_config_file(request.mcp_config_path, build_mcp_client_config(request.gate_url, request.mcp_token))

    # AIPOS-198 F-c9 fail-closed guard: the readonly 0600 mount is only readable by the
    # container if its owner uid matches --user. Verify on the just-written file before
    # launching; mismatch means the worker could not reach the gate anyway.
    if request.run_as_uid is not None and os.name == "posix":
        actual_uid = os.stat(request.mcp_config_path).st_uid
        if actual_uid != request.run_as_uid:
            teardown_token_file(request.mcp_config_path)
            raise ConfinedWorkerError(
                f"token-file owner uid {actual_uid} != run_as_uid {request.run_as_uid}; "
                "the container --user could not read the 0600 mcp.json mount"
            )

    status = "completed"
    exit_code: int | None = None
    timed_out = False
    stdout_text = ""
    stderr_text = ""
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
        stdout_text = _as_text(completed.stdout)
        stderr_text = _as_text(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        timed_out = True
        stdout_text = _as_text(exc.stdout)
        stderr_text = _as_text(exc.stderr)
    except FileNotFoundError:
        status = "docker_unavailable"
    finally:
        token_file_removed = teardown_token_file(request.mcp_config_path)
        teardown_verified = token_file_removed and not request.mcp_config_path.exists()

    transcript = build_transcript_block(
        stdout_text, stderr_text, needles, captured=(status != "docker_unavailable")
    )
    return build_worker_report(
        request,
        docker_argv=docker_argv,
        executed=True,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        token_file_removed=token_file_removed,
        teardown_verified=teardown_verified,
        transcript=transcript,
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

    auth_mode = args.auth_mode
    cred_env = ANTHROPIC_AUTH_TOKEN_ENV if auth_mode == "auth_token" else ANTHROPIC_KEY_ENV
    anthropic_key = os.environ.get(cred_env, "")

    # AIPOS-198 F-c9: derive the container uid/gid from the orchestrator (this process
    # writes the 0600 token file via write_mcp_config_file, so the file owner == this
    # uid by construction). --run-as / --run-as-uid/gid override; --no-user disables.
    run_as_uid: int | None = None
    run_as_gid: int | None = None
    if not args.no_user:
        if args.run_as:
            parts = str(args.run_as).split(":", 1)
            run_as_uid = int(parts[0])
            run_as_gid = int(parts[1]) if len(parts) > 1 and parts[1] else None
        if run_as_uid is None:
            run_as_uid = args.run_as_uid if args.run_as_uid is not None else os.getuid()
        if run_as_gid is None:
            run_as_gid = args.run_as_gid if args.run_as_gid is not None else os.getgid()

    # AIPOS-198 F-c10: every raw role token + the live LLM key are scrubbed from the
    # captured transcript. The live mcp_token is always added downstream.
    redaction_secrets = tuple(
        s for s in (all_raw_secrets(connection_json) + ([anthropic_key] if anthropic_key else [])) if s
    )

    build_projection(
        repo_root,
        projection_dir,
        task_id=args.task_id,
        task_path=args.task_path,
        connection_json=connection_json,
        anthropic_key=anthropic_key or None,
        scratch_host_dir=str(scratch_run_dir.resolve()),
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
        anthropic_base_url=str(args.anthropic_base_url).strip() or None if args.anthropic_base_url else None,
        model=str(args.model).strip() or None if args.model else None,
        auth_mode=auth_mode,
        allowed_tools=args.allowed_tools,
        timeout_seconds=args.timeout,
        cpus=args.cpus,
        memory=args.memory,
        pids_limit=args.pids_limit,
        repo_root=repo_root,
        dry_run=args.dry_run,
        claim_task_id=args.task_id,
        run_as_uid=run_as_uid,
        run_as_gid=run_as_gid,
        redaction_secrets=redaction_secrets,
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
    parser.add_argument("--anthropic-base-url", help="Non-secret BYO-LLM base URL (e.g. https://xchai.xyz); omit for default api.anthropic.com")
    parser.add_argument("--model", help="Non-secret model id passed as claude --model (e.g. claude-sonnet-4-6)")
    parser.add_argument("--auth-mode", choices=("api_key", "auth_token"), default="api_key", help="LLM auth header: api_key (x-api-key, default) or auth_token (Bearer)")
    parser.add_argument("--tmp-root", help="Controlled host temp root for projection + token file (outside repo, scratch, projection)")
    parser.add_argument("--run-id", help="Explicit run id; default generated")
    parser.add_argument("--allowed-tools", default=DEFAULT_ALLOWED_TOOLS, help="claude --allowedTools value (default: file tools for /scratch + gate tools; Bash off)")
    parser.add_argument("--timeout", type=int, default=900, help="Wall-clock timeout seconds")
    parser.add_argument("--cpus", help="Optional docker --cpus")
    parser.add_argument("--memory", help="Optional docker --memory")
    parser.add_argument("--pids-limit", type=int, default=512, help="docker --pids-limit")
    parser.add_argument("--repo-root", help="Lybra workspace root; default auto-detected")
    parser.add_argument("--run-as", help="Run container as UID[:GID] (overrides the orchestrator-derived owner match; AIPOS-198 F-c9)")
    parser.add_argument("--run-as-uid", type=int, help="Run container as this uid (default: orchestrator os.getuid(), matching the 0600 token-file owner)")
    parser.add_argument("--run-as-gid", type=int, help="Run container as this gid (default: orchestrator os.getgid())")
    parser.add_argument("--no-user", action="store_true", help="Escape hatch: do not set --user (root container). Off by default; breaks the WSL2 0600-mount read.")
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
