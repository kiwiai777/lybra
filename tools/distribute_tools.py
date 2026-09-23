#!/usr/bin/env python3
"""AIPOS-CONN-LOOP-1 §4§5: Lybra 分发器 v2 — 规格驱动的泛化执行器

按 distribution.schema.json 声明的规格分发工具/技能/契约到 harness 工位。
实现 DESIGN v2 §4 分发规格底座: distribution WHAT = 数据(schema),
HOW = 固定原语(本引擎)。新增分发条目 = 加数据零改代码。

设计决策(§4): 明确不做代码插件 — 插件=一机制一实现的旁路+供应链面难审;
真正新原语=产品仓一张卡过审计。本引擎只实现固定原语集。

AIPOS-F66B 件①(推送侧与 `lybra sync` 同一规则):
- 目标工位必须已 enroll(.lybra/role), 其 role 须等于请求角色, 其实例项目段(parse_instance_name)须等于分发项目范围
  (--project 显式, 缺省 = 工位自身项目); 不符 = 跳过, 零写入, 结果记 skipped_workstation(2026-09-05 chris hbj-coder 实撞根治)。
- 新原语 render_charter(seed_only 退役): charter = 母本 + 项目声明 → 渲染物(charter_render.render_charter 唯一实现),
  母本变/声明变即重渲染; 工位本地改动 = 声明缺口(覆盖前报 diff); charter 不再有「工位主权文件」例外。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.schema_loader import load_schema, SchemaLoadError

# 产品仓根目录
REPO_ROOT = Path(__file__).parent.parent


def get_product_repo_version() -> str:
    """获取产品仓当前 commit hash 作为版本标识"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()[:12]  # 短 hash
    except subprocess.CalledProcessError:
        return "unknown"


# ============================================================================
# 固定分发原语(DESIGN v2 §4 — 仅此四种,禁插件/自定义代码)
# ============================================================================


def _primitive_copy_tree(
    source_path: Path, target_path: Path, *, force: bool = False, filter_include: list[str] | None = None
) -> dict[str, Any]:
    """原语: 递归拷贝目录树
    
    Args:
        source_path: 源路径
        target_path: 目标路径
        force: 强制覆盖
        filter_include: 如果提供,只拷贝列表中的子项(用于skills过滤)
    
    Returns:
        {"ok": bool, "action": "copy_tree", "source": str, "target": str, "error": str|None}
    """
    if not source_path.exists():
        return {
            "ok": False,
            "action": "copy_tree",
            "source": str(source_path),
            "target": str(target_path),
            "error": f"Source not found: {source_path}",
        }

    try:
        # AIPOS-R6H靶③: 存在性检查改查分发落点本体(非wrapper)
        # 对于file类条目,检查文件本身;对于目录,检查目录本身
        target_exists = target_path.exists()
        
        if target_exists:
            if not force:
                return {
                    "ok": False,
                    "action": "copy_tree",
                    "source": str(source_path),
                    "target": str(target_path),
                    "error": "Target exists (use --force to overwrite)",
                }
            # 强制覆盖:删除已存在的目标
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()

        # 如果有filter,只拷贝指定子项
        if filter_include and source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            for item_name in filter_include:
                item_src = source_path / item_name
                item_dst = target_path / item_name
                if item_src.exists():
                    if item_src.is_dir():
                        shutil.copytree(item_src, item_dst)
                    else:
                        shutil.copy2(item_src, item_dst)
        else:
            # 完整拷贝
            # AIPOS-R6H靶③: file类条目按文件拷贝(消Not a directory)
            if source_path.is_file():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
            elif source_path.is_dir():
                shutil.copytree(source_path, target_path)
            else:
                return {
                    "ok": False,
                    "action": "copy_tree",
                    "source": str(source_path),
                    "target": str(target_path),
                    "error": f"Source is neither file nor directory: {source_path}",
                }

        return {
            "ok": True,
            "action": "copy_tree",
            "source": str(source_path),
            "target": str(target_path),
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "action": "copy_tree",
            "source": str(source_path),
            "target": str(target_path),
            "error": str(e),
        }


