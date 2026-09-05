"""AIPOS-F76 integration test: 工作纪律 section in create/publish/regen."""
from pathlib import Path

from tools.aipos_cli.draft_writer import create_draft
from tools.aipos_cli.machine_zone import derive_machine_zone_纪律段


def test_f76_create_injects_discipline_section():
    """AIPOS-F76 件①: draft create injects 工作纪律 section."""
    repo_root = Path(__file__).parent.parent.parent.parent
    
    metadata = {
        "task_id": "TEST-F76-INTEGRATION",
        "title": "Test F76 integration",
        "project": "lybra",
        "assigned_to": "test.agent",
        "context_bundle": "test",
        "task_mode": "code",
        "priority": "high",
        "status": "pending",
        "created_by": "test",
        "needs_owner": False,
        "output_target": "test/",
        "artifact_policy": "formal_write",
    }
    
    body = "## Goal\n\nTest goal.\n"
    
    result = create_draft(repo_root, metadata, body, dry_run=True)
    
    # Should not block (may warn)
    assert result["verdict"] in ["PASS", "WARN"], f"Create failed: {result}"
    
    rendered = result.get("rendered_markdown", "")
    
    # Verify 工作纪律 section is present
    assert "## 工作纪律" in rendered, "工作纪律 section should be injected"
    assert "card/TEST-F76-INTEGRATION" in rendered, "Branch pattern should be in section"
    assert "RETURN.md" in rendered, "Report path should be in section"
    
    print("✓ F76 件①: create injects 工作纪律 section")


def test_f76_derive_function_exists():
    """AIPOS-F76: verify derive_machine_zone_纪律段 is callable."""
    repo_root = Path(__file__).parent.parent.parent.parent
    
    metadata = {"task_id": "TEST"}
    result = derive_machine_zone_纪律段("TEST", metadata, repo_root)
    
    assert "## 工作纪律" in result
    assert "card/TEST" in result
    
    print("✓ F76: derive_machine_zone_纪律段 works")


def test_f76_grep_verification():
    """AIPOS-F76 硬约束①: grep verification that body text only has one generation source."""
    import subprocess
    
    repo_root = Path(__file__).parent.parent.parent.parent
    
    # Grep for calls to derive_machine_zone_纪律段
    result = subprocess.run(
        ["grep", "-n", "derive_machine_zone_纪律段", 
         "tools/aipos_cli/draft_writer.py",
         "tools/aipos_cli/machine_zone.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    
    lines = result.stdout.strip().split("\n")
    
    # Should have exactly:
    # 1. One definition in machine_zone.py
    # 2. Three imports in draft_writer.py (create, publish, regen)
    # 3. Three calls in draft_writer.py
    
    definition_count = sum(1 for line in lines if "def derive_machine_zone_纪律段" in line)
    call_count = sum(1 for line in lines if "derive_machine_zone_纪律段(" in line and "def " not in line)
    
    assert definition_count == 1, f"Should have exactly 1 definition, found {definition_count}"
    assert call_count == 3, f"Should have exactly 3 calls (create/publish/regen), found {call_count}"
    
    print(f"✓ F76 硬约束①: grep confirms 1 definition, 3 calls (create/publish/regen)")


if __name__ == "__main__":
    test_f76_create_injects_discipline_section()
    test_f76_derive_function_exists()
    test_f76_grep_verification()
    print("\n✓ All F76 integration tests passed")
