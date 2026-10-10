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

import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

from tools.schema_loader import get_enum_values
from tools.schema_constants import RecordType, Verdict
from tools.aipos_cli.record_writer import record_dir

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



def _check_deployment_integrity(repo_root: Path, governance_root: Path | None) -> dict[str, Any]:
    """AIPOS-C3 大项A: 部署完整性区间校验(current..HEAD 每个 commit 都属已 PASS 的卡)。

    使用 deployment_authorization.check_commit_interval_coverage 的统一实现。
    实证修复(2026-08-18 三层空洞): 取代原 current==HEAD 简单相等。

    AIPOS-F130 件①(gap #105, F129 finalize 死锁): governance_root 必传(禁默认 None 静默)——原唯一调用方
    _finalize_task_impl 未传, dev_override 基线的两条出口(Owner 授权记录 / 世系)永远不可达、区间校验恒被跳过(fail-open)。
    现 None = 拒(fail-closed, 出声), 不再「跳过区间校验」。

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

    if governance_root is None:
        return {
            "integrity_ok": False,
            "current_commit": None,
            "head_commit": head_commit,
            "provenance": None,
            "missing_commits": [],
            "message": ("部署完整性校验缺 governance_root(AIPOS-F130 件①: 禁默认 None 静默跳过), 无法核对裁决/Owner 授权记录; "
                        "出口: 调用方传入治理根(finalize --governance-root 或工作区解析)"),
        }
    
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
    # AIPOS-F78 前置零⑥(F73D/F79 实撞: 一次 dev_override 部署成了 finalize 全局锁): provenance 校验须认
    # Owner 授权记录(owner_decisions 记录引用该 commit)或世系(该 commit 已被门生 PASS 裁决覆盖)——认得即放行并出声。
    if provenance == "dev_override":
        authorized, why = _dev_override_base_authorized(repo_root, governance_root, current_commit)
        if not authorized:
            return {
                "integrity_ok": False,
                "current_commit": current_commit,
                "head_commit": head_commit,
                "provenance": provenance,
                "missing_commits": [],
                "message": (
                    f"Deployment provenance=dev_override (current={current_commit[:8]}). "
                    "finalize 拒绝在 dev_override 部署上结算 —— 必须先用审过的 commit 重部署 "
                    "(lybra-deploy --verdict-ref <pass_verdict_id>), 或 Owner 落授权记录(owner-decision 引用该 commit)。"
                    f" 判定: {why}"
                ),
            }
        print(f"Note: dev_override 部署基线 {current_commit[:8]} 已认可({why}), 继续区间校验", file=sys.stderr)
    # AIPOS-F130 件①: 认可依据进结果与 operations 行(出声, 不只 stderr)
    base_note = (f"dev_override 部署基线 {current_commit[:8]} 已认可({why}); "
                 if provenance == "dev_override" else "")

    if current_commit == head_commit:
        return {
            "integrity_ok": True,
            "current_commit": current_commit,
            "head_commit": head_commit,
            "provenance": provenance,
            "missing_commits": [],
            "message": f"{base_note}Deployment integrity OK: current == HEAD ({head_commit[:8]})",
        }

    # AIPOS-C3 大项A②: 区间校验统一实现(check_commit_interval_coverage); AIPOS-F130: governance_root 已在入口保证非 None
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
        "verdicts": coverage.get("verdicts", []),
        "message": f"{base_note}{coverage['message']}",
    }


def _dev_override_base_authorized(repo_root: Path, governance_root: Path | None, current_commit: str) -> tuple[bool, str]:
    """AIPOS-F78 前置零⑥: dev_override 部署基线是否可认。

    ① Owner 授权记录: 治理根 5_tasks/records/owner_decisions/ 下门生记录(record_type 以 owner_decision 开头)正文/frontmatter
       引用该 commit(≥8 位前缀); ② 世系: 该 commit 的卡有门生 PASS 裁决精确覆盖(F70)或 legacy 裁决。
    返回 (authorized, 原因)。governance_root 缺 = 不可认(fail-closed)。
    """
    if governance_root is None:
        return False, "无 governance_root, 无法核对 Owner 授权记录或世系"
    short = current_commit[:8]
    decisions_dir = record_dir(Path(governance_root), "owner_decisions")
    if decisions_dir.is_dir():
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

        for record in sorted(decisions_dir.rglob("*.md")):
            try:
                text = record.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                print(f"Warning: owner decision record unreadable {record}: {exc}", file=sys.stderr)
                continue
            metadata, _body, _warn = parse_markdown_frontmatter(text)
            record_type = str((metadata or {}).get("record_type") or "")
            if record_type.startswith("owner_decision") and short in text:
                return True, f"Owner 授权记录 {record.name} 引用 {short}"
    # 世系: commit 归属卡(或其世系)有门生 PASS 裁决且绑定覆盖该 commit —— AIPOS-F130 件②: 唯一实现 deployment_authorization.commit_pass_coverage
    # (精确绑定 / finalize merge --no-ff 合并提交第二父 = 裁决绑定的卡分支 tip / 绑定 tip 的祖先 / legacy)。原「以该 commit 本身找
    # required_commit_sha 精确裁决」对 merge 提交恒不成立(裁决绑卡分支 tip, 合并提交从不被裁决绑定)。
    from tools.aipos_cli.deployment_authorization import commit_pass_coverage
    from tools.schema_loader import SchemaLoadError

    try:
        coverage = commit_pass_coverage(repo_root, Path(governance_root), current_commit)
    except SchemaLoadError as exc:  # task_id_pattern 声明缺: 出声不吞(fail-closed)
        return False, f"世系核对声明缺失: {exc}"
    if coverage["covered"]:
        return True, f"世系: {coverage['reason']}"
    return False, f"世系不成立且无 Owner 授权记录引用 {short}: {coverage['reason']}"


class FinalizationRecordError(RuntimeError):
    """AIPOS-F120 件①: 合并(及部署)之后 finalization 记录写失败——finalize 整体 FAIL(原「告警 + 照报 PASS」= 记录缺失却报成功, fail-open)。
    出口 = 修复原因后重跑 finalize: 分支已合并且记录缺失 → 续跑补记录(件②), 不重复合并/部署。"""


def _ensure_finalization_record(
    governance_root: Path,
    task_id: str,
    actor: str,
    commit_hash: str,
    verdict_id: str | None,
    deployed: bool,
    operations: list[str],
    deploy_status: str | None = None,
    *,
    deployment_record_ref: str | None = None,
    dry_run: bool = False,
    regression: dict[str, Any] | None = None,
    push_status: str | None = None,
    push_status_reason: str | None = None,
    deploy_status_reason: str | None = None,
) -> dict[str, Any]:
    """AIPOS-C3B 大项B③: 写 finalization 记录(必落)。

    所有 finalize PASS 路径(含 working-tree-clean 早退)都必须调用此函数,
    确保 finalizations/ 目录有记录。三次 finalize 成功但 finalizations/ 全空
    的实撞必须不再发生。

    AIPOS-F73D 前置一①: merge/push 成功即落记录, 部署失败也落(deploy_status=deploy_failed),
    值域声明在 transitions.schema N5.record.deploy_status 一处; 推导核 N5→N6 只认此记录。
    AIPOS-F120 件①: 写失败 = 抛 FinalizationRecordError(finalize_task 收成 FAIL + 续跑出口), 不再告警后照报 PASS。
    AIPOS-F120 件②: 续跑补记录同走本函数(唯一写口); dry_run=True 只算出将写的记录(路径 + frontmatter), 不落盘。
    AIPOS-F135 件②③: push_status(+依据)与 deploy_status_reason 如实入记录(声明 transitions N5.record.push_status / deploy_status.reason_field)。
    """
    from tools.schema_loader import SchemaLoadError

    try:
        from tools.aipos_cli.clock import iso_z
        from tools.aipos_cli.finalization_record import write_finalization_record

        record_kwargs = dict(
            finalized_at=iso_z(),  # 一次取时: 同名核对与真写同一落盘名
            governance_root=governance_root,
            task_id=task_id,
            actor=actor,
            commit=commit_hash,
            authorization_type="verdict_ref",
            authorization_ref=verdict_id or "unknown",
            deployed=deployed,
            deployment_record_ref=deployment_record_ref,
            deploy_status=deploy_status,
            merge_commit=commit_hash,  # AIPOS-F78 前置零③: merge 后 main HEAD(直提场景=finalize 时 HEAD; 续跑=识别出的本卡合并提交)
            post_merge_regression=regression,  # AIPOS-F118 件①: 合并后回归结果(声明 transitions N5.record.post_merge_regression)
            push_status=push_status,
            push_status_reason=push_status_reason,
            deploy_status_reason=deploy_status_reason,
        )
        if not dry_run:
            # AIPOS-F130 件④: finalization 记录只追加——落盘名(finalize_ref)与已有记录同名(同秒同 actor)= 拒覆盖(fail-closed, 出声)
            planned = write_finalization_record(**record_kwargs, dry_run=True)
            if Path(planned["path"]).exists():
                raise FileExistsError(f"finalization 记录 {planned['path']} 已存在, 只追加不覆盖; 出口: 稍后重跑同一条 finalize")
        fin_result = write_finalization_record(**record_kwargs, dry_run=dry_run)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError, SchemaLoadError) as e:
        print(f"Error: finalization record write failed for {task_id}: {type(e).__name__}: {e}", file=sys.stderr)
        operations.append(f"✗ Finalization record write failed: {type(e).__name__}: {e}")
        raise FinalizationRecordError(f"{type(e).__name__}: {e}") from e
    verb = "would be written (dry-run)" if dry_run else "written"
    push_note = f", push_status={fin_result['frontmatter']['push_status']}" if fin_result["frontmatter"].get("push_status") else ""
    operations.append(f"Finalization record {verb}: {fin_result['path']} (deploy_status={fin_result['frontmatter'].get('deploy_status')}{push_note})")
    return fin_result


# ---------------------------------------------------------------------------
# AIPOS-F120 件①: 合并之后同一进程不得混用新旧代码
# 定因(靶场复现 F109): `lybra` 为 editable 安装, 代码源 = 产品仓工作树; finalize 的 merge --no-ff 在本进程运行中把代码源
# 换成了卡分支的新代码。之后首次懒加载的模块(finalization_record)读到新版, 它再向已加载的旧版 record_writer 要新符号
# (record_dir) → ImportError, 合并与部署都已完成而记录未落。修法: 合并前把合并后要用的产品模块全部预载(同一版本一次装齐),
# 合并后装导入闸——本进程再从磁盘加载任何未预载的产品模块即拒(_PostMergeImportBlocked, finalize 收成 FAIL + 续跑出口),
# 绝不静默混用。不另起子进程: 合并后的剩余步骤(推送/部署/记录)全由合并前已装齐的同一版本代码完成。
# ---------------------------------------------------------------------------

_PRODUCT_PACKAGE = __name__.split(".", 1)[0]

# 合并之后(推送 / 部署 / 部署核验 / 漂移检查 / 记录写入 / 续跑判定)直接用到的产品模块 = 预载根。预载集 = 根模块源码里全部产品包导入
# (含函数体内懒导入)的传递闭包(_post_merge_import_closure)——新增懒导入自动纳入, 不靠手抄清单; 闸门测试
# (tests/test_aipos_f120_finalize_resumable.py)在全新进程跑合并全链(代码源 = 产品仓, 合并改写代码源)证明闭合。
_POST_MERGE_ROOTS: tuple[str, ...] = (
    "tools.schema_loader",
    "tools.schema_constants",
    "tools.aipos_cli.clock",
    "tools.aipos_cli.frontmatter",
    "tools.aipos_cli.record_writer",
    "tools.aipos_cli.finalization_record",
    "tools.aipos_cli.deploy_gate",
    "tools.aipos_cli.deployment_authorization",
    "tools.aipos_cli.deployment_record",
    "tools.aipos_cli.gate_drift",
    "tools.aipos_cli.next_resolver",
    "tools.aipos_cli.post_merge_regression",  # AIPOS-F118: 合并后回归(check_after_merge / undo_merge / 事件记录; 闭包含 runall_discovery)
    "tools.aipos_cli.task_loader",
    "tools.aipos_cli.workspace_config",
)
# next_resolver 合并后只用 card_branch_name / card_base_branch(纯函数), 不展开其懒导入(展开 = 整个 CLI, 且合并前已加载)
_POST_MERGE_NO_DESCEND = frozenset({"tools.aipos_cli.next_resolver"})


def _is_product_module(name: str) -> bool:
    return name == _PRODUCT_PACKAGE or name.startswith(_PRODUCT_PACKAGE + ".")


def _post_merge_import_closure() -> list[str]:
    """预载集: 从 _POST_MERGE_ROOTS 出发, 静态扫源码(ast)里的产品包导入(模块级 + 函数体内), 取传递闭包。合并前调用(读的是旧版源码)。"""
    import ast
    import importlib.util

    seen: set[str] = set()
    stack = list(_POST_MERGE_ROOTS)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in _POST_MERGE_NO_DESCEND:
            continue
        spec = importlib.util.find_spec(name)
        origin = getattr(spec, "origin", None)
        if not origin or not str(origin).endswith(".py"):
            continue
        tree = ast.parse(Path(origin).read_text(encoding="utf-8"), filename=str(origin))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                stack.extend(alias.name for alias in node.names if _is_product_module(alias.name))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and _is_product_module(node.module):
                stack.append(node.module)
                parent = importlib.util.find_spec(node.module)
                for location in (getattr(parent, "submodule_search_locations", None) or []):
                    for alias in node.names:  # `from 包 import 子模块` 形
                        if (Path(location) / f"{alias.name}.py").is_file() or (Path(location) / alias.name / "__init__.py").is_file():
                            stack.append(f"{node.module}.{alias.name}")
    return sorted(seen)


class _PostMergeImportBlocked(ImportError):
    """合并后本进程试图从磁盘加载未预载的产品模块(会读到合并进来的新代码)。"""


class _PostMergeImportGuard:
    """meta path 闸: 只拦本产品包内、尚未加载的模块(已加载模块走 sys.modules 缓存, 不经 finder)。"""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        if fullname == _PRODUCT_PACKAGE or fullname.startswith(_PRODUCT_PACKAGE + "."):
            raise _PostMergeImportBlocked(
                f"AIPOS-F120: finalize 合并后禁止从磁盘加载未预载的产品模块 {fullname}"
                "(合并已改写代码源, 同进程会混用新旧代码); 出口: 把它的导入写进合并后路径模块源码(纳入预载闭包)或加入 finalize._POST_MERGE_ROOTS"
            )
        return None


def _preload_post_merge(governance_root: Path, task_id: str, operations: list[str]) -> None:
    """合并前预载合并后要用的产品模块与其声明缓存(record_dir 读 transitions.schema 经 load_schema 缓存), 然后装导入闸。"""
    import importlib

    modules = _post_merge_import_closure()
    for name in modules:
        importlib.import_module(name)
    record_dir(Path(governance_root), "finalizations", task_id)  # 记录落点声明在合并前读入缓存(合并后不再读新 schema)
    record_dir(Path(governance_root), "deployments")
    if not any(isinstance(finder, _PostMergeImportGuard) for finder in sys.meta_path):
        sys.meta_path.insert(0, _PostMergeImportGuard())
    operations.append(f"AIPOS-F120: 合并前已预载 {len(modules)} 个产品模块(合并后路径的导入闭包), 合并后禁止再从磁盘加载产品模块(同进程不混用新旧代码)")


def _remove_post_merge_guard() -> None:
    sys.meta_path[:] = [finder for finder in sys.meta_path if not isinstance(finder, _PostMergeImportGuard)]


# ---------------------------------------------------------------------------
# AIPOS-F120 件②: 续跑补记录——分支已合并、记录缺失时识别本卡合并提交, 补写 finalization 记录(不重复合并、不重复部署)
# ---------------------------------------------------------------------------

def _git_out(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True)


def find_card_merge_commit(
    workspace_root: Path,
    branch_name: str,
    base_branch: str,
    verdict_id: str | None,
    bound_commit: str | None,
) -> dict[str, Any]:
    """在基线分支第一父链上、卡分支 tip 之后的合并提交里找本卡那一个(唯一识别)。

    绑定判据(任一即候选): ① 第二父(被合并方) = 裁决 artifact_subject.commit_sha(裁决绑定的卡分支 tip);
    ② 合并信息含裁决号 verdict_id(N5 merge_message_format 写入)。候选恰好 1 个 = 找到; 0 个或多个 = 拒(F61: 禁写错误 commit 证据)。
    返回 {found, merge_commit, candidates, reason}。git 失败 = 拒(原文)。
    """
    tip = _git_out(workspace_root, "rev-parse", "--verify", f"refs/heads/{branch_name}^{{commit}}")
    if tip.returncode != 0:
        return {"found": False, "merge_commit": None, "candidates": [],
                "reason": f"卡分支 {branch_name} 不可解析: {tip.stderr.strip()}"}
    if not verdict_id and not bound_commit:
        return {"found": False, "merge_commit": None, "candidates": [],
                "reason": "裁决既无 verdict_id 也无 artifact_subject.commit_sha, 无绑定可识别合并提交"}
    log = _git_out(workspace_root, "log", "--first-parent", "--merges", "--format=%H%x1f%P%x1f%B%x1e",
                   base_branch, f"^{tip.stdout.strip()}")
    if log.returncode != 0:
        return {"found": False, "merge_commit": None, "candidates": [],
                "reason": f"git log {base_branch} 失败: {log.stderr.strip()}"}
    candidates: list[dict[str, Any]] = []
    for entry in log.stdout.split("\x1e"):
        entry = entry.strip("\n")
        if not entry.strip():
            continue
        sha, parents, body = (entry.split("\x1f") + ["", ""])[:3]
        by = []
        if bound_commit and bound_commit in parents.split()[1:]:
            by.append(f"第二父=裁决绑定 commit {bound_commit[:8]}")
        if verdict_id and verdict_id in body:
            by.append(f"合并信息含裁决号 {verdict_id}")
        if by:
            candidates.append({"commit": sha.strip(), "subject": body.strip().splitlines()[0] if body.strip() else "", "bound_by": by})
    if len(candidates) == 1:
        c = candidates[0]
        return {"found": True, "merge_commit": c["commit"], "candidates": candidates,
                "reason": f"唯一识别本卡合并提交 {c['commit'][:8]}({'; '.join(c['bound_by'])}): {c['subject']}"}
    if not candidates:
        reason = (f"{base_branch} 第一父链上 {branch_name} tip 之后无绑定本卡的合并提交"
                  f"(判据: 第二父 = {bound_commit[:8] if bound_commit else '(裁决无 artifact_subject)'} 或合并信息含 {verdict_id or '(无裁决号)'})")
    else:
        reason = f"绑定本卡的合并提交不唯一({len(candidates)} 个: {', '.join(c['commit'][:8] for c in candidates)})"
    return {"found": False, "merge_commit": None, "candidates": candidates, "reason": reason}


def _resume_deploy_status(
    workspace_root: Path,
    governance_root: Path,
    merge_commit: str,
    deploy_judgement: dict[str, Any],
    operations: list[str],
) -> dict[str, Any]:
    """续跑的 deploy_status 判定(只读, 不部署): ① 门生部署记录(record_locations.kinds.deployments)commit = 合并提交 → deployed;
    ② 当前部署(.deploy/current/VERSION git_commit)= 合并提交或其后代 → deployed; ③ 部署不适用(deploy_gate.deploy_mechanism_present
    判定: 声明本仓不部署 = not_applicable / 无部署机制 = skipped, AIPOS-F135 件③)→ 该状态 + 依据; ④ 否则 not_attempted。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    deployments_dir = record_dir(Path(governance_root), "deployments")
    short = merge_commit[:8]
    if deployments_dir.is_dir():
        # 只读文件名/目录绑定该提交的部署记录(现名 deployment_<时间>_<commit8>.md; 存量 <commit8>/deployment_*.md), 再以 frontmatter commit 全等确认
        named = [r for r in sorted(deployments_dir.rglob("deployment_*.md")) if r.stem.endswith(f"_{short}") or r.parent.name == short]
        for record in named:
            try:
                metadata, _body = require_frontmatter(record)
            except FrontmatterReadError as exc:
                operations.append(f"⚠️  部署记录读不出, 不计入续跑判定: {exc}")
                continue
            if str(metadata.get("commit") or "").strip() == merge_commit:
                return {"deploy_status": "deployed", "deployed": True, "deployment_record_ref": record.stem,
                        "reason": f"部署记录 {record.name} commit = 合并提交 {merge_commit[:8]}"}
    current = _read_deploy_current(workspace_root).get("current_commit")
    if current:
        if current == merge_commit:
            return {"deploy_status": "deployed", "deployed": True, "deployment_record_ref": None,
                    "reason": f"当前部署 .deploy/current = 合并提交 {merge_commit[:8]}(无对应部署记录)"}
        if _git_out(workspace_root, "merge-base", "--is-ancestor", merge_commit, current).returncode == 0:
            return {"deploy_status": "deployed", "deployed": True, "deployment_record_ref": None,
                    "reason": f"当前部署 {current[:8]} 含合并提交 {merge_commit[:8]}(其后代)"}
    if not deploy_judgement["applicable"]:
        return {"deploy_status": deploy_judgement["deploy_status"], "deployed": False, "deployment_record_ref": None,
                "reason": f"部署不适用: {deploy_judgement['reason']}"}
    return {"deploy_status": "not_attempted", "deployed": False, "deployment_record_ref": None,
            "reason": f"无部署记录且当前部署({current[:8] if current else '无'})不含合并提交 {merge_commit[:8]}; 续跑不部署"}


