#!/usr/bin/env python3
"""AIPOS-F22 大项B: 两阶段动词薄壳工厂

单一实现生成 CLI 的 --confirm 逻辑（dry_run → confirm 同一门动词，参数走注册表解析）。
覆盖动词: queue_claim / queue_return / audit_verdict / task_progress (单阶段，走 MCP)。

防碎片化红线:
- 禁逐动词手写两阶段逻辑，一律经本工厂
- 三层（托管/工位命令/CLI）继续共用同一执行函数与同一门动词
- verbs.schema 注册表单源，参数/阶段定义不得第二处硬编码
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tools.aipos_cli.confirm_client import GateClient, load_owner_token
from tools.aipos_cli.renderer import render_json


def resolve_role_from_connection(
    *,
    connection_json_path: str,
    required_role_class: str,
    repo_root: Path | None = None,
) -> str:
    """AIPOS-F44D-A-fix1: 从连接文件+注册表解析实际角色名
    
    问题: CLI 两阶段薄壳写死 role="executor", 导致自定义角色项目全断。
    解决: 优先从 token.role_class 读取，回退到 schema 注册表。
    
    Args:
        connection_json_path: connection.json 路径
        required_role_class: 所需角色类 ("executor" | "auditor" | "advisor")
        repo_root: 治理仓根目录 (用于读取 roles 注册表, 可选)
    
    Returns:
        实际角色名 (如 "hbj-coder" 而非 "executor")
    
    Raises:
        ValueError: 无匹配角色, 报错带路 (点名实有角色与期望角色)
    """
    # 1. 读取 connection.json
    try:
        conn_data = json.loads(Path(connection_json_path).read_text(encoding="utf-8"))
        tokens = conn_data.get("tokens", [])
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"cannot read connection.json: {exc}")
    
    if not tokens:
        raise ValueError(f"no tokens found in {connection_json_path}")
    
    # 2. 读取 roles 注册表 (回退方案, 如果有 repo_root)
    role_class_map = {}  # {role_name: role_class}
    if repo_root:
        # AIPOS-F44D-A-fix1: 修正路径为 roles.schema.json (不是 .yaml)
        roles_schema_path = repo_root / "0_ontology" / "schemas" / "roles.schema.json"
        if roles_schema_path.exists():
            try:
                schema_data = json.loads(roles_schema_path.read_text(encoding="utf-8"))
                for role_def in schema_data.get("roles", []):
                    role_name = role_def.get("role")
                    role_class = role_def.get("class")
                    if role_name and role_class:
                        role_class_map[role_name] = role_class
            except Exception:
                pass  # 注册表读取失败, 降级为名字匹配
    
    # 3. 匹配角色
    available_roles = []
    for token in tokens:
        role = token.get("role")
        if not role:
            continue
        available_roles.append(role)
        
        # AIPOS-F44D-A-fix1: 优先从 token 的 role_class 字段读取 (现成的, 零解析成本)
        token_role_class = token.get("role_class")
        if token_role_class and token_role_class == required_role_class:
            return role
        
        # 回退: 按注册表角色类匹配
        if role in role_class_map:
            if role_class_map[role] == required_role_class:
                return role
        # 降级: 直接名字匹配 (向后兼容)
        elif role == required_role_class:
            return role
    
    # 4. 无匹配角色, 报错带路
    raise ValueError(
        f"No role with class '{required_role_class}' found in {connection_json_path}. "
        f"Available roles: {', '.join(available_roles)}. "
        f"Please use a connection.json with a {required_role_class}-class role "
        f"(add 'role_class: {required_role_class}' to the token entry), "
        f"or register your custom role in 0_ontology/schemas/roles.schema.json with class={required_role_class}."
    )


def _fail(error: str) -> tuple[int, dict[str, Any]]:
    """错误路径必须出声(exit 1 + stderr): 静默失败 = 不可审计(F22-fix1 可观测性补齐)"""
    print(f"two_phase_shell_factory: {error}", file=sys.stderr)
    return 1, {"error": error}


def execute_two_phase_verb(
    *,
    verb_base: str,
    args_dict: dict[str, Any],
    connection_json_path: str,
    role: str,
    owner_confirmation_literal: str = "OWNER_CONFIRMED",
    json_output: bool = False,
) -> tuple[int, dict[str, Any]]:
    """两阶段动词薄壳工厂核心：dry_run → confirm

    Args:
        verb_base: 基础动词名（如 "lybra_queue_claim"）
        args_dict: 动词参数字典（已按 verbs.schema 解析）
        connection_json_path: connection.json 路径
        role: 角色名（用于加载 token）
        owner_confirmation_literal: 自确认字面常量（AIPOS-328）
        json_output: 是否 JSON 输出

    Returns:
        (exit_code, response_dict)
    """
    # 1. 加载 gate 连接
    try:
        token = load_owner_token(connection_json=connection_json_path, role=role)
    except ValueError as exc:
        return _fail(f"cannot load {role} token: {exc}")

    try:
        conn_data = json.loads(Path(connection_json_path).read_text(encoding="utf-8"))
        gate_url = conn_data.get("mcp", {}).get("rpc_url", "").replace("/mcp", "")
        if not gate_url:
            gate_url = conn_data.get("mcp", {}).get("url", "")
        if not gate_url:
            return _fail("cannot determine gate URL from connection.json")
    except (json.JSONDecodeError, OSError) as exc:
        return _fail(f"cannot read connection.json: {exc}")

    # 2. 初始化 gate 客户端
    try:
        client = GateClient(gate_url, token)
        client.initialize()
    except Exception as exc:
        return _fail(f"gate client init failed: {exc}")

    # 3. Step 1: dry_run
    dry_run_verb = f"{verb_base}_dry_run"
    # AIPOS-F51: for queue_return, inject owner_confirmation_token into dry_run
    # so self-check waiver is reachable at the same stage as the判据.
    dry_run_args = dict(args_dict)
    if verb_base == "lybra_queue_return":
        dry_run_args["owner_confirmation_token"] = owner_confirmation_literal
    try:
        dry_run_resp = client.call_tool(dry_run_verb, dry_run_args)
    except Exception as exc:
        return _fail(f"{dry_run_verb} failed: {exc}")

    # 检查 BLOCK
    verdict = dry_run_resp.get("verdict", "")
    if verdict == "BLOCK" or dry_run_resp.get("isError"):
        reasons = dry_run_resp.get("blocking_reasons") or dry_run_resp.get("errors") or []
        if json_output:
            print(render_json(dry_run_resp))
        else:
            print(f"{verb_base} BLOCKED: {reasons}", file=sys.stderr)
        return 1, dry_run_resp

    dry_run_token = dry_run_resp.get("dry_run_token")
    if not dry_run_token:
        if json_output:
            print(render_json(dry_run_resp))
        else:
            print(f"Error: no dry_run_token in {dry_run_verb} response", file=sys.stderr)
        return 1, dry_run_resp

    # 4. Step 2: confirm（AIPOS-328 自确认）
    confirm_verb = f"{verb_base}_confirm"
    confirm_args = {
        "dry_run_token": str(dry_run_token),
        "actor": args_dict.get("actor"),
        "agent_instance": args_dict.get("agent_instance"),
        "owner_policy_ref": args_dict.get("owner_policy_ref"),
        "owner_confirmation_token": owner_confirmation_literal,
    }
    # 针对特定动词的额外参数
    if verb_base == "lybra_audit_verdict":
        confirm_args["audit_task_id"] = args_dict.get("audit_task_id")
        confirm_args["reviewed_task_id"] = args_dict.get("reviewed_task_id")

    try:
        confirm_resp = client.call_tool(confirm_verb, confirm_args)
    except Exception as exc:
        return _fail(f"{confirm_verb} failed: {exc}")

    # 5. 输出结果
    if json_output:
        print(render_json(confirm_resp))
    else:
        # 简化输出
        op_name = verb_base.replace("lybra_", "").replace("_", " ")
        task_id = args_dict.get("task_id") or args_dict.get("audit_task_id", "")
        print(f"{op_name} confirmed for {task_id}")

    return 0, confirm_resp


def execute_single_phase_via_gate(
    *,
    verb_name: str,
    args_dict: dict[str, Any],
    connection_json_path: str,
    role: str,
    json_output: bool = False,
) -> tuple[int, dict[str, Any]]:
    """单阶段动词经 gate MCP 执行（如 task_progress）

    Args:
        verb_name: 动词全名（如 "lybra_task_progress"）
        args_dict: 动词参数字典
        connection_json_path: connection.json 路径
        role: 角色名
        json_output: 是否 JSON 输出

    Returns:
        (exit_code, response_dict)
    """
    # 1. 加载 gate 连接
    try:
        token = load_owner_token(connection_json=connection_json_path, role=role)
    except ValueError as exc:
        return _fail(f"cannot load {role} token: {exc}")

    try:
        conn_data = json.loads(Path(connection_json_path).read_text(encoding="utf-8"))
        gate_url = conn_data.get("mcp", {}).get("rpc_url", "").replace("/mcp", "")
        if not gate_url:
            gate_url = conn_data.get("mcp", {}).get("url", "")
        if not gate_url:
            return _fail("cannot determine gate URL from connection.json")
    except (json.JSONDecodeError, OSError) as exc:
        return _fail(f"cannot read connection.json: {exc}")

    # 2. 初始化 gate 客户端并调用
    try:
        client = GateClient(gate_url, token)
        client.initialize()
        resp = client.call_tool(verb_name, args_dict)
    except Exception as exc:
        return _fail(f"{verb_name} failed: {exc}")

    # 3. 检查结果
    if resp.get("isError"):
        if json_output:
            print(render_json(resp))
        else:
            errors = resp.get("errors", [])
            print(f"{verb_name} error: {errors}", file=sys.stderr)
        return 1, resp

    # 4. 输出
    if json_output:
        print(render_json(resp))
    else:
        print(f"{verb_name} completed: {resp.get('ok', False)}")

    return 0, resp


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
