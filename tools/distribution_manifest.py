#!/usr/bin/env python3
"""AIPOS-C4B 大项A: 分发清单(manifest)构建器 — 单一真相的机器可读快照。

设计权威: LOOP-REDESIGN v2 §4(分发器)+ 本卡大项A。

本模块是分发清单的**唯一实现**(一机制一实现)。它把 distribution.schema.json
声明的分发规格(每个分发物: 连接器 extensions / skills / charters / schema)
具体化为一份 manifest:每个分发物 = 文件列表 + 内容哈希(sha256)+ 源 commit。

消费方:
- 产品仓 deploy 时随发布目录生成 manifest.json(由 tools/lybra-deploy 调用)。
- gate 暴露拉取面(gate 被动): MCP 动词 lybra_distribution_manifest 读本模块。
- 工位侧 `lybra sync` 对比本地 _distributed 与清单, 拉取差异落盘(工位发起 pull)。

红线: 工位发起 pull; 禁任何 gate/顾问侧 ssh 推送。_distributed 保持生成物不入库。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tools.schema_loader import load_schema, SchemaLoadError

REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_VERSION = 1

# 分发物目标落点基准: charter 写到 harness 根目录(AGENTS.md), 其余写到
# harness 父目录的 _distributed/(生成物不入库)。单一规则, 分发器与 sync 同用。
def target_base_for_kind(kind: str) -> str:
    return "harness_root" if kind == "charter" else "harness_parent"


def get_product_commit(repo_root: Path | None = None) -> str:
    """产品仓当前 commit 短哈希(版本戳单一真相 = 源 commit)。

    部署快照(.deploy/current)无 .git/, 优先读 VERSION 文件的 git_commit;
    工作树(git 可查)读 git rev-parse; 两者都不可得 → unknown。
    """
    root = repo_root or REPO_ROOT
    # Priority 1: VERSION 文件(部署快照 ground truth)
    version_file = root / "VERSION"
    if version_file.is_file():
        try:
            text = version_file.read_text(encoding="utf-8")
            import re as _re
            m = _re.search(r"^git_commit:\s*([a-f0-9]{40})", text, _re.MULTILINE)
            if m:
                return m.group(1)[:12]
            m2 = _re.search(r"^git_short:\s*([a-f0-9]+)", text, _re.MULTILINE)
            if m2:
                return m2.group(1)[:12]
        except Exception:
            pass
    # Priority 2: git rev-parse(工作树 dev 模式)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def list_files_with_hashes(root: Path, *, include_filter: list[str] | None = None) -> list[dict[str, Any]]:
    """递归列出一个分发物源目录下的文件 + sha256 哈希(相对路径)。

    单一源路径解析: source.path 可能是文件(charter)或目录(extension/skills/schema)。
    """
    if not root.exists():
        raise FileNotFoundError(f"Distribution source not found: {root}")

    if root.is_file():
        return [{
            "path": root.name,
            "sha256": _sha256(root),
            "size": root.stat().st_size,
        }]

    entries: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if include_filter:
            # include 是技能子目录名列表(第一段路径名)
            top = rel.split("/", 1)[0]
            if top not in include_filter:
                continue
        entries.append({
            "path": rel,
            "sha256": _sha256(p),
            "size": p.stat().st_size,
        })
    return entries


def build_role_manifest(repo_root: Path, role: str) -> dict[str, Any]:
    """为一个角色构建分发清单(该角色应得件 + 文件列表 + 哈希 + 源 commit)。"""
    root = Path(repo_root)
    try:
        spec = load_schema("distribution", repo_root=root)
    except SchemaLoadError as e:
        raise ValueError(f"Failed to load distribution.schema.json: {e}") from e

    version = get_product_commit(root)
    distributions: list[dict[str, Any]] = []

    for dist in spec.get("distributions", []):
        if role not in dist.get("applies_to_roles", []):
            continue
        dist_id = dist.get("distribution_id", "unknown")
        kind = dist.get("kind", "unknown")
        source_path = dist.get("source", {}).get("path", "")
        target_rel = dist.get("target", {}).get("relative_path", "")
        target_harness = dist.get("target", {}).get("harness", "pi")
        include_filter = (dist.get("filter") or {}).get("include")

        src = root / source_path
        if not src.exists():
            raise FileNotFoundError(
                f"Distribution {dist_id}: source not found at {source_path}"
            )

        source_is_file = src.is_file()
        files = list_files_with_hashes(src, include_filter=include_filter)
        distributions.append({
            "distribution_id": dist_id,
            "kind": kind,
            "source_commit": version,
            "source_path": source_path,
            "source_is_file": source_is_file,
            "target_harness": target_harness,
            "target_base": target_base_for_kind(kind),
            "target_path": target_rel,
            "files": files,
        })

    if not distributions:
        raise ValueError(f"No distributions found for role: {role}")

    return {
        "role": role,
        "harness": "pi",
        "distributions": distributions,
    }


def build_full_manifest(repo_root: Path | None = None) -> dict[str, Any]:
    """构建全角色分发清单(单机分发闭环的机器可读真相)。"""
    root = repo_root or REPO_ROOT
    spec = load_schema("distribution", repo_root=root)
    roles = sorted({r for d in spec.get("distributions", []) for r in d.get("applies_to_roles", [])})

    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "product_commit": get_product_commit(root),
        "roles": {},
    }
    for role in roles:
        try:
            manifest["roles"][role] = build_role_manifest(root, role)
        except FileNotFoundError:
            # 某角色的某分发物源缺失: 该角色跳过(缺件由 build 时暴露/审计)
            continue
    return manifest


def write_manifest(repo_root: Path | None = None, output_path: Path | None = None) -> dict[str, Any]:
    """写 manifest.json 到指定位置(默认 <repo_root>/dist/manifest.json)。"""
    root = repo_root or REPO_ROOT
    manifest = build_full_manifest(root)
    target = output_path or (root / "dist" / "manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(target), "manifest": manifest}


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="AIPOS-C4B: build distribution manifest")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Product repo root")
    parser.add_argument("--out", default=None, help="Output manifest.json path (default <repo>/dist/manifest.json)")
    parser.add_argument("--role", default=None, help="Build manifest for a single role only")
    args = parser.parse_args()

    root = Path(args.repo_root)
    try:
        if args.role:
            data = build_role_manifest(root, args.role)
        else:
            data = build_full_manifest(root)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"manifest written: {out}")
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