def _git_push(workspace_root: Path) -> None:
    """AIPOS-F130 件④ / F135 件②: 推送基线分支的唯一实现——finalize 主路径(净树/脏树两支)与续做路径同走; 失败抛 CalledProcessError
    (调用方收成 FAIL, 不写记录: 声明 transitions N5.record.deploy_status.written_when「push 失败不写」)。推送前先经
    _push_not_applicable_reason 判适用性(无远端 = push_status=not_applicable, 不调本函数)。产品源码其余处禁写 git push
    (不变量夹具 tests/test_aipos_f135_finalize_repo_serial.py)。"""
    subprocess.run(["git", "push"], cwd=str(workspace_root), check=True, capture_output=True, text=True)


def _push_not_applicable_reason(workspace_root: Path, base_branch: str) -> str | None:
    """AIPOS-F135 件②: 「推送是否适用」唯一判据(主路径 / 续做路径同口径, 声明 transitions N5.record.push_status)。
    无 origin 远端, 或无远端跟踪分支 origin/<基线> = 不适用, 返回依据文本(进 finalization 记录 push_status_reason 与输出);
    适用 = None。原「无 origin 视为已同步」静默收尾(结果不可辨)由此如实标注 push_status=not_applicable。"""
    if _git_out(workspace_root, "remote", "get-url", "origin").returncode != 0:
        return f"产品仓 {workspace_root} 无 origin 远端, 推送不适用"
    remote = _git_out(workspace_root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base_branch}^{{commit}}")
    if remote.returncode != 0:
        return f"无远端跟踪分支 origin/{base_branch}, 推送不适用"
    return None


