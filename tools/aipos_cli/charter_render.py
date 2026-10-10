"""AIPOS-F66B 件①: 章程 = 声明渲染物(母本 + 项目声明 → 工位副本)。**唯一渲染实现**, sync(pull)与 distribute_tools(push)同用。

定案(卡面 2026-09-16 增补): seed_only 退役——
  - 渲染物 = 母本正文(`{{key}}` 占位按渲染上下文替换)+「项目声明」尾节(project.json paths/repos、门地址、工位/实例)
    +「写权限边界」节(roles.schema write_boundary 该角色行 + hard_rules「拒后禁换方式重试」, 件②);
  - 更新语义 = 母本变 / 声明变 则重渲染(manifest 记 source_sha256 / render_context_sha256 / rendered_sha256 三指纹);
  - 工位本地改动 = 声明缺口: 覆盖前报 unified diff(须回流母本或声明), 不保留第二份真相。

工位归属(件① 硬前置): workstation_identity() 读工位 `.lybra/role`(role/instance), 项目段经 naming_profile.parse_instance_name
(注册表模板唯一解析); 任何分发条目的目标工位与 sync 项目范围不符 = 跳过 + manifest 记 skipped(禁跨项目覆盖, 2026-09-05 chris hbj-coder 实撞)。

渲染上下文全部读声明: project.json(project / paths / repos, 经 workspace_config 唯一读取口)、config.schema 门地址缺省、
工位 connection.json(governance_root / mcp.rpc_url; token 永不进上下文)。母本含未声明占位 = ValueError(fail-closed)。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

CHARTER_RENDER_MARKER = "<!-- lybra:charter-render"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
# AIPOS-F146 件①: 母本按执行者接入模式分段(声明式最小分段; 母本仍只一份)。段标记独占一行:
#   <!-- lybra:executor-mode <模式> -->  … 该模式的正文行 …  <!-- lybra:executor-mode <另一模式> -->  …  <!-- lybra:executor-mode end -->
# 模式名 = roles.schema executor_modes.values 的键(唯一读取口 enrollment.executor_mode_declaration); 一组须恰覆盖全部声明模式
# (新增模式而母本未补段 = 渲染拒, fail-closed)。渲染只留 ctx["executor_mode"] 那段, 标记行不进渲染物; 母本无段标记 = 原样(行为不变)。
EXECUTOR_MODE_SEGMENT_MARKER = "<!-- lybra:executor-mode"
_MODE_SEGMENT_RE = re.compile(r"^<!-- lybra:executor-mode (?P<mode>[A-Za-z0-9_-]+) -->[ \t]*$")
_MODE_SEGMENT_END = "end"


class WorkstationIdentityError(ValueError):
    """工位身份不可解析(缺 .lybra/role / 实例名不合模板), fail-closed。"""


class WorkstationInstanceNotRegistered(WorkstationIdentityError):
    """AIPOS-F140 件①: 给定实例在该工位 / 治理根 .lybra/role 既非顶层也无其槽(读口 enroll_client.read_role_record 判)。
    携 top_instance / registered / slotted(无槽工位的拒因原文保持修前形, 有槽治理根附已登记实例清单)。"""

    def __init__(self, message: str, *, instance: str, top_instance: str, registered: list[str], slotted: bool):
        super().__init__(message)
        self.instance = instance
        self.top_instance = top_instance
        self.registered = registered
        self.slotted = slotted


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 工位身份 / 项目归属
# ---------------------------------------------------------------------------

# AIPOS-F106 件④(M2): 工位身份文件名(config.schema configuration_sources.role.source_file = .lybra/role)。
# .lybra 目录一律经 loop_context.ConnectionResolver.discover_lybra_dir 定位; 文件内容(含 instances.<实例> 分槽)只经
# enroll_client.read_role_record 读(AIPOS-F140 件①, 与写入器同处的唯一分槽读口; 另一读取口 ConnectionResolver.resolve_role /
# resolve_identity 在车道外, 只出顶层身份键), 其余代码禁直接读该文件。
WORKSTATION_ROLE_FILE = "role"


def workstation_role_file(harness_root: str | Path) -> Path | None:
    """AIPOS-F106 件④: 工位 .lybra/role 的唯一定位(不读内容)。无 .lybra 目录 = None。"""
    from tools.loop_context import ConnectionResolver

    lybra_dir = ConnectionResolver.discover_lybra_dir(Path(harness_root).expanduser())
    return lybra_dir / WORKSTATION_ROLE_FILE if lybra_dir is not None else None


def is_enrolled_workstation(harness_root: str | Path) -> bool:
    """AIPOS-F106 件④: 已 enroll 工位判据 = 有 .lybra/role 文件(只看存在, 内容合法性由 workstation_identity fail-closed 判)。"""
    role_file = workstation_role_file(harness_root)
    return role_file is not None and role_file.is_file()


def workstation_identity(harness_root: str | Path, instance: str | None = None) -> dict[str, Any]:
    """读工位 .lybra/role(+ connection.json 非秘密字段)→ {harness_root, role, instance, project, owner_policy_ref, harness,
    governance_root_declared, token_projects, gate_url}。token 值永不读入。

    AIPOS-F140 件①: 记录经唯一分槽读口 enroll_client.read_role_record 取——给 instance = 该实例的记录(治理根多顾问实例各取
    instances.<实例> 槽: role / harness / owner_policy_ref 都取槽内), 未登记 = WorkstationInstanceNotRegistered(列出已登记实例);
    未给 = 顶层(现行; 工位无 instances 键, 行为不变)。

    AIPOS-F106: .lybra 经 ConnectionResolver.discover_lybra_dir 定位; gate_url = 门基址, 经 confirm_client.resolve_gate_base_url
    (唯一推导口, 委托 ConnectionResolver.resolve_gate_url) 读本工位 connection.json 的 mcp.rpc_url, 未声明 = None;
    harness = role 文件的 harness 原值(工位 harness 声明, distribution_sync.workstation_harness 消费)。"""
    root = Path(harness_root).expanduser().resolve()
    role_file = workstation_role_file(root)
    if role_file is None or not role_file.is_file():
        raise WorkstationIdentityError(f"{root}: 无 .lybra/role — 不是已 enroll 工位, 拒绝分发/渲染")
    from tools.aipos_cli.enroll_client import RoleInstanceNotRegistered, RoleRecordError, read_role_record

    try:
        data = read_role_record(role_file.parent, instance) or {}
    except RoleInstanceNotRegistered as exc:
        raise WorkstationInstanceNotRegistered(str(exc), instance=exc.instance, top_instance=exc.top_instance,
                                               registered=exc.registered, slotted=exc.slotted) from exc
    except RoleRecordError as exc:
        raise WorkstationIdentityError(str(exc)) from exc
    role = str(data.get("role") or "").strip()
    instance = str(data.get("instance") or "").strip()
    if not role or not instance:
        raise WorkstationIdentityError(f"{role_file} 缺 role/instance(工位归属按实例名项目段判, 禁猜)")
    from tools.aipos_cli.naming_profile import parse_instance_name

    parsed = parse_instance_name(instance)
    if parsed is None or not parsed.get("project"):
        raise WorkstationIdentityError(
            f"{role_file} instance={instance!r} 不合注册表模板(roles.schema naming.template), 无法判项目归属"
        )
    identity: dict[str, Any] = {
        "harness_root": str(root),
        "role": role,
        "instance": instance,
        "project": parsed["project"],
        "owner_policy_ref": str(data.get("owner_policy_ref") or "") or None,
        "harness": data.get("harness"),
        "governance_root_declared": None,
        "token_projects": [],
        "gate_url": None,
    }
    identity.update(_connection_identity_fields(role_file.parent / "connection.json", instance))
    return identity


def _connection_identity_fields(conn_file: Path, instance: str) -> dict[str, Any]:
    """身份的连接文件非秘密字段 {governance_root_declared, gate_url, token_projects}(token 值永不读入)。
    AIPOS-F146: 自 workstation_identity 抽出, 工位身份(工位 .lybra/connection.json)与子 agent 执行者身份(治理根 .lybra/connection.json,
    subagent_identity)同读此处, 禁第二份连接文件读法。文件不在 = 缺省值; 不可读 / 非 JSON = WorkstationIdentityError。"""
    out: dict[str, Any] = {"governance_root_declared": None, "gate_url": None, "token_projects": []}
    if conn_file.is_file():
        try:
            conn = json.loads(conn_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkstationIdentityError(f"{conn_file} 不可读/非 JSON: {exc}") from exc
        if isinstance(conn, dict):
            from tools.aipos_cli.confirm_client import declared_rpc_url, resolve_gate_base_url

            gov = str(conn.get("governance_root") or "").strip()
            out["governance_root_declared"] = gov or None
            if declared_rpc_url(conn_file):
                out["gate_url"] = resolve_gate_base_url(connection_json=conn_file, require_declared=True)
            # AIPOS-F81: 条目挑选委托 token_resolver 单源(按实例, 排除 retired), 只取非秘密字段 projects。
            # 无可用条目(无命中/全 retired)→ [](元数据缺省); 凭据的 fail-closed 在 token 解析处(带重签出口)。
            from tools.aipos_cli.token_resolver import TOKEN_ENTRY_FIELDS, TokenResolutionError, select_token_entry

            projects: list[str] = []
            tokens = conn.get("tokens")
            if isinstance(tokens, list):
                try:
                    entry = select_token_entry(tokens, agent_instance=instance, source=str(conn_file))
                    projects = [str(p) for p in (entry.get(TOKEN_ENTRY_FIELDS["projects"]) or [])]
                except TokenResolutionError:
                    projects = []
            out["token_projects"] = projects
    return out


def subagent_identity(governance_root: str | Path, enrollment_view: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F146 件①: 无工位的已接入执行者(子 agent 模式, enrollment_view.governance_root_mode)的身份 —— 与 workstation_identity 同形。

    身份源只有一处 = enrollment.instance_enrollment 的结果(本函数不再读接入日志): role = 最新 land 事件行的 role=(门签码时记下;
    子 agent 不写 .lybra/role, roles.schema executor_modes 声明), harness = land 事件 harness=(顾问会话 kind)、host = land 事件 host=;
    harness_root = 治理根(凭据所在 .lybra/connection.json 所在根; 写权限边界对执行者恒为只读面); 连接文件非秘密字段同
    _connection_identity_fields。实例名不合注册表模板 / land 事件缺 role = WorkstationIdentityError(fail-closed)。"""
    from tools.aipos_cli.naming_profile import parse_instance_name

    gov = Path(governance_root).expanduser().resolve()
    instance = str(enrollment_view.get("instance") or "").strip()
    role = str(enrollment_view.get("role") or "").strip()
    if not enrollment_view.get("governance_root_mode"):
        raise WorkstationIdentityError(f"实例 {instance!r} 的接入模式 {enrollment_view.get('mode')!r} 不是无工位模式(子 agent), 身份须读工位 .lybra/role")
    if not role:
        raise WorkstationIdentityError(f"实例 {instance!r} 的最新 land 事件缺 role=(接入日志行形坏), 身份不可判")
    parsed = parse_instance_name(instance)
    if parsed is None or not parsed.get("project"):
        raise WorkstationIdentityError(f"实例 {instance!r} 不合注册表模板(roles.schema naming.template), 无法判项目归属")
    from tools.aipos_cli.service_mode import connection_path

    identity: dict[str, Any] = {
        "harness_root": str(gov),
        "role": role,
        "instance": instance,
        "project": parsed["project"],
        "owner_policy_ref": None,  # 子 agent 不推导信封(认领由驱动方信封放行, 执行体零门)
        "harness": {"kind": enrollment_view.get("harness"), "dir": None, "host": enrollment_view.get("host")},
        "executor_mode": enrollment_view.get("mode"),
    }
    identity.update(_connection_identity_fields(connection_path(gov), instance))
    return identity

