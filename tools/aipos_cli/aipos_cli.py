from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from tools.schema_constants import RecordType, Verdict
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
from tools.aipos_cli.agent_profiles import actor_matches_task, availability_for_actor, load_agent_profiles, canonical_agent
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
from tools.aipos_cli.records import load_records, expected_session_record_path
from tools.aipos_cli.service_mode import (
    render_connection_table,
    roles_list_report,
    roles_reconcile_report,
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
    _project_candidates,
    get_collaboration_profile,
    load_workspace_config,
    project_json_path,
    read_project_json,
    resolve_home_root,
    resolve_workspace_root,
    scaffold_project,
    set_project_repo,
    write_workspace_config,
)

# AIPOS-R4B-1 FIX-2: 延迟初始化(避免模块级导入崩溃 CLI)。见 AUDIT-R4B-1 F-R4B1-1。
_DEFAULT_GATE_URL: str | None = None

def _get_default_gate_url() -> str:
    """惰性读取 gate URL(避免模块级导入 schema_loader)。"""
    global _DEFAULT_GATE_URL
    if _DEFAULT_GATE_URL is None:
        try:
            from tools.schema_loader import get_config_default_gate_url
            _DEFAULT_GATE_URL = get_config_default_gate_url()
        except ImportError as e:
            raise ImportError(
                "Cannot load schema_loader.get_config_default_gate_url() for gate URL default. "
                "This typically occurs when running lybra CLI from outside the project root "
                "in an editable install. Run from the project directory or ensure PYTHONPATH "
                "includes the project root."
            ) from e
    return _DEFAULT_GATE_URL
from tools.aipos_cli.home_git import execute_home_git_init, plan_home_git_init
from tools.aipos_cli.project_structure import (
    export_project_to_yaml,
    import_project_structure,
    validate_structure,
    parse_yaml,
)


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
        if task["verdict"] == Verdict.NEEDS_OWNER
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


def _normalize_mcp_host_for_config(host: str) -> str:
    """AIPOS-R6K件③: MCP配置生成 loopback 优先。
    
    如果 host 是 loopback 或默认值(0.0.0.0/127.0.0.1),规范化为 127.0.0.1。
    这确保 pi 自带 MCP 通道配置也走 loopback,免疫代理劫持。
    """
    if host in ('127.0.0.1', 'localhost', '::1', '0.0.0.0', ''):
        return '127.0.0.1'
    return host


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


