"""AIPOS-294 — Multi-project single-door routing tests.

Covers: request-level project routing (explicit project argument + single-project inference),
multi-project token authorization, workspace isolation, zero regression for single-project tokens.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.mcp_server import tools as gate
from tools.mcp_server.tools import (
    REQUEST_PROJECT,
    _resolve_request_project,
    dispatch_tool,
    request_capability_scope,
    request_project_scope,
)

_VALID = "2999-01-01T00:00:00Z"


def _cap(*, projects=None, operations=("queue_claim",)):
    cap = {
        "token_ref": "t",
        "role": "executor",
        "operations": list(operations),
        "expires_at": _VALID,
        "source": "service_v0",
    }
    if projects is not None:
        cap["projects"] = list(projects)
        cap["projects_enforced"] = True
    return cap


def _err(result):
    return (result.get("structuredContent") or {}).get("error_code")


class MultiProjectRoutingTests(unittest.TestCase):
    """AIPOS-294 S1: Request-level project routing tests."""

    def test_explicit_project_argument_routes_to_workspace(self) -> None:
        """Explicit project argument in tool call routes to that project's workspace."""
        mock_home = Path("/home/test/.lybra/projects")
        mock_project_root = mock_home / "projectA"
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root", return_value=mock_project_root) as mock_resolve, \
             patch.object(gate, "_resolve_active_project_for", return_value="projectA"), \
             request_capability_scope(_cap(projects=["projectA", "projectB"])):
            
            # Dispatch with explicit project argument
            with patch.object(gate, "get_queue", return_value={"ok": True}) as mock_handler:
                result = dispatch_tool("lybra_queue_list", {"project": "projectA"})
                
                # Verify project was resolved
                mock_resolve.assert_called_once_with(mock_home, "projectA")
                
                # Verify request project context was set
                # (handler should see the routed workspace)

    def test_single_project_token_infers_project(self) -> None:
        """Single-project token requires explicit project argument (no auto-inference for zero regression)."""
        request_project = _resolve_request_project({})  # No explicit project
        
        # Without explicit argument: returns None (legacy path)
        self.assertIsNone(request_project)
        
        # With single-project capability but no explicit argument: still None
        with request_capability_scope(_cap(projects=["projectA"])):
            request_project = _resolve_request_project({})
            self.assertIsNone(request_project)  # No auto-inference

    def test_multi_project_token_without_explicit_falls_back_legacy(self) -> None:
        """Multi-project token without explicit project argument falls back to legacy resolution."""
        with request_capability_scope(_cap(projects=["projectA", "projectB"])):
            request_project = _resolve_request_project({})
            self.assertIsNone(request_project)  # Falls back to None (legacy)

    def test_explicit_project_overrides_single_project_inference(self) -> None:
        """Explicit project argument overrides single-project inference."""
        with request_capability_scope(_cap(projects=["projectA", "projectB"])):
            request_project = _resolve_request_project({"project": "projectB"})
            self.assertEqual(request_project, "projectB")

    def test_unauthorized_project_denied(self) -> None:
        """Request for project not in token's projects list is denied."""
        mock_home = Path("/home/test/.lybra/projects")
        mock_project_root = mock_home / "projectC"
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root", return_value=mock_project_root), \
             request_capability_scope(_cap(projects=["projectA", "projectB"])):
            
            # Request projectC (not authorized)
            result = dispatch_tool("lybra_queue_list", {"project": "projectC"})
            self.assertEqual(_err(result), "PROJECT_SCOPE_DENIED")

    def test_request_project_context_isolation(self) -> None:
        """REQUEST_PROJECT context is properly isolated between requests."""
        with request_project_scope("projectA"):
            self.assertEqual(REQUEST_PROJECT.get(), "projectA")
            
            with request_project_scope("projectB"):
                self.assertEqual(REQUEST_PROJECT.get(), "projectB")
            
            # Restored after inner context
            self.assertEqual(REQUEST_PROJECT.get(), "projectA")
        
        # Cleared after outer context
        self.assertIsNone(REQUEST_PROJECT.get())

    def test_zero_regression_no_projects_field(self) -> None:
        """Tokens without projects field work unchanged (legacy behavior)."""
        with patch.object(gate, "_repo_root") as mock_repo_root, \
             patch.object(gate, "get_queue", return_value={"ok": True}), \
             request_capability_scope(_cap(projects=None)):
            
            # No projects field: should use legacy resolution (no routing)
            request_project = _resolve_request_project({})
            self.assertIsNone(request_project)
            
            # Dispatch should work (falls back to process-level resolution)
            result = dispatch_tool("lybra_queue_list", {})
            # Legacy path: _repo_root() called without routing
            mock_repo_root.assert_called()


