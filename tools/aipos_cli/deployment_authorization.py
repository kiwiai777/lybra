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
from tools.schema_loader import SchemaLoadError


# AIPOS-F5: "什么长得像本项目的卡号"是项目属性 —— 声明一处 (card_policy.json 的
# task_id_pattern), 归属解析与一切判卡号的调用点只读它。解析器零内置默认 (C2 原则):
# 声明缺失即出声报错。lybra 声明 AIPOS-[A-Z0-9]+, 别的项目声明自己的。
# conventional-commit 前缀家族 (2026-08-19 实锤: 只认 feat 前缀漏掉 fix/chore, A1 被迫两跳)
_CONVENTIONAL_PREFIX_RE = re.compile(r"^(?:feat|fix|chore|docs|refactor|test|perf)\(([^)]+)\)")

_DEFAULT_BRANCH_PATTERN = "card/{task_id}"


def _resolve_task_id_pattern(governance_root: Path | None, repo_root: Path | None = None) -> str:
    """AIPOS-F5: 读项目声明的 task_id_pattern (单源, card_policy.json/R8C 同构)。

    返回正则片段 (如 "AIPOS-[A-Z0-9]+"), 可被 fullmatch/match/search 直接使用。
    缺失时出声报错 (C2 原则: 无内置默认模式)。
    """
    if governance_root is None:
        raise SchemaLoadError(
            "归属解析需要 governance_root 才能读项目声明的 task_id_pattern (card_policy.json)"
        )
    from tools.card_policy_loader import get_task_id_pattern
    pattern = get_task_id_pattern(governance_root, repo_root=repo_root)
    if not pattern:
        raise SchemaLoadError(
            f"项目未声明 task_id_pattern (card_policy.json @ {governance_root}); "
            "卡号形状是项目属性, 解析器无内置默认, 无法做归属解析 (C2 原则)"
        )
    return pattern


def _branch_pattern_regex(repo_root: Path | None = None, task_id_pattern: str | None = None) -> str | None:
    """从 N5.branch_integration.branch_pattern 声明派生任务 ID 捕获正则 (读同一份声明)。

    例如 'card/{task_id}' + task_id_pattern 'AIPOS-[A-Z0-9]+' → 'card/(AIPOS-[A-Z0-9]+)'。
    声明缺失/损坏时回退到默认 'card/{task_id}' (单元测试夹具无 schema 目录)。

    Returns:
        正则字符串, 或 None (branch_pattern 不含 {task_id} 占位符 或 无 task_id_pattern)
    """
    pattern = _DEFAULT_BRANCH_PATTERN
    try:
        from tools.schema_loader import get_branch_integration
        bi = get_branch_integration(repo_root)
        declared = str(bi.get("branch_pattern") or "").strip()
        if declared and "{task_id}" in declared:
            pattern = declared
    except Exception:
        pass
    if "{task_id}" not in pattern:
        return None
    prefix = pattern.split("{task_id}", 1)[0]
    if not prefix:
        return None
    if not task_id_pattern:
        return None
    return re.escape(prefix) + f"({task_id_pattern})"


