"""
AIPOS-F42-fix1: _resolve_queue_workspace projects_enforced 单元测试
验证 F-1(P0) 越权洞修复：workspace_root 跨项目访问应被拦截
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Add lybra root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_resolve_queue_workspace_enforces_project_scope():
    """F-1: workspace_root 跨项目访问应被 projects_enforced 拦截"""
    from tools.mcp_server.tools import _resolve_queue_workspace, _capability_token
    
    # Mock: token scoped to ["lybra"]
    with patch('tools.mcp_server.tools._capability_token') as mock_token, \
         patch('tools.mcp_server.tools._resolve_active_project_for') as mock_resolve_project:
        
        # Setup: token with projects=["lybra"]
        mock_token.return_value = {"projects": ["lybra"]}
        
        # Setup: target workspace resolves to "chris-huibojin" project
        mock_resolve_project.return_value = "chris-huibojin"
        
        # Test: lybra-scoped token tries to access chris workspace
        chris_workspace = Path("/home/kiwi/ai-project-os/2_projects/chris-huibojin")
        
        with pytest.raises(ValueError) as exc_info:
            _resolve_queue_workspace({"workspace_root": str(chris_workspace)})
        
        # Verify: error message contains PROJECT_SCOPE_DENIED and project names
        error_msg = str(exc_info.value)
        assert "PROJECT_SCOPE_DENIED" in error_msg, f"Error should mention PROJECT_SCOPE_DENIED: {error_msg}"
        assert "lybra" in error_msg, f"Error should mention token's project scope: {error_msg}"
        assert "chris-huibojin" in error_msg, f"Error should mention target project: {error_msg}"
        
        print(f"✓ F-1 修复验证通过：跨项目访问被拦截")
        print(f"  拒因: {error_msg[:100]}...")


def test_resolve_queue_workspace_allows_same_project():
    """正向测试：同项目访问应该允许"""
    from tools.mcp_server.tools import _resolve_queue_workspace
    
    with patch('tools.mcp_server.tools._capability_token') as mock_token, \
         patch('tools.mcp_server.tools._resolve_active_project_for') as mock_resolve_project:
        
        # Setup: token scoped to ["lybra"]
        mock_token.return_value = {"projects": ["lybra"]}
        
        # Setup: workspace resolves to "lybra" (same project)
        mock_resolve_project.return_value = "lybra"
        
        # Test: lybra-scoped token accesses lybra workspace (should succeed)
        lybra_workspace = Path("/home/kiwi/ai-project-os/2_projects/lybra")
        
        result = _resolve_queue_workspace({"workspace_root": str(lybra_workspace)})
        
        assert result == lybra_workspace.resolve()
        print(f"✓ 同项目访问允许：lybra token → lybra workspace")


def test_resolve_queue_workspace_no_projects_field():
    """向后兼容：无 projects 字段的 token 不受限制"""
    from tools.mcp_server.tools import _resolve_queue_workspace
    
    with patch('tools.mcp_server.tools._capability_token') as mock_token, \
         patch('tools.mcp_server.tools._resolve_active_project_for') as mock_resolve_project:
        
        # Setup: token without projects field (legacy/owner token)
        mock_token.return_value = {}  # No "projects" field
        
        # Setup: workspace resolves to any project
        mock_resolve_project.return_value = "any-project"
        
        # Test: token without projects can access any workspace
        any_workspace = Path("/home/kiwi/ai-project-os/2_projects/chris-huibojin")
        
        result = _resolve_queue_workspace({"workspace_root": str(any_workspace)})
        
        assert result == any_workspace.resolve()
        print(f"✓ 向后兼容：无 projects 字段的 token 不受限")


def test_resolve_queue_workspace_nonexistent_path():
    """边界测试：不存在的 workspace_root 应报错"""
    from tools.mcp_server.tools import _resolve_queue_workspace
    
    with pytest.raises(ValueError) as exc_info:
        _resolve_queue_workspace({"workspace_root": "/nonexistent/path"})
    
    error_msg = str(exc_info.value)
    assert "does not exist" in error_msg
    print(f"✓ 边界测试：不存在的路径被拒绝")


if __name__ == "__main__":
    print("=== AIPOS-F42-fix1 单元测试 ===\n")
    
    test_resolve_queue_workspace_enforces_project_scope()
    print()
    
    test_resolve_queue_workspace_allows_same_project()
    print()
    
    test_resolve_queue_workspace_no_projects_field()
    print()
    
    test_resolve_queue_workspace_nonexistent_path()
    print()
    
    print("所有单元测试通过！")