def _primitive_write_manifest(target_path: Path, manifest_data: dict[str, Any]) -> dict[str, Any]:
    """原语: 写入版本/来源manifest
    
    Args:
        target_path: manifest文件路径
        manifest_data: manifest数据
    
    Returns:
        {"ok": bool, "action": "write_manifest", "path": str, "error": str|None}
    """
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "action": "write_manifest", "path": str(target_path), "error": None}
    except Exception as e:
        return {"ok": False, "action": "write_manifest", "path": str(target_path), "error": str(e)}


def _primitive_render_config(
    template_path: Path, target_path: Path, variables: dict[str, str], *, force: bool = False
) -> dict[str, Any]:
    """原语: 渲染配置模板(简单变量替换)
    
    Args:
        template_path: 模板文件路径
        target_path: 目标文件路径
        variables: 变量字典 {key: value}
        force: 强制覆盖
    
    Returns:
        {"ok": bool, "action": "render_config", "template": str, "target": str, "error": str|None}
    """
    if not template_path.exists():
        return {
            "ok": False,
            "action": "render_config",
            "template": str(template_path),
            "target": str(target_path),
            "error": f"Template not found: {template_path}",
        }

    if target_path.exists() and not force:
        return {
            "ok": False,
            "action": "render_config",
            "template": str(template_path),
            "target": str(target_path),
            "error": "Target exists (use --force to overwrite)",
        }

    try:
        content = template_path.read_text(encoding="utf-8")
        for key, value in variables.items():
            content = content.replace(f"{{{{{key}}}}}", value)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

        return {
            "ok": True,
            "action": "render_config",
            "template": str(template_path),
            "target": str(target_path),
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "action": "render_config",
            "template": str(template_path),
            "target": str(target_path),
            "error": str(e),
        }


def _primitive_mint_credential(role: str, agent_instance: str, projects: list[str]) -> dict[str, Any]:
    """原语: 铸发角色凭据(通过gate enroll流程)
    
    注: 本原语声明存在但未实现完整逻辑(需要gate交互),
    作为扩展点保留。当前enroll由独立工具完成。
    
    Args:
        role: 角色名
        agent_instance: agent实例名
        projects: 项目列表
    
    Returns:
        {"ok": bool, "action": "mint_credential", "role": str, "error": str|None}
    """
    return {
        "ok": False,
        "action": "mint_credential",
        "role": role,
        "error": "mint_credential primitive not yet implemented (use enroll tool)",
    }


# ============================================================================
# 分发引擎 — 规格驱动执行器
# ============================================================================


def load_distribution_spec() -> dict[str, Any]:
    """加载分发规格 schema"""
    try:
        return load_schema("distribution", repo_root=REPO_ROOT)
    except SchemaLoadError as e:
        raise ValueError(f"Failed to load distribution.schema.json: {e}") from e


def get_distributions_for_role(role: str, spec: dict[str, Any], project_root: Path | None = None) -> list[dict[str, Any]]:
    """获取适用于某角色的所有分发条目
    
    AIPOS-F25 大项B: 支持角色类引用(class:executor)。applies_to_roles 可包含:
    - 内建角色名: "executor" / "auditor" / "advisor"
    - 角色类引用: "class:executor" (匹配该类下所有角色,含自定义角色)
    - 自定义角色按注册表所属 class 匹配
    
    Args:
        role: 角色名(内建或自定义)
        spec: distribution schema内容
        project_root: 产品仓根目录(用于解析自定义角色 class)
    
    Returns:
        适用的distribution条目列表
    """
    from tools.aipos_cli.custom_roles import resolve_role_to_class
    
    # 解析角色到其 builtin class
    role_class = resolve_role_to_class(role, project_root) if project_root else role
    if not role_class:
        role_class = role  # fallback: 按原名匹配
    
    all_distributions = spec.get("distributions", [])
    matched = []
    for d in all_distributions:
        applies = d.get("applies_to_roles", [])
        # 直接角色名匹配
        if role in applies:
            matched.append(d)
            continue
        # 角色类引用匹配 (class:executor)
        class_ref = f"class:{role_class}"
        if class_ref in applies:
            matched.append(d)
            continue
    return matched


