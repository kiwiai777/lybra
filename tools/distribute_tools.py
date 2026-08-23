#!/usr/bin/env python3
"""AIPOS-CONN-LOOP-1 §4§5: Lybra 分发器 v2 — 规格驱动的泛化执行器

按 distribution.schema.json 声明的规格分发工具/技能/契约到 harness 工位。
实现 LOOP-REDESIGN v2 §4 分发规格底座: distribution WHAT = 数据(schema),
HOW = 固定原语(本引擎)。新增分发条目 = 加数据零改代码。

设计决策(§4): 明确不做代码插件 — 插件=一机制一实现的旁路+供应链面难审;
真正新原语=产品仓一张卡过审计。本引擎只实现固定原语集。
"""
import json
import shutil
import subprocess
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
# 固定分发原语(LOOP-REDESIGN v2 §4 — 仅此四种,禁插件/自定义代码)
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


def execute_distribution(
    dist: dict[str, Any], target_harness_root: Path, *, force: bool = False, version: str = "unknown"
) -> dict[str, Any]:
    """执行单个分发条目
    
    Args:
        dist: distribution条目(来自schema)
        target_harness_root: 目标harness根目录
        force: 强制覆盖
        version: 源版本(git commit hash)
    
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


def distribute_to_harness(target_harness_root: Path, role: str, *, force: bool = False) -> dict[str, Any]:
    """将工具/技能/契约分发到目标 harness 工位
    
    Args:
        target_harness_root: harness 根目录(如 ~/projects/kiwiai-pi/lybra-executor)
        role: 角色类别(内建或自定义角色名)
        force: 强制覆盖已存在的文件
    
    Returns:
        分发结果字典
    """
    target_harness_root = Path(target_harness_root).expanduser().resolve()
    if not target_harness_root.exists():
        raise FileNotFoundError(f"Target harness not found: {target_harness_root}")

    # 加载分发规格
    spec = load_distribution_spec()
    version = get_product_repo_version()

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

    # 执行所有分发
    results = {
        "ok": True,
        "role": role,
        "version": version,
        "target": str(target_harness_root),
        "distributed": [],
        "skipped": [],
        "errors": [],
    }

    distribution_records = []
    for dist in distributions:
        result = execute_distribution(dist, target_harness_root, force=force, version=version)
        
        if result["ok"]:
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
    parser.add_argument("--force", action="store_true", help="Force overwrite existing files")
    parser.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    try:
        result = distribute_to_harness(Path(args.target), args.role, force=args.force)
        
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
            
            if result["skipped"]:
                print(f"\n⊙ Skipped ({len(result['skipped'])}):")
                for item in result["skipped"]:
                    print(f"  - {item}")
            
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
