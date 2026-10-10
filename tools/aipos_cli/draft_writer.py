from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from tools.aipos_cli.clock import iso_z
from tools.aipos_cli.record_writer import record_dir
from pathlib import Path
from typing import Any

from tools.aipos_cli.draft_validator import (



    DRAFTS_DIR,
    pending_queue_dir,
    draft_slug,
    expected_pending_relative_path,
    find_case_insensitive_path_collision,
    read_draft_markdown,
    resolve_draft_path,
    resolve_pending_target_path,
    validate_draft_metadata,
)
from tools.aipos_cli.records import expected_publish_record_path
from tools.aipos_cli.record_writer import (
    CARD_FRONTMATTER_ORDER,
    card_field_defaults,
    render_frontmatter_block,
    render_markdown as _render_markdown_single_source,
)
from tools.aipos_cli.task_complexity import validate_task_complexity

# AIPOS-R8C: card_policy placeholder fields for draft create
try:
    from tools.card_policy_loader import get_card_policy_placeholder_fields
    CARD_POLICY_AVAILABLE = True
except ImportError:
    CARD_POLICY_AVAILABLE = False


def _check_project_map_staleness(repo_root: Path, validation: dict[str, Any]) -> None:
    """AIPOS-276: project-map staleness check (publish gate warning hook).
    
    If project-map.md exists and has an 'updated' field that is >3 days older
    than the most recent return record (收编), append a warning to validation["warnings"].
    Non-blocking; gracefully degrades if map absent, no updated field, or no returns.
    """
    map_path = repo_root / "governance" / "project-map.md"
    if not map_path.is_file():
        return  # no map = no check
    
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    def _note(warning: str) -> None:
        if warning not in validation["warnings"]:
            validation["warnings"].append(warning)

    try:
        # AIPOS-F100 件②: 地图与 return 记录「必须读出」; 读不出不再静默跳过检查, 而是出声点名(仍只告警, 不挡发布)
        try:
            meta, _body = require_frontmatter(map_path)
        except FrontmatterReadError as exc:
            _note(f"PROJECT_MAP_UNREADABLE (新鲜度未检查: {exc})")
            return
        map_updated_str = str(meta.get("updated") or "").strip()
        if not map_updated_str:
            return  # no updated field = no check
        
        # Parse map updated timestamp
        map_updated = datetime.fromisoformat(map_updated_str.replace("Z", "+00:00"))
        if map_updated.tzinfo is None:
            map_updated = map_updated.replace(tzinfo=timezone.utc)
        
        # Find most recent return record (收编 = finalized delivery)
        returns_root = record_dir(repo_root, "returns")
        if not returns_root.exists():
            return  # no returns = no check
        
        most_recent_return: datetime | None = None
        unreadable: list[str] = []
        for task_dir in returns_root.iterdir():
            if not task_dir.is_dir():
                continue
            for record_file in task_dir.glob("*.md"):
                try:
                    record_meta, _record_body = require_frontmatter(record_file)
                except FrontmatterReadError as exc:
                    unreadable.append(str(exc))
                    continue
                returned_at_str = str(record_meta.get("returned_at") or record_meta.get("created_at") or "").strip()
                if not returned_at_str:
                    continue
                try:
                    returned_at = datetime.fromisoformat(returned_at_str.replace("Z", "+00:00"))
                except ValueError:
                    unreadable.append(f"{record_file}: returned_at 不是 ISO 时间 {returned_at_str!r}")
                    continue
                if returned_at.tzinfo is None:
                    returned_at = returned_at.replace(tzinfo=timezone.utc)
                if most_recent_return is None or returned_at > most_recent_return:
                    most_recent_return = returned_at
        if unreadable:
            _note(f"PROJECT_MAP_STALENESS_PARTIAL ({len(unreadable)} 份 return 记录读不出, 新鲜度只按可读记录判; 首份: {unreadable[0]})")
        
        if most_recent_return is None:
            return  # no valid return records = no check
        
        # Check staleness: map_updated is >3 days before most_recent_return
        delta = most_recent_return - map_updated
        if delta.total_seconds() > 3 * 24 * 3600:
            map_date = map_updated.strftime("%Y-%m-%d")
            return_date = most_recent_return.strftime("%Y-%m-%d")
            warning = f"PROJECT_MAP_STALE (地图更新于 {map_date}, 最近收编 {return_date})"
            if warning not in validation["warnings"]:
                validation["warnings"].append(warning)
    
    except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
        # Graceful degradation: staleness check is advisory, never fails publish
        import warnings
        warnings.warn(f"PROJECT_MAP staleness check failed: {exc}")

EXTERNAL_INTAKE_EXECUTION_ASSIGNED_TO = "agent-01"
EXTERNAL_INTAKE_EXECUTION_OUTPUT_TARGET = "workspace_artifacts/external_intake"

# AIPOS-F108 件①③: 草稿模板缺省值原写死在本模块 DEFAULT_TEMPLATE_VALUES(含不存在的项目 ID "ai-project-os"), 已退役:
# 字段缺省读 card.schema fields.<键>.default(record_writer.card_field_defaults 投影); project 缺省读治理根
# project.json#project(workspace_config.declared_project_id), 缺则拒(DRAFT_PROJECT_UNDECLARED)。

# AIPOS-F87 件①: 卡字段序唯一定义在 record_writer.CARD_FRONTMATTER_ORDER(原本模块另有 28 键一份, 已退役)。
FRONTMATTER_ORDER = CARD_FRONTMATTER_ORDER


def _record_frontmatter(metadata: dict[str, Any], order: list[str]) -> str:
    """AIPOS-F46/F87: 收敛到单源 record_writer.render_frontmatter_block(原以 render_markdown 渲染后再 split 截取, 已退役)。"""
    return render_frontmatter_block(metadata, order) + "\n"


def render_markdown_task_card(metadata: dict[str, Any], body: str) -> str:
    """AIPOS-F46: 收敛到 F22B 单源 (record_writer.render_markdown).

    原实现用本地 _yaml_scalar 拼接, 不处理 **bold**/"/full-width colon 等毒字段.
    """
    return _render_markdown_single_source(metadata, body, FRONTMATTER_ORDER)


def stable_publish_id(task_id: str) -> str:
    return f"publish_{draft_slug(task_id)}"