def _push_pending(workspace_root: Path, base_branch: str, merge_commit: str) -> dict[str, Any]:
    """AIPOS-F130 件④: 本卡合并提交是否已推送 = 它在远端跟踪分支 origin/<基线> 上。推送不适用(AIPOS-F135: 判据
    _push_not_applicable_reason, 与主路径同口径)= not_applicable。返回 {pending, not_applicable, reason}。"""
    not_applicable = _push_not_applicable_reason(workspace_root, base_branch)
    if not_applicable is not None:
        return {"pending": False, "not_applicable": True, "reason": not_applicable}
    remote_head = _git_out(workspace_root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base_branch}^{{commit}}").stdout.strip()
    if _git_out(workspace_root, "merge-base", "--is-ancestor", merge_commit, remote_head).returncode == 0:
        return {"pending": False, "not_applicable": False,
                "reason": f"本卡合并提交 {merge_commit[:8]} 已在 origin/{base_branch}({remote_head[:8]})"}
    ahead = _git_out(workspace_root, "rev-list", "--count", f"{remote_head}..HEAD").stdout.strip() or "?"
    return {"pending": True, "not_applicable": False,
            "reason": f"本卡合并提交 {merge_commit[:8]} 不在 origin/{base_branch}({remote_head[:8]}), 本地 {base_branch} 领先远端 {ahead} 个提交(推送未完成)"}


class RepoMergeBusy(RuntimeError):
    """AIPOS-F135 件①: 同仓另一 finalize 正持有仓级合入锁(可重试; 不排队不抢)。holder = 持锁方自述(卡号/进程/起始时间)。"""

    def __init__(self, lock_path: Path, holder: str):
        super().__init__(f"同仓合入进行中: 锁 {lock_path} 被持有({holder or '持锁方未留自述'})")
        self.lock_path = lock_path
        self.holder = holder


class RepoMergeLockError(RuntimeError):
    """AIPOS-F135 件①: 仓级合入锁无法建立(产品仓 git common-dir 取不到 / 锁文件打不开 / 声明缺)= finalize BLOCK(fail-closed, 不无锁合并)。"""


def _repo_merge_lock_declaration(branch_integration: dict[str, Any]) -> dict[str, Any]:
    decl = branch_integration.get("repo_merge_lock")
    if not isinstance(decl, dict) or not str(decl.get("lock_file") or "").strip() or not str(decl.get("busy_category") or "").strip():
        raise RepoMergeLockError("transitions.schema.json nodes.N5.branch_integration.repo_merge_lock(lock_file/busy_category)未声明")
    return decl


@contextlib.contextmanager
def _repo_merge_lock(workspace_root: Path, task_id: str, branch_integration: dict[str, Any]) -> Iterator[Path]:
    """AIPOS-F135 件①: 同仓合入串行——对产品仓 git common-dir 下锁文件(声明 N5.branch_integration.repo_merge_lock.lock_file)
    加非阻塞排他锁(唯一锁实现 workspace_config.exclusive_flock, 与 project.json 写路径同一实现; 不起锁服务)。
    common-dir 对同一仓的所有工作树相同 = 各工作树上的 finalize 也互斥。拿到锁后写持锁方自述(卡号 / pid / 起始时间), 释放前清空;
    已被持有 = RepoMergeBusy(带持锁方自述); 无法建锁 = RepoMergeLockError。锁随进程退出由内核释放。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.workspace_config import FileLockBusy, exclusive_flock

    decl = _repo_merge_lock_declaration(branch_integration)
    common = _git_out(workspace_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode != 0 or not common.stdout.strip():
        raise RepoMergeLockError(f"取不到产品仓 {workspace_root} 的 git common-dir: {(common.stderr or '').strip()[:200]}")
    lock_path = Path(common.stdout.strip()) / str(decl["lock_file"]).strip()
    try:
        handle = open(lock_path, "a+", encoding="utf-8")
    except OSError as exc:
        raise RepoMergeLockError(f"仓级合入锁文件 {lock_path} 打不开: {exc}") from exc
    with handle, contextlib.ExitStack() as held:  # 退出序: 先清自述并解锁(held), 再关文件
        try:
            held.enter_context(exclusive_flock(handle, wait=False))
        except FileLockBusy:
            handle.seek(0)
            raise RepoMergeBusy(lock_path, handle.read().strip()[:300]) from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"task_id": task_id, "pid": os.getpid(), "since": iso_z()}, ensure_ascii=False))
            handle.flush()
            yield lock_path
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()


def _latest_record_merge_commit(records: list[Path]) -> tuple[str | None, str]:
    """已有 finalization 记录(新到旧)里最新一条的 merge_commit(缺省 commit)。读不出 = (None, 原因)(调用方拒, fail-closed)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    try:
        metadata, _body = require_frontmatter(records[0])
    except FrontmatterReadError as exc:
        return None, f"finalization 记录读不出: {exc}"
    merge_commit = str(metadata.get("merge_commit") or metadata.get("commit") or "").strip()
    if not merge_commit:
        return None, f"finalization 记录 {records[0].name} 无 merge_commit/commit"
    return merge_commit, f"已有 finalization 记录 {records[0].name} merge_commit={merge_commit[:8]}"


