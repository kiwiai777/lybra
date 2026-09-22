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

AIPOS-F79C push 假阴性热修(chris 总顾问阻塞, 2026-09-21 --json 取证):
- 件①: 旧判据 ``merge-base(local, remote) != local`` 在本地刚提交后恒真 → 永远 rebase → F69-R2 越界脏树
  检查把共享仓里他项目的未提交修改也算进 → pushed=False。改为 fetch 后双向 ``rev-list --count``:
  远端独有=0 → 直接 fast-forward push, 不 rebase、不做脏树检查。
- 件②: 远端独有>0 → 在仓外临时 linked worktree(``git worktree add --detach``)上 cherry-pick 本地独有
  提交并在那里 push; 共享检出/索引/他项目未提交文件全程不动; 冲突 → 清理临时 worktree、精确列冲突文件、
  BLOCK, 本地 commit 保留。成功后本地分支用 ``git reset --keep origin/<branch>`` 跟到远端(只改远端提交
  改过的文件, 有本地改动即整体中止只报告)。
- 件③: pushed=False 一律 verdict != PASS(CLI 非零退出), operations 末条 = ``PUSH NOT DONE: <原因>``;
  F79 的 manifest 核对保留。
- 件④: 目录 pathspec 内的删除用 ``git add -A -- <paths>`` 暂存(范围仍限 --paths); 暂存集/预暂存集一律
  ``--no-renames`` 与清单同口径(病根: ``diff --cached --name-only`` 把「删旧路径+加新路径」的队列搬动折叠成
  一条重命名, 删除项就「missing」); mismatch 时 ``git reset -- <paths>`` 清掉本次暂存再拒, 不留半成品。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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


# ---------------------------------------------------------------------------
# AIPOS-F79C: push 判据(双向 rev-list)+ 临时 linked worktree 整合 + pushed=False 出声
# ---------------------------------------------------------------------------

_TMP_WORKTREE_PREFIX = "lybra-governance-commit-"
PUSH_NOT_DONE_PREFIX = "PUSH NOT DONE: "


