#!/usr/bin/env python3
"""AIPOS-R2: lybra enroll — 一条命令铸角色凭据+落.lybra自发现配置(新机/新角色零手工上线)

设计权威: LOOP-REDESIGN v2 §4 (gate分发器) + §7 R2

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
        except Exception:
            pass
        
        # 判定交集
        return bool(gate_ips & local_ips)
    except Exception:
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
    port = parsed.port or 7118
    return f"http://127.0.0.1:{port}"


def verify_token_against_gate(gate_url: str, token: str, *, timeout: int = 15) -> tuple[bool, str]:
    """AIPOS-R6S 大项C②: enroll --verify — 用新铸 token 立刻调一次 gate, 验真通。

    调一个只读工具 (lybra_gate_version), 成功 → (True, detail); 失败(401/拒)→ (False, detail)。
    """
    url = f"{gate_url.rstrip('/')}/mcp"
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
    inner_code = code
    if sc is not None:
        # F23: 自包含码 —— 内嵌运输凭证即 transport 认证(bootstrap 要求删除)
        bootstrap_token = sc["transport_token"]
        inner_code = sc["code"]
    elif not bootstrap_token:
        bootstrap_token = os.environ.get("LYBRA_BOOTSTRAP_TOKEN", "").strip()
    if not bootstrap_token:
        raise RuntimeError(
            "Bootstrap token required for HTTP transport authentication (legacy plain code).\n"
            "Provide via --bootstrap-token or set LYBRA_BOOTSTRAP_TOKEN env var, or better: use a "
            "self-contained code (LYBRAENROLL1.*) from `lybra roles enroll-code` — 码即运输认证, 无需 bootstrap。\n"
            "Example: lybra roles enroll --code LYBRAENROLL1.<base64> --workspace ~/my-workstation"
        )
    
    url = f"{gate_url.rstrip('/')}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lybra_roles_enroll_exchange",
            "arguments": {"code": inner_code}
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
        err_code = str(parsed.get("error_code") or "")
        raise RuntimeError(
            f"Enrollment exchange returned ok=False: {reason}"
            + (f" (error_code={err_code})" if err_code else "")
            + (f"\n下一步: {next_step}" if next_step else "")
        )
    return parsed


def ensure_lybra_dir(workspace_root: Path) -> Path:
    """确保 .lybra/ 目录存在,返回路径。"""
    lybra_dir = workspace_root / ".lybra"
    lybra_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return lybra_dir


def load_or_create_connection_json(lybra_dir: Path, gate_url: str, workspace_root: Path | None = None) -> dict[str, Any]:
    """加载现有 connection.json 或创建新的。
    
    幂等:如果已存在,保留现有结构(board/mcp/其他 tokens);
    如果不存在,创建最小结构。
    
    AIPOS-C2 大项B: 铸全 workspace_root (config.schema 必填键)。
    已入册工位重跑 enroll 即补全; 已有正确值则保留 (幂等, 不动 token)。
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
            mcp_url = normalized_gate_url if normalized_gate_url.endswith("/mcp") else f"{normalized_gate_url}/mcp"
            data["mcp"] = {
                "rpc_url": mcp_url,
            }
        else:
            # 更新现有 mcp.rpc_url (幂等:如已存在也更新为规范化 URL)
            mcp_url = normalized_gate_url if normalized_gate_url.endswith("/mcp") else f"{normalized_gate_url}/mcp"
            data["mcp"]["rpc_url"] = mcp_url
    
    # AIPOS-C2 大项B: 铸全 workspace_root (config.schema 必填键, 顾问手补的授权例外就此退役)。
    # 已有正确值则保留 (幂等补铸, 不覆盖), 缺则写入。
    if workspace_root is not None:
        existing_root = data.get("workspace_root")
        if not existing_root:
            data["workspace_root"] = str(workspace_root)
    
    # 确保 config_version 存在
    if "config_version" not in data:
        data["config_version"] = 1
    
    return data


