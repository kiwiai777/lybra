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

import json
import logging
import re
import sys
from datetime import datetime, timezone
from tools.aipos_cli.clock import utc_now
from pathlib import Path
from typing import Any, Callable

from tools.aipos_cli.frontmatter import parse_markdown_frontmatter, read_markdown_frontmatter
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
ENVELOPE_ERROR_HOST_NOT_AUTHORIZED = "ENVELOPE_HOST_NOT_AUTHORIZED"  # AIPOS-F224: 项目授权允许主机外
ENVELOPE_ERROR_ZONE_FORBIDDEN = "ENVELOPE_ZONE_FORBIDDEN"  # AIPOS-F224: 项目授权未放开的禁区


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
    """Emit one structured envelope-decision trace line (JSON). Best-effort: never raises.
    AIPOS-F142 件②: 发射前只问唯一开关 envelope_trace_enabled()(关 = 不序列化不输出; 信封判定逻辑与结果不变, 门拒的拒因与原因链
    由各调用方照常输出, 收的只是逐谓词调试轨迹)。"""
    if not envelope_trace_enabled():
        return
    try:
        line = json.dumps(payload, default=str, sort_keys=True)
    except (TypeError, ValueError) as exc:  # AIPOS-F115 件③: 精确捕获序列化失败并出声(原 except Exception: pass 静默丢迹)
        line = json.dumps({"trace_serialize_error": str(exc), "payload_repr": repr(payload)[:500]}, sort_keys=True)
    # logging 自身的输出错误由 Handler.handleError 处理(不抛), 无需再包
    _ENVELOPE_LOGGER.info("[ENVELOPE_TRACE] " + line)


# ---------------------------------------------------------------------------
# AIPOS-F142 件②: [ENVELOPE_TRACE] 输出开关——只在发射处(trace_envelope)一处判定, 声明只在 verbs.schema envelope_trace 一处。
#   CLI(lybra 入口 main: decide_cli_envelope_trace, cli_envelope_trace_entry 收尾恢复)缺省关: --verbose(挂在 envelope_trace.verbose_commands 所列命令上)或调试开关环境变量(debug_env)开;
#   in-process 嵌套调用(loop 驱动器执行派生命令)沿用外层决定。不经 CLI 入口的进程(门 MCP 服务, journald 留痕, AIPOS-PRERELEASE-1)
#   无 CLI 决定 = 取 non_cli_default_enabled(缺省开, 门侧证据照旧)。进程内全局(门服务为多线程, 不用 ContextVar)。
# ---------------------------------------------------------------------------
_TRACE_SWITCH: dict[str, bool | None] = {"enabled": None}  # None = 无 CLI 决定(非 CLI 进程)


def envelope_trace_declaration() -> dict[str, Any]:
    """verbs.schema envelope_trace(缺 / 缺键 = SchemaLoadError, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("verbs").get("envelope_trace")
    keys = ("cli_default_enabled", "non_cli_default_enabled", "verbose_flag", "verbose_help", "verbose_commands", "debug_env",
            "debug_env_on_values")
    if not isinstance(decl, dict) or any(k not in decl for k in keys) or not isinstance(decl.get("verbose_commands"), list):
        raise SchemaLoadError(f"verbs.schema.json envelope_trace 未声明齐 {list(keys)}")
    return decl


def _debug_env_on(decl: dict[str, Any]) -> bool:
    import os

    value = str(os.environ.get(str(decl["debug_env"])) or "").strip().lower()
    return bool(value) and value in {str(v).strip().lower() for v in decl["debug_env_on_values"]}


def envelope_trace_enabled() -> bool:
    """唯一开关读口: CLI 已决定 = 用之; 否则(非 CLI 进程)= 调试环境变量开 或 声明 non_cli_default_enabled。
    AIPOS-F245 件③: 非 CLI 进程处在请求级只读作用域内(task_loader.read_request_scope: 看板页 / 收件箱等只读推导)且调试环境变量未开
    = 声明 read_scope_enabled(false = 不输出; 缺键 = 照旧输出)。真正认领 / 匹配路径不在只读作用域内, 不变。"""
    from tools.aipos_cli.task_loader import request_scope_active

    decided = _TRACE_SWITCH["enabled"]
    if decided is not None:
        return decided
    decl = envelope_trace_declaration()
    if _debug_env_on(decl):
        return True
    if request_scope_active() and not decl.get("read_scope_enabled", True):
        return False
    return bool(decl["non_cli_default_enabled"])


def decide_cli_envelope_trace(verbose: bool) -> None:
    """CLI 入口(aipos_cli.main, 解析参数后)唯一调用: --verbose 或调试环境变量 = 开; 已在外层 CLI 决定之内(in-process 嵌套,
    如 loop 驱动器执行派生命令)= 沿用外层; 否则 = 声明 cli_default_enabled(缺省关)。恢复原状由 cli_envelope_trace_entry 负责。"""
    decl = envelope_trace_declaration()
    if verbose or _debug_env_on(decl):
        _TRACE_SWITCH["enabled"] = True
    elif _TRACE_SWITCH["enabled"] is None:
        _TRACE_SWITCH["enabled"] = bool(decl["cli_default_enabled"])


def cli_envelope_trace_entry(func: Callable[..., Any]) -> Callable[..., Any]:
    """CLI 入口装饰器: 调用结束(含异常 / SystemExit)恢复开关原状——嵌套调用的决定不泄漏给外层, 进程内多次调用互不影响。"""
    import functools

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        previous = _TRACE_SWITCH["enabled"]
        try:
            return func(*args, **kwargs)
        finally:
            _TRACE_SWITCH["enabled"] = previous

    return wrapper


def attach_verbose_flags(root_parser: Any) -> None:
    """给 verbs.schema envelope_trace.verbose_commands 所列命令(空格分隔的子命令路径)挂 --verbose(dest=verbose, 缺省 SUPPRESS:
    父子命令都挂时不互相覆盖; 读取方一律 getattr(args, "verbose", False))。路径找不到 = SchemaLoadError(声明与命令树不符, fail-closed)。"""
    import argparse

    from tools.aipos_cli.cli_self_describe import subcommand_parser  # AIPOS-F144: 子命令定位唯一遍历实现(与 --workspace-root 注册共用)
    from tools.schema_loader import SchemaLoadError

    decl = envelope_trace_declaration()
    flag = str(decl["verbose_flag"])
    for path in decl["verbose_commands"]:
        parser = subcommand_parser(root_parser, path, label="verbs.schema envelope_trace.verbose_commands")
        if any(flag in a.option_strings for a in parser._actions):
            raise SchemaLoadError(f"verbs.schema envelope_trace.verbose_commands: 命令 {path!r} 已自带 {flag}(开关须只挂一处)")
        parser.add_argument(flag, dest="verbose", action="store_true", default=argparse.SUPPRESS, help=str(decl["verbose_help"]))

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
        # AIPOS-F224: 同一 owner_policy_ref 读取口——不是信封文件而命名本项目一份项目授权 = 授权最新修订(门 / loop 信封族调用方照常认它,
        # 判定经 match_claim_envelope 分派到 judge_project_authorization)。信封文件优先(同名时信封照旧, 行为不变)。
        return load_authorization(repo_root, pid) if is_authorization_id(repo_root, pid) else None
    metadata, _body, warnings = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
    if warnings:
        return None
    policy = normalize_policy(metadata)
    if policy is None or policy["policy_id"] != pid:
        return None
    return policy


def count_preauthorized_claims(repo_root: Path, policy_id: str) -> int:
    """信封用量(唯一计数; 门 count_bound / loop / 收件箱 / 看板 / 续签建议同读)。Stateless and auditable (R-1 count bound): the gate
    re-derives the count from truth every match.

    AIPOS-F192: 单位 = 业务卡(根卡)——已落盘 PreAuthorized 认领记录(owner_policy_ref = 本信封)的卡经 envelope_root_task_id 归到根卡后去重
    (审计卡 / 复审卡 / 补审卡 / 返工派生卡 / FINALIZE 卡的认领归属根卡, 不另扣; 声明 verbs.schema lybra_loop.envelope.quota)。
    AIPOS-F142 件①: 只读视图作用域(task_loader.task_card_lookup_scope)内, 认领记录只遍历一趟、一次得出全部信封的根卡集
    (lookup_scope_memo); 作用域外(门 claim 的放行判定)每次现读现数。"""
    return len(released_business_cards(repo_root, policy_id))


def released_business_cards(repo_root: Path, policy_id: str) -> dict[str, list[str]]:
    """AIPOS-F192: 本信封已放行的根业务卡 {根卡: [经本信封认领的卡(根卡自身或其派生卡), …]}(count_preauthorized_claims 的唯一来源)。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo

    pid = str(policy_id or "").strip()
    if not pid:
        return {}
    roots = lookup_scope_memo(Path(repo_root), "autonomy_policy.preauthorized_claim_roots",
                              lambda: _preauthorized_claim_roots(Path(repo_root)))
    carried = _carried_roots(Path(repo_root), pid)  # AIPOS-F224: 授权承接的(迁入旧信封已放行的)根卡不扣授权额度
    return {root: list(tids) for root, tids in (roots["roots"].get(pid) or {}).items() if root not in carried}


def preauthorized_claim_records(repo_root: Path, policy_id: str) -> int:
    """AIPOS-F192 对照用: 本信封名下 PreAuthorized 认领记录条数(旧口径「按认领次数计」; 只供存量重算前后对照展示, 不参与任何判定)。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo

    pid = str(policy_id or "").strip()
    if not pid:
        return 0
    roots = lookup_scope_memo(Path(repo_root), "autonomy_policy.preauthorized_claim_roots",
                              lambda: _preauthorized_claim_roots(Path(repo_root)))
    return int(roots["records"].get(pid, 0))


def _preauthorized_claim_roots(repo_root: Path) -> dict[str, dict[str, Any]]:
    """已落盘 PreAuthorized 认领记录按 owner_policy_ref 归组, 认领卡归到根卡(envelope_root_task_id): {roots: {pid: {根卡: [卡]}},
    records: {pid: 认领记录条数}}。认领记录无 task_id(形不合)= 以记录相对路径自成一根(只多不少)。整根只遍历一趟(队列索引共用一个只读作用域)。"""
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    claims_root = (repo_root / record_root("claims")).resolve()
    pairs: list[tuple[str, str]] = []
    if claims_root.is_dir():
        for path in sorted(claims_root.rglob("*.md")):
            if not path.is_file():
                continue
            try:
                metadata, _body, _warn, _fence = read_markdown_frontmatter(path)  # AIPOS-F177: per-file cache (unchanged record = no re-read)
            except OSError:
                continue
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("autonomy_mode") or "").strip() != AUTONOMY_MODE_PREAUTHORIZED:
                continue
            ref = str(metadata.get("owner_policy_ref") or "").strip()
            if ref:
                pairs.append((ref, str(metadata.get("task_id") or "").strip() or f"<claim {path.relative_to(claims_root).as_posix()}>"))
    roots: dict[str, dict[str, list[str]]] = {}
    records: dict[str, int] = {}
    with task_card_lookup_scope(repo_root):
        for ref, task_id in pairs:
            records[ref] = records.get(ref, 0) + 1
            root = envelope_root_task_id(repo_root, task_id) if not task_id.startswith("<claim ") else task_id
            bucket = roots.setdefault(ref, {}).setdefault(root, [])
            if task_id not in bucket:
                bucket.append(task_id)
    return {"roots": roots, "records": records}


# ---------------------------------------------------------------------------
# AIPOS-F192: 额度单位 = 业务卡(根卡)。派生关系只读卡面字段(verbs.schema lybra_loop.envelope.quota.parent_links), 禁按卡号字符串猜。
# ---------------------------------------------------------------------------

_QUOTA_KEYS = ("unit_label", "parent_links", "max_depth", "renew_quota")


def envelope_quota_declaration() -> dict[str, Any]:
    """verbs.schema lybra_loop.envelope.quota(fail-closed): 键齐; parent_links 每项有 field / kind; renew_quota 有 line / basis 四式 / state_labels。"""
    from tools.schema_loader import SchemaLoadError

    decl = loop_envelope_declaration().get("quota")
    links = (decl or {}).get("parent_links") if isinstance(decl, dict) else None
    renew = (decl or {}).get("renew_quota") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or any(k not in decl for k in _QUOTA_KEYS) or not isinstance(links, list) or not links
            or any(not isinstance(l, dict) or not str(l.get("field") or "").strip() or not str(l.get("kind") or "").strip() for l in links)
            or not isinstance(decl.get("max_depth"), int) or isinstance(decl.get("max_depth"), bool) or decl["max_depth"] < 1
            or not isinstance(renew, dict) or not isinstance(renew.get("line"), str)
            or any(not isinstance((renew.get("basis") or {}).get(k), str) for k in ("card_released", "card_new", "card_carry", "batch"))
            or not isinstance(renew.get("state_labels"), dict)):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.envelope.quota 未声明齐(unit_label / parent_links[{field, kind}] / "
                              "max_depth≥1 / renew_quota{line, basis{card_released, card_new, card_carry, batch}, state_labels})")
    return decl


def _card_frontmatter_for(repo_root: Path, task_id: str) -> dict[str, Any] | None:
    """队列里该卡的 frontmatter(查找唯一 task_loader.find_task_card); 不在队列 / 多义 / 读不出 = None(派生链在此止步)。"""
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    try:
        path, _queue = find_task_card(repo_root, task_id)
    except AmbiguousTaskCard:
        return None
    if path is None:
        return None
    try:
        metadata, _body, _warn, _fence = read_markdown_frontmatter(path)
    except OSError:
        return None
    return metadata if isinstance(metadata, dict) else None


def _declared_parent(repo_root: Path, task_id: str, fm: dict[str, Any], links: list[dict[str, Any]]) -> str:
    """按声明 parent_links 次序取首个命中的派生来源卡号; 无 = ""。parent_declares=finalize_task_id: 来源卡按 FINALIZE 卡号规则
    (next_resolver.finalize_task_id_for, transitions artifact_ingest.finalization 唯一声明)推出的卡号须恰为本卡, 否则不认。"""
    mode = str(fm.get("task_mode") or "").strip().lower()
    for link in links:
        want_mode = str(link.get("task_mode") or "").strip().lower()
        if want_mode and mode != want_mode:
            continue
        value = fm.get(str(link["field"]))
        parent = str(value).strip() if isinstance(value, (str, int)) else ""
        if not parent or parent == task_id:
            continue
        if str(link.get("parent_declares") or "") == "finalize_task_id":
            from tools.aipos_cli.next_resolver import finalize_task_id_for  # 延迟导入(next_resolver 依赖本模块)

            parent_fm = _card_frontmatter_for(repo_root, parent)
            if parent_fm is None or finalize_task_id_for(parent, parent_fm) != task_id:
                continue
        return parent
    return ""


def envelope_root_task_id(governance_root: Path | str, task_id: str) -> str:
    """AIPOS-F192: 卡的根业务卡(唯一判据): 沿声明 parent_links(审计 / 复审 / 补审 → 被审卡; 返工派生卡 → 审计卡 → 被审卡; FINALIZE 卡 → 被审卡)
    逐级上溯到无来源字段的卡。卡不在队列 = 该卡自成根(链在此止步; 来源字段已指明的上级卡号照样作根)。链长超 max_depth 或成环 = 原卡自成根
    (fail-safe: 不借别的卡的放行)。只读; 只读作用域内按卡缓存。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo, task_card_lookup_scope

    root = Path(governance_root)
    start = str(task_id or "").strip()
    if not start:
        return ""
    decl = envelope_quota_declaration()
    links = list(decl["parent_links"])
    with task_card_lookup_scope(root):
        cache = lookup_scope_memo(root, "autonomy_policy.envelope_root_cache", dict)
        if start in cache:
            return cache[start]
        chain = [start]
        result = start
        for _step in range(int(decl["max_depth"]) + 1):
            current = chain[-1]
            fm = _card_frontmatter_for(root, current)
            parent = _declared_parent(root, current, fm, links) if fm is not None else ""
            if not parent:
                result = current
                break
            if parent in chain:  # 成环 = 原卡自成根
                result = start
                break
            chain.append(parent)
        else:  # 链长超上限 = 原卡自成根
            result = start
        cache[start] = result
        return result


