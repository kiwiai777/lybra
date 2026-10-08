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

from tools.aipos_cli.confirm_client import (
    GateAddressError,
    GateClient,
    GateError,
    GateTimeout,
    load_owner_token,
    resolve_gate_base_url,
)
from tools.aipos_cli.renderer import render_json


def _role_class_map(repo_root: Path | None) -> dict[str, str]:
    """roles 注册表 {角色名: 角色类}(回退方案, 仅有 repo_root 时读; AIPOS-F44D-A-fix1 原内联, AIPOS-F134 抽出供驱动方实例判定同用)。"""
    role_class_map: dict[str, str] = {}
    if not repo_root:
        return role_class_map
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
        except (OSError, ValueError) as exc:  # 注册表读取失败: 出声后降级为名字匹配(AIPOS-F78B: 禁静默吞)
            print(f"two_phase_shell_factory: roles registry unreadable at {roles_schema_path}: {exc}", file=sys.stderr)
    return role_class_map


def _token_has_role_class(token: Any, required_role_class: str, role_class_map: dict[str, str]) -> bool:
    """token 条目是否属所需角色类——唯一判定(resolve_role_from_connection 与 driver_token_entry 同用):
    ① 条目 role_class 字段 = 所需类(门兑换自定义角色时写入) → ② 注册表角色类 → ③ 角色名即内建类名(向后兼容)。
    判序与 AIPOS-F44D-A-fix1 原内联逻辑逐字等价(抽出不改行为)。"""
    if not isinstance(token, dict):
        return False
    role = str(token.get("role") or "").strip()
    if not role:
        return False
    if str(token.get("role_class") or "").strip() == required_role_class:
        return True
    if role in role_class_map:
        return role_class_map[role] == required_role_class
    return role == required_role_class


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
    
    # 2+3. 匹配角色(AIPOS-F134 件①: 角色类判定抽为 _token_has_role_class, 驱动方按实例挑条目时同用, 禁第二判定)
    role_class_map = _role_class_map(repo_root)
    available_roles = []
    for token in tokens:
        role = token.get("role")
        if not role:
            continue
        available_roles.append(role)
        if _token_has_role_class(token, required_role_class, role_class_map):
            return role
    
    # 4. 无匹配角色, 报错带路
    raise ValueError(
        f"No role with class '{required_role_class}' found in {connection_json_path}. "
        f"Available roles: {', '.join(available_roles)}. "
        f"Please use a connection.json with a {required_role_class}-class role "
        f"(add 'role_class: {required_role_class}' to the token entry), "
        f"or register your custom role in 0_ontology/schemas/roles.schema.json with class={required_role_class}."
    )