def resolve_workstation_governance_root(identity: dict[str, Any], *, explicit: str | Path | None = None) -> Path:
    """工位的治理根(渲染上下文来源): 显式 → connection.json#governance_root → home_root/<project>(F66 单一解析)。
    解析不到 = FileNotFoundError(fail-closed, 带出口)。"""
    from tools.aipos_cli.workspace_config import resolve_home_root, resolve_project_root

    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not (root / "project.json").is_file():
            raise FileNotFoundError(f"--workspace-root {root} 无 project.json(治理根须已建项目)")
        return root
    declared = identity.get("governance_root_declared")
    if declared:
        root = Path(declared).expanduser().resolve()
        if (root / "project.json").is_file():
            return root
        raise FileNotFoundError(f"工位 connection.json#governance_root={root} 无 project.json(声明指向非项目根)")
    try:
        return resolve_project_root(resolve_home_root(), str(identity["project"]))
    except (FileNotFoundError, ValueError) as exc:
        raise FileNotFoundError(
            f"工位 {identity.get('instance')} 的治理根不可解析(connection.json 无 governance_root, "
            f"home_root/{identity.get('project')} 亦不成立: {exc}); 出口: lybra sync --workspace-root <治理根>"
        ) from exc


# ---------------------------------------------------------------------------
# 渲染上下文(全读声明)
# ---------------------------------------------------------------------------

