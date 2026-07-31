"""
AIPOS-286 契约测试：跨机感知（服务端位置注入 + 第0步 + SSH提醒 + i18n双语）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web.board.app import _get_server_location_info, _get_runtime_status_route


class TestAIPOS286ServerLocation:
    """AIPOS-286: 服务端位置信息注入与跨机感知"""

    def test_get_server_location_info_returns_hostname_and_ip(self):
        """S1.1: _get_server_location_info() 返回 hostname 和 ip 字段"""
        result = _get_server_location_info()
        if result is None:
            # Graceful degradation acceptable
            pytest.skip("_get_server_location_info returned None (acceptable fallback)")
        assert isinstance(result, dict), "Should return dict"
        assert "hostname" in result, "Should contain hostname"
        assert "ip" in result, "Should contain ip"
        assert "note" in result, "Should contain note"
        assert isinstance(result["hostname"], str), "hostname should be string"
        assert isinstance(result["ip"], str), "ip should be string"
        assert "AIPOS-286" in result["note"], "note should reference AIPOS-286"

    def test_runtime_status_includes_server_location(self, tmp_path):
        """S1.2: runtime-status API 响应包含 server_location 字段"""
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks").mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir()

        result = _get_runtime_status_route(
            params={"workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None,
        )
        assert result["ok"] is True, "Response should be ok"
        assert "data" in result, "Response should have data"
        data = result["data"]
        assert "server_location" in data, "data should contain server_location"
        # server_location can be None (graceful degradation) or dict
        if data["server_location"] is not None:
            assert isinstance(data["server_location"], dict), "server_location should be dict or None"
            assert "hostname" in data["server_location"], "server_location should have hostname"
            assert "ip" in data["server_location"], "server_location should have ip"
            assert "note" in data["server_location"], "server_location should have note"

    @patch('web.board.app.socket.gethostname')
    @patch('web.board.app.socket.socket')
    def test_runtime_status_graceful_fallback_on_location_failure(self, mock_socket_class, mock_gethostname, tmp_path):
        """S1.3: 检测失败时优雅降级（server_location 为 None）"""
        # Force socket operations to fail
        mock_gethostname.side_effect = Exception("Mock hostname failure")
        
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks").mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir()

        result = _get_runtime_status_route(
            params={"workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None,
        )
        assert result["ok"] is True, "Response should still be ok on location failure"
        data = result["data"]
        assert "server_location" in data, "server_location field should exist"
        # Graceful degradation: should be None
        assert data["server_location"] is None, "server_location should be None on failure"

    def test_advisor_prompt_structure_includes_server_info(self):
        """S2.1: 提示词模板包含服务端位置变量与第0步(AIPOS-286 FIX-2: server-side generation)"""
        from pathlib import Path
        
        # AIPOS-286 FIX-2: Prompt now generated server-side via _generate_text().
        # Check that the server-side templates include required fields.
        from web.board.app import _I18N_TEMPLATES
        
        zh_template = _I18N_TEMPLATES["zh"]["advisor_prompt"]
        en_template = _I18N_TEMPLATES["en"]["advisor_prompt"]
        
        # Check for server location variables in templates
        assert "{server_hostname}" in zh_template, "zh template should use server_hostname variable"
        assert "{server_ip}" in zh_template, "zh template should use server_ip variable"
        assert "{server_hostname}" in en_template, "en template should use server_hostname variable"
        assert "{server_ip}" in en_template, "en template should use server_ip variable"
        
        # Check for step-0 content
        assert "第 0 步" in zh_template, "zh template should have step-0 header"
        assert "Step 0" in en_template, "en template should have step-0 header"
        assert "同机确认" in zh_template or "连通性检测" in zh_template, "zh template should mention connectivity check"
        assert "connectivity" in en_template or "Same-machine" in en_template, "en template should mention connectivity check"
        assert "AIPOS-286" in zh_template, "zh template should mark step-0 as AIPOS-286"
        assert "AIPOS-286" in en_template, "en template should mark step-0 as AIPOS-286"
        assert "curl" in zh_template or "health" in zh_template, "zh template should have health check example"
        assert "curl" in en_template or "health" in en_template, "en template should have health check example"
        assert "block-and-report" in zh_template, "zh template should mention block-and-report"
        assert "block-and-report" in en_template, "en template should mention block-and-report"

    def test_i18n_includes_ssh_reminder_translations(self):
        """S4.1: i18n 包含双语 SSH 提醒键值"""
        i18n_path = REPO_ROOT / "web" / "board" / "static" / "i18n.js"
        assert i18n_path.exists(), "i18n.js should exist"
        
        content = i18n_path.read_text(encoding='utf-8')
        
        # Check zh translations
        assert "'onboarding.step1_title'" in content, "Should have step1_title key"
        assert "'onboarding.step1_body'" in content, "Should have step1_body key"
        assert "'onboarding.copy_prompt'" in content, "Should have copy_prompt key"
        assert "'onboarding.ssh_reminder'" in content, "Should have ssh_reminder key"
        assert "'onboarding.ssh_reminder_text'" in content, "Should have ssh_reminder_text key"
        
        # Check content (zh)
        assert "连接你的顾问 Agent" in content, "Should have Chinese step1_title"
        assert "跨机接入提醒" in content, "Should have Chinese ssh_reminder"
        
        # Check content (en)
        assert "Connect Your Advisor Agent" in content, "Should have English step1_title"
        assert "Cross-Machine Setup" in content, "Should have English ssh_reminder"

    def test_prompt_includes_gate_url_from_runtime_status(self):
        """S2.2: Gate URL 从 runtime-status API 动态取得（非硬编码）(AIPOS-286 FIX-2: server-side)"""
        # AIPOS-286 FIX-2: Gate URL is now fetched server-side by _generate_advisor_prompt_route
        # and injected into the template. Check that the API route does this.
        from web.board.app import _generate_advisor_prompt_route
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = Path(tmpdir) / "test_workspace"
            workspace_root.mkdir()
            (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
            
            # Create connection.json to provide gate URL
            lybra_dir = workspace_root / ".lybra"
            lybra_dir.mkdir()
            connection_json = lybra_dir / "connection.json"
            connection_json.write_text(json.dumps({
                "mcp": {"rpc_url": "http://custom-gate:9999/mcp"},
                "board": {"url": "http://custom-board:8888"}
            }), encoding="utf-8")
            
            result = _generate_advisor_prompt_route(
                {"locale": ["zh"]},
                repo_root=workspace_root,
                board_config_path=None
            )
            
            assert result["ok"] is True, "Should succeed"
            prompt = result["data"]["prompt"]
            gate_url = result["data"]["gate_url"]
            
            # Should use custom gate URL from connection.json
            assert "http://custom-gate:9999" in gate_url, "Should load gate URL from connection.json"
            assert "http://custom-gate:9999" in prompt, "Prompt should contain custom gate URL"

    def test_step_0_before_mcp_config(self):
        """S2.3: 第0步在 MCP 配置段之前（顺序断言）(AIPOS-286 FIX-2: server-side template)"""
        # AIPOS-286 FIX-2: Templates are now server-side. Check template ordering.
        from web.board.app import _I18N_TEMPLATES
        
        zh_template = _I18N_TEMPLATES["zh"]["advisor_prompt"]
        en_template = _I18N_TEMPLATES["en"]["advisor_prompt"]
        
        # Check zh ordering
        step0_pos_zh = zh_template.find("第 0 步")
        mcp_section_pos_zh = zh_template.find("零安装接入")
        
        assert step0_pos_zh != -1, "zh: Step-0 should exist"
        assert mcp_section_pos_zh != -1, "zh: MCP section should exist"
        assert step0_pos_zh < mcp_section_pos_zh, "zh: Step-0 should come before MCP configuration section"
        
        # Check en ordering
        step0_pos_en = en_template.find("Step 0")
        mcp_section_pos_en = en_template.find("Zero-install onboarding") or en_template.find("onboarding")
        
        assert step0_pos_en != -1, "en: Step-0 should exist"
        assert mcp_section_pos_en != -1, "en: MCP section should exist"
        assert step0_pos_en < mcp_section_pos_en, "en: Step-0 should come before MCP configuration section"

    def test_ssh_reminder_on_advisor_connection_step(self):
        """S3.1: SSH 提醒在 step-1 向导区块内"""
        project_detail_path = REPO_ROOT / "web" / "board" / "static" / "project-detail.html"
        content = project_detail_path.read_text(encoding='utf-8')
        
        # Check for ssh-reminder element
        assert 'id="ssh-reminder"' in content, "Should have ssh-reminder element"
        
        # Check styling (orange left border)
        assert "#d97706" in content, "Should have orange color (#d97706)"
        assert "border-left: 3px solid" in content, "Should have left border"
        
        # Check content references
        assert "data-i18n=\"onboarding.ssh_reminder\"" in content, "Should use i18n for ssh_reminder"
        assert "data-i18n=\"onboarding.ssh_reminder_text\"" in content, "Should use i18n for ssh_reminder_text"
