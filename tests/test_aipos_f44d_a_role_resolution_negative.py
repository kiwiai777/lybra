#!/usr/bin/env python3
"""
AIPOS-F44D-A 负夹具: 无匹配角色时报错带路

测试策略: 连接文件内无匹配角色时，报错应点名实有角色与期望角色类
"""
import json
import tempfile
from pathlib import Path


def test_no_matching_role_error_guidance():
    """负夹具: 无匹配角色时报错带路（点名实有角色与期望角色）"""
    
    # 创建连接文件（只有 auditor 角色，无 executor 角色）
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        conn_path = tmpdir / "connection.json"
        conn_data = {
            "tokens": [
                {
                    "role": "custom-auditor",
                    "agent_instance": "audit.project.local",
                    "token": "test-token-789",
                }
            ]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        # roles.schema.yaml
        schema_dir = tmpdir / "0_ontology" / "schemas"
        schema_dir.mkdir(parents=True)
        schema_path = schema_dir / "roles.schema.yaml"
        schema_content = """roles:
  - role: custom-auditor
    class: auditor
    description: Custom auditor role
"""
        schema_path.write_text(schema_content, encoding="utf-8")
        
        # 测试: 请求 executor 角色但连接文件只有 auditor
        from tools.aipos_cli.two_phase_shell_factory import resolve_role_from_connection
        
        try:
            resolve_role_from_connection(
                connection_json_path=str(conn_path),
                required_role_class="executor",
                repo_root=tmpdir,
            )
            assert False, "应该抛出 ValueError (无匹配的 executor 角色)"
        except ValueError as exc:
            error_msg = str(exc)
            
            # 验证错误信息包含关键要素
            assert "executor" in error_msg, f"错误信息应包含期望角色类 'executor'，实际: {error_msg}"
            assert "custom-auditor" in error_msg, f"错误信息应列出实有角色 'custom-auditor'，实际: {error_msg}"
            assert "Available roles" in error_msg, f"错误信息应提示可用角色，实际: {error_msg}"
            
            print(f"✓ 负夹具通过: 报错带路")
            print(f"  错误信息: {error_msg}")


def test_empty_tokens_error():
    """负夹具: 连接文件无 tokens 时报错"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        conn_path = Path(tmpdir) / "connection.json"
        conn_data = {"tokens": []}
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        from tools.aipos_cli.two_phase_shell_factory import resolve_role_from_connection
        
        try:
            resolve_role_from_connection(
                connection_json_path=str(conn_path),
                required_role_class="executor",
                repo_root=None,
            )
            assert False, "应该抛出 ValueError (无 tokens)"
        except ValueError as exc:
            error_msg = str(exc)
            assert "no tokens" in error_msg.lower(), f"错误信息应提示无 tokens，实际: {error_msg}"
            print(f"✓ 负夹具通过: 空 tokens 报错")


def test_invalid_json_error():
    """负夹具: connection.json 格式错误时报错"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        conn_path = Path(tmpdir) / "connection.json"
        conn_path.write_text("{ invalid json", encoding="utf-8")
        
        from tools.aipos_cli.two_phase_shell_factory import resolve_role_from_connection
        
        try:
            resolve_role_from_connection(
                connection_json_path=str(conn_path),
                required_role_class="executor",
                repo_root=None,
            )
            assert False, "应该抛出 ValueError (JSON 格式错误)"
        except ValueError as exc:
            error_msg = str(exc)
            assert "cannot read connection.json" in error_msg, f"错误信息应提示无法读取，实际: {error_msg}"
            print(f"✓ 负夹具通过: JSON 格式错误报错")


if __name__ == "__main__":
    print("=== AIPOS-F44D-A 负夹具测试 ===")
    test_no_matching_role_error_guidance()
    test_empty_tokens_error()
    test_invalid_json_error()
    print("✓ AIPOS-F44D-A 负夹具测试全部通过")
