from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.renderer import (
    render_agents_text,
    render_draft_list_text,
    render_draft_result_text,
    render_json,
    render_my_tasks_text,
    render_needs_owner_text,
    render_preview_text,
    render_queue_text,
    render_queue_mutation_text,
    render_records_text,
    render_task_detail_text,
    render_validate_text,
)
from tools.aipos_cli.agent_profiles import actor_matches_task, availability_for_actor, load_agent_profiles
from tools.aipos_cli.context_pack_builder import build_context_pack_preview
from tools.aipos_cli.ai_assisted_authoring import (
    build_authoring_draft,
    build_live_authoring_draft,
    confirm_authoring_draft,
    confirm_live_authoring_draft,
    load_intent_payload,
)
from tools.aipos_cli.custom_agent_profiles import (
    build_profile_draft,
    confirm_profile_draft,
    load_custom_registry,
    validate_custom_registry,
)
from tools.aipos_cli.adapter_response import blocked_response, derive_verdict, make_response
from tools.aipos_cli.board_adapter import execute_dry_run as execute_controlled_dry_run
from tools.aipos_cli.board_adapter import record_owner_decision
from tools.aipos_cli.board_adapter import submit_external_intake
from tools.aipos_cli.controlled_execute import register_dry_run, snapshot_hash, validate_owner_confirmation
from tools.aipos_cli.draft_validator import list_drafts, validate_draft_file
from tools.aipos_cli.draft_writer import (
    build_template_payload,
    create_draft,
    load_body_file,
    load_create_payload_from_json,
    publish_draft,
)
from tools.aipos_cli.external_intake_writer import build_external_intake_draft, load_intake_payload_from_json
from tools.aipos_cli.orchestration_event_writer import append_orchestration_event, load_event_payload_from_json
from tools.aipos_cli.orchestration_summary_preview import build_orchestration_summary_preview
from tools.aipos_cli.owner_decision_writer import build_owner_decision_record, load_owner_decision_payload_from_json
from tools.aipos_cli.planner_loop_mvp import build_planner_loop_mvp_preview
from tools.aipos_cli.planner_iteration_writer import append_planner_iteration, load_iteration_payload_from_json
from tools.aipos_cli.preview import build_preview
from tools.aipos_cli.queue_mutation import mutate_queue_task
from tools.aipos_cli.records import load_records
from tools.aipos_cli.service_mode import (
    render_connection_table,
    rotate_report,
    start_report,
    status_report,
    stop_report,
)
from tools.aipos_cli.state_recovery import build_state_recovery_preview
from tools.aipos_cli.task_loader import find_repo_root, load_all_tasks, load_task_by_path
from tools.aipos_cli.validator import (
    build_records_diagnostics,
    build_records_summary,
    validate_single_task,
    validate_tasks,
)
from tools.aipos_cli.workspace_templates import (
    TEMPLATE_OPERATION,
    build_workspace_init_plan,
    execute_workspace_init,
    parse_var_items,
)
from tools.aipos_cli.workspace_config import (
    DEFAULT_BOARD_HOST,
    DEFAULT_BOARD_PORT,
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    load_workspace_config,
    project_json_path,
    resolve_home_root,
    resolve_workspace_root,
    scaffold_project,
    set_project_repo,
    write_workspace_config,
)
from tools.aipos_cli.home_git import execute_home_git_init, plan_home_git_init


def _filter_my_tasks(report: dict[str, Any], actor: str, profiles: dict[str, Any]) -> dict[str, Any]:
    filtered = [
        task
        for task in report["tasks"]
        if actor_matches_task(task, actor, profiles)
    ]
    availability = availability_for_actor(actor, profiles)
    return {**report, "scope": "my_tasks", "actor": actor, "tasks": filtered, **availability}


def _filter_needs_owner(report: dict[str, Any]) -> dict[str, Any]:
    filtered = [
        task
        for task in report["tasks"]
        if task["verdict"] == "NEEDS_OWNER"
        or task["metadata"].get("needs_owner") is True
        or task["metadata"].get("owner_review_required") is True
        or task["metadata"].get("approval_required") is True
        or bool(task["needs_owner_reasons"])
    ]
    return {**report, "scope": "needs_owner", "tasks": filtered}


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "path": task.get("path"),
        "queue_state": task.get("queue_state"),
        "status": task.get("status"),
        "source_tag": task.get("metadata", {}).get("source_tag"),
        "client_tag": task.get("metadata", {}).get("client_tag"),
        "external_ref": task.get("metadata", {}).get("external_ref"),
        "task_mode": task.get("task_mode"),
        "task_class": task.get("task_class"),
        "effective_task_class": task.get("effective_task_class"),
        "task_class_explicit": task.get("task_class_explicit"),
        "complexity_note": task.get("complexity_note"),
        "verdict": task.get("verdict"),
        "blocking_reasons": task.get("blocking_reasons", []),
        "warnings": task.get("warnings", []),
        "needs_owner_reasons": task.get("needs_owner_reasons", []),
        "recommended_action": task.get("recommended_action"),
        "record_ref_checks": [
            {
                "field": item.get("reference"),
                "record_type": item.get("record_type"),
                "record_id": item.get("record_id"),
                "status": item.get("status"),
                "severity": item.get("level"),
                "message": item.get("message"),
            }
            for item in task.get("record_ref_checks", [])
        ],
        "records": task.get(
            "records",
            {
                "session_records": len(task.get("record_links", {}).get("sessions", [])),
                "claim_logs": len(task.get("record_links", {}).get("claims", [])),
                "has_record_issues": False,
            },
        ),
    }


