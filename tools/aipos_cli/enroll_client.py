#!/usr/bin/env python3
"""AIPOS-R2: lybra enroll — 一条命令铸角色凭据+落.lybra自发现配置(新机/新角色零手工上线)

设计权威: DESIGN v2 §4 (gate分发器) + §7 R2

功能:
1. 使用 enrollment code 从 gate 兑换 role credential
2. 落 `.lybra/connection.json`(带 token,0600)
3. 落 `.lybra/role`、`.lybra/actor`、`.lybra/policy`(自发现配置)
4. 幂等:重复 enroll 同 (project, role, machine) 轮换凭据不重复注册
5. 跨机安全:enrollment code 可明文传输,token 只落目标机配置文件

Usage:
    lybra enroll --code <enrollment_code> --gate-url <url> [--workspace <path>]

Security:
  - Enrollment code 可明文传输(它不是凭据,只是兑换凭据的临时通行证)
  - Token 明文只出现在目标机的 .lybra/connection.json(0600)
  - Token 不输出到 stdout/stderr/日志
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
from tools.aipos_cli.clock import iso_z
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.aipos_cli.confirm_client import gate_rpc_url  # AIPOS-F106 件①: 门基址↔MCP 端点换算唯一实现

try:
    import urllib.request
    import urllib.error
except ImportError:
    raise RuntimeError("This script requires Python 3.x with urllib")


def is_same_host(gate_url: str) -> bool:
    """判定 gate host 是否解析到本机 (AIPOS-R6K件①)。
    
    同机判定逻辑:
    1. gate host 是 loopback 地址(127.0.0.1/localhost/::1)
    2. gate host 解析的 IP 与本机 IP 有交集
    
    Returns:
        True if gate is on same host, False otherwise
    """
    try:
        parsed = urlparse(gate_url)
        host = parsed.hostname or parsed.netloc.split(':')[0]
        
        # 直接是 loopback
        if host in ('127.0.0.1', 'localhost', '::1'):
            return True
        
        # 解析 gate host 的 IP
        gate_ips = set(addr[4][0] for addr in socket.getaddrinfo(host, None))
        
        # 获取本机所有 IP
        local_ips = {'127.0.0.1', '::1'}
        hostname = socket.gethostname()
        try:
            local_ips.update(addr[4][0] for addr in socket.getaddrinfo(hostname, None))
        except OSError as exc:
            # AIPOS-F115 件③(gap #58 残留): 本机名解析失败 → 只按 loopback 判, 出声(原 except Exception: pass)
            print(f"Warning: 本机名 {hostname!r} 解析失败({exc}), 同机判定只认 loopback", file=sys.stderr)
        
        # 判定交集
        return bool(gate_ips & local_ips)
    except (OSError, ValueError) as exc:
        # AIPOS-F115 件③: 门地址解析失败 = 判「非同机」(按原 URL 连接, 不改写为 loopback), 出声; 原 except Exception 裸吞
        print(f"Warning: 门地址 {gate_url!r} 无法解析({exc}), 按非同机处理", file=sys.stderr)
        return False


def normalize_gate_url_for_same_host(gate_url: str) -> str:
    """同机时规范化为 loopback URL (AIPOS-R6K件①: 免疫代理劫持)。
    
    Args:
        gate_url: 原始 gate URL
    
    Returns:
        同机时返回 http://127.0.0.1:<port>,跨机返回原URL
    """
    if not gate_url:
        return gate_url
    if not is_same_host(gate_url):
        return gate_url
    
    parsed = urlparse(gate_url)
    from tools.schema_loader import get_config_port

    port = parsed.port or get_config_port("gate_default")  # AIPOS-F106 件②: 端口单源 config.schema ports.gate_default
    return f"http://127.0.0.1:{port}"


def verify_token_against_gate(gate_url: str, token: str, *, timeout: int = 15) -> tuple[bool, str]:
    """AIPOS-R6S 大项C②: enroll --verify — 用新铸 token 立刻调一次 gate, 验真通。

    调一个只读工具 (lybra_gate_version), 成功 → (True, detail); 失败(401/拒)→ (False, detail)。
    """
    url = gate_rpc_url(gate_url)  # AIPOS-F106 件①: 门基址→MCP 端点唯一换算
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "lybra_gate_version", "arguments": {}},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return False, f"verify request failed: {e}"
    if "error" in result:
        return False, f"gate error: {json.dumps(result['error'])[:200]}"
    return True, "gate accepted new token (lybra_gate_version ok)"


def converge_role_tokens(connection_data: dict[str, Any], role: str) -> tuple[list[str], bool]:
    """AIPOS-R6S 大项C③: 同角色多 token 收敛。

    移除该角色下 agent_instance 以 'test.' 开头的陈旧测试 token(治理仓 executor 仍3条含
    两条 test.* 的实证)。保留真实绑定实例 token。返回 (removed_instances, changed)。
    """
    tokens = connection_data.get("tokens")
    if not isinstance(tokens, list):
        return [], False
    removed: list[str] = []
    kept: list[dict[str, Any]] = []
    changed = False
    for entry in tokens:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        if entry.get("role") != role:
            kept.append(entry)
            continue
        instance = str(entry.get("agent_instance") or "")
        if instance.startswith("test."):
            removed.append(instance)
            changed = True
            continue
        kept.append(entry)
    if changed:
        connection_data["tokens"] = kept
    return removed, changed


def exchange_enrollment_code(gate_url: str, code: str, bootstrap_token: str | None = None) -> dict[str, Any]:
    """调用 gate 的 lybra_roles_enroll_exchange MCP 动词兑换 enrollment code。
    
    AIPOS-F23: code 可为自包含码(LYBRAENROLL1.*)——内嵌零 scope 运输凭证作 transport 认证,
    不再需要任何 bootstrap token(码即运输认证); 旧裸码仍走 bootstrap token 路径(顾问内部用)。
    
    Args:
        gate_url: Gate MCP URL (e.g., http://host:<gate-port>)
        code: Enrollment code(自包含码或旧裸码)
        bootstrap_token: 旧裸码路径的 transport 凭证(自包含码自动忽略; 缺省读 LYBRA_BOOTSTRAP_TOKEN)
    
    Returns:
        MCP response dict with token_entry
    
    Raises:
        RuntimeError: If exchange fails (含 ok=False 的原因与下一步, F9 标准)
    """
    from tools.aipos_cli.enrollment import decode_self_contained_code

    sc = decode_self_contained_code(code)
    # AIPOS-F52: 修复 CLI 只发内层 code 导致门侧解码失败的问题。
    # 门侧需要完整的自包含码来解析 governance_root 等元数据。
    # 修复前: inner_code = sc["code"] —— 只发内层短码
    # 修复后: inner_code = code —— 发完整自包含码 LYBRAENROLL1.*
    inner_code = code
    if sc is not None:
        # F23: 自包含码 —— 内嵌运输凭证即 transport 认证(bootstrap 要求删除)
        bootstrap_token = sc["transport_token"]
        # 保留 inner_code = code (完整自包含码), 不再提取 sc["code"]
    elif not bootstrap_token:
        bootstrap_token = os.environ.get("LYBRA_BOOTSTRAP_TOKEN", "").strip()
    if not bootstrap_token:
        raise RuntimeError(
            "Bootstrap token required for HTTP transport authentication (legacy plain code).\n"
            "Provide via --bootstrap-token or set LYBRA_BOOTSTRAP_TOKEN env var, or better: use a "
            "self-contained code (LYBRAENROLL1.*) from `lybra roles enroll-code` — 码即运输认证, 无需 bootstrap。\n"
            "Example: lybra roles enroll --code LYBRAENROLL1.<base64> --workspace ~/my-workstation"
        )
    
    url = gate_rpc_url(gate_url)  # AIPOS-F106 件①: 门基址→MCP 端点唯一换算
    # AIPOS-F52: 发送完整自包含码(不是 sc["code"] 内层短码)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lybra_roles_enroll_exchange",
            "arguments": {"code": inner_code}  # inner_code = code (完整自包含码)
        }
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {bootstrap_token}',
    }
    
    # AIPOS-R6K件②: 禁用环境代理(trust_env=False同义)。
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    
    try:
        with opener.open(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        # AIPOS-F54 同族并入④: 运输凭证 401 分类——区分码已消费/码过期/运输凭证失效,
        # 给出"请顾问重签"的可执行出口(chris auditor 实撞笼统 401)。
        if e.code == 401:
            lowered = error_body.lower()
            if "expired" in lowered or "过期" in error_body:
                hint = "运输凭证已过期(码内嵌运输凭证超出 TTL)"
            elif "revoked" in lowered or "吊销" in error_body:
                hint = "运输凭证已被吊销"
            else:
                hint = "运输凭证失效(码内嵌运输凭证不被门接受: 可能已被消费/重签/属于其他门)"
            raise RuntimeError(
                f"HTTP 401: {hint}。原文: {error_body[:200]}\n"
                "下一步: 请顾问重签新码 —— lybra roles enroll-code --role <角色> "
                "--instance <实例> --owner-authorization-ref <owner授权引用>, 拿到新码后重跑 enroll。"
            )
        raise RuntimeError(f"HTTP {e.code}: {error_body[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Failed to connect to gate ({url}): {e.reason}\n"
            "下一步: 确认 gate 已 lybra serve 且地址可达; 自包含码内嵌 gate 地址, 码与 gate 不匹配时会走到这里。"
        )
    except Exception as e:
        raise RuntimeError(f"Exchange failed: {e}")
    
    # Parse MCP response
    if "error" in result:
        error_msg = result["error"].get("message", str(result["error"]))
        raise RuntimeError(f"Gate returned error: {error_msg}")
    
    if "result" not in result:
        raise RuntimeError("Invalid MCP response: missing 'result'")
    
    # MCP result contains structuredContent with the actual tool response
    mcp_result = result["result"]
    parsed: dict[str, Any] | None = None
    if "structuredContent" in mcp_result:
        parsed = mcp_result["structuredContent"]
    
    # Fallback: try to parse from content[0].text (legacy format)
    if parsed is None and "content" in mcp_result and mcp_result["content"]:
        text_content = mcp_result["content"][0].get("text", "")
        if text_content:
            try:
                parsed = json.loads(text_content)
            except json.JSONDecodeError:
                parsed = None
    
    if parsed is None:
        raise RuntimeError(f"Cannot parse MCP response: {json.dumps(mcp_result)[:200]}")
    
    # F23 大項目C②: ok=False 必带原因与下一步(F9)——透传 gate 的 teaching error 字段
    if not parsed.get("ok"):
        reason = str(parsed.get("message") or parsed.get("error") or "unknown error")
        next_step = str(parsed.get("suggested_next_action") or "")
        details = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}
        if not next_step and isinstance(details, dict):
            next_step = str(details.get("suggested_next_action") or "")
        # AIPOS-F54 同族并入④: 业务错误分类(码已消费/码过期/码已吊销/码不存在)带重签出口
        err_code = str(parsed.get("error_code") or parsed.get("code") or "")
        category = {
            "CODE_ALREADY_USED": "码已消费(单次码, 不可重用)",
            "CODE_EXPIRED": "码已过期(TTL 超窗)",
            "CODE_REVOKED": "码已被吊销",
            "CODE_NOT_FOUND": "码不存在(可能属于其他门/工作区)",
        }.get(err_code)
        if category and not next_step:
            next_step = ("请顾问重签新码 —— lybra roles enroll-code --role <角色> "
                         "--instance <实例> --owner-authorization-ref <owner授权引用>")
        raise RuntimeError(
            f"Enrollment exchange returned ok=False: {category + ' — ' if category else ''}{reason}"
            + (f" (error_code={err_code})" if err_code else "")
            + (f"\n下一步: {next_step}" if next_step else "")
        )
    return parsed


def ensure_lybra_dir(workspace_root: Path) -> Path:
    """确保 .lybra/ 目录存在,返回路径。"""
    lybra_dir = workspace_root / ".lybra"
    lybra_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return lybra_dir


def load_or_create_connection_json(lybra_dir: Path, gate_url: str, workspace_root: Path | None = None, *, governance_root: str | None = None) -> dict[str, Any]:
    """加载现有 connection.json 或创建新的。
    
    幂等:如果已存在,保留现有结构(board/mcp/其他 tokens);
    如果不存在,创建最小结构。
    
    AIPOS-C2 大项B: 铸全 workspace_root (config.schema 必填键)。
    已入册工位重跑 enroll 即补全; 已有正确值则保留 (幂等, 不动 token)。
    
    AIPOS-F54-fix1: workspace_root 单源 = governance_root(码内治理根)。
    当 governance_root 可用时,始终覆盖 connection.json#workspace_root(禁 harness root 混入);
    当 governance_root 不可用时,回退到 workspace_root 参数(旧行为兼容)。
    governance_root 参数同时写入 connection.json#governance_root(显式声明)。
    """
    connection_file = lybra_dir / "connection.json"
    
    if connection_file.exists():
        try:
            data = json.loads(connection_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                # 损坏的文件,重建
                data = {}
        except (json.JSONDecodeError, OSError):
            # 损坏的文件,重建
            data = {}
    else:
        data = {}
    
    # 确保基本结构存在
    if "tokens" not in data or not isinstance(data["tokens"], list):
        data["tokens"] = []
    
    # 确保 mcp 配置存在(用于 ConnectionResolver)
    # ConnectionResolver expects rpc_url to be the full MCP endpoint URL
    # AIPOS-R6K件①: 同机时写 loopback URL (免疫代理劫持)
    # AIPOS-R6S 大项C①: gate_url=None 时不触碰 mcp 配置(保留现有 rpc_url)——
    # enroll_exchange 在 gate 侧注册 token 时传 None, 此前的 None.endswith 崩溃导致
    # token 从未写进 gate 认可列表(新铸 token 被拒的根因)。
    if gate_url:
        normalized_gate_url = normalize_gate_url_for_same_host(gate_url)
        if "mcp" not in data:
            mcp_url = gate_rpc_url(normalized_gate_url)  # AIPOS-F106 件①: 门基址↔MCP 端点换算唯一实现
            data["mcp"] = {
                "rpc_url": mcp_url,
            }
        else:
            # 更新现有 mcp.rpc_url (幂等:如已存在也更新为规范化 URL)
            mcp_url = gate_rpc_url(normalized_gate_url)  # AIPOS-F106 件①: 门基址↔MCP 端点换算唯一实现
            data["mcp"]["rpc_url"] = mcp_url
    
    # AIPOS-F54-fix1: workspace_root 单源 = governance_root(码内治理根, 禁 harness root 混入)。
    # governance_root 可用时始终覆盖(已入册工位重跑 enroll 即校正);
    # governance_root 不可用时回退到 workspace_root 参数(旧行为兼容, 如 backfill 模式)。
    effective_ws_root: str | None = None
    if governance_root:
        effective_ws_root = str(Path(governance_root).expanduser().resolve())
        data["governance_root"] = effective_ws_root
    elif workspace_root is not None:
        effective_ws_root = str(workspace_root)
    if effective_ws_root:
        data["workspace_root"] = effective_ws_root
    
    # 确保 config_version 存在
    if "config_version" not in data:
        data["config_version"] = 1
    
    return data


def upsert_token_entry(connection_data: dict[str, Any], token_entry: dict[str, Any]) -> bool:
    """将 token entry 插入或更新到 connection.json tokens[] 中。
    
    AIPOS-F59: 旧条目留痕退场 — 匹配的旧 token 标记为 retired (不删除),新 token 追加。
    
    幂等:如果已存在同 agent_instance 或同 role(无 instance)的 token,标记为 retired;
    新 token 总是追加到末尾。这样确保新 token 排在后面,但因为 token_resolver 会
    过滤 retired 条目,所以新 token 会被选中。
    
    Returns:
        True if rotated (retired existing), False if new
    """
    tokens = connection_data["tokens"]
    agent_instance = token_entry.get("agent_instance")
    role = token_entry.get("role")

    # AIPOS-F92 件②: 同一凭据(同指纹的未退场条目)已在册 = 幂等, 不退场不追加。顾问 enroll 到治理根时, 门(home 注册表模式)
    # 兑换时已把同一 token 写进该治理根 connection.json, 客户端再 upsert 曾把它自己退场并追加一份 → 同指纹重复两条(一条 retired)。
    fingerprint = str(token_entry.get("fingerprint") or "").strip()
    if fingerprint and any(
        isinstance(t, dict) and not t.get("retired") and str(t.get("fingerprint") or "").strip() == fingerprint
        and t.get("token") == token_entry.get("token")
        for t in tokens
    ):
        return False

    # 查找匹配的现有 token 并标记为 retired
    rotated = False
    now = iso_z()
    for existing in tokens:
        if not isinstance(existing, dict):
            continue
        if existing.get("retired"):
            # 已经 retired,跳过
            continue
        
        # 优先匹配 agent_instance
        if agent_instance and existing.get("agent_instance") == agent_instance:
            existing["retired"] = True
            existing["retired_at"] = now
            existing["retired_reason"] = "superseded by re-enrollment"
            rotated = True
            continue
        # 其次匹配 role(仅当两者都无 agent_instance)
        if not agent_instance and not existing.get("agent_instance") and existing.get("role") == role:
            existing["retired"] = True
            existing["retired_at"] = now
            existing["retired_reason"] = "superseded by re-enrollment"
            rotated = True
            continue
    
    # 新 token 总是追加到末尾 (即使 retired 了旧的)
    tokens.append(token_entry)
    return rotated


def write_connection_json(lybra_dir: Path, connection_data: dict[str, Any]) -> None:
    """写入 connection.json,0600 权限。"""
    connection_file = lybra_dir / "connection.json"
    
    # 使用 os.open 确保 umask 不影响权限
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(connection_file, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(connection_data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        # 确保权限(防止 umask)
        os.chmod(connection_file, 0o600)


def validate_connection_complete(connection_data: dict[str, Any]) -> list[str]:
    """AIPOS-C2 大项B: 铸全校验 —— connection.json 按 config.schema 必填键逐键检查。

    AIPOS-F106 件③: 必填键只读 config.schema configuration_sources.connection.schema 的 required(唯一声明; 原代码内手写的键表删除)。
    嵌套对象(如 mcp)报其必填子键的点路径(mcp.rpc_url)。缺/空/类型不符 = 缺; 声明缺失 = SchemaLoadError(fail-closed)。
    返回缺失键列表 (空 = 完整)。缺键 = enroll 失败出声, 不落半成品。
    """
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = ((load_schema("config", code_repo_schema_root()).get("configuration_sources") or {}).get("connection") or {}).get("schema")
    if not isinstance(decl, dict) or not decl:
        raise SchemaLoadError("config.schema.json configuration_sources.connection.schema 未声明")
    kinds = {"string": str, "integer": int, "array": list, "object": dict}

    def _walk(schema: dict[str, Any], data: Any, prefix: str) -> list[str]:
        out: list[str] = []
        for key, spec in schema.items():
            if not isinstance(spec, dict) or not spec.get("required"):
                continue
            name = f"{prefix}{key}"
            value = data.get(key) if isinstance(data, dict) else None
            sub = spec.get("schema")
            if isinstance(sub, dict) and any(isinstance(s, dict) and s.get("required") for s in sub.values()):
                out.extend(_walk(sub, value, f"{name}."))
                continue
            expected = kinds.get(str(spec.get("type") or ""))
            if value is None or value == "" or (expected is not None and not isinstance(value, expected)):
                out.append(name)
        return out

    return _walk(decl, connection_data, "")


def is_governance_workspace(path: Path, governance_root: str | None = None) -> bool:
    """F23 验收⑧/第九坑防护: 判定目标是否治理工作区(enroll 禁落治理仓)。

    判据(任一命中即治理工作区):
    ① 目标路径绝对化后 == 自包含码内嵌的 governance_root
    ② 治理仓结构签名(AIPOS-F88 件②: 委托 workspace_config.has_workspace_queue 唯一结构判据, 读声明的队列根, 不看路径名)
    工位目录(pi harness 目录)不含这些结构 —— 历史实录: 误写把治理仓 .lybra/role
    污染成 auditor 身份(第九坑)。
    """
    from tools.aipos_cli.workspace_config import has_workspace_queue

    target = Path(path).resolve()
    if governance_root:
        try:
            if target == Path(governance_root).expanduser().resolve():
                return True
        except (OSError, RuntimeError) as exc:
            print(f"Warning: governance_root {governance_root!r} 不可解析, 仅按结构签名判定: {exc}", file=sys.stderr)
    return has_workspace_queue(target)


def land_enrollment_code(
    gate_url: str,
    code: str,
    *,
    transport_token: str | None = None,
    landed_detail: str = "",
) -> bool:
    """F23 验收⑦: 落盘成功后调 lybra_roles_enroll_land 关闭 grace 窗口。

    落盘失败时绝不调(码留在 grace 窗口内可免费重试)。失败不抛 —— 上层已落盘成功,
    land 失败只降级为告警(码最终由 grace 过期自然消费, 不影响工位可用性)。
    """
    from tools.aipos_cli.enrollment import decode_self_contained_code
    sc = decode_self_contained_code(code)
    inner_code = sc["code"] if sc is not None else code
    bearer = transport_token or (sc["transport_token"] if sc is not None else "")
    if not bearer:
        bearer = os.environ.get("LYBRA_BOOTSTRAP_TOKEN", "").strip()
    url = gate_rpc_url(gate_url)  # AIPOS-F106 件①: 门基址→MCP 端点唯一换算
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lybra_roles_enroll_land",
            "arguments": {"code": inner_code, "landed_detail": landed_detail[:400]},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer}",
    }
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with opener.open(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"[enroll] WARNING: land call failed (码将由 grace 窗口过期自然消费): {e}", file=sys.stderr)
        return False
    parsed = None
    mcp_result = result.get("result") or {}
    if isinstance(mcp_result, dict):
        if "structuredContent" in mcp_result:
            parsed = mcp_result["structuredContent"]
        elif mcp_result.get("content"):
            try:
                parsed = json.loads(mcp_result["content"][0].get("text", ""))
            except (json.JSONDecodeError, KeyError, IndexError):
                parsed = None
    if not (isinstance(parsed, dict) and parsed.get("ok")):
        print(f"[enroll] WARNING: land returned not-ok: {json.dumps(parsed)[:200] if parsed else result}", file=sys.stderr)
        return False
    return True


def _landed_host(explicit: str | None) -> str:
    """AIPOS-F95 件③b: land 事件的 host 值——显式(enroll_deliver --ssh 目标)优先, 否则本机主机名。不含空白(日志按空白分字段)。"""
    import socket

    host = str(explicit or "").strip() or socket.gethostname()
    if not host or any(ch.isspace() for ch in host):
        raise RuntimeError(f"land 事件 host 值非法(空或含空白): {host!r}")
    return host


def write_role_file(lybra_dir: Path, role: str, agent_instance: str | None = None, owner_policy_ref: str | None = None,
                    *, harness: dict[str, str] | None = None, slot_by_instance: bool = False) -> list[str]:
    """写入 .lybra/role 文件(统一JSON格式,AIPOS-R6H靶②)。
    
    AIPOS-F23 验收⑨: 合并保留既有键 —— 禁整文件覆盖。既有 owner_policy_ref 等键
    若新值未提供则原样保留; 新值提供则覆盖。返回实际写入的键清单。
    
    Args:
        lybra_dir: .lybra目录路径
        role: 角色名
        agent_instance: agent_instance(可选)
        owner_policy_ref: owner策略引用(可选)
        slot_by_instance: AIPOS-F134 件②: 治理席位类(顾问/规划方)写治理根时 = True, 按实例分槽(见下); 工位类 = False(行为不变)
    """
    role_file = lybra_dir / "role"
    role_data: dict[str, Any] = {}
    # 验收⑨: 先读既有文件(存在且合法 JSON 则并入, 既有键默认保留)
    if role_file.exists():
        try:
            existing = json.loads(role_file.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                role_data = dict(existing)
        except (json.JSONDecodeError, OSError):
            role_data = {}
    # AIPOS-F134 件②: 按实例分槽(slot_by_instance) —— 治理根 .lybra/role 只有一个顶层记录, 同一治理根先后接入两个顾问实例时后者覆盖前者
    # (含继承前者的 owner_policy_ref/harness)。另一实例写入(或文件已分槽)时各实例记录并存于 instances.<实例>(先接入者的顶层记录迁入其槽),
    # 互不覆盖、互不继承; 顶层仍 = 最近一次写入的实例记录(单实例治理根与全部工位: 文件形与合并语义不变, 无 instances 键)。
    # 读口不新增: 既有两个读口(ConnectionResolver.resolve_identity / charter_render.workstation_identity)照读顶层(= 最近接入实例,
    # 语义不变); 各实例槽是留存记录(互不覆盖)。多顾问实例时驱动方身份不靠 role 文件, 以 lybra loop --actor + 该实例凭据条目为准
    # (next_resolver: 连接文件绑定 ≥2 个驱动方实例而未给 --actor = 不定, 拒)。
    slots = role_data.get("instances") if isinstance(role_data.get("instances"), dict) else None
    top = {k: v for k, v in role_data.items() if k != "instances"}
    prior_instance = str(top.get("instance") or "").strip()
    if agent_instance and (slots is not None or (slot_by_instance and prior_instance and prior_instance != agent_instance)):
        slots = dict(slots or {})
        if prior_instance and prior_instance not in slots:
            slots[prior_instance] = top
        record = dict(slots.get(agent_instance) or {}) if isinstance(slots.get(agent_instance), dict) else {}
    else:
        record = top  # 验收⑨ 既有合并语义(同一实例重写 / 无实例写入)
    record["role"] = role
    if agent_instance:
        record["instance"] = agent_instance
    if owner_policy_ref:
        record["owner_policy_ref"] = owner_policy_ref
    if harness:
        # AIPOS-F92 件②: 工位 harness {kind, dir}(distribution.schema harness_semantics; 缺 = pi 工位)
        record["harness"] = dict(harness)
    record["enrolled_at"] = iso_z()
    if slots is not None and agent_instance:
        slots[agent_instance] = dict(record)
    role_data = {**record, **({"instances": slots} if slots is not None else {})}
    role_file.write_text(json.dumps(role_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    role_file.chmod(0o644)
    return sorted(role_data.keys())


def write_actor_file(lybra_dir: Path, actor: str) -> None:
    """写入 .lybra/actor 文件(纯文本,单行actor/instance标识)。
    
    DEPRECATED: 仅为向后兼容保留。新代码应使用 write_role_file 的 JSON 格式。
    """
    actor_file = lybra_dir / "actor"
    actor_file.write_text(actor + "\n", encoding="utf-8")
    actor_file.chmod(0o644)


def write_policy_file(lybra_dir: Path, policy: str | None) -> None:
    """写入 .lybra/policy 文件(纯文本,单行policy ref)。
    
    DEPRECATED: 仅为向后兼容保留。新代码应使用 write_role_file 的 JSON 格式。
    
    如果 policy 为 None,不写入(保留现有或不创建)。
    """
    if policy is None:
        return
    
    policy_file = lybra_dir / "policy"
    policy_file.write_text(policy + "\n", encoding="utf-8")
    policy_file.chmod(0o644)


def enroll(
    *,
    code: str | None = None,
    gate_url: str,
    workspace_root: Path,
    policy: str | None = None,
    bootstrap_token: str | None = None,
    verify: bool = False,
    harness_kind: str | None = None,
    harness_dir: Path | None = None,
    landed_host: str | None = None,
    harness_host: str | None = None,
) -> dict[str, Any]:
    """执行完整的 enroll 流程。

    AIPOS-F95 件③b: land 事件带工位位置 host(本机 enroll = 本机主机名 socket.gethostname(); enroll_deliver --ssh 传 ssh 目标),
    与 workstation=<目录> 一起写入 enrollment_log, 供 lybra loop 定位工位(enrollment.workstation_location)。

    AIPOS-F92 件②: harness_kind/harness_dir —— 工位 harness(distribution.schema harness_semantics; 缺省 pi = 既有行为)。
    非 pi harness(如 claude-code 顾问会话): .lybra/role 记 harness {kind, dir}; 不落 .pi 接线; 落盘(+verify)后经同一分发引擎
    (distribution_sync.sync)把声明给本角色该 harness 的件交付到 harness_dir(Claude Code: <会话目录>/.claude/skills/)。
    AIPOS-F129 件②: harness_host —— 会话所在机(codex 顾问可在他机; 声明 harness_host=optional 的 kind 才许给);
    codex 记 {kind, dir: <可空>, host: <可空>}, 他机目录如实记不校验本机存在; 声明给 codex 的件 = 0 时交付零写入。

    Args:
        code: Enrollment code(自包含码 LYBRAENROLL1.* 或旧裸码; 从 owner/advisor 获得)。None = 幂等补铸模式 (AIPOS-C2 大项B):
              只按 config.schema 必填键铸全 connection.json (含 workspace_root), 不动 token。
        gate_url: Gate MCP URL(自包含码可传内嵌地址, 此参数仅作显式覆盖/兼容)
        workspace_root: Workspace root(落配置的目标目录,不需要预先存在; 必须是工位目录 ——
              治理工作区会被拒绝, F23 验收⑧/第九坑)
        policy: Optional policy reference(如未提供,从 gate 返回中提取或不设置)
        bootstrap_token: 旧裸码路径的 transport 凭证(自包含码自动忽略)
        verify: AIPOS-R6S 大项C② — enroll 后立刻用新 token 调一次 gate, 不通即报错并回滚
    
    Returns:
        {
            "ok": bool,
            "operation": "enroll" | "backfill",
            "role": str | None,
            "agent_instance": str | None,
            "fingerprint": str,
            "scopes": list[str],
            "rotated": bool,  # True if replaced existing token
            "workspace_root": str,
            "lybra_dir": str,
            "files_written": list[str],
            "landed": bool | None,  # F23: 落盘确认(grace 窗口关闭); backfill 为 None
            "verify": dict | None,  # AIPOS-R6S 大项C②
        }
    
    Raises:
        RuntimeError: If enrollment fails (含 F9 带路文案)
    
    Note:
        FIX-1: workspace_root 不需要预先存在。Enroll 只需要落 .lybra/ 配置,
        不需要队列结构(队列在 gate 侧)。新机零手工上线。
        F23: 交换与落盘原子 —— 落盘成功才调 land; 落盘抛异常则不 land,
        码留在 grace 窗口内可免费重试(返回同一 token)。
    """
    from tools.aipos_cli.enrollment import decode_self_contained_code
    from tools.aipos_cli.distribution_sync import harness_role_record

    workspace_root = workspace_root.resolve()

    # AIPOS-F92 件② / AIPOS-F129 件②: harness 参数校验(先于任何写盘/兑换, fail-closed); 取舍读 distribution.schema
    # harness_semantics.kinds(唯一判定 harness_role_record: 未知 kind 拒并列出合法值; codex 记 {kind, dir, host})
    try:
        harness_record = harness_role_record(harness_kind, harness_dir, harness_host)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    # F23: 自包含码解析(内嵌 gate 地址优先; 显式 gate_url 参数可覆盖)
    sc = decode_self_contained_code(code) if code is not None else None
    governance_root: str | None = None
    effective_gate_url = gate_url
    if sc is not None:
        governance_root = sc.get("governance_root") or None
        if not (gate_url and gate_url.strip()):
            effective_gate_url = sc["gate_url"]

    # FIX-1: 确保目标目录存在(对空目录新机零手工上线)
    # AIPOS-F54 同族并入③: 目录不存在时自动创建并出声(chris 实撞 cd: No such file or directory)
    created_workspace_dir = not workspace_root.exists()
    workspace_root.mkdir(parents=True, exist_ok=True)

    # AIPOS-F22D: 治理工作区守卫延迟到 exchange 之后(需先知道 role 才能按角色类判定)
    # 工位角色类(executor/auditor 及其自定义角色)维持拒绝; 顾问角色类(planner/advisor)允许
    _gov_workspace = is_governance_workspace(workspace_root, governance_root) if code is not None else False

    # Step 2: 确保 .lybra/ 目录存在
    lybra_dir = ensure_lybra_dir(workspace_root)
    
    role: str | None = None
    agent_instance: str | None = None
    fingerprint = "(none)"
    scopes: list[str] = []
    token_entry: dict[str, Any] | None = None
    token_value: str | None = None
    rotated = False
    landed: bool | None = None
    
    # Step 1: Exchange enrollment code for token (backfill 模式 code=None 则跳过, 不动 token)
    if code is not None:
        try:
            exchange_result = exchange_enrollment_code(effective_gate_url, code, bootstrap_token)
        except RuntimeError as exc:
            raise RuntimeError(f"Enrollment exchange failed: {exc}") from exc
        
        if not exchange_result.get("ok"):
            raise RuntimeError(f"Enrollment exchange returned ok=False: {exchange_result.get('message', 'unknown error')}")
        
        token_entry = exchange_result.get("token_entry")
        if not token_entry:
            raise RuntimeError("No token_entry in exchange response")
        
        role = token_entry.get("role")
        agent_instance = token_entry.get("agent_instance")
        fingerprint = token_entry.get("fingerprint", "(unknown)")
        scopes = token_entry.get("scopes", [])
        token_value = token_entry.get("token")
        
        if not role:
            raise RuntimeError("token_entry missing 'role' field")
        
        # AIPOS-F22D: 治理工作区守卫——按角色类判定(F23⑧ 第九坑防护升级)
        # 工位角色类(executor/auditor)→拒绝; 顾问角色类(planner/advisor)→允许
        # AIPOS-F102 件②: 角色类唯一解析 custom_roles.resolve_role_to_class(解析不到 = 拒, 原「回落角色名自身」退役); 分组读 roles.schema class_groups
        if _gov_workspace:
            from tools.aipos_cli.custom_roles import UnknownRoleClass, resolve_role_to_class, role_classes_in_group

            try:
                _role_class = resolve_role_to_class(role, str(workspace_root), required=True)
            except UnknownRoleClass as exc:
                raise RuntimeError(f"enroll 目标是治理工作区({workspace_root}), {exc}") from exc
            if _role_class not in role_classes_in_group("governance_seat"):
                raise RuntimeError(
                    f"enroll 目标是治理工作区({workspace_root}), 角色 {role}(类={_role_class}) 拒绝落盘 —— "
                    f"工位角色类(executor/auditor)只落工位目录 .lybra/。\n"
                    "下一步: 在工位目录(pi harness 目录)运行, 或用 --workspace 指向工位目录。\n"
                    "可抄示例: lybra roles enroll --code <码> --workspace ~/workstations/my-agent\n"
                    "(顾问角色类 planner/advisor 允许落治理工作区)"
                )
    
    # Step 3: 加载或创建 connection.json (AIPOS-C2 大项B: 铸全 workspace_root)
    # AIPOS-F54-fix1: workspace_root 单源 = governance_root(码内治理根);
    # .lybra/ 物理位置 = harness workspace_root(工位目录); 两者分离。
    connection_data = load_or_create_connection_json(
        lybra_dir, effective_gate_url, workspace_root,
        governance_root=governance_root,
    )
    
    # AIPOS-F54-fix1 ③: lybra_bin —— 指向实际部署位(运行中 bin 优先, 否则探测 .deploy/current);
    # 推导不出则不写(留空会让 /lybra sync 探测, 探测失败时 sync 会带路, 禁静默写错路径)
    # 始终校正(已入册工位重跑 enroll 即补铸/校正, 禁"已有错值则保留")
    from tools.aipos_cli.custom_roles import role_classes_in_group
    from tools.aipos_cli.workstation_wiring import (
        materialize_pi_wiring,
        resolve_deployed_lybra_bin,
        resolve_role_class,
        verify_minimum_bootable_set,
    )
    _bin = resolve_deployed_lybra_bin()
    if _bin:
        connection_data["lybra_bin"] = _bin
    # AIPOS-F54-fix1 ②: governance_root 已在 load_or_create_connection_json 写入(单源)
    # 此处不再重复写(避免两处写入不一致)
    
    # Step 4: Upsert token entry(幂等; backfill 模式 token_entry=None 则不动 token)
    if token_entry is not None:
        rotated = upsert_token_entry(connection_data, token_entry)
    
    # AIPOS-C2 大项B: 铸全校验 —— 缺必填键则失败出声, 不落半成品
    missing = validate_connection_complete(connection_data)
    if missing:
        raise RuntimeError(
            f"enroll 铸全失败: connection.json 缺必填键 {', '.join(missing)} "
            f"(声明见 config.schema#identity_resolution)。不落半成品。"
        )
    
    # Step 5: 写入 connection.json
    write_connection_json(lybra_dir, connection_data)
    files_written = ["connection.json"]
    
    # Step 6: 写入自发现配置文件 (统一JSON格式, 验收⑨合并保留既有键); backfill 模式不动 role/token
    wiring_report: dict[str, Any] | None = None
    policy_derivation: dict[str, Any] | None = None
    if code is not None and role:
        # AIPOS-F54 ②: owner_policy_ref 按角色类从门侧生效信封推导(禁硬编码 policy id)
        from tools.aipos_cli.workstation_wiring import derive_effective_owner_policy_ref
        effective_gov_root = str(connection_data.get("governance_root") or "").strip() or None
        derived_policy, policy_reason = derive_effective_owner_policy_ref(
            effective_gov_root, role=role, agent_instance=agent_instance,
        )
        policy_derivation = {"policy_id": derived_policy, "reason": policy_reason}
        role_class = resolve_role_class(role, token_entry, project_root=workspace_root)
        # AIPOS-F134 件②: 治理席位类(顾问/规划方)按实例分槽(同一治理根多顾问实例并存); 分组读 roles.schema class_groups
        seat_slots = role_class in role_classes_in_group("governance_seat")
        if derived_policy:
            write_role_file(lybra_dir, role, agent_instance, derived_policy, harness=harness_record, slot_by_instance=seat_slots)
            files_written.append("role(含 owner_policy_ref)")
        elif role_class in role_classes_in_group("workstation"):  # AIPOS-F102 件②: 工位类读 roles.schema class_groups
            # 卡面②: 推导不出 → 报错带路, 禁静默留空导致循环起不来(验收⑩)
            raise RuntimeError(
                f"enroll 推导 owner_policy_ref 失败: {policy_reason}。\n"
                "下一步: 请先为该角色铸信封(owner_autonomy_policy, PreAuthorized), 命令示例: \n"
                "  lybra owner-decision ... 铸 owner_autonomy_policy 后重跑 enroll;\n"
                "或顾问代设置: lybra sync 会按生效信封校正该键。"
            )
        else:
            # 非循环角色类(advisor/planner 等): 仅告警不阻断
            write_role_file(lybra_dir, role, agent_instance, None, harness=harness_record, slot_by_instance=seat_slots)
            files_written.append("role(无 owner_policy_ref, 非循环角色类仅告警)")
            policy_derivation["warning"] = True
    elif code is None:
        # backfill 模式: 从既有 .lybra/role 读角色, 补齐接线(修复既有残缺工位)
        # AIPOS-F106 件④: role 只经 ConnectionResolver.resolve_role 读(.lybra/role 唯一读取实现之一; env={} = 只认工位声明层)
        from tools.loop_context import ConnectionResolver

        role = ConnectionResolver.resolve_role(workspace_root=lybra_dir.parent, env={}) or None
    
    # AIPOS-F54 ①: .pi 接线 + AGENTS.md 种子(seed_only 幂等, 已存在跳过不覆盖)
    # AIPOS-F82 件②: 接线目标由 distribution 声明推导(只写目标存在的扩展挂载, 不写的项进 warnings); AGENTS.md = charter_render 渲染物
    if role and harness_record is None:
        role_class = resolve_role_class(role, token_entry, project_root=workspace_root)
        wiring_report = materialize_pi_wiring(workspace_root, role=role, role_class=role_class)
        files_written.append(".pi/接线")

    # F23 验收⑦: 落盘全部成功后才 land(关 grace 窗口, 码彻底消费); land 失败仅告警
    if code is not None:
        landed = land_enrollment_code(
            effective_gate_url,
            code,
            transport_token=(sc["transport_token"] if sc is not None else None),
            landed_detail=f"host={_landed_host(landed_host)} workstation={workspace_root} files={files_written}",
        )
    
    # Step 7 (AIPOS-R6S 大项C②): 可选 --verify — 新 token 调一次 gate, 不通即回滚
    verify_result = None
    if verify:
        if not token_value:
            raise RuntimeError("token_entry missing 'token' — cannot verify")
        ok, detail = verify_token_against_gate(effective_gate_url, token_value)
        verify_result = {"ok": ok, "detail": detail}
        if not ok:
            # 回滚: 移除刚写入的 token(禁静默留坏配置)
            try:
                rollback_data = load_or_create_connection_json(lybra_dir, effective_gate_url, workspace_root, governance_root=governance_root)
                rollback_data["tokens"] = [
                    t for t in rollback_data.get("tokens", [])
                    if not (t.get("agent_instance") == agent_instance or (not agent_instance and t.get("role") == role))
                ]
                write_connection_json(lybra_dir, rollback_data)
            except Exception as rb_exc:
                raise RuntimeError(
                    f"enroll --verify FAILED ({detail}) and rollback also failed: {rb_exc}"
                ) from rb_exc
            raise RuntimeError(f"enroll --verify FAILED: {detail} — token 已回滚, 未留下坏配置")
    
    # AIPOS-F58: 工位私有状态自我保护 —— 把 .lybra/ 凭据路径登记进 .git/info/exclude
    # 防止邻居项目 `git stash -u` 连坐抹掉凭据(不碰 .gitignore, 幂等, 无 git 静默跳过)
    from tools.aipos_cli.git_exclude import collect_enroll_exclude_paths, register_git_exclude
    exclude_paths = collect_enroll_exclude_paths(workspace_root, files_written)
    git_exclude_report = register_git_exclude(workspace_root, exclude_paths)

    # AIPOS-F92 件②: 非 pi harness —— 凭据落盘(+verify)后经同一分发引擎交付声明给本角色该 harness 的件到 harness_dir
    harness_delivery: dict[str, Any] | None = None
    if harness_record is not None and code is not None:
        from tools.aipos_cli.distribution_sync import sync as _distribution_sync

        try:
            harness_delivery = _distribution_sync(harness_root=workspace_root)
        except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
            raise RuntimeError(
                f"凭据已落盘({lybra_dir}), 但 {harness_record['kind']} 件交付失败: {exc.__class__.__name__}: {exc}\n"
                f"出口: lybra sync --harness-root {workspace_root}(凭据保留, 重跑即可; 勿重跑 enroll)"
            ) from exc
        if not harness_delivery.get("ok"):
            raise RuntimeError(
                f"凭据已落盘({lybra_dir}), 但 {harness_record['kind']} 件交付失败: {harness_delivery.get('error')}\n"
                f"出口: lybra sync --harness-root {workspace_root}"
            )
        # AIPOS-F129: 非本机可落 harness(codex 无 dir / 他机 host)交付零写入, 原因由 sync 点名(note)
        _where = harness_record.get("dir") or f"host={harness_record.get('host')}"
        files_written.append(f"{harness_record['kind']} 件 → {_where}({harness_delivery.get('files_fetched')} 个文件)")

    # AIPOS-F54 ⑮: 可启动最小集逐项校验(缺项逐项点名, 禁"少一个键整个起不来但不知道少哪个")
    # AIPOS-F92: 最小集是 pi 工位清单(.pi 接线 / go 扩展), 非 pi harness 不适用
    if harness_record is None:
        bootable_check = verify_minimum_bootable_set(workspace_root)
    else:
        bootable_check = {"ok": True, "checks": [], "missing": [], "skipped": f"harness={harness_record['kind']}: 可启动最小集为 pi 工位清单, 不适用"}
    
    return {
        "ok": True,
        "operation": "backfill" if code is None else "enroll",
        "role": role,
        "agent_instance": agent_instance,
        "fingerprint": fingerprint,
        "scopes": scopes,
        "rotated": rotated,
        "workspace_root": str(workspace_root),
        "lybra_dir": str(lybra_dir),
        "files_written": files_written,
        "landed": landed,
        "verify": verify_result,
        "created_workspace_dir": created_workspace_dir,
        "policy_derivation": policy_derivation,
        "wiring": wiring_report,
        # AIPOS-F82 件②: 未写的接线项/章程逐项点名(禁写悬空、禁落未渲染母本)
        "warnings": list((wiring_report or {}).get("warnings") or []),
        "minimum_bootable_set": bootable_check,
        "harness": harness_record,
        "harness_delivery": ({k: harness_delivery.get(k) for k in ("status", "files_fetched", "manifest_path", "harness", "changes", "note")}
                             if harness_delivery else None),
        "git_exclude": git_exclude_report,
        "next_step": (
            None if code is None else
            (f"上岗完成: {harness_record['kind']} 件已交付到 {harness_record['dir']}; 在该目录起(或重启)会话即加载"
             if harness_record is not None and not (harness_delivery or {}).get("note") else
             f"上岗完成: harness={harness_record['kind']} 的凭据与身份已落 {lybra_dir}; {(harness_delivery or {}).get('note')}"
             if harness_record is not None else
             f"上岗完成: 接着 lybra sync --harness-root {workspace_root} --workspace-root <治理根>, 然后在工位起 pi")
        ),
    }


def main() -> int:
    """CLI entry point for standalone execution."""
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(
        description="AIPOS-R2: lybra enroll — 铸角色凭据+落.lybra自发现配置",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--code", required=False, help="Enrollment code(from owner/advisor); 省略 + --backfill = 幂等补铸模式(不触 token)")
    parser.add_argument("--code-stdin", action="store_true", help="AIPOS-F113: read the enrollment code from stdin (first line) instead of argv — enroll_deliver --ssh sends it this way so the code never appears in the remote command line")
    parser.add_argument("--gate-url", required=True, help="Gate MCP URL (e.g., http://host:<gate-port>)")
    parser.add_argument("--workspace", help="Workspace root(defaults to current directory)")
    parser.add_argument("--policy", help="Optional policy reference")
    parser.add_argument("--bootstrap-token", help="Bootstrap token for HTTP transport auth (any valid token; or set LYBRA_BOOTSTRAP_TOKEN)")
    parser.add_argument("--verify", action="store_true", help="AIPOS-R6S 大项C②: enroll 后立刻用新 token 调一次 gate, 不通即报错并回滚")
    parser.add_argument("--backfill", action="store_true", help="AIPOS-C2 大项B: 幂等补铸模式 —— 只按 config.schema 必填键铸全 connection.json (含 workspace_root), 不动 token")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--landed-host", help="AIPOS-F95: host recorded in the land event (enroll_deliver --ssh passes the ssh target; default = this machine's hostname)")
    
    args = parser.parse_args()

    # AIPOS-F113: --code-stdin —— 注册码从 stdin 读(不进 argv / 不落盘 / 不进环境变量); 与 --code 互斥, 读不到即拒(fail-closed)
    if args.code_stdin:
        if args.code:
            print("Error: --code and --code-stdin are mutually exclusive", file=sys.stderr)
            return 2
        args.code = sys.stdin.readline().strip()
        if not args.code:
            print("Error: --code-stdin given but stdin carried no enrollment code", file=sys.stderr)
            return 2
    
    # AIPOS-F28 大项C: workspace_root 默认取码内治理根(工位场景正确路径)
    # 只有显式传 --workspace 时才覆盖(兼容性)
    if args.workspace:
        # 显式指定 --workspace: 尊重用户意图
        workspace_root = Path(args.workspace).expanduser().resolve()
    else:
        # 未指定 --workspace: 尝试从自包含码提取 governance_root
        from tools.aipos_cli.enrollment import decode_self_contained_code
        code_to_check = None if args.backfill else args.code
        sc = decode_self_contained_code(code_to_check) if code_to_check else None
        if sc and sc.get("governance_root"):
            # 码内包含治理根: 用它作为 workspace_root(工位场景)
            workspace_root = Path(sc["governance_root"]).expanduser().resolve()
            if not args.quiet and not args.json:
                print(f"Using governance_root from code as workspace: {workspace_root}")
        else:
            # 无码或码内无 governance_root: 回退到 cwd(旧行为兼容)
            workspace_root = Path(os.getcwd()).expanduser().resolve()
    
    if not args.quiet and not args.json:
        print(f"Enrolling with gate: {args.gate_url}")
        print(f"Workspace: {workspace_root}")
    
    try:
        result = enroll(
            code=None if args.backfill else args.code,
            gate_url=args.gate_url,
            workspace_root=workspace_root,
            policy=args.policy,
            bootstrap_token=getattr(args, 'bootstrap_token', None),
            verify=bool(getattr(args, 'verify', False)),
            landed_host=getattr(args, 'landed_host', None),
        )
    except RuntimeError as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    
    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.quiet:
        print(f"\n✓ Enrollment successful!")
        print(f"  Role: {result['role']}")
        if result.get('agent_instance'):
            print(f"  Instance: {result['agent_instance']}")
        print(f"  Token fingerprint: {result['fingerprint']}")
        print(f"  Scopes: {', '.join(result['scopes'])}")
        if result['rotated']:
            print(f"  ⟳ Token rotated (replaced existing credential)")
        else:
            print(f"  ✓ New credential registered")
        print(f"\n  Configuration written to: {result['lybra_dir']}/")
        for fname in result['files_written']:
            print(f"    - {fname}")
        print(f"\n⚠ Enrollment code has been consumed and cannot be reused.")
        print(f"⚠ Token is stored with 0600 permissions in connection.json")
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
