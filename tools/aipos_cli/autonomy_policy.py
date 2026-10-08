"""AIPOS-250 — owner_autonomy_policy artifact: render + gate reader + envelope matcher.

The FIRST autonomy tier ("PreAuthorized envelope"). An Owner hand-confirms ONE bounded
autonomy envelope (a policy artifact under the project's declared policies_root, default 5_tasks/policies/); at runtime the gate does a
STRUCTURAL match — it never re-decides. Matching is strict AND (task_selector ∧ agent/role
∧ time window ∧ released_count < max_tasks ∧ status==active); any doubt falls back to
Supervised (fail-safe,偏窄). The envelope is bounded on TWO axes: time (expires_at) and
count (max_tasks); reaching either bound drops the claim back to Supervised.

This module is pure/low-level (task_loader + record_writer only) so both the gate handlers
and the owner_decision writer can share it without an import cycle.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from tools.aipos_cli.clock import utc_now
from pathlib import Path
from typing import Any, Iterator

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
from tools.aipos_cli.record_writer import record_root, render_markdown
from tools.schema_constants import RecordType


AUTONOMY_MODE_SUPERVISED = "Supervised"
AUTONOMY_MODE_PREAUTHORIZED = "PreAuthorized"
POLICY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
POLICY_STATUS_ACTIVE = "active"
POLICY_STATUSES = {"active", "expired", "revoked"}

# AIPOS-F9: envelope guard error codes (map to transitions.schema.json envelope_guards)
ENVELOPE_ERROR_POLICY_NOT_FOUND = "ENVELOPE_POLICY_NOT_FOUND"
ENVELOPE_ERROR_NOT_YET_ACTIVE = "ENVELOPE_NOT_YET_ACTIVE"
ENVELOPE_ERROR_EXPIRED = "ENVELOPE_EXPIRED"
ENVELOPE_ERROR_QUOTA_EXHAUSTED = "ENVELOPE_QUOTA_EXHAUSTED"
ENVELOPE_ERROR_SELECTOR_TASK_ID_MISMATCH = "ENVELOPE_SELECTOR_TASK_ID_MISMATCH"
ENVELOPE_ERROR_SELECTOR_TASK_MODE_MISMATCH = "ENVELOPE_SELECTOR_TASK_MODE_MISMATCH"
ENVELOPE_ERROR_SELECTOR_PROJECT_MISMATCH = "ENVELOPE_SELECTOR_PROJECT_MISMATCH"
ENVELOPE_ERROR_SELECTOR_LANE_REPO_MISMATCH = "ENVELOPE_SELECTOR_LANE_REPO_MISMATCH"  # AIPOS-F134 件③
ENVELOPE_ERROR_AGENT_NOT_COVERED = "ENVELOPE_AGENT_NOT_COVERED"
ENVELOPE_ERROR_NOT_APPROVED = "ENVELOPE_NOT_APPROVED"
ENVELOPE_ERROR_STATUS_NOT_ACTIVE = "ENVELOPE_STATUS_NOT_ACTIVE"
ENVELOPE_ERROR_IDENTITY_MISMATCH = "ENVELOPE_IDENTITY_MISMATCH"


# AIPOS-PRERELEASE-1: permanent gate-side audit trace for PreAuthorized envelope decisions.
# Emits one JSON line to stderr (→ journald under lybra-dev-gate.service) so an envelope match
# or a Supervised fallback is observable from gate evidence — not agent self-report. Low
# volume: fires only on PreAuthorized claim attempts (the envelope path).
_ENVELOPE_LOGGER = logging.getLogger("lybra.envelope")
if not _ENVELOPE_LOGGER.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _ENVELOPE_LOGGER.addHandler(_h)
_ENVELOPE_LOGGER.setLevel(logging.INFO)
_ENVELOPE_LOGGER.propagate = False


def trace_envelope(payload: dict[str, Any]) -> None:
    """Emit one structured envelope-decision trace line (JSON). Best-effort: never raises."""
    try:
        line = json.dumps(payload, default=str, sort_keys=True)
    except (TypeError, ValueError) as exc:  # AIPOS-F115 件③: 精确捕获序列化失败并出声(原 except Exception: pass 静默丢迹)
        line = json.dumps({"trace_serialize_error": str(exc), "payload_repr": repr(payload)[:500]}, sort_keys=True)
    # logging 自身的输出错误由 Handler.handleError 处理(不抛), 无需再包
    _ENVELOPE_LOGGER.info("[ENVELOPE_TRACE] " + line)


@contextlib.contextmanager
def envelope_trace_output(enabled: bool) -> Iterator[None]:
    """AIPOS-F138 件③: 只读视图(`lybra loop status`)按开关输出信封判定诊断行 —— enabled=False 时本区段内不打印 [ENVELOPE_TRACE]
    (状态结论不被淹没), 退出区段恢复原状。只开关输出, 不改 trace_envelope 的调用与信封判定逻辑; 门侧(serve)不经本开关, 照旧留痕。"""
    previous = _ENVELOPE_LOGGER.disabled
    _ENVELOPE_LOGGER.disabled = not enabled
    try:
        yield
    finally:
        _ENVELOPE_LOGGER.disabled = previous

# FLAT bounded-map frontmatter (AIPOS-219 P5 idiom: depth-1, readable on bare python).
# task_selector is flattened into three explicit fields; task_ids is a YAML list.
POLICY_FRONTMATTER_ORDER = [
    "record_type",
    "policy_id",
    "mode",
    "status",
    "approved_by_owner",
    "owner_approval_ref",
    "active_from",
    "expires_at",
    "agent_or_role",
    "task_selector_task_mode",
    "task_selector_project",
    "task_selector_task_ids",
    "max_tasks",
    "launch_harnesses",
    "task_selector_lane_repo",  # AIPOS-F134 件③: 只追加; 缺省(键缺)= 不限 lane, 存量信封不变
]


def loop_envelope_declaration() -> dict[str, Any]:
    """verbs.schema lybra_loop.envelope(allowed_verbs / boundary_template 唯一声明)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    envelope = ((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get("lybra_loop") or {}).get("envelope")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("allowed_verbs"), list) \
            or not isinstance(envelope.get("boundary_template"), str):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.envelope(allowed_verbs / boundary_template)未声明")
    return envelope


