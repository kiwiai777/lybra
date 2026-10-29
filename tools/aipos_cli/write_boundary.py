"""AIPOS-F66B 件②: 写权限边界的唯一读取口(护栏读声明)。

声明在 roles.schema.json `write_boundary` 一处: 角色类 × 目标面(surface) × 三级(read / append / mutate)。
本模块只做三件事, 禁第二份矩阵:
  - build_write_boundary(governance_root, ...): 把声明具体化为一个**可读面**(每行 = 角色/类/面/级/绝对路径),
    自定义角色按门注册表 class 展开(custom_roles.load_custom_roles, 与分发/信封同一加载), 实例按 enrollment 记录归属;
  - check_access(governance_root, role=..., path=..., level=..., task_id=...): 判一次访问, 供外部护栏消费;
    只读治理永远合法; 面外路径 = 未声明 = 拒(带出口); per_task 面无卡 ID = 只许 read;
    product_repo 的 mutate 受本卡工作树 + 卡 lane.paths 双限(与 N3 交回判据②同一口径);
  - render_write_boundary_markdown(...): 章程渲染物用的「写权限边界(声明渲染)」节(含 hard_rules「拒后禁换方式重试」)。

路径全部读声明: config.schema governance_structure.paths(path_key) / project.json paths(project_paths, F78 唯一读取口)
/ project.json repos(project_repos, F78C 唯一读取口)/ worktree_root(next_resolver.card_worktree_location, F86 开工面单源)。
fail-closed: 声明缺 = SchemaLoadError; 违规 = 拒, 不吞。外部护栏(kiwiaiops guard-lib 等)的改造不在本模块(登记不代做)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tools.schema_loader import SchemaLoadError, load_schema


class WriteBoundaryError(ValueError):
    """读取口输入无效(角色未知 / 级别未知 / 卡未找到), fail-closed。"""


# ---------------------------------------------------------------------------
# 声明读取
# ---------------------------------------------------------------------------

def load_write_boundary_declaration(repo_root: Path | None = None) -> dict[str, Any]:
    """读 roles.schema write_boundary(缺 = SchemaLoadError, fail-closed)。"""
    decl = load_schema("roles", repo_root).get("write_boundary")
    if not isinstance(decl, dict):
        raise SchemaLoadError("roles.schema.json write_boundary 未声明(AIPOS-F66B 件②)")
    for key in ("levels", "level_order", "surfaces", "matrix", "hard_rules"):
        if not decl.get(key):
            raise SchemaLoadError(f"roles.schema.json write_boundary.{key} 未声明")
    return decl


def _governance_structure_paths(repo_root: Path | None = None) -> dict[str, Any]:
    paths = (load_schema("config", repo_root).get("governance_structure") or {}).get("paths")
    if not isinstance(paths, dict) or not paths:
        raise SchemaLoadError("config.schema.json governance_structure.paths 未声明")
    return paths


def _resolve_structure_path(paths: dict[str, Any], key: str, governance_root: Path) -> Path:
    """按 relative_to 链解析 config.schema governance_structure.paths[key] 为绝对路径。"""
    spec = paths.get(key)
    if not isinstance(spec, dict):
        raise SchemaLoadError(f"config.schema.json governance_structure.paths.{key} 未声明")
    rel = str(spec.get("path") or "").strip("/")
    parent_key = str(spec.get("relative_to") or "").strip()
    if not parent_key or parent_key == "governance_root":
        return governance_root / rel if rel else governance_root
    return _resolve_structure_path(paths, parent_key, governance_root) / rel


def _level_rank(decl: dict[str, Any], level: str) -> int:
    order = [str(x) for x in decl["level_order"]]
    if level not in order:
        raise WriteBoundaryError(f"未知级别 {level!r}; 声明值域 {order}(roles.schema write_boundary.level_order)")
    return order.index(level)


# ---------------------------------------------------------------------------
# 角色 / 实例解析(单源: roles.schema 内建 + 门注册表自定义 + enrollment)
# ---------------------------------------------------------------------------

def resolve_role_class_for(governance_root: Path, role: str) -> str:
    """角色 → 类: 唯一实现 custom_roles.resolve_role_to_class(required=True, 内建自映射 / 门注册表自定义); 未知 = 拒(AIPOS-F102 件② 统一失败语义)。"""
    from tools.aipos_cli.custom_roles import UnknownRoleClass, resolve_role_to_class

    try:
        return str(resolve_role_to_class(role, governance_root, required=True))
    except UnknownRoleClass as exc:
        raise WriteBoundaryError(str(exc)) from exc


def enrolled_instances(governance_root: Path) -> list[dict[str, Any]]:
    """实例归属面: enrollment 记录(role/instance/governance_root/landed)。无记录 = 空表(非错误)。"""
    from tools.aipos_cli.enrollment import list_enrollment_codes

    out: list[dict[str, Any]] = []
    for item in list_enrollment_codes(governance_root):
        if not item.get("instance"):
            continue
        out.append({
            "instance": item["instance"],
            "role": item.get("role"),
            "governance_root": item.get("governance_root"),
            "landed": bool(item.get("landed")),
            "status": item.get("status"),
        })
    return out


# ---------------------------------------------------------------------------
# 可读面
# ---------------------------------------------------------------------------

def _surface_roots(governance_root: Path, decl: dict[str, Any], *, harness_root: Path | None) -> dict[str, dict[str, Any]]:
    """把每个面解析为 {name: {roots: [Path...], per_task, per_task_lane, description}}。"""
    from tools.aipos_cli.workspace_config import project_paths, project_repos

    structure = _governance_structure_paths()
    ppaths = project_paths(governance_root)
    repos = project_repos(governance_root)
    out: dict[str, dict[str, Any]] = {}
    for name, spec in decl["surfaces"].items():
        root_kind = str(spec.get("root") or "")
        roots: list[Path] = []
        if root_kind == "governance_root":
            if spec.get("path_key"):
                roots = [_resolve_structure_path(structure, str(spec["path_key"]), governance_root)]
            elif spec.get("project_paths_key"):
                key = str(spec["project_paths_key"])
                if key not in ppaths:
                    raise SchemaLoadError(f"project.json paths.{key} 无声明/缺省(config.schema project_json.paths)")
                roots = [Path(ppaths[key])]
            else:
                roots = [governance_root]
        elif root_kind == "project_repos":
            roots = list(repos["items"].values()) if repos["declared"] else ([repos["code_repo"]] if repos["code_repo"] else [])
        elif root_kind == "harness_root":
            roots = [harness_root] if harness_root else []
        elif root_kind == "validation_roots":
            roots = []  # AIPOS-F190: 按主机声明(project.json validation_roots), 不按本机路径前缀匹配; 判定走 validation_root.judge_access
        elif root_kind == "release_targets":
            roots = []  # AIPOS-F251: 按主机声明(project.json release_targets), 判定走 release_target.judge_access
        else:
            raise SchemaLoadError(f"roles.schema write_boundary.surfaces.{name}.root={root_kind!r} 未知")
        out[name] = {
            "roots": [Path(r).expanduser() for r in roots],
            "per_task": bool(spec.get("per_task")),
            "per_task_lane": bool(spec.get("per_task_lane")),
            "per_run": bool(spec.get("per_run")),
            "host_scoped": root_kind in ("validation_roots", "release_targets"),
            "row_path": str(spec.get("row_path") or ""),  # AIPOS-F251: 按主机声明面的可读面行(缺省 = 验证根模板)
            "row_scope": str(spec.get("row_scope") or ""),
            "description": str(spec.get("description") or ""),
        }
    return out


def build_write_boundary(
    governance_root: str | Path,
    *,
    role: str | None = None,
    harness_root: str | Path | None = None,
) -> dict[str, Any]:
    """生成可读面(声明 → 具体路径)。role 给定 = 只出该角色行(自定义角色按类展开)。"""
    governance_root = Path(governance_root).expanduser().resolve()
    decl = load_write_boundary_declaration()
    surfaces = _surface_roots(governance_root, decl, harness_root=Path(harness_root).expanduser() if harness_root else None)
    from tools.aipos_cli.custom_roles import load_custom_roles
    from tools.aipos_cli.workspace_config import read_project_json

    project = str(read_project_json(governance_root).get("project") or governance_root.name)
    custom = load_custom_roles(governance_root)
    roles: dict[str, str] = {r: r for r in decl["matrix"]}
    roles.update({name: entry["class"] for name, entry in custom.items() if entry.get("class") in decl["matrix"]})
    if role:
        cls = resolve_role_class_for(governance_root, role)
        if cls not in decl["matrix"]:
            raise WriteBoundaryError(f"角色类 {cls!r} 在 roles.schema write_boundary.matrix 无行; 出口: 补矩阵声明")
        roles = {str(role).strip(): cls}
    rows: list[dict[str, Any]] = []
    for name, cls in sorted(roles.items()):
        for surface, level in decl["matrix"][cls].items():
            spec = surfaces.get(surface)
            if spec is None:
                raise SchemaLoadError(f"roles.schema write_boundary.matrix.{cls}.{surface} 引用未声明的面")
            _level_rank(decl, str(level))
            if spec["host_scoped"]:
                # AIPOS-F190: 验证根按主机声明, 可读面给模板(实际声明 = lybra project validation-root list; 本实例条目随章程同源渲染)
                rows.append({"role": name, "role_class": cls, "surface": surface, "level": str(level),
                             "path": spec["row_path"] or "<声明 base>/<task_id>/<run_id>(按主机声明: lybra project validation-root list)",
                             "scope": spec["row_scope"] or "本卡本活动 run 目录(按主机)", "description": spec["description"]})
                continue
            for root in spec["roots"] or [None]:
                rows.append({
                    "role": name,
                    "role_class": cls,
                    "surface": surface,
                    "level": str(level),
                    "path": (str(root / "<task_id>") if spec["per_task"] and root else str(root)) if root else "<unresolved: 无声明根>",
                    "scope": ("本卡目录" if spec["per_task"] else ("本卡工作树+lane.paths(mutate)" if spec["per_task_lane"] else "整面")),
                    "description": spec["description"],
                })
    return {
        "project": project,
        "governance_root": str(governance_root),
        "levels": dict(decl["levels"]),
        "level_order": list(decl["level_order"]),
        "roles": roles,
        "rows": rows,
        "instances": enrolled_instances(governance_root),
        "hard_rules": [dict(r) for r in decl["hard_rules"]],
        "matrix_note": str(decl.get("matrix_note") or ""),
        "source": "roles.schema.json write_boundary (单源; 读取口 tools/aipos_cli/write_boundary.py)",
    }


# ---------------------------------------------------------------------------
# 读取口: 判一次访问
# ---------------------------------------------------------------------------

def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _card_frontmatter(governance_root: Path, task_id: str) -> dict[str, Any]:
    """卡面 frontmatter(卡不存在 / 读不出 = WriteBoundaryError, fail-closed)。"""
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    task_path, _queue = _find_task_in_queue(governance_root, task_id)
    if not task_path:
        raise WriteBoundaryError(f"卡 {task_id} 不在队列目录中(product_repo mutate 需按卡 lane.paths 判)")
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    try:
        return _read_frontmatter(task_path)
    except FrontmatterReadError as exc:
        # AIPOS-F100 件②: 卡读不出 = 拒(不按空 lane 判, 原文点名文件)
        raise WriteBoundaryError(f"卡 {task_id} frontmatter {exc}") from exc


def _card_lane_paths(governance_root: Path, task_id: str) -> tuple[Path, list[str]]:
    """卡的产品仓 + lane.paths(读卡面; 卡不存在 = 拒)。"""
    from tools.aipos_cli.workspace_config import resolve_card_repo

    fm = _card_frontmatter(governance_root, task_id)
    lane = fm.get("lane") if isinstance(fm.get("lane"), dict) else {}
    paths = [str(p).strip() for p in (lane.get("paths") or []) if str(p).strip()]
    repo = resolve_card_repo(governance_root, {**fm, "task_id": task_id})
    return Path(repo), paths


def check_access(
    governance_root: str | Path,
    *,
    role: str,
    path: str | Path,
    level: str,
    task_id: str | None = None,
    harness_root: str | Path | None = None,
    host: str | None = None,
    instance: str | None = None,
    run_id: str | None = None,
    host_fingerprint: str | None = None,
) -> dict[str, Any]:
    """判一次访问: {allowed, reason, code, surface, granted_level, role_class, hard_rules}。

    判据(全部读声明):
      1. 面匹配 = 路径落在哪个面(最深根优先; per_task 面按 <root>/<task_id>);
      2. 角色类对该面的声明级别 ≥ 请求级别 → 允许;
      3. per_task 面无 task_id: 只许 read(mutate 需 task_id 指本卡);
      4. product_repo mutate: 路径须在 <worktree_root>/<task_id>/ 内且在卡 lane.paths 内;
      5. 面外路径 = 未声明 = 拒(code SURFACE_UNDECLARED, 出口: 补声明);
      6. 只读治理永远合法(governance_root 面 read 对所有有行角色成立)。
      7. AIPOS-F190 主机维度: host 缺省 / 等于本机 = 本机判定; 路径落在该主机 validation_roots 声明 base 内 = 面 validation_root
         (validation_root.judge_access: 本实例本卡本活动 run 目录才放行; 本机另按 realpath 判符号链接逃逸);
         他机路径只判 validation_root 面(字面), 不在任何声明 base 内 = SURFACE_UNDECLARED。
      8. AIPOS-F251 受控生产发布面: validation_root 不命中时, 路径落在该主机某发布目标的发布根 / 入口 / 其父目录内 = 面 release_target
         (release_target.judge_access: 只声明内精确路径且未存在才放行 append; 父目录其他条目 / 发布根内其他路径 / 已存在 / mutate 一律拒)。
    """
    governance_root = Path(governance_root).expanduser().resolve()
    decl = load_write_boundary_declaration()
    want = str(level or "").strip()
    want_rank = _level_rank(decl, want)
    cls = resolve_role_class_for(governance_root, role)
    matrix = decl["matrix"].get(cls)
    if not matrix:
        raise WriteBoundaryError(f"角色类 {cls!r} 在 roles.schema write_boundary.matrix 无行; 出口: 补矩阵声明")
    hard_rules = [dict(r) for r in decl["hard_rules"]]
    # AIPOS-F190: 主机维度 + validation_root 面(先于本机面匹配; base 已在声明时排除治理根 / 产品仓, 与其他面不重叠)
    from tools.aipos_cli import validation_root as vr

    remote = not vr.is_local_host(host)
    raw = str(path)
    raw = raw if remote else str(Path(raw).expanduser()) if raw.startswith("~") else raw
    if not raw.startswith("/"):
        if remote:
            return {"allowed": False, "code": "PATH_NOT_CANONICAL", "surface": vr.SURFACE, "granted_level": None, "role_class": cls,
                    "reason": f"主机 {host} 上的路径须为绝对路径: {raw!r}", "hard_rules": hard_rules}
        raw = str(governance_root / raw)
    granted_vr = str(matrix.get(vr.SURFACE) or "") or None
    vr_result = vr.judge_access(governance_root, host=str(host or ""), raw_path=raw, want_rank=want_rank, granted=granted_vr,
                                granted_rank=_level_rank(decl, granted_vr) if granted_vr else None, instance=instance, task_id=task_id,
                                run_id=run_id, host_fingerprint=host_fingerprint)
    if vr_result is not None:
        return {**vr_result, "role_class": cls, "hard_rules": hard_rules}
    # AIPOS-F251: 受控生产发布面(按主机声明 project.json release_targets; 与 validation_root 是两个面, 判定与文案互不冒充)
    from tools.aipos_cli import release_target as rt

    granted_rt = str(matrix.get(rt.SURFACE) or "") or None
    rt_result = rt.judge_access(governance_root, host=host, raw_path=raw, want_rank=want_rank, granted=granted_rt,
                                granted_rank=_level_rank(decl, granted_rt) if granted_rt else None)
    if rt_result is not None:
        return {**rt_result, "role_class": cls, "hard_rules": hard_rules}
    if remote:
        return {
            "allowed": False, "code": "SURFACE_UNDECLARED", "surface": None, "granted_level": None, "role_class": cls, "host": host,
            "reason": (f"主机 {host} 上的路径 {raw} 不在任何验证根声明内(project.json validation_roots); 出口: 顾问 "
                       "`lybra project validation-root declare` 声明, 禁角色自行突破"),
            "hard_rules": hard_rules,
        }
    target = Path(raw)
    target = target.resolve() if target.exists() else Path(str(target))
    surfaces = _surface_roots(governance_root, decl, harness_root=Path(harness_root).expanduser() if harness_root else None)

    # 1. 面匹配: 最深根优先(governance_docs 深于 governance_root); 同根并列时优先「本角色有行」的 per_task 面
    #    (lybra 形 return_root = verdict_root = task_cards 三面同根: executor 走 return_slot, auditor 走 verdict_slot)
    matches: list[tuple[int, int, int, str, Path]] = []
    for name, spec in surfaces.items():
        for root in spec["roots"]:
            root_r = root.resolve() if root.exists() else Path(str(root))
            if _is_within(target, root_r):
                matches.append((len(str(root_r)), int(bool(matrix.get(name))), int(spec["per_task"]), name, root_r))
    if not matches:
        return {
            "allowed": False, "code": "SURFACE_UNDECLARED", "surface": None, "granted_level": None, "role_class": cls,
            "reason": f"路径 {target} 不在任何声明面内(roles.schema write_boundary.surfaces); 出口: 补面声明, 禁角色自行突破",
            "hard_rules": hard_rules,
        }
    matches.sort(key=lambda m: (m[0], m[1], m[2]), reverse=True)
    _, _, _, surface, root_r = matches[0]
    spec = surfaces[surface]
    granted = str(matrix.get(surface) or "")
    if not granted:
        # 该角色对此面无行 → 退到 governance_root 兜底面(只读永远合法)
        if _is_within(target, governance_root) and "governance_root" in matrix:
            surface, granted, spec, root_r = "governance_root", str(matrix["governance_root"]), surfaces["governance_root"], governance_root
        else:
            return {
                "allowed": False, "code": "ROLE_NO_ROW", "surface": surface, "granted_level": None, "role_class": cls,
                "reason": f"角色类 {cls} 对面 {surface} 无声明行(roles.schema write_boundary.matrix.{cls}); 出口: 补矩阵声明",
                "hard_rules": hard_rules,
            }
    granted_rank = _level_rank(decl, granted)
    base = {"surface": surface, "granted_level": granted, "role_class": cls, "hard_rules": hard_rules}

    # 3. per_task 面: mutate/append 须指本卡目录
    if spec["per_task"] and want_rank > 0:
        if not task_id:
            return {**base, "allowed": False, "code": "TASK_ID_REQUIRED",
                    "reason": f"面 {surface} 按卡目录授权(<root>/<task_id>/), {want} 需给 task_id"}
        if not _is_within(target, root_r / task_id):
            return {**base, "allowed": False, "code": "NOT_OWN_TASK_DIR",
                    "reason": f"面 {surface} 只对本卡目录 {root_r / task_id} 允许 {want}; 目标 {target} 不在其中"}

    # 2. 级别比较
    if want_rank > granted_rank:
        return {**base, "allowed": False, "code": "LEVEL_EXCEEDED",
                "reason": f"角色类 {cls} 对面 {surface} 声明级别={granted}, 请求={want}(roles.schema write_boundary.matrix.{cls}.{surface})"}

    # 4. product_repo mutate: 本卡工作树 + lane.paths 双限
    if spec["per_task_lane"] and want_rank > 0:
        if not task_id:
            return {**base, "allowed": False, "code": "TASK_ID_REQUIRED",
                    "reason": f"面 {surface} 的 {want} 受本卡工作树 + lane.paths 双限, 需给 task_id"}
        from tools.aipos_cli.next_resolver import card_needs_worktree, card_report_path, card_worktree_location

        card_fm = _card_frontmatter(governance_root, task_id)
        if not card_needs_worktree(governance_root, {**card_fm, "task_id": task_id}):
            # AIPOS-F178 件②: 只写治理根的卡不建卡工作树(唯一判据 card_needs_worktree), 产品仓无本卡可写处; 点名治理根报告落点
            report = card_report_path(governance_root, task_id, card_fm)
            from tools.aipos_cli.next_resolver import card_follows_candidate_subject

            if card_follows_candidate_subject(governance_root, {**card_fm, "task_id": task_id}):
                # AIPOS-F196 件①: 固定候选验证卡(及其审计卡)不写产品仓源码; 产物只落验证根与报告落点
                return {**base, "allowed": False, "code": "CARD_NO_WORKTREE",
                        "reason": (f"卡 {task_id} 交付主体 = 固定候选(判据 enums.schema task_mode.candidate_subject), 不写产品仓源码, "
                                   f"product_repo 的 {want} 一律拒; 产物只落本卡验证根(validation_root 面)与报告落点 {report}")}
            return {**base, "allowed": False, "code": "CARD_NO_WORKTREE",
                    "reason": (f"卡 {task_id} 只写治理根(不写产品仓, 不建卡工作树; 判据 enums.schema task_mode.product_repo_write), "
                               f"product_repo 的 {want} 一律拒; 产物写治理根报告落点 {report}")}
        _repo, lane_paths = _card_lane_paths(governance_root, task_id)
        _repo, worktree = card_worktree_location(governance_root, task_id)  # AIPOS-F86: 与 claim 建树 / my-tasks 同一推导
        worktree_r = worktree.resolve() if worktree.exists() else Path(str(worktree))
        if not _is_within(target, worktree_r):
            return {**base, "allowed": False, "code": "NOT_CARD_WORKTREE",
                    "reason": f"product_repo 的 {want} 只在本卡工作树 {worktree} 内(主检出/他卡工作树一律拒); 目标 {target}"}
        rel = target.relative_to(worktree_r).as_posix()
        if not any(rel == lp.rstrip("/") or rel.startswith(lp.rstrip("/") + "/") for lp in lane_paths):
            return {**base, "allowed": False, "code": "LANE_OUT_OF_SCOPE",
                    "reason": f"目标 {rel} 不在卡 {task_id} lane.paths {lane_paths} 内(与 N3 交回判据②同一口径); 出口: 顾问 amend 车道"}
    return {**base, "allowed": True, "code": "OK", "reason": f"角色类 {cls} 对面 {surface} 声明级别={granted} ≥ 请求={want}"}


# ---------------------------------------------------------------------------
# 章程渲染节
# ---------------------------------------------------------------------------

def role_rows(boundary: dict[str, Any], role: str, *, task_id: str | None = None) -> list[dict[str, Any]]:
    """AIPOS-F212 件④: 某角色的写权限边界行(章程表 render_write_boundary_markdown 与 lybra kickoff 边界行同读, 选行只此一处);
    task_id 给 = 路径占位 <task_id> 落为本卡(开工稿按卡), 缺 = 原样(章程不按卡)。"""
    rows = [dict(r) for r in boundary["rows"] if r["role"] == role]
    if task_id:
        for row in rows:
            row["path"] = str(row["path"]).replace("<task_id>", task_id)
    return rows


def no_write_lines(boundary: dict[str, Any], role: str, *, task_id: str, omit_surfaces: tuple[str, ...] = ()) -> list[str]:
    """AIPOS-F215 件③: 开工稿「禁写」行由写权限边界派生(声明 roles.schema write_boundary.no_write; 选行 = role_rows, 与边界表同读)。
    该角色级别为 read 且 no_write.surfaces 有条目的面各出一行(按 role_rows 序); {path} = 该面行路径, {slots} = 该角色 mutate 的
    slot_surfaces 行路径。面行路径未解析 / 在 omit_surfaces(调用方声明本卡不适用, 如子 agent 无工位)= 不出该行。
    声明缺 / 形不合 / 模板要 {slots} 而本角色无报告落点面 = SchemaLoadError(fail-closed)。"""
    decl = load_write_boundary_declaration().get("no_write")
    templates = decl.get("surfaces") if isinstance(decl, dict) else None
    slot_surfaces = decl.get("slot_surfaces") if isinstance(decl, dict) else None
    if (not isinstance(templates, dict) or not templates or any(not isinstance(t, str) or not t for t in templates.values())
            or not isinstance(slot_surfaces, list) or not slot_surfaces):
        raise SchemaLoadError("roles.schema.json write_boundary.no_write(surfaces 非空串表 / slot_surfaces 非空列表)未声明")
    rows = role_rows(boundary, role, task_id=task_id)
    slots = "、".join(f"`{r['path']}`" for r in rows if r["surface"] in slot_surfaces and r["level"] == "mutate")
    lines: list[str] = []
    for row in rows:
        template = templates.get(row["surface"])
        if template is None or row["level"] != "read" or row["surface"] in omit_surfaces or str(row["path"]).startswith("<unresolved"):
            continue
        if "{slots}" in template and not slots:
            raise SchemaLoadError(f"roles.schema.json write_boundary.no_write.surfaces.{row['surface']} 要 {{slots}}, 角色 {role} 无 mutate 的 "
                                  f"{slot_surfaces} 面")
        line = template.replace("{path}", str(row["path"])).replace("{slots}", slots)
        if line not in lines:  # 多仓项目 product_repo 每仓一行, 模板无 {path} 时只出一次
            lines.append(line)
    return lines


def render_write_boundary_markdown(boundary: dict[str, Any], *, role: str) -> str:
    """章程渲染物用: 该角色行 + hard_rules。同一份声明, 所有角色章程同源。"""
    rows = role_rows(boundary, role)
    lines = [
        "## 🔴 写权限边界(声明渲染, AIPOS-F66B 件②; 单源 roles.schema write_boundary)",
        "",
        f"角色 `{role}`(类 `{boundary['roles'].get(role, role)}`)对项目 `{boundary['project']}` 的三级边界(read < append < mutate); 面外路径 = 未声明 = 不许写:",
        "",
        "| 面 | 级别 | 路径 | 范围 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['surface']} | **{r['level']}** | `{r['path']}` | {r['scope']} |")
    lines += ["", "**硬规矩(全角色)**:"]
    for rule in boundary["hard_rules"]:
        lines.append(f"- **{rule.get('id')}**: {rule.get('text')}")
    return "\n".join(lines) + "\n"


def render_write_boundary_text(boundary: dict[str, Any]) -> str:
    lines = [f"Write boundary · project={boundary['project']} · governance_root={boundary['governance_root']}",
             f"levels: {' < '.join(boundary['level_order'])}",
             f"{'role':<18} {'class':<10} {'surface':<16} {'level':<8} path"]
    for r in boundary["rows"]:
        lines.append(f"{r['role']:<18} {r['role_class']:<10} {r['surface']:<16} {r['level']:<8} {r['path']}")
    if boundary["instances"]:
        lines.append("enrolled instances (enrollment 记录):")
        for i in boundary["instances"]:
            lines.append(f"  - {i['instance']}  role={i['role']}  governance_root={i.get('governance_root') or '-'}  landed={i['landed']}")
    lines.append("hard rules:")
    for rule in boundary["hard_rules"]:
        lines.append(f"  - {rule.get('id')}: {rule.get('text')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI 薄壳: lybra roles write-boundary
# ---------------------------------------------------------------------------

def run_write_boundary_cli(args: Any, workspace_root: Path) -> int:
    role = getattr(args, "role", None)
    instance = getattr(args, "instance", None)
    if instance and not role:
        match = next((i for i in enrolled_instances(workspace_root) if i["instance"] == instance), None)
        if match is None:
            # AIPOS-F190: 无接入码记录的已接入实例(子 agent land 事件 / 连接文件条目)按实例接入唯一判定取角色
            from tools.aipos_cli.enrollment import instance_enrollment

            view = instance_enrollment(workspace_root, instance)
            if view["enrolled"] and view.get("role"):
                match = {"instance": instance, "role": view["role"]}
        if match is None:
            print(f"lybra roles write-boundary: 实例 {instance} 不在 enrollment 记录中(--workspace-root {workspace_root})", file=sys.stderr)
            return 1
        role = match["role"]
    try:
        check_path = getattr(args, "check", None)
        if check_path:
            if not role:
                print("lybra roles write-boundary --check 需 --role 或 --instance", file=sys.stderr)
                return 2
            result = check_access(
                workspace_root, role=role, path=check_path, level=getattr(args, "level", None) or "read",
                task_id=getattr(args, "task_id", None), harness_root=getattr(args, "harness_root", None),
                host=getattr(args, "host", None), instance=instance, run_id=getattr(args, "run_id", None),
                host_fingerprint=getattr(args, "host_fingerprint", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"{'ALLOW' if result['allowed'] else 'DENY'} [{result['code']}] {result['reason']}")
                if result.get("host_side_check"):
                    print(f"  目标主机复核(只认退出码 0 + ALLOW): {result['host_side_check']}")
            return 0 if result["allowed"] else 3
        boundary = build_write_boundary(workspace_root, role=role, harness_root=getattr(args, "harness_root", None))
    except (WriteBoundaryError, SchemaLoadError, OSError, ValueError) as exc:  # ValidationRootError / ReleaseTargetError ⊂ ValueError(段形不合 = 拒, 不放行)
        print(f"lybra roles write-boundary: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(boundary, ensure_ascii=False, indent=2))
    elif getattr(args, "markdown", False):
        print(render_write_boundary_markdown(boundary, role=role or "<role>"), end="")
    else:
        print(render_write_boundary_text(boundary))
    return 0


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
