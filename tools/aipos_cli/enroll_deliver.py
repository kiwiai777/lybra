#!/usr/bin/env python3
"""AIPOS-R6H 靶②: enroll-deliver 一体化 — Owner授权侧一条命令完成铸码+交换+凭据落位+工具分发

设计权威: AIPOS-R6H governance_refs 靶②

功能:
1. Owner签发enrollment code
2. 自动exchange换取token
3. 落.lybra/配置(connection.json + role文件统一JSON形状)
4. 调用distribute_tools分发工具/技能/契约
5. 校验workspace_root不是治理仓(拒绝ai-project-os路径)
6. 支持同机直写/跨机SSH分发

Usage:
    # 同机直写
    lybra enroll-deliver --role executor --instance exec.lybra.mac1 \\
        --target-workspace ~/my-agent-workstation \\
        --target-harness ~/my-agent-workstation/lybra-executor \\
        --gate-url http://host:7118 --owner-token <token>
    
    # 跨机SSH
    lybra enroll-deliver --role executor --instance exec.lybra.mac1 \\
        --target-workspace ~/my-agent-workstation \\
        --target-harness ~/my-agent-workstation/lybra-executor \\
        --ssh user@remote-host \\
        --gate-url http://host:7118 --owner-token <token>

Security:
  - enrollment code仅在本函数内存在,不落盘
  - token通过exchange自动获取,0600权限
  - owner_token从connection.json读取或env提供,永不回显
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Import enrollment and distribution modules
try:
    from tools.aipos_cli.enrollment import create_enrollment_code
    from tools.aipos_cli.enroll_client import enroll, exchange_enrollment_code
    from tools.distribute_tools import distribute_to_harness
    from tools.aipos_cli.confirm_client import load_owner_token
except ImportError as e:
    print(f"Error: Failed to import required modules: {e}", file=sys.stderr)
    sys.exit(1)


def validate_workspace_root(workspace_root: str) -> None:
    """校验workspace_root不是治理仓(AIPOS-R6H靶②)"""
    if "ai-project-os" in workspace_root:
        raise ValueError(
            f"workspace_root cannot be governance repo (ai-project-os): {workspace_root}. "
            "Use product repo or agent workstation path."
        )


def write_role_file_json(lybra_dir: Path, role: str, instance: str | None, owner_policy_ref: str | None) -> None:
    """写入.lybra/role文件(统一JSON形状,AIPOS-R6H靶②)
    
    Args:
        lybra_dir: .lybra目录路径
        role: 角色名
        instance: agent_instance(可选)
        owner_policy_ref: owner策略引用(可选)
    """
    role_file = lybra_dir / "role"
    role_data = {
        "role": role,
    }
    if instance:
        role_data["instance"] = instance
    if owner_policy_ref:
        role_data["owner_policy_ref"] = owner_policy_ref
    
    role_file.write_text(json.dumps(role_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    role_file.chmod(0o644)


def enroll_deliver_local(
    *,
    role: str,
    instance: str | None,
    workspace_root: Path,
    harness_root: Path,
    gate_url: str,
    owner_policy_ref: str,
    owner_token: str,
    ttl_seconds: int = 3600,
    force: bool = False,
) -> dict[str, Any]:
    """同机直写: 铸码+兑换+落位+分发
    
    Args:
        role: 角色名(executor/auditor/advisor)
        instance: agent_instance(e.g., exec.lybra.mac1)
        workspace_root: 目标workspace根目录(不能是治理仓)
        harness_root: 目标harness根目录(e.g., ~/kiwiai-pi/lybra-executor)
        gate_url: Gate MCP URL
        owner_policy_ref: Owner策略引用
        owner_token: Owner token(用于签发enrollment code)
        ttl_seconds: enrollment code有效期(秒)
        force: 强制覆盖已存在的文件
    
    Returns:
        操作结果字典
    """
    # 1. 校验workspace_root
    validate_workspace_root(str(workspace_root))
    
    # 2. Owner签发enrollment code
    from tools.aipos_cli.enrollment import create_enrollment_code
    
    # 注意: create_enrollment_code需要workspace_root指向gate所在的治理仓
    # 这里需要推断gate的workspace(通常是gate服务所在的workspace)
    # 简化实现: 假设gate workspace从env LYBRA_WORKSPACE_ROOT读取
    gate_workspace = os.environ.get("LYBRA_WORKSPACE_ROOT", "")
    if not gate_workspace:
        raise ValueError("LYBRA_WORKSPACE_ROOT env var required for gate workspace")
    
    enrollment_result = create_enrollment_code(
        workspace_root=gate_workspace,
        role=role,
        instance=instance,
        ttl_seconds=ttl_seconds,
        by=f"owner (enroll-deliver)",
        reason=f"enroll-deliver for {instance or role}",
    )
    
    code = enrollment_result["code"]
    
    try:
        # 3. 兑换enrollment code获取token
        exchange_result = exchange_enrollment_code(gate_url, code, bootstrap_token=owner_token)
        
        if not exchange_result.get("ok"):
            raise RuntimeError(f"Exchange failed: {exchange_result.get('message', 'unknown')}")
        
        token_entry = exchange_result.get("token_entry")
        if not token_entry:
            raise RuntimeError("No token_entry in exchange response")
        
        # 4. 落.lybra/配置(统一JSON格式role文件)
        workspace_root.mkdir(parents=True, exist_ok=True)
        lybra_dir = workspace_root / ".lybra"
        lybra_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        
        # 4a. connection.json
        connection_file = lybra_dir / "connection.json"
        if connection_file.exists():
            conn_data = json.loads(connection_file.read_text(encoding="utf-8"))
        else:
            conn_data = {
                "config_version": 1,
                "mcp": {
                    "rpc_url": gate_url if gate_url.endswith("/mcp") else f"{gate_url}/mcp",
                },
                "tokens": [],
            }
        
        # Upsert token entry
        tokens = conn_data.get("tokens", [])
        matched_idx = None
        for idx, existing in enumerate(tokens):
            if instance and existing.get("agent_instance") == instance:
                matched_idx = idx
                break
            if not instance and not existing.get("agent_instance") and existing.get("role") == role:
                matched_idx = idx
                break
        
        if matched_idx is not None:
            tokens[matched_idx] = token_entry
        else:
            tokens.append(token_entry)
        
        conn_data["tokens"] = tokens
        
        # 写入connection.json (0600)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(connection_file, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(conn_data, fh, indent=2, sort_keys=True)
                fh.write("\n")
        finally:
            os.chmod(connection_file, 0o600)
        
        # 4b. role文件(统一JSON形状)
        write_role_file_json(lybra_dir, role, instance, owner_policy_ref)
        
        # 5. 分发工具/技能/契约
        distribute_result = distribute_to_harness(harness_root, role, force=force)
        
        return {
            "ok": True,
            "operation": "enroll-deliver",
            "mode": "local",
            "role": role,
            "instance": instance,
            "workspace_root": str(workspace_root),
            "harness_root": str(harness_root),
            "enrollment": {
                "code_id": enrollment_result["code_id"],
                "fingerprint": enrollment_result["fingerprint"],
            },
            "token": {
                "fingerprint": token_entry.get("fingerprint"),
                "scopes": token_entry.get("scopes", []),
            },
            "distribution": distribute_result,
        }
    
    except Exception as e:
        # 如果失败,尝试撤销enrollment code
        from tools.aipos_cli.enrollment import revoke_enrollment_code
        try:
            revoke_enrollment_code(
                gate_workspace,
                enrollment_result["code_id"],
                by="owner (enroll-deliver rollback)",
                reason=f"enroll-deliver failed: {e}",
            )
        except:
            pass
        raise


def enroll_deliver_ssh(
    *,
    role: str,
    instance: str | None,
    workspace_root: str,
    harness_root: str,
    ssh_target: str,
    gate_url: str,
    owner_policy_ref: str,
    owner_token: str,
    ttl_seconds: int = 3600,
    force: bool = False,
) -> dict[str, Any]:
    """跨机SSH: 铸码+传输+远程执行
    
    Args:
        role: 角色名
        instance: agent_instance
        workspace_root: 远程目标workspace根目录(字符串,不展开~)
        harness_root: 远程目标harness根目录
        ssh_target: SSH目标(user@host)
        gate_url: Gate MCP URL
        owner_policy_ref: Owner策略引用
        owner_token: Owner token
        ttl_seconds: enrollment code有效期
        force: 强制覆盖
    
    Returns:
        操作结果字典
    """
    # 1. Owner签发enrollment code
    gate_workspace = os.environ.get("LYBRA_WORKSPACE_ROOT", "")
    if not gate_workspace:
        raise ValueError("LYBRA_WORKSPACE_ROOT env var required for gate workspace")
    
    enrollment_result = create_enrollment_code(
        workspace_root=gate_workspace,
        role=role,
        instance=instance,
        ttl_seconds=ttl_seconds,
        by=f"owner (enroll-deliver SSH to {ssh_target})",
        reason=f"enroll-deliver SSH for {instance or role}",
    )
    
    code = enrollment_result["code"]
    
    try:
        # 2. 构造远程命令
        # 远程机需要有enroll_client.py可用
        # 简化: 假设远程机有lybra工具链
        enroll_cmd = [
            "python3", "-m", "tools.aipos_cli.enroll_client",
            "--code", code,
            "--gate-url", gate_url,
            "--workspace", workspace_root,
            "--policy", owner_policy_ref,
            "--bootstrap-token", owner_token,
            "--json",
        ]
        
        # SSH执行enroll
        ssh_enroll = ["ssh", ssh_target, " ".join(enroll_cmd)]
        result = subprocess.run(ssh_enroll, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            raise RuntimeError(f"Remote enroll failed: {result.stderr}")
        
        enroll_result = json.loads(result.stdout)
        if not enroll_result.get("ok"):
            raise RuntimeError(f"Remote enroll returned ok=False: {enroll_result}")
        
        # 3. 远程分发工具
        # 需要将distribute_tools.py传输到远程或假设远程已有
        # 简化: 假设远程有lybra工具链
        distribute_cmd = [
            "python3", "-m", "tools.distribute_tools",
            harness_root,
            role,
            "--json",
        ]
        if force:
            distribute_cmd.append("--force")
        
        ssh_distribute = ["ssh", ssh_target, " ".join(distribute_cmd)]
        result = subprocess.run(ssh_distribute, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            raise RuntimeError(f"Remote distribute failed: {result.stderr}")
        
        distribute_result = json.loads(result.stdout)
        
        return {
            "ok": True,
            "operation": "enroll-deliver",
            "mode": "ssh",
            "ssh_target": ssh_target,
            "role": role,
            "instance": instance,
            "workspace_root": workspace_root,
            "harness_root": harness_root,
            "enrollment": {
                "code_id": enrollment_result["code_id"],
                "fingerprint": enrollment_result["fingerprint"],
            },
            "token": {
                "fingerprint": enroll_result.get("fingerprint"),
                "scopes": enroll_result.get("scopes", []),
            },
            "distribution": distribute_result,
        }
    
    except Exception as e:
        # 失败时撤销enrollment code
        from tools.aipos_cli.enrollment import revoke_enrollment_code
        try:
            revoke_enrollment_code(
                gate_workspace,
                enrollment_result["code_id"],
                by=f"owner (enroll-deliver SSH rollback)",
                reason=f"enroll-deliver SSH failed: {e}",
            )
        except:
            pass
        raise


def main() -> int:
    """CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="AIPOS-R6H: enroll-deliver — 一条命令完成铸码+落位+分发",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--role", required=True, help="Role name (executor/auditor/advisor)")
    parser.add_argument("--instance", help="Agent instance (e.g., exec.lybra.mac1)")
    parser.add_argument("--target-workspace", required=True, help="Target workspace root (cannot be ai-project-os)")
    parser.add_argument("--target-harness", required=True, help="Target harness root (e.g., ~/kiwiai-pi/lybra-executor)")
    parser.add_argument("--gate-url", required=True, help="Gate MCP URL")
    parser.add_argument("--owner-policy-ref", required=True, help="Owner policy reference")
    parser.add_argument("--owner-token", help="Owner token (or use --connection-json)")
    parser.add_argument("--connection-json", help="Path to connection.json (to read owner token)")
    parser.add_argument("--ssh", help="SSH target for remote delivery (user@host)")
    parser.add_argument("--ttl", type=int, default=3600, help="Enrollment code TTL in seconds (default: 3600)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing files")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    
    args = parser.parse_args()
    
    # Load owner token
    if args.owner_token:
        owner_token = args.owner_token
    elif args.connection_json:
        try:
            owner_token = load_owner_token(connection_json=args.connection_json, role="owner")
        except Exception as e:
            print(f"Error loading owner token: {e}", file=sys.stderr)
            return 1
    else:
        print("Error: provide --owner-token or --connection-json", file=sys.stderr)
        return 1
    
    try:
        if args.ssh:
            # 跨机SSH
            result = enroll_deliver_ssh(
                role=args.role,
                instance=args.instance,
                workspace_root=args.target_workspace,
                harness_root=args.target_harness,
                ssh_target=args.ssh,
                gate_url=args.gate_url,
                owner_policy_ref=args.owner_policy_ref,
                owner_token=owner_token,
                ttl_seconds=args.ttl,
                force=args.force,
            )
        else:
            # 同机直写
            result = enroll_deliver_local(
                role=args.role,
                instance=args.instance,
                workspace_root=Path(args.target_workspace).expanduser().resolve(),
                harness_root=Path(args.target_harness).expanduser().resolve(),
                gate_url=args.gate_url,
                owner_policy_ref=args.owner_policy_ref,
                owner_token=owner_token,
                ttl_seconds=args.ttl,
                force=args.force,
            )
        
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"\n✓ Enroll-deliver successful!")
            print(f"  Mode: {result['mode']}")
            print(f"  Role: {result['role']}")
            if result.get('instance'):
                print(f"  Instance: {result['instance']}")
            print(f"  Workspace: {result['workspace_root']}")
            print(f"  Harness: {result['harness_root']}")
            print(f"\n  Enrollment:")
            print(f"    Code ID: {result['enrollment']['code_id']}")
            print(f"    Fingerprint: {result['enrollment']['fingerprint']}")
            print(f"\n  Token:")
            print(f"    Fingerprint: {result['token']['fingerprint']}")
            print(f"    Scopes: {', '.join(result['token']['scopes'])}")
            
            dist = result.get('distribution', {})
            if dist.get('distributed'):
                print(f"\n  ✓ Distributed ({len(dist['distributed'])}):")
                for item in dist['distributed']:
                    print(f"    - {item}")
        
        return 0
    
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2), file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
