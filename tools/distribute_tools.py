#!/usr/bin/env python3
"""AIPOS-R3: Lybra 工具包分发器

按角色类别将产品仓工具包下发到目标机 harness 挂载点。
实现 LOOP-REDESIGN v2 §4 gate统一分发机制。
"""
import os
import shutil
import json
from pathlib import Path
from typing import Optional

from tools.schema_loader import (
    get_role_tool_package,
    get_roles_with_tool_package,
    SchemaLoadError,
)

# 产品仓工具包路径
TOOLS_SOURCE = Path(__file__).parent.parent / "agents" / "pi"

# AIPOS-R4B-1: 角色到工具包的映射现从单一源 schema/roles.schema.json 读取
# (LOOP-REDESIGN v2 §5-6 角色注册表)。原硬编码 ROLE_TOOL_MAPPING 已删除。
# 新角色 = 注册表加一条 (含 tool_package)，本分发器零改即可分发。


def get_product_repo_version() -> str:
    """获取产品仓当前 commit hash 作为工具包版本"""
    import subprocess
    repo_root = Path(__file__).parent.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()[:12]  # 短 hash
    except subprocess.CalledProcessError:
        return "unknown"


def distribute_to_harness(
    target_harness_root: Path,
    role: str,
    force: bool = False
) -> dict:
    """
    将工具包分发到目标 harness 挂载点
    
    Args:
        target_harness_root: harness 根目录（如 ~/projects/kiwiai-pi/lybra-executor）
        role: 角色类别（executor/auditor/advisor）
        force: 强制覆盖已存在的文件
        
    Returns:
        分发结果字典
    """
    try:
        tool_spec = get_role_tool_package(role)
    except SchemaLoadError as e:
        raise ValueError(
            f"Role has no distributed tool package: {role}. "
            f"Roles with a tool package: {get_roles_with_tool_package()}"
        ) from e
    
    if not TOOLS_SOURCE.exists():
        raise FileNotFoundError(f"Tools source not found: {TOOLS_SOURCE}")
    
    target_harness_root = Path(target_harness_root).expanduser().resolve()
    if not target_harness_root.exists():
        raise FileNotFoundError(f"Target harness not found: {target_harness_root}")
    
    # 确定挂载点
    pi_dir = target_harness_root / ".pi"
    extensions_dir = pi_dir / "extensions"
    skills_dir = pi_dir / "skills"
    
    # 创建分发落点标记目录（与 _shared 平级，由分发器管理）
    distributed_dir = target_harness_root.parent / "_distributed"
    distributed_extensions = distributed_dir / "extensions"
    distributed_skills = distributed_dir / "skills"
    
    distributed_dir.mkdir(exist_ok=True)
    distributed_extensions.mkdir(exist_ok=True)
    distributed_skills.mkdir(exist_ok=True)
    
    version = get_product_repo_version()
    results = {
        "role": role,
        "version": version,
        "target": str(target_harness_root),
        "distributed": [],
        "skipped": [],
        "errors": []
    }
    
    # 分发 extensions
    for ext_name in tool_spec.get("extensions", []):
        src = TOOLS_SOURCE / "lybra-loop"  # 统一入口
        dst = distributed_extensions / "lybra-loop"
        
        try:
            if dst.exists():
                if not force:
                    results["skipped"].append(f"extensions/{ext_name} (already exists)")
                    continue
                shutil.rmtree(dst)
            
            shutil.copytree(src, dst)
            results["distributed"].append(f"extensions/{ext_name}")
        except Exception as e:
            results["errors"].append(f"extensions/{ext_name}: {e}")
    
    # 分发 skills
    for skill_name in tool_spec.get("skills", []):
        src = TOOLS_SOURCE / "skills" / skill_name
        dst = distributed_skills / skill_name
        
        if not src.exists():
            results["errors"].append(f"skills/{skill_name}: source not found")
            continue
        
        try:
            if dst.exists():
                if not force:
                    results["skipped"].append(f"skills/{skill_name} (already exists)")
                    continue
                shutil.rmtree(dst)
            
            shutil.copytree(src, dst)
            results["distributed"].append(f"skills/{skill_name}")
        except Exception as e:
            results["errors"].append(f"skills/{skill_name}: {e}")
    
    # 写入版本标记
    version_file = distributed_dir / f".version-{role}"
    version_file.write_text(json.dumps({
        "version": version,
        "role": role,
        "distributed_at": str(Path.cwd()),
        "spec": tool_spec
    }, indent=2))
    
    return results


if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Distribute Lybra tools to harness")
    parser.add_argument("target", help="Target harness root (e.g., ~/projects/kiwiai-pi/lybra-executor)")
    parser.add_argument("role", choices=get_roles_with_tool_package(), help="Role category")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing files")
    
    args = parser.parse_args()
    
    try:
        result = distribute_to_harness(Path(args.target), args.role, force=args.force)
        print(json.dumps(result, indent=2))
        
        if result["errors"]:
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)
