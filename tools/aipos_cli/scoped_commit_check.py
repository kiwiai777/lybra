"""AIPOS-R4B-2: Scoped commit check — return gate按卡scope.

交回门的未提交检查只看【本卡 artifact_scope/output_target 内】改动，
不再全仓级检查。非 code 卡不查 code_repo；他人在途/既有 untracked 不误拦。

设计权威: DESIGN v2 §2 N3 (交回门按卡scope, 收编FND-16)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def check_uncommitted_in_scope(
    repo_root: Path,
    task_id: str,
    *,
    scoped_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Check if there are uncommitted changes within specified paths.
    
    AIPOS-R4B-2 N3: 交回门按卡 scope — 只检查本卡声明的 artifact_scope/output_target
    路径下的改动，而非全仓。这样：
    - 他人在途卡的改动不误拦本卡 return
    - 既有 untracked 文件不阻塞本卡
    - docs 卡等非 code 任务可以跳过 code_repo 检查
    
    Args:
        repo_root: 产品仓根路径
        task_id: 任务 ID
        scoped_paths: 要检查的路径列表（相对 repo_root）。
                     None = 全仓检查（旧行为，向后兼容）
    
    Returns:
        {
            "has_uncommitted": bool,
            "message": str (if has_uncommitted=True),
            "details": dict,
            "scoped": bool  # True if scoped check was performed
        }
    """
    try:
        # 如果没有指定 scoped_paths，回退到全仓检查（向后兼容）
        if scoped_paths is None:
            return _check_full_repo(repo_root, task_id)
        
        # Scoped check: 只看指定路径下的改动
        return _check_scoped_paths(repo_root, task_id, scoped_paths)
    
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "has_uncommitted": False,
            "skip_reason": f"git check error: {type(exc).__name__}",
            "scoped": scoped_paths is not None,
        }


def _check_full_repo(repo_root: Path, task_id: str) -> dict[str, Any]:
    """全仓检查（旧行为，向后兼容）。"""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return {"has_uncommitted": False, "skip_reason": "git status failed", "scoped": False}
    
    status_output = result.stdout.strip()
    if status_output:
        lines = status_output.split("\n")
        file_count = len(lines)
        return {
            "has_uncommitted": True,
            "message": f"Working tree has {file_count} uncommitted file(s)",
            "details": {"status_output": status_output[:500]},
            "scoped": False,
        }
    
    # Check if there's a recent commit mentioning this task_id
    if task_id:
        result = subprocess.run(
            ["git", "log", "-1", "--oneline", "--grep", task_id],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return {
                "has_uncommitted": False,
                "commit_found": True,
                "commit_line": result.stdout.strip()[:200],
                "scoped": False,
            }
    
    return {"has_uncommitted": False, "scoped": False}


def _check_scoped_paths(
    repo_root: Path,
    task_id: str,
    scoped_paths: list[str],
) -> dict[str, Any]:
    """只检查指定路径下的未提交改动。
    
    使用 `git status --porcelain <paths>` 只列出指定路径下的改动。
    这样既有 untracked 文件和他人卡的改动不会误拦本卡。
    """
    if not scoped_paths:
        # 空列表 = 不检查任何路径（pass）
        return {
            "has_uncommitted": False,
            "message": "No paths in scope, skip check",
            "scoped": True,
        }
    
    # 标准化路径：确保相对路径，去掉前导 ./
    normalized_paths = []
    for p in scoped_paths:
        # F-R4B2-7: Use removeprefix instead of lstrip to avoid eating leading dots
        p_clean = str(p)
        if p_clean.startswith("./"):
            p_clean = p_clean[2:]
        if p_clean:
            normalized_paths.append(p_clean)
    
    if not normalized_paths:
        return {
            "has_uncommitted": False,
            "message": "No valid paths in scope",
            "scoped": True,
        }
    
    # git status --porcelain -- <path1> <path2> ...
    # 只列出这些路径下的改动
    cmd = ["git", "status", "--porcelain", "--"]
    cmd.extend(normalized_paths)
    
    result = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=5,
    )
    
    if result.returncode != 0:
        return {
            "has_uncommitted": False,
            "skip_reason": "git status failed",
            "scoped": True,
        }
    
    status_output = result.stdout.strip()
    if status_output:
        lines = status_output.split("\n")
        file_count = len(lines)
        return {
            "has_uncommitted": True,
            "message": f"Working tree has {file_count} uncommitted file(s) in scope {normalized_paths}",
            "details": {
                "status_output": status_output[:500],
                "scoped_paths": normalized_paths,
            },
            "scoped": True,
        }
    
    # Scoped check passed - no uncommitted changes in specified paths
    return {
        "has_uncommitted": False,
        "message": f"No uncommitted changes in scope {normalized_paths}",
        "scoped": True,
    }


def resolve_check_scope_from_task(task_metadata: dict[str, Any]) -> list[str] | None:
    """从任务卡 metadata 解析出要检查的 scope 路径列表。
    
    逻辑:
    1. 非 code 卡（task_mode != "code" 且 artifact_policy != "formal_write"）
       → 返回空列表（不检查 code_repo）
    2. output_target 存在 → 只检查 output_target 路径
    3. artifact_scope 存在且可解析为路径 → 检查 artifact_scope 路径
    4. 否则 → None（全仓检查，向后兼容）
    
    Args:
        task_metadata: 任务卡 frontmatter metadata
    
    Returns:
        路径列表 or None (None = 全仓检查)
    """
    task_mode = str(task_metadata.get("task_mode") or "").strip()
    artifact_policy = str(task_metadata.get("artifact_policy") or "").strip()
    
    # 非 code 卡：不检查 code_repo（返回空列表 = pass）
    if task_mode != "code" and artifact_policy != "formal_write":
        return []
    
    # output_target 优先（明确的输出路径）
    output_target = str(task_metadata.get("output_target") or "").strip()
    if output_target:
        return [output_target]
    
    # artifact_scope 次之（可能是描述性文本，尝试解析为路径）
    artifact_scope = str(task_metadata.get("artifact_scope") or "").strip()
    if artifact_scope:
        # 简单启发式：如果包含 / 或常见目录名，当作路径
        if "/" in artifact_scope or any(
            keyword in artifact_scope.lower()
            for keyword in ["tools/", "src/", "lib/", "schema/", "agents/"]
        ):
            # 提取第一个看起来像路径的部分
            parts = artifact_scope.split()
            for part in parts:
                if "/" in part and not part.startswith("http"):
                    return [part.rstrip(".,;")]
    
    # 回退：全仓检查（向后兼容旧卡）
    return None


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