def _git_run(args: list[str], cwd: Path, *, timeout: int = 60) -> subprocess.CompletedProcess:
    """写操作 git 调用(fetch/push/add/reset/worktree/cherry-pick); 失败抛 CalledProcessError, 禁静默。"""
    return subprocess.run(
        _GIT_QUOTEPATH_OFF + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _divergence(governance_root: Path, upstream: str) -> tuple[int, int]:
    """件① 判据: (远端独有提交数, 本地独有提交数) = rev-list --count HEAD..upstream / upstream..HEAD。"""
    remote_only = int(_git_readonly(["rev-list", "--count", f"HEAD..{upstream}"], governance_root).strip())
    local_only = int(_git_readonly(["rev-list", "--count", f"{upstream}..HEAD"], governance_root).strip())
    return remote_only, local_only


def _local_commits_not_upstream(governance_root: Path, upstream: str) -> list[str]:
    """本地独有且其补丁(patch-id)尚未在远端的提交, 旧→新(即 rebase 会重放的集合)。"""
    raw = _git_readonly(
        ["rev-list", "--reverse", "--right-only", "--cherry-pick", f"{upstream}...HEAD"], governance_root
    )
    return [line for line in raw.split("\n") if line]


def _status_paths(governance_root: Path) -> set[str]:
    """共享检出里所有未提交状态的仓根相对路径(含已暂存/未暂存/未跟踪), 只读。"""
    tokens = _split_nul(
        _git_readonly(["status", "--porcelain", "--untracked-files=all", "-z"], governance_root)
    )
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        xy, repo_path = entry[:2], entry[3:]
        paths.add(repo_path)
        if xy[0] in ("R", "C") and i < len(tokens):
            paths.add(tokens[i])  # 重命名/复制的源路径
            i += 1
    return paths


def _dirty_overlap_with(governance_root: Path, upstream: str) -> list[str]:
    """把本地分支移到 upstream 会改写的文件(HEAD vs upstream)中, 本地有未提交状态的那些(fail-closed 前置守卫)。"""
    changed = set(_split_nul(_git_readonly(["diff", "--name-only", "-z", "HEAD", upstream], governance_root)))
    return sorted(changed & _status_paths(governance_root))


def integrate_in_temp_worktree(
    governance_root: Path,
    branch: str,
    commits: list[str],
    actor: str,
    operations: list[str],
) -> dict[str, Any]:
    """AIPOS-F79C 件②: 在 <repo>/.git 之外的临时 linked worktree 上 cherry-pick 本地独有提交并 push。

    共享检出的工作树、索引、他项目未提交文件全程不动;禁 stash/禁整根 add -A/禁 pull --rebase。
    cherry-pick 冲突 → --abort, 精确取 unmerged 文件清单, 清理临时 worktree, 返回 ok=False(调用方 BLOCK,
    本地 commit 保留)。临时 worktree 在 finally 里无条件清理(remove --force + rmtree + prune)。

    Returns:
        {"ok": bool, "pushed_tip": str|None, "conflict_files": [repo_path...], "reason": str|None,
         "temp_worktree": str, "cleaned": bool}
    """
    tmp_root = Path(tempfile.mkdtemp(prefix=_TMP_WORKTREE_PREFIX))
    wt = tmp_root / "wt"
    upstream = f"origin/{branch}"
    outcome: dict[str, Any] = {
        "ok": False, "pushed_tip": None, "conflict_files": [], "reason": None,
        "temp_worktree": str(wt), "cleaned": False,
    }
    try:
        _git_run(["worktree", "add", "--detach", str(wt), upstream], governance_root, timeout=120)
        operations.append(f"Temp worktree added (detached at {upstream}): {wt}")
        try:
            _git_run(
                ["-c", f"user.name={actor}", "-c", f"user.email={actor}@lybra.local", "cherry-pick", *commits],
                wt, timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            conflict_files = sorted(
                _split_nul(_git_readonly(["diff", "--name-only", "--diff-filter=U", "-z"], wt))
            )
            try:
                _git_run(["cherry-pick", "--abort"], wt, timeout=60)
            except subprocess.CalledProcessError as abort_exc:
                operations.append(
                    f"WARNING: git cherry-pick --abort in temp worktree failed: {(abort_exc.stderr or '').strip()[:200]}"
                    f" (worktree is removed anyway)"
                )
            stderr_tail = (exc.stderr or "").strip().replace("\n", " | ")[:400]
            outcome["conflict_files"] = conflict_files
            outcome["reason"] = (
                f"cherry-pick conflict in temp worktree: {len(conflict_files)} file(s): {', '.join(conflict_files)}"
                if conflict_files
                else f"cherry-pick failed in temp worktree (no unmerged files): {stderr_tail}"
            )
            operations.append(
                f"Cherry-pick failed in temp worktree ({len(commits)} commit(s)); conflict files: "
                f"{conflict_files if conflict_files else '(none) ' + stderr_tail}; aborted"
            )
            return outcome
        tip = _git_readonly(["rev-parse", "HEAD"], wt).strip()
        operations.append(f"Cherry-picked {len(commits)} commit(s) onto {upstream} in temp worktree -> {tip[:8]}")
        _git_run(["push", "origin", f"HEAD:refs/heads/{branch}"], wt, timeout=30)
        outcome["ok"] = True
        outcome["pushed_tip"] = tip
        return outcome
    finally:
        if wt.exists():
            try:
                _git_run(["worktree", "remove", "--force", str(wt)], governance_root, timeout=60)
            except subprocess.CalledProcessError as rm_exc:
                operations.append(
                    f"WARNING: git worktree remove failed: {(rm_exc.stderr or '').strip()[:200]}; falling back to rmtree + prune"
                )
        try:
            shutil.rmtree(tmp_root)
        except OSError as os_exc:
            operations.append(f"WARNING: could not delete temp dir {tmp_root}: {os_exc.__class__.__name__}: {os_exc}")
        try:
            _git_run(["worktree", "prune"], governance_root, timeout=60)
        except subprocess.CalledProcessError as prune_exc:
            operations.append(f"WARNING: git worktree prune failed: {(prune_exc.stderr or '').strip()[:200]}")
        outcome["cleaned"] = not wt.exists() and not tmp_root.exists()
        operations.append(f"Temp worktree cleaned: {outcome['cleaned']}")


def _push_not_done(
    base: dict[str, Any],
    operations: list[str],
    *,
    reason: str,
    message: str,
    verdict: str,
) -> dict[str, Any]:
    """件③: pushed=False 的统一出口 — verdict 永不为 PASS, operations 末条 = PUSH NOT DONE: <原因>。"""
    assert verdict != Verdict.PASS
    operations.append(PUSH_NOT_DONE_PREFIX + reason)
    base.update({
        "verdict": verdict,
        "pushed": False,
        "message": message,
        "operations": operations,
        "push_result": {"pushed": False, "reason": reason},
    })
    return base


def _seal_push_outcome(result: dict[str, Any], *, push_requested: bool) -> dict[str, Any]:
    """件③ 总闸(唯一提交路径末端): 只要请求了 push 而 pushed=False, verdict 不得为 PASS,
    operations 末条必为 "PUSH NOT DONE: <原因>"。唯一例外: 无待收内容且本地无未推提交(真无事可推, EXIT=0)。
    """
    result["push_requested"] = push_requested
    if not push_requested:
        result.setdefault("push_result", None)
        return result
    ops = result.setdefault("operations", [])
    if result.get("pushed"):
        result["push_result"] = {"pushed": True, "reason": None}
        if not ops or not ops[-1].startswith("Push result:"):
            ops.append(f"Push result: pushed {str(result.get('commit_hash') or '')[:8]}")
        return result
    if result.get("severity") == "info" and not result.get("committed") and not result.get("unpushed_local_commits"):
        result["push_result"] = {"pushed": False, "reason": "nothing to push (no changes, no unpushed local commits)"}
        return result
    existing = result.get("push_result") or {}
    reason = existing.get("reason")
    if not reason:
        first_line = (result.get("message") or "").strip().split("\n", 1)[0]
        reason = (
            f"not committed ({result.get('verdict')}): {first_line}"
            if not result.get("committed")
            else f"committed {str(result.get('commit_hash') or '')[:8]} but not pushed ({result.get('verdict')}): {first_line}"
        )
    if result.get("verdict") == Verdict.PASS:
        result["verdict"] = Verdict.FAIL
    result["push_result"] = {"pushed": False, "reason": reason}
    if not ops or not ops[-1].startswith(PUSH_NOT_DONE_PREFIX):
        ops.append(PUSH_NOT_DONE_PREFIX + reason)
    return result


def _unpushed_local_count(governance_root: Path) -> int | None:
    """无待收内容路径用: 本地相对「上次 fetch 到的」origin/<branch> 的未推提交数(不联网); 无上游/detached → None。"""
    try:
        branch = _git_readonly(["rev-parse", "--abbrev-ref", "HEAD"], governance_root).strip()
        if branch == "HEAD":
            return None
        return int(_git_readonly(["rev-list", "--count", f"origin/{branch}..HEAD"], governance_root).strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


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
    """N6 收账提交:校验四件 → commit → push(唯一提交口; AIPOS-F79C 件③ 总闸在此封口)。"""
    result = _governance_commit_impl(
        governance_root, task_id, actor,
        repo_root=repo_root, dry_run=dry_run, push=push, message=message, paths=paths, paths_file=paths_file,
    )
    return _seal_push_outcome(result, push_requested=bool(push) and not dry_run)


def _run_guardrails_on_manifest(
    governance_root: Path,
    manifest: dict[str, Any],
    repo_root: Path | None,
    operations: list[str],
) -> dict[str, Any] | None:
    """AIPOS-F79D 件①: 对清单跑 governance_guardrails(B①–B⑤), 与 pre-commit hook 同模块同文案。

    通过 → None(operations 记一行); 拒/声明不可用 → 返回 BLOCK/FAIL 结果骨架(调用方补上下文字段)。
    读工作树文件内容判 B②/B④, 与 hook 一致(hook 提交的也是工作树状态: add 后 commit)。
    """
    from tools.aipos_cli.governance_guardrails import (
        GuardrailDeclarationError,
        git_current_branch,
        git_toplevel,
        manifest_entries,
        resolve_schema_dir,
        run_guardrails,
    )

    try:
        git_root = git_toplevel(governance_root)
        report, _decls, text = run_guardrails(
            git_root,
            manifest_entries(manifest),
            schema_dir=resolve_schema_dir(repo_root),
            current_branch=git_current_branch(git_root),
        )
    except (GuardrailDeclarationError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        operations.append(f"Guardrails (governance_guardrails, same module as pre-commit hook): FAIL — {exc}")
        return {
            "verdict": Verdict.FAIL,
            "message": f"治理仓四检模块不可用, 拒绝放行(fail-closed): {exc}",
            "operations": operations,
            "guardrail_report": None,
        }
    if report.ok:
        operations.append(
            f"Guardrails (governance_guardrails, same module as pre-commit hook): PASS — {report.checked} file(s) checked"
        )
        return None
    operations.append(
        f"Guardrails (governance_guardrails, same module as pre-commit hook): BLOCK — "
        f"{len(report.violations)} violation(s)" + ("" if report.branch_ok else f", branch={report.branch} != main")
    )
    return {
        "verdict": Verdict.BLOCK,
        "message": text,
        "operations": operations,
        "guardrail_report": report.to_dict(),
        "rejected_files": report.to_dict()["rejected_files"],
    }


def _governance_commit_impl(
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
            # AIPOS-F79C 件③: 「治理仓已最新」不能在本地还有未推提交时说出口(chris 8 条未推提交静默的病根)
            unpushed = _unpushed_local_count(governance_root) if (push and not dry_run) else None
            if unpushed:
                operations.append(f"Unpushed local commits vs last-fetched origin: {unpushed}")
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
                        f"无待收内容, 但本地有 {unpushed} 条提交尚未推到远端(治理仓并未「已最新」)。\n"
                        f"可执行出口: cd {governance_root} && git fetch origin && git log --oneline @{{u}}..HEAD; "
                        f"确认后 git push(远端未前进时为 fast-forward)"
                    ),
                    "operations": operations,
                    "selected_paths": selected_paths,
                    "commit_manifest": None,
                    "unpushed_local_commits": unpushed,
                    "push_result": {"pushed": False, "reason": f"nothing new committed; {unpushed} local commit(s) not on origin"},
                }
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
    # AIPOS-F79C 件④: 预暂存/暂存集一律 --no-renames, 与清单(diff --no-renames)及 git show --no-renames 同口径
    pre_staged_inside: set[str] = set()
    try:
        pre_staged_result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--no-renames"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        pre_staged_files = [f.strip() for f in pre_staged_result.stdout.split('\n') if f.strip()]

        if pre_staged_files and selected_paths:
            inside_result = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--no-renames", "--"] + selected_paths,
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            inside = {f.strip() for f in inside_result.stdout.split('\n') if f.strip()}
            outside = [f for f in pre_staged_files if f not in inside]
            if inside:
                operations.append(f"Pre-staged within --paths: {len(inside)} file(s) accepted into manifest")
            pre_staged_inside = inside
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
    
    # AIPOS-F79D 件①: dry-run 与正式提交前对同一「将提交清单」跑同一四检模块(hook 也调它), 拒因文案同源。
    # 病根: 2026-09-22 chris dry-run PASS 而正式提交被 hook exit 1 —— 预演从不跑 B①–B④。
    guardrail_outcome = _run_guardrails_on_manifest(governance_root, manifest, repo_root, operations)
    if guardrail_outcome is not None:
        guardrail_outcome.update({
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        })
        return guardrail_outcome

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

    def _result_base(*, committed: bool, commit_hash: str | None) -> dict[str, Any]:
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": committed,
            "pushed": False,
            "commit_hash": commit_hash,
            "message": "",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }

    stage_pathspec = list(selected_paths) if selected_paths else ["."]

    def _unstage_this_run(saved_index_tree: str | None) -> None:
        """AIPOS-F79C 件④: 拒提交时清掉本次暂存(git reset -- <pathspec>, 范围仍限 --paths), 不留半成品;
        --paths 内原有的预暂存条目从暂存前的索引快照精确还原(不动工作树)。"""
        _git_run(["reset", "-q", "--"] + stage_pathspec, governance_root)
        operations.append(f"Unstaged this run's staging (git reset -- {' '.join(stage_pathspec)})")
        if pre_staged_inside and saved_index_tree:
            # 预暂存路径来自 diff --cached(仓根相对), 而 cwd 是治理根 → 用 :/ 顶层 pathspec 魔法
            _git_run(
                ["restore", "--staged", f"--source={saved_index_tree}", "--"]
                + [f":/{p}" for p in sorted(pre_staged_inside)],
                governance_root,
            )
            operations.append(f"Restored {len(pre_staged_inside)} pre-staged entr(y/ies) within --paths from index snapshot")

    try:
        # 件④: 暂存前对索引拍快照(write-tree), 仅用于 mismatch 时精确还原 --paths 内的预暂存条目
        saved_index_tree = _git_readonly(["write-tree"], governance_root).strip() if pre_staged_inside else None
        if selected_paths:
            # AIPOS-F79 件①: 只 stage 白名单 pathspec, 绝不整根 add -A
            # AIPOS-F79C 件④: -A 限定在 <paths> 内 — 目录 pathspec 内的删除也必须暂存(这不是整根 add -A)
            subprocess.run(
                ["git", "add", "-A", "--"] + selected_paths,
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            operations.append(f"Staged whitelist only (git add -A -- {' '.join(selected_paths)} in {governance_root}; deletions within these paths included)")
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
            ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--no-renames"],
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
            _unstage_this_run(saved_index_tree)
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
                "index_restored": True,
            }
        
        # AIPOS-F79 件③: 暂存集必须与清单逐条相同, 否则拒提交并出声
        # AIPOS-F79C 件④: 同口径 --no-renames; mismatch 时清掉本次暂存(出声记录, 非静默)再拒, 不留半成品
        staged_set = set(_git_readonly(["diff", "--cached", "--name-only", "--no-renames"], governance_root).split("\n")) - {""}
        manifest_set = set(_manifest_repo_paths(manifest))
        if staged_set != manifest_set:
            extra = sorted(staged_set - manifest_set)
            missing_from_stage = sorted(manifest_set - staged_set)
            operations.append(f"MANIFEST MISMATCH before commit: +{len(extra)} unexpected staged, -{len(missing_from_stage)} missing")
            _unstage_this_run(saved_index_tree)
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
                    "暂存集与 dry-run 清单不一致, 拒绝提交(现场在检查与暂存之间被改动?)。本次暂存已清掉(git reset -- <paths>), 暂存区不留半成品。\n"
                    + (f"多出: {extra}\n" if extra else "")
                    + (f"缺少: {missing_from_stage}\n" if missing_from_stage else "")
                    + f"可执行出口: cd {governance_root} && git status; 然后重跑 --dry-run 核对清单后再提交"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
                "staged_files": sorted(staged_set),
                "index_restored": True,
            }
        
        # Commit with explicit identity
        # AIPOS-F79D 件③: commit 子进程非零退出(pre-commit hook 拒)也走 _unstage_this_run —— 还原预暂存快照,
        # 范围仍限 --paths; 结果带 index_restored=true 与被拒清单。病根: 24 条留 index 反过来挡他项目提交。
        try:
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
        except subprocess.CalledProcessError as exc:
            hook_output = ((exc.stdout or "") + (exc.stderr or "")).strip()
            operations.append(f"Commit rejected by git/pre-commit hook (exit {exc.returncode})")
            _unstage_this_run(saved_index_tree)
            rejected_files = sorted(manifest_set)
            result = _result_base(committed=False, commit_hash=None)
            result["verdict"] = Verdict.BLOCK
            result["message"] = (
                f"治理仓 pre-commit 拒绝提交(exit {exc.returncode}); 本次暂存已清掉(git reset -- {' '.join(stage_pathspec)}), "
                f"预暂存条目已按快照还原, 暂存区不留半成品。\n\n"
                f"被拒清单({len(rejected_files)}):\n  - " + "\n  - ".join(rejected_files) + "\n\n"
                f"hook 输出:\n{hook_output}\n\n"
                f"可执行出口: 按 hook 输出修正文件后重跑同一命令(先 --dry-run: 同一四检模块, 拒因逐字相同)"
            )
            result["index_restored"] = True
            result["rejected_files"] = rejected_files
            result["hook_output"] = hook_output
            return result
        
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
        # AIPOS-F79C 件①: fetch 后双向 rev-list 判分歧 — 远端独有=0 → 直接 fast-forward push,
        #   不 rebase、不做脏树检查(F69-R2 的越界脏树检查只对「真需整合」有意义, 且它把共享仓
        #   里他项目的未提交修改也算进去 → 恒 BLOCK = 2026-09-21 chris 阻塞的病根之一;
        #   另一病根: 旧判据 merge-base(local, remote) != local 在本地刚提交后恒真 → 永远走 rebase)。
        # AIPOS-F79C 件②: 远端独有>0 → 在仓外临时 linked worktree 上 cherry-pick 本地独有提交并
        #   在该 worktree push; 共享检出的工作树/索引/他项目未提交文件全程不动(禁 stash/禁整根
        #   add -A/禁 pull --rebase); 冲突 → 清理临时 worktree, 精确列出冲突文件, BLOCK, 本地 commit 保留。
        # AIPOS-F79C 件③: 任何 pushed=False 都出声 — 由 governance_commit() 的 _seal_push_outcome 统一
        #   保证 verdict != PASS 且 operations 末条 = "PUSH NOT DONE: <原因>"。
        pushed = False
        pushed_commit_hash: str | None = None
        if push:
            try:
                # ① Fetch 远端状态
                _git_run(["fetch", "origin"], governance_root, timeout=30)
                operations.append("Fetched from remote")

                current_branch = _git_readonly(["rev-parse", "--abbrev-ref", "HEAD"], governance_root).strip()
                if current_branch == "HEAD":
                    return _push_not_done(
                        _result_base(committed=True, commit_hash=commit_hash),
                        operations,
                        reason="detached HEAD: no branch to push",
                        message=f"已提交 {commit_hash[:8]}, 但治理仓处于 detached HEAD, 无分支可推。\n可执行出口: cd {governance_root} && git switch <branch> 后重试",
                        verdict=Verdict.BLOCK,
                    )
                upstream = f"origin/{current_branch}"
                try:
                    _git_readonly(["rev-parse", "--verify", "--quiet", f"{upstream}^{{commit}}"], governance_root)
                except subprocess.CalledProcessError:
                    return _push_not_done(
                        _result_base(committed=True, commit_hash=commit_hash),
                        operations,
                        reason=f"no remote-tracking branch {upstream} after fetch",
                        message=(
                            f"已提交 {commit_hash[:8]}, 但 fetch 后不存在 {upstream}(远端无此分支, 或 remote 未配置)。\n"
                            f"可执行出口: cd {governance_root} && git push -u origin {current_branch}"
                        ),
                        verdict=Verdict.FAIL,
                    )

                # ② 件①判据: 双向 rev-list(远端独有 / 本地独有)
                remote_only, local_only = _divergence(governance_root, upstream)
                operations.append(
                    f"Divergence vs {upstream}: remote-only={remote_only} local-only={local_only} "
                    f"(git rev-list --count HEAD..{upstream} / {upstream}..HEAD)"
                )

                if remote_only == 0:
                    # 远端未前进 → 直接 fast-forward push; 不 rebase、不做脏树检查、不碰工作树
                    _git_run(["push", "origin", f"HEAD:refs/heads/{current_branch}"], governance_root, timeout=30)
                    pushed_commit_hash = commit_hash
                    push_mode = "fast-forward"
                    operations.append(
                        f"Pushed to remote (fast-forward: {upstream} had not advanced; no rebase, no dirty-tree check, "
                        f"shared checkout untouched)"
                    )
                else:
                    # 件②: 远端已前进 → 临时 linked worktree 上 cherry-pick + push
                    operations.append(
                        f"Remote has advanced ({remote_only} commit(s) on {upstream} not in local); integrating in a "
                        f"temporary linked worktree outside the shared checkout (no stash / no in-place rebase)"
                    )
                    commits = _local_commits_not_upstream(governance_root, upstream)
                    if commit_hash not in commits:
                        return _push_not_done(
                            _result_base(committed=True, commit_hash=commit_hash),
                            operations,
                            reason=f"commit {commit_hash[:8]} not found among local-only commits vs {upstream}",
                            message=(
                                f"已提交 {commit_hash[:8]}, 但 git rev-list --right-only --cherry-pick {upstream}...HEAD "
                                f"未列出它(现场在提交与 push 之间被改动?), 拒绝整合(fail-closed)。\n"
                                f"可执行出口: cd {governance_root} && git log --oneline {upstream}...HEAD"
                            ),
                            verdict=Verdict.FAIL,
                        )
                    if len(commits) > 1:
                        operations.append(
                            f"Local branch also carries {len(commits) - 1} earlier unpushed commit(s) not on {upstream}; "
                            f"they are integrated together in order (a fast-forward push would carry them too)"
                        )
                    outcome = integrate_in_temp_worktree(
                        governance_root, current_branch, commits, actor, operations
                    )
                    if not outcome["ok"]:
                        conflict_files = outcome["conflict_files"]
                        listing = "\n  - ".join(conflict_files) if conflict_files else "(无 unmerged 文件; 见 cherry-pick 输出)"
                        base = _result_base(committed=True, commit_hash=commit_hash)
                        base["conflict_files"] = conflict_files
                        base["temp_worktree_cleaned"] = outcome["cleaned"]
                        return _push_not_done(
                            base,
                            operations,
                            reason=outcome["reason"],
                            message=(
                                f"已提交 {commit_hash[:8]}(本地保留), 但与 {upstream} 整合冲突, 未 push(fail-closed)。\n\n"
                                f"冲突文件({len(conflict_files)}):\n  - {listing}\n\n"
                                f"临时 worktree 已清理: {outcome['cleaned']}; 共享检出/索引/他项目未提交文件未动。\n\n"
                                f"可执行出口(先落库冲突文件; 产品尚无 --resolve 出口, 以下为同款临时 worktree 手法, 不碰共享检出):\n"
                                f"1. 看远端版本: cd {governance_root} && git diff HEAD {upstream} -- <冲突文件>\n"
                                f"2. 在仓外临时 worktree 解决并推: git worktree add --detach /tmp/gc-resolve {upstream} && cd /tmp/gc-resolve && "
                                f"git cherry-pick {commit_hash[:8]} → 手工合并冲突文件 → git add -- <冲突文件> && git cherry-pick --continue && "
                                f"git push origin HEAD:refs/heads/{current_branch}\n"
                                f"3. 回治理根跟进并清理: cd {governance_root} && git fetch origin && git reset --keep {upstream} && "
                                f"git worktree remove /tmp/gc-resolve\n"
                                f"4. 禁 stash/禁整根 add -A/禁 pull --rebase(F69 铁律); 不要在共享检出里改冲突文件后重跑(原 commit 会再次冲突)"
                            ),
                            verdict=Verdict.BLOCK,
                        )
                    pushed_commit_hash = outcome["pushed_tip"]
                    push_mode = f"cherry-picked in temp worktree, {len(commits)} commit(s) rewritten"
                    operations.append(
                        f"Pushed to remote from temp worktree ({commit_hash[:8]} -> {pushed_commit_hash[:8]}; "
                        f"temp worktree cleaned={outcome['cleaned']})"
                    )

                    # 成功后把本地分支 fast-forward 到远端: fetch → 若本地所有提交都已(按 patch)在远端 → 移动分支.
                    # 因 cherry-pick 改写了哈希, 字面 `git merge --ff-only` 不可能成立(本地有原 commit 对象);
                    # 语义等价物 = `git reset --keep origin/<branch>`: 只更新「远端提交改动过」的文件, 遇到本地
                    # 有改动的同名文件即整体中止(fail-closed); 他项目未提交文件与未跟踪文件全程不动。
                    _git_run(["fetch", "origin"], governance_root, timeout=30)
                    remaining = _local_commits_not_upstream(governance_root, upstream)
                    if remaining:
                        operations.append(
                            f"Local branch NOT moved: {len(remaining)} local commit(s) still not on {upstream} "
                            f"({', '.join(c[:8] for c in remaining)}); reported only, nothing touched"
                        )
                    else:
                        overlap = _dirty_overlap_with(governance_root, upstream)
                        if overlap:
                            operations.append(
                                f"Local branch NOT moved: {len(overlap)} locally modified/untracked file(s) would be "
                                f"overwritten by {upstream}: {', '.join(overlap[:10])}"
                                + (f" (+{len(overlap) - 10} more)" if len(overlap) > 10 else "")
                                + f"; reported only. Exit: save them, then cd {governance_root} && git reset --keep {upstream}"
                            )
                        else:
                            try:
                                _git_run(["reset", "--keep", upstream], governance_root, timeout=60)
                            except subprocess.CalledProcessError as exc:
                                operations.append(
                                    f"WARNING: Local branch NOT moved: git reset --keep {upstream} refused: "
                                    f"{(exc.stderr or '').strip()[:300]}; nothing overwritten, reported only"
                                )
                            else:
                                commit_hash = _git_readonly(["rev-parse", "HEAD"], governance_root).strip()
                                operations.append(
                                    f"Local branch fast-forwarded to {upstream} ({commit_hash[:8]}) via git reset --keep "
                                    f"(only files changed by remote commits updated; other projects' uncommitted files untouched)"
                                )

                # AIPOS-F69 大项③: push 后校验远端确实包含本次 commit(禁「push 返回 0 即报成功」)
                remote_branches = _git_readonly(
                    ["branch", "-r", "--contains", pushed_commit_hash], governance_root
                ).strip()
                if not remote_branches:
                    return _push_not_done(
                        _result_base(committed=True, commit_hash=commit_hash),
                        operations,
                        reason=f"push returned 0 but {pushed_commit_hash[:8]} not found in any remote branch",
                        message=(
                            f"Push 命令返回成功,但远端不包含 commit {pushed_commit_hash[:8]}\n\n"
                            f"可执行出口:\n"
                            f"1. 手动检查: git branch -r --contains {pushed_commit_hash[:8]}\n"
                            f"2. 重试: cd {governance_root} && lybra governance-commit ...(本次 commit 保留)"
                        ),
                        verdict=Verdict.FAIL,
                    )
                operations.append(
                    f"Verified commit {pushed_commit_hash[:8]} exists in remote: {remote_branches.split()[0]}"
                )
                pushed = True
                # 件③: --json operations 末条 = push 结果
                operations.append(f"Push result: pushed {pushed_commit_hash[:8]} to {upstream} ({push_mode})")

            except subprocess.CalledProcessError as e:
                return _push_not_done(
                    _result_base(committed=True, commit_hash=commit_hash),
                    operations,
                    reason=f"git {' '.join(e.cmd[3:5]) if isinstance(e.cmd, list) and len(e.cmd) > 4 else 'command'} failed: {(e.stderr or '').strip()[:300]}",
                    message=f"Committed {commit_hash[:8]} but push failed: {e.stderr}",
                    verdict=Verdict.FAIL,
                )
            except subprocess.TimeoutExpired as e:
                return _push_not_done(
                    _result_base(committed=True, commit_hash=commit_hash),
                    operations,
                    reason=f"git command timed out after {e.timeout}s",
                    message=f"Committed {commit_hash[:8]} but push step timed out: {e.cmd}",
                    verdict=Verdict.FAIL,
                )

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
