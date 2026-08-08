#!/usr/bin/env python3
"""
AIPOS-FND-8: Test lybra_gate_version reports correct deployment commit.

Validates:
1. Reads VERSION file git_commit when present (deployment snapshot)
2. Falls back to git in runtime dir (not workspace)
3. Does NOT report workspace (治理仓) HEAD
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest


def test_gate_version_reads_version_file():
    """VERSION file git_commit takes priority (deployment snapshot)."""
    # This test validates the logic but cannot simulate actual deployment
    # because lybra_gate_version uses __file__ to locate code root.
    # The actual deployment test is integration-level (deploy + HTTP probe).
    # Here we just verify the current .deploy/current can be read correctly.
    from tools.mcp_server.tools import lybra_gate_version
    
    result = lybra_gate_version()
    
    assert result["isError"] is False
    content = result.get("structuredContent", {})
    assert content["ok"] is True
    assert "git_commit" in content
    assert "git_commit_short" in content
    assert "runtime_directory" in content
    # Should use product repo git (non-deployed) or VERSION file (deployed)
    assert content["source"] in ["VERSION_file", "git_code_root"]


def test_gate_version_fallback_to_git():
    """When no VERSION file, falls back to git in code root (product repo)."""
    from tools.mcp_server.tools import lybra_gate_version
    
    # In normal operation (non-deployed), should read product repo HEAD
    result = lybra_gate_version()
    
    assert result["isError"] is False
    content = result.get("structuredContent", {})
    assert content["ok"] is True
    
    # Should have git commit from product repo
    assert "git_commit" in content
    assert len(content["git_commit"]) == 40  # Full SHA-1
    assert content["git_commit_short"] == content["git_commit"][:7]
    
    # Verify it's from product repo, NOT workspace
    product_repo = Path(__file__).parent.parent.parent
    product_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=product_repo,
        check=True,
        capture_output=True,
        text=True
    ).stdout.strip()
    
    assert content["git_commit"] == product_commit


def test_gate_version_does_not_read_workspace():
    """Regression: must NOT read workspace (治理仓) HEAD."""
    from tools.mcp_server.tools import lybra_gate_version
    
    # Get workspace (治理仓) HEAD
    # Workspace is typically parent of product repo or AIPOS_WORKSPACE_ROOT
    workspace_root = Path(os.environ.get("AIPOS_WORKSPACE_ROOT", "/home/kiwi/ai-project-os/2_projects/lybra"))
    if workspace_root.exists() and (workspace_root / ".git").exists():
        workspace_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace_root,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        # Get product repo HEAD
        product_repo = Path(__file__).parent.parent.parent
        product_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=product_repo,
            check=True,
            capture_output=True,
            text=True
        ).stdout.strip()
        
        result = lybra_gate_version()
        content = result.get("structuredContent", {})
        
        # MUST NOT report workspace commit
        if workspace_commit != product_commit:
            assert content["git_commit"] != workspace_commit, \
                "BUG: lybra_gate_version reported workspace HEAD instead of product commit"
            assert content["git_commit"] == product_commit or content.get("source") == "VERSION_file", \
                "Should report product repo or VERSION file, not workspace"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
