"""AIPOS-C4B 大项A③: `lybra sync` — 工位发起 pull 的单机分发闭环。

设计权威: DESIGN v2 §4(分发器 pull-over-单门)+ 本卡大项A。

红线:
- **工位发起 pull**, 禁任何 gate/顾问侧 ssh 推送; gate 被动。
- 只读落点 `_distributed/` = 生成物不入库; charter 写 harness 根 AGENTS.md。
- 范围按角色 scope: 只拉本角色应得件(gate 按 token role 过滤)。

流程: 连 gate → lybra_distribution_manifest(本角色清单) → 对比本地
_distributed 哈希 → 只拉差异(lybra_distribution_fetch, base64)→ 落盘 →
更新 .version-{role} manifest。

AIPOS-F66B 件①(2026-09-05 chris hbj-coder 被写入 lybra 执行体章程的根治):
- **工位项目归属过滤**: 目标工位按其 `.lybra/role` 实例的项目段(naming_profile.parse_instance_name 唯一解析)与本次 sync
  的项目范围(--project / --workspace-root 的 project.json / 单工位时=工位自身项目)比对, 不符 = 跳过, 零写入,
  manifest 记 skipped。`--harness-root` 可给工位父根(kiwiai-pi 根): 逐工位判归属, 只 sync 本项目工位。
- **章程 = 声明渲染物**(seed_only 退役): charter 分发物经 charter_render.render_charter(母本 + 项目声明 + 写权限边界)
  写工位副本; manifest 记 source_sha256 / render_context_sha256 / rendered_sha256; 母本变 / 声明变 = 重渲染;
  工位本地改动 = 声明缺口(覆盖前报 diff, 须回流母本或声明)。
- `--dry-run`: 零写入, 列出 would-fetch / would-render / would-prune / skipped。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

SYNC_RUN_MANIFEST = ".version-sync"  # 多工位 sync 运行清单(含 skipped), `.version-` 前缀 = prune 不碰


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
        # AIPOS-F81: 全 retired 等 fail-closed 拒因(带重签出口)原样带出, token 值永不上屏
        detail = identity["token"].get("error")
        raise ValueError(
            "cannot resolve token from .lybra connection.json" + (f": {detail}" if detail else "")
        )

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


# ---------------------------------------------------------------------------
# AIPOS-F66B 件①: 本地 manifest 读取 + 章程渲染态判定
# ---------------------------------------------------------------------------

def _local_manifest_path(harness_root: Path, role: str) -> Path:
    return harness_root.parent / "_distributed" / f".version-{role}"


def _load_local_manifest(harness_root: Path, role: str) -> dict[str, Any]:
    """读本地 .version-{role}(不存在 = {}; 坏 JSON = 出声 warning + {}, 不吞)。"""
    path = _local_manifest_path(harness_root, role)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: 本地 manifest {path} 不可读, 视为无记录(将重渲染章程): {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _recorded_file(local_manifest: dict[str, Any], dist_id: str, rel: str) -> dict[str, Any] | None:
    for d in local_manifest.get("distributions") or []:
        if d.get("distribution_id") != dist_id:
            continue
        for f in d.get("files") or []:
            if f.get("path") == rel:
                return f
    return None


def _is_charter(dist: dict[str, Any]) -> bool:
    return str(dist.get("kind") or "") == "charter"


def _charter_render_reason(
    local_path: Path,
    remote_file: dict[str, Any],
    recorded: dict[str, Any] | None,
    render_context_sha: str,
) -> str | None:
    """章程是否需重渲染(None = 已最新)。判据顺序: 缺 → 无渲染指纹(seed_only 时代副本)→ 母本变 → 声明变 → 工位本地改动。"""
    if not local_path.is_file():
        return "missing"
    if not recorded or not recorded.get("rendered_sha256"):
        return "unrendered(无渲染指纹: seed_only 时代副本或首次渲染)"
    if recorded.get("sha256") != remote_file.get("sha256"):
        return "master_changed(母本变)"
    if recorded.get("render_context_sha256") != render_context_sha:
        return "declaration_changed(项目/角色声明变)"
    try:
        local_sha = _sha256_file(local_path)
    except OSError as exc:
        return f"unreadable({exc})"
    if local_sha != recorded["rendered_sha256"]:
        return "local_edit(工位本地改动 = 声明缺口, 将覆盖并报 diff)"
    return None


def compute_diffs(
    harness_root: Path,
    remote: dict[str, Any],
    *,
    render_context: dict[str, Any] | None = None,
    role: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """对比本地与远端清单, 返回 [(dist, [path...])] 差异、应存在文件清单、应删除文件清单。

    本地状态 = 直接哈希目标落点上的文件(不依赖本地 manifest, 防陈旧)。

    AIPOS-F66C 件①: 按部署声明 prune — 返回应存在文件清单(declared_files)
    与应删除文件清单(to_prune)。声明剔除 = 盘面清除(落点+manifest 重生)。

    AIPOS-F66B 件①: 给了 render_context 时, charter 分发物按渲染态判(本地 manifest 三指纹: 母本 sha / 声明指纹 / 渲染物 sha),
    差异项带 reasons{path: 原因}; 未给 render_context = 旧的字节比对(纯函数夹具兼容)。
    """
    from tools.aipos_cli.charter_render import render_context_fingerprint

    to_fetch: list[dict[str, Any]] = []
    declared_files: set[str] = set()  # 所有声明中应存在的文件
    local_manifest = _load_local_manifest(harness_root, role) if (render_context is not None and role) else {}
    ctx_sha = render_context_fingerprint(render_context) if render_context is not None else ""

    for dist in remote.get("distributions", []):
        need: list[str] = []
        reasons: dict[str, str] = {}
        for f in dist.get("files", []):
            rel = f["path"]
            local_path = _file_target_path(harness_root, dist, rel)
            declared_files.add(str(local_path))
            if render_context is not None and _is_charter(dist):
                reason = _charter_render_reason(local_path, f, _recorded_file(local_manifest, dist.get("distribution_id", ""), rel), ctx_sha)
                if reason:
                    need.append(rel)
                    reasons[rel] = reason
                continue
            if not local_path.is_file():
                need.append(rel)
                reasons[rel] = "missing"
                continue
            try:
                if _sha256_file(local_path) != f.get("sha256"):
                    need.append(rel)
                    reasons[rel] = "changed"
            except OSError as exc:
                need.append(rel)
                reasons[rel] = f"unreadable({exc})"
        if need:
            to_fetch.append({"dist": dist, "paths": need, "reasons": reasons})

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


def apply_charter_render(
    harness_root: Path,
    dist: dict[str, Any],
    fetched_files: list[dict[str, Any]],
    render_context: dict[str, Any],
    *,
    reasons: dict[str, str] | None = None,
    previous_rendered: dict[str, str] | None = None,
) -> dict[str, Any]:
    """AIPOS-F66B 件①: 章程 = 母本(哈希核验)+ 声明 → 渲染物写工位副本。

    声明缺口判据: 工位副本 sha256 ≠ 上次渲染物 sha256(previous_rendered[rel], 来自本地 manifest)且 ≠ 本次渲染物
    → 工位本地改动, 覆盖前记 unified diff(工位本地 vs 渲染物); 副本 == 上次渲染物(母本/声明变的正常重渲染)不算缺口;
    无上次记录(seed_only 时代副本)且 ≠ 渲染物 = 缺口(出声, 让旧副本上的手改回流)。
    返回 {written, files: {rel: fingerprints}, declaration_gaps: [{path, reason, diff}]}。
    """
    from tools.aipos_cli.charter_render import charter_fingerprints, local_edit_diff, render_charter

    out: dict[str, Any] = {"written": 0, "files": {}, "declaration_gaps": []}
    for f in fetched_files:
        rel = f["path"]
        data = base64.b64decode(f["content_b64"])
        expected = next((x["sha256"] for x in dist.get("files", []) if x["path"] == rel), None)
        if expected and _sha256_bytes(data) != expected:
            raise ValueError(f"hash mismatch after fetch: {rel}")
        master_text = data.decode("utf-8")
        rendered = render_charter(master_text, render_context)
        dest = _file_target_path(harness_root, dist, rel)
        if dest.is_file():
            local_text = dest.read_text(encoding="utf-8", errors="replace")
            prev_sha = (previous_rendered or {}).get(rel)
            local_is_previous_render = bool(prev_sha) and _sha256_file(dest) == prev_sha
            if local_text != rendered and not local_is_previous_render:
                out["declaration_gaps"].append({
                    "path": str(dest),
                    "reason": (reasons or {}).get(rel, "differs"),
                    "diff": local_edit_diff(rendered, local_text, dest),
                })
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        out["written"] += 1
        out["files"][rel] = charter_fingerprints(master_text, render_context, rendered)
    return out


def write_local_manifest(
    harness_root: Path,
    remote: dict[str, Any],
    *,
    charter_records: dict[str, dict[str, dict[str, str]]] | None = None,
    workstation: dict[str, Any] | None = None,
) -> Path:
    """写/更新 _distributed/.version-{role}(含文件哈希, 供连接器版本自答)。

    AIPOS-F66C 件①: manifest 由部署声明每次重生, 禁累积, 禁从陈旧 manifest 复活已剔除条目。
    本函数每次从 remote(当前部署声明) 完全重建 manifest, 不读取/合并旧 manifest。
    AIPOS-F66B 件①: charter 文件附三指纹(charter_records[dist_id][rel] = {source_sha256, render_context_sha256, rendered_sha256});
    workstation = 工位归属(instance/project)。
    """
    role = remote.get("role", "unknown")
    manifest_dir = harness_root.parent / "_distributed"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f".version-{role}"

    # AIPOS-F66C 件①: manifest 每次从当前部署声明完全重生(禁累积旧条目)
    distributions = []
    for dist in remote.get("distributions", []):
        dist_id = dist.get("distribution_id")
        files = []
        for f in dist.get("files", []):
            entry: dict[str, Any] = {"path": f["path"], "sha256": f["sha256"]}
            fp = ((charter_records or {}).get(str(dist_id)) or {}).get(f["path"])
            if fp:
                entry["render_context_sha256"] = fp.get("render_context_sha256")
                entry["rendered_sha256"] = fp.get("rendered_sha256")
            files.append(entry)
        record: dict[str, Any] = {
            "distribution_id": dist_id,
            "kind": dist.get("kind"),
            "source_commit": dist.get("source_commit"),
            "target_path": dist.get("target_path"),
            "files": files,
        }
        if _is_charter(dist):
            record["rendered"] = bool((charter_records or {}).get(str(dist_id)))
        distributions.append(record)

    data = {
        "version": remote.get("product_commit"),
        "role": role,
        "distributed_at": str(harness_root),
        "synced_at": _now_iso(),
        "distributions": distributions,
    }
    if workstation:
        data["workstation"] = {k: workstation.get(k) for k in ("instance", "project", "role", "harness_root")}
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
    manifest/role 不可读 = 出声 warning + 空集合(fail-safe, 不静默吞)。
    """
    historical: set[str] = set()
    role_file = harness_root / ".lybra" / "role"
    if not role_file.exists():
        return historical

    try:
        role_data = json.loads(role_file.read_text())
        role = role_data.get("role", "unknown")
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        print(f"Warning: {role_file} 不可读, prune 历史集合为空: {exc}", file=sys.stderr)
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

    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        print(f"Warning: 本地 manifest {manifest_path} 解析失败, prune 历史集合为空: {exc}", file=sys.stderr)

    return historical


