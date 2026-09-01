"""AIPOS-FND-2/FND-9 — finalize: git commit/push/deploy for PASS tasks.

AIPOS-FINALIZE-FIX-1 (2026-08-12): 三项红线修正:
  ① 剥离治理仓 git 操作 — finalize 的 git commit/push 只作用于产品仓 (workspace_root),
     绝不操作治理仓 (governance_root)。records/queue 文件由 gate 动词写入,治理仓 git
     归 N6 收账节点 (顾问职责),executor 无权推治理仓。
  ② deploy 失败 → finalize 整体 FAIL — deploy 子步失败 (显式或自动) 必须返回
     verdict=Verdict.FAIL + exit 非0,禁止吞错报成功。
  ③ lybra-deploy 路径从产品仓根解析 — repo_root / "tools" / "lybra-deploy",
     禁止 cwd 猜测,符合 config.schema 标准位置。

After audit verdict=PASS, finalize commits the changes to git and optionally pushes.
Enforces deployment integrity (current==HEAD) and only allows finalization of PASS tasks.

AIPOS-FND-9: Auto-deploy gate-side changes after commit to prevent "committed but not live" drift.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.schema_loader import get_enum_values
from tools.schema_constants import RecordType, Verdict

# FND-47: record_type 从 enums.schema 读取（单一源）
_RECORD_TYPE_ENUM_CACHE: list[str] | None = None

def _get_valid_record_types() -> list[str]:
    """Get all valid record_type values from enums.schema.json."""
    global _RECORD_TYPE_ENUM_CACHE
    if _RECORD_TYPE_ENUM_CACHE is None:
        _RECORD_TYPE_ENUM_CACHE = get_enum_values("record_type")
    return _RECORD_TYPE_ENUM_CACHE


def _git_rev_parse_head(repo_root: Path) -> str:
    """Get current git HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def _git_status_clean(repo_root: Path) -> bool:
    """Check if working tree is clean (no uncommitted changes)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return not result.stdout.strip()
    except subprocess.CalledProcessError:
        return False


def _git_local_origin_synced(repo_root: Path) -> bool:
    """Check if local HEAD is synced with origin (AIPOS-R6A 靶子③: push判据修正).
    
    Returns:
        True if local HEAD == origin/HEAD (or origin doesn't exist)
        False if local has unpushed commits
    
    Context:
        working tree clean ≠ already pushed. A clean tree with unpushed commits
        should trigger push, not skip as "nothing to do".
    """
    try:
        # Get current branch
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        branch = branch_result.stdout.strip()
        
        # Get local HEAD
        local_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        local_head = local_result.stdout.strip()
        
        # Try to get origin HEAD
        origin_result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        
        # If origin doesn't exist, consider synced (no remote to push to)
        if origin_result.returncode != 0:
            return True
        
        origin_head = origin_result.stdout.strip()
        return local_head == origin_head
        
    except subprocess.CalledProcessError:
        # If git commands fail, assume not synced (safe default)
        return False


def _read_deploy_current(repo_root: Path) -> dict[str, str | None]:
    """读 .deploy/current/VERSION 的 git_commit / deployment_provenance / authorization_ref。"""
    deploy_dir = repo_root / ".deploy"
    current_link = deploy_dir / "current"
    result: dict[str, str | None] = {"current_commit": None, "provenance": None, "authorization_ref": None}
    if not current_link.exists():
        return result
    version_file = current_link / "VERSION"
    if not version_file.exists():
        return result
    text = version_file.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("git_commit:"):
            result["current_commit"] = line.split(":", 1)[1].strip()
        elif line.startswith("deployment_provenance:"):
            result["provenance"] = line.split(":", 1)[1].strip()
        elif line.startswith("authorization_ref:"):
            result["authorization_ref"] = line.split(":", 1)[1].strip()
    return result



def _check_deployment_integrity(repo_root: Path, governance_root: Path | None = None) -> dict[str, Any]:
    """AIPOS-C3 大项A: 部署完整性区间校验(current..HEAD 每个 commit 都属已 PASS 的卡)。

    使用 deployment_authorization.check_commit_interval_coverage 的统一实现。
    实证修复(2026-08-18 三层空洞): 取代原 current==HEAD 简单相等。

    语义:
      - 无部署 → OK(首次 commit, 无漂移可校验)
      - provenance=dev_override → 拒(finalize 拒绝在 dev_override 上结算)
      - current == HEAD → OK
      - current..HEAD 每个 commit 均属已 PASS 的卡 → OK(待 deploy 追平)
      - 否则 → 拒, 列出缺审 commit

    Returns:
        {"integrity_ok": bool, "current_commit": str|None, "head_commit": str,
         "provenance": str|None, "missing_commits": list[str], "message": str}
    """
    from tools.aipos_cli.deployment_authorization import check_commit_interval_coverage
    
    head_commit = _git_rev_parse_head(repo_root)
    deploy_dir = repo_root / ".deploy"
    current_link = deploy_dir / "current"
    
    if not current_link.exists():
        # No deployment setup yet - this is OK for finalize (we're just committing)
        return {
            "integrity_ok": True,
            "current_commit": None,
            "head_commit": head_commit,
            "provenance": None,
            "missing_commits": [],
            "message": "No .deploy/current symlink (no deployment yet - OK for commit)",
        }

    deployed = _read_deploy_current(repo_root)
    current_commit = deployed["current_commit"]
    provenance = deployed["provenance"]
    
    if not current_commit:
        return {
            "integrity_ok": False,
            "current_commit": None,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": ".deploy/current/VERSION missing git_commit field",
        }

    # AIPOS-C3 大项A: provenance=dev_override → finalize 拒绝在其上结算
    if provenance == "dev_override":
        return {
            "integrity_ok": False,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": (
                f"Deployment provenance=dev_override (current={current_commit[:8]}). "
                "finalize 拒绝在 dev_override 部署上结算 —— 必须先用审过的 commit 重部署 "
                "(lybra-deploy --verdict-ref <pass_verdict_id>)。"
            ),
        }

    if current_commit == head_commit:
        return {
            "integrity_ok": True,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": f"Deployment integrity OK: current == HEAD ({head_commit[:8]})",
        }

    # AIPOS-C3 大项A②: 区间校验统一实现(check_commit_interval_coverage)
    if governance_root is None:
        # 无 governance_root, 退化为简单检查(不做深度校验)
        return {
            "integrity_ok": True,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": (
                f"区间校验跳过(无 governance_root): current({current_commit[:8]})..HEAD({head_commit[:8]})"
            ),
        }
    
    coverage = check_commit_interval_coverage(
        repo_root=repo_root,
        governance_root=governance_root,
        current_commit=current_commit,
        head_commit=head_commit,
    )
    
    return {
        "integrity_ok": coverage["coverage_ok"],
        "current_commit": current_commit,
        "head_commit": head_commit,
        "provenance": provenance,
        "missing_commits": coverage["missing_commits"],
        "message": coverage["message"],
    }


def _ensure_finalization_record(
    governance_root: Path,
    task_id: str,
    actor: str,
    commit_hash: str,
    verdict_id: str | None,
    deployed: bool,
    operations: list[str],
) -> None:
    """AIPOS-C3B 大项B③: 写 finalization 记录(必落)。
    
    所有 finalize PASS 路径(含 working-tree-clean 早退)都必须调用此函数,
    确保 finalizations/ 目录有记录。三次 finalize 成功但 finalizations/ 全空
    的实撞必须不再发生。
    """
    try:
        from tools.aipos_cli.finalization_record import write_finalization_record
        fin_result = write_finalization_record(
            governance_root=governance_root,
            task_id=task_id,
            actor=actor,
            commit=commit_hash,
            authorization_type="verdict_ref",
            authorization_ref=verdict_id or "unknown",
            deployed=deployed,
            deployment_record_ref=None,
        )
        operations.append(f"Finalization record written: {fin_result['path']}")
    except Exception as e:
        operations.append(f"⚠️  Finalization record write failed: {e}")


def _report_frontmatter_verdict_for_display(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-FND-14: best-effort, DISPLAY-ONLY lookup of the human-authored
    task_cards/<task_id>/AUDIT-REPORT-*.md frontmatter ``verdict:`` field.

    This is NEVER judged for finalize eligibility (see ``check_task_can_finalize`` below —
    that report has no reliable frontmatter and is a plain editable markdown file anyone could
    hand-write a fake ``verdict: PASS`` into). It is surfaced purely so operators can see what
    the (non-authoritative) report says alongside the real gate verdict. Any failure here is
    swallowed — this must never block or alter the real finalize decision.
    """
    try:
        task_dir = workspace_root / "task_cards" / task_id
        audit_reports = sorted(task_dir.glob("AUDIT-REPORT-*.md"))
        if not audit_reports:
            return {"report_path": None, "report_verdict": None}
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

        report_path = audit_reports[0]
        metadata, _body, _warnings = parse_markdown_frontmatter(report_path.read_text(encoding="utf-8"))
        return {"report_path": str(report_path), "report_verdict": metadata.get("verdict")}
    except Exception:
        return {"report_path": None, "report_verdict": None}


def check_task_can_finalize(task_id: str, governance_root: Path, commit_sha: str | None = None) -> dict[str, Any]:
    """AIPOS-C3 大项A + AIPOS-F70: 检查任务是否可以 finalize(基于门生 PASS 裁决 + 精确 SHA 核对)。

    使用 deployment_authorization.find_gate_pass_verdict_for_task 的统一实现。
    
    实证修复:
      - 旧逻辑读 task_cards AUDIT-REPORT frontmatter(手写文件,可伪造)
      - 新逻辑只认门生裁决(5_tasks/records/audit_verdicts/,具备机器特征)
      - 手写文件(缺 record_type/verdict_id/verdict_at) = 拒绝
    
    AIPOS-F70:
      - 如果提供 commit_sha,裁决必须精确覆盖该 commit
      - 裁决有 artifact_subject.commit_sha → 精确匹配
      - 裁决无 artifact_subject (legacy) → 警告但放行

    Args:
        task_id: 任务 ID
        governance_root: 治理工作区根
        commit_sha: (可选) 待 finalize 的 commit SHA,用于精确核对 (AIPOS-F70)

    Returns:
        {
            "can_finalize": bool,
            "task_id": str,
            "verdict": str | None,
            "verdict_record_path": str | None,
            "verdict_id": str | None,
            "is_legacy_verdict": bool,  # AIPOS-F70
            "reason": str
        }
    """
    from tools.aipos_cli.deployment_authorization import find_gate_pass_verdict_for_task
    
    # AIPOS-F70: 传递 commit_sha 进行精确核对
    verdict_check = find_gate_pass_verdict_for_task(task_id, governance_root, required_commit_sha=commit_sha)
    
    return {
        "can_finalize": verdict_check["found"],
        "task_id": task_id,
        "verdict": verdict_check["verdict"],
        "verdict_record_path": verdict_check["verdict_file"],
        "verdict_id": verdict_check["verdict_id"],
        "is_legacy_verdict": verdict_check.get("is_legacy_verdict", False),  # AIPOS-F70
        "reason": verdict_check["reason"],
    }


def check_stage_archive_gate(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """AIPOS-R6M 大项A③: 阶段粒度门票 — stage transition (finalize/发布门) 前校验阶段快照存在。

    判据与路径从 config.schema 治理目录树读
    (``timeline_enforcement.stage_level.path_key`` + ``governance_structure.paths.<key>``),
    代码零写死。缺阶段快照 → BLOCK (门票机制: 阶段快照=转换前提, 缺快照=未关账=不许转换)。

    Args:
        governance_root: 治理工作区根 (拥有 stage_archive/ 的根, 非产品仓)。
        repo_root: 产品仓根 (用于定位 schema/config.schema.json 单一源)。

    Returns:
        {"passed": bool, "message": str, "stage_archive_dir": str|None,
         "snapshot_count": int, "path_key": str|None}
    """
    try:
        from tools.schema_loader import get_governance_structure, resolve_governance_path

        gs = get_governance_structure(repo_root)
        stage_level = (gs.get("timeline_enforcement") or {}).get("stage_level") or {}
        path_key = str(stage_level.get("path_key") or "stage_archive")
        stage_dir = resolve_governance_path(path_key, governance_root, repo_root)
    except Exception as exc:
        return {
            "passed": False,
            "message": f"Stage gate config load failed: {exc}",
            "stage_archive_dir": None,
            "snapshot_count": 0,
            "path_key": None,
        }

    if not stage_dir.is_dir():
        return {
            "passed": False,
            "message": (
                f"Stage gate BLOCK: stage archive dir missing ({stage_dir}). "
                "阶段快照=转换前提, 缺快照=未关账=不许转换 (AIPOS-R6M 大项A③)."
            ),
            "stage_archive_dir": str(stage_dir),
            "snapshot_count": 0,
            "path_key": path_key,
        }

    # 阶段快照 = 目录内 .md 文件, 排除 README/index (索引非阶段快照)。
    snapshots = sorted(
        p for p in stage_dir.glob("*.md")
        if p.name.lower() not in {"readme.md", "index.md"}
    )
    if not snapshots:
        return {
            "passed": False,
            "message": (
                f"Stage gate BLOCK: no stage snapshot in {stage_dir} (empty or index-only). "
                "阶段快照=转换前提, 缺快照=未关账=不许转换 (AIPOS-R6M 大项A③)."
            ),
            "stage_archive_dir": str(stage_dir),
            "snapshot_count": 0,
            "path_key": path_key,
        }

    return {
        "passed": True,
        "message": f"Stage gate OK: {len(snapshots)} stage snapshot(s) in {stage_dir}",
        "stage_archive_dir": str(stage_dir),
        "snapshot_count": len(snapshots),
        "path_key": path_key,
    }


# ---------------------------------------------------------------------------
# AIPOS-C3C: N5 branch_integration 声明驱动 — 卡分支整合 (merge --no-ff)
# 声明是唯一真相: 分支命名/合并策略/信息格式/冲突策略全在 transitions.schema N5。
# finalize 读声明执行, 归属解析器读同一份声明; 生成什么格式就解析什么格式。
# ---------------------------------------------------------------------------

_DEFAULT_BRANCH_INTEGRATION = {
    "branch_pattern": "card/{task_id}",
    "merge_strategy": "no-ff",
    "merge_message_format": "Merge {branch}: {summary} ({verdict_id})",
    "auto_checkout": True,
    "auto_checkout_next_step": {
        "dirty_tree": {
            "audience": "self",
            "action": "处理未提交改动(提交或还原)后重试 finalize; 或手动 checkout main 后重试",
            "command": None,
        },
        "not_on_main": {
            "audience": "self",
            "action": "手动切回 main 分支后重试 finalize(auto_checkout 已关闭或切回失败)",
            "command": "git checkout main",
        },
    },
}


def _load_branch_integration(repo_root: Path) -> dict[str, Any]:
    """读 N5.branch_integration 声明 (单一真相); schema 缺失/损坏时回退默认。

    回退仅用于 schema 目录不存在的环境 (单元测试夹具), 且回退值与声明一致。
    """
    try:
        from tools.schema_loader import get_branch_integration
        branch_integration = get_branch_integration(repo_root)
        if isinstance(branch_integration, dict):
            return branch_integration
    except Exception:
        pass
    return dict(_DEFAULT_BRANCH_INTEGRATION)


def _branch_name_for_task(branch_pattern: str, task_id: str) -> str:
    """按声明 branch_pattern 派生分支名 ('card/{task_id}' → 'card/AIPOS-C3C')."""
    return branch_pattern.replace("{task_id}", task_id)


def _git_branch_exists(repo_root: Path, branch_name: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch_name}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False


def _git_branch_merged_into_main(repo_root: Path, branch_name: str) -> bool:
    """AIPOS-C3C/F11: 分支 tip 是否为 main 祖先 (已合并进 main)。

    F11 前 HEAD 恒为 main (交回前切回 main 纪律), 查 HEAD 等价查 main; auto_checkout 落地后
    HEAD 可能停在卡分支, 必须显式查 main, 否则卡分支对自身恒"已合并"→ 误跳过整合。
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch_name, "main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False


def _git_current_branch(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def _git_conflict_files(repo_root: Path) -> list[str]:
    """列出未合并 (冲突) 路径 (git diff --diff-filter=U)。"""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except subprocess.CalledProcessError:
        return []


def _git_dirty_files(repo_root: Path) -> list[str]:
    """AIPOS-F11 大项A: 列出工作树未提交改动 (git status --porcelain)。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def _render_next_step(branch_integration: dict[str, Any], key: str) -> str:
    """AIPOS-F11 大项A / F9: 从 branch_integration.auto_checkout_next_step 读 next_step 并渲染。

    出声点绝不手写下一步文案(F9 野读者); 只读声明 (audience/action/command)。
    """
    ns = (branch_integration.get("auto_checkout_next_step") or {}).get(key) or {}
    action = str(ns.get("action") or "").strip()
    if not action:
        return ""
    audience = str(ns.get("audience") or "self")
    command = ns.get("command")
    parts = [f"下一步({audience}): {action}"]
    if command:
        parts.append(f"命令: {command}")
    return " | ".join(parts)


def _blocked_dirty_tree(
    workspace_root: Path,
    branch_integration: dict[str, Any],
    branch_name: str,
) -> dict[str, Any]:
    """AIPOS-F11 大项A: 脏树拒绝体 — halt + 脏文件清单 + next_step (按 F9)。"""
    dirty_files = _git_dirty_files(workspace_root)
    dirty_list = "    - " + "\n    - ".join(dirty_files[:10]) if dirty_files else "    - (无法列出脏文件)"
    if len(dirty_files) > 10:
        dirty_list += f"\n    - ... 等 {len(dirty_files)} 个文件"
    next_step = _render_next_step(branch_integration, "dirty_tree")
    message = (
        f"工作树不干净, 无法合并 {branch_name} — 先处理未提交改动。"
        f"\n  脏文件:\n{dirty_list}"
        + (f"\n  {next_step}" if next_step else "")
    )
    return {"blocked": True, "action": "blocked_not_clean", "message": message}


def _ensure_on_main_branch(
    workspace_root: Path,
    branch_integration: dict[str, Any],
    operations: list[str],
    main_branch: str = "main",
) -> dict[str, Any] | None:
    """AIPOS-F11 大项A: auto_checkout 声明驱动 — 确保工作树在 main 分支。

    非 main 且树干净 + auto_checkout=true → 自行 checkout main 并出声;
    脏树 → 停下(halt+出声, 附脏文件 + next_step);
    auto_checkout=false → 停下喊人。已在 main → 放行 (脏树留给 merge/commit 步骤处理)。

    Returns:
        None = 已在 main(可继续); 否则返回拒绝体 {"blocked", "action", "message"}。
    """
    auto_checkout = bool(branch_integration.get("auto_checkout", True))
    current_branch = _git_current_branch(workspace_root)
    if current_branch == main_branch:
        return None

    if auto_checkout and _git_status_clean(workspace_root):
        co_result = subprocess.run(
            ["git", "checkout", main_branch],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
        )
        if co_result.returncode != 0:
            next_step = _render_next_step(branch_integration, "not_on_main")
            message = (
                f"auto_checkout 切回 {main_branch} 失败: "
                f"{(co_result.stderr or co_result.stdout or '').strip() or 'unknown'}"
                + (f"\n  {next_step}" if next_step else "")
            )
            operations.append(f"  → BLOCKED: {message}")
            return {"blocked": True, "action": "blocked_checkout_failed", "message": message}
        operations.append(
            f"  → ℹ️ 已自动切回 {main_branch} (auto_checkout 声明, 原分支 {current_branch})"
        )
        return None

    if not _git_status_clean(workspace_root):
        blk = _blocked_dirty_tree(workspace_root, branch_integration, f"(当前分支 {current_branch})")
        operations.append(f"  → BLOCKED: {blk['message']}")
        return blk

    next_step = _render_next_step(branch_integration, "not_on_main")
    message = (
        f"声明 auto_checkout=false, 当前在 '{current_branch}' 未自动切回 {main_branch} "
        f"— 需人工切回 main 后再 finalize"
        + (f"\n  {next_step}" if next_step else "")
    )
    operations.append(f"  → BLOCKED: {message}")
    return {"blocked": True, "action": "blocked_auto_checkout_disabled", "message": message}


def _task_title_summary(governance_root: Path, task_id: str) -> str:
    """Best-effort: 从治理仓任务卡 frontmatter title 提炼摘要 (剥离 task_id 前缀)。

    查找顺序: 5_tasks/queue/{claimed,completed,pending}/<id>.md → task_cards/<ID>/CARD.md。
    失败返回空串 (摘要非归属关键, 归属由 branch 卡号 + verdict_id 裁决号保证)。
    """
    candidates: list[Path] = []
    queue_dir = governance_root / "5_tasks" / "queue"
    for state in ("claimed", "completed", "pending"):
        state_dir = queue_dir / state
        if not state_dir.is_dir():
            continue
        for card in state_dir.glob("*.md"):
            if card.stem.lower() == task_id.lower():
                candidates.append(card)
    card_md = governance_root / "task_cards" / task_id / "CARD.md"
    if card_md.exists():
        candidates.append(card_md)

    for path in candidates:
        try:
            from tools.aipos_cli.frontmatter import parse_markdown_frontmatter
            metadata, _body, _warnings = parse_markdown_frontmatter(
                path.read_text(encoding="utf-8")
            )
            title = str(metadata.get("title") or "").strip()
            if title:
                if title.startswith(task_id):
                    title = title[len(task_id):]
                summary = title.lstrip(" :：-–—").strip()
                if summary:
                    return summary
        except Exception:
            continue
    return ""


def _find_similar_branches(repo_root: Path, task_id: str) -> list[str]:
    """AIPOS-F61: 找与 task_id 相近的本地分支(帮用户定位命名错误)。

    列出所有包含 task_id 的本地分支。
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "--format=%(refname:short)"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        all_branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
        return [b for b in all_branches if task_id in b]
    except Exception:
        return []


def _integrate_card_branch(
    task_id: str,
    verdict_id: str | None,
    workspace_root: Path,
    governance_root: Path,
    dry_run: bool,
    operations: list[str],
    branch_integration: dict[str, Any] | None = None,
    task_mode: str | None = None,
    output_target: str | None = None,
) -> dict[str, Any]:
    """AIPOS-C3C: 按 N5 branch_integration 声明执行卡分支整合 (merge --no-ff)。

    ①按 branch_pattern 派生分支名 → 找 card/<task_id>
    ②存在且未合并 → 检查工作树干净 + 当前在 main → merge --no-ff
      (信息格式由 merge_message_format 声明保证归属: 含卡号 + 裁决号)
    ③冲突 → 中止出声, 列冲突文件, 绝不自动解, main 无半合并残留
    ④分支不存在 → 代码任务(task_mode=code)硬 BLOCK(AIPOS-F61); 非代码任务跳过出声
    ⑤已合并 → 跳过出声
    分支保留 (不删除), 与既有惯例一致。

    Returns:
        {"branch_name", "action", "blocked", "message", "conflict_files"}
    """
    if branch_integration is None:
        branch_integration = _load_branch_integration(workspace_root)

    branch_pattern = str(branch_integration.get("branch_pattern") or "card/{task_id}")
    merge_strategy = str(branch_integration.get("merge_strategy") or "no-ff")
    message_format = str(
        branch_integration.get("merge_message_format")
        or "Merge {branch}: {summary} ({verdict_id})"
    )
    main_branch = "main"

    branch_name = _branch_name_for_task(branch_pattern, task_id)
    base = {"branch_name": branch_name, "blocked": False, "conflict_files": []}

    operations.append(
        f"Branch integration (N5 branch_integration): pattern={branch_pattern!r}, "
        f"strategy={merge_strategy}"
    )

    # ① 找分支
    if not _git_branch_exists(workspace_root, branch_name):
        # AIPOS-F61: 代码任务分支不存在 = 硬 BLOCK(禁假报成功 + 写错误 finalization 记录)
        # 非代码任务(无 output_target 或 task_mode != code)仍允许跳过
        is_code_task = (
            task_mode == "code"
            or (output_target and str(output_target).strip())
        )
        if is_code_task:
            similar = _find_similar_branches(workspace_root, task_id)
            similar_text = ", ".join(similar[:5]) if similar else "(无相近分支)"
            message = (
                f"BLOCKED: 未找到声明分支 {branch_name} (task_mode={task_mode or '?'}, "
                f"output_target={output_target or '?'}). "
                f"现有相近分支: {similar_text}. "
                f"可执行出口: 改名分支为 {branch_name}, 或在卡面指定正确分支模式。"
            )
            operations.append(f"  → {message}")
            return {**base, "action": "blocked_branch_not_found", "blocked": True, "message": message}
        message = f"无卡分支 {branch_name} (直提 main 的历史卡/无代码卡), 跳过整合"
        operations.append(f"  → {message}")
        return {**base, "action": "skipped_no_branch", "message": message}

    # 已合并进 main? (分支 tip 为 main 祖先)
    if _git_branch_merged_into_main(workspace_root, branch_name):
        message = f"分支 {branch_name} 已合并 (tip 为 main 祖先), 跳过整合"
        operations.append(f"  → {message}")
        return {**base, "action": "skipped_already_merged", "message": message}

    # ② 前置: 工作树干净 + 当前在 main
    # AIPOS-F11 大项A: auto_checkout 声明驱动 — 非 main 且树干净 → 自行 checkout main 并出声;
    # 脏树 → 保持现行为(halt+出声, 按 F9 带 next_step); auto_checkout=false → 停下喊人。
    # 开关值只读声明, 代码零写死; 逻辑单源在 _ensure_on_main_branch。
    ensure_main = _ensure_on_main_branch(workspace_root, branch_integration, operations, main_branch)
    if ensure_main is not None:
        return {**base, **ensure_main}

    # 合并前置: 已在 main 后工作树仍可能脏(直提 main 场景), git merge 需干净树。
    if not _git_status_clean(workspace_root):
        blk = _blocked_dirty_tree(workspace_root, branch_integration, branch_name)
        operations.append(f"  → BLOCKED: {blk['message']}")
        return {**base, **blk}

    # 合并信息 (声明格式保证归属: 含卡号 + 裁决号)
    summary = _task_title_summary(governance_root, task_id)
    merge_message = (
        message_format
        .replace("{branch}", branch_name)
        .replace("{summary}", summary)
        .replace("{verdict_id}", verdict_id or "unknown")
    )
    merge_message = re.sub(r"\s{2,}", " ", merge_message).strip()

    if dry_run:
        message = (
            f"DRY-RUN: 将 merge --no-ff {branch_name} → {main_branch}, "
            f"信息: {merge_message!r}"
        )
        operations.append(f"  → {message}")
        return {**base, "action": "merged", "message": message, "dry_run": True}

    # ③ merge --no-ff
    merge_cmd = ["git", "merge", "--no-ff", branch_name, "-m", merge_message]
    operations.append(f"  → 执行: {' '.join(merge_cmd)}")
    result = subprocess.run(
        merge_cmd,
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # 冲突 → 列文件 → abort → main 无半合并残留
        conflict_files = _git_conflict_files(workspace_root)
        operations.append(f"  → 冲突! 冲突文件: {conflict_files}")
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
        )
        clean_after_abort = _git_status_clean(workspace_root)
        message = (
            f"合并冲突, 中止 (main 无半合并残留): {len(conflict_files)} 个冲突文件 — "
            + (", ".join(conflict_files[:10]) if conflict_files else "(无 U 路径)")
        )
        if not clean_after_abort:
            message += " [警告: abort 后工作树仍不干净]"
        operations.append(f"  → BLOCKED: {message}")
        return {
            **base,
            "blocked": True,
            "action": "blocked_conflict",
            "message": message,
            "conflict_files": conflict_files,
        }

    operations.append(f"  → ✓ 已合并 {branch_name} (no-ff), 分支保留不删除")
    return {**base, "action": "merged", "message": f"已合并 {branch_name} (no-ff)"}


def finalize_task(
    task_id: str,
    actor: str,
    workspace_root: Path,
    *,
    governance_root: Path | None = None,
    dry_run: bool = False,
    push: bool = False,
    deploy: bool = False,
) -> dict[str, Any]:
    """Finalize a PASS task by committing changes to git.

    AIPOS-FINALIZE-FIX-1: finalize 只操作产品仓 git,绝不 commit/push 治理仓。
    治理仓(5_tasks/records/)的 git 操作归 N6 收账节点(顾问职责),executor 无权。
    
    AIPOS-FND-9: After commit, auto-deploys gate-side changes to prevent drift.
    AIPOS-FND-14 + FND-47: Audit eligibility is now checked against the authoritative gate
    audit verdict record (governance workspace 5_tasks/records/), NOT the task_cards
    AUDIT-REPORT markdown frontmatter. FND-47: record_type validation reads from enums.schema (single source).

    Args:
        task_id: Task ID to finalize
        actor: Actor performing the finalization
        workspace_root: Product code repo root (git operations run here) - must be product
            repo, NOT governance repo. finalize git commit/push only operates here.
        governance_root: Governance workspace root (owns 5_tasks/records/) - read-only for
            audit verdict check. NO git operations here. If None, resolved via
            resolve_workspace_root().
        dry_run: If True, only validate without committing
        push: If True, also push after commit (product repo only)
        deploy: If True, run lybra-deploy after push (AIPOS-R4B-2)

    Returns:
        {
            "verdict": Verdict.PASS | "BLOCK" | "FAIL",  # AIPOS-FINALIZE-FIX-1: deploy fail -> FAIL
            "task_id": str,
            "actor": str,
            "dry_run": bool,
            "can_finalize": bool,
            "integrity_check": dict,
            "branch_check": dict,  # AIPOS-R4B-2: deployment branch enforcement
            "committed": bool,
            "pushed": bool,
            "deployed": bool,
            "deployment_skipped": bool,
            "deployment_error": str | None,
            "commit_hash": str | None,
            "message": str,
            "operations": list[str]
        }
    """
    operations = []

    # AIPOS-FND-14: resolve governance root (where 5_tasks/records/ lives) separately from
    # workspace_root (the product code repo, where git commit/push runs). In the standard
    # two-root setup, workspace_root=~/projects/lybra and governance_root=
    # ~/ai-project-os/2_projects/lybra; they MUST NOT be conflated.
    if governance_root is None:
        from tools.aipos_cli.workspace_config import resolve_workspace_root
        try:
            governance_root = resolve_workspace_root()
        except FileNotFoundError as exc:
            operations.append(f"Cannot resolve governance root: {exc}")
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": False,
                "integrity_check": None,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                "message": f"Cannot locate governance workspace (5_tasks/records/): {exc}",
                "operations": operations,
            }

    operations.append(f"Governance root (audit verdicts): {governance_root}")
    operations.append(f"Product repo root (git ops): {workspace_root}")
    
    # AIPOS-R6A 靶子⑦: finalize 场地根治 — 硬拒治理仓 git 操作
    # workspace_root 必须是产品仓，绝不能是治理仓（218f8b7 实证：治理仓大扫除卷入 agency 记录）
    try:
        ws_resolved = workspace_root.resolve()
        gov_resolved = governance_root.resolve()
        
        # 检查 workspace_root 是否在治理仓路径下
        if ws_resolved == gov_resolved or str(ws_resolved).startswith(str(gov_resolved) + "/"):
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": False,
                "integrity_check": None,
                "branch_check": None,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                "message": (
                    f"BLOCKED: workspace_root ({ws_resolved}) is inside governance_root ({gov_resolved}). "
                    f"finalize git operations MUST run in product repo only. "
                    f"治理仓 git 归 N6 收账节点(顾问职责), executor 无权操作。"
                ),
                "operations": operations,
            }
    except Exception as exc:
        operations.append(f"Warning: Could not verify workspace/governance separation: {exc}")

    # AIPOS-FND-14: display-only — surface the task_cards AUDIT-REPORT frontmatter verdict
    # (if any) alongside the real gate verdict for operator visibility. Never judged.
    report_display = _report_frontmatter_verdict_for_display(workspace_root, task_id)
    if report_display["report_path"]:
        operations.append(
            f"(display only, not judged) task_cards AUDIT-REPORT frontmatter verdict: "
            f"{report_display['report_verdict']!r} at {report_display['report_path']}"
        )

    # AIPOS-F70: 获取当前 HEAD commit SHA 用于精确核对
    current_head = _git_rev_parse_head(workspace_root)
    if current_head:
        operations.append(f"Current HEAD: {current_head[:8]}")

    # Check if task can be finalized (gate audit verdict = PASS + 精确 SHA 覆盖)
    finalize_check = check_task_can_finalize(task_id, governance_root, commit_sha=current_head if current_head else None)
    operations.append(f"Checked finalize eligibility: {finalize_check['reason']}")
    
    # AIPOS-F70: legacy 裁决警告
    if finalize_check.get("is_legacy_verdict"):
        operations.append(
            "WARNING: 裁决为 legacy 版本 (无 artifact_subject), "
            "无法精确核对 commit SHA. 建议复审以获取精确裁决。"
        )
    
    if not finalize_check["can_finalize"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": False,
            "integrity_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": finalize_check["reason"],
            "operations": operations,
        }

    # AIPOS-R6M 大项A③: 阶段粒度门票 — finalize(发布门) 前校验 stage_archive 快照存在。
    # 判据与路径从 config.schema 治理目录树读(代码零写死), 缺快照 → BLOCK。
    stage_gate = check_stage_archive_gate(governance_root, repo_root=workspace_root)
    operations.append(f"Stage gate: {stage_gate['message']}")
    if not stage_gate["passed"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "stage_gate": stage_gate,
            "message": stage_gate["message"],
            "operations": operations,
        }

    # Check deployment integrity (current==HEAD)
    integrity = _check_deployment_integrity(workspace_root)
    operations.append(f"Deployment integrity: {integrity['message']}")
    
    if not integrity["integrity_ok"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": f"Deployment integrity check failed: {integrity['message']}",
            "operations": operations,
        }
    
    # AIPOS-F11 大项A: auto_checkout 声明驱动 — 部署分支强制之前先确保在 main。
    # 必须在 check_deployment_branch 之前执行, 否则卡分支上直接判"非 main"拦下,
    # 永远到不了整合步骤的自动切回("交回前切回 main"纪律就此退役)。
    branch_integration = _load_branch_integration(workspace_root)
    ensure_main = _ensure_on_main_branch(workspace_root, branch_integration, operations)
    if ensure_main is not None:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": None,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "branch_integration": ensure_main,
            "message": ensure_main["message"],
            "operations": operations,
        }

    # AIPOS-R4B-2: 部署分支强制 — finalize/deploy 只允许从 main 分支
    from tools.aipos_cli.deploy_gate import check_deployment_branch
    
    branch_check = check_deployment_branch(workspace_root, required_branch="main")
    operations.append(f"Branch check: {branch_check['message']}")
    
    # 如果要 push 或 deploy，必须在 main 分支上
    if (push or deploy) and not branch_check["on_required_branch"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": f"Deployment branch check failed: {branch_check['message']}",
            "operations": operations,
        }
    
    # AIPOS-C3C: N5 branch_integration 声明驱动 — 卡分支整合 (merge --no-ff, 保留分支)
    # 取代 AIPOS-R5A 的 squash 合并 + 删分支。规则活在 N5 声明, 代码零写死;
    # 冲突→中止出声列文件, 缺分支→代码任务 BLOCK(AIPOS-F61), 绝不自动解/绝不删分支。
    # AIPOS-F61: 读卡面 task_mode/output_target 传给分支整合, 代码任务缺分支=硬 BLOCK
    _task_mode_for_branch: str | None = None
    _output_target_for_branch: str | None = None
    try:
        from tools.aipos_cli.task_loader import find_task_by_id as _find_task
        _matches = _find_task(task_id, governance_root)
        if _matches[1]:
            _task_meta = _matches[1][0].get("metadata", {}) or _matches[1][0]
            _task_mode_for_branch = str(_task_meta.get("task_mode") or "").strip() or None
            _ot = _task_meta.get("output_target")
            if _ot:
                _output_target_for_branch = str(_ot).strip() if not isinstance(_ot, list) else ", ".join(str(x) for x in _ot)
    except Exception:
        pass  # 读卡失败不阻断, 降级为旧行为(跳过)
    integrate = _integrate_card_branch(
        task_id=task_id,
        verdict_id=finalize_check.get("verdict_id"),
        workspace_root=workspace_root,
        governance_root=governance_root,
        dry_run=dry_run,
        operations=operations,
        branch_integration=branch_integration,
        task_mode=_task_mode_for_branch,
        output_target=_output_target_for_branch,
    )
    if integrate["blocked"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "branch_integration": integrate,
            "message": integrate["message"],
            "operations": operations,
        }
    
    # AIPOS-F61: 跟踪是否有实际合并动作作——clean-tree 路径只有在实际合并时才写 finalization 记录
    # 防止把上一张卡的 commit 误记为本卡的 finalization 证据(F58 假成功根因)
    _actual_merge_happened = integrate.get("action") == "merged"

    # Check if there are changes to commit
    # AIPOS-R6A 靶子③: finalize push判据修正 — working tree clean ≠ already pushed
    # 需要检查 local vs origin 同步状态
    # AIPOS-R7A2 靶①(P0): clean-tree 早退必须检查 deploy 状态,禁静默跳过
    if _git_status_clean(workspace_root):
        synced = _git_local_origin_synced(workspace_root)
        current_commit = _git_rev_parse_head(workspace_root)
        
        # AIPOS-R7A2 靶①: 检查当前 commit 是否已部署
        # 有 PASS 裁决 + 未部署 → 必须 deploy 或显式 FAIL
        deployed_info = _read_deploy_current(workspace_root)
        deployed_commit = deployed_info.get("current_commit")
        needs_deploy = (deployed_commit != current_commit)
        
        # Case 1: working tree clean + synced → 检查 deploy 状态
        if synced:
            if needs_deploy:
                # AIPOS-R7A2 靶①(P0): 未部署但有 PASS 裁决 → 必须 deploy,不可静默跳过
                # 进入 finalize 说明已有 PASS 裁决,commit 未部署是闭环缺口,必须补
                operations.append(f"⚠️  Current commit {current_commit[:8]} not deployed (deployed: {deployed_commit[:8] if deployed_commit else 'none'})")
                operations.append("⚠️  PASS 裁决下的 commit 必须部署,触发强制 deploy...")
                
                from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
                if deploy_result["success"]:
                    operations.append("✓ Deploy completed successfully")
                    verification = verify_deployment_version(workspace_root, current_commit)
                    if verification["verified"]:
                        # AIPOS-F61: 只有实际合并才写 finalization 记录(禁把上一张卡的 commit 当证据)
                        if _actual_merge_happened:
                            _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), True, operations)
                        else:
                            operations.append("AIPOS-F61: 无实际合并动作, 跳过 finalization 记录(禁写错误 commit 证据)")
                        return {
                            "verdict": Verdict.PASS,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": dry_run,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": False,
                            "deployed": True,
                            "deployment_skipped": False,
                            "deployment_error": None,
                            "commit_hash": current_commit,
                            "message": f"No changes to commit, deployed {current_commit[:8]} to close gap",
                            "operations": operations,
                        }
                    else:
                        # Deploy 验证失败 → FAIL
                        return {
                            "verdict": Verdict.FAIL,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": dry_run,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": False,
                            "deployed": False,
                            "deployment_skipped": False,
                            "deployment_error": verification["message"],
                            "commit_hash": current_commit,
                            "message": f"Deploy verification failed: {verification['message']}",
                            "operations": operations,
                        }
                else:
                    # Deploy 失败 → FAIL
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": dry_run,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": False,
                        "pushed": False,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deploy_result["stderr"],
                        "commit_hash": current_commit,
                        "message": f"Deploy failed: {deploy_result['stderr'][:200]}",
                        "operations": operations,
                    }
            else:
                # 已部署 → 真正无事可做
                # AIPOS-F61: 只有实际合并才写 finalization 记录
                if _actual_merge_happened:
                    _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), True, operations)
                else:
                    operations.append("AIPOS-F61: 无实际合并动作, 跳过 finalization 记录(禁写错误 commit 证据)")
                return {
                    "verdict": Verdict.PASS,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": dry_run,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": False,
                    "pushed": False,
                    "deployed": False,
                    "deployment_skipped": True,
                    "deployment_error": None,
                    "commit_hash": current_commit,
                    "message": "No changes to commit (working tree clean, synced, and deployed)",
                    "operations": operations,
                }
        
        # Case 2: working tree clean but not synced → 需要 push (如果 push=True)
        if not push:
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": True,
                "integrity_check": integrity,
                "branch_check": branch_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": current_commit,
                "message": "Working tree clean but unpushed commits exist (use --push to push)",
                "operations": operations,
            }
        
        # Case 3: working tree clean, not synced, push=True → 执行 push
        if dry_run:
            operations.append("DRY-RUN: Would push unpushed commits to remote")
            if needs_deploy:
                operations.append("DRY-RUN: Would deploy to close deployment gap")
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": True,
                "can_finalize": True,
                "integrity_check": integrity,
                "branch_check": branch_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": current_commit,
                "message": "DRY-RUN: Would push unpushed commits",
                "operations": operations,
            }
        
        # Actually push
        try:
            operations.append("Pushing unpushed commits to remote...")
            subprocess.run(
                ["git", "push"],
                cwd=str(workspace_root),
                check=True,
                capture_output=True,
                text=True,
            )
            operations.append("Push successful")
            
            # AIPOS-R7A2 靶①: push 成功后检查 deploy 需求
            if needs_deploy:
                operations.append(f"⚠️  Current commit {current_commit[:8]} not deployed (deployed: {deployed_commit[:8] if deployed_commit else 'none'})")
                operations.append("Triggering deploy after push...")
                
                from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
                if deploy_result["success"]:
                    operations.append("✓ Deploy completed successfully")
                    verification = verify_deployment_version(workspace_root, current_commit)
                    if verification["verified"]:
                        # AIPOS-F61: 只有实际合并才写 finalization 记录
                        if _actual_merge_happened:
                            _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), True, operations)
                        else:
                            operations.append("AIPOS-F61: 无实际合并动作, 跳过 finalization 记录(禁写错误 commit 证据)")
                        return {
                            "verdict": Verdict.PASS,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": False,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": True,
                            "deployed": True,
                            "deployment_skipped": False,
                            "deployment_error": None,
                            "commit_hash": current_commit,
                            "message": "Pushed and deployed successfully",
                            "operations": operations,
                        }
                    else:
                        return {
                            "verdict": Verdict.FAIL,
                            "task_id": task_id,
                            "actor": actor,
                            "dry_run": False,
                            "can_finalize": True,
                            "integrity_check": integrity,
                            "branch_check": branch_check,
                            "committed": False,
                            "pushed": True,
                            "deployed": False,
                            "deployment_skipped": False,
                            "deployment_error": verification["message"],
                            "commit_hash": current_commit,
                            "message": f"Pushed but deploy verification failed: {verification['message']}",
                            "operations": operations,
                        }
                else:
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": False,
                        "pushed": True,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deploy_result["stderr"],
                        "commit_hash": current_commit,
                        "message": f"Pushed but deploy failed: {deploy_result['stderr'][:200]}",
                        "operations": operations,
                    }
            else:
                # 已部署,只需 push
                # AIPOS-F61: 只有实际合并才写 finalization 记录
                if _actual_merge_happened:
                    _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), True, operations)
                else:
                    operations.append("AIPOS-F61: 无实际合并动作, 跳过 finalization 记录(禁写错误 commit 证据)")
                return {
                    "verdict": Verdict.PASS,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": False,
                    "pushed": True,
                    "deployed": False,
                    "deployment_skipped": True,
                    "deployment_error": None,
                    "commit_hash": current_commit,
                    "message": "Pushed unpushed commits (already deployed)",
                    "operations": operations,
                }
        except subprocess.CalledProcessError as e:
            operations.append(f"Push failed: {e.stderr}")
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "can_finalize": False,
                "integrity_check": integrity,
                "branch_check": branch_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                "message": f"Push failed: {e.stderr}",
                "operations": operations,
            }
    
    if dry_run:
        operations.append("DRY-RUN: Would commit changes")
        if push:
            operations.append("DRY-RUN: Would push to remote")
        if deploy:
            operations.append("DRY-RUN: Would run lybra-deploy")
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": "DRY-RUN: Changes would be committed",
            "operations": operations,
        }
    
    # Commit changes
    commit_msg = f"feat({task_id}): finalize PASS task\n\nActor: {actor}\nAudit: {finalize_check['verdict']}"
    
    try:
        # AIPOS-R8B 大项A: Stage changes with pathspec限定到 workspace_root (产品仓)
        # 防止 git add -A 越界 stage 治理仓或其他项目的文件
        subprocess.run(
            ["git", "add", "-A", "--", "."],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        operations.append(f"Staged all changes (git add -A -- . in {workspace_root})")
        
        # AIPOS-R8B 大项A②: 断言 staged 文件全部落在 workspace_root 内,越界即 BLOCK
        staged_files_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        staged_files = [f.strip() for f in staged_files_result.stdout.split('\n') if f.strip()]
        
        # 检查 staged 文件是否全部在 workspace_root 内(相对路径不应以 ../ 开头)
        # 同时检查敏感路径(.lybra/connection.json 等)不应被 stage
        out_of_scope = []
        sensitive_files = []
        for staged_file in staged_files:
            # 相对路径以 ../ 开头或包含 ../ 说明越界
            if staged_file.startswith('../') or '/../' in staged_file:
                out_of_scope.append(staged_file)
            # 敏感路径检查 (AIPOS-R6K token 泄漏同病根)
            if staged_file.startswith('.lybra/') and any(sensitive in staged_file for sensitive in ['connection.json', 'role', 'token']):
                sensitive_files.append(staged_file)
        
        if out_of_scope:
            operations.append(f"SCOPE VIOLATION: {len(out_of_scope)} staged files outside workspace_root")
            out_of_scope_list = '\n  - '.join(out_of_scope[:10])  # 最多列10个
            if len(out_of_scope) > 10:
                out_of_scope_list += f'\n  - ... and {len(out_of_scope) - 10} more'
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "finalize_check": finalize_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "commit_hash": None,
                "message": f"SCOPE VIOLATION: Staged files outside workspace root (G3 铁律):\n  - {out_of_scope_list}",
                "operations": operations,
            }
        
        if sensitive_files:
            operations.append(f"SENSITIVE FILES BLOCKED: {len(sensitive_files)} credential/config files")
            sensitive_list = '\n  - '.join(sensitive_files)
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "finalize_check": finalize_check,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "commit_hash": None,
                "message": f"SENSITIVE FILES: Cannot commit credentials/config to product repo:\n  - {sensitive_list}",
                "operations": operations,
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
            cwd=str(workspace_root),
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = _git_rev_parse_head(workspace_root)
        operations.append(f"Committed changes: {commit_hash[:8]}")
        
        pushed = False
        if push:
            try:
                subprocess.run(
                    ["git", "push"],
                    cwd=str(workspace_root),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                operations.append("Pushed to remote")
                pushed = True
            except subprocess.CalledProcessError as e:
                operations.append(f"Push failed: {e.stderr}")
            except subprocess.TimeoutExpired:
                operations.append("Push timed out after 30s")
        
        # AIPOS-R4B-2 / AIPOS-FINALIZE-FIX-1: Explicit deploy with lybra-deploy
        # deploy 失败 → finalize 整体 FAIL (exit 非0 + verdict FAIL),禁吞错报成功
        deployed = False
        deployment_skipped = False
        deployment_error = None
        
        if deploy:
            # 显式 deploy 模式：直接调用 lybra-deploy
            from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
            
            operations.append("ℹ️  Invoking lybra-deploy (explicit deploy mode)...")
            deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
            
            if deploy_result["success"]:
                operations.append("✓ lybra-deploy completed successfully")
                # Append first 10 lines of output
                deploy_lines = deploy_result["stdout"].strip().splitlines()
                for line in deploy_lines[:10]:
                    operations.append(f"  {line}")
                if len(deploy_lines) > 10:
                    operations.append(f"  ... ({len(deploy_lines) - 10} more lines)")
                
                # Verify deployment
                verification = verify_deployment_version(workspace_root, commit_hash)
                operations.append(f"Deployment verification: {verification['message']}")
                if verification["verified"]:
                    deployed = True
                else:
                    # AIPOS-FINALIZE-FIX-1: 部署验证失败 → finalize FAIL
                    deployment_error = verification["message"]
                    operations.append(f"✗ Deployment verification FAILED: {verification['message']}")
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": True,
                        "pushed": pushed,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deployment_error,
                        "commit_hash": commit_hash,
                        "message": f"Deployment verification failed: {deployment_error}",
                        "operations": operations,
                    }
            else:
                # AIPOS-FINALIZE-FIX-1: deploy 子步失败 → finalize 整体 FAIL
                deployment_error = deploy_result["stderr"]
                operations.append(f"✗ lybra-deploy FAILED: {deploy_result['stderr'][:200]}")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": True,
                    "pushed": pushed,
                    "deployed": False,
                    "deployment_skipped": False,
                    "deployment_error": deployment_error,
                    "commit_hash": commit_hash,
                    "message": f"Deployment failed: {deployment_error[:200]}",
                    "operations": operations,
                }
        else:
            # F-R4B2-3: FND-9 Auto-deploy gate-side changes (无论 push 与否都检查)
            from tools.aipos_cli.gate_drift import check_gate_drift
            from tools.aipos_cli.deploy_gate import invoke_lybra_deploy
            
            drift_check = check_gate_drift(workspace_root)
            operations.append(f"Drift check: {drift_check['message']}")
            
            if drift_check["has_drift"] and drift_check["classification"]["has_gate_side_changes"]:
                # Gate-side changes detected - auto-deploy
                operations.append("⚠️  Gate-side changes detected - triggering auto-deploy...")
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor)
                if deploy_result["success"]:
                    operations.append("✓ Deployment completed successfully")
                    # Append deployment output (first 10 lines)
                    deploy_lines = deploy_result["stdout"].strip().splitlines()
                    for line in deploy_lines[:10]:
                        operations.append(f"  {line}")
                    if len(deploy_lines) > 10:
                        operations.append(f"  ... ({len(deploy_lines) - 10} more lines)")
                    deployed = True
                else:
                    # AIPOS-FINALIZE-FIX-1: 自动部署失败也必须 FAIL,禁吞错
                    deployment_error = deploy_result["stderr"]
                    operations.append(f"✗ Auto-deployment FAILED: {deploy_result['stderr'][:200]}")
                    return {
                        "verdict": Verdict.FAIL,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": False,
                        "can_finalize": True,
                        "integrity_check": integrity,
                        "branch_check": branch_check,
                        "committed": True,
                        "pushed": pushed,
                        "deployed": False,
                        "deployment_skipped": False,
                        "deployment_error": deployment_error,
                        "commit_hash": commit_hash,
                        "message": f"Auto-deployment failed: {deployment_error[:200]}",
                        "operations": operations,
                    }
            elif drift_check["has_drift"] and not drift_check["classification"]["has_gate_side_changes"]:
                operations.append("ℹ️  CLI-side changes only - no deployment needed")
                deployment_skipped = True
            else:
                operations.append("ℹ️  No drift detected - deployment up-to-date")
                deployment_skipped = True
        
        # Build final message
        final_message = f"Successfully committed changes: {commit_hash[:8]}"
        if deployed:
            final_message += " and deployed to gate"
        elif deployment_error:
            final_message += f" but deployment FAILED: {deployment_error[:100]}"
        
        # AIPOS-C3B 大项B③: 写 finalization 记录(必落,统一用 helper)
        if not dry_run:
            _ensure_finalization_record(governance_root, task_id, actor, commit_hash, finalize_check.get("verdict_id"), deployed, operations)
        
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": True,
            "pushed": pushed,
            "deployed": deployed,
            "deployment_skipped": deployment_skipped,
            "deployment_error": deployment_error,
            "commit_hash": commit_hash,
            "message": final_message,
            "operations": operations,
        }
        
    except subprocess.CalledProcessError as e:
        operations.append(f"Git operation failed: {e.stderr}")
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "can_finalize": True,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": f"Git operation failed: {e.stderr}",
            "operations": operations,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
