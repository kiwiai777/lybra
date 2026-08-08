"""Test command_builder finalize action integration."""

from pathlib import Path

from tools.turn_advancer.command_builder import build_command


def test_finalize_action_builds_cli_command():
    """Test that finalize action builds a lybra finalize CLI command."""
    state = {
        "task_id": "AIPOS-TEST",
        "task_frontmatter": {
            "assigned_to": "exec.test",
            "agent_instance": "exec.test",
        },
        "latest_claim": {},
    }
    
    workspace_root = Path("/home/test/workspace")
    
    result = build_command("finalize", state, workspace_root)
    
    assert result["command_type"] == "cli"
    assert result["verb"] is None
    assert result["args"]["task_id"] == "AIPOS-TEST"
    assert result["args"]["actor"] == "exec.test"
    assert result["args"]["workspace_root"] == str(workspace_root)
    assert "lybra finalize" in result["copyable_line"] or "aipos_cli finalize" in result["copyable_line"]
    assert "--task-id AIPOS-TEST" in result["copyable_line"]
    assert "--actor exec.test" in result["copyable_line"]


def test_finalize_action_with_no_assigned_to():
    """Test finalize action falls back to system actor when no assigned_to."""
    state = {
        "task_id": "AIPOS-TEST",
        "task_frontmatter": {},
        "latest_claim": {},
    }
    
    workspace_root = Path("/home/test/workspace")
    
    result = build_command("finalize", state, workspace_root)
    
    assert result["command_type"] == "cli"
    assert result["args"]["actor"] == "system"
