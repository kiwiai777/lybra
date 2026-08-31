"""AIPOS-F58 — 工位私有状态自我保护(git exclude 登记)。

问题: 邻居项目在共用仓根跑 `git stash -u`,五个工位的 .lybra/ 凭据同时蒸发——
因为 .lybra/ 和 .pi/ 接线文件未跟踪也未忽略。

修法: enroll/wiring 落盘时把自己落的确切路径登记进该仓 .git/info/exclude
(不碰他人 .gitignore、不整目录排除、项目名不写死、幂等、无 git 时降级跳过)。

设计约束(卡面红线):
  - 只写 .git/info/exclude(本地, 不 commit, 不影响他人)
  - 逐文件精确登记(禁目录级排除如 .lybra/ —— 会误排未来需跟踪的文件)
  - 幂等: 已存在的行不重复追加
  - 无 git 仓时静默跳过(不报错)
  - 项目名不写死(从 .git 目录推导)
  - 标记段: 用 BEGIN/END 标记段包裹, 便于识别和清理
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

# 标记段边界(用于识别 F58 登记块, 禁手工编辑此段)
_MARKER_BEGIN = "# --- BEGIN AIPOS-F58 git-exclude (lybra workstation protection) ---"
_MARKER_END = "# --- END AIPOS-F58 git-exclude ---"


def _find_git_dir(workspace_root: Path) -> Path | None:
    """查找 workspace_root 所在 git 仓的 .git 目录(向上遍历)。

    优先用 git rev-parse --git-dir(处理 worktree/bare 等边缘情况);
    git 不可用或不在 git 仓内则回退到逐层查找 .git 目录。
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_dir = result.stdout.strip()
            # git-dir 可能是相对路径(相对于 cwd)
            git_path = Path(git_dir)
            if not git_path.is_absolute():
                git_path = (workspace_root / git_path).resolve()
            if git_path.is_dir():
                return git_path
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    # 回退: 逐层查找 .git 目录
    current = workspace_root.resolve()
    while True:
        candidate = current / ".git"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _read_exclude_lines(exclude_file: Path) -> list[str]:
    """读取 .git/info/exclude 文件行(不存在返回空列表)。"""
    if not exclude_file.is_file():
        return []
    try:
        text = exclude_file.read_text(encoding="utf-8")
        return text.splitlines()
    except OSError:
        return []


def _extract_f58_block(lines: list[str]) -> tuple[int, int, list[str]]:
    """从 exclude 文件行中提取 F58 标记段。

    返回 (begin_idx, end_idx, entries_in_block)。
    无标记段时 begin_idx=end_idx=-1, entries 为空。
    """
    begin_idx = -1
    end_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == _MARKER_BEGIN:
            begin_idx = i
        elif line.strip() == _MARKER_END:
            end_idx = i
            break
    if begin_idx < 0 or end_idx < 0:
        return -1, -1, []
    entries = []
    for line in lines[begin_idx + 1 : end_idx]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.append(stripped)
    return begin_idx, end_idx, entries


