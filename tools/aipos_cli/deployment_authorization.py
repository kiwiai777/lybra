"""AIPOS-C3 大项A: N5 部署授权守卫 — 区间校验与裁决认证的单一实现。

本模块实现 transitions.schema.json N5 节点声明的守卫逻辑:
  1. 裁决记录真实性校验(门生 vs 手写)
  2. 裁决 verdict 值校验(PASS/PASS_WITH_NOTES)
  3. commit 区间覆盖校验(current..HEAD 每个 commit 都属已 PASS 的卡)

设计权威: transitions.schema.json N5.guards + 
  governance/decision_log/2026-08/2026-08-18-gate-c-refactor-four-roots.md (C3)

实证修复(2026-08-18 三层空洞):
  - 区间校验从 current==HEAD 简单相等改为逐 commit 寻找门生 PASS 裁决
  - 裁决记录必须具备门生标记(record_type/verdict_id/verdict_at),手写文件拒绝
  - verdict_ref 指向的裁决必须覆盖所有待部署 commit(跨卡挪用 = 拒绝)
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from tools.schema_constants import Verdict


def _task_id_from_commit_subject(subject: str) -> str | None:
    """从 commit 主题提取 task_id (如 'feat(AIPOS-C3): ...' 或 'AIPOS-C3: ...')."""
    # 标准格式: feat(TASK-ID): ...
    m = re.search(r"feat\(([^)]+)\)", subject)
    if m:
        return m.group(1).strip()
    # 宽松格式: TASK-ID: ... (开头)
    m = re.search(r"^([A-Z][A-Z0-9]*-[A-Z0-9]+)", subject.strip())
    if m:
        return m.group(1).strip()
    return None


def _commits_between(repo_root: Path, current_commit: str, head_commit: str) -> list[dict[str, str]]:
    """current..HEAD 的 commit 列表(按新旧序), 每项 {hash, subject}."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H %s", f"{current_commit}..{head_commit}"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    commits: list[dict[str, str]] = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        commits.append({"hash": parts[0], "subject": parts[1] if len(parts) > 1 else ""})
    return commits