def _run_board_open(args: argparse.Namespace) -> int:
    """AIPOS-271 ``board open``:读 connection.json(文件权即身份)铸 OTC → 开/打看板登录链接。
    
    F-271-1: 支持免 --workspace-root(单工作区自动发现:按当前目录或环境变量)。
    """
    import webbrowser

    from tools.aipos_cli.board_login import (
        build_login_url,
        load_role_token,
        mint_otc,
        resolve_board_url,
        token_fingerprint,
    )

    connection_json = getattr(args, "connection_json", None)
    role = getattr(args, "role", None)
    try:
        token, role_used = load_role_token(connection_json, role=role)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: 读取 connection.json 失败 —— {exc}", file=sys.stderr)
        print("提示:用 --connection-json 指定路径,或 --role 指定角色。", file=sys.stderr)
        return 1
    
    # F-271-1: 支持工作区自动发现(优先显式参数,否则尝试当前目录)
    workspace_root = getattr(args, "workspace_root", None)
    if not workspace_root:
        try:
            workspace_root = _resolve_workspace_for_command(args)
        except Exception:
            workspace_root = None  # 降级到 connection.json
    
    base_url = resolve_board_url(
        connection_json,
        url=getattr(args, "url", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        workspace_root=workspace_root,
    )
    print(f"Board server: {base_url}")
    print(f"身份(角色): {role_used}  token 指纹: {token_fingerprint(token)}")
    try:
        result = mint_otc(base_url, token)
    except OSError as exc:
        print(f"Error: 无法连接 board server({base_url})—— {exc}", file=sys.stderr)
        print("提示:先在另一终端 `lybra board` 或 `lybra serve start` 启动 server。", file=sys.stderr)
        return 1
    if not result.ok:
        print(f"Error: 铸 OTC 失败 —— {result.error}", file=sys.stderr)
        return 1
    login_url = build_login_url(base_url, result.login_url)
    print(f"已铸一次性登录票(TTL {result.expires_in}s)。")
    if getattr(args, "no_browser", False):
        print(login_url)
        return 0
    print(f"打开浏览器:{login_url}")
    try:
        webbrowser.open(login_url)
    except Exception as exc:  # 无头环境/webbrowser 不可用 → 退回打印 URL。
        print(f"(未能自动打开浏览器:{exc};请手动复制上方 URL)", file=sys.stderr)
    return 0


def _run_board_approve(args: argparse.Namespace) -> int:
    """AIPOS-271 ``board approve <码>``:gate 机读 connection.json 确认身份 → 批准跨机设备码。
    
    F-271-1: 支持免 --workspace-root(单工作区自动发现:按当前目录或环境变量)。
    """
    from tools.aipos_cli.board_login import (
        approve_device,
        load_role_token,
        resolve_board_url,
        token_fingerprint,
    )

    connection_json = getattr(args, "connection_json", None)
    role = getattr(args, "role", None)
    try:
        token, role_used = load_role_token(connection_json, role=role)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: 读取 connection.json 失败 —— {exc}", file=sys.stderr)
        return 1
    
    # F-271-1: 支持工作区自动发现(优先显式参数,否则尝试当前目录)
    workspace_root = getattr(args, "workspace_root", None)
    if not workspace_root:
        try:
            workspace_root = _resolve_workspace_for_command(args)
        except Exception:
            workspace_root = None  # 降级到 connection.json
    
    base_url = resolve_board_url(
        connection_json,
        url=getattr(args, "url", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        workspace_root=workspace_root,
    )
    code = str(getattr(args, "code", "") or "")
    print(f"Board server: {base_url}")
    print(f"身份(角色): {role_used}  token 指纹: {token_fingerprint(token)}")
    try:
        ok, message = approve_device(base_url, token, code)
    except OSError as exc:
        print(f"Error: 无法连接 board server({base_url})—— {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0 if ok else 1


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
    # AIPOS-R6K件③: loopback 优先(免疫代理劫持)
    normalized_host = _normalize_mcp_host_for_config(host)
    port = int(getattr(args, "port", None) or defaults["mcp_port"])
    token_env = str(getattr(args, "transport_token_env", None) or defaults["transport_token_env"])
    capability_env = str(getattr(args, "capability_token_env", None) or defaults["capability_token_env"])
    transport_raw = str(source.get(token_env) or "")
    capability_raw = str(source.get(capability_env) or "")
    return {
        "operation": "mcp_config",
        "workspace_root": str(workspace_root),
        "endpoint": f"http://{normalized_host}:{port}/mcp",
        "sse_endpoint": f"http://{normalized_host}:{port}/sse",
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
            "proxy_exempt": True,  # AIPOS-R6K件③: 明示代理豁免
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


def _ask_project_type_interactive() -> dict[str, Any] | None:
    """AIPOS-335 S2: 交互式询问项目类型，生成 collaboration_profile。
    
    按 AIPOS-304 D3 措辞：只设默认、明示可后加，不吓唬用户“定死了”。
    可跳过：不回答时给安全默认并提示如何后改。
    
    返回 collaboration_profile dict 或 None（跳过）。
    """
    import sys
    
    print("\n" + "=" * 80)
    print("🔧 项目类型配置（可以在项目设置中修改）")
    print("=" * 80)
    print("\n这个项目主要用来做什么？")
    print("  1. 代码开发（需要独立审计 agent）")
    print("  2. 非代码任务（文档/配置/部署，验证台审计）")
    print("  3. 混合项目（同时包含代码与非代码任务）")
    print("  [Enter] 跳过（使用默认：代码开发）")
    print("\n💡 提示：选择后仍可在项目设置中调整，首次出现新类型任务时会有智能提示\n")
    
    try:
        choice = input("请选择 (1-3 或 Enter): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n跳过配置，使用默认值。")
        return None
    
    if not choice:
        print("使用默认配置：代码开发项目")
        print("🔧 可以后续在项目设置中修改协作能力")
        return None
    
    # 根据选择生成 collaboration_profile
    if choice == "1":
        profile = {
            "code_enabled": True,
            "deploy_gate_enabled": False,
            "default_audit_mode": "agent",
            "output_locations": ["product_repo_worktree", "workspace_records"],
        }
        print("✅ 项目类型：代码开发（完整 agent 审计）")
    elif choice == "2":
        profile = {
            "code_enabled": False,
            "deploy_gate_enabled": False,
            "default_audit_mode": "bench",
            "output_locations": ["workspace_records", "remote_system"],
        }
        print("✅ 项目类型：非代码（验证台审计）")
    elif choice == "3":
        profile = {
            "code_enabled": True,
            "deploy_gate_enabled": False,
            "default_audit_mode": "hybrid",
            "output_locations": ["product_repo_worktree", "workspace_records", "remote_system"],
        }
        print("✅ 项目类型：混合（按任务自适应）")
    else:
        print(f"无效选择 '{choice}'，使用默认配置")
        return None
    
    # 询问是否涉及部署
    print("\n是否涉及部署？(y/N): ", end="")
    try:
        deploy = input().strip().lower()
        if deploy in ("y", "yes"):
            profile["deploy_gate_enabled"] = True
            print("✅ 启用部署门（需要双层 Owner 确认）")
    except (EOFError, KeyboardInterrupt):
        pass
    
    print("🔧 可以后续在项目设置中修改协作能力")
    print("=" * 80 + "\n")
    
    return profile


def _print_onboarding_guide(workspace_root: Path, project_id: str) -> None:
    """Print three-step onboarding guide after successful init (AIPOS-272)."""
    print("\n" + "=" * 80)
    print("🎉 Workspace initialized successfully!")
    print("=" * 80)
    print("\n📦 Onboarding package created:")
    print(f"  - governance/advisor-charter.md   (顾问接入包：置顶铁律 + 六查 + governance_refs)")
    print(f"  - governance/AGENTS.md             (Executor/Auditor 角色说明)")
    print(f"  - 5_tasks/drafts/example-task.md   (示例任务卡)")
    print("\n🚀 Next steps — Get started in 3 steps:\n")
    print("  ① Start the gate:")
    print(f"     cd {workspace_root}")
    print(f"     lybra serve --workspace-root .")
    print("\n  ② Open the board (in another terminal):")
    print(f"     lybra board open --workspace-root {workspace_root}")
    print("     # The board will show a welcome guide when empty.")
    print("\n  ③ Connect your advisor agent:")
    print("     Copy the advisor onboarding prompt from the board's welcome guide,")
    print("     paste it to your agent (Claude/Codex/any MCP-capable agent),")
    print("     and start drafting your first task card!")
    print("\n💡 See QUICKSTART.md for the complete walkthrough.")
    print("=" * 80 + "\n")


def _run_top_level_init(args: argparse.Namespace) -> int:
    # AIPOS-272F5: Default output to ~/.lybra/workspaces/<project_id>/ if not provided
    if args.output:
        output = Path(args.output).expanduser().resolve()
    else:
        output = Path.home() / ".lybra" / "workspaces" / args.project_id
        output = output.resolve()
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
        # AIPOS-272: Print onboarding guide after successful init
        if not args.dry_run and result.get("ok") and not result.get("blocking_reasons"):
            _print_onboarding_guide(output, variables.get("project_id", "workspace"))
    return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0


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
    if envelope_operation not in {"intake_submit", RecordType.OWNER_DECISION_RECORD, TEMPLATE_OPERATION}:
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
    elif envelope_operation == RecordType.OWNER_DECISION_RECORD:
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
    elif envelope_operation == RecordType.OWNER_DECISION_RECORD:
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


def _run_dispatch(args: argparse.Namespace) -> dict[str, Any]:
    """AIPOS-FND-12: Generate executor dispatch command (claim via connector, not file path).
    
    Output: a command string that invokes `lybra agent materialize` with all required params.
    The executor runs this command, which forces claim→materialize flow (gate-recorded, no bypass).
    """
    from pathlib import Path
    
    task_id = args.task_id
    executor = args.executor
    
    # Resolve workspace config to get gate URL and connection.json
    try:
        workspace_root = _resolve_workspace_for_command(args)
    except Exception:
        # If workspace resolution fails, use explicit args or defaults
        workspace_root = Path(getattr(args, "workspace_root", None) or ".").expanduser()
    
    # Gate URL: explicit > workspace config > default
    gate_url = args.gate_url
    if not gate_url:
        try:
            config = load_workspace_config(workspace_root)
            gate_url = config.get("mcp_url") or f"http://{config.get('mcp_host', DEFAULT_MCP_HOST)}:{config.get('mcp_port', DEFAULT_MCP_PORT)}"
        except Exception:
            gate_url = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}"
    
    # Connection JSON: explicit > workspace default
    connection_json = args.connection_json
    if not connection_json:
        connection_json = str(workspace_root / ".lybra" / "connection.json")
    
    # Owner policy ref: explicit or use task's declared policy
    owner_policy_ref = args.owner_policy_ref
    if not owner_policy_ref:
        # AIPOS-R6C ⑩: 自发现全序 (.lybra/role → env → 显式)
        # Try to load task and extract policy from frontmatter
        try:
            from tools.aipos_cli.task_loader import find_task_by_id, find_repo_root
            from tools.aipos_cli.policy_resolver import find_active_policy
            
            repo_root = find_repo_root(workspace_root)
            task, _ = find_task_by_id(task_id, repo_root)
            if task:
                metadata = task.get("metadata", {})
                owner_policy_ref = metadata.get("owner_policy_ref")
            
            # Fallback to policy resolver autodiscovery
            if not owner_policy_ref:
                owner_policy_ref = find_active_policy(workspace_root, role="exec", policy_type="dev")
            
            if not owner_policy_ref:
                print("Error: Could not resolve owner_policy_ref. Specify --owner-policy-ref or ensure active policy exists.", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error resolving owner_policy_ref: {e}", file=sys.stderr)
            sys.exit(1)
    
    # Material root: explicit > default
    material_root = args.material_root or "~/.lybra/work"
    
    # Build the dispatch command
    cmd_parts = [
        "lybra agent materialize",
        f"--task-id {task_id}",
        f"--actor {executor}",
        f"--owner-policy-ref {owner_policy_ref}",
        f"--gate-url {gate_url}",
        f"--connection-json {connection_json}",
        f"--material-root {material_root}",
    ]
    
    dispatch_command = " ".join(cmd_parts)
    
    return {
        "ok": True,
        "operation": "dispatch",
        "task_id": task_id,
        "executor": executor,
        "dispatch_command": dispatch_command,
        "gate_url": gate_url,
        "connection_json": connection_json,
        "owner_policy_ref": owner_policy_ref,
        "material_root": material_root,
        "usage_hint": (
            "Give this command to the executor. They run it to claim and materialize the task. "
            "DO NOT give file paths directly — this enforces claim via connector (gate-recorded)."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project OS CLI")
    parser.add_argument("--workspace-root", dest="global_workspace_root", help="Workspace root; may also be provided on supported subcommands")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a Lybra workspace from a bundled template")
    init_parser.add_argument("output", nargs="?", help="Target workspace path (defaults to ~/.lybra/workspaces/<project-id>/)")
    init_parser.add_argument("--project-id", required=True, help="Workspace project_id")
    init_parser.add_argument("--template", default="blank", help="Bundled template name; defaults to blank")
    init_parser.add_argument("--var", action="append", default=[], help="Additional template variable in k=v form")
    init_parser.add_argument("--actor", default="owner", help="Actor requesting init; defaults to owner")
    init_parser.add_argument("--dry-run", action="store_true", help="Preview planned writes without creating the workspace")
    init_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-248: agent-side connector — a STATELESS pull over the gate read tool. The
    # loop host is the AGENT-side process (never a Lybra daemon); role-agnostic client.
    # Distinct from `agents` below (recorded-profile rendering) — disclosed:
    # /agents = recorded snapshot, `agent watch` = client-side loop.
    agent_parser = subparsers.add_parser(
        "agent",
        help="Agent-side connector: fetch claimable tasks (gate pull) / bounded watch — "
        "two harness modes (候选⑤⑫合流): --workspace-root = filesystem pump (AIPOS-268, any bash agent); "
        "--gate-url = stateless gate pull (AIPOS-248)",
    )
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command")
    # `agent fetch` (candidate ⑤, AIPOS-248): one stateless gate pull — byte-identical to before.
    _fetch_parser = agent_subparsers.add_parser(
        "fetch", help="One stateless pull: tasks claimable by --actor (advisory list; the gate is the truth)"
    )
    _fetch_parser.add_argument("--gate-url", required=True, help="Gate URL (e.g. http://127.0.0.1:7118)")
    _fetch_src = _fetch_parser.add_mutually_exclusive_group(required=True)
    _fetch_src.add_argument("--connection-json", help="path to connection.json (token read by --role; never on argv)")
    _fetch_src.add_argument("--token-env", help="env var holding the role bearer token")
    _fetch_parser.add_argument("--role", default="executor", help="role token to read (role-agnostic client; default executor)")
    _fetch_parser.add_argument("--actor", required=True, help="your agent/actor name (matched against assigned_to/agent_instance)")
    _fetch_parser.add_argument("--json", action="store_true", help="Output JSON")
    # `agent watch` (候选⑤⑫合流): two MUTUALLY EXCLUSIVE harness modes. --workspace-root
    # (candidate ⑫, AIPOS-268) = the harness-agnostic filesystem pump (no gate/MCP/token,
    # any agent that can run bash); --gate-url (candidate ⑤, AIPOS-248) = the stateless
    # gate pull for claimable tasks. Both are foreground, bounded, client-side loops.
    _watch_parser = agent_subparsers.add_parser(
        "watch",
        help="Foreground BOUNDED client loop. Two modes (候选⑤⑫合流): "
        "--workspace-root = filesystem mtime pump (candidate ⑫, AIPOS-268+284+284C+284D; any bash agent, no gate); "
        "--gate-url = stateless gate pull for claimable tasks (candidate ⑤, AIPOS-248). "
        "Exit codes: 0=change/expect satisfied, 2=timeout, 3=end-pattern but no product, 4=stall, 130=signal. "
        "Stream mode (--stream): emits 'kind:end' event before timeout/signal exit.",
    )
    _watch_mode = _watch_parser.add_mutually_exclusive_group(required=True)
    _watch_mode.add_argument(
        "--workspace-root",
        help="候选⑫ filesystem pump (AIPOS-268+284): poll 5_tasks/queue/** + 5_tasks/records/** mtime+path; "
        "print a JSON change summary on the first change (exit 0); exit 2 silent on --timeout. No gate/token.",
    )
    _watch_mode.add_argument("--gate-url", help="候选⑤ gate pull (AIPOS-248): Gate URL (e.g. http://127.0.0.1:7118)")
    _watch_gate_src = _watch_parser.add_mutually_exclusive_group(required=False)
    _watch_gate_src.add_argument("--connection-json", help="[gate mode] path to connection.json (token read by --role; never on argv)")
    _watch_gate_src.add_argument("--token-env", help="[gate mode] env var holding the role bearer token")
    _watch_parser.add_argument("--role", default="executor", help="[gate mode] role token to read (role-agnostic client; default executor)")
    _watch_parser.add_argument("--actor", help="[gate mode] your agent/actor name (matched against assigned_to/agent_instance)")
    # --interval is shared by both modes with DIFFERENT defaults (pump 15 / gate 60); the
    # argparse default is None and each mode resolves its own default in the dispatch.
    _watch_parser.add_argument("--interval", type=float, default=None, help="poll interval seconds. Filesystem pump default 15; gate pull default 60 (hard floor 15).")
    _watch_parser.add_argument("--max-wait", type=float, default=1800.0, help="[gate mode] bounded wait seconds before a clean exit (default 1800)")
    _watch_parser.add_argument("--timeout", type=float, default=None, help="[filesystem pump] no-change timeout seconds -> silent exit 2 (default: 1800 for default mode, infinite for --stream mode; 0 = explicit infinite)")
    # AIPOS-284 v2: three "death silence" semantics
    _watch_parser.add_argument("--expect", action="append", help="[filesystem pump v2] glob pattern for expected artifact; check immediately on startup and every poll (布防即检). Can be repeated. Exit 0 when any match.")
    _watch_parser.add_argument("--run-log", help="[filesystem pump v2] path to run log (for end-pattern and stall detection)")
    _watch_parser.add_argument("--end-pattern", help="[filesystem pump v2] regex: if found in run-log but --expect NOT satisfied, exit 3 after one grace poll (结束无产物)")
    _watch_parser.add_argument("--stall-secs", type=float, default=None, help="[filesystem pump v2] silence threshold seconds (default 600). If run-log (or observation surface) mtime unchanged for ≥N seconds, exit 4 (静默停滞)")
    # AIPOS-284C: --stream mode (persistent observer, event lines, no exit on change/stall/run_end)
    _watch_parser.add_argument("--stream", action="store_true", help="[filesystem pump v3/AIPOS-284C] persistent mode: emit JSON event lines (kind: expect|change|stall|run_end|end) and continue. Only timeout/signal exits (emits 'end' event). Event deduplication: expect files reported once (new only).")
    # AIPOS-284D: --events filter (F-284C-1 抑噪)
    _watch_parser.add_argument("--events", choices=["expect", "change", "all"], help="[filesystem pump v4/AIPOS-284D] event filter: 'expect' = only expect events; 'change' = only filesystem changes; 'all' = both. Default: 'expect' when --expect is given, 'all' otherwise (F-284C-1 抑噪).")
    # AIPOS-295: health monitoring (requires --stream)
    _watch_parser.add_argument("--health", type=float, metavar="SECS", help="[AIPOS-295] Enable health monitoring: emit 'kind:health' heartbeat every SECS seconds (default: 300). Requires --stream. Reports proc_alive, cpu_delta, new_session_files, worktree_changes, silent_secs.")
    _watch_parser.add_argument("--pid-file", help="[AIPOS-295] PID file path for process tree monitoring (reads parent PID, monitors pi children excluding timeout wrapper)")
    _watch_parser.add_argument("--proc-pattern", help="[AIPOS-295] Process name pattern to monitor (e.g., 'node' for pi). Excludes timeout/bash wrappers.")
    _watch_parser.add_argument("--session-dirs", help="[AIPOS-295] Comma-separated session storage directories to monitor for new files")
    _watch_parser.add_argument("--worktree-path", help="[AIPOS-295] Git worktree path to monitor for changes (default: parent of workspace-root)")
    _watch_parser.add_argument("--unhealthy-cycles", type=int, default=2, help="[AIPOS-295] Consecutive silent health cycles before emitting 'unhealthy' event (default: 2)")
    
    # `agent supervise` (AIPOS-295): health monitoring with bounded auto-restart
    _supervise_parser = agent_subparsers.add_parser(
        "supervise",
        help="[AIPOS-295] Health monitoring with bounded auto-restart. Spawns a command, monitors health, "
        "and implements bounded self-healing (1 respawn, then ESCALATE). Exit 75 on escalation (RestartPreventExitStatus)."
    )
    _supervise_parser.add_argument("--spawn-cmd", required=True, help="Command to spawn (must include timeout wrapper)")
    _supervise_parser.add_argument("--workspace-root", required=True, help="Lybra workspace root")
    _supervise_parser.add_argument("--product-repo", help="Product repo root (default: ~/projects/lybra)")
    _supervise_parser.add_argument("--card-id", required=True, help="Task card ID (for ESCALATE file)")
    _supervise_parser.add_argument("--health-interval", type=float, default=300, help="Health check interval seconds (default: 300)")
    _supervise_parser.add_argument("--pid-file", help="PID file path (optional, for process monitoring)")
    _supervise_parser.add_argument("--proc-pattern", help="Process name pattern (e.g., 'node' for pi)")
    _supervise_parser.add_argument("--session-dirs", help="Comma-separated session directories")
    _supervise_parser.add_argument("--worktree-path", help="Git worktree path (default: product-repo)")
    _supervise_parser.add_argument("--run-log", help="Run log path (for stall detection)")
    
    # `agent launch-check` (AIPOS-295C): 开工确认 + 首刻失败自愈
    _launch_check_parser = agent_subparsers.add_parser(
        "launch-check",
        help="[AIPOS-295C] 开工确认 + 首刻失败自愈. Verifies agent actually starts working (not just process exists). "
        "Implements bounded retry (1 relaunch) and writes BLOCK on double failure. Exit 2 on BLOCK."
    )
    _launch_check_parser.add_argument("--spawn-cmd", required=True, help="Command to spawn (must include timeout wrapper)")
    _launch_check_parser.add_argument("--task-id", required=True, help="Task card ID (e.g., AIPOS-295C)")
    _launch_check_parser.add_argument("--executor-instance", required=True, help="Executor agent instance name")
    _launch_check_parser.add_argument("--product-repo", help="Product repo root (default: ~/projects/lybra)")
    _launch_check_parser.add_argument("--session-dirs", help="Comma-separated session directories to monitor")
    _launch_check_parser.add_argument("--worktree-path", help="Git worktree path (default: product-repo)")
    # AIPOS-332F4: CLI 兆底默认从 90→180(慢端点冷启动实测 ~60s 留裕量)
    _launch_check_parser.add_argument("--launch-window", type=float, default=180, help="Launch verification window seconds (default: 180, AIPOS-332F4)")
    _launch_check_parser.add_argument("--check-interval", type=float, default=5, help="Poll interval seconds (default: 5)")
    _launch_check_parser.add_argument("--model-fallback-policy", help="JSON file with model substitution policy (optional)")

    # AIPOS-363 S1/S2: `agent materialize` / `agent pushback` — the cross-machine adaptation
    # layer. materialize = claim + pull body (319) + drop LOCAL material + print a zero-gate-verb
    # kickoff; pushback = read LOCAL RETURN + push via 320 + self-confirm (328). The agent only
    # reads/writes LOCAL files (card S3: harness-agnostic baseline). gate-url mode only.
    def _add_material_common(p, *, require_actor: bool = True) -> None:
        p.add_argument("--gate-url", required=True, help="Gate URL (e.g. http://127.0.0.1:7118, gate-url mode only)")
        _src = p.add_mutually_exclusive_group(required=True)
        _src.add_argument("--connection-json", help="path to connection.json (token read by --role; never on argv)")
        _src.add_argument("--token-env", help="env var holding the role bearer token")
        p.add_argument("--role", default="executor", help="role token to read (default executor)")
        p.add_argument("--actor", required=require_actor, help="your agent/actor name (must match the claim token binding)")
        p.add_argument("--task-id", required=True, help="task card id to materialize / push back")
        p.add_argument("--owner-policy-ref", required=True, help="owner_policy_ref for claim/return (PreAuthorized envelope id)")
        p.add_argument("--material-root", help="material area root (default ~/.lybra/work; env LYBRA_MATERIAL_ROOT)")
        p.add_argument("--actual-model", default="", help="capability-ledger: self-reported model (recorded, never verified)")
        p.add_argument("--json", action="store_true", help="Output JSON")

    _materialize_parser = agent_subparsers.add_parser(
        "materialize",
        help="[AIPOS-363 S1] Cross-machine: claim + pull card body via gate + drop LOCAL material + "
             "print a zero-gate-verb kickoff (any harness that reads a file). gate-url mode only.",
    )
    _add_material_common(_materialize_parser)
    _materialize_parser.add_argument("--autonomy-mode", default="PreAuthorized", help="claim autonomy_mode (default PreAuthorized)")
    _materialize_parser.add_argument("--gate-workspace", default="", help="gate workspace root (recorded in MANIFEST for traceability)")

    _pushback_parser = agent_subparsers.add_parser(
        "pushback",
        help="[AIPOS-363 S2] Cross-machine: read LOCAL RETURN.md + push back via gate (320) + "
             "self-confirm (328). On failure emits a blocked event (323) — never silent.",
    )
    _add_material_common(_pushback_parser)

    board_parser = subparsers.add_parser("board", help="Start the local Lybra Board")
    board_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    board_parser.add_argument("--host", help="Bind host; defaults to 127.0.0.1")
    board_parser.add_argument("--port", type=int, help=f"Bind port; defaults to {DEFAULT_BOARD_PORT}")
    # AIPOS-271: board 子命令 —— start(旧版默认)/open(本机无感)/approve(跨机设备码)。
    # ``lybra board``(无子命令)仍启动 server(向后兼容,零回归)。
    board_sub = board_parser.add_subparsers(dest="board_command")
    _board_start_parser = board_sub.add_parser("start", help="Start the local Lybra Board server (default)")
    _board_start_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    _board_start_parser.add_argument("--host", help="Bind host; defaults to 127.0.0.1")
    _board_start_parser.add_argument("--port", type=int, help=f"Bind port; defaults to {DEFAULT_BOARD_PORT}")
    board_open_parser = board_sub.add_parser("open", help="AIPOS-271: open the Board in the browser with a one-time ticket (no token pasting)")
    board_open_parser.add_argument("--workspace-root", help="Workspace root for auto-discovery; defaults to current directory or env")
    board_open_parser.add_argument("--connection-json", help="Override the connection.json path (default <workspace>/.lybra/connection.json)")
    board_open_parser.add_argument("--role", help="Role token to read from connection.json; default prefers owner then first available")
    board_open_parser.add_argument("--url", help="Board server base URL; overrides connection.json board.url")
    board_open_parser.add_argument("--host", help="Board server host; overrides connection.json")
    board_open_parser.add_argument("--port", type=int, help="Board server port; overrides connection.json")
    board_open_parser.add_argument("--no-browser", action="store_true", help="Print the login URL instead of opening a browser")
    board_approve_parser = board_sub.add_parser("approve", help="AIPOS-271: approve a cross-machine device code (run on the gate machine)")
    board_approve_parser.add_argument("code", help="6-digit device code shown in the remote browser")
    board_approve_parser.add_argument("--workspace-root", help="Workspace root for auto-discovery; defaults to current directory or env")
    board_approve_parser.add_argument("--connection-json", help="Override the connection.json path (default <workspace>/.lybra/connection.json)")
    board_approve_parser.add_argument("--role", help="Role token to read from connection.json; default prefers owner then first available")
    board_approve_parser.add_argument("--url", help="Board server base URL; overrides connection.json board.url")
    board_approve_parser.add_argument("--host", help="Board server host; overrides connection.json")
    board_approve_parser.add_argument("--port", type=int, help="Board server port; overrides connection.json")

    # AIPOS-205: TUI client over an Owner-started gate. The Textual dependency lives only
    # in tools/lybra_tui (the tui extra); this CLI stays stdlib/zero-dep and lazy-imports it.
    tui_parser = subparsers.add_parser("tui", help="Launch the Lybra TUI client (requires the TUI extra: pip install textual)")
    tui_parser.add_argument("--gate-url", required=True, help="Owner-started gate URL (e.g. http://127.0.0.1:7118)")
    tui_parser.add_argument("--connection-json", help="Path to .lybra/connection.json (token read by role)")
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
    mcp_config_parser.add_argument("--port", type=int, help=f"MCP port; defaults to workspace config or {DEFAULT_MCP_PORT}")
    mcp_config_parser.add_argument("--transport-token-env", help="Transport token env var; defaults to LYBRA_MCP_TOKEN")
    mcp_config_parser.add_argument("--capability-token-env", help="Capability token env var; defaults to LYBRA_CAPABILITY_TOKEN")
    mcp_config_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-12: dispatch — 产出给执行体的是认领命令(经连接器),不是队列文件路径
    dispatch_parser = subparsers.add_parser("dispatch", help="Generate executor dispatch command (claim via connector, not file path)")
    dispatch_parser.add_argument("task_id", help="Task ID to dispatch")
    dispatch_parser.add_argument("--to", dest="executor", required=True, help="Executor actor/instance name")
    dispatch_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    dispatch_parser.add_argument("--gate-url", help="Gate URL; defaults to workspace config or http://127.0.0.1:7118")
    dispatch_parser.add_argument("--owner-policy-ref", help="Owner policy reference (PreAuthorized envelope)")
    dispatch_parser.add_argument("--connection-json", help="Path to connection.json (for token resolution)")
    dispatch_parser.add_argument("--material-root", help="Material area root (default ~/.lybra/work)")
    dispatch_parser.add_argument("--json", action="store_true", help="Output JSON")

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

    sync_parser = subparsers.add_parser("sync", help="AIPOS-C4B: worker-initiated distribution pull (lybra sync)")
    sync_parser.add_argument("--harness-root", default=None, help="Harness root (REQUIRED: no cwd guessing; fallback env LYBRA_HARNESS_ROOT)")
    sync_parser.add_argument("--gate-url", default=None, help="Gate MCP URL (auto from .lybra if omitted)")
    sync_parser.add_argument("--token", default=None, help="Bearer token (auto from .lybra connection.json if omitted)")
    sync_parser.add_argument("--json", action="store_true", help="Output JSON")

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

    queue_amend_parser = queue_subparsers.add_parser("amend", help="Amend a pending task")
    queue_amend_parser.add_argument("--task-id", required=True, help="Task ID to amend")
    queue_amend_parser.add_argument("--actor", required=True, help="Actor performing the amendment")
    queue_amend_parser.add_argument("--amendments", required=True, help="JSON dict of amendments")
    queue_amend_parser.add_argument("--amendment-reason", required=True, help="Reason for amendment")
    queue_amend_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_amend_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_withdraw_parser = queue_subparsers.add_parser("withdraw", help="Withdraw a task from queue")
    queue_withdraw_parser.add_argument("--task-id", required=True, help="Task ID to withdraw")
    queue_withdraw_parser.add_argument("--actor", required=True, help="Actor performing the withdrawal")
    queue_withdraw_parser.add_argument("--reason", required=True, help="Reason for withdrawal")
    queue_withdraw_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_withdraw_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_return_repair_parser = queue_subparsers.add_parser("return-repair", help="Repair a stuck return")
    queue_return_repair_parser.add_argument("--task-id", required=True, help="Task ID with stuck return")
    queue_return_repair_parser.add_argument("--actor", required=True, help="Actor performing the repair")
    queue_return_repair_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_return_repair_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-1: queue return subcommand (same-machine task return)
    queue_return_parser = queue_subparsers.add_parser("return", help="Return completed task (same-machine)")
    queue_return_parser.add_argument("--task-id", required=True, help="Task ID to return")
    queue_return_parser.add_argument("--actor", required=True, help="Actor returning the task")
    queue_return_parser.add_argument("--agent-instance", required=True, help="Agent instance name")
    queue_return_parser.add_argument("--result-summary", required=True, help="Result summary")
    queue_return_parser.add_argument("--owner-policy-ref", required=True, help="Owner policy reference")
    queue_return_parser.add_argument("--artifact-refs", help="JSON array of artifact references")
    queue_return_parser.add_argument("--completion-report-ref", help="Completion report reference")
    queue_return_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_return_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-C1 大项A: queue close subcommand (derived from verbs.schema lybra_queue_close)
    queue_close_parser = queue_subparsers.add_parser("close", help="Close a claimed task with closure evidence (AIPOS-283)")
    queue_close_parser.add_argument("--task-id", required=True, help="Task ID to close")
    queue_close_parser.add_argument("--actor", required=True, help="Actor performing the close")
    queue_close_parser.add_argument("--closure-evidence", required=True, help="JSON object with at least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref")
    queue_close_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_close_parser.add_argument("--json", action="store_true", help="Output JSON")

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
    # AIPOS-C3B 大项C①: state lint — 卡状态三方一致 lint(队列目录×frontmatter status×records)
    lint_parser = state_subparsers.add_parser("lint", help="AIPOS-C3B: 卡状态三方一致 lint(队列目录×frontmatter×records)")
    lint_parser.add_argument("--workspace-root", type=Path, default=None, help="Governance workspace root (default: auto-detect)")
    lint_parser.add_argument("--task-id", default=None, help="Limit to specific task ID")
    lint_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-C3B 大项C③: state repair — 按 records 重建卡一致状态
    repair_parser = state_subparsers.add_parser("repair", help="AIPOS-C3B: 按 records 重建卡一致状态(坏卡修复)")
    repair_parser.add_argument("--task-id", required=True, help="Task ID to repair")
    repair_parser.add_argument("--workspace-root", type=Path, default=None, help="Governance workspace root (default: auto-detect)")
    repair_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    repair_parser.add_argument("--json", action="store_true", help="Output JSON")
    recovery_parser = state_subparsers.add_parser("recovery", help="State recovery preview operations")
    recovery_subparsers = recovery_parser.add_subparsers(dest="recovery_command")
    recovery_preview_parser = recovery_subparsers.add_parser("preview", help="Preview file-authoritative recovery state")
    _task_lookup_arguments(recovery_preview_parser)
    recovery_preview_parser.add_argument("--dry-run-token", help="Optional dry-run token to classify for staleness")
    recovery_preview_parser.add_argument("--expected-operation", help="Optional expected operation for dry-run token compatibility")
    recovery_preview_parser.add_argument("--json", action="store_true", help="Output JSON")

    agents_parser = subparsers.add_parser("agents", help="Render agent profiles")
    agents_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-358: auditor thin shell (退役私有编排,定时器直驱 turn-advancer)
    auditor_parser = subparsers.add_parser("auditor", help="AIPOS-358: Auditor daemon operations (thin shell)")
    auditor_subparsers = auditor_parser.add_subparsers(dest="auditor_command")
    auditor_loop_parser = auditor_subparsers.add_parser(
        "loop",
        help="AIPOS-358: Auditor thin shell daemon. Calls turn-advancer scan --mode auto on a timer. Never exits due to business results."
    )
    auditor_loop_parser.add_argument("--workspace-root", required=True, help="Lybra workspace root (治理仓)")
    auditor_loop_parser.add_argument("--interval", type=float, default=20.0, help="Scan interval seconds (default: 20)")
    # Retained args for backward compat (systemd unit may pass them); ignored by thin shell.
    auditor_loop_parser.add_argument("--product-repo", help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--gate-url", help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--connection-json", help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--auditor-instance", help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--policy", "--envelope", dest="envelope", help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--runtime-cmd", help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--timeout", type=float, help=argparse.SUPPRESS)
    auditor_loop_parser.add_argument("--claim-transient-tries", type=int, help=argparse.SUPPRESS)
    # AIPOS-358: auditor launch (执行出口, 由 turn-advancer dispatch_audit 调用)
    auditor_launch_parser = auditor_subparsers.add_parser(
        "launch",
        help="AIPOS-358: Launch auditor agent for a specific audit card (called by turn-advancer dispatch_audit)."
    )
    auditor_launch_parser.add_argument("--task-id", required=True, help="Audit task ID")
    auditor_launch_parser.add_argument("--reviewed-task-id", default="", help="Reviewed (audited) task ID")
    auditor_launch_parser.add_argument("--workspace-root", required=True, type=Path, help="Workspace root")
    auditor_launch_parser.add_argument("--product-repo", type=Path, help="Product repo (default: ~/projects/lybra)")
    auditor_launch_parser.add_argument("--envelope", default="pol_lybra_audit_1", help="PreAuthorized envelope ref")
    auditor_launch_parser.add_argument("--audit-cards-path", default="", help="Path to the audit card file")
    auditor_launch_parser.add_argument(
        "--runtime-cmd",
        default="pi --model anthropic/claude-3-5-sonnet-20241022 --prompt '{kickoff}'",
        help="Auditor runtime command template"
    )

    # AIPOS-FND-7: audit dispatch 顶级命令（派审自动建记录）
    audit_dispatch_parser = subparsers.add_parser("audit", help="Audit operations")
    audit_subparsers = audit_dispatch_parser.add_subparsers(dest="audit_command")
    
    dispatch_parser = audit_subparsers.add_parser("dispatch", help="Dispatch audit for a completed task")
    dispatch_parser.add_argument("--source-task-id", "--task-id", dest="source_task_id", help="Source task ID")
    dispatch_parser.add_argument("--source-task-path", "--task-path", dest="source_task_path", help="Source task path")
    dispatch_parser.add_argument("--actor", required=True, help="Actor dispatching audit")
    dispatch_parser.add_argument("--agent-instance", required=True, help="Agent instance (dispatcher)")
    dispatch_parser.add_argument("--owner-policy-ref", required=True, help="Owner policy reference")
    dispatch_parser.add_argument("--audit-task-id", required=True, help="Audit task ID")
    dispatch_parser.add_argument("--audit-task-title", help="Audit task title")
    dispatch_parser.add_argument("--audit-by", help="Auditor role/instance")
    dispatch_parser.add_argument("--audit-agent-instance", required=True, help="Auditor agent instance")
    dispatch_parser.add_argument("--dispatch-reason", help="Dispatch reason")
    dispatch_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    dispatch_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-370 / FND-15: audit-verdict 顶级命令（真落库过 gate MCP）
    # F-R4B2-1: verdict choices 从 enums.schema 读（唯一权威）
    from tools.schema_loader import get_enum_values
    verdict_choices = get_enum_values("verdict")
    
    audit_verdict_parser = subparsers.add_parser("audit-verdict", help="Submit audit verdict for a reviewed task (via gate MCP)")
    audit_verdict_parser.add_argument("--audit-task-id", help="Audit task ID (optional)")
    audit_verdict_parser.add_argument("--reviewed-task-id", required=True, help="Reviewed task ID")
    audit_verdict_parser.add_argument("--actor", help="AIPOS-R4B-2: Actor submitting verdict (auto-discovered if not provided)")
    audit_verdict_parser.add_argument("--agent-instance", help="AIPOS-R4B-2: Agent instance (auto-discovered if not provided)")
    audit_verdict_parser.add_argument("--owner-policy-ref", help="AIPOS-R4B-2: Owner policy reference (auto-discovered if not provided)")
    audit_verdict_parser.add_argument("--verdict", required=True, choices=verdict_choices, help="Verdict (from enums.schema.json)")
    audit_verdict_parser.add_argument("--findings-summary", help="Findings summary")
    audit_verdict_parser.add_argument("--evidence-refs", help="JSON list of evidence references")
    audit_verdict_parser.add_argument("--audit-claim-id", help="Audit claim ID")
    audit_verdict_parser.add_argument("--audit-session-id", help="Audit session ID")
    audit_verdict_parser.add_argument("--audit-dispatch-record-ref", help="Audit dispatch record reference")
    audit_verdict_parser.add_argument("--reviewed-return-record-ref", help="Reviewed return record reference")
    audit_verdict_parser.add_argument("--recommended-next-action", help="Recommended next action")
    audit_verdict_parser.add_argument("--owner-waiver-ref", help="Owner waiver reference")
    audit_verdict_parser.add_argument("--gate-url", default=None, help="Gate MCP server URL (default: http://127.0.0.1:7118)")
    audit_verdict_parser.add_argument("--connection-json", help="Path to connection.json (default: .lybra/connection.json in workspace)")
    audit_verdict_parser.add_argument("--token-role", default="auditor", help="Token role in connection.json (default: auditor)")
    audit_verdict_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-325: pump 子命令 (kickoff 三层制约 + 产品 CLI 入口)
    pump_parser = subparsers.add_parser("pump", help="AIPOS-325: Advisor pump operations with kickoff constraints")
    pump_subparsers = pump_parser.add_subparsers(dest="pump_command")
    pump_run_parser = pump_subparsers.add_parser(
        "run",
        help="Dispatch a task with kickoff three-layer constraints (generated kickoff + budget limit + repetition check)"
    )
    pump_run_parser.add_argument("--card", "--card-id", dest="card_id", required=True, help="Task card ID (e.g., AIPOS-325)")
    pump_run_parser.add_argument("--role", required=True, choices=["executor", "auditor"], help="Target role (executor or auditor)")
    pump_run_parser.add_argument("--round-type", default="first", choices=["first", "fix", "resume"], help="Round type: first (default), fix (repair), or resume (continue)")
    pump_run_parser.add_argument("--delta", default="", help="Incremental information for this round (advisor provides only delta)")
    pump_run_parser.add_argument("--workspace-root", required=True, help="Lybra workspace root (governance repo)")
    pump_run_parser.add_argument("--product-repo", help="Product repo root (default: ~/projects/lybra)")
    pump_run_parser.add_argument("--gate-url", default=None, help="Gate URL (default: http://127.0.0.1:7118)")
    pump_run_parser.add_argument("--connection-json", help="Path to connection.json (default: <workspace>/.lybra/connection.json)")
    pump_run_parser.add_argument("--envelope", help="Policy envelope ID (auto-detect from policies/ if not provided)")
    pump_run_parser.add_argument("--budget-threshold", type=int, default=8000, help="Budget threshold in tokens (default: 8000)")
    pump_run_parser.add_argument("--repetition-threshold", type=float, default=0.3, help="Repetition overlap threshold 0.0-1.0 (default: 0.3)")
    pump_run_parser.add_argument("--dry-run", action="store_true", help="Validate kickoff constraints without actual dispatch")
    pump_run_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-332: 编排式全程派工的新参数(默认关,不改既有参数语义 S6④)
    pump_run_parser.add_argument("--runtime", default=None, help="[AIPOS-332] 运行体类型档案(pi/cc/claude_code/generic_bash);决定观测面选择")
    pump_run_parser.add_argument("--output-target", default=None, help="[AIPOS-332] 任务产出位置(tools/docs/config/remote/workspace_only);决定 worktree 判据是否适用")
    pump_run_parser.add_argument("--runtime-cmd", default=None, help="[AIPOS-332] 拉起命令模板(含 {kickoff} 占位);非 dry-run 时必需,判断留人")
    pump_run_parser.add_argument("--reviewed-task-id", default=None, help="[AIPOS-332] 审计裁决落该(被审卡)ID 目录;role=auditor 时用")
    pump_run_parser.add_argument("--executor-instance", default=None, help="[AIPOS-332] 执行体实例名(默认 <role>.lybra.kiwiai-dev)")
    pump_run_parser.add_argument("--workdir", default=None, help="[AIPOS-332F5] 运行体真实工作目录(用于会话目录编码);不配则会话判据不可用")
    pump_run_parser.add_argument("--runtime-cmds-yaml", default=None, help="[AIPOS-332F5] runtime_cmds.yaml 路径(从中读 workdir 等配置)")
    pump_run_parser.add_argument("--check-unmanaged", action="store_true", help="[AIPOS-332 S3] 只读列出非泵派出的在跑 agent,后退出")

    mcp_parser = subparsers.add_parser("mcp", help="Start MCP HTTP/SSE or run MCP setup diagnostics")
    mcp_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    mcp_parser.add_argument("--host", help="Bind host; defaults to 127.0.0.1")
    mcp_parser.add_argument("--port", type=int, help=f"Bind port; defaults to {DEFAULT_MCP_PORT}")
    mcp_parser.add_argument("--keepalive-seconds", type=float, help="SSE ping interval; defaults to 30 seconds")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_doctor_parser = mcp_subparsers.add_parser("doctor", help="Inspect MCP transport auth and capability scopes")
    mcp_doctor_parser.add_argument("--json", action="store_true", help="Output JSON")

    serve_parser = subparsers.add_parser("serve", help="Start and inspect local Lybra gate service mode")
    serve_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    # AIPOS-349: connection.json defaults to <workspace>/.lybra/connection.json (workspace-scoped).
    # --connection-json overrides the location.
    serve_parser.add_argument("--connection-json", help="Override the connection.json path (default <workspace>/.lybra/connection.json)")
    serve_subparsers = serve_parser.add_subparsers(dest="serve_command")
    serve_start_parser = serve_subparsers.add_parser("start", help="Start Board and MCP gate surfaces in foreground")
    serve_start_parser.add_argument("--board-host", default=None, help="Board BIND host (AIPOS-258: passed through to web.board.app --host). Default 127.0.0.1; AIPOS-259: when given, overrides a stored connection.json and is written back.")
    serve_start_parser.add_argument("--board-advertise", default=None, help="AIPOS-259: address clients should dial for the Board URL (default = bind host). REQUIRED when --board-host is a wildcard (0.0.0.0), else serve start BLOCKs fail-closed.")
    serve_start_parser.add_argument("--board-port", type=int, default=DEFAULT_BOARD_PORT, help=f"Board port; defaults to {DEFAULT_BOARD_PORT} (config.schema)")
    serve_start_parser.add_argument("--mcp-host", default=None, help="MCP BIND host (AIPOS-258: passed through to mcp_server serve-http --host). Default 127.0.0.1; AIPOS-259: when given, overrides a stored connection.json and is written back.")
    serve_start_parser.add_argument("--mcp-advertise", default=None, help="AIPOS-259: address clients should dial for rpc_url/sse_url (default = bind host). REQUIRED when --mcp-host is a wildcard (0.0.0.0), else serve start BLOCKs fail-closed.")
    serve_start_parser.add_argument("--mcp-port", type=int, default=DEFAULT_MCP_PORT, help=f"MCP port; defaults to {DEFAULT_MCP_PORT} (config.schema)")
    serve_start_parser.add_argument("--reuse-port", action="store_true", default=False, help="AIPOS-356: set SO_REUSEPORT on the MCP listening socket so a new process can bind the same port while the old one drains (graceful deploy handoff)")
    serve_start_parser.add_argument("--json", action="store_true", help="Output JSON after the supervisor exits")
    serve_status_parser = serve_subparsers.add_parser("status", help="Print redacted service-mode status")
    serve_status_parser.add_argument("--json", action="store_true", help="Output JSON")
    serve_stop_parser = serve_subparsers.add_parser("stop", help="Stop service-owned Board/MCP child processes")
    serve_stop_parser.add_argument("--json", action="store_true", help="Output JSON")
    serve_rotate_parser = serve_subparsers.add_parser("rotate", help="Rotate local service-mode role tokens")
    serve_rotate_parser.add_argument("--board-host", default=None, help="Board BIND host for regenerated connection config (default 127.0.0.1)")
    serve_rotate_parser.add_argument("--board-advertise", default=None, help="AIPOS-259: address clients dial for the Board URL (default = bind host; REQUIRED when --board-host is 0.0.0.0)")
    serve_rotate_parser.add_argument("--board-port", type=int, default=DEFAULT_BOARD_PORT, help="Board port for regenerated connection config")
    serve_rotate_parser.add_argument("--mcp-host", default=None, help="MCP BIND host for regenerated connection config (default 127.0.0.1)")
    serve_rotate_parser.add_argument("--mcp-advertise", default=None, help="AIPOS-259: address clients dial for rpc_url/sse_url (default = bind host; REQUIRED when --mcp-host is 0.0.0.0)")
    serve_rotate_parser.add_argument("--mcp-port", type=int, default=DEFAULT_MCP_PORT, help="MCP port for regenerated connection config")
    serve_rotate_parser.add_argument("--project", help="Scope the minted role tokens to this project (AIPOS-229: enforced — calls for another project return PROJECT_SCOPE_DENIED)")
    serve_rotate_parser.add_argument("--executor-instance", help="AIPOS-250B: bind the executor token to this canonical agent_instance (PreAuthorized identity authority); unspecified → no binding (backward-compatible: PreAuthorized unavailable, falls back Supervised)")
    serve_rotate_parser.add_argument("--role-instance", action="append", dest="role_instances", metavar="ROLE=INSTANCE", help="AIPOS-254: bind any role token to a canonical agent_instance (format: role=instance, e.g., auditor=audit.lybra.local); can be specified multiple times; --executor-instance is kept as an alias for executor role")
    # AIPOS-346 S1/S2: binding change confirmation + owner authorization
    serve_rotate_parser.add_argument("--confirm-binding-changes", action="store_true", help="AIPOS-346 S1: confirm explicit binding changes (without this, binding changes BLOCK)")
    serve_rotate_parser.add_argument("--actor", help="AIPOS-346 S2: who is performing the rotation (recorded in rotation log)")
    serve_rotate_parser.add_argument("--owner-authorization-ref", help="AIPOS-346 S2: reference to owner authorization for this rotation")
    serve_rotate_parser.add_argument("--roles", help="AIPOS-353: comma-separated list of roles to rotate (e.g. auditor or auditor,executor); unselected roles keep their existing tokens byte-for-byte. Omit for full rotation.")
    serve_rotate_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-346 S5: roles subcommand (first-class role management)
    roles_parser = subparsers.add_parser("roles", help="AIPOS-346 S5: first-class role management commands")
    roles_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    roles_parser.add_argument("--connection-json", help="Override the connection.json path")
    roles_subparsers = roles_parser.add_subparsers(dest="roles_command")
    roles_list_parser = roles_subparsers.add_parser("list", help="List all roles with instance bindings, compliance, fingerprints")
    roles_list_parser.add_argument("--json", action="store_true", help="Output JSON")
    roles_reconcile_parser = roles_subparsers.add_parser("reconcile", help="Compare actual vs expected roles (missing/extra/non-compliant/unbound)")
    roles_reconcile_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-352F1: custom role write-side entry points
    roles_register_parser = roles_subparsers.add_parser("register", help="AIPOS-352F1: register a custom role (name → builtin class mapping)")
    roles_register_parser.add_argument("name", help="Custom role name (lowercase, alphanumeric + hyphens, max 32 chars)")
    roles_register_parser.add_argument("--class", dest="builtin_class", required=True, help="Built-in role class to map to (e.g. executor, auditor)")
    roles_register_parser.add_argument("--owner-authorization-ref", help="AIPOS-346F2: reference to owner authorization for this registration")
    roles_register_parser.add_argument("--reason", default="", help="Reason for registering this custom role")
    roles_register_parser.add_argument("--json", action="store_true", help="Output JSON")
    roles_remove_parser = roles_subparsers.add_parser("remove", help="AIPOS-352F1: remove a custom role (idempotent); AIPOS-F21: --instance removes a token entry from connection.json")
    roles_remove_parser.add_argument("name", nargs="?", help="Custom role name to remove (or omit when using --instance)")
    roles_remove_parser.add_argument("--instance", help="AIPOS-F21: remove the token entry bound to this agent_instance (e.g., test.mac.aipos362) from connection.json")
    roles_remove_parser.add_argument("--owner-authorization-ref", help="AIPOS-346F2: reference to owner authorization for this removal")
    roles_remove_parser.add_argument("--reason", default="", help="Reason for removing this custom role")
    roles_remove_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F21: two-phase service-token rotation (credential rotation as a product action)
    roles_rotate_parser = roles_subparsers.add_parser("rotate", help="AIPOS-F21: rotate service tokens in connection.json (two-phase: --dry-run preview; execution needs --owner-authorization-ref)")
    roles_rotate_parser.add_argument("--dry-run", action="store_true", help="Phase 1: preview the fingerprints that would be rotated; lands NO change")
    roles_rotate_parser.add_argument("--owner-authorization-ref", help="Reference to owner authorization for this rotation (required for execution)")
    roles_rotate_parser.add_argument("--role", help="Comma-separated role subset to rotate (e.g., executor or executor,auditor); omit to rotate all roles")
    roles_rotate_parser.add_argument("--actor", help="Who is performing the rotation (recorded in the rotation record)")
    roles_rotate_parser.add_argument("--reason", default="", help="Reason for this rotation")
    roles_rotate_parser.add_argument("--no-reload", action="store_true", help="Skip the gate hot-reload attempt (still prints restart guidance)")
    roles_rotate_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-362: enrollment code management (remote agent credential enrollment)
    roles_enroll_code_parser = roles_subparsers.add_parser("enroll-code", help="AIPOS-362: generate a one-time enrollment code for remote agent credential bootstrap")
    roles_enroll_code_parser.add_argument("--role", required=True, help="Role to bind (e.g., executor, auditor, or custom role)")
    roles_enroll_code_parser.add_argument("--instance", help="Optional instance name to bind (e.g., exec.lybra.mac1); omit for any instance")
    roles_enroll_code_parser.add_argument("--ttl", type=int, help="Time-to-live in seconds (default 86400 = 24h; also bounds the embedded transport credential)")
    roles_enroll_code_parser.add_argument("--gate-url", help="Externally reachable gate URL to embed (default: connection.json mcp.rpc_url if non-loopback, else http://127.0.0.1:7118)")
    roles_enroll_code_parser.add_argument("--governance-root", help="Governance workspace root to embed (F24A): bare registered project name or absolute path; validated against the project registry on the gate. Default: workspace_root of the local connection.json")
    roles_enroll_code_parser.add_argument("--token-role", default="advisor", help="Role of the local token used to call the gate verb (default: advisor; falls back to owner if advisor is absent)")
    roles_enroll_code_parser.add_argument("--owner-authorization-ref", help="Reference to owner authorization for this enrollment")
    roles_enroll_code_parser.add_argument("--reason", default="", help="Reason for generating this enrollment code")
    roles_enroll_code_parser.add_argument("--json", action="store_true", help="Output JSON")
    roles_enroll_revoke_parser = roles_subparsers.add_parser("enroll-revoke", help="AIPOS-362: revoke an enrollment code")
    roles_enroll_revoke_parser.add_argument("code_id", help="Enrollment code ID to revoke")
    roles_enroll_revoke_parser.add_argument("--owner-authorization-ref", help="Reference to owner authorization for this revocation")
    roles_enroll_revoke_parser.add_argument("--reason", default="", help="Reason for revoking this enrollment code")
    roles_enroll_revoke_parser.add_argument("--json", action="store_true", help="Output JSON")
    roles_enroll_list_parser = roles_subparsers.add_parser("enroll-list", help="AIPOS-362: list enrollment codes")
    roles_enroll_list_parser.add_argument("--json", action="store_true", help="Output JSON")
    
    # AIPOS-R2: enroll command (client-side enrollment: exchange code + write .lybra/ config)
    roles_enroll_parser = roles_subparsers.add_parser("enroll", help="AIPOS-R2/F23: enroll this workstation (exchange enrollment code + write .lybra/ config). Self-contained code carries gate URL; run from the workstation directory")
    roles_enroll_parser.add_argument("--code", required=True, help="Enrollment code (self-contained LYBRAENROLL1.* from owner/advisor, or legacy plain code)")
    roles_enroll_parser.add_argument("--gate-url", help="Gate MCP URL override (self-contained codes embed it; legacy plain codes require this or LYBRA_GATE_URL)")
    roles_enroll_parser.add_argument("--workspace", help="Workstation root (defaults to current directory — must be the workstation dir, NOT the governance workspace)")
    roles_enroll_parser.add_argument("--policy", help="Optional policy reference")
    roles_enroll_parser.add_argument("--bootstrap-token", help="Legacy plain codes only: bootstrap token for HTTP transport auth (self-contained codes need none)")
    roles_enroll_parser.add_argument("--verify", action="store_true", help="AIPOS-R6S 大项C②: enroll 后立刻用新 token 调一次 gate, 不通即报错并回滚")
    roles_enroll_parser.add_argument("--json", action="store_true", help="Output JSON")

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
    # AIPOS-335 S4: list existing projects and their inferred collaboration_profile
    project_list_parser = project_subparsers.add_parser("list", help="AIPOS-335: List existing projects and their collaboration profiles")
    project_list_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_list_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-338 S5: workspace-level dispatch_mode switch (Owner-only)
    dm_parser = project_subparsers.add_parser("dispatch-mode", help="AIPOS-338: Show/set workspace dispatch_mode (auto|manual)")
    dm_sub = dm_parser.add_subparsers(dest="dispatch_mode_command")
    dm_show = dm_sub.add_parser("show", help="Show the current dispatch_mode (read-only)")
    dm_show.add_argument("--project-root", help="Project root (governance); defaults to resolver")
    dm_show.add_argument("--json", action="store_true", help="Output JSON")
    dm_set = dm_sub.add_parser("set", help="Set dispatch_mode (Owner-only; append-only logged)")
    dm_set.add_argument("--mode", required=True, choices=["auto", "manual"], help="Target mode")
    dm_set.add_argument("--project-root", help="Project root (governance); defaults to resolver")
    dm_set.add_argument("--by", default="owner", help="Who is switching (default: owner)")
    dm_set.add_argument("--reason", default="", help="Why (logged in the append-only trail)")
    dm_set.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-293: export/import project structure file
    project_export_parser = project_subparsers.add_parser("export", help="AIPOS-293: Export workspace structure to a YAML file")
    project_export_parser.add_argument("workspace_root", nargs="?", help="Workspace root to export (defaults to current workspace)")
    project_export_parser.add_argument("--output", "-o", help="Output file path (default: <workspace>/lybra-project.yaml)")
    project_export_parser.add_argument("--project-name", help="Override project name in the structure file")
    project_export_parser.add_argument("--json", action="store_true", help="Output JSON")
    project_import_parser = project_subparsers.add_parser("import", help="AIPOS-293: Import project from a structure file")
    project_import_parser.add_argument("structure_file", help="Path to lybra-project.yaml structure file")
    project_import_parser.add_argument("output_root", help="Target directory for the imported project")
    project_import_parser.add_argument("--dry-run", action="store_true", help="Preview planned writes without creating")
    project_import_parser.add_argument("--actor", default=default_actor, help="Actor for provenance (registered_by)")
    project_import_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-370: envelope mint command (owner-gated)
    envelope_parser = subparsers.add_parser("envelope", help="Owner autonomy envelope operations")
    envelope_subparsers = envelope_parser.add_subparsers(dest="envelope_command")
    envelope_mint_parser = envelope_subparsers.add_parser("mint", help="Mint a PreAuthorized autonomy envelope")
    envelope_mint_parser.add_argument("--policy-id", required=True, help="Policy ID")
    envelope_mint_parser.add_argument("--agent-or-role", required=True, help="Agent instance or role")
    envelope_mint_parser.add_argument("--max-tasks", type=int, required=True, help="Maximum tasks allowed")
    envelope_mint_parser.add_argument("--task-mode", help="Task mode selector (e.g., code)")
    envelope_mint_parser.add_argument("--expires-at", required=True, help="Expiration datetime (ISO8601)")
    envelope_mint_parser.add_argument("--decision-summary", required=True, help="Decision summary")
    envelope_mint_parser.add_argument("--actor", default="owner", help="Actor (default: owner)")
    envelope_mint_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    envelope_mint_parser.add_argument("--json", action="store_true", help="Output JSON")

    envelope_revoke_parser = envelope_subparsers.add_parser("revoke", help="Revoke (disable) an autonomy envelope")
    envelope_revoke_parser.add_argument("--policy-id", required=True, help="Policy ID to revoke")
    envelope_revoke_parser.add_argument("--revocation-reason", required=True, help="Reason for revocation")
    envelope_revoke_parser.add_argument("--actor", default="owner", help="Actor (default: owner)")
    envelope_revoke_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    envelope_revoke_parser.add_argument("--json", action="store_true", help="Output JSON")

    envelope_renew_parser = envelope_subparsers.add_parser("renew", help="Renew/extend an existing envelope")
    envelope_renew_parser.add_argument("--policy-id", required=True, help="Policy ID to renew")
    envelope_renew_parser.add_argument("--add-tasks", type=int, help="Additional tasks to add to quota")
    envelope_renew_parser.add_argument("--new-expiry", help="New expiration datetime (ISO8601)")
    envelope_renew_parser.add_argument("--decision-summary", required=True, help="Decision summary for renewal")
    envelope_renew_parser.add_argument("--actor", default="owner", help="Actor (default: owner)")
    envelope_renew_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    envelope_renew_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-R7A: Owner decision record (arbitration, exemptions)
    owner_decision_parser = subparsers.add_parser("owner-decision", help="Record owner decision (arbitration, exemptions, policy changes)")
    owner_decision_parser.add_argument("--decision-id", required=True, help="Unique decision ID")
    owner_decision_parser.add_argument("--decision-type", required=True, help="Decision type (e.g., arbitration, exemption)")
    owner_decision_parser.add_argument("--decision-summary", required=True, help="Decision summary")
    owner_decision_parser.add_argument("--task-id", help="Related task ID (for arbitration)")
    owner_decision_parser.add_argument("--actor", default="owner", help="Actor (default: owner)")
    owner_decision_parser.add_argument("--context-refs", help="JSON array of context references")
    owner_decision_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    owner_decision_parser.add_argument("--json", action="store_true", help="Output JSON")

    home_parser = subparsers.add_parser("home", help="Governance home operations (Owner-explicit, local only)")
    home_subparsers = home_parser.add_subparsers(dest="home_command")
    home_git_init_parser = home_subparsers.add_parser("git-init", help="One-shot, transparent local git init of the home (no remote, no push)")
    home_git_init_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    home_git_init_parser.add_argument("--project", help="Init at <home>/<project> instead of the home root (topology B); default is workspace-level (topology A)")
    home_git_init_parser.add_argument("--actor", default=default_actor, help="Commit identity actor; defaults to $USER or owner")

    # AIPOS-340: Turn advancer (next-step command resolver)
    turn_parser = subparsers.add_parser("turn-advancer", help="AIPOS-340: Resolve next-step command for turn-based workflow")
    turn_subparsers = turn_parser.add_subparsers(dest="turn_command")
    turn_next_parser = turn_subparsers.add_parser("next", help="Resolve next command for a single task")
    turn_next_parser.add_argument("task_id", help="Task ID to resolve")
    turn_next_parser.add_argument("--workspace-root", type=Path, help="Workspace root; defaults to auto-discovery")
    turn_next_parser.add_argument("--mode", choices=["manual", "auto"], default="manual", help="Dispatch mode: manual (print) or auto (execute)")
    turn_scan_parser = turn_subparsers.add_parser("scan", help="Scan all tasks and show next-step list")
    turn_scan_parser.add_argument("--workspace-root", type=Path, help="Workspace root; defaults to auto-discovery")
    turn_scan_parser.add_argument("--mode", choices=["manual", "auto"], default="manual", help="Dispatch mode")

    # AIPOS-R7A: next-step navigation (reads transitions.schema, no memory narrative)
    next_step_parser = subparsers.add_parser("next-step", help="AIPOS-R7A: Show next-step command with full parameters from transitions.schema")
    next_step_parser.add_argument("--task-id", required=True, help="Task ID to resolve")
    next_step_parser.add_argument("--workspace-root", type=Path, help="Workspace root; defaults to auto-discovery")
    next_step_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-1: Five missing loop-step CLIs (wrap existing gate verbs/backend functions)
    # 1. task progress - wrap lybra_task_progress (tools.py:2847)
    task_progress_parser = subparsers.add_parser("task-progress", help="Report task progress event")
    task_progress_parser.add_argument("--task-id", required=True, help="Task ID")
    task_progress_parser.add_argument("--actor", required=True, help="Actor reporting progress")
    task_progress_parser.add_argument("--agent-instance", required=True, help="Agent instance name")
    task_progress_parser.add_argument("--event-type", required=True, choices=["started", "progress", "completed", "blocked"], help="Event type")
    task_progress_parser.add_argument("--summary", help="Event summary (optional)")
    task_progress_parser.add_argument("--model-self-reported", help="Model used (for capability ledger)")
    task_progress_parser.add_argument("--stage", help="Current stage (optional)")
    task_progress_parser.add_argument("--reason", help="Reason (for blocked events)")
    task_progress_parser.add_argument("--json", action="store_true", help="Output JSON")

    # 3. bench-audit - wrap lybra_bench_audit_submit_dry_run/confirm (tools.py:2398/2449)
    bench_audit_parser = subparsers.add_parser("bench-audit", help="Submit bench audit conclusion")
    bench_audit_parser.add_argument("--task-id", required=True, help="Task ID being audited")
    bench_audit_parser.add_argument("--actor", required=True, help="Actor submitting audit")
    bench_audit_parser.add_argument("--conclusion", required=True, help="Audit conclusion")
    bench_audit_parser.add_argument("--evidence-type", help="Evidence type (optional)")
    bench_audit_parser.add_argument("--task-mode", help="Task mode (optional)")
    bench_audit_parser.add_argument("--evidence-refs", help="JSON array of evidence references")
    bench_audit_parser.add_argument("--notes", help="Additional notes (optional)")
    bench_audit_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    bench_audit_parser.add_argument("--json", action="store_true", help="Output JSON")

    # 4. owner-verify - wrap lybra_owner_decision_record_dry_run/confirm (tools.py:1344/1354)
    owner_verify_parser = subparsers.add_parser("owner-verify", help="Record owner verification decision")
    owner_verify_parser.add_argument("--task-id", required=True, help="Task ID being verified")
    owner_verify_parser.add_argument("--actor", required=True, help="Actor recording decision")
    owner_verify_parser.add_argument("--decision-type", required=True, help="Decision type")
    owner_verify_parser.add_argument("--decision-summary", required=True, help="Decision summary")
    owner_verify_parser.add_argument("--context-refs", help="JSON array of context references")
    owner_verify_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    owner_verify_parser.add_argument("--json", action="store_true", help="Output JSON")

    # 5. converge / mark-concluded - wrap lybra_converge_r_cards/lybra_mark_concluded (tools.py:2599/2621)
    converge_parser = subparsers.add_parser("converge", help="Batch convergence of R cards")
    converge_parser.add_argument("--actor", default="system", help="Actor performing convergence")
    converge_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    converge_parser.add_argument("--json", action="store_true", help="Output JSON")

    mark_concluded_parser = subparsers.add_parser("mark-concluded", help="Mark task as concluded (report-style audits)")
    mark_concluded_parser.add_argument("--task-id", required=True, help="Task ID to mark concluded")
    mark_concluded_parser.add_argument("--actor", default="system", help="Actor marking concluded")
    mark_concluded_parser.add_argument("--report-path", help="Report path (optional)")
    mark_concluded_parser.add_argument("--conclusion-note", help="Conclusion note (optional)")
    mark_concluded_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    mark_concluded_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-2: finalize PASS tasks (git commit/push)
    finalize_parser = subparsers.add_parser("finalize", help="AIPOS-FND-2: Finalize PASS task (git commit/push)")
    finalize_parser.add_argument("--task-id", required=True, help="Task ID to finalize (must have verdict=PASS)")
    finalize_parser.add_argument("--actor", required=True, help="Actor performing finalization")
    finalize_parser.add_argument("--workspace-root", help="Product code repo root (git commit/push runs here); defaults to auto-discovery")
    finalize_parser.add_argument("--governance-root", help="AIPOS-FND-14: Governance workspace root that owns 5_tasks/records/audit_verdicts/ (authoritative gate verdicts). Defaults to auto-discovery via the standard Lybra workspace resolution ladder; must be set explicitly when it differs from --workspace-root.")
    finalize_parser.add_argument("--push", action="store_true", help="Push to remote after commit")
    finalize_parser.add_argument("--deploy", action="store_true", help="AIPOS-R4B-2: Run lybra-deploy after push (requires --push, enforces deployment branch)")
    finalize_parser.add_argument("--dry-run", action="store_true", help="Validate without committing")
    finalize_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-9: gate deployment drift detection
    gate_parser = subparsers.add_parser("gate", help="AIPOS-FND-9: Gate deployment operations")
    gate_subparsers = gate_parser.add_subparsers(dest="gate_command", help="Gate operations")
    
    gate_drift_parser = gate_subparsers.add_parser("drift", help="Check deployment drift (committed but not deployed)")
    gate_drift_parser.add_argument("--workspace-root", help="Workspace root; defaults to auto-discovery")
    gate_drift_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-R7A2 靶②: governance-commit (顾问收口一条命令)
    governance_commit_parser = subparsers.add_parser("governance-commit", help="AIPOS-R7A2: N6 收账提交(校验四件→commit→push)")
    governance_commit_parser.add_argument("--task-id", required=True, help="Task ID for governance closure")
    governance_commit_parser.add_argument("--actor", required=True, help="Actor performing governance commit")
    governance_commit_parser.add_argument("--governance-root", help="Governance workspace root; defaults to auto-discovery")
    governance_commit_parser.add_argument("--workspace-root", help="Product repo root (for schema resolution); defaults to ~/projects/lybra")
    governance_commit_parser.add_argument("--no-push", action="store_true", help="Commit but do not push (default: push)")
    governance_commit_parser.add_argument("--message", help="Custom commit message")
    governance_commit_parser.add_argument("--dry-run", action="store_true", help="Validate without committing")
    governance_commit_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-A1 大项A: governance add 子命令族(产生侧治理写入 CLI)
    governance_parser = subparsers.add_parser("governance", help="AIPOS-A1: 治理文件操作(产生侧写入 CLI, 声明驱动)")
    governance_subparsers = governance_parser.add_subparsers(dest="governance_command")

    # governance add
    governance_add_parser = governance_subparsers.add_parser("add", help="AIPOS-A1: 生成治理文件骨架(声明驱动, 格式从 config.schema 读取)")
    governance_add_subparsers = governance_add_parser.add_subparsers(dest="governance_add_type")

    # governance add decision
    gov_add_decision = governance_add_subparsers.add_parser("decision", help="生成 decision_log 条目骨架")
    gov_add_decision.add_argument("--title", "-t", default="", help="Decision title (used for slug)")
    gov_add_decision.add_argument("--status", default="active", help="Status field (default: active)")
    gov_add_decision.add_argument("--decided-at", help="ISO8601 timestamp (default: now)")
    gov_add_decision.add_argument("--body", help="Body content (or use --body-file)")
    gov_add_decision.add_argument("--body-file", help="Path to file with body content")
    gov_add_decision.add_argument("--governance-root", required=True, help="Governance workspace root")
    gov_add_decision.add_argument("--workspace-root", help="Product repo root (for schema resolution)")
    gov_add_decision.add_argument("--dry-run", action="store_true", help="Preview without writing")
    gov_add_decision.add_argument("--json", action="store_true", help="Output JSON")

    # governance add stage
    gov_add_stage = governance_add_subparsers.add_parser("stage", help="生成 stage_archive 快照骨架")
    gov_add_stage.add_argument("--stage-name", "-s", required=True, help="Stage name")
    gov_add_stage.add_argument("--status", default="archived", help="Status field (default: archived)")
    gov_add_stage.add_argument("--snapshot-date", help="Snapshot date (default: today)")
    gov_add_stage.add_argument("--body", help="Body content")
    gov_add_stage.add_argument("--body-file", help="Path to file with body content")
    gov_add_stage.add_argument("--governance-root", required=True, help="Governance workspace root")
    gov_add_stage.add_argument("--workspace-root", help="Product repo root (for schema resolution)")
    gov_add_stage.add_argument("--dry-run", action="store_true", help="Preview without writing")
    gov_add_stage.add_argument("--json", action="store_true", help="Output JSON")

    # governance add doc
    gov_add_doc = governance_add_subparsers.add_parser("doc", help="生成 governance doc 骨架")
    gov_add_doc.add_argument("--name", "-n", required=True, help="Document name (used for filename)")
    gov_add_doc.add_argument("--title", "-t", default="", help="Document title")
    gov_add_doc.add_argument("--status", default="active", help="Status field (default: active)")
    gov_add_doc.add_argument("--body", help="Body content")
    gov_add_doc.add_argument("--body-file", help="Path to file with body content")
    gov_add_doc.add_argument("--governance-root", required=True, help="Governance workspace root")
    gov_add_doc.add_argument("--workspace-root", help="Product repo root (for schema resolution)")
    gov_add_doc.add_argument("--dry-run", action="store_true", help="Preview without writing")
    gov_add_doc.add_argument("--json", action="store_true", help="Output JSON")

    # governance add record
    gov_add_record = governance_add_subparsers.add_parser("record", help="生成 record 骨架")
    gov_add_record.add_argument("--record-type", "-r", required=True, help="Record type (e.g. claim, return)")
    gov_add_record.add_argument("--task-id", "-i", required=True, help="Task ID")
    gov_add_record.add_argument("--body", help="Body content")
    gov_add_record.add_argument("--body-file", help="Path to file with body content")
    gov_add_record.add_argument("--governance-root", required=True, help="Governance workspace root")
    gov_add_record.add_argument("--workspace-root", help="Product repo root (for schema resolution)")
    gov_add_record.add_argument("--dry-run", action="store_true", help="Preview without writing")
    gov_add_record.add_argument("--json", action="store_true", help="Output JSON")

    # governance list-declarations
    gov_list_decl = governance_subparsers.add_parser("list-declarations", help="列出所有文件声明(格式/命名/必填字段)")
    gov_list_decl.add_argument("--workspace-root", help="Product repo root (for schema resolution)")
    gov_list_decl.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def _render_token_lifecycle_result(result: dict[str, Any]) -> None:
    """AIPOS-F21: human rendering for roles rotate / remove --instance.

    Prints fingerprints ONLY — the raw token plaintext never reaches stdout.
    """
    if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons"):
        print(f"BLOCKED ({result.get('operation')}): ")
        for item in result.get("blocking_reasons") or []:
            print(f"  - {item.get('message') if isinstance(item, dict) else item}")
        return
    print(f"OK ({result.get('operation')})")
    for entry in result.get("would_rotate") or []:
        label = entry.get("instance") or "(unbound)"
        print(f"  would rotate  {entry.get('role', ''):<16} {label:<36} {entry.get('fingerprint', '')}")
    for entry in result.get("rotated") or []:
        label = entry.get("instance") or "(unbound)"
        print(f"  rotated       {entry.get('role', ''):<16} {label:<36} {entry.get('old_fingerprint', '')} -> {entry.get('new_fingerprint', '')}")
    for entry in result.get("removed") or []:
        label = entry.get("instance") or "(unbound)"
        print(f"  removed       {entry.get('role', ''):<16} {label:<36} {entry.get('fingerprint', '')}")
    if result.get("backup_path"):
        print(f"  backup: {result['backup_path']} (0600)")
    for key in ("rotation_record", "removal_record"):
        if result.get(key):
            print(f"  record: {result[key]}")
    if result.get("gate_reload"):
        print(f"  gate reload: {result['gate_reload']}")
    for line in result.get("restart_guidance") or []:
        print(f"  {line}")
    if result.get("dry_run"):
        print("  Dry-run only: nothing was written.")
    for line in result.get("next_steps") or []:
        print(line)


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

    if args.command == "agent":
        # 候选⑤⑫合流 dispatch. `agent watch --workspace-root` (candidate ⑫, AIPOS-268)
        # routes to the filesystem pump; everything else (`fetch`, and `watch --gate-url`)
        # is the AIPOS-248 gate path in agent_connector.py — left byte-identical.
        if getattr(args, "agent_command", None) == "watch" and getattr(args, "workspace_root", None):
            from tools.aipos_cli.agent_watch_fs import run_fs_watch_cli
            return run_fs_watch_cli(args)
        # AIPOS-295: agent supervise
        if getattr(args, "agent_command", None) == "supervise":
            from tools.aipos_cli.agent_supervise import main as supervise_main
            return supervise_main(sys.argv[3:])  # Pass remaining args after 'agent supervise'
        # AIPOS-295C: agent launch-check
        if getattr(args, "agent_command", None) == "launch-check":
            from tools.aipos_cli.agent_launch_check import main as launch_check_main
            return launch_check_main(sys.argv[3:])  # Pass remaining args after 'agent launch-check'
        # AIPOS-363 S1/S2: agent materialize / pushback (cross-machine adaptation layer)
        if getattr(args, "agent_command", None) == "materialize":
            from tools.aipos_cli.agent_materialize import run_materialize
            return run_materialize(args)
        if getattr(args, "agent_command", None) == "pushback":
            from tools.aipos_cli.agent_materialize import run_pushback
            return run_pushback(args)
        # Gate mode (candidate ⑤): preserve the AIPOS-248 required-arg contract in code
        # (--actor / a token source are required for the gate pull). argparse can no longer
        # express 'required only when --gate-url is set' now that `watch` is polymorphic;
        # validate here so run_agent_command / agent_connector.py stay byte-identical.
        if getattr(args, "agent_command", None) == "watch":
            missing = []
            if not getattr(args, "actor", None):
                missing.append("--actor")
            if not getattr(args, "connection_json", None) and not getattr(args, "token_env", None):
                missing.append("(--connection-json | --token-env)")
            if missing:
                print(
                    "lybra agent watch --gate-url: missing required argument(s): "
                    + ", ".join(missing),
                    file=sys.stderr,
                )
                return 2
            # Resolve the shared --interval default for gate mode (60s) here so
            # agent_connector.run_watch — which does `float(args.interval)` — stays untouched.
            from tools.aipos_cli.agent_connector import DEFAULT_INTERVAL_SECONDS
            if getattr(args, "interval", None) is None:
                args.interval = DEFAULT_INTERVAL_SECONDS
        from tools.aipos_cli.agent_connector import run_agent_command
        return run_agent_command(args)

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
        # AIPOS-271:子命令 start(默认)/open/approve;无子命令 → start(零回归)。
        board_cmd = getattr(args, "board_command", None)
        try:
            if board_cmd == "open":
                return _run_board_open(args)
            if board_cmd == "approve":
                return _run_board_approve(args)
            return _run_board_command(args)  # None | "start"
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

    if args.command == "dispatch":
        # AIPOS-FND-12: 产出给执行体的是认领命令(经连接器 claim→材料化),不是队列文件路径
        try:
            result = _run_dispatch(args)
        except (OSError, ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(result["dispatch_command"])
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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0

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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0

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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0

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
            conn_override = getattr(args, "connection_json", None)
            connection_target = Path(conn_override).expanduser() if conn_override else None
            if args.serve_command == "stop":
                # AIPOS-238 (F-o3-13 Part 1 A): `serve stop` is a pure lifecycle op — it locates the
                # recorded service_state.json via connection_target (--connection-json) / the runtime
                # root and SIGTERMs only service_owned PIDs. It must NOT fail-close on project
                # resolution: a missing LYBRA_HOME_ROOT / unestablished project used to abort stop
                # BEFORE any PID was killed (orphaned board+mcp kept the port). workspace_root here is
                # cosmetic (stop_report reads the state by connection_target), so resolve leniently.
                try:
                    workspace_root = _resolve_workspace_for_command(args)
                except Exception:
                    workspace_root = Path(getattr(args, "workspace_root", None) or ".").expanduser()
                result = stop_report(workspace_root, connection_target=connection_target)
                print(render_json(result))
                return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0
            workspace_root = _resolve_workspace_for_command(args)
            if args.serve_command == "start":
                result = start_report(
                    workspace_root,
                    board_host=args.board_host,
                    board_port=int(args.board_port),
                    mcp_host=args.mcp_host,
                    mcp_port=int(args.mcp_port),
                    board_advertise_host=args.board_advertise,
                    mcp_advertise_host=args.mcp_advertise,
                    start_processes=True,
                    connection_target=connection_target,
                    reuse_port=bool(getattr(args, "reuse_port", False)),
                )
            elif args.serve_command == "status":
                result = status_report(workspace_root, connection_target=connection_target)
            elif args.serve_command == "rotate":
                # AIPOS-254: parse --role-instance (multi-use) into dict
                role_inst_map = {}
                if getattr(args, "role_instances", None):
                    for item in args.role_instances:
                        if "=" in item:
                            role, instance = item.split("=", 1)
                            role_inst_map[role.strip()] = instance.strip()
                # AIPOS-353: parse --roles (comma-separated) into list
                roles_list = None
                if getattr(args, "roles", None):
                    roles_list = [r.strip() for r in args.roles.split(",") if r.strip()]
                result = rotate_report(
                    workspace_root,
                    board_host=args.board_host,
                    board_port=int(args.board_port),
                    mcp_host=args.mcp_host,
                    mcp_port=int(args.mcp_port),
                    board_advertise_host=args.board_advertise,
                    mcp_advertise_host=args.mcp_advertise,
                    connection_target=connection_target,
                    project=(str(args.project).strip() if getattr(args, "project", None) else None),
                    executor_instance=(str(args.executor_instance).strip() if getattr(args, "executor_instance", None) else None),
                    role_instances=role_inst_map if role_inst_map else None,
                    confirm_binding_changes=bool(getattr(args, "confirm_binding_changes", False)),
                    actor=(str(args.actor).strip() if getattr(args, "actor", None) else None),
                    owner_authorization_ref=(str(args.owner_authorization_ref).strip() if getattr(args, "owner_authorization_ref", None) else None),
                    roles=roles_list,
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
        return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0

    # AIPOS-346 S5: roles subcommand
    if args.command == "roles":
        if not getattr(args, "roles_command", None):
            parser.print_help()
            return 2
        try:
            # FIX-1: roles enroll 不需要 5_tasks/queue 结构(只落 .lybra/ 配置)
            if args.roles_command == "enroll":
                # enroll 自己创建 workspace_root,不需要预先存在。
                # F23 大项C①: 缺省当前目录(工位目录约定 = pi 从工位根启动), 报错必带可抄示例。
                explicit_root = (
                    getattr(args, "workspace", None)
                    or getattr(args, "workspace_root", None)
                    or getattr(args, "global_workspace_root", None)
                )
                if not explicit_root:
                    explicit_root = os.getcwd()
                workspace_root = Path(explicit_root).expanduser().resolve()
                connection_target = None
            else:
                # 其他 roles 命令需要完整 workspace 结构
                conn_override = getattr(args, "connection_json", None)
                connection_target = Path(conn_override).expanduser() if conn_override else None
                workspace_root = _resolve_workspace_for_command(args)
            if args.roles_command == "list":
                result = roles_list_report(workspace_root, connection_target=connection_target)
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    # Render table
                    print(f"{'Role':<16} {'Instance':<36} {'Compliant':<10} {'Fingerprint'}")
                    for role_info in result.get("roles", []):
                        compliant_str = "✓" if role_info.get("compliant") else "✗"
                        instance = role_info.get("instance") or "(unbound)"
                        print(f"{role_info.get('role', ''):<16} {instance:<36} {compliant_str:<10} {role_info.get('fingerprint', '')}")
                        if role_info.get("validation_message"):
                            print(f"  ⚠ {role_info['validation_message']}")
            elif args.roles_command == "reconcile":
                result = roles_reconcile_report(workspace_root, connection_target=connection_target)
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    print(f"Verdict: {result.get('verdict')}")
                    if result.get("missing"):
                        print(f"Missing roles: {', '.join(result['missing'])}")
                    if result.get("extra"):
                        print(f"Extra roles: {', '.join(result['extra'])}")
                    if result.get("non_compliant"):
                        print("Non-compliant instances:")
                        for nc in result["non_compliant"]:
                            print(f"  - {nc['role']}: {nc['instance']} — {nc['message']}")
                    if result.get("unbound"):
                        print(f"Unbound roles: {', '.join(result['unbound'])}")
            elif args.roles_command == "register":
                # AIPOS-F24 大项A: 薄壳模式 - 调用门动词 lybra_roles_register
                from tools.aipos_cli.confirm_client import GateClient, GateError, load_owner_token
                owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                reason = str(getattr(args, "reason", "") or "").strip()
                
                if not owner_auth_ref:
                    print("Error: --owner-authorization-ref is required (owner-gated)", file=sys.stderr)
                    return 1
                
                # 连接信息
                conn_override = getattr(args, "connection_json", None)
                connection_target = Path(conn_override).expanduser() if conn_override else None
                conn_path = Path(connection_target or (workspace_root / ".lybra" / "connection.json")).expanduser()
                if not conn_path.exists():
                    print(f"Error: connection.json not found: {conn_path}", file=sys.stderr)
                    print("Hint: 在治理工作区运行或 --connection-json 指定", file=sys.stderr)
                    return 1
                
                conn_data = json.loads(conn_path.read_text(encoding="utf-8"))
                rpc_url = str(((conn_data.get("mcp") or {}).get("rpc_url")) or "").strip()
                if not rpc_url:
                    print(f"Error: connection.json has no mcp.rpc_url: {conn_path}", file=sys.stderr)
                    return 1
                
                base_url = rpc_url[:-len("/mcp")] if rpc_url.endswith("/mcp") else rpc_url
                token = None
                for role in ("advisor", "planner", "owner"):
                    try:
                        token = load_owner_token(connection_json=conn_path, role=role)
                        break
                    except ValueError:
                        continue
                if not token:
                    print(f"Error: no usable token in {conn_path}", file=sys.stderr)
                    return 1
                
                try:
                    client = GateClient(base_url, token, timeout=30.0)
                    dry = client.call_tool("lybra_roles_register_dry_run", {
                        "name": args.name,
                        "builtin_class": args.builtin_class,
                        "workspace_root": str(workspace_root),
                        "owner_authorization_ref": owner_auth_ref,
                        "reason": reason,
                        "actor": "cli:roles-register",
                    })
                    if not dry.get("ok"):
                        print(f"Error: gate rejected: {json.dumps(dry, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
                    
                    confirm = client.call_tool("lybra_roles_register_confirm", {
                        "dry_run_token": dry.get("dry_run_token"),
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                        "actor": "cli:roles-register",
                    })
                    if not confirm.get("ok"):
                        print(f"Error: gate rejected confirm: {json.dumps(confirm, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
                    
                    result = confirm
                    if getattr(args, "json", False):
                        print(render_json(result))
                    else:
                        print(f"Registered custom role '{args.name}' → class '{args.builtin_class}' (via gate verb)")
                        if owner_auth_ref:
                            print(f"  Owner authorization ref: {owner_auth_ref}")
                        print(f"  Active custom roles: {list(result.get('custom_roles', {}).keys())}")
                except GateError as exc:
                    print(f"Error: gate call failed: {exc}", file=sys.stderr)
                    return 1
            elif args.roles_command == "remove":
                instance = str(getattr(args, "instance", "") or "").strip() or None
                name = str(getattr(args, "name", "") or "").strip() or None
                if instance and name:
                    print("Error: pass either a custom role name or --instance, not both", file=sys.stderr)
                    return 1
                if not instance and not name:
                    print("Error: roles remove requires a custom role name or --instance <agent_instance>", file=sys.stderr)
                    return 1
                if instance:
                    # AIPOS-F21: instance-token removal from connection.json (with record + gate reload)
                    from tools.aipos_cli.token_rotation import remove_instance_report
                    owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                    if not owner_auth_ref:
                        print("Error: --owner-authorization-ref is required for instance token removal", file=sys.stderr)
                        return 1
                    result = remove_instance_report(
                        workspace_root,
                        instance=instance,
                        owner_authorization_ref=owner_auth_ref,
                        actor=owner_auth_ref,
                        reason=str(getattr(args, "reason", "") or "").strip(),
                        connection_target=connection_target,
                    )
                    if getattr(args, "json", False):
                        print(render_json(result))
                    else:
                        _render_token_lifecycle_result(result)
                    return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0
                # AIPOS-352F1: remove a custom role
                from tools.aipos_cli.custom_roles import remove_custom_role
                owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                reason = str(getattr(args, "reason", "") or "").strip()
                by = owner_auth_ref or "owner"
                updated = remove_custom_role(
                    workspace_root,
                    args.name,
                    by=by,
                    reason=reason or (f"owner-authorization-ref: {owner_auth_ref}" if owner_auth_ref else ""),
                )
                result = {
                    "ok": True,
                    "operation": "roles_remove",
                    "name": args.name,
                    "owner_authorization_ref": owner_auth_ref,
                    "custom_roles": updated,
                }
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    print(f"Removed custom role '{args.name}' (idempotent)")
                    if owner_auth_ref:
                        print(f"  Owner authorization ref: {owner_auth_ref}")
                    print(f"  Active custom roles: {list(updated.keys())}")
            elif args.roles_command == "rotate":
                # AIPOS-F21: two-phase service-token rotation
                from tools.aipos_cli.token_rotation import rotate_tokens_report
                roles_arg = str(getattr(args, "role", "") or "").strip()
                roles_list = [r.strip() for r in roles_arg.split(",") if r.strip()] or None
                result = rotate_tokens_report(
                    workspace_root,
                    dry_run=bool(getattr(args, "dry_run", False)),
                    roles=roles_list,
                    owner_authorization_ref=str(getattr(args, "owner_authorization_ref", "") or "").strip() or None,
                    actor=str(getattr(args, "actor", "") or "").strip() or None,
                    reason=str(getattr(args, "reason", "") or "").strip(),
                    connection_target=connection_target,
                    reload_gate=not bool(getattr(args, "no_reload", False)),
                )
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    _render_token_lifecycle_result(result)
                return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0
            elif args.roles_command == "enroll-code":
                # AIPOS-F24A 大項A: CLI 薄壳化 —— 发码唯一实现=门动词 in-server(运输凭证注册与
                # 门内存注册表同进程, 死凭证类缺陷根除)。本 CLI 只调 lybra_enroll_code_dry_run/
                # confirm 两阶段动词, 本地发码路径已删除(grep 证明单实现); gate 不可达=如实报错,
                # 绝不回退到本地发码(本地发码=死运输凭证)。
                from tools.aipos_cli.confirm_client import GateClient, GateError, load_owner_token

                def _enroll_code_fail(message: str, *, next_step: str = "") -> int:
                    print(f"Error: {message}", file=sys.stderr)
                    if next_step:
                        print(f"下一步: {next_step}", file=sys.stderr)
                    return 1

                owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                reason = str(getattr(args, "reason", "") or "").strip()
                ttl = getattr(args, "ttl", None)
                instance = getattr(args, "instance", None)
                gate_url_arg = str(getattr(args, "gate_url", "") or "").strip() or None
                governance_root_arg = str(getattr(args, "governance_root", "") or "").strip() or None
                token_role = str(getattr(args, "token_role", "advisor") or "advisor").strip() or "advisor"
                if not args.role:
                    raise ValueError(
                        "roles enroll-code requires --role.\n"
                        "可抄示例: lybra roles enroll-code --role executor --instance exec.lybra.mac1 "
                        "--ttl 86400 --owner-authorization-ref <owner-authorization-ref>"
                    )
                if not owner_auth_ref:
                    raise ValueError(
                        "roles enroll-code requires --owner-authorization-ref (发码是 owner-gated).\n"
                        "可抄示例: lybra roles enroll-code --role executor --owner-authorization-ref <owner-authorization-ref>"
                    )
                # ① 连接源: <workspace>/.lybra/connection.json(或 --connection-json 覆盖)
                conn_path = Path(connection_target or (workspace_root / ".lybra" / "connection.json")).expanduser()
                if not conn_path.exists():
                    return _enroll_code_fail(
                        f"local connection.json not found: {conn_path}",
                        next_step=("在治理工作区运行本命令(或 --connection-json 指定本机 connection.json); "
                                   "薄壳只调门动词, 没有(也不许有)本地发码路径。"),
                    )
                conn_data = json.loads(conn_path.read_text(encoding="utf-8"))
                rpc_url = str(((conn_data.get("mcp") or {}).get("rpc_url")) or "").strip()
                if not rpc_url:
                    return _enroll_code_fail(
                        f"connection.json has no mcp.rpc_url: {conn_path}",
                        next_step="先 lybra serve start 或修正 connection.json。",
                    )
                base_url = rpc_url[:-len("/mcp")] if rpc_url.endswith("/mcp") else rpc_url
                # ② 治理根(F24A): 显式参数优先, 缺省=本机 connection.json 的 workspace_root ——
                #    显式化传入, 码内治理根不再依赖门进程环境解析(被吞缺陷根除)
                governance_root = governance_root_arg or str(conn_data.get("workspace_root") or "").strip() or None
                # ③ token 按角色读(默认 advisor, 缺席回落 owner); 原始 token 只进程内使用, 永不上 argv/不回显
                token = None
                token_role_used = None
                for candidate_role in (token_role, "owner"):
                    try:
                        token = load_owner_token(connection_json=conn_path, role=candidate_role)
                        token_role_used = candidate_role
                        break
                    except ValueError:
                        continue
                if not token:
                    return _enroll_code_fail(
                        f"no usable role token ({token_role}/owner) in {conn_path}",
                        next_step="用 --token-role 指定角色, 或先在本机 enroll 出顾问/Owner 凭据。",
                    )
                dry_run_args = {
                    "role": args.role,
                    "instance": instance,
                    "ttl": ttl,
                    "gate_url": gate_url_arg,
                    "governance_root": governance_root,
                    "owner_authorization_ref": owner_auth_ref,
                    "reason": reason,
                    "actor": f"cli:roles-enroll-code:{token_role_used}",
                }
                dry_run_args = {k: v for k, v in dry_run_args.items() if v not in (None, "")}
                try:
                    client = GateClient(base_url, token, timeout=30.0)
                    dry = client.call_tool("lybra_enroll_code_dry_run", dry_run_args)
                    if not dry.get("ok"):
                        return _enroll_code_fail(
                            "gate rejected lybra_enroll_code_dry_run: " + json.dumps(dry, ensure_ascii=False)[:800],
                            next_step="按上面 teaching error 修正参数后重试。",
                        )
                    confirm = client.call_tool("lybra_enroll_code_confirm", {
                        "dry_run_token": dry.get("dry_run_token"),
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                        "actor": dry_run_args["actor"],
                    })
                    if not confirm.get("ok"):
                        return _enroll_code_fail(
                            "gate rejected lybra_enroll_code_confirm: " + json.dumps(confirm, ensure_ascii=False)[:800],
                            next_step="按上面 teaching error 处理(dry_run_token TTL=600s, 过期重跑本命令)。",
                        )
                except GateError as exc:
                    return _enroll_code_fail(
                        f"gate call failed: {exc}",
                        next_step=("检查门: lybra serve status / 先 lybra serve start。"
                                   "此为产品侧/连接故障, 与你无关, 禁自行诊断修复门/服务/部署 —— 报告顾问即可。"
                                   "薄壳没有本地发码回退路径(本地发码=死运输凭证, 已废除)。"),
                    )
                # FIX-2 兼容: --json 输出稳定含 self_contained_code/code_id/fingerprint 在顶层
                result_out = {
                    "ok": True,
                    "operation": "roles_enroll_code",
                    "issued_via": "gate_verb_thin_shell(F24A)",
                    "code_id": confirm.get("code_id"),
                    "self_contained_code": confirm.get("self_contained_code"),
                    "paste_text": confirm.get("paste_text"),
                    "fingerprint": confirm.get("fingerprint"),
                    "role": confirm.get("role"),
                    "instance": confirm.get("instance"),
                    "expires_at": confirm.get("expires_at"),
                    "gate_url": confirm.get("gate_url"),
                    "governance_root": confirm.get("governance_root"),
                    "transport_token_fingerprint": confirm.get("transport_token_fingerprint"),
                }
                if getattr(args, "json", False):
                    print(render_json(result_out))
                else:
                    print(f"Generated SELF-CONTAINED enrollment code for role '{args.role}' (via gate verb, in-server)")
                    if instance:
                        print(f"  Instance: {instance}")
                    print(f"  Code ID: {confirm.get('code_id')}")
                    print(f"  Fingerprint: {confirm.get('fingerprint')}")
                    print(f"  Gate URL: {confirm.get('gate_url')}")
                    print(f"  Governance root: {confirm.get('governance_root')}")
                    if ttl:
                        print(f"  Expires at: {confirm.get('expires_at')}")
                    print(f"\n  把下面这条整体转贴到新工位的 pi 会话(Owner 唯一要做的事):")
                    print(f"  {confirm.get('paste_text')}")
                    print(f"\n  ⚠ 码单次 + TTL + 可撤销; 内嵌零 scope 运输凭证(码即运输认证, 无需 bootstrap token)。")
                    print(f"  ⚠ This code is shown only once. Share it immediately.")
                return 0
            elif args.roles_command == "enroll-revoke":
                # AIPOS-362: revoke enrollment code
                from tools.aipos_cli.enrollment import revoke_enrollment_code
                owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                reason = str(getattr(args, "reason", "") or "").strip()
                by = owner_auth_ref or "owner"
                revoked = revoke_enrollment_code(
                    workspace_root,
                    args.code_id,
                    by=by,
                    reason=reason or (f"owner-authorization-ref: {owner_auth_ref}" if owner_auth_ref else ""),
                )
                result = {
                    "ok": True,
                    "operation": "roles_enroll_revoke",
                    "revoked": revoked,
                }
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    print(f"Revoked enrollment code: {args.code_id}")
                    if owner_auth_ref:
                        print(f"  Owner authorization ref: {owner_auth_ref}")
            elif args.roles_command == "enroll-list":
                # AIPOS-362: list enrollment codes
                from tools.aipos_cli.enrollment import list_enrollment_codes
                codes = list_enrollment_codes(workspace_root, include_code=False)
                result = {
                    "ok": True,
                    "operation": "roles_enroll_list",
                    "enrollments": codes,
                }
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    print(f"{'Code ID':<24} {'Role':<16} {'Instance':<32} {'Status':<10} {'Expires At'}")
                    for code in codes:
                        inst = code.get('instance') or '(any)'
                        expires = code.get('expires_at') or '(never)'
                        print(f"{code['code_id']:<24} {code['role']:<16} {inst:<32} {code['status']:<10} {expires}")
            elif args.roles_command == "enroll":
                # AIPOS-R2/F23: client-side enrollment (exchange code + write .lybra/ config)
                from tools.aipos_cli.enroll_client import enroll
                gate_url_arg = str(getattr(args, "gate_url", "") or "").strip()
                if not gate_url_arg:
                    gate_url_arg = os.environ.get("LYBRA_GATE_URL", "").strip()
                # F23: 自包含码内嵌 gate 地址 —— gate_url 可省; 旧裸码必须有(F9 带可抄示例)
                from tools.aipos_cli.enrollment import decode_self_contained_code
                is_self_contained = decode_self_contained_code(args.code or "") is not None
                if not gate_url_arg and not is_self_contained:
                    raise ValueError(
                        "roles enroll requires --gate-url for legacy plain codes "
                        "(自包含码 LYBRAENROLL1.* 内嵌 gate 地址, 无需此参数)。\n"
                        "可抄示例: lybra roles enroll --code LYBRAENROLL1.<base64> --workspace ~/workstations/my-agent\n"
                        "旧码示例:   lybra roles enroll --code <裸码> --gate-url http://<host>:7118 --workspace ~/workstations/my-agent"
                    )
                try:
                    result = enroll(
                        code=args.code,
                        gate_url=gate_url_arg,
                        workspace_root=workspace_root,
                        policy=getattr(args, "policy", None),
                        bootstrap_token=getattr(args, "bootstrap_token", None),
                        verify=bool(getattr(args, "verify", False)),
                    )
                    if getattr(args, "json", False):
                        print(render_json(result))
                    else:
                        print(f"\n✓ Enrollment successful!")
                        print(f"  Role: {result['role']}")
                        if result.get('agent_instance'):
                            print(f"  Instance: {result['agent_instance']}")
                        print(f"  Token fingerprint: {result['fingerprint']}")
                        print(f"  Scopes: {', '.join(result['scopes'])}")
                        if result['rotated']:
                            print(f"  ⟳ Token rotated (replaced existing credential)")
                        else:
                            print(f"  ✓ New credential registered")
                        # F23 大项C③: 输出的落盘路径与实际一致且为工位目录
                        print(f"\n  Configuration written to: {result['lybra_dir']}/ (工位目录)")
                        for fname in result['files_written']:
                            print(f"    - {fname}")
                        if result.get('landed') is True:
                            print(f"  ✓ 落盘已确认(码已消费, grace 窗口关闭)")
                        elif result.get('landed') is False:
                            print(f"  ⚠ 落盘成功但 land 确认失败(码将由 grace 窗口过期自然消费, 不影响使用)")
                        print(f"\n⚠ Enrollment code has been consumed and cannot be reused.")
                        print(f"⚠ Token is stored with 0600 permissions in connection.json")
                        if result.get('next_step'):
                            print(f"\n下一步: {result['next_step']}")
                except RuntimeError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 2
            else:
                parser.print_help()
                return 2
        except (OSError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        return 1 if isinstance(result, dict) and (result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons")) else 0

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
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "project":
        if not getattr(args, "project_command", None):
            parser.print_help()
            return 2
        try:
            # AIPOS-293: export/import don't need home_root; dispatch-mode uses --project-root
            if args.project_command in ("export", "import", "dispatch-mode"):
                pass  # handled below
            else:
                home = resolve_home_root(explicit_root=args.home_root)
            if args.project_command == "new":
                # AIPOS-F24 大项A: 薄壳模式 - 调用门动词 lybra_project_new
                # 保留交互式询问(CLI 侧体验),但实际创建走门动词
                from tools.aipos_cli.confirm_client import GateClient, GateError, load_owner_token
                
                collaboration_profile = _ask_project_type_interactive()
                
                # 需要 owner_authorization_ref
                owner_auth_ref = getattr(args, "owner_authorization_ref", None)
                if not owner_auth_ref:
                    print("Error: --owner-authorization-ref is required (owner-gated)", file=sys.stderr)
                    print("Hint: project creation requires owner authorization.", file=sys.stderr)
                    return 1
                
                # 连接信息 (从 home 推导连接配置)
                # 对于 project new,我们需要有一个已存在的门服务
                # 暂时使用环境变量或默认连接
                conn_path = Path("~/.lybra/connection.json").expanduser()
                if not conn_path.exists():
                    # 降级:直接调用本地函数(向后兼容)
                    root = scaffold_project(
                        home, args.name, code_repo=args.code_repo, registered_by=args.actor,
                        collaboration_profile=collaboration_profile
                    )
                    print(f"Created project root: {root} (local fallback)")
                    print(f"project.json: {project_json_path(root)}")
                    if collaboration_profile:
                        print(f"collaboration_profile: {collaboration_profile}")
                    print(f"next: lybra serve with LYBRA_HOME_ROOT={home}")
                    return 0
                
                conn_data = json.loads(conn_path.read_text(encoding="utf-8"))
                rpc_url = str(((conn_data.get("mcp") or {}).get("rpc_url")) or "").strip()
                if not rpc_url:
                    # 降级:本地调用
                    root = scaffold_project(
                        home, args.name, code_repo=args.code_repo, registered_by=args.actor,
                        collaboration_profile=collaboration_profile
                    )
                    print(f"Created project root: {root} (local fallback)")
                    return 0
                
                base_url = rpc_url[:-len("/mcp")] if rpc_url.endswith("/mcp") else rpc_url
                token = None
                for role in ("advisor", "planner", "owner"):
                    try:
                        token = load_owner_token(connection_json=conn_path, role=role)
                        break
                    except ValueError:
                        continue
                if not token:
                    print(f"Error: no usable token in {conn_path}", file=sys.stderr)
                    return 1
                
                try:
                    client = GateClient(base_url, token, timeout=30.0)
                    dry = client.call_tool("lybra_project_new_dry_run", {
                        "name": args.name,
                        "code_repo": args.code_repo,
                        "home_root": str(home),
                        "collaboration_profile": collaboration_profile,
                        "owner_authorization_ref": owner_auth_ref,
                        "actor": args.actor,
                    })
                    if not dry.get("ok"):
                        print(f"Error: gate rejected: {json.dumps(dry, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
                    
                    confirm = client.call_tool("lybra_project_new_confirm", {
                        "dry_run_token": dry.get("dry_run_token"),
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                        "actor": args.actor,
                    })
                    if not confirm.get("ok"):
                        print(f"Error: gate rejected confirm: {json.dumps(confirm, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
                    
                    root = Path(confirm.get("project_root"))
                    print(f"Created project root: {root} (via gate verb)")
                    print(f"project.json: {project_json_path(root)}")
                    if collaboration_profile:
                        print(f"collaboration_profile: {collaboration_profile}")
                    if confirm.get("connection_json_written"):
                        print(f".lybra/connection.json: written with mcp.rpc_url")
                    print(f"next: {'; '.join(confirm.get('next_steps', []))}")
                except GateError as exc:
                    print(f"Error: gate call failed: {exc}", file=sys.stderr)
                    return 1
                return 0
            if args.project_command == "set-repo":
                # AIPOS-F24 大项A: 薄壳模式
                from tools.aipos_cli.confirm_client import GateClient, GateError, load_owner_token
                
                owner_auth_ref = getattr(args, "owner_authorization_ref", None)
                if not owner_auth_ref:
                    print("Error: --owner-authorization-ref is required (owner-gated)", file=sys.stderr)
                    return 1
                
                conn_path = Path("~/.lybra/connection.json").expanduser()
                if not conn_path.exists():
                    # 降级
                    root = set_project_repo(
                        home, args.name, args.code_repo, registered_by=args.actor
                    )
                    print(f"Updated {project_json_path(root)}: code_repo={Path(args.code_repo).expanduser()} (local fallback)")
                    return 0
                
                conn_data = json.loads(conn_path.read_text(encoding="utf-8"))
                rpc_url = str(((conn_data.get("mcp") or {}).get("rpc_url")) or "").strip()
                if not rpc_url:
                    # 降级
                    root = set_project_repo(
                        home, args.name, args.code_repo, registered_by=args.actor
                    )
                    print(f"Updated {project_json_path(root)}: code_repo={Path(args.code_repo).expanduser()} (local fallback)")
                    return 0
                
                base_url = rpc_url[:-len("/mcp")] if rpc_url.endswith("/mcp") else rpc_url
                token = None
                for role in ("advisor", "planner", "owner"):
                    try:
                        token = load_owner_token(connection_json=conn_path, role=role)
                        break
                    except ValueError:
                        continue
                if not token:
                    print(f"Error: no usable token", file=sys.stderr)
                    return 1
                
                try:
                    client = GateClient(base_url, token, timeout=30.0)
                    dry = client.call_tool("lybra_project_set_repo_dry_run", {
                        "name": args.name,
                        "code_repo": args.code_repo,
                        "home_root": str(home),
                        "owner_authorization_ref": owner_auth_ref,
                        "actor": args.actor,
                    })
                    if not dry.get("ok"):
                        print(f"Error: {json.dumps(dry, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
                    
                    confirm = client.call_tool("lybra_project_set_repo_confirm", {
                        "dry_run_token": dry.get("dry_run_token"),
                        "owner_confirmation_token": "OWNER_CONFIRMED",
                        "actor": args.actor,
                    })
                    if not confirm.get("ok"):
                        print(f"Error: {json.dumps(confirm, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
                    
                    root = Path(confirm.get("project_root"))
                    print(f"Updated {project_json_path(root)}: code_repo={Path(args.code_repo).expanduser()} (via gate verb)")
                except GateError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                return 0
            if args.project_command == "list":
                # AIPOS-335 S4: 存量项目盘点
                candidates = _project_candidates(home)
                result = {
                    "ok": True,
                    "home_root": str(home),
                    "project_count": len(candidates),
                    "projects": [],
                }
                for name in candidates:
                    project_root = home / name
                    project_json = read_project_json(project_root)
                    profile = get_collaboration_profile(project_root)
                    has_explicit_profile = "collaboration_profile" in project_json
                    try:
                        from tools.aipos_cli.workspace_config import get_dispatch_mode
                        dispatch_mode = get_dispatch_mode(project_root)
                    except Exception:
                        dispatch_mode = "auto"
                    result["projects"].append({
                        "name": name,
                        "project_root": str(project_root),
                        "code_repo": project_json.get("code_repo"),
                        "collaboration_profile": profile,
                        "has_explicit_profile": has_explicit_profile,
                        "inferred": not has_explicit_profile,
                        "dispatch_mode": dispatch_mode,
                    })
                if args.json:
                    print(render_json(result))
                else:
                    print(f"\nFound {len(candidates)} project(s) under {home}:\n")
                    for proj in result["projects"]:
                        marker = "✅" if proj["has_explicit_profile"] else "🔵"
                        print(f"{marker} {proj['name']}")
                        print(f"   Path: {proj['project_root']}")
                        print(f"   Code repo: {proj['code_repo'] or 'None'}")
                        if proj["inferred"]:
                            print(f"   Profile: (inferred, not yet written to project.json)")
                        else:
                            print(f"   Profile: (explicit in project.json)")
                        cp = proj["collaboration_profile"]
                        print(f"     - code_enabled: {cp['code_enabled']}")
                        print(f"     - deploy_gate_enabled: {cp['deploy_gate_enabled']}")
                        print(f"     - default_audit_mode: {cp['default_audit_mode']}")
                        print(f"     - output_locations: {', '.join(cp['output_locations'])}")
                        print(f"     - dispatch_mode: {proj['dispatch_mode']}")
                        print()
                    print("💡 提示：蓝色圆点 🔵 表示该项目尚未写入 collaboration_profile，使用默认推断值")
                    print("💡 Owner 可决定是否写入（本命令只列不改）")
                return 0
            if args.project_command == "dispatch-mode":
                from tools.aipos_cli.workspace_config import (
                    get_dispatch_mode, set_dispatch_mode, dispatch_mode_trail_path,
                )
                proj_root = args.project_root
                if not proj_root:
                    try:
                        proj_root = str(resolve_workspace_root())
                    except (FileNotFoundError, OSError) as exc:
                        print(f"Error: {exc}", file=sys.stderr)
                        print("Hint: provide --project-root or run from within a project.", file=sys.stderr)
                        return 1
                sub = getattr(args, "dispatch_mode_command", None)
                if sub == "show":
                    mode = get_dispatch_mode(proj_root)
                    if args.json:
                        print(render_json({"ok": True, "project_root": proj_root, "dispatch_mode": mode}))
                    else:
                        print(f"dispatch_mode: {mode}  (project: {proj_root})")
                    return 0
                if sub == "set":
                    new_mode, trail = set_dispatch_mode(
                        proj_root, args.mode, by=args.by, reason=args.reason,
                    )
                    if args.json:
                        print(render_json({
                            "ok": True, "project_root": proj_root,
                            "dispatch_mode": new_mode, "trail_path": str(trail),
                        }))
                    else:
                        print(f"✓ dispatch_mode set to {new_mode}  (trail: {trail})")
                    return 0
                print("Usage: lybra project dispatch-mode {show|set} ...", file=sys.stderr)
                return 2
            if args.project_command == "export":
                ws_root = args.workspace_root
                if not ws_root:
                    try:
                        ws_root = str(resolve_workspace_root())
                    except (FileNotFoundError, OSError) as exc:
                        print(f"Error: {exc}", file=sys.stderr)
                        print("Hint: provide workspace_root or run from within a workspace.", file=sys.stderr)
                        return 1
                result = export_project_to_yaml(
                    ws_root,
                    project_name=args.project_name,
                    output_path=args.output,
                )
                if args.json:
                    print(render_json(result))
                else:
                    if result.get("ok"):
                        print(f"Exported project structure to: {result['output_path']}")
                        print(f"  Project: {result['structure']['project_name']}")
                        print(f"  Documents: {result['doc_count']}")
                        print(f"  Governance files: {', '.join(result['governance_files'])}")
                    else:
                        print(f"Export failed: {result.get('blocking_reasons')}", file=sys.stderr)
                return 0 if result.get("ok") else 1
            if args.project_command == "import":
                result = import_project_structure(
                    args.structure_file,
                    args.output_root,
                    dry_run=args.dry_run,
                    actor=args.actor,
                )
                if args.json:
                    print(render_json(result))
                else:
                    if result.get("ok"):
                        if args.dry_run:
                            print(f"Dry-run: would create project '{result['project_name']}' at {result['output_root']}")
                            print(f"  Directories: {len(result['planned_dirs'])}")
                            print(f"  Files: {len(result['planned_files'])}")
                            print(f"  Migration items: {result['migration_item_count']}")
                        else:
                            print(f"Imported project '{result['project_name']}' to {result['output_root']}")
                            print(f"  Directories created: {len(result['planned_dirs'])}")
                            print(f"  Files written: {len(result['planned_files'])}")
                            print(f"  Skipped (existing): {len(result['skipped_existing'])}")
                            print(f"  Migration checklist: {result['migration_checklist']}")
                    else:
                        print(f"Import failed: {result.get('blocking_reasons')}", file=sys.stderr)
                return 0 if result.get("ok") else 1
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

    if args.command == "gate":
        # AIPOS-FND-9: Gate deployment operations
        if not hasattr(args, 'gate_command') or args.gate_command is None:
            parser.parse_args([args.command, '--help'])
            return 2
        
        if args.gate_command == "drift":
            from tools.aipos_cli.gate_drift import check_gate_drift
            
            # Resolve workspace root
            if args.workspace_root:
                repo_root = Path(args.workspace_root).expanduser().resolve()
            elif args.global_workspace_root:
                repo_root = Path(args.global_workspace_root).expanduser().resolve()
            else:
                repo_root = Path.cwd()
            
            result = check_gate_drift(repo_root)
            
            if args.json:
                print(render_json(result))
            else:
                # Text output
                print("=== Gate Deployment Drift Check ===")
                print()
                print(result['message'])
                print()
                
                if result['has_drift']:
                    print(f"Undeployed commits: {result['commits_ahead']}")
                    if result['undeployed_commits']:
                        print()
                        print("Recent commits:")
                        for commit in result['undeployed_commits']:
                            print(f"  {commit['hash']} {commit['message']}")
                    
                    classification = result['classification']
                    if classification['gate_side']:
                        print()
                        print(f"Gate-side changes ({len(classification['gate_side'])} files):")
                        for path in classification['gate_side'][:10]:
                            print(f"  - {path}")
                        if len(classification['gate_side']) > 10:
                            print(f"  ... and {len(classification['gate_side']) - 10} more")
                    
                    if classification['cli_side']:
                        print()
                        print(f"CLI-side changes ({len(classification['cli_side'])} files):")
                        for path in classification['cli_side'][:5]:
                            print(f"  - {path}")
                        if len(classification['cli_side']) > 5:
                            print(f"  ... and {len(classification['cli_side']) - 5} more")
                    
                    print()
                    print(f"Recommendation: {result['recommendation']}")
                else:
                    print("✓ No drift detected")
            
            return 0 if not result['has_drift'] else 1
        
        return 2

    if args.command == "governance-commit":
        # AIPOS-R7A2 靶②: N6 收账提交(校验四件→commit→push)
        # AIPOS-R7A2 FIX-1: 传入 repo_root 用于 schema 解析
        from tools.aipos_cli.governance_commit import governance_commit
        from tools.aipos_cli.workspace_config import resolve_workspace_root
        
        # Resolve governance root
        if args.governance_root:
            governance_root = Path(args.governance_root).expanduser().resolve()
        elif args.global_workspace_root:
            governance_root = Path(args.global_workspace_root).expanduser().resolve()
        else:
            try:
                governance_root = resolve_workspace_root()
            except Exception as e:
                print(f"Error: Cannot auto-discover governance root: {e}", file=sys.stderr)
                return 1
        
        # Resolve repo_root (产品仓根,用于定位 schema/config.schema.json)
        # governance-commit 通常由顾问在治理仓调用,但 schema 在产品仓
        # 使用 workspace_root 参数或从环境自动发现
        if args.workspace_root:
            repo_root = Path(args.workspace_root).expanduser().resolve()
        else:
            # 尝试从治理仓配置或环境变量发现产品仓位置
            # 默认假设产品仓在 ~/projects/lybra (kiwiai-dev 标准位置)
            default_repo_root = Path.home() / "projects" / "lybra"
            if default_repo_root.is_dir():
                repo_root = default_repo_root
            else:
                print(f"Error: Cannot locate product repo for schema resolution. Use --workspace-root to specify.", file=sys.stderr)
                return 1
        
        try:
            result = governance_commit(
                governance_root=governance_root,
                task_id=args.task_id,
                actor=args.actor,
                repo_root=repo_root,
                dry_run=args.dry_run,
                push=not args.no_push,
                message=args.message,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(result))
        else:
            # Text output
            verdict = result['verdict']
            print(f"\n=== Governance Commit Result ===")
            print(f"Task: {result['task_id']}")
            print(f"Actor: {result['actor']}")
            print(f"Verdict: {verdict}")
            print(f"\nMessage: {result['message']}")
            
            if result.get('operations'):
                print("\nOperations:")
                for op in result['operations']:
                    print(f"  - {op}")
            
            # 显示完整性检查详情
            if result.get('completeness_check'):
                check = result['completeness_check']
                print("\n=== N6 收账清单 ===")
                
                details = check.get('details', {})
                
                # task_cards
                if details.get('task_cards', {}).get('exists'):
                    files = details['task_cards'].get('files', [])
                    print(f"✓ task_cards/{result['task_id']}/ ({len(files)} files)")
                else:
                    print(f"✗ task_cards/{result['task_id']}/ (missing)")
                
                # decision_log
                decision = details.get('decision_log', {})
                if decision.get('applicable'):
                    print(f"✓ decision_log pointer ({len(decision.get('files', []))} files)")
                
                # stage_snapshots
                snapshots = details.get('stage_snapshots', {}).get('snapshots', [])
                if snapshots:
                    print(f"✓ stage snapshots ({len(snapshots)} snapshots)")
                
                # archive_files
                archive = details.get('archive_files', {})
                if archive.get('exists'):
                    print(f"✓ archive files ({', '.join(archive.get('files', []))})")
                else:
                    print("✗ archive files (missing RETURN/AUDIT-REPORT/CLOSURE)")
                
                if not check['complete']:
                    print("\n缺少:")
                    for item in check['missing']:
                        print(f"  - {item}")
            
            if result.get('commit_hash'):
                print(f"\n✓ Commit: {result['commit_hash']}")
            
            if result.get('pushed'):
                print("✓ Pushed to remote")
            
            print("\n=== Next Steps ===")
            if verdict == Verdict.PASS:
                if result.get('committed') and result.get('pushed'):
                    print("N6 收账完成,治理记录已同步")
                elif result.get('committed'):
                    print("已 commit,但未 push (use --no-push to skip push)")
                else:
                    print("无待收内容, 治理仓已最新 (no-op, EXIT=0)")
            elif verdict == Verdict.BLOCK:
                print("请补充缺失的收账文件后重试")
            else:
                print("操作失败,请查看错误信息")
        
        return 0 if result['verdict'] == Verdict.PASS else 1

    if args.command == "governance":
        # AIPOS-A1 大项A: governance add 子命令族(产生侧治理写入 CLI)
        from tools.aipos_cli.governance_add import (
            add_decision, add_stage, add_doc, add_record, list_declarations,
        )

        gov_cmd = getattr(args, "governance_command", None)

        if gov_cmd == "list-declarations":
            repo_root = None
            if getattr(args, "workspace_root", None):
                repo_root = Path(args.workspace_root).expanduser().resolve()
            else:
                default_repo = Path.home() / "projects" / "lybra"
                if default_repo.is_dir():
                    repo_root = default_repo
            result = list_declarations(repo_root)
            if getattr(args, "json", False):
                print(render_json(result))
            else:
                print("\n=== File Declarations (config.schema governance_structure.file_declarations) ===")
                for key, decl in result.get("declarations", {}).items():
                    print(f"\n  [{key}]")
                    print(f"    Path key: {decl.get('path_key', '')}")
                    print(f"    Naming: {decl.get('naming_pattern', '')}")
                    print(f"    Required frontmatter: {decl.get('required_frontmatter', [])}")
                    print(f"    Append-only: {decl.get('append_only', False)}")
                    print(f"    Description: {decl.get('description', '')}")
            return 0

        if gov_cmd != "add":
            print("Usage: lybra governance add <decision|stage|doc|record> [options]")
            print("       lybra governance list-declarations")
            return 2

        add_type = getattr(args, "governance_add_type", None)
        if not add_type:
            print("Usage: lybra governance add <decision|stage|doc|record> [options]")
            return 2

        governance_root = Path(args.governance_root).expanduser().resolve()
        repo_root = None
        if getattr(args, "workspace_root", None):
            repo_root = Path(args.workspace_root).expanduser().resolve()
        else:
            default_repo = Path.home() / "projects" / "lybra"
            if default_repo.is_dir():
                repo_root = default_repo

        # Resolve body content
        body_content = getattr(args, "body", None) or ""
        body_file = getattr(args, "body_file", None)
        if body_file:
            body_path = Path(body_file).expanduser().resolve()
            if body_path.is_file():
                body_content = body_path.read_text(encoding="utf-8")
            else:
                print(f"Error: body file not found: {body_path}", file=sys.stderr)
                return 1

        dry_run = getattr(args, "dry_run", False)
        use_json = getattr(args, "json", False)

        if add_type == "decision":
            result = add_decision(
                governance_root,
                title=getattr(args, "title", "") or "",
                status=getattr(args, "status", "active"),
                decided_at=getattr(args, "decided_at", None),
                body=body_content,
                repo_root=repo_root,
                dry_run=dry_run,
            )
        elif add_type == "stage":
            result = add_stage(
                governance_root,
                stage_name=getattr(args, "stage_name", "") or "",
                status=getattr(args, "status", "archived"),
                snapshot_date=getattr(args, "snapshot_date", None),
                body=body_content,
                repo_root=repo_root,
                dry_run=dry_run,
            )
        elif add_type == "doc":
            result = add_doc(
                governance_root,
                name=getattr(args, "name", "") or "",
                title=getattr(args, "title", "") or "",
                status=getattr(args, "status", "active"),
                body=body_content,
                repo_root=repo_root,
                dry_run=dry_run,
            )
        elif add_type == "record":
            result = add_record(
                governance_root,
                record_type=getattr(args, "record_type", "") or "",
                task_id=getattr(args, "task_id", "") or "",
                body=body_content,
                repo_root=repo_root,
                dry_run=dry_run,
            )
        else:
            print(f"Unknown governance add type: {add_type}", file=sys.stderr)
            return 1

        if use_json:
            print(render_json(result))
        else:
            if result.get("ok"):
                print(f"\u2713 {result.get('message', 'OK')}")
                if result.get("dry_run"):
                    print(f"  [DRY-RUN] Target: {result.get('target_path', '')}")
                    print(f"  Required frontmatter: {result.get('required_frontmatter', [])}")
                else:
                    print(f"  Path: {result.get('target_path', '')}")
            else:
                print(f"\u2717 {result.get('error', 'Unknown error')}")
                print(f"  {result.get('message', '')}")

        return 0 if result.get("ok") else 1

    if args.command == "finalize":
        # AIPOS-FND-2: Finalize PASS task (git commit/push)
        # AIPOS-CONN-LOOP-1 §6: finalize走Context — code_repo从LoopContext/自发现,
        # --workspace-root仅作override,废除cwd猜测(08-12实撞:误传治理仓致越权提交)
        from tools.aipos_cli.finalize import finalize_task
        from tools.aipos_cli.workspace_config import resolve_workspace_root
        from tools.aipos_cli.cli_self_describe import wrap_error_with_verb_help
        
        # finalize doesn't require full 5_tasks/queue structure, only task_cards/ and git
        if args.workspace_root:
            # Explicit override (highest priority)
            repo_root = Path(args.workspace_root).expanduser().resolve()
        elif args.global_workspace_root:
            repo_root = Path(args.global_workspace_root).expanduser().resolve()
        else:
            # AIPOS-CONN-LOOP-1 §6: Auto-discover via workspace resolution ladder
            # (precedence: AIPOS_WORKSPACE_ROOT env → .lybra/config.json → 5_tasks/queue marker)
            # This replaces the dangerous Path.cwd() fallback that caused 08-12 incident
            try:
                repo_root = resolve_workspace_root()
            except Exception as e:
                error_msg = f"Error: Cannot auto-discover workspace root: {e}"
                print(wrap_error_with_verb_help(error_msg, "lybra_finalize", None), file=sys.stderr)
                return 1

        # AIPOS-FND-14: governance_root (owns 5_tasks/records/audit_verdicts/) is resolved
        # separately from repo_root (the product code repo where git ops run) — the two are
        # decoupled and must never be guessed as the same path. --governance-root wins;
        # otherwise finalize_task() falls back to resolve_workspace_root() auto-discovery.
        governance_root = (
            Path(args.governance_root).expanduser().resolve() if getattr(args, "governance_root", None) else None
        )

        try:
            result = finalize_task(
                task_id=args.task_id,
                actor=args.actor,
                workspace_root=repo_root,
                governance_root=governance_root,
                dry_run=args.dry_run,
                push=args.push,
                deploy=getattr(args, 'deploy', False),
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            error_msg = f"Error: {exc}"
            print(wrap_error_with_verb_help(error_msg, "lybra_finalize", None), file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(result))
        else:
            # AIPOS-R6I 靶③: 自描述补CLI面 - 输出必自携结果+拒因+下一步动作
            verdict = result['verdict']
            print(f"\n=== Finalize Result ===")
            print(f"Task: {result['task_id']}")
            print(f"Actor: {result['actor']}")
            print(f"Verdict: {verdict}")
            print(f"\nMessage: {result['message']}")
            
            if result.get('operations'):
                print("\nOperations:")
                for op in result['operations']:
                    print(f"  - {op}")
            
            if result.get('commit_hash'):
                print(f"\n✓ Commit: {result['commit_hash']}")
            
            # AIPOS-FND-9: Show deployment status
            if result.get('deployed'):
                print("\n✓ Gate deployment completed successfully")
            elif result.get('deployment_error'):
                print(f"\n⚠️  WARNING: Deployment failed: {result['deployment_error']}")
                print("   Manual deployment required: run 'lybra-deploy'")
            elif result.get('deployment_skipped'):
                print("\nℹ️  Deployment skipped (no gate-side changes)")
            
            # AIPOS-R6I 靶③: 下一步动作指引
            print("\n=== Next Steps ===")
            if verdict == Verdict.PASS:
                if result.get('committed') and result.get('pushed'):
                    print("✓ Task finalized and pushed to remote.")
                    if result.get('deployed'):
                        print("✓ Changes deployed to gate.")
                        print("\nAction: Task complete. Run 'lybra queue close --task-id <ID>' to mark as concluded.")
                    else:
                        print("\nAction: Changes committed but deployment pending. Run 'lybra-deploy' if needed.")
                elif result.get('committed'):
                    print("✓ Changes committed locally.")
                    print("\nAction: Push changes with 'git push' or re-run with --push flag.")
                elif result.get('dry_run'):
                    print("ℹ️  Dry-run mode - no changes made.")
                    print("\nAction: Review the operations above. Run without --dry-run to commit.")
            elif verdict == Verdict.BLOCK:
                print("❌ Finalize blocked.")
                print("\nAction: Resolve the blocking reasons listed above before retrying.")
                if not result.get('can_finalize'):
                    print("  - Ensure audit verdict is PASS/PASS_WITH_NOTES in governance records.")
            else:  # FAIL
                print("❌ Finalize failed.")
                print("\nAction: Check error messages above and resolve issues before retrying.")
            
            print()
        
        return 0 if result.get("verdict") == Verdict.PASS else 1

    try:
        repo_root = _find_repo_root_for_args(args)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "envelope":
        if not getattr(args, "envelope_command", None):
            parser.print_help()
            return 2
        if args.envelope_command == "mint":
            # Build autonomy_policy payload
            task_selector = {}
            if args.task_mode:
                task_selector["task_mode"] = args.task_mode
            autonomy_policy = {
                "policy_id": args.policy_id,
                "agent_or_role": args.agent_or_role,
                "active_from": datetime.now(timezone.utc).isoformat(),
                "expires_at": args.expires_at,
                "max_tasks": args.max_tasks,
                "task_selector": task_selector,
            }
            payload = {
                "decision_id": f"envelope-{args.policy_id}",
                "actor": args.actor,
                "decided_by_ref": args.actor,
                "decision_summary": args.decision_summary,
                "autonomy_policy": autonomy_policy,
            }
            try:
                result = record_owner_decision(
                    payload,
                    dry_run=args.dry_run,
                    repo_root=repo_root,
                    actor=args.actor,
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
        
        elif args.envelope_command == "revoke":
            # Revoke envelope by creating superseding decision
            payload = {
                "decision_id": f"revoke-{args.policy_id}",
                "decision_type": "envelope_revocation",
                "actor": args.actor,
                "decided_by_ref": args.actor,
                "decision_summary": f"Revoke envelope {args.policy_id}: {args.revocation_reason}",
                "revoked_policy_id": args.policy_id,
                "revocation_reason": args.revocation_reason,
            }
            try:
                result = record_owner_decision(
                    payload,
                    dry_run=args.dry_run,
                    repo_root=repo_root,
                    actor=args.actor,
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
        
        elif args.envelope_command == "renew":
            # Renew envelope by creating new policy with updated limits
            if not args.add_tasks and not args.new_expiry:
                print("Error: Must specify --add-tasks or --new-expiry (or both)", file=sys.stderr)
                return 1
            
            payload = {
                "decision_id": f"renew-{args.policy_id}",
                "decision_type": "envelope_renewal",
                "actor": args.actor,
                "decided_by_ref": args.actor,
                "decision_summary": args.decision_summary,
                "renewed_policy_id": args.policy_id,
            }
            if args.add_tasks:
                payload["add_tasks"] = args.add_tasks
            if args.new_expiry:
                payload["new_expiry"] = args.new_expiry
            
            try:
                result = record_owner_decision(
                    payload,
                    dry_run=args.dry_run,
                    repo_root=repo_root,
                    actor=args.actor,
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
        
        parser.print_help()
        return 2

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
        return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0

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
        return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0

    if args.command == "queue" and getattr(args, "queue_command", None) in {"claim", "block", "complete", "reopen"}:
        profiles = load_agent_profiles(repo_root)
        # AIPOS-370F2: file-CLI claim now defaults to with_records=True to align session record
        # creation with gate-verb claim, eliminating the "Session record does not exist" blocker
        # when using gate-verb return after file-CLI claim.
        with_records_value = args.with_records
        if args.queue_command == "claim" and not args.with_records:
            # Default to True for claim to create session records (gate-verb return requires them)
            with_records_value = True
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
                with_records=with_records_value,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_queue_mutation_text(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "queue" and getattr(args, "queue_command", None) == "amend":
        from tools.aipos_cli.board_adapter import amend_task
        try:
            amendments = json.loads(args.amendments)
        except json.JSONDecodeError as exc:
            print(f"Error: Invalid JSON in --amendments: {exc}", file=sys.stderr)
            return 1
        try:
            result = amend_task(
                task_id=args.task_id,
                actor=args.actor,
                amendments=amendments,
                amendment_reason=args.amendment_reason,
                dry_run=args.dry_run,
                repo_root=repo_root,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "queue" and getattr(args, "queue_command", None) == "withdraw":
        from tools.aipos_cli.board_adapter import withdraw_task
        try:
            result = withdraw_task(
                task_id=args.task_id,
                actor=args.actor,
                reason=args.reason,
                dry_run=args.dry_run,
                repo_root=repo_root,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "queue" and getattr(args, "queue_command", None) == "return-repair":
        # AIPOS-370F2: return-repair — diagnose and repair stuck return
        from tools.aipos_cli.task_loader import find_task_by_id
        
        try:
            task, all_matches = find_task_by_id(args.task_id, repo_root)
            if not task:
                print(f"Error: Task {args.task_id} not found", file=sys.stderr)
                return 1
            if len(all_matches) > 1:
                print(f"Error: Multiple tasks match {args.task_id}", file=sys.stderr)
                return 1
            
            records = load_records(repo_root)
            task_claims = [c for c in records.get("claims", []) if c.get("task_id") == args.task_id]
            task_returns = [r for r in records.get("returns", []) if r.get("task_id") == args.task_id]
            task_sessions = records.get("task_sessions", {}).get(args.task_id, [])
            
            diagnosis = {
                "operation": "return_repair",
                "task_id": args.task_id,
                "current_state": task.get("queue_state"),
                "claim_count": len(task_claims),
                "return_count": len(task_returns),
                "session_count": len(task_sessions),
                "diagnosis": [],
                "recommended_action": None,
                "repair_actions": [],
            }
            
            # Diagnose stuck patterns
            if task.get("queue_state") == "claimed" and not task_returns:
                diagnosis["diagnosis"].append("Task is claimed but no return records found (stuck return)")
                metadata = task.get("metadata", {})
                session_id = metadata.get("active_session_id") or metadata.get("last_session_id")
                
                # Check if session record exists
                session_exists = False
                if session_id:
                    session_path = expected_session_record_path(repo_root, args.task_id, session_id)
                    session_exists = session_path.exists()
                    diagnosis["session_id"] = session_id
                    diagnosis["session_record_exists"] = session_exists
                
                if not args.dry_run:
                    # AIPOS-370F2: Real repair — file-CLI claim doesn't build session records,
                    # causing gate-verb return to fail. Two strategies:
                    # 1. If no session_id or session doesn't exist: block→reopen→reclaim with --with-records
                    # 2. If session exists but return blocked: manual gate-verb return needed (out of scope)
                    
                    if not session_id or not session_exists:
                        diagnosis["repair_actions"].append("No session record; executing: block → reopen → reclaim with session record")
                        profiles = load_agent_profiles(repo_root)
                        
                        # Step 1: block
                        block_result = mutate_queue_task(
                            repo_root, "block",
                            task_id=args.task_id,
                            actor=args.actor,
                            reason="AIPOS-370F2 return-repair: missing session record",
                            dry_run=False,
                            profiles=profiles,
                            with_records=False,
                        )
                        if block_result.get("verdict") == Verdict.BLOCK:
                            diagnosis["verdict"] = Verdict.BLOCK
                            diagnosis["repair_error"] = f"Block failed: {block_result.get('blocking_reasons')}"
                            print(render_json(diagnosis), file=sys.stderr)
                            return 1
                        diagnosis["repair_actions"].append(f"Blocked: {args.task_id}")
                        
                        # Step 2: reopen
                        reopen_result = mutate_queue_task(
                            repo_root, "reopen",
                            task_id=args.task_id,
                            actor=args.actor,
                            reason="AIPOS-370F2 return-repair: prepare for reclaim with session record",
                            dry_run=False,
                            profiles=profiles,
                            with_records=False,
                        )
                        if reopen_result.get("verdict") == Verdict.BLOCK:
                            diagnosis["verdict"] = Verdict.BLOCK
                            diagnosis["repair_error"] = f"Reopen failed: {reopen_result.get('blocking_reasons')}"
                            print(render_json(diagnosis), file=sys.stderr)
                            return 1
                        diagnosis["repair_actions"].append(f"Reopened: {args.task_id}")
                        
                        # Step 3: reclaim with --with-records to create session record
                        reclaim_result = mutate_queue_task(
                            repo_root, "claim",
                            task_id=args.task_id,
                            actor=args.actor,
                            dry_run=False,
                            profiles=profiles,
                            with_records=True,
                        )
                        if reclaim_result.get("verdict") == Verdict.BLOCK:
                            diagnosis["verdict"] = Verdict.BLOCK
                            diagnosis["repair_error"] = f"Reclaim failed: {reclaim_result.get('blocking_reasons')}"
                            print(render_json(diagnosis), file=sys.stderr)
                            return 1
                        diagnosis["repair_actions"].append(f"Reclaimed with session record: {args.task_id}")
                        diagnosis["new_session_id"] = reclaim_result.get("proposed_session_id")
                        diagnosis["verdict"] = "REPAIRED"
                        diagnosis["recommended_action"] = "Task reclaimed with session record; now ready for gate-verb return"
                    else:
                        diagnosis["verdict"] = "OK"
                        diagnosis["recommended_action"] = "Session record exists; use lybra_queue_return gate tool to complete return"
                else:
                    # Dry-run: just diagnose
                    if not session_id or not session_exists:
                        diagnosis["recommended_action"] = "Would execute: block → reopen → reclaim with --with-records"
                    else:
                        diagnosis["recommended_action"] = "Session record exists; use lybra_queue_return gate tool"
                    diagnosis["verdict"] = "OK"
            elif task.get("queue_state") == "claimed" and task_returns:
                diagnosis["diagnosis"].append(f"Task has {len(task_returns)} return record(s) but still in claimed state")
                diagnosis["recommended_action"] = "State inconsistency detected; requires manual queue state correction"
                diagnosis["verdict"] = Verdict.BLOCK
            else:
                diagnosis["diagnosis"].append(f"Task is in {task.get('queue_state')} state")
                diagnosis["recommended_action"] = "No stuck return detected"
                diagnosis["verdict"] = "OK"
            
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(diagnosis))
        else:
            print(render_json(diagnosis))
        return 1 if diagnosis.get("verdict") == Verdict.BLOCK else 0

    if args.command == "queue" and getattr(args, "queue_command", None) == "return":
        # AIPOS-FND-1: queue return — wrap board_adapter.return_task
        from tools.aipos_cli.board_adapter import return_task
        from tools.aipos_cli.cli_self_describe import wrap_error_with_verb_help
        # AIPOS-R6L 大项B②: canonical_agent 已在顶部导入 (line 32)
        
        artifact_refs = None
        if args.artifact_refs:
            try:
                artifact_refs = json.loads(args.artifact_refs)
            except json.JSONDecodeError as exc:
                error_msg = f"Error: Invalid JSON in --artifact-refs: {exc}"
                print(wrap_error_with_verb_help(error_msg, "lybra_queue_return", repo_root), file=sys.stderr)
                return 1
        
        # 解析actor和agent_instance，使用canonical_agent规范化
        try:
            profiles = load_agent_profiles(repo_root)
            # 如果没有提供agent_instance，使用actor作为agent_instance
            agent_instance = args.agent_instance if hasattr(args, 'agent_instance') and args.agent_instance else args.actor
            # 规范化为canonical agent instance
            canonical_instance = canonical_agent(agent_instance, profiles)
            # actor也规范化
            canonical_actor = canonical_agent(args.actor, profiles)
        except Exception as exc:
            # 如果profiles加载失败，回退到原始值
            canonical_actor = args.actor
            canonical_instance = args.agent_instance if hasattr(args, 'agent_instance') and args.agent_instance else args.actor
        
        try:
            result = return_task(
                task_id=args.task_id,
                actor=canonical_actor,
                agent_instance=canonical_instance,
                owner_policy_ref=args.owner_policy_ref,
                result_summary=args.result_summary,
                artifact_refs=artifact_refs,
                completion_report_ref=getattr(args, "completion_report_ref", None),
                dry_run=args.dry_run,
                repo_root=repo_root,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            error_msg = f"Error: {exc}"
            print(wrap_error_with_verb_help(error_msg, "lybra_queue_return", repo_root), file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    # AIPOS-C1 大项A: queue close — wrap board_adapter.close_task
    if args.command == "queue" and getattr(args, "queue_command", None) == "close":
        from tools.aipos_cli.board_adapter import close_task
        try:
            closure_evidence = json.loads(args.closure_evidence)
        except json.JSONDecodeError as exc:
            print(f"Error: Invalid JSON in --closure-evidence: {exc}", file=sys.stderr)
            return 1
        try:
            result = close_task(
                task_id=args.task_id,
                actor=args.actor,
                closure_evidence=closure_evidence,
                dry_run=args.dry_run,
                repo_root=repo_root,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
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
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
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
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

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
        state_cmd = getattr(args, "state_command", None)

        # AIPOS-C3B 大项C①: state lint
        if state_cmd == "lint":
            from tools.aipos_cli.state_lint import run_state_lint
            ws_root = getattr(args, "workspace_root", None)
            if ws_root is None:
                from tools.aipos_cli.workspace_config import resolve_workspace_root
                ws_root = resolve_workspace_root()
            result = run_state_lint(
                governance_root=Path(ws_root),
                task_id_filter=getattr(args, "task_id", None),
            )
            if getattr(args, "json", False):
                print(render_json(result))
            else:
                issues = result.get("issues", [])
                if not issues:
                    print(f"✓ state lint OK: {result['scanned']} 张卡扫描, 无断层")
                else:
                    print(f"✗ state lint: {len(issues)} 断层(扫描 {result['scanned']} 张卡)")
                    for issue in issues:
                        print(f"  - [{issue['severity']}] {issue['task_id']}: {issue['message']}")
            return 1 if issues else 0

        # AIPOS-C3B 大项C③: state repair
        if state_cmd == "repair":
            from tools.aipos_cli.state_lint import repair_task_state
            ws_root = getattr(args, "workspace_root", None)
            if ws_root is None:
                from tools.aipos_cli.workspace_config import resolve_workspace_root
                ws_root = resolve_workspace_root()
            result = repair_task_state(
                governance_root=Path(ws_root),
                task_id=args.task_id,
                dry_run=getattr(args, "dry_run", False),
            )
            if getattr(args, "json", False):
                print(render_json(result))
            else:
                if result.get("repaired"):
                    print(f"✓ 已修复 {args.task_id}: {result['message']}")
                elif result.get("dry_run"):
                    print(f"(dry-run) 会修复 {args.task_id}: {result['message']}")
                else:
                    print(f"无需修复 {args.task_id}: {result['message']}")
            return 0

        if (
            state_cmd != "recovery"
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
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "queue":
        if args.json:
            print(render_json(_json_report(report, records=records)))
        else:
            print(render_queue_text(report))
        return 0

    if args.command == "sync":
        # AIPOS-C4B 大项A③: lybra sync — 工位发起 pull, 对比清单并拉差异落盘
        from tools.aipos_cli.distribution_sync import sync as run_sync
        from pathlib import Path as _Path
        try:
            result = run_sync(
                harness_root=_Path(args.harness_root) if args.harness_root else None,
                gate_url=args.gate_url,
                token=args.token,
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            if result.get("ok"):
                print(f"sync ok · role={result['role']} · product_commit={result['product_commit']}")
                print(f"  harness: {result['harness_root']}")
                print(f"  distributions checked: {result['distributions_checked']}, files fetched: {result['files_fetched']}")
                for c in result.get("changes", []):
                    print(f"  - {c['distribution_id']}: {c['files_written']} file(s) → {c['target_path']}")
                print(f"  manifest: {result['manifest_path']}")
                print("  下一步: /reload 让新扩展/技能生效")
            else:
                print(f"sync failed: {result.get('error')}")
        return 0 if result.get("ok") else 1

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

    if args.command == "auditor":
        if getattr(args, "auditor_command", None) == "loop":
            # AIPOS-358: thin shell — only workspace-root and interval matter
            from tools.aipos_cli.auditor_loop import main as auditor_loop_main
            argv = ["--workspace-root", args.workspace_root, "--interval", str(args.interval)]
            return auditor_loop_main(argv)
        if getattr(args, "auditor_command", None) == "launch":
            # AIPOS-358: auditor launch (execution出口, called by turn-advancer dispatch_audit)
            from tools.aipos_cli.auditor_runtime import launch_auditor_runtime
            product_repo = (args.product_repo or Path.home() / "projects" / "lybra").expanduser().resolve()
            ws = args.workspace_root.expanduser().resolve()
            try:
                result = launch_auditor_runtime(
                    runtime_cmd_template=args.runtime_cmd,
                    audit_task_id=args.task_id,
                    reviewed_task_id=args.reviewed_task_id or "",
                    audit_card_path=args.audit_card_path or "",
                    product_repo=product_repo,
                    workspace_root=ws,
                    envelope=args.envelope,
                )
                return int(result["exit_code"])
            except Exception as exc:
                print(f"ERROR: auditor launch failed: {exc}", file=sys.stderr)
                return 1
        parser.print_help()
        return 2

    if args.command == "audit":
        if getattr(args, "audit_command", None) == "dispatch":
            from tools.aipos_cli.board_adapter import audit_dispatch_task
            try:
                result = audit_dispatch_task(
                    source_task_id=args.source_task_id,
                    source_path=args.source_task_path,
                    actor=args.actor,
                    agent_instance=args.agent_instance,
                    owner_policy_ref=args.owner_policy_ref,
                    audit_task_id=args.audit_task_id,
                    audit_task_title=args.audit_task_title,
                    audit_by=args.audit_by,
                    audit_agent_instance=args.audit_agent_instance,
                    dispatch_reason=args.dispatch_reason,
                    dry_run=args.dry_run,
                    repo_root=repo_root,
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_json(result))
            return 1 if result.get("verdict") == Verdict.BLOCK else 0
        parser.print_help()
        return 2

    if args.command == "audit-verdict":
        # AIPOS-R4B-2 N4: 审计裁决自助 — 从 LoopContext 自发现身份参数
        from tools.aipos_cli.confirm_client import GateClient
        from tools.aipos_cli.audit_helpers import (
            resolve_audit_context,
            build_audit_verdict_dry_run_args,
            build_audit_verdict_confirm_args,
        )
        
        try:
            evidence_refs = []
            if args.evidence_refs:
                evidence_refs = json.loads(args.evidence_refs)
        except json.JSONDecodeError as exc:
            print(f"Error: Invalid JSON in --evidence-refs: {exc}", file=sys.stderr)
            return 1
        
        # AIPOS-R4B-2: 自发现模式 — 当必要参数缺失时，从 LoopContext 解析
        auto_discover = not all([
            getattr(args, 'actor', None),
            getattr(args, 'agent_instance', None),
            getattr(args, 'owner_policy_ref', None),
        ])
        # AIPOS-SMOKE-LOOP-1 坑①: workspace_root 两分支都要可用 (派生 audit R 卡 task_id)
        workspace_root: Path | None = None
        if hasattr(args, 'workspace_root') and args.workspace_root:
            workspace_root = Path(args.workspace_root).expanduser().resolve()
        elif hasattr(args, 'global_workspace_root') and args.global_workspace_root:
            workspace_root = Path(args.global_workspace_root).expanduser().resolve()
        
        if auto_discover:
            # 自发现模式
            try:
                # Resolve workspace root
                workspace_root = None
                if hasattr(args, 'workspace_root') and args.workspace_root:
                    workspace_root = Path(args.workspace_root).expanduser().resolve()
                elif hasattr(args, 'global_workspace_root') and args.global_workspace_root:
                    workspace_root = Path(args.global_workspace_root).expanduser().resolve()
                
                context = resolve_audit_context(
                    workspace_root=workspace_root,
                    role=getattr(args, 'token_role', 'auditor'),
                    gate_url=getattr(args, 'gate_url', None),
                )
                
                print(f"[自发现] gate_url: {context['gate_url']}", file=sys.stderr)
                print(f"[自发现] role: {context['role']}", file=sys.stderr)
                print(f"[自发现] agent_instance: {context['agent_instance']}", file=sys.stderr)
                print(f"[自发现] actor: {context['actor']}", file=sys.stderr)
                
            except Exception as exc:
                print(f"Error: Auto-discovery failed: {exc}", file=sys.stderr)
                print("Hint: Ensure .lybra/connection.json exists or provide explicit parameters.", file=sys.stderr)
                return 1
        else:
            # 显式参数模式（向后兼容）
            from tools.aipos_cli.confirm_client import load_owner_token
            
            connection_json_path = args.connection_json
            if not connection_json_path:
                workspace_candidate = Path(repo_root).parent if repo_root else Path.cwd()
                connection_json_path = workspace_candidate / ".lybra" / "connection.json"
                if not connection_json_path.exists():
                    connection_json_path = Path(repo_root or Path.cwd()) / ".lybra" / "connection.json"
            
            try:
                token = load_owner_token(
                    connection_json=connection_json_path,
                    role=args.token_role
                )
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f"Error reading token: {exc}", file=sys.stderr)
                return 1
            
            context = {
                "gate_url": args.gate_url,
                "token": token,
                "role": args.token_role,
                "actor": args.actor,
                "agent_instance": args.agent_instance,
                "owner_policy_ref": args.owner_policy_ref,
                "source": "explicit",
            }
        
        # 初始化 GateClient
        client = GateClient(context["gate_url"], context["token"])
        try:
            client.initialize()
        except Exception as exc:
            print(f"Error connecting to gate: {exc}", file=sys.stderr)
            return 1
        
        # 构建 dry_run 参数
        dry_run_args = build_audit_verdict_dry_run_args(
            reviewed_task_id=args.reviewed_task_id,
            verdict=args.verdict,
            context=context,
            audit_task_id=getattr(args, 'audit_task_id', None),
            findings_summary=getattr(args, 'findings_summary', None),
            evidence_refs=evidence_refs if evidence_refs else None,
            audit_claim_id=getattr(args, 'audit_claim_id', None),
            audit_session_id=getattr(args, 'audit_session_id', None),
            audit_dispatch_record_ref=getattr(args, 'audit_dispatch_record_ref', None),
            reviewed_return_record_ref=getattr(args, 'reviewed_return_record_ref', None),
            recommended_next_action=getattr(args, 'recommended_next_action', None),
            owner_waiver_ref=getattr(args, 'owner_waiver_ref', None),
            # AIPOS-SMOKE-LOOP-1 坑①: 传 workspace 作 repo_root, 供派生 audit R 卡 task_id
            repo_root=workspace_root,
        )
        
        # 第一阶段：dry_run
        try:
            structured = client.call_tool("lybra_audit_verdict_dry_run", dry_run_args)
        except Exception as exc:
            print(f"Error calling lybra_audit_verdict_dry_run: {exc}", file=sys.stderr)
            return 1
        
        dry_run_token = structured.get("dry_run_token") or structured.get("dry_run_id")
        if not dry_run_token:
            # 无 token = 被拦截或错误
            if args.json:
                print(render_json(structured))
            else:
                print("\u2717 Audit verdict dry-run BLOCKED or ERROR:", file=sys.stderr)
                print(render_json(structured), file=sys.stderr)
            return 1
        
        # 第二阶段：自动 confirm（审计 pi 无需人工介入）
        confirm_args = build_audit_verdict_confirm_args(
            dry_run_token=dry_run_token,
            context=context,
        )
        
        try:
            result = client.call_tool("lybra_audit_verdict_confirm", confirm_args)
        except Exception as exc:
            print(f"Error calling lybra_audit_verdict_confirm: {exc}", file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(result))
        else:
            if result.get("ok"):
                print("\u2713 Audit verdict recorded successfully.")
                data = result.get("data", {})
                if data.get("target_path"):
                    print(f"  verdict record: {data.get('target_path')}")
            else:
                print("\u2717 Audit verdict BLOCKED:", file=sys.stderr)
                print(render_json(result), file=sys.stderr)
        
        return 0 if result.get("ok") else 1

    if args.command == "pump":
        if getattr(args, "pump_command", None) == "run":
            from tools.aipos_cli.advisor_pump import validate_and_dispatch
            from tools.aipos_cli.pump_orchestration import (
                DispatchContext, run_pump_dispatch, render_dispatch_plan, list_unmanaged_agents,
            )

            workspace_root = Path(args.workspace_root).expanduser().resolve()
            product_repo = Path(args.product_repo).expanduser().resolve() if args.product_repo else Path.home() / "projects" / "lybra"
            connection_json = Path(args.connection_json).expanduser().resolve() if getattr(args, "connection_json", None) else (workspace_root / ".lybra" / "connection.json")

            # S3: --check-unmanaged 只读列出非泵派出的在跑 agent,后退出(不阻止人工介入)
            if getattr(args, "check_unmanaged", False):
                unmanaged = list_unmanaged_agents(product_repo, workspace_root, managed_task_ids=set())
                if args.json:
                    print(render_json({"unmanaged": unmanaged}))
                else:
                    print("[S3] 非泵派出的在跑 agent(只读告警,不阻止):")
                    for u in unmanaged:
                        print(f"  - {u['task_id']}  信号: {u['signal']}")
                    if not unmanaged:
                        print("  (无)")
                return 0

            # 保留现有三层制约(预算/复述)——零回归
            result = validate_and_dispatch(
                card_id=args.card_id, role=args.role, round_type=args.round_type,
                delta=args.delta, workspace_root=workspace_root,
                budget_threshold=args.budget_threshold, repetition_threshold=args.repetition_threshold,
                dry_run=args.dry_run,
            )
            if not result["ok"]:
                if args.json:
                    print(render_json(result))
                else:
                    print("\u2717 Kickoff validation BLOCKED")
                    for error in result.get("errors", []):
                        print(f"  - {error}")
                return 1

            # 构建编排上下文(AIPOS-332)
            try:
                collab = get_collaboration_profile(str(product_repo))
            except Exception:
                collab = None
            # AIPOS-332F5:解析 workdir(优先级:CLI --workdir > runtime_cmds.yaml > None)
            workdir_path = None
            if getattr(args, "workdir", None):
                workdir_path = Path(args.workdir).expanduser().resolve()
            else:
                # 尝试从 runtime_cmds.yaml 读取
                import yaml as _yaml
                rc_yaml_path = getattr(args, "runtime_cmds_yaml", None)
                if not rc_yaml_path:
                    # 自动发现:产品仓 config/runtime_cmds.yaml
                    candidate = product_repo / "config" / "runtime_cmds.yaml"
                    if candidate.is_file():
                        rc_yaml_path = str(candidate)
                if rc_yaml_path:
                    try:
                        with open(rc_yaml_path) as _f:
                            _rc_data = _yaml.safe_load(_f) or {}
                        runtime_type = getattr(args, "runtime", None)
                        if runtime_type and runtime_type in _rc_data:
                            _wd = _rc_data[runtime_type].get("workdir")
                            if _wd:
                                workdir_path = Path(_wd).expanduser().resolve()
                    except Exception:
                        pass  # 配置读失败不阻塞派工,workdir 留 None 走降级
            ctx = DispatchContext(
                card_id=args.card_id, role=args.role, round_type=args.round_type, delta=args.delta,
                workspace_root=workspace_root, product_repo=product_repo,
                gate_url=getattr(args, "gate_url", None) or _get_default_gate_url(),
                connection_json=connection_json, envelope=getattr(args, "envelope", "") or "",
                executor_instance=getattr(args, "executor_instance", None) or "",
                reviewed_task_id=getattr(args, "reviewed_task_id", None),
                runtime_type=getattr(args, "runtime", None),
                output_target=getattr(args, "output_target", None),
                collaboration_profile=collab,
                runtime_cmd_template=getattr(args, "runtime_cmd", None),
                workdir=workdir_path,
            )

            dispatch = run_pump_dispatch(ctx, dry_run=args.dry_run)

            if args.json:
                # JSON 只增字段不删字段、不改字段语义(S6④)
                out = dict(result)
                out["dispatch"] = dispatch
                print(render_json(out))
            else:
                if args.dry_run:
                    print("\u2713 Kickoff validation PASSED")
                    print(render_dispatch_plan(dispatch))
                    metrics = result.get("metrics", {})
                    if metrics:
                        print(f"\nMetrics: tokens={metrics.get('total_tokens','N/A')} overlap={metrics.get('overlap_ratio',0.0):.1%}")
                    print("\n[DRY RUN] 校验通过,未派工(--dry-run 保留现有语义)。")
                else:
                    print(render_dispatch_plan(dispatch))
                    sv = (dispatch.get("watch") or {}).get("verify") or dispatch.get("sentinel_verify") or {}
                    if sv.get("expect_status"):
                        print("\n哨兵自证(expect 布防即检):")
                        for e in sv["expect_status"]:
                            tag = f" [{e.get('label')}]" if e.get("matched") else ""
                            print(f"  - {e['pattern']}  命中={e['matched']}{tag}")
            return 0 if dispatch["ok"] else 1
        
        parser.print_help()
        return 2

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

    if args.command == "turn-advancer":
        # AIPOS-340: Turn advancer (next-step resolver)
        from tools.turn_advancer import resolve_next_command
        from tools.turn_advancer.resolver import scan_all_tasks
        
        workspace_root = args.workspace_root or workspace
        
        if args.turn_command == "next":
            try:
                result = resolve_next_command(
                    task_id=args.task_id,
                    workspace_root=workspace_root,
                    dispatch_mode=args.mode,
                )
                exit_code = 0
                # AIPOS-340F6:auto 模式接真执行出口(subprocess + 退出码透传 + 前后留痕 +
                # 判断留人拒绝);manual 模式零回归(仅解析打印,不执行)。
                if args.mode == "auto":
                    from tools.turn_advancer.auto_executor import execute_auto
                    actor = os.environ.get("LYBRA_AUTO_ACTOR", "turn_advancer_auto")
                    result["execution"] = execute_auto(
                        result, workspace_root, actor=actor
                    )
                    exit_code = int(result["execution"]["exit_code"])
                print(render_json(result))
                return exit_code
            except Exception as exc:
                print(f"Error resolving next command: {exc}", file=sys.stderr)
                return 1
        
        elif args.turn_command == "scan":
            try:
                results = scan_all_tasks(workspace_root, dispatch_mode=args.mode)
                print(render_json({"tasks": results, "total": len(results)}))
                return 0
            except Exception as exc:
                print(f"Error scanning tasks: {exc}", file=sys.stderr)
                return 1
        
        else:
            print("turn-advancer subcommand required: next | scan", file=sys.stderr)
            return 2

    if args.command == "next-step":
        # AIPOS-R7A: next-step navigation from transitions.schema
        from tools.aipos_cli.transition_engine import resolve_next_step_from_schema
        
        try:
            if args.workspace_root:
                workspace_root = Path(args.workspace_root)
            else:
                workspace_root = _find_repo_root_for_args(args)
            
            result = resolve_next_step_from_schema(
                task_id=args.task_id,
                workspace_root=workspace_root,
            )
            if args.json:
                print(render_json(result))
            else:
                # Human-readable output
                print(f"Task: {result['task_id']}")
                print(f"Current state: {result['current_state']}")
                print(f"Next step: {result['next_step']}")
                print(f"Triggered by: {result['triggered_by']}")
                print(f"\nCommand:")
                print(f"  {result['command']}")
                if result.get('notes'):
                    print(f"\nNotes: {result['notes']}")
            return 0
        except Exception as exc:
            print(f"Error resolving next-step: {exc}", file=sys.stderr)
            return 1

    # AIPOS-FND-1: Five missing loop-step CLI implementations
    if args.command == "task-progress":
        # Wrap task progress writer (local variant, bypasses MCP scope)
        from tools.aipos_cli.task_progress_writer import write_task_progress_event
        
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        result = write_task_progress_event(
            repo_root=repo_root,
            task_id=args.task_id,
            actor=args.actor,
            event_type=args.event_type,
            agent_instance=args.agent_instance,
            summary=args.summary,
            model_self_reported=args.model_self_reported,
            stage=args.stage,
            reason=args.reason,
        )
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "bench-audit":
        # Wrap bench_audit_writer (local variant)
        from tools.aipos_cli.bench_audit_writer import build_bench_audit_record
        
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        payload = {
            "task_id": args.task_id,
            "conclusion": args.conclusion,
        }
        if args.evidence_type:
            payload["evidence_type"] = args.evidence_type
        if args.task_mode:
            payload["task_mode"] = args.task_mode
        if args.evidence_refs:
            try:
                payload["evidence_refs"] = json.loads(args.evidence_refs)
            except json.JSONDecodeError as exc:
                print(f"Error: Invalid JSON in --evidence-refs: {exc}", file=sys.stderr)
                return 1
        if args.notes:
            payload["notes"] = args.notes
        
        result = build_bench_audit_record(
            repo_root=repo_root,
            payload=payload,
            actor=args.actor,
            dry_run=args.dry_run,
        )
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "owner-decision":
        # AIPOS-R7A: Record owner decision (arbitration, exemptions, policy changes)
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        payload = {
            "decision_id": args.decision_id,
            "decision_type": args.decision_type,
            "actor": args.actor,
            "decided_by_ref": args.actor,
            "decision_summary": args.decision_summary,
        }
        if args.task_id:
            payload["applies_to"] = {"task_id": args.task_id}
        if args.context_refs:
            try:
                payload["context_refs"] = json.loads(args.context_refs)
            except json.JSONDecodeError as exc:
                print(f"Error: Invalid JSON in --context-refs: {exc}", file=sys.stderr)
                return 1
        
        try:
            result = record_owner_decision(
                payload,
                dry_run=args.dry_run,
                repo_root=repo_root,
                actor=args.actor,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "owner-verify":
        # Wrap owner_verification_writer (local variant)
        from tools.aipos_cli.owner_verification_writer import build_owner_verification_record
        
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        payload = {
            "task_id": args.task_id,
            "decision": args.decision_type,
            "reason": args.decision_summary,
            "decided_via": "cli",
        }
        if args.context_refs:
            try:
                payload["context_refs"] = json.loads(args.context_refs)
            except json.JSONDecodeError as exc:
                print(f"Error: Invalid JSON in --context-refs: {exc}", file=sys.stderr)
                return 1
        
        result = build_owner_verification_record(
            repo_root=repo_root,
            payload=payload,
            actor=args.actor,
            dry_run=args.dry_run,
        )
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK else 0

    if args.command == "converge":
        # Wrap converge_r_cards from board_adapter
        from tools.aipos_cli.board_adapter import converge_r_cards
        
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        result = converge_r_cards(
            repo_root=repo_root,
            actor=args.actor,
            dry_run=args.dry_run,
        )
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK or not result.get("ok") else 0

    if args.command == "mark-concluded":
        # Wrap mark_concluded_task from board_adapter
        from tools.aipos_cli.board_adapter import mark_concluded_task
        from tools.aipos_cli.cli_self_describe import wrap_error_with_verb_help
        
        try:
            repo_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            error_msg = f"Error: {exc}"
            print(wrap_error_with_verb_help(error_msg, "lybra_mark_concluded", None), file=sys.stderr)
            return 1
        
        try:
            result = mark_concluded_task(
                task_id=args.task_id,
                repo_root=repo_root,
                actor=args.actor,
                report_path=args.report_path,
                conclusion_note=args.conclusion_note,
                dry_run=args.dry_run,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            error_msg = f"Error: {exc}"
            print(wrap_error_with_verb_help(error_msg, "lybra_mark_concluded", None), file=sys.stderr)
            return 1
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == Verdict.BLOCK or not result.get("ok") else 0

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