def driver_role_class(repo_root: Path | None = None) -> str:
    """AIPOS-F78 前置零①: 账务动词驱动方的角色类——唯一声明 roles.schema.json driver.role_class(缺 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    driver = load_schema("roles", repo_root).get("driver")
    role_class = str((driver or {}).get("role_class") or "").strip() if isinstance(driver, dict) else ""
    if not role_class:
        raise SchemaLoadError("roles.schema.json driver.role_class(账务动词驱动方)未声明")
    return role_class


class DriverTokenError(ValueError):
    """AIPOS-F134 件①: 驱动方实例的凭据解析失败(实例无 token / 非驱动方角色类 / 多实例未指明)。fail-closed, 禁回落首个驱动方 token;
    拒因只带指纹与出口, 永不带 token 值。ValueError 子类: 既有 `except ValueError` 调用方照常接住。"""


# AIPOS-F134 件①: loop 派生命令子进程的驱动方实例(声明 config.schema environment_variables.LYBRA_DRIVER_INSTANCE;
# 只由 next_resolver._run_product_command 在 driver_scope 内写入子进程环境, 本模块 driver_instance_hint 唯一读取)
DRIVER_INSTANCE_ENV = "LYBRA_DRIVER_INSTANCE"


def driver_instance_hint() -> str:
    """AIPOS-F134 件①: 当前驱动方实例——进程内 = lybra loop 已校验的 --actor(next_resolver.driver_scope); 派生命令子进程 =
    环境变量 LYBRA_DRIVER_INSTANCE(loop 写入)。都无 = ""(人手直敲薄壳: 按连接文件判, 见 resolve_driver_role_from_connection)。"""
    import os

    from tools.aipos_cli.next_resolver import _scoped_driver  # 延迟导入(next_resolver 依赖本模块)

    return str(_scoped_driver().get("actor") or os.environ.get(DRIVER_INSTANCE_ENV) or "").strip()


def driver_token_entry(*, connection_json_path: str | Path, agent_instance: str, repo_root: Path | None = None) -> dict[str, Any]:
    """AIPOS-F134 件①: 驱动方实例的 token 条目——挑选唯一实现 token_resolver.select_token_entry(只按 agent_instance, 不给 role:
    该实例无可用条目即拒, 禁回落连接文件里首个驱动方 token); 命中条目的角色类须 = roles.schema driver.role_class(_token_has_role_class)。
    拒 = DriverTokenError(带出口)。"""
    from tools.aipos_cli.token_resolver import TokenResolutionError, load_connection_tokens, select_token_entry, token_fingerprint

    instance = str(agent_instance or "").strip()
    if not instance:
        raise DriverTokenError("驱动方实例为空, 无从按实例挑凭据")
    path = str(Path(connection_json_path).expanduser())
    tokens = load_connection_tokens(path)  # 读失败/无 tokens = ValueError(fail-closed)
    try:
        entry = select_token_entry(tokens, agent_instance=instance, source=path)
    except TokenResolutionError as exc:
        bound = driver_token_instances(connection_json_path=path, repo_root=repo_root, tokens=tokens)
        raise DriverTokenError(
            f"驱动方实例 {instance!r} 在 {path} 无可用凭据(按实例挑, 禁回落首个驱动方 token): {exc}。"
            f"该连接文件已绑定的驱动方实例: {bound or '(无)'}。出口: 以已接入的实例跑 `lybra loop --actor <实例>`, "
            f"或为 {instance} 接入顾问凭据(Owner 发带 --instance {instance} 的注册码 → `lybra roles enroll --code <码> --workspace <治理根>`)"
        ) from exc
    wanted = driver_role_class()
    if not _token_has_role_class(entry, wanted, _role_class_map(repo_root)):
        raise DriverTokenError(
            f"驱动方实例 {instance!r} 的凭据(role={entry.get('role')!r}, {token_fingerprint(str(entry.get('token') or ''))})"
            f"角色类不是驱动方 {wanted}(roles.schema driver.role_class), 不能提交账务动词。"
            f"出口: 为该实例接入驱动方角色类({wanted} 或其自定义角色)的凭据"
        )
    return entry


def driver_token_instances(*, connection_json_path: str | Path, repo_root: Path | None = None,
                           tokens: list[Any] | None = None) -> list[str]:
    """AIPOS-F134 件①: 连接文件中可用(非 retired 且 token 非空, token_resolver.is_token_entry_active)驱动方角色类条目绑定的实例
    (去重, 文件序)。角色级条目(无 agent_instance, 如自定义角色 svc-<名>)不计。"""
    from tools.aipos_cli.token_resolver import is_token_entry_active, load_connection_tokens

    items = tokens if tokens is not None else load_connection_tokens(str(Path(connection_json_path).expanduser()))
    wanted = driver_role_class()
    role_map = _role_class_map(repo_root)
    out: list[str] = []
    for entry in items:
        if not is_token_entry_active(entry) or not _token_has_role_class(entry, wanted, role_map):
            continue
        instance = str(entry.get("agent_instance") or "").strip()
        if instance and instance not in out:
            out.append(instance)
    return out


def resolve_driver_role_from_connection(*, connection_json_path: str, repo_root: Path | None = None,
                                        agent_instance: str | None = None) -> str:
    """AIPOS-F78 前置零①: 账务动词(claim/return/verdict…)一律用驱动方 token 提交——按 roles.schema driver.role_class
    从 connection.json 选 token 的 role 名(actor/agent_instance 仍=卡实例, 由调用方传)。无驱动方 token = ValueError 带路。

    AIPOS-F134 件①: 驱动方实例已知(显式 agent_instance, 或 driver_instance_hint: loop --actor)= 取该实例条目的角色(driver_token_entry,
    无即拒, 禁回落首个); 未知且连接文件绑定了 ≥2 个驱动方实例 = 拒(多顾问实例不得取首个冒名); 否则既有行为(首个驱动方角色类)。"""
    wanted = driver_role_class()  # schema 单一源在产品仓(自动定位), 禁随治理根解析
    instance = str(agent_instance or driver_instance_hint() or "").strip()
    if instance:
        return str(driver_token_entry(connection_json_path=connection_json_path, agent_instance=instance, repo_root=repo_root).get("role") or "")
    try:
        bound = driver_token_instances(connection_json_path=connection_json_path, repo_root=repo_root)
    except ValueError as exc:
        raise ValueError(f"账务动词须由驱动方 token 提交(roles.schema driver.role_class={wanted}), 但 connection.json 读不出: {exc}") from exc
    if len(bound) > 1:
        raise DriverTokenError(
            f"{connection_json_path} 绑定了 {len(bound)} 个驱动方实例 {bound}, 未指明本次驱动方(禁取首个冒名)。"
            f"出口: 经 `lybra loop --task-id <卡> --actor <实例>` 推进(派生命令自动带实例), 人手直敲薄壳时设环境变量 {DRIVER_INSTANCE_ENV}=<实例>"
        )
    try:
        return resolve_role_from_connection(
            connection_json_path=connection_json_path, required_role_class=wanted, repo_root=repo_root
        )
    except ValueError as exc:
        raise ValueError(
            f"账务动词须由驱动方 token 提交(roles.schema driver.role_class={wanted}), 但 connection.json 无该角色类 token: {exc}"
        ) from exc


def load_role_token(*, connection_json_path: str, role: str) -> str:
    """AIPOS-F134 件①: 薄壳取门凭据的唯一入口(execute_two_phase_verb / execute_single_phase_via_gate 同用)。
    驱动方实例已知(driver_instance_hint)且所求角色 = 该实例条目角色 → 取该实例条目(同角色多实例不取首个); 否则按角色(既有 load_owner_token)。"""
    instance = driver_instance_hint()
    if instance:
        entry = driver_token_entry(connection_json_path=connection_json_path, agent_instance=instance)
        if str(entry.get("role") or "").strip() == str(role or "").strip():
            return str(entry.get("token") or "").strip()
    return load_owner_token(connection_json=connection_json_path, role=role)


def _no_envelope_exit() -> int:
    """无信封退出码: 唯一声明 verbs.schema lybra_loop.exit_codes.no_envelope(与 loop_driver.exit_code_for 同源)。"""
    from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract

    return exit_code_for(load_loop_contract(), "no_envelope")


def _is_envelope_rejection(resp: dict[str, Any]) -> bool:
    """门拒因是否为信封守卫(error_code/errors[].category 以 ENVELOPE_ 开头, transitions envelope_guards)。"""
    codes = [str(resp.get("error_code") or "")]
    for err in resp.get("errors") or []:
        if isinstance(err, dict):
            codes.append(str(err.get("category") or err.get("code") or ""))
    return any(c.startswith("ENVELOPE_") for c in codes)


def _stage_contract(verb_name: str) -> dict[str, Any]:
    from tools.schema_loader import get_verb_contract

    contract = get_verb_contract(verb_name) or {}
    stage = contract.get("stage_contract") if isinstance(contract, dict) else None
    return stage if isinstance(stage, dict) else {}


def client_timeout_seconds(verb_name: str) -> float | None:
    """AIPOS-F90 件①(缺陷③): 门动词的客户端等待上限——verbs.schema <verb>.stage_contract.client_timeout_ms 声明(须覆盖门处理时长,
    如认领同步建工作树); 未声明 = None(GateClient 缺省 = config.schema timeouts.gate_mcp_request_ms)。禁写死秒数。"""
    value = _stage_contract(verb_name).get("client_timeout_ms")
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.schema.json {verb_name}.stage_contract.client_timeout_ms 非正数")
    return float(value) / 1000.0


def readback_after_timeout(client: Any, verb_base: str, args_dict: dict[str, Any]) -> tuple[bool | None, str]:
    """AIPOS-F90 件①(缺陷③): 等门应答超时后回读真相判定门侧是否已落(幂等: 已由本实例认领 = 成功)。

    判据唯一声明 verbs.schema <verb_base>_dry_run.stage_contract.timeout_readback(read_verb + 落地条件), 读门的只读动词(不读文件)。
    返回 (True=已落 / False=未落 / None=未声明判据或回读失败, 说明原文)。"""
    decl = _stage_contract(f"{verb_base}_dry_run").get("timeout_readback")
    if not isinstance(decl, dict):
        return None, f"verbs.schema {verb_base}_dry_run 未声明 timeout_readback, 结果未知"
    task_id = str(args_dict.get(str(decl.get("task_id_arg") or "task_id")) or "").strip()
    expected = str(args_dict.get(str(decl.get("claimer_arg") or "agent_instance")) or "").strip()
    try:
        listing = client.call_tool(str(decl["read_verb"]), {})
    except (GateError, KeyError) as exc:
        return None, f"回读 {decl.get('read_verb')} 失败: {exc}"
    data = listing.get("data") if isinstance(listing.get("data"), dict) else {}
    for task in data.get("tasks") or []:
        if not isinstance(task, dict) or str(task.get("task_id") or "") != task_id:
            continue
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        state = str(task.get("queue_state") or "")
        claimer = str(metadata.get(str(decl.get("claimer_field") or "claimed_by")) or "").strip()
        landed = state == str(decl.get("landed_queue_state")) and bool(expected) and claimer == expected
        return landed, f"{task_id}: queue_state={state} {decl.get('claimer_field')}={claimer or '(空)'} (期望 {decl.get('landed_queue_state')}/{expected})"
    return False, f"{task_id}: 门的 {decl.get('read_verb')} 中查无此卡"


def _call_with_readback(client: Any, verb: str, verb_base: str, args: dict[str, Any], replay_args: dict[str, Any],
                        json_output: bool) -> tuple[dict[str, Any] | None, tuple[int, dict[str, Any]] | None]:
    """调一个门动词; 等应答超时 → 回读判定(已落 = 成功返回, 不再执行第二次; 未落/未知 = 出声失败带出口)。"""
    try:
        return client.call_tool(verb, args, timeout=client_timeout_seconds(verb)), None
    except GateTimeout as exc:
        landed, detail = readback_after_timeout(client, verb_base, replay_args)
        if landed:
            resp = {"ok": True, "readback_landed": True, "readback": detail, "timeout": str(exc)}
            if json_output:
                print(render_json(resp))
            else:
                print(f"{verb_base.replace('lybra_', '').replace('_', ' ')} landed (等门应答超时后回读确认门侧已落, 不重复提交): {detail}")
            return None, (0, resp)
        task_id = replay_args.get("task_id") or replay_args.get("audit_task_id") or "<卡ID>"
        state = "门侧未落" if landed is False else "结果未知"
        return None, _fail(f"{verb} 等门应答超时({exc}); 回读: {state} — {detail}。出口: lybra next --task-id {task_id} 重推导核实后再推进, 禁直接重复提交")
    except GateError as exc:
        return None, _fail(f"{verb} failed: {exc}")


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
    
    AIPOS-F73B件②: PreAuthorized 模式走一阶段协议——只调 dry_run（门自动放行落记录）。
    complex 类卡仍走 Supervised 两跳。

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
        token = load_role_token(connection_json_path=connection_json_path, role=role)  # AIPOS-F134 件①: 驱动方按实例
    except ValueError as exc:
        return _fail(f"cannot load {role} token: {exc}")

    # AIPOS-F106 件①: 门基址唯一推导口(显式凭据文件层, 委托 ConnectionResolver.resolve_gate_url; 原 .replace 全串替换
    # 与未声明的 mcp.url 回退删除); 文件坏/未声明 mcp.rpc_url = 拒(fail-closed)
    try:
        gate_url = resolve_gate_base_url(connection_json=connection_json_path, require_declared=True)
    except GateAddressError as exc:
        return _fail(f"cannot determine gate URL from connection.json: {exc}")

    # 2. 初始化 gate 客户端
    try:
        client = GateClient(gate_url, token)
        client.initialize()
    except Exception as exc:
        return _fail(f"gate client init failed: {exc}")

    # AIPOS-F73B件②: 判定是否走一阶段协议
    autonomy_mode = args_dict.get("autonomy_mode", "Supervised")
    use_one_phase = (autonomy_mode == "PreAuthorized")
    
    # 3. Step 1: dry_run（PreAuthorized 时门自动放行）; AIPOS-F90 件①: 等应答超时 → 回读判定, 不得直接报失败
    dry_run_verb = f"{verb_base}_dry_run"
    dry_run_resp, early = _call_with_readback(client, dry_run_verb, verb_base, args_dict, args_dict, json_output)
    if early is not None:
        return early

    # 检查 BLOCK
    verdict = dry_run_resp.get("verdict", "")
    if verdict == "BLOCK" or dry_run_resp.get("isError"):
        reasons = dry_run_resp.get("blocking_reasons") or dry_run_resp.get("errors") or []
        if json_output:
            print(render_json(dry_run_resp))
        else:
            print(f"{verb_base} BLOCKED: {reasons}", file=sys.stderr)
        # AIPOS-F78B 件③: 一阶段被信封门拒(ENVELOPE_*)= 无覆盖驱动方的有效信封 → exit 5(verbs.schema lybra_loop.exit_codes.no_envelope)
        if use_one_phase and _is_envelope_rejection(dry_run_resp):
            return _no_envelope_exit(), dry_run_resp
        return 1, dry_run_resp

    # AIPOS-F73B件②: PreAuthorized 一阶段——以记录落地为判据
    if use_one_phase:
        task_id = args_dict.get("task_id") or args_dict.get("audit_task_id", "")
        # AIPOS-F78B 件③(fail-closed): 门未放行(回落 Supervised 预览: 带 dry_run_token / preauthorized_release=false)≠ 落记录,
        # 不得报 "completed"——exit 5 带 envelope_error 出口
        if dry_run_resp.get("preauthorized_release") is False or dry_run_resp.get("dry_run_token"):
            if json_output:
                print(render_json(dry_run_resp))
            else:
                print(f"{verb_base} NOT released (PreAuthorized 信封未匹配, 门回落 Supervised 预览, 未落记录): "
                      f"{dry_run_resp.get('envelope_error') or dry_run_resp.get('errors') or ''}", file=sys.stderr)
            return _no_envelope_exit(), dry_run_resp
        
        # 输出结果
        if json_output:
            print(render_json(dry_run_resp))
        else:
            op_name = verb_base.replace("lybra_", "").replace("_", " ")
            print(f"{op_name} completed (PreAuthorized) for {task_id}")
        
        return 0, dry_run_resp

    # 否则走两阶段协议（Supervised）
    dry_run_token = dry_run_resp.get("dry_run_token")
    if verb_base == "lybra_queue_close" and not dry_run_token:
        dry_run_token = "replay_args"  # close 不发 token(verbs.schema stage_contract.dry_run_emits_token=false)
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
    if verb_base == "lybra_queue_close":
        # verbs.schema lybra_queue_close_dry_run.confirm_via=replay_args: confirm 重放 task_id/actor/closure_evidence, 无 dry_run_token
        confirm_args = {k: v for k, v in args_dict.items() if k in ("task_id", "actor", "closure_evidence")}

    confirm_resp, early = _call_with_readback(client, confirm_verb, verb_base, confirm_args, args_dict, json_output)
    if early is not None:
        return early

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
        token = load_role_token(connection_json_path=connection_json_path, role=role)  # AIPOS-F134 件①: 驱动方按实例
    except ValueError as exc:
        return _fail(f"cannot load {role} token: {exc}")

    # AIPOS-F106 件①: 门基址唯一推导口(显式凭据文件层, 委托 ConnectionResolver.resolve_gate_url; 原 .replace 全串替换
    # 与未声明的 mcp.url 回退删除); 文件坏/未声明 mcp.rpc_url = 拒(fail-closed)
    try:
        gate_url = resolve_gate_base_url(connection_json=connection_json_path, require_declared=True)
    except GateAddressError as exc:
        return _fail(f"cannot determine gate URL from connection.json: {exc}")

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