def _is_distributed_file(file_path: Path) -> bool:
    """AIPOS-F66C-R3: 检查文件是否为分发产物(通过文件头标记判断)。

    分发器铺的wrapper文件头应含标记注释,如:
    // AIPOS-R3: 挂载包装指向分发落点
    // @lybra-distributed
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    # 检查前100行是否含分发标记
    lines = content.split("\n")[:100]
    for line in lines:
        if "@lybra-distributed" in line or "AIPOS-R3: 挂载包装" in line:
            return True
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
        except OSError as e:
            errors.append(f"{p}: {e}")

    return {
        "pruned_count": len(pruned),
        "pruned_files": pruned,
        "errors": errors,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# AIPOS-F66B 件①: 工位归属 + 单工位 sync(含 dry-run / 章程渲染)
# ---------------------------------------------------------------------------

def _public_identity(identity: dict[str, Any]) -> dict[str, Any]:
    return {k: identity.get(k) for k in ("harness_root", "role", "instance", "project", "token_projects", "governance_root_declared")}


def discover_workstations(root: Path) -> list[Path]:
    """`root` 本身是工位(有 .lybra/role)→ [root]; 否则取其直接子目录中已 enroll 的工位(排序)。都不是 = ValueError。"""
    root = Path(root).expanduser().resolve()
    if (root / ".lybra" / "role").is_file():
        return [root]
    found = sorted(p for p in root.iterdir() if p.is_dir() and (p / ".lybra" / "role").is_file()) if root.is_dir() else []
    if not found:
        raise ValueError(
            f"--harness-root {root}: 既不是已 enroll 工位(无 .lybra/role), 其直接子目录也无已 enroll 工位。"
            "\n  正确用法: lybra sync --harness-root <工位根 或 工位父根>"
        )
    return found


def _scope_project(*, project: str | None, governance_root: Path | None) -> str | None:
    """sync 项目范围: --project 显式 > --workspace-root 的 project.json project > None(单工位时=工位自身项目)。"""
    if project:
        return str(project).strip()
    if governance_root is not None:
        from tools.aipos_cli.workspace_config import read_project_json

        name = str(read_project_json(Path(governance_root)).get("project") or "").strip()
        if not name:
            raise ValueError(f"--workspace-root {governance_root}/project.json 缺 project 字段, 无法确定 sync 项目范围")
        return name
    return None


def sync(
    *,
    harness_root: Path | None = None,
    gate_url: str | None = None,
    token: str | None = None,
    governance_root: Path | None = None,
    project: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """执行一次单工位 sync。返回结构化结果。

    AIPOS-F66B 件①: 先判工位项目归属(project 显式范围 ≠ 工位实例项目段 → status=skipped, 零写入, 不连门);
    charter 走 apply_charter_render(母本 + 声明渲染), manifest 记三指纹; dry_run 零写入只列计划。
    """
    from tools.aipos_cli.charter_render import (
        charter_render_context,
        resolve_workstation_governance_root,
        workstation_identity,
    )
    from tools.aipos_cli.confirm_client import GateClient

    root = (harness_root or Path(os.environ.get("LYBRA_HARNESS_ROOT") or "")).expanduser().resolve() if (harness_root or os.environ.get("LYBRA_HARNESS_ROOT")) else None
    if root is None:
        # 走既有解析序出声(列出找过哪几层)
        resolve_sync_context(harness_root=None, gate_url=gate_url, token=token)
        raise AssertionError("unreachable")
    identity = workstation_identity(root)  # 缺 .lybra/role = WorkstationIdentityError(fail-closed)
    scope = _scope_project(project=project, governance_root=governance_root) or identity["project"]
    if identity["project"] != scope:
        return {
            "ok": True,
            "status": "skipped",
            "role": identity["role"],
            "harness_root": str(root),
            "workstation": _public_identity(identity),
            "scope_project": scope,
            "reason": f"非本项目工位: 实例 {identity['instance']} 项目段={identity['project']!r} ≠ sync 范围 {scope!r}, 跳过(零写入, 未连门)",
            "dry_run": dry_run,
            "files_fetched": 0,
            "files_pruned": 0,
            "changes": [],
        }

    ctx = resolve_sync_context(harness_root=root, gate_url=gate_url, token=token)
    # 注: _validate_enrolled 已在 resolve_sync_context 内调用, 此处无需重复

    client = GateClient(ctx["gate_url"], ctx["token"])
    client.initialize()

    remote = client.call_tool("lybra_distribution_manifest", {})
    if not remote.get("ok"):
        return {"ok": False, "error": f"gate manifest not ok: {remote}", "role": ctx["role"]}

    # AIPOS-F66B 件①: 章程渲染上下文(只在清单含 charter 时解析治理根; 解析不到 = fail-closed 出声)
    render_ctx: dict[str, Any] | None = None
    if any(_is_charter(d) for d in remote.get("distributions", [])):
        gov = resolve_workstation_governance_root(identity, explicit=governance_root)
        render_ctx = charter_render_context(gov, identity=identity, product_commit=str(remote.get("product_commit") or "unknown"))

    diffs, declared_files, to_prune = compute_diffs(ctx["harness_root"], remote, render_context=render_ctx, role=ctx["role"])

    plan = []
    for item in diffs:
        dist = item["dist"]
        plan.append({
            "distribution_id": dist["distribution_id"],
            "kind": dist.get("kind"),
            "action": "would-render" if _is_charter(dist) and render_ctx is not None else "would-fetch",
            "paths": list(item["paths"]),
            "reasons": dict(item.get("reasons") or {}),
            "target_path": str(_file_target_path(ctx["harness_root"], dist, item["paths"][0])) if dist.get("source_is_file") else dist.get("target_path"),
        })
    base_result: dict[str, Any] = {
        "ok": True,
        "status": "dry-run" if dry_run else "synced",
        "role": ctx["role"],
        "gate_url": ctx["gate_url"],
        "product_commit": remote.get("product_commit"),
        "harness_root": str(ctx["harness_root"]),
        "workstation": _public_identity(identity),
        "scope_project": scope,
        "governance_root": render_ctx["governance_root"] if render_ctx else None,
        "distributions_checked": len(remote.get("distributions", [])),
        "dry_run": dry_run,
        "plan": plan,
        "would_prune": list(to_prune),
        "declared_files": declared_files,
    }
    if dry_run:
        return {**base_result, "files_fetched": 0, "files_pruned": 0, "changes": [], "pruned_files": [], "prune_errors": [],
                "declaration_gaps": [], "manifest_path": str(_local_manifest_path(ctx["harness_root"], ctx["role"])),
                "owner_policy_correction": {"checked": False, "note": "dry-run 零写入"}}

    # AIPOS-F66C 件①: prune 不在声明中的文件(声明剔除=盘面清除)
    prune_result = _prune_files(to_prune) if to_prune else {"pruned_count": 0, "pruned_files": [], "errors": []}

    fetched_total = 0
    results = []
    declaration_gaps: list[dict[str, Any]] = []
    charter_records: dict[str, dict[str, dict[str, str]]] = {}
    # 未变的 charter 文件: 沿用本地 manifest 三指纹(否则下次会被当作无指纹重渲染)
    if render_ctx is not None:
        local_manifest = _load_local_manifest(ctx["harness_root"], ctx["role"])
        for dist in remote.get("distributions", []):
            if not _is_charter(dist):
                continue
            for f in dist.get("files", []):
                rec = _recorded_file(local_manifest, dist.get("distribution_id", ""), f["path"])
                if rec and rec.get("rendered_sha256"):
                    charter_records.setdefault(str(dist.get("distribution_id")), {})[f["path"]] = {
                        "source_sha256": rec.get("sha256", ""),
                        "render_context_sha256": rec.get("render_context_sha256", ""),
                        "rendered_sha256": rec.get("rendered_sha256", ""),
                    }
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
        if _is_charter(dist) and render_ctx is not None:
            prev = {rel: fp.get("rendered_sha256", "") for rel, fp in (charter_records.get(str(dist["distribution_id"])) or {}).items()}
            rendered = apply_charter_render(ctx["harness_root"], dist, files, render_ctx, reasons=item.get("reasons"), previous_rendered=prev)
            written = rendered["written"]
            charter_records.setdefault(str(dist["distribution_id"]), {}).update(rendered["files"])
            declaration_gaps.extend(rendered["declaration_gaps"])
            action = "rendered"
        else:
            written = apply_fetch(ctx["harness_root"], dist, files)
            action = "fetched"
        fetched_total += written
        results.append({
            "distribution_id": dist["distribution_id"],
            "action": action,
            "files_written": written,
            "reasons": dict(item.get("reasons") or {}),
            "target_path": dist.get("target_path"),
        })

    manifest_path = write_local_manifest(ctx["harness_root"], remote, charter_records=charter_records or None, workstation=identity)

    # AIPOS-F54 ⑪: 信封更换后的产品更新路径 —— sync 时按生效信封校正 .lybra/role#owner_policy_ref,
    # 顾问无须手写文件(推导不出/路径不可读则非致命告警, 不阻断分发)。
    policy_correction = _correct_owner_policy_ref(ctx["harness_root"], ctx["role"])

    return {
        **base_result,
        "files_fetched": fetched_total,
        "files_pruned": prune_result["pruned_count"],
        "changes": results,
        "declaration_gaps": declaration_gaps,
        "manifest_path": str(manifest_path),
        "pruned_files": prune_result["pruned_files"],
        "prune_errors": prune_result["errors"],
        "owner_policy_correction": policy_correction,
    }


def sync_many(
    harness_root: Path,
    *,
    governance_root: Path | None = None,
    project: str | None = None,
    dry_run: bool = False,
    gate_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F66B 件①: 工位根或工位父根 → 逐工位按项目归属过滤后 sync。

    多工位时项目范围必须显式(--project 或 --workspace-root), 禁猜; 单工位缺省 = 工位自身项目。
    每工位独立 fail-closed(一工位出错不阻断其余, 总 ok = 无错误); 非 dry-run 写运行清单 <父根>/_distributed/.version-sync(含 skipped)。
    """
    from tools.aipos_cli.charter_render import WorkstationIdentityError, workstation_identity

    root = Path(harness_root).expanduser().resolve()
    workstations = discover_workstations(root)
    multi = len(workstations) > 1 or workstations[0] != root
    scope = _scope_project(project=project, governance_root=governance_root)
    if multi and not scope:
        raise ValueError(
            f"--harness-root {root} 下有 {len(workstations)} 个工位, 多工位 sync 必须显式项目范围(禁猜): "
            "--project <name> 或 --workspace-root <该项目治理根>"
        )
    entries: list[dict[str, Any]] = []
    for ws in workstations:
        try:
            identity = workstation_identity(ws)
        except WorkstationIdentityError as exc:
            entries.append({"harness_root": str(ws), "status": "error", "reason": str(exc)})
            continue
        try:
            result = sync(harness_root=ws, gate_url=gate_url, token=token, governance_root=governance_root, project=scope, dry_run=dry_run)
        except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
            entries.append({"harness_root": str(ws), "status": "error", "workstation": _public_identity(identity), "reason": f"{exc.__class__.__name__}: {exc}"})
            continue
        if not result.get("ok"):
            entries.append({"harness_root": str(ws), "status": "error", "workstation": _public_identity(identity), "reason": str(result.get("error")), "result": result})
            continue
        entries.append({"harness_root": str(ws), "status": result.get("status"), "workstation": _public_identity(identity),
                        "reason": result.get("reason"), "result": result})
    run = {
        "ok": all(e["status"] != "error" for e in entries),
        "mode": "multi" if multi else "single",
        "harness_root": str(root),
        "scope_project": scope or (entries[0].get("workstation") or {}).get("project") if entries else scope,
        "dry_run": dry_run,
        "workstations": entries,
        "skipped": [e for e in entries if e["status"] == "skipped"],
        "synced_at": _now_iso(),
    }
    if multi and not dry_run:
        manifest_dir = root / "_distributed"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        run_manifest = manifest_dir / SYNC_RUN_MANIFEST
        run_manifest.write_text(json.dumps(
            {k: v for k, v in run.items() if k != "workstations"} | {
                "workstations": [{k: v for k, v in e.items() if k != "result"} for e in entries],
            }, indent=2, ensure_ascii=False), encoding="utf-8")
        run["run_manifest_path"] = str(run_manifest)
    return run


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


