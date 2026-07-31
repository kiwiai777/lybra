"""
AIPOS-286 FIX-2: 统一 i18n 生成通道契约测试
生成类文案(接入提示词/向导说明/MCP片段/QUICKSTART)走统一服务端通道,en模式CJK零残留硬断言。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web.board.app import _generate_text, _generate_advisor_prompt_route, _I18N_TEMPLATES


class TestAIPOS286FIX2I18nChannel:
    """AIPOS-286 FIX-2: 生成类文案统一 i18n 通道"""

    # ===== S1: 服务端 i18n 通道建立 =====

    def test_generate_text_function_exists_and_enforces_locale_coverage(self):
        """S1.1: _generate_text() 存在,zh/en 必须同时存在同键名模板(缺 en 键抛异常不静默回退)"""
        # zh mode should work
        result_zh = _generate_text("advisor_prompt", "zh", 
                                    workspace_label="test", workspace_root="/test",
                                    gate_url="http://test", charter_path="/charter",
                                    example_card_path="/example", 
                                    server_hostname="host", server_ip="1.2.3.4")
        assert isinstance(result_zh, str), "Should return zh text"
        assert "工作区" in result_zh, "zh template should contain Chinese"
        
        # en mode should work
        result_en = _generate_text("advisor_prompt", "en",
                                    workspace_label="test", workspace_root="/test",
                                    gate_url="http://test", charter_path="/charter",
                                    example_card_path="/example",
                                    server_hostname="host", server_ip="1.2.3.4")
        assert isinstance(result_en, str), "Should return en text"
        assert "Workspace" in result_en or "workspace" in result_en, "en template should contain English"
        
        # Missing key should raise KeyError (not silent fallback)
        with pytest.raises(KeyError, match="missing in locale"):
            _generate_text("nonexistent_key", "en", test_var="value")

    def test_i18n_templates_have_both_zh_and_en_for_advisor_prompt(self):
        """S1.2: _I18N_TEMPLATES 中 advisor_prompt 键 zh/en 同时存在"""
        assert "zh" in _I18N_TEMPLATES, "Should have zh locale"
        assert "en" in _I18N_TEMPLATES, "Should have en locale"
        assert "advisor_prompt" in _I18N_TEMPLATES["zh"], "zh should have advisor_prompt"
        assert "advisor_prompt" in _I18N_TEMPLATES["en"], "en should have advisor_prompt"

    def test_api_route_generate_advisor_prompt_exists(self, tmp_path):
        """S1.3: GET /api/generate/advisor-prompt?locale=<zh|en> 路由存在,返回生成文案"""
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
        
        # zh mode
        result_zh = _generate_advisor_prompt_route(
            {"locale": ["zh"], "workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        assert result_zh["ok"] is True, "zh request should succeed"
        assert "data" in result_zh, "Should have data"
        assert "prompt" in result_zh["data"], "Should have prompt in data"
        assert isinstance(result_zh["data"]["prompt"], str), "prompt should be string"
        assert "工作区" in result_zh["data"]["prompt"], "zh prompt should contain Chinese"
        
        # en mode
        result_en = _generate_advisor_prompt_route(
            {"locale": ["en"], "workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        assert result_en["ok"] is True, "en request should succeed"
        assert "Workspace" in result_en["data"]["prompt"] or "workspace" in result_en["data"]["prompt"], \
            "en prompt should contain English"

    # ===== S2: en 模式 CJK 零残留硬断言 =====

    def test_en_prompt_has_zero_cjk_characters(self, tmp_path):
        """S2.1: en 模式生成的 advisor_prompt 不含 CJK 字符(路径/专名/卡号白名单除外)"""
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
        
        result = _generate_advisor_prompt_route(
            {"locale": ["en"], "workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        assert result["ok"] is True, "en request should succeed"
        prompt_text = result["data"]["prompt"]
        
        # CJK regex: Chinese, Japanese, Korean characters
        cjk_pattern = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')
        
        # Extract all CJK characters (for debugging if test fails)
        cjk_chars = cjk_pattern.findall(prompt_text)
        
        # Hard assertion: no CJK in en mode
        assert len(cjk_chars) == 0, \
            f"en mode prompt contains CJK characters (AIPOS-286 FIX-2 red line): {cjk_chars[:20]}"

    def test_zh_prompt_content_equivalent_to_baseline(self, tmp_path):
        """S2.2: zh 模式内容与现状等价(关键要素存在)"""
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
        
        result = _generate_advisor_prompt_route(
            {"locale": ["zh"], "workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        prompt_text = result["data"]["prompt"]
        
        # Check key elements (baseline equivalence)
        assert "顾问" in prompt_text or "Advisor" in prompt_text, "Should mention advisor role"
        assert "工作区" in prompt_text, "Should mention workspace"
        assert "第 0 步" in prompt_text, "Should have step-0"
        assert "同机确认" in prompt_text or "连通性检测" in prompt_text, "Should have connectivity check"
        assert "零安装接入" in prompt_text or "MCP" in prompt_text, "Should have MCP onboarding"
        assert "Charter" in prompt_text or "charter" in prompt_text, "Should reference charter"
        assert "lybra agent watch" in prompt_text, "Should mention watch command"

    def test_en_prompt_has_all_required_sections(self, tmp_path):
        """S2.3: en 模式包含所有必要章节(step-0/MCP接入/charter等)"""
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
        
        result = _generate_advisor_prompt_route(
            {"locale": ["en"], "workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        prompt_text = result["data"]["prompt"]
        
        # Check required sections
        assert "Advisor" in prompt_text or "advisor" in prompt_text, "Should mention advisor role"
        assert "Workspace" in prompt_text or "workspace" in prompt_text, "Should mention workspace"
        assert "Step 0" in prompt_text, "Should have step-0"
        assert "connectivity" in prompt_text or "Same-machine" in prompt_text, \
            "Should have connectivity check"
        assert "onboarding" in prompt_text or "MCP" in prompt_text, "Should have MCP onboarding"
        assert "charter" in prompt_text or "Charter" in prompt_text, "Should reference charter"
        assert "lybra agent watch" in prompt_text, "Should mention watch command"

    # ===== S3: 通道模块 docstring 规约 =====

    def test_generate_text_docstring_declares_usage_rules(self):
        """S3.1: _generate_text() docstring 写明规约(新增文案必走此通道)"""
        docstring = _generate_text.__doc__ or ""
        assert "AIPOS-286 FIX-2" in docstring, "Should reference FIX-2"
        assert "generated content" in docstring.lower() or "生成" in docstring, \
            "Should mention generated content"
        assert "locale" in docstring.lower(), "Should mention locale"
        assert "template" in docstring.lower(), "Should mention template"

    def test_i18n_templates_module_level_comment_exists(self):
        """S3.2: _I18N_TEMPLATES 模块级注释写明通道用途与约束"""
        # Read app.py source to check for module-level comment
        app_py = REPO_ROOT / "web" / "board" / "app.py"
        content = app_py.read_text(encoding="utf-8")
        
        # Find _I18N_TEMPLATES definition and check for preceding comment
        templates_pos = content.find("_I18N_TEMPLATES")
        assert templates_pos > 0, "_I18N_TEMPLATES should exist"
        
        # Check for AIPOS-286 FIX-2 comment within 1500 chars before definition
        preceding_text = content[max(0, templates_pos - 1500):templates_pos]
        assert "AIPOS-286 FIX-2" in preceding_text, \
            "Should have AIPOS-286 FIX-2 comment before _I18N_TEMPLATES"
        assert "i18n" in preceding_text.lower() or "I18N" in preceding_text, \
            "Comment should mention i18n"

    # ===== S4: 前端集成 =====

    def test_frontend_calls_server_api_not_inline_generation(self):
        """S4.1: 前端 project-detail.html 改为调用 /api/generate/advisor-prompt (不再内联拼串)"""
        detail_html = REPO_ROOT / "web" / "board" / "static" / "project-detail.html"
        content = detail_html.read_text(encoding="utf-8")
        
        # Should call API
        assert "/api/generate/advisor-prompt" in content, \
            "Frontend should call /api/generate/advisor-prompt"
        
        # Should pass locale parameter
        assert "locale=" in content or "getCurrentLang" in content, \
            "Frontend should pass locale parameter"
        
        # Should NOT have inline Chinese template (old hardcoded prompt removed)
        # Check that the old template string is gone
        renderOnboardingGuide_pos = content.find("async function renderOnboardingGuide")
        if renderOnboardingGuide_pos > 0:
            # Check next 2000 chars for old inline template markers
            func_body = content[renderOnboardingGuide_pos:renderOnboardingGuide_pos + 2000]
            # Old inline template would have had these literal string concatenations
            assert "你是 ${workspaceLabel} 工作区的顾问" not in func_body, \
                "Old inline Chinese template should be removed"

    @patch('web.board.app.socket.gethostname')
    @patch('web.board.app.socket.socket')
    def test_api_includes_server_location_in_prompt(self, mock_socket_class, mock_gethostname, tmp_path):
        """S4.2: API 生成的提示词包含服务端位置信息(AIPOS-286)"""
        mock_gethostname.return_value = "test-server"
        mock_socket_instance = mock_socket_class.return_value
        mock_socket_instance.getsockname.return_value = ("192.168.1.100", 0)
        
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
        
        result = _generate_advisor_prompt_route(
            {"locale": ["zh"], "workspace": ["0"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        
        prompt_text = result["data"]["prompt"]
        assert "test-server" in prompt_text, "Should include server hostname"
        assert "192.168.1.100" in prompt_text, "Should include server IP"

    # ===== S5: 回归防护 =====

    def test_all_existing_i18n_keys_preserved(self):
        """S5.1: 现有前端 i18n 键值不受影响(向后兼容)"""
        i18n_js = REPO_ROOT / "web" / "board" / "static" / "i18n.js"
        content = i18n_js.read_text(encoding="utf-8")
        
        # Check that existing keys still exist
        existing_keys = [
            "onboarding.step1_title",
            "onboarding.step1_body",
            "onboarding.copy_prompt",
            "onboarding.ssh_reminder",
            "map.title",
            "vb.title",
            "tc.title",
        ]
        
        for key in existing_keys:
            assert f"'{key}'" in content, f"Existing i18n key '{key}' should be preserved"

    def test_zh_en_mode_switching_works(self, tmp_path):
        """S5.2: zh/en 模式切换正常(locale 参数生效)"""
        workspace_root = tmp_path / "test_workspace"
        workspace_root.mkdir()
        (workspace_root / "5_tasks" / "queue").mkdir(parents=True)
        
        # Request zh
        result_zh = _generate_advisor_prompt_route(
            {"locale": ["zh"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        assert result_zh["data"]["locale"] == "zh", "Should return zh locale"
        
        # Request en
        result_en = _generate_advisor_prompt_route(
            {"locale": ["en"]},
            repo_root=workspace_root,
            board_config_path=None
        )
        assert result_en["data"]["locale"] == "en", "Should return en locale"
        
        # Default (no locale param) should be zh
        result_default = _generate_advisor_prompt_route(
            {},
            repo_root=workspace_root,
            board_config_path=None
        )
        assert result_default["data"]["locale"] == "zh", "Default should be zh"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
