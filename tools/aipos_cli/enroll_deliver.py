#!/usr/bin/env python3
"""AIPOS-R6H 靶②: enroll-deliver 一体化 — Owner授权侧一条命令完成铸码+交换+凭据落位+工具分发

设计权威: AIPOS-R6H governance_refs 靶②

功能:
1. Owner签发enrollment code
2. 自动exchange换取token
3. 落.lybra/配置(connection.json + role文件统一JSON形状)
4. 调用distribute_tools分发工具/技能/契约
5. 校验workspace_root不是治理仓(AIPOS-F88: 结构识别, 不看路径名)
6. 支持同机直写/跨机SSH分发

Usage:
    # 同机直写
    lybra enroll-deliver --role executor --instance exec.lybra.mac1 \\
        --target-workspace ~/my-agent-workstation \\
        --target-harness ~/my-agent-workstation/lybra-executor \\
        --gate-url http://<host>:<port> --connection-json <owner connection.json>
    
    # 跨机SSH
    lybra enroll-deliver --role executor --instance exec.lybra.mac1 \\
        --target-workspace ~/my-agent-workstation \\
        --target-harness ~/my-agent-workstation/lybra-executor \\
        --ssh user@remote-host \\
        --gate-url http://<host>:<port> --connection-json <owner connection.json>

Security:
  - enrollment code仅在本函数内存在,不落盘
  - token通过exchange自动获取,0600权限
  - owner_token从connection.json读取或经 --owner-token-stdin 自 stdin 读入, 永不进 argv、永不回显(AIPOS-F113)
  - --ssh: 远端 enroll 不需要 Owner 凭据(自包含码即运输认证); 注册码经 ssh stdin 送达, 远端命令逐参数 shlex.quote
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# Import enrollment and distribution modules
try:
    from tools.aipos_cli.enrollment import create_enrollment_code
    from tools.aipos_cli.enroll_client import enroll, exchange_enrollment_code
    from tools.distribute_tools import distribute_to_harness
    from tools.aipos_cli.confirm_client import GateClient, gate_base_url, gate_rpc_url, load_owner_token
    from tools.sandbox_runtime.confined_worker import redact_transcript
except ImportError as e:
    print(f"Error: Failed to import required modules: {e}", file=sys.stderr)
    sys.exit(1)


# AIPOS-F113: 同机判定 / loopback 规范化复用 enroll_client 唯一实现(原本文件内逐字副本退役; 本模块仍导出同名供既有调用方)
from tools.aipos_cli.enroll_client import is_same_host, normalize_gate_url_for_same_host  # noqa: E402,F401


def validate_workspace_root(workspace_root: str, role: str) -> None:
    """校验workspace_root按角色类判定(AIPOS-F22 F23⑧)
    
    策略(按角色类,从roles.schema.json单源):
      - 工位角色类(executor/auditor及其custom roles) → 必须工位目录,拒绝治理仓
      - 顾问角色类(planner/advisor及其custom roles) → 允许治理仓,也允许工位
      - 其他角色类(owner/copilot等) → 保持既有判据(拒绝治理仓)
    
    Args:
        workspace_root: workspace路径
        role: 角色名
    
    Raises:
        ValueError: 工位角色类在治理仓时拒绝; 角色类不可解析(custom_roles.UnknownRoleClass, ValueError 子类)
    """
    # AIPOS-F102 件②: 角色类解析唯一实现 custom_roles.resolve_role_to_class(解析不到 = 拒, 原「异常回落角色名」退役),
    # 分组读 roles.schema class_groups(原写死的顾问类分组元组退役)
    from tools.aipos_cli.custom_roles import resolve_role_to_class, role_classes_in_group

    role_class = resolve_role_to_class(role, workspace_root, required=True)
    # AIPOS-F88 件②: 治理仓识别 = 唯一结构判据(enroll_client.is_governance_workspace → workspace_config.has_workspace_queue),
    # 不看路径名(原路径子串判定退役: 换机器目录名不同即失效, 且把路径里恰含治理目录名的产品仓误判为治理仓)
    from tools.aipos_cli.enroll_client import is_governance_workspace

    is_governance = is_governance_workspace(Path(workspace_root).expanduser())
    
    # 治理席位类(顾问/规划方, roles.schema class_groups.governance_seat):允许治理仓,也允许工位(任何路径都通过)
    if role_class in role_classes_in_group("governance_seat"):
        return
    
    # 工位角色类(executor/auditor)+其他角色:拒绝治理仓
    if is_governance:
        raise ValueError(
            f"workspace_root cannot be governance repo (结构识别: 含声明的队列根) for role class '{role_class}': {workspace_root}. "
            "Use product repo or agent workstation path. "
            "(Only planner/advisor roles may use governance workspace.)"
        )


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
    validate_workspace_root(str(workspace_root), role)
    
    # 2. Owner签发enrollment code(模块顶部已导入 create_enrollment_code)
    # 注意: create_enrollment_code需要workspace_root指向gate所在的治理仓
    # 这里需要推断gate的workspace(通常是gate服务所在的workspace)
    # 简化实现: 假设gate workspace从env LYBRA_WORKSPACE_ROOT读取
    from tools.aipos_cli.workspace_config import workspace_root_from_env  # AIPOS-F106 件③: 工作区根 env 唯一读取口

    gate_workspace = workspace_root_from_env()[0] or ""
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
        from tools.aipos_cli.enroll_client import ensure_lybra_dir  # AIPOS-F106 件④: .lybra 建目录唯一实现

        lybra_dir = ensure_lybra_dir(workspace_root)
        
        # 4a. connection.json (AIPOS-R6K件①: 同机写 loopback URL)
        # AIPOS-F27 大项D: 铸全 connection.json — workspace_root/mcp.rpc_url/tokens 三全
        normalized_gate_url = normalize_gate_url_for_same_host(gate_url)
        connection_file = lybra_dir / "connection.json"
        if connection_file.exists():
            conn_data = json.loads(connection_file.read_text(encoding="utf-8"))
        else:
            conn_data = {
                "config_version": 1,
                "workspace_root": str(workspace_root),
                "mcp": {
                    "rpc_url": gate_rpc_url(normalized_gate_url),
                },
                "tokens": [],
            }
        # AIPOS-F27 大项D: 幂等补铸 workspace_root(已有正确值则保留)
        if not conn_data.get("workspace_root"):
            conn_data["workspace_root"] = str(workspace_root)
        
        # 更新 mcp.rpc_url (幂等:如已存在也更新为规范化 URL)
        if "mcp" not in conn_data:
            conn_data["mcp"] = {}
        conn_data["mcp"]["rpc_url"] = gate_rpc_url(normalized_gate_url)  # AIPOS-F106 件①: 门基址↔MCP 端点换算唯一实现
        
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
        # AIPOS-F106 件④: .lybra/role 写入唯一实现 enroll_client.write_role_file(合并保留既有键); 原本模块整文件覆盖的第二份写入器删除
        from tools.aipos_cli.enroll_client import write_role_file

        write_role_file(lybra_dir, role, instance, owner_policy_ref)
        
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
        # 失败 → 吊销注册码(AIPOS-F113 件③: 回滚 fail-closed —— 回滚失败明确报错并给出口, 禁吞)
        from tools.aipos_cli.enrollment import revoke_enrollment_code
        secrets_ = [code, owner_token]
        primary = _redact(f"{e.__class__.__name__}: {e}", secrets_)
        code_id = enrollment_result["code_id"]
        try:
            revoke_enrollment_code(
                gate_workspace,
                code_id,
                by="owner (enroll-deliver rollback)",
                reason=f"enroll-deliver failed: {primary}",
            )
        except Exception as rb_exc:
            raise EnrollDeliverRollbackError(
                f"enroll-deliver 失败: {primary}\n"
                f"且回滚失败: 注册码 {code_id} 未能吊销({_redact(f'{rb_exc.__class__.__name__}: {rb_exc}', secrets_)})。\n"
                f"出口: ① 手工吊销该码: lybra roles enroll-revoke {code_id} --owner-authorization-ref <owner授权引用> "
                f"(门工作区 {gate_workspace}); ② 检查 {workspace_root}/.lybra/connection.json, "
                f"若已写入 agent_instance={instance or '(无实例, role=' + role + ')'} 的 token 条目则删除该条目后重跑。"
            ) from rb_exc
        raise RuntimeError(f"enroll-deliver 失败: {primary}(已回滚: 注册码 {code_id} 已吊销)") from e


class EnrollDeliverRollbackError(RuntimeError):
    """AIPOS-F113 件③: 失败后回滚(吊销注册码)本身也失败 —— 非零退出, 消息含远端/门侧手工清理出口。"""


def _redact(text: str, secrets_: list[str | None]) -> str:
    """AIPOS-F113: 本机输出/日志/异常文本里的凭据与注册码明文 → «redacted:<指纹>»(复用 confined_worker.redact_transcript 唯一实现)。"""
    needles = [str(x) for x in secrets_ if x]
    clean, _hits = redact_transcript(str(text), needles)
    return clean


def remote_command(argv: list[str]) -> str:
    """AIPOS-F113 件②: ssh 把远端命令交给远端登录 shell 解释 —— 每个参数逐个 shlex.quote(禁字符串直拼)。"""
    return shlex.join([str(a) for a in argv])


def ssh_argv(ssh_target: str, argv: list[str]) -> list[str]:
    """本机 ssh 调用的 argv: `ssh -- <目标> <已转义远端命令>`。

    `--` 终止 ssh 选项解析, 且拒绝以 '-' 开头的目标(防 `-oProxyCommand=…` 类选项注入, fail-closed)。
    远端命令只含非秘密参数; 注册码经 stdin 送达(见 enroll_deliver_ssh)。
    """
    target = str(ssh_target or "").strip()
    if not target or target.startswith("-") or any(ch.isspace() for ch in target):
        raise ValueError(f"--ssh 目标非法(空 / 以 '-' 开头 / 含空白): {ssh_target!r}")
    return ["ssh", "--", target, remote_command(argv)]


def _run_remote(ssh_target: str, argv: list[str], *, stdin_text: str, stage: str, secrets_: list[str | None]) -> dict[str, Any]:
    """经 ssh 执行一条远端命令(参数已转义, 秘密只走 stdin), 解析其 --json 输出; 非零/非 JSON/ok≠true 一律抛错(已脱敏)。"""
    result = subprocess.run(ssh_argv(ssh_target, argv), input=stdin_text, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"远端 {stage} 失败(ssh 退出码 {result.returncode}): "
            f"{_redact((result.stderr or '').strip() or (result.stdout or '').strip(), secrets_)[:800]}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"远端 {stage} 输出不是 JSON({exc}): {_redact(result.stdout, secrets_)[:400]}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"远端 {stage} 返回 ok≠true: {_redact(json.dumps(payload, ensure_ascii=False), secrets_)[:800]}")
    return payload


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
    owner_authorization_ref: str | None = None,
) -> dict[str, Any]:
    """跨机SSH: 门内发自包含码 → 码经 ssh stdin 送远端兑换落盘 → 远端分发

    AIPOS-F113(安全):
      件① 远端 enroll 不需要 Owner 凭据 —— 自包含码(LYBRAENROLL1.*, F23)内嵌零 scope 运输凭证, 码即运输认证
          (exchange_enrollment_code 对自包含码忽略 bootstrap token)。Owner 凭据只在本机进程内作 HTTP Authorization
          调门动词 lybra_roles_enroll_code(发码唯一实现 issue_self_contained_code, 住门进程内, 运输凭证即时热生效)
          与 lybra_roles_enroll_revoke(回滚), 永不出本机、永不进任何 argv。注册码经 ssh stdin 送远端
          (`enroll_client --code-stdin`), 不进远端 argv / 不落盘 / 不进环境变量。
      件② 远端命令逐参数 shlex.quote; ssh 目标前置 `--` 且拒 '-' 开头。
      件③ 失败即经门吊销注册码; 吊销失败 → EnrollDeliverRollbackError(非零), 消息含手工清理出口。

    Args:
        role: 角色名
        instance: agent_instance
        workspace_root: 远程目标workspace根目录(字符串, 原样逐字节送达; ~ 由远端 enroll_client expanduser 展开)
        harness_root: 远程目标harness根目录(同上, distribute_tools expanduser)
        ssh_target: SSH目标(user@host)
        gate_url: Gate MCP URL(远端可达地址; 码内嵌此地址, 本机发码调用同一门)
        owner_policy_ref: Owner策略引用
        owner_token: Owner token(只在本机进程内调门)
        ttl_seconds: enrollment code有效期
        force: 强制覆盖
        owner_authorization_ref: 发码/吊销的 Owner 授权引用(门动词必填; 缺省 = owner_policy_ref)

    Returns:
        操作结果字典(凭据与注册码只出现指纹)
    """
    base_url = gate_base_url(gate_url)  # AIPOS-F106 件①: 门基址换算唯一实现(F113 新增的本地剥 /mcp 副本退役)
    auth_ref = str(owner_authorization_ref or "").strip() or str(owner_policy_ref or "").strip()
    if not auth_ref:
        raise ValueError("owner_authorization_ref(或 owner_policy_ref)必填: 门发码/吊销是 Owner 门动词")
    # 先验 ssh 目标(fail-closed, 先于发码: 非法目标不白铸码)
    ssh_argv(ssh_target, ["true"])
    client = GateClient(normalize_gate_url_for_same_host(base_url), owner_token, timeout=30.0)
    secrets_: list[str | None] = [owner_token]

    # 1. 门内发自包含码(发码唯一实现; Owner 凭据只作本机 HTTP Authorization)
    issued = client.call_tool("lybra_roles_enroll_code", {
        "role": role,
        **({"instance": instance} if instance else {}),
        "ttl": int(ttl_seconds),
        "gate_url": base_url,
        "owner_authorization_ref": auth_ref,
        "reason": f"enroll-deliver SSH to {ssh_target} for {instance or role}",
    })
    code = str(issued.get("self_contained_code") or "")
    secrets_.append(code)
    if not issued.get("ok") or not code:
        raise RuntimeError(f"门发码失败(lybra_roles_enroll_code): {_redact(json.dumps(issued, ensure_ascii=False), secrets_)[:800]}")
    code_id = str(issued.get("code_id") or "")
    enrollment_info = issued.get("enrollment") if isinstance(issued.get("enrollment"), dict) else {}
    code_fingerprint = enrollment_info.get("fingerprint")

    enroll_argv = [
        "python3", "-m", "tools.aipos_cli.enroll_client",
        "--code-stdin",
        "--gate-url", base_url,
        "--workspace", workspace_root,
        "--policy", owner_policy_ref,
        "--landed-host", ssh_target,  # AIPOS-F95 件③b: land 事件 host = ssh 目标(跨机工位, loop 不拉起而提示 host:dir)
        "--json",
    ]
    distribute_argv = ["python3", "-m", "tools.distribute_tools", harness_root, role, "--json"]
    if force:
        distribute_argv.append("--force")

    stage = "enroll"
    try:
        # 2. 远端兑换落盘: 码只走 stdin
        enroll_result = _run_remote(ssh_target, enroll_argv, stdin_text=code + "\n", stage="enroll", secrets_=secrets_)
        # 3. 远端分发工具(无秘密参数, stdin 空)
        stage = "distribute"
        distribute_result = _run_remote(ssh_target, distribute_argv, stdin_text="", stage="distribute", secrets_=secrets_)
    except Exception as e:
        primary = _redact(f"{e.__class__.__name__}: {e}", secrets_)
        remote_state = (
            f"远端 enroll 已成功(凭据已落 {workspace_root}/.lybra/connection.json), 失败在分发阶段: "
            f"可直接重跑分发 `{shlex.join(ssh_argv(ssh_target, distribute_argv))}`; 若要撤回该工位, "
            f"在远端删除该文件中 agent_instance={instance or '(无实例, role=' + role + ')'} 的 token 条目并在门侧吊销该 token"
            if stage == "distribute" else
            f"远端 enroll 未确认成功: 检查远端 {workspace_root}/.lybra/connection.json, "
            f"若已写入 agent_instance={instance or '(无实例, role=' + role + ')'} 的 token 条目则删除该条目"
        )
        try:
            revoked = client.call_tool("lybra_roles_enroll_revoke", {
                "code_id": code_id,
                "owner_authorization_ref": auth_ref,
                "reason": f"enroll-deliver SSH rollback ({stage} failed)",
            })
            if not revoked.get("ok"):
                raise RuntimeError(f"门拒绝吊销: {_redact(json.dumps(revoked, ensure_ascii=False), secrets_)[:400]}")
        except Exception as rb_exc:
            raise EnrollDeliverRollbackError(
                f"enroll-deliver SSH 失败({stage}): {primary}\n"
                f"且回滚失败: 注册码 {code_id}(指纹 {code_fingerprint}) 未能吊销"
                f"({_redact(f'{rb_exc.__class__.__name__}: {rb_exc}', secrets_)})。\n"
                f"出口: ① 手工吊销该码: 门动词 lybra_roles_enroll_revoke {{code_id: {code_id}}}"
                f"(或在门工作区 lybra roles enroll-revoke {code_id} --owner-authorization-ref <owner授权引用>); "
                f"② 远端手工清理(ssh {ssh_target}): {remote_state}。"
            ) from rb_exc
        raise RuntimeError(
            f"enroll-deliver SSH 失败({stage}): {primary}\n"
            f"已回滚: 注册码 {code_id}(指纹 {code_fingerprint}) 已经门吊销。远端状态: {remote_state}。"
        ) from e

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
            "code_id": code_id,
            "fingerprint": code_fingerprint,
        },
        "owner_token_fingerprint": client.token_fingerprint,
        "token": {
            "fingerprint": enroll_result.get("fingerprint"),
            "scopes": enroll_result.get("scopes", []),
        },
        "distribution": distribute_result,
    }


def main() -> int:
    """CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="AIPOS-R6H: enroll-deliver — 一条命令完成铸码+落位+分发",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--role", required=True, help="Role name (executor/auditor/advisor)")
    parser.add_argument("--instance", help="Agent instance (e.g., exec.lybra.mac1)")
    parser.add_argument("--target-workspace", required=True, help="Target workspace root (cannot be a governance workspace)")
    parser.add_argument("--target-harness", required=True, help="Target harness root (e.g., ~/kiwiai-pi/lybra-executor)")
    parser.add_argument("--gate-url", required=True, help="Gate MCP URL")
    parser.add_argument("--owner-policy-ref", required=True, help="Owner policy reference")
    # AIPOS-F113: Owner 凭据禁进 argv(原 --owner-token <明文> 退役) —— 从 connection.json 读, 或经本机 stdin 送入
    parser.add_argument("--owner-token-stdin", action="store_true", help="Read the owner token from stdin (first line); never pass it on the command line")
    parser.add_argument("--connection-json", help="Path to connection.json (to read owner token)")
    parser.add_argument("--owner-authorization-ref", help="Owner authorization ref for gate enroll-code/revoke verbs (--ssh; default = --owner-policy-ref)")
    parser.add_argument("--ssh", help="SSH target for remote delivery (user@host)")
    parser.add_argument("--ttl", type=int, default=3600, help="Enrollment code TTL in seconds (default: 3600)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing files")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    
    args = parser.parse_args()
    
    # Load owner token
    if args.owner_token_stdin:
        owner_token = sys.stdin.readline().strip()
        if not owner_token:
            print("Error: --owner-token-stdin given but stdin carried no token", file=sys.stderr)
            return 1
    elif args.connection_json:
        try:
            owner_token = load_owner_token(connection_json=args.connection_json, role="owner")
        except Exception as e:
            print(f"Error loading owner token: {e}", file=sys.stderr)
            return 1
    else:
        print("Error: provide --connection-json or --owner-token-stdin (owner token never on the command line)", file=sys.stderr)
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
                owner_authorization_ref=args.owner_authorization_ref,
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
        # AIPOS-F113: 出口文本再脱敏一次(Owner 凭据明文永不上终端)
        message = _redact(str(e), [owner_token])
        if args.json:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