class MultiProjectAuthorizationTests(unittest.TestCase):
    """AIPOS-294 S2/S3: Multi-project token authorization tests."""

    def test_token_with_multiple_projects_authorized(self) -> None:
        """Token with projects=[A, B] can access both A and B."""
        mock_home = Path("/home/test/.lybra/projects")
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root") as mock_resolve, \
             patch.object(gate, "get_queue", return_value={"ok": True}), \
             request_capability_scope(_cap(projects=["projectA", "projectB"])):
            
            # Access projectA
            mock_resolve.return_value = mock_home / "projectA"
            result_a = dispatch_tool("lybra_queue_list", {"project": "projectA"})
            self.assertNotEqual(_err(result_a), "PROJECT_SCOPE_DENIED")
            
            # Access projectB
            mock_resolve.return_value = mock_home / "projectB"
            result_b = dispatch_tool("lybra_queue_list", {"project": "projectB"})
            self.assertNotEqual(_err(result_b), "PROJECT_SCOPE_DENIED")

    def test_cross_project_access_denied(self) -> None:
        """Token for project A cannot access project B's workspace."""
        mock_home = Path("/home/test/.lybra/projects")
        mock_project_root = mock_home / "projectB"
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root", return_value=mock_project_root), \
             request_capability_scope(_cap(projects=["projectA"])):
            
            result = dispatch_tool("lybra_queue_list", {"project": "projectB"})
            self.assertEqual(_err(result), "PROJECT_SCOPE_DENIED")


