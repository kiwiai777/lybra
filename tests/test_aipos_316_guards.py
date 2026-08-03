"""AIPOS-316: Tests for advisor guardrails (误用即响).

S1.1: Internal modules reject direct invocation
S1.2: Watch emits observation surface hint when never changed
S1.3: Rotate blocks when losing instance bindings
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest


# S1.1: Test that all internal modules (no __main__) reject direct invocation
def test_internal_modules_reject_direct_invocation():
    """All tools/aipos_cli/*.py modules without __main__ should exit 1 when run with python -m."""
    tools_dir = Path(__file__).parent.parent / "tools" / "aipos_cli"
    
    # Collect all .py files without __main__
    internal_modules = []
    for py_file in tools_dir.glob("*.py"):
        if py_file.name.startswith("__"):
            continue
        # Skip the guard implementation itself (it's not a business module)
        if py_file.stem == "_cli_entry_guard":
            continue
        content = py_file.read_text(encoding="utf-8")
        if 'if __name__ == "__main__"' not in content and "if __name__ == '__main__'" not in content:
            internal_modules.append(py_file.stem)
    
    # At least some internal modules should exist
    assert len(internal_modules) > 10, f"Expected many internal modules, found {len(internal_modules)}"
    
    # Test ALL internal modules (dynamic enumeration, not hardcoded sample)
    # This ensures newly added modules are automatically covered
    for module_name in internal_modules:
        
        result = subprocess.run(
            [sys.executable, "-m", f"tools.aipos_cli.{module_name}"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
        )
        
        # Should exit with non-zero code
        assert result.returncode != 0, (
            f"{module_name} should reject direct invocation with non-zero exit code, "
            f"got {result.returncode}"
        )
        
        # Should print error message to stderr
        assert "not a command-line entry point" in result.stderr.lower() or "not a cli entry point" in result.stderr.lower(), (
            f"{module_name} should print clear error message, got: {result.stderr}"
        )
        
        # Should mention 'lybra' command
        assert "lybra" in result.stderr.lower(), (
            f"{module_name} error should mention 'lybra' command, got: {result.stderr}"
        )


def test_cli_entry_guard_coverage():
    """Verify that _cli_entry_guard is imported by internal modules."""
    tools_dir = Path(__file__).parent.parent / "tools" / "aipos_cli"
    
    # Sample a few internal modules
    sample_modules = [
        "agent_watch_fs.py",
        "board_adapter.py",
        "service_mode.py",
    ]
    
    for module_file in sample_modules:
        path = tools_dir / module_file
        if not path.exists():
            continue
        
        content = path.read_text(encoding="utf-8")
        
        # Should import the guard
        assert "_cli_entry_guard" in content or "check_direct_invocation" in content, (
            f"{module_file} should import _cli_entry_guard"
        )


# S1.2: Test watch observation surface hint
def test_watch_observation_surface_hint(tmp_path, monkeypatch):
    """Watch should emit hint when observation surface never changes since startup."""
    # This is an integration-style test that would require a full watch setup.
    # For now, verify the code path exists in agent_watch_fs.py
    watch_file = Path(__file__).parent.parent / "tools" / "aipos_cli" / "agent_watch_fs.py"
    content = watch_file.read_text(encoding="utf-8")
    
    # Verify the hint emission logic exists
    assert "initial_obs_mtime" in content, "Watch should track initial observation mtime"
    assert "stall_hint_emitted" in content, "Watch should track whether hint was emitted"
    assert "Observation surface has not changed" in content, "Watch should emit observation surface hint"
    assert "--worktree-path" in content and "--proc-pattern" in content, "Hint should mention alternative surfaces"


# S1.3: Test rotate blocks when losing instance bindings
def test_rotate_blocks_on_lost_bindings(tmp_path):
    """serve rotate should block when it would lose existing instance bindings."""
    from tools.aipos_cli.service_mode import rotate_report, write_connection_config
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".lybra").mkdir()
    conn_file = workspace / ".lybra" / "connection.json"
    
    # Create a connection.json with instance bindings
    existing_config = {
        "config_version": 1,
        "mode": "service_v0",
        "workspace_root": str(workspace),
        "tokens": [
            {
                "role": "executor",
                "token": "test_token_1",
                "scopes": ["queue_claim", "queue_return"],
                "agent_instance": "exec.test.local",
                "token_ref": "svc-executor",
                "fingerprint": "sha256:test1",
            },
            {
                "role": "auditor",
                "token": "test_token_2",
                "scopes": ["queue_claim", "audit_verdict"],
                "agent_instance": "audit.test.local",
                "token_ref": "svc-auditor",
                "fingerprint": "sha256:test2",
            },
        ],
    }
    write_connection_config(workspace, existing_config, connection_target=conn_file)
    
    # Attempt rotate WITHOUT preserving bindings (should block)
    result = rotate_report(
        workspace,
        board_host="127.0.0.1",
        board_port=7117,
        mcp_host="127.0.0.1",
        mcp_port=7118,
        connection_target=conn_file,
        executor_instance=None,  # Not preserving executor binding
        role_instances=None,     # Not preserving auditor binding
    )
    
    # Should block
    assert result.get("verdict") == "BLOCK", "rotate should block when losing bindings"
    assert result.get("ok") is False, "rotate should not succeed when losing bindings"
    
    blocking_reasons = result.get("blocking_reasons", [])
    assert len(blocking_reasons) > 0, "Should have blocking reasons"
    
    # Should mention the lost bindings
    blocking_text = " ".join(blocking_reasons).lower()
    assert "instance binding" in blocking_text, "Should mention instance bindings"
    assert "exec.test.local" in blocking_text or "executor" in blocking_text, "Should mention executor binding"


def test_rotate_succeeds_with_preserved_bindings(tmp_path):
    """serve rotate should succeed when instance bindings are preserved."""
    from tools.aipos_cli.service_mode import rotate_report, write_connection_config
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".lybra").mkdir()
    conn_file = workspace / ".lybra" / "connection.json"
    
    # Create a connection.json with instance bindings
    existing_config = {
        "config_version": 1,
        "mode": "service_v0",
        "workspace_root": str(workspace),
        "tokens": [
            {
                "role": "executor",
                "token": "test_token_1",
                "scopes": ["queue_claim", "queue_return"],
                "agent_instance": "exec.test.local",
                "token_ref": "svc-executor",
                "fingerprint": "sha256:test1",
            },
        ],
    }
    write_connection_config(workspace, existing_config, connection_target=conn_file)
    
    # Rotate WITH preserved binding (should succeed)
    result = rotate_report(
        workspace,
        board_host="127.0.0.1",
        board_port=7117,
        mcp_host="127.0.0.1",
        mcp_port=7118,
        connection_target=conn_file,
        executor_instance="exec.test.local",  # Preserving executor binding
    )
    
    # Should succeed
    assert result.get("ok") is True, f"rotate should succeed when preserving bindings: {result.get('blocking_reasons')}"
    assert result.get("verdict") in ("PASS", "WARN"), "rotate should pass when bindings preserved"


def test_rotate_succeeds_with_no_existing_bindings(tmp_path):
    """serve rotate should succeed when there are no existing bindings to lose."""
    from tools.aipos_cli.service_mode import rotate_report, write_connection_config
    
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".lybra").mkdir()
    conn_file = workspace / ".lybra" / "connection.json"
    
    # Create a connection.json WITHOUT instance bindings
    existing_config = {
        "config_version": 1,
        "mode": "service_v0",
        "workspace_root": str(workspace),
        "tokens": [
            {
                "role": "executor",
                "token": "test_token_1",
                "scopes": ["queue_claim", "queue_return"],
                "token_ref": "svc-executor",
                "fingerprint": "sha256:test1",
                # No agent_instance field
            },
        ],
    }
    write_connection_config(workspace, existing_config, connection_target=conn_file)
    
    # Rotate without specifying bindings (should succeed since there's nothing to lose)
    result = rotate_report(
        workspace,
        board_host="127.0.0.1",
        board_port=7117,
        mcp_host="127.0.0.1",
        mcp_port=7118,
        connection_target=conn_file,
        executor_instance=None,
    )
    
    # Should succeed
    assert result.get("ok") is True, f"rotate should succeed when no bindings exist: {result.get('blocking_reasons')}"
    assert result.get("verdict") in ("PASS", "WARN"), "rotate should pass when no bindings to lose"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
