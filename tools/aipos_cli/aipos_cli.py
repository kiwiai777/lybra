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
    apply_signer_created_by,
    build_template_payload,
    create_draft,
    load_body_file,
    load_create_payload_from_json,
    publish_draft,
    render_revision_lines,
    resolve_publish_signer,
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


def _owner_viewer_for_args(governance_root: Path, args: Any, lane: str | list[str] | None) -> dict[str, Any] | None:
    """AIPOS-F156 件②: 三视图(next 项目扫描 / brief / needs-owner)的归属视角——唯一判定 machine_zone.owner_viewer
    (--actor 给出 = 该实例; 否则 --lane 且其有效信封恰属一个实例 = 该实例; 都无 = None 不按归属分)。"""
    from tools.aipos_cli.machine_zone import owner_view_declaration, owner_viewer

    actor = getattr(args, str(owner_view_declaration()["dest"]), None)
    return owner_viewer(Path(governance_root), actor=actor, lane=lane)


def _filter_needs_owner(report: dict[str, Any], *, governance_root: Path | None = None, lane: str | list[str] | None = None,
                        include_frozen: bool = False, viewer: dict[str, Any] | None = None) -> dict[str, Any]:
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

    view = visible_cards(governance_root, ((task, task.get("metadata")) for task in filtered), lane=lane, include_frozen=include_frozen,
                         viewer=viewer)  # AIPOS-F156 件②: 归属视角同一判定
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


def _board_login_target(args: argparse.Namespace) -> tuple[Path, str, Path | None]:
    """AIPOS-F185 件④(gap #168): ``board open`` / ``board approve`` 的治理根与凭据文件(唯一定位 board_login.locate_connection_json)。
    治理根经 F144 唯一薄壳 _find_repo_root_for_args(全局/子命令级 --workspace-root > 环境变量 > 所在治理根 > 工位声明); 显式
    --connection-json 时治理根可解析不出(看板地址按 connection.json / 缺省)。返回 (凭据文件, 来源说明, 治理根 | None);
    定位不到 = board_login.ConnectionLocateError(人话拒因)。"""
    from tools.aipos_cli.board_login import locate_connection_json

    root: Path | None = None
    unresolved = ""
    try:
        root = Path(_find_repo_root_for_args(args))
    except FileNotFoundError as exc:  # F144 拒因原文(GOVERNANCE_ROOT_UNRESOLVED / CONFLICT)进定位拒因
        unresolved = str(exc)
    hit = getattr(args, "_governance_root_hit", None) or {}
    path, how = locate_connection_json(getattr(args, "connection_json", None), governance_root=root,
                                       root_source=str(hit.get("source") or ""), unresolved=unresolved)
    return path, how, root