# ---------------------------------------------------------------------------
# CLI 薄壳(lybra sync 与 python -m tools.aipos_cli.distribution_sync 同一入口)
# ---------------------------------------------------------------------------

def render_sync_text(run: dict[str, Any]) -> str:
    lines = [f"sync {'dry-run' if run.get('dry_run') else 'run'} · mode={run.get('mode')} · scope_project={run.get('scope_project')} · harness_root={run.get('harness_root')}"]
    for e in run.get("workstations", []):
        ws = e.get("workstation") or {}
        head = f"  - {Path(e['harness_root']).name}: {e['status']}"
        if ws:
            head += f" (role={ws.get('role')} instance={ws.get('instance')} project={ws.get('project')})"
        lines.append(head)
        if e.get("reason") and e["status"] in ("skipped", "error"):
            lines.append(f"      {e['reason']}")
        r = e.get("result") or {}
        if r and e["status"] in ("dry-run", "synced"):
            lines.append(f"      product_commit={r.get('product_commit')} distributions_checked={r.get('distributions_checked')} governance_root={r.get('governance_root')}")
            for p in r.get("plan") or []:
                lines.append(f"      {p['action']}: {p['distribution_id']} ({p['kind']}) {len(p['paths'])} file(s) → {p['target_path']}")
                for rel, why in (p.get("reasons") or {}).items():
                    lines.append(f"          {rel}: {why}")
            if not r.get("plan"):
                lines.append("      up-to-date: 0 file(s) to fetch/render")
            if r.get("would_prune"):
                lines.append(f"      would-prune (不在声明): {len(r['would_prune'])} file(s)")
                for pf in r["would_prune"][:5]:
                    lines.append(f"          - {pf}")
            if e["status"] == "synced":
                lines.append(f"      files fetched/rendered: {r.get('files_fetched')}, files pruned: {r.get('files_pruned')}, manifest: {r.get('manifest_path')}")
                for gap in r.get("declaration_gaps") or []:
                    lines.append(f"      ⚠ 声明缺口(工位本地改动已被渲染物覆盖, 须回流母本/声明): {gap['path']} [{gap['reason']}]")
                    for dl in str(gap.get("diff") or "").splitlines()[:40]:
                        lines.append(f"          {dl}")
    if run.get("run_manifest_path"):
        lines.append(f"  run manifest: {run['run_manifest_path']}")
    if run.get("mode") == "single" and run.get("workstations") and run["workstations"][0]["status"] == "synced":
        lines.append("  下一步: /reload 让新扩展/技能生效")
    return "\n".join(lines)


