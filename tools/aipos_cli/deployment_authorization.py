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
from tools.aipos_cli.record_writer import record_dir


# AIPOS-F5: "什么长得像本项目的卡号"是项目属性 —— 声明一处 (card_policy.json 的
# task_id_pattern), 归属解析与一切判卡号的调用点只读它。解析器零内置默认 (C2 原则):
# 声明缺失即出声报错。lybra 声明 AIPOS-[A-Z0-9]+, 别的项目声明自己的。
# conventional-commit 前缀家族 (2026-08-19 实锤: 只认 feat 前缀漏掉 fix/chore, A1 被迫两跳)
_CONVENTIONAL_PREFIX_RE = re.compile(r"^(?:feat|fix|chore|docs|refactor|test|perf)\(([^)]+)\)")

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


def _branch_pattern_regex(task_id_pattern: str | None) -> str | None:
    """从 N5.branch_integration.branch_pattern 声明派生任务 ID 捕获正则 (读同一份声明)。

    例如 'card/{task_id}' + task_id_pattern 'AIPOS-[A-Z0-9]+' → 'card/(AIPOS-[A-Z0-9]+)'。
    AIPOS-F108 件②(M18): 原「声明缺失/损坏回退写死 'card/{task_id}' + except Exception: pass」与按产品仓读 schema
    (非 lybra 形项目无 schema/ → 恒回落写死)已退役: 声明经唯一读取口 next_resolver.card_branch_name 读 Lybra 自身 schema
    (以占位符自身代入取回声明模式, 同一校验), 声明缺 = SchemaLoadError(fail-closed)。

    Returns:
        正则字符串, 或 None (声明模式在占位符前无前缀 或 无 task_id_pattern)
    """
    from tools.aipos_cli.next_resolver import card_branch_name

    pattern = card_branch_name("{task_id}")
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
    pattern_regex = _branch_pattern_regex(task_id_pattern)
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
    verdicts_dir = record_dir(governance_root, "audit_verdicts", task_id)
    
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


def _commit_parents(repo_root: Path, commit_hash: str) -> list[str] | None:
    """commit 的父提交列表(第一父在前); git 失败 = None(调用方出声, 不当无父)。"""
    result = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", commit_hash],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    return parts[1:] if parts else None


def _is_ancestor_or_equal(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """git merge-base --is-ancestor(含相等); 0=是, 1=否, 其它(对象不存在等)按「否」——只用于判「已审覆盖」, 判不出即不覆盖(fail-closed)。"""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    return result.returncode == 0


def _commit_subject(repo_root: Path, commit_hash: str) -> str | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", commit_hash],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _verdict_binding_covers(
    repo_root: Path, commit_hash: str, verdict_check: dict[str, Any]
) -> tuple[str | None, str]:
    """门生 PASS 裁决(find_gate_pass_verdict_for_task found=True 的结果)是否覆盖该 commit → (how, 说明)。how=None 即不覆盖。

    ① exact: 裁决 artifact_subject.commit_sha == commit(AIPOS-F70 精确绑定)
    ② merge: commit 是合并提交且第二父 == 裁决绑定 commit(finalize 的 merge --no-ff; 裁决绑卡分支 tip, 合并提交本身从不被裁决绑定)
    ③ ancestor: commit 是裁决绑定 commit 的祖先(卡分支上的中间提交 / 合入基线的合并提交——审过的树已含它)
    ④ legacy: 裁决无 artifact_subject(存量, 沿 AIPOS-F70 警告放行)
    """
    if verdict_check.get("is_legacy_verdict"):
        return "legacy", "legacy 裁决(无 artifact_subject), 沿 AIPOS-F70 警告放行"
    bound = str((verdict_check.get("artifact_subject") or {}).get("commit_sha") or "").strip().lower()
    commit = commit_hash.strip().lower()
    if not bound:
        return None, "裁决 artifact_subject 无 commit_sha, 无法核对绑定"
    if bound == commit:
        return "exact", f"裁决精确绑定 {commit[:8]}"
    parents = _commit_parents(repo_root, commit) or []
    if len(parents) >= 2 and parents[1].lower() == bound:
        return "merge", f"合并提交 {commit[:8]} 第二父 = 裁决绑定的卡分支 tip {bound[:8]}"
    if _is_ancestor_or_equal(repo_root, commit, bound):
        return "ancestor", f"{commit[:8]} 是裁决绑定的卡分支 tip {bound[:8]} 的祖先(审过的树已含它)"
    return None, f"裁决绑定 {bound[:8]}, {commit[:8]} 既非该提交、非其合并提交、亦非其祖先(产物已变化, 须复审)"


