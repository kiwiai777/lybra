from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from tools.schema_constants import RecordType, Verdict
from datetime import datetime, timezone
from tools.aipos_cli.clock import iso_z, utc_now
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
from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN, snapshot_hash, validate_owner_confirmation
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
from tools.aipos_cli.autonomy_policy import cli_envelope_trace_entry, decide_cli_envelope_trace  # AIPOS-F142 件②
from tools.aipos_cli.queue_mutation import mutate_queue_task
from tools.aipos_cli.records import load_records, expected_session_record_path
from tools.aipos_cli.service_mode import (
    connection_path as workspace_connection_path,
    render_connection_table,
    roles_list_report,
    roles_reconcile_report,
    rotate_report,
    start_report,
    status_report,
    stop_report,
)
from tools.aipos_cli.state_recovery import build_state_recovery_preview
from tools.aipos_cli.task_loader import load_all_tasks, load_task_by_path
from tools.aipos_cli.validator import (
    build_records_diagnostics,
    build_records_summary,
    validate_single_task,
    validate_tasks,
)
from tools.aipos_cli.workspace_config import (
    WORKSPACE_ROOT_ENV,
    DEFAULT_BOARD_HOST,
    DEFAULT_BOARD_PORT,
    DEFAULT_MCP_PORT,
    _project_candidates,
    get_collaboration_profile,
    project_json_path,
    read_project_json,
    governance_workspace_root,
    resolve_home_root,
    resolve_home_root_with_source,
    resolve_workspace_root,
    scaffold_project,
    stage_archive_root,
)

from tools.aipos_cli.home_git import execute_home_git_init, plan_home_git_init
from tools.aipos_cli.project_structure import (
    export_project_to_yaml,
    import_project_structure,
    validate_structure,
    parse_yaml,
)


def _agent_runtime_arg(args: Any) -> dict[str, Any] | None:
    """AIPOS-F90 件②: --agent-runtime JSON(产品填写的运行时模型 bundle)。缺 = {}; 不可解析/非对象 = 出声 + None(调用方 exit 1)。"""
    raw = getattr(args, "agent_runtime", None)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON in --agent-runtime: {exc}", file=sys.stderr)
        return None
    if not isinstance(value, dict):
        print("Error: --agent-runtime must be a JSON object", file=sys.stderr)
        return None
    return value


def _filter_my_tasks(report: dict[str, Any], actor: str, profiles: dict[str, Any]) -> dict[str, Any]:
    filtered = [
        task
        for task in report["tasks"]
        if actor_matches_task(task, actor, profiles)
    ]
    availability = availability_for_actor(actor, profiles)
    return {**report, "scope": "my_tasks", "actor": actor, "tasks": filtered, **availability}


def _subagent_actor_exit(workstation: str, actor: str) -> str:
    """AIPOS-F146 件②: `my-tasks --workstation <治理根> --actor <子 agent 执行者>` 的明确出口(子 agent 无工位, 不写 .lybra/role 槽)。
    判定唯一 enrollment.instance_enrollment(governance_root_mode); 目录非治理根 / 非子 agent = 空串(拒因原文同修前)。
    接入登记读不出 = 如实附在拒因里(仍拒, 不吞)。"""
    root = Path(workstation).expanduser()
    if not actor or not (root / "project.json").is_file():
        return ""
    from tools.aipos_cli.enrollment import instance_enrollment

    try:
        view = instance_enrollment(root, actor)
    except ValueError as exc:
        return f"; 接入登记读不出: {exc}"
    if not view["governance_root_mode"]:
        return ""
    return (f"; 实例 {actor} 为子 agent 执行者(接入模式 {view['mode']}, 无工位), 出口: lybra my-tasks --actor {actor} "
            f"--workspace-root {root.resolve()} --task-id <卡号>(不带 --workstation)")


def _resolve_my_tasks_workstation(args: argparse.Namespace) -> int | None:
    """AIPOS-F89 件③b(Owner 2026-10-03 裁定 A2): `my-tasks --workstation <工位目录>` = 产品解析工位身份——实例与治理根经
    charter_render.workstation_identity / resolve_workstation_governance_root(唯一实现), 回填 args.actor / args.workspace_root。
    无 --workstation 时须给 --actor; 两者都给且不一致 = 拒。返回 None = 继续; 整数 = 退出码(拒因已上 stderr, 不含 token)。"""
    workstation = str(getattr(args, "workstation", None) or "").strip()
    actor = str(getattr(args, "actor", None) or "").strip()
    if not workstation:
        if not actor:
            print("Error: my-tasks 需 --actor <实例> 或 --workstation <工位目录>(工位 /go 传 --workstation)", file=sys.stderr)
            return 2
        return None
    from tools.aipos_cli.charter_render import (
        WorkstationIdentityError,
        WorkstationInstanceNotRegistered,
        resolve_workstation_governance_root,
        workstation_identity,
    )

    try:
        # AIPOS-F140 件②: 给了 --actor = 按实例取记录(治理根多顾问实例取其槽; 工位无槽 = 顶层, 实例不符拒因同修前)
        identity = workstation_identity(workstation, instance=actor or None)
    except WorkstationInstanceNotRegistered as exc:
        listed = f"; 已登记实例: {', '.join(exc.registered)}" if exc.slotted else ""
        print(f"Error: --actor {actor} ≠ 工位 {Path(workstation).expanduser().resolve()} 的实例 {exc.top_instance}"
              f"(二者择一, 以工位身份为准{listed}){_subagent_actor_exit(workstation, actor)}", file=sys.stderr)
        return 2
    except WorkstationIdentityError as exc:
        print(f"Error: WORKSTATION_IDENTITY_UNRESOLVED: {exc}", file=sys.stderr)
        return 1
    if actor and actor != identity["instance"]:
        print(f"Error: --actor {actor} ≠ 工位 {identity['harness_root']} 的实例 {identity['instance']}(二者择一, 以工位身份为准)",
              file=sys.stderr)
        return 2
    args.actor = identity["instance"]
    explicit_root = getattr(args, "workspace_root", None) or getattr(args, "global_workspace_root", None)
    if not explicit_root:
        try:
            args.workspace_root = str(resolve_workstation_governance_root(identity))
        except FileNotFoundError as exc:
            print(f"Error: WORKSTATION_GOVERNANCE_ROOT_UNRESOLVED: {exc}", file=sys.stderr)
            return 1
    args.workstation_identity = {"harness_root": identity["harness_root"], "instance": identity["instance"],
                                 "role": identity["role"], "project": identity["project"]}
    return None


def _resolve_kickoff_ref(repo_root: Path, ref: str) -> str:
    """AIPOS-F90 件③: 冷启动指向(卡号或卡文件路径) → 卡 task_id(路径读卡面 frontmatter, 唯一读取口); 解析不到原样返回(按卡号判)。"""
    text = str(ref or "").strip()
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    if (text.endswith(".md") or "/" in text) and candidate.is_file():
        from tools.aipos_cli.next_resolver import _read_frontmatter

        # AIPOS-F100 件②: 读不出 = FrontmatterReadError 向上(调用方转 FRONTMATTER_UNREADABLE 拒因), 不按文件名猜卡号
        return str(_read_frontmatter(candidate).get("task_id") or candidate.stem).strip()
    return text


def _attach_workstation_view(output: dict[str, Any], actor_report: dict[str, Any], repo_root: Path, *, actor: str = "",
                             requested: str | None = None, remote_workstation: bool = False) -> dict[str, Any]:
    """AIPOS-F86 件①: my-tasks --json 的每张 claimed 卡附开工面字段(card_path / worktree_* / report_*)。

    推导只在产品侧一处: next_resolver.card_workstation_view(→ card_worktree_location / card_report_path, 与 claim 建树、
    card render 同一函数); 工位 /go 只读这些字段。不可推导 / 尚未建立 = 明确拒因字段, 不输出空串。
    """
    from tools.aipos_cli.next_resolver import card_workstation_view, kickoff_refusal, select_next_card

    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.next_resolver import KICKOFF_REFUSAL_CODES

    root = Path(repo_root).resolve()
    try:
        requested_id = _resolve_kickoff_ref(root, requested) if requested else None
    except FrontmatterReadError as exc:
        # AIPOS-F100 件②: 冷启动指向的卡文件读不出 = 拒因原文(不选卡、不猜卡号)
        output.update(select_next_card([]))
        output["next_card_excluded"] = [{"task_id": str(requested), "code": "FRONTMATTER_UNREADABLE",
                                         "reason": f"{KICKOFF_REFUSAL_CODES['FRONTMATTER_UNREADABLE']}: {exc}"}]
        output["kickoff_requested"] = str(requested)
        return output
    candidates: list[dict[str, Any]] = []
    for summary, task in zip(output["tasks"], actor_report["tasks"]):
        if summary.get("queue_state") != "claimed":
            continue
        if requested_id and str(task.get("task_id") or "") != requested_id:
            continue
        summary["card_path"] = str(root / str(task.get("path")))
        summary.update(card_workstation_view(root, str(task.get("task_id") or ""), task.get("metadata") or {}))
        metadata = task.get("metadata") or {}
        # AIPOS-F90 件③: 开工核验(本人在办/未结案/产物未交), 与「以卡号/路径冷启动」同一判据
        summary["kickoff_refusal"] = kickoff_refusal(root, str(task.get("task_id") or ""), actor, queue_state="claimed",
                                                     card_frontmatter=metadata)
        candidates.append({
            **summary,
            "claimed_at": metadata.get("claimed_at"),
            "frontmatter_warnings": list(task.get("parse_errors") or []),
        })
    # AIPOS-F110 件②: --remote-workstation = 本实例工位跨机(land 事件 transport=remote), 开工提示附门机材料段(同一 render_kickoff);
    # 上下文不可得(非跨机 / 材料未声明 / 含凭据)= 不出开工提示(KICKOFF_UNRESOLVED, fail-closed)
    remote_ctx: dict[str, Any] | None = None
    remote_error = ""
    if remote_workstation:
        from tools.aipos_cli.next_resolver import remote_kickoff_context

        try:
            remote_ctx = remote_kickoff_context(root, actor)
        except (ValueError, OSError) as exc:
            remote_error = str(exc)
    # AIPOS-F87 件③: 开工选卡由产品给出(判据唯一声明 next_resolver.NEXT_CARD_RULE), 工位 /go 只读 next_card
    output.update(select_next_card(candidates, remote=remote_ctx))
    if remote_error and isinstance(output.get("next_card"), dict):
        output["next_card_excluded"] = [{"task_id": output["next_card"]["task_id"], "code": "KICKOFF_UNRESOLVED",
                                         "reason": f"跨机开工提示不可渲染: {remote_error}"}, *output.get("next_card_excluded", [])]
        output["next_card"] = None
    if requested_id and not candidates:
        # AIPOS-F90 件③: 冷启动指向的卡不在本实例的 claimed 集 → 按队列真相给拒因(未认领/已结案/非本人/找不到)
        from tools.aipos_cli.next_resolver import _read_frontmatter
        from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

        try:
            card_path, queue_state = find_task_card(root, requested_id)
        except AmbiguousTaskCard as exc:
            output["next_card_excluded"] = [{"task_id": requested_id, "code": "NOT_FOUND", "reason": str(exc)}]
        else:
            try:
                card_fm = _read_frontmatter(card_path) if card_path else {}
            except FrontmatterReadError as exc:
                refusal = {"task_id": requested_id, "code": "FRONTMATTER_UNREADABLE",
                           "reason": f"{KICKOFF_REFUSAL_CODES['FRONTMATTER_UNREADABLE']}: {exc}"}
            else:
                refusal = kickoff_refusal(root, requested_id, actor, queue_state=queue_state, card_frontmatter=card_fm)
            if refusal is None:  # 队列里在办且判据放行, 但不在本实例 my-tasks 名下 = 非本人
                refusal = {"task_id": requested_id, "code": "NOT_MINE", "reason": f"卡的认领实例不是本实例, 不能开工: {requested_id} 不在 {actor} 的已认领卡中"}
            output["next_card_excluded"] = [refusal]
    if requested_id:
        output["kickoff_requested"] = requested_id
    return output


def _filter_needs_owner(report: dict[str, Any], *, governance_root: Path | None = None, lane: str | list[str] | None = None,
                        include_frozen: bool = False) -> dict[str, Any]:
    filtered = [
        task
        for task in report["tasks"]
        if task["verdict"] == Verdict.NEEDS_OWNER
        or task["metadata"].get("needs_owner") is True
        or task["metadata"].get("owner_review_required") is True
        or task["metadata"].get("approval_required") is True
        or bool(task["needs_owner_reasons"])
    ]
    if governance_root is None:
        return {**report, "scope": "needs_owner", "tasks": filtered}
    # AIPOS-F141: 四视图唯一可见卡入口 machine_zone.visible_cards(冻结卡缺省不列 = F122 唯一判定; lane 经唯一派生 lane_of_card +
    # 唯一过滤 filter_rows_by_lane, 给定 --lane 时未解析 lane 的卡单独成组)
    from tools.aipos_cli.machine_zone import visible_cards

    view = visible_cards(governance_root, ((task, task.get("metadata")) for task in filtered), lane=lane, include_frozen=include_frozen)
    return {**report, "scope": "needs_owner", "tasks": view["rows"], "view": view}


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
            expires_status = "valid" if parsed_expires > utc_now() else "expired"
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
    """AIPOS-F117 件①(gap #52): 委托 .lybra/config.json 运行时字段唯一读取口 workspace_config.workspace_runtime_config
    (strict: 文件坏 = 抛, 启动路径 fail-closed); 原逐字段第二份读取 + 缺省删除。"""
    from tools.aipos_cli.workspace_config import workspace_runtime_config

    return workspace_runtime_config(workspace_root, strict=True)


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
    """AIPOS-F144: CLI 命令治理根的薄壳 —— 实现 = workspace_config.resolve_governance_root(读写共用唯一实现)。
    显式根 = 全局 --workspace-root 与子命令级 --workspace-root 合并(merge_workspace_root_flags: 同义, 两处不同 = 拒);
    其后 环境变量 > 当前目录所在治理根 > 工位声明; 活动项目只给 governance_root_resolution.global_view_commands 所列命令; 否则拒
    (不回落 home 级活动项目)。announce_commands 所列命令打印首行「解析到的项目 + 来源」(--json 时写 stderr)。
    解析不出 / 冲突 = FileNotFoundError(拒因原文; 各调用方既有出口)。同一次调用内只解析、只标注一次。"""
    from tools.aipos_cli.workspace_config import governance_root_declaration, merge_workspace_root_flags, resolve_governance_root

    cached = getattr(args, "_governance_root_hit", None)
    if cached is not None:
        return cached["project_root"]
    decl = governance_root_declaration()
    command = getattr(args, "governance_root_command", None)
    try:
        explicit = merge_workspace_root_flags(getattr(args, "global_workspace_root", None), getattr(args, "workspace_root", None))
        hit = resolve_governance_root(explicit, established=False, allow_active_project=command in decl["global_view_commands"])
    except ValueError as exc:  # ProjectTargetError(拒因码) / 工位 connection.json 不可读(声明坏了不猜)
        raise FileNotFoundError(str(exc)) from exc
    args._governance_root_hit = hit
    if command in decl["announce_commands"]:
        line = decl["resolved_line"].format(project=hit["project"] or decl["unknown_project"], source=hit["source"])
        print(line, file=sys.stderr if getattr(args, "json", False) else sys.stdout, flush=True)
    return hit["project_root"]


def _run_board_command(args: argparse.Namespace) -> int:
    from web.board.app import run_server

    workspace_root = _resolve_workspace_for_command(args)
    defaults = _config_defaults(workspace_root)
    host = str(getattr(args, "host", None) or defaults["board_host"])
    port = int(getattr(args, "port", None) or defaults["board_port"])
    print(f"Lybra Board: http://{host}:{port}")
    print(f"Workspace: {workspace_root}")
    previous_root = os.environ.get(WORKSPACE_ROOT_ENV)  # AIPOS-F106 件③: 只写新名
    os.environ[WORKSPACE_ROOT_ENV] = str(workspace_root)
    try:
        run_server(host=host, port=port, repo_root=workspace_root)
    finally:
        if previous_root is None:
            os.environ.pop(WORKSPACE_ROOT_ENV, None)
        else:
            os.environ[WORKSPACE_ROOT_ENV] = previous_root
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
    previous_root = os.environ.get(WORKSPACE_ROOT_ENV)  # AIPOS-F106 件③: 只写新名
    os.environ[WORKSPACE_ROOT_ENV] = str(workspace_root)
    try:
        return run_http_server(config_from_env(host, port, keepalive))
    finally:
        if previous_root is None:
            os.environ.pop(WORKSPACE_ROOT_ENV, None)
        else:
            os.environ[WORKSPACE_ROOT_ENV] = previous_root


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
            WORKSPACE_ROOT_ENV: str(workspace_root),
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


