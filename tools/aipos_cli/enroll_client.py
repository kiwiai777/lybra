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
from pathlib import Path
from typing import Any

try:
    import urllib.request
    import urllib.error
except ImportError:
    raise RuntimeError("This script requires Python 3.x with urllib")


def exchange_enrollment_code(gate_url: str, code: str, bootstrap_token: str | None = None) -> dict[str, Any]:
    """调用 gate 的 lybra_roles_enroll_exchange MCP 动词兑换 enrollment code。
    
    Args:
        gate_url: Gate MCP URL (e.g., http://host:<gate-port>)
        code: Enrollment code
        bootstrap_token: Optional bootstrap token for HTTP transport auth.
                        If not provided, tries LYBRA_BOOTSTRAP_TOKEN env var.
                        Note: enrollment exchange itself is "public" (no scope required),
                        but HTTP transport layer requires *some* valid bearer token.
    
    Returns:
        MCP response dict with token_entry
    
    Raises:
        RuntimeError: If exchange fails
    """
    # Resolve bootstrap token: explicit > env > error
    if not bootstrap_token:
        bootstrap_token = os.environ.get("LYBRA_BOOTSTRAP_TOKEN", "").strip()
    if not bootstrap_token:
        raise RuntimeError(
            "Bootstrap token required for HTTP transport authentication.\n"
            "Provide via --bootstrap-token or set LYBRA_BOOTSTRAP_TOKEN env var.\n"
            "Note: any valid token works (enrollment code is the real auth)."
        )
    
    url = f"{gate_url.rstrip('/')}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lybra_roles_enroll_exchange",
            "arguments": {"code": code}
        }
    }
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {bootstrap_token}',
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to connect to gate: {e.reason}")
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
    if "structuredContent" in mcp_result:
        return mcp_result["structuredContent"]
    
    # Fallback: try to parse from content[0].text (legacy format)
    if "content" in mcp_result and mcp_result["content"]:
        text_content = mcp_result["content"][0].get("text", "")
        if text_content:
            try:
                return json.loads(text_content)
            except json.JSONDecodeError:
                pass
    
    raise RuntimeError(f"Cannot parse MCP response: {json.dumps(mcp_result)[:200]}")


def ensure_lybra_dir(workspace_root: Path) -> Path:
    """确保 .lybra/ 目录存在,返回路径。"""
    lybra_dir = workspace_root / ".lybra"
    lybra_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return lybra_dir


def load_or_create_connection_json(lybra_dir: Path, gate_url: str) -> dict[str, Any]:
    """加载现有 connection.json 或创建新的。
    
    幂等:如果已存在,保留现有结构(board/mcp/其他 tokens);
    如果不存在,创建最小结构。
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
    if "mcp" not in data:
        mcp_url = gate_url if gate_url.endswith("/mcp") else f"{gate_url}/mcp"
        data["mcp"] = {
            "rpc_url": mcp_url,
        }
    
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


def write_role_file(lybra_dir: Path, role: str) -> None:
    """写入 .lybra/role 文件(纯文本,单行角色名)。"""
    role_file = lybra_dir / "role"
    role_file.write_text(role + "\n", encoding="utf-8")
    role_file.chmod(0o644)


def write_actor_file(lybra_dir: Path, actor: str) -> None:
    """写入 .lybra/actor 文件(纯文本,单行 actor/instance 标识)。"""
    actor_file = lybra_dir / "actor"
    actor_file.write_text(actor + "\n", encoding="utf-8")
    actor_file.chmod(0o644)


def write_policy_file(lybra_dir: Path, policy: str | None) -> None:
    """写入 .lybra/policy 文件(纯文本,单行 policy ref)。
    
    如果 policy 为 None,不写入(保留现有或不创建)。
    """
    if policy is None:
        return
    
    policy_file = lybra_dir / "policy"
    policy_file.write_text(policy + "\n", encoding="utf-8")
    policy_file.chmod(0o644)


def enroll(
    *,
    code: str,
    gate_url: str,
    workspace_root: Path,
    policy: str | None = None,
    bootstrap_token: str | None = None,
) -> dict[str, Any]:
    """执行完整的 enroll 流程。
    
    Args:
        code: Enrollment code(从 owner/advisor 获得)
        gate_url: Gate MCP URL
        workspace_root: Workspace root(落配置的目标目录,不需要预先存在)
        policy: Optional policy reference(如未提供,从 gate 返回中提取或不设置)
        bootstrap_token: Optional bootstrap token for HTTP transport auth(any valid token)
    
    Returns:
        {
            "ok": bool,
            "operation": "enroll",
            "role": str,
            "agent_instance": str | None,
            "fingerprint": str,
            "scopes": list[str],
            "rotated": bool,  # True if replaced existing token
            "workspace_root": str,
            "lybra_dir": str,
            "files_written": list[str],
        }
    
    Raises:
        RuntimeError: If enrollment fails
    
    Note:
        FIX-1: workspace_root 不需要预先存在。Enroll 只需要落 .lybra/ 配置,
        不需要队列结构(队列在 gate 侧)。新机零手工上线。
    """
    workspace_root = workspace_root.resolve()
    
    # FIX-1: 确保 workspace_root 存在(对空目录新机零手工上线)
    workspace_root.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Exchange enrollment code for token
    try:
        exchange_result = exchange_enrollment_code(gate_url, code, bootstrap_token)
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
    
    if not role:
        raise RuntimeError("token_entry missing 'role' field")
    
    # Step 2: 确保 .lybra/ 目录存在
    lybra_dir = ensure_lybra_dir(workspace_root)
    
    # Step 3: 加载或创建 connection.json
    connection_data = load_or_create_connection_json(lybra_dir, gate_url)
    
    # Step 4: Upsert token entry(幂等)
    rotated = upsert_token_entry(connection_data, token_entry)
    
    # Step 5: 写入 connection.json
    write_connection_json(lybra_dir, connection_data)
    files_written = ["connection.json"]
    
    # Step 6: 写入自发现配置文件
    write_role_file(lybra_dir, role)
    files_written.append("role")
    
    if agent_instance:
        write_actor_file(lybra_dir, agent_instance)
        files_written.append("actor")
    
    write_policy_file(lybra_dir, policy)
    if policy:
        files_written.append("policy")
    
    return {
        "ok": True,
        "operation": "enroll",
        "role": role,
        "agent_instance": agent_instance,
        "fingerprint": fingerprint,
        "scopes": scopes,
        "rotated": rotated,
        "workspace_root": str(workspace_root),
        "lybra_dir": str(lybra_dir),
        "files_written": files_written,
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
    parser.add_argument("--code", required=True, help="Enrollment code(from owner/advisor)")
    parser.add_argument("--gate-url", required=True, help="Gate MCP URL (e.g., http://host:<gate-port>)")
    parser.add_argument("--workspace", help="Workspace root(defaults to current directory)")
    parser.add_argument("--policy", help="Optional policy reference")
    parser.add_argument("--bootstrap-token", help="Bootstrap token for HTTP transport auth (any valid token; or set LYBRA_BOOTSTRAP_TOKEN)")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    
    args = parser.parse_args()
    
    workspace_root = Path(args.workspace or os.getcwd()).expanduser().resolve()
    
    if not args.quiet and not args.json:
        print(f"Enrolling with gate: {args.gate_url}")
        print(f"Workspace: {workspace_root}")
    
    try:
        result = enroll(
            code=args.code,
            gate_url=args.gate_url,
            workspace_root=workspace_root,
            policy=args.policy,
            bootstrap_token=getattr(args, 'bootstrap_token', None),
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
