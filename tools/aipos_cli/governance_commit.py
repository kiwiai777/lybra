"""AIPOS-R7A2 靶②: lybra governance-commit — 顾问收口一条命令

N6 收账清单校验四件齐全:
1. 本卡台账条目 (task_cards/<ID>/)
2. decision_log 指针 (如适用)
3. 阶段快照 (如阶段收口)
4. task_cards 归档

校验通过 → commit(过治理仓 pre-commit 四检) → push
缺件明确报哪一件并拒绝;失败不静默。

命令须可由顾问工位 advisor token 使用(无 finalize 权限)。

AIPOS-F79 精确批次提交(chris 总顾问实撞):
- 件①: ``--paths``/``--paths-file`` 显式白名单 → 只 ``git add -- <paths>``, 绝不 add -A;
  路径越出治理根或指向他项目 = 拒(fail-closed)。
- 件②: ``--dry-run`` 输出将提交文件清单(modified/added/deleted/untracked_selected),
  只读 ``git status --porcelain`` + ``git diff --name-status``, 不 add/不 reset/不 stash。
- 件③: 正式提交后 ``git show --name-only HEAD`` 与清单逐条比对, 不同即 FAIL 出声;
  预暂存文件在 --paths 内 = 纳入清单, 在 --paths 外 = 拒并列出。
- 无 --paths 的整根 ``git add -A -- .`` 仅保留给 lybra 自身工作区; 他项目一律 --paths。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from tools.schema_constants import Verdict


# ---------------------------------------------------------------------------
# AIPOS-F79: 显式路径白名单 + 只读清单
# ---------------------------------------------------------------------------

# 清单分类(件②): 与 git status/diff 状态码的映射
MANIFEST_MODIFIED = "modified"
MANIFEST_ADDED = "added"
MANIFEST_DELETED = "deleted"
MANIFEST_UNTRACKED = "untracked_selected"

_GIT_QUOTEPATH_OFF = ["git", "-c", "core.quotepath=false"]


def _git_readonly(args: list[str], cwd: Path, *, timeout: int = 30) -> str:
    """只读 git 调用(status/diff/rev-parse/show); 失败直接抛 CalledProcessError, 禁静默。"""
    result = subprocess.run(
        _GIT_QUOTEPATH_OFF + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


def _split_nul(raw: str) -> list[str]:
    return [t for t in raw.split("\0") if t != ""]


def resolve_commit_paths(
    governance_root: Path,
    paths: list[str] | None,
    paths_file: Path | str | None,
) -> dict[str, Any]:
    """AIPOS-F79 件①: 把 --paths / --paths-file 归一为治理根相对路径白名单。

    二者同一实现: --paths-file 每行一路径(空行与 # 行忽略), 与 --paths 二选一。
    fail-closed 规则(任一命中即整体拒绝, 不做部分接受):
      - 两种给法同时出现;
      - --paths-file 不存在/不可读;
      - 路径为空 / 指向治理根本身(等价整根 add -A);
      - 路径(含 ``..``/绝对路径/符号链接解析后)落在治理根之外 —— 含指向他项目。

    Returns:
        {"selected": bool, "paths": [rel...], "source": "paths"|"paths_file"|None,
         "rejected": [{"path": str, "reason": str}], "message": str|None}
    """
    if not paths and paths_file is None:
        return {"selected": False, "paths": [], "source": None, "rejected": [], "message": None}

    if paths and paths_file is not None:
        return {
            "selected": True,
            "paths": [],
            "source": None,
            "rejected": [{"path": str(paths_file), "reason": "--paths 与 --paths-file 二选一, 不可同时给"}],
            "message": "--paths 与 --paths-file 二选一, 不可同时给(fail-closed)",
        }

    raw_entries: list[str]
    source: str
    if paths_file is not None:
        source = "paths_file"
        pf = Path(paths_file).expanduser()
        try:
            content = pf.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, IsADirectoryError, UnicodeDecodeError) as exc:
            return {
                "selected": True,
                "paths": [],
                "source": source,
                "rejected": [{"path": str(pf), "reason": f"--paths-file 不可读: {exc.__class__.__name__}: {exc}"}],
                "message": f"--paths-file 不可读: {pf} ({exc.__class__.__name__})",
            }
        raw_entries = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raw_entries.append(stripped)
        if not raw_entries:
            return {
                "selected": True,
                "paths": [],
                "source": source,
                "rejected": [{"path": str(pf), "reason": "--paths-file 为空(无有效路径行)"}],
                "message": f"--paths-file 为空, 无可提交路径: {pf}",
            }
    else:
        source = "paths"
        raw_entries = [str(p) for p in (paths or [])]

    root_resolved = governance_root.resolve()
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        candidate = raw.strip()
        if not candidate:
            rejected.append({"path": raw, "reason": "空路径"})
            continue
        abs_candidate = Path(candidate) if os.path.isabs(candidate) else governance_root / candidate
        # resolve(): 消掉 ../ 并解析符号链接; 不存在的路径(如已删除文件)也可解析(strict=False)
        resolved = abs_candidate.resolve()
        if resolved == root_resolved:
            rejected.append({"path": raw, "reason": "指向治理根本身 = 整根提交; 请列具体文件/目录, 或去掉 --paths(仅 lybra 自身工作区允许)"})
            continue
        if not resolved.is_relative_to(root_resolved):
            rejected.append({"path": raw, "reason": f"越出治理根 {root_resolved}(他项目或仓外路径, 拒)"})
            continue
        rel = resolved.relative_to(root_resolved).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        accepted.append(rel)

    if rejected:
        listing = "\n  - ".join(f"{r['path']}: {r['reason']}" for r in rejected)
        return {
            "selected": True,
            "paths": [],
            "source": source,
            "rejected": rejected,
            "message": (
                f"--paths 白名单含非法路径, 整体拒绝(fail-closed, 不做部分提交):\n  - {listing}\n\n"
                f"可执行出口:\n"
                f"1. 只列治理根 {root_resolved} 内的相对路径(文件或目录)\n"
                f"2. 他项目内容须在其自己的治理根下用各自的 lybra governance-commit --paths 提交"
            ),
        }

    return {"selected": True, "paths": accepted, "source": source, "rejected": [], "message": None}


def _git_ws_prefix(governance_root: Path) -> str:
    """治理根在 git 仓内的相对前缀(如 ``2_projects/chris-huibojin/``; 仓根即治理根时为 ``""``)。"""
    return _git_readonly(["rev-parse", "--show-prefix"], governance_root).strip()


def collect_commit_manifest(governance_root: Path, selected_paths: list[str] | None) -> dict[str, Any]:
    """AIPOS-F79 件②: 只读取得「将提交的具体文件」清单。

    只用 ``git status --porcelain -uall -z -- <pathspec>``(取未跟踪文件)与
    ``git diff --name-status --no-renames -z HEAD -- <pathspec>``(取已跟踪的 M/A/D, 即
    ``git add <pathspec>`` + commit 后相对 HEAD 的净差异)。不 add/不 reset/不 stash。

    selected_paths=None → pathspec ``.``(整根, 即既有 ``git add -A -- .`` 会吞入的范围),
    并对未跟踪文件数出 warning。

    Returns:
        {"mode": "paths"|"whole_root", "pathspec": [...], "ws_prefix": str,
         "files": [{"path": 治理根相对, "repo_path": 仓根相对, "status": 分类}],
         "counts": {分类: n, "total": n}, "warnings": [str]}
    """
    ws_prefix = _git_ws_prefix(governance_root)
    mode = "paths" if selected_paths else "whole_root"
    pathspec = list(selected_paths) if selected_paths else ["."]

    def to_gov_rel(repo_path: str) -> str:
        if ws_prefix and repo_path.startswith(ws_prefix):
            return repo_path[len(ws_prefix):]
        return repo_path

    files: dict[str, dict[str, str]] = {}

    # ① 已跟踪文件: HEAD vs 工作树(含已暂存), 即提交后的净差异
    diff_raw = _git_readonly(
        ["diff", "--name-status", "--no-renames", "-z", "HEAD", "--"] + pathspec, governance_root
    )
    tokens = _split_nul(diff_raw)
    i = 0
    while i + 1 < len(tokens):
        code, repo_path = tokens[i], tokens[i + 1]
        i += 2
        letter = code[:1]
        if letter == "D":
            status = MANIFEST_DELETED
        elif letter == "A":
            status = MANIFEST_ADDED
        else:  # M / T(类型变更) 均视为 modified
            status = MANIFEST_MODIFIED
        files[repo_path] = {"path": to_gov_rel(repo_path), "repo_path": repo_path, "status": status}

    # ② 未跟踪文件: 只取 ?? 行(-uall 展开目录到文件级, 与 git add 后的粒度一致)
    status_raw = _git_readonly(
        ["status", "--porcelain", "--untracked-files=all", "-z", "--"] + pathspec, governance_root
    )
    tokens = _split_nul(status_raw)
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        xy, repo_path = entry[:2], entry[3:]
        if xy[0] in ("R", "C"):
            i += 1  # 重命名/复制条目带一个源路径 token, 跳过
        if xy == "??" and repo_path not in files:
            files[repo_path] = {"path": to_gov_rel(repo_path), "repo_path": repo_path, "status": MANIFEST_UNTRACKED}

    ordered = sorted(files.values(), key=lambda f: f["repo_path"])
    counts = {MANIFEST_MODIFIED: 0, MANIFEST_ADDED: 0, MANIFEST_DELETED: 0, MANIFEST_UNTRACKED: 0}
    for f in ordered:
        counts[f["status"]] += 1
    counts["total"] = len(ordered)

    warnings_out: list[str] = []
    if mode == "whole_root":
        warnings_out.append(
            f"无 --paths: 将对治理根整根 git add -A -- . (仅限 lybra 自身工作区; 他项目一律 --paths)"
        )
        if counts[MANIFEST_UNTRACKED]:
            warnings_out.append(
                f"WARNING: 整根提交会吞入 {counts[MANIFEST_UNTRACKED]} 个未跟踪文件(历史材料会被夹带); "
                f"请改用 --paths 精确选定"
            )
    return {
        "mode": mode,
        "pathspec": pathspec,
        "ws_prefix": ws_prefix,
        "files": ordered,
        "counts": counts,
        "warnings": warnings_out,
    }


def _manifest_repo_paths(manifest: dict[str, Any]) -> list[str]:
    return sorted(f["repo_path"] for f in manifest["files"])


def check_governance_completeness(
    governance_root: Path,
    task_id: str | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """校验 N6 收账清单四件齐全。
    
    AIPOS-R7A2 FIX-1: 所有治理路径从 config.schema 解析,零写死。
    参考 finalize.py::check_stage_archive_gate 的正确模式。
    
    AIPOS-F69 大项①: task_id 可选 — 无卡时跳过本卡台账条目检查(治理批次语义),
    仍走同一校验链与 pre-commit 四检。
    
    Args:
        governance_root: 治理工作区根
        task_id: 任务 ID (可选;无卡时跳过本卡台账检查)
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
    
    Returns:
        {
            "complete": bool,
            "missing": list[str],  # 缺件列表
            "details": dict,       # 各项详情
        }
    """
    from tools.schema_loader import resolve_governance_path
    
    missing = []
    details = {}
    
    # AIPOS-R7A2 FIX-2: 四件解析统一为"解析失败即显式报错/BLOCK",禁静默降级到硬编码
    
    # AIPOS-F69 大项①: 无 task_id 时跳过本卡台账条目检查(治理批次语义)
    if task_id is None:
        details["task_cards"] = {"exists": None, "note": "Skipped (no task_id provided)"}
        details["archive_files"] = {"exists": None, "note": "Skipped (no task_id provided)"}
        details["decision_log"] = {"applicable": False, "note": "Skipped (no task_id provided)"}
        details["stage_snapshots"] = {"applicable": True, "note": "Stage archive check still applies"}
        # 无卡时只检查 stage_archive (阶段粒度治理更新仍需快照)
    else:
        # ① 本卡台账条目 (task_cards/<ID>/) + ④ 归档文件检查
        # AIPOS-R7A2 FIX-2: task_cards 路径从 schema 解析,失败即 BLOCK
        try:
            task_cards_root = resolve_governance_path("task_cards", governance_root, repo_root)
            task_cards_dir = task_cards_root / task_id
        
            if task_cards_dir.is_dir():
                details["task_cards"] = {
                    "exists": True,
                    "path": str(task_cards_dir),
                    "files": [f.name for f in task_cards_dir.iterdir()],
                }
                
                # ④ task_cards 归档 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)
                archive_files = [
                    f.name for f in task_cards_dir.iterdir()
                    if f.name in ["RETURN.md", "AUDIT-REPORT.md", "CLOSURE.md"]
                ]
                
                if not archive_files:
                    missing.append(f"task_cards/{task_id}/ 缺少归档文件 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)")
                
                details["archive_files"] = {
                    "exists": len(archive_files) > 0,
                    "files": archive_files,
                }
            else:
                missing.append(f"task_cards/{task_id}/ (台账条目不存在)")
                details["task_cards"] = {"exists": False}
                details["archive_files"] = {"exists": False, "files": []}
        except Exception as exc:
            # AIPOS-R7A2 FIX-2: 移除 fallback,解析失败显式报错
            missing.append(f"task_cards/ (路径解析失败: {exc})")
            details["task_cards"] = {"exists": False, "error": str(exc)}
            details["archive_files"] = {"exists": False, "error": str(exc)}
        
        # ② decision_log 指针 (如适用)
        # AIPOS-R7A2 FIX-2: decision_log 路径从 schema 解析,失败显式报错
        try:
            decision_log_dir = resolve_governance_path("decision_log_dir", governance_root, repo_root)
            # 在 decision_log/ 下查找与本任务相关的条目 (按 YYYY-MM/YYYY-MM-DD-<slug>.md 结构)
            # 这里简化为检查整个目录树中包含 task_id 的 .md 文件
            decision_files = []
            if decision_log_dir.is_dir():
                for md_file in decision_log_dir.rglob("*.md"):
                    if task_id.lower() in md_file.stem.lower():
                        decision_files.append(str(md_file.relative_to(decision_log_dir)))
            
            details["decision_log"] = {
                "applicable": len(decision_files) > 0,
                "files": decision_files,
                "path": str(decision_log_dir),
            }
        except Exception as exc:
            # AIPOS-R7A2 FIX-2: decision_log 解析失败也显式报错 (BLOCK)
            missing.append(f"decision_log_dir/ (路径解析失败: {exc})")
            details["decision_log"] = {
                "applicable": False,
                "error": str(exc),
            }
    
    # ③ 阶段快照 (stage_archive/)
    # AIPOS-R7A2 FIX-2: stage_archive 路径从 schema 解析,失败显式报错 (同 finalize.py 模式)
    try:
        stage_archive_dir = resolve_governance_path("stage_archive", governance_root, repo_root)
        
        stage_snapshots = []
        if stage_archive_dir.is_dir():
            # 阶段快照 = *.md 文件 (排除 README/index)
            for snapshot_file in stage_archive_dir.glob("*.md"):
                if snapshot_file.name.lower() not in {"readme.md", "index.md"}:
                    stage_snapshots.append(snapshot_file.name)
        
        details["stage_snapshots"] = {
            "applicable": True,
            "snapshots": stage_snapshots,
            "path": str(stage_archive_dir),
        }
        
        # 阶段快照为空时不强制 BLOCK (允许非阶段收口卡无快照)
        if not stage_snapshots and stage_archive_dir.is_dir():
            details["stage_snapshots"]["note"] = "Stage archive directory exists but no snapshots found (OK for non-stage-closure tasks)"
    
    except Exception as exc:
        # AIPOS-R7A2 FIX-2: stage_archive 解析失败显式报错 (BLOCK)
        missing.append(f"stage_archive/ (路径解析失败: {exc})")
        details["stage_snapshots"] = {
            "applicable": False,
            "error": str(exc),
        }
    
    return {
        "complete": len(missing) == 0,
        "missing": missing,
        "details": details,
    }


def governance_commit(
    governance_root: Path,
    task_id: str | None,
    actor: str,
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
    push: bool = True,
    message: str | None = None,
    paths: list[str] | None = None,
    paths_file: Path | str | None = None,
) -> dict[str, Any]:
    """N6 收账提交:校验四件 → commit → push。
    
    AIPOS-F69 大项①: task_id 可选 — 无卡时走治理批次语义(台账追加/裁定入档/契约修正),
    仍走同一校验链与同一 pre-commit 四检,仅跳过本卡台账条目检查。
    
    Args:
        governance_root: 治理仓根目录
        task_id: 任务 ID (可选;无卡时走治理批次语义)
        actor: 执行者
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
        dry_run: 只校验不提交
        push: 是否 push 到远程
        message: commit message (默认自动生成)
        paths: AIPOS-F79 件① 显式白名单(治理根相对路径, 文件或目录); 给了即只 git add -- <paths>
        paths_file: 同上, 每行一路径的清单文件(与 paths 二选一)
    
    Returns:
        {
            "verdict": "PASS" | "BLOCK" | "FAIL",
            "task_id": str | None,
            "actor": str,
            "dry_run": bool,
            "completeness_check": dict,
            "committed": bool,
            "pushed": bool,
            "commit_hash": str | None,
            "message": str,
            "operations": list[str],
            "selected_paths": list[str] | None,   # F79: 白名单(无 --paths 为 None)
            "commit_manifest": dict | None,       # F79: 将提交/已提交文件清单(见 collect_commit_manifest)
        }
    """
    operations = []
    
    # AIPOS-F79 件①: 先归一白名单, 非法即整体拒绝(fail-closed), 不碰 git
    path_selection = resolve_commit_paths(governance_root, paths, paths_file)
    if path_selection["rejected"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": path_selection["message"],
            "operations": operations + ["Blocked: --paths whitelist rejected"],
            "selected_paths": None,
            "rejected_paths": path_selection["rejected"],
            "commit_manifest": None,
        }
    selected_paths: list[str] | None = path_selection["paths"] if path_selection["selected"] else None
    if selected_paths is not None:
        operations.append(f"Path whitelist ({path_selection['source']}): {len(selected_paths)} path(s) — git add限定, 绝不 add -A")
    
    # 校验治理仓目录
    if not governance_root.is_dir():
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Governance root does not exist: {governance_root}",
            "operations": operations,
        }
    
    operations.append(f"Governance root: {governance_root}")
    
    # AIPOS-F7 大项B①: 先查 git status — 无待收内容 → info "无待收内容, 治理仓已最新" + EXIT=0
    # (F4 no-op 档)。放在完整性校验之前:仓已干净则无需校验,避免无变更场景误触 BLOCK。
    # AIPOS-F79: 有 --paths 时 status 限定到白名单 pathspec(选定路径外的变更与本次无关)
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--"] + (selected_paths if selected_paths else []),
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        has_changes = bool(status_result.stdout.strip())
        
        if not has_changes:
            operations.append("No changes to commit" + (" (within --paths)" if selected_paths else ""))
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": None,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": "无待收内容, 治理仓已最新" + ("(选定 --paths 内无变更)" if selected_paths else ""),
                "severity": "info",
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": None,
            }
    except subprocess.CalledProcessError as e:
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git status check failed: {e.stderr}",
            "operations": operations,
        }
    
    # ① 校验 N6 收账清单四件齐全(仅在有变更时校验)
    completeness = check_governance_completeness(governance_root, task_id, repo_root)
    operations.append(f"Completeness check: {'PASS' if completeness['complete'] else 'FAIL'}")
    
    if not completeness["complete"]:
        missing_items = "\n  - ".join(completeness["missing"])
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"N6 收账清单不完整,缺少:\n  - {missing_items}",
            "operations": operations,
        }
    
    # P0 修复(AIPOS-F69-R2): commit 前检查是否有范围外 staged 文件
    # 实锤(2026-09-05): 顾问治理仓有门刚落的账(staged 但未 commit),
    # governance-commit 会把这些也一起 commit,然后 rebase 可能导致问题
    # AIPOS-F79 件③: 检查提到 dry-run 之前(dry-run 必须如实预演 BLOCK), 语义改为:
    #   无 --paths: 任何预暂存 → BLOCK(原样);
    #   有 --paths: 预暂存在 --paths 内 = 纳入清单可接受; 在 --paths 外 = 拒并列出。
    try:
        pre_staged_result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        pre_staged_files = [f.strip() for f in pre_staged_result.stdout.split('\n') if f.strip()]
        
        if pre_staged_files and selected_paths:
            inside_result = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--"] + selected_paths,
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            inside = {f.strip() for f in inside_result.stdout.split('\n') if f.strip()}
            outside = [f for f in pre_staged_files if f not in inside]
            if inside:
                operations.append(f"Pre-staged within --paths: {len(inside)} file(s) accepted into manifest")
            pre_staged_files = outside
        
        if pre_staged_files:
            # 有 staged 文件 → BLOCK(这些可能是门刚落的账)
            operations.append(f"Blocked: {len(pre_staged_files)} pre-staged files detected" + (" outside --paths" if selected_paths else ""))
            file_list = '\n  - '.join(pre_staged_files[:10])
            if len(pre_staged_files) > 10:
                file_list += f'\n  - ... and {len(pre_staged_files) - 10} more'
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": None,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": (
                    f"治理仓有已 staged 但未 committed 的文件"
                    + ("(在 --paths 白名单之外)" if selected_paths else "")
                    + f",必须先处理(fail-closed,禁混入本次提交)。\n\n"
                    f"检测到 {len(pre_staged_files)} 个 staged 文件:\n  - {file_list}\n\n"
                    f"可执行出口:\n"
                    f"1. 先提交这些文件: cd {governance_root} && git commit -m '...'\n"
                    f"2. 或取消 stage: cd {governance_root} && git reset HEAD -- <文件>\n"
                    + ("3. 或把它们加入 --paths 白名单(若确属本批次)\n" if selected_paths else "")
                    + f"{'4' if selected_paths else '3'}. 禁止混入本次提交 — 这些可能是门刚落的账,必须独立提交以保持原子性"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "pre_staged_outside": pre_staged_files,
                "commit_manifest": None,
            }
        
        # AIPOS-F79 件②: 只读取得将提交文件清单(不 add/不 reset/不 stash)
        manifest = collect_commit_manifest(governance_root, selected_paths)
        for w in manifest["warnings"]:
            operations.append(w)
        operations.append(
            f"Manifest ({manifest['mode']}): {manifest['counts']['total']} file(s) — "
            f"modified={manifest['counts'][MANIFEST_MODIFIED]} added={manifest['counts'][MANIFEST_ADDED]} "
            f"deleted={manifest['counts'][MANIFEST_DELETED]} untracked_selected={manifest['counts'][MANIFEST_UNTRACKED]}"
        )
        
        if not manifest["files"]:
            operations.append("No net changes in manifest (nothing would be committed)")
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": "无待收内容, 清单为空(选定路径相对 HEAD 无净变更)",
                "severity": "info",
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
            }
    except subprocess.CalledProcessError as e:
        operations.append(f"Git read-only inspection failed: {e.stderr}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git inspection failed (status/diff): {e.stderr}",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": None,
        }
    except subprocess.TimeoutExpired:
        operations.append("Git read-only inspection timed out")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": "Git inspection timed out (status/diff)",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": None,
        }
    
    if dry_run:
        operations.append("DRY-RUN: Would commit and push the manifest above (scene untouched: no add/reset/stash)")
        listing = "\n".join(f"  {f['status']:<18} {f['path']}" for f in manifest["files"])
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": (
                ("DRY-RUN: N6 收账清单完整,可以提交" if task_id else "DRY-RUN: 治理批次更新检查通过,可以提交")
                + f"\n将提交 {manifest['counts']['total']} 个文件"
                + (f"(--paths 白名单)" if selected_paths else "(整根 add -A 范围)")
                + f":\n{listing}"
                + ("".join(f"\n{w}" for w in manifest["warnings"]) if manifest["warnings"] else "")
            ),
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }
    
    # ③ Commit (会触发 pre-commit 四检)
    if task_id:
        commit_msg = message or f"chore(governance): N6 收账 {task_id}\n\nActor: {actor}\nType: governance_commit"
    else:
        commit_msg = message or f"chore(governance): 治理批次更新\n\nActor: {actor}\nType: governance_commit"
    
    try:
        if selected_paths:
            # AIPOS-F79 件①: 只 stage 白名单 pathspec, 绝不 add -A
            subprocess.run(
                ["git", "add", "--"] + selected_paths,
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            operations.append(f"Staged whitelist only (git add -- {' '.join(selected_paths)} in {governance_root})")
        else:
            # AIPOS-R8B 大项A: Stage all changes in governance repo with pathspec限定到 governance_root
            # 防止 git add -A 越界 stage 其他项目(如 kiwiaiagency)的文件
            # AIPOS-F79: 整根模式仅限 lybra 自身工作区; 他项目一律 --paths
            subprocess.run(
                ["git", "add", "-A", "--", "."],
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            operations.append(f"Staged all governance changes (git add -A -- . in {governance_root})")
        
        # AIPOS-R8B 大项A②: 断言 staged 文件全部落在 governance_root 内,越界即 BLOCK
        staged_files_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        staged_files = [f.strip() for f in staged_files_result.stdout.split('\n') if f.strip()]
        
        # 检查 staged 文件是否全部在 governance_root 内(相对路径不应以 ../ 开头)
        out_of_scope = []
        for staged_file in staged_files:
            # 相对路径以 ../ 开头或包含 ../ 说明越界
            if staged_file.startswith('../') or '/../' in staged_file:
                out_of_scope.append(staged_file)
        
        if out_of_scope:
            operations.append(f"SCOPE VIOLATION: {len(out_of_scope)} staged files outside governance_root")
            out_of_scope_list = '\n  - '.join(out_of_scope[:10])  # 最多列10个
            if len(out_of_scope) > 10:
                out_of_scope_list += f'\n  - ... and {len(out_of_scope) - 10} more'
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": f"SCOPE VIOLATION: Staged files outside governance root (G3 铁律):\n  - {out_of_scope_list}",
                "operations": operations,
            }
        
        # AIPOS-F79 件③: 暂存集必须与清单逐条相同, 否则拒提交并出声(不自动 reset, 禁静默清理)
        staged_set = set(_git_readonly(["diff", "--cached", "--name-only"], governance_root).split("\n")) - {""}
        manifest_set = set(_manifest_repo_paths(manifest))
        if staged_set != manifest_set:
            extra = sorted(staged_set - manifest_set)
            missing_from_stage = sorted(manifest_set - staged_set)
            operations.append(f"MANIFEST MISMATCH before commit: +{len(extra)} unexpected staged, -{len(missing_from_stage)} missing")
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": (
                    "暂存集与 dry-run 清单不一致, 拒绝提交(现场在检查与暂存之间被改动?)。\n"
                    + (f"多出: {extra}\n" if extra else "")
                    + (f"缺少: {missing_from_stage}\n" if missing_from_stage else "")
                    + f"可执行出口: cd {governance_root} && git status && git reset HEAD -- <多出的文件>; 然后重跑 --dry-run 核对清单"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
                "staged_files": sorted(staged_set),
            }
        
        # Commit with explicit identity
        subprocess.run(
            [
                "git",
                "-c", f"user.name={actor}",
                "-c", f"user.email={actor}@lybra.local",
                "commit",
                "-m", commit_msg,
            ],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        
        # Get commit hash
        commit_hash_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = commit_hash_result.stdout.strip()
        operations.append(f"Committed: {commit_hash[:8]}")
        
        # AIPOS-F79 件③: 提交后 git show --name-only HEAD 与清单逐条相同, 不同即 FAIL 出声
        shown_set = set(_git_readonly(["show", "--name-only", "--no-renames", "--format=", "HEAD"], governance_root).split("\n")) - {""}
        if shown_set != manifest_set:
            operations.append("POST-COMMIT VERIFICATION FAILED: git show --name-only HEAD != manifest")
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": True,
                "pushed": False,
                "commit_hash": commit_hash,
                "message": (
                    f"提交 {commit_hash[:8]} 的文件集与清单不一致, 未 push(fail-closed)。\n"
                    f"提交内: {sorted(shown_set)}\n清单: {sorted(manifest_set)}\n"
                    f"可执行出口: cd {governance_root} && git show --name-only HEAD; 核对后决定是否 git reset --soft HEAD~1"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
                "committed_files": sorted(shown_set),
            }
        operations.append(f"Verified: git show --name-only HEAD == manifest ({len(shown_set)} files)")
        
        # ④ Push (N6 语义「漏 push 即未收口」)
        # AIPOS-F69 大项②: 并发安全 — fetch → 若远端前进则只对本项目路径 rebase → push
        pushed = False
        if push:
            try:
                # ① Fetch 远端状态
                subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Fetched from remote")
                
                # ② 检查远端是否前进
                current_branch_result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                current_branch = current_branch_result.stdout.strip()
                
                # 获取本地和远端 HEAD
                local_head_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                local_head = local_head_result.stdout.strip()
                
                remote_head_result = subprocess.run(
                    ["git", "rev-parse", f"origin/{current_branch}"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                remote_head = remote_head_result.stdout.strip()
                
                # ③ 若远端前进且不是当前的祖先,需要 rebase
                if local_head != remote_head:
                    # 检查 local_head 是否是 remote_head 的祖先(即远端是否领先)
                    merge_base_result = subprocess.run(
                        ["git", "merge-base", local_head, remote_head],
                        cwd=str(governance_root),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    merge_base = merge_base_result.stdout.strip()
                    
                    if merge_base != local_head:
                        # 远端已前进,需要 rebase
                        operations.append(f"Remote has advanced ({remote_head[:8]}), rebasing...")
                        
                        # P0 修复(AIPOS-F69-R2): rebase 前检查范围外脏树
                        # 实锤(2026-09-05): 顾问跑验收①时,治理仓有门刚落的账(队列移动+claim记录)未提交,
                        # rebase 把这些全冲掉 → 违反原子性精神内核(别人的未提交状态不是脏数据)
                        # 修法: 有范围外未提交变更 → 拒绝执行并出声,给可执行出口
                        status_result = subprocess.run(
                            ["git", "status", "--porcelain"],
                            cwd=str(governance_root),
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        status_lines = status_result.stdout.strip().split("\n") if status_result.stdout.strip() else []
                        
                        # 过滤出范围外的未提交变更(排除本次已提交的文件)
                        # git status --porcelain 格式: XY filename
                        # X=staged状态, Y=working tree状态
                        # 我们关心的是任何非本次提交的变更(M/A/D/R/C/U/?? 开头,且不在本次提交里)
                        # 本次提交已经 commit 完成,所以任何剩余的变更都是范围外的
                        out_of_scope_changes = []
                        for line in status_lines:
                            if not line.strip():
                                continue
                            # 任何未提交变更(包括 staged 和 unstaged)都是范围外的
                            # 因为本次提交已经完成,工作树应该干净
                            status_code = line[:2]
                            filename = line[3:] if len(line) > 3 else ""
                            # 排除未追踪文件(??)- 这些不会被 rebase 影响
                            if status_code != "??":
                                out_of_scope_changes.append(line)
                        
                        if out_of_scope_changes:
                            # 有范围外未提交变更 → BLOCK
                            operations.append(f"Blocked: {len(out_of_scope_changes)} out-of-scope uncommitted changes detected")
                            return {
                                "verdict": Verdict.BLOCK,
                                "task_id": task_id,
                                "actor": actor,
                                "dry_run": False,
                                "completeness_check": completeness,
                                "committed": True,
                                "pushed": False,
                                "commit_hash": commit_hash,
                                "message": (
                                    f"治理仓有未落库的变更,rebase 前必须处理(fail-closed,禁自动清理)。\n\n"
                                    f"检测到 {len(out_of_scope_changes)} 个范围外未提交变更:\n"
                                    + "\n".join(f"  {line}" for line in out_of_scope_changes[:10])
                                    + (f"\n  ... 还有 {len(out_of_scope_changes) - 10} 个" if len(out_of_scope_changes) > 10 else "")
                                    + "\n\n可执行出口:\n"
                                    f"1. 先落库其它变更: cd {governance_root} && lybra governance-commit ...\n"
                                    f"2. 或明示处理(stash/reset): cd {governance_root} && git status\n"
                                    f"3. 禁用任何形式的自动清理 — 这些可能是门刚落的账"
                                ),
                                "operations": operations,
                            }
                        
                        # AIPOS-F69 大项②: 只 rebase 本项目路径 — 复用 R6M 既有 pathspec
                        # 这里的 governance_root 就是本项目的工作区,因为每个项目有自己的治理工作区
                        # 在当前工作树 cwd 下执行 rebase,git 只会处理当前目录内的冲突
                        try:
                            subprocess.run(
                                ["git", "rebase", f"origin/{current_branch}"],
                                cwd=str(governance_root),
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=30,
                            )
                            # AIPOS-F79 顺手修 F69 隐患: rebase 会改写本次 commit 的哈希, 后续
                            # push 后校验必须用改写后的 HEAD, 否则真 rebase 场景必报「远端不含」假阴性
                            # (F69 夹具的 clone 落在 master 分支, 从未跑过真 rebase, 故当时未暴露)。
                            rebased_head = _git_readonly(["rev-parse", "HEAD"], governance_root).strip()
                            if rebased_head != commit_hash:
                                operations.append(f"Rebased successfully (commit rewritten {commit_hash[:8]} -> {rebased_head[:8]})")
                                commit_hash = rebased_head
                            else:
                                operations.append("Rebased successfully")
                        except subprocess.CalledProcessError as e:
                            # 冲突 → 拒收并给可执行出口
                            subprocess.run(
                                ["git", "rebase", "--abort"],
                                cwd=str(governance_root),
                                capture_output=True,
                                text=True,
                            )
                            operations.append("Rebase failed: conflicts detected, aborted")
                            return {
                                "verdict": Verdict.BLOCK,
                                "task_id": task_id,
                                "actor": actor,
                                "dry_run": False,
                                "completeness_check": completeness,
                                "committed": True,
                                "pushed": False,
                                "commit_hash": commit_hash,
                                "message": (
                                    f"Rebase 冲突，拒绝提交。\n\n"
                                    f"冲突输出:\n{e.stderr}\n\n"
                                    f"可执行出口:\n"
                                    f"1. 手动解决冲突: cd {governance_root} && git pull --rebase\n"
                                    f"2. 或等待其他项目提交完成后重试"
                                ),
                                "operations": operations,
                            }
                
                # ④ Push 到远端
                subprocess.run(
                    ["git", "push"],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Pushed to remote")
                
                # AIPOS-F69 大项③: push 后校验远端确实包含本次 commit
                # 禁“push 返回 0 即报成功”
                verify_result = subprocess.run(
                    ["git", "branch", "-r", "--contains", commit_hash],
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                remote_branches = verify_result.stdout.strip()
                
                if not remote_branches:
                    operations.append(f"VERIFICATION FAILED: commit {commit_hash[:8]} not found in remote")
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "completeness_check": completeness,
                        "committed": True,
                        "pushed": False,  # push 命令成功但验证失败 = 未真正 push
                        "commit_hash": commit_hash,
                        "message": (
                            f"Push 命令返回成功,但远端不包含 commit {commit_hash[:8]}\n\n"
                            f"可能原因:\n"
                            f"1. Push 到只读/落后 ref\n"
                            f"2. 网络延迟造成的短暂不一致\n\n"
                            f"可执行出口:\n"
                            f"1. 手动检查: git branch -r --contains {commit_hash[:8]}\n"
                            f"2. 重试 push: cd {governance_root} && git push"
                        ),
                        "operations": operations,
                    }
                
                operations.append(f"Verified commit {commit_hash[:8]} exists in remote: {remote_branches.split()[0]}")
                pushed = True
                
            except subprocess.CalledProcessError as e:
                operations.append(f"Push failed: {e.stderr}")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "completeness_check": completeness,
                    "committed": True,
                    "pushed": False,
                    "commit_hash": commit_hash,
                    "message": f"Committed but push failed: {e.stderr}",
                    "operations": operations,
                }
            except subprocess.TimeoutExpired:
                operations.append("Push timed out after 30s")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "completeness_check": completeness,
                    "committed": True,
                    "pushed": False,
                    "commit_hash": commit_hash,
                    "message": "Committed but push timed out",
                    "operations": operations,
                }
        
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": True,
            "pushed": pushed,
            "commit_hash": commit_hash,
            "message": (
                f"N6 收账完成: {commit_hash[:8]}" if task_id
                else f"治理批次更新完成: {commit_hash[:8]}"
            ) + (" (pushed)" if pushed else ""),
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }
        
    except subprocess.CalledProcessError as e:
        operations.append(f"Git operation failed: {e.stderr}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Commit failed: {e.stderr}",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }
    except subprocess.TimeoutExpired as e:
        operations.append(f"Git operation timed out: {e.cmd}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git operation timed out: {e.cmd}",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