def _fingerprint_diff(source_path: Path, target_path: Path) -> str:
    """AIPOS-F27 大项A: 计算源与目标的差异指纹(用于 seed_only 跳过时的出声提示)。
    
    Returns:
        简短差异描述字符串
    """
    import hashlib
    
    def _file_hash(p: Path) -> str:
        if not p.exists():
            return "<missing>"
        if p.is_file():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        # 目录: 递归哈希所有文件
        h = hashlib.sha256()
        for child in sorted(p.rglob("*")):
            if child.is_file():
                h.update(child.relative_to(p).as_posix().encode())
                with open(child, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
        return h.hexdigest()[:16]
    
    src_hash = _file_hash(source_path)
    tgt_hash = _file_hash(target_path)
    
    if src_hash == tgt_hash:
        return f"identical (sha256:{src_hash})"
    return f"source=sha256:{src_hash} target=sha256:{tgt_hash} DIFFER"


def _primitive_render_charter(
    source_path: Path, target_path: Path, render_context: dict[str, Any] | None, *, previous_rendered_sha: str | None = None
) -> dict[str, Any]:
    """原语(AIPOS-F66B 件①): 章程 = 母本 + 项目声明 → 渲染物(charter_render.render_charter 唯一实现, 与 sync 同用)。

    - render_context 缺(治理根不可解析)= 拒(fail-closed, 带出口), 不落半成品、不退回裸拷贝;
    - 目标已存在且 ≠ 渲染物 = 声明缺口: 记 unified diff(工位本地 vs 渲染物)后覆盖;
    - 目标 == 渲染物 = unchanged(零写)。
    """
    from tools.aipos_cli.charter_render import charter_fingerprints, local_edit_diff, render_charter

    base = {"action": "render_charter", "source": str(source_path), "target": str(target_path)}
    if not source_path.is_file():
        return {**base, "ok": False, "error": f"Charter master not found: {source_path}"}
    if render_context is None:
        return {**base, "ok": False, "error": (
            "章程渲染需项目声明(治理根不可解析): 工位 connection.json 无 governance_root 且 home_root/<project> 不成立; "
            "出口: distribute_tools --governance-root <治理根>(禁裸拷贝母本)"
        )}
    master_text = source_path.read_text(encoding="utf-8")
    try:
        rendered = render_charter(master_text, render_context)
    except ValueError as exc:
        return {**base, "ok": False, "error": f"章程渲染失败: {exc}"}
    gap = None
    written = True
    if target_path.is_file():
        local_text = target_path.read_text(encoding="utf-8", errors="replace")
        if local_text == rendered:
            written = False
        else:
            import hashlib as _hashlib

            local_is_previous_render = bool(previous_rendered_sha) and _hashlib.sha256(local_text.encode("utf-8")).hexdigest() == previous_rendered_sha
            if not local_is_previous_render:  # 母本/声明变的正常重渲染不算缺口; 本地手改 / 无指纹旧副本才是
                gap = {"path": str(target_path), "diff": local_edit_diff(rendered, local_text, target_path)}
    if written:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered, encoding="utf-8")
    return {**base, "ok": True, "error": None, "written": written, "declaration_gap": gap,
            "fingerprints": charter_fingerprints(master_text, render_context, rendered)}


