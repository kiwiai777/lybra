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
    """唯一开关读口: CLI 已决定 = 用之; 否则(非 CLI 进程)= 调试环境变量开 或 声明 non_cli_default_enabled。"""
    decided = _TRACE_SWITCH["enabled"]
    if decided is not None:
        return decided
    decl = envelope_trace_declaration()
    return _debug_env_on(decl) or bool(decl["non_cli_default_enabled"])


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
        return None
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
    return {root: list(tids) for root, tids in (roots["roots"].get(pid) or {}).items()}


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
        return {"used": len(cards), "card_released": bool(card_root) and card_root in cards, "root": card_root}


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
    candidates = [str(policy_id).strip()] if policy_id else policy_ids(root)
    if not candidates:
        pdir = policies_dir(root)
        try:
            shown = f"{pdir.resolve().relative_to(root.resolve()).as_posix()}/"
        except ValueError:
            shown = f"{pdir}/"
        return None, [f"{shown} 下没有任何信封"]
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    loaded: list[tuple[str, dict[str, Any], int]] = []
    reasons: list[str] = []
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
                last_reason[pid] = f"已被同驱动方更新信封 {successors[pid]} 接替(看板信封页 / 收件箱同一判定), 不再挑中、不再消耗"
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
    for row in rows:
        row["superseded_by"] = successors[row["policy_id"]]
    out: list[dict[str, Any]] = []
    for row in rows:
        usage, policy, state = row["usage"], row["policy"], row["state"]
        why = envelope_warning(usage) if state in ("active", "exhausted", "expired") else None
        if state in ("active", "not_yet_active") and row["superseded_by"]:
            status = "superseded"
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


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