def _project_freeze_legacy(args: argparse.Namespace) -> int:
    """AIPOS-F122 件②: `lybra project freeze-legacy` 薄壳——选卡干跑在 legacy_freeze、落条目在 legacy_baseline(唯一实现), 本处只解析参数与输出。
    缺省 = 干跑(零写入); --confirm 追加清单条目。未知卡号 / 清单不合 = 拒(exit 1, 零写入)。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, write_freeze_entry
    from tools.aipos_cli.legacy_freeze import plan_freeze_legacy

    # AIPOS-F127 件①: 目标治理根 = --workspace-root / 所在治理根(写命令同一解析), 禁回落 home 级活动项目
    target = _project_write_target(args, None, None)
    if target is None:
        return 1
    root = Path(target["project_root"])
    queues = [q.strip() for q in str(args.queue).split(",") if q.strip()] if args.queue else None
    if args.exclude and not queues:
        print("Error: --exclude 只与 --queue 合用", file=sys.stderr)
        return 2
    try:
        plan = plan_freeze_legacy(root, task_ids=args.task_ids, queues=queues, exclude=args.exclude, unfreeze=args.unfreeze)
        written = (write_freeze_entry(root, plan, reason=args.reason, actor=args.actor, owner_policy_ref=args.owner_policy_ref)
                   if args.confirm else None)
    except LegacyBaselineError as exc:
        print(f"Error: {exc}(freeze-legacy 零写入)", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(render_json({"ok": True, "confirm": bool(args.confirm), "plan": plan, "written": written}))
        return 0
    verb = "冻结" if plan["action"] == "freeze" else "解冻"
    mode = "confirm" if args.confirm else "dry-run"
    print(f"freeze-legacy ({mode}) {verb} {len(plan['selected'])} 张卡; 治理根 {plan['governance_root']}; "
          f"清单落点 {plan['manifest_dir']}{'' if plan['manifest_declared'] else '(project.json 未声明, confirm 时写入)'}")
    for item in plan["selected"]:
        print(f"  {item['task_id']}  queue={item['queue_state']}  status={item['card_status']}  lint_issues={item['lint_issues']}")
    print(f"  合计当前 lint 问题 {plan['lint_issues_total']} 条")
    if plan["already"]:
        state = "已冻结" if plan["action"] == "freeze" else "未冻结"
        print(f"  {state}(幂等, 不重复写): {', '.join(plan['already'])}")
    if plan["excluded"]:
        print(f"  排除: {', '.join(plan['excluded'])}")
    if written is None:
        print("  (dry-run 零写入; 确认后加 --confirm 追加清单条目)")
    elif written["written"]:
        print(f"✓ 已追加清单条目 {written['entry']}" + ("; 并在 project.json 声明 legacy_baseline 落点" if written["project_json_declared"] else ""))
        print("  卡文件与记录未被移动或改写; 清单条目属治理真相, 按项目惯例经 governance-commit 落账")
    else:
        print(f"  本批无需{verb}的卡(幂等), 未写清单")
    return 0


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


def _queue_gate_connection_json(args: argparse.Namespace, repo_root: Any) -> str | None:
    """queue claim --confirm / queue adopt 的凭据文件解析(唯一): --connection-json → 治理根 .lybra/connection.json → LYBRA_CONNECTION_JSON。"""
    explicit = getattr(args, "connection_json", None)
    if explicit:
        return str(explicit)
    default_conn = workspace_connection_path(Path(repo_root))
    if default_conn.exists():
        return str(default_conn)
    return os.environ.get("LYBRA_CONNECTION_JSON") or None


def render_adopt_summary(resp: dict[str, Any]) -> str:
    """AIPOS-F123 件①: `lybra queue adopt` 的人读摘要(门应答原样字段, 不另判)。"""
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    adoption = data.get("adoption") if isinstance(data.get("adoption"), dict) else {}
    mode = "预览(零写入; 加 --confirm 收编)" if resp.get("preview_only") else ("已收编" if resp.get("preauthorized_release") else "未收编")
    lines = [f"queue adopt {data.get('task_id') or ''}: {mode} verdict={resp.get('verdict')}"]
    if adoption:
        lines.append(f"  绑定: {adoption.get('adopted_branch')}@{adoption.get('adopted_branch_tip')} → 卡分支 {adoption.get('card_branch')}"
                     f"(adopted_from={adoption.get('adopted_from')}, adopted_by={adoption.get('adopted_by')})")
    if data.get("claimer"):
        lines.append(f"  认领实例(claim 记录 actor): {data.get('claimer')}")
    for key, label in (("claim_record_path", "claim 记录"), ("session_record_path", "session 记录"), ("worktree_path", "卡工作树落点")):
        if data.get(key):
            lines.append(f"  {label}: {data.get(key)}")
    envelope = resp.get("envelope") if isinstance(resp.get("envelope"), dict) else None
    if envelope is not None:
        lines.append(f"  信封: {'覆盖' if envelope.get('matched') else '不覆盖'} {envelope.get('policy_id') or ''}".rstrip())
    if resp.get("error_code"):
        lines.append(f"  ✗ {resp.get('error_code')}: {resp.get('message') or ''}; 出口: {resp.get('suggested_next_action') or ''}")
    for reason in resp.get("blocking_reasons") or []:
        lines.append(f"  ✗ {reason}")
    for warning in resp.get("warnings") or []:
        lines.append(f"  ! {warning}")
    return "\n".join(lines)


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
    return utc_now() > expires_at


def _execute_controlled_from_dry_run_envelope(
    repo_root: Any,
    envelope: dict[str, Any],
    actor: str,
    *,
    owner_confirmation_token: str | None = None,
) -> dict[str, Any]:
    operation = "controlled_execute_confirm"
    envelope_operation = str(envelope.get("operation") or "")
    if envelope_operation not in {"intake_submit", RecordType.OWNER_DECISION_RECORD}:
        return blocked_response(
            operation=operation,
            dry_run=False,
            category="UNSUPPORTED_OPERATION",
            message="controlled-execute confirm --from-json supports only intake_submit and owner_decision_record",
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
    else:
        current = record_owner_decision(payload, dry_run=True, repo_root=repo_root, actor=actor)
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
    else:
        result = build_owner_decision_record(repo_root, payload, actor=actor, dry_run=False)
        summary = {
            "decision_id": result.get("decision_id"),
            "target_path": result.get("target_path"),
            "wrote": result.get("wrote", False),
        }
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


# ---------------------------------------------------------------------------
# AIPOS-F92 件①: 信封签发产品化 —— `lybra envelope mint --confirm` 是门 owner_decision_record envelope 路径的薄壳
# (签信封逻辑只在门侧 owner_decision_writer 一处; 本 CLI 只构 payload、读凭据、两阶段转发、以门生记录为准输出)
# ---------------------------------------------------------------------------

def _envelope_mint_payload(
    *,
    policy_id: str,
    agent_or_role: str,
    max_tasks: int,
    task_mode: str | None,
    expires_at: str,
    decision_summary: str,
    actor: str,
    launch_harnesses: list[str] | None = None,
    lane_repo: str | list[str] | None = None,
) -> dict[str, Any]:
    """信封 payload 唯一构造(--dry-run 本地预演与 --confirm 门路径同读): 门 envelope 路径只要 decision_id + autonomy_policy。"""
    from tools.aipos_cli.workspace_config import repo_name_set

    task_selector: dict[str, Any] = {}
    if task_mode:
        task_selector["task_mode"] = task_mode
    lane_repos = repo_name_set(lane_repo)  # AIPOS-F139: 仓集合唯一解析(--lane-repo 可重复)
    if lane_repos:
        # AIPOS-F134 件③: lane 选择器(门 owner_decision_writer._normalize_autonomy_policy 按 resolve_card_repo 逐个校验); 缺省 = 不限 lane
        task_selector["lane_repo"] = lane_repos
    return {
        "decision_id": f"envelope-{policy_id}",
        "actor": actor,
        "decided_by_ref": actor,
        "decision_summary": decision_summary,
        "autonomy_policy": {
            "policy_id": policy_id,
            "agent_or_role": agent_or_role,
            "active_from": iso_z(),
            "expires_at": expires_at,
            "max_tasks": max_tasks,
            "task_selector": task_selector,
            # AIPOS-F95 件②(b): 拉起授权(缺省 [] = 只手工 /go); 门 owner_decision_writer 按 enums.schema harness.launch 校验
            "launch_harnesses": list(launch_harnesses or []),
        },
    }


def _envelope_mint_via_gate(
    payloads: list[dict[str, Any]],
    *,
    governance_root: Path,
    actor: str,
    connection_json: str | None,
    token_role: str,
    json_output: bool,
) -> int:
    """逐张经门两阶段签信封: lybra_owner_decision_record_dry_run(workspace_root=目标治理根, 门按凭据 projects 校验项目范围)
    → _confirm(owner_confirmation_token=OWNER_CONFIRMED; 门另要 owner_confirm scope)。凭据只从 connection.json 读, 只出指纹。
    任一张门拒 = 停在该张(已落的照实列出), exit 1。输出以门生记录为准(performed_writes)。"""
    from tools.aipos_cli.confirm_client import (
        GateAddressError,
        GateClient,
        GateError,
        load_owner_token,
        resolve_gate_base_url,
        token_fingerprint,
    )
    conn_path = workspace_connection_path(governance_root, connection_target=Path(connection_json) if connection_json else None)
    if not conn_path.is_file():
        print(f"Error: 凭据文件不存在: {conn_path}(--connection-json 指向持 {token_role} 凭据的 connection.json, 如门的中央凭据库)", file=sys.stderr)
        return 1
    try:
        token = load_owner_token(connection_json=conn_path, role=token_role)
    except (OSError, ValueError) as exc:
        print(f"Error: 读不到 {token_role} 凭据({conn_path}): {exc}", file=sys.stderr)
        return 1
    try:
        # AIPOS-F106 件①: 门基址唯一推导口(委托 ConnectionResolver.resolve_gate_url), 凭据文件须声明 mcp.rpc_url
        base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
    except GateAddressError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    signed: list[dict[str, Any]] = []
    report: dict[str, Any] = {"ok": False, "operation": "envelope_mint", "via": "gate owner_decision_record envelope path",
                              "gate_url": base_url, "credential_role": token_role, "credential_fingerprint": token_fingerprint(token),
                              "governance_root": str(governance_root), "signed": signed}

    def _emit(rc: int, error: str | None = None) -> int:
        if error:
            report["error"] = error
        if json_output:
            print(render_json(report))
        else:
            print(f"envelope mint --confirm via {base_url} (credential {token_role} {report['credential_fingerprint']}) → {governance_root}")
            for item in signed:
                print(f"  signed {item['policy_id']} covers {item['agent_or_role']}: decision={item['decision_id']}")
                for path in item["written"]:
                    print(f"    wrote {path}")
            if error:
                print(f"Error: {error}", file=sys.stderr)
        return rc

    try:
        client = GateClient(base_url, token)
        client.initialize()
        for payload in payloads:
            policy = payload["autonomy_policy"]
            dry = client.call_tool("lybra_owner_decision_record_dry_run", {**payload, "workspace_root": str(governance_root)})
            if dry.get("verdict") == Verdict.BLOCK or not dry.get("ok", False) or not dry.get("dry_run_token"):
                reasons = dry.get("blocking_reasons") or dry.get("errors") or dry.get("message") or dry
                return _emit(1, f"门拒 {policy['policy_id']} 预演: {json.dumps(reasons, ensure_ascii=False)[:800]}")
            done = client.call_tool("lybra_owner_decision_record_confirm", {
                "dry_run_token": dry["dry_run_token"],
                "actor": actor,
                "owner_confirmation_token": OWNER_CONFIRMATION_TOKEN,
                "workspace_root": str(governance_root),
            })
            if not done.get("ok", False):
                reasons = done.get("errors") or done.get("message") or done.get("error_code") or done
                return _emit(1, f"门拒 {policy['policy_id']} 确认: {json.dumps(reasons, ensure_ascii=False)[:800]}")
            writes = [str(w.get("path")) for w in (done.get("performed_writes") or []) if isinstance(w, dict) and w.get("path")]
            written_files = [governance_root / w for w in writes]
            missing = [str(f) for f in written_files if not f.is_file()]
            if not writes or missing:
                return _emit(1, f"门应答成功但记录未见落盘({policy['policy_id']}): writes={writes} missing={missing}")
            signed.append({"policy_id": policy["policy_id"], "agent_or_role": policy["agent_or_role"],
                           "decision_id": payload["decision_id"], "written": writes})
    except GateError as exc:
        return _emit(1, f"门调用失败: {exc}(已签 {len(signed)} 张, 以门生记录为准; 未签的重跑本命令, 已落的 policy_id 会被门拒「already exists」)")
    report["ok"] = True
    return _emit(0)


# ---------------------------------------------------------------------------
# AIPOS-F125: 写 project.json 的 CLI 命令(set-paths / set-repo / set-repos / set-workstation)同一两阶段——
# 声明 verbs.schema two_phase_protocol.project_json_writers 一处; 旗标注册与输出渲染只此一份包装(预演/diff 本体 =
# workspace_config.update_project_json dry_run)。缺省预演(diff + 校验, 零写入), --confirm 才写。
# ---------------------------------------------------------------------------

def _project_json_writers_declaration() -> dict[str, Any]:
    """verbs.schema two_phase_protocol.project_json_writers。缺键 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("verbs").get("two_phase_protocol") or {}).get("project_json_writers")
    phases = ("dry_run", "confirm")
    if (not isinstance(decl, dict) or not isinstance(decl.get("commands"), list) or not decl["commands"]
            or decl.get("default_phase") != "dry_run"
            or not all(isinstance((decl.get(k) or {}).get(p), str) and (decl.get(k) or {}).get(p) for k in ("flags", "flag_help") for p in phases)
            or not all(isinstance((decl.get("outcome_labels") or {}).get(k), str) for k in ("written", "unchanged", "preview"))
            or not all(f"{{{k}}}" in str(decl.get("target_line") or "") for k in ("project", "source", "project_json"))):
        raise SchemaLoadError("verbs.schema.json two_phase_protocol.project_json_writers(commands / default_phase=dry_run / "
                              "flags / flag_help {dry_run, confirm} / outcome_labels {written, unchanged, preview} / "
                              "target_line 含 {project} {source} {project_json})未声明齐")
    return decl


