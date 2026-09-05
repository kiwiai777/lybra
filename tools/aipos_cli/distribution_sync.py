"""AIPOS-C4B 大项A③: `lybra sync` — 工位发起 pull 的单机分发闭环。

设计权威: DESIGN v2 §4(分发器 pull-over-单门)+ 本卡大项A。

红线:
- **工位发起 pull**, 禁任何 gate/顾问侧 ssh 推送; gate 被动。
- 只读落点 `_distributed/` = 生成物不入库; charter 写 harness 根 AGENTS.md。
- 范围按角色 scope: 只拉本角色应得件(gate 按 token role 过滤)。

流程: 连 gate → lybra_distribution_manifest(本角色清单) → 对比本地
_distributed 哈希 → 只拉差异(lybra_distribution_fetch, base64)→ 落盘 →
更新 .version-{role} manifest。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _discover_harness_root_from(start: Path) -> Path | None:
    """从给定目录向上找含 .lybra/ 的工位根(harness root)。

    仅供显式起点使用; 不再以 cwd 为默认起点(防裸跑猜错工位)。
    """
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for _ in range(10):
        if (cur / ".lybra").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _validate_enrolled(root: Path) -> None:
    """校验目标为已 enroll 工位(有 .lybra/role); 否则拒绝, 零写入。"""
    lybra_dir = root / ".lybra"
    role_file = lybra_dir / "role"
    if not lybra_dir.is_dir():
        raise ValueError(
            f"harness root '{root}' 没有 .lybra/ 目录 — 不是已注册工位, 拒绝写入。\n"
            f"  正确用法: lybra sync --harness-root <你的工位根>"
        )
    if not role_file.is_file():
        raise ValueError(
            f"harness root '{root}' 有 .lybra/ 但缺少 role 文件 — 未完成 enroll, 拒绝写入。\n"
            f"  先执行: lybra enroll --role <role> --harness-root '{root}'"
        )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_sync_context(
    *,
    harness_root: Path | None = None,
    gate_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """解析 sync 身份/连接(harness 根 → role → gate_url → token)。

    harness-root 解析序(禁按 cwd 猜):
      1. 显式参数 --harness-root
      2. 环境变量 LYBRA_HARNESS_ROOT
      3. 解析不到 → 出声报错退出(列出找过哪几层)

    无静默缺省: 解析不到即抛错(工位 .lybra 是身份单一真相)。
    """
    from tools.loop_context import ConnectionResolver

    tried: list[str] = []
    root: Path | None = None

    # 1. 显式参数
    if harness_root is not None:
        root = harness_root.resolve()
        tried.append(f"--harness-root={root}")
    else:
        tried.append("--harness-root=<未提供>")

    # 2. 环境变量
    if root is None:
        env_root = os.environ.get("LYBRA_HARNESS_ROOT")
        if env_root:
            root = Path(env_root).resolve()
            tried.append(f"LYBRA_HARNESS_ROOT={root}")
        else:
            tried.append("LYBRA_HARNESS_ROOT=<未设置>")

    # 3. 解析不到 → 出声
    if root is None:
        raise ValueError(
            "sync: 无法确定 harness-root(禁按 cwd 猜角色)。找过:\n"
            + "\n".join(f"  - {t}" for t in tried)
            + "\n  正确用法: lybra sync --harness-root <你的工位根>\n"
            + "  或设环境变量: LYBRA_HARNESS_ROOT=<你的工位根> lybra sync"
        )

    # 落盘前校验: 目标必须为已 enroll 工位(有 .lybra/role); 校验失败零写入
    _validate_enrolled(root)

    lybra_dir = root / ".lybra"

    identity = ConnectionResolver.resolve_identity(workspace_root=root)
    role = identity["role"]["value"]
    if not role:
        raise ValueError("cannot resolve role from .lybra (无静默缺省); 检查 .lybra/role")

    resolved_gate = gate_url or identity["gate_url"]["value"]
    if not resolved_gate:
        raise ValueError("cannot resolve gate_url from .lybra")
    resolved_gate = str(resolved_gate).rstrip("/mcp").rstrip("/")

    resolved_token = token or identity["token"]["value"]
    if not resolved_token:
        raise ValueError("cannot resolve token from .lybra connection.json")

    return {
        "harness_root": root,
        "role": role,
        "gate_url": resolved_gate,
        "token": str(resolved_token),
        "lybra_dir": lybra_dir,
    }


def _target_base_root(harness_root: Path, dist: dict[str, Any]) -> Path:
    """分发物落点基准: charter → harness 根; 其余 → harness 父目录 _distributed/。"""
    base = dist.get("target_base") or "harness_parent"
    return harness_root if base == "harness_root" else harness_root.parent


def _file_target_path(harness_root: Path, dist: dict[str, Any], file_rel: str) -> Path:
    """单个文件的完整落点路径。

    - 文件型分发物(source_is_file, 如 charter): target_path 就是完整文件路径, 文件
      直接落 target_path(单一文件, files[].path 只是源 basename)。
    - 目录型分发物(extension/skills/schema): 文件落 target_path/file_rel。
    """
    base = _target_base_root(harness_root, dist)
    if dist.get("source_is_file"):
        return base / dist.get("target_path", "")
    return base / dist.get("target_path", "") / file_rel


def compute_diffs(
    harness_root: Path,
    remote: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """对比本地与远端清单, 返回 [(dist, [path...])] 差异、应存在文件清单、应删除文件清单。

    本地状态 = 直接哈希目标落点上的文件(不依赖本地 manifest, 防陈旧)。
    
    AIPOS-F66C 件①: 按部署声明 prune — 返回应存在文件清单(declared_files)
    与应删除文件清单(to_prune)。声明剔除 = 盘面清除(落点+manifest 重生)。
    """
    to_fetch: list[dict[str, Any]] = []
    declared_files: set[str] = set()  # 所有声明中应存在的文件

    for dist in remote.get("distributions", []):
        need: list[str] = []
        for f in dist.get("files", []):
            rel = f["path"]
            local_path = _file_target_path(harness_root, dist, rel)
            declared_files.add(str(local_path))
            if not local_path.is_file():
                need.append(rel)
                continue
            try:
                if _sha256_file(local_path) != f.get("sha256"):
                    need.append(rel)
            except OSError:
                need.append(rel)
        if need:
            to_fetch.append({"dist": dist, "paths": need})

    # AIPOS-F66C 件①: 找出本地存在但声明中不存在的文件(应删除)
    to_prune = _find_files_to_prune(harness_root, declared_files)

    return to_fetch, list(declared_files), to_prune


def apply_fetch(
    harness_root: Path,
    dist: dict[str, Any],
    fetched_files: list[dict[str, Any]],
) -> int:
    """把 base64 内容写到分发落点, 返回写盘文件数。"""
    written = 0
    for f in fetched_files:
        rel = f["path"]
        data = base64.b64decode(f["content_b64"])
        # 校验哈希与清单一致(防传输损坏/投毒)
        expected = next(
            (x["sha256"] for x in dist.get("files", []) if x["path"] == rel), None
        )
        if expected and _sha256_bytes(data) != expected:
            raise ValueError(f"hash mismatch after fetch: {rel}")
        dest = _file_target_path(harness_root, dist, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written += 1
    return written


def write_local_manifest(harness_root: Path, remote: dict[str, Any]) -> Path:
    """写/更新 _distributed/.version-{role}(含文件哈希, 供连接器版本自答)。
    
    AIPOS-F66C 件①: manifest 由部署声明每次重生, 禁累积, 禁从陈旧 manifest 复活已剔除条目。
    本函数每次从 remote(当前部署声明) 完全重建 manifest, 不读取/合并旧 manifest。
    """
    role = remote.get("role", "unknown")
    manifest_dir = harness_root.parent / "_distributed"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f".version-{role}"

    # AIPOS-F66C 件①: manifest 每次从当前部署声明完全重生(禁累积旧条目)
    distributions = []
    for dist in remote.get("distributions", []):
        distributions.append({
            "distribution_id": dist.get("distribution_id"),
            "kind": dist.get("kind"),
            "source_commit": dist.get("source_commit"),
            "target_path": dist.get("target_path"),
            "files": [
                {"path": f["path"], "sha256": f["sha256"]}
                for f in dist.get("files", [])
            ],
        })

    data = {
        "version": remote.get("product_commit"),
        "role": role,
        "distributed_at": str(harness_root),
        "synced_at": _now_iso(),
        "distributions": distributions,
    }
    # 直接覆盖写入(不读取旧 manifest), 实现每次重生
    manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def _find_files_to_prune(harness_root: Path, declared_files: set[str]) -> list[str]:
    """AIPOS-F66C 件①-R3: 找出分发器曾铺过、但不在当前部署声明中的文件。
    
    P0 修复: prune删除集合 = 分发器自己铺过的产物(判据:manifest历史/文件头标记),
    **绝不能是"目录里凡不在声明的文件"** — 非分发文件(claim.ts等)一律不碰。
    
    作用域:
    - _distributed/ (共享分发落点,全部为分发产物)
    - .pi/extensions/ (wrapper目录,需检查文件头标记)
    - AGENTS.md (charter,分发产物)
    
    Returns:
        应删除的文件路径列表(绝对路径)
    """
    to_prune: list[str] = []
    
    # 1. _distributed/ 全部为分发产物,不在声明即prune
    distributed_dir = harness_root.parent / "_distributed"
    if distributed_dir.is_dir():
        for p in distributed_dir.rglob("*"):
            if p.is_file() and not p.name.startswith(".version-"):
                if str(p) not in declared_files:
                    to_prune.append(str(p))
    
    # 2. .pi/extensions/ 需区分分发wrapper vs 非分发文件(claim.ts等)
    # 判据: 读取本地manifest历史 + 文件头分发标记
    wrapper_dir = harness_root / ".pi" / "extensions"
    if wrapper_dir.is_dir():
        # 读取本地manifest获取历史分发过的wrapper列表
        historical_wrappers = _get_historical_distributed_files(harness_root)
        
        for p in wrapper_dir.rglob("*"):
            if p.is_file():
                path_str = str(p)
                if path_str not in declared_files:  # 不在当前声明
                    # 检查是否为分发器曾铺过的文件
                    if path_str in historical_wrappers or _is_distributed_file(p):
                        to_prune.append(path_str)
                    # 否则为非分发文件(claim.ts等),不碰
    
    # 3. AGENTS.md (charter) 为分发产物
    charter = harness_root / "AGENTS.md"
    if charter.is_file() and str(charter) not in declared_files:
        to_prune.append(str(charter))
    
    return to_prune


def _get_historical_distributed_files(harness_root: Path) -> set[str]:
    """AIPOS-F66C-R3: 从本地manifest读取历史分发过的文件列表。
    
    用于判断哪些文件是分发器铺的,避免误删非分发文件。
    """
    historical = set()
    role_file = harness_root / ".lybra" / "role"
    if not role_file.exists():
        return historical
    
    try:
        role_data = json.loads(role_file.read_text())
        role = role_data.get("role", "unknown")
    except Exception:
        return historical
    
    manifest_path = harness_root.parent / "_distributed" / f".version-{role}"
    if not manifest_path.exists():
        return historical
    
    try:
        manifest = json.loads(manifest_path.read_text())
        for dist in manifest.get("distributions", []):
            target_base = dist.get("target_base", "harness_root")
            target_path = dist.get("target_path", "")
            
            for f in dist.get("files", []):
                rel_path = f["path"]
                # 重建文件绝对路径
                if target_base == "harness_root":
                    if target_path:
                        full_path = harness_root / target_path
                    else:
                        full_path = harness_root / rel_path
                else:  # distributed_root
                    full_path = harness_root.parent / "_distributed" / target_path / rel_path
                
                historical.add(str(full_path))
        
    except Exception:
        pass  # manifest解析失败,返回空集合(fail-safe)
    
    return historical


def _is_distributed_file(file_path: Path) -> bool:
    """AIPOS-F66C-R3: 检查文件是否为分发产物(通过文件头标记判断)。
    
    分发器铺的wrapper文件头应含标记注释,如:
    // AIPOS-R3: 挂载包装指向分发落点
    // @lybra-distributed
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        # 检查前100行是否含分发标记
        lines = content.split("\n")[:100]
        for line in lines:
            if "@lybra-distributed" in line or "AIPOS-R3: 挂载包装" in line:
                return True
    except Exception:
        pass
    return False