def register_git_exclude(
    workspace_root: Path,
    relative_paths: Sequence[str],
) -> dict[str, object]:
    """将指定相对路径登记到 workspace_root 所在 git 仓的 .git/info/exclude。

    Args:
        workspace_root: 工位根目录(向上查找 .git)
        relative_paths: 要排除的文件相对路径列表(相对于 workspace_root,
                        如 ".lybra/connection.json")

    Returns:
        {
            "ok": bool,          # 操作是否成功(无 git 仓也算 ok=True, 静默跳过)
            "skipped": bool,     # True=无 git 仓, 跳过
            "git_dir": str|None, # .git 目录路径(跳过时为 None)
            "added": list[str],  # 本次新增的路径
            "already_present": list[str],  # 已存在的路径
            "exclude_file": str|None,  # exclude 文件路径
        }
    """
    if not relative_paths:
        return {
            "ok": True,
            "skipped": True,
            "git_dir": None,
            "added": [],
            "already_present": [],
            "exclude_file": None,
        }

    git_dir = _find_git_dir(workspace_root)
    if git_dir is None:
        # 无 git 仓: 静默跳过(不报错)
        return {
            "ok": True,
            "skipped": True,
            "git_dir": None,
            "added": [],
            "already_present": [],
            "exclude_file": None,
        }

    exclude_file = git_dir / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = _read_exclude_lines(exclude_file)
    begin_idx, end_idx, existing_entries = _extract_f58_block(existing_lines)

    # 去重: 已在 exclude 中的路径不重复添加
    desired = list(dict.fromkeys(relative_paths))  # 保序去重
    already_present = [p for p in desired if p in existing_entries]
    to_add = [p for p in desired if p not in existing_entries]

    if not to_add and begin_idx >= 0:
        # 全部已存在且标记段已在: 幂等 no-op
        return {
            "ok": True,
            "skipped": False,
            "git_dir": str(git_dir),
            "added": [],
            "already_present": already_present,
            "exclude_file": str(exclude_file),
        }

    # 构建新标记段内容
    all_entries = list(dict.fromkeys(existing_entries + to_add))  # 合并+保序去重
    block_lines = [_MARKER_BEGIN] + all_entries + [_MARKER_END]

    if begin_idx >= 0 and end_idx >= 0:
        # 替换现有标记段
        new_lines = existing_lines[:begin_idx] + block_lines + existing_lines[end_idx + 1 :]
    else:
        # 追加新标记段(前加空行分隔)
        new_lines = existing_lines
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.extend(block_lines)

    # 写入
    content = "\n".join(new_lines) + "\n"
    try:
        exclude_file.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "skipped": False,
            "git_dir": str(git_dir),
            "added": [],
            "already_present": [],
            "exclude_file": str(exclude_file),
            "error": str(exc),
        }

    return {
        "ok": True,
        "skipped": False,
        "git_dir": str(git_dir),
        "added": to_add,
        "already_present": already_present,
        "exclude_file": str(exclude_file),
    }


def collect_enroll_exclude_paths(workspace_root: Path, files_written: list[str]) -> list[str]:
    """收集 enroll 落盘后需要登记到 git exclude 的路径。

    逐文件精确登记(禁目录级排除):
      - .lybra/connection.json
      - .lybra/role
      - .lybra/actor  (legacy, 可能不存在)
      - .lybra/policy (legacy, 可能不存在)

    Args:
        workspace_root: 工位根
        files_written: enroll 返回的 files_written 列表

    Returns:
        相对路径列表(相对于 workspace_root)
    """
    lybra_dir = workspace_root / ".lybra"
    candidates = [
        ".lybra/connection.json",
        ".lybra/role",
        ".lybra/actor",
        ".lybra/policy",
    ]
    # 只登记实际存在的路径(避免 exclude 不存在的文件——虽然无害但脏)
    return [p for p in candidates if (workspace_root / p).exists()]


def collect_wiring_exclude_paths(workspace_root: Path) -> list[str]:
    """收集 .pi 接线落盘后需要登记到 git exclude 的路径。

    逐文件精确登记:
      - .pi/settings.json
      - .pi/extensions/claim.ts
      - .pi/extensions/lybra-loop.ts
      - .pi/skills/<name> (逐个)

    Returns:
        相对路径列表(相对于 workspace_root)
    """
    pi_dir = workspace_root / ".pi"
    if not pi_dir.is_dir():
        return []
    candidates: list[str] = []
    # settings.json
    if (pi_dir / "settings.json").exists():
        candidates.append(".pi/settings.json")
    # extensions
    ext_dir = pi_dir / "extensions"
    if ext_dir.is_dir():
        for f in sorted(ext_dir.iterdir()):
            if f.is_file() or f.is_symlink():
                candidates.append(f".pi/extensions/{f.name}")
    # skills
    skills_dir = pi_dir / "skills"
    if skills_dir.is_dir():
        for f in sorted(skills_dir.iterdir()):
            if f.is_dir() or f.is_symlink():
                candidates.append(f".pi/skills/{f.name}")
    return candidates