def commit_pass_coverage(
    repo_root: Path,
    governance_root: Path,
    commit_hash: str,
    subject: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F130 件②③: 单个 commit 是否被门生 PASS 裁决覆盖 —— 唯一实现。

    调用方: 区间覆盖(check_commits_coverage → finalize 完整性 check_commit_interval_coverage 与 lybra-deploy --verdict-ref
    的 check_verdict_ref_authorization)、finalize dev_override 部署基线世系(finalize._dev_override_base_authorized)。

    判定: commit 归属卡 = _task_id_from_commit_subject(项目声明 task_id_pattern + N5 branch_pattern; merge --no-ff 信息可解析出卡号);
    先看归属卡自身, 不覆盖再看其世系(_resolve_task_lineage: fix 链末端 + 结案承接, AIPOS-F53)。每张候选卡取最新门生裁决
    (find_gate_pass_verdict_for_task), PASS 且绑定覆盖该 commit(_verdict_binding_covers 四式)即覆盖。

    Raises:
        SchemaLoadError: 项目未声明 task_id_pattern(C2: 出声停, 调用方点名)。
    Returns:
        {covered, commit, task_id, covering_task_id, verdict_id, how, reason}
    """
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    base: dict[str, Any] = {"covered": False, "commit": commit_hash, "task_id": None,
                            "covering_task_id": None, "verdict_id": None, "how": None}
    if subject is None:
        subject = _commit_subject(repo_root, commit_hash)
        if subject is None:
            return {**base, "reason": f"{commit_hash[:8]}: 无法读取 commit message"}
    task_id = _task_id_from_commit_subject(subject, repo_root=repo_root, governance_root=governance_root)
    if not task_id:
        return {**base, "reason": f"{commit_hash[:8]}: 无 task_id (commit message: {subject[:60]})"}
    base["task_id"] = task_id

    tried: list[str] = []

    def _try(candidate: str) -> dict[str, Any] | None:
        check = find_gate_pass_verdict_for_task(candidate, governance_root)
        if not check.get("found"):
            tried.append(f"{candidate}: {str(check.get('reason'))[:80]}")
            return None
        how, why = _verdict_binding_covers(repo_root, commit_hash, check)
        if how is None:
            tried.append(f"{candidate}: {why}")
            return None
        return {**base, "covered": True, "covering_task_id": candidate, "verdict_id": check.get("verdict_id"),
                "how": how, "reason": f"{commit_hash[:8]} ({task_id}) 由 {candidate} 门生 PASS 裁决 {check.get('verdict_id')} 覆盖: {why}"}

    own = _try(task_id)
    if own:
        return own
    try:
        lineage = _resolve_task_lineage(task_id, governance_root)
    except FrontmatterReadError as exc:
        return {**base, "reason": f"{commit_hash[:8]} ({task_id}): 世系记录读不出, 无法判承接: {exc}"}
    for candidate in lineage:
        if candidate == task_id:
            continue
        hit = _try(candidate)
        if hit:
            return hit
    return {**base, "reason": f"{commit_hash[:8]} ({task_id}): 无覆盖它的门生 PASS 裁决({'; '.join(tried)})"}


def check_commits_coverage(
    repo_root: Path,
    governance_root: Path,
    commits: list[dict[str, str]],
) -> dict[str, Any]:
    """AIPOS-F130 件③: 一组 commit 的覆盖校验(多卡并集) —— 每个 commit 属某张有门生 PASS 覆盖的卡即可(commit_pass_coverage)。

    commits: [{hash, subject?}](subject 缺则读 git)。
    Returns: {coverage_ok, total_commits, missing_commits, covered_commits, verdicts(覆盖区间的全部裁决, 首见序), message}
    """
    missing: list[str] = []
    covered: list[dict[str, Any]] = []
    verdicts: list[str] = []
    for commit in commits:
        try:
            result = commit_pass_coverage(repo_root, governance_root, commit["hash"], commit.get("subject"))
        except SchemaLoadError as e:
            # AIPOS-F5: 声明缺失 = 出声停 (C2 原则), 不静默跳过
            return {
                "coverage_ok": False,
                "total_commits": len(commits),
                "missing_commits": [],
                "covered_commits": covered,
                "verdicts": verdicts,
                "message": f"归属解析声明缺失 (task_id_pattern): {e}",
            }
        if not result["covered"]:
            missing.append(result["reason"])
            continue
        covered.append({k: result[k] for k in ("commit", "task_id", "covering_task_id", "verdict_id", "how")})
        if result["verdict_id"] and result["verdict_id"] not in verdicts:
            verdicts.append(result["verdict_id"])
    return {
        "coverage_ok": not missing,
        "total_commits": len(commits),
        "missing_commits": missing,
        "covered_commits": covered,
        "verdicts": verdicts,
        "message": (
            f"共 {len(commits)} 个 commit, 其中 {len(missing)} 个未审" if missing else
            f"共 {len(commits)} 个 commit 均属已 PASS 的卡(覆盖裁决 {len(verdicts)} 张: {', '.join(verdicts) or '无'})"
        ),
    }


def commits_to_deploy(repo_root: Path, current_commit: str | None, head_commit: str) -> list[str]:
    """待部署 commit(完整 hash, 新到旧): 无部署 = [HEAD]; current == HEAD = [](空区间); 否则 current..HEAD。
    AIPOS-F130 件③: deploy_gate(finalize 内部署)与 lybra-deploy 自身预检/部署记录共用此推导。git 失败 = CalledProcessError 向上。"""
    if not current_commit:
        return [head_commit]
    if current_commit == head_commit:
        return []
    result = subprocess.run(
        ["git", "log", "--format=%H", f"{current_commit}..{head_commit}"],
        cwd=str(repo_root), check=True, capture_output=True, text=True,
    )
    return [c.strip() for c in result.stdout.splitlines() if c.strip()]


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
      2. 逐 commit 判覆盖(AIPOS-F130: 唯一实现 commit_pass_coverage, 经 check_commits_coverage 多卡并集):
         归属卡(或其世系)有门生 PASS 裁决, 且裁决绑定覆盖该 commit(精确 / 合并提交第二父 / 绑定 tip 的祖先 / legacy)
      3. 所有 commit 都已审 → 返回 OK(带覆盖区间的全部裁决 verdicts)
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
            "covered_commits": list[dict],  # AIPOS-F130: 每个已覆盖 commit 的归属卡/覆盖裁决/覆盖方式
            "verdicts": list[str],  # AIPOS-F130: 覆盖区间的全部裁决(并集)
            "message": str,
        }
    """
    if current_commit == head_commit:
        return {
            "coverage_ok": True,
            "total_commits": 0,
            "missing_commits": [],
            "covered_commits": [],
            "verdicts": [],
            "message": f"current == HEAD ({head_commit[:8]}), 无待部署 commit",
        }
    
    commits = _commits_between(repo_root, current_commit, head_commit)
    
    if not commits:
        # current 不是 HEAD 祖先(分叉/漂移)
        return {
            "coverage_ok": False,
            "total_commits": 0,
            "missing_commits": [],
            "covered_commits": [],
            "verdicts": [],
            "message": (
                f"DRIFT: current ({current_commit[:8]}) 不是 HEAD ({head_commit[:8]}) 的祖先 "
                "(分支分叉或部署漂移)"
            ),
        }
    
    coverage = check_commits_coverage(repo_root, governance_root, commits)
    interval = f"current({current_commit[:8]})..HEAD({head_commit[:8]})"
    if not coverage["coverage_ok"]:
        message = (coverage["message"] if not coverage["missing_commits"] else
                   f"区间覆盖校验失败: {interval} {coverage['message']}")
    else:
        message = f"区间覆盖校验 OK: {interval} {coverage['message']}"
    return {**coverage, "message": message}


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
    fix_closures_root = record_dir(governance_root, "fix_closures")
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
                # AIPOS-F100 件②: 修复链记录「必须读出」; 读不出 = FrontmatterReadError 向上(调用方点名拒), 禁静默跳过致链提前终止
                from tools.aipos_cli.frontmatter import require_frontmatter

                metadata, _body = require_frontmatter(deriv_file)
                source = str(metadata.get("source_task_id") or "").strip()
                fix_task = str(metadata.get("fix_task_id") or "").strip()

                if source == current and fix_task:
                    next_fix = fix_task
                    break
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
    # 查找任务卡（可能在 completed/claimed/pending/withdrawn/blocked 等目录）
    # AIPOS-F78 前置零⑥: 世系扫描含 withdrawn(撤废卡带承接声明时其产物由续卡承接, F53 原只扫三目录 → 撤废卡不入世系不可结案)
    # AIPOS-F78B 件①: 唯一查找 task_loader.find_task_card(frontmatter task_id 匹配)
    from tools.aipos_cli.task_loader import find_task_card

    task_file, _state = find_task_card(governance_root, task_id)  # AIPOS-F104 件②: 缺省 = 全部队列目录(task_loader.QUEUE_STATES 投影)
    
    if not task_file:
        return None
    
    # AIPOS-F100 件②: 卡面「必须读出」; 读不出 = FrontmatterReadError 向上(调用方点名拒)——原 `except Exception: pass`
    # 把读不出当「无承接」, 世系静默变短
    from tools.aipos_cli.frontmatter import require_frontmatter

    metadata, _body = require_frontmatter(task_file)
    conclusion_note = str(metadata.get("conclusion_note") or "").strip()

    if not conclusion_note:
        return None

    # 解析承接声明（匹配 "由续卡 TASK-ID 承接" 或 "由 TASK-ID 承接" 等模式）
    # 使用项目声明的 task_id_pattern
    try:
        task_id_pattern = _resolve_task_id_pattern(governance_root)
    except SchemaLoadError:
        return None
    import re
    # 匹配 "由...承接" 或 "承接" 附近的任务 ID
    match = re.search(rf"(?:由.*?({task_id_pattern}).*?承接|承接.*?({task_id_pattern}))", conclusion_note)
    if match:
        return match.group(1) or match.group(2)
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
      1. verdict_ref 必须是真实的门生裁决文件(find 并校验真实性), verdict ∈ PASS/PASS_WITH_NOTES
      2. AIPOS-F130 件③: 待部署每个 commit 属某张有门生 PASS 覆盖的卡(多卡并集; 与 finalize 完整性同一实现
         check_commits_coverage → commit_pass_coverage), 任一未覆盖 → 拒绝, 列出未覆盖 commit
      3. verdict_ref 指向的裁决须在覆盖区间的裁决并集内(区间无该卡产物 = 跨卡挪用 → 拒绝)
    
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
            "covering_verdicts": list[str],  # AIPOS-F130: 覆盖区间的全部裁决(并集)
            "covered_commits": list[dict],
            "message": str,
        }
    """
    # 1. 查找 verdict_ref 文件
    verdicts_root = record_dir(governance_root, "audit_verdicts")
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
    
    # 5. AIPOS-F130 件③: 覆盖判据单源 —— 与 finalize 完整性同一实现(check_commits_coverage → commit_pass_coverage)。
    #    待部署区间每个 commit 属某张有门生 PASS 覆盖的卡即可(多卡并集); verdict_ref 指向的裁决须在并集内(防跨卡挪用)。
    #    原「每个 commit 都须属 reviewed_task_id(或其世系)」的单裁决判据退役(F128 实撞: 区间跨两张各自 PASS 的卡即拒)。
    verdict_id = str(metadata.get("verdict_id") or verdict_file.stem).strip()
    coverage = check_commits_coverage(repo_root, governance_root, [{"hash": c} for c in commits_to_deploy])
    base = {
        "verdict_id": verdict_id,
        "reviewed_task_id": reviewed_task_id,
        "verdict": verdict_value,
        "covering_verdicts": coverage["verdicts"],
        "covered_commits": coverage["covered_commits"],
    }
    if not coverage["coverage_ok"]:
        uncovered = coverage["missing_commits"] or list(commits_to_deploy)
        return {
            **base,
            "authorized": False,
            "uncovered_commits": uncovered,
            "message": (
                f"verdict_ref '{verdict_ref}' 未覆盖所有待部署 commit: "
                f"{coverage['message']}(区间内每个 commit 须属某张有门生 PASS 覆盖的卡)。\n"
                f"如确需部署，请 Owner 授权 dev_override: "
                f"lybra-deploy deploy --dev-override --reason '<Owner 授权原因>'"
            ),
        }
    if commits_to_deploy and verdict_id not in coverage["verdicts"]:
        return {
            **base,
            "authorized": False,
            "uncovered_commits": [],
            "message": (
                f"verdict_ref '{verdict_ref}' ({verdict_id}) 不在待部署区间的覆盖裁决并集内 "
                f"(并集: {', '.join(coverage['verdicts'])}) —— 区间无该卡产物, 跨卡挪用拒绝。"
                f"出口: 用并集内任一裁决部署; 如确需部署, 请 Owner 授权 dev_override: "
                f"lybra-deploy deploy --dev-override --reason '<Owner 授权原因>'"
            ),
        }
    return {
        **base,
        "authorized": True,
        "uncovered_commits": [],
        "message": (
            f"verdict_ref '{verdict_ref}' 授权 OK: {reviewed_task_id} {verdict_value}, "
            f"覆盖 {len(commits_to_deploy)} 个待部署 commit"
            + (f"(区间覆盖裁决并集 {len(coverage['verdicts'])} 张: {', '.join(coverage['verdicts'])})" if commits_to_deploy else "(空区间)")
        ),
    }


def verdict_ref_deploy_coverage(
    repo_root: Path,
    governance_root: Path,
    verdict_ref: str,
    current_commit: str | None,
    head_commit: str,
) -> dict[str, Any]:
    """AIPOS-F130 件③: 部署区间(commits_to_deploy) + verdict_ref 授权(check_verdict_ref_authorization)一步——
    deploy_gate(finalize 内部署)、lybra-deploy 预检与部署记录(deployment_record --coverage-base)共用。
    返回 check_verdict_ref_authorization 结果 + interval/commits_to_deploy。git 失败 = 拒(原文)。"""
    try:
        commits = commits_to_deploy(repo_root, current_commit, head_commit)
    except subprocess.CalledProcessError as exc:
        return {"authorized": False, "verdict_id": verdict_ref, "reviewed_task_id": None, "verdict": None,
                "uncovered_commits": [], "covering_verdicts": [], "covered_commits": [], "commits_to_deploy": [],
                "interval": f"{(current_commit or '')[:8]}..{head_commit[:8]}",
                "message": f"无法取待部署区间 {(current_commit or '')[:8]}..{head_commit[:8]}: {(exc.stderr or '').strip() or exc}"}
    result = check_verdict_ref_authorization(verdict_ref=verdict_ref, governance_root=governance_root,
                                             commits_to_deploy=commits, repo_root=repo_root)
    result.setdefault("covering_verdicts", [])
    result.setdefault("covered_commits", [])
    return {**result, "commits_to_deploy": commits,
            "interval": f"{current_commit or '(无部署)'}..{head_commit}"}


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