def execute_distribution(
    dist: dict[str, Any], target_harness_root: Path, *, force: bool = False, version: str = "unknown",
    render_context: dict[str, Any] | None = None, previous_rendered_sha: str | None = None,
) -> dict[str, Any]:
    """执行单个分发条目
    
    AIPOS-F66B 件①: charter kind 一律走 render_charter 原语(seed_only 退役, --force 与之无关);
    render_context = charter_render.charter_render_context(工位治理根声明), 缺 = 该条目拒。
    
    Args:
        dist: distribution条目(来自schema)
        target_harness_root: 目标harness根目录
        force: 强制覆盖(非 charter 条目)
        version: 源版本(git commit hash)
        render_context: 章程渲染上下文(charter 条目必需)
    
    Returns:
        执行结果字典
    """
    dist_id = dist.get("distribution_id", "unknown")
    kind = dist.get("kind", "unknown")
    operation = dist.get("operation", "copy_tree")

    # 解析源路径
    source_spec = dist.get("source", {})
    source_rel_path = source_spec.get("path", "")
    source_path = REPO_ROOT / source_rel_path

    # 解析目标路径
    target_spec = dist.get("target", {})
    target_rel_path = target_spec.get("relative_path", "")
    
    # 根据kind决定目标基准目录
    if kind == "charter":
        # 契约直接写到harness根目录
        target_path = target_harness_root / target_rel_path
    else:
        # 其他(extensions/skills)写到父目录的_distributed/
        target_path = target_harness_root.parent / target_rel_path

    # AIPOS-F66B 件①: charter kind 一律渲染(seed_only 退役; 声明里若残留 seed_only 亦不生效, 出声)
    if kind == "charter" or operation == "render_charter":
        if dist.get("seed_only"):
            print(f"Warning: 分发条目 {dist_id} 声明 seed_only 已退役(AIPOS-F66B), 按 render_charter 处理", file=sys.stderr)
        result = _primitive_render_charter(source_path, target_path, render_context, previous_rendered_sha=previous_rendered_sha)
        result["distribution_id"] = dist_id
        result["kind"] = kind
        result["source_commit"] = version
        return result

    # 执行原语
    if operation == "copy_tree":
        filter_spec = dist.get("filter", {})
        filter_include = filter_spec.get("include") if filter_spec else None
        result = _primitive_copy_tree(source_path, target_path, force=force, filter_include=filter_include)
    elif operation == "render_config":
        # 未来扩展: 支持变量渲染
        variables = dist.get("variables", {})
        result = _primitive_render_config(source_path, target_path, variables, force=force)
    elif operation == "mint_credential":
        # 未来扩展: 支持凭据铸发
        result = _primitive_mint_credential("unknown", "unknown", [])
    else:
        result = {"ok": False, "error": f"Unknown operation: {operation}"}

    # 补充分发元信息
    result["distribution_id"] = dist_id
    result["kind"] = kind
    result["source_commit"] = version

    return result