def _prune_files(paths: list[str]) -> dict[str, Any]:
    """AIPOS-F66C 件①: 删除不在当前部署声明中的文件。
    
    返回删除结果统计。
    """
    pruned = []
    errors = []
    
    for p_str in paths:
        p = Path(p_str)
        try:
            if p.is_file():
                p.unlink()
                pruned.append(str(p))
                # 清理空目录(自底向上)
                parent = p.parent
                while parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
        except Exception as e:
            errors.append(f"{p}: {e}")
    
    return {
        "pruned_count": len(pruned),
        "pruned_files": pruned,
        "errors": errors,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def sync(*, harness_root: Path | None = None, gate_url: str | None = None, token: str | None = None) -> dict[str, Any]:
    """执行一次 sync。返回结构化结果。"""
    from tools.aipos_cli.confirm_client import GateClient

    ctx = resolve_sync_context(harness_root=harness_root, gate_url=gate_url, token=token)
    # 注: _validate_enrolled 已在 resolve_sync_context 内调用, 此处无需重复

    client = GateClient(ctx["gate_url"], ctx["token"])
    client.initialize()

    remote = client.call_tool("lybra_distribution_manifest", {})
    if not remote.get("ok"):
        return {"ok": False, "error": f"gate manifest not ok: {remote}", "role": ctx["role"]}

    diffs, declared_files, to_prune = compute_diffs(ctx["harness_root"], remote)

    # AIPOS-F66C 件①: prune 不在声明中的文件(声明剔除=盘面清除)
    prune_result = _prune_files(to_prune) if to_prune else {"pruned_count": 0, "pruned_files": [], "errors": []}

    fetched_total = 0
    results = []
    for item in diffs:
        dist = item["dist"]
        resp = client.call_tool("lybra_distribution_fetch", {
            "distribution_id": dist["distribution_id"],
            "paths": item["paths"],
        })
        if not resp.get("ok"):
            return {
                "ok": False,
                "error": f"fetch failed for {dist['distribution_id']}: {resp}",
                "role": ctx["role"],
            }
        files = resp.get("files") or []
        written = apply_fetch(ctx["harness_root"], dist, files)
        fetched_total += written
        results.append({
            "distribution_id": dist["distribution_id"],
            "files_written": written,
            "target_path": dist.get("target_path"),
        })

    manifest_path = write_local_manifest(ctx["harness_root"], remote)

    # AIPOS-F54 ⑪: 信封更换后的产品更新路径 —— sync 时按生效信封校正 .lybra/role#owner_policy_ref,
    # 顾问无须手写文件(推导不出/路径不可读则非致命告警, 不阻断分发)。
    policy_correction = _correct_owner_policy_ref(ctx["harness_root"], ctx["role"])

    return {
        "ok": True,
        "role": ctx["role"],
        "gate_url": ctx["gate_url"],
        "product_commit": remote.get("product_commit"),
        "harness_root": str(ctx["harness_root"]),
        "distributions_checked": len(remote.get("distributions", [])),
        "files_fetched": fetched_total,
        "files_pruned": prune_result["pruned_count"],
        "changes": results,
        "manifest_path": str(manifest_path),
        "declared_files": declared_files,
        "pruned_files": prune_result["pruned_files"],
        "prune_errors": prune_result["errors"],
        "owner_policy_correction": policy_correction,
    }


def _correct_owner_policy_ref(harness_root: Path, role: str) -> dict[str, Any]:
    """按当前生效信封校正 role#owner_policy_ref(信封更替后无须手写文件)。

    单源: connection.json#governance_root → 5_tasks/policies 信封工件; 判定复用
    workstation_wiring.derive_effective_owner_policy_ref。读不到治理根/推导不出 →
    非致命告警(sync 的本职是分发, 不因信封缺失阻断)。
    """
    import json as _json

    lybra_dir = harness_root / ".lybra"
    out: dict[str, Any] = {"checked": True}
    try:
        conn = _json.loads((lybra_dir / "connection.json").read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return {"checked": False, "note": "connection.json 不可读, 跳过信封校正"}
    gov_root = str(conn.get("governance_root") or "").strip() or None
    instance = None
    role_file = lybra_dir / "role"
    try:
        rd = _json.loads(role_file.read_text(encoding="utf-8"))
        instance = str(rd.get("instance") or "") or None
        current = str(rd.get("owner_policy_ref") or "") or None
    except (OSError, _json.JSONDecodeError):
        current = None

    from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref

    derived, reason = derive_effective_owner_policy_ref(gov_root, role=role, agent_instance=instance)
    out["derived"] = derived
    out["reason"] = reason
    if derived and derived != current:
        from tools.aipos_cli.enroll_client import write_role_file

        write_role_file(lybra_dir, role, instance, derived)
        out["updated"] = {"from": current, "to": derived}
    elif derived == current and derived:
        out["updated"] = None
        out["note"] = "已与生效信封一致"
    else:
        out["updated"] = None
        out["note"] = f"未推导出生效信封({reason}), 保留现值 {current}"
    return out


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AIPOS-C4B: lybra sync — worker-initiated distribution pull")
    parser.add_argument("--harness-root", default=None, help="Harness root (REQUIRED: no cwd guessing; fallback env LYBRA_HARNESS_ROOT)")
    parser.add_argument("--gate-url", default=None, help="Gate MCP URL (auto from .lybra if omitted)")
    parser.add_argument("--token", default=None, help="Bearer token (auto from .lybra connection.json if omitted)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    try:
        result = sync(
            harness_root=Path(args.harness_root) if args.harness_root else None,
            gate_url=args.gate_url,
            token=args.token,
        )
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print(f"Error: {e}")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("ok"):
            print(f"sync ok · role={result['role']} · product_commit={result['product_commit']}")
            print(f"  harness: {result['harness_root']}")
            print(f"  distributions checked: {result['distributions_checked']}, files fetched: {result['files_fetched']}, files pruned: {result.get('files_pruned', 0)}")
            for c in result.get("changes", []):
                print(f"  - {c['distribution_id']}: {c['files_written']} file(s) → {c['target_path']}")
            if result.get('pruned_files'):
                print(f"  pruned (不在声明): {len(result['pruned_files'])} file(s)")
                for pf in result['pruned_files'][:5]:  # 最多显示5个
                    print(f"    - {pf}")
                if len(result['pruned_files']) > 5:
                    print(f"    ... and {len(result['pruned_files']) - 5} more")
            print(f"  manifest: {result['manifest_path']}")
            print("  下一步: /reload 让新扩展/技能生效")
        else:
            print(f"sync failed: {result.get('error')}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