def launchable_harnesses() -> dict[str, dict[str, Any]]:
    """AIPOS-F95 件②(a): enums.schema harness 中 launch 非 null 的值 → 其 launch 声明(唯一读取口)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    harness = (load_schema("enums", code_repo_schema_root()).get("enums") or {}).get("harness")
    if not isinstance(harness, dict) or not isinstance(harness.get("values"), list):
        raise SchemaLoadError("enums.schema.json enums.harness.values 未声明")
    out: dict[str, dict[str, Any]] = {}
    for item in harness["values"]:
        if isinstance(item, dict) and isinstance(item.get("launch"), dict):
            out[str(item.get("value"))] = dict(item["launch"])
    return out


def render_envelope_boundary(launch_harnesses: list[str] | None) -> str:
    """AIPOS-F95 件②(c): 信封正文 Boundary 由声明渲染(allowed_verbs + launch_harnesses 实值), 唯一渲染。"""
    envelope = loop_envelope_declaration()
    verbs = ", ".join(str(v) for v in envelope["allowed_verbs"])
    harnesses = ", ".join(launch_harnesses or []) or "none (manual /go only)"
    return envelope["boundary_template"].replace("{allowed_verbs}", verbs).replace("{launch_harnesses}", harnesses)


def envelope_authorizes_launch(policy: dict[str, Any] | None, harness: str) -> tuple[bool, str]:
    """AIPOS-F95 件②(b): 信封是否授权 loop 拉起该 harness(同一信封族唯一实现; 信封本身的有效性由 match_claim_envelope 先判)。

    判据: 信封 launch_harnesses 含该 harness(缺省 [] = 不授权)。返回 (授权与否, 原因)。"""
    if not isinstance(policy, dict):
        return False, "无已匹配信封"
    granted = [str(h).strip() for h in (policy.get("launch_harnesses") or []) if str(h).strip()]
    name = str(harness or "").strip()
    if not granted:
        return False, f"信封 {policy.get('policy_id')} 未授权拉起(launch_harnesses 为空 = 只手工 /go)"
    if name not in granted:
        return False, f"信封 {policy.get('policy_id')} launch_harnesses={granted} 不含卡 harness {name!r}"
    return True, f"信封 {policy.get('policy_id')} launch_harnesses 含 {name}"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_autonomy_policy_markdown(
    *,
    policy_id: str,
    agent_or_role: str,
    active_from: str,
    expires_at: str,
    max_tasks: int,
    owner_approval_ref: str,
    task_selector_task_mode: str | None = None,
    task_selector_project: str | None = None,
    task_selector_task_ids: list[str] | None = None,
    status: str = POLICY_STATUS_ACTIVE,
    approved_by_owner: bool = True,
    launch_harnesses: list[str] | None = None,
    task_selector_lane_repo: str | list[str] | None = None,
) -> str:
    """Render an owner_autonomy_policy artifact. Written only through the owner_confirm-gated
    owner_decision_record grant path — the presence of this on-disk artifact IS the Owner's
    one hand-confirmation (red line 1: pre-authorization is not delegation)."""
    metadata = {
        "record_type": RecordType.OWNER_AUTONOMY_POLICY,
        "policy_id": policy_id,
        "mode": AUTONOMY_MODE_PREAUTHORIZED,
        "status": status,
        "approved_by_owner": bool(approved_by_owner),
        "owner_approval_ref": owner_approval_ref,
        "active_from": active_from,
        "expires_at": expires_at,
        "agent_or_role": agent_or_role,
        "task_selector_task_mode": str(task_selector_task_mode or "") or "",
        "task_selector_project": str(task_selector_project or "") or "",
        "task_selector_task_ids": list(task_selector_task_ids or []),
        "max_tasks": int(max_tasks),
        "launch_harnesses": [str(h) for h in (launch_harnesses or [])],
    }
    from tools.aipos_cli.workspace_config import repo_name_set

    # AIPOS-F139: 仓集合唯一序列化 repo_name_set(新铸一律落列表); 空 = 不落键(缺省 = 不限 lane, 渲染与存量信封逐字节同)
    lane_repos = repo_name_set(task_selector_lane_repo)
    if lane_repos:  # AIPOS-F134 件③: 只在给定时落键
        metadata["task_selector_lane_repo"] = lane_repos
    lane_text = ", ".join(f"`{name}`" for name in lane_repos)
    body = "\n".join(
        [
            f"# Owner Autonomy Policy: {policy_id}",
            "",
            "## Envelope",
            "",
            f"- Mode: {AUTONOMY_MODE_PREAUTHORIZED} (one-stage release for the matched envelope only).",
            f"- Covers: `{agent_or_role}`.",
            f"- Active from `{active_from}` until `{expires_at}` (time bound).",
            f"- Max auto-released claims: {max_tasks} (count bound).",
            f"- Owner approval: `{owner_approval_ref}`.",
            f"- Harness launch: {', '.join(launch_harnesses or []) or 'none (manual /go only)'}.",
            *([f"- Lane: cards whose lane.repo resolves to {'one of ' if len(lane_repos) > 1 else ''}{lane_text} only (task_selector_lane_repo)."]
              if lane_repos else []),
            "",
            "## Boundary",
            "",
            # AIPOS-F95 件②(c): 由声明渲染(verbs.schema lybra_loop.envelope.allowed_verbs + 本信封 launch_harnesses), 删陈旧「CLAIM only」
            render_envelope_boundary(launch_harnesses),
            "",
        ]
    )
    return render_markdown(metadata, body, POLICY_FRONTMATTER_ORDER)


def normalize_policy(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce a parsed policy artifact frontmatter into a normalized policy dict, or None if
    it is not a well-formed owner_autonomy_policy."""
    if not isinstance(metadata, dict):
        return None
    if str(metadata.get("record_type") or "").strip() != RecordType.OWNER_AUTONOMY_POLICY:
        return None
    policy_id = str(metadata.get("policy_id") or "").strip()
    if not policy_id or not POLICY_ID_PATTERN.fullmatch(policy_id):
        return None
    task_ids = metadata.get("task_selector_task_ids")
    if not isinstance(task_ids, list):
        task_ids = []
    try:
        max_tasks = int(metadata.get("max_tasks"))
    except (TypeError, ValueError):
        max_tasks = 0
    launch = metadata.get("launch_harnesses")
    if not isinstance(launch, list):
        launch = []
    from tools.aipos_cli.workspace_config import repo_name_set

    try:  # AIPOS-F139: 仓集合唯一解析(单值存量信封 = 单元素集合; 形不合 = 非合规信封, fail-closed 不授权)
        lane_repos = repo_name_set(metadata.get("task_selector_lane_repo"))
    except ValueError:
        return None
    return {
        "policy_id": policy_id,
        "mode": str(metadata.get("mode") or "").strip(),
        "status": str(metadata.get("status") or "").strip(),
        "approved_by_owner": bool(metadata.get("approved_by_owner")),
        "owner_approval_ref": str(metadata.get("owner_approval_ref") or "").strip(),
        "active_from": str(metadata.get("active_from") or "").strip(),
        "expires_at": str(metadata.get("expires_at") or "").strip(),
        "agent_or_role": str(metadata.get("agent_or_role") or "").strip(),
        "task_selector_task_mode": str(metadata.get("task_selector_task_mode") or "").strip(),
        "task_selector_project": str(metadata.get("task_selector_project") or "").strip(),
        "task_selector_task_ids": [str(item).strip() for item in task_ids if str(item).strip()],
        "max_tasks": max_tasks,
        # AIPOS-F95 件②(b): 缺省 [] = 不授权拉起(存量信封无此键)
        "launch_harnesses": [str(item).strip() for item in launch if str(item).strip()],
        # AIPOS-F134 件③ / F139: 仓集合(缺省 [] = 不限 lane, 存量信封无此键; 单值存量 = 单元素集合)
        "task_selector_lane_repo": lane_repos,
    }


