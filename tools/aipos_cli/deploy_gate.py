"""AIPOS-R4B-2: Deploy gate helpers — 一条命令真 push+deploy+部署分支强制.

AIPOS-R6C: Renamed from finalize_enhanced.py to deploy_gate.py for neutral naming.
AIPOS-FINALIZE-FIX-1 (2026-08-12): lybra-deploy 路径从产品仓根解析,禁 cwd 猜测。

finalize 收口：读 gate 真裁决(PASS前提) → 产品仓commit校验 → push → lybra-deploy
→ VERSION对齐断言，全程一条命令。含部署分支强制：部署commit必须在main，否则拒。

设计权威: DESIGN v2 §2 N5 (finalize自助, 收编FND-18) + §6 (部署分支强制②)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def check_deployment_branch(repo_root: Path, *, required_branch: str = "main") -> dict[str, Any]:
    """AIPOS-R4B-2 部署分支强制：检查当前 commit 是否在部署分支上。
    
    DESIGN v2 §6 分支集成卫生②：只从单一部署分支部署（lybra-deploy
    校验部署commit在部署分支上，否则拒）。这是 agency F7 险情同款防护的代码化。
    
    Args:
        repo_root: 产品仓根路径
        required_branch: 要求的部署分支名（默认 "main"）
    
    Returns:
        {
            "on_required_branch": bool,
            "current_branch": str | None,
            "current_commit": str,
            "message": str,
            "detached_head": bool,
        }
    """
    try:
        # Get current commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        current_commit = result.stdout.strip()
        
        # Get current branch (may fail if detached HEAD)
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        
        if result.returncode != 0:
            return {
                "on_required_branch": False,
                "current_branch": None,
                "current_commit": current_commit,
                "detached_head": True,
                "message": f"Detached HEAD at {current_commit[:8]} — deployment requires {required_branch} branch",
            }
        
        current_branch = result.stdout.strip()
        
        # Check if in detached HEAD state
        if current_branch == "HEAD":
            return {
                "on_required_branch": False,
                "current_branch": None,
                "current_commit": current_commit,
                "detached_head": True,
                "message": f"Detached HEAD at {current_commit[:8]} — deployment requires {required_branch} branch",
            }
        
        # Check if on required branch
        if current_branch != required_branch:
            return {
                "on_required_branch": False,
                "current_branch": current_branch,
                "current_commit": current_commit,
                "detached_head": False,
                "message": f"Current branch '{current_branch}' is not '{required_branch}' — deployment only allowed from {required_branch}",
            }
        
        # Success: on required branch
        return {
            "on_required_branch": True,
            "current_branch": current_branch,
            "current_commit": current_commit,
            "detached_head": False,
            "message": f"On deployment branch '{required_branch}' at {current_commit[:8]}",
        }
    
    except subprocess.CalledProcessError as exc:
        return {
            "on_required_branch": False,
            "current_branch": None,
            "current_commit": None,
            "detached_head": False,
            "message": f"Git command failed: {exc}",
        }
    except subprocess.TimeoutExpired:
        return {
            "on_required_branch": False,
            "current_branch": None,
            "current_commit": None,
            "detached_head": False,
            "message": "Git command timed out",
        }


def invoke_lybra_deploy(
    repo_root: Path,
    *,
    verdict_ref: str | None = None,
    dev_override: bool = False,
    reason: str | None = None,
    actor: str | None = None,
    governance_root: Path | None = None,
) -> dict[str, Any]:
    """调用 lybra-deploy 脚本执行部署。
    
    AIPOS-C3 大项A: 新增 verdict_ref 校验 — 裁决必须真实存在(门生)且覆盖待部署 commit。
    AIPOS-FINALIZE-FIX-1: 脚本路径从产品仓根解析 (repo_root / "tools" / "lybra-deploy"),
    禁止依赖 cwd 猜测。config.schema 定义此路径为标准位置。

    AIPOS-R6S 大项B②: deploy 授权判据 — 仅 verdict_ref(finalize 传本卡 PASS 裁决 id)
    或 dev_override(须显式 --reason), 缺授权即拒(由 lybra-deploy 脚本执行)。
    
    Args:
        repo_root: 产品仓根路径 (必须是产品仓,不是治理仓)
        verdict_ref: PASS 裁决 id(audited 授权)
        dev_override: 未审 dev 部署
        reason: dev_override 的显式理由
        actor: 执行部署的 actor
        governance_root: 治理工作区根(用于 verdict_ref 校验),None 则从 connection.json 解析
    
    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "returncode": int,
        }
    """
    # AIPOS-FINALIZE-FIX-1: 从产品仓根解析脚本路径,禁 cwd 猜
    deploy_script = repo_root / "tools" / "lybra-deploy"
    
    if not deploy_script.exists():
        return {
            "success": False,
            "stdout": "",
            "stderr": f"lybra-deploy script not found at expected location: {deploy_script} (resolved from repo_root={repo_root})",
            "returncode": 1,
        }

    # AIPOS-C3 大项A③: verdict_ref 授权校验(门生真实性 + commit 覆盖)
    if verdict_ref:
        # 解析 governance_root
        if governance_root is None:
            from tools.aipos_cli.workspace_config import resolve_workspace_root
            try:
                governance_root = resolve_workspace_root()
            except FileNotFoundError as e:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Cannot resolve governance_root for verdict_ref validation: {e}",
                    "returncode": 2,
                }
        
        # 获取待部署的 commit 列表(current..HEAD)
        try:
            current_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
                text=True,
            )
            head_commit = current_result.stdout.strip()
            
            # 读取 .deploy/current/VERSION 的 git_commit
            version_file = repo_root / ".deploy" / "current" / "VERSION"
            current_commit = None
            if version_file.exists():
                for line in version_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("git_commit:"):
                        current_commit = line.split(":", 1)[1].strip()
                        break
            
            commits_to_deploy: list[str] = []
            if current_commit and current_commit != head_commit:
                # 有部署漂移,获取 current..HEAD 的 commit 列表
                commits_result = subprocess.run(
                    ["git", "log", "--format=%H", f"{current_commit}..{head_commit}"],
                    cwd=str(repo_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                commits_to_deploy = [c.strip() for c in commits_result.stdout.strip().splitlines() if c.strip()]
            else:
                # 首次部署或 current==HEAD,待部署的只有 HEAD
                commits_to_deploy = [head_commit]
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Failed to determine commits to deploy: {e}",
                "returncode": 2,
            }
        
        # 校验 verdict_ref 覆盖所有待部署 commit
        from tools.aipos_cli.deployment_authorization import check_verdict_ref_authorization
        
        auth_check = check_verdict_ref_authorization(
            verdict_ref=verdict_ref,
            governance_root=governance_root,
            commits_to_deploy=commits_to_deploy,
            repo_root=repo_root,
        )
        
        if not auth_check["authorized"]:
            uncovered_detail = ""
            if auth_check["uncovered_commits"]:
                uncovered_detail = "\n未覆盖的 commit:\n  " + "\n  ".join(auth_check["uncovered_commits"][:5])
                if len(auth_check["uncovered_commits"]) > 5:
                    uncovered_detail += f"\n  ... 及 {len(auth_check['uncovered_commits']) - 5} 个更多"
            return {
                "success": False,
                "stdout": "",
                "stderr": f"verdict_ref 授权校验失败: {auth_check['message']}{uncovered_detail}",
                "returncode": 2,
            }

    # AIPOS-R6S 大项B②: 组装授权参数(缺授权即拒由脚本执行)
    from tools.aipos_cli.deployment_record import resolve_authorization
    auth_type, auth_ref = resolve_authorization(
        verdict_ref=verdict_ref, dev_override=dev_override, reason=reason
    )
    if auth_type is None:
        return {
            "success": False,
            "stdout": "",
            "stderr": "deploy requires authorization: verdict_ref or dev_override(--reason) — 缺授权即拒 (AIPOS-R6S 大项B②)",
            "returncode": 2,
        }

    argv = [str(deploy_script), "deploy"]
    if auth_type == "verdict_ref":
        argv += ["--verdict-ref", auth_ref]
    else:
        argv += ["--dev-override", "--reason", auth_ref]
    if actor:
        argv += ["--actor", actor]
    if governance_root:
        argv += ["--governance-root", str(governance_root)]
    
    try:
        # AIPOS-FINALIZE-FIX-1: cwd 设为产品仓根,确保脚本在正确上下文执行
        result = subprocess.run(
            argv,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,  # 2分钟超时
        )
        
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "lybra-deploy timed out after 120s",
            "returncode": -1,
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Failed to invoke lybra-deploy: {exc}",
            "returncode": -1,
        }


def verify_deployment_version(repo_root: Path, expected_commit: str) -> dict[str, Any]:
    """验证 .deploy/current/VERSION 是否指向预期的 commit。
    
    AIPOS-369 断言：部署后 current symlink 必须指向 HEAD commit。
    
    Args:
        repo_root: 产品仓根路径
        expected_commit: 预期的 git commit hash (full)
    
    Returns:
        {
            "verified": bool,
            "current_commit": str | None,
            "expected_commit": str,
            "message": str,
        }
    """
    version_file = repo_root / ".deploy" / "current" / "VERSION"
    
    if not version_file.exists():
        return {
            "verified": False,
            "current_commit": None,
            "expected_commit": expected_commit,
            "message": ".deploy/current/VERSION does not exist",
        }
    
    try:
        version_content = version_file.read_text(encoding="utf-8")
        current_commit = None
        
        for line in version_content.splitlines():
            if line.startswith("git_commit:"):
                current_commit = line.split(":", 1)[1].strip()
                break
        
        if not current_commit:
            return {
                "verified": False,
                "current_commit": None,
                "expected_commit": expected_commit,
                "message": "git_commit not found in VERSION file",
            }
        
        if current_commit == expected_commit:
            return {
                "verified": True,
                "current_commit": current_commit,
                "expected_commit": expected_commit,
                "message": f"Deployment verified: current == HEAD ({current_commit[:8]})",
            }
        else:
            return {
                "verified": False,
                "current_commit": current_commit,
                "expected_commit": expected_commit,
                "message": f"Deployment mismatch: current={current_commit[:8]}, expected={expected_commit[:8]}",
            }
    
    except Exception as exc:
        return {
            "verified": False,
            "current_commit": None,
            "expected_commit": expected_commit,
            "message": f"Error reading VERSION: {exc}",
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