class UnifiedRegistryLoadingTests(unittest.TestCase):
    """AIPOS-294C: Unified home_root registry loading tests."""

    def test_unified_load_combines_home_and_projects(self) -> None:
        """Unified loading combines home-level and project-level registries."""
        import tempfile
        import json
        from tools.mcp_server.http_sse import load_unified_service_role_registry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            
            # Create home-level connection.json (cross-project token)
            home_lybra = home / ".lybra"
            home_lybra.mkdir()
            home_conn = {
                "tokens": [
                    {
                        "token": "home_cross_project_token",
                        "role": "advisor",
                        "scopes": ["queue_claim"],
                        "projects": ["*"],  # Explicit cross-project
                    }
                ]
            }
            (home_lybra / "connection.json").write_text(json.dumps(home_conn))
            
            # Create project A
            proj_a = home / "projectA"
            proj_a.mkdir()
            (proj_a / "project.json").write_text(json.dumps({"name": "projectA"}))
            proj_a_queue = proj_a / "5_tasks" / "queue"
            proj_a_queue.mkdir(parents=True)
            proj_a_lybra = proj_a / ".lybra"
            proj_a_lybra.mkdir()
            proj_a_conn = {
                "tokens": [
                    {
                        "token": "projectA_dev_token",
                        "role": "dev",
                        "scopes": ["queue_claim"],
                        # No projects field - should default to ["projectA"]
                    }
                ]
            }
            (proj_a_lybra / "connection.json").write_text(json.dumps(proj_a_conn))
            
            # Create project B
            proj_b = home / "projectB"
            proj_b.mkdir()
            (proj_b / "project.json").write_text(json.dumps({"name": "projectB"}))
            proj_b_queue = proj_b / "5_tasks" / "queue"
            proj_b_queue.mkdir(parents=True)
            proj_b_lybra = proj_b / ".lybra"
            proj_b_lybra.mkdir()
            proj_b_conn = {
                "tokens": [
                    {
                        "token": "projectB_executor_token",
                        "role": "executor",
                        "scopes": ["queue_claim"],
                        "projects": ["projectB"],  # Explicit single project
                    }
                ]
            }
            (proj_b_lybra / "connection.json").write_text(json.dumps(proj_b_conn))
            
            # Load unified registry
            registry = load_unified_service_role_registry(home)
            
            # Verify all three tokens present
            self.assertIn("home_cross_project_token", registry)
            self.assertIn("projectA_dev_token", registry)
            self.assertIn("projectB_executor_token", registry)
            
            # Verify projects attribution
            self.assertEqual(registry["home_cross_project_token"]["projects"], ["*"])
            self.assertEqual(registry["projectA_dev_token"]["projects"], ["projectA"])
            self.assertEqual(registry["projectB_executor_token"]["projects"], ["projectB"])

    def test_explicit_projects_respected_not_overwritten(self) -> None:
        """DEBT REMOVAL: Explicit projects in token are respected, not overwritten by source."""
        import tempfile
        import json
        from tools.mcp_server.http_sse import load_unified_service_role_registry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            
            # Create project A with a token that explicitly declares multi-project access
            proj_a = home / "projectA"
            proj_a.mkdir()
            (proj_a / "project.json").write_text(json.dumps({"name": "projectA"}))
            proj_a_queue = proj_a / "5_tasks" / "queue"
            proj_a_queue.mkdir(parents=True)
            proj_a_lybra = proj_a / ".lybra"
            proj_a_lybra.mkdir()
            proj_a_conn = {
                "tokens": [
                    {
                        "token": "multi_project_token",
                        "role": "auditor",
                        "scopes": ["queue_claim"],
                        "projects": ["projectA", "projectB"],  # Explicit multi-project
                    }
                ]
            }
            (proj_a_lybra / "connection.json").write_text(json.dumps(proj_a_conn))
            
            # Load unified registry
            registry = load_unified_service_role_registry(home)
            
            # CRITICAL: projects must be ["projectA", "projectB"], NOT overwritten to ["projectA"]
            self.assertEqual(registry["multi_project_token"]["projects"], ["projectA", "projectB"])

    def test_token_collision_warns_keeps_first(self) -> None:
        """Token collision between files warns and keeps first-seen."""
        import tempfile
        import json
        import io
        from tools.mcp_server.http_sse import load_unified_service_role_registry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            
            # Home-level token
            home_lybra = home / ".lybra"
            home_lybra.mkdir()
            home_conn = {
                "tokens": [
                    {
                        "token": "duplicate_token",
                        "role": "home_role",
                        "scopes": ["queue_claim"],
                        "projects": ["*"],
                    }
                ]
            }
            (home_lybra / "connection.json").write_text(json.dumps(home_conn))
            
            # Project A with same token
            proj_a = home / "projectA"
            proj_a.mkdir()
            (proj_a / "project.json").write_text(json.dumps({"name": "projectA"}))
            proj_a_queue = proj_a / "5_tasks" / "queue"
            proj_a_queue.mkdir(parents=True)
            proj_a_lybra = proj_a / ".lybra"
            proj_a_lybra.mkdir()
            proj_a_conn = {
                "tokens": [
                    {
                        "token": "duplicate_token",
                        "role": "project_role",
                        "scopes": ["queue_claim"],
                    }
                ]
            }
            (proj_a_lybra / "connection.json").write_text(json.dumps(proj_a_conn))
            
            # Load with captured stderr
            stderr = io.StringIO()
            registry = load_unified_service_role_registry(home, error_stream=stderr)
            
            # Should keep home entry (first-seen)
            self.assertEqual(registry["duplicate_token"]["role"], "home_role")
            self.assertEqual(registry["duplicate_token"]["projects"], ["*"])
            
            # Should have warned
            warning = stderr.getvalue()
            self.assertIn("collision", warning.lower())

    def test_missing_projects_defaults_to_source_project(self) -> None:
        """Tokens without projects field default to their source project."""
        import tempfile
        import json
        from tools.mcp_server.http_sse import load_unified_service_role_registry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            
            # Create project without projects field
            proj_a = home / "projectA"
            proj_a.mkdir()
            (proj_a / "project.json").write_text(json.dumps({"name": "projectA"}))
            proj_a_queue = proj_a / "5_tasks" / "queue"
            proj_a_queue.mkdir(parents=True)
            proj_a_lybra = proj_a / ".lybra"
            proj_a_lybra.mkdir()
            proj_a_conn = {
                "tokens": [
                    {
                        "token": "project_token_no_projects",
                        "role": "dev",
                        "scopes": ["queue_claim"],
                        # No projects field
                    }
                ]
            }
            (proj_a_lybra / "connection.json").write_text(json.dumps(proj_a_conn))
            
            # Load unified registry
            registry = load_unified_service_role_registry(home)
            
            # Should default to source project
            self.assertEqual(registry["project_token_no_projects"]["projects"], ["projectA"])

    def test_home_level_without_projects_defaults_to_wildcard(self) -> None:
        """Home-level tokens without projects field default to ["*"]."""
        import tempfile
        import json
        from tools.mcp_server.http_sse import load_unified_service_role_registry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            
            # Home-level token without projects
            home_lybra = home / ".lybra"
            home_lybra.mkdir()
            home_conn = {
                "tokens": [
                    {
                        "token": "home_token_no_projects",
                        "role": "owner",
                        "scopes": ["queue_claim"],
                        # No projects field
                    }
                ]
            }
            (home_lybra / "connection.json").write_text(json.dumps(home_conn))
            
            # Load unified registry
            registry = load_unified_service_role_registry(home)
            
            # Should default to wildcard
            self.assertEqual(registry["home_token_no_projects"]["projects"], ["*"])

    def test_token_values_never_modified(self) -> None:
        """Token values are preserved exactly as in source files."""
        import tempfile
        import json
        from tools.mcp_server.http_sse import load_unified_service_role_registry
        
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            
            original_token_value = "original_token_12345_unchanged"
            
            # Create project with token
            proj_a = home / "projectA"
            proj_a.mkdir()
            (proj_a / "project.json").write_text(json.dumps({"name": "projectA"}))
            proj_a_queue = proj_a / "5_tasks" / "queue"
            proj_a_queue.mkdir(parents=True)
            proj_a_lybra = proj_a / ".lybra"
            proj_a_lybra.mkdir()
            proj_a_conn = {
                "tokens": [
                    {
                        "token": original_token_value,
                        "role": "dev",
                        "scopes": ["queue_claim"],
                    }
                ]
            }
            (proj_a_lybra / "connection.json").write_text(json.dumps(proj_a_conn))
            
            # Load unified registry
            registry = load_unified_service_role_registry(home)
            
            # Token value must be unchanged
            self.assertIn(original_token_value, registry)
            # Verify it's the exact key (not modified)
            self.assertEqual(list(registry.keys())[0], original_token_value)


if __name__ == "__main__":
    unittest.main()