def card_released_by_envelope(repo_root: Path | str, policy_id: str, task_id: str) -> bool:
    """AIPOS-F192: 「卡已在本信封放行」唯一判据——该卡的根业务卡 ∈ 本信封已放行根卡集(本卡或其根卡 / 同链派生卡曾经本信封 PreAuthorized 认领)。
    是 = 该卡后续步骤不受额度拦截(match_claim_envelope card_released=True; 时间窗 / 撤销 / 身份 / 选择器照判)。"""
    return envelope_quota_state(repo_root, policy_id, task_id)["card_released"]


def envelope_quota_state(repo_root: Path | str, policy_id: str, task_id: str | None) -> dict[str, Any]:
    """AIPOS-F192: 一次读出 {used(本信封已放行根业务卡数), card_released(本卡链已由本信封放行), root(本卡根卡)}——门逐请求核验与
    select_envelope 同读; 一个只读作用域内共用一趟队列索引与认领记录遍历。task_id 空 = card_released False。"""
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    root = Path(repo_root)
    with task_card_lookup_scope(root):
        cards = released_business_cards(root, policy_id)
        tid = str(task_id or "").strip()
        card_root = envelope_root_task_id(root, tid) if tid else ""
        # AIPOS-F224: 项目授权承接其迁入旧信封已放行的根卡(本卡链后续步骤不受授权额度拦截, 也不扣授权额度)
        released = bool(card_root) and (card_root in cards or card_root in _carried_roots(root, str(policy_id or "").strip()))
        return {"used": len(cards), "card_released": released, "root": card_root}


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
    from tools.aipos_cli.next_resolver import task_mode_is_audit  # AIPOS-F216 件④: 审计卡识别卡面层单源(低层模块只依赖 next_resolver)

    if task_mode_is_audit(task_mode) and str(reviewed_task_id or "").strip():
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
    card_released: bool = False,
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
    AIPOS-F192: card_released = 本卡链已由本信封放行(唯一判据 card_released_by_envelope / envelope_quota_state, 调用方读出后传入)——
    额度谓词 count_bound 放行(后续步骤只受有效 / 时间窗 / 撤销 / 身份 / 选择器约束); 缺省 False = 原口径(released_count < max_tasks)。
    """
    if is_authorization(policy):
        # AIPOS-F224: 项目授权的覆盖判定唯一实现 judge_project_authorization(门 _match_claim_envelope / _match_driver_envelope 与 loop
        # select_envelope 同经本函数进入, 不另立判定); 身份集合与信封同口径 = {agent_instance, actor, claiming_role}
        identity_set = {str(x).strip() for x in (agent_instance, actor, claiming_role) if str(x or "").strip()}
        judged = judge_project_authorization(policy, task_id=task_id, task_mode=task_mode, project=project, lane=lane, identities=identity_set,
                                             now=now, released_count=released_count, card_released=card_released,
                                             driver=str(agent_instance or actor or ""))
        trace_envelope({"phase": "match_claim_envelope", "policy_id": policy.get("policy_id"), "grant_kind": policy.get(GRANT_KIND_KEY),
                        "revision": policy.get("revision"), "task_id": task_id, "identities": sorted(identity_set),
                        "matched": judged["matched"], "dimension": judged["dimension"], "reason": judged["reason"],
                        "error_code": judged["error_code"]})
        return bool(judged["matched"]), str(judged["reason"]), judged["error_code"]
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
        "count_bound": bool(card_released) or released_count < max_tasks,
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
    elif released_count >= max_tasks and not card_released:
        reason = f"policy count bound reached ({released_count}/{max_tasks} business cards)"
        error_code = ENVELOPE_ERROR_QUOTA_EXHAUSTED
    elif card_released:
        reason = (f"matched policy {policy.get('policy_id')} (card chain already released by this envelope; "
                  f"later steps not quota-bound; released {released_count}/{max_tasks} business cards)")
        error_code = None  # success, no error
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
            "card_released": bool(card_released),
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
    notes: list[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """AIPOS-F103 件④: 信封挑选唯一实现——判据只有 match_claim_envelope(禁第二套有效性/覆盖/选择器判定)。

    - identities: 身份三元组 (agent_instance, actor, claiming_role) 列表, 按具体程度降序(实例/角色名 → 角色类);
      每个三元组原样交 match_claim_envelope(其身份集合 = 三者去空)。
    - task: 判定对象 {task_id, task_mode, project, reviewed_task_id?, lane?}; 经 envelope_subject(审计卡 = 被审卡, 门与 loop 同一规则);
      AIPOS-F134 件③: 候选信封有 task_selector_lane_repo 时才解析卡 lane 指称(card_lane_refs)。
      None = 任务无关(工位 .lybra/role 推导、凭据上下文自发现等无卡场景): 判定对象取该信封自身 task_selector
      (= 「该信封覆盖某张卡」), 其余谓词(有效/时间窗/身份覆盖/选择器非空/额度)照常由 match_claim_envelope 判。
    - policy_id: 给定 = 只核该信封(同一判据重核, 不另挑); 缺省 = 扫描声明目录 policies_dir 全部信封。
    挑选次序(确定性): 身份三元组次序优先(更具体者先), 同级取最新生效者(active_from 新→旧, 同刻按 id 降序)。
    AIPOS-F185 件③(gap #163): 扫描时已被同驱动方更新信封接替的旧信封(接替判定唯一实现 _supersession, 看板信封页 / 收件箱同读)不再
    挑中、不再被消耗——原因写「已被 X 接替」; 原「同级按 id 升序取首个」会先消耗看板已判「已接替」的旧信封。
    notes(可选, 调用方给列表)= 追加挑选说明(跳过了哪些已接替信封), 供输出写明用的是哪个信封。
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
    # AIPOS-F224: 覆盖判定优先级(声明 lybra_loop.envelope.project_authorization.priority)——①项目授权先判(判据 match_claim_envelope →
    # judge_project_authorization); ②未覆盖才兼容旧信封, 已迁入授权的旧信封视为 superseded(不挑中、不消耗); 显式 policy_id 只核该一份。
    explicit = str(policy_id or "").strip()
    explicit_auth = bool(explicit) and not (policies_dir(root) / f"{explicit}.md").is_file() and is_authorization_id(root, explicit)
    auth_ids = [explicit] if explicit_auth else ([] if explicit else authorization_ids(root))
    auth_reasons: list[str] = []
    if auth_ids:
        found, failures = judge_authorizations(root, auth_ids, triples=triples, task=task, now=now)
        if found is not None:
            if notes is not None:
                notes.append(f"挑中项目授权 {found['policy_id']}(r{found['revision']}; 项目授权优先于旧信封)")
            return found, []
        auth_reasons = [f"{f['authorization_id']}: " + (f"越界({f['label']}): {f['reason']}" if f.get("label") else str(f["reason"]))
                        for f in failures]
        if explicit_auth:
            return None, auth_reasons
    migrated = authorization_superseded_policies(root)
    if explicit and explicit in migrated:
        msg = project_authorization_declaration()["messages"]["migrated"].format(policy_id=explicit, authorization_id=migrated[explicit])
        return None, auth_reasons + [f"{explicit}: {msg}(不再挑中、不再消耗)"]
    candidates = [str(policy_id).strip()] if policy_id else policy_ids(root)
    if not candidates:
        if auth_reasons:
            return None, auth_reasons
        pdir = policies_dir(root)
        try:
            shown = f"{pdir.resolve().relative_to(root.resolve()).as_posix()}/"
        except ValueError:
            shown = f"{pdir}/"
        return None, [f"{shown} 下没有任何信封"]
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    loaded: list[tuple[str, dict[str, Any], int]] = []
    reasons: list[str] = list(auth_reasons)
    released_card: dict[str, bool] = {}  # AIPOS-F192: 本卡链已由该信封放行 = 额度不拦(唯一判据 envelope_quota_state)
    task_key = str((task or {}).get("task_id") or "").strip()
    with task_card_lookup_scope(root):  # 只读: 认领记录遍历与队列索引各一趟(多候选信封共用)
        for pid in candidates:
            policy = load_policy(root, pid)
            if policy is None:
                reasons.append(f"{pid}: 信封文件缺失或格式不合规(owner_autonomy_policy)")
                continue
            quota = envelope_quota_state(root, pid, task_key or None)
            loaded.append((pid, policy, int(quota["used"])))
            released_card[pid] = bool(quota["card_released"])
    successors: dict[str, str | None] = {}
    if not policy_id:
        # AIPOS-F185 件③: 最新生效者优先 + 已接替者不挑(接替判定与看板 / 收件箱同一函数 _supersession)
        loaded.sort(key=lambda item: (_parse_iso(item[1].get("active_from")) or datetime.min.replace(tzinfo=timezone.utc), item[0]),
                    reverse=True)
        successors = _supersession([{"policy_id": pid, "state": _usage_state(policy, released, now), "policy": policy,
                                     "_from": _parse_iso(policy.get("active_from"))} for pid, policy, released in loaded])
        for pid, aid in migrated.items():  # AIPOS-F224: 已迁入项目授权 = superseded(接替者 = 该授权)
            if pid in successors:
                successors[pid] = aid
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
            if successors.get(pid):
                last_reason[pid] = (project_authorization_declaration()["messages"]["migrated"].format(policy_id=pid, authorization_id=migrated[pid])
                                    + "(不再挑中、不再消耗)" if pid in migrated else
                                    f"已被同驱动方更新信封 {successors[pid]} 接替(看板信封页 / 收件箱同一判定), 不再挑中、不再消耗")
                continue
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
                card_released=released_card.get(pid, False),
            )
            if matched:
                if notes is not None and not policy_id:
                    replaced = sorted(old for old, succ in successors.items() if succ == pid)
                    notes.append(f"挑中信封 {pid}(覆盖本卡与驱动方的有效信封中最新生效者)"
                                 + (f"; 被它接替的旧信封 {', '.join(replaced)} 不再消耗" if replaced else ""))
                return policy, []
            last_reason[pid] = reason
    reasons.extend(f"{pid}: {last_reason[pid]}" for pid, _p, _r in loaded if pid in last_reason)
    return None, reasons


# ---------------------------------------------------------------------------
# AIPOS-F166 件②(gap #152): 信封用量真相(门侧既有判据, 不另计数)+ 续签出口 + 预警。
# 声明 = verbs.schema lybra_loop.envelope.renew(结束原因名 / 续签天数 / 预警阈值 / 文案); 下一动作映射 = lybra_loop_status.next_action。
# ---------------------------------------------------------------------------

ENVELOPE_RENEW_KEYS = ("end_reasons", "renew_days", "warn", "messages")
_POLICY_SEQ_RE = re.compile(r"^(?P<prefix>.*?)(?P<seq>\d+)$")


def envelope_renew_declaration() -> dict[str, Any]:
    """verbs.schema lybra_loop.envelope.renew。缺键 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = loop_envelope_declaration().get("renew")
    if not isinstance(decl, dict) or any(k not in decl for k in ENVELOPE_RENEW_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_loop.envelope.renew 未声明齐 {list(ENVELOPE_RENEW_KEYS)}")
    ends, warn, msgs = decl["end_reasons"], decl["warn"], decl["messages"]
    if (not isinstance(ends, dict) or set(ends) != {"exhausted", "expired"}
            or not isinstance(warn, dict) or any(not isinstance(warn.get(k), (int, float)) or isinstance(warn.get(k), bool)
                                                 for k in ("remaining_ratio", "remaining_count", "expiry_hours"))
            or not isinstance(msgs, dict) or any(not isinstance(msgs.get(k), str) for k in ("exhausted", "expired", "renew", "warn"))
            or not isinstance(decl["renew_days"], int) or decl["renew_days"] < 1
            or not isinstance(warn.get("unused_exempt", False), bool)):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.envelope.renew 形不合(end_reasons{exhausted,expired} / warn 三阈值"
                              "(+ 可选布尔 unused_exempt) / messages{exhausted,expired,renew,warn} / renew_days≥1)")
    return decl


def envelope_guard_codes() -> list[str]:
    """transitions.schema envelope_guards.guards 声明的全部 error_code(ENVELOPE_*; 门信封拒因码唯一声明处)。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    guards = (load_schema("transitions", code_repo_schema_root()).get("envelope_guards") or {}).get("guards")
    codes = [str(g.get("error_code")) for g in (guards or {}).values() if isinstance(g, dict) and g.get("error_code")]
    if not codes:
        raise SchemaLoadError("transitions.schema.json envelope_guards.guards 未声明 error_code")
    return codes