def charter_render_context(
    governance_root: str | Path,
    *,
    identity: dict[str, Any],
    product_commit: str = "unknown",
) -> dict[str, Any]:
    """渲染上下文: 项目声明(project.json paths/repos)+ 门地址 + 工位/实例(+ 机器段 / 兄弟角色实例名, F80)+ 角色类。token 永不入。

    **本字典 = 章程母本 `{{key}}` 占位的唯一键表**(AIPOS-F80 件②): 母本只准用这里的标量键, 不够在此处加, 禁第二份键表。
    """
    from tools.aipos_cli.custom_roles import resolve_role_to_class
    from tools.aipos_cli.hard_rules_extractor import hard_rules_source_ref
    from tools.aipos_cli.workspace_config import project_paths, project_repos, read_project_json
    from tools.schema_loader import get_config_default_gate_url

    gov = Path(governance_root).expanduser().resolve()
    project_json = read_project_json(gov)
    project = str(project_json.get("project") or "").strip()
    if not project:
        raise ValueError(f"{gov}/project.json 缺 project 字段(章程渲染需项目声明, 禁默认)")
    if project != identity.get("project"):
        raise ValueError(
            f"工位实例 {identity.get('instance')} 的项目段 {identity.get('project')!r} ≠ 治理根 {gov} 的 project={project!r}"
            f"(分发按工位项目归属过滤, 禁跨项目渲染)"
        )
    paths = project_paths(gov)
    repos = project_repos(gov)
    # AIPOS-F80 件②: 母本占位化所需的机器段/兄弟实例名(本声明处是唯一键表)——机器段取工位实例名(注册表模板唯一解析),
    # 兄弟角色实例名经注册表模板唯一实现 default_instance_name(前缀读 naming_profile prefix_mapping, 项目可覆写); 缺 = 拒。
    from tools.aipos_cli.naming_profile import default_instance_name, get_naming_profile, parse_instance_name

    parsed = parse_instance_name(str(identity["instance"])) or {}
    machine = str(parsed.get("host") or "").strip()
    if not machine:
        raise ValueError(f"工位实例 {identity.get('instance')!r} 无机器段(roles.schema naming.template), 章程渲染拒")
    prefixes = get_naming_profile(gov).get("prefix_mapping") or {}
    sibling: dict[str, str] = {}
    from tools.aipos_cli.custom_roles import role_classes_in_group

    # AIPOS-F102 件②: 兄弟实例 = 工位类角色(roles.schema class_groups.workstation, 注册表顺序), 原写死的工位类分组元组退役
    for role_key in role_classes_in_group("workstation"):
        prefix = str(prefixes.get(role_key) or "").strip()
        if not prefix:
            raise ValueError(f"naming_profile prefix_mapping 缺 {role_key}(roles.schema naming.prefix), 章程渲染拒")
        sibling[f"{role_key}_instance"] = default_instance_name(prefix, project=str(parsed["project"]), host=machine)
    role = str(identity["role"])
    role_class = resolve_role_to_class(role, gov, required=True)  # AIPOS-F102 件②: 解析不到 = 拒(原「回落角色名」退役)
    code_repo = repos["items"].get(repos["default"]) if repos["declared"] else repos["code_repo"]
    ctx: dict[str, Any] = {
        "project": project,
        "governance_root": str(gov),
        "code_repo": str(code_repo) if code_repo else "<unresolved: project.json 无 code_repo/repos>",
        "repos": {name: str(path) for name, path in repos["items"].items()} if repos["declared"] else {},
        "return_root": str(paths["return_root"]),
        "verdict_root": str(paths["verdict_root"]),
        "queue_root": str(paths["queue_root"]),
        "task_cards_root": str(paths["task_cards_root"]),
        # AIPOS-F89 件② M17: 硬规矩来源读项目声明(paths.hard_rules_source), 与提取器同一函数; 未声明 = 母本自带条文为准
        "hard_rules_source": hard_rules_source_ref(gov),
        "gate_url": str(identity.get("gate_url") or get_config_default_gate_url()),
        "harness_root": str(identity["harness_root"]),
        "harness_parent": str(Path(identity["harness_root"]).parent),
        "role": role,
        "role_class": role_class,
        "instance": str(identity["instance"]),
        "machine": machine,
        "executor_instance": sibling["executor_instance"],
        "auditor_instance": sibling["auditor_instance"],
        "product_commit": str(product_commit or "unknown"),
        # AIPOS-F146 件①: 执行者接入模式(母本分段选择键)。子 agent 身份(subagent_identity)带 land 事件 mode; 工位身份无此键 =
        # roles.schema executor_modes.default(工位, 行为不变)。
        "executor_mode": str(identity.get("executor_mode") or _default_executor_mode()),
    }
    # AIPOS-F93 件①: 章程报告节的报告必填字段 = 声明 transitions artifact_ingest 单源渲染(与落点句 / my-tasks / 认领模板同一函数)
    from tools.aipos_cli.next_resolver import render_report_frontmatter_clause, report_frontmatter_contract

    for kind in ("return", "verdict"):
        ctx[f"{kind}_required_frontmatter"] = render_report_frontmatter_clause(report_frontmatter_contract(kind))
    # AIPOS-F99 件②: 审计「独立全量基线」的测试清单位置 = 项目声明 test_contract.runall_path(唯一读取口
    # workspace_config.project_test_contract, 卡仓取缺省产品仓; 形坏 = ValueError 原样抛, fail-closed); 未声明 = 明确文字, 不留空占位。
    from tools.aipos_cli.workspace_config import project_test_contract

    test_contract = project_test_contract(gov, code_repo)
    runall = test_contract.get("runall_path")
    ctx["runall_path"] = (
        f"`{runall}`(相对产品仓根; 来源 {test_contract['source']}.runall_path)"
        if runall
        else "本项目未声明测试清单(project.json 无 test_contract.runall_path)——独立全量基线跳过, 报告注明「项目未声明 test_contract.runall_path」"
    )
    # AIPOS-F137 件③(gap #117/#101): 长测试写法——全量一次的命令式样与 bash 工具超时。超时 = 同一清单全量跑一次的既有时限声明
    # test_contract.post_merge_regression.timeout_seconds(finalize 合并后回归跑的正是同一清单全量; 同一读取口, 未声明取 config.schema
    # 缺省), 不另设键、不写死数值; 命令式样只拼声明的清单位置。
    pmr = test_contract["post_merge_regression"]
    ctx["runall_command"] = (
        f"`bash {runall} 2>&1 | tee <本工位临时目录>/runall-<tip|main>.log`"
        if runall
        else "(本项目未声明测试清单, 无全量可跑——见第 6 条)"
    )
    ctx["runall_timeout_seconds"] = (
        f"`{int(pmr['timeout_seconds'])}` 秒(来源 {pmr['source']} 的 timeout_seconds"
        "——同一清单全量跑一次的时限声明)"
    )
    return ctx


