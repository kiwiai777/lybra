"""AIPOS-FND-17 — Single-gate project inference tests.

Tests connection-level project inference so standard MCP clients (Claude Code) can use
single-gate without injecting project per-call.

Covers:
1. Single-project token auto-inference (agency token → kiwiaiagency)
2. Connection-level default_project (multi-project tokens)
3. Explicit per-call project override (highest priority)
4. Actionable error messages when inference fails
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.mcp_server import tools as gate
from tools.mcp_server.tools import (
    _resolve_request_project,
    dispatch_tool,
    request_capability_scope,
)

_VALID = "2999-01-01T00:00:00Z"


def _cap(*, projects=None, default_project=None, operations=("queue_claim",)):
    """Create capability token with optional projects and default_project."""
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
    if default_project is not None:
        cap["default_project"] = default_project
    return cap


def _err(result):
    """Extract error code from result."""
    return (result.get("structuredContent") or {}).get("error_code")


class ProjectInferenceTests(unittest.TestCase):
    """AIPOS-FND-17: Connection-level project inference tests."""

    def test_explicit_project_has_highest_priority(self) -> None:
        """Explicit project argument overrides all inference."""
        # Single-project token with default_project
        with request_capability_scope(_cap(projects=["projectA"], default_project="projectA")):
            result = _resolve_request_project({"project": "projectB"})
            self.assertEqual(result, "projectB")  # Explicit wins

    def test_default_project_second_priority(self) -> None:
        """Token's default_project used when no explicit project."""
        with request_capability_scope(_cap(projects=["projectA", "projectB"], default_project="projectA")):
            result = _resolve_request_project({})
            self.assertEqual(result, "projectA")  # default_project

    def test_single_project_inference_third_priority(self) -> None:
        """Single-project token auto-infers its only project."""
        with request_capability_scope(_cap(projects=["kiwiaiagency"])):
            result = _resolve_request_project({})
            self.assertEqual(result, "kiwiaiagency")  # Auto-inferred

    def test_single_project_inference_agency_use_case(self) -> None:
        """Agency token (single-project) auto-routes without per-call injection."""
        with request_capability_scope(_cap(projects=["kiwiaiagency"])):
            result = _resolve_request_project({})
            self.assertEqual(result, "kiwiaiagency")
            
    def test_multi_project_without_default_falls_back_legacy(self) -> None:
        """Multi-project token without default_project falls back to None (legacy)."""
        with request_capability_scope(_cap(projects=["projectA", "projectB"])):
            result = _resolve_request_project({})
            self.assertIsNone(result)  # No inference, legacy path

    def test_no_projects_field_returns_none(self) -> None:
        """Token without projects field returns None (legacy unrestricted)."""
        with request_capability_scope(_cap()):  # No projects
            result = _resolve_request_project({})
            self.assertIsNone(result)

    def test_empty_projects_list_returns_none(self) -> None:
        """Empty projects list returns None (edge case)."""
        with request_capability_scope(_cap(projects=[])):
            result = _resolve_request_project({})
            self.assertIsNone(result)

    def test_whitespace_project_argument_ignored(self) -> None:
        """Whitespace-only project argument is ignored, falls to inference."""
        with request_capability_scope(_cap(projects=["projectA"])):
            result = _resolve_request_project({"project": "  "})
            self.assertEqual(result, "projectA")  # Falls to single-project inference

    def test_default_project_overrides_single_project_inference(self) -> None:
        """If both default_project and single-project exist, default_project wins."""
        # This is a degenerate case (single-project token shouldn't need default_project)
        # but validates priority order
        with request_capability_scope(_cap(projects=["projectA"], default_project="projectA")):
            result = _resolve_request_project({})
            self.assertEqual(result, "projectA")  # default_project checked before inference


class ProjectGateErrorTests(unittest.TestCase):
    """AIPOS-FND-17: Error message clarity tests."""

    def test_unauthorized_project_gives_actionable_error(self) -> None:
        """Requesting unauthorized project gives clear error with guidance."""
        mock_home = Path("/home/test/.lybra/projects")
        mock_project_root = mock_home / "projectC"
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root", return_value=mock_project_root), \
             request_capability_scope(_cap(projects=["projectA", "projectB"])):
            
            result = dispatch_tool("lybra_queue_list", {"project": "projectC"})
            
            # Should be PROJECT_SCOPE_DENIED with clear message
            self.assertEqual(_err(result), "PROJECT_SCOPE_DENIED")
            error_msg = result.get("structuredContent", {}).get("message", "")
            self.assertIn("projectC", error_msg)
            self.assertIn("not in the token's authorized projects", error_msg)

    def test_single_project_inference_works_end_to_end(self) -> None:
        """Single-project token can call tools without explicit project argument."""
        mock_home = Path("/home/test/.lybra/projects")
        mock_project_root = mock_home / "projectA"
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root", return_value=mock_project_root), \
             patch.object(gate, "_resolve_active_project_for", return_value="projectA"), \
             patch.object(gate, "get_queue", return_value={"ok": True}), \
             request_capability_scope(_cap(projects=["projectA"])):
            
            # Call without project argument - should auto-infer
            result = dispatch_tool("lybra_queue_list", {})
            
            # Should succeed (not PROJECT_SCOPE_DENIED)
            self.assertIsNone(_err(result))

    def test_default_project_works_end_to_end(self) -> None:
        """Multi-project token with default_project can call tools without explicit project."""
        mock_home = Path("/home/test/.lybra/projects")
        mock_project_root = mock_home / "projectA"
        
        with patch("tools.aipos_cli.workspace_config.resolve_home_root", return_value=mock_home), \
             patch("tools.aipos_cli.workspace_config.resolve_project_root", return_value=mock_project_root), \
             patch.object(gate, "_resolve_active_project_for", return_value="projectA"), \
             patch.object(gate, "get_queue", return_value={"ok": True}), \
             request_capability_scope(_cap(projects=["projectA", "projectB"], default_project="projectA")):
            
            # Call without project argument - should use default_project
            result = dispatch_tool("lybra_queue_list", {})
            
            # Should succeed
            self.assertIsNone(_err(result))


if __name__ == "__main__":
    unittest.main()