def run_sync_cli(args: Any) -> int:
    harness_root = getattr(args, "harness_root", None) or os.environ.get("LYBRA_HARNESS_ROOT") or None
    json_mode = bool(getattr(args, "json", False))
    try:
        if not harness_root:
            resolve_sync_context(harness_root=None)  # 出声列出找过哪几层
            return 1
        gov = getattr(args, "workspace_root", None)
        run = sync_many(
            Path(harness_root),
            governance_root=Path(gov).expanduser() if gov else None,
            project=getattr(args, "project", None),
            dry_run=bool(getattr(args, "dry_run", False)),
            gate_url=getattr(args, "gate_url", None),
            token=getattr(args, "token", None),
        )
    except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
        if json_mode:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    if json_mode:
        print(json.dumps(run, ensure_ascii=False, indent=2))
    else:
        print(render_sync_text(run))
    return 0 if run.get("ok") else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AIPOS-C4B: lybra sync — worker-initiated distribution pull")
    parser.add_argument("--harness-root", default=None, help="Harness root(工位根或工位父根; REQUIRED: no cwd guessing; fallback env LYBRA_HARNESS_ROOT)")
    parser.add_argument("--workspace-root", default=None, help="AIPOS-F66B: 治理根(project.json): 定 sync 项目范围 + 章程渲染声明; 多工位必给(或 --project)")
    parser.add_argument("--project", default=None, help="AIPOS-F66B: sync 项目范围(非本项目工位跳过并记 skipped)")
    parser.add_argument("--dry-run", action="store_true", help="AIPOS-F66B: 零写入, 列 would-fetch/would-render/would-prune/skipped")
    parser.add_argument("--gate-url", default=None, help="Gate MCP URL (auto from .lybra if omitted)")
    parser.add_argument("--token", default=None, help="Bearer token (auto from .lybra connection.json if omitted)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()
    return run_sync_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