def render_context_fingerprint(ctx: dict[str, Any]) -> str:
    """声明指纹(不含 product_commit: 版本戳单独记 source_commit, 母本变由 source_sha256 判)。"""
    stable = {k: v for k, v in ctx.items() if k != "product_commit"}
    return _sha256_text(json.dumps(stable, ensure_ascii=False, sort_keys=True))


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _default_executor_mode() -> str:
    from tools.aipos_cli.enrollment import executor_mode_declaration

    return str(executor_mode_declaration()["default"])


def select_mode_segments(master_text: str, mode: str) -> str:
    """AIPOS-F146 件①: 母本执行者接入模式分段的唯一选择实现(render_charter 内调用; 段语法见 EXECUTOR_MODE_SEGMENT_MARKER 注释)。
    无段标记 = 原文返回(不读声明)。段名不在声明 / 同组重复 / 组未覆盖全部声明模式 / 未闭合 / 组外 end / mode 不在声明 = ValueError。"""
    if EXECUTOR_MODE_SEGMENT_MARKER not in master_text:
        return master_text
    from tools.aipos_cli.enrollment import executor_mode_declaration

    declared = list(executor_mode_declaration()["values"])
    if mode not in declared:
        raise ValueError(f"章程母本按执行者接入模式分段, 渲染上下文 executor_mode={mode!r} 不在 roles.schema executor_modes {declared}")
    out: list[str] = []
    seen: list[str] | None = None  # None = 组外
    current = ""
    for lineno, line in enumerate(master_text.splitlines(keepends=True), start=1):
        stripped = line.rstrip("\r\n")
        if stripped.lstrip().startswith(EXECUTOR_MODE_SEGMENT_MARKER):
            match = _MODE_SEGMENT_RE.match(stripped)
            if not match:
                raise ValueError(f"章程母本第 {lineno} 行段标记形坏(须独占一行: <!-- lybra:executor-mode <模式|end> -->): {stripped!r}")
            name = match.group("mode")
            if name == _MODE_SEGMENT_END:
                if seen is None:
                    raise ValueError(f"章程母本第 {lineno} 行 end 段标记不在任何分段组内")
                if sorted(seen) != sorted(declared):
                    raise ValueError(f"章程母本第 {lineno} 行结束的分段组覆盖模式 {seen} ≠ roles.schema executor_modes {declared}"
                                     "(每组须恰覆盖全部声明模式; 新增模式须先补母本段)")
                seen, current = None, ""
                continue
            if name not in declared:
                raise ValueError(f"章程母本第 {lineno} 行段模式 {name!r} 不在 roles.schema executor_modes {declared}")
            seen = [] if seen is None else seen
            if name in seen:
                raise ValueError(f"章程母本第 {lineno} 行分段组内模式 {name!r} 重复")
            seen.append(name)
            current = name
            continue
        if seen is None or current == mode:
            out.append(line)
    if seen is not None:
        raise ValueError(f"章程母本分段组未闭合(缺 <!-- lybra:executor-mode end -->), 已开模式 {seen}")
    return "".join(out)


