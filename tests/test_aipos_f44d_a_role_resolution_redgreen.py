#!/usr/bin/env python3
"""
AIPOS-F44D-A 先红后绿: 用自定义角色连接文件验证角色解析

测试策略: 
1. 红测试: 模拟旧代码硬编码 role="executor"，自定义角色连接文件报错
2. 绿测试: 使用新的 resolve_role_from_connection，自定义角色成功解析
"""
import json
import tempfile
from pathlib import Path


def test_red_hardcoded_executor():
    """红测试: 硬编码 role='executor' 在自定义角色连接文件下报错"""
    
    # 创建自定义角色连接文件 (hbj-coder)
    with tempfile.TemporaryDirectory() as tmpdir:
        conn_path = Path(tmpdir) / "connection.json"
        conn_data = {
            "tokens": [
                {
                    "role": "hbj-coder",
                    "agent_instance": "code.hbj.local",
                    "token": "test-token-123",
                    "scopes": ["queue_claim", "queue_return"]
                }
            ]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        # 模拟旧代码: 硬编码 load_owner_token(role="executor")
        from tools.aipos_cli.confirm_client import load_owner_token
        
        try:
            load_owner_token(connection_json=str(conn_path), role="executor")
            assert False, "应该抛出 ValueError (role 'executor' not found)"
        except ValueError as exc:
            error_msg = str(exc)
            assert "executor" in error_msg, f"错误信息应包含 'executor'，实际: {error_msg}"
            assert "not found" in error_msg, f"错误信息应包含 'not found'，实际: {error_msg}"
            print(f"✓ 红测试通过: 硬编码 'executor' 报错: {error_msg}")


def test_green_resolve_custom_role():
    """绿测试: resolve_role_from_connection 成功解析自定义角色"""
    
    # 创建自定义角色连接文件 + roles.schema.yaml
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # 连接文件
        conn_path = tmpdir / "connection.json"
        conn_data = {
            "tokens": [
                {
                    "role": "hbj-coder",
                    "agent_instance": "code.hbj.local",
                    "token": "test-token-123",
                    "scopes": ["queue_claim", "queue_return"]
                }
            ]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        # roles.schema.yaml (注册表)
        schema_dir = tmpdir / "0_ontology" / "schemas"
        schema_dir.mkdir(parents=True)
        schema_path = schema_dir / "roles.schema.yaml"
        schema_content = """roles:
  - role: hbj-coder
    class: executor
    description: Chris project custom executor
  - role: hbj-auditor
    class: auditor
    description: Chris project custom auditor
"""
        schema_path.write_text(schema_content, encoding="utf-8")
        
        # 测试新代码: resolve_role_from_connection
        from tools.aipos_cli.two_phase_shell_factory import resolve_role_from_connection
        
        resolved_role = resolve_role_from_connection(
            connection_json_path=str(conn_path),
            required_role_class="executor",
            repo_root=tmpdir,
        )
        
        assert resolved_role == "hbj-coder", f"应该解析为 'hbj-coder'，实际: {resolved_role}"
        print(f"✓ 绿测试通过: 解析到自定义角色 '{resolved_role}'")


def test_green_standard_roles_backward_compatible():
    """绿测试: 标准角色 (executor/auditor) 向后兼容"""
    
    # 创建标准角色连接文件（无 roles.schema.yaml）
    with tempfile.TemporaryDirectory() as tmpdir:
        conn_path = Path(tmpdir) / "connection.json"
        conn_data = {
            "tokens": [
                {
                    "role": "executor",
                    "agent_instance": "exec.lybra.local",
                    "token": "test-token-456",
                }
            ]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        # 测试: 无注册表时降级为名字匹配
        from tools.aipos_cli.two_phase_shell_factory import resolve_role_from_connection
        
        resolved_role = resolve_role_from_connection(
            connection_json_path=str(conn_path),
            required_role_class="executor",
            repo_root=None,  # 无 repo_root，无法读取注册表
        )
        
        assert resolved_role == "executor", f"应该解析为 'executor'，实际: {resolved_role}"
        print(f"✓ 绿测试通过: 标准角色向后兼容 '{resolved_role}'")


if __name__ == "__main__":
    print("=== AIPOS-F44D-A 先红后绿测试 ===")
    test_red_hardcoded_executor()
    test_green_resolve_custom_role()
    test_green_standard_roles_backward_compatible()
    print("✓ AIPOS-F44D-A 先红后绿测试全部通过")