def _resume_finalization(
    *,
    task_id: str,
    actor: str,
    workspace_root: Path,
    governance_root: Path,
    dry_run: bool,
    push: bool,
    deploy: bool,
    finalize_check: dict[str, Any],
    branch_name: str,
    base_branch: str,
    integrity: dict[str, Any],
    branch_check: dict[str, Any],
    operations: list[str],
    existing_records: list[Path],
) -> dict[str, Any] | None:
    """分支已合并(本次无合并动作)时的续做。

    AIPOS-F120 件②: finalization 记录缺失 → 识别本卡合并提交并补写记录(merge_commit = 该提交); 找不到唯一合并提交 = BLOCK 带出口
    (F61 禁写错误 commit 证据)。
    AIPOS-F130 件④(F127 实撞: 首跑合并后推送 500 → 记录未落、部署未做; 续做只补记录、不推不部署却报 PASS): 续做先补完未完成的
    推送(本卡合并提交不在 origin/<基线>)与部署(当前部署不含本卡合并提交), 与主路径同一实现(_git_push / deploy_gate.invoke_lybra_deploy
    + verify_deployment_version), 结果如实写回: 记录缺失 → 补写; 记录已有但推送/部署未完成 → 追加一条新记录(只追加, 不改旧记录)。
    推送未完成而未带 --push = BLOCK(不报成功); 推送失败 = FAIL 不写记录(N5 written_when); 部署失败 = 记录 deploy_status=deploy_failed + FAIL。
    部署只在带 --deploy 时执行(未带 = 如实 not_attempted, AIPOS-F120 语义不变)。
    记录已有且推送/部署均已完成(或未请求部署)→ 返回 None, 调用方维持原行为。
    """
    from tools.aipos_cli.deploy_gate import deploy_mechanism_present, invoke_lybra_deploy, verify_deployment_version

    verdict_id = finalize_check.get("verdict_id")
    base = {
        "task_id": task_id, "actor": actor, "dry_run": dry_run, "can_finalize": True,
        "integrity_check": integrity, "branch_check": branch_check,
        "committed": False, "pushed": False, "resumed": True,
    }
    if existing_records:
        merge_commit, why = _latest_record_merge_commit(existing_records)
        if merge_commit is None:
            message = f"BLOCKED: 续做判定读不出本卡已有 finalization 记录({why}); 出口: 修复该记录文件后重跑同一条 finalize"
            operations.append(f"  → {message}")
            return {**base, "verdict": Verdict.BLOCK, "deployed": False, "deployment_skipped": False,
                    "deployment_error": None, "commit_hash": None, "message": message, "operations": operations}
    else:
        subject = finalize_check.get("artifact_subject") or {}
        bound_commit = str(subject.get("commit_sha") or "").strip() or None
        found = find_card_merge_commit(workspace_root, branch_name, base_branch, verdict_id, bound_commit)
        operations.append(f"AIPOS-F120 续跑判定: {found['reason']}")
        if not found["found"]:
            message = (
                f"BLOCKED: 分支 {branch_name} 已合并但 finalization 记录缺失, 且{found['reason']}——不补写记录(AIPOS-F61: 禁写错误 commit 证据)。"
                f"出口: 核对 `git log --first-parent --merges {base_branch}` 中本卡的合并提交(须第二父 = 裁决绑定 commit 或合并信息含裁决号);"
                " 无法唯一识别时交顾问/Owner 裁定, 禁手写 finalization 记录。"
            )
            operations.append(f"  → {message}")
            return {**base, "verdict": Verdict.BLOCK, "deployed": False, "deployment_skipped": False,
                    "deployment_error": None, "commit_hash": None, "merge_candidates": found["candidates"],
                    "message": message, "operations": operations}
        merge_commit = str(found["merge_commit"])
        why = found["reason"]

    deploy_judgement = deploy_mechanism_present(workspace_root, governance_root)  # AIPOS-F135 件③: 含「本仓不部署」声明(合并前已预检)
    push_state = _push_pending(workspace_root, base_branch, merge_commit)
    deploy_state = _resume_deploy_status(workspace_root, governance_root, merge_commit, deploy_judgement, operations)
    deploy_pending = deploy_state["deploy_status"] == "not_attempted"
    if existing_records:
        if not push_state["pending"] and not (deploy_pending and deploy):
            return None  # 记录已有、推送已完成、部署已完成或未请求 → 维持原行为
        operations.append(f"AIPOS-F130 续做判定: {why}; 推送: {push_state['reason']}; 部署: {deploy_state['reason']}")
    else:
        operations.append(f"AIPOS-F130 续做判定: 推送: {push_state['reason']}; 部署: {deploy_state['reason']}")

    pushed = False
    if push_state["pending"]:
        if not push:
            message = (f"BLOCKED: 续做: {push_state['reason']}; 未带 --push, 不推送也不报成功(AIPOS-F130 件④)。"
                       f"出口: 带 --push 重跑同一条 finalize(续做推送{'与部署' if deploy_pending else ''}后{'追加' if existing_records else '补写'} finalization 记录)")
            operations.append(f"  → {message}")
            return {**base, "verdict": Verdict.BLOCK, "deployed": False, "deployment_skipped": False,
                    "deployment_error": None, "commit_hash": merge_commit, "merge_commit": merge_commit,
                    "message": message, "operations": operations}
        if dry_run:
            operations.append(f"DRY-RUN: 续做将推送 {base_branch}(同主路径 git push)")
        else:
            try:
                _git_push(workspace_root)
            except subprocess.CalledProcessError as exc:
                message = (f"续做推送失败: {(exc.stderr or '').strip()[:300]}; finalization 记录不写(N5: push 失败不写)。"
                           "出口: 远端恢复/修复原因后重跑同一条 finalize(续做重试推送, 再部署并写记录)")
                operations.append(f"✗ {message}")
                return {**base, "verdict": Verdict.FAIL, "deployed": False, "deployment_skipped": False,
                        "deployment_error": None, "commit_hash": merge_commit, "merge_commit": merge_commit,
                        "category": "FINALIZE_RESUME_PUSH_FAILED", "message": message, "operations": operations}
            pushed = True
            operations.append(f"✓ 续做推送成功({base_branch} → origin)")

    deploy_status = deploy_state["deploy_status"]
    deployed = deploy_state["deployed"]
    deployment_record_ref = deploy_state["deployment_record_ref"]
    deployment_error: str | None = None
    if deploy_pending and deploy:
        head = _git_rev_parse_head(workspace_root)
        if dry_run:
            operations.append(f"DRY-RUN: 续做将部署 HEAD {head[:8]}(verdict_ref={verdict_id}; 同主路径 invoke_lybra_deploy + 部署核验)")
        else:
            operations.append(f"续做部署 HEAD {head[:8]}(当前部署不含本卡合并提交 {merge_commit[:8]})...")
            deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=verdict_id, actor=actor, governance_root=governance_root)
            if deploy_result["success"]:
                verification = verify_deployment_version(workspace_root, head)
                if verification["verified"]:
                    deploy_status, deployed = "deployed", True
                    operations.append(f"✓ 续做部署完成并核验: {verification['message']}")
                else:
                    deploy_status, deployed, deployment_error = "deploy_failed", False, verification["message"]
                    operations.append(f"✗ 续做部署核验失败: {verification['message']}")
            else:
                deploy_status, deployed = "deploy_failed", False
                deployment_error = str(deploy_result.get("stderr") or "")[:500]
                operations.append(f"✗ 续做部署失败: {deployment_error[:200]}")
            if deployed:
                # 部署记录(若部署脚本落了绑定本提交的门生记录)回填引用
                after = _resume_deploy_status(workspace_root, governance_root, merge_commit, deploy_judgement, operations)
                deployment_record_ref = after["deployment_record_ref"]

    # AIPOS-F118: 续做无合并动作 → 合并后回归不重跑, 记 skipped 并注明(不静默缺字段)
    from tools.aipos_cli.post_merge_regression import summary_line

    regression = {"status": "skipped", "action": "none",
                  "reason": f"续跑补记录: 本卡合并提交 {merge_commit[:8]} 已在先前 finalize 合入, 本次无合并动作, 不重跑合并后回归"}
    regression["summary"] = summary_line(regression)
    operations.append(regression["summary"])
    # AIPOS-F135 件②: 续做与主路径同口径如实标注推送(判据 _push_not_applicable_reason)
    push_status = ("pushed" if pushed or (dry_run and push_state["pending"]) else "not_applicable" if push_state["not_applicable"]
                   else "not_requested" if push_state["pending"] else "already_synced")
    operations.append(f"推送: push_status={push_status}({push_state['reason']})")
    fin = _ensure_finalization_record(
        governance_root, task_id, actor, merge_commit, verdict_id, deployed, operations,
        deploy_status=deploy_status, deployment_record_ref=deployment_record_ref, dry_run=dry_run,
        regression=regression, push_status=push_status, push_status_reason=push_state["reason"],
        deploy_status_reason=deploy_state["reason"] if deploy_status in ("skipped", "not_applicable") else None,
    )
    verb = ("DRY-RUN 续做判定: 将" if dry_run else "续做: 已") + ("追加" if existing_records else "补写")
    done = [x for x, on in (("推送", pushed or (dry_run and push_state["pending"])),
                            ("部署", deployed and deploy_pending)) if on]
    message = (f"{verb} finalization 记录 (merge_commit={merge_commit[:8]}, deploy_status={deploy_status})"
               f"{'; 续做补完: ' + '/'.join(done) if done else ''}; 未重复合并")
    if deployment_error:
        message += f"; 部署失败: {deployment_error[:200]}; 出口: 修复部署原因后重跑同一条 finalize(续做重试部署并追加记录)"
    return {**base, "verdict": Verdict.FAIL if deploy_status == "deploy_failed" else Verdict.PASS,
            "pushed": pushed, "push_status": push_status, "deploy_status": deploy_status, "deployed": deployed,
            "deployment_skipped": deploy_status == "skipped", "deployment_error": deployment_error,
            "commit_hash": merge_commit, "merge_commit": merge_commit,
            "finalization_record": {"path": fin["path"], "wrote": fin["wrote"], "frontmatter": fin["frontmatter"]},
            "message": message, "operations": operations}