def render_publish_record(
    *,
    task_id: str,
    publish_id: str,
    actor: str | None,
    source_draft_ref: str,
    published_task_ref: str,
    source_sha256: str,
    published_sha256: str,
    published_at: str,
    confirmer: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> str:
    # AIPOS-204 / F-c4: a gated publish records WHO approved the publish, mirroring the
    # AIPOS-199 claim/return confirmer attribution. `published_by` is the publisher
    # (who drafted/initiated); confirmer_* is the Owner who confirmed the gate. The
    # raw token is never recorded — only the non-secret role/ref/fingerprint. §9
    # signing fields are placeholders (per-op nonce/signature stays deferred).
    confirmer = confirmer if isinstance(confirmer, dict) else {}
    warnings = warnings if isinstance(warnings, list) else []
    metadata = {
        "record_type": RecordType.PUBLISH_RECORD,
        "task_id": task_id,
        "publish_id": publish_id,
        "actor": actor or "unknown",
        "published_by": actor or "unknown",
        "source_draft_ref": source_draft_ref,
        "published_task_ref": published_task_ref,
        "source_sha256": source_sha256,
        "published_sha256": published_sha256,
        "published_at": published_at,
        "created_at": published_at,
        "confirmer_role": confirmer.get("confirmer_role"),
        "confirmer_token_ref": confirmer.get("confirmer_token_ref"),
        "confirmer_token_fingerprint": confirmer.get("confirmer_token_fingerprint"),
        "gate_signature": confirmer.get("gate_signature"),
        "authority_seal": confirmer.get("authority_seal"),
        "signature_key_ref": confirmer.get("signature_key_ref"),
        "signed_payload_hash": confirmer.get("signed_payload_hash"),
        "signed_at": confirmer.get("signed_at"),
        "warnings": warnings if warnings else None,
    }
    body = "\n".join(
        [
            "## Publish Provenance",
            "",
            f"- Source draft: `{source_draft_ref}`",
            f"- Published task: `{published_task_ref}`",
            "- Authority: `draft_publish` controlled gate",
            "",
        ]
    )
    return _record_frontmatter(
        metadata,
        [
            "record_type",
            "task_id",
            "publish_id",
            "actor",
            "published_by",
            "source_draft_ref",
            "published_task_ref",
            "source_sha256",
            "published_sha256",
            "published_at",
            "created_at",
            "confirmer_role",
            "confirmer_token_ref",
            "confirmer_token_fingerprint",
            "gate_signature",
            "authority_seal",
            "signature_key_ref",
            "signed_payload_hash",
            "signed_at",
            "warnings",
        ],
    ) + body


def default_draft_body() -> str:
    return "\n".join(
        [
            "## Goal",
            "",
            "- Describe the concrete task goal.",
            "",
            "## Context",
            "",
            "- Add relevant constraints, links, or prior decisions.",
            "",
            "## Acceptance Criteria",
            "",
            "- Define the minimum observable outcomes.",
            "",
            "## Completion Report Instructions",
            "",
            "- Summarize what changed, what was verified, and any remaining risks.",
            "",
        ]
    )


def load_create_payload_from_json(path: str | Path) -> tuple[dict[str, Any], str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Draft JSON payload must be an object")
    frontmatter = data.get("frontmatter")
    if not isinstance(frontmatter, dict):
        raise ValueError("Draft JSON payload must include a frontmatter object")
    body = data.get("body", default_draft_body())
    if body is None:
        body = default_draft_body()
    if not isinstance(body, str):
        raise ValueError("Draft JSON body must be a string when provided")
    return dict(frontmatter), body


def build_template_payload(template_name: str, values: dict[str, Any], body: str | None = None) -> tuple[dict[str, Any], str]:
    if template_name != "basic":
        raise ValueError(f"Unsupported draft template: {template_name}")
    metadata = {**card_field_defaults(), **values}
    return metadata, body if body is not None else default_draft_body()


def load_body_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _fill_declared_project(metadata: dict[str, Any], repo_root: Path) -> list[str]:
    """AIPOS-F108 件③(H6): 草稿未给 project = 读治理根 project.json#project(workspace_config.declared_project_id);
    治理根未声明 = 不填并返回拒因(fail-closed, 禁回落任何写死项目 ID)。返回拒因列表(空 = 已有或已填)。"""
    if metadata.get("project") not in (None, ""):
        return []
    from tools.aipos_cli.workspace_config import declared_project_id

    try:
        metadata["project"] = declared_project_id(repo_root)
    except ValueError as exc:
        metadata.pop("project", None)
        return [str(exc)]
    return []


def _normalized_metadata(metadata: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    normalized = dict(metadata)
    # AIPOS-F108 件①: 缺省值读 card.schema fields.<键>.default(单源), 不写死
    _defaults = card_field_defaults()
    normalized.setdefault("status", _defaults["status"])
    normalized.setdefault("needs_owner", _defaults["needs_owner"])
    if normalized.get("task_class") in (None, ""):
        normalized["task_class"] = _defaults["task_class"]
    if normalized.get("complexity_note") in (None, ""):
        normalized.pop("complexity_note", None)
    
    # AIPOS-F68: Derive machine zone from schema (single source)
    # Legacy fields below are kept for backward compatibility during transition
    task_id = normalized.get("task_id")
    created_by = normalized.get("created_by")
    timestamp = iso_z()
    if isinstance(task_id, str) and task_id:
        normalized.setdefault("draft_id", f"draft_{draft_slug(task_id)}")
    normalized.setdefault("draft_status", "draft")
    if created_by not in (None, ""):
        normalized.setdefault("draft_created_by", created_by)
    normalized.setdefault("draft_created_at", timestamp)
    normalized.setdefault("draft_updated_at", timestamp)
    # AIPOS-F89 件① M8: 发布落点读队列根声明(task_loader.queue_state_ref), 与 machine_zone 派生同一来源
    from tools.aipos_cli.task_loader import queue_state_ref

    normalized.setdefault("draft_publish_target", queue_state_ref(repo_root, "pending"))
    return normalized


def _is_external_intake_draft(source_path: Path, repo_root: Path, metadata: dict[str, Any]) -> bool:
    try:
        rel_parts = source_path.resolve().relative_to((repo_root / DRAFTS_DIR / "external_intake").resolve()).parts
        if rel_parts:
            return True
    except ValueError:
        pass
    return metadata.get("context_bundle") == "external_intake" or metadata.get("draft_id", "").startswith("external_intake_")


def _external_intake_execution_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata)
    title = str(updated.get("title") or "")
    prefix = "Review external intake: "
    if title.startswith(prefix):
        updated["title"] = title[len(prefix) :]
    updated["assigned_to"] = updated.get("handoff_assigned_to") or EXTERNAL_INTAKE_EXECUTION_ASSIGNED_TO
    updated["agent_instance"] = updated.get("handoff_agent_instance") or updated["assigned_to"]
    updated["context_bundle"] = "external_intake_execution"
    updated["task_mode"] = "coding"
    updated["model_tier"] = updated.get("model_tier") or "L2"
    updated["needs_owner"] = False
    updated["output_target"] = updated.get("handoff_output_target") or EXTERNAL_INTAKE_EXECUTION_OUTPUT_TARGET
    updated["artifact_policy"] = "formal_write"
    updated["polling_mode"] = "agent_polling"
    updated["claim_policy"] = "assigned_agent_only"
    updated["report_mode"] = "completion_summary"
    updated["handoff_source"] = "external_intake"
    updated["owner_review_completed"] = True
    return updated


# ---------------------------------------------------------------------------
# AIPOS-F143 件②③: 执行方式声明(project.json execution, 唯一读取口 workspace_config.project_execution)驱动的起草缺省 + 发卡核身份
# (已接入判定唯一实现 enrollment.instance_enrollment)。起草与发卡同读一处, 禁另建身份表 / 第二份缺省。
# ---------------------------------------------------------------------------

def _execution_or_reasons(repo_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError, project_execution

    try:
        return project_execution(repo_root), []
    except ExecutionDeclarationError as exc:
        return None, [f"AIPOS-F143: project.json execution 声明不合, 拒(出口: lybra project set-execution 重声明): {exc}"]
    except (OSError, ValueError) as exc:
        return None, [f"AIPOS-F143: project.json 读不出, 执行方式声明不可得(fail-closed): {exc}"]


def _pi_mode_refusal(execution: dict[str, Any], task_mode: str) -> str:
    """卡要派 pi 执行工位: task_mode 须在 pi_allowed_task_modes(Owner 明示)且声明了 pi_executor; 否则拒因(EXECUTION_PI_NOT_ALLOWED)。"""
    from tools.aipos_cli.workspace_config import execution_declaration

    if task_mode in execution["pi_allowed_task_modes"] and execution["pi_executor"]:
        return ""
    codes = execution_declaration()["reject_codes"]
    return (f"AIPOS-F143 EXECUTION_PI_NOT_ALLOWED: task_mode={task_mode or '(缺)'} 不在 execution.pi_allowed_task_modes "
            f"{execution['pi_allowed_task_modes']}(pi_executor={execution['pi_executor'] or '未声明'})。{codes['EXECUTION_PI_NOT_ALLOWED']}")


def apply_execution_defaults(metadata: dict[str, Any], repo_root: Path) -> list[str]:
    """AIPOS-F143 件②: 起草缺省按执行方式声明补 assigned_to / agent_instance / harness / audit_by(只补缺, 已给原样保留)。
    返回拒因(空 = 通过)。未声明 execution = 不动(行为不变)。
      - 派 pi 仅当卡稿显式要 pi(harness=pi 或 assigned_to=pi_executor)且 task_mode ∈ pi_allowed_task_modes(Owner 明示)→ 执行者 = pi_executor;
        否则拒 EXECUTION_PI_NOT_ALLOWED(未列明 = 按缺省, 不静默改派)。
      - 其余 = 缺省子 agent: assigned_to 缺 → subagent_executor; harness 缺且执行者是 subagent_executor → 其子 agent 模式接入登记记下的
        顾问会话 kind(enrollment.instance_enrollment; 登记里没有 = 拒, 出口 = 卡稿显式给 harness 或按向导 --executor-mode subagent 接入)。
      - audit_by 缺且卡要审计(audit ≠ none)→ auditor(审计恒为声明的独立 pi 审计工位)。"""
    execution, reasons = _execution_or_reasons(repo_root)
    if reasons or execution is None:
        return reasons
    from tools.aipos_cli.enrollment import instance_enrollment

    task_mode = str(metadata.get("task_mode") or "").strip()
    harness = str(metadata.get("harness") or "").strip()
    assigned = str(metadata.get("assigned_to") or "").strip()
    wants_pi = harness == "pi" or (not harness and bool(assigned) and assigned == execution["pi_executor"])
    if wants_pi:
        refusal = _pi_mode_refusal(execution, task_mode)
        if refusal:
            return [refusal]
        metadata["assigned_to"] = assigned or execution["pi_executor"]
        metadata["harness"] = "pi"
    else:
        metadata["assigned_to"] = assigned or execution["subagent_executor"]
        if not harness and metadata["assigned_to"] == execution["subagent_executor"]:
            try:
                view = instance_enrollment(repo_root, execution["subagent_executor"])
            except ValueError as exc:
                return [f"AIPOS-F143: 治理根连接文件读不出, 子 agent 执行者接入登记不可得: {exc}"]
            if not view.get("harness"):
                return [f"AIPOS-F143: 子 agent 执行者 {execution['subagent_executor']} 无子 agent 模式接入登记(land 事件无 harness=), "
                        "顾问会话 harness 不可得。出口: 卡稿显式给 harness(claude-code|codex), 或按 lybra onboarding guide "
                        "--executor-mode subagent 重接入该实例"]
            metadata["harness"] = view["harness"]
    if not str(metadata.get("agent_instance") or "").strip():
        metadata["agent_instance"] = metadata["assigned_to"]
    if str(metadata.get("audit") or "").strip().lower() != "none" and not str(metadata.get("audit_by") or "").strip():
        metadata["audit_by"] = execution["auditor"]
    return []


def multi_repo_lane_refusal(repo_root: Path, metadata: dict[str, Any]) -> str:
    """AIPOS-F148 件①(gap #123): 多仓项目发卡必写 lane.repo 的唯一判据——project.json repos.items ≥ 2 而卡稿 lane.repo 缺 =
    拒因原文(LANE_REPO_REQUIRED, 列出仓名); 单仓项目(无 repos 段 / items 仅 1 项)或已写 lane.repo = ""(行为不变)。
    仓清单只读唯一读取口 workspace_config.project_repos; 清单自身不一致(REPOS_CONFLICT)/ project.json 读不出 = 拒因原文(fail-closed)。
    metadata = 卡稿原样 frontmatter(须在意图面派生之前取: 派生会按 repos.default 补 lane.repo)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos

    lane = metadata.get("lane") if isinstance(metadata.get("lane"), dict) else {}
    if str(lane.get("repo") or "").strip():
        return ""
    try:
        repos = project_repos(repo_root)
    except CardRepoUnresolved as exc:
        return f"{exc.code}: {exc.reason}(多仓判定不可得, fail-closed)"
    except (OSError, ValueError) as exc:
        return f"LANE_REPO_REQUIRED: project.json 读不出, 多仓判定不可得(fail-closed): {exc}"
    names = sorted(repos["items"])
    if len(names) < 2:
        return ""
    return (f"LANE_REPO_REQUIRED: 本项目声明了 {len(names)} 个产品仓 {names}(project.json repos.items), 卡稿未写 lane.repo——"
            f"多仓项目不按 repos.default({repos['default']})缺省派生, 卡须写明属于哪个仓。"
            f"出口: 草稿 frontmatter 写 lane.repo: <{' | '.join(names)}>(一卡一仓, 跨仓改动拆卡)后重发")


def publish_identity_refusals(repo_root: Path, metadata: dict[str, Any], *,
                              draft_metadata: dict[str, Any] | None = None) -> tuple[list[str], dict[str, Any]]:
    """AIPOS-F143 件③: 发卡核身份——assigned_to 与审计者须为本项目已接入实例(enrollment.instance_enrollment 唯一判定);
    harness=pi 的执行者须有工位位置; 审计者恒为独立 pi 工位(须有工位位置); 声明了 execution 时派 pi 须在 pi_allowed_task_modes。
    适用 = 声明了 execution 或本项目有接入登记(enrollment_log, enrollment.has_enrollment_registry); 都没有(裸治理根 / 夹具)= 不适用, 行为不变。
    审计者 = 卡面 audit_by; 缺而 task_mode=code 且 audit ≠ none = 派审时将用的实例(audit_derivation.resolve_audit_instance 唯一解析)。
    返回 (拒因, 核验视图)。拒因末条列已接入实例与 set-execution 出口。
    AIPOS-F148 件①: 同一校验入口先判多仓项目卡稿是否写了 lane.repo(multi_repo_lane_refusal; 不受上面的适用条件限制——
    裸治理根声明了多仓同样适用); draft_metadata = 意图面派生前的卡稿 frontmatter(缺省 = metadata)。"""
    from tools.aipos_cli.enrollment import enrolled_instances_line, has_enrollment_registry, instance_enrollment

    lane_refusal = multi_repo_lane_refusal(repo_root, draft_metadata if draft_metadata is not None else metadata)
    lane_refusals = [lane_refusal] if lane_refusal else []
    execution, reasons = _execution_or_reasons(repo_root)
    if reasons:
        return [*lane_refusals, *reasons], {"applied": True}
    if execution is None and not has_enrollment_registry(repo_root):
        return lane_refusals, {"applied": False, "reason": "本项目无接入登记且未声明 execution(裸治理根), 发卡核身份不适用"}
    harness = str(metadata.get("harness") or "").strip()
    task_mode = str(metadata.get("task_mode") or "").strip()
    refusals: list[str] = []
    if execution is not None and harness == "pi":
        refusal = _pi_mode_refusal(execution, task_mode)
        if refusal:
            refusals.append(refusal)
    checks: list[tuple[str, str, bool]] = [("assigned_to", str(metadata.get("assigned_to") or "").strip(), harness == "pi")]
    auditor = str(metadata.get("audit_by") or "").strip()
    if not auditor and task_mode == "code" and str(metadata.get("audit") or "").strip().lower() != "none":
        from tools.aipos_cli.audit_derivation import resolve_audit_instance

        try:
            auditor = resolve_audit_instance(metadata, repo_root)
        except ValueError as exc:
            refusals.append(f"AIPOS-F143: 卡无 audit_by 且审计实例推导不出: {exc}")
    if auditor:
        checks.append(("audit_by" if metadata.get("audit_by") else "audit_by(缺省推导)", auditor, True))
    view: dict[str, Any] = {"applied": True, "instances": {}}
    refusals = [*lane_refusals, *refusals]
    for key, name, needs_workstation in checks:
        if not name:
            refusals.append(f"AIPOS-F143: {key} 缺, 发卡须指明本项目已接入的执行者")
            continue
        try:
            status = instance_enrollment(repo_root, name)
        except ValueError as exc:
            refusals.append(f"AIPOS-F143: 治理根连接文件读不出, {key}={name} 接入状态不可得(fail-closed): {exc}")
            continue
        view["instances"][key] = {"instance": name, "enrolled": status["enrolled"], "via": status["via"],
                                  "workstation": bool(status["workstation"].get("found")), "mode": status["mode"]}
        if not status["enrolled"]:
            refusals.append(f"AIPOS-F143 身份未接入: {key}={name} 不是本项目已接入实例({status['reason']})")
        elif needs_workstation and not status["workstation"].get("found"):
            role = "审计者恒为独立 pi 工位" if key.startswith("audit_by") else "harness=pi 的执行者须有 pi 工位"
            refusals.append(f"AIPOS-F143 无工位位置: {key}={name} 已接入但无工位位置({status['workstation'].get('reason')}); {role}")
    if refusals[len(lane_refusals):]:
        refusals.append(
            f"{enrolled_instances_line(repo_root)}。出口: 卡稿 assigned_to / audit_by 改写为已接入实例; 或声明执行方式后由起草缺省填写: "
            f"lybra project set-execution --workspace-root {repo_root} --subagent-executor <已接入子 agent 执行者> --auditor <已接入 pi 审计工位> "
            "[--pi-executor <pi 执行工位> --pi-allowed-task-mode <task_mode>](--dry-run 预演 / --confirm 写入); "
            "新实例先按 lybra onboarding guide 接入(子 agent 执行者: --executor-mode subagent)")
    return refusals, view


def create_draft(
    repo_root: Path,
    metadata: dict[str, Any],
    body: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    # AIPOS-R8C: pre-populate placeholder fields from project card_policy
    if CARD_POLICY_AVAILABLE:
        placeholders = get_card_policy_placeholder_fields(
            governance_root=repo_root, repo_root=None
        )
        for field_name, placeholder_value in placeholders.items():
            if field_name not in metadata or metadata[field_name] in (None, ""):
                metadata[field_name] = placeholder_value

    project_reasons = _fill_declared_project(metadata, repo_root)
    # AIPOS-F143 件②: 执行方式声明 → 缺省执行者 / harness / 审计者(未声明 = 不动; 先于 harness 派生, 派生只补仍缺的)
    project_reasons = [*project_reasons, *apply_execution_defaults(metadata, repo_root)]
    normalized = _normalized_metadata(metadata, repo_root)
    
    # AIPOS-F78 件①: 意图面 harness/lane 缺则派生(create/publish/regen 三口一函数 derive_intent_declarations)
    _intent_warnings: list[str] = []
    try:
        from tools.aipos_cli.machine_zone import derive_intent_declarations
        from tools.schema_loader import SchemaLoadError as _IntentSchemaLoadError

        _intent = derive_intent_declarations(normalized, repo_root)
        if not _intent["blocking_reasons"]:
            normalized.setdefault("harness", _intent["harness"])
            # AIPOS-F148 件①: 多仓项目卡稿缺 lane.repo 时起草不替卡写 repos.default(否则发卡判据被起草缺省绕过); 只出声
            _lane_refusal = multi_repo_lane_refusal(repo_root, normalized)
            if _lane_refusal:
                _intent_warnings.append(_lane_refusal)
                normalized.setdefault("lane", {k: v for k, v in _intent["lane"].items() if k != "repo"})
            else:
                normalized.setdefault("lane", _intent["lane"])
        else:
            _intent_warnings.extend(_intent["blocking_reasons"])
    except _IntentSchemaLoadError as e:
        _intent_warnings.append(f"harness/lane 派生失败(声明缺失): {e}")
    
    # AIPOS-F76 件①: Inject machine-generated 工作纪律 section into body
    # Use the single-source derive function (F68 established, now wiring to create path)
    task_body = body or default_draft_body()
    task_id = normalized.get("task_id")
    if task_id:
        try:
            from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
            from tools.schema_loader import SchemaLoadError
            # F76-R2: governance_root for path resolution, product_root=None for schema auto-detect
            discipline_section = derive_machine_zone_纪律段(
                task_id, normalized, governance_root=repo_root, product_root=None
            )
            # Append discipline section to body if not already present
            if "## 工作纪律" not in task_body:
                task_body = task_body.rstrip() + "\n\n" + discipline_section + "\n"
        except SchemaLoadError as e:
            # F76-R2: schema declaration missing → warn but don't block (存量兼容)
            # AIPOS-F115 件①(gap #63): 原写 result.setdefault(...) 而 result 此处尚未定义 → NameError 掀翻整个 create_draft;
            # 改入本函数既有告警汇集点 _intent_warnings(下方并入 result["warnings"], 应答可见)。
            _intent_warnings.append(f"工作纪律节派生失败(存量兼容): {e}")
    
    rendered_markdown = render_markdown_task_card(normalized, task_body)
    validation = validate_draft_metadata(repo_root, normalized)
    if project_reasons:
        # AIPOS-F108 件③: 项目未声明 = 拒(与 schema 必填校验同一 BLOCK 出口, 拒因带出口)
        validation["blocking_reasons"] = [*project_reasons, *validation["blocking_reasons"]]
        validation["verdict"] = Verdict.BLOCK
    target_path = validation["target_path"]
    planned_writes = []

    if target_path:
        planned_writes.append(
            {
                "path": target_path,
                "kind": "create",
                "type": "draft_markdown",
            }
        )

    result: dict[str, Any] = {
        "action": "draft_create",
        "dry_run": dry_run,
        "task_id": normalized.get("task_id"),
        "verdict": validation["verdict"],
        "blocking_reasons": validation["blocking_reasons"],
        "warnings": [*validation["warnings"], *_intent_warnings],
        "classification_warnings": list(validation.get("classification_warnings", [])),
        "target_path": target_path,
        "planned_writes": planned_writes,
    }

    if dry_run:
        result["would_write"] = validation["verdict"] != Verdict.BLOCK and bool(target_path)
        result["rendered_markdown"] = rendered_markdown
        return result

    if validation["verdict"] == Verdict.BLOCK or not target_path:
        result["wrote"] = False
        return result

    drafts_root = repo_root / DRAFTS_DIR
    target_file = repo_root / target_path
    if target_file.exists():
        result["verdict"] = Verdict.BLOCK
        result["wrote"] = False
        result["blocking_reasons"] = [*result["blocking_reasons"], f"Draft file already exists: {target_path}"]
        return result

    drafts_root.mkdir(parents=True, exist_ok=True)
    target_file.write_text(rendered_markdown, encoding="utf-8")
    result["wrote"] = True
    return result


def registered_gate_verbs() -> set[str]:
    """AIPOS-F78 前置零⑧: 门动词全名集合 = verbs.schema verbs[*] 中 surface 含 mcp 的键(card.schema intent_face.gate_verb_check.match)。"""
    from tools.schema_loader import load_schema

    verbs = load_schema("verbs").get("verbs") or {}
    return {
        name for name, spec in verbs.items()
        if isinstance(spec, dict) and "mcp" in [str(x) for x in (spec.get("surface") or [])]
    }


def find_gate_verbs_in_intent_body(body: str) -> set[str]:
    """AIPOS-F78 前置零⑧: 在意图面正文(frontmatter 之外)整词匹配注册的 MCP 门动词全名。"""
    import re

    names = registered_gate_verbs()
    if not names or not body:
        return set()
    found: set[str] = set()
    for token in re.findall(r"lybra_\w+", body):
        if token in names:
            found.add(token)
    return found


def _workspace_gate_url(repo_root: Path) -> str:
    """AIPOS-338 S1: delegate to the single-source connection reader."""
    from tools.aipos_cli.gate_contract_section import workspace_gate_url
    return workspace_gate_url(repo_root)


class ContractSectionError(RuntimeError):
    """AIPOS-343: raised when the contract section cannot be generated.

    The error message includes diagnostic information about which step failed
    and how to fix it, so publish fails loudly instead of producing a silent card.
    """


def _manual_gate_mode(repo_root: Path | None) -> bool:
    """AIPOS-F73C 件①: 人肉 gate 项目在治理工作区 project.json 声明 manual_gate_mode=true。

    声明为真时执行类卡面保留「认领与交回」节, 且 publish 不做 lybra_ 动词校验(两处同一判据)。
    AIPOS-F80: 读取口收归 workspace_config.project_paths(paths 段优先, 兼容顶层 manual_gate_mode;
    与 next_resolver/ingest 同一读法, 禁第二读法——此前本函数只读顶层, lybra 形 paths.manual_gate_mode 被漏读)。
    读取失败 = warning 非静默, 视为未声明。
    """
    if repo_root is None:
        return False
    from tools.aipos_cli.workspace_config import project_paths
    from tools.schema_loader import SchemaLoadError

    try:
        return bool(project_paths(Path(repo_root))["manual_gate_mode"])
    except (SchemaLoadError, OSError, ValueError, KeyError) as exc:
        import sys

        print(f"Warning: project.json manual_gate_mode unreadable ({repo_root}): {exc}", file=sys.stderr)
        return False


def _card_role_class(metadata: dict[str, Any], repo_root: Path | None) -> str:
    """AIPOS-F73C 件①(顾问代修, Owner 2026-09-08 仲裁 C): 卡 assigned_to/agent_instance → 角色类别。

    AIPOS-F102 件②: 角色 → 类只经唯一实现 custom_roles.resolve_role_to_class; AIPOS-F132: 「实例名 → 角色名」也收口到
    custom_roles.resolve_instance_role_class(门侧裁决/返工授权判定同一函数), 本函数只按候选顺序(assigned_to → agent_instance)取首个可解析者:
      1. 候选本身即角色名(executor/auditor/advisor… 或自定义角色名);
      2. 实例名首段(roles.schema naming.template 唯一解析 parse_instance_name)按注册表 naming.prefix 反查角色名
         (exec./audit./advisor.), 首段不是前缀则按角色名(自定义角色 hbj-coder.<项目>.<机器>);
    禁子串猜(exec/audit in name)。全部候选解析不到 / 注册表读不到 = 拒(UnknownRoleClass, 统一失败语义),
    不再返回 None 被调用方当「非执行体」放行。repo_root = 治理根(自定义角色在门注册表, 内建角色无需)。
    """
    from tools.aipos_cli.custom_roles import UnknownRoleClass, resolve_instance_role_class
    from tools.schema_loader import SchemaLoadError

    candidates = [
        str(metadata.get("assigned_to") or "").strip(),
        str(metadata.get("agent_instance") or "").strip(),
    ]
    try:
        for cand in candidates:
            if not cand:
                continue
            # AIPOS-F132: 实例名 → 角色 → 类收口到唯一实现 custom_roles.resolve_instance_role_class(门侧授权判定同一函数)
            resolved = resolve_instance_role_class(cand, repo_root)
            if resolved:
                return resolved[1]
    except (SchemaLoadError, FileNotFoundError, OSError, json.JSONDecodeError, KeyError) as exc:
        raise UnknownRoleClass(f"角色注册表读不到, 卡角色类不可解析(assigned_to/agent_instance={candidates}): {exc}") from exc
    raise UnknownRoleClass(
        f"卡 assigned_to/agent_instance={candidates} 角色类不可解析(既非 roles.schema 内建角色/实例前缀, 也不在门注册表自定义角色内); "
        "出口: 卡面 assigned_to/agent_instance 写注册表角色的实例名, 或 lybra roles register <name> --class <builtin>"
    )


def card_carries_gate_contract_section(metadata: dict[str, Any], repo_root: Path | None) -> bool:
    """AIPOS-F80 件①: 「哪类卡带认领与交回节」的**唯一判据**(执行卡 / 派生审计卡 / 手动派审卡 / regen 同口径)。

    - 角色类别经 roles 注册表解析(_card_role_class, 禁子串猜)属工位类(roles.schema class_groups.workstation, AIPOS-F102 件②
      读声明)的卡 = 零门卡面: 不带「认领与交回」节、不带门动词提交配方(执行体/审计体只写产物, 认领与裁决提交由驱动方完成, Owner 09-06);
    - 项目声明 manual_gate_mode=true(chris 形人肉 gate)= 两类卡都保留现行为;
    - 其它角色(advisor/planner/…) = 保留; 角色不可解析 = 拒(ContractSectionError, AIPOS-F102 件②: 原「不可解析按保留放行」退役)。
    调用方: _append_gate_contract_section(发布追加)、publish 门动词校验、audit_derivation.build_derived_audit_task
    (派生审计卡)、regen_machine_zone_for_pending(存量卡删节)。禁第二判据。
    """
    from tools.aipos_cli.custom_roles import UnknownRoleClass, role_classes_in_group

    if _manual_gate_mode(repo_root):  # 人肉 gate 项目: 任何角色都带节, 判据与角色类无关(无需解析)
        return True
    try:
        role_class = _card_role_class(metadata, repo_root)
    except UnknownRoleClass as exc:
        raise ContractSectionError(f"AIPOS-F102: 卡面零门判据无据(角色类不可解析, 工作区 {repo_root}), 拒: {exc}") from exc
    return role_class not in role_classes_in_group("workstation")


def _append_gate_contract_section(
    repo_root: Path, metadata: dict[str, Any], task_id: str, rendered_markdown: str
) -> str:
    """AIPOS-338 S1 / AIPOS-343: append the single-source 「认领与交回」 section.

    AIPOS-343: rendering failures are NO LONGER silently swallowed. If the section
    cannot be generated, a ContractSectionError is raised with diagnostic info
    (which step failed, what's missing, how to fix it). The caller (publish_draft)
    propagates this as a BLOCK, so the user gets a clear error instead of a mute card.

    Old cards are not backfilled — only NEW publishes get it.
    
    AIPOS-F73件①: executor/auditor 卡面停止渲染门契约节 (代码保留仅供 manual 项目声明开启).
    判据 = card_carries_gate_contract_section(AIPOS-F80 唯一判据; roles 注册表解析角色类, 禁子串猜).
    """
    if "【认领与交回】" in rendered_markdown:
        return rendered_markdown  # idempotency: never double-append
    
    # AIPOS-F73C件① / F80 件①: executor/auditor 且项目未声明 manual gate → 零门卡面, 跳过渲染
    # 判据唯一实现 card_carries_gate_contract_section(roles 注册表 + manual_gate_mode)。
    if not card_carries_gate_contract_section(metadata, repo_root):
        return rendered_markdown

    from tools.aipos_cli.flow_description import resolve_collaboration_profile
    from tools.aipos_cli.gate_contract_section import render_gate_contract_section

    # AIPOS-343: workspace-agnostic project.json location (no lybra-specific fallback)
    project_json = repo_root / "project.json"
    profile = resolve_collaboration_profile(project_json)

    # AIPOS-F103 件④: 契约节信封按卡面身份 + 本卡判定(autonomy_policy.select_envelope), 带上 project/实例/审计实例
    task_fields = {k: v for k, v in metadata.items() if k in (
        "task_mode", "output_target", "deploy", "audit", "owner_verify", "task_class",
        "project", "assigned_to", "agent_instance", "audit_by",
    )}

    # AIPOS-343: each failure mode gets a specific diagnostic message
    gate_url = _workspace_gate_url(repo_root)

    try:
        section = render_gate_contract_section(
            profile, task_fields, role="executor",
            gate_url=gate_url,
            connection_json_rel=".lybra/connection.json",
            workspace_display=str(repo_root), task_id=task_id,
            workspace_root=repo_root,
        )
    except ValueError as exc:
        # Envelope resolution failed (no active policies, missing workspace_root, etc.)
        raise ContractSectionError(
            f"AIPOS-343: contract section generation failed for task {task_id}. "
            f"Policy envelope resolution error: {exc}\n"
            f"  workspace_root={repo_root}\n"
            f"  Fix: ensure an active, non-expired envelope under the project's policies_root "
            f"(project.json paths.policies_root, default 5_tasks/policies/) whose agent_or_role covers the card's agent_instance."
        ) from exc
    except Exception as exc:
        # Any other unexpected failure — still loud, with context
        raise ContractSectionError(
            f"AIPOS-343: contract section generation failed for task {task_id}. "
            f"Unexpected error during rendering: {type(exc).__name__}: {exc}\n"
            f"  workspace_root={repo_root}\n"
            f"  project_json_exists={project_json.is_file()}\n"
            f"  gate_url={gate_url}\n"
            f"  Fix: check workspace structure (project.json, .lybra/connection.json, policies/)."
        ) from exc

    return rendered_markdown.rstrip() + "\n\n" + section + "\n"


def publish_draft(
    repo_root: Path,
    draft_path: str | Path,
    *,
    dry_run: bool = False,
    actor: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # AIPOS-240 (F-o3-19): resolve once, locally, for symlink-safe repo-relative rendering. macOS
    # /var→/private/var etc. make `source_path` (resolved) mismatch an unresolved `repo_root`. The
    # `repo_root` parameter is left untouched (helpers below expect the caller's form).
    root = repo_root.resolve()
    source_path = resolve_draft_path(repo_root, draft_path)
    source_rel = str(source_path.resolve().relative_to(root))
    validation = {
        "action": "draft_validate",
        "path": str(Path(draft_path)),
        "task_id": None,
        "verdict": Verdict.BLOCK,
        "blocking_reasons": [],
        "warnings": [],
        "frontmatter": {},
    }
    result: dict[str, Any] = {
        "action": "draft_publish",
        "dry_run": dry_run,
        "source_path": source_rel,
        "target_path": None,
        "task_id": None,
        "verdict": Verdict.BLOCK,
        "blocking_reasons": [],
        "warnings": [],
        "planned_writes": [],
        "validation": validation,
    }

    if not source_path.exists():
        reason = f"Draft path does not exist: {draft_path}"
        validation["blocking_reasons"] = [reason]
        result["blocking_reasons"] = [reason]
        result["would_write"] = False
        result["wrote"] = False
        return result
    if not source_path.is_file():
        reason = f"Draft path is not a file: {draft_path}"
        validation["blocking_reasons"] = [reason]
        result["blocking_reasons"] = [reason]
        result["would_write"] = False
        result["wrote"] = False
        return result

    metadata, body, parse_errors = read_draft_markdown(source_path)
    source_markdown = source_path.read_text(encoding="utf-8")
    is_external_intake = _is_external_intake_draft(source_path, repo_root, metadata)
    publish_metadata = _external_intake_execution_metadata(metadata) if is_external_intake else metadata
    rendered_markdown = render_markdown_task_card(publish_metadata, body) if is_external_intake else source_markdown
    validation = validate_draft_metadata(repo_root, metadata, actual_path=source_path, parse_errors=parse_errors)
    # AIPOS-F133 件③: 发布时依赖判据读治理根门生记录(同一 unmet_dependencies; 不再退回卡面自报)
    publish_complexity = validate_task_complexity(publish_metadata, enforce_dependency_gate=True, governance_root=repo_root)
    for reason in publish_complexity["blocking_reasons"]:
        if reason not in validation["blocking_reasons"]:
            validation["blocking_reasons"].append(reason)
    for warning in publish_complexity["warnings"]:
        if warning not in validation["warnings"]:
            validation["warnings"].append(warning)
        if warning not in validation.setdefault("classification_warnings", []):
            validation["classification_warnings"].append(warning)
    
    # AIPOS-276: project-map staleness check (publish gate warning hook)
    # If project-map.md exists and updated > 3 days before most recent return record, warn.
    _check_project_map_staleness(repo_root, validation)
    
    # AIPOS-F68 大项②: Machine zone validation - prevent advisor hand-edits
    # Gracefully degrade if schema not available (存量兼容③)
    # AIPOS-F68 大项②: Machine zone validation - prevent advisor hand-edits
    # Backward compatibility: SchemaLoadError (schema declaration missing) → warning (legacy repos)
    # Other exceptions: re-raise (fail-closed)
    # Validation failures: block (hand-edited machine zone)
    try:
        from tools.aipos_cli.machine_zone import (
            validate_machine_zone_unchanged,
            validate_output_target_coverage,
        )
        from tools.schema_loader import SchemaLoadError
        
        machine_valid, machine_reasons = validate_machine_zone_unchanged(metadata, repo_root)
        if not machine_valid:
            for reason in machine_reasons:
                if reason not in validation["blocking_reasons"]:
                    validation["blocking_reasons"].append(reason)
        
        # AIPOS-F68 大项②: output_target coverage validation
        anchor_refs = metadata.get("anchor_refs", [])
        if not isinstance(anchor_refs, list):
            anchor_refs = []
        governance_refs = metadata.get("governance_refs", [])
        if not isinstance(governance_refs, list):
            governance_refs = []
        all_refs = anchor_refs + governance_refs
        
        coverage_valid, coverage_reasons = validate_output_target_coverage(metadata, all_refs)
        if not coverage_valid:
            for reason in coverage_reasons:
                if reason not in validation["blocking_reasons"]:
                    validation["blocking_reasons"].append(reason)
        
        # AIPOS-F76 件①: Validate 工作纪律 section exists and matches current derivation
        task_id_for_section = metadata.get("task_id")
        if task_id_for_section:
            from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
            # F76-R2: governance_root for path resolution, product_root=None for schema auto-detect
            expected_discipline = derive_machine_zone_纪律段(
                task_id_for_section, metadata, governance_root=repo_root, product_root=None
            )
            
            # Check if section exists in body
            if "## 工作纪律" not in body:
                # Missing section: legacy compatibility warning (存量卡 from before F76)
                validation["warnings"].append(
                    "工作纪律节缺失 (F76前创建的卡)，存量兼容允许发布。"
                    "建议: 重新 draft create 或使用 regen 补充该节。"
                )
            else:
                # Section exists: validate it matches current derivation
                import re
                # Extract the actual discipline section from body
                pattern = r"(## 工作纪律.*?)(?=\n## |\Z)"
                match = re.search(pattern, body, re.DOTALL)
                if match:
                    actual_discipline = match.group(1).strip()
                    expected_discipline_normalized = expected_discipline.strip()
                    
                    if actual_discipline != expected_discipline_normalized:
                        validation["blocking_reasons"].append(
                            "工作纪律节被手改，与 schema 派生不一致。"
                            "机器区由 schema 派生，禁止手动编辑。"
                            "可执行出口: 删除手改内容，重新 draft create 或使用 regen 更新该节"
                        )
    except SchemaLoadError:
        # Legacy compatibility: schema declaration missing (存量卡)
        # Add warning but allow publish to proceed
        validation["warnings"].append(
            "机器区声明缺失 (schema 不完整)，存量兼容跳过机器区校验。"
            "新项目请确保 schema/card.schema.json 包含 machine_zone 声明。"
        )
    
    result["task_id"] = validation["task_id"]
    result["warnings"] = list(validation["warnings"])

    task_id = validation["task_id"]
    if isinstance(task_id, str) and task_id:
        target_path = expected_pending_relative_path(task_id, repo_root)
        target_file = resolve_pending_target_path(repo_root, task_id)
        publish_id = stable_publish_id(task_id)
        publish_record_path = expected_publish_record_path(repo_root, task_id, publish_id)
        publish_record_rel = str(publish_record_path.relative_to(repo_root))
        result["publish_id"] = publish_id
        result["publish_record_path"] = publish_record_rel
        result["target_path"] = target_path
        result["planned_writes"] = [
            {
                "path": target_path,
                "kind": "create",
                "type": "pending_markdown",
            },
            {
                "path": publish_record_rel,
                "kind": "create",
                "type": RecordType.PUBLISH_RECORD,
                "record_type": RecordType.PUBLISH_RECORD,
            }
        ]

        pending_root = pending_queue_dir(repo_root)
        case_collision = find_case_insensitive_path_collision(pending_root, target_file.name)
        if case_collision is not None:
            collision_rel = str(case_collision.resolve().relative_to(repo_root.resolve()))
            if case_collision.resolve() != target_file.resolve():
                validation["blocking_reasons"].append(
                    f"Case-insensitive pending filename collision: {collision_rel}"
                )
            elif target_file.exists():
                validation["blocking_reasons"].append(f"Pending target already exists: {target_path}")
        if publish_record_path.exists():
            validation["blocking_reasons"].append(f"Publish record already exists: {publish_record_rel}")

        # AIPOS-338 S1 / AIPOS-343: append the single-source 「认领与交回」 section.
        # AIPOS-343: failure is now loud — ContractSectionError → BLOCK with diagnostic.
        try:
            rendered_markdown = _append_gate_contract_section(
                repo_root, publish_metadata, str(task_id), rendered_markdown
            )
        except ContractSectionError as exc:
            validation["blocking_reasons"].append(str(exc))
        
        _draft_intent_metadata = publish_metadata  # AIPOS-F148 件①: 意图面派生前的卡稿(多仓 lane.repo 判据读此, 派生会补 default)
        # AIPOS-F78 件①: 意图面 harness/lane 校验——缺则派生(与 create/regen 同一函数), 派生不出(LANE_REQUIRED)= 拒
        try:
            from tools.aipos_cli.machine_zone import derive_intent_declarations
            from tools.schema_loader import SchemaLoadError as _IntentSchemaLoadError

            _intent = derive_intent_declarations(publish_metadata, repo_root)
            if _intent["blocking_reasons"]:
                validation["blocking_reasons"].extend(_intent["blocking_reasons"])
            elif _intent["derived"] and not is_external_intake:
                publish_metadata = dict(publish_metadata)
                publish_metadata["harness"] = _intent["harness"]
                publish_metadata["lane"] = _intent["lane"]
                rendered_markdown = render_markdown_task_card(publish_metadata, body)
                # 纪律段/契约节在上面已按 body 追加过的情况: 重新追加(同一函数, 幂等)
                try:
                    rendered_markdown = _append_gate_contract_section(repo_root, publish_metadata, str(task_id), rendered_markdown)
                except ContractSectionError as exc:
                    validation["blocking_reasons"].append(str(exc))
                validation["warnings"].append(
                    f"AIPOS-F78 件①: 已派生意图面字段 {', '.join(_intent['derived'])}(harness={_intent['harness']}, lane.paths={_intent['lane']['paths']})"
                )
        except _IntentSchemaLoadError as exc:
            validation["blocking_reasons"].append(f"AIPOS-F78 件①: harness/lane 声明缺失, 拒发布: {exc}")

        # AIPOS-F143 件③: 发卡核身份(执行者 / 审计者须为本项目已接入实例; 判定唯一实现 enrollment.instance_enrollment)——
        # 读派生后的意图面(harness 缺省已补), 与起草缺省同读执行方式声明
        # AIPOS-F148 件①: 同一入口先判多仓项目卡稿必写 lane.repo(draft_metadata = 派生前卡稿)
        identity_reasons, result["identity_check"] = publish_identity_refusals(repo_root, publish_metadata,
                                                                               draft_metadata=_draft_intent_metadata)
        for reason in identity_reasons:
            if reason not in validation["blocking_reasons"]:
                validation["blocking_reasons"].append(reason)

        # AIPOS-F73C件①: 校验 executor/auditor 卡面不含门动词 (fail-closed)
        # 角色判据:与渲染侧共用 _card_role_class(顾问代修, 仲裁 C;此前引用不存在的
        # load_roles_schema 且被 except/pass 吞掉, 判据从未生效)。
        # AIPOS-F78 前置零⑧(F79B 实撞: 裸正则 lybra_\w+ 扫整卡把 pol_lybra_dev_9 / governance_refs 里的动词键名当门动词拒):
        # 只匹配 verbs.schema 注册的 MCP 动词全名、整词、只扫意图面正文(body), 排除 frontmatter 的策略 id 与文档性引用。
        # AIPOS-F80 件①: 与渲染侧同一判据 card_carries_gate_contract_section(零门卡面 = 不得含门动词)。
        try:
            _zero_gate_card = not card_carries_gate_contract_section(publish_metadata, repo_root)
        except ContractSectionError as exc:  # AIPOS-F102 件②: 角色类不可解析 = 拒(发布阻塞带出口), 不按"非零门卡"放行
            _zero_gate_card = False
            if str(exc) not in validation["blocking_reasons"]:
                validation["blocking_reasons"].append(str(exc))
        if _zero_gate_card:
            lybra_verbs = find_gate_verbs_in_intent_body(body)
            if lybra_verbs:
                validation["blocking_reasons"].append(
                    f"AIPOS-F73C件①: executor/auditor 卡面不得包含门动词。检测到: {', '.join(sorted(lybra_verbs))}。"
                    "执行体/审计体零门——认领/交回由产品 (lybra next --run) 执行，卡面不再渲染门链。"
                )

    classification_warnings = list(validation.get("classification_warnings", []))
    verdict_warnings = [warning for warning in validation["warnings"] if warning not in classification_warnings]
    result["verdict"] = Verdict.BLOCK if validation["blocking_reasons"] else (Verdict.WARN if verdict_warnings else Verdict.PASS)
    result["blocking_reasons"] = list(validation["blocking_reasons"])
    result["classification_warnings"] = classification_warnings
    result["would_write"] = result["verdict"] != Verdict.BLOCK and bool(result["target_path"])
    result["validation"] = {
        "action": "draft_validate",
        "path": str(source_path.resolve().relative_to(root)),  # AIPOS-240: symlink-safe
        "task_id": validation["task_id"],
        "verdict": result["verdict"],
        "blocking_reasons": list(validation["blocking_reasons"]),
        "warnings": list(validation["warnings"]),
        "frontmatter": metadata,
        "published_frontmatter": publish_metadata,
    }
    if dry_run:
        result["wrote"] = False
        result["rendered_markdown"] = rendered_markdown
        return result

    if result["verdict"] == Verdict.BLOCK or not result["target_path"]:
        result["wrote"] = False
        return result

    pending_root = pending_queue_dir(repo_root)
    pending_root.mkdir(parents=True, exist_ok=True)
    target_file = repo_root / result["target_path"]
    target_file.write_text(rendered_markdown, encoding="utf-8")
    publish_id = str(result["publish_id"])
    publish_record_path = expected_publish_record_path(repo_root, str(task_id), publish_id)
    source_sha256 = hashlib.sha256(source_markdown.encode("utf-8")).hexdigest()
    published_sha256 = hashlib.sha256(rendered_markdown.encode("utf-8")).hexdigest()
    published_at = iso_z()
    publish_markdown = render_publish_record(
        task_id=str(task_id),
        publish_id=publish_id,
        actor=actor,
        source_draft_ref=source_rel,
        published_task_ref=str(result["target_path"]),
        source_sha256=source_sha256,
        published_sha256=published_sha256,
        published_at=published_at,
        confirmer=confirmer,
        warnings=validation["warnings"],
    )
    
    # AIPOS-F64: 统一写入器
    from tools.aipos_cli.record_writer import write_records_atomic
    write_result = write_records_atomic(
        repo_root=repo_root,
        records=[("publish", publish_id, publish_markdown, str(task_id))],  # AIPOS-F109 件①: 显式 key = 卡 ID(与 expected_publish_record_path 同位)
    )
    
    result["wrote"] = True
    result["record_writes"] = [
        {
            "path": write_result["paths"][0],
            "record_type": RecordType.PUBLISH_RECORD,
            "wrote": True,
        }
    ]
    return result


def regen_machine_zone_for_pending(
    governance_root: Path,
    product_root: Path,
    *,
    task_id: str | None = None,
    actor: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """AIPOS-F76 件② (F74 首用两缺修复): 对 pending 存量卡重生成机器纪律段。
    
    对指定任务卡(或所有 pending 卡)重新派生纪律段,
    经门 amend 落卡(留 amendment 记录),顾问区零触碰。
    
    AIPOS-F76 修复:
    1. 按卡阶段派生: pending/claimed 卡禁重置 draft_status (只更新纪律段)
    2. 纪律段缺失时追加(不是只替换既有节)
    3. 三口一函数: derive_machine_zone_纪律段 是唯一派生源
    
    Args:
        governance_root: 治理工作区根 (pending 卡所在位置)
        product_root: 产品仓根 (schema 所在位置，派生机器区的数据源)
        task_id: 指定任务 ID(若缺省则处理所有 pending 卡)
        actor: 执行重生成的 actor
        dry_run: 预览不写入
    
    Returns:
        {
            "verdict": "APPROVE" | "BLOCK",
            "message": str,
            "data": {
                "updated_cards": list[str],  # 更新的卡号列表
                "amendments": list[dict],     # 每张卡的修改详情
            },
            "blocking_reasons": list[str],
        }
    """
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段
    from tools.aipos_cli.board_adapter import amend_task
    
    blocking_reasons = []
    updated_cards = []
    amendments_detail = []
    
    pending_dir = pending_queue_dir(governance_root)
    if not pending_dir.exists():
        return {
            "verdict": "BLOCK",
            "message": f"Pending directory not found: {pending_dir}",
            "blocking_reasons": [f"PENDING_DIR_NOT_FOUND: {pending_dir}"],
            "data": {"updated_cards": [], "amendments": []},
        }
    
    # 确定要处理的任务卡列表
    if task_id:
        # 单张卡
        task_files = []
        for card_file in pending_dir.glob("*.md"):
            try:
                text = card_file.read_text(encoding="utf-8")
                metadata, _body, _warnings = parse_markdown_frontmatter(text)
                if str(metadata.get("task_id") or "").strip().upper() == task_id.upper():
                    task_files.append(card_file)
                    break
            except Exception:
                continue
        
        if not task_files:
            return {
                "verdict": "BLOCK",
                "message": f"Task {task_id} not found in pending",
                "blocking_reasons": [f"TASK_NOT_FOUND: {task_id} not in pending directory"],
                "data": {"updated_cards": [], "amendments": []},
            }
    else:
        # 所有 pending 卡
        task_files = list(pending_dir.glob("*.md"))
    
    # 处理每张卡
    for card_file in task_files:
        try:
            text = card_file.read_text(encoding="utf-8")
            metadata, body, _warnings = parse_markdown_frontmatter(text)
            
            card_task_id = str(metadata.get("task_id") or "").strip()
            if not card_task_id:
                blocking_reasons.append(f"MISSING_TASK_ID: {card_file.name} lacks task_id")
                continue
            
            # AIPOS-F76 件②: 检测卡阶段 (pending/claimed)
            # pending 目录下的卡默认为 pending, 但可能已 claimed (有 claim_id/claimed_by)
            card_stage = "pending"
            if metadata.get("status") == "claimed" or metadata.get("claimed_by") or metadata.get("claim_id"):
                card_stage = "claimed"
            
            # AIPOS-F76 件②: pending/claimed 卡禁重置 draft_status
            # 只更新纪律段, 不触碰 frontmatter 机器区字段
            amendments = {}
            
            # AIPOS-F78 件①: 存量卡补 harness/lane(缺则派生, 同一函数 derive_intent_declarations; 派生不出=出声跳过不阻塞)
            try:
                from tools.aipos_cli.machine_zone import derive_intent_declarations

                intent = derive_intent_declarations(metadata, governance_root, product_root=product_root)
                if intent["blocking_reasons"]:
                    import sys
                    print(f"Warning: {card_task_id}: harness/lane 派生失败: {'; '.join(intent['blocking_reasons'])}", file=sys.stderr)
                else:
                    if not str(metadata.get("harness") or "").strip():
                        amendments["harness"] = intent["harness"]
                    if not isinstance(metadata.get("lane"), dict) or not metadata.get("lane"):
                        # AIPOS-F148 件①: 多仓项目不替存量卡写 repos.default 为 lane.repo(归属含糊), 只出声
                        lane_refusal = multi_repo_lane_refusal(governance_root, metadata)
                        if lane_refusal:
                            import sys
                            print(f"Warning: {card_task_id}: {lane_refusal}", file=sys.stderr)
                            amendments["lane"] = {k: v for k, v in intent["lane"].items() if k != "repo"}
                        else:
                            amendments["lane"] = intent["lane"]
            except Exception as e:  # SchemaLoadError 等: 出声不吞, 不阻塞纪律段重生成
                import sys
                print(f"Warning: {card_task_id}: harness/lane 声明读取失败: {e}", file=sys.stderr)
            
            # 重新派生机器纪律段 (三口一函数: 唯一派生源)
            try:
                # AIPOS-F73C返工⑤ / F80 件①: 存量卡零门收口(新卡不再生成)——判据唯一 card_carries_gate_contract_section
                # (manual_gate_mode 项目保留现行为); 派生审计卡另收口门动词配方节与报告落位句(audit_derivation 单源)。
                import re
                if not card_carries_gate_contract_section(metadata, governance_root):
                    from tools.aipos_cli.audit_derivation import zero_gate_audit_body

                    body_without_gate = re.sub(r"## 【认领与交回】.*?(?=\n## |\Z)", "", body, flags=re.DOTALL)
                    if str(metadata.get("task_mode") or "").strip().lower() == "audit":
                        body_without_gate = zero_gate_audit_body(
                            body_without_gate, governance_root, card_task_id,
                            str(metadata.get("reviewed_task_id") or metadata.get("derived_from") or "").strip() or None)
                    if body_without_gate != body:
                        amendments["body"] = body_without_gate
                        body = body_without_gate

                # F76-R2: governance_root for path resolution, product_root for schema reading
                # (AIPOS-F80: 派生移到零门收口之后——纪律段派生失败不再连带跳过存量卡删节)
                new_discipline_section = derive_machine_zone_纪律段(
                    card_task_id, metadata, governance_root=governance_root, product_root=product_root
                )

                # 查找 body 中是否有旧的纪律段
                if "## 工作纪律" in body:
                    # 已有节: 替换
                    # 匹配从 "## 工作纪律" 到下一个 "##" 或文末
                    pattern = r"(## 工作纪律.*?)(?=\n## |\Z)"
                    new_body = re.sub(pattern, new_discipline_section, body, flags=re.DOTALL)
                    if new_body != body:
                        amendments["body"] = new_body
                        body = new_body
                else:
                    # AIPOS-F76 件②: 缺失时追加 (与 create 同一函数同一节名)
                    new_body = body.rstrip() + "\n\n" + new_discipline_section + "\n"
                    amendments["body"] = new_body
                    body = new_body
            except Exception as e:
                # AIPOS-F73C返工R2件③: 纪律段派生失败,警告但不阻塞
                import sys
                print(f"Warning: Failed to derive machine zone for {card_task_id}: {e}", file=sys.stderr)
            
            if not amendments:
                # 无变化,跳过
                continue
            
            # 使用 amend_task 更新卡
            if not dry_run:
                try:
                    amend_result = amend_task(
                        repo_root=governance_root,
                        task_id=card_task_id,
                        actor=actor,
                        amendments=amendments,
                        amendment_reason=f"AIPOS-F76: 机器区补完 (纪律段写入卡面)",
                        dry_run=False,
                    )
                    
                    if amend_result.get("verdict") == "BLOCK":
                        blocking_reasons.append(
                            f"AMEND_FAILED ({card_task_id}): {amend_result.get('message', 'Unknown error')}"
                        )
                        continue
                except Exception as e:
                    blocking_reasons.append(f"AMEND_FAILED ({card_task_id}): {e}")
                    continue
            
            updated_cards.append(card_task_id)
            amendments_detail.append({
                "task_id": card_task_id,
                "amendments": amendments,
            })
            
        except Exception as e:
            blocking_reasons.append(f"PROCESS_FAILED ({card_file.name}): {e}")
            continue
    
    if blocking_reasons:
        return {
            "verdict": "BLOCK",
            "message": f"Regeneration encountered {len(blocking_reasons)} error(s)",
            "blocking_reasons": blocking_reasons,
            "data": {
                "updated_cards": updated_cards,
                "amendments": amendments_detail,
            },
        }
    
    if not updated_cards:
        return {
            "verdict": "APPROVE",
            "message": "No cards needed regeneration (all machine zones already up-to-date)",
            "blocking_reasons": [],
            "data": {
                "updated_cards": [],
                "amendments": [],
            },
        }
    
    action = "Would update" if dry_run else "Updated"
    return {
        "verdict": "APPROVE",
        "message": f"{action} {len(updated_cards)} card(s) machine zone",
        "blocking_reasons": [],
        "data": {
            "updated_cards": updated_cards,
            "amendments": amendments_detail,
        },
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
from tools.schema_constants import RecordType, Verdict
check_direct_invocation(__name__)