def _run_board_open(args: argparse.Namespace) -> int:
    """AIPOS-271 ``board open``:读 connection.json(文件权即身份)铸 OTC → 开/打看板登录链接。
    
    F-271-1: 支持免 --workspace-root(单工作区自动发现:按当前目录或环境变量)。
    """
    import webbrowser

    from tools.aipos_cli.board_login import (
        ConnectionLocateError,
        build_login_url,
        load_role_token,
        mint_otc,
        resolve_board_url,
        token_fingerprint,
    )

    role = getattr(args, "role", None)
    try:
        connection_json, how, workspace_root = _board_login_target(args)  # AIPOS-F185 件④: F144 治理根解析梯
    except ConnectionLocateError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        token, role_used = load_role_token(connection_json, role=role)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: 读取 connection.json 失败 —— {exc}", file=sys.stderr)
        print("提示:用 --connection-json 指定路径,或 --role 指定角色。", file=sys.stderr)
        return 1

    base_url = resolve_board_url(
        connection_json,
        url=getattr(args, "url", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        workspace_root=workspace_root,
    )
    print(f"凭据文件: {connection_json}({how})")
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
        ConnectionLocateError,
        approve_device,
        load_role_token,
        resolve_board_url,
        token_fingerprint,
    )

    role = getattr(args, "role", None)
    try:
        connection_json, how, workspace_root = _board_login_target(args)  # AIPOS-F185 件④: F144 治理根解析梯
    except ConnectionLocateError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        token, role_used = load_role_token(connection_json, role=role)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: 读取 connection.json 失败 —— {exc}", file=sys.stderr)
        return 1

    base_url = resolve_board_url(
        connection_json,
        url=getattr(args, "url", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        workspace_root=workspace_root,
    )
    code = str(getattr(args, "code", "") or "")
    print(f"凭据文件: {connection_json}({how})")
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
    # AIPOS-F159 件①: 追加的清单条目(+ 本次补声明落点的 project.json)经同一落账薄壳落账; 本批无写出 = 不落
    landing = None
    if written and written.get("written"):
        landing = _land_project_writes(
            root, [written["entry"]] + ([project_json_path(root)] if written["project_json_declared"] else []),
            actor=args.actor, subcommand="freeze-legacy", project=str(target["project"]))
    rc = 0 if landing is None or landing["landed"] else int(landing["exit_code"])
    if getattr(args, "json", False):
        print(render_json({"ok": True, "confirm": bool(args.confirm), "plan": plan, "written": written,
                           **({"landing": _landing_view(landing)} if landing is not None else {})}))
        return rc
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
        print("  卡文件与记录未被移动或改写")
    else:
        print(f"  本批无需{verb}的卡(幂等), 未写清单")
    if landing is not None:
        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return rc


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


def _signer_line(signer: dict[str, Any]) -> str:
    """AIPOS-F156 件③: draft create / publish 文字输出末行 = 签发实例与来源(未归因 = 说明原文)。"""
    if signer.get("actor"):
        return f"签发实例: {signer['actor']}(来源 {signer.get('source')})"
    return f"签发实例: 未归因 — {signer.get('note')}"


def _queue_gate_connection_json(args: argparse.Namespace, repo_root: Any) -> str | None:
    """queue claim --confirm / queue adopt 的凭据文件解析(唯一): --connection-json → 治理根 .lybra/connection.json → LYBRA_CONNECTION_JSON。"""
    explicit = getattr(args, "connection_json", None)
    if explicit:
        return str(explicit)
    default_conn = workspace_connection_path(Path(repo_root))
    if default_conn.exists():
        return str(default_conn)
    return os.environ.get("LYBRA_CONNECTION_JSON") or None


def _queue_rework_preview(args: argparse.Namespace, repo_root: Any, role: str, focus_items: list[Any],
                          acceptance_criteria: list[str]) -> int:
    """AIPOS-F155 件④(gap #134): `lybra queue rework` 缺省预演——将追加的返工节由返工节唯一构建 queue_mutation.build_rework_round
    (门 queue_rework_task 同一函数; 校验 claimed / 轮次上限 + 构造, 不写盘)算出, 签发者角色类以门侧同一判定 custom_roles.authorize_instance_class
    预检(签发凭据已由调用方经 signer_role_for_actor 按 --actor 实例选定)。零写入、不调门动词、不打印凭据; 拒 = 非零退出(与确认阶段同一拒因)。"""
    from tools.aipos_cli.board_adapter import restricted_amend_actor_class
    from tools.aipos_cli.custom_roles import authorize_instance_class
    from tools.aipos_cli.queue_mutation import build_rework_round

    labels = _two_phase_family_declaration("queue_writers")["outcome_labels"]
    gate = authorize_instance_class(args.actor, restricted_amend_actor_class(), Path(repo_root))
    planned = build_rework_round(repo_root=Path(repo_root), task_id=args.task_id, verdict_ref=args.verdict_ref,
                                 focus_items=[str(x) for x in focus_items], acceptance_criteria="\n".join(acceptance_criteria),
                                 actor=args.actor)
    reasons = list(planned.get("blocking_reasons") or [])
    if not gate["ok"]:
        reasons.insert(0, f"ROLE_VIOLATION: queue_rework 只能由角色类 {gate['required_class']} 的实例执行; 实例 '{args.actor}' 未授权: {gate['reason']}")
    data = planned.get("data") or {}
    payload = {"ok": not reasons, "command": "queue rework", "phase": "dry_run", "wrote": False, "task_id": args.task_id,
               "signer_instance": args.actor, "signer_role": role, "blocking_reasons": reasons,
               "rework_round": data.get("new_round"), "round_added": data.get("round_added"), "max_rounds": data.get("max_rounds"),
               "task_path": data.get("task_path")}
    if args.json:
        print(render_json(payload))
    elif reasons:
        print(f"queue rework {args.task_id}: BLOCKED(预演, 未写): {reasons}", file=sys.stderr)
    else:
        rnd = data.get("new_round") or {}
        print(f"queue rework {args.task_id}: {labels['preview']}")
        print(f"  签发实例: {args.actor}(凭据角色 {role}; 门侧角色类 {gate.get('role_class')})")
        print(f"  卡: {data.get('task_path')}  将追加第 {data.get('round_added')} 轮(上限 {data.get('max_rounds')})")
        print(f"  依据裁决: {rnd.get('verdict_ref')}")
        for item in rnd.get("focus_items") or []:
            print(f"  点杀: {item}")
        for line in str(rnd.get("acceptance_criteria") or "").splitlines():
            print(f"  验收: {line}")
        print("  确认写入: 同一命令加 --confirm")
    return 1 if reasons else 0


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

def _owner_command(args: argparse.Namespace) -> int:
    """AIPOS-F168: `lybra owner inbox|decide` 薄壳。inbox: owner_inbox.collect_inbox + render_inbox_text(看板同一数据);
    decide: owner_inbox.decide(预演 / 确认两阶段)。退出码读 verbs.schema lybra_owner_inbox / lybra_owner_decide(落账失败读 post_write_landing)。"""
    from tools.aipos_cli.owner_inbox import (
        DECIDE_VERB,
        VERB,
        InboxItemNotFound,
        InboxUsageError,
        collect_inbox,
        decide,
        render_inbox_text,
        render_stale_text,
        split_item_ids,
        stale_view,
    )
    from tools.aipos_cli.verb_contract import declared_exit_code

    action = getattr(args, "owner_action", None)
    if action == "inbox":
        try:
            inbox = collect_inbox(getattr(args, "home_root", None), project=getattr(args, "project", None),
                                  include_paused=bool(getattr(args, "include_paused", False)))
            view = stale_view(inbox, args.stale_before) if getattr(args, "stale_before", None) else None
        except (ValueError, OSError) as exc:  # InboxUsageError(日期不合)同属用法错
            print(f"lybra owner inbox: {exc}", file=sys.stderr)
            return declared_exit_code(VERB, "usage")
        if view is not None:  # AIPOS-F170 件①: 遗留视图(同一数据)
            print(render_json(view) if args.json else render_stale_text(view))
        else:
            print(render_json(inbox) if args.json else render_inbox_text(inbox))
        return declared_exit_code(VERB, "partial" if inbox["errors"] else "ok")
    if action == "decide":
        try:
            ids = split_item_ids(getattr(args, "item", None), getattr(args, "items", None))
            report = decide(ids, args.choice, note=args.note, home_root=getattr(args, "home_root", None), actor=args.actor,
                            relayed_by=getattr(args, "relayed_by", None), owner_words=getattr(args, "owner_words", None),
                            confirm=bool(args.confirm))
        except InboxItemNotFound as exc:
            print(f"lybra owner decide: {exc}", file=sys.stderr)
            return declared_exit_code(DECIDE_VERB, "not_found")
        except InboxUsageError as exc:  # 选项不属该事项 / 缺转达人或原话 / --item 与 --items 不合(AIPOS-F170)
            print(f"lybra owner decide: {exc}", file=sys.stderr)
            return declared_exit_code(DECIDE_VERB, "usage")
        except (ValueError, OSError) as exc:
            print(f"lybra owner decide: {type(exc).__name__}: {exc}", file=sys.stderr)
            return declared_exit_code(DECIDE_VERB, "blocked")
        landings = report.get("landings") or []
        if args.json:
            print(render_json({**report, "landing": _landing_view(report.get("landing")),
                               "landings": [_landing_view(lg) for lg in landings]}))
        else:
            _print_owner_decide(report)
        if report["rc_outcome"] == "landing_failed":
            return int(next((lg.get("exit_code") for lg in landings if not lg.get("landed")), 1) or 1)
        return declared_exit_code(DECIDE_VERB, report["rc_outcome"])
    print("lybra owner: 须给子命令 inbox | decide", file=sys.stderr)
    return 2


def _print_owner_decide(report: dict[str, Any]) -> None:
    """`lybra owner decide` 文本输出(单项 / 批量同一渲染; AIPOS-F170 附转达人与 Owner 原话、冻结组)。"""
    stage = "预演(零写入; 加 --confirm 写入并落账)" if report["stage"] == "preview" else "确认"
    entries = report.get("entries") or []
    head = (f"{entries[0]['item']['item_id']} {entries[0]['item']['project']} / {entries[0]['item']['subject']}" if len(entries) == 1
            else f"批量 {len(entries)} 项")
    print(f"lybra owner decide {stage}: {head} → 选 {report['choice']}" + (f" — 备注: {report['note']}" if report.get("note") else ""))
    print(f"  转达: {report.get('relayed_by')} · Owner 原话: 「{report.get('owner_words')}」")
    for entry in entries:
        item, decision = entry["item"], entry.get("decision") or {}
        prefix = "  " if len(entries) == 1 else f"  [{item['item_id']} {item['project']} / {item['subject']}] "
        print(f"{prefix}决策记录: {decision.get('target_path') or '-'} verdict={decision.get('verdict')}")
        for reason in decision.get("blocking_reasons") or []:
            print(f"  ✗ 拒因: {reason}", file=sys.stderr)
        release = entry.get("release")
        if entry.get("effect") and entry["effect"] != "freeze_legacy":
            print(f"{prefix}解除动作 {entry['effect']}(既有队列出口): ok={bool((release or {}).get('ok'))} verdict={(release or {}).get('verdict')}")
            for reason in (release or {}).get("blocking_reasons") or []:
                print(f"  ✗ 解除拒因: {reason}", file=sys.stderr)
            for err in (release or {}).get("errors") or []:
                print(f"  ✗ 解除拒因: {err.get('message') if isinstance(err, dict) else err}", file=sys.stderr)
        if item["category"] == "envelope_warning" and report["choice"] == "renew" or (item.get("facts") or {}).get("reason") == "envelope_renew":
            if item.get("recommend"):
                print(f"  续签由 Owner 执行: {item['recommend']}")
            if item.get("board_url"):  # AIPOS-F172: Owner 也可在看板信封页亲自续签
                print(f"  或 Owner 在看板信封页续签: {item['board_url']}")
    for group in report.get("freeze") or []:
        written = group.get("written") or {}
        where = f" 清单条目 {written.get('entry')}" if written.get("written") else ""
        print(f"  解除动作 freeze_legacy(既有口 project freeze-legacy): {group['governance_root']} 卡 {', '.join(group['task_ids'])} "
              f"ok={bool(group.get('ok'))} verdict={group.get('verdict')}{where}")
        for reason in group.get("blocking_reasons") or []:
            print(f"  ✗ 冻结拒因: {reason}", file=sys.stderr)
    for path in report.get("written") or []:
        print(f"    wrote {path}")
    for landing in report.get("landings") or []:
        print(landing["line"], file=sys.stdout if landing.get("landed") else sys.stderr)


def _envelope_mint_payload(**kwargs: Any) -> dict[str, Any]:
    """信封 payload 唯一构造(AIPOS-F172: 实现收口 envelope_mint.build_mint_payload, CLI 与看板信封页同调)。"""
    from tools.aipos_cli.envelope_mint import build_mint_payload

    return build_mint_payload(**kwargs)


def _envelope_mint_via_gate(
    payloads: list[dict[str, Any]],
    *,
    governance_root: Path,
    actor: str,
    connection_json: str | None,
    token_role: str,
    json_output: bool,
) -> int:
    """`envelope mint --confirm` 出声薄壳: 签发与落账全在 envelope_mint.mint_via_gate(AIPOS-F172 收口, 看板同调), 本函数只按 --json / 文本出声。"""
    from tools.aipos_cli.envelope_mint import mint_via_gate

    report = mint_via_gate(payloads, governance_root=governance_root, actor=actor, connection_json=connection_json, token_role=token_role)
    if report["early"]:
        print(f"Error: {report['error']}", file=sys.stderr)
        return int(report["exit_code"])
    if json_output:
        print(render_json({k: v for k, v in report.items() if k not in ("exit_code", "early", "landing_line")
                           and not (k == "error" and v is None)}))
    else:
        print(f"envelope mint --confirm via {report['gate_url']} (credential {token_role} {report['credential_fingerprint']}) → {governance_root}")
        for item in report["signed"]:
            print(f"  signed {item['policy_id']} covers {item['agent_or_role']}: decision={item['decision_id']}")
            for path in item["written"]:
                print(f"    wrote {path}")
        if report["landing_line"] is not None:
            print(report["landing_line"], file=sys.stdout if (report["landing"] or {}).get("landed") else sys.stderr)
        if report["error"]:
            print(f"Error: {report['error']}", file=sys.stderr)
    return int(report["exit_code"])


def _migrate_record_type_cli(args: argparse.Namespace) -> int:
    """AIPOS-F158 件②: `lybra migrate record-type` 薄壳——全部逻辑在 record_type_migration(计划 / 改写 / 经同一提交口落账);
    本函数只解析治理根(_find_repo_root_for_args 唯一薄壳)、按两阶段声明出声。--confirm 缺 --actor = exit 2(零写入)。"""
    import subprocess

    from tools.aipos_cli.record_type_migration import MigrationError, run_record_type_migration
    from tools.schema_loader import SchemaLoadError

    decl = _two_phase_family_declaration("records_migration_writers")
    labels, item_labels = decl["outcome_labels"], decl.get("item_status_labels") or {}
    if args.confirm and not str(args.actor or "").strip():
        print("Error: --confirm 须给 --actor(落账提交身份); 未写入", file=sys.stderr)
        return 2
    try:
        root = _find_repo_root_for_args(args)
        result = run_record_type_migration(root, actor=args.actor, confirm=bool(args.confirm))
    except MigrationError as exc:
        print(f"Error: {exc}" + (f"\n出口: {exc.next_step}" if exc.next_step else ""), file=sys.stderr)
        return 1
    except (FileNotFoundError, SchemaLoadError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    landing = result.get("landing")
    rc = int(landing["exit_code"]) if landing else 0
    items = [{k: v for k, v in item.items() if k != "content"} for item in result["items"]]
    if args.json:
        print(render_json({**{k: v for k, v in result.items() if k not in ("items", "landing")}, "items": items,
                           "landing": {k: v for k, v in landing.items() if k != "line"} if landing else None}))
        return rc
    counts = result["counts"]
    outcome = (labels["done"] if result["written"] else labels["nothing"]) if result["confirm"] else labels["preview"]
    print(f"migrate record-type: {outcome} 治理根 {result['governance_root']}"
          f"(records = {result['records']})")
    print(f"  缺记录类型被 B④ 拒入: {counts['total']} 份; " + ", ".join(
        f"{item_labels.get(k, k)} {counts[k]}" for k in ("derived", "typed_not_rebuildable", "underivable")))
    for item in items:
        print(f"  [{item_labels.get(item['status'], item['status'])}] {item['path']}  缺 {item['missing_fields']}  "
              f"类型 {item.get('record_type') or '-'}  — {item.get('basis') or item.get('reason') or ''}")
    for path in result["written"]:
        print(f"  wrote {path}")
    if landing is not None:
        for op in landing.get("operations") or []:
            print(f"    · {op}")
        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return rc


def _migrate_decision_log_pointers_cli(args: argparse.Namespace) -> int:
    """AIPOS-F159 件② → AIPOS-F160 件③(gap #145): `lybra migrate decision-log-pointers` 薄壳——全部逻辑在 decision_log_pointers
    (预演 / 补生成 / 经同一提交口落账); 与 record-type 同族同口径(两阶段声明 records_migration_writers, 输出标签同读)。
    --confirm 缺 --actor = exit 2(零写入)。"""
    import subprocess

    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.decision_log_pointers import DecisionPointerError, run_pointer_backfill
    from tools.schema_loader import SchemaLoadError

    decl = _two_phase_family_declaration("records_migration_writers")
    labels, item_labels = decl["outcome_labels"], decl.get("item_status_labels") or {}
    if args.confirm and not str(args.actor or "").strip():
        print("Error: --confirm 须给 --actor(落账提交身份); 未写入", file=sys.stderr)
        return 2
    try:
        root = _find_repo_root_for_args(args)
        result = run_pointer_backfill(root, actor=args.actor, confirm=bool(args.confirm))
    except (DecisionPointerError, FileNotFoundError, SchemaLoadError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    landing = result.get("landing")
    rc = int(landing["exit_code"]) if landing else 0
    items = [{k: (iso_z(v) if k == "decided_at" else v) for k, v in item.items()} for item in result["items"]]
    if args.json:
        print(render_json({**{k: v for k, v in result.items() if k not in ("items", "landing")}, "items": items,
                           "landing": {k: v for k, v in landing.items() if k != "line"} if landing else None}))
        return rc
    counts = result["counts"]
    outcome = (labels["done"] if result["written"] else labels["nothing"]) if result["confirm"] else labels["preview"]
    print(f"migrate decision-log-pointers: {outcome} 治理根 {result['governance_root']}"
          f"(决策记录 {result['records_dir']}, 指针目录 {result['decision_log_dir']})")
    print(f"  决策记录 {result['decisions']} 份; 有指针 {result['pointed']}; 缺指针 {len(result['missing'])}"
          f"(指针文件 {result['pointers']} 份, 新旧文件名同判); " + ", ".join(
              f"{item_labels.get(k, k)} {counts[k]}" for k in ("derived", "underivable")))
    for item in items:
        tail = f"→ {item['pointer']}" if item.get("pointer") else f"— {item.get('reason') or ''}"
        print(f"  [{item_labels.get(item['status'], item['status'])}] {item['record']}  decision_id={item['decision_id']}  {tail}")
    for reason in result["unreadable"]:
        print(f"  [读不出] {reason}", file=sys.stderr)
    for path in result["written"]:
        print(f"  wrote {path}")
    if landing is not None:
        for op in landing.get("operations") or []:
            print(f"    · {op}")
        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return rc


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
            or not all(f"{{{k}}}" in str(decl.get("target_line") or "") for k in ("project", "source", "project_json"))
            or not all(isinstance((decl.get("landing_actor_flag") or {}).get(k), str) and decl["landing_actor_flag"][k]
                       for k in ("flag", "help"))):
        raise SchemaLoadError("verbs.schema.json two_phase_protocol.project_json_writers(commands / default_phase=dry_run / "
                              "flags / flag_help {dry_run, confirm} / outcome_labels {written, unchanged, preview} / "
                              "target_line 含 {project} {source} {project_json} / landing_actor_flag {flag, help})未声明齐")
    return decl


def _two_phase_family_declaration(family: str = "project_json_writers") -> dict[str, Any]:
    """AIPOS-F153 件① / AIPOS-F151: 本地写命令族的两阶段声明(唯一读取口)。旗标名 / 缺省阶段 = project_json_writers 一处(同一两阶段语义,
    各族不重复声明); 其他族(enrollment_log_writers / roles_writers)只声明 commands 与 flag_help(及族专属键)。状态标签缺省继承
    project_json_writers; 族的动作不是「写文件」时可在本族声明自己的 outcome_labels(须为非空串, 如 roles_writers {preview, done})。
    缺键 / 形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    base = _project_json_writers_declaration()
    if family == "project_json_writers":
        return base
    decl = (load_schema("verbs").get("two_phase_protocol") or {}).get(family)
    own_labels = decl.get("outcome_labels") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not isinstance(decl.get("commands"), list) or not decl["commands"]
            or not all(isinstance((decl.get("flag_help") or {}).get(p), str) and decl["flag_help"][p] for p in ("dry_run", "confirm"))
            or any(k in decl for k in ("flags", "default_phase"))
            or (own_labels is not None and (not isinstance(own_labels, dict) or not own_labels
                                            or not all(isinstance(v, str) and v for v in own_labels.values())))):
        raise SchemaLoadError(f"verbs.schema.json two_phase_protocol.{family}(commands / flag_help {{dry_run, confirm}}; 可选 outcome_labels "
                              "非空串; flags / default_phase 只在 project_json_writers 声明)未声明齐或重复声明")
    return {**decl, "default_phase": base["default_phase"], "flags": base["flags"],
            "outcome_labels": own_labels if own_labels is not None else base["outcome_labels"]}


def _project_json_two_phase_flags(parser: argparse.ArgumentParser, command: str, *, family: str = "project_json_writers") -> None:
    """本地写命令两阶段的唯一旗标注册包装: 互斥的 --dry-run(缺省)/ --confirm(旗标名与帮助读声明; 未登记的命令 = SchemaLoadError)。
    family 选写命令族: 缺省 project.json 写命令(AIPOS-F125); enrollment_log_writers = 写接入日志(AIPOS-F153);
    roles_writers = roles 族写命令 enroll-code / enroll-deliver / register / remove(AIPOS-F151)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _two_phase_family_declaration(family)
    if command not in decl["commands"]:
        raise SchemaLoadError(f"verbs.schema.json two_phase_protocol.{family}.commands 未登记 {command}")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(decl["flags"]["dry_run"], dest="dry_run", action="store_true", help=decl["flag_help"]["dry_run"])
    group.add_argument(decl["flags"]["confirm"], dest="confirm", action="store_true", help=decl["flag_help"]["confirm"])
    if family == "project_json_writers":
        # AIPOS-F159 件①: --confirm 写后落账的提交身份(声明 landing_actor_flag); 命令自带同名旗标(set-repo)= 沿用, 不重复注册
        actor_flag = decl["landing_actor_flag"]
        if actor_flag["flag"] not in parser._option_string_actions:
            parser.add_argument(actor_flag["flag"], dest="actor", default=_default_actor(), help=actor_flag["help"])


def _roadmap_command(args: argparse.Namespace) -> int:
    """AIPOS-F171 件①: `lybra roadmap` 薄壳。目标项目解析 = project 族同一实现(_project_write_target); show 只读; 写命令两阶段
    (roadmap.apply_roadmap_change)。拒 = exit 1(拒因码 + 出口, 零写入); 落账未完成 = 声明退出码(写入已生效)。"""
    import subprocess

    from tools.aipos_cli import roadmap as rm
    from tools.aipos_cli.workspace_config import resolve_home_root_with_source
    from tools.schema_loader import SchemaLoadError

    command = getattr(args, "roadmap_command", None)
    if not command:
        print("Error: 缺子命令(show / status / add-milestone / add-criterion / set / remove / verify)", file=sys.stderr)
        return 2
    try:
        home, _source = resolve_home_root_with_source(explicit_root=args.home_root)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    target = _project_write_target(args, home, args.name)
    if target is None:
        return 1
    root, project = Path(target["project_root"]), target["project"]
    try:
        if command == "show":
            view = rm.roadmap_view(root, lane=args.lane, project=project)
            if args.json:
                print(render_json(view))
            else:
                print(rm.render_view_text(view))
            return 0
        if command == "status":  # AIPOS-F173: 只读推导(roadmap_drift.roadmap_status 唯一实现; 收件箱 / 看板同读)
            from tools.aipos_cli import roadmap_drift

            drift = roadmap_drift.roadmap_status(root, lane=args.lane, project=project)
            print(render_json(roadmap_drift.json_view(drift)) if args.json else roadmap_drift.render_status_text(drift))
            return 0
        if command == "verify":  # AIPOS-F175: two-phase, single implementation roadmap.apply_owner_verification
            return _roadmap_verify(args, root, project, target["source"])
        params = {k: getattr(args, k, None) for k in rm.roadmap_param_keys()}
        outcome = rm.apply_roadmap_change(root, command, params, actor=args.actor, confirm=bool(args.confirm), project=project)
    except rm.RoadmapError as exc:
        print(f"Error: {exc}{'' if command in ('show', 'status') else '(路线图未改动)'}", file=sys.stderr)
        return 1
    except (SchemaLoadError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    landing = outcome.get("landing")
    rc = 0 if landing is None or landing["landed"] else int(landing["exit_code"])
    if args.json:
        print(render_json({"ok": True, "command": command, "project": project, "target_source": target["source"],
                           **{k: v for k, v in outcome.items() if k != "landing"}, "landing": _landing_view(landing)}))
        return rc
    labels = _two_phase_family_declaration("roadmap_writers")["outcome_labels"]
    mode = labels["written"] if outcome["written"] else (labels["unchanged"] if not outcome["changed"] else labels["preview"])
    print(f"目标项目 {project}(来源: {target['source']}) · 路线图 {outcome['path']}")
    print(f"roadmap {command} {outcome['target']}: {mode}")
    print(f"  {outcome['detail']}")
    if outcome["diff"]:
        print(outcome["diff"].rstrip("\n"))
    if outcome.get("pointer"):
        print(f"  decision_log 指针{'' if outcome['written'] else '(将生成)'}: {outcome['pointer']}")
    if outcome.get("pointer_warning"):
        print(f"  ⚠ {outcome['pointer_warning']}", file=sys.stderr)
    if landing is not None:
        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return rc


def _roadmap_verify(args: argparse.Namespace, root: Path, project: str, source: str) -> int:
    """AIPOS-F175 件③: `lybra roadmap verify` 薄壳。缺转达人 / 原话 = 用法错 exit 2(与 owner decide 同一校验); 拒 = exit 1(零写入);
    落账未完成 = 声明退出码(写入已生效); 修复卡起草被拒 = exit 1 但抽查记录照写(预演渲染 / 确认落账, AIPOS-F180C 不连坐)。"""
    from tools.aipos_cli import roadmap as rm
    from tools.aipos_cli.owner_inbox import InboxUsageError

    try:
        outcome = rm.apply_owner_verification(root, criterion=args.criterion, result=args.result, relayed_by=args.relayed_by,
                                              owner_words=args.owner_words, note=args.note, card=args.card, actor=args.actor,
                                              confirm=bool(args.confirm), project=project)
    except InboxUsageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except rm.RoadmapError as exc:
        print(f"Error: {exc}(零写入)", file=sys.stderr)
        return 1
    landing = outcome.get("landing")
    rc = 0 if landing is None or landing["landed"] else int(landing["exit_code"])
    if outcome.get("fix_error"):  # AIPOS-F180C: fix draft refused, owner record kept (preview: will write; confirm: landed); non-zero
        print(rm.verify_declaration()["texts"]["fix_error"].format(task_id=outcome["fix_task_id"],
                                                                   record_state=rm.verify_record_state(outcome)), file=sys.stderr)
        rc = rc or 1
    if args.json:
        print(render_json({"ok": not outcome.get("fix_error"), "command": "verify", "project": project, "target_source": source,
                           **{k: v for k, v in outcome.items() if k != "landing"}, "landing": _landing_view(landing)}))
        return rc
    labels = _two_phase_family_declaration("roadmap_writers")["outcome_labels"]
    mode = labels["written"] if outcome["written"] else labels["preview"]
    for line in rm.render_verify_text(outcome, project=project, source=source, mode=mode):
        print(line)
    if landing is not None:
        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return rc


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


def _default_actor() -> str:
    """来源 / 提交身份缺省: $USER 或 owner(project new / set-repo / home git-init 与 project 族落账同一缺省)。"""
    return os.environ.get("USER") or "owner"


def _land_project_writes(project_root: str | Path, written: list[str | Path], *, actor: str, subcommand: str,
                         project: str, command: str = "project_writers", **fields: Any) -> dict[str, Any]:
    """AIPOS-F159 件①(gap #144): project 族写命令 --confirm 后的落账薄壳——复用 F158 写后落账唯一实现
    governance_commit.land_written_paths(N6 同一提交口, 声明 verbs.lybra_governance_commit.post_write_landing.commands.project_writers),
    不另写 git 逻辑。written = 本次写出的文件(绝对或治理根相对), 换算为治理根相对路径作 --paths 白名单。
    落账声明不可读 = 不落账(fail-closed), 写入已生效: 拒因出声, exit 1(与 envelope mint 同一处置)。返回 land_written_paths 的结果(含 line)。
    AIPOS-F213 件①: 路径换算 / 声明不可读处置收为写后落账唯一薄壳 board_adapter.land_after_write(队列写 / owner decide 同用), 本函数只填
    project 族的提交信息字段。"""
    from tools.aipos_cli.board_adapter import land_after_write

    return land_after_write(project_root, list(written), actor=actor, command=command, subcommand=subcommand, project=project,
                            **fields)


def _landing_view(landing: dict[str, Any] | None) -> dict[str, Any] | None:
    """落账结果的 JSON 视图(去掉供文本输出的 line; 唯一实现 board_adapter.landing_view)。"""
    from tools.aipos_cli.board_adapter import landing_view

    return landing_view(landing)


def _land_queue_entry(repo_root: Path, result: dict[str, Any], *, actor: str, subcommand: str, task_id: str,
                      written: list[str] | None = None) -> dict[str, Any] | None:
    """AIPOS-F213 件①: CLI 队列写命令(block / reopen / withdraw / amend / supersede / correct / return-repair)写入成功后的落账——
    同一薄壳 board_adapter.land_after_write(command = queue_writers, 与 MCP 确认分支 / owner decide 同一实现); 写出清单缺省 =
    board_adapter.queue_write_paths(result)(两阶段撤换 / 纠正给自报的 written)。无写出 = None(不落)。落账失败不回滚队列状态。
    结果的 JSON 视图同时挂到 result["landing"]。"""
    from tools.aipos_cli.board_adapter import land_after_write, landing_view, queue_write_paths

    paths = list(written) if written is not None else queue_write_paths(result)
    landing = land_after_write(repo_root, paths, actor=actor, command="queue_writers", subcommand=subcommand, task_id=task_id)
    if landing is not None:
        result["landing"] = landing_view(landing)
    return landing


def _emit_landing(landing: dict[str, Any] | None) -> int:
    """落账结果行输出(成功 stdout / 未完成 stderr)并给退出码: 无落账 / 成功 = 0, 未完成 = 声明 landing_failed_exit_code。"""
    if landing is None:
        return 0
    print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return 0 if landing["landed"] else int(landing["exit_code"])


def _project_json_two_phase_emit(command: str, target: dict[str, Any], outcome: dict[str, Any], *, actor: str, json_out: bool,
                                 details: list[str] | tuple[str, ...] = (), extra: dict[str, Any] | None = None,
                                 via: str = "", extra_written: list[str] | tuple[str, ...] = ()) -> int:
    """写 project.json 的命令同一输出: 首行 = 目标项目名 + 来源 + project.json 绝对路径(AIPOS-F127 件②, 文案读声明 target_line),
    次行状态(已写入 / 无改动 / 预览)+ 命令自述的改动行 + project.json unified diff。target = workspace_config.resolve_project_write_target
    的结果(各命令目标解析同一实现)。只涉 project.json(update_project_json 的结果), 不读不印 connection.json / 凭据。--json = 同一内容的 JSON。

    AIPOS-F159 件①(gap #144): 本族唯一落账点——确认阶段(outcome dry_run=False)写后即经 _land_project_writes 落 project.json
    (提交身份 = actor; 无改动亦核一次: 范围已落账 = PASS no-op, 前次写后未落账者随之补落); 预演零写入、不落账。
    返回退出码: 落账成功 / 预演 = 0, 落账未完成 = 声明的 landing_failed_exit_code(写入已生效, 出拒因 + 补账命令)。
    AIPOS-F203 件①: extra_written = 本次随 project.json 一并写出的治理根文件(validation-root run-start 新存的 helper 快照), 同一提交落账。"""
    decl = _project_json_writers_declaration()
    labels = decl["outcome_labels"]
    project_name = target["project"]
    landing = None
    if not outcome.get("dry_run", True):
        landing = _land_project_writes(target["project_root"], [outcome["project_json"], *extra_written], actor=actor, subcommand=command,
                                       project=project_name)
    rc = 0 if landing is None or landing["landed"] else int(landing["exit_code"])
    if json_out:
        payload = {"ok": True, "command": command, "project": project_name, "target_source": target["source"],
                   **(extra or {}), **outcome}
        if landing is not None:
            payload["landing"] = _landing_view(landing)
        print(render_json({k: (str(v) if isinstance(v, Path) else v) for k, v in payload.items()}))
        return rc
    print(decl["target_line"].format(project=project_name, source=target["source"],
                                     project_json=Path(outcome["project_json"]).resolve()))
    mode = labels["written"] if outcome["written"] else (labels["unchanged"] if not outcome["changed"] else labels["preview"])
    print(f"project {command} {project_name}: {mode}{via} {outcome['project_json']}")
    for line in details:
        print(f"  {line}")
    if outcome["diff"]:
        print(outcome["diff"].rstrip("\n"))
    if landing is not None:
        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
    return rc


def _project_authorization_cli(args: argparse.Namespace, home: Path | None) -> int:
    """AIPOS-F224: `lybra project grant` 薄壳。块构造 / 校验 / 写全在 autonomy_policy(authorization_block / build_authorization_decision /
    authorization_overview; 迁移草案 envelope_board.authorization_draft_from_envelopes); 预演 = board_adapter.record_owner_decision(dry_run, 零写入), 确认 =
    envelope_mint.mint_via_gate(门 owner_decision_record 同一路径, 含落账)。退出码读 verbs.schema lybra_project_authorization.exit_codes。"""
    from tools.aipos_cli import autonomy_policy as ap
    from tools.aipos_cli.board_adapter import record_owner_decision
    from tools.aipos_cli.verb_contract import declared_exit_codes

    codes = declared_exit_codes("lybra_project_authorization")
    target = _project_write_target(args, home, args.name)
    if target is None:
        return codes["refused"]
    root = Path(target["project_root"])
    json_out = bool(getattr(args, "json", False))
    cmd = args.auth_command
    if cmd == "show":
        view = {"governance_root": str(root), "authorizations": ap.authorization_overview(root),
                "migrated_envelopes": ap.authorization_superseded_policies(root)}
        if json_out:
            print(render_json(view))
        else:
            print(_authorization_text(view))
        return codes["ok"]
    notes: list[str] = []
    if cmd == "draft":
        from tools.aipos_cli.envelope_board import authorization_draft_from_envelopes

        draft = authorization_draft_from_envelopes(root, max_tasks=args.max_tasks)
        notes = list(draft["notes"])
        if draft["block"] is None:
            print(f"lybra project grant draft: {'; '.join(notes)}", file=sys.stderr)
            return codes["refused"]
        block = draft["block"]
        notes.insert(0, "迁移的在用信封: " + "; ".join(
            f"{e['policy_id']}({e['agent_or_role']}, task_mode={e['task_mode'] or '不限'}, lane={e['lane_repos'] or '不限'}, "
            f"剩余 {e['remaining']}/{e['max_tasks']}, 到期 {e['expires_at']})" for e in draft["envelopes"]))
    elif cmd == "revoke":
        block = ap.authorization_block(root, action="revoke", notes=args.reason)
    else:
        modes, drivers = list(args.task_mode or []), list(args.driver or [])
        inline = [t.split(":") for t in (args.grant_lane or []) if t.count(":") == 2]
        plain = [t for t in (args.grant_lane or []) if t.count(":") != 2]
        lanes = [{"repo": r, "task_modes": [m for m in ms.split(",") if m], "drivers": [d for d in ds.split(",") if d]} for r, ms, ds in inline]
        if (plain and (not modes or not drivers)) or (not plain and (modes or drivers)) or any(not l["task_modes"] or not l["drivers"] for l in lanes):
            print("Error: --grant-lane / --task-mode / --driver 须成组给出(每个 --grant-lane 得到给出的全部任务类型与驱动方; 或 --grant-lane 仓:类型,…:驱动方,…)",
                  file=sys.stderr)
            return codes["usage"]
        lanes += [{"repo": r, "task_modes": modes, "drivers": drivers} for r in plain]
        if cmd == "sign" and (not lanes or not args.host or args.max_tasks is None or not args.expires_at):
            print("Error: sign 须给 --grant-lane/--task-mode/--driver、--host、--max-tasks 与 --expires-at(期限 + 总卡数双上限必填)", file=sys.stderr)
            return codes["usage"]
        block = ap.authorization_block(root, action="grant" if cmd == "sign" else "extend", lanes=lanes,
                                       hosts=list(args.host or []), launch_harnesses=list(args.launch_harness or []),
                                       zones_open=list(args.open_zone or []), max_tasks=args.max_tasks, expires_at=args.expires_at)
    payload = {"decision_id": f"grant-{block['authorization_id']}-r{int(block['base_revision']) + 1:04d}",
               "project_authorization": block, "actor": args.actor, "decided_by_ref": args.actor}
    if args.decision_summary:
        payload["decision_summary"] = args.decision_summary
    if args.confirm:
        from tools.aipos_cli.envelope_mint import mint_via_gate

        report = mint_via_gate([payload], governance_root=root, actor=args.actor, connection_json=args.connection_json,
                               token_role=args.token_role)
        if report["early"]:
            print(f"Error: {report['error']}", file=sys.stderr)
            return codes["refused"]
        if json_out:
            print(render_json({k: v for k, v in report.items() if k not in ("early", "landing_line")}))
        else:
            print(f"project grant {cmd} --confirm via {report['gate_url']} (credential {args.token_role} "
                  f"{report['credential_fingerprint']}) → {root}")
            for item in report["signed"]:
                print(f"  signed {item['policy_id']} ({item['agent_or_role']}): decision={item['decision_id']}")
                for path in item["written"]:
                    print(f"    wrote {path}")
            if report["landing_line"] is not None:
                print(report["landing_line"], file=sys.stdout if (report["landing"] or {}).get("landed") else sys.stderr)
            if report["error"]:
                print(f"Error: {report['error']}", file=sys.stderr)
        if report["exit_code"] and report["signed"]:
            return codes["landing_failed"]
        return codes["ok"] if report["exit_code"] == 0 else codes["refused"]
    preview = record_owner_decision(payload, dry_run=True, repo_root=root, actor=args.actor)
    data = preview.get("data") or {}
    reasons = list(preview.get("blocking_reasons") or [])
    if json_out:
        print(render_json({"stage": "preview", "verdict": preview.get("verdict"), "blocking_reasons": reasons, "notes": notes,
                           "block": block, "authorization_record": data.get("authorization_record"),
                           "planned_writes": preview.get("planned_writes")}))
    else:
        print(f"lybra project grant {cmd} 预演(零写入; 加 --confirm 经门签发并落账): verdict={preview.get('verdict')}")
        for note in notes:
            print(f"  {note}")
        for item in preview.get("planned_writes") or []:
            print(f"  将写 {item.get('path')}")
        for reason in reasons:
            print(f"  ✗ 拒因: {reason}", file=sys.stderr)
        if data.get("authorization_markdown"):
            print("---- 授权修订全文(预演) ----")
            print(str(data["authorization_markdown"]).rstrip())
    return codes["refused"] if reasons else codes["ok"]


def _authorization_text(view: dict[str, Any]) -> str:
    """`project grant show` 文本(数据 = autonomy_policy.authorization_overview, 看板「项目授权」同读)。"""
    lines = [f"项目授权 — 治理根 {view['governance_root']}"]
    if not view["authorizations"]:
        lines.append("  (无项目授权: 覆盖判定只看旧信封, 行为不变)")
    for row in view["authorizations"]:
        if row.get("state") == "missing":
            lines.append(f"  {row['authorization_id']}: 最新修订缺失或格式不合规")
            continue
        lines.append(f"  {row['authorization_id']} r{row['revision']} 状态={row['state']} 已放行 {row['used']}/{row['max_tasks']} 张业务卡, "
                     f"到期 {row['expires_at']}")
        for lane in row["lanes"]:
            lines.append(f"    车道 {lane['repo']}: 任务类型 {', '.join(lane['task_modes'])}; 驱动方 {', '.join(lane['drivers'])}")
        lines.append(f"    主机 {', '.join(row['hosts'])} · 拉起 {', '.join(row['launch_harnesses']) or '只手工 /go'} · "
                     f"放开禁区 {', '.join(row['zones_open']) or '无(全禁)'}")
        if row["supersedes_policies"]:
            lines.append(f"    迁入的旧信封(superseded): {', '.join(row['supersedes_policies'])}")
        for why in row["notices"]:
            lines.append(f"    告知: {why}")
        for rev in row["revisions"]:
            lines.append(f"    修订 r{rev['revision']} {rev.get('action')} {rev.get('status')} @ {rev.get('revised_at')} 裁决 {rev.get('owner_approval_ref')}")
    return "\n".join(lines)


def _validation_root_cli(args: argparse.Namespace, home: Path | None) -> int:
    """AIPOS-F190: `lybra project validation-root` 薄壳。写动作两阶段 = 同一包装(_project_json_two_phase_emit, 写后落账);
    逻辑全在 validation_root(唯一实现)。拒 = refused(拒因码 + 问题, project.json 未改动)。
    AIPOS-F216 件②(F208 P3-4): 退出码读动词契约 verbs.schema lybra_project_validation_root.exit_codes(ok / refused / usage; 写后落账失败
    由 _project_json_two_phase_emit 按 post_write_landing 声明返回 landing_failed), 禁写死。"""
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli.verb_contract import declared_exit_codes
    from tools.schema_loader import SchemaLoadError

    codes = declared_exit_codes("lybra_project_validation_root")

    if args.vr_command == "helper":
        if bool(args.decl_id) != bool(args.run_id):
            print("Error: --id 与 --run-id 须同给(按 run 取本 run 固定的 helper); 都不给 = 当前部署版", file=sys.stderr)
            return codes["usage"]
        if not args.decl_id:
            if args.sha256:
                print(vr.helper_sha256())
            else:
                sys.stdout.write(vr.helper_source())
            return codes["ok"]
    target = _project_write_target(args, home, args.name)
    if target is None:
        return codes["refused"]
    root = Path(target["project_root"])
    if args.vr_command == "helper":  # AIPOS-F203 件②: 按 run 查询口(唯一实现 validation_root.run_helper_lookup); 项目脚本运行时取摘要, 禁写死
        try:
            got = vr.run_helper_lookup(root, args.decl_id, args.run_id)
        except vr.ValidationRootError as exc:
            print(f"Error: {exc.code}(不下发 helper)", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return codes["refused"]
        except (SchemaLoadError, OSError) as exc:
            print(f"Error: {exc}(不下发 helper)", file=sys.stderr)
            return codes["refused"]
        if got["note"]:
            print(f"注意: {got['note']}", file=sys.stderr)  # 旧 run 标注走 stderr, stdout 只有 helper 原文 / 摘要(管道与脚本取值不受影响)
        if args.sha256:
            print(got["sha256"])
        else:
            sys.stdout.write(got["source"])
        return codes["ok"]
    if args.vr_command == "list":
        try:
            entries = vr.project_validation_roots(root)
            hosts = vr.project_validation_hosts(root)  # AIPOS-F196 件③
        except (vr.ValidationRootError, SchemaLoadError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return codes["refused"]
        entries = [e for e in entries if (not args.task_id or e["task_id"] == args.task_id) and (not args.host or e["host"] == args.host)]
        if args.json:
            print(render_json({"project": target["project"], "declarations": entries, "hosts": hosts, "helper_sha256": vr.helper_sha256()}))
        else:
            print(f"项目 {target['project']} 验证根声明(project.json validation_roots):")
            for line in vr.render_list_lines(entries):
                print(f"  {line}")
            for label, h in sorted(hosts.items()):
                print(f"  目标主机 {label}: ssh 别名 {h['ssh_alias']}(owner_ref={h['owner_ref']}, by {h['declared_by']} @ {h['declared_at']})")
        return codes["ok"]
    if args.vr_command == "init-command":  # AIPOS-F191: 只读重取目标主机目录链初始化命令(唯一铸造口 validation_root.init_command)
        try:
            got = vr.init_command(root, args.decl_id, instance=args.instance)
        except vr.ValidationRootError as exc:
            print(f"Error: {exc.code}(不铸初始化命令)", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return codes["refused"]
        except (SchemaLoadError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return codes["refused"]
        delivery = got.get("source_delivery") or {}
        if args.json:
            print(render_json({"project": target["project"], "declaration_id": got["entry"]["id"], "host": got["entry"]["host"],
                               "run_id": got["run"]["run_id"], "run_dir": got["run_dir"], "command": got["command"],
                               "ssh_alias": got.get("ssh_alias"), "source_delivery": delivery}))
        else:
            where = (f"在治理根所在机运行(经 ssh {got['ssh_alias']} 到目标主机 {got['entry']['host']})" if got.get("ssh_alias")
                     else f"在目标主机 {got['entry']['host']} 运行")
            print(f"声明 {got['entry']['id']} 活动 run {got['run']['run_id']}: {where}(只建空目录链 {got['run_dir']}):")
            print(got["command"])  # 末行 = 目录链初始化命令(行为不变); AIPOS-F197 候选源码送达命令见 --json source_delivery / 开工稿 / run-start 输出
        return codes["ok"]
    if args.vr_command == "evidence-fetch":  # AIPOS-F200 件②: 远端审计 run 证据取回(唯一实现 validation_root.evidence_fetch; 不写 project.json)
        try:
            got = vr.evidence_fetch(root, args.decl_id, args.run_id)
        except vr.ValidationRootError as exc:
            print(f"Error: {exc.code}(副本未落 / 未改动)", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
            return codes["refused"]
        except (SchemaLoadError, OSError) as exc:
            print(f"Error: {exc}(副本未落 / 未改动)", file=sys.stderr)
            return codes["refused"]
        if args.json:
            print(render_json({"project": target["project"], "declaration_id": got["entry"]["id"], "host": got["entry"]["host"],
                               "run_id": got["run"]["run_id"], "copy_dir": got["copy_dir"], "manifest_sha256": got["manifest_sha256"],
                               "files": got["files"], "bytes": got["bytes"], "skipped": got["skipped"], "ssh_alias": got["ssh_alias"],
                               "notes": got["notes"]}))
        else:
            print(f"声明 {got['entry']['id']} run {got['run']['run_id']}(主机 {got['entry']['host']}, 经 ssh {got['ssh_alias']})证据已取回并逐个核对: "
                  f"{got['files']} 个文件 {got['bytes']} 字节, 清单 sha256 {got['manifest_sha256']}")
            for item in got["skipped"]:
                print(f"  未取回 {item['path']}: {item['reason']}")
            for note in got["notes"]:
                print(f"  {note}")
            print(f"FETCH_OK copy_dir={got['copy_dir']}")
        return codes["ok"]
    action = args.vr_command
    try:
        outcome = vr.apply_validation_root_change(
            root, action, dry_run=not args.confirm, actor=args.actor, host=getattr(args, "host", None), base=getattr(args, "base", None),
            task_id=getattr(args, "task_id", None), instance=getattr(args, "instance", None), expires_at=getattr(args, "expires_at", None),
            owner_ref=getattr(args, "owner_ref", None), host_fingerprint=getattr(args, "host_fingerprint", None),
            decl_id=getattr(args, "decl_id", None), reason=getattr(args, "reason", None), candidate=getattr(args, "candidate", None),
            run_id=getattr(args, "run_id", None), udid=getattr(args, "udid", None), sim_evidence=getattr(args, "sim_evidence", None),
            ssh_alias=getattr(args, "ssh_alias", None))
    except vr.ValidationRootError as exc:
        print(f"Error: {exc.code}(project.json 未改动)", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return codes["refused"]
    except (SchemaLoadError, OSError) as exc:
        print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
        return codes["refused"]
    json_out = getattr(args, "json", False)
    payload = {k: outcome[k] for k in ("dry_run", "changed", "written", "diff", "project_json")}
    if action == "set-host":  # AIPOS-F196 件③: 目标主机 ssh 访问声明(project.json validation_roots.hosts)
        (label, host_entry), = outcome["host"].items()
        details = [f"主机 {label}: ssh 别名 {host_entry['ssh_alias']} owner_ref={host_entry['owner_ref']} declared_by={host_entry['declared_by']}",
                   *outcome["notes"]]
        return _project_json_two_phase_emit("validation-root set-host", target, payload, actor=args.actor, json_out=json_out,
                                            extra={"action": action, "host": outcome["host"], "notes": outcome["notes"]}, details=details)
    entry, run = outcome["entry"], outcome.get("run")
    details = [f"声明 {entry['id']}: 主机 {entry['host']}(指纹 {entry['host_fingerprint'] or '(未绑定)'}) 卡 {entry['task_id']} "
               f"实例 {entry['instance']}({entry['role_class']}) 到期 {entry['expires_at']} owner_ref={entry['owner_ref']}",
               f"可写目录 = {vr.run_directory(entry, run['run_id'] if run else '<run-id>')}"]
    if run:
        details.append(f"run {run['run_id']} 候选 {run['candidate']} 起 {run['started_at']}" + (f" 收口 {run['finished_at']}" if run["finished_at"] else ""))
        if run.get("simulator"):  # AIPOS-F194: 本 run 专用模拟器(与 run-id 同一处)
            sim = run["simulator"]
            details.append(f"专用模拟器 {sim['name']} udid={sim['udid']}" + (f" 删除核对通过 {sim['verified_at']}" if sim.get("verified_at") else ""))
    details += list(outcome["notes"])
    extra = {"action": action, "declaration": entry, "run": run, "notes": outcome["notes"]}
    if action == "declare":
        return _project_json_two_phase_emit("validation-root declare", target, payload, actor=args.actor, json_out=json_out,
                                            extra=extra, details=details)
    if action == "revoke":
        return _project_json_two_phase_emit("validation-root revoke", target, payload, actor=args.actor, json_out=json_out,
                                            extra=extra, details=details)
    if action == "run-start":  # AIPOS-F203 件①: 新存的 helper 快照随 project.json 同一提交落账
        extra["helper_snapshot"] = outcome.get("helper_snapshot")
        return _project_json_two_phase_emit("validation-root run-start", target, payload, actor=args.actor, json_out=json_out,
                                            extra=extra, details=details, extra_written=list(outcome.get("written_paths") or []))
    if action == "sim-bind":
        return _project_json_two_phase_emit("validation-root sim-bind", target, payload, actor=args.actor, json_out=json_out,
                                            extra=extra, details=details)
    return _project_json_two_phase_emit("validation-root run-finish", target, payload, actor=args.actor, json_out=json_out,
                                        extra=extra, details=details)


def _project_upgrade_cli(args: argparse.Namespace, home: Path | None) -> int:
    """AIPOS-F176 件①③: `lybra project upgrade` 薄壳——目标项目解析 = project 族同一实现(_project_write_target); 预演 / 确认 / 引导全部在
    project_upgrade(plan_upgrade / run_upgrade / derive_guidance + 同一文本渲染)。--confirm 缺 --actor 或与 --guidance 同用 = 用法错(零写入)。"""
    from tools.aipos_cli import project_upgrade as pu
    from tools.schema_loader import SchemaLoadError

    try:
        codes = {k: int(v["code"]) for k, v in pu.upgrade_declaration()["exit_codes"].items()}
    except SchemaLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.confirm and args.guidance:
        print("Error: --guidance 只读, 不与 --confirm 同用; 未写入", file=sys.stderr)
        return codes["usage"]
    if args.confirm and not str(args.actor or "").strip():
        print("Error: --confirm 须给 --actor(各步落账提交身份: 实例名或 owner); 未写入", file=sys.stderr)
        return codes["usage"]
    target = _project_write_target(args, home, args.name)
    if target is None:
        return codes["refused"]
    root, project = Path(target["project_root"]), str(target["project"])
    source = str(target["source"])
    try:
        if args.guidance:
            guidance = pu.derive_guidance(root, project=project, home_root=home)
            print(render_json(guidance) if args.json else "\n".join(pu.render_guidance_lines(guidance)))
            return codes["ok"]
        if not args.confirm:
            plan = pu.plan_upgrade(root, project=project, home_root=home)
            print(render_json({"target_source": source, **pu.plan_view(plan)}) if args.json
                  else "\n".join(pu.render_plan_lines(plan, source=source)))
            return codes["ok"]
        run = pu.run_upgrade(root, project=project, actor=str(args.actor), home_root=home)
    except (SchemaLoadError, pu.UpgradeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return codes["refused"]
    if args.json:
        print(render_json({"target_source": source, **run}))
    else:
        lines = pu.render_run_lines(run, source=source)
        print("\n".join(lines), file=sys.stdout if run["stopped_at"] is None else sys.stderr)
    return int(run["exit_code"])


def _project_workstation_plan_cli(args: argparse.Namespace, home: Path | None) -> int:
    """AIPOS-F198: `lybra project workstation-plan` 薄壳——目标项目解析 = project 族同一实现(_project_write_target); 现状 / 计划 / 话术 / 文本
    全部在 workstation_plan(看板工位设置页同一推导与同一文本)。只读。"""
    from tools.aipos_cli import workstation_plan as wp
    from tools.aipos_cli.board_pages import pages_declaration
    from tools.schema_loader import SchemaLoadError

    try:
        codes = {k: int(v["code"]) for k, v in wp.declaration()["exit_codes"].items()}
        locale = pages_declaration()["locale"]
    except SchemaLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    lang = args.lang or str(locale["default"])
    if lang not in locale["supported"]:
        print(f"Error: --lang {lang} 不在 {list(locale['supported'])}", file=sys.stderr)
        return codes["usage"]
    target = _project_write_target(args, home, args.name)
    if target is None:
        return codes["refused"]
    try:
        modes = None if args.pi_modes is None else [m for m in str(args.pi_modes).split(",") if m.strip()]
        goal = wp.normalize_target(pi_modes=modes, pi_executor=args.pi_executor, workspace=args.workspace, host=args.host,
                                   ssh_alias=args.ssh_alias, owner_ref=args.owner_ref, lang=lang)
        plan = wp.build_plan(target["project_root"], project=str(target["project"]), home_root=home, target=goal, lang=lang)
    except wp.TargetError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return codes["usage"]
    except SchemaLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return codes["refused"]
    print(render_json(wp.plan_view(plan)) if args.json else "\n".join(plan["lines"]))
    return codes["ok"]


def _render_pi_model_affected(entry: dict[str, Any]) -> str:
    """AIPOS-F169: set-model 预演/写入的一行影响工位(角色 / 实例 / 位置 / 下次拉起模型变化)。"""
    from tools.aipos_cli.loop_run_record import model_label

    loc = entry.get("workstation") or {}
    where = (f"{loc.get('host') or '本机'}:{loc.get('dir')} transport={loc.get('transport')}" if loc.get("found")
             else f"位置定位不到({loc.get('reason')})" if loc else "无工位")
    line = (f"影响工位 [{entry['role']}] {entry.get('instance') or '(未声明)'} @ {where}: 下次拉起 "
            f"{model_label(entry['before'])} → {model_label(entry['after'])}")
    return line + (f"; {entry['note']}" if entry.get("note") else "")


class _RolesIssuerError(RuntimeError):
    """`lybra roles enroll-code / enroll-deliver` 取门客户端失败(拒因 + 出口)。"""

    def __init__(self, message: str, next_step: str) -> None:
        super().__init__(message)
        self.next_step = next_step


def _roles_issuer_root(args: argparse.Namespace) -> tuple[Path, str]:
    """AIPOS-F199 件③(gap #203): 签码命令(roles enroll-code / enroll-deliver)凭据所在治理根的唯一定位 → (治理根, 来源说明)。
    序: 显式 --workspace-root(全局或 roles 级; 原取法不变)> 本命令 --governance-root(enroll_deliver.resolve_local_governance_root:
    本机已建治理根 / 已登记项目名)> F144 治理根唯一解析梯(_find_repo_root_for_args: 环境变量 > 当前目录所在治理根 > 工位声明; board approve
    F185 同法)。原缺省走 AIPOS-226 home 模型, 在别的项目治理根目录下运行仍取活动项目(lybra)的 connection.json(chris 实撞)。
    解析不出 = FileNotFoundError / ValueError(拒因原文), 调用方停线报错, 不回落活动项目。"""
    from tools.aipos_cli.enroll_deliver import resolve_local_governance_root
    from tools.aipos_cli.workspace_config import merge_workspace_root_flags

    explicit = merge_workspace_root_flags(getattr(args, "global_workspace_root", None), getattr(args, "workspace_root", None))
    if explicit:
        return Path(_resolve_workspace_for_command(args)), f"--workspace-root {explicit}"
    governance_root = str(getattr(args, "governance_root", "") or "").strip()
    if governance_root:
        return resolve_local_governance_root(governance_root), f"--governance-root {governance_root}"
    root = Path(_find_repo_root_for_args(args))
    hit = getattr(args, "_governance_root_hit", None) or {}
    return root, str(hit.get("source") or root)


def _roles_issuer_client(workspace_root: Path, connection_target: Path | None, token_role: str,
                         actor: str | None = None, *, root_source: str = "") -> tuple[Any, dict[str, Any], str, str | None]:
    """签码命令(roles enroll-code / enroll-deliver, AIPOS-F151 同一取法)的门客户端: <workspace>/.lybra/connection.json(或 --connection-json)
    声明的门基址(唯一推导口 resolve_gate_base_url, 须声明 mcp.rpc_url)+ 门凭据。
    凭据原文只在进程内作 HTTP Authorization, 永不上 argv / 不回显。返回 (client, connection 数据, 实际凭据角色, 签发实例或 None)。

    AIPOS-F157 件①(gap #132): 取凭据只走 F134/F154 按实例选凭据唯一口(two_phase_shell_factory), 删除按角色取首个 token 的旁路——
    --actor <签发实例> = signer_role_for_actor(F134 driver_token_entry, 未接入即拒)定角色 + load_role_token(agent_instance=该实例)取同一条目;
    缺 --actor = 按角色(缺省 advisor, 缺席回落 owner)经 load_role_token: 同角色可用条目绑定 ≥2 个实例 = 拒(禁取首个冒名, 不回落 owner),
    ≤1 个实例 = 既有行为。"""
    from tools.aipos_cli.confirm_client import GateAddressError, GateClient, resolve_gate_base_url
    from tools.aipos_cli.two_phase_shell_factory import DriverTokenError, load_role_token, signer_role_for_actor

    conn_path = workspace_connection_path(workspace_root, connection_target=connection_target)
    # AIPOS-F199 件③: 拒因写明实际读取的凭据文件与其来源(--connection-json / 治理根解析来源)
    origin = "--connection-json 显式指定" if connection_target is not None else f"治理根 {workspace_root} 的 .lybra/connection.json(治理根: {root_source or workspace_root})"
    if not conn_path.exists():
        raise _RolesIssuerError(f"local connection.json not found: {conn_path}(读取来源: {origin})",
                                "在治理工作区运行本命令(或 --governance-root / --workspace-root 指向持有签发凭据的治理根, 或 --connection-json 指定本机 connection.json); "
                                "薄壳只调门动词, 没有(也不许有)本地发码路径。")
    print(f"签码凭据文件: {conn_path}(读取来源: {origin})", file=sys.stderr)
    conn_data = json.loads(conn_path.read_text(encoding="utf-8"))
    try:
        base_url = resolve_gate_base_url(connection_json=conn_path, require_declared=True)
    except GateAddressError as exc:
        raise _RolesIssuerError(str(exc), "先 lybra serve start 或修正 connection.json。") from exc
    signer = str(actor or "").strip()
    if signer:
        try:
            role = signer_role_for_actor(connection_json_path=str(conn_path), actor=signer, repo_root=workspace_root)
            token = load_role_token(connection_json_path=str(conn_path), role=role, agent_instance=signer)
        except ValueError as exc:  # DriverTokenError(未接入 / 非驱动方角色类 / 与 loop 驱动方冲突)及读失败: 拒, 不回落按角色取
            raise _RolesIssuerError(f"签发实例 --actor {signer} 取凭据被拒({conn_path}; 读取来源: {origin}): {exc}",
                                    f"--actor 写本治理根已接入的顾问实例(其凭据在 {conn_path}), 或去掉 --actor 用 --token-role 按角色取"
                                    "(同角色多实例时必须 --actor)。") from exc
        return GateClient(base_url, token, timeout=30.0), conn_data, role, signer
    for candidate_role in (token_role, "owner"):
        try:
            token = load_role_token(connection_json_path=str(conn_path), role=candidate_role)
        except DriverTokenError as exc:  # 同角色多实例未指明 = 拒(禁取首个冒名), 不回落下一个角色掩盖
            raise _RolesIssuerError(str(exc), f"加 --actor <签发实例>(本治理根已接入的 {candidate_role} 实例之一)按实例取凭据。") from exc
        except ValueError:  # 该角色无可用条目(TokenResolutionError 族)/ 读失败: 按既有序试下一个角色, 全无 = 下方拒
            continue
        return GateClient(base_url, token, timeout=30.0), conn_data, candidate_role, None
    raise _RolesIssuerError(f"no usable role token ({token_role}/owner) in {conn_path}(读取来源: {origin})",
                            "用 --actor <签发实例> 或 --token-role 指定, 或先在本机 enroll 出顾问/Owner 凭据。")


def _render_roles_preview(command: str, args: argparse.Namespace, payload: dict[str, Any], lines: list[str]) -> None:
    """AIPOS-F151 件④: roles register / remove 预演输出(状态文案读声明 roles_writers.outcome_labels; 只含 token 引用 / 指纹 / 角色类)。"""
    label = _two_phase_family_declaration("roles_writers")["outcome_labels"]["preview"]
    if getattr(args, "json", False):
        print(render_json({"ok": True, "operation": f"roles_{command}", "phase": "dry_run", "dry_run": True, "written": False, **payload}))
        return
    target = getattr(args, "name", None) or getattr(args, "instance", None)
    print(f"roles {command} {target}: {label}")
    for line in lines:
        print(f"  · 将: {line}")
    print("  零写入; 确认后同一条命令加 --confirm 写入")


def _render_enroll_deliver(result: dict[str, Any]) -> None:
    """`lybra roles enroll-deliver` 文本输出(只含角色 / 实例 / 治理根 / 落点 / code_id / 指纹; 注册码与凭据明文永不出现)。"""
    labels = _two_phase_family_declaration("roles_writers")["outcome_labels"]
    head = labels["preview"] if result.get("dry_run") else labels["done"]
    print(f"roles enroll-deliver {result['role']}: {head}")
    print(f"  角色 {result['role']}(类 {result['role_class']})  实例 {result.get('instance') or '-'}  执行者模式 {result.get('executor_mode')}")
    print(f"  治理根 {result['governance_root']}")
    where = f"{result['ssh_target']}:{result['workspace_root']}" if result.get("ssh_target") else result["workspace_root"]
    print(f"  接入落点 {where}(mode={result['mode']})")
    env = result.get("envelope") or {}
    if env.get("required") or env.get("policy_id"):
        print(f"  覆盖信封 {env.get('policy_id') or '-'}({env.get('reason')})")
    if result.get("dry_run"):
        preview = result.get("gate_preview") or {}
        print(f"  有效期 {preview.get('ttl_seconds')} 秒  Gate URL {preview.get('gate_url')}")
        for line in result.get("will") or []:
            print(f"  · 将: {line}")
        print("  未签码、零写入; 确认后同一条命令加 --confirm 执行")
        return
    enrollment = result.get("enrollment") or {}
    print(f"  注册码 {enrollment.get('code_id')}(指纹 {enrollment.get('fingerprint')}, 已在本进程内兑换, 未输出)")
    print(f"  凭据指纹 {(result.get('token') or {}).get('fingerprint')}  scopes {', '.join((result.get('token') or {}).get('scopes') or [])}")
    enrolled = result.get("enroll") or {}
    for item in enrolled.get("files_written") or []:
        print(f"    - {item}")
    for warn in enrolled.get("warnings") or []:
        print(f"    ⚠ {warn}")
    if enrolled.get("next_step"):
        print(f"  下一步: {enrolled['next_step']}")


def build_parser() -> argparse.ArgumentParser:
    from tools.aipos_cli.machine_zone import add_lane_argument  # AIPOS-F133 件②: --lane 声明 verbs.schema lane_view
    from tools.aipos_cli.machine_zone import add_owner_view_argument  # AIPOS-F156 件②: --actor 归属视角(lane_view.owner_view)
    # AIPOS-F106 件②: 帮助文案里的门地址示例读 config.schema urls.gate_local(唯一读取口 schema_loader), 禁写端口字面
    from tools.schema_loader import get_config_default_gate_url
    from tools.aipos_cli.verb_contract import declared_exit_codes  # AIPOS-F101 件③: help 中的退出码读声明

    _GATE_URL_DEFAULT = get_config_default_gate_url()
    parser = argparse.ArgumentParser(description="AI Project OS CLI")
    parser.add_argument("--workspace-root", dest="global_workspace_root", help="Workspace root; may also be provided on supported subcommands")
    # AIPOS-F152 件②: 本进程运行的代码(部署快照 / --dev-tree 开发模式 / 工作树), 唯一实现 deploy_runtime.version_line
    from tools.aipos_cli.deploy_runtime import DEV_TREE_FLAG, VersionAction

    parser.add_argument("--version", action=VersionAction,
                        help="show which code this lybra runs (deploy snapshot + commit / --dev-tree dev mode / work tree) and exit")
    parser.add_argument(DEV_TREE_FLAG, dest="dev_tree", metavar="WORK_TREE",
                        help="entry-level dev switch, must be the FIRST argument of the lybra entry (consumed before any code "
                             "loads): run that product work tree instead of the deploy snapshot; stderr first line marks dev mode")
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
    board_open_parser.add_argument("--workspace-root", help="Governance root whose .lybra/connection.json holds the credential (default: governance-root resolution — env / the governance root containing the current directory / workstation declaration)")
    board_open_parser.add_argument("--connection-json", help="Override the credential file (default <governance root>/.lybra/connection.json; no other implicit location)")
    board_open_parser.add_argument("--role", help="Role token to read from connection.json; default prefers owner then first available")
    board_open_parser.add_argument("--url", help="Board server base URL; overrides connection.json board.url")
    board_open_parser.add_argument("--host", help="Board server host; overrides connection.json")
    board_open_parser.add_argument("--port", type=int, help="Board server port; overrides connection.json")
    board_open_parser.add_argument("--no-browser", action="store_true", help="Print the login URL instead of opening a browser")
    board_approve_parser = board_sub.add_parser("approve", help="AIPOS-271: approve a cross-machine device code (run on the gate machine)")
    board_approve_parser.add_argument("code", help="6-digit device code shown in the remote browser")
    board_approve_parser.add_argument("--workspace-root", help="Governance root whose .lybra/connection.json holds the credential (default: governance-root resolution — env / the governance root containing the current directory / workstation declaration)")
    board_approve_parser.add_argument("--connection-json", help="Override the credential file (default <governance root>/.lybra/connection.json; no other implicit location)")
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
    draft_create_parser.add_argument("--actor", default=None, help="AIPOS-F156: signing advisor instance (fills created_by; verified via the F154 per-instance credential pick). Default = the governance root's only advisor instance; required when several are enrolled")
    draft_create_parser.add_argument("--replace", action="store_true", help="AIPOS-F220: revise this card's existing UNPUBLISHED draft from the corrected input (old draft archived byte-for-byte first; same write/validation path as create). Published / queued cards are refused with the queue amend exit; no existing draft = refused (declared transitions.schema nodes.N0.draft_revision)")
    draft_create_parser.add_argument("--dry-run", action="store_true", help="Render and validate without writing")
    draft_create_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_validate_parser = draft_subparsers.add_parser("validate", help="Validate a draft task card")
    draft_validate_parser.add_argument("--path", required=True, help="Draft path under 5_tasks/drafts/")
    draft_validate_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_list_parser = draft_subparsers.add_parser("list", help="List draft task cards")
    draft_list_parser.add_argument("--json", action="store_true", help="Output JSON")

    draft_publish_parser = draft_subparsers.add_parser("publish", help="Publish a validated draft to pending")
    draft_publish_parser.add_argument("--path", required=True, help="Draft path under 5_tasks/drafts/")
    draft_publish_parser.add_argument("--actor", default=None, help="AIPOS-F156: signing advisor instance recorded as the publish record actor (= card owner; verified via the F154 per-instance credential pick). Default = the governance root's only advisor instance; required when several are enrolled")
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
    # AIPOS-F214 件④: 手工章程收编旗标名读 distribution.schema charter_render_semantics.hand_maintained_guard(adopt_flag / owner_ref_flag; 不写死)
    from tools.aipos_cli.distribution_sync import hand_maintained_guard_declaration

    hand_guard = hand_maintained_guard_declaration()
    sync_parser.add_argument(hand_guard["adopt_flag"], dest="adopt_hand_charter", action="store_true",
                             help="AIPOS-F196: formally take over a hand-maintained workstation charter (one carrying a hand-maintained marker such as "
                                  "「禁止 Lybra 分发器覆盖」): back the original up (never deleted), log the adoption, then render the product charter; "
                                  f"single workstation only, requires {hand_guard['owner_ref_flag']}; add --dry-run to preview the backup path")
    sync_parser.add_argument(hand_guard["owner_ref_flag"], dest="owner_ref", default=None,
                             help=f"AIPOS-F196: Owner decision reference authorizing {hand_guard['adopt_flag']} (single line, no credentials)")

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
    queue_amend_parser.add_argument("--restricted", action="store_true", help="AIPOS-F78 前置零⑤: advisor 对 claimed 卡受限 amend(允许字段读 card.schema restricted_amend.claimed_card_fields: rework_rounds/output_target/lane/resources/validation_source_task/validation_candidate; 复用 F75 restricted amend; AIPOS-F223: 另可按子键改 restricted_amend.claimed_card_subfields 所列, 如 audit_runtime_evidence 只改 host)")
    queue_amend_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_withdraw_parser = queue_subparsers.add_parser("withdraw", help="Withdraw a task from queue")
    queue_withdraw_parser.add_argument("--task-id", required=True, help="Task ID to withdraw")
    queue_withdraw_parser.add_argument("--actor", required=True, help="Actor performing the withdrawal")
    queue_withdraw_parser.add_argument("--reason", required=True, help="Reason for withdrawal")
    queue_withdraw_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    # AIPOS-F234 件①: 在途保护的显式确认出口(旗标名读声明 transitions queue_mutations.in_transit_guard.ack_flag; 判定唯一实现
    # queue_mutation.withdraw_in_transit_check)
    queue_withdraw_parser.add_argument("--ack-in-transit", dest="ack_in_transit", action="store_true", default=False,
                                       help="AIPOS-F234: the card is in transit (a live loop run or launched executor / auditor process) and "
                                            "you accept losing that in-flight work — withdraw anyway; the risk confirmation (with the live "
                                            "processes) is written into withdrawal_reason. Without it an in-transit card is refused with the "
                                            "loop stop exit. No effect when the card is not in transit")
    queue_withdraw_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F234 件②: 存量孤儿审计卡一次性扫描(声明 transitions queue_mutations.in_transit_guard; 唯一实现 board_adapter.withdraw_orphan_audits)
    queue_orphan_parser = queue_subparsers.add_parser(
        "orphan-audits", help="AIPOS-F234: list audit cards whose business card was already withdrawn / superseded / completed but which "
                              "are still pending / claimed (preview by default, read-only); --confirm withdraws them one by one through "
                              "queue withdraw and lands the writes")
    queue_orphan_parser.add_argument("--actor", default=None, help="Advisor instance performing the withdrawal (required with --confirm)")
    queue_orphan_mode = queue_orphan_parser.add_mutually_exclusive_group()
    queue_orphan_mode.add_argument("--dry-run", action="store_true", help="Preview, zero writes (default)")
    queue_orphan_mode.add_argument("--confirm", action="store_true", help="Withdraw every listed orphan audit card and land the writes")
    queue_orphan_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F184 件③: Owner 中途改目标 → 顾问凭 Owner 原话撤换在途卡(声明 transitions queue_mutations.transitions.supersede /
    # verbs.lybra_queue_supersede; 唯一实现 board_adapter.supersede_task)
    queue_supersede_parser = queue_subparsers.add_parser("supersede", help="AIPOS-F184: Owner changed the goal of an in-flight card — the advisor supersedes it with a new card on the Owner's words (pending/claimed/blocked → withdrawn with superseded_by; Owner decision record written; preview by default, --confirm writes and lands)")
    queue_supersede_parser.add_argument("--task-id", required=True, help="Card being superseded (pending / claimed / blocked)")
    queue_supersede_parser.add_argument("--by", required=True, help="New card task_id that takes over (must already be in the queue, not withdrawn)")
    queue_supersede_parser.add_argument("--actor", required=True, help="Advisor instance performing the supersede (recorded as the relayer)")
    queue_supersede_parser.add_argument("--owner-words", default=None, help="Owner's words, one line (card face + decision record rationale); "
                                        "with --decision-id defaults to the referenced ruling's Owner words")
    queue_supersede_parser.add_argument("--owner-ref", default=None, help="Optional: where the Owner ruled (inbox item id / decision record id / session ref)")
    # AIPOS-F213 件②: 引用收件箱已有裁决(核验唯一实现 owner_inbox.referenced_ruling; 不另记决策)
    queue_supersede_parser.add_argument("--decision-id", default=None,
                                        help="AIPOS-F213: reference an Owner ruling already recorded in the inbox (owner decision record id; the "
                                             "inbox relay gives it): no second decision record; refused if the ruling's choice is not supersede, it is "
                                             "for another card, or another action already used it")
    # AIPOS-F186: 撤换承继(声明 transitions supersede.carry_branch; 判定唯一实现 queue_mutation.supersede_carry_plan)
    queue_supersede_parser.add_argument("--carry-branch", action="store_true", default=False,
                                        help="AIPOS-F186: the new card carries the old card's branch and committed work (new card must be pending "
                                             "and unclaimed; old card worktree must have no uncommitted changes; the new card branch is created "
                                             "from the old branch tip when it is claimed; the old branch is kept)")
    queue_supersede_mode = queue_supersede_parser.add_mutually_exclusive_group()
    queue_supersede_mode.add_argument("--dry-run", action="store_true", help="Preview, zero writes (default)")
    queue_supersede_mode.add_argument("--confirm", action="store_true", help="Write the decision record, move the card, land the writes")
    queue_supersede_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F207 件②: Owner 纠正已结案卡(声明 transitions queue_mutations.correct / verbs.lybra_queue_correct; 唯一实现 board_adapter.correct_closed_task)
    queue_correct_parser = queue_subparsers.add_parser("correct", help="AIPOS-F207: the Owner overturns a closed card — record the correction (owner decision record), mark the original closure / verdict as disputed (records untouched), publish a follow-up card inheriting lane / criteria (preview by default, --confirm writes and lands)")
    queue_correct_parser.add_argument("--task-id", required=True, help="Closed card being corrected (completed)")
    queue_correct_parser.add_argument("--relayed-by", default=None, help="Advisor instance relaying the Owner's words (decision captured_by; follow-up card created_by and publish signer)")
    queue_correct_parser.add_argument("--owner-words", default=None, help="Owner's words, one line")
    queue_correct_parser.add_argument("--reason", default=None, help="Optional: why the closure was wrong")
    queue_correct_parser.add_argument("--followup-title", default=None, help="Optional: follow-up card title")
    queue_correct_parser.add_argument("--owner-ref", default=None, help="Optional: where the Owner ruled (session ref / inbox item id)")
    # AIPOS-F213 件②: 引用收件箱已有纠正裁决(核验唯一实现 owner_inbox.referenced_ruling; 不另记决策)
    queue_correct_parser.add_argument("--decision-id", default=None,
                                      help="AIPOS-F213: reference an Owner correction ruling already recorded in the inbox (owner decision record id): "
                                           "no second decision record; --owner-words defaults to the ruling's words; refused if the choice / card do "
                                           "not match or the ruling was already used")
    queue_correct_parser.add_argument("--connection-json", default=None, help="Credential file for the publish signer check (default: governance root .lybra/connection.json)")
    queue_correct_mode = queue_correct_parser.add_mutually_exclusive_group()
    queue_correct_mode.add_argument("--dry-run", action="store_true", help="Preview, zero writes (default)")
    queue_correct_mode.add_argument("--confirm", action="store_true", help="Write the decision, dispute marker and follow-up card, then land the writes")
    queue_correct_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F231 件①: Owner 暂停 / 恢复在途卡(声明 verbs.lybra_queue_pause / lybra_queue_resume; 唯一实现 board_adapter.pause_task)
    for _pause_action, _pause_help in (
            ("pause", "AIPOS-F231: the Owner pauses an in-flight card (owner decision record, appended; while paused the loop refuses to start "
                      "or stops between steps, lybra next --run is refused, loop status shows paused instead of 're-run loop'); preview by default, "
                      "--confirm writes and lands"),
            ("resume", "AIPOS-F231: the Owner resumes a paused card (owner decision record, appended); preview by default, --confirm writes and lands")):
        _pause_parser = queue_subparsers.add_parser(_pause_action, help=_pause_help)
        _pause_parser.add_argument("--task-id", required=True, help="In-flight card (pending / claimed / blocked)")
        _pause_parser.add_argument("--relayed-by", default=None, help="Advisor instance relaying the Owner's words (decision captured_by)")
        _pause_parser.add_argument("--owner-words", default=None, help="Owner's words, one line")
        _pause_parser.add_argument("--owner-ref", default=None, help="Optional: where the Owner ruled (session ref / inbox item id)")
        _pause_mode = _pause_parser.add_mutually_exclusive_group()
        _pause_mode.add_argument("--dry-run", action="store_true", help="Preview, zero writes (default)")
        _pause_mode.add_argument("--confirm", action="store_true", help="Write the decision record, then land the write")
        _pause_parser.add_argument("--json", action="store_true", help="Output JSON")

    queue_return_repair_parser = queue_subparsers.add_parser("return-repair", help="Repair a stuck return")
    queue_return_repair_parser.add_argument("--task-id", required=True, help="Task ID with stuck return")
    queue_return_repair_parser.add_argument("--actor", required=True, help="Actor performing the repair")
    queue_return_repair_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_return_repair_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F65C 件①: queue repair subcommand (repair bad frontmatter YAML)
    queue_repair_parser = queue_subparsers.add_parser("repair", help="AIPOS-F65C: Repair bad frontmatter in task card (minimal fix for unparseable YAML); AIPOS-F178: also detects a claimed card missing its worktree (preview by default, --confirm builds it)")
    queue_repair_parser.add_argument("--task-id", required=True, help="Task ID to repair")
    queue_repair_parser.add_argument("--actor", required=True, help="Actor performing the repair")
    queue_repair_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    queue_repair_parser.add_argument("--confirm", action="store_true", help="AIPOS-F178: build the missing card worktree of a claimed card (same entry as gate claim; idempotent). Without it the worktree part is preview only")
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
    _project_json_two_phase_flags(queue_rework_parser, "rework", family="queue_writers")  # AIPOS-F155 件④: 缺省预演 / --confirm 才经门写
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
    add_owner_view_argument(needs_owner_parser)  # AIPOS-F156 件②

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
    # AIPOS-F162 件②: --supplementary 时以下四项有缺省(派审实例 = --actor; 授权引用 / 卡号读声明 transitions supplementary_audit;
    # 审计实例 = 原卡 audit_by → 按项目推导), 常规派审仍必填(handler 校验, 缺 = 原文拒)
    dispatch_parser.add_argument("--agent-instance", help="Agent instance (dispatcher); 常规派审必填")
    dispatch_parser.add_argument("--owner-policy-ref", help="Owner policy reference; 常规派审必填")
    dispatch_parser.add_argument("--audit-task-id", help="Audit task ID; 常规派审必填")
    dispatch_parser.add_argument("--audit-task-title", help="Audit task title")
    dispatch_parser.add_argument("--audit-by", help="Auditor role/instance")
    dispatch_parser.add_argument("--audit-agent-instance", help="Auditor agent instance; 常规派审必填")
    dispatch_parser.add_argument("--dispatch-reason", "--reason", dest="dispatch_reason", help="Dispatch reason(--supplementary 时 = 争议摘要, 必填)")
    dispatch_parser.add_argument("--supplementary", action="store_true",
                                 help="AIPOS-F162: 已结案卡补充审计(派生 <原卡ID>-S<n>R 补审卡; 原 closure/原裁决不动; 缺省预演, --confirm 写)")
    dispatch_parser.add_argument("--runtime-evidence-command", action="append", default=None, dest="runtime_evidence_commands",
                                 help="AIPOS-F162: 补审卡声明 audit_runtime_evidence(required=true)的验证命令, 可多次; 占位 {worktree}/{commit_sha}/{tree_hash}(AIPOS-F233: 固定候选词表 {src_dir}/{candidate}/{candidate_tree} 为同义别名, 按被审卡主体自动改写为规范形)")
    dispatch_parser.add_argument("--confirm", action="store_true", help="AIPOS-F162: --supplementary 的写入阶段(缺省预演)")
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
    from tools.aipos_cli.enrollment import executor_mode_declaration as _executor_mode_declaration  # AIPOS-F143 件④: 取值读声明
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
    _project_json_two_phase_flags(roles_register_parser, "register", family="roles_writers")  # AIPOS-F151 件④: 缺省预演 / --confirm 才写
    roles_remove_parser = roles_subparsers.add_parser("remove", help="AIPOS-352F1: remove a custom role (idempotent); AIPOS-F21: --instance removes a token entry from connection.json")
    roles_remove_parser.add_argument("name", nargs="?", help="Custom role name to remove (or omit when using --instance)")
    roles_remove_parser.add_argument("--instance", help="AIPOS-F21: remove the token entry bound to this agent_instance (e.g., test.mac.aipos362) from connection.json")
    roles_remove_parser.add_argument("--owner-authorization-ref", help="AIPOS-346F2: reference to owner authorization for this removal")
    roles_remove_parser.add_argument("--reason", default="", help="Reason for removing this custom role")
    roles_remove_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(roles_remove_parser, "remove", family="roles_writers")  # AIPOS-F151 件④: 缺省预演 / --confirm 才写
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
    roles_enroll_code_parser.add_argument("--gate-url", help="Gate URL to embed. Default (AIPOS-F153, config.schema same_host_rule): loopback http://127.0.0.1:<gate port> when the code is redeemed on the gate host; the gate's connection.json mcp.rpc_url only when --instance is declared cross-host in its project's project.json workstations. Pass an externally reachable URL here for other cross-host redemption")
    roles_enroll_code_parser.add_argument("--governance-root", help="Governance workspace root to embed (F24A): bare registered project name or absolute path; validated against the project registry on the gate. Default: workspace_root of the local connection.json")
    roles_enroll_code_parser.add_argument("--token-role", default="advisor", help="Role of the local token used to call the gate verb (default: advisor; falls back to owner if advisor is absent)")
    roles_enroll_code_parser.add_argument("--actor", default=None, help="AIPOS-F157: signing instance — the gate credential is taken from this instance's entry in connection.json (per-instance selection, AIPOS-F134/F154); required when the token role has two or more instances")
    roles_enroll_code_parser.add_argument("--owner-authorization-ref", help="Reference to owner authorization for this enrollment")
    roles_enroll_code_parser.add_argument("--reason", default="", help="Reason for generating this enrollment code")
    roles_enroll_code_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(roles_enroll_code_parser, "enroll-code", family="roles_writers")  # AIPOS-F151 件①: 缺省预演不出码 / --confirm 才出码
    # AIPOS-F151 件②(gap #106/#92): 签码 + 接入一体, 注册码只在本进程内(唯一实现 enroll_deliver.enroll_deliver)
    roles_deliver_parser = roles_subparsers.add_parser(
        "enroll-deliver",
        help="AIPOS-F151: issue an enrollment code AND enroll the target in one command — the code never leaves this process "
             "(same machine: in-process enroll; --ssh: code via ssh stdin). Run on the governance-root machine; "
             "default = preview (no code issued), --confirm executes")
    roles_deliver_parser.add_argument("--role", required=True, help="Role to enroll (built-in or registered custom role)")
    roles_deliver_parser.add_argument("--instance", help="Instance name (<prefix>.<project>.<host>; required for advisor-class roles and --executor-mode subagent)")
    roles_deliver_parser.add_argument("--governance-root", required=True, help="Governance root on this machine: registered project name or absolute path (embedded in the code; the envelope pre-check reads it)")
    roles_deliver_parser.add_argument("--workspace", required=True, help="Enrollment target: workstation directory, or the governance root for advisor-class roles / --executor-mode subagent (with --ssh: path on the remote host)")
    roles_deliver_parser.add_argument("--executor-mode", choices=list(_executor_mode_declaration()["values"]), default=None,
                                      help="Executor enrollment mode (roles.schema executor_modes; same as `lybra roles enroll --executor-mode`)")
    roles_deliver_parser.add_argument("--harness", help="Harness kind (distribution.schema harness_semantics.kinds; same as `lybra roles enroll --harness`)")
    roles_deliver_parser.add_argument("--harness-dir", help="Absolute working directory of a non-pi harness (same as `lybra roles enroll --harness-dir`)")
    roles_deliver_parser.add_argument("--harness-host", help="Host the harness session runs on (same as `lybra roles enroll --harness-host`)")
    roles_deliver_parser.add_argument("--ssh", help="SSH target of a remote workstation (user@host); the code reaches the remote enroll via ssh stdin only")
    roles_deliver_parser.add_argument("--gate-url", help=f"Externally reachable gate URL to embed (default: connection.json mcp.rpc_url if non-loopback, else {_GATE_URL_DEFAULT})")
    roles_deliver_parser.add_argument("--ttl", type=int, help="Code time-to-live in seconds (default 86400)")
    roles_deliver_parser.add_argument("--token-role", default="advisor", help="Role of the local credential used to call the gate (default: advisor; falls back to owner)")
    roles_deliver_parser.add_argument("--actor", default=None, help="AIPOS-F157: signing instance — the gate credential is taken from this instance's entry in connection.json (per-instance selection, AIPOS-F134/F154); required when the token role has two or more instances")
    roles_deliver_parser.add_argument("--owner-authorization-ref", required=True, help="Owner authorization ref for issuing the code (workstation codes: the envelope covering that workstation)")
    roles_deliver_parser.add_argument("--reason", default="", help="Reason recorded with the code")
    roles_deliver_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(roles_deliver_parser, "enroll-deliver", family="roles_writers")
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
    roles_enroll_void_parser = roles_subparsers.add_parser("enroll-void", help="AIPOS-F121/F153: 作废某实例的接入事件——向显式指定的 enrollment_log(--log 或 --project, 缺省拒绝跨日志)追加 void 行(指向被作废行号区间/code_id, 带操作者与理由), 不删行; workstation_location/enroll-where 忽略被作废事件。缺省预演, --confirm 才追加")
    roles_enroll_void_parser.add_argument("--instance", required=True, help="要作废其接入事件的实例名(未知实例拒)")
    roles_enroll_void_parser.add_argument("--reason", required=True, help="作废理由(单行, 写入 void 行)")
    roles_enroll_void_parser.add_argument("--actor", required=True, help="操作者(写入 void 行 by=; 不含空白)")
    # AIPOS-F153 件①: 作用域旗标名读声明 verbs.schema two_phase_protocol.enrollment_log_writers.scope.flags(缺 = SchemaLoadError)
    _void_scope = (_two_phase_family_declaration("enrollment_log_writers").get("scope") or {}).get("flags") or {}
    if not all(isinstance(_void_scope.get(k), str) and _void_scope[k].startswith("--") for k in ("log", "project", "code_id", "before")):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("verbs.schema.json two_phase_protocol.enrollment_log_writers.scope.flags {log, project, code_id, before} 未声明齐")
    roles_enroll_void_parser.add_argument(_void_scope["log"], dest="void_log", help="作用域: 只在这份 enrollment_log 作废(路径; 与 --project 二选一, 都不给 = 拒)")
    roles_enroll_void_parser.add_argument(_void_scope["project"], dest="void_project", help="作用域: 只在该项目的 enrollment_log 作废(项目名; 与 --log 二选一)")
    roles_enroll_void_parser.add_argument(_void_scope["code_id"], dest="void_code_ids", action="append", default=[], help="再限定: 只作废这些 code_id 的事件(可重复)")
    roles_enroll_void_parser.add_argument(_void_scope["before"], dest="void_before", help="再限定: 只作废时间戳早于该 ISO 时刻的事件(如 2026-10-08T16:00:00Z)")
    _project_json_two_phase_flags(roles_enroll_void_parser, "enroll-void", family="enrollment_log_writers")  # AIPOS-F153 件①: 缺省预演 / --confirm 才写
    roles_enroll_void_parser.add_argument("--json", action="store_true", help="Output JSON")
    
    # AIPOS-F66B 件②: 写权限边界可读面 + 读取口(护栏读声明; 单源 roles.schema write_boundary)
    roles_wb_parser = roles_subparsers.add_parser("write-boundary", help="AIPOS-F66B: 写权限边界可读面(角色类 × 面 × read/append/mutate, 读 roles.schema write_boundary)与单次访问判定(--check)")
    roles_wb_parser.add_argument("--role", help="只出该角色行(内建或门注册表自定义角色, 按类展开)")
    roles_wb_parser.add_argument("--instance", help="按 enrollment 记录的实例反查角色")
    roles_wb_parser.add_argument("--harness-root", help="工位根(解析 harness_root 面; 缺省不列)")
    roles_wb_parser.add_argument("--check", metavar="PATH", help="判一次访问: 路径(绝对或相对治理根)")
    roles_wb_parser.add_argument("--level", choices=["read", "append", "mutate"], default="read", help="--check 的请求级别(缺省 read)")
    roles_wb_parser.add_argument("--task-id", help="--check 的卡 ID(per_task 面 / product_repo lane 判定需要)")
    # AIPOS-F190 件③: 主机维度(验证根面 validation_root 按主机声明; 缺省 = 本机)
    roles_wb_parser.add_argument("--host", help="AIPOS-F190: host the --check path lives on (validation-root host label; default = this host)")
    roles_wb_parser.add_argument("--run-id", help="AIPOS-F190: validation run id (minted by `lybra project validation-root run-start`)")
    roles_wb_parser.add_argument("--host-fingerprint", help="AIPOS-F190: caller host fingerprint (hfp1:..., from the host-side helper) to cross-check the declaration")
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
    default_actor = _default_actor()

    project_parser = subparsers.add_parser("project", help="Owner project scaffold under the governance home")
    project_subparsers = project_parser.add_subparsers(dest="project_command")
    project_new_parser = project_subparsers.add_parser("new", help="Scaffold a fresh per-project truth root + project.json")
    project_new_parser.add_argument("name", help="Project name (becomes <home>/<name>)")
    project_new_parser.add_argument("--code-repo", help="Optional absolute path to the project's code repo")
    project_new_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_new_parser.add_argument("--actor", default=default_actor, help="Provenance actor (registered_by); defaults to $USER or owner")
    project_setrepo_parser = project_subparsers.add_parser("set-repo", help="Set/update an established project's code_repo mapping; --dry-run (default) shows the project.json diff, --confirm writes")
    project_setrepo_parser.add_argument("name", help="Established project name")
    project_setrepo_parser.add_argument("--code-repo", required=True, help="Absolute path to the project's code repo")
    project_setrepo_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setrepo_parser.add_argument("--actor", default=default_actor, help="Provenance actor (registered_by); defaults to $USER or owner")
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
    from tools.aipos_cli.workspace_config import declared_meta_keys, project_meta_declaration

    project_setmeta_parser = project_subparsers.add_parser("set-meta", help="AIPOS-F127: set project.json top-level description keys (phase / note; each must be declared in config.schema project_json); --dry-run (default) shows the diff, --confirm writes")
    project_setmeta_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory; never the home-level active project)")
    _meta_enums = {k: list(v.get("enum") or []) for k, v in declared_meta_keys().items()}  # AIPOS-F170: enum 键(status)帮助列值域
    for _meta_key, _meta_flag in project_meta_declaration()["keys"].items():
        _enum = _meta_enums.get(_meta_key) or []
        project_setmeta_parser.add_argument(_meta_flag, dest=f"meta_{_meta_key}", default=None, metavar="|".join(_enum) or "TEXT",
                                            help=f"project.json {_meta_key} " + (f"(one of {', '.join(_enum)})" if _enum else "(single line)"))
    project_setmeta_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setmeta_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setmeta_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setmeta_parser, "set-meta")  # 缺省预演 / --confirm 才写(与四个写命令同一包装)
    # AIPOS-F176 件①: 项目升级(预演 / 确认; 步骤清单读 verbs.schema verbs.lybra_project_upgrade.upgrade_steps, 唯一实现 project_upgrade)
    project_upgrade_parser = project_subparsers.add_parser("upgrade", help="AIPOS-F176: bring the project up to the latest Lybra: governance-truth migrations, config defaults and workstation charter sync in one go; --dry-run (default) lists every step (what would change, why, already current or not) and unread update counts, --confirm --actor runs the pending mechanical steps in order (stop at the first failure); --guidance lists what the advisors must start using themselves")
    project_upgrade_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_upgrade_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_upgrade_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_upgrade_parser.add_argument("--actor", default=None, help="Commit identity for landing each step (instance name or owner; required with --confirm)")
    project_upgrade_parser.add_argument("--guidance", action="store_true", help="Only list, per advisor instance, the new capabilities the button cannot do (why / what to tell the advisor incl. command / done criterion); read-only")
    project_upgrade_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_upgrade_parser, "upgrade", family="project_upgrade_writers")
    # AIPOS-F198: 工位设置 —— 现状 + 目标分工的切换计划与「告诉顾问」话术(只读; 看板工位设置页同一推导, 唯一实现 workstation_plan)
    project_wsplan_parser = project_subparsers.add_parser(
        "workstation-plan",
        help="AIPOS-F198: show the project's execution mode and workstations (sub-agent executor, pi executor, auditor, card types sent to "
             "pi, target hosts / ssh aliases, validation roots, charter state) and, for a target division of work, the switch-over plan: "
             "product commands in dependency order (preview first, then confirm; done steps marked) plus the messages for the lead advisor "
             "and the specialist advisors; read-only, same text as the board workstation page")
    project_wsplan_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_wsplan_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_wsplan_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted")
    project_wsplan_parser.add_argument("--pi-modes", default=None, metavar="MODES", help="Comma-separated card types to send to the pi executor (verbs.schema lybra_project_workstation_plan.offered_pi_modes, e.g. validation,config,docs); '' = none; omitted = keep the current declaration")
    project_wsplan_parser.add_argument("--pi-executor", default=None, metavar="INSTANCE", help="pi executor workstation instance (existing or new; default = the declared one)")
    project_wsplan_parser.add_argument("--workspace", default=None, metavar="DIR", help="Workstation directory of the pi executor (default = its enrolled location / the discovered workstation)")
    project_wsplan_parser.add_argument("--host", default=None, help="Target host label the pi works over ssh (with --ssh-alias)")
    project_wsplan_parser.add_argument("--ssh-alias", default=None, help="ssh alias of the target host on the governance-root machine (with --host)")
    project_wsplan_parser.add_argument("--owner-ref", default=None, help="Existing Owner decision reference (the decision-log step is then done)")
    project_wsplan_parser.add_argument("--lang", default=None, help="Text language (board locale.supported; default board locale.default)")
    project_wsplan_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F187 件④: 存量卡工作树 / 审计工作树清理(判据 transitions.schema queue_mutations.worktree_cleanup; 唯一实现 worktree_cleanup)
    project_cleanup_parser = project_subparsers.add_parser(
        "cleanup-worktrees",
        help="AIPOS-F187: clean up card and audit work trees of this project across all declared product repos with the same rule as the "
             "automatic cleanup after close / withdraw (terminal and clean -> git worktree remove, branch kept; dirty -> listed, untouched; "
             "in flight / carried by a pending card -> kept); --dry-run (default) lists, --confirm removes")
    project_cleanup_mode = project_cleanup_parser.add_mutually_exclusive_group()
    project_cleanup_mode.add_argument("--dry-run", action="store_true", help="Preview, zero writes (default)")
    project_cleanup_mode.add_argument("--confirm", action="store_true", help="Remove the work trees judged removable (branches kept)")
    project_cleanup_parser.add_argument("--json", action="store_true", help="JSON output")
    # AIPOS-F143 件②: 项目执行方式声明写入口(project.json execution; 声明 config.schema project_json.execution; update_project_json 唯一写路径)
    project_setexec_parser = project_subparsers.add_parser("set-execution", help="AIPOS-F143: declare the project's execution mode (project.json execution: sub-agent executor by default, Owner-approved task modes may go to a pi executor workstation, audits always by an independent pi auditor); every instance must already be enrolled in this project; --dry-run (default) shows the diff, --confirm writes")
    project_setexec_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_setexec_parser.add_argument("--subagent-executor", required=True, metavar="INSTANCE", help="Sub-agent executor instance (enrolled with lybra roles enroll --executor-mode subagent); default assigned_to of drafted cards")
    project_setexec_parser.add_argument("--auditor", required=True, metavar="INSTANCE", help="Independent pi auditor workstation instance (must have a workstation location); default audit_by of drafted cards")
    project_setexec_parser.add_argument("--pi-executor", default=None, metavar="INSTANCE", help="pi executor workstation instance (required when --pi-allowed-task-mode is given)")
    project_setexec_parser.add_argument("--pi-allowed-task-mode", action="append", dest="pi_allowed_task_modes", default=[], metavar="TASK_MODE", help="task_mode the Owner explicitly allows to go to the pi executor (repeatable; enums.schema task_mode); unlisted = sub-agent default")
    project_setexec_parser.add_argument("--default-mode", default=None, help="Default execution mode (config.schema project_json.execution.default_mode enum; default subagent)")
    project_setexec_parser.add_argument("--subagent-max-wait", type=float, default=None, metavar="SECONDS", help="AIPOS-F185: how long `lybra loop` waits for a sub-agent executor/auditor return by default (project.json execution.subagent_max_wait; omitted = keep the current value, absent = verbs.schema lybra_loop.wait_limits default)")
    project_setexec_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setexec_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setexec_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setexec_parser, "set-execution")  # 缺省预演 / --confirm 才写(同一包装)
    # AIPOS-F169: pi 模型声明写入口(project.json execution.pi_models; 并入 set-execution 族: 同一两阶段包装与写后落账)
    from tools.aipos_cli.workspace_config import pi_models_declaration

    project_setmodel_parser = project_subparsers.add_parser("set-model", help="AIPOS-F169: declare which pi model the loop launches for this project (project.json execution.pi_models: project default, or per role with --role); the model must be one the local pi already has configured (pi --list-models), otherwise refused with near matches; takes effect on the next launch (in-flight processes unaffected); --dry-run (default) shows the diff and affected workstations, --confirm writes and lands")
    project_setmodel_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_setmodel_parser.add_argument("--role", default=None, choices=list(pi_models_declaration()["roles"]), help="Override for one role only (by_role.<role>); omitted = the project default for every role")
    project_setmodel_parser.add_argument("--model", required=True, metavar="PROVIDER/ID", help="pi model id as listed by `pi --list-models` (<provider>/<model>, e.g. kiwiai/qwen3.7-plus)")
    project_setmodel_parser.add_argument("--owner-ref", required=True, metavar="TEXT", help="Owner instruction summary or decision record this switch follows (single line, recorded as execution.pi_models.owner_ref)")
    project_setmodel_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_setmodel_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_setmodel_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_setmodel_parser, "set-model")  # 缺省预演 / --confirm 才写(同一包装, 写后落账)
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
    project_checkws_parser = project_subparsers.add_parser("check-workstation", help="AIPOS-F110: check a workstation for loop launch: land-event location, material declaration (remote), gate→workstation ssh probe (dir + harness executable) and workstation→gate ssh reachability via the declared gate_ssh_alias; AIPOS-F164: (local) charter freshness = same check as `lybra sync --dry-run` (F66B render fingerprints), FAIL gives the sync command")
    project_checkws_parser.add_argument("name", help="Established project name")
    project_checkws_parser.add_argument("--instance", required=True, help="Workstation instance (as in its land event)")
    project_checkws_parser.add_argument("--harness", required=True, help="Harness whose launch template executable must be on the workstation PATH (enums.schema harness.launch)")
    project_checkws_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_checkws_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F190 件①②: 跨机临时验证根声明(project.json validation_roots; 唯一实现 validation_root.apply_validation_root_change)
    project_vr_parser = project_subparsers.add_parser(
        "validation-root",
        help="AIPOS-F190: declare a temporary cross-machine validation root (host + base + card + executor/auditor instance; writable = "
             "<base>/<card>/<run-id>/ of the active run only): declare / revoke / run-start / run-finish / sim-bind (--dry-run default, --confirm "
             "writes project.json and lands it), list, helper (print the host-side check helper), init-command (AIPOS-F191: host-side directory-chain init command)")
    project_vr_sub = project_vr_parser.add_subparsers(dest="vr_command", required=True)

    def _vr_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
        sub.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
        sub.add_argument("--workspace-root", help="Target governance root when the project name is omitted")
        sub.add_argument("--json", action="store_true", help="Output JSON")

    vr_declare = project_vr_sub.add_parser("declare", help="Declare a validation root for one executor or auditor instance on one card")
    _vr_common(vr_declare)
    vr_declare.add_argument("--host", required=True, help="Target host label (e.g. the Mac running the validation)")
    vr_declare.add_argument("--base", required=True, help="Absolute base path on that host; writable = <base>/<task-id>/<run-id>/")
    vr_declare.add_argument("--task-id", required=True, help="The instance's own card (executor card, or the audit card for an auditor)")
    vr_declare.add_argument("--instance", required=True, help="Executor or auditor instance enrolled in this project")
    vr_declare.add_argument("--expires-at", default=None, help="Expiry ISO time YYYY-MM-DDTHH:MM:SSZ (default: declared_at + roles.schema validation_root.default_ttl_hours)")
    vr_declare.add_argument("--owner-ref", default=None, help="Owner authorization reference (single line, no credentials). AIPOS-F224: optional when the project has a standing authorization whose hosts include --host (owner_ref then auto-references that authorization record); outside the authorization = refused VR_HOST_NOT_AUTHORIZED. Required when the project has no authorization")
    vr_declare.add_argument("--host-fingerprint", default=None, help="Target host fingerprint hfp1:... (run the helper's `fingerprint` on that host; required on --confirm for a remote host)")
    _project_json_two_phase_flags(vr_declare, "validation-root declare")
    vr_revoke = project_vr_sub.add_parser("revoke", help="Revoke a declaration (every later write is refused)")
    _vr_common(vr_revoke)
    vr_revoke.add_argument("--id", dest="decl_id", required=True, help="Declaration id")
    vr_revoke.add_argument("--reason", required=True, help="Why (single line)")
    _project_json_two_phase_flags(vr_revoke, "validation-root revoke")
    vr_runstart = project_vr_sub.add_parser("run-start", help="Mint a run id for a declaration (only the active run directory is writable)")
    _vr_common(vr_runstart)
    vr_runstart.add_argument("--id", dest="decl_id", required=True, help="Declaration id")
    vr_runstart.add_argument("--candidate", required=True, help="Fixed dev candidate: full 40-hex commit sha in the card's product repo (AIPOS-F205: an environment card bound via validation_source_task / validation_candidate = the source card's product repo, and it must equal the card's validation_candidate)")
    vr_runstart.add_argument("--host-fingerprint", default=None, help="Optional cross-check against the declared host fingerprint")
    _project_json_two_phase_flags(vr_runstart, "validation-root run-start")
    vr_runfinish = project_vr_sub.add_parser("run-finish", help="Close a run (its directory is refused afterwards)")
    _vr_common(vr_runfinish)
    vr_runfinish.add_argument("--id", dest="decl_id", required=True, help="Declaration id")
    vr_runfinish.add_argument("--run-id", required=True, help="Run id to close")
    vr_runfinish.add_argument("--sim-evidence", default=None, help="AIPOS-F194: local copy of the run's .lybra-vr/simulator.json (required when the "
                                                                   "run has a bound simulator; checks only that one was created and deleted)")
    _project_json_two_phase_flags(vr_runfinish, "validation-root run-finish")
    vr_simbind = project_vr_sub.add_parser("sim-bind", help="AIPOS-F194: bind this run's own iOS simulator UDID (created on the host by the helper's "
                                                         "sim-create) into the run record; only that device directory becomes writable")
    _vr_common(vr_simbind)
    vr_simbind.add_argument("--id", dest="decl_id", required=True, help="Declaration id")
    vr_simbind.add_argument("--run-id", required=True, help="Active run id")
    vr_simbind.add_argument("--udid", required=True, help="UDID printed by the helper's sim-create (SIM_CREATED udid=...)")
    vr_simbind.add_argument("--sim-evidence", default=None, help="Optional local copy of the run's .lybra-vr/simulator.json (creation is verified)")
    _project_json_two_phase_flags(vr_simbind, "validation-root sim-bind")
    vr_sethost = project_vr_sub.add_parser("set-host", help="AIPOS-F196: declare how instances on this (governance-root) machine reach a target "
                                                         "host over ssh (project.json validation_roots.hosts.<host> = ssh alias); kickoff / charter "
                                                         "validation-root sections then render `ssh <alias> ...` commands and pipe the helper")
    _vr_common(vr_sethost)
    vr_sethost.add_argument("--host", required=True, help="Target host label used by the declarations (e.g. kiwiai-mac)")
    vr_sethost.add_argument("--ssh-alias", required=True, help="ssh alias on this machine (a Host entry of ~/.ssh/config), e.g. kiwiai-mac")
    vr_sethost.add_argument("--owner-ref", default=None, help="Owner authorization reference (single line, no credentials). AIPOS-F224: optional when the project has a standing authorization whose hosts include --host (owner_ref then auto-references that authorization record); outside the authorization = refused VR_HOST_NOT_AUTHORIZED. Required when the project has no authorization")
    _project_json_two_phase_flags(vr_sethost, "validation-root set-host")
    vr_list = project_vr_sub.add_parser("list", help="List validation root declarations and runs (read-only)")
    _vr_common(vr_list)
    vr_list.add_argument("--task-id", default=None, help="Only this card")
    vr_list.add_argument("--host", default=None, help="Only this host")
    vr_init = project_vr_sub.add_parser("init-command", help="AIPOS-F191: print the host-side directory-chain init command (helper init-run) "
                                                          "for a declaration's active run (read-only)")
    _vr_common(vr_init)
    vr_init.add_argument("--id", dest="decl_id", required=True, help="Declaration id")
    vr_init.add_argument("--instance", default=None, help="Refuse unless the declaration belongs to this instance")
    vr_fetch = project_vr_sub.add_parser("evidence-fetch", help="AIPOS-F200: fetch a remote audit run's evidence (files listed by the host-side "
                                                              "helper evidence-manifest) over the host's declared ssh alias into the audit report "
                                                              "slot copy <verdict_root>/<audit card>/run_evidence/<run-id>/, verifying every sha256; "
                                                              "read-only on the target host, does not write project.json")
    _vr_common(vr_fetch)
    vr_fetch.add_argument("--id", dest="decl_id", required=True, help="Audit instance's declaration id")
    vr_fetch.add_argument("--run-id", default=None, help="Run id (default: the active run, else the last run)")
    vr_helper = project_vr_sub.add_parser("helper", help="Print the host-side check helper (pure python3 stdlib single file) or its sha256; "
                                                      "AIPOS-F203: with --id/--run-id print the helper pinned to that run at run-start "
                                                      "(product upgrades do not change it; project scripts query this at run time)")
    vr_helper.add_argument("name", nargs="?", default=None, help="Established project name (only with --id/--run-id; default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    vr_helper.add_argument("--sha256", action="store_true", help="Print only the helper's sha256")
    vr_helper.add_argument("--id", dest="decl_id", default=None, help="AIPOS-F203: declaration id (with --run-id: the helper pinned to that run)")
    vr_helper.add_argument("--run-id", default=None, help="AIPOS-F203: run id (with --id)")
    vr_helper.add_argument("--workspace-root", help="Target governance root when the project name is omitted (only with --id/--run-id)")
    vr_helper.add_argument("--home-root", help="Governance home root (only with --id/--run-id; accepted for the project family)")
    # AIPOS-F161: pi 工位迁出共用检出(位置声明唯一来源 = enrollment_log 位置事件; 唯一实现 workstation_relocate.relocate_workstation)
    project_relocate_parser = project_subparsers.add_parser("relocate-workstation", help="AIPOS-F161: move an enrolled pi workstation (auditor / executor) of this project out of its current location (e.g. a git checkout shared with other projects) to a location of its own: preview (default) lists the workstation state (credentials by path/mode/size only), old/new location, affected declarations and in-flight checks; --confirm moves it, syncs the new location, appends a relocate location event to the enrollment log, verifies, leaves a read-only pointer in the old directory and lands the log; --rollback moves it back")
    project_relocate_parser.add_argument("name", nargs="?", default=None, help="Established project name (default: the project.json#project of --workspace-root / the governance root containing the current directory)")
    project_relocate_parser.add_argument("--instance", required=True, help="Workstation instance of this project (as in its enrollment log location event)")
    relocate_target = project_relocate_parser.add_mutually_exclusive_group()
    relocate_target.add_argument("--to", default=None, help="New workstation directory (absolute; must not be inside any git checkout; must not exist or be empty). Omitted on preview = print the suggested location")
    relocate_target.add_argument("--rollback", action="store_true", help="Move the workstation back to where it was before its latest relocation")
    project_relocate_parser.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
    project_relocate_parser.add_argument("--workspace-root", help="Target governance root when the project name is omitted (default: the one containing the current directory)")
    project_relocate_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(project_relocate_parser, "relocate-workstation", family="enrollment_log_writers")  # 缺省预演 / --confirm 才写
    _relocate_actor = _project_json_writers_declaration()["landing_actor_flag"]  # AIPOS-F159 落账提交身份旗标(同一声明)
    project_relocate_parser.add_argument(_relocate_actor["flag"], dest="actor", default=_default_actor(), help=_relocate_actor["help"])
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

    # AIPOS-F224: 项目常设授权(声明 verbs.schema lybra_project_authorization; 唯一实现 autonomy_policy)
    project_auth_parser = project_subparsers.add_parser(
        "grant",
        help="AIPOS-F224: project standing grant (项目常设授权; one Owner signature per project: lanes x task modes x drivers / hosts / harness "
             "launch / expiry + total business cards / forbidden zones) replacing per-card envelopes: show (read-only), draft --from-envelopes "
             "(migration preview), sign / extend / revoke (default local preview; --confirm signs through the gate owner_decision_record path)")
    project_auth_sub = project_auth_parser.add_subparsers(dest="auth_command", required=True)

    def _auth_common(sub: argparse.ArgumentParser, *, write: bool) -> None:
        sub.add_argument("name", nargs="?", default=None, help="Established project name (default: project.json#project of --workspace-root)")
        sub.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
        sub.add_argument("--workspace-root", help="Target governance root when the project name is omitted")
        sub.add_argument("--json", action="store_true", help="Output JSON")
        if not write:
            return
        sub.add_argument("--actor", default="owner", help="Actor recorded on the Owner decision (default: owner)")
        sub.add_argument("--decision-summary", default=None, help="Decision summary (default: derived from the authorization)")
        sub.add_argument("--confirm", action="store_true", help="Sign through the gate (lybra_owner_decision_record_dry_run -> _confirm, Owner-only "
                                                                "owner_confirm); without it = local preview, zero writes")
        sub.add_argument("--connection-json", help="connection.json holding the Owner credential (token never printed, fingerprint only)")
        sub.add_argument("--token-role", default="owner", help="Credential role used for --confirm (default owner)")

    def _auth_scope(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--grant-lane", dest="grant_lane", action="append", default=None, help="Lane repo (name in project.json repos.items, or the code repo path); "
                                                                       "repeatable; each gets every --task-mode and --driver given. Inline form "
                                                                       "REPO:MODE[,MODE…]:DRIVER[,DRIVER…] sets that lane's own modes and drivers "
                                                                       "(one signature for several lanes with different drivers)")
        sub.add_argument("--task-mode", action="append", default=None, help="Task mode for the given lanes (enums task_mode; repeatable)")
        sub.add_argument("--driver", action="append", default=None, help="Driver (advisor instance / role name / role class) for the given lanes; repeatable")
        sub.add_argument("--host", action="append", default=None, help="Allowed host label (instance host segment / validation-root host); repeatable")
        sub.add_argument("--launch-harness", action="append", default=None, help="Harness the loop may launch (enums harness.launch); repeatable")
        sub.add_argument("--open-zone", action="append", default=None, help="Open a forbidden zone (default all closed; declared zones); repeatable")
        sub.add_argument("--max-tasks", type=int, default=None, help="Total business cards for the whole project (hard bound)")
        sub.add_argument("--expires-at", default=None, help="Expiry ISO8601 (hard bound)")

    auth_show = project_auth_sub.add_parser("show", help="Show authorizations (latest revision, usage, notices, revisions, migrated envelopes)")
    _auth_common(auth_show, write=False)
    auth_draft = project_auth_sub.add_parser("draft", help="Merge the in-use envelopes into one authorization draft (preview; --confirm signs it)")
    _auth_common(auth_draft, write=True)
    auth_draft.add_argument("--from-envelopes", action="store_true", required=True, help="Draft from the project's in-use envelopes")
    auth_draft.add_argument("--max-tasks", type=int, default=None, help="Override the total (default = sum of the envelopes' remaining quota)")
    auth_sign = project_auth_sub.add_parser("sign", help="Sign a project authorization (the scope given is the whole scope)")
    _auth_common(auth_sign, write=True)
    _auth_scope(auth_sign)
    auth_extend = project_auth_sub.add_parser("extend", help="Extend the current authorization (lanes / modes / drivers / hosts / harnesses / "
                                                             "zones are merged in; --max-tasks / --expires-at replace)")
    _auth_common(auth_extend, write=True)
    _auth_scope(auth_extend)
    auth_revoke = project_auth_sub.add_parser("revoke", help="Revoke the current authorization (a new revision with status revoked)")
    _auth_common(auth_revoke, write=True)
    auth_revoke.add_argument("--reason", required=True, help="Why (single line)")

    # AIPOS-370: envelope mint command (owner-gated)
    envelope_parser = subparsers.add_parser("envelope", help="Owner autonomy envelope operations")
    envelope_subparsers = envelope_parser.add_subparsers(dest="envelope_command")
    envelope_mint_parser = envelope_subparsers.add_parser("mint", help="Mint PreAuthorized autonomy envelope(s): --dry-run = local preview, --confirm = signed through the gate (owner_decision_record envelope path)")
    # AIPOS-F92 件①: --policy-id / --agent-or-role 可重复且按出现顺序成对(一条命令签一组信封, 每张走同一门路径)
    envelope_mint_parser.add_argument("--policy-id", action="append", required=True, help="Policy ID (repeatable; paired in order with --agent-or-role)")
    envelope_mint_parser.add_argument("--agent-or-role", action="append", required=True, help="Agent instance or role covered (repeatable; paired in order with --policy-id)")
    envelope_mint_parser.add_argument("--max-tasks", type=int, required=True, help="Quota in business cards (AIPOS-F192): each card counts once when claimed under this envelope; its return, audit (incl. re-audits), rework, merge and close are not counted again and are not blocked by the quota, only by expiry and revocation")
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

    # AIPOS-F158 件②: migrate 族(存量治理真相迁移; 两阶段 verbs.schema two_phase_protocol.records_migration_writers, 治理根
    # --workspace-root 经 governance_root_resolution.commands 唯一注册口挂载)
    migrate_parser = subparsers.add_parser("migrate", help="AIPOS-F158: governance-truth migrations (default preview; --confirm writes and lands via governance-commit)")
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_command")
    migrate_record_type_parser = migrate_subparsers.add_parser(
        "record-type", help="AIPOS-F158: backfill record_type on unlanded records rejected by guard B④ (type derived from declared record "
                            "locations; underivable ones are listed, not changed)")
    migrate_record_type_parser.add_argument("--actor", help="Commit identity for landing (required with --confirm)")
    migrate_record_type_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(migrate_record_type_parser, "record-type", family="records_migration_writers")
    # AIPOS-F159 件② → AIPOS-F160 件③: 历史缺 decision_log 自动指针补生成(与 record-type 同族同口径: 缺省预演零写入, --confirm 写入并落账;
    # 声明 config.schema decision_level.missing_pointer_backfill)
    migrate_pointer_parser = migrate_subparsers.add_parser(
        "decision-log-pointers", help="AIPOS-F160: backfill decision_log auto-pointers for owner decision records that have none "
                                      "(old and new pointer filenames both count; pointer written by the gate's own generator; "
                                      "underivable records are listed, not changed)")
    migrate_pointer_parser.add_argument("--actor", help="Commit identity for landing (required with --confirm)")
    migrate_pointer_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(migrate_pointer_parser, "decision-log-pointers", family="records_migration_writers")

    # AIPOS-F57: Onboarding guide (from 0 to first card running)
    onboarding_parser = subparsers.add_parser("onboarding", help="AIPOS-F57: From 0 to first card running — full onboarding guide (zero manual editing)")
    onboarding_subparsers = onboarding_parser.add_subparsers(dest="onboarding_command")
    onboarding_guide_parser = onboarding_subparsers.add_parser("guide", help="Generate step-by-step onboarding guide for a new project")
    onboarding_guide_parser.add_argument("project_name", help="Project name (the project you're onboarding)")
    onboarding_guide_parser.add_argument("--home-root", help="Governance home root (defaults to LYBRA_HOME_ROOT env or ~/.lybra/projects)")
    onboarding_guide_parser.add_argument("--gate-url", help=f"Gate URL embedded in the guide's codes (default: loopback per config.schema same_host_rule, port from Owner connection.json mcp.rpc_url > LYBRA_GATE_URL env > {_GATE_URL_DEFAULT}; AIPOS-F153)")
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
    add_owner_view_argument(next_parser)  # AIPOS-F156 件②: --actor 归属视角(只用于项目扫描)

    # AIPOS-F73D: lybra loop — 顾问侧驱动器(信封授权下 watch 产物落盘 → next --run 单步 → 重推导, 直到 completed)。
    # 参数缺省与退出码只声明在 schema/verbs.schema.json verbs.lybra_loop 一处(argparse 缺省 None, 运行时读声明)。
    loop_parser = subparsers.add_parser(
        "loop",
        help="AIPOS-F73D: 顾问侧驱动器 — Owner 信封授权下有界循环推进一张卡到 completed(复用 agent watch + next --run)。"
        "AIPOS-F95: 信封 launch_harnesses 授权且工位条件全满足时, 等待前按 enums.schema harness.launch 模板在本机工位拉起一次 harness 进程"
        "(过程汇总为一行式进度, 产物就绪/超时/早退/中断即清进程组); 否则手工模式(工位敲 /go)。"
        "退出码(verbs.schema lybra_loop.exit_codes): 0=completed, 2=门拒, 3=等待超时/停滞/步数用尽, 4=不可推导/命令不可解析, 5=无信封, 6=部署已更新, 7=同卡已有 loop 在跑(AIPOS-F171B, 用 loop status 观察)",
    )
    # AIPOS-F131 件②: `lybra loop status` 子命令(读运行记录 + 探活 + 判停滞); 推进本身仍须 --task-id(run_loop_cli 校验, 退出码同 argparse 必填 2)
    loop_parser.add_argument("--task-id", help="要推进的卡 ID(推进时必填)")
    loop_parser.add_argument("--envelope", help="policy_id(<policies_root>/<id>.md, project.json paths.policies_root 缺省 5_tasks/policies); 缺省扫描信封目录取首个匹配本卡与驱动方身份的有效信封")
    loop_parser.add_argument("--actor", help="驱动方身份(顾问实例); 缺省=治理根 .lybra/role 的 instance, 再缺省 advisor")
    loop_parser.add_argument("--connection-json", help="透传给 next --run 的 connection.json 路径(token 永不上屏)")
    loop_parser.add_argument("--max-steps", type=int, default=None, help="硬上限: 推导轮数(含等待轮); 缺省读 verbs.schema(20)")
    loop_parser.add_argument("--max-wait", type=float, default=None, help="硬上限: 每次等待产物秒数; 缺省按本卡执行方式读 verbs.schema(子 agent 执行方式 = lybra_loop.wait_limits, project.json execution.subagent_max_wait 可覆盖; 其余沿用 agent watch 1800)")
    loop_parser.add_argument("--interval", type=float, default=None, help="等待轮询间隔秒(经 agent watch, 禁 sleep 自旋); 缺省读 verbs.schema(15)")
    loop_parser.add_argument("--no-launch", action="store_true", default=False,
                             help="AIPOS-F95: 不拉起 harness 进程(即使信封授权), 手工模式: 等待提示「请在 <工位目录> 的 <harness> 会话敲 /go」")
    loop_parser.add_argument("--json", action="store_true", help="Output JSON")
    loop_parser.add_argument("--continues-run", dest="continues_run", default=None,
                             help="AIPOS-F166 件①: 续等关联(loop 在等待步遇部署更新 re-exec 到新快照时自填上一运行 run_id); 人工启动不给")
    loop_actions = loop_parser.add_subparsers(dest="loop_action")
    from tools.aipos_cli.verb_contract import declared_exit_codes as _declared_exit_codes  # AIPOS-F136: help 出口读声明, 不写死
    _loop_status_help = (
        "AIPOS-F131: 看 loop 进度——读运行记录(project.json paths.loop_runs_root)+ 本机探活 + 判停滞; 状态 running/stalled/"
        "launch_dead/loop_dead/ended/unprobeable(verbs.schema lybra_loop.run_record.states)。缺 --task-id = 列本项目全部未结束的运行。"
        "禁 tail/grep 原始日志自判。"
        "AIPOS-F136: 输出附「顾问下一动作」continue_wait/owner_needed/card_done_take_next/investigate"
        "/advisor_fix(AIPOS-F150: 例行门拒, 修法读声明 next_action.routine_rejections); --wait <秒> 有界等到可行动或到时"
        "(退出码读声明: " + ", ".join(f"{k}={c}" for k, c in _declared_exit_codes("lybra_loop_status").items()) + ")"
    )
    loop_status_parser = loop_actions.add_parser("status", help=_loop_status_help, description=_loop_status_help)
    # 与父解析器同名参数用 SUPPRESS 缺省: 不覆盖写在 status 之前的同名参数
    loop_status_parser.add_argument("--task-id", default=argparse.SUPPRESS, help="卡 ID; 缺省 = 本项目全部未结束的 loop 运行")
    loop_status_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output JSON")
    add_lane_argument(loop_status_parser)  # AIPOS-F133 件②
    loop_status_parser.add_argument("--actor", default=argparse.SUPPRESS,
                                    help="AIPOS-F167 件③: 看状态的顾问实例(末尾「Lybra 未读更新」按本实例已读计; 不影响状态判定); "
                                    "缺省 = 本治理根唯一已接入顾问实例(多个时按任一实例未读计)")
    loop_status_parser.add_argument("--wait", type=float, default=None, metavar="SECONDS",
                                    help="AIPOS-F136 件①: 有界等待秒数(须 --task-id; 上限读 verbs.schema lybra_loop_status.wait.max_seconds), "
                                    "经 agent watch 等到顾问下一动作 ≠ continue_wait 或到时, 返回当时状态; 禁 until/sleep 轮询")
    # AIPOS-F138 件③ 的 --verbose 由 AIPOS-F142 件② 统一挂载(verbs.schema envelope_trace.verbose_commands 含 "loop status", 见 build_parser 末)
    # AIPOS-F168 件③: `lybra loop note` —— 给本卡最近一次运行补写 Owner 待决结构字段(声明 verbs.schema lybra_loop_note; --workspace-root 由
    # governance_root_resolution.commands 统一挂载)。与父解析器同名参数用 SUPPRESS 缺省, 不覆盖写在 note 之前的同名参数
    loop_note_parser = loop_actions.add_parser(
        "note", help="AIPOS-F168: 给本卡最近一次 loop 运行补写「要 Owner 定什么 + 选项 + 顾问建议」(收件箱 lybra owner inbox 据此展示); "
                     "缺省预演零写入, --confirm 写入并落账")
    loop_note_parser.add_argument("--task-id", default=argparse.SUPPRESS, help="卡 ID(必填)")
    loop_note_parser.add_argument("--ask", default=None, help="要 Owner 定什么(一句话)")
    loop_note_parser.add_argument("--options", dest="note_options", nargs="+", default=None, metavar="KEY=LABEL",
                                  help="裁决选项, 每项 键=标签(键式样读 verbs.schema lybra_owner_inbox.custom_option.key_pattern)")
    loop_note_parser.add_argument("--recommend", default=None, help="顾问建议(建议选哪个 + 理由)")
    loop_note_parser.add_argument("--actor", default=argparse.SUPPRESS, help="补写人(顾问实例); 缺省 = 运行记录 driver")
    # AIPOS-F180 件①: 授权请求结构化建议(写进 owner_ask.grant, 收件箱建议逐字段以此覆盖推导值; 声明 verbs.schema lybra_loop_note / lybra_owner_inbox.grant_request)
    loop_note_parser.add_argument("--grant-mode", default=None, metavar="TASK_MODE", help="AIPOS-F180: 授权建议 task_mode(如 docs)")
    loop_note_parser.add_argument("--grant-repos", nargs="+", default=None, metavar="REPO", help="AIPOS-F180: 授权建议 lane 仓(逗号分隔或多值)")
    loop_note_parser.add_argument("--grant-max-tasks", default=None, metavar="N", help="AIPOS-F180: 授权建议额度(正整数)")
    loop_note_parser.add_argument("--grant-expires", default=None, metavar="DATE", help="AIPOS-F180: 授权建议到期(YYYY-MM-DD 或带时区 ISO 时刻)")
    loop_note_parser.add_argument("--grant-reason", default=None, help="AIPOS-F180: 授权理由(一句话; 缺省 = 卡标题)")
    # AIPOS-F225 件③: 升 Owner 必带第一档事由(键读 verbs.schema lybra_owner_inbox.tiers.tier1.reasons; 缺 / 不在清单 = 拒写指回顾问)
    loop_note_parser.add_argument("--owner-reason", default=None, metavar="TIER1_KEY",
                                  help="AIPOS-F225: 第一档事由(键 = verbs.schema lybra_owner_inbox.tiers.tier1.reasons); --ask 必带, "
                                       "缺 / 不在清单 = 拒写(技术故障归顾问自处理)")
    # AIPOS-F232 件①: 顾问撤回本人补写的 Owner 请求(原请求留档, 追加撤回字段; 声明 verbs.schema lybra_loop_note)
    loop_note_parser.add_argument("--retract", action="store_true", default=False,
                                  help="AIPOS-F232: 撤回本人补写的 Owner 请求(只许补写人本人、事项未被 Owner 裁决; 原请求留档); 与 --ask / --grant-* 互斥")
    loop_note_parser.add_argument("--retract-reason", default=None, help="AIPOS-F232: 撤回理由(一句话, 可选)")
    loop_note_parser.add_argument("--confirm", action="store_true", default=False, help="写入并落账; 缺省预演")
    loop_note_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output JSON")

    # AIPOS-F231 件②: `lybra loop stop` —— 协作式停线(声明 verbs.schema lybra_loop_stop / lybra_loop.stop; 全部逻辑在 loop_run_record.stop_run)
    loop_stop_parser = loop_actions.add_parser(
        "stop", help="AIPOS-F231: 协作式停线——给本卡在跑的 loop 写停线请求并有界等它保存现场、清理本卡进程 / 隧道 / 模拟器、核无残留后释放独占资源; "
                     "loop 已不在 = 本命令按运行记录清理并写终态; 上次停线有未清理项 = 复核")
    loop_stop_parser.add_argument("--task-id", default=argparse.SUPPRESS, help="卡 ID(必填)")
    loop_stop_parser.add_argument("--reason", default=None, help="停线原因(进运行记录 stop.reason)")
    loop_stop_parser.add_argument("--owner-words", default=None, help="Owner 原话(带 = 结束原因 stopped_by_owner; 须同给 --relayed-by)")
    loop_stop_parser.add_argument("--relayed-by", default=None, help="转达 Owner 原话的顾问实例")
    loop_stop_parser.add_argument("--actor", default=argparse.SUPPRESS, help="请求者(顾问实例; 缺省 = --relayed-by)")
    loop_stop_parser.add_argument("--timeout", dest="stop_timeout", type=float, default=None,
                                  help="有界等待 / 清理秒数(缺省与上限读 verbs.schema lybra_loop.stop)")
    loop_stop_parser.add_argument("--dry-run", action="store_true", default=False, help="预演: 只列将停的运行与将清理 / 未清理项, 零写入、不发信号")
    loop_stop_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output JSON")

    # AIPOS-F168 件①②: `lybra owner inbox / decide` —— Owner 待决收件箱与一处裁决(声明 verbs.schema lybra_owner_inbox / lybra_owner_decide;
    # 全部逻辑在 owner_inbox, 本处只注册参数)
    owner_parser = subparsers.add_parser("owner", help="AIPOS-F168: Owner 待决收件箱(inbox)与一处裁决(decide)")
    owner_actions = owner_parser.add_subparsers(dest="owner_action")
    owner_inbox_parser = owner_actions.add_parser(
        "inbox", help="跨 home 根全部已注册项目汇总需 Owner 定的事项(要定什么 / 顾问建议 / 影响 / 选项 / 裁决命令); 只读")
    owner_inbox_parser.add_argument("--home-root", help="home 根; 缺省 = resolve_home_root 同一优先级梯")
    owner_inbox_parser.add_argument("--project", help="只看该项目(home 根下注册名)")
    owner_inbox_parser.add_argument("--include-paused", action="store_true", default=False,
                                    help="AIPOS-F170: 也列 project.json status=paused 的项目(缺省不列); AIPOS-F181: status=test 的项目同此(声明 status.inbox_hidden)")
    owner_inbox_parser.add_argument("--stale-before", default=None, metavar="DATE",
                                    help="AIPOS-F170: 遗留视图——只列发生早于该日期(YYYY-MM-DD / ISO)的事项, 按项目分组每项一行并给批量裁决命令")
    owner_inbox_parser.add_argument("--json", action="store_true", help="JSON 输出(与看板 /api/owner/inbox 同一数据)")
    owner_decide_parser = owner_actions.add_parser(
        "decide", help="裁决一个收件箱事项: 缺省预演零写入; --confirm 落 owner_decision_record(+ needs_owner 卡经既有出口解除)并落账")
    owner_decide_parser.add_argument("--item", default=None, help="事项 ID(收件箱列出的 OI-…); 与 --items 二选一")
    owner_decide_parser.add_argument("--items", default=None, metavar="ID,ID,…",
                                     help="AIPOS-F170: 逗号分隔的多个事项 ID, 同一裁决批量(整批预演, 任一被拒整批不写); 与 --item 二选一")
    owner_decide_parser.add_argument("--choice", required=True, help="选项键(该事项列出的选项之一)")
    owner_decide_parser.add_argument("--relayed-by", default=None, metavar="INSTANCE",
                                     help="AIPOS-F170: 转达裁决的顾问实例(Owner 本人直接敲写 owner); 必填")
    owner_decide_parser.add_argument("--owner-words", default=None, metavar="TEXT", help="AIPOS-F170: Owner 原话摘要(单行); 必填")
    owner_decide_parser.add_argument("--note", default=None, help="裁决备注(顾问侧原样显示)")
    owner_decide_parser.add_argument("--home-root", help="home 根; 缺省 = resolve_home_root 同一优先级梯")
    owner_decide_parser.add_argument("--actor", default="owner", help="裁决人身份(缺省 owner)")
    owner_decide_parser.add_argument("--confirm", action="store_true", default=False, help="写入并落账; 缺省预演")
    owner_decide_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # AIPOS-F138 件①: lybra charter — 拉取式输出实例的渲染后章程(他机会话开局经 ssh 读取; 参数由 verbs.schema lybra_charter 生成)
    from tools.aipos_cli.charter_render import add_charter_arguments as _add_charter_arguments

    charter_parser = subparsers.add_parser(
        "charter",
        help="AIPOS-F138: 输出实例的渲染后章程全文到 stdout(与 sync 同一渲染器; 他机会话开局经 ssh 在治理根所在机读取, 产品不推送)",
    )
    _add_charter_arguments(charter_parser)

    # AIPOS-F187 件①: lybra kickoff — 子 agent 开工稿由产品生成(声明 verbs.schema lybra_kickoff; 唯一实现 subagent_kickoff)
    kickoff_parser = subparsers.add_parser(
        "kickoff",
        help="AIPOS-F187: print a self-contained kickoff brief for the sub-agent that works a claimed card (identity, charter command, "
             "work tree / branch / base sha, report location and required frontmatter, write boundary and no-write list, verification "
             "requirements, residual-process check, stop after the report); read-only; hand it to the sub-agent as is, append card-specific notes after it",
    )
    kickoff_parser.add_argument("--task-id", required=True, help="Card id (executor card or audit card)")
    from tools.aipos_cli.subagent_kickoff import kickoff_roles  # AIPOS-F214 件④: 值域读 enums.schema kickoff_role(不写死)

    kickoff_parser.add_argument("--role", choices=list(kickoff_roles()), default=None,
                                help="Brief for this role; default = by the card (audit card = auditor); a mismatch is refused")
    kickoff_parser.add_argument("--json", action="store_true", help="JSON output (text plus the projected values)")

    # AIPOS-F167 件③: lybra updates — 看 Lybra 更新说明(顾问须知)与记已读(--ack 两阶段, 族 advisor_update_writers; 声明 verbs.schema lybra_updates)
    updates_parser = subparsers.add_parser(
        "updates",
        help="AIPOS-F167: show Lybra update notes for advisors (unread for --actor by default, --all for every note); "
             "--ack marks them read for --actor (default preview, --confirm writes the read-marker record and lands it)",
    )
    updates_parser.add_argument("--actor", default=None, help="Advisor instance (read state is per instance in this governance root); "
                                "viewing defaults to the only advisor instance enrolled in this governance root (with several, a note counts as "
                                "unread while any of them has not read it); required with --ack")
    updates_parser.add_argument("--all", action="store_true", help="List every update note (marked read/unread), not only the unread ones")
    updates_parser.add_argument("--ack", action="store_true", help="Mark the currently unread update notes as read for --actor")
    updates_parser.add_argument("--json", action="store_true", help="Output JSON")
    _project_json_two_phase_flags(updates_parser, "updates", family="advisor_update_writers")

    # AIPOS-F171 件①: lybra roadmap — 结构化路线图(里程碑 + 验收标准)声明与编辑(声明 verbs.schema lybra_roadmap; 文件形
    # config.schema governance_structure.roadmap; 写命令两阶段族 roadmap_writers; 唯一实现 tools/aipos_cli/roadmap.py)
    roadmap_parser = subparsers.add_parser("roadmap", help="AIPOS-F171: project roadmap (milestones + acceptance criteria) in the governance root's roadmap file "
             "(project.json paths.roadmap_file, default governance/roadmap.yaml): show (read-only) / add-milestone / add-criterion / "
             "set / remove (default preview, --confirm writes + decision_log pointer + landing)",
    )
    roadmap_sub = roadmap_parser.add_subparsers(dest="roadmap_command")
    _roadmap_actor = _project_json_writers_declaration()["landing_actor_flag"]  # 落账提交身份旗标(与 project 族同一声明)

    def _roadmap_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("name", nargs="?", default=None, help="Project name (default: the project.json#project of --workspace-root / "
                         "the governance root containing the current directory)")
        sub.add_argument("--home-root", help="Governance home root; defaults to resolver (env/config/default)")
        sub.add_argument("--workspace-root", help="Target governance root when the project name is omitted")
        sub.add_argument("--json", action="store_true", help="Output JSON")

    roadmap_show = roadmap_sub.add_parser("show", help="Read-only: milestones / criteria with linked-card counts and the unlinked "
                                          "(free-floating) cards; no roadmap file = first-use hint")
    _roadmap_common(roadmap_show)
    # 里程碑 / 标准的字段旗标 = 路线图形声明(config.schema governance_structure.roadmap *_keys)的投影, 不手写字段旗标
    from tools.aipos_cli.roadmap import roadmap_cli_fields

    _rm_fields = roadmap_cli_fields()
    roadmap_show.add_argument(_rm_fields["lane_filter"]["flag"], dest="lane", default=None, help=_rm_fields["lane_filter"]["help"])
    # AIPOS-F173: lybra roadmap status —— 里程碑进度 / 偏差信号 / 下一步建议(只读; 推导唯一实现 roadmap_drift.roadmap_status,
    # 声明 verbs.schema lybra_roadmap.status; 阈值 project.json roadmap_drift)
    roadmap_status_parser = roadmap_sub.add_parser("status", help="Read-only: per-milestone progress (satisfied criteria / total), "
                                                   "drift signals (unlinked ratio / stalled / uncovered criteria / repeated rework / "
                                                   "overdue / recent roadmap changes) and next-step advice; no roadmap file = first-use hint")
    _roadmap_common(roadmap_status_parser)
    roadmap_status_parser.add_argument(_rm_fields["lane_filter"]["flag"], dest="lane", default=None, help=_rm_fields["lane_filter"]["help"])
    roadmap_add_ms = roadmap_sub.add_parser("add-milestone", help="Add a milestone (two-phase)")
    roadmap_add_crit = roadmap_sub.add_parser("add-criterion", help="Add an acceptance criterion under a milestone (two-phase)")
    for _rm_parser, _rm_kind in ((roadmap_add_ms, "milestone"), (roadmap_add_crit, "criterion")):
        for _rm_field in _rm_fields[_rm_kind]:
            _rm_parser.add_argument(_rm_field["flag"], dest=_rm_field["dest"], required=_rm_field["required"], default=None,
                                    help=_rm_field["help"])
    roadmap_set = roadmap_sub.add_parser("set", help="Change one field of a milestone / criterion (two-phase; ids cannot be renamed)")
    roadmap_set.add_argument("--id", required=True, help="Milestone or criterion id")
    roadmap_set.add_argument("--field", required=True, help="Field name (settable fields per config.schema governance_structure.roadmap)")
    roadmap_set.add_argument("--value", required=True, help="New value (empty string clears an optional field)")
    roadmap_remove = roadmap_sub.add_parser("remove", help="Remove a milestone / criterion that no card links to "
                                            "(two-phase; linked = refused, set the milestone status to dropped instead)")
    roadmap_remove.add_argument("--id", required=True, help="Milestone or criterion id")
    _project_json_two_phase_flags(roadmap_add_ms, "add-milestone", family="roadmap_writers")
    _project_json_two_phase_flags(roadmap_add_crit, "add-criterion", family="roadmap_writers")
    _project_json_two_phase_flags(roadmap_set, "set", family="roadmap_writers")
    _project_json_two_phase_flags(roadmap_remove, "remove", family="roadmap_writers")
    # AIPOS-F175 件③: lybra roadmap verify —— Owner 抽查结论经顾问记录(写既有 owner_verifications 记录; 不符自动起草修复卡; 两阶段同族)
    roadmap_verify = roadmap_sub.add_parser("verify", help="AIPOS-F175: record the Owner's spot-check result for an owner_check "
                                            "criterion (two-phase; writes an owner_verifications record; mismatch also drafts a fix card "
                                            "linked to the same criterion)")
    roadmap_verify.add_argument("--criterion", required=True, help="Criterion id (verify = owner_check)")
    from tools.aipos_cli.roadmap import verify_declaration  # AIPOS-F214 件④: --result 值域读 verbs lybra_roadmap.verify.results(不写死)

    verify_results = list(verify_declaration()["results"])
    roadmap_verify.add_argument("--result", required=True, choices=verify_results, help=f"Owner's result: {' | '.join(verify_results)}")
    roadmap_verify.add_argument("--relayed-by", dest="relayed_by", default=None,
                                help="Advisor instance relaying the Owner's result (owner if the Owner runs it); required")
    roadmap_verify.add_argument("--owner-words", dest="owner_words", default=None, help="The Owner's own words (one line); required")
    roadmap_verify.add_argument("--note", default=None, help="Note (goes into the fix card body on mismatch)")
    roadmap_verify.add_argument("--card", default=None, help="Linked card that was checked (default: the card the derivation "
                                "says awaits the check)")
    _project_json_two_phase_flags(roadmap_verify, "verify", family="roadmap_writers")
    for _roadmap_sub_parser in (roadmap_add_ms, roadmap_add_crit, roadmap_set, roadmap_remove, roadmap_verify):
        _roadmap_common(_roadmap_sub_parser)
        # AIPOS-F175: verify 的 --actor = 抽查记录 decided_by(与 owner decide 一致缺省 owner); 其余写命令照旧
        _roadmap_sub_parser.add_argument(_roadmap_actor["flag"], dest="actor",
                                         default="owner" if _roadmap_sub_parser is roadmap_verify else _default_actor(),
                                         help=_roadmap_actor["help"])

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
    # AIPOS-F207 件①: 顾问复核结论(值域 card.schema dependency_gate.conclusion.outcomes); block = 不满足他卡 closure 依赖
    # AIPOS-F221 件②: choices = 结论值域 + 复核打回返工(verbs.schema lybra_mark_concluded.review_rework.outcome; 唯一渲染 mark_concluded_outcome_choices)
    from tools.aipos_cli.board_adapter import mark_concluded_outcome_choices

    mark_concluded_parser.add_argument("--outcome", choices=list(mark_concluded_outcome_choices()), default=None,
                                       help="AIPOS-F207: review conclusion pass|block (block = does not satisfy other cards' closure "
                                            "dependency, same as gate closure judgement); default pass with a hint. AIPOS-F221: rework = "
                                            "review failed on a non-code fast-lane card -> advisor review record + rework round, card stays claimed")
    mark_concluded_parser.add_argument("--focus-items", default=None,
                                       help="AIPOS-F221: only with --outcome rework (required): JSON array of rework points")
    mark_concluded_parser.add_argument("--acceptance-criteria", default=None,
                                       help="AIPOS-F221: only with --outcome rework (optional): JSON array of acceptance criteria")
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
    # AIPOS-F215 件③: help 三态标签读 verbs.schema state_labels(唯一渲染 post_merge_regression.baseline_cli_help)
    from tools.aipos_cli.post_merge_regression import baseline_cli_help

    regression_baseline_parser = regression_subparsers.add_parser("baseline", help=baseline_cli_help())
    regression_baseline_parser.add_argument("--sha", help="要查的提交(完整 40 位原样用; 短 sha/引用在产品仓解析); 缺省 = 产品仓基线分支当前 HEAD")
    regression_baseline_parser.add_argument("--repo-root", help="解析 sha 用的产品仓; 缺省 = project.json repos.default / code_repo")
    # AIPOS-F188 件①: 三态之 pending(该 sha 合并后回归在跑)可有界等待; 到点仍在跑 = self_run_required(声明 verbs.schema lybra_regression_baseline.wait)
    regression_baseline_parser.add_argument("--wait", type=float, metavar="<秒>",
                                            help="AIPOS-F188: 结论为 pending(合并后回归在跑)时有界等待该秒数, 醒来重判; 到点仍在跑 = self_run_required")
    regression_baseline_parser.add_argument("--json", action="store_true", help="Output JSON")
    # AIPOS-F166 件③(gap #153): lybra regression run — 合并后回归状态与补跑(预演缺省, --confirm 执行; 声明 verbs.schema lybra_regression_run)
    regression_run_parser = regression_subparsers.add_parser(
        "run",
        help="AIPOS-F166: 合并后回归补跑。缺省预演(只读: 该提交的回归状态 + 将跑什么、结论落哪); --confirm 前台执行(先落进行中记录, "
        "跑完同一记录更新为结论, F149 同格式); --confirm --background = 与 finalize async 同一后台入口。仍在跑 / 已有完整结论 = 拒",
    )
    regression_run_parser.add_argument("--sha", required=True, help="合并提交(完整 40 位原样; 短 sha/引用在产品仓解析)")
    regression_run_parser.add_argument("--repo-root", help="产品仓; 缺省 = project.json repos.default / code_repo")
    regression_run_parser.add_argument("--task-id", help="结论记到哪张卡; 缺省 = 该提交的回归 / finalization 记录所属卡")
    regression_run_parser.add_argument("--actor", help="--confirm 时必给: 记录与补落账的执行身份(驱动方实例)")
    regression_run_parser.add_argument("--confirm", action="store_true", help="执行(缺省只预演, 不写任何文件)")
    regression_run_parser.add_argument("--background", action="store_true", help="与 --confirm 同用: 后台执行, 立即返回 pid / 日志 / 进行中记录")
    regression_run_parser.add_argument("--json", action="store_true", help="Output JSON")

    # AIPOS-F179 件①: lybra run-long — 长验证命令脱离运行 + 分段等待(声明 verbs.schema lybra_run_long; 唯一实现 run_long)
    run_long_parser = subparsers.add_parser(
        "run-long", help="AIPOS-F179: 长验证命令(全量测试 / UI 套件等, 超过 agent bash 工具单次时限)脱离运行 + 日志 / 退出码落卡产物落点 + 分段等待。"
        "执行体与审计体共用, 不连门、不读凭据。治理根用全局 `lybra --workspace-root <治理根> run-long ...`")
    run_long_sub = run_long_parser.add_subparsers(dest="run_long_command")
    run_long_start = run_long_sub.add_parser("start", help="脱离起命令(新会话), 立即返回; 名字已用 = 拒")
    run_long_start.add_argument("--task-id", required=True, help="卡 ID(执行卡或审计卡); 运行目录落在该卡报告落点下")
    run_long_start.add_argument("--name", required=True, help="本次运行名(字母数字 . _ -)")
    run_long_start.add_argument("--cwd", required=True, help="命令执行目录(绝对路径; 审计 = 审计分配工作树, 执行 = 卡工作树)")
    run_long_start.add_argument("--max-seconds", type=float, default=None, help="命令运行上限秒数(缺省读 verbs.schema lybra_run_long.max_seconds_default)")
    run_long_start.add_argument("--json", action="store_true", help="Output JSON")
    run_long_start.add_argument("run_long_argv", nargs=argparse.REMAINDER, help="`--` 之后的命令: 一个参数 = 命令串(经 bash -c); 多个 = argv")
    run_long_wait = run_long_sub.add_parser("wait", help="等一段(≤ --timeout 秒)直到命令结束; 仍在跑 = exit 3, 再调一次")
    run_long_wait.add_argument("--task-id", required=True, help="卡 ID")
    run_long_wait.add_argument("--name", required=True, help="运行名")
    run_long_wait.add_argument("--timeout", type=float, default=None, help="本段最长等待秒数(取本会话工具单次时限以内; 缺省读声明)")
    run_long_wait.add_argument("--json", action="store_true", help="Output JSON")
    run_long_status = run_long_sub.add_parser("status", help="只读: 运行中 / 已结束 + 退出码 / 失联")
    run_long_status.add_argument("--task-id", required=True, help="卡 ID")
    run_long_status.add_argument("--name", default=None, help="运行名; 缺省 = 列本卡全部运行")
    run_long_status.add_argument("--json", action="store_true", help="Output JSON")

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
    add_owner_view_argument(brief_parser)  # AIPOS-F156 件②

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


def _queue_return_repair(args: argparse.Namespace, repo_root: Path) -> int:
    """`lybra queue return-repair`(AIPOS-370F2): 诊断并修复卡住的交回(无会话记录 = block → reopen → 带会话记录重认领)。
    AIPOS-F213 件①(F208 P1-6 E35): 修复序列的三步写入(卡迁移 + 重认领会话记录)写完经队列写落账唯一薄壳一次落账
    (queue_writers return-repair; 原 3 处 mutate_queue_task 直调绕过落账); 某步被拒 = 已写的前几步照落账再出拒因。"""
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
                    from tools.aipos_cli.board_adapter import queue_write_paths

                    # AIPOS-F213 件①: 三步同一搬卡出口(block → reopen → 带会话记录重认领), 写出文件累计后经队列写落账唯一薄壳落账
                    steps = (("block", {"reason": "AIPOS-370F2 return-repair: missing session record", "with_records": False},
                              "Blocked", "Block"),
                             ("reopen", {"reason": "AIPOS-370F2 return-repair: prepare for reclaim with session record",
                                         "with_records": False}, "Reopened", "Reopen"),
                             ("claim", {"with_records": True}, "Reclaimed with session record", "Reclaim"))
                    written: list[str] = []
                    reclaim_result: dict[str, Any] = {}
                    for action, step_kwargs, done_label, fail_label in steps:
                        step_result = mutate_queue_task(repo_root, action, task_id=args.task_id, actor=args.actor, dry_run=False,
                                                        profiles=profiles, **step_kwargs)
                        if step_result.get("verdict") == Verdict.BLOCK:
                            diagnosis["verdict"] = Verdict.BLOCK
                            diagnosis["repair_error"] = f"{fail_label} failed: {step_result.get('blocking_reasons')}"
                            landing = _land_queue_entry(repo_root, diagnosis, actor=args.actor, subcommand="return-repair",
                                                        task_id=args.task_id, written=written)  # 已写的前几步照落账
                            print(render_json(diagnosis), file=sys.stderr)
                            if landing is not None and not args.json:
                                _emit_landing(landing)
                            return 1
                        written += queue_write_paths(step_result)
                        diagnosis["repair_actions"].append(f"{done_label}: {args.task_id}")
                        reclaim_result = step_result
                    diagnosis["landing_paths"] = list(written)
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
            # AIPOS-F218 件③: claimed + 已有交回 = 现行模型正常形态(交回后保持 claimed 直到 finalize / 结案), 本工具不适用 → 看推导核;
            # 措辞读声明 transitions nodes.N2.return_repair.claimed_with_return(不再建议手工改队列)
            from tools.aipos_cli.next_resolver import _transition_node
            from tools.aipos_cli.workspace_config import lybra_command_prefix
            from tools.schema_loader import SchemaLoadError

            normal = (_transition_node("N2").get("return_repair") or {}).get("claimed_with_return")
            if not isinstance(normal, dict) or not all(isinstance(normal.get(k), str) and normal[k].strip()
                                                       for k in ("verdict", "diagnosis", "recommended_action")):
                raise SchemaLoadError("transitions.schema.json nodes.N2.return_repair.claimed_with_return(verdict / diagnosis / "
                                      "recommended_action)未声明齐(AIPOS-F218)")
            next_command = f"{lybra_command_prefix(repo_root)} next --task-id {args.task_id}"
            diagnosis["diagnosis"].append(normal["diagnosis"].format(count=len(task_returns)))
            diagnosis["recommended_action"] = normal["recommended_action"].format(next_command=next_command)
            diagnosis["next_command"] = next_command
            diagnosis["verdict"] = normal["verdict"]
        else:
            diagnosis["diagnosis"].append(f"Task is in {task.get('queue_state')} state")
            diagnosis["recommended_action"] = "No stuck return detected"
            diagnosis["verdict"] = "OK"
        
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    landing = None
    if diagnosis.get("verdict") == "REPAIRED":
        landing = _land_queue_entry(repo_root, diagnosis, actor=args.actor, subcommand="return-repair", task_id=args.task_id,
                                    written=list(diagnosis.pop("landing_paths", [])))
    diagnosis.pop("landing_paths", None)
    print(render_json(diagnosis))
    if diagnosis.get("verdict") == Verdict.BLOCK:
        return 1
    if landing is not None and not args.json:
        return _emit_landing(landing)
    return 0 if landing is None or landing["landed"] else int(landing["exit_code"])


@cli_envelope_trace_entry  # AIPOS-F142 件②: 本次 CLI 调用结束即恢复 [ENVELOPE_TRACE] 开关原状(in-process 嵌套调用不泄漏)
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # AIPOS-F142 件②: [ENVELOPE_TRACE] 唯一开关的 CLI 决定点(缺省关; --verbose / 调试环境变量开; in-process 嵌套沿用外层)
    decide_cli_envelope_trace(bool(getattr(args, "verbose", False)))
    if getattr(args, "dev_tree", None):
        # AIPOS-F152 件②: --dev-tree 只在入口第一个参数生效(入口在加载代码前消费); 到这里 = 位置不对或未经入口, 拒(不静默忽略)
        from tools.aipos_cli.deploy_runtime import version_line

        print(f"lybra: --dev-tree 须作为 lybra 入口的第一个参数(入口在加载任何代码前消费它); 本进程运行的是: {version_line()}",
              file=sys.stderr)
        return 2
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
                # AIPOS-F156 件③: 签发实例(唯一判定 draft_writer.resolve_publish_signer; 凭据文件经唯一解析口)→ 卡稿 created_by
                signer = resolve_publish_signer(repo_root, args.actor, _queue_gate_connection_json(args, repo_root))
                refusal = signer["refusal"] or apply_signer_created_by(metadata, signer)
                if refusal:
                    print(f"Error: {refusal}", file=sys.stderr)
                    return 1
                result = create_draft(repo_root, metadata, body, dry_run=args.dry_run, replace=args.replace)
                result["signer"] = signer
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
                for line in render_revision_lines(result):  # AIPOS-F220: 修订差异摘要 / 留档行(无修订 = 无输出)
                    print(line)
                print(_signer_line(result["signer"]))
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
                # AIPOS-F156 件③: 签发实例 = 发布记录 actor(卡归属唯一来源); 多实例缺 --actor / 核不过 = 拒, 不落任何文件
                signer = resolve_publish_signer(repo_root, args.actor, _queue_gate_connection_json(args, repo_root))
                if signer["refusal"]:
                    print(f"Error: {signer['refusal']}", file=sys.stderr)
                    return 1
                result = publish_draft(repo_root, args.path, dry_run=args.dry_run, actor=signer["actor"])
                result["signer"] = signer
            except (OSError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(result))
            else:
                print(render_draft_result_text(result))
                print(_signer_line(signer))
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
                issuer_root_source = ""
                if args.roles_command in ("enroll-code", "enroll-deliver"):
                    # AIPOS-F199 件③(gap #203): 签码凭据按 --workspace-root > --governance-root > F144 解析梯定位(_roles_issuer_root 唯一实现)
                    try:
                        workspace_root, issuer_root_source = _roles_issuer_root(args)
                    except (FileNotFoundError, ValueError) as exc:
                        if connection_target is not None:  # 显式 --connection-json: 凭据文件已指明, 治理根仅作接入判定, 按原取法(行为不变)
                            workspace_root, issuer_root_source = _resolve_workspace_for_command(args), f"原取法(治理根解析: {exc})"
                        else:
                            print(f"Error: roles {args.roles_command}: 签发凭据所在治理根解析不出: {exc}", file=sys.stderr)
                            print("下一步: 在持有签发凭据的治理根目录下运行, 或加 --governance-root <治理根> / --workspace-root <治理根>, "
                                  "或 --connection-json <connection.json 路径>", file=sys.stderr)
                            return 1
                else:
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
                if not getattr(args, "confirm", False):
                    # AIPOS-F151 件④(gap #121): 缺省预演——本机经唯一实现的计划(custom_role_registration_plan)算将建 / 改的 token 引用与
                    # 角色类, 零写入、不取凭据、不调门; --confirm 才经门动词 lybra_roles_register 写
                    from tools.aipos_cli.custom_roles import custom_role_registration_plan

                    plan = custom_role_registration_plan(workspace_root, args.name, args.builtin_class)
                    _render_roles_preview("register", args, {"owner_authorization_ref": owner_auth_ref, "plan": plan}, [
                        f"{'新建' if plan['action'] == 'create' else '更新'} token 引用 {plan['token_ref']}"
                        f"(指纹 {plan['fingerprint'] or '确认时新铸'}) 角色 {plan['name']} → 类 {plan['builtin_class']}"
                        + (f"(原类 {plan['previous_class']})" if plan.get("previous_class") else ""),
                        f"scopes(按类派生) {', '.join(plan['scopes']) or '-'}",
                        f"注册表 {plan['registry']}",
                    ])
                    return 0
                
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
                confirm_phase = bool(getattr(args, "confirm", False))  # AIPOS-F151 件④: 缺省预演 / --confirm 才写
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
                        dry_run=not confirm_phase,
                    )
                    if result.get("dry_run") and not getattr(args, "json", False):
                        _render_roles_preview("remove", args, result, [
                            f"删 token 引用 {e['token_ref'] or '-'}(指纹 {e['fingerprint']}) 角色 {e['role']} 类 {e.get('role_class') or '-'} 实例 {e['instance']}"
                            for e in result["would_remove"]] + [f"凭据文件 {result['connection_path']}(确认时先备份 + 写删除记录 + 重载门)"])
                        return 0
                    if getattr(args, "json", False):
                        print(render_json(result))
                    else:
                        _render_token_lifecycle_result(result)
                    return 1 if result.get("verdict") == Verdict.BLOCK or result.get("blocking_reasons") else 0
                # AIPOS-352F1: remove a custom role
                from tools.aipos_cli.custom_roles import custom_role_removal_plan, remove_custom_role
                owner_auth_ref = str(getattr(args, "owner_authorization_ref", "") or "").strip() or None
                reason = str(getattr(args, "reason", "") or "").strip()
                by = owner_auth_ref or "owner"
                if not confirm_phase:
                    plan = custom_role_removal_plan(workspace_root, args.name)
                    _render_roles_preview("remove", args, {"owner_authorization_ref": owner_auth_ref, "plan": plan}, [
                        f"删 token 引用 {e['token_ref'] or '-'}(指纹 {e['fingerprint']}) 角色 {plan['name']} 类 {e.get('role_class') or '-'}"
                        + (f" 实例 {e['agent_instance']}" if e.get("agent_instance") else "")
                        for e in plan["entries"]] or [f"注册表无角色 {plan['name']} 的条目: 确认也无改动(幂等)"])
                    return 0
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
                # AIPOS-F24A 大項A: CLI 薄壳化 —— 发码唯一实现=门动词 in-server(运输凭证注册与门内存注册表同进程, 死凭证类缺陷根除)。
                # 本 CLI 只经 enroll_deliver.gate_enroll_code 调 lybra_enroll_code_dry_run / lybra_enroll_code_confirm 两阶段动词, 本地发码
                # 路径已删除(grep 证明单实现); gate 不可达=如实报错, 绝不回退到本地发码(本地发码=死运输凭证)。
                # AIPOS-F151 件①(gap #106): 两阶段(verbs.schema two_phase_protocol.roles_writers)——缺省预演 = 只调 dry_run 动词
                # (显示将签的角色 / 实例 / 治理根 / 有效期, 不出码); --confirm 才 dry_run → confirm 出码。
                from tools.aipos_cli.confirm_client import GateError
                from tools.aipos_cli.enroll_deliver import ENROLL_CODE_DRY_RUN_VERB, GateVerbRefused, gate_enroll_code

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
                confirm_phase = bool(getattr(args, "confirm", False))
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
                try:
                    # AIPOS-F157 件①: --actor = 签发实例(按实例取凭据唯一口); 缺 = 按角色取, 同角色多实例即拒
                    client, conn_data, token_role_used, signer = _roles_issuer_client(
                        workspace_root, connection_target, token_role, str(getattr(args, "actor", "") or "").strip() or None,
                        root_source=issuer_root_source)
                except _RolesIssuerError as exc:
                    return _enroll_code_fail(str(exc), next_step=exc.next_step)
                # ② 治理根(F24A): 显式参数优先, 缺省=本机 connection.json 的 workspace_root —— 显式化传入, 码内治理根不再依赖门进程环境解析
                governance_root = governance_root_arg or str(conn_data.get("workspace_root") or "").strip() or None
                params = {
                    "role": args.role,
                    "instance": instance,
                    "ttl": ttl,
                    "gate_url": gate_url_arg,
                    "governance_root": governance_root,
                    "owner_authorization_ref": owner_auth_ref,
                    "reason": reason,
                    "actor": signer or f"cli:roles-enroll-code:{token_role_used}",
                }
                try:
                    issued = gate_enroll_code(client, params, confirm=confirm_phase)
                except GateVerbRefused as exc:
                    return _enroll_code_fail(
                        str(exc),
                        next_step=("按上面 teaching error 修正参数后重试。" if exc.verb == ENROLL_CODE_DRY_RUN_VERB
                                   else "按上面 teaching error 处理(dry_run_token TTL=600s, 过期重跑本命令)。"),
                    )
                except GateError as exc:
                    return _enroll_code_fail(
                        f"gate call failed: {exc}",
                        next_step=("检查门: lybra serve status / 先 lybra serve start。"
                                   "此为产品侧/连接故障, 与你无关, 禁自行诊断修复门/服务/部署 —— 报告顾问即可。"
                                   "薄壳没有本地发码回退路径(本地发码=死运输凭证, 已废除)。"),
                    )
                if not confirm_phase:
                    preview = issued["dry_run"].get("preview") or {}
                    label = _two_phase_family_declaration("roles_writers")["outcome_labels"]["preview"]
                    result_out = {
                        "ok": True, "operation": "roles_enroll_code", "phase": "dry_run", "dry_run": True, "issued": False,
                        "issued_via": "gate_verb_thin_shell(F24A)", "preview": preview,
                        "next_step": "确认后同一条命令加 --confirm 才出码(码只显示一次)",
                    }
                    if getattr(args, "json", False):
                        print(render_json(result_out))
                    else:
                        print(f"roles enroll-code {args.role}: {label}")
                        print(f"  Role: {preview.get('role', args.role)}  Instance: {preview.get('instance') or instance or '(any)'}")
                        print(f"  Governance root: {preview.get('governance_root') or governance_root}")
                        print(f"  有效期: {preview.get('ttl_seconds') or ttl} 秒(自 --confirm 签码起算)")
                        print(f"  Gate URL: {preview.get('gate_url') or gate_url_arg}")
                        print(f"  Owner authorization ref: {owner_auth_ref}")
                        print("  未出码、零写入; 确认后同一条命令加 --confirm 才出码(码只显示一次)")
                    return 0
                confirm = issued["confirm"]
                # AIPOS-F93 件②: 交付文案 = onboarding.enroll_delivery(门 paste_text / paste_instruction 同一渲染源, CLI 与门一份)
                from tools.aipos_cli.onboarding import enroll_delivery

                delivery = enroll_delivery(str(confirm.get("self_contained_code") or ""))
                # FIX-2 兼容: --json 输出稳定含 self_contained_code/code_id/fingerprint 在顶层
                result_out = {
                    "ok": True,
                    "operation": "roles_enroll_code",
                    "phase": "confirm",
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
                    print("  · 同机(治理根所在机)接入不必经手此码: lybra roles enroll-deliver 签码 + 接入一体, 码不出进程")
                return 0
            elif args.roles_command == "enroll-deliver":
                # AIPOS-F151 件②(gap #106/#92): 薄壳, 全部逻辑在 enroll_deliver.enroll_deliver(唯一实现; 码只在本进程内)。
                # 门客户端凭据与 enroll-code 同一取法(_roles_issuer_client); 两阶段旗标 = roles_writers 同一包装
                from tools.aipos_cli.confirm_client import GateError
                from tools.aipos_cli.enroll_deliver import EnrollDeliverRollbackError, GateVerbRefused, enroll_deliver

                try:
                    client, _conn, token_role_used, signer = _roles_issuer_client(
                        workspace_root, connection_target, str(getattr(args, "token_role", "advisor") or "advisor"),
                        str(getattr(args, "actor", "") or "").strip() or None,  # AIPOS-F157 件①: 与 enroll-code 同一取法
                        root_source=issuer_root_source)
                except _RolesIssuerError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    print(f"下一步: {exc.next_step}", file=sys.stderr)
                    return 1
                try:
                    result = enroll_deliver(
                        client=client, role=args.role, instance=args.instance, governance_root=args.governance_root,
                        workspace=args.workspace, owner_authorization_ref=args.owner_authorization_ref,
                        confirm=bool(getattr(args, "confirm", False)), ssh_target=args.ssh, harness_kind=args.harness,
                        harness_dir=args.harness_dir, harness_host=args.harness_host, executor_mode=args.executor_mode,
                        gate_url=args.gate_url, ttl_seconds=args.ttl, reason=str(args.reason or "").strip(),
                        actor=signer or f"cli:roles-enroll-deliver:{token_role_used}",
                    )
                except (GateVerbRefused, GateError, EnrollDeliverRollbackError, RuntimeError, ValueError) as exc:
                    if getattr(args, "json", False):
                        print(render_json({"ok": False, "operation": "enroll-deliver", "error": str(exc)}), file=sys.stderr)
                    else:
                        print(f"Error: {exc}", file=sys.stderr)
                    return 1
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    _render_enroll_deliver(result)
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

                # AIPOS-F153 件①: 作用域显式(--log / --project, 缺省拒)+ 两阶段同 F125 包装(缺省预演, --confirm 才追加)
                labels = _two_phase_family_declaration("enrollment_log_writers")["outcome_labels"]
                result = void_instance_events(workspace_root, args.instance, by=args.actor, reason=args.reason,
                                              dry_run=not args.confirm, log=args.void_log, project=args.void_project,
                                              code_ids=list(args.void_code_ids or []), before=args.void_before)
                if getattr(args, "json", False):
                    print(render_json(result))
                else:
                    head = labels["preview"] if result["dry_run"] else labels["written"]
                    print(f"roles enroll-void {result['instance']}: {head}")
                    for item in result["entries"]:
                        print(f"  log: {item['log']}(project {item['project']})")
                        print(f"    作废 {item['event_count']} 个事件(code_id {item['code_id_count']} 个), 行号区间 {item['voids_lines']};"
                              f" 该日志该实例余未作废事件 {item['left_live']} 个")
                        for ev in item["events"]:
                            print(f"      行 {ev['line']}: {ev['ts']}  {ev['action']}  code_id={ev['code_id']}")
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
                        "在治理根所在机接入请改用 lybra roles enroll-deliver(签码 + 兑换同一进程, 码不出进程; AIPOS-F151)。\n"
                        "旧裸码兑换: 在本条命令上加 --gate-url http://<host>:<port>(AIPOS-F160: 不再给带码命令行示例)"
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

    if args.command == "project" and getattr(args, "project_command", None) == "cleanup-worktrees":
        # AIPOS-F187 件④: 薄壳 —— 治理根经 _find_repo_root_for_args(子命令级 --workspace-root), 不走 home 根解析
        from tools.aipos_cli.worktree_cleanup import run_cleanup_cli
        return run_cleanup_cli(args)

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
                # AIPOS-226 裁定 2=a: 本地 Owner 脚手架(不铸凭据、不过门), 门按 home 根扫描发现新项目; 唯一实现 workspace_config.scaffold_project。
                # AIPOS-F216 件②: 原 AIPOS-F24「~/.lybra/connection.json 在场 = 经门动词 lybra_project_new」分支删除——该门动词自 F24 起
                # 从未登记进门 TOOL_HANDLERS(门路径恒失败), 门 token 按项目绑定不承接建项目; 建项目恒为本机脚手架。
                # AIPOS-F92 件②: home 根只经 AIPOS-226 优先级梯解析, 打印结果与来源(门只扫描 home 根, 不登记 home 根外的治理根)
                print(f"home 根: {home}(来源: {home_source}); 项目建在 {home / args.name}")
                # AIPOS-F92: 非交互(stdin 非终端, 如 agent 会话)不弹询问, 用缺省协作配置(可后改)
                collaboration_profile = _ask_project_type_interactive() if sys.stdin.isatty() else None
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
                return _project_json_two_phase_emit(
                    "set-repos", target, {k: declared[k] for k in ("dry_run", "changed", "written", "diff")}
                    | {"project_json": str(declared["project_json"])}, actor=args.actor, json_out=getattr(args, "json", False), extra=repos_view,
                    details=[f"repos.default = {repos_view['default']}  code_repo = {repos_view['code_repo']}"]
                    + [f"repos.items.{k} = {v}" for k, v in repos_view["items"].items()]
                    + [f"repos.no_deploy = {repos_view['no_deploy']}(本仓不部署: finalize deploy_status=not_applicable)"] * bool(repos_view["no_deploy"]))
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
                return _project_json_two_phase_emit(
                    "set-paths", target, outcome, actor=args.actor, json_out=getattr(args, "json", False),
                    details=[f"paths.{change['key']}: {change['before']!r} → {change['after']!r}" for change in outcome["changes"]])
            if args.project_command == "validation-root":
                return _validation_root_cli(args, home)
            if args.project_command == "grant":  # AIPOS-F224(子命令名避开凭据判据词 authorization: loop 日志 / 状态按 harness_launch.redact_progress 整行隐去含该词的行)
                return _project_authorization_cli(args, home)
            if args.project_command == "upgrade":
                return _project_upgrade_cli(args, home)
            if args.project_command == "workstation-plan":
                return _project_workstation_plan_cli(args, home)
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
                return _project_json_two_phase_emit(
                    "set-meta", target, outcome, actor=args.actor, json_out=getattr(args, "json", False),
                    details=[f"{change['key']}: {change['before']!r} → {change['after']!r}" for change in outcome["changes"]])
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
                        default_mode=args.default_mode, subagent_max_wait=args.subagent_max_wait, dry_run=not args.confirm)
                except ExecutionDeclarationError as exc:
                    print(f"Error: {exc.code}(project.json 未改动)", file=sys.stderr)
                    for problem in exc.problems:
                        print(f"  - {problem}", file=sys.stderr)
                    return 1
                view = outcome["execution"]
                return _project_json_two_phase_emit(
                    "set-execution", target, {k: outcome[k] for k in ("dry_run", "changed", "written", "diff", "project_json")},
                    actor=args.actor, json_out=getattr(args, "json", False), extra={"execution": view},
                    details=[f"execution.{k} = {view[k]!r}" for k in ("default_mode", "subagent_executor", "pi_allowed_task_modes",
                                                                       "pi_executor", "auditor")]
                    + ([f"execution.subagent_max_wait = {args.subagent_max_wait:g}(子 agent 执行方式下 loop 等交回缺省上限, 秒)"]
                       if args.subagent_max_wait is not None else [])
                    + ["各实例已核: 本项目已接入(审计者 / pi 执行者有工位位置); 起草缺省与发卡核身份同读本段"])
            if args.project_command == "set-model":
                # AIPOS-F169: 唯一实现 enrollment.set_project_pi_model(pi --list-models 核可用 + project_execution 校验 + update_project_json 唯一写路径)
                from tools.aipos_cli.enrollment import set_project_pi_model
                from tools.aipos_cli.workspace_config import ExecutionDeclarationError

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                try:
                    outcome = set_project_pi_model(target["project_root"], model=args.model, owner_ref=args.owner_ref, role=args.role,
                                                   dry_run=not args.confirm)
                except ExecutionDeclarationError as exc:
                    print(f"Error: {exc.code}(project.json 未改动)", file=sys.stderr)
                    for problem in exc.problems:
                        print(f"  - {problem}", file=sys.stderr)
                    return 1
                return _project_json_two_phase_emit(
                    "set-model", target, {k: outcome[k] for k in ("dry_run", "changed", "written", "diff", "project_json")},
                    actor=args.actor, json_out=getattr(args, "json", False),
                    extra={k: outcome[k] for k in ("pi_models", "model", "role", "target_key", "catalog", "affected", "effect_note")},
                    details=[f"模型可用: {outcome['model']} 在本机 pi 已配置模型中({outcome['catalog']['detail']})",
                             f"execution.pi_models.{outcome['target_key']} = {outcome['model']}  owner_ref = {args.owner_ref.strip()}"]
                    + [_render_pi_model_affected(entry) for entry in outcome["affected"]]
                    + [f"生效: {outcome['effect_note']}"])
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
                return _project_json_two_phase_emit(
                    "set-return-summary", target, {k: outcome[k] for k in ("dry_run", "changed", "written", "diff", "project_json")},
                    actor=args.actor, json_out=getattr(args, "json", False),
                    extra={"return_summary_source": {k: view[k] for k in ("frontmatter_keys", "section_markers")}},
                    details=[f"return_summary_source.frontmatter_keys = {view['frontmatter_keys']!r}",
                             f"return_summary_source.section_markers = {view['section_markers']!r}",
                             "交回就绪判据 / 推导核交回步 / artifact ingest 同读本段(未给的键取 transitions 缺省)"])
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
                return _project_json_two_phase_emit(
                    "set-workstation", target, {k: declared[k] for k in ("project_json", "dry_run", "changed", "written", "diff")},
                    actor=args.actor, json_out=getattr(args, "json", False),
                    extra={k: declared[k] for k in ("instance", "gate_ssh_alias", "material_access")},
                    details=[f"workstations.{declared['instance']}: gate_ssh_alias={declared['gate_ssh_alias']} "
                             f"material_access={declared['material_access']}"])
            if args.project_command == "relocate-workstation":
                # AIPOS-F161: 唯一实现 workstation_relocate.relocate_workstation; 确认后落账经 _land_project_writes(F158/F159 同一提交口)
                from tools.aipos_cli.workstation_relocate import RelocationRefused, relocate_workstation, render_relocation

                target = _project_write_target(args, home, args.name)
                if target is None:
                    return 1
                try:
                    result = relocate_workstation(target["project_root"], args.instance, to=args.to, rollback=args.rollback,
                                                  dry_run=not args.confirm, actor=args.actor)
                except RelocationRefused as exc:
                    print(f"Error: {exc.code}(位置声明未改; 工位态未动)", file=sys.stderr)
                    for problem in exc.problems:
                        print(f"  - {problem}", file=sys.stderr)
                    return 1
                landing = None
                if not result["dry_run"]:
                    landing = _land_project_writes(target["project_root"], result["written"], actor=args.actor,
                                                   subcommand="relocate-workstation", project=target["project"])
                rc = 1 if result["refusals"] or (not result["dry_run"] and not result.get("ok")) else 0
                if landing is not None and not landing["landed"]:
                    rc = int(landing["exit_code"])
                if getattr(args, "json", False):
                    print(render_json({**result, "landing": _landing_view(landing)}))
                    return rc
                labels = _two_phase_family_declaration("enrollment_log_writers")["outcome_labels"]
                print(_project_json_writers_declaration()["target_line"].format(
                    project=target["project"], source=target["source"], project_json=result["project_json"]))
                print(f"project relocate-workstation {result['instance']}: {labels['preview'] if result['dry_run'] else labels['written']}")
                for line in render_relocation(result):
                    print(line)
                if landing is not None:
                    print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
                return rc
            if args.project_command == "check-workstation":
                from tools.aipos_cli.loop_driver import check_workstation
                from tools.aipos_cli.workspace_config import resolve_project_root

                report = check_workstation(resolve_project_root(home, args.name), args.instance, args.harness)
                if getattr(args, "json", False):
                    print(render_json(report))
                else:
                    for item in report["checks"]:
                        print(f"{'✓' if item['ok'] else '✗'} {item['check']}: {item['detail']}")
                    if report.get("privilege_guard"):  # AIPOS-F165 件③: 拉起禁提权状态(applied / disabled / degraded)
                        guard = report["privilege_guard"]
                        print(f"拉起禁提权(no_new_privs): {guard['state']} — {guard['detail']}")
                    if report.get("pi_model"):  # AIPOS-F169: 该工位下次拉起将用的模型与来源(声明 / pi 缺省)
                        from tools.aipos_cli.loop_run_record import model_label

                        pm = report["pi_model"]
                        print(f"拉起模型 [{pm['role']}]: {model_label(pm)}" + (f" — {pm['availability']}" if pm.get("availability") else ""))
                    print(f"check-workstation {args.instance}: {'OK' if report['ok'] else 'FAILED'}")
                return 0 if report["ok"] else 1
            if args.project_command == "set-repo":
                # AIPOS-F216 件②: 本机经唯一实现 workspace_config.update_project_repo 预演 / 写入(原 AIPOS-F24「~/.lybra/connection.json
                # 在场 = 经门动词 lybra_project_set_repo_dry_run → _confirm」分支删除: 该门动词自 F24 起从未登记进门 TOOL_HANDLERS,
                # 门路径恒失败; 门 token 按项目绑定, 不承接改他项目产品仓)。
                from tools.aipos_cli.workspace_config import CardRepoUnresolved, update_project_repo

                target = _project_write_target(args, home, args.name)  # AIPOS-F127 件①: 与所在治理根冲突 = 拒(预演前)
                if target is None:
                    return 1
                # AIPOS-F125: 缺省预演 = 本机经唯一实现算 project.json diff + 校验(零写入; 不取凭据、不连门); --confirm 才写。
                try:
                    outcome = update_project_repo(home, args.name, args.code_repo, registered_by=args.actor, dry_run=not args.confirm)
                except (CardRepoUnresolved, FileNotFoundError) as exc:
                    print(f"Error: {exc}(project.json 未改动)", file=sys.stderr)
                    return 1
                return _project_json_two_phase_emit(
                    "set-repo", target, {k: outcome[k] for k in ("project_json", "dry_run", "changed", "written", "diff")},
                    actor=args.actor, json_out=False, details=[f"code_repo = {outcome['code_repo']}"])
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
                    # AIPOS-F159 件①: project.json + 切换留痕经同一落账薄壳落账(提交身份 = --by)
                    landing = _land_project_writes(proj_root, [project_json_path(Path(proj_root)), trail], actor=args.by,
                                                   subcommand="dispatch-mode set", project=str(target["project"]))
                    if args.json:
                        print(render_json({
                            "ok": True, "project_root": proj_root,
                            "dispatch_mode": new_mode, "trail_path": str(trail), "landing": _landing_view(landing),
                        }))
                    else:
                        print(f"✓ dispatch_mode set to {new_mode}  (trail: {trail})")
                        print(landing["line"], file=sys.stdout if landing["landed"] else sys.stderr)
                    return 0 if landing["landed"] else int(landing["exit_code"])
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

    if args.command == "run-long":
        # AIPOS-F179 件①: 薄壳——start / wait / status 全在 run_long(唯一实现), 退出码读声明 verbs.schema lybra_run_long.exit_codes
        if getattr(args, "run_long_command", None) not in ("start", "wait", "status"):
            parser.parse_args([args.command, "--help"])
            return 2
        from tools.aipos_cli import run_long as run_long_mod
        from tools.schema_loader import SchemaLoadError

        try:
            governance_root = Path(_find_repo_root_for_args(args))
        except (FileNotFoundError, SchemaLoadError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return run_long_mod.exit_code("refused")
        return run_long_mod.cli(args, governance_root)

    if args.command == "regression":
        # AIPOS-F149 件③: 只读薄壳——治理根经唯一薄壳 _find_repo_root_for_args, sha 解析与判据在 post_merge_regression(唯一实现)
        if getattr(args, "regression_command", None) not in ("baseline", "run"):
            parser.parse_args([args.command, "--help"])
            return 2
        from tools.aipos_cli import post_merge_regression as pmr_mod
        from tools.schema_loader import SchemaLoadError

        if args.regression_command == "run":
            # AIPOS-F166 件③: 薄壳——状态判定 / 补跑 / 记录 / 落账全在 post_merge_regression.rerun(唯一实现), 退出码读声明
            try:
                governance_root = Path(_find_repo_root_for_args(args))
                outcome = pmr_mod.rerun(governance_root, args.sha, repo_root=args.repo_root, task_id=args.task_id, actor=args.actor,
                                        confirm=bool(args.confirm), background=bool(args.background))
            except (FileNotFoundError, SchemaLoadError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            if args.json:
                print(render_json(outcome))
            else:
                stream = sys.stderr if outcome.get("refused") else sys.stdout
                print("\n".join(outcome["lines"]), file=stream)
            return int(outcome["exit_code"])

        try:
            governance_root = _find_repo_root_for_args(args)
            commit, sha_source = pmr_mod.resolve_audit_sha(governance_root, args.sha, args.repo_root)
        except (FileNotFoundError, ValueError, OSError, SchemaLoadError) as exc:  # 拒因原文 + 出口, 非 0 退出(fail-closed)
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.wait is not None:  # AIPOS-F188 件①: pending 有界等待(秒数越界 = ValueError, 非 0 退出)
            try:
                from tools.aipos_cli.loop_run_record import wait_regression_baseline

                result = wait_regression_baseline(governance_root, commit, args.wait)
            except (ValueError, OSError, SchemaLoadError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        else:
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
            actor=getattr(args, "view_actor", None),  # AIPOS-F156 件②: 归属视角
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
            if verdict != Verdict.PASS and result.get('category'):
                # AIPOS-F155 件①(gap #133): 非 PASS 结果的结构化拒因码(finalize 结果 category: 仓级合入锁 REPO_MERGE_IN_PROGRESS /
                # 合并冲突 MERGE_CONFLICT 等)以 `error_code: <码>` 行出声——loop 运行记录 end_codes 由 extract_end_codes 唯一抽取
                print(f"error_code: {result['category']}")
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

    if args.command == "migrate":
        if getattr(args, "migrate_command", None) == "decision-log-pointers":
            return _migrate_decision_log_pointers_cli(args)
        if getattr(args, "migrate_command", None) != "record-type":
            parser.print_help()
            return 2
        return _migrate_record_type_cli(args)

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
            from tools.aipos_cli.envelope_mint import mint_preview  # AIPOS-F172: 预演唯一实现(看板信封页同调)

            try:
                results = mint_preview(payloads, governance_root=envelope_root, actor=args.actor)
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

    if args.command == "updates":
        # AIPOS-F167 件③: 薄壳 — 全部逻辑在 advisor_updates_cli(退出码读 verbs.schema lybra_updates.exit_codes)
        from tools.aipos_cli.advisor_updates_cli import run_updates_cli

        return run_updates_cli(args)

    if args.command == "roadmap":
        # AIPOS-F171 件①: 薄壳 —— 全部逻辑在 roadmap(读 / 校验 / 两阶段写 + 指针 + 落账唯一实现)
        return _roadmap_command(args)

    if args.command == "owner":
        # AIPOS-F168 件①②: 薄壳 —— 全部逻辑在 owner_inbox(收集 / 渲染 / 裁决唯一实现), 退出码读 verbs.schema。跨项目命令(home 根),
        # 先于下方单治理根解析分派
        return _owner_command(args)

    if args.command == "charter":
        # AIPOS-F138 件①: 只读薄壳 — 全部逻辑在 charter_render(渲染唯一 render_charter)。AIPOS-F144: 先于下方通用治理根解析分派,
        # 治理根由 run_charter_cli 经同一薄壳解析, 解析不出走本命令声明的退出码(lybra_charter.exit_codes.unreadable)
        from tools.aipos_cli.charter_render import run_charter_cli
        return run_charter_cli(args)

    if args.command == "kickoff":
        # AIPOS-F187 件①: 只读薄壳 — 全部逻辑在 subagent_kickoff(渲染唯一 render_kickoff_brief), 治理根经同一薄壳解析
        from tools.aipos_cli.subagent_kickoff import run_kickoff_cli
        return run_kickoff_cli(args)

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
            signer_role_for_actor,
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
        # AIPOS-F154 件②: 收编的签发者 = --actor(门判 actor = token 绑定驱动方实例, 记 adopted_by)——按 --actor 实例选凭据,
        # 唯一口 signer_role_for_actor(F134 driver_token_entry; 未接入即拒, 禁取首个驱动方 token)
        try:
            role = signer_role_for_actor(connection_json_path=conn_json_path, actor=args.actor, repo_root=repo_root)
        except ValueError as exc:
            print(f"Error resolving role: {exc}", file=sys.stderr)
            return 1
        json_out = bool(getattr(args, "json", False))
        if getattr(args, "confirm", False):
            exit_code, resp = execute_two_phase_verb(verb_base="lybra_queue_adopt", args_dict=verb_args,
                                                     connection_json_path=conn_json_path, role=role, json_output=json_out,
                                                     signer_instance=args.actor)
        else:
            exit_code, resp = execute_single_phase_via_gate(verb_name="lybra_queue_adopt_dry_run",
                                                            args_dict={**verb_args, "preview_only": True},
                                                            connection_json_path=conn_json_path, role=role, json_output=json_out,
                                                            signer_instance=args.actor)
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
        # AIPOS-F184 件②(gap #165): block / reopen 写入成功后即按路径清单落账(queue_writers; claim 由门 / N6 落账, 不在此)
        # AIPOS-F213 件①: 经写后落账唯一薄壳(登记 post_write_landing.queue_write_sites)
        landing = None
        if args.queue_command in {"block", "reopen"} and not args.dry_run and result.get("verdict") != Verdict.BLOCK:
            landing = _land_queue_entry(repo_root, result, actor=args.actor, subcommand=args.queue_command,
                                        task_id=str(result.get("task_id") or getattr(args, "task_id", None) or ""))
        if args.json:
            print(render_json(result))
        else:
            print(render_queue_mutation_text(result))
        if result.get("verdict") == Verdict.BLOCK:
            return 1
        return _emit_landing(landing) if not args.json else (0 if landing is None or landing["landed"] else int(landing["exit_code"]))

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
        # AIPOS-F223 件②: 改卡面 audit_runtime_evidence = 判据 artifact_ingest.runtime_evidence_amend_problems(新值形校验同出卡; 已认领卡
        # --restricted 只许改 card.schema restricted_amend.claimed_card_subfields 所列子键, 如在途验证卡补运行证据执行主机 host)
        from tools.aipos_cli.artifact_ingest import (
            normalize_runtime_evidence_declaration, render_equivalent_subfields, restricted_amend_subfields, runtime_evidence_amend_problems,
            runtime_evidence_declaration,
        )

        evidence_key = str(runtime_evidence_declaration()["card_field"])
        evidence_notes: list[str] = []
        if isinstance(amendments, dict) and evidence_key in amendments:
            from tools.aipos_cli.adapter_response import blocked_response
            from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

            card_path, _queue = _find_task_in_queue(Path(repo_root), str(args.task_id))
            card_fm = _read_frontmatter(card_path) if card_path else {}
            current = card_fm.get(evidence_key)
            # AIPOS-F233 件②④: 新值别名按本卡交付主体改写为规范形(改写前后原文进输出 warnings); 已认领卡 --restricted 另认渲染等价的 commands
            subject_fm = {**card_fm, **{k: v for k, v in amendments.items() if k != evidence_key}, "task_id": str(args.task_id)}
            normalized = normalize_runtime_evidence_declaration(amendments[evidence_key], card_fm=subject_fm, task_id=str(args.task_id))
            amendments[evidence_key] = normalized["value"]
            evidence_notes = [*normalized["notes"], *normalized["literals"]]  # 改写前后原文 + 字面花括号提示(不拒)
            problems = runtime_evidence_amend_problems(current, amendments[evidence_key], claimed=restricted_fields is not None,
                                                       card_fm=subject_fm)
            if problems:
                print(render_json(blocked_response(
                    operation="queue_amend", dry_run=bool(args.dry_run), category="VALIDATION_ERROR", message="; ".join(problems),
                    data={"task_id": args.task_id, "field": evidence_key, "validation_errors": problems},
                    safety_notice="AIPOS-F223: audit_runtime_evidence amend 判据 artifact_ingest.runtime_evidence_amend_problems")))
                return 1
            if restricted_fields is not None and evidence_key in (set(restricted_amend_subfields()) | set(render_equivalent_subfields())):
                restricted_fields = set(restricted_fields) | {evidence_key}
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
        if evidence_notes:  # AIPOS-F233 件②: 占位规范化改写前后原文 / 字面花括号提示
            result.setdefault("warnings", []).extend(evidence_notes)
        # AIPOS-F213 件①(F208 P1-6): 修订写入成功后即落账(卡面 + 修订记录; 原 CLI amend 不落账)——同一薄壳 queue_writers
        landing = None
        if not args.dry_run and result.get("ok") and result.get("verdict") != Verdict.BLOCK:
            landing = _land_queue_entry(repo_root, result, actor=args.actor, subcommand="amend",
                                        task_id=str((result.get("data") or {}).get("task_id") or args.task_id))
        if any((err or {}).get("category") == "NOT_FOUND" for err in result.get("errors") or []):
            # AIPOS-F220 件①: 不在队列而本卡有未发布草稿 = 点名草稿修订入口(draft create --replace; 判定唯一 draft_revision_state)
            from tools.aipos_cli.draft_writer import draft_revision_state, duplicate_revision_hint

            try:
                hint = duplicate_revision_hint(repo_root, str(args.task_id)) \
                    if draft_revision_state(repo_root, str(args.task_id))["state"] == "draft" else None
            except ValueError as exc:  # 卡号转不成草稿文件名: 不给提示, 拒因原文照出
                hint = None
                result.setdefault("warnings", []).append(str(exc))
            if hint and hint not in result.setdefault("blocking_reasons", []):
                result["blocking_reasons"].append(hint)
        print(render_json(result))
        if result.get("verdict") == Verdict.BLOCK:
            return 1
        if landing is not None and not args.json:
            return _emit_landing(landing)
        return 0 if landing is None or landing["landed"] else int(landing["exit_code"])

    if args.command == "queue" and getattr(args, "queue_command", None) == "withdraw":
        from tools.aipos_cli.board_adapter import withdraw_task
        try:
            result = withdraw_task(
                task_id=args.task_id,
                actor=args.actor,
                reason=args.reason,
                dry_run=args.dry_run,
                repo_root=repo_root,
                ack_in_transit=bool(getattr(args, "ack_in_transit", False)),
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        # AIPOS-F184 件②(gap #165): 撤回写入成功后即按路径清单落账(源路径删除 + withdrawn 新卡面)
        landing = None
        if not args.dry_run and result.get("ok") and result.get("verdict") != Verdict.BLOCK:
            landing = _land_queue_entry(repo_root, result, actor=args.actor, subcommand="withdraw",
                                        task_id=str((result.get("data") or {}).get("task_id") or args.task_id))
        print(render_json(result))
        if result.get("verdict") == Verdict.BLOCK:
            return 1
        if landing is not None and not args.json:
            return _emit_landing(landing)
        return 0 if landing is None or landing["landed"] else int(landing["exit_code"])

    if args.command == "queue" and getattr(args, "queue_command", None) == "orphan-audits":
        # AIPOS-F234 件②: 存量孤儿审计卡(唯一实现 board_adapter.withdraw_orphan_audits; 写出经 queue_writers 落账)
        from tools.aipos_cli.board_adapter import withdraw_orphan_audits

        if args.confirm and not str(args.actor or "").strip():
            print("Error: --confirm 须给 --actor <你的顾问实例>(撤回执行身份与落账提交身份)", file=sys.stderr)
            return 2
        try:
            result = withdraw_orphan_audits(actor=str(args.actor or ""), confirm=bool(args.confirm), repo_root=repo_root)
        except (FileNotFoundError, OSError, ValueError, LookupError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        landing = None
        if args.confirm and result.get("written"):
            landing = _land_queue_entry(repo_root, result, actor=str(args.actor), subcommand="orphan-audits",
                                        task_id=",".join(i["task_id"] for i in result["items"] if i.get("ok")), written=result["written"])
        if args.json:
            print(render_json(result))
        else:
            for line in result["lines"]:
                print(line)
            if not args.confirm and result["items"]:
                print("预演(未执行、未写任何文件)。执行: 原命令加 --confirm --actor <你的顾问实例>")
        rc = 0 if result.get("ok") else 1
        if landing is not None and not args.json:
            return _emit_landing(landing) or rc
        return rc if landing is None or landing["landed"] else int(landing["exit_code"])

    if args.command == "queue" and getattr(args, "queue_command", None) == "supersede":
        # AIPOS-F184 件③: 顾问凭 Owner 裁决撤换在途卡(唯一实现 board_adapter.supersede_task; 写出文件经 queue_writers 落账)
        from tools.aipos_cli.board_adapter import supersede_task

        # AIPOS-F213 件②: --owner-words 在带 --decision-id 时可省(取自该裁决); 两者都无 = 撤换前置拒(missing_words, 既有拒法)
        try:
            result = supersede_task(args.task_id, by=args.by, actor=args.actor, owner_words=args.owner_words,
                                    owner_ref=args.owner_ref, dry_run=not args.confirm, repo_root=repo_root,
                                    carry_branch=bool(getattr(args, "carry_branch", False)),
                                    decision_id=getattr(args, "decision_id", None))
        except (FileNotFoundError, OSError, ValueError, LookupError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        landing = None
        if args.confirm and result.get("written"):
            landing = _land_queue_entry(repo_root, result, actor=args.actor, subcommand="supersede", task_id=result["task_id"],
                                        written=result["written"])
        if args.json:
            print(render_json(result))
        else:
            print(result["line"])
            if result.get("carry_line"):
                print(f"  {result['carry_line']}")  # AIPOS-F186: 承继(原卡分支 / tip / 工作树 / 未提交改动计数; 确认后 = 新卡卡面已记)
            for warning in result.get("warnings") or []:
                print(f"  ! {warning}")
            if result.get("decision", {}).get("target_path") and not args.confirm:
                print(f"  决策记录将写: {result['decision']['target_path']}")
            if args.confirm and result.get("ok"):
                print("  Owner 收件箱该卡的待决事项随卡进 withdrawn 自动消失")
        if not result.get("ok"):
            if landing is not None and not args.json:
                _emit_landing(landing)
            return 1
        if landing is not None and not args.json:
            return _emit_landing(landing)
        return 0 if landing is None or landing["landed"] else int(landing["exit_code"])

    if args.command == "queue" and getattr(args, "queue_command", None) in ("pause", "resume"):
        # AIPOS-F231 件①: Owner 暂停 / 恢复在途卡(唯一实现 board_adapter.pause_task; 写出的决策记录经 queue_writers 落账)
        from tools.aipos_cli.board_adapter import pause_task
        from tools.aipos_cli.owner_inbox import InboxUsageError
        from tools.aipos_cli.verb_contract import declared_exit_code

        verb = f"lybra_queue_{args.queue_command}"
        try:
            result = pause_task(args.task_id, action=args.queue_command, relayed_by=args.relayed_by, owner_words=args.owner_words,
                                owner_ref=args.owner_ref, dry_run=not args.confirm, repo_root=repo_root)
        except InboxUsageError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return declared_exit_code(verb, "usage")
        except (FileNotFoundError, OSError, ValueError, LookupError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return declared_exit_code(verb, "blocked")
        landing = None
        if args.confirm and result.get("written"):
            landing = _land_queue_entry(repo_root, result, actor=str(result.get("relayed_by") or args.relayed_by),
                                        subcommand=args.queue_command, task_id=result["task_id"], written=result["written"])
        if args.json:
            print(render_json(result))
        else:
            print(result["line"])
        if not result.get("ok"):
            return declared_exit_code(verb, "blocked")
        if landing is not None and not args.json:
            return _emit_landing(landing)
        return 0 if landing is None or landing["landed"] else declared_exit_code(verb, "landing_failed")

    if args.command == "queue" and getattr(args, "queue_command", None) == "correct":
        # AIPOS-F207 件②: Owner 纠正已结案卡(唯一实现 board_adapter.correct_closed_task; 写出文件经 queue_writers 落账)
        from tools.aipos_cli.board_adapter import correct_closed_task, render_correct_text
        from tools.aipos_cli.owner_inbox import InboxUsageError

        try:
            result = correct_closed_task(args.task_id, relayed_by=args.relayed_by, owner_words=args.owner_words, reason=args.reason,
                                         followup_title=args.followup_title, owner_ref=args.owner_ref, dry_run=not args.confirm,
                                         repo_root=repo_root, connection_json=_queue_gate_connection_json(args, repo_root),
                                         decision_id=getattr(args, "decision_id", None))
        except InboxUsageError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        except (FileNotFoundError, OSError, ValueError, LookupError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        landing = None
        if args.confirm and result.get("written"):
            landing = _land_queue_entry(repo_root, result, actor=str(result.get("relayed_by") or args.relayed_by),
                                        subcommand="correct", task_id=result["task_id"], written=result["written"])
        if args.json:
            print(render_json(result))
        else:
            for line in render_correct_text(result):
                print(line)
        if not result.get("ok"):
            if landing is not None and not args.json:
                _emit_landing(landing)
            return 1
        if landing is not None and not args.json:
            return _emit_landing(landing)
        return 0 if landing is None or landing["landed"] else int(landing["exit_code"])

    if args.command == "queue" and getattr(args, "queue_command", None) == "return-repair":
        return _queue_return_repair(args, repo_root)

    if args.command == "queue" and getattr(args, "queue_command", None) == "repair":
        # AIPOS-F65C 件①: repair bad frontmatter
        from tools.aipos_cli.queue_mutation import repair_bad_frontmatter
        
        try:
            result = repair_bad_frontmatter(
                repo_root=repo_root,
                task_id=args.task_id,
                actor=args.actor,
                dry_run=args.dry_run,
                confirm=bool(getattr(args, "confirm", False)),
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
        # AIPOS-F155 件④(gap #134): 两阶段(verbs.schema two_phase_protocol.queue_writers)——缺省预演(本机算将追加的返工节 + 签发者预检,
        # 零写入不调门); --confirm 才经门动词写入。原「不带 --confirm 直接报错」退役。
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
            if not isinstance(acceptance_criteria, list) or not all(isinstance(c, str) for c in acceptance_criteria):
                raise ValueError("acceptance_criteria must be a JSON array of strings")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Error: Invalid --acceptance-criteria JSON: {exc}", file=sys.stderr)
            return 1
        
        from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb, signer_role_for_actor
        
        # 凭据文件解析走唯一口(queue claim / adopt 同用): --connection-json → 治理根 .lybra/connection.json → LYBRA_CONNECTION_JSON
        conn_json_path = _queue_gate_connection_json(args, repo_root)
        if not conn_json_path:
            print(f"Error: --connection-json required (default {workspace_connection_path(Path(repo_root))} not found, LYBRA_CONNECTION_JSON unset)", file=sys.stderr)
            return 1
        
        # AIPOS-F154 件①: 返工的签发者 = --actor(顾问类实例)。身份确定只走唯一口 signer_role_for_actor(F134 按实例选凭据,
        # 未接入实例即拒、禁取首个顾问 token; 角色类授权在门侧 F132 authorize_instance_class), agent_instance=该实例交门
        try:
            role = signer_role_for_actor(connection_json_path=conn_json_path, actor=args.actor, repo_root=repo_root)
        except ValueError as exc:
            print(f"Error resolving advisor credential for --actor {args.actor}: {exc}", file=sys.stderr)
            return 1
        
        if not args.confirm:
            return _queue_rework_preview(args, repo_root, role, focus_items, acceptance_criteria)

        # 构建动词参数(门 lybra_queue_rework_dry_run inputSchema: acceptance_criteria = string, card.schema rework_rounds 同型;
        # CLI 收 JSON 数组, 逐条一行交门)
        args_dict = {
            "task_id": args.task_id,
            "actor": args.actor,
            "agent_instance": args.actor,
            "verdict_ref": args.verdict_ref,
            "focus_items": focus_items,
            "acceptance_criteria": "\n".join(acceptance_criteria),
        }
        
        # 调用两阶段薄壳(按 --actor 实例取凭据)
        exit_code, response = execute_two_phase_verb(
            verb_base="lybra_queue_rework",
            args_dict=args_dict,
            connection_json_path=conn_json_path,
            role=role,
            json_output=args.json,
            signer_instance=args.actor,
        )
        
        if not args.json:  # --json 时工厂已输出门应答, 不重复打印
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
        if getattr(args, "loop_action", None) == "note":
            # AIPOS-F168 件③: 薄壳 — 全部逻辑在 loop_run_record.annotate_owner_ask
            from tools.aipos_cli.loop_run_record import loop_note_cli
            return loop_note_cli(args)
        if getattr(args, "loop_action", None) == "stop":
            # AIPOS-F231 件②: 协作式停线薄壳 — 全部逻辑在 loop_run_record.stop_run(声明 verbs.schema lybra_loop_stop / lybra_loop.stop)
            from tools.aipos_cli.loop_run_record import stop_cli
            return stop_cli(args)
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
            if args.task_id and getattr(args, "view_actor", None):
                from tools.aipos_cli.machine_zone import lane_view_declaration

                print("Error: --actor 只用于项目扫描(无 --task-id); 单卡推导不按归属过滤", file=sys.stderr)
                return int(lane_view_declaration()["invalid_lane_exit_code"])
            if args.task_id:
                # 单卡模式
                # AIPOS-F231 件①: Owner 暂停的卡 = 「已暂停」(推导硬停形, 给恢复命令; 唯一判定 loop_driver.card_paused_stop → owner_inbox.card_pause_state)
                from tools.aipos_cli.loop_driver import card_paused_stop

                paused = card_paused_stop(Path(ws_root), args.task_id)
                result = paused if paused is not None else derive_next_step(args.task_id, ws_root)
                if run_mode and paused is not None:
                    from tools.aipos_cli.queue_mutation import pause_declaration

                    print(pause_declaration()["texts"]["next_run_refused"].format(line=paused["pause"]["line"]), file=sys.stderr)
                    return 1

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
                    # AIPOS-F166 件③: 本卡合并后回归未完成 = 一并报出(唯一判定 post_merge_regression.unfinished_regressions)
                    from tools.aipos_cli.post_merge_regression import unfinished_regressions

                    unfinished = unfinished_regressions(Path(ws_root), task_id=args.task_id)
                    if unfinished:
                        result["post_merge_regression_unfinished"] = unfinished
                    # AIPOS-F168 件②: 本卡最近一条 Owner 收件箱裁决(唯一读取 owner_inbox.next_ruling_lines)
                    from tools.aipos_cli.owner_inbox import next_ruling_lines

                    ruling_lines = next_ruling_lines(Path(ws_root), args.task_id)
                    if ruling_lines:
                        result["owner_ruling"] = ruling_lines
                    print(format_output(result, json_mode=json_mode))
                    if ruling_lines and not json_mode:
                        print("\n".join(ruling_lines))
                    if not json_mode:  # AIPOS-F167 件③: 末尾「Lybra 未读更新」(四出口同一渲染; 无未读 = 不显示)
                        from tools.aipos_cli.advisor_updates_cli import advisor_banner_lines

                        for line in advisor_banner_lines(Path(ws_root)):
                            print(line)
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
                view = scan_project_view(ws_root, lane=lane, include_frozen=bool(getattr(args, "include_frozen", False)),
                                         viewer=_owner_viewer_for_args(Path(ws_root), args, lane))  # AIPOS-F156 件②
                print(format_scan_output(view["rows"], json_mode=json_mode, lane=lane, view=view))
                if not json_mode:  # AIPOS-F166 件③: 全项目合并后回归未完成(JSON 形 = 行列表, 不改形)
                    from tools.aipos_cli.post_merge_regression import unfinished_regressions

                    for row in unfinished_regressions(Path(ws_root)):
                        print(row["line"])
                    # AIPOS-F167 件③: 末尾「Lybra 未读更新」(按 --actor 视角实例, 缺省治理根 .lybra/role 实例; 无未读 = 不显示)
                    from tools.aipos_cli.advisor_updates_cli import advisor_banner_lines
                    from tools.aipos_cli.machine_zone import owner_view_declaration

                    for line in advisor_banner_lines(Path(ws_root), getattr(args, str(owner_view_declaration()["dest"]), None)):
                        print(line)
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
                # AIPOS-F209: 信息级提示(顾问复核结案结论 BLOCK 等)另列, 不计断层、不影响退出码
                for note in result.get("notes") or []:
                    print(f"  · [{note['severity']}] {note['task_id']}: {note['message']}")
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
                                           include_frozen=bool(getattr(args, "include_frozen", False)),
                                           viewer=_owner_viewer_for_args(Path(repo_root), args, lane))  # AIPOS-F156 件②
        view = owner_report["view"]
        # AIPOS-F162 件②: 已结案卡补充审计链路(只读告知, 不升 Owner; 唯一实现 audit_derivation.supplementary_audit_overview, brief 同读)
        from tools.aipos_cli.audit_derivation import supplementary_audit_overview

        supplementary_unreadable: list[str] = []
        supplementary = supplementary_audit_overview(Path(repo_root), supplementary_unreadable)
        if args.json:
            payload = _json_report(owner_report, records=records)
            payload["supplementary_audits"] = supplementary
            payload["supplementary_unreadable"] = supplementary_unreadable
            lanes = {name: [t.get("task_id") for t in rows]
                     for name, rows in group_rows_by_lane(owner_report["tasks"], repo_root).items()}
            payload.update({"lane_filter": lane, "lanes": lanes,
                            "task_lanes": {str(t.get("path")): {"lane": t.get("lane"), "lane_error": t.get("lane_error")}
                                           for t in owner_report["tasks"]},
                            "frozen_hidden": view["frozen_hidden"], "unresolved_lane": view["unresolved_lane"],
                            "frozen_error": view["frozen_error"],  # AIPOS-F141: 可见卡口径(machine_zone.visible_cards)
                            "owner_view": view["owner_view"], "owner_other": view["owner_other"]})  # AIPOS-F156 件②
            print(render_json(payload))
        else:
            print(render_needs_owner_text(owner_report, groups=None if lane else group_rows_by_lane(owner_report["tasks"], repo_root),
                                          lane=lane))
            if supplementary:
                print("补充审计链路(已结案卡, 只读; 补审由顾问发起、loop 推进, 不需 Owner):")
                for row in supplementary:
                    print(f"  - {row['line']}")
            for line in supplementary_unreadable:  # AIPOS-F100 口径: 读不出不省略
                print(f"补充审计链路读不出: {line}")
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
            supplementary = bool(getattr(args, "supplementary", False))
            if not supplementary:
                missing_flags = [flag for flag, value in (("--agent-instance", args.agent_instance), ("--owner-policy-ref", args.owner_policy_ref),
                                                          ("--audit-task-id", args.audit_task_id),
                                                          ("--audit-agent-instance", args.audit_agent_instance)) if not value]
                if missing_flags or args.confirm or args.runtime_evidence_commands:
                    extra = ("--confirm / --runtime-evidence-command 只用于 --supplementary; " if (args.confirm or args.runtime_evidence_commands) else "")
                    print(f"Error: {extra}常规 audit dispatch 缺必填参数: {', '.join(missing_flags) or '无'}", file=sys.stderr)
                    return 2
            elif args.confirm and args.dry_run:
                print("Error: --dry-run 与 --confirm 互斥", file=sys.stderr)
                return 2
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
                    # AIPOS-F162 件②: 补充审计缺省预演, --confirm 才写(两阶段); 常规派审行为不变
                    dry_run=(not args.confirm) if supplementary else args.dry_run,
                    repo_root=repo_root,
                    supplementary=supplementary,
                    runtime_evidence_commands=args.runtime_evidence_commands,
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
        # AIPOS-F221 件②⑤: 非代码快车道卡(只写治理根、不声明独立审计)交回后由顾问复核, 不走审计裁决——在取凭据前判, 拒因点名
        # 复核结案 / 复核打回两条正式出口(唯一判定 next_resolver.fast_lane_audit_refusal, 门侧裁决同判), 不再表现为凭据错
        from tools.aipos_cli.frontmatter import FrontmatterReadError
        from tools.aipos_cli.next_resolver import fast_lane_audit_refusal
        from tools.aipos_cli.task_loader import AmbiguousTaskCard

        try:
            refusal = fast_lane_audit_refusal(Path(repo_root), str(args.reviewed_task_id))
        except (FrontmatterReadError, AmbiguousTaskCard) as exc:
            print(f"Error: 被审卡 {args.reviewed_task_id} 读不出: {exc}", file=sys.stderr)
            return 1
        if refusal:
            print(f"Error: {refusal}", file=sys.stderr)
            return 1

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
        # AIPOS-F221 件①: 治理根 = 本命令已经 F144 唯一薄壳解析出的 repo_root(全局 --workspace-root > 环境变量 > 所在治理根 > 工位声明);
        # 原自发现分支把它重置为 None 再交 resolve_audit_context 走老解析梯, 在 chris 根下撞全局 ~/.lybra/config.json 解析成活动项目
        # (去 lybra 根找凭据)。两分支同用这一个根, 不再各自解析。
        workspace_root: Path = Path(repo_root).expanduser().resolve()
        root_hit = getattr(args, "_governance_root_hit", None) or {}
        root_line = f"治理根 {workspace_root}(来源: {root_hit.get('label') or '--workspace-root / 治理根解析'})"
        
        if auto_discover:
            # 自发现模式
            try:
                # AIPOS-F157 件①(gap #132): 给了 --agent-instance / --actor = 按该实例取凭据(唯一口 F154 load_role_token, 无即拒);
                # 都未给 = 按角色取, 同角色多实例即拒(禁取首个)。显式给出的身份参数优先于自发现结果(只补缺的)。
                signer_instance = (str(getattr(args, 'agent_instance', None) or getattr(args, 'actor', None) or "").strip() or None)
                context = resolve_audit_context(
                    workspace_root=workspace_root,
                    role=getattr(args, 'token_role', 'auditor'),
                    gate_url=getattr(args, 'gate_url', None),
                    agent_instance=signer_instance,
                )
                for key in ("actor", "agent_instance", "owner_policy_ref"):
                    if getattr(args, key, None):
                        context[key] = getattr(args, key)
                
                print(f"[自发现] gate_url: {context['gate_url']}", file=sys.stderr)
                print(f"[自发现] role: {context['role']}", file=sys.stderr)
                print(f"[自发现] agent_instance: {context['agent_instance']}", file=sys.stderr)
                print(f"[自发现] actor: {context['actor']}", file=sys.stderr)
                
            except Exception as exc:
                # AIPOS-F221 件①: 拒因点名所用治理根与 connection.json 路径(凭据 / 角色缺失时能一眼看出找错了项目)
                print(f"Error: Auto-discovery failed({root_line}; connection.json {workspace_connection_path(workspace_root)}): {exc}",
                      file=sys.stderr)
                print("Hint: Ensure .lybra/connection.json exists or provide explicit parameters.", file=sys.stderr)
                return 1
        else:
            # 显式参数模式（向后兼容）
            # AIPOS-F154 件②: 取凭据走薄壳唯一口 load_role_token(loop 驱动方实例按实例取; 同角色 ≥2 实例未指明 = 拒, 禁取首个)
            # AIPOS-F157 件①: 与自发现同一取法 resolve_audit_credential——按 --agent-instance 实例取(无即拒), 未给 --token-role 时按该实例定角色
            from tools.aipos_cli.audit_helpers import resolve_audit_credential
            
            connection_json_path = args.connection_json
            if not connection_json_path:
                workspace_candidate = Path(repo_root).parent if repo_root else Path.cwd()
                connection_json_path = workspace_connection_path(workspace_candidate)
                if not connection_json_path.exists():
                    connection_json_path = workspace_connection_path(Path(repo_root or Path.cwd()))
            
            try:
                token_role_used, token, _entry = resolve_audit_credential(
                    connection_json_path=str(connection_json_path),
                    role=args.token_role,
                    agent_instance=args.agent_instance,
                )
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print(f"Error reading token({root_line}; connection.json {connection_json_path}): {exc}", file=sys.stderr)  # AIPOS-F221 件①
                return 1
            
            # AIPOS-F73前置②: gate_url 默认值解析 (修复 args.gate_url=None 时 AttributeError)
            # AIPOS-F106 件①: 门基址唯一推导口(委托 ConnectionResolver.resolve_gate_url; GateClient 自拼 MCP 路径)
            from tools.aipos_cli.confirm_client import resolve_gate_base_url

            resolved_gate_url = resolve_gate_base_url(workspace_root=workspace_root, explicit_url=args.gate_url)
            
            context = {
                "gate_url": resolved_gate_url,
                "token": token,
                "role": token_role_used,
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
        
        # AIPOS-F213 件④(F208 P1-9): 不给 --audit-task-id = 审计卡查找唯一实现自发现最新一轮在途审计卡(F202 重新派审后 R / R2 并存
        # 命中 R2; 原 audit_helpers 同名函数命中多张即 None, 自发现必失败)
        audit_task_id = getattr(args, 'audit_task_id', None)
        if not audit_task_id:
            from tools.aipos_cli.audit_derivation import find_open_audit_task

            audit_task_id = find_open_audit_task(args.reviewed_task_id, workspace_root or repo_root)
        dry_run_args = build_audit_verdict_dry_run_args(
            reviewed_task_id=args.reviewed_task_id,
            verdict=args.verdict,
            context=context,
            audit_task_id=audit_task_id,
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
        
        # AIPOS-F44D-A: 角色解析不写死(task_progress scope: executor/auditor 都有)
        # AIPOS-F154 件②: 身份确定走唯一口 progress_reporter_identity——--actor 持本角色凭据即按实例取, 否则按角色取
        # (同角色 ≥2 实例未指明 = 拒, 禁取首个冒名)
        from tools.aipos_cli.two_phase_shell_factory import progress_reporter_identity
        try:
            role, signer = progress_reporter_identity(connection_json_path=conn_json_path, actor=args.actor, repo_root=repo_root)
        except ValueError as exc:
            print(f"Error resolving role: {exc}", file=sys.stderr)
            return 1
        
        exit_code, _ = execute_single_phase_via_gate(
            verb_name="lybra_task_progress",
            args_dict=verb_args,
            connection_json_path=conn_json_path,
            role=role,
            json_output=getattr(args, "json", False),
            signer_instance=signer,
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
                outcome=args.outcome,
                focus_items=args.focus_items,  # AIPOS-F221 件②: 仅 --outcome rework
                acceptance_criteria=args.acceptance_criteria,
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