def _substitute(master_text: str, ctx: dict[str, Any]) -> str:
    """母本 → 正文: 先按 ctx["executor_mode"] 选段(select_mode_segments, AIPOS-F146), 再替换 `{{key}}` 占位(未声明占位 = 拒)。"""
    master_text = select_mode_segments(master_text, str(ctx.get("executor_mode") or ""))
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        value = ctx.get(key)
        if value is None or isinstance(value, (dict, list)):
            missing.append(key)
            return match.group(0)
        return str(value)

    out = _PLACEHOLDER_RE.sub(repl, master_text)
    if missing:
        raise ValueError(
            f"章程母本含未声明占位 {sorted(set(missing))}(渲染上下文键: {sorted(k for k in ctx if not isinstance(ctx[k], (dict, list)))}); "
            "出口: 母本改用已声明键或补声明, 禁渲染半成品"
        )
    return out


def _harness_root_line(ctx: dict[str, Any]) -> str:
    """尾节「工位根」行。AIPOS-F146: 无工位接入模式(子 agent, roles.schema executor_modes credential_landing=governance_root)无工位根 /
    共享层, 如实写凭据所在(治理根 .lybra/, 门领地); 工位模式原文不变。"""
    from tools.aipos_cli.enrollment import governance_root_executor_modes

    mode = str(ctx.get("executor_mode") or "")
    if mode and mode in governance_root_executor_modes():
        return (f"- 无工位(接入模式 `{mode}`): 凭据与身份在治理根 `{ctx['governance_root']}/.lybra/`(门领地, 只读; 由驱动方使用, "
                "执行体不读不用)")
    return f"- 工位根: `{ctx['harness_root']}`(共享层 `{ctx['harness_parent']}/_shared` 与 `_distributed` 由分发器写, 角色只读)"