def _report_frontmatter_verdict_for_display(governance_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-FND-14: best-effort, DISPLAY-ONLY lookup of the audit report frontmatter ``verdict:`` field.

    AIPOS-F89 件① H9: 报告落点只读项目声明——审计报告就绪判据唯一实现 next_resolver._check_verdict_artifact
    (<paths.verdict_root>/<审计卡ID>/ + transitions artifact_ingest.verdict 候选), 治理根内; 原写死
    `<workspace_root>/task_cards/<ID>/AUDIT-REPORT-*.md`(且落在产品仓)退役。

    This is NEVER judged for finalize eligibility (see ``check_task_can_finalize`` below — the report is an editable
    markdown file). It is surfaced purely so operators can see what the (non-authoritative) report says alongside the
    real gate verdict. Read failures are reported on stderr and yield an empty display (never block/alter finalize).
    """
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.audit_derivation import current_audit_task_id
    from tools.aipos_cli.next_resolver import _check_verdict_artifact, _read_frontmatter
    from tools.schema_loader import SchemaLoadError

    try:
        # AIPOS-F112: 展示当前一轮审计卡(R2/R3…)的报告, 原写死 <ID>R
        report_path = _check_verdict_artifact(Path(governance_root), current_audit_task_id(task_id, Path(governance_root)))
        if report_path is None:
            return {"report_path": None, "report_verdict": None}
        return {"report_path": str(report_path),
                "report_verdict": _read_frontmatter(report_path, allow_missing_block=True).get("verdict")}
    except FrontmatterReadError as exc:
        # AIPOS-F100 件②: 展示面不省略——报告读不出时显示该文件与「读不出: <路径>: <原因>」(仍只展示, 不判 finalize)
        return {"report_path": exc.path, "report_verdict": str(exc)}
    except (SchemaLoadError, OSError, ValueError) as exc:
        print(f"Warning: audit report display lookup failed for {task_id}: {exc}", file=sys.stderr)
        return {"report_path": None, "report_verdict": None}


def _actor_is_claimer(governance_root: Path, task_id: str, actor: str) -> dict[str, Any]:
    """AIPOS-F78B 件⑤b: finalize actor==claimer 判据(与 queue_mutation complete / close_task 同一函数 actor_matches_task_actor)。
    卡不在 queue(历史卡/靶场无卡)或卡面无 claimed_by = 无据可判 → 放行并出声; 有 claimed_by 且不匹配 = 拒。"""
    from tools.aipos_cli.agent_profiles import actor_matches_task_actor, load_agent_profiles
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    try:
        card_path, _state = find_task_card(governance_root, task_id)
    except AmbiguousTaskCard as exc:
        return {"ok": False, "reason": str(exc)}
    if card_path is None:
        return {"ok": True, "reason": f"task card {task_id} not in queue; claimer unknown (not judged)"}
    try:
        # AIPOS-F100 件②: 卡面「必须读出」; 读不出 = 拒(原「解析告警被忽略 → claimed_by 空 → not judged 放行」退役)
        metadata, _body = require_frontmatter(card_path)
    except FrontmatterReadError as exc:
        return {"ok": False, "reason": f"task card unreadable for claimer check: {exc}"}
    claimed_by = str(metadata.get("claimed_by") or "").strip()
    if not claimed_by:
        return {"ok": True, "reason": f"task card {task_id} has no claimed_by; claimer unknown (not judged)"}
    profiles = load_agent_profiles(governance_root)
    matches = actor_matches_task_actor(actor, claimed_by, profiles) if profiles else claimed_by == actor
    if not matches:
        return {"ok": False, "reason": f"finalize actor {actor!r} is not the claimer {claimed_by!r} of {task_id} (actor must equal the claim instance, same rule as close/return/verdict)"}
    return {"ok": True, "reason": "actor == claimer"}


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
        "artifact_subject": verdict_check.get("artifact_subject"),  # AIPOS-F120 件②: 续跑按裁决绑定 commit 识别本卡合并提交
        "reason": verdict_check["reason"],
    }


def check_stage_archive_gate(governance_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """AIPOS-R6M 大项A③: 阶段粒度门票 — stage transition (finalize/发布门) 前校验阶段快照存在。

    判据从 config.schema ``timeline_enforcement.stage_level`` 读(目录内 .md 排除 README/index, 代码零写死);
    AIPOS-F145 件①: 落点经唯一读取口 ``workspace_config.stage_archive_root``(project.json paths.stage_archive_root 声明,
    未声明 = governance_structure.paths.<stage_level.path_key>, 存量行为不变)。缺阶段快照 → BLOCK
    (门票机制: 阶段快照=转换前提, 缺快照=未关账=不许转换); 件②: 拒因附出口(stage_level.next_step, 声明他处落点)。

    Args:
        governance_root: 治理工作区根 (拥有 project.json 与阶段档案的根, 非产品仓)。
        repo_root: 产品仓根 (用于定位 schema/config.schema.json 单一源)。

    Returns:
        {"passed": bool, "message": str, "stage_archive_dir": str|None,
         "snapshot_count": int, "path_key": str|None, "declared": bool|None}
    """
    try:
        from tools.aipos_cli.workspace_config import STAGE_ARCHIVE_PATH_KEY, project_paths
        from tools.schema_loader import SchemaLoadError, get_governance_structure

        gs = get_governance_structure(repo_root)
        stage_level = (gs.get("timeline_enforcement") or {}).get("stage_level") or {}
        next_step = str(stage_level.get("next_step") or "").strip()
        if "{key}" not in next_step:
            raise SchemaLoadError("config.schema governance_structure.timeline_enforcement.stage_level.next_step 未声明(须含 {key})")
        next_step = next_step.format(key=STAGE_ARCHIVE_PATH_KEY)
        resolved = project_paths(governance_root)
        stage_dir = Path(resolved[STAGE_ARCHIVE_PATH_KEY])  # = workspace_config.stage_archive_root(同一读取口, 已解析一次复用)
        declared = bool(resolved["declared"][STAGE_ARCHIVE_PATH_KEY])
    except Exception as exc:
        return {
            "passed": False,
            "message": f"Stage gate config load failed: {exc}",
            "stage_archive_dir": None,
            "snapshot_count": 0,
            "path_key": None,
            "declared": None,
        }

    source = (f"落点来源: project.json paths.{STAGE_ARCHIVE_PATH_KEY} 声明" if declared
              else f"落点来源: 缺省(project.json 未声明 paths.{STAGE_ARCHIVE_PATH_KEY})")
    if not stage_dir.is_dir():
        return {
            "passed": False,
            "message": (
                f"Stage gate BLOCK: stage archive dir missing ({stage_dir}; {source}). "
                f"阶段快照=转换前提, 缺快照=未关账=不许转换 (AIPOS-R6M 大项A③). 出口: {next_step}"
            ),
            "stage_archive_dir": str(stage_dir),
            "snapshot_count": 0,
            "path_key": STAGE_ARCHIVE_PATH_KEY,
            "declared": declared,
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
                f"Stage gate BLOCK: no stage snapshot in {stage_dir} (empty or index-only; {source}). "
                f"阶段快照=转换前提, 缺快照=未关账=不许转换 (AIPOS-R6M 大项A③). 出口: {next_step}"
            ),
            "stage_archive_dir": str(stage_dir),
            "snapshot_count": 0,
            "path_key": STAGE_ARCHIVE_PATH_KEY,
            "declared": declared,
        }

    return {
        "passed": True,
        "message": f"Stage gate OK: {len(snapshots)} stage snapshot(s) in {stage_dir}",
        "stage_archive_dir": str(stage_dir),
        "snapshot_count": len(snapshots),
        "path_key": STAGE_ARCHIVE_PATH_KEY,
        "declared": declared,
    }


# ---------------------------------------------------------------------------
# AIPOS-C3C: N5 branch_integration 声明驱动 — 卡分支整合 (merge --no-ff)
# 声明是唯一真相: 分支命名/合并策略/信息格式/冲突策略全在 transitions.schema N5。
# finalize 读声明执行, 归属解析器读同一份声明; 生成什么格式就解析什么格式。
# ---------------------------------------------------------------------------

def _load_branch_integration() -> dict[str, Any]:
    """读 N5.branch_integration 声明 (单一真相; AIPOS-F92: 读 Lybra 自身 schema, 不读项目产品仓)。

    AIPOS-F108 件②(M18): 原「schema 缺失/损坏回退写死默认(_DEFAULT_BRANCH_INTEGRATION) + except Exception: pass」已退役——
    声明缺 = SchemaLoadError(fail-closed); 分支名 / 基线经 next_resolver.card_branch_name / card_base_branch 唯一派生。
    """
    from tools.schema_loader import get_branch_integration

    return get_branch_integration()


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


def _git_branch_merged_into_main(repo_root: Path, branch_name: str, main_branch: str) -> bool:
    """AIPOS-C3C/F11: 分支 tip 是否为基线分支祖先 (已合并进基线分支)。

    F11 前 HEAD 恒为 main (交回前切回 main 纪律), 查 HEAD 等价查 main; auto_checkout 落地后
    HEAD 可能停在卡分支, 必须显式查基线分支, 否则卡分支对自身恒"已合并"→ 误跳过整合。
    AIPOS-F108 件②: 基线分支由调用方按 N5.branch_integration.base_branch 声明传入(next_resolver.card_base_branch), 零写死。
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch_name, main_branch],
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
    main_branch: str | None = None,
) -> dict[str, Any] | None:
    """AIPOS-F11 大项A: auto_checkout 声明驱动 — 确保工作树在 main 分支。

    非 main 且树干净 + auto_checkout=true → 自行 checkout main 并出声;
    脏树 → 停下(halt+出声, 附脏文件 + next_step);
    auto_checkout=false → 停下喊人。已在 main → 放行 (脏树留给 merge/commit 步骤处理)。

    Returns:
        None = 已在 main(可继续); 否则返回拒绝体 {"blocked", "action", "message"}。
    """
    if main_branch is None:
        from tools.aipos_cli.next_resolver import card_base_branch

        main_branch = card_base_branch(branch_integration)  # AIPOS-F108 件②: 基线读声明
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
        f"— 需人工切回 {main_branch} 后再 finalize"
        + (f"\n  {next_step}" if next_step else "")
    )
    operations.append(f"  → BLOCKED: {message}")
    return {"blocked": True, "action": "blocked_auto_checkout_disabled", "message": message}


def _task_title_summary(governance_root: Path, task_id: str) -> str:
    """Best-effort: 从治理仓任务卡 frontmatter title 提炼摘要 (剥离 task_id 前缀)。

    查找顺序: 队列(task_loader.find_task_card: claimed/completed/pending)→ <paths.task_cards_root>/<ID>/CARD.md(项目声明)。
    失败返回空串 (摘要非归属关键, 归属由 branch 卡号 + verdict_id 裁决号保证)。
    """
    candidates: list[Path] = []
    # AIPOS-F78B 件①: 唯一查找 task_loader.find_task_card(frontmatter task_id 匹配, 文件名不限); 多义 = 不取摘要(归属由分支/裁决号保证)
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    try:
        card_path, _state = find_task_card(governance_root, task_id, states=("claimed", "completed", "pending"))
    except AmbiguousTaskCard as exc:
        print(f"Warning: {exc}", file=sys.stderr)
        card_path = None
    if card_path is not None:
        candidates.append(card_path)
    # AIPOS-F89 件① H9: 台账根读项目声明(project.json paths.task_cards_root, 唯一读取口 workspace_config.project_paths)
    from tools.aipos_cli.workspace_config import project_paths

    card_md = Path(project_paths(governance_root)["task_cards_root"]) / task_id / "CARD.md"
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
    from tools.aipos_cli.next_resolver import card_base_branch, card_branch_name

    if branch_integration is None:
        branch_integration = _load_branch_integration()  # AIPOS-F92: 声明读 Lybra 自身 schema, 不读项目产品仓

    branch_pattern = branch_integration.get("branch_pattern")
    merge_strategy = str(branch_integration.get("merge_strategy") or "no-ff")
    message_format = str(
        branch_integration.get("merge_message_format")
        or "Merge {branch}: {summary} ({verdict_id})"
    )
    # AIPOS-F108 件②(M18): 分支名 / 基线唯一派生(声明缺 = SchemaLoadError, 不回落写死)
    main_branch = card_base_branch(branch_integration)
    branch_name = card_branch_name(task_id, branch_integration)
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
        message = f"无卡分支 {branch_name} (直提 {main_branch} 的历史卡/无代码卡), 跳过整合"
        operations.append(f"  → {message}")
        return {**base, "action": "skipped_no_branch", "message": message}

    # 已合并进 main? (分支 tip 为 main 祖先)
    if _git_branch_merged_into_main(workspace_root, branch_name, main_branch):
        message = f"分支 {branch_name} 已合并 (tip 为 {main_branch} 祖先), 跳过整合"
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
        # AIPOS-F78 前置零⑦(F79 实撞: 并行两卡各在同一 JSON 声明文件加并列键 → 冲突中止): JSON 文件做键级三方合并,
        # 并列键自动并存, 同键异值才是真冲突; 全部 JSON 冲突解开且无其它冲突 → 完成 merge commit(分支 tip 不变, 裁决绑定仍成立)
        resolved, unresolved = _resolve_json_conflicts(workspace_root, branch_name, conflict_files, operations)
        if resolved and not unresolved:
            commit_result = subprocess.run(
                ["git", "commit", "--no-verify", "-m", merge_message],
                cwd=str(workspace_root), capture_output=True, text=True,
            )
            if commit_result.returncode == 0:
                operations.append(f"  → ✓ JSON 键级三方合并后完成 merge {branch_name} (no-ff): {', '.join(resolved)}")
                return {**base, "action": "merged", "message": f"已合并 {branch_name} (JSON 键级三方合并: {', '.join(resolved)})",
                        "json_key_merged": resolved}
            operations.append(f"  → 合并提交失败: {commit_result.stderr.strip()}")
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


def _json_three_way_merge(base: Any, ours: Any, theirs: Any) -> tuple[Any, list[str]]:
    """键级三方合并(递归 dict): 一侧未改取另一侧; 两侧同改同值取之; 两侧同改异值 = 冲突键。非 dict 视为整体值。"""
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        merged: dict[str, Any] = {}
        conflicts: list[str] = []
        for key in list(dict.fromkeys([*base.keys(), *ours.keys(), *theirs.keys()])):
            b, o, t = base.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING)
            if o == t:
                value = o
            elif o == b:
                value = t
            elif t == b:
                value = o
            else:
                value, sub = _json_three_way_merge(b, o, t) if isinstance(o, dict) and isinstance(t, dict) and isinstance(b, dict) else (_MISSING, [key])
                if sub:
                    conflicts.extend(f"{key}.{c}" if c != key else key for c in sub)
                    continue
            if value is not _MISSING:
                merged[key] = value
        return merged, conflicts
    if ours == theirs:
        return ours, []
    if ours == base:
        return theirs, []
    if theirs == base:
        return ours, []
    return _MISSING, ["<root>"]


_MISSING = object()