def upsert_token_entry(connection_data: dict[str, Any], token_entry: dict[str, Any]) -> bool:
    """将 token entry 插入或更新到 connection.json tokens[] 中。
    
    幂等:如果已存在同 agent_instance 或同 role(无 instance)的 token,替换;
    否则追加。
    
    Returns:
        True if rotated (replaced existing), False if new
    """
    tokens = connection_data["tokens"]
    agent_instance = token_entry.get("agent_instance")
    role = token_entry.get("role")
    
    # 查找匹配的现有 token
    matched_idx = None
    for idx, existing in enumerate(tokens):
        # 优先匹配 agent_instance
        if agent_instance and existing.get("agent_instance") == agent_instance:
            matched_idx = idx
            break
        # 其次匹配 role(仅当两者都无 agent_instance)
        if not agent_instance and not existing.get("agent_instance") and existing.get("role") == role:
            matched_idx = idx
            break
    
    if matched_idx is not None:
        # 轮换:替换现有 token
        tokens[matched_idx] = token_entry
        return True
    else:
        # 新增
        tokens.append(token_entry)
        return False


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
    
    返回缺失键列表 (空 = 完整)。缺键 = enroll 失败出声, 不落半成品。
    """
    missing: list[str] = []
    if not connection_data.get("workspace_root"):
        missing.append("workspace_root")
    mcp = connection_data.get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("rpc_url"):
        missing.append("mcp.rpc_url")
    if not isinstance(connection_data.get("tokens"), list):
        missing.append("tokens")
    if "config_version" not in connection_data:
        missing.append("config_version")
    return missing


def is_governance_workspace(path: Path, governance_root: str | None = None) -> bool:
    """F23 验收⑧/第九坑防护: 判定目标是否治理工作区(enroll 禁落治理仓)。

    判据(任一命中即治理工作区):
    ① 目标路径绝对化后 == 自包含码内嵌的 governance_root
    ② 目标下存在 5_tasks/queue 目录(治理仓结构签名)
    工位目录(pi harness 目录)不含这些结构 —— 历史实录: 误写把治理仓 .lybra/role
    污染成 auditor 身份(第九坑)。
    """
    target = Path(path).resolve()
    if governance_root:
        try:
            if target == Path(governance_root).expanduser().resolve():
                return True
        except (OSError, RuntimeError):
            pass
    return (target / "5_tasks" / "queue").is_dir()


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
    url = f"{gate_url.rstrip('/')}/mcp"
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


def write_role_file(lybra_dir: Path, role: str, agent_instance: str | None = None, owner_policy_ref: str | None = None) -> list[str]:
    """写入 .lybra/role 文件(统一JSON格式,AIPOS-R6H靶②)。
    
    AIPOS-F23 验收⑨: 合并保留既有键 —— 禁整文件覆盖。既有 owner_policy_ref 等键
    若新值未提供则原样保留; 新值提供则覆盖。返回实际写入的键清单。
    
    Args:
        lybra_dir: .lybra目录路径
        role: 角色名
        agent_instance: agent_instance(可选)
        owner_policy_ref: owner策略引用(可选)
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
    role_data["role"] = role
    if agent_instance:
        role_data["instance"] = agent_instance
    if owner_policy_ref:
        role_data["owner_policy_ref"] = owner_policy_ref
    role_data["enrolled_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
) -> dict[str, Any]:
    """执行完整的 enroll 流程。
    
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

    workspace_root = workspace_root.resolve()

    # F23: 自包含码解析(内嵌 gate 地址优先; 显式 gate_url 参数可覆盖)
    sc = decode_self_contained_code(code) if code is not None else None
    governance_root: str | None = None
    effective_gate_url = gate_url
    if sc is not None:
        governance_root = sc.get("governance_root") or None
        if not (gate_url and gate_url.strip()):
            effective_gate_url = sc["gate_url"]

    # FIX-1: 确保 workspace_root 存在(对空目录新机零手工上线)
    workspace_root.mkdir(parents=True, exist_ok=True)

    # F23 验收⑧/第九坑: enroll 落盘只有一个目标 —— 工位 .lybra/; 治理工作区拒绝
    if code is not None and is_governance_workspace(workspace_root, governance_root):
        raise RuntimeError(
            f"enroll 目标是治理工作区({workspace_root}), 拒绝落盘 —— enroll 只落工位目录 .lybra/。\n"
            "下一步: 在工位目录(pi harness 目录)运行, 或用 --workspace 指向工位目录。\n"
            "可抄示例: lybra roles enroll --code <码> --workspace ~/workstations/my-agent"
        )

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
    
    # Step 3: 加载或创建 connection.json (AIPOS-C2 大项B: 铸全 workspace_root)
    connection_data = load_or_create_connection_json(lybra_dir, effective_gate_url, workspace_root)
    
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
    if code is not None and role:
        write_role_file(lybra_dir, role, agent_instance, policy)
        files_written.append("role")

    # F23 验收⑦: 落盘全部成功后才 land(关 grace 窗口, 码彻底消费); land 失败仅告警
    if code is not None:
        landed = land_enrollment_code(
            effective_gate_url,
            code,
            transport_token=(sc["transport_token"] if sc is not None else None),
            landed_detail=f"workstation={workspace_root} files={files_written}",
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
                rollback_data = load_or_create_connection_json(lybra_dir, effective_gate_url, workspace_root)
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
        "next_step": "上岗完成: 接着 /lybra sync 然后 /reload" if code is not None else None,
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
    parser.add_argument("--gate-url", required=True, help="Gate MCP URL (e.g., http://host:<gate-port>)")
    parser.add_argument("--workspace", help="Workspace root(defaults to current directory)")
    parser.add_argument("--policy", help="Optional policy reference")
    parser.add_argument("--bootstrap-token", help="Bootstrap token for HTTP transport auth (any valid token; or set LYBRA_BOOTSTRAP_TOKEN)")
    parser.add_argument("--verify", action="store_true", help="AIPOS-R6S 大项C②: enroll 后立刻用新 token 调一次 gate, 不通即报错并回滚")
    parser.add_argument("--backfill", action="store_true", help="AIPOS-C2 大项B: 幂等补铸模式 —— 只按 config.schema 必填键铸全 connection.json (含 workspace_root), 不动 token")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    
    args = parser.parse_args()
    
    workspace_root = Path(args.workspace or os.getcwd()).expanduser().resolve()
    
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