def render_charter(master_text: str, ctx: dict[str, Any]) -> str:
    """渲染物 = 母本(占位替换)+ 项目声明尾节 + 写权限边界节 + 渲染标记行(母本/声明指纹, 无时间戳: 同输入同输出)。"""
    from tools.aipos_cli.write_boundary import build_write_boundary, render_write_boundary_markdown

    body = _substitute(master_text, ctx).rstrip("\n")
    master_sha = _sha256_text(master_text)
    ctx_sha = render_context_fingerprint(ctx)
    repos_line = ", ".join(f"{k}=`{v}`" for k, v in sorted(ctx["repos"].items())) if ctx["repos"] else f"`{ctx['code_repo']}`"
    trailer = [
        "",
        "---",
        "",
        "## 项目声明(声明渲染, AIPOS-F66B 件①)",
        "",
        "> 本文件 = 章程母本 + 项目声明的渲染物, 由分发器写入; **母本变/声明变即重渲染**, 本地改动 = 声明缺口(会被覆盖并报 diff), 须回流母本或声明。",
        "",
        f"- 项目: `{ctx['project']}`(实例 `{ctx['instance']}`, 角色 `{ctx['role']}` / 类 `{ctx['role_class']}`)",
        f"- 治理根(只读真相): `{ctx['governance_root']}`",
        f"- 产品仓(project.json repos/code_repo): {repos_line}",
        f"- Return 落点: `{ctx['return_root']}/<卡ID>/`; 审计报告落点: `{ctx['verdict_root']}/<审计卡ID>/`; 队列(门领地): `{ctx['queue_root']}`",
        f"- 门: `{ctx['gate_url']}`(凭据只在工位 `.lybra/connection.json`, 只按名引用, 永不上屏)",
        _harness_root_line(ctx),
        "",
    ]
    boundary = build_write_boundary(ctx["governance_root"], role=ctx["role"], harness_root=ctx["harness_root"])
    wb_section = render_write_boundary_markdown(boundary, role=ctx["role"]).rstrip("\n")
    marker = f"{CHARTER_RENDER_MARKER} master_sha256={master_sha} context_sha256={ctx_sha} product_commit={ctx['product_commit']} -->"
    return body + "\n" + "\n".join(trailer) + "\n" + wb_section + "\n\n" + marker + "\n"


def charter_fingerprints(master_text: str, ctx: dict[str, Any], rendered: str) -> dict[str, str]:
    return {
        "source_sha256": _sha256_text(master_text),
        "render_context_sha256": render_context_fingerprint(ctx),
        "rendered_sha256": _sha256_text(rendered),
    }


def local_edit_diff(previous_rendered: str | None, current_local: str, target: Path, *, max_lines: int = 200) -> str:
    """工位本地改动 = 声明缺口: 与上次渲染物的 unified diff(截断), 供回流母本/声明。"""
    base = previous_rendered if previous_rendered is not None else ""
    lines = list(difflib.unified_diff(
        base.splitlines(keepends=True), current_local.splitlines(keepends=True),
        fromfile=f"{target.name} (上次渲染物)", tofile=f"{target.name} (工位本地)", n=2,
    ))
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)\n"]
    return "".join(lines)


# ---------------------------------------------------------------------------
# AIPOS-F138 件①: 拉取式取章程(`lybra charter`)—— 本机无落点的会话(他机 Codex 顾问)开局经 ssh 在治理根所在机读取。
# 渲染只走本模块 render_charter + charter_render_context(与 sync 落盘同一渲染器、同一上下文构造), 禁另写模板拼装。
# ---------------------------------------------------------------------------

CHARTER_VERB = "lybra_charter"


class CharterRefused(ValueError):
    """`lybra charter` 拒(实例未接入 / 工位在他机 / 身份或角色类不符 / 该角色该 harness 无章程), 消息带原因与出口。"""


def charter_verb_contract() -> dict[str, Any]:
    """verbs.schema verbs.lybra_charter(cli_command / parameters / exit_codes 唯一声明)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    contract = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(CHARTER_VERB)
    props = ((contract or {}).get("parameters") or {}).get("properties") if isinstance(contract, dict) else None
    if not isinstance(contract, dict) or not str(contract.get("cli_command") or "").strip() or not isinstance(props, dict) or not props:
        raise SchemaLoadError(f"verbs.schema.json verbs.{CHARTER_VERB}(cli_command / parameters.properties)未声明")
    return contract


def remote_session_delivery() -> dict[str, Any]:
    """distribution.schema harness_semantics.remote_session_delivery(他机会话章程的拉取出口声明)。缺键 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("distribution").get("harness_semantics") or {}).get("remote_session_delivery")
    if not isinstance(decl, dict) or decl.get("mode") != "pull" or decl.get("verb") != CHARTER_VERB \
            or not str(decl.get("host_placeholder") or "").strip():
        raise SchemaLoadError("distribution.schema.json harness_semantics.remote_session_delivery(mode=pull / "
                              f"verb={CHARTER_VERB} / host_placeholder)未声明")
    return decl


def add_charter_arguments(parser: Any) -> None:
    """argparse 参数由 verbs.schema lybra_charter.parameters 生成(命令参数只此一处声明)。"""
    contract = charter_verb_contract()
    params = contract["parameters"]
    required = set(params.get("required") or [])
    for name, spec in params["properties"].items():
        flag = "--" + name.replace("_", "-")
        kwargs: dict[str, Any] = {"help": str(spec.get("description") or ""), "required": name in required}
        if spec.get("type") == "boolean":
            kwargs["action"] = "store_true"
        elif name == "workspace_root":
            kwargs["type"] = Path
        elif name == "harness":  # 值域 = distribution.schema harness_semantics.kinds 的键(唯一推导口), 越界 = argparse 用法错
            from tools.aipos_cli.distribution_sync import declared_harness_kinds

            kwargs["choices"] = list(declared_harness_kinds())
        parser.add_argument(flag, **kwargs)