def policies_dir(governance_root: Path | str) -> Path:
    """AIPOS-F103 件④: 信封目录唯一读取口 = 项目 project.json paths.policies_root(声明 config.schema
    configuration_sources.project_json.schema.paths.policies_root, 缺省 5_tasks/policies; 相对治理根或绝对),
    经 workspace_config.project_paths 解析(与 return_root/queue_root 等落点同一读取口)。禁写死目录。"""
    from tools.aipos_cli.workspace_config import project_paths

    return Path(project_paths(Path(governance_root))["policies_root"])


def policy_relpath(governance_root: Path | str, policy_id: str) -> str:
    """信封工件相对治理根的路径(门写入面 planned_writes / 决策记录 policy_ref 用)。声明落点在治理根外 = ValueError
    (fail-closed: 门只在治理根内落信封)。"""
    root = Path(governance_root).resolve()
    path = (policies_dir(root) / f"{policy_id}.md").resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"policies_root 声明落在治理根外({path}), 门只在治理根内落信封(project.json paths.policies_root)") from exc


def policy_ids(governance_root: Path | str) -> list[str]:
    """声明信封目录下全部信封 id(文件名去 .md, 升序)。目录不存在 = []。"""
    pdir = policies_dir(governance_root)
    if not pdir.is_dir():
        return []
    return sorted(p.stem for p in pdir.glob("*.md") if p.is_file())