def distribute_to_harness(
    target_harness_root: Path, role: str, *, force: bool = False,
    governance_root: Path | None = None, project: str | None = None,
) -> dict[str, Any]:
    """将工具/技能/契约分发到目标 harness 工位
    
    AIPOS-F66B 件①: 目标工位须已 enroll 且 .lybra/role.role == role(禁把别角色章程写进工位);
    工位实例项目段 ≠ 项目范围(project 显式, 缺省 = 工位自身项目)= 跳过 + 零写入 + 结果 skipped_workstation。
    charter 条目按工位治理根声明渲染(governance_root 显式 > connection.json#governance_root > home_root/<project>)。
    
    Args:
        target_harness_root: harness 根目录(如 ~/projects/kiwiai-pi/lybra-executor)
        role: 角色类别(内建或自定义角色名)
        force: 强制覆盖已存在的文件(非 charter)
        governance_root: 章程渲染用治理根(缺省按工位声明解析)
        project: 分发项目范围(缺省 = 工位自身项目)
    
    Returns:
        分发结果字典
    """
    from tools.aipos_cli.charter_render import (
        charter_render_context,
        resolve_workstation_governance_root,
        workstation_identity,
    )

    target_harness_root = Path(target_harness_root).expanduser().resolve()
    if not target_harness_root.exists():
        raise FileNotFoundError(f"Target harness not found: {target_harness_root}")

    # AIPOS-F66B 件①: 工位身份 + 角色一致 + 项目归属过滤(全部在任何写入之前)
    identity = workstation_identity(target_harness_root)
    if identity["role"] != str(role).strip():
        raise ValueError(
            f"目标工位 {target_harness_root} 的 .lybra/role.role={identity['role']!r} ≠ 请求角色 {role!r}: 拒绝分发(禁把别角色的分发物写进工位)"
        )
    scope = str(project).strip() if project else identity["project"]
    public_identity = {k: identity.get(k) for k in ("harness_root", "role", "instance", "project", "token_projects")}
    version = get_product_repo_version()
    if identity["project"] != scope:
        reason = f"非本项目工位: 实例 {identity['instance']} 项目段={identity['project']!r} ≠ 分发范围 {scope!r}, 跳过(零写入)"
        return {
            "ok": True, "skipped_workstation": True, "role": role, "version": version, "target": str(target_harness_root),
            "workstation": public_identity, "scope_project": scope, "reason": reason,
            "distributed": [], "skipped": [reason], "errors": [],
        }

    # 加载分发规格
    spec = load_distribution_spec()

    # 获取适用于该角色的所有分发条目 (AIPOS-F25 大项B: 传递产品仓根以解析自定义角色类)
    distributions = get_distributions_for_role(role, spec, project_root=REPO_ROOT)
    if not distributions:
        return {
            "ok": False,
            "role": role,
            "version": version,
            "target": str(target_harness_root),
            "error": f"No distributions found for role: {role}",
            "distributed": [],
            "skipped": [],
            "errors": [],
        }

    # AIPOS-F66B 件①: 章程渲染上下文(只在有 charter 条目时解析治理根; 解析不到 = 该条目拒, 其余照常)
    render_context: dict[str, Any] | None = None
    render_context_error: str | None = None
    if any(d.get("kind") == "charter" for d in distributions):
        try:
            gov = resolve_workstation_governance_root(identity, explicit=governance_root)
            render_context = charter_render_context(gov, identity=identity, product_commit=version)
        except (FileNotFoundError, ValueError) as exc:
            render_context_error = str(exc)

    # 执行所有分发
    results = {
        "ok": True,
        "role": role,
        "version": version,
        "target": str(target_harness_root),
        "workstation": public_identity,
        "scope_project": scope,
        "governance_root": render_context["governance_root"] if render_context else None,
        "distributed": [],
        "skipped": [],
        "errors": [],
        "declaration_gaps": [],
    }
    if render_context_error:
        results["errors"].append(f"charter render context unresolved: {render_context_error}")

    # 上次渲染物指纹(本地 manifest, 供声明缺口判据: 副本 == 上次渲染物 = 正常重渲染, ≠ = 本地手改)
    previous_rendered: dict[str, str] = {}
    prev_manifest_path = target_harness_root.parent / "_distributed" / f".version-{role}"
    if prev_manifest_path.is_file():
        try:
            prev_manifest = json.loads(prev_manifest_path.read_text(encoding="utf-8"))
            for rec in prev_manifest.get("distributions") or []:
                for f in rec.get("files") or []:
                    if f.get("rendered_sha256"):
                        previous_rendered[str(rec.get("distribution_id"))] = str(f["rendered_sha256"])
        except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            print(f"Warning: 本地 manifest {prev_manifest_path} 不可读, 章程按无指纹处理: {exc}", file=sys.stderr)

    distribution_records = []
    for dist in distributions:
        result = execute_distribution(
            dist, target_harness_root, force=force, version=version, render_context=render_context,
            previous_rendered_sha=previous_rendered.get(str(dist.get("distribution_id"))),
        )
        
        if result.get("action") == "render_charter" and result["ok"]:
            # AIPOS-F66B 件①: 章程渲染物(母本 + 声明); 本地改动 = 声明缺口出声
            state = "rendered" if result.get("written") else "unchanged"
            results["distributed"].append(
                f"{result['distribution_id']} (charter, {state}): {result.get('source', '?')} → {result.get('target', '?')}"
            )
            if result.get("declaration_gap"):
                results["declaration_gaps"].append({**result["declaration_gap"], "distribution_id": result["distribution_id"]})
            distribution_records.append({
                "distribution_id": result["distribution_id"],
                "kind": result["kind"],
                "source_commit": version,
                "target_path": result.get("target", "unknown"),
                "rendered": True,
                "files": [{"path": Path(result.get("target", "")).name, **result.get("fingerprints", {})}],
            })
        elif result["ok"]:
            results["distributed"].append(
                f"{result['distribution_id']} ({result['kind']}): {result.get('source', '?')} → {result.get('target', '?')}"
            )
            distribution_records.append({
                "distribution_id": result["distribution_id"],
                "kind": result["kind"],
                "source_commit": version,
                "target_path": result.get("target", "unknown"),
            })
        else:
            error_msg = result.get("error", "unknown error")
            if "exists" in error_msg.lower() and not force:
                results["skipped"].append(f"{result['distribution_id']}: {error_msg}")
            else:
                results["errors"].append(f"{result['distribution_id']}: {error_msg}")
                results["ok"] = False

    # 写入总manifest到_distributed/.version-{role}
    manifest_dir = target_harness_root.parent / "_distributed"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f".version-{role}"

    manifest_data = {
        "version": version,
        "role": role,
        "distributed_at": str(target_harness_root),
        "workstation": public_identity,
        "distributions": distribution_records,
    }

    manifest_result = _primitive_write_manifest(manifest_path, manifest_data)
    if not manifest_result["ok"]:
        results["errors"].append(f"Failed to write manifest: {manifest_result['error']}")
        results["ok"] = False

    return results