def instance_charter(
    governance_root: str | Path,
    *,
    role_class: str,
    instance: str | None = None,
    harness: str | None = None,
) -> dict[str, Any]:
    """实例的渲染后章程(只读)。返回 {text, instance, role, role_class, harness, distribution_id, workstation, executor_mode, governance_root}
    (子 agent: workstation=None, executor_mode=land 事件 mode; 工位: executor_mode=None)。

    判序(任一不成立 = CharterRefused, 带出口): 实例(缺省 = 治理根 .lybra/role 的顶层实例)→ 本项目已接入(enrollment.instance_enrollment
    唯一判定; AIPOS-F146 件①: 其 land 事件 mode 为无工位模式(子 agent 执行者)= 身份取 subagent_identity(land 事件 role/mode/harness),
    harness 缺省 = land 事件 harness, 不要求工位, 下接角色类判定)→ 否则本治理根 enrollment_log 有其 land 事件
    (instance_enrollment 结果中的 workstation_location 定位)且工位在本机 → 工位 .lybra/role 有该实例的记录(workstation_identity(…, instance=)
    经分槽读口 read_role_record: 治理根多顾问实例取 instances.<实例> 槽, AIPOS-F140; 只读非秘密字段)
    → 角色解析出的类(custom_roles.resolve_role_to_class)== role_class → 本角色该 harness(缺省 = 工位 role 记录的 harness)恰有一条
    kind=charter 分发条目(与门清单同一构建器 workstation_wiring.declared_role_distributions)→ 母本 + charter_render_context → render_charter。
    声明/治理根坏 = 原异常上抛(ValueError / FileNotFoundError / SchemaLoadError, fail-closed)。"""
    from tools.aipos_cli.custom_roles import UnknownRoleClass, resolve_role_to_class
    from tools.aipos_cli.distribution_sync import _is_charter, harness_distributions, harness_kind_declaration, workstation_harness
    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.aipos_cli.workstation_wiring import declared_role_distributions
    from tools.distribution_manifest import REPO_ROOT, get_product_commit

    gov = Path(governance_root).expanduser().resolve()
    if not (gov / "project.json").is_file():
        raise FileNotFoundError(f"{gov} 无 project.json(不是已建治理根); 出口: 在治理根下运行或给 --workspace-root <治理根>")
    inst = str(instance or "").strip()
    if not inst:
        if not is_enrolled_workstation(gov):
            raise CharterRefused(f"未给 --instance, 且治理根 {gov} 无 .lybra/role(无本治理根工位实例可取); 出口: 加 --instance <实例>")
        inst = str(workstation_identity(gov)["instance"])
    exit_enroll = ("出口: 先按接入向导 enroll 该实例(lybra onboarding guide), 或核对实例名(lybra roles enroll-where --instance <实例>)")
    view = instance_enrollment(gov, inst)  # AIPOS-F146: 实例身份与模式唯一判定(连接文件读不出 = ValueError 上抛, fail-closed)
    if not view["enrolled"]:
        raise CharterRefused(f"实例 {inst} 未在本治理根接入: {view['reason']}; {exit_enroll}")
    if view["governance_root_mode"]:
        # AIPOS-F146 件①: 无工位的已接入执行者(子 agent): 身份 = land 事件 role/mode/harness, 不要求工位、不读 .lybra/role
        try:
            identity = subagent_identity(gov, view)
        except WorkstationIdentityError as exc:
            raise CharterRefused(f"实例 {inst}(接入模式 {view['mode']})身份不可用: {exc}; 出口: 以同一模式重新 enroll 该实例") from exc
        workstation = gov
        default_kind = str(view["harness"] or "").strip()
        if not default_kind:
            raise CharterRefused(f"实例 {inst}(接入模式 {view['mode']})的 land 事件缺 harness=, 无从取章程条目; "
                                 "出口: 加 --harness <顾问会话 kind>, 或以同一模式重新 enroll 该实例")
    else:
        loc = view["workstation"]
        if not loc["found"]:
            raise CharterRefused(f"实例 {inst} 未在本治理根接入: {loc['reason']}; {exit_enroll}")
        if loc["transport"] != "local":
            raise CharterRefused(f"实例 {inst} 的工位在他机(land 事件 host={loc['host']} dir={loc['dir']}), 本机读不到其 .lybra/role; "
                                 "出口: 在该工位所在机运行本命令")
        workstation = Path(str(loc["dir"])).expanduser()
        try:
            # AIPOS-F140 件①: 按实例取记录(治理根多顾问实例各取其槽; 工位无槽 = 顶层, 实例不符拒因原文同修前)
            identity = workstation_identity(workstation, instance=inst)
        except WorkstationInstanceNotRegistered as exc:
            listed = f"; 本治理根已登记实例: {', '.join(exc.registered)}" if exc.slotted else ""
            raise CharterRefused(f"登记工位 {workstation} 的 .lybra/role 实例 = {exc.top_instance!r} ≠ {inst!r}"
                                 f"(工位已被别的实例接入{listed}); 出口: 核对实例名或重新 enroll") from exc
        except WorkstationIdentityError as exc:
            raise CharterRefused(f"实例 {inst} 的登记工位 {workstation} 身份不可用: {exc}; 出口: 重新 enroll 该实例") from exc
        if identity["instance"] != inst:
            raise CharterRefused(f"登记工位 {workstation} 的 .lybra/role 实例 = {identity['instance']!r} ≠ {inst!r}"
                                 "(工位已被别的实例接入); 出口: 核对实例名或重新 enroll")
        default_kind = ""
    try:
        actual_class = str(resolve_role_to_class(str(identity["role"]), gov, required=True))
    except UnknownRoleClass as exc:
        raise CharterRefused(f"实例 {inst} 的角色 {identity['role']!r} 解析不到角色类: {exc}") from exc
    if actual_class != role_class:
        raise CharterRefused(f"实例 {inst} 的角色类 = {actual_class!r}(角色 {identity['role']!r}) ≠ --role {role_class!r}; "
                             f"出口: --role {actual_class}")
    kind = str(harness or "").strip() or default_kind or str(workstation_harness(workstation, instance=inst)["kind"])
    harness_kind_declaration(kind)  # 不在声明 = ValueError(列出合法值)
    charters = [d for d in harness_distributions(declared_role_distributions(str(identity["role"]), actual_class), kind)
                if _is_charter(d)]
    if not charters:
        raise CharterRefused(f"角色 {identity['role']!r}(类 {actual_class})在 harness={kind} 下无章程分发条目"
                             "(distribution.schema distributions[kind=charter, applies_to_roles, target.harness]); 无可输出")
    if len(charters) > 1:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"角色类 {actual_class} 在 harness={kind} 下有多条章程分发条目 "
                              f"{[d['distribution_id'] for d in charters]}(须恰一条, 声明错误)")
    dist = charters[0]
    master_text = (REPO_ROOT / str(dist["source_path"])).read_text(encoding="utf-8")
    ctx = charter_render_context(gov, identity=identity, product_commit=get_product_commit(REPO_ROOT))
    return {
        "text": render_charter(master_text, ctx),
        "instance": inst,
        "role": identity["role"],
        "role_class": actual_class,
        "harness": kind,
        "distribution_id": dist["distribution_id"],
        "workstation": None if view["governance_root_mode"] else str(workstation),  # 子 agent 无工位
        "executor_mode": identity.get("executor_mode"),
        "governance_root": str(gov),
    }