def _secret_fingerprint(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def build_mcp_doctor_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    source = env if env is not None else os.environ
    transport_token = str(source.get("LYBRA_MCP_TOKEN") or "").strip()
    capability_raw = str(source.get("LYBRA_CAPABILITY_TOKEN") or "").strip()
    capability: dict[str, Any] = {}
    capability_errors: list[str] = []
    if capability_raw:
        try:
            parsed = json.loads(capability_raw)
        except json.JSONDecodeError:
            capability_errors.append("LYBRA_CAPABILITY_TOKEN is not valid JSON")
        else:
            if isinstance(parsed, dict):
                capability = parsed
            else:
                capability_errors.append("LYBRA_CAPABILITY_TOKEN must be a JSON object")

    operations_raw = capability.get("operations")
    operations = [str(item) for item in operations_raw] if isinstance(operations_raw, list) else []
    if capability_raw and not isinstance(operations_raw, list):
        capability_errors.append("LYBRA_CAPABILITY_TOKEN.operations must be a list")

    expires_at = str(capability.get("expires_at") or "").strip()
    expires_status = "missing"
    if expires_at:
        try:
            parsed_expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if parsed_expires.tzinfo is None:
                parsed_expires = parsed_expires.replace(tzinfo=timezone.utc)
            expires_status = "valid" if parsed_expires > datetime.now(timezone.utc) else "expired"
        except ValueError:
            expires_status = "invalid"

    tool_visibility = {
        "queue_claim": "visible" if "queue_claim" in operations else "hidden",
        "queue_return": "visible" if "queue_return" in operations else "hidden",
        "audit_dispatch": "visible" if "audit_dispatch" in operations else "hidden",
        "audit_verdict": "visible" if "audit_verdict" in operations else "hidden",
    }
    hints: list[str] = [
        "Bearer transport auth controls whether the MCP client can connect.",
        "LYBRA_CAPABILITY_TOKEN.operations controls which scoped write tools are visible.",
    ]
    if transport_token and not operations:
        hints.append("Connection may work while claim/return tools stay hidden; check capability operations first.")
    if "queue_claim" not in operations:
        hints.append("Add queue_claim to operations before expecting lybra_queue_claim_* tools.")
    if "queue_return" not in operations:
        hints.append("Add queue_return to operations before expecting lybra_queue_return_* tools.")

    return {
        "operation": "mcp_doctor",
        "ok": not capability_errors,
        "transport_auth": {
            "env_var": "LYBRA_MCP_TOKEN",
            "present": bool(transport_token),
            "fingerprint": _secret_fingerprint(transport_token),
            "meaning": "Bearer token for HTTP/SSE transport connection only; it does not grant write-tool visibility.",
        },
        "capability_scope": {
            "env_var": "LYBRA_CAPABILITY_TOKEN",
            "present": bool(capability_raw),
            "fingerprint": _secret_fingerprint(capability_raw),
            "operations": operations,
            "expires_at": expires_at or None,
            "expires_status": expires_status,
            "token_ref_present": bool(capability.get("token_ref") or capability.get("token_id")),
            "meaning": "Capability token scopes determine which mutation tools are exposed.",
        },
        "tool_visibility": tool_visibility,
        "diagnostics": capability_errors,
        "hints": hints,
        "secrets_notice": "Raw tokens are never printed; fingerprints are non-secret SHA-256 prefixes for comparison only.",
    }


def render_mcp_doctor_text(report: dict[str, Any]) -> str:
    transport = report["transport_auth"]
    capability = report["capability_scope"]
    visibility = report["tool_visibility"]
    lines = [
        "MCP doctor",
        "",
        "Transport authentication:",
        f"- LYBRA_MCP_TOKEN present: {transport['present']}",
        f"- fingerprint: {transport.get('fingerprint') or '(missing)'}",
        "- meaning: Bearer lets the MCP client connect; it does not grant write tools.",
        "",
        "Capability scopes:",
        f"- LYBRA_CAPABILITY_TOKEN present: {capability['present']}",
        f"- fingerprint: {capability.get('fingerprint') or '(missing)'}",
        f"- operations: {capability.get('operations') or []}",
        f"- expires_at: {capability.get('expires_at') or '(missing)'}",
        f"- expires_status: {capability.get('expires_status')}",
        "",
        "Scoped tool visibility:",
        f"- lybra_queue_claim_*: {visibility.get('queue_claim')}",
        f"- lybra_queue_return_*: {visibility.get('queue_return')}",
        f"- lybra_audit_dispatch_*: {visibility.get('audit_dispatch')}",
        f"- lybra_audit_verdict_*: {visibility.get('audit_verdict')}",
        "",
        "Troubleshooting:",
    ]
    lines.extend(f"- {hint}" for hint in report.get("hints", []))
    if report.get("diagnostics"):
        lines.append("")
        lines.append("Diagnostics:")
        lines.extend(f"- {item}" for item in report["diagnostics"])
    lines.append("")
    lines.append(str(report["secrets_notice"]))
    return "\n".join(lines)


def _config_defaults(workspace_root: Path) -> dict[str, Any]:
    config_path = workspace_root / ".lybra" / "config.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        config = load_workspace_config(config_path)
    board = config.get("board") if isinstance(config.get("board"), dict) else {}
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    return {
        "config_path": str(config_path) if config_path.is_file() else None,
        "board_host": str(board.get("host") or DEFAULT_BOARD_HOST),
        "board_port": int(board.get("port") or DEFAULT_BOARD_PORT),
        "mcp_host": str(mcp.get("host") or DEFAULT_MCP_HOST),
        "mcp_port": int(mcp.get("port") or DEFAULT_MCP_PORT),
        "transport_token_env": str(mcp.get("transport_token_env") or "LYBRA_MCP_TOKEN"),
        "capability_token_env": str(mcp.get("capability_token_env") or "LYBRA_CAPABILITY_TOKEN"),
    }


def _resolve_workspace_for_command(args: argparse.Namespace) -> Path:
    explicit_root = getattr(args, "workspace_root", None) or getattr(args, "global_workspace_root", None)
    return resolve_workspace_root(explicit_root=explicit_root)


def _find_repo_root_for_args(args: argparse.Namespace) -> Path:
    explicit_root = getattr(args, "workspace_root", None) or getattr(args, "global_workspace_root", None)
    if explicit_root:
        return resolve_workspace_root(explicit_root=explicit_root)
    return find_repo_root()


def _run_board_command(args: argparse.Namespace) -> int:
    from web.board.app import run_server

    workspace_root = _resolve_workspace_for_command(args)
    defaults = _config_defaults(workspace_root)
    host = str(getattr(args, "host", None) or defaults["board_host"])
    port = int(getattr(args, "port", None) or defaults["board_port"])
    print(f"Lybra Board: http://{host}:{port}")
    print(f"Workspace: {workspace_root}")
    previous_root = os.environ.get("AIPOS_WORKSPACE_ROOT")
    os.environ["AIPOS_WORKSPACE_ROOT"] = str(workspace_root)
    try:
        run_server(host=host, port=port, repo_root=workspace_root)
    finally:
        if previous_root is None:
            os.environ.pop("AIPOS_WORKSPACE_ROOT", None)
        else:
            os.environ["AIPOS_WORKSPACE_ROOT"] = previous_root
    return 0


def _run_mcp_command(args: argparse.Namespace) -> int:
    from tools.mcp_server.http_sse import DEFAULT_KEEPALIVE_SECONDS, config_from_env, run_http_server

    workspace_root = _resolve_workspace_for_command(args)
    defaults = _config_defaults(workspace_root)
    host = str(getattr(args, "host", None) or defaults["mcp_host"])
    port = int(getattr(args, "port", None) or defaults["mcp_port"])
    keepalive = float(getattr(args, "keepalive_seconds", None) or DEFAULT_KEEPALIVE_SECONDS)
    print(f"Lybra MCP HTTP/SSE: http://{host}:{port}")
    print(f"Workspace: {workspace_root}")
    previous_root = os.environ.get("AIPOS_WORKSPACE_ROOT")
    os.environ["AIPOS_WORKSPACE_ROOT"] = str(workspace_root)
    try:
        return run_http_server(config_from_env(host, port, keepalive))
    finally:
        if previous_root is None:
            os.environ.pop("AIPOS_WORKSPACE_ROOT", None)
        else:
            os.environ["AIPOS_WORKSPACE_ROOT"] = previous_root


def build_mcp_config_report(args: argparse.Namespace, env: dict[str, str] | None = None) -> dict[str, Any]:
    source = env if env is not None else os.environ
    workspace_root = _resolve_workspace_for_command(args)
    defaults = _config_defaults(workspace_root)
    host = str(getattr(args, "host", None) or defaults["mcp_host"])
    port = int(getattr(args, "port", None) or defaults["mcp_port"])
    token_env = str(getattr(args, "transport_token_env", None) or defaults["transport_token_env"])
    capability_env = str(getattr(args, "capability_token_env", None) or defaults["capability_token_env"])
    transport_raw = str(source.get(token_env) or "")
    capability_raw = str(source.get(capability_env) or "")
    return {
        "operation": "mcp_config",
        "workspace_root": str(workspace_root),
        "endpoint": f"http://{host}:{port}/mcp",
        "sse_endpoint": f"http://{host}:{port}/sse",
        "server_command": f"lybra mcp --workspace-root {workspace_root}",
        "server_env": {
            "AIPOS_WORKSPACE_ROOT": str(workspace_root),
            token_env: f"${{{token_env}}}",
            capability_env: f"${{{capability_env}}}",
        },
        "client": {
            "authorization_header": f"Bearer ${{{token_env}}}",
            "transport_token_env": token_env,
            "capability_token_env": capability_env,
            "capability_token_note": "LYBRA_CAPABILITY_TOKEN is consumed by the Lybra MCP server process for tool visibility.",
        },
        "fingerprints": {
            token_env: _secret_fingerprint(transport_raw),
            capability_env: _secret_fingerprint(capability_raw),
        },
        "secrets_notice": "Raw token values are never printed. Set tokens in the environment before starting lybra mcp.",
    }


def _render_mcp_config_text(report: dict[str, Any]) -> str:
    lines = [
        "MCP config",
        "",
        f"Workspace: {report['workspace_root']}",
        f"Endpoint: {report['endpoint']}",
        f"SSE: {report['sse_endpoint']}",
        "",
        "Start server:",
        f"- {report['server_command']}",
        "",
        "Environment references:",
    ]
    for key, value in report["server_env"].items():
        lines.append(f"- {key}={value}")
    lines.extend(
        [
            "",
            "Client Authorization header:",
            f"- Authorization: {report['client']['authorization_header']}",
            "",
            "Fingerprints:",
        ]
    )
    for key, value in report["fingerprints"].items():
        lines.append(f"- {key}: {value or '(missing)'}")
    lines.append("")
    lines.append(str(report["secrets_notice"]))
    return "\n".join(lines)


def _run_top_level_init(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    variables = {"project_id": args.project_id, **parse_var_items(args.var)}
    if args.dry_run:
        result = build_workspace_init_plan(
            template=args.template,
            output=output,
            variables=variables,
            actor=args.actor,
            dry_run=True,
        )
        config_path = output / ".lybra" / "config.json"
        result["planned_writes"].append(
            {
                "path": ".lybra/config.json",
                "kind": "file",
                "type": "lybra_workspace_config",
                "byte_size": 0,
            }
        )
        result["summary"]["config_path"] = str(config_path)
    else:
        result = execute_workspace_init(
            template=args.template,
            output=output,
            variables=variables,
            actor=args.actor,
        )
        if result.get("ok"):
            config_path = write_workspace_config(output)
            result["config_path"] = str(config_path)
            result["summary"]["config_path"] = str(config_path)
    if args.json:
        print(render_json(result))
    else:
        print(render_json(result))
    return 1 if result.get("verdict") == "BLOCK" or result.get("blocking_reasons") else 0


def build_validate_json_report(report: dict[str, Any], records: dict[str, Any] | None = None) -> dict[str, Any]:
    output = {"scope": report.get("scope"), "tasks": [_task_summary(task) for task in report["tasks"]]}
    if "summary" in report:
        output["summary"] = report["summary"]
    if "actor" in report:
        output["actor"] = report["actor"]
    if report.get("scope") == "queue":
        output["records_summary"] = report.get("records_summary") or build_records_summary(
            records or {}, report["tasks"]
        )
        output["records_diagnostics"] = report.get("records_diagnostics") or build_records_diagnostics(
            records or {}, report["tasks"]
        )
    return output


def _json_report(report: dict[str, Any], records: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_validate_json_report(report, records=records)


def _records_json(records: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": "records",
        "summary": records["summary"],
        "sessions": records["sessions"],
        "claims": records["claims"],
        "warnings": records.get("warnings", []),
        "parse_errors": records.get("parse_errors", []),
    }


def _agents_json(profiles: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": "agents",
        "summary": profiles["summary"],
        "profiles": profiles["profiles"],
    }


def _task_lookup_arguments(subparser: argparse.ArgumentParser) -> None:
    group = subparser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task-id", help="Task ID to locate across queue directories")
    group.add_argument("--path", help="Task path relative to repo root")


def _queue_mutation_arguments(subparser: argparse.ArgumentParser) -> None:
    _task_lookup_arguments(subparser)
    subparser.add_argument("--actor", required=True, help="Actor performing the mutation")
    subparser.add_argument("--with-records", action="store_true", help="Opt in to records writing under 5_tasks/records/")
    subparser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    subparser.add_argument("--json", action="store_true", help="Output JSON")


def _load_json_object(path: str) -> dict[str, Any]:
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def _is_expired_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    try:
        expires_at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires_at


def _execute_controlled_from_dry_run_envelope(
    repo_root: Any,
    envelope: dict[str, Any],
    actor: str,
    *,
    owner_confirmation_token: str | None = None,
) -> dict[str, Any]:
    operation = "controlled_execute_confirm"
    envelope_operation = str(envelope.get("operation") or "")
    if envelope_operation not in {"intake_submit", "owner_decision_record", TEMPLATE_OPERATION}:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="UNSUPPORTED_OPERATION",
            message="controlled-execute confirm --from-json supports only intake_submit, owner_decision_record, and workspace_init",
            actor={"actor": actor},
            safety_notice="Local CLI controlled execute proof validation.",
        )
    if (envelope.get("actor") or {}).get("actor") != actor:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="ACTOR_MISMATCH",
            message="confirm actor does not match dry-run actor",
            actor={"actor": actor},
            safety_notice="Local CLI controlled execute proof validation.",
        )
    if _is_expired_iso(envelope.get("dry_run_expires_at")):
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="dry-run proof expired; run dry-run again",
            actor={"actor": actor},
            safety_notice="Local CLI controlled execute proof validation.",
        )

    source_data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    payload = source_data.get("original_payload")
    if not isinstance(payload, dict):
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="BACKEND_CONTRACT_MISMATCH",
            message="dry-run envelope is missing data.original_payload",
            actor={"actor": actor},
            safety_notice="Local CLI controlled execute proof validation.",
        )

    if envelope_operation == "intake_submit":
        current = submit_external_intake(payload, dry_run=True, repo_root=repo_root, actor=actor)
    elif envelope_operation == "owner_decision_record":
        current = record_owner_decision(payload, dry_run=True, repo_root=repo_root, actor=actor)
    else:
        variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
        current = build_workspace_init_plan(
            template=str(payload.get("template") or ""),
            output=str(payload.get("output") or ""),
            variables={str(key): str(value) for key, value in variables.items()},
            actor=actor,
            dry_run=True,
        )
    current_hash = snapshot_hash(envelope_operation, actor, current)
    expected_hash = str(envelope.get("dry_run_snapshot_hash") or "")
    if not expected_hash or current_hash != expected_hash:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="REVALIDATION_FAILED",
            message="dry-run snapshot mismatch; run dry-run again",
            actor={"actor": actor},
            data={
                "expected_dry_run_snapshot_hash": expected_hash,
                "current_snapshot_hash": current_hash,
                "recommended_action": "run dry-run again",
            },
            safety_notice="Local CLI controlled execute proof validation.",
        )

    ok_owner, owner_error = validate_owner_confirmation(
        required=bool(envelope.get("owner_confirmation_required", False)),
        owner_confirmation_token=owner_confirmation_token,
    )
    if not ok_owner:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="OWNER_CONFIRMATION_REQUIRED",
            message=owner_error or "owner confirmation required",
            actor={"actor": actor},
            owner_confirmation_required=True,
            owner_confirmation_reasons=list(envelope.get("owner_confirmation_reasons", [])),
            safety_notice="Local CLI controlled execute proof validation.",
        )

    if envelope_operation == "intake_submit":
        result = build_external_intake_draft(repo_root, payload, actor=actor, dry_run=False)
        summary = {
            "safe_id": result.get("safe_id"),
            "task_id": result.get("task_id"),
            "target_path": result.get("target_path"),
            "wrote": result.get("wrote", False),
        }
    elif envelope_operation == "owner_decision_record":
        result = build_owner_decision_record(repo_root, payload, actor=actor, dry_run=False)
        summary = {
            "decision_id": result.get("decision_id"),
            "target_path": result.get("target_path"),
            "wrote": result.get("wrote", False),
        }
    else:
        variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
        result = execute_workspace_init(
            template=str(payload.get("template") or ""),
            output=str(payload.get("output") or ""),
            variables={str(key): str(value) for key, value in variables.items()},
            actor=actor,
        )
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    verdict = derive_verdict(
        blocking_reasons=list(result.get("blocking_reasons", [])),
        warnings=list(result.get("warnings", [])),
    )
    return make_response(
        ok=bool(result.get("wrote", False)),
        verdict=verdict,
        operation=envelope_operation,
        dry_run=False,
        actor={"actor": actor},
        data=result,
        summary=summary,
        planned_writes=list(result.get("planned_writes", [])),
        performed_writes=list(result.get("planned_writes", [])) if result.get("wrote") else [],
        warnings=list(result.get("warnings", [])),
        blocking_reasons=list(result.get("blocking_reasons", [])),
        safety_notice="Local CLI controlled execute proof validation.",
        errors=[],
    )