# ============================================================================
# CLI 入口
# ============================================================================


def main() -> int:
    """CLI 主入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Lybra 分发器 v2 — 规格驱动执行器 (AIPOS-CONN-LOOP-1 §4§5)"
    )
    parser.add_argument(
        "target", help="Target harness root (e.g., ~/projects/kiwiai-pi/lybra-executor)"
    )
    parser.add_argument("role", help="Role category (executor/auditor/advisor)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing files (非 charter)")
    parser.add_argument("--governance-root", default=None, help="AIPOS-F66B: 章程渲染用治理根(缺省按工位 connection.json#governance_root / home_root/<project> 解析)")
    parser.add_argument("--project", default=None, help="AIPOS-F66B: 分发项目范围(工位实例项目段不符 = 跳过零写入); 缺省 = 工位自身项目")
    parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    try:
        result = distribute_to_harness(
            Path(args.target), args.role, force=args.force,
            governance_root=Path(args.governance_root).expanduser() if args.governance_root else None,
            project=args.project,
        )
        
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            # 文本输出
            print(f"Distribution for role: {result['role']}")
            print(f"Version: {result['version']}")
            print(f"Target: {result['target']}")
            print()
            
            if result["distributed"]:
                print(f"✓ Distributed ({len(result['distributed'])}):")
                for item in result["distributed"]:
                    print(f"  - {item}")
            
            if result.get("skipped_workstation"):
                print(f"⊙ Workstation skipped: {result.get('reason')}")
            if result["skipped"]:
                print(f"\n⊙ Skipped ({len(result['skipped'])}):")
                for item in result["skipped"]:
                    print(f"  - {item}")
            for gap in result.get("declaration_gaps") or []:
                print(f"\n⚠ 声明缺口(工位本地改动已被渲染物覆盖, 须回流母本/声明): {gap['path']}")
                for line in str(gap.get("diff") or "").splitlines()[:40]:
                    print(f"    {line}")
            
            if result["errors"]:
                print(f"\n✗ Errors ({len(result['errors'])}):")
                for item in result["errors"]:
                    print(f"  - {item}")
        
        return 0 if result["ok"] and not result["errors"] else 1

    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