def _project_json_two_phase_flags(parser: argparse.ArgumentParser, command: str) -> None:
    """给写 project.json 的子命令注册互斥的 --dry-run(缺省)/ --confirm(旗标名与帮助读声明; 未登记的命令 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _project_json_writers_declaration()
    if command not in decl["commands"]:
        raise SchemaLoadError(f"verbs.schema.json two_phase_protocol.project_json_writers.commands 未登记 {command}")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(decl["flags"]["dry_run"], dest="dry_run", action="store_true", help=decl["flag_help"]["dry_run"])
    group.add_argument(decl["flags"]["confirm"], dest="confirm", action="store_true", help=decl["flag_help"]["confirm"])


def _project_write_target(args: argparse.Namespace, home: Path | None, name: str | None, *,
                          workspace_root: str | Path | None = None) -> dict[str, Any] | None:
    """AIPOS-F127 件①: project 族写命令目标项目解析的 CLI 薄壳——实现 = workspace_config.resolve_project_write_target(唯一);
    显式治理根 = workspace_root(命令自有旗标, 如 dispatch-mode --project-root) > 子命令 --workspace-root > 全局 --workspace-root。
    解析不出 / 冲突 / 显式项目未建(PROJECT_NOT_ESTABLISHED)= 打印拒因(含出口)返回 None(调用方 exit 1, 零写入)。"""
    from tools.aipos_cli.workspace_config import ProjectTargetError, resolve_project_write_target

    if workspace_root is None:
        workspace_root = getattr(args, "workspace_root", None) or getattr(args, "global_workspace_root", None)
    try:
        return resolve_project_write_target(home, name, workspace_root=workspace_root)
    except (ProjectTargetError, FileNotFoundError) as exc:
        print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
        return None


def _project_json_two_phase_emit(command: str, target: dict[str, Any], outcome: dict[str, Any], *, json_out: bool,
                                 details: list[str] | tuple[str, ...] = (), extra: dict[str, Any] | None = None,
                                 via: str = "") -> None:
    """写 project.json 的命令同一输出: 首行 = 目标项目名 + 来源 + project.json 绝对路径(AIPOS-F127 件②, 文案读声明 target_line),
    次行状态(已写入 / 无改动 / 预览)+ 命令自述的改动行 + project.json unified diff。target = workspace_config.resolve_project_write_target
    的结果(各命令目标解析同一实现)。只涉 project.json(update_project_json 的结果), 不读不印 connection.json / 凭据。--json = 同一内容的 JSON。"""
    decl = _project_json_writers_declaration()
    labels = decl["outcome_labels"]
    project_name = target["project"]
    if json_out:
        payload = {"ok": True, "command": command, "project": project_name, "target_source": target["source"],
                   **(extra or {}), **outcome}
        print(render_json({k: (str(v) if isinstance(v, Path) else v) for k, v in payload.items()}))
        return
    print(decl["target_line"].format(project=project_name, source=target["source"],
                                     project_json=Path(outcome["project_json"]).resolve()))
    mode = labels["written"] if outcome["written"] else (labels["unchanged"] if not outcome["changed"] else labels["preview"])
    print(f"project {command} {project_name}: {mode}{via} {outcome['project_json']}")
    for line in details:
        print(f"  {line}")
    if outcome["diff"]:
        print(outcome["diff"].rstrip("\n"))


def build_parser() -> argparse.ArgumentParser:
    from tools.aipos_cli.machine_zone import add_lane_argument  # AIPOS-F133 件②: --lane 声明 verbs.schema lane_view
    # AIPOS-F106 件②: 帮助文案里的门地址示例读 config.schema urls.gate_local(唯一读取口 schema_loader), 禁写端口字面
    from tools.schema_loader import get_config_default_gate_url
    from tools.aipos_cli.verb_contract import declared_exit_codes  # AIPOS-F101 件③: help 中的退出码读声明

    _GATE_URL_DEFAULT = get_config_default_gate_url()
    parser = argparse.ArgumentParser(description="AI Project OS CLI")
    parser.add_argument("--workspace-root", dest="global_workspace_root", help="Workspace root; may also be provided on supported subcommands")
    subparsers = parser.add_subparsers(dest="command")

    # `agent watch --workspace-root`(AIPOS-268 文件系统哨兵): loop 唯一等待原语, 纯客户端只读(无门/凭据)。
    # AIPOS-F103 件①: 旧跨机连接器(AIPOS-248 门拉取 / AIPOS-363 材料化与回推)与执行体派工命令整体退役
    # (Owner 10-04 裁定: 执行体自认领自确认违执行体零门); 子命令不再注册 = argparse 报不存在。
    agent_parser = subparsers.add_parser(
        "agent",
        help="Agent-side filesystem sentinel: `agent watch --workspace-root` (pure client, read-only, no gate/token)",
    )
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command")
    _watch_exit = declared_exit_codes("lybra_agent_watch")  # AIPOS-F101 件③: 本节 help 的退出码一律读声明
    _watch_parser = agent_subparsers.add_parser(
        "watch",
        help="Foreground BOUNDED filesystem mtime sentinel over 5_tasks/queue/** + 5_tasks/records/** "
        "(AIPOS-268+284+284C+284D; any bash agent, no gate). "
        # AIPOS-F101 件③: 退出码从唯一声明 verbs.schema lybra_agent_watch.exit_codes 渲染, 禁写死
        + "Exit codes (verbs.schema lybra_agent_watch.exit_codes): "
        + ", ".join(f"{code}={outcome}" for outcome, code in _watch_exit.items())
        + ". Stream mode (--stream): emits 'kind:end' event before timeout/signal exit.",
    )
    _watch_parser.add_argument(
        "--workspace-root",
        required=True,
        help="governance root to watch (AIPOS-268+284): poll 5_tasks/queue/** + 5_tasks/records/** mtime+path; "
        "print a JSON change summary on the first change; silent on --timeout (exit codes: verbs.schema lybra_agent_watch.exit_codes). No gate/token.",
    )
    _watch_parser.add_argument("--interval", type=float, default=None, help="poll interval seconds (default 15)")
    _watch_parser.add_argument("--timeout", type=float, default=None, help=f"[filesystem pump] no-change timeout seconds -> silent exit {_watch_exit['timeout']} (default: 1800 for default mode, infinite for --stream mode; 0 = explicit infinite)")
    # AIPOS-284 v2: three "death silence" semantics
    _watch_parser.add_argument("--expect", action="append", help=f"[filesystem pump v2] glob pattern for expected artifact; check immediately on startup and every poll (布防即检). Can be repeated. Exit {_watch_exit['change']} when any match.")
    _watch_parser.add_argument("--run-log", help="[filesystem pump v2] path to run log (for end-pattern and stall detection)")
    _watch_parser.add_argument("--end-pattern", help=f"[filesystem pump v2] regex: if found in run-log but --expect NOT satisfied, exit {_watch_exit['end_no_product']} after one grace poll (结束无产物)")
    _watch_parser.add_argument("--stall-secs", type=float, default=None, help=f"[filesystem pump v2] silence threshold seconds (default 600). If run-log (or observation surface) mtime unchanged for ≥N seconds, exit {_watch_exit['stall']} (静默停滞)")
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
    tui_parser.add_argument("--gate-url", required=True, help=f"Owner-started gate URL (e.g. {_GATE_URL_DEFAULT})")
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


    draft_parser = subparsers.add_parser("draft", help="Safe task draft writer")
    draft_subparsers = draft_parser.add_subparsers(dest="draft_command")

    draft_create_parser = draft_subparsers.add_parser("create", help="Create a draft task card")
    create_source_group = draft_create_parser.add_mutually_exclusive_group(required=True)
    create_source_group.add_argument("--from-json", help="Read draft payload from JSON file")
    create_source_group.add_argument("--from-template", help="Create from a built-in template")
    draft_create_parser.add_argument("--task-id", help="Draft task_id")
    draft_create_parser.add_argument("--title", help="Draft title")
    draft_create_parser.add_argument("--project", help="Project(缺省 = 治理根 project.json#project; 未声明 = 拒, AIPOS-F108)", default=None)
    draft_create_parser.add_argument("--assigned-to", help="assigned_to value")
    draft_create_parser.add_argument("--agent-instance", help="agent_instance value")
    draft_create_parser.add_argument("--context-bundle", help="context_bundle value")
    draft_create_parser.add_argument("--task-mode", help="task_mode value")
    from tools.aipos_cli.task_complexity import ALLOWED_TASK_CLASSES  # AIPOS-F104 件④: 值域 = enums.schema task_class(唯一读取口)

    draft_create_parser.add_argument("--task-class", choices=ALLOWED_TASK_CLASSES, help="task_class value (enums.schema task_class)")
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

    # AIPOS-F74 件②: draft regen-machine-zone subcommand
    draft_regen_parser = draft_subparsers.add_parser("regen-machine-zone", help="AIPOS-F74: Regenerate machine zone for pending cards")
    draft_regen_parser.add_argument("--task-id", help="Task ID to regenerate (if omitted, process all pending cards)")
    draft_regen_parser.add_argument("--actor", required=True, help="Actor performing the regeneration")
    draft_regen_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    draft_regen_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_parser = subparsers.add_parser("queue", help="Render task queue")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")
    queue_parser.add_argument("--json", action="store_true", help="Output JSON")

    sync_parser = subparsers.add_parser("sync", help="AIPOS-C4B: worker-initiated distribution pull (lybra sync); AIPOS-F66B: 按工位项目归属过滤 + 章程声明渲染 + --dry-run")
    sync_parser.add_argument("--harness-root", default=None, help="Harness root: 工位根或工位父根(逐工位按项目归属过滤)(REQUIRED: no cwd guessing; fallback env LYBRA_HARNESS_ROOT)")
    sync_parser.add_argument("--workspace-root", default=None, help="AIPOS-F66B: 治理根(project.json): 定 sync 项目范围 + 章程渲染声明; 多工位必给(或 --project)")
    sync_parser.add_argument("--project", default=None, help="AIPOS-F66B: sync 项目范围(非本项目工位跳过并在 manifest 记 skipped)")
    sync_parser.add_argument("--dry-run", action="store_true", help="AIPOS-F66B: 零写入, 列 would-fetch/would-render/would-prune/skipped")
    sync_parser.add_argument("--gate-url", default=None, help="Gate MCP URL (auto from .lybra if omitted)")
    sync_parser.add_argument("--token", default=None, help="Bearer token (auto from .lybra connection.json if omitted)")
    sync_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_claim_parser = queue_subparsers.add_parser("claim", help="Move a task from pending to claimed")
    _queue_mutation_arguments(queue_claim_parser)
    queue_claim_parser.add_argument("--confirm", action="store_true", help="AIPOS-F22: Two-phase gate claim (dry_run + confirm via薄壳工厂, executor self-confirm)")
    queue_claim_parser.add_argument("--connection-json", help="Path to connection.json (for --confirm gate access)")
    queue_claim_parser.add_argument("--agent-instance", help="Agent instance (for --confirm)")
    queue_claim_parser.add_argument("--owner-policy-ref", help="Owner policy ref (for --confirm)")
    queue_claim_parser.add_argument("--autonomy-mode", default="Supervised", help="Autonomy mode (for --confirm)")
    queue_claim_parser.add_argument("--active-session-id", help="Active session ID (for --confirm)")

    # AIPOS-F123 件①: 人肉期在途卡收编(claim 动词族, 经门 lybra_queue_adopt_dry_run; 声明 transitions nodes.N1.adoption)
    queue_adopt_parser = queue_subparsers.add_parser("adopt", help="AIPOS-F123: adopt an in-flight legacy card (in claimed/, no gate-born claim record) — the gate mints its claim record bound to an existing branch and builds the card worktree; --dry-run (default) previews, --confirm writes (one-stage envelope)")
    queue_adopt_parser.add_argument("--task-id", required=True, help="Card task_id")
    queue_adopt_parser.add_argument("--branch", required=True, help="Existing branch in the card's lane.repo holding the in-flight work")
    queue_adopt_parser.add_argument("--actor", required=True, help="Driver instance submitting the adoption (recorded as adopted_by)")
    queue_adopt_parser.add_argument("--owner-policy-ref", required=True, help="Envelope policy_id covering the driver and this card (project.json paths.policies_root)")
    queue_adopt_mode = queue_adopt_parser.add_mutually_exclusive_group()
    queue_adopt_mode.add_argument("--dry-run", action="store_true", help="Preview through the gate with zero writes (default)")
    queue_adopt_mode.add_argument("--confirm", action="store_true", help="Adopt (one-stage PreAuthorized release under the envelope)")
    queue_adopt_parser.add_argument("--connection-json", help="Path to connection.json (driver token)")
    queue_adopt_parser.add_argument("--json", action="store_true", help="Output JSON")

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
    queue_amend_parser.add_argument("--restricted", action="store_true", help="AIPOS-F78 前置零⑤: advisor 对 claimed 卡受限 amend(允许字段读 card.schema restricted_amend.claimed_card_fields: rework_rounds/output_target/lane; 复用 F75 restricted amend)")
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

    # AIPOS-F65C 件①: queue repair subcommand (repair bad frontmatter YAML)
    queue_repair_parser = queue_subparsers.add_parser("repair", help="AIPOS-F65C: Repair bad frontmatter in task card (minimal fix for unparseable YAML)")
    queue_repair_parser.add_argument("--task-id", required=True, help="Task ID to repair")
    queue_repair_parser.add_argument("--actor", required=True, help="Actor performing the repair")
    queue_repair_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_repair_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-1: queue return subcommand (same-machine task return)
    queue_return_parser = queue_subparsers.add_parser("return", help="Return completed task (same-machine)")
    queue_return_parser.add_argument("--task-id", required=True, help="Task ID to return")
    queue_return_parser.add_argument("--actor", required=True, help="Actor returning the task")
    queue_return_parser.add_argument("--agent-instance", required=True, help="Agent instance name")
    queue_return_parser.add_argument("--result-summary", required=True, help="Result summary")
    queue_return_parser.add_argument("--owner-policy-ref", required=True, help="Owner policy reference")
    queue_return_parser.add_argument("--autonomy-mode", default="Supervised", help="AIPOS-F78B 件③: PreAuthorized=驱动方 token 经信封一阶段落记录(--owner-policy-ref=覆盖驱动方的信封); 缺省 Supervised")
    queue_return_parser.add_argument("--artifact-refs", help="JSON array of artifact references")
    queue_return_parser.add_argument("--completion-report-ref", help="Completion report reference")
    queue_return_parser.add_argument("--actual-model", help="AIPOS-F78 件③: 执行体实际模型自报(来自 Return frontmatter.model, 透传 verbs.schema actual_model)")
    queue_return_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_return_parser.add_argument("--confirm", action="store_true", help="AIPOS-F33: Two-step gate return (dry_run + confirm via MCP, executor self-confirm). Thin shell over same gate verbs as /lybra return and tryAutoReturn.")
    queue_return_parser.add_argument("--connection-json", help="Path to connection.json (for --confirm gate access)")
    queue_return_parser.add_argument("--active-session-id", help="Active session ID (for --confirm)")
    queue_return_parser.add_argument("--agent-runtime", default=None, help="AIPOS-F90 件②: 产品填写的运行时模型 bundle(JSON: harness/model/model_source/model_self_reported/model_mismatch), 由 artifact ingest 按会话记录定位器生成")
    queue_return_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-C1 大项A: queue close subcommand (derived from verbs.schema lybra_queue_close)
    queue_close_parser = queue_subparsers.add_parser("close", help="Close a claimed task with closure evidence (AIPOS-283)")
    queue_close_parser.add_argument("--task-id", required=True, help="Task ID to close")
    queue_close_parser.add_argument("--actor", required=True, help="Actor performing the close")
    queue_close_parser.add_argument("--closure-evidence", required=True, help="JSON object with at least one of: finalize_commit_hash, finalize_return_ref, owner_verification_ref")
    queue_close_parser.add_argument("--conclusion-note", help="AIPOS-F78 前置零⑨: 结案说明(含承接声明如「由续卡 <ID> 承接」时登记承接世系, F53 lineage 读此字段)")
    queue_close_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_close_parser.add_argument("--confirm", action="store_true", help="AIPOS-F78B 件③: 经门 MCP(lybra_queue_close_dry_run/confirm, 两阶段薄壳工厂)而非本地 writer; 驱动方 token")
    queue_close_parser.add_argument("--connection-json", help="Path to connection.json (for --confirm gate access)")
    queue_close_parser.add_argument("--autonomy-mode", default="Supervised", help="AIPOS-F78B 件③: PreAuthorized=驱动方 token 经信封一阶段落记录(--owner-policy-ref=覆盖驱动方的信封)")
    queue_close_parser.add_argument("--owner-policy-ref", help="PreAuthorized 时必填: 覆盖驱动方的信封 policy_id")
    queue_close_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F73C件⑤: queue rework subcommand (顾问追加返工节到卡面)
    queue_rework_parser = queue_subparsers.add_parser("rework", help="AIPOS-F75: Add rework round to task card (advisor only)")
    queue_rework_parser.add_argument("--task-id", required=True, help="Task ID to add rework round")
    queue_rework_parser.add_argument("--actor", required=True, help="Actor (advisor) adding rework round")
    queue_rework_parser.add_argument("--verdict-ref", required=True, help="Reference to FAIL verdict (audit report path or verdict_id)")
    queue_rework_parser.add_argument("--focus-items", required=True, help="JSON array of focus items (what to fix)")
    queue_rework_parser.add_argument("--acceptance-criteria", required=True, help="JSON array of acceptance criteria")
    queue_rework_parser.add_argument("--connection-json", help="Path to connection.json (for MCP gate access)")
    queue_rework_parser.add_argument("--confirm", action="store_true", help="Execute two-phase rework (dry_run + confirm via MCP)")
    queue_rework_parser.add_argument("--json", action="store_true", help="Output JSON")

    my_tasks_parser = subparsers.add_parser("my-tasks", help="Render tasks for an actor")
    my_tasks_parser.add_argument("--actor", default=None, help="Role instance or agent instance(与 --workstation 二选一)")
    my_tasks_parser.add_argument(
        "--workstation", default=None,
        help="AIPOS-F89 件③b: 工位目录——产品经 charter_render.workstation_identity 解析本工位实例(.lybra/role)与治理根"
             "(connection.json#governance_root 等, resolve_workstation_governance_root), 工位 /go 只传本参数, 不自读身份文件",
    )
    my_tasks_parser.add_argument("--task-id", default=None, help="AIPOS-F90 件③: 冷启动指向的卡(卡号或卡文件路径)——只核验这一张: 非 claimed/非本实例/已结案/产物已交即拒并给原因(工位 /go <卡号> 用)")
    my_tasks_parser.add_argument("--remote-workstation", action="store_true", default=False,
                                 help="AIPOS-F110 件②: 本实例工位跨机(land 事件 host ≠ 门机): next_card.kickoff 附门机材料段(project.json workstations.<实例>; 未声明 = 不出开工提示); lybra loop 拉起跨机工位时传")
    my_tasks_parser.add_argument("--json", action="store_true", help="Output JSON")

    needs_owner_parser = subparsers.add_parser("needs-owner", help="Render owner review tasks")
    needs_owner_parser.add_argument("--json", action="store_true", help="Output JSON")
    add_lane_argument(needs_owner_parser)  # AIPOS-F133 件②: 缺省按 lane 分组

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

    workspace_parser = subparsers.add_parser("workspace", help="Workspace root queries (read-only; create projects with `lybra onboarding guide` + `lybra project new`)")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command")
    # AIPOS-F88 件③: 根路径只读查询(两命名函数 + home 根的唯一出口; bash 调用方 lybra-deploy / governance-pre-commit 读此输出, 禁再写死)
    workspace_roots_parser = workspace_subparsers.add_parser(
        "roots", help="Show resolved governance workspace / product repo / home roots (read-only, AIPOS-F88)")
    workspace_roots_parser.add_argument(
        "--field", choices=["governance_root", "product_repo", "code_repo", "schema_dir", "home_root"],
        help="Print only this field's value (plain text, for shell command substitution); unresolvable = exit 1")
    workspace_roots_parser.add_argument("--json", action="store_true", help="Output JSON")

    records_parser = subparsers.add_parser("records", help="Render records summary")
    records_parser.add_argument("--json", action="store_true", help="Output JSON")

    state_parser = subparsers.add_parser("state", help="Read-only state recovery and provenance previews")
    state_subparsers = state_parser.add_subparsers(dest="state_command")
    # AIPOS-C3B 大项C①: state lint — 卡状态三方一致 lint(队列目录×frontmatter status×records)
    lint_parser = state_subparsers.add_parser("lint", help="AIPOS-C3B: 卡状态三方一致 lint(队列目录×frontmatter×records)")
    lint_parser.add_argument("--task-id", default=None, help="Limit to specific task ID")
    lint_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-C3B 大项C③: state repair — 按 records 重建卡一致状态
    repair_parser = state_subparsers.add_parser("repair", help="AIPOS-C3B: 按 records 重建卡一致状态(坏卡修复)")
    repair_parser.add_argument("--task-id", required=True, help="Task ID to repair")
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
    harness_choices = get_enum_values("harness")  # AIPOS-F78 件①: 执行引擎值域(enums.schema harness)
    
    audit_verdict_parser = subparsers.add_parser("audit-verdict", help="Submit audit verdict for a reviewed task (via gate MCP)")
    audit_verdict_parser.add_argument("--audit-task-id", help="Audit task ID (optional)")
    audit_verdict_parser.add_argument("--reviewed-task-id", required=True, help="Reviewed task ID")
    audit_verdict_parser.add_argument("--actor", help="AIPOS-R4B-2: Actor submitting verdict (auto-discovered if not provided)")
    audit_verdict_parser.add_argument("--agent-instance", help="AIPOS-R4B-2: Agent instance (auto-discovered if not provided)")
    audit_verdict_parser.add_argument("--owner-policy-ref", help="AIPOS-R4B-2: Owner policy reference (auto-discovered if not provided)")
    audit_verdict_parser.add_argument("--autonomy-mode", default="Supervised", help="AIPOS-F78B 件③: PreAuthorized=驱动方 token 经信封一阶段落记录(--owner-policy-ref=覆盖驱动方的信封); 缺省 Supervised")
    audit_verdict_parser.add_argument("--verdict", required=True, choices=verdict_choices, help="Verdict (from enums.schema.json)")
    audit_verdict_parser.add_argument("--findings-summary", help="Findings summary")
    audit_verdict_parser.add_argument("--evidence-refs", help="JSON list of evidence references")
    audit_verdict_parser.add_argument("--audit-claim-id", help="Audit claim ID")
    audit_verdict_parser.add_argument("--audit-session-id", help="Audit session ID")
    audit_verdict_parser.add_argument("--audit-dispatch-record-ref", help="Audit dispatch record reference")
    audit_verdict_parser.add_argument("--reviewed-return-record-ref", help="Reviewed return record reference")
    audit_verdict_parser.add_argument("--recommended-next-action", help="Recommended next action")
    audit_verdict_parser.add_argument("--owner-waiver-ref", help="Owner waiver reference")
    audit_verdict_parser.add_argument("--artifact-subject-repository", help="AIPOS-F73前置②: Repository identifier for artifact_subject (required for code tasks)")
    audit_verdict_parser.add_argument("--artifact-subject-commit-sha", help="AIPOS-F73前置②: Commit SHA for artifact_subject (required for code tasks)")
    audit_verdict_parser.add_argument("--artifact-subject-tree-hash", help="AIPOS-F73前置②: Tree hash for artifact_subject (required for code tasks)")
    audit_verdict_parser.add_argument("--agent-runtime", default=None, help="AIPOS-F90 件②: 产品填写的运行时模型 bundle(JSON: harness/model/model_source/model_self_reported/model_mismatch), 由 artifact ingest 按会话记录定位器生成")
    audit_verdict_parser.add_argument("--confirm", action="store_true", help="AIPOS-F22: Two-phase gate verdict (dry_run + confirm via薄壳工厂, auditor self-confirm)")
    audit_verdict_parser.add_argument("--gate-url", default=None, help=f"Gate MCP server URL (default: workspace connection.json mcp.rpc_url, else {_GATE_URL_DEFAULT})")
    audit_verdict_parser.add_argument("--connection-json", help="Path to connection.json (default: .lybra/connection.json in workspace)")
    audit_verdict_parser.add_argument("--token-role", default=None, help="Token role in connection.json (AIPOS-F78 前置零①: 缺省=roles.schema driver.role_class 的驱动方 token; 显式指定仅供靶场/人肉 gate)")
    audit_verdict_parser.add_argument("--json", action="store_true", help="Output JSON")

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
    roles_enroll_code_parser.add_argument("--gate-url", help=f"Externally reachable gate URL to embed (default: connection.json mcp.rpc_url if non-loopback, else {_GATE_URL_DEFAULT})")
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
    # AIPOS-F107 件②: 只读诊断——实例接入事件实际所在 log 与应在(所属项目)log
    roles_enroll_where_parser = roles_subparsers.add_parser("enroll-where", help="AIPOS-F107: 只读诊断某实例接入事件(create/use/land)实际所在 enrollment_log 与应在(所属项目)log, 供重接入判断")
    roles_enroll_where_parser.add_argument("--instance", required=True, help="实例名(<prefix>.<project>.<host>)")
    roles_enroll_where_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F121 件②: 接入日志作废(只追加 void 事件行, 不删行; 读取口忽略被作废事件)
    roles_enroll_void_parser = roles_subparsers.add_parser("enroll-void", help="AIPOS-F121: 作废某实例的接入事件——向事件所在 enrollment_log 追加 void 行(指向被作废行号区间/code_id, 带操作者与理由), 不删行; workstation_location/enroll-where 忽略被作废事件。先 --dry-run 看将追加的行")
    roles_enroll_void_parser.add_argument("--instance", required=True, help="要作废其接入事件的实例名(未知实例拒)")
    roles_enroll_void_parser.add_argument("--reason", required=True, help="作废理由(单行, 写入 void 行)")
    roles_enroll_void_parser.add_argument("--actor", required=True, help="操作者(写入 void 行 by=; 不含空白)")
    roles_enroll_void_parser.add_argument("--dry-run", action="store_true", help="只打印将追加的 void 行, 零写入")
    roles_enroll_void_parser.add_argument("--json", action="store_true", help="Output JSON")
    
    # AIPOS-F66B 件②: 写权限边界可读面 + 读取口(护栏读声明; 单源 roles.schema write_boundary)
    roles_wb_parser = roles_subparsers.add_parser("write-boundary", help="AIPOS-F66B: 写权限边界可读面(角色类 × 面 × read/append/mutate, 读 roles.schema write_boundary)与单次访问判定(--check)")
    roles_wb_parser.add_argument("--role", help="只出该角色行(内建或门注册表自定义角色, 按类展开)")
    roles_wb_parser.add_argument("--instance", help="按 enrollment 记录的实例反查角色")
    roles_wb_parser.add_argument("--harness-root", help="工位根(解析 harness_root 面; 缺省不列)")
    roles_wb_parser.add_argument("--check", metavar="PATH", help="判一次访问: 路径(绝对或相对治理根)")
    roles_wb_parser.add_argument("--level", choices=["read", "append", "mutate"], default="read", help="--check 的请求级别(缺省 read)")
    roles_wb_parser.add_argument("--task-id", help="--check 的卡 ID(per_task 面 / product_repo lane 判定需要)")
    roles_wb_parser.add_argument("--markdown", action="store_true", help="输出章程渲染节(需 --role)")
    roles_wb_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-R2: enroll command (client-side enrollment: exchange code + write .lybra/ config)
    roles_enroll_parser = roles_subparsers.add_parser("enroll", help="AIPOS-R2/F23: enroll this workstation (exchange enrollment code + write .lybra/ config). Self-contained code carries gate URL; run from the workstation directory")
    roles_enroll_parser.add_argument("--code", required=True, help="Enrollment code (self-contained LYBRAENROLL1.* from owner/advisor, or legacy plain code)")
    roles_enroll_parser.add_argument("--gate-url", help="Gate MCP URL override (self-contained codes embed it; legacy plain codes require this or LYBRA_GATE_URL)")
    roles_enroll_parser.add_argument("--workspace", help="Workstation root (defaults to current directory — must be the workstation dir, NOT the governance workspace)")
    roles_enroll_parser.add_argument("--policy", help="Optional policy reference")
    roles_enroll_parser.add_argument("--bootstrap-token", help="Legacy plain codes only: bootstrap token for HTTP transport auth (self-contained codes need none)")
    roles_enroll_parser.add_argument("--verify", action="store_true", help="AIPOS-R6S 大项C②: enroll 后立刻用新 token 调一次 gate, 不通即报错并回滚")
    roles_enroll_parser.add_argument("--harness", help="AIPOS-F92/F129: workstation harness kind (legal values = distribution.schema harness_semantics.kinds; default pi; unknown = refused with the declared list). claude-code = advisor Claude Code session: credentials land in --workspace, declared skills land in --harness-dir/.claude/skills; codex = advisor Codex session (may run on another machine): credentials land in --workspace, no .pi wiring, no skills delivered")
    roles_enroll_parser.add_argument("--harness-dir", help="AIPOS-F92/F129: absolute working directory of a non-pi harness (claude-code: required, must exist here; codex: optional, recorded as given — not checked locally when --harness-host is set)")
    roles_enroll_parser.add_argument("--harness-host", help="AIPOS-F129: host the harness session runs on when it is not this machine (only kinds declaring harness_host=optional, e.g. codex); recorded in .lybra/role harness.host")
    from tools.aipos_cli.enrollment import executor_mode_declaration as _executor_mode_declaration  # AIPOS-F143 件④: 取值读声明
    roles_enroll_parser.add_argument("--executor-mode", choices=list(_executor_mode_declaration()["values"]), default=None,
                                     help="AIPOS-F143: executor enrollment mode (roles.schema executor_modes; default workstation = existing behavior). "
                                          "subagent = advisor-spawned sub-agent executor (ROLES 1a): credentials land in the governance root's "
                                          ".lybra/connection.json (--workspace = governance root), --harness = the advisor session kind "
                                          "(claude-code|codex), --harness-host = the advisor session host if not this machine; executor class only "
                                          "(auditors are always independent pi workstations)")
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
    project_new_parser.add_argument("--owner-authorization-ref", help="Owner authorization ref — required only on the gate path (when ~/.lybra/connection.json exists); the local scaffold (AIPOS-226 ruling 2=a) needs none")
    project_setrepo_parser = project_subparsers.add_parser("set-repo", help="Set/update an established project's code_repo mapping; --dry-run (default) shows the project.json diff, --confirm writes")
    project_setrepo_parser.add_argument("name", help="Established project name")
    project_setrepo_parser.add_argument("--code-repo", required=True, help="Absolute path to the project's code repo")
    project_setrepo_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setrepo_parser.add_argument("--actor", default=default_actor, help="Provenance actor (registered_by); defaults to $USER or owner")
    project_setrepo_parser.add_argument("--owner-authorization-ref", help="Owner authorization ref — required only on the gate path (when ~/.lybra/connection.json exists)")
    _project_json_two_phase_flags(project_setrepo_parser, "set-repo")  # AIPOS-F125: 缺省预演 / --confirm 才写
    # AIPOS-F92 件②: 多仓声明(project.json repos {default, items} + code_repo 别名), 经 config.schema project_json.repos 声明校验
    project_setrepos_parser = project_subparsers.add_parser("set-repos", help="AIPOS-F92: declare the project's product repos (project.json repos {default, items} + code_repo alias), validated against config.schema project_json.repos; --dry-run (default) shows the diff, --confirm writes")
    project_setrepos_parser.add_argument("name", help="Established project name")
    project_setrepos_parser.add_argument("--repo", action="append", required=True, metavar="NAME=ABS_PATH", help="Product repo entry (repeatable): <仓名>=<绝对路径>")
    project_setrepos_parser.add_argument("--default", dest="default_repo", help="Default repo name (must be one of --repo names; required when more than one --repo)")
    project_setrepos_parser.add_argument("--no-deploy", dest="no_deploy", action="append", default=[], metavar="NAME", help="AIPOS-F135: declare repo NAME (one of --repo names; repeatable) as not deployed — finalize skips deployment and records deploy_status=not_applicable (project.json repos.no_deploy; set-repos is a whole-section declaration, omitted = cleared)")
    project_setrepos_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setrepos_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setrepos_parser, "set-repos")  # AIPOS-F125: 缺省预演 / --confirm 才写
    # AIPOS-F123 件②: project.json paths 落点声明写入口(键/值按 config.schema project_json.paths 校验; 与 set-repos 同一写路径与锁)
    project_setpaths_parser = project_subparsers.add_parser("set-paths", help="AIPOS-F123: declare project.json paths.<key> (repeatable --key/--value pairs), validated against config.schema project_json.paths; --dry-run (default) shows the diff, --confirm writes")
    project_setpaths_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory; never the home-level active project — AIPOS-F127)")
    project_setpaths_parser.add_argument("--key", action="append", dest="path_keys", required=True, metavar="KEY", help="paths key (repeatable, paired in order with --value)")
    project_setpaths_parser.add_argument("--value", action="append", dest="path_values", required=True, metavar="VALUE", help="value for the --key at the same position")
    _project_json_two_phase_flags(project_setpaths_parser, "set-paths")  # AIPOS-F125: 两阶段旗标同一声明(原 F123 本地注册收归)
    project_setpaths_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setpaths_parser.add_argument("--workspace-root", help="AIPOS-F127: target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setpaths_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F127 件③: project.json 顶层说明键(phase / note)写入口; 旗标 → 键登记读 verbs.schema project_json_writers.meta.keys
    from tools.aipos_cli.workspace_config import project_meta_declaration

    project_setmeta_parser = project_subparsers.add_parser("set-meta", help="AIPOS-F127: set project.json top-level description keys (phase / note; each must be declared in config.schema project_json); --dry-run (default) shows the diff, --confirm writes")
    project_setmeta_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory; never the home-level active project)")
    for _meta_key, _meta_flag in project_meta_declaration()["keys"].items():
        project_setmeta_parser.add_argument(_meta_flag, dest=f"meta_{_meta_key}", default=None, metavar="TEXT",
                                            help=f"project.json {_meta_key} (single line)")
    project_setmeta_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setmeta_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setmeta_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setmeta_parser, "set-meta")  # 缺省预演 / --confirm 才写(与四个写命令同一包装)
    # AIPOS-F143 件②: 项目执行方式声明写入口(project.json execution; 声明 config.schema project_json.execution; update_project_json 唯一写路径)
    project_setexec_parser = project_subparsers.add_parser("set-execution", help="AIPOS-F143: declare the project's execution mode (project.json execution: sub-agent executor by default, Owner-approved task modes may go to a pi executor workstation, audits always by an independent pi auditor); every instance must already be enrolled in this project; --dry-run (default) shows the diff, --confirm writes")
    project_setexec_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_setexec_parser.add_argument("--subagent-executor", required=True, metavar="INSTANCE", help="Sub-agent executor instance (enrolled with lybra roles enroll --executor-mode subagent); default assigned_to of drafted cards")
    project_setexec_parser.add_argument("--auditor", required=True, metavar="INSTANCE", help="Independent pi auditor workstation instance (must have a workstation location); default audit_by of drafted cards")
    project_setexec_parser.add_argument("--pi-executor", default=None, metavar="INSTANCE", help="pi executor workstation instance (required when --pi-allowed-task-mode is given)")
    project_setexec_parser.add_argument("--pi-allowed-task-mode", action="append", dest="pi_allowed_task_modes", default=[], metavar="TASK_MODE", help="task_mode the Owner explicitly allows to go to the pi executor (repeatable; enums.schema task_mode); unlisted = sub-agent default")
    project_setexec_parser.add_argument("--default-mode", default=None, help="Default execution mode (config.schema project_json.execution.default_mode enum; default subagent)")
    project_setexec_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setexec_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setexec_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setexec_parser, "set-execution")  # 缺省预演 / --confirm 才写(同一包装)
    # AIPOS-F148 件②: Return 摘要来源声明写入口(project.json return_summary_source; 声明 config.schema project_json.return_summary_source;
    # 缺省 transitions artifact_ingest.return.summary_source; 唯一实现 workspace_config.set_return_summary_source)
    project_setsummary_parser = project_subparsers.add_parser("set-return-summary", help="AIPOS-F148: declare where this project's Return carries its one-line summary (project.json return_summary_source: Return frontmatter keys and/or body section markers; unset keys fall back to transitions.schema artifact_ingest.return.summary_source, default = the 一句话结论 section); --dry-run (default) shows the diff, --confirm writes")
    project_setsummary_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_setsummary_parser.add_argument("--frontmatter-key", action="append", dest="summary_frontmatter_keys", default=[], metavar="KEY", help="Return frontmatter key holding the one-line summary, e.g. result_summary (repeatable; first non-placeholder value wins)")
    project_setsummary_parser.add_argument("--section-marker", action="append", dest="summary_section_markers", default=[], metavar="MARKER", help="Body section marker: the first non-empty line after a line containing it is the summary (repeatable; omitted = the declared default markers)")
    project_setsummary_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setsummary_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setsummary_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setsummary_parser, "set-return-summary")  # 缺省预演 / --confirm 才写(同一包装)
    # AIPOS-F110 件②③: 跨机工位开工材料声明(project.json workstations.<实例>) + 双向可达检查(同一 ssh transport 代码路径)
    project_setws_parser = project_subparsers.add_parser("set-workstation", help="AIPOS-F110: declare a cross-machine workstation's material access (project.json workstations.<instance>: gate_ssh_alias + material_access), validated against config.schema project_json.workstations; --dry-run (default) shows the diff, --confirm writes")
    project_setws_parser.add_argument("name", help="Established project name")
    project_setws_parser.add_argument("--instance", required=True, help="Workstation instance (as in its land event)")
    project_setws_parser.add_argument("--gate-ssh-alias", required=True, help="ssh alias of the gate machine as seen FROM the remote workstation")
    project_setws_parser.add_argument("--material-access", required=True, help="One line: how the remote executor reads/writes gate-side materials (no credentials)")
    project_setws_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setws_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setws_parser, "set-workstation")  # AIPOS-F125: 缺省预演 / --confirm 才写
    project_checkws_parser = project_subparsers.add_parser("check-workstation", help="AIPOS-F110: check a workstation for loop launch: land-event location, material declaration (remote), gate→workstation ssh probe (dir + harness executable) and workstation→gate ssh reachability via the declared gate_ssh_alias")
    project_checkws_parser.add_argument("name", help="Established project name")
    project_checkws_parser.add_argument("--instance", required=True, help="Workstation instance (as in its land event)")
    project_checkws_parser.add_argument("--harness", required=True, help="Harness whose launch template executable must be on the workstation PATH (enums.schema harness.launch)")
    project_checkws_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_checkws_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F122 件②: 存量冻结(迁移基线)——人肉期卡一次声明为历史(只追加清单; 声明 config.schema project_json.legacy_baseline)
    project_freeze_parser = project_subparsers.add_parser("freeze-legacy", help="AIPOS-F122: freeze legacy (pre-gate) cards as history via an append-only manifest (project.json legacy_baseline): state lint stops reporting them, next scan skips them, gate writes and loop refuse them (LEGACY_FROZEN). --unfreeze appends an unfreeze entry. Never moves or rewrites cards/records.")
    freeze_select = project_freeze_parser.add_mutually_exclusive_group(required=True)
    freeze_select.add_argument("--task-ids", nargs="+", metavar="ID", help="Freeze exactly these cards (unknown ID = whole command refused)")
    freeze_select.add_argument("--queue", help="Freeze every card in these queue dirs (comma-separated, e.g. claimed,completed,blocked)")
    freeze_select.add_argument("--unfreeze", nargs="+", metavar="ID", help="Append an unfreeze entry for these cards")
    project_freeze_parser.add_argument("--exclude", nargs="+", metavar="ID", default=None, help="With --queue: cards to leave unfrozen")
    project_freeze_parser.add_argument("--reason", required=True, help="Why (recorded in the manifest entry)")
    project_freeze_parser.add_argument("--actor", required=True, help="Instance freezing/unfreezing (recorded as frozen_by/unfrozen_by)")
    project_freeze_parser.add_argument("--owner-policy-ref", default=None, help="Optional Owner policy/decision ref recorded in the entry")
    freeze_mode = project_freeze_parser.add_mutually_exclusive_group()
    freeze_mode.add_argument("--dry-run", action="store_true", help="Preview only (default): list cards and their current lint issue counts, zero writes")
    freeze_mode.add_argument("--confirm", action="store_true", help="Append the manifest entry (and declare project.json legacy_baseline if absent)")
    project_freeze_parser.add_argument("--workspace-root", type=Path, default=None, help="Governance root (default: auto-detect)")
    project_freeze_parser.add_argument("--json", action="store_true", help="Output JSON")
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
    # AIPOS-F117 件①(Owner 10-04 裁定): 导入建项目 = project new 唯一实现(workspace_config.scaffold_project)+ 迁移清单;
    # 落点 = <home 根>/<项目名>(home 根同 project new 的 AIPOS-226 优先级梯), 原「任意目标目录自建骨架」第二实现退役
    project_import_parser = project_subparsers.add_parser("import", help="AIPOS-293/F117: Import a project from a structure file (created by the same implementation as `lybra project new` under the home root, plus a migration checklist)")
    project_import_parser.add_argument("structure_file", help="Path to lybra-project.yaml structure file")
    project_import_parser.add_argument("--name", help="Project name (default: structure file project_name)")
    project_import_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default), same as project new")
    project_import_parser.add_argument("--dry-run", action="store_true", help="Preview without creating (zero writes)")
    project_import_parser.add_argument("--actor", default=default_actor, help="Actor for provenance (registered_by)")
    project_import_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-370: envelope mint command (owner-gated)
    envelope_parser = subparsers.add_parser("envelope", help="Owner autonomy envelope operations")
    envelope_subparsers = envelope_parser.add_subparsers(dest="envelope_command")
    envelope_mint_parser = envelope_subparsers.add_parser("mint", help="Mint PreAuthorized autonomy envelope(s): --dry-run = local preview, --confirm = signed through the gate (owner_decision_record envelope path)")
    # AIPOS-F92 件①: --policy-id / --agent-or-role 可重复且按出现顺序成对(一条命令签一组信封, 每张走同一门路径)
    envelope_mint_parser.add_argument("--policy-id", action="append", required=True, help="Policy ID (repeatable; paired in order with --agent-or-role)")
    envelope_mint_parser.add_argument("--agent-or-role", action="append", required=True, help="Agent instance or role covered (repeatable; paired in order with --policy-id)")
    envelope_mint_parser.add_argument("--max-tasks", type=int, required=True, help="Maximum tasks allowed")
    envelope_mint_parser.add_argument("--task-mode", help="Task mode selector (e.g., code)")
    envelope_mint_parser.add_argument("--lane-repo", dest="lane_repo", action="append", default=None,
                                      help="AIPOS-F134/F139: lane selector — only cards whose lane.repo resolves to one of these repos (name in project.json "
                                           "repos.items; path when the project has no repos list). Repeatable for a repo set (a sub-project spanning "
                                           "several repos; a shared repo may appear in several advisors' sets); every name must resolve or the mint is "
                                           "refused. Default: any lane")
    envelope_mint_parser.add_argument("--expires-at", required=True, help="Expiration datetime (ISO8601)")
    envelope_mint_parser.add_argument("--decision-summary", required=True, help="Decision summary")
    envelope_mint_parser.add_argument("--actor", default="owner", help="Actor (default: owner)")
    envelope_mint_parser.add_argument(
        "--launch-harness", action="append", default=None, dest="launch_harness",
        help="AIPOS-F95: authorize `lybra loop` to launch this harness (repeatable; must have an enums.schema harness.launch template, "
             "e.g. pi). Omitted = manual mode only (workstation types /go). Applies to every envelope in this mint.")
    envelope_mint_parser.add_argument("--workspace-root", help="AIPOS-F92: target project governance root (where the policy lands); default = resolved governance workspace")
    envelope_mint_parser.add_argument("--connection-json", help="AIPOS-F92: connection.json holding the Owner credential (default <workspace-root>/.lybra/connection.json); token never printed (fingerprint only)")
    envelope_mint_parser.add_argument("--token-role", default="owner", help="AIPOS-F92: credential role used for --confirm (default owner; arming an envelope needs owner_confirm)")
    envelope_mint_mode = envelope_mint_parser.add_mutually_exclusive_group(required=True)
    envelope_mint_mode.add_argument("--dry-run", action="store_true", help="Preview without writing (same writer and same criteria as --confirm)")
    envelope_mint_mode.add_argument("--confirm", action="store_true", help="AIPOS-F92: sign through the gate (lybra_owner_decision_record_dry_run → _confirm with OWNER_CONFIRMED); output = gate-written records")
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

    # AIPOS-F57: Onboarding guide (from 0 to first card running)
    onboarding_parser = subparsers.add_parser("onboarding", help="AIPOS-F57: From 0 to first card running — full onboarding guide (zero manual editing)")
    onboarding_subparsers = onboarding_parser.add_subparsers(dest="onboarding_command")
    onboarding_guide_parser = onboarding_subparsers.add_parser("guide", help="Generate step-by-step onboarding guide for a new project")
    onboarding_guide_parser.add_argument("project_name", help="Project name (the project you're onboarding)")
    onboarding_guide_parser.add_argument("--home-root", help="Governance home root (defaults to LYBRA_HOME_ROOT env or ~/.lybra/projects)")
    onboarding_guide_parser.add_argument("--gate-url", help=f"Gate URL (default order per config.schema identity_resolution.keys.gate_url: Owner connection.json mcp.rpc_url > LYBRA_GATE_URL env > {_GATE_URL_DEFAULT})")
    onboarding_guide_parser.add_argument("--code-repo", help="Optional code repo path")
    onboarding_guide_parser.add_argument("--actor", help="Actor name (defaults to $USER or owner)")
    onboarding_guide_parser.add_argument("--workspace-dir", help="Executor workstation directory (defaults to ~/<project>-executor)")
    # AIPOS-F92 件②: 单门 home 根约定下的完整接入参数(全部可缺省推导; guide 打印推导结果与来源)
    onboarding_guide_parser.add_argument("--auditor-dir", help="Auditor workstation directory (defaults to ~/<project>-auditor)")
    onboarding_guide_parser.add_argument("--advisor-dir", help="Advisor Claude Code session directory — advisor skills land in <dir>/.claude/skills (defaults to ~/<project>)")
    onboarding_guide_parser.add_argument("--repo", action="append", dest="repos", metavar="NAME=ABS_PATH", help="Product repo (repeatable) for Step 2 project set-repos")
    onboarding_guide_parser.add_argument("--default-repo", help="Default repo name when more than one --repo")
    onboarding_guide_parser.add_argument("--host-segment", help="Host segment of instance names (defaults to short hostname)")
    from tools.aipos_cli.distribution_sync import advisor_harness_kinds as _advisor_harness_kinds
    onboarding_guide_parser.add_argument("--advisor-harness", choices=list(_advisor_harness_kinds()), default=None,
                                         help="AIPOS-F129: advisor session harness (choices = distribution.schema harness_semantics.kinds with advisor_session=true; default claude-code = existing guide). codex = Codex session: Step 5 enrolls with --harness codex, no .claude/skills delivery")
    onboarding_guide_parser.add_argument("--advisor-host", help="AIPOS-F129: host the advisor session runs on when it is not the governance-root machine (codex; recorded as --harness-host; its short name is the advisor instance host segment)")
    onboarding_guide_parser.add_argument("--executor-mode", choices=list(_executor_mode_declaration()["values"]), default=None,
                                         help="AIPOS-F143: executor enrollment mode (roles.schema executor_modes; default workstation = existing pi executor steps). subagent = advisor-spawned sub-agent executor: Step 7 enrolls it into the governance root with --executor-mode subagent --harness <advisor kind>, Step 9 declares lybra project set-execution")
    onboarding_guide_parser.add_argument("--owner-workspace", help="Gate workspace holding the Owner credential (central registry); defaults to <home>/<active project>")
    onboarding_guide_parser.add_argument("--owner-connection-json", help="Owner credential connection.json (defaults to <owner-workspace>/.lybra/connection.json)")
    onboarding_guide_parser.add_argument("--envelope-days", type=int, default=30, help="Envelope validity in days (default 30)")
    onboarding_guide_parser.add_argument("--max-tasks", type=int, default=50, help="Envelope max auto-released claims (default 50)")
    onboarding_guide_parser.add_argument("--json", action="store_true", help="Output JSON")
    onboarding_check_parser = onboarding_subparsers.add_parser("check", help="Check prerequisites for a specific onboarding step")
    onboarding_check_parser.add_argument("project_name", help="Project name")
    onboarding_check_parser.add_argument("--step", type=int, required=True, help="Guide step number to check prerequisites for (1-9, same numbering as onboarding guide)")
    onboarding_check_parser.add_argument("--home-root", help="Governance home root")
    onboarding_check_parser.add_argument("--workspace-dir", help="Workstation directory")
    onboarding_check_parser.add_argument("--json", action="store_true", help="Output JSON")

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

    # AIPOS-F71: lybra next — 唯一推导实现(合并 turn-advancer + next-step)
    next_parser = subparsers.add_parser("next", help="AIPOS-F71: 推导下一步(唯一实现)。无参=项目级扫描;--task-id=单卡")
    next_parser.add_argument("--task-id", help="Task ID to resolve (omit for project scan)")
    next_parser.add_argument("--run", action="store_true", help="AIPOS-F73件②③: 机器扣扳机 — 推导后立即执行命令（单步即退，禁循环）")
    next_parser.add_argument("--connection-json", help="Path to connection.json (for --run gate access)")
    next_parser.add_argument("--json", action="store_true", help="Output JSON")
    add_lane_argument(next_parser)  # AIPOS-F133 件②: --lane(声明 verbs.schema lane_view; 只用于项目扫描)

    # AIPOS-F73D: lybra loop — 顾问侧驱动器(信封授权下 watch 产物落盘 → next --run 单步 → 重推导, 直到 completed)。
    # 参数缺省与退出码只声明在 schema/verbs.schema.json verbs.lybra_loop 一处(argparse 缺省 None, 运行时读声明)。
    loop_parser = subparsers.add_parser(
        "loop",
        help="AIPOS-F73D: 顾问侧驱动器 — Owner 信封授权下有界循环推进一张卡到 completed(复用 agent watch + next --run)。"
        "AIPOS-F95: 信封 launch_harnesses 授权且工位条件全满足时, 等待前按 enums.schema harness.launch 模板在本机工位拉起一次 harness 进程"
        "(过程汇总为一行式进度, 产物就绪/超时/早退/中断即清进程组); 否则手工模式(工位敲 /go)。"
        "退出码(verbs.schema lybra_loop.exit_codes): 0=completed, 2=门拒, 3=等待超时/停滞/步数用尽, 4=不可推导/命令不可解析, 5=无信封",
    )
    # AIPOS-F131 件②: `lybra loop status` 子命令(读运行记录 + 探活 + 判停滞); 推进本身仍须 --task-id(run_loop_cli 校验, 退出码同 argparse 必填 2)
    loop_parser.add_argument("--task-id", help="要推进的卡 ID(推进时必填)")
    loop_parser.add_argument("--envelope", help="policy_id(<policies_root>/<id>.md, project.json paths.policies_root 缺省 5_tasks/policies); 缺省扫描信封目录取首个匹配本卡与驱动方身份的有效信封")
    loop_parser.add_argument("--actor", help="驱动方身份(顾问实例); 缺省=治理根 .lybra/role 的 instance, 再缺省 advisor")
    loop_parser.add_argument("--connection-json", help="透传给 next --run 的 connection.json 路径(token 永不上屏)")
    loop_parser.add_argument("--max-steps", type=int, default=None, help="硬上限: 推导轮数(含等待轮); 缺省读 verbs.schema(20)")
    loop_parser.add_argument("--max-wait", type=float, default=None, help="硬上限: 每次等待产物秒数; 缺省读 verbs.schema(沿用 agent watch 1800)")
    loop_parser.add_argument("--interval", type=float, default=None, help="等待轮询间隔秒(经 agent watch, 禁 sleep 自旋); 缺省读 verbs.schema(15)")
    loop_parser.add_argument("--no-launch", action="store_true", default=False,
                             help="AIPOS-F95: 不拉起 harness 进程(即使信封授权), 手工模式: 等待提示「请在 <工位目录> 的 <harness> 会话敲 /go」")
    loop_parser.add_argument("--json", action="store_true", help="Output JSON")
    loop_actions = loop_parser.add_subparsers(dest="loop_action")
    from tools.aipos_cli.verb_contract import declared_exit_codes as _declared_exit_codes  # AIPOS-F136: help 出口读声明, 不写死
    _loop_status_help = (
        "AIPOS-F131: 看 loop 进度——读运行记录(project.json paths.loop_runs_root)+ 本机探活 + 判停滞; 状态 running/stalled/"
        "launch_dead/loop_dead/ended/unprobeable(verbs.schema lybra_loop.run_record.states)。缺 --task-id = 列本项目全部未结束的运行。"
        "禁 tail/grep 原始日志自判。"
        "AIPOS-F136: 输出附「顾问下一动作」continue_wait/owner_needed/card_done_take_next/investigate; --wait <秒> 有界等到可行动或到时"
        "(退出码读声明: " + ", ".join(f"{k}={c}" for k, c in _declared_exit_codes("lybra_loop_status").items()) + ")"
    )
    loop_status_parser = loop_actions.add_parser("status", help=_loop_status_help, description=_loop_status_help)
    # 与父解析器同名参数用 SUPPRESS 缺省: 不覆盖写在 status 之前的同名参数
    loop_status_parser.add_argument("--task-id", default=argparse.SUPPRESS, help="卡 ID; 缺省 = 本项目全部未结束的 loop 运行")
    loop_status_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output JSON")
    add_lane_argument(loop_status_parser)  # AIPOS-F133 件②
    loop_status_parser.add_argument("--wait", type=float, default=None, metavar="SECONDS",
                                    help="AIPOS-F136 件①: 有界等待秒数(须 --task-id; 上限读 verbs.schema lybra_loop_status.wait.max_seconds), "
                                    "经 agent watch 等到顾问下一动作 ≠ continue_wait 或到时, 返回当时状态; 禁 until/sleep 轮询")
    # AIPOS-F138 件③ 的 --verbose 由 AIPOS-F142 件② 统一挂载(verbs.schema envelope_trace.verbose_commands 含 "loop status", 见 build_parser 末)

    # AIPOS-F138 件①: lybra charter — 拉取式输出实例的渲染后章程(他机会话开局经 ssh 读取; 参数由 verbs.schema lybra_charter 生成)
    from tools.aipos_cli.charter_render import add_charter_arguments as _add_charter_arguments

    charter_parser = subparsers.add_parser(
        "charter",
        help="AIPOS-F138: 输出实例的渲染后章程全文到 stdout(与 sync 同一渲染器; 他机会话开局经 ssh 在治理根所在机读取, 产品不推送)",
    )
    _add_charter_arguments(charter_parser)

    # AIPOS-F78 件②: lybra card render — 卡意图面单一渲染器(pi 三行 / codex Prompt.md+Plan.md / claude-code CLAUDE.md 片段)
    card_parser = subparsers.add_parser("card", help="AIPOS-F78: 卡意图面操作(render)")
    card_subparsers = card_parser.add_subparsers(dest="card_command")
    card_render_parser = card_subparsers.add_parser(
        "render",
        help="AIPOS-F78 件②: 把卡的意图面按 harness 渲染给执行引擎(同一源三输出; 零门动词/零 token; 落点全读项目声明)",
    )
    card_render_parser.add_argument("--task-id", required=True, help="卡 ID")
    card_render_parser.add_argument("--harness", choices=harness_choices, default=None, help="目标引擎(enums.schema harness); 缺省=卡面 harness, 再缺省=card.schema intent_face.harness.default_by_task_mode")
    card_render_parser.add_argument("--out-dir", help="codex/claude-code 输出目录; 缺省=卡工作树根")
    card_render_parser.add_argument("--stdout", action="store_true", help="只打印不落盘")
    card_render_parser.add_argument("--json", action="store_true", help="JSON 输出(文件名→内容)")

    # AIPOS-F78 件③: lybra artifact ingest — 产物入口(由 loop/next --run 触发, 非人用)
    artifact_parser = subparsers.add_parser("artifact", help="AIPOS-F78: 产物入口(ingest)")
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_command")
    artifact_ingest_parser = artifact_subparsers.add_parser(
        "ingest",
        help="AIPOS-F78 件③: 读项目声明落点找 Return/裁决报告, 校验必填 frontmatter 与分支 tip==commit_sha, 经既有薄壳铸记录(驱动方 token)",
    )
    artifact_ingest_parser.add_argument("--task-id", required=True, help="卡 ID(执行卡=Return 入口; R 卡=裁决入口)")
    artifact_ingest_parser.add_argument("--connection-json", help="connection.json(驱动方 token, 永不上屏)")
    artifact_ingest_parser.add_argument("--dry-run", action="store_true", help="只校验与打印将执行的薄壳命令, 不提交")
    artifact_ingest_parser.add_argument("--kind", choices=["return", "verdict", "finalization"], default=None,
                                        help="AIPOS-F90 件②: 推导核声明的入门节点(return=执行体 Return / verdict=审计报告 / finalization=外部 FINALIZE Return); "
                                             "给出时与产物入口自判的种类不符即拒(INGEST_KIND_MISMATCH)")
    artifact_ingest_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # AIPOS-F71: 退役旧入口 next-step 保留为兼容转发(输出退役提示); turn-advancer 入口随 AIPOS-F91 删除
    next_step_parser = subparsers.add_parser("next-step", help="[RETIRED by AIPOS-F71] Use 'lybra next --task-id <ID>' instead")
    next_step_parser.add_argument("--task-id", required=True, help="Task ID to resolve")
    next_step_parser.add_argument("--workspace-root", type=Path, help="Workspace root")
    next_step_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-FND-1: Five missing loop-step CLIs (wrap existing gate verbs/backend functions)
    # 1. task progress - wrap lybra_task_progress (tools.py:2847)
    task_progress_parser = subparsers.add_parser("task-progress", help="Report task progress event")
    task_progress_parser.add_argument("--task-id", required=True, help="Task ID")
    task_progress_parser.add_argument("--actor", required=True, help="Actor reporting progress")
    task_progress_parser.add_argument("--agent-instance", required=True, help="Agent instance name")
    task_progress_parser.add_argument("--event-type", required=True, choices=get_enum_values("progress_status"), help="Event type (enums.schema progress_status)")
    task_progress_parser.add_argument("--summary", help="Event summary (optional)")
    task_progress_parser.add_argument("--model-self-reported", help="Model used (for capability ledger)")
    task_progress_parser.add_argument("--stage", help="Current stage (optional)")
    task_progress_parser.add_argument("--reason", help="Reason (for blocked events)")
    task_progress_parser.add_argument("--confirm", action="store_true", help="AIPOS-F22: Use gate MCP (via薄壳工厂) instead of local write")
    task_progress_parser.add_argument("--connection-json", help="Path to connection.json (for --confirm gate access)")
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

    # AIPOS-F149 件③(gap #89): lybra regression baseline — 审计 main 侧基线按 sha 取已记录的合并后回归失败集合(只读)
    regression_parser = subparsers.add_parser(
        "regression", help="AIPOS-F149: 合并后回归记录查询(只读; finalize 合入后已落的回归记录, 实现 post_merge_regression)")
    regression_subparsers = regression_parser.add_subparsers(dest="regression_command")
    regression_baseline_parser = regression_subparsers.add_parser(
        "baseline",
        help="AIPOS-F149: 按 sha 取已记录的合并后回归失败集合(审计 main 侧基线复用)。结论 recorded = main 侧不必自跑; "
        "self_run_required = 无该 sha 的记录或记录为超时/未完成/读不出, main 侧须自跑。被审分支一侧无论如何须自跑。"
        "治理根用全局 `lybra --workspace-root <治理根> regression baseline`(缺省同其他命令的解析梯)",
    )
    regression_baseline_parser.add_argument("--sha", help="要查的提交(完整 40 位原样用; 短 sha/引用在产品仓解析); 缺省 = 产品仓基线分支当前 HEAD")
    regression_baseline_parser.add_argument("--repo-root", help="解析 sha 用的产品仓; 缺省 = project.json repos.default / code_repo")
    regression_baseline_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-R7A2 靶②: governance-commit (顾问收口一条命令)
    governance_commit_parser = subparsers.add_parser("governance-commit", help="AIPOS-R7A2: N6 收账提交(校验四件→commit→push)")
    governance_commit_parser.add_argument("--task-id", required=False, help="Task ID for governance closure (optional; omit for governance batch updates). AIPOS-F94: without --paths/--paths-file = task-scoped precise commit (paths derived from the card + declarations: this card and its audit card's queue files/drafts/records/ledger dirs + card chronicle; never whole-root); idempotent (already committed and pushed = no-op)")
    governance_commit_parser.add_argument("--actor", required=True, help="Actor performing governance commit")
    governance_commit_parser.add_argument("--governance-root", help="Governance workspace root; defaults to auto-discovery")
    governance_commit_parser.add_argument("--workspace-root", help="Product repo root (for schema resolution); defaults to the running Lybra code repo (AIPOS-F88 product_repo_root)")
    governance_commit_parser.add_argument("--no-push", action="store_true", help="Commit but do not push (default: push)")
    governance_commit_parser.add_argument("--message", help="Custom commit message")
    governance_commit_parser.add_argument("--dry-run", action="store_true", help="AIPOS-F79: validate and list the exact files that would be committed (read-only: no add/reset/stash)")
    governance_commit_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F79 件①: 显式白名单 — 给了即只 git add -- <paths>, 绝不 add -A; 越出治理根/他项目 = 拒
    governance_commit_parser.add_argument("--paths", action="append", default=None, metavar="PATH", help="AIPOS-F79: whitelist path relative to governance root (file or dir; repeatable). Only these are staged (git add -- <paths>, never add -A). Paths outside the governance root are rejected. Mandatory for any project other than lybra's own workspace.")
    governance_commit_parser.add_argument("--paths-file", default=None, metavar="FILE", help="AIPOS-F79: file with one whitelist path per line (blank/# lines ignored); mutually exclusive with --paths")

    # AIPOS-F67: lybra brief — 顾问真相派生(冷启动简报算出来,不写出来)
    brief_parser = subparsers.add_parser("brief", help="AIPOS-F67: 冷启动简报(阶段坐标+增量真相+在途三查+契约清单+新鲜度)")
    brief_parser.add_argument("--repo-root", help="Product repo root (for schema resolution); defaults to auto-detection")
    brief_parser.add_argument("--since", help="Only show decisions since this date (YYYY-MM-DD)")
    brief_parser.add_argument("--json", action="store_true", help="Output JSON")
    add_lane_argument(brief_parser)  # AIPOS-F133 件②: 缺省按 lane 分组

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

    # AIPOS-F142 件②: [ENVELOPE_TRACE] 的 --verbose 只按声明挂载(verbs.schema envelope_trace.verbose_commands, 唯一挂载口)
    from tools.aipos_cli.autonomy_policy import attach_verbose_flags

    attach_verbose_flags(parser)
    # AIPOS-F144 件②: 子命令级 --workspace-root 只按声明挂载(verbs.schema governance_root_resolution.commands, 唯一注册口;
    # 与全局 --workspace-root 同义, 合并与解析见 _find_repo_root_for_args)
    from tools.aipos_cli.workspace_config import attach_workspace_root_flags

    attach_workspace_root_flags(parser)
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


def _workspace_roots_command(args: argparse.Namespace) -> int:
    """AIPOS-F88 件③: `lybra workspace roots` —— 根路径语义分域的只读出口。
    governance_root = workspace_config.governance_workspace_root; product_repo = product_repo_root(治理根)(项目声明的产品仓);
    code_repo / schema_dir = product_repo_root()(运行中 Lybra 代码所在仓 / 其 schema/); home_root = resolve_home_root。
    各字段按需惰性解析; 解析失败的字段带拒因原文(不猜路径)。--field 取单值, 解析不到 = 退出码 1。"""
    from tools.aipos_cli.workspace_config import (
        CardRepoUnresolved,
        governance_workspace_root,
        merge_workspace_root_flags,
        product_repo_root,
        resolve_home_root,
    )
    from tools.schema_loader import SchemaLoadError

    resolve_errors = (FileNotFoundError, ValueError, OSError, CardRepoUnresolved, SchemaLoadError)

    def _gov() -> Path:
        # AIPOS-F144: 全局与子命令级 --workspace-root 同义(merge_workspace_root_flags; 两处不同 = 拒)
        return governance_workspace_root(merge_workspace_root_flags(getattr(args, "global_workspace_root", None),
                                                                    getattr(args, "workspace_root", None)))

    resolvers = {
        "governance_root": _gov,
        "product_repo": lambda: product_repo_root(_gov()),
        "code_repo": lambda: product_repo_root(),
        "schema_dir": lambda: product_repo_root() / "schema",
        "home_root": lambda: resolve_home_root(),
    }
    field = getattr(args, "field", None)
    if field:
        try:
            print(str(resolvers[field]()))
            return 0
        except resolve_errors as exc:
            print(f"Error: {field} 不可解析: {exc}", file=sys.stderr)
            return 1
    report: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, fn in resolvers.items():
        try:
            report[key] = str(fn())
        except resolve_errors as exc:
            report[key] = None
            errors[key] = str(exc)
    report["errors"] = errors
    if getattr(args, "json", False):
        print(render_json(report))
    else:
        for key in resolvers:
            print(f"{key}: {report[key] if report[key] is not None else '✗ ' + errors[key]}")
    return 1 if errors else 0


@cli_envelope_trace_entry  # AIPOS-F142 件②: 本次 CLI 调用结束即恢复 [ENVELOPE_TRACE] 开关原状(in-process 嵌套调用不泄漏)
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # AIPOS-F142 件②: [ENVELOPE_TRACE] 唯一开关的 CLI 决定点(缺省关; --verbose / 调试环境变量开; in-process 嵌套沿用外层)
    decide_cli_envelope_trace(bool(getattr(args, "verbose", False)))
    if not args.command:
        parser.print_help()
        return 2


    if args.command == "agent":
        # AIPOS-F103 件①: 只剩 `agent watch --workspace-root`(文件系统哨兵, loop 唯一等待); 旧跨机连接器子命令已退役
        if getattr(args, "agent_command", None) == "watch":
            from tools.aipos_cli.agent_watch_fs import run_fs_watch_cli
            return run_fs_watch_cli(args)
        print("usage: lybra agent watch --workspace-root <治理根> [...](lybra agent watch --help)", file=sys.stderr)
        return 2

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

        if args.draft_command == "regen-machine-zone":
            try:
                from tools.aipos_cli.draft_writer import regen_machine_zone_for_pending
                from tools.aipos_cli.board_adapter import _resolve_product_code_repo
                
                # AIPOS-F74-R2: 分离治理根与产品根 - schema 在产品仓
                # AIPOS-F78C: 项目级调用(无卡)= 项目缺省仓(repos.default / code_repo 别名), 经同一解析函数
                governance_root = repo_root
                product_root = _resolve_product_code_repo(governance_root)
                
                result = regen_machine_zone_for_pending(
                    governance_root,
                    product_root,
                    task_id=args.task_id,
                    actor=args.actor,
                    dry_run=args.dry_run,
                )
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                # Render simple text output
                if result.get("verdict") == Verdict.BLOCK:
                    print(f"BLOCKED: {result.get('message', 'Unknown error')}")
                    if result.get("blocking_reasons"):
                        for reason in result["blocking_reasons"]:
                            print(f"  - {reason}")
                else:
                    print(result.get("message", "Success"))
                    if result.get("data", {}).get("updated_cards"):
                        print(f"\nUpdated {len(result['data']['updated_cards'])} card(s):")
                        for card in result["data"]["updated_cards"]:
                            print(f"  - {card}")
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
            if args.roles_command == "write-boundary":
                # AIPOS-F66B 件②: 薄壳, 全部逻辑在 write_boundary(唯一读取口)
                from tools.aipos_cli.write_boundary import run_write_boundary_cli

                return run_write_boundary_cli(args, Path(workspace_root))
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
                from tools.aipos_cli.confirm_client import GateClient, GateError, load_gate_client_token, resolve_gate_base_url
                owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                reason = str(getattr(args, "reason", "") or "").strip()
                
                if not owner_auth_ref:
                    print("Error: --owner-authorization-ref is required (owner-gated)", file=sys.stderr)
                    return 1
                
                # 连接信息
                conn_override = getattr(args, "connection_json", None)
                conn_path = workspace_connection_path(workspace_root, connection_target=Path(conn_override) if conn_override else None)
                if not conn_path.exists():
                    print(f"Error: connection.json not found: {conn_path}", file=sys.stderr)
                    print("Hint: 在治理工作区运行或 --connection-json 指定", file=sys.stderr)
                    return 1
                
                # AIPOS-F106 件①: 门基址唯一推导口 + 凭据角色偏好序唯一声明(config.schema identity_resolution)
                try:
                    base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
                    _token_role, token = load_gate_client_token(conn_path)
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                
                try:
                    # AIPOS-F78B 件④a: 调既有单一动词 lybra_roles_register(owner_authorization_ref 门控, workspace_root 落盘根);
                    # 旧 dry_run/confirm 对从未挂入 TOOL_HANDLERS(Unknown tool), 已删
                    client = GateClient(base_url, token, timeout=30.0)
                    result = client.call_tool("lybra_roles_register", {
                        "name": args.name,
                        "builtin_class": args.builtin_class,
                        "workspace_root": str(workspace_root),
                        "owner_authorization_ref": owner_auth_ref,
                        "reason": reason,
                        "actor": "cli:roles-register",
                    })
                    if not result.get("ok"):
                        print(f"Error: gate rejected: {json.dumps(result, ensure_ascii=False)[:800]}", file=sys.stderr)
                        return 1
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
                from tools.aipos_cli.confirm_client import GateAddressError, GateClient, GateError, load_owner_token, resolve_gate_base_url

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
                conn_path = workspace_connection_path(workspace_root, connection_target=connection_target)
                if not conn_path.exists():
                    return _enroll_code_fail(
                        f"local connection.json not found: {conn_path}",
                        next_step=("在治理工作区运行本命令(或 --connection-json 指定本机 connection.json); "
                                   "薄壳只调门动词, 没有(也不许有)本地发码路径。"),
                    )
                conn_data = json.loads(conn_path.read_text(encoding="utf-8"))
                # AIPOS-F106 件①: 门基址唯一推导口(委托 ConnectionResolver.resolve_gate_url), 凭据文件须声明 mcp.rpc_url
                try:
                    base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
                except GateAddressError as exc:
                    return _enroll_code_fail(str(exc), next_step="先 lybra serve start 或修正 connection.json。")
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
                # AIPOS-F93 件②: 交付文案 = onboarding.enroll_delivery(门 paste_text / paste_instruction 同一渲染源, CLI 与门一份)
                from tools.aipos_cli.onboarding import enroll_delivery

                delivery = enroll_delivery(str(confirm.get("self_contained_code") or ""))
                # FIX-2 兼容: --json 输出稳定含 self_contained_code/code_id/fingerprint 在顶层
                result_out = {
                    "ok": True,
                    "operation": "roles_enroll_code",
                    "issued_via": "gate_verb_thin_shell(F24A)",
                    "code_id": confirm.get("code_id"),
                    "self_contained_code": confirm.get("self_contained_code"),
                    "paste_text": delivery["paste_text"],
                    "paste_instruction": delivery["paste_instruction"],
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
                    print("\n  " + delivery["paste_instruction"].replace("\n", "\n  "))
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
                # AIPOS-362/F78B 件④c: 注册码只住在发码门的注册表(发码/兑换同根), 本地文件不是真相 → 薄壳调门动词
                # lybra_roles_enroll_list(governance_root=本工作区: 只列签给该治理根工位的码), 与门口径唯一
                from tools.aipos_cli.confirm_client import GateClient, load_gate_client_token, resolve_gate_base_url
                conn_override = getattr(args, "connection_json", None)
                conn_path = workspace_connection_path(workspace_root, connection_target=Path(conn_override) if conn_override else None)
                if not conn_path.exists():
                    print(f"Error: connection.json not found: {conn_path}", file=sys.stderr)
                    print("Hint: enroll-list 读门注册表(发码所在), 需 --connection-json 或在治理工作区运行", file=sys.stderr)
                    return 1
                # AIPOS-F106 件①: 门基址唯一推导口 + 凭据角色偏好序唯一声明(config.schema identity_resolution)
                try:
                    base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
                    _token_role, token = load_gate_client_token(conn_path)
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                client = GateClient(base_url, token, timeout=30.0)
                result = client.call_tool("lybra_roles_enroll_list", {"governance_root": str(workspace_root)})
                if not result.get("ok"):
                    print(f"Error: gate rejected: {json.dumps(result, ensure_ascii=False)[:800]}", file=sys.stderr)
                    return 1
                codes = result.get("enrollments") or []
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    print(f"registry: {result.get('registry_root')}  filter governance_root: {result.get('governance_root_filter')}")
                    print(f"{'Code ID':<24} {'Role':<16} {'Instance':<32} {'Status':<10} {'Expires At'}")
                    for code in codes:
                        inst = code.get('instance') or '(any)'
                        expires = code.get('expires_at') or '(never)'
                        print(f"{code['code_id']:<24} {code['role']:<16} {inst:<32} {code['status']:<10} {expires}")
            elif args.roles_command == "enroll-where":
                # AIPOS-F107 件②: 薄壳, 逻辑在 enrollment.enrollment_whereabouts(只读; 所属项目经 enrollment_owner_root 唯一解析口)
                from tools.aipos_cli.enrollment import enrollment_whereabouts

                report = enrollment_whereabouts(workspace_root, args.instance)
                if getattr(args, "json", False):
                    print(render_json(report))
                else:
                    print(f"实例 {report['instance']}: verdict={report['verdict']}")
                    print(f"  应在 log: {report['expected_log'] or '(无所属项目)'}"
                          f"(所属项目 {report['owner_project'] or '-'}, 依据 {report['owner_source'] or report['owner_reason']})")
                    if not report["found"]:
                        print("  实际: home 下各项目与签发门工作区的 enrollment_log 均无该实例事件")
                    for item in report["found"]:
                        acts = ", ".join(f"{k}×{v}" for k, v in sorted(item["actions"].items())) or "(无未作废事件)"
                        voided = f"  已作废 {item['voided_events']} 个(void 行 {item['void_lines']})" if item["voided_events"] else ""
                        print(f"  实际所在 log: {item['log']}  事件 {acts}{voided}  最新 land {item['latest_land_at'] or '-'}"
                              f"  workstation={item['latest_land_workstation'] or '-'}")
                    loc = report["workstation_location"]
                    if loc is not None:
                        print(f"  loop 工位定位(workstation_location@所属项目): found={loc['found']} dir={loc['dir']} {loc['reason']}".rstrip())
                    if report["verdict"] == "misplaced":
                        print("  处置: 存量事件在签发方 log, loop 定位不到; 在所属项目重签码重接入(新事件落所属项目 log), 不手改日志")
                return 0
            elif args.roles_command == "enroll-void":
                # AIPOS-F121 件②: 薄壳, 逻辑在 enrollment.void_instance_events(定位与 enroll-where 同一扫描; 写口 _write_trail_line 只追加)
                from tools.aipos_cli.enrollment import void_instance_events

                result = void_instance_events(workspace_root, args.instance, by=args.actor, reason=args.reason,
                                              dry_run=bool(args.dry_run))
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    head = "dry-run(零写入), 将追加" if result["dry_run"] else "已追加"
                    print(f"实例 {result['instance']}: {head} {len(result['entries'])} 行 void 事件")
                    for item in result["entries"]:
                        print(f"  log: {item['log']}")
                        print(f"    作废 {item['event_count']} 个事件(code_id {item['code_id_count']} 个), 行号区间 {item['voids_lines']}")
                        print(f"    {'将追加' if result['dry_run'] else '已追加'}: {item['line']}")
                return 0
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
                        "旧码示例:   lybra roles enroll --code <裸码> --gate-url http://<host>:<port> --workspace ~/workstations/my-agent"
                    )
                try:
                    result = enroll(
                        code=args.code,
                        gate_url=gate_url_arg,
                        workspace_root=workspace_root,
                        policy=getattr(args, "policy", None),
                        bootstrap_token=getattr(args, "bootstrap_token", None),
                        verify=bool(getattr(args, "verify", False)),
                        harness_kind=getattr(args, "harness", None),
                        harness_dir=getattr(args, "harness_dir", None) or None,
                        harness_host=getattr(args, "harness_host", None),
                        executor_mode=getattr(args, "executor_mode", None),
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
                        # AIPOS-F27 大项B: 输出落点字符串与实际路径 assert 相等(禁错标)
                        print(f"\n  Configuration written to: {result['lybra_dir']}/")
                        for fname in result['files_written']:
                            print(f"    - {fname}")
                        # AIPOS-F54 ③: 目标目录自动创建并出声
                        if result.get('created_workspace_dir'):
                            print(f"\n  ✓ 目标目录不存在, 已自动创建: {result['workspace_root']}")
                        # AIPOS-F54 ①: 接线逐项报告(seed_only 跳过项也出声)
                        wiring = result.get('wiring') or {}
                        if wiring:
                            print(f"\n  工位接线(role_class={wiring.get('role_class')}):")
                            for item, info in (wiring.get('items') or {}).items():
                                if item == 'skills':
                                    links = info.get('links') or {}
                                    print(f"    - .pi/skills/ ({info.get('count')} 项, 按角色类分配)")
                                    for sname, sinfo in links.items():
                                        print(f"        · {sname}: {sinfo.get('status')} → {sinfo.get('target_exists')}")
                                else:
                                    status = info.get('status') if isinstance(info, dict) else info
                                    print(f"    - {item}: {status}")
                        # AIPOS-F82 件②: 未写的接线项/章程逐项点名(禁写悬空扩展、禁落未渲染母本)
                        for warn in result.get('warnings') or []:
                            print(f"    ⚠ {warn}")
                        # AIPOS-F54 ⑮: 可启动最小集逐项校验(缺项逐项点名)
                        mbs = result.get('minimum_bootable_set') or {}
                        if mbs:
                            if mbs.get('skipped'):
                                print(f"\n  · 可启动最小集: {mbs['skipped']}")
                            elif mbs.get('ok'):
                                print(f"\n  ✓ 可启动最小集全部就绪({len(mbs.get('checks') or [])} 项)")
                            else:
                                print(f"\n  ⚠ 可启动最小集缺项: {', '.join(mbs.get('missing') or [])}")
                        hd_ = result.get('harness_delivery')
                        if hd_ and hd_.get('note'):  # AIPOS-F129: 非本机可落 harness(codex 无 dir / 他机会话)零交付, 原因由 sync 点名
                            print(f"\n  · {hd_['note']}")
                        elif hd_:
                            print(f"\n  ✓ {(result.get('harness') or {}).get('kind')} 件已交付: {hd_.get('files_fetched')} 个文件 → {(result.get('harness') or {}).get('dir')}(清单 {hd_.get('manifest_path')})")
                            for ch in hd_.get('changes') or []:
                                print(f"    - {ch.get('distribution_id')}: {ch.get('files_written')} 个文件 → {ch.get('target_path')}")
                        if result.get('harness'):
                            print(f"  harness: {json.dumps(result['harness'], ensure_ascii=False)}(已记入 .lybra/role)")
                        if result.get('subagent_harness'):  # AIPOS-F143 件④
                            print(f"  executor_mode: {result['executor_mode']}(子 agent 执行者; harness={result['subagent_harness']} 已记入 land 事件, 未写 .lybra/role)")
                        pd_ = result.get('policy_derivation')
                        if pd_ and pd_.get('policy_id'):
                            print(f"\n  ✓ owner_policy_ref 已推导: {pd_['policy_id']}")
                        elif pd_ and pd_.get('warning'):
                            print(f"\n  ⚠ 未推导 owner_policy_ref(非循环角色类): {pd_.get('reason')}")
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
        # AIPOS-F105: workspace 组只剩只读 roots(旧 workspace init 随建项目单入口退役; 建项目 = lybra onboarding guide + lybra project new)
        if getattr(args, "workspace_command", None) == "roots":
            return _workspace_roots_command(args)
        parser.print_help()
        return 2

    if args.command == "project":
        if not getattr(args, "project_command", None):
            parser.print_help()
            return 2
        try:
            # AIPOS-293: export doesn't need home_root; dispatch-mode uses --project-root(AIPOS-F117: import 与 project new 同读 home 根)
            if args.project_command in ("export", "dispatch-mode", "freeze-legacy"):
                pass  # handled below(AIPOS-F122: freeze-legacy 按 --workspace-root 定位治理根)
            else:
                home, home_source = resolve_home_root_with_source(explicit_root=args.home_root)
            if args.project_command == "new":
                # AIPOS-F24 大项A: 薄壳模式 - 调用门动词 lybra_project_new
                # 保留交互式询问(CLI 侧体验),但实际创建走门动词
                from tools.aipos_cli.confirm_client import (
                    GateAddressError,
                    GateClient,
                    GateError,
                    declared_rpc_url,
                    load_gate_client_token,
                    resolve_gate_base_url,
                )

                # AIPOS-F92 件②: home 根只经 AIPOS-226 优先级梯解析, 打印结果与来源(门只扫描 home 根, 不登记 home 根外的治理根)
                print(f"home 根: {home}(来源: {home_source}); 项目建在 {home / args.name}")
                # AIPOS-F92: 非交互(stdin 非终端, 如 agent 会话)不弹询问, 用缺省协作配置(可后改)
                collaboration_profile = _ask_project_type_interactive() if sys.stdin.isatty() else None

                # 连接信息 (从 home 推导连接配置)
                # 对于 project new,我们需要有一个已存在的门服务
                # 暂时使用环境变量或默认连接
                conn_path = workspace_connection_path(Path.home())
                if not conn_path.exists():
                    # AIPOS-226 裁定 2=a: 本地 Owner 脚手架(不铸凭据、不过门), 门按 home 根扫描发现新项目
                    root = scaffold_project(
                        home, args.name, code_repo=args.code_repo, registered_by=args.actor,
                        collaboration_profile=collaboration_profile
                    )
                    print(f"Created project root: {root} (local scaffold)")
                    print(f"project.json: {project_json_path(root)}")
                    for snap in sorted(stage_archive_root(root).glob("*.md")):  # AIPOS-F145: 阶段档案落点唯一读取口
                        if snap.name.lower() != "readme.md":
                            print(f"stage snapshot: {snap}")
                    if collaboration_profile:
                        print(f"collaboration_profile: {collaboration_profile}")
                    print(f"next: 门以 home 根 {home} 发现本项目(双标记 5_tasks/queue + project.json); 接入步骤见 lybra onboarding guide {args.name}")
                    return 0

                # 门路径(~/.lybra/connection.json 存在): owner-gated
                owner_auth_ref = getattr(args, "owner_authorization_ref", None)
                if not owner_auth_ref:
                    print("Error: --owner-authorization-ref is required on the gate path (owner-gated)", file=sys.stderr)
                    print("Hint: project creation via the gate requires owner authorization.", file=sys.stderr)
                    return 1
                
                # AIPOS-F106 件①: 凭据文件坏 = 拒(fail-closed); 未声明 mcp.rpc_url = 降级本地; 门基址/凭据角色序走唯一推导口
                try:
                    declared = declared_rpc_url(conn_path)
                except GateAddressError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                if declared is None:
                    # 降级:本地调用
                    root = scaffold_project(
                        home, args.name, code_repo=args.code_repo, registered_by=args.actor,
                        collaboration_profile=collaboration_profile
                    )
                    print(f"Created project root: {root} (local fallback)")
                    return 0
                
                try:
                    base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
                    _token_role, token = load_gate_client_token(conn_path)
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
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
            if args.project_command == "freeze-legacy":
                return _project_freeze_legacy(args)
            if args.project_command == "set-repos":
                # AIPOS-F92 件②: 声明产品仓(唯一写入口 workspace_config.set_project_repos; 校验 = 唯一读取口 project_repos)
                from tools.aipos_cli.workspace_config import CardRepoUnresolved, set_project_repos

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                items: dict[str, str] = {}
                for raw in args.repo:
                    name_part, sep, path_part = str(raw).partition("=")
                    if not sep or not name_part.strip() or not path_part.strip():
                        print(f"Error: --repo 须为 <仓名>=<绝对路径>, 得到 {raw!r}", file=sys.stderr)
                        return 2
                    if name_part.strip() in items:
                        print(f"Error: --repo 仓名 {name_part.strip()!r} 重复", file=sys.stderr)
                        return 2
                    items[name_part.strip()] = path_part.strip()
                default_repo = args.default_repo or (next(iter(items)) if len(items) == 1 else None)
                if not default_repo:
                    print("Error: 多于一个 --repo 时须给 --default <仓名>(卡缺 lane.repo 时派生此仓)", file=sys.stderr)
                    return 2
                try:
                    declared = set_project_repos(home, args.name, items, default=default_repo, no_deploy=list(args.no_deploy or []),
                                                 dry_run=not args.confirm)
                except CardRepoUnresolved as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                # AIPOS-F125: 缺省预演(diff + 校验, 零写入), --confirm 才写; 输出同一包装
                repos_view = {"default": declared["default"], "items": {k: str(v) for k, v in declared["items"].items()},
                              "no_deploy": list(declared["no_deploy"]), "code_repo": str(declared["code_repo"])}
                _project_json_two_phase_emit(
                    "set-repos", target, {k: declared[k] for k in ("dry_run", "changed", "written", "diff")}
                    | {"project_json": str(declared["project_json"])}, json_out=getattr(args, "json", False), extra=repos_view,
                    details=[f"repos.default = {repos_view['default']}  code_repo = {repos_view['code_repo']}"]
                    + [f"repos.items.{k} = {v}" for k, v in repos_view["items"].items()]
                    + [f"repos.no_deploy = {repos_view['no_deploy']}(本仓不部署: finalize deploy_status=not_applicable)"] * bool(repos_view["no_deploy"]))
                return 0
            if args.project_command == "set-paths":
                # AIPOS-F123 件②: 唯一实现 workspace_config.set_project_paths(声明校验 + update_project_json 唯一写路径)
                from tools.aipos_cli.workspace_config import PathsDeclarationError, set_project_paths

                keys, values = list(args.path_keys or []), list(args.path_values or [])
                if len(keys) != len(values):
                    print(f"Error: --key 与 --value 须成对按序给出(得到 {len(keys)} 个 --key, {len(values)} 个 --value)", file=sys.stderr)
                    return 2
                # AIPOS-F127 件①: 目标 = 显式项目名 > --workspace-root / 所在治理根声明 > 拒(原缺省走 home 级活动项目, 已删)
                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                try:
                    outcome = set_project_paths(target["project_root"], list(zip(keys, values)), dry_run=not args.confirm)
                except PathsDeclarationError as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                _project_json_two_phase_emit(
                    "set-paths", target, outcome, json_out=getattr(args, "json", False),
                    details=[f"paths.{change['key']}: {change['before']!r} → {change['after']!r}" for change in outcome["changes"]])
                return 0
            if args.project_command == "set-meta":
                # AIPOS-F127 件③: 唯一实现 workspace_config.set_project_meta(键须 config.schema project_json 已声明; update_project_json 唯一写路径)
                from tools.aipos_cli.workspace_config import ProjectMetaError, project_meta_declaration, set_project_meta

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                meta_values = {key: getattr(args, f"meta_{key}", None) for key in project_meta_declaration()["keys"]}
                try:
                    outcome = set_project_meta(target["project_root"], meta_values, dry_run=not args.confirm)
                except ProjectMetaError as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                _project_json_two_phase_emit(
                    "set-meta", target, outcome, json_out=getattr(args, "json", False),
                    details=[f"{change['key']}: {change['before']!r} → {change['after']!r}" for change in outcome["changes"]])
                return 0
            if args.project_command == "set-execution":
                # AIPOS-F143 件②: 唯一实现 enrollment.set_project_execution(声明校验 + 实例接入核验 + update_project_json 唯一写路径)
                from tools.aipos_cli.enrollment import set_project_execution
                from tools.aipos_cli.workspace_config import ExecutionDeclarationError

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                try:
                    outcome = set_project_execution(
                        target["project_root"], subagent_executor=args.subagent_executor, auditor=args.auditor,
                        pi_executor=args.pi_executor, pi_allowed_task_modes=list(args.pi_allowed_task_modes or []),
                        default_mode=args.default_mode, dry_run=not args.confirm)
                except ExecutionDeclarationError as exc:
                    print(f"Error: {exc.code}(project.json 未改动)", file=sys.stderr)
                    for problem in exc.problems:
                        print(f"  - {problem}", file=sys.stderr)
                    return 1
                view = outcome["execution"]
                _project_json_two_phase_emit(
                    "set-execution", target, {k: outcome[k] for k in ("dry_run", "changed", "written", "diff", "project_json")},
                    json_out=getattr(args, "json", False), extra={"execution": view},
                    details=[f"execution.{k} = {view[k]!r}" for k in ("default_mode", "subagent_executor", "pi_allowed_task_modes",
                                                                       "pi_executor", "auditor")]
                    + ["各实例已核: 本项目已接入(审计者 / pi 执行者有工位位置); 起草缺省与发卡核身份同读本段"])
                return 0
            if args.project_command == "set-return-summary":
                # AIPOS-F148 件②: 唯一实现 workspace_config.set_return_summary_source(声明校验 + update_project_json 唯一写路径)
                from tools.aipos_cli.workspace_config import ReturnSummarySourceError, set_return_summary_source

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                try:
                    outcome = set_return_summary_source(
                        target["project_root"], frontmatter_keys=list(args.summary_frontmatter_keys or []),
                        section_markers=list(args.summary_section_markers or []), dry_run=not args.confirm)
                except ReturnSummarySourceError as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                view = outcome["return_summary_source"]
                _project_json_two_phase_emit(
                    "set-return-summary", target, {k: outcome[k] for k in ("dry_run", "changed", "written", "diff", "project_json")},
                    json_out=getattr(args, "json", False),
                    extra={"return_summary_source": {k: view[k] for k in ("frontmatter_keys", "section_markers")}},
                    details=[f"return_summary_source.frontmatter_keys = {view['frontmatter_keys']!r}",
                             f"return_summary_source.section_markers = {view['section_markers']!r}",
                             "交回就绪判据 / 推导核交回步 / artifact ingest 同读本段(未给的键取 transitions 缺省)"])
                return 0
            if args.project_command == "set-workstation":
                # AIPOS-F110 件②: 唯一写入口 workspace_config.set_project_workstation(校验 = config.schema project_json.workstations)
                from tools.aipos_cli.workspace_config import WorkstationDeclarationError, set_project_workstation

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                try:
                    declared = set_project_workstation(target["project_root"], args.instance,
                                                       gate_ssh_alias=args.gate_ssh_alias, material_access=args.material_access,
                                                       dry_run=not args.confirm)
                except WorkstationDeclarationError as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                # AIPOS-F125: 缺省预演(diff + 校验, 零写入), --confirm 才写; 输出同一包装
                _project_json_two_phase_emit(
                    "set-workstation", target, {k: declared[k] for k in ("project_json", "dry_run", "changed", "written", "diff")},
                    json_out=getattr(args, "json", False),
                    extra={k: declared[k] for k in ("instance", "gate_ssh_alias", "material_access")},
                    details=[f"workstations.{declared['instance']}: gate_ssh_alias={declared['gate_ssh_alias']} "
                             f"material_access={declared['material_access']}"])
                return 0
            if args.project_command == "check-workstation":
                from tools.aipos_cli.loop_driver import check_workstation
                from tools.aipos_cli.workspace_config import resolve_project_root

                report = check_workstation(resolve_project_root(home, args.name), args.instance, args.harness)
                if getattr(args, "json", False):
                    print(render_json(report))
                else:
                    for item in report["checks"]:
                        print(f"{'✓' if item['ok'] else '✗'} {item['check']}: {item['detail']}")
                    print(f"check-workstation {args.instance}: {'OK' if report['ok'] else 'FAILED'}")
                return 0 if report["ok"] else 1
            if args.project_command == "set-repo":
                # AIPOS-F24 大项A: 薄壳模式
                from tools.aipos_cli.confirm_client import (
                    GateAddressError,
                    GateClient,
                    GateError,
                    declared_rpc_url,
                    load_gate_client_token,
                    resolve_gate_base_url,
                )
                from tools.aipos_cli.workspace_config import CardRepoUnresolved, update_project_repo

                owner_auth_ref = getattr(args, "owner_authorization_ref", None)
                conn_path = workspace_connection_path(Path.home())
                if conn_path.exists() and not owner_auth_ref:
                    print("Error: --owner-authorization-ref is required on the gate path (owner-gated)", file=sys.stderr)
                    return 1
                target = _project_write_target(args, home, args.name)  # AIPOS-F127 件①: 与所在治理根冲突 = 拒(预演前)
                if target is None:
                    return 1
                # AIPOS-F125: 缺省预演 = 本机经唯一实现算 project.json diff + 校验(零写入; 门路径亦不取凭据、不调门动词);
                # --confirm 才写(本地降级直写 / 门路径经 dry_run → confirm 门动词)。
                try:
                    preview = update_project_repo(home, args.name, args.code_repo, registered_by=args.actor, dry_run=True)
                except (CardRepoUnresolved, FileNotFoundError) as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                details = [f"code_repo = {preview['code_repo']}"]

                def _emit_set_repo(outcome: dict[str, Any], via: str = "") -> int:
                    _project_json_two_phase_emit(
                        "set-repo", target, {k: outcome[k] for k in ("project_json", "dry_run", "changed", "written", "diff")},
                        json_out=False, details=details, via=via)
                    return 0

                def _local_write() -> int:
                    try:
                        written = update_project_repo(home, args.name, args.code_repo, registered_by=args.actor)
                    except (CardRepoUnresolved, FileNotFoundError) as exc:
                        print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                        return 1
                    return _emit_set_repo(written, " (local fallback)")

                if not args.confirm:
                    return _emit_set_repo(preview)
                if not conn_path.exists():
                    return _local_write()  # 降级

                # AIPOS-F106 件①: 凭据文件坏 = 拒(fail-closed); 未声明 mcp.rpc_url = 降级本地; 门基址/凭据角色序走唯一推导口
                try:
                    declared = declared_rpc_url(conn_path)
                except GateAddressError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                if declared is None:
                    return _local_write()  # 降级

                try:
                    base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
                    _token_role, token = load_gate_client_token(conn_path)
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
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
                except GateError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                # 门侧经同一实现写入; 本机所示 diff = 写前预演(门与本机同读 home 根下该 project.json)
                return _emit_set_repo({**preview, "dry_run": False, "written": preview["changed"]}, " (via gate verb)")
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
                    # AIPOS-F78C: 项目级看板列全部仓(repos 清单; 单仓项目只有 code_repo 别名)
                    from tools.aipos_cli.workspace_config import CardRepoUnresolved as _RepoDeclError, project_repos as _project_repos
                    try:
                        _repos = _project_repos(project_root)
                        repos_view = {name_: str(path_) for name_, path_ in _repos["items"].items()}
                        repos_default = _repos["default"]
                    except _RepoDeclError as exc:
                        repos_view, repos_default = {"<invalid>": str(exc)}, None
                    result["projects"].append({
                        "name": name,
                        "project_root": str(project_root),
                        "code_repo": project_json.get("code_repo"),
                        "repos": repos_view,
                        "repos_default": repos_default,
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
                        if proj.get("repos"):
                            print(f"   Repos ({len(proj['repos'])}, default={proj.get('repos_default')}): " + ", ".join(f"{k}={v}" for k, v in proj["repos"].items()))
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
                sub = getattr(args, "dispatch_mode_command", None)
                if sub == "set":
                    # AIPOS-F127 件①: 写 project.json 的目标 = --project-root / 所在治理根(同一解析), 禁回落 home 级活动项目
                    target = _project_write_target(args, None, None, workspace_root=proj_root)
                    if target is None:
                        return 1
                    proj_root = str(target["project_root"])
                elif not proj_root:
                    try:
                        proj_root = str(_find_repo_root_for_args(args))  # AIPOS-F144: 唯一薄壳(全局 --workspace-root > 所在治理根 > 拒)
                    except (FileNotFoundError, OSError) as exc:
                        print(f"Error: {exc}", file=sys.stderr)
                        print("Hint: provide --project-root or run from within a project.", file=sys.stderr)
                        return 1
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
                    # AIPOS-F127 件①: 缺省输出写进目标治理根 → 隐式目标同写命令解析(所在治理根, 禁回落 home 级活动项目)
                    target = _project_write_target(args, None, None)
                    if target is None:
                        return 1
                    ws_root = str(target["project_root"])
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
                print(f"home 根: {home}(来源: {home_source})", file=sys.stderr)
                result = import_project_structure(
                    args.structure_file,
                    home,
                    name=args.name,
                    dry_run=args.dry_run,
                    actor=args.actor,
                )
                if args.json:
                    print(render_json(result))
                else:
                    if result.get("ok"):
                        if args.dry_run:
                            print(f"Dry-run: would create project '{result['project_name']}' at {result['project_root']} (lybra project new 同一实现)")
                            print(f"  code_repo: {result['code_repo']}")
                            print(f"  Migration items: {result['migration_item_count']}")
                        else:
                            print(f"Imported project '{result['project_name']}' to {result['project_root']} (lybra project new 同一实现)")
                            print(f"  project.json: {result['project_json']}")
                            print(f"  Migration checklist: {result['migration_checklist']} ({result['migration_item_count']} items)")
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

    if args.command == "regression":
        # AIPOS-F149 件③: 只读薄壳——治理根经唯一薄壳 _find_repo_root_for_args, sha 解析与判据在 post_merge_regression(唯一实现)
        if getattr(args, "regression_command", None) != "baseline":
            parser.parse_args([args.command, "--help"])
            return 2
        from tools.aipos_cli import post_merge_regression as pmr_mod
        from tools.schema_loader import SchemaLoadError

        try:
            governance_root = _find_repo_root_for_args(args)
            commit, sha_source = pmr_mod.resolve_audit_sha(governance_root, args.sha, args.repo_root)
        except (FileNotFoundError, ValueError, OSError, SchemaLoadError) as exc:  # 拒因原文 + 出口, 非 0 退出(fail-closed)
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        result = pmr_mod.audit_baseline(governance_root, commit)
        result["sha_source"] = sha_source
        print(render_json(result) if args.json else pmr_mod.render_audit_baseline(result))
        return 0

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

        # Resolve governance root
        if args.governance_root:
            governance_root = Path(args.governance_root).expanduser().resolve()
        elif args.global_workspace_root:
            governance_root = Path(args.global_workspace_root).expanduser().resolve()
        else:
            # AIPOS-F144: 缺省治理根经读写共用唯一实现(所在治理根 > 拒, 不回落 home 级活动项目); 本命令的子命令级
            # --workspace-root 是产品仓根(schema 解析), 不作治理根, 故此处直调 resolve_governance_root 而非 CLI 薄壳
            from tools.aipos_cli.workspace_config import resolve_governance_root
            try:
                governance_root = resolve_governance_root(None, established=False)["project_root"]
            except ValueError as e:
                print(f"Error: Cannot auto-discover governance root: {e}", file=sys.stderr)
                return 1
        
        # Resolve repo_root (产品仓根,用于定位 schema/config.schema.json)
        # governance-commit 通常由顾问在治理仓调用,但 schema 在产品仓
        # 使用 workspace_root 参数或从环境自动发现
        if args.workspace_root:
            repo_root = Path(args.workspace_root).expanduser().resolve()
        else:
            # AIPOS-F88 件③: schema 根 = 运行中 Lybra 代码所在仓(product_repo_root() → code_repo_schema_root), 禁写死机器路径
            from tools.aipos_cli.workspace_config import product_repo_root

            repo_root = product_repo_root()
        
        try:
            result = governance_commit(
                governance_root=governance_root,
                task_id=args.task_id,
                actor=args.actor,
                repo_root=repo_root,
                dry_run=args.dry_run,
                push=not args.no_push,
                message=args.message,
                paths=args.paths,
                paths_file=args.paths_file,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(result))
        else:
            # Text output
            verdict = result['verdict']
            # AIPOS-F79C 件③: 请求了 push 而没推上 → 首行就是 PUSH NOT DONE: <原因>(不得被后文淹没)
            push_result = result.get('push_result')
            if result.get('push_requested') and push_result and not push_result.get('pushed') and verdict != Verdict.PASS:
                print(f"PUSH NOT DONE: {push_result.get('reason')}")
            print(f"\n=== Governance Commit Result ===")
            print(f"Task: {result['task_id']}")
            print(f"Actor: {result['actor']}")
            print(f"Verdict: {verdict}")
            print(f"\nMessage: {result['message']}")
            
            # AIPOS-F79 件②: 文件清单(dry-run 为将提交; 正式提交为已提交并经 git show 校验)
            manifest = result.get('commit_manifest')
            if manifest:
                print(f"\n=== 文件清单 ({manifest['mode']}, {manifest['counts']['total']} files) ===")
                for f in manifest['files']:
                    print(f"  {f['status']:<18} {f['path']}")
                for w in manifest.get('warnings', []):
                    print(f"  ! {w}")
            
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
            elif result.get('push_requested') and push_result and not push_result.get('pushed') and verdict != Verdict.PASS:
                print(f"✗ PUSH NOT DONE: {push_result.get('reason')}")
            
            print("\n=== Next Steps ===")
            if verdict == Verdict.PASS:
                if result.get('committed') and result.get('pushed'):
                    print("N6 收账完成,治理记录已同步")
                elif result.get('committed'):
                    print("已 commit,但未 push (use --no-push to skip push)")
                else:
                    print("无待收内容, 治理仓已最新 (no-op, EXIT=0)")
            elif verdict == Verdict.BLOCK:
                if result.get('push_requested') and push_result and not push_result.get('pushed'):
                    print("推送未完成: 按上方 Message 的可执行出口处理后重跑同一命令(本地 commit 已保留)")
                else:
                    print("请补充缺失的收账文件后重试")
            else:
                print("操作失败,请查看错误信息")
        
        # AIPOS-F79C 件③: pushed=False(请求了 push 时)已由 governance_commit 封口为 verdict != PASS → 非零退出
        return 0 if result['verdict'] == Verdict.PASS else 1

    if args.command == "brief":
        # AIPOS-F67: lybra brief — 顾问真相派生(冷启动简报算出来,不写出来)
        from tools.aipos_cli.brief import run_brief

        # AIPOS-F144: 治理根经唯一薄壳(全局/子命令级 --workspace-root 同义 > 环境变量 > 所在治理根 > 工位声明 > 拒);
        # 原缺省 = 当前目录(不在治理根内也照读)退役
        try:
            workspace_root = _find_repo_root_for_args(args)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        repo_root = getattr(args, "repo_root", None)
        if repo_root:
            repo_root = Path(repo_root).expanduser().resolve()
        
        output_format = "json" if getattr(args, "json", False) else "text"
        since = getattr(args, "since", None)
        
        return run_brief(
            workspace_root=workspace_root,
            repo_root=repo_root,
            output_format=output_format,
            since=since,
            lane=getattr(args, "lane", None),
            include_frozen=bool(getattr(args, "include_frozen", False)),  # AIPOS-F141 件①
        )

    if args.command == "governance":
        # AIPOS-A1 大项A: governance add 子命令族(产生侧治理写入 CLI)
        from tools.aipos_cli.governance_add import (
            add_decision, add_stage, add_doc, add_record, list_declarations,
        )

        gov_cmd = getattr(args, "governance_command", None)

        if gov_cmd == "list-declarations":
            if getattr(args, "workspace_root", None):
                repo_root = Path(args.workspace_root).expanduser().resolve()
            else:
                # AIPOS-F88 件③: schema 根 = product_repo_root()(运行代码所在仓), 禁写死机器路径
                from tools.aipos_cli.workspace_config import product_repo_root

                repo_root = product_repo_root()
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
        if getattr(args, "workspace_root", None):
            repo_root = Path(args.workspace_root).expanduser().resolve()
        else:
            # AIPOS-F88 件③: schema 根 = product_repo_root()(运行代码所在仓), 禁写死机器路径
            from tools.aipos_cli.workspace_config import product_repo_root

            repo_root = product_repo_root()

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
            # (precedence: LYBRA_WORKSPACE_ROOT env → .lybra/config.json → 5_tasks/queue marker)
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
            # AIPOS-F135 件②: 推送结果如实标注(pushed / already_synced / not_applicable / not_requested; 声明 transitions N5.record.push_status)
            if result.get('push_status'):
                print(f"Push: push_status={result['push_status']}")
            
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
                elif result.get('committed') and result.get('push_status') == "not_applicable":
                    print("✓ Changes committed locally; push not applicable (no origin remote / tracking branch).")
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

    if args.command == "my-tasks":
        workstation_rc = _resolve_my_tasks_workstation(args)
        if workstation_rc is not None:
            return workstation_rc

    # AIPOS-F92 件②: 接入向导在新项目建成前就要能跑(顾问会话目录不是治理工作区), 不解析工作区根
    if args.command == "onboarding":
        if not getattr(args, "onboarding_command", None):
            parser.print_help()
            return 2
        from tools.aipos_cli.onboarding import (
            generate_onboarding_guide,
            format_guide_text,
            validate_step_prerequisites,
        )
        if args.onboarding_command == "guide":
            try:
                guide = generate_onboarding_guide(
                    args.project_name,
                    home_root=args.home_root,
                    gate_url=args.gate_url,
                    code_repo=args.code_repo,
                    actor=args.actor,
                    workspace_dir=args.workspace_dir,
                    repos=args.repos,
                    default_repo=args.default_repo,
                    advisor_dir=args.advisor_dir,
                    auditor_dir=args.auditor_dir,
                    host_segment=args.host_segment,
                    advisor_harness=args.advisor_harness,
                    advisor_host=args.advisor_host,
                    executor_mode=args.executor_mode,
                    owner_workspace=args.owner_workspace,
                    owner_connection_json=args.owner_connection_json,
                    envelope_days=args.envelope_days,
                    max_tasks=args.max_tasks,
                )
            except ValueError as exc:  # AIPOS-F129: 顾问 harness 取值/会话所在机违反声明 = 拒(原文给出口)
                print(f"Error: {exc}", file=sys.stderr)
                return 2
            if args.json:
                print(render_json(guide))
            else:
                print(format_guide_text(guide))
            return 0
        elif args.onboarding_command == "check":
            result = validate_step_prerequisites(
                args.step,
                project_name=args.project_name,
                home_root=args.home_root,
                workspace_dir=args.workspace_dir,
            )
            if args.json:
                print(render_json(result))
            else:
                if result["ok"]:
                    print(f"✓ Step {args.step} prerequisites satisfied")
                else:
                    print(f"✗ Step {args.step} prerequisites not satisfied")
                    print(f"Missing: {', '.join(result['missing'])}")
                    if result["guidance"]:
                        print("\nGuidance:")
                        for g in result["guidance"]:
                            print(f"  - {g}")
            return 0 if result["ok"] else 1
        parser.print_help()
        return 2

    # AIPOS-F92 件①: 信封命令先校验参数、自行解析目标治理根(mint 用 --workspace-root / governance_workspace_root), 不先过通用工作区解析
    if args.command == "envelope":
        if not getattr(args, "envelope_command", None):
            parser.print_help()
            return 2
        if args.envelope_command == "mint":
            # AIPOS-F92 件①: 预演(--dry-run, 本地同一 writer)与执行(--confirm, 经门 owner_decision_record envelope 路径)同一 payload 构造
            policy_ids = list(args.policy_id or [])
            agents = list(args.agent_or_role or [])
            if len(policy_ids) != len(agents):
                print(f"Error: --policy-id({len(policy_ids)} 个)与 --agent-or-role({len(agents)} 个)须按顺序成对", file=sys.stderr)
                return 2
            if len(set(policy_ids)) != len(policy_ids):
                print(f"Error: --policy-id 重复: {policy_ids}", file=sys.stderr)
                return 2
            payloads = [
                _envelope_mint_payload(
                    policy_id=pid, agent_or_role=agent, max_tasks=args.max_tasks, task_mode=args.task_mode,
                    expires_at=args.expires_at, decision_summary=args.decision_summary, actor=args.actor,
                    launch_harnesses=list(getattr(args, "launch_harness", None) or []),
                    lane_repo=getattr(args, "lane_repo", None),
                )
                for pid, agent in zip(policy_ids, agents)
            ]
            try:
                envelope_root = governance_workspace_root(getattr(args, "workspace_root", None) or None)
            except FileNotFoundError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.confirm:
                return _envelope_mint_via_gate(
                    payloads, governance_root=envelope_root, actor=args.actor,
                    connection_json=getattr(args, "connection_json", None), token_role=args.token_role,
                    json_output=bool(args.json),
                )
            results = []
            for payload in payloads:
                try:
                    results.append(record_owner_decision(payload, dry_run=True, repo_root=envelope_root, actor=args.actor))
                except (FileNotFoundError, OSError, ValueError) as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
            print(render_json(results[0] if len(results) == 1 else {"ok": all(r.get("ok") for r in results), "dry_run": True, "results": results}))
            return 1 if any(r.get("verdict") == Verdict.BLOCK for r in results) else 0
        
        elif args.envelope_command == "revoke":
            try:
                repo_root = _find_repo_root_for_args(args)
            except FileNotFoundError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
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
            try:
                repo_root = _find_repo_root_for_args(args)
            except FileNotFoundError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
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

    if args.command == "charter":
        # AIPOS-F138 件①: 只读薄壳 — 全部逻辑在 charter_render(渲染唯一 render_charter)。AIPOS-F144: 先于下方通用治理根解析分派,
        # 治理根由 run_charter_cli 经同一薄壳解析, 解析不出走本命令声明的退出码(lybra_charter.exit_codes.unreadable)
        from tools.aipos_cli.charter_render import run_charter_cli
        return run_charter_cli(args)

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

    # AIPOS-F123 件①: queue adopt —— 经门收编(预览 = preview_only 单跳零写入; --confirm = 一段式信封放行, 与 claim 同一薄壳工厂)
    if args.command == "queue" and getattr(args, "queue_command", None) == "adopt":
        from tools.aipos_cli.two_phase_shell_factory import (
            execute_single_phase_via_gate,
            execute_two_phase_verb,
            resolve_driver_role_from_connection,
        )

        conn_json_path = _queue_gate_connection_json(args, repo_root)
        if not conn_json_path:
            print("Error: queue adopt needs connection.json with the driver token (use --connection-json or set LYBRA_CONNECTION_JSON)", file=sys.stderr)
            return 1
        verb_args = {
            "task_id": args.task_id,
            "branch": args.branch,
            "actor": args.actor,
            "owner_policy_ref": args.owner_policy_ref,
            "autonomy_mode": "PreAuthorized",
        }
        if getattr(args, "workspace_root", None) or getattr(args, "global_workspace_root", None):
            verb_args["workspace_root"] = str(repo_root)  # AIPOS-F144: 显式给出(全局或子命令级)= 唯一薄壳解析结果交门
        try:
            role = resolve_driver_role_from_connection(connection_json_path=conn_json_path, repo_root=repo_root)
        except ValueError as exc:
            print(f"Error resolving role: {exc}", file=sys.stderr)
            return 1
        json_out = bool(getattr(args, "json", False))
        if getattr(args, "confirm", False):
            exit_code, resp = execute_two_phase_verb(verb_base="lybra_queue_adopt", args_dict=verb_args,
                                                     connection_json_path=conn_json_path, role=role, json_output=json_out)
        else:
            exit_code, resp = execute_single_phase_via_gate(verb_name="lybra_queue_adopt_dry_run",
                                                            args_dict={**verb_args, "preview_only": True},
                                                            connection_json_path=conn_json_path, role=role, json_output=json_out)
        if not json_out and isinstance(resp, dict):
            print(render_adopt_summary(resp))
        if exit_code == 0 and isinstance(resp, dict) and resp.get("verdict") == Verdict.BLOCK:
            return 1  # 预览即见拒因: 与 --confirm 同一非零出口(门应答原样, 不另判)
        return exit_code

    # AIPOS-F22 大项B: queue claim --confirm（薄壳工厂模式）
    if args.command == "queue" and getattr(args, "queue_command", None) == "claim" and getattr(args, "confirm", False):
        from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb

        # 解析 connection.json 路径
        conn_json_path = _queue_gate_connection_json(args, repo_root)
        if not conn_json_path:
            print("Error: --confirm needs connection.json (use --connection-json or set LYBRA_CONNECTION_JSON)", file=sys.stderr)
            return 1
        
        # AIPOS-F90 件①(缺陷①②): PreAuthorized 一段式必带覆盖驱动方的信封 policy_id——缺即本地拒(exit 5 无信封出口),
        # 不发到门再被误报成 Supervised 拒因; 原缺省写死的信封 id 退役(换项目即错)
        autonomy_mode = getattr(args, "autonomy_mode", None) or "Supervised"
        owner_policy_ref = str(getattr(args, "owner_policy_ref", None) or "").strip()
        if autonomy_mode == "PreAuthorized" and not owner_policy_ref:
            from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract

            print("Error: --autonomy-mode PreAuthorized 须带 --owner-policy-ref <覆盖驱动方与本卡的信封 policy_id>"
                  "(信封目录 = project.json paths.policies_root; 推进请用 `lybra loop --task-id <卡ID>`, 推导核自动带上)", file=sys.stderr)
            return exit_code_for(load_loop_contract(), "no_envelope")
        # 构造动词参数（按 verbs.schema 的 lybra_queue_claim_dry_run）
        verb_args = {
            "task_id": getattr(args, "task_id", None),
            "task_path": getattr(args, "path", None),
            "actor": args.actor,
            "agent_instance": getattr(args, "agent_instance", None) or args.actor,
            "autonomy_mode": autonomy_mode,
            "owner_policy_ref": owner_policy_ref,
        }
        if getattr(args, "active_session_id", None):
            verb_args["active_session_id"] = args.active_session_id
        
        # AIPOS-F78 前置零①(F73D 活体实撞: 薄壳按卡角色类选 executor/auditor token, 二者已无账务 scope → SCOPE_DENIED):
        # 账务动词一律驱动方 token(roles.schema driver.role_class), actor/agent_instance=卡实例(verb_args 已按参数带入)。
        from tools.aipos_cli.two_phase_shell_factory import resolve_driver_role_from_connection
        
        try:
            role = resolve_driver_role_from_connection(connection_json_path=conn_json_path, repo_root=repo_root)
        except ValueError as exc:
            print(f"Error resolving role: {exc}", file=sys.stderr)
            return 1
        
        exit_code, _ = execute_two_phase_verb(
            verb_base="lybra_queue_claim",
            args_dict=verb_args,
            connection_json_path=conn_json_path,
            role=role,
            json_output=getattr(args, "json", False),
        )
        return exit_code
    
    # AIPOS-F61 大项①: CLI `queue complete` 收敛到 close_task 单一 writer。
    # 禁存在"只搬文件不落记录"的旁路——complete 与 close 走同一条路径。
    if args.command == "queue" and getattr(args, "queue_command", None) == "complete":
        from tools.aipos_cli.board_adapter import close_task as _close_task
        # 从 --report-link 自动派生 closure_evidence(禁只搬文件)
        _report_link = getattr(args, "report_link", None) or ""
        _closure_evidence = {"finalize_return_ref": _report_link} if _report_link else {}
        try:
            result = _close_task(
                task_id=getattr(args, "task_id", None),
                actor=args.actor,
                closure_evidence=_closure_evidence,
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

    if args.command == "queue" and getattr(args, "queue_command", None) in {"claim", "block", "reopen"}:
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
        # AIPOS-F78 前置零⑤: --restricted → advisor 对 claimed 卡受限 amend, 允许字段唯一声明在 card.schema restricted_amend
        restricted_fields = None
        if getattr(args, "restricted", False):
            from tools.aipos_cli.board_adapter import restricted_amend_fields
            try:
                restricted_fields = restricted_amend_fields()
            except Exception as exc:  # SchemaLoadError 等: 声明缺失即出声停
                print(f"Error: restricted amend 声明读取失败: {exc}", file=sys.stderr)
                return 1
        try:
            result = amend_task(
                task_id=args.task_id,
                actor=args.actor,
                amendments=amendments,
                amendment_reason=args.amendment_reason,
                restricted_fields=restricted_fields,
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

    if args.command == "queue" and getattr(args, "queue_command", None) == "repair":
        # AIPOS-F65C 件①: repair bad frontmatter
        from tools.aipos_cli.queue_mutation import repair_bad_frontmatter
        
        try:
            result = repair_bad_frontmatter(
                repo_root=repo_root,
                task_id=args.task_id,
                actor=args.actor,
                dry_run=args.dry_run,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        
        if args.json:
            print(render_json(result))
        else:
            print(render_json(result))
        return 1 if result.get("verdict") == "BLOCK" else 0

    if args.command == "queue" and getattr(args, "queue_command", None) == "return":
        # AIPOS-FND-1: queue return — wrap board_adapter.return_task
        # AIPOS-F33 大项C: --confirm 薄壳模式(调同一门动词, 补上"CLI只有dry-run"的历史缺口)
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
        
        # AIPOS-F22 大项B: --confirm 薄壳工厂模式（替代 AIPOS-F33 手写实现）
        # 三层(托管/工位/CLI)共用同一执行函数与同一门动词, 禁第二实现。
        if getattr(args, "confirm", False):
            from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb

            # 解析 connection.json 路径
            conn_json_path = getattr(args, "connection_json", None)
            if not conn_json_path:
                default_conn = workspace_connection_path(Path(repo_root))
                if default_conn.exists():
                    conn_json_path = str(default_conn)
                else:
                    conn_json_path = os.environ.get("LYBRA_CONNECTION_JSON")
            if not conn_json_path:
                print("Error: --confirm needs connection.json (use --connection-json or set LYBRA_CONNECTION_JSON)", file=sys.stderr)
                return 1
            
            # 构造动词参数（按 verbs.schema 的 lybra_queue_return_dry_run）
            verb_args = {
                "task_id": args.task_id,
                "actor": canonical_actor,
                "agent_instance": canonical_instance,
                "autonomy_mode": getattr(args, "autonomy_mode", None) or "Supervised",  # AIPOS-F78B 件③
                "owner_policy_ref": args.owner_policy_ref,
                "result_summary": args.result_summary,
            }
            if artifact_refs:
                verb_args["artifact_refs"] = artifact_refs
            if getattr(args, "completion_report_ref", None):
                verb_args["completion_report_ref"] = args.completion_report_ref
            if getattr(args, "active_session_id", None):
                verb_args["active_session_id"] = args.active_session_id
            if getattr(args, "actual_model", None):
                verb_args["actual_model"] = args.actual_model
            runtime_bundle = _agent_runtime_arg(args)
            if runtime_bundle is None and getattr(args, "agent_runtime", None):
                return 1
            if runtime_bundle:
                verb_args["agent_runtime"] = runtime_bundle
            
            # AIPOS-F78 前置零①: 账务动词一律驱动方 token(roles.schema driver), actor=卡实例(claimer)
            from tools.aipos_cli.two_phase_shell_factory import resolve_driver_role_from_connection
            try:
                role = resolve_driver_role_from_connection(connection_json_path=conn_json_path, repo_root=repo_root)
            except ValueError as exc:
                print(f"Error resolving role: {exc}", file=sys.stderr)
                return 1
            
            exit_code, _ = execute_two_phase_verb(
                verb_base="lybra_queue_return",
                args_dict=verb_args,
                connection_json_path=conn_json_path,
                role=role,
                json_output=args.json,
            )
            return exit_code
        
        # 原有路径: board_adapter.return_task (dry-run only, AIPOS-R6A F-003)
        from tools.aipos_cli.board_adapter import return_task
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
        from tools.aipos_cli.next_resolver import _driver_actor
        try:
            closure_evidence = json.loads(args.closure_evidence)
        except json.JSONDecodeError as exc:
            print(f"Error: Invalid JSON in --closure-evidence: {exc}", file=sys.stderr)
            return 1
        # AIPOS-F78B 件③: --confirm = 经门(lybra_queue_close 两阶段薄壳工厂, 驱动方 token); PreAuthorized + 信封 → 门一阶段落记录
        if getattr(args, "confirm", False):
            from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb, resolve_driver_role_from_connection

            conn_json_path = getattr(args, "connection_json", None)
            if not conn_json_path:
                default_conn = workspace_connection_path(Path(repo_root))
                conn_json_path = str(default_conn) if default_conn.exists() else os.environ.get("LYBRA_CONNECTION_JSON")
            if not conn_json_path:
                print("Error: --confirm needs connection.json (use --connection-json or set LYBRA_CONNECTION_JSON)", file=sys.stderr)
                return 1
            verb_args: dict[str, Any] = {
                "task_id": args.task_id,
                "actor": args.actor,
                "closure_evidence": closure_evidence,
                "autonomy_mode": getattr(args, "autonomy_mode", None) or "Supervised",
            }
            if getattr(args, "owner_policy_ref", None):
                verb_args["owner_policy_ref"] = args.owner_policy_ref
            try:
                role = resolve_driver_role_from_connection(connection_json_path=conn_json_path, repo_root=repo_root)
            except ValueError as exc:
                print(f"Error resolving role: {exc}", file=sys.stderr)
                return 1
            exit_code, _ = execute_two_phase_verb(
                verb_base="lybra_queue_close",
                args_dict=verb_args,
                connection_json_path=conn_json_path,
                role=role,
                json_output=args.json,
            )
            return exit_code
        try:
            result = close_task(
                task_id=args.task_id,
                actor=args.actor,
                closure_evidence=closure_evidence,
                conclusion_note=getattr(args, "conclusion_note", None),
                # AIPOS-F73E 件②: 本地薄壳无 token, 提交身份=工位声明的驱动方实例(_driver_actor 单一实现); actor 仍=认领实例
                submitted_by=_driver_actor(repo_root),
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

    # AIPOS-F73C件⑤: queue rework — two-phase shell (复用 two_phase_shell_factory)
    if args.command == "queue" and getattr(args, "queue_command", None) == "rework":
        if not args.confirm:
            # 没有 --confirm，返回用法提示
            print("Error: queue rework requires --confirm flag (two-phase via MCP gate)", file=sys.stderr)
            print("Usage: lybra queue rework --task-id <ID> --actor <advisor> --verdict-ref <ref> \\", file=sys.stderr)
            print("         --focus-items '[...]' --acceptance-criteria '[...]' --connection-json <path> --confirm", file=sys.stderr)
            return 1
        
        # 解析 JSON 参数
        try:
            focus_items = json.loads(args.focus_items)
            if not isinstance(focus_items, list):
                raise ValueError("focus_items must be a JSON array")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Error: Invalid --focus-items JSON: {exc}", file=sys.stderr)
            return 1
        
        try:
            acceptance_criteria = json.loads(args.acceptance_criteria)
            if not isinstance(acceptance_criteria, list):
                raise ValueError("acceptance_criteria must be a JSON array")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Error: Invalid --acceptance-criteria JSON: {exc}", file=sys.stderr)
            return 1
        
        from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb, resolve_role_from_connection
        
        # 解析 connection.json 路径
        conn_json_path = getattr(args, "connection_json", None)
        if not conn_json_path:
            default_conn = workspace_connection_path(Path(repo_root))
            if default_conn.exists():
                conn_json_path = str(default_conn)
            else:
                print(f"Error: --connection-json required (default {default_conn} not found)", file=sys.stderr)
                return 1
        
        # 派生 required_role_class: queue_rework 是 advisor scope
        required_role_class = "advisor"
        
        try:
            role = resolve_role_from_connection(
                connection_json_path=conn_json_path,
                required_role_class=required_role_class,
                repo_root=repo_root,
            )
        except ValueError as exc:
            print(f"Error resolving advisor role: {exc}", file=sys.stderr)
            return 1
        
        # 构建动词参数
        args_dict = {
            "task_id": args.task_id,
            "actor": args.actor,
            "verdict_ref": args.verdict_ref,
            "focus_items": focus_items,
            "acceptance_criteria": acceptance_criteria,
        }
        
        # 调用两阶段薯壳
        exit_code, response = execute_two_phase_verb(
            verb_base="lybra_queue_rework",
            args_dict=args_dict,
            connection_json_path=conn_json_path,
            role=role,
            json_output=args.json,
        )
        
        if args.json:
            print(render_json(response))
        else:
            # 简单文本输出
            if response.get("error"):
                print(f"Error: {response['error']}", file=sys.stderr)
            elif response.get("result"):
                result_data = response["result"]
                print(f"Task: {args.task_id}")
                print(f"Action: queue_rework")
                print(f"Status: {result_data.get('status', 'unknown')}")
                if result_data.get("amendment_id"):
                    print(f"Amendment ID: {result_data['amendment_id']}")
        
        return exit_code

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

    # AIPOS-F142 件①: loop(含 loop status)与 next 是自含薄壳, 不用下方全量校验报告(load_all_tasks + load_records + validate_tasks,
    # lybra 治理根 1000+ 卡约 5 秒)——先于它分派, 免每次查询白读全部卡与记录。分派体原样搬移, 逻辑不变
    # AIPOS-F71: lybra next — 唯一推导实现
    # AIPOS-F73件②③: --run 机器扣扳机 (推导 + 执行)
    if args.command == "loop":
        if getattr(args, "loop_action", None) == "status":
            # AIPOS-F131 件②: 只读薄壳 — 全部逻辑在 loop_run_record(判据唯一 judge_run)
            from tools.aipos_cli.loop_run_record import loop_status_cli
            return loop_status_cli(args)
        # AIPOS-F73D: 顾问侧驱动器薄壳 — 全部逻辑在 loop_driver(复用 next_resolver + agent_watch_fs + autonomy_policy)
        from tools.aipos_cli.loop_driver import run_loop_cli
        return run_loop_cli(args)

    if args.command == "next":
        from tools.aipos_cli.next_resolver import derive_next_step, scan_project_view, format_output, format_scan_output

        try:
            ws_root = _find_repo_root_for_args(args)  # AIPOS-F144: 子命令级/全局 --workspace-root 合并 + 首行标注, 唯一薄壳
            json_mode = getattr(args, "json", False)
            run_mode = getattr(args, "run", False)

            if args.task_id and getattr(args, "lane", None):
                from tools.aipos_cli.machine_zone import lane_view_declaration

                print("Error: --lane 只用于项目扫描(无 --task-id); 单卡推导不按 lane 过滤", file=sys.stderr)
                return int(lane_view_declaration()["invalid_lane_exit_code"])
            if args.task_id:
                # 单卡模式
                result = derive_next_step(args.task_id, ws_root)
                
                if run_mode and result.get("derivable"):
                    # AIPOS-F73件③: 推导后立即执行
                    from tools.aipos_cli.next_resolver import execute_derived_action
                    conn_json = getattr(args, "connection_json", None)
                    exec_result = execute_derived_action(result, ws_root, conn_json)
                    if json_mode:
                        print(render_json(exec_result))
                    else:
                        print(f"Action: {exec_result.get('action_type', '?')}")
                        if exec_result.get("ok"):
                            print(f"✓ {exec_result.get('message', 'Success')}")
                        else:
                            print(f"✗ {exec_result.get('message', 'Failed')}", file=sys.stderr)
                    return 0 if exec_result.get("ok") else 1
                else:
                    print(format_output(result, json_mode=json_mode))
                    return 0 if result.get("derivable") else 1
            else:
                # 项目级扫描(AIPOS-F133 件②: --lane 经 machine_zone.resolve_lane_filter 校验后交同一过滤函数)
                from tools.aipos_cli.machine_zone import LaneFilterInvalid, lane_view_declaration, resolve_lane_filter

                try:
                    lane = resolve_lane_filter(Path(ws_root), getattr(args, "lane", None))
                except LaneFilterInvalid as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return int(lane_view_declaration()["invalid_lane_exit_code"])
                # AIPOS-F141: 扫描经唯一可见卡入口(machine_zone.visible_cards; 冻结卡缺省不列、--lane 只列所选), 汇总行同一渲染
                view = scan_project_view(ws_root, lane=lane, include_frozen=bool(getattr(args, "include_frozen", False)))
                print(format_scan_output(view["rows"], json_mode=json_mode, lane=lane, view=view))
                return 0
        except Exception as exc:
            print(f"Error in lybra next: {exc}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return 1

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
            ws_root = repo_root  # AIPOS-F144: 已经唯一薄壳 _find_repo_root_for_args 解析(子命令级/全局 --workspace-root 同义)
            result = run_state_lint(
                governance_root=Path(ws_root),
                task_id_filter=getattr(args, "task_id", None),
            )
            # AIPOS-F87 顺手实撞: issues 原只在文本分支赋值, `state lint --json` 走到 return 即 UnboundLocalError
            issues = result.get("issues", [])
            if getattr(args, "json", False):
                print(render_json(result))
            else:
                if not issues:
                    print(f"✓ state lint OK: {result['scanned']} 张卡扫描, 无断层")
                else:
                    print(f"✗ state lint: {len(issues)} 断层(扫描 {result['scanned']} 张卡)")
                    for issue in issues:
                        frozen_tag = " (frozen)" if issue.get("frozen") else ""
                        print(f"  - [{issue['severity']}] {issue['task_id']}{frozen_tag}: {issue['message']}")
                # AIPOS-F122 件③: 存量冻结汇总一行(全项目扫描不报冻结卡; 单卡 lint 照实报并标注)
                if result.get("frozen"):
                    if getattr(args, "task_id", None):
                        batch = result.get("frozen_batch") or {}
                        print(f"  frozen: {args.task_id} 在存量冻结清单内(批次 {batch.get('batch_id')}; 全项目扫描不报)")
                    else:
                        print(f"  frozen {result['frozen']}(存量冻结卡, 历史, 不报断层; 清单见 project.json legacy_baseline)")
            return 1 if issues else 0

        # AIPOS-C3B 大项C③: state repair
        if state_cmd == "repair":
            from tools.aipos_cli.state_lint import repair_task_state
            ws_root = repo_root  # AIPOS-F144: 已经唯一薄壳 _find_repo_root_for_args 解析(子命令级/全局 --workspace-root 同义)
            result = repair_task_state(
                governance_root=Path(ws_root),
                task_id=args.task_id,
                dry_run=getattr(args, "dry_run", False),
            )
            if getattr(args, "json", False):
                print(render_json(result))
            else:
                if result.get("unresolved"):
                    print(f"✗ 拒改 {args.task_id}: {result['message']}")
                elif result.get("repaired"):
                    print(f"✓ 已修复 {args.task_id}: {result['message']}")
                elif result.get("dry_run"):
                    print(f"(dry-run) 会修复 {args.task_id}: {result['message']}")
                else:
                    print(f"无需修复 {args.task_id}: {result['message']}")
                # AIPOS-F87 件②: 卡面规整预览/结果逐行贴出(前后两行), unresolved 原样列出
                fm_repair = result.get("frontmatter_repair") or {}
                for item in fm_repair.get("repairs", []):
                    print(f"  {fm_repair.get('card_path')} 第 {item['line']} 行 {item['key']}:")
                    print(f"    - {item['before']}")
                    print(f"    + {item['after']}")
                for reason in fm_repair.get("unresolved", []):
                    print(f"  ✗ unresolved: {reason}")
                if fm_repair.get("repair_record"):
                    print(f"  repair 记录: {fm_repair['repair_record']}")
            # AIPOS-F87 件②: 卡面无法安全规整 = 拒改, 非零退出(fail-closed)
            return 1 if result.get("unresolved") else 0

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
        # AIPOS-F66B 件①: 薄壳 — 工位项目归属过滤 / 章程声明渲染 / dry-run 全在 distribution_sync.run_sync_cli
        from tools.aipos_cli.distribution_sync import run_sync_cli

        return run_sync_cli(args)

    if args.command == "my-tasks":
        actor_report = _filter_my_tasks(report, args.actor, profiles)
        if args.json:
            output = _attach_workstation_view(_json_report(actor_report, records=records), actor_report, repo_root,
                                              actor=args.actor, requested=getattr(args, "task_id", None),
                                              remote_workstation=bool(getattr(args, "remote_workstation", False)))
            if getattr(args, "workstation_identity", None):
                output["workstation"] = {**args.workstation_identity, "governance_root": str(repo_root)}
            print(render_json(output))
        else:
            print(render_my_tasks_text(actor_report, args.actor))
        return 0

    if args.command == "needs-owner":
        # AIPOS-F133 件②: lane 视图(过滤/分组同一函数 machine_zone.filter_rows_by_lane / group_rows_by_lane)
        from tools.aipos_cli.machine_zone import LaneFilterInvalid, group_rows_by_lane, lane_view_declaration, resolve_lane_filter

        try:
            lane = resolve_lane_filter(repo_root, getattr(args, "lane", None))
        except LaneFilterInvalid as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return int(lane_view_declaration()["invalid_lane_exit_code"])
        owner_report = _filter_needs_owner(report, governance_root=repo_root, lane=lane,
                                           include_frozen=bool(getattr(args, "include_frozen", False)))
        view = owner_report["view"]
        if args.json:
            payload = _json_report(owner_report, records=records)
            lanes = {name: [t.get("task_id") for t in rows]
                     for name, rows in group_rows_by_lane(owner_report["tasks"], repo_root).items()}
            payload.update({"lane_filter": lane, "lanes": lanes,
                            "task_lanes": {str(t.get("path")): {"lane": t.get("lane"), "lane_error": t.get("lane_error")}
                                           for t in owner_report["tasks"]},
                            "frozen_hidden": view["frozen_hidden"], "unresolved_lane": view["unresolved_lane"],
                            "frozen_error": view["frozen_error"]})  # AIPOS-F141: 可见卡口径(machine_zone.visible_cards)
            print(render_json(payload))
        else:
            print(render_needs_owner_text(owner_report, groups=None if lane else group_rows_by_lane(owner_report["tasks"], repo_root),
                                          lane=lane))
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

    # AIPOS-F22 大项B: audit-verdict --confirm（薄壳工厂模式）
    if args.command == "audit-verdict" and getattr(args, "confirm", False):
        from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb

        # 解析 connection.json 路径
        conn_json_path = getattr(args, "connection_json", None)
        if not conn_json_path:
            workspace_candidate = Path(repo_root).parent if repo_root else Path.cwd()
            default_conn = workspace_connection_path(workspace_candidate)
            if default_conn.exists():
                conn_json_path = str(default_conn)
            else:
                default_conn = workspace_connection_path(Path(repo_root or Path.cwd()))
                if default_conn.exists():
                    conn_json_path = str(default_conn)
        if not conn_json_path:
            conn_json_path = os.environ.get("LYBRA_CONNECTION_JSON")
        if not conn_json_path:
            print("Error: --confirm needs connection.json (use --connection-json or set LYBRA_CONNECTION_JSON)", file=sys.stderr)
            return 1
        
        # 解析 evidence_refs
        evidence_refs = None
        if getattr(args, "evidence_refs", None):
            try:
                evidence_refs = json.loads(args.evidence_refs)
            except json.JSONDecodeError as exc:
                print(f"Error: Invalid JSON in --evidence-refs: {exc}", file=sys.stderr)
                return 1
        
        # 构造动词参数（按 verbs.schema 的 lybra_audit_verdict_dry_run）
        verb_args = {
            "reviewed_task_id": args.reviewed_task_id,
            "verdict": args.verdict,
            "autonomy_mode": getattr(args, "autonomy_mode", None) or "Supervised",  # AIPOS-F78B 件③
        }
        if getattr(args, "audit_task_id", None):
            verb_args["audit_task_id"] = args.audit_task_id
        if getattr(args, "actor", None):
            verb_args["actor"] = args.actor
        if getattr(args, "agent_instance", None):
            verb_args["agent_instance"] = args.agent_instance
        if getattr(args, "owner_policy_ref", None):
            verb_args["owner_policy_ref"] = args.owner_policy_ref
        if getattr(args, "findings_summary", None):
            verb_args["findings_summary"] = args.findings_summary
        if evidence_refs:
            verb_args["evidence_refs"] = evidence_refs
        if getattr(args, "audit_claim_id", None):
            verb_args["audit_claim_id"] = args.audit_claim_id
        if getattr(args, "audit_session_id", None):
            verb_args["audit_session_id"] = args.audit_session_id
        if getattr(args, "audit_dispatch_record_ref", None):
            verb_args["audit_dispatch_record_ref"] = args.audit_dispatch_record_ref
        if getattr(args, "reviewed_return_record_ref", None):
            verb_args["reviewed_return_record_ref"] = args.reviewed_return_record_ref
        if getattr(args, "recommended_next_action", None):
            verb_args["recommended_next_action"] = args.recommended_next_action
        if getattr(args, "owner_waiver_ref", None):
            verb_args["owner_waiver_ref"] = args.owner_waiver_ref
        
        # AIPOS-F78 前置零①: 裁决提交亦是账务动词 → 缺省驱动方 token(roles.schema driver); --token-role 显式覆盖
        verdict_role = getattr(args, "token_role", None)
        if not verdict_role:
            from tools.aipos_cli.two_phase_shell_factory import resolve_driver_role_from_connection
            try:
                verdict_role = resolve_driver_role_from_connection(connection_json_path=conn_json_path, repo_root=repo_root)
            except ValueError as exc:
                print(f"Error resolving role: {exc}", file=sys.stderr)
                return 1
        # AIPOS-F73前置②: artifact_subject 从 CLI 参数带入(推导核/ingest 由分支 tip 取)
        artifact_subject = {
            k: getattr(args, f"artifact_subject_{k}", None)
            for k in ("repository", "commit_sha", "tree_hash")
            if getattr(args, f"artifact_subject_{k}", None)
        }
        if artifact_subject:
            verb_args["artifact_subject"] = artifact_subject
        runtime_bundle = _agent_runtime_arg(args)
        if runtime_bundle is None and getattr(args, "agent_runtime", None):
            return 1
        if runtime_bundle:
            verb_args["agent_runtime"] = runtime_bundle
        exit_code, _ = execute_two_phase_verb(
            verb_base="lybra_audit_verdict",
            args_dict=verb_args,
            connection_json_path=conn_json_path,
            role=verdict_role,
            json_output=getattr(args, "json", False),
        )
        return exit_code
    
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
                connection_json_path = workspace_connection_path(workspace_candidate)
                if not connection_json_path.exists():
                    connection_json_path = workspace_connection_path(Path(repo_root or Path.cwd()))
            
            try:
                token = load_owner_token(
                    connection_json=connection_json_path,
                    role=args.token_role
                )
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f"Error reading token: {exc}", file=sys.stderr)
                return 1
            
            # AIPOS-F73前置②: gate_url 默认值解析 (修复 args.gate_url=None 时 AttributeError)
            # AIPOS-F106 件①: 门基址唯一推导口(委托 ConnectionResolver.resolve_gate_url; GateClient 自拼 MCP 路径)
            from tools.aipos_cli.confirm_client import resolve_gate_base_url

            resolved_gate_url = resolve_gate_base_url(workspace_root=workspace_root, explicit_url=args.gate_url)
            
            context = {
                "gate_url": resolved_gate_url,
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
        # AIPOS-F73前置①: 构建 artifact_subject (code 卡需要)
        artifact_subject = None
        if any([
            getattr(args, 'artifact_subject_repository', None),
            getattr(args, 'artifact_subject_commit_sha', None),
            getattr(args, 'artifact_subject_tree_hash', None),
        ]):
            artifact_subject = {
                "repository": getattr(args, 'artifact_subject_repository', ''),
                "commit_sha": getattr(args, 'artifact_subject_commit_sha', ''),
                "tree_hash": getattr(args, 'artifact_subject_tree_hash', ''),
            }
        
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
            artifact_subject=artifact_subject,
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

    if args.command == "card":
        # AIPOS-F78 件②: 单一渲染器薄壳
        if getattr(args, "card_command", None) != "render":
            print("Usage: lybra card render --task-id <ID> [--harness pi|codex|claude-code] [--stdout|--out-dir DIR]", file=sys.stderr)
            return 2
        from tools.aipos_cli.card_render import run_render_cli
        return run_render_cli(args)

    if args.command == "artifact":
        # AIPOS-F78 件③: 产物入口薄壳(校验后经既有 queue return / audit-verdict 薄壳)
        if getattr(args, "artifact_command", None) != "ingest":
            print("Usage: lybra artifact ingest --task-id <ID> [--connection-json PATH] [--dry-run]", file=sys.stderr)
            return 2
        from tools.aipos_cli.artifact_ingest import run_ingest_cli
        return run_ingest_cli(args)

    if args.command == "next-step":
        print("[RETIRED] 'lybra next-step' is retired by AIPOS-F71. Use 'lybra next --task-id <ID>' instead.", file=sys.stderr)
        from tools.aipos_cli.next_resolver import derive_next_step, format_output
        ws_root = getattr(args, "workspace_root", None) or _find_repo_root_for_args(args)
        json_mode = getattr(args, "json", False)
        result = derive_next_step(args.task_id, ws_root)
        print(format_output(result, json_mode=json_mode))
        return 0 if result.get("derivable") else 1

    # AIPOS-F22 大项B: task-progress --confirm（薄壳工厂模式，经 gate MCP）
    if args.command == "task-progress" and getattr(args, "confirm", False):
        from tools.aipos_cli.two_phase_shell_factory import execute_single_phase_via_gate

        # 解析 connection.json 路径
        conn_json_path = getattr(args, "connection_json", None)
        if not conn_json_path:
            default_conn = workspace_connection_path(Path(repo_root))
            if default_conn.exists():
                conn_json_path = str(default_conn)
            else:
                conn_json_path = os.environ.get("LYBRA_CONNECTION_JSON")
        if not conn_json_path:
            print("Error: --confirm needs connection.json (use --connection-json or set LYBRA_CONNECTION_JSON)", file=sys.stderr)
            return 1
        
        # 构造动词参数（按 verbs.schema 的 lybra_task_progress）
        verb_args = {
            "task_id": args.task_id,
            "event_type": args.event_type,
            "actor": args.actor,
        }
        if getattr(args, "summary", None):
            verb_args["summary"] = args.summary
        if getattr(args, "model_self_reported", None):
            verb_args["model_self_reported"] = args.model_self_reported
        if getattr(args, "stage", None):
            verb_args["stage"] = args.stage
        if getattr(args, "reason", None):
            verb_args["reason"] = args.reason
        
        # AIPOS-F44D-A: 角色解析不写死
        # task_progress 需要 task_progress scope（executor/auditor 都有）
        from tools.aipos_cli.two_phase_shell_factory import resolve_role_from_connection
        try:
            role = resolve_role_from_connection(
                connection_json_path=conn_json_path,
                required_role_class="executor",  # 默认 executor，如果不存在则尝试 auditor
                repo_root=repo_root,
            )
        except ValueError:
            # 降级尝试 auditor (因为 auditor 也有 task_progress scope)
            try:
                role = resolve_role_from_connection(
                    connection_json_path=conn_json_path,
                    required_role_class="auditor",
                    repo_root=repo_root,
                )
            except ValueError as exc:
                print(f"Error resolving role: {exc}", file=sys.stderr)
                return 1
        
        exit_code, _ = execute_single_phase_via_gate(
            verb_name="lybra_task_progress",
            args_dict=verb_args,
            connection_json_path=conn_json_path,
            role=role,
            json_output=getattr(args, "json", False),
        )
        return exit_code
    
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

