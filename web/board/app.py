from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import secrets
import socket
import sys
import threading
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# 脚本直跑自举: 仅为让 `tools.*` 可导入而把产品仓根放进 sys.path(导入前无单源可调)。
_IMPORT_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_IMPORT_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_BOOTSTRAP_ROOT))

from tools.schema_loader import code_repo_schema_root  # noqa: E402
from tools.aipos_cli.task_loader import queue_root_for, queue_state_ref  # noqa: E402

# AIPOS-F91(L5): 产品仓根读唯一实现 schema_loader.code_repo_schema_root, 不再自算
REPO_ROOT = code_repo_schema_root()

from tools.aipos_cli.adapter_response import blocked_response
from tools.aipos_cli.verify_bench import get_verify_bench
from tools.aipos_cli.board_adapter import (
    get_advisor_pending_items,
    get_agents,
    get_context_pack_preview,
    get_drafts,
    get_external_intake_review,
    get_health,
    get_governance,
    get_needs_owner,
    get_owner_decision_records,
    get_owner_inbox,
    get_owner_truth_view,
    get_orchestration_summary_preview,
    get_orchestration_index,
    get_orchestration_timeline_preview,
    get_planner_loop_mvp_preview,
    get_preview,
    get_queue,
    get_records,
    get_roadmap_view,
    get_task,
    get_validate,
    publish_draft,
)
from tools.aipos_cli.draft_validator import validate_draft_file
from tools.aipos_cli.draft_writer import publish_draft as backend_publish_draft
from tools.aipos_cli.state_recovery import build_state_recovery_preview
from tools.aipos_cli.workspace_config import (
    DEFAULT_BOARD_HOST,
    DEFAULT_BOARD_PORT,
    has_workspace_queue,
)
from web.board.md_source import get_markdown_source
from web.board.auth_otc import (
    DEVICE_CODE_TTL_SECONDS,
    OTC_TTL_SECONDS,
    DeviceCodeStore,
    OTCStore,
    PreviewTokenStore,
    append_auth_log,
    resolve_auth_log_path,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _board_workspaces(repo_root: Path | None, home_root: str | Path | None = None) -> list[dict[str, Any]]:
    """AIPOS-F171 件③(gap #156): 看板项目列表 = home 根已注册项目(owner_inbox.home_projects, 与 Owner 收件箱同一枚举; 序 = 项目名序,
    ?workspace=<序号> 即此序), 暂停项目(project.json status=paused)标 paused。原 .lybra/board_config.json / --board-config 静态清单
    退役(文件保留不删, 不再读取)。home 根无已注册项目 = 单工作区(服务进程的工作区根, 与旧缺省一致)。只读。"""
    from tools.aipos_cli.owner_inbox import home_projects

    workspaces = [
        {"label": p["name"], "project": p["name"], "root": str(p["root"]), "project_status": p["status"],
         "paused": p["status"] == "paused", "status_error": p["status_error"]}
        for p in home_projects(home_root)["projects"]
    ]
    if workspaces:
        return workspaces
    fallback = repo_root or REPO_ROOT
    return [{"label": "Default Workspace", "project": None, "root": str(fallback), "project_status": None, "paused": False,
             "status_error": None}]


def get_overview(repo_root: Path | None = None, *, home_root: str | Path | None = None) -> dict[str, Any]:
    """Multi-workspace overview aggregation (AIPOS-251; AIPOS-F171 件③: 项目列表 = home 根推导 _board_workspaces, 暂停项目标注).
    AIPOS-F229 件③: one request-level read scope (task_loader.read_request_scope) for the whole aggregation: per project the
    queue (load_all_tasks) and the records (load_records) are loaded once and shared by get_queue / get_needs_owner / get_records /
    owner truth view / advisor pending items (was 3 queue loads and 5 records loads per project).
    AIPOS-F245 item 1: board server = result cached on the truth fingerprint (task_loader.truth_cached; any truth change = recomputed
    on the next request)."""
    from tools.aipos_cli.task_loader import read_request_scope, truth_cached

    def compute() -> dict[str, Any]:
        with read_request_scope():
            return _get_overview(_board_workspaces(repo_root, home_root))

    return truth_cached(("web.board.app.get_overview", str(repo_root), str(home_root)), compute)


def _get_overview(workspaces_config: list[dict[str, Any]]) -> dict[str, Any]:

    results = []
    for ws_config in workspaces_config:
        label = ws_config.get("label", "Unnamed")
        root_str = ws_config.get("root", "")
        
        try:
            root = Path(root_str).expanduser().resolve()
            
            # Validate workspace
            if not has_workspace_queue(root):
                error_entry = {
                    **_workspace_identity(ws_config),
                    "label": label,
                    "root": str(root),
                    "status": "error",
                    "error": "Workspace root does not contain 5_tasks/queue"
                }
                results.append(error_entry)
                continue
            
            # Aggregate data from this workspace
            queue_data = get_queue(repo_root=root)
            needs_owner_data = get_needs_owner(repo_root=root)
            records_data = get_records(repo_root=root)
            
            # Extract queue counts
            queue_counts = {}
            if queue_data.get("ok") and "data" in queue_data:
                summary = queue_data["data"].get("summary", {})
                from tools.aipos_cli.task_loader import QUEUE_STATES  # AIPOS-F104: queue dirs = enums queue_state projection

                for state in QUEUE_STATES:
                    queue_counts[state] = summary.get(state, 0)
            
            # Extract needs-owner items (top 5)
            needs_owner_items = []
            if needs_owner_data.get("ok") and "data" in needs_owner_data:
                items = needs_owner_data["data"].get("needs_owner_items", [])
                for item in items[:5]:
                    needs_owner_items.append({
                        "task_id": item.get("task_id"),
                        "reason": item.get("reason"),
                        "created": item.get("created")
                    })
            
            # Extract recent activity
            recent_activity = None
            if records_data.get("ok") and "data" in records_data:
                summary = records_data["data"].get("summary", {})
                if summary.get("most_recent_timestamp"):
                    recent_activity = {
                        "timestamp": summary.get("most_recent_timestamp"),
                        "type": summary.get("most_recent_type", "activity")
                    }

            # AIPOS-260 FIX-1: record-derived true-stage counts per workspace
            # (Owner overview must not show the raw claimed/ count as "进行中").
            # Read-only aggregation; falls back to empty on any error.
            stage_counts: dict[str, int] = {}
            truth_total = 0
            try:
                truth_data = get_owner_truth_view(repo_root=root)
                if truth_data.get("ok"):
                    summary = truth_data.get("summary") or {}
                    stage_counts = dict(summary.get("stage_counts") or {})
                    top_level_counts = dict(summary.get("top_level_counts") or {})
                    truth_total = int(summary.get("total_tasks") or 0)
            except Exception:
                stage_counts = {}
                top_level_counts = {}
                truth_total = 0

            # AIPOS-297: advisor pending items (gate 零推送纯推导)
            advisor_pending = {}
            try:
                pending_data = get_advisor_pending_items(repo_root=root)
                if pending_data.get("ok"):
                    data = pending_data.get("data") or {}
                    advisor_pending = {
                        "pending_approvals": data.get("pending_approvals", []),
                        "pending_rejects": data.get("pending_rejects", []),
                        "total_pending": data.get("total_pending", 0),
                    }
            except Exception:
                advisor_pending = {
                    "pending_approvals": [],
                    "pending_rejects": [],
                    "total_pending": 0,
                }

            ok_entry = {
                **_workspace_identity(ws_config),
                "label": label,
                "root": str(root),
                "status": "ok",
                "queue_counts": queue_counts,
                "needs_owner": needs_owner_items,
                "recent_activity": recent_activity,
                "stage_counts": stage_counts,
                "top_level_counts": top_level_counts,
                "truth_total": truth_total,
                "advisor_pending": advisor_pending,
            }
            results.append(ok_entry)
            
        except Exception as e:
            exception_entry = {
                **_workspace_identity(ws_config),
                "label": label,
                "root": root_str,
                "status": "error",
                "error": str(e)
            }
            results.append(exception_entry)
    
    return {
        "ok": True,
        "workspaces": results
    }


def _workspace_identity(ws_config: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F171 件③: 总览每项带项目名与推进状态(暂停标注), 取自 _board_workspaces。"""
    return {"project": ws_config.get("project"), "project_status": ws_config.get("project_status"),
            "paused": bool(ws_config.get("paused")), "status_error": ws_config.get("status_error")}


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    return "application/octet-stream"


def _resolve_workspace_root(params: dict[str, list[str]], fallback_root: Path | None, home_root: str | Path | None = None) -> Path | None:
    """?workspace=<序号> → 该项目治理根(AIPOS-252 多工作区; AIPOS-F171 件③: 序号 = _board_workspaces 的 home 根推导序, 原 board_config
    静态清单退役)。无参 / 越界 / 非数字 = fallback_root(单工作区)。"""
    workspace_param = params.get('workspace', [])
    if not workspace_param:
        return fallback_root
    try:
        workspace_index = int(workspace_param[0])
    except ValueError:
        return fallback_root
    workspaces = _board_workspaces(fallback_root, home_root)
    if workspace_index < 0 or workspace_index >= len(workspaces):
        return fallback_root
    return Path(str(workspaces[workspace_index]["root"])).expanduser().resolve()


def _api_routes(repo_root: Path | None, board_config_path: Path | None = None, *,
                home_root: str | Path | None = None) -> dict[str, Callable[[dict[str, list[str]]], dict[str, Any]]]:
    """看板只读 GET 路由表(AIPOS-F171 件③: 写接口全部下线, 无 POST 业务路由表)。board_config_path 已退役(AIPOS-F171: 不再读取, 参数
    只为旧调用方兼容); 项目列表 = home 根推导(home_root 缺省 = resolve_home_root 同一优先级梯)。"""
    return {
        "/api/health": lambda params: get_health(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/overview": lambda _params: get_overview(repo_root=repo_root, home_root=home_root),
        "/api/runtime-status": partial(_get_runtime_status_route, repo_root=repo_root, home_root=home_root),
        "/api/generate/advisor-prompt": partial(_generate_advisor_prompt_route, repo_root=repo_root, home_root=home_root),
        "/api/lifecycle": partial(_get_lifecycle_route, repo_root=repo_root),
        "/api/governance": lambda params: get_governance(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/queue": lambda params: get_queue(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/needs-owner": lambda params: get_needs_owner(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/validate": lambda params: get_validate(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/agents": lambda params: get_agents(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/drafts": lambda params: get_drafts(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/records": lambda params: get_records(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/owner-truth": lambda params: get_owner_truth_view(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        # AIPOS-F171 件③: 旧项目地图(读手写 governance/project-map.md, gap #156)下线(文件不删); 项目页里程碑改读路线图声明(只读简版)
        "/api/roadmap": lambda params: get_roadmap_view(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/verify-bench": lambda params: get_verify_bench(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/external-intake/review": lambda params: get_external_intake_review(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/owner-decision-records": lambda params: get_owner_decision_records(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        # AIPOS-F168 件①: Owner 待决收件箱(跨 home 根全部已注册项目, CLI `lybra owner inbox` 同一数据; home 根 = 服务进程的 resolve_home_root)
        "/api/owner/inbox": lambda _params: get_owner_inbox(home_root),  # AIPOS-F172: server home root (None = same resolver ladder, unchanged)
        "/api/planner-drafts/review": partial(_get_planner_drafts_review_route, repo_root=repo_root),
        "/api/owner-decisions/review": partial(_get_owner_decisions_review_route, repo_root=repo_root),
        "/api/orchestration/index": lambda params: get_orchestration_index(repo_root=_resolve_workspace_root(params, repo_root, home_root)),
        "/api/orchestration-summary": partial(_get_orchestration_summary_route, repo_root=repo_root),
        "/api/orchestration/summary": partial(_get_orchestration_summary_route, repo_root=repo_root),
        "/api/orchestration-timeline": partial(_get_orchestration_timeline_route, repo_root=repo_root),
        "/api/orchestration/timeline": partial(_get_orchestration_timeline_route, repo_root=repo_root),
        "/api/planner-loop/mvp": partial(_get_planner_loop_mvp_route, repo_root=repo_root),
        "/api/context-pack/preview": partial(_get_context_pack_preview_route, repo_root=repo_root),
        "/api/task": partial(_get_task_route, repo_root=repo_root),
        "/api/preview": partial(_get_preview_route, repo_root=repo_root),
        "/api/markdown-source": partial(_get_markdown_source_route, repo_root=repo_root, home_root=home_root),
    }


def _first_param(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name) or []
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def _selector_error(operation: str, message: str) -> dict[str, Any]:
    return blocked_response(
        operation=operation,
        dry_run=False,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local read-only web UI route. No files are written.",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _secret_fingerprint(value: str) -> str | None:
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _runtime_config_defaults(repo_root: Path) -> dict[str, Any]:
    """AIPOS-F117 件①(gap #52): 委托 .lybra/config.json 运行时字段唯一读取口 workspace_config.workspace_runtime_config
    (strict=False: 只读状态面把配置错误放进 config_error 展示, 不抛); 原逐字段第二份读取 + 缺省与宽 except 删除。
    config_path 在状态面按工作区相对路径展示。"""
    from tools.aipos_cli.workspace_config import workspace_runtime_config

    cfg = workspace_runtime_config(repo_root, strict=False)
    if cfg["config_path"]:
        cfg["config_path"] = str(Path(cfg["config_path"]).relative_to(repo_root))
    return cfg


def _load_connection_endpoints(repo_root: Path) -> dict[str, Any]:
    """Load actual endpoint URLs from workspace connection.json (AIPOS-272 FIX-8).
    
    Returns dict with mcp_rpc_url, mcp_sse_url, board_url (or None if not found).
    Graceful degradation: missing file or parse error => all None + notice.
    """
    from tools.aipos_cli.service_mode import connection_path as _connection_path  # AIPOS-F106 件④: 定位唯一实现

    connection_path = _connection_path(repo_root)
    if not connection_path.exists():
        return {
            "mcp_rpc_url": None,
            "mcp_sse_url": None,
            "board_url": None,
            "connection_notice": f"connection.json not found at {connection_path.relative_to(repo_root)}",
        }
    
    try:
        data = json.loads(connection_path.read_text(encoding="utf-8"))
        mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
        board = data.get("board") if isinstance(data.get("board"), dict) else {}
        return {
            "mcp_rpc_url": mcp.get("rpc_url"),
            "mcp_sse_url": mcp.get("sse_url"),
            "board_url": board.get("url"),
            "connection_notice": None,
        }
    except Exception as exc:
        return {
            "mcp_rpc_url": None,
            "mcp_sse_url": None,
            "board_url": None,
            "connection_notice": f"Failed to parse connection.json: {exc}",
        }


def _capability_status(raw: str) -> dict[str, Any]:
    operations: list[str] = []
    diagnostics: list[str] = []
    expires_at: str | None = None
    expires_status = "missing"
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
            diagnostics.append("Capability token is not valid JSON")
        if isinstance(payload, dict):
            raw_operations = payload.get("operations")
            if isinstance(raw_operations, list):
                operations = [str(item) for item in raw_operations]
            else:
                diagnostics.append("Capability token operations must be a list")
            expires_text = str(payload.get("expires_at") or "").strip()
            if expires_text:
                expires_at = expires_text
                try:
                    parsed = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    expires_status = "valid" if parsed > datetime.now(timezone.utc) else "expired"
                except ValueError:
                    expires_status = "invalid"
        elif raw:
            diagnostics.append("Capability token must be a JSON object")
    visibility = {
        "queue_claim": "visible" if "queue_claim" in operations else "hidden",
        "queue_return": "visible" if "queue_return" in operations else "hidden",
        "audit_dispatch": "visible" if "audit_dispatch" in operations else "hidden",
        "audit_verdict": "visible" if "audit_verdict" in operations else "hidden",
    }
    return {
        "operations": operations,
        "expires_at": expires_at,
        "expires_status": expires_status,
        "tool_visibility": visibility,
        "diagnostics": diagnostics,
    }


def _derive_task_lifecycle(task: dict[str, Any]) -> str:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    status = str(task.get("status") or metadata.get("status") or "").strip()
    if status == "blocked":
        return "blocked_or_needs_owner"
    if str(metadata.get("dependency_audit_status") or "").lower() in {"pass", "audit_pass"}:
        return "verdict_pass"
    records = task.get("records") if isinstance(task.get("records"), dict) else {}
    if int(records.get("audit_verdict_records") or 0) > 0:
        return "verdict_recorded"
    if int(records.get("audit_dispatch_records") or 0) > 0 or metadata.get("audit_dispatch_record_ref"):
        return "audit_dispatched"
    if (
        int(records.get("return_records") or 0) > 0
        or metadata.get("return_record_ref")
        or (metadata.get("executor_status") == "completed" and metadata.get("audit_readiness") == "ready")
    ):
        return "returned"
    if status == "claimed" or metadata.get("claim_id"):
        return "claimed"
    if status == "pending":
        return "pending"
    verdict = str(task.get("verdict") or "").strip()
    if verdict in {"BLOCK", "NEEDS_OWNER"}:
        return "blocked_or_needs_owner"
    return status or "unknown"


def _loop_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for task in tasks:
        stage = _derive_task_lifecycle(task)
        counts[stage] = counts.get(stage, 0) + 1
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        rows.append(
            {
                "task_id": task.get("task_id") or metadata.get("task_id"),
                "title": task.get("title") or metadata.get("title"),
                "path": task.get("path"),
                "status": task.get("status") or metadata.get("status"),
                "verdict": task.get("verdict"),
                "lifecycle_stage": stage,
                "dependency_audit_status": metadata.get("dependency_audit_status"),
            }
        )
    return {"counts": counts, "tasks": rows[:20], "total": len(rows)}


def _record_ref_summary(recovery: dict[str, Any]) -> dict[str, Any]:
    chain = recovery.get("provenance_chain") if isinstance(recovery.get("provenance_chain"), dict) else {}
    claim = chain.get("claim") if isinstance(chain.get("claim"), dict) else {}
    session = chain.get("session") if isinstance(chain.get("session"), dict) else {}
    returned = chain.get("return") if isinstance(chain.get("return"), dict) else {}
    audit = chain.get("audit") if isinstance(chain.get("audit"), dict) else {}
    return {
        "claim_id": recovery.get("claim_id"),
        "claim_record_ref": claim.get("claim_record_ref"),
        "active_session_id": recovery.get("active_session_id"),
        "session_record_ref": session.get("session_record_ref"),
        "return_record_ref": returned.get("return_record_ref"),
        "return_record_path": returned.get("return_record_path"),
        "audit_dispatch_record_ref": audit.get("audit_dispatch_record_ref") or None,
        "related_audit_task_ref": audit.get("related_audit_task_ref"),
        "related_audit_verdict_ref": audit.get("related_audit_verdict_ref"),
    }


def _owner_gate_state(task: dict[str, Any], recovery: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    task_verdict = str(task.get("verdict") or "")
    dependency_audit_status = str(metadata.get("dependency_audit_status") or recovery.get("dependency_audit_status") or "")
    if dependency_audit_status.upper() == "PASS" and not metadata.get("finalize_ref") and not metadata.get("finalize_status"):
        return {
            "state": "audit_pass_waiting_owner_finalize",
            "label": "Audit PASS recorded; Owner finalize is still a separate gate",
            "reasons": ["Finalize writer / accepted-work unblock is intentionally deferred."],
        }
    if task_verdict == "NEEDS_OWNER" or recovery.get("needs_owner_reasons"):
        return {"state": "needs_owner", "label": "Owner decision required", "reasons": task.get("needs_owner_reasons") or recovery.get("needs_owner_reasons") or []}
    if task_verdict == "BLOCK" or recovery.get("blocking_reasons"):
        return {"state": "blocked", "label": "Blocked until durable state is repaired", "reasons": task.get("blocking_reasons") or recovery.get("blocking_reasons") or []}
    if recovery.get("provenance_completeness") in {"partial", "missing", "contradictory"}:
        return {
            "state": "provenance_gap",
            "label": "Provenance gap visible",
            "reasons": recovery.get("warnings") or recovery.get("contradictions") or [],
        }
    return {"state": "none", "label": "No Owner gate surfaced", "reasons": []}


def _lifecycle_row(task: dict[str, Any], recovery: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    linked = recovery.get("linked_records") if isinstance(recovery.get("linked_records"), dict) else {}
    audit_dispatches = linked.get("audit_dispatches") if isinstance(linked.get("audit_dispatches"), list) else []
    audit_verdicts = linked.get("audit_verdicts") if isinstance(linked.get("audit_verdicts"), list) else []
    return {
        "task_id": task.get("task_id") or metadata.get("task_id") or recovery.get("task_id"),
        "title": task.get("title") or metadata.get("title"),
        "path": task.get("path") or recovery.get("task_path"),
        "queue_state": recovery.get("queue_state") or task.get("queue_state"),
        "status": task.get("status") or metadata.get("status"),
        "validator_verdict": task.get("verdict"),
        "recovery_verdict": recovery.get("verdict"),
        "lifecycle_stage": _derive_task_lifecycle(task),
        "owner_gate": _owner_gate_state(task, recovery),
        "provenance_completeness": recovery.get("provenance_completeness"),
        "record_refs": _record_ref_summary(recovery),
        "staleness": recovery.get("staleness") or [],
        "contradictions": recovery.get("contradictions") or [],
        "warnings": recovery.get("warnings") or [],
        "audit_relation": {
            "reviewed_task_id": metadata.get("reviewed_task_id"),
            "related_audit_task_ref": metadata.get("related_audit_task_ref"),
            "related_audit_verdict_ref": metadata.get("related_audit_verdict_ref"),
            "audit_dispatches": audit_dispatches,
            "audit_verdicts": audit_verdicts,
            "distinct_auditor_visible": bool(audit_dispatches or audit_verdicts or metadata.get("related_audit_task_ref")),
        },
        "recommended_next_action": recovery.get("recommended_next_action"),
        "writes_enabled": False,
        "execute_allowed": False,
    }


def _get_lifecycle_route(_params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    operation = "get_lifecycle"
    resolved_root = (repo_root or REPO_ROOT).resolve()
    queue = get_queue(repo_root=resolved_root)
    records = get_records(repo_root=resolved_root)
    records_report = records.get("data") if isinstance(records.get("data"), dict) else {}
    tasks = queue.get("data", {}).get("tasks") if isinstance(queue.get("data"), dict) else []
    tasks = tasks if isinstance(tasks, list) else []
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for task in tasks:
        try:
            recovery = build_state_recovery_preview(resolved_root, path=str(task.get("path") or ""), records=records_report)
        except Exception as exc:
            recovery = {
                "verdict": "WARN",
                "task_id": task.get("task_id"),
                "task_path": task.get("path"),
                "provenance_completeness": "unknown",
                "warnings": [f"state recovery preview unavailable: {exc}"],
                "staleness": [],
                "contradictions": [],
                "linked_records": {},
                "provenance_chain": {},
                "writes_enabled": False,
                "execute_allowed": False,
            }
            warnings.append(f"{task.get('task_id') or task.get('path')}: state recovery preview unavailable")
        rows.append(_lifecycle_row(task, recovery))
    stage_counts: dict[str, int] = {}
    owner_gate_counts: dict[str, int] = {}
    completeness_counts: dict[str, int] = {}
    for row in rows:
        stage = str(row.get("lifecycle_stage") or "unknown")
        gate = str(row.get("owner_gate", {}).get("state") or "none")
        completeness = str(row.get("provenance_completeness") or "unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        owner_gate_counts[gate] = owner_gate_counts.get(gate, 0) + 1
        completeness_counts[completeness] = completeness_counts.get(completeness, 0) + 1
    return {
        "ok": True,
        "verdict": "WARN" if warnings else "PASS",
        "operation": operation,
        "dry_run": False,
        "actor": None,
        "actor_match": None,
        "timestamp": _utc_now(),
        "data": {
            "tasks": rows,
            "stage_counts": stage_counts,
            "owner_gate_counts": owner_gate_counts,
            "provenance_completeness_counts": completeness_counts,
            "record_summary": records.get("summary"),
            "writes_enabled": False,
            "execute_allowed": False,
            "deferred_gates": ["finalize_writer", "accepted_work_unblock", "active_lease_writer", "delegated", "standing", "trace_native_audit"],
        },
        "summary": {
            "total_tasks": len(rows),
            "stage_counts": stage_counts,
            "owner_gate_counts": owner_gate_counts,
            "provenance_completeness_counts": completeness_counts,
        },
        "planned_writes": [],
        "planned_moves": [],
        "performed_writes": [],
        "performed_moves": [],
        "warnings": warnings,
        "blocking_reasons": [],
        "needs_owner_reasons": [],
        "owner_confirmation_required": False,
        "owner_confirmation_reasons": [],
        "execute_allowed": False,
        "execute_blocking_reasons": [],
        "dry_run_id": None,
        "dry_run_token": None,
        "dry_run_snapshot_hash": None,
        "dry_run_created_at": None,
        "dry_run_expires_at": None,
        "safety_notice": "Read-only Board lifecycle surface. State is derived from tasks, records, validator, and AIPOS-173 recovery preview; no files are written.",
        "errors": [],
    }


def _get_server_location_info() -> dict[str, Any] | None:
    """
    AIPOS-286: Get server hostname and IP for advisor same-machine verification.
    Returns dict with 'hostname', 'ip', 'note' or None on failure (graceful degradation).
    """
    try:
        hostname = socket.gethostname()
        # Use dummy socket connect to get preferred outbound IP (doesn't actually connect)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return {
            "hostname": hostname,
            "ip": ip,
            "note": "AIPOS-286: Advisor agents should verify same-machine before connecting. See onboarding prompt step-0."
        }
    except Exception:
        return None


# ===== AIPOS-286 FIX-2: Server-side i18n generation channel =====
# Unified template system for generated content (advisor onboarding prompt,
# wizard guidance, MCP snippet comments, QUICKSTART blocks). All generated
# text must flow through this channel to ensure locale-aware rendering with
# zero CJK leakage in en mode.
#
# CONSTRAINT: All template keys must exist in BOTH zh and en. Missing en key
# => test failure (not silent fallback to zh). This enforces complete i18n
# coverage for generated content.
#
# USAGE: Call _generate_text(template_key, locale, **vars) to get rendered text.
# New generated content MUST use this channel (see docstring enforcement test).

_I18N_TEMPLATES = {
    "zh": {
        "advisor_prompt": """你是 {workspace_label} 工作区的顾问 (Advisor)。

工作区信息：
- 路径：{workspace_root}
- Gate URL：{gate_url}
- Charter：{charter_path}

Lybra 服务端位置（AIPOS-286）：
- 主机名：{server_hostname}
- IP 地址：{server_ip}

你的职责：
- 读取 charter 了解红线和工作方式
- 起草任务卡（在 5_tasks/drafts/）
- 向 Owner 建议发布
- 查看状态：lybra queue / lybra records / lybra owner-truth
- 盯队列与记录变化：lybra agent watch --workspace-root {workspace_root} —— 有变化即返回摘要,适合做你的唤醒泵

快速开始：

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  第 0 步：同机确认与连通性检测（AIPOS-286 强制前置）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

在连接 gate 之前，你必须先完成以下检查：

1. **确认你与 Lybra 服务端是否在同一台机器**：
   - 服务端主机名：{server_hostname}
   - 服务端 IP：{server_ip}
   - 检查方法：运行 `hostname` 和 `hostname -I` 命令，对比上述信息
   - 同机：继续第 2 步
   - 不同机：继续第 2 步（跨机场景）

2. **连通性检测**（不同机时必做，同机时也建议做）：
   
   **方式 A — HTTP 健康检查（推荐）**：
   ```bash
   curl -v {gate_url}/health
   ```
   预期：返回 200 OK + JSON 响应（包含 `"ok": true`）
   
   **方式 B — 文件真相面检测**（需要 SSH 或挂载）：
   - 尝试访问工作区路径：`ls {workspace_root}/5_tasks/queue`
   - 预期：能列出队列文件
   
   **不通过怎么办**：
   - 如果连通性检测失败（curl 超时、SSH 不通、路径无法访问）：
     **立即停止，block-and-report 给 Owner**，说明：
     * 你的位置（主机名 + IP）
     * 服务端位置（{server_hostname} / {server_ip}）
     * 检测失败的具体现象（超时、拒绝连接、权限不足等）
     * 需要 Owner 配置 SSH 连通性或网络路由后再继续
   - **绝不带病接线**：连不通 gate 时强行配置 MCP 会导致后续所有操作静默失败

3. **通过后再继续**：
   - 连通性确认 OK → 进入下方「零安装接入」配置 MCP 连接

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔌 零安装接入（任何 MCP agent 均可，无需安装 Lybra CLI）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 使用 Owner 提供的 advisor token：
   - Claude Desktop/Cline: mcpServers → {{"lybra": {{"url": "{gate_url}/mcp", "headers": {{"Authorization": "Bearer <ADVISOR_TOKEN>"}}}}}}
   - Claude Code 命令行: claude mcp add lybra --transport http {gate_url}/mcp --header "Authorization: Bearer <ADVISOR_TOKEN>"
   - Pi/Codex/其他 HTTP MCP harness: {{"url": "{gate_url}/mcp", "headers": {{"Authorization": "Bearer <ADVISOR_TOKEN>"}}}}
   注：以上为常见 harness 示意（非穷举），桌面版/命令行均可；不支持 MCP 的 agent 可直接文件系统操作。

2. 🔧 安装 Lybra CLI（标准第二步，完整功能需要）
   完整功能（含 agent watch 哨兵与 lybra loop 推进）需要安装 Lybra CLI：
   
   方式 A — 从 npm 安装（推荐）：
   npm install -g lybra
   pip install "textual>=4.0"  # TUI 依赖
   lybra --version
   
   方式 B — 从 gate 自举（agent 可自取）：
   假设 gate 机已暴露 git/pip 源，agent 可执行：
   git clone <LYBRA_REPO_URL> /tmp/lybra && cd /tmp/lybra
   npm install -g .
   pip install "textual>=4.0"
   lybra --version
   
   安装后等待队列/记录变化用文件哨兵（纯客户端只读, 无需凭据）：
     lybra agent watch --workspace-root {workspace_root} --timeout 30
   推进卡片用 lybra loop --task-id <卡ID>（执行体工位只敲 /go）。

3. 📖 阅读 charter 与示例
   - Charter: {charter_path}
   - 示例卡: {example_card_path}

4. 起草第一张任务卡，建议 Owner 发布

---

重要边界：
- 你对治理工作区有写权（起草任务卡、维护治理文档）
- 已发布的卡、queue、records 是 gate 的领地，不可手写
- 产品仓等其他仓库默认只读，除非 Owner 明确授权
- 发布由 Owner 确认（你起草，Owner 决定）
- 凭据只按名引用，绝不读取/回显 token 文件内容""",
        # AIPOS-F174: 看板只读页(总览 / 路线图与验收 / 项目接入)文案, zh / en 同键(web/board/readonly_pages.py 经 _generate_text 取)
        "rp_nav_overview": "项目总览",
        "rp_nav_roadmap": "路线图与验收",
        "rp_nav_onboarding": "项目接入",
        "rp_nav_workstation": "工位设置",
        "rp_nav_inbox": "待你决定",
        "rp_nav_envelopes": "信封",
        "rp_nav_project": "项目",
        "rp_lang_other": "English",
        "rp_readonly_note": "本页只读。需要你出手的地方都写好了「告诉顾问」的那句话和顾问要跑的命令:把那句话转告顾问, 由顾问执行。",
        "rp_tell": "告诉 {advisor}:{ask}",
        "rp_advisor_fallback": "你的顾问",
        "rp_command": "给顾问的命令(点开)",
        "rp_paused_folded": "已暂停的项目({count} 个)",
        "rp_paused_badge": "已暂停",
        "rp_error": "读不出:{error}",
        "rp_page_error": "页面读不出(拒因原文):{error}",
        "rp_ov_title": "项目总览 · {count} 个项目",
        "rp_ov_counts": "在途 {inflight} 张 · 待办 {todo} 张 · 已结案 {closed} 张",
        "rp_ov_frozen": "历史存量 {count} 张(已冻结, 不计入上面的数字)",
        "rp_ov_frozen_error": "冻结清单读不出, 上面的数字按全部卡算:{error}",
        "rp_ov_one_advisor": "归属顾问:{advisor}",
        "rp_ov_inbox": "待你决定 {count} 件",
        "rp_ov_inbox_paused": "暂停项目不进收件箱",
        "rp_ov_envelope_ok": "信封在用:{policies}",
        "rp_ov_envelope_low": "信封将尽或用尽:{policies}",
        "rp_ov_envelope_none": "无在用信封",
        "rp_ov_roadmap": "路线图:标准满足 {met}/{total} · 里程碑 {milestones} 个 · 偏差 {signals} 条(严重 {severe})",
        "rp_ov_roadmap_none": "路线图:未声明",
        "rp_ov_onboarding": "接入:已完成 {done}/{total} 步",
        "rp_ov_upgrade": "可升级({count} 项)",
        "rp_ov_upgrade_none": "项目升级:已是最新",
        "rp_ov_detail": "项目详情",
        "rp_rm_title": "路线图与验收 · {project}",
        "rp_rm_projects": "项目",
        "rp_rm_source": "来源 {path} · 今天 {today} · 标准满足 {met}/{total}",
        "rp_rm_signals": "偏差信号 {count} 条",
        "rp_rm_no_signals": "无偏差信号",
        "rp_sev_inbox": "严重",
        "rp_sev_warn": "提示",
        "rp_rm_signal_ask": "处理路线图偏差「{label}」({subject})",
        "rp_rm_advice": "建议",
        "rp_rm_milestones": "里程碑(按 lane)",
        "rp_rm_lane": "lane {lane}",
        "rp_rm_target": "目标日 {date}",
        "rp_rm_overdue": "已逾期",
        "rp_rm_status_planned": "计划中",
        "rp_rm_status_active": "进行中",
        "rp_rm_status_done": "已完成",
        "rp_rm_status_dropped": "已放弃",
        "rp_rm_criteria_title": "{id} · {title}:逐条验收标准",
        "rp_rm_verify": "验证方式:{verify}",
        "rp_rm_verify_auto": "自动(审计 PASS + 运行证据)",
        "rp_rm_verify_artifact": "看产物",
        "rp_rm_verify_owner_check": "Owner 抽查",
        "rp_rm_crit_met": "已满足",
        "rp_rm_crit_unmet": "未满足:{reason}",
        "rp_rm_state": "验证状态:{state}",
        "rp_rm_state_unverified": "未验证",
        "rp_rm_state_auto_passed": "自动验证通过",
        "rp_rm_state_owner_pending": "待 Owner 抽查",
        "rp_rm_state_owner_confirmed": "Owner 已确认",
        "rp_rm_state_mismatch": "不符",
        "rp_rm_basis": "依据:{basis}",
        "rp_rm_artifact": "产物 [{kind}] {path_glob}",
        "rp_rm_artifact_none": "落点 {dir} 里没有匹配的文件",
        "rp_rm_artifact_open": "看原图",
        "rp_rm_owner_record": "Owner 抽查:{label} · {decided_at} · 经 {relayed_by} 转达 · 原话「{owner_words}」",
        "rp_rm_history": "抽查历史 {count} 条",
        "rp_rm_fix": "修复卡:{fix}",
        "rp_rm_verify_ask": "看完标准 {criterion} 的产物后,告诉我「{criterion} 符合」或「{criterion} 不符:哪里不对」",
        "rp_rm_card": "卡 {task_id} · 队列 {queue_state}",
        "rp_rm_verdict": "审计裁决 {verdict}",
        "rp_rm_no_verdict": "尚无审计裁决",
        "rp_rm_supp": "补审裁决 {verdict}({verdict_id})",
        "rp_rm_record_link": "看裁决记录",
        "rp_rm_evidence": "运行证据(摘自 {ref})",
        "rp_rm_evidence_missing": "无运行证据摘录:{problem}",
        "rp_rm_evidence_entry": "退出码 {exit_code}",
        "rp_rm_mismatch": "状态与推导不一致:{text}",
        "rp_rm_next": "下一步建议",
        "rp_rm_unlinked": "游离卡(建路线图后出的卡里未挂里程碑的){count} 张:{cards}",
        "rp_rm_unlinked_before": "另有建路线图之前的历史卡 {count} 张没挂里程碑, 不算游离卡",
        "rp_rm_folded": "已完成 / 已放弃的里程碑({count} 个)",
        "rp_rm_none_for_sub": "这个子项目还没有里程碑",
        "rp_rm_invalid": "挂接不合 {task_id}:{problems}",
        "rp_rm_errors": "读不出的真相 {count} 处(相关标准按未满足、相关里程碑不判停滞)",
        "rp_rm_howto_title": "如何开始",
        "rp_rm_howto_ask": "为项目 {project} 建路线图:里程碑 + 每条可核对的验收标准",
        "rp_rm_unknown_project": "项目 {project} 不在 home 根已注册项目中",
        "rp_rm_unknown_milestone": "里程碑 {milestone} 不在路线图中, 已改显示缺省里程碑",
        "rp_rm_no_projects": "home 根下没有已注册项目",
        "rp_rm_no_criteria": "该里程碑还没有验收标准",
        "rp_ob_title": "项目接入清单 · {count} 个项目",
        "rp_ob_summary": "已完成 {done}/{total} 步",
        "rp_ob_state_ok": "已完成",
        "rp_ob_state_todo": "待办",
        "rp_ob_state_na": "不适用",
        "rp_ob_state_error": "读不出",
        "rp_ob_item_governance_root": "治理根已建",
        "rp_ob_item_advisor": "顾问实例已接入",
        "rp_ob_item_executor": "执行实例已接入",
        "rp_ob_item_auditor": "审计实例已接入",
        "rp_ob_item_envelope": "信封在用且未将尽",
        "rp_ob_item_charter": "工位章程新鲜",
        "rp_ob_item_pi_model": "pi 模型已声明",
        "rp_ob_item_roadmap": "路线图已声明",
        "rp_ob_item_first_closed": "首张卡已结案",
        "rp_ob_ask_governance_root": "核对项目 {project} 的 project.json 推进状态(status)",
        "rp_ob_ask_advisor": "给项目 {project} 接入顾问实例",
        "rp_ob_ask_executor": "给项目 {project} 声明并接入执行实例({instance})",
        "rp_ob_ask_auditor": "给项目 {project} 声明并接入审计实例({instance})",
        "rp_ob_ask_envelope": "给项目 {project} 准备在用信封(续签或新签, 我在信封页授权)",
        "rp_ob_ask_charter": "核对项目 {project} 的工位章程({instance})",
        "rp_ob_ask_pi_model": "给项目 {project} 声明 pi 模型",
        "rp_ob_ask_roadmap": "给项目 {project} 建路线图",
        "rp_ob_ask_first_closed": "推进项目 {project} 的第一张卡到结案",
        "rp_ob_detail_governance_root": "治理根 {root}",
        "rp_ob_detail_advisor_ok": "已接入:{instances}",
        "rp_ob_detail_advisor_none": "本项目没有已接入的顾问实例",
        "rp_ob_detail_execution_undeclared": "project.json 未声明执行方式(execution)",
        "rp_ob_detail_instance_ok": "{instance} 已接入({via})",
        "rp_ob_detail_instance_missing": "{instance} 未接入:{reason}",
        "rp_ob_detail_instance_no_ws": "{instance} 已接入但无工位位置:{reason}",
        "rp_ob_detail_instance_error": "{instance} 接入状态读不出:{error}",
        "rp_ob_detail_envelope_ok": "在用:{policies}",
        "rp_ob_detail_envelope_low": "将尽或用尽:{policies}",
        "rp_ob_detail_envelope_none": "无在用信封",
        "rp_ob_detail_charter_ok": "{instance} 最近一次拉起前章程核对 {outcome}({at})",
        "rp_ob_detail_charter_bad": "{instance} 最近一次章程核对未过 {outcome}({at}):{detail}",
        "rp_ob_detail_charter_unchecked": "{instance} 还没有章程核对记录(loop 拉起前会核)",
        "rp_ob_detail_charter_remote": "{instance} 是跨机工位, 章程在工位所在机核",
        "rp_ob_detail_charter_na": "无本机 pi 工位实例",
        "rp_ob_detail_pi_model_ok": "{role} 用 {model}(来源 {source})",
        "rp_ob_detail_pi_model_unset": "{role} 未声明模型({source})",
        "rp_ob_detail_roadmap_ok": "{rel}:里程碑 {milestones} 个, 标准满足 {met}/{total}",
        "rp_ob_detail_roadmap_none": "未声明路线图({rel} 不存在)",
        "rp_ob_detail_first_closed_ok": "已结案 {count} 张",
        "rp_ob_detail_first_closed_none": "还没有结案的卡(在途 {inflight} 张)",
        # AIPOS-F181: one skeleton for the board, project page, subproject grouping, counts without frozen cards, inbox in plain words
        "rp_tech": "技术详情",
        "rp_show_hidden": "显示测试项目({count} 个)",
        "rp_hide_hidden": "隐藏测试项目",
        "rp_test_badge": "测试",
        "rp_qs_count": "{state} {count}",
        "rp_qs_pending": "待认领",
        "rp_qs_claimed": "进行中",
        "rp_qs_blocked": "受阻",
        "rp_qs_completed": "已结案",
        "rp_qs_withdrawn": "已撤回",
        "rp_sub_head": "子项目",
        "rp_sub_inflight": "在途",
        "rp_sub_todo": "待办",
        "rp_sub_closed": "已结案",
        "rp_sub_advisor": "归属顾问",
        "rp_sub_no_advisor": "(未知)",
        "rp_pj_title": "项目 · {project}",
        "rp_pj_all": "全部",
        "rp_pj_filter": "子项目",
        "rp_pj_inflight": "在途 {count} 张",
        "rp_pj_todo": "待办 {count} 张",
        "rp_pj_closed": "已结案 {count} 张(点开)",
        "rp_pj_nothing": "没有在途或待办的卡",
        "rp_pj_unknown_sub": "子项目 {subproject} 不在本项目, 已显示全部",
        "rp_pj_roadmap": "看路线图",
        "rp_pj_envelopes": "看信封",
        "rp_pj_workstation": "工位设置(谁做哪类卡)",
        "rp_pj_legacy": "旧版任务中心(卡片原文与预览)",
        # AIPOS-F246: 首页 / 项目一页纸 / 工程页
        "rp_nav_home": "首页",
        "rp_nav_history": "历史与审计",
        "rp_nav_engineering": "工程",
        "hm_title": "首页",
        "hm_asks_kicker": "需要你 · {count} 件",
        "hm_asks_none": "现在没有要你定的事",
        "hm_ask_more": "还有 {count} 件 →",
        "hm_ask_go": "去处理 →",
        "hm_ask_waited": "等了 {days} 天",
        "hm_ask_today": "今天提出",
        "hm_ask_loop_owner_needed": "{card}:停下等你定",
        "hm_ask_needs_owner_card": "{card}:等你定",
        "hm_ask_rework_limit": "{card}:返工到上限, 要你定下一步",
        "hm_ask_supplementary_fail": "{card}:补审没过, 要你定",
        "hm_ask_envelope_warning": "{advisor} 的自动推进授权快用完或快到期",
        "hm_ask_roadmap_drift": "{milestone}:进度偏了",
        "hm_ask_owner_check": "{milestone}:验收抽查",
        "hm_ask_enrollment_redeclare": "{advisor} 改登记待你批",
        "hm_ask_auto_released": "{card}:已自动放行(可撤回)",
        "hm_ask_other": "{project}:有一件事等你定",
        "hm_inbox_error": "需要你的事暂时读不出:{error}",
        "hm_projects_kicker": "项目",
        "hm_now": "现在",
        "hm_done": "做成",
        "hm_stuck": "卡住",
        "hm_no_headline": "顾问还没写简报",
        "hm_stuck_none": "无",
        "hm_stuck_line": "{count} 件停滞 · 最久:{title}({days} 天无进展)",
        "hm_stuck_line_norecord": "{count} 件停滞 · 最久:{title}(没有任何活动记录)",
        "hm_progress_criteria": "验收标准 {met}/{total}",
        "hm_progress_closed": "近 {days} 天结案 {count} 件",
        "hm_foot": "顾问 {advisor} · {age}",
        "hm_age_now": "刚刚更新",
        "hm_age_hours": "{hours} 小时前更新",
        "hm_age_days": "{days} 天前更新",
        "hm_stale": "{days} 天未更新",
        "hm_foot_none": "还没写简报",
        "hm_detail": "详情 →",
        "hm_paused_folded": "已暂停 {count} 位(点开)",
        "hm_paused_chip": "暂停",
        "hm_no_cards": "还没有接入顾问的项目",
        "hm_project_error": "{project} 有些数据读不出:{error}",
        # AIPOS-F248: "needs you" recommendation + one click (web/board/readonly_pages.py + static/owner-asks.js), zh / en same keys
        "hm_rec_tag": "推荐",
        "hm_rec_advisor": "{advisor} 建议「{label}」:",
        "hm_rec_default": "建议「{label}」:",
        "hm_advisor_generic": "顾问",
        "hm_artifacts_count": "{count} 个产物",
        "hm_look_artifacts": "看产物 →",
        "hm_look_scope": "看授权范围 →",
        "hm_alt_scope": "改范围…",
        "hm_btn_renew": "续 {days} 天",
        "hm_btn_grant": "按建议授权",
        "hm_btn_more": "再给 {count} 张",
        "hm_cf_more": "将按原范围再给 {count} 张(到期不变) · 签好后告知{advisor}顾问",
        "hm_done_more": "已再给 {count} 张 · 已告知{advisor}顾问",
        "hm_cf_decide": "将记录你的决定「{label}」{words} · 记录后告知{advisor}顾问",
        "hm_cf_renew": "将按原范围续 {days} 天、{count} 张 · 签好后告知{advisor}顾问",
        "hm_cf_grant": "将按顾问请求的范围签发授权 · 签好后告知{advisor}顾问",
        "hm_done_decide": "已记录:{label} · 已告知{advisor}顾问",
        "hm_done_renew": "已续 {days} 天 · 已告知{advisor}顾问",
        "hm_done_grant": "已授权 · 已告知{advisor}顾问",
        "hm_group_title": "{milestone}:{count} 项验收抽查",
        "hm_group_open": "逐项看",
        "hm_js_confirm": "确认",
        "hm_js_cancel": "取消",
        "hm_js_next": "下一步",
        "hm_js_working": "处理中…",
        "hm_js_fail": "没成功:{message}",
        "hm_js_login": "登录已过期, 请重新登录后再点",
        "hm_js_words_required": "哪里不对(一句话, 必填)",
        "hm_js_words_optional": "想对顾问说的一句话(可不填)",
        "hm_js_words_part": "(「{words}」)",
        "pb_back": "← 首页",
        "pb_counts": "在推 {pushing} 件(另有停滞 {stalled} 件)",
        "pb_advisors": "顾问",
        "pb_sub": "{stage}顾问 {advisor} · {age}",
        "pb_pushing_kicker": "在推的事",
        "pb_state_inflight": "进行中",
        "pb_state_todo": "待开始",
        "pb_state_stalled": "卡住", "pb_state_check": "抽查",
        "pb_pushing_none": "没有在推的事",
        "pb_stalled_kicker": "卡住 · {count}",
        "pb_stalled_days": "{days} 天无进展",
        "pb_stalled_norecord": "没有活动记录",
        "pb_ms_kicker": "里程碑",
        "pb_ms_none": "没有归这位顾问的里程碑",
        "pb_ms_done": "已完成",
        "pb_ms_target": "目标 {date}",
        "pb_ms_overdue": "已逾期 · 目标 {date}",
        "pb_ms_owner": "等你抽查 {count} 条",
        "pb_dl_kicker": "最近交付物",
        "pb_dl_none": "近 {days} 天没有结案",
        "pb_dl_more": "近 {days} 天共结案 {count} 件",
        "pb_asks_kicker": "等你验收 · {count}",
        "pb_asks_none": "没有等你验收的",
        "pb_asks_go": "去看 →",
        "pb_eng": "工程详情(点开)",
        "pb_eng_cards": "全部任务卡(含已结案)",
        "pb_eng_roadmap": "路线图与验收(审计记录与证据)",
        "pb_eng_envelopes": "自动推进授权与信封",
        "pb_eng_onboarding": "接入状态",
        "pb_eng_workstation": "工位设置",
        "pb_eng_cleanup": "清理停滞卡(给顾问的命令, 先预演)",
        "pb_unknown_advisor": "顾问 {advisor} 不在本项目, 已显示 {shown}",
        "pb_no_advisor": "本项目还没有接入顾问",
        "eng_title": "工程",
        "eng_intro": "工程细节都在这里;首页与项目页只放概要。",
        "eng_inbox": "待你决定(全部)",
        "eng_inbox_desc": "收件箱全量:每件的选项、顾问建议与裁决记录",
        "eng_overview": "项目总览(工程版)",
        "eng_overview_desc": "每个项目的在途 / 待办 / 已结案计数、信封、路线图偏差、接入、升级",
        "eng_roadmap": "路线图与验收",
        "eng_roadmap_desc": "逐条验收标准、挂接卡、审计裁决与运行证据",
        "eng_envelopes": "信封与项目授权",
        "eng_envelopes_desc": "自动推进授权的在用 / 待签 / 历史",
        "eng_onboarding": "项目接入",
        "eng_onboarding_desc": "每个项目的接入清单与未完成项",
        "eng_workstation": "工位设置",
        "eng_workstation_desc": "谁做哪类卡、工位计划",
        "eng_upgrade": "项目升级",
        "eng_upgrade_desc": "治理根跟随产品升级的待办",
        "eng_cards": "全部任务卡",
        "eng_cards_desc": "按子项目列出在途 / 待办 / 已结案卡(历史与审计页上线前的检索入口)",
        "ib_title": "待你决定 · {count} 件({projects} 个项目)",
        "ib_empty": "待你决定:没有待定事项({projects} 个项目)",
        "ib_heading": "{project} · {what}",
        "ib_card": "卡 {task_id}",
        "ib_card_title": "卡 {task_id}「{title}」",
        "ib_ask": "要你做什么:{ask}",
        "ib_recommend": "顾问建议:{recommend}",
        "ib_grant": "这件事上面已有「待授权请求」卡, 在那里按建议签发",
        "ib_board": "去信封页处理",
        "ib_details": "查看详情",
        "ib_hidden": "已暂停 / 测试项目缺省不列:{projects}",
        "ib_show": "显示已暂停 / 测试项目({count} 个)",
        "ib_hide": "隐藏已暂停 / 测试项目",
        "ib_errors": "有 {count} 处真相读不出(未计入上面, 不猜)",
        "ib_error": "{project}:{source}:{error}",
        # AIPOS-F226: inbox one-click ruling (web/board/readonly_pages.py + static/inbox-decide.js), zh / en same keys
        "ib_decide_heading": "直接裁决:点一个选项 → 看预览 → 确认, 记为你本人的裁决, 顾问下一次 loop 直接读到接着做",
        "ib_decide_readonly": "直接裁决只对 Owner 登录会话开放;其他角色看到这里,请告诉顾问。",
        "ib_words_label": "想对顾问说的一句话(可不填)",
        "ib_fallback": "顾问没跟上时可复制的话术",
        "ib_deny_decide": "不批准(直接记录, 顾问下次 loop 读到)",
        "ib_ruled_banner": "已裁决「{label}」→ 将由 {advisor} 的下一次 loop 接续",
        "ib_ruled_done": "已裁决「{label}」(决策记录 {decision_id})",
        "ib_ruled_unknown": "该卡驱动顾问",
        # AIPOS-F228: inbox "auto-released" column (tier 2, revocable; web/board/readonly_pages.py), zh / en same keys
        "ib_auto_heading": "已自动放行 · {count} 项(不需你定, 可撤回)",
        "ib_auto_note": "门已按规则自动放行并留了记录。要撤回就点「撤回自动放行」, 本卡会停住等你另定;点「知悉」= 收起这一条。",
        "ib_dc_confirm_heading": "确认这个裁决",
        "ib_dc_item": "事项:{what}",
        "ib_dc_choice": "你选的:{label}",
        "ib_dc_words": "你的原话:{words}",
        "ib_dc_after": "之后:{advisor} 下一次跑 loop / next 会直接读到并接着做, 不用你转达",
        "ib_dc_confirm_btn": "确认裁决",
        "ib_dc_cancel_btn": "取消",
        "ib_dc_working": "处理中…",
        "ib_dc_ok_heading": "已裁决, 正在刷新页面…",
        "ib_dc_fail_heading": "没有裁决成功",
        "ib_dc_fail_next": "下一步:刷新页面后重来;仍不行就把下面的话术复制给顾问。",
        "ib_dc_login": "登录已过期或还没登录, 这次没有记录任何东西。",
        "ib_dc_login_next": "下一步:重新登录后再点一次。",
        "ib_dc_retry_btn": "刷新页面",
        "ib_dc_tech": "查看技术详情",
        # AIPOS-F180: authorization requests on the inbox / envelope pages (web/board/readonly_pages.py + static/envelopes.js), zh / en same keys
        "gr_section_title": "待授权请求({count})",
        "gr_readonly": "签发只对 Owner 登录会话开放;其他角色看到这里,请告诉顾问。",
        "gr_errors_note": "有 {count} 处真相读不出,可能漏列请求:去收件箱看详情",
        "gr_sentence": "{advisor} 请求:为 {task_id}{title_part}开一个{mode}的授权,{repos}{launch},{count} 张业务卡(每张含其审计与返工),到 {date}。理由:{reason}",
        "gr_auth_sentence": "{advisor} 推 {task_id}{title_part}超出项目授权({dimension}):建议把项目授权 {authorization_id} 扩为 —— {change}(修订 r{revision})",
        "gr_launch_part": ",允许 loop 拉起 {harnesses} 工位",
        "gr_title_part": "({title})",
        "gr_repos_part": "仅 {repos}",
        "gr_repos_any": "不限仓",
        "gr_mode_code": "改代码",
        "gr_mode_docs": "只写文档",
        "gr_mode_governance": "写治理文档",
        "gr_mode_config": "改配置",
        "gr_mode_audit": "做审计",
        "gr_mode_validation": "做固定候选验证",
        "gr_mode_any": "不限模式",
        "gr_meta": "项目 {project} · 签发后编号 {policy_id} · 停下于 {since}",
        "gr_btn_sign": "按建议签发",
        "gr_btn_edit": "修改后签发",
        "gr_btn_deny": "不批准(告诉顾问)",
        "gr_edit_max": "几张卡(每张含其审计与返工)",
        "gr_edit_until": "到哪天",
        "gr_edit_repos": "哪些仓(逗号分隔, 留空 = 不限)",
        "gr_edit_submit": "看一下再签",
        "gr_edit_invalid": "「几张卡」要填大于 0 的整数,「到哪天」要选一个日期。",
        "gr_deny_tell": "告诉 {advisor}:这次不批准 {task_id} 的授权(可以补一句原因)。顾问会用下面的命令记下你的决定(选项填 deny_grant)。",
        "gr_tech": "查看技术详情",
        "gr_confirm_heading": "确认签发这个授权?",
        "gr_confirm_who": "给谁:{agent}",
        "gr_confirm_what": "能做什么:{mode}",
        "gr_confirm_repos": "哪些仓:{repos}",
        "gr_confirm_count": "几张卡:{count} 张业务卡(每张卡认领时扣 1;其交回、审计、返工、合入、结案不另扣,只受到期与撤销约束)",
        "gr_confirm_until": "到哪天:{date}",
        "gr_confirm_card": "影响哪张卡:{task_id}{title_part}",
        "gr_confirm_project": "哪个项目:{project}",
        "gr_confirm_btn": "确认签发",
        "gr_cancel_btn": "取消",
        "gr_working": "正在处理,请稍候…",
        "gr_ok_heading": "已签发 {policy_id}",
        "gr_fail_heading": "没签成",
        "gr_fail_next": "下一步:{next}",
        "gr_retry_btn": "刷新后重来",
        "gr_err_other": "签发没完成:{message}",
        "gr_next_other": "把这段话发给顾问看看。",
        "gr_err_OWNER_ONLY": "只有 Owner 登录后才能签发。",
        "gr_next_OWNER_ONLY": "用 Owner 身份登录看板后再点一次。",
        "gr_err_ORIGIN_MISMATCH": "这个请求不是从看板页面发出的,为安全起见拒了。",
        "gr_next_ORIGIN_MISMATCH": "直接在看板页面里操作,不要从别的网站或脚本发。",
        "gr_err_WORKSPACE_REQUIRED": "请求里没写是哪个项目。",
        "gr_next_WORKSPACE_REQUIRED": "刷新页面后重来;还不行就告诉顾问。",
        "gr_err_WORKSPACE_NOT_IN_HOME": "这个项目不在本看板管理的项目里。",
        "gr_next_WORKSPACE_NOT_IN_HOME": "到「项目接入」页确认项目已接入,或告诉顾问。",
        "gr_err_PARAMS_INVALID": "填的内容有问题:{message}",
        "gr_next_PARAMS_INVALID": "改一下次数、日期或仓名再试。",
        "gr_err_TEMPLATE_NOT_CURRENT": "要续签的那个信封已经不是在用的了(可能刚被别的信封接替)。",
        "gr_next_TEMPLATE_NOT_CURRENT": "刷新页面,对最新的在用信封再续签。",
        "gr_err_STAGE_INVALID": "页面发出的请求格式不对。",
        "gr_next_STAGE_INVALID": "刷新页面后重来。",
        "gr_err_PREVIEW_BLOCKED": "签发前的检查没过:{message}",
        "gr_next_PREVIEW_BLOCKED": "把这段话发给顾问,让顾问调整请求后再来。",
        "gr_err_PREVIEW_TOKEN_REQUIRED": "还没确认就直接签了。",
        "gr_next_PREVIEW_TOKEN_REQUIRED": "从「按建议签发」重新开始。",
        "gr_err_PREVIEW_TOKEN_INVALID": "这次确认已经用过了(每次确认只能用一次)。",
        "gr_next_PREVIEW_TOKEN_INVALID": "刷新页面后重来。",
        "gr_err_PREVIEW_TOKEN_EXPIRED": "确认等得太久,已经超时。",
        "gr_next_PREVIEW_TOKEN_EXPIRED": "重新点「按建议签发」,几分钟内确认。",
        "gr_err_PREVIEW_SESSION_MISMATCH": "确认用的不是这次登录。",
        "gr_next_PREVIEW_SESSION_MISMATCH": "在同一个浏览器窗口里重新签一次。",
        "gr_err_PREVIEW_PARAMS_MISMATCH": "确认前内容变了(可能刚有人签了同一个编号)。",
        "gr_next_PREVIEW_PARAMS_MISMATCH": "刷新页面后重新签。",
        "gr_err_AUDIT_LOG_UNAVAILABLE": "看板的签发记录写不进去,为保证留痕拒签。",
        "gr_next_AUDIT_LOG_UNAVAILABLE": "告诉顾问检查看板服务工作区的 .lybra 目录是否可写。",
        "gr_err_CREDENTIAL_UNAVAILABLE": "看板服务没有可用的 Owner 签发凭据。",
        "gr_next_CREDENTIAL_UNAVAILABLE": "告诉顾问;也可以在治理根所在机用技术详情里的命令签。",
        "gr_err_MINT_REJECTED": "门没让签:{message}",
        "gr_next_MINT_REJECTED": "把这段话发给顾问;如果是编号已存在,刷新页面再签。",
    },
    "en": {
        "advisor_prompt": """You are the Advisor for the {workspace_label}.

Workspace info:
- Path: {workspace_root}
- Gate URL: {gate_url}
- Charter: {charter_path}

Lybra server location (AIPOS-286):
- Hostname: {server_hostname}
- IP address: {server_ip}

Your responsibilities:
- Read the charter to understand red lines and working protocols
- Draft task cards (in 5_tasks/drafts/)
- Suggest publishing to Owner
- Check status: lybra queue / lybra records / lybra owner-truth
- Watch queue & record changes: lybra agent watch --workspace-root {workspace_root} — returns summary on change, ideal as your wakeup pump

Quick start:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  Step 0: Same-machine verification & connectivity check (AIPOS-286 mandatory prerequisite)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before connecting to the gate, you must complete these checks:

1. **Verify if you and the Lybra server are on the same machine**:
   - Server hostname: {server_hostname}
   - Server IP: {server_ip}
   - Check method: Run `hostname` and `hostname -I`, compare with the above
   - Same machine: proceed to step 2
   - Different machines: proceed to step 2 (cross-machine scenario)

2. **Connectivity check** (mandatory for cross-machine, recommended for same-machine):
   
   **Method A — HTTP health check (recommended)**:
   ```bash
   curl -v {gate_url}/health
   ```
   Expected: 200 OK + JSON response (containing `"ok": true`)
   
   **Method B — File truth surface check** (requires SSH or mount):
   - Try accessing workspace path: `ls {workspace_root}/5_tasks/queue`
   - Expected: Can list queue files
   
   **What if checks fail**:
   - If connectivity check fails (curl timeout, SSH unreachable, path inaccessible):
     **Stop immediately, block-and-report to Owner**, stating:
     * Your location (hostname + IP)
     * Server location ({server_hostname} / {server_ip})
     * Specific failure symptom (timeout, connection refused, permission denied, etc.)
     * Need Owner to configure SSH connectivity or network routing before proceeding
   - **Never proceed with broken connectivity**: Forcing MCP config when gate is unreachable causes all subsequent operations to fail silently

3. **Proceed only after passing**:
   - Connectivity confirmed OK → continue to "Zero-install onboarding" below to configure MCP connection

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔌 Zero-install onboarding (any MCP agent, no Lybra CLI installation needed)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Use the advisor token provided by Owner:
   - Claude Desktop/Cline: mcpServers → {{"lybra": {{"url": "{gate_url}/mcp", "headers": {{"Authorization": "Bearer <ADVISOR_TOKEN>"}}}}}}
   - Claude Code CLI: claude mcp add lybra --transport http {gate_url}/mcp --header "Authorization: Bearer <ADVISOR_TOKEN>"
   - Pi/Codex/other HTTP MCP harness: {{"url": "{gate_url}/mcp", "headers": {{"Authorization": "Bearer <ADVISOR_TOKEN>"}}}}
   Note: Above are common harness examples (non-exhaustive), desktop/CLI both work; agents without MCP support can use direct filesystem operations.

2. 🔧 Install Lybra CLI (standard step 2, needed for full functionality)
   Full functionality (including the agent watch sentinel and lybra loop) requires Lybra CLI:
   
   Method A — Install from npm (recommended):
   npm install -g lybra
   pip install "textual>=4.0"  # TUI dependency
   lybra --version
   
   Method B — Bootstrap from gate (agent self-service):
   If gate machine exposes git/pip sources, agent can run:
   git clone <LYBRA_REPO_URL> /tmp/lybra && cd /tmp/lybra
   npm install -g .
   pip install "textual>=4.0"
   lybra --version
   
   After installation, wait for queue/record changes with the filesystem sentinel (pure client, read-only, no token):
     lybra agent watch --workspace-root {workspace_root} --timeout 30
   Drive a card with lybra loop --task-id <card-id> (executor workstations only type /go).

3. 📖 Read charter & examples
   - Charter: {charter_path}
   - Example card: {example_card_path}

4. Draft your first task card, suggest publishing to Owner

---

Important boundaries:
- You have write access to the governance workspace (draft task cards, maintain governance docs)
- Published cards, queue, records are gate's domain, no manual writes
- Product repos and other repos are read-only by default, unless Owner explicitly authorizes
- Publishing requires Owner confirmation (you draft, Owner decides)
- Credentials are referenced by name only, never read/echo token file contents""",
        # AIPOS-F174: 看板只读页(总览 / 路线图与验收 / 项目接入)文案, zh / en 同键(web/board/readonly_pages.py 经 _generate_text 取)
        "rp_nav_overview": "Project overview",
        "rp_nav_roadmap": "Roadmap & acceptance",
        "rp_nav_onboarding": "Project onboarding",
        "rp_nav_workstation": "Workstations",
        "rp_nav_inbox": "Your decisions",
        "rp_nav_envelopes": "Envelopes",
        "rp_nav_project": "Projects",
        "rp_lang_other": "中文",
        "rp_readonly_note": "This page is read-only. Wherever your action is needed, it shows what to tell your advisor and the command your advisor runs.",
        "rp_tell": "Tell {advisor}: {ask}",
        "rp_advisor_fallback": "your advisor",
        "rp_command": "Commands for your advisor (open)",
        "rp_paused_folded": "Paused projects ({count})",
        "rp_paused_badge": "Paused",
        "rp_error": "Unreadable: {error}",
        "rp_page_error": "Page unreadable (reason verbatim): {error}",
        "rp_ov_title": "Project overview · {count} projects",
        "rp_ov_counts": "In flight {inflight} · To do {todo} · Closed {closed}",
        "rp_ov_frozen": "Legacy backlog {count} cards (frozen, not counted above)",
        "rp_ov_frozen_error": "Frozen list unreadable, the numbers above count every card: {error}",
        "rp_ov_one_advisor": "Advisor: {advisor}",
        "rp_ov_inbox": "{count} awaiting your decision",
        "rp_ov_inbox_paused": "Paused projects stay out of the inbox",
        "rp_ov_envelope_ok": "Envelopes in use: {policies}",
        "rp_ov_envelope_low": "Envelopes running low or exhausted: {policies}",
        "rp_ov_envelope_none": "No envelope in use",
        "rp_ov_roadmap": "Roadmap: criteria met {met}/{total} · {milestones} milestones · {signals} drift signals ({severe} severe)",
        "rp_ov_roadmap_none": "Roadmap: not declared",
        "rp_ov_onboarding": "Onboarding: {done}/{total} steps done",
        "rp_ov_upgrade": "Upgradable ({count})",
        "rp_ov_upgrade_none": "Project upgrade: up to date",
        "rp_ov_detail": "Project detail",
        "rp_rm_title": "Roadmap & acceptance · {project}",
        "rp_rm_projects": "Projects",
        "rp_rm_source": "Source {path} · today {today} · criteria met {met}/{total}",
        "rp_rm_signals": "{count} drift signals",
        "rp_rm_no_signals": "No drift signals",
        "rp_sev_inbox": "Severe",
        "rp_sev_warn": "Warning",
        "rp_rm_signal_ask": "Handle the roadmap drift \"{label}\" ({subject})",
        "rp_rm_advice": "Advice",
        "rp_rm_milestones": "Milestones (by lane)",
        "rp_rm_lane": "lane {lane}",
        "rp_rm_target": "Target {date}",
        "rp_rm_overdue": "Overdue",
        "rp_rm_status_planned": "Planned",
        "rp_rm_status_active": "Active",
        "rp_rm_status_done": "Done",
        "rp_rm_status_dropped": "Dropped",
        "rp_rm_criteria_title": "{id} · {title}: acceptance criteria",
        "rp_rm_verify": "Verified by: {verify}",
        "rp_rm_verify_auto": "automatic (audit PASS + runtime evidence)",
        "rp_rm_verify_artifact": "artifact check",
        "rp_rm_verify_owner_check": "Owner spot check",
        "rp_rm_crit_met": "Met",
        "rp_rm_crit_unmet": "Not met: {reason}",
        "rp_rm_state": "Verification: {state}",
        "rp_rm_state_unverified": "not verified",
        "rp_rm_state_auto_passed": "auto-verified",
        "rp_rm_state_owner_pending": "awaiting Owner spot check",
        "rp_rm_state_owner_confirmed": "confirmed by Owner",
        "rp_rm_state_mismatch": "mismatch",
        "rp_rm_basis": "Basis: {basis}",
        "rp_rm_artifact": "Artifact [{kind}] {path_glob}",
        "rp_rm_artifact_none": "no matching file in {dir}",
        "rp_rm_artifact_open": "full size",
        "rp_rm_owner_record": "Owner spot check: {label} · {decided_at} · relayed by {relayed_by} · Owner said \"{owner_words}\"",
        "rp_rm_history": "{count} spot-check record(s)",
        "rp_rm_fix": "Fix card: {fix}",
        "rp_rm_verify_ask": "after looking at the artifacts of {criterion}: \"{criterion} conforms\" or \"{criterion} does not conform: what is wrong\"",
        "rp_rm_card": "Card {task_id} · queue {queue_state}",
        "rp_rm_verdict": "Audit verdict {verdict}",
        "rp_rm_no_verdict": "No audit verdict yet",
        "rp_rm_supp": "Supplementary verdict {verdict} ({verdict_id})",
        "rp_rm_record_link": "View verdict record",
        "rp_rm_evidence": "Runtime evidence (from {ref})",
        "rp_rm_evidence_missing": "No runtime evidence excerpt: {problem}",
        "rp_rm_evidence_entry": "exit code {exit_code}",
        "rp_rm_mismatch": "Status disagrees with the derivation: {text}",
        "rp_rm_next": "Next steps",
        "rp_rm_unlinked": "{count} unlinked cards created after the roadmap: {cards}",
        "rp_rm_unlinked_before": "{count} older cards from before the roadmap have no milestone; they do not count as unlinked",
        "rp_rm_folded": "Done / dropped milestones ({count})",
        "rp_rm_none_for_sub": "No milestone for this subproject yet",
        "rp_rm_invalid": "Invalid link {task_id}: {problems}",
        "rp_rm_errors": "{count} unreadable sources (affected criteria count as unmet)",
        "rp_rm_howto_title": "How to start",
        "rp_rm_howto_ask": "Set up a roadmap for {project}: milestones plus checkable acceptance criteria",
        "rp_rm_unknown_project": "Project {project} is not registered under the home root",
        "rp_rm_unknown_milestone": "Milestone {milestone} is not in the roadmap; showing the default one",
        "rp_rm_no_projects": "No registered project under the home root",
        "rp_rm_no_criteria": "This milestone has no acceptance criteria yet",
        "rp_ob_title": "Project onboarding checklist · {count} projects",
        "rp_ob_summary": "{done}/{total} steps done",
        "rp_ob_state_ok": "Done",
        "rp_ob_state_todo": "To do",
        "rp_ob_state_na": "N/A",
        "rp_ob_state_error": "Unreadable",
        "rp_ob_item_governance_root": "Governance root set up",
        "rp_ob_item_advisor": "Advisor instance enrolled",
        "rp_ob_item_executor": "Executor instance enrolled",
        "rp_ob_item_auditor": "Auditor instance enrolled",
        "rp_ob_item_envelope": "Envelope in use and not running low",
        "rp_ob_item_charter": "Workstation charter fresh",
        "rp_ob_item_pi_model": "pi model declared",
        "rp_ob_item_roadmap": "Roadmap declared",
        "rp_ob_item_first_closed": "First card closed",
        "rp_ob_ask_governance_root": "Check the status field in the project.json of {project}",
        "rp_ob_ask_advisor": "Enroll an advisor instance for {project}",
        "rp_ob_ask_executor": "Declare and enroll the executor instance of {project} ({instance})",
        "rp_ob_ask_auditor": "Declare and enroll the auditor instance of {project} ({instance})",
        "rp_ob_ask_envelope": "Prepare an envelope in use for {project} (renew or mint; I authorize it on the Envelopes page)",
        "rp_ob_ask_charter": "Check the workstation charter of {project} ({instance})",
        "rp_ob_ask_pi_model": "Declare the pi model for {project}",
        "rp_ob_ask_roadmap": "Set up a roadmap for {project}",
        "rp_ob_ask_first_closed": "Drive the first card of {project} to closure",
        "rp_ob_detail_governance_root": "Governance root {root}",
        "rp_ob_detail_advisor_ok": "Enrolled: {instances}",
        "rp_ob_detail_advisor_none": "No advisor instance is enrolled in this project",
        "rp_ob_detail_execution_undeclared": "project.json declares no execution section",
        "rp_ob_detail_instance_ok": "{instance} enrolled ({via})",
        "rp_ob_detail_instance_missing": "{instance} not enrolled: {reason}",
        "rp_ob_detail_instance_no_ws": "{instance} enrolled but has no workstation: {reason}",
        "rp_ob_detail_instance_error": "Enrollment of {instance} unreadable: {error}",
        "rp_ob_detail_envelope_ok": "In use: {policies}",
        "rp_ob_detail_envelope_low": "Running low or exhausted: {policies}",
        "rp_ob_detail_envelope_none": "No envelope in use",
        "rp_ob_detail_charter_ok": "Last pre-launch charter check of {instance}: {outcome} ({at})",
        "rp_ob_detail_charter_bad": "Last charter check of {instance} failed: {outcome} ({at}): {detail}",
        "rp_ob_detail_charter_unchecked": "No charter check recorded yet for {instance} (the loop checks it before each launch)",
        "rp_ob_detail_charter_remote": "{instance} is a cross-machine workstation; its charter is checked on that machine",
        "rp_ob_detail_charter_na": "No local pi workstation instance",
        "rp_ob_detail_pi_model_ok": "{role} uses {model} (from {source})",
        "rp_ob_detail_pi_model_unset": "{role} has no declared model ({source})",
        "rp_ob_detail_roadmap_ok": "{rel}: {milestones} milestones, criteria met {met}/{total}",
        "rp_ob_detail_roadmap_none": "No roadmap declared ({rel} does not exist)",
        "rp_ob_detail_first_closed_ok": "{count} cards closed",
        "rp_ob_detail_first_closed_none": "No card closed yet ({inflight} in flight)",
        # AIPOS-F181: one skeleton for the board, project page, subproject grouping, counts without frozen cards, inbox in plain words
        "rp_tech": "Technical details",
        "rp_show_hidden": "Show test projects ({count})",
        "rp_hide_hidden": "Hide test projects",
        "rp_test_badge": "Test",
        "rp_qs_count": "{state} {count}",
        "rp_qs_pending": "waiting",
        "rp_qs_claimed": "in progress",
        "rp_qs_blocked": "blocked",
        "rp_qs_completed": "closed",
        "rp_qs_withdrawn": "withdrawn",
        "rp_sub_head": "Subproject",
        "rp_sub_inflight": "In flight",
        "rp_sub_todo": "To do",
        "rp_sub_closed": "Closed",
        "rp_sub_advisor": "Advisor",
        "rp_sub_no_advisor": "(unknown)",
        "rp_pj_title": "Project · {project}",
        "rp_pj_all": "All",
        "rp_pj_filter": "Subproject",
        "rp_pj_inflight": "In flight ({count})",
        "rp_pj_todo": "To do ({count})",
        "rp_pj_closed": "Closed ({count}, click to open)",
        "rp_pj_nothing": "No card in flight or to do",
        "rp_pj_unknown_sub": "Subproject {subproject} is not in this project; showing all",
        "rp_pj_roadmap": "Roadmap",
        "rp_pj_envelopes": "Envelopes",
        "rp_pj_workstation": "Workstations (who works which card types)",
        "rp_pj_legacy": "Classic task center (card text and preview)",
        # AIPOS-F246: home / project brief / engineering page
        "rp_nav_home": "Home",
        "rp_nav_history": "History & audit",
        "rp_nav_engineering": "Engineering",
        "hm_title": "Home",
        "hm_asks_kicker": "Needs you · {count}",
        "hm_asks_none": "Nothing waiting for you right now",
        "hm_ask_more": "{count} more →",
        "hm_ask_go": "Open →",
        "hm_ask_waited": "waiting {days} days",
        "hm_ask_today": "raised today",
        "hm_ask_loop_owner_needed": "{card}: stopped, waiting for you",
        "hm_ask_needs_owner_card": "{card}: waiting for you",
        "hm_ask_rework_limit": "{card}: rework limit reached, decide the next step",
        "hm_ask_supplementary_fail": "{card}: follow-up review failed, decide",
        "hm_ask_envelope_warning": "{advisor}'s auto-run grant is running low or expiring",
        "hm_ask_roadmap_drift": "{milestone}: progress drifting",
        "hm_ask_owner_check": "{milestone}: acceptance spot check",
        "hm_ask_enrollment_redeclare": "{advisor} re-registration awaits your approval",
        "hm_ask_auto_released": "{card}: auto-released (can be withdrawn)",
        "hm_ask_other": "{project}: something awaits your decision",
        "hm_inbox_error": "Items for you cannot be read right now: {error}",
        "hm_projects_kicker": "Projects",
        "hm_now": "Now",
        "hm_done": "Done",
        "hm_stuck": "Stuck",
        "hm_no_headline": "The advisor has not written a headline yet",
        "hm_stuck_none": "None",
        "hm_stuck_line": "{count} stalled · longest: {title} ({days} days without progress)",
        "hm_stuck_line_norecord": "{count} stalled · longest: {title} (no activity recorded)",
        "hm_progress_criteria": "Acceptance criteria {met}/{total}",
        "hm_progress_closed": "{count} closed in the last {days} days",
        "hm_foot": "Advisor {advisor} · {age}",
        "hm_age_now": "updated just now",
        "hm_age_hours": "updated {hours} h ago",
        "hm_age_days": "updated {days} days ago",
        "hm_stale": "not updated for {days} days",
        "hm_foot_none": "no headline yet",
        "hm_detail": "Details →",
        "hm_paused_folded": "{count} paused (expand)",
        "hm_paused_chip": "Paused",
        "hm_no_cards": "No project has an advisor yet",
        "hm_project_error": "Some data of {project} cannot be read: {error}",
        # AIPOS-F248: "needs you" recommendation + one click
        "hm_rec_tag": "Recommended",
        "hm_rec_advisor": "{advisor} suggests \"{label}\": ",
        "hm_rec_default": "Suggested \"{label}\": ",
        "hm_advisor_generic": "The advisor",
        "hm_artifacts_count": "{count} artifacts",
        "hm_look_artifacts": "See artifacts →",
        "hm_look_scope": "See the grant scope →",
        "hm_alt_scope": "Change scope…",
        "hm_btn_renew": "Renew {days} days",
        "hm_btn_grant": "Grant as suggested",
        "hm_btn_more": "{count} more cards",
        "hm_cf_more": "Will add {count} cards with the same scope (expiry unchanged) · then tell the {advisor} advisor",
        "hm_done_more": "Added {count} cards · the {advisor} advisor has been told",
        "hm_cf_decide": "Will record your decision \"{label}\"{words} · then tell the {advisor} advisor",
        "hm_cf_renew": "Will renew with the same scope for {days} days, {count} cards · then tell the {advisor} advisor",
        "hm_cf_grant": "Will grant the scope the advisor asked for · then tell the {advisor} advisor",
        "hm_done_decide": "Recorded: {label} · the {advisor} advisor has been told",
        "hm_done_renew": "Renewed {days} days · the {advisor} advisor has been told",
        "hm_done_grant": "Granted · the {advisor} advisor has been told",
        "hm_group_title": "{milestone}: {count} acceptance spot checks",
        "hm_group_open": "One by one",
        "hm_js_confirm": "Confirm",
        "hm_js_cancel": "Cancel",
        "hm_js_next": "Next",
        "hm_js_working": "Working…",
        "hm_js_fail": "Did not go through: {message}",
        "hm_js_login": "Your login has expired; log in again and retry",
        "hm_js_words_required": "What is wrong (one sentence, required)",
        "hm_js_words_optional": "A sentence for the advisor (optional)",
        "hm_js_words_part": " (\"{words}\")",
        "pb_back": "← Home",
        "pb_counts": "{pushing} in progress ({stalled} more stalled)",
        "pb_advisors": "Advisors",
        "pb_sub": "{stage}Advisor {advisor} · {age}",
        "pb_pushing_kicker": "In progress",
        "pb_state_inflight": "Working",
        "pb_state_todo": "To start",
        "pb_state_stalled": "Stuck", "pb_state_check": "Check",
        "pb_pushing_none": "Nothing in progress",
        "pb_stalled_kicker": "Stuck · {count}",
        "pb_stalled_days": "{days} days without progress",
        "pb_stalled_norecord": "no activity recorded",
        "pb_ms_kicker": "Milestones",
        "pb_ms_none": "No milestone belongs to this advisor",
        "pb_ms_done": "done",
        "pb_ms_target": "target {date}",
        "pb_ms_overdue": "overdue · target {date}",
        "pb_ms_owner": "{count} awaiting your spot check",
        "pb_dl_kicker": "Recent deliverables",
        "pb_dl_none": "Nothing closed in the last {days} days",
        "pb_dl_more": "{count} closed in the last {days} days",
        "pb_asks_kicker": "Awaiting your acceptance · {count}",
        "pb_asks_none": "Nothing awaiting your acceptance",
        "pb_asks_go": "View →",
        "pb_eng": "Engineering details (expand)",
        "pb_eng_cards": "All task cards (incl. closed)",
        "pb_eng_roadmap": "Roadmap & acceptance (audit records and evidence)",
        "pb_eng_envelopes": "Auto-run grants and envelopes",
        "pb_eng_onboarding": "Onboarding status",
        "pb_eng_workstation": "Workstations",
        "pb_eng_cleanup": "Clean up stalled cards (advisor commands, preview first)",
        "pb_unknown_advisor": "Advisor {advisor} is not in this project; showing {shown}",
        "pb_no_advisor": "This project has no advisor enrolled yet",
        "eng_title": "Engineering",
        "eng_intro": "Engineering details live here; the home and project pages only show the summary.",
        "eng_inbox": "Your decisions (all)",
        "eng_inbox_desc": "The full inbox: options, advisor advice and rulings per item",
        "eng_overview": "Project overview (engineering)",
        "eng_overview_desc": "Per project: in flight / to do / closed counts, envelopes, roadmap drift, onboarding, upgrade",
        "eng_roadmap": "Roadmap & acceptance",
        "eng_roadmap_desc": "Each acceptance criterion, linked cards, audit verdicts and run evidence",
        "eng_envelopes": "Envelopes and project grants",
        "eng_envelopes_desc": "Auto-run grants in use / to sign / history",
        "eng_onboarding": "Project onboarding",
        "eng_onboarding_desc": "Each project's onboarding checklist and open steps",
        "eng_workstation": "Workstations",
        "eng_workstation_desc": "Who does which kind of card, workstation plans",
        "eng_upgrade": "Project upgrade",
        "eng_upgrade_desc": "Pending steps for governance roots to follow the product",
        "eng_cards": "All task cards",
        "eng_cards_desc": "In flight / to do / closed cards by subproject (the lookup until the history page ships)",
        "ib_title": "Your decisions · {count} ({projects} projects)",
        "ib_empty": "Your decisions: nothing pending ({projects} projects)",
        "ib_heading": "{project} · {what}",
        "ib_card": "card {task_id}",
        "ib_card_title": "card {task_id} \"{title}\"",
        "ib_ask": "What you are asked: {ask}",
        "ib_recommend": "Advisor suggests: {recommend}",
        "ib_grant": "This one has an authorization request card above; sign it there as suggested",
        "ib_board": "Handle it on the envelope page",
        "ib_details": "Show details",
        "ib_hidden": "Paused / test projects are not listed: {projects}",
        "ib_show": "Show paused / test projects ({count})",
        "ib_hide": "Hide paused / test projects",
        "ib_errors": "{count} truths could not be read (not counted above, not guessed)",
        "ib_error": "{project}: {source}: {error}",
        # AIPOS-F226: inbox one-click ruling (web/board/readonly_pages.py + static/inbox-decide.js), zh / en same keys
        "ib_decide_heading": "Decide here: pick an option -> check the preview -> confirm; it is recorded as your own decision and the advisor's next loop picks it up",
        "ib_decide_readonly": "Deciding here is open to an Owner login session only; other roles, please tell the advisor.",
        "ib_words_label": "A sentence for the advisor (optional)",
        "ib_fallback": "Message to copy if the advisor falls behind",
        "ib_deny_decide": "Decline (recorded now, the advisor's next loop reads it)",
        "ib_ruled_banner": "Decided \"{label}\" -> {advisor} picks it up on the next loop",
        "ib_ruled_done": "Decided \"{label}\" (decision record {decision_id})",
        "ib_ruled_unknown": "the card's driving advisor",
        # AIPOS-F228: inbox "auto-released" column (tier 2, revocable)
        "ib_auto_heading": "Auto-released · {count} (no decision needed, can be revoked)",
        "ib_auto_note": "The gate released these automatically under the rules and kept a record. To revoke one, press the revoke button: the card stops and waits for your decision; acknowledge = hide this one.",
        "ib_dc_confirm_heading": "Confirm this decision",
        "ib_dc_item": "Item: {what}",
        "ib_dc_choice": "Your choice: {label}",
        "ib_dc_words": "Your words: {words}",
        "ib_dc_after": "Then: {advisor}'s next loop / next reads it and continues, no relaying needed",
        "ib_dc_confirm_btn": "Confirm",
        "ib_dc_cancel_btn": "Cancel",
        "ib_dc_working": "Working...",
        "ib_dc_ok_heading": "Decided, reloading the page...",
        "ib_dc_fail_heading": "The decision was not recorded",
        "ib_dc_fail_next": "Next: reload the page and try again; if it still fails, copy the message below to the advisor.",
        "ib_dc_login": "The login expired or you are not logged in; nothing was recorded.",
        "ib_dc_login_next": "Next: log in again and click once more.",
        "ib_dc_retry_btn": "Reload the page",
        "ib_dc_tech": "Show technical details",
        # AIPOS-F180: authorization requests on the inbox / envelope pages (web/board/readonly_pages.py + static/envelopes.js), zh / en same keys
        "gr_section_title": "Authorization requests ({count})",
        "gr_readonly": "Only an Owner login session can sign; other roles, please tell the advisor.",
        "gr_errors_note": "{count} source(s) could not be read, some requests may be missing: see the inbox for details",
        "gr_sentence": "{advisor} asks: open an authorization to {mode} for {task_id}{title_part}, {repos}{launch}, {count} business card(s) (each including its audit and rework), until {date}. Reason: {reason}",
        "gr_auth_sentence": "{advisor} driving {task_id}{title_part} is outside the project grant ({dimension}): extend project grant {authorization_id} — {change} (revision r{revision})",
        "gr_launch_part": ", letting the loop start the {harnesses} workstation",
        "gr_title_part": " ({title})",
        "gr_repos_part": "only {repos}",
        "gr_repos_any": "any repo",
        "gr_mode_code": "change code",
        "gr_mode_docs": "write docs only",
        "gr_mode_governance": "write governance docs",
        "gr_mode_config": "change config",
        "gr_mode_audit": "run audits",
        "gr_mode_validation": "validate a fixed candidate",
        "gr_mode_any": "work in any task mode",
        "gr_meta": "Project {project} · will be signed as {policy_id} · stopped at {since}",
        "gr_btn_sign": "Sign as suggested",
        "gr_btn_edit": "Edit, then sign",
        "gr_btn_deny": "Decline (tell the advisor)",
        "gr_edit_max": "How many cards (each including its audit and rework)",
        "gr_edit_until": "Until",
        "gr_edit_repos": "Which repos (comma-separated, empty = any)",
        "gr_edit_submit": "Review before signing",
        "gr_edit_invalid": "\"How many cards\" must be a whole number above 0 and \"Until\" must be a date.",
        "gr_deny_tell": "Tell {advisor}: this authorization for {task_id} is declined (add a reason if you like). The advisor records your decision with the command below (choice deny_grant).",
        "gr_tech": "Show technical details",
        "gr_confirm_heading": "Sign this authorization?",
        "gr_confirm_who": "For: {agent}",
        "gr_confirm_what": "Allowed to: {mode}",
        "gr_confirm_repos": "Repos: {repos}",
        "gr_confirm_count": "How many cards: {count} business card(s) (each card counts once at claim; its return, audit, rework, merge and close are not counted again and only the expiry and revocation apply)",
        "gr_confirm_until": "Until: {date}",
        "gr_confirm_card": "Card affected: {task_id}{title_part}",
        "gr_confirm_project": "Project: {project}",
        "gr_confirm_btn": "Confirm and sign",
        "gr_cancel_btn": "Cancel",
        "gr_working": "Working, please wait…",
        "gr_ok_heading": "Signed {policy_id}",
        "gr_fail_heading": "Not signed",
        "gr_fail_next": "Next step: {next}",
        "gr_retry_btn": "Reload and start over",
        "gr_err_other": "Signing did not complete: {message}",
        "gr_next_other": "Send this message to the advisor.",
        "gr_err_OWNER_ONLY": "Only a logged-in Owner can sign.",
        "gr_next_OWNER_ONLY": "Log in to the board as Owner and click again.",
        "gr_err_ORIGIN_MISMATCH": "This request did not come from the board page, so it was refused for safety.",
        "gr_next_ORIGIN_MISMATCH": "Use the board page directly, not another site or a script.",
        "gr_err_WORKSPACE_REQUIRED": "The request did not say which project.",
        "gr_next_WORKSPACE_REQUIRED": "Reload the page and start over; if it persists, tell the advisor.",
        "gr_err_WORKSPACE_NOT_IN_HOME": "This project is not one of the projects this board manages.",
        "gr_next_WORKSPACE_NOT_IN_HOME": "Check the Onboarding page that the project is set up, or tell the advisor.",
        "gr_err_PARAMS_INVALID": "Something in the form is not valid: {message}",
        "gr_next_PARAMS_INVALID": "Fix the number of times, the date or the repo names and try again.",
        "gr_err_TEMPLATE_NOT_CURRENT": "The envelope you are renewing is no longer the one in use (it may have just been replaced).",
        "gr_next_TEMPLATE_NOT_CURRENT": "Reload the page and renew the envelope that is now in use.",
        "gr_err_STAGE_INVALID": "The page sent a malformed request.",
        "gr_next_STAGE_INVALID": "Reload the page and start over.",
        "gr_err_PREVIEW_BLOCKED": "The pre-signing check did not pass: {message}",
        "gr_next_PREVIEW_BLOCKED": "Send this message to the advisor so they can adjust the request.",
        "gr_err_PREVIEW_TOKEN_REQUIRED": "Signing was attempted without the confirmation step.",
        "gr_next_PREVIEW_TOKEN_REQUIRED": "Start again from \"Sign as suggested\".",
        "gr_err_PREVIEW_TOKEN_INVALID": "This confirmation was already used (each confirmation works once).",
        "gr_next_PREVIEW_TOKEN_INVALID": "Reload the page and start over.",
        "gr_err_PREVIEW_TOKEN_EXPIRED": "The confirmation waited too long and expired.",
        "gr_next_PREVIEW_TOKEN_EXPIRED": "Click \"Sign as suggested\" again and confirm within a few minutes.",
        "gr_err_PREVIEW_SESSION_MISMATCH": "The confirmation does not belong to this login.",
        "gr_next_PREVIEW_SESSION_MISMATCH": "Sign again in the same browser window.",
        "gr_err_PREVIEW_PARAMS_MISMATCH": "The details changed before confirming (someone may have just signed the same id).",
        "gr_next_PREVIEW_PARAMS_MISMATCH": "Reload the page and sign again.",
        "gr_err_AUDIT_LOG_UNAVAILABLE": "The board could not write its signing log, so it refused to sign (every signing must be logged).",
        "gr_next_AUDIT_LOG_UNAVAILABLE": "Ask the advisor to check that the board service's .lybra directory is writable.",
        "gr_err_CREDENTIAL_UNAVAILABLE": "The board service has no Owner signing credential available.",
        "gr_next_CREDENTIAL_UNAVAILABLE": "Tell the advisor; you can also sign on the governance host with the command in the technical details.",
        "gr_err_MINT_REJECTED": "The gate refused to sign: {message}",
        "gr_next_MINT_REJECTED": "Send this message to the advisor; if the id already exists, reload the page and sign again.",
    },
}


def _generate_text(template_key: str, locale: str, **variables: Any) -> str:
    """
    AIPOS-286 FIX-2: Unified server-side i18n generation channel.
    
    Generate locale-aware text from templates. All generated content (advisor
    onboarding prompts, wizard guidance, MCP snippet comments, QUICKSTART blocks)
    MUST flow through this function.
    
    Args:
        template_key: Template identifier (e.g., 'advisor_prompt')
        locale: Language code ('zh' or 'en')
        **variables: Template variables for substitution
    
    Returns:
        Rendered text in the requested locale
    
    Raises:
        KeyError: If template_key is missing in the requested locale
                  (enforces complete i18n coverage, no silent fallback)
    
    Red line: Missing en key => test failure. New generated content must declare
    both zh and en templates upfront.
    """
    if locale not in _I18N_TEMPLATES:
        locale = "zh"  # Default to zh if unsupported locale requested
    
    templates = _I18N_TEMPLATES[locale]
    if template_key not in templates:
        raise KeyError(
            f"Template '{template_key}' missing in locale '{locale}'. "
            f"All templates must exist in both zh and en (AIPOS-286 FIX-2 red line)."
        )
    
    template = templates[template_key]
    return template.format(**variables)


def _get_runtime_status_route(params: dict[str, list[str]], *, repo_root: Path | None, home_root: str | Path | None = None,
                              board_config_path: Path | None = None) -> dict[str, Any]:
    operation = "get_runtime_status"
    resolved_root = _resolve_workspace_root(params, repo_root or REPO_ROOT, home_root)  # AIPOS-F171: board_config_path 退役不读
    if resolved_root is None:
        resolved_root = (repo_root or REPO_ROOT).resolve()
    else:
        resolved_root = resolved_root.resolve()
    defaults = _runtime_config_defaults(resolved_root)
    connection_endpoints = _load_connection_endpoints(resolved_root)
    transport_env = defaults["transport_token_env"]
    capability_env = defaults["capability_token_env"]
    transport_raw = str(os.environ.get(transport_env) or "")
    capability_raw = str(os.environ.get(capability_env) or "")
    capability = _capability_status(capability_raw)
    queue = get_queue(repo_root=resolved_root)
    records = get_records(repo_root=resolved_root)
    validate = get_validate(repo_root=resolved_root)
    tasks = queue.get("data", {}).get("tasks") if isinstance(queue.get("data"), dict) else []
    tasks = tasks if isinstance(tasks, list) else []
    warnings: list[str] = []
    if defaults["config_error"]:
        warnings.append(f"Workspace config issue: {defaults['config_error']}")
    if connection_endpoints["connection_notice"]:
        warnings.append(connection_endpoints["connection_notice"])
    if not transport_raw:
        warnings.append(f"{transport_env} is not set; MCP transport clients cannot authenticate.")
    if not capability_raw:
        warnings.append(f"{capability_env} is not set; scoped MCP mutation tools will be hidden.")
    warnings.extend(capability["diagnostics"])
    server_location = _get_server_location_info()
    data = {
        "server_location": server_location,
        "workspace": {
            "root": str(resolved_root),
            "config_path": defaults["config_path"],
            "discovery_note": "Board server repo_root / LYBRA_WORKSPACE_ROOT is authoritative for this process.",
            "initialized": queue_root_for(resolved_root).exists(),
        },
        "endpoints": {
            "board": {
                "url": connection_endpoints["board_url"] or f"http://{defaults['board_host']}:{defaults['board_port']}",
                "host": defaults["board_host"],
                "port": defaults["board_port"],
                "loopback": _is_loopback_host(defaults["board_host"]),
            },
            "mcp": {
                "url": connection_endpoints["mcp_rpc_url"] or f"http://{defaults['mcp_host']}:{defaults['mcp_port']}/mcp",
                "sse_url": connection_endpoints["mcp_sse_url"] or f"http://{defaults['mcp_host']}:{defaults['mcp_port']}/sse",
                "host": defaults["mcp_host"],
                "port": defaults["mcp_port"],
                "loopback": _is_loopback_host(defaults["mcp_host"]),
            },
        },
        "agent_setup": {
            "server_command": f"lybra mcp --workspace-root {resolved_root}",
            "transport_token_env": transport_env,
            "capability_token_env": capability_env,
            "authorization_header_ref": f"Bearer ${{{transport_env}}}",
            "transport_token_present": bool(transport_raw),
            "transport_token_fingerprint": _secret_fingerprint(transport_raw),
            "capability_token_present": bool(capability_raw),
            "capability_token_fingerprint": _secret_fingerprint(capability_raw),
            "capability_operations": capability["operations"],
            "capability_expires_at": capability["expires_at"],
            "capability_expires_status": capability["expires_status"],
            "tool_visibility": capability["tool_visibility"],
            "secrets_notice": "Raw tokens are never returned by this route; use environment references and redacted fingerprints only.",
        },
        "loop": _loop_summary(tasks),
        "read_sources": {
            "queue_summary": queue.get("summary"),
            "records_summary": records.get("summary"),
            "validate_summary": validate.get("summary"),
        },
        "writes_enabled": False,
        "execute_allowed": False,
        "deferred_gates": [
            "finalize_writer",
            "accepted_work_unblock",
            "active_lease_writer",
            "delegated",
            "standing",
            "runtime_launcher",
        ],
    }
    return {
        "ok": True,
        "verdict": "WARN" if warnings else "PASS",
        "operation": operation,
        "dry_run": False,
        "actor": None,
        "actor_match": None,
        "timestamp": _utc_now(),
        "data": data,
        "summary": {
            "workspace_initialized": data["workspace"]["initialized"],
            "transport_auth_present": bool(transport_raw),
            "capability_scope_present": bool(capability_raw),
            "task_count": data["loop"]["total"],
            "loop_stages": data["loop"]["counts"],
        },
        "planned_writes": [],
        "planned_moves": [],
        "performed_writes": [],
        "performed_moves": [],
        "warnings": warnings,
        "blocking_reasons": [],
        "needs_owner_reasons": [],
        "owner_confirmation_required": False,
        "owner_confirmation_reasons": [],
        "execute_allowed": False,
        "execute_blocking_reasons": [],
        "dry_run_id": None,
        "dry_run_token": None,
        "dry_run_snapshot_hash": None,
        "dry_run_created_at": None,
        "dry_run_expires_at": None,
        "safety_notice": "Read-only Board runtime status surface. No files are written and no services are started.",
        "errors": [],
    }


def _generate_advisor_prompt_route(params: dict[str, list[str]], *, repo_root: Path | None, home_root: str | Path | None = None,
                                  board_config_path: Path | None = None) -> dict[str, Any]:
    """
    AIPOS-286 FIX-2: Server-side advisor prompt generation with locale support.
    
    GET /api/generate/advisor-prompt?workspace=<index>&locale=<zh|en>
    Returns generated advisor onboarding prompt in the requested language.
    """
    operation = "generate_advisor_prompt"
    locale = _first_param(params, "locale") or "zh"
    if locale not in ("zh", "en"):
        locale = "zh"
    
    resolved_root = _resolve_workspace_root(params, repo_root or REPO_ROOT, home_root)  # AIPOS-F171: board_config_path 退役不读
    if resolved_root is None:
        resolved_root = (repo_root or REPO_ROOT).resolve()
    else:
        resolved_root = resolved_root.resolve()
    
    # Load workspace label(AIPOS-F171 件③: 取 home 根推导的项目名, 原 board_config 静态清单退役)
    workspace_label = "unnamed workspace"
    workspace_param = _first_param(params, "workspace")
    if workspace_param and workspace_param.isdigit():
        workspaces = _board_workspaces(repo_root, home_root)
        idx = int(workspace_param)
        if 0 <= idx < len(workspaces):
            workspace_label = str(workspaces[idx].get("label") or workspace_label)
    
    # AIPOS-F106 件①: 门基址唯一推导口(confirm_client.resolve_gate_base_url → ConnectionResolver.resolve_gate_url, 声明序
    # config.schema identity_resolution.keys.gate_url: 工作区 connection.json mcp.rpc_url → env 兜底 → urls.gate_local); 原本地剥 MCP 路径删除
    from tools.aipos_cli.confirm_client import resolve_gate_base_url

    gate_url = resolve_gate_base_url(workspace_root=resolved_root)
    
    # Get server location
    server_location = _get_server_location_info()
    server_hostname = server_location["hostname"] if server_location else "<server_hostname>"
    server_ip = server_location["ip"] if server_location else "<server_ip>"
    
    # Generate prompt
    try:
        prompt_text = _generate_text(
            "advisor_prompt",
            locale,
            workspace_label=workspace_label,
            workspace_root=str(resolved_root),
            gate_url=gate_url,
            charter_path=str(resolved_root / "governance" / "advisor-charter.md"),
            example_card_path=str(resolved_root / "5_tasks" / "drafts" / "example-task.md"),
            server_hostname=server_hostname,
            server_ip=server_ip,
        )
    except KeyError as e:
        return {
            "ok": False,
            "verdict": "BLOCK",
            "operation": operation,
            "message": str(e),
            "errors": [str(e)],
        }
    
    return {
        "ok": True,
        "verdict": "PASS",
        "operation": operation,
        "data": {
            "prompt": prompt_text,
            "locale": locale,
            "workspace_root": str(resolved_root),
            "gate_url": gate_url,
        },
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


PLANNER_DRAFT_REQUIRED_FIELDS = [
    "draft_id",
    "draft_status",
    "draft_created_by",
    "draft_created_at",
    "draft_source",
    "publish_status",
    "publish_target",
    "requirement_id",
    "orchestration_id",
    "parent_task_id",
    "created_by_planner",
    "planner_agent",
    "planner_agent_instance",
    "planner_model_tier",
    "planner_iteration_id",
    "iteration",
    "subtask_sequence",
    "subtask_type",
    "depends_on",
    "reviewer",
    "audit_by",
    "assigned_to",
    "agent_instance",
    "context_bundle",
    "task_mode",
    "model_tier",
    "output_target",
    "artifact_policy",
    "session_policy",
    "context_isolation",
    "artifact_scope",
    "memory_scope",
    "forum_thread_ref",
]


def _is_missing_metadata(metadata: dict[str, Any], field: str) -> bool:
    return metadata.get(field) in (None, "", [])


OWNER_DECISION_TYPE_KEYWORDS = {
    "architecture": ["architecture", "route", "design", "boundary", "service", "database", "deployment"],
    "scope": ["scope", "expand", "expansion", "out of scope", "requirement"],
    "risk": ["risk", "high-risk", "irreversible", "data loss", "refactor"],
    "security": ["security", "credential", "secret", "permission", "auth", "rbac"],
    "model_tier": ["model", "tier", "l3", "l4", "authority"],
    "authority": ["authority", "permission", "owner", "agent", "role"],
    "audit_boundary": ["audit", "reviewer", "auditor", "self-audit"],
    "publish_finalize": ["publish", "finalize", "commit", "push", "release"],
    "long_term_direction": ["long-term", "direction", "strategy", "workflow", "policy"],
}


def _decision_type_from_text(values: list[Any]) -> str:
    text = " ".join(str(value or "") for value in values).lower()
    for decision_type, keywords in OWNER_DECISION_TYPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return decision_type
    return "owner_review"


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            seen.setdefault(text, None)
    return list(seen.keys())


def _get_owner_decisions_review_route(_params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    requests: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    try:
        needs_owner = get_needs_owner(repo_root=resolved_root)
    except Exception as exc:
        needs_owner = {"data": {"tasks": []}}
        warnings.append(f"Unable to read needs-owner tasks: {str(exc) or exc.__class__.__name__}")
    needs_owner_data = needs_owner.get("data") if isinstance(needs_owner, dict) else {}
    if not isinstance(needs_owner_data, dict):
        needs_owner_data = {}
    for task in needs_owner_data.get("tasks", []) or []:
        metadata = dict(task.get("metadata") or {})
        reasons = _dedupe_strings(list(task.get("needs_owner_reasons", [])) + list(metadata.get("needs_owner_reasons") or []))
        title = metadata.get("title") or task.get("title") or task.get("task_id") or task.get("path")
        source_refs = _dedupe_strings([task.get("path"), metadata.get("forum_thread_ref")])
        requests.append(
            {
                "request_id": f"queue:{task.get('task_id') or task.get('path')}",
                "source": "queue_task",
                "decision_type": _decision_type_from_text([title, *reasons, metadata.get("output_target"), metadata.get("artifact_policy")]),
                "title": title,
                "summary": "; ".join(reasons) or "Task requires Owner review.",
                "severity": "needs_owner",
                "status": "open",
                "related_task_id": task.get("task_id"),
                "related_orchestration_id": metadata.get("orchestration_id"),
                "related_iteration_id": metadata.get("planner_iteration_id"),
                "source_refs": source_refs,
                "timeline_refs": [],
                "owner_decision_required": True,
                "review_only": True,
                "resolution_enabled": False,
            }
        )

    orchestration_root = resolved_root / "5_tasks" / "orchestration"
    if orchestration_root.exists():
        for directory in sorted(orchestration_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            orchestration_id = directory.name
            try:
                timeline = get_orchestration_timeline_preview(orchestration_id=orchestration_id, repo_root=resolved_root)
            except Exception as exc:
                warnings.append(f"Unable to read orchestration timeline {orchestration_id}: {str(exc) or exc.__class__.__name__}")
                continue
            if timeline.get("verdict") == "BLOCK":
                blocking_reasons.extend(str(item) for item in timeline.get("blocking_reasons", []))
            for item in timeline.get("data", {}).get("timeline", []) or []:
                if not item.get("owner_attention_required") and not item.get("blocking"):
                    continue
                raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
                reasons = _dedupe_strings(raw.get("needs_owner_reasons") if isinstance(raw.get("needs_owner_reasons"), list) else [])
                summary = item.get("summary") or "; ".join(reasons) or item.get("title") or "Owner decision requested."
                refs = _dedupe_strings(list(item.get("refs") or []) + [item.get("source_ref")])
                requests.append(
                    {
                        "request_id": f"timeline:{orchestration_id}:{item.get('id') or item.get('timestamp')}",
                        "source": item.get("kind") or "orchestration_timeline",
                        "decision_type": _decision_type_from_text([item.get("title"), summary, *reasons, item.get("severity")]),
                        "title": item.get("title") or item.get("id") or "Timeline decision request",
                        "summary": summary,
                        "severity": item.get("severity") or "needs_owner",
                        "status": "open",
                        "related_task_id": item.get("related_task_id") or raw.get("parent_task_id"),
                        "related_orchestration_id": orchestration_id,
                        "related_iteration_id": item.get("related_iteration_id") or raw.get("iteration_id"),
                        "source_refs": refs,
                        "timeline_refs": [item.get("source_ref")],
                        "owner_decision_required": True,
                        "review_only": True,
                        "resolution_enabled": False,
                    }
                )

    type_counts: dict[str, int] = {}
    for request in requests:
        decision_type = str(request.get("decision_type") or "owner_review")
        type_counts[decision_type] = type_counts.get(decision_type, 0) + 1
    return {
        "ok": not blocking_reasons,
        "verdict": "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if requests else ("WARN" if warnings else "PASS")),
        "operation": "owner_decisions_review",
        "dry_run": True,
        "data": {
            "decision_requests": requests,
            "decision_type_counts": type_counts,
            "writes_enabled": False,
            "review_only": True,
            "controlled_mutation_allowed": False,
            "resolution_enabled": False,
            "mobile_responsive_required": True,
        },
        "summary": {
            "total": len(requests),
            "open": len(requests),
            "by_type": type_counts,
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": [str(item.get("summary")) for item in requests if item.get("summary")],
        "owner_confirmation_required": bool(requests),
        "owner_confirmation_reasons": [str(item.get("summary")) for item in requests if item.get("summary")],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-72 Owner Decision Gate UI is read-only and does not resolve decisions."],
        "dry_run_token": None,
        "safety_notice": "Local Owner decision review route. No files are written and no decisions are resolved.",
        "errors": [],
    }


def _get_planner_drafts_review_route(_params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    drafts_root = resolved_root / "5_tasks" / "drafts"
    drafts: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    base_payload = {
        "drafts_dir": "5_tasks/drafts",
        "writes_enabled": False,
        "review_only": True,
        "controlled_mutation_allowed": False,
        "publish_execute_disabled": True,
        "mobile_responsive_required": True,
    }

    if not drafts_root.exists():
        return {
            "ok": True,
            "verdict": "PASS",
            "operation": "planner_drafts_review",
            "dry_run": True,
            "data": {**base_payload, "drafts": []},
            "summary": {"total": 0, "planner_created_total": 0, "ready": 0, "needs_owner": 0, "blocked": 0, "review": 0},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": [],
            "blocking_reasons": [],
            "needs_owner_reasons": [],
            "owner_confirmation_required": False,
            "owner_confirmation_reasons": [],
            "execute_allowed": False,
            "execute_blocking_reasons": ["AIPOS-71 planner draft review desk is read-only."],
            "dry_run_token": None,
            "safety_notice": "Local planner draft review list route. No files are written.",
            "errors": [],
        }

    for path in sorted(drafts_root.rglob("*.md")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(resolved_root).as_posix()
        try:
            validation = validate_draft_file(resolved_root, rel_path)
            publish_preview = backend_publish_draft(resolved_root, rel_path, dry_run=True)
        except Exception as exc:
            warnings.append(f"Unable to review draft {rel_path}: {str(exc) or exc.__class__.__name__}")
            continue

        metadata = dict(validation.get("frontmatter") or {})
        planner_created = (
            str(metadata.get("draft_source") or "").strip() == "planner"
            or _as_bool(metadata.get("created_by_planner"))
            or "/planner/" in rel_path
        )
        if not planner_created:
            continue

        missing_fields = [field for field in PLANNER_DRAFT_REQUIRED_FIELDS if _is_missing_metadata(metadata, field)]
        publish_status = str(metadata.get("publish_status") or "").strip()
        draft_status = str(metadata.get("draft_status") or "").strip()
        planner_tier = str(metadata.get("planner_model_tier") or "").strip().upper()
        planner_agent = str(metadata.get("planner_agent") or "").strip()
        reviewer = str(metadata.get("reviewer") or "").strip()
        audit_by = str(metadata.get("audit_by") or "").strip()
        owner_gate = draft_status == "needs_owner" or publish_status == "needs_owner" or _as_bool(metadata.get("needs_owner"))
        rejected_or_blocked = draft_status in {"rejected", "superseded", "blocked"} or publish_status in {"rejected", "superseded", "blocked"}
        publish_target_ok = str(metadata.get("publish_target") or metadata.get("draft_publish_target") or "").strip() == queue_state_ref(resolved_root, "pending")
        publish_preview_blocked = str(publish_preview.get("verdict") or "") == "BLOCK"
        planner_separated = bool(planner_agent and reviewer and audit_by and planner_agent != reviewer and planner_agent != audit_by)
        ready = (
            not missing_fields
            and planner_tier in {"L3", "L4"}
            and publish_status == "approved_for_publish"
            and not owner_gate
            and not rejected_or_blocked
            and publish_target_ok
            and planner_separated
            and not publish_preview_blocked
        )
        if rejected_or_blocked or publish_preview_blocked or validation.get("verdict") == "BLOCK":
            review_status = "blocked"
        elif owner_gate or publish_status != "approved_for_publish":
            review_status = "needs_owner"
        elif ready:
            review_status = "ready"
        else:
            review_status = "review"

        drafts.append(
            {
                "task_id": validation.get("task_id"),
                "title": metadata.get("title"),
                "path": validation.get("path") or rel_path,
                "draft_status": draft_status or None,
                "publish_status": publish_status or None,
                "review_status": review_status,
                "planner_created": True,
                "assigned_to": metadata.get("assigned_to"),
                "agent_instance": metadata.get("agent_instance"),
                "task_mode": metadata.get("task_mode"),
                "task_class": metadata.get("task_class"),
                "effective_task_class": str(metadata.get("task_class") or "simple").strip().lower(),
                "complexity_note": metadata.get("complexity_note"),
                "planner_agent": planner_agent or None,
                "planner_agent_instance": metadata.get("planner_agent_instance"),
                "planner_model_tier": planner_tier or None,
                "reviewer": reviewer or None,
                "audit_by": audit_by or None,
                "depends_on": metadata.get("depends_on"),
                "forum_thread_ref": metadata.get("forum_thread_ref"),
                "requirement_id": metadata.get("requirement_id"),
                "orchestration_id": metadata.get("orchestration_id"),
                "parent_task_id": metadata.get("parent_task_id"),
                "publish_target": metadata.get("publish_target") or metadata.get("draft_publish_target"),
                "target_path": publish_preview.get("target_path"),
                "missing_metadata": missing_fields,
                "owner_gate": owner_gate,
                "publish_ready": ready,
                "validation_verdict": validation.get("verdict"),
                "publish_preview_verdict": publish_preview.get("verdict"),
                "warnings": list(validation.get("warnings", [])) + list(publish_preview.get("warnings", [])),
                "blocking_reasons": list(validation.get("blocking_reasons", [])) + list(publish_preview.get("blocking_reasons", [])),
                "review_only": True,
                "controlled_mutation_allowed": False,
                "publish_execute_disabled": True,
            }
        )

    summary = {
        "total": len(drafts),
        "planner_created_total": len(drafts),
        "ready": sum(1 for item in drafts if item.get("review_status") == "ready"),
        "needs_owner": sum(1 for item in drafts if item.get("review_status") == "needs_owner"),
        "blocked": sum(1 for item in drafts if item.get("review_status") == "blocked"),
        "review": sum(1 for item in drafts if item.get("review_status") == "review"),
    }
    return {
        "ok": True,
        "verdict": "WARN" if warnings else "PASS",
        "operation": "planner_drafts_review",
        "dry_run": True,
        "data": {**base_payload, "drafts": drafts},
        "summary": summary,
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": [],
        "owner_confirmation_required": False,
        "owner_confirmation_reasons": [],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-71 planner draft review desk is read-only."],
        "dry_run_token": None,
        "safety_notice": "Local planner draft review list route. No files are written.",
        "errors": [],
    }


def _get_orchestration_summary_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    orchestration_id = _first_param(params, "orchestration_id")
    if not orchestration_id:
        return _selector_error("orchestration_summary_preview", "orchestration_id is required")
    return get_orchestration_summary_preview(orchestration_id=orchestration_id, repo_root=repo_root)


def _get_orchestration_timeline_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    orchestration_id = _first_param(params, "orchestration_id")
    if not orchestration_id:
        return _selector_error("orchestration_timeline_preview", "orchestration_id is required")
    return get_orchestration_timeline_preview(orchestration_id=orchestration_id, repo_root=repo_root)


def _get_planner_loop_mvp_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    orchestration_id = _first_param(params, "orchestration_id")
    actor = _first_param(params, "actor")
    if not orchestration_id:
        return _selector_error("planner_loop_mvp_preview", "orchestration_id is required")
    return get_planner_loop_mvp_preview(orchestration_id=orchestration_id, repo_root=repo_root, actor=actor)


def _get_context_pack_preview_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    task_id = _first_param(params, "task_id")
    path = _first_param(params, "path")
    orchestration_id = _first_param(params, "orchestration_id")
    if sum(bool(value) for value in (task_id, path, orchestration_id)) != 1:
        return _selector_error("context_pack_preview", "Exactly one of task_id, path, or orchestration_id is required")
    return get_context_pack_preview(task_id=task_id, path=path, orchestration_id=orchestration_id, repo_root=repo_root)


def _get_task_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    task_id = _first_param(params, "task_id")
    path = _first_param(params, "path")
    if bool(task_id) == bool(path):
        return _selector_error("get_task", "Exactly one of task_id or path is required")
    return get_task(task_id=task_id, path=path, repo_root=repo_root)


def _get_preview_route(params: dict[str, list[str]], *, repo_root: Path | None) -> dict[str, Any]:
    task_id = _first_param(params, "task_id")
    path = _first_param(params, "path")
    actor = _first_param(params, "actor")
    if not actor:
        return _selector_error("get_preview", "actor is required")
    if bool(task_id) == bool(path):
        return _selector_error("get_preview", "Exactly one of task_id or path is required")
    return get_preview(task_id=task_id, path=path, actor=actor, repo_root=repo_root)


def _get_markdown_source_route(params: dict[str, list[str]], *, repo_root: Path | None, home_root: str | Path | None = None) -> dict[str, Any]:
    # AIPOS-263: md 原文侧栏 — 队列卡 / 记录 md 的只读安全渲染(零依赖、先转义后变换、路径白名单)。
    resolved_root = _resolve_workspace_root(params, repo_root, home_root)
    return get_markdown_source(
        path=_first_param(params, "path"),
        task_id=_first_param(params, "task_id"),
        record_id=_first_param(params, "record_id"),
        repo_root=resolved_root,
    )


def board_readonly_declaration() -> dict[str, Any]:
    """AIPOS-F171 件③(gap #155): verbs.schema board_readonly(看板只读应答 / 允许的鉴权 POST 通道)。缺键 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("verbs").get("board_readonly")
    if (not isinstance(decl, dict) or not isinstance(decl.get("status"), int)
            or not all(isinstance(decl.get(k), str) and decl[k] for k in ("error", "message", "message_en", "next_step"))
            or not isinstance(decl.get("allowed_post_paths"), list)):
        raise SchemaLoadError("verbs.schema.json board_readonly(status / error / message / message_en / next_step / allowed_post_paths)未声明齐")  # i18n-exempt: 声明缺失 fail-closed 错误(SchemaLoadError 消息, 非界面文字)
    return decl


def read_only_response() -> tuple[int, dict[str, Any]]:
    """看板写接口下线后的唯一应答(状态码与文案读 board_readonly 声明); 不调用任何写实现。"""
    decl = board_readonly_declaration()
    return int(decl["status"]), {"ok": False, "verdict": "BLOCK", "error": decl["error"], "message": decl["message"],
                                 "message_en": decl["message_en"], "next_step": decl["next_step"]}


def dispatch_api_request(
    *,
    method: str,
    path: str,
    routes: dict[str, Callable[[dict[str, list[str]]], dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    """API 分派: GET = 只读路由; 其余方法一律看板只读应答(AIPOS-F171 件③: 原 POST 业务路由表与其写实现入口全部下线, 不修鉴权;
    鉴权 / 登录通道由 BoardHandler.do_POST 先行处理, 不经本函数)。"""
    parsed = urlparse(path)
    clean_path = parsed.path
    params = parse_qs(parsed.query, keep_blank_values=True)
    if method != "GET":
        return read_only_response()
    if clean_path in routes:
        return int(HTTPStatus.OK), routes[clean_path](params)
    return (
        int(HTTPStatus.NOT_FOUND),
        {
            "ok": False,
            "verdict": "BLOCK",
            "error": "NOT_FOUND",
            "message": "Route not found",
        },
    )


# ---------------------------------------------------------------------------
# AIPOS-270 — board 鉴权(owner/角色 token 登录 + 会话 cookie)
#
# 红线:零依赖(stdlib http.cookies / secrets / hashlib);token 明文不落日志、不进 cookie。
# 登录仅用「提交 token 的 sha256 指纹」对照 connection.json 里已存的 fingerprint 字段——
# 永不读取/回显/比较 connection.json 的原始 token 字段。会话 = 进程内存态随机 secret,
# 重启失效(重登即可)。
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "board_session"
REMEMBER_COOKIE_NAME = "board_remember"  # F-271-6: 长效 remember token (HMAC 签名)  # i18n-exempt: auth constant

# 无需登录即可访问的路径(登录页 / 鉴权 API 本身 + AIPOS-271 一次性凭据通道)。
# AIPOS-271:OTC mint / 设备码三通道均为公开 —— 这些端点本身用递来的 token 指纹鉴权
# (复用 verify_login_token),不依赖既有会话;原始 token / OTC 值 / 设备码值不落日志。
_AUTH_PUBLIC_PATHS = frozenset({
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/otc/mint",
    "/api/auth/device/code",
    "/api/auth/device/poll",
    "/api/auth/device/approve",
})

# 静态资源后缀(登录页与受保护页都能正常渲染所需):放行,不走鉴权。
# 注意:.html 不在此列——HTML 页面本身是受保护的应用页(由各自路由在登录后提供)。
_STATIC_ASSET_SUFFIXES = frozenset({
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
})


def _token_fingerprint(token: str) -> str:
    """与 connection.json 同算法:sha256 前 12 hex。"""
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _discover_connection_paths(repo_root: Path | None = None, board_config_path: Path | None = None) -> list[Path]:
    """发现用于校验登录 token 的 connection.json。

    AIPOS-349: 首选 board 启动参数 repo_root 自身的 .lybra/connection.json(唯一规范),
    次选远端 agent 侧凭据 ~/.lybra/agent_credentials.json(仅 agent 侧读,工作区操作不回落)。
    不再枚举 .board_config.json 配置的其他工作区(部署路径不得入产品代码;避免跨工作区认证串门)。
    仅返回真实存在的文件,去重。"""
    candidates: list[Path] = []
    # 首选:board 启动时明确声明的工作区
    if repo_root is not None:
        from tools.aipos_cli.service_mode import connection_path as _connection_path  # AIPOS-F106 件④: 定位唯一实现

        candidates.append(_connection_path(repo_root))
    # 次选:远端 agent 侧凭据(改名后,与工作区配置一眼可分)
    candidates.append(Path("~/.lybra/agent_credentials.json").expanduser())
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        try:
            if path.is_file():
                key = str(path.resolve())
                if key not in seen:
                    seen.add(key)
                    result.append(path)
        except OSError:
            continue
    return result


def verify_login_token(
    token: str,
    connection_paths: list[Path] | None = None,
) -> dict[str, Any] | None:
    """校验登录 token:对照 connection.json 的 fingerprint 字段。

    命中返回 {role, is_owner, scopes, token_ref};未命中返回 None。
    **只比较指纹,绝不读取/回显 connection.json 的原始 token 字段**;用 secrets.compare_digest
    做常量时间比较,避免基于时间的指纹探测。任何 IO/解析异常 → 视为不可信 → 不命中(fail-closed)。

    AIPOS-F82 件③: 已退役条目(config.schema token.entry.retirement_fields.retired, 判据唯一实现
    token_resolver.is_token_entry_retired)不参与比对 —— 重签后旧 token 仍留在 connection.json 里留痕, 不得再登录。
    """
    from tools.aipos_cli.token_resolver import is_token_entry_retired

    if not token or not isinstance(token, str):
        return None
    submitted_fp = _token_fingerprint(token)
    paths = connection_paths if connection_paths is not None else _discover_connection_paths()
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tokens = data.get("tokens") if isinstance(data, dict) else None
        if not isinstance(tokens, list):
            continue
        for item in tokens:
            if not isinstance(item, dict) or is_token_entry_retired(item):
                continue
            stored_fp = str(item.get("fingerprint") or "")
            if not stored_fp:
                continue
            if secrets.compare_digest(stored_fp, submitted_fp):
                role = str(item.get("role") or "").strip()
                raw_scopes = item.get("scopes")
                scopes = (
                    [str(s) for s in raw_scopes if str(s).strip()]
                    if isinstance(raw_scopes, list)
                    else []
                )
                return {
                    "role": role,
                    "is_owner": role == "owner",
                    "scopes": scopes,
                    "token_ref": str(item.get("token_ref") or ""),
                }
    return None


class SessionStore:
    """进程内会话表(session_id -> 会话信息)。重启即失效(重新登录)。

    session_id 是 secrets.token_urlsafe 生成的随机不透明 secret;**原始 token 永不入库、不入 cookie**。
    线程安全(ThreadingHTTPServer 多线程处理)。"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, *, role: str, scopes: list[str], token_ref: str = "", login_method: str = "") -> str:
        """AIPOS-F172: 另记登录方式 login_method(token / otc / device_code / remember; 信封签发来源写明会话 / 设备标识用)。"""
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = {
                "role": role,
                "is_owner": role == "owner",
                "scopes": list(scopes),
                "token_ref": token_ref,
                "login_method": login_method,
                "created_at": _utc_now(),
            }
        return session_id

    def get(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        with self._lock:
            entry = self._sessions.get(session_id)
            return dict(entry) if entry is not None else None

    def revoke(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


def parse_session_cookie(cookie_header: str | None) -> str | None:
    """从 Cookie 头解析出 board_session 的值(不透明 secret);无/畸形 → None。"""
    if not cookie_header:
        return None
    try:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if morsel is None:
        return None
    value = (morsel.value or "").strip()
    return value or None


def parse_remember_cookie(cookie_header: str | None) -> str | None:
    """从 Cookie 头解析 board_remember 完整 token 值(需 URL 解码)。返回 token 字符串或 None。"""
    import urllib.parse
    if not cookie_header:
        return None
    try:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(REMEMBER_COOKIE_NAME)
    if morsel is None:
        return None
    value = (morsel.value or "").strip()
    if not value:
        return None
    # URL 解码
    try:
        return urllib.parse.unquote(value)
    except Exception:
        return None


def build_session_cookie_header(
    session_id: str,
    remember: bool = False,
    remember_secret: str = "",
    session_info: dict[str, Any] | None = None,
) -> list[str]:
    """构造签发会话的 Set-Cookie 值列表(可能包含两枚 cookie):HttpOnly;Path=/;SameSite=Lax。
    
    FIX-4: 返回列表以支持多个独立 Set-Cookie header(HTTP 规范要求每枚 cookie 独立 header)。
    F-271-6: remember=True 时另发第二枚 HttpOnly cookie = HMAC 签名 remember token (30 天),
    载荷含 session_id:role:scopes:token_ref:signature。会话 cookie 无效(serve 重启)而
    remember token 验签有效 → 自动重建会话。

    不设 Secure:board 常以明文 HTTP 跑在 loopback / 0.0.0.0(收账注记),Secure 会使 cookie 在
    http:// 下被丢弃;Owner 可在前面套 TLS 终端时再开 Secure。
    
    Args:
        session_id: 会话 ID
        remember: 是否启用长效 cookie
        remember_secret: remember token 签名密钥
        session_info: 会话信息 {"role": str, "scopes": list[str], "token_ref": str}
    
    Returns:
        Set-Cookie header 值的列表(1 或 2 个元素)
    """
    from web.board.auth_otc import REMEMBER_DAYS, sign_remember_token
    import urllib.parse
    
    cookies = []
    
    # 会话 cookie (短期或长期，取决于 remember)
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = session_id
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["httponly"] = True
    morsel["path"] = "/"
    morsel["samesite"] = "Lax"
    if remember:
        morsel["max-age"] = str(REMEMBER_DAYS * 24 * 3600)
    cookies.append(cookie.output(header="").strip())
    
    # F-271-6: remember=True 时另发 remember token (携带完整会话信息 + HMAC 签名)
    # URL 编码以避免冒号等特殊字符导致 SimpleCookie 解析失败
    if remember and remember_secret and session_info:
        role = session_info.get("role", "")
        scopes = session_info.get("scopes", [])
        token_ref = session_info.get("token_ref", "")
        remember_token = sign_remember_token(remember_secret, session_id, role, scopes, token_ref)
        # URL 编码 remember token
        encoded_token = urllib.parse.quote(remember_token, safe="")
        remember_cookie = SimpleCookie()
        remember_cookie[REMEMBER_COOKIE_NAME] = encoded_token
        remember_morsel = remember_cookie[REMEMBER_COOKIE_NAME]
        remember_morsel["httponly"] = True
        remember_morsel["path"] = "/"
        remember_morsel["samesite"] = "Lax"
        remember_morsel["max-age"] = str(REMEMBER_DAYS * 24 * 3600)
        cookies.append(remember_cookie.output(header="").strip())
    
    return cookies


def build_clear_cookie_header() -> list[str]:
    """构造清除会话的 Set-Cookie 值列表(Max-Age=0 + 过期)。
    
    FIX-4: 返回列表以支持多个独立 Set-Cookie header。
    F-271-6: 清除两枚 cookie (session + remember)。
    
    Returns:
        Set-Cookie header 值的列表(2 个元素)
    """
    cookies = []
    
    # 清除会话 cookie
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = ""
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["httponly"] = True
    morsel["path"] = "/"
    morsel["samesite"] = "Lax"
    morsel["max-age"] = "0"
    morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    cookies.append(cookie.output(header="").strip())
    
    # F-271-6: 同时清除 remember cookie
    remember_cookie = SimpleCookie()
    remember_cookie[REMEMBER_COOKIE_NAME] = ""
    remember_morsel = remember_cookie[REMEMBER_COOKIE_NAME]
    remember_morsel["httponly"] = True
    remember_morsel["path"] = "/"
    remember_morsel["samesite"] = "Lax"
    remember_morsel["max-age"] = "0"
    remember_morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    cookies.append(remember_cookie.output(header="").strip())
    
    return cookies


def is_public_path(clean_path: str) -> bool:
    """登录页 / 鉴权 API 本身:无需登录。"""
    return clean_path in _AUTH_PUBLIC_PATHS


def is_static_asset_path(clean_path: str) -> bool:
    """受保护页与登录页渲染所需的静态资源(css/js/img…):放行。

    判据:解析到 STATIC_DIR 内真实存在的文件,且后缀属于资源集合(排 .html)。含路径穿越防护。"""
    if not clean_path or clean_path in {"/", "/."}:
        return False
    try:
        candidate = (STATIC_DIR / clean_path.lstrip("/")).resolve()
    except OSError:
        return False
    static_root = STATIC_DIR.resolve()
    try:
        if not candidate.exists() or not candidate.is_file():
            return False
        candidate.relative_to(static_root)
    except ValueError:
        return False
    return candidate.suffix.lower() in _STATIC_ASSET_SUFFIXES


def is_authorized(
    clean_path: str,
    method: str,
    cookie_header: str | None,
    session_store: SessionStore,
    remember_secret: str = "",
) -> bool:
    """鉴权闸门:True=放行,False=需 302 到登录页。

    F-271-6: 会话 cookie 无效(serve 重启)而 remember token 验签有效 → 自动重建会话(无感)。
    顺序:公开路径 → 静态资源(GET) → 会话 cookie 校验 → remember token 自动续登。
    cookie 篡改/未知 → False(拒)。"""
    if is_public_path(clean_path):
        return True
    if method.upper() == "GET" and is_static_asset_path(clean_path):
        return True
    
    session_id = parse_session_cookie(cookie_header)
    existing_session = session_store.get(session_id)
    if existing_session is not None:
        return True
    
    # F-271-6: 会话失效，尝试 remember token 自动续登
    if not remember_secret:
        return False
    
    remember_token = parse_remember_cookie(cookie_header)
    if remember_token is None:
        return False
    
    from web.board.auth_otc import verify_remember_token
    session_info = verify_remember_token(remember_secret, remember_token)
    if session_info is None:
        return False
    
    # 验签通过，从 remember token 中恢复会话信息并重建会话
    # 注意：这里重建的 session_id 应该与 remember token 中携带的一致
    remembered_session_id = session_info["session_id"]
    role = session_info["role"]
    scopes = session_info["scopes"]
    token_ref = session_info.get("token_ref", "")
    
    # 重建会话（使用原 session_id）
    with session_store._lock:
        session_store._sessions[remembered_session_id] = {
            "role": role,
            "is_owner": role == "owner",
            "scopes": scopes,
            "token_ref": token_ref,
            "login_method": "remember",
            "created_at": _utc_now(),
        }
    
    return True


def _append_perf_line(log_path: Path, method: str, path: str, started: float) -> None:
    """AIPOS-F177 item 3 (optional board perf log, separate from the board audit log): one JSON line per request with the
    server-side time and the frontmatter read-cache counters. Only the path is logged, never the query string (it may
    carry one-time login codes). AIPOS-F245 item 4: plus the truth-fingerprinted result cache counters (task_loader.truth_cache_stats)."""
    import time

    from tools.aipos_cli.frontmatter import frontmatter_memo_stats
    from tools.aipos_cli.task_loader import truth_cache_stats

    record = {"at": _utc_now(), "method": method, "path": path,
              "server_ms": round((time.perf_counter() - started) * 1000.0, 1), "frontmatter_cache": frontmatter_memo_stats(),
              "truth_cache": truth_cache_stats()}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_handler(
    repo_root: Path | None = None,
    *,
    board_config_path: Path | None = None,
    home_root: str | Path | None = None,
    session_store: SessionStore | None = None,
    connection_paths: list[Path] | None = None,
    otc_store: OTCStore | None = None,
    device_store: DeviceCodeStore | None = None,
    auth_log_path: Path | None = None,
    preview_store: PreviewTokenStore | None = None,
    mint_connection_json: Path | None = None,
    board_audit_log_path: Path | None = None,
    perf_log_path: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    routes = _api_routes(repo_root, board_config_path, home_root=home_root)  # AIPOS-F171: 只读 GET 路由; POST 只有鉴权通道 + 三个 Owner 写例外
    sessions = session_store if session_store is not None else SessionStore()
    # AIPOS-272 FIX-2: 未显式注入时，按 repo_root 发现 connection.json（首选工作区自身）
    conn_paths = connection_paths if connection_paths is not None else _discover_connection_paths(repo_root=repo_root)
    otc = otc_store if otc_store is not None else OTCStore()
    device = device_store if device_store is not None else DeviceCodeStore()
    # auth-log 落点:显式注入优先(测试),否则按 repo_root 解析(repo_root 为 None 则不落盘)。
    log_path = auth_log_path if auth_log_path is not None else resolve_auth_log_path(repo_root)
    # F-271-3: 长效 cookie secret(持久化到 .lybra/remember_secret)
    from web.board.auth_otc import load_or_create_remember_secret
    remember_secret = load_or_create_remember_secret(repo_root)
    # AIPOS-F172 写例外①(信封签发): 预演令牌表(TTL 读声明)、签发凭据库(= 看板登录凭据库: 服务进程工作区 .lybra/connection.json, 只在服务端经
    # envelope_mint → load_role_token 读, 不进应答)、看板审计日志落点(服务进程工作区 .lybra/<声明 audit_log_file>)。显式注入优先(测试)。
    from tools.aipos_cli.envelope_board import board_envelopes_declaration, resolve_board_audit_log_path

    envelope_decl = board_envelopes_declaration()
    previews = preview_store if preview_store is not None else PreviewTokenStore(ttl_seconds=int(envelope_decl["preview_ttl_seconds"]))
    if mint_connection_json is not None:
        mint_conn = mint_connection_json
    elif repo_root is not None:
        from tools.aipos_cli.service_mode import connection_path as _connection_path

        mint_conn = _connection_path(repo_root)
    else:
        mint_conn = None
    board_audit = board_audit_log_path if board_audit_log_path is not None else resolve_board_audit_log_path(repo_root)
    # AIPOS-F174: 看板只读页(总览 / 路线图与验收 / 项目接入)路径读声明 verbs.schema board_readonly_pages.pages; 仅 GET(写方法照旧只读应答)
    from tools.aipos_cli.board_pages import pages_declaration

    readonly_pages = {str(url): page for page, url in pages_declaration()["pages"].items()}
    # AIPOS-F175: artifact thumbnail route (GET only; path declared in verbs.schema board_readonly_pages.artifact_route)
    artifact_route = str(pages_declaration()["artifact_route"]["path"])
    # AIPOS-F176 write exception 2 (project upgrade): page + single write route read from verbs.schema verbs.lybra_project_upgrade.board
    from tools.aipos_cli.project_upgrade import board_declaration as upgrade_board_declaration

    upgrade_decl = upgrade_board_declaration()
    # AIPOS-F226 write exception 3 (inbox one-click ruling): single write route read from verbs.schema lybra_owner_inbox.board.decide
    from tools.aipos_cli.owner_inbox import board_decide_declaration

    inbox_decide_decl = board_decide_declaration()
    # AIPOS-F198: workstation page (read-only, GET only; path read from verbs.schema verbs.lybra_project_workstation_plan.board)
    from tools.aipos_cli.workstation_plan import declaration as workstation_plan_declaration

    workstation_page = str(workstation_plan_declaration()["board"]["page_path"])

    class BoardHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        _sessions = sessions
        _conn_paths = conn_paths
        _otc = otc
        _device = device
        _auth_log_path = log_path
        _remember_secret = remember_secret
        _previews = previews
        _mint_conn = mint_conn
        _board_audit = board_audit
        _envelope_paths = {k: str(envelope_decl[k]) for k in ("page_path", "api_path", "write_path")}
        _readonly_pages = readonly_pages
        _root_redirect = str(pages_declaration()["pages"][pages_declaration()["root_redirect"]])
        _artifact_route = artifact_route
        _perf_log = Path(perf_log_path) if perf_log_path is not None else None  # AIPOS-F177: optional, off by default
        _upgrade_paths = {k: str(upgrade_decl[k]) for k in ("page_path", "write_path")}
        _inbox_decide_path = str(inbox_decide_decl["write_path"])
        _workstation_page = workstation_page

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path) -> None:
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _content_type(path))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._send_json(HTTPStatus.NOT_FOUND, dispatch_api_request(method="GET", path="/missing", routes={})[1])

        def _method_not_allowed(self) -> None:
            """AIPOS-F171 件③: 非鉴权通道的写请求一律看板只读应答(状态码与文案读 verbs.schema board_readonly)。"""
            status, payload = read_only_response()
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 0:
                self.rfile.read(length)  # 读掉请求体(keep-alive 连接不残留), 不解析不使用
            self._send_json(HTTPStatus(status), payload)

        # ---- AIPOS-270 鉴权辅助 ----
        def _redirect(self, status: int, location: str, *, set_cookie: str | list[str] | None = None) -> None:
            """发送重定向响应。
            
            FIX-4: set_cookie 支持列表,每个元素发送独立的 Set-Cookie header。
            """
            self.send_response(status)
            self.send_header("Location", location)
            if set_cookie:
                if isinstance(set_cookie, list):
                    for cookie in set_cookie:
                        self.send_header("Set-Cookie", cookie)
                else:
                    self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _redirect_to_login(self) -> None:
            """未登录:302 到登录页。"""
            self._redirect(int(HTTPStatus.FOUND), "/login")

        def _auth_gate(self, method: str) -> bool:
            """鉴权闸门:放行返回 True;未通过则发 302 并返回 False。"""
            clean = self.path.split("?", 1)[0]
            if is_authorized(clean, method, self.headers.get("Cookie"), self._sessions, self._remember_secret):
                return True
            self._redirect_to_login()
            return False

        def _send_json_cookie(
            self, status: HTTPStatus, payload: dict[str, Any], *, set_cookie: str | list[str] | None = None
        ) -> None:
            """发送 JSON 响应并设置 cookie。
            
            FIX-4: set_cookie 支持列表,每个元素发送独立的 Set-Cookie header。
            """
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if set_cookie:
                if isinstance(set_cookie, list):
                    for cookie in set_cookie:
                        self.send_header("Set-Cookie", cookie)
                else:
                    self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _source_ip(self) -> str:
            """请求来源 IP(仅用于 auth-log;不用于鉴权决策)。"""
            try:
                return str(self.client_address[0]) if self.client_address else "unknown"
            except (IndexError, TypeError):
                return "unknown"

        def _read_json_body(self) -> dict[str, Any]:
            """读 JSON POST body(畸形/空 → {})。供 AIPOS-271 鉴权通道复用。"""
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                return {}
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return body if isinstance(body, dict) else {}

        def _issue_session(self, info: dict[str, Any], *, method: str) -> str:
            """建会话 + 写登录留痕。method ∈ {token, otc, device_code}。

            红线:原始 token / OTC 值 / 设备码值均不写日志;auth-log 只记
            时间/方式/角色/token_ref/来源 IP。留痕失败不影响登录(尽力留痕)。"""
            session_id = self._sessions.create(
                role=info["role"], scopes=info["scopes"], token_ref=info.get("token_ref", ""), login_method=method
            )
            append_auth_log(
                self._auth_log_path,
                method=method,
                role=info["role"],
                token_ref=info.get("token_ref", ""),
                source_ip=self._source_ip(),
            )
            return session_id

        def _handle_auth_status(self) -> None:
            sid = parse_session_cookie(self.headers.get("Cookie"))
            info = self._sessions.get(sid)
            if info is None:
                self._send_json(HTTPStatus.OK, {"ok": True, "authenticated": False})
                return
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "authenticated": True,
                "role": info.get("role"),
                "is_owner": bool(info.get("is_owner")),
            })

        def _handle_login(self) -> None:
            """POST /api/auth/login:校验 token(仅指纹)→ 签发会话 cookie。

            红线:原始 token 不入日志、不入 cookie、不进会话表;失败 401。"""
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            token = ""
            try:
                body = json.loads(raw.decode("utf-8") or "{}") if raw else {}
                if isinstance(body, dict):
                    token = str(body.get("token") or "")
            except (json.JSONDecodeError, UnicodeDecodeError):
                token = ""
            info = verify_login_token(token, self._conn_paths)
            if info is None:
                self._send_json(HTTPStatus.UNAUTHORIZED, {
                    "ok": False,
                    "error": "INVALID_TOKEN",
                    "message": "Token 无效或未匹配到任何角色。",  # i18n-exempt: auth error
                })
                return
            session_id = self._issue_session(info, method="token")
            self._send_json_cookie(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "role": info["role"],
                    "is_owner": info["is_owner"],
                    "redirect": "/",
                },
                set_cookie=build_session_cookie_header(session_id),
            )

        def _handle_logout(self) -> None:
            """POST /api/auth/logout:撤销会话 + 清 cookie + 303 回登录页。"""
            sid = parse_session_cookie(self.headers.get("Cookie"))
            self._sessions.revoke(sid)
            self._redirect(
                int(HTTPStatus.SEE_OTHER), "/login", set_cookie=build_clear_cookie_header()
            )

        # ---- AIPOS-271 一次性凭据通道(本机 OTC + 跨机设备码)----
        # 红线:原始 token / OTC 值 / 设备码值一律不落日志。auth-log 只在换会话 cookie
        # 成功时追加一条(method/role/token_ref/IP),由 _issue_session 统一处理。

        def _handle_otc_mint(self) -> None:
            """POST /api/auth/otc/mint:CLI 携 token(指纹校验)→ 铸 OTC + 返回换票链接。

            浏览器随后 GET ``/login?otc=…`` redeem 换 cookie。原始 token 不进日志/响应。"""
            token = self._read_json_body().get("token", "")
            info = verify_login_token(token, self._conn_paths)
            if info is None:
                self._send_json(HTTPStatus.UNAUTHORIZED, {
                    "ok": False, "error": "INVALID_TOKEN",
                    "message": "Token 无效或未匹配到任何角色。",  # i18n-exempt: auth error
                })
                return
            otc = self._otc.mint(
                role=info["role"], scopes=info["scopes"], token_ref=info.get("token_ref", "")
            )
            self._send_json(HTTPStatus.OK, {
                "ok": True, "otc": otc, "expires_in": OTC_TTL_SECONDS,
                "login_url": f"/login?otc={otc}",
            })

        def _handle_device_code(self) -> None:
            """POST /api/auth/device/code:跨机浏览器申请一个 pending 6 位设备码。"""
            code = self._device.issue()
            self._send_json(HTTPStatus.OK, {
                "ok": True, "code": code, "expires_in": DEVICE_CODE_TTL_SECONDS,
            })

        def _handle_device_poll(self) -> None:
            """POST /api/auth/device/poll:浏览器轮询设备码状态。

            approved 且未过期 → 单次取出、换会话 cookie(method=device_code 留痕)。
            F-271-6: 支持 remember 参数(长效 cookie + remember token)。"""
            body = self._read_json_body()
            code = str(body.get("code", "") or "")
            remember = bool(body.get("remember", False))
            res = self._device.poll(code)
            status = res.get("status")
            if status == "approved":
                session_id = self._issue_session(res, method="device_code")
                session_info = {
                    "role": res.get("role", ""),
                    "scopes": res.get("scopes", []),
                    "token_ref": res.get("token_ref", ""),
                }
                self._send_json_cookie(
                    HTTPStatus.OK,
                    {"ok": True, "status": "approved", "role": res.get("role"), "redirect": "/"},
                    set_cookie=build_session_cookie_header(
                        session_id,
                        remember=remember,
                        remember_secret=self._remember_secret,
                        session_info=session_info,
                    ),
                )
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "status": status})

        def _handle_device_approve(self) -> None:
            """POST /api/auth/device/approve:gate 机 CLI 携 token 确认一个 pending 设备码。"""
            body = self._read_json_body()
            code = str(body.get("code", "") or "")
            token = str(body.get("token", "") or "")
            info = verify_login_token(token, self._conn_paths)
            if info is None:
                self._send_json(HTTPStatus.UNAUTHORIZED, {
                    "ok": False, "error": "INVALID_TOKEN",
                    "message": "Token 无效或未匹配到任何角色。",  # i18n-exempt: auth error
                })
                return
            approved = self._device.approve(
                code, role=info["role"], scopes=info["scopes"], token_ref=info.get("token_ref", "")
            )
            self._send_json(HTTPStatus.OK, {"ok": approved, "status": "approved" if approved else "not_approved"})

        # ---- AIPOS-F172 信封页 + Owner 签发写例外 ----
        def _current_session(self) -> tuple[str | None, dict[str, Any] | None]:
            """当前请求的会话(会话 cookie; 失效时 remember token 验签所重建的会话, 与 is_authorized 同一判据)。"""
            cookie_header = self.headers.get("Cookie")
            sid = parse_session_cookie(cookie_header)
            info = self._sessions.get(sid)
            if info is None and self._remember_secret:
                from web.board.auth_otc import verify_remember_token

                token = parse_remember_cookie(cookie_header)
                restored = verify_remember_token(self._remember_secret, token) if token else None
                if restored is not None:
                    sid = restored["session_id"]
                    info = self._sessions.get(sid)
            return (sid, info) if info is not None else (None, None)

        def _send_html(self, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_envelopes_page(self) -> None:
            """GET 信封页(服务端渲染): 全部登录会话可看; 只有 Owner 会话渲染表单(owner=True)。
            AIPOS-F180: F174 page skeleton + authorization requests on top (web/board/readonly_pages.render_envelopes)."""
            from web.board.readonly_pages import render_envelopes

            _sid, info = self._current_session()
            self._send_html(render_envelopes(home_root, parse_qs(urlparse(self.path).query), owner=bool(info and info.get("role") == "owner")))

        def _handle_owner_inbox_page(self) -> None:
            """AIPOS-F168 inbox page; AIPOS-F180: F174 page skeleton + authorization request cards (Owner session = sign buttons)."""
            from web.board.readonly_pages import render_owner_inbox

            _sid, info = self._current_session()
            self._send_html(render_owner_inbox(home_root, parse_qs(urlparse(self.path).query), owner=bool(info and info.get("role") == "owner")))

        def _handle_envelopes_api(self) -> None:
            from tools.aipos_cli.envelope_board import collect_envelopes
            from tools.schema_loader import SchemaLoadError

            try:
                self._send_json(HTTPStatus.OK, {"ok": True, "data": collect_envelopes(home_root)})
            except (OSError, ValueError, SchemaLoadError) as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "ENVELOPES_UNREADABLE",
                                                                   "message": f"{type(exc).__name__}: {exc}"})

        def _handle_envelope_mint(self) -> None:
            """POST 写例外①(唯一写路由): 全部判定在 envelope_board.handle_mint_request(Owner 会话 / workspace / 预演令牌 / 审计日志)。
            会话 actor 来自登录会话(role / token_ref / 会话指纹 / 登录方式 / 来源 IP), 不取请求体。"""
            from tools.aipos_cli.envelope_board import handle_mint_request, session_ref

            sid, info = self._current_session()
            session = None
            if info is not None and sid:
                session = {"role": info.get("role"), "token_ref": info.get("token_ref") or "", "session_ref": session_ref(sid),
                           "login_method": info.get("login_method") or "", "source_ip": self._source_ip()}
            status, payload = handle_mint_request(
                self._read_json_body(), session=session, home_root=home_root, preview_store=self._previews,
                connection_json=self._mint_conn, audit_log_path=self._board_audit, origin=self.headers.get("Origin"),
                host=self.headers.get("Host"), content_type=self.headers.get("Content-Type"))
            self._send_json(HTTPStatus(status), payload)

        # ---- AIPOS-F176 project upgrade page + Owner write exception 2 (same F172 framework) ----
        def _handle_upgrade_page(self) -> None:
            """GET upgrade page (server-rendered): every logged-in session sees it; only Owner sessions get the preview button.
            AIPOS-F181: on the board skeleton (web/board/readonly_pages.render_upgrade)."""
            from web.board.readonly_pages import render_upgrade

            _sid, info = self._current_session()
            self._send_html(render_upgrade(home_root, parse_qs(urlparse(self.path).query), owner=bool(info and info.get("role") == "owner")))

        def _handle_upgrade(self) -> None:
            """POST write exception 2 (single write route): every check lives in project_upgrade.handle_upgrade_request
            -> envelope_board.handle_owner_write (Owner session / workspace / preview token / audit log); actor comes from the session."""
            from tools.aipos_cli.envelope_board import session_ref
            from tools.aipos_cli.project_upgrade import handle_upgrade_request

            sid, info = self._current_session()
            session = None
            if info is not None and sid:
                session = {"role": info.get("role"), "token_ref": info.get("token_ref") or "", "session_ref": session_ref(sid),
                           "login_method": info.get("login_method") or "", "source_ip": self._source_ip()}
            status, payload = handle_upgrade_request(
                self._read_json_body(), session=session, home_root=home_root, preview_store=self._previews,
                audit_log_path=self._board_audit, origin=self.headers.get("Origin"), host=self.headers.get("Host"),
                content_type=self.headers.get("Content-Type"))
            self._send_json(HTTPStatus(status), payload)

        # ---- AIPOS-F226 inbox one-click ruling: Owner write exception 3 (same F172 framework) ----
        def _handle_inbox_decide(self) -> None:
            """POST write exception 3 (single write route): every check lives in owner_inbox.handle_board_decide
            -> envelope_board.handle_owner_write (Owner session / workspace / preview token / audit log); the ruling itself is
            owner_inbox.decide (same implementation as `lybra owner decide`); actor and provenance come from the session."""
            from tools.aipos_cli.envelope_board import session_ref
            from tools.aipos_cli.owner_inbox import handle_board_decide

            sid, info = self._current_session()
            session = None
            if info is not None and sid:
                session = {"role": info.get("role"), "token_ref": info.get("token_ref") or "", "session_ref": session_ref(sid),
                           "login_method": info.get("login_method") or "", "source_ip": self._source_ip()}
            status, payload = handle_board_decide(
                self._read_json_body(), session=session, home_root=home_root, preview_store=self._previews,
                audit_log_path=self._board_audit, origin=self.headers.get("Origin"), host=self.headers.get("Host"),
                content_type=self.headers.get("Content-Type"))
            self._send_json(HTTPStatus(status), payload)

        def do_GET(self) -> None:  # noqa: N802
            if self._perf_log is None:
                self._route_get()
                return
            import time

            started = time.perf_counter()
            try:
                self._route_get()
            finally:
                _append_perf_line(self._perf_log, "GET", self.path.split("?", 1)[0], started)

        def _route_get(self) -> None:
            # AIPOS-270 鉴权闸门:未登录 302 到 /login(静态资源与公开路径放行)。
            if not self._auth_gate("GET"):
                return
            path = self.path.split("?", 1)[0]

            # 公开路由:登录页 / 会话状态(闸门已放行,这里分发)。
            if path == "/login":
                # AIPOS-271:本机无感 —— ``/login?otc=…`` 换会话 cookie 后直跳看板。
                qs = parse_qs(urlparse(self.path).query)
                otc_vals = qs.get("otc", [])
                if otc_vals:
                    info = self._otc.redeem(otc_vals[0])
                    if info is not None:
                        session_id = self._issue_session(info, method="otc")
                        # OTC 登录不支持 remember（只有设备码登录支持）
                        self._redirect(
                            int(HTTPStatus.FOUND), "/",
                            set_cookie=build_session_cookie_header(session_id),
                        )
                        return
                    # OTC 无效/过期/已用:回登录页并带错误标志供前端提示(不复用单次票)。
                    self._redirect(int(HTTPStatus.FOUND), "/login?otc_err=1")
                    return
                self._send_file(STATIC_DIR / "login.html")
                return
            if path == "/api/auth/status":
                self._handle_auth_status()
                return

            if path in routes:
                _status, result = dispatch_api_request(method="GET", path=self.path, routes=routes)
                self._send_json(HTTPStatus.OK, result)
                return

            # AIPOS-F168 件①: Owner 待决页(服务端渲染, 与 /api/owner/inbox 同一数据; 文案读 verbs.schema lybra_owner_inbox)
            if path == "/owner/inbox":
                self._handle_owner_inbox_page()
                return

            # AIPOS-F172: 信封页(服务端渲染)+ 只读数据
            if path == self._envelope_paths["page_path"]:
                self._handle_envelopes_page()
                return
            if path == self._envelope_paths["api_path"]:
                self._handle_envelopes_api()
                return

            # AIPOS-F176: project upgrade page (server-rendered)
            if path == self._upgrade_paths["page_path"]:
                self._handle_upgrade_page()
                return

            # AIPOS-F198: workstation page (server-rendered, read-only; the board writes nothing, the advisor runs product commands)
            if path == self._workstation_page:
                from web.board.readonly_pages import render_workstation

                self._send_html(render_workstation(home_root, parse_qs(urlparse(self.path).query)))
                return

            # AIPOS-F174: 看板只读页(服务端渲染; 数据 tools/aipos_cli/board_pages 只调既有唯一推导, 渲染 web/board/readonly_pages)
            if path in self._readonly_pages:
                from web.board.readonly_pages import render_page

                _sid, info = self._current_session()  # AIPOS-F248: Owner sessions get the home one-click buttons (checks stay on the server)
                self._send_html(render_page(self._readonly_pages[path], home_root, parse_qs(urlparse(self.path).query),
                                            owner=bool(info and info.get("role") == "owner")))
                return

            # AIPOS-F175: card artifact image (declared on the card, inside its artifact dir, image suffix; anything else = 404)
            if path == self._artifact_route:
                from tools.aipos_cli.board_pages import artifact_for_route

                found = artifact_for_route(home_root, parse_qs(urlparse(self.path).query))
                if found is None:
                    self._not_found()
                    return
                body = found["path"].read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", found["content_type"])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(body)
                return

            # AIPOS-251: Overview page as new root; AIPOS-F181: the root redirects to the read-only overview (one skeleton for the
            # whole board; declared board_readonly_pages.root_redirect). The former home stays reachable at /overview.html.
            if path == "/":
                self._redirect(int(HTTPStatus.FOUND), self._root_redirect)
                return
            
            # AIPOS-252: Workspace-specific detail view (Owner-friendly)
            if path.startswith("/workspace/"):
                self._send_file(STATIC_DIR / "project-detail.html")
                return

            # Legacy single-workspace direct access (now debug view)
            if path == "/index.html":
                self._send_file(STATIC_DIR / "index.html")
                return

            static_path = (STATIC_DIR / path.lstrip("/")).resolve()
            if static_path.exists() and static_path.is_file() and STATIC_DIR.resolve() in static_path.parents:
                self._send_file(static_path)
                return

            self._not_found()

        def do_POST(self) -> None:  # noqa: N802
            # AIPOS-270 鉴权闸门:登录/登出本身为公开路径,其余未登录 302 到 /login。
            if not self._auth_gate("POST"):
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/auth/login":
                self._handle_login()
                return
            if path == "/api/auth/logout":
                self._handle_logout()
                return
            # AIPOS-271 一次性凭据通道(均为公开路径,闸门已放行)。
            if path == "/api/auth/otc/mint":
                self._handle_otc_mint()
                return
            if path == "/api/auth/device/code":
                self._handle_device_code()
                return
            if path == "/api/auth/device/poll":
                self._handle_device_poll()
                return
            if path == "/api/auth/device/approve":
                self._handle_device_approve()
                return
            # AIPOS-F172 写例外①: Owner 信封签发(声明 verbs.schema board_owner_envelopes.write_path); 其余写请求照旧只读应答
            if path == self._envelope_paths["write_path"]:
                self._handle_envelope_mint()
                return
            # AIPOS-F176 write exception 2: Owner project upgrade (declared verbs.lybra_project_upgrade.board.write_path)
            if path == self._upgrade_paths["write_path"]:
                self._handle_upgrade()
                return
            # AIPOS-F226 write exception 3: Owner inbox one-click ruling (declared verbs.lybra_owner_inbox.board.decide.write_path)
            if path == self._inbox_decide_path:
                self._handle_inbox_decide()
                return
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            if self._auth_gate("PUT"):
                self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            if self._auth_gate("PATCH"):
                self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            if self._auth_gate("DELETE"):
                self._method_not_allowed()

        def log_message(self, format: str, *args: object) -> None:
            return

    return BoardHandler


def prewarm_read_caches(repo_root: Path | None = None, home_root: str | Path | None = None) -> dict[str, Any]:
    """AIPOS-F229: fill the process read caches (frontmatter / JSON per-file cache, records group cache) once at board start by
    running the two heaviest read-only derivations the Owner opens first (the overview page data board_pages.collect_overview,
    which includes the inbox, and the legacy /api/overview get_overview). Read-only, writes nothing; a freshly (re)started board
    otherwise pays the whole cold parse (~10k record / card files) on the first visit. A failure is reported (stderr + result),
    never swallowed, and does not stop the board (the pages derive again on request and show their own errors).
    Returns {ok, seconds, error}."""
    import time as _time

    from tools.aipos_cli.board_pages import collect_overview

    started = _time.perf_counter()
    try:
        collect_overview(home_root)
        get_overview(repo_root=repo_root, home_root=home_root)
    except Exception as exc:  # noqa: BLE001 - reported below, the board keeps serving (pages derive again on request)
        error = f"{type(exc).__name__}: {exc}"
        print(f"Warning: board read-cache prewarm failed (pages derive on request): {error}", file=sys.stderr, flush=True)
        return {"ok": False, "seconds": round(_time.perf_counter() - started, 3), "error": error}
    seconds = round(_time.perf_counter() - started, 3)
    print(f"AIPOS board read caches prewarmed in {seconds}s", flush=True)
    return {"ok": True, "seconds": seconds, "error": None}


def run_server(host: str = DEFAULT_BOARD_HOST, port: int = DEFAULT_BOARD_PORT, repo_root: Path | None = None,
               perf_log_path: Path | None = None, *, prewarm: bool = True) -> None:
    from tools.aipos_cli.task_loader import enable_truth_cache

    enable_truth_cache()  # AIPOS-F245 item 1: this process serves the board pages = their results cached on the truth fingerprint
    handler = make_handler(repo_root=repo_root, perf_log_path=perf_log_path)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"AIPOS board local UI listening on http://{host}:{port}")
        if prewarm:  # AIPOS-F229: background, the server answers from the first second
            threading.Thread(target=prewarm_read_caches, kwargs={"repo_root": repo_root}, name="board-prewarm", daemon=True).start()
        httpd.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local read-only board UI server")
    parser.add_argument("--host", default=DEFAULT_BOARD_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_BOARD_PORT)  # AIPOS-F106 件②: config.schema ports.board_default
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--perf-log", default=None, help="AIPOS-F177: optional JSON-lines file for per-request server time "
                        "(separate from the board audit log; off when omitted)")
    parser.add_argument("--no-prewarm", action="store_true", help="AIPOS-F229: do not fill the read caches in the background at start "
                        "(default: prewarm, so the first overview / inbox visit after a restart is not a cold parse)")
    parser.add_argument("--board-config", default=None, help="Retired (AIPOS-F171): the project list is derived from the governance home root; "
                        "board_config.json is no longer read (accepted for old launch scripts, ignored)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from tools.aipos_cli.workspace_config import workspace_root_from_env  # AIPOS-F106 件③: 工作区根 env 唯一读取口

    repo_root_arg = args.repo_root or workspace_root_from_env()[0]
    repo_root = Path(repo_root_arg).expanduser().resolve() if repo_root_arg else None
    if args.board_config:
        print("Warning: --board-config is retired (AIPOS-F171): the project list is derived from the governance home root; "
              "the file is not read", file=sys.stderr)
    perf_log = Path(args.perf_log).expanduser().resolve() if args.perf_log else None
    run_server(host=str(args.host), port=int(args.port), repo_root=repo_root, perf_log_path=perf_log, prewarm=not args.no_prewarm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