def _task_id_from_commit_subject(
    subject: str,
    repo_root: Path | None = None,
    governance_root: Path | None = None,
) -> str | None:
    """从 commit 主题提取 task_id。

    AIPOS-F5: 卡号形状读项目声明 (task_id_pattern), 各规则抓取物必须匹配声明模式,
    不匹配则继续尝试其它规则。兼容家族:
      1. feat/fix/chore/docs/refactor/test/perf(TASK-ID): ... (conventional 前缀)
      2. TASK-ID: ... (裸前缀, 历史卡)
      3. Merge <branch_pattern>/<TASK-ID>: ... (merge --no-ff 信息, 声明保证归属含卡号)
      4. 信息任意位置的模式命中 (如句尾括号 "(AIPOS-F3)" —— 858655a 回归夹具)

    Raises:
        SchemaLoadError: 项目未声明 task_id_pattern (C2 原则, 无内置默认)。
    """
    task_id_pattern = _resolve_task_id_pattern(governance_root, repo_root)

    # 1. conventional 前缀家族
    m = _CONVENTIONAL_PREFIX_RE.search(subject)
    if m:
        candidate = m.group(1).strip()
        if re.fullmatch(task_id_pattern, candidate):
            return candidate
    # 2. 裸 TASK-ID 前缀
    m = re.match(task_id_pattern, subject.strip())
    if m:
        return m.group(0)
    # 3. merge 信息 (branch_pattern 声明)
    pattern_regex = _branch_pattern_regex(repo_root, task_id_pattern)
    if pattern_regex:
        m = re.search(pattern_regex, subject)
        if m:
            return m.group(1)
    # 4. AIPOS-F5: 信息任意位置的模式命中 (句尾括号等)
    m = re.search(task_id_pattern, subject)
    if m:
        return m.group(0)
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
    """AIPOS-C3 大项A① + AIPOS-F2: 裁决记录真实性校验 — 门生 vs 手写。
    
    门生记录必须具备 transitions.schema.json 声明的机器特征:
      - record_type: audit_verdict_record (或 audit_verdict, schema 迁移期兼容)
      - verdict_id: verdict_{task_id}_{timestamp}_{auditor} (完整命名)
      - verdict_at: ISO8601 时间戳
    
    手写文件(缺少以上任一标记) = 拒绝,绝不参与 finalize 判定。
    
    AIPOS-F2: 门生判定核心逻辑委托给 audit_helpers.is_gate_born_verdict_metadata
    (单源声明),本函数保留详细诊断输出但判定结果与共享函数一致。
    
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
    
    # AIPOS-F2: 核心判定走共享函数(单源)
    from tools.aipos_cli.audit_helpers import is_gate_born_verdict_metadata
    if is_gate_born_verdict_metadata(metadata):
        return {
            "authentic": True,
            "reason": "门生记录:具备完整机器特征(record_type + verdict_id + verdict_at)",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    
    # 详细诊断信息(保留原有逐字段报错)
    if not record_type.startswith("audit_verdict"):
        return {
            "authentic": False,
            "reason": f"缺少门生标记: record_type='{record_type}' 不是 audit_verdict* 家族",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    if not verdict_id or not verdict_id.startswith("verdict_"):
        return {
            "authentic": False,
            "reason": f"缺少门生标记: verdict_id='{verdict_id}' 不符合命名约定(应为 verdict_*)",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    if not verdict_at:
        return {
            "authentic": False,
            "reason": "缺少门生标记: verdict_at 字段缺失",
            "record_type": record_type,
            "verdict_id": verdict_id,
            "verdict_at": verdict_at,
        }
    # Should not reach here (is_gate_born_verdict_metadata would have returned True)
    return {
        "authentic": False,
        "reason": "缺少门生标记: 未知原因",
        "record_type": record_type,
        "verdict_id": verdict_id,
        "verdict_at": verdict_at,
    }


def find_gate_pass_verdict_for_task(
    task_id: str,
    governance_root: Path,
    required_commit_sha: str | None = None,
) -> dict[str, Any]:
    """AIPOS-C3 大项A + AIPOS-F70: 为指定 task_id 查找门生 PASS 裁决(支持精确 SHA 匹配)。
    
    查找规则(transitions.schema N5.guards + AIPOS-F70):
      1. 扫描 5_tasks/records/audit_verdicts/{task_id}/*.md
      2. 拒绝手写文件(check_verdict_record_authentic)
      3. 按 verdict_at 排序,取最新
      4. 最新裁决 verdict ∈ {PASS, PASS_WITH_NOTES} → 检查 artifact_subject
      5. AIPOS-F70: 如果提供了 required_commit_sha,裁决必须精确覆盖该 commit
         - 裁决有 artifact_subject.commit_sha → 精确匹配
         - 裁决无 artifact_subject (存量 legacy) → 警告但放行
      6. 否则 → 拒绝
    
    Args:
        task_id: 任务 ID
        governance_root: 治理工作区根(拥有 5_tasks/records/)
        required_commit_sha: (可选) 要求裁决覆盖的精确 commit SHA (AIPOS-F70)
    
    Returns:
        {
            "found": bool,
            "verdict": str | None,  # PASS / PASS_WITH_NOTES / FAIL / BLOCK / None
            "verdict_id": str | None,
            "verdict_file": str | None,
            "verdict_at": str | None,
            "artifact_subject": dict | None,  # AIPOS-F70: 裁决自述的产物
            "is_legacy_verdict": bool,  # AIPOS-F70: 无 artifact_subject 的存量裁决
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
            # AIPOS-F70: 提取 artifact_subject
            artifact_subject = metadata.get("artifact_subject") if isinstance(metadata.get("artifact_subject"), dict) else None
            
            candidates.append({
                "path": verdict_file,
                "verdict": verdict_value,
                "verdict_at": verdict_at,
                "verdict_id": verdict_id,
                "artifact_subject": artifact_subject,
                "metadata": metadata,
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
            "artifact_subject": None,
            "is_legacy_verdict": False,
            "reason": reason,
        }
    
    # 按 verdict_at 排序,取最新
    latest = max(candidates, key=lambda c: c["verdict_at"])
    
    # AIPOS-F70: 判断是否为 legacy 裁决 (无 artifact_subject)
    is_legacy = latest["artifact_subject"] is None
    
    if latest["verdict"] in {Verdict.PASS, Verdict.PASS_WITH_NOTES}:
        # AIPOS-F70: 如果要求精确 commit SHA 匹配
        if required_commit_sha:
            if is_legacy:
                # 存量 legacy 裁决 -> 警告但放行
                return {
                    "found": True,
                    "verdict": latest["verdict"],
                    "verdict_id": latest["verdict_id"],
                    "verdict_file": str(latest["path"]),
                    "verdict_at": latest["verdict_at"],
                    "artifact_subject": None,
                    "is_legacy_verdict": True,
                    "reason": f"最新门生裁决: {latest['verdict']} ({latest['path'].name}), 但是 legacy 裁决 (无 artifact_subject), 警告放行",
                }
            else:
                # 新裁决: 精确匹配 commit_sha
                verdict_commit_sha = str(latest["artifact_subject"].get("commit_sha") or "").strip()
                if verdict_commit_sha.lower() == required_commit_sha.lower():
                    return {
                        "found": True,
                        "verdict": latest["verdict"],
                        "verdict_id": latest["verdict_id"],
                        "verdict_file": str(latest["path"]),
                        "verdict_at": latest["verdict_at"],
                        "artifact_subject": latest["artifact_subject"],
                        "is_legacy_verdict": False,
                        "reason": f"最新门生裁决: {latest['verdict']} ({latest['path'].name}), 精确覆盖 commit {required_commit_sha[:8]}",
                    }
                else:
                    # commit SHA 不匹配 -> 拒绝
                    return {
                        "found": False,
                        "verdict": latest["verdict"],
                        "verdict_id": latest["verdict_id"],
                        "verdict_file": str(latest["path"]),
                        "verdict_at": latest["verdict_at"],
                        "artifact_subject": latest["artifact_subject"],
                        "is_legacy_verdict": False,
                        "reason": (
                            f"AIPOS-F70: 裁决 commit_sha 不匹配. "
                            f"裁决覆盖: {verdict_commit_sha[:8] if verdict_commit_sha else 'None'}, "
                            f"要求: {required_commit_sha[:8]}. "
                            f"产物已变化,须复审 ({latest['path'].name})"
                        ),
                    }
        else:
            # 未要求精确匹配 (旧逻辑, finalize 不带 required_commit_sha)
            return {
                "found": True,
                "verdict": latest["verdict"],
                "verdict_id": latest["verdict_id"],
                "verdict_file": str(latest["path"]),
                "verdict_at": latest["verdict_at"],
                "artifact_subject": latest["artifact_subject"],
                "is_legacy_verdict": is_legacy,
                "reason": f"最新门生裁决: {latest['verdict']} ({latest['path'].name})",
            }
    
    return {
        "found": False,
        "verdict": latest["verdict"],
        "verdict_id": latest["verdict_id"],
        "verdict_file": str(latest["path"]),
        "verdict_at": latest["verdict_at"],
        "artifact_subject": latest["artifact_subject"],
        "is_legacy_verdict": is_legacy,
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
        try:
            task_id = _task_id_from_commit_subject(
                commit["subject"], repo_root=repo_root, governance_root=governance_root
            )
        except SchemaLoadError as e:
            # AIPOS-F5: 声明缺失 = 出声停 (C2 原则), 不静默跳过
            return {
                "coverage_ok": False,
                "total_commits": len(commits),
                "missing_commits": [],
                "message": f"归属解析声明缺失 (task_id_pattern): {e}",
            }
        
        if not task_id:
            missing.append(
                f"{commit['hash'][:8]}: 无 task_id (commit message: {commit['subject'][:60]})"
            )
            continue
        
        # AIPOS-F70: 精确 SHA 匹配 — 裁决必须覆盖该 commit
        verdict_check = find_gate_pass_verdict_for_task(
            task_id, governance_root, required_commit_sha=commit["hash"]
        )
        
        if not verdict_check["found"]:
            # AIPOS-F70: 区分 legacy 裁决的警告
            if verdict_check.get("is_legacy_verdict"):
                # legacy 裁决警告但放行 (已在 find_gate_pass_verdict_for_task 中处理)
                # 这里不应该进入,因为 legacy found=True
                pass
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


def _find_fix_chain_terminal(task_id: str, governance_root: Path) -> str | None:
    """AIPOS-F53: 查找 fix 链的末端任务 ID（从 F18 派生记录读取）。
    
    fix 链关系从 fix_closures 目录的 derivation 记录中读取，记录格式：
      - fix_task_id: 当前 fix 卡 ID
      - source_task_id: 原始卡 ID
    
    递归查找：task_id → fix_task_id → fix_task_id → ... 直到没有下一级。
    
    Args:
        task_id: 起始任务 ID
        governance_root: 治理工作区根
    
    Returns:
        链末端的任务 ID，如果没有 fix 链则返回 None
    """
    fix_closures_root = governance_root / "5_tasks" / "records" / "fix_closures"
    if not fix_closures_root.exists():
        return None
    
    current = task_id
    visited = set()  # 防止循环
    
    while True:
        if current in visited:
            # 检测到循环，停止
            return None
        visited.add(current)
        
        # 查找以 current 为 source_task_id 的 derivation 记录
        next_fix = None
        for task_dir in fix_closures_root.glob("*"):
            if not task_dir.is_dir():
                continue
            for deriv_file in task_dir.glob("derivation_*.md"):
                try:
                    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
                    text = deriv_file.read_text(encoding="utf-8")
                    metadata, _body, _warnings = parse_markdown_frontmatter(text)
                    source = str(metadata.get("source_task_id") or "").strip()
                    fix_task = str(metadata.get("fix_task_id") or "").strip()
                    
                    if source == current and fix_task:
                        next_fix = fix_task
                        break
                except Exception:
                    continue
            if next_fix:
                break
        
        if not next_fix:
            # 没有下一级，current 是链末端
            return current if current != task_id else None
        
        current = next_fix


def _find_continuation_task(task_id: str, governance_root: Path) -> str | None:
    """AIPOS-F53: 查找结案-承接关系中的承接任务（从 conclusion_note 解析）。
    
    结案-承接形态：卡 A 因卡面缺陷结案，由续卡 B 承接。
    判据：A 的任务卡 conclusion_note 中包含承接声明（如 "由续卡 TASK-B 承接"）。
    
    Args:
        task_id: 结案任务 ID
        governance_root: 治理工作区根
    
    Returns:
        承接任务 ID，如果没有承接关系则返回 None
    """
    # 查找任务卡（可能在 completed/claimed/pending 等目录）
    queue_root = governance_root / "5_tasks" / "queue"
    task_file = None
    
    for status_dir in ["completed", "claimed", "pending"]:
        potential = queue_root / status_dir / f"{task_id.lower()}.md"
        if potential.exists():
            task_file = potential
            break
    
    if not task_file:
        return None
    
    try:
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
        text = task_file.read_text(encoding="utf-8")
        metadata, _body, _warnings = parse_markdown_frontmatter(text)
        conclusion_note = str(metadata.get("conclusion_note") or "").strip()
        
        if not conclusion_note:
            return None
        
        # 解析承接声明（匹配 "由续卡 TASK-ID 承接" 或 "由 TASK-ID 承接" 等模式）
        # 使用项目声明的 task_id_pattern
        try:
            task_id_pattern = _resolve_task_id_pattern(governance_root)
            # 查找 "承接" 关键字附近的任务 ID
            import re
            # 匹配 "由...承接" 或 "承接" 附近的任务 ID
            match = re.search(rf"(?:由.*?({task_id_pattern}).*?承接|承接.*?({task_id_pattern}))", conclusion_note)
            if match:
                continuation_id = match.group(1) or match.group(2)
                return continuation_id
        except SchemaLoadError:
            pass
    except Exception:
        pass
    
    return None


def _resolve_task_lineage(task_id: str, governance_root: Path) -> list[str]:
    """AIPOS-F53: 解析任务的完整世系（fix 链 + 结案-承接链）。
    
    返回从 task_id 开始的完整世系链，包括：
    1. task_id 自身
    2. 所有 fix 链任务（递归查找）
    3. 结案-承接关系（如果有）
    
    Args:
        task_id: 起始任务 ID
        governance_root: 治理工作区根
    
    Returns:
        世系列表，按优先级排序（链末端优先）
    """
    lineage = [task_id]
    visited = {task_id}
    
    # 1. 查找 fix 链末端
    terminal = _find_fix_chain_terminal(task_id, governance_root)
    if terminal and terminal not in visited:
        lineage.append(terminal)
        visited.add(terminal)
    
    # 2. 查找结案-承接关系
    continuation = _find_continuation_task(task_id, governance_root)
    if continuation and continuation not in visited:
        lineage.append(continuation)
        visited.add(continuation)
        
        # 递归查找承接任务的 fix 链
        cont_terminal = _find_fix_chain_terminal(continuation, governance_root)
        if cont_terminal and cont_terminal not in visited:
            lineage.append(cont_terminal)
            visited.add(cont_terminal)
    
    return lineage


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
        except subprocess.CalledProcessError:
            uncovered.append(f"{commit_hash[:8]}: 无法读取 commit message")
            continue
        
        try:
            task_id = _task_id_from_commit_subject(
                subject, repo_root=repo_root, governance_root=governance_root
            )
        except SchemaLoadError as e:
            # AIPOS-F5: 声明缺失 = 出声停 (C2 原则)
            return {
                "authorized": False,
                "verdict_id": verdict_ref,
                "reviewed_task_id": reviewed_task_id,
                "verdict": verdict_value,
                "uncovered_commits": commits_to_deploy,
                "message": f"归属解析声明缺失 (task_id_pattern): {e}",
            }
        
        if not task_id:
            uncovered.append(f"{commit_hash[:8]}: commit message 无 task_id ({subject[:40]})")
        elif task_id != reviewed_task_id:
            # AIPOS-F53: 修复轮承接判定 — 检查 commit 所属任务的世系是否包含 reviewed_task_id
            # 世系包括: fix 链 + 结案-承接关系
            commit_lineage = _resolve_task_lineage(task_id, governance_root)
            if reviewed_task_id not in commit_lineage:
                uncovered.append(
                    f"{commit_hash[:8]}: 属于 {task_id}, 但裁决审的是 {reviewed_task_id} (跨卡挪用)"
                )
    
    if uncovered:
        # AIPOS-F53: 拒绝时给出 dev_override 出口引导
        return {
            "authorized": False,
            "verdict_id": verdict_ref,
            "reviewed_task_id": reviewed_task_id,
            "verdict": verdict_value,
            "uncovered_commits": uncovered,
            "message": (
                f"verdict_ref '{verdict_ref}' 未覆盖所有待部署 commit: "
                f"{len(uncovered)}/{len(commits_to_deploy)} 个 commit 不属于 {reviewed_task_id}。\n"
                f"如确需部署，请 Owner 授权 dev_override: "
                f"lybra-deploy deploy --dev-override --reason '<Owner 授权原因>'"
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
