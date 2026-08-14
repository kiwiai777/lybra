#!/usr/bin/env python3
"""AIPOS-R6K 连接面契约五件验收测试。

测试覆盖:
1. 件①: enroll/enroll-deliver 同机判定→loopback URL
2. 件②: Python GateClient 禁用环境代理
3. 件③: MCP配置生成 loopback优先
4. 件④: 连接失败双路诊断
5. 件⑤: (TS侧)接线债修复,Python侧已有resolvers
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.aipos_cli.enroll_deliver import is_same_host, normalize_gate_url_for_same_host
from tools.aipos_cli.enroll_client import is_same_host as enroll_is_same_host, normalize_gate_url_for_same_host as enroll_normalize
from tools.aipos_cli.confirm_client import GateClient, _diagnose_connection_failure
from tools.aipos_cli.aipos_cli import _normalize_mcp_host_for_config


def test_same_host_detection():
    """件①: 同机判定逻辑正确性"""
    print("测试件①: 同机判定...")
    
    # loopback 直接判定
    assert is_same_host("http://127.0.0.1:7118") == True
    assert is_same_host("http://localhost:7118") == True
    assert is_same_host("http://[::1]:7118") == True
    
    # 本机 hostname 应该解析为同机
    hostname = socket.gethostname()
    local_url = f"http://{hostname}:7118"
    # 这个可能因网络配置不同,允许失败
    try:
        result = is_same_host(local_url)
        print(f"  本机hostname({hostname}): {result}")
    except Exception as e:
        print(f"  本机hostname({hostname}): 跳过 ({e})")
    
    # enroll_client 的实现应该一致
    assert enroll_is_same_host("http://127.0.0.1:7118") == True
    assert enroll_is_same_host("http://localhost:7118") == True
    
    print("  ✓ 同机判定测试通过")


def test_normalize_gate_url():
    """件①: loopback 规范化"""
    print("测试件①: loopback规范化...")
    
    # 同机URL应该规范化为 127.0.0.1
    assert normalize_gate_url_for_same_host("http://127.0.0.1:7118") == "http://127.0.0.1:7118"
    assert normalize_gate_url_for_same_host("http://localhost:7118") == "http://127.0.0.1:7118"
    assert normalize_gate_url_for_same_host("http://localhost:9999") == "http://127.0.0.1:9999"
    
    # enroll_client 的实现应该一致
    assert enroll_normalize("http://127.0.0.1:7118") == "http://127.0.0.1:7118"
    assert enroll_normalize("http://localhost:7118") == "http://127.0.0.1:7118"
    
    print("  ✓ loopback规范化测试通过")


def test_gate_client_proxy_bypass():
    """件②: GateClient 禁用环境代理"""
    print("测试件②: GateClient代理豁免...")
    
    # GateClient 应该使用空的 ProxyHandler
    client = GateClient("http://127.0.0.1:7118", "test-token")
    
    # 检查 _opener 是否存在(使用 build_opener(ProxyHandler({})) 创建)
    assert client._opener is not None, "GateClient 应该有 _opener"
    
    # 关键验证:GateClient 代码中显式使用了 ProxyHandler({})
    # 这会禁用所有环境变量代理(http_proxy/https_proxy等)
    # 验证策略:检查源码中是否包含 ProxyHandler({})
    import inspect
    source = inspect.getsource(GateClient.__init__)
    assert "ProxyHandler({})" in source, "GateClient.__init__ 应该使用 ProxyHandler({})"
    
    print("  ✓ GateClient代理豁免测试通过")


def test_mcp_config_loopback_priority():
    """件③: MCP配置生成 loopback优先"""
    print("测试件③: MCP配置loopback优先...")
    
    # loopback 主机名应该规范化为 127.0.0.1
    assert _normalize_mcp_host_for_config("127.0.0.1") == "127.0.0.1"
    assert _normalize_mcp_host_for_config("localhost") == "127.0.0.1"
    assert _normalize_mcp_host_for_config("0.0.0.0") == "127.0.0.1"
    assert _normalize_mcp_host_for_config("::1") == "127.0.0.1"
    assert _normalize_mcp_host_for_config("") == "127.0.0.1"
    
    # 非 loopback 保持不变
    assert _normalize_mcp_host_for_config("192.168.1.100") == "192.168.1.100"
    assert _normalize_mcp_host_for_config("example.com") == "example.com"
    
    print("  ✓ MCP配置loopback优先测试通过")


def test_connection_diagnosis():
    """件④: 连接失败双路诊断"""
    print("测试件④: 连接失败双路诊断...")
    
    # 测试诊断函数(不实际连接)
    with patch('socket.create_connection') as mock_conn:
        # 模拟: 配置URL不可达,但loopback可达
        def side_effect(addr, timeout=None):
            host, port = addr
            if host == '127.0.0.1':
                # loopback 成功
                mock_sock = Mock()
                return mock_sock
            else:
                # 其他地址失败
                raise ConnectionError("Connection refused")
        
        mock_conn.side_effect = side_effect
        
        diagnosis = _diagnose_connection_failure("http://kiwiai-dev.tail6b5218.ts.net:7118", ConnectionError("test"))
        
        # 诊断应该包含关键信息
        assert "🔍 连接诊断" in diagnosis
        assert "配置URL" in diagnosis
        assert "Loopback可达" in diagnosis
        
        print("  ✓ 连接失败双路诊断测试通过")


def test_loop_context_resolver_priority():
    """件⑤: LoopContext resolver 优先级"""
    print("测试件⑤: LoopContext resolver优先级...")
    
    from tools.loop_context import ConnectionResolver
    from tempfile import TemporaryDirectory
    
    with TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        lybra_dir = workspace / ".lybra"
        lybra_dir.mkdir()
        
        # 写入 connection.json
        conn_data = {
            "config_version": 1,
            "mcp": {
                "rpc_url": "http://127.0.0.1:7118/mcp"
            },
            "tokens": [
                {
                    "role": "executor",
                    "token": "discovered-token-from-lybra"
                }
            ]
        }
        (lybra_dir / "connection.json").write_text(json.dumps(conn_data))
        
        # 测试 gate_url 解析: .lybra 优先于 env
        env = {"LYBRA_GATE_URL": "http://override-from-env:9999/mcp"}
        
        # .lybra 存在时应该使用 .lybra (env 是最低优先级,不覆盖 .lybra)
        url = ConnectionResolver.resolve_gate_url(workspace_root=workspace, env={})
        assert url == "http://127.0.0.1:7118/mcp", f"Expected .lybra discovery, got {url}"
        
        # env 在 .lybra 存在时不生效(env是fallback,不是override)
        url_with_env = ConnectionResolver.resolve_gate_url(workspace_root=workspace, env=env)
        assert url_with_env == "http://127.0.0.1:7118/mcp", f"Expected .lybra priority over env, got {url_with_env}"
        
        # 测试 token 解析: .lybra 优先于 env
        token = ConnectionResolver.resolve_token(workspace_root=workspace, role="executor", env={})
        assert token == "discovered-token-from-lybra"
        
        # env 在 .lybra 存在时不生效
        token_with_env = ConnectionResolver.resolve_token(
            workspace_root=workspace, 
            role="executor", 
            env={"LYBRA_TOKEN": "token-from-env"}
        )
        assert token_with_env == "discovered-token-from-lybra", "Expected .lybra priority over env"
        
        # 测试 env 作为 fallback: .lybra 不存在时才用 env
        empty_workspace = Path(tmpdir) / "empty"
        empty_workspace.mkdir()
        
        url_fallback = ConnectionResolver.resolve_gate_url(workspace_root=empty_workspace, env=env)
        assert url_fallback == "http://override-from-env:9999/mcp", f"Expected env fallback, got {url_fallback}"
        
        print("  ✓ LoopContext resolver优先级测试通过")


if __name__ == "__main__":
    print("=" * 60)
    print("AIPOS-R6K 连接面契约五件验收测试")
    print("=" * 60)
    print()
    
    try:
        test_same_host_detection()
        test_normalize_gate_url()
        test_gate_client_proxy_bypass()
        test_mcp_config_loopback_priority()
        test_connection_diagnosis()
        test_loop_context_resolver_priority()
        
        print()
        print("=" * 60)
        print("✅ 全部测试通过")
        print("=" * 60)
        sys.exit(0)
    except AssertionError as e:
        print()
        print("=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        sys.exit(1)
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 60)
        sys.exit(1)