def charter_pull_command(governance_root: str | Path, *, instance: str, role_class: str) -> dict[str, Any]:
    """他机会话开局取章程的命令(唯一拼装; 声明 distribution.schema harness_semantics.remote_session_delivery)。

    返回 {command, host, host_declared, host_hint}: host = project.json workstations.<实例>.gate_ssh_alias(唯一读取口
    workspace_config.project_workstation; 治理根尚无 project.json(向导先于建项目生成)或未声明 = host_placeholder + 声明出口, 不猜主机)。"""
    import shlex

    from tools.aipos_cli.workspace_config import WorkstationDeclarationError, project_workstation, read_project_json

    decl = remote_session_delivery()
    cli = str(charter_verb_contract()["cli_command"]).strip()
    gov = Path(governance_root).expanduser()
    host: str | None = None
    hint = ""
    if (gov / "project.json").is_file():
        try:
            host = project_workstation(gov, instance)["gate_ssh_alias"]
        except WorkstationDeclarationError as exc:
            hint = f"主机未声明({exc.code}: {exc.reason})"
        project = str(read_project_json(gov).get("project") or "").strip() or gov.name
    else:
        hint = f"主机未声明(治理根 {gov} 尚未建项目)"
        project = gov.name
    if host is None:
        hint += (f"; 声明后本出口带真实主机: lybra project set-workstation {shlex.quote(project)} --instance {shlex.quote(instance)} "
                 f"--gate-ssh-alias {decl['host_placeholder']} --material-access <一句话> --confirm")
    remote = f"cd {shlex.quote(str(gov))} && {cli} --role {shlex.quote(role_class)} --instance {shlex.quote(instance)}"
    host_text = shlex.quote(host) if host else str(decl["host_placeholder"])
    return {"command": f"ssh {host_text} {shlex.quote(remote)}", "host": host, "host_declared": host is not None, "host_hint": hint}


def run_charter_cli(args: Any) -> int:
    """`lybra charter` 薄壳: stdout 只出章程全文; 拒因 / 错误走 stderr, 退出码读 verbs.schema lybra_charter.exit_codes。"""
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    try:
        # AIPOS-F144 件①: 治理根经 CLI 唯一薄壳(读写共用 resolve_governance_root: 全局/子命令级 --workspace-root > 环境变量 >
        # 所在治理根 > 工位声明 > 拒), 原本处自写的「显式 > 所在治理根 > 拒」退役(禁第二实现)
        gov = _find_repo_root_for_args(args)
        result = instance_charter(gov, role_class=str(args.role).strip(), instance=getattr(args, "instance", None),
                                  harness=getattr(args, "harness", None))
    except CharterRefused as exc:
        print(f"lybra charter: 拒: {exc}", file=sys.stderr)
        return declared_exit_code(CHARTER_VERB, "refused")
    except (FileNotFoundError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra charter: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(CHARTER_VERB, "unreadable")
    sys.stdout.write(result["text"])
    return declared_exit_code(CHARTER_VERB, "ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
