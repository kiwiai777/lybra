"""AIPOS-F73B件②测试: PreAuthorized 一阶段协议（只调 dry_run）"""

import tempfile
from pathlib import Path
import json
import pytest
from unittest.mock import patch, MagicMock
from tools.aipos_cli.two_phase_shell_factory import execute_two_phase_verb


def test_f73b_item2_preauthorized_one_phase():
    """件②: PreAuthorized 模式只调 dry_run（门自动放行落记录）"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        # 创建临时 connection.json
        conn_path = workspace / "connection.json"
        conn_data = {
            "mcp": {
                "rpc_url": "http://localhost:7118/mcp"
            },
            "tokens": [
                {
                    "role": "executor",
                    "role_class": "executor",
                    "token": "test-token-exec"
                }
            ]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        # Mock gate client
        with patch("tools.aipos_cli.two_phase_shell_factory.GateClient") as mock_gate_class:
            mock_client = MagicMock()
            mock_gate_class.return_value = mock_client
            
            # Mock dry_run 返回 WARN（PreAuthorized 放行）
            mock_client.call_tool.return_value = {
                "verdict": "WARN",
                "warnings": ["PreAuthorized mode - record landed"],
                "isError": False,
            }
            
            # 执行 PreAuthorized 认领
            args_dict = {
                "task_id": "TEST-PREAUTH",
                "actor": "exec.lybra.test",
                "agent_instance": "exec.lybra.test",
                "autonomy_mode": "PreAuthorized",
                "owner_policy_ref": "policy-123",
            }
            
            exit_code, response = execute_two_phase_verb(
                verb_base="lybra_queue_claim",
                args_dict=args_dict,
                connection_json_path=str(conn_path),
                role="executor",
                json_output=False,
            )
            
            # 验证：只调用了一次工具（dry_run），没有调用 confirm
            assert mock_client.call_tool.call_count == 1
            call_args = mock_client.call_tool.call_args[0]
            assert call_args[0] == "lybra_queue_claim_dry_run"
            
            # 验证：exit_code 为 0（成功）
            assert exit_code == 0


def test_f73b_item2_supervised_two_phase():
    """件②对照组: Supervised 模式仍走两阶段"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        conn_path = workspace / "connection.json"
        conn_data = {
            "mcp": {"rpc_url": "http://localhost:7118/mcp"},
            "tokens": [{"role": "executor", "role_class": "executor", "token": "test-token"}]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        with patch("tools.aipos_cli.two_phase_shell_factory.GateClient") as mock_gate_class:
            mock_client = MagicMock()
            mock_gate_class.return_value = mock_client
            
            # Mock dry_run 返回 token
            mock_client.call_tool.side_effect = [
                {
                    "verdict": "PASS",
                    "dry_run_token": "drt-12345",
                    "isError": False,
                },
                {
                    "verdict": "CONFIRMED",
                    "isError": False,
                }
            ]
            
            args_dict = {
                "task_id": "TEST-SUPERVISED",
                "actor": "exec.lybra.test",
                "agent_instance": "exec.lybra.test",
                "autonomy_mode": "Supervised",
                "owner_policy_ref": "policy-123",
            }
            
            exit_code, response = execute_two_phase_verb(
                verb_base="lybra_queue_claim",
                args_dict=args_dict,
                connection_json_path=str(conn_path),
                role="executor",
                owner_confirmation_literal="OWNER_CONFIRMED",
                json_output=False,
            )
            
            # 验证：调用了两次（dry_run + confirm）
            assert mock_client.call_tool.call_count == 2
            
            # 第一次调用 dry_run
            first_call = mock_client.call_tool.call_args_list[0][0]
            assert first_call[0] == "lybra_queue_claim_dry_run"
            
            # 第二次调用 confirm
            second_call = mock_client.call_tool.call_args_list[1][0]
            assert second_call[0] == "lybra_queue_claim_confirm"
            assert second_call[1]["dry_run_token"] == "drt-12345"
            
            assert exit_code == 0


def test_f73b_item2_preauth_blocked():
    """件②错误场景: PreAuthorized 模式 dry_run 返回 BLOCK"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        conn_path = workspace / "connection.json"
        conn_data = {
            "mcp": {"rpc_url": "http://localhost:7118/mcp"},
            "tokens": [{"role": "executor", "role_class": "executor", "token": "test"}]
        }
        conn_path.write_text(json.dumps(conn_data), encoding="utf-8")
        
        with patch("tools.aipos_cli.two_phase_shell_factory.GateClient") as mock_gate_class:
            mock_client = MagicMock()
            mock_gate_class.return_value = mock_client
            
            # Mock dry_run 返回 BLOCK
            mock_client.call_tool.return_value = {
                "verdict": "BLOCK",
                "blocking_reasons": ["Task already claimed"],
                "isError": False,
            }
            
            args_dict = {
                "task_id": "TEST-BLOCKED",
                "actor": "exec.lybra.test",
                "agent_instance": "exec.lybra.test",
                "autonomy_mode": "PreAuthorized",
                "owner_policy_ref": "policy-123",
            }
            
            exit_code, response = execute_two_phase_verb(
                verb_base="lybra_queue_claim",
                args_dict=args_dict,
                connection_json_path=str(conn_path),
                role="executor",
                json_output=False,
            )
            
            # 验证：只调用一次，exit_code 非零
            assert mock_client.call_tool.call_count == 1
            assert exit_code == 1
            assert response["verdict"] == "BLOCK"