def check_verdict_record_authentic(verdict_file: Path) -> dict[str, Any]:
    """AIPOS-C3 大项A①: 裁决记录真实性校验 — 门生 vs 手写。
    
    门生记录必须具备 transitions.schema.json 声明的机器特征:
      - record_type: audit_verdict_record (或 audit_verdict, schema 迁移期兼容)
      - verdict_id: verdict_{task_id}_{timestamp}_{auditor} (完整命名)
      - verdict_at: ISO8601 时间戳
    
    手写文件(缺少以上任一标记) = 拒绝,绝不参与 finalize 判定。
    
    Args:
        verdict_file: 裁决文件路径
    
    Returns:
        {
            "authentic": bool,
            "reason": str,
            "record_type": str | None,
            "verdict_id": str | None,
            "verdict_at": str | None,
        }
    """
    try:
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        text = verdict_file.read_text(encoding="utf-8")
        metadata, _body, _warnings = parse_markdown_frontmatter(text)
    except Exception as e:
        return {
            "authentic": False,
            "reason": f"文件读取或解析失败: {e}",
            "record_type": None,
            "verdict_id": None,
            "verdict_at": None,
        }
    
    record_type = str(metadata.get("record_type") or "").strip()
    verdict_id = str(metadata.get("verdict_id") or "").strip()
    verdict_at = str(metadata.get("verdict_at") or metadata.get("timestamp") or "").strip()
    
    # 检查 record_type: 必须是 audit_verdict_record 或 audit_verdict
    if not record_type.startswith("audit_verdict"):
        return {
            "authentic": False,
            "reason": f"缺少门生标记: record_type='{record_type}' 不是 audit_verdict* 家族",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    
    # 检查 verdict_id: 必须存在且符合命名约定
    if not verdict_id or not verdict_id.startswith("verdict_"):
        return {
            "authentic": False,
            "reason": f"缺少门生标记: verdict_id='{verdict_id}' 不符合命名约定(应为 verdict_*)",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    
    # 检查 verdict_at: 必须存在
    if not verdict_at:
        return {
            "authentic": False,
            "reason": "缺少门生标记: verdict_at 字段缺失",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    
    return {
        "authentic": True,
        "reason": "门生记录:具备完整机器特征(record_type + verdict_id + verdict_at)",
        "record_type": record_type,
        "verdict_id": verdict_id,
        "verdict_at": verdict_at,
    }


def find_gate_pass_verdict_for_task(
    task_id: str,
    governance_root: Path,
) -> dict[str, Any]:
    """AIPOS-C3 大项A: 为指定 task_id 查找门生 PASS 裁决。
    
    查找规则(transitions.schema N5.guards):
      1. 扫描 5_tasks/records/audit_verdicts/{task_id}/*.md
      2. 拒绝手写文件(check_verdict_record_authentic)
      3. 按 verdict_at 排序,取最新
      4. 最新裁决 verdict ∈ {PASS, PASS_WITH_NOTES} → 返回成功
      5. 否则 → 拒绝
    
    Args:
        task_id: 任务 ID
        governance_root: 治理工作区根(拥有 5_tasks/records/)
    
    Returns:
        {
            "found": bool,
            "verdict": str | None,  # PASS / PASS_WITH_NOTES / FAIL / BLOCK / None
            "verdict_id": str | None,
            "verdict_file": str | None,
            "verdict_at": str | None,
            "reason": str,
        }
    """
    verdicts_dir = governance_root / "5_tasks" / "records" / "audit_verdicts" / task_id
    
    if not verdicts_dir.is_dir():
        return {
            "found": False,
            "verdict": None,
            "verdict_id": None,
            "verdict_file": None,
            "verdict_at": None,
            "reason": f"无门生裁决记录: {verdicts_dir} 目录不存在",
        }
    
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    
    candidates: list[dict[str, Any]] = []
    rejected_files: list[str] = []
    
    for verdict_file in sorted(verdicts_dir.glob("*.md")):
        # 检查门生真实性
        auth_check = check_verdict_record_authentic(verdict_file)
        if not auth_check["authentic"]:
            rejected_files.append(f"{verdict_file.name}: {auth_check['reason']}")
            continue
        
        # 解析 verdict 值
        try:
            text = verdict_file.read_text(encoding="utf-8")
            metadata, _body, _warnings = parse_markdown_frontmatter(text)
            verdict_value = str(metadata.get("verdict") or "").strip().upper()
            verdict_at = str(metadata.get("verdict_at") or metadata.get("timestamp") or "")
            verdict_id = str(metadata.get("verdict_id") or verdict_file.stem)
            
            candidates.append({
                "path": verdict_file,
                "verdict": verdict_value,
                "verdict_at": verdict_at,
                "verdict_id": verdict_id,
            })
        except Exception as e:
            rejected_files.append(f"{verdict_file.name}: 解析失败 {e}")
            continue
    
    if not candidates:
        reason = f"无门生 PASS 裁决: {verdicts_dir} 下所有文件均被拒绝"
        if rejected_files:
            reason += f" (拒绝: {'; '.join(rejected_files[:3])}{'...' if len(rejected_files) > 3 else ''})"
        return {
            "found": False,
            "verdict": None,
            "verdict_id": None,
            "verdict_file": None,
            "verdict_at": None,
            "reason": reason,
        }
    
    # 按 verdict_at 排序,取最新
    latest = max(candidates, key=lambda c: c["verdict_at"])
    
    if latest["verdict"] in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
        return {
            "found": True,
            "verdict": latest["verdict"],
            "verdict_id": latest["verdict_id"],
            "verdict_file": str(latest["path"]),
            "verdict_at": latest["verdict_at"],
            "reason": f"最新门生裁决: {latest['verdict']} ({latest['path'].name})",
        }
    
    return {
        "found": False,
        "verdict": latest["verdict"],
        "verdict_id": latest["verdict_id"],
        "verdict_file": str(latest["path"]),
        "verdict_at": latest["verdict_at"],
        "reason": f"最新门生裁决不是 PASS: {latest['verdict']} ({latest['path'].name})",
    }


def check_commit_interval_coverage(
    repo_root: Path,
    governance_root: Path,
    current_commit: str,
    head_commit: str,
) -> dict[str, Any]:
    """AIPOS-C3 大项A②: commit 区间覆盖校验 — current..HEAD 每个 commit 都属已 PASS 的卡。
    
    实证修复(2026-08-18 三层空洞):
      - 旧逻辑: current==HEAD 简单相等 → 堆叠两张已审卡被误拦
      - 新逻辑: current..HEAD 逐 commit 找到其归属卡的门生 PASS 裁决才算已审
    
    校验流程:
      1. 获取 current..HEAD 的所有 commit
      2. 对每个 commit:
         a. 从 commit message 提取 task_id
         b. 查找该 task_id 的门生 PASS 裁决(find_gate_pass_verdict_for_task)
         c. 缺 task_id 或缺 PASS 裁决 → 标记为未审
      3. 所有 commit 都已审 → 返回 OK
      4. 任一 commit 未审 → 返回 FAIL,列出未审 commit
    
    Args:
        repo_root: 产品仓根
        governance_root: 治理工作区根(拥有 5_tasks/records/)
        current_commit: 当前部署的 commit hash (full)
        head_commit: HEAD commit hash (full)
    
    Returns:
        {
            "coverage_ok": bool,
            "total_commits": int,
            "missing_commits": list[str],  # ["hash: reason", ...]
            "message": str,
        }
    """
    if current_commit == head_commit:
        return {
            "coverage_ok": True,
            "total_commits": 0,
            "missing_commits": [],
            "message": f"current == HEAD ({head_commit[:8]}), 无待部署 commit",
        }
    
    commits = _commits_between(repo_root, current_commit, head_commit)
    
    if not commits:
        # current 不是 HEAD 祖先(分叉/漂移)
        return {
            "coverage_ok": False,
            "total_commits": 0,
            "missing_commits": [],
            "message": (
                f"DRIFT: current ({current_commit[:8]}) 不是 HEAD ({head_commit[:8]}) 的祖先 "
                "(分支分叉或部署漂移)"
            ),
        }
    
    missing: list[str] = []
    
    for commit in commits:
        task_id = _task_id_from_commit_subject(commit["subject"])
        
        if not task_id:
            missing.append(
                f"{commit['hash'][:8]}: 无 task_id (commit message: {commit['subject'][:60]})"
            )
            continue
        
        verdict_check = find_gate_pass_verdict_for_task(task_id, governance_root)
        
        if not verdict_check["found"]:
            missing.append(
                f"{commit['hash'][:8]} ({task_id}): {verdict_check['reason'][:80]}"
            )
    
    if missing:
        return {
            "coverage_ok": False,
            "total_commits": len(commits),
            "missing_commits": missing,
            "message": (
                f"区间覆盖校验失败: current({current_commit[:8]})..HEAD({head_commit[:8]}) "
                f"共 {len(commits)} 个 commit, 其中 {len(missing)} 个未审"
            ),
        }
    
    return {
        "coverage_ok": True,
        "total_commits": len(commits),
        "missing_commits": [],
        "message": (
            f"区间覆盖校验 OK: current({current_commit[:8]})..HEAD({head_commit[:8]}) "
            f"共 {len(commits)} 个 commit 均属已 PASS 的卡"
        ),
    }


def check_verdict_ref_authorization(
    verdict_ref: str,
    governance_root: Path,
    commits_to_deploy: list[str],
    repo_root: Path,
) -> dict[str, Any]:
    """AIPOS-C3 大项A③: verdict_ref 授权校验 — 裁决必须覆盖所有待部署 commit。
    
    防止跨卡挪用(实证:拿 A 卡裁决部署 B 卡 commit = 拒绝)。
    
    校验流程:
      1. verdict_ref 必须是真实的门生裁决文件(find 并校验真实性)
      2. 裁决的 reviewed_task_id 对应的所有 commit 必须覆盖 commits_to_deploy
      3. 任一待部署 commit 不属于该 task → 拒绝,列出未覆盖 commit
    
    Args:
        verdict_ref: 裁决 ID (如 verdict_AIPOS-C3_20260819_...)
        governance_root: 治理工作区根
        commits_to_deploy: 待部署的 commit hash 列表(完整 hash)
        repo_root: 产品仓根
    
    Returns:
        {
            "authorized": bool,
            "verdict_id": str,
            "reviewed_task_id": str | None,
            "verdict": str | None,
            "uncovered_commits": list[str],
            "message": str,
        }
    """
    # 1. 查找 verdict_ref 文件
    verdicts_root = governance_root / "5_tasks" / "records" / "audit_verdicts"
    verdict_file: Path | None = None
    
    # verdict_ref 可能是完整 ID (verdict_TASK-ID_...) 或简写 (TASK-ID)
    # 先尝试从所有任务目录中找
    for task_dir in sorted(verdicts_root.glob("*")):
        if not task_dir.is_dir():
            continue
        for vf in task_dir.glob("*.md"):
            if verdict_ref in vf.stem:
                verdict_file = vf
                break
        if verdict_file:
            break
    
    if not verdict_file or not verdict_file.exists():
        return {
            "authorized": False,
            "verdict_id": verdict_ref,
            "reviewed_task_id": None,
            "verdict": None,
            "uncovered_commits": commits_to_deploy,
            "message": f"verdict_ref '{verdict_ref}' 未找到对应的门生裁决文件",
        }
    
    # 2. 校验裁决真实性
    auth_check = check_verdict_record_authentic(verdict_file)
    if not auth_check["authentic"]:
        return {
            "authorized": False,
            "verdict_id": verdict_ref,
            "reviewed_task_id": None,
            "verdict": None,
            "uncovered_commits": commits_to_deploy,
            "message": f"verdict_ref '{verdict_ref}' 不是门生记录: {auth_check['reason']}",
        }
    
    # 3. 解析裁决内容
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
    try:
        text = verdict_file.read_text(encoding="utf-8")
        metadata, _body, _warnings = parse_markdown_frontmatter(text)
        reviewed_task_id = str(metadata.get("reviewed_task_id") or "").strip()
        verdict_value = str(metadata.get("verdict") or "").strip().upper()
    except Exception as e:
        return {
            "authorized": False,
            "verdict_id": verdict_ref,
            "reviewed_task_id": None,
            "verdict": None,
            "uncovered_commits": commits_to_deploy,
            "message": f"verdict_ref '{verdict_ref}' 解析失败: {e}",
        }
    
    # 4. 检查 verdict 值
    if verdict_value not in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
        return {
            "authorized": False,
            "verdict_id": verdict_ref,
            "reviewed_task_id": reviewed_task_id,
            "verdict": verdict_value,
            "uncovered_commits": commits_to_deploy,
            "message": f"verdict_ref '{verdict_ref}' 的 verdict 不是 PASS: {verdict_value}",
        }
    
    # 5. 检查 commit 覆盖:待部署的每个 commit 都必须属于 reviewed_task_id
    uncovered: list[str] = []
    for commit_hash in commits_to_deploy:
        # 获取 commit 的 task_id
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%s", commit_hash],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
                text=True,
            )
            subject = result.stdout.strip()
            task_id = _task_id_from_commit_subject(subject)
        except subprocess.CalledProcessError:
            uncovered.append(f"{commit_hash[:8]}: 无法读取 commit message")
            continue
        
        if not task_id:
            uncovered.append(f"{commit_hash[:8]}: commit message 无 task_id ({subject[:40]})")
        elif task_id != reviewed_task_id:
            uncovered.append(
                f"{commit_hash[:8]}: 属于 {task_id}, 但裁决审的是 {reviewed_task_id} (跨卡挪用)"
            )
    
    if uncovered:
        return {
            "authorized": False,
            "verdict_id": verdict_ref,
            "reviewed_task_id": reviewed_task_id,
            "verdict": verdict_value,
            "uncovered_commits": uncovered,
            "message": (
                f"verdict_ref '{verdict_ref}' 未覆盖所有待部署 commit: "
                f"{len(uncovered)}/{len(commits_to_deploy)} 个 commit 不属于 {reviewed_task_id}"
            ),
        }
    
    return {
        "authorized": True,
        "verdict_id": verdict_ref,
        "reviewed_task_id": reviewed_task_id,
        "verdict": verdict_value,
        "uncovered_commits": [],
        "message": (
            f"verdict_ref '{verdict_ref}' 授权 OK: {reviewed_task_id} {verdict_value}, "
            f"覆盖 {len(commits_to_deploy)} 个待部署 commit"
        ),
    }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