def load_policy(repo_root: Path, policy_id: str) -> dict[str, Any] | None:
    """Read a single policy artifact by id. Returns None on a missing/malformed/forged ref —
    the caller falls back to Supervised (★A1 anti-forgery: a ref to a nonexistent policy grants
    nothing). AIPOS-F103 件④: 目录读项目声明 policies_root(policies_dir)。"""
    pid = str(policy_id or "").strip()
    if not pid or not POLICY_ID_PATTERN.fullmatch(pid):
        return None
    pdir = policies_dir(repo_root).resolve()
    path = (pdir / f"{pid}.md").resolve()
    try:
        path.relative_to(pdir)
    except ValueError:
        return None
    if not path.is_file():
        return None
    metadata, _body, warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    if warnings:
        return None
    policy = normalize_policy(metadata)
    if policy is None or policy["policy_id"] != pid:
        return None
    return policy


def count_preauthorized_claims(repo_root: Path, policy_id: str) -> int:
    """Count already-landed PreAuthorized claim records attributed to this policy. Stateless and
    auditable (R-1 count bound): the gate re-derives the count from truth every match."""
    pid = str(policy_id or "").strip()
    if not pid:
        return 0
    claims_root = (repo_root / record_root("claims")).resolve()
    if not claims_root.is_dir():
        return 0
    count = 0
    for path in claims_root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            metadata, _body, _warn = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("autonomy_mode") or "").strip() != AUTONOMY_MODE_PREAUTHORIZED:
            continue
        if str(metadata.get("owner_policy_ref") or "").strip() == pid:
            count += 1
    return count