def _resolve_task_selection(args: argparse.Namespace, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if args.task_id:
        matches = [task for task in tasks if task.get("task_id") == args.task_id]
        if not matches:
            raise ValueError(f"No task found for task_id: {args.task_id}")
        if len(matches) > 1:
            paths = ", ".join(task["path"] for task in matches)
            raise ValueError(f"Duplicate task_id {args.task_id} found in: {paths}")
        return matches[0]
    if args.path:
        return load_task_by_path(args.path)
    raise ValueError("Exactly one of --task-id or --path must be provided")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project OS CLI")
    parser.add_argument("--workspace-root", dest="global_workspace_root", help="Workspace root; may also be provided on supported subcommands")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a Lybra workspace from a bundled template")
    init_parser.add_argument("output", help="Target workspace path")
    init_parser.add_argument("--project-id", required=True, help="Workspace project_id")
    init_parser.add_argument("--template", default="blank", help="Bundled template name; defaults to blank")
    init_parser.add_argument("--var", action="append", default=[], help="Additional template variable in k=v form")
    init_parser.add_argument("--actor", default="owner", help="Actor requesting init; defaults to owner")
    init_parser.add_argument("--dry-run", action="store_true", help="Preview planned writes without creating the workspace")
    init_parser.add_argument("--json", action="store_true", help="Output JSON")

    board_parser = subparsers.add_parser("board", help="Start the local Lybra Board")
    board_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    board_parser.add_argument("--host", help="Bind host; defaults to 127.0.0.1")
    board_parser.add_argument("--port", type=int, help="Bind port; defaults to 7117")

    # AIPOS-205: TUI client over an Owner-started gate. The Textual dependency lives only
    # in tools/lybra_tui (the tui extra); this CLI stays stdlib/zero-dep and lazy-imports it.
    tui_parser = subparsers.add_parser("tui", help="Launch the Lybra TUI client (requires the TUI extra: pip install textual)")
    tui_parser.add_argument("--gate-url", required=True, help="Owner-started gate, e.g. http://127.0.0.1:7118")
    tui_parser.add_argument("--connection-json", help="Path to .lybra/local/connection.json (token read by role)")
    tui_parser.add_argument("--token-env", help="Env var holding the owner bearer token")
    tui_parser.add_argument("--role", default="owner", help="Role to read from connection.json; defaults to owner")
    # AIPOS-206: read-only planning copilot (DG-11). Enabled only when an LLM config is given.
    tui_parser.add_argument("--workspace-root", help="Workspace root used to land copilot DRAFTs under 5_tasks/drafts/")
    tui_parser.add_argument("--project", help="Project the copilot session is scoped to (single-project, R4)")
    tui_parser.add_argument("--llm-base-url", help="OpenAI-compatible base URL; enables the read-only planning copilot")
    tui_parser.add_argument("--llm-key-env", help="Env var holding the LLM api key (never passed on the command line)")
    tui_parser.add_argument("--llm-model", help="LLM model id (default gpt-4o-mini)")

    mcp_config_parser = subparsers.add_parser("mcp-config", help="Print redacted MCP client/server configuration")
    mcp_config_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    mcp_config_parser.add_argument("--host", help="MCP host; defaults to workspace config or 127.0.0.1")
    mcp_config_parser.add_argument("--port", type=int, help="MCP port; defaults to workspace config or 7118")
    mcp_config_parser.add_argument("--transport-token-env", help="Transport token env var; defaults to LYBRA_MCP_TOKEN")
    mcp_config_parser.add_argument("--capability-token-env", help="Capability token env var; defaults to LYBRA_CAPABILITY_TOKEN")
    mcp_config_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_parser = subparsers.add_parser("draft", help="Safe task draft writer")
    draft_subparsers = draft_parser.add_subparsers(dest="draft_command")

    draft_create_parser = draft_subparsers.add_parser("create", help="Create a draft task card")
    create_source_group = draft_create_parser.add_mutually_exclusive_group(required=True)
    create_source_group.add_argument("--from-json", help="Read draft payload from JSON file")
    create_source_group.add_argument("--from-template", help="Create from a built-in template")
    draft_create_parser.add_argument("--task-id", help="Draft task_id")
    draft_create_parser.add_argument("--title", help="Draft title")
    draft_create_parser.add_argument("--project", help="Project", default="ai-project-os")
    draft_create_parser.add_argument("--assigned-to", help="assigned_to value")
    draft_create_parser.add_argument("--agent-instance", help="agent_instance value")
    draft_create_parser.add_argument("--context-bundle", help="context_bundle value")
    draft_create_parser.add_argument("--task-mode", help="task_mode value")
    draft_create_parser.add_argument("--task-class", choices=("simple", "complex"), help="task_class value")
    draft_create_parser.add_argument("--complexity-note", help="Optional complexity_note")
    draft_create_parser.add_argument("--model-tier", help="model_tier value")
    draft_create_parser.add_argument("--priority", help="priority value")
    draft_create_parser.add_argument("--created-by", help="created_by value")
    draft_create_parser.add_argument("--output-target", help="output_target value")
    draft_create_parser.add_argument("--artifact-policy", help="artifact_policy value")
    draft_create_parser.add_argument("--body-file", help="Optional body markdown file")
    draft_create_parser.add_argument("--dry-run", action="store_true", help="Render and validate without writing")
    draft_create_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_validate_parser = draft_subparsers.add_parser("validate", help="Validate a draft task card")
    draft_validate_parser.add_argument("--path", required=True, help="Draft path under 5_tasks/drafts/")
    draft_validate_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_list_parser = draft_subparsers.add_parser("list", help="List draft task cards")
    draft_list_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_publish_parser = draft_subparsers.add_parser("publish", help="Publish a validated draft to pending")
    draft_publish_parser.add_argument("--path", required=True, help="Draft path under 5_tasks/drafts/")
    draft_publish_parser.add_argument("--dry-run", action="store_true", help="Validate and render without writing")
    draft_publish_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_parser = subparsers.add_parser("queue", help="Render task queue")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")
    queue_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_claim_parser = queue_subparsers.add_parser("claim", help="Move a task from pending to claimed")
    _queue_mutation_arguments(queue_claim_parser)

    queue_block_parser = queue_subparsers.add_parser("block", help="Move a task from claimed to blocked")
    _queue_mutation_arguments(queue_block_parser)
    queue_block_parser.add_argument("--reason", required=True, help="Blocking reason")

    queue_complete_parser = queue_subparsers.add_parser("complete", help="Move a task from claimed to completed")
    _queue_mutation_arguments(queue_complete_parser)
    queue_complete_parser.add_argument("--report-link", required=True, help="Completion report link")

    queue_reopen_parser = queue_subparsers.add_parser("reopen", help="Move a task from blocked to pending")
    _queue_mutation_arguments(queue_reopen_parser)
    queue_reopen_parser.add_argument("--reason", required=True, help="Reopen reason")

    my_tasks_parser = subparsers.add_parser("my-tasks", help="Render tasks for an actor")
    my_tasks_parser.add_argument("--actor", required=True, help="Role instance or agent instance")
    my_tasks_parser.add_argument("--json", action="store_true", help="Output JSON")

    needs_owner_parser = subparsers.add_parser("needs-owner", help="Render owner review tasks")
    needs_owner_parser.add_argument("--json", action="store_true", help="Output JSON")

    validate_parser = subparsers.add_parser("validate", help="Run validator")
    validate_parser.add_argument("--json", action="store_true", help="Output JSON")

    controlled_parser = subparsers.add_parser("controlled-execute", help="Local controlled execute dry-run/confirm")
    controlled_subparsers = controlled_parser.add_subparsers(dest="controlled_command")
    controlled_dry_run_parser = controlled_subparsers.add_parser("dry-run", help="Build a controlled execute dry-run proof")
    controlled_dry_run_parser.add_argument("--operation", required=True, choices=["intake_submit", "owner_decision_record"], help="Controlled execute operation")
    controlled_dry_run_parser.add_argument("--actor", required=True, help="Actor requesting the dry-run")
    controlled_dry_run_parser.add_argument("--from-json", required=True, help="Read normalized operation payload from JSON")
    controlled_dry_run_parser.add_argument("--json", action="store_true", help="Output JSON")
    controlled_confirm_parser = controlled_subparsers.add_parser("confirm", help="Confirm a controlled execute dry-run proof")
    confirm_source = controlled_confirm_parser.add_mutually_exclusive_group(required=True)
    confirm_source.add_argument("--dry-run-id", help="In-process dry-run id, for module-level integrations")
    confirm_source.add_argument("--from-json", help="Read prior dry-run JSON envelope as stateless CLI proof")
    controlled_confirm_parser.add_argument("--actor", required=True, help="Actor confirming the dry-run")
    controlled_confirm_parser.add_argument("--owner-confirmation-token", help="Owner confirmation token if required")
    controlled_confirm_parser.add_argument("--json", action="store_true", help="Output JSON")

    workspace_parser = subparsers.add_parser("workspace", help="Workspace template operations")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command")
    workspace_init_parser = workspace_subparsers.add_parser("init", help="Initialize a workspace from a bundled template")
    workspace_init_mode = workspace_init_parser.add_mutually_exclusive_group(required=True)
    workspace_init_mode.add_argument("--dry-run", action="store_true", help="Preview template writes and emit a dry-run proof")
    workspace_init_mode.add_argument("--confirm", action="store_true", help="Confirm a prior dry-run envelope")
    workspace_init_parser.add_argument("--template", help="Bundled template name")
    workspace_init_parser.add_argument("--output", help="Target output path")
    workspace_init_parser.add_argument("--var", action="append", default=[], help="Template variable in k=v form")
    workspace_init_parser.add_argument("--from-json", help="Read prior workspace init dry-run envelope for confirm")
    workspace_init_parser.add_argument("--actor", required=True, help="Actor requesting workspace init")
    workspace_init_parser.add_argument("--owner-confirmation-token", help="Owner confirmation token if required")
    workspace_init_parser.add_argument("--json", action="store_true", help="Output JSON")

    records_parser = subparsers.add_parser("records", help="Render records summary")
    records_parser.add_argument("--json", action="store_true", help="Output JSON")

    state_parser = subparsers.add_parser("state", help="Read-only state recovery and provenance previews")
    state_subparsers = state_parser.add_subparsers(dest="state_command")
    recovery_parser = state_subparsers.add_parser("recovery", help="State recovery preview operations")
    recovery_subparsers = recovery_parser.add_subparsers(dest="recovery_command")
    recovery_preview_parser = recovery_subparsers.add_parser("preview", help="Preview file-authoritative recovery state")
    _task_lookup_arguments(recovery_preview_parser)
    recovery_preview_parser.add_argument("--dry-run-token", help="Optional dry-run token to classify for staleness")
    recovery_preview_parser.add_argument("--expected-operation", help="Optional expected operation for dry-run token compatibility")
    recovery_preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    agents_parser = subparsers.add_parser("agents", help="Render agent profiles")
    agents_parser.add_argument("--json", action="store_true", help="Output JSON")

    mcp_parser = subparsers.add_parser("mcp", help="Start MCP HTTP/SSE or run MCP setup diagnostics")
    mcp_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    mcp_parser.add_argument("--host", help="Bind host; defaults to 127.0.0.1")
    mcp_parser.add_argument("--port", type=int, help="Bind port; defaults to 7118")
    mcp_parser.add_argument("--keepalive-seconds", type=float, help="SSE ping interval; defaults to 30 seconds")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_doctor_parser = mcp_subparsers.add_parser("doctor", help="Inspect MCP transport auth and capability scopes")
    mcp_doctor_parser.add_argument("--json", action="store_true", help="Output JSON")

    serve_parser = subparsers.add_parser("serve", help="Start and inspect local Lybra gate service mode")
    serve_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    # AIPOS-226: connection.json defaults to the global runtime root ~/.lybra/local/connection.json
    # (tokens never enter a truth repo). --connection-json overrides the location.
    serve_parser.add_argument("--connection-json", help="Override the connection.json path (default ~/.lybra/local/connection.json)")
    serve_subparsers = serve_parser.add_subparsers(dest="serve_command")
    serve_start_parser = serve_subparsers.add_parser("start", help="Start Board and MCP gate surfaces in foreground")
    serve_start_parser.add_argument("--board-host", default="127.0.0.1", help="Board bind host; service mode v0 requires 127.0.0.1")
    serve_start_parser.add_argument("--board-port", type=int, default=7117, help="Board port; defaults to 7117")
    serve_start_parser.add_argument("--mcp-host", default="127.0.0.1", help="MCP bind host; service mode v0 requires 127.0.0.1")
    serve_start_parser.add_argument("--mcp-port", type=int, default=7118, help="MCP port; defaults to 7118")
    serve_start_parser.add_argument("--json", action="store_true", help="Output JSON after the supervisor exits")
    serve_status_parser = serve_subparsers.add_parser("status", help="Print redacted service-mode status")
    serve_status_parser.add_argument("--json", action="store_true", help="Output JSON")
    serve_stop_parser = serve_subparsers.add_parser("stop", help="Stop service-owned Board/MCP child processes")
    serve_stop_parser.add_argument("--json", action="store_true", help="Output JSON")
    serve_rotate_parser = serve_subparsers.add_parser("rotate", help="Rotate local service-mode role tokens")
    serve_rotate_parser.add_argument("--board-host", default="127.0.0.1", help="Board host for regenerated connection config")
    serve_rotate_parser.add_argument("--board-port", type=int, default=7117, help="Board port for regenerated connection config")
    serve_rotate_parser.add_argument("--mcp-host", default="127.0.0.1", help="MCP host for regenerated connection config")
    serve_rotate_parser.add_argument("--mcp-port", type=int, default=7118, help="MCP port for regenerated connection config")
    serve_rotate_parser.add_argument("--json", action="store_true", help="Output JSON")

    profile_parser = subparsers.add_parser("agent-profile", help="Workspace-local custom agent profile authoring")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command")
    profile_draft_parser = profile_subparsers.add_parser("draft", help="Validate and preview a custom profile registry write")
    profile_draft_parser.add_argument("--from-json", required=True, help="Read profile authoring payload from JSON")
    profile_draft_parser.add_argument("--actor", required=True, help="Actor requesting the profile mutation preview")
    profile_draft_parser.add_argument("--json", action="store_true", help="Output JSON")
    profile_confirm_parser = profile_subparsers.add_parser("confirm", help="Confirm a prior custom profile draft")
    profile_confirm_parser.add_argument("--from-json", required=True, help="Read prior custom profile draft envelope")
    profile_confirm_parser.add_argument("--actor", required=True, help="Actor confirming the profile mutation")
    profile_confirm_parser.add_argument("--owner-confirmation-token", required=True, help="Explicit Owner confirmation token")
    profile_confirm_parser.add_argument("--json", action="store_true", help="Output JSON")
    profile_validate_parser = profile_subparsers.add_parser("validate", help="Validate workspace-local custom profiles")
    profile_validate_parser.add_argument("--json", action="store_true", help="Output JSON")
    profile_list_parser = profile_subparsers.add_parser("list", help="List workspace-local custom profiles")
    profile_list_parser.add_argument("--json", action="store_true", help="Output JSON")
    profile_inspect_parser = profile_subparsers.add_parser("inspect", help="Inspect one workspace-local custom instance")
    profile_inspect_parser.add_argument("--agent-instance", required=True, help="Canonical custom agent_instance")
    profile_inspect_parser.add_argument("--json", action="store_true", help="Output JSON")

    ai_author_parser = subparsers.add_parser("ai-author", help="Fixture-only AI-assisted task authoring")
    ai_author_subparsers = ai_author_parser.add_subparsers(dest="ai_author_command")
    ai_author_draft_parser = ai_author_subparsers.add_parser("draft", help="Build a fixture-only AI authoring preview")
    ai_author_draft_parser.add_argument("--intent-json", required=True, help="Read semantic intent payload from JSON")
    ai_author_draft_parser.add_argument("--fixture", required=True, help="Bundled fixture id")
    ai_author_draft_parser.add_argument("--actor", required=True, help="Actor requesting the preview")
    ai_author_draft_parser.add_argument("--json", action="store_true", help="Output JSON")
    ai_author_confirm_parser = ai_author_subparsers.add_parser("confirm", help="Confirm a fixture-only AI authoring preview")
    ai_author_confirm_parser.add_argument("--from-json", required=True, help="Read prior AI authoring preview envelope")
    ai_author_confirm_parser.add_argument("--actor", required=True, help="Actor confirming the draft write")
    ai_author_confirm_parser.add_argument("--owner-confirmation-token", required=True, help="Explicit Owner confirmation token")
    ai_author_confirm_parser.add_argument("--json", action="store_true", help="Output JSON")

    ai_author_live_parser = ai_author_subparsers.add_parser("live", help="Live BYO-LLM AI-assisted authoring")
    ai_author_live_subparsers = ai_author_live_parser.add_subparsers(dest="ai_author_live_command")
    ai_author_live_draft_parser = ai_author_live_subparsers.add_parser("draft", help="Build a live BYO-LLM AI authoring preview")
    ai_author_live_draft_parser.add_argument("--intent-json", required=True, help="Read semantic intent payload from JSON")
    ai_author_live_draft_parser.add_argument("--endpoint-ref", required=True, help="Owner-configured live adapter endpoint")
    ai_author_live_draft_parser.add_argument("--credential-ref", required=True, help="Environment-based credential reference such as env:LYBRA_LLM_API_KEY")
    ai_author_live_draft_parser.add_argument("--model-ref", required=True, help="Model reference for the live adapter")
    ai_author_live_draft_parser.add_argument("--provider-ref", default="provider-neutral", help="Optional provider reference for provenance")
    ai_author_live_draft_parser.add_argument("--request-config-ref", default="live-default", help="Request configuration reference for provenance")
    ai_author_live_draft_parser.add_argument("--request-timeout-seconds", type=int, default=30, help="Live adapter timeout in seconds")
    ai_author_live_draft_parser.add_argument("--max-output-tokens", type=int, default=768, help="Maximum output tokens for the live adapter")
    ai_author_live_draft_parser.add_argument("--actor", required=True, help="Actor requesting the preview")
    ai_author_live_draft_parser.add_argument("--json", action="store_true", help="Output JSON")
    ai_author_live_confirm_parser = ai_author_live_subparsers.add_parser("confirm", help="Confirm a prior live BYO-LLM preview")
    ai_author_live_confirm_parser.add_argument("--from-json", required=True, help="Read prior live AI authoring preview envelope")
    ai_author_live_confirm_parser.add_argument("--actor", required=True, help="Actor confirming the draft write")
    ai_author_live_confirm_parser.add_argument("--owner-confirmation-token", required=True, help="Explicit Owner confirmation token")
    ai_author_live_confirm_parser.add_argument("--json", action="store_true", help="Output JSON")

    context_pack_parser = subparsers.add_parser("context-pack", help="Read-only context pack preview")
    context_pack_subparsers = context_pack_parser.add_subparsers(dest="context_pack_command")
    context_pack_preview_parser = context_pack_subparsers.add_parser("preview", help="Build a read-only context pack preview")
    context_pack_source = context_pack_preview_parser.add_mutually_exclusive_group(required=True)
    context_pack_source.add_argument("--task-id", help="Task ID to build context from")
    context_pack_source.add_argument("--path", help="Task path relative to repo root")
    context_pack_source.add_argument("--orchestration-id", help="Orchestration id to build context from")
    context_pack_preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    orchestration_parser = subparsers.add_parser("orchestration", help="Orchestration append-only writers")
    orchestration_subparsers = orchestration_parser.add_subparsers(dest="orchestration_command")
    event_parser = orchestration_subparsers.add_parser("event", help="Orchestration event log operations")
    event_subparsers = event_parser.add_subparsers(dest="event_command")
    event_append_parser = event_subparsers.add_parser("append", help="Append one orchestration event")
    event_append_parser.add_argument("--from-json", required=True, help="Read event payload from JSON file")
    event_append_parser.add_argument("--actor", required=True, help="Actor requesting the append; must match payload actor")
    event_append_parser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    event_append_parser.add_argument("--expected-hash", help="Required snapshot hash for non-dry-run writes")
    event_append_parser.add_argument("--json", action="store_true", help="Output JSON")
    iteration_parser = orchestration_subparsers.add_parser("iteration", help="Planner iteration log operations")
    iteration_subparsers = iteration_parser.add_subparsers(dest="iteration_command")
    iteration_append_parser = iteration_subparsers.add_parser("append", help="Append one planner iteration")
    iteration_append_parser.add_argument("--from-json", required=True, help="Read planner iteration payload from JSON file")
    iteration_append_parser.add_argument(
        "--actor", required=True, help="Actor requesting the append; must match planner_agent or planner_agent_instance"
    )
    iteration_append_parser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    iteration_append_parser.add_argument("--expected-hash", help="Required snapshot hash for non-dry-run writes")
    iteration_append_parser.add_argument("--json", action="store_true", help="Output JSON")
    summary_parser = orchestration_subparsers.add_parser("summary", help="Orchestration summary preview operations")
    summary_subparsers = summary_parser.add_subparsers(dest="summary_command")
    summary_preview_parser = summary_subparsers.add_parser("preview", help="Preview reconstructable summary state")
    summary_preview_parser.add_argument("--orchestration-id", required=True, help="Orchestration id to summarize")
    summary_preview_parser.add_argument("--json", action="store_true", help="Output JSON")
    loop_parser = orchestration_subparsers.add_parser("loop", help="Semi-automated planner loop MVP operations")
    loop_subparsers = loop_parser.add_subparsers(dest="loop_command")
    loop_preview_parser = loop_subparsers.add_parser("preview", help="Preview one safe planner loop coordinator step")
    loop_preview_parser.add_argument("--orchestration-id", required=True, help="Orchestration id to coordinate")
    loop_preview_parser.add_argument("--actor", help="Actor requesting the preview")
    loop_preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    task_parser = subparsers.add_parser("task", help="Render task detail")
    _task_lookup_arguments(task_parser)
    task_parser.add_argument("--json", action="store_true", help="Output JSON")

    preview_parser = subparsers.add_parser("preview", help="Render start task session preview")
    _task_lookup_arguments(preview_parser)
    preview_parser.add_argument("--actor", required=True, help="Current actor")
    preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-226 (Slice 2, Phase 2a): governance-home Owner scaffold + one-shot git setup.
    # These are LOCAL Owner actions (ruling 2=a) — they perform no gate confirm and mint no token.
    default_actor = os.environ.get("USER") or "owner"

    project_parser = subparsers.add_parser("project", help="Owner project scaffold under the governance home")
    project_subparsers = project_parser.add_subparsers(dest="project_command")
    project_new_parser = project_subparsers.add_parser("new", help="Scaffold a fresh per-project truth root + project.json")
    project_new_parser.add_argument("name", help="Project name (becomes <home>/<name>)")
    project_new_parser.add_argument("--code-repo", help="Optional absolute path to the project's code repo")
    project_new_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_new_parser.add_argument("--actor", default=default_actor, help="Provenance actor (registered_by); defaults to $USER or owner")
    project_setrepo_parser = project_subparsers.add_parser("set-repo", help="Set/update an established project's code_repo mapping")
    project_setrepo_parser.add_argument("name", help="Established project name")
    project_setrepo_parser.add_argument("--code-repo", required=True, help="Absolute path to the project's code repo")
    project_setrepo_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setrepo_parser.add_argument("--actor", default=default_actor, help="Provenance actor (registered_by); defaults to $USER or owner")

    home_parser = subparsers.add_parser("home", help="Governance home operations (Owner-explicit, local only)")
    home_subparsers = home_parser.add_subparsers(dest="home_command")
    home_git_init_parser = home_subparsers.add_parser("git-init", help="One-shot, transparent local git init of the home (no remote, no push)")
    home_git_init_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    home_git_init_parser.add_argument("--project", help="Init at <home>/<project> instead of the home root (topology B); default is workspace-level (topology A)")
    home_git_init_parser.add_argument("--actor", default=default_actor, help="Commit identity actor; defaults to $USER or owner")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    if args.command == "init":
        try:
            return _run_top_level_init(args)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if args.command == "tui":
        # Lazy import so the Textual dependency is required only when launching the TUI;
        # the rest of the CLI / gate stays stdlib/zero-dep.
        try:
            from tools.lybra_tui.__main__ import run_tui
        except ImportError:
            print(
                "lybra tui requires Textual. Install it with: pip install textual "
                "(lybra is npm-distributed, not on PyPI; see README Quick start).",
                file=sys.stderr,
            )
            return 2
        return run_tui(
            gate_url=args.gate_url,
            connection_json=args.connection_json,
            token_env=args.token_env,
            role=args.role,
            workspace_root=args.workspace_root,
            project=args.project,
            llm_base_url=args.llm_base_url,
            llm_key_env=args.llm_key_env,
            llm_model=args.llm_model,
        )

    if args.command == "board":
        try:
            return _run_board_command(args)
        except (OSError, ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if args.command == "mcp-config":
        try:
            result = build_mcp_config_report(args)
        except (OSError, ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(_render_mcp_config_text(result))
        return 0

    if args.command == "draft":
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if not args.draft_command:
            parser.print_help()
            return 2

        if args.draft_command == "create":
            try:
                if args.from_json:
                    metadata, body = load_create_payload_from_json(args.from_json)
                else:
                    body = load_body_file(args.body_file) if args.body_file else None
                    metadata, body = build_template_payload(
                        args.from_template,
                        {
                            "task_id": args.task_id,
                            "title": args.title,
                            "project": args.project,
                            "assigned_to": args.assigned_to,
                            "agent_instance": args.agent_instance,
                            "context_bundle": args.context_bundle,
                            "task_mode": args.task_mode,
                            "task_class": args.task_class,
                            "complexity_note": args.complexity_note,
                            "model_tier": args.model_tier,
                            "priority": args.priority,
                            "created_by": args.created_by,
                            "output_target": args.output_target,
                            "artifact_policy": args.artifact_policy,
                        },
                        body=body,
                    )
                result = create_draft(repo_root, metadata, body, dry_run=args.dry_run)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
            return 1 if result.get("verdict") == "BLOCK" else 0

        if args.draft_command == "validate":
            try:
                result = validate_draft_file(repo_root, args.path)
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
            return 1 if result.get("verdict") == "BLOCK" else 0

        if args.draft_command == "list":
            result = list_drafts(repo_root)
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_list_text(result))
            return 0

        if args.draft_command == "publish":
            try:
                result = publish_draft(repo_root, args.path, dry_run=args.dry_run)
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
            return 1 if result.get("verdict") == "BLOCK" else 0

        print(f"Unknown draft command: {args.draft_command}", file=sys.stderr)
        return 2

    if args.command == "mcp":
        if getattr(args, "mcp_command", None) == "doctor":
            result = build_mcp_doctor_report()
            if args.json:
                print(render_json(result))
            else:
                print(render_mcp_doctor_text(result))
            return 0 if result.get("ok") else 1
        try:
            return _run_mcp_command(args)
        except (OSError, ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if args.command == "serve":
        if not getattr(args, "serve_command", None):
            parser.print_help()
            return 2
        try:
            workspace_root = _resolve_workspace_for_command(args)
            conn_override = getattr(args, "connection_json", None)
            connection_target = Path(conn_override).expanduser() if conn_override else None
            if args.serve_command == "start":
                result = start_report(
                    workspace_root,
                    board_host=str(args.board_host),
                    board_port=int(args.board_port),
                    mcp_host=str(args.mcp_host),
                    mcp_port=int(args.mcp_port),
                    start_processes=True,
                    connection_target=connection_target,
                )
            elif args.serve_command == "status":
                result = status_report(workspace_root, connection_target=connection_target)
            elif args.serve_command == "stop":
                result = stop_report(workspace_root, connection_target=connection_target)
            elif args.serve_command == "rotate":
                result = rotate_report(
                    workspace_root,
                    board_host=str(args.board_host),
                    board_port=int(args.board_port),
                    mcp_host=str(args.mcp_host),
                    mcp_port=int(args.mcp_port),
                    connection_target=connection_target,
                )
            else:
                parser.print_help()
                return 2
        except (OSError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        elif args.serve_command == "start" and result.get("supervisor_printed"):
            pass
        elif args.serve_command in {"start", "status", "rotate"}:
            print(render_connection_table(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" or result.get("blocking_reasons") else 0

    if args.command == "workspace":
        if not getattr(args, "workspace_command", None):
            parser.print_help()
            return 2
        if args.workspace_command != "init":
            parser.print_help()
            return 2
        try:
            if args.dry_run:
                if not args.template or not args.output:
                    raise ValueError("--template and --output are required for workspace init --dry-run")
                variables = parse_var_items(args.var)
                result = build_workspace_init_plan(
                    template=args.template,
                    output=args.output,
                    variables=variables,
                    actor=args.actor,
                    dry_run=True,
                )
                if result.get("execute_allowed"):
                    token_meta = register_dry_run(operation=TEMPLATE_OPERATION, actor=args.actor, plan=result)
                    result.update(token_meta)
                    result["dry_run_token"] = token_meta["dry_run_id"]
            else:
                if not args.from_json:
                    raise ValueError("--from-json is required for workspace init --confirm")
                envelope = _load_json_object(args.from_json)
                result = _execute_controlled_from_dry_run_envelope(
                    None,
                    envelope,
                    args.actor,
                    owner_confirmation_token=args.owner_confirmation_token,
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    if args.command == "project":
        if not getattr(args, "project_command", None):
            parser.print_help()
            return 2
        try:
            home = resolve_home_root(explicit_root=args.home_root)
            if args.project_command == "new":
                root = scaffold_project(
                    home, args.name, code_repo=args.code_repo, registered_by=args.actor
                )
                print(f"Created project root: {root}")
                print(f"project.json: {project_json_path(root)}")
                print(f"next: lybra serve with LYBRA_HOME_ROOT={home}")
                print("tokens: lybra serve writes ~/.lybra/local/connection.json (runtime root)")
                return 0
            if args.project_command == "set-repo":
                root = set_project_repo(
                    home, args.name, args.code_repo, registered_by=args.actor
                )
                print(f"Updated {project_json_path(root)}: code_repo={Path(args.code_repo).expanduser()}")
                return 0
        except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        parser.print_help()
        return 2

    if args.command == "home":
        if not getattr(args, "home_command", None):
            parser.print_help()
            return 2
        if args.home_command == "git-init":
            try:
                home = resolve_home_root(explicit_root=args.home_root)
                # Topology B (--project) versions <home>/<project>; default (topology A) is the
                # whole home as one repo.
                project = getattr(args, "project", None)
                target = (home / project) if project else home
                # Transparent: print the exact plan (gitignore + commands + push hint) FIRST.
                plan = plan_home_git_init(target, args.actor)
                print(f"Home: {plan['home']}")
                print("Planned .gitignore:")
                print(plan["gitignore"].rstrip("\n"))
                print("Planned git commands (one-shot, local only — no remote, no push):")
                for cmd in plan["commands"]:
                    print("  " + " ".join(cmd))
                print("After it completes, push yourself with your own remote URL:")
                for hint in plan["push_hint"]:
                    print("  " + hint)
                result = execute_home_git_init(target, actor=args.actor)
            except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print("Ran:")
            for ran in result["ran"]:
                print("  " + ran)
            print("Push hint (Owner runs this — Lybra never pushes):")
            for hint in result["push_hint"]:
                print("  " + hint)
            return 0
        parser.print_help()
        return 2

    try:
        repo_root = _find_repo_root_for_args(args)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "controlled-execute":
        if not getattr(args, "controlled_command", None):
            parser.print_help()
            return 2
        try:
            if args.controlled_command == "dry-run":
                if args.operation == "intake_submit":
                    payload = load_intake_payload_from_json(args.from_json)
                    result = submit_external_intake(payload, dry_run=True, repo_root=repo_root, actor=args.actor)
                else:
                    payload = load_owner_decision_payload_from_json(args.from_json)
                    result = record_owner_decision(payload, dry_run=True, repo_root=repo_root, actor=args.actor)
            elif args.controlled_command == "confirm":
                if getattr(args, "from_json", None):
                    envelope = _load_json_object(args.from_json)
                    result = _execute_controlled_from_dry_run_envelope(
                        repo_root,
                        envelope,
                        args.actor,
                        owner_confirmation_token=args.owner_confirmation_token,
                    )
                else:
                    result = execute_controlled_dry_run(
                        args.dry_run_id,
                        args.actor,
                        owner_confirmation_token=args.owner_confirmation_token,
                        repo_root=repo_root,
                    )
            else:
                parser.print_help()
                return 2
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(render_json(result))
        return 0

    if args.command == "agent-profile":
        if not getattr(args, "profile_command", None):
            parser.print_help()
            return 2
        try:
            if args.profile_command == "draft":
                result = build_profile_draft(repo_root, _load_json_object(args.from_json), actor=args.actor)
            elif args.profile_command == "confirm":
                result = confirm_profile_draft(
                    repo_root,
                    _load_json_object(args.from_json),
                    actor=args.actor,
                    owner_confirmation_token=args.owner_confirmation_token,
                )
            elif args.profile_command == "validate":
                result = validate_custom_registry(repo_root)
            elif args.profile_command == "list":
                registry, blocking = load_custom_registry(repo_root)
                result = {"scope": "custom_agent_profiles", "path": "0_control_plane/agents/custom_agent_profiles.yaml", "profiles": registry["profiles"], "blocking_reasons": blocking}
            elif args.profile_command == "inspect":
                registry, blocking = load_custom_registry(repo_root)
                matches = [
                    instance
                    for profile in registry["profiles"]
                    if isinstance(profile, dict)
                    for instance in profile.get("instances", []) or []
                    if isinstance(instance, dict) and instance.get("agent_instance") == args.agent_instance
                ]
                result = {"scope": "custom_agent_profile", "agent_instance": args.agent_instance, "instance": matches[0] if len(matches) == 1 else None, "blocking_reasons": [*blocking, *(["custom agent_instance not found or is ambiguous"] if len(matches) != 1 else [])]}
            else:
                parser.print_help()
                return 2
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" or result.get("blocking_reasons") else 0

    if args.command == "ai-author":
        if not getattr(args, "ai_author_command", None):
            parser.print_help()
            return 2
        try:
            if args.ai_author_command == "draft":
                result = build_authoring_draft(
                    repo_root,
                    load_intent_payload(args.intent_json),
                    fixture_id=args.fixture,
                    actor=args.actor,
                )
            elif args.ai_author_command == "confirm":
                result = confirm_authoring_draft(
                    repo_root,
                    _load_json_object(args.from_json),
                    actor=args.actor,
                    owner_confirmation_token=args.owner_confirmation_token,
                )
            elif args.ai_author_command == "live":
                if not getattr(args, "ai_author_live_command", None):
                    parser.print_help()
                    return 2
                if args.ai_author_live_command == "draft":
                    result = build_live_authoring_draft(
                        repo_root,
                        load_intent_payload(args.intent_json),
                        endpoint_ref=args.endpoint_ref,
                        credential_ref=args.credential_ref,
                        model_ref=args.model_ref,
                        actor=args.actor,
                        provider_ref=args.provider_ref,
                        request_config_ref=args.request_config_ref,
                        request_timeout_seconds=args.request_timeout_seconds,
                        max_output_tokens=args.max_output_tokens,
                    )
                elif args.ai_author_live_command == "confirm":
                    result = confirm_live_authoring_draft(
                        repo_root,
                        _load_json_object(args.from_json),
                        actor=args.actor,
                        owner_confirmation_token=args.owner_confirmation_token,
                    )
                else:
                    parser.print_help()
                    return 2
            else:
                parser.print_help()
                return 2
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" or result.get("blocking_reasons") else 0

    if args.command == "queue" and getattr(args, "queue_command", None) in {"claim", "block", "complete", "reopen"}:
        profiles = load_agent_profiles(repo_root)
        try:
            result = mutate_queue_task(
                repo_root,
                args.queue_command,
                task_id=getattr(args, "task_id", None),
                task_path=getattr(args, "path", None),
                actor=args.actor,
                reason=getattr(args, "reason", None),
                report_link=getattr(args, "report_link", None),
                dry_run=args.dry_run,
                profiles=profiles,
                with_records=args.with_records,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_queue_mutation_text(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    if args.command == "orchestration":
        if getattr(args, "orchestration_command", None) == "event" and getattr(args, "event_command", None) == "append":
            try:
                payload = load_event_payload_from_json(args.from_json)
                result = append_orchestration_event(
                    repo_root,
                    payload,
                    actor=args.actor,
                    dry_run=args.dry_run,
                    expected_hash=args.expected_hash,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        if (
            getattr(args, "orchestration_command", None) == "iteration"
            and getattr(args, "iteration_command", None) == "append"
        ):
            try:
                payload = load_iteration_payload_from_json(args.from_json)
                result = append_planner_iteration(
                    repo_root,
                    payload,
                    actor=args.actor,
                    dry_run=args.dry_run,
                    expected_hash=args.expected_hash,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        if (
            getattr(args, "orchestration_command", None) == "summary"
            and getattr(args, "summary_command", None) == "preview"
        ):
            try:
                tasks = load_all_tasks(repo_root)
                records = load_records(repo_root)
                result = build_orchestration_summary_preview(
                    repo_root,
                    args.orchestration_id,
                    tasks=tasks,
                    records=records,
                )
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        if (
            getattr(args, "orchestration_command", None) == "loop"
            and getattr(args, "loop_command", None) == "preview"
        ):
            try:
                result = build_planner_loop_mvp_preview(repo_root, args.orchestration_id, actor=getattr(args, "actor", None))
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == "BLOCK" else 0
        else:
            parser.print_help()
            return 2

    if args.command == "context-pack":
        if getattr(args, "context_pack_command", None) != "preview":
            parser.print_help()
            return 2
        try:
            result = build_context_pack_preview(
                repo_root,
                task_id=getattr(args, "task_id", None),
                path=getattr(args, "path", None),
                orchestration_id=getattr(args, "orchestration_id", None),
            )
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    try:
        tasks = load_all_tasks(repo_root)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    records = load_records(repo_root)
    profiles = load_agent_profiles(repo_root)
    actor = getattr(args, "actor", None)
    report = validate_tasks(tasks, current_actor=actor, records=records, profiles=profiles)

    if args.command == "state":
        if (
            getattr(args, "state_command", None) != "recovery"
            or getattr(args, "recovery_command", None) != "preview"
        ):
            parser.print_help()
            return 2
        try:
            result = build_state_recovery_preview(
                repo_root,
                task_id=getattr(args, "task_id", None),
                path=getattr(args, "path", None),
                records=records,
                dry_run_token=getattr(args, "dry_run_token", None),
                expected_operation=getattr(args, "expected_operation", None),
            )
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    if args.command == "queue":
        if args.json:
            print(render_json(_json_report(report, records=records)))
        else:
            print(render_queue_text(report))
        return 0

    if args.command == "my-tasks":
        actor_report = _filter_my_tasks(report, args.actor, profiles)
        if args.json:
            print(render_json(_json_report(actor_report, records=records)))
        else:
            print(render_my_tasks_text(actor_report, args.actor))
        return 0

    if args.command == "needs-owner":
        owner_report = _filter_needs_owner(report)
        if args.json:
            print(render_json(_json_report(owner_report, records=records)))
        else:
            print(render_needs_owner_text(owner_report))
        return 0

    if args.command == "validate":
        if args.json:
            print(render_json(build_validate_json_report(report, records=records)))
        else:
            print(render_validate_text(report))
        return 0

    if args.command == "records":
        if args.json:
            print(render_json(_records_json(records)))
        else:
            print(render_records_text(records))
        return 0

    if args.command == "agents":
        if args.json:
            print(render_json(_agents_json(profiles)))
        else:
            print(render_agents_text(profiles))
        return 0

    if args.command == "task":
        try:
            selected = _resolve_task_selection(args, tasks)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        validated = validate_single_task(selected, tasks=tasks, records=records, profiles=profiles)
        if args.json:
            print(render_json(validated))
        else:
            print(render_task_detail_text(validated))
        return 0

    if args.command == "preview":
        try:
            selected = _resolve_task_selection(args, tasks)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        validated = validate_single_task(
            selected, tasks=tasks, current_actor=args.actor, records=records, profiles=profiles
        )
        preview = build_preview(validated, actor=args.actor, records=records, profiles=profiles)
        if args.json:
            print(render_json(preview))
        else:
            print(render_preview_text(preview))
        return 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
