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


class WorkstationIdentityError(ValueError):
    """工位身份不可解析(缺 .lybra/role / 实例名不合模板), fail-closed。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 工位身份 / 项目归属
# ---------------------------------------------------------------------------

# AIPOS-F106 件④(M2): 工位身份文件名(config.schema configuration_sources.role.source_file = .lybra/role)。
# .lybra 目录一律经 loop_context.ConnectionResolver.discover_lybra_dir 定位; 本模块是 .lybra/role 的唯一读取实现
# (另一读取口 ConnectionResolver.resolve_role / resolve_identity 只出身份键), 其余代码禁直接读该文件。
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


def workstation_identity(harness_root: str | Path) -> dict[str, Any]:
    """读工位 .lybra/role(+ connection.json 非秘密字段)→ {harness_root, role, instance, project, owner_policy_ref, harness,
    governance_root_declared, token_projects, gate_url}。token 值永不读入。

    AIPOS-F106: .lybra 经 ConnectionResolver.discover_lybra_dir 定位; gate_url = 门基址, 经 confirm_client.resolve_gate_base_url
    (唯一推导口, 委托 ConnectionResolver.resolve_gate_url) 读本工位 connection.json 的 mcp.rpc_url, 未声明 = None;
    harness = role 文件的 harness 原值(工位 harness 声明, distribution_sync.workstation_harness 消费)。"""
    root = Path(harness_root).expanduser().resolve()
    role_file = workstation_role_file(root)
    if role_file is None or not role_file.is_file():
        raise WorkstationIdentityError(f"{root}: 无 .lybra/role — 不是已 enroll 工位, 拒绝分发/渲染")
    try:
        data = json.loads(role_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkstationIdentityError(f"{role_file} 不可读/非 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkstationIdentityError(f"{role_file} 须为 JSON 对象(role/instance)")
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
    conn_file = role_file.parent / "connection.json"
    if conn_file.is_file():
        try:
            conn = json.loads(conn_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkstationIdentityError(f"{conn_file} 不可读/非 JSON: {exc}") from exc
        if isinstance(conn, dict):
            from tools.aipos_cli.confirm_client import declared_rpc_url, resolve_gate_base_url

            gov = str(conn.get("governance_root") or "").strip()
            identity["governance_root_declared"] = gov or None
            if declared_rpc_url(conn_file):
                identity["gate_url"] = resolve_gate_base_url(connection_json=conn_file, require_declared=True)
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
            identity["token_projects"] = projects
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

def _substitute(master_text: str, ctx: dict[str, Any]) -> str:
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
        f"- 工位根: `{ctx['harness_root']}`(共享层 `{ctx['harness_parent']}/_shared` 与 `_distributed` 由分发器写, 角色只读)",
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
    """实例的渲染后章程(只读)。返回 {text, instance, role, role_class, harness, distribution_id, workstation, governance_root}。

    判序(任一不成立 = CharterRefused, 带出口): 实例(缺省 = 治理根 .lybra/role 的实例)→ 本治理根 enrollment_log 有其 land 事件
    (enrollment.workstation_location 唯一定位)且工位在本机 → 工位 .lybra/role(workstation_identity 唯一读取, 只读非秘密字段)实例相符
    → 角色解析出的类(custom_roles.resolve_role_to_class)== role_class → 本角色该 harness(缺省 = 工位 role 记录的 harness)恰有一条
    kind=charter 分发条目(与门清单同一构建器 workstation_wiring.declared_role_distributions)→ 母本 + charter_render_context → render_charter。
    声明/治理根坏 = 原异常上抛(ValueError / FileNotFoundError / SchemaLoadError, fail-closed)。"""
    from tools.aipos_cli.custom_roles import UnknownRoleClass, resolve_role_to_class
    from tools.aipos_cli.distribution_sync import _is_charter, harness_distributions, harness_kind_declaration, workstation_harness
    from tools.aipos_cli.enrollment import workstation_location
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
    loc = workstation_location(gov, inst)
    if not loc["found"]:
        raise CharterRefused(f"实例 {inst} 未在本治理根接入: {loc['reason']}; 出口: 先按接入向导 enroll 该实例"
                             "(lybra onboarding guide), 或核对实例名(lybra roles enroll-where --instance <实例>)")
    if loc["transport"] != "local":
        raise CharterRefused(f"实例 {inst} 的工位在他机(land 事件 host={loc['host']} dir={loc['dir']}), 本机读不到其 .lybra/role; "
                             "出口: 在该工位所在机运行本命令")
    workstation = Path(str(loc["dir"])).expanduser()
    try:
        identity = workstation_identity(workstation)
    except WorkstationIdentityError as exc:
        raise CharterRefused(f"实例 {inst} 的登记工位 {workstation} 身份不可用: {exc}; 出口: 重新 enroll 该实例") from exc
    if identity["instance"] != inst:
        raise CharterRefused(f"登记工位 {workstation} 的 .lybra/role 实例 = {identity['instance']!r} ≠ {inst!r}"
                             "(工位已被别的实例接入); 出口: 核对实例名或重新 enroll")
    try:
        actual_class = str(resolve_role_to_class(str(identity["role"]), gov, required=True))
    except UnknownRoleClass as exc:
        raise CharterRefused(f"实例 {inst} 的角色 {identity['role']!r} 解析不到角色类: {exc}") from exc
    if actual_class != role_class:
        raise CharterRefused(f"实例 {inst} 的角色类 = {actual_class!r}(角色 {identity['role']!r}) ≠ --role {role_class!r}; "
                             f"出口: --role {actual_class}")
    kind = str(harness or "").strip() or str(workstation_harness(workstation)["kind"])
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
        "workstation": str(workstation),
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

    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.aipos_cli.workspace_config import enclosing_governance_root
    from tools.schema_loader import SchemaLoadError

    try:
        explicit = getattr(args, "workspace_root", None) or getattr(args, "global_workspace_root", None)
        gov = Path(explicit).expanduser() if explicit else enclosing_governance_root()
        if gov is None:
            raise FileNotFoundError("当前目录不在任何已建治理根内; 出口: cd <治理根> 后运行, 或给 --workspace-root <治理根>")
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