def card_lane_refs(governance_root: Path | str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-F134 件③: 卡所在仓的全部指称(信封 task_selector_lane_repo 的判定对象)——解析唯一 workspace_config.resolve_card_repo
    (卡 lane.repo → project.json repos 清单 → 缺省仓), 指称 = {解析路径, 其 resolve 形, 卡面原值, 清单里指向同一路径的仓名}。
    解析不到 = {"refs": [], "error": 拒因原文}(带 lane 选择器的信封据此判不匹配, fail-closed; 无 lane 选择器的信封不调用本函数)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, _same_path, project_repos, resolve_card_repo

    root = Path(governance_root)
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    try:
        path = resolve_card_repo(root, fm)
        repos = project_repos(root)
    except (CardRepoUnresolved, OSError, ValueError) as exc:  # 精确捕获并带出原文(禁静默); 判定落 match_claim_envelope 原因链
        return {"refs": [], "error": f"{type(exc).__name__}: {exc}"}
    refs = [str(path)]
    try:
        refs.append(str(path.resolve()))
    except OSError as exc:
        return {"refs": [], "error": f"OSError: {exc}"}
    lane = fm.get("lane") if isinstance(fm.get("lane"), dict) else {}
    raw = str(lane.get("repo") or "").strip()
    if raw:
        refs.append(raw)
    refs.extend(name for name, item in (repos.get("items") or {}).items() if _same_path(item, path))
    return {"refs": list(dict.fromkeys(refs)), "error": ""}


def envelope_subject(repo_root: Path, *, task_id: str, task_mode: str, project: str, reviewed_task_id: str = "",
                     lane: dict[str, Any] | None = None, with_lane: bool = False
                     ) -> tuple[str, str, str, dict[str, Any] | None]:
    """AIPOS-F90 件①: 信封 task_selector 的判定对象(驱动方 loop_driver.find_envelope 与门 _match_claim_envelope 同读此处)。

    审计卡(task_mode=audit 且声明 reviewed_task_id)= 被审卡: 审计卡由派审从被审卡派生, 其认领是被审卡推进链的一步
    (与裁决/close 按被审卡判信封同口径), 否则 task_selector_task_mode=code 的驱动信封永远认领不了审计卡, loop 停在审计认领。
    被审卡找不到 = 仍按审计卡自身字段判(只窄不宽, fail-safe)。声明: verbs.schema lybra_loop.envelope.subject_rule。
    AIPOS-F134 件③: with_lane=True 时第四项 = 判定对象卡的 lane 指称(card_lane_refs; 审计卡 = 被审卡的 lane, 同一主体规则),
    否则 None(只在有信封带 task_selector_lane_repo 时才解析, 无 lane 信封零额外读取 = 行为不变)。
    返回 (task_id, task_mode, project, lane_refs | None)。"""
    if str(task_mode or "").strip() == "audit" and str(reviewed_task_id or "").strip():
        from tools.aipos_cli.task_loader import find_task_card

        reviewed_path, _queue = find_task_card(Path(repo_root), str(reviewed_task_id).strip())
        if reviewed_path is not None:
            fm, _body, _warnings = parse_markdown_frontmatter(reviewed_path.read_text(encoding="utf-8"))
            if isinstance(fm, dict):
                rid = str(reviewed_task_id).strip()
                lane_refs = card_lane_refs(repo_root, {**fm, "task_id": rid}) if with_lane else None
                return (rid, str(fm.get("task_mode") or ""), str(fm.get("project") or ""), lane_refs)
    lane_refs = None
    if with_lane:
        lane_refs = card_lane_refs(repo_root, {"task_id": str(task_id or ""), "lane": lane if isinstance(lane, dict) else {}})
    return str(task_id or ""), str(task_mode or ""), str(project or ""), lane_refs


def driver_envelope_identities(instance: str | None, role_name: str | None, role_class: str | None
                               ) -> list[tuple[str, str, str | None]]:
    """AIPOS-F134 件①: 驱动方信封身份集合的唯一构造(loop_driver.find_envelope 与门 _match_driver_envelope / _match_claim_envelope 同用)。

    身份 = {驱动方实例(缺则角色类), 角色名(凭据条目 role, 如 hbj-advisor), 角色类(roles.schema driver.role_class)}——信封 agent_or_role
    写其一即覆盖。返回 match_claim_envelope 三元组列表, 按具体程度降序(角色名 → 角色类; select_envelope 据此优先更具体的信封)。"""
    klass = str(role_class or "").strip()
    inst = str(instance or "").strip() or klass
    roles = list(dict.fromkeys(r for r in (str(role_name or "").strip(), klass) if r))
    return [(inst, inst, r) for r in roles] or [(inst, inst, None)]


def match_claim_envelope(
    *,
    policy: dict[str, Any],
    task_id: str,
    task_mode: str,
    project: str,
    agent_instance: str,
    actor: str,
    now: datetime,
    released_count: int,
    claiming_role: str | None = None,
    lane: dict[str, Any] | None = None,
) -> tuple[bool, str, str | None]:
    """Strict-AND envelope match for a claim. Returns (matched, reason, error_code). Every predicate must
    hold; any miss returns matched=False with a human reason and error_code (maps to transitions.schema.json
    envelope_guards). The caller uses matched=True to auto-release (one-stage direct write) and matched=False
    to fall back to Supervised (per-task owner_confirm).

    AIPOS-F9: error_code maps to transitions.schema.json envelope_guards for structured next_step.
    AIPOS-PRERELEASE-1: every predicate's input and judgment is traced to stderr (→ journald)
    as gate-side evidence, so a match or fallback is observable without agent self-report.
    AIPOS-F134 件③: task_selector_lane_repo(缺省 "" = 不限 lane) —— lane = 判定对象卡的仓指称(envelope_subject(with_lane=True) /
    card_lane_refs); 选择器值须在其 refs 内。带 lane 选择器而调用方未给 lane / 卡仓解析不到 = 不匹配(fail-closed, 原因链带原文)。
    AIPOS-F139: 选择器 = 仓集合(workspace_config.repo_name_set 唯一解析; 单值存量信封 = 单元素集合), 集合任一仓在 refs 内即命中;
    不命中原因链列出整个集合。lane 判定只在此处。
    """
    # ── evaluate every predicate (full picture for the trace) ─────────────
    is_dict = isinstance(policy, dict)
    covered = str(policy.get("agent_or_role") or "").strip() if is_dict else ""
    identity = {str(agent_instance or "").strip(), str(actor or "").strip()}
    if claiming_role:
        identity.add(str(claiming_role).strip())
    identity.discard("")

    sel_mode = str(policy.get("task_selector_task_mode") or "").strip() if is_dict else ""
    sel_project = str(policy.get("task_selector_project") or "").strip() if is_dict else ""
    sel_ids = list(policy.get("task_selector_task_ids") or []) if is_dict else []
    # AIPOS-F139: 仓集合唯一解析 workspace_config.repo_name_set(单值存量信封 = 单元素集合); 卡 lane 指称 ∈ 集合任一即命中
    from tools.aipos_cli.workspace_config import repo_name_set

    lane_set_error = ""
    try:
        sel_lane = repo_name_set(policy.get("task_selector_lane_repo")) if is_dict else []
    except ValueError as exc:  # 形不合 = 视为带 lane 限定且不命中(fail-closed, 原因链带原文)
        sel_lane, lane_set_error = [], str(exc)
    lane_refs = [str(r) for r in ((lane or {}).get("refs") or [])] if isinstance(lane, dict) else []
    lane_ok = not lane_set_error and ((not sel_lane) or any(name in lane_refs for name in sel_lane))

    active_from = _parse_iso(policy.get("active_from")) if is_dict else None
    expires_at = _parse_iso(policy.get("expires_at")) if is_dict else None
    tw_parseable = active_from is not None and expires_at is not None

    try:
        max_tasks = int(policy.get("max_tasks") or 0) if is_dict else 0
    except (TypeError, ValueError):
        max_tasks = 0

    predicates = {
        "policy_is_dict": is_dict,
        "mode": is_dict and policy.get("mode") == AUTONOMY_MODE_PREAUTHORIZED,
        "status_active": is_dict and policy.get("status") == POLICY_STATUS_ACTIVE,
        "approved_by_owner": bool(policy.get("approved_by_owner")) if is_dict else False,
        "time_window_parseable": tw_parseable,
        "time_window_active": tw_parseable and (active_from <= now < expires_at),
        "agent_or_role": bool(covered) and covered in identity,
        "task_selector_present": bool(sel_mode or sel_project or sel_ids or sel_lane or lane_set_error),
        "task_id_in_ids": (not sel_ids) or (str(task_id or "").strip() in sel_ids),
        "task_mode": (not sel_mode) or (str(task_mode or "").strip() == sel_mode),
        "project": (not sel_project) or (str(project or "").strip() == sel_project),
        "lane_repo": lane_ok,
        "max_tasks_positive": max_tasks > 0,
        "count_bound": released_count < max_tasks,
    }

    # ── first-failing reason (preserves original short-circuit order & strings) ──
    # AIPOS-F9: each failure case gets an error_code that maps to transitions.schema.json envelope_guards
    error_code = None
    if not is_dict:
        reason = "no policy artifact resolved for owner_policy_ref"
        error_code = ENVELOPE_ERROR_POLICY_NOT_FOUND
    elif policy.get("mode") != AUTONOMY_MODE_PREAUTHORIZED:
        reason = f"policy mode is not {AUTONOMY_MODE_PREAUTHORIZED}"
        error_code = None  # mode mismatch not in envelope_guards (config error)
    elif policy.get("status") != POLICY_STATUS_ACTIVE:
        reason = f"policy status is {policy.get('status') or 'unset'}, not active"
        error_code = ENVELOPE_ERROR_STATUS_NOT_ACTIVE
    elif not policy.get("approved_by_owner"):
        reason = "policy is not approved_by_owner"
        error_code = ENVELOPE_ERROR_NOT_APPROVED
    elif active_from is None or expires_at is None:
        reason = "policy time window is missing or unparseable"
        error_code = None  # unparseable time (config error)
    elif now < active_from:
        reason = f"policy is not yet active (active_from={policy.get('active_from')})"
        error_code = ENVELOPE_ERROR_NOT_YET_ACTIVE
    elif now >= expires_at:
        reason = f"policy has expired (expires_at={policy.get('expires_at')})"
        error_code = ENVELOPE_ERROR_EXPIRED
    elif not covered or covered not in identity:
        reason = "claiming agent/role is not covered by policy.agent_or_role"
        error_code = ENVELOPE_ERROR_AGENT_NOT_COVERED
    elif not (sel_mode or sel_project or sel_ids or sel_lane or lane_set_error):
        reason = "policy task_selector is empty (no wildcard auto-release)"
        error_code = None  # empty selector (config error)
    elif sel_ids and str(task_id or "").strip() not in sel_ids:
        reason = "task_id is not in policy task_selector.task_ids"
        error_code = ENVELOPE_ERROR_SELECTOR_TASK_ID_MISMATCH
    elif sel_mode and str(task_mode or "").strip() != sel_mode:
        reason = "task_mode does not match policy task_selector.task_mode"
        error_code = ENVELOPE_ERROR_SELECTOR_TASK_MODE_MISMATCH
    elif sel_project and str(project or "").strip() != sel_project:
        reason = "project does not match policy task_selector.project"
        error_code = ENVELOPE_ERROR_SELECTOR_PROJECT_MISMATCH
    elif not lane_ok:
        if lane_set_error:
            where = f"policy task_selector.lane_repo unparseable ({lane_set_error})"
        elif not isinstance(lane, dict):
            where = "card lane not provided to the matcher"
        elif lane.get("error"):
            where = f"card repo unresolvable ({lane.get('error')})"
        else:
            where = f"card lane.repo resolves to {lane_refs}"
        reason = f"{where}; does not match policy task_selector.lane_repo={sel_lane!r} (any of the set)"
        error_code = ENVELOPE_ERROR_SELECTOR_LANE_REPO_MISMATCH
    elif max_tasks <= 0:
        reason = "policy max_tasks is not a positive bound"
        error_code = None  # invalid max_tasks (config error)
    elif released_count >= max_tasks:
        reason = f"policy count bound reached ({released_count}/{max_tasks})"
        error_code = ENVELOPE_ERROR_QUOTA_EXHAUSTED
    else:
        reason = f"matched policy {policy.get('policy_id')} (released {released_count}/{max_tasks})"
        error_code = None  # success, no error

    matched = all(predicates.values())
    trace_envelope({
        "phase": "match_claim_envelope",
        "policy_id": policy.get("policy_id") if is_dict else None,
        "task_id": task_id,
        "inputs": {
            "task_mode": task_mode,
            "project": project,
            "agent_instance": agent_instance,
            "actor": actor,
            "claiming_role": claiming_role,
            "now": now.isoformat(),
            "released_count": released_count,
            "policy_agent_or_role": covered,
            "policy_task_selector": {"task_mode": sel_mode, "project": sel_project, "task_ids": sel_ids, "lane_repo": sel_lane},
            "lane": lane,
            "policy_active_from": policy.get("active_from") if is_dict else None,
            "policy_expires_at": policy.get("expires_at") if is_dict else None,
            "policy_max_tasks": max_tasks,
        },
        "predicates": predicates,
        "matched": matched,
        "reason": reason,
        "error_code": error_code,
    })
    return matched, reason, error_code


def select_envelope(
    governance_root: Path | str,
    *,
    identities: list[tuple[str | None, str | None, str | None]],
    task: dict[str, Any] | None = None,
    policy_id: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """AIPOS-F103 件④: 信封挑选唯一实现——判据只有 match_claim_envelope(禁第二套有效性/覆盖/选择器判定)。

    - identities: 身份三元组 (agent_instance, actor, claiming_role) 列表, 按具体程度降序(实例/角色名 → 角色类);
      每个三元组原样交 match_claim_envelope(其身份集合 = 三者去空)。
    - task: 判定对象 {task_id, task_mode, project, reviewed_task_id?, lane?}; 经 envelope_subject(审计卡 = 被审卡, 门与 loop 同一规则);
      AIPOS-F134 件③: 候选信封有 task_selector_lane_repo 时才解析卡 lane 指称(card_lane_refs)。
      None = 任务无关(工位 .lybra/role 推导、凭据上下文自发现等无卡场景): 判定对象取该信封自身 task_selector
      (= 「该信封覆盖某张卡」), 其余谓词(有效/时间窗/身份覆盖/选择器非空/额度)照常由 match_claim_envelope 判。
    - policy_id: 给定 = 只核该信封(同一判据重核, 不另挑); 缺省 = 扫描声明目录 policies_dir 全部信封。
    挑选次序(确定性): 身份三元组次序优先(更具体者先), 同级按信封 id 升序取首个匹配。
    返回 (policy | None, 未匹配原因列表)。"""
    root = Path(governance_root)
    now = now or utc_now()
    triples = [
        (str(a or "").strip(), str(b or "").strip(), (str(c).strip() or None) if c else None)
        for a, b, c in identities
    ]
    triples = [t for t in triples if t[0] or t[1] or t[2]]
    if not triples:
        return None, ["无可判定的身份(实例/执行者/角色均为空)"]
    candidates = [str(policy_id).strip()] if policy_id else policy_ids(root)
    if not candidates:
        pdir = policies_dir(root)
        try:
            shown = f"{pdir.resolve().relative_to(root.resolve()).as_posix()}/"
        except ValueError:
            shown = f"{pdir}/"
        return None, [f"{shown} 下没有任何信封"]
    loaded: list[tuple[str, dict[str, Any], int]] = []
    reasons: list[str] = []
    for pid in candidates:
        policy = load_policy(root, pid)
        if policy is None:
            reasons.append(f"{pid}: 信封文件缺失或格式不合规(owner_autonomy_policy)")
            continue
        loaded.append((pid, policy, count_preauthorized_claims(root, pid)))
    subject: tuple[str, str, str, dict[str, Any] | None] | None = None
    if task is not None:
        subject = envelope_subject(
            root,
            task_id=str(task.get("task_id") or ""),
            task_mode=str(task.get("task_mode") or ""),
            project=str(task.get("project") or ""),
            reviewed_task_id=str(task.get("reviewed_task_id") or ""),
            lane=task.get("lane") if isinstance(task.get("lane"), dict) else None,
            with_lane=any(p.get("task_selector_lane_repo") for _pid, p, _r in loaded),
        )
    last_reason: dict[str, str] = {}
    for agent_instance, actor, claiming_role in triples:
        for pid, policy, released in loaded:
            if subject is not None:
                subject_id, subject_mode, subject_project, subject_lane = subject
            else:
                sel_ids = list(policy.get("task_selector_task_ids") or [])
                subject_id = sel_ids[0] if sel_ids else ""
                subject_mode = str(policy.get("task_selector_task_mode") or "")
                subject_project = str(policy.get("task_selector_project") or "")
                # 任务无关: 判定对象取信封自身选择器(= 「该信封覆盖某张卡」), lane 同理
                from tools.aipos_cli.workspace_config import repo_name_set

                # AIPOS-F139: 集合经唯一解析; 已过 normalize_policy 的信封形必合规(形不合者 load_policy 已拒)
                subject_lane = {"refs": repo_name_set(policy.get("task_selector_lane_repo")), "error": ""}
            matched, reason, _code = match_claim_envelope(
                policy=policy,
                task_id=subject_id,
                task_mode=subject_mode,
                project=subject_project,
                agent_instance=agent_instance,
                actor=actor,
                now=now,
                released_count=released,
                claiming_role=claiming_role,
                lane=subject_lane,
            )
            if matched:
                return policy, []
            last_reason[pid] = reason
    reasons.extend(f"{pid}: {last_reason[pid]}" for pid, _p, _r in loaded if pid in last_reason)
    return None, reasons


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