def _resolve_json_conflicts(
    workspace_root: Path, branch_name: str, conflict_files: list[str], operations: list[str]
) -> tuple[list[str], list[str]]:
    """AIPOS-F78 前置零⑦: 对冲突中的 .json 文件做键级三方合并(base=merge-base, ours=main, theirs=卡分支)。
    返回 (已解开文件, 未解开文件)。任何解析/键冲突 = 该文件未解开(调用方 abort, 绝不半合并)。"""
    import json as _json

    if not conflict_files:
        return [], []
    mb = subprocess.run(["git", "merge-base", "HEAD", branch_name], cwd=str(workspace_root), capture_output=True, text=True)
    base_rev = mb.stdout.strip() if mb.returncode == 0 else ""
    resolved: list[str] = []
    unresolved: list[str] = []
    for rel in conflict_files:
        if not rel.endswith(".json") or not base_rev:
            unresolved.append(rel)
            continue
        versions = {}
        for label, rev in (("base", base_rev), ("ours", "HEAD"), ("theirs", branch_name)):
            show = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=str(workspace_root), capture_output=True, text=True)
            if show.returncode != 0:
                versions[label] = {} if label == "base" else None
                continue
            try:
                versions[label] = _json.loads(show.stdout)
            except ValueError as exc:
                operations.append(f"  → {rel}@{label} 非合法 JSON, 不做键级合并: {exc}")
                versions[label] = None
        if versions.get("ours") is None or versions.get("theirs") is None:
            unresolved.append(rel)
            continue
        merged, conflicts = _json_three_way_merge(versions["base"], versions["ours"], versions["theirs"])
        if conflicts or merged is _MISSING:
            operations.append(f"  → {rel}: 同键异值真冲突 {conflicts}, 停(需人解)")
            unresolved.append(rel)
            continue
        indent = 2
        try:
            (workspace_root / rel).write_text(_json.dumps(merged, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as exc:
            operations.append(f"  → {rel}: 写合并结果失败: {exc}")
            unresolved.append(rel)
            continue
        add = subprocess.run(["git", "add", "--", rel], cwd=str(workspace_root), capture_output=True, text=True)
        if add.returncode != 0:
            operations.append(f"  → {rel}: git add 失败: {add.stderr.strip()}")
            unresolved.append(rel)
            continue
        operations.append(f"  → {rel}: JSON 键级三方合并成功(并列键并存)")
        resolved.append(rel)
    return resolved, unresolved


def _acquire_repo_merge_lock(
    workspace_root: Path,
    task_id: str,
    branch_integration: dict[str, Any],
    repo_lock: contextlib.ExitStack | None,
    operations: list[str],
) -> dict[str, Any] | None:
    """AIPOS-F135 件①: 取仓级合入锁挂到外壳 repo_lock(None = 取到); 拿不到 = {category, message}(调用方 BLOCK)。"""
    if repo_lock is None:  # 只经外壳 finalize_task 调用; 无外壳 = 无处持锁, 拒(fail-closed, 不无锁合并)
        message = "finalize 主体须经 finalize_task 调用(仓级合入锁由外壳持有); 出口: 调 finalize_task"
        operations.append(f"BLOCK — {message}")
        return {"category": "REPO_MERGE_LOCK_UNAVAILABLE", "message": message}
    try:
        lock_path = repo_lock.enter_context(_repo_merge_lock(workspace_root, task_id, branch_integration))
    except RepoMergeBusy as exc:
        decl = _repo_merge_lock_declaration(branch_integration)
        step = decl.get("next_step") or {}
        next_text = f"下一步({step.get('audience') or 'advisor'}): {step.get('action')}" if step.get("action") else ""
        message = f"BLOCKED: {exc}; 本卡 {task_id} 未合并、未改仓。{next_text}"
        operations.append(f"仓级合入锁: {message}")
        return {"category": str(decl["busy_category"]), "message": message, "retryable": True, "lock_holder": exc.holder}
    except RepoMergeLockError as exc:
        message = f"BLOCKED: 仓级合入锁无法建立, 不无锁合并: {exc}"
        operations.append(f"仓级合入锁: {message}")
        return {"category": "REPO_MERGE_LOCK_UNAVAILABLE", "message": message}
    operations.append(f"仓级合入锁: 已取得 {lock_path}(同仓其他 finalize 在本次返回前拿不到锁)")
    return None


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
    """Finalize a PASS task (主体见 _finalize_task_impl)。

    AIPOS-F120 件①: 外壳只做两件事——① 合并后中途失败(finalization 记录写失败 / 合并后试图加载未预载的产品模块)收成
    verdict=FAIL + 续跑出口(不再以 traceback 退出、不再告警后照报 PASS); ② 无论从哪个出口返回, 都拆掉合并后导入闸。
    AIPOS-F135 件①: 仓级合入锁由主体在合并前取得、挂在外壳的 repo_lock 上, 无论从哪个出口返回都在最后释放(持有 = 整个合并后段)。
    """
    operations: list[str] = []
    with contextlib.ExitStack() as repo_lock:
        try:
            return _finalize_task_impl(
                task_id, actor, workspace_root, governance_root=governance_root,
                dry_run=dry_run, push=push, deploy=deploy, operations=operations, repo_lock=repo_lock,
            )
        except (FinalizationRecordError, _PostMergeImportBlocked) as exc:
            head = _git_rev_parse_head(workspace_root)
            message = (
                f"finalize 合并后中途失败: {type(exc).__name__}: {exc}。合并/推送/部署可能已完成而 finalization 记录未落"
                f"(当前 HEAD {head[:8] if head else 'unknown'})。出口: 修复原因后重跑同一条 finalize——分支已合并且记录缺失时"
                "续跑识别本卡合并提交并补写记录(AIPOS-F120 件②), 不重复合并、不重复部署"
            )
            operations.append(f"✗ {message}")
            print(f"Error: {message}", file=sys.stderr)
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": True,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": head or None,
                "category": "FINALIZE_INTERRUPTED_AFTER_MERGE",
                "message": message,
                "operations": operations,
            }
        finally:
            _remove_post_merge_guard()


def _finalize_task_impl(
    task_id: str,
    actor: str,
    workspace_root: Path,
    *,
    governance_root: Path | None = None,
    dry_run: bool = False,
    push: bool = False,
    deploy: bool = False,
    operations: list[str],
    repo_lock: contextlib.ExitStack | None = None,
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
    # AIPOS-F120: operations 由外壳 finalize_task 传入(中途失败时外壳仍能带出已执行的步骤)

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

    # AIPOS-F78B 件⑤b: finalize 与 close/return/verdict 同口径——actor 须为该卡认领实例(卡面 claimed_by, validator.actor_matches_task_actor
    # 同一判据); 驱动方/他人当 actor 即拒(transitions record_authenticity.submission_identity.actor_rule)
    # AIPOS-F122 件③: 存量冻结卡拒 finalize(判定唯一实现 legacy_baseline.frozen_rejection; 清单读不出 = 拒, fail-closed)
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_rejection

    try:
        rejection = frozen_rejection(Path(governance_root), [task_id], action="finalize")
    except LegacyBaselineError as exc:
        rejection = {"code": exc.code, "message": f"{exc}; 存量冻结清单读不出, 拒 finalize", "unfreeze_command": None}
    if rejection is not None:
        operations.append(rejection["message"])
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
            "category": rejection["code"],
            "unfreeze_command": rejection["unfreeze_command"],
            "message": rejection["message"],
            "operations": operations,
        }

    claimer_check = _actor_is_claimer(governance_root, task_id, actor)
    if not claimer_check["ok"]:
        operations.append(f"ACTOR_MISMATCH: {claimer_check['reason']}")
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
            "category": "ACTOR_MISMATCH",
            "message": f"BLOCKED: {claimer_check['reason']}",
            "operations": operations,
        }

    # AIPOS-FND-14: display-only — surface the audit report frontmatter verdict (declared verdict_root, governance
    # root; AIPOS-F89 件① H9) alongside the real gate verdict for operator visibility. Never judged.
    report_display = _report_frontmatter_verdict_for_display(governance_root, task_id)
    if report_display["report_path"]:
        operations.append(
            f"(display only, not judged) audit report frontmatter verdict: "
            f"{report_display['report_verdict']!r} at {report_display['report_path']}"
        )

    # AIPOS-F70-fix2: 比对对象 = 待整合的卡分支顶端 (裁决绑的正是它), 非 main HEAD
    # ① 先获取 branch_integration 声明
    from tools.aipos_cli.next_resolver import card_base_branch, card_branch_name

    branch_integration = _load_branch_integration()  # AIPOS-F92: 声明读 Lybra 自身 schema, 不读项目产品仓
    # AIPOS-F108 件②(M18): 分支名 / 基线唯一派生(声明缺 = SchemaLoadError, 不回落写死)
    branch_name = card_branch_name(task_id, branch_integration)
    base_branch = card_base_branch(branch_integration)
    
    # ② 获取卡分支顶端 commit (如果分支存在且未合并)
    required_commit_sha: str | None = None
    current_head = _git_rev_parse_head(workspace_root)
    
    if _git_branch_exists(workspace_root, branch_name):
        # 获取分支 tip
        try:
            result = subprocess.run(
                ["git", "rev-parse", branch_name],
                cwd=str(workspace_root),
                check=True,
                capture_output=True,
                text=True,
            )
            branch_tip = result.stdout.strip()
        except subprocess.CalledProcessError:
            branch_tip = None
        
        if branch_tip and not _git_branch_merged_into_main(workspace_root, branch_name, base_branch):
            # 分支存在且未合并 → 裁决核对对象 = 卡分支 tip
            required_commit_sha = branch_tip
            operations.append(
                f"F70-fix2: 裁决核对对象 = 卡分支 {branch_name} tip ({required_commit_sha[:8]}), "
                f"main HEAD = {current_head[:8] if current_head else 'unknown'}"
            )
        else:
            # 分支已合并 → 不传 required_commit_sha (由 find_gate_pass_verdict_for_task 从裁决 artifact_subject 推导)
            # 已合并场景: 分支 tip 已进 main, 裁决绑的就是分支原tip, 不应要求匹配 merge commit
            required_commit_sha = None
            operations.append(
                f"F70-fix2: 分支 {branch_name} 已合并, 裁决核对从 artifact_subject 推导 "
                f"(当前 main HEAD = {current_head[:8] if current_head else 'unknown'})"
            )
    else:
        # 分支不存在 (直提 main 历史卡 / 非代码卡) → 用 main HEAD (向后兼容)
        required_commit_sha = current_head
        operations.append(
            f"F70-fix2: 无卡分支 {branch_name} (历史卡/非代码卡), 裁决核对对象 = main HEAD "
            f"({current_head[:8] if current_head else 'unknown'})"
        )

    # Check if task can be finalized (gate audit verdict = PASS + 精确 SHA 覆盖卡分支 tip)
    finalize_check = check_task_can_finalize(task_id, governance_root, commit_sha=required_commit_sha)
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
    # AIPOS-F92 件③: 阶段门判据读 Lybra 自身 schema(code_repo_schema_root, repo_root=None), 不读项目产品仓
    # (原 repo_root=workspace_root: 新项目产品仓无 schema/ → 「Stage gate config load failed」首次 finalize 必 BLOCK, 靶场实撞)
    stage_gate = check_stage_archive_gate(governance_root)
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

    # AIPOS-F135 件①: 同仓合入串行——部署完整性检查(读 HEAD/.deploy)与卡分支整合之前取仓级合入锁(非阻塞, 持有到 finalize 返回);
    # 同仓另一 finalize 在途 = BLOCK 可重试出口(不排队不抢)。dry-run 不改仓, 不取锁。
    if not dry_run:
        lock_block = _acquire_repo_merge_lock(workspace_root, task_id, branch_integration, repo_lock, operations)
        if lock_block is not None:
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "can_finalize": True,
                "integrity_check": None,
                "branch_check": None,
                "committed": False,
                "pushed": False,
                "deployed": False,
                "deployment_skipped": False,
                "deployment_error": None,
                "commit_hash": None,
                **lock_block,
                "operations": operations,
            }

    # Check deployment integrity (current==HEAD)
    integrity = _check_deployment_integrity(workspace_root, governance_root)  # AIPOS-F130 件①: 传治理根(dev_override 出口可达、区间校验生效)
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
    # (branch_integration 已在上方 F70-fix2 修复中加载, 此处不重复)
    ensure_main = _ensure_on_main_branch(workspace_root, branch_integration, operations, base_branch)
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
    
    branch_check = check_deployment_branch(workspace_root, required_branch=base_branch)  # AIPOS-F108 件②: 部署分支 = 基线声明
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
    except (OSError, ValueError) as exc:
        # AIPOS-F115 件③(F108R): 原 except Exception: pass 降级为「不知 task_mode」→ 代码任务缺分支的硬 BLOCK 被静默跳过(fail-open)。
        # 读卡失败 = 无法判定是否代码任务, 拒 finalize(拒因带出口)。
        reason = f"读卡失败, 无法判定 task_mode/output_target(代码任务缺分支须硬拒): {exc}; 出口: 修复卡文件后重跑 finalize"
        operations.append(f"Card read: BLOCK — {reason}")
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "can_finalize": False,
            "integrity_check": integrity,
            "branch_check": branch_check,
            "committed": False,
            "pushed": False,
            "deployed": False,
            "deployment_skipped": False,
            "deployment_error": None,
            "commit_hash": None,
            "message": reason,
            "operations": operations,
        }
    # AIPOS-F118 件①②(gap #77): 合并后回归策略先于合并读取——声明形坏 = 合并前就拒(fail-closed, 不留半成品合并)。
    from tools.aipos_cli import post_merge_regression as pmr
    from tools.schema_loader import SchemaLoadError

    try:
        regression_policy = pmr.resolve_policy(governance_root, workspace_root)
    except (ValueError, SchemaLoadError, OSError) as exc:
        reason = (f"合并后回归策略读取失败(config.schema test_contract.post_merge_regression): {exc}; "
                  "出口: 修正治理根 project.json test_contract 后重跑 finalize")
        operations.append(f"{pmr.MARKER} BLOCK — {reason}")
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
            "message": reason,
            "operations": operations,
        }
    # AIPOS-F120 件①: 合并会改写代码源(editable 安装 = 产品仓工作树), 合并前预载合并后要用的产品模块并装导入闸
    if not dry_run:
        from tools.schema_loader import SchemaLoadError

        try:
            _preload_post_merge(governance_root, task_id, operations)
        except (ImportError, SchemaLoadError, ValueError, OSError, SyntaxError) as exc:
            reason = f"合并前预载合并后所需产品模块失败, 未合并: {type(exc).__name__}: {exc}"
            operations.append(f"BLOCK — {reason}")
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
                "message": reason,
                "operations": operations,
            }
    # AIPOS-F135 件③: 部署适用性(含 project.json repos.no_deploy 声明)合并前预检——声明读不出/不合 = 合并前就拒(fail-closed, 不留半成品合并)
    from tools.aipos_cli.deploy_gate import deploy_mechanism_present

    try:
        deploy_mechanism_present(workspace_root, governance_root)
    except (ValueError, OSError, SchemaLoadError) as exc:
        reason = f"部署适用性判定失败(project.json repos / no_deploy 声明): {exc}; 出口: 修正治理根 project.json 后重跑 finalize"
        operations.append(f"BLOCK — {reason}")
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
            "message": reason,
            "operations": operations,
        }
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
    
    # AIPOS-F120 件②: 分支已合并而本卡 finalization 记录缺失(上次 finalize 合并/部署后中途失败)→ 续跑补记录;
    # 已有记录 = 维持原行为(下方 F61 守卫)
    if integrate.get("action") == "skipped_already_merged":
        from tools.aipos_cli.finalization_record import existing_finalization_records

        # AIPOS-F130 件④: 记录已有时也判推送/部署是否完成, 未完成 → 续做补完并追加记录; 均已完成 → None, 维持原行为
        resumed = _resume_finalization(
            task_id=task_id, actor=actor, workspace_root=workspace_root, governance_root=governance_root,
            dry_run=dry_run, push=push, deploy=deploy, finalize_check=finalize_check, branch_name=branch_name,
            base_branch=base_branch, integrity=integrity, branch_check=branch_check, operations=operations,
            existing_records=existing_finalization_records(governance_root, task_id),
        )
        if resumed is not None:
            return resumed

    # AIPOS-F61: 跟踪是否有实际合并动作作——clean-tree 路径只有在实际合并时才写 finalization 记录
    # 防止把上一张卡的 commit 误记为本卡的 finalization 证据(F58 假成功根因)
    _actual_merge_happened = integrate.get("action") == "merged"

    # AIPOS-F118 件①: 合并后回归——merge --no-ff 之后、push/deploy 之前, 在合并结果上跑项目测试清单并与合并前 main 比对。
    # 唯一实现 post_merge_regression.check_after_merge; 结果进 finalization 记录(regression)与一行输出(operations, loop 取此行)。
    if _actual_merge_happened and dry_run:
        operations.append(
            f"DRY-RUN: {pmr.MARKER} 合并后将在合并结果上跑 {regression_policy['runall_path'] or '(未声明 runall_path, 跳过)'} "
            f"(mode={regression_policy['mode']}, execution={regression_policy['execution']}, 来源 {regression_policy['policy_source']})"
        )
        regression = None
    elif _actual_merge_happened:
        merge_commit = _git_rev_parse_head(workspace_root)
        regression = pmr.check_after_merge(governance_root=governance_root, repo_root=workspace_root, task_id=task_id,
                                           actor=actor, merge_commit=merge_commit, policy=regression_policy)
        operations.append(regression["summary"])
        operations.extend(f"  ↳ 提示: {note}" for note in regression.get("notes") or [])
        if regression["action"] == "blocked":
            undone, undo_message = pmr.undo_merge(workspace_root, merge_commit, regression["pre_merge_commit"]) if regression.get("pre_merge_commit") else (False, "取不到合并前提交, 无法复位")
            operations.append(f"  ↳ 撤销合并: {undo_message}")
            regression["undo"] = undo_message
            try:
                event_path = pmr.write_event_record(governance_root, task_id, actor, regression)
                operations.append(f"  ↳ 回归事件记录: {event_path}")
            except (OSError, ValueError) as exc:
                operations.append(f"  ↳ ⚠️ 回归事件记录写入失败: {exc}")
            exit_hint = (
                f"出口: 在卡分支 {branch_name} 上 git merge {base_branch} 复现组合失败并修复 → 重新交回、重审后再 finalize; "
                "或 Owner 判定可接受时将项目 test_contract.post_merge_regression.mode 改为 warn 后重跑 finalize"
                if regression["status"] == "new_failures" else
                f"出口: 按日志 {regression.get('merged_log') or regression.get('baseline_log') or '(无)'} 核对检查为何未完成, "
                "调整项目 test_contract.post_merge_regression.timeout_seconds 或修好测试清单后重跑 finalize; "
                "或 Owner 判定可接受时将 mode 改为 warn 后重跑"
            )
            return {
                "verdict": Verdict.BLOCK if undone else Verdict.FAIL,
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
                "commit_hash": None if undone else merge_commit,
                "post_merge_regression": regression,
                "category": "POST_MERGE_REGRESSION",
                "message": (f"{regression['summary']}; {undo_message}; {exit_hint}" if undone else
                            f"{regression['summary']}; 撤销合并失败({undo_message}), main 停在未推送的合并提交 {merge_commit[:12]}, "
                            f"未推送未部署; 出口: 人工核对后 git reset --keep {str(regression.get('pre_merge_commit') or '<合并前提交>')[:12]} 再按上述出口处理"),
                "operations": operations,
            }
    else:
        regression = {"status": "skipped", "action": "none", "mode": regression_policy["mode"],
                      "reason": f"本次 finalize 无卡分支合并动作(整合 {integrate.get('action')})"}
        regression["summary"] = pmr.summary_line(regression)
        operations.append(regression["summary"])

    # AIPOS-F92 件③: 部署是否适用 = 产品仓有部署机制(deploy_gate.deploy_mechanism_present: 标准位置部署脚本或 .deploy/)。
    # 新项目普通产品仓两者皆无 → 部署不适用: 不强制部署、不判失败, finalization 记录 deploy_status=skipped(靶场实撞:
    # 原逻辑对任何仓都强制调 <产品仓>/tools/lybra-deploy → 「script not found」→ finalize FAIL)。
    # AIPOS-F135 件③: 判定唯一实现 deploy_gate.deploy_mechanism_present(含 project.json repos.no_deploy「本仓不部署」声明 →
    # deploy_status=not_applicable; 无机制 → skipped; 依据进 finalization 记录 deploy_status_reason)。合并前已预检, 此处读不出 = 合并已做,
    # 收成 FAIL 不写记录(出口: 修复后重跑 = 续跑补记录)。
    try:
        deploy_judgement = deploy_mechanism_present(workspace_root, governance_root)
    except (ValueError, OSError, SchemaLoadError) as exc:
        raise FinalizationRecordError(f"合并后部署适用性判定失败(project.json repos / no_deploy 声明): {exc}") from exc
    deploy_applicable = deploy_judgement["applicable"]
    skip_deploy_status = deploy_judgement["deploy_status"]  # 不适用时 = not_applicable | skipped(声明 N5.record.deploy_status)
    skip_deploy_reason = None if deploy_applicable else deploy_judgement["reason"]
    if not deploy_applicable:
        operations.append(f"部署不适用: {deploy_judgement['reason']}(deploy_status={skip_deploy_status})")
        deploy = False

    # AIPOS-F135 件②: 推送适用性唯一判据(无 origin / 无 origin/<基线> = push_status=not_applicable, 如实标注而非静默「已同步」)
    push_na_reason = _push_not_applicable_reason(workspace_root, base_branch)
    if push_na_reason is not None:
        operations.append(f"推送: push_status=not_applicable({push_na_reason})")

    # Check if there are changes to commit
    # AIPOS-R6A 靶子③: finalize push判据修正 — working tree clean ≠ already pushed
    # 需要检查 local vs origin 同步状态
    # AIPOS-R7A2 靶①(P0): clean-tree 早退必须检查 deploy 状态,禁静默跳过
    if _git_status_clean(workspace_root):
        synced = True if push_na_reason is not None else _git_local_origin_synced(workspace_root)
        clean_push_status = "not_applicable" if push_na_reason is not None else ("already_synced" if synced else "not_requested")
        if synced and push_na_reason is None:
            operations.append("推送: push_status=already_synced(本地基线与 origin 一致)")
        current_commit = _git_rev_parse_head(workspace_root)
        
        # AIPOS-R7A2 靶①: 检查当前 commit 是否已部署
        # 有 PASS 裁决 + 未部署 → 必须 deploy 或显式 FAIL
        deployed_info = _read_deploy_current(workspace_root)
        deployed_commit = deployed_info.get("current_commit")
        needs_deploy = deploy_applicable and (deployed_commit != current_commit)
        
        # Case 1: working tree clean + synced → 检查 deploy 状态
        if synced:
            if needs_deploy:
                # AIPOS-R7A2 靶①(P0): 未部署但有 PASS 裁决 → 必须 deploy,不可静默跳过
                # 进入 finalize 说明已有 PASS 裁决,commit 未部署是闭环缺口,必须补
                operations.append(f"⚠️  Current commit {current_commit[:8]} not deployed (deployed: {deployed_commit[:8] if deployed_commit else 'none'})")
                operations.append("⚠️  PASS 裁决下的 commit 必须部署,触发强制 deploy...")
                
                from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor, governance_root=governance_root)
                if deploy_result["success"]:
                    operations.append("✓ Deploy completed successfully")
                    verification = verify_deployment_version(workspace_root, current_commit)
                    if verification["verified"]:
                        # AIPOS-F61: 只有实际合并才写 finalization 记录(禁把上一张卡的 commit 当证据)
                        if _actual_merge_happened:
                            _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), True, operations, regression=regression, push_status=clean_push_status, push_status_reason=push_na_reason)
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
                            "push_status": clean_push_status,
                            "deployed": True,
                            "deployment_skipped": False,
                            "deployment_error": None,
                            "commit_hash": current_commit,
                            "message": f"No changes to commit, deployed {current_commit[:8]} to close gap",
                            "operations": operations,
                        }
                    else:
                        # Deploy 验证失败 → FAIL
                        # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                        if _actual_merge_happened:
                            _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status=clean_push_status, push_status_reason=push_na_reason)
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
                            "push_status": clean_push_status,
                            "deployed": False,
                            "deployment_skipped": False,
                            "deployment_error": verification["message"],
                            "commit_hash": current_commit,
                            "message": f"Deploy verification failed: {verification['message']}",
                            "operations": operations,
                        }
                else:
                    # Deploy 失败 → FAIL
                    # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                    if _actual_merge_happened:
                        _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status=clean_push_status, push_status_reason=push_na_reason)
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
                        "push_status": clean_push_status,
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
                    _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), deploy_applicable, operations, regression=regression, deploy_status=skip_deploy_status or "skipped", deploy_status_reason=skip_deploy_reason, push_status=clean_push_status, push_status_reason=push_na_reason)
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
                    "push_status": clean_push_status,
                    "deployed": False,
                    "deployment_skipped": True,
                    "deployment_error": None,
                    "commit_hash": current_commit,
                    "message": ("No changes to commit (working tree clean, synced, and deployed)" if deploy_applicable and push_na_reason is None
                                else f"No changes to commit (合并已完成); 推送: push_status={clean_push_status}"
                                     f"{'(' + push_na_reason + ')' if push_na_reason else ''}; "
                                     f"部署: deploy_status={skip_deploy_status or 'skipped'}{'(' + skip_deploy_reason + ')' if skip_deploy_reason else ''}"),
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
                "push_status": "not_requested",
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
            _git_push(workspace_root)  # AIPOS-F130 件④: 与续做同一实现
            operations.append("Push successful")
            
            # AIPOS-R7A2 靶①: push 成功后检查 deploy 需求
            if needs_deploy:
                operations.append(f"⚠️  Current commit {current_commit[:8]} not deployed (deployed: {deployed_commit[:8] if deployed_commit else 'none'})")
                operations.append("Triggering deploy after push...")
                
                from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor, governance_root=governance_root)
                if deploy_result["success"]:
                    operations.append("✓ Deploy completed successfully")
                    verification = verify_deployment_version(workspace_root, current_commit)
                    if verification["verified"]:
                        # AIPOS-F61: 只有实际合并才写 finalization 记录
                        if _actual_merge_happened:
                            _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), True, operations, regression=regression, push_status="pushed")
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
                            "push_status": "pushed",
                            "deployed": True,
                            "deployment_skipped": False,
                            "deployment_error": None,
                            "commit_hash": current_commit,
                            "message": "Pushed and deployed successfully",
                            "operations": operations,
                        }
                    else:
                        # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                        if _actual_merge_happened:
                            _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status="pushed")
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
                            "push_status": "pushed",
                            "deployed": False,
                            "deployment_skipped": False,
                            "deployment_error": verification["message"],
                            "commit_hash": current_commit,
                            "message": f"Pushed but deploy verification failed: {verification['message']}",
                            "operations": operations,
                        }
                else:
                    # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                    if _actual_merge_happened:
                        _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status="pushed")
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
                        "push_status": "pushed",
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
                    _ensure_finalization_record(governance_root, task_id, actor, current_commit, finalize_check.get("verdict_id"), deploy_applicable, operations, regression=regression, deploy_status=skip_deploy_status or "skipped", deploy_status_reason=skip_deploy_reason, push_status="pushed")
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
                    "push_status": "pushed",
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
        
        # AIPOS-F135 件②: 原此处内联 git push(第二路径, 失败只记 operations 照写记录)收归唯一实现 _git_push;
        # 适用性同一判据 _push_not_applicable_reason(无远端 = not_applicable, 不推); 推送失败 = FAIL 不写记录(N5 written_when)。
        pushed = False
        dirty_push_status = "not_applicable" if push_na_reason is not None else "not_requested"
        if push and push_na_reason is None:
            try:
                _git_push(workspace_root)
            except subprocess.CalledProcessError as e:
                message = (f"Push failed: {(e.stderr or '').strip()[:300]}; 已提交 {commit_hash[:8]} 未推送, finalization 记录不写"
                           "(N5: push 失败不写)。出口: 远端恢复/修复原因后带 --push 重跑同一条 finalize(续做推送并补写记录)")
                operations.append(f"✗ {message}")
                return {
                    "verdict": Verdict.FAIL,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": False,
                    "can_finalize": True,
                    "integrity_check": integrity,
                    "branch_check": branch_check,
                    "committed": True,
                    "pushed": False,
                    "push_status": None,
                    "deployed": False,
                    "deployment_skipped": False,
                    "deployment_error": None,
                    "commit_hash": commit_hash,
                    "category": "FINALIZE_PUSH_FAILED",
                    "message": message,
                    "operations": operations,
                }
            operations.append("Pushed to remote")
            pushed = True
            dirty_push_status = "pushed"
        push_done = pushed or dirty_push_status == "not_applicable"  # 推送完成或不适用 = 部署失败也落记录(F73D 前置一①)
        
        # AIPOS-R4B-2 / AIPOS-FINALIZE-FIX-1: Explicit deploy with lybra-deploy
        # deploy 失败 → finalize 整体 FAIL (exit 非0 + verdict FAIL),禁吞错报成功
        deployed = False
        deployment_skipped = False
        deployment_error = None
        
        if deploy:
            # 显式 deploy 模式：直接调用 lybra-deploy
            from tools.aipos_cli.deploy_gate import invoke_lybra_deploy, verify_deployment_version
            
            operations.append("ℹ️  Invoking lybra-deploy (explicit deploy mode)...")
            deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor, governance_root=governance_root)
            
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
                    # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                    if push_done:
                        _ensure_finalization_record(governance_root, task_id, actor, commit_hash, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status=dirty_push_status, push_status_reason=push_na_reason)
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
                        "push_status": dirty_push_status,
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
                # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                if push_done:
                    _ensure_finalization_record(governance_root, task_id, actor, commit_hash, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status=dirty_push_status, push_status_reason=push_na_reason)
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
                    "push_status": dirty_push_status,
                    "deployed": False,
                    "deployment_skipped": False,
                    "deployment_error": deployment_error,
                    "commit_hash": commit_hash,
                    "message": f"Deployment failed: {deployment_error[:200]}",
                    "operations": operations,
                }
        elif not deploy_applicable:
            # AIPOS-F135 件③: 部署不适用(声明本仓不部署 / 无部署机制)= 不做漂移自动部署
            operations.append(f"ℹ️  部署不适用, 不检查漂移自动部署(deploy_status={skip_deploy_status})")
            deployment_skipped = True
        else:
            # F-R4B2-3: FND-9 Auto-deploy gate-side changes (无论 push 与否都检查)
            from tools.aipos_cli.gate_drift import check_gate_drift
            from tools.aipos_cli.deploy_gate import invoke_lybra_deploy
            
            drift_check = check_gate_drift(workspace_root)
            operations.append(f"Drift check: {drift_check['message']}")
            
            if drift_check["has_drift"] and drift_check["classification"]["has_gate_side_changes"]:
                # Gate-side changes detected - auto-deploy
                operations.append("⚠️  Gate-side changes detected - triggering auto-deploy...")
                
                deploy_result = invoke_lybra_deploy(workspace_root, verdict_ref=finalize_check.get("verdict_id"), actor=actor, governance_root=governance_root)
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
                    # AIPOS-F73D 前置一①: push/merge 已成功, 部署失败也落 finalization 记录(deploy_status=deploy_failed)
                    if push_done:
                        _ensure_finalization_record(governance_root, task_id, actor, commit_hash, finalize_check.get("verdict_id"), False, operations, regression=regression, deploy_status="deploy_failed", push_status=dirty_push_status, push_status_reason=push_na_reason)
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
                        "push_status": dirty_push_status,
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
        final_message += f"; push_status={dirty_push_status}" + (f"({push_na_reason})" if push_na_reason else "")
        
        # AIPOS-C3B 大项B③: 写 finalization 记录(必落,统一用 helper)
        if not dry_run:
            _ensure_finalization_record(
                governance_root, task_id, actor, commit_hash, finalize_check.get("verdict_id"), deployed, operations,
                deploy_status="deployed" if deployed else ((skip_deploy_status or "skipped") if deployment_skipped else "not_attempted"),
                deploy_status_reason=skip_deploy_reason, regression=regression,
                push_status=dirty_push_status, push_status_reason=push_na_reason,
            )
        
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
            "push_status": dirty_push_status,
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