def envelope_usage(governance_root: Path | str, policy_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """一个信封的用量真相(唯一实现): 读门侧同一判据——信封文件 load_policy + 已放行计数 count_preauthorized_claims(门 match_claim_envelope
    的 count_bound 即此计数; AIPOS-F192: 单位 = 根业务卡, released_cards = 已放行根卡, claim_records = 认领记录条数仅供新旧口径对照)+ 时间窗。state: missing(信封文件缺 / 形不合)/ inactive(status≠active 或未获 Owner 批准)/ invalid(时间窗读不出)/
    not_yet_active / expired / exhausted / active —— 次序与门判定一致(过期先于额度)。"""
    root = Path(governance_root)
    now = now or utc_now()
    pid = str(policy_id or "").strip()
    policy = load_policy(root, pid) if pid else None
    if policy is None:
        return {"policy_id": pid, "state": "missing", "policy": None}
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    with task_card_lookup_scope(root):  # AIPOS-F192: 用量 = 已放行根业务卡数(认领记录与队列索引同一趟)
        cards = released_business_cards(root, pid)
        claim_records = preauthorized_claim_records(root, pid)
    used = len(cards)
    max_tasks = int(policy.get("max_tasks") or 0)
    exp = _parse_iso(policy.get("expires_at"))
    state = _usage_state(policy, used, now)
    return {"policy_id": pid, "state": state, "used": used, "max_tasks": max_tasks, "remaining": max(0, max_tasks - used),
            "expires_at": policy.get("expires_at"), "seconds_to_expiry": int((exp - now).total_seconds()) if exp else None,
            "released_cards": sorted(cards), "claim_records": claim_records, "policy": policy}


def _usage_state(policy: dict[str, Any], used: int, now: datetime) -> str:
    """已载入信封 + 已放行认领计数 → 用量状态(envelope_usage 与 select_envelope 接替判定同读; 次序与门判定一致: 过期先于额度)。"""
    try:
        max_tasks = int(policy.get("max_tasks") or 0)
    except (TypeError, ValueError):
        max_tasks = 0
    frm, exp = _parse_iso(policy.get("active_from")), _parse_iso(policy.get("expires_at"))
    if policy.get("status") != POLICY_STATUS_ACTIVE or not policy.get("approved_by_owner"):
        return "inactive"
    if frm is None or exp is None:
        return "invalid"
    if now < frm:
        return "not_yet_active"
    if now >= exp:
        return "expired"
    if max_tasks <= 0 or used >= max_tasks:
        return "exhausted"
    return "active"


def next_policy_id(governance_root: Path | str, *, identities: set[str] | list[str], previous: str | None = None,
                   project: str = "") -> str:
    """续签信封的 policy_id: 该驱动方(agent_or_role ∈ identities)同前缀信封已有最大序号 +1; 与已有文件撞名则顺延(不覆盖任何信封)。
    前缀 = 用尽信封 id 去掉末尾序号(previous 无序号 = previous + "_"); 无 previous = pol_<项目>_loop_(既有申领出口惯例)。"""
    root = Path(governance_root)
    match = _POLICY_SEQ_RE.match(str(previous or "").strip()) if previous else None
    if match and match.group("prefix"):
        prefix = match.group("prefix")
    elif previous:
        prefix = f"{str(previous).strip()}_"
    else:
        prefix = f"pol_{str(project or '').strip() or 'project'}_loop_"
    who = {str(i).strip() for i in identities if str(i).strip()}
    existing = set(policy_ids(root))
    seqs = []
    for pid in existing:
        hit = _POLICY_SEQ_RE.match(pid)
        if not hit or hit.group("prefix") != prefix:
            continue
        policy = load_policy(root, pid)
        if policy is not None and policy.get("agent_or_role") in who:
            seqs.append(int(hit.group("seq")))
    seq = max(seqs, default=0) + 1
    while f"{prefix}{seq}" in existing:
        seq += 1
    return f"{prefix}{seq}"


def render_mint_command(*, policy_id: str, agent_or_role: str, max_tasks: int, task_mode: str, expires_at: str,
                        decision_summary: str, governance_root: Path | str | None = None,
                        lane_repos: list[str] | None = None, launch_harnesses: list[str] | None = None) -> str:
    """`lybra envelope mint --confirm` 申领 / 续签出口命令的唯一渲染(loop 无信封出口 loop_driver.mint_hint 与信封用尽续签同用)。
    Owner 亲自敲(--connection-json 指向持 Owner 凭据的 connection.json, 本命令不读不显示凭据)。"""
    import shlex

    parts = [f"lybra envelope mint --confirm --policy-id {policy_id} --agent-or-role {agent_or_role}",
             f"--max-tasks {int(max_tasks)}"]
    if task_mode:
        parts.append(f"--task-mode {task_mode}")
    parts += [f"--lane-repo {shlex.quote(str(r))}" for r in (lane_repos or [])]
    parts += [f"--launch-harness {h}" for h in (launch_harnesses or [])]
    parts.append(f"--expires-at {expires_at} --decision-summary \"{decision_summary}\" --actor owner")
    if governance_root:
        parts.append(f"--workspace-root {shlex.quote(str(governance_root))}")
    parts.append("--connection-json <Owner 凭据 connection.json>")
    return " ".join(parts)


def renew_plan(governance_root: Path | str, usage: dict[str, Any], *, identities: set[str] | list[str], task_id: str,
               card_task_mode: str = "", now: datetime | None = None) -> dict[str, Any]:
    """用尽 / 过期信封的续签计划: {next_policy_id, renew_command, note, max_tasks, card_need, quota_line}。范围照抄该信封(agent_or_role / task_mode
    (信封无则取本卡)/ lane_repo / launch_harnesses); 信封另有 mint 不支持的选择器(task_selector_project / task_selector_task_ids)= note 写明,
    命令不丢范围之外的东西。AIPOS-F192: 额度不再照抄原值——唯一推导 envelope_card_need(本卡已放行 = 0 / 新卡 = 1; 无具体卡 = 原额度批量)。"""
    from datetime import timedelta

    decl = envelope_renew_declaration()
    now = now or utc_now()
    policy = usage.get("policy") or {}
    pid = str(usage.get("policy_id") or "")
    nxt = next_policy_id(governance_root, identities=identities, previous=pid or None, project=str(policy.get("task_selector_project") or ""))
    expires = (now + timedelta(days=int(decl["renew_days"]))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    need = envelope_card_need(governance_root, usage, task_id=task_id)
    command = render_mint_command(
        policy_id=nxt, agent_or_role=str(policy.get("agent_or_role") or next(iter(sorted(identities)), "advisor")),
        max_tasks=int(need["max_tasks"]),
        task_mode=str(policy.get("task_selector_task_mode") or card_task_mode or ""), expires_at=expires,
        decision_summary=f"renew {pid} ({usage.get('state')}) for {task_id}", governance_root=governance_root,
        lane_repos=list(policy.get("task_selector_lane_repo") or []), launch_harnesses=list(policy.get("launch_harnesses") or []))
    unexpressible = [f"{k}={policy.get(k)!r}" for k in ("task_selector_project", "task_selector_task_ids") if policy.get(k)]
    note = (f"原信封另有 {', '.join(unexpressible)}(envelope mint 无对应参数, 续签命令只照抄可表达的范围; 如需同样收窄请 Owner 另定)"
            if unexpressible else "")
    return {"next_policy_id": nxt, "renew_command": command, "note": note, "max_tasks": int(need["max_tasks"]), "card_need": need["need"],
            "quota_line": need["line"]}


def envelope_card_need(governance_root: Path | str, usage: dict[str, Any], *, task_id: str) -> dict[str, Any]:
    """AIPOS-F192: 续签 / 授权建议额度的唯一推导(声明 verbs.schema lybra_loop.envelope.quota.renew_quota; renew_plan 与 owner_inbox.suggest_grant 同读)。
    task_id 是队列里的卡: 本卡链已由 usage 所指信封放行且该信封仍在时间窗内(active / exhausted)= need 0(后续步骤已不受额度限制, 本卡无需续签;
    命令额度 1 只为之后的新卡); 曾放行但信封已过期 / 撤销等 = need 1(新信封承接本卡); 未放行 = need 1(新业务卡)。
    task_id 不是卡(信封页 / brief 的当前信封续签, 无在推卡)= need None, 额度沿用原信封(批量授权)。
    返回 {need, max_tasks, basis, line, released}。"""
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    decl = envelope_quota_declaration()
    renew = decl["renew_quota"]
    root = Path(governance_root)
    policy = usage.get("policy") or {}
    pid = str(usage.get("policy_id") or "").strip()
    state = str(usage.get("state") or "")
    tid = str(task_id or "").strip()
    try:
        is_card = bool(tid) and find_task_card(root, tid)[0] is not None
    except AmbiguousTaskCard:
        is_card = True  # 多义仍是卡号(按卡口径给 1, 不猜成批量)
    released = False
    if not is_card:
        need, max_tasks = None, int(policy.get("max_tasks") or usage.get("max_tasks") or 1)
        basis = renew["basis"]["batch"].format(max_tasks=max_tasks)
    else:
        released = bool(pid) and card_released_by_envelope(root, pid, tid)
        if released and state in ("active", "exhausted"):
            need, basis = 0, renew["basis"]["card_released"].format(task_id=tid, policy_id=pid)
        elif released:
            label = str(renew["state_labels"].get(state) or state)
            need, basis = 1, renew["basis"]["card_carry"].format(task_id=tid, policy_id=pid, state_label=label)
        else:
            need, basis = 1, renew["basis"]["card_new"].format(task_id=tid)
        max_tasks = max(1, need)
    line = renew["line"].format(max_tasks=max_tasks, unit=decl["unit_label"], basis=basis)
    return {"need": need, "max_tasks": max_tasks, "basis": basis, "line": line, "released": released}


def envelope_warning(usage: dict[str, Any]) -> str | None:
    """预警判定(唯一实现, loop status / brief 同用): 有效信封(state=active)剩余 ≤ remaining_ratio × 上限、或剩余 ≤ remaining_count 次、或
    距到期 ≤ expiry_hours 小时 = 一行原因; 用尽 / 过期 = 一行原因; 其余(缺 / 未生效 / 撤销)= None(不归预警管)。
    AIPOS-F201 件④(gap #196 / #206): 声明 warn.unused_exempt = true 时, 未用过(used = 0)的有效信封不报剩余不足(到期临近照报)。"""
    decl = envelope_renew_declaration()
    warn = decl["warn"]
    state = usage.get("state")
    if state == "exhausted":
        return f"额度已用尽(已放行 {usage.get('used')}/{usage.get('max_tasks')} 张业务卡)"
    if state == "expired":
        return f"已过期(到期 {usage.get('expires_at')})"
    if state != "active":
        return None
    reasons: list[str] = []
    remaining, max_tasks = int(usage.get("remaining") or 0), int(usage.get("max_tasks") or 0)
    unused = bool(warn.get("unused_exempt", False)) and int(usage.get("used") or 0) == 0
    if not unused and (remaining <= int(warn["remaining_count"]) or remaining <= float(warn["remaining_ratio"]) * max_tasks):
        reasons.append(f"剩余 {remaining}/{max_tasks} 张业务卡(阈值: ≤{warn['remaining_count']} 张或 ≤{float(warn['remaining_ratio']):.0%})")
    seconds = usage.get("seconds_to_expiry")
    if isinstance(seconds, int) and seconds <= float(warn["expiry_hours"]) * 3600:
        reasons.append(f"{usage.get('expires_at')} 到期(剩 {seconds // 3600}h{(seconds % 3600) // 60:02d}m, 阈值 ≤{warn['expiry_hours']}h)")
    return "; ".join(reasons) or None


def envelope_warnings(governance_root: Path | str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """brief / Owner 收件箱用: 本项目达预警阈值且仍在用的信封 [{policy_id, why, ...}](判定唯一 envelope_overview: status=warn;
    用尽 / 过期的历史信封不列; AIPOS-F172(gap #160): 已被同驱动方更新信封接替的旧信封不列)。"""
    out: list[dict[str, Any]] = []
    template = ""
    for row in sorted(envelope_overview(governance_root, now=now)["envelopes"], key=lambda r: r["policy_id"]):  # 次序同原(信封 id 升序)
        if row["status"] != "warn":
            continue
        template = template or envelope_renew_declaration()["messages"]["warn"]
        pid, who, why = row["policy_id"], row["agent_or_role"], row["why"]
        out.append({"policy_id": pid, "why": why, "used": row["used"], "max_tasks": row["max_tasks"], "expires_at": row["expires_at"],
                    "agent_or_role": who, "next_policy_id": row["next_policy_id"],
                    "line": f"{template.format(policy_id=pid, why=why, command=row['renew_command'])}(覆盖 {who})"})
    return out


# ---------------------------------------------------------------------------
# AIPOS-F172: 信封状态单一推导(看板信封页 / Owner 收件箱 F168 / loop status 预警 F166 / brief 共用)。
# 状态 = envelope_usage(门侧同一用量真相)+ envelope_warning(同一预警阈值)+ 接替关系(本节唯一判定)。
# ---------------------------------------------------------------------------

#: 展示状态值域(看板文案 / 色读 verbs.schema board_owner_envelopes.status_labels; 键须与此一致)
ENVELOPE_DISPLAY_STATUSES = ("warn", "exhausted", "active", "not_yet_active", "superseded", "expired", "inactive", "invalid", "missing")
#: 「当前在用」= 未被接替且处于这些状态(用尽而无接替者 = 仍是该驱动方的当前信封, 待续签)
_CURRENT_STATUSES = ("warn", "exhausted", "active", "not_yet_active")
#: 可接替别人的信封状态(新信封自身有效或已签待生效)
_SUCCESSOR_STATES = ("active", "not_yet_active")


def envelope_driver_role(agent_or_role: str) -> str:
    """信封 agent_or_role → 驱动方角色名(custom_roles.instance_role_name_candidates 唯一实现的末候选: 实例名首段按 naming.prefix 反查;
    裸角色名 = 自身)。只读 roles.schema 注册表。"""
    from tools.aipos_cli.custom_roles import instance_role_name_candidates

    cands = instance_role_name_candidates(agent_or_role)
    return cands[-1] if cands else ""


def _same_driver(older: dict[str, Any], newer: dict[str, Any]) -> bool:
    """同一驱动方: agent_or_role 相同; 或同一角色名且其一写的是裸角色名(角色级信封 ↔ 该角色实例信封, 如 advisor ↔ advisor.<项目>.<机器>)。
    两个不同的具体实例 = 不同驱动方(并行多顾问各自的信封互不接替)。"""
    a, b = str(older.get("agent_or_role") or ""), str(newer.get("agent_or_role") or "")
    if not a or not b:
        return False
    if a == b:
        return True
    return envelope_driver_role(a) == envelope_driver_role(b) and ("." not in a or "." not in b)


def _scope_covers(newer: dict[str, Any], older: dict[str, Any]) -> bool:
    """新信封的作用范围 ⊇ 旧信封(task_mode / project / task_ids / lane 仓集合; 新者缺省 = 不限)。范围变窄 = 不算接替(旧信封仍覆盖新者不覆盖的卡)。
    launch_harnesses 是拉起授权附加项, 不计入范围。"""
    for key in ("task_selector_task_mode", "task_selector_project"):
        if newer.get(key) and newer.get(key) != older.get(key):
            return False
    for key in ("task_selector_task_ids", "task_selector_lane_repo"):
        new_set, old_set = set(newer.get(key) or []), set(older.get(key) or [])
        if new_set and (not old_set or not old_set <= new_set):
            return False
    return True


def _supersession(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """接替判定唯一实现(AIPOS-F172 gap #160; AIPOS-F185 件③ gap #163 起门挑选信封同读): rows = [{policy_id, state, policy, _from}]
    → {policy_id: 接替者 policy_id | None}。旧信封 A 被 B 接替 ⇔ B 自身有效或已签待生效(_SUCCESSOR_STATES)、B 的 active_from 严格晚于 A、
    同一驱动方(_same_driver)、B 的范围 ⊇ A(_scope_covers); 取最新的那个 B。active_from 相同 = 不判先后(都不接替)。
    缺失 / 形不合 / 时间读不出的信封不被接替(也不接替别人)。"""
    out: dict[str, str | None] = {}
    for row in rows:
        best: dict[str, Any] | None = None
        if row["state"] not in ("missing", "invalid") and row["_from"] is not None:
            for other in rows:
                if (other is row or other["state"] not in _SUCCESSOR_STATES or other["_from"] is None or other["_from"] <= row["_from"]
                        or not _same_driver(row["policy"], other["policy"]) or not _scope_covers(other["policy"], row["policy"])):
                    continue
                if best is None or (other["_from"], other["policy_id"]) > (best["_from"], best["policy_id"]):
                    best = other
        out[row["policy_id"]] = best["policy_id"] if best else None
    return out


def envelope_successor(governance_root: Path | str, policy_id: str, *, now: datetime | None = None) -> str | None:
    """AIPOS-F185 件③: 该信封的接替者(同一接替判定 _supersession, 看板信封页 / 收件箱 / loop status 预警同读); 未被接替 / 不在声明目录 = None。
    只载入信封与已放行认领计数, 不算续签计划。"""
    root = Path(governance_root)
    now = now or utc_now()
    migrated = authorization_superseded_policies(root).get(str(policy_id or "").strip())
    if migrated:  # AIPOS-F224: 已迁入项目授权 = 接替者是该授权(显式 --envelope 续跑改用授权)
        return migrated
    rows: list[dict[str, Any]] = []
    for pid in policy_ids(root):
        policy = load_policy(root, pid)
        if policy is None:
            continue
        rows.append({"policy_id": pid, "state": _usage_state(policy, count_preauthorized_claims(root, pid), now), "policy": policy,
                     "_from": _parse_iso(policy.get("active_from"))})
    return _supersession(rows).get(str(policy_id or "").strip())


def envelope_overview(governance_root: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """一个项目全部信封的状态(唯一推导)。每条 = envelope_usage 用量真相 + 展示状态 status ∈ ENVELOPE_DISPLAY_STATUSES + 接替者 superseded_by
    + 是否当前在用 current + 预警原因 why(envelope_warning)+ 续签计划(renew_plan: next_policy_id / renew_command / note)。

    接替(gap #160): 旧信封 A 被 B 接替 ⇔ B 自身有效或已签待生效(active / not_yet_active)、B 的 active_from 严格晚于 A、同一驱动方(_same_driver)、
    B 的范围 ⊇ A(_scope_covers); 取最新的那个 B。active_from 相同 = 不判先后(都不接替)。
    status: 有效信封被接替 = superseded(不预警); 有效且达阈值 = warn; 其余 = usage.state(用尽 / 过期 / 未生效 / 停用 / 形不合 / 缺失)。
    current = 未被接替且 status ∈ _CURRENT_STATUSES。返回 {governance_root, envelopes(按 active_from 新→旧, 同刻按 id), drivers}:
    drivers = 每个驱动方(agent_or_role)一行 {agent_or_role, current: [当前在用 policy_id], latest}。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo, task_card_lookup_scope

    root = Path(governance_root)
    if now is None:
        # AIPOS-F177 件③: 只读作用域(请求级 / 视图级)内同一治理根只推导一次, 各调用方拿深拷贝; 作用域外照旧每次现算
        return lookup_scope_memo(root, "autonomy_policy.envelope_overview", lambda: envelope_overview(root, now=utc_now()),
                                 copy_out=True)
    with task_card_lookup_scope(root):  # 只读推导: 已放行认领计数整根只数一趟(F142 同一作用域, 可重入)
        return _envelope_overview(root, now)


def _envelope_overview(root: Path, now: datetime) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for pid in policy_ids(root):
        usage = envelope_usage(root, pid, now=now)
        policy = usage.get("policy") or {}
        rows.append({"policy_id": pid, "state": usage["state"], "usage": usage, "policy": policy,
                     "agent_or_role": str(policy.get("agent_or_role") or ""), "_from": _parse_iso(policy.get("active_from"))})
    successors = _supersession(rows)  # AIPOS-F185 件③: 接替判定唯一实现(门挑选信封 select_envelope 同读)
    migrated = authorization_superseded_policies(root)  # AIPOS-F224: 已迁入项目授权的旧信封 = superseded(接替者 = 该授权; 文件不改不删)
    covered = authorization_covered_policies(root, rows, now)  # AIPOS-F246 件⑤: 被更新的在用项目授权全覆盖的旧信封 = 已被它接替
    for row in rows:
        row["superseded_by"] = migrated.get(row["policy_id"]) or successors[row["policy_id"]] or covered.get(row["policy_id"])
    out: list[dict[str, Any]] = []
    for row in rows:
        usage, policy, state = row["usage"], row["policy"], row["state"]
        why = envelope_warning(usage) if state in ("active", "exhausted", "expired") else None
        if (state in ("active", "not_yet_active") and row["superseded_by"]) or row["policy_id"] in migrated:
            status = "superseded"  # AIPOS-F224: 迁入项目授权的旧信封一律退役为历史(superseded), 不论原用量状态
        elif state == "active" and why:
            status = "warn"
        else:
            status = state
        current = not row["superseded_by"] and status in _CURRENT_STATUSES
        # 续签计划只为当前在用者算(next_policy_id 要扫全部信封; 历史信封不续签)
        plan = (renew_plan(root, usage, identities={row["agent_or_role"]} - {""} or {"advisor"}, task_id="<在推卡>", now=now)
                if policy and current else {"next_policy_id": None, "renew_command": None, "note": ""})
        out.append({
            "policy_id": row["policy_id"], "agent_or_role": row["agent_or_role"], "driver_role": envelope_driver_role(row["agent_or_role"]),
            "state": state, "status": status, "current": current, "superseded_by": row["superseded_by"],
            "why": why if status in ("warn", "exhausted", "expired") else None,
            "used": usage.get("used"), "max_tasks": usage.get("max_tasks"), "remaining": usage.get("remaining"),
            "active_from": policy.get("active_from"), "expires_at": usage.get("expires_at"), "seconds_to_expiry": usage.get("seconds_to_expiry"),
            "task_mode": policy.get("task_selector_task_mode") or "", "lane_repos": list(policy.get("task_selector_lane_repo") or []),
            "launch_harnesses": list(policy.get("launch_harnesses") or []), "project_selector": policy.get("task_selector_project") or "",
            "task_ids": list(policy.get("task_selector_task_ids") or []),
            "next_policy_id": plan["next_policy_id"], "renew_command": plan["renew_command"], "renew_note": plan["note"],
            "usage": usage,  # 门侧用量真相原样(续签计划 renew_plan 的入参; 对外展示的调用方自行裁剪)
        })
    out.sort(key=lambda r: (str(r["active_from"] or ""), r["policy_id"]), reverse=True)
    drivers: dict[str, dict[str, Any]] = {}
    for row in out:  # 新→旧: 每个驱动方首见者 = latest
        entry = drivers.setdefault(row["agent_or_role"], {"agent_or_role": row["agent_or_role"], "current": [], "latest": row["policy_id"]})
        if row["current"]:
            entry["current"].append(row["policy_id"])
    return {"governance_root": str(root), "envelopes": out, "drivers": list(drivers.values())}


def envelope_status(governance_root: Path | str, policy_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """一个信封在 envelope_overview 里的那一行(loop status 预警按运行所用信封取); 不在声明目录 = None。"""
    pid = str(policy_id or "").strip()
    return next((r for r in envelope_overview(governance_root, now=now)["envelopes"] if r["policy_id"] == pid), None)


# ---------------------------------------------------------------------------
# AIPOS-F224: 项目常设授权(project authorization)——每个项目 Owner 签一次(车道 × 任务类型 × 驱动方 / 主机 / 拉起 / 到期 + 总卡数 / 禁区),
# 范围内 loop 自动覆盖, 越界才升 Owner 一次扩大授权。声明 = verbs.schema lybra_loop.envelope.project_authorization(本节唯一读取口
# project_authorization_declaration)。覆盖判定只有一处: match_claim_envelope 遇授权记录分派到 judge_project_authorization(门与 loop 同读);
# 挑选优先级(授权先、未迁移旧信封兼容)只在 select_envelope。记录只追加修订: <authorizations_root>/<authorization_id>/r<修订号>.md。
# ---------------------------------------------------------------------------

#: 授权记录载入后(normalize_authorization)的种类标记键; 值 = 声明 record_type。信封族函数据此分派, 信封本身无此键(行为不变)。
GRANT_KIND_KEY = "grant_kind"
_AUTH_DECL_KEYS = ("record_type", "id_template", "revision_file_template", "priority", "dimensions", "dimension_order", "zones",
                   "notice", "extend", "end_reason", "messages", "write_actions", "card_zones_field")
_AUTH_MESSAGES = ("out_of_bound", "extend", "no_extend", "migrated", "notice")
_AUTH_REVISION_RE = re.compile(r"^r(\d{1,9})\.md$")
_AUTH_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_AUTH_DRIVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
#: 越界维度中不能以「扩大授权」解除者(须 Owner 重新签发): 撤销 / 未生效 / 项目不符
_AUTH_NO_EXTEND = ("revoked", "not_yet_active", "project")
AUTHORIZATION_FRONTMATTER_ORDER = [
    "record_type", "authorization_id", "revision", "project", "status", "approved_by_owner", "owner_approval_ref", "action",
    "revised_at", "active_from", "expires_at", "max_tasks", "lanes", "hosts", "launch_harnesses", "zones_open",
    "supersedes_policies", "previous_revision", "notes",
]


def project_authorization_declaration() -> dict[str, Any]:
    """verbs.schema lybra_loop.envelope.project_authorization(fail-closed): 键齐; dimension_order 恰为 dimensions 的键; 每维有 label 与
    error_code 且 error_code ∈ transitions envelope_guards(拒因码唯一声明); notice / extend 数值; zones 非空; messages 齐。"""
    from tools.schema_loader import SchemaLoadError

    decl = loop_envelope_declaration().get("project_authorization")
    if not isinstance(decl, dict) or any(k not in decl for k in _AUTH_DECL_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_loop.envelope.project_authorization 未声明齐 {list(_AUTH_DECL_KEYS)}")
    dims, order = decl["dimensions"], decl["dimension_order"]
    notice, extend, msgs = decl["notice"], decl["extend"], decl["messages"]
    codes = set(envelope_guard_codes())
    if (not isinstance(dims, dict) or not isinstance(order, list) or set(order) != set(dims) or len(order) != len(dims)
            or any(not isinstance(v, dict) or not str(v.get("label") or "").strip() or str(v.get("error_code") or "") not in codes
                   for v in dims.values())
            or not isinstance(decl["zones"], dict) or not decl["zones"]
            or not isinstance(notice, dict) or not isinstance(notice.get("used_ratio"), (int, float)) or isinstance(notice.get("used_ratio"), bool)
            or not isinstance(notice.get("expiry_days"), int) or isinstance(notice.get("expiry_days"), bool)
            or not isinstance(notice.get("messages"), dict) or any(not isinstance(notice["messages"].get(k), str) for k in ("quota", "expiry"))
            or not isinstance(extend, dict) or not isinstance(extend.get("quota_step_ratio"), (int, float))
            or not isinstance(extend.get("expiry_days"), int) or extend["expiry_days"] < 1
            or not isinstance(msgs, dict) or any(not isinstance(msgs.get(k), str) for k in _AUTH_MESSAGES)
            or not isinstance(decl["write_actions"], list) or set(decl["write_actions"]) != {"grant", "extend", "revoke"}
            or not str(decl["record_type"]).strip() or "{project}" not in str(decl["id_template"])
            or "{revision" not in str(decl["revision_file_template"])):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.envelope.project_authorization 形不合(dimensions{label, error_code∈envelope_guards} "
                              "与 dimension_order 一致 / zones 非空 / notice{used_ratio, expiry_days, messages{quota, expiry}} / "
                              "extend{quota_step_ratio, expiry_days≥1} / messages / write_actions{grant, extend, revoke})")
    return decl


def is_authorization(policy: Any) -> bool:
    """已载入的授予物(load_policy 结果)是否为项目授权(信封无 GRANT_KIND_KEY)。"""
    return isinstance(policy, dict) and bool(policy.get(GRANT_KIND_KEY))


def authorizations_dir(governance_root: Path | str) -> Path:
    """AIPOS-F224: 授权目录唯一读取口 = project.json paths.authorizations_root(config.schema 声明, 缺省 5_tasks/authorizations;
    相对治理根或绝对), 经 workspace_config.project_paths 解析(与 policies_root 同一读取口)。"""
    from tools.aipos_cli.workspace_config import project_paths

    return Path(project_paths(Path(governance_root))["authorizations_root"])


def authorization_id_for(project: str) -> str:
    """项目缺省授权 ID(声明 id_template; 一个项目一份授权, 多位顾问按车道共享, 设计稿 §8 第 2 条)。"""
    return str(project_authorization_declaration()["id_template"]).format(project=str(project or "").strip() or "project")


def _revision_files(directory: Path) -> list[tuple[int, Path]]:
    if not directory.is_dir():
        return []
    out = []
    for path in directory.iterdir():
        hit = _AUTH_REVISION_RE.match(path.name)
        if hit and path.is_file():
            out.append((int(hit.group(1)), path))
    return sorted(out)


def authorization_ids(governance_root: Path | str) -> list[str]:
    """声明授权目录下全部授权 ID(有至少一份修订的子目录名, 升序)。目录不存在 = [](项目无授权, 行为不变)。"""
    adir = authorizations_dir(governance_root)
    if not adir.is_dir():
        return []
    return sorted(p.name for p in adir.iterdir() if p.is_dir() and POLICY_ID_PATTERN.fullmatch(p.name) and _revision_files(p))


def is_authorization_id(governance_root: Path | str, ref: Any) -> bool:
    """ref 是否命名本项目一份项目授权(授权目录下有该 ID 的修订)。只读目录存在性。"""
    rid = str(ref or "").strip()
    if not rid or not POLICY_ID_PATTERN.fullmatch(rid):
        return False
    return bool(_revision_files(authorizations_dir(governance_root) / rid))


def authorization_revision_relpath(governance_root: Path | str, authorization_id: str, revision: int) -> str:
    """授权修订文件相对治理根的路径(门写入面 planned_writes 用)。声明落点在治理根外 = ValueError(fail-closed)。"""
    root = Path(governance_root).resolve()
    name = str(project_authorization_declaration()["revision_file_template"]).format(revision=int(revision))
    path = (authorizations_dir(root) / str(authorization_id) / name).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"authorizations_root 声明落在治理根外({path}), 门只在治理根内落授权(project.json paths.authorizations_root)") from exc


def _str_list(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(dict.fromkeys(v.strip() for v in value if v.strip()))


def _normalize_lanes(value: Any) -> list[dict[str, Any]] | None:
    """lanes = [{repo, task_modes, drivers}](形不合 = None)。同 repo 多条 = 合并(并集, 保序)。"""
    if not isinstance(value, list) or not value:
        return None
    merged: dict[str, dict[str, list[str]]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("repo"), str) or not item["repo"].strip():
            return None
        modes, drivers = _str_list(item.get("task_modes")), _str_list(item.get("drivers"))
        if not modes or not drivers:
            return None
        lane = merged.setdefault(item["repo"].strip(), {"task_modes": [], "drivers": []})
        lane["task_modes"] = list(dict.fromkeys(lane["task_modes"] + modes))
        lane["drivers"] = list(dict.fromkeys(lane["drivers"] + drivers))
    return [{"repo": repo, **lane} for repo, lane in merged.items()]


def normalize_authorization(metadata: dict[str, Any], *, governance_root: Path | str, revision_path: str = "") -> dict[str, Any] | None:
    """授权修订 frontmatter → 规整后的授予物(形不合 = None, fail-closed 不授权)。带信封族兼容键(policy_id = authorization_id、mode、
    task_selector_project、task_selector_lane_repo = 各车道仓并集、launch_harnesses), 使门 / loop 的信封族调用方按同一 owner_policy_ref 认它;
    判定一律经 match_claim_envelope → judge_project_authorization(不读兼容键做判定)。"""
    decl = project_authorization_declaration()
    if not isinstance(metadata, dict) or str(metadata.get("record_type") or "").strip() != str(decl["record_type"]):
        return None
    aid = str(metadata.get("authorization_id") or "").strip()
    if not aid or not POLICY_ID_PATTERN.fullmatch(aid):
        return None
    try:
        revision, max_tasks = int(metadata.get("revision")), int(metadata.get("max_tasks"))
    except (TypeError, ValueError):
        return None
    lanes = _normalize_lanes(metadata.get("lanes"))
    lists = {k: _str_list(metadata.get(k)) for k in ("hosts", "launch_harnesses", "zones_open", "supersedes_policies")}
    if revision < 1 or lanes is None or any(v is None for v in lists.values()):
        return None
    from tools.aipos_cli.workspace_config import repo_name_set

    project = str(metadata.get("project") or "").strip()
    return {
        GRANT_KIND_KEY: str(decl["record_type"]),
        "policy_id": aid, "authorization_id": aid, "revision": revision, "revision_path": revision_path,
        "governance_root": str(Path(governance_root)), "project": project,
        "mode": AUTONOMY_MODE_PREAUTHORIZED, "status": str(metadata.get("status") or "").strip(),
        "approved_by_owner": bool(metadata.get("approved_by_owner")),
        "owner_approval_ref": str(metadata.get("owner_approval_ref") or "").strip(),
        "action": str(metadata.get("action") or "").strip(), "revised_at": str(metadata.get("revised_at") or "").strip(),
        "active_from": str(metadata.get("active_from") or "").strip(), "expires_at": str(metadata.get("expires_at") or "").strip(),
        "max_tasks": max_tasks, "lanes": lanes, **{k: list(v or []) for k, v in lists.items()},
        "previous_revision": metadata.get("previous_revision"), "notes": str(metadata.get("notes") or "").strip(),
        # 信封族兼容键(调用方按同一 owner_policy_ref / 拉起授权 / lane 指称解析认它; 判定不读)
        "agent_or_role": "", "task_selector_task_mode": "", "task_selector_project": project, "task_selector_task_ids": [],
        "task_selector_lane_repo": repo_name_set([lane["repo"] for lane in lanes]),
    }


def load_authorization(governance_root: Path | str, authorization_id: str, *, revision: int | None = None) -> dict[str, Any] | None:
    """读一份授权的最新修订(或指定修订)。缺 / 形不合 / ID 与目录不符 / 修订号与文件名不符 = None(★A1: 读不出的授权不授予任何东西)。"""
    root = Path(governance_root)
    aid = str(authorization_id or "").strip()
    if not aid or not POLICY_ID_PATTERN.fullmatch(aid):
        return None
    adir = authorizations_dir(root).resolve()
    files = _revision_files(adir / aid)
    if revision is not None:
        files = [(n, p) for n, p in files if n == int(revision)]
    if not files:
        return None
    number, path = files[-1]
    try:
        path.resolve().relative_to(adir)
        metadata, _body, _warn, _fence = read_markdown_frontmatter(path)
    except (OSError, ValueError):
        return None
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = str(path)
    auth = normalize_authorization(metadata, governance_root=root, revision_path=rel)
    if auth is None or auth["authorization_id"] != aid or auth["revision"] != number:
        return None
    return auth


def authorization_history(governance_root: Path | str, authorization_id: str) -> list[dict[str, Any]]:
    """一份授权的全部修订(旧→新; 形不合的修订 = {revision, invalid: True})。看板「项目授权」修订史读此。"""
    root = Path(governance_root)
    out: list[dict[str, Any]] = []
    for number, _path in _revision_files(authorizations_dir(root) / str(authorization_id)):
        auth = load_authorization(root, authorization_id, revision=number)
        out.append(auth if auth is not None else {"revision": number, "invalid": True})
    return out


def authorization_superseded_policies(governance_root: Path | str) -> dict[str, str]:
    """已迁入项目授权的旧信封 {policy_id: authorization_id}(任一修订 supersedes_policies 列名即算; 授权撤销不令旧信封复活)。
    接替判定在信封族的唯一并入点: select_envelope / envelope_successor / envelope_overview 同读。"""
    root = Path(governance_root)
    out: dict[str, str] = {}
    for aid in authorization_ids(root):
        for rev in authorization_history(root, aid):
            for pid in rev.get("supersedes_policies") or []:
                out.setdefault(str(pid), aid)
    return out


def authorization_covered_policies(governance_root: Path | str, rows: list[dict[str, Any]], now: datetime) -> dict[str, str]:
    """AIPOS-F246 件⑤(Owner 10-28 实测: 看板把已被本项目授权取代的旧信封显示为「在用」): 展示口径的接替——
    门挑选次序已是「项目授权覆盖则用授权, 未覆盖才兼容旧信封」(select_envelope → judge_authorizations), 未写进 supersedes_policies 的
    旧信封却仍按信封自身状态显示在用。本函数给 envelope_overview(信封展示状态唯一推导, 看板信封页 / 收件箱 / brief 同读)补上这条接替:
    旧信封 A 被授权 G 接替 ⇔ G 用量真相为在用(envelope_usage state=active: 生效、在时间窗、额度未尽)、G 的 active_from 严格晚于 A
    (同 _supersession「新替旧」)、G 项目 = A 的项目选择器(若有)、且 A 覆盖的每个 车道仓 × 任务类型 都在 G 某车道内并以 A 的驱动方
    为该车道驱动方(A 不限车道 = 本项目全部声明车道 workspace_config.project_lane_keys; 不限任务类型 = enums task_mode 全部)。
    rows = _envelope_overview 的行({policy_id, state, policy, _from})。返回 {policy_id: authorization_id}; 只读。门挑选不读本函数
    (授权额度尽 / 过期时门照旧回落旧信封, 那时 G 不在用、本函数也不判接替)。"""
    root = Path(governance_root)
    grants: list[tuple[dict[str, Any], datetime]] = []
    for aid in authorization_ids(root):
        auth = load_authorization(root, aid)
        frm = _parse_iso((auth or {}).get("active_from"))
        if auth is None or frm is None or envelope_usage(root, aid, now=now).get("state") != "active":
            continue
        grants.append((auth, frm))
    if not grants:
        return {}
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, _same_path, project_lane_keys, project_repos, repo_name_set
    from tools.schema_loader import get_enum_values

    items = {str(k): str(v) for k, v in (project_repos(root).get("items") or {}).items()}

    def equiv(ref: str) -> set[str]:
        out = {ref}
        if ref in items:
            out.add(items[ref])
        for name, path in items.items():
            if ref.startswith("/") and _same_path(Path(path), Path(ref)):
                out |= {name, path}
        return out

    try:
        all_lanes = [str(x) for x in project_lane_keys(root)]
    except CardRepoUnresolved:  # 项目车道推不出(无 repos / code_repo 声明)= 不限车道的旧信封无从判全覆盖, 不判接替(fail-closed: 照信封自身状态)
        all_lanes = []
    out: dict[str, str] = {}
    for row in rows:
        policy = row.get("policy") or {}
        driver = str(policy.get("agent_or_role") or "").strip()
        if row.get("state") not in ("active", "not_yet_active", "exhausted", "warn") or not driver or row.get("_from") is None:
            continue
        mode = str(policy.get("task_selector_task_mode") or "").strip()
        modes = [mode] if mode else list(get_enum_values("task_mode"))
        lanes = repo_name_set(policy.get("task_selector_lane_repo")) or all_lanes
        selector = str(policy.get("task_selector_project") or "").strip()
        best: tuple[datetime, str] | None = None
        for auth, frm in grants:
            if frm <= row["_from"] or (selector and selector != str(auth.get("project") or "")):
                continue
            ok = bool(lanes) and all(
                any(equiv(str(g["repo"])) & equiv(lane) and driver in g["drivers"] and set(modes) <= set(g["task_modes"])
                    for g in auth.get("lanes") or [])
                for lane in lanes)
            if ok and (best is None or (frm, str(auth["authorization_id"])) > best):
                best = (frm, str(auth["authorization_id"]))
        if best is not None:
            out[str(row["policy_id"])] = best[1]
    return out


def _carried_roots(repo_root: Path, policy_id: str) -> set[str]:
    """授权承接的根业务卡 = 其迁入的旧信封已放行的根卡(不扣授权额度; 本卡链后续步骤视同已由授权放行)。非授权 = 空集。"""
    from tools.aipos_cli.task_loader import lookup_scope_memo

    if not is_authorization_id(repo_root, policy_id):
        return set()
    auth = load_authorization(repo_root, policy_id)
    if auth is None:
        return set()
    roots = lookup_scope_memo(Path(repo_root), "autonomy_policy.preauthorized_claim_roots",
                              lambda: _preauthorized_claim_roots(Path(repo_root)))
    carried: set[str] = set()
    for old in auth.get("supersedes_policies") or []:
        carried.update((roots["roots"].get(str(old)) or {}).keys())
    return carried


def card_host(card_fm: dict[str, Any] | None) -> str:
    """卡的主机(声明 host_rule): assigned_to(缺则 agent_instance)实例名按 roles.schema naming.template 解析出的 host 段; 解析不出 = ""。"""
    from tools.aipos_cli.naming_profile import parse_instance_name

    fm = card_fm if isinstance(card_fm, dict) else {}
    inst = str(fm.get("assigned_to") or fm.get("agent_instance") or "").strip()
    parsed = parse_instance_name(inst) if inst else None
    return str((parsed or {}).get("host") or "").strip()


def card_zones(card_fm: dict[str, Any] | None) -> list[str]:
    """卡面声明触及的禁区(card.schema authorization_zones, 字段名读声明 card_zones_field; 缺 = [])。形不合 = 一项原文说明(判为越界, fail-closed)。"""
    field = str(project_authorization_declaration()["card_zones_field"])
    value = (card_fm or {}).get(field) if isinstance(card_fm, dict) else None
    items = _str_list(value)
    return [f"<{field} 形不合: {value!r}>"] if items is None else items


def _lane_name(governance_root: Path, refs: list[str]) -> str:
    """卡 lane 指称 → 写进授权车道的仓名: project.json repos 清单里的仓名优先, 否则卡面原值, 再否则解析路径。"""
    from tools.aipos_cli.workspace_config import project_repos

    try:
        names = set((project_repos(governance_root).get("items") or {}))
    except (OSError, ValueError) as exc:  # 清单读不出 = 不猜仓名, 退回卡面指称(原因写进轨迹)
        trace_envelope({"phase": "authorization_lane_name", "error": f"{type(exc).__name__}: {exc}"})
        names = set()
    named = [r for r in refs if r in names]
    if named:
        return named[0]
    plain = [r for r in refs if not Path(r).is_absolute()]
    return (plain or refs or [""])[0]


def judge_project_authorization(
    auth: dict[str, Any],
    *,
    task_id: str,
    task_mode: str,
    project: str,
    lane: dict[str, Any] | None,
    identities: set[str],
    now: datetime,
    released_count: int,
    card_released: bool = False,
    driver: str = "",
    card_fm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """项目授权覆盖判定(唯一实现; match_claim_envelope 遇授权分派到此, 门与 loop 同读)。次序 = 声明 dimension_order:
    撤销 / 未生效 / 到期 / 项目 → 车道(卡 lane 指称 ∈ 授权车道仓)→ 任务类型(命中车道的 task_modes 含卡 task_mode)→ 驱动方(命中车道的 drivers
    含驱动方身份之一)→ 主机(card_host ∈ hosts)→ 禁区(card_zones ⊆ zones_open)→ 总额(本授权已放行根业务卡 < max_tasks, 本卡链已放行 = 不拦)。
    task_id 空 = 任务无关(工位推导等无卡场景): 只判有效性与驱动方覆盖任一车道。判定对象卡由调用方经 envelope_subject 给出(审计卡 = 被审卡)。
    返回 {matched, reason, error_code, dimension, label, facts}; facts = 扩大授权建议(authorization_extension)所需的卡面事实。"""
    decl = project_authorization_declaration()
    dims = decl["dimensions"]
    root = Path(str(auth.get("governance_root") or "."))
    ident = {str(i).strip() for i in identities if str(i or "").strip()}
    tid = str(task_id or "").strip()
    mode = str(task_mode or "").strip()
    facts: dict[str, Any] = {"task_id": tid, "task_mode": mode, "driver": str(driver or "").strip() or (sorted(ident)[0] if ident else ""),
                             "identities": sorted(ident), "lane_refs": [], "lane_name": "", "host": "", "zones": [],
                             "lanes_hit": [], "mode_hit": []}
    aid, rev = auth.get("authorization_id"), auth.get("revision")

    def verdict(dimension: str | None, reason: str) -> dict[str, Any]:
        spec = dims.get(dimension) if dimension else None
        return {"matched": dimension is None, "reason": reason, "error_code": (spec or {}).get("error_code"), "dimension": dimension,
                "label": (spec or {}).get("label"), "facts": facts, "authorization_id": aid, "revision": rev}

    frm, exp = _parse_iso(auth.get("active_from")), _parse_iso(auth.get("expires_at"))
    if auth.get("status") != POLICY_STATUS_ACTIVE or not auth.get("approved_by_owner"):
        return verdict("revoked", f"project grant {aid} r{rev} status={auth.get('status') or 'unset'} (not active / not owner-approved)")
    if frm is None or exp is None:
        return verdict("expiry", f"project grant {aid} r{rev} time window missing or unparseable")
    if now < frm:
        return verdict("not_yet_active", f"project grant {aid} r{rev} not yet active (active_from={auth.get('active_from')})")
    if now >= exp:
        return verdict("expiry", f"project grant {aid} r{rev} expired (expires_at={auth.get('expires_at')})")
    if project and auth.get("project") and str(project).strip() != auth["project"]:
        return verdict("project", f"card project {project!r} ≠ grant project {auth['project']!r}")
    lanes = list(auth.get("lanes") or [])
    if not tid:
        if any(ident & set(l["drivers"]) for l in lanes):
            return verdict(None, f"matched project grant {aid} r{rev} (task-independent: driver covered)")
        # 任务无关(工位接入推导 owner_policy_ref 等): 实例名 host 段在授权允许主机内的工位 = 在本项目授权范围内(执行 / 审计工位不是驱动方)
        hosts = [card_host({"assigned_to": i}) for i in sorted(ident)]
        if any(h and h in (auth.get("hosts") or []) for h in hosts):
            return verdict(None, f"matched project grant {aid} r{rev} (task-independent: workstation host in grant hosts)")
        return verdict("driver", f"identities {sorted(ident)} neither lane drivers nor on grant hosts of {aid} r{rev}")
    if isinstance(lane, dict):
        refs = [str(r) for r in (lane.get("refs") or [])]
        lane_error = str(lane.get("error") or "")
    else:
        fm_for_lane = card_fm if isinstance(card_fm, dict) else (_card_frontmatter_for(root, tid) or {})
        got = card_lane_refs(root, {**fm_for_lane, "task_id": tid})
        refs, lane_error = list(got["refs"]), str(got["error"] or "")
    facts["lane_refs"] = refs
    facts["lane_name"] = _lane_name(root, refs) if refs else ""
    lanes_hit = [l for l in lanes if l["repo"] in refs]
    facts["lanes_hit"] = [l["repo"] for l in lanes_hit]
    if not lanes_hit:
        where = f"card repo unresolvable ({lane_error})" if lane_error else f"card lane resolves to {refs}"
        return verdict("lane", f"{where}; not in grant lanes {[l['repo'] for l in lanes]}")
    mode_hit = [l for l in lanes_hit if mode in l["task_modes"]]
    facts["mode_hit"] = [l["repo"] for l in mode_hit]
    if not mode_hit:
        return verdict("task_mode", f"task_mode {mode or '(unset)'!r} not in lane task_modes "
                                    f"{ {l['repo']: l['task_modes'] for l in lanes_hit} }")
    if not any(ident & set(l["drivers"]) for l in mode_hit):
        return verdict("driver", f"driver identities {sorted(ident)} not in lane drivers { {l['repo']: l['drivers'] for l in mode_hit} }")
    fm = card_fm if isinstance(card_fm, dict) else _card_frontmatter_for(root, tid)
    if fm is None:
        return verdict("host", f"card {tid} frontmatter unreadable; host cannot be judged (fail-closed)")
    host = card_host(fm)
    facts["host"] = host
    if not host or host not in (auth.get("hosts") or []):
        return verdict("host", f"card host {host or '(unparseable assigned_to)'!r} not in grant hosts {auth.get('hosts')}")
    zones = card_zones(fm)
    facts["zones"] = zones
    closed = [z for z in zones if z not in (auth.get("zones_open") or [])]
    if closed:
        facts["zones_closed"] = closed
        return verdict("zone", f"card touches forbidden zones {closed} (zones_open={auth.get('zones_open')})")
    max_tasks = int(auth.get("max_tasks") or 0)
    if max_tasks <= 0 or (released_count >= max_tasks and not card_released):
        return verdict("quota", f"grant count bound reached ({released_count}/{max_tasks} business cards)")
    if card_released:
        return verdict(None, f"matched project grant {aid} r{rev} (card chain already released; later steps not quota-bound; "
                             f"released {released_count}/{max_tasks} business cards)")
    return verdict(None, f"matched project grant {aid} r{rev} (released {released_count}/{max_tasks})")


def _authorization_view(auth: dict[str, Any], identities: set[str]) -> dict[str, Any]:
    """挑中的授权给本驱动方的视图: task_selector_lane_repo = 本驱动方被授权的车道仓(归属视角 machine_zone.owner_viewer 等按此读),
    agent_or_role = 命中的驱动方身份。判定不读本视图。"""
    from tools.aipos_cli.workspace_config import repo_name_set

    view = dict(auth)
    mine = [l["repo"] for l in auth.get("lanes") or [] if identities & set(l["drivers"])]
    hit = sorted(identities & {d for l in auth.get("lanes") or [] for d in l["drivers"]})
    view["task_selector_lane_repo"] = repo_name_set(mine)
    view["agent_or_role"] = hit[0] if hit else ""
    return view


def judge_authorizations(
    root: Path,
    ids: list[str],
    *,
    triples: list[tuple[str, str, str | None]],
    task: dict[str, Any] | None,
    now: datetime,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """授权候选逐个判(身份三元组次序同信封); 返回 (挑中授权的驱动方视图 | None, 每个未覆盖授权最深的那条判定)。"""
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    order = list(project_authorization_declaration()["dimension_order"])
    failures: list[dict[str, Any]] = []
    task_key = str((task or {}).get("task_id") or "").strip()
    with task_card_lookup_scope(root):
        subject = None
        if task is not None:
            subject = envelope_subject(root, task_id=str(task.get("task_id") or ""), task_mode=str(task.get("task_mode") or ""),
                                       project=str(task.get("project") or ""), reviewed_task_id=str(task.get("reviewed_task_id") or ""),
                                       lane=task.get("lane") if isinstance(task.get("lane"), dict) else None, with_lane=True)
        for aid in ids:
            auth = load_authorization(root, aid)
            if auth is None:
                failures.append({"matched": False, "authorization_id": aid, "revision": None, "dimension": None, "label": None,
                                 "reason": f"{aid}: 项目授权最新修订缺失或格式不合规",
                                 "error_code": ENVELOPE_ERROR_POLICY_NOT_FOUND, "facts": {}})
                continue
            quota = envelope_quota_state(root, aid, task_key or None)
            best: dict[str, Any] | None = None
            for agent_instance, actor, role in triples:
                identities = {x for x in (agent_instance, actor, role) if x}
                if subject is not None:
                    sid, smode, sproj, slane = subject
                    judged = judge_project_authorization(auth, task_id=sid, task_mode=smode, project=sproj, lane=slane, identities=identities,
                                                         now=now, released_count=int(quota["used"]), card_released=bool(quota["card_released"]),
                                                         driver=agent_instance or actor)
                else:
                    judged = judge_project_authorization(auth, task_id="", task_mode="", project="", lane=None, identities=identities, now=now,
                                                         released_count=int(quota["used"]), driver=agent_instance or actor)
                trace_envelope({"phase": "judge_project_authorization", "authorization_id": aid, "revision": auth.get("revision"),
                                "task_id": task_key, "identities": sorted(identities), "matched": judged["matched"],
                                "dimension": judged["dimension"], "reason": judged["reason"]})
                if judged["matched"]:
                    return _authorization_view(auth, identities), []
                depth = order.index(judged["dimension"]) if judged["dimension"] in order else -1
                if best is None or depth > (order.index(best["dimension"]) if best["dimension"] in order else -1):
                    best = judged
            if best is not None:
                failures.append({**best, "auth": auth, "used": int(quota["used"])})
    return None, failures


def authorization_refusal(
    governance_root: Path | str,
    *,
    identities: list[tuple[str | None, str | None, str | None]],
    task: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """loop / 收件箱的越界出口数据: 本项目有授权而没有一份覆盖本卡与驱动方 = 最深的那条越界判定(含 dimension / label / reason / facts /
    auth / used); 有授权覆盖 / 项目无授权 = None。判定同 select_envelope(judge_authorizations, 判据 judge_project_authorization)。"""
    root = Path(governance_root)
    ids = authorization_ids(root)
    if not ids:
        return None
    triples = [(str(a or "").strip(), str(b or "").strip(), (str(c).strip() or None) if c else None) for a, b, c in identities]
    found, failures = judge_authorizations(root, ids, triples=[t for t in triples if t[0] or t[1] or t[2]], task=task, now=now or utc_now())
    if found is not None or not failures:
        return None
    order = list(project_authorization_declaration()["dimension_order"])
    return max(failures, key=lambda f: order.index(f["dimension"]) if f.get("dimension") in order else -1)


def authorization_notices(governance_root: Path | str, authorization_id: str, *, now: datetime | None = None) -> list[str]:
    """告知(第二档, 不阻塞; 唯一判定): 有效授权已放行根业务卡 ≥ notice.used_ratio × 总卡数, 或距到期 ≤ notice.expiry_days 天 = 各一行。
    无效 / 用尽 / 过期 = [](越界走 authorization_refusal, 不归告知管)。看板「项目授权」/ 收件箱 / loop 启动行同读。"""
    decl = project_authorization_declaration()
    notice = decl["notice"]
    usage = envelope_usage(governance_root, authorization_id, now=now)
    if usage.get("state") != "active":
        return []
    out: list[str] = []
    used, max_tasks = int(usage.get("used") or 0), int(usage.get("max_tasks") or 0)
    ratio = float(notice["used_ratio"])
    if max_tasks > 0 and used >= ratio * max_tasks:
        out.append(str(notice["messages"]["quota"]).format(used=used, max_tasks=max_tasks, ratio=ratio))
    seconds = usage.get("seconds_to_expiry")
    if isinstance(seconds, int) and seconds <= int(notice["expiry_days"]) * 86400:
        out.append(str(notice["messages"]["expiry"]).format(expires_at=usage.get("expires_at"), days=max(0, seconds // 86400),
                                                           expiry_days=notice["expiry_days"]))
    return out


def authorization_overview(governance_root: Path | str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """本项目全部授权的状态(看板「项目授权」主视图 / CLI show / 收件箱告知同读): 每份 = 最新修订 + 门侧同一用量真相(envelope_usage:
    已放行根业务卡, 迁入旧信封承接的根卡不计)+ 告知 + 修订史 + 迁入的旧信封。"""
    root = Path(governance_root)
    now = now or utc_now()
    out: list[dict[str, Any]] = []
    for aid in authorization_ids(root):
        auth = load_authorization(root, aid)
        history = authorization_history(root, aid)
        if auth is None:
            out.append({"authorization_id": aid, "state": "missing", "revisions": history})
            continue
        usage = envelope_usage(root, aid, now=now)
        out.append({
            "authorization_id": aid, "revision": auth["revision"], "revision_path": auth["revision_path"], "project": auth["project"],
            "state": usage.get("state"), "status": auth["status"], "used": usage.get("used"), "max_tasks": usage.get("max_tasks"),
            "remaining": usage.get("remaining"), "active_from": auth["active_from"], "expires_at": auth["expires_at"],
            "seconds_to_expiry": usage.get("seconds_to_expiry"), "lanes": auth["lanes"], "hosts": auth["hosts"],
            "launch_harnesses": auth["launch_harnesses"], "zones_open": auth["zones_open"],
            "supersedes_policies": auth["supersedes_policies"], "owner_approval_ref": auth["owner_approval_ref"],
            "released_cards": usage.get("released_cards") or [], "notices": authorization_notices(root, aid, now=now),
            "revisions": [{k: r.get(k) for k in ("revision", "action", "status", "revised_at", "owner_approval_ref", "max_tasks",
                                                  "expires_at", "invalid")} for r in history],
        })
    return out


def authorization_notice_command(governance_root: Path | str, row: dict[str, Any], *, now: datetime | None = None) -> str:
    """AIPOS-F232 件③: 告知行的扩大授权建议命令(唯一渲染; 收件箱告知 / loop status / brief 同读): 总卡数 + max(1, ceil(extend.quota_step_ratio ×
    原总卡数)), 到期 = 现在 + extend.expiry_days 天(声明 project_authorization.extend)。row = authorization_overview 的一行。"""
    import math
    from datetime import timedelta

    from tools.aipos_cli.clock import iso_z

    decl = project_authorization_declaration()
    total = int(row.get("max_tasks") or 0)
    step = max(1, math.ceil(float(decl["extend"]["quota_step_ratio"]) * total))
    later = iso_z(((now or utc_now()) + timedelta(days=int(decl["extend"]["expiry_days"]))).replace(microsecond=0))
    return render_authorization_command(governance_root, "extend", [f"--max-tasks {total + step}", f"--expires-at {later}"])


def authorization_warnings(governance_root: Path | str, *, now: datetime | None = None,
                           authorization_id: str | None = None) -> list[dict[str, Any]]:
    """AIPOS-F232 件③(F224 执行体自报: loop status / brief 预警只认信封): 有告知(authorization_notices: 已用 ≥ notice.used_ratio / 到期前
    notice.expiry_days 天)的项目授权各一行 {authorization_id, revision, why, command, line, used, max_tasks, expires_at, drivers}; line =
    messages.notice + 扩大命令(authorization_notice_command)。authorization_id 给定 = 只看该份。brief / loop status / 收件箱告知同读, 判定只在
    authorization_notices。"""
    decl = project_authorization_declaration()
    out: list[dict[str, Any]] = []
    for row in authorization_overview(governance_root, now=now):
        if authorization_id and str(row["authorization_id"]) != str(authorization_id):
            continue
        if not row.get("notices"):
            continue
        aid, why = str(row["authorization_id"]), "; ".join(row["notices"])
        command = authorization_notice_command(governance_root, row, now=now)
        line = decl["messages"]["notice"].format(authorization_id=aid, why=why) + f"; 扩大授权(Owner 执行): {command}"
        out.append({"authorization_id": aid, "revision": row.get("revision"), "why": why, "command": command, "line": line,
                    "used": row.get("used"), "max_tasks": row.get("max_tasks"), "expires_at": row.get("expires_at"),
                    "notices": list(row["notices"]),
                    "drivers": sorted({d for lane in row.get("lanes") or [] for d in lane.get("drivers") or []})})
    return out


# ---- 写: 授权块(签发 / 扩大 / 撤销)构造与校验 + owner_decision_record 同一写路径 -------------------------------------------------

def authorization_block(
    governance_root: Path | str,
    *,
    action: str,
    now: datetime | None = None,
    lanes: list[dict[str, Any]] | None = None,
    hosts: list[str] | None = None,
    launch_harnesses: list[str] | None = None,
    zones_open: list[str] | None = None,
    max_tasks: int | None = None,
    expires_at: str | None = None,
    supersedes_policies: list[str] | None = None,
    notes: str = "",
    authorization_id: str | None = None,
) -> dict[str, Any]:
    """owner_decision_record payload 的 project_authorization 块唯一构造(CLI / 看板 / 收件箱扩大授权同调)。
    grant = 给出的范围即全集(无现授权时 base_revision 0); extend = 在最新修订上并入(车道按仓合并任务类型与驱动方, 主机 / 拉起 / 禁区 / 迁入信封取并集,
    max_tasks / expires_at 给出即替换); revoke = 照抄最新修订、status 改撤销。时刻(active_from / revised_at)在此定死, 预演与确认同一快照。
    不校验(校验唯一在 build_authorization_decision, 预演即见拒因)。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.workspace_config import read_project_json

    root = Path(governance_root)
    now = now or utc_now()
    project = str(read_project_json(root).get("project") or "").strip()
    aid = str(authorization_id or "").strip() or (authorization_ids(root)[0] if authorization_ids(root) else authorization_id_for(project))
    current = load_authorization(root, aid)
    base = int(current["revision"]) if current else max([n for n, _p in _revision_files(authorizations_dir(root) / aid)] or [0])
    stamp = iso_z(now.replace(microsecond=0))
    if action == "grant" or current is None:
        block = {"lanes": _normalize_lanes(lanes) or list(lanes or []), "hosts": list(dict.fromkeys(hosts or [])),
                 "launch_harnesses": list(dict.fromkeys(launch_harnesses or [])), "zones_open": list(dict.fromkeys(zones_open or [])),
                 "max_tasks": max_tasks, "expires_at": expires_at, "active_from": stamp,
                 "supersedes_policies": list(dict.fromkeys(supersedes_policies or []))}
    else:
        merged = _normalize_lanes(list(current["lanes"]) + list(lanes or [])) or list(current["lanes"])
        block = {"lanes": merged, "hosts": list(dict.fromkeys(list(current["hosts"]) + list(hosts or []))),
                 "launch_harnesses": list(dict.fromkeys(list(current["launch_harnesses"]) + list(launch_harnesses or []))),
                 "zones_open": list(dict.fromkeys(list(current["zones_open"]) + list(zones_open or []))),
                 "max_tasks": int(max_tasks) if max_tasks is not None else current["max_tasks"],
                 "expires_at": expires_at or current["expires_at"], "active_from": current["active_from"],
                 "supersedes_policies": list(dict.fromkeys(list(current["supersedes_policies"]) + list(supersedes_policies or [])))}
    return {"action": action, "authorization_id": aid, "project": project, "base_revision": base, "revised_at": stamp,
            "notes": str(notes or "").strip(), **block}


def _validate_authorization_block(root: Path, block: Any, blocking: list[str]) -> dict[str, Any] | None:
    """授权块 → 将落盘的修订记录(拒因追加进 blocking; 有拒因仍尽量给出记录供预演展示)。校验全集: 动作 / ID / 项目 = project.json /
    基于最新修订(过期预演拒)/ 时间窗 / 总卡数 / 车道仓可解析(workspace_config.resolve_card_repo, 与信封 lane 同一解析)/ 任务类型 ∈ enums
    task_mode / 驱动方形 / 主机标签 / 拉起 ∈ enums harness.launch / 禁区 ∈ 声明 zones / 迁入信封存在。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, read_project_json, resolve_card_repo
    from tools.schema_loader import get_enum_values

    decl = project_authorization_declaration()
    if not isinstance(block, dict):
        blocking.append("project_authorization must be a mapping")
        return None
    action = str(block.get("action") or "").strip()
    if action not in decl["write_actions"]:
        blocking.append(f"project_authorization.action={action!r} 不在 {decl['write_actions']}")
    aid = str(block.get("authorization_id") or "").strip()
    if not POLICY_ID_PATTERN.fullmatch(aid):
        blocking.append(f"project_authorization.authorization_id={aid!r} 形不合")
        return None
    project = str(read_project_json(root).get("project") or "").strip()
    if str(block.get("project") or "").strip() != project:
        blocking.append(f"project_authorization.project={block.get('project')!r} ≠ 本治理根 project.json project {project!r}")
    current = load_authorization(root, aid)
    latest = max([n for n, _p in _revision_files(authorizations_dir(root) / aid)] or [0])
    try:
        base = int(block.get("base_revision"))
    except (TypeError, ValueError):
        base = -1
    if base != latest:
        blocking.append(f"授权 {aid} 最新修订 r{latest}, 本次基于 r{base}(预演已过期或并发修订), 请重新预演")
    if action in ("extend", "revoke") and (current is None or current.get("status") != POLICY_STATUS_ACTIVE):
        blocking.append(f"授权 {aid} 无生效修订, 不能 {action}(先 grant)")
    revised = _parse_iso(block.get("revised_at"))
    frm, exp = _parse_iso(block.get("active_from")), _parse_iso(block.get("expires_at"))
    if revised is None or frm is None or exp is None:
        blocking.append("project_authorization 须带可解析的 revised_at / active_from / expires_at(ISO8601)")
    elif exp <= frm:
        blocking.append("project_authorization.expires_at 须晚于 active_from")
    try:
        max_tasks = int(block.get("max_tasks"))
    except (TypeError, ValueError):
        max_tasks = 0
    if max_tasks <= 0 or isinstance(block.get("max_tasks"), bool):
        blocking.append("project_authorization.max_tasks 须为正整数(总卡数硬上限必填)")
    lanes = _normalize_lanes(block.get("lanes"))
    if lanes is None:
        blocking.append("project_authorization.lanes 须为非空 [{repo, task_modes(非空), drivers(非空)}]")
        lanes = []
    modes = set(get_enum_values("task_mode"))
    for lane in lanes:
        try:
            resolve_card_repo(root, {"task_id": f"authorization:{aid}", "lane": {"repo": lane["repo"]}})
        except (CardRepoUnresolved, OSError, ValueError) as exc:
            blocking.append(f"车道仓 {lane['repo']!r} 不可解析: {exc}")
        bad = [m for m in lane["task_modes"] if m not in modes]
        if bad:
            blocking.append(f"车道 {lane['repo']} 任务类型 {bad} 不在 task_mode 值域 {sorted(modes)}")
        bad_drivers = [d for d in lane["drivers"] if not _AUTH_DRIVER_RE.fullmatch(d)]
        if bad_drivers:
            blocking.append(f"车道 {lane['repo']} 驱动方 {bad_drivers} 形不合")
    hosts = _str_list(block.get("hosts"))
    if not hosts or any(not _AUTH_HOST_RE.fullmatch(h) for h in hosts):
        blocking.append(f"project_authorization.hosts 须为非空主机标签列表([A-Za-z0-9._-]): {block.get('hosts')!r}")
        hosts = hosts or []
    launch = _str_list(block.get("launch_harnesses"))
    launchable = launchable_harnesses()
    if launch is None or any(h not in launchable for h in launch):
        blocking.append(f"project_authorization.launch_harnesses 须 ⊆ 有 launch 模板的 harness {sorted(launchable)}: {block.get('launch_harnesses')!r}")
        launch = [h for h in (launch or []) if h in launchable]
    zones = _str_list(block.get("zones_open"))
    if zones is None or any(z not in decl["zones"] for z in zones):
        blocking.append(f"project_authorization.zones_open 须 ⊆ 声明禁区 {list(decl['zones'])}: {block.get('zones_open')!r}")
        zones = [z for z in (zones or []) if z in decl["zones"]]
    supersedes = _str_list(block.get("supersedes_policies"))
    if supersedes is None:
        blocking.append("project_authorization.supersedes_policies 须为信封 policy_id 列表")
        supersedes = []
    missing = [pid for pid in supersedes if load_policy(root, pid) is None or is_authorization(load_policy(root, pid))]
    if missing:
        blocking.append(f"迁入的旧信封 {missing} 不存在或不是信封")
    return {
        "authorization_id": aid, "revision": latest + 1, "project": project,
        "status": "revoked" if action == "revoke" else POLICY_STATUS_ACTIVE, "approved_by_owner": True,
        "owner_approval_ref": "", "action": action, "revised_at": str(block.get("revised_at") or ""),
        "active_from": str(block.get("active_from") or ""), "expires_at": str(block.get("expires_at") or ""), "max_tasks": max_tasks,
        "lanes": lanes, "hosts": hosts, "launch_harnesses": launch, "zones_open": zones, "supersedes_policies": supersedes,
        "previous_revision": latest or None, "notes": str(block.get("notes") or "").strip(),
    }


def build_authorization_markdown(record: dict[str, Any]) -> str:
    """授权修订工件唯一渲染(只经 owner_decision_record 写路径落盘 = Owner 一次手确认; 红线 1: 预授权不是委托)。"""
    decl = project_authorization_declaration()
    metadata = {"record_type": decl["record_type"], **{k: record.get(k) for k in AUTHORIZATION_FRONTMATTER_ORDER if k != "record_type"}}
    zones_open = ", ".join(record.get("zones_open") or []) or "none (all forbidden zones closed)"
    body = "\n".join([
        f"# Project Authorization: {record['authorization_id']} (r{record['revision']})", "",
        "## Scope", "",
        f"- Project: `{record['project']}`; action: {record['action']}; status: {record['status']}.",
        *[f"- Lane `{l['repo']}`: task modes {', '.join(l['task_modes'])}; drivers {', '.join(l['drivers'])}." for l in record.get("lanes") or []],
        f"- Hosts: {', '.join(record.get('hosts') or []) or 'none'}.",
        f"- Harness launch: {', '.join(record.get('launch_harnesses') or []) or 'none (manual /go only)'}.",
        f"- Open forbidden zones: {zones_open}.",
        f"- Bounds: until `{record['expires_at']}` (from `{record['active_from']}`) and at most {record['max_tasks']} business cards in total.",
        f"- Supersedes envelopes: {', '.join(record.get('supersedes_policies') or []) or 'none'}.",
        f"- Owner approval: `{record['owner_approval_ref']}`.", "",
        "## Boundary", "",
        render_envelope_boundary(record.get("launch_harnesses")), "",
    ])
    return render_markdown(metadata, body, AUTHORIZATION_FRONTMATTER_ORDER)


def client_tag(project: str) -> str:
    """项目名 → owner_decision_record client_tag(TAG_PATTERN 形; 唯一实现, owner_inbox 决策 payload 与项目授权决策同读)。"""
    tag = re.sub(r"[^a-z0-9_]", "_", str(project or "").lower())
    return tag if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", tag) else f"p_{tag}"[:64]


def _synthesize_authorization_decision(payload: dict[str, Any], record: dict[str, Any], relpath: str, actor: str | None) -> dict[str, Any]:
    """CLI / 看板签发(payload 只带授权块)= 合成完整 owner_decision_record payload(AIPOS-110 形, 交既有 writer 校验与渲染): 证据 = 门内
    owner_confirm(capture_method harness_owner_confirm), evidence_hash = 授权修订内容 sha256(如实: 哈希的就是 Owner 确认的那份授权)。
    全部取值由块定死(无 now()), 预演与确认同一快照。"""
    import hashlib
    from datetime import timedelta

    from tools.aipos_cli.clock import iso_z

    aid, rev, action = record["authorization_id"], record["revision"], record["action"]
    project = record["project"]
    tag = client_tag(project)
    decided_by = str(payload.get("decided_by_ref") or actor or "owner").strip() or "owner"
    captured_by = str(payload.get("captured_by") or decided_by).strip() or decided_by
    external_ref = f"project_authorization:{aid}@r{rev}"
    revised = _parse_iso(record.get("revised_at")) or utc_now()
    digest = hashlib.sha256(json.dumps({k: v for k, v in record.items() if k != "owner_approval_ref"}, ensure_ascii=False,
                                       sort_keys=True).encode("utf-8")).hexdigest()
    surface = str(payload.get("capture_surface") or "cli").strip() or "cli"
    return {
        "decision_id": str(payload.get("decision_id") or "").strip() or f"grant-{aid}-r{rev:04d}",
        "decision_type": f"project_authorization_{action}", "decision_status": "approved", "decided_at": record["revised_at"],
        "decided_by_ref": decided_by, "captured_by": captured_by, "capture_surface": surface,
        "decision_summary": (str(payload.get("decision_summary") or "").strip()
                             or f"{action} project grant {aid} r{rev}: {len(record['lanes'])} lane(s), hosts {record['hosts']}, "
                                f"max {record['max_tasks']} business cards, until {record['expires_at']}")[:320],
        "decision_rationale": str(payload.get("decision_rationale") or "").strip() or None,
        "applies_to": {"project": project if project == tag else None, "task_id": str(payload.get("task_id") or "").strip() or None,
                       "external_ref": external_ref},
        "approval_scope": {"operation": "owner_decision_record", "authority_boundary": f"{action} project grant {aid} r{rev}"[:160],
                           "allowed_next_action": "preauthorized_claim_autorelease", "expires_at": record["expires_at"] or None},
        "owner_approval_evidence": {
            "evidence_id": f"inband-owner-confirm:{aid}@r{rev}", "source_tag": "owner_console", "client_tag": tag,
            "external_ref": external_ref, "approval_actor_ref": decided_by, "approval_timestamp": record["revised_at"],
            "approval_intent": f"{action}_project_authorization", "evidence_hash": digest, "evidence_ref": "harness:owner_confirm",
            "captured_by": captured_by, "capture_method": "harness_owner_confirm", "redaction_status": "not_applicable", "refs": [relpath],
        },
        "refs": [relpath],
        "capability_scope": {"token_ref": "owner_console", "operations": ["owner_decision_record"], "projects": [project],
                             "expires_at": iso_z(revised + timedelta(days=1))},
    }


def build_authorization_decision(
    governance_root: Path | str,
    payload: dict[str, Any],
    *,
    actor: str | None,
    dry_run: bool,
    backend: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """AIPOS-F224: owner_decision_record 带 project_authorization 块的唯一构造(board_adapter.record_owner_decision 与门确认重放同调;
    决策记录本身仍由既有 writer backend(owner_decision_writer.build_owner_decision_record)校验与渲染, 本函数只加授权修订一份写):
    - payload 只带授权块(CLI / 看板签发)= 合成完整决策 payload(_synthesize_authorization_decision); 带完整决策字段(收件箱「扩大授权」裁决)= 原样;
    - 授权块校验(_validate_authorization_block)拒因并入 blocking_reasons; planned_writes = 决策记录 + 授权修订 r<N>(文件已在 = BLOCK);
    - original_payload 回带原始授权块(确认重放从它重建, 快照一致); autonomy_policy_grant = True(门确认要 Owner-only owner_confirm, 与签信封同);
    - 确认(dry_run=False)= 先由 backend 写决策记录, 再独占创建授权修订(已存在 = 不覆盖, 拒)。"""
    from tools.schema_constants import Verdict

    root = Path(governance_root)
    block = payload.get("project_authorization")
    blocking: list[str] = []
    record = _validate_authorization_block(root, block, blocking)
    relpath = ""
    if record is not None:
        try:
            relpath = authorization_revision_relpath(root, record["authorization_id"], record["revision"])
        except ValueError as exc:
            blocking.append(str(exc))
        if relpath and (root / relpath).exists():
            blocking.append(f"Project grant revision already exists: {relpath}")
    base_payload = {k: v for k, v in payload.items() if k != "project_authorization"}
    if record is not None and not isinstance(base_payload.get("owner_approval_evidence"), dict):
        base_payload = _synthesize_authorization_decision(base_payload, record, relpath, actor)
    result = dict(backend(root, base_payload, actor=actor, dry_run=True))
    if record is not None:
        record["owner_approval_ref"] = str(result.get("decision_id") or base_payload.get("decision_id") or "")
    markdown = ""
    if record is not None and relpath and not blocking:
        try:
            markdown = build_authorization_markdown(record)
        except (ValueError, TypeError) as exc:  # 渲染回读不一致 = 不落盘(原文进拒因)
            blocking.append(f"{type(exc).__name__}: {exc}")
    reasons = list(result.get("blocking_reasons") or []) + blocking
    original = dict(result.get("original_payload") or {})
    original["project_authorization"] = block
    planned = list(result.get("planned_writes") or [])
    if relpath:
        planned.append({"path": relpath, "kind": "create", "type": "record_markdown",
                        "record_type": str(project_authorization_declaration()["record_type"])})
    verdict = Verdict.BLOCK if reasons else (Verdict.WARN if result.get("warnings") else Verdict.PASS)
    result.update({"blocking_reasons": reasons, "verdict": verdict, "planned_writes": planned, "original_payload": original,
                   "would_write": verdict != Verdict.BLOCK and bool(result.get("target_path")),
                   "autonomy_policy_grant": True, "autonomy_policy_id": (record or {}).get("authorization_id"),
                   "autonomy_policy_path": relpath or None, "project_authorization_id": (record or {}).get("authorization_id"),
                   "authorization_revision": (record or {}).get("revision"), "authorization_path": relpath or None,
                   "authorization_record": record, "authorization_markdown": markdown})
    if dry_run:
        return result
    if verdict == Verdict.BLOCK or record is None or not markdown:
        result["wrote"] = False
        return result
    written = backend(root, base_payload, actor=actor, dry_run=False)
    if not written.get("wrote"):
        result.update({"wrote": False, "blocking_reasons": reasons + list(written.get("blocking_reasons") or ["决策记录未写入"]),
                       "verdict": Verdict.BLOCK})
        return result
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as handle:  # 只追加修订: 已存在即 FileExistsError(不覆盖任何修订)
        handle.write(markdown)
    result["wrote"] = True
    return result


def authorization_extension(governance_root: Path | str, refusal: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    """越界判定 → 一次扩大授权建议(唯一推导; 声明 project_authorization.extend): {block(extend 授权块), change(人话增量), flags(CLI 增量参数),
    revision}; 撤销 / 未生效 / 项目不符 / 卡面事实缺(主机读不出等)= None(须 Owner 重新签发)。loop 越界出口 / 收件箱建议 / 看板按建议签发同读。"""
    import math
    from datetime import timedelta

    from tools.aipos_cli.clock import iso_z

    decl = project_authorization_declaration()
    dim = refusal.get("dimension")
    auth, facts = refusal.get("auth"), refusal.get("facts") or {}
    if not isinstance(auth, dict) or dim in _AUTH_NO_EXTEND or dim not in decl["dimensions"]:
        return None
    now = now or utc_now()
    lanes: list[dict[str, Any]] = []
    kw: dict[str, Any] = {}
    flags: list[str] = []
    mode, driver = str(facts.get("task_mode") or ""), str(facts.get("driver") or "")
    if dim in ("lane", "task_mode", "driver"):
        repos = [facts.get("lane_name")] if dim == "lane" else list(facts.get("mode_hit" if dim == "driver" else "lanes_hit") or [])
        repos = [r for r in repos if r]
        if not repos or not mode or not driver:
            return None
        lanes = [{"repo": r, "task_modes": [mode], "drivers": [driver]} for r in repos]
        flags += [f"--grant-lane {r}" for r in repos] + [f"--task-mode {mode}", f"--driver {driver}"]
        change = f"车道 {', '.join(repos)} 加任务类型 {mode} / 驱动方 {driver}"
    elif dim == "host":
        if not facts.get("host"):
            return None
        kw["hosts"] = [facts["host"]]
        flags.append(f"--host {facts['host']}")
        change = f"允许主机加 {facts['host']}"
    elif dim == "zone":
        closed = [z for z in facts.get("zones_closed") or [] if z in decl["zones"]]
        if not closed:
            return None
        kw["zones_open"] = closed
        flags += [f"--open-zone {z}" for z in closed]
        change = "放开禁区 " + ", ".join(f"{z}({decl['zones'][z]})" for z in closed)
    elif dim == "quota":
        step = max(1, math.ceil(float(decl["extend"]["quota_step_ratio"]) * int(auth.get("max_tasks") or 0)))
        kw["max_tasks"] = int(auth.get("max_tasks") or 0) + step
        flags.append(f"--max-tasks {kw['max_tasks']}")
        change = f"总卡数 {auth.get('max_tasks')} → {kw['max_tasks']}(已放行 {refusal.get('used')} 张业务卡)"
    else:  # expiry
        kw["expires_at"] = iso_z((now + timedelta(days=int(decl["extend"]["expiry_days"]))).replace(microsecond=0))
        flags.append(f"--expires-at {kw['expires_at']}")
        change = f"到期 {auth.get('expires_at')} → {kw['expires_at']}"
    block = authorization_block(governance_root, action="extend", now=now, lanes=lanes, authorization_id=str(auth["authorization_id"]),
                                notes=f"extend for {facts.get('task_id') or '?'}: {dim}", **kw)
    return {"block": block, "change": change, "flags": flags, "revision": int(block["base_revision"]) + 1, "dimension": dim}


def render_authorization_command(governance_root: Path | str, subcommand: str, flags: list[str]) -> str:
    """`lybra project grant <sign|extend|revoke|draft> … --confirm` 命令唯一渲染(Owner 亲自敲; --connection-json 指向持 Owner 凭据的
    connection.json, 命令不读不显示凭据)。loop 越界出口 / 收件箱 / 看板凭据缺时同读。"""
    import shlex

    return " ".join(["lybra project grant", subcommand, f"--workspace-root {shlex.quote(str(governance_root))}", *flags,
                     "--confirm --connection-json <Owner 凭据 connection.json>"])


def authorization_refusal_message(governance_root: Path | str, refusal: dict[str, Any], *, what: str,
                                  now: datetime | None = None) -> dict[str, Any]:
    """越界出口文案与扩大授权命令(唯一渲染; 声明 messages.out_of_bound / extend / no_extend): {message, command, extension, state}。
    state = 运行记录 envelope_state(授权用量 / 上限 / 到期 + 越界维度 + 扩大命令)。"""
    decl = project_authorization_declaration()
    msgs = decl["messages"]
    head = msgs["out_of_bound"].format(authorization_id=refusal.get("authorization_id"), revision=refusal.get("revision"),
                                       dimension_label=refusal.get("label") or refusal.get("dimension"), reason=refusal.get("reason"), what=what)
    extension = authorization_extension(governance_root, refusal, now=now)
    if extension is not None:
        command = render_authorization_command(governance_root, "extend", extension["flags"])
        tail = msgs["extend"].format(command=command) + f"(增量: {extension['change']})"
    else:
        command = render_authorization_command(governance_root, "sign", ["--grant-lane <仓> --task-mode <类型> --driver <驱动方>", "--host <主机>",
                                                                         "--max-tasks <总卡数> --expires-at <到期>"])
        tail = msgs["no_extend"].format(dimension_label=refusal.get("label") or refusal.get("dimension"), command=command)
    auth = refusal.get("auth") or {}
    state = {"policy_id": refusal.get("authorization_id"), "state": refusal.get("dimension"), "used": refusal.get("used"),
             "max_tasks": auth.get("max_tasks"), "remaining": max(0, int(auth.get("max_tasks") or 0) - int(refusal.get("used") or 0)),
             "expires_at": auth.get("expires_at"), "renew_command": command, "next_policy_id": refusal.get("authorization_id"),
             "note": f"越界维度 {refusal.get('label')}", "quota_line": (extension or {}).get("change") or ""}
    return {"message": f"{head}\n{tail}", "command": command, "extension": extension, "state": state}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
