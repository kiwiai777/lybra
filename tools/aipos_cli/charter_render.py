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

def workstation_identity(harness_root: str | Path) -> dict[str, Any]:
    """读工位 .lybra/role(+ connection.json 非秘密字段)→ {harness_root, role, instance, project, owner_policy_ref,
    governance_root_declared, token_projects, gate_url}。token 值永不读入。"""
    root = Path(harness_root).expanduser().resolve()
    role_file = root / ".lybra" / "role"
    if not role_file.is_file():
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
        "governance_root_declared": None,
        "token_projects": [],
        "gate_url": None,
    }
    conn_file = root / ".lybra" / "connection.json"
    if conn_file.is_file():
        try:
            conn = json.loads(conn_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkstationIdentityError(f"{conn_file} 不可读/非 JSON: {exc}") from exc
        if isinstance(conn, dict):
            gov = str(conn.get("governance_root") or "").strip()
            identity["governance_root_declared"] = gov or None
            rpc = str(((conn.get("mcp") or {}).get("rpc_url")) or "").strip()
            identity["gate_url"] = (rpc[:-len("/mcp")] if rpc.endswith("/mcp") else rpc) or None
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
    for role_key in ("executor", "auditor"):
        prefix = str(prefixes.get(role_key) or "").strip()
        if not prefix:
            raise ValueError(f"naming_profile prefix_mapping 缺 {role_key}(roles.schema naming.prefix), 章程渲染拒")
        sibling[f"{role_key}_instance"] = default_instance_name(prefix, project=str(parsed["project"]), host=machine)
    role = str(identity["role"])
    role_class = resolve_role_to_class(role, gov) or role
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


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
