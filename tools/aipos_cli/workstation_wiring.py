"""AIPOS-F54 — 工位可启动最小集(bootstrap minimum)单源实现。

enroll 一次性落齐"可启动最小集"(缺任何一项, 新工位起不来):
  ① .pi/ 接线(settings.json + extensions/{claim.ts, 声明的扩展} + skills/<name>)
  ② .lybra/role#owner_policy_ref(从门侧生效 owner_autonomy_policy 信封推导)
  ③ .lybra/connection.json#lybra_bin(指向实际部署位)
  ④ AGENTS.md 章程种子(AIPOS-F82: charter_render 渲染物; 母本变/声明变由 /lybra sync 重渲染)

AIPOS-F82 件②(2026-09-24 重 enroll 实撞: pi 启动 Cannot find module + AGENTS.md 显示 {{占位}}):
  - 接线目标由 **distribution 声明**推导(tools.distribution_manifest.build_role_manifest, 与门/sync 同一构建器):
      扩展挂载 = 本角色 kind=extension 分发物(单文件 → 相对软链; 多文件 → 转发包装); claim.ts = 最小集声明项;
      **只写目标已存在的扩展挂载**(不存在 = 不写 + warnings 点名, 禁写悬空包装——悬空扩展令 pi 启动即崩);
      skills/<name> = 本角色 kind=skills 分发物的技能目录(声明即下一次 sync 的落点, 首次 sync 前软链待落地, warnings 点名);
      roles.schema tool_package 中未被 distribution 声明分发给本角色的扩展/技能(如已退役的旧门循环扩展 lybra-loop、
      执行体零门后的 finalize-slice)= 不接 + warnings 点名。
  - AGENTS.md 种子 = charter_render.render_charter 渲染物(与 sync 同一渲染器、同一渲染上下文); 渲染不成立(无治理根/
    声明不全)= 不写 + warnings(fail-closed, 禁落未渲染母本); 已存在仍不覆盖(seed 语义保留, 覆盖归 sync)。

单源纪律(卡面锚点):
  - role→skills/extensions 映射 = schema/distribution.schema.json applies_to_roles(AIPOS-F82; 按 role_class 展开, 禁代码硬编码角色名)
  - 最小集清单 = schema/distribution.schema.json minimum_bootable_set(缺项逐项点名)
  - 信封判定 = tools/aipos_cli/autonomy_policy.py normalize(复用, 禁第二份信封解析)
  - 接线规格 = 卡面 Owner 裁定(2026-08-28 项目顾问逆向+顾问实测复核):
      settings.json 最小配置禁写 defaultModel、禁用 extensions 数组当加载清单;
      claim.ts = 相对软链;多文件扩展挂载 = 真实转发文件(多文件扩展经 symlink 丢兄弟模块);
      skills/<name> = 逐技能软链(按角色类分配子集)。

seed_only 语义(F27):已存在则跳过并出声, 绝不覆盖用户定制。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 接线规格常量(卡面 Owner 裁定原文, 禁再摸索)
# ---------------------------------------------------------------------------

#: settings.json 最小配置:禁写 defaultModel;禁用 extensions 数组当加载清单(pi 自动发现)。
SETTINGS_TEMPLATE: dict[str, Any] = {
    "defaultProvider": "kiwiai",
    "enableSkillCommands": True,
    "skills": [],
}

#: 多文件扩展的挂载包装必须是真实转发文件(多文件扩展经 symlink 挂载会丢兄弟模块)。
#: 标记行「AIPOS-R3: 挂载包装」= 分发器铺的包装(sync prune 据此识别, 见 distribution_sync._is_distributed_file)。
EXTENSION_WRAPPER_TEMPLATE = (
    "// AIPOS-R3: 挂载包装指向分发落点(由 gate 分发器写入工位父根)\n"
    "// 真实文件非 symlink:pi 扩展加载器按 symlink 所在位置解析相对导入,\n"
    "// 多文件扩展经文件 symlink 挂载会丢兄弟模块。包装文件以自身真实路径转发,\n"
    "// 兄弟导入在分发落点真实目录内解析。\n"
    'export {{ default }} from "{target}";\n'
)

#: 工位/.pi/<子目录>/<挂载名> → 工位父根的相对前缀(接线规格: 挂载点在工位根下两级)。
MOUNT_TO_HARNESS_PARENT = "../../../"

#: claim.ts = 相对软链(工位/.pi/extensions/claim.ts → 仓库根/_shared/extensions/claim.ts; 最小集声明项)。
CLAIM_SYMLINK_TARGET = "../../../_shared/extensions/claim.ts"

#: 无 harness 循环的角色类(不落 .pi 接线; owner/copilot 等不入循环)。
LOOP_ROLE_CLASSES = ("executor", "auditor", "advisor")


# ---------------------------------------------------------------------------
# role_class 解析(单源: token_entry.role_class 优先, 否则 builtin 注册表)
# ---------------------------------------------------------------------------

def resolve_role_class(role: str, token_entry: dict[str, Any] | None = None) -> str:
    """解析角色类:自定义角色按 token 携带的 role_class 取 builtin 类(与 F22D/F44D-A 同源)。"""
    rc = str((token_entry or {}).get("role_class") or "").strip()
    if rc:
        return rc
    from tools.schema_loader import get_role_spec

    spec = get_role_spec(role)
    return str((spec or {}).get("role_class") or role or "").strip()


def declared_role_distributions(role: str, role_class: str) -> list[dict[str, Any]]:
    """AIPOS-F82 件②: 本角色(按 role_class 展开)的分发声明 —— 与门 lybra_distribution_manifest / sync 并集同一构建器
    (tools.distribution_manifest.build_role_manifest, 本 CLI 所在产品树)。该角色无任何分发条目 = [](声明即零应得件);
    声明不可读 / 源缺失 = 原样抛(fail-closed)。"""
    from tools.distribution_manifest import REPO_ROOT, build_role_manifest

    try:
        return list(build_role_manifest(REPO_ROOT, role, role_class=role_class)["distributions"])
    except ValueError as exc:
        if str(exc).startswith("No distributions found for role"):
            return []
        raise


def declared_role_skills(dists: list[dict[str, Any]]) -> dict[str, str]:
    """kind=skills 分发物 → {技能名: 挂载软链目标}(技能名 = 文件相对路径首段; 目标 = 工位父根/<target_path>/<名>)。"""
    out: dict[str, str] = {}
    for dist in dists:
        if dist.get("kind") != "skills" or dist.get("target_base") != "harness_parent":
            continue
        base = str(dist.get("target_path") or "").strip("/")
        for f in dist.get("files", []):
            name = str(f["path"]).split("/", 1)[0]
            out.setdefault(name, f"{MOUNT_TO_HARNESS_PARENT}{base}/{name}")
    return dict(sorted(out.items()))


def declared_role_extensions(dists: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """kind=extension 分发物 → {挂载文件名: {kind: symlink|wrapper, target}}。

    单文件扩展(source_is_file / 落点即 .ts 文件)= 相对软链; 多文件扩展(目录落点)= 转发包装, 入口 = <目录>/<目录名>.ts。
    """
    out: dict[str, dict[str, str]] = {}
    for dist in dists:
        if dist.get("kind") != "extension" or dist.get("target_base") != "harness_parent":
            continue
        target_path = str(dist.get("target_path") or "").strip("/")
        if dist.get("source_is_file") or target_path.endswith(".ts"):
            name = Path(target_path).name
            out[name] = {"kind": "symlink", "target": f"{MOUNT_TO_HARNESS_PARENT}{target_path}", "distribution_id": str(dist.get("distribution_id"))}
        else:
            entry = Path(target_path).name
            out[f"{entry}.ts"] = {"kind": "wrapper", "target": f"{MOUNT_TO_HARNESS_PARENT}{target_path}/{entry}.ts", "distribution_id": str(dist.get("distribution_id"))}
    return dict(sorted(out.items()))


def tool_package_for_class(role_class: str) -> dict[str, list[str]]:
    """roles.schema tool_package(按角色类): 仅用于点名「tool_package 列出但 distribution 未声明分发」的退役项(不作接线源)。"""
    from tools.schema_loader import load_schema

    for spec in load_schema("roles").get("roles", []):
        if str(spec.get("role_class") or "") == role_class and spec.get("tool_package"):
            pkg = spec["tool_package"] or {}
            return {"extensions": list(pkg.get("extensions") or []), "skills": list(pkg.get("skills") or [])}
    return {"extensions": [], "skills": []}


# ---------------------------------------------------------------------------
# ② owner_policy_ref 推导(单源: 治理仓 5_tasks/policies 信封工件)
# ---------------------------------------------------------------------------

def derive_effective_owner_policy_ref(
    governance_root: Path | str | None,
    *,
    role: str,
    agent_instance: str | None = None,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    """推导当前生效的 owner_autonomy_policy 信封 ID(写入 .lybra/role#owner_policy_ref)。

    判定复用 autonomy_policy.normalize_policy 字段语义(禁第二份信封解析):
      mode=PreAuthorized + status=active + approved_by_owner + 时间窗内 +
      agent_or_role 覆盖 {role, agent_instance} + max_tasks>0。
    多信封命中时确定性择一:实例精确匹配 > 角色匹配, 同级取 active_from 最新。
    推导不出返回 (None, 原因)——调用方按角色类决定报错带路或仅告警(禁静默留空)。
    """
    if not governance_root:
        return None, "无 governance_root(自包含码未携带且 connection.json 未声明), 无法推导信封"
    root = Path(governance_root).expanduser()
    policies_dir = root / "5_tasks" / "policies"
    if not policies_dir.is_dir():
        return None, f"信封目录不存在: {policies_dir}"

    from tools.aipos_cli.autonomy_policy import normalize_policy
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    now = now or datetime.now(timezone.utc)
    identity = {str(role or "").strip(), str(agent_instance or "").strip()}
    identity.discard("")

    candidates: list[tuple[int, str, str]] = []  # (优先级, active_from, policy_id)
    reasons: list[str] = []
    for path in sorted(policies_dir.glob("pol_*.md")):
        try:
            metadata, _body, _warn = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except OSError as exc:
            reasons.append(f"{path.name}: 读取失败 {exc}")
            continue
        policy = normalize_policy(metadata if isinstance(metadata, dict) else {})
        if policy is None:
            continue
        covered = str(policy.get("agent_or_role") or "").strip()
        if policy.get("mode") != "PreAuthorized":
            continue
        if policy.get("status") != "active" or not policy.get("approved_by_owner"):
            reasons.append(f"{policy['policy_id']}: status={policy.get('status')} 非 active/未获 owner 批")
            continue
        try:
            active_from = datetime.fromisoformat(str(policy.get("active_from")).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(policy.get("expires_at")).replace("Z", "+00:00"))
        except ValueError:
            reasons.append(f"{policy['policy_id']}: 时间窗不可解析")
            continue
        if not (active_from <= now < expires_at):
            reasons.append(f"{policy['policy_id']}: 时间窗外({active_from.date()}~{expires_at.date()})")
            continue
        if not covered or covered not in identity:
            continue
        if int(policy.get("max_tasks") or 0) <= 0:
            reasons.append(f"{policy['policy_id']}: max_tasks<=0")
            continue
        priority = 2 if covered == str(agent_instance or "").strip() and agent_instance else 1
        candidates.append((priority, str(policy.get("active_from") or ""), policy["policy_id"]))

    if not candidates:
        detail = f"; {'; '.join(reasons[:3])}" if reasons else ""
        return None, f"无覆盖角色 {role}(实例 {agent_instance or '-'})的生效 PreAuthorized 信封{detail}"
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2], "matched"


# ---------------------------------------------------------------------------
# ③ lybra_bin 推导(指向实际部署位)
# ---------------------------------------------------------------------------

def resolve_deployed_lybra_bin() -> str | None:
    """推导本机 lybra CLI 部署位:运行中的 bin 本身优先(跟随 symlink 到部署位), 否则探测代码仓 .deploy/current。

    AIPOS-F54-fix1: 跟随 symlink —— sys.argv[0] 可能是外部符号链接,
    实际部署位在 .deploy/current/bin/lybra。跟随后返回真实路径。
    禁硬编码任何绝对路径字面量。
    """
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 and argv0.name == "lybra" and argv0.is_file():
        # Follow symlink to actual deployment location
        resolved = argv0.resolve()
        if resolved.is_file():
            return str(resolved)
        return str(argv0.resolve()) if argv0.is_absolute() else str(argv0.absolute())
    repo_root = Path(__file__).resolve().parents[2]
    probe = repo_root / ".deploy" / "current" / "bin" / "lybra"
    if probe.is_file():
        return str(probe.resolve())
    return None


# ---------------------------------------------------------------------------
# ① .pi 接线 + ④ AGENTS.md 占位(seed_only: 已存在则跳过并出声)
# ---------------------------------------------------------------------------

def _seed_file(path: Path, content: str) -> str:
    if path.exists() or path.is_symlink():
        return "skipped(existing)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "created"


def _seed_symlink(path: Path, target: str) -> str:
    if path.exists() or path.is_symlink():
        return "skipped(existing)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    return "created(pending-sync)" if not path.exists() else "created"


def _seed_charter(workspace_root: Path, dists: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    """AIPOS-F82 件②: AGENTS.md 种子 = charter_render 渲染物(与 sync 同一渲染器、同一上下文); 已存在不覆盖(seed 语义)。

    母本 = 本角色 kind=charter 分发声明的源(本 CLI 所在产品树); 上下文 = charter_render_context(工位治理根, 工位身份)。
    渲染不成立 = 不写 + warnings(fail-closed, 禁落未渲染母本/占位文本)。
    """
    from tools.aipos_cli.charter_render import (
        WorkstationIdentityError,
        charter_render_context,
        render_charter,
        resolve_workstation_governance_root,
        workstation_identity,
    )
    from tools.distribution_manifest import REPO_ROOT, get_product_commit

    path = workspace_root / "AGENTS.md"
    if path.exists():
        return {"status": "skipped(existing)", "source": None}
    charters = [d for d in dists if d.get("kind") == "charter"]
    if not charters:
        warnings.append("AGENTS.md 未写: 本角色无 kind=charter 分发声明(distribution.schema), 无母本可渲染")
        return {"status": "not_written(no-charter-declaration)", "source": None}
    dist = charters[0]
    master = REPO_ROOT / str(dist["source_path"])
    try:
        identity = workstation_identity(workspace_root)
        gov = resolve_workstation_governance_root(identity)
        ctx = charter_render_context(gov, identity=identity, product_commit=get_product_commit(REPO_ROOT))
        rendered = render_charter(master.read_text(encoding="utf-8"), ctx)
    except (WorkstationIdentityError, FileNotFoundError, ValueError, OSError) as exc:
        warnings.append(
            f"AGENTS.md 未写: 章程渲染不成立({exc.__class__.__name__}: {exc}); 禁落未渲染母本。"
            "出口: 补齐声明(connection.json#governance_root / project.json)后重跑 enroll, 或 lybra sync 渲染"
        )
        return {"status": "not_written(render-failed)", "source": str(master)}
    path.write_text(rendered, encoding="utf-8")
    return {"status": "created(rendered)", "source": str(master), "distribution_id": dist.get("distribution_id")}


def _seed_extension(pi: Path, name: str, spec: dict[str, str], warnings: list[str], *, origin: str) -> dict[str, Any]:
    """扩展挂载: 目标(相对挂载点解析)已存在才写; 不存在 = 不写 + warnings(禁写悬空——悬空扩展令 pi 启动即崩)。"""
    mount = pi / "extensions" / name
    info: dict[str, Any] = {"symlink": spec["kind"] == "symlink", "target": spec["target"], "origin": origin}
    if mount.exists() or mount.is_symlink():
        info.update(status="skipped(existing)", target_exists=mount.exists())
        return info
    target_abs = (mount.parent / spec["target"]).resolve()
    if not target_abs.is_file():
        info.update(status="not_written(target-missing)", target_exists=False)
        warnings.append(
            f".pi/extensions/{name} 未接: 目标 {target_abs} 不存在({origin}); 禁写悬空扩展。"
            "出口: lybra sync 落地分发物后重跑 enroll(已存在项幂等跳过)补挂"
        )
        return info
    mount.parent.mkdir(parents=True, exist_ok=True)
    if spec["kind"] == "symlink":
        mount.symlink_to(spec["target"])
    else:
        mount.write_text(EXTENSION_WRAPPER_TEMPLATE.format(target=spec["target"]), encoding="utf-8")
    info.update(status="created", target_exists=mount.exists())
    return info


def materialize_pi_wiring(
    workspace_root: Path,
    *,
    role: str,
    role_class: str,
) -> dict[str, Any]:
    """幂等落 .pi 接线 + AGENTS.md 渲染种子(seed_only, 已存在跳过, 禁覆盖用户定制)。

    AIPOS-F82 件②: 接线目标全部由 distribution 声明推导(见模块文档); 返回逐项落盘报告 + warnings(不写的项逐项点名)。
    无 harness 循环的角色类不落接线(仅章程种子)。
    """
    report: dict[str, Any] = {"role": role, "role_class": role_class, "items": {}, "warnings": []}
    items = report["items"]
    warnings: list[str] = report["warnings"]

    dists = declared_role_distributions(role, role_class)
    items["AGENTS.md"] = _seed_charter(workspace_root, dists, warnings)

    if role_class not in LOOP_ROLE_CLASSES:
        report["note"] = f"role_class={role_class} 无 harness 循环, 不落 .pi 接线"
        return report

    pi = workspace_root / ".pi"
    items["settings.json"] = {"status": _seed_file(pi / "settings.json", json.dumps(SETTINGS_TEMPLATE, indent=2) + "\n")}
    items["extensions/claim.ts"] = _seed_extension(
        pi, "claim.ts", {"kind": "symlink", "target": CLAIM_SYMLINK_TARGET}, warnings,
        origin="distribution.schema minimum_bootable_set .pi/extensions/claim.ts",
    )
    declared_ext = declared_role_extensions(dists)
    for name, spec in declared_ext.items():
        items[f"extensions/{name}"] = _seed_extension(pi, name, spec, warnings, origin=f"distribution {spec['distribution_id']}")

    skills = declared_role_skills(dists)
    skills_report: dict[str, Any] = {}
    pending: list[str] = []
    for name, target in skills.items():
        link = pi / "skills" / name
        status = _seed_symlink(link, target)
        skills_report[name] = {"status": status, "target": target, "target_exists": link.exists()}
        if not link.exists():
            pending.append(name)
    items["skills"] = {"count": len(skills), "links": skills_report}
    if pending:
        warnings.append(
            f".pi/skills 待 sync 落地({len(pending)} 项, distribution 已声明分发给本角色): {', '.join(pending)}; 下一步 lybra sync"
        )

    # 退役/未声明项点名: roles.schema tool_package 列出但 distribution 未声明分发给本角色 = 不接
    pkg = tool_package_for_class(role_class)
    declared_ext_stems = {Path(n).stem for n in declared_ext}
    retired_ext = [e for e in pkg["extensions"] if e not in declared_ext_stems]
    retired_skills = [k for k in pkg["skills"] if k not in skills]
    if retired_ext or retired_skills:
        report["not_declared"] = {"extensions": retired_ext, "skills": retired_skills}
        warnings.append(
            "未接(roles.schema tool_package 列出, distribution 声明未分发给本角色 = 已退役): "
            + ", ".join([f"extension:{e}" for e in retired_ext] + [f"skill:{k}" for k in retired_skills])
        )

    # AIPOS-F58: 把 .pi/ 接线路径登记进 .git/info/exclude(防 `git stash -u` 连坐抹掉)
    from tools.aipos_cli.git_exclude import collect_wiring_exclude_paths, register_git_exclude
    wiring_exclude_paths = collect_wiring_exclude_paths(workspace_root)
    if wiring_exclude_paths:
        git_exclude_report = register_git_exclude(workspace_root, wiring_exclude_paths)
        report["git_exclude"] = git_exclude_report

    return report


# ---------------------------------------------------------------------------
# ⑮ 可启动最小集校验(清单单源 config.schema#minimum_bootable_set, 缺项逐项点名)
# ---------------------------------------------------------------------------

def minimum_bootable_set_items() -> list[dict[str, str]]:
    """读 distribution.schema#minimum_bootable_set.items 声明(单源; 禁代码内写第二份清单)。

    声明落位 = 分发清单(卡面⑮"config.schema 或分发清单择一"; 本卡 output_target 声明
    schema/distribution.schema.json, 故落此件)。

    AIPOS-F54-fix1: 追加 workspace_root_match 检查项(代码内补铸, schema 未更新前
    由代码保证自检覆盖; schema 更新后此处自动去重)。
    """
    from tools.schema_loader import load_schema

    dist = load_schema("distribution")
    section = dist.get("minimum_bootable_set")
    items = section.get("items") if isinstance(section, dict) else section
    items = items or []
    result = [dict(item) for item in items if isinstance(item, dict) and item.get("name")]
    # AIPOS-F54-fix1: 补铸 workspace_root_match(若 schema 尚未声明则追加; 已声明则跳过)
    if not any(str(i.get("kind") or "") == "workspace_root_match" for i in result):
        result.append({
            "name": "connection.json#workspace_root==governance_root",
            "kind": "workspace_root_match",
            "container": ".lybra/connection.json",
            "path": ".lybra/connection.json",
            "description": "workspace_root must equal governance_root (single source: code governance_root; harness root must not leak in). Missing or mismatched = missing item.",
        })
    return result


def verify_minimum_bootable_set(workspace_root: Path) -> dict[str, Any]:
    """逐项核对可启动最小集; 缺项逐项点名(禁"少一个键整个起不来但不知道少哪个")。

    AIPOS-F54-fix1: 新增 workspace_root_match 检查 —— workspace_root 须等于 governance_root
    (单源: 码内治理根)。harness root 混入 = 缺项。
    """
    root = Path(workspace_root)
    checks: list[dict[str, Any]] = []
    for item in minimum_bootable_set_items():
        name = str(item.get("name") or "")
        kind = str(item.get("kind") or "file")
        path = root / str(item.get("path") or "")
        if kind == "symlink_dir":
            present = path.is_dir()
        elif kind == "json_key":
            container = root / str(item.get("container") or "")
            key = str(item.get("key") or "")
            try:
                data = json.loads(container.read_text(encoding="utf-8"))
                value = data.get(key) if isinstance(data, dict) else None
                present = bool(value)
                # ⑭ 值须指向实际存在的文件(如 lybra_bin 悬空 = 缺项, sync 侧另有探测回落)
                if present and item.get("value_must_exist") and isinstance(value, str):
                    present = Path(value).expanduser().is_file()
            except (OSError, json.JSONDecodeError):
                present = False
        elif kind == "workspace_root_match":
            # AIPOS-F54-fix1: workspace_root 须等于 governance_root(单源: 码内治理根)
            container = root / str(item.get("container") or "")
            try:
                data = json.loads(container.read_text(encoding="utf-8"))
                ws = str(data.get("workspace_root") or "").strip()
                gov = str(data.get("governance_root") or "").strip()
                if not ws or not gov:
                    present = False
                else:
                    # 规范化比较(消除 symlink/trailing slash 差异)
                    present = Path(ws).resolve() == Path(gov).resolve()
            except (OSError, json.JSONDecodeError):
                present = False
        else:
            present = path.is_file() or path.is_symlink()
        checks.append({"name": name, "path": str(item.get("path") or ""), "present": present})
    missing = [c["name"] for c in checks if not c["present"]]
    return {"ok": not missing, "missing": missing, "checks": checks}
