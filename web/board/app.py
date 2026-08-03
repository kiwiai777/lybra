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

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.adapter_response import blocked_response
from tools.aipos_cli.ai_assisted_authoring import (
    build_authoring_draft,
    build_live_authoring_draft,
    confirm_authoring_draft,
    confirm_live_authoring_draft,
)
from tools.aipos_cli.custom_agent_profiles import build_profile_draft, confirm_profile_draft
from tools.aipos_cli.project_map import get_project_map
from tools.aipos_cli.verify_bench import get_verify_bench
from tools.aipos_cli.board_adapter import (
    get_advisor_pending_items,
    append_orchestration_event,
    append_planner_iteration,
    claim_task,
    create_draft,
    execute_dry_run,
    get_agents,
    get_context_pack_preview,
    get_drafts,
    get_external_intake_review,
    get_health,
    get_governance,
    get_needs_owner,
    get_owner_decision_records,
    get_owner_truth_view,
    get_orchestration_summary_preview,
    get_orchestration_index,
    get_orchestration_timeline_preview,
    get_planner_loop_mvp_preview,
    get_preview,
    get_queue,
    get_records,
    get_task,
    get_validate,
    publish_draft,
    record_owner_decision,
    record_owner_verification,
)
from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
from tools.aipos_cli.draft_validator import validate_draft_file
from tools.aipos_cli.draft_writer import publish_draft as backend_publish_draft
from tools.aipos_cli.state_recovery import build_state_recovery_preview
from tools.aipos_cli.workspace_config import (
    CONFIG_RELATIVE_PATH,
    DEFAULT_BOARD_HOST,
    DEFAULT_BOARD_PORT,
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    load_workspace_config,
    has_workspace_queue,
)
from web.board.md_source import get_markdown_source
from web.board.auth_otc import (
    DEVICE_CODE_TTL_SECONDS,
    OTC_TTL_SECONDS,
    DeviceCodeStore,
    OTCStore,
    append_auth_log,
    resolve_auth_log_path,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _load_board_config(config_path: Path | None, repo_root: Path | None) -> list[dict[str, str]]:
    """Load multi-workspace config from specified path or workspace-local deployment config.
    
    AIPOS-272 FIX-3: Config resolution order:
    1. Explicit --board-config parameter (config_path)
    2. <repo_root>/.lybra/board_config.json (workspace-local deployment config)
    3. No config => single-workspace mode (workspace is repo_root itself; empty => wizard)
    
    Returns list of {label, root} dicts, or empty list for single-workspace fallback.
    """
    # Priority 1: Explicit --board-config parameter
    if config_path and config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                workspaces = data.get("workspaces", [])
                if isinstance(workspaces, list):
                    return workspaces
        except (json.JSONDecodeError, OSError):
            pass
    
    # Priority 2: Workspace-local deployment config
    if repo_root:
        workspace_config = repo_root / ".lybra" / "board_config.json"
        if workspace_config.exists():
            try:
                data = json.loads(workspace_config.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    workspaces = data.get("workspaces", [])
                    if isinstance(workspaces, list):
                        return workspaces
            except (json.JSONDecodeError, OSError):
                pass
    
    # Priority 3: Single-workspace mode (empty list => caller handles fallback)
    return []


def get_overview(board_config_path: Path | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    """Multi-workspace overview aggregation (AIPOS-251, AIPOS-272 FIX-3)."""
    workspaces_config = _load_board_config(board_config_path, repo_root)
    
    # Fallback to single workspace if no config
    if not workspaces_config:
        fallback_root = repo_root or REPO_ROOT
        workspaces_config = [{"label": "Default Workspace", "root": str(fallback_root)}]
    
    results = []
    for ws_config in workspaces_config:
        label = ws_config.get("label", "Unnamed")
        root_str = ws_config.get("root", "")
        
        try:
            root = Path(root_str).expanduser().resolve()
            
            # Validate workspace
            if not has_workspace_queue(root):
                error_entry = {
                    "label": label,
                    "root": str(root),
                    "status": "error",
                    "error": "Workspace root does not contain 5_tasks/queue"
                }
                label_en = ws_config.get("label_en")
                if label_en:
                    error_entry["label_en"] = label_en
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
                for state in ["pending", "claimed", "blocked", "completed"]:
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
            label_en = ws_config.get("label_en")
            if label_en:
                ok_entry["label_en"] = label_en
            results.append(ok_entry)
            
        except Exception as e:
            exception_entry = {
                "label": label,
                "root": root_str,
                "status": "error",
                "error": str(e)
            }
            label_en = ws_config.get("label_en")
            if label_en:
                exception_entry["label_en"] = label_en
            results.append(exception_entry)
    
    return {
        "ok": True,
        "workspaces": results
    }


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


def _resolve_workspace_root(params: dict[str, list[str]], fallback_root: Path | None, board_config_path: Path | None) -> Path | None:
    """Resolve workspace root from query params or fallback to default.
    
    AIPOS-252: Multi-workspace API support.
    AIPOS-272 FIX-3: Use new config resolution.
    If ?workspace=N is provided, load that workspace from config.
    Otherwise use fallback_root (single-workspace mode).
    """
    workspace_param = params.get('workspace', [])
    if not workspace_param:
        return fallback_root
    
    try:
        workspace_index = int(workspace_param[0])
        workspaces_config = _load_board_config(board_config_path, fallback_root)
        if not workspaces_config or workspace_index >= len(workspaces_config):
            return fallback_root
        
        ws_config = workspaces_config[workspace_index]
        root_str = ws_config.get('root', '')
        return Path(root_str).expanduser().resolve()
    except (ValueError, IndexError, KeyError):
        return fallback_root


def _api_routes(repo_root: Path | None, board_config_path: Path | None = None) -> dict[str, Callable[[dict[str, list[str]]], dict[str, Any]]]:
    return {
        "/api/health": lambda params: get_health(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/overview": lambda _params: get_overview(board_config_path=board_config_path, repo_root=repo_root),
        "/api/runtime-status": partial(_get_runtime_status_route, repo_root=repo_root, board_config_path=board_config_path),
        "/api/generate/advisor-prompt": partial(_generate_advisor_prompt_route, repo_root=repo_root, board_config_path=board_config_path),
        "/api/lifecycle": partial(_get_lifecycle_route, repo_root=repo_root),
        "/api/governance": lambda params: get_governance(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/queue": lambda params: get_queue(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/needs-owner": lambda params: get_needs_owner(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/validate": lambda params: get_validate(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/agents": lambda params: get_agents(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/drafts": lambda params: get_drafts(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/records": lambda params: get_records(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/owner-truth": lambda params: get_owner_truth_view(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/project-map": lambda params: get_project_map(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/verify-bench": lambda params: get_verify_bench(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/external-intake/review": lambda params: get_external_intake_review(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/owner-decision-records": lambda params: get_owner_decision_records(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/planner-drafts/review": partial(_get_planner_drafts_review_route, repo_root=repo_root),
        "/api/owner-decisions/review": partial(_get_owner_decisions_review_route, repo_root=repo_root),
        "/api/orchestration/index": lambda params: get_orchestration_index(repo_root=_resolve_workspace_root(params, repo_root, board_config_path)),
        "/api/orchestration-summary": partial(_get_orchestration_summary_route, repo_root=repo_root),
        "/api/orchestration/summary": partial(_get_orchestration_summary_route, repo_root=repo_root),
        "/api/orchestration-timeline": partial(_get_orchestration_timeline_route, repo_root=repo_root),
        "/api/orchestration/timeline": partial(_get_orchestration_timeline_route, repo_root=repo_root),
        "/api/planner-loop/mvp": partial(_get_planner_loop_mvp_route, repo_root=repo_root),
        "/api/context-pack/preview": partial(_get_context_pack_preview_route, repo_root=repo_root),
        "/api/task": partial(_get_task_route, repo_root=repo_root),
        "/api/preview": partial(_get_preview_route, repo_root=repo_root),
        "/api/markdown-source": partial(_get_markdown_source_route, repo_root=repo_root, board_config_path=board_config_path),
    }


def _api_post_routes(repo_root: Path | None) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    return {
        "/api/workspace/init": partial(_workspace_init_route, repo_root=repo_root),
        "/api/project-structure/preview": partial(_project_structure_preview_route, repo_root=repo_root),
        "/api/project-structure/import": partial(_project_structure_import_route, repo_root=repo_root),
        "/api/parent-requirement/preview": partial(_parent_requirement_preview_route, repo_root=repo_root),
        "/api/planner-tick/preview": partial(_planner_tick_preview_route, repo_root=repo_root),
        "/api/planner-tick/manual-flow/preview": partial(_planner_tick_manual_flow_preview_route, repo_root=repo_root),
        "/api/planner-draft/review": partial(_planner_draft_review_route, repo_root=repo_root),
        "/api/forum-event/review": partial(_forum_event_review_route, repo_root=repo_root),
        "/api/owner-decision/resolve/review": partial(_owner_decision_resolution_review_route, repo_root=repo_root),
        "/api/planner-draft/publish/dry-run": partial(_planner_draft_publish_dry_run_route, repo_root=repo_root),
        "/api/ai-author/preview": partial(_ai_author_preview_route, repo_root=repo_root),
        "/api/ai-author/confirm": partial(_ai_author_confirm_route, repo_root=repo_root),
        "/api/ai-author/live/preview": partial(_ai_author_live_preview_route, repo_root=repo_root),
        "/api/ai-author/live/confirm": partial(_ai_author_live_confirm_route, repo_root=repo_root),
        "/api/agent-profile/draft": partial(_agent_profile_draft_route, repo_root=repo_root),
        "/api/agent-profile/confirm": partial(_agent_profile_confirm_route, repo_root=repo_root),
        "/api/execute/dry-run": partial(_execute_dry_run_route, repo_root=repo_root),
        "/api/execute/confirm": partial(_execute_confirm_route, repo_root=repo_root),
        "/api/verify/approve": partial(_owner_verification_approve_route, repo_root=repo_root),
        "/api/verify/reject": partial(_owner_verification_reject_route, repo_root=repo_root),
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


def _execute_error(operation: str, message: str, *, category: str = "VALIDATION_ERROR") -> dict[str, Any]:
    return blocked_response(
        operation=operation,
        dry_run=True,
        category=category,
        message=message,
        safety_notice="Local controlled execute UI route. Writes require dry-run token revalidation.",
    )


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", text) or "requirement"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _secret_fingerprint(value: str) -> str | None:
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _runtime_config_defaults(repo_root: Path) -> dict[str, Any]:
    config_path = repo_root / CONFIG_RELATIVE_PATH
    config: dict[str, Any] = {}
    config_error: str | None = None
    if config_path.is_file():
        try:
            config = load_workspace_config(config_path)
        except Exception as exc:  # read-only status surface should report config issues instead of raising
            config_error = str(exc)
            config = {}
    board = config.get("board") if isinstance(config.get("board"), dict) else {}
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    return {
        "config_path": str(config_path.relative_to(repo_root)) if config_path.exists() else None,
        "config_error": config_error,
        "board_host": str(board.get("host") or DEFAULT_BOARD_HOST),
        "board_port": int(board.get("port") or DEFAULT_BOARD_PORT),
        "mcp_host": str(mcp.get("host") or DEFAULT_MCP_HOST),
        "mcp_port": int(mcp.get("port") or DEFAULT_MCP_PORT),
        "transport_token_env": str(mcp.get("transport_token_env") or "LYBRA_MCP_TOKEN"),
        "capability_token_env": str(mcp.get("capability_token_env") or "LYBRA_CAPABILITY_TOKEN"),
    }


def _load_connection_endpoints(repo_root: Path) -> dict[str, Any]:
    """Load actual endpoint URLs from workspace connection.json (AIPOS-272 FIX-8).
    
    Returns dict with mcp_rpc_url, mcp_sse_url, board_url (or None if not found).
    Graceful degradation: missing file or parse error => all None + notice.
    """
    connection_path = repo_root / ".lybra" / "connection.json"
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
   完整功能（含 agent watch 耳朵/claim 全链）需要安装 Lybra CLI：
   
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
   
   安装后可用双式 watch：
   - 跨机模式（无需本地 workspace，通过 gate 拉取）：
     lybra agent watch --gate-url {gate_url} --token <ADVISOR_TOKEN> --timeout 30
   - 同机模式（agent 与 workspace 在同一台机器）：
     lybra agent watch --workspace-root {workspace_root} --timeout 30

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
   Full functionality (including agent watch listener / claim full chain) requires Lybra CLI:
   
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
   
   After installation, use dual-mode watch:
   - Cross-machine mode (no local workspace, pull through gate):
     lybra agent watch --gate-url {gate_url} --token <ADVISOR_TOKEN> --timeout 30
   - Same-machine mode (agent and workspace on same machine):
     lybra agent watch --workspace-root {workspace_root} --timeout 30

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


def _get_runtime_status_route(params: dict[str, list[str]], *, repo_root: Path | None, board_config_path: Path | None = None) -> dict[str, Any]:
    operation = "get_runtime_status"
    resolved_root = _resolve_workspace_root(params, repo_root or REPO_ROOT, board_config_path)
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
            "discovery_note": "Board server repo_root / AIPOS_WORKSPACE_ROOT is authoritative for this process.",
            "initialized": (resolved_root / "5_tasks" / "queue").exists(),
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


def _generate_advisor_prompt_route(params: dict[str, list[str]], *, repo_root: Path | None, board_config_path: Path | None = None) -> dict[str, Any]:
    """
    AIPOS-286 FIX-2: Server-side advisor prompt generation with locale support.
    
    GET /api/generate/advisor-prompt?workspace=<index>&locale=<zh|en>
    Returns generated advisor onboarding prompt in the requested language.
    """
    operation = "generate_advisor_prompt"
    locale = _first_param(params, "locale") or "zh"
    if locale not in ("zh", "en"):
        locale = "zh"
    
    resolved_root = _resolve_workspace_root(params, repo_root or REPO_ROOT, board_config_path)
    if resolved_root is None:
        resolved_root = (repo_root or REPO_ROOT).resolve()
    else:
        resolved_root = resolved_root.resolve()
    
    # Load workspace label
    workspace_label = "unnamed workspace"
    if board_config_path and board_config_path.exists():
        try:
            config_data = json.loads(board_config_path.read_text(encoding="utf-8"))
            workspaces = config_data.get("workspaces", [])
            workspace_param = _first_param(params, "workspace")
            if workspace_param and workspace_param.isdigit():
                idx = int(workspace_param)
                if 0 <= idx < len(workspaces):
                    workspace_label = workspaces[idx].get("label", "unnamed workspace")
        except Exception:
            pass
    
    # Load connection endpoints
    connection_endpoints = _load_connection_endpoints(resolved_root)
    defaults = _runtime_config_defaults(resolved_root)
    gate_url = connection_endpoints["mcp_rpc_url"] or f"http://{defaults['mcp_host']}:{defaults['mcp_port']}/mcp"
    # Strip /mcp suffix if present to get base gate URL
    if gate_url.endswith("/mcp"):
        gate_url = gate_url[:-4]
    
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


def _parent_requirement_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="parent_requirement_preview",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local parent requirement preview route. No files are written.",
    )


PLANNER_TICK_VERDICTS = {
    "continue",
    "draft_subtasks",
    "publish_ready",
    "wait_for_audit",
    "repair",
    "needs_owner",
    "blocked",
    "complete",
    "cancel",
    "failed",
}


def _planner_tick_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="planner_tick_preview",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local planner tick preview route. No files are written.",
    )


def _list_from_payload(payload: dict[str, Any], name: str) -> list[str]:
    value = payload.get(name)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


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


def _planner_draft_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="planner_draft_review",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local planner draft review route. No files are written.",
    )


def _is_missing_metadata(metadata: dict[str, Any], field: str) -> bool:
    return metadata.get(field) in (None, "", [])


ORCHESTRATION_EVENT_TYPES = {
    "orchestration_created",
    "planner_assigned",
    "planner_tick_started",
    "planner_tick_completed",
    "planner_paused",
    "planner_resumed",
    "planner_verdict_recorded",
    "subtask_created",
    "subtask_draft_proposed",
    "subtask_publish_ready",
    "subtask_claimed",
    "subtask_completed",
    "subtask_blocked",
    "review_submitted",
    "repair_requested",
    "quota_warning",
    "quota_exhausted",
    "runtime_unavailable",
    "needs_owner_raised",
    "owner_decision_recorded",
    "audit_handoff_requested",
    "handoff_recommended",
    "handoff_approved",
    "orchestration_completed",
    "orchestration_cancelled",
    "orchestration_failed",
}

ORCHESTRATION_EVENT_SEVERITIES = {"info", "warning", "needs_owner", "blocking"}
ORCHESTRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _forum_event_error(message: str) -> dict[str, Any]:
    return blocked_response(
        operation="forum_event_persistence_review",
        dry_run=True,
        category="VALIDATION_ERROR",
        message=message,
        safety_notice="Local forum event persistence review route. No files are written.",
    )


def _safe_orchestration_id(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    if "/" in value or "\\" in value or ".." in value:
        return False
    return bool(ORCHESTRATION_ID_PATTERN.fullmatch(value))



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
        publish_target_ok = str(metadata.get("publish_target") or metadata.get("draft_publish_target") or "").strip() == "5_tasks/queue/pending/"
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


def _forum_event_review_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    del repo_root
    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    event_type = str(payload.get("event_type") or "").strip()
    severity = str(payload.get("severity") or "info").strip()
    actor = str(payload.get("actor") or "").strip()
    source = str(payload.get("source") or "web_board_forum_event_review").strip()
    summary = str(payload.get("summary") or "").strip()
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    timestamp = str(payload.get("timestamp") or "").strip() or _utc_now()
    related_task_id = str(payload.get("related_task_id") or "").strip() or None
    related_subtask_id = str(payload.get("related_subtask_id") or "").strip() or None
    related_iteration_id = str(payload.get("related_iteration_id") or "").strip() or None
    details_text = str(payload.get("details") or "").strip()
    refs = _list_from_payload(payload, "refs")
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    needs_owner_reasons: list[str] = []
    preconditions: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str, severity_level: str = "block") -> None:
        preconditions.append({"name": name, "passed": passed, "severity": severity_level, "detail": detail})
        if passed:
            return
        if severity_level == "needs_owner":
            if detail not in needs_owner_reasons:
                needs_owner_reasons.append(detail)
        elif severity_level == "warn":
            if detail not in warnings:
                warnings.append(detail)
        elif detail not in blocking_reasons:
            blocking_reasons.append(detail)

    add_check("orchestration_id_present", bool(orchestration_id), "orchestration_id is required")
    add_check("orchestration_id_path_safe", _safe_orchestration_id(orchestration_id), "orchestration_id must be path-safe")
    add_check("event_type_allowed", event_type in ORCHESTRATION_EVENT_TYPES, "event_type must be allowed by orchestration_event_log_schema.md")
    add_check("severity_allowed", severity in ORCHESTRATION_EVENT_SEVERITIES, "severity must be info, warning, needs_owner, or blocking")
    add_check("actor_present", bool(actor), "actor is required")
    add_check("source_present", bool(source), "source is required")
    add_check("summary_present", bool(summary), "summary is required")
    add_check("forum_ref_present", bool(forum_thread_ref), "forum_thread_ref is required")

    if forum_thread_ref and forum_thread_ref not in refs:
        refs.insert(0, forum_thread_ref)

    owner_gate_event = event_type in {"needs_owner_raised", "owner_decision_recorded"} or severity == "needs_owner"
    if event_type == "owner_decision_recorded":
        has_owner_ref = any("owner" in ref.lower() or "decision" in ref.lower() for ref in refs)
        add_check(
            "owner_decision_evidence_ref",
            has_owner_ref,
            "owner_decision_recorded requires an Owner decision evidence ref",
        )
    if owner_gate_event:
        add_check(
            "owner_gate_preserved",
            event_type != "owner_decision_recorded" or not blocking_reasons,
            "Owner gate events must preserve or reference explicit Owner decision evidence",
            severity_level="needs_owner",
        )

    event_id = str(payload.get("event_id") or "").strip()
    if not event_id and orchestration_id and event_type:
        event_id = f"evt_{_slug(orchestration_id)}_{_slug(event_type)}_{timestamp[:10].replace('-', '')}"
    target_path = f"5_tasks/orchestration/{orchestration_id}/orchestration_events.md" if orchestration_id else None
    event_entry = {
        "event_id": event_id or None,
        "orchestration_id": orchestration_id or None,
        "event_type": event_type or None,
        "timestamp": timestamp,
        "actor": actor or None,
        "source": source or None,
        "related_task_id": related_task_id,
        "related_subtask_id": related_subtask_id,
        "related_iteration_id": related_iteration_id,
        "severity": severity,
        "summary": summary or None,
        "details": {"text": details_text} if details_text else {},
        "refs": refs,
    }
    append_plan = {
        "target_path": target_path,
        "append_only": True,
        "operation": "future_append_orchestration_event",
        "planned_writes": [
            {
                "path": target_path,
                "kind": "append",
                "type": "orchestration_event_entry",
            }
        ] if target_path and not blocking_reasons else [],
    }
    review_passed = not blocking_reasons
    verdict = "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if needs_owner_reasons else ("WARN" if warnings else "PASS"))
    return {
        "ok": review_passed,
        "verdict": verdict,
        "operation": "forum_event_persistence_review",
        "dry_run": True,
        "actor": {"actor": actor} if actor else None,
        "data": {
            "event_entry": event_entry,
            "append_plan": append_plan,
            "preconditions": preconditions,
            "writer_review_only": True,
            "writes_enabled": False,
            "forum_backend_enabled": False,
            "network_posting_enabled": False,
            "controlled_execute_expanded": False,
            "handoff_to_future_writer": {
                "enabled": review_passed,
                "next_operation": "append_only_orchestration_event_writer",
                "requires_future_audit": True,
            },
        },
        "summary": {
            "orchestration_id": orchestration_id,
            "event_type": event_type,
            "severity": severity,
            "target_path": target_path,
            "writer_review_passed": review_passed,
            "preconditions_total": len(preconditions),
            "preconditions_passed": sum(1 for item in preconditions if item.get("passed")),
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": needs_owner_reasons,
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-63 forum event persistence review is review-only; AIPOS-64 must implement any writer."],
        "safety_notice": "Local forum event persistence review route. No files are written.",
        "errors": [],
    }




def _owner_decision_resolution_review_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "").strip()
    decision = str(payload.get("decision") or "").strip()
    decision_reason = str(payload.get("decision_reason") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    evidence_ref = str(payload.get("evidence_ref") or "").strip()
    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    decision_type = str(payload.get("decision_type") or "owner_review").strip()
    related_task_id = str(payload.get("related_task_id") or "").strip()
    related_iteration_id = str(payload.get("related_iteration_id") or "").strip()
    allowed_decisions = {"approved", "rejected", "scope_reduced", "needs_revision", "deferred"}
    blocking_reasons: list[str] = []
    if not request_id:
        blocking_reasons.append("request_id is required")
    if decision not in allowed_decisions:
        blocking_reasons.append("decision must be approved, rejected, scope_reduced, needs_revision, or deferred")
    if not decision_reason:
        blocking_reasons.append("decision_reason is required")
    if not actor:
        blocking_reasons.append("actor is required")
    if not evidence_ref:
        blocking_reasons.append("evidence_ref is required")
    if not orchestration_id:
        blocking_reasons.append("orchestration_id is required")
    if not forum_thread_ref:
        blocking_reasons.append("forum_thread_ref is required")
    if blocking_reasons:
        return {
            "ok": False,
            "verdict": "BLOCK",
            "operation": "owner_decision_resolution_review",
            "dry_run": True,
            "data": {
                "resolution_review_only": True,
                "writes_enabled": False,
                "decision_persistence_enabled": False,
            },
            "summary": {"request_id": request_id, "decision": decision, "writes_enabled": False},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": [],
            "blocking_reasons": blocking_reasons,
            "needs_owner_reasons": [],
            "execute_allowed": False,
            "execute_blocking_reasons": ["AIPOS-76 Owner decision resolution review is preview-only."],
            "dry_run_token": None,
            "safety_notice": "Owner decision resolution review only. No files are written and no decisions are persisted.",
            "errors": [],
        }
    details = {
        "request_id": request_id,
        "decision": decision,
        "decision_type": decision_type,
        "decision_reason": decision_reason,
        "resolution_scope": "review_only",
    }
    review = _forum_event_review_route(
        {
            "orchestration_id": orchestration_id,
            "event_type": "owner_decision_recorded",
            "severity": "info",
            "actor": actor,
            "source": "web_board_owner_decision_resolution_review",
            "forum_thread_ref": forum_thread_ref,
            "related_task_id": related_task_id,
            "related_iteration_id": related_iteration_id,
            "summary": f"Owner decision {decision} for {request_id}: {decision_reason}",
            "details": json.dumps(details, ensure_ascii=False, sort_keys=True),
            "refs": [forum_thread_ref, evidence_ref, f"owner_decision:{request_id}"],
        },
        repo_root=repo_root,
    )
    data = dict(review.get("data") or {})
    data.update(
        {
            "resolution_request": {
                "request_id": request_id,
                "decision": decision,
                "decision_type": decision_type,
                "decision_reason": decision_reason,
                "evidence_ref": evidence_ref,
            },
            "resolution_review_only": True,
            "decision_persistence_enabled": False,
            "controlled_mutation_allowed": False,
            "writes_enabled": False,
        }
    )
    return {
        **review,
        "operation": "owner_decision_resolution_review",
        "data": data,
        "summary": {
            **dict(review.get("summary") or {}),
            "request_id": request_id,
            "decision": decision,
            "decision_type": decision_type,
            "writer_review_passed": review.get("verdict") == "PASS",
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-76 previews Owner decision resolution only; persistence remains a future controlled gate."],
        "dry_run_token": None,
        "safety_notice": "Owner decision resolution review only. No files are written, no forum backend is posted, and no decision is persisted.",
    }


def _planner_draft_publish_dry_run_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    path = str(payload.get("path") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    if not actor:
        return _execute_error("planner_draft_publish", "actor is required")
    if not path:
        return _execute_error("planner_draft_publish", "path is required")

    review = _planner_draft_review_route({"path": path, "actor": actor}, repo_root=repo_root)
    review_data = dict(review.get("data") or {})
    review_summary = dict(review.get("summary") or {})
    if review.get("verdict") != "PASS" or not review_summary.get("publish_eligible"):
        return {
            "ok": False,
            "verdict": "NEEDS_OWNER" if review.get("verdict") == "NEEDS_OWNER" else "BLOCK",
            "operation": "planner_draft_publish",
            "dry_run": True,
            "actor": {"actor": actor},
            "data": {
                "path": path,
                "planner_review": review_data,
                "owner_decision_gate": {"clear": False, "decision_requests": []},
                "writes_enabled": False,
                "controlled_execute_operation": "draft_publish",
            },
            "summary": {"path": path, "publish_eligible": False, "owner_gate_clear": False},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": list(review.get("warnings", [])),
            "blocking_reasons": list(review.get("blocking_reasons", [])),
            "needs_owner_reasons": list(review.get("needs_owner_reasons", [])),
            "owner_confirmation_required": bool(review.get("needs_owner_reasons")),
            "owner_confirmation_reasons": list(review.get("needs_owner_reasons", [])),
            "execute_allowed": False,
            "execute_blocking_reasons": ["Planner draft is not approved for controlled publish."],
            "dry_run_token": None,
            "safety_notice": "Planner draft publish wrapper did not create a dry-run token because preconditions failed.",
            "errors": [],
        }

    metadata = dict(review_data.get("frontmatter") or {})
    task_id = str(review_summary.get("task_id") or metadata.get("task_id") or "").strip()
    orchestration_id = str(metadata.get("orchestration_id") or "").strip()
    owner_gate = _get_owner_decisions_review_route({}, repo_root=repo_root)
    related_requests = []
    for request in owner_gate.get("data", {}).get("decision_requests", []) or []:
        request_task = str(request.get("related_task_id") or "").strip()
        request_orch = str(request.get("related_orchestration_id") or "").strip()
        if (task_id and request_task == task_id) or (orchestration_id and request_orch == orchestration_id):
            related_requests.append(request)
    if related_requests:
        reasons = [str(item.get("summary") or item.get("title") or item.get("request_id")) for item in related_requests]
        return {
            "ok": False,
            "verdict": "NEEDS_OWNER",
            "operation": "planner_draft_publish",
            "dry_run": True,
            "actor": {"actor": actor},
            "data": {
                "path": path,
                "planner_review": review_data,
                "owner_decision_gate": {"clear": False, "decision_requests": related_requests},
                "writes_enabled": False,
                "controlled_execute_operation": "draft_publish",
            },
            "summary": {"path": path, "task_id": task_id, "publish_eligible": False, "owner_gate_clear": False},
            "planned_writes": [],
            "planned_moves": [],
            "warnings": list(owner_gate.get("warnings", [])),
            "blocking_reasons": [],
            "needs_owner_reasons": reasons,
            "owner_confirmation_required": True,
            "owner_confirmation_reasons": reasons,
            "execute_allowed": False,
            "execute_blocking_reasons": ["Related Owner decision gate is still open."],
            "dry_run_token": None,
            "safety_notice": "Planner draft publish wrapper blocked publish because an Owner decision gate is open.",
            "errors": [],
        }

    dry_run = publish_draft(path=path, dry_run=True, repo_root=repo_root, actor=actor)
    dry_data = dict(dry_run.get("data") or {})
    dry_data.update(
        {
            "planner_review_summary": review_summary,
            "owner_decision_gate": {"clear": True, "decision_requests": []},
            "controlled_execute_operation": "draft_publish",
            "second_confirmation_required": True,
        }
    )
    dry_run["operation"] = "planner_draft_publish"
    dry_run["data"] = dry_data
    dry_run["summary"] = {
        **dict(dry_run.get("summary") or {}),
        "task_id": task_id,
        "path": path,
        "publish_eligible": True,
        "owner_gate_clear": True,
        "second_confirmation_required": True,
    }
    dry_run["execute_blocking_reasons"] = list(dry_run.get("execute_blocking_reasons", []))
    dry_run["safety_notice"] = "Planner draft publish uses existing controlled draft_publish dry-run token and still requires explicit confirmation."
    return dry_run


def _planner_draft_review_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    path = str(payload.get("path") or "").strip()
    actor = str(payload.get("actor") or "").strip() or None
    if not path:
        return _planner_draft_error("path is required")
    resolved_root = (repo_root or REPO_ROOT).resolve()
    try:
        validation = validate_draft_file(resolved_root, path)
        publish_preview = backend_publish_draft(resolved_root, path, dry_run=True)
    except Exception as exc:
        return _planner_draft_error(str(exc) or exc.__class__.__name__)

    metadata = dict(validation.get("frontmatter") or {})
    blocking_reasons = list(validation.get("blocking_reasons", []))
    warnings = list(validation.get("warnings", []))
    needs_owner_reasons: list[str] = []
    preconditions: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str, severity: str = "block") -> None:
        preconditions.append({"name": name, "passed": passed, "severity": severity, "detail": detail})
        if passed:
            return
        if severity == "needs_owner":
            if detail not in needs_owner_reasons:
                needs_owner_reasons.append(detail)
        elif severity == "warn":
            if detail not in warnings:
                warnings.append(detail)
        elif detail not in blocking_reasons:
            blocking_reasons.append(detail)

    planner_created = (
        str(metadata.get("draft_source") or "").strip() == "planner"
        or _as_bool(metadata.get("created_by_planner"))
        or "/planner/" in path
    )
    add_check(
        "planner_created_draft",
        planner_created,
        "Draft must be marked as planner-created for Planner Draft Review.",
    )

    missing_fields = [field for field in PLANNER_DRAFT_REQUIRED_FIELDS if _is_missing_metadata(metadata, field)]
    add_check(
        "required_planner_metadata",
        not missing_fields,
        "Missing planner draft metadata: " + ", ".join(missing_fields),
    )

    planner_tier = str(metadata.get("planner_model_tier") or "").strip().upper()
    add_check(
        "planner_model_tier",
        planner_tier in {"L3", "L4"},
        "planner_model_tier must be L3 or L4.",
    )

    publish_status = str(metadata.get("publish_status") or "").strip()
    draft_status = str(metadata.get("draft_status") or "").strip()
    add_check(
        "publish_status_approved",
        publish_status == "approved_for_publish",
        "publish_status must be approved_for_publish before planner draft publish.",
        severity="needs_owner",
    )
    add_check(
        "draft_not_rejected_or_superseded",
        draft_status not in {"rejected", "superseded", "blocked"} and publish_status not in {"rejected", "superseded", "blocked"},
        "Draft is rejected, superseded, or blocked.",
    )
    add_check(
        "owner_gate_clear",
        draft_status != "needs_owner" and publish_status != "needs_owner" and not _as_bool(metadata.get("needs_owner")),
        "Draft has a pending Owner decision gate.",
        severity="needs_owner",
    )

    planner_agent = str(metadata.get("planner_agent") or "").strip()
    reviewer = str(metadata.get("reviewer") or "").strip()
    audit_by = str(metadata.get("audit_by") or "").strip()
    add_check("reviewer_explicit", bool(reviewer), "reviewer is required for planner-created drafts.")
    add_check("audit_by_explicit", bool(audit_by), "audit_by is required for planner-created drafts.")
    add_check(
        "planner_not_reviewer",
        bool(planner_agent and reviewer and planner_agent != reviewer),
        "planner_agent must not review its own planned work.",
    )
    add_check(
        "planner_not_auditor",
        bool(planner_agent and audit_by and planner_agent != audit_by),
        "planner_agent must not audit its own planned work.",
    )
    add_check(
        "publish_target_pending_queue",
        str(metadata.get("publish_target") or metadata.get("draft_publish_target") or "").strip() == "5_tasks/queue/pending/",
        "publish target must be 5_tasks/queue/pending/.",
    )

    publish_blocking = list(publish_preview.get("blocking_reasons", []))
    publish_warnings = list(publish_preview.get("warnings", []))
    classification_warnings = list(validation.get("classification_warnings", [])) + list(
        publish_preview.get("classification_warnings", [])
    )
    add_check(
        "controlled_publish_dry_run_compatible",
        str(publish_preview.get("verdict")) != "BLOCK",
        "Existing draft_publish dry-run blocks this draft: " + "; ".join(publish_blocking),
    )
    for warning in publish_warnings:
        if warning not in warnings:
            warnings.append(warning)

    publish_eligible = not blocking_reasons and not needs_owner_reasons
    verdict_warnings = [warning for warning in warnings if warning not in classification_warnings]
    verdict = "BLOCK" if blocking_reasons else ("NEEDS_OWNER" if needs_owner_reasons else ("WARN" if verdict_warnings else "PASS"))
    return {
        "ok": verdict != "BLOCK",
        "verdict": verdict,
        "operation": "planner_draft_review",
        "dry_run": True,
        "actor": {"actor": actor} if actor else None,
        "data": {
            "path": validation.get("path"),
            "task_id": validation.get("task_id"),
            "frontmatter": metadata,
            "planner_created": planner_created,
            "preconditions": preconditions,
            "publish_preview": {
                "operation": "draft_publish",
                "source_path": publish_preview.get("source_path"),
                "target_path": publish_preview.get("target_path"),
                "task_id": publish_preview.get("task_id"),
                "verdict": publish_preview.get("verdict"),
                "would_write": publish_preview.get("would_write", False),
                "planned_writes": publish_preview.get("planned_writes", []),
                "blocking_reasons": publish_blocking,
                "warnings": publish_warnings,
                "classification_warnings": classification_warnings,
            },
            "handoff_to_draft_publish": {
                "enabled": publish_eligible,
                "path": validation.get("path"),
                "next_operation": "draft_publish",
                "next_action": "Run the existing Draft Publish dry-run, then confirm through the controlled execute path.",
            },
            "writes_enabled": False,
            "review_only": True,
            "controlled_execute_expanded": False,
        },
        "summary": {
            "task_id": validation.get("task_id"),
            "path": validation.get("path"),
            "publish_eligible": publish_eligible,
            "preconditions_total": len(preconditions),
            "preconditions_passed": sum(1 for item in preconditions if item.get("passed")),
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "needs_owner_reasons": needs_owner_reasons,
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-61 planner draft review is review-only; publish must use existing draft_publish dry-run and confirm."],
        "safety_notice": "Local planner draft review route. No files are written.",
        "errors": [],
    }


def _planner_tick_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    del repo_root
    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    parent_task_id = str(payload.get("parent_task_id") or "").strip()
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    planner_agent = str(payload.get("planner_agent") or "planner_agent").strip() or "planner_agent"
    planner_agent_instance = str(payload.get("planner_agent_instance") or "planner.local.001").strip() or "planner.local.001"
    planner_model_tier = str(payload.get("planner_model_tier") or "L3").strip().upper()
    decision = str(payload.get("decision") or "").strip()
    decision_reason = str(payload.get("decision_reason") or "").strip()
    next_expected_action = str(payload.get("next_expected_action") or "").strip()
    combined_planner_executor = bool(payload.get("combined_planner_executor", False))
    try:
        iteration_number = int(str(payload.get("iteration_number") or "1").strip())
    except ValueError:
        return _planner_tick_error("iteration_number must be an integer")
    if not orchestration_id:
        return _planner_tick_error("orchestration_id is required")
    if not parent_task_id:
        return _planner_tick_error("parent_task_id is required")
    if not forum_thread_ref:
        return _planner_tick_error("forum_thread_ref is required")
    if planner_model_tier not in {"L3", "L4"}:
        return _planner_tick_error("planner_model_tier must be L3 or L4")
    if iteration_number < 1:
        return _planner_tick_error("iteration_number must be greater than zero")
    if decision not in PLANNER_TICK_VERDICTS:
        return _planner_tick_error("decision must be an AIPOS-54 planner tick verdict")
    if not decision_reason:
        return _planner_tick_error("decision_reason is required")
    if not next_expected_action:
        return _planner_tick_error("next_expected_action is required")

    timestamp = _utc_now()
    iteration_id = f"iter_{timestamp[:10].replace('-', '')}_{_slug(orchestration_id)}_{iteration_number:03d}"
    inputs_read = _list_from_payload(payload, "inputs_read")
    observations = _list_from_payload(payload, "observations")
    needs_owner_reasons = _list_from_payload(payload, "needs_owner_reasons")
    publish_candidates = _list_from_payload(payload, "publish_candidates")
    repair_recommendations = _list_from_payload(payload, "repair_recommendations")
    stop_condition_hits = _list_from_payload(payload, "stop_condition_hits")
    subtask_drafts_proposed = _list_from_payload(payload, "subtask_drafts_proposed")
    audit_handoff_needed = decision == "wait_for_audit" or bool(payload.get("audit_handoff_needed", False))
    owner_decision_required = decision == "needs_owner" or bool(needs_owner_reasons)
    severity = "needs_owner" if owner_decision_required else "blocking" if decision in {"blocked", "failed"} else "info"
    planner_iteration = {
        "iteration_id": iteration_id,
        "orchestration_id": orchestration_id,
        "iteration_number": iteration_number,
        "planner_agent": planner_agent,
        "planner_agent_instance": planner_agent_instance,
        "planner_model_tier": planner_model_tier,
        "started_at": timestamp,
        "ended_at": timestamp,
        "input_refs": inputs_read,
        "observed_queue_state": str(payload.get("observed_queue_state") or "not_observed_in_preview").strip(),
        "observed_subtask_summary": observations,
        "decisions": [
            {
                "decision": decision,
                "reason": decision_reason,
                "owner_decision_required": owner_decision_required,
            }
        ],
        "created_subtasks": subtask_drafts_proposed,
        "updated_recommendations": repair_recommendations,
        "failure_observations": _list_from_payload(payload, "failure_observations"),
        "quota_observations": _list_from_payload(payload, "quota_observations"),
        "needs_owner_reasons": needs_owner_reasons,
        "next_check_after": str(payload.get("next_check_after") or "").strip() or None,
        "verdict": decision,
    }
    visible_report = {
        "planner_iteration_id": iteration_id,
        "orchestration_id": orchestration_id,
        "parent_task_id": parent_task_id,
        "planner_agent": planner_agent,
        "planner_agent_instance": planner_agent_instance,
        "planner_model_tier": planner_model_tier,
        "combined_planner_executor": combined_planner_executor,
        "forum_thread_ref": forum_thread_ref,
        "inputs_read": inputs_read,
        "observations": observations,
        "decision": decision,
        "decision_reason": decision_reason,
        "owner_decision_required": owner_decision_required,
        "needs_owner_reasons": needs_owner_reasons,
        "subtask_drafts_proposed": subtask_drafts_proposed,
        "publish_candidates": publish_candidates,
        "repair_recommendations": repair_recommendations,
        "audit_handoff_needed": audit_handoff_needed,
        "next_expected_action": next_expected_action,
        "stop_condition_hits": stop_condition_hits,
    }
    event_log_preview = [
        {
            "event_id": f"evt_{iteration_id}_started",
            "orchestration_id": orchestration_id,
            "event_type": "planner_tick_started",
            "timestamp": timestamp,
            "actor": planner_agent_instance,
            "source": "web_board_planner_tick_preview",
            "related_task_id": parent_task_id,
            "related_subtask_id": None,
            "related_iteration_id": iteration_id,
            "severity": "info",
            "summary": "Planner tick preview started.",
            "details": {"planner_model_tier": planner_model_tier},
            "refs": [forum_thread_ref],
        },
        {
            "event_id": f"evt_{iteration_id}_verdict",
            "orchestration_id": orchestration_id,
            "event_type": "planner_verdict_recorded",
            "timestamp": timestamp,
            "actor": planner_agent_instance,
            "source": "web_board_planner_tick_preview",
            "related_task_id": parent_task_id,
            "related_subtask_id": None,
            "related_iteration_id": iteration_id,
            "severity": severity,
            "summary": f"Planner tick preview verdict: {decision}.",
            "details": {"decision_reason": decision_reason, "next_expected_action": next_expected_action},
            "refs": [forum_thread_ref],
        },
        {
            "event_id": f"evt_{iteration_id}_completed",
            "orchestration_id": orchestration_id,
            "event_type": "planner_tick_completed",
            "timestamp": timestamp,
            "actor": planner_agent_instance,
            "source": "web_board_planner_tick_preview",
            "related_task_id": parent_task_id,
            "related_subtask_id": None,
            "related_iteration_id": iteration_id,
            "severity": severity,
            "summary": "Planner tick preview completed.",
            "details": {"owner_decision_required": owner_decision_required},
            "refs": [forum_thread_ref],
        },
    ]
    if owner_decision_required:
        event_log_preview.append(
            {
                "event_id": f"evt_{iteration_id}_needs_owner",
                "orchestration_id": orchestration_id,
                "event_type": "needs_owner_raised",
                "timestamp": timestamp,
                "actor": planner_agent_instance,
                "source": "web_board_planner_tick_preview",
                "related_task_id": parent_task_id,
                "related_subtask_id": None,
                "related_iteration_id": iteration_id,
                "severity": "needs_owner",
                "summary": "Planner tick preview requires Owner decision.",
                "details": {"needs_owner_reasons": needs_owner_reasons},
                "refs": [forum_thread_ref],
            }
        )
    return {
        "ok": True,
        "verdict": "NEEDS_OWNER" if owner_decision_required else "PASS",
        "operation": "planner_tick_preview",
        "dry_run": True,
        "data": {
            "planner_iteration": planner_iteration,
            "visible_report": visible_report,
            "event_log_preview": event_log_preview,
            "writes_enabled": False,
            "forum_backend_enabled": False,
            "planner_runtime_launch_enabled": False,
            "orchestration_writer_enabled": False,
        },
        "summary": {
            "orchestration_id": orchestration_id,
            "planner_iteration_id": iteration_id,
            "decision": decision,
            "owner_decision_required": owner_decision_required,
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": [
            "Preview only. AIPOS-60 does not write planner iterations, orchestration events, forum events, task cards, drafts, queue files, records, or memory."
        ],
        "blocking_reasons": [],
        "needs_owner_reasons": needs_owner_reasons,
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-60 planner tick/event log UI is preview-only"],
        "safety_notice": "Local planner tick preview route. No files are written.",
        "errors": [],
    }



CRITICAL_OWNER_FORK_KEYWORDS = {
    "architecture": ["architecture", "route", "design", "service", "database", "deployment"],
    "scope": ["scope", "expand", "expansion", "requirement"],
    "risk": ["risk", "high-risk", "irreversible", "data loss", "refactor"],
    "authority": ["authority", "permission", "agent authority", "model tier"],
    "security": ["security", "credential", "secret", "auth", "rbac"],
    "audit_boundary": ["audit", "reviewer", "auditor", "self-audit"],
}


def _critical_fork_hits(payload: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(payload.get(field) or "")
        for field in [
            "decision",
            "decision_reason",
            "observations",
            "needs_owner_reasons",
            "publish_candidates",
            "repair_recommendations",
            "stop_condition_hits",
            "next_expected_action",
        ]
    ).lower()
    hits: list[str] = []
    for fork_type, keywords in CRITICAL_OWNER_FORK_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            hits.append(fork_type)
    return hits


def _planner_tick_manual_flow_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    preview = _planner_tick_preview_route(payload, repo_root=repo_root)
    if preview.get("verdict") == "BLOCK":
        preview["operation"] = "planner_tick_manual_flow_preview"
        return preview

    orchestration_id = str(payload.get("orchestration_id") or "").strip()
    summary = get_orchestration_summary_preview(orchestration_id=orchestration_id, repo_root=resolved_root) if orchestration_id else {}
    timeline = get_orchestration_timeline_preview(orchestration_id=orchestration_id, repo_root=resolved_root) if orchestration_id else {}
    owner_gate = _get_owner_decisions_review_route({}, repo_root=repo_root)
    related_owner_requests = [
        request
        for request in owner_gate.get("data", {}).get("decision_requests", []) or []
        if str(request.get("related_orchestration_id") or "") == orchestration_id
        or str(request.get("related_task_id") or "") == str(payload.get("parent_task_id") or "").strip()
    ]
    fork_hits = _critical_fork_hits(payload)
    decision = str(payload.get("decision") or "").strip()
    needs_owner_reasons = list(preview.get("needs_owner_reasons", []))
    if related_owner_requests:
        needs_owner_reasons.extend(str(item.get("summary") or item.get("title") or item.get("request_id")) for item in related_owner_requests)
    if fork_hits and decision != "needs_owner":
        needs_owner_reasons.extend(f"critical fork requires Owner decision: {fork}" for fork in fork_hits)

    data = dict(preview.get("data") or {})
    visible_report = dict(data.get("visible_report") or {})
    owner_decision_required = bool(needs_owner_reasons) or bool(visible_report.get("owner_decision_required"))
    visible_report["critical_fork_hits"] = fork_hits
    visible_report["related_owner_decision_requests"] = related_owner_requests
    visible_report["owner_decision_required"] = owner_decision_required
    visible_report["manual_flow_next_step"] = "stop_for_owner" if owner_decision_required else visible_report.get("next_expected_action")
    data["visible_report"] = visible_report
    data.update(
        {
            "manual_flow": True,
            "orchestration_summary_snapshot": summary.get("summary") or {},
            "timeline_snapshot": timeline.get("summary") or {},
            "owner_decision_snapshot": {
                "total": owner_gate.get("summary", {}).get("total", 0),
                "related_requests": related_owner_requests,
            },
            "writes_enabled": False,
            "orchestration_writer_enabled": False,
            "planner_iteration_append_enabled": False,
            "forum_event_append_enabled": False,
            "planner_runtime_launch_enabled": False,
            "queue_mutation_enabled": False,
        }
    )
    verdict = "NEEDS_OWNER" if owner_decision_required else preview.get("verdict")
    return {
        **preview,
        "ok": True,
        "verdict": verdict,
        "operation": "planner_tick_manual_flow_preview",
        "data": data,
        "summary": {
            **dict(preview.get("summary") or {}),
            "manual_flow": True,
            "critical_fork_hits": fork_hits,
            "related_owner_decision_requests": len(related_owner_requests),
            "owner_decision_required": owner_decision_required,
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "needs_owner_reasons": list(dict.fromkeys(reason for reason in needs_owner_reasons if reason)),
        "owner_confirmation_required": owner_decision_required,
        "owner_confirmation_reasons": list(dict.fromkeys(reason for reason in needs_owner_reasons if reason)),
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-74 manual planner tick flow is preview-only; no planner iteration or event is persisted."],
        "dry_run_token": None,
        "safety_notice": "Manual planner tick flow preview only. No planner iteration, event, task, queue, draft, record, runtime, forum, or git state is written.",
    }


def _parent_requirement_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    del repo_root
    title = str(payload.get("title") or "").strip()
    owner_goal = str(payload.get("owner_goal") or "").strip()
    project = str(payload.get("project") or "lybra").strip() or "lybra"
    forum_thread_ref = str(payload.get("forum_thread_ref") or "").strip()
    planner_agent = str(payload.get("planner_agent") or "planner_agent").strip() or "planner_agent"
    planner_agent_instance = str(payload.get("planner_agent_instance") or "planner.local.001").strip() or "planner.local.001"
    planner_runtime_profile = str(payload.get("planner_runtime_profile") or "local_process").strip() or "local_process"
    planner_model_tier = str(payload.get("planner_model_tier") or "L3").strip().upper()
    max_iterations = str(payload.get("max_iterations") or "5").strip()
    timestamp = _utc_now()
    if not title:
        return _parent_requirement_error("title is required")
    if not owner_goal:
        return _parent_requirement_error("owner_goal is required")
    if not forum_thread_ref:
        return _parent_requirement_error("forum_thread_ref is required")
    if planner_model_tier not in {"L3", "L4"}:
        return _parent_requirement_error("planner_model_tier must be L3 or L4")
    requirement_slug = _slug(title)
    date_slug = timestamp[:10].replace("-", "")
    requirement_id = f"REQ-{date_slug}-{requirement_slug}"
    orchestration_id = f"orch_{_slug(project)}_{date_slug}_{requirement_slug}"
    parent_task_id = f"{requirement_id}-PARENT"
    requirement = {
        "requirement_id": requirement_id,
        "title": title,
        "owner_goal": owner_goal,
        "created_by": "Owner",
        "created_at": timestamp,
        "project": project,
        "task_class": "complex",
        "complexity_note": "Parent requirement uses the governed planner closed loop.",
        "intake_status": "received",
        "forum_thread_ref": forum_thread_ref,
        "visibility": "forum_visible",
        "planning_required": True,
        "min_planner_model_tier": "L3",
        "allowed_planner_agents": ["dev_codex", "dev_claude"],
        "assigned_planner": planner_agent,
        "assigned_planner_instance": planner_agent_instance,
        "planner_runtime_profile": planner_runtime_profile,
        "planner_assignment_status": "proposed",
        "orchestration_id": orchestration_id,
        "parent_task_id": parent_task_id,
        "needs_owner": False,
        "needs_owner_reasons": [],
    }
    planner_loop_preview = {
        "loop": "observe -> decide -> emit -> wait",
        "next_expected_action": "Owner reviews preview, then a future approved writer may create the parent requirement record.",
        "max_iterations": max_iterations,
        "stop_conditions": [
            "owner_decision_required",
            "audit_pending",
            "dependency_blocked",
            "max_iterations_reached",
            "scope_or_risk_fork",
        ],
        "owner_decision_required_for": [
            "architecture_route_split",
            "scope_expansion",
            "risk_escalation",
            "new_runtime_or_service",
            "audit_boundary_change",
            "turning_protocol_into_implementation",
        ],
    }
    return {
        "ok": True,
        "verdict": "PASS",
        "operation": "parent_requirement_preview",
        "dry_run": True,
        "data": {
            "parent_requirement": requirement,
            "planner_loop_preview": planner_loop_preview,
            "writes_enabled": False,
            "forum_backend_enabled": False,
            "planner_runtime_launch_enabled": False,
        },
        "summary": {
            "requirement_id": requirement_id,
            "orchestration_id": orchestration_id,
            "planner_model_tier": planner_model_tier,
            "writes_enabled": False,
        },
        "planned_writes": [],
        "planned_moves": [],
        "warnings": [
            "Preview only. AIPOS-59 does not write parent requirement records, orchestration files, forum events, or task cards."
        ],
        "blocking_reasons": [],
        "needs_owner_reasons": [],
        "execute_allowed": False,
        "execute_blocking_reasons": ["AIPOS-59 parent requirement entry is preview-only"],
        "safety_notice": "Local parent requirement preview route. No files are written.",
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


def _get_markdown_source_route(params: dict[str, list[str]], *, repo_root: Path | None, board_config_path: Path | None = None) -> dict[str, Any]:
    # AIPOS-263: md 原文侧栏 — 队列卡 / 记录 md 的只读安全渲染(零依赖、先转义后变换、路径白名单)。
    resolved_root = _resolve_workspace_root(params, repo_root, board_config_path)
    return get_markdown_source(
        path=_first_param(params, "path"),
        task_id=_first_param(params, "task_id"),
        record_id=_first_param(params, "record_id"),
        repo_root=resolved_root,
    )


def _ai_author_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    fixture_id = str(payload.get("fixture_id") or "").strip()
    intent = payload.get("intent")
    if not actor:
        return _execute_error("ai_assisted_fixture_authoring", "actor is required")
    if not fixture_id:
        return _execute_error("ai_assisted_fixture_authoring", "fixture_id is required")
    if not isinstance(intent, dict):
        return _execute_error("ai_assisted_fixture_authoring", "intent object is required")
    try:
        return build_authoring_draft(Path(repo_root or REPO_ROOT), intent, fixture_id=fixture_id, actor=actor)
    except Exception as exc:
        return _execute_error("ai_assisted_fixture_authoring", str(exc))


def _ai_author_confirm_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    preview = payload.get("preview")
    if not actor:
        return _execute_error("ai_assisted_fixture_authoring", "actor is required")
    if not isinstance(preview, dict):
        return _execute_error("ai_assisted_fixture_authoring", "preview object is required")
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", False)) else None
    try:
        return confirm_authoring_draft(
            Path(repo_root or REPO_ROOT),
            preview,
            actor=actor,
            owner_confirmation_token=owner_token,
        )
    except Exception as exc:
        return _execute_error("ai_assisted_fixture_authoring", str(exc))


def _ai_author_live_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    intent = payload.get("intent")
    if not actor:
        return _execute_error("ai_assisted_live_authoring", "actor is required")
    if not isinstance(intent, dict):
        return _execute_error("ai_assisted_live_authoring", "intent object is required")
    try:
        return build_live_authoring_draft(
            Path(repo_root or REPO_ROOT),
            intent,
            endpoint_ref=str(payload.get("endpoint_ref") or "").strip(),
            credential_ref=str(payload.get("credential_ref") or "").strip(),
            model_ref=str(payload.get("model_ref") or "").strip(),
            actor=actor,
            provider_ref=str(payload.get("provider_ref") or "provider-neutral").strip(),
            request_config_ref=str(payload.get("request_config_ref") or "live-default").strip(),
            request_timeout_seconds=int(payload.get("request_timeout_seconds") or 30),
            max_output_tokens=int(payload.get("max_output_tokens") or 768),
        )
    except Exception as exc:
        return _execute_error("ai_assisted_live_authoring", str(exc))


def _ai_author_live_confirm_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    preview = payload.get("preview")
    if not actor:
        return _execute_error("ai_assisted_live_authoring", "actor is required")
    if not isinstance(preview, dict):
        return _execute_error("ai_assisted_live_authoring", "preview object is required")
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", False)) else None
    try:
        return confirm_live_authoring_draft(
            Path(repo_root or REPO_ROOT),
            preview,
            actor=actor,
            owner_confirmation_token=owner_token,
        )
    except Exception as exc:
        return _execute_error("ai_assisted_live_authoring", str(exc))


def _agent_profile_draft_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    profile_payload = payload.get("payload")
    if not actor:
        return _execute_error("custom_agent_profile_write", "actor is required")
    if not isinstance(profile_payload, dict):
        return _execute_error("custom_agent_profile_write", "payload object is required")
    try:
        return build_profile_draft(Path(repo_root or REPO_ROOT), profile_payload, actor=actor)
    except Exception as exc:
        return _execute_error("custom_agent_profile_write", str(exc))


def _agent_profile_confirm_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    actor = str(payload.get("actor") or "").strip()
    preview = payload.get("preview")
    if not actor:
        return _execute_error("custom_agent_profile_write", "actor is required")
    if not isinstance(preview, dict):
        return _execute_error("custom_agent_profile_write", "preview object is required")
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", False)) else None
    try:
        return confirm_profile_draft(
            Path(repo_root or REPO_ROOT),
            preview,
            actor=actor,
            owner_confirmation_token=owner_token,
        )
    except Exception as exc:
        return _execute_error("custom_agent_profile_write", str(exc))


def _workspace_init_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    """AIPOS-277: Server-side workspace init + board_config registration."""
    from tools.aipos_cli.workspace_templates import execute_workspace_init
    
    project_id = str(payload.get("project_id") or "").strip()
    label_en = str(payload.get("label_en") or "").strip()  # AIPOS-288 FIX-5: optional English name
    if not project_id:
        return blocked_response(
            operation="workspace_init",
            dry_run=False,
            category="VALIDATION_ERROR",
            message="project_id is required",
        )
    
    if not re.match(r"^[a-z0-9_-]+$", project_id):
        return blocked_response(
            operation="workspace_init",
            dry_run=False,
            category="VALIDATION_ERROR",
            message="project_id must use lowercase letters, numbers, dash, or underscore",
        )
    
    # Default workspace path
    home = Path.home()
    output_path = home / ".lybra" / "workspaces" / project_id
    
    if output_path.exists() and any(output_path.iterdir()):
        return blocked_response(
            operation="workspace_init",
            dry_run=False,
            category="VALIDATION_ERROR",
            message=f"Workspace path already exists and is not empty: {output_path}",
        )
    
    try:
        # Execute workspace init with blank template
        result = execute_workspace_init(
            template="blank",
            output=output_path,
            variables={"project_id": project_id},
            actor="board.server",
            template_repo_root=REPO_ROOT,
        )
        
        if not result.get("ok"):
            return result
        
        # Append to board_config.json (追加不重写)
        board_config_path = (repo_root or REPO_ROOT) / ".lybra" / "board_config.json"
        board_config_path.parent.mkdir(parents=True, exist_ok=True)
        
        workspaces = []
        if board_config_path.exists():
            try:
                data = json.loads(board_config_path.read_text(encoding="utf-8"))
                workspaces = data.get("workspaces", [])
            except (json.JSONDecodeError, OSError):
                pass
        
        # Check if already registered
        existing = any(ws.get("root") == str(output_path) for ws in workspaces)
        if not existing:
            # AIPOS-288 FIX-5: write label_en if provided
            ws_entry = {
                "label": project_id.replace("-", " ").replace("_", " ").title(),
                "root": str(output_path)
            }
            if label_en:
                ws_entry["label_en"] = label_en
            workspaces.append(ws_entry)
            board_config_path.write_text(
                json.dumps({"workspaces": workspaces}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8"
            )
        
        result["board_config_updated"] = not existing
        result["workspace_path"] = str(output_path)
        return result
        
    except Exception as exc:
        return blocked_response(
            operation="workspace_init",
            dry_run=False,
            category="EXECUTION_ERROR",
            message=str(exc),
        )


# ---------------------------------------------------------------------------
# AIPOS-293 FIX-1: Error code → i18n key mapping for humanized errors
# ---------------------------------------------------------------------------

# Maps backend error codes to i18n keys. Frontend uses these keys to display
# localized, actionable error messages. "Unknown error" is forbidden.
_ERROR_CODE_TO_I18N: dict[str, str] = {
    "path_required": "error.import.path_required",
    "path_not_exists": "error.import.path_not_exists",
    "path_not_directory": "error.import.path_not_directory",
    "path_not_file": "error.import.path_not_file",
    "path_not_yaml": "error.import.path_not_yaml",
    "file_read_failed": "error.import.file_read_failed",
    "schema_validation_failed": "error.import.schema_validation_failed",
    "project_id_required": "error.import.project_id_required",
    "project_id_invalid": "error.import.project_id_invalid",
    "workspace_not_empty": "error.import.workspace_not_empty",
    "export_failed": "error.import.export_failed",
    "import_failed": "error.import.import_failed",
    "unexpected_error": "error.import.unexpected_error",
}


def _humanized_error(code: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    """Return a structured error response with i18n error code.

    AIPOS-293 FIX-1: All errors must carry an error_code that maps to an i18n key.
    The 'Unknown error' string is forbidden — every error path must use this helper
    or include an explicit error_code.
    """
    i18n_key = _ERROR_CODE_TO_I18N.get(code, "error.import.unexpected_error")
    result: dict[str, Any] = {
        "ok": False,
        "error_code": code,
        "error_i18n_key": i18n_key,
        "error_detail": detail,
    }
    result.update(extra)
    return result


def _project_structure_preview_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    """AIPOS-293 S4 + FIX-1: Preview structure from directory OR structure file.

    Supports two modes:
    - mode="directory" (default): Export structure from an existing workspace directory
    - mode="file": Read and validate a YAML structure file directly
    """
    from tools.aipos_cli.project_structure import (
        export_project_structure,
        validate_structure,
        parse_yaml,
        _check_no_credentials,
    )

    mode = str(payload.get("mode") or "directory").strip().lower()
    workspace_path = str(payload.get("workspace_path") or "").strip()
    structure_file_path = str(payload.get("structure_file_path") or "").strip()

    # --- Mode: file (AIPOS-293 FIX-1: direct structure file import) ---
    if mode == "file":
        if not structure_file_path:
            return {
                **_humanized_error("path_required", "Structure file path is required"),
                "operation": "project_structure_preview",
            }

        fp = Path(structure_file_path).expanduser().resolve()
        if not fp.exists():
            return {
                **_humanized_error("path_not_exists", str(fp)),
                "operation": "project_structure_preview",
            }
        if not fp.is_file():
            return {
                **_humanized_error("path_not_file", str(fp)),
                "operation": "project_structure_preview",
            }
        if fp.suffix.lower() not in (".yaml", ".yml"):
            return {
                **_humanized_error("path_not_yaml", str(fp)),
                "operation": "project_structure_preview",
            }

        try:
            yaml_text = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {
                **_humanized_error("file_read_failed", str(exc)),
                "operation": "project_structure_preview",
            }

        try:
            structure = parse_yaml(yaml_text)
        except Exception as exc:
            return {
                **_humanized_error("schema_validation_failed", str(exc)),
                "operation": "project_structure_preview",
            }

        errors = validate_structure(structure)
        if errors:
            return {
                **_humanized_error("schema_validation_failed", "; ".join(errors)),
                "operation": "project_structure_preview",
                "validation_errors": errors,
            }

        # Credential safety check (red line)
        cred_findings = _check_no_credentials(structure)
        if cred_findings:
            return {
                **_humanized_error("schema_validation_failed", "Credential values detected"),
                "operation": "project_structure_preview",
                "validation_errors": cred_findings,
            }

        return {
            "ok": True,
            "operation": "project_structure_preview",
            "mode": "file",
            "source_file": str(fp),
            "project_name": structure.get("project_name"),
            "description": structure.get("description", ""),
            "code_repos": structure.get("code_repos", []),
            "doc_count": len(structure.get("doc_manifest", [])),
            "governance_files": list(structure.get("governance_files", {}).keys()),
            "queue_summary": structure.get("queue_summary", {}),
            "roles": structure.get("roles", []),
        }

    # --- Mode: directory (default, existing behavior) ---
    if not workspace_path:
        return {
            **_humanized_error("path_required", "Workspace path is required"),
            "operation": "project_structure_preview",
        }

    ws = Path(workspace_path).expanduser().resolve()
    if not ws.exists():
        return {
            **_humanized_error("path_not_exists", str(ws)),
            "operation": "project_structure_preview",
        }
    if not ws.is_dir():
        # AIPOS-293 FIX-1: Smart suggestion — if user typed a .yaml path, suggest file mode
        if ws.suffix.lower() in (".yaml", ".yml"):
            return {
                **_humanized_error("path_not_directory", str(ws)),
                "operation": "project_structure_preview",
                "suggest_file_mode": True,
            }
        return {
            **_humanized_error("path_not_directory", str(ws)),
            "operation": "project_structure_preview",
        }

    try:
        structure = export_project_structure(ws)
        errors = validate_structure(structure)
        if errors:
            return {
                **_humanized_error("schema_validation_failed", "; ".join(errors)),
                "operation": "project_structure_preview",
                "validation_errors": errors,
            }
        return {
            "ok": True,
            "operation": "project_structure_preview",
            "mode": "directory",
            "project_name": structure.get("project_name"),
            "description": structure.get("description", ""),
            "code_repos": structure.get("code_repos", []),
            "doc_count": len(structure.get("doc_manifest", [])),
            "governance_files": list(structure.get("governance_files", {}).keys()),
            "queue_summary": structure.get("queue_summary", {}),
            "roles": structure.get("roles", []),
        }
    except Exception as exc:
        return {
            **_humanized_error("unexpected_error", str(exc)),
            "operation": "project_structure_preview",
        }


def _project_structure_import_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    """AIPOS-293 S4 + FIX-1: Import workspace from directory OR structure file.

    Supports two modes:
    - mode="directory" (default): Export from directory then import (existing behavior)
    - mode="file": Import directly from a YAML structure file (new in FIX-1)
    """
    from tools.aipos_cli.project_structure import export_project_to_yaml, import_project_structure

    mode = str(payload.get("mode") or "directory").strip().lower()
    workspace_path = str(payload.get("workspace_path") or "").strip()
    structure_file_path = str(payload.get("structure_file_path") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    label_en = str(payload.get("label_en") or "").strip()

    # --- Common validation ---
    if not project_id:
        return {
            **_humanized_error("project_id_required", "project_id is required"),
            "operation": "project_structure_import",
        }
    if not re.match(r"^[a-z0-9_-]+$", project_id):
        return {
            **_humanized_error("project_id_invalid", "project_id must use lowercase letters, numbers, dash, or underscore"),
            "operation": "project_structure_import",
        }

    # Target workspace path
    home = Path.home()
    output_path = home / ".lybra" / "workspaces" / project_id

    if output_path.exists() and any(output_path.iterdir()):
        return {
            **_humanized_error("workspace_not_empty", f"Target workspace already exists and is not empty: {output_path}"),
            "operation": "project_structure_import",
        }

    try:
        # --- Mode: file (AIPOS-293 FIX-1: direct structure file import) ---
        if mode == "file":
            if not structure_file_path:
                return {
                    **_humanized_error("path_required", "Structure file path is required"),
                    "operation": "project_structure_import",
                }

            fp = Path(structure_file_path).expanduser().resolve()
            if not fp.exists():
                return {
                    **_humanized_error("path_not_exists", str(fp)),
                    "operation": "project_structure_import",
                }
            if not fp.is_file():
                return {
                    **_humanized_error("path_not_file", str(fp)),
                    "operation": "project_structure_import",
                }
            if fp.suffix.lower() not in (".yaml", ".yml"):
                return {
                    **_humanized_error("path_not_yaml", str(fp)),
                    "operation": "project_structure_import",
                }

            # Import directly from the structure file
            import_result = import_project_structure(fp, output_path, actor="board.import-wizard")
            if not import_result.get("ok"):
                blocking = import_result.get("blocking_reasons", "unknown")
                if isinstance(blocking, list):
                    blocking = "; ".join(blocking)
                return {
                    **_humanized_error("import_failed", str(blocking)),
                    "operation": "project_structure_import",
                }

        # --- Mode: directory (default, existing behavior) ---
        else:
            if not workspace_path:
                return {
                    **_humanized_error("path_required", "Workspace path is required"),
                    "operation": "project_structure_import",
                }

            ws = Path(workspace_path).expanduser().resolve()
            if not ws.exists():
                return {
                    **_humanized_error("path_not_exists", str(ws)),
                    "operation": "project_structure_import",
                }
            if not ws.is_dir():
                if ws.suffix.lower() in (".yaml", ".yml"):
                    return {
                        **_humanized_error("path_not_directory", str(ws)),
                        "operation": "project_structure_import",
                        "suggest_file_mode": True,
                    }
                return {
                    **_humanized_error("path_not_directory", str(ws)),
                    "operation": "project_structure_import",
                }

            # Step 1: Export source workspace to a temp YAML file
            import tempfile as _tempfile
            tmp_yaml = Path(_tempfile.mkdtemp(prefix="aipos293_import_")) / "lybra-project.yaml"
            export_result = export_project_to_yaml(ws, project_name=project_id, output_path=tmp_yaml)
            if not export_result.get("ok"):
                blocking = export_result.get("blocking_reasons", "unknown")
                if isinstance(blocking, list):
                    blocking = "; ".join(blocking)
                return {
                    **_humanized_error("export_failed", str(blocking)),
                    "operation": "project_structure_import",
                }

            # Step 2: Import from structure file to target
            import_result = import_project_structure(tmp_yaml, output_path, actor="board.import-wizard")
            if not import_result.get("ok"):
                blocking = import_result.get("blocking_reasons", "unknown")
                if isinstance(blocking, list):
                    blocking = "; ".join(blocking)
                return {
                    **_humanized_error("import_failed", str(blocking)),
                    "operation": "project_structure_import",
                }

        # Step 3: Register in board_config.json (shared by both modes)
        board_config_path = (repo_root or REPO_ROOT) / ".lybra" / "board_config.json"
        board_config_path.parent.mkdir(parents=True, exist_ok=True)
        workspaces = []
        if board_config_path.exists():
            try:
                data = json.loads(board_config_path.read_text(encoding="utf-8"))
                workspaces = data.get("workspaces", [])
            except (json.JSONDecodeError, OSError):
                pass
        existing = any(ws_entry.get("root") == str(output_path) for ws_entry in workspaces)
        if not existing:
            ws_entry = {
                "label": project_id.replace("-", " ").replace("_", " ").title(),
                "root": str(output_path),
            }
            if label_en:
                ws_entry["label_en"] = label_en
            workspaces.append(ws_entry)
            board_config_path.write_text(
                json.dumps({"workspaces": workspaces}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        import_result["board_config_updated"] = not existing
        import_result["workspace_path"] = str(output_path)
        import_result["mode"] = mode
        return import_result

    except Exception as exc:
        return {
            **_humanized_error("unexpected_error", str(exc)),
            "operation": "project_structure_import",
        }


def _execute_dry_run_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    operation = str(payload.get("operation") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    if not actor:
        return _execute_error(operation or "execute_dry_run", "actor is required")
    if operation == "draft_create":
        draft_payload = payload.get("payload")
        if not isinstance(draft_payload, dict):
            return _execute_error("draft_create", "payload object is required")
        return create_draft(draft_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "draft_publish":
        path = str(payload.get("path") or "").strip()
        if not path:
            return _execute_error("draft_publish", "path is required")
        return publish_draft(path=path, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "orchestration_event_append":
        event_payload = payload.get("payload")
        if not isinstance(event_payload, dict):
            return _execute_error("orchestration_event_append", "payload object is required")
        return append_orchestration_event(event_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "planner_iteration_append":
        iteration_payload = payload.get("payload")
        if not isinstance(iteration_payload, dict):
            return _execute_error("planner_iteration_append", "payload object is required")
        return append_planner_iteration(iteration_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "owner_decision_record":
        decision_payload = payload.get("payload")
        if not isinstance(decision_payload, dict):
            return _execute_error("owner_decision_record", "payload object is required")
        return record_owner_decision(decision_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation == "owner_verification_record":
        verification_payload = payload.get("payload")
        if not isinstance(verification_payload, dict):
            return _execute_error("owner_verification_record", "payload object is required")
        return record_owner_verification(verification_payload, dry_run=True, repo_root=repo_root, actor=actor)
    if operation != "queue_claim":
        return _execute_error(
            "execute_dry_run",
            "Only queue_claim, draft_create, draft_publish, orchestration_event_append, planner_iteration_append, owner_decision_record, and owner_verification_record are enabled in the controlled execute API",
        )
    task_id = str(payload.get("task_id") or "").strip() or None
    path = str(payload.get("path") or "").strip() or None
    if bool(task_id) == bool(path):
        return _execute_error("queue_claim", "Exactly one of task_id or path is required")
    if bool(payload.get("with_records", False)):
        return _execute_error("queue_claim", "with_records execute is not enabled in the AIPOS-55 UI")
    return claim_task(task_id=task_id, path=path, actor=actor, dry_run=True, with_records=False, repo_root=repo_root)


def _execute_confirm_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    dry_run_id = str(payload.get("dry_run_id") or "").strip()
    actor = str(payload.get("actor") or "").strip()
    if not dry_run_id:
        return _execute_error("execute_dry_run", "dry_run_id is required")
    if not actor:
        return _execute_error("execute_dry_run", "actor is required")
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", False)) else None
    return execute_dry_run(dry_run_id, actor, owner_confirmation_token=owner_token, repo_root=repo_root)


def _owner_verification_approve_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    """AIPOS-273: Owner verification approve route (POST /api/verify/<task_id>/approve).
    
    Wrapper for the controlled execute flow: builds payload -> dry-run -> confirm.
    """
    task_id = str(payload.get("task_id") or "").strip()
    actor = str(payload.get("actor") or "owner").strip()
    if not task_id:
        return _execute_error("owner_verification_record", "task_id is required")
    
    verification_payload = {
        "task_id": task_id,
        "decision": "approve",
        "reason": "",  # approve doesn't require reason
        "decided_via": "web_session",
    }
    
    # Step 1: dry-run to get the plan
    dry_run_response = record_owner_verification(
        verification_payload,
        dry_run=True,
        repo_root=repo_root,
        actor=actor,
    )
    
    if not dry_run_response.get("ok") or dry_run_response.get("verdict") == "BLOCK":
        return dry_run_response
    
    # Step 2: extract dry_run_id and confirm
    dry_run_id = dry_run_response.get("dry_run_id")
    if not dry_run_id:
        return _execute_error("owner_verification_record", "Failed to register dry-run")
    
    # Step 3: execute with owner_confirm
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", True)) else None
    return execute_dry_run(dry_run_id, actor, owner_confirmation_token=owner_token, repo_root=repo_root)


def _owner_verification_reject_route(payload: dict[str, Any], *, repo_root: Path | None) -> dict[str, Any]:
    """AIPOS-273: Owner verification reject route (POST /api/verify/<task_id>/reject).
    
    Wrapper for the controlled execute flow: builds payload -> dry-run -> confirm.
    Requires reason field.
    """
    task_id = str(payload.get("task_id") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    actor = str(payload.get("actor") or "owner").strip()
    
    if not task_id:
        return _execute_error("owner_verification_record", "task_id is required")
    if not reason:
        return _execute_error("owner_verification_record", "reason is required for reject decision")
    
    verification_payload = {
        "task_id": task_id,
        "decision": "reject",
        "reason": reason,
        "decided_via": "web_session",
    }
    
    # Step 1: dry-run to get the plan
    dry_run_response = record_owner_verification(
        verification_payload,
        dry_run=True,
        repo_root=repo_root,
        actor=actor,
    )
    
    if not dry_run_response.get("ok") or dry_run_response.get("verdict") == "BLOCK":
        return dry_run_response
    
    # Step 2: extract dry_run_id and confirm
    dry_run_id = dry_run_response.get("dry_run_id")
    if not dry_run_id:
        return _execute_error("owner_verification_record", "Failed to register dry-run")
    
    # Step 3: execute with owner_confirm
    owner_token = OWNER_CONFIRMATION_TOKEN if bool(payload.get("owner_confirmed", True)) else None
    return execute_dry_run(dry_run_id, actor, owner_confirmation_token=owner_token, repo_root=repo_root)


def dispatch_api_request(
    *,
    method: str,
    path: str,
    routes: dict[str, Callable[[dict[str, list[str]]], dict[str, Any]]],
    post_routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    clean_path = parsed.path
    params = parse_qs(parsed.query, keep_blank_values=True)
    if method == "POST" and post_routes and clean_path in post_routes:
        return int(HTTPStatus.OK), post_routes[clean_path](body or {})
    if method != "GET":
        return (
            int(HTTPStatus.METHOD_NOT_ALLOWED),
            {
                "ok": False,
                "verdict": "BLOCK",
                "error": "METHOD_NOT_ALLOWED",
                "message": "Read-only API. Only GET is supported.",
            },
        )
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

    AIPOS-272 FIX-2: 首选 board 启动参数 repo_root 自身的 .lybra/connection.json,
    次选全局 ~/.lybra/local/connection.json(单机默认工作区场景)。
    不再枚举 .board_config.json 配置的其他工作区(部署路径不得入产品代码;避免跨工作区认证串门)。
    仅返回真实存在的文件,去重。"""
    candidates: list[Path] = []
    # 首选:board 启动时明确声明的工作区
    if repo_root is not None:
        candidates.append(repo_root / ".lybra" / "connection.json")
    # 次选:单机默认全局运行时
    candidates.append(Path("~/.lybra/local/connection.json").expanduser())
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
    """
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
            if not isinstance(item, dict):
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

    def create(self, *, role: str, scopes: list[str], token_ref: str = "") -> str:
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = {
                "role": role,
                "is_owner": role == "owner",
                "scopes": list(scopes),
                "token_ref": token_ref,
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
            "created_at": _utc_now(),
        }
    
    return True


def make_handler(
    repo_root: Path | None = None,
    *,
    board_config_path: Path | None = None,
    session_store: SessionStore | None = None,
    connection_paths: list[Path] | None = None,
    otc_store: OTCStore | None = None,
    device_store: DeviceCodeStore | None = None,
    auth_log_path: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    routes = _api_routes(repo_root, board_config_path)
    post_routes = _api_post_routes(repo_root)
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

    class BoardHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        _sessions = sessions
        _conn_paths = conn_paths
        _otc = otc
        _device = device
        _auth_log_path = log_path
        _remember_secret = remember_secret

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
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                dispatch_api_request(method="POST", path="/api/health", routes=routes)[1],
            )

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
                role=info["role"], scopes=info["scopes"], token_ref=info.get("token_ref", "")
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

        def do_GET(self) -> None:  # noqa: N802
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

            # AIPOS-251: Overview page as new root
            if path == "/":
                self._send_file(STATIC_DIR / "overview.html")
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
            if path in post_routes:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                _status, result = dispatch_api_request(
                    method="POST",
                    path=self.path,
                    routes=routes,
                    post_routes=post_routes,
                    body=body,
                )
                self._send_json(HTTPStatus.OK, result)
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


def run_server(host: str = "127.0.0.1", port: int = 7117, repo_root: Path | None = None, board_config_path: Path | None = None) -> None:
    handler = make_handler(repo_root=repo_root, board_config_path=board_config_path)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"AIPOS board local UI listening on http://{host}:{port}")
        httpd.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local read-only board UI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7117)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--board-config", default=None, help="Path to board_config.json (AIPOS-272 FIX-3)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root_arg = args.repo_root or os.environ.get("AIPOS_WORKSPACE_ROOT")
    repo_root = Path(repo_root_arg).expanduser().resolve() if repo_root_arg else None
    board_config_arg = args.board_config
    board_config_path = Path(board_config_arg).expanduser().resolve() if board_config_arg else None
    run_server(host=str(args.host), port=int(args.port), repo_root=repo_root, board_config_path=board_config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
